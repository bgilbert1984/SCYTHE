"""Phase 1: the policy explains a restart. It must never perform one."""

import os
import tempfile
import unittest

from rf_capture_recovery import (
    NO_ACTION, RECOVERY_ATTEMPT_LIMIT, RECOVERY_COOLDOWN_S, RECOVERY_SUPPRESSED,
    REQUEST_RESTART, SUSTAINED_STARVATION_S, ProcessIdentity, RecoveryObservation,
    capture_process_identity, decide, kernel_boot_id, observe, policy_status,
    process_start_ticks,
)


BOOT = "0f4d1c9e-2a77-4a1e-9d3b-6c5f0a2b8e11"
OTHER_BOOT = "aaaaaaaa-2a77-4a1e-9d3b-6c5f0a2b8e11"
SECOND = 1_000_000_000


def _identity(pid=168025, ticks=98765, boot=BOOT):
    return ProcessIdentity(boot_id=boot, pid=pid, start_ticks=ticks)


def _starved(*, seconds=60.0, attempts=0, last_attempt_s=None,
             process=None, incident_process=None, **overrides):
    """A source that has been starved for `seconds`, with everything else clean."""
    now = 10_000 * SECOND
    # The incident's identity defaults to the canonical process, not to whatever
    # `process` was overridden to. Defaulting it to `process` would silently make
    # the two always agree and quietly disarm every changed-process test.
    process = _identity() if process is None else process
    incident_process = _identity() if incident_process is None else incident_process
    fields = dict(
        observed_monotonic_ns=now,
        availability="SOURCE_STARVED",
        transport_state="CONNECTED",
        flow_state="STARVED",
        last_sample_age_ms=seconds * 1000.0,
        reconnect_count=12,
        incident_id="starve-incident-0001",
        incident_opened_monotonic_ns=int(now - seconds * SECOND),
        capture_process=process,
        incident_capture_process=incident_process,
        recovery_attempt_count=attempts,
        last_recovery_attempt_monotonic_ns=(
            None if last_attempt_s is None else int(now - last_attempt_s * SECOND)),
    )
    fields.update(overrides)
    return RecoveryObservation(**fields)


class DecisionBoundaryTests(unittest.TestCase):
    """Exactly the five conditions, and the reason when one of them fails."""

    def test_sustained_starvation_on_an_unchanged_process_requests_a_restart(self):
        decision = decide(_starved(seconds=60.0))
        self.assertEqual(decision.decision, REQUEST_RESTART)
        self.assertEqual(decision.reason, "SUSTAINED_STARVATION_SAME_PROCESS")

    def test_a_healthy_source_is_no_action_and_says_which_no_action(self):
        decision = decide(_starved(availability="SOURCE_STREAMING", flow_state="ACTIVE"))
        self.assertEqual(decision.decision, NO_ACTION)
        self.assertEqual(decision.reason, "NOT_STARVED")

    def test_a_reconnecting_bridge_is_not_the_policy_s_business(self):
        decision = decide(_starved(transport_state="CONNECTING",
                                   availability="SOURCE_CONNECTING"))
        self.assertEqual(decision.reason, "TRANSPORT_NOT_CONNECTED")

    def test_a_brief_stall_is_reported_and_not_acted_on(self):
        brief = decide(_starved(seconds=SUSTAINED_STARVATION_S - 0.1))
        self.assertEqual(brief.decision, NO_ACTION)
        self.assertEqual(brief.reason, "STARVATION_TOO_BRIEF")
        self.assertEqual(decide(_starved(seconds=SUSTAINED_STARVATION_S)).decision,
                         REQUEST_RESTART, "the boundary is inclusive")

    def test_an_already_restarted_process_is_not_restarted_again(self):
        """Something else fixed the process and it is still starved."""
        decision = decide(_starved(process=_identity(pid=1727, ticks=4242)))
        self.assertEqual(decision.decision, NO_ACTION)
        self.assertEqual(decision.reason, "CAPTURE_PROCESS_CHANGED")

    def test_a_cooling_request_is_given_time_to_fail(self):
        decision = decide(_starved(last_attempt_s=RECOVERY_COOLDOWN_S - 1))
        self.assertEqual(decision.reason, "COOLDOWN_ACTIVE")
        self.assertEqual(decide(_starved(last_attempt_s=RECOVERY_COOLDOWN_S)).decision,
                         REQUEST_RESTART)

    def test_the_attempt_limit_suppresses_rather_than_repeating(self):
        decision = decide(_starved(attempts=RECOVERY_ATTEMPT_LIMIT,
                                   last_attempt_s=RECOVERY_COOLDOWN_S * 2))
        self.assertEqual(decision.decision, RECOVERY_SUPPRESSED)
        self.assertEqual(decision.reason, "ATTEMPT_LIMIT_REACHED")

    def test_suppression_cannot_leak_onto_a_healthy_source(self):
        """A spent incident must not leave a working receiver reading SUPPRESSED."""
        decision = decide(_starved(availability="SOURCE_STREAMING", flow_state="ACTIVE",
                                   attempts=99))
        self.assertEqual(decision.decision, NO_ACTION)
        self.assertEqual(decision.reason, "NOT_STARVED")

    def test_an_unidentifiable_process_blocks_rather_than_assumes(self):
        for observation in (_starved(capture_process=None),
                            _starved(incident_capture_process=None)):
            decision = decide(observation)
            self.assertEqual(decision.decision, NO_ACTION)
            self.assertEqual(decision.reason, "CAPTURE_PROCESS_IDENTITY_UNAVAILABLE")

    def test_starvation_without_an_open_incident_has_nothing_to_act_on(self):
        decision = decide(_starved(incident_id=None))
        self.assertEqual(decision.reason, "NO_OPEN_INCIDENT")

    def test_the_first_unmet_condition_is_the_one_reported(self):
        """A changed process and a spent limit together must name the process.

        The attempt limit is a rate limiter; sending an operator there for a
        problem that is not one wastes the only signal they were given.
        """
        decision = decide(_starved(process=_identity(pid=1727),
                                   attempts=RECOVERY_ATTEMPT_LIMIT))
        self.assertEqual(decision.reason, "CAPTURE_PROCESS_CHANGED")


class ProcessIdentityTests(unittest.TestCase):
    """PID reuse must not be able to satisfy 'unchanged'."""

    def test_the_same_pid_at_a_different_start_time_is_a_different_process(self):
        self.assertFalse(_identity(ticks=1).same_process_as(_identity(ticks=2)))

    def test_the_same_pid_and_start_ticks_in_a_different_boot_do_not_match(self):
        """starttime counts from each boot's own zero, so the pair can repeat."""
        self.assertFalse(_identity(boot=BOOT).same_process_as(_identity(boot=OTHER_BOOT)))

    def test_all_three_matching_is_the_same_process(self):
        self.assertTrue(_identity().same_process_as(_identity()))

    def test_nothing_matches_an_absent_identity(self):
        self.assertFalse(_identity().same_process_as(None))

    def test_start_ticks_are_published_in_native_units_with_their_source(self):
        payload = _identity().as_dict()
        self.assertEqual(payload["capture_process_start_ticks"], 98765)
        self.assertEqual(payload["start_ticks_authority"],
                         "KERNEL_PROC_STAT_FIELD_22_NATIVE_TICKS")
        self.assertNotIn("start_ns", payload)
        for key in payload:
            self.assertNotIn("_ns", key, "a converted value must not look kernel-attested")


class ProcParsingTests(unittest.TestCase):
    """Field 22, read correctly even when the process name fights back."""

    def _stat(self, comm: str, start_ticks: int = 4242) -> str:
        """A stat line whose field 22 really is field 22.

        Fields 3..21 are 19 values, so starttime sits at index 19 of what
        follows the comm. Getting this wrong in the fixture would let a wrong
        parser pass, which is the one thing this fixture must not do.
        """
        root = tempfile.mkdtemp()
        os.makedirs(f"{root}/99", exist_ok=True)
        before = [str(n) for n in range(3, 22)]        # fields 3..21
        self.assertEqual(len(before), 19)
        fields = before + [str(start_ticks)] + ["0", "0", "0"]
        with open(f"{root}/99/stat", "w", encoding="utf-8") as handle:
            handle.write(f"99 ({comm}) " + " ".join(fields) + "\n")
        return root

    def test_a_plain_command_name_parses(self):
        self.assertEqual(process_start_ticks(99, proc_root=self._stat("rtl_tcp")), 4242)

    def test_a_command_name_containing_spaces_and_parens_still_parses(self):
        """Splitting on whitespace would silently return a different field."""
        root = self._stat("rtl tcp (x) y")
        self.assertEqual(process_start_ticks(99, proc_root=root), 4242)

    def test_an_absent_process_is_none_not_zero(self):
        self.assertIsNone(process_start_ticks(999_999, proc_root=self._stat("x")))

    def test_a_partial_identity_is_refused_whole(self):
        """Two of three fields cannot answer 'is this the same process'."""
        root = self._stat("rtl_tcp")
        self.assertIsNone(capture_process_identity(
            999_999, proc_root=root, boot_id_path="/proc/sys/kernel/random/boot_id"))
        self.assertIsNone(capture_process_identity(
            99, proc_root=root, boot_id_path=f"{root}/no-such-boot-id"))

    def test_this_host_reports_a_real_boot_id(self):
        boot = kernel_boot_id()
        self.assertIsNotNone(boot)
        self.assertEqual(len(boot), 36, "a boot id is a formatted uuid")


class PurityAndScopeTests(unittest.TestCase):
    """Phase 1 explains. It does not act, diagnose, or declare things restored."""

    def test_decide_reads_no_clock(self):
        """Same observation, same decision, forever."""
        observation = _starved(seconds=60.0)
        first = decide(observation)
        second = decide(observation)
        self.assertEqual(first.as_dict(), second.as_dict())

    def test_no_decision_claims_a_cause(self):
        for observation in (_starved(), _starved(seconds=1.0),
                            _starved(attempts=RECOVERY_ATTEMPT_LIMIT)):
            payload = decide(observation).as_dict()
            self.assertEqual(payload["cause"], "NOT_DETERMINABLE_FROM_THIS_PROCESS")
            blob = repr(payload).upper()
            for forbidden in ("USB", "UNPLUG", "WEDGE", "DEVICE REMOVED"):
                self.assertNotIn(forbidden, blob)

    def test_every_decision_states_what_would_count_as_restored(self):
        payload = decide(_starved()).as_dict()
        self.assertIn("DECODED SAMPLES ONLY", payload["restoration_definition"])
        self.assertIn("RTL0", payload["restoration_definition"])

    def test_the_module_declares_that_it_does_not_execute(self):
        for payload in (policy_status(), decide(_starved()).as_dict().get("policy")):
            self.assertIs(payload["executes_restart"], False)
            self.assertEqual(payload["execution_owner"],
                             "CAPTURE_SERVICE_LAYER_NOT_THIS_MODULE")

    def test_the_module_imports_nothing_that_could_restart_a_process(self):
        import rf_capture_recovery as module
        with open(module.__file__, encoding="utf-8") as handle:
            source = handle.read()
        for forbidden in ("subprocess", "systemctl", "os.kill", "signal.",
                          "busctl", "Popen", "dbus"):
            self.assertNotIn(forbidden, source,
                             f"phase 1 must not be able to act: {forbidden}")

    def test_the_attempt_window_does_not_claim_to_be_a_wall_clock_limit(self):
        """The declared observation set carries a count, not attempt timestamps."""
        self.assertEqual(policy_status()["attempt_window_authority"],
                         "PER_INCIDENT_COUNT_NOT_WALL_CLOCK_WINDOW")

    def test_the_audit_gap_is_declared_rather_than_left_to_be_discovered(self):
        note = policy_status()["audit_note"]
        self.assertIn("CANNOT BE THE SAME MECHANISM", note)
        self.assertIn("NOT IMPLEMENTED IN THIS PHASE", note)


class CollectionTests(unittest.TestCase):
    """Collection assembles; it does not decide."""

    def _source(self, **overrides):
        source = {
            "availability": "SOURCE_STARVED",
            "transport_state": "CONNECTED",
            "sample_flow_state": "STARVED",
            "last_sample_age_ms": 45604.8,
            "reconnect_count": 12,
            "starvation_incident": {"incident_id": "starve-incident-0001",
                                    "opened_monotonic_ns": 9_000 * SECOND},
        }
        source.update(overrides)
        return source

    def test_an_observation_is_assembled_from_the_bridge_block(self):
        observation = observe(self._source(), capture_pid=None, reconnect_count=12,
                              observed_monotonic_ns=10_000 * SECOND)
        self.assertEqual(observation.availability, "SOURCE_STARVED")
        self.assertEqual(observation.incident_id, "starve-incident-0001")
        self.assertEqual(observation.reconnect_count, 12)
        self.assertAlmostEqual(observation.starvation_duration_s(), 1000.0)

    def test_a_missing_incident_yields_no_incident_rather_than_a_zero(self):
        observation = observe(self._source(starvation_incident=None),
                              observed_monotonic_ns=10_000 * SECOND)
        self.assertIsNone(observation.incident_id)
        self.assertIsNone(observation.starvation_duration_s())
        self.assertEqual(decide(observation).reason, "NO_OPEN_INCIDENT")

    def test_an_absent_field_becomes_undeclared_not_a_default_state(self):
        observation = observe({}, observed_monotonic_ns=1)
        self.assertEqual(observation.availability, "UNDECLARED")
        self.assertEqual(observation.transport_state, "UNDECLARED")
        self.assertEqual(decide(observation).reason, "TRANSPORT_NOT_CONNECTED")


if __name__ == "__main__":
    unittest.main()
