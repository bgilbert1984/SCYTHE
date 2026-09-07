"""Phase 1: the policy explains a restart. It must never perform one."""

import os
import tempfile
import unittest

from rf_capture_recovery import (
    ATTEMPT_WINDOW_S, AUTHORIZATION_EXPIRED, AUTHORIZATION_INVALIDATED,
    AUTHORIZATION_VALID, MAX_ATTEMPTS_PER_PROCESS, NO_ACTION,
    OBSERVATION_DEADLINE_S, PROCESS_RESTARTED_STILL_STARVED,
    RECOVERY_ATTEMPT_LIMIT, RECOVERY_COOLDOWN_S, RECOVERY_OUTCOME_PENDING,
    RECOVERY_SUPPRESSED, REQUEST_RESTART, SAMPLE_FLOW_RESTORED,
    SUSTAINED_STARVATION_S, ProcessIdentity, RecoveryAttempt, RecoveryObservation,
    authorize, capture_process_identity, decide, kernel_boot_id, observe,
    policy_status, process_start_ticks, recovery_outcome, revalidate,
    _listener_inode, _proc_hex_address,
)


BOOT = "0f4d1c9e-2a77-4a1e-9d3b-6c5f0a2b8e11"
OTHER_BOOT = "aaaaaaaa-2a77-4a1e-9d3b-6c5f0a2b8e11"
SECOND = 1_000_000_000


def _identity(pid=168025, ticks=98765, boot=BOOT):
    return ProcessIdentity(boot_id=boot, pid=pid, start_ticks=ticks)


def _attempt(at_s, *, incident="starve-incident-0001", pid=168025, ticks=98765,
             boot=BOOT, now=10_000 * SECOND):
    """An attempt made `at_s` seconds before the canonical observation instant."""
    return RecoveryAttempt(incident_id=incident, kernel_boot_id=boot, target_pid=pid,
                           target_start_ticks=ticks,
                           attempted_monotonic_ns=int(now - at_s * SECOND))


def _starved(*, seconds=60.0, attempts=(), process=None,
             incident_process=None, **overrides):
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
        recovery_attempts=tuple(attempts),
        latest_sequence=overrides.pop("latest_sequence", 0),
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
        decision = decide(_starved(attempts=(_attempt(RECOVERY_COOLDOWN_S - 1,
                                                      pid=1, ticks=1),)))
        self.assertEqual(decision.reason, "COOLDOWN_ACTIVE")
        self.assertEqual(
            decide(_starved(attempts=(_attempt(RECOVERY_COOLDOWN_S, pid=1, ticks=1),))).decision,
            REQUEST_RESTART)

    def test_one_attempt_per_process_identity_and_no_more(self):
        decision = decide(_starved(attempts=(_attempt(RECOVERY_COOLDOWN_S * 2),)))
        self.assertEqual(decision.decision, RECOVERY_SUPPRESSED)
        self.assertEqual(decision.reason, "PROCESS_ALREADY_ATTEMPTED")
        self.assertEqual(MAX_ATTEMPTS_PER_PROCESS, 1)

    def test_the_rolling_window_spans_incidents(self):
        """A new PID and a new incident must not refill the budget.

        This is the escape the per-incident count of Phase 1 left open: a
        restarted-but-still-broken rtl_tcp arrives with a fresh identity and a
        fresh incident, and a per-incident budget hands it three more attempts.
        """
        # Spaced beyond the cooldown, so the window is what refuses them and
        # not the gap since the last one.
        others = tuple(_attempt(130.0 * n, incident=f"starve-incident-{n:04d}",
                                pid=1000 + n, ticks=n)
                       for n in range(1, RECOVERY_ATTEMPT_LIMIT + 1))
        decision = decide(_starved(attempts=others))
        self.assertEqual(decision.decision, RECOVERY_SUPPRESSED)
        self.assertEqual(decision.reason, "ATTEMPT_WINDOW_EXHAUSTED")

    def test_attempts_older_than_the_window_do_not_count(self):
        stale = tuple(_attempt(ATTEMPT_WINDOW_S + 60.0 * n,
                               incident=f"old-{n}", pid=2000 + n, ticks=n)
                      for n in range(1, RECOVERY_ATTEMPT_LIMIT + 2))
        self.assertEqual(decide(_starved(attempts=stale)).decision, REQUEST_RESTART)

    def test_suppression_cannot_leak_onto_a_healthy_source(self):
        """A spent incident must not leave a working receiver reading SUPPRESSED."""
        decision = decide(_starved(availability="SOURCE_STREAMING", flow_state="ACTIVE",
                                   attempts=(_attempt(1.0),)))
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
                                   attempts=(_attempt(300.0, pid=1727, ticks=98765),)))
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


class EndpointResolverTests(unittest.TestCase):
    """Recovery authority is tied to the declared endpoint, not to a port."""

    def _net_tcp(self, rows):
        root = tempfile.mkdtemp()
        os.makedirs(f"{root}/net", exist_ok=True)
        header = ("  sl  local_address rem_address   st tx_queue rx_queue tr "
                  "tm->when retrnsmt   uid  timeout inode\n")
        with open(f"{root}/net/tcp", "w", encoding="utf-8") as handle:
            handle.write(header + "".join(rows))
        return root

    def _row(self, local_hex, port_hex, state="0A", inode=4242):
        return (f"   0: {local_hex}:{port_hex} 00000000:0000 {state} "
                f"00000000:00000000 00:00000000 00000000  1000  0 {inode} 1\n")

    def test_the_configured_loopback_address_is_matched(self):
        root = self._net_tcp([self._row("0100007F", "04D2")])
        self.assertEqual(_listener_inode("127.0.0.1", 1234, root), 4242)

    def test_a_wildcard_listener_on_the_same_port_is_not_matched(self):
        """It would serve the bridge, and it violates the loopback-only rule.

        No identity means NO_ACTION, which is the safe direction: this module
        will not name a policy-violating process as a restart target.
        """
        root = self._net_tcp([self._row("00000000", "04D2")])
        self.assertIsNone(_listener_inode("127.0.0.1", 1234, root))

    def test_another_host_holding_the_same_port_is_not_matched(self):
        root = self._net_tcp([self._row("0102A8C0", "04D2")])   # 192.168.2.1
        self.assertIsNone(_listener_inode("127.0.0.1", 1234, root))

    def test_a_connected_socket_on_the_endpoint_is_not_a_listener(self):
        root = self._net_tcp([self._row("0100007F", "04D2", state="01")])
        self.assertIsNone(_listener_inode("127.0.0.1", 1234, root))

    def test_a_different_port_on_the_right_host_is_not_matched(self):
        root = self._net_tcp([self._row("0100007F", "04D3")])
        self.assertIsNone(_listener_inode("127.0.0.1", 1234, root))

    def test_an_unparseable_host_refuses_rather_than_widening(self):
        self.assertIsNone(_proc_hex_address("not-an-ip"))
        root = self._net_tcp([self._row("0100007F", "04D2")])
        self.assertIsNone(_listener_inode("not-an-ip", 1234, root))

    def test_the_address_encoding_is_little_endian_upper_hex(self):
        self.assertEqual(_proc_hex_address("127.0.0.1"), "0100007F")
        self.assertEqual(_proc_hex_address("0.0.0.0"), "00000000")


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
                            _starved(attempts=(_attempt(300.0),))):
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

    def test_the_attempt_window_is_now_a_real_wall_clock_window(self):
        self.assertEqual(policy_status()["attempt_window_authority"],
                         "ROLLING_MONOTONIC_WINDOW_ACROSS_INCIDENTS")
        self.assertEqual(policy_status()["attempt_window_s"], ATTEMPT_WINDOW_S)

    def test_the_audit_store_is_declared_independent_of_ring_allocation(self):
        note = policy_status()["audit_note"]
        self.assertIn("INDEPENDENT OF RING ALLOCATION", note)
        self.assertIn("NO RING HAD BEEN", note)


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
                              latest_sequence=0, observed_monotonic_ns=10_000 * SECOND)
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


class AuthorizationTests(unittest.TestCase):
    """A latch that a socket flap cannot cancel, and evidence cannot outlive."""

    def _auth(self, observation=None):
        observation = observation or _starved(latest_sequence=0)
        return authorize(decide(observation), authorization_id="auth-0001")

    def test_only_a_request_restart_authorizes(self):
        self.assertIsNone(authorize(decide(_starved(seconds=1.0)),
                                    authorization_id="auth-0001"))
        self.assertIsNotNone(self._auth())

    def test_an_authorization_latches_the_incident_and_the_target(self):
        auth = self._auth()
        self.assertEqual(auth.incident_id, "starve-incident-0001")
        self.assertEqual(auth.target, _identity())
        self.assertEqual(auth.sequence_at_authorization, 0)

    def test_a_transient_connecting_snapshot_does_not_revoke_it(self):
        """The whole reason the latch exists. Socket flapping is not recovery."""
        auth = self._auth()
        flapped = _starved(transport_state="CONNECTING", availability="SOURCE_CONNECTING")
        check = revalidate(auth, flapped)
        self.assertTrue(check.valid)
        self.assertEqual(check.outcome, AUTHORIZATION_VALID)

    def test_transport_is_absent_from_revalidation_by_design(self):
        import inspect
        import rf_capture_recovery as module
        source = inspect.getsource(module.revalidate)
        self.assertNotIn("transport_state", source)

    def test_it_expires_rather_than_outliving_its_evidence(self):
        auth = self._auth()
        late = _starved(observed_monotonic_ns=auth.expires_monotonic_ns())
        check = revalidate(auth, late)
        self.assertEqual(check.outcome, AUTHORIZATION_EXPIRED)
        self.assertEqual(check.reason, "TTL_ELAPSED")

    def test_a_changed_target_invalidates_rather_than_retargets(self):
        """Acting now would kill something that was never judged."""
        auth = self._auth()
        check = revalidate(auth, _starved(process=_identity(pid=1727, ticks=1)))
        self.assertEqual(check.outcome, AUTHORIZATION_INVALIDATED)
        self.assertEqual(check.reason, "TARGET_IDENTITY_CHANGED")

    def test_a_different_or_closed_incident_invalidates(self):
        auth = self._auth()
        self.assertEqual(revalidate(auth, _starved(incident_id="starve-incident-0002")).reason,
                         "INCIDENT_CHANGED")
        self.assertEqual(revalidate(auth, _starved(incident_id=None)).reason,
                         "INCIDENT_CLOSED")

    def test_samples_arriving_after_authorization_cancel_it(self):
        """The fault fixed itself. There is nothing left to restart."""
        auth = self._auth()
        # Five seconds later, with a sample 10 ms old: it arrived after the
        # authorization instant, which is the whole test.
        healed = _starved(observed_monotonic_ns=auth.authorized_monotonic_ns + 5 * SECOND,
                          last_sample_age_ms=10.0)
        check = revalidate(auth, healed)
        self.assertEqual(check.outcome, AUTHORIZATION_INVALIDATED)
        self.assertEqual(check.reason, "SAMPLES_ARRIVED_SINCE_AUTHORIZATION")


class RecoveryOutcomeTests(unittest.TestCase):
    """Success is decoded samples plus a sequence that moved. Both."""

    def _auth(self, sequence=0):
        return authorize(decide(_starved(latest_sequence=sequence)),
                         authorization_id="auth-0001")

    def _after(self, auth, *, seconds, sequence, sample_age_ms):
        return _starved(
            observed_monotonic_ns=auth.authorized_monotonic_ns + int(seconds * SECOND),
            latest_sequence=sequence, last_sample_age_ms=sample_age_ms)

    def test_samples_and_a_sequence_advance_are_success(self):
        auth = self._auth()
        after = self._after(auth, seconds=5.0, sequence=7, sample_age_ms=20.0)
        self.assertEqual(recovery_outcome(auth, after), SAMPLE_FLOW_RESTORED)

    def test_a_sequence_above_zero_is_not_enough_on_its_own(self):
        """A late incident begins after thousands of good frames.

        'sequence > 0' would then be satisfied by history rather than by this
        capture chain producing anything now.
        """
        auth = self._auth(sequence=5000)
        stale = self._after(auth, seconds=5.0, sequence=5000, sample_age_ms=999_000.0)
        self.assertNotEqual(recovery_outcome(auth, stale), SAMPLE_FLOW_RESTORED)
        self.assertGreater(stale.latest_sequence, 0)

    def test_samples_without_a_sequence_advance_are_not_success(self):
        auth = self._auth()
        self.assertNotEqual(
            recovery_outcome(auth, self._after(auth, seconds=5.0, sequence=0,
                                               sample_age_ms=20.0)),
            SAMPLE_FLOW_RESTORED)

    def test_before_the_deadline_the_answer_is_pending_not_failed(self):
        auth = self._auth()
        waiting = self._after(auth, seconds=OBSERVATION_DEADLINE_S - 1,
                              sequence=0, sample_age_ms=30_000.0)
        self.assertEqual(recovery_outcome(auth, waiting), RECOVERY_OUTCOME_PENDING)

    def test_past_the_deadline_it_is_still_starved(self):
        auth = self._auth()
        expired = self._after(auth, seconds=OBSERVATION_DEADLINE_S,
                              sequence=0, sample_age_ms=60_000.0)
        self.assertEqual(recovery_outcome(auth, expired), PROCESS_RESTARTED_STILL_STARVED)

    def test_a_greeting_sized_byte_count_cannot_appear_in_the_verdict(self):
        import inspect
        import rf_capture_recovery as module
        source = inspect.getsource(module.recovery_outcome)
        for forbidden in ("bytes", "reconnect", "pid", "socket", "RTL0"):
            self.assertNotIn(forbidden, source.replace("# ", "").split('"""')[-1])


if __name__ == "__main__":
    unittest.main()
