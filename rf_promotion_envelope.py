"""What a promotion corpus was built on, frozen before its first window.

§5.23, accepted 2026-09-15.  `PromotionCorpusLock` froze the method and not the
instrument: a corpus validated on one dongle and one antenna licensed the same
promoted claim from a different chain, and nothing in the lock noticed.

The repair §5.22 first proposed -- freeze the `signal_chain_hash` -- **would not
work**.  `gain_db` is inside the chain identity and `IQRetentionOwner.set_gain_db`
rebuilds the chain *before* raising ``GAIN_CHANGE``, so the two windows a
``GAIN_STEPS`` observation is made of carry different chain hashes by
construction, and §5.21's spur protocol swaps the front end for two more.  A
promotion corpus never has one chain hash.

Two declarations, because one is not enough
-------------------------------------------
`InstrumentChainEnvelope` freezes **which instruments**: an explicit canonical
set of `ChainMember` declarations, one member to one chain hash.  It is not a
pair of sets and deliberately not their Cartesian product -- validating
``(ANTENNA_A, 20 dB)`` and ``(TERMINATION, 40 dB)`` does not validate
``(ANTENNA_A, 40 dB)``, and a product rule would admit two chains nobody
sampled.

`CapturePlanDeclaration` freezes **which distribution**.  `signal_chain_hash`
excludes the centre frequency on purpose, and that exclusion is right -- but
bands and tunings are exactly what define the sampled distribution, so the chain
layer cannot carry them.  The plan holds the *materialized* ordered schedule, not
merely its seed: a seed determines a schedule only alongside the algorithm that
consumed it, and a generator revised afterwards turns the same seed into a
different plan with nothing disagreeing.  Both are held, and the schedule is
regenerated and compared byte for byte, so a forged schedule and a drifted
generator are one failure caught by one check.

What this module is not
-----------------------
**Nothing here persists, captures, tunes or opens a device.**  It imports
`hashlib`, `json`, `math` and two pure modules, and it holds no sample.  Nor does
it make the corpus buildable: it says what a corpus may contain, and building one
still needs a receiver, a capture path and a spur catalogue, none of which exist.

Admission is enforced at **use time** -- `rf_validation_manifest._corpus_state`
refuses to promote a chain the frozen envelope does not admit.  Captured-window
admission, which would refuse a window on its way to disk, is **not implemented
and not implementable here**: there is no way to disk.  §5.23 assigns it to the
persistence slice, behind `PENDING_AMENDMENTS` entry 9.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any, Dict, Optional, Tuple

from rf_corpus_vocabulary import CAPTURED, SYNTHETIC
from rf_promotion_geometry import (
    PROMOTION_WINDOW_SAMPLES,
    PROMOTION_SAMPLE_RATE_HZ,
    promotion_geometry_deviations,
)
from rf_signal_chain_identity import UNDECLARED, signal_chain_hash

ENVELOPE_SCHEMA = "scythe.rf-instrument-chain-envelope.v1"
CAPTURE_PLAN_SCHEMA = "scythe.rf-capture-plan.v1"

# The algorithm that turns a seed into a schedule. Frozen beside the seed in
# every declaration, because a seed alone determines nothing: revise the
# generator and the same seed produces a different plan.
SCHEDULE_GENERATOR_REVISION = "rf-visit-schedule.v1"

# The algorithm that picks which eligible units the corpus will actually use.
# Frozen beside the seed for the same reason the schedule generator is: the
# selection has to be decidable **before** capture, and a seed without the
# algorithm that consumed it decides nothing.
SELECTION_REVISION = "rf-spur-selection.v1"


# -- what a sensor identity is worth ----------------------------------------
#
# `signal_chain_manifest` gives the antenna an `authority`, the extension an
# `extension_authority`, the feedline an `authority` and the gain an
# `authority`. **`sensor_id` has none** -- it is the one field in the manifest
# taken on trust, and it is the field §5.22's first scope limit ("the claim is
# about this receiver") rests on entirely.
#
# These three are ONE tuple, and that is load-bearing rather than tidy:
# RECEIVER_INSTANCE_UNIQUENESS_UNATTESTED and RECEIVER_ATTESTED_UNIQUE are a
# negation pair under `is_negation_pair`, on the `UN` prefix. The mechanical
# name check clears a negation pair only **within one declared set**, on the
# same ground that keeps GRAPH_RECORD_FOUND beside GRAPH_RECORD_NOT_FOUND.
# Split across two tuples, these names are rejected.
RECEIVER_ATTESTED_UNIQUE = "RECEIVER_ATTESTED_UNIQUE"
RECEIVER_OPERATOR_INSTANCE = "RECEIVER_OPERATOR_INSTANCE"
RECEIVER_INSTANCE_UNIQUENESS_UNATTESTED = "RECEIVER_INSTANCE_UNIQUENESS_UNATTESTED"
RECEIVER_IDENTITY_AUTHORITIES: Tuple[str, ...] = (
    RECEIVER_ATTESTED_UNIQUE,
    RECEIVER_OPERATOR_INSTANCE,
    RECEIVER_INSTANCE_UNIQUENESS_UNATTESTED,
)

# A corpus may be *built* under an unattested identity. It may not be *promoted*
# from one.
#
# §5.23: a non-unique identifier establishes only that the physical unit cannot
# be recovered from it. Validating one unidentified member of a device class
# validates ONE UNIT, not the class -- and the identifier cannot prove continuity
# across a disconnect, a host change or a hardware substitution, so such a corpus
# may silently span two units. No class-wide generalization follows in either
# direction. A genuine class claim needs multi-unit sampling, unit allocation and
# unit-level blocking, and is not reachable by weakening one unit's identity.
AUTHORITIES_SUFFICIENT_FOR_PROMOTION: Tuple[str, ...] = (
    RECEIVER_ATTESTED_UNIQUE, RECEIVER_OPERATOR_INSTANCE,
)

ENVELOPE_ABSENT = "ENVELOPE_ABSENT"
ENVELOPE_ADMITS_NOTHING = "ENVELOPE_ADMITS_NOTHING"
ENVELOPE_GEOMETRY_REFUSED = "ENVELOPE_GEOMETRY_REFUSED"
ENVELOPE_QUANTITY_NOT_FINITE = "ENVELOPE_QUANTITY_NOT_FINITE"
ENVELOPE_DECLARATION_REPEATED = "ENVELOPE_DECLARATION_REPEATED"
ENVELOPE_DECLARATION_COLLAPSED = "ENVELOPE_DECLARATION_COLLAPSED"
ENVELOPE_MULTIPLE_RECEIVERS = "ENVELOPE_MULTIPLE_RECEIVERS"
ENVELOPE_AUTHORITY_NOT_DECLARED = "ENVELOPE_AUTHORITY_NOT_DECLARED"
ENVELOPE_EXTENSION_NOT_CANONICAL = "ENVELOPE_EXTENSION_NOT_CANONICAL"
ENVELOPE_RECEIVER_SETTING_VARIES = "ENVELOPE_RECEIVER_SETTING_VARIES"
PLAN_ABSENT = "PLAN_ABSENT"
PLAN_SCHEDULE_NOT_REPRODUCIBLE = "PLAN_SCHEDULE_NOT_REPRODUCIBLE"
PLAN_SCHEDULE_UNSATISFIABLE = "PLAN_SCHEDULE_UNSATISFIABLE"
PLAN_ALLOCATION_OUTSIDE_ENVELOPE = "PLAN_ALLOCATION_OUTSIDE_ENVELOPE"
PLAN_QUANTITY_NOT_FINITE = "PLAN_QUANTITY_NOT_FINITE"
PLAN_BAND_UNUSABLE = "PLAN_BAND_UNUSABLE"
PLAN_TUNING_OUTSIDE_BAND = "PLAN_TUNING_OUTSIDE_BAND"
PLAN_RETUNE_NOT_DECLARED = "PLAN_RETUNE_NOT_DECLARED"
PLAN_TRIALS_DO_NOT_RECONCILE = "PLAN_TRIALS_DO_NOT_RECONCILE"
PLAN_SPUR_CATALOGUE_ABSENT = "PLAN_SPUR_CATALOGUE_ABSENT"
PLAN_SPUR_NOT_FEASIBLE = "PLAN_SPUR_NOT_FEASIBLE"
PLAN_SPUR_ALLOCATION_UNDECLARED = "PLAN_SPUR_ALLOCATION_UNDECLARED"
PLAN_SPUR_NOT_PERSISTENT = "PLAN_SPUR_NOT_PERSISTENT"
PLAN_REPEATS_NOT_OBSERVED = "PLAN_REPEATS_NOT_OBSERVED"
PLAN_TRIAL_NOT_ELIGIBLE = "PLAN_TRIAL_NOT_ELIGIBLE"
PLAN_TRIAL_IDENTITY_AMBIGUOUS = "PLAN_TRIAL_IDENTITY_AMBIGUOUS"
PLAN_SELECTION_NOT_REPRODUCIBLE = "PLAN_SELECTION_NOT_REPRODUCIBLE"

ENVELOPE_REFUSALS: Tuple[str, ...] = (
    ENVELOPE_ABSENT, ENVELOPE_ADMITS_NOTHING, ENVELOPE_GEOMETRY_REFUSED,
    ENVELOPE_QUANTITY_NOT_FINITE, ENVELOPE_DECLARATION_REPEATED,
    ENVELOPE_DECLARATION_COLLAPSED, ENVELOPE_MULTIPLE_RECEIVERS,
    ENVELOPE_AUTHORITY_NOT_DECLARED, ENVELOPE_EXTENSION_NOT_CANONICAL,
    ENVELOPE_RECEIVER_SETTING_VARIES, PLAN_ABSENT,
    PLAN_SCHEDULE_NOT_REPRODUCIBLE, PLAN_SCHEDULE_UNSATISFIABLE,
    PLAN_ALLOCATION_OUTSIDE_ENVELOPE, PLAN_QUANTITY_NOT_FINITE,
    PLAN_BAND_UNUSABLE, PLAN_TUNING_OUTSIDE_BAND, PLAN_RETUNE_NOT_DECLARED,
    PLAN_TRIALS_DO_NOT_RECONCILE, PLAN_SPUR_CATALOGUE_ABSENT,
    PLAN_SPUR_NOT_FEASIBLE, PLAN_SPUR_ALLOCATION_UNDECLARED,
    PLAN_SPUR_NOT_PERSISTENT, PLAN_REPEATS_NOT_OBSERVED,
    PLAN_TRIAL_NOT_ELIGIBLE, PLAN_TRIAL_IDENTITY_AMBIGUOUS,
    PLAN_SELECTION_NOT_REPRODUCIBLE,
)


class EnvelopeRefused(RuntimeError):
    """A declaration that is not one. A code and a detail, never a repair."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def _finite(value: Any) -> bool:
    """A real number that is actually a number.

    NaN is refused rather than tolerated because it is not equal to itself: an
    envelope holding one cannot reliably answer whether it admits its own
    declared member, and membership is the whole of what an envelope does.
    """
    if type(value) is bool:
        return False
    if not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def _optional_number_key(value: Optional[float]) -> Tuple[int, float]:
    """A sort key that makes an absence explicit.

    `None < 1.0` raises in Python, so sorting members on a raw `Optional[float]`
    would crash on a legitimately undeclared gain -- a worse defect than the
    unordered digest it was introduced to fix.
    """
    return (0, 0.0) if value is None else (1, float(value))


def _extension_key(value: Any) -> Tuple[int, float, str]:
    """`extension_mm` is `Any` by §13n O.3, so its order must be too.

    A declared millimetre figure sorts numerically; anything else sorts after it
    by its own text, which keeps UNDECLARED and a number in one total order
    without pretending the two are comparable as lengths.
    """
    if _finite(value):
        return (0, float(value), "")
    return (1, 0.0, str(value))


@dataclass(frozen=True)
class FrontEnd:
    """One antenna path, exactly as the signal-chain identity sees it.

    A 50 Ohm termination is a front end like any other: `antenna` names it and
    `extension_mm` says it has no extension. That is deliberate -- §5.21's spur
    protocol swaps the front end for a termination, and the envelope has to be
    able to say so without a special case for "no antenna".
    """

    antenna: str
    extension_mm: Any
    feedline: str
    feedline_length_m: Optional[float]

    def __post_init__(self) -> None:
        for name in ("antenna", "feedline"):
            if type(getattr(self, name)) is not str or not getattr(self, name):
                raise EnvelopeRefused(
                    ENVELOPE_ABSENT,
                    f"{name} must be a declared non-empty string")
        if self.feedline_length_m is not None and not _finite(self.feedline_length_m):
            raise EnvelopeRefused(
                ENVELOPE_QUANTITY_NOT_FINITE,
                f"feedline_length_m is {self.feedline_length_m!r}")
        # A closed domain, not a type hint. `extension_mm` is `Any` in
        # `signal_chain_manifest` because §13n O.3 preserves every existing
        # digest byte-for-byte, including the boolean that has always hashed as
        # 1.0 -- but "the identity module accepts it" is not "a promotion corpus
        # may declare it". Anything outside this domain reaches `to_dict` as a
        # value JSON cannot serialise, and a declaration that is only discovered
        # to be invalid when something later asks for its digest is the nominal
        # type problem §5.23 rejected, one field down.
        if type(self.extension_mm) is bool:
            pass                                   # 1.0 or 0.0, and always was
        elif isinstance(self.extension_mm, (int, float)):
            if not _finite(self.extension_mm):
                raise EnvelopeRefused(
                    ENVELOPE_QUANTITY_NOT_FINITE,
                    f"extension_mm is {self.extension_mm!r}")
        elif self.extension_mm != UNDECLARED:
            raise EnvelopeRefused(
                ENVELOPE_EXTENSION_NOT_CANONICAL,
                f"extension_mm is {self.extension_mm!r}; a promotion corpus "
                f"declares a finite millimetre figure or exactly {UNDECLARED!r}")

    def as_tuple(self) -> Tuple[Any, ...]:
        return (self.antenna, self.extension_mm, self.feedline,
                self.feedline_length_m)


@dataclass(frozen=True)
class ChainMember:
    """One complete chain declaration, and the identity it produces.

    **The hash is computed, never stored.** A transcribed digest beside the
    fields it describes is a second source of truth that can disagree with the
    first, which is the mistake `rf_promotion_geometry` exists to end; here it
    would be worse, because the disagreeing copy is what admission compares
    against.
    """

    sensor_id: str
    receiver_identity_authority: str
    sample_type: str
    sample_rate_hz: float
    front_end: FrontEnd
    gain_db: Optional[float]

    def __post_init__(self) -> None:
        if type(self.front_end) is not FrontEnd:
            raise EnvelopeRefused(
                ENVELOPE_ABSENT,
                f"front_end is a FrontEnd; got {type(self.front_end).__name__}")
        for name in ("sensor_id", "sample_type"):
            if type(getattr(self, name)) is not str or not getattr(self, name):
                raise EnvelopeRefused(
                    ENVELOPE_ABSENT,
                    f"{name} must be a declared non-empty string")
        if self.receiver_identity_authority not in RECEIVER_IDENTITY_AUTHORITIES:
            raise EnvelopeRefused(
                ENVELOPE_AUTHORITY_NOT_DECLARED,
                f"{self.receiver_identity_authority!r} is not one of "
                f"{list(RECEIVER_IDENTITY_AUTHORITIES)}. A sensor identity "
                "without a declared authority is the hole this field exists "
                "to close, and defaulting one would reopen it quietly")
        if not _finite(self.sample_rate_hz):
            raise EnvelopeRefused(
                ENVELOPE_QUANTITY_NOT_FINITE,
                f"sample_rate_hz is {self.sample_rate_hz!r}")
        if self.gain_db is not None and not _finite(self.gain_db):
            raise EnvelopeRefused(
                ENVELOPE_QUANTITY_NOT_FINITE, f"gain_db is {self.gain_db!r}")
        deviations = promotion_geometry_deviations(
            sample_rate_hz=self.sample_rate_hz,
            window_samples=PROMOTION_WINDOW_SAMPLES)
        if deviations:
            raise EnvelopeRefused(
                ENVELOPE_GEOMETRY_REFUSED,
                f"{list(deviations)} deviate from the promotion geometry")

    def chain_hash(self) -> str:
        """The identity `rf_iq_retention` would compute for this declaration."""
        return signal_chain_hash(
            sensor_id=self.sensor_id, sample_type=self.sample_type,
            sample_rate_hz=self.sample_rate_hz,
            antenna=self.front_end.antenna, feedline=self.front_end.feedline,
            extension_mm=self.front_end.extension_mm, gain_db=self.gain_db,
            feedline_length_m=self.front_end.feedline_length_m)

    def sort_key(self) -> Tuple[Any, ...]:
        return (self.sensor_id, self.receiver_identity_authority,
                self.sample_type, float(self.sample_rate_hz),
                self.front_end.antenna, _extension_key(self.front_end.extension_mm),
                self.front_end.feedline,
                _optional_number_key(self.front_end.feedline_length_m),
                _optional_number_key(self.gain_db), self.chain_hash())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sensor_id": self.sensor_id,
            "receiver_identity_authority": self.receiver_identity_authority,
            "sample_type": self.sample_type,
            "sample_rate_hz": float(self.sample_rate_hz),
            "antenna": self.front_end.antenna,
            "extension_mm": self.front_end.extension_mm,
            "feedline": self.front_end.feedline,
            "feedline_length_m": self.front_end.feedline_length_m,
            "gain_db": self.gain_db,
            "chain_hash": self.chain_hash(),
        }


@dataclass(frozen=True)
class InstrumentChainEnvelope:
    """Every chain a promotion corpus may contain, written down one by one.

    **Validates itself.** A validating factory beside a publicly constructible
    dataclass is a locked door beside an open window: `type(x) is
    InstrumentChainEnvelope` passes for a hand-built instance with no members at
    all, and the lock's nominal gate checks nothing further. The gate is not the
    defect -- the defect is a type that does not mean what the gate assumes.

    **Canonicalizes itself.** Members are converted once, validated, then sorted,
    so two declarations differing only in order are one object with one digest.
    An envelope with as many digests as it has orderings would be as many
    envelopes.
    """

    members: Tuple[ChainMember, ...]

    def __post_init__(self) -> None:
        # Converted FIRST. Validating an iterable and then storing it consumes a
        # generator during the checks and stores an empty tuple afterwards -- the
        # order of these two lines is the whole fix.
        members = tuple(self.members)
        for member in members:
            if type(member) is not ChainMember:
                raise EnvelopeRefused(
                    ENVELOPE_ABSENT,
                    f"members are ChainMember; got {type(member).__name__}")
        if not members:
            raise EnvelopeRefused(
                ENVELOPE_ADMITS_NOTHING,
                "an envelope with no members admits no window, and a corpus "
                "under it could never contain one")
        receivers = {(m.sensor_id, m.receiver_identity_authority) for m in members}
        if len(receivers) != 1:
            raise EnvelopeRefused(
                ENVELOPE_MULTIPLE_RECEIVERS,
                f"{sorted(receivers)} -- the envelope fixes the receiver and "
                "varies the gain and the front end. Two receivers in one "
                "corpus is a different experiment")
        # §5.23 fixes the receiver, the sample type AND the rate, and varies
        # only the gain and the front end. Distinct hashes are not a licence:
        # uint8 and cs8 members hash differently and would both be admitted,
        # which is two decode paths inside one declared envelope.
        settings = {(m.sample_type, float(m.sample_rate_hz)) for m in members}
        if len(settings) != 1:
            raise EnvelopeRefused(
                ENVELOPE_RECEIVER_SETTING_VARIES,
                f"{sorted(settings)} -- the sample type and rate are fixed by "
                "the envelope. Only the gain and the front end may vary")
        keys = [m.sort_key() for m in members]
        if len(set(keys)) != len(keys):
            raise EnvelopeRefused(
                ENVELOPE_DECLARATION_REPEATED,
                "a member declared twice admits nothing new, so it is a "
                "declaration error rather than a redundancy to absorb")
        hashes = {m.chain_hash() for m in members}
        if len(hashes) != len(members):
            raise EnvelopeRefused(
                ENVELOPE_DECLARATION_COLLAPSED,
                f"{len(members)} declared members produce {len(hashes)} "
                "distinct chain hashes. The identity does not distinguish two "
                "declarations written as different, and that belongs in a "
                "refusal rather than in a count nobody reads")
        object.__setattr__(self, "members",
                           tuple(sorted(members, key=ChainMember.sort_key)))

    @property
    def sensor_id(self) -> str:
        return self.members[0].sensor_id

    @property
    def receiver_identity_authority(self) -> str:
        """One value, because `__post_init__` refuses more than one."""
        return self.members[0].receiver_identity_authority

    @property
    def may_be_promoted_from(self) -> bool:
        """Whether a corpus under this envelope could promote at all.

        False is not a defect in the envelope. It is an accurate report that the
        declared identifier does not establish which unit was tested, and a
        promoted claim would be about a unit nobody can name.
        """
        return (self.receiver_identity_authority
                in AUTHORITIES_SUFFICIENT_FOR_PROMOTION)

    def admissible_chain_hashes(self) -> frozenset:
        """Exactly the declared members' identities. Never a cross product.

        Two declared sets do not declare their product: an envelope built from
        the gains ``{20, 40}`` and the front ends ``{ANTENNA_A, TERMINATION}``
        would admit four chains where the plan sampled two, and the two extra
        would pass admission while lying outside the validated distribution.
        """
        return frozenset(member.chain_hash() for member in self.members)

    def admits(self, chain_hash: Any) -> bool:
        """Is a window from this instrument, inside this envelope?"""
        return chain_hash in self.admissible_chain_hashes()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": ENVELOPE_SCHEMA,
            "sensor_id": self.sensor_id,
            "receiver_identity_authority": self.receiver_identity_authority,
            "members": [member.to_dict() for member in self.members],
            "member_count": len(self.members),
        }

    def digest(self) -> str:
        # No `default=str`. A value the schema did not anticipate must raise
        # here rather than be quietly stringified into the digest, which would
        # widen what may be declared and record the widening as if it were
        # declared on purpose.
        material = json.dumps(self.to_dict(), sort_keys=True,
                              separators=(",", ":")).encode("utf-8")
        return f"blake2s:{hashlib.blake2s(material, digest_size=16).hexdigest()}"


def declare_instrument_chain_envelope(*, members: Any) -> InstrumentChainEnvelope:
    """Declare an envelope. A convenience over a type that is already safe."""
    return InstrumentChainEnvelope(members=tuple(members))


# -- the capture plan, which is the other half of the envelope ---------------
#
# Every value below is §5.22's, derived there and pinned there. None is chosen
# here, and the two that could look chosen are computed: the harmonic cap from
# the declared crystal tolerance, and the retune-delta ceiling from the slope
# bound and the band-edge exclusion. `test_rf_promotion_envelope` reproduces
# §5.22's published numbers from that arithmetic, which is the check that the
# table and the code agree.
#
# A constant in a digest is not a plan. Every one of these is referenced by a
# check over a declared act -- a tuning that must sit inside its band, a signed
# retune that must be one of the declared deltas, a trial count that must
# reconcile. The first implementation digested `PLAN_RETUNE_DELTAS_HZ`,
# `PLAN_MAX_MIXING_SLOPE` and `PLAN_BAND_EDGE_EXCLUSION` while nothing in the
# plan referred to a frequency at all, which made them ornaments.
PLAN_TUNING_COUNT = 64                    # K
PLAN_REPEATS_PER_TUNING = 8               # R
PLAN_PERSISTENCE_REQUIRED = 7             # of R
PLAN_VISITS_PER_TUNING = 3                # each LO setting visited >= 3 times
PLAN_MINIMUM_SEPARATION = 10              # separated by >= 10 other settings
PLAN_RETUNE_DELTAS_HZ: Tuple[float, ...] = (50_000.0, 100_000.0, 200_000.0)
PLAN_MAX_MIXING_SLOPE = 3                 # |s| <= 3 inside the usable half-span
PLAN_SLOPE_TOLERANCE = 0.01
PLAN_PERSISTENCE_MARGIN_DB = 10.0
PLAN_BAND_EDGE_EXCLUSION = 0.05           # folded features reverse direction
PLAN_REFERENCE_MATCH_SPAN_FRACTION = 0.005

# Imported from `rf_corpus_vocabulary`, which owns the concept. A plan names
# the source of every stratum's trials and `rf_null_corpus` labels every window
# it builds; one declaration, in neither of the modules that use it.
TRIAL_SOURCES: Tuple[str, ...] = (SYNTHETIC, CAPTURED)

# §5.21's usable classes and stability axis. The catalogue records both for
# every product, and §5.22 requires the allocation to draw from every class the
# catalogue contains: an allocation that happened to under-sample the
# SESSION_SCOPED products would test the stratum where it is easiest.
CONSISTENT_WITH_INTERNAL_MIXING = "CONSISTENT_WITH_INTERNAL_MIXING"
CONSISTENT_WITH_INTERNAL_REFERENCE = "CONSISTENT_WITH_INTERNAL_REFERENCE"
SPUR_CANDIDATE_UNRESOLVED = "SPUR_CANDIDATE_UNRESOLVED"
VANISHES_ON_DECLARED_TERMINATION = "VANISHES_ON_DECLARED_TERMINATION"
SPUR_CLASSIFICATIONS: Tuple[str, ...] = (
    CONSISTENT_WITH_INTERNAL_MIXING, CONSISTENT_WITH_INTERNAL_REFERENCE,
    SPUR_CANDIDATE_UNRESOLVED, VANISHES_ON_DECLARED_TERMINATION,
)
SESSION_SCOPED = "SESSION_SCOPED"
RECONNECT_STABLE = "RECONNECT_STABLE"
POWER_CYCLE_STABLE = "POWER_CYCLE_STABLE"
SPUR_STABILITY_CLASSES: Tuple[str, ...] = (
    SESSION_SCOPED, RECONNECT_STABLE, POWER_CYCLE_STABLE,
)

# The two accepted confidence levels, §5.22. Neither is reachable by asserting
# it, and they differ because §5.21 gives the two usable classes different
# amounts of evidence.
CONFIDENCE_MIXING_TERMINATION_AND_SECOND_TIME = (
    "TERMINATION_DECLARED_AND_REPEATED_AT_SECOND_TIME_OF_DAY")
CONFIDENCE_REFERENCE_TERMINATION_AND_SECOND_SITE = (
    "TERMINATION_DECLARED_AND_SECOND_SITE_OR_SHIELDED_ENCLOSURE")
ACCEPTED_CONFIDENCE_LEVELS: Tuple[str, ...] = (
    CONFIDENCE_MIXING_TERMINATION_AND_SECOND_TIME,
    CONFIDENCE_REFERENCE_TERMINATION_AND_SECOND_SITE,
)
# §5.21: CONSISTENT_WITH_INTERNAL_REFERENCE has one discriminator and a model
# match that is not a second, so it is accepted only at the higher level.
CONFIDENCE_REQUIRED_BY_CLASSIFICATION: Dict[str, str] = {
    CONSISTENT_WITH_INTERNAL_MIXING: CONFIDENCE_MIXING_TERMINATION_AND_SECOND_TIME,
    CONSISTENT_WITH_INTERNAL_REFERENCE: CONFIDENCE_REFERENCE_TERMINATION_AND_SECOND_SITE,
}

# A bound on retries, not a tuning knob. The constraint is satisfiable and this
# says so loudly if it ever stops being: a generator that quietly relaxed a
# separation rule would produce a schedule that looks like the declared design
# and is not one.
_SCHEDULE_ATTEMPT_CEILING = 4096


def usable_half_span_hz(span_hz: float = PROMOTION_SAMPLE_RATE_HZ) -> float:
    """The half-span a feature may be tracked across, after the folding guard.

    §5.22 excludes features within 5% of either edge from slope estimation,
    because a folded feature reverses its apparent direction of travel. What is
    left is the half-span a retune may actually move a product within.
    """
    return span_hz * (0.5 - PLAN_BAND_EDGE_EXCLUSION)


def retune_delta_ceiling_hz(sample_rate_hz: float, band_edge_exclusion: float,
                            max_mixing_slope: int) -> float:
    """The ceiling, as arithmetic with all three inputs explicit.

    Separated from the module constants so the two things it is easy to conflate
    can be tested apart: **that §5.22's chosen deltas fit the ceiling**, and
    **that the ceiling actually depends on the slope bound**. Mutating the
    global `PLAN_MAX_MIXING_SLOPE` proves only that the constant is
    load-bearing -- it makes every plan fixture unconstructable, which is a
    detonation rather than a discrimination.
    """
    return sample_rate_hz * (0.5 - band_edge_exclusion) / max_mixing_slope


def maximum_retune_delta_hz(span_hz: float = PROMOTION_SAMPLE_RATE_HZ) -> float:
    """A modelled product moves `|s| * delta`, so the slope bound caps the delta.

    §5.22 derived 341 kHz from the raw 1.024 MHz half-span. This is the same
    arithmetic over the *usable* half-span, which is the one a slope is actually
    estimated across, and it is therefore smaller. Every declared delta is
    checked against it rather than against the §5.22 figure, so the number that
    governs is the one the exclusion leaves.
    """
    return retune_delta_ceiling_hz(span_hz, PLAN_BAND_EDGE_EXCLUSION,
                                   PLAN_MAX_MIXING_SLOPE)


def deltas_over_ceiling(deltas: Any, ceiling: float) -> Tuple[float, ...]:
    """Declared retune deltas that exceed the slope bound's ceiling.

    Extracted so it can be tested **directly** with a ceiling that something
    violates. Inside `__post_init__` it is unreachable by any legal
    configuration -- §5.22's three deltas all fit -- so a control that mutated
    the comprehension into the empty list it already produces changed nothing
    and failed zero tests. A guard against a future edit is still worth having;
    it just cannot be exercised by building a valid plan.
    """
    return tuple(value for value in deltas if value > ceiling)


def harmonic_cap(*, reference_hz: float, reference_ppm: float,
                 span_hz: float = PROMOTION_SAMPLE_RATE_HZ) -> int:
    """How far up the reference comb a match still means anything.

    The match window is ``n * f_ref * ppm``, so it **grows with the harmonic**.
    Requiring it to stay under 0.5% of the analysis span gives a usable range
    that depends entirely on how well the reference is known -- and an
    uncalibrated dongle earns almost no reference matches, which is the correct
    outcome rather than an inconvenience.
    """
    if not (_finite(reference_hz) and _finite(reference_ppm)
            and _finite(span_hz)):
        raise EnvelopeRefused(
            PLAN_QUANTITY_NOT_FINITE,
            "the reference, its tolerance and the span must all be numbers")
    if reference_hz <= 0 or reference_ppm <= 0 or span_hz <= 0:
        raise EnvelopeRefused(
            PLAN_QUANTITY_NOT_FINITE,
            "a non-positive reference, tolerance or span describes no comb")
    window_per_harmonic = reference_hz * reference_ppm * 1e-6
    return int(math.floor(
        PLAN_REFERENCE_MATCH_SPAN_FRACTION * span_hz / window_per_harmonic))


@dataclass(frozen=True)
class Band:
    """A declared band, in hertz, that the tunings are drawn from."""

    band_id: str
    low_hz: float
    high_hz: float

    def __post_init__(self) -> None:
        if type(self.band_id) is not str or not self.band_id:
            raise EnvelopeRefused(PLAN_ABSENT, "a band needs a declared id")
        for name in ("low_hz", "high_hz"):
            if not _finite(getattr(self, name)) or getattr(self, name) <= 0:
                raise EnvelopeRefused(
                    PLAN_QUANTITY_NOT_FINITE,
                    f"{name} is {getattr(self, name)!r}")
        if self.high_hz <= self.low_hz:
            raise EnvelopeRefused(
                PLAN_BAND_UNUSABLE,
                f"{self.band_id}: {self.low_hz} is not below {self.high_hz}")
        if self.usable_width_hz() <= 0:
            raise EnvelopeRefused(
                PLAN_BAND_UNUSABLE,
                f"{self.band_id} is narrower than one analysis span plus the "
                "retune excursion, so no tuning in it can be retuned and still "
                "have the whole span inside the band")

    def _guard_hz(self) -> float:
        """Half a span, plus the largest declared retune excursion.

        A tuning is only usable if the *retuned* analysis span is still inside
        the declared band. Otherwise the plan would declare a band and sample
        outside it, which is the envelope-scope limit failing quietly.
        """
        return (PROMOTION_SAMPLE_RATE_HZ / 2.0) + max(PLAN_RETUNE_DELTAS_HZ)

    def usable_low_hz(self) -> float:
        return self.low_hz + self._guard_hz()

    def usable_high_hz(self) -> float:
        return self.high_hz - self._guard_hz()

    def usable_width_hz(self) -> float:
        return self.usable_high_hz() - self.usable_low_hz()

    def contains_tuning(self, center_hz: float) -> bool:
        return self.usable_low_hz() <= center_hz <= self.usable_high_hz()

    def to_dict(self) -> Dict[str, Any]:
        return {"band_id": self.band_id, "low_hz": float(self.low_hz),
                "high_hz": float(self.high_hz)}


@dataclass(frozen=True)
class Tuning:
    """One LO setting, in hertz, inside a named band.

    The first implementation carried an integer label and called it a tuning.
    A permutation of 0..63 is not §5.22's sixty-four pseudorandomly spaced
    tunings; it is a permutation, and nothing about it could be checked against
    a band, a delta or a slope.
    """

    tuning_index: int
    center_frequency_hz: float
    band_id: str

    @property
    def tuning_id(self) -> str:
        """The name an eligible trial refers to this tuning by."""
        return f"tuning-{self.tuning_index:03d}"

    def __post_init__(self) -> None:
        if type(self.tuning_index) is not int:
            raise EnvelopeRefused(
                PLAN_QUANTITY_NOT_FINITE, "tuning_index is an int")
        if not _finite(self.center_frequency_hz) or self.center_frequency_hz <= 0:
            raise EnvelopeRefused(
                PLAN_QUANTITY_NOT_FINITE,
                f"center_frequency_hz is {self.center_frequency_hz!r}")
        if type(self.band_id) is not str or not self.band_id:
            raise EnvelopeRefused(PLAN_ABSENT, "a tuning names its band")

    def to_dict(self) -> Dict[str, Any]:
        return {"tuning_index": self.tuning_index,
                "center_frequency_hz": float(self.center_frequency_hz),
                "band_id": self.band_id}


@dataclass(frozen=True)
class Visit:
    """One tuning, visited once, with the signed retune it is visited for.

    `position` is explicit rather than left to a list index. §5.22 pins
    randomised *and counterbalanced* order precisely so that retune position and
    elapsed time stay two dimensions, and a structure implying the second from
    the first re-collapses the confound the randomisation exists to break.

    `retune_delta_hz` is **signed**, and it is the act `PLAN_RETUNE_DELTAS_HZ`
    governs. Without it the declared deltas described nothing that happened.
    """

    position: int
    tuning_index: int
    retune_delta_hz: float

    def to_dict(self) -> Dict[str, Any]:
        return {"position": self.position, "tuning_index": self.tuning_index,
                "retune_delta_hz": float(self.retune_delta_hz)}


def select_spur_trials(*, eligible: Any, seed: int, required: int,
                       selection_revision: str = SELECTION_REVISION,
                       ) -> Tuple[Tuple[str, str, str], ...]:
    """Which eligible units the corpus will use, decided before it is captured.

    Membership in the eligible set is **weaker than precommitment**. With 6 000
    eligible units and 5 561 needed, a corpus that only had to prove membership
    could pick whichever 5 561 produced convenient outcomes once the windows
    existed -- the same post-hoc selection the frozen threshold exists to
    prevent, moved from the detector to the sample.

    So the subset is derived: digest-ordered from the seed and this revision,
    reproducible by anyone holding the plan, and fixed before a single window.
    """
    if selection_revision != SELECTION_REVISION:
        raise EnvelopeRefused(
            PLAN_SELECTION_NOT_REPRODUCIBLE,
            f"{selection_revision!r} is not {SELECTION_REVISION!r}; this "
            "module implements one selection and cannot reproduce another")
    if type(seed) is not int:
        raise EnvelopeRefused(
            PLAN_QUANTITY_NOT_FINITE, f"the seed is an int; got {type(seed).__name__}")
    keys = {trial.key() for trial in eligible}
    if len(keys) < required:
        raise EnvelopeRefused(
            PLAN_SPUR_NOT_FEASIBLE,
            f"{len(keys)} eligible units cannot supply {required} selected ones")
    ordered = sorted(
        (hashlib.blake2s(
            f"{seed}|{selection_revision}|{'|'.join(key)}".encode(),
            digest_size=16).digest(), key)
        for key in keys)
    return tuple(sorted(key for _digest, key in ordered[:required]))


def _digest_int(material: str, modulus: int) -> int:
    """A deterministic integer from material, independent of any RNG."""
    raw = hashlib.blake2s(material.encode(), digest_size=16).digest()
    return int.from_bytes(raw, "big") % modulus


def _deterministic_permutation(count: int, *, material: str) -> Tuple[int, ...]:
    """A permutation fixed by its material, and by nothing else.

    Not `random.shuffle`. The plan promises byte-for-byte regeneration, and a
    promise resting on the stability of an interpreter's RNG across versions is
    a promise about CPython rather than about this repository.
    """
    keyed = sorted(
        (hashlib.blake2s(f"{material}|{index}".encode(), digest_size=16).digest(),
         index)
        for index in range(count))
    return tuple(index for _key, index in keyed)


def _tunings_per_band(bands: Tuple[Band, ...]) -> Tuple[int, ...]:
    """K split across the declared bands by usable width, largest remainder.

    Deterministic, and every band gets at least one: a band declared and never
    visited is a band the envelope claims and the corpus does not cover.
    """
    widths = [band.usable_width_hz() for band in bands]
    total = sum(widths)
    exact = [PLAN_TUNING_COUNT * width / total for width in widths]
    counts = [max(1, int(math.floor(value))) for value in exact]
    # Largest remainder, then trim from the largest allocations if the floor
    # plus the per-band minimum has overshot.
    order = sorted(range(len(bands)), key=lambda i: (-(exact[i] - math.floor(exact[i])), i))
    while sum(counts) < PLAN_TUNING_COUNT:
        for i in order:
            if sum(counts) == PLAN_TUNING_COUNT:
                break
            counts[i] += 1
    while sum(counts) > PLAN_TUNING_COUNT:
        for i in sorted(range(len(bands)), key=lambda i: (-counts[i], i)):
            if sum(counts) == PLAN_TUNING_COUNT or counts[i] <= 1:
                continue
            counts[i] -= 1
    return tuple(counts)


def generate_tunings(*, seed: int, bands: Tuple[Band, ...],
                     generator_revision: str = SCHEDULE_GENERATOR_REVISION,
                     ) -> Tuple[Tuning, ...]:
    """§5.22's K tunings, at pseudorandom spacing from the declared seed.

    **Never uniform.** Uniform spacing at a divisor of the reference interval
    would systematically hit or systematically miss the comb, and either way the
    catalogue would be an artefact of the grid rather than of the receiver.

    Frequencies are whole hertz. A plan that regenerates byte for byte cannot
    rest on float formatting, and the bin is 3.9 Hz wide, so a hertz is finer
    than anything the analysis can see.
    """
    if generator_revision != SCHEDULE_GENERATOR_REVISION:
        raise EnvelopeRefused(
            PLAN_SCHEDULE_NOT_REPRODUCIBLE,
            f"{generator_revision!r} is not {SCHEDULE_GENERATOR_REVISION!r}")
    if type(seed) is not int:
        raise EnvelopeRefused(
            PLAN_QUANTITY_NOT_FINITE, f"the seed is an int; got {type(seed).__name__}")
    if not bands:
        raise EnvelopeRefused(
            PLAN_BAND_UNUSABLE, "a capture plan declares at least one band")
    counts = _tunings_per_band(bands)
    tunings: list = []
    for band, wanted in zip(bands, counts):
        low = int(math.ceil(band.usable_low_hz()))
        high = int(math.floor(band.usable_high_hz()))
        width = high - low + 1
        if width < wanted:
            raise EnvelopeRefused(
                PLAN_BAND_UNUSABLE,
                f"{band.band_id} has {width} usable hertz for {wanted} tunings")
        chosen: list = []
        seen = set()
        for draw in range(wanted):
            for attempt in range(_SCHEDULE_ATTEMPT_CEILING):
                value = low + _digest_int(
                    f"{seed}|{generator_revision}|tuning|{band.band_id}|"
                    f"{draw}|{attempt}", width)
                if value not in seen:
                    seen.add(value)
                    chosen.append(value)
                    break
            else:
                raise EnvelopeRefused(
                    PLAN_BAND_UNUSABLE,
                    f"{band.band_id} could not place {wanted} distinct tunings")
        for value in sorted(chosen):
            tunings.append((band.band_id, value))
    return tuple(
        Tuning(tuning_index=index, center_frequency_hz=float(centre),
               band_id=band_id)
        for index, (band_id, centre) in enumerate(tunings))


def _separations_hold(order: Tuple[int, ...]) -> bool:
    """Every repeat of one tuning is > PLAN_MINIMUM_SEPARATION apart, so a
    non-stationary emitter is caught between visits rather than fitted
    through them."""
    last: Dict[int, int] = {}
    for position, tuning in enumerate(order):
        previous = last.get(tuning)
        if previous is not None and position - previous <= PLAN_MINIMUM_SEPARATION:
            return False
        last[tuning] = position
    return True


def _signed_deltas() -> Tuple[float, ...]:
    """Each declared delta, in both directions. §5.22 uses both."""
    return tuple(sign * delta
                 for delta in PLAN_RETUNE_DELTAS_HZ for sign in (1.0, -1.0))


def generate_visit_schedule(*, seed: int, tunings: Tuple[Tuning, ...],
                            generator_revision: str = SCHEDULE_GENERATOR_REVISION,
                            ) -> Tuple[Visit, ...]:
    """The declared schedule over the declared tunings, with signed retunes.

    Rounds of permuted tunings, retried at the round boundary until the
    separation rule holds -- refusing rather than relaxing if it cannot, because
    a silently relaxed schedule is indistinguishable from the declared one when
    it is read back.
    """
    if generator_revision != SCHEDULE_GENERATOR_REVISION:
        raise EnvelopeRefused(
            PLAN_SCHEDULE_NOT_REPRODUCIBLE,
            f"{generator_revision!r} is not {SCHEDULE_GENERATOR_REVISION!r}; "
            "this module implements one generator and cannot reproduce "
            "another. A seed without its algorithm determines no schedule")
    if type(seed) is not int:
        raise EnvelopeRefused(
            PLAN_QUANTITY_NOT_FINITE, f"the seed is an int; got {type(seed).__name__}")
    if len(tunings) != PLAN_TUNING_COUNT:
        raise EnvelopeRefused(
            PLAN_SCHEDULE_UNSATISFIABLE,
            f"{len(tunings)} tunings declared; §5.22 pins K = {PLAN_TUNING_COUNT}")
    order: list = []
    for round_index in range(PLAN_VISITS_PER_TUNING):
        for attempt in range(_SCHEDULE_ATTEMPT_CEILING):
            candidate = _deterministic_permutation(
                PLAN_TUNING_COUNT,
                material=f"{seed}|{generator_revision}|{round_index}|{attempt}")
            if _separations_hold(tuple(order) + candidate):
                order.extend(candidate)
                break
        else:
            raise EnvelopeRefused(
                PLAN_SCHEDULE_UNSATISFIABLE,
                f"round {round_index} found no permutation keeping every "
                f"repeat {PLAN_MINIMUM_SEPARATION} apart in "
                f"{_SCHEDULE_ATTEMPT_CEILING} attempts")
    half = len(order) // 2
    if not (set(order[:half]) == set(order[half:]) == set(range(PLAN_TUNING_COUNT))):
        raise EnvelopeRefused(
            PLAN_SCHEDULE_UNSATISFIABLE,
            "the schedule is not counterbalanced: a tuning missing from one "
            "half confounds its frequency with the time it was visited")
    signed = _signed_deltas()
    visits = tuple(
        Visit(position=position, tuning_index=tuning,
              retune_delta_hz=signed[_digest_int(
                  f"{seed}|{generator_revision}|delta|{position}", len(signed))])
        for position, tuning in enumerate(order))
    used = {visit.retune_delta_hz for visit in visits}
    if used != set(signed):
        raise EnvelopeRefused(
            PLAN_SCHEDULE_UNSATISFIABLE,
            f"{sorted(set(signed) - used)} are declared and never used; §5.22 "
            "uses each delta in both directions")
    return visits


@dataclass(frozen=True)
class SpurPersistenceObservation:
    """The eight repeats a feature earned its catalogue entry with.

    Not a summary. `observed_in_repeats = 7` beside `excess_db = 12.4` is two
    numbers that can disagree about **which** repeats qualified, and a single
    maximum or mean would let one spectacular repeat launder seven absences.
    The repeats are declared and the verdict is derived from them, so all three
    of §5.22's accepted values are operative at once: exactly eight repeats, at
    least seven qualifying, and each qualifying one at least 10 dB above its
    own tuning-local median.

    `None` is a repeat where the feature was not present at all, which is a
    different fact from a small excess and is recorded as one.
    """

    tuning_id: str
    repeat_excess_db: Tuple[Optional[float], ...]

    def __post_init__(self) -> None:
        values = tuple(self.repeat_excess_db)
        object.__setattr__(self, "repeat_excess_db", values)
        if type(self.tuning_id) is not str or not self.tuning_id:
            raise EnvelopeRefused(
                PLAN_ABSENT, "a persistence observation names its tuning")
        if len(values) != PLAN_REPEATS_PER_TUNING:
            raise EnvelopeRefused(
                PLAN_REPEATS_NOT_OBSERVED,
                f"{len(values)} repeats declared against the "
                f"{PLAN_REPEATS_PER_TUNING} §5.22 pins. Seven of eight is a "
                "ratio over a fixed denominator, not over whatever was tried")
        for value in values:
            if value is not None and not _finite(value):
                raise EnvelopeRefused(
                    PLAN_QUANTITY_NOT_FINITE,
                    f"a repeat excess of {value!r} is not a measurement")

    def qualifying(self) -> int:
        """Repeats at or above the declared margin, measured per tuning because
        the noise floor is not flat across the envelope."""
        return sum(value is not None and value >= PLAN_PERSISTENCE_MARGIN_DB
                   for value in self.repeat_excess_db)

    def persistent(self) -> bool:
        return self.qualifying() >= PLAN_PERSISTENCE_REQUIRED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tuning_id": self.tuning_id,
            "repeat_excess_db": [None if v is None else float(v)
                                 for v in self.repeat_excess_db],
            "qualifying": self.qualifying(),
            "persistent": self.persistent(),
        }


@dataclass(frozen=True)
class CataloguedSpur:
    """One identified product, with what §5.21 could decide about it."""

    spur_id: str
    classification: str
    stability_class: str
    persistence: SpurPersistenceObservation

    def __post_init__(self) -> None:
        if type(self.spur_id) is not str or not self.spur_id:
            raise EnvelopeRefused(PLAN_ABSENT, "a catalogued spur needs an id")
        if type(self.persistence) is not SpurPersistenceObservation:
            raise EnvelopeRefused(
                PLAN_ABSENT,
                "a catalogued spur carries its SpurPersistenceObservation; got "
                f"{type(self.persistence).__name__}")
        if not self.persistence.persistent():
            raise EnvelopeRefused(
                PLAN_SPUR_NOT_PERSISTENT,
                f"{self.spur_id} qualified in "
                f"{self.persistence.qualifying()} of "
                f"{PLAN_REPEATS_PER_TUNING} repeats against the "
                f"{PLAN_PERSISTENCE_REQUIRED} §5.22 requires. A feature "
                "somebody saw once is not a catalogued product")
        if self.classification not in SPUR_CLASSIFICATIONS:
            raise EnvelopeRefused(
                PLAN_ABSENT,
                f"{self.classification!r} is not one of {list(SPUR_CLASSIFICATIONS)}")
        if self.stability_class not in SPUR_STABILITY_CLASSES:
            raise EnvelopeRefused(
                PLAN_ABSENT,
                f"{self.stability_class!r} is not one of {list(SPUR_STABILITY_CLASSES)}")

    @property
    def required_confidence(self) -> Optional[str]:
        """The governed level §5.22 accepts this class at, where there is one.

        `SPUR_CANDIDATE_UNRESOLVED` and `VANISHES_ON_DECLARED_TERMINATION` have
        none: §5.21 gives them no usable classification, so there is no
        confidence at which they attest to internality. They may still be
        catalogued and still be allocated -- §5.22 says any class may attest --
        and this returning None is the honest report, not an omission.
        """
        return CONFIDENCE_REQUIRED_BY_CLASSIFICATION.get(self.classification)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "spur_id": self.spur_id,
            "classification": self.classification,
            "stability_class": self.stability_class,
            "persistence": self.persistence.to_dict(),
            "required_confidence": self.required_confidence,
        }


@dataclass(frozen=True)
class EligibleSpurTrial:
    """One `(spur, tuning, epoch)` unit that can actually be produced.

    §5.22 chose the trial unit; `S * K * E` counts the unit's **cardinality**,
    which is an upper bound and not a feasibility proof. A spur is not
    observable at every tuning -- it may fold past the band edge, sit outside
    the usable half-span, or lack the termination evidence its class needs --
    and the chain it is observed on has to be one the envelope admits. Counting
    the tuples that survive all of that is the only count that says the plan can
    be executed, and the lock is what closes §5.22's option 4.
    """

    spur_id: str
    tuning_id: str
    epoch_id: str
    chain_hash: str
    stability_class: str
    signed_baseband_hz: float
    confidence: str

    def __post_init__(self) -> None:
        for name in ("spur_id", "tuning_id", "epoch_id", "chain_hash",
                     "stability_class", "confidence"):
            value = getattr(self, name)
            if type(value) is not str or not value:
                raise EnvelopeRefused(
                    PLAN_ABSENT, f"an eligible trial declares {name}")
        if self.stability_class not in SPUR_STABILITY_CLASSES:
            raise EnvelopeRefused(
                PLAN_ABSENT,
                f"{self.stability_class!r} is not one of "
                f"{list(SPUR_STABILITY_CLASSES)}")
        if self.confidence not in ACCEPTED_CONFIDENCE_LEVELS:
            raise EnvelopeRefused(
                PLAN_ABSENT,
                f"{self.confidence!r} is not one of "
                f"{list(ACCEPTED_CONFIDENCE_LEVELS)}. Neither level is "
                "reachable by asserting a third")
        if not _finite(self.signed_baseband_hz):
            raise EnvelopeRefused(
                PLAN_QUANTITY_NOT_FINITE,
                f"signed_baseband_hz is {self.signed_baseband_hz!r}")
        # Signed, on [-f_s/2, +f_s/2), and outside the folding guard. An
        # unsigned magnitude cannot tell +1 from -1 at all, and a feature within
        # 5% of either edge reverses its apparent direction of travel.
        if abs(self.signed_baseband_hz) > usable_half_span_hz():
            raise EnvelopeRefused(
                PLAN_TRIAL_NOT_ELIGIBLE,
                f"{self.spur_id} at {self.signed_baseband_hz:.0f} Hz is inside "
                f"the {PLAN_BAND_EDGE_EXCLUSION:.0%} band-edge exclusion, where "
                "a folded feature reverses its apparent direction of travel")

    def key(self) -> Tuple[str, str, str]:
        """The trial unit §5.22 named. The chain and the offset are facts
        *about* the unit, not part of its identity, so two records of one
        `(spur, tuning, epoch)` do not become two trials."""
        return (self.spur_id, self.tuning_id, self.epoch_id)

    def to_dict(self) -> Dict[str, Any]:
        data = {field: getattr(self, field) for field in self.__dataclass_fields__}
        data["signed_baseband_hz"] = float(self.signed_baseband_hz)
        return data


@dataclass(frozen=True)
class SpurAllocation:
    """The catalogue, the epochs, and how the trials are drawn across classes.

    §5.22 makes two hard requirements and this carries both. **Feasibility is
    checked before the corpus opens** -- distinct `(spur, tuning, epoch)`
    combinations must reach the required trials given the actual S, K and E, so
    a small catalogue is discovered before a lock exists rather than after four
    thousand windows. And **the allocation over spurs is declared**, including
    how many windows come from each stability class, because an allocation that
    happened to under-sample `SESSION_SCOPED` would test the stratum where it is
    easiest.
    """

    catalogue: Tuple[CataloguedSpur, ...]
    epochs: int
    per_stability_class: Tuple[Tuple[str, int], ...]
    eligible_trials: Tuple[EligibleSpurTrial, ...]
    selected_trials: Tuple[Tuple[str, str, str], ...]
    selection_seed: int
    selection_revision: str = SELECTION_REVISION

    def __post_init__(self) -> None:
        catalogue = tuple(self.catalogue)
        object.__setattr__(self, "eligible_trials", tuple(self.eligible_trials))
        object.__setattr__(self, "selected_trials",
                           tuple(tuple(key) for key in self.selected_trials))
        allocation = tuple((str(name), int(count))
                           for name, count in self.per_stability_class)
        object.__setattr__(self, "catalogue", catalogue)
        object.__setattr__(self, "per_stability_class", allocation)
        for spur in catalogue:
            if type(spur) is not CataloguedSpur:
                raise EnvelopeRefused(
                    PLAN_ABSENT,
                    f"the catalogue holds CataloguedSpur; got {type(spur).__name__}")
        if not catalogue:
            raise EnvelopeRefused(
                PLAN_SPUR_CATALOGUE_ABSENT,
                "RECEIVER_SPURS is planned against a catalogue of identified "
                "products. An empty catalogue identifies nothing, and §5.21's "
                "method run by nobody identifies nothing either")
        if len({spur.spur_id for spur in catalogue}) != len(catalogue):
            raise EnvelopeRefused(
                PLAN_ABSENT, "a spur catalogued twice is one product, counted twice")
        if type(self.epochs) is not int or self.epochs < 1:
            raise EnvelopeRefused(
                PLAN_QUANTITY_NOT_FINITE, f"epochs is {self.epochs!r}")
        present = {spur.stability_class for spur in catalogue}
        declared = {name for name, _count in allocation}
        if declared != present:
            raise EnvelopeRefused(
                PLAN_SPUR_ALLOCATION_UNDECLARED,
                f"the catalogue contains {sorted(present)} and the allocation "
                f"declares {sorted(declared)}. §5.22 requires the allocation to "
                "draw from every class the catalogue contains, with the "
                "per-class counts declared in advance")
        for name, count in allocation:
            if count < 1:
                raise EnvelopeRefused(
                    PLAN_SPUR_ALLOCATION_UNDECLARED,
                    f"{name} is allocated {count}; a class present in the "
                    "catalogue and allocated nothing is the under-sampling "
                    "§5.22 names")
        by_id = {spur.spur_id: spur for spur in catalogue}
        epoch_ids = set()
        facts: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        for trial in self.eligible_trials:
            if type(trial) is not EligibleSpurTrial:
                raise EnvelopeRefused(
                    PLAN_ABSENT,
                    f"eligible trials are EligibleSpurTrial; got "
                    f"{type(trial).__name__}")
            spur = by_id.get(trial.spur_id)
            if spur is None:
                raise EnvelopeRefused(
                    PLAN_TRIAL_NOT_ELIGIBLE,
                    f"{trial.spur_id} is not in this catalogue")
            if trial.stability_class != spur.stability_class:
                raise EnvelopeRefused(
                    PLAN_TRIAL_NOT_ELIGIBLE,
                    f"{trial.spur_id} is {spur.stability_class} in the "
                    f"catalogue and {trial.stability_class} in this trial")
            required = spur.required_confidence
            if required is not None and trial.confidence != required:
                raise EnvelopeRefused(
                    PLAN_TRIAL_NOT_ELIGIBLE,
                    f"{trial.spur_id} is {spur.classification} and needs "
                    f"{required}; this trial declares {trial.confidence}")
            epoch_ids.add(trial.epoch_id)
            # A trial identity must functionally determine its facts. Two
            # records of one (spur, tuning, epoch) that disagree about the
            # chain, the offset, the stability class or the confidence are not
            # one trial seen twice -- and collapsing them into a single counted
            # unit would let `distinct_trial_units` certify a set that never
            # cohered.
            previous = facts.get(trial.key())
            if previous is not None and previous != trial.to_dict():
                raise EnvelopeRefused(
                    PLAN_TRIAL_IDENTITY_AMBIGUOUS,
                    f"{trial.key()} is declared twice with different facts. One "
                    "identity names one trial, or the count that proves "
                    "feasibility is counting something that does not exist")
            facts[trial.key()] = trial.to_dict()
        # Canonical storage, not merely a canonical count. "It counts once" is
        # not the property: a duplicate left in the stored field gives one
        # authorised set two identities and two digests, which is the same
        # defect the member sort fixed one layer up.
        seen: Dict[str, EligibleSpurTrial] = {}
        for trial in self.eligible_trials:
            seen.setdefault(_canonical_bytes(trial.to_dict()).decode(), trial)
        object.__setattr__(
            self, "eligible_trials",
            tuple(sorted(seen.values(),
                         key=lambda t: (t.spur_id, t.tuning_id, t.epoch_id,
                                        t.chain_hash))))
        if self.selected_trials:
            regenerated = select_spur_trials(
                eligible=self.eligible_trials, seed=self.selection_seed,
                required=len(self.selected_trials),
                selection_revision=self.selection_revision)
            if tuple(self.selected_trials) != regenerated:
                raise EnvelopeRefused(
                    PLAN_SELECTION_NOT_REPRODUCIBLE,
                    "the declared selection is not what this eligible set, "
                    "this seed and this revision produce. A subset chosen by "
                    "hand is a subset that could have been chosen afterwards")
        if len(epoch_ids) > self.epochs:
            raise EnvelopeRefused(
                PLAN_TRIAL_NOT_ELIGIBLE,
                f"{len(epoch_ids)} distinct epochs appear in the trials and "
                f"{self.epochs} are declared")

    @property
    def cardinality_bound(self) -> int:
        """`S * K * E`: how many `(spur, tuning, epoch)` units could exist.

        An **upper bound**, kept as a cheap preliminary refusal and never as the
        feasibility proof. A catalogue that cannot reach the bound even in
        principle is refused without enumerating anything.
        """
        return len(self.catalogue) * PLAN_TUNING_COUNT * self.epochs

    @property
    def distinct_trial_units(self) -> int:
        """The units that survived every eligibility check, counted."""
        return len({trial.key() for trial in self.eligible_trials})

    def allocated_trials(self) -> int:
        return sum(count for _name, count in self.per_stability_class)

    def feasible_for(self, required_trials: int) -> bool:
        """Enumerated, not multiplied. Both must hold, and the second is the
        one that says the plan can actually be executed."""
        return (self.cardinality_bound >= required_trials
                and self.distinct_trial_units >= required_trials)

    def eligible_chain_hashes(self) -> frozenset:
        return frozenset(trial.chain_hash for trial in self.eligible_trials)

    def eligible_tuning_ids(self) -> frozenset:
        return frozenset(trial.tuning_id for trial in self.eligible_trials)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "catalogue": [spur.to_dict() for spur in self.catalogue],
            "catalogue_size": len(self.catalogue),
            "epochs": self.epochs,
            "cardinality_bound": self.cardinality_bound,
            "distinct_trial_units": self.distinct_trial_units,
            # The units themselves are RETAINED, on `eligible_trials`; this is
            # the compact summary and exposes their digest instead of 5 561
            # rows, which would otherwise put half a megabyte of JSON through
            # every digest() call and every status report. A digest standing
            # alone would be a commitment rather than a declaration: the lock
            # could no longer say which units were authorised, whether an
            # executed trial is one of them, or reproduce the digest from
            # anything it holds. It can, because it holds them.
            "eligible_trials_digest": self.eligible_trials_digest(),
            "selected_trials_digest": self.selected_trials_digest(),
            "selected_count": len(self.selected_trials),
            "selection_seed": self.selection_seed,
            "selection_revision": self.selection_revision,
            "per_stability_class": [list(pair) for pair in self.per_stability_class],
            "generalises_across_spur_types": False,
            "generalises_across_receiver_units": False,
        }

    def selected_trials_digest(self) -> str:
        material = _canonical_bytes([list(key) for key in self.selected_trials])
        return f"blake2s:{hashlib.blake2s(material, digest_size=16).hexdigest()}"

    def eligible_trials_digest(self) -> str:
        rows = sorted((trial.to_dict() for trial in self.eligible_trials),
                      key=lambda row: (row["spur_id"], row["tuning_id"],
                                       row["epoch_id"], row["chain_hash"]))
        material = _canonical_bytes(rows)
        return f"blake2s:{hashlib.blake2s(material, digest_size=16).hexdigest()}"


@dataclass(frozen=True)
class StratumTrialPlan:
    """How many trials one stratum contributes, and where they come from.

    A visit annotation is not a trial allocation. The first implementation
    scheduled 192 visits and said nothing about the 16 683 captured windows the
    corpus is made of, so the constants `R = 8` and `7/8` governed no repeated
    observation and the totals reconciled with nothing.
    """

    stratum: str
    source: str
    trials: int
    chain_hashes: Tuple[str, ...]
    per_visit: Tuple[Tuple[int, int], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "chain_hashes", tuple(self.chain_hashes))
        object.__setattr__(self, "per_visit",
                           tuple((int(p), int(n)) for p, n in self.per_visit))
        if type(self.stratum) is not str or not self.stratum:
            raise EnvelopeRefused(PLAN_ABSENT, "a trial plan names its stratum")
        if self.source not in TRIAL_SOURCES:
            raise EnvelopeRefused(
                PLAN_ABSENT,
                f"{self.source!r} is not one of {list(TRIAL_SOURCES)}")
        if type(self.trials) is not int or self.trials < 1:
            raise EnvelopeRefused(
                PLAN_QUANTITY_NOT_FINITE, f"{self.stratum}: trials is {self.trials!r}")
        if self.source == CAPTURED:
            if not self.chain_hashes:
                raise EnvelopeRefused(
                    PLAN_ABSENT,
                    f"{self.stratum} is captured and names no chain. A captured "
                    "window came through a declared chain or it is not corpus")
            if sum(count for _p, count in self.per_visit) != self.trials:
                raise EnvelopeRefused(
                    PLAN_TRIALS_DO_NOT_RECONCILE,
                    f"{self.stratum}: per-visit counts sum to "
                    f"{sum(c for _p, c in self.per_visit)} against {self.trials} "
                    "declared trials")
            if any(count < 0 for _p, count in self.per_visit):
                raise EnvelopeRefused(
                    PLAN_QUANTITY_NOT_FINITE,
                    f"{self.stratum}: a negative window count is not a count")
        elif self.per_visit or self.chain_hashes:
            raise EnvelopeRefused(
                PLAN_ABSENT,
                f"{self.stratum} is synthetic: it has no visits and no chain, "
                "because a regenerated window came through no instrument")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stratum": self.stratum, "source": self.source,
            "trials": self.trials, "chain_hashes": list(self.chain_hashes),
            "per_visit": [list(pair) for pair in self.per_visit],
        }


@dataclass(frozen=True)
class CapturePlanDeclaration:
    """The sampled distribution, frozen before the corpus that samples it.

    **The bands, tunings and schedule are materialized, not implied.** A seed is
    provenance: it determines a plan only alongside the algorithm that consumed
    it, so a generator revised after the lock was opened would turn the same
    seed into a different plan with nothing in the lock disagreeing. Holding
    them, and regenerating to compare byte for byte, makes a forged plan and a
    drifted generator one failure caught by one check.

    **And it accounts for every trial.** Twelve strata, each with a declared
    count and a declared source; the captured ones with the chains they come
    through and the visits they come from; `RECEIVER_SPURS` with the catalogue
    and epochs its feasibility depends on. A plan that scheduled visits and
    never mentioned a trial would let a lock open before the corpus it freezes
    had been described -- and the lock is what closes §5.22's option 4.
    """

    seed: int
    schedule_generator_revision: str
    bands: Tuple[Band, ...]
    tunings: Tuple[Tuning, ...]
    schedule: Tuple[Visit, ...]
    trial_plans: Tuple[StratumTrialPlan, ...]
    spur_allocation: Optional[SpurAllocation]
    reference_hz: float
    reference_ppm: float

    def __post_init__(self) -> None:
        # Converted first, for the same reason the envelope converts first.
        for name, expected in (("bands", Band), ("tunings", Tuning),
                               ("schedule", Visit),
                               ("trial_plans", StratumTrialPlan)):
            value = tuple(getattr(self, name))
            object.__setattr__(self, name, value)
            for element in value:
                if type(element) is not expected:
                    raise EnvelopeRefused(
                        PLAN_ABSENT,
                        f"{name} holds {expected.__name__}; got "
                        f"{type(element).__name__}")
        if (self.spur_allocation is not None
                and type(self.spur_allocation) is not SpurAllocation):
            raise EnvelopeRefused(
                PLAN_ABSENT,
                f"spur_allocation is a SpurAllocation or None; got "
                f"{type(self.spur_allocation).__name__}")
        # The complete check, at construction. `harmonic_cap` refuses a
        # non-positive reference, and a declaration only found invalid when
        # something later asks it a question is not self-validating.
        self.harmonic_cap

        # Every declared constant governs a declared act, checked before the
        # regeneration comparison so that a plan wrong in two ways reports the
        # substantive fault rather than "it does not regenerate".
        ceiling = maximum_retune_delta_hz()
        declared = {abs(v.retune_delta_hz) for v in self.schedule}
        over = deltas_over_ceiling(PLAN_RETUNE_DELTAS_HZ, ceiling)
        if not declared <= set(PLAN_RETUNE_DELTAS_HZ):
            raise EnvelopeRefused(
                PLAN_RETUNE_NOT_DECLARED,
                f"{sorted(declared - set(PLAN_RETUNE_DELTAS_HZ))} are not "
                f"among the declared deltas {list(PLAN_RETUNE_DELTAS_HZ)}")
        # Over the DECLARED CONSTANTS, not over the schedule. A schedule delta
        # above the ceiling is also not one of the declared three, so the
        # membership check above always reaches it first and a ceiling test over
        # the schedule discriminates nothing -- which a control proved by
        # failing zero tests. The real invariant is that §5.22's chosen deltas
        # fit the slope bound the band-edge exclusion leaves.
        if over:
            raise EnvelopeRefused(
                PLAN_RETUNE_NOT_DECLARED,
                f"declared deltas {over} exceed {ceiling:.0f} Hz, above which a "
                f"slope-{PLAN_MAX_MIXING_SLOPE} product leaves the usable "
                "half-span, folds, and reverses its apparent direction of travel")
        by_id = {band.band_id: band for band in self.bands}
        if len(by_id) != len(self.bands):
            raise EnvelopeRefused(PLAN_ABSENT, "a band declared twice")
        for tuning in self.tunings:
            band = by_id.get(tuning.band_id)
            if band is None:
                raise EnvelopeRefused(
                    PLAN_TUNING_OUTSIDE_BAND,
                    f"tuning {tuning.tuning_index} names undeclared band "
                    f"{tuning.band_id!r}")
            if not band.contains_tuning(tuning.center_frequency_hz):
                raise EnvelopeRefused(
                    PLAN_TUNING_OUTSIDE_BAND,
                    f"tuning {tuning.tuning_index} at "
                    f"{tuning.center_frequency_hz:.0f} Hz cannot be retuned "
                    f"with the whole analysis span inside {band.band_id}")
        if len({t.center_frequency_hz for t in self.tunings}) != len(self.tunings):
            raise EnvelopeRefused(
                PLAN_ABSENT, "two tunings at one frequency are one tuning")

        regenerated_tunings = generate_tunings(
            seed=self.seed, bands=self.bands,
            generator_revision=self.schedule_generator_revision)
        if _canonical_bytes([t.to_dict() for t in self.tunings]) != \
                _canonical_bytes([t.to_dict() for t in regenerated_tunings]):
            raise EnvelopeRefused(
                PLAN_SCHEDULE_NOT_REPRODUCIBLE,
                "the declared tunings are not what this seed, these bands and "
                "this generator revision produce")
        regenerated = generate_visit_schedule(
            seed=self.seed, tunings=self.tunings,
            generator_revision=self.schedule_generator_revision)
        if _canonical_bytes([v.to_dict() for v in self.schedule]) != \
                _canonical_bytes([v.to_dict() for v in regenerated]):
            raise EnvelopeRefused(
                PLAN_SCHEDULE_NOT_REPRODUCIBLE,
                "the declared schedule is not what this seed and this "
                "generator revision produce. Either it was written by hand or "
                "the generator has moved, and from the reader's side those are "
                "the same failure")

        positions = {visit.position for visit in self.schedule}
        for plan in self.trial_plans:
            stray = sorted({p for p, _n in plan.per_visit} - positions)
            if stray:
                raise EnvelopeRefused(
                    PLAN_TRIALS_DO_NOT_RECONCILE,
                    f"{plan.stratum} allocates trials to visits {stray}, which "
                    "the schedule does not contain")
        if len({plan.stratum for plan in self.trial_plans}) != len(self.trial_plans):
            raise EnvelopeRefused(
                PLAN_TRIALS_DO_NOT_RECONCILE, "a stratum planned twice")
        spurs = [p for p in self.trial_plans if p.stratum == "RECEIVER_SPURS"]
        if spurs:
            if self.spur_allocation is None:
                raise EnvelopeRefused(
                    PLAN_SPUR_CATALOGUE_ABSENT,
                    "RECEIVER_SPURS is planned and no catalogue is declared. "
                    "§5.22 requires feasibility established before the corpus "
                    "opens, and the lock is what closes option 4")
            required = spurs[0].trials
            if not self.spur_allocation.feasible_for(required):
                raise EnvelopeRefused(
                    PLAN_SPUR_NOT_FEASIBLE,
                    f"{self.spur_allocation.distinct_trial_units} enumerated "
                    f"(spur, tuning, epoch) units survive every eligibility "
                    f"check against {required} trials, with an S*K*E bound of "
                    f"{self.spur_allocation.cardinality_bound}. Discovered "
                    "before a lock exists, which is the whole point of "
                    "checking it here rather than after four thousand windows")
            declared_tunings = {tuning.tuning_id for tuning in self.tunings}
            stray = sorted(self.spur_allocation.eligible_tuning_ids()
                           - declared_tunings)
            if stray:
                raise EnvelopeRefused(
                    PLAN_TRIAL_NOT_ELIGIBLE,
                    f"{len(stray)} eligible trial(s) name tunings this plan "
                    f"does not declare, beginning {stray[:3]}")
            if self.spur_allocation.cardinality_bound < required:
                raise EnvelopeRefused(
                    PLAN_SPUR_NOT_FEASIBLE,
                    f"S*K*E = {self.spur_allocation.cardinality_bound} cannot "
                    f"reach {required} even in principle")
            if len(self.spur_allocation.selected_trials) != required:
                raise EnvelopeRefused(
                    PLAN_SELECTION_NOT_REPRODUCIBLE,
                    f"{len(self.spur_allocation.selected_trials)} units are "
                    f"selected against {required} trials the stratum plans. "
                    "The selection is the sample, so it is exact in both "
                    "directions")
            if self.spur_allocation.allocated_trials() != required:
                raise EnvelopeRefused(
                    PLAN_SPUR_ALLOCATION_UNDECLARED,
                    f"the per-class allocation sums to "
                    f"{self.spur_allocation.allocated_trials()} against "
                    f"{required} declared trials")
        elif self.spur_allocation is not None:
            raise EnvelopeRefused(
                PLAN_SPUR_CATALOGUE_ABSENT,
                "a spur catalogue is declared and RECEIVER_SPURS is not "
                "planned. §5.22's option 4 drops the stratum; it does not keep "
                "the catalogue and drop the trials")

    @property
    def harmonic_cap(self) -> int:
        """Derived from the declared tolerance, never transcribed."""
        return harmonic_cap(reference_hz=self.reference_hz,
                            reference_ppm=self.reference_ppm)

    def trials_for(self, stratum: str) -> Optional[int]:
        for plan in self.trial_plans:
            if plan.stratum == stratum:
                return plan.trials
        return None

    def planned_strata(self) -> Tuple[str, ...]:
        return tuple(plan.stratum for plan in self.trial_plans)

    def captured_strata(self) -> Tuple[str, ...]:
        return tuple(p.stratum for p in self.trial_plans if p.source == CAPTURED)

    def total_trials(self) -> int:
        return sum(plan.trials for plan in self.trial_plans)

    def allocated_chain_hashes(self) -> frozenset:
        """Every chain the plan says a window will come through.

        The spur trials are in here too: a chain that appears only in the
        eligibility enumeration is still a chain this plan intends to sample
        on, and leaving it out would let it escape the envelope reconciliation.
        """
        from_strata = {h for plan in self.trial_plans for h in plan.chain_hashes}
        from_spurs = (frozenset() if self.spur_allocation is None
                      else self.spur_allocation.eligible_chain_hashes())
        return frozenset(from_strata | set(from_spurs))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": CAPTURE_PLAN_SCHEMA,
            "seed": self.seed,
            "schedule_generator_revision": self.schedule_generator_revision,
            "bands": [band.to_dict() for band in self.bands],
            "tunings": [tuning.to_dict() for tuning in self.tunings],
            "schedule": [visit.to_dict() for visit in self.schedule],
            "trial_plans": [plan.to_dict() for plan in self.trial_plans],
            "spur_allocation": (None if self.spur_allocation is None
                                else self.spur_allocation.to_dict()),
            "total_trials": self.total_trials(),
            "tuning_count": PLAN_TUNING_COUNT,
            "repeats_per_tuning": PLAN_REPEATS_PER_TUNING,
            "persistence_required": PLAN_PERSISTENCE_REQUIRED,
            "visits_per_tuning": PLAN_VISITS_PER_TUNING,
            "minimum_separation": PLAN_MINIMUM_SEPARATION,
            "retune_deltas_hz": list(PLAN_RETUNE_DELTAS_HZ),
            "maximum_retune_delta_hz": maximum_retune_delta_hz(),
            "max_mixing_slope": PLAN_MAX_MIXING_SLOPE,
            "slope_tolerance": PLAN_SLOPE_TOLERANCE,
            "persistence_margin_db": PLAN_PERSISTENCE_MARGIN_DB,
            "band_edge_exclusion": PLAN_BAND_EDGE_EXCLUSION,
            "usable_half_span_hz": usable_half_span_hz(),
            "reference_hz": float(self.reference_hz),
            "reference_ppm": float(self.reference_ppm),
            "harmonic_cap": self.harmonic_cap,
            "accepted_confidence_levels": list(ACCEPTED_CONFIDENCE_LEVELS),
        }

    def digest(self) -> str:
        material = json.dumps(self.to_dict(), sort_keys=True,
                              separators=(",", ":")).encode("utf-8")
        return f"blake2s:{hashlib.blake2s(material, digest_size=16).hexdigest()}"


def _canonical_bytes(payload: Any) -> bytes:
    """The bytes a regenerated structure is compared as."""
    return json.dumps(payload, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def allocations_outside_envelope(plan: CapturePlanDeclaration,
                                 envelope: InstrumentChainEnvelope) -> Tuple[str, ...]:
    """Allocated chains the envelope does not admit.

    A cross-object check, so it lives where both objects do rather than inside
    either. `CapturePlanDeclaration` cannot run it in `__post_init__` because it
    does not hold the envelope, and giving it one to satisfy a check would make
    two declarations into one again.
    """
    admissible = envelope.admissible_chain_hashes()
    return tuple(sorted(h for h in plan.allocated_chain_hashes()
                        if h not in admissible))


def declare_capture_plan(*, seed: int, envelope: InstrumentChainEnvelope,
                         bands: Any, trial_plans: Any, reference_hz: float,
                         reference_ppm: float,
                         spur_allocation: Optional[SpurAllocation] = None,
                         generator_revision: str = SCHEDULE_GENERATOR_REVISION,
                         ) -> CapturePlanDeclaration:
    """Declare a plan against an envelope, or refuse and say which part is not one."""
    if type(envelope) is not InstrumentChainEnvelope:
        raise EnvelopeRefused(
            ENVELOPE_ABSENT,
            "a plan is declared against an InstrumentChainEnvelope; got "
            f"{type(envelope).__name__}")
    bands = tuple(bands)
    tunings = generate_tunings(seed=seed, bands=bands,
                               generator_revision=generator_revision)
    plan = CapturePlanDeclaration(
        seed=seed, schedule_generator_revision=generator_revision,
        bands=bands, tunings=tunings,
        schedule=generate_visit_schedule(seed=seed, tunings=tunings,
                                         generator_revision=generator_revision),
        trial_plans=tuple(trial_plans), spur_allocation=spur_allocation,
        reference_hz=reference_hz, reference_ppm=reference_ppm)
    outside = allocations_outside_envelope(plan, envelope)
    if outside:
        raise EnvelopeRefused(
            PLAN_ALLOCATION_OUTSIDE_ENVELOPE,
            f"{len(outside)} allocated chain(s) are not declared members of "
            "the envelope this plan is declared against")
    return plan
