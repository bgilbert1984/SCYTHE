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


class CaptureWorld:
    """A capture process that behaves like one: restarting mints a new incarnation.

    The fault model, not the coverage, was what let two live bugs through. The
    earlier fixtures held the identity constant across a restart, which no real
    restart does -- so a test could assert the constant-identity world and pass
    while the coordinator misjudged every real recovery. Restart here advances
    start_ticks unconditionally, and a test that wants the "restart did not
    take" case has to ask for it by name.
    """

    def __init__(self, *, pid=1727, ticks=13345, boot=BOOT):
        self.identity = ProcessIdentity(boot_id=boot, pid=pid, start_ticks=ticks)
        self.restarts = 0
        self.will_take = True          # False models a restart that never happened
        self.will_stream = True        # False models a restart that delivers nothing
        self.sequence = 0
        self.sample_age_ms = 60_000.0

    def restart(self):
        self.restarts += 1
        if self.will_take:
            # A new incarnation. start_ticks only increases within a boot.
            self.identity = ProcessIdentity(
                boot_id=self.identity.boot_id,
                pid=self.identity.pid + 90_000 + self.restarts,
                start_ticks=self.identity.start_ticks + 1_000_000 * self.restarts)
        if self.will_stream:
            self.sequence += 82
            self.sample_age_ms = 20.0
        return RestartRequestResult(RESTART_REQUEST_ACCEPTED, "job 1")

    def observe(self, *, now=NOW, **overrides):
        fields = dict(
            observed_monotonic_ns=now,
            availability="SOURCE_STREAMING" if self.sample_age_ms < 2500 else "SOURCE_STARVED",
            transport_state="CONNECTED",
            flow_state="ACTIVE" if self.sample_age_ms < 2500 else "STARVED",
            last_sample_age_ms=self.sample_age_ms,
            reconnect_count=411,
            incident_id="starve-incident-0001",
            incident_opened_monotonic_ns=int(now - 60 * SECOND),
            capture_process=self.identity, incident_capture_process=self.identity,
            recovery_attempts=(), latest_sequence=self.sequence)
        fields.update(overrides)
        return RecoveryObservation(**fields)


class CoordinatorLifecycleTests(unittest.TestCase):
    """Outcomes reach the audit, including the one that was unreachable."""

    def setUp(self):
        self.audit = CaptureRecoveryAudit()
        self.world = CaptureWorld()
        self.coordinator = RecoveryCoordinator(
            self.audit, mode=MODE_ARMED, requester=self.world.restart)

    def _events(self):
        return [r["event"] for r in self.audit.status()["records"]]

    def test_a_working_restart_records_sample_flow_restored(self):
        """The live 2026-09-07 case, which previously recorded an invalidation.

        The restart succeeds, which necessarily changes the incarnation. Before
        the fix the fence ran post-attempt, read that change as
        TARGET_IDENTITY_CHANGED, and recorded AUTHORIZATION_INVALIDATED for a
        recovery that plainly worked.
        """
        self.coordinator.evaluate(self.world.observe())
        self.assertEqual(self.world.restarts, 1)
        self.assertTrue(self.world.identity.supersedes(
            self.coordinator.attempts[0] and ProcessIdentity(BOOT, 1727, 13345)))
        result = self.coordinator.evaluate(self.world.observe(now=NOW + 8 * SECOND))
        self.assertEqual(result["action"], "SAMPLE_FLOW_RESTORED")
        self.assertEqual(result["supersession"], "SUPERSEDED")
        self.assertIn("SAMPLE_FLOW_RESTORED", self._events())

    def test_a_restart_that_takes_but_still_starves_is_named_as_such(self):
        self.world.will_stream = False
        self.coordinator.evaluate(self.world.observe())
        later = self.world.observe(now=NOW + int(OBSERVATION_DEADLINE_S * SECOND) + SECOND)
        result = self.coordinator.evaluate(later)
        self.assertEqual(result["action"], "PROCESS_RESTARTED_STILL_STARVED")
        self.assertEqual(result["supersession"], "SUPERSEDED")

    def test_a_host_reboot_is_undetermined_rather_than_a_failed_restart(self):
        """The metric the split protects: recovery failures are not reboots."""
        self.world.will_stream = False
        self.coordinator.evaluate(self.world.observe())
        rebooted = ProcessIdentity(boot_id="a-different-boot", pid=4242, start_ticks=7)
        later = self.world.observe(
            now=NOW + int(OBSERVATION_DEADLINE_S * SECOND) + SECOND,
            capture_process=rebooted)
        result = self.coordinator.evaluate(later)
        self.assertEqual(result["action"], "RECOVERY_OUTCOME_UNDETERMINED")
        self.assertEqual(result["supersession"], "UNRELATED")
        for failure in ("PROCESS_RESTARTED_STILL_STARVED", "RESTART_NOT_OBSERVED"):
            self.assertNotIn(failure, self._events())
        recorded = self.audit.status()["records"][-1]
        self.assertEqual(recorded["detail"]["reason"], "KERNEL_BOOT_CHANGED")

    def test_a_vanished_capture_process_is_still_a_same_boot_assertion(self):
        self.world.will_stream = False
        self.coordinator.evaluate(self.world.observe())
        later = self.world.observe(
            now=NOW + int(OBSERVATION_DEADLINE_S * SECOND) + SECOND,
            capture_process=None)
        result = self.coordinator.evaluate(later)
        self.assertEqual(result["action"], "RESTART_NOT_OBSERVED")
        self.assertEqual(result["supersession"], "UNOBSERVABLE")

    def test_a_restart_that_never_took_is_not_reported_as_a_restart(self):
        """PROCESS_RESTARTED_STILL_STARVED would assert something that did not happen."""
        self.world.will_take = False
        self.world.will_stream = False
        self.coordinator.evaluate(self.world.observe())
        later = self.world.observe(now=NOW + int(OBSERVATION_DEADLINE_S * SECOND) + SECOND)
        result = self.coordinator.evaluate(later)
        self.assertEqual(result["action"], "RESTART_NOT_OBSERVED")
        self.assertEqual(result["supersession"], "UNCHANGED")
        self.assertNotIn("PROCESS_RESTARTED_STILL_STARVED", self._events())

    def test_a_changed_identity_before_acting_still_invalidates(self):
        """The fence keeps its polarity on the pre-action side.

        Same comparison, opposite meaning: before acting, a changed incarnation
        means something else already replaced the target, so the authorization
        no longer describes anything and must be dropped.
        """
        from rf_capture_recovery import authorize, decide, revalidate
        auth = authorize(decide(self.world.observe()), authorization_id="a1")
        self.world.restart()                      # something else restarted it
        check = revalidate(auth, self.world.observe())
        self.assertFalse(check.valid)
        self.assertEqual(check.reason, "TARGET_IDENTITY_CHANGED")

    def test_the_fence_cannot_be_asked_of_an_acted_authorization(self):
        from rf_capture_recovery import evaluate_attempt, revalidate
        self.coordinator.evaluate(self.world.observe())
        acted = self.coordinator.authorization
        self.assertIsNotNone(acted)
        self.assertTrue(acted.acted)
        with self.assertRaises(ValueError):
            revalidate(acted, self.world.observe())

    def test_the_outcome_evaluator_cannot_be_asked_of_a_fenced_authorization(self):
        from rf_capture_recovery import evaluate_attempt
        coordinator = RecoveryCoordinator(self.audit, mode=MODE_SHADOW)
        from rf_capture_recovery import authorize, decide
        auth = authorize(decide(self.world.observe()), authorization_id="a1")
        self.assertFalse(auth.acted)
        with self.assertRaises(ValueError):
            evaluate_attempt(auth, self.world.observe())

    def test_an_expiring_authorization_is_recorded_before_it_is_acted_on(self):
        coordinator = RecoveryCoordinator(
            self.audit, mode=MODE_ARMED,
            requester=lambda: RestartRequestResult(RESTART_REQUEST_FAILED, "no bus"))
        coordinator.evaluate(self.world.observe())
        self.assertIn("RESTART_REQUEST_FAILED", self._events())

    def test_suppression_reaches_the_audit(self):
        from rf_capture_recovery import RecoveryAttempt
        spent = self.world.observe(recovery_attempts=(RecoveryAttempt(
            incident_id="starve-incident-0001", kernel_boot_id=BOOT, target_pid=1727,
            target_start_ticks=13345, attempted_monotonic_ns=NOW - 300 * SECOND),))
        result = self.coordinator.evaluate(spent)
        self.assertEqual(result["decision"], "RECOVERY_SUPPRESSED")
        self.assertIn("RECOVERY_SUPPRESSED", self._events())


class ShadowFidelityTests(unittest.TestCase):
    """Shadow must reproduce the breaker, not the amplification it prevents."""

    def setUp(self):
        self.audit = CaptureRecoveryAudit()
        self.world = CaptureWorld()
        self.coordinator = RecoveryCoordinator(self.audit, mode=MODE_SHADOW)

    def test_shadow_spends_a_simulated_budget_instead_of_amplifying(self):
        """2688 would-be restarts over four hours was the amplification, not the plan.

        The check runs every 5 s for an hour of simulated outage. Exactly one
        would-be restart is judged; the cooldown holds the next two minutes, and
        the breaker holds everything after that. Before the simulated ledger
        existed this produced 720 would-be restarts for one incident.
        """
        actions = []
        ticks = 720                                   # 5 s apart == one hour
        for tick in range(ticks):
            observation = self.world.observe(
                now=NOW + tick * 5 * SECOND,
                recovery_attempts=self.coordinator.policy_attempts)
            actions.append(self.coordinator.evaluate(observation)["action"])
        counts = self.audit.status()["counts"]
        self.assertEqual(actions[0], "WOULD_REQUEST_RESTART")
        self.assertEqual(counts["WOULD_REQUEST_RESTART"], 1,
                         "one per process identity, not one per check")
        self.assertNotIn("WOULD_REQUEST_RESTART", actions[1:])
        # The cooldown answers first, then the breaker takes over for good.
        self.assertEqual(set(actions[1:]), {"NONE", "WOULD_BE_SUPPRESSED"})
        self.assertGreater(counts["WOULD_BE_SUPPRESSED"], 0)
        self.assertEqual(actions[-1], "WOULD_BE_SUPPRESSED")

    def test_the_amplification_is_what_the_breaker_exists_to_stop(self):
        """Without the simulated ledger, every check would judge a fresh restart."""
        naive = RecoveryCoordinator(CaptureRecoveryAudit(), mode=MODE_SHADOW)
        judged = sum(1 for tick in range(40)
                     if naive.evaluate(self.world.observe(
                         now=NOW + tick * 5 * SECOND,
                         recovery_attempts=()))["action"] == "WOULD_REQUEST_RESTART")
        self.assertEqual(judged, 40, "an empty ledger reproduces the old behaviour")

    def test_a_simulated_budget_never_becomes_a_real_one(self):
        for tick in range(5):
            self.coordinator.evaluate(self.world.observe(
                now=NOW + tick * 5 * SECOND,
                recovery_attempts=self.coordinator.policy_attempts))
        self.assertEqual(self.coordinator.attempts, (),
                         "shadow consumed no real attempt")
        self.assertEqual(len(self.coordinator.policy_attempts), 1)
        # One judgement, not one per tick: the remaining four are inside the
        # cooldown and produce no audit event at all.
        self.assertEqual(self.audit.status()["shadow_decisions"], 1)
        self.assertEqual(self.audit.status()["counts"].get("RECOVERY_SUPPRESSED", 0), 0,
                         "shadow suppression is never counted as real suppression")

    def test_armed_reads_the_real_ledger_not_the_shadow_one(self):
        armed = RecoveryCoordinator(self.audit, mode=MODE_ARMED,
                                    requester=self.world.restart)
        self.assertEqual(armed.policy_attempts, armed.attempts)


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
