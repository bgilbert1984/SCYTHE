"""When an eligible promotion happens, and what the record says about it."""

import ast
import inspect
import itertools
import json
import threading
import unittest

import scythe_promotion_ledger as ledger_module
from scythe_invariant_ledger import (
    Coordinate, TransitionContract, check_transition, signature,
)
from scythe_promotion_ledger import (
    ACTING_EVENTS, BUDGET_EXHAUSTED, COMMITTED, CREATED, DEFAULT_MODE, EVENTS,
    ADAPTER_REPORTED_UNKNOWN, FAILED, INVALID_WRITER_RESULT,
    MAX_AUDIT_RECORDS, NOT_CREATED, UNRESOLVED_CAUSES, WRITER_EXCEPTION,
    PROMOTION_OUTCOME_UNRESOLVED, RESERVED,
    RETRY_REQUIRES_OPERATOR, UNKNOWN, WRITE_OUTCOMES,
    EXECUTABILITY_NOTES, EXECUTABILITY_REFUSALS, IDENTITY_UNRESOLVED,
    MERIT_REFUSALS, NOT_YET_REACHABLE, MODE_ARMED,
    MODE_DISABLED, MODE_SHADOW, MODES, NONE, PROMOTION_ATTEMPTED,
    PROMOTION_BUDGET, PROMOTION_FAILED, PROMOTION_RECORDED, PROMOTION_REFUSED,
    PROMOTION_SUPPRESSED, SHADOW_EVENTS, WOULD_BE_REFUSED, WOULD_BE_SUPPRESSED,
    WOULD_PROMOTE, PromotionAudit, PromotionCoordinator, PromotionLedgerError,
    UnknownPromotionEvent, WriteResult,
)
from scythe_promotion_policy import (
    REFUSALS as policy_refusals,
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


def _collect(results, lock, coordinator, verdict):
    outcome = coordinator.evaluate(verdict, REQUEST, CAPSULE,
                                   now_monotonic_ns=NOW)["outcome"]
    with lock:
        results.append(outcome)


def _unknown_for(prefix, *, accept_others=False):
    """A writer that answers UNKNOWN once, then CREATED."""
    state = {"used": False}

    def writer(decision):
        if not state["used"]:
            state["used"] = True
            return WriteResult(UNKNOWN, "no answer")
        return WriteResult(CREATED if accept_others else UNKNOWN)
    return writer


def _accepting_writer(log):
    def writer(decision):
        log.append(decision.idempotency_key)
        return WriteResult(CREATED, "record created")
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
        cannot silently regress.

        The budget is raised out of the way deliberately. Before §6's split one
        list served both idempotency and the window, so clearing it reset both
        and the amplification showed unimpeded; now clearing the identity map
        leaves the window spent, and a budget of 8 would stop the run at 8 for a
        reason that has nothing to do with the simulated ledger. Two structures
        means two things to say about, which is the point of separating them.
        """
        naive = PromotionCoordinator(PromotionAudit(), mode=MODE_SHADOW,
                                     budget=100)
        verdict = _verdict()
        judged = 0
        for tick in range(20):
            # Emptying the simulated ledger each cycle is what a shadow mode
            # without one amounts to.
            naive._shadow.identities.clear()
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
        """Reserve first: a crash then loses a finding rather than duplicating
        one. A record written and never counted is free to be written again.

        The identity is RESERVED when the writer runs, not COMMITTED -- the
        fence exists before the write and the terminal state only afterwards.
        """
        seen = []

        def writer(decision):
            seen.append((coordinator.fenced_keys, coordinator.promoted_keys,
                         coordinator.unresolved_keys))
            return WriteResult(CREATED)

        coordinator = PromotionCoordinator(self.audit, mode=MODE_ARMED,
                                           writer=writer)
        coordinator.evaluate(_verdict(), REQUEST, CAPSULE, now_monotonic_ns=NOW)
        (fenced, promoted, unresolved), = seen
        self.assertEqual(len(fenced), 1)
        self.assertEqual(promoted, ())
        self.assertEqual(unresolved, fenced)
        self.assertEqual(len(coordinator.promoted_keys), 1)

    def test_a_definite_failure_is_recorded_and_still_consumes_the_identity(self):
        """NOT_CREATED: the adapter attests nothing was written. It still
        fences, on §2's operator-action rule rather than on ambiguity."""
        coordinator = PromotionCoordinator(
            self.audit, mode=MODE_ARMED,
            writer=lambda d: WriteResult(NOT_CREATED, "rejected by schema"))
        result = coordinator.evaluate(_verdict(), REQUEST, CAPSULE,
                                      now_monotonic_ns=NOW)
        self.assertEqual(result["outcome"], PROMOTION_FAILED)
        self.assertEqual(result["write_outcome"], NOT_CREATED)
        self.assertEqual(len(coordinator.write_failed_keys), 1)
        self.assertEqual(coordinator.promoted_keys, ())
        self.assertEqual(len(coordinator.fenced_keys), 1)

    def test_the_same_finding_is_never_written_twice(self):
        verdict = _verdict()
        first = self.coordinator.evaluate(verdict, REQUEST, CAPSULE,
                                          now_monotonic_ns=NOW)
        second = self.coordinator.evaluate(verdict, REQUEST, CAPSULE,
                                           now_monotonic_ns=NOW + 5 * SECOND)
        self.assertEqual(first["outcome"], PROMOTION_RECORDED)
        self.assertEqual(second["outcome"], PROMOTION_REFUSED)
        self.assertIn("DUPLICATE_PROMOTION", second["refusals"])
        self.assertEqual(second["outcome"], PROMOTION_REFUSED)
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
        self.assertEqual(imported, {"__future__", "bisect", "collections", "dataclasses",
                                    "os", "scythe_graphops_adapter",
                                    "scythe_promotion_ceilings",
                                    "scythe_promotion_ledger_writer",
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


class VocabularyTests(unittest.TestCase):
    """§5, the coordinator's half of SCYTHE_VERDICT_VOCABULARIES.md.

    The cross-vocabulary name check lives in test_scythe_verdict_vocabularies,
    because it reads more modules than this one.
    """

    def setUp(self):
        self.audit = PromotionAudit()
        self.coordinator = PromotionCoordinator(self.audit, mode=MODE_SHADOW)

    def test_the_merit_set_is_bound_by_reference_and_not_copied(self):
        """A copy is a second answer to a question that has one, and it drifts
        in the direction that makes disjointness pass while the vocabulary is
        wrong."""
        self.assertIs(MERIT_REFUSALS, policy_refusals)

    def test_budget_exhausted_is_executability_and_not_merit(self):
        """It was placed here ad hoc before the set it belonged to had a name."""
        self.assertIn(BUDGET_EXHAUSTED, EXECUTABILITY_REFUSALS)
        self.assertNotIn(BUDGET_EXHAUSTED, MERIT_REFUSALS)

    def test_every_executability_code_carries_its_repair(self):
        """The discriminating question is who repairs it, and how. A code whose
        note is a gloss rather than a repair has not answered it."""
        for code in EXECUTABILITY_REFUSALS:
            self.assertIn(code, EXECUTABILITY_NOTES)
            self.assertIn("REPAIRED BY", EXECUTABILITY_NOTES[code])

    def test_a_merit_refusal_is_counted_only_in_the_merit_set(self):
        self.coordinator.evaluate(_satisfied(), REQUEST, CAPSULE,
                                  now_monotonic_ns=NOW)
        status = self.coordinator.status()
        self.assertTrue(status["merit_refusals"])
        self.assertEqual(status["executability_refusals"], {})

    def test_a_budget_refusal_is_counted_only_in_the_executability_set(self):
        for index in range(PROMOTION_BUDGET + 3):
            self.coordinator.evaluate(_distinct(index), REQUEST, CAPSULE,
                                      now_monotonic_ns=NOW)
        status = self.coordinator.status()
        self.assertEqual(status["executability_refusals"], {BUDGET_EXHAUSTED: 3})
        self.assertEqual(status["merit_refusals"], {})

    def test_the_two_counts_are_never_summed(self):
        """A total would answer 'how many refusals' with a number mixing a
        judgement about the finding and a judgement about the apparatus."""
        status = self.coordinator.status()
        for key in status:
            self.assertNotIn("total_refusals", key)
        self.assertIn("NEVER SUMMED", status["refusal_counts_note"])

    def test_a_refused_result_names_its_refusals_as_merit(self):
        result = self.coordinator.evaluate(_satisfied(), REQUEST, CAPSULE,
                                           now_monotonic_ns=NOW)
        self.assertEqual(result["merit_refusals"], result["refusals"])
        self.assertNotIn("executability_code", result)

    def test_a_suppressed_result_names_its_executability_code(self):
        for index in range(PROMOTION_BUDGET + 1):
            result = self.coordinator.evaluate(_distinct(index), REQUEST, CAPSULE,
                                               now_monotonic_ns=NOW)
        self.assertEqual(result["executability_code"], BUDGET_EXHAUSTED)
        self.assertNotIn("merit_refusals", result)

    def test_the_counts_survive_across_evaluations(self):
        self.coordinator.evaluate(_satisfied(), REQUEST, CAPSULE,
                                  now_monotonic_ns=NOW)
        self.coordinator.evaluate(_satisfied(), REQUEST, CAPSULE,
                                  now_monotonic_ns=NOW + SECOND)
        counts = self.coordinator.status()["merit_refusals"]
        self.assertEqual(sorted(set(counts.values())), [2])

    def test_identity_unresolved_became_reachable_in_slice_4(self):
        """Slice 3 declared it unreachable and said this test would have to
        change. It changed."""
        self.assertIn(IDENTITY_UNRESOLVED, EXECUTABILITY_REFUSALS)
        self.assertEqual(NOT_YET_REACHABLE, ())
        self.assertEqual(
            self.coordinator.status()["executability_not_yet_reachable"], [])

    def test_the_coordinator_lock_is_declared_present(self):
        status = self.coordinator.status()
        self.assertEqual(status["coordinator_lock"], "PLAIN_LOCK")
        self.assertFalse(status["durable_ledger_connected"])
        self.assertIsNone(status["seeded_from_ledger"])

    def test_the_store_is_reached_through_the_writer_and_not_directly(self):
        """Slice 6b connected the coordinator to the ledger, and did it one way:
        coordinator -> writer -> reader. The store keeps exactly one caller."""
        with open(ledger_module.__file__, "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        modules = {node.module for node in ast.walk(tree)
                   if isinstance(node, ast.ImportFrom)}
        self.assertNotIn("scythe_promotion_ledger_store", modules)
        self.assertIn("scythe_promotion_ledger_writer", modules)


class WriteResultTests(unittest.TestCase):
    """Three states, not a Boolean (§13a B.1)."""

    def test_the_three_outcomes_are_the_declared_set(self):
        self.assertEqual(WRITE_OUTCOMES, (CREATED, NOT_CREATED, UNKNOWN))

    def test_a_bool_is_not_a_write_outcome(self):
        """The old shape. It must fail loudly rather than mean something."""
        for value in (True, False):
            with self.assertRaises(PromotionLedgerError):
                WriteResult(value)

    def test_an_unknown_outcome_is_refused_rather_than_recorded(self):
        with self.assertRaises(PromotionLedgerError):
            WriteResult("MAYBE")


class UnresolvedTests(unittest.TestCase):
    """UNKNOWN and exceptions take the same path (§13a B.2)."""

    def setUp(self):
        self.audit = PromotionAudit()

    def _armed(self, writer):
        return PromotionCoordinator(self.audit, mode=MODE_ARMED, writer=writer)

    def _events(self):
        return [r["event"] for r in self.audit.status()["records"]]

    def test_an_unknown_result_leaves_the_identity_reserved(self):
        coordinator = self._armed(lambda d: WriteResult(UNKNOWN, "timed out"))
        result = coordinator.evaluate(_verdict(), REQUEST, CAPSULE,
                                      now_monotonic_ns=NOW)
        self.assertEqual(result["outcome"], PROMOTION_OUTCOME_UNRESOLVED)
        self.assertEqual(result["executability_code"], IDENTITY_UNRESOLVED)
        self.assertEqual(len(coordinator.unresolved_keys), 1)
        self.assertEqual(coordinator.promoted_keys, ())
        self.assertEqual(coordinator.write_failed_keys, ())

    def test_no_terminal_event_is_recorded_for_an_unknown_result(self):
        coordinator = self._armed(lambda d: WriteResult(UNKNOWN))
        coordinator.evaluate(_verdict(), REQUEST, CAPSULE, now_monotonic_ns=NOW)
        self.assertEqual(self._events(),
                         [PROMOTION_ATTEMPTED, PROMOTION_OUTCOME_UNRESOLVED])
        self.assertNotIn(PROMOTION_FAILED, self._events())

    def test_a_raising_writer_does_not_propagate(self):
        """Raising hands the caller no idempotency key, and the key is the only
        handle on a reservation that already fences an identity."""
        def writer(decision):
            raise TimeoutError("bus did not answer")

        coordinator = self._armed(writer)
        result = coordinator.evaluate(_verdict(), REQUEST, CAPSULE,
                                      now_monotonic_ns=NOW)
        self.assertEqual(result["outcome"], PROMOTION_OUTCOME_UNRESOLVED)
        self.assertIn("idempotency_key", result)
        self.assertEqual(len(coordinator.unresolved_keys), 1)

    def test_the_exception_message_is_discarded_and_not_truncated(self):
        """Adapter exception text carries endpoints, payload fragments and
        credentials. The class name is the part about the apparatus."""
        secret = "https://graphops.internal/write?token=hunter2"

        def writer(decision):
            raise ConnectionError(secret)

        coordinator = self._armed(writer)
        result = coordinator.evaluate(_verdict(), REQUEST, CAPSULE,
                                      now_monotonic_ns=NOW)
        self.assertEqual(result["detail"], {"cause": WRITER_EXCEPTION,
                                            "exception_type": "ConnectionError"})
        blob = json.dumps([result, coordinator.status(), self.audit.status()])
        self.assertNotIn("hunter2", blob)
        self.assertNotIn("graphops.internal", blob)
        # Not merely absent because it was cut short.
        self.assertNotIn(secret[:20], blob)

    def test_an_adapter_returning_something_else_told_us_nothing(self):
        """Nothing is UNKNOWN, never NOT_CREATED, which would be a claim."""
        coordinator = self._armed(lambda d: "ok")
        result = coordinator.evaluate(_verdict(), REQUEST, CAPSULE,
                                      now_monotonic_ns=NOW)
        self.assertEqual(result["outcome"], PROMOTION_OUTCOME_UNRESOLVED)
        self.assertEqual(coordinator.write_failed_keys, ())

    def test_an_invalid_return_is_not_labelled_an_exception(self):
        """No exception occurred. Calling it one sends an operator looking for
        a failure that never happened."""
        coordinator = self._armed(lambda d: "ok")
        result = coordinator.evaluate(_verdict(), REQUEST, CAPSULE,
                                      now_monotonic_ns=NOW)
        self.assertEqual(result["detail"], {"cause": INVALID_WRITER_RESULT,
                                            "returned_type": "str"})
        self.assertNotIn("exception_type", result["detail"])

    def test_an_adapter_that_says_unknown_is_its_own_cause(self):
        """The adapter answered, and its answer was that it does not know.
        That is a different fact from a writer that raised."""
        coordinator = self._armed(lambda d: WriteResult(UNKNOWN, "timed out"))
        result = coordinator.evaluate(_verdict(), REQUEST, CAPSULE,
                                      now_monotonic_ns=NOW)
        self.assertEqual(result["detail"], {"cause": ADAPTER_REPORTED_UNKNOWN})

    def test_the_three_causes_are_distinguishable_and_declared(self):
        self.assertEqual(UNRESOLVED_CAUSES,
                         (WRITER_EXCEPTION, INVALID_WRITER_RESULT,
                          ADAPTER_REPORTED_UNKNOWN))
        causes = set()
        for writer in (lambda d: (_ for _ in ()).throw(TimeoutError("x")),
                       lambda d: object(),
                       lambda d: WriteResult(UNKNOWN)):
            coordinator = self._armed(writer)
            causes.add(coordinator.evaluate(_verdict(), REQUEST, CAPSULE,
                                            now_monotonic_ns=NOW)["detail"]["cause"])
        self.assertEqual(causes, set(UNRESOLVED_CAUSES))

    def test_a_returned_value_is_never_carried_out(self):
        """Type name only, for the same reason as an exception message."""
        coordinator = self._armed(lambda d: "token=hunter2")
        result = coordinator.evaluate(_verdict(), REQUEST, CAPSULE,
                                      now_monotonic_ns=NOW)
        blob = json.dumps([result, coordinator.status(), self.audit.status()])
        self.assertNotIn("hunter2", blob)

    def test_a_second_evaluation_is_unresolved_and_not_a_duplicate(self):
        """§13a B.4. Both fence; only one of them claims the finding reached
        the graph, and that one would be false."""
        coordinator = self._armed(lambda d: WriteResult(UNKNOWN))
        verdict = _verdict()
        coordinator.evaluate(verdict, REQUEST, CAPSULE, now_monotonic_ns=NOW)
        second = coordinator.evaluate(verdict, REQUEST, CAPSULE,
                                      now_monotonic_ns=NOW + SECOND)
        self.assertEqual(second["executability_code"], IDENTITY_UNRESOLVED)
        self.assertNotIn("refusals", second)
        self.assertNotIn("DUPLICATE_PROMOTION", json.dumps(second))

    def test_a_definite_failure_refuses_a_retry_and_is_not_a_duplicate(self):
        """NOT_CREATED means nothing is in the graph, so DUPLICATE_PROMOTION
        would be a false claim. Re-promotion is an operator action."""
        coordinator = self._armed(lambda d: WriteResult(NOT_CREATED))
        verdict = _verdict()
        coordinator.evaluate(verdict, REQUEST, CAPSULE, now_monotonic_ns=NOW)
        second = coordinator.evaluate(verdict, REQUEST, CAPSULE,
                                      now_monotonic_ns=NOW + SECOND)
        self.assertEqual(second["executability_code"], RETRY_REQUIRES_OPERATOR)
        self.assertNotIn("DUPLICATE_PROMOTION", json.dumps(second))

    def test_an_unresolved_identity_blocks_only_itself(self):
        coordinator = self._armed(_unknown_for("promotion", accept_others=True))
        first = coordinator.evaluate(_distinct(0), REQUEST, CAPSULE,
                                     now_monotonic_ns=NOW)
        self.assertEqual(first["outcome"], PROMOTION_OUTCOME_UNRESOLVED)
        second = coordinator.evaluate(_distinct(1), REQUEST, CAPSULE,
                                      now_monotonic_ns=NOW)
        self.assertEqual(second["outcome"], PROMOTION_RECORDED)

    def test_the_unresolved_set_outlives_the_audit_ring(self):
        """§13a B.5. The ring is bounded by design and drops the transition
        while the reservation itself persists forever."""
        coordinator = self._armed(_unknown_for("promotion", accept_others=True))
        coordinator.evaluate(_distinct(0), REQUEST, CAPSULE, now_monotonic_ns=NOW)
        key = coordinator.unresolved_keys[0]
        for index in range(1, MAX_AUDIT_RECORDS + 10):
            coordinator.evaluate(_distinct(index), REQUEST, CAPSULE,
                                 now_monotonic_ns=NOW + index * 200 * SECOND)
        ring = json.dumps(self.audit.status()["records"])
        self.assertNotIn(key, ring)
        self.assertEqual(coordinator.unresolved_keys, (key,))
        self.assertEqual(coordinator.status()["unresolved_keys"], [key])

    def test_the_window_stays_ordered_when_instants_arrive_out_of_order(self):
        """The lock orders acquisitions, not the instants callers captured
        before acquiring it. Two threads can read 1000 and 1010, and the one
        holding 1010 can win the lock.

        Appending would leave [1010, 1000], and pruning from the left would stop
        at 1010 and keep the expired 1000 behind it. That fails conservatively
        -- over-suppressing rather than overspending -- which is exactly why a
        passing suite would not have caught it.
        """
        posture = ledger_module._Posture()
        for index, instant in enumerate((1010, 1000, 1020)):
            posture.reserve(f"key-{index}", instant)
        self.assertEqual(list(posture.window), [1000, 1010, 1020])
        # floor = 1005, between the first two.
        self.assertEqual(posture.spent_in_window(now_ns=1020, window_ns=15), 2)
        self.assertEqual(list(posture.window), [1010, 1020])

    def test_the_window_never_outgrows_the_budget(self):
        """What makes the ordered insert affordable: pruning happens before the
        budget check, and a reservation is only taken below it."""
        coordinator = self._armed(lambda d: WriteResult(CREATED))
        for index in range(PROMOTION_BUDGET + 6):
            coordinator.evaluate(_distinct(index), REQUEST, CAPSULE,
                                 now_monotonic_ns=NOW + index)
        self.assertLessEqual(len(coordinator._real.window), PROMOTION_BUDGET)

    def test_the_window_is_not_spent_twice_by_a_slow_writer(self):
        """Spent at reservation. Spending it again at the terminal update would
        measure the adapter's latency as promotion rate."""
        coordinator = self._armed(lambda d: WriteResult(CREATED))
        for index in range(3):
            coordinator.evaluate(_distinct(index), REQUEST, CAPSULE,
                                 now_monotonic_ns=NOW)
        self.assertEqual(len(coordinator._real.window), 3)


class LockTests(unittest.TestCase):
    """§3 and §4. The races are forced rather than waited for."""

    def setUp(self):
        self.audit = PromotionAudit()

    def _race(self, coordinator, verdicts, *, at="decision"):
        """Run evaluations on threads, tripping a barrier mid-step.

        The barrier forces the interleaving; it is not what is asserted. Under
        the lock only one thread reaches it and the barrier times out, so the
        assertion is on the outcome and a timing wobble makes the test skip an
        interleaving rather than report a false result.

        `at` chooses which check-then-act window is forced, and the choice
        matters: a barrier inside the policy forces two threads to decide before
        either reserves, which is §3's *same identity* race, and leaves the
        budget race unforced because the budget is read later. `at="budget"`
        opens the window between reading the spend and taking the reservation,
        which is §3's *distinct identities* race.
        """
        barrier = threading.Barrier(len(verdicts), timeout=0.35)
        results = []
        lock = threading.Lock()

        def trip():
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                pass

        real_decide = ledger_module.decide_promotion
        real_spend = ledger_module._Posture.spent_in_window

        def decide(*args, **kwargs):
            decision = real_decide(*args, **kwargs)
            trip()
            return decision

        def spend(self_, *args, **kwargs):
            count = real_spend(self_, *args, **kwargs)
            trip()
            return count

        if at == "decision":
            ledger_module.decide_promotion = decide
        else:
            ledger_module._Posture.spent_in_window = spend
        try:
            threads = [threading.Thread(target=lambda v=v: _collect(
                results, lock, coordinator, v)) for v in verdicts]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive(), "an evaluation never finished")
        finally:
            ledger_module.decide_promotion = real_decide
            ledger_module._Posture.spent_in_window = real_spend
        return results

    def test_two_evaluations_of_one_identity_produce_one_promotion(self):
        """§3's first race. Both threads pass the duplicate check before either
        reserves, unless the section is atomic."""
        written = []
        coordinator = PromotionCoordinator(
            self.audit, mode=MODE_ARMED, writer=_accepting_writer(written))
        verdict = _verdict()
        outcomes = self._race(coordinator, [verdict, verdict])
        self.assertEqual(len(written), 1)
        self.assertEqual(outcomes.count(PROMOTION_RECORDED), 1)
        self.assertEqual(len(coordinator.fenced_keys), 1)

    def test_concurrent_distinct_identities_do_not_overspend_the_budget(self):
        """§3's second race. All threads pass the budget check before any
        reserves, unless the section is atomic."""
        written = []
        coordinator = PromotionCoordinator(
            self.audit, mode=MODE_ARMED, budget=3,
            writer=_accepting_writer(written))
        outcomes = self._race(coordinator, [_distinct(i) for i in range(8)],
                              at="budget")
        self.assertEqual(len(written), 3)
        self.assertEqual(outcomes.count(PROMOTION_RECORDED), 3)
        self.assertEqual(outcomes.count(PROMOTION_SUPPRESSED), 5)

    def test_shadow_takes_the_same_lock(self):
        """§3: the lock is not conditional on mode. A SHADOW that is not locked
        measures a concurrency behaviour ARMED will not have."""
        coordinator = PromotionCoordinator(self.audit, mode=MODE_SHADOW)
        verdict = _verdict()
        outcomes = self._race(coordinator, [verdict, verdict])
        self.assertEqual(outcomes.count(WOULD_PROMOTE), 1)
        self.assertEqual(outcomes.count(WOULD_BE_REFUSED), 1)

    def test_the_writer_is_called_with_the_lock_released(self):
        """A non-blocking acquire from the writer's own thread. With a plain
        Lock, a held lock cannot be reacquired by its holder, so success here
        means it is genuinely free."""
        observed = []

        def writer(decision):
            acquired = coordinator._lock.acquire(blocking=False)
            observed.append(acquired)
            if acquired:
                coordinator._lock.release()
            return WriteResult(CREATED)

        coordinator = PromotionCoordinator(self.audit, mode=MODE_ARMED,
                                           writer=writer)
        coordinator.evaluate(_verdict(), REQUEST, CAPSULE, now_monotonic_ns=NOW)
        self.assertEqual(observed, [True])

    def test_a_writer_that_re_enters_the_coordinator_completes(self):
        """Release-before-call, proved by the thing it exists to permit."""
        seen = []

        def writer(decision):
            if not seen:
                seen.append(decision.idempotency_key)
                inner = coordinator.evaluate(_distinct(99), REQUEST, CAPSULE,
                                             now_monotonic_ns=NOW)
                seen.append(inner["outcome"])
            return WriteResult(CREATED)

        coordinator = PromotionCoordinator(self.audit, mode=MODE_ARMED,
                                           writer=writer)
        result = coordinator.evaluate(_distinct(0), REQUEST, CAPSULE,
                                      now_monotonic_ns=NOW)
        self.assertEqual(result["outcome"], PROMOTION_RECORDED)
        self.assertEqual(seen[1], PROMOTION_RECORDED)

    def test_re_entry_inside_the_critical_section_deadlocks(self):
        """§4: a plain Lock makes the mistake a deadlock rather than a wrong
        answer under the interleaving hardest to reproduce.

        Asserted by a non-blocking second acquisition rather than by stranding a
        thread: a deadlocked thread in this process would hold the lock for
        every test after it.
        """
        coordinator = PromotionCoordinator(self.audit, mode=MODE_SHADOW)
        self.assertNotIsInstance(coordinator._lock, type(threading.RLock()))
        with coordinator._lock:
            self.assertFalse(coordinator._lock.acquire(blocking=False))

    def test_the_public_accessors_acquire(self):
        """Held from another thread, every public accessor blocks. If one did
        not, it would be reading a structure mid-mutation."""
        coordinator = PromotionCoordinator(self.audit, mode=MODE_SHADOW)
        for name in ("promoted_keys", "policy_keys", "unresolved_keys",
                     "write_failed_keys", "fenced_keys"):
            done = threading.Event()
            thread = threading.Thread(
                target=lambda n=name: (getattr(coordinator, n), done.set()))
            with coordinator._lock:
                thread.start()
                self.assertFalse(done.wait(timeout=0.1), f"{name} did not acquire")
            thread.join(timeout=5)
            self.assertTrue(done.is_set(), name)

    def test_status_acquires(self):
        coordinator = PromotionCoordinator(self.audit, mode=MODE_SHADOW)
        done = threading.Event()
        thread = threading.Thread(target=lambda: (coordinator.status(), done.set()))
        with coordinator._lock:
            thread.start()
            self.assertFalse(done.wait(timeout=0.1))
        thread.join(timeout=5)
        self.assertTrue(done.is_set())


class CriticalSectionScopeTests(unittest.TestCase):
    """What the critical section is allowed to touch."""

    def setUp(self):
        with open(ledger_module.__file__, "r", encoding="utf-8") as handle:
            self.tree = ast.parse(handle.read())
        self.functions = {node.name: node for node in ast.walk(self.tree)
                          if isinstance(node, ast.FunctionDef)}

    def _attributes(self, name):
        return {node.attr for node in ast.walk(self.functions[name])
                if isinstance(node, ast.Attribute)}

    def test_the_critical_section_calls_no_locking_accessor(self):
        """§13a B.7. Calling your own property does not look like calling out,
        which is the direction a later refactor reintroduces it from -- and with
        a plain Lock the result is a deadlock, in production, under load."""
        locking = {"promoted_keys", "policy_keys", "unresolved_keys",
                   "write_failed_keys", "fenced_keys", "status"}
        for name in ("_reserve_unlocked", "_refuse_unlocked",
                     "_resolve_unlocked", "_status_unlocked"):
            self.assertEqual(self._attributes(name) & locking, set(), name)

    def test_the_writer_is_not_called_from_inside_the_lock(self):
        for name in ("_reserve_unlocked", "_resolve_unlocked"):
            self.assertNotIn("_writer", self._attributes(name), name)

    def test_the_posture_class_takes_no_lock_of_its_own(self):
        """Two locks on one object make the order between them a question
        nobody asked."""
        posture = {node.name for node in ast.walk(self.tree)
                   if isinstance(node, ast.ClassDef) and node.name == "_Posture"}
        self.assertEqual(posture, {"_Posture"})
        source = ast.get_source_segment(
            open(ledger_module.__file__, encoding="utf-8").read(),
            [n for n in ast.walk(self.tree)
             if isinstance(n, ast.ClassDef) and n.name == "_Posture"][0])
        self.assertNotIn("Lock", source)
        self.assertNotIn("self._lock", source)

    def test_the_ownership_module_is_not_imported(self):
        """The coordinator is handed a writer; how ownership was obtained is
        not its business, and importing the producer would make it so."""
        modules = {node.module for node in ast.walk(self.tree)
                   if isinstance(node, ast.ImportFrom)}
        self.assertNotIn("scythe_promotion_ledger_ownership", modules)
        self.assertNotIn("fcntl", modules)


if __name__ == "__main__":
    unittest.main()
