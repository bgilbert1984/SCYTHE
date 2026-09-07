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

# Requests per incident, not per wall-clock window.
#
# The declared observation set carries recovery_attempt_count and
# last_recovery_attempt_monotonic_ns -- a count and one timestamp. A true
# "3 per 10 minutes" rolling window needs the timestamps of every attempt, and
# inventing one from a count would be a rate limit that cannot say what it
# limited. Scoping the count to the incident is what these fields actually
# support, and it binds attempts to the fault they address rather than to a
# clock. With the cooldown above, three requests span at least 240 s.
RECOVERY_ATTEMPT_LIMIT = 3
ATTEMPT_WINDOW_AUTHORITY = "PER_INCIDENT_COUNT_NOT_WALL_CLOCK_WINDOW"

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
    "ATTEMPT_LIMIT_REACHED": (
        "THIS INCIDENT HAS ALREADY EXHAUSTED ITS RESTART REQUESTS. FURTHER "
        "REQUESTS WOULD BE REPETITION, NOT RECOVERY"),
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

    def as_dict(self) -> Dict[str, Any]:
        return {"kernel_boot_id": self.boot_id, "capture_pid": self.pid,
                "capture_process_start_ticks": self.start_ticks,
                "start_ticks_authority": "KERNEL_PROC_STAT_FIELD_22_NATIVE_TICKS"}


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
    recovery_attempt_count: int = 0
    last_recovery_attempt_monotonic_ns: Optional[int] = None

    def starvation_duration_s(self) -> Optional[float]:
        if self.incident_opened_monotonic_ns is None:
            return None
        return (self.observed_monotonic_ns - self.incident_opened_monotonic_ns) / 1e9

    def since_last_attempt_s(self) -> Optional[float]:
        if self.last_recovery_attempt_monotonic_ns is None:
            return None
        return (self.observed_monotonic_ns - self.last_recovery_attempt_monotonic_ns) / 1e9

    def as_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["capture_process"] = (
            None if self.capture_process is None else self.capture_process.as_dict())
        payload["incident_capture_process"] = (
            None if self.incident_capture_process is None
            else self.incident_capture_process.as_dict())
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
    if observation.recovery_attempt_count >= RECOVERY_ATTEMPT_LIMIT:
        return _decision(RECOVERY_SUPPRESSED, "ATTEMPT_LIMIT_REACHED", observation)

    return _decision(REQUEST_RESTART, "SUSTAINED_STARVATION_SAME_PROCESS", observation)


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
            recovery_attempt_count: int = 0,
            last_recovery_attempt_monotonic_ns: Optional[int] = None,
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
        recovery_attempt_count=int(recovery_attempt_count),
        last_recovery_attempt_monotonic_ns=(
            None if last_recovery_attempt_monotonic_ns is None
            else int(last_recovery_attempt_monotonic_ns)),
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
        "attempt_window_authority": ATTEMPT_WINDOW_AUTHORITY,
        "policy_authority": "CONFIGURED_POLICY",
        "cause": UNDETERMINED_CAUSE,
        "restoration_definition": RESTORATION_DEFINITION,
        "process_identity_fields": ("kernel_boot_id", "capture_pid",
                                    "capture_process_start_ticks"),
        "audit_note": (
            "A RECOVERY EVENT MUST REACH THE CAPTURE-OWNER AUDIT HISTORY EVEN "
            "WHEN NO IQ RING EXISTS. RING INVALIDATION AND INCIDENT AUDITING "
            "CANNOT BE THE SAME MECHANISM, OR PRE-FIRST-SAMPLE FAILURES LEAVE "
            "NO RECORD -- OBSERVED 2026-09-06, WHEN A 45 s STARVATION WROTE "
            "NOTHING TO THE INVALIDATION HISTORY BECAUSE NO RING HAD BEEN "
            "ALLOCATED. NOT IMPLEMENTED IN THIS PHASE"),
    }
