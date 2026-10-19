"""Durable, append-only audit of control actions.

Answers, after the fact: who did what, when, through which proposal, with what
parameters, and what happened.  It records operational control -- tool
executions that change state, hardware first -- and is deliberately separate
from the graph: an RF retune may never create a graph mutation, and still needs
an accountable history.  It does not go through WriteBus.

Each execution writes two rows sharing an ``execution_id``: ``started`` before
the tool runs and ``success`` or ``error`` after.  A ``started`` row with no
partner is an execution that did not finish recording (crash, kill), which is
itself worth knowing for a hardware action.

Append-only is enforced in the database, not just by convention: UPDATE is
refused outright, and DELETE is refused for any row still inside the retention
window.  Retention is configuration (``control_audit_retention_days``, default
90) and is applied by ``purge_expired()``, which is never called automatically.
Anyone who can edit the database file can drop the triggers, so this is
tamper-evident policy, not protection from a privileged host user.

    python control_audit.py list  <db> [--proposal ID] [--limit N]
    python control_audit.py purge <db>        # uses the retention stored in the db
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import os
import socket
import sqlite3
import sys
import threading
import time
import uuid
from functools import lru_cache
from typing import Any, Callable, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 90

SCHEMA = """
CREATE TABLE IF NOT EXISTS control_audit_events (
    event_id         TEXT PRIMARY KEY,
    timestamp        TEXT NOT NULL,
    ts_epoch         REAL NOT NULL,
    execution_id     TEXT NOT NULL,
    principal_kind   TEXT NOT NULL,
    principal_id     TEXT,
    session_id       TEXT,
    action           TEXT NOT NULL,
    effect           TEXT NOT NULL,
    proposal_id      TEXT,
    parameters_json  TEXT NOT NULL,
    result           TEXT NOT NULL,
    result_summary   TEXT,
    software_version TEXT,
    deployment_id    TEXT,
    host_id          TEXT
);
CREATE INDEX IF NOT EXISTS idx_cae_ts        ON control_audit_events (ts_epoch);
CREATE INDEX IF NOT EXISTS idx_cae_proposal  ON control_audit_events (proposal_id);
CREATE INDEX IF NOT EXISTS idx_cae_execution ON control_audit_events (execution_id);
CREATE TABLE IF NOT EXISTS control_audit_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS control_audit_no_update
BEFORE UPDATE ON control_audit_events
BEGIN SELECT RAISE(ABORT, 'control_audit_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS control_audit_no_early_delete
BEFORE DELETE ON control_audit_events
WHEN OLD.ts_epoch > CAST(strftime('%s','now') AS REAL)
     - (SELECT CAST(value AS REAL) FROM control_audit_meta WHERE key = 'retention_days') * 86400.0
BEGIN SELECT RAISE(ABORT, 'control_audit_events row is inside the retention window'); END;
"""

_SECRET_WORDS = ("token", "secret", "password", "passwd", "apikey", "api_key", "authorization", "credential")


def redact(value: Any) -> Any:
    """Copy of ``value`` with the contents of obviously secret-named keys removed."""
    if isinstance(value, dict):
        return {k: ("<redacted>" if any(w in str(k).lower() for w in _SECRET_WORDS) else redact(v))
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


@lru_cache(maxsize=1)
def _software_version() -> Optional[str]:
    try:
        from ingest_manifest import git_commit
        return git_commit()
    except Exception:
        return None


class ControlAuditStore:
    def __init__(self, path: Union[str, Callable[[], str]], retention_days: Optional[int] = DEFAULT_RETENTION_DAYS,
                 deployment_id: Union[None, str, Callable[[], Optional[str]]] = None):
        """``retention_days=None`` opens an existing store without touching its
        stored retention setting (for inspection tools); the server passes a value."""
        if retention_days is not None and retention_days < 1:
            raise ValueError("control_audit_retention_days must be at least 1")
        self._path = path
        self._deployment_id = deployment_id      # which operational instance ran it (git_commit says what code)
        self.retention_days = None if retention_days is None else int(retention_days)
        self._lock = threading.Lock()
        self._db: Optional[sqlite3.Connection] = None
        self.write_failures = 0
        self._host = socket.gethostname()

    def _deployment(self) -> Optional[str]:
        try:
            value = self._deployment_id() if callable(self._deployment_id) else self._deployment_id
        except Exception:
            return None
        return str(value) if value else None

    @property
    def path(self) -> str:
        return self._path() if callable(self._path) else self._path

    def _conn(self) -> sqlite3.Connection:
        # Opened on first use so a data directory chosen after import is honoured.
        if self._db is None:
            path = self.path
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            if self.retention_days is not None:
                db.execute("INSERT OR REPLACE INTO control_audit_meta (key, value) VALUES ('retention_days', ?)",
                           (str(self.retention_days),))
            else:
                row = db.execute("SELECT value FROM control_audit_meta WHERE key = 'retention_days'").fetchone()
                self.retention_days = int(float(row[0])) if row else DEFAULT_RETENTION_DAYS
            self._db = db
        return self._db

    def record(self, *, execution_id: str, action: str, effect: str, result: str,
               principal: Optional[Dict[str, Any]] = None, proposal_id: Optional[str] = None,
               parameters: Optional[Dict[str, Any]] = None, result_summary: Optional[str] = None,
               timestamp: Optional[float] = None) -> str:
        """Append one event and return its id.  Raises on failure; see ``safe_record``."""
        ts = time.time() if timestamp is None else float(timestamp)
        principal = principal or {}
        event_id = str(uuid.uuid4())
        row = (
            event_id,
            _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).isoformat(),
            ts, execution_id,
            principal.get("kind") or "none", principal.get("operator_id"), principal.get("session_id"),
            action, effect, proposal_id,
            json.dumps(redact(parameters or {}), sort_keys=True, default=str),
            result, result_summary, _software_version(), self._deployment(), self._host,
        )
        with self._lock:
            self._conn().execute(
                "INSERT INTO control_audit_events (event_id, timestamp, ts_epoch, execution_id, principal_kind,"
                " principal_id, session_id, action, effect, proposal_id, parameters_json, result,"
                " result_summary, software_version, deployment_id, host_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
        return event_id

    def safe_record(self, **fields) -> Optional[str]:
        """``record`` that never raises: a failed audit write is logged loudly and counted."""
        try:
            return self.record(**fields)
        except Exception:
            self.write_failures += 1
            logger.error("[control_audit] FAILED to record %s %s (failures=%d)",
                         fields.get("action"), fields.get("result"), self.write_failures, exc_info=True)
            return None

    def events(self, *, proposal_id: Optional[str] = None, execution_id: Optional[str] = None,
               limit: int = 100) -> List[Dict[str, Any]]:
        where, args = [], []
        if proposal_id:
            where.append("proposal_id = ?"); args.append(proposal_id)
        if execution_id:
            where.append("execution_id = ?"); args.append(execution_id)
        sql = "SELECT * FROM control_audit_events" + (" WHERE " + " AND ".join(where) if where else "") \
              + " ORDER BY ts_epoch, rowid LIMIT ?"
        with self._lock:
            cur = self._conn().execute(sql, (*args, int(limit)))
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def purge_expired(self) -> int:
        """Delete rows older than the retention window; returns how many.  Never automatic."""
        with self._lock:
            db = self._conn()
            before = db.total_changes
            db.execute("DELETE FROM control_audit_events WHERE ts_epoch <= ?",
                       (time.time() - self.retention_days * 86400.0,))
            return db.total_changes - before

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None


# ── process-wide store and the execution hook ────────────────────────────────

_store: Optional[ControlAuditStore] = None


def configure(path: Union[str, Callable[[], str]], retention_days: Optional[int] = None,
              deployment_id: Union[None, str, Callable[[], Optional[str]]] = None) -> ControlAuditStore:
    """Install the process-wide store.  ``retention_days`` falls back to the
    ``SCYTHE_CONTROL_AUDIT_RETENTION_DAYS`` environment variable, then 90."""
    global _store
    if retention_days is None:
        retention_days = int(os.environ.get("SCYTHE_CONTROL_AUDIT_RETENTION_DAYS", DEFAULT_RETENTION_DAYS))
    _store = ControlAuditStore(path, retention_days, deployment_id)
    return _store


def get_store() -> Optional[ControlAuditStore]:
    return _store


def reset() -> None:
    global _store
    if _store is not None:
        _store.close()
    _store = None


def begin_execution(tool, params: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Record that a state-changing tool is about to run.  None when auditing does not apply."""
    store = _store
    if store is None or not getattr(tool, "mutates_state", False):
        return None
    from mcp_principal import current_proposal_id, principal_dict
    ctx = {
        "store": store, "execution_id": str(uuid.uuid4()), "action": tool.name,
        "effect": getattr(tool, "effect", None) or "UNDECLARED", "params": params or {},
        "principal": principal_dict(), "proposal_id": current_proposal_id(),
    }
    _write(ctx, "started")
    return ctx


def finish_execution(ctx: Optional[Dict[str, Any]], *, result_summary: Optional[str] = None,
                     error: Optional[BaseException] = None) -> None:
    if ctx is None:
        return
    if error is not None:
        _write(ctx, "error", f"{type(error).__name__}: {error}"[:500])
    else:
        _write(ctx, "success", result_summary)


def _write(ctx: Dict[str, Any], result: str, summary: Optional[str] = None) -> None:
    ctx["store"].safe_record(
        execution_id=ctx["execution_id"], action=ctx["action"], effect=ctx["effect"], result=result,
        principal=ctx["principal"], proposal_id=ctx["proposal_id"], parameters=ctx["params"],
        result_summary=summary)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list"); ls.add_argument("db"); ls.add_argument("--proposal"); ls.add_argument("--limit", type=int, default=50)
    pg = sub.add_parser("purge"); pg.add_argument("db")
    a = ap.parse_args(argv)
    store = ControlAuditStore(a.db, retention_days=None)   # use the retention the server stored
    if a.cmd == "list":
        for e in store.events(proposal_id=a.proposal, limit=a.limit):
            print(json.dumps(e, sort_keys=True))
    else:
        print(f"purged {store.purge_expired()} rows older than {store.retention_days} days")
    return 0


if __name__ == "__main__":
    sys.exit(main())
