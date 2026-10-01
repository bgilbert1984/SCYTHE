"""The §5.21 feature-retention estimator, measured from synthesis.

The estimator's claim is a number -- the feature's excess in dB over the
tuning-local median -- so the tests check the number, not just the
verdict. A tone synthesised at a known amplitude has a known true excess
(the Hann window's coherent gain over the noise floor's median), and the
estimator must land near it in every repeat. The verdicts (qualifying,
persistent, the 7-of-8 boundary) are then read off the observation the
estimator returns, which is the same object the catalogue entry is built
from.
"""

import math
import unittest

import numpy as np

from rf_promotion_envelope import (
    PLAN_PERSISTENCE_MARGIN_DB,
    PLAN_REPEATS_NOT_OBSERVED,
    EnvelopeRefused,
    SpurPersistenceObservation,
)
from rf_spur_retention_estimator import (
    RETENTION_FEATURE_OUT_OF_SPAN,
    RETENTION_SAMPLE_NOT_FINITE,
    RETENTION_SAMPLE_RATE_NOT_POSITIVE,
    RETENTION_TUNING_NOT_DECLARED,
    RETENTION_WINDOW_LENGTH_MISMATCH,
    RETENTION_WINDOWS_NOT_DECLARED,
    RetentionRefused,
    measure_persistence,
)

SAMPLE_RATE_HZ = 240_000.0
WINDOW_SAMPLES = 16_384
# Exactly on a bin, so the calibration below is exact rather than
# leakage-blurred: f0 = k0 * fs / N.
TONE_BIN = 2_731
TONE_HZ = TONE_BIN * SAMPLE_RATE_HZ / WINDOW_SAMPLES
SEED = 0x5EED_2026


def _true_excess_db(amplitude: float, noise_sigma: float) -> float:
    """The excess a tone of the given amplitude earns over the noise floor's
    median: coherent Hann gain against the median of an exponential."""
    window = np.hanning(WINDOW_SAMPLES)
    coherent = float(window.sum())            # S1: the tone's voltage gain
    power_gain = float((window ** 2).sum())   # S2: the noise's power gain
    tone_power = (amplitude * coherent) ** 2
    median_noise = (noise_sigma ** 2) * power_gain * math.log(2.0)
    return 10.0 * math.log10(tone_power / median_noise)


def _amplitude_for_excess(excess_db: float, noise_sigma: float) -> float:
    window = np.hanning(WINDOW_SAMPLES)
    coherent = float(window.sum())
    power_gain = float((window ** 2).sum())
    median_noise = (noise_sigma ** 2) * power_gain * math.log(2.0)
    return math.sqrt(median_noise * 10.0 ** (excess_db / 10.0)) / coherent


def _window(rng, tone_db=None, noise_sigma=1.0):
    """One repeat: complex noise, plus the tone if asked, exactly on-bin."""
    noise = (rng.standard_normal(WINDOW_SAMPLES)
             + 1j * rng.standard_normal(WINDOW_SAMPLES)) * noise_sigma / math.sqrt(2.0)
    if tone_db is None:
        return noise
    amplitude = _amplitude_for_excess(tone_db, noise_sigma)
    n = np.arange(WINDOW_SAMPLES)
    return noise + amplitude * np.exp(2j * math.pi * TONE_BIN * n / WINDOW_SAMPLES)


def _measure(windows, feature_hz=TONE_HZ, tuning_id="tuning-000"):
    return measure_persistence(tuning_id=tuning_id, windows=windows,
                               sample_rate_hz=SAMPLE_RATE_HZ,
                               feature_baseband_hz=feature_hz)


class ExcessMeasurementTests(unittest.TestCase):
    def test_a_strong_tone_measures_near_its_true_excess_in_every_repeat(self):
        # 30 dB up the tone bin's own noise is negligible, so this is the
        # calibration: the estimator against the Hann window's known gain.
        rng = np.random.default_rng(SEED)
        windows = [_window(rng, tone_db=30.0) for _ in range(8)]
        observation = _measure(windows)
        self.assertIsInstance(observation, SpurPersistenceObservation)
        self.assertEqual(observation.tuning_id, "tuning-000")
        for excess in observation.repeat_excess_db:
            self.assertAlmostEqual(excess, 30.0, delta=1.5)
        self.assertEqual(observation.qualifying(), 8)
        self.assertTrue(observation.persistent())

    def test_a_tone_clear_of_the_margin_is_persistent_in_every_repeat(self):
        # Near the margin the per-repeat excess is noisy -- the tone bin
        # carries its own noise -- which is why the 7-of-8 rule exists. At
        # 20 dB true excess every repeat still clears 10 dB with room.
        rng = np.random.default_rng(SEED)
        windows = [_window(rng, tone_db=20.0) for _ in range(8)]
        observation = _measure(windows)
        for excess in observation.repeat_excess_db:
            self.assertGreaterEqual(excess, PLAN_PERSISTENCE_MARGIN_DB)
        self.assertTrue(observation.persistent())

    def test_a_weak_tone_never_reaches_the_margin(self):
        # A 5 dB feature is real -- its excesses scatter around 5 dB, some
        # repeats even below the floor -- but none reaches 10 dB, so the
        # verdict is not persistent. Presence is a measurement; persistence
        # is the verdict.
        rng = np.random.default_rng(SEED)
        windows = [_window(rng, tone_db=5.0) for _ in range(8)]
        observation = _measure(windows)
        for excess in observation.repeat_excess_db:
            self.assertLess(excess, PLAN_PERSISTENCE_MARGIN_DB)
        self.assertEqual(observation.qualifying(), 0)
        self.assertFalse(observation.persistent())

    def test_noise_alone_qualifies_nothing(self):
        # No feature: the excesses scatter around the floor and none reaches
        # the margin. The seed is the fixture; the value below is measured,
        # not assumed -- rerun the synthesis if the seed ever changes.
        rng = np.random.default_rng(SEED)
        windows = [_window(rng) for _ in range(8)]
        observation = _measure(windows)
        self.assertLess(max(observation.repeat_excess_db),
                        PLAN_PERSISTENCE_MARGIN_DB)
        self.assertEqual(observation.qualifying(), 0)
        self.assertFalse(observation.persistent())

    def test_seven_of_eight_persists_and_six_does_not(self):
        rng = np.random.default_rng(SEED)
        seven = [_window(rng, tone_db=20.0) for _ in range(7)]
        seven.append(_window(rng))
        self.assertTrue(_measure(seven).persistent())
        self.assertEqual(_measure(seven).qualifying(), 7)
        rng = np.random.default_rng(SEED)
        six = [_window(rng, tone_db=20.0) for _ in range(6)]
        six.extend(_window(rng) for _ in range(2))
        self.assertFalse(_measure(six).persistent())
        self.assertEqual(_measure(six).qualifying(), 6)


class InputValidationTests(unittest.TestCase):
    def _windows(self, count=8, tone_db=20.0):
        rng = np.random.default_rng(SEED)
        return [_window(rng, tone_db=tone_db) for _ in range(count)]

    def test_seven_repeats_are_refused_with_the_plan_code(self):
        # The observation owns its denominator; the estimator lets its
        # refusal through rather than inventing its own.
        with self.assertRaises(EnvelopeRefused) as caught:
            _measure(self._windows(count=7))
        self.assertEqual(caught.exception.code, PLAN_REPEATS_NOT_OBSERVED)

    def test_mismatched_window_lengths_are_refused(self):
        windows = self._windows()
        windows[3] = windows[3][: WINDOW_SAMPLES // 2]
        with self.assertRaises(RetentionRefused) as caught:
            _measure(windows)
        self.assertEqual(caught.exception.code, RETENTION_WINDOW_LENGTH_MISMATCH)

    def test_a_non_finite_sample_is_refused(self):
        windows = self._windows()
        broken = windows[0].copy()
        broken[100] = complex(float("inf"), 0.0)
        windows[0] = broken
        with self.assertRaises(RetentionRefused) as caught:
            _measure(windows)
        self.assertEqual(caught.exception.code, RETENTION_SAMPLE_NOT_FINITE)

    def test_a_non_positive_sample_rate_is_refused(self):
        with self.assertRaises(RetentionRefused) as caught:
            measure_persistence(tuning_id="tuning-000",
                                windows=self._windows(), sample_rate_hz=0.0,
                                feature_baseband_hz=TONE_HZ)
        self.assertEqual(caught.exception.code,
                         RETENTION_SAMPLE_RATE_NOT_POSITIVE)

    def test_a_feature_beyond_nyquist_is_refused(self):
        with self.assertRaises(RetentionRefused) as caught:
            _measure(self._windows(),
                     feature_hz=SAMPLE_RATE_HZ / 2 + 1.0)
        self.assertEqual(caught.exception.code, RETENTION_FEATURE_OUT_OF_SPAN)

    def test_an_unnamed_tuning_is_refused(self):
        with self.assertRaises(RetentionRefused) as caught:
            _measure(self._windows(), tuning_id="")
        self.assertEqual(caught.exception.code, RETENTION_TUNING_NOT_DECLARED)

    def test_one_window_is_not_a_visit_of_repeats(self):
        rng = np.random.default_rng(SEED)
        with self.assertRaises(RetentionRefused) as caught:
            _measure(_window(rng, tone_db=20.0))
        self.assertEqual(caught.exception.code, RETENTION_WINDOWS_NOT_DECLARED)


if __name__ == "__main__":
    unittest.main()
