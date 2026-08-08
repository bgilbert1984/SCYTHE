import unittest

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
