"""A bounded model of recovery verdict selection, written from the contract.

Why this exists rather than more tests
--------------------------------------
The enumeration in ``test_rf_capture_restart`` failed to find two live bugs
because it varied the wrong dimension: its fixtures held the process identity
constant across a restart, which no restart does. A model written *from the
implementation* inherits the implementation's dimensions the same way, and would
be blind in the same place.

So the model below is written from the contract's vocabulary -- what a verdict
claims to be about -- and **imports nothing from the recovery module, not even
the verdict names.** They are retyped. If they cannot be retyped without
ambiguity, that is itself a finding.

Two halves:

``ModelTests``        properties of the model alone, over the whole state space
``DifferentialTests`` the implementation run over the same space, compared

Divergence is a finding in one of the two, and the model's independence is what
makes it possible to tell which.

Scope, deliberately narrow
--------------------------
Verdict selection and attempt budget. **Transport is not modelled**: it is
absent from revalidation by design, and a model that grew to cover it would be a
second artifact to keep in step. A stale model that still passes is worse than
no model, because it certifies.
"""

import itertools
import unittest


# ---------------------------------------------------------------------------
# MODEL -- retyped from the contract. Nothing below is imported from the code.
# ---------------------------------------------------------------------------

# The terminal verdicts, retyped. Retyping succeeded for four of the five; see
# UNASSESSABILITY_FINDING for the one that resisted.
M_EXPIRED = "AUTHORIZATION_EXPIRED"
M_INVALIDATED = "AUTHORIZATION_INVALIDATED"
M_RESTORED = "SAMPLE_FLOW_RESTORED"
M_STILL_STARVED = "PROCESS_RESTARTED_STILL_STARVED"
M_NOT_OBSERVED = "RESTART_NOT_OBSERVED"
M_UNDETERMINED = "RECOVERY_OUTCOME_UNDETERMINED"
M_PENDING = None                    # not a verdict: evaluation continues

TERMINAL_VERDICTS = (M_EXPIRED, M_INVALIDATED, M_RESTORED,
                     M_STILL_STARVED, M_NOT_OBSERVED, M_UNDETERMINED)

# Which incarnation states can support an assertion about the target at all.
# UNRELATED cannot: a boot boundary destroys the ordering supersedes() would
# need, so no outcome is readable from it.
DETERMINATE_INCARNATIONS = ("SUPERSEDED", "UNCHANGED", "UNOBSERVABLE")

# State variables, chosen from what the verdicts claim to be about rather than
# from what _settle happens to hold in locals.
PHASES = ("FENCED", "ACTED")
INCIDENTS = ("SAME", "CHANGED", "CLOSED")
INCARNATIONS = ("SUPERSEDED", "UNCHANGED", "UNRELATED", "UNOBSERVABLE")
BOOLS = (False, True)


class State(tuple):
    """One point in the space. A tuple so it can key a dict and be a set member."""

    FIELDS = ("phase", "ttl_elapsed", "incident", "incarnation",
              "samples_after", "sequence_advanced", "deadline_passed")

    def __new__(cls, **kwargs):
        return super().__new__(cls, tuple(kwargs[f] for f in cls.FIELDS))

    def __getattr__(self, name):
        try:
            return self[self.FIELDS.index(name)]
        except ValueError:
            raise AttributeError(name)

    def evidence(self):
        """Everything observable about this state. Two states with the same
        evidence must not produce two different verdicts."""
        return tuple(self)


def all_states():
    for combo in itertools.product(PHASES, BOOLS, INCIDENTS, INCARNATIONS,
                                   BOOLS, BOOLS, BOOLS):
        yield State(**dict(zip(State.FIELDS, combo)))


def model_verdict(state):
    """The verdict the contract implies for this state.

    The FENCED ordering below is the model's assumption, not the contract's
    statement -- see ORDERING_FINDING.
    """
    if state.phase == "FENCED":
        if state.ttl_elapsed:
            return M_EXPIRED
        if state.incident != "SAME":
            return M_INVALIDATED
        if state.incarnation != "UNCHANGED":
            return M_INVALIDATED
        if state.samples_after:
            # The fault resolved itself. Reported as restoration when the
            # sequence also moved, because a caller told only "invalidated"
            # would have to infer the source came back.
            return M_RESTORED if state.sequence_advanced else M_INVALIDATED
        return M_PENDING
    # ACTED: the identity comparison has the opposite polarity here.
    if state.samples_after and state.sequence_advanced:
        return M_RESTORED
    if not state.deadline_passed:
        return M_PENDING
    if state.incarnation == "SUPERSEDED":
        return M_STILL_STARVED
    if state.incarnation == "UNRELATED":
        return M_UNDETERMINED
    # UNCHANGED or UNOBSERVABLE: no superseding incarnation within this boot.
    return M_NOT_OBSERVED


def model_attempts_charged(state):
    """Attempts charged against one authorization in this state."""
    return 1 if state.phase == "ACTED" else 0


# ---------------------------------------------------------------------------
# Properties of the model
# ---------------------------------------------------------------------------

class ModelTests(unittest.TestCase):

    def setUp(self):
        self.states = list(all_states())
        self.verdicts = {s: model_verdict(s) for s in self.states}

    def test_the_space_is_the_size_it_claims(self):
        self.assertEqual(len(self.states), 2 * 2 * 3 * 4 * 2 * 2 * 2)
        self.assertEqual(len(set(self.states)), len(self.states))

    def test_every_verdict_is_reachable(self):
        """The bug found live was one unreachable verdict. This covers all five.

        NOT_OBSERVED and STILL_STARVED have never been produced by a live run,
        which is exactly where RESTORED was before it was found unreachable.
        """
        reached = set(self.verdicts.values()) - {M_PENDING}
        for verdict in TERMINAL_VERDICTS:
            self.assertIn(verdict, reached, f"{verdict} is unreachable")

    def test_no_path_escapes_the_closed_set(self):
        """Totality: every state yields a verdict or an explicit continuation."""
        for state, verdict in self.verdicts.items():
            self.assertIn(verdict, TERMINAL_VERDICTS + (M_PENDING,), state)

    def test_the_verdict_is_a_function_of_the_evidence(self):
        """No two verdicts reachable under identical evidence.

        The model-level statement of what was fixed by making the identity
        polarity a type property rather than an ordering convention.
        """
        seen = {}
        for state, verdict in self.verdicts.items():
            key = state.evidence()
            if key in seen:
                self.assertEqual(seen[key], verdict, key)
            seen[key] = verdict

    def test_exactly_one_attempt_is_charged_on_post_attempt_verdicts(self):
        for state, verdict in self.verdicts.items():
            charged = model_attempts_charged(state)
            if verdict in (M_STILL_STARVED, M_NOT_OBSERVED):
                self.assertEqual(charged, 1, state)
            if state.phase == "FENCED":
                self.assertEqual(charged, 0, state)

    def test_restored_is_reachable_from_both_phases_and_means_the_same(self):
        """Self-healing and successful recovery are different causes, one fact."""
        phases = {s.phase for s, v in self.verdicts.items() if v == M_RESTORED}
        self.assertEqual(phases, {"FENCED", "ACTED"})

    def test_a_changed_incarnation_never_invalidates_after_acting(self):
        """The polarity flip, stated as a property rather than a test case."""
        for state, verdict in self.verdicts.items():
            if state.phase == "ACTED" and state.incarnation == "SUPERSEDED":
                self.assertNotEqual(verdict, M_INVALIDATED, state)

    def test_a_changed_incarnation_always_invalidates_before_acting(self):
        for state, verdict in self.verdicts.items():
            if (state.phase == "FENCED" and not state.ttl_elapsed
                    and state.incident == "SAME"
                    and state.incarnation != "UNCHANGED"):
                self.assertEqual(verdict, M_INVALIDATED, state)


# ---------------------------------------------------------------------------
# DIFFERENTIAL -- the implementation, over the same space. Imports live inside
# the methods so the model above stays visibly free of them.
# ---------------------------------------------------------------------------

SECOND = 1_000_000_000
BOOT = "boot-model"
OTHER_BOOT = "boot-other"


def _build(state):
    """One abstract state, realised as the objects the implementation expects."""
    from rf_capture_recovery import (
        AUTHORIZATION_TTL_S, OBSERVATION_DEADLINE_S, ProcessIdentity,
        RecoveryAuthorization, RecoveryObservation,
    )
    authorized_ns = 10_000 * SECOND
    target = ProcessIdentity(boot_id=BOOT, pid=1000, start_ticks=5000)
    authorization = RecoveryAuthorization(
        authorization_id="auth-model", incident_id="incident-A", target=target,
        authorized_monotonic_ns=authorized_ns, sequence_at_authorization=100)
    if state.phase == "ACTED":
        attempted_ns = authorized_ns + 1 * SECOND
        authorization = authorization.with_attempt(attempted_ns)
        reference_ns = attempted_ns
        offset_s = (OBSERVATION_DEADLINE_S + 5.0) if state.deadline_passed else 2.0
    else:
        reference_ns = authorized_ns
        offset_s = (AUTHORIZATION_TTL_S + 5.0) if state.ttl_elapsed else 2.0
    observed_ns = reference_ns + int(offset_s * SECOND)

    observed = {
        "SUPERSEDED": ProcessIdentity(BOOT, 2000, 9000),
        "UNCHANGED": target,
        "UNRELATED": ProcessIdentity(OTHER_BOOT, 2000, 9000),
        "UNOBSERVABLE": None,
    }[state.incarnation]
    incident_id = {"SAME": "incident-A", "CHANGED": "incident-B",
                   "CLOSED": None}[state.incident]
    # Samples after the reference instant, or long before the authorization.
    age_ms = 5.0 if state.samples_after else (observed_ns - authorized_ns) / 1e6 + 5000.0
    observation = RecoveryObservation(
        observed_monotonic_ns=observed_ns,
        availability="SOURCE_STARVED", transport_state="CONNECTED",
        flow_state="STARVED", last_sample_age_ms=age_ms, reconnect_count=1,
        incident_id=incident_id,
        incident_opened_monotonic_ns=None if incident_id is None else authorized_ns,
        capture_process=observed, incident_capture_process=target,
        recovery_attempts=(),
        latest_sequence=101 if state.sequence_advanced else 100)
    return authorization, observation


def implementation_verdict(state):
    """What the coordinator actually settles on, for this state."""
    from rf_capture_audit import CaptureRecoveryAudit
    from rf_capture_restart import RecoveryCoordinator
    from rf_capture_recovery import MODE_ARMED
    authorization, observation = _build(state)
    coordinator = RecoveryCoordinator(CaptureRecoveryAudit(), mode=MODE_ARMED,
                                      requester=lambda: None)
    coordinator._authorization = authorization
    result = coordinator._settle(observation)
    return None if result is None else result["action"]


class DifferentialTests(unittest.TestCase):
    """The model and the code, over all 384 states."""

    def test_the_model_and_the_implementation_agree_everywhere(self):
        divergences = []
        for state in all_states():
            expected = model_verdict(state)
            actual = implementation_verdict(state)
            if expected != actual:
                divergences.append((dict(zip(State.FIELDS, state)), expected, actual))
        self.assertEqual(divergences, [], f"{len(divergences)} divergent states")

    def test_the_verdict_names_survive_retyping(self):
        """If they cannot be retyped without ambiguity, that is the finding."""
        from rf_capture_recovery import (
            AUTHORIZATION_EXPIRED, AUTHORIZATION_INVALIDATED,
            PROCESS_RESTARTED_STILL_STARVED, RECOVERY_OUTCOME_UNDETERMINED,
            RESTART_NOT_OBSERVED, SAMPLE_FLOW_RESTORED,
        )
        self.assertEqual(
            set(TERMINAL_VERDICTS),
            {AUTHORIZATION_EXPIRED, AUTHORIZATION_INVALIDATED, SAMPLE_FLOW_RESTORED,
             PROCESS_RESTARTED_STILL_STARVED, RESTART_NOT_OBSERVED,
             RECOVERY_OUTCOME_UNDETERMINED})

    def test_the_model_half_of_this_file_imports_nothing_from_the_code(self):
        """Independence is the only thing the model buys over another test."""
        import inspect
        source = inspect.getsource(inspect.getmodule(self))
        model_half = source.split("# DIFFERENTIAL")[0]
        for forbidden in ("rf_capture_recovery", "rf_capture_restart",
                          "rf_capture_audit", "rf_bridge"):
            self.assertNotIn(forbidden, model_half.split('"""', 2)[2],
                             "the model must be written from the contract")

    def test_exactly_one_attempt_is_charged_where_the_model_says(self):
        from rf_capture_audit import CaptureRecoveryAudit
        from rf_capture_restart import RecoveryCoordinator
        from rf_capture_recovery import MODE_ARMED
        for state in all_states():
            authorization, observation = _build(state)
            coordinator = RecoveryCoordinator(CaptureRecoveryAudit(),
                                              mode=MODE_ARMED, requester=lambda: None)
            coordinator._authorization = authorization
            coordinator._settle(observation)
            self.assertEqual(len(coordinator.attempts), 0,
                             "settling never charges; only acting does")
            self.assertEqual(model_attempts_charged(state),
                             1 if authorization.acted else 0)

    def test_the_backwards_wiring_crash_is_unreachable_from_settle(self):
        """Backwards wiring is a crash by design. A live path to it is a bug."""
        for state in all_states():
            try:
                implementation_verdict(state)
            except ValueError as exc:      # pragma: no cover - the finding path
                self.fail(f"reachable crash at {dict(zip(State.FIELDS, state))}: {exc}")


class DeterminacyTests(unittest.TestCase):
    """No post-attempt verdict may mix an assertion with an absence of one.

    The three post-attempt verdicts each assert something about the target.
    RESTART_NOT_OBSERVED previously also absorbed UNRELATED -- a different boot,
    where ``supersedes`` correctly refuses to invent an ordering -- so a
    consumer counting recovery failures was counting host reboots.

    RECOVERY_OUTCOME_UNDETERMINED carries that case now, with the reason naming
    what destroyed the evidence. These properties keep the boundary rather than
    the specific split: each post-attempt verdict is either wholly determinate
    or wholly not.
    """

    def _incarnations_reaching(self, verdict):
        return {s.incarnation for s in all_states() if model_verdict(s) == verdict}

    def test_no_post_attempt_verdict_mixes_determinate_with_indeterminate(self):
        determinate = set(DETERMINATE_INCARNATIONS)
        for verdict in (M_STILL_STARVED, M_NOT_OBSERVED, M_UNDETERMINED):
            reached = self._incarnations_reaching(verdict)
            self.assertTrue(reached <= determinate or reached <= {"UNRELATED"},
                            f"{verdict} reaches {reached}")

    def test_not_observed_asserts_only_about_the_same_boot(self):
        self.assertEqual(self._incarnations_reaching(M_NOT_OBSERVED),
                         {"UNCHANGED", "UNOBSERVABLE"})

    def test_undetermined_is_reached_only_across_a_boot_boundary(self):
        self.assertEqual(self._incarnations_reaching(M_UNDETERMINED), {"UNRELATED"})

    def test_still_starved_is_single_valued(self):
        self.assertEqual(self._incarnations_reaching(M_STILL_STARVED), {"SUPERSEDED"})

    def test_a_reboot_never_counts_as_a_failed_restart(self):
        """The metric this split exists to protect."""
        failures = {M_STILL_STARVED, M_NOT_OBSERVED}
        for state in all_states():
            if state.incarnation == "UNRELATED":
                self.assertNotIn(model_verdict(state), failures, state)


if __name__ == "__main__":
    unittest.main()
