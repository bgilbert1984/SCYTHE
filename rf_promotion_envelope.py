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

from rf_promotion_geometry import (
    PROMOTION_WINDOW_SAMPLES,
    PROMOTION_SAMPLE_RATE_HZ,
    promotion_geometry_deviations,
)
from rf_signal_chain_identity import signal_chain_hash

ENVELOPE_SCHEMA = "scythe.rf-instrument-chain-envelope.v1"
CAPTURE_PLAN_SCHEMA = "scythe.rf-capture-plan.v1"

# The algorithm that turns a seed into a schedule. Frozen beside the seed in
# every declaration, because a seed alone determines nothing: revise the
# generator and the same seed produces a different plan.
SCHEDULE_GENERATOR_REVISION = "rf-visit-schedule.v1"


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
PLAN_ABSENT = "PLAN_ABSENT"
PLAN_SCHEDULE_NOT_REPRODUCIBLE = "PLAN_SCHEDULE_NOT_REPRODUCIBLE"
PLAN_SCHEDULE_UNSATISFIABLE = "PLAN_SCHEDULE_UNSATISFIABLE"
PLAN_ALLOCATION_INCOMPLETE = "PLAN_ALLOCATION_INCOMPLETE"
PLAN_ALLOCATION_OUTSIDE_ENVELOPE = "PLAN_ALLOCATION_OUTSIDE_ENVELOPE"
PLAN_QUANTITY_NOT_FINITE = "PLAN_QUANTITY_NOT_FINITE"

ENVELOPE_REFUSALS: Tuple[str, ...] = (
    ENVELOPE_ABSENT, ENVELOPE_ADMITS_NOTHING, ENVELOPE_GEOMETRY_REFUSED,
    ENVELOPE_QUANTITY_NOT_FINITE, ENVELOPE_DECLARATION_REPEATED,
    ENVELOPE_DECLARATION_COLLAPSED, ENVELOPE_MULTIPLE_RECEIVERS,
    ENVELOPE_AUTHORITY_NOT_DECLARED, PLAN_ABSENT,
    PLAN_SCHEDULE_NOT_REPRODUCIBLE, PLAN_SCHEDULE_UNSATISFIABLE,
    PLAN_ALLOCATION_INCOMPLETE, PLAN_ALLOCATION_OUTSIDE_ENVELOPE,
    PLAN_QUANTITY_NOT_FINITE,
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
        if isinstance(self.extension_mm, float) and not _finite(self.extension_mm):
            raise EnvelopeRefused(
                ENVELOPE_QUANTITY_NOT_FINITE,
                f"extension_mm is {self.extension_mm!r}")

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
# here, and the one that looks like a choice -- the harmonic cap -- is computed
# from the declared crystal tolerance rather than transcribed from §5.22's
# table. `test_rf_promotion_envelope` reproduces all three published rows from
# this arithmetic, which is the check that the table and the code agree.
PLAN_TUNING_COUNT = 64                    # K
PLAN_REPEATS_PER_TUNING = 8               # R
PLAN_PERSISTENCE_REQUIRED = 7             # of R
PLAN_VISITS_PER_TUNING = 3                # each LO setting visited >= 3 times
PLAN_MINIMUM_SEPARATION = 10              # separated by >= 10 other settings
PLAN_RETUNE_DELTAS_HZ: Tuple[float, ...] = (50_000.0, 100_000.0, 200_000.0)
PLAN_MAX_MIXING_SLOPE = 3                 # |s| <= 3 inside the half-span
PLAN_SLOPE_TOLERANCE = 0.01
PLAN_PERSISTENCE_MARGIN_DB = 10.0
PLAN_BAND_EDGE_EXCLUSION = 0.05           # folded features reverse direction
PLAN_REFERENCE_MATCH_SPAN_FRACTION = 0.005

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

# A bound on retries, not a tuning knob. The constraint is satisfiable and this
# says so loudly if it ever stops being: a generator that quietly relaxed a
# separation rule would produce a schedule that looks like the declared design
# and is not one.
_SCHEDULE_ATTEMPT_CEILING = 4096


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
class Visit:
    """One tuning, visited once, at one place in the session sequence.

    `position` is kept explicitly rather than left to the index of a list.
    §5.22 pins randomised *and counterbalanced* order precisely so that retune
    position and elapsed time are two dimensions, and a structure that implies
    the second from the first re-collapses the confound the randomisation exists
    to break.
    """

    position: int
    tuning_index: int


def _deterministic_permutation(count: int, *, material: str) -> Tuple[int, ...]:
    """A permutation fixed by its material, and by nothing else.

    Not `random.shuffle`. The plan promises byte-for-byte regeneration, and a
    promise resting on the stability of an interpreter's RNG across versions is
    a promise about CPython rather than about this repository. A digest-ordered
    sort is specified entirely by `SCHEDULE_GENERATOR_REVISION`.
    """
    keyed = sorted(
        (hashlib.blake2s(f"{material}|{index}".encode(), digest_size=16).digest(),
         index)
        for index in range(count))
    return tuple(index for _key, index in keyed)


def _separations_hold(schedule: Tuple[int, ...]) -> bool:
    """Every repeat of one tuning is >= PLAN_MINIMUM_SEPARATION apart.

    A non-stationary emitter is then caught between visits rather than fitted
    through them, which is what the separation is for.
    """
    last: Dict[int, int] = {}
    for position, tuning in enumerate(schedule):
        previous = last.get(tuning)
        if previous is not None and position - previous <= PLAN_MINIMUM_SEPARATION:
            return False
        last[tuning] = position
    return True


def _counterbalanced(schedule: Tuple[int, ...]) -> bool:
    """Every tuning appears in both halves of the session sequence."""
    half = len(schedule) // 2
    first = set(schedule[:half])
    second = set(schedule[half:])
    return first == second == set(range(PLAN_TUNING_COUNT))


def generate_visit_schedule(*, seed: int,
                            generator_revision: str = SCHEDULE_GENERATOR_REVISION,
                            ) -> Tuple[Visit, ...]:
    """The declared schedule, materialized from a seed and this algorithm.

    Rounds of permuted tunings, retried at the round boundary until the
    separation rule holds -- refusing rather than relaxing if it cannot, because
    a silently relaxed schedule is indistinguishable from the declared one when
    read back.
    """
    if generator_revision != SCHEDULE_GENERATOR_REVISION:
        raise EnvelopeRefused(
            PLAN_SCHEDULE_NOT_REPRODUCIBLE,
            f"{generator_revision!r} is not {SCHEDULE_GENERATOR_REVISION!r}; "
            "this module implements one generator and cannot reproduce "
            "another. A seed without its algorithm determines no schedule")
    if type(seed) is not int:
        raise EnvelopeRefused(
            PLAN_QUANTITY_NOT_FINITE,
            f"the seed is an int; got {type(seed).__name__}")
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
    schedule = tuple(order)
    if not _counterbalanced(schedule):
        raise EnvelopeRefused(
            PLAN_SCHEDULE_UNSATISFIABLE,
            "the schedule is not counterbalanced: a tuning missing from one "
            "half confounds its frequency with the time it was visited")
    return tuple(Visit(position=position, tuning_index=tuning)
                 for position, tuning in enumerate(schedule))


@dataclass(frozen=True)
class VisitAllocation:
    """What one visit is for, declared in advance of the visit.

    §5.22 requires the per-class counts declared in advance, which is only
    meaningful if each visit's purpose is fixed before it happens. An allocation
    written afterwards is a description of what was collected.
    """

    position: int
    chain_hash: str
    stratum: str
    block: str
    condition: str
    confidence_requirement: str

    def __post_init__(self) -> None:
        if type(self.position) is not int:
            raise EnvelopeRefused(
                PLAN_QUANTITY_NOT_FINITE,
                f"position is an int; got {type(self.position).__name__}")
        for name in ("chain_hash", "stratum", "block", "condition",
                     "confidence_requirement"):
            value = getattr(self, name)
            if type(value) is not str or not value:
                raise EnvelopeRefused(
                    PLAN_ABSENT, f"{name} must be a declared non-empty string")
        if self.confidence_requirement not in ACCEPTED_CONFIDENCE_LEVELS:
            raise EnvelopeRefused(
                PLAN_ABSENT,
                f"{self.confidence_requirement!r} is not one of "
                f"{list(ACCEPTED_CONFIDENCE_LEVELS)}; §5.22 accepts two levels "
                "and neither is reachable by naming a third")

    def to_dict(self) -> Dict[str, Any]:
        return {field: getattr(self, field) for field in self.__dataclass_fields__}


@dataclass(frozen=True)
class CapturePlanDeclaration:
    """The sampled distribution, frozen before the corpus that samples it.

    **The schedule is materialized, not implied.** A seed is provenance: it
    determines a schedule only alongside the algorithm that consumed it, so a
    generator revised after the lock was opened would turn the same seed into a
    different plan with nothing in the lock disagreeing. Holding both, and
    regenerating to compare byte for byte, makes a forged schedule and a drifted
    generator one failure caught by one check.
    """

    seed: int
    schedule_generator_revision: str
    schedule: Tuple[Visit, ...]
    allocations: Tuple[VisitAllocation, ...]
    reference_hz: float
    reference_ppm: float

    def __post_init__(self) -> None:
        # Converted first, for the same reason the envelope converts first.
        schedule = tuple(self.schedule)
        allocations = tuple(self.allocations)
        object.__setattr__(self, "schedule", schedule)
        object.__setattr__(self, "allocations", allocations)
        for item, expected in ((schedule, Visit), (allocations, VisitAllocation)):
            for element in item:
                if type(element) is not expected:
                    raise EnvelopeRefused(
                        PLAN_ABSENT,
                        f"expected {expected.__name__}; got "
                        f"{type(element).__name__}")
        for name in ("reference_hz", "reference_ppm"):
            if not _finite(getattr(self, name)):
                raise EnvelopeRefused(
                    PLAN_QUANTITY_NOT_FINITE,
                    f"{name} is {getattr(self, name)!r}")
        regenerated = generate_visit_schedule(
            seed=self.seed,
            generator_revision=self.schedule_generator_revision)
        if _canonical_schedule_bytes(schedule) != _canonical_schedule_bytes(regenerated):
            raise EnvelopeRefused(
                PLAN_SCHEDULE_NOT_REPRODUCIBLE,
                "the declared schedule is not what this seed and this "
                "generator revision produce. Either the schedule was written "
                "by hand or the generator has moved, and the two are the same "
                "failure from the reader's side")
        positions = [allocation.position for allocation in allocations]
        if sorted(positions) != [visit.position for visit in schedule]:
            raise EnvelopeRefused(
                PLAN_ALLOCATION_INCOMPLETE,
                f"{len(allocations)} allocations for {len(schedule)} visits, "
                "or positions that do not correspond. Every visit is allocated "
                "in advance or the plan is a description written afterwards")

    @property
    def harmonic_cap(self) -> int:
        """Derived from the declared tolerance, never transcribed."""
        return harmonic_cap(reference_hz=self.reference_hz,
                            reference_ppm=self.reference_ppm)

    def allocated_chain_hashes(self) -> frozenset:
        return frozenset(a.chain_hash for a in self.allocations)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": CAPTURE_PLAN_SCHEMA,
            "seed": self.seed,
            "schedule_generator_revision": self.schedule_generator_revision,
            "schedule": [[v.position, v.tuning_index] for v in self.schedule],
            "allocations": [a.to_dict() for a in self.allocations],
            "tuning_count": PLAN_TUNING_COUNT,
            "repeats_per_tuning": PLAN_REPEATS_PER_TUNING,
            "persistence_required": PLAN_PERSISTENCE_REQUIRED,
            "visits_per_tuning": PLAN_VISITS_PER_TUNING,
            "minimum_separation": PLAN_MINIMUM_SEPARATION,
            "retune_deltas_hz": list(PLAN_RETUNE_DELTAS_HZ),
            "max_mixing_slope": PLAN_MAX_MIXING_SLOPE,
            "slope_tolerance": PLAN_SLOPE_TOLERANCE,
            "persistence_margin_db": PLAN_PERSISTENCE_MARGIN_DB,
            "band_edge_exclusion": PLAN_BAND_EDGE_EXCLUSION,
            "reference_hz": float(self.reference_hz),
            "reference_ppm": float(self.reference_ppm),
            "harmonic_cap": self.harmonic_cap,
            "accepted_confidence_levels": list(ACCEPTED_CONFIDENCE_LEVELS),
        }

    def digest(self) -> str:
        material = json.dumps(self.to_dict(), sort_keys=True,
                              separators=(",", ":")).encode("utf-8")
        return f"blake2s:{hashlib.blake2s(material, digest_size=16).hexdigest()}"


def _canonical_schedule_bytes(schedule: Tuple[Visit, ...]) -> bytes:
    """The bytes a schedule is compared as. Sorted keys, no incidental spacing."""
    return json.dumps([[v.position, v.tuning_index] for v in schedule],
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
                         allocations: Any, reference_hz: float,
                         reference_ppm: float,
                         generator_revision: str = SCHEDULE_GENERATOR_REVISION,
                         ) -> CapturePlanDeclaration:
    """Declare a plan against an envelope, or refuse and say which part is not one."""
    if type(envelope) is not InstrumentChainEnvelope:
        raise EnvelopeRefused(
            ENVELOPE_ABSENT,
            f"a plan is declared against an InstrumentChainEnvelope; got "
            f"{type(envelope).__name__}")
    plan = CapturePlanDeclaration(
        seed=seed, schedule_generator_revision=generator_revision,
        schedule=generate_visit_schedule(seed=seed,
                                         generator_revision=generator_revision),
        allocations=tuple(allocations), reference_hz=reference_hz,
        reference_ppm=reference_ppm)
    outside = allocations_outside_envelope(plan, envelope)
    if outside:
        raise EnvelopeRefused(
            PLAN_ALLOCATION_OUTSIDE_ENVELOPE,
            f"{len(outside)} allocated chain(s) are not declared members of "
            "the envelope this plan is declared against")
    return plan
