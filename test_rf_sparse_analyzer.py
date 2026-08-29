import unittest

import numpy as np

from rf_sparse_analyzer import (
    RFSparseAnalyzer, SparseAnalyzerConfig, _null_outcome, _residual_excess_db,
    compact_model_context, recover_support,
)


def _spectrogram(frames=40, bins=64, peak_bins=None, sideband_hz=2.0, dt=0.1):
    spectrogram = np.full((frames, bins), -80.0, dtype=np.float32)
    if peak_bins is None:
        peak_bins = np.full(frames, 32)
    t = np.arange(frames) * dt
    for index, peak in enumerate(peak_bins):
        spectrogram[index, int(peak)] = -20.0
        spectrogram[index, max(0, int(peak) - 1)] = -35.0
        spectrogram[index, min(bins - 1, int(peak) + 1)] = -35.0
        spectrogram[index, min(bins - 1, int(peak) + 4)] = -40.0 + 8.0 * np.cos(2 * np.pi * sideband_hz * t[index])
    timestamps = 1_000.0 + t
    sequences = list(range(1, frames + 1))
    return spectrogram, timestamps, sequences


class RFSparseAnalyzerTests(unittest.TestCase):
    def test_stationary_carrier_is_derived_inference(self):
        spectrogram, timestamps, sequences = _spectrogram()
        window, supports = recover_support(
            spectrogram, center_frequency_hz=100_000_000, sample_rate_hz=2_048_000,
            fft_size=4096, timestamps=timestamps, sequences=sequences,
            sensor_id="NESDR-SMART-V5-14530058", compression_ratio=1.0,
            available_frames=40, dropped_frames=0, sampling_seed=7,
            max_support=3, max_candidates=4, mad_k=4.0,
        )
        self.assertEqual(window.schema, "scythe.rf-residual-window.v1")
        self.assertEqual(window.evidence_class, "DERIVED_INFERENCE")
        self.assertFalse(window.raw_iq_exposed)
        carrier = next(item for item in supports if item.atom_family == "stationary_carrier")
        self.assertAlmostEqual(carrier.parameters["carrier_hz"], 100_000_000, delta=80_000)
        self.assertNotIn("range", carrier.to_dict())
        self.assertEqual(carrier.evidence_class, "DERIVED_INFERENCE")

    def test_linear_drift_is_recovered_from_peak_track(self):
        peak_bins = np.linspace(16, 48, 40)
        spectrogram, timestamps, sequences = _spectrogram(peak_bins=peak_bins, sideband_hz=0.0)
        window, supports = recover_support(
            spectrogram, center_frequency_hz=100_000_000, sample_rate_hz=2_048_000,
            fft_size=4096, timestamps=timestamps, sequences=sequences,
            sensor_id="edge-a", compression_ratio=1.0, available_frames=40,
            dropped_frames=0, sampling_seed=7, max_support=3, max_candidates=2, mad_k=4.0,
        )
        drift = next(item for item in supports if item.atom_family == "linear_drift")
        self.assertGreater(abs(drift.parameters["drift_hz_per_second"]), 50_000)

    def test_periodic_amplitude_atom_is_selected(self):
        spectrogram, timestamps, sequences = _spectrogram(sideband_hz=2.0)
        window, supports = recover_support(
            spectrogram, center_frequency_hz=100_000_000, sample_rate_hz=2_048_000,
            fft_size=4096, timestamps=timestamps, sequences=sequences,
            sensor_id="edge-a", compression_ratio=1.0, available_frames=40,
            dropped_frames=0, sampling_seed=7, max_support=3, max_candidates=4, mad_k=3.0,
        )
        families = {item.atom_family for item in supports}
        self.assertIn("periodic_amplitude", families)
        self.assertNotIn("periodic_sideband", families)
        amplitude = next(item for item in supports if item.atom_family == "periodic_amplitude")
        self.assertAlmostEqual(amplitude.parameters["modulation_rate_hz"], 2.0, delta=0.5)
        self.assertNotIn("spacing_hz", amplitude.parameters)

    def test_periodic_amplitude_rejects_single_frame_impulse(self):
        spectrogram, timestamps, sequences = _spectrogram(sideband_hz=0.0)
        spectrogram[:, 36] = -80.0
        spectrogram[12, 36] = -20.0
        window, supports = recover_support(
            spectrogram, center_frequency_hz=100_000_000, sample_rate_hz=2_048_000,
            fft_size=4096, timestamps=timestamps, sequences=sequences,
            sensor_id="edge-a", compression_ratio=1.0, available_frames=40,
            dropped_frames=0, sampling_seed=7, max_support=3, max_candidates=4, mad_k=3.0,
        )
        self.assertNotIn("periodic_amplitude", {item.atom_family for item in supports})
        impulse = next(region for region in window.candidate_regions if region["bin_index"] == 36)
        self.assertGreaterEqual(impulse["snr_db"], 6.0)
        self.assertLess(impulse["persistence"], 0.35)

    def test_periodic_amplitude_rejects_low_snr_pattern(self):
        spectrogram, timestamps, sequences = _spectrogram(sideband_hz=0.0)
        t = np.arange(40) * 0.1
        spectrogram[:, 36] = -77.0 + 2.0 * np.cos(2 * np.pi * 2.0 * t)
        window, supports = recover_support(
            spectrogram, center_frequency_hz=100_000_000, sample_rate_hz=2_048_000,
            fft_size=4096, timestamps=timestamps, sequences=sequences,
            sensor_id="edge-a", compression_ratio=1.0, available_frames=40,
            dropped_frames=0, sampling_seed=7, max_support=3, max_candidates=4, mad_k=3.0,
        )
        self.assertNotIn("periodic_amplitude", {item.atom_family for item in supports})
        low_snr = next(region for region in window.candidate_regions if region["bin_index"] == 36)
        self.assertLess(low_snr["snr_db"], 6.0)

    def test_each_null_outcome_is_classified_directly(self):
        cases = (
            ("INSUFFICIENT_EVIDENCE", 4, 8, [], -180.0),
            ("NOISE_COMPATIBLE", 40, 8, [], -85.0),
            ("NO_SUPPORT", 40, 8, [12], -60.0),
        )
        for expected, retained, minimum, bins, energy_db in cases:
            with self.subTest(expected=expected):
                self.assertEqual(
                    _null_outcome(retained, minimum, bins, energy_db), expected,
                )

    def test_residual_energy_is_computed_as_linear_excess_power(self):
        spectrogram = np.array([[-80.0], [-70.0]], dtype=np.float64)
        background = np.array([-75.0], dtype=np.float64)
        observed_power = np.power(10.0, spectrogram / 10.0)
        background_power = np.power(10.0, background / 10.0)[None, :]
        expected = 10.0 * np.log10(np.maximum(observed_power - background_power, 0.0).mean())
        self.assertAlmostEqual(
            _residual_excess_db(spectrogram, background), expected, places=10,
        )

    def test_noise_only_window_emits_null_outcome(self):
        spectrogram = np.full((40, 64), -80.0, dtype=np.float32)
        timestamps = 1_000.0 + np.arange(40) * 0.1
        sequences = list(range(1, 41))
        window, supports = recover_support(
            spectrogram, center_frequency_hz=100_000_000, sample_rate_hz=2_048_000,
            fft_size=4096, timestamps=timestamps, sequences=sequences,
            sensor_id="edge-a", compression_ratio=1.0, available_frames=40,
            dropped_frames=0, sampling_seed=7, max_support=3, max_candidates=4, mad_k=4.0,
        )
        self.assertEqual(supports, [])
        self.assertEqual(window.outcome, "NOISE_COMPATIBLE")
        self.assertLessEqual(window.residual_energy_db, -70.0)
        self.assertIn("analysis_bin_width_hz", window.chain)
        self.assertAlmostEqual(window.chain["analysis_bin_width_hz"], 32_000.0, delta=1.0)
        self.assertAlmostEqual(window.chain["native_fft_bin_width_hz"], 500.0, delta=0.1)

    def test_compression_support_stability_and_noise_false_positive_rate(self):
        spectrogram, timestamps, sequences = _spectrogram()
        recovered_frequencies = []
        false_positives = 0
        trials = 0
        for ratio in (1.0, 0.75, 0.5, 0.25):
            retained = int(round(40 * ratio))
            for seed in (7, 11, 23, 47):
                with self.subTest(ratio=ratio, seed=seed):
                    rng = np.random.default_rng(seed)
                    keep = np.sort(rng.choice(40, size=retained, replace=False))
                    kept_sequences = [sequences[index] for index in keep]
                    window, supports = recover_support(
                        spectrogram[keep], center_frequency_hz=100_000_000,
                        sample_rate_hz=2_048_000, fft_size=4096,
                        timestamps=timestamps[keep], sequences=kept_sequences,
                        sensor_id="edge-a", compression_ratio=ratio, available_frames=40,
                        dropped_frames=0, sampling_seed=seed, max_support=3,
                        max_candidates=4, mad_k=4.0,
                    )
                    carrier = next(
                        item for item in supports if item.atom_family == "stationary_carrier"
                    )
                    self.assertEqual(window.outcome, "SUPPORT")
                    recovered_frequencies.append(carrier.parameters["carrier_hz"])

                    noise = rng.normal(-80.0, 1.5, size=(40, 64)).astype(np.float32)
                    _noise_window, noise_supports = recover_support(
                        noise[keep], center_frequency_hz=100_000_000,
                        sample_rate_hz=2_048_000, fft_size=4096,
                        timestamps=timestamps[keep], sequences=kept_sequences,
                        sensor_id="edge-a", compression_ratio=ratio, available_frames=40,
                        dropped_frames=0, sampling_seed=seed, max_support=3,
                        max_candidates=4, mad_k=4.0,
                    )
                    false_positives += bool(noise_supports)
                    trials += 1
        self.assertLessEqual(max(recovered_frequencies) - min(recovered_frequencies), 1.0)
        self.assertEqual(false_positives, 0, f"noise supports in {false_positives}/{trials} trials")

    def test_analyzer_emits_window_without_raw_bins_or_iq(self):
        analyzer = RFSparseAnalyzer(SparseAnalyzerConfig(min_frames=8, max_frames=16, window_seconds=10.0))
        spectrogram, timestamps, sequences = _spectrogram(frames=16)
        payload = None
        for index in range(16):
            payload = analyzer.ingest_frame({
                "timestamp": timestamps[index], "sequence": sequences[index],
                "sensor_id": "edge-a", "center_frequency_hz": 100_000_000,
                "sample_rate_hz": 2_048_000, "fft_size": 4096,
                "bins_dbfs": spectrogram[index].tolist(),
            })
        self.assertIsNotNone(payload)
        self.assertNotIn("bins_dbfs", payload["window"])
        self.assertFalse(payload["window"]["raw_iq_exposed"])
        self.assertEqual(payload["window"]["retained_frames"], 16)
        context = compact_model_context(payload["window"], payload["supports"])
        self.assertFalse(context["hardware_authority"])
        self.assertEqual(context["evidence_class"], "DERIVED_INFERENCE")

    def test_existing_peak_schema_still_rejects_sparse_fields(self):
        from graphops_rf_ingest import validate_measured_rf_frame
        with self.assertRaisesRegex(ValueError, "unknown measured RF fields"):
            validate_measured_rf_frame({
                "sensor_id": "edge-a", "sequence": 1, "timestamp": 1.0,
                "center_frequency_hz": 1e8, "peak_frequency_hz": 1e8,
                "sample_rate_hz": 2.048e6, "peak_dbfs": -20, "noise_floor_dbfs": -80,
                "atom_family": "stationary_carrier",
            })


if __name__ == "__main__":
    unittest.main()
