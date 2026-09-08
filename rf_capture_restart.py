"""The one module that can act. Everything with authority lives here.

Read this file to audit what SCYTHE is able to do to a process. The policy in
``rf_capture_recovery`` decides; the store in ``rf_capture_audit`` records;
neither can restart anything. Keeping the authority in a single small file is
the point of the split -- a reviewer should not have to establish the blast
radius by reading three modules.

What it can do, exhaustively
----------------------------
Ask the *user* systemd manager to restart one hard-coded unit:

    RestartUnit("scythe-rtl-tcp.service", "replace")

The unit name is a module constant. There is no parameter for it, no parameter
for the action, no parameter for the bus address, and no string that a caller
contributes to the command. The argument vector is a frozen tuple passed to
``subprocess.run`` with ``shell=False``, so there is no shell to inject into and
nothing to quote.

What a success means
--------------------
``RESTART_REQUEST_ACCEPTED`` means the unit manager returned a job object. It
does not mean the process restarted, that it opened a socket, or that a single
sample arrived. Recovery success is decided elsewhere, by decoded samples and a
frame sequence that moved past its recorded mark.
"""

from __future__ import annotations

from dataclasses import dataclass
import subprocess
from typing import Dict, Optional, Tuple

from rf_capture_recovery import (
    MODE_ARMED, MODE_DISABLED, MODE_SHADOW, RECOVERY_OUTCOME_PENDING,
    REQUEST_RESTART, RECOVERY_SUPPRESSED, RecoveryAttempt,
    RecoveryAuthorization, RecoveryObservation, authorize, decide,
    evaluate_attempt, revalidate,
)


# The target, fixed at import. Not configurable, because a configurable target
# is an arbitrary-unit-restart primitive wearing a recovery policy's name.
RECOVERY_UNIT = "scythe-rtl-tcp.service"
DBUS_OPERATION = f'RestartUnit("{RECOVERY_UNIT}", "replace")'

# The complete argument vector. A frozen tuple, every element a literal.
RESTART_ARGV: Tuple[str, ...] = (
    "busctl", "--user", "call",
    "org.freedesktop.systemd1",
    "/org/freedesktop/systemd1",
    "org.freedesktop.systemd1.Manager",
    "RestartUnit", "ss", RECOVERY_UNIT, "replace",
)
RESTART_TIMEOUT_S = 20.0

RESTART_REQUEST_ACCEPTED = "RESTART_REQUEST_ACCEPTED"
RESTART_REQUEST_FAILED = "RESTART_REQUEST_FAILED"

# Enough of a failure to identify it; never the whole of one. A failing busctl
# can emit arbitrarily much, and an audit trail a failure can flood is an audit
# trail a failure can erase.
MAX_REPLY_CHARS = 200


@dataclass(frozen=True)
class RestartRequestResult:
    outcome: str
    detail: str

    @property
    def accepted(self) -> bool:
        return self.outcome == RESTART_REQUEST_ACCEPTED


def request_restart(*, runner=subprocess.run) -> RestartRequestResult:
    """Invoke the fixed vector. The runner is injectable so tests need no bus."""
    try:
        completed = runner(list(RESTART_ARGV), capture_output=True, text=True,
                           timeout=RESTART_TIMEOUT_S, check=False, shell=False)
    except Exception as exc:                      # noqa: BLE001 - reported, not raised
        return RestartRequestResult(RESTART_REQUEST_FAILED,
                                    f"{type(exc).__name__}: {exc}"[:MAX_REPLY_CHARS])
    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    if completed.returncode == 0:
        return RestartRequestResult(RESTART_REQUEST_ACCEPTED, stdout[:MAX_REPLY_CHARS])
    return RestartRequestResult(
        RESTART_REQUEST_FAILED,
        f"rc={completed.returncode} {stderr or stdout}"[:MAX_REPLY_CHARS])


class RecoveryCoordinator:
    """Drives decide -> authorize -> revalidate -> act, under a declared mode.

    Holds the attempt sequence and the open authorization. Every transition it
    makes is written to the audit store, including the ones where it declined,
    because a recovery system that only logs its actions cannot be shown to have
    been correct when it did nothing.
    """

    def __init__(self, audit, *, mode: str = MODE_SHADOW,
                 requester=request_restart) -> None:
        if mode not in (MODE_DISABLED, MODE_SHADOW, MODE_ARMED):
            raise ValueError(f"unknown recovery mode {mode!r}")
        self.mode = mode
        self._audit = audit
        self._requester = requester
        self._authorization: Optional[RecoveryAuthorization] = None
        self._attempts: list = []
        # Shadow's simulated budget, kept strictly apart from the real one. A
        # shadow judgement must never consume a real attempt, and a real attempt
        # must never be inferred from a shadow run.
        self._shadow_attempts: list = []
        self._serial = 0

    @property
    def attempts(self) -> tuple:
        """Restarts actually requested. Shadow never appears here."""
        return tuple(self._attempts)

    @property
    def policy_attempts(self) -> tuple:
        """What the policy should see as spent budget, for this mode.

        In SHADOW this is the simulated ledger. Without it the breaker never
        engages in shadow, and the record reports one would-be restart per
        check for the whole outage -- 2688 of them across four hours on
        2026-09-07, where ARMED would have made one attempt and then suppressed.
        A shadow record that cannot reproduce its own circuit breaker is
        measuring the amplification the breaker exists to prevent.
        """
        return tuple(self._shadow_attempts) if self.mode == MODE_SHADOW else self.attempts

    @property
    def authorization(self) -> Optional[RecoveryAuthorization]:
        return self._authorization

    def _next_id(self, kind: str) -> str:
        self._serial += 1
        return f"{kind}-{self._serial:04d}"

    def _record(self, event: str, reason: str, observation, **kwargs) -> None:
        self._audit.record(
            event, mode=self.mode, reason=reason,
            incident_id=getattr(observation, "incident_id", None),
            monotonic_ns=getattr(observation, "observed_monotonic_ns", None),
            **kwargs)

    def evaluate(self, observation: RecoveryObservation) -> Dict:
        """One step. Returns what was decided and what, if anything, was done."""
        if self.mode == MODE_DISABLED:
            return {"mode": self.mode, "decision": None,
                    "action": "NONE", "note": "RECOVERY EVALUATION IS DISABLED"}

        # An open authorization is settled before a new decision is taken, so an
        # expiry or an invalidation is always recorded rather than overwritten.
        if self._authorization is not None:
            settled = self._settle(observation)
            if settled is not None:
                return settled

        decision = decide(observation)
        if decision.decision == RECOVERY_SUPPRESSED:
            # Named apart in shadow: a simulated budget is not a real one, and
            # the counts must never be able to conflate them.
            event = ("WOULD_BE_SUPPRESSED" if self.mode == MODE_SHADOW
                     else "RECOVERY_SUPPRESSED")
            self._record(event, decision.reason, observation)
            return {"mode": self.mode, "decision": decision.decision,
                    "reason": decision.reason, "action": event}
        if decision.decision != REQUEST_RESTART:
            return {"mode": self.mode, "decision": decision.decision,
                    "reason": decision.reason, "action": "NONE"}

        authorization = authorize(decision, authorization_id=self._next_id("auth"))
        if authorization is None:
            return {"mode": self.mode, "decision": decision.decision,
                    "reason": decision.reason, "action": "NONE"}
        self._authorization = authorization
        self._record("AUTHORIZATION_CREATED", decision.reason, observation,
                     attempt_id=authorization.authorization_id,
                     target=authorization.target.as_dict(),
                     detail={"sequence_at_authorization":
                             authorization.sequence_at_authorization})

        check = revalidate(authorization, observation)
        if not check.valid:
            return self._drop(check, observation)

        if self.mode == MODE_SHADOW:
            # The terminal event for shadow. No attempt is appended, no budget
            # is consumed, and nothing is asked of the process.
            self._record("WOULD_REQUEST_RESTART", decision.reason, observation,
                         attempt_id=authorization.authorization_id,
                         target=authorization.target.as_dict(),
                         detail={"dbus_operation": DBUS_OPERATION,
                                 "would_run": " ".join(RESTART_ARGV)})
            # Spend the simulated budget, so the next judgement sees the breaker
            # the way ARMED would. Nothing here touches the real ledger.
            self._shadow_attempts.append(RecoveryAttempt(
                incident_id=authorization.incident_id,
                kernel_boot_id=authorization.target.boot_id,
                target_pid=authorization.target.pid,
                target_start_ticks=authorization.target.start_ticks,
                attempted_monotonic_ns=observation.observed_monotonic_ns))
            self._authorization = None
            return {"mode": self.mode, "decision": decision.decision,
                    "reason": decision.reason, "action": "WOULD_REQUEST_RESTART",
                    "dbus_operation": DBUS_OPERATION}

        return self._act(authorization, decision.reason, observation)

    def _act(self, authorization, reason, observation) -> Dict:
        attempt = RecoveryAttempt(
            incident_id=authorization.incident_id,
            kernel_boot_id=authorization.target.boot_id,
            target_pid=authorization.target.pid,
            target_start_ticks=authorization.target.start_ticks,
            attempted_monotonic_ns=observation.observed_monotonic_ns)
        # Appended BEFORE the request. A crash between the call and the record
        # must leave an attempt that happened and was not counted, never the
        # reverse: an uncounted attempt can restart a process for free.
        self._attempts.append(attempt)
        # Past the fence. From here the identity comparison flips polarity, and
        # the type carries which question may be asked of it.
        self._authorization = authorization = authorization.with_attempt(
            observation.observed_monotonic_ns)
        self._record("RESTART_ATTEMPTED", reason, observation,
                     attempt_id=authorization.authorization_id,
                     target=authorization.target.as_dict(),
                     detail={"dbus_operation": DBUS_OPERATION})
        result = self._requester()
        self._record(result.outcome, reason, observation,
                     attempt_id=authorization.authorization_id,
                     target=authorization.target.as_dict(),
                     detail={"dbus_operation": DBUS_OPERATION,
                             "reply": result.detail})
        if not result.accepted:
            self._authorization = None
        return {"mode": self.mode, "decision": REQUEST_RESTART, "reason": reason,
                "action": result.outcome, "dbus_operation": DBUS_OPERATION,
                "accepted_note": ("A D-BUS REPLY IS A JOB, NOT A SAMPLE. "
                                  "RECOVERY SUCCESS IS DECIDED BY DECODED "
                                  "SAMPLES AND A SEQUENCE ADVANCE")}

    def _settle(self, observation) -> Optional[Dict]:
        """Close out an open authorization, by the question its phase allows.

        Before acting the fence applies: a changed identity means something else
        replaced the target and the authorization no longer describes anything.
        After acting the same comparison is a supersession signal and a changed
        identity is the intended outcome, so the fence is not asked at all.

        Running the fence after acting is what recorded a successful live
        recovery as AUTHORIZATION_INVALIDATED / TARGET_IDENTITY_CHANGED on
        2026-09-07. The dispatch below is on the authorization's own phase, so
        the two cannot be swapped by a caller reading the code in the wrong
        order.
        """
        authorization = self._authorization
        if not authorization.acted:
            check = revalidate(authorization, observation)
            if not check.valid:
                return self._drop(check, observation)
            return None
        evaluation = evaluate_attempt(authorization, observation)
        if evaluation.outcome == RECOVERY_OUTCOME_PENDING:
            return None
        self._record(evaluation.outcome, evaluation.outcome, observation,
                     attempt_id=authorization.authorization_id,
                     target=authorization.target.as_dict(),
                     detail={"supersession": evaluation.supersession,
                             "reason": evaluation.reason,
                             "sequence_advanced": evaluation.sequence_advanced,
                             "elapsed_s": round(evaluation.elapsed_s, 3)})
        self._authorization = None
        return {"mode": self.mode, "decision": None, "action": evaluation.outcome,
                "supersession": evaluation.supersession}

    def _drop(self, check, observation) -> Dict:
        authorization = self._authorization
        self._record(check.outcome, check.reason, observation,
                     attempt_id=authorization.authorization_id if authorization else None,
                     target=authorization.target.as_dict() if authorization else None)
        # Only reachable before acting, so nothing here judges a restart. A
        # fence dropped because samples arrived is the fault resolving itself:
        # recorded, and reported as the outcome that matters rather than as the
        # mechanism, since a caller told only "AUTHORIZATION_INVALIDATED" would
        # have to infer that the source came back on its own.
        healed = (check.reason == "SAMPLES_ARRIVED_SINCE_AUTHORIZATION"
                  and authorization is not None
                  and observation.latest_sequence > authorization.sequence_at_authorization)
        if healed:
            self._record("SAMPLE_FLOW_RESTORED", check.reason, observation,
                         attempt_id=authorization.authorization_id,
                         target=authorization.target.as_dict(),
                         detail={"restored_without_intervention": True})
        self._authorization = None
        return {"mode": self.mode, "decision": None,
                "action": "SAMPLE_FLOW_RESTORED" if healed else check.outcome,
                "reason": check.reason}

    def status(self) -> Dict:
        return {
            "mode": self.mode,
            "modes": [MODE_DISABLED, MODE_SHADOW, MODE_ARMED],
            "unit": RECOVERY_UNIT,
            "unit_is_fixed": True,
            "dbus_operation": DBUS_OPERATION,
            "argv": list(RESTART_ARGV),
            "shell": False,
            "caller_supplied_arguments": "NONE",
            "accepted_means": ("A D-BUS JOB WAS CREATED. NOT THAT THE PROCESS "
                               "RESTARTED, NOT THAT A SAMPLE ARRIVED"),
            "attempts": [a.as_dict() for a in self._attempts],
            "authorization": (None if self._authorization is None
                              else self._authorization.as_dict()),
        }
