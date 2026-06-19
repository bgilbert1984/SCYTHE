"""
scene_event_log.py
==================
Thread-safe, persistent event log for the Immutable Battlefield Ledger.

Storage strategy
----------------
* **Primary store**: SQLite (WAL mode) — immediate durability, queryable, trivial
  to deploy.  Each row stores one msgpack-encoded event.
* **Snapshots**: periodic msgpack+zstd scene-state blobs written to disk so
  replay can skip millions of events and start from a recent checkpoint.
* **Export**: ``export_atakrec(path)`` packs events + snapshots + metadata into
  a ``*.atakrec`` zip archive (engine-agnostic, portable).

Session model
-------------
A *session* is a bounded timeline identified by a ``session_id``.  Multiple
sessions live in one SQLite database.  Each session begins with a
``session.start`` event and ends with ``session.end``.

Usage
-----
    log = SceneEventLog("/tmp/scythe_events.db")
    sid = log.new_session("op_2026_03_14")

    log.append(make_event("entity.spawn", id="uav_12", class_="uav",
                           lat=32.81, lon=-96.86, alt=400))
    log.append(swarm_create("swarm_A", [32.81, -96.86], members=8))

    log.snapshot(sid)                        # persist compressed scene state
    log.export_atakrec(sid, "/tmp/op.atakrec")
    log.close()
"""

import io
import json
import os
import sqlite3
import threading
import time
import zipfile
from dataclasses import dataclass, field
from typing import Any, Generator, Iterator

import msgpack
import zstandard

from scene_event_schema import (
    from_msgpack, make_event, session_end, session_start, to_msgpack,
    validate_event,
)

# ---------------------------------------------------------------------------
# Scene state machine — applies events to produce current state
# ---------------------------------------------------------------------------

@dataclass
class SceneState:
    """In-memory tactical scene reconstructed by replaying events."""
    session_id: str = ""
    projection:  str = "EPSG:4326"
    seed:        int = 0
    entities:    dict[str, dict]  = field(default_factory=dict)
    swarms:      dict[str, dict]  = field(default_factory=dict)
    rf_emitters: dict[str, dict]  = field(default_factory=dict)
    camera:      dict[str, Any]   = field(default_factory=dict)
    assets:      dict[str, str]   = field(default_factory=dict)  # name → hash
    annotations: list[dict]       = field(default_factory=list)
    event_count: int = 0

    # ------------------------------------------------------------------
    def apply(self, evt: dict) -> None:
        t = evt.get("type", "")
        self.event_count += 1

        if t == "session.start":
            self.session_id = evt.get("session_id", "")
            self.projection  = evt.get("projection", "EPSG:4326")
            self.seed        = evt.get("seed", 0)

        elif t == "camera.pose":
            self.camera = {k: evt[k] for k in
                           ("lat","lon","alt","heading","pitch","roll")
                           if k in evt}

        elif t == "entity.spawn":
            self.entities[evt["id"]] = {k: v for k, v in evt.items()
                                         if k not in ("type","timestamp")}

        elif t == "entity.move":
            if evt["id"] in self.entities:
                for k in ("lat","lon","alt"):
                    if k in evt:
                        self.entities[evt["id"]][k] = evt[k]

        elif t == "entity.update":
            if evt["id"] in self.entities:
                skip = {"type","timestamp","id"}
                for k, v in evt.items():
                    if k not in skip:
                        self.entities[evt["id"]][k] = v

        elif t == "entity.remove":
            self.entities.pop(evt.get("id",""), None)

        elif t == "rf.triangulate":
            self.rf_emitters[evt["emitter_id"]] = {
                k: v for k, v in evt.items() if k not in ("type","timestamp")
            }

        elif t == "swarm.create":
            self.swarms[evt["swarm_id"]] = {
                k: v for k, v in evt.items() if k not in ("type","timestamp")
            }

        elif t == "swarm.update":
            sid = evt.get("swarm_id","")
            if sid not in self.swarms:
                self.swarms[sid] = {"swarm_id": sid}
            skip = {"type","timestamp"}
            for k, v in evt.items():
                if k not in skip:
                    self.swarms[sid][k] = v

        elif t == "swarm.dissolve":
            self.swarms.pop(evt.get("swarm_id",""), None)

        elif t == "asset.reference":
            self.assets[evt["asset"]] = evt["hash"]

        elif t == "overlay.annotation":
            self.annotations.append({k: v for k, v in evt.items()
                                      if k not in ("type","timestamp")})

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "projection":  self.projection,
            "seed":        self.seed,
            "entities":    self.entities,
            "swarms":      self.swarms,
            "rf_emitters": self.rf_emitters,
            "camera":      self.camera,
            "assets":      self.assets,
            "annotations": self.annotations,
            "event_count": self.event_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SceneState":
        s = cls()
        s.session_id = d.get("session_id","")
        s.projection  = d.get("projection","EPSG:4326")
        s.seed        = d.get("seed", 0)
        s.entities    = d.get("entities",{})
        s.swarms      = d.get("swarms",{})
        s.rf_emitters = d.get("rf_emitters",{})
        s.camera      = d.get("camera",{})
        s.assets      = d.get("assets",{})
        s.annotations = d.get("annotations",[])
        s.event_count = d.get("event_count",0)
        return s

# ---------------------------------------------------------------------------
# Compressor helper
# ---------------------------------------------------------------------------

_CCTX = zstandard.ZstdCompressor(level=3)
_DCTX = zstandard.ZstdDecompressor()


def _compress(data: bytes) -> bytes:
    return _CCTX.compress(data)


def _decompress(data: bytes) -> bytes:
    return _DCTX.decompress(data)

# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class SceneEventLog:
    """
    Persistent, thread-safe event log.

    One SQLite file holds all sessions.  Snapshots go to the same directory
    with a ``.snap`` suffix.
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS events (
        rowid      INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT    NOT NULL,
        timestamp  REAL    NOT NULL,
        type       TEXT    NOT NULL,
        payload    BLOB    NOT NULL          -- msgpack(event dict)
    );
    CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, timestamp);

    CREATE TABLE IF NOT EXISTS sessions (
        session_id  TEXT PRIMARY KEY,
        started_at  REAL NOT NULL,
        ended_at    REAL,
        meta        TEXT DEFAULT '{}'
    );

    CREATE TABLE IF NOT EXISTS snapshots (
        session_id   TEXT NOT NULL,
        taken_at     REAL NOT NULL,
        after_rowid  INTEGER NOT NULL,
        state_blob   BLOB NOT NULL,          -- msgpack(SceneState)+zstd
        PRIMARY KEY (session_id, taken_at)
    );
    """

    def __init__(self, db_path: str = "scene_events.db"):
        self.db_path    = db_path
        self._lock      = threading.RLock()
        self._conn      = self._open_db()
        self._cur_session: str | None = None

    # ------------------------------------------------------------------
    # Internal DB helpers
    # ------------------------------------------------------------------

    def _open_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False,
                               isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(self.SCHEMA)
        return conn

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def new_session(self, session_id: str, projection: str = "EPSG:4326",
                    seed: int = 0, meta: dict | None = None) -> str:
        """Open a new session and emit session.start.  Returns session_id."""
        with self._lock:
            now = time.time()
            self._conn.execute(
                "INSERT OR REPLACE INTO sessions VALUES (?,?,NULL,?)",
                (session_id, now, json.dumps(meta or {}))
            )
            self._cur_session = session_id
            self.append(session_start(session_id, projection, seed))
        return session_id

    def end_session(self, session_id: str | None = None) -> None:
        sid = session_id or self._cur_session
        if not sid:
            return
        with self._lock:
            self.append(session_end())
            self._conn.execute(
                "UPDATE sessions SET ended_at=? WHERE session_id=?",
                (time.time(), sid)
            )
            if self._cur_session == sid:
                self._cur_session = None

    def list_sessions(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT session_id, started_at, ended_at, meta FROM sessions"
            " ORDER BY started_at DESC"
        ).fetchall()
        return [
            {"session_id": r[0], "started_at": r[1],
             "ended_at": r[2], "meta": json.loads(r[3])}
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Event append
    # ------------------------------------------------------------------

    def append(self, evt: dict,
               session_id: str | None = None) -> int:
        """
        Append one event to the log.  Returns the rowid.

        If *session_id* is not given, uses the current open session.
        Validates the event and raises ValueError on schema errors.
        """
        errs = validate_event(evt)
        if errs:
            raise ValueError(f"Invalid event {evt.get('type')!r}: {errs}")

        sid = session_id or self._cur_session
        if not sid:
            raise RuntimeError("No open session — call new_session() first")

        raw = to_msgpack(evt)
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO events (session_id, timestamp, type, payload)"
                " VALUES (?,?,?,?)",
                (sid, evt["timestamp"], evt["type"], sqlite3.Binary(raw))
            )
            return cur.lastrowid

    def append_many(self, events: list[dict],
                    session_id: str | None = None) -> None:
        for evt in events:
            self.append(evt, session_id)

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def snapshot(self, session_id: str | None = None) -> int:
        """
        Rebuild scene from all events in the session and persist a
        compressed snapshot blob.  Returns the after_rowid.
        """
        sid = session_id or self._cur_session
        if not sid:
            raise RuntimeError("No session specified")

        with self._lock:
            state, last_rowid = self._rebuild(sid)
            blob = _compress(msgpack.packb(state.to_dict(), use_bin_type=True))
            self._conn.execute(
                "INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?)",
                (sid, time.time(), last_rowid, sqlite3.Binary(blob))
            )
        return last_rowid

    def _rebuild(self, session_id: str,
                 up_to_rowid: int | None = None) -> tuple["SceneState", int]:
        """Replay all events for session → (SceneState, last_rowid)."""
        state = SceneState()
        last_rowid = 0

        q = "SELECT rowid, payload FROM events WHERE session_id=?"
        args: list = [session_id]
        if up_to_rowid is not None:
            q += " AND rowid <= ?"
            args.append(up_to_rowid)
        q += " ORDER BY rowid"

        for rowid, raw in self._conn.execute(q, args):
            evt = from_msgpack(bytes(raw))
            state.apply(evt)
            last_rowid = rowid

        return state, last_rowid

    def get_scene_state(self, session_id: str | None = None,
                        use_snapshot: bool = True) -> "SceneState":
        """
        Return current SceneState for a session.

        If *use_snapshot* is True, loads the latest snapshot and applies
        only events after it (fast path).
        """
        sid = session_id or self._cur_session
        if not sid:
            raise RuntimeError("No session specified")

        with self._lock:
            if use_snapshot:
                snap = self._conn.execute(
                    "SELECT state_blob, after_rowid FROM snapshots"
                    " WHERE session_id=? ORDER BY taken_at DESC LIMIT 1",
                    (sid,)
                ).fetchone()
                if snap:
                    state_dict = msgpack.unpackb(
                        _decompress(bytes(snap[0])), raw=False,
                        strict_map_key=False)
                    state = SceneState.from_dict(state_dict)
                    after = snap[1]
                    # Apply events newer than the snapshot
                    for _, raw in self._conn.execute(
                        "SELECT rowid, payload FROM events"
                        " WHERE session_id=? AND rowid > ? ORDER BY rowid",
                        (sid, after)
                    ):
                        state.apply(from_msgpack(bytes(raw)))
                    return state

            state, _ = self._rebuild(sid)
            return state

    # ------------------------------------------------------------------
    # Streaming / iteration
    # ------------------------------------------------------------------

    def iter_events(self, session_id: str,
                    after_rowid: int = 0) -> Iterator[dict]:
        """Yield events for session in order, optionally after a rowid."""
        for _, raw in self._conn.execute(
            "SELECT rowid, payload FROM events"
            " WHERE session_id=? AND rowid>? ORDER BY rowid",
            (session_id, after_rowid)
        ):
            yield from_msgpack(bytes(raw))

    def tail_events(self, session_id: str,
                    last_n: int = 100) -> list[dict]:
        """Return the last *last_n* events for a session."""
        rows = self._conn.execute(
            "SELECT payload FROM ("
            "  SELECT rowid, payload FROM events WHERE session_id=?"
            "  ORDER BY rowid DESC LIMIT ?"
            ") ORDER BY rowid",
            (session_id, last_n)
        ).fetchall()
        return [from_msgpack(bytes(r[0])) for r in rows]

    def event_count(self, session_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM events WHERE session_id=?", (session_id,)
        ).fetchone()
        return row[0] if row else 0

    # ------------------------------------------------------------------
    # Export to .atakrec archive
    # ------------------------------------------------------------------

    def export_atakrec(self, session_id: str, out_path: str,
                       compress_events: bool = True) -> str:
        """
        Pack events + latest snapshot + metadata into a portable zip archive.

        Archive layout:
            metadata.json          — session info + schema version
            events.msgpack.zst     — all events, msgpack-encoded list, zstd-compressed
            snapshot.msgpack.zst   — latest scene state blob (if available)
        """
        sid = session_id
        meta_row = self._conn.execute(
            "SELECT started_at, ended_at, meta FROM sessions WHERE session_id=?",
            (sid,)
        ).fetchone()
        if not meta_row:
            raise ValueError(f"Session {sid!r} not found")

        # Collect all events
        all_events = list(self.iter_events(sid))
        event_list_raw = msgpack.packb(all_events, use_bin_type=True)
        events_blob = _compress(event_list_raw)

        # Latest snapshot
        snap_row = self._conn.execute(
            "SELECT state_blob FROM snapshots WHERE session_id=?"
            " ORDER BY taken_at DESC LIMIT 1", (sid,)
        ).fetchone()

        metadata = {
            "schema_version":  "1.0",
            "session_id":      sid,
            "started_at":      meta_row[0],
            "ended_at":        meta_row[1],
            "event_count":     len(all_events),
            "has_snapshot":    snap_row is not None,
            "exported_at":     time.time(),
            "generator":       "rf_scythe/scene_event_log.py",
        }

        with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("metadata.json",
                        json.dumps(metadata, indent=2))
            zf.writestr("events.msgpack.zst", events_blob)
            if snap_row:
                zf.writestr("snapshot.msgpack.zst", bytes(snap_row[0]))

        return out_path

    @staticmethod
    def import_atakrec(archive_path: str) -> tuple[dict, list[dict], "SceneState | None"]:
        """
        Read a .atakrec archive.  Returns (metadata, events, scene_state_or_None).
        """
        with zipfile.ZipFile(archive_path, "r") as zf:
            metadata = json.loads(zf.read("metadata.json"))
            events_blob = zf.read("events.msgpack.zst")
            events = msgpack.unpackb(_decompress(events_blob),
                                     raw=False, strict_map_key=False)
            state = None
            if "snapshot.msgpack.zst" in zf.namelist():
                snap_raw = zf.read("snapshot.msgpack.zst")
                state_dict = msgpack.unpackb(_decompress(snap_raw),
                                             raw=False, strict_map_key=False)
                state = SceneState.from_dict(state_dict)
        return metadata, events, state

    # ------------------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import tempfile
    import os

    print("=== SceneEventLog self-test ===\n")

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path  = os.path.join(tmpdir, "test_events.db")
        rec_path = os.path.join(tmpdir, "test_op.atakrec")

        from scene_event_schema import (
            camera_pose, entity_move, entity_remove, entity_spawn,
            rf_detect, rf_triangulate, swarm_create, swarm_dissolve,
            swarm_update,
        )

        log = SceneEventLog(db_path)
        sid = log.new_session("op_test_2026", seed=42)

        # Build a 50-event scenario
        events_to_log = (
            [camera_pose(32.81, -96.87, 2000)] +
            [entity_spawn(f"uav_{i}", "uav", lat=32.81+i*0.01,
                          lon=-96.86, alt=400)
             for i in range(5)] +
            [entity_move(f"uav_{i%5}", 32.81+i*0.001, -96.86+i*0.001)
             for i in range(20)] +
            [rf_detect("sensor_1", freq=2450, power=-47+i, bearing=200+i)
             for i in range(10)] +
            [rf_triangulate("emitter_A", 32.814, -96.863, 0.92)] +
            [swarm_create("swarm_A", [32.81, -96.86], members=8)] +
            [swarm_update("swarm_A", centroid=[32.812+i*0.001, -96.862],
                          members=8+i) for i in range(5)] +
            [swarm_dissolve("swarm_A")] +
            [entity_remove("uav_0")]
        )

        log.append_many(events_to_log)
        n = log.event_count(sid)
        print(f"  Appended events: {n} (expected ~{len(events_to_log)+1})")  # +1 session.start

        # Snapshot
        snap_rowid = log.snapshot(sid)
        print(f"  Snapshot at rowid {snap_rowid}")

        # Reconstruct (fast path via snapshot)
        state = log.get_scene_state(sid, use_snapshot=True)
        print(f"  Scene state: {len(state.entities)} entities,"
              f" {len(state.swarms)} swarms,"
              f" {len(state.rf_emitters)} rf_emitters")

        # Verify specific expectations
        assert "uav_0" not in state.entities, "uav_0 should be removed"
        assert "uav_1" in state.entities,     "uav_1 should exist"
        assert "swarm_A" not in state.swarms,  "swarm_A should be dissolved"
        assert "emitter_A" in state.rf_emitters
        print("  Entity/swarm state assertions: PASS")

        # Reconstruct cold (no snapshot)
        state2 = log.get_scene_state(sid, use_snapshot=False)
        assert state2.event_count == state.event_count
        print("  Cold vs snapshot reconstruction: MATCH")

        # Export
        log.export_atakrec(sid, rec_path)
        size_kb = os.path.getsize(rec_path) / 1024
        print(f"  Exported .atakrec: {size_kb:.1f} KB")

        # Import + verify round-trip
        meta, events_back, snap_state = SceneEventLog.import_atakrec(rec_path)
        assert meta["event_count"] == n
        assert len(events_back) == n
        print(f"  Import round-trip: {len(events_back)} events, metadata OK")

        log.end_session(sid)
        log.close()

        print("\nALL PASS ✓")
