"""The channelizer's SNR is a measurement, and this is what it measures.

The first implementation estimated the noise floor as the median of every bin
outside the occupied region.  Most of those bins lie outside the channelizer's
own passband, where its FIR has already crushed them by ~90 dB, so the median
landed in the stopband and the published figure measured filter rejection rather
than signal.  On live hardware it reported 106.5 dB; against synthetic ground
truth it reported 108.7 dB for a true 20 dB channel.

These tests exist so that error cannot come back quietly.  They check the number
against known truth, check that the reference bins are taken from where the
filter is flat, and check that an SNR which cannot be defended is published as
``None`` with a reason rather than as a plausible-looking figure.
"""

import unittest

import numpy as np

from rf_iq_ring import BoundedIQRing
from rf_channelizer import (
    DC_GUARD_HZ, NOISE_REFERENCE_GUARD_BINS, NOISE_REFERENCE_MIN_PER_SIDE,
    NOISE_REFERENCE_MIN_TOTAL, PASSBAND_REFERENCE_FRACTION, SNR_BASIS,
    SNR_MEASUREMENT_REVISION, SNR_REASON_CODES, SUPERSEDED_SNR_BASES,
    _EMITTED_SNR_REASON_CODES, ChannelRequest, _estimate_snr, channelize,
    channelizer_status, welch_power_db,
)


CAPTURE_CENTER_HZ = 100_000_000.0
RATE_HZ = 2_048_000.0
# The capacity the bridge actually allocates. Reference-bin geometry depends on
# the Welch resolution, which depends on window length, so a sweep run at a
# convenient length would not describe the products this system emits.
PRODUCTION_SAMPLES = 524_288
CHAIN = "blake2s:test-chain"


def _channel_with_snr(true_snr_db, *, count, rate_hz, bandwidth_hz, offset_hz, seed=7):
    """Band-limited signal plus unit-power white noise at a known in-band SNR.

    The noise is white across the whole span, so the noise inside the signal's
    own bandwidth is ``bandwidth / rate`` of the total.  Scaling the signal by
    ``10**(snr/10) * bandwidth / rate`` therefore sets the in-channel SNR exactly,
    which is what the channelizer claims to report.
    """
    rng = np.random.default_rng(seed)
    noise = (rng.normal(0, 1, count) + 1j * rng.normal(0, 1, count)) / np.sqrt(2)
    bins = int(bandwidth_hz / rate_hz * count)
    spectrum = np.zeros(count, dtype=complex)
    index = (np.arange(-bins // 2, bins // 2) + int(offset_hz / rate_hz * count)) % count
    spectrum[index] = rng.normal(0, 1, bins) + 1j * rng.normal(0, 1, bins)
    signal = np.fft.ifft(spectrum)
    signal /= np.sqrt(np.mean(np.abs(signal) ** 2))
    signal *= np.sqrt(10 ** (true_snr_db / 10.0) * (bandwidth_hz / rate_hz))
    return (signal + noise).astype(np.complex64)


def _product(true_snr_db, *, bandwidth_hz=200_000.0, offset_hz=400_000.0,
             count=PRODUCTION_SAMPLES, rate_hz=RATE_HZ):
    samples = _channel_with_snr(true_snr_db, count=count, rate_hz=rate_hz,
                                bandwidth_hz=bandwidth_hz, offset_hz=offset_hz)
    ring = BoundedIQRing(capacity_samples=count, sample_rate_hz=rate_hz,
                         signal_chain_hash=CHAIN)
    ring.append(samples)
    acquisition = ring.acquire_window()
    request = ChannelRequest(capture_center_hz=CAPTURE_CENTER_HZ,
                             target_frequency_hz=CAPTURE_CENTER_HZ + offset_hz,
                             expected_signal_chain_hash=CHAIN,
                             expected_configuration_epoch=ring.configuration_epoch)
    return channelize(acquisition.window, request, ring=ring).product


def _isolated(true_snr_db, *, rate_hz=512_000.0, count=131_072, bandwidth_hz=100_000.0,
              channel_bandwidth_hz=None, seed=11):
    """The estimator alone, over a channel whose occupied region is known.

    The pipeline's coarse selection refuses below roughly 20 dB, so running only
    end-to-end would leave the estimator uncharacterised in exactly the region a
    detection threshold lives in.  Here the occupied bins are given rather than
    walked, so the estimator is the only thing under test.
    """
    samples = _channel_with_snr(true_snr_db, count=count, rate_hz=rate_hz,
                                bandwidth_hz=bandwidth_hz, offset_hz=0.0, seed=seed)
    power_db, segment = welch_power_db(samples)
    power = np.power(10.0, power_db / 10.0)
    bin_hz = rate_hz / segment
    frequencies = (np.arange(segment) - segment // 2) * bin_hz
    left = int(np.searchsorted(frequencies, -bandwidth_hz / 2.0))
    right = int(np.searchsorted(frequencies, bandwidth_hz / 2.0))
    return _estimate_snr(
        power, frequencies, bin_hz, left, right,
        channel_bandwidth_hz=channel_bandwidth_hz or rate_hz * PASSBAND_REFERENCE_FRACTION,
        dc_offset_hz=None)


class GroundTruthTests(unittest.TestCase):
    """The number is checked against a signal whose SNR is known by construction."""

    def test_the_stopband_error_does_not_return(self):
        """A true 20 dB channel once read 108.7 dB. It must now read about 20."""
        product = _product(20.0)
        self.assertEqual(product.outcome, "CHANNELIZED")
        self.assertIsNotNone(product.snr_db)
        self.assertLess(abs(product.snr_db - 20.0), 1.0)

    def test_the_end_to_end_sweep_tracks_truth_wherever_selection_succeeds(self):
        """Every level the coarse selection accepts is measured within 1 dB."""
        measured = {}
        for true_snr in (20.0, 25.0, 30.0, 40.0):
            product = _product(true_snr)
            self.assertEqual(product.outcome, "CHANNELIZED")
            self.assertIsNotNone(product.snr_db, f"{true_snr} dB went unmeasured")
            measured[true_snr] = product.snr_db
            self.assertLess(abs(product.snr_db - true_snr), 1.0)
        # Monotone, and it tracks the input rather than merely sitting in range.
        self.assertEqual(sorted(measured.values()), list(measured.values()))

    def test_the_estimator_holds_below_the_selection_gate(self):
        """From -10 dB up. The pipeline refuses down here; the estimator must not lie."""
        for true_snr in (-10.0, -5.0, 0.0, 5.0, 10.0, 15.0, 20.0, 30.0, 40.0):
            fields = _isolated(true_snr)
            self.assertIsNone(fields["snr_reason_code"], f"{true_snr} dB was refused")
            self.assertLess(abs(fields["snr_db"] - true_snr), 0.5,
                            f"{true_snr} dB measured as {fields['snr_db']}")

    def test_subtracting_in_band_noise_is_what_makes_low_snr_honest(self):
        """At 0 dB, half the occupied power is noise. Not subtracting it doubles the answer."""
        power_db, segment = welch_power_db(
            _channel_with_snr(0.0, count=131_072, rate_hz=512_000.0,
                              bandwidth_hz=100_000.0, offset_hz=0.0, seed=11))
        power = np.power(10.0, power_db / 10.0)
        bin_hz = 512_000.0 / segment
        frequencies = (np.arange(segment) - segment // 2) * bin_hz
        left = int(np.searchsorted(frequencies, -50_000.0))
        right = int(np.searchsorted(frequencies, 50_000.0))
        reference = ((np.abs(frequencies) <= 512_000.0 * PASSBAND_REFERENCE_FRACTION / 2.0)
                     & ((frequencies < frequencies[left] - NOISE_REFERENCE_GUARD_BINS * bin_hz)
                        | (frequencies > frequencies[right] + NOISE_REFERENCE_GUARD_BINS * bin_hz)))
        floor = float(np.median(power[reference])) * (right - left + 1)
        without = 10.0 * np.log10(float(power[left:right + 1].sum()) / floor)

        with_subtraction = _isolated(0.0)["snr_db"]
        self.assertLess(abs(with_subtraction - 0.0), 0.5)
        # Total occupied power over the same floor counts the noise under the
        # signal as signal, and near a threshold that is the whole error.
        self.assertGreater(without - with_subtraction, 2.0)


class ReferenceGeometryTests(unittest.TestCase):
    """Where the floor is measured from is the whole of what went wrong before."""

    def test_reference_bins_are_declared_and_two_sided(self):
        product = _product(30.0)
        self.assertEqual(product.noise_reference_sides, "BOTH")
        self.assertIsNone(product.snr_quality)
        self.assertGreaterEqual(product.noise_reference_left_bins,
                                NOISE_REFERENCE_MIN_PER_SIDE)
        self.assertGreaterEqual(product.noise_reference_right_bins,
                                NOISE_REFERENCE_MIN_PER_SIDE)
        self.assertGreaterEqual(product.noise_reference_bin_count,
                                NOISE_REFERENCE_MIN_TOTAL)
        self.assertIsNotNone(product.noise_reference_bandwidth_hz)
        self.assertIsNotNone(product.noise_reference_side_disagreement_db)

    def test_no_reference_bin_lies_in_the_filter_skirt(self):
        """The bins used must sit where the declared FIR is still flat."""
        product = _product(30.0)
        usable = PASSBAND_REFERENCE_FRACTION * product.channel_bandwidth_hz / 2.0
        # Everything the estimator was allowed to see is inside the flat region,
        # so no reference bin can be attenuated noise pretending to be a floor.
        self.assertLess(usable, product.channel_bandwidth_hz / 2.0)
        self.assertLessEqual(product.noise_reference_bandwidth_hz,
                             2.0 * (usable - product.occupied_bandwidth_hz / 2.0) + 1.0)

    def test_a_geometry_without_room_publishes_none_and_a_reason(self):
        """A narrow channel leaves no gap between the signal and the skirt."""
        product = _product(30.0, bandwidth_hz=40_000.0, offset_hz=200_000.0,
                           count=262_144, rate_hz=1_024_000.0)
        self.assertEqual(product.outcome, "CHANNELIZED")
        self.assertIsNone(product.snr_db)
        self.assertEqual(product.snr_reason_code, "INSUFFICIENT_CLEAN_REFERENCE_BINS")
        # The counts that failed the budget are still published, so the refusal
        # can be understood rather than merely observed.
        self.assertIsNotNone(product.noise_reference_left_bins)
        self.assertIsNotNone(product.noise_reference_right_bins)
        self.assertLess(product.noise_reference_left_bins + product.noise_reference_right_bins,
                        NOISE_REFERENCE_MIN_TOTAL)

    def test_an_unresolved_snr_does_not_refuse_the_channelization(self):
        """Measurement failure is not transformation failure."""
        product = _product(30.0, bandwidth_hz=40_000.0, offset_hz=200_000.0,
                           count=262_144, rate_hz=1_024_000.0)
        self.assertEqual(product.outcome, "CHANNELIZED")
        self.assertEqual(product.reason_code, "CHANNELIZED")
        self.assertIsNotNone(product.channel_bandwidth_hz)
        self.assertIsNotNone(product.occupied_bandwidth_hz)
        self.assertGreater(product.sample_count, 0)

    def test_one_sided_estimation_is_never_silent(self):
        """It must clear the full budget on one side and say that it did."""
        # A signal sitting hard against the upper edge of the flat passband, so
        # only the lower side has room for a reference. This is the case the
        # declaration exists for: the estimate is still possible, but it is no
        # longer symmetric and must not pretend otherwise.
        rate_hz, count, bandwidth_hz = 512_000.0, 131_072, 100_000.0
        channel_bandwidth_hz = rate_hz * PASSBAND_REFERENCE_FRACTION
        usable = PASSBAND_REFERENCE_FRACTION * channel_bandwidth_hz / 2.0
        samples = _channel_with_snr(20.0, count=count, rate_hz=rate_hz,
                                    bandwidth_hz=bandwidth_hz,
                                    offset_hz=usable - bandwidth_hz / 2.0 - 2_000.0)
        power_db, segment = welch_power_db(samples)
        power = np.power(10.0, power_db / 10.0)
        bin_hz = rate_hz / segment
        frequencies = (np.arange(segment) - segment // 2) * bin_hz
        left = int(np.searchsorted(frequencies, usable - bandwidth_hz - 4_000.0))
        right = int(np.searchsorted(frequencies, usable - bin_hz))
        fields = _estimate_snr(power, frequencies, bin_hz, left, right,
                               channel_bandwidth_hz=channel_bandwidth_hz,
                               dc_offset_hz=None)
        self.assertIn(fields["noise_reference_sides"], ("LEFT_ONLY", "RIGHT_ONLY"))
        self.assertEqual(fields["snr_quality"], "DEGRADED_ONE_SIDED")
        self.assertGreaterEqual(fields["noise_reference_bin_count"],
                                NOISE_REFERENCE_MIN_TOTAL)

    def test_the_dc_exclusion_removes_bins_rather_than_trusting_the_filter(self):
        rate_hz, count, bandwidth_hz = 512_000.0, 131_072, 100_000.0
        samples = _channel_with_snr(20.0, count=count, rate_hz=rate_hz,
                                    bandwidth_hz=bandwidth_hz, offset_hz=0.0)
        power_db, segment = welch_power_db(samples)
        power = np.power(10.0, power_db / 10.0)
        bin_hz = rate_hz / segment
        frequencies = (np.arange(segment) - segment // 2) * bin_hz
        left = int(np.searchsorted(frequencies, -bandwidth_hz / 2.0))
        right = int(np.searchsorted(frequencies, bandwidth_hz / 2.0))
        kwargs = dict(channel_bandwidth_hz=rate_hz * PASSBAND_REFERENCE_FRACTION)
        clean = _estimate_snr(power, frequencies, bin_hz, left, right,
                              dc_offset_hz=None, **kwargs)
        excluded = _estimate_snr(power, frequencies, bin_hz, left, right,
                                 dc_offset_hz=100_000.0, **kwargs)
        self.assertLess(excluded["noise_reference_bin_count"],
                        clean["noise_reference_bin_count"])
        self.assertLess(excluded["noise_reference_right_bins"],
                        clean["noise_reference_right_bins"])


class DeclarationTests(unittest.TestCase):
    """A number without its definition is not a measurement."""

    def test_a_measured_snr_carries_its_basis_and_revision(self):
        product = _product(30.0)
        self.assertEqual(product.snr_basis, SNR_BASIS)
        self.assertEqual(product.snr_authority, "DERIVED_MEASUREMENT")
        self.assertEqual(product.snr_measurement_revision, SNR_MEASUREMENT_REVISION)
        self.assertEqual(product.noise_estimator, "MEDIAN_LINEAR_POWER")
        self.assertEqual(product.noise_reference_guard_bins, NOISE_REFERENCE_GUARD_BINS)
        self.assertIsNone(product.snr_reason_code)

    def test_a_refused_channelization_says_not_attempted_not_unresolved(self):
        """There is no channel to measure, which is not a measurement failure."""
        product = _product(30.0, offset_hz=RATE_HZ)     # outside the capture span
        self.assertEqual(product.outcome, "TARGET_OUTSIDE_CAPTURE_SPAN")
        self.assertIsNone(product.snr_db)
        self.assertIsNone(product.snr_reason_code)
        self.assertEqual(product.snr_basis, "NOT_MEASURED")
        self.assertEqual(product.noise_estimator, "NOT_MEASURED")
        self.assertEqual(product.noise_reference_sides, "NOT_ATTEMPTED")

    def test_the_measurement_revision_moves_the_product_digest(self):
        """Products under different SNR definitions must not pool silently."""
        import rf_channelizer

        first = _product(30.0).product_digest
        original = rf_channelizer.SNR_MEASUREMENT_REVISION
        try:
            rf_channelizer.SNR_MEASUREMENT_REVISION = "some-other-definition.v9"
            second = _product(30.0).product_digest
        finally:
            rf_channelizer.SNR_MEASUREMENT_REVISION = original
        self.assertNotEqual(first, second)

    def test_reserved_reason_codes_are_named_but_never_emitted(self):
        reserved = set(SNR_REASON_CODES) - set(_EMITTED_SNR_REASON_CODES)
        self.assertTrue(reserved)
        for code in reserved:
            self.assertIn("RESERVED", SNR_REASON_CODES[code])
        status = channelizer_status()
        self.assertEqual(set(status["snr_reason_codes_reserved"]), reserved)
        self.assertEqual(set(status["snr_reason_codes_emitted"]),
                         set(_EMITTED_SNR_REASON_CODES))

    def test_the_superseded_basis_is_declared_uncorrectable(self):
        """The old figure must never be rescaled into the new one."""
        self.assertIn("OCCUPIED_POWER_OVER_ALL_OUT_OF_BAND_MEDIAN", SUPERSEDED_SNR_BASES)
        note = SUPERSEDED_SNR_BASES["OCCUPIED_POWER_OVER_ALL_OUT_OF_BAND_MEDIAN"]
        self.assertIn("NOT COMPARABLE", note)
        self.assertIn("NO CORRECTION FACTOR EXISTS", note)
        self.assertNotEqual(SNR_BASIS, "OCCUPIED_POWER_OVER_ALL_OUT_OF_BAND_MEDIAN")

    def test_no_channelized_product_carries_baseband(self):
        """The measurement fields must not have become a way to smuggle samples."""
        payload = _product(30.0).to_dict()
        for key, value in payload.items():
            self.assertNotIsInstance(value, np.ndarray, key)
            if isinstance(value, (list, tuple)):
                self.assertLess(len(value), 32, key)


if __name__ == "__main__":
    unittest.main()
