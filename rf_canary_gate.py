"""Visit and tuning gates for antenna-on observation runs (§5.21 canary proposal, owner-accepted 2026-11-15).

Pure functions. Nothing here contacts a device or changes the sequencer; wiring a gate into a
run needs the antenna-on mode and its own frozen authorization (see
docs/521_CANARY_THRESHOLD_PROPOSAL_DRAFT.md section 6, slices 3).

Visit gate: every intrinsic-tier offset (today +-256 kHz, so both sides) is detected in the visit
AND the weakest of them is >= CANARY_MIN_EXCESS_DB above its local floor; the clipped-sample
fraction is <= CLIP_LIMIT_PCT (that last rule is the proposal's separate guard, section 4.3).
Tuning gate: a tuning is scored only if EVERY expected visit passed.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Sequence

REVISION = "canary-1"
CANARY_MIN_EXCESS_DB = 11.0
CANARY_MATCH_TOL_HZ = 60.0
CLIP_LIMIT_PCT = 0.001


class CanaryRefused(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def tier_offsets(tier: Dict[str, Any]) -> List[float]:
    """Verify a tier artifact from rf_catalogue_tiers and return its offsets (Hz)."""
    doc = dict(tier)
    claimed = doc.pop("content_sha256", None)
    if not claimed:
        raise CanaryRefused("TIER_UNHASHED")
    actual = hashlib.sha256(json.dumps(doc, indent=1, sort_keys=True).encode()).hexdigest()
    if actual != claimed:
        raise CanaryRefused("TIER_HASH_MISMATCH")
    rule = doc.get("rule", {})
    if rule.get("min_sessions", 0) < 3:
        raise CanaryRefused("TIER_TOO_FEW_SESSIONS")
    offsets = [float(r["offset_hz"]) for r in doc.get("intrinsic", [])]
    if not offsets:
        raise CanaryRefused("TIER_EMPTY")
    return offsets


def clipped_pct(raw_windows: Sequence[bytes]) -> float:
    total = sum(len(w) for w in raw_windows)
    if not total:
        raise CanaryRefused("NO_SAMPLES")
    return 100.0 * sum(w.count(0) + w.count(255) for w in raw_windows) / total


def visit_gate(candidates: Sequence[Any], raw_windows: Sequence[bytes],
               offsets_hz: Sequence[float]) -> Dict[str, Any]:
    """`candidates` have .baseband_hz and .excess_db (the sequencer's CandidateFeature)."""
    excess: Dict[str, Any] = {}
    for off in offsets_hz:
        hits = [c.excess_db for c in candidates
                if abs(c.baseband_hz - off) < CANARY_MATCH_TOL_HZ]
        excess[f"{off:+.0f}"] = max(hits) if hits else None
    reasons = []
    missing = [k for k, v in excess.items() if v is None]
    if missing:
        reasons.append("CANARY_NOT_DETECTED")
    elif min(excess.values()) < CANARY_MIN_EXCESS_DB:
        reasons.append("CANARY_BELOW_MIN_EXCESS")
    clip = clipped_pct(raw_windows)
    if clip > CLIP_LIMIT_PCT:
        reasons.append("CLIPPING_ABOVE_LIMIT")
    return {"passed": not reasons, "reasons": reasons, "canary_excess_db": excess,
            "clipped_pct": clip, "gate_revision": REVISION}


def tuning_gate(visit_results: Sequence[Dict[str, Any]], expected_visits: int) -> Dict[str, Any]:
    n = len(visit_results)
    reasons = []
    if n != expected_visits:
        reasons.append("VISIT_COUNT_MISMATCH")
    if any(not v["passed"] for v in visit_results):
        reasons.append("A_VISIT_FAILED_THE_GATE")
    return {"scored": not reasons, "reasons": reasons, "visits": n,
            "visits_passed": sum(1 for v in visit_results if v["passed"]),
            "gate_revision": REVISION}
