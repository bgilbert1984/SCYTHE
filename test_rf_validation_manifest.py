"""Q4 is a bound, not a fraction, and this is what that costs in trials."""

import unittest

from rf_validation_manifest import (
    CONFIDENCE, MAX_FALSE_DIGITAL_RATE, MINIMUM_TRIALS_FOR_ZERO_FAILURES,
    MINIMUM_TRIALS_FOR_ZERO_FAILURES_CORRECTED, PER_BOUND_ALPHA,
    PER_BOUND_CONFIDENCE, STRATA, STRATUM_KEYS, TARGET_TOTAL_NULL_WINDOWS,
    TESTED_BOUND_COUNT, clopper_pearson_upper, evaluate, freeze_promotion_corpus,
    manifest_status, wilson_upper,
)


class BoundTests(unittest.TestCase):

    def test_zero_failures_in_one_hundred_trials_is_not_evidence_of_a_low_rate(self):
        """The whole reason the gate is a bound: 0/100 looks perfect and is not."""
        bound = clopper_pearson_upper(0, 100, confidence=0.95)
        self.assertGreater(bound, MAX_FALSE_DIGITAL_RATE * 20)
        self.assertAlmostEqual(bound, 0.0295, places=3)

    def test_the_rule_of_three_is_reproduced_exactly_at_95_percent(self):
        """~3/n for zero failures, so ~3000 trials to reach 0.001 at 95%."""
        self.assertEqual(MINIMUM_TRIALS_FOR_ZERO_FAILURES, 3000)
        self.assertGreater(clopper_pearson_upper(0, 2_900, confidence=0.95),
                           MAX_FALSE_DIGITAL_RATE)
        self.assertLessEqual(clopper_pearson_upper(0, 3_000, confidence=0.95),
                             MAX_FALSE_DIGITAL_RATE)

    def test_the_family_correction_costs_trials_and_says_how_many(self):
        """Thirteen bounds at 95% do not give the family 95%, and it is not free."""
        self.assertEqual(TESTED_BOUND_COUNT, 13)
        self.assertAlmostEqual(PER_BOUND_ALPHA, 0.05 / 13, places=12)
        self.assertAlmostEqual(PER_BOUND_CONFIDENCE, 0.9961538, places=6)
        # Roughly -ln(alpha)/rate, and materially more than the 95% figure.
        self.assertEqual(MINIMUM_TRIALS_FOR_ZERO_FAILURES_CORRECTED, 5561)
        self.assertGreater(MINIMUM_TRIALS_FOR_ZERO_FAILURES_CORRECTED,
                           MINIMUM_TRIALS_FOR_ZERO_FAILURES)
        # The default bound is the corrected one, not the nominal 95%.
        self.assertGreater(clopper_pearson_upper(0, 3_000), MAX_FALSE_DIGITAL_RATE)
        self.assertLessEqual(clopper_pearson_upper(0, 5_561), MAX_FALSE_DIGITAL_RATE)

    def test_the_bound_is_exact_rather_than_normal_approximate(self):
        """A Wald interval has zero width at zero failures; this must not."""
        self.assertGreater(clopper_pearson_upper(0, 10_000), 0.0)

    def test_wilson_is_not_a_substitute_at_the_corrected_confidence(self):
        """The usual ordering holds at 95% and reverses at the family alpha.

        Wilson's normal approximation degrades in a far tail with a tiny observed
        rate, so at 99.6154% it sits above the exact bound throughout this gate's
        operating regime. Harmless in a number nobody gates on, and exactly why
        the gate is the exact bound.
        """
        for failures, trials in ((0, 10_000), (1, 10_000), (5, 10_000), (50, 10_000)):
            self.assertLess(wilson_upper(failures, trials, confidence=0.95),
                            clopper_pearson_upper(failures, trials, confidence=0.95))
            self.assertGreater(wilson_upper(failures, trials),
                               clopper_pearson_upper(failures, trials))

    def test_the_bound_rises_with_failures_and_falls_with_trials(self):
        self.assertLess(clopper_pearson_upper(0, 10_000), clopper_pearson_upper(1, 10_000))
        self.assertLess(clopper_pearson_upper(1, 20_000), clopper_pearson_upper(1, 10_000))
        self.assertEqual(clopper_pearson_upper(5, 5), 1.0)
        self.assertIsNone(clopper_pearson_upper(0, 0))


class StratificationTests(unittest.TestCase):

    def test_all_twelve_approved_strata_are_present(self):
        self.assertEqual(len(STRATA), 12)
        for key in ("THERMAL_NO_INPUT", "STATIONARY_ANALOGUE_FM", "AM",
                    "CONSTANT_ENVELOPE_DIGITAL", "ADJACENT_CHANNEL_INTERFERENCE",
                    "DC_CONTAMINATION", "GAIN_STEPS", "RETUNE_TRANSIENTS",
                    "DROPPED_FRAMES_TIMING_GAPS", "OVERLOADED_CLIPPED",
                    "RECEIVER_SPURS", "TWO_SIGNAL_COLLISIONS"):
            self.assertIn(key, STRATUM_KEYS)

    def test_thermal_noise_cannot_carry_a_failing_stratum(self):
        """The aggregate passes comfortably; one safety-critical stratum does not."""
        observations = {key: (1_000, 0) for key in STRATUM_KEYS}
        observations["THERMAL_NO_INPUT"] = (50_000, 0)
        observations["CONSTANT_ENVELOPE_DIGITAL"] = (1_000, 5)
        report = evaluate(observations)
        self.assertTrue(report["aggregate"]["passes"])
        self.assertIn("CONSTANT_ENVELOPE_DIGITAL", report["failing_strata"])
        self.assertFalse(report["promotes"])

    def test_a_stratum_below_its_own_minimum_does_not_pass_on_a_good_bound(self):
        observations = {key: (60_000, 0) for key in STRATUM_KEYS}
        observations["TWO_SIGNAL_COLLISIONS"] = (100, 0)
        report = evaluate(observations)
        entry = next(r for r in report["strata"] if r["stratum"] == "TWO_SIGNAL_COLLISIONS")
        self.assertEqual(entry["state"], "INSUFFICIENT_TRIALS")
        self.assertFalse(report["promotes"])

    def test_every_stratum_is_now_buildable(self):
        """GAIN_CHANGE and CLOCK_DISCONTINUITY are wired, so nothing is blocked."""
        report = evaluate({key: (60_000, 0) for key in STRATUM_KEYS})
        self.assertEqual(report["not_buildable"], [])
        self.assertEqual(report["failing_strata"], [])
        for stratum in STRATA:
            self.assertTrue(stratum.buildable, stratum.key)
            self.assertIsNone(stratum.blocked_by, stratum.key)

    def test_an_unbuildable_stratum_would_still_block_and_refuse_trials(self):
        """The mechanism stays, because DIRECT_SAMPLING_CHANGE is still unwired."""
        from rf_validation_manifest import Stratum, evaluate_stratum
        blocked = Stratum("HYPOTHETICAL", "not buildable", 100, safety_critical=True,
                          buildable=False, blocked_by="nothing calls it")
        entry = evaluate_stratum(blocked, 60_000, 0)
        self.assertEqual(entry["state"], "NOT_BUILDABLE")
        self.assertFalse(entry["passes"])
        # Trials are refused, not merely absent: a corpus cannot accumulate
        # against a stratum it could not honestly label.
        self.assertEqual(entry["trials"], 0)
        self.assertIsNone(entry["upper_bound_95"])

    def test_a_full_corpus_with_a_frozen_lock_promotes(self):
        """The whole gate, passing, so a failure elsewhere is not mistaken for it."""
        lock = freeze_promotion_corpus(
            corpus_id="phase3-a", method_revision="squared-envelope-cyclic.v1",
            decision_threshold=8.4, preprocessing_revision="passband-local-excess-power.v1")
        configuration = {"method_revision": "squared-envelope-cyclic.v1",
                         "decision_threshold": 8.4,
                         "preprocessing_revision": "passband-local-excess-power.v1"}
        report = evaluate({key: (10_000, 0) for key in STRATUM_KEYS},
                          lock=lock, configuration=configuration)
        self.assertEqual(report["corpus_state"], "FROZEN")
        self.assertTrue(report["promotes"])
        self.assertIsNone(report["promotion_blocked_reason"])

    def test_a_corpus_without_a_lock_never_promotes(self):
        """Development against these windows is fine; calling it validation is not."""
        report = evaluate({key: (10_000, 0) for key in STRATUM_KEYS})
        self.assertEqual(report["corpus_state"], "NO_LOCK_EXPLORATORY")
        self.assertFalse(report["promotes"])
        self.assertEqual(report["promotion_blocked_reason"], "NO_LOCK_EXPLORATORY")

    def test_tuning_the_threshold_after_opening_the_corpus_voids_promotion(self):
        """Otherwise repeated tuning turns validation into training."""
        lock = freeze_promotion_corpus(
            corpus_id="phase3-a", method_revision="squared-envelope-cyclic.v1",
            decision_threshold=8.4, preprocessing_revision="p.v1")
        observations = {key: (10_000, 0) for key in STRATUM_KEYS}
        tuned = {"method_revision": "squared-envelope-cyclic.v1",
                 "decision_threshold": 8.1, "preprocessing_revision": "p.v1"}
        report = evaluate(observations, lock=lock, configuration=tuned)
        self.assertFalse(report["promotes"])
        self.assertEqual(report["promotion_blocked_reason"],
                         "CONFIGURATION_CHANGED_AFTER_FREEZE")
        # And so does changing the preprocessing, not only the threshold.
        repro = dict(tuned, decision_threshold=8.4, preprocessing_revision="p.v2")
        self.assertEqual(evaluate(observations, lock=lock, configuration=repro)
                         ["promotion_blocked_reason"], "CONFIGURATION_CHANGED_AFTER_FREEZE")

    def test_the_aggregate_alone_does_not_promote(self):
        observations = {key: (60_000, 0) for key in STRATUM_KEYS}
        observations["RETUNE_TRANSIENTS"] = (60_000, 400)
        report = evaluate(observations)
        self.assertGreater(report["aggregate"]["upper_bound_95"], 0.0)
        self.assertIn("RETUNE_TRANSIENTS", report["failing_strata"])
        self.assertFalse(report["promotes"])

    def test_the_target_corpus_size_is_declared_and_enforced(self):
        self.assertEqual(TARGET_TOTAL_NULL_WINDOWS, 10_000)
        small = evaluate({key: (100, 0) for key in STRATUM_KEYS})
        self.assertFalse(small["aggregate"]["passes"])

    def test_unknown_or_impossible_observations_are_refused(self):
        with self.assertRaises(ValueError):
            evaluate({"NOT_A_STRATUM": (10, 0)})
        with self.assertRaises(ValueError):
            evaluate({"THERMAL_NO_INPUT": (10, 11)})


class DeclarationTests(unittest.TestCase):

    def test_the_manifest_declares_no_corpus_has_been_collected(self):
        status = manifest_status()
        self.assertEqual(status["state"], "DECLARED_NO_CORPUS_COLLECTED")
        self.assertEqual(status["max_false_digital_rate"], MAX_FALSE_DIGITAL_RATE)
        self.assertEqual(status["confidence"], CONFIDENCE)
        self.assertEqual(status["gate_estimator"], "CLOPPER_PEARSON_EXACT")
        self.assertEqual(status["not_buildable"], [])
        self.assertEqual(len(status["strata"]), 12)
        self.assertEqual(status["simultaneous_control"], "BONFERRONI")
        self.assertEqual(status["tested_bound_count"], 13)
        self.assertEqual(status["promotion_corpus"], "FROZEN_LOCK_REQUIRED_FOR_PROMOTION")

    def test_the_rule_is_the_bound_and_says_so(self):
        self.assertIn("UPPER_CONFIDENCE_BOUND", manifest_status()["rule"])


if __name__ == "__main__":
    unittest.main()
