"""Bounded RIS Live control-plane observations kept parallel to data-plane evidence."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import sqlite3
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


SCHEMA_VERSION = "graphops.ris-live.v1"
RIS_WS_URL = "wss://ris-live.ripe.net/v1/ws/?client=scythe-graphops-infraflow-v1"
MAX_PREFIXES = 32
MAX_ASNS = 32
MAX_OBSERVATIONS = 512
MAX_PERSISTED_OBSERVATIONS = 50_000
RETENTION_SECONDS = 7 * 24 * 60 * 60
DEFAULT_STORE_PATH = Path(__file__).resolve().parent / "runtime" / "graphops_ris_live.sqlite"


class RisObservationStore:
    """SQLite WAL store with time/count retention and immutable message IDs."""
    def __init__(self, path: Path = DEFAULT_STORE_PATH, *, clock=time.time):
        self.path = Path(path); self.clock = clock; self.lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True); self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA journal_mode=WAL"); connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS ris_observations (
                    id TEXT PRIMARY KEY, collector_received_at REAL NOT NULL,
                    collector_id TEXT NOT NULL, message_type TEXT NOT NULL,
                    prefix TEXT NOT NULL, origin_json TEXT NOT NULL, row_json TEXT NOT NULL,
                    persisted_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ris_time ON ris_observations(collector_received_at);
                CREATE INDEX IF NOT EXISTS idx_ris_prefix_time ON ris_observations(prefix, collector_received_at);
            """)

    def insert_many(self, rows: Iterable[Dict[str, Any]]) -> int:
        values = [(str(row["id"]), float(row["collectorReceivedAt"]), str(row.get("collectorId") or ""),
                   str(row.get("messageType") or ""), str(row.get("prefix") or ""),
                   json.dumps(row.get("originAsn"), separators=(",", ":")),
                   json.dumps(row, sort_keys=True, separators=(",", ":")), self.clock()) for row in rows]
        if not values: return 0
        with self.lock, self._connect() as connection:
            before = connection.total_changes
            connection.executemany("INSERT OR IGNORE INTO ris_observations VALUES (?,?,?,?,?,?,?,?)", values)
            inserted = connection.total_changes - before
            self._prune(connection)
            return inserted

    def _prune(self, connection) -> None:
        connection.execute("DELETE FROM ris_observations WHERE collector_received_at < ?",
                           (self.clock() - RETENTION_SECONDS,))
        connection.execute("""DELETE FROM ris_observations WHERE id IN (
            SELECT id FROM ris_observations ORDER BY collector_received_at DESC
            LIMIT -1 OFFSET ?)""", (MAX_PERSISTED_OBSERVATIONS,))

    def query(self, *, since: Optional[float] = None, until: Optional[float] = None,
              limit: int = 128) -> list[Dict[str, Any]]:
        clauses, parameters = [], []
        if since is not None: clauses.append("collector_received_at >= ?"); parameters.append(float(since))
        if until is not None: clauses.append("collector_received_at <= ?"); parameters.append(float(until))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        parameters.append(min(max(int(limit), 1), MAX_PERSISTED_OBSERVATIONS))
        with self.lock, self._connect() as connection:
            rows = connection.execute(
                f"SELECT row_json FROM ris_observations{where} ORDER BY collector_received_at DESC LIMIT ?",
                parameters).fetchall()
        return [json.loads(row[0]) for row in reversed(rows)]

    def stats(self) -> Dict[str, Any]:
        with self.lock, self._connect() as connection:
            count, earliest, latest = connection.execute(
                "SELECT COUNT(*), MIN(collector_received_at), MAX(collector_received_at) FROM ris_observations").fetchone()
        return {"persistedObservations": int(count), "earliest": earliest, "latest": latest,
                "retentionSeconds": RETENTION_SECONDS, "maximumObservations": MAX_PERSISTED_OBSERVATIONS,
                "store": "SQLITE_WAL"}


def _asns(values: Iterable[Any]) -> list[int]:
    output = set()
    for value in values:
        try: number = int(value)
        except (TypeError, ValueError): continue
        if 0 < number <= 4_294_967_295: output.add(number)
    return sorted(output)[:MAX_ASNS]


def _prefixes(values: Iterable[Any]) -> list[str]:
    output = set()
    for value in values:
        try: output.add(str(ipaddress.ip_network(str(value), strict=False)))
        except ValueError: continue
    return sorted(output, key=lambda item: (ipaddress.ip_network(item).version, item))[:MAX_PREFIXES]


def _relevant_to_scope(row: Dict[str, Any], prefixes: list[str], asns: list[int]) -> bool:
    origins = row.get("originAsn") if isinstance(row.get("originAsn"), list) else [row.get("originAsn")]
    if any(value in asns for value in _asns(origins)):
        return True
    try: observed = ipaddress.ip_network(str(row.get("prefix")), strict=False)
    except ValueError: return False
    for value in prefixes:
        try: scoped = ipaddress.ip_network(value, strict=False)
        except ValueError: continue
        if observed.version == scoped.version and observed.overlaps(scoped):
            return True
    return False


def normalize_ris_message(envelope: Dict[str, Any]) -> list[Dict[str, Any]]:
    """Normalize UPDATE NLRIs; raw BGP bytes are deliberately ignored."""
    if envelope.get("type") != "ris_message" or not isinstance(envelope.get("data"), dict):
        return []
    data = envelope["data"]
    if data.get("type") != "UPDATE":
        return []
    try:
        timestamp = float(data["timestamp"])
    except (KeyError, TypeError, ValueError):
        return []
    path = data.get("path") if isinstance(data.get("path"), list) else []
    clean_path = []
    for hop in path[:64]:
        if isinstance(hop, list): clean_path.append(_asns(hop))
        else:
            try: clean_path.append(int(hop))
            except (TypeError, ValueError): continue
    origin = clean_path[-1] if clean_path else None
    try: peer_asn = int(data.get("peer_asn") or 0)
    except (TypeError, ValueError): peer_asn = 0
    common = {
        "collectorId": str(data.get("host") or ""), "collectorReceivedAt": timestamp,
        "collectorReceivedIso": datetime.fromtimestamp(timestamp, timezone.utc).isoformat(),
        "peer": str(data.get("peer") or ""), "peerAsn": peer_asn,
        "asPath": clean_path, "originAsn": origin,
        "evidenceClass": "CONTROL_PLANE_OBSERVATION",
        "authority": "RIS_LIVE_COLLECTOR_VANTAGE",
        "dataPlaneAuthority": "NON_AUTHORITATIVE",
        "provenance": {"provider": "RIPE RIS Live", "stream": RIS_WS_URL.split("?")[0],
                       "messageId": str(data.get("id") or ""), "rawIncluded": False},
    }
    output = []
    for announcement in list(data.get("announcements") or [])[:32]:
        for prefix in list((announcement or {}).get("prefixes") or [])[:32]:
            try: normalized = str(ipaddress.ip_network(str(prefix), strict=False))
            except ValueError: continue
            row = {**common, "messageType": "ANNOUNCE", "prefix": normalized,
                   "nextHop": str((announcement or {}).get("next_hop") or "")}
            row["id"] = "ris-" + hashlib.blake2s(json.dumps(row, sort_keys=True).encode(), digest_size=12).hexdigest()
            output.append(row)
    for withdrawal in list(data.get("withdrawals") or [])[:32]:
        for prefix in list((withdrawal or {}).get("prefixes") or [])[:32]:
            try: normalized = str(ipaddress.ip_network(str(prefix), strict=False))
            except ValueError: continue
            row = {**common, "messageType": "WITHDRAW", "prefix": normalized,
                   "originAsn": None, "asPath": []}
            row["id"] = "ris-" + hashlib.blake2s(json.dumps(row, sort_keys=True).encode(), digest_size=12).hexdigest()
            output.append(row)
    return output[:64]


class RisLiveCollector:
    def __init__(self, *, store: Optional[RisObservationStore] = None):
        self.lock = threading.RLock(); self.stop_event = threading.Event(); self.scope_event = threading.Event()
        self.thread: Optional[threading.Thread] = None; self.socket = None
        self.prefixes: list[str] = []; self.asns: list[int] = []; self.generation = 0
        self.store = store or RisObservationStore()
        self.observations: deque[Dict[str, Any]] = deque(
            self.store.query(limit=MAX_OBSERVATIONS), maxlen=MAX_OBSERVATIONS)
        self.status = "idle"; self.last_error: Optional[str] = None; self.connected_at = None

    def update_scope(self, prefixes: Iterable[Any], asns: Iterable[Any]) -> None:
        next_prefixes, next_asns = _prefixes(prefixes), _asns(asns)
        with self.lock:
            if next_prefixes == self.prefixes and next_asns == self.asns:
                return
            self.prefixes, self.asns = next_prefixes, next_asns; self.generation += 1
            self.scope_event.set()
            try:
                if self.socket: self.socket.close()
            except Exception:
                pass
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._run, name="graphops-ris-live", daemon=True); self.thread.start()

    def _run(self) -> None:
        backoff = 2.0
        while not self.stop_event.is_set():
            with self.lock: prefixes, generation = list(self.prefixes), self.generation
            if not prefixes:
                self.status = "idle"; self.scope_event.wait(10); self.scope_event.clear(); continue
            try:
                import websocket
                self.status = "connecting"
                ws = websocket.create_connection(RIS_WS_URL, timeout=20, enable_multithread=True)
                with self.lock: self.socket = ws; self.connected_at = time.time(); self.status = "connected"; self.last_error = None
                ws.send(json.dumps({"type": "ris_subscribe", "data": {
                    "type": "UPDATE", "prefix": prefixes, "moreSpecific": True, "lessSpecific": True,
                    "socketOptions": {"includeRaw": False, "acknowledge": True},
                }}))
                backoff = 2.0
                while not self.stop_event.is_set() and generation == self.generation:
                    parsed = json.loads(ws.recv())
                    normalized = normalize_ris_message(parsed)
                    self.store.insert_many(normalized)
                    for row in normalized:
                        with self.lock: self.observations.append(row)
                ws.close()
            except Exception as exc:
                with self.lock:
                    self.status = "unavailable"; self.last_error = type(exc).__name__; self.socket = None
                self.stop_event.wait(backoff); backoff = min(backoff * 2, 60.0)

    def snapshot(self, *, since: Optional[float] = None, until: Optional[float] = None,
                 limit: int = 128) -> Dict[str, Any]:
        limit = min(max(int(limit), 1), 256)
        with self.lock:
            status = self.status; error_name = self.last_error
            prefixes, asns = list(self.prefixes), list(self.asns)
        # Persistence is wider than any one live graph revision. Re-apply the
        # current environment scope before returning or disclosing observations.
        candidate_limit = MAX_PERSISTED_OBSERVATIONS if since is not None or until is not None else 4096
        candidates = self.store.query(since=since, until=until, limit=candidate_limit)
        rows = [row for row in candidates if _relevant_to_scope(row, prefixes, asns)][-limit:]
        collectors = sorted({row["collectorId"] for row in rows if row.get("collectorId")})
        revision_seed = {"scope": {"prefixes": prefixes, "asns": asns}, "observations": rows}
        revision = hashlib.sha256(json.dumps(revision_seed, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return {"status": "ok" if rows else status, "schemaVersion": SCHEMA_VERSION,
                "snapshotRevision": revision, "capturedAt": datetime.now(timezone.utc).isoformat(),
                "observationWindow": {"from": rows[0]["collectorReceivedAt"] if rows else None,
                                      "to": rows[-1]["collectorReceivedAt"] if rows else None},
                "controlPlanePaths": rows,
                "scope": {"prefixes": prefixes, "asns": asns, "bounded": True},
                "collectors": collectors, "summary": {"observations": len(rows), "collectors": len(collectors),
                                                        **self.store.stats()},
                "lastError": error_name,
                "provenance": {"provider": "RIPE RIS Live", "stream": RIS_WS_URL.split("?")[0],
                               "rawIncluded": False, "collectorVantageExplicit": True,
                               "currentEnvironmentScopeReapplied": True},
                "boundary": "RIS LIVE IS OBSERVED CONTROL-PLANE EVIDENCE AT COLLECTOR VANTAGES; IT IS NON-AUTHORITATIVE FOR DATA-PLANE ROUTING; THE LOCAL STORE IS BOUNDED BY AGE AND COUNT"}

    def close(self) -> None:
        self.stop_event.set()
        try:
            if self.socket: self.socket.close()
        except Exception:
            pass


_COLLECTOR = RisLiveCollector()


def get_ris_live_collector() -> RisLiveCollector:
    return _COLLECTOR
