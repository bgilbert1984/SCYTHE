"""The walking-survey admission verdict, and nothing else.

Implements §4 of ``docs/RF_WALK_SURVEY_CONTRACT.md``: exactly one disposition
plus zero or more reason codes from a closed vocabulary. It is the smallest
executable piece of that contract and the piece every later stage must route
through.

What this module is
-------------------
A pure representation of a decision that has already been made elsewhere. It
consumes *already-derived admission facts* -- booleans -- and produces the
two-level verdict the contract specifies. Nothing here decides whether a fact is
true; it decides what a frame's fate is given the facts.

What this module does not do, by construction
---------------------------------------------
It does not parse RF arrays, read a sample, perform time alignment, compute an
H3 index, update a surface, touch a filesystem or a network, or mutate anything
anywhere. A test asserts the source contains no import or call that could.

It also does not recreate ``ALIGNMENT_CAPABILITIES``. Whether a join is
VERIFIED, BOUNDED, UNVERIFIED or STALE -- and what each of those permits -- is
owned by ``rf_receiver_state.py``, which is implemented and tested. Integration
obtains alignment facts from there and hands this module the booleans that
follow. A second capability table here would be a competing authority, and the
two would drift.

The two levels
--------------
The disposition says what happens to the frame, and a frame has one fate. The
reason codes say why, and there may be several: a frame can be missing its
signal chain *and* its sweep-plan revision, and an operator who repairs only the
first has not repaired the frame. An earlier draft of the contract required
exactly one outcome while also requiring both to be reported -- a contradiction
an implementation could satisfy in either direction while claiming compliance.
The split is what makes compliance checkable.

There is no ``UNKNOWN`` reason and no fallback. A fact the vocabulary cannot
express is refused at the boundary rather than admitted under a generic name;
a catch-all reason is how a closed vocabulary stops being closed.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Dict, Iterable, Tuple


SCHEMA = "scythe.rf-walk-survey-admission.v1"
CONTRACT = "docs/RF_WALK_SURVEY_CONTRACT.md"
CONTRACT_SECTION = "4"

# Exactly one of these per frame.
SURFACE_ELIGIBLE = "SURFACE_ELIGIBLE"
BREADCRUMB_ONLY = "BREADCRUMB_ONLY"
FRAME_REFUSED = "FRAME_REFUSED"
DISPOSITIONS: Tuple[str, ...] = (SURFACE_ELIGIBLE, BREADCRUMB_ONLY, FRAME_REFUSED)

# The closed reason vocabulary, in FROZEN CONTRACT ORDER.
#
# This tuple is the serialization order. Reasons are never emitted in the order
# they were discovered: discovery order is an artefact of how the caller
# happened to evaluate its facts, and a verdict whose field order depends on
# that is a verdict two correct implementations would disagree about while both
# being right.
TIME_ALIGNMENT_UNVERIFIED = "TIME_ALIGNMENT_UNVERIFIED"
RECEIVER_STATE_STALE = "RECEIVER_STATE_STALE"
SIGNAL_CHAIN_UNBOUND = "SIGNAL_CHAIN_UNBOUND"
RECEIVER_STATE_UNBOUND = "RECEIVER_STATE_UNBOUND"
PRODUCT_LINEAGE_UNBOUND = "PRODUCT_LINEAGE_UNBOUND"
POWER_UNIT_UNSUPPORTED = "POWER_UNIT_UNSUPPORTED"
RAW_IQ_PRESENT = "RAW_IQ_PRESENT"

REASON_CODES: Tuple[str, ...] = (
    TIME_ALIGNMENT_UNVERIFIED,
    RECEIVER_STATE_STALE,
    SIGNAL_CHAIN_UNBOUND,
    RECEIVER_STATE_UNBOUND,
    PRODUCT_LINEAGE_UNBOUND,
    POWER_UNIT_UNSUPPORTED,
    RAW_IQ_PRESENT,
)
_REASON_RANK: Dict[str, int] = {code: index for index, code in enumerate(REASON_CODES)}

# Raw IQ is the only reason that refuses a frame, and it refuses alone.
EXCLUSIVE_REASON = RAW_IQ_PRESENT
# The six that produce BREADCRUMB_ONLY, accumulating rather than short-circuiting.
ORDINARY_REASONS: Tuple[str, ...] = tuple(
    code for code in REASON_CODES if code != EXCLUSIVE_REASON)

REASON_NOTES: Dict[str, str] = {
    TIME_ALIGNMENT_UNVERIFIED: (
        "NOTHING JOINED THE OBSERVATION TO A RECEIVER STATE. A SURFACE BUILT ON "
        "THIS WOULD ASSERT A CONTEMPORANEITY NOBODY MEASURED"),
    RECEIVER_STATE_STALE: (
        "THE RECEIVER STATE IS TOO OLD FOR THIS OBSERVATION AT THIS SPEED. "
        "STALENESS IS METRES OF POSSIBLE MOVEMENT, NOT SECONDS"),
    SIGNAL_CHAIN_UNBOUND: (
        "THE SIGNAL-CHAIN HASH OR ITS REQUIRED EXPLANATORY FIELDS ARE MISSING. "
        "REPAIRED BY THE RF APPARATUS DECLARATION"),
    RECEIVER_STATE_UNBOUND: (
        "THE RECEIVER-STATE CHAIN HASH OR POSITIONING IDENTITY IS MISSING. "
        "REPAIRED BY THE POSITIONING APPARATUS DECLARATION"),
    PRODUCT_LINEAGE_UNBOUND: (
        "THE SWEEP-PLAN OR PROCESSING REVISION IS MISSING. REPAIRED BY THE "
        "SURVEY CONFIGURATION"),
    POWER_UNIT_UNSUPPORTED: (
        "DBM WITHOUT A QUALIFYING CALIBRATION, OR AN UNKNOWN UNIT. A MOVED DBFS "
        "NUMBER IS NOT A CALIBRATED ONE"),
    RAW_IQ_PRESENT: (
        "THE FRAME CARRIED SAMPLES. THE FRAME IS REJECTED WHOLE AND NO "
        "BREADCRUMB IS DERIVED FROM IT"),
}

# What each disposition permits. Derived from the disposition, never stored
# alongside it, so the two cannot disagree.
_CAPABILITIES: Dict[str, Dict[str, bool]] = {
    SURFACE_ELIGIBLE: {"breadcrumb_retained": True, "surface_contribution": True},
    BREADCRUMB_ONLY: {"breadcrumb_retained": True, "surface_contribution": False},
    FRAME_REFUSED: {"breadcrumb_retained": False, "surface_contribution": False},
}

# Scoping note carried on every FRAME_REFUSED verdict, because this is the
# boundary a reader is most likely to over-read.
REFUSAL_SCOPE_NOTE = (
    "THE REFUSAL IS SCOPED TO THIS FRAME. A RECEIVER-STATE OBSERVATION RECEIVED "
    "INDEPENDENTLY THROUGH ITS OWN VALID INGESTION PATH IS NEITHER DELETED NOR "
    "INVALIDATED BY IT"
)


class UnknownDisposition(ValueError):
    """Refused, not coerced. A typo must not become a fate."""


class UnknownReasonCode(ValueError):
    """Refused, not coerced. There is no fallback reason to absorb it into."""


class VerdictInvariantError(ValueError):
    """A disposition and its reasons that cannot both be true."""


def _ordered(codes: Iterable[str]) -> Tuple[str, ...]:
    """Deduplicate and sort into frozen contract order, refusing the unknown."""
    seen = set()
    for code in codes:
        if code not in _REASON_RANK:
            raise UnknownReasonCode(
                f"unknown reason code {str(code)[:48]!r}; expected one of "
                f"{', '.join(REASON_CODES)}")
        seen.add(code)
    return tuple(sorted(seen, key=_REASON_RANK.__getitem__))


@dataclass(frozen=True)
class MetadataAdmissionFacts:
    """What structural validation of a frame's own metadata establishes.

    Every field is required. There is no default, because a default here would
    be an unexamined field asserting that nothing is wrong with it.
    """

    signal_chain_unbound: bool
    receiver_state_unbound: bool
    product_lineage_unbound: bool
    power_unit_unsupported: bool


@dataclass(frozen=True)
class AlignmentAdmissionFacts:
    """What joining the frame to a receiver state establishes.

    Sourced from ``rf_receiver_state.time_align`` and ``may_update_posterior``.
    Every field is required and there is deliberately no default constructor:
    ``AlignmentAdmissionFacts()`` would mean "alignment ran and found nothing
    wrong", which is precisely what an un-run alignment must not be able to say.
    """

    time_alignment_unverified: bool
    receiver_state_stale: bool


@dataclass(frozen=True)
class AdmissionFacts:
    """The complete fact set. Every field required, none defaulted.

    Two authorities establish these, and neither may stand in for the other.
    Metadata validation cannot know whether clocks aligned; alignment cannot
    know whether a sweep-plan revision was declared. "Not yet determined" is a
    state of the pipeline, not a value a fact may hold, so there is no ``None``,
    no nullable boolean and no default: a fact set exists only once both stages
    have run.

    Build it with ``from_stages``. Constructing it directly requires naming all
    seven, which is the same requirement stated less conveniently.
    """

    raw_iq_present: bool
    time_alignment_unverified: bool
    receiver_state_stale: bool
    signal_chain_unbound: bool
    receiver_state_unbound: bool
    product_lineage_unbound: bool
    power_unit_unsupported: bool

    @classmethod
    def from_stages(cls, metadata: MetadataAdmissionFacts,
                    alignment: AlignmentAdmissionFacts) -> "AdmissionFacts":
        """Both stages, both required, in either order but never one alone.

        ``raw_iq_present`` is False by construction here. Raw IQ is a
        discriminated early exit taken before metadata is assessed at all (see
        ``rf_walk_survey_metadata.assess_frame``), so a frame that reaches this
        point is one that carried no samples. A raw-IQ frame never produces a
        fact set; it produces a finished verdict.
        """
        if not isinstance(metadata, MetadataAdmissionFacts):
            raise TypeError("metadata must be MetadataAdmissionFacts")
        if not isinstance(alignment, AlignmentAdmissionFacts):
            raise TypeError("alignment must be AlignmentAdmissionFacts; an "
                            "un-run alignment has no value to pass here")
        return cls(
            raw_iq_present=False,
            time_alignment_unverified=alignment.time_alignment_unverified,
            receiver_state_stale=alignment.receiver_state_stale,
            signal_chain_unbound=metadata.signal_chain_unbound,
            receiver_state_unbound=metadata.receiver_state_unbound,
            product_lineage_unbound=metadata.product_lineage_unbound,
            power_unit_unsupported=metadata.power_unit_unsupported,
        )

    @classmethod
    def from_reason_codes(cls, codes: Iterable[str]) -> "AdmissionFacts":
        """Build from a set of already-named reasons, refusing any unknown one.

        Complete by enumeration: a code absent from the set is asserted false,
        not left undetermined. Callers holding a partial picture must use
        ``from_stages`` instead.
        """
        present = set(_ordered(codes))
        return cls(**{name: (code in present)
                      for name, code in _FACT_TO_REASON.items()})

    def reason_codes(self) -> Tuple[str, ...]:
        """Every applicable reason, in contract order. Never the first found.

        Sorted explicitly rather than relying on the mapping's insertion order.
        The mapping happens to be in contract order today, and a future edit
        that appends a field would silently put it last.
        """
        return _ordered(code for name, code in _FACT_TO_REASON.items()
                        if getattr(self, name))


# One place mapping fields to codes, so a field cannot be added to one and
# forgotten in the other. Declared after the dataclass so it can read its fields.
_FACT_TO_REASON: Dict[str, str] = {
    "time_alignment_unverified": TIME_ALIGNMENT_UNVERIFIED,
    "receiver_state_stale": RECEIVER_STATE_STALE,
    "signal_chain_unbound": SIGNAL_CHAIN_UNBOUND,
    "receiver_state_unbound": RECEIVER_STATE_UNBOUND,
    "product_lineage_unbound": PRODUCT_LINEAGE_UNBOUND,
    "power_unit_unsupported": POWER_UNIT_UNSUPPORTED,
    "raw_iq_present": RAW_IQ_PRESENT,
}
# The mapping and the dataclass must describe the same set of facts.
assert set(_FACT_TO_REASON) == {f.name for f in fields(AdmissionFacts)}
assert set(_FACT_TO_REASON.values()) == set(REASON_CODES)


@dataclass(frozen=True)
class AdmissionVerdict:
    """One disposition, zero or more reasons. Immutable and self-validating.

    The invariants are enforced in ``__post_init__`` rather than only in
    ``decide``, so an invalid verdict cannot be constructed at all -- not by
    this module, not by a caller assembling one by hand, and not by a future
    integration that thinks it knows better.
    """

    disposition: str
    reasons: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.disposition not in DISPOSITIONS:
            raise UnknownDisposition(
                f"unknown disposition {str(self.disposition)[:48]!r}; expected "
                f"one of {', '.join(DISPOSITIONS)}")
        ordered = _ordered(self.reasons)
        if tuple(self.reasons) != ordered:
            # Rewritten rather than rejected: deduplication and ordering are
            # this type's job, and a caller that got them wrong asked for the
            # right verdict with the wrong spelling.
            object.__setattr__(self, "reasons", ordered)
        if self.disposition == SURFACE_ELIGIBLE and ordered:
            raise VerdictInvariantError(
                "SURFACE_ELIGIBLE carries no reason code; a verdict that passed "
                "and still names a refusal reason is two verdicts in a coat")
        if self.disposition == FRAME_REFUSED and ordered != (EXCLUSIVE_REASON,):
            raise VerdictInvariantError(
                f"FRAME_REFUSED is reached only by {EXCLUSIVE_REASON} and carries "
                f"no other reason; got {list(ordered)}")
        if self.disposition == BREADCRUMB_ONLY:
            if not ordered:
                raise VerdictInvariantError(
                    "BREADCRUMB_ONLY without a reason cannot say why the frame "
                    "was refused the surface")
            if EXCLUSIVE_REASON in ordered:
                raise VerdictInvariantError(
                    f"{EXCLUSIVE_REASON} refuses the frame whole; it can never "
                    f"appear under {BREADCRUMB_ONLY}")

    # -- derived, never stored -------------------------------------------

    @property
    def breadcrumb_retained(self) -> bool:
        return _CAPABILITIES[self.disposition]["breadcrumb_retained"]

    @property
    def surface_contribution(self) -> bool:
        return _CAPABILITIES[self.disposition]["surface_contribution"]

    def as_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "schema": SCHEMA,
            "contract": CONTRACT,
            "contract_section": CONTRACT_SECTION,
            "disposition": self.disposition,
            "reasons": list(self.reasons),
            "reason_notes": {code: REASON_NOTES[code] for code in self.reasons},
            "breadcrumb_retained": self.breadcrumb_retained,
            "surface_contribution": self.surface_contribution,
        }
        if self.disposition == FRAME_REFUSED:
            payload["refusal_scope"] = REFUSAL_SCOPE_NOTE
        return payload


def decide(facts: AdmissionFacts) -> AdmissionVerdict:
    """Facts to verdict. Pure: no clock, no I/O, no state, no side effect.

    Raw IQ short-circuits. Nothing else about the frame is evaluated once
    samples are found: there is nothing to learn from gating a frame that will
    not be retained, and gating it anyway means reading more of a payload that
    has already violated the boundary.
    """
    if facts.raw_iq_present:
        return AdmissionVerdict(FRAME_REFUSED, (EXCLUSIVE_REASON,))
    reasons = facts.reason_codes()
    if not reasons:
        return AdmissionVerdict(SURFACE_ELIGIBLE)
    return AdmissionVerdict(BREADCRUMB_ONLY, reasons)


def decide_from_reason_codes(codes: Iterable[str]) -> AdmissionVerdict:
    """Convenience for callers holding named reasons rather than booleans."""
    return decide(AdmissionFacts.from_reason_codes(codes))


def admission_status() -> Dict[str, Any]:
    """The vocabulary, published so a consumer can enumerate it rather than
    discover it one refusal at a time."""
    return {
        "schema": SCHEMA,
        "contract": CONTRACT,
        "contract_section": CONTRACT_SECTION,
        "dispositions": list(DISPOSITIONS),
        "reason_codes": list(REASON_CODES),
        "reason_notes": dict(REASON_NOTES),
        "exclusive_reason": EXCLUSIVE_REASON,
        "ordinary_reasons": list(ORDINARY_REASONS),
        "serialization_order": "FROZEN_CONTRACT_ORDER_NOT_DISCOVERY_ORDER",
        "fallback_reason": None,
        "fallback_note": (
            "THERE IS NO UNKNOWN OR CATCH-ALL REASON. A FACT THE VOCABULARY "
            "CANNOT EXPRESS IS REFUSED AT THE BOUNDARY, BECAUSE A CATCH-ALL IS "
            "HOW A CLOSED VOCABULARY STOPS BEING CLOSED"),
        "alignment_authority": (
            "rf_receiver_state.ALIGNMENT_CAPABILITIES, NOT RESTATED HERE"),
        "side_effects": "NONE",
        "surface_update": "NOT_IMPLEMENTED",
        "graph_mutation": "NOT_IMPLEMENTED",
    }
