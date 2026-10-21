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
import contextlib
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
    source           TEXT,
    initiator        TEXT,
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
            if "source" not in {r[1] for r in db.execute("PRAGMA table_info(control_audit_events)")}:
                db.execute("ALTER TABLE control_audit_events ADD COLUMN source TEXT")   # stores created before #227
            if "initiator" not in {r[1] for r in db.execute("PRAGMA table_info(control_audit_events)")}:
                db.execute("ALTER TABLE control_audit_events ADD COLUMN initiator TEXT")   # stores created before ADR 0005 D5
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
               timestamp: Optional[float] = None, source: Optional[str] = None,
               initiator: Optional[str] = None) -> str:
        """Append one event and return its id.  Raises on failure; see ``safe_record``."""
        ts = time.time() if timestamp is None else float(timestamp)
        principal = principal or {}
        event_id = str(uuid.uuid4())
        row = (
            event_id,
            _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).isoformat(),
            ts, execution_id, source, initiator,
            principal.get("kind") or "none", principal.get("operator_id"), principal.get("session_id"),
            action, effect, proposal_id,
            json.dumps(redact(parameters or {}), sort_keys=True, default=str),
            result, result_summary, _software_version(), self._deployment(), self._host,
        )
        with self._lock:
            self._conn().execute(
                "INSERT INTO control_audit_events (event_id, timestamp, ts_epoch, execution_id, source,"
                " initiator, principal_kind, principal_id, session_id, action, effect, proposal_id, parameters_json, result,"
                " result_summary, software_version, deployment_id, host_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
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


def begin_action(*, action: str, effect: str, params: Optional[Dict[str, Any]], principal: Optional[Dict[str, Any]],
                 source: str, proposal_id: Optional[str] = None,
                 initiator: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Write the ``started`` row for an action and return the context ``finish_execution`` closes.

    ``source`` says which entry point asked: ``mcp`` or an HTTP route such as
    ``api.sdr.tune``, so the same underlying effect reached two ways stays distinguishable.
    """
    store = _store
    if store is None:
        return None
    ctx = {
        "store": store, "execution_id": str(uuid.uuid4()), "action": action, "effect": effect or "UNDECLARED",
        "params": params or {}, "principal": principal, "proposal_id": proposal_id, "source": source,
        "initiator": initiator,
    }
    _write(ctx, "started")
    return ctx


def begin_execution(tool, params: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """MCP entry: record that a state-changing tool is about to run.  None when auditing does not apply."""
    if _store is None or not getattr(tool, "mutates_state", False):
        return None
    from mcp_principal import current_initiator, current_proposal_id, current_source, principal_dict
    return begin_action(action=tool.name, effect=getattr(tool, "effect", None), params=params,
                        principal=principal_dict(), source=current_source(), proposal_id=current_proposal_id(),
                        initiator=current_initiator())


PROPOSAL_CREATED = "proposal.created"
PROPOSAL_APPROVED = "proposal.approved"
PROPOSAL_DENIED = "proposal.denied"
PROPOSAL_EXPIRED = "proposal.expired"
PROPOSAL_ACTIONS = (PROPOSAL_CREATED, PROPOSAL_APPROVED, PROPOSAL_DENIED, PROPOSAL_EXPIRED)


def record_transition(*, action: str, effect: Optional[str], principal: Optional[Dict[str, Any]], proposal_id: str,
                      result: str, parameters: Optional[Dict[str, Any]] = None,
                      summary: Optional[str] = None, source: Optional[str] = None,
                      initiator: Optional[str] = None) -> Optional[str]:
    """Append one proposal-lifecycle row (ADR 0006).  Evidence only.

    ``proposal_id`` is the join key that ties a proposal's rows to its execution rows.
    Best effort and a no-op without a store: nothing in the approval or execution
    decision reads these rows or depends on this write succeeding.
    """
    store = _store
    if store is None:
        return None
    from mcp_principal import current_source
    return store.safe_record(execution_id=str(uuid.uuid4()), action=action, effect=effect or "UNDECLARED",
                             result=result, principal=principal, proposal_id=proposal_id, parameters=parameters,
                             result_summary=summary, source=source or current_source(), initiator=initiator)


def reconstruct_proposal(store: "ControlAuditStore", proposal_id: str) -> Dict[str, Any]:
    """The recorded history of one proposal, from its audit rows alone.

    Who proposed, approved, denied and executed it, and in what order.  This is a read
    of history for people and tools; it is never an input to an approval or execution
    decision, and a proposal that is gone from memory is not made executable by it.
    """
    rows = store.events(proposal_id=proposal_id, limit=1000)
    out: Dict[str, Any] = {"proposal_id": proposal_id, "events": [(r["action"], r["result"]) for r in rows],
                           "proposed_by": None, "approved_by": None, "denied_by": None, "executed_by": None,
                           "expired": False, "executed": False}
    for r in rows:
        who = r["principal_id"] or r["principal_kind"]
        if r["action"] == PROPOSAL_CREATED:
            out["proposed_by"] = who
            out.setdefault("tool", json.loads(r["parameters_json"]).get("tool"))
        elif r["action"] == PROPOSAL_APPROVED:
            out["approved_by"] = who
        elif r["action"] == PROPOSAL_DENIED:
            out["denied_by"] = who
        elif r["action"] == PROPOSAL_EXPIRED:
            out["expired"] = True
        elif r["result"] == "success":
            out["executed_by"], out["executed"] = who, True
    return out


class _Outcome:
    """Lets the audited block say what happened, for the closing row."""
    summary: Optional[str] = None


@contextlib.contextmanager
def audited(*, action: str, effect: str, params: Optional[Dict[str, Any]], principal: Optional[Dict[str, Any]],
            source: str):
    """Audit the block: ``started`` on entry, then ``success`` or ``error`` on exit.

    An exception is recorded and re-raised unchanged, so a caller's own error
    handling and responses are unaffected.  With no store configured this is a no-op.
    """
    ctx = begin_action(action=action, effect=effect, params=params, principal=principal, source=source)
    outcome = _Outcome()
    try:
        yield outcome
    except BaseException as exc:
        finish_execution(ctx, error=exc)
        raise
    finish_execution(ctx, result_summary=outcome.summary)


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
        result_summary=summary, source=ctx["source"], initiator=ctx.get("initiator"))


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
