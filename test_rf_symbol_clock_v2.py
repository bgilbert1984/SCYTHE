"""The detrended shadow revision, against the inputs that broke v1.

v1 is frozen and pinned by ``test_rf_symbol_clock``.  These tests pin v2's claims
and, as importantly, its limits.  All inputs are synthetic; no number here is a
false-alarm probability.
"""

import unittest

import numpy as np

import test_rf_symbol_clock as fx
import rf_symbol_clock as v1
import rf_symbol_clock_v2 as v2
import rf_signal_family

CARRIER = fx.BandLimitedNoiseSpecificityTests.CARRIER


def _mask_noise(width_hz, mask, seed, count=524_288, rate_hz=2_048_000.0):
    rng = np.random.default_rng(seed)
    white = rng.normal(0, 1, count) + 1j * rng.normal(0, 1, count)
    freq = np.fft.fftfreq(count, 1.0 / rate_hz)
    if mask == "gaussian":
        sigma2 = (width_hz / 2.0) ** 2 / (2.0 * np.log(100.0))
        amp = np.exp(-(freq ** 2) / (4.0 * sigma2))
    else:
        amp = (np.abs(freq) <= width_hz / 2.0).astype(float)
    shaped = np.fft.ifft(np.fft.fft(white) * amp)
    shaped /= np.sqrt(np.mean(np.abs(shaped) ** 2))
    shaped += 0.01 * (rng.normal(0, 1, count) + 1j * rng.normal(0, 1, count))
    return shaped.astype(np.complex64)


def _run(baseband):
    result, ring = fx._real_channelization((baseband * CARRIER).astype(np.complex64))
    assert result.product.outcome == "CHANNELIZED", result.product.reason
    return v1.detect(result, ring=ring), v2.detect(result, ring=ring)


class RevisionIdentityTests(unittest.TestCase):
    def test_v2_is_a_separate_unregistered_shadow_method(self):
        self.assertNotEqual(v2.METHOD_ID, v1.METHOD_ID)
        status = v2.detector_status()
        self.assertFalse(status["registered_in_signal_family"])
        self.assertTrue(status["shadow_mode"])
        self.assertIsNone(status["false_alarm_probability"])
        self.assertNotIn(v2.METHOD_ID, rf_signal_family.METHOD_REGISTRY)

    def test_v1_is_unchanged(self):
        self.assertEqual(v1.METHOD_REVISION, "squared-envelope-cyclic.v1")
        self.assertEqual(v1.DECISION_THRESHOLD, 2.5)


class ShapedNoiseSpecificityTests(unittest.TestCase):
    CELLS = [("raised", 30_000.0), ("raised", 50_000.0), ("gaussian", 40_000.0),
             ("gaussian", 60_000.0), ("brick", 40_000.0)]
    # Gaussian and brick-wall 30 kHz channelize to fewer than MINIMUM_SAMPLE_COUNT samples and
    # are INSUFFICIENT_WINDOW in production; they are not comparisons and are not listed.

    def _noise(self, mask, width, seed):
        if mask == "raised":
            return fx._band_limited_noise(width, seed=seed)
        return _mask_noise(width, mask, seed)

    def test_noise_v1_crosses_on_and_v2_does_not(self):
        v1_crossings = 0
        for mask, width in self.CELLS:
            for seed in (0, 3, 17):
                with self.subTest(mask=mask, width=width, seed=seed):
                    old, new = _run(self._noise(mask, width, seed))
                    v1_crossings += old.outcome == "SYMBOL_CLOCK_LIKE_FEATURE"
                    self.assertEqual(new.outcome, "NO_SYMBOL_CLOCK")
                    self.assertLess(new.detection_statistic, v2.DECISION_THRESHOLD - 1.0)
                    self.assertEqual(new.method_id, v2.METHOD_ID)
        # Guard against the comparison going vacuous: v1 must still be the thing that fails.
        self.assertGreater(v1_crossings, 0)


class RealClockRetentionTests(unittest.TestCase):
    def test_real_clocks_are_detected_far_above_threshold(self):
        for rate in (50_000.0, 100_000.0, 200_000.0):
            with self.subTest(rate=rate):
                base = fx._psk(rate, count=524_288, rate_hz=2_048_000.0, noise=0.02)
                _, new = _run(base)
                self.assertEqual(new.outcome, "SYMBOL_CLOCK_LIKE_FEATURE")
                self.assertGreater(new.detection_statistic, 4 * v2.DECISION_THRESHOLD)
                realised = 2_048_000.0 / round(2_048_000.0 / rate)
                self.assertLess(abs(new.symbol_rate_hz - realised), 5.0 * new.cyclic_resolution_hz)

    def test_a_real_clock_is_not_reported_at_the_search_floor(self):
        base = fx._psk(100_000.0, count=524_288, rate_hz=2_048_000.0, noise=0.02)
        _, new = _run(base)
        self.assertGreater((new.symbol_rate_hz - new.search_floor_hz) / new.cyclic_resolution_hz, 64.0)


class GatingAndShadowTests(unittest.TestCase):
    def test_constant_envelope_is_still_a_blind_spot_not_a_negative(self):
        # Asserted on the statistic: after channelization the same signal acquires
        # envelope variation (CV ~0.12) in v1 as well, so the verdict path is not
        # where this blind spot is guarded.
        statistic, _, _, cv, _ = v2.detrended_statistic(
            fx._psk(16_000.0, constant_envelope=True), fx.RATE)
        self.assertLess(cv, v1.CONSTANT_ENVELOPE_CV)
        self.assertIsNone(statistic)

    def test_measurement_channel_is_still_refused_a_verdict(self):
        base = fx._psk(100_000.0, count=524_288, rate_hz=2_048_000.0, noise=0.02)
        result, ring = fx._real_channelization((base * CARRIER).astype(np.complex64),
                                               purpose="MEASUREMENT_CHANNEL")
        self.assertEqual(v2.detect(result, ring=ring).outcome, "CHANNEL_PURPOSE_NOT_ELIGIBLE")

    def test_nothing_promotes(self):
        base = fx._psk(100_000.0, count=524_288, rate_hz=2_048_000.0, noise=0.02)
        _, new = _run(base)
        self.assertFalse(new.promotes)
        self.assertEqual(new.family_summary, "NOT_DERIVED")
        self.assertEqual(new.promotion_state, "SHADOW_NO_PROMOTION")
        self.assertFalse(new.raw_iq_exposed)

    def test_the_periodic_artefact_limit_is_not_claimed_solved(self):
        self.assertIn("PERIODIC_TRANSPORT_ARTEFACT", v1.KNOWN_FALSE_POSITIVE_MODES)
        self.assertIn("periodic transport artefact", v2.__doc__)


if __name__ == "__main__":
    unittest.main()
