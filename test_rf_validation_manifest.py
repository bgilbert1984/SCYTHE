"""Q4 is a bound, not a fraction, and this is what that costs in trials."""

import unittest

from rf_validation_manifest import (
    CONFIDENCE, MAX_FALSE_DIGITAL_RATE, MINIMUM_TRIALS_FOR_ZERO_FAILURES, STRATA,
    STRATUM_KEYS, TARGET_TOTAL_NULL_WINDOWS, clopper_pearson_upper, evaluate,
    manifest_status, wilson_upper,
)


class BoundTests(unittest.TestCase):

    def test_zero_failures_in_one_hundred_trials_is_not_evidence_of_a_low_rate(self):
        """The whole reason the gate is a bound: 0/100 looks perfect and is not."""
        bound = clopper_pearson_upper(0, 100)
        self.assertGreater(bound, MAX_FALSE_DIGITAL_RATE * 20)
        self.assertAlmostEqual(bound, 0.0295, places=3)

    def test_the_rule_of_three_is_reproduced_exactly(self):
        """~3/n for zero failures, so ~3000 trials to reach 0.001."""
        self.assertEqual(MINIMUM_TRIALS_FOR_ZERO_FAILURES, 3000)
        self.assertGreater(clopper_pearson_upper(0, 2_900), MAX_FALSE_DIGITAL_RATE)
        self.assertLessEqual(clopper_pearson_upper(0, 3_000), MAX_FALSE_DIGITAL_RATE)

    def test_the_bound_is_exact_rather_than_normal_approximate(self):
        """A Wald interval has zero width at zero failures; this must not."""
        self.assertGreater(clopper_pearson_upper(0, 10_000), 0.0)
        # Wilson is reported beside it and is less conservative, never instead.
        self.assertLess(wilson_upper(1, 10_000), clopper_pearson_upper(1, 10_000))

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

    def test_unbuildable_strata_block_promotion_and_say_why(self):
        """A corpus that labelled these would be generating its own labels."""
        report = evaluate({key: (60_000, 0) for key in STRATUM_KEYS})
        self.assertEqual(set(report["not_buildable"]),
                         {"GAIN_STEPS", "DROPPED_FRAMES_TIMING_GAPS"})
        self.assertFalse(report["promotes"])
        self.assertEqual(report["promotion_blocked_reason"], "STRATA_NOT_BUILDABLE")
        for entry in report["strata"]:
            if entry["state"] == "NOT_BUILDABLE":
                self.assertFalse(entry["passes"])
                self.assertIn("nothing calls", entry["blocked_by"])
                # Trials are not merely absent: they are refused, so a corpus
                # cannot accumulate against a stratum it cannot honestly label.
                self.assertEqual(entry["trials"], 0)

    def test_an_unbuildable_stratums_trials_do_not_enter_the_aggregate(self):
        report = evaluate({key: (1_000, 0) for key in STRATUM_KEYS})
        self.assertEqual(report["aggregate"]["trials"], 1_000 * 10)

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
        self.assertEqual(set(status["not_buildable"]),
                         {"GAIN_STEPS", "DROPPED_FRAMES_TIMING_GAPS"})
        self.assertEqual(len(status["strata"]), 12)

    def test_the_rule_is_the_bound_and_says_so(self):
        self.assertIn("UPPER_CONFIDENCE_BOUND", manifest_status()["rule"])


if __name__ == "__main__":
    unittest.main()
