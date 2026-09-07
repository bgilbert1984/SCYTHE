"""The executor and the coordinator: what SCYTHE is able to do to a process."""

import subprocess
import unittest

from rf_capture_audit import ACTING_EVENTS, CaptureRecoveryAudit, UnknownRecoveryEvent
from rf_capture_recovery import (
    MODE_ARMED, MODE_DISABLED, MODE_SHADOW, OBSERVATION_DEADLINE_S, ProcessIdentity,
    RecoveryObservation,
)
from rf_capture_restart import (
    DBUS_OPERATION, RECOVERY_UNIT, RESTART_ARGV, RESTART_REQUEST_ACCEPTED,
    RESTART_REQUEST_FAILED, RecoveryCoordinator, RestartRequestResult, request_restart,
)

BOOT = "0f4d1c9e-2a77-4a1e-9d3b-6c5f0a2b8e11"
SECOND = 1_000_000_000
NOW = 10_000 * SECOND


def _identity(pid=1727, ticks=13345):
    return ProcessIdentity(boot_id=BOOT, pid=pid, start_ticks=ticks)


def _starved(*, seconds=60.0, now=NOW, sequence=0, sample_age_ms=None, **overrides):
    fields = dict(
        observed_monotonic_ns=now,
        availability="SOURCE_STARVED", transport_state="CONNECTED", flow_state="STARVED",
        last_sample_age_ms=(seconds * 1000.0 if sample_age_ms is None else sample_age_ms),
        reconnect_count=411,
        incident_id="starve-incident-0001",
        incident_opened_monotonic_ns=int(now - seconds * SECOND),
        capture_process=_identity(), incident_capture_process=_identity(),
        recovery_attempts=(), latest_sequence=sequence,
    )
    fields.update(overrides)
    return RecoveryObservation(**fields)


class ExecutorSurfaceTests(unittest.TestCase):
    """The blast radius, asserted rather than described."""

    def test_the_unit_is_a_literal_and_takes_no_caller_input(self):
        self.assertEqual(RECOVERY_UNIT, "scythe-rtl-tcp.service")
        self.assertEqual(DBUS_OPERATION, 'RestartUnit("scythe-rtl-tcp.service", "replace")')
        self.assertIn(RECOVERY_UNIT, RESTART_ARGV)
        self.assertEqual(RESTART_ARGV[-1], "replace")

    def test_request_restart_accepts_no_arguments_that_reach_the_command(self):
        import inspect
        signature = inspect.signature(request_restart)
        self.assertEqual(list(signature.parameters), ["runner"],
                         "a unit, action or bus parameter would make this a "
                         "general-purpose restart primitive")

    def test_the_vector_is_frozen_and_never_shelled(self):
        self.assertIsInstance(RESTART_ARGV, tuple)
        captured = {}

        def runner(argv, **kwargs):
            captured["argv"] = argv
            captured["kwargs"] = kwargs
            return subprocess.CompletedProcess(argv, 0, "job 1", "")

        request_restart(runner=runner)
        self.assertEqual(captured["argv"], list(RESTART_ARGV))
        self.assertIs(captured["kwargs"]["shell"], False)
        self.assertIn("timeout", captured["kwargs"])

    def test_a_nonzero_reply_is_a_failure_not_an_acceptance(self):
        def runner(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 1, "", "Unit not found.")
        result = request_restart(runner=runner)
        self.assertEqual(result.outcome, RESTART_REQUEST_FAILED)
        self.assertFalse(result.accepted)
        self.assertIn("Unit not found", result.detail)

    def test_an_exception_is_reported_rather_than_raised_at_the_caller(self):
        def runner(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, 20)
        self.assertEqual(request_restart(runner=runner).outcome, RESTART_REQUEST_FAILED)

    def test_command_output_never_enters_a_record_unbounded(self):
        def runner(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 1, "", "x" * 100_000)
        self.assertLess(len(request_restart(runner=runner).detail), 300)


class ModeTests(unittest.TestCase):
    """Three states, and what each one is permitted to do."""

    def setUp(self):
        self.audit = CaptureRecoveryAudit()
        self.calls = []

    def _requester(self, outcome=RESTART_REQUEST_ACCEPTED, detail="job 1"):
        def request():
            self.calls.append(True)
            return RestartRequestResult(outcome, detail)
        return request

    def _coordinator(self, mode):
        return RecoveryCoordinator(self.audit, mode=mode, requester=self._requester())

    def test_disabled_evaluates_nothing(self):
        result = self._coordinator(MODE_DISABLED).evaluate(_starved())
        self.assertEqual(result["action"], "NONE")
        self.assertIsNone(result["decision"])
        self.assertEqual(self.audit.status()["recorded_total"], 0)
        self.assertEqual(self.calls, [])

    def test_shadow_judges_fully_and_touches_nothing(self):
        coordinator = self._coordinator(MODE_SHADOW)
        result = coordinator.evaluate(_starved())
        self.assertEqual(result["action"], "WOULD_REQUEST_RESTART")
        self.assertEqual(result["dbus_operation"], DBUS_OPERATION)
        self.assertEqual(self.calls, [], "shadow must not invoke the executor")
        events = [r["event"] for r in self.audit.status()["records"]]
        self.assertEqual(events, ["AUTHORIZATION_CREATED", "WOULD_REQUEST_RESTART"])

    def test_a_shadow_judgement_consumes_no_attempt_budget(self):
        coordinator = self._coordinator(MODE_SHADOW)
        for _ in range(5):
            coordinator.evaluate(_starved())
        self.assertEqual(coordinator.attempts, (),
                         "shadow judgements are not attempts")
        status = self.audit.status()
        self.assertEqual(status["shadow_decisions"], 5)
        self.assertEqual(status["counts"].get("RESTART_ATTEMPTED", 0), 0)

    def test_the_audit_store_refuses_an_acting_event_in_shadow(self):
        for event in ACTING_EVENTS:
            with self.assertRaises(UnknownRecoveryEvent):
                self.audit.record(event, mode=MODE_SHADOW, reason="x")

    def test_armed_acts_and_records_the_exact_dbus_operation(self):
        coordinator = self._coordinator(MODE_ARMED)
        result = coordinator.evaluate(_starved())
        self.assertEqual(result["action"], RESTART_REQUEST_ACCEPTED)
        self.assertEqual(self.calls, [True])
        events = [r["event"] for r in self.audit.status()["records"]]
        self.assertEqual(events, ["AUTHORIZATION_CREATED", "RESTART_ATTEMPTED",
                                  RESTART_REQUEST_ACCEPTED])
        attempted = self.audit.status()["records"][1]
        self.assertEqual(attempted["detail"]["dbus_operation"], DBUS_OPERATION)
        self.assertEqual(attempted["target"]["capture_pid"], 1727)

    def test_an_accepted_reply_is_never_reported_as_recovery(self):
        result = self._coordinator(MODE_ARMED).evaluate(_starved())
        self.assertIn("NOT A SAMPLE", result["accepted_note"])
        self.assertNotIn("SAMPLE_FLOW_RESTORED",
                         [r["event"] for r in self.audit.status()["records"]])

    def test_an_unknown_mode_is_refused_at_construction(self):
        with self.assertRaises(ValueError):
            RecoveryCoordinator(self.audit, mode="ARMD")

    def test_the_attempt_is_recorded_before_the_request_is_made(self):
        """A crash mid-call must leave an attempt counted, never uncounted.

        An uncounted attempt restarts a process for free and escapes the breaker.
        """
        order = []

        def request():
            order.append(("request", len(coordinator.attempts)))
            return RestartRequestResult(RESTART_REQUEST_ACCEPTED, "job 1")

        coordinator = RecoveryCoordinator(self.audit, mode=MODE_ARMED, requester=request)
        coordinator.evaluate(_starved())
        self.assertEqual(order, [("request", 1)])


class CoordinatorLifecycleTests(unittest.TestCase):
    """Authorization outcomes reach the audit, including the ones we like."""

    def setUp(self):
        self.audit = CaptureRecoveryAudit()
        self.coordinator = RecoveryCoordinator(
            self.audit, mode=MODE_ARMED,
            requester=lambda: RestartRequestResult(RESTART_REQUEST_ACCEPTED, "job 1"))

    def _events(self):
        return [r["event"] for r in self.audit.status()["records"]]

    def test_a_restart_that_works_records_sample_flow_restored(self):
        self.coordinator.evaluate(_starved())
        healed = _starved(now=NOW + 5 * SECOND, sequence=9, sample_age_ms=20.0)
        result = self.coordinator.evaluate(healed)
        self.assertEqual(result["action"], "SAMPLE_FLOW_RESTORED")
        self.assertIn("SAMPLE_FLOW_RESTORED", self._events())
        self.assertIsNone(self.coordinator.authorization)

    def test_a_restart_that_does_not_work_records_still_starved(self):
        self.coordinator.evaluate(_starved())
        later = _starved(now=NOW + int(OBSERVATION_DEADLINE_S * SECOND),
                         sequence=0, sample_age_ms=90_000.0)
        result = self.coordinator.evaluate(later)
        self.assertEqual(result["action"], "PROCESS_RESTARTED_STILL_STARVED")
        self.assertIn("PROCESS_RESTARTED_STILL_STARVED", self._events())

    def test_a_new_pid_alone_is_not_treated_as_recovery(self):
        """The restart produced a new process that still delivers nothing."""
        self.coordinator.evaluate(_starved())
        fresh = _starved(now=NOW + int(OBSERVATION_DEADLINE_S * SECOND),
                         sequence=0, sample_age_ms=90_000.0,
                         capture_process=_identity(pid=9999, ticks=99999))
        result = self.coordinator.evaluate(fresh)
        self.assertEqual(result["action"], "AUTHORIZATION_INVALIDATED")
        self.assertNotIn("SAMPLE_FLOW_RESTORED", self._events())

    def test_success_is_observable_only_if_the_policy_still_runs_when_healthy(self):
        """The bug the live ARMED test found.

        Stepping the policy only while the socket is silent means a restart that
        works stops the timeouts, the coordinator is never called again, and the
        one event proving recovery is never written. Success must be reachable
        from an observation taken while samples are flowing.
        """
        self.coordinator.evaluate(_starved())
        self.assertIsNotNone(self.coordinator.authorization)
        streaming = _starved(now=NOW + 8 * SECOND, sequence=82, sample_age_ms=15.0,
                             availability="SOURCE_STREAMING", flow_state="ACTIVE")
        result = self.coordinator.evaluate(streaming)
        self.assertEqual(result["action"], "SAMPLE_FLOW_RESTORED")
        self.assertIn("SAMPLE_FLOW_RESTORED", self._events())

    def test_an_expiring_authorization_is_recorded_not_dropped_silently(self):
        self.coordinator.evaluate(_starved())
        late = _starved(now=NOW + 3600 * SECOND, sequence=0, sample_age_ms=3_600_000.0)
        self.coordinator.evaluate(late)
        self.assertIn("AUTHORIZATION_EXPIRED", self._events())

    def test_suppression_reaches_the_audit(self):
        from rf_capture_recovery import RecoveryAttempt
        spent = _starved(recovery_attempts=(RecoveryAttempt(
            incident_id="starve-incident-0001", kernel_boot_id=BOOT, target_pid=1727,
            target_start_ticks=13345, attempted_monotonic_ns=NOW - 300 * SECOND),))
        result = self.coordinator.evaluate(spent)
        self.assertEqual(result["decision"], "RECOVERY_SUPPRESSED")
        self.assertIn("RECOVERY_SUPPRESSED", self._events())


class AuditIndependenceTests(unittest.TestCase):
    """The whole reason this store exists separately from the ring."""

    def test_a_pre_first_sample_failure_is_recorded_with_no_ring_anywhere(self):
        audit = CaptureRecoveryAudit()
        coordinator = RecoveryCoordinator(audit, mode=MODE_SHADOW)
        coordinator.evaluate(_starved(sequence=0))
        status = audit.status()
        self.assertTrue(status["independent_of_ring_allocation"])
        self.assertEqual(status["counts"]["WOULD_REQUEST_RESTART"], 1)

    def test_the_history_is_bounded_and_says_when_it_truncated(self):
        audit = CaptureRecoveryAudit(maxlen=4)
        for _ in range(10):
            audit.record("RECOVERY_SUPPRESSED", mode=MODE_SHADOW, reason="x")
        status = audit.status()
        self.assertEqual(status["retained"], 4)
        self.assertEqual(status["recorded_total"], 10)
        self.assertTrue(status["truncated"])

    def test_detail_values_and_keys_are_both_capped(self):
        audit = CaptureRecoveryAudit()
        record = audit.record("RECOVERY_SUPPRESSED", mode=MODE_SHADOW, reason="x",
                              detail={f"k{i}": "y" * 5000 for i in range(50)})
        self.assertLessEqual(len(record["detail"]), 8)
        for value in record["detail"].values():
            self.assertLessEqual(len(value), 240)

    def test_every_record_carries_the_fields_an_audit_needs(self):
        audit = CaptureRecoveryAudit()
        record = audit.record("RESTART_ATTEMPTED", mode=MODE_ARMED, reason="r",
                              incident_id="i1", attempt_id="auth-0001",
                              target=_identity().as_dict())
        for key in ("event", "incident_id", "attempt_id", "target", "monotonic_ns",
                    "mode", "reason"):
            self.assertIn(key, record)
        self.assertEqual(record["target"]["capture_pid"], 1727)


if __name__ == "__main__":
    unittest.main()
