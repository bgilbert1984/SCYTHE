#!/usr/bin/env python3
"""
Operator Session Manager - Multi-User Collaboration System
Based on Anduril Lattice SDK patterns for entity management and real-time sync.

Features:
- Operator authentication and session management
- Real-time entity synchronization via Server-Sent Events (SSE)
- Provenance tracking for all entity modifications
- Team-based collaboration
- Heartbeat-based presence detection
"""

import json
import uuid
import time
import threading
import queue
import hmac
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Set, Callable, Any, Tuple, Union
from enum import Enum
import sqlite3
import hashlib
import secrets
import os
import socket
import warnings
from pathlib import Path

def _default_operator_db_path(explicit_path: Optional[str] = None) -> str:
    """Resolve stable SQLite path for operator_sessions.db.

    Precedence:
      1) explicit argument
      2) OP_SESSION_DB_PATH / OPERATOR_SESSIONS_DB_PATH env var
      3) alongside this module (repo-stable, not cwd-dependent)
    """
    if explicit_path:
        return explicit_path
    env_path = os.environ.get("OP_SESSION_DB_PATH") or os.environ.get("OPERATOR_SESSIONS_DB_PATH")
    if env_path:
        return env_path
    return str(Path(__file__).resolve().parent / "operator_sessions.db")

def _resolve_operator_db_path(db_path: Optional[str]) -> str:
    """Resolve DB path handling explicit args and environment overrides."""
    if not db_path:
        return _default_operator_db_path()
    return str(Path(db_path).resolve())

# Optional Redis integration for Phase-1 prototype
try:
    import redis
    REDIS_AVAILABLE = True
except Exception:
    redis = None
    REDIS_AVAILABLE = False

# Optional FusionAuth Python client (embedded in assets for convenience)
try:
    import sys
    _fa_path = os.environ.get("FUSIONAUTH_PYTHON_CLIENT_PATH") or str(Path(__file__).resolve().parent / "assets" / "fusionauth-python-client-develop" / "src" / "main" / "python")
    if Path(_fa_path).exists() and _fa_path not in sys.path:
        sys.path.insert(0, _fa_path)
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="pkg_resources is deprecated as an API.*",
            category=UserWarning,
        )
        from fusionauth.fusionauth_client import FusionAuthClient
    FUSIONAUTH_CLIENT_AVAILABLE = True
except Exception:
    FusionAuthClient = None
    FUSIONAUTH_CLIENT_AVAILABLE = False

# Optional PyJWT JWKS support for local JWT validation
try:
    import jwt as _pyjwt
    try:
        from jwt import PyJWKClient as _PyJWKClient
    except Exception:
        _PyJWKClient = None
    JWT_AVAILABLE = True
except Exception:
    _pyjwt = None
    _PyJWKClient = None
    JWT_AVAILABLE = False


class OperatorRole(Enum):
    """Operator roles with different permission levels"""
    OBSERVER = "observer"           # Read-only access
    OPERATOR = "operator"           # Can modify entities
    SUPERVISOR = "supervisor"       # Can assign tasks and manage operators
    ADMIN = "admin"                 # Full system access


class EntityEventType(Enum):
    """Entity event types for SSE/WebSocket streaming"""
    PREEXISTING = "PREEXISTING"     # Initial state sync
    CREATE = "CREATE"               # New entity created
    UPDATE = "UPDATE"               # Entity modified
    DELETE = "DELETE"               # Entity removed
    HEARTBEAT = "HEARTBEAT"         # Keep-alive signal

    # Room events
    ROOM_CREATED = "ROOM_CREATED"
    ROOM_JOINED = "ROOM_JOINED"
    ROOM_LEFT = "ROOM_LEFT"
    ROOM_CLOSED = "ROOM_CLOSED"
    ROOM_MESSAGE = "ROOM_MESSAGE"

    # Presence events
    OPERATOR_JOINED = "OPERATOR_JOINED"
    OPERATOR_LEFT = "OPERATOR_LEFT"
    OPERATOR_VIEW_UPDATE = "OPERATOR_VIEW_UPDATE"


@dataclass
class Provenance:
    """Data provenance tracking - who modified what and when"""
    integration_name: str = "command-ops-visualization"
    data_type: str = "manual"
    source_id: str = ""             # Operator ID who made the change
    source_description: str = ""     # Human readable (operator name/email)
    source_update_time: str = ""     # ISO timestamp of modification

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_operator(cls, operator: 'Operator', data_type: str = "manual"):
        return cls(
            integration_name="command-ops-visualization",
            data_type=data_type,
            source_id=operator.operator_id,
            source_description=f"{operator.callsign} ({operator.email})",
            source_update_time=datetime.utcnow().isoformat() + "Z"
        )


@dataclass
class Operator:
    """Represents an authenticated operator"""
    operator_id: str
    callsign: str
    email: str
    role: OperatorRole
    team_id: Optional[str] = None
    created_at: str = ""
    last_active: str = ""

    def to_dict(self):
        return {
            "operator_id": self.operator_id,
            "callsign": self.callsign,
            "email": self.email,
            "role": self.role.value,
            "team_id": self.team_id,
            "created_at": self.created_at,
            "last_active": self.last_active
        }


@dataclass
class OperatorSession:
    """Active session for an operator"""
    session_id: str
    operator_id: str
    session_token: str
    created_at: str
    expires_at: str
    last_heartbeat: str
    current_view: Optional[Dict] = None  # Current map view bounds
    tracked_entities: Set[str] = field(default_factory=set)

    def to_dict(self):
        return {
            "session_id": self.session_id,
            "operator_id": self.operator_id,
            "session_token": self.session_token,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "last_heartbeat": self.last_heartbeat,
            "current_view": self.current_view,
            "tracked_entities": list(self.tracked_entities)
        }


@dataclass
class EntityEvent:
    """Event for entity changes to broadcast via SSE"""
    event_type: EntityEventType
    entity_id: str
    entity_type: str
    entity_data: Dict
    provenance: Provenance
    timestamp: str = ""
    sequence_id: int = 0

    def to_dict(self):
        return {
            "event_type": self.event_type.value,
            "entity_id": self.entity_id,
            "entity_type": self.entity_type,
            "entity_data": self.entity_data,
            "provenance": self.provenance.to_dict(),
            "timestamp": self.timestamp,
            "sequence_id": self.sequence_id
        }

    def to_sse(self):
        """Format as Server-Sent Event"""
        data = json.dumps(self.to_dict())
        return f"event: {self.event_type.value}\ndata: {data}\n\n"


class SSEClient:
    """Represents a connected SSE client"""
    def __init__(self, session_id: str, operator_id: str):
        self.session_id = session_id
        self.operator_id = operator_id
        self.queue: queue.Queue = queue.Queue()
        self.connected = True
        self.last_event_id = 0
        self.created_at = datetime.utcnow()
        self.filters: Dict[str, Any] = {}  # Entity type filters
        self.rooms: Set[str] = set()  # Rooms this client has joined

    def send(self, event: 'EntityEvent'):
        """Queue an event for this client"""
        if self.connected:
            self.queue.put(event)
            self.last_event_id = event.sequence_id

    def disconnect(self):
        """Mark client as disconnected"""
        self.connected = False
        self.queue.put(None)  # Signal to stop iteration


@dataclass
class Room:
    """Represents a communication room/channel"""
    room_id: str
    room_name: str
    room_type: str  # 'mission', 'team', 'geographic', 'custom'
    created_at: str
    created_by: str  # operator_id
    capacity: int = 50  # Max members
    is_private: bool = False
    password_hash: Optional[str] = None  # For private rooms
    metadata: Dict = field(default_factory=dict)  # Additional room data

    def to_dict(self):
        return {
            "room_id": self.room_id,
            "room_name": self.room_name,
            "room_type": self.room_type,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "capacity": self.capacity,
            "is_private": self.is_private,
            "metadata": self.metadata
        }


@dataclass
class RoomMembership:
    """Tracks room membership for an operator"""
    room_id: str
    operator_id: str
    session_id: str
    joined_at: str
    role: str = "member"  # 'owner', 'moderator', 'member'


class WebSocketClient:
    """Represents a connected WebSocket client for bidirectional communication"""
    def __init__(self, session_id: str, operator_id: str, websocket):
        self.session_id = session_id
        self.operator_id = operator_id
        self.websocket = websocket
        self.connected = True
        self.last_event_id = 0
        self.created_at = datetime.utcnow()
        self.rooms: Set[str] = set()  # Rooms this client has joined
        self.filters: Dict[str, Any] = {}

    async def send(self, event: 'EntityEvent'):
        """Send event via WebSocket"""
        if self.connected and self.websocket:
            try:
                await self.websocket.send(json.dumps(event.to_dict()))
            except Exception as e:
                print(f"[WebSocket] Send error: {e}")
                self.connected = False

    def send_sync(self, event: 'EntityEvent'):
        """Synchronous send (queues for async send)"""
        if self.connected and self.websocket:
            try:
                # For Flask-SocketIO, use emit
                self.websocket.emit('entity_event', event.to_dict())
            except Exception as e:
                print(f"[WebSocket] Sync send error: {e}")
                self.connected = False

    def disconnect(self):
        """Mark client as disconnected"""
        self.connected = False
        self.last_event_id = 0
        self.created_at = datetime.utcnow()
        self.filters: Dict[str, Any] = {}  # Entity type filters

    def send(self, event: EntityEvent):
        """Queue an event for this client"""
        if self.connected:
            self.queue.put(event)
            self.last_event_id = event.sequence_id

    def disconnect(self):
        """Mark client as disconnected"""
        self.connected = False
        self.queue.put(None)  # Signal to stop iteration


class OperatorSessionManager:
    """
    Manages operator sessions and real-time entity synchronization.
    Based on Anduril Lattice SDK patterns with Room/Channel support.
    """

    def __init__(self, db_path: Optional[str] = None, internal_token: Optional[str] = None):
        self.db_path = _resolve_operator_db_path(db_path)
        self.internal_token = internal_token
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.operators: Dict[str, Operator] = {}
        self.sessions: Dict[str, OperatorSession] = {}
        self.bootstrap_tokens: Dict[str, Dict[str, Any]] = {}
        self.sse_clients: Dict[str, SSEClient] = {}
        self.ws_clients: Dict[str, WebSocketClient] = {}  # WebSocket clients
        self.rooms: Dict[str, Room] = {}  # Active rooms
        self.room_members: Dict[str, Set[str]] = {}  # room_id -> set of session_ids
        self.room_entities: Dict[str, Dict[str, Dict]] = {}  # room_id -> entity_id -> entity_data
        self.entity_sequence = 0
        self.entity_cache: Dict[str, Dict] = {}  # Cache of current entities (global)
        self.lock = threading.RLock()

        # Optional FusionAuth integration - configured via env vars:
        # FUSIONAUTH_API_KEY, FUSIONAUTH_BASE_URL, FUSIONAUTH_APPLICATION_ID (optional)
        self.fusionauth_enabled = False
        self.fusionauth_client = None
        self.fusionauth_application_id = None
        self.fusionauth_base_url = None
        fa_api_key = os.environ.get("FUSIONAUTH_API_KEY")
        fa_base_url = os.environ.get("FUSIONAUTH_BASE_URL")
        fa_app_id = os.environ.get("FUSIONAUTH_APPLICATION_ID")
        # Auth mode: 'local', 'fusionauth-hybrid', 'fusionauth-jwt'
        self.auth_mode = os.environ.get("SCYTHE_AUTH_MODE", "fusionauth-hybrid")
        self.jwks_client = None
        self.jwt_enabled = False
        if fa_api_key and fa_base_url and FUSIONAUTH_CLIENT_AVAILABLE:
            try:
                self.fusionauth_client = FusionAuthClient(fa_api_key, fa_base_url)
                self.fusionauth_base_url = fa_base_url
                if fa_app_id:
                    self.fusionauth_application_id = fa_app_id
                self.fusionauth_enabled = True
                print(f"[OperatorManager] FusionAuth client initialized for {fa_base_url}")
                # Initialize JWKS client if PyJWT is available
                if JWT_AVAILABLE and _PyJWKClient is not None:
                    try:
                        jwks_url = fa_base_url.rstrip('/') + '/.well-known/jwks.json'
                        self.jwks_client = _PyJWKClient(jwks_url)
                        self.jwt_enabled = True
                        print(f"[OperatorManager] JWKS client initialized for {jwks_url}")
                    except Exception as e:
                        print(f"[OperatorManager] Failed to initialize JWKS client: {e}")
                        self.jwks_client = None
                        self.jwt_enabled = False
            except Exception as e:
                print(f"[OperatorManager] Failed to initialize FusionAuth client: {e}")
                self.fusionauth_enabled = False


        # Session configuration
        self.session_timeout = timedelta(hours=8)
        self.heartbeat_interval = 30  # seconds
        self.heartbeat_timeout = 90   # seconds before considered disconnected
        # Redis configuration (optional). Enable by setting OP_SESSION_REDIS_URL env var.
        self.redis = None
        redis_url = os.environ.get("OP_SESSION_REDIS_URL")
        if redis_url and REDIS_AVAILABLE:
            try:
                self.redis = redis.from_url(redis_url)
                # test connection
                self.redis.ping()
                print(f"[OperatorManager] Connected to Redis: {redis_url}")
                # configure stream/group/consumer names
                self.redis_stream = "entity_events_stream"
                self.redis_group = "entity_events_group"
                self.redis_consumer = f"{socket.gethostname()}:{uuid.uuid4().hex[:8]}"
                try:
                    # create consumer group if it doesn't exist
                    self.redis.xgroup_create(self.redis_stream, self.redis_group, id='$', mkstream=True)
                except Exception as e:
                    # BUSYGROUP means group already exists
                    if "BUSYGROUP" in str(e):
                        pass
                    else:
                        print(f"[OperatorManager] Redis xgroup_create issue: {e}")
            except Exception as e:
                print(f"[OperatorManager] Redis connection failed: {e}")
                self.redis = None

        self._init_database()
        self._load_operators()
        self._load_rooms()

        # Start background cleanup thread
        self._start_cleanup_thread()

        # Start Redis stream consumer if Redis is available
        if self.redis:
            self._start_redis_stream_consumer()

    def _init_database(self):
        """Initialize SQLite database for persistent storage"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Operators table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS operators (
                operator_id TEXT PRIMARY KEY,
                callsign TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'operator',
                team_id TEXT,
                created_at TEXT NOT NULL,
                last_active TEXT
            )
        ''')

        # Sessions table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                operator_id TEXT NOT NULL,
                session_token TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                last_heartbeat TEXT NOT NULL,
                current_view TEXT,
                FOREIGN KEY (operator_id) REFERENCES operators(operator_id)
            )
        ''')

        # Teams table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS teams (
                team_id TEXT PRIMARY KEY,
                team_name TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL,
                created_by TEXT
            )
        ''')

        # Entity audit log
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS entity_audit_log (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                event_type TEXT NOT NULL,
                operator_id TEXT,
                timestamp TEXT NOT NULL,
                previous_data TEXT,
                new_data TEXT
            )
        ''')

        # Rooms table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS rooms (
                room_id TEXT PRIMARY KEY,
                room_name TEXT UNIQUE NOT NULL,
                room_type TEXT NOT NULL DEFAULT 'custom',
                created_at TEXT NOT NULL,
                created_by TEXT,
                capacity INTEGER DEFAULT 50,
                is_private INTEGER DEFAULT 0,
                password_hash TEXT,
                metadata TEXT,
                FOREIGN KEY (created_by) REFERENCES operators(operator_id)
            )
        ''')

        # Room membership table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS room_membership (
                room_id TEXT NOT NULL,
                operator_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                joined_at TEXT NOT NULL,
                role TEXT DEFAULT 'member',
                PRIMARY KEY (room_id, session_id),
                FOREIGN KEY (room_id) REFERENCES rooms(room_id),
                FOREIGN KEY (operator_id) REFERENCES operators(operator_id)
            )
        ''')

        # Room entities table (entities scoped to rooms)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS room_entities (
                room_id TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                created_by TEXT,
                PRIMARY KEY (room_id, entity_id),
                FOREIGN KEY (room_id) REFERENCES rooms(room_id)
            )
        ''')

        conn.commit()
        conn.close()

        # Create default admin if no operators exist
        self._ensure_default_admin()

    def _ensure_default_admin(self):
        """Create default admin operator if none exist"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM operators")
        count = cursor.fetchone()[0]
        conn.close()

        if count == 0:
            self.register_operator(
                callsign="ADMIN",
                email="admin@command-ops.local",
                password="admin123",  # Should be changed immediately
                role=OperatorRole.ADMIN
            )
            print("[OperatorManager] Created default admin operator (callsign: ADMIN)")

    def _load_operators(self):
        """Load operators from database into memory"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM operators")

        for row in cursor.fetchall():
            operator = Operator(
                operator_id=row[0],
                callsign=row[1],
                email=row[2],
                role=OperatorRole(row[4]),
                team_id=row[5],
                created_at=row[6],
                last_active=row[7] or ""
            )
            self.operators[operator.operator_id] = operator

        conn.close()
        print(f"[OperatorManager] Loaded {len(self.operators)} operators")

    def _load_rooms(self):
        """Load rooms from database into memory"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM rooms")

        for row in cursor.fetchall():
            room = Room(
                room_id=row[0],
                room_name=row[1],
                room_type=row[2],
                created_at=row[3],
                created_by=row[4] or "",
                capacity=row[5] or 50,
                is_private=bool(row[6]),
                password_hash=row[7],
                metadata=json.loads(row[8]) if row[8] else {}
            )
            self.rooms[room.room_id] = room
            self.room_members[room.room_id] = set()
            self.room_entities[room.room_id] = {}

        # Load room entities
        cursor.execute("SELECT * FROM room_entities")
        for row in cursor.fetchall():
            room_id, entity_id, entity_type, entity_data, _, _, _ = row
            if room_id in self.room_entities:
                self.room_entities[room_id][entity_id] = {
                    "id": entity_id,
                    "type": entity_type,
                    "data": json.loads(entity_data) if entity_data else {}
                }

        conn.close()
        print(f"[OperatorManager] Loaded {len(self.rooms)} rooms")

        # Create default global room if none exist
        if not self.rooms:
            self._create_default_rooms()

    def create_bootstrap_token(self, operator_id: str, instance_id: str) -> str:
        """Create a signed bootstrap token for identity handoff."""
        if self.internal_token:
            expiry = int(time.time() + 300)  # 5 minutes
            payload = f"{operator_id}|{instance_id}|{expiry}"
            signature = hmac.new(
                self.internal_token.encode(),
                payload.encode(),
                hashlib.sha256
            ).hexdigest()
            return f"{payload}|{signature}"

        # Fallback to in-memory if no secret (local behavior)
        token = secrets.token_urlsafe(32)
        expiry_dt = datetime.utcnow() + timedelta(seconds=300)
        with self.lock:
            self.bootstrap_tokens[token] = {
                "operator_id": operator_id,
                "instance_id": instance_id,
                "expires_at": expiry_dt,
                "used": False
            }
        return token

    def exchange_bootstrap_token(self, token: str, instance_id: str) -> Optional[OperatorSession]:
        """Verify bootstrap token (signed or in-memory) and return a fresh session."""
        # 1. Try signed token verification if we have the secret
        if self.internal_token and "|" in token:
            try:
                parts = token.split("|")
                if len(parts) == 4:
                    op_id, inst_id, expiry, signature = parts
                    payload = f"{op_id}|{inst_id}|{expiry}"

                    expected = hmac.new(
                        self.internal_token.encode(),
                        payload.encode(),
                        hashlib.sha256
                    ).hexdigest()

                    if hmac.compare_digest(signature, expected):
                        if int(time.time()) < int(expiry):
                            if inst_id == instance_id:
                                return self._create_session(op_id)
            except Exception as e:
                print(f"[OperatorManager] Signed bootstrap error: {e}")

        # 2. Fallback to in-memory tokens
        with self.lock:
            data = self.bootstrap_tokens.get(token)
            if not data or data.get("used") or datetime.utcnow() > data.get("expires_at"):
                return None

            # Verify instance scope
            if data.get("instance_id") != instance_id:
                return None

            data["used"] = True
            operator_id = data.get("operator_id")

        return self._create_session(operator_id)

    def _create_default_rooms(self):
        """Create default rooms"""
        # Global room - everyone can join
        self.create_room(
            room_name="Global",
            room_type="global",
            created_by=None,
            is_private=False
        )
        print("[OperatorManager] Created default Global room")

    def _start_cleanup_thread(self):
        """Start background thread for session cleanup"""
        def cleanup_loop():
            while True:
                time.sleep(60)  # Check every minute
                self._cleanup_expired_sessions()

        thread = threading.Thread(target=cleanup_loop, daemon=True)
        thread.start()

    def _cleanup_expired_sessions(self):
        """Remove expired sessions and disconnected SSE clients"""
        now = datetime.utcnow()

        with self.lock:
            # Clean up expired sessions
            expired = []
            for session_id, session in self.sessions.items():
                expires_at = datetime.fromisoformat(session.expires_at.replace('Z', ''))
                if now > expires_at:
                    expired.append(session_id)

            for session_id in expired:
                self._end_session(session_id)

            # Clean up disconnected SSE clients
            disconnected = [
                sid for sid, client in self.sse_clients.items()
                if not client.connected
            ]
            for session_id in disconnected:
                del self.sse_clients[session_id]

    def _start_redis_stream_consumer(self):
        """Start a background thread to consume Redis stream and forward events to local clients."""
        def consumer_loop():
            stream = "entity_events_stream"
            # Use consumer group for scalable delivery and ACK tracking
            group = getattr(self, 'redis_group', 'entity_events_group')
            consumer = getattr(self, 'redis_consumer', f"{socket.gethostname()}:{uuid.uuid4().hex[:8]}")
            claim_idle_ms = 60000  # claim messages idle > 60s

            while True:
                try:
                    # Read new messages for this group/consumer
                    entries = self.redis.xreadgroup(groupname=group, consumername=consumer, streams={stream: '>'}, count=10, block=5000)
                    if entries:
                        for stream_name, msgs in entries:
                            for msg_id, fields in msgs:
                                data = None
                                if isinstance(list(fields.keys())[0], bytes):
                                    data = fields.get(b'data')
                                else:
                                    data = fields.get('data')
                                if isinstance(data, bytes):
                                    data = data.decode()
                                if not data:
                                    # Acknowledge empty? skip
                                    try:
                                        self.redis.xack(stream, group, msg_id)
                                    except Exception:
                                        pass
                                    continue
                                try:
                                    obj = json.loads(data)
                                    prov = Provenance(**obj.get('provenance', {})) if obj.get('provenance') else Provenance()
                                    evt = EntityEvent(
                                        event_type=EntityEventType(obj.get('event_type')),
                                        entity_id=obj.get('entity_id',''),
                                        entity_type=obj.get('entity_type','entity'),
                                        entity_data=obj.get('entity_data',{}),
                                        provenance=prov,
                                        timestamp=obj.get('timestamp',''),
                                        sequence_id=obj.get('sequence_id',0)
                                    )
                                    # Forward to local clients (SSE + WebSocket)
                                    with self.lock:
                                        for client in self.sse_clients.values():
                                            if client.connected:
                                                client.send(evt)
                                        for ws in self.ws_clients.values():
                                            if ws.connected:
                                                try:
                                                    ws.send_sync(evt)
                                                except Exception:
                                                    try:
                                                        import asyncio
                                                        asyncio.run(ws.send(evt))
                                                    except Exception:
                                                        pass
                                    # Acknowledge the message
                                    try:
                                        self.redis.xack(stream, group, msg_id)
                                    except Exception:
                                        pass
                                except Exception as e:
                                    print(f"[OperatorManager] Failed to parse/forward stream message: {e}")

                    # Periodically claim old pending messages (stalled consumers)
                    try:
                        pending = self.redis.xpending_range(stream, group, '-', '+', count=10)
                        for pend in pending:
                            # pend may be tuple (id, consumer, idle, deliveries)
                            msg_id = pend[0] if isinstance(pend, tuple) else pend.get('message_id')
                            idle = pend[2] if isinstance(pend, tuple) else pend.get('idle', 0)
                            if idle and int(idle) >= claim_idle_ms:
                                claimed = self.redis.xclaim(stream, group, consumer, min_idle_time=claim_idle_ms, message_ids=[msg_id])
                                for cmid, fields in claimed:
                                    d = fields.get(b'data') if isinstance(list(fields.keys())[0], bytes) else fields.get('data')
                                    if isinstance(d, bytes):
                                        d = d.decode()
                                    try:
                                        obj = json.loads(d)
                                        prov = Provenance(**obj.get('provenance', {})) if obj.get('provenance') else Provenance()
                                        evt = EntityEvent(
                                            event_type=EntityEventType(obj.get('event_type')),
                                            entity_id=obj.get('entity_id',''),
                                            entity_type=obj.get('entity_type','entity'),
                                            entity_data=obj.get('entity_data',{}),
                                            provenance=prov,
                                            timestamp=obj.get('timestamp',''),
                                            sequence_id=obj.get('sequence_id',0)
                                        )
                                        with self.lock:
                                            for client in self.sse_clients.values():
                                                if client.connected:
                                                    client.send(evt)
                                            for ws in self.ws_clients.values():
                                                if ws.connected:
                                                    try:
                                                        ws.send_sync(evt)
                                                    except Exception:
                                                        try:
                                                            import asyncio
                                                            asyncio.run(ws.send(evt))
                                                        except Exception:
                                                            pass
                                        try:
                                            self.redis.xack(stream, group, cmid)
                                        except Exception:
                                            pass
                                    except Exception as e:
                                        print(f"[OperatorManager] Failed to parse claimed msg: {e}")
                    except Exception:
                        pass

                except Exception as e:
                    print(f"[OperatorManager] Redis stream consumer error: {e}")
                    time.sleep(1)

        thread = threading.Thread(target=consumer_loop, daemon=True)
        thread.start()

    def _hash_password(self, password: str) -> str:
        """Hash password with salt"""
        salt = "command-ops-salt"  # In production, use unique salt per user
        return hashlib.sha256(f"{salt}{password}".encode()).hexdigest()

    def register_operator(
        self,
        callsign: str,
        email: str,
        password: str,
        role: OperatorRole = OperatorRole.OPERATOR,
        team_id: Optional[str] = None
    ) -> Optional[Operator]:
        """Register a new operator"""
        operator_id = str(uuid.uuid4())
        password_hash = self._hash_password(password)
        created_at = datetime.utcnow().isoformat() + "Z"

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            cursor.execute('''
                INSERT INTO operators (operator_id, callsign, email, password_hash, role, team_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (operator_id, callsign, email, password_hash, role.value, team_id, created_at))
            conn.commit()

            operator = Operator(
                operator_id=operator_id,
                callsign=callsign,
                email=email,
                role=role,
                team_id=team_id,
                created_at=created_at
            )
            self.operators[operator_id] = operator

            print(f"[OperatorManager] Registered operator: {callsign}")
            return operator

        except sqlite3.IntegrityError as e:
            print(f"[OperatorManager] Registration failed: {e}")
            return None
        finally:
            conn.close()

    def authenticate(self, callsign: str, password: str) -> Optional[OperatorSession]:
        """Authenticate operator and create session"""
        # Prefer FusionAuth if configured
        if getattr(self, "fusionauth_enabled", False) and getattr(self, "fusionauth_client", None):
            try:
                req = {"loginId": callsign, "password": password}
                if getattr(self, "fusionauth_application_id", None):
                    req["applicationId"] = self.fusionauth_application_id

                print(f"[OperatorManager] FUSIONAUTH AUTH ATTEMPT")
                print(f"  callsign: {callsign}")
                print(f"  application_id: {self.fusionauth_application_id}")

                resp = self.fusionauth_client.login(req)

                if resp:
                    print(f"[OperatorManager] FA resp.status: {getattr(resp, 'status', 'N/A')}")
                    if hasattr(resp, 'success_response'):
                        print(f"[OperatorManager] FA success: {resp.success_response}")
                    if hasattr(resp, 'error_response'):
                        print(f"[OperatorManager] FA error: {resp.error_response}")
                else:
                    print(f"[OperatorManager] FA resp is None")

                if resp and getattr(resp, "was_successful", lambda: False)():
                    data = resp.success_response or {}

                    # DEBUG: Print full response to understand why token might be missing
                    print(f"[OperatorManager] FA Auth Success Data: {data}")

                    # extract possible JWT token
                    token = None
                    token_data = data.get("token")
                    if isinstance(token_data, dict):
                        token = token_data.get("jwt") or token_data.get("access_token")
                    token = token or data.get("jwt") or data.get("access_token") or None

                    user = data.get("user") if isinstance(data.get("user"), dict) else {}
                    email = user.get("email") if isinstance(user, dict) else None
                    # Try to find existing operator by callsign
                    conn = sqlite3.connect(self.db_path)
                    cursor = conn.cursor()
                    cursor.execute('SELECT operator_id FROM operators WHERE callsign = ?', (callsign,))
                    row = cursor.fetchone()
                    if row:
                        operator_id = row[0]
                    else:
                        # Create operator record using FusionAuth profile data
                        operator_id = str(uuid.uuid4())
                        created_at = datetime.utcnow().isoformat() + "Z"
                        email_val = email or f"{callsign}@fusionauth.local"
                        password_hash = self._hash_password(password) if password else ""
                        try:
                            cursor.execute('''
                                INSERT INTO operators (operator_id, callsign, email, password_hash, role, team_id, created_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                            ''', (operator_id, callsign, email_val, password_hash, OperatorRole.OPERATOR.value, None, created_at))
                            conn.commit()
                        except sqlite3.IntegrityError:
                            # fallback query
                            cursor.execute('SELECT operator_id FROM operators WHERE callsign = ?', (callsign,))
                            r = cursor.fetchone()
                            if r:
                                operator_id = r[0]
                        self.operators[operator_id] = Operator(operator_id=operator_id, callsign=callsign, email=email_val, role=OperatorRole.OPERATOR, team_id=None, created_at=created_at)
                    conn.close()
                    # Create session, prefer returning the FusionAuth token as session token if available
                    return self._create_session(operator_id, session_token_override=token) if token else self._create_session(operator_id)
                # else fall back to local auth
            except Exception as e:
                print(f"[OperatorManager] FusionAuth login error: {e}")

        # Legacy local credential check
        password_hash = self._hash_password(password)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT operator_id FROM operators
            WHERE callsign = ? AND password_hash = ?
        ''', (callsign, password_hash))

        result = cursor.fetchone()
        conn.close()

        if not result:
            return None

        operator_id = result[0]
        return self._create_session(operator_id)

    def _create_session(self, operator_id: str, session_token_override: Optional[str] = None, expires_at_override: Optional[datetime] = None) -> OperatorSession:
        """Create a new session for an operator, preferring JWT issuance if an internal secret exists."""
        session_id = str(uuid.uuid4())
        now = datetime.utcnow()
        expires_at = expires_at_override if expires_at_override else (now + self.session_timeout)

        # Issue JWT if we have an internal secret
        if session_token_override:
            session_token = session_token_override
        elif self.internal_token:
            try:
                import jwt
                payload = {
                    "sub": operator_id,
                    "iss": "scythe-orchestrator",
                    "iat": int(now.timestamp()),
                    "exp": int(expires_at.timestamp()),
                    "session_id": session_id
                }
                session_token = jwt.encode(payload, self.internal_token, algorithm="HS256")
            except Exception as e:
                print(f"[OperatorManager] JWT issuance failed, falling back to opaque token: {e}")
                session_token = secrets.token_urlsafe(32)
        else:
            session_token = secrets.token_urlsafe(32)

        session = OperatorSession(
            session_id=session_id,
            operator_id=operator_id,
            session_token=session_token,
            created_at=now.isoformat() + "Z",
            expires_at=expires_at.isoformat() + "Z",
            last_heartbeat=now.isoformat() + "Z"
        )

        with self.lock:
            self.sessions[session_id] = session

            # Store session token in Redis with TTL for fast validation across processes
            if self.redis:
                try:
                    key = f"session:{session.session_token}"
                    value = json.dumps({"session_id": session.session_id, "operator_id": session.operator_id})
                    ttl = int((expires_at - now).total_seconds()) if expires_at else int(self.session_timeout.total_seconds())
                    self.redis.set(key, value, ex=ttl)
                except Exception as e:
                    print(f"[OperatorManager] Failed to set session in Redis: {e}")

        # Persist to database
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO sessions (session_id, operator_id, session_token, created_at, expires_at, last_heartbeat)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (session_id, operator_id, session_token, session.created_at, session.expires_at, session.last_heartbeat))

        # Update operator's last_active
        cursor.execute('''
            UPDATE operators SET last_active = ? WHERE operator_id = ?
        ''', (now.isoformat() + "Z", operator_id))

        conn.commit()
        conn.close()

        operator = self.operators.get(operator_id)
        if operator:
            print(f"[OperatorManager] Session created for {operator.callsign}")

        return session

    def _validate_fusionauth_jwt(self, token: str) -> Optional[dict]:
        """Validate a FusionAuth JWT locally using JWKS and return decoded claims or None."""
        if not token or not isinstance(token, str) or token.count('.') != 2:
            return None
        if not getattr(self, 'jwt_enabled', False) or not getattr(self, 'jwks_client', None):
            return None
        try:
            signing_key = self.jwks_client.get_signing_key_from_jwt(token)
            public_key = signing_key.key
            # If application id is configured, validate audience; otherwise skip audience check
            if getattr(self, 'fusionauth_application_id', None):
                claims = _pyjwt.decode(token, public_key, algorithms=["RS256"], audience=self.fusionauth_application_id, issuer=self.fusionauth_base_url)
            else:
                # disable audience verification
                claims = _pyjwt.decode(token, public_key, algorithms=["RS256"], issuer=self.fusionauth_base_url, options={"verify_aud": False})
            return claims
        except Exception as e:
            print(f"[OperatorManager] JWT validation failed: {e}")
            return None

    def _session_from_claims(self, token: str, claims: dict) -> OperatorSession:
        """Create or lookup an Operator and produce an OperatorSession from JWT claims."""
        email = claims.get('email') or claims.get('email_address')
        user_id = claims.get('sub') or claims.get('userId') or claims.get('username')
        callsign = claims.get('preferred_username') or claims.get('username') or (user_id if isinstance(user_id, str) else None)
        if not callsign and email:
            callsign = email.split('@')[0]
        if not callsign:
            callsign = f"fusionauth_user_{secrets.token_urlsafe(6)}"

        # Find or create operator
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        operator_id = None
        if email:
            cursor.execute('SELECT operator_id FROM operators WHERE email = ?', (email,))
            row = cursor.fetchone()
            if row:
                operator_id = row[0]
        if not operator_id:
            cursor.execute('SELECT operator_id FROM operators WHERE callsign = ?', (callsign,))
            row = cursor.fetchone()
            if row:
                operator_id = row[0]
        if not operator_id:
            operator_id = str(uuid.uuid4())
            created_at = datetime.utcnow().isoformat() + "Z"
            email_val = email or f"{callsign}@fusionauth.local"
            try:
                cursor.execute('''
                    INSERT INTO operators (operator_id, callsign, email, password_hash, role, team_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (operator_id, callsign, email_val, "", OperatorRole.OPERATOR.value, None, created_at))
                conn.commit()
            except sqlite3.IntegrityError:
                cursor.execute('SELECT operator_id FROM operators WHERE email = ? OR callsign = ?', (email_val, callsign))
                r = cursor.fetchone()
                if r:
                    operator_id = r[0]
            self.operators[operator_id] = Operator(operator_id=operator_id, callsign=callsign, email=email_val, role=OperatorRole.OPERATOR, team_id=None, created_at=created_at)
        conn.close()

        # Determine expiry
        exp = claims.get('exp')
        expires_at_dt = None
        if exp:
            try:
                expires_at_dt = datetime.utcfromtimestamp(int(exp))
            except Exception:
                expires_at_dt = datetime.utcnow() + self.session_timeout
        else:
            expires_at_dt = datetime.utcnow() + self.session_timeout

        session = self._create_session(operator_id, session_token_override=token, expires_at_override=expires_at_dt)
        return session

    def validate_session(self, session_token: str) -> Optional[OperatorSession]:
        """Validate a session token and return session if valid"""
        if not session_token:
            return None

        # 1. JWT Local validation (JWT-first)
        if self.jwt_enabled and isinstance(session_token, str) and session_token.count('.') == 2:
            try:
                claims = self._validate_fusionauth_jwt(session_token)
                if claims:
                    return self._session_from_claims(session_token, claims)
            except Exception as e:
                print(f"[OperatorManager] Local JWKS validation failed: {e}")

        # 2. Remote introspection fallback (only if mode allows)
        if getattr(self, 'auth_mode', 'fusionauth-hybrid') == 'fusionauth-hybrid':
            if getattr(self, 'fusionauth_enabled', False) and getattr(self, 'fusionauth_client', None):
                try:
                    resp = self.fusionauth_client.introspect_access_token(None, session_token)
                    if resp and getattr(resp, 'was_successful', lambda: False)() and resp.success_response:
                        data = resp.success_response
                        if data.get('active', False):
                            return self._session_from_claims(session_token, data)
                except Exception as e:
                    print(f"[OperatorManager] FusionAuth introspect error: {e}")

        # 3. Legacy Redis/Local check
        if self.redis:
            try:
                key = f"session:{session_token}"
                val = self.redis.get(key)
                if val:
                    try:
                        obj = json.loads(val)
                        session_id = obj.get("session_id")
                        # Return in-memory session if present
                        with self.lock:
                            session = self.sessions.get(session_id)
                            if session:
                                expires_at = datetime.fromisoformat(session.expires_at.replace('Z', ''))
                                if datetime.utcnow() < expires_at:
                                    return session
                                else:
                                    self._end_session(session.session_id)
                                    return None
                            # Not in memory: attempt to load from DB
                            loaded = self._load_session_from_db(session_id)
                            return loaded
                    except Exception:
                        pass
            except Exception as e:
                print(f"[OperatorManager] Redis validate error: {e}")

        # Fallback to in-memory sessions
        with self.lock:
            for session in self.sessions.values():
                if session.session_token == session_token:
                    expires_at = datetime.fromisoformat(session.expires_at.replace('Z', ''))
                    if datetime.utcnow() < expires_at:
                        return session
                    else:
                        self._end_session(session.session_id)
                        return None
        return None

    def _load_session_from_db(self, session_id: str) -> Optional[OperatorSession]:
        """Load a session row from the DB into memory and return it."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('SELECT session_id, operator_id, session_token, created_at, expires_at, last_heartbeat, current_view FROM sessions WHERE session_id = ?', (session_id,))
            row = cursor.fetchone()
            conn.close()
            if not row:
                return None
            session = OperatorSession(
                session_id=row[0],
                operator_id=row[1],
                session_token=row[2],
                created_at=row[3],
                expires_at=row[4],
                last_heartbeat=row[5],
                current_view=json.loads(row[6]) if row[6] else None
            )
            with self.lock:
                self.sessions[session.session_id] = session
            return session
        except Exception as e:
            print(f"[OperatorManager] Failed to load session from DB: {e}")
            return None

    def validate_session_by_id(self, session_id: str) -> Optional[OperatorSession]:
        """Validate by session ID"""
        with self.lock:
            session = self.sessions.get(session_id)
            if session:
                expires_at = datetime.fromisoformat(session.expires_at.replace('Z', ''))
                if datetime.utcnow() < expires_at:
                    return session
        return None

    def heartbeat(self, session_token: str, current_view: Optional[Dict] = None) -> bool:
        """Update session heartbeat and optionally current view"""
        session = self.validate_session(session_token)
        if not session:
            return False

        now = datetime.utcnow().isoformat() + "Z"

        with self.lock:
            session.last_heartbeat = now
            if current_view:
                session.current_view = current_view

        # Update database
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE sessions SET last_heartbeat = ?, current_view = ? WHERE session_id = ?
        ''', (now, json.dumps(current_view) if current_view else None, session.session_id))
        cursor.execute('''
            UPDATE operators SET last_active = ? WHERE operator_id = ?
        ''', (now, session.operator_id))
        conn.commit()
        conn.close()

        return True

    def _end_session(self, session_id: str):
        """End a session"""
        with self.lock:
            # If present, delete Redis session key
            sess = self.sessions.get(session_id)
            if sess and self.redis:
                try:
                    key = f"session:{sess.session_token}"
                    self.redis.delete(key)
                except Exception as e:
                    print(f"[OperatorManager] Failed to delete session in Redis: {e}")

            if session_id in self.sessions:
                del self.sessions[session_id]
            if session_id in self.sse_clients:
                self.sse_clients[session_id].disconnect()
                del self.sse_clients[session_id]

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        conn.commit()
        conn.close()

    def logout(self, session_token: str) -> bool:
        """End operator session"""
        session = self.validate_session(session_token)
        if session:
            self._end_session(session.session_id)
            return True
        return False

    def get_operator(self, operator_id: str) -> Optional[Operator]:
        """Get operator by ID"""
        return self.operators.get(operator_id)

    def get_operator_for_session(self, session_token: str) -> Optional[Operator]:
        """Get operator for a session"""
        session = self.validate_session(session_token)
        if session:
            return self.operators.get(session.operator_id)
        return None

    def get_room_entities_snapshot(self, room_id: str) -> List[Dict]:
        """Thread-safe snapshot of all entities in a room."""
        with self.lock:
            # Check if we have room in memory
            if room_id in self.room_entities:
                # return copy to prevent mutation issues
                return [dict(e) for e in self.room_entities[room_id].values()]
            return []

    def get_active_operators(self) -> List[Dict]:
        """Get list of currently active operators with their views"""
        active = []
        now = datetime.utcnow()

        with self.lock:
            for session in self.sessions.values():
                last_hb = datetime.fromisoformat(session.last_heartbeat.replace('Z', ''))
                if (now - last_hb).total_seconds() < self.heartbeat_timeout:
                    operator = self.operators.get(session.operator_id)
                    if operator:
                        active.append({
                            "operator_id": operator.operator_id,
                            "callsign": operator.callsign,
                            "role": operator.role.value,
                            "team_id": operator.team_id,
                            "current_view": session.current_view,
                            "last_heartbeat": session.last_heartbeat
                        })

        return active

    # ==================== SSE Entity Streaming ====================

    def register_sse_client(self, session_token: str) -> Optional[SSEClient]:
        """Register a new SSE client for entity streaming"""
        session = self.validate_session(session_token)
        if not session:
            return None

        client = SSEClient(session.session_id, session.operator_id)

        with self.lock:
            self.sse_clients[session.session_id] = client

        operator = self.operators.get(session.operator_id)
        if operator:
            print(f"[SSE] Client connected: {operator.callsign}")

        # Send preexisting entities
        # First send cached in-memory entities
        self._send_preexisting_entities(client)

        # Then attempt to replay recent events from Redis stream (if available)
        if self.redis:
            try:
                stream = "entity_events_stream"
                # get last 100 entries (newest first), then reverse to chronological
                entries = self.redis.xrevrange(stream, max='+', min='-', count=100)
                if entries:
                    for msg_id, fields in reversed(entries):
                        data = fields.get(b'data') if isinstance(list(fields.keys())[0], bytes) else fields.get('data')
                        if isinstance(data, bytes):
                            data = data.decode()
                        try:
                            obj = json.loads(data)
                            # recreate EntityEvent
                            prov = Provenance(**obj.get('provenance', {})) if obj.get('provenance') else Provenance()
                            evt = EntityEvent(
                                event_type=EntityEventType(obj.get('event_type')),
                                entity_id=obj.get('entity_id',''),
                                entity_type=obj.get('entity_type','entity'),
                                entity_data=obj.get('entity_data',{}),
                                provenance=prov,
                                timestamp=obj.get('timestamp',''),
                                sequence_id=obj.get('sequence_id',0)
                            )
                            client.send(evt)
                        except Exception:
                            continue
            except Exception as e:
                print(f"[OperatorManager] Failed to replay Redis stream to client: {e}")

        return client

    def replay_events_since(self, client: SSEClient, since_sequence: int):
        """Replay events from Redis stream to the given SSE client where sequence_id > since_sequence.

        This is a best-effort helper; if Redis is not configured, this is a no-op.
        """
        if not self.redis or not client:
            return
        try:
            stream = "entity_events_stream"
            # iterate recent entries and send those with sequence_id > since_sequence
            entries = self.redis.xrange(stream, min='-', max='+', count=1000)
            for msg_id, fields in entries:
                data = fields.get(b'data') if isinstance(list(fields.keys())[0], bytes) else fields.get('data')
                if isinstance(data, bytes):
                    data = data.decode()
                try:
                    obj = json.loads(data)
                    seq = int(obj.get('sequence_id', 0))
                    if seq and seq > int(since_sequence):
                        prov = Provenance(**obj.get('provenance', {})) if obj.get('provenance') else Provenance()
                        evt = EntityEvent(
                            event_type=EntityEventType(obj.get('event_type')),
                            entity_id=obj.get('entity_id',''),
                            entity_type=obj.get('entity_type','entity'),
                            entity_data=obj.get('entity_data',{}),
                            provenance=prov,
                            timestamp=obj.get('timestamp',''),
                            sequence_id=seq
                        )
                        client.send(evt)
                except Exception:
                    continue
        except Exception as e:
            print(f"[OperatorManager] Failed to replay Redis events since {since_sequence}: {e}")

    def unregister_sse_client(self, session_id: str):
        """Unregister an SSE client"""
        with self.lock:
            if session_id in self.sse_clients:
                self.sse_clients[session_id].disconnect()
                del self.sse_clients[session_id]

    def _send_preexisting_entities(self, client: SSEClient):
        """Send all cached entities to a newly connected client"""
        # Prefer authoritative source from hypergraph_engine when available
        try:
            if 'hypergraph_engine' in globals() and hypergraph_engine is not None:
                with self.lock:
                    for entity_id, node in getattr(hypergraph_engine, 'nodes', {}).items():
                        entity_data = dict(node) if isinstance(node, dict) else node
                        event = EntityEvent(
                            event_type=EntityEventType.PREEXISTING,
                            entity_id=entity_id,
                            entity_type=entity_data.get("kind", entity_data.get("type", "entity")),
                            entity_data=entity_data,
                            provenance=Provenance(
                                source_id="system",
                                source_description="Initial sync (engine)",
                                source_update_time=datetime.utcnow().isoformat() + "Z"
                            ),
                            timestamp=datetime.utcnow().isoformat() + "Z",
                            sequence_id=self.entity_sequence
                        )
                        client.send(event)
                return
        except Exception:
            # Fall back to cached entities on any error
            pass

        # Fallback: send cached in-memory entities
        with self.lock:
            for entity_id, entity_data in self.entity_cache.items():
                event = EntityEvent(
                    event_type=EntityEventType.PREEXISTING,
                    entity_id=entity_id,
                    entity_type=entity_data.get("type", "unknown"),
                    entity_data=entity_data,
                    provenance=Provenance(
                        source_id="system",
                        source_description="Initial sync",
                        source_update_time=datetime.utcnow().isoformat() + "Z"
                    ),
                    timestamp=datetime.utcnow().isoformat() + "Z",
                    sequence_id=self.entity_sequence
                )
                client.send(event)

    def broadcast_entity_event(
        self,
        event_type: EntityEventType,
        entity_id: str,
        entity_type: str,
        entity_data: Dict,
        operator: Optional[Operator] = None,
        exclude_session: Optional[str] = None,
        sequence_id: Optional[int] = None
    ):
        """Broadcast an entity event to all connected SSE clients.

        If `sequence_id` is provided, reconcile the internal `entity_sequence`
        with the provided value and use it for the outgoing event. Otherwise
        increment the internal sequence as before.
        """
        with self.lock:
            if sequence_id is not None:
                # Reconcile with external authoritative sequence
                try:
                    self.entity_sequence = max(self.entity_sequence, int(sequence_id))
                except Exception:
                    pass
            else:
                self.entity_sequence += 1

            # Update entity cache
            if event_type == EntityEventType.DELETE:
                self.entity_cache.pop(entity_id, None)
            else:
                self.entity_cache[entity_id] = {
                    **entity_data,
                    "id": entity_id,
                    "type": entity_type
                }

            # Create event
            if operator:
                provenance = Provenance.from_operator(operator)
            else:
                provenance = Provenance(
                    source_id="system",
                    source_description="System",
                    source_update_time=datetime.utcnow().isoformat() + "Z"
                )

            event = EntityEvent(
                event_type=event_type,
                entity_id=entity_id,
                entity_type=entity_type,
                entity_data=entity_data,
                provenance=provenance,
                timestamp=datetime.utcnow().isoformat() + "Z",
                sequence_id=self.entity_sequence
            )

            # Log to audit
            self._log_entity_event(event)

            # Publish event to Redis Pub/Sub (optional, for cross-process fan-out)
            if self.redis:
                try:
                    # Publish to Pub/Sub (quick notify)
                    ch = "entity_events"
                    self.redis.publish(ch, json.dumps(event.to_dict()))
                    # Also append to Stream for replay and durable in-memory log
                    stream = "entity_events_stream"
                    # Use approximate maxlen to cap stream growth (keep recent 10000)
                    self.redis.xadd(stream, {"data": json.dumps(event.to_dict())}, maxlen=10000, approximate=True)
                except Exception as e:
                    print(f"[OperatorManager] Failed to publish event to Redis: {e}")

            # Broadcast to all clients
            for session_id, client in self.sse_clients.items():
                if session_id != exclude_session and client.connected:
                    client.send(event)

    def _log_entity_event(self, event: EntityEvent):
        """Log entity event to audit trail"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO entity_audit_log (entity_id, entity_type, event_type, operator_id, timestamp, new_data)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            event.entity_id,
            event.entity_type,
            event.event_type.value,
            event.provenance.source_id,
            event.timestamp,
            json.dumps(event.entity_data)
        ))
        conn.commit()
        conn.close()

    def send_heartbeat(self):
        """Send heartbeat to all connected clients"""
        with self.lock:
            event = EntityEvent(
                event_type=EntityEventType.HEARTBEAT,
                entity_id="",
                entity_type="heartbeat",
                entity_data={"timestamp": datetime.utcnow().isoformat() + "Z"},
                provenance=Provenance(),
                timestamp=datetime.utcnow().isoformat() + "Z",
                sequence_id=self.entity_sequence
            )

            for client in self.sse_clients.values():
                if client.connected:
                    client.send(event)

    def sse_event_generator(self, client: SSEClient):
        """Generator for SSE events - use in Flask response"""
        try:
            while client.connected:
                try:
                    event = client.queue.get(timeout=self.heartbeat_interval)
                    if event is None:
                        break
                    yield event.to_sse()
                except queue.Empty:
                    # Send heartbeat on timeout
                    heartbeat = EntityEvent(
                        event_type=EntityEventType.HEARTBEAT,
                        entity_id="",
                        entity_type="heartbeat",
                        entity_data={"timestamp": datetime.utcnow().isoformat() + "Z"},
                        provenance=Provenance(),
                        timestamp=datetime.utcnow().isoformat() + "Z",
                        sequence_id=self.entity_sequence
                    )
                    yield heartbeat.to_sse()
        finally:
            client.disconnect()

    # -----------------
    # GraphEvent subscription / handler
    # -----------------
    def subscribe_to_graph_events(self, source) -> None:
        """Subscribe the session manager to a graph event source.

        `source` may be a HypergraphEngine (has `subscribe`) or a GraphEventBus-like
        object with `subscribe` method. This registers `_on_graph_event` as the
        callback for incoming GraphEvents.
        """
        try:
            if not source:
                return
            if hasattr(source, 'subscribe') and callable(getattr(source, 'subscribe')):
                try:
                    source.subscribe(self._on_graph_event)
                    print('[OperatorManager] Subscribed to graph events (source.subscribe)')
                    return
                except Exception:
                    pass

            # Fallback: if source has an `event_bus` attribute with subscribe
            eb = getattr(source, 'event_bus', None)
            if eb and hasattr(eb, 'subscribe') and callable(getattr(eb, 'subscribe')):
                try:
                    eb.subscribe(self._on_graph_event)
                    print('[OperatorManager] Subscribed to graph events via source.event_bus')
                    return
                except Exception:
                    pass

            # Last resort: if source itself exposes `subscribe` as non-callable or via dict
            print('[OperatorManager] subscribe_to_graph_events: no subscribe() found on source')
        except Exception as e:
            print(f"[OperatorManager] subscribe_to_graph_events error: {e}")

    def _on_graph_event(self, ge) -> None:
        """Translate a GraphEvent (dict or SimpleNamespace) into an EntityEvent and broadcast it.

        Handles common event types: NODE_CREATE, NODE_UPDATE, NODE_DELETE,
        EDGE_CREATE / HYPEREDGE_CREATE and deletions.
        """
        try:
            # support both dict-like and object-like events
            if ge is None:
                return
            if isinstance(ge, dict):
                et = ge.get('event_type')
                eid = ge.get('entity_id') or (ge.get('entity_data') or {}).get('id')
                ekind = ge.get('entity_kind') or ge.get('entity_type') or (ge.get('entity_data') or {}).get('kind')
                data = ge.get('entity_data') or {}
            else:
                et = getattr(ge, 'event_type', None)
                eid = getattr(ge, 'entity_id', None) or (getattr(ge, 'entity_data', {}) or {}).get('id')
                ekind = getattr(ge, 'entity_kind', None) or getattr(ge, 'entity_type', None) or (getattr(ge, 'entity_data', {}) or {}).get('kind')
                data = getattr(ge, 'entity_data', {}) or {}

            if not et:
                return

            # Normalize entity_type for broadcasting
            entity_type = ekind or 'entity'

            # Map GraphEvent -> EntityEventType
            seq = None
            try:
                seq = int(getattr(ge, 'sequence_id', None) or (ge.get('sequence_id') if isinstance(ge, dict) else None))
            except Exception:
                seq = None

            if et in ('NODE_CREATE', 'NODE_UPDATE'):
                evt = EntityEventType.CREATE if et == 'NODE_CREATE' else EntityEventType.UPDATE
                self.broadcast_entity_event(evt, eid or data.get('id', ''), entity_type, data or {}, operator=None, sequence_id=seq)
            elif et in ('NODE_DELETE',):
                self.broadcast_entity_event(EntityEventType.DELETE, eid or '', entity_type, {}, operator=None, sequence_id=seq)
            elif et in ('EDGE_CREATE', 'HYPEREDGE_CREATE'):
                self.broadcast_entity_event(EntityEventType.CREATE, eid or data.get('id', ''), entity_type or 'edge', data or {}, operator=None, sequence_id=seq)
            elif et in ('EDGE_DELETE', 'HYPEREDGE_DELETE'):
                self.broadcast_entity_event(EntityEventType.DELETE, eid or '', entity_type or 'edge', {}, operator=None, sequence_id=seq)
            else:
                # Unknown event types are ignored
                return
        except Exception as e:
            print(f"[OperatorManager] _on_graph_event error: {e}")

    # ==================== Team Management ====================

    def create_team(self, team_name: str, created_by: Optional[str] = None) -> Optional[str]:
        """Create a new team"""
        team_id = str(uuid.uuid4())
        created_at = datetime.utcnow().isoformat() + "Z"

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            cursor.execute('''
                INSERT INTO teams (team_id, team_name, created_at, created_by)
                VALUES (?, ?, ?, ?)
            ''', (team_id, team_name, created_at, created_by))
            conn.commit()
            return team_id
        except sqlite3.IntegrityError:
            return None
        finally:
            conn.close()

    def assign_to_team(self, operator_id: str, team_id: str) -> bool:
        """Assign an operator to a team"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE operators SET team_id = ? WHERE operator_id = ?
        ''', (team_id, operator_id))
        conn.commit()
        affected = cursor.rowcount
        conn.close()

        if affected > 0 and operator_id in self.operators:
            self.operators[operator_id].team_id = team_id
            return True
        return False

    def get_team_members(self, team_id: str) -> List[Operator]:
        """Get all operators in a team"""
        return [op for op in self.operators.values() if op.team_id == team_id]

    # ==================== Statistics ====================

    def get_stats(self) -> Dict:
        """Get session manager statistics"""
        with self.lock:
            return {
                "total_operators": len(self.operators),
                "active_sessions": len(self.sessions),
                "connected_sse_clients": len([c for c in self.sse_clients.values() if c.connected]),
                "connected_ws_clients": len([c for c in self.ws_clients.values() if c.connected]),
                "active_rooms": len(self.rooms),
                "cached_entities": len(self.entity_cache),
                "event_sequence": self.entity_sequence
            }

    # ==================== Room/Channel Management ====================

    def create_room(
        self,
        room_name: str,
        room_type: str = "custom",
        created_by: Optional[str] = None,
        capacity: int = 50,
        is_private: bool = False,
        password: Optional[str] = None,
        metadata: Optional[Dict] = None
    ) -> Optional[Room]:
        """Create a new room/channel"""
        room_id = str(uuid.uuid4())
        created_at = datetime.utcnow().isoformat() + "Z"
        password_hash = self._hash_password(password) if password else None

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            cursor.execute('''
                INSERT INTO rooms (room_id, room_name, room_type, created_at, created_by, capacity, is_private, password_hash, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (room_id, room_name, room_type, created_at, created_by, capacity,
                  1 if is_private else 0, password_hash, json.dumps(metadata or {})))
            conn.commit()

            room = Room(
                room_id=room_id,
                room_name=room_name,
                room_type=room_type,
                created_at=created_at,
                created_by=created_by or "",
                capacity=capacity,
                is_private=is_private,
                password_hash=password_hash,
                metadata=metadata or {}
            )

            with self.lock:
                self.rooms[room_id] = room
                self.room_members[room_id] = set()
                self.room_entities[room_id] = {}

            print(f"[RoomManager] Created room: {room_name} ({room_type})")

            # Broadcast room creation
            self._broadcast_room_event(EntityEventType.ROOM_CREATED, room)

            return room

        except sqlite3.IntegrityError as e:
            print(f"[RoomManager] Room creation failed: {e}")
            return None
        finally:
            conn.close()

    def get_room(self, room_id: str) -> Optional[Room]:
        """Get room by ID"""
        return self.rooms.get(room_id)

    def get_room_by_name(self, room_name: str) -> Optional[Room]:
        """Get room by name"""
        for room in self.rooms.values():
            if room.room_name == room_name:
                return room
        return None

    def list_rooms(self, include_private: bool = False) -> List[Dict]:
        """List all available rooms"""
        rooms_list = []
        for room_id, room in self.rooms.items():
            if room.is_private and not include_private:
                continue
            rooms_list.append({
                **room.to_dict(),
                "member_count": len(self.room_members.get(room_id, [])),
                "entity_count": len(self.room_entities.get(room_id, {}))
            })
        return rooms_list

    def join_room(
        self,
        room_id: str,
        session_id: str,
        password: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Join an operator to a room"""
        room = self.rooms.get(room_id)
        if not room:
            return False, "Room not found"

        # Check capacity
        members = self.room_members.get(room_id, set())
        if len(members) >= room.capacity:
            return False, "Room is full"

        # Check password for private rooms
        if room.is_private and room.password_hash:
            if not password or self._hash_password(password) != room.password_hash:
                return False, "Invalid password"

        # Get session and operator
        session = self.sessions.get(session_id)
        if not session:
            return False, "Invalid session"

        operator = self.operators.get(session.operator_id)
        if not operator:
            return False, "Operator not found"

        # Join room
        with self.lock:
            if room_id not in self.room_members:
                self.room_members[room_id] = set()
            self.room_members[room_id].add(session_id)

            # Add to SSE client's rooms
            if session_id in self.sse_clients:
                self.sse_clients[session_id].rooms.add(room_id)
            if session_id in self.ws_clients:
                self.ws_clients[session_id].rooms.add(room_id)

        # Persist membership
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute('''
                INSERT OR REPLACE INTO room_membership (room_id, operator_id, session_id, joined_at, role)
                VALUES (?, ?, ?, ?, ?)
            ''', (room_id, session.operator_id, session_id, datetime.utcnow().isoformat() + "Z", "member"))
            conn.commit()
        finally:
            conn.close()

        print(f"[RoomManager] {operator.callsign} joined room: {room.room_name}")

        # Broadcast join event to room members
        self._broadcast_to_room(room_id, EntityEventType.OPERATOR_JOINED, {
            "room_id": room_id,
            "operator_id": operator.operator_id,
            "callsign": operator.callsign,
            "joined_at": datetime.utcnow().isoformat() + "Z"
        }, exclude_session=None)

        # Send room's existing entities to the joining client
        self._send_room_entities_to_client(room_id, session_id)

        return True, "Joined room successfully"

    def leave_room(self, room_id: str, session_id: str) -> Tuple[bool, str]:
        """Remove an operator from a room"""
        if room_id not in self.rooms:
            return False, "Room not found"

        session = self.sessions.get(session_id)
        if not session:
            return False, "Invalid session"

        operator = self.operators.get(session.operator_id)

        with self.lock:
            if room_id in self.room_members:
                self.room_members[room_id].discard(session_id)

            # Remove from client's rooms
            if session_id in self.sse_clients:
                self.sse_clients[session_id].rooms.discard(room_id)
            if session_id in self.ws_clients:
                self.ws_clients[session_id].rooms.discard(room_id)

        # Remove from database
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM room_membership WHERE room_id = ? AND session_id = ?", (room_id, session_id))
        conn.commit()
        conn.close()

        if operator:
            print(f"[RoomManager] {operator.callsign} left room: {self.rooms[room_id].room_name}")

            # Broadcast leave event
            self._broadcast_to_room(room_id, EntityEventType.OPERATOR_LEFT, {
                "room_id": room_id,
                "operator_id": operator.operator_id,
                "callsign": operator.callsign,
                "left_at": datetime.utcnow().isoformat() + "Z"
            })

        return True, "Left room successfully"

    def close_room(self, room_id: str, closed_by: str) -> Tuple[bool, str]:
        """Close/delete a room"""
        room = self.rooms.get(room_id)
        if not room:
            return False, "Room not found"

        # Don't allow closing the Global room
        if room.room_type == "global":
            return False, "Cannot close the Global room"

        # Broadcast close event before removing
        self._broadcast_to_room(room_id, EntityEventType.ROOM_CLOSED, {
            "room_id": room_id,
            "room_name": room.room_name,
            "closed_by": closed_by,
            "closed_at": datetime.utcnow().isoformat() + "Z"
        })

        with self.lock:
            # Remove all members
            if room_id in self.room_members:
                for session_id in list(self.room_members[room_id]):
                    if session_id in self.sse_clients:
                        self.sse_clients[session_id].rooms.discard(room_id)
                    if session_id in self.ws_clients:
                        self.ws_clients[session_id].rooms.discard(room_id)
                del self.room_members[room_id]

            # Remove room entities
            if room_id in self.room_entities:
                del self.room_entities[room_id]

            # Remove room
            del self.rooms[room_id]

        # Remove from database
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM room_membership WHERE room_id = ?", (room_id,))
        cursor.execute("DELETE FROM room_entities WHERE room_id = ?", (room_id,))
        cursor.execute("DELETE FROM rooms WHERE room_id = ?", (room_id,))
        conn.commit()
        conn.close()

        print(f"[RoomManager] Room closed: {room.room_name}")
        return True, "Room closed successfully"

    def get_room_members(self, room_id: str) -> List[Dict]:
        """Get list of operators in a room"""
        members = []
        session_ids = self.room_members.get(room_id, set())

        for session_id in session_ids:
            session = self.sessions.get(session_id)
            if session:
                operator = self.operators.get(session.operator_id)
                if operator:
                    members.append({
                        "operator_id": operator.operator_id,
                        "callsign": operator.callsign,
                        "role": operator.role.value,
                        "session_id": session_id,
                        "current_view": session.current_view,
                        "last_heartbeat": session.last_heartbeat
                    })
        return members

    def _send_room_entities_to_client(self, room_id: str, session_id: str):
        """Send all entities in a room to a newly joined client"""
        entities = self.room_entities.get(room_id, {})

        for entity_id, entity_info in entities.items():
            event = EntityEvent(
                event_type=EntityEventType.PREEXISTING,
                entity_id=entity_id,
                entity_type=entity_info.get("type", "unknown"),
                entity_data={
                    **entity_info.get("data", {}),
                    "room_id": room_id
                },
                provenance=Provenance(
                    source_id="system",
                    source_description="Room sync",
                    source_update_time=datetime.utcnow().isoformat() + "Z"
                ),
                timestamp=datetime.utcnow().isoformat() + "Z",
                sequence_id=self.entity_sequence
            )

            # Send to specific client
            if session_id in self.sse_clients:
                self.sse_clients[session_id].send(event)
            if session_id in self.ws_clients and self.ws_clients[session_id].connected:
                self.ws_clients[session_id].send_sync(event)

    def _broadcast_room_event(self, event_type: EntityEventType, room: Room):
        """Broadcast room lifecycle event to all connected clients"""
        with self.lock:
            self.entity_sequence += 1

            event = EntityEvent(
                event_type=event_type,
                entity_id=room.room_id,
                entity_type="room",
                entity_data=room.to_dict(),
                provenance=Provenance(
                    source_id="system",
                    source_description="Room Manager",
                    source_update_time=datetime.utcnow().isoformat() + "Z"
                ),
                timestamp=datetime.utcnow().isoformat() + "Z",
                sequence_id=self.entity_sequence
            )

            # Broadcast to all clients
            for client in self.sse_clients.values():
                if client.connected:
                    client.send(event)
            for client in self.ws_clients.values():
                if client.connected:
                    client.send_sync(event)

    def _broadcast_to_room(
        self,
        room_id: str,
        event_type: EntityEventType,
        data: Dict,
        operator: Union[Operator, str, None] = None,
        exclude_session: Optional[str] = None
    ):
        """Broadcast an event to all members of a room"""
        with self.lock:
            self.entity_sequence += 1

            if operator:
                if isinstance(operator, str):
                    provenance = Provenance(
                        source_id=operator,
                        source_description=operator,
                        source_update_time=datetime.utcnow().isoformat() + "Z"
                    )
                else:
                    provenance = Provenance.from_operator(operator)
            else:
                provenance = Provenance(
                    source_id="system",
                    source_description="System",
                    source_update_time=datetime.utcnow().isoformat() + "Z"
                )

            event = EntityEvent(
                event_type=event_type,
                entity_id=data.get("entity_id", room_id),
                entity_type=data.get("entity_type", "room_event"),
                entity_data={**data, "room_id": room_id},
                provenance=provenance,
                timestamp=datetime.utcnow().isoformat() + "Z",
                sequence_id=self.entity_sequence
            )

            # Get room members
            members = self.room_members.get(room_id, set())

            # Send to SSE clients in room
            for session_id in members:
                if session_id == exclude_session:
                    continue
                if session_id in self.sse_clients and self.sse_clients[session_id].connected:
                    self.sse_clients[session_id].send(event)
                if session_id in self.ws_clients and self.ws_clients[session_id].connected:
                    self.ws_clients[session_id].send_sync(event)

    def publish_to_room(
        self,
        room_id: str,
        entity_id: str,
        entity_type: str,
        entity_data: Dict,
        operator: Union[Operator, str, None] = None,
        exclude_session: Optional[str] = None
    ) -> bool:
        """Publish an entity to a specific room"""
        if room_id not in self.rooms:
            return False

        with self.lock:
            self.entity_sequence += 1

            # Check if entity exists
            is_update = entity_id in self.room_entities.get(room_id, {})
            event_type = EntityEventType.UPDATE if is_update else EntityEventType.CREATE

            # Update room entity cache
            if room_id not in self.room_entities:
                self.room_entities[room_id] = {}

            self.room_entities[room_id][entity_id] = {
                "id": entity_id,
                "type": entity_type,
                "data": entity_data
            }

        # Handle string operator (ID) or Operator object
        creator_id = None
        if operator:
            if isinstance(operator, str):
                creator_id = operator
            elif hasattr(operator, 'operator_id'):
                creator_id = operator.operator_id

        # Persist to database
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        now = datetime.utcnow().isoformat() + "Z"
        cursor.execute('''
            INSERT OR REPLACE INTO room_entities (room_id, entity_id, entity_type, entity_data, created_at, updated_at, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (room_id, entity_id, entity_type, json.dumps(entity_data), now, now, creator_id))
        conn.commit()
        conn.close()

        # Broadcast to room
        self._broadcast_to_room(room_id, event_type, {
            "entity_id": entity_id,
            "entity_type": entity_type,
            "entity_data": entity_data
        }, operator, exclude_session)

        return True

    def delete_from_room(
        self,
        room_id: str,
        entity_id: str,
        operator: Union[Operator, str, None] = None
    ) -> bool:
        """Delete an entity from a room"""
        if room_id not in self.rooms:
            return False

        with self.lock:
            if room_id in self.room_entities and entity_id in self.room_entities[room_id]:
                del self.room_entities[room_id][entity_id]
            else:
                return False

        # Remove from database
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM room_entities WHERE room_id = ? AND entity_id = ?", (room_id, entity_id))
        conn.commit()
        conn.close()

        # Broadcast deletion
        self._broadcast_to_room(room_id, EntityEventType.DELETE, {
            "entity_id": entity_id,
            "entity_type": "entity",
            "entity_data": {"deleted": True}
        }, operator)

        return True

    def send_message_to_room(
        self,
        room_id: str,
        message: str,
        operator: Operator,
        message_type: str = "chat"
    ) -> bool:
        """Send a message to a room"""
        if room_id not in self.rooms:
            return False

        self._broadcast_to_room(room_id, EntityEventType.ROOM_MESSAGE, {
            "message_id": str(uuid.uuid4()),
            "message": message,
            "message_type": message_type,
            "sender_id": operator.operator_id,
            "sender_callsign": operator.callsign,
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }, operator)

        return True

    # ==================== WebSocket Client Management ====================

    def register_ws_client(self, session_token: str, websocket) -> Optional[WebSocketClient]:
        """Register a new WebSocket client"""
        session = self.validate_session(session_token)
        if not session:
            return None

        client = WebSocketClient(session.session_id, session.operator_id, websocket)

        with self.lock:
            self.ws_clients[session.session_id] = client

        operator = self.operators.get(session.operator_id)
        if operator:
            print(f"[WebSocket] Client connected: {operator.callsign}")

        # Auto-join Global room
        global_room = self.get_room_by_name("Global")
        if global_room:
            self.join_room(global_room.room_id, session.session_id)

        # Replay recent events from Redis stream to this WebSocket client (if available)
        if self.redis:
            try:
                stream = "entity_events_stream"
                entries = self.redis.xrevrange(stream, max='+', min='-', count=100)
                if entries:
                    for msg_id, fields in reversed(entries):
                        data = fields.get(b'data') if isinstance(list(fields.keys())[0], bytes) else fields.get('data')
                        if isinstance(data, bytes):
                            data = data.decode()
                        try:
                            obj = json.loads(data)
                            prov = Provenance(**obj.get('provenance', {})) if obj.get('provenance') else Provenance()
                            evt = EntityEvent(
                                event_type=EntityEventType(obj.get('event_type')),
                                entity_id=obj.get('entity_id',''),
                                entity_type=obj.get('entity_type','entity'),
                                entity_data=obj.get('entity_data',{}),
                                provenance=prov,
                                timestamp=obj.get('timestamp',''),
                                sequence_id=obj.get('sequence_id',0)
                            )
                            try:
                                client.send_sync(evt)
                            except Exception:
                                try:
                                    # fallback to async send
                                    import asyncio
                                    asyncio.run(client.send(evt))
                                except Exception:
                                    pass
                        except Exception:
                            continue
            except Exception as e:
                print(f"[OperatorManager] Failed to replay Redis stream to ws client: {e}")

        return client

    def unregister_ws_client(self, session_id: str):
        """Unregister a WebSocket client and leave all rooms"""
        with self.lock:
            if session_id in self.ws_clients:
                client = self.ws_clients[session_id]

                # Leave all rooms
                for room_id in list(client.rooms):
                    self.leave_room(room_id, session_id)

                client.disconnect()
                del self.ws_clients[session_id]

    def handle_ws_message(self, session_id: str, message: Dict) -> Optional[Dict]:
        """Handle incoming WebSocket message and return response"""
        action = message.get("action")

        session = self.sessions.get(session_id)
        if not session:
            return {"error": "Invalid session"}

        operator = self.operators.get(session.operator_id)

        if action == "join_room":
            room_id = message.get("room_id")
            password = message.get("password")
            success, msg = self.join_room(room_id, session_id, password)
            return {"action": "join_room", "success": success, "message": msg, "room_id": room_id}

        elif action == "leave_room":
            room_id = message.get("room_id")
            success, msg = self.leave_room(room_id, session_id)
            return {"action": "leave_room", "success": success, "message": msg, "room_id": room_id}

        elif action == "create_room":
            room = self.create_room(
                room_name=message.get("room_name"),
                room_type=message.get("room_type", "custom"),
                created_by=session.operator_id,
                capacity=message.get("capacity", 50),
                is_private=message.get("is_private", False),
                password=message.get("password"),
                metadata=message.get("metadata")
            )
            if room:
                # Auto-join creator to the room
                self.join_room(room.room_id, session_id)
                return {"action": "create_room", "success": True, "room": room.to_dict()}
            return {"action": "create_room", "success": False, "message": "Failed to create room"}

        elif action == "list_rooms":
            rooms = self.list_rooms(include_private=message.get("include_private", False))
            return {"action": "list_rooms", "rooms": rooms}

        elif action == "room_members":
            room_id = message.get("room_id")
            members = self.get_room_members(room_id)
            return {"action": "room_members", "room_id": room_id, "members": members}

        elif action == "publish_entity":
            room_id = message.get("room_id")
            entity_id = message.get("entity_id")
            entity_type = message.get("entity_type", "entity")
            entity_data = message.get("entity_data", {})

            if room_id:
                success = self.publish_to_room(room_id, entity_id, entity_type, entity_data, operator, session_id)
            else:
                # Global publish
                self.broadcast_entity_event(
                    EntityEventType.UPDATE if entity_id in self.entity_cache else EntityEventType.CREATE,
                    entity_id, entity_type, entity_data, operator, session_id
                )
                success = True

            return {"action": "publish_entity", "success": success, "entity_id": entity_id}

        elif action == "delete_entity":
            room_id = message.get("room_id")
            entity_id = message.get("entity_id")

            if room_id:
                success = self.delete_from_room(room_id, entity_id, operator)
            else:
                self.broadcast_entity_event(EntityEventType.DELETE, entity_id, "entity", {}, operator)
                success = True

            return {"action": "delete_entity", "success": success, "entity_id": entity_id}

        elif action == "send_message":
            room_id = message.get("room_id")
            text = message.get("message", "")
            message_type = message.get("message_type", "chat")

            if operator and room_id:
                success = self.send_message_to_room(room_id, text, operator, message_type)
                return {"action": "send_message", "success": success}
            return {"action": "send_message", "success": False, "message": "Invalid operator or room"}

        elif action == "heartbeat":
            current_view = message.get("current_view")
            self.heartbeat(session.session_token, current_view)
            return {"action": "heartbeat", "success": True}

        elif action == "get_rooms":
            client = self.ws_clients.get(session_id)
            rooms = list(client.rooms) if client else []
            return {"action": "get_rooms", "rooms": rooms}

        else:
            return {"error": f"Unknown action: {action}"}


# Global instance
_session_manager: Optional[OperatorSessionManager] = None


def get_session_manager(db_path: Optional[str] = None, internal_token: Optional[str] = None) -> OperatorSessionManager:
    """Get or create the global session manager instance"""
    global _session_manager
    if _session_manager is None:
        _session_manager = OperatorSessionManager(db_path=db_path, internal_token=internal_token)
    return _session_manager


if __name__ == "__main__":
    # Test the module
    manager = get_session_manager()

    # Register test operator
    op = manager.register_operator(
        callsign="BRAVO-1",
        email="bravo1@command-ops.local",
        password="test123",
        role=OperatorRole.OPERATOR
    )

    if op:
        print(f"Created operator: {op.to_dict()}")

    # Authenticate
    session = manager.authenticate("BRAVO-1", "test123")
    if session:
        print(f"Session created: {session.to_dict()}")

        # Test heartbeat
        manager.heartbeat(session.session_token, {"center": [40.7, -74.0], "zoom": 10})

        # Get active operators
        active = manager.get_active_operators()
        print(f"Active operators: {active}")

        # Stats
        print(f"Stats: {manager.get_stats()}")
