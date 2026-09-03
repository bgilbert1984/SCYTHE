"""Phase 0 evidence contract for RF signal characterisation.

No detector ships in this module and none is imported by it.  What ships is the
vocabulary, the admission gate and the declared absences, so that the counters on
the spectrum panel state a reason instead of a bare zero.

Three axes, not one label
-------------------------
The first cut of this contract published a single DIGITAL / ANALOGUE / UNCLASSIFIED
label.  That one field was doing four jobs at once -- what the carrier is doing,
whether the information is symbol-structured, which protocol it might be, and a
one-word summary for the panel -- and the jobs have different evidence
requirements and different detectors.  They are now separate:

    modulation             UNRESOLVED, AM_LIKE, FM_LIKE, FSK_LIKE, PSK_LIKE, QAM_LIKE
    information_structure  NOT_ATTEMPTED, NO_SYMBOL_CLOCK_DETECTED,
                           SYMBOL_CLOCK_LIKE_FEATURE
    protocol               UNRESOLVED, CANDIDATE, CONFIRMED_BY_DECODER

The split is not tidiness.  It dissolves the constant-envelope problem that made
the single label unsafe.  P25 C4FM is ``FM_LIKE`` on the modulation axis and
carries a symbol clock; FM broadcast voice is ``FM_LIKE`` and does not.  Under one
label those two facts collide and the system has to choose a wrong answer.  Under
two axes they are simply two different rows, and neither axis has to lie.

DIGITAL and ANALOGUE survive **only as derived compatibility summaries** for the
existing counters.  They are not observations, they are not claimable, and
``derive_family`` is the only thing that produces them.

Four findings are encoded here as refusals rather than as prose
--------------------------------------------------------------
1.  Only a symbol clock justifies DIGITAL.  Spectral flatness, steep shoulders
    and constant occupied bandwidth are circumstantial; a cyclostationary line at
    ``alpha = R_s`` is the one positive, falsifiable signature.

2.  ANALOGUE must not become the leftover bucket.  The squared-envelope cyclic
    test is blind to constant-envelope modulations, and that set spans both
    families -- GMSK/GFSK, P25 C4FM and DMR are digital, FM broadcast and NOAA
    weather are analogue.  ``NO_SYMBOL_CLOCK_DETECTED`` from an envelope-based
    method therefore does **not** summarise to ANALOGUE, even alongside
    ``FM_LIKE``.  See ``derive_family``.

3.  A field being present is not a claim being true.  An early cut of this gate
    demanded that a ``detection_statistic`` exist and stopped there, which let
    ``{"detection_statistic": -999, "confidence": 0.99}`` through as DIGITAL.
    Evidence-shaped fields are not evidence.  An axis claim must name a
    **registered** method and be shown to have passed that method's own decision
    rule -- threshold, direction, false-alarm bound, null model, sample count,
    pinned revision and calibration.

4.  A summary is not an observation.  A caller may not assert ``family`` at all;
    the field is refused with ``FAMILY_NOT_DIRECTLY_CLAIMABLE`` so that no path
    exists to write DIGITAL without the axis evidence that derives it.

Consequence of (3): no method has cleared Phase 3 validation, so no method in the
registry is VALIDATED, so **live DIGITAL is unreachable in this build** -- not by
convention but by the gate.  "Validate false-digital behaviour before enabling
any live DIGITAL result" is enforced here rather than merely documented.

Consequence of the axis split: ``modulation`` and ``protocol`` have no detector
and no decoder respectively, so both are structurally pinned at ``UNRESOLVED``.
``CONFIRMED_BY_DECODER`` in particular is unreachable without decoder evidence,
which is the entire point of naming it that way.

The outcome vocabulary is a small stable set per axis plus a reason code, which is
the shape ``docs/SparseSCYTHE.md`` recommends over an ever-growing flat
enumeration: the counters stay compatible while the reason carries the detail.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from typing import Any, Dict, Mapping, Optional, Tuple


SCHEMA = "scythe.rf-signal-family.v2"
CONTRACT_PHASE = "0"

# --------------------------------------------------------------------------
# The three axes
# --------------------------------------------------------------------------

AXES: Tuple[str, ...] = ("modulation", "information_structure", "protocol")

# What the carrier is doing. Requires a modulation classifier; none exists.
MODULATION_VALUES: Tuple[str, ...] = (
    "UNRESOLVED", "AM_LIKE", "FM_LIKE", "FSK_LIKE", "PSK_LIKE", "QAM_LIKE",
)

# Whether the information is symbol-structured. The one axis Phase 2 will reach.
INFORMATION_STRUCTURE_VALUES: Tuple[str, ...] = (
    "NOT_ATTEMPTED", "NO_SYMBOL_CLOCK_DETECTED", "SYMBOL_CLOCK_LIKE_FEATURE",
)

# Which protocol, if any. CONFIRMED_BY_DECODER means a decoder produced frames.
PROTOCOL_VALUES: Tuple[str, ...] = ("UNRESOLVED", "CANDIDATE", "CONFIRMED_BY_DECODER")

AXIS_VOCABULARY: Dict[str, Tuple[str, ...]] = {
    "modulation": MODULATION_VALUES,
    "information_structure": INFORMATION_STRUCTURE_VALUES,
    "protocol": PROTOCOL_VALUES,
}

# The value each axis holds when nothing has established otherwise. Every one of
# these is a declared absence, not a measurement.
AXIS_DEFAULTS: Dict[str, str] = {
    "modulation": "UNRESOLVED",
    "information_structure": "NOT_ATTEMPTED",
    "protocol": "UNRESOLVED",
}

# Only one axis has any reachable non-default value in this build, and only via
# the registry. The other two are pinned by the absence of their detectors.
CLAIMABLE_INFORMATION_STRUCTURE: Tuple[str, ...] = (
    "NO_SYMBOL_CLOCK_DETECTED", "SYMBOL_CLOCK_LIKE_FEATURE",
)
# A negative result claims no structure, so it is honoured without the registry.
# A positive one must pass a registered, validated decision rule.
GATED_INFORMATION_STRUCTURE: Tuple[str, ...] = ("SYMBOL_CLOCK_LIKE_FEATURE",)

MODULATION_DETECTOR = "NOT_IMPLEMENTED"
MODULATION_DETECTOR_NOTE = (
    "NO MODULATION CLASSIFIER RUNS. AM_LIKE, FM_LIKE, FSK_LIKE, PSK_LIKE AND "
    "QAM_LIKE REQUIRE A POSITIVE DETECTOR OVER AN ISOLATED CHANNEL; NEITHER THE "
    "CHANNELIZER NOR THAT DETECTOR EXISTS, SO THIS AXIS IS PINNED AT UNRESOLVED."
)

PROTOCOL_HYPOTHESIS = "NOT_IMPLEMENTED"
PROTOCOL_HYPOTHESIS_NOTE = (
    "NOTHING PRODUCES PROTOCOL CANDIDATES. A BAND-PLAN COINCIDENCE IS NOT A "
    "PROTOCOL HYPOTHESIS, AND A CANDIDATE WITH NO NAMED SOURCE IS A GUESS "
    "WEARING A FIELD NAME."
)

PROTOCOL_DECODER = "NOT_IMPLEMENTED"
PROTOCOL_DECODER_NOTE = (
    "CONFIRMED_BY_DECODER REQUIRES DECODER EVIDENCE — FRAMES DEMODULATED, "
    "SYNCHRONISED AND CHECKED. NO DECODER RUNS, SO THIS VALUE IS UNREACHABLE. "
    "IT IS NAMED FOR THE EVIDENCE IT DEMANDS PRECISELY SO IT CANNOT BE REACHED "
    "BY INFERENCE."
)

# --------------------------------------------------------------------------
# The derived compatibility summary
# --------------------------------------------------------------------------

# Retained for the three panel counters. Not an axis, not an observation.
FAMILIES: Tuple[str, ...] = ("DIGITAL", "ANALOGUE", "UNCLASSIFIED")
FAMILY_AUTHORITY = "DERIVED_SUMMARY"
FAMILY_SUMMARY_NOTE = (
    "DIGITAL AND ANALOGUE ARE COMPATIBILITY SUMMARIES DERIVED FROM THE AXES. "
    "THEY ARE NOT PRIMARY OBSERVATIONS AND CANNOT BE SUBMITTED. READ THE AXES "
    "FOR WHAT WAS ACTUALLY DETERMINED."
)

ANALOGUE_DETECTOR = "NOT_IMPLEMENTED"
ANALOGUE_DETECTOR_NOTE = (
    "ANALOGUE REQUIRES A POSITIVE DETECTOR. IT IS NOT INFERRED FROM THE ABSENCE "
    "OF A SYMBOL CLOCK, BECAUSE CONSTANT-ENVELOPE DIGITAL MODES SUCH AS P25 C4FM "
    "AND DMR ARE INDISTINGUISHABLE FROM FM VOICE TO AN ENVELOPE-BASED TEST."
)

CLASSIFIER_STATE = "NOT_IMPLEMENTED"
CLASSIFIER_STATE_NOTE = (
    "PHASE 0 SHIPS THE EVIDENCE CONTRACT ONLY. NO CHANNELIZER AND NO SYMBOL-CLOCK "
    "DETECTOR ARE RUNNING, SO EVERY RETAINED DETECTION IS UNCLASSIFIED BY "
    "CONSTRUCTION AND NOT BY MEASUREMENT."
)

# The one authority an axis inference may carry. An axis value is reasoned to
# from a measurement; it is never itself observed.
REQUIRED_AUTHORITY = "DERIVED_INFERENCE"

MAX_METHOD_LENGTH = 96

VALIDATION_STATUSES: Tuple[str, ...] = ("VALIDATED", "REGISTERED_NOT_VALIDATED")
STATISTIC_DIRECTIONS: Tuple[str, ...] = ("GREATER_IS_STRONGER", "LESS_IS_STRONGER")

# A window hash must be an algorithm-qualified digest of the declared length. A
# bare non-empty string is not a hash: it cannot be recomputed, so it cannot tie
# a verdict to the samples that produced it. Shape only at this phase -- see
# docs/RF_Signal_Family_Classifier_Scope.md 5.4 on hash ownership.
DIGEST_LENGTHS: Dict[str, int] = {"sha256": 64, "sha384": 96, "sha512": 128, "blake2s": 64}
_DIGEST_PATTERN = re.compile(r"^([a-z0-9][a-z0-9_-]{1,15}):([0-9a-f]+)$")

# Where an axis claim may come from. Classifications are computed inside the
# bridge process beside the IQ; they are not ingestible over HTTP, and
# graphops_rf_ingest.ALLOWED_FIELDS deliberately excludes the field. A statistic
# and its false-alarm probability must be produced by the registered detector,
# never accepted from a caller that merely asserts them.
CLASSIFICATION_TRUST = "BRIDGE_LOCAL_DETECTOR_ONLY"
CLASSIFICATION_TRUST_NOTE = (
    "AN AXIS CLAIM IS COMPUTED IN THE BRIDGE PROCESS BESIDE THE IQ. IT IS NOT "
    "ACCEPTED OVER THE OBSERVATION INGEST API, WHERE signal_classification IS AN "
    "UNKNOWN FIELD AND THE FRAME IS REJECTED. A DETECTION STATISTIC AND ITS "
    "FALSE-ALARM PROBABILITY MUST BE MEASURED BY THE REGISTERED DETECTOR, NEVER "
    "ASSERTED BY A CALLER."
)


def derive_family(modulation: str, information_structure: str) -> str:
    """Summarise the axes into the legacy three-value counter.

    Exactly one rule fires.  A symbol-clock-like feature that cleared a
    registered, validated decision rule summarises to DIGITAL; everything else
    summarises to UNCLASSIFIED.

    ANALOGUE is deliberately not derivable, and the tempting rule is the reason
    this function exists rather than being an inline expression::

        FM_LIKE + NO_SYMBOL_CLOCK_DETECTED  ->  ANALOGUE     # WRONG

    P25 C4FM is ``FM_LIKE`` and carries a symbol clock that a squared-envelope
    test cannot see, so it reports ``NO_SYMBOL_CLOCK_DETECTED`` for a reason that
    has nothing to do with being analogue.  That rule would label encrypted
    public-safety digital voice as analogue voice.  ANALOGUE stays unreachable
    until a positive analogue detector exists to assert it directly.
    """
    if information_structure == "SYMBOL_CLOCK_LIKE_FEATURE":
        return "DIGITAL"
    return "UNCLASSIFIED"


@dataclass(frozen=True)
class RegisteredMethod:
    """A detector the gate is willing to hear an axis claim from.

    The registry -- not the submitter -- owns the decision rule.  A detector may
    report a statistic; it may not decide what counts as significant, which
    direction is stronger, or how much false alarm is tolerable.
    """

    method_id: str
    method_revision: str
    axis: str
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
            "axis": self.axis,
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
#
# Note the `axis` field: a method is registered against one axis. A symbol-clock
# detector has no standing to assert a modulation, and the gate enforces that.
METHOD_REGISTRY: Dict[str, RegisteredMethod] = {
    "squared-envelope-cyclic.v1": RegisteredMethod(
        method_id="squared-envelope-cyclic.v1",
        method_revision="UNPINNED_PENDING_IMPLEMENTATION",
        axis="information_structure",
        statistic_direction="GREATER_IS_STRONGER",
        minimum_statistic=8.4,
        maximum_false_alarm_probability=0.001,
        null_model="CHANNELIZED_NOISE_PLUS_NONCYCLIC_SIGNAL",
        minimum_sample_count=262_144,
        validation_status="REGISTERED_NOT_VALIDATED",
        validation_note=(
            "PHASE 3 HAS NOT RUN. NO LABELLED CORPUS HAS MEASURED THIS METHOD'S "
            "FALSE-DIGITAL RATE ON NOISE OR ON ANALOGUE INPUTS, AND ITS CONFIDENCE "
            "IS UNCALIBRATED. A POSITIVE VERDICT FROM IT IS REFUSED."
        ),
        calibration_revision=None,
    ),
}

# Reason codes, ordered from "never ran" through to the single positive outcome.
REASON_CODES: Dict[str, str] = {
    "NOT_ATTEMPTED": "NO CLASSIFIER RAN OVER THIS DETECTION",
    "INSUFFICIENT_WINDOW": "FEWER SAMPLES THAN THE CONFIGURED CLASSIFICATION WINDOW",
    "CHANNELIZATION_FAILED": "OCCUPIED BANDWIDTH NOT ESTIMABLE AROUND THE PEAK AT THIS SNR",
    "NO_SYMBOL_CLOCK_DETECTED": "THE DETECTOR RAN AND FOUND NO SIGNIFICANT CYCLIC FEATURE",
    "CONSTANT_ENVELOPE": (
        "ENVELOPE VARIATION BELOW THE TEST FLOOR — THE KNOWN BLIND SPOT. "
        "DIGITAL AND ANALOGUE BOTH REMAIN POSSIBLE"
    ),
    "NOISE_COMPATIBLE": "CONSISTENT WITH NOISE ALONE",
    "STALE_WINDOW": "THE VERDICT WINDOW DOES NOT COVER THIS DETECTION",
    "FAMILY_NOT_DIRECTLY_CLAIMABLE": (
        "DIGITAL AND ANALOGUE ARE DERIVED SUMMARIES, NOT OBSERVATIONS. SUBMIT AN "
        "AXIS VALUE WITH ITS EVIDENCE; THE SUMMARY FOLLOWS FROM IT"
    ),
    "ANALOGUE_DETECTOR_NOT_IMPLEMENTED": ANALOGUE_DETECTOR_NOTE,
    "MODULATION_DETECTOR_NOT_IMPLEMENTED": MODULATION_DETECTOR_NOTE,
    "PROTOCOL_HYPOTHESIS_NOT_IMPLEMENTED": PROTOCOL_HYPOTHESIS_NOTE,
    "DECODER_NOT_IMPLEMENTED": PROTOCOL_DECODER_NOTE,
    "METHOD_NOT_REGISTERED": (
        "THE CLAIMED METHOD IS NOT IN THE REGISTRY. AN ARBITRARY METHOD STRING "
        "CANNOT CROSS THE GATE, BECAUSE AN UNREGISTERED METHOD HAS NO DECISION RULE"
    ),
    "METHOD_NOT_VALIDATED": (
        "THE METHOD IS REGISTERED BUT HAS NOT PASSED PHASE 3 VALIDATION. ITS "
        "FALSE-DIGITAL RATE IS UNMEASURED AND ITS CONFIDENCE IS UNCALIBRATED"
    ),
    "METHOD_WRONG_AXIS": (
        "THE METHOD IS REGISTERED AGAINST A DIFFERENT AXIS. A SYMBOL-CLOCK "
        "DETECTOR HAS NO STANDING TO ASSERT A MODULATION, AND THE REVERSE"
    ),
    "DECISION_RULE_NOT_MET": (
        "THE STATISTIC DID NOT PASS THE METHOD'S REGISTERED DECISION RULE. A "
        "NUMBER BEING PRESENT IS NOT THE NUMBER BEING SIGNIFICANT"
    ),
    "UNQUALIFIED_CLAIM": "AN AXIS CLAIM ARRIVED WITHOUT THE EVIDENCE THE CONTRACT DEMANDS",
    "SYMBOL_CLOCK_LIKE_FEATURE": (
        "A SYMBOL-CLOCK-LIKE CYCLIC FEATURE PASSED A REGISTERED, VALIDATED "
        "DECISION RULE — DIGITAL STRUCTURE SUPPORTED, NOT PROVEN"
    ),
}

NULL_REASON_CODES: Tuple[str, ...] = (
    "NOT_ATTEMPTED", "INSUFFICIENT_WINDOW", "CHANNELIZATION_FAILED",
    "NO_SYMBOL_CLOCK_DETECTED", "CONSTANT_ENVELOPE", "NOISE_COMPATIBLE",
    "STALE_WINDOW", "FAMILY_NOT_DIRECTLY_CLAIMABLE",
    "ANALOGUE_DETECTOR_NOT_IMPLEMENTED", "MODULATION_DETECTOR_NOT_IMPLEMENTED",
    "PROTOCOL_HYPOTHESIS_NOT_IMPLEMENTED", "DECODER_NOT_IMPLEMENTED",
    "METHOD_NOT_REGISTERED", "METHOD_NOT_VALIDATED", "METHOD_WRONG_AXIS",
    "DECISION_RULE_NOT_MET", "UNQUALIFIED_CLAIM",
)

POSITIVE_REASON_CODE = "SYMBOL_CLOCK_LIKE_FEATURE"

CLAIMS_WITHHELD: Tuple[str, ...] = (
    "analogue_family", "constant_envelope_digital", "modulation_family",
    "modulation_order", "protocol_identity", "symbol_alignment",
    "emitter_identity", "content",
)

# The contract a Phase 2 detector has to satisfy for a positive axis value. Not a
# description of anything running: nothing runs. Structural fields, then the rule.
AXIS_EVIDENCE_REQUIRED: Tuple[str, ...] = (
    "authority", "method", "method_revision", "confidence", "symbol_rate_hz",
    "detection_statistic", "decision_threshold", "statistic_direction",
    "estimated_false_alarm_probability", "null_model", "sample_count",
    "source_window_hash", "calibration_revision", "window_start", "window_end",
)
# Retained under the pre-split name for the panel, which reads it verbatim.
DIGITAL_EVIDENCE_REQUIRED: Tuple[str, ...] = AXIS_EVIDENCE_REQUIRED


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _structure_for_reason(reason_code: str) -> str:
    """Which information-structure value a null outcome leaves behind.

    Only an actual negative result from a detector that ran sets
    ``NO_SYMBOL_CLOCK_DETECTED``.  Everything else -- including
    ``CONSTANT_ENVELOPE``, where the detector ran and hit its known blind spot --
    leaves ``NOT_ATTEMPTED``.  Recording a blind spot as a negative is how a
    constant-envelope digital signal would quietly acquire evidence of being
    analogue.
    """
    return reason_code if reason_code == "NO_SYMBOL_CLOCK_DETECTED" else "NOT_ATTEMPTED"


@dataclass(frozen=True)
class SignalCharacterVerdict:
    """What the store is allowed to record about one detection's character."""

    modulation: str = "UNRESOLVED"
    information_structure: str = "NOT_ATTEMPTED"
    protocol: str = "UNRESOLVED"
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
    def family(self) -> str:
        """The legacy counter value. Derived, never stored as an input."""
        return derive_family(self.modulation, self.information_structure)

    @property
    def classified(self) -> bool:
        return self.family != "UNCLASSIFIED"

    def axes(self) -> Dict[str, str]:
        return {
            "modulation": self.modulation,
            "information_structure": self.information_structure,
            "protocol": self.protocol,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "axes": self.axes(),
            "family": self.family,
            "family_authority": FAMILY_AUTHORITY,
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


UNATTEMPTED = SignalCharacterVerdict()


def _refused(reason_code: str, refusals: Tuple[str, ...] = ()) -> SignalCharacterVerdict:
    """A refusal is a result: the axes hold their declared-absence values."""
    return SignalCharacterVerdict(
        information_structure=_structure_for_reason(reason_code),
        reason_code=reason_code,
        refusals=refusals,
    )


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

    failures.extend(_check_window_hash(claim.get("source_window_hash")))

    return failures


def _check_window_hash(value: Any) -> list[str]:
    """A verdict must be traceable to the samples that produced it.

    Shape only, at this phase. There is no window record to bind against until
    Phase 1 owns an IQ ring; when there is, this should also confirm the digest
    matches a window the bridge actually retained. See scope 5.4.
    """
    text = _text(value)
    if not text:
        return ["SOURCE WINDOW HASH IS REQUIRED — A VERDICT MUST BE TRACEABLE TO THE "
                "SAMPLES THAT PRODUCED IT"]
    match = _DIGEST_PATTERN.match(text)
    if match is None:
        return ["SOURCE WINDOW HASH MUST BE AN ALGORITHM-QUALIFIED LOWERCASE HEX "
                "DIGEST, AS IN sha256:<hex> — A BARE STRING CANNOT BE RECOMPUTED"]
    algorithm, digest = match.group(1), match.group(2)
    expected = DIGEST_LENGTHS.get(algorithm)
    if expected is None:
        return [f"SOURCE WINDOW HASH ALGORITHM {algorithm} IS NOT RECOGNISED — "
                f"EXPECTED ONE OF {sorted(DIGEST_LENGTHS)}"]
    if len(digest) != expected:
        return [f"SOURCE WINDOW HASH FOR {algorithm} MUST BE {expected} HEX "
                f"CHARACTERS, RECEIVED {len(digest)}"]
    return []


def _check_unreachable_axes(claim: Mapping[str, Any]) -> Optional[SignalCharacterVerdict]:
    """Refuse the two axes that have no detector, before anything else is read.

    These refusals come first because they are about capability, not evidence.
    A perfectly documented modulation claim is still refused, and telling the
    submitter "your evidence was incomplete" would be a lie about the reason.
    """
    modulation = _text(claim.get("modulation")).upper()
    if modulation and modulation != "UNRESOLVED":
        if modulation not in MODULATION_VALUES:
            return _refused("UNQUALIFIED_CLAIM",
                            (f"MODULATION {modulation[:32]} IS NOT IN THE VOCABULARY",))
        return _refused("MODULATION_DETECTOR_NOT_IMPLEMENTED", (MODULATION_DETECTOR_NOTE,))

    protocol = _text(claim.get("protocol")).upper()
    if protocol and protocol != "UNRESOLVED":
        if protocol not in PROTOCOL_VALUES:
            return _refused("UNQUALIFIED_CLAIM",
                            (f"PROTOCOL {protocol[:32]} IS NOT IN THE VOCABULARY",))
        if protocol == "CONFIRMED_BY_DECODER":
            return _refused("DECODER_NOT_IMPLEMENTED", (PROTOCOL_DECODER_NOTE,))
        return _refused("PROTOCOL_HYPOTHESIS_NOT_IMPLEMENTED", (PROTOCOL_HYPOTHESIS_NOTE,))

    return None


def normalize_classification(claim: Any, *, observed_at: Optional[float] = None,
                             registry: Optional[Mapping[str, RegisteredMethod]] = None,
                             ) -> SignalCharacterVerdict:
    """Admit an axis claim only with the evidence that makes it falsifiable.

    Anything short of the full contract is recorded with the reason it was
    refused.  A refused claim is a result, not a dropped field: downstream
    renders the reason rather than a blank.
    """
    methods = METHOD_REGISTRY if registry is None else registry
    if claim is None:
        return UNATTEMPTED
    if not isinstance(claim, Mapping):
        return _refused("UNQUALIFIED_CLAIM", ("CLAIM IS NOT A MAPPING",))

    # A summary may not be submitted as an observation. ANALOGUE gets the more
    # specific refusal because "no detector exists" is the more useful answer.
    declared_family = _text(claim.get("family")).upper()
    if declared_family in ("ANALOG", "ANALOGUE"):
        return _refused("ANALOGUE_DETECTOR_NOT_IMPLEMENTED", (ANALOGUE_DETECTOR_NOTE,))
    if declared_family and declared_family != "UNCLASSIFIED":
        return _refused("FAMILY_NOT_DIRECTLY_CLAIMABLE", (
            f"FAMILY {declared_family[:32]} WAS SUBMITTED DIRECTLY. "
            + FAMILY_SUMMARY_NOTE,))

    unreachable = _check_unreachable_axes(claim)
    if unreachable is not None:
        return unreachable

    structure = _text(claim.get("information_structure")).upper()

    # No positive axis value is being claimed. A detector that ran and concluded
    # nothing gets to say which nothing it found.
    if not structure or structure == "NOT_ATTEMPTED":
        submitted = _text(claim.get("reason_code")).upper()
        if submitted in NULL_REASON_CODES:
            return _refused(submitted)
        if submitted:
            return _refused("UNQUALIFIED_CLAIM", (f"UNKNOWN REASON CODE {submitted[:32]}",))
        return UNATTEMPTED

    if structure not in INFORMATION_STRUCTURE_VALUES:
        return _refused("UNQUALIFIED_CLAIM", (
            f"INFORMATION STRUCTURE {structure[:32]} IS NOT IN THE VOCABULARY",))

    # A negative result asserts no structure, so it carries no evidence burden.
    if structure not in GATED_INFORMATION_STRUCTURE:
        return _refused(structure)

    # --- structural evidence -------------------------------------------------
    refusals: list[str] = []
    authority = _text(claim.get("authority")).upper()
    if authority != REQUIRED_AUTHORITY:
        refusals.append(
            f"AUTHORITY MUST BE {REQUIRED_AUTHORITY} — AN AXIS VALUE IS REASONED TO, "
            f"NEVER OBSERVED")

    method = _text(claim.get("method"))
    if not method:
        refusals.append("METHOD IS REQUIRED — AN UNNAMED METHOD CANNOT BE AUDITED OR REPEATED")

    confidence = _finite(claim.get("confidence"))
    if confidence is None or not 0.0 <= confidence <= 1.0:
        refusals.append("CONFIDENCE MUST BE A FINITE NUMBER IN [0,1]")

    symbol_rate_hz = _finite(claim.get("symbol_rate_hz"))
    if symbol_rate_hz is None or symbol_rate_hz <= 0.0:
        refusals.append(
            "SYMBOL RATE IS REQUIRED — SYMBOL_CLOCK_LIKE_FEATURE IS CLAIMABLE ONLY ON "
            "A DETECTED SYMBOL CLOCK, NEVER ON SPECTRAL SHAPE")

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
    if entry.axis != "information_structure":
        return _refused("METHOD_WRONG_AXIS", (
            f"METHOD {method[:MAX_METHOD_LENGTH]} IS REGISTERED AGAINST "
            f"{entry.axis}, NOT information_structure",))
    if not entry.validated:
        return _refused("METHOD_NOT_VALIDATED", (entry.validation_note,))

    failures = _check_decision_rule(claim, entry)
    if failures:
        return _refused("DECISION_RULE_NOT_MET", tuple(failures))

    return SignalCharacterVerdict(
        # The modulation and protocol axes stay at their declared absences. A
        # symbol clock says the information is symbol-structured; it says nothing
        # about what the carrier is doing or whose protocol it is.
        modulation="UNRESOLVED",
        information_structure=structure,
        protocol="UNRESOLVED",
        # There is exactly one route to a positive information-structure value,
        # so the reason is not the submitter's to choose. It records support,
        # not proof.
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


def empty_axis_counts() -> Dict[str, Dict[str, int]]:
    """Per-axis counters, every declared value present at zero.

    Absent keys and zero counts are different statements, and the panel has to be
    able to tell them apart.
    """
    return {axis: {value: 0 for value in values} for axis, values in AXIS_VOCABULARY.items()}


def validated_methods(registry: Optional[Mapping[str, RegisteredMethod]] = None) -> list[str]:
    methods = METHOD_REGISTRY if registry is None else registry
    return sorted(name for name, entry in methods.items() if entry.validated)


def classifier_status(registry: Optional[Mapping[str, RegisteredMethod]] = None) -> Dict[str, Any]:
    """The declared-absence block published beside the counters.

    Everything here is a statement about what this build does not do.  It is the
    whole point of Phase 0: a zero that explains itself is evidence, and a bare
    zero is an ambiguity between "nothing was there" and "nothing looked".
    """
    methods = METHOD_REGISTRY if registry is None else registry
    validated = validated_methods(methods)
    structure_reachable = sorted(
        name for name, entry in methods.items()
        if entry.validated and entry.axis == "information_structure")
    return {
        "schema": SCHEMA,
        "contract_phase": CONTRACT_PHASE,
        "state": CLASSIFIER_STATE,
        "state_note": CLASSIFIER_STATE_NOTE,
        "authority": REQUIRED_AUTHORITY,
        "axes": {
            "modulation": {
                "values": list(MODULATION_VALUES),
                "default": AXIS_DEFAULTS["modulation"],
                "detector": MODULATION_DETECTOR,
                "detector_note": MODULATION_DETECTOR_NOTE,
                "claimable": [],
            },
            "information_structure": {
                "values": list(INFORMATION_STRUCTURE_VALUES),
                "default": AXIS_DEFAULTS["information_structure"],
                "detector": CLASSIFIER_STATE,
                "detector_note": CLASSIFIER_STATE_NOTE,
                "claimable": list(CLAIMABLE_INFORMATION_STRUCTURE),
                "gated": list(GATED_INFORMATION_STRUCTURE),
                "reachable": structure_reachable,
            },
            "protocol": {
                "values": list(PROTOCOL_VALUES),
                "default": AXIS_DEFAULTS["protocol"],
                "hypothesis_source": PROTOCOL_HYPOTHESIS,
                "hypothesis_note": PROTOCOL_HYPOTHESIS_NOTE,
                "decoder": PROTOCOL_DECODER,
                "decoder_note": PROTOCOL_DECODER_NOTE,
                "claimable": [],
            },
        },
        "family_summary": {
            "values": list(FAMILIES),
            "authority": FAMILY_AUTHORITY,
            "note": FAMILY_SUMMARY_NOTE,
            "derived_from": ["modulation", "information_structure"],
            "analogue_derivable": False,
            "analogue_blocked_note": ANALOGUE_DETECTOR_NOTE,
        },
        # Retained flat for the existing panel bindings.
        "families": list(FAMILIES),
        "claimable_families": [],
        "reserved_families": ["ANALOGUE"],
        "reason_codes": dict(REASON_CODES),
        "null_reason_codes": list(NULL_REASON_CODES),
        "axis_evidence_required": list(AXIS_EVIDENCE_REQUIRED),
        "digital_evidence_required": list(AXIS_EVIDENCE_REQUIRED),
        "registered_methods": [entry.to_dict() for _, entry in sorted(methods.items())],
        "validated_methods": validated,
        # With no validated method on the information-structure axis there is no
        # route to a DIGITAL summary. This is the gate's own state, not a
        # description of current band activity.
        "digital_reachable": bool(structure_reachable),
        "digital_reachable_note": (
            "A DIGITAL SUMMARY REQUIRES A SYMBOL-CLOCK-LIKE FEATURE FROM A REGISTERED "
            "METHOD THAT HAS PASSED PHASE 3 VALIDATION. NONE HAS. LIVE DIGITAL IS "
            "UNREACHABLE IN THIS BUILD."
        ) if not structure_reachable else None,
        "classification_trust": CLASSIFICATION_TRUST,
        "classification_trust_note": CLASSIFICATION_TRUST_NOTE,
        "window_hash_algorithms": sorted(DIGEST_LENGTHS),
        "analogue_detector": ANALOGUE_DETECTOR,
        "analogue_detector_note": ANALOGUE_DETECTOR_NOTE,
        "modulation_detector": MODULATION_DETECTOR,
        "modulation_detector_note": MODULATION_DETECTOR_NOTE,
        "protocol_decoder": PROTOCOL_DECODER,
        "protocol_decoder_note": PROTOCOL_DECODER_NOTE,
        "claims_withheld": list(CLAIMS_WITHHELD),
        "raw_iq_exposed": False,
        "iq_retention": "NONE_BEYOND_ONE_FFT_BLOCK",
    }
