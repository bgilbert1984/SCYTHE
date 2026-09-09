"""When an eligible promotion happens, and what the record says about it."""

import ast
import inspect
import itertools
import json
import unittest

import scythe_promotion_ledger as ledger_module
from scythe_invariant_ledger import (
    Coordinate, TransitionContract, check_transition, signature,
)
from scythe_promotion_ledger import (
    ACTING_EVENTS, BUDGET_EXHAUSTED, DEFAULT_MODE, EVENTS, MODE_ARMED,
    MODE_DISABLED, MODE_SHADOW, MODES, NONE, PROMOTION_ATTEMPTED,
    PROMOTION_BUDGET, PROMOTION_FAILED, PROMOTION_RECORDED, PROMOTION_REFUSED,
    PROMOTION_SUPPRESSED, SHADOW_EVENTS, WOULD_BE_REFUSED, WOULD_BE_SUPPRESSED,
    WOULD_PROMOTE, PromotionAudit, PromotionCoordinator, PromotionLedgerError,
    UnknownPromotionEvent, WriteResult,
)
from scythe_promotion_policy import (
    CapsuleIdentity, PromotionRequest, verdict_digest,
)

SECOND = 1_000_000_000
NOW = 10_000 * SECOND
# Eight preserved fields, so that distinct findings can be built the only way
# the merged policy distinguishes them: since promotion identity v2 that is
# the coordinate a field moved to. See PromotionIdentityTests.
CONTRACT = TransitionContract(name="T", must_preserve=("keep", "other"),
                              must_change=("move",), domain_fields=("boot",))
BASE = {"boot": "boot-a", "keep": "same", "other": "same", "move": 1}
CAPSULE = CapsuleIdentity(schema="scythe.invariant-capsule.v1",
                          digest="blake2s:aabbcc", within_bounds=True,
                          carries_samples=False)
REQUEST = PromotionRequest(requested_by="OPERATOR",
                           target_graph="scythe.graphops.evidence",
                           justification_source="OPERATOR")


def _verdict(move_to=1, changed_field=None, changed_to="different"):
    """A REQUIRED_CHANGE_NOT_OBSERVED by default, promotable.

    `changed_field` adds a PROHIBITED_CHANGE on that field. Distinctness comes
    from the coordinate it moved to: since promotion identity v2 the digest
    binds each finding to its before and after values, so two movements of one
    field are two promotions. PromotionIdentityTests holds that.
    """
    after = dict(BASE, move=move_to)
    if changed_field is not None:
        after[changed_field] = changed_to
    return check_transition(signature(**BASE), "T", signature(**after), CONTRACT)


def _distinct(index):
    """The index-th of a family of findings, told apart by their coordinate."""
    return _verdict(changed_field="keep", changed_to=f"100.{index}")


def _satisfied():
    return check_transition(signature(**BASE), "T",
                            signature(**dict(BASE, move=2)), CONTRACT)


def _accepting_writer(log):
    def writer(decision):
        log.append(decision.idempotency_key)
        return WriteResult(True, "record created")
    return writer


class ModeTests(unittest.TestCase):

    def setUp(self):
        self.audit = PromotionAudit()

    def test_the_default_posture_is_shadow(self):
        self.assertEqual(DEFAULT_MODE, MODE_SHADOW)
        self.assertEqual(PromotionCoordinator(self.audit).mode, MODE_SHADOW)

    def test_disabled_evaluates_nothing(self):
        coordinator = PromotionCoordinator(self.audit, mode=MODE_DISABLED)
        result = coordinator.evaluate(_verdict(), REQUEST, CAPSULE,
                                      now_monotonic_ns=NOW)
        self.assertEqual(result["outcome"], NONE)
        self.assertEqual(self.audit.status()["recorded_total"], 0)

    def test_armed_cannot_be_constructed_without_a_writer(self):
        """No adapter exists, so this is how ARMED stays unreachable."""
        with self.assertRaises(PromotionLedgerError) as caught:
            PromotionCoordinator(self.audit, mode=MODE_ARMED)
        self.assertIn("no execution adapter exists", str(caught.exception))

    def test_an_unknown_mode_is_refused(self):
        with self.assertRaises(PromotionLedgerError):
            PromotionCoordinator(self.audit, mode="ARMD")

    def test_nothing_requested_records_nothing(self):
        coordinator = PromotionCoordinator(self.audit)
        result = coordinator.evaluate(_verdict(), None, CAPSULE,
                                      now_monotonic_ns=NOW)
        self.assertEqual(result["outcome"], NONE)
        self.assertEqual(self.audit.status()["recorded_total"], 0,
                         "the absence of an event is not an event")


class ShadowFidelityTests(unittest.TestCase):
    """Shadow must reproduce idempotency, not the amplification it prevents."""

    def setUp(self):
        self.audit = PromotionAudit()
        self.coordinator = PromotionCoordinator(self.audit, mode=MODE_SHADOW)

    def test_one_finding_evaluated_repeatedly_is_one_would_be_promotion(self):
        verdict = _verdict()
        outcomes = [self.coordinator.evaluate(
            verdict, REQUEST, CAPSULE, now_monotonic_ns=NOW + tick * 5 * SECOND
        )["outcome"] for tick in range(120)]
        self.assertEqual(outcomes[0], WOULD_PROMOTE)
        self.assertNotIn(WOULD_PROMOTE, outcomes[1:])
        self.assertEqual(self.audit.status()["counts"][WOULD_PROMOTE], 1)
        self.assertEqual(set(outcomes[1:]), {WOULD_BE_REFUSED})

    def test_an_empty_simulated_ledger_reproduces_the_amplification(self):
        """The behaviour the simulation exists to prevent, pinned so the fix
        cannot silently regress."""
        naive = PromotionCoordinator(PromotionAudit(), mode=MODE_SHADOW)
        verdict = _verdict()
        judged = 0
        for tick in range(20):
            # Emptying the simulated ledger each cycle is what a shadow mode
            # without one amounts to.
            naive._shadow_promoted.clear()
            if naive.evaluate(verdict, REQUEST, CAPSULE,
                              now_monotonic_ns=NOW + tick * 5 * SECOND
                              )["outcome"] == WOULD_PROMOTE:
                judged += 1
        self.assertEqual(judged, 20)

    def test_a_simulated_ledger_never_becomes_a_real_one(self):
        for tick in range(5):
            self.coordinator.evaluate(_distinct(tick), REQUEST, CAPSULE,
                                      now_monotonic_ns=NOW + tick * SECOND)
        self.assertEqual(self.coordinator.promoted_keys, (),
                         "shadow wrote nothing real")
        self.assertEqual(len(self.coordinator.policy_keys), 5)

    def test_distinct_findings_each_get_one_would_be_promotion(self):
        outcomes = [self.coordinator.evaluate(
            _distinct(i), REQUEST, CAPSULE,
            now_monotonic_ns=NOW + i * SECOND)["outcome"] for i in range(5)]
        self.assertEqual(outcomes, [WOULD_PROMOTE] * 5)

    def test_shadow_events_are_named_apart_from_real_ones(self):
        self.assertEqual(set(SHADOW_EVENTS) & set(ACTING_EVENTS), set())
        counts = self.audit.status()["counts"]
        self.coordinator.evaluate(_verdict(), REQUEST, CAPSULE, now_monotonic_ns=NOW)
        self.assertNotIn(PROMOTION_RECORDED, self.audit.status()["counts"])

    def test_the_audit_refuses_an_acting_event_in_shadow(self):
        for event in ACTING_EVENTS:
            with self.assertRaises(UnknownPromotionEvent):
                self.audit.record(event, mode=MODE_SHADOW, reason="x")

    def test_the_audit_refuses_a_shadow_event_outside_shadow(self):
        for event in SHADOW_EVENTS:
            with self.assertRaises(UnknownPromotionEvent):
                self.audit.record(event, mode=MODE_ARMED, reason="x")


class BudgetTests(unittest.TestCase):
    """Idempotency is about one record twice; the budget is about a thousand."""

    def setUp(self):
        self.audit = PromotionAudit()
        self.coordinator = PromotionCoordinator(self.audit, mode=MODE_SHADOW,
                                                budget=3, window_s=600.0)

    def _distinct(self, count, start=NOW, step=SECOND):
        return [self.coordinator.evaluate(
            _distinct(i), REQUEST, CAPSULE,
            now_monotonic_ns=start + i * step)["outcome"] for i in range(count)]

    def test_the_budget_bounds_distinct_findings(self):
        outcomes = self._distinct(6)
        self.assertEqual(outcomes[:3], [WOULD_PROMOTE] * 3)
        self.assertEqual(set(outcomes[3:]), {WOULD_BE_SUPPRESSED})

    def test_the_budget_is_a_rolling_window(self):
        self._distinct(3)
        later = self.coordinator.evaluate(
            _distinct(7), REQUEST, CAPSULE,
            now_monotonic_ns=NOW + 700 * SECOND)
        self.assertEqual(later["outcome"], WOULD_PROMOTE)

    def test_suppression_names_the_budget_rather_than_a_policy_refusal(self):
        self._distinct(4)
        record = self.audit.status()["records"][-1]
        self.assertEqual(record["event"], WOULD_BE_SUPPRESSED)
        self.assertEqual(record["reason"], BUDGET_EXHAUSTED)

    def test_the_budget_is_declared_as_a_ledger_concern(self):
        status = self.coordinator.status()
        self.assertIn("A THOUSAND DIFFERENT FINDINGS", status["budget_note"])
        self.assertEqual(status["budget"], 3)


class ArmedTests(unittest.TestCase):
    """ARMED needs an injected writer; there is no adapter."""

    def setUp(self):
        self.audit = PromotionAudit()
        self.written = []
        self.coordinator = PromotionCoordinator(
            self.audit, mode=MODE_ARMED, writer=_accepting_writer(self.written))

    def _events(self):
        return [r["event"] for r in self.audit.status()["records"]]

    def test_an_eligible_promotion_is_attempted_then_recorded(self):
        result = self.coordinator.evaluate(_verdict(), REQUEST, CAPSULE,
                                           now_monotonic_ns=NOW)
        self.assertEqual(result["outcome"], PROMOTION_RECORDED)
        self.assertEqual(self._events(), [PROMOTION_ATTEMPTED, PROMOTION_RECORDED])
        self.assertEqual(len(self.written), 1)

    def test_the_key_is_ledgered_before_the_write(self):
        """An uncounted promotion can write again for free."""
        order = []

        def writer(decision):
            order.append(len(coordinator.promoted_keys))
            return WriteResult(True)

        coordinator = PromotionCoordinator(self.audit, mode=MODE_ARMED,
                                           writer=writer)
        coordinator.evaluate(_verdict(), REQUEST, CAPSULE, now_monotonic_ns=NOW)
        self.assertEqual(order, [1])

    def test_a_failed_write_is_recorded_and_still_consumes_the_identity(self):
        coordinator = PromotionCoordinator(
            self.audit, mode=MODE_ARMED,
            writer=lambda d: WriteResult(False, "bus unavailable"))
        result = coordinator.evaluate(_verdict(), REQUEST, CAPSULE,
                                      now_monotonic_ns=NOW)
        self.assertEqual(result["outcome"], PROMOTION_FAILED)
        self.assertEqual(len(coordinator.promoted_keys), 1)

    def test_the_same_finding_is_never_written_twice(self):
        verdict = _verdict()
        first = self.coordinator.evaluate(verdict, REQUEST, CAPSULE,
                                          now_monotonic_ns=NOW)
        second = self.coordinator.evaluate(verdict, REQUEST, CAPSULE,
                                           now_monotonic_ns=NOW + 5 * SECOND)
        self.assertEqual(first["outcome"], PROMOTION_RECORDED)
        self.assertEqual(second["outcome"], PROMOTION_REFUSED)
        self.assertIn("DUPLICATE_PROMOTION", second["refusals"])
        self.assertEqual(len(self.written), 1)

    def test_armed_reads_the_real_ledger_not_the_shadow_one(self):
        self.assertEqual(self.coordinator.policy_keys,
                         self.coordinator.promoted_keys)

    def test_acceptance_is_a_record_created_not_a_finding_confirmed(self):
        result = self.coordinator.evaluate(_verdict(), REQUEST, CAPSULE,
                                           now_monotonic_ns=NOW)
        self.assertIn("NOT A FINDING", result["accepted_note"])


class RefusalPassthroughTests(unittest.TestCase):
    def test_a_policy_refusal_reaches_the_audit_under_the_mode_s_name(self):
        for mode, expected in ((MODE_SHADOW, WOULD_BE_REFUSED),):
            with self.subTest(mode=mode):
                audit = PromotionAudit()
                coordinator = PromotionCoordinator(audit, mode=mode)
                result = coordinator.evaluate(_satisfied(), REQUEST, CAPSULE,
                                              now_monotonic_ns=NOW)
                self.assertEqual(result["outcome"], expected)
                self.assertIn("VERDICT_NOT_PROMOTABLE", result["refusals"])

    def test_a_satisfied_verdict_never_reaches_a_writer(self):
        written = []
        audit = PromotionAudit()
        coordinator = PromotionCoordinator(audit, mode=MODE_ARMED,
                                           writer=_accepting_writer(written))
        result = coordinator.evaluate(_satisfied(), REQUEST, CAPSULE,
                                      now_monotonic_ns=NOW)
        self.assertEqual(result["outcome"], PROMOTION_REFUSED)
        self.assertEqual(written, [])


class AuditTests(unittest.TestCase):
    def test_the_history_is_bounded_and_says_when_it_truncated(self):
        audit = PromotionAudit(maxlen=4)
        for _ in range(10):
            audit.record(WOULD_BE_REFUSED, mode=MODE_SHADOW, reason="x")
        status = audit.status()
        self.assertEqual(status["retained"], 4)
        self.assertEqual(status["recorded_total"], 10)
        self.assertTrue(status["truncated"])

    def test_an_unknown_event_is_refused(self):
        with self.assertRaises(UnknownPromotionEvent):
            PromotionAudit().record("PROBABLY_FINE", mode=MODE_SHADOW, reason="x")

    def test_detail_is_bounded(self):
        record = PromotionAudit().record(WOULD_BE_REFUSED, mode=MODE_SHADOW,
                                         reason="x", detail="y" * 5000)
        self.assertLessEqual(len(record["detail"]), 240)

    def test_shadow_decisions_are_counted_apart(self):
        audit = PromotionAudit()
        coordinator = PromotionCoordinator(audit, mode=MODE_SHADOW)
        coordinator.evaluate(_verdict(), REQUEST, CAPSULE, now_monotonic_ns=NOW)
        self.assertEqual(audit.status()["shadow_decisions"], 1)


class PromotionIdentityTests(unittest.TestCase):
    """What the merged policy can and cannot tell apart.

    The ledger's whole idempotency claim rests on promotion_identity, so what
    that identity is blind to is a ledger property, tested here rather than
    assumed.
    """

    def test_a_disagreement_on_a_different_field_is_a_different_promotion(self):
        self.assertNotEqual(verdict_digest(_verdict(changed_field="keep")),
                            verdict_digest(_verdict(changed_field="other")))

    def test_two_movements_of_one_field_are_two_promotions(self):
        """Repaired in #27. Before it, both hashed alike and the ledger --
        correctly, on the key it was handed -- refused the second as a
        duplicate, dropping a real finding without a trace.
        """
        audit = PromotionAudit()
        coordinator = PromotionCoordinator(audit, mode=MODE_SHADOW)
        first = coordinator.evaluate(
            _verdict(changed_field="keep", changed_to="100.2"),
            REQUEST, CAPSULE, now_monotonic_ns=NOW)
        second = coordinator.evaluate(
            _verdict(changed_field="keep", changed_to="100.3"),
            REQUEST, CAPSULE, now_monotonic_ns=NOW + SECOND)
        self.assertEqual(first["outcome"], WOULD_PROMOTE)
        self.assertEqual(second["outcome"], WOULD_PROMOTE)
        self.assertEqual(audit.status()["counts"][WOULD_PROMOTE], 2)
        # Two identities in the simulated ledger, not one entry written twice.
        self.assertEqual(len(coordinator.policy_keys), 2)

    def test_the_same_finding_twice_is_still_one_promotion(self):
        """The repair must not have cost idempotency, which is the other half.

        A key that told everything apart, including a re-evaluation of one
        transition, would make re-checking accumulate records.
        """
        audit = PromotionAudit()
        coordinator = PromotionCoordinator(audit, mode=MODE_SHADOW)
        first = coordinator.evaluate(_distinct(2), REQUEST, CAPSULE,
                                     now_monotonic_ns=NOW)
        second = coordinator.evaluate(_distinct(2), REQUEST, CAPSULE,
                                      now_monotonic_ns=NOW + SECOND)
        self.assertEqual(first["outcome"], WOULD_PROMOTE)
        self.assertEqual(second["outcome"], WOULD_BE_REFUSED)
        self.assertIn("DUPLICATE_PROMOTION", second["refusals"])


class ScopeTests(unittest.TestCase):
    def test_the_module_writes_nothing_of_its_own(self):
        import ast
        with open(inspect.getsourcefile(ledger_module), encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
            elif isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
        self.assertEqual(imported, {"__future__", "collections", "dataclasses",
                                    "threading", "typing",
                                    "scythe_promotion_policy",
                                    "scythe_invariant_ledger"})

    def test_it_declares_the_adapter_absent(self):
        status = PromotionCoordinator(PromotionAudit()).status()
        self.assertEqual(status["execution_adapter"], "NOT_IMPLEMENTED")
        self.assertEqual(status["writer"], "NOT_IMPLEMENTED")
        self.assertTrue(status["armed_requires_writer"])
        self.assertTrue(status["shadow_simulates_idempotency"])

    def test_evaluation_reads_no_clock_of_its_own(self):
        """Scanned by AST, not by text. The module docstring contains the
        phrase "whether now is the time." and a raw-text scan for "time."
        cannot tell an English sentence from a call to the clock."""
        parameters = inspect.signature(PromotionCoordinator.evaluate).parameters
        self.assertIn("now_monotonic_ns", parameters)
        with open(inspect.getsourcefile(ledger_module), encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        imported, called = set(), set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
            elif isinstance(node, ast.Call):
                target = node.func
                if isinstance(target, ast.Name):
                    called.add(target.id)
                elif isinstance(target, ast.Attribute):
                    called.add(target.attr)
        self.assertNotIn("time", imported, "the ledger must be handed the time")
        for clock in ("monotonic", "monotonic_ns", "time", "time_ns", "now",
                      "perf_counter", "utcnow"):
            self.assertNotIn(clock, called, clock)

    def test_status_is_serialisable(self):
        json.dumps(PromotionCoordinator(PromotionAudit()).status())
        json.dumps(PromotionAudit().status())


if __name__ == "__main__":
    unittest.main()
