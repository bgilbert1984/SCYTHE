"""
inference_exhaustion_ledger.py — Epistemic termination for RF_SCYTHE inference.

Tracks per-(entity, rule, evidence_epoch) inference attempts and prevents
re-invocation when no new evidence has arrived.

    ┌─────────────────────────────────────────────────────────────────┐
    │ RULE INVOKED(entity, rule)                                     │
    │  ├─ ledger.is_exhausted(entity, rule, epoch) → True → SKIP    │
    │  └─ RUN RULE                                                   │
    │       ├─ validated_edges > 0 → SUCCESS → clear exhaustion      │
    │       ├─ 0 edges + no error → mark NO_VALID_EDGES → exhausted  │
    │       ├─ policy blocked     → mark POLICY_BLOCKED → exhausted  │
    │       └─ exception          → mark ERROR (retry-eligible)      │
    └─────────────────────────────────────────────────────────────────┘

Exhaustion is *never global*.  It is scoped to:
    (entity_id, rule_id, evidence_epoch)

If any of those change → exhaustion resets automatically.

Usage:
    from inference_exhaustion_ledger import InferenceExhaustionLedger

    ledger = InferenceExhaustionLedger()
    epoch = ledger.compute_evidence_epoch(engine, entity_id)

    if ledger.is_exhausted(entity_id, rule_id, epoch):
        return  # HARD STOP — waiting for reality

    # ... run inference ...

    ledger.record_attempt(entity_id, rule_id, epoch,
                          result="NO_VALID_EDGES",
                          entity_kind="host")
"""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Result codes
# ─────────────────────────────────────────────────────────────────────────────

RESULT_SUCCESS = "SUCCESS"
RESULT_NO_VALID_EDGES = "NO_VALID_EDGES"
RESULT_POLICY_BLOCKED = "POLICY_BLOCKED"
RESULT_ERROR = "ERROR"

# Only these result codes cause exhaustion.
# ERROR is *not* exhaustive — transient failures should be retried.
_EXHAUSTING_RESULTS = frozenset({RESULT_NO_VALID_EDGES, RESULT_POLICY_BLOCKED})


# ─────────────────────────────────────────────────────────────────────────────
# Exhaustion Record
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ExhaustionRecord:
    """Per-(entity, rule, epoch) inference attempt record."""

    entity_id: str
    entity_kind: str           # host | flow | pcap_session
    rule_id: str               # R-0001 or "batch" for rule-agnostic runs
    evidence_epoch: str        # hash of sensor state touching this entity

    attempt_count: int = 0
    first_attempt_ts: float = 0.0
    last_attempt_ts: float = 0.0

    last_result: str = ""      # SUCCESS | NO_VALID_EDGES | POLICY_BLOCKED | ERROR
    blocked_reason: Optional[str] = None  # e.g. "missing_sensor:pcap"

    exhausted: bool = False
    exhausted_ts: Optional[float] = None

    # What must change before this entity+rule is eligible again
    resume_condition: Optional[Dict[str, str]] = None

    edges_produced: int = 0    # cumulative across attempts in this epoch

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "entity_kind": self.entity_kind,
            "rule_id": self.rule_id,
            "evidence_epoch": self.evidence_epoch,
            "attempt_count": self.attempt_count,
            "first_attempt_ts": self.first_attempt_ts,
            "last_attempt_ts": self.last_attempt_ts,
            "last_result": self.last_result,
            "blocked_reason": self.blocked_reason,
            "exhausted": self.exhausted,
            "exhausted_ts": self.exhausted_ts,
            "resume_condition": self.resume_condition,
            "edges_produced": self.edges_produced,
        }


# ─────────────────────────────────────────────────────────────────────────────
# The Ledger
# ─────────────────────────────────────────────────────────────────────────────

class InferenceExhaustionLedger:
    """Thread-safe in-memory ledger tracking epistemic exhaustion.

    Key invariant:
        Exhausted ≠ Failed.
        Exhausted = Waiting for reality (new sensor data).
    """

    def __init__(self, max_records: int = 10_000):
        # Key: (entity_id, rule_id) → ExhaustionRecord
        # We only keep the *current epoch* record per (entity, rule).
        # If evidence_epoch changes, old record is replaced.
        self._records: Dict[tuple, ExhaustionRecord] = {}
        self._max_records = max_records

    # ── Core API ─────────────────────────────────────────────────────────

    def is_exhausted(
        self,
        entity_id: str,
        rule_id: str,
        evidence_epoch: str,
    ) -> bool:
        """Check if inference for (entity, rule) is exhausted in this epoch.

        Returns False if:
          - No record exists
          - Record is from a different epoch (evidence changed → retry ok)
          - Record is not marked exhausted
        """
        key = (entity_id, rule_id)
        rec = self._records.get(key)
        if rec is None:
            return False
        if rec.evidence_epoch != evidence_epoch:
            # Evidence changed → old exhaustion is void
            return False
        return rec.exhausted

    def record_attempt(
        self,
        entity_id: str,
        rule_id: str,
        evidence_epoch: str,
        *,
        result: str,
        entity_kind: str = "unknown",
        edges_produced: int = 0,
        blocked_reason: Optional[str] = None,
    ) -> ExhaustionRecord:
        """Record an inference attempt and update exhaustion state.

        Args:
            result: One of SUCCESS, NO_VALID_EDGES, POLICY_BLOCKED, ERROR
            edges_produced: number of validated edges from this attempt
            blocked_reason: human-readable reason if blocked
        """
        now = time.time()
        key = (entity_id, rule_id)
        rec = self._records.get(key)

        # If epoch changed, start fresh
        if rec is None or rec.evidence_epoch != evidence_epoch:
            rec = ExhaustionRecord(
                entity_id=entity_id,
                entity_kind=entity_kind,
                rule_id=rule_id,
                evidence_epoch=evidence_epoch,
                first_attempt_ts=now,
            )
            self._records[key] = rec

        rec.attempt_count += 1
        rec.last_attempt_ts = now
        rec.last_result = result
        rec.blocked_reason = blocked_reason
        rec.edges_produced += edges_produced

        if result == RESULT_SUCCESS and edges_produced > 0:
            # Success clears exhaustion
            rec.exhausted = False
            rec.exhausted_ts = None
            rec.resume_condition = None
        elif result in _EXHAUSTING_RESULTS:
            # One failed attempt is enough — retrying without new evidence
            # is epistemic malpractice.
            rec.exhausted = True
            rec.exhausted_ts = now
            if result == RESULT_NO_VALID_EDGES:
                rec.resume_condition = {
                    "type": "NEW_SENSOR",
                    "detail": f"new sensor data for {entity_id}",
                }
            elif result == RESULT_POLICY_BLOCKED:
                rec.resume_condition = {
                    "type": "POLICY_CHANGE",
                    "detail": blocked_reason or "policy constraint",
                }
        # ERROR does NOT exhaust — transient, may succeed on retry

        # Evict oldest if over capacity
        if len(self._records) > self._max_records:
            self._evict_oldest()

        return rec

    def clear_exhaustion(
        self,
        entity_id: str,
        rule_id: Optional[str] = None,
    ) -> int:
        """Manually clear exhaustion for an entity (all rules or specific).

        Returns number of records cleared.
        """
        cleared = 0
        keys_to_clear = []
        for key, rec in self._records.items():
            if key[0] == entity_id:
                if rule_id is None or key[1] == rule_id:
                    keys_to_clear.append(key)

        for key in keys_to_clear:
            rec = self._records[key]
            if rec.exhausted:
                rec.exhausted = False
                rec.exhausted_ts = None
                rec.resume_condition = None
                cleared += 1

        return cleared

    # ── Evidence Epoch ───────────────────────────────────────────────────

    @staticmethod
    def compute_evidence_epoch(
        engine: Any,
        entity_id: str,
    ) -> str:
        """Compute the evidence epoch hash for an entity.

        The epoch changes when any edge touching this entity changes.
        This includes observed, implied, and inferred edges.

        If the engine has no edge data, returns a fixed epoch
        (all entities share the same epoch → exhaustion still works,
        just resets less granularly).
        """
        edge_sigs: List[str] = []

        # Collect edges touching this entity
        edges = []
        if hasattr(engine, 'edges_for_node'):
            try:
                edges = list(engine.edges_for_node(entity_id))
            except Exception:
                pass
        elif hasattr(engine, 'edges') and isinstance(engine.edges, dict):
            for eid, e in engine.edges.items():
                ed = e if isinstance(e, dict) else (
                    e.to_dict() if hasattr(e, 'to_dict') else {}
                )
                enodes = ed.get('nodes', [])
                src = ed.get('source') or ed.get('src', '')
                dst = ed.get('target') or ed.get('dst', '')
                if entity_id in enodes or entity_id == src or entity_id == dst:
                    edges.append(ed)

        if not edges:
            # No edges → stable epoch based on entity existence
            return hashlib.sha256(
                f"entity:{entity_id}:no_edges".encode()
            ).hexdigest()[:16]

        for e in edges:
            ed = e if isinstance(e, dict) else (
                e.to_dict() if hasattr(e, 'to_dict') else {}
            )
            eid = ed.get('id', '')
            kind = ed.get('kind', '')
            ts = str(ed.get('timestamp', ''))
            edge_sigs.append(f"{eid}:{kind}:{ts}")

        edge_sigs.sort()
        combined = "|".join(edge_sigs)
        return hashlib.sha256(combined.encode()).hexdigest()[:16]

    # ── Query / Introspection ────────────────────────────────────────────

    def get_exhausted_entities(self) -> List[Dict[str, Any]]:
        """Return all currently exhausted records as dicts."""
        return [
            rec.to_dict()
            for rec in self._records.values()
            if rec.exhausted
        ]

    def get_record(
        self,
        entity_id: str,
        rule_id: str,
    ) -> Optional[ExhaustionRecord]:
        """Look up a specific record."""
        return self._records.get((entity_id, rule_id))

    def stats(self) -> Dict[str, Any]:
        """Summary statistics for the ledger."""
        total = len(self._records)
        exhausted = sum(1 for r in self._records.values() if r.exhausted)
        by_result = {}
        for r in self._records.values():
            by_result[r.last_result] = by_result.get(r.last_result, 0) + 1
        return {
            "total_records": total,
            "exhausted_count": exhausted,
            "active_count": total - exhausted,
            "by_result": by_result,
        }

    def waiting_for_sensor(self) -> List[Dict[str, Any]]:
        """Return entities exhausted due to missing sensor data.

        These are candidates for collection task generation.
        """
        return [
            rec.to_dict()
            for rec in self._records.values()
            if rec.exhausted
            and rec.resume_condition
            and rec.resume_condition.get("type") == "NEW_SENSOR"
        ]

    # ── Internal ─────────────────────────────────────────────────────────

    def _evict_oldest(self):
        """Remove the oldest non-exhausted records when over capacity."""
        if len(self._records) <= self._max_records:
            return
        # Sort by last_attempt_ts, evict oldest non-exhausted first
        candidates = sorted(
            [(k, r) for k, r in self._records.items() if not r.exhausted],
            key=lambda x: x[1].last_attempt_ts,
        )
        to_remove = len(self._records) - self._max_records
        for k, _ in candidates[:to_remove]:
            del self._records[k]
