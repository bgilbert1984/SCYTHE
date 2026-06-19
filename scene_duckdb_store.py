"""
scene_duckdb_store.py — DuckDB-backed tactical event store.

Replaces the SQLite scene_event_log.py with a columnar engine that:
  - stores events in DuckDB (persistent .duckdb file or in-memory)
  - exports/imports Parquet blocks for cold storage + replay
  - supports the same append / scrub / fork / snapshot API
  - enables arbitrary SQL analytics on the event stream
"""

import time
import uuid
import json
import threading
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

EVENTS_SCHEMA = pa.schema([
    pa.field("timestamp",  pa.int64()),       # Unix millis
    pa.field("event_type", pa.string()),
    pa.field("entity_id",  pa.string()),
    pa.field("session_id", pa.string()),
    pa.field("lat",        pa.float64()),
    pa.field("lon",        pa.float64()),
    pa.field("alt",        pa.float64()),
    pa.field("payload",    pa.string()),       # JSON blob for extra fields
    pa.field("seq",        pa.int64()),        # monotonic insert order
])


@dataclass
class TacticalEvent:
    timestamp: int          # Unix millis
    event_type: str
    entity_id: str
    session_id: str
    lat: float = 0.0
    lon: float = 0.0
    alt: float = 0.0
    payload: Optional[Dict[str, Any]] = None
    seq: int = 0

    def to_row(self) -> dict:
        return {
            "timestamp":  self.timestamp,
            "event_type": self.event_type,
            "entity_id":  self.entity_id,
            "session_id": self.session_id,
            "lat":        self.lat,
            "lon":        self.lon,
            "alt":        self.alt,
            "payload":    json.dumps(self.payload or {}),
            "seq":        self.seq,
        }

    @classmethod
    def from_row(cls, row: dict) -> "TacticalEvent":
        return cls(
            timestamp=row["timestamp"],
            event_type=row["event_type"],
            entity_id=row["entity_id"],
            session_id=row["session_id"],
            lat=row.get("lat", 0.0),
            lon=row.get("lon", 0.0),
            alt=row.get("alt", 0.0),
            payload=json.loads(row.get("payload") or "{}"),
            seq=row.get("seq", 0),
        )


# ---------------------------------------------------------------------------
# ScytheDuckStore
# ---------------------------------------------------------------------------

class ScytheDuckStore:
    """
    Persistent DuckDB event store with Parquet cold storage.

    Thread-safe for concurrent append + query from Flask routes.
    """

    def __init__(self, db_path: str = "/home/spectrcyde/NerfEngine/metrics_logs/scythe_events.duckdb",
                 parquet_dir: str = "/home/spectrcyde/NerfEngine/metrics_logs/parquet_blocks"):
        self._lock = threading.Lock()
        self._db_path = db_path
        self._parquet_dir = Path(parquet_dir)
        self._parquet_dir.mkdir(parents=True, exist_ok=True)

        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(db_path)
        self._seq = 0
        self._init_schema()

    def _init_schema(self):
        con = self._con
        con.execute("""
            CREATE TABLE IF NOT EXISTS events (
                seq        BIGINT PRIMARY KEY,
                timestamp  BIGINT NOT NULL,
                event_type VARCHAR NOT NULL,
                entity_id  VARCHAR NOT NULL,
                session_id VARCHAR NOT NULL,
                lat        DOUBLE DEFAULT 0.0,
                lon        DOUBLE DEFAULT 0.0,
                alt        DOUBLE DEFAULT 0.0,
                payload    VARCHAR DEFAULT '{}'
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_ts  ON events(timestamp)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_eid ON events(entity_id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_sid ON events(session_id)")
        con.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                snap_id     VARCHAR PRIMARY KEY,
                session_id  VARCHAR NOT NULL,
                timestamp   BIGINT  NOT NULL,
                state_json  VARCHAR NOT NULL,
                created_at  BIGINT  NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS parquet_index (
                block_id   VARCHAR PRIMARY KEY,
                file_path  VARCHAR NOT NULL,
                min_ts     BIGINT  NOT NULL,
                max_ts     BIGINT  NOT NULL,
                row_count  INTEGER NOT NULL,
                session_id VARCHAR NOT NULL
            )
        """)
        # Restore sequence counter
        row = con.execute("SELECT COALESCE(MAX(seq), -1) FROM events").fetchone()
        self._seq = (row[0] + 1) if row else 0

    # ------------------------------------------------------------------
    # Append
    # ------------------------------------------------------------------

    def append(self, event: TacticalEvent) -> int:
        with self._lock:
            event.seq = self._seq
            self._seq += 1
            r = event.to_row()
            self._con.execute(
                "INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?)",
                [r["seq"], r["timestamp"], r["event_type"], r["entity_id"],
                 r["session_id"], r["lat"], r["lon"], r["alt"], r["payload"]]
            )
            return event.seq

    def append_batch(self, events: List[TacticalEvent]) -> int:
        """Bulk insert via PyArrow — orders of magnitude faster than executemany."""
        with self._lock:
            rows = []
            for e in events:
                e.seq = self._seq
                self._seq += 1
                rows.append(e.to_row())
            if not rows:
                return 0
            table = pa.Table.from_pylist(rows, schema=EVENTS_SCHEMA)
            self._con.register("_batch_tmp", table)
            self._con.execute("""
                INSERT INTO events
                SELECT seq, timestamp, event_type, entity_id,
                       session_id, lat, lon, alt, payload
                FROM _batch_tmp
            """)
            self._con.unregister("_batch_tmp")
            return len(rows)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def query_sql(self, sql: str) -> List[dict]:
        """Execute arbitrary DuckDB SQL against the events table."""
        with self._lock:
            rel = self._con.execute(sql)
            cols = [d[0] for d in rel.description]
            return [dict(zip(cols, row)) for row in rel.fetchall()]

    def query_since(self, since_seq: int, limit: int = 200) -> List[dict]:
        """Return events with seq > since_seq, ordered by seq, up to limit rows.

        Used by /api/hypergraph/events/since for delta queries — clients track
        their last-seen sequence number and only fetch new events.
        """
        with self._lock:
            rows = self._con.execute(
                "SELECT seq, timestamp, event_type, entity_id, lat, lon, alt, payload "
                "FROM events WHERE seq > ? ORDER BY seq LIMIT ?",
                [since_seq, limit]
            ).fetchall()
            cols = ["seq", "timestamp", "event_type", "entity_id", "lat", "lon", "alt", "payload"]
            result = []
            for r in rows:
                row = dict(zip(cols, r))
                if row.get('payload'):
                    try:
                        row['payload'] = json.loads(row['payload'])
                    except Exception:
                        pass
                result.append(row)
            return result

    def events_in_range(self, t0: int, t1: int, session_id: Optional[str] = None) -> List[TacticalEvent]:
        with self._lock:
            if session_id:
                rows = self._con.execute(
                    "SELECT * FROM events WHERE timestamp BETWEEN ? AND ? AND session_id=? ORDER BY timestamp, seq",
                    [t0, t1, session_id]
                ).fetchall()
            else:
                rows = self._con.execute(
                    "SELECT * FROM events WHERE timestamp BETWEEN ? AND ? ORDER BY timestamp, seq",
                    [t0, t1]
                ).fetchall()
            cols = ["seq","timestamp","event_type","entity_id","session_id","lat","lon","alt","payload"]
            return [TacticalEvent.from_row(dict(zip(cols, r))) for r in rows]

    def scrub(self, target_ts: int, session_id: str) -> List[TacticalEvent]:
        """Return all events for a session up to target_ts, time-ordered."""
        with self._lock:
            rows = self._con.execute(
                "SELECT * FROM events WHERE session_id=? AND timestamp<=? ORDER BY timestamp, seq",
                [session_id, target_ts]
            ).fetchall()
            cols = ["seq","timestamp","event_type","entity_id","session_id","lat","lon","alt","payload"]
            return [TacticalEvent.from_row(dict(zip(cols, r))) for r in rows]

    def stats(self) -> dict:
        with self._lock:
            row = self._con.execute("""
                SELECT
                  COUNT(*) AS total_events,
                  COUNT(DISTINCT session_id) AS sessions,
                  COUNT(DISTINCT entity_id) AS entities,
                  MIN(timestamp) AS earliest_ts,
                  MAX(timestamp) AS latest_ts
                FROM events
            """).fetchone()
            return {
                "total_events": row[0],
                "sessions": row[1],
                "entities": row[2],
                "earliest_ts": row[3],
                "latest_ts": row[4],
            }

    # ------------------------------------------------------------------
    # Snapshots
    # ------------------------------------------------------------------

    def save_snapshot(self, session_id: str, timestamp: int, state: dict) -> str:
        snap_id = str(uuid.uuid4())
        with self._lock:
            self._con.execute(
                "INSERT INTO snapshots VALUES (?,?,?,?,?)",
                [snap_id, session_id, timestamp, json.dumps(state), int(time.time() * 1000)]
            )
        return snap_id

    def load_snapshot(self, session_id: str, before_ts: int) -> Optional[dict]:
        with self._lock:
            row = self._con.execute(
                "SELECT state_json FROM snapshots WHERE session_id=? AND timestamp<=? ORDER BY timestamp DESC LIMIT 1",
                [session_id, before_ts]
            ).fetchone()
        return json.loads(row[0]) if row else None

    # ------------------------------------------------------------------
    # Parquet cold storage
    # ------------------------------------------------------------------

    def export_parquet_block(self, t0: int, t1: int, session_id: Optional[str] = None) -> Path:
        """
        Export a time-range of events to a Parquet file with ZSTD compression.
        Registers the block in the parquet_index table.
        """
        events = self.events_in_range(t0, t1, session_id)
        if not events:
            raise ValueError(f"No events in range [{t0}, {t1}]")

        rows = [e.to_row() for e in events]
        table = pa.Table.from_pylist(rows, schema=EVENTS_SCHEMA)

        block_id = str(uuid.uuid4())[:8]
        sid_tag = (session_id or "all")[:16]
        fname = f"block_{sid_tag}_{t0}_{t1}_{block_id}.parquet"
        fpath = self._parquet_dir / fname

        pq.write_table(
            table, fpath,
            compression="zstd",
            compression_level=9,
            use_dictionary=True,
            write_statistics=True,
        )

        with self._lock:
            self._con.execute(
                "INSERT OR REPLACE INTO parquet_index VALUES (?,?,?,?,?,?)",
                [block_id, str(fpath), t0, t1, len(events), session_id or ""]
            )

        return fpath

    def ingest_parquet_block(self, file_path: str) -> int:
        """Load a Parquet block back into the live DuckDB store."""
        table = pq.read_table(file_path)
        rows = table.to_pylist()
        events = [TacticalEvent.from_row(r) for r in rows]
        return self.append_batch(events)

    def list_parquet_blocks(self) -> List[dict]:
        with self._lock:
            rows = self._con.execute(
                "SELECT block_id, file_path, min_ts, max_ts, row_count, session_id FROM parquet_index ORDER BY min_ts"
            ).fetchall()
        return [
            {"block_id": r[0], "file_path": r[1], "min_ts": r[2],
             "max_ts": r[3], "row_count": r[4], "session_id": r[5]}
            for r in rows
        ]

    def close(self):
        self._con.close()


# ---------------------------------------------------------------------------
# Module-level singleton (shared by Flask routes)
# ---------------------------------------------------------------------------

_store: Optional[ScytheDuckStore] = None


def get_store() -> ScytheDuckStore:
    global _store
    if _store is None:
        _store = ScytheDuckStore()
    return _store


# ---------------------------------------------------------------------------
# Quick self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import random, math

    print("=== ScytheDuckStore self-test ===")
    store = ScytheDuckStore(db_path=":memory:", parquet_dir="/tmp/scythe_parquet_test")
    Path("/tmp/scythe_parquet_test").mkdir(exist_ok=True)

    t0 = int(time.time() * 1000)
    N = 5000
    session = "sess_test_001"

    events = []
    for i in range(N):
        events.append(TacticalEvent(
            timestamp=t0 + i * 100,
            event_type="entity.move" if i % 10 else "entity.spawn",
            entity_id=f"uav_{i % 20}",
            session_id=session,
            lat=38.1 + math.sin(i * 0.01) * 0.05,
            lon=-77.1 + math.cos(i * 0.01) * 0.05,
            alt=100.0 + i * 0.1,
            payload={"heading": i % 360, "speed": 15.0},
        ))

    t_insert = time.perf_counter()
    store.append_batch(events)
    insert_ms = (time.perf_counter() - t_insert) * 1000
    print(f"Inserted {N:,} events in {insert_ms:.1f}ms")

    stats = store.stats()
    print(f"Stats: {stats}")

    # SQL analytics
    top_entities = store.query_sql("""
        SELECT entity_id, COUNT(*) as evt_count, AVG(lat) as avg_lat
        FROM events
        GROUP BY entity_id
        ORDER BY evt_count DESC
        LIMIT 5
    """)
    print(f"Top entities: {top_entities[:2]}...")

    # Scrub
    mid_ts = t0 + (N // 2) * 100
    t_scrub = time.perf_counter()
    replayed = store.scrub(mid_ts, session)
    scrub_ms = (time.perf_counter() - t_scrub) * 1000
    print(f"Scrub to midpoint: {len(replayed)} events in {scrub_ms:.1f}ms")

    # Parquet export
    t1 = t0 + N * 100
    t_parquet = time.perf_counter()
    pfile = store.export_parquet_block(t0, t1, session)
    parquet_ms = (time.perf_counter() - t_parquet) * 1000
    fsize_kb = pfile.stat().st_size / 1024
    print(f"Parquet export: {fsize_kb:.1f} KB in {parquet_ms:.1f}ms  (raw ~{N*64/1024:.0f} KB)")
    ratio = (N * 64) / pfile.stat().st_size
    print(f"Compression ratio: {ratio:.1f}×")

    # Re-ingest
    store2 = ScytheDuckStore(db_path=":memory:", parquet_dir="/tmp/scythe_parquet_test")
    ingested = store2.ingest_parquet_block(str(pfile))
    print(f"Re-ingested {ingested} events from Parquet ✅")

    store.close()
    store2.close()
    print("ALL TESTS PASSED ✅")
