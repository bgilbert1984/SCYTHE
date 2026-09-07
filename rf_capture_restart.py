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
    MODE_ARMED, MODE_DISABLED, MODE_SHADOW, PROCESS_RESTARTED_STILL_STARVED,
    RECOVERY_OUTCOME_PENDING, REQUEST_RESTART, RECOVERY_SUPPRESSED,
    SAMPLE_FLOW_RESTORED, RecoveryAttempt, RecoveryAuthorization,
    RecoveryObservation, authorize, decide, recovery_outcome, revalidate,
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
        self._serial = 0

    @property
    def attempts(self) -> tuple:
        return tuple(self._attempts)

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
            self._record("RECOVERY_SUPPRESSED", decision.reason, observation)
            return {"mode": self.mode, "decision": decision.decision,
                    "reason": decision.reason, "action": "NONE"}
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
        """Close out an open authorization, or report the attempt's outcome."""
        authorization = self._authorization
        check = revalidate(authorization, observation)
        if not check.valid:
            return self._drop(check, observation)
        outcome = recovery_outcome(authorization, observation)
        if outcome == RECOVERY_OUTCOME_PENDING:
            return None
        self._record(outcome, outcome, observation,
                     attempt_id=authorization.authorization_id,
                     target=authorization.target.as_dict())
        self._authorization = None
        return {"mode": self.mode, "decision": None, "action": outcome}

    def _drop(self, check, observation) -> Dict:
        authorization = self._authorization
        self._record(check.outcome, check.reason, observation,
                     attempt_id=authorization.authorization_id if authorization else None,
                     target=authorization.target.as_dict() if authorization else None)
        # An authorization invalidated because samples arrived is the good case.
        # It is recorded with the same weight as the bad ones, and reported as
        # the outcome that matters: invalidation is the mechanism, restored
        # sample flow is the result. A caller told only "AUTHORIZATION_
        # INVALIDATED" would have to infer the recovery worked.
        restored = (check.reason == "SAMPLES_ARRIVED_SINCE_AUTHORIZATION"
                    and authorization is not None
                    and observation.latest_sequence > authorization.sequence_at_authorization)
        if restored:
            self._record(SAMPLE_FLOW_RESTORED, check.reason, observation,
                         attempt_id=authorization.authorization_id,
                         target=authorization.target.as_dict())
        self._authorization = None
        return {"mode": self.mode, "decision": None,
                "action": SAMPLE_FLOW_RESTORED if restored else check.outcome,
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
