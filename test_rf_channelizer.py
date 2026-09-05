"""Phase 1b: the channelizer refuses more carefully than it computes.

Two halves.  The DSP half checks that a channel comes out where it went in, at
the amplitude it went in at.  The larger half checks the refusals: every declared
outcome is reachable, provenance is re-checked immediately before processing, and
no failure mode degrades quietly into a plausible-looking product.
"""

import json
import pickle
import unittest

import numpy as np

from rf_iq_ring import BoundedIQRing, IQWindow, RawIQNotTransportable
from rf_channelizer import (
    CHANNEL_MARGIN, DC_NOTCH, FIR_DESIGN, FIR_TAPS, METHOD_REVISION, MIN_OVERSAMPLE,
    OUTCOMES, REFUSAL_OUTCOMES, SCHEMA, ChannelRequest, Channelization,
    ChannelizedProduct, channelize, channelizer_status, design_lowpass,
    estimate_occupied_bandwidth,
)


CAPTURE_CENTER_HZ = 100_000_000.0
SAMPLE_RATE_HZ = 1_024_000.0
WINDOW_SAMPLES = 262_144


def _noise(count, seed, scale=1.0):
    rng = np.random.default_rng(seed)
    return scale * (rng.standard_normal(count) + 1j * rng.standard_normal(count))


def _band(count, sample_rate, bandwidth_hz, seed=7):
    """A band-limited noise burst of a known width, centred at baseband zero."""
    raw = _noise(count, seed)
    frequencies = np.fft.fftfreq(count, 1.0 / sample_rate)
    spectrum = np.fft.fft(raw)
    spectrum[np.abs(frequencies) > bandwidth_hz / 2.0] = 0
    shaped = np.fft.ifft(spectrum)
    return shaped / np.abs(shaped).mean()


def _signal(offset_hz=200_000.0, bandwidth_hz=40_000.0, amplitude=0.5,
            count=WINDOW_SAMPLES, sample_rate=SAMPLE_RATE_HZ, noise=0.002, seed=7):
    t = np.arange(count) / sample_rate
    carrier = np.exp(2j * np.pi * offset_hz * t)
    body = (_band(count, sample_rate, bandwidth_hz, seed) if bandwidth_hz
            else np.ones(count, dtype=complex))
    return (amplitude * body * carrier + _noise(count, seed + 1, noise)).astype(np.complex64)


def _windowed(samples, *, chain="chain-a", sample_rate=SAMPLE_RATE_HZ):
    ring = BoundedIQRing(capacity_samples=samples.size, sample_rate_hz=sample_rate,
                         signal_chain_hash=chain)
    ring.append(samples, {"timestamp": 1000.0})
    return ring, ring.acquire_window(samples.size).window


def _request(target_hz=100_200_000.0, capture_center_hz=CAPTURE_CENTER_HZ, **kwargs):
    return ChannelRequest(capture_center_hz=capture_center_hz,
                          target_frequency_hz=target_hz, **kwargs)


class ChannelIsolationTests(unittest.TestCase):
    """The DSP half: a channel comes out where and how it went in."""

    def test_a_known_band_is_isolated_at_its_known_centre_and_width(self):
        ring, window = _windowed(_signal(offset_hz=200_000.0, bandwidth_hz=40_000.0))
        product = channelize(window, _request(), ring=ring).product
        self.assertEqual(product.outcome, "CHANNELIZED")
        self.assertAlmostEqual(product.candidate_center_hz, 100_200_000.0, delta=1_000.0)
        self.assertAlmostEqual(product.candidate_bandwidth_hz, 40_000.0, delta=4_000.0)
        self.assertAlmostEqual(product.channel_bandwidth_hz,
                               product.candidate_bandwidth_hz * CHANNEL_MARGIN, places=3)
        # No SNR here, and that is the correct answer rather than a missing one:
        # a 40 kHz signal in a 50 kHz channel leaves no room between the occupied
        # region and the FIR skirt for a noise reference the filter has not
        # touched. This assertion used to read `assertGreater(snr_db, 30.0)`,
        # which passed easily because the figure was inflated by the stopband.
        # See test_rf_channelizer_snr.py for the measurement itself.
        self.assertIsNone(product.snr_db)
        self.assertEqual(product.snr_reason_code, "INSUFFICIENT_CLEAN_REFERENCE_BINS")

    def test_tuning_offset_and_carrier_offset_are_two_different_quantities(self):
        """Reporting only their sum would hide selection error as signal."""
        ring, window = _windowed(_signal(offset_hz=203_000.0, bandwidth_hz=0.0))
        product = channelize(window, _request(target_hz=100_203_000.0), ring=ring).product
        self.assertEqual(product.outcome, "CHANNELIZED")
        # Where the DDC was pointed.
        self.assertAlmostEqual(product.tuning_offset_hz, 203_000.0, delta=1_500.0)
        # Where the carrier actually turned out to be, relative to that.
        self.assertAlmostEqual(product.frequency_offset_hz, 0.0, delta=500.0)
        self.assertNotEqual(product.tuning_offset_hz, product.frequency_offset_hz)

    def test_absolute_amplitude_survives_because_nothing_normalizes_it(self):
        """Per-window normalization would make every capture look equally strong."""
        amplitudes = {}
        for amplitude in (0.1, 0.5):
            ring, window = _windowed(_signal(bandwidth_hz=0.0, amplitude=amplitude,
                                             noise=0.0005))
            result = channelize(window, _request(), ring=ring)
            self.assertEqual(result.product.outcome, "CHANNELIZED")
            self.assertEqual(result.product.amplitude_normalization, "NONE")
            amplitudes[amplitude] = float(np.abs(result.samples).mean())
        self.assertAlmostEqual(amplitudes[0.5], 0.5, delta=0.02)
        self.assertAlmostEqual(amplitudes[0.5] / amplitudes[0.1], 5.0, delta=0.3)

    def test_the_fir_has_unity_dc_gain_and_a_declared_design(self):
        taps = design_lowpass(0.1)
        self.assertEqual(taps.size, FIR_TAPS)
        self.assertAlmostEqual(float(taps.sum()), 1.0, places=9)
        status = channelizer_status()
        self.assertEqual(status["fir_design"], FIR_DESIGN)
        self.assertEqual(status["method_revision"], METHOD_REVISION)
        self.assertEqual(status["amplitude_normalization"], "NONE")
        for bad in (0.0, 0.5, 0.9, -0.1):
            with self.assertRaises(ValueError):
                design_lowpass(bad)

    def test_filter_transients_are_discarded_not_counted_as_usable(self):
        ring, window = _windowed(_signal())
        product = channelize(window, _request(), ring=ring).product
        self.assertEqual(product.transient_samples_discarded, FIR_TAPS - 1)
        usable = window.sample_count - (FIR_TAPS - 1)
        self.assertEqual(product.sample_count, -(-usable // product.decimation))

    def test_the_occupancy_walk_measures_the_signal_not_the_periodogram_variance(self):
        """A raw periodogram crosses a 20 dB floor in the first spectral null."""
        samples = _signal(offset_hz=200_000.0, bandwidth_hz=40_000.0)
        centre, bandwidth, method = estimate_occupied_bandwidth(
            samples, SAMPLE_RATE_HZ, CAPTURE_CENTER_HZ, 100_200_000.0)
        self.assertIn("WELCH", method)
        self.assertAlmostEqual(bandwidth, 40_000.0, delta=4_000.0)
        self.assertAlmostEqual(centre, 100_200_000.0, delta=1_000.0)


class ProvenanceTests(unittest.TestCase):
    """The window must still be real at the instant it is used."""

    def test_the_window_is_verified_immediately_before_processing(self):
        ring, window = _windowed(_signal())
        ring.invalidate("RETUNE")
        result = channelize(window, _request(), ring=ring)
        self.assertEqual(result.product.outcome, "SOURCE_WINDOW_EXPIRED")
        self.assertIsNone(result.samples)
        self.assertFalse(result)

    def test_an_evicted_window_expires_even_though_the_caller_still_holds_it(self):
        ring, window = _windowed(_signal())
        ring.append(_signal(count=1024)[:1024])
        self.assertEqual(channelize(window, _request(), ring=ring).product.outcome,
                         "SOURCE_WINDOW_EXPIRED")

    def test_a_window_from_another_ring_is_a_forgery_not_an_expiry(self):
        """Collapsing the two would report a forged window as a timing problem."""
        _, window = _windowed(_signal())
        other_ring, _ = _windowed(_signal(seed=11))
        result = channelize(window, _request(), ring=other_ring)
        self.assertEqual(result.product.outcome, "SOURCE_WINDOW_UNVERIFIED")
        self.assertIn("FORGERY", result.product.reason)

    def test_an_epoch_mismatch_is_refused_even_when_the_digest_matches(self):
        ring, window = _windowed(_signal())
        result = channelize(window, _request(expected_configuration_epoch=99), ring=ring)
        self.assertEqual(result.product.outcome, "SOURCE_WINDOW_EXPIRED")
        # And the ring itself still considers the window perfectly valid.
        self.assertTrue(ring.verify_window(window.window_id, window.digest))

    def test_a_different_signal_chain_is_refused_as_incomparable(self):
        ring, window = _windowed(_signal(), chain="chain-a")
        result = channelize(window, _request(expected_signal_chain_hash="chain-b"), ring=ring)
        self.assertEqual(result.product.outcome, "SIGNAL_CHAIN_CHANGED")
        self.assertIn("DIFFERENT ANTENNAS", result.product.reason)

    def test_the_measured_sample_rate_comes_from_the_window_not_the_request(self):
        ring, window = _windowed(_signal(), sample_rate=SAMPLE_RATE_HZ)
        product = channelize(window, _request(), ring=ring).product
        self.assertEqual(product.sample_rate_hz, SAMPLE_RATE_HZ)
        self.assertEqual(product.sample_rate_hz, window.sample_rate_hz)
        self.assertNotIn("sample_rate_hz", ChannelRequest.__dataclass_fields__)

    def test_a_window_whose_timestamps_contradict_its_samples_is_refused(self):
        ring, window = _windowed(_signal())
        lying = IQWindow(
            window_id=window.window_id, configuration_epoch=window.configuration_epoch,
            start_time=window.start_time, end_time=window.start_time + 0.001,
            sample_count=window.sample_count, sample_rate_hz=window.sample_rate_hz,
            digest=window.digest, signal_chain_hash=window.signal_chain_hash,
            samples=window.samples)
        self.assertEqual(channelize(lying, _request(), ring=ring).product.outcome,
                         "TIMING_QUALITY_INSUFFICIENT")


class RefusalTests(unittest.TestCase):
    """Every declared outcome is reachable, and none degrades into a product."""

    def test_a_target_outside_the_capture_span_is_refused(self):
        ring, window = _windowed(_signal())
        for target in (CAPTURE_CENTER_HZ + SAMPLE_RATE_HZ, CAPTURE_CENTER_HZ - 1e6):
            result = channelize(window, _request(target_hz=target), ring=ring)
            self.assertEqual(result.product.outcome, "TARGET_OUTSIDE_CAPTURE_SPAN")
            self.assertIn("DID NOT SAMPLE", result.product.reason)

    def test_a_window_too_short_to_characterise_is_an_insufficient_window(self):
        ring, window = _windowed(_signal(count=256))
        self.assertEqual(channelize(window, _request(), ring=ring).product.outcome,
                         "INSUFFICIENT_WINDOW")

    def test_full_span_noise_leaves_the_bandwidth_unresolved(self):
        """The occupancy never closes, which is not the same as it being narrow."""
        ring, window = _windowed(_noise(WINDOW_SAMPLES, 3, 0.2).astype(np.complex64))
        result = channelize(window, _request(), ring=ring)
        self.assertEqual(result.product.outcome, "OCCUPIED_BANDWIDTH_UNRESOLVED")
        self.assertIsNone(result.product.channel_bandwidth_hz)
        self.assertIn("NO CHANNEL WIDTH IS DEFENSIBLE", result.product.reason)

    def test_a_channel_running_off_the_span_edge_is_refused_not_clipped(self):
        # 100 kHz wide, centred 57 kHz below the span edge: the candidate closes,
        # but the margin-widened channel runs past the edge.
        offset = 455_000.0
        ring, window = _windowed(_signal(offset_hz=offset, bandwidth_hz=100_000.0))
        result = channelize(window, _request(target_hz=CAPTURE_CENTER_HZ + offset), ring=ring)
        self.assertEqual(result.product.outcome, "CHANNEL_EDGE_TRUNCATED")
        # The selection that was rejected is still recorded, so the refusal is
        # auditable rather than merely negative.
        self.assertIsNotNone(result.product.channel_bandwidth_hz)
        self.assertIsNotNone(result.product.candidate_bandwidth_hz)

    def test_a_channel_over_the_capture_centre_is_refused_rather_than_notched(self):
        ring, window = _windowed(_signal(offset_hz=0.0, bandwidth_hz=20_000.0))
        result = channelize(window, _request(target_hz=CAPTURE_CENTER_HZ), ring=ring)
        self.assertEqual(result.product.outcome, "DC_CONTAMINATION")
        self.assertIn("NO NOTCH IS APPLIED", result.product.reason)
        self.assertEqual(channelizer_status()["dc_notch"], DC_NOTCH)
        self.assertEqual(DC_NOTCH, "NONE_DECLARED_NOT_APPLIED")

    def test_a_decimation_that_would_alias_the_channel_is_refused(self):
        ring, window = _windowed(_signal(bandwidth_hz=40_000.0))
        clean = channelize(window, _request(), ring=ring).product
        self.assertGreaterEqual(clean.output_sample_rate_hz,
                                clean.channel_bandwidth_hz * MIN_OVERSAMPLE)
        aliasing = int(SAMPLE_RATE_HZ // (clean.channel_bandwidth_hz)) + 1
        result = channelize(window, _request(decimation=aliasing), ring=ring)
        self.assertEqual(result.product.outcome, "ALIAS_RISK")
        self.assertIn("FOLD IN", result.product.reason)
        self.assertEqual(channelize(window, _request(decimation=0), ring=ring).product.outcome,
                         "ALIAS_RISK")

    def test_every_declared_outcome_has_a_description_and_none_is_decorative(self):
        self.assertIn("CHANNELIZED", OUTCOMES)
        self.assertNotIn("CHANNELIZED", REFUSAL_OUTCOMES)
        self.assertEqual(set(REFUSAL_OUTCOMES) | {"CHANNELIZED"}, set(OUTCOMES))
        for code, description in OUTCOMES.items():
            self.assertTrue(description.strip(), code)
            self.assertEqual(description, description.upper(), code)
        self.assertEqual(set(channelizer_status()["outcomes"]), set(OUTCOMES))


class TwoStageSelectionTests(unittest.TestCase):
    """The channelizer must not grade its own channel selection."""

    def test_the_coarse_candidate_and_the_final_channel_are_recorded_separately(self):
        ring, window = _windowed(_signal(bandwidth_hz=40_000.0))
        product = channelize(window, _request(), ring=ring).product
        self.assertIsNotNone(product.candidate_center_hz)
        self.assertIsNotNone(product.candidate_bandwidth_hz)
        self.assertIn("WALK", product.candidate_method)
        # The final channel is wider than the candidate by the declared margin,
        # so the two numbers can never be confused for one measurement.
        self.assertGreater(product.channel_bandwidth_hz, product.candidate_bandwidth_hz)

    def test_the_fit_declares_that_it_is_not_independent_of_the_selection(self):
        ring, window = _windowed(_signal(bandwidth_hz=40_000.0))
        product = channelize(window, _request(), ring=ring).product
        self.assertEqual(product.occupied_bandwidth_basis, "SAME_WINDOW_AS_SELECTION")
        self.assertNotEqual(product.occupied_bandwidth_basis, "INDEPENDENT_WINDOW")
        # Nothing in this module may promote its own estimate to independent.
        import rf_channelizer
        with open(rf_channelizer.__file__, encoding="utf-8") as handle:
            source = handle.read()
        self.assertEqual(source.count('"INDEPENDENT_WINDOW"'), 0)


class ProductIdentityTests(unittest.TestCase):
    """A derived product carries its own hash over its parameters and its source."""

    def _product(self, **kwargs):
        ring, window = _windowed(_signal(**kwargs))
        return channelize(window, _request(), ring=ring).product

    def test_the_digest_covers_the_source_window_and_the_channel_parameters(self):
        first, second = self._product(), self._product()
        self.assertEqual(first.product_digest, second.product_digest,
                         "identical inputs must produce an identical product digest")
        different = self._product(offset_hz=250_000.0)
        self.assertNotEqual(first.product_digest, different.product_digest)
        self.assertTrue(first.product_digest.startswith("blake2s:"))
        self.assertTrue(first.product_id.startswith("chp-"))

    def test_every_channel_parameter_moves_the_derived_product_hash(self):
        """A product hash that ignored a parameter would let two different cuts
        of the spectrum claim the same identity."""
        ring, window = _windowed(_signal())
        baseline = channelize(window, _request(), ring=ring).product.product_digest

        # Source identity: a different window, and a different signal chain.
        other_ring, other_window = _windowed(_signal(seed=21))
        self.assertNotEqual(
            baseline,
            channelize(other_window, _request(), ring=other_ring).product.product_digest,
            "a different source window must not reuse the digest")
        chain_ring, chain_window = _windowed(_signal(), chain="chain-b")
        self.assertNotEqual(
            baseline,
            channelize(chain_window, _request(), ring=chain_ring).product.product_digest,
            "products through different signal chains are not comparable")

        # Request parameters.
        self.assertNotEqual(
            baseline,
            channelize(window, _request(capture_center_hz=100_000_001.0),
                       ring=ring).product.product_digest)

        # Channel selection: a signal at a different frequency is a different
        # channel centre, and a wider one is a different channel bandwidth.
        moved_ring, moved_window = _windowed(_signal(offset_hz=260_000.0))
        self.assertNotEqual(
            baseline,
            channelize(moved_window, _request(target_hz=100_260_000.0),
                       ring=moved_ring).product.product_digest)
        wide_ring, wide_window = _windowed(_signal(bandwidth_hz=90_000.0))
        wide = channelize(wide_window, _request(), ring=wide_ring).product
        self.assertNotEqual(baseline, wide.product_digest)

        # Outcome: a refusal over the same window is its own identity.
        self.assertNotEqual(
            baseline,
            channelize(window, _request(target_hz=1e9), ring=ring).product.product_digest,
            "a refusal must not share a digest with a successful cut")

    def test_a_refusal_is_a_complete_product_rather_than_a_missing_one(self):
        ring, window = _windowed(_signal())
        product = channelize(window, _request(target_hz=1e9), ring=ring).product
        self.assertEqual(product.schema, SCHEMA)
        self.assertEqual(product.source_window_id, window.window_id)
        self.assertEqual(product.source_window_digest, window.digest)
        self.assertEqual(product.configuration_epoch, window.configuration_epoch)
        self.assertEqual(product.signal_chain_hash, window.signal_chain_hash)
        self.assertEqual(product.sample_count, 0)
        self.assertEqual(product.occupied_bandwidth_basis, "NOT_MEASURED")

    def test_the_product_binds_the_filter_it_actually_used(self):
        product = self._product()
        self.assertEqual(product.fir_design, FIR_DESIGN)
        self.assertEqual(product.fir_taps, FIR_TAPS)
        self.assertEqual(product.method_revision, METHOD_REVISION)


class BasebandContainmentTests(unittest.TestCase):
    """Channelized samples stay process-local; only measurements are published."""

    def _result(self):
        ring, window = _windowed(_signal(amplitude=0.5))
        return channelize(window, _request(), ring=ring)

    def test_the_product_is_json_serializable_and_holds_no_baseband(self):
        result = self._result()
        payload = result.product.to_dict()
        self.assertNotIn("samples", payload)
        self.assertNotIn("baseband", payload)
        self.assertFalse(payload["raw_iq_exposed"])
        json.dumps(payload)
        sample_named = [name for name in ChannelizedProduct.__dataclass_fields__
                        if "sample" in name and name != "sample_count"]
        self.assertEqual(
            sample_named,
            ["sample_rate_hz", "output_sample_rate_hz", "transient_samples_discarded",
             "output_samples_per_candidate_symbol", "output_samples_per_symbol_achieved"])
        # The list above is a tripwire for a field being added; this is the
        # actual claim. Every field whose name mentions samples must hold a
        # number, so a baseband array cannot arrive under a plausible name.
        for name in sample_named:
            value = getattr(result.product, name)
            self.assertIsInstance(value, (int, float, type(None)), name)

    def test_the_channelization_refuses_to_serialize(self):
        with self.assertRaises(RawIQNotTransportable):
            pickle.dumps(self._result())

    def test_the_repr_withholds_the_baseband_because_reprs_reach_logs(self):
        text = repr(self._result())
        self.assertIn("withheld", text)
        self.assertNotIn("0.5+", text)

    def test_the_consumer_receives_a_frozen_array(self):
        result = self._result()
        self.assertFalse(result.samples.flags.writeable)
        with self.assertRaises(ValueError):
            result.samples[0] = 1.0

    def test_the_status_declares_what_is_not_yet_wired(self):
        status = channelizer_status()
        self.assertEqual(status["state"], "IMPLEMENTED")
        # Wired to capture in Phase 1d. Nothing consumes the products, and that
        # is reported as its own claim rather than folded into the first.
        self.assertEqual(status["bridge_integration"], "INTEGRATED")
        self.assertEqual(status["detector_integration"], "NOT_IMPLEMENTED")
        self.assertFalse(status["raw_iq_exposed"])
        self.assertFalse(status["baseband_transportable"])

    def test_nothing_in_the_capture_path_imports_the_channelizer_yet(self):
        """The first channelizer commit does not touch rf_bridge."""
        import rf_bridge
        self.assertFalse(hasattr(rf_bridge, "channelize"))
        self.assertFalse(hasattr(rf_bridge, "BoundedIQRing"))


if __name__ == "__main__":
    unittest.main()
