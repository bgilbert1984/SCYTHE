"""Structural validation of a survey frame's own metadata. Nothing more.

Implements the metadata half of ``docs/RF_WALK_SURVEY_CONTRACT.md`` admission:
it establishes whether a frame's declared identity is present and well formed,
and produces ``MetadataAdmissionFacts``. It does **not** decide whether clocks
align, whether a receiver state is stale, or whether a surface may update.
Those belong to ``rf_receiver_state`` and to the stage after this one.

"Validated" is a narrow word here
---------------------------------
``ValidatedMetadata`` means **structurally and semantically well formed**. It
does not mean trustworthy, corroborated, or surface-eligible. A frame can carry
a perfectly formed signal-chain hash describing an instrument that was never
attached, and this module will call it validated, because the alternative --
inferring trust from syntax -- is the error the whole contract exists to
prevent. The limitation is published on the type so validation cannot be
mistaken for admission.

Raw IQ is a discriminated early exit
------------------------------------
``assess_frame`` returns one of two shapes:

    RAW_IQ_FRAME        a finished FRAME_REFUSED verdict, and no facts at all
    METADATA_ASSESSED   MetadataAdmissionFacts plus bounded validated metadata

Sample-bearing fields are looked for **first**, before unknown-field policy and
before any other validation, because reading further into a payload that
already violated the boundary is the thing not to do.

For a non-IQ frame, structural refusals do **not** end evaluation. The facts are
returned so alignment can run and ``BREADCRUMB_ONLY`` can carry every applicable
reason. Raw IQ is the only exclusive short circuit.

Malformed is not the same as refused
------------------------------------
An input lacking a contract §2 field, or carrying one in a structurally invalid
form, is **not a survey frame for the purposes of the contract** and is outside
the range of every disposition and reason code (§4, *The vocabulary's range*).
It is not refused: refusal is a verdict about a frame, and this never became
one. This module signals the class by raising ``FrameMetadataError``, which is
the implementation half of a category the contract names and leaves open.

Keeping the two apart keeps refusal counts clean. "How often are frames arriving
unaligned" and "how often is the producer emitting malformed payloads" are
different questions with different owners, and a store keyed by reason code must
not have to separate them after the fact.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Dict, Mapping, Optional, Tuple

from rf_walk_survey_admission import (
    EXCLUSIVE_REASON, FRAME_REFUSED, AdmissionVerdict, MetadataAdmissionFacts,
)


SCHEMA = "scythe.rf-walk-survey-metadata.v1"
CONTRACT = "docs/RF_WALK_SURVEY_CONTRACT.md"

RAW_IQ_FRAME = "RAW_IQ_FRAME"
METADATA_ASSESSED = "METADATA_ASSESSED"
ASSESSMENT_OUTCOMES: Tuple[str, ...] = (RAW_IQ_FRAME, METADATA_ASSESSED)

VALIDATION_MEANING = "STRUCTURALLY_AND_SEMANTICALLY_WELL_FORMED"
VALIDATION_LIMITATION = (
    "WELL FORMED IS NOT TRUSTWORTHY AND NOT SURFACE-ELIGIBLE. A FRAME MAY CARRY "
    "A PERFECTLY FORMED SIGNAL-CHAIN HASH DESCRIBING AN INSTRUMENT THAT WAS "
    "NEVER ATTACHED. VALIDATION IS SYNTAX AND RANGE; IT INFERS NO TRUST"
)
# The contract names the category; this maps it onto what the code raises.
ASSESSABILITY_NOTE = (
    "AN INPUT LACKING A CONTRACT SECTION 2 FIELD, OR CARRYING ONE IN A "
    "STRUCTURALLY INVALID FORM, IS NOT A SURVEY FRAME FOR THE PURPOSES OF THE "
    "CONTRACT AND IS OUTSIDE THE RANGE OF EVERY DISPOSITION AND REASON CODE "
    "(SECTION 4, 'THE VOCABULARY'S RANGE'). IT IS NOT REFUSED, BECAUSE REFUSAL "
    "IS A VERDICT ABOUT A FRAME. THIS IMPLEMENTATION SIGNALS THE CLASS BY "
    "RAISING FrameMetadataError"
)

# -- bounds ---------------------------------------------------------------

MAX_STRING = 256
MAX_EVIDENCE_REFS = 16
MAX_ACQUISITION_SPAN_NS = 3600 * 1_000_000_000      # an hour is already absurd
MIN_EXTENSION_MM, MAX_EXTENSION_MM = 10.0, 2000.0
MIN_GAIN_DB, MAX_GAIN_DB = -10.0, 60.0
MIN_SAMPLE_RATE_HZ, MAX_SAMPLE_RATE_HZ = 1.0, 20_000_000.0
MIN_FREQUENCY_HZ, MAX_FREQUENCY_HZ = 0.0, 300_000_000_000.0
MAX_UNCERTAINTY_DB = 100.0

POWER_UNITS: Tuple[str, ...] = ("DBFS", "DBM")
CALIBRATED_UNIT = "DBM"

# Fields the frame may carry. Anything else is refused: an allow-list is the
# only unknown-field policy that stays correct as the schema grows.
ALLOWED_FRAME_FIELDS = frozenset({
    "frame_id", "observer_id",
    "acquisition_start_monotonic_ns", "acquisition_end_monotonic_ns",
    "monotonic_source_id", "configuration_epoch",
    "power_unit", "calibration",
    "signal_chain_hash", "antenna_id", "feedline_id", "extension_mm",
    "gain_db", "sample_rate_hz",
    "receiver_state_chain_hash", "receiver_state_device_id",
    "sweep_plan_revision", "processing_revision",
    "evidence_refs",
})
ALLOWED_CALIBRATION_FIELDS = frozenset({
    "calibration_id", "calibration_revision", "frequency_range_hz",
    "gain_state", "antenna_id", "feedline_id", "extension_mm", "uncertainty_db",
})

# Frame identity: absence makes the frame unassessable, not refused.
REQUIRED_IDENTITY_FIELDS = ("frame_id", "observer_id", "monotonic_source_id",
                            "acquisition_start_monotonic_ns",
                            "acquisition_end_monotonic_ns", "configuration_epoch")

# Groups whose absence produces a reason code rather than an error.
SIGNAL_CHAIN_FIELDS = ("signal_chain_hash", "antenna_id", "feedline_id",
                       "extension_mm", "gain_db", "sample_rate_hz")
RECEIVER_STATE_FIELDS = ("receiver_state_chain_hash", "receiver_state_device_id")
PRODUCT_LINEAGE_FIELDS = ("sweep_plan_revision", "processing_revision")

# -- raw IQ detection -----------------------------------------------------
#
# Normalized so an alias cannot smuggle samples past a literal key match:
# "IQ-Data", "iq_data" and "iqData" all normalize to "iqdata".
_IQ_KEYS = frozenset({
    "iq", "rawiq", "iqdata", "iqsamples", "samples", "rawsamples",
    "baseband", "basebandsamples", "complexsamples", "isamples", "qsamples",
    "iqblob", "iqb64", "iqbase64", "iqbuffer", "iqwindow", "sampledata",
})
# evidence_refs may name an IQ buffer. A name is not a grant, and the contract
# says so explicitly; refs are values, never containers of samples.
_REF_FIELD = "evidence_refs"


def _normalize_key(key: Any) -> str:
    return "".join(ch for ch in str(key).lower() if ch.isalnum())


class FrameMetadataError(ValueError):
    """The payload is not an assessable survey frame."""


def find_sample_bearing_field(payload: Any, *, _path: str = "") -> Optional[str]:
    """First sample-bearing key anywhere in the payload, or None.

    Recursive, because a nested container is the obvious way to carry samples
    past a flat key check. ``evidence_refs`` is skipped: its values are names.
    """
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            path = f"{_path}.{key}" if _path else str(key)
            if _normalize_key(key) in _IQ_KEYS:
                return path
            if str(key) == _REF_FIELD:
                continue
            found = find_sample_bearing_field(value, _path=path)
            if found is not None:
                return found
    elif isinstance(payload, (list, tuple)):
        for index, item in enumerate(payload):
            found = find_sample_bearing_field(item, _path=f"{_path}[{index}]")
            if found is not None:
                return found
    return None


# -- scalar validators ----------------------------------------------------

def _string(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise FrameMetadataError(f"{name} must be a string")
    text = value.strip()
    if not text:
        raise FrameMetadataError(f"{name} must not be empty")
    if len(text) > MAX_STRING:
        raise FrameMetadataError(f"{name} exceeds {MAX_STRING} characters")
    return text


def _number(value: Any, name: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FrameMetadataError(f"{name} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise FrameMetadataError(f"{name} must be finite")
    if not low <= number <= high:
        raise FrameMetadataError(f"{name} must be between {low} and {high}")
    return number


def _integer(value: Any, name: str, low: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise FrameMetadataError(f"{name} must be an integer")
    if value < low:
        raise FrameMetadataError(f"{name} must be at least {low}")
    return value


def _present(payload: Mapping[str, Any], fields: Tuple[str, ...]) -> bool:
    """Every field present and not None. Absence of any one unbinds the group."""
    return all(payload.get(name) is not None for name in fields)


# -- validated metadata ---------------------------------------------------

@dataclass(frozen=True)
class ValidatedMetadata:
    """Bounded, well-formed frame metadata. See VALIDATION_LIMITATION."""

    frame_id: str
    observer_id: str
    monotonic_source_id: str
    acquisition_start_monotonic_ns: int
    acquisition_end_monotonic_ns: int
    configuration_epoch: int
    power_unit: Optional[str]
    signal_chain_hash: Optional[str]
    receiver_state_chain_hash: Optional[str]
    sweep_plan_revision: Optional[str]
    processing_revision: Optional[str]
    evidence_ref_count: int

    @property
    def acquisition_span_ns(self) -> int:
        return self.acquisition_end_monotonic_ns - self.acquisition_start_monotonic_ns

    def as_dict(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA,
            "validation": VALIDATION_MEANING,
            "validation_limitation": VALIDATION_LIMITATION,
            "frame_id": self.frame_id,
            "observer_id": self.observer_id,
            "monotonic_source_id": self.monotonic_source_id,
            "acquisition_start_monotonic_ns": self.acquisition_start_monotonic_ns,
            "acquisition_end_monotonic_ns": self.acquisition_end_monotonic_ns,
            "acquisition_span_ns": self.acquisition_span_ns,
            "configuration_epoch": self.configuration_epoch,
            "power_unit": self.power_unit,
            "signal_chain_hash": self.signal_chain_hash,
            "receiver_state_chain_hash": self.receiver_state_chain_hash,
            "sweep_plan_revision": self.sweep_plan_revision,
            "processing_revision": self.processing_revision,
            "evidence_ref_count": self.evidence_ref_count,
            "surface_eligible": None,
            "surface_eligibility_note": (
                "NOT DECIDED HERE. ELIGIBILITY REQUIRES ALIGNMENT FACTS AND THE "
                "ADMISSION VERDICT, NEITHER OF WHICH THIS STAGE PRODUCES"),
        }


@dataclass(frozen=True)
class FrameMetadataAssessment:
    """Discriminated: either a finished refusal, or facts plus metadata."""

    outcome: str
    verdict: Optional[AdmissionVerdict] = None
    facts: Optional[MetadataAdmissionFacts] = None
    metadata: Optional[ValidatedMetadata] = None
    detail: Optional[str] = None

    def __post_init__(self) -> None:
        if self.outcome not in ASSESSMENT_OUTCOMES:
            raise FrameMetadataError(
                f"unknown assessment outcome {str(self.outcome)[:48]!r}")
        if self.outcome == RAW_IQ_FRAME:
            if self.verdict is None or self.facts is not None or self.metadata is not None:
                raise FrameMetadataError(
                    "RAW_IQ_FRAME carries a finished verdict and nothing else; a "
                    "refused frame produced no facts because none were assessed")
            if self.verdict.disposition != FRAME_REFUSED:
                raise FrameMetadataError("RAW_IQ_FRAME must carry FRAME_REFUSED")
        else:
            if self.facts is None or self.metadata is None or self.verdict is not None:
                raise FrameMetadataError(
                    "METADATA_ASSESSED carries facts and metadata and no verdict; "
                    "a verdict here would pre-empt alignment")

    def as_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"schema": SCHEMA, "outcome": self.outcome,
                                   "detail": self.detail}
        if self.outcome == RAW_IQ_FRAME:
            payload["verdict"] = self.verdict.as_dict()
        else:
            payload["metadata"] = self.metadata.as_dict()
            payload["facts"] = {
                "signal_chain_unbound": self.facts.signal_chain_unbound,
                "receiver_state_unbound": self.facts.receiver_state_unbound,
                "product_lineage_unbound": self.facts.product_lineage_unbound,
                "power_unit_unsupported": self.facts.power_unit_unsupported,
            }
            payload["alignment"] = "NOT_RUN_HERE"
            payload["alignment_note"] = (
                "THESE FACTS ARE HALF OF AN ADMISSION. AdmissionFacts.from_stages "
                "REQUIRES ALIGNMENT FACTS FROM rf_receiver_state BEFORE A VERDICT "
                "CAN BE DECIDED")
        return payload


# -- the assessment -------------------------------------------------------

def _power_unit_unsupported(payload: Mapping[str, Any]) -> bool:
    """Unrecognised unit, or DBM without a complete calibration identity."""
    unit = payload.get("power_unit")
    if unit is None or not isinstance(unit, str) or unit.upper() not in POWER_UNITS:
        return True
    if unit.upper() != CALIBRATED_UNIT:
        return False
    calibration = payload.get("calibration")
    if not isinstance(calibration, Mapping):
        return True
    unknown = set(calibration) - ALLOWED_CALIBRATION_FIELDS
    if unknown:
        raise FrameMetadataError(
            f"unknown calibration fields: {', '.join(sorted(unknown))[:120]}")
    if not _present(calibration, tuple(sorted(ALLOWED_CALIBRATION_FIELDS))):
        return True
    # Shape the pieces so a malformed calibration cannot pass as a complete one.
    _string(calibration["calibration_id"], "calibration.calibration_id")
    _string(calibration["calibration_revision"], "calibration.calibration_revision")
    _string(calibration["gain_state"], "calibration.gain_state")
    _string(calibration["antenna_id"], "calibration.antenna_id")
    _string(calibration["feedline_id"], "calibration.feedline_id")
    _number(calibration["extension_mm"], "calibration.extension_mm",
            MIN_EXTENSION_MM, MAX_EXTENSION_MM)
    _number(calibration["uncertainty_db"], "calibration.uncertainty_db",
            0.0, MAX_UNCERTAINTY_DB)
    span = calibration["frequency_range_hz"]
    if (not isinstance(span, (list, tuple)) or len(span) != 2):
        raise FrameMetadataError(
            "calibration.frequency_range_hz must be a two-element range")
    low = _number(span[0], "calibration.frequency_range_hz[0]",
                  MIN_FREQUENCY_HZ, MAX_FREQUENCY_HZ)
    high = _number(span[1], "calibration.frequency_range_hz[1]",
                   MIN_FREQUENCY_HZ, MAX_FREQUENCY_HZ)
    if not low < high:
        raise FrameMetadataError(
            "calibration.frequency_range_hz must be ordered low to high")
    return False


def assess_frame(payload: Any) -> FrameMetadataAssessment:
    """Assess one frame's metadata. Pure: no clock, no I/O, no state.

    Raw IQ is looked for before anything else and ends evaluation. Every other
    structural failure is recorded as a fact and evaluation continues, so
    alignment can run and the verdict can carry every applicable reason.
    """
    if not isinstance(payload, Mapping):
        raise FrameMetadataError("a survey frame must be a mapping")

    # First, and before reading anything else out of the payload.
    sample_field = find_sample_bearing_field(payload)
    if sample_field is not None:
        return FrameMetadataAssessment(
            RAW_IQ_FRAME,
            verdict=AdmissionVerdict(FRAME_REFUSED, (EXCLUSIVE_REASON,)),
            detail=f"sample-bearing field at {sample_field[:MAX_STRING]}")

    unknown = set(payload) - ALLOWED_FRAME_FIELDS
    if unknown:
        raise FrameMetadataError(
            f"unknown frame fields: {', '.join(sorted(unknown))[:120]}")

    missing = [name for name in REQUIRED_IDENTITY_FIELDS if payload.get(name) is None]
    if missing:
        raise FrameMetadataError(
            f"not a survey frame; missing {', '.join(missing)}. "
            f"{ASSESSABILITY_NOTE}")

    start = _integer(payload["acquisition_start_monotonic_ns"],
                     "acquisition_start_monotonic_ns", 0)
    end = _integer(payload["acquisition_end_monotonic_ns"],
                   "acquisition_end_monotonic_ns", 0)
    if end <= start:
        raise FrameMetadataError(
            "acquisition bounds must be ordered; a sweep takes time and a frame "
            "whose end does not follow its start describes no acquisition")
    if end - start > MAX_ACQUISITION_SPAN_NS:
        raise FrameMetadataError("acquisition span exceeds the bound")

    refs = payload.get("evidence_refs") or ()
    if not isinstance(refs, (list, tuple)):
        raise FrameMetadataError("evidence_refs must be a list")
    if len(refs) > MAX_EVIDENCE_REFS:
        raise FrameMetadataError(f"evidence_refs exceeds {MAX_EVIDENCE_REFS} entries")
    for index, ref in enumerate(refs):
        _string(ref, f"evidence_refs[{index}]")

    # Optional groups: shape-checked when present, absent means unbound.
    for name, low, high in (("extension_mm", MIN_EXTENSION_MM, MAX_EXTENSION_MM),
                            ("gain_db", MIN_GAIN_DB, MAX_GAIN_DB),
                            ("sample_rate_hz", MIN_SAMPLE_RATE_HZ, MAX_SAMPLE_RATE_HZ)):
        if payload.get(name) is not None:
            _number(payload[name], name, low, high)
    for name in ("signal_chain_hash", "antenna_id", "feedline_id",
                 "receiver_state_chain_hash", "receiver_state_device_id",
                 "sweep_plan_revision", "processing_revision"):
        if payload.get(name) is not None:
            _string(payload[name], name)

    facts = MetadataAdmissionFacts(
        signal_chain_unbound=not _present(payload, SIGNAL_CHAIN_FIELDS),
        receiver_state_unbound=not _present(payload, RECEIVER_STATE_FIELDS),
        product_lineage_unbound=not _present(payload, PRODUCT_LINEAGE_FIELDS),
        power_unit_unsupported=_power_unit_unsupported(payload),
    )
    unit = payload.get("power_unit")
    metadata = ValidatedMetadata(
        frame_id=_string(payload["frame_id"], "frame_id"),
        observer_id=_string(payload["observer_id"], "observer_id"),
        monotonic_source_id=_string(payload["monotonic_source_id"],
                                    "monotonic_source_id"),
        acquisition_start_monotonic_ns=start,
        acquisition_end_monotonic_ns=end,
        configuration_epoch=_integer(payload["configuration_epoch"],
                                     "configuration_epoch", 0),
        power_unit=unit.upper() if isinstance(unit, str) and not facts.power_unit_unsupported else None,
        signal_chain_hash=payload.get("signal_chain_hash"),
        receiver_state_chain_hash=payload.get("receiver_state_chain_hash"),
        sweep_plan_revision=payload.get("sweep_plan_revision"),
        processing_revision=payload.get("processing_revision"),
        evidence_ref_count=len(refs),
    )
    return FrameMetadataAssessment(METADATA_ASSESSED, facts=facts, metadata=metadata)


def metadata_status() -> Dict[str, Any]:
    """The validator's own boundaries, published rather than discovered."""
    return {
        "schema": SCHEMA,
        "contract": CONTRACT,
        "outcomes": list(ASSESSMENT_OUTCOMES),
        "validation": VALIDATION_MEANING,
        "validation_limitation": VALIDATION_LIMITATION,
        "assessability_note": ASSESSABILITY_NOTE,
        "allowed_frame_fields": sorted(ALLOWED_FRAME_FIELDS),
        "unknown_field_policy": "REJECT_WHOLE_FRAME",
        "power_units": list(POWER_UNITS),
        "calibrated_unit": CALIBRATED_UNIT,
        "required_calibration_fields": sorted(ALLOWED_CALIBRATION_FIELDS),
        "decides_time_alignment": False,
        "decides_receiver_state_staleness": False,
        "decides_surface_eligibility": False,
        "side_effects": "NONE",
        "authority_note": (
            "ALIGNMENT FACTS COME FROM rf_receiver_state. THIS STAGE PRODUCES "
            "HALF AN ADMISSION AND CANNOT COMPLETE ONE"),
    }
