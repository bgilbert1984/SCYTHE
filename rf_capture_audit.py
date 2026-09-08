"""The capture-owner's own record of recovery, independent of the IQ ring.

Why this is not ``invalidation_history``
----------------------------------------
On 2026-09-06 a source starved for 45 seconds and the invalidation history
recorded nothing at all. That was not a bug: ``IQRetentionOwner.invalidate``
returns early when no ring is allocated, and no ring had been -- not one sample
had ever arrived. The clear correctly did not happen, so correctly nothing was
written.

The consequence is the problem. Every pre-first-sample failure, which is exactly
the class of failure a wedged capture process produces after a restart, leaves
no trace in the only history that existed. Ring invalidation answers "what did
we throw away and why". Incident auditing answers "what did this system decide,
attempt, and observe". They are different questions, and a mechanism that only
speaks when a ring exists cannot answer the second.

This store outlives ring allocation because it never consults one.

Bounded, always
---------------
Fixed-capacity deque, a closed event vocabulary, and every free-text field
truncated on the way in. Command output in particular never enters unbounded:
a failing ``busctl`` can emit arbitrarily much, and an audit trail that a
failure can flood is an audit trail that a failure can erase.
"""

from __future__ import annotations

from collections import deque
import threading
import time
from typing import Any, Dict, Optional, Tuple


SCHEMA = "scythe.rf-capture-recovery-audit.v1"

MAX_AUDIT_RECORDS = 64
# Enough to identify a failure, far too little to flood a record with.
MAX_DETAIL_CHARS = 240
MAX_DETAIL_KEYS = 8

# The closed vocabulary. An unrecognised event is refused rather than recorded,
# so the history cannot quietly grow a category nobody reviews.
EVENTS: Tuple[str, ...] = (
    "AUTHORIZATION_CREATED",
    "AUTHORIZATION_EXPIRED",
    "AUTHORIZATION_INVALIDATED",
    # Shadow's terminal event. Named for what it is -- a judgement -- so it can
    # never be read as, or counted as, something that happened to a process.
    "WOULD_REQUEST_RESTART",
    "RESTART_ATTEMPTED",
    # A D-Bus reply, and nothing more. The unit manager accepted a job; whether
    # samples ever arrive is a separate question with a separate event.
    "RESTART_REQUEST_ACCEPTED",
    "RESTART_REQUEST_FAILED",
    "SAMPLE_FLOW_RESTORED",
    "PROCESS_RESTARTED_STILL_STARVED",
    # The deadline passed and the incarnation never advanced. Distinct from the
    # line above, which asserts a restart that this one did not observe.
    "RESTART_NOT_OBSERVED",
    # Not a failed restart: a boot boundary left no evidence to judge one.
    # Separate so a recovery-failure count never becomes a reboot count.
    "RECOVERY_OUTCOME_UNDETERMINED",
    "RECOVERY_SUPPRESSED",
    # Shadow's suppression. Named apart from RECOVERY_SUPPRESSED so a simulated
    # budget can never be counted as a real one.
    "WOULD_BE_SUPPRESSED",
)

# Events that mean a real process was targeted. Shadow may never write one.
ACTING_EVENTS: Tuple[str, ...] = (
    "RESTART_ATTEMPTED", "RESTART_REQUEST_ACCEPTED", "RESTART_REQUEST_FAILED")


class UnknownRecoveryEvent(ValueError):
    """Raised rather than recorded. A typo must not become a category."""


def _bounded(value: Any) -> Any:
    if isinstance(value, str):
        return value[:MAX_DETAIL_CHARS]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:MAX_DETAIL_CHARS]


class CaptureRecoveryAudit:
    """Bounded, thread-safe, and owned by the capture owner rather than the ring."""

    def __init__(self, maxlen: int = MAX_AUDIT_RECORDS) -> None:
        self._lock = threading.RLock()
        self._records: deque = deque(maxlen=maxlen)
        self._counts: Dict[str, int] = {}
        # Shadow judgements are counted here and nowhere near the attempt
        # sequence. A shadow run must not be able to consume a real budget.
        self._shadow_decisions = 0
        self._recorded = 0

    def record(self, event: str, *, mode: str, reason: str,
               incident_id: Optional[str] = None,
               attempt_id: Optional[str] = None,
               target: Optional[Dict[str, Any]] = None,
               detail: Optional[Dict[str, Any]] = None,
               monotonic_ns: Optional[int] = None) -> Dict[str, Any]:
        if event not in EVENTS:
            raise UnknownRecoveryEvent(
                f"unknown recovery event {str(event)[:48]!r}; expected one of "
                f"{', '.join(EVENTS)}")
        if mode == "SHADOW" and event in ACTING_EVENTS:
            # A shadow run that could write an acting event would make the audit
            # trail unable to answer the only question it exists for: did this
            # system ever touch the process.
            raise UnknownRecoveryEvent(
                f"{event} cannot be recorded in SHADOW mode; shadow does not act")
        bounded_detail = None
        if detail:
            bounded_detail = {str(k)[:48]: _bounded(v)
                              for k, v in list(detail.items())[:MAX_DETAIL_KEYS]}
        record = {
            "event": event,
            "incident_id": incident_id,
            "attempt_id": attempt_id,
            "target": dict(target) if target else None,
            "monotonic_ns": int(time.monotonic_ns() if monotonic_ns is None
                                else monotonic_ns),
            # Wall clock for a human reading the log, never for a comparison.
            # Every decision in this system is made on the monotonic field above.
            "observed_at_display": time.time(),
            "mode": mode,
            "reason": str(reason)[:MAX_DETAIL_CHARS],
            "detail": bounded_detail,
        }
        with self._lock:
            self._records.append(record)
            self._counts[event] = self._counts.get(event, 0) + 1
            self._recorded += 1
            if event in ("WOULD_REQUEST_RESTART", "WOULD_BE_SUPPRESSED"):
                self._shadow_decisions += 1
        return record

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "schema": SCHEMA,
                "independent_of_ring_allocation": True,
                "independence_note": (
                    "THIS HISTORY IS WRITTEN WHETHER OR NOT AN IQ RING EXISTS. "
                    "A PRE-FIRST-SAMPLE FAILURE LEAVES A RECORD HERE AND NONE "
                    "IN invalidation_history, WHICH IS THE DEFECT IT EXISTS FOR"),
                "events": list(EVENTS),
                "capacity": self._records.maxlen,
                "recorded_total": self._recorded,
                "retained": len(self._records),
                "truncated": self._recorded > len(self._records),
                "counts": dict(self._counts),
                # Kept apart from any attempt count, deliberately.
                "shadow_decisions": self._shadow_decisions,
                "records": list(self._records),
            }
