"""Whether a capture-process restart is justified. Never whether to perform one.

Phase 1 is a decision, not an action. Nothing here calls systemd, signals a
process, opens a socket or touches the bridge. The output is one of three words
and the reason for it, so the policy can be argued with and tested before
anything is granted authority to act on it.

The separation is two-stage and deliberate:

    /proc + capture state          -> RecoveryObservation   (impure, reads files)
    RecoveryObservation + incident -> RecoveryDecision       (pure, no I/O)

``decide`` takes no clock and reads no file. Every input it needs, including the
current instant, arrives on the observation, so a test states a situation rather
than arranging for one.

What this module refuses to conclude
------------------------------------
*A cause.* A removed USB device, a wedged ``rtl_tcp`` and a suspended host are
indistinguishable from this side of the socket. The decision names the state it
observed and stops.

*That anything was restored.* A new PID, a listening socket, an accepted
connection and a 12-byte ``RTL0`` greeting are all things a dead capture chain
produces. Only decoded samples end a starvation, which is why the boundary is
written against ``last_sample_age_ms`` and never against bytes or liveness.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import os
# Address parsing only. There is no socket call anywhere in this module -- it
# reads /proc and decides; it never connects to, signals or starts anything.
import socket
import time
from typing import Any, Dict, Optional


SCHEMA = "scythe.rf-capture-recovery.v1"

# The three outcomes, and nothing else. A fourth would be an action.
NO_ACTION = "NO_ACTION"
REQUEST_RESTART = "REQUEST_RESTART"
RECOVERY_SUPPRESSED = "RECOVERY_SUPPRESSED"
DECISIONS = (NO_ACTION, REQUEST_RESTART, RECOVERY_SUPPRESSED)

# How long an established connection must deliver nothing before a restart is
# arguable. Six times the bridge's 2.5 s starvation threshold: that one exists to
# stop the UI claiming a live trace, and this one exists to justify interfering
# with a process. A momentary stall is worth reporting and is not worth a
# restart, so they are separate numbers with separate jobs.
SUSTAINED_STARVATION_S = 15.0

# Minimum between two requests for the same incident. A restart that has not yet
# had time to produce a sample cannot be judged to have failed.
RECOVERY_COOLDOWN_S = 120.0

# Two limits over one bounded attempt sequence, both across incidents.
#
# Phase 1 counted attempts per incident, because a count and one timestamp were
# all the observation carried. That left an escape: a restarted-but-still-broken
# rtl_tcp arrives with a new PID and opens a new incident, and a per-incident
# budget hands it a fresh three. The circuit breaker could be laundered by the
# very failure it exists to stop. The sequence closes it -- the window is wall
# clock and spans incidents, so repeated failed recoveries accumulate.
RECOVERY_ATTEMPT_LIMIT = 3
ATTEMPT_WINDOW_S = 600.0
# One restart per process identity, ever. A second attempt against the same
# (boot, pid, start_ticks) is a repetition of something already shown not to
# work, and identity is exact enough that a genuinely new process is a genuinely
# new target.
MAX_ATTEMPTS_PER_PROCESS = 1
MAX_TRACKED_ATTEMPTS = 32
ATTEMPT_WINDOW_AUTHORITY = "ROLLING_MONOTONIC_WINDOW_ACROSS_INCIDENTS"

# How long a granted authorization stays good. Long enough to survive the
# CONNECTED/CONNECTING flap of a wedged source, short enough that an
# authorization cannot outlive the evidence that justified it.
AUTHORIZATION_TTL_S = 60.0

# How long after a restart request the system waits for the only thing that
# counts as success. Beyond it the outcome is PROCESS_RESTARTED_STILL_STARVED:
# a declared deadline, so "not yet" and "never" are not the same answer.
OBSERVATION_DEADLINE_S = 45.0

# Three states, not a boolean. "Off" and "watching" are different postures and a
# flag that conflates them cannot express the one we actually want to run in.
MODE_DISABLED = "DISABLED"
MODE_SHADOW = "SHADOW"
MODE_ARMED = "ARMED"
RECOVERY_MODES = (MODE_DISABLED, MODE_SHADOW, MODE_ARMED)
DEFAULT_RECOVERY_MODE = MODE_SHADOW

# Every decision carries this. The policy observes a state; it does not diagnose.
UNDETERMINED_CAUSE = "NOT_DETERMINABLE_FROM_THIS_PROCESS"
RESTORATION_DEFINITION = (
    "SAMPLE FLOW IS RESTORED BY DECODED SAMPLES ONLY. A NEW PID, A LISTENING "
    "SOCKET, AN ACCEPTED CONNECTION AND AN RTL0 GREETING ARE ALL PRODUCED BY A "
    "CAPTURE CHAIN THAT IS DELIVERING NOTHING"
)

# Reasons. A bare NO_ACTION cannot distinguish a healthy stream from a starved
# one that is thirty seconds too young, and an operator reading the second as
# the first is the whole failure this policy exists to end.
REASONS: Dict[str, str] = {
    "NOT_STARVED": "THE SOURCE IS DELIVERING SAMPLES",
    "TRANSPORT_NOT_CONNECTED": (
        "THERE IS NO ESTABLISHED CONNECTION TO STARVE. RECONNECTION IS THE "
        "BRIDGE'S ORDINARY BUSINESS AND NEEDS NO INTERVENTION"),
    "NO_OPEN_INCIDENT": "NO STARVATION INCIDENT IS OPEN TO ACT ON",
    "STARVATION_TOO_BRIEF": (
        "STARVED, BUT NOT FOR LONG ENOUGH TO JUSTIFY INTERFERING WITH A PROCESS"),
    "CAPTURE_PROCESS_IDENTITY_UNAVAILABLE": (
        "THE CAPTURE PROCESS COULD NOT BE IDENTIFIED, SO A RESTART CANNOT BE "
        "SHOWN TO BE NEEDED RATHER THAN ALREADY DONE"),
    "CAPTURE_PROCESS_CHANGED": (
        "THE CAPTURE PROCESS IS NOT THE ONE THE INCIDENT OPENED AGAINST. "
        "SOMETHING ALREADY RESTARTED IT, AND IT IS STILL STARVED"),
    "COOLDOWN_ACTIVE": (
        "A REQUEST WAS MADE TOO RECENTLY TO HAVE BEEN GIVEN TIME TO FAIL"),
    "SUSTAINED_STARVATION_SAME_PROCESS": (
        "AN ESTABLISHED CONNECTION TO AN UNCHANGED CAPTURE PROCESS HAS "
        "DELIVERED NO SAMPLES FOR LONGER THAN THE SUSTAINED THRESHOLD"),
    "PROCESS_ALREADY_ATTEMPTED": (
        "THIS EXACT PROCESS HAS ALREADY BEEN RESTARTED ONCE. RESTARTING IT "
        "AGAIN WOULD REPEAT SOMETHING ALREADY SHOWN NOT TO WORK"),
    "ATTEMPT_WINDOW_EXHAUSTED": (
        "THE ROLLING ATTEMPT WINDOW IS FULL. THE WINDOW SPANS INCIDENTS, SO A "
        "NEW PID AND A NEW INCIDENT DO NOT REFILL IT"),
}


@dataclass(frozen=True)
class ProcessIdentity:
    """Which process, unambiguously, across reboots as well as across restarts.

    A PID alone is reused. A PID with its start time is unique within one boot
    and can still repeat across two, because ``starttime`` counts from that
    boot's own zero -- so a machine that reboots and re-launches the same
    service early can present the identical pair for a genuinely different
    process. The boot id closes that.

    ``start_ticks`` stays in the kernel's native clock ticks. Converting it to
    nanoseconds would require USER_HZ, which is a userspace view of the value
    rather than part of what the kernel attested, and the converted number would
    then look like a kernel-supplied timestamp. These ticks are only ever
    compared against other ticks from the same boot; no arithmetic is done on
    them, so the unit never needs to be known.
    """

    boot_id: str
    pid: int
    start_ticks: int

    def same_process_as(self, other: Optional["ProcessIdentity"]) -> bool:
        """Identity, not liveness. Two Nones are not a match."""
        if other is None:
            return False
        return (self.boot_id == other.boot_id
                and self.pid == other.pid
                and self.start_ticks == other.start_ticks)

    def supersedes(self, other: Optional["ProcessIdentity"]) -> bool:
        """Whether this incarnation came after `other` in the same boot.

        ``start_ticks`` counts from that boot's zero, so within one boot it is
        an incarnation number and it only increases. That gives the ordering a
        bare identity comparison cannot: "different" and "later" are not the
        same claim, and a restart is supposed to produce the second.

        Across boots there is no ordering to have. Each boot restarts the
        counter, so two identities from different boots are unrelated rather
        than one being newer, and this returns False for them.
        """
        if other is None or self.boot_id != other.boot_id:
            return False
        return self.start_ticks > other.start_ticks

    def as_dict(self) -> Dict[str, Any]:
        return {"kernel_boot_id": self.boot_id, "capture_pid": self.pid,
                "capture_process_start_ticks": self.start_ticks,
                "start_ticks_authority": "KERNEL_PROC_STAT_FIELD_22_NATIVE_TICKS"}


@dataclass(frozen=True)
class RecoveryAttempt:
    """One restart that was actually requested. Shadow judgements are not these.

    Carries the target's full identity rather than a PID, so a later attempt
    against "the same process" is a comparison and not an assumption.
    """

    incident_id: str
    kernel_boot_id: str
    target_pid: int
    target_start_ticks: int
    attempted_monotonic_ns: int

    def targets(self, identity: Optional["ProcessIdentity"]) -> bool:
        if identity is None:
            return False
        return (self.kernel_boot_id == identity.boot_id
                and self.target_pid == identity.pid
                and self.target_start_ticks == identity.start_ticks)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RecoveryObservation:
    """One bounded reading. Immutable, serializable, and complete on its own.

    ``decide`` reads nothing that is not here -- including the current instant --
    so a decision is reproducible from its input alone.
    """

    observed_monotonic_ns: int
    availability: str
    transport_state: str
    flow_state: str
    last_sample_age_ms: Optional[float]
    reconnect_count: int
    incident_id: Optional[str] = None
    incident_opened_monotonic_ns: Optional[int] = None
    capture_process: Optional[ProcessIdentity] = None
    # The identity the incident was opened against. Held beside the current one
    # so "unchanged" is a comparison rather than an assumption.
    incident_capture_process: Optional[ProcessIdentity] = None
    # The bounded attempt sequence itself, not a count and a timestamp derived
    # from it. Two summaries of one list can disagree; the list cannot disagree
    # with itself.
    recovery_attempts: tuple = ()
    latest_sequence: int = 0

    def recovery_attempt_count(self) -> int:
        return len(self.recovery_attempts)

    def last_recovery_attempt_monotonic_ns(self) -> Optional[int]:
        if not self.recovery_attempts:
            return None
        return max(a.attempted_monotonic_ns for a in self.recovery_attempts)

    def last_sample_monotonic_ns(self) -> Optional[int]:
        """When a sample last arrived, on the same clock as everything else."""
        if self.last_sample_age_ms is None:
            return None
        return int(self.observed_monotonic_ns - float(self.last_sample_age_ms) * 1e6)

    def starvation_duration_s(self) -> Optional[float]:
        if self.incident_opened_monotonic_ns is None:
            return None
        return (self.observed_monotonic_ns - self.incident_opened_monotonic_ns) / 1e9

    def since_last_attempt_s(self) -> Optional[float]:
        last = self.last_recovery_attempt_monotonic_ns()
        if last is None:
            return None
        return (self.observed_monotonic_ns - last) / 1e9

    def attempts_in_window(self, window_s: float = ATTEMPT_WINDOW_S) -> int:
        """Attempts inside the rolling window, counted across every incident."""
        floor_ns = self.observed_monotonic_ns - int(window_s * 1e9)
        return sum(1 for a in self.recovery_attempts
                   if a.attempted_monotonic_ns >= floor_ns)

    def attempts_against_current_process(self) -> int:
        return sum(1 for a in self.recovery_attempts if a.targets(self.capture_process))

    def as_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["capture_process"] = (
            None if self.capture_process is None else self.capture_process.as_dict())
        payload["incident_capture_process"] = (
            None if self.incident_capture_process is None
            else self.incident_capture_process.as_dict())
        payload["recovery_attempts"] = [a.as_dict() for a in self.recovery_attempts]
        payload["recovery_attempt_count"] = self.recovery_attempt_count()
        payload["attempts_in_window"] = self.attempts_in_window()
        payload["starvation_duration_s"] = self.starvation_duration_s()
        return payload


@dataclass(frozen=True)
class RecoveryDecision:
    """What the policy concluded, why, and what it declined to conclude."""

    decision: str
    reason: str
    reason_note: str
    observation: RecoveryObservation
    # Published on every decision, including NO_ACTION. A reader must not have to
    # find the one payload where the refusal to diagnose was written down.
    cause: str = UNDETERMINED_CAUSE
    restoration_definition: str = RESTORATION_DEFINITION

    def as_dict(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA,
            "decision": self.decision,
            "reason": self.reason,
            "reason_note": self.reason_note,
            "cause": self.cause,
            "restoration_definition": self.restoration_definition,
            "policy": {
                "sustained_starvation_s": SUSTAINED_STARVATION_S,
                "recovery_cooldown_s": RECOVERY_COOLDOWN_S,
                "recovery_attempt_limit": RECOVERY_ATTEMPT_LIMIT,
                "attempt_window_s": ATTEMPT_WINDOW_S,
                "max_attempts_per_process": MAX_ATTEMPTS_PER_PROCESS,
                "attempt_window_authority": ATTEMPT_WINDOW_AUTHORITY,
                "policy_authority": "CONFIGURED_POLICY",
                "executes_restart": False,
                "execution_owner": "CAPTURE_SERVICE_LAYER_NOT_THIS_MODULE",
            },
            "observation": self.observation.as_dict(),
        }


def _decision(decision: str, reason: str,
              observation: RecoveryObservation) -> RecoveryDecision:
    return RecoveryDecision(decision=decision, reason=reason,
                            reason_note=REASONS[reason], observation=observation)


def decide(observation: RecoveryObservation) -> RecoveryDecision:
    """Pure. No clock, no filesystem, no side effect, no restart.

    The gates are ordered so the reason names the *first* thing that was not
    true. A starved source with a changed capture process and an exhausted
    attempt count should report the changed process: that is the fact an
    operator needs, and reporting the attempt limit instead would send them to
    a rate limiter for a problem that is not one.
    """
    if observation.transport_state != "CONNECTED":
        return _decision(NO_ACTION, "TRANSPORT_NOT_CONNECTED", observation)
    if observation.availability != "SOURCE_STARVED":
        return _decision(NO_ACTION, "NOT_STARVED", observation)
    if observation.incident_id is None or observation.incident_opened_monotonic_ns is None:
        return _decision(NO_ACTION, "NO_OPEN_INCIDENT", observation)

    duration_s = observation.starvation_duration_s()
    if duration_s is None or duration_s < SUSTAINED_STARVATION_S:
        return _decision(NO_ACTION, "STARVATION_TOO_BRIEF", observation)

    if observation.capture_process is None or observation.incident_capture_process is None:
        return _decision(NO_ACTION, "CAPTURE_PROCESS_IDENTITY_UNAVAILABLE", observation)
    if not observation.capture_process.same_process_as(observation.incident_capture_process):
        # Restarting again would be restarting something that already restarted.
        return _decision(NO_ACTION, "CAPTURE_PROCESS_CHANGED", observation)

    since_s = observation.since_last_attempt_s()
    if since_s is not None and since_s < RECOVERY_COOLDOWN_S:
        return _decision(NO_ACTION, "COOLDOWN_ACTIVE", observation)

    # Suppression is checked last, and only here. Reached earlier it would let a
    # perfectly healthy source report RECOVERY_SUPPRESSED because of an incident
    # that ended an hour ago.
    if observation.attempts_against_current_process() >= MAX_ATTEMPTS_PER_PROCESS:
        return _decision(RECOVERY_SUPPRESSED, "PROCESS_ALREADY_ATTEMPTED", observation)
    if observation.attempts_in_window() >= RECOVERY_ATTEMPT_LIMIT:
        return _decision(RECOVERY_SUPPRESSED, "ATTEMPT_WINDOW_EXHAUSTED", observation)

    return _decision(REQUEST_RESTART, "SUSTAINED_STARVATION_SAME_PROCESS", observation)


# -- authorization ---------------------------------------------------------
# Still pure. An authorization is a claim about a specific broken thing, not a
# permission slip that floats free of it.

AUTHORIZATION_VALID = "AUTHORIZATION_VALID"
AUTHORIZATION_EXPIRED = "AUTHORIZATION_EXPIRED"
AUTHORIZATION_INVALIDATED = "AUTHORIZATION_INVALIDATED"

REVALIDATION_REASONS: Dict[str, str] = {
    "AUTHORIZATION_STILL_VALID": (
        "THE SAME INCIDENT, THE SAME PROCESS, AND NOT ONE SAMPLE SINCE"),
    "TTL_ELAPSED": (
        "THE AUTHORIZATION OUTLIVED THE EVIDENCE THAT JUSTIFIED IT"),
    "INCIDENT_CHANGED": (
        "THE OPEN INCIDENT IS NOT THE ONE THIS AUTHORIZATION WAS GRANTED FOR"),
    "INCIDENT_CLOSED": "THE INCIDENT THIS AUTHORIZATION NAMED IS NO LONGER OPEN",
    "TARGET_IDENTITY_CHANGED": (
        "THE CAPTURE PROCESS IS NOT THE ONE THIS AUTHORIZATION TARGETED. "
        "RESTARTING NOW WOULD KILL SOMETHING THAT WAS NEVER JUDGED"),
    "TARGET_IDENTITY_UNAVAILABLE": (
        "THE CAPTURE PROCESS CANNOT BE IDENTIFIED, SO THE TARGET CANNOT BE "
        "SHOWN TO BE THE ONE THAT WAS AUTHORIZED"),
    "SAMPLES_ARRIVED_SINCE_AUTHORIZATION": (
        "DECODED SAMPLES ARRIVED AFTER THIS WAS AUTHORIZED. THE FAULT RESOLVED "
        "ITSELF AND THERE IS NOTHING LEFT TO RESTART"),
}


@dataclass(frozen=True)
class RecoveryAuthorization:
    """Permission to restart one named process, for one named incident.

    Latched deliberately. A wedged source flaps CONNECTED/CONNECTING every few
    seconds, so a transport check sampled at the wrong instant would revoke a
    restart that fifteen seconds of evidence had already justified. Socket
    flapping is not recovery, and an authorization that a flap can cancel is an
    authorization that a broken source can veto.
    """

    authorization_id: str
    incident_id: str
    target: ProcessIdentity
    authorized_monotonic_ns: int
    sequence_at_authorization: int
    ttl_s: float = AUTHORIZATION_TTL_S
    # None until a restart has actually been requested against this
    # authorization. Its presence selects which question may be asked of it, so
    # the two callsites cannot be wired to the wrong one.
    attempted_monotonic_ns: Optional[int] = None

    @property
    def acted(self) -> bool:
        return self.attempted_monotonic_ns is not None

    def with_attempt(self, attempted_monotonic_ns: int) -> "RecoveryAuthorization":
        """Move the authorization past the fence. One-way."""
        if self.acted:
            raise ValueError("this authorization has already been acted on")
        return replace(self, attempted_monotonic_ns=int(attempted_monotonic_ns))

    def expires_monotonic_ns(self) -> int:
        return self.authorized_monotonic_ns + int(self.ttl_s * 1e9)

    def as_dict(self) -> Dict[str, Any]:
        return {"authorization_id": self.authorization_id,
                "incident_id": self.incident_id,
                "target": self.target.as_dict(),
                "authorized_monotonic_ns": self.authorized_monotonic_ns,
                "sequence_at_authorization": self.sequence_at_authorization,
                "ttl_s": self.ttl_s,
                "expires_monotonic_ns": self.expires_monotonic_ns(),
                "attempted_monotonic_ns": self.attempted_monotonic_ns,
                "phase": "ACTED" if self.acted else "FENCED"}


@dataclass(frozen=True)
class Revalidation:
    outcome: str
    reason: str
    reason_note: str

    @property
    def valid(self) -> bool:
        return self.outcome == AUTHORIZATION_VALID

    def as_dict(self) -> Dict[str, Any]:
        return {"outcome": self.outcome, "reason": self.reason,
                "reason_note": self.reason_note, "valid": self.valid}


def _revalidation(outcome: str, reason: str) -> Revalidation:
    return Revalidation(outcome=outcome, reason=reason,
                        reason_note=REVALIDATION_REASONS[reason])


def authorize(decision: RecoveryDecision, *, authorization_id: str
              ) -> Optional[RecoveryAuthorization]:
    """Latch a REQUEST_RESTART to its incident and target. Pure."""
    if decision.decision != REQUEST_RESTART:
        return None
    observation = decision.observation
    if observation.incident_id is None or observation.capture_process is None:
        return None
    return RecoveryAuthorization(
        authorization_id=authorization_id,
        incident_id=observation.incident_id,
        target=observation.capture_process,
        authorized_monotonic_ns=observation.observed_monotonic_ns,
        sequence_at_authorization=observation.latest_sequence)


def revalidate(authorization: RecoveryAuthorization,
               observation: RecoveryObservation) -> Revalidation:
    """The fence, immediately before acting. Pure, and deliberately not transport.

    Transport state is absent from this by design. The question here is not "is
    the socket up right now" but "is this still the same broken thing I was
    authorized against, and has it stayed broken". A flapping wedge answers the
    first differently every few seconds and the second identically every time.

    Refuses to run on an authorization that has already been acted on. After a
    restart the identity has changed *because we changed it*, and running the
    fence there reads the intended outcome as a disqualification -- which is
    exactly what it did, on a live wedge, on 2026-09-07.
    """
    if authorization.acted:
        raise ValueError(
            "revalidate is the pre-action fence and cannot judge an acted "
            "authorization; use evaluate_attempt")
    if observation.observed_monotonic_ns >= authorization.expires_monotonic_ns():
        return _revalidation(AUTHORIZATION_EXPIRED, "TTL_ELAPSED")
    if observation.incident_id is None:
        return _revalidation(AUTHORIZATION_INVALIDATED, "INCIDENT_CLOSED")
    if observation.incident_id != authorization.incident_id:
        return _revalidation(AUTHORIZATION_INVALIDATED, "INCIDENT_CHANGED")
    if observation.capture_process is None:
        return _revalidation(AUTHORIZATION_INVALIDATED, "TARGET_IDENTITY_UNAVAILABLE")
    if not observation.capture_process.same_process_as(authorization.target):
        return _revalidation(AUTHORIZATION_INVALIDATED, "TARGET_IDENTITY_CHANGED")
    last_sample_ns = observation.last_sample_monotonic_ns()
    if (last_sample_ns is not None
            and last_sample_ns > authorization.authorized_monotonic_ns):
        return _revalidation(AUTHORIZATION_INVALIDATED,
                             "SAMPLES_ARRIVED_SINCE_AUTHORIZATION")
    return _revalidation(AUTHORIZATION_VALID, "AUTHORIZATION_STILL_VALID")


# -- outcome of a completed attempt ---------------------------------------

SAMPLE_FLOW_RESTORED = "SAMPLE_FLOW_RESTORED"
PROCESS_RESTARTED_STILL_STARVED = "PROCESS_RESTARTED_STILL_STARVED"
# Distinct from the above, and the distinction is honesty rather than detail.
# PROCESS_RESTARTED_STILL_STARVED asserts a restart happened. If the incarnation
# never advanced, no restart was observed, and reporting one would be a claim
# about something that did not occur.
RESTART_NOT_OBSERVED = "RESTART_NOT_OBSERVED"
RECOVERY_OUTCOME_PENDING = "RECOVERY_OUTCOME_PENDING"
RECOVERY_OUTCOMES: Tuple[str, ...] = (
    SAMPLE_FLOW_RESTORED, PROCESS_RESTARTED_STILL_STARVED,
    RESTART_NOT_OBSERVED, RECOVERY_OUTCOME_PENDING)

# What the observed incarnation says about the authorized one, after acting.
SUPERSEDED = "SUPERSEDED"          # the fence advanced: this is what a restart does
UNCHANGED = "UNCHANGED"            # the same process is still there
UNRELATED = "UNRELATED"            # a different boot, or an older incarnation
UNOBSERVABLE = "UNOBSERVABLE"      # nothing holds the endpoint to identify
SUPERSESSION_STATES: Tuple[str, ...] = (SUPERSEDED, UNCHANGED, UNRELATED, UNOBSERVABLE)


def supersession(authorized: ProcessIdentity,
                 observed: Optional[ProcessIdentity]) -> str:
    """Which side of the generation boundary the observed process is on."""
    if observed is None:
        return UNOBSERVABLE
    if observed.same_process_as(authorized):
        return UNCHANGED
    if observed.supersedes(authorized):
        return SUPERSEDED
    return UNRELATED


@dataclass(frozen=True)
class AttemptEvaluation:
    """What the restart did, and which side of the fence the process is on."""

    outcome: str
    supersession: str
    elapsed_s: float
    sequence_advanced: bool
    samples_after_authorization: bool

    def as_dict(self) -> Dict[str, Any]:
        return {"outcome": self.outcome, "supersession": self.supersession,
                "elapsed_s": round(self.elapsed_s, 3),
                "sequence_advanced": self.sequence_advanced,
                "samples_after_authorization": self.samples_after_authorization,
                "note": ("A CHANGED INCARNATION AFTER ACTING IS THE INTENDED "
                         "OUTCOME, NOT A DISQUALIFICATION. SUCCESS IS STILL "
                         "DECIDED BY DECODED SAMPLES AND A SEQUENCE ADVANCE")}


def evaluate_attempt(authorization: RecoveryAuthorization,
                     observation: RecoveryObservation,
                     *, deadline_s: float = OBSERVATION_DEADLINE_S
                     ) -> AttemptEvaluation:
    """Judge an authorization that has been acted on. Pure.

    The identity comparison here has the opposite polarity to the fence. A
    restart necessarily produces a new incarnation, so ``SUPERSEDED`` is what
    success looks like from the identity side and is never a reason to
    invalidate. It is recorded, then the outcome falls through to the only
    things that actually establish recovery: decoded samples arriving after the
    authorization, and a frame sequence past the mark taken at that moment.

    That ordering is why ``SAMPLE_FLOW_RESTORED`` is reachable by construction
    rather than by a caller remembering to ask the right question first.

    Refuses to run on an authorization that has not been acted on: before a
    restart, a changed identity means something else replaced the target, which
    is a fence failure and not an outcome.
    """
    if not authorization.acted:
        raise ValueError(
            "evaluate_attempt judges a restart that was made; an unacted "
            "authorization is judged by revalidate")
    where = supersession(authorization.target, observation.capture_process)
    last_sample_ns = observation.last_sample_monotonic_ns()
    samples_after = (last_sample_ns is not None
                     and last_sample_ns > authorization.attempted_monotonic_ns)
    advanced = observation.latest_sequence > authorization.sequence_at_authorization
    elapsed_s = (observation.observed_monotonic_ns
                 - authorization.attempted_monotonic_ns) / 1e9
    if samples_after and advanced:
        outcome = SAMPLE_FLOW_RESTORED
    elif elapsed_s < deadline_s:
        outcome = RECOVERY_OUTCOME_PENDING
    elif where == SUPERSEDED:
        outcome = PROCESS_RESTARTED_STILL_STARVED
    else:
        # The deadline passed and the incarnation never advanced. Reporting
        # PROCESS_RESTARTED_STILL_STARVED here would assert a restart that was
        # not observed to happen.
        outcome = RESTART_NOT_OBSERVED
    return AttemptEvaluation(outcome=outcome, supersession=where,
                             elapsed_s=elapsed_s, sequence_advanced=advanced,
                             samples_after_authorization=samples_after)


def recovery_outcome(authorization: RecoveryAuthorization,
                     observation: RecoveryObservation,
                     *, deadline_s: float = OBSERVATION_DEADLINE_S) -> str:
    """Did the restart work? Two conditions, both required.

    Decoded samples must have arrived AFTER the authorization, and the frame
    sequence must have advanced past where it stood at that moment. The second
    is not "sequence > 0": an incident beginning after thousands of good frames
    would satisfy that on the strength of history. Only movement past the
    recorded mark shows this capture chain producing something now.

    A new PID, a listening socket, a successful connection and another 12-byte
    RTL0 greeting are all absent from this function on purpose.
    """
    last_sample_ns = observation.last_sample_monotonic_ns()
    samples_after = (last_sample_ns is not None
                     and last_sample_ns > authorization.authorized_monotonic_ns)
    sequence_advanced = observation.latest_sequence > authorization.sequence_at_authorization
    if samples_after and sequence_advanced:
        return SAMPLE_FLOW_RESTORED
    elapsed_s = (observation.observed_monotonic_ns
                 - authorization.authorized_monotonic_ns) / 1e9
    if elapsed_s >= deadline_s:
        return PROCESS_RESTARTED_STILL_STARVED
    # Neither yet. "Not yet" and "never" are different answers and the deadline
    # is what separates them.
    return RECOVERY_OUTCOME_PENDING


# -- collection ------------------------------------------------------------
# Everything below reads the world. Nothing below decides anything.

BOOT_ID_PATH = "/proc/sys/kernel/random/boot_id"


def kernel_boot_id(path: str = BOOT_ID_PATH) -> Optional[str]:
    """This boot's identity, or None. A guess here would defeat the field."""
    try:
        with open(path, "r", encoding="ascii") as handle:
            return handle.read().strip() or None
    except OSError:
        return None


def process_start_ticks(pid: int, proc_root: str = "/proc") -> Optional[int]:
    """Field 22 of /proc/<pid>/stat, in native ticks.

    Parsed from the last ``)`` rather than by splitting on whitespace: field 2
    is the executable name in parentheses and may contain spaces and parens of
    its own, so a naive split silently returns a different field for a process
    whose name happens to contain one.
    """
    try:
        with open(f"{proc_root}/{pid}/stat", "r", encoding="utf-8") as handle:
            line = handle.read()
    except OSError:
        return None
    try:
        after_comm = line[line.rindex(")") + 2:]
    except ValueError:
        return None
    fields = after_comm.split()
    # fields[0] is field 3 (state), so field 22 is fields[19].
    if len(fields) < 20:
        return None
    try:
        return int(fields[19])
    except ValueError:
        return None


def _proc_hex_address(host: str) -> Optional[str]:
    """The /proc/net/tcp spelling of an IPv4 address: little-endian, upper hex.

    127.0.0.1 becomes "0100007F". Returns None for anything that is not a plain
    IPv4 literal, which makes the caller refuse rather than widen its match.
    """
    try:
        packed = socket.inet_aton(host)
    except OSError:
        return None
    return "".join(f"{byte:02X}" for byte in reversed(packed))


def _listener_inode(host: str, port: int, proc_root: str = "/proc") -> Optional[int]:
    """The inode LISTENing on exactly `host:port`.

    The address is matched, not just the port. Matching a bare port would tie
    recovery authority to "whoever holds 1234" rather than to SCYTHE's declared
    capture endpoint, and the process this policy may restart must be the one
    actually serving the connection the bridge is starved on.

    Only /proc/net/tcp is consulted. An IPv6 listener is deliberately not
    matched: the configured endpoint is an IPv4 literal, and a socket on a
    different address family is a different endpoint.
    """
    wanted = _proc_hex_address(host)
    if wanted is None:
        return None
    try:
        with open(f"{proc_root}/net/tcp", "r", encoding="utf-8") as handle:
            rows = handle.read().splitlines()[1:]
    except OSError:
        return None
    for row in rows:
        parts = row.split()
        if len(parts) < 10 or parts[3] != "0A":           # 0A == TCP_LISTEN
            continue
        try:
            local_hex, local_port = parts[1].rsplit(":", 1)
            if int(local_port, 16) != port or local_hex.upper() != wanted:
                continue
            return int(parts[9])
        except (ValueError, IndexError):
            continue
    return None


def pid_holding_listener(host: str, port: int,
                         proc_root: str = "/proc") -> Optional[int]:
    """Which process holds the declared capture endpoint, from /proc alone.

    The bridge owns a socket, not a child: it never starts rtl_tcp and has no
    handle on it. The endpoint is the only thing the two demonstrably share, so
    the holder of that listening socket is the process this policy is about --
    a stronger answer than a unit's MainPID, which is what systemd believes
    rather than what is serving the connection we are starved on.

    A wildcard-bound listener (0.0.0.0:1234) would serve the bridge's connection
    and is deliberately NOT matched. rtl_tcp is required to bind loopback only,
    and a process violating that is not one this module will identify as a
    restart target. The result is no identity, and therefore NO_ACTION -- the
    safe direction, and consistent with the bind address being a security
    control rather than a preference.

    Reads only. Nothing here can signal, start or stop anything.
    """
    inode = _listener_inode(host, port, proc_root)
    if inode is None:
        return None
    target = f"socket:[{inode}]"
    try:
        entries = os.listdir(proc_root)
    except OSError:
        return None
    for entry in entries:
        if not entry.isdigit():
            continue
        fd_dir = f"{proc_root}/{entry}/fd"
        try:
            for fd in os.listdir(fd_dir):
                try:
                    if os.readlink(f"{fd_dir}/{fd}") == target:
                        return int(entry)
                except OSError:
                    continue
        except OSError:
            continue
    return None


def capture_process_identity(pid: Optional[int], *, proc_root: str = "/proc",
                             boot_id_path: str = BOOT_ID_PATH
                             ) -> Optional[ProcessIdentity]:
    """All three parts or nothing. A partial identity cannot answer 'unchanged'."""
    if pid is None:
        return None
    boot_id = kernel_boot_id(boot_id_path)
    start_ticks = process_start_ticks(pid, proc_root=proc_root)
    if boot_id is None or start_ticks is None:
        return None
    return ProcessIdentity(boot_id=boot_id, pid=int(pid), start_ticks=start_ticks)


def observe(capture_source: Dict[str, Any], *, capture_pid: Optional[int] = None,
            incident_capture_process: Optional[ProcessIdentity] = None,
            recovery_attempts: tuple = (),
            latest_sequence: int = 0,
            reconnect_count: int = 0,
            observed_monotonic_ns: Optional[int] = None,
            proc_root: str = "/proc",
            boot_id_path: str = BOOT_ID_PATH) -> RecoveryObservation:
    """Assemble one observation from a bridge capture_source block and /proc.

    The incident id and its opening instant come from the bridge, which owns
    them; this module does not open incidents, and inventing one here would put
    two different components in charge of the same fact.
    """
    incident = capture_source.get("starvation_incident") or {}
    opened_ns = incident.get("opened_monotonic_ns")
    return RecoveryObservation(
        observed_monotonic_ns=(time.monotonic_ns() if observed_monotonic_ns is None
                               else int(observed_monotonic_ns)),
        availability=str(capture_source.get("availability") or "UNDECLARED"),
        transport_state=str(capture_source.get("transport_state") or "UNDECLARED"),
        flow_state=str(capture_source.get("sample_flow_state") or "UNDECLARED"),
        last_sample_age_ms=capture_source.get("last_sample_age_ms"),
        reconnect_count=int(reconnect_count),
        incident_id=incident.get("incident_id"),
        incident_opened_monotonic_ns=None if opened_ns is None else int(opened_ns),
        capture_process=capture_process_identity(
            capture_pid, proc_root=proc_root, boot_id_path=boot_id_path),
        incident_capture_process=incident_capture_process,
        recovery_attempts=tuple(recovery_attempts),
        latest_sequence=int(latest_sequence),
    )


def policy_status() -> Dict[str, Any]:
    """Declared before anything acts on it, for the same reason the detector
    contract was: a policy published after the thing it governs is a description."""
    return {
        "schema": SCHEMA,
        "phase": "DECISION_ONLY",
        "executes_restart": False,
        "execution_owner": "CAPTURE_SERVICE_LAYER_NOT_THIS_MODULE",
        "decisions": list(DECISIONS),
        "reasons": dict(REASONS),
        "sustained_starvation_s": SUSTAINED_STARVATION_S,
        "recovery_cooldown_s": RECOVERY_COOLDOWN_S,
        "recovery_attempt_limit": RECOVERY_ATTEMPT_LIMIT,
        "attempt_window_s": ATTEMPT_WINDOW_S,
        "max_attempts_per_process": MAX_ATTEMPTS_PER_PROCESS,
        "attempt_window_authority": ATTEMPT_WINDOW_AUTHORITY,
        "authorization_ttl_s": AUTHORIZATION_TTL_S,
        "observation_deadline_s": OBSERVATION_DEADLINE_S,
        "modes": list(RECOVERY_MODES),
        "default_mode": DEFAULT_RECOVERY_MODE,
        "revalidation_excludes_transport": True,
        "revalidation_note": (
            "THE FINAL CHECK ASKS WHETHER THIS IS STILL THE SAME BROKEN THING, "
            "NOT WHETHER THE SOCKET IS UP AT THIS INSTANT. SOCKET FLAPPING IS "
            "NOT RECOVERY"),
        "success_requires": ("DECODED_SAMPLES_AFTER_AUTHORIZATION",
                             "LATEST_SEQUENCE_ABOVE_SEQUENCE_AT_AUTHORIZATION"),
        "recovery_outcomes": list(RECOVERY_OUTCOMES),
        "supersession_states": list(SUPERSESSION_STATES),
        "identity_polarity_note": (
            "THE SAME IDENTITY COMPARISON IS A FENCE BEFORE ACTING AND A "
            "SUPERSESSION SIGNAL AFTER IT. A RESTART NECESSARILY CHANGES THE "
            "INCARNATION, SO A CHANGED IDENTITY AFTER ACTING IS THE INTENDED "
            "OUTCOME AND NEVER A DISQUALIFICATION"),
        "policy_authority": "CONFIGURED_POLICY",
        "cause": UNDETERMINED_CAUSE,
        "restoration_definition": RESTORATION_DEFINITION,
        "process_identity_fields": ("kernel_boot_id", "capture_pid",
                                    "capture_process_start_ticks"),
        "audit_note": (
            "RECOVERY EVENTS REACH THE CAPTURE-OWNER AUDIT HISTORY IN "
            "rf_capture_audit, WHICH IS INDEPENDENT OF RING ALLOCATION. RING "
            "INVALIDATION AND INCIDENT AUDITING ARE NOT THE SAME MECHANISM -- "
            "OBSERVED 2026-09-06, WHEN A 45 s STARVATION WROTE NOTHING TO THE "
            "INVALIDATION HISTORY BECAUSE NO RING HAD BEEN ALLOCATED"),
    }
