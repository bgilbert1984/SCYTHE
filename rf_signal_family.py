"""Phase 0 evidence contract for RF signal-family classification.

No detector ships in this module and none is imported by it.  What ships is the
vocabulary, the admission gate and the declared absences, so that the counters on
the spectrum panel state a reason instead of a bare zero.

Three findings are encoded here as refusals rather than as prose:

1.  Only a symbol clock justifies DIGITAL.  Spectral flatness, steep shoulders
    and constant occupied bandwidth are circumstantial; a cyclostationary line at
    ``alpha = R_s`` is the one positive, falsifiable signature.

2.  ANALOGUE must not become the leftover bucket.  The squared-envelope cyclic
    test is blind to constant-envelope modulations, and that set spans both
    families -- GMSK/GFSK, P25 C4FM and DMR are digital, FM broadcast and NOAA
    weather are analogue.  If "not digital" were allowed to mean ANALOGUE, this
    system would label P25 as analogue voice.  ANALOGUE is therefore reserved and
    structurally unreachable until a positive detector exists.

3.  A field being present is not a claim being true.  The first cut of this gate
    demanded that a ``detection_statistic`` exist and stopped there, which let
    ``{"detection_statistic": -999, "confidence": 0.99}`` through as DIGITAL.
    Evidence-shaped fields are not evidence.  A family claim must now name a
    **registered** method and be shown to have passed that method's own decision
    rule -- threshold, direction, false-alarm bound, null model, sample count,
    pinned revision and calibration.

Consequence of (3): no method has cleared Phase 3 validation, so no method in the
registry is VALIDATED, so **live DIGITAL is unreachable in this build** -- not by
convention but by the gate.  "Validate false-digital behaviour before enabling
any live DIGITAL result" is enforced here rather than merely documented.

The outcome vocabulary is a small stable family plus a reason code, which is the
shape ``docs/SparseSCYTHE.md`` recommends over an ever-growing flat enumeration:
the three counters stay compatible while the reason carries the detail.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Dict, Mapping, Optional, Tuple


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

VALIDATION_STATUSES: Tuple[str, ...] = ("VALIDATED", "REGISTERED_NOT_VALIDATED")
STATISTIC_DIRECTIONS: Tuple[str, ...] = ("GREATER_IS_STRONGER", "LESS_IS_STRONGER")


@dataclass(frozen=True)
class RegisteredMethod:
    """A detector the gate is willing to hear a family claim from.

    The registry -- not the submitter -- owns the decision rule.  A detector may
    report a statistic; it may not decide what counts as significant, which
    direction is stronger, or how much false alarm is tolerable.
    """

    method_id: str
    method_revision: str
    statistic_direction: str
    minimum_statistic: float
    maximum_false_alarm_probability: float
    null_model: str
    minimum_sample_count: int
    validation_status: str
    validation_note: str
    calibration_revision: Optional[str] = None

    @property
    def validated(self) -> bool:
        return self.validation_status == "VALIDATED"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "method_id": self.method_id,
            "method_revision": self.method_revision,
            "statistic_direction": self.statistic_direction,
            "minimum_statistic": self.minimum_statistic,
            "maximum_false_alarm_probability": self.maximum_false_alarm_probability,
            "null_model": self.null_model,
            "minimum_sample_count": self.minimum_sample_count,
            "validation_status": self.validation_status,
            "validation_note": self.validation_note,
            "calibration_revision": self.calibration_revision,
        }


# The Phase 2 detector, registered ahead of existing so its decision rule is
# fixed before anyone writes code that would like the rule to be looser. It is
# deliberately NOT validated: Phase 3 has not run, no corpus has measured its
# false-DIGITAL rate, and no reliability diagram has calibrated its confidence.
METHOD_REGISTRY: Dict[str, RegisteredMethod] = {
    "squared-envelope-cyclic.v1": RegisteredMethod(
        method_id="squared-envelope-cyclic.v1",
        method_revision="UNPINNED_PENDING_IMPLEMENTATION",
        statistic_direction="GREATER_IS_STRONGER",
        minimum_statistic=8.4,
        maximum_false_alarm_probability=0.001,
        null_model="CHANNELIZED_NOISE_PLUS_NONCYCLIC_SIGNAL",
        minimum_sample_count=262_144,
        validation_status="REGISTERED_NOT_VALIDATED",
        validation_note=(
            "PHASE 3 HAS NOT RUN. NO LABELLED CORPUS HAS MEASURED THIS METHOD'S "
            "FALSE-DIGITAL RATE ON NOISE OR ON ANALOGUE INPUTS, AND ITS CONFIDENCE "
            "IS UNCALIBRATED. A DIGITAL VERDICT FROM IT IS REFUSED."
        ),
        calibration_revision=None,
    ),
}

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
    "METHOD_NOT_REGISTERED": (
        "THE CLAIMED METHOD IS NOT IN THE REGISTRY. AN ARBITRARY METHOD STRING "
        "CANNOT CROSS THE GATE, BECAUSE AN UNREGISTERED METHOD HAS NO DECISION RULE"
    ),
    "METHOD_NOT_VALIDATED": (
        "THE METHOD IS REGISTERED BUT HAS NOT PASSED PHASE 3 VALIDATION. ITS "
        "FALSE-DIGITAL RATE IS UNMEASURED AND ITS CONFIDENCE IS UNCALIBRATED"
    ),
    "DECISION_RULE_NOT_MET": (
        "THE STATISTIC DID NOT PASS THE METHOD'S REGISTERED DECISION RULE. A "
        "NUMBER BEING PRESENT IS NOT THE NUMBER BEING SIGNIFICANT"
    ),
    "UNQUALIFIED_CLAIM": "A FAMILY CLAIM ARRIVED WITHOUT THE EVIDENCE THE CONTRACT DEMANDS",
    "SYMBOL_CLOCK_LIKE_FEATURE": (
        "A SYMBOL-CLOCK-LIKE CYCLIC FEATURE PASSED A REGISTERED, VALIDATED "
        "DECISION RULE — DIGITAL STRUCTURE SUPPORTED, NOT PROVEN"
    ),
}

NULL_REASON_CODES: Tuple[str, ...] = (
    "NOT_ATTEMPTED", "INSUFFICIENT_WINDOW", "CHANNELIZATION_FAILED", "NO_SYMBOL_CLOCK",
    "CONSTANT_ENVELOPE", "NOISE_COMPATIBLE", "STALE_WINDOW",
    "ANALOGUE_DETECTOR_NOT_IMPLEMENTED", "METHOD_NOT_REGISTERED", "METHOD_NOT_VALIDATED",
    "DECISION_RULE_NOT_MET", "UNQUALIFIED_CLAIM",
)

POSITIVE_REASON_CODE = "SYMBOL_CLOCK_LIKE_FEATURE"

CLAIMS_WITHHELD: Tuple[str, ...] = (
    "analogue_family", "constant_envelope_digital", "modulation_order",
    "symbol_alignment", "emitter_identity", "content",
)

# The contract a Phase 2 detector has to satisfy. Not a description of anything
# running: nothing runs. Structural fields first, then the decision rule.
DIGITAL_EVIDENCE_REQUIRED: Tuple[str, ...] = (
    "authority", "method", "method_revision", "confidence", "symbol_rate_hz",
    "detection_statistic", "decision_threshold", "statistic_direction",
    "estimated_false_alarm_probability", "null_model", "sample_count",
    "source_window_hash", "calibration_revision", "window_start", "window_end",
)


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _text(value: Any) -> str:
    return str(value or "").strip()


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
    decision_threshold: Optional[float] = None
    false_alarm_probability: Optional[float] = None
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
            "decision_threshold": self.decision_threshold,
            "false_alarm_probability": self.false_alarm_probability,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "refusals": list(self.refusals),
        }


UNATTEMPTED = SignalFamilyVerdict()


def _refused(reason_code: str, refusals: Tuple[str, ...] = ()) -> SignalFamilyVerdict:
    return SignalFamilyVerdict(reason_code=reason_code, refusals=refusals)


def _check_decision_rule(claim: Mapping[str, Any], entry: RegisteredMethod) -> list[str]:
    """Did the claim pass the *registry's* rule, not the submitter's own?"""
    failures: list[str] = []

    direction = _text(claim.get("statistic_direction")).upper()
    if direction != entry.statistic_direction:
        failures.append(
            f"STATISTIC DIRECTION MUST BE {entry.statistic_direction} FOR "
            f"{entry.method_id} — A SUBMITTER MAY NOT REVERSE THE SENSE OF THE TEST")
        direction = entry.statistic_direction

    statistic = _finite(claim.get("detection_statistic"))
    threshold = _finite(claim.get("decision_threshold"))
    if statistic is None:
        failures.append("DETECTION STATISTIC MUST BE A FINITE NUMBER")
    if threshold is None:
        failures.append("DECISION THRESHOLD MUST BE A FINITE NUMBER")

    if statistic is not None and threshold is not None:
        if direction == "GREATER_IS_STRONGER":
            # The registry floor cannot be lowered by declaring a softer threshold.
            if threshold < entry.minimum_statistic:
                failures.append(
                    f"DECLARED THRESHOLD {threshold} IS BELOW THE REGISTERED MINIMUM "
                    f"{entry.minimum_statistic} — THE BAR IS NOT THE SUBMITTER'S TO LOWER")
            effective = max(threshold, entry.minimum_statistic)
            if statistic < effective:
                failures.append(
                    f"STATISTIC {statistic} DID NOT REACH {effective} "
                    f"({entry.statistic_direction})")
        else:
            if threshold > entry.minimum_statistic:
                failures.append(
                    f"DECLARED THRESHOLD {threshold} IS ABOVE THE REGISTERED MAXIMUM "
                    f"{entry.minimum_statistic} — THE BAR IS NOT THE SUBMITTER'S TO LOWER")
            effective = min(threshold, entry.minimum_statistic)
            if statistic > effective:
                failures.append(
                    f"STATISTIC {statistic} DID NOT REACH {effective} "
                    f"({entry.statistic_direction})")

    pfa = _finite(claim.get("estimated_false_alarm_probability"))
    if pfa is None or not 0.0 <= pfa <= 1.0:
        failures.append("ESTIMATED FALSE-ALARM PROBABILITY MUST BE A FINITE NUMBER IN [0,1]")
    elif pfa > entry.maximum_false_alarm_probability:
        failures.append(
            f"FALSE-ALARM PROBABILITY {pfa} EXCEEDS THE REGISTERED MAXIMUM "
            f"{entry.maximum_false_alarm_probability}")

    null_model = _text(claim.get("null_model")).upper()
    if null_model != entry.null_model:
        failures.append(
            f"NULL MODEL MUST BE {entry.null_model} — A STATISTIC IS ONLY SIGNIFICANT "
            f"RELATIVE TO THE NULL IT WAS MEASURED AGAINST")

    sample_count = _finite(claim.get("sample_count"))
    if sample_count is None or sample_count < entry.minimum_sample_count:
        failures.append(
            f"SAMPLE COUNT MUST BE AT LEAST {entry.minimum_sample_count} — A SHORT "
            f"WINDOW CANNOT RESOLVE A SYMBOL CLOCK")

    revision = _text(claim.get("method_revision"))
    if revision != entry.method_revision:
        failures.append(
            "METHOD REVISION DOES NOT MATCH THE REGISTERED REVISION — A SILENTLY "
            "CHANGED DETECTOR MAY NOT REUSE AN EARLIER REGISTRATION")

    calibration = _text(claim.get("calibration_revision")) or None
    if entry.calibration_revision is None:
        failures.append(
            "NO CALIBRATION REVISION IS REGISTERED FOR THIS METHOD — AN UNCALIBRATED "
            "CONFIDENCE IS DECORATIVE")
    elif calibration != entry.calibration_revision:
        failures.append(
            f"CALIBRATION REVISION MUST BE {entry.calibration_revision}")

    if not _text(claim.get("source_window_hash")):
        failures.append(
            "SOURCE WINDOW HASH IS REQUIRED — A VERDICT MUST BE TRACEABLE TO THE "
            "SAMPLES THAT PRODUCED IT")

    return failures


def normalize_classification(claim: Any, *, observed_at: Optional[float] = None,
                             registry: Optional[Mapping[str, RegisteredMethod]] = None,
                             ) -> SignalFamilyVerdict:
    """Admit a family claim only with the evidence that makes it falsifiable.

    Anything short of the full contract is recorded as UNCLASSIFIED with the
    reason it was refused.  A refused claim is a result, not a dropped field:
    downstream renders the reason rather than a blank.
    """
    methods = METHOD_REGISTRY if registry is None else registry
    if claim is None:
        return UNATTEMPTED
    if not isinstance(claim, Mapping):
        return _refused("UNQUALIFIED_CLAIM", ("CLAIM IS NOT A MAPPING",))

    declared = _text(claim.get("family")).upper()
    if declared == "ANALOG":
        declared = "ANALOGUE"

    # A submitted reason code is honoured only when no family is being claimed.
    # A detector that ran and concluded nothing gets to say which nothing it found.
    if not declared or declared == "UNCLASSIFIED":
        submitted = _text(claim.get("reason_code")).upper()
        if submitted in NULL_REASON_CODES:
            return _refused(submitted)
        if submitted:
            return _refused("UNQUALIFIED_CLAIM", (f"UNKNOWN REASON CODE {submitted[:32]}",))
        return UNATTEMPTED

    if declared in RESERVED_FAMILIES:
        return _refused("ANALOGUE_DETECTOR_NOT_IMPLEMENTED", (ANALOGUE_DETECTOR_NOTE,))
    if declared not in CLAIMABLE_FAMILIES:
        return _refused("UNQUALIFIED_CLAIM", (f"FAMILY {declared[:32]} IS NOT IN THE VOCABULARY",))

    # --- structural evidence -------------------------------------------------
    refusals: list[str] = []
    authority = _text(claim.get("authority")).upper()
    if authority != REQUIRED_AUTHORITY:
        refusals.append(
            f"AUTHORITY MUST BE {REQUIRED_AUTHORITY} — A FAMILY IS REASONED TO, NEVER OBSERVED")

    method = _text(claim.get("method"))
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

    window_start = _finite(claim.get("window_start"))
    window_end = _finite(claim.get("window_end"))
    if window_start is None or window_end is None or window_end <= window_start:
        refusals.append(
            "VERDICT WINDOW IS REQUIRED — A CLASSIFICATION COVERS AN INTERVAL WHILE A "
            "FRAME IS AN INSTANT")

    if refusals:
        return _refused("UNQUALIFIED_CLAIM", tuple(refusals))

    if observed_at is not None:
        instant = _finite(observed_at)
        if instant is not None and not window_start <= instant <= window_end:
            return _refused("STALE_WINDOW", (
                "THE DETECTION FALLS OUTSIDE THE WINDOW THE CLASSIFIER ANALYSED",))

    # --- the decision rule ---------------------------------------------------
    entry = methods.get(method)
    if entry is None:
        return _refused("METHOD_NOT_REGISTERED", (
            f"METHOD {method[:MAX_METHOD_LENGTH]} IS NOT REGISTERED",))
    if not entry.validated:
        return _refused("METHOD_NOT_VALIDATED", (entry.validation_note,))

    failures = _check_decision_rule(claim, entry)
    if failures:
        return _refused("DECISION_RULE_NOT_MET", tuple(failures))

    return SignalFamilyVerdict(
        family=declared,
        # There is exactly one route to DIGITAL, so the reason is not the
        # submitter's to choose. It records support, not proof.
        reason_code=POSITIVE_REASON_CODE,
        authority=authority,
        method=method[:MAX_METHOD_LENGTH],
        confidence=round(confidence, 4),
        symbol_rate_hz=round(symbol_rate_hz, 4),
        detection_statistic=round(_finite(claim.get("detection_statistic")), 6),
        decision_threshold=round(_finite(claim.get("decision_threshold")), 6),
        false_alarm_probability=_finite(claim.get("estimated_false_alarm_probability")),
        window_start=window_start,
        window_end=window_end,
    )


def empty_reason_counts() -> Dict[str, int]:
    return {code: 0 for code in REASON_CODES}


def validated_methods(registry: Optional[Mapping[str, RegisteredMethod]] = None) -> list[str]:
    methods = METHOD_REGISTRY if registry is None else registry
    return sorted(name for name, entry in methods.items() if entry.validated)


def classifier_status(registry: Optional[Mapping[str, RegisteredMethod]] = None) -> Dict[str, Any]:
    """The declared-absence block published beside the three counters.

    Everything here is a statement about what this build does not do.  It is the
    whole point of Phase 0: a zero that explains itself is evidence, and a bare
    zero is an ambiguity between "nothing was there" and "nothing looked".
    """
    methods = METHOD_REGISTRY if registry is None else registry
    validated = validated_methods(methods)
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
        "registered_methods": [entry.to_dict() for _, entry in sorted(methods.items())],
        "validated_methods": validated,
        # With no validated method there is no route to a DIGITAL verdict. This
        # is the gate's own state, not a description of current band activity.
        "digital_reachable": bool(validated),
        "digital_reachable_note": (
            "A DIGITAL VERDICT REQUIRES A REGISTERED METHOD THAT HAS PASSED PHASE 3 "
            "VALIDATION. NONE HAS. LIVE DIGITAL IS UNREACHABLE IN THIS BUILD."
        ) if not validated else None,
        "analogue_detector": ANALOGUE_DETECTOR,
        "analogue_detector_note": ANALOGUE_DETECTOR_NOTE,
        "claims_withheld": list(CLAIMS_WITHHELD),
        "raw_iq_exposed": False,
        "iq_retention": "NONE_BEYOND_ONE_FFT_BLOCK",
    }
