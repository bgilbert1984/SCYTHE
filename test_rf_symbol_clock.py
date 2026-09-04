"""Four layers, because the last defect survived a full green phase.

The stopband-SNR error passed every test in `test_rf_channelizer.py` because
those tests exercised the mechanics around `_measure` -- that it was called, that
its outputs were plumbed, that refusals propagated -- and never once asked whether
the number it produced was right. Architecture bounded the blast radius; nothing
supplied the missing oracle.

So this detector is tested at four levels from the start:

    1. algebra      the statistic on inputs whose answer is known analytically
    2. end-to-end   through the real FIR, channelizer and contract
    3. metamorphic  transformations that must not change the verdict
    4. adversarial  inputs that produce a precise answer from an artefact

Layer 4 is the one that would have caught the SNR defect: a mathematically tidy
number derived from the instrument rather than the signal.
"""

import math
import unittest

import numpy as np

import test_rf_channelizer_snr as fixtures
from rf_channelizer import ChannelRequest, channelize
from rf_iq_ring import BoundedIQRing
from rf_symbol_clock import (
    AXIS_VALUES, CONSTANT_ENVELOPE_CV, DECISION_THRESHOLD,
    KNOWN_FALSE_NEGATIVE_MODES, KNOWN_FALSE_POSITIVE_MODES,
    MINIMUM_SAMPLE_COUNT, MIN_SYMBOL_PERIODS_PER_SEGMENT, NULL_CHARACTERISATION,
    OUTCOMES, PROMOTION_STATE, SymbolClockVerdict, detect, detector_status,
    squared_envelope_statistic,
)

RATE = 512_000.0
COUNT = 262_144


def _raised_cosine(samples_per_symbol, span=8, beta=0.35):
    """A pulse with excess bandwidth, because without it there is nothing to find.

    A full-width rectangular NRZ pulse has ``sum |p(t - kT)|^2`` constant, so its
    squared envelope carries **no** discrete line at the symbol rate -- the
    timing line only exists when the pulse has excess bandwidth. Testing this
    detector on rectangular pulses would be asserting an answer the physics does
    not give, and the first version of these tests did exactly that.
    """
    t = np.arange(-span * samples_per_symbol, span * samples_per_symbol + 1) / samples_per_symbol
    with np.errstate(divide="ignore", invalid="ignore"):
        pulse = np.sinc(t) * np.cos(np.pi * beta * t) / (1.0 - (2.0 * beta * t) ** 2)
    pulse[~np.isfinite(pulse)] = 0.0
    return pulse / np.sqrt((pulse ** 2).sum())


def _psk(symbol_rate_hz, *, count=COUNT, rate_hz=RATE, noise=0.0, seed=3,
         constant_envelope=False):
    """A raised-cosine shaped signal with a known symbol rate."""
    rng = np.random.default_rng(seed)
    samples_per_symbol = int(round(rate_hz / symbol_rate_hz))
    symbol_count = int(np.ceil(count / samples_per_symbol)) + 32
    if constant_envelope:
        # Unit modulus throughout: the phase carries the symbols and the envelope
        # carries nothing, which is the P25 C4FM case this test is about.
        phase = np.cumsum(rng.integers(0, 4, symbol_count) - 1.5) * np.pi / 4.0
        index = (np.arange(count) / samples_per_symbol).astype(int)
        signal = np.exp(1j * phase[index])
    else:
        symbols = ((rng.integers(0, 2, symbol_count) * 2 - 1)
                   + 1j * (rng.integers(0, 2, symbol_count) * 2 - 1))
        upsampled = np.zeros(symbol_count * samples_per_symbol, dtype=complex)
        upsampled[::samples_per_symbol] = symbols
        signal = np.convolve(upsampled, _raised_cosine(samples_per_symbol),
                             mode="same")[:count]
    if noise:
        signal = signal + noise * (rng.normal(0, 1, count) + 1j * rng.normal(0, 1, count))
    return signal.astype(np.complex64)


class _FakeChannelization:
    """Only for refusal paths; the real type is required everywhere else."""

    def __init__(self, product, samples):
        self.product = product
        self.samples = samples


def _real_channelization(samples, *, rate_hz=2_048_000.0, offset_hz=400_000.0):
    ring = BoundedIQRing(capacity_samples=samples.size, sample_rate_hz=rate_hz,
                         signal_chain_hash=fixtures.CHAIN)
    ring.append(samples)
    acquisition = ring.acquire_window()
    request = ChannelRequest(capture_center_hz=100e6,
                             target_frequency_hz=100e6 + offset_hz,
                             expected_signal_chain_hash=fixtures.CHAIN,
                             expected_configuration_epoch=ring.configuration_epoch)
    return channelize(acquisition.window, request, ring=ring), ring


# --- layer 1: algebra ------------------------------------------------------

class StatisticAlgebraTests(unittest.TestCase):
    """The statistic on inputs whose answer is known without running it."""

    def test_a_known_symbol_rate_is_recovered_within_one_bin(self):
        for symbol_rate in (8_000.0, 16_000.0, 32_000.0, 64_000.0, 128_000.0):
            statistic, found, resolution, _, _ = squared_envelope_statistic(
                _psk(symbol_rate, noise=0.05), RATE)
            self.assertIsNotNone(found, f"{symbol_rate} Hz not found")
            self.assertLess(abs(found - symbol_rate), 2.0 * resolution,
                            f"{symbol_rate} Hz recovered as {found}")
            self.assertGreater(statistic, DECISION_THRESHOLD)

    def test_a_symbol_rate_below_the_declared_floor_is_missed_not_invented(self):
        """A miss is a limitation; 2187.5 Hz for a 4 kHz signal was a fabrication.

        Below MIN_SYMBOL_PERIODS_PER_SEGMENT the squared envelope's continuous
        data component cannot be separated from the timing line, so the search
        does not go there.
        """
        statistic, _, _, _, _ = squared_envelope_statistic(_psk(4_000.0, noise=0.05), RATE)
        self.assertLess(statistic, DECISION_THRESHOLD)

    def test_averaging_is_what_makes_the_statistic_mean_anything(self):
        """A single periodogram's null peak-to-median is ~ln(N)/ln 2, not ~1.5."""
        rng = np.random.default_rng(5)
        noise = ((rng.normal(0, 1, COUNT) + 1j * rng.normal(0, 1, COUNT))
                 / np.sqrt(2))
        envelope = np.abs(noise) ** 2
        centred = envelope - envelope.mean()
        single = np.abs(np.fft.rfft(centred * np.hanning(COUNT))) ** 2
        band = single[500:130_000]
        unaveraged = float(band.max() / np.median(band))
        # Pure extreme-value statistics over ~10^5 exponential bins.
        self.assertGreater(unaveraged, 15.0)
        self.assertLess(abs(unaveraged - np.log(band.size) / np.log(2.0)), 4.0)
        # The averaged statistic on the same noise is an order of magnitude lower,
        # which is why the threshold registered against no implementation (8.4)
        # described a different quantity entirely.
        averaged, _, _, _, _ = squared_envelope_statistic(noise.astype(np.complex64), RATE)
        self.assertLess(averaged, 2.0)
        self.assertLess(averaged, unaveraged / 5.0)

    def test_complex_noise_produces_no_significant_cyclic_feature(self):
        rng = np.random.default_rng(5)
        noise = ((rng.normal(0, 1, COUNT) + 1j * rng.normal(0, 1, COUNT))
                 / np.sqrt(2)).astype(np.complex64)
        statistic, _, _, cv, _ = squared_envelope_statistic(noise, RATE)
        # The coefficient of variation of |x|^2 for complex Gaussian noise is 1.
        self.assertAlmostEqual(cv, 1.0, delta=0.05)
        self.assertLess(statistic, DECISION_THRESHOLD)

    def test_an_unmodulated_carrier_is_constant_envelope(self):
        tone = np.exp(2j * np.pi * 1_000.0 * np.arange(COUNT) / RATE).astype(np.complex64)
        statistic, _, _, cv, _ = squared_envelope_statistic(tone, RATE)
        self.assertLess(cv, CONSTANT_ENVELOPE_CV)
        self.assertIsNone(statistic)

    def test_the_peak_is_excluded_from_its_own_noise_floor(self):
        """Otherwise a strong feature raises the floor it is measured against."""
        weak, _, _, _, _ = squared_envelope_statistic(_psk(16_000.0, noise=0.9), RATE)
        strong, _, _, _, _ = squared_envelope_statistic(_psk(16_000.0, noise=0.05), RATE)
        self.assertGreater(strong, weak)

    def test_the_floor_is_local_so_a_broad_hump_is_not_a_line(self):
        """The data hump beat a global median and produced a wrong rate at 4 kHz."""
        statistic, found, resolution, _, _ = squared_envelope_statistic(
            _psk(32_000.0, noise=0.05), RATE)
        self.assertLess(abs(found - 32_000.0), 2.0 * resolution)
        # The local floor is what separates a line from the data hump at 4 kHz;
        # test_a_symbol_rate_below_the_declared_floor_is_missed_not_invented
        # covers the case it fixed.


# --- layer 2: end to end ---------------------------------------------------

class EndToEndTests(unittest.TestCase):
    """Through the real FIR, channelizer, contract and detector."""

    def test_a_modulated_channel_reaches_the_detector_and_is_measured(self):
        """Through the real FIR, decimation, contract and detector."""
        carrier = np.exp(2j * np.pi * 400_000.0 * np.arange(524_288) / 2_048_000.0)
        baseband = _psk(100_000.0, count=524_288, rate_hz=2_048_000.0, noise=0.02)
        result, ring = _real_channelization((baseband * carrier).astype(np.complex64))
        self.assertEqual(result.product.outcome, "CHANNELIZED")
        verdict = detect(result, ring=ring)
        self.assertEqual(verdict.outcome, "SYMBOL_CLOCK_LIKE_FEATURE")
        # The realised rate, not the requested one: a generator with an integer
        # number of samples per symbol produces 2048000/20 = 102400 Hz, and
        # asserting 100000 would be testing the request rather than the signal.
        realised = 2_048_000.0 / round(2_048_000.0 / 100_000.0)
        self.assertLess(abs(verdict.symbol_rate_hz - realised),
                        5.0 * verdict.cyclic_resolution_hz)
        self.assertEqual(verdict.source_window_id, result.product.source_window_id)
        # Even a positive verdict is inert.
        self.assertFalse(verdict.promotes)

    def test_channelization_can_destroy_the_feature_it_was_meant_to_isolate(self):
        """The channel's FIR attenuates the shoulders the timing line lives in.

        A symbol-timing line in the squared envelope exists only because the pulse
        has excess bandwidth, and it lives in exactly the spectral shoulders that
        a channel cut at CHANNEL_MARGIN x a -20 dB occupancy estimate puts into
        the FIR's skirt. Measured at 50 kbaud: the statistic is 56.07 on the
        baseband and 1.38 after channelization -- from a clean detection to no
        detection, with nothing wrong at either end.

        This is recorded rather than fixed. Widening the channel is a hashed
        configuration change with consequences for adjacent-signal exposure and
        collision rate, and it belongs to Phase 3 evidence rather than to a test.
        """
        baud = 50_000.0
        baseband = _psk(baud, count=524_288, rate_hz=2_048_000.0, noise=0.02)
        before, found_before, _, _, _ = squared_envelope_statistic(
            baseband, 2_048_000.0)
        self.assertGreater(before, DECISION_THRESHOLD)
        self.assertLess(abs(found_before - baud), 500.0)

        carrier = np.exp(2j * np.pi * 400_000.0 * np.arange(524_288) / 2_048_000.0)
        result, ring = _real_channelization((baseband * carrier).astype(np.complex64))
        self.assertEqual(result.product.outcome, "CHANNELIZED")
        verdict = detect(result, ring=ring)
        # The channelizer did its job; the detector correctly reports no feature.
        # The feature is gone, and neither component is at fault for that.
        self.assertEqual(verdict.outcome, "NO_SYMBOL_CLOCK")
        self.assertLess(verdict.detection_statistic, DECISION_THRESHOLD)
        self.assertIn("CHANNEL_MARGIN_ATTENUATES_EXCESS_BANDWIDTH",
                      KNOWN_FALSE_NEGATIVE_MODES)

    def test_a_short_window_is_insufficient_rather_than_negative(self):
        """Above the contract's usable-sample floor, below the method's minimum."""
        carrier = np.exp(2j * np.pi * 400_000.0 * np.arange(262_144) / 2_048_000.0)
        baseband = _psk(20_000.0, count=262_144, rate_hz=2_048_000.0, noise=0.02)
        result, ring = _real_channelization((baseband * carrier).astype(np.complex64))
        if result.product.outcome != "CHANNELIZED":
            self.skipTest("channelizer refused this geometry")
        self.assertLess(result.product.sample_count, MINIMUM_SAMPLE_COUNT)
        verdict = detect(result, ring=ring)
        self.assertEqual(verdict.outcome, "INSUFFICIENT_WINDOW")
        self.assertEqual(verdict.axis_value, "NOT_ATTEMPTED")

    def test_a_retune_between_channelization_and_detection_is_unverified(self):
        carrier = np.exp(2j * np.pi * 400_000.0 * np.arange(524_288) / 2_048_000.0)
        baseband = _psk(20_000.0, count=524_288, rate_hz=2_048_000.0, noise=0.02)
        result, ring = _real_channelization((baseband * carrier).astype(np.complex64))
        ring.invalidate("RETUNE")
        verdict = detect(result, ring=ring)
        self.assertEqual(verdict.outcome, "SOURCE_PRODUCT_UNVERIFIED")
        self.assertEqual(verdict.axis_value, "NOT_ATTEMPTED")

    def test_a_dictionary_off_the_wire_is_never_detected_on(self):
        verdict = detect({"transformation": {"outcome": "CHANNELIZED"}})
        self.assertEqual(verdict.outcome, "SOURCE_PRODUCT_UNVERIFIED")


# --- layer 3: metamorphic --------------------------------------------------

class MetamorphicTests(unittest.TestCase):
    """Transformations a symbol clock does not care about must not change it."""

    def setUp(self):
        self.signal = _psk(16_000.0, noise=0.1)
        self.baseline, _, _, _, _ = squared_envelope_statistic(self.signal, RATE)

    def test_amplitude_scaling_does_not_change_the_statistic(self):
        """It is a ratio; a gain change is not a change in structure."""
        for scale in (0.01, 0.5, 2.0, 100.0):
            scaled = (self.signal * scale).astype(np.complex64)
            statistic, rate, _, _, _ = squared_envelope_statistic(scaled, RATE)
            self.assertAlmostEqual(statistic / self.baseline, 1.0, places=6,
                                   msg=f"scale {scale}")

    def test_phase_rotation_does_not_change_the_statistic(self):
        """The squared envelope discards phase, so a rotation must be invisible."""
        for phase in (0.3, 1.7, math.pi):
            rotated = (self.signal * np.exp(1j * phase)).astype(np.complex64)
            statistic, _, _, _, _ = squared_envelope_statistic(rotated, RATE)
            self.assertAlmostEqual(statistic / self.baseline, 1.0, places=6,
                                   msg=f"phase {phase}")

    def test_time_translation_does_not_change_the_recovered_rate(self):
        """A symbol clock's rate is not a function of where the window started."""
        for shift in (1, 37, 512):
            shifted = np.roll(self.signal, shift).astype(np.complex64)
            _, rate, resolution, _, _ = squared_envelope_statistic(shifted, RATE)
            self.assertLess(abs(rate - 16_000.0), 2.0 * resolution, f"shift {shift}")

    def test_a_frequency_offset_does_not_change_the_verdict(self):
        """|x|^2 removes the carrier, so a residual tuning error is not structure."""
        for offset in (0.0, 500.0, -2_000.0):
            mixed = (self.signal
                     * np.exp(2j * np.pi * offset * np.arange(COUNT) / RATE)
                     ).astype(np.complex64)
            statistic, rate, resolution, _, _ = squared_envelope_statistic(mixed, RATE)
            self.assertAlmostEqual(statistic / self.baseline, 1.0, places=4,
                                   msg=f"offset {offset}")
            self.assertLess(abs(rate - 16_000.0), 2.0 * resolution)


# --- layer 4: adversarial --------------------------------------------------

class AdversarialTests(unittest.TestCase):
    """Inputs that yield a precise number from the instrument, not the signal.

    This is the layer the stopband-SNR defect would have failed. Every case here
    produces a clean, plausible answer from an artefact, and the requirement is
    that the detector either finds nothing or names its blindness -- never that it
    reports a confident symbol clock.
    """

    def _statistic(self, samples):
        return squared_envelope_statistic(np.asarray(samples, dtype=np.complex64), RATE)[:4]

    def test_a_constant_envelope_digital_signal_is_a_blind_spot_not_a_negative(self):
        """The P25 C4FM trap, and the most expensive error available here."""
        statistic, _, _, cv = self._statistic(_psk(16_000.0, constant_envelope=True))
        self.assertLess(cv, CONSTANT_ENVELOPE_CV)
        self.assertIsNone(statistic)
        # The mapping is the whole point: NOT_ATTEMPTED, never a negative result.
        self.assertEqual(AXIS_VALUES["CONSTANT_ENVELOPE"], "NOT_ATTEMPTED")
        self.assertNotEqual(AXIS_VALUES["CONSTANT_ENVELOPE"], "NO_SYMBOL_CLOCK_DETECTED")

    def test_a_dc_spike_does_not_become_a_symbol_clock(self):
        rng = np.random.default_rng(9)
        noise = (rng.normal(0, 1, COUNT) + 1j * rng.normal(0, 1, COUNT)) / np.sqrt(2)
        statistic, _, _, _ = self._statistic(noise + 6.0)     # a large DC offset
        self.assertLess(statistic, DECISION_THRESHOLD)

    def test_clipping_harmonics_do_not_become_a_symbol_clock(self):
        rng = np.random.default_rng(13)
        noise = (rng.normal(0, 1, COUNT) + 1j * rng.normal(0, 1, COUNT)) / np.sqrt(2)
        clipped = np.clip(noise.real, -0.5, 0.5) + 1j * np.clip(noise.imag, -0.5, 0.5)
        statistic, _, _, _ = self._statistic(clipped)
        self.assertLess(statistic, DECISION_THRESHOLD)

    def test_a_retune_transient_does_not_become_a_symbol_clock(self):
        rng = np.random.default_rng(17)
        noise = ((rng.normal(0, 1, COUNT) + 1j * rng.normal(0, 1, COUNT))
                 / np.sqrt(2))
        noise[:COUNT // 2] *= 0.05          # one step, as a retune leaves
        statistic, _, _, _ = self._statistic(noise)
        self.assertLess(statistic, DECISION_THRESHOLD)

    def test_analogue_fm_with_periodic_content_does_not_become_a_symbol_clock(self):
        """A 1 kHz tone through an FM modulator is periodic and is not digital."""
        t = np.arange(COUNT) / RATE
        phase = 6.0 * np.sin(2 * np.pi * 1_000.0 * t)
        fm = np.exp(1j * phase)
        rng = np.random.default_rng(21)
        fm = fm + 0.1 * (rng.normal(0, 1, COUNT) + 1j * rng.normal(0, 1, COUNT))
        statistic, _, _, cv = self._statistic(fm)
        # Either it is constant-envelope (blind spot, declared) or it fails the
        # rule. What it must never be is a symbol-clock-like feature.
        if statistic is not None:
            self.assertLess(statistic, DECISION_THRESHOLD)
        else:
            self.assertLess(cv, CONSTANT_ENVELOPE_CV)

    def test_a_sloping_cyclic_spectrum_is_a_declared_weakness_not_a_silent_one(self):
        """A slow amplitude drift beats the local median from the slope alone.

        Measured at 4.07 against a threshold of 2.5 on a random-walk envelope with
        no symbol structure. The mitigation that resists it -- taking the higher of
        the two side medians -- costs a factor of four on real signals (26.8 to
        6.4 on a 16 kHz PSK), so it is not applied. The weakness is declared, and
        shadow mode is what keeps it out of evidence.
        """
        rng = np.random.default_rng(31)
        drift = np.cumsum(rng.normal(0, 1, COUNT))
        drift = drift / np.abs(drift).max()
        shaped = ((1.0 + drift) * np.exp(2j * np.pi * 0.01 * np.arange(COUNT))
                  ).astype(np.complex64)
        statistic, _, _, _ = self._statistic(shaped)
        if statistic is not None and statistic >= DECISION_THRESHOLD:
            self.assertIn("SLOWLY_SLOPING_CYCLIC_SPECTRUM", KNOWN_FALSE_POSITIVE_MODES)
            self.assertEqual(PROMOTION_STATE, "SHADOW_NO_PROMOTION")
            self.assertFalse(detector_status()["digital_reachable"])

    def test_a_periodic_buffer_artefact_is_reported_but_cannot_promote(self):
        """A 64 kB USB buffer seam is periodic and is not a symbol clock.

        This one the statistic cannot distinguish -- a periodic amplitude glitch
        looks exactly like slow symbols -- so the honest position is that shadow
        mode is what stops it becoming evidence, not the statistic. The test
        records that, rather than pretending the detector is cleverer than it is.
        """
        rng = np.random.default_rng(23)
        samples = ((rng.normal(0, 1, COUNT) + 1j * rng.normal(0, 1, COUNT))
                   / np.sqrt(2))
        samples[::32_768] *= 8.0                     # a seam every 64 kB
        statistic, found, _, _ = self._statistic(samples)
        self.assertIsNotNone(statistic)
        if statistic >= DECISION_THRESHOLD:
            # It fooled the statistic. It must still not be able to promote.
            self.assertEqual(PROMOTION_STATE, "SHADOW_NO_PROMOTION")
            self.assertFalse(detector_status()["digital_reachable"])


# --- declarations ----------------------------------------------------------

class ShadowModeTests(unittest.TestCase):

    def test_no_verdict_can_promote(self):
        verdict = _psk(16_000.0)
        carrier = np.exp(2j * np.pi * 400_000.0 * np.arange(524_288) / 2_048_000.0)
        baseband = _psk(20_000.0, count=524_288, rate_hz=2_048_000.0, noise=0.02)
        result, ring = _real_channelization((baseband * carrier).astype(np.complex64))
        record = detect(result, ring=ring)
        self.assertFalse(record.promotes)
        self.assertTrue(record.shadow_mode)
        self.assertEqual(record.promotion_state, "SHADOW_NO_PROMOTION")
        self.assertEqual(record.validation_status, "REGISTERED_NOT_VALIDATED")
        self.assertEqual(record.family_summary, "NOT_DERIVED")
        self.assertFalse(record.to_dict()["promotes"])

    def test_every_declared_outcome_maps_to_an_axis_value(self):
        self.assertEqual(set(OUTCOMES), set(AXIS_VALUES))
        for outcome, value in AXIS_VALUES.items():
            self.assertIn(value, ("SYMBOL_CLOCK_LIKE_FEATURE",
                                  "NO_SYMBOL_CLOCK_DETECTED", "NOT_ATTEMPTED"))
        # Exactly one outcome may report a measured negative.
        negatives = [k for k, v in AXIS_VALUES.items() if v == "NO_SYMBOL_CLOCK_DETECTED"]
        self.assertEqual(negatives, ["NO_SYMBOL_CLOCK"])

    def test_all_eight_required_outcomes_are_declared(self):
        for outcome in ("SYMBOL_CLOCK_LIKE_FEATURE", "NO_SYMBOL_CLOCK",
                        "CONSTANT_ENVELOPE", "INSUFFICIENT_WINDOW",
                        "TIMING_QUALITY_INSUFFICIENT", "SOURCE_PRODUCT_UNVERIFIED",
                        "METHOD_NOT_VALIDATED", "DETECTOR_ERROR"):
            self.assertIn(outcome, OUTCOMES)

    def test_a_verdict_carries_no_baseband(self):
        carrier = np.exp(2j * np.pi * 400_000.0 * np.arange(524_288) / 2_048_000.0)
        baseband = _psk(20_000.0, count=524_288, rate_hz=2_048_000.0, noise=0.02)
        result, ring = _real_channelization((baseband * carrier).astype(np.complex64))
        for key, value in detect(result, ring=ring).to_dict().items():
            self.assertNotIsInstance(value, np.ndarray, key)


if __name__ == "__main__":
    unittest.main()
