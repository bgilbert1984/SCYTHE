import unittest
from unittest.mock import patch

from graphops_rf_ingest import ingest_measured_rf, validate_measured_rf_frame
from rf_bridge import RFObservationStore


class GraphOpsRFIngestTests(unittest.TestCase):
    def setUp(self):
        self.frame = {"sensor_id": "fixture", "sequence": 1, "timestamp": 100.0,
                      "center_frequency_hz": 900_000_000, "peak_frequency_hz": 900_000_000,
                      "sample_rate_hz": 2_400_000, "peak_dbfs": -40, "noise_floor_dbfs": -80}

    def test_accepts_bounded_summary_and_derives_observed_evidence(self):
        result = ingest_measured_rf(RFObservationStore(min_snr_db=12), self.frame)
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["evidenceClass"], "OBSERVED")
        self.assertFalse(result["rawIqAccepted"])

    def test_rejects_raw_iq_and_nonfinite_values(self):
        with self.assertRaisesRegex(ValueError, "unknown measured RF fields"):
            validate_measured_rf_frame({**self.frame, "iq": [0, 1]})
        with self.assertRaisesRegex(ValueError, "finite"):
            validate_measured_rf_frame({**self.frame, "peak_frequency_hz": float("nan")})


if __name__ == "__main__":
    unittest.main()


class ObservationOriginTests(unittest.TestCase):
    """A hand-entered frame must never be retained as an IQ-exporter measurement."""

    def test_api_frames_default_to_external_sensor_not_the_local_bridge(self):
        frame = validate_measured_rf_frame(_frame())
        self.assertEqual(frame["observation_origin"], "EXTERNAL_SENSOR")

    def test_operator_synthetic_origin_is_retained_as_the_observation_source(self):
        store = RFObservationStore(min_snr_db=1.0, cooldown_s=0.0)
        frame = validate_measured_rf_frame({**_frame(), "observation_origin": "operator_synthetic"})
        observation = store.ingest_frame(frame)
        self.assertEqual(observation["source"], "operator_synthetic")
        self.assertNotEqual(observation["source"], "sdrpp_iq_exporter")

    def test_a_family_claim_is_not_ingestible_over_the_observation_api(self):
        """A classification is computed beside the IQ, never asserted by a caller.

        A remote caller that could attach signal_classification would be
        supplying its own detection statistic and false-alarm probability, which
        is exactly the evidence the registry exists to own.
        """
        from graphops_rf_ingest import ALLOWED_FIELDS
        from rf_signal_family import CLASSIFICATION_TRUST
        self.assertNotIn("signal_classification", ALLOWED_FIELDS)
        for smuggled in ("signal_classification", "detection_statistic",
                         "estimated_false_alarm_probability", "symbol_rate_hz",
                         "source_window_hash", "signal_family"):
            with self.assertRaises(ValueError) as caught:
                validate_measured_rf_frame({**_frame(), smuggled: {"family": "DIGITAL"}})
            self.assertIn(smuggled, str(caught.exception))
        self.assertEqual(CLASSIFICATION_TRUST, "BRIDGE_LOCAL_DETECTOR_ONLY")

    def test_an_ingested_frame_is_recorded_as_never_classified(self):
        store = RFObservationStore(min_snr_db=1.0, cooldown_s=0.0)
        item = ingest_measured_rf(store, validate_measured_rf_frame(_frame()))
        observation = item.get("observation") or item
        self.assertEqual(observation["signal_family"], "UNCLASSIFIED")
        self.assertEqual(observation["classification_reason_code"], "NOT_ATTEMPTED")

    def test_an_unrecognised_origin_is_refused_rather_than_coerced(self):
        with self.assertRaisesRegex(ValueError, "observation_origin must be one of"):
            validate_measured_rf_frame({**_frame(), "observation_origin": "MEASURED_BY_HARDWARE"})


def _frame():
    return {"sensor_id": "NESDR-SMART-V5-14530058", "sequence": 1, "timestamp": 1000.0,
            "center_frequency_hz": 100e6, "peak_frequency_hz": 100e6,
            "sample_rate_hz": 2_048_000, "peak_dbfs": -30.0, "noise_floor_dbfs": -90.0}


class SpectrumEndpointTests(unittest.TestCase):
    """The published bin product must reach the browser whole."""

    def _frame(self, bins):
        return {"timestamp": 1000.0, "sequence": 3, "fft_size": 4096, "bin_count": len(bins),
                "sample_rate_hz": 2_048_000.0, "center_frequency_hz": 100e6,
                "min_frequency_hz": 99e6, "max_frequency_hz": 101e6,
                "peak_dbfs": -30.0, "noise_floor_dbfs": -90.0, "bins_dbfs": bins}

    def _get(self, bins, max_bins=512):
        from scythe_orchestrator import app
        bridge = unittest.mock.MagicMock()
        bridge.latest_frame.return_value = self._frame(bins)
        bridge.config.max_bins = max_bins
        with patch('scythe_orchestrator._graphops_directive_authorized', return_value=True), \
             patch('rf_bridge.get_rf_bridge', return_value=bridge):
            response = app.test_client().get(
                '/api/graphops/rf-spectrum/latest?include_bins=1')
        return response.get_json()

    def test_the_whole_published_512_bin_product_is_served(self):
        payload = self._get([-90.0] * 512)
        self.assertEqual(len(payload['spectrum']['bins_dbfs']), 512,
                         'a 512-bin product must not be silently halved to 256')
        self.assertNotIn('bins_truncated', payload['spectrum'])
        self.assertFalse(payload['raw_iq_exposed'])

    def test_bins_are_withheld_unless_explicitly_requested(self):
        from scythe_orchestrator import app
        bridge = unittest.mock.MagicMock()
        bridge.latest_frame.return_value = self._frame([-90.0] * 512)
        bridge.config.max_bins = 512
        with patch('scythe_orchestrator._graphops_directive_authorized', return_value=True), \
             patch('rf_bridge.get_rf_bridge', return_value=bridge):
            payload = app.test_client().get('/api/graphops/rf-spectrum/latest').get_json()
        self.assertNotIn('bins_dbfs', payload['spectrum'])

    def test_a_truncated_product_declares_that_its_axis_no_longer_spans_the_rate(self):
        payload = self._get([-90.0] * 900, max_bins=512)
        self.assertEqual(len(payload['spectrum']['bins_dbfs']), 512)
        self.assertTrue(payload['spectrum']['bins_truncated'])
        self.assertEqual(payload['spectrum']['bins_truncated_span'],
                         'FREQUENCY_AXIS_NO_LONGER_SPANS_SAMPLE_RATE')
