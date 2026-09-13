"""What live SHADOW can actually observe.

§17 slice 10, implementing PROMOTION_EXECUTION_CONTRACT.md §13h Amendment I.

**This is not a prediction of what ARMED would do.** §12 said it was, and §13h I.1
established that it cannot be: `decide_promotion` needs a `PromotionRequest`,
nothing in production builds one, and `scythe_promotion_policy` forbids treating
a completed check as an ask. ARMED's rate depends on a requester that does not
exist -- undefined rather than unobserved.

What this observes is the **apparatus**: whether seeding works against a real
ledger, whether the budget and the ceilings engage, whether promotion identity
is stable across real verdicts, and whether the lineage was quiescent across the
interval.

Four things it will not do, each structural rather than remembered:

  **It holds no ownership scope, no LedgerWriter and no adapter.** Without a
  scope no append exists at all (§13c D.4), so the durable ledger is not
  protected from this module -- it is unreachable by it.

  **It acquires nothing.** No capture, no rtl_tcp, no socket. The raw-IQ and
  loopback constraints are not engaged carefully; they are not engaged.

  **It runs in the foreground and stops by itself.** No daemon, no background
  process, no scheduled task. This repository carries a wedged PID from the last
  time something ran and did not stop.

  **Its synthetic input is a distinct type.** ObservationSubject and
  ObservationOutcome are not a PromotionRequest with a flag on it, because a
  flag can be omitted or falsified and the call site does not show whether the
  claim was true (§13h I.2a).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
import time
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Tuple

from scythe_invariant_ledger import InvariantVerdict
from scythe_promotion_ceilings import configuration_identity as ceiling_identity
from scythe_promotion_lineage import Lineage, LineageError, Syscalls
from scythe_promotion_policy import (
    PROMOTION_ELIGIBLE, PROMOTION_REFUSED, REFUSALS, PolicyFacts,
    evaluate_policy, verdict_digest,
)

SCHEMA = "scythe.shadow-observation.v1"

# -- what this record is, said in the record ------------------------------
NOT_A_PREDICTION = "NOT_A_PREDICTION"
SYNTHETIC_REQUEST = "SYNTHETIC_REQUEST"

# -- how a run ends -------------------------------------------------------
VERDICT_COUNT_REACHED = "VERDICT_COUNT_REACHED"
OBSERVATION_DURATION_REACHED = "OBSERVATION_DURATION_REACHED"
VERDICT_SOURCE_EXHAUSTED = "VERDICT_SOURCE_EXHAUSTED"
OBSERVATION_INTERRUPTED = "OBSERVATION_INTERRUPTED"
ENDINGS: Tuple[str, ...] = (VERDICT_COUNT_REACHED, OBSERVATION_DURATION_REACHED,
                            VERDICT_SOURCE_EXHAUSTED, OBSERVATION_INTERRUPTED)

# -- refusals -------------------------------------------------------------
OBSERVATION_LIMIT_INVALID = "OBSERVATION_LIMIT_INVALID"
OBSERVATION_PATH_REFUSED = "OBSERVATION_PATH_REFUSED"
OBSERVATION_PUBLICATION_REFUSED = "OBSERVATION_PUBLICATION_REFUSED"
OBSERVATION_REFUSALS: Tuple[str, ...] = (
    OBSERVATION_LIMIT_INVALID, OBSERVATION_PATH_REFUSED,
    OBSERVATION_PUBLICATION_REFUSED,
)

# -- what the digests are allowed to say (§13h I.4a) ----------------------
LINEAGE_QUIESCENT = "LINEAGE_QUIESCENT"
LINEAGE_NOT_QUIESCENT = "LINEAGE_NOT_QUIESCENT"

# -- where the verdicts came from -----------------------------------------
#
# Recorded rather than assumed. Nothing in this repository stores derived RF
# evidence, so a run today is driven by constructed inputs through the real
# checkers. Saying so in the record is the difference between an observation and
# a test with a record attached -- and it leaves the artefact source reachable
# later without anything having to be rewritten to admit it.
EVIDENCE_CONSTRUCTED = "EVIDENCE_CONSTRUCTED"
EVIDENCE_DERIVED_ARTEFACT = "EVIDENCE_DERIVED_ARTEFACT"
EVIDENCE_SOURCES: Tuple[str, ...] = (EVIDENCE_CONSTRUCTED,
                                     EVIDENCE_DERIVED_ARTEFACT)

# What a run *is*, which is not the same question as where its verdicts came
# from. Running production checkers over invented inputs certifies the
# apparatus; it does not observe anything live, and calling the verdicts genuine
# does not make the evidence live.
APPARATUS_CERTIFICATION = "APPARATUS_CERTIFICATION"
LIVE_OBSERVATION = "LIVE_OBSERVATION"
NOT_A_LIVE_OBSERVATION = "NOT_A_LIVE_OBSERVATION"
DERIVED_EVIDENCE_UNAVAILABLE = "DERIVED_EVIDENCE_UNAVAILABLE"

# Only one source is eligible to be live, and it is not the one available today.
_LIVE_ELIGIBLE: Tuple[str, ...] = (EVIDENCE_DERIVED_ARTEFACT,)

# -- where the instrument declaration came from (§13l M.4, 37b) -----------
#
# The observer cannot state what the instrument did. It never touched one, and
# neither did the producer -- the only party with a claim to make is the
# artefact, and the artefact makes it in writing. So the record **carries** a
# declaration or says it has none, and there is no third way for one to arrive.
#
# Named CARRIED_FROM_ARTEFACT rather than ARTEFACT_DECLARED or ARTEFACT_ATTESTED:
# both of those are negation pairs against LEDGER_GENERATION_UNDECLARED and
# LOCK_EXCLUSION_UNATTESTED, checked mechanically rather than by eye.
CARRIED_FROM_ARTEFACT = "CARRIED_FROM_ARTEFACT"
NO_INSTRUMENT_DECLARATION = "NO_INSTRUMENT_DECLARATION"
DECLARATION_AUTHORITIES: Tuple[str, ...] = (CARRIED_FROM_ARTEFACT,
                                            NO_INSTRUMENT_DECLARATION)

VERDICT_SOURCE_REFUSED = "VERDICT_SOURCE_REFUSED"

OBSERVATION_REFUSALS = OBSERVATION_REFUSALS + (DERIVED_EVIDENCE_UNAVAILABLE,
                                               VERDICT_SOURCE_REFUSED)

# -- contract-declared maxima (§13h I.5) ----------------------------------
#
# A configurable bound is not a bound: "configured count and duration" permits a
# billion verdicts or several years. Not runtime knobs, for §13f G.4's reason --
# a limit a deployment can raise will be raised at the moment it first binds.
MAX_OBSERVATION_VERDICTS = 1_000
MAX_OBSERVATION_DURATION_S = 900.0


class ObservationRefused(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# -- the distinct types (§13h I.2a) ---------------------------------------

@dataclass(frozen=True)
class ObservationSubject:
    """What *would* be asked, without being an ask.

    Deliberately not a PromotionRequest and deliberately not convertible into
    one by anything public here. It carries the same declared fields as data so
    the policy can be evaluated over them; it carries no method that hands them
    to a production path.
    """

    requested_by: str
    target_graph: str
    justification_source: str
    origin: str = SYNTHETIC_REQUEST


@dataclass(frozen=True)
class ObservationOutcome:
    """One verdict, evaluated. Not a PromotionDecision, so no act path takes it.

    That is the guarantee that matters: every path which can reach the graph or
    the ledger accepts a PromotionDecision, and this type is not one. There is
    nothing for an act path to be handed.
    """

    identity: str
    disposition: str
    merit_refusals: Tuple[str, ...]
    executability_refusals: Tuple[str, ...]
    origin: str = SYNTHETIC_REQUEST


@dataclass(frozen=True)
class InstrumentDeclaration:
    """What the instrument did, and who said so (§13l M.4).

    Unconstructable in an inconsistent state: an authority of
    CARRIED_FROM_ARTEFACT with values the reader does not declare, or
    NO_INSTRUMENT_DECLARATION with any value at all, raises here rather than
    reaching a record where nobody would look again.

    There is no constructor taking a status directly. `from_artefact` is the
    only way to build one carrying a status, and it takes an `Artefact` -- so
    the values in the record are the ones a reader admitted from bytes on disk,
    not ones an observer typed.
    """

    authority: str
    measurement_status: str
    instrument_state: str

    def __post_init__(self) -> None:
        from scythe_derived_evidence import INSTRUMENT_STATES, MEASUREMENT_STATUSES

        if self.authority not in DECLARATION_AUTHORITIES:
            raise ObservationRefused(
                VERDICT_SOURCE_REFUSED,
                f"{str(self.authority)[:32]!r} is not a declaration authority")
        if self.authority == NO_INSTRUMENT_DECLARATION:
            if (self.measurement_status, self.instrument_state) != \
                    (NO_INSTRUMENT_DECLARATION, NO_INSTRUMENT_DECLARATION):
                raise ObservationRefused(
                    VERDICT_SOURCE_REFUSED,
                    "a run with no artefact carries no instrument status; a "
                    "value here would be the observer making the claim")
            return
        if self.measurement_status not in MEASUREMENT_STATUSES:
            raise ObservationRefused(
                VERDICT_SOURCE_REFUSED,
                f"measurement status {str(self.measurement_status)[:32]!r} is "
                f"not one the reader declares")
        if self.instrument_state not in INSTRUMENT_STATES:
            raise ObservationRefused(
                VERDICT_SOURCE_REFUSED,
                f"instrument state {str(self.instrument_state)[:32]!r} is not "
                f"one the reader declares")

    @classmethod
    def from_artefact(cls, artefact: Any) -> "InstrumentDeclaration":
        """The only way a status reaches a record. Nominal, never `isinstance`.

        A mapping with the right attributes is exactly the impostor this
        refuses: what is carried has to have come from an artefact a reader
        admitted, not from something shaped like one.
        """
        from scythe_derived_evidence import Artefact

        if type(artefact) is not Artefact:
            raise ObservationRefused(
                VERDICT_SOURCE_REFUSED,
                f"{type(artefact).__name__} is not an Artefact; a declaration "
                f"is carried from one or is not carried")
        return cls(authority=CARRIED_FROM_ARTEFACT,
                   measurement_status=artefact.measurement_status,
                   instrument_state=artefact.instrument_state)


NO_DECLARATION = InstrumentDeclaration(
    authority=NO_INSTRUMENT_DECLARATION,
    measurement_status=NO_INSTRUMENT_DECLARATION,
    instrument_state=NO_INSTRUMENT_DECLARATION)


@dataclass(frozen=True)
class VerdictSource:
    """Verdicts and the declaration of the instrument that did not measure them.

    One type rather than two arguments, because the two must come from the same
    read: a declaration passed beside an iterator is a declaration about
    whatever the caller says, and §13i J.7's lesson is that the second look is
    where the substitution fits.
    """

    declaration: InstrumentDeclaration
    verdicts: Iterable[Tuple[InvariantVerdict, ObservationSubject, Any]]

    def __post_init__(self) -> None:
        if type(self.declaration) is not InstrumentDeclaration:
            raise ObservationRefused(
                VERDICT_SOURCE_REFUSED,
                f"{type(self.declaration).__name__} is not an "
                f"InstrumentDeclaration")


def _facts_of(subject: ObservationSubject) -> PolicyFacts:
    """A subject yields facts. It does not yield a request, here or anywhere.

    §13h I.2a forbids the conversion, and the policy's shared core is what makes
    obeying it cost nothing: both paths evaluate the same rules because the
    rules belong to neither the ask nor the observation.
    """
    return PolicyFacts(requested_by=subject.requested_by,
                       target_graph=subject.target_graph,
                       justification_source=subject.justification_source)


def _evaluate(verdict: InvariantVerdict, subject: ObservationSubject,
              capsule: Any, already: Tuple[str, ...]) -> ObservationOutcome:
    """Evaluate the shared core and wrap the conclusion as an outcome.

    `evaluate_policy` returns a `PolicyConclusion`, which no act path accepts.
    Only `decide_promotion` wraps one into a `PromotionDecision`, and this
    module does not call it: no `PromotionRequest` is built here, so there is
    nothing to convert and nothing to leak.
    """
    conclusion = evaluate_policy(verdict, _facts_of(subject), capsule,
                                 already_promoted=already)
    return ObservationOutcome(
        identity=conclusion.idempotency_key or verdict_digest(verdict),
        disposition=conclusion.disposition,
        merit_refusals=tuple(conclusion.refusals),
        executability_refusals=(),
    )


# -- publication (§13h I.4, by §13e F.6's protocol) -----------------------

def _digest_of(path: str) -> Optional[str]:
    try:
        handle = hashlib.blake2s(digest_size=16)
        with open(path, "rb") as source:
            for block in iter(lambda: source.read(65536), b""):
                handle.update(block)
        return f"blake2s:{handle.hexdigest()}"
    except OSError:
        return None


def lineage_digest(root: str) -> Dict[str, Optional[str]]:
    """Every published generation's digest, for the quiescence comparison."""
    digests: Dict[str, Optional[str]] = {}
    try:
        for generation in Lineage(root=root).published():
            digests[os.path.basename(generation.path)] = _digest_of(generation.path)
    except LineageError:
        pass
    return digests


def refuse_record_path(record_path: str, lineage_root: str) -> None:
    """Outside the complete lineage namespace, after symlink resolution.

    Not a prefix check on the configured strings: both sides are resolved first,
    because a symlink into the lineage directory would satisfy a string
    comparison and place a non-ledger among the ledgers.
    """
    resolved = os.path.realpath(record_path)
    namespace = os.path.realpath(os.path.dirname(os.path.abspath(lineage_root)))
    if resolved == namespace or resolved.startswith(namespace + os.sep):
        raise ObservationRefused(
            OBSERVATION_PATH_REFUSED,
            "the record resolves inside the lineage namespace; a file that "
            "accumulates beside the ledgers is a second ledger nobody accepted")


def publish_record(path: str, payload: Dict[str, Any], syscalls: Syscalls) -> str:
    """§13e F.6's five steps, because it is the same problem.

    Exclusive creation, complete write, file fsync, atomic rename, directory
    fsync. A second run at one path is refused rather than extending the first.
    """
    if os.path.exists(path):
        raise ObservationRefused(
            OBSERVATION_PUBLICATION_REFUSED,
            "a record already exists at this path; observations are published "
            "once and never appended to")
    body = json.dumps(payload, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    temporary = f"{path}.{payload['observation_id']}.partial"
    fd = syscalls.open_exclusive(temporary)
    try:
        syscalls.write(fd, body)
        syscalls.fsync(fd)
    finally:
        syscalls.close(fd)
    syscalls.rename(temporary, path)
    syscalls.fsync_directory(os.path.dirname(os.path.abspath(path)) or ".")
    return path


# -- the run ---------------------------------------------------------------

@dataclass
class ShadowObservation:
    """One bounded, foreground observation of the apparatus."""

    lineage_root: str
    record_path: str
    verdict_limit: int
    duration_s: float
    evidence_source: str = EVIDENCE_CONSTRUCTED
    # A caller asking for a live observation gets one or gets a refusal. It
    # never gets constructed evidence wearing the label, which is the whole
    # reason this is a separate flag rather than an inference from the source.
    require_live: bool = False
    syscalls: Syscalls = field(default_factory=Syscalls)
    clock: Callable[[], float] = time.monotonic

    @property
    def run_class(self) -> str:
        """What this run is. Derived from the source and never asserted."""
        return (LIVE_OBSERVATION if self.evidence_source in _LIVE_ELIGIBLE
                else APPARATUS_CERTIFICATION)

    def _validate(self) -> None:
        """Before anything is read or written (§13h I.5).

        A run that never started has nothing to report, so an invalid limit
        publishes no record at all rather than one saying it refused.
        """
        if not isinstance(self.verdict_limit, int) \
                or isinstance(self.verdict_limit, bool) \
                or not 1 <= self.verdict_limit <= MAX_OBSERVATION_VERDICTS:
            raise ObservationRefused(
                OBSERVATION_LIMIT_INVALID,
                f"verdict limit must be 1..{MAX_OBSERVATION_VERDICTS}")
        if not isinstance(self.duration_s, (int, float)) \
                or isinstance(self.duration_s, bool) \
                or not 0 < float(self.duration_s) <= MAX_OBSERVATION_DURATION_S:
            raise ObservationRefused(
                OBSERVATION_LIMIT_INVALID,
                f"duration must be 0 < d <= {MAX_OBSERVATION_DURATION_S}")
        if self.evidence_source not in EVIDENCE_SOURCES:
            raise ObservationRefused(OBSERVATION_LIMIT_INVALID,
                                     "the evidence source is not a declared one")
        if self.require_live and self.evidence_source not in _LIVE_ELIGIBLE:
            # A bounded refusal, never a substitution. Constructed evidence
            # cannot stand in for a derived artefact, because the substitution
            # would be invisible in the record that exists to prevent it.
            raise ObservationRefused(
                DERIVED_EVIDENCE_UNAVAILABLE,
                "no derived-evidence source is available; constructed evidence "
                "certifies the apparatus and is not a live observation")
        refuse_record_path(self.record_path, self.lineage_root)

    def run(self, source: "VerdictSource") -> Dict[str, Any]:
        """Observe until a bound, then publish once.

        Both bounds stay active and the first reached ends the run. An exception
        still publishes (§13h I.7): an observation that produces nothing when it
        fails cannot be told from one that never started, and that difference is
        what the record was for.

        The source carries its own instrument declaration. There is no
        parameter for one here and no field on this class, which is the whole
        of slice 10e: the observer has no way to say what the instrument did.
        """
        self._validate()
        self._require_source(source)
        verdicts = source.verdicts
        declaration = source.declaration

        started = self.clock()
        before = lineage_digest(self.lineage_root)
        observed: List[ObservationOutcome] = []
        promoted: List[str] = []
        merit: Dict[str, int] = {}
        ending, interrupted_by = VERDICT_SOURCE_EXHAUSTED, None

        try:
            for verdict, subject, capsule in verdicts:
                if len(observed) >= self.verdict_limit:
                    ending = VERDICT_COUNT_REACHED
                    break
                if self.clock() - started >= float(self.duration_s):
                    ending = OBSERVATION_DURATION_REACHED
                    break
                outcome = _evaluate(verdict, subject, capsule, tuple(promoted))
                observed.append(outcome)
                if outcome.disposition == PROMOTION_ELIGIBLE:
                    promoted.append(outcome.identity)
                for refusal in outcome.merit_refusals:
                    merit[refusal] = merit.get(refusal, 0) + 1
        except Exception as exc:             # noqa: BLE001 -- bounded below
            ending = OBSERVATION_INTERRUPTED
            interrupted_by = type(exc).__name__

        after = lineage_digest(self.lineage_root)
        record = self._record(started, observed, promoted, merit, before, after,
                              ending, interrupted_by, declaration)
        publish_record(self.record_path, record, self.syscalls)
        return record

    def _require_source(self, source: Any) -> None:
        """Nominal, and consistent with what this run claims to be.

        A live observation whose declaration came from nowhere, or a
        constructed run carrying an instrument status, publishes nothing at
        all: the mismatch is refused before the first verdict rather than
        recorded beside the numbers it would undermine.
        """
        if type(source) is not VerdictSource:
            raise ObservationRefused(
                VERDICT_SOURCE_REFUSED,
                f"{type(source).__name__} is not a VerdictSource; a bare "
                f"iterable carries no declaration and would leave the record "
                f"silent about the instrument")
        authority = source.declaration.authority
        if self.run_class == LIVE_OBSERVATION and \
                authority != CARRIED_FROM_ARTEFACT:
            raise ObservationRefused(
                VERDICT_SOURCE_REFUSED,
                "a live observation carries its instrument declaration from "
                "the artefact its verdicts came from")
        if self.evidence_source == EVIDENCE_CONSTRUCTED and \
                authority != NO_INSTRUMENT_DECLARATION:
            raise ObservationRefused(
                VERDICT_SOURCE_REFUSED,
                "constructed evidence has no instrument, so it has no "
                "instrument status to carry")

    def _record(self, started, observed, promoted, merit, before, after,
                ending, interrupted_by, declaration) -> Dict[str, Any]:
        quiescent = before == after
        return {
            "schema": SCHEMA,
            "observation_id": hashlib.blake2s(
                f"{self.record_path}:{started}".encode("utf-8"),
                digest_size=8).hexdigest(),
            "claim": NOT_A_PREDICTION,
            "run_class": self.run_class,
            "live_observation": self.run_class == LIVE_OBSERVATION,
            "live_note": (
                "RUNNING PRODUCTION CHECKERS OVER INVENTED INPUTS CERTIFIES THE "
                "APPARATUS. IT IS " + NOT_A_LIVE_OBSERVATION + ": THE VERDICTS "
                "ARE GENUINE AND THE EVIDENCE IS NOT LIVE, AND THE FIRST DOES "
                "NOT MAKE THE SECOND TRUE"),
            "claim_note": (
                "THIS IS AN OBSERVATION OF THE APPARATUS. IT IS NOT A FORECAST "
                "OF WHAT ARMED WOULD DO: ARMED'S RATE DEPENDS ON A REQUESTER "
                "THAT DOES NOT EXIST, AND IS UNDEFINED RATHER THAN UNOBSERVED"),
            "request_origin": SYNTHETIC_REQUEST,
            "evidence_source": self.evidence_source,
            "instrument_declaration": {
                "authority": declaration.authority,
                "measurement_status": declaration.measurement_status,
                "instrument_state": declaration.instrument_state,
            },
            "instrument_note": (
                "WHAT THE INSTRUMENT DID IS CARRIED FROM THE ARTEFACT THAT "
                "SAID SO. THIS OBSERVER NEVER TOUCHED AN INSTRUMENT AND HAS NO "
                "WAY TO STATE ONE'S STATUS"),
            "ending": ending,
            "interrupted_by": interrupted_by,
            "bounds": {"verdict_limit": self.verdict_limit,
                       "duration_s": float(self.duration_s),
                       "max_verdicts": MAX_OBSERVATION_VERDICTS,
                       "max_duration_s": MAX_OBSERVATION_DURATION_S,
                       "bounds_are_configurable": False},
            "elapsed_monotonic_s": round(self.clock() - started, 6),
            "verdicts_observed": len(observed),
            "would_promote": len(promoted),
            "merit_refusals": dict(sorted(merit.items())),
            "executability_refusals": {},
            "counts_note": ("COUNTED PER SET AND NEVER SUMMED (§5). SHADOW "
                            "REACHES NO EXECUTABILITY REFUSAL BECAUSE IT "
                            "RESERVES NOTHING"),
            "merit_vocabulary": list(REFUSALS),
            "lineage_root": self.lineage_root,
            "lineage_digest_before": before,
            "lineage_digest_after": after,
            "quiescence": LINEAGE_QUIESCENT if quiescent else LINEAGE_NOT_QUIESCENT,
            "quiescence_note": (
                "EQUAL DIGESTS SHOW THE LINEAGE WAS UNCHANGED ACROSS THIS "
                "INTERVAL. THEY DO NOT ATTRIBUTE THAT TO THIS OBSERVER: §12 "
                "PERMITS SHADOW BESIDE AN ARMED WRITER, AND THIS PROCESS "
                "CANNOT TELL ITS OWN WRITES FROM ANOTHER'S"),
            "ceiling_configuration_identity": ceiling_identity(),
            "appends": False,
            "acquires": False,
            "holds_ownership": False,
            "survives_sigkill": False,
            "survival_note": (
                "A RECOVERABLE EXCEPTION STILL PUBLISHES THIS RECORD. IT CLAIMS "
                "NOTHING ABOUT SIGKILL, POWER LOSS, OR A FAILURE OF ITS OWN "
                "PUBLICATION"),
        }


# -- a verdict source that uses the real checkers -------------------------

def derived_walk_verdicts(artefact_path: str) -> VerdictSource:
    """Verdicts from a derived artefact, the only source eligible to be live.

    Delegates to the reader, which refuses before either checker runs if the
    artefact's sample status is not schema-conformant. **There is no fallback**:
    a missing or refused artefact raises rather than quietly becoming a
    constructed run, because the substitution would be invisible in the record
    that exists to prevent it (§13h I.2, §13i J.9).

    **One read.** The declaration and the verdicts come from the same admitted
    artefact, so the record cannot describe an instrument belonging to a
    different file than the verdicts do (§13i J.7).
    """
    from scythe_derived_evidence import read_artefact, walk_verdicts_of

    artefact = read_artefact(artefact_path)
    return VerdictSource(declaration=InstrumentDeclaration.from_artefact(artefact),
                         verdicts=walk_verdicts_of(artefact))


def constructed_walk_verdicts(count: int) -> VerdictSource:
    """Real `check_walk_step`, over inputs this repository does not store.

    The checker is production code and the verdicts are genuine; the evidence is
    constructed, which is why the record says EVIDENCE_CONSTRUCTED. A run that
    called this a live observation of captured data would be a test with a
    record attached.

    It carries NO_DECLARATION, because there was no instrument: not an idle
    one, not a configured one, none. A status here would be the observer
    inventing the very claim §13l M.4 exists to keep it from making.
    """
    return VerdictSource(declaration=NO_DECLARATION,
                         verdicts=_constructed_walk_verdicts(count))


def _constructed_walk_verdicts(count: int) -> Iterator[
        Tuple[InvariantVerdict, ObservationSubject, Any]]:
    from rf_walk_transitions import check_walk_step, walk_signature, with_step_measures
    from scythe_promotion_policy import CapsuleIdentity

    capsule = CapsuleIdentity(schema="scythe.invariant-capsule.v1",
                              digest="blake2s:observation", within_bounds=True,
                              carries_samples=False)
    subject = ObservationSubject(requested_by="OPERATOR",
                                 target_graph="scythe.graphops.evidence",
                                 justification_source="OPERATOR")
    common = dict(device_id="dev-1", receiver_state_chain_hash="rsc-1",
                  monotonic_source_id="mono-1", signal_chain_hash="chain-1",
                  configuration_epoch=1, pose_uncertainty_m=2.0)
    for index in range(count):
        before = walk_signature(latitude=51.5, longitude=-0.12,
                                observed_monotonic_ns=index * 1_000_000_000,
                                **common)
        after = walk_signature(latitude=51.5 + index * 1e-5, longitude=-0.12,
                               observed_monotonic_ns=(index + 1) * 1_000_000_000,
                               **common)
        before, after = with_step_measures(before, after, speed_before_mps=1.4,
                                           speed_after_mps=1.4)
        yield check_walk_step(before, after), subject, capsule
