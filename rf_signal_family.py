"""Phase 0 evidence contract for RF signal-family classification.

No detector ships in this module and none is imported by it.  What ships is the
vocabulary, the admission gate and the declared absences, so that the counters on
the spectrum panel state a reason instead of a bare zero.

Two findings from ``docs/RF_Signal_Family_Classifier_Scope.md`` are encoded here
as refusals rather than as prose:

1.  Only a symbol clock justifies DIGITAL.  Spectral flatness, steep shoulders
    and constant occupied bandwidth are circumstantial; a cyclostationary line at
    ``alpha = R_s`` is the one positive, falsifiable signature.  The gate below
    therefore demands a symbol-rate estimate and a detection statistic, so a
    future detector cannot claim DIGITAL on a heuristic.

2.  ANALOGUE must not become the leftover bucket.  The squared-envelope cyclic
    test is blind to constant-envelope modulations, and that set spans both
    families -- GMSK/GFSK, P25 C4FM and DMR are digital, FM broadcast and NOAA
    weather are analogue.  If "not digital" were allowed to mean ANALOGUE, this
    system would label P25 as analogue voice.  ANALOGUE is therefore reserved and
    structurally unreachable until a positive detector exists.

The outcome vocabulary is a small stable family plus a reason code, which is the
shape ``docs/SparseSCYTHE.md`` recommends over an ever-growing flat enumeration:
the three counters stay compatible while the reason carries the detail.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Dict, Optional, Tuple


SCHEMA = "scythe.rf-signal-family.v1"
CONTRACT_PHASE = "0"

# Stable outcome vocabulary. These are the three counters the panel renders.
FAMILIES: Tuple[str, ...] = ("DIGITAL", "ANALOGUE", "UNCLASSIFIED")

# ANALOGUE is reserved: named, counted, and unreachable until Phase 4 ships a
# positive detector. A reserved family is not the same as an unsupported one.
CLAIMABLE_FAMILIES: Tuple[str, ...] = ("DIGITAL",)
RESERVED_FAMILIES: Tuple[str, ...] = ("ANALOGUE",)

CLASSIFIER_STATE = "NOT_IMPLEMENTED"
CLASSIFIER_STATE_NOTE = (
    "PHASE 0 SHIPS THE EVIDENCE CONTRACT ONLY. NO CHANNELIZER AND NO SYMBOL-CLOCK "
    "DETECTOR ARE RUNNING, SO EVERY RETAINED DETECTION IS UNCLASSIFIED BY "
    "CONSTRUCTION AND NOT BY MEASUREMENT."
)

ANALOGUE_DETECTOR = "NOT_IMPLEMENTED"
ANALOGUE_DETECTOR_NOTE = (
    "ANALOGUE REQUIRES A POSITIVE DETECTOR. IT IS NOT INFERRED FROM THE ABSENCE "
    "OF A SYMBOL CLOCK, BECAUSE CONSTANT-ENVELOPE DIGITAL MODES SUCH AS P25 C4FM "
    "AND DMR ARE INDISTINGUISHABLE FROM FM VOICE TO AN ENVELOPE-BASED TEST."
)

# The one authority a family inference may carry. A family is reasoned to from a
# measurement; it is never itself observed.
REQUIRED_AUTHORITY = "DERIVED_INFERENCE"

MAX_METHOD_LENGTH = 96

# Reason codes, ordered from "never ran" through to the single positive outcome.
REASON_CODES: Dict[str, str] = {
    "NOT_ATTEMPTED": "NO CLASSIFIER RAN OVER THIS DETECTION",
    "INSUFFICIENT_WINDOW": "FEWER SAMPLES THAN THE CONFIGURED CLASSIFICATION WINDOW",
    "CHANNELIZATION_FAILED": "OCCUPIED BANDWIDTH NOT ESTIMABLE AROUND THE PEAK AT THIS SNR",
    "NO_SYMBOL_CLOCK": "THE DETECTOR RAN AND FOUND NO SIGNIFICANT CYCLIC FEATURE",
    "CONSTANT_ENVELOPE": (
        "ENVELOPE VARIATION BELOW THE TEST FLOOR — THE KNOWN BLIND SPOT. "
        "DIGITAL AND ANALOGUE BOTH REMAIN POSSIBLE"
    ),
    "NOISE_COMPATIBLE": "CONSISTENT WITH NOISE ALONE",
    "STALE_WINDOW": "THE VERDICT WINDOW DOES NOT COVER THIS DETECTION",
    "ANALOGUE_DETECTOR_NOT_IMPLEMENTED": ANALOGUE_DETECTOR_NOTE,
    "UNQUALIFIED_CLAIM": "A FAMILY CLAIM ARRIVED WITHOUT THE EVIDENCE THE CONTRACT DEMANDS",
    "SYMBOL_CLOCK_DETECTED": "SIGNIFICANT CYCLIC FEATURE WITH AN ASSOCIATED SYMBOL-RATE ESTIMATE",
}

NULL_REASON_CODES: Tuple[str, ...] = (
    "NOT_ATTEMPTED", "INSUFFICIENT_WINDOW", "CHANNELIZATION_FAILED", "NO_SYMBOL_CLOCK",
    "CONSTANT_ENVELOPE", "NOISE_COMPATIBLE", "STALE_WINDOW",
    "ANALOGUE_DETECTOR_NOT_IMPLEMENTED", "UNQUALIFIED_CLAIM",
)

CLAIMS_WITHHELD: Tuple[str, ...] = (
    "analogue_family", "constant_envelope_digital", "modulation_order",
    "symbol_alignment", "emitter_identity", "content",
)

# A DIGITAL claim must arrive with all of these. The list is the contract a
# Phase 2 detector has to satisfy; it is not a description of anything running.
DIGITAL_EVIDENCE_REQUIRED: Tuple[str, ...] = (
    "authority", "method", "confidence", "symbol_rate_hz", "detection_statistic",
    "window_start", "window_end",
)


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


@dataclass(frozen=True)
class SignalFamilyVerdict:
    """What the store is allowed to record about one detection's family."""

    family: str = "UNCLASSIFIED"
    reason_code: str = "NOT_ATTEMPTED"
    authority: str = "UNCLASSIFIED"
    method: Optional[str] = None
    confidence: Optional[float] = None
    symbol_rate_hz: Optional[float] = None
    detection_statistic: Optional[float] = None
    window_start: Optional[float] = None
    window_end: Optional[float] = None
    refusals: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def classified(self) -> bool:
        return self.family in CLAIMABLE_FAMILIES

    def to_dict(self) -> Dict[str, Any]:
        return {
            "family": self.family,
            "reason_code": self.reason_code,
            "reason": REASON_CODES.get(self.reason_code, self.reason_code),
            "authority": self.authority,
            "method": self.method,
            "confidence": self.confidence,
            "symbol_rate_hz": self.symbol_rate_hz,
            "detection_statistic": self.detection_statistic,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "refusals": list(self.refusals),
        }


UNATTEMPTED = SignalFamilyVerdict()


def _refused(reason_code: str, refusals: Tuple[str, ...] = ()) -> SignalFamilyVerdict:
    return SignalFamilyVerdict(reason_code=reason_code, refusals=refusals)


def normalize_classification(claim: Any, *, observed_at: Optional[float] = None,
                             ) -> SignalFamilyVerdict:
    """Admit a family claim only with the evidence that makes it falsifiable.

    Anything short of the full contract is recorded as UNCLASSIFIED with the
    reason it was refused.  A refused claim is a result, not a dropped field:
    downstream renders the reason rather than a blank.
    """
    if claim is None:
        return UNATTEMPTED
    if not isinstance(claim, dict):
        return _refused("UNQUALIFIED_CLAIM", ("CLAIM IS NOT A MAPPING",))

    declared = str(claim.get("family") or "").strip().upper()
    if declared == "ANALOG":
        declared = "ANALOGUE"

    # A submitted reason code is honoured only when no family is being claimed.
    # A detector that ran and concluded nothing gets to say which nothing it found.
    if not declared or declared == "UNCLASSIFIED":
        submitted = str(claim.get("reason_code") or "").strip().upper()
        if submitted in NULL_REASON_CODES:
            return _refused(submitted)
        if submitted:
            return _refused("UNQUALIFIED_CLAIM", (f"UNKNOWN REASON CODE {submitted[:32]}",))
        return UNATTEMPTED

    if declared in RESERVED_FAMILIES:
        return _refused("ANALOGUE_DETECTOR_NOT_IMPLEMENTED", (ANALOGUE_DETECTOR_NOTE,))
    if declared not in CLAIMABLE_FAMILIES:
        return _refused("UNQUALIFIED_CLAIM", (f"FAMILY {declared[:32]} IS NOT IN THE VOCABULARY",))

    refusals: list[str] = []
    authority = str(claim.get("authority") or "").strip().upper()
    if authority != REQUIRED_AUTHORITY:
        refusals.append(
            f"AUTHORITY MUST BE {REQUIRED_AUTHORITY} — A FAMILY IS REASONED TO, NEVER OBSERVED")
    method = str(claim.get("method") or "").strip()
    if not method:
        refusals.append("METHOD IS REQUIRED — AN UNNAMED METHOD CANNOT BE AUDITED OR REPEATED")

    confidence = _finite(claim.get("confidence"))
    if confidence is None or not 0.0 <= confidence <= 1.0:
        refusals.append("CONFIDENCE MUST BE A FINITE NUMBER IN [0,1]")

    symbol_rate_hz = _finite(claim.get("symbol_rate_hz"))
    if symbol_rate_hz is None or symbol_rate_hz <= 0.0:
        refusals.append(
            "SYMBOL RATE IS REQUIRED — DIGITAL IS CLAIMABLE ONLY ON A DETECTED SYMBOL "
            "CLOCK, NEVER ON SPECTRAL SHAPE")

    detection_statistic = _finite(claim.get("detection_statistic"))
    if detection_statistic is None:
        refusals.append(
            "DETECTION STATISTIC IS REQUIRED — SIGNIFICANCE MUST BE A NUMBER THAT CAN "
            "BE THRESHOLDED AND REFUTED")

    window_start = _finite(claim.get("window_start"))
    window_end = _finite(claim.get("window_end"))
    if window_start is None or window_end is None or window_end <= window_start:
        refusals.append(
            "VERDICT WINDOW IS REQUIRED — A CLASSIFICATION COVERS AN INTERVAL WHILE A "
            "FRAME IS AN INSTANT")
    elif observed_at is not None:
        instant = _finite(observed_at)
        if instant is not None and not window_start <= instant <= window_end:
            return _refused("STALE_WINDOW", (
                "THE DETECTION FALLS OUTSIDE THE WINDOW THE CLASSIFIER ANALYSED",))

    if refusals:
        return _refused("UNQUALIFIED_CLAIM", tuple(refusals))

    return SignalFamilyVerdict(
        family=declared,
        # There is exactly one route to DIGITAL, so the reason is not the
        # submitter's to choose.
        reason_code="SYMBOL_CLOCK_DETECTED",
        authority=authority,
        method=method[:MAX_METHOD_LENGTH],
        confidence=round(confidence, 4),
        symbol_rate_hz=round(symbol_rate_hz, 4),
        detection_statistic=round(detection_statistic, 6),
        window_start=window_start,
        window_end=window_end,
    )


def empty_reason_counts() -> Dict[str, int]:
    return {code: 0 for code in REASON_CODES}


def classifier_status() -> Dict[str, Any]:
    """The declared-absence block published beside the three counters.

    Everything here is a statement about what this build does not do.  It is the
    whole point of Phase 0: a zero that explains itself is evidence, and a bare
    zero is an ambiguity between "nothing was there" and "nothing looked".
    """
    return {
        "schema": SCHEMA,
        "contract_phase": CONTRACT_PHASE,
        "state": CLASSIFIER_STATE,
        "state_note": CLASSIFIER_STATE_NOTE,
        "authority": REQUIRED_AUTHORITY,
        "families": list(FAMILIES),
        "claimable_families": list(CLAIMABLE_FAMILIES),
        "reserved_families": list(RESERVED_FAMILIES),
        "reason_codes": dict(REASON_CODES),
        "null_reason_codes": list(NULL_REASON_CODES),
        "digital_evidence_required": list(DIGITAL_EVIDENCE_REQUIRED),
        "analogue_detector": ANALOGUE_DETECTOR,
        "analogue_detector_note": ANALOGUE_DETECTOR_NOTE,
        "claims_withheld": list(CLAIMS_WITHHELD),
        "raw_iq_exposed": False,
        "iq_retention": "NONE_BEYOND_ONE_FFT_BLOCK",
    }
