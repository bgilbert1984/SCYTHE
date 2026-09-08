"""The invariant ledger, and its differential against recovery.

Recovery is the pilot because it already has everything the ledger needs: a
closed state vocabulary, a generation boundary, an exact process identity, a
declared action, post-action evidence, an independent 384-state model, and two
real failures that escaped conventional tests.

The differential is not a proof that the ledger is right. It establishes that
two independently written derivations of the same question agree, and shows
exactly where their granularity differs.
"""

import inspect
import itertools
import json
import unittest

import scythe_invariant_ledger as ledger_module
from scythe_invariant_ledger import (
    ABSENT, CHANGED, COMPARISON_DOMAIN_CHANGED, EVIDENCE_MISSING,
    INDETERMINATE, INVARIANTS_SATISFIED, NOT_ASSESSED, NUMERIC_BALANCE_EXCEEDED,
    PROHIBITED_CHANGE, REQUIRED_CHANGE_NOT_OBSERVED, UNCHANGED, UNSUPPORTED,
    UNVERIFIED, VALUE, VERDICT_PRECEDENCE, BoundedBalance, Coordinate,
    LedgerContractError, TransitionContract, check_transition, ledger_status,
    signature,
)

RESTART = TransitionContract(
    name="RESTART_CAPTURE",
    must_preserve=("incident_id", "capture_unit", "iq_endpoint"),
    must_change=("capture_pid", "capture_process_start_ticks"),
    domain_fields=("kernel_boot_id",),
)


def _sig(boot="boot-a", pid=1000, ticks=5000, incident="incident-A",
         unit="scythe-rtl-tcp.service", endpoint="127.0.0.1:1234", **extra):
    return signature(kernel_boot_id=boot, capture_pid=pid,
                     capture_process_start_ticks=ticks, incident_id=incident,
                     capture_unit=unit, iq_endpoint=endpoint, **extra)


class CoordinateTests(unittest.TestCase):
    """Absence has kinds, and the kinds do not collapse."""

    def test_nobody_looked_is_never_unchanged(self):
        """The distinction the whole ledger rests on."""
        for other in (Coordinate.of(1), Coordinate(ABSENT), Coordinate(UNVERIFIED),
                      Coordinate(NOT_ASSESSED), Coordinate(UNSUPPORTED)):
            self.assertEqual(Coordinate(NOT_ASSESSED).compare(other), INDETERMINATE)
            self.assertEqual(other.compare(Coordinate(NOT_ASSESSED)), INDETERMINATE)

    def test_absent_is_a_finding_not_a_missing_value(self):
        """Looked and found nothing is comparable; nobody looked is not."""
        self.assertEqual(Coordinate(ABSENT).compare(Coordinate(ABSENT)), UNCHANGED)
        self.assertEqual(Coordinate(ABSENT).compare(Coordinate.of(1)), CHANGED)

    def test_the_kinds_are_distinguishable_from_each_other(self):
        kinds = [Coordinate(k) for k in (ABSENT, UNVERIFIED, UNSUPPORTED)]
        for first, second in itertools.combinations(kinds, 2):
            self.assertEqual(first.compare(second), CHANGED)

    def test_values_compare_by_value(self):
        self.assertEqual(Coordinate.of("x").compare(Coordinate.of("x")), UNCHANGED)
        self.assertEqual(Coordinate.of("x").compare(Coordinate.of("y")), CHANGED)

    def test_a_non_value_kind_cannot_carry_a_value(self):
        with self.assertRaises(LedgerContractError):
            Coordinate(ABSENT, "but here it is")

    def test_an_unknown_kind_is_refused(self):
        with self.assertRaises(LedgerContractError):
            Coordinate("PROBABLY_FINE")


class ContractTests(unittest.TestCase):
    def test_a_coordinate_cannot_have_two_rules(self):
        with self.assertRaises(LedgerContractError) as caught:
            TransitionContract(name="T", must_preserve=("x",), must_change=("x",))
        self.assertIn("two rules", str(caught.exception))

    def test_a_balance_without_an_accounting_basis_cannot_be_built(self):
        """Its residual would absorb effects nobody named."""
        with self.assertRaises(LedgerContractError) as caught:
            BoundedBalance(name="power", total_field="t", component_fields=("a",),
                           tolerance=0.01, accounting_basis=())
        self.assertIn("accounting basis", str(caught.exception))

    def test_a_contract_cannot_judge_another_operation(self):
        with self.assertRaises(LedgerContractError):
            check_transition(_sig(), "RETUNE", _sig(), RESTART)


class VerdictTests(unittest.TestCase):

    def test_a_clean_restart_satisfies(self):
        verdict = check_transition(_sig(), "RESTART_CAPTURE",
                                   _sig(pid=2000, ticks=9000), RESTART)
        self.assertTrue(verdict.satisfied)
        self.assertEqual(verdict.findings, ())

    def test_failing_to_change_is_evidence(self):
        verdict = check_transition(_sig(), "RESTART_CAPTURE", _sig(), RESTART)
        self.assertEqual(verdict.verdict, REQUIRED_CHANGE_NOT_OBSERVED)
        self.assertEqual({f.field for f in verdict.findings},
                         {"capture_pid", "capture_process_start_ticks"})

    def test_a_preserved_coordinate_that_moved_is_prohibited(self):
        verdict = check_transition(_sig(), "RESTART_CAPTURE",
                                   _sig(pid=2000, ticks=9000, incident="incident-B"),
                                   RESTART)
        self.assertEqual(verdict.verdict, PROHIBITED_CHANGE)
        self.assertEqual(verdict.findings[0].field, "incident_id")

    def test_an_undeclared_coordinate_that_moved_is_prohibited(self):
        """A transition must enumerate what it may move."""
        before = _sig(antenna_id="telescopic")
        after = _sig(pid=2000, ticks=9000, antenna_id="uhf")
        verdict = check_transition(before, "RESTART_CAPTURE", after, RESTART)
        self.assertEqual(verdict.verdict, PROHIBITED_CHANGE)
        self.assertEqual([f.field for f in verdict.findings], ["antenna_id"])
        self.assertIn("enumerate", verdict.findings[0].detail)

    def test_a_domain_change_suppresses_everything_else(self):
        """Nothing else was comparable across it."""
        before = _sig()
        after = _sig(boot="boot-b", pid=2000, ticks=9, incident="incident-B")
        verdict = check_transition(before, "RESTART_CAPTURE", after, RESTART)
        self.assertEqual(verdict.verdict, COMPARISON_DOMAIN_CHANGED)
        self.assertEqual([f.field for f in verdict.findings], ["kernel_boot_id"])
        self.assertNotIn(PROHIBITED_CHANGE, [f.verdict for f in verdict.findings])

    def test_an_unassessed_coordinate_does_not_satisfy_by_default(self):
        after = _sig(pid=2000, ticks=9000)
        after["incident_id"] = Coordinate(NOT_ASSESSED)
        verdict = check_transition(_sig(), "RESTART_CAPTURE", after, RESTART)
        self.assertEqual(verdict.verdict, EVIDENCE_MISSING)
        self.assertEqual(verdict.findings[0].field, "incident_id")

    def test_findings_accumulate_and_the_verdict_is_the_most_severe(self):
        after = _sig(incident="incident-B")          # prohibited + not-changed
        verdict = check_transition(_sig(), "RESTART_CAPTURE", after, RESTART)
        self.assertEqual(verdict.verdict, PROHIBITED_CHANGE)
        self.assertEqual({f.verdict for f in verdict.findings},
                         {PROHIBITED_CHANGE, REQUIRED_CHANGE_NOT_OBSERVED})

    def test_the_precedence_is_total_and_ordered(self):
        self.assertEqual(len(set(VERDICT_PRECEDENCE)), len(VERDICT_PRECEDENCE))
        self.assertEqual(VERDICT_PRECEDENCE[0], COMPARISON_DOMAIN_CHANGED)
        self.assertEqual(VERDICT_PRECEDENCE[-1], INVARIANTS_SATISFIED)


class BoundedBalanceTests(unittest.TestCase):
    """Bounded, never exact."""

    BALANCE = BoundedBalance(
        name="window_power", total_field="p_window",
        component_fields=("p_supports", "p_residual", "p_excluded"),
        tolerance=0.006,
        accounting_basis=("WINDOW_COHERENT_GAIN", "EQUIVALENT_NOISE_BANDWIDTH",
                          "DISCARDED_FIR_TRANSIENTS", "DC_EXCLUSION"))
    CONTRACT = TransitionContract(name="DECOMPOSE_WINDOW",
                                  may_change=("p_window", "p_supports",
                                              "p_residual", "p_excluded"),
                                  balances=(BALANCE,))

    def _check(self, **after):
        return check_transition({}, "DECOMPOSE_WINDOW", signature(**after),
                                self.CONTRACT)

    def test_a_balance_within_tolerance_satisfies(self):
        verdict = self._check(p_window=1.0, p_supports=0.137, p_residual=0.705,
                              p_excluded=0.16)
        self.assertTrue(verdict.satisfied)

    def test_a_balance_outside_tolerance_is_reported_with_its_basis(self):
        verdict = self._check(p_window=1.0, p_supports=0.5, p_residual=0.705,
                              p_excluded=0.16)
        self.assertEqual(verdict.verdict, NUMERIC_BALANCE_EXCEEDED)
        self.assertIn("WINDOW_COHERENT_GAIN", verdict.findings[0].detail)

    def test_an_unassessed_component_is_missing_evidence_not_zero(self):
        verdict = self._check(p_window=1.0, p_supports=0.137, p_residual=0.705)
        self.assertEqual(verdict.verdict, EVIDENCE_MISSING)
        self.assertEqual(verdict.findings[0].field, "p_excluded")


class RecoveryDifferentialTests(unittest.TestCase):
    """Two independent derivations of the same question, compared.

    The ledger knows nothing about recovery: it is handed coordinates and a
    contract. Recovery knows nothing about the ledger. Where they agree, two
    derivations agree; where they differ, the difference is granularity and is
    stated rather than reconciled by fiat.
    """

    def _recovery_outcome(self, incarnation):
        from rf_capture_recovery import (
            OBSERVATION_DEADLINE_S, ProcessIdentity, RecoveryAuthorization,
            RecoveryObservation, evaluate_attempt,
        )
        SECOND = 1_000_000_000
        target = ProcessIdentity("boot-a", 1000, 5000)
        authorized = 10_000 * SECOND
        authorization = RecoveryAuthorization(
            "auth-1", "incident-A", target, authorized, 100).with_attempt(authorized)
        observed = {
            "SUPERSEDED": ProcessIdentity("boot-a", 2000, 9000),
            "UNCHANGED": target,
            "UNRELATED": ProcessIdentity("boot-b", 2000, 9000),
            "UNOBSERVABLE": None,
        }[incarnation]
        observation = RecoveryObservation(
            observed_monotonic_ns=authorized + int((OBSERVATION_DEADLINE_S + 5) * SECOND),
            availability="SOURCE_STARVED", transport_state="CONNECTED",
            flow_state="STARVED", last_sample_age_ms=90_000.0, reconnect_count=1,
            incident_id="incident-A", incident_opened_monotonic_ns=authorized,
            capture_process=observed, incident_capture_process=target,
            recovery_attempts=(), latest_sequence=100)
        return evaluate_attempt(authorization, observation).outcome

    def _ledger_verdict(self, incarnation):
        after = {
            "SUPERSEDED": _sig(pid=2000, ticks=9000),
            "UNCHANGED": _sig(),
            "UNRELATED": _sig(boot="boot-b", pid=2000, ticks=9000),
            "UNOBSERVABLE": _sig(pid=Coordinate(ABSENT), ticks=Coordinate(ABSENT)),
        }[incarnation]
        if incarnation == "UNOBSERVABLE":
            after = _sig()
            after["capture_pid"] = Coordinate(ABSENT)
            after["capture_process_start_ticks"] = Coordinate(ABSENT)
        return check_transition(_sig(), "RESTART_CAPTURE", after, RESTART).verdict

    CORRESPONDENCE = {
        "SUPERSEDED": ("PROCESS_RESTARTED_STILL_STARVED", INVARIANTS_SATISFIED),
        "UNCHANGED": ("RESTART_NOT_OBSERVED", REQUIRED_CHANGE_NOT_OBSERVED),
        "UNRELATED": ("RECOVERY_OUTCOME_UNDETERMINED", COMPARISON_DOMAIN_CHANGED),
        # A vanished process: the ledger sees the required change happen (the
        # identity moved from a value to ABSENT) and is satisfied by it.
        "UNOBSERVABLE": ("RESTART_NOT_OBSERVED", INVARIANTS_SATISFIED),
    }

    def test_the_two_derivations_correspond(self):
        for incarnation, (expected_recovery, expected_ledger) in self.CORRESPONDENCE.items():
            with self.subTest(incarnation=incarnation):
                self.assertEqual(self._recovery_outcome(incarnation), expected_recovery)
                self.assertEqual(self._ledger_verdict(incarnation), expected_ledger)

    def test_the_boot_boundary_is_a_domain_change_in_both(self):
        """The finding that produced RECOVERY_OUTCOME_UNDETERMINED, independently."""
        self.assertEqual(self._recovery_outcome("UNRELATED"),
                         "RECOVERY_OUTCOME_UNDETERMINED")
        self.assertEqual(self._ledger_verdict("UNRELATED"), COMPARISON_DOMAIN_CHANGED)

    def test_where_the_granularity_differs_and_why(self):
        """RESTART_NOT_OBSERVED covers two states the ledger separates.

        Recovery deliberately keeps UNCHANGED and UNOBSERVABLE under one verdict
        with the incarnation state as detail. The ledger, knowing only
        coordinates, sees UNCHANGED as a required change that did not happen and
        a vanished process as a required change that did.

        Neither is wrong. The ledger is finer because it is asked a narrower
        question, and the difference is recorded here rather than reconciled by
        making one imitate the other.
        """
        recovery_verdicts = {self._recovery_outcome(i)
                             for i in ("UNCHANGED", "UNOBSERVABLE")}
        ledger_verdicts = {self._ledger_verdict(i)
                           for i in ("UNCHANGED", "UNOBSERVABLE")}
        self.assertEqual(recovery_verdicts, {"RESTART_NOT_OBSERVED"})
        self.assertEqual(len(ledger_verdicts), 2,
                         "the ledger separates what recovery deliberately merges")


class ScopeTests(unittest.TestCase):
    def _source(self):
        with open(inspect.getsourcefile(ledger_module), encoding="utf-8") as handle:
            return handle.read()

    def test_the_ledger_knows_nothing_about_any_domain(self):
        import ast
        tree = ast.parse(self._source())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
            elif isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
        self.assertEqual(imported, {"__future__", "dataclasses", "math", "typing"})

    def test_it_cannot_mutate_anything(self):
        """Scanned by AST, not by text: the docstring names WriteBus in order
        to say it never calls one, and a raw-text scan cannot tell a promise
        from a call."""
        import ast
        tree = ast.parse(self._source())
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                target = node.func
                if isinstance(target, ast.Name):
                    called.add(target.id)
                elif isinstance(target, ast.Attribute):
                    called.add(target.attr)
        for forbidden in ("commit", "write", "execute", "post", "send", "open",
                          "ingest", "promote", "publish"):
            self.assertNotIn(forbidden, called, forbidden)
        self.assertFalse(ledger_status()["mutates"])
        self.assertEqual(ledger_status()["graph_promotion"], "NOT_IMPLEMENTED")

    def test_the_verdict_says_it_is_derived_and_not_promoted(self):
        payload = check_transition(_sig(), "RESTART_CAPTURE",
                                   _sig(pid=2000, ticks=9000), RESTART).as_dict()
        self.assertFalse(payload["mutates"])
        self.assertIn("SEPARATE DECISION", payload["mutation_note"])
        json.dumps(payload)

    def test_evaluation_is_pure_across_repeated_calls(self):
        first = check_transition(_sig(), "RESTART_CAPTURE", _sig(), RESTART).as_dict()
        for _ in range(5):
            self.assertEqual(
                check_transition(_sig(), "RESTART_CAPTURE", _sig(), RESTART).as_dict(),
                first)


if __name__ == "__main__":
    unittest.main()
