"""
scythe_pg.py — Per-Instance PostgreSQL Authority Layer
=======================================================

Each SCYTHE instance gets its own embedded Postgres via pgserver.
Postgres is the **authoritative** source of truth for:
  - pcap_artifacts (ingested files)
  - sessions (structured PCAP sessions)
  - bsg_groups (behavioral group detections)
  - instance_state (evidence gate, T-state, lifecycle)

The hypergraph engine remains the **analytical** layer.
Postgres is the **forensic** layer.

    "If a human investigator cannot independently verify a claim
     via a database query, GraphOps must not assert it."

Usage:
    from scythe_pg import InstanceDB
    db = InstanceDB(data_dir='/path/to/instance/dir')
    db.mirror_session(session_dict)
    sessions = db.list_sessions()
    db.close()
"""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger('scythe_pg')

# Use canonical BSG projection helpers when exposing MCP/DB-backed projections
try:
    from NerfEngine import bsg_projection as bp
except Exception:
    bp = None

# ── Postgres availability check ──
_PG_AVAILABLE = False
_PG_IMPORT_ERROR = None
pgserver = None   # module-level sentinel; replaced by _attempt_pg_import on success
psycopg2 = None  # module-level sentinel; replaced by _attempt_pg_import on success

# Helper to attempt import and set flags.  This catches *any* error
# during import rather than just ImportError so that partially-broken
# installations (e.g. missing binaries in the bundled assets) don't
# crash the server during module import.
def _attempt_pg_import():
    global _PG_AVAILABLE, _PG_IMPORT_ERROR, pgserver, psycopg2
    try:
        import pgserver as _pgserver
        import psycopg2 as _psycopg2
        import psycopg2.extras
        pgserver = _pgserver
        psycopg2 = _psycopg2
        _PG_AVAILABLE = True
        _PG_IMPORT_ERROR = None
        return True
    except Exception as e:  # catch ImportError and other runtime errors
        # store the repr so the caller can log or display it
        _PG_IMPORT_ERROR = repr(e)
        return False

# first try normal import
if not _attempt_pg_import():
    # if module missing, try loading from embedded assets folder
    candidate = os.path.abspath(
        os.path.join(os.path.dirname(__file__), 'assets', 'pgserver-main', 'src')
    )
    if os.path.isdir(candidate):
        import sys
        sys.path.insert(0, candidate)
        if _attempt_pg_import():
            logger.info(f"Postgres module loaded from asset path: {candidate}")
        else:
            # candidate folder existed but import still failed
            logger.info(
                "Attempted to load pgserver from bundled assets (%s) but error occurred: %s",
                candidate,
                _PG_IMPORT_ERROR,
            )
    if not _PG_AVAILABLE:
        logger.info(
            "Postgres not available (%s). InstanceDB will use SQLite fallback. "
            "To enable Postgres, build binaries via scripts/build_pgserver.sh or "
            "install a proper pgserver package.",
            _PG_IMPORT_ERROR,
        )

# ── SQLite fallback ──
import sqlite3


# ===========================================================================
# SCHEMA
# ===========================================================================

_PG_SCHEMA = """
-- Extensions
CREATE EXTENSION IF NOT EXISTS vector;

-- PCAP Artifacts: source files that were ingested
CREATE TABLE IF NOT EXISTS pcap_artifacts (
    artifact_id TEXT PRIMARY KEY,
    filename    TEXT NOT NULL,
    sha256      TEXT,
    file_size   BIGINT DEFAULT 0,
    ingested_at TIMESTAMPTZ DEFAULT NOW(),
    instance_id TEXT,
    metadata    JSONB DEFAULT '{}'::jsonb
);

-- Sessions: structured network sessions extracted from artifacts
CREATE TABLE IF NOT EXISTS sessions (
    session_id  TEXT PRIMARY KEY,
    artifact_id TEXT REFERENCES pcap_artifacts(artifact_id) ON DELETE SET NULL,
    src_ip      TEXT,
    dst_ip      TEXT,
    src_port    INTEGER,
    dst_port    INTEGER,
    protocol    TEXT DEFAULT 'unknown',
    packet_count BIGINT DEFAULT 0,
    total_bytes BIGINT DEFAULT 0,
    duration_sec REAL DEFAULT 0,
    time_bucket BIGINT DEFAULT 0,
    instance_id TEXT,
    metadata    JSONB DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sessions_artifact ON sessions(artifact_id);
CREATE INDEX IF NOT EXISTS idx_sessions_protocol ON sessions(protocol);
CREATE INDEX IF NOT EXISTS idx_sessions_src_ip ON sessions(src_ip);

-- BSG Groups: behavioral group detections
CREATE TABLE IF NOT EXISTS bsg_groups (
    bsg_id      TEXT PRIMARY KEY,
    behavior    TEXT NOT NULL,
    confidence  REAL DEFAULT 0,
    member_count INTEGER DEFAULT 0,
    session_ids TEXT[],
    instance_id TEXT,
    metadata    JSONB DEFAULT '{}'::jsonb,
    detected_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_bsg_behavior ON bsg_groups(behavior);

-- Instance State: evidence gate, T-state, lifecycle
CREATE TABLE IF NOT EXISTS instance_state (
    instance_id   TEXT PRIMARY KEY,
    has_evidence   BOOLEAN DEFAULT FALSE,
    session_count  INTEGER DEFAULT 0,
    artifact_count INTEGER DEFAULT 0,
    bsg_count      INTEGER DEFAULT 0,
    t_state        TEXT DEFAULT 'INIT_EMPTY',
    t_state_id     INTEGER DEFAULT 0,
    last_ingest_at TIMESTAMPTZ,
    last_bsg_at    TIMESTAMPTZ,
    updated_at     TIMESTAMPTZ DEFAULT NOW()
);

-- MacClusters: persistent actor identities
CREATE TABLE IF NOT EXISTS mac_clusters (
    cluster_id   TEXT PRIMARY KEY,
    behavior     TEXT,
    confidence   REAL DEFAULT 0,
    motion_basis TEXT,
    centroid_lat REAL,
    centroid_lon REAL,
    drift_tensor JSONB DEFAULT '[]'::jsonb,
    embedding    VECTOR(384), -- Reflex tier (e.g. Granite)
    metadata     JSONB DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_mac_clusters_embedding ON mac_clusters USING ivfflat (embedding vector_cosine_ops);
"""

# SQLite equivalent (no JSONB, no TIMESTAMPTZ, no arrays)
_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS pcap_artifacts (
    artifact_id TEXT PRIMARY KEY,
    filename    TEXT NOT NULL,
    sha256      TEXT,
    file_size   INTEGER DEFAULT 0,
    ingested_at TEXT DEFAULT (datetime('now')),
    instance_id TEXT,
    metadata    TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id  TEXT PRIMARY KEY,
    artifact_id TEXT,
    src_ip      TEXT,
    dst_ip      TEXT,
    src_port    INTEGER,
    dst_port    INTEGER,
    protocol    TEXT DEFAULT 'unknown',
    packet_count INTEGER DEFAULT 0,
    total_bytes INTEGER DEFAULT 0,
    duration_sec REAL DEFAULT 0,
    time_bucket INTEGER DEFAULT 0,
    instance_id TEXT,
    metadata    TEXT DEFAULT '{}',
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS bsg_groups (
    bsg_id      TEXT PRIMARY KEY,
    behavior    TEXT NOT NULL,
    confidence  REAL DEFAULT 0,
    member_count INTEGER DEFAULT 0,
    session_ids TEXT DEFAULT '[]',
    instance_id TEXT,
    metadata    TEXT DEFAULT '{}',
    detected_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS instance_state (
    instance_id   TEXT PRIMARY KEY,
    has_evidence   INTEGER DEFAULT 0,
    session_count  INTEGER DEFAULT 0,
    artifact_count INTEGER DEFAULT 0,
    bsg_count      INTEGER DEFAULT 0,
    t_state        TEXT DEFAULT 'INIT_EMPTY',
    t_state_id     INTEGER DEFAULT 0,
    last_ingest_at TEXT,
    last_bsg_at    TEXT,
    updated_at     TEXT DEFAULT (datetime('now'))
);
"""


# ===========================================================================
# InstanceDB — unified interface (Postgres primary, SQLite fallback)
# ===========================================================================

class InstanceDB:
    """Per-instance authoritative database.

    Tries Postgres via pgserver first. Falls back to SQLite.
    All reads/writes go through this class — no direct SQL elsewhere.
    """

    def __init__(self, data_dir: str, instance_id: str = 'unknown'):
        self.data_dir = Path(data_dir)
        self.instance_id = instance_id
        self._pg_server = None
        self._pg_uri = None
        self._sqlite_path = None
        self._backend = None  # 'postgres' or 'sqlite'

        self._init_backend()

    def _init_backend(self):
        """Try Postgres first, fall back to SQLite."""
        if _PG_AVAILABLE:
            try:
                pg_data = self.data_dir / 'pg'
                pg_data.mkdir(parents=True, exist_ok=True)
                self._pg_server = pgserver.get_server(pg_data, cleanup_mode='stop')
                self._pg_uri = self._pg_server.get_uri()
                self._backend = 'postgres'
                logger.info(f"[InstanceDB] Postgres started for {self.instance_id}: {self._pg_uri}")
                self._init_schema_pg()
                return
            except Exception as e:
                logger.warning(f"[InstanceDB] Postgres init failed, falling back to SQLite: {e}")
                self._pg_server = None

        # SQLite fallback
        self._sqlite_path = str(self.data_dir / 'scythe_authority.db')
        self._backend = 'sqlite'
        logger.info(f"[InstanceDB] Using SQLite for {self.instance_id}: {self._sqlite_path}")
        self._init_schema_sqlite()

    def _init_schema_pg(self):
        """Create schema in Postgres."""
        conn = psycopg2.connect(self._pg_uri)
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(_PG_SCHEMA)
            logger.info(f"[InstanceDB] Postgres schema initialized for {self.instance_id}")
        finally:
            conn.close()

    def _init_schema_sqlite(self):
        """Create schema in SQLite."""
        conn = sqlite3.connect(self._sqlite_path)
        try:
            conn.executescript(_SQLITE_SCHEMA)
            conn.commit()
            logger.info(f"[InstanceDB] SQLite schema initialized for {self.instance_id}")
        finally:
            conn.close()

    @contextmanager
    def _conn(self):
        """Get a database connection (Postgres or SQLite)."""
        if self._backend == 'postgres':
            conn = psycopg2.connect(self._pg_uri)
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        else:
            conn = sqlite3.connect(self._sqlite_path)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    # ===================================================================
    # ARTIFACT OPERATIONS
    # ===================================================================

    def upsert_artifact(self, artifact_id: str, filename: str,
                        sha256: str = None, file_size: int = 0,
                        metadata: dict = None) -> bool:
        """Insert or update a PCAP artifact record."""
        meta_str = json.dumps(metadata or {})
        with self._conn() as conn:
            cur = conn.cursor()
            if self._backend == 'postgres':
                cur.execute("""
                    INSERT INTO pcap_artifacts (artifact_id, filename, sha256, file_size, instance_id, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (artifact_id) DO UPDATE SET
                        filename = EXCLUDED.filename,
                        sha256 = EXCLUDED.sha256,
                        file_size = EXCLUDED.file_size,
                        metadata = EXCLUDED.metadata
                """, (artifact_id, filename, sha256, file_size, self.instance_id, meta_str))
            else:
                cur.execute("""
                    INSERT OR REPLACE INTO pcap_artifacts
                    (artifact_id, filename, sha256, file_size, instance_id, metadata)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (artifact_id, filename, sha256, file_size, self.instance_id, meta_str))
            return True

    def list_artifacts(self) -> List[Dict[str, Any]]:
        """List all PCAP artifacts."""
        with self._conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM pcap_artifacts ORDER BY ingested_at DESC")
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
            return [dict(zip(cols, r)) for r in rows]

    # ===================================================================
    # SESSION OPERATIONS
    # ===================================================================

    def upsert_session(self, session_id: str, artifact_id: str = None,
                       src_ip: str = '', dst_ip: str = '',
                       src_port: int = None, dst_port: int = None,
                       protocol: str = 'unknown',
                       packet_count: int = 0, total_bytes: int = 0,
                       duration_sec: float = 0, time_bucket: int = 0,
                       metadata: dict = None) -> bool:
        """Insert or update a session record."""
        meta_str = json.dumps(metadata or {})
        with self._conn() as conn:
            cur = conn.cursor()
            if self._backend == 'postgres':
                cur.execute("""
                    INSERT INTO sessions (session_id, artifact_id, src_ip, dst_ip,
                        src_port, dst_port, protocol, packet_count, total_bytes,
                        duration_sec, time_bucket, instance_id, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (session_id) DO UPDATE SET
                        artifact_id = EXCLUDED.artifact_id,
                        src_ip = EXCLUDED.src_ip,
                        dst_ip = EXCLUDED.dst_ip,
                        protocol = EXCLUDED.protocol,
                        packet_count = EXCLUDED.packet_count,
                        total_bytes = EXCLUDED.total_bytes,
                        metadata = EXCLUDED.metadata
                """, (session_id, artifact_id, src_ip, dst_ip, src_port, dst_port,
                      protocol, packet_count, total_bytes, duration_sec, time_bucket,
                      self.instance_id, meta_str))
            else:
                cur.execute("""
                    INSERT OR REPLACE INTO sessions
                    (session_id, artifact_id, src_ip, dst_ip, src_port, dst_port,
                     protocol, packet_count, total_bytes, duration_sec, time_bucket,
                     instance_id, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (session_id, artifact_id, src_ip, dst_ip, src_port, dst_port,
                      protocol, packet_count, total_bytes, duration_sec, time_bucket,
                      self.instance_id, meta_str))
            return True

    def list_sessions(self, artifact_id: str = None,
                      protocol: str = None,
                      limit: int = 5000) -> List[Dict[str, Any]]:
        """List sessions, optionally filtered by artifact or protocol."""
        with self._conn() as conn:
            cur = conn.cursor()
            ph = '%s' if self._backend == 'postgres' else '?'
            sql = "SELECT * FROM sessions WHERE 1=1"
            params = []
            if artifact_id:
                sql += f" AND artifact_id = {ph}"
                params.append(artifact_id)
            if protocol:
                sql += f" AND protocol = {ph}"
                params.append(protocol)
            sql += f" ORDER BY created_at DESC LIMIT {ph}"
            params.append(limit)
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def session_count(self) -> int:
        """Return total session count."""
        with self._conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM sessions")
            return cur.fetchone()[0]

    def sessions_grouped_by_artifact(self) -> List[Dict[str, Any]]:
        """Return sessions grouped by artifact (for /api/pcap/ftp_sessions)."""
        with self._conn() as conn:
            cur = conn.cursor()

            # Get artifacts
            cur.execute("SELECT * FROM pcap_artifacts ORDER BY ingested_at DESC")
            art_cols = [d[0] for d in cur.description]
            artifacts_raw = cur.fetchall()

            # Get all sessions
            cur.execute("SELECT * FROM sessions ORDER BY artifact_id, created_at")
            ses_cols = [d[0] for d in cur.description]
            sessions_raw = cur.fetchall()

            # Group sessions by artifact
            sessions_by_art = {}
            for row in sessions_raw:
                s = dict(zip(ses_cols, row))
                aid = s.get('artifact_id') or '__unlinked__'
                if aid not in sessions_by_art:
                    sessions_by_art[aid] = []
                sessions_by_art[aid].append(s)

            result = []
            for row in artifacts_raw:
                art = dict(zip(art_cols, row))
                aid = art['artifact_id']
                art_sessions = sessions_by_art.pop(aid, [])
                result.append({
                    'artifact_id': aid,
                    'filename': art.get('filename', ''),
                    'sha256': art.get('sha256', ''),
                    'file_size': art.get('file_size', 0),
                    'session_count': len(art_sessions),
                    'sessions': art_sessions,
                })

            # Unlinked sessions (no artifact)
            for aid, orphans in sessions_by_art.items():
                result.append({
                    'artifact_id': aid,
                    'filename': aid,
                    'sha256': '',
                    'file_size': 0,
                    'session_count': len(orphans),
                    'sessions': orphans,
                })

            return result

    # ===================================================================
    # BSG OPERATIONS
    # ===================================================================

    def upsert_bsg(self, bsg_id: str, behavior: str,
                   confidence: float = 0, member_count: int = 0,
                   session_ids: list = None,
                   metadata: dict = None) -> bool:
        """Insert or update a BSG group record."""
        meta_str = json.dumps(metadata or {})
        sids = session_ids or []
        with self._conn() as conn:
            cur = conn.cursor()
            if self._backend == 'postgres':
                cur.execute("""
                    INSERT INTO bsg_groups (bsg_id, behavior, confidence, member_count,
                        session_ids, instance_id, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (bsg_id) DO UPDATE SET
                        behavior = EXCLUDED.behavior,
                        confidence = EXCLUDED.confidence,
                        member_count = EXCLUDED.member_count,
                        session_ids = EXCLUDED.session_ids,
                        metadata = EXCLUDED.metadata
                """, (bsg_id, behavior, confidence, member_count, sids,
                      self.instance_id, meta_str))
            else:
                cur.execute("""
                    INSERT OR REPLACE INTO bsg_groups
                    (bsg_id, behavior, confidence, member_count, session_ids,
                     instance_id, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (bsg_id, behavior, confidence, member_count,
                      json.dumps(sids), self.instance_id, meta_str))
            return True

    def list_bsg_groups(self) -> List[Dict[str, Any]]:
        """List all BSG groups."""
        with self._conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM bsg_groups ORDER BY detected_at DESC")
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def list_bsg_projection(self) -> Dict[str, Any]:
        """Return the canonical BSG projection envelope for this instance.

        Reads `bsg_groups` and builds the projection via `bsg_projection.generate_bsg_projection`.
        Falls back to a minimal envelope if `bsg_projection` is unavailable.
        """
        groups = self.list_bsg_groups()
        # derive basic session stats
        try:
            sessions_total = self.session_count()
        except Exception:
            sessions_total = 0
        try:
            sessions_grouped = len(self.sessions_grouped_by_artifact())
        except Exception:
            sessions_grouped = 0

        # translate DB rows to projection-friendly dicts
        projected_groups = []
        for g in groups:
            # DB row may contain metadata JSON/text
            meta = g.get('metadata') or {}
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}

            grp = {
                'group_id': g.get('bsg_id') or g.get('bsg_id'),
                'group_type': g.get('behavior'),
                'confidence': g.get('confidence', 0.0),
                'evidence_level': meta.get('evidence_level', 'STRUCTURAL'),
                'rationale': meta.get('rationale') or {},
                'session_stats': {
                    'session_count': g.get('member_count', 0),
                },
                'network_characteristics': meta.get('network_characteristics') or {},
                'temporal_bounds': {
                    'first_seen': meta.get('first_seen'),
                    'last_seen': meta.get('last_seen') or g.get('detected_at'),
                },
                'negative_assertions': meta.get('negative_assertions') or [],
            }
            projected_groups.append(grp)

        if bp:
            envelope = bp.generate_bsg_projection(
                instance_id=self.instance_id,
                groups=projected_groups,
                sessions_total=sessions_total,
                sessions_grouped=sessions_grouped,
                groups_total=len(projected_groups),
            )
        else:
            envelope = {
                'bsg_projection_version': '1.0',
                'instance_id': self.instance_id,
                'generated_at': datetime.now(timezone.utc).isoformat(),
                'evidence_summary': {
                    'sessions_total': sessions_total,
                    'sessions_grouped': sessions_grouped,
                    'groups_total': len(projected_groups),
                    'coverage_pct': 0.0,
                },
                'groups': projected_groups,
                'constraints': {
                    'no_geo_inference': True,
                    'no_actor_attribution': True,
                    'no_intent_certainty': True,
                },
            }

        return envelope

    def bsg_count(self) -> int:
        """Return total BSG group count."""
        with self._conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM bsg_groups")
            return cur.fetchone()[0]

    # ===================================================================
    # INSTANCE STATE
    # ===================================================================

    def update_instance_state(self, **kwargs) -> bool:
        """Update instance state record."""
        with self._conn() as conn:
            cur = conn.cursor()
            ph = '%s' if self._backend == 'postgres' else '?'

            # Ensure row exists
            cur.execute(f"SELECT 1 FROM instance_state WHERE instance_id = {ph}",
                        (self.instance_id,))
            if not cur.fetchone():
                if self._backend == 'postgres':
                    cur.execute(
                        "INSERT INTO instance_state (instance_id) VALUES (%s)",
                        (self.instance_id,))
                else:
                    cur.execute(
                        "INSERT INTO instance_state (instance_id) VALUES (?)",
                        (self.instance_id,))

            # Build update
            allowed = {'has_evidence', 'session_count', 'artifact_count',
                       'bsg_count', 't_state', 't_state_id',
                       'last_ingest_at', 'last_bsg_at'}
            sets = []
            vals = []
            for k, v in kwargs.items():
                if k in allowed:
                    sets.append(f"{k} = {ph}")
                    vals.append(v)
            if not sets:
                return False
            if self._backend == 'postgres':
                sets.append("updated_at = NOW()")
            else:
                sets.append("updated_at = datetime('now')")

            sql = f"UPDATE instance_state SET {', '.join(sets)} WHERE instance_id = {ph}"
            vals.append(self.instance_id)
            cur.execute(sql, vals)
            return True

    def get_instance_state(self) -> Optional[Dict[str, Any]]:
        """Get current instance state."""
        with self._conn() as conn:
            cur = conn.cursor()
            ph = '%s' if self._backend == 'postgres' else '?'
            cur.execute(f"SELECT * FROM instance_state WHERE instance_id = {ph}",
                        (self.instance_id,))
            row = cur.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))

    # ===================================================================
    # MacCluster OPERATIONS (Persistent Actor Memory)
    # ===================================================================

    def upsert_mac_cluster(self, cluster_id: str, behavior: str = None,
                           confidence: float = 0, motion_basis: str = None,
                           centroid: Tuple[float, float] = (0, 0),
                           drift_tensor: list = None,
                           embedding: List[float] = None,
                           metadata: dict = None) -> bool:
        """Insert or update a MacCluster record with optional vector embedding."""
        meta_str = json.dumps(metadata or {})
        drift_str = json.dumps(drift_tensor or [])
        lat, lon = centroid

        with self._conn() as conn:
            cur = conn.cursor()
            if self._backend == 'postgres':
                # vector string format: '[1,2,3]'
                emb_str = f"[{','.join(map(str, embedding))}]" if embedding else None
                cur.execute("""
                    INSERT INTO mac_clusters (cluster_id, behavior, confidence,
                        motion_basis, centroid_lat, centroid_lon, drift_tensor,
                        embedding, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s::jsonb)
                    ON CONFLICT (cluster_id) DO UPDATE SET
                        behavior = EXCLUDED.behavior,
                        confidence = EXCLUDED.confidence,
                        motion_basis = EXCLUDED.motion_basis,
                        centroid_lat = EXCLUDED.centroid_lat,
                        centroid_lon = EXCLUDED.centroid_lon,
                        drift_tensor = EXCLUDED.drift_tensor,
                        embedding = COALESCE(EXCLUDED.embedding, mac_clusters.embedding),
                        metadata = EXCLUDED.metadata,
                        updated_at = NOW()
                """, (cluster_id, behavior, confidence, motion_basis, lat, lon,
                      drift_str, emb_str, meta_str))
            else:
                # SQLite fallback: embedding stored as JSON text (no vector similarity search)
                emb_str = json.dumps(embedding) if embedding else None
                cur.execute("""
                    INSERT OR REPLACE INTO mac_clusters
                    (cluster_id, behavior, confidence, motion_basis, centroid_lat,
                     centroid_lon, drift_tensor, embedding, metadata, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """, (cluster_id, behavior, confidence, motion_basis, lat, lon,
                      drift_str, emb_str, meta_str))
            return True

    def search_similar_clusters(self, query_embedding: List[float],
                                threshold: float = 0.8, limit: int = 5) -> List[Dict[str, Any]]:
        """Search for clusters with similar embeddings using pgvector cosine similarity."""
        if self._backend != 'postgres':
            logger.warning("[InstanceDB] Vector search requested on non-postgres backend")
            return []

        emb_str = f"[{','.join(map(str, query_embedding))}]"
        with self._conn() as conn:
            cur = conn.cursor()
            # cosine similarity = 1 - cosine distance
            cur.execute("""
                SELECT *, (1 - (embedding <=> %s::vector)) as similarity
                FROM mac_clusters
                WHERE embedding IS NOT NULL
                  AND (1 - (embedding <=> %s::vector)) >= %s
                ORDER BY similarity DESC
                LIMIT %s
            """, (emb_str, emb_str, threshold, limit))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    # ===================================================================
    # BULK OPERATIONS (for ingest mirroring)
    # ===================================================================

    def mirror_ingest_result(self, ingest_data: Dict[str, Any],
                             engine=None) -> Dict[str, Any]:
        """Mirror a complete ingest result to the authority DB.

        Called post-ingest to synchronize the database with
        what the hypergraph engine received.

        Args:
            ingest_data: Response dict from handle_mcp_pcap_ingest()
            engine: Optional HypergraphEngine to read session details from

        Returns:
            Summary dict with counts of what was mirrored.
        """
        mirrored = {'artifacts': 0, 'sessions': 0, 'bsg_groups': 0}

        # Extract artifacts from ingest result
        pcaps_processed = ingest_data.get('pcaps_processed', 0)

        # If engine is available, mirror from the authoritative graph
        if engine and hasattr(engine, 'nodes'):
            nodes = engine.nodes or {}

            # Mirror artifacts
            for nid, node in nodes.items():
                try:
                    nd = node.to_dict() if hasattr(node, 'to_dict') else (
                        node if isinstance(node, dict) else {})
                    kind = nd.get('kind', '')

                    if kind == 'pcap_artifact':
                        labels = nd.get('labels', {}) or {}
                        meta = nd.get('metadata', {}) or {}
                        self.upsert_artifact(
                            artifact_id=nid,
                            filename=labels.get('filename', nid),
                            sha256=labels.get('sha256', ''),
                            file_size=labels.get('file_size', 0),
                            metadata=meta,
                        )
                        mirrored['artifacts'] += 1

                    elif kind in ('session', 'pcap_session'):
                        labels = nd.get('labels', {}) or {}
                        meta = nd.get('metadata', {}) or {}
                        prov = meta.get('provenance', {}) or {}

                        # Derive artifact_id from provenance or edges
                        art_id = prov.get('pcap_file') or labels.get('pcap_file', '')
                        if art_id:
                            # Normalize to artifact node ID format
                            art_node_id = f"artifact:{art_id}" if not art_id.startswith('artifact:') else art_id
                            if art_node_id not in nodes:
                                art_node_id = art_id  # try raw

                        self.upsert_session(
                            session_id=nid,
                            artifact_id=art_id or None,
                            src_ip=labels.get('src_ip', ''),
                            dst_ip=labels.get('dst_ip', ''),
                            src_port=labels.get('src_port'),
                            dst_port=labels.get('dst_port'),
                            protocol=labels.get('proto', 'unknown'),
                            packet_count=labels.get('packet_count', 0),
                            total_bytes=labels.get('total_bytes', 0),
                            duration_sec=labels.get('duration_sec', 0),
                            time_bucket=labels.get('time_bucket', 0),
                            metadata=meta,
                        )
                        mirrored['sessions'] += 1

                    elif kind == 'behavior_group':
                        labels = nd.get('labels', {}) or {}
                        meta = nd.get('metadata', {}) or {}
                        member_ids = meta.get('member_session_ids', [])
                        self.upsert_bsg(
                            bsg_id=nid,
                            behavior=labels.get('behavior', 'UNKNOWN'),
                            confidence=labels.get('confidence', 0),
                            member_count=labels.get('member_count', 0),
                            session_ids=member_ids,
                            metadata=meta,
                        )
                        mirrored['bsg_groups'] += 1

                except Exception as e:
                    logger.warning(f"[InstanceDB] Mirror node {nid} failed: {e}")
                    continue

        # Update instance state
        try:
            now = datetime.now(timezone.utc).isoformat()
            self.update_instance_state(
                has_evidence=mirrored['sessions'] > 0 or mirrored['artifacts'] > 0,
                session_count=self.session_count(),
                artifact_count=len(self.list_artifacts()),
                bsg_count=self.bsg_count(),
                last_ingest_at=now,
            )
        except Exception as e:
            logger.warning(f"[InstanceDB] Instance state update failed: {e}")

        logger.info(f"[InstanceDB] Mirrored: {mirrored['artifacts']} artifacts, "
                     f"{mirrored['sessions']} sessions, {mirrored['bsg_groups']} BSGs")
        return mirrored

    # ===================================================================
    # DIAGNOSTIC QUERIES (for GraphOps / MCP tools)
    # ===================================================================

    def why_no_sessions(self) -> Dict[str, Any]:
        """Diagnostic: explain why sessions may not be visible."""
        result = {
            'backend': self._backend,
            'instance_id': self.instance_id,
            'checks': [],
        }
        with self._conn() as conn:
            cur = conn.cursor()

            # Check artifacts
            cur.execute("SELECT COUNT(*) FROM pcap_artifacts")
            art_count = cur.fetchone()[0]
            result['artifact_count'] = art_count
            if art_count == 0:
                result['checks'].append({
                    'check': 'artifacts',
                    'status': 'EMPTY',
                    'reason': 'No PCAP artifacts have been ingested',
                    'fix': 'Use "Ingest FTP" to fetch PCAPs'
                })
            else:
                result['checks'].append({
                    'check': 'artifacts',
                    'status': 'OK',
                    'count': art_count
                })

            # Check sessions
            cur.execute("SELECT COUNT(*) FROM sessions")
            ses_count = cur.fetchone()[0]
            result['session_count'] = ses_count
            if ses_count == 0 and art_count > 0:
                result['checks'].append({
                    'check': 'sessions',
                    'status': 'MISSING',
                    'reason': f'{art_count} artifacts ingested but 0 sessions created',
                    'fix': 'Session parsing may have failed — check server logs'
                })
            elif ses_count == 0:
                result['checks'].append({
                    'check': 'sessions',
                    'status': 'EMPTY',
                    'reason': 'No sessions exist — ingest data first'
                })
            else:
                result['checks'].append({
                    'check': 'sessions',
                    'status': 'OK',
                    'count': ses_count
                })

            # Check BSGs
            cur.execute("SELECT COUNT(*) FROM bsg_groups")
            bsg_count = cur.fetchone()[0]
            result['bsg_count'] = bsg_count
            if bsg_count == 0 and ses_count > 0:
                result['checks'].append({
                    'check': 'bsg_groups',
                    'status': 'NOT_RUN',
                    'reason': f'{ses_count} sessions exist but BSG detection has not produced groups',
                    'fix': 'BSG detection may be pending or found no patterns'
                })

            # Check instance state
            ph = '%s' if self._backend == 'postgres' else '?'
            cur.execute(f"SELECT * FROM instance_state WHERE instance_id = {ph}",
                        (self.instance_id,))
            state_row = cur.fetchone()
            if state_row:
                cols = [d[0] for d in cur.description]
                state = dict(zip(cols, state_row))
                result['instance_state'] = state
            else:
                result['checks'].append({
                    'check': 'instance_state',
                    'status': 'MISSING',
                    'reason': 'No instance state record — DB may not have been initialized'
                })

        return result

    def health(self) -> Dict[str, Any]:
        """Return database health summary."""
        try:
            with self._conn() as conn:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM pcap_artifacts")
                art_count = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM sessions")
                ses_count = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM bsg_groups")
                bsg_count = cur.fetchone()[0]
                return {
                    'ok': True,
                    'backend': self._backend,
                    'instance_id': self.instance_id,
                    'artifact_count': art_count,
                    'session_count': ses_count,
                    'bsg_count': bsg_count,
                }
        except Exception as e:
            return {
                'ok': False,
                'backend': self._backend,
                'error': str(e),
            }

    # ===================================================================
    # LIFECYCLE
    # ===================================================================

    def close(self):
        """Shut down the database connection / server."""
        if self._pg_server:
            try:
                self._pg_server.cleanup()
            except Exception:
                pass
            self._pg_server = None

    def __del__(self):
        self.close()

    def __repr__(self):
        return f"<InstanceDB backend={self._backend} instance={self.instance_id}>"
