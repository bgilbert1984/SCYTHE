# writebus.py
from __future__ import annotations

import contextvars
from dataclasses import dataclass, field, asdict
import functools
from typing import Any, Dict, List, Optional, Tuple, Union
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _safe_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, default=str, separators=(",", ":"))


def _stable_hash(obj: Any) -> str:
    return hashlib.sha256(_safe_json(obj).encode("utf-8")).hexdigest()


def _coalesce(*vals):
    for v in vals:
        if v is not None and v != "":
            return v
    return None


def _writebus_db_path(operator_manager: Any = None, explicit_path: Optional[str] = None) -> str:
    if explicit_path:
        return explicit_path
    op_db = getattr(operator_manager, "db_path", None)
    if op_db:
        return str(op_db)
    return os.path.join(os.getcwd(), "writebus_state.sqlite3")


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def _call_maybe(obj: Any, *args, method: str = "", **kwargs) -> Any:
    if obj is None:
        return None
    if method and hasattr(obj, method):
        return getattr(obj, method)(*args, **kwargs)
    if callable(obj):
        return obj(*args, **kwargs)
    return None


_WRITEBUS_KERNEL_DEPTH: contextvars.ContextVar[int] = contextvars.ContextVar(
    "writebus_kernel_depth",
    default=0,
)


def writebus_kernel_active() -> bool:
    return _WRITEBUS_KERNEL_DEPTH.get() > 0


class WriteBusKernelViolation(PermissionError):
    pass


class _WriteBusKernelScope:
    def __enter__(self):
        self._token = _WRITEBUS_KERNEL_DEPTH.set(_WRITEBUS_KERNEL_DEPTH.get() + 1)
        return self

    def __exit__(self, exc_type, exc, tb):
        _WRITEBUS_KERNEL_DEPTH.reset(self._token)
        return False


class CommitStatus:
    PENDING_GRAPH = "PENDING_GRAPH"
    GRAPH_APPLIED = "GRAPH_APPLIED"
    ROOM_PERSISTED = "ROOM_PERSISTED"
    ROOM_SKIPPED = "ROOM_SKIPPED"
    BUS_PUBLISHED = "BUS_PUBLISHED"
    BUS_SKIPPED = "BUS_SKIPPED"
    AUDITED = "AUDITED"
    AUDIT_SKIPPED = "AUDIT_SKIPPED"
    COMMITTED = "COMMITTED"
    FAILED_PARTIAL = "FAILED_PARTIAL"
    REJECTED = "REJECTED"


def _write_result_from_dict(data: Dict[str, Any]) -> "WriteResult":
    return WriteResult(
        ok=bool(data.get("ok")),
        entity_id=str(data.get("entity_id") or ""),
        entity_type=str(data.get("entity_type") or ""),
        room_name=str(data.get("room_name") or data.get("room") or ""),
        persisted=bool(data.get("persisted")),
        graph_applied=bool(data.get("graph_applied")),
        commit_status=str(data.get("commit_status") or "UNKNOWN"),
        errors=list(data.get("errors") or []),
        debug=dict(data.get("debug") or {}),
    )


def _write_result_to_dict(result: "WriteResult") -> Dict[str, Any]:
    return asdict(result)


class SQLiteIdempotencyStore:
    """Durable idempotency gate. Completed results are replayed verbatim."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.RLock()
        _ensure_parent_dir(self.db_path)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS writebus_idempotency (
                    idempotency_key TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    request_json TEXT,
                    result_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def seen(self, idempotency_key: str) -> bool:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM writebus_idempotency WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            return row is not None

    def status(self, idempotency_key: str) -> Optional[str]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM writebus_idempotency WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            return row[0] if row else None

    def updated_at(self, idempotency_key: str) -> Optional[str]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT updated_at FROM writebus_idempotency WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            return row[0] if row else None

    def pending_age_seconds(self, idempotency_key: str) -> Optional[float]:
        updated = _parse_utc_iso(self.updated_at(idempotency_key))
        if updated is None:
            return None
        return max(0.0, (datetime.now(timezone.utc) - updated).total_seconds())

    def claim_stale_pending(
        self,
        idempotency_key: str,
        *,
        stale_after_seconds: float = 300.0,
        request: Optional[Dict[str, Any]] = None,
    ) -> bool:
        now = _utc_now_iso()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT status, updated_at FROM writebus_idempotency WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if not row:
                return False
            status, updated_at = row
            if status != CommitStatus.PENDING_GRAPH:
                return False
            updated = _parse_utc_iso(updated_at)
            if updated is None:
                stale = True
            else:
                stale = (datetime.now(timezone.utc) - updated).total_seconds() >= stale_after_seconds
            if not stale:
                return False
            cur = conn.execute(
                """
                UPDATE writebus_idempotency
                SET status = ?, request_json = ?, result_json = NULL, updated_at = ?
                WHERE idempotency_key = ? AND status = ? AND updated_at = ?
                """,
                (
                    CommitStatus.PENDING_GRAPH,
                    _safe_json(request or {}),
                    now,
                    idempotency_key,
                    CommitStatus.PENDING_GRAPH,
                    updated_at,
                ),
            )
            return cur.rowcount == 1

    def result(self, idempotency_key: str) -> Optional[WriteResult]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT result_json FROM writebus_idempotency WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if not row or not row[0]:
                return None
            return _write_result_from_dict(json.loads(row[0]))

    def record_pending(self, idempotency_key: str, request: Optional[Dict[str, Any]] = None) -> bool:
        now = _utc_now_iso()
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO writebus_idempotency
                    (idempotency_key, status, request_json, result_json, created_at, updated_at)
                VALUES (?, ?, ?, NULL, ?, ?)
                """,
                (
                    idempotency_key,
                    CommitStatus.PENDING_GRAPH,
                    _safe_json(request or {}),
                    now,
                    now,
                ),
            )
            return cur.rowcount == 1

    def record_result(self, idempotency_key: str, result: WriteResult) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE writebus_idempotency
                SET status = ?, result_json = ?, updated_at = ?
                WHERE idempotency_key = ?
                """,
                (
                    result.commit_status,
                    _safe_json(_write_result_to_dict(result)),
                    _utc_now_iso(),
                    idempotency_key,
                ),
            )


class SQLiteCommitStatusStore:
    """Append-light commit status ledger for recovery and operator diagnostics."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.RLock()
        _ensure_parent_dir(self.db_path)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS writebus_commit_status (
                    idempotency_key TEXT PRIMARY KEY,
                    entity_id TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    room_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    errors_json TEXT,
                    context_json TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS writebus_commit_status_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    idempotency_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    errors_json TEXT,
                    context_json TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )

    def record_status(
        self,
        idempotency_key: str,
        *,
        entity_id: str,
        entity_type: str,
        room_name: str,
        status: str,
        errors: Optional[List[str]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        now = _utc_now_iso()
        errors_json = _safe_json(errors or [])
        context_json = _safe_json(context or {})
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO writebus_commit_status
                    (idempotency_key, entity_id, entity_type, room_name, status, errors_json, context_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(idempotency_key) DO UPDATE SET
                    entity_id = excluded.entity_id,
                    entity_type = excluded.entity_type,
                    room_name = excluded.room_name,
                    status = excluded.status,
                    errors_json = excluded.errors_json,
                    context_json = excluded.context_json,
                    updated_at = excluded.updated_at
                """,
                (
                    idempotency_key,
                    entity_id,
                    entity_type,
                    room_name,
                    status,
                    errors_json,
                    context_json,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO writebus_commit_status_history
                    (idempotency_key, status, errors_json, context_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (idempotency_key, status, errors_json, context_json, now),
            )


class SQLiteRecordSink:
    def __init__(self, db_path: str, table_name: str):
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", table_name):
            raise ValueError("unsafe table name")
        self.db_path = db_path
        self.table_name = table_name
        self._lock = threading.RLock()
        _ensure_parent_dir(self.db_path)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.table_name} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    idempotency_key TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def write(self, record: Dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                f"""
                INSERT INTO {self.table_name} (idempotency_key, record_json, created_at)
                VALUES (?, ?, ?)
                """,
                (
                    str(record.get("idempotency_key") or ""),
                    _safe_json(record),
                    _utc_now_iso(),
                ),
            )


class SQLiteTemporalContextSnapshot:
    def __init__(self, data: Optional[Dict[str, Any]] = None):
        self._data = data or {}

    def summary(self) -> Dict[str, Any]:
        return dict(self._data)


class SQLiteTemporalContextEngine:
    """
    Compact slow-memory state for WriteBus.

    This intentionally stores a summary, not event history, so the fast write path
    can carry useful context without replaying the whole graph.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.RLock()
        _ensure_parent_dir(self.db_path)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS writebus_temporal_context (
                    entity_key TEXT PRIMARY KEY,
                    entity_id TEXT NOT NULL,
                    room_name TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    write_count INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def _key(self, entity_id: str, room_name: str) -> str:
        return _stable_hash({"entity_id": entity_id, "room_name": room_name})[:32]

    def get(self, entity_id: str, room_name: str) -> SQLiteTemporalContextSnapshot:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT summary_json FROM writebus_temporal_context WHERE entity_key = ?",
                (self._key(entity_id, room_name),),
            ).fetchone()
            if not row:
                return SQLiteTemporalContextSnapshot()
            return SQLiteTemporalContextSnapshot(json.loads(row[0]))

    def update(
        self,
        entity_id: str,
        room_name: str,
        entity_data: Dict[str, Any],
        graph_ops: List["GraphOp"],
        entity_type: Optional[str] = None,
    ) -> None:
        key = self._key(entity_id, room_name)
        now = _utc_now_iso()
        meta = dict(entity_data.get("metadata") or entity_data.get("meta") or {})
        prev = self.get(entity_id, room_name).summary()
        write_count = int(prev.get("write_count") or 0) + 1
        summary = {
            "entity_id": entity_id,
            "room_name": room_name,
            "write_count": write_count,
            "entity_type": entity_type or entity_data.get("type"),
            "last_entity_type": entity_type or entity_data.get("type"),
            "last_event_types": [op.event_type for op in graph_ops][-8:],
            "last_write_fingerprint": meta.get("write_fingerprint"),
            "last_source": (meta.get("provenance_write") or meta.get("provenance") or {}).get("source"),
            "last_updated_at": now,
        }
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO writebus_temporal_context
                    (entity_key, entity_id, room_name, summary_json, write_count, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(entity_key) DO UPDATE SET
                    summary_json = excluded.summary_json,
                    write_count = excluded.write_count,
                    updated_at = excluded.updated_at
                """,
                (key, entity_id, room_name, _safe_json(summary), write_count, now),
            )


class NoopSchemaValidator:
    def validate(self, entity_type: str, entity_data: Dict[str, Any]) -> bool:
        return True


class NoopPolicyEngine:
    def authorize(
        self,
        ctx: "WriteContext",
        entity_type: str,
        entity_data: Dict[str, Any],
        graph_ops: List["GraphOp"],
    ) -> bool:
        return True


@dataclass
class Provenance:
    source: str = "manual_ui"        # e.g. "manual_ui", "lpi_detector_v1", "pcap_ingest"
    operator_id: Optional[str] = None
    session_id: Optional[str] = None
    request_id: Optional[str] = None
    model_version: Optional[str] = None
    evidence_refs: List[str] = field(default_factory=list)  # hashes/paths/pcap ids
    timestamp: str = field(default_factory=_utc_now_iso)
    temporal_state: Dict[str, Any] = field(default_factory=dict)
    caused_by_event_id: Optional[str] = None
    parent_write_fingerprint: Optional[str] = None
    correlation_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class WriteContext:
    """
    Context derived from request/session. Registries should accept ctx, not raw globals.
    """
    room_name: str = "Global"
    mission_id: Optional[str] = None
    team_id: Optional[str] = None
    operator: Any = None                  # optional operator object
    operator_id: Optional[str] = None     # string if no operator object
    session_token: Optional[str] = None
    request_id: Optional[str] = None
    source: str = "manual_ui"
    origin_host: Optional[str] = None
    model_version: Optional[str] = None
    evidence_refs: List[str] = field(default_factory=list)
    slow_memory_id: Optional[str] = None
    temporal_embedding: List[float] = field(default_factory=list)
    temporal_state: Dict[str, Any] = field(default_factory=dict)
    caused_by_event_id: Optional[str] = None
    parent_write_fingerprint: Optional[str] = None
    correlation_id: Optional[str] = None

    def provenance(self) -> Provenance:
        op_id = _coalesce(
            self.operator_id,
            getattr(self.operator, "operator_id", None),
            getattr(self.operator, "callsign", None),
            "UNKNOWN",
        )
        ses_id = None
        if self.session_token:
            # do NOT store raw token; hash it
            ses_id = hashlib.sha256(self.session_token.encode("utf-8")).hexdigest()[:16]
        return Provenance(
            source=self.source,
            operator_id=op_id,
            session_id=ses_id,
            request_id=self.request_id,
            model_version=self.model_version,
            evidence_refs=list(self.evidence_refs),
            temporal_state=dict(self.temporal_state),
            caused_by_event_id=self.caused_by_event_id,
            parent_write_fingerprint=self.parent_write_fingerprint,
            correlation_id=self.correlation_id,
        )


@dataclass
class GraphOp:
    """
    A canonical graph operation expressed as a GraphEvent-like dict.
    We favor apply_graph_event() because it centralizes behavior and sequence IDs.
    """
    event_type: str                      # NODE_UPDATE / NODE_CREATE / EDGE_CREATE / EDGE_UPDATE / NODE_DELETE / EDGE_DELETE
    entity_id: str
    entity_data: Dict[str, Any]


@dataclass
class WriteResult:
    ok: bool
    entity_id: str
    entity_type: str
    room_name: str
    persisted: bool
    graph_applied: bool
    commit_status: str = "UNKNOWN"
    errors: List[str] = field(default_factory=list)
    debug: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CommitEnvelope:
    entity_id: str
    entity_type: str
    entity_data: Dict[str, Any]
    graph_ops: List[GraphOp]
    ctx: WriteContext
    prov: Provenance
    room_name: str
    idempotency_key: str
    persist: bool
    audit: bool
    room_id_override: Optional[str] = None
    errors: List[str] = field(default_factory=list)
    status_history: List[str] = field(default_factory=list)
    commit_status: str = CommitStatus.PENDING_GRAPH
    persisted: bool = False
    graph_applied: bool = False
    bus_published: bool = False
    audited: bool = False
    mutation_started: bool = False
    reclaimed_stale_pending: bool = False
    temporal_summary: Dict[str, Any] = field(default_factory=dict)


class WriteBus:
    """
    The only sanctioned writer that touches both persistence/broadcast AND the hypergraph.
    Everything else must call this.
    """

    def __init__(
        self,
        operator_manager: Any,
        hypergraph_engine: Any,
        *,
        default_room: str = "Global",
        graph_event_bus: Optional[Any] = None,   # optional pubsub bus
        strict_no_bypass: bool = False,
        idempotency_store: Optional[Any] = None,
        commit_status_store: Optional[Any] = None,
        schema_validator: Optional[Any] = None,
        policy_engine: Optional[Any] = None,
        dead_letter_sink: Optional[Any] = None,
        repair_sink: Optional[Any] = None,
        temporal_context: Optional[Any] = None,
        writebus_db_path: Optional[str] = None,
        idempotency_stale_seconds: float = 300.0,
    ):
        self.operator_manager = operator_manager
        self.hypergraph = hypergraph_engine
        self.default_room = default_room
        self.graph_event_bus = graph_event_bus
        self.strict_no_bypass = strict_no_bypass
        self.idempotency_stale_seconds = float(idempotency_stale_seconds)
        state_db_path = _writebus_db_path(operator_manager, writebus_db_path)
        self.idempotency_store = idempotency_store or SQLiteIdempotencyStore(state_db_path)
        self.commit_status_store = commit_status_store or SQLiteCommitStatusStore(state_db_path)
        self.schema_validator = schema_validator or NoopSchemaValidator()
        self.policy_engine = policy_engine or NoopPolicyEngine()
        self.dead_letter_sink = dead_letter_sink or SQLiteRecordSink(state_db_path, "writebus_dead_letter")
        self.repair_sink = repair_sink or SQLiteRecordSink(state_db_path, "writebus_repair_tasks")
        self.temporal_context = temporal_context or SQLiteTemporalContextEngine(state_db_path)
        if self.strict_no_bypass:
            self._install_kernel_enforcement()

    # --- room helpers ---

    def _ensure_room_id(self, room_name: str, operator: Any = None) -> Optional[str]:
        if not self.operator_manager:
            return None
        try:
            room = self.operator_manager.get_room_by_name(room_name)
            if room:
                if isinstance(room, dict):
                    return room.get("room_id") or room.get("id")
                return getattr(room, "room_id", None) or getattr(room, "id", None)
        except Exception:
            pass

        # best-effort create
        try:
            created = self.operator_manager.create_room(room_name, description=f"Auto-created room: {room_name}", operator=operator)
            if created:
                if isinstance(created, dict):
                    return created.get("room_id") or created.get("id")
                return getattr(created, "room_id", None) or getattr(created, "id", None)
        except Exception:
            return None
        return None

    # --- kernel enforcement ---

    def _install_kernel_enforcement(self) -> None:
        self._guard_target_methods(
            self.hypergraph,
            (
                "apply_graph_event",
                "add_node",
                "update_node",
                "remove_node",
                "add_edge",
                "remove_edge",
                "add_geo_streamline",
                "add_geo_fiber_anchor",
                "add_geo_singularity",
                "add_node_from_rf",
                "add_edge_from_rf",
            ),
            "hypergraph",
        )
        self._guard_target_methods(
            self.operator_manager,
            (
                "publish_to_room",
                "delete_from_room",
                "broadcast_entity_event",
            ),
            "operator_manager",
        )
        self._remove_graph_event_bus_hypergraph_writer()

    def _guard_target_methods(self, target: Any, method_names: Tuple[str, ...], target_name: str) -> None:
        if target is None:
            return
        for method_name in method_names:
            original = getattr(target, method_name, None)
            if not callable(original) or getattr(original, "_writebus_kernel_guard", False):
                continue

            @functools.wraps(original)
            def guarded(*args, __original=original, __method_name=method_name, __target_name=target_name, **kwargs):
                if not writebus_kernel_active():
                    raise WriteBusKernelViolation(
                        f"WriteBus kernel violation: direct {__target_name}.{__method_name}() mutation "
                        "is forbidden; route writes through WriteBus.commit()"
                    )
                return __original(*args, **kwargs)

            guarded._writebus_kernel_guard = True  # type: ignore[attr-defined]
            guarded._writebus_kernel_original = original  # type: ignore[attr-defined]
            setattr(target, f"_writebus_kernel_original_{method_name}", original)
            setattr(target, method_name, guarded)

    def _remove_graph_event_bus_hypergraph_writer(self) -> None:
        bus = self.graph_event_bus
        if bus is None or self.hypergraph is None or not hasattr(bus, "subscribers"):
            return
        try:
            subscribers = list(getattr(bus, "subscribers") or [])
            kept = []
            removed = 0
            for cb in subscribers:
                if (
                    getattr(cb, "__self__", None) is self.hypergraph
                    and getattr(getattr(cb, "__func__", None), "__name__", None) == "apply_graph_event"
                ):
                    removed += 1
                    continue
                kept.append(cb)
            if removed:
                setattr(bus, "subscribers", kept)
        except Exception:
            pass

    # --- provenance injection ---

    def _inject_provenance(self, payload: Dict[str, Any], prov: Provenance) -> Dict[str, Any]:
        """Inject write provenance WITHOUT clobbering rule/inference provenance.

        Split namespaces:
          metadata.provenance_write  → who/what wrote the GraphOp (room/source/operator)
          metadata.provenance_rule   → inference explanation (rule_id, evidence, engine)
          metadata.provenance        → merged view for backwards-compat (rule wins if present)
        """
        payload = dict(payload or {})
        meta = dict(payload.get("meta") or payload.get("metadata") or {})

        # Preserve any existing rule provenance (from inference engines)
        existing_prov = meta.get("provenance") or {}
        has_rule_prov = bool(existing_prov.get("rule_id") or existing_prov.get("engine"))

        # Always write the write-provenance namespace
        meta["provenance_write"] = prov.to_dict()

        if has_rule_prov:
            # Rule provenance exists — preserve it and merge
            meta["provenance_rule"] = dict(existing_prov)
            # Merged view: rule provenance wins, augmented with write context
            merged = dict(existing_prov)
            merged["write_source"] = prov.source
            merged["write_timestamp"] = prov.timestamp
            if prov.operator_id:
                merged["write_operator"] = prov.operator_id
            meta["provenance"] = merged
        else:
            # No rule provenance — write provenance is canonical
            meta["provenance"] = prov.to_dict()

        # keep both keys if your UI uses either
        payload["metadata"] = meta
        payload["meta"] = meta
        return payload

    # --- idempotency key ---

    def _canonical_payload_for_idempotency(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(payload or {})
        meta = dict(payload.get("metadata") or payload.get("meta") or {})
        for key in (
            "write_fingerprint",
            "provenance_write",
            "provenance",
            "trust",
            "temporal_context",
            "causality",
        ):
            meta.pop(key, None)
        if meta:
            payload["metadata"] = meta
            payload["meta"] = meta
        else:
            payload.pop("metadata", None)
            payload.pop("meta", None)
        return payload

    def _graph_ops_for_idempotency(self, graph_ops: List[GraphOp]) -> List[Dict[str, Any]]:
        return [
            {
                "event_type": op.event_type,
                "entity_id": op.entity_id,
                "entity_data": self._canonical_payload_for_idempotency(op.entity_data),
            }
            for op in (graph_ops or [])
        ]

    def _idempotency_key(
        self,
        entity_id: str,
        entity_type: str,
        payload: Dict[str, Any],
        prov: Provenance,
        graph_ops: Optional[List[GraphOp]] = None,
        room_name: Optional[str] = None,
        room_id: Optional[str] = None,
    ) -> str:
        # stable across retries of the same write
        core = {
            "entity_id": entity_id,
            "entity_type": entity_type,
            "payload_hash": _stable_hash(self._canonical_payload_for_idempotency(payload)),
            "graph_ops_hash": _stable_hash(self._graph_ops_for_idempotency(graph_ops or [])),
            "request_id": prov.request_id,
            "correlation_id": prov.correlation_id,
            "source": prov.source,
            "room_name": room_name or self.default_room,
            "room_id": room_id,
        }
        return _stable_hash(core)[:24]

    # --- staged commit helpers ---

    def _trust_metadata(self, prov: Provenance) -> Dict[str, str]:
        author_class = "anonymous"
        auth_level = "none"
        if prov.operator_id and prov.operator_id != "UNKNOWN":
            if prov.operator_id.startswith("SYSTEM:"):
                author_class = "system"
                auth_level = "bounded"
            elif prov.operator_id.startswith(("system_", "auto_", "ingest_")):
                author_class = "automated"
                auth_level = "system"
            else:
                author_class = "operator"
                auth_level = "authenticated"
        return {"author_class": author_class, "auth_level": auth_level}

    def _causality_metadata(self, ctx: WriteContext, prov: Provenance) -> Dict[str, str]:
        causality = {
            "caused_by_event_id": prov.caused_by_event_id or ctx.caused_by_event_id,
            "parent_write_fingerprint": prov.parent_write_fingerprint or ctx.parent_write_fingerprint,
            "correlation_id": prov.correlation_id or ctx.correlation_id or ctx.request_id,
        }
        return {k: v for k, v in causality.items() if v}

    def _attach_write_metadata(
        self,
        payload: Dict[str, Any],
        env: CommitEnvelope,
        *,
        include_temporal: bool = False,
    ) -> Dict[str, Any]:
        payload = self._inject_provenance(payload, env.prov)
        meta = dict(payload.get("metadata") or payload.get("meta") or {})
        meta["write_fingerprint"] = env.idempotency_key
        meta["trust"] = self._trust_metadata(env.prov)
        causality = self._causality_metadata(env.ctx, env.prov)
        if causality:
            meta["causality"] = causality
        if include_temporal and env.temporal_summary:
            meta["temporal_context"] = dict(env.temporal_summary)
        payload["metadata"] = meta
        payload["meta"] = meta
        return payload

    def _schema_validate(self, entity_type: str, entity_data: Dict[str, Any]) -> None:
        verdict = _call_maybe(self.schema_validator, entity_type, entity_data, method="validate")
        if verdict is False:
            raise ValueError(f"schema validation rejected entity_type={entity_type}")
        if isinstance(verdict, dict) and not verdict.get("ok", verdict.get("allowed", True)):
            reason = verdict.get("reason") or verdict.get("message") or "schema validation rejected write"
            raise ValueError(str(reason))

    def _authorize(self, env: CommitEnvelope) -> None:
        verdict = _call_maybe(
            self.policy_engine,
            env.ctx,
            env.entity_type,
            env.entity_data,
            env.graph_ops,
            method="authorize",
        )
        if verdict is False:
            raise PermissionError(f"policy rejected entity_type={env.entity_type}")
        if isinstance(verdict, dict) and not verdict.get("ok", verdict.get("allowed", True)):
            reason = verdict.get("reason") or verdict.get("message") or "policy rejected write"
            raise PermissionError(str(reason))

    def _temporal_summary(self, entity_id: str, room_name: str) -> Dict[str, Any]:
        if not self.temporal_context:
            return {}
        snapshot = _call_maybe(self.temporal_context, entity_id, room_name, method="get")
        if snapshot is None:
            return {}
        if hasattr(snapshot, "summary"):
            summary = snapshot.summary()
            return dict(summary or {})
        if isinstance(snapshot, dict):
            return dict(snapshot)
        return {"value": snapshot}

    def _update_temporal_context(self, env: CommitEnvelope) -> None:
        if not self.temporal_context:
            return
        try:
            if hasattr(self.temporal_context, "update"):
                try:
                    self.temporal_context.update(
                        env.entity_id,
                        env.room_name,
                        env.entity_data,
                        env.graph_ops,
                        entity_type=env.entity_type,
                    )
                except TypeError:
                    self.temporal_context.update(env.entity_id, env.room_name, env.entity_data, env.graph_ops)
            elif callable(self.temporal_context):
                self.temporal_context(env.entity_id, env.room_name, env.entity_data, env.graph_ops)
        except Exception:
            pass

    def prepare_commit(
        self,
        *,
        entity_id: str,
        entity_type: str,
        entity_data: Dict[str, Any],
        graph_ops: List[GraphOp],
        ctx: WriteContext,
        persist: bool,
        audit: bool,
        idempotency_key: Optional[str],
        room_id_override: Optional[str] = None,
    ) -> CommitEnvelope:
        room_name = ctx.room_name or self.default_room
        prov = ctx.provenance()

        # Legal boundary: every write MUST carry provenance.
        if not prov.operator_id:
            raise PermissionError(
                "WriteBus violation: missing operator provenance - "
                "construct WriteContext before commit"
            )

        raw_entity = dict(entity_data or {})
        ops = list(graph_ops or [])
        idem = idempotency_key or self._idempotency_key(
            entity_id,
            entity_type,
            raw_entity,
            prov,
            graph_ops=ops,
            room_name=room_name,
            room_id=room_id_override,
        )
        return CommitEnvelope(
            entity_id=entity_id,
            entity_type=entity_type,
            entity_data=raw_entity,
            graph_ops=ops,
            ctx=ctx,
            prov=prov,
            room_name=room_name,
            idempotency_key=idem,
            persist=persist,
            audit=audit,
            room_id_override=room_id_override,
        )

    def _idempotency_replay_or_lock(self, env: CommitEnvelope) -> Optional[WriteResult]:
        idem = env.idempotency_key
        existing = self.idempotency_store.result(idem)
        if existing:
            existing.debug = dict(existing.debug or {})
            existing.debug["idempotent_replay"] = True
            return existing
        if self.idempotency_store.seen(idem):
            if self._claim_stale_pending(env):
                return None
            status = self.idempotency_store.status(idem) or "UNKNOWN"
            return WriteResult(
                ok=False,
                entity_id=env.entity_id,
                entity_type=env.entity_type,
                room_name=env.room_name,
                persisted=False,
                graph_applied=False,
                commit_status=status,
                errors=[f"idempotency_pending:{idem}"],
                debug={"idempotency_key": idem, "idempotent_replay": False, "status": status},
            )
        inserted = self.idempotency_store.record_pending(
            idem,
            self._idempotency_request(env),
        )
        if not inserted:
            replay = self.idempotency_store.result(idem)
            if replay:
                replay.debug = dict(replay.debug or {})
                replay.debug["idempotent_replay"] = True
                return replay
            if self._claim_stale_pending(env):
                return None
            status = self.idempotency_store.status(idem) or "UNKNOWN"
            return WriteResult(
                ok=False,
                entity_id=env.entity_id,
                entity_type=env.entity_type,
                room_name=env.room_name,
                persisted=False,
                graph_applied=False,
                commit_status=status,
                errors=[f"idempotency_pending:{idem}"],
                debug={"idempotency_key": idem, "idempotent_replay": False, "status": status},
            )
        return None

    def _idempotency_request(self, env: CommitEnvelope) -> Dict[str, Any]:
        return {
            "entity_id": env.entity_id,
            "entity_type": env.entity_type,
            "room_name": env.room_name,
            "room_id": env.room_id_override,
            "source": env.prov.source,
            "request_id": env.prov.request_id,
            "correlation_id": env.prov.correlation_id,
        }

    def _claim_stale_pending(self, env: CommitEnvelope) -> bool:
        claim = getattr(self.idempotency_store, "claim_stale_pending", None)
        if not callable(claim):
            return False
        try:
            claimed = claim(
                env.idempotency_key,
                stale_after_seconds=self.idempotency_stale_seconds,
                request=self._idempotency_request(env),
            )
        except TypeError:
            claimed = claim(env.idempotency_key)
        if claimed:
            env.reclaimed_stale_pending = True
        return bool(claimed)

    def _record_status(self, env: CommitEnvelope, status: str) -> None:
        env.commit_status = status
        env.status_history.append(status)
        self.commit_status_store.record_status(
            env.idempotency_key,
            entity_id=env.entity_id,
            entity_type=env.entity_type,
            room_name=env.room_name,
            status=status,
            errors=env.errors,
            context={
                "persist": env.persist,
                "audit": env.audit,
                "room_id_override": env.room_id_override,
                "graph_applied": env.graph_applied,
                "persisted": env.persisted,
                "bus_published": env.bus_published,
                "audited": env.audited,
                "mutation_started": env.mutation_started,
                "reclaimed_stale_pending": env.reclaimed_stale_pending,
                "status_history": env.status_history,
            },
        )

    def _finalize_payload_before_mutation(self, env: CommitEnvelope) -> None:
        self._schema_validate(env.entity_type, env.entity_data)
        env.temporal_summary = self._temporal_summary(env.entity_id, env.room_name)
        env.entity_data = self._attach_write_metadata(env.entity_data, env, include_temporal=True)
        self._authorize(env)

    def apply_graph(self, env: CommitEnvelope) -> None:
        env.graph_applied = True
        with _WriteBusKernelScope():
            for op in env.graph_ops:
                ge = {
                    "event_type": op.event_type,
                    "entity_id": op.entity_id,
                    "entity_data": self._attach_write_metadata(op.entity_data, env),
                }
                try:
                    if hasattr(self.hypergraph, "apply_graph_event") and callable(getattr(self.hypergraph, "apply_graph_event")):
                        env.mutation_started = True
                        ok = self.hypergraph.apply_graph_event(ge)
                        if not ok:
                            env.graph_applied = False
                            env.errors.append(f"graph_op_failed:{op.event_type}:{op.entity_id}")
                    else:
                        env.graph_applied = False
                        env.errors.append("hypergraph_missing_apply_graph_event")
                except Exception as e:
                    env.graph_applied = False
                    env.errors.append(f"hypergraph_exception:{type(e).__name__}:{e}")
        self._record_status(env, CommitStatus.GRAPH_APPLIED if env.graph_applied else CommitStatus.FAILED_PARTIAL)

    def persist_room(self, env: CommitEnvelope) -> None:
        if not env.persist:
            env.persisted = False
            self._record_status(env, CommitStatus.ROOM_SKIPPED)
            return
        if not self.operator_manager:
            env.errors.append("operator_manager_missing")
            return
        try:
            with _WriteBusKernelScope():
                room_id = env.room_id_override or self._ensure_room_id(env.room_name, operator=env.ctx.operator)
                if room_id:
                    result = self.operator_manager.publish_to_room(
                        room_id,
                        entity_id=env.entity_id,
                        entity_type=env.entity_type,
                        entity_data=env.entity_data,
                        operator=env.ctx.operator if env.ctx.operator is not None else env.ctx.operator_id,
                    )
                    if result is False:
                        env.errors.append(f"room_publish_rejected:{env.room_name}")
                    else:
                        env.persisted = True
                        env.mutation_started = True
                else:
                    env.errors.append(f"room_missing:{env.room_name}")
        except Exception as e:
            env.errors.append(f"publish_exception:{type(e).__name__}:{e}")
        if not env.errors:
            self._record_status(env, CommitStatus.ROOM_PERSISTED)

    def publish_bus(self, env: CommitEnvelope) -> None:
        if not self.graph_event_bus:
            env.bus_published = False
            self._record_status(env, CommitStatus.BUS_SKIPPED)
            return
        try:
            self.graph_event_bus.publish({
                "event_type": "ENTITY_UPSERT",
                "entity_id": env.entity_id,
                "entity_type": env.entity_type,
                "entity_data": env.entity_data,
                "room": env.room_name,
                "idempotency_key": env.idempotency_key,
                "causality": self._causality_metadata(env.ctx, env.prov),
                "timestamp": time.time(),
            })
            env.bus_published = True
            env.mutation_started = True
        except Exception as e:
            env.errors.append(f"bus_publish_exception:{type(e).__name__}:{e}")
        if not env.errors:
            self._record_status(env, CommitStatus.BUS_PUBLISHED)

    def audit_commit(self, env: CommitEnvelope) -> None:
        if not env.audit:
            self._record_status(env, CommitStatus.AUDIT_SKIPPED)
            return
        if not self.operator_manager:
            self._record_status(env, CommitStatus.AUDIT_SKIPPED)
            return
        try:
            if hasattr(self.operator_manager, "audit_entity_event"):
                self.operator_manager.audit_entity_event(
                    entity_id=env.entity_id,
                    entity_type=env.entity_type,
                    event_type="UPSERT",
                    operator_id=env.prov.operator_id,
                    timestamp=env.prov.timestamp,
                    new_data=env.entity_data,
                    idempotency_key=env.idempotency_key,
                )
            env.audited = True
        except Exception as e:
            env.errors.append(f"audit_exception:{type(e).__name__}:{e}")
        if not env.errors:
            self._record_status(env, CommitStatus.AUDITED)

    def _write_dead_letter(self, env: CommitEnvelope) -> None:
        record = {
            "idempotency_key": env.idempotency_key,
            "commit_status": env.commit_status,
            "status_history": list(env.status_history),
            "errors": list(env.errors),
            "entity_id": env.entity_id,
            "entity_type": env.entity_type,
            "room_name": env.room_name,
            "entity_data": env.entity_data,
            "graph_ops": [
                {"event_type": op.event_type, "entity_id": op.entity_id, "entity_data": op.entity_data}
                for op in env.graph_ops
            ],
            "causality": self._causality_metadata(env.ctx, env.prov),
            "mutation_started": env.mutation_started,
            "timestamp": _utc_now_iso(),
        }
        _call_maybe(self.dead_letter_sink, record, method="write")

    def _emit_repair_task(self, env: CommitEnvelope) -> None:
        if not (env.graph_applied and env.errors):
            return
        record = {
            "idempotency_key": env.idempotency_key,
            "entity_id": env.entity_id,
            "entity_type": env.entity_type,
            "room_name": env.room_name,
            "errors": list(env.errors),
            "needs_room_repair": env.persist and not env.persisted,
            "needs_bus_repair": bool(self.graph_event_bus) and not env.bus_published,
            "needs_audit_repair": env.audit and not env.audited,
            "entity_data": env.entity_data,
            "graph_ops": [
                {"event_type": op.event_type, "entity_id": op.entity_id, "entity_data": op.entity_data}
                for op in env.graph_ops
            ],
            "timestamp": _utc_now_iso(),
        }
        _call_maybe(self.repair_sink, record, method="write")

    def finalize_commit(self, env: CommitEnvelope) -> WriteResult:
        self._update_temporal_context(env)
        self._record_status(env, CommitStatus.COMMITTED)
        result = self._result_from_env(env, ok=True)
        self.idempotency_store.record_result(env.idempotency_key, result)
        return result

    def handle_partial_failure(self, env: CommitEnvelope) -> WriteResult:
        self._record_status(env, CommitStatus.FAILED_PARTIAL)
        self._emit_repair_task(env)
        self._write_dead_letter(env)
        result = self._result_from_env(env, ok=False)
        self.idempotency_store.record_result(env.idempotency_key, result)
        return result

    def _result_from_env(self, env: CommitEnvelope, *, ok: bool) -> WriteResult:
        return WriteResult(
            ok=ok,
            entity_id=env.entity_id,
            entity_type=env.entity_type,
            room_name=env.room_name,
            persisted=env.persisted,
            graph_applied=env.graph_applied,
            commit_status=env.commit_status,
            errors=list(env.errors),
            debug={
                "idempotency_key": env.idempotency_key,
                "provenance": env.prov.to_dict(),
                "commit_status": env.commit_status,
                "status_history": list(env.status_history),
                "causality": self._causality_metadata(env.ctx, env.prov),
                "temporal_context": dict(env.temporal_summary),
                "reclaimed_stale_pending": env.reclaimed_stale_pending,
            },
        )

    # --- core commit ---

    def commit(
        self,
        *,
        entity_id: str,
        entity_type: str,
        entity_data: Dict[str, Any],
        graph_ops: List[GraphOp],
        ctx: WriteContext,
        persist: bool = True,
        audit: bool = True,
        idempotency_key: Optional[str] = None,
        room_id_override: Optional[str] = None,
    ) -> WriteResult:
        """
        Commit a write atomically-ish across:
          (1) hypergraph apply_graph_event
          (2) operator_manager publish_to_room (SQLite + broadcast)
          (3) optional graph_event_bus publish for streaming clients
          (4) optional audit log (if your operator_manager provides it)

        The order is: graph -> room persistence -> bus publish.
        Rationale: hypergraph becomes canonical, room mirrors for collaboration/persistence.
        """
        env = self.prepare_commit(
            entity_id=entity_id,
            entity_type=entity_type,
            entity_data=entity_data,
            graph_ops=graph_ops,
            ctx=ctx,
            persist=persist,
            audit=audit,
            idempotency_key=idempotency_key,
            room_id_override=room_id_override,
        )

        try:
            self._finalize_payload_before_mutation(env)
        except Exception as e:
            env.errors.append(f"commit_exception:{type(e).__name__}:{e}")
            self._record_status(env, CommitStatus.REJECTED)
            return self._result_from_env(env, ok=False)

        replay = self._idempotency_replay_or_lock(env)
        if replay:
            return replay

        try:
            self._record_status(env, CommitStatus.PENDING_GRAPH)
            self.apply_graph(env)
            if env.errors:
                return self.handle_partial_failure(env)
            self.persist_room(env)
            if env.errors:
                return self.handle_partial_failure(env)
            self.publish_bus(env)
            if env.errors:
                return self.handle_partial_failure(env)
            self.audit_commit(env)
            if env.errors:
                return self.handle_partial_failure(env)
            return self.finalize_commit(env)
        except Exception as e:
            env.errors.append(f"commit_exception:{type(e).__name__}:{e}")
            return self.handle_partial_failure(env)


# ---------------------------
# Singleton convenience
# ---------------------------

_DEFAULT_BUS: Optional[WriteBus] = None


def init_writebus(
    operator_manager: Any,
    hypergraph_engine: Any,
    *,
    default_room: str = "Global",
    graph_event_bus: Optional[Any] = None,
    strict_no_bypass: bool = False,
    idempotency_store: Optional[Any] = None,
    commit_status_store: Optional[Any] = None,
    schema_validator: Optional[Any] = None,
    policy_engine: Optional[Any] = None,
    dead_letter_sink: Optional[Any] = None,
    repair_sink: Optional[Any] = None,
    temporal_context: Optional[Any] = None,
    writebus_db_path: Optional[str] = None,
    idempotency_stale_seconds: float = 300.0,
) -> WriteBus:
    global _DEFAULT_BUS
    _DEFAULT_BUS = WriteBus(
        operator_manager=operator_manager,
        hypergraph_engine=hypergraph_engine,
        default_room=default_room,
        graph_event_bus=graph_event_bus,
        strict_no_bypass=strict_no_bypass,
        idempotency_store=idempotency_store,
        commit_status_store=commit_status_store,
        schema_validator=schema_validator,
        policy_engine=policy_engine,
        dead_letter_sink=dead_letter_sink,
        repair_sink=repair_sink,
        temporal_context=temporal_context,
        writebus_db_path=writebus_db_path,
        idempotency_stale_seconds=idempotency_stale_seconds,
    )
    return _DEFAULT_BUS


def bus() -> WriteBus:
    if _DEFAULT_BUS is None:
        raise RuntimeError("WriteBus not initialized. Call init_writebus(...) during server startup.")
    return _DEFAULT_BUS
