"""The §5.21 catalogue runner, measured from the tree.

Every verdict the runner reports is checked against the fixture ground the
plan tests stand on: the 28.8 MHz / ±1 ppm comb, the seeded UHF tunings, a
catalogue with slope-0 products (in span everywhere, entry 10's case) and
one re-sloped so spur-free visits exist. The runner's violation codes are
the plan's own refusal codes, so a verdict here is the same refusal the
plan would have raised at the visit.
"""

import unittest

from rf_promotion_envelope import (
    CAPTURED, SYNTHETIC,
    PLAN_SPUR_NOT_IN_SPAN,
    PLAN_THERMAL_NOT_SPUR_FREE,
    StratumTrialPlan,
    catalogued_spurs_in_span,
    generate_tunings,
    generate_visit_schedule,
)
from rf_validation_manifest import (
    MINIMUM_WINDOWS_PER_STRATUM, STRATUM_KEYS, TUNER_REQUIRED,
)
from rf_spur_catalogue_runner import (
    RUNNER_LO_NOT_AT_TUNING,
    RUNNER_PLAN_NOT_DECLARED,
    RUNNER_QUANTITY_NOT_FINITE,
    RUNNER_STRATUM_NOT_PLANNED,
    RUNNER_TUNING_NOT_DECLARED,
    RUNNER_WINDOW_DECLARED_TWICE,
    RUNNER_WINDOW_NOT_DECLARED,
    VERDICT_CLEAN,
    VERDICT_NOT_APPLICABLE,
    VERDICT_VIOLATION,
    CapturedWindow,
    CatalogueRunReport,
    RunnerRefused,
    assert_clean,
    run_catalogue_windows,
)
from test_rf_promotion_envelope import (
    SEED,
    VANISHES_ON_DECLARED_TERMINATION,
    _bands,
    _catalogue,
    _eligible_trials,
    _envelope,
    _plan,
    _spread,
    _spur_allocation,
    _trial_plans,
)


def _thermal_captured(envelope, positions):
    """The default trial plans, with THERMAL_NO_INPUT captured at the given
    visit positions instead of regenerated."""
    chains = sorted(envelope.admissible_chain_hashes())
    plans = []
    for plan in _trial_plans(envelope):
        if plan.stratum == "THERMAL_NO_INPUT":
            plan = StratumTrialPlan(
                stratum="THERMAL_NO_INPUT", source=CAPTURED,
                trials=MINIMUM_WINDOWS_PER_STRATUM,
                chain_hashes=tuple(chains),
                per_visit=_spread(MINIMUM_WINDOWS_PER_STRATUM, positions))
        plans.append(plan)
    return tuple(plans)


def _spur_free_positions(plan):
    """The plan's visits split into spur-free and occupied, by the same
    in-span check the runner applies."""
    by_index = {t.tuning_index: t for t in plan.tunings}
    free, occupied = [], []
    for visit in plan.schedule:
        lo = (by_index[visit.tuning_index].center_frequency_hz
              + visit.retune_delta_hz)
        present = catalogued_spurs_in_span(plan.spur_allocation.catalogue,
                                           plan.tunings, lo)
        (occupied if present else free).append(visit.position)
    return free, occupied


def _visit_lo(plan, position):
    """The (LO, tuning_id) a visit actually sets."""
    by_index = {t.tuning_index: t for t in plan.tunings}
    visit = {v.position: v for v in plan.schedule}[position]
    tuning = by_index[visit.tuning_index]
    return (tuning.center_frequency_hz + visit.retune_delta_hz,
            tuning.tuning_id)


def _thermal_plan():
    """A plan that captures thermal, under a catalogue re-sloped so
    spur-free visits exist. VANISHES accepts any slope -- the load decides
    it -- so re-sloping the fixture's slope-0 products to 2.0 keeps every
    class supported while opening the free visits entry 10 needs."""
    envelope = _envelope()
    allocation = _spur_allocation(
        chains=sorted(envelope.admissible_chain_hashes()),
        slopes={VANISHES_ON_DECLARED_TERMINATION: 2.0})
    reference = _plan(envelope, spur_allocation=allocation)
    free, occupied = _spur_free_positions(reference)
    assert free and occupied, (len(free), len(occupied))
    plan = _plan(envelope, spur_allocation=allocation,
                 trial_plans=_thermal_captured(envelope, free))
    return plan, free, occupied


class ThermalVerdictTests(unittest.TestCase):
    def test_a_thermal_window_where_the_catalogue_puts_a_product_in_span_is_a_violation(self):
        plan, _free, occupied = _thermal_plan()
        lo, tuning_id = _visit_lo(plan, occupied[0])
        window = CapturedWindow(window_id="w-occupied-001",
                                stratum="THERMAL_NO_INPUT",
                                lo_hz=lo, tuning_id=tuning_id)
        report = run_catalogue_windows(plan=plan, windows=(window,))
        (verdict,) = report.verdicts
        self.assertEqual(verdict.verdict, VERDICT_VIOLATION)
        self.assertEqual(verdict.refusal_code, PLAN_THERMAL_NOT_SPUR_FREE)
        self.assertTrue(verdict.in_span)
        self.assertIn("THERMAL_NO_INPUT", verdict.detail)
        self.assertIn("RECEIVER_SPURS", verdict.detail)

    def test_a_thermal_window_where_the_catalogue_puts_nothing_in_span_is_clean(self):
        plan, free, _occupied = _thermal_plan()
        lo, tuning_id = _visit_lo(plan, free[0])
        window = CapturedWindow(window_id="w-free-001",
                                stratum="THERMAL_NO_INPUT",
                                lo_hz=lo, tuning_id=tuning_id)
        report = run_catalogue_windows(plan=plan, windows=(window,))
        (verdict,) = report.verdicts
        self.assertEqual(verdict.verdict, VERDICT_CLEAN)
        self.assertEqual(verdict.in_span, ())
        self.assertIsNone(verdict.refusal_code)

    def test_the_violation_names_the_products_in_span(self):
        plan, _free, occupied = _thermal_plan()
        lo, tuning_id = _visit_lo(plan, occupied[0])
        expected = catalogued_spurs_in_span(plan.spur_allocation.catalogue,
                                            plan.tunings, lo)
        self.assertTrue(expected)
        window = CapturedWindow(window_id="w-occupied-002",
                                stratum="THERMAL_NO_INPUT",
                                lo_hz=lo, tuning_id=tuning_id)
        report = run_catalogue_windows(plan=plan, windows=(window,))
        (verdict,) = report.verdicts
        self.assertEqual(verdict.in_span, expected)


class SpurVerdictTests(unittest.TestCase):
    def _restricted_plan(self):
        """A plan whose eligible units name every tuning but the last, and
        whose RECEIVER_SPURS visits are drawn only from the eligible ones,
        so the plan constructs and the runner has a tuning to catch."""
        envelope = _envelope()
        chains = sorted(envelope.admissible_chain_hashes())
        tunings = generate_tunings(seed=SEED, bands=_bands())
        catalogue = _catalogue(45)
        short = _eligible_trials(catalogue, chains, 3,
                                 MINIMUM_WINDOWS_PER_STRATUM + 139,
                                 tunings=tunings[:-1])
        allocation = _spur_allocation(chains=chains, epochs=3,
                                      eligible=short)
        excluded = tunings[-1].tuning_id
        self.assertNotIn(excluded, allocation.eligible_tuning_ids())
        visits = generate_visit_schedule(seed=SEED, tunings=tunings)
        eligible_positions = [v.position for v in visits
                              if v.tuning_index != tunings[-1].tuning_index]
        trial_plans = []
        for key in STRATUM_KEYS:
            if key in TUNER_REQUIRED:
                positions = (eligible_positions if key == "RECEIVER_SPURS"
                             else [v.position for v in visits])
                trial_plans.append(StratumTrialPlan(
                    stratum=key, source=CAPTURED,
                    trials=MINIMUM_WINDOWS_PER_STRATUM,
                    chain_hashes=tuple(chains),
                    per_visit=_spread(MINIMUM_WINDOWS_PER_STRATUM,
                                      positions)))
            else:
                trial_plans.append(StratumTrialPlan(
                    stratum=key, source=SYNTHETIC,
                    trials=MINIMUM_WINDOWS_PER_STRATUM,
                    chain_hashes=(), per_visit=()))
        plan = _plan(envelope, spur_allocation=allocation,
                     trial_plans=tuple(trial_plans))
        return plan, excluded

    def _centre(self, plan, tuning_id):
        by_id = {t.tuning_id: t for t in plan.tunings}
        return by_id[tuning_id].center_frequency_hz

    def test_a_spur_window_at_an_eligible_tuning_is_clean(self):
        plan, _excluded = self._restricted_plan()
        tuning_id = sorted(plan.spur_allocation.eligible_tuning_ids())[0]
        window = CapturedWindow(window_id="w-spur-clean",
                                stratum="RECEIVER_SPURS",
                                lo_hz=self._centre(plan, tuning_id),
                                tuning_id=tuning_id)
        report = run_catalogue_windows(plan=plan, windows=(window,))
        (verdict,) = report.verdicts
        self.assertEqual(verdict.verdict, VERDICT_CLEAN)
        self.assertIsNone(verdict.refusal_code)

    def test_a_spur_window_where_no_eligible_unit_names_the_tuning_is_a_violation(self):
        plan, excluded = self._restricted_plan()
        window = CapturedWindow(window_id="w-spur-bad",
                                stratum="RECEIVER_SPURS",
                                lo_hz=self._centre(plan, excluded),
                                tuning_id=excluded)
        report = run_catalogue_windows(plan=plan, windows=(window,))
        (verdict,) = report.verdicts
        self.assertEqual(verdict.verdict, VERDICT_VIOLATION)
        self.assertEqual(verdict.refusal_code, PLAN_SPUR_NOT_IN_SPAN)
        self.assertIn(excluded, verdict.detail)


class OtherStrataTests(unittest.TestCase):
    def test_a_gain_step_window_gets_no_verdict_from_the_in_span_check(self):
        plan = _plan()
        tuning = plan.tunings[0]
        self.assertIn("GAIN_STEPS", plan.captured_strata())
        window = CapturedWindow(window_id="w-gain-001", stratum="GAIN_STEPS",
                                lo_hz=tuning.center_frequency_hz,
                                tuning_id=tuning.tuning_id)
        report = run_catalogue_windows(plan=plan, windows=(window,))
        (verdict,) = report.verdicts
        self.assertEqual(verdict.verdict, VERDICT_NOT_APPLICABLE)
        self.assertIsNone(verdict.refusal_code)


class InputValidationTests(unittest.TestCase):
    def _window(self, **kwargs):
        defaults = dict(window_id="w-001", stratum="THERMAL_NO_INPUT",
                        lo_hz=401_761_248.0, tuning_id="tuning-000")
        defaults.update(kwargs)
        return CapturedWindow(**defaults)

    def test_a_plan_that_is_not_a_plan_is_refused(self):
        with self.assertRaises(RunnerRefused) as caught:
            run_catalogue_windows(plan="not a plan",
                                  windows=(self._window(),))
        self.assertEqual(caught.exception.code, RUNNER_PLAN_NOT_DECLARED)

    def test_a_window_that_is_not_a_window_is_refused(self):
        plan, _, _ = _thermal_plan()
        with self.assertRaises(RunnerRefused) as caught:
            run_catalogue_windows(plan=plan, windows=("not a window",))
        self.assertEqual(caught.exception.code, RUNNER_WINDOW_NOT_DECLARED)

    def test_a_window_walked_twice_is_refused(self):
        plan, _, _ = _thermal_plan()
        with self.assertRaises(RunnerRefused) as caught:
            run_catalogue_windows(plan=plan,
                                  windows=(self._window(), self._window()))
        self.assertEqual(caught.exception.code, RUNNER_WINDOW_DECLARED_TWICE)

    def test_a_window_under_a_stratum_the_plan_does_not_capture_is_refused(self):
        # The default plan regenerates thermal; it captures nothing under it.
        plan = _plan()
        self.assertNotIn("THERMAL_NO_INPUT", plan.captured_strata())
        with self.assertRaises(RunnerRefused) as caught:
            run_catalogue_windows(plan=plan, windows=(self._window(),))
        self.assertEqual(caught.exception.code, RUNNER_STRATUM_NOT_PLANNED)

    def test_a_window_at_an_undeclared_tuning_is_refused(self):
        plan, _, _ = _thermal_plan()
        with self.assertRaises(RunnerRefused) as caught:
            run_catalogue_windows(
                plan=plan, windows=(self._window(tuning_id="tuning-999"),))
        self.assertEqual(caught.exception.code, RUNNER_TUNING_NOT_DECLARED)

    def test_a_window_whose_lo_is_not_at_its_tuning_is_refused(self):
        plan, _, _ = _thermal_plan()
        with self.assertRaises(RunnerRefused) as caught:
            run_catalogue_windows(
                plan=plan, windows=(self._window(lo_hz=100_000_000.0),))
        self.assertEqual(caught.exception.code, RUNNER_LO_NOT_AT_TUNING)

    def test_a_non_finite_lo_is_refused_at_construction(self):
        with self.assertRaises(RunnerRefused) as caught:
            self._window(lo_hz=float("nan"))
        self.assertEqual(caught.exception.code, RUNNER_QUANTITY_NOT_FINITE)


class ReportTests(unittest.TestCase):
    def _mixed_report(self):
        plan, free, occupied = _thermal_plan()
        lo_free, tuning_free = _visit_lo(plan, free[0])
        lo_occ, tuning_occ = _visit_lo(plan, occupied[0])
        windows = (
            CapturedWindow(window_id="w-free-001", stratum="THERMAL_NO_INPUT",
                           lo_hz=lo_free, tuning_id=tuning_free),
            CapturedWindow(window_id="w-occupied-001",
                           stratum="THERMAL_NO_INPUT",
                           lo_hz=lo_occ, tuning_id=tuning_occ),
        )
        return run_catalogue_windows(plan=plan, windows=windows)

    def test_the_report_counts_what_it_walked(self):
        report = self._mixed_report()
        self.assertEqual(report.counts(),
                         {VERDICT_CLEAN: 1, VERDICT_VIOLATION: 1,
                          VERDICT_NOT_APPLICABLE: 0})
        (violation,) = report.violations()
        self.assertEqual(violation.window_id, "w-occupied-001")

    def test_the_windows_digest_is_stable_and_hex(self):
        first = self._mixed_report().windows_digest
        second = self._mixed_report().windows_digest
        self.assertEqual(first, second)
        int(first, 16)
        self.assertEqual(len(first), 64)

    def test_assert_clean_raises_the_first_violation_with_the_plan_code(self):
        report = self._mixed_report()
        with self.assertRaises(RunnerRefused) as caught:
            assert_clean(report)
        self.assertEqual(caught.exception.code, PLAN_THERMAL_NOT_SPUR_FREE)
        self.assertIn("w-occupied-001", caught.exception.detail)

    def test_assert_clean_passes_a_clean_report(self):
        plan, free, _occupied = _thermal_plan()
        lo, tuning_id = _visit_lo(plan, free[0])
        report = run_catalogue_windows(
            plan=plan, windows=(CapturedWindow(
                window_id="w-free-001", stratum="THERMAL_NO_INPUT",
                lo_hz=lo, tuning_id=tuning_id),))
        self.assertIsNone(assert_clean(report))

    def test_assert_clean_refuses_a_report_that_is_not_a_report(self):
        with self.assertRaises(RunnerRefused) as caught:
            assert_clean("not a report")
        self.assertTrue(caught.exception.code.startswith("RUNNER_"))


if __name__ == "__main__":
    unittest.main()
