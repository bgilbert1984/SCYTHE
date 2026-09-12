"""Slice 8: C1, C2, and the opposite tests that calibrate them."""

import ast
import json
import os
import tempfile
import unittest

import scythe_promotion_ceilings as ceilings_module
from scythe_promotion_ceilings import (
    C1, C2, CEILINGS, DURABLE_CEILING_REACHED, NOT_SIMULABLE,
    OUTSTANDING_RESERVATION_CEILING_REACHED, RESERVATION_CEILING,
    UNRESOLVED_CEILING, Ceiling, CeilingError, CeilingTotals,
    configuration_identity, status,
)
from scythe_promotion_ledger import (
    CREATED, MODE_ARMED, MODE_SHADOW, NOT_CREATED, PROMOTION_RECORDED,
    PROMOTION_SUPPRESSED, UNKNOWN, PromotionAudit, PromotionCoordinator,
    WriteResult,
)
from scythe_promotion_ledger_ownership import LedgerOwnership
from scythe_promotion_ledger_store import read_ledger
from scythe_promotion_ledger_writer import LedgerWriter
from scythe_promotion_lineage import Lineage, generation_path
from scythe_promotion_reconciliation import (
    CEILING_REACHED, GRAPH_RECORD_NOT_FOUND, OperatorRequest, close_generation,
    reconcile_identity,
)
from test_scythe_promotion_ledger import CAPSULE, REQUEST, _distinct

GOOD_MOUNTS = (("/", "ext4"),)
OWNER = {"boot_id": "boot-a", "pid": 4242, "start_ticks": 99}
NOW = 10_000 * 1_000_000_000


class DeclarationTests(unittest.TestCase):
    """§13f G.1: five things, or it is not a ceiling."""

    def test_both_ceilings_declare_all_five(self):
        for ceiling in CEILINGS:
            for field in ("subject", "accounting_source", "scope", "reset_rule",
                          "refusal"):
                self.assertTrue(getattr(ceiling, field), f"{ceiling.name}.{field}")

    def test_a_ceiling_missing_a_declaration_is_refused(self):
        for blank in ("subject", "accounting_source", "scope", "reset_rule"):
            fields = dict(name="X", subject="counts the things that happened",
                          accounting_source="records in the file on disk",
                          scope="one generation of the ledger",
                          reset_rule="an explicit operator act, never time",
                          refusal=DURABLE_CEILING_REACHED, limit=5)
            fields[blank] = ""
            with self.subTest(blank=blank), self.assertRaises(CeilingError):
                Ceiling(**fields)

    def test_boundedceiling_is_not_imported(self):
        """Its violations are merit findings; these refusals are executability
        codes, and reuse would be §5's contamination through a shared class."""
        with open(ceilings_module.__file__, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.update(a.name for a in node.names)
                self.assertNotEqual(node.module, "scythe_invariant_ledger")
        self.assertNotIn("BoundedCeiling", imported)
        self.assertNotIn("Finding", imported)

    def test_the_two_are_calibrated_by_opposite_tests(self):
        published = status(CeilingTotals())
        self.assertEqual(published["calibration"]["C1"], "SHOULD_NEVER_FIRE")
        self.assertIn("FIRES", published["calibration"]["C2"])

    def test_the_values_are_the_contract_declared_ones(self):
        self.assertEqual(RESERVATION_CEILING, 10_000)
        self.assertEqual(UNRESOLVED_CEILING, 32)
        self.assertEqual(C1.limit, RESERVATION_CEILING)
        self.assertEqual(C2.limit, UNRESOLVED_CEILING)

    def test_the_ceilings_are_not_configurable(self):
        """No constructor argument, environment variable or setter. A ceiling a
        deployment can raise is one that will be raised the moment it fires.

        Read from the AST. A first version scanned the source text for
        "environ" and matched this module's own comment saying there is no
        environment variable -- the eighth appearance of that false positive in
        this repository, written during the slice whose prerequisite was
        repairing a check for exactly it. Prose about a thing is not the thing,
        and apparently knowing that is not enough.
        """
        with open(ceilings_module.__file__, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        attributes = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        self.assertNotIn("getenv", attributes)
        self.assertNotIn("environ", attributes)
        imported = {a.name for n in ast.walk(tree)
                    if isinstance(n, ast.ImportFrom) for a in n.names}
        self.assertNotIn("getenv", imported)
        coordinator = PromotionCoordinator(PromotionAudit())
        for name in coordinator.__init__.__code__.co_varnames:
            self.assertNotIn("ceiling", name.lower())

    def test_the_configuration_identity_covers_more_than_the_numbers(self):
        """A ceiling whose subject changed while its number stayed the same is
        a different ceiling wearing the same value."""
        before = configuration_identity()
        original = C1.subject
        object.__setattr__(C1, "subject", original + " and also something else")
        try:
            self.assertNotEqual(configuration_identity(), before)
        finally:
            object.__setattr__(C1, "subject", original)
        self.assertEqual(configuration_identity(), before)


class TotalsTests(unittest.TestCase):
    """§13f G.2, G.3."""

    def test_c1_counts_reservations_regardless_of_outcome(self):
        totals = CeilingTotals()
        for _ in range(5):
            totals.reserve()
            totals.resolve()
        self.assertEqual(totals.reservations_in_generation, 5)
        self.assertEqual(totals.unresolved_in_lineage, 0)

    def test_c2_counts_only_what_is_still_outstanding(self):
        totals = CeilingTotals()
        for _ in range(4):
            totals.reserve()
        totals.resolve()
        self.assertEqual(totals.unresolved_in_lineage, 3)

    def test_a_new_generation_resets_c1_and_not_c2(self):
        """The sharp edge. Were C2 to reset, an operator facing it could clear
        it by closing the generation."""
        totals = CeilingTotals()
        for _ in range(6):
            totals.reserve()
        totals.new_generation()
        self.assertEqual(totals.reservations_in_generation, 0)
        self.assertEqual(totals.unresolved_in_lineage, 6)

    def test_c2_decreases_on_reconciliation(self):
        totals = CeilingTotals()
        totals.reserve()
        totals.reconcile()
        self.assertEqual(totals.unresolved_in_lineage, 0)

    def test_reaching_the_limit_refuses(self):
        totals = CeilingTotals(reservations_in_generation=RESERVATION_CEILING)
        self.assertEqual(totals.refusal(), DURABLE_CEILING_REACHED)
        totals = CeilingTotals(unresolved_in_lineage=UNRESOLVED_CEILING)
        self.assertEqual(totals.refusal(),
                         OUTSTANDING_RESERVATION_CEILING_REACHED)

    def test_below_the_limit_does_not_refuse(self):
        self.assertIsNone(CeilingTotals(
            reservations_in_generation=RESERVATION_CEILING - 1,
            unresolved_in_lineage=UNRESOLVED_CEILING - 1).refusal())


class CoordinatorCeilingTests(unittest.TestCase):
    """The ceilings where they are enforced."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.dir = self._dir.name
        self.root = os.path.join(self.dir, "promotion")
        self.owner = LedgerOwnership(lineage_root=self.root, mounts=GOOD_MOUNTS,
                                     refused_prefixes=(),
                                     devices={os.stat(self.dir).st_dev: "ext4"})
        self.addCleanup(self.owner.release)
        self.assertTrue(self.owner.acquire())
        self.ledger = LedgerWriter(path=generation_path(self.root, 0),
                                   ownership=self.owner.scope,
                                   refused_prefixes=())
        with self.ledger.owned() as session:
            session.initialize()
            session.declare_generation("gen-1", OWNER)

    def _coordinator(self, writer=None, budget=1000):
        return PromotionCoordinator(
            PromotionAudit(), mode=MODE_ARMED, ledger=self.ledger, budget=budget,
            writer=writer or (lambda d: WriteResult(CREATED)))

    def test_c2_refuses_once_enough_writes_go_unanswered(self):
        """The designed detector: an adapter that stops answering."""
        coordinator = self._coordinator(writer=lambda d: WriteResult(UNKNOWN))
        outcomes = [coordinator.evaluate(_distinct(i), REQUEST, CAPSULE,
                                         now_monotonic_ns=NOW)
                    for i in range(UNRESOLVED_CEILING + 2)]
        codes = [o.get("executability_code") for o in outcomes]
        self.assertIn(OUTSTANDING_RESERVATION_CEILING_REACHED, codes)
        self.assertEqual(codes.count(OUTSTANDING_RESERVATION_CEILING_REACHED), 2)

    def test_a_ceiling_refusal_writes_no_record(self):
        """§13f G.5. A ceiling that counted the reservations it refused would
        raise itself every time it fired."""
        coordinator = self._coordinator(writer=lambda d: WriteResult(UNKNOWN))
        for index in range(UNRESOLVED_CEILING):
            coordinator.evaluate(_distinct(index), REQUEST, CAPSULE,
                                 now_monotonic_ns=NOW)
        with open(self.ledger.path, "rb") as handle:
            before = handle.read()
        fenced_before = coordinator.fenced_keys
        result = coordinator.evaluate(_distinct(999), REQUEST, CAPSULE,
                                      now_monotonic_ns=NOW)
        self.assertEqual(result["executability_code"],
                         OUTSTANDING_RESERVATION_CEILING_REACHED)
        with open(self.ledger.path, "rb") as handle:
            self.assertEqual(handle.read(), before)
        self.assertEqual(coordinator.fenced_keys, fenced_before)

    def test_the_totals_are_rebuilt_from_the_ledger_after_a_restart(self):
        coordinator = self._coordinator(writer=lambda d: WriteResult(UNKNOWN))
        for index in range(5):
            coordinator.evaluate(_distinct(index), REQUEST, CAPSULE,
                                 now_monotonic_ns=NOW)
        fresh = self._coordinator(writer=lambda d: WriteResult(CREATED))
        self.assertEqual(fresh._ceilings.reservations_in_generation, 5)
        self.assertEqual(fresh._ceilings.unresolved_in_lineage, 5)

    def _recount_from_disk(self):
        """Both totals, read from the files without the production helpers.

        A first version compared the cache to `LedgerWriter.lineage_unresolved`
        -- the same function that seeds it. That is a tautology: a control that
        broke the helper moved both sides together and the test could not fail.
        The recompute has to be independent or it is not a check.
        """
        chain = Lineage(root=self.root).chain()
        return (
            sum(read_ledger(g.path).reservations_total
                for g in chain if g.ordinal == max(x.ordinal for x in chain)),
            sum(len(read_ledger(g.path).unresolved) for g in chain),
        )

    def test_the_cache_agrees_with_the_disk(self):
        """What keeps a cache honest is not a promise (§13f G.6)."""
        coordinator = self._coordinator(writer=lambda d: WriteResult(UNKNOWN))
        for index in range(7):
            coordinator.evaluate(_distinct(index), REQUEST, CAPSULE,
                                 now_monotonic_ns=NOW)
        reservations, unresolved = self._recount_from_disk()
        self.assertEqual(coordinator._ceilings.reservations_in_generation,
                         reservations)
        self.assertEqual(coordinator._ceilings.unresolved_in_lineage, unresolved)

    def test_the_cache_agrees_with_the_disk_across_a_closure(self):
        coordinator = self._coordinator(writer=lambda d: WriteResult(UNKNOWN))
        for index in range(3):
            coordinator.evaluate(_distinct(index), REQUEST, CAPSULE,
                                 now_monotonic_ns=NOW)
        close_generation(Lineage(root=self.root), OWNER, generation="gen-2",
                         reason=CEILING_REACHED,
                         request=OperatorRequest(operator="op", request_id="r1"),
                         nonce="n1")
        successor = LedgerWriter(path=generation_path(self.root, 1),
                                 ownership=self.owner.scope, refused_prefixes=())
        fresh = PromotionCoordinator(PromotionAudit(), mode=MODE_ARMED,
                                     ledger=successor,
                                     writer=lambda d: WriteResult(CREATED))
        reservations, unresolved = self._recount_from_disk()
        self.assertEqual(fresh._ceilings.reservations_in_generation, reservations)
        self.assertEqual(fresh._ceilings.unresolved_in_lineage, unresolved)
        self.assertEqual(unresolved, 3)

    def test_c2_survives_a_generation_closure_and_c1_does_not(self):
        coordinator = self._coordinator(writer=lambda d: WriteResult(UNKNOWN))
        for index in range(4):
            coordinator.evaluate(_distinct(index), REQUEST, CAPSULE,
                                 now_monotonic_ns=NOW)
        close_generation(Lineage(root=self.root), OWNER, generation="gen-2",
                         reason=CEILING_REACHED,
                         request=OperatorRequest(operator="op", request_id="r1"),
                         nonce="n1")
        successor = LedgerWriter(path=generation_path(self.root, 1),
                                 ownership=self.owner.scope, refused_prefixes=())
        fresh = PromotionCoordinator(PromotionAudit(), mode=MODE_ARMED,
                                     ledger=successor,
                                     writer=lambda d: WriteResult(CREATED))
        self.assertEqual(fresh._ceilings.reservations_in_generation, 0)
        self.assertEqual(fresh._ceilings.unresolved_in_lineage, 4)

    def test_closing_the_generation_restores_promotion_after_c1(self):
        """C1's exit, end to end: slice 7 declared CEILING_REACHED without
        inventing the ceiling."""
        coordinator = self._coordinator()
        coordinator._ceilings.reservations_in_generation = RESERVATION_CEILING
        refused = coordinator.evaluate(_distinct(1), REQUEST, CAPSULE,
                                       now_monotonic_ns=NOW)
        self.assertEqual(refused["executability_code"], DURABLE_CEILING_REACHED)
        close_generation(Lineage(root=self.root), OWNER, generation="gen-2",
                         reason=CEILING_REACHED,
                         request=OperatorRequest(operator="op", request_id="r1"),
                         nonce="n1")
        successor = LedgerWriter(path=generation_path(self.root, 1),
                                 ownership=self.owner.scope, refused_prefixes=())
        fresh = PromotionCoordinator(PromotionAudit(), mode=MODE_ARMED,
                                     ledger=successor,
                                     writer=lambda d: WriteResult(CREATED))
        result = fresh.evaluate(_distinct(1), REQUEST, CAPSULE,
                                now_monotonic_ns=NOW)
        self.assertEqual(result["outcome"], PROMOTION_RECORDED)


class ShadowPublicationTests(unittest.TestCase):
    """§13f G.7: durable and simulated are never one number."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.dir = self._dir.name
        self.root = os.path.join(self.dir, "promotion")
        self.owner = LedgerOwnership(lineage_root=self.root, mounts=GOOD_MOUNTS,
                                     refused_prefixes=(),
                                     devices={os.stat(self.dir).st_dev: "ext4"})
        self.addCleanup(self.owner.release)
        self.owner.acquire()
        self.ledger = LedgerWriter(path=generation_path(self.root, 0),
                                   ownership=self.owner.scope,
                                   refused_prefixes=())
        with self.ledger.owned() as session:
            session.initialize()
            session.declare_generation("gen-1", OWNER)

    def test_shadow_reports_a_real_durable_c2_from_an_earlier_run(self):
        """A SHADOW coordinator reads a lineage that may hold unresolved
        reservations from an earlier authorized run. Reporting zero would be a
        false statement about the record."""
        armed = PromotionCoordinator(PromotionAudit(), mode=MODE_ARMED,
                                     ledger=self.ledger,
                                     writer=lambda d: WriteResult(UNKNOWN))
        for index in range(3):
            armed.evaluate(_distinct(index), REQUEST, CAPSULE,
                           now_monotonic_ns=NOW)
        shadow = PromotionCoordinator(PromotionAudit(), mode=MODE_SHADOW,
                                      ledger=self.ledger)
        published = shadow.status()["ceilings"]
        self.assertEqual(published["durable_unresolved_in_lineage"], 3)
        self.assertEqual(published["shadow_unresolved_simulation"], NOT_SIMULABLE)

    def test_the_simulated_count_never_stands_in_for_the_durable_one(self):
        shadow = PromotionCoordinator(PromotionAudit(), mode=MODE_SHADOW,
                                      ledger=self.ledger)
        for index in range(4):
            shadow.evaluate(_distinct(index), REQUEST, CAPSULE,
                            now_monotonic_ns=NOW)
        published = shadow.status()["ceilings"]
        self.assertEqual(published["shadow_simulated_reservations"], 4)
        self.assertEqual(published["durable_reservations_in_generation"], 0)

    def test_status_is_serialisable_and_names_the_configuration(self):
        shadow = PromotionCoordinator(PromotionAudit(), mode=MODE_SHADOW,
                                      ledger=self.ledger)
        published = shadow.status()["ceilings"]
        json.dumps(published)
        self.assertEqual(published["ceiling_configuration_identity"],
                         configuration_identity())
        self.assertFalse(published["ceilings_are_configurable"])


class CalibrationTests(unittest.TestCase):
    """§11: if C1 fires during correct operation, it was set wrong."""

    def test_c1_is_far_above_anything_the_budget_permits(self):
        """The calibration test. With C1 at 8, ordinary budget-valid operation
        reaches the catastrophe ceiling -- which is what 31n mutates."""
        from scythe_promotion_ledger import PROMOTION_BUDGET
        self.assertGreater(RESERVATION_CEILING, PROMOTION_BUDGET * 100)

    def test_c2_is_small_because_noticing_late_is_worthless(self):
        self.assertLess(UNRESOLVED_CEILING, RESERVATION_CEILING // 100)


if __name__ == "__main__":
    unittest.main()
