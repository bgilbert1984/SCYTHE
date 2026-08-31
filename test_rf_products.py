import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

from rf_products import declare_rf_products
from rf_spectrum_contract import analyze_spectrum_product, build_spectrum_frame, validate_spectrum_frame


class RFProductStatusTests(unittest.TestCase):
    def test_fft_and_sparse_freshness_are_independent_of_iq_connection(self):
        bridge = {
            "iq_connected": False,
            "latest_frame_at": 998.0,
            "capture_owner": "orchestrator",
            "config": {"sample_rate_hz": 2_048_000, "fft_size": 4096,
                       "max_bins": 512, "frames_per_second": 10},
        }
        sparse = {"latest_observed_end": 900.0, "latest_outcome": "NOISE_COMPATIBLE",
                  "dictionary_revision": "scythe.rf-sparse-dict.m1.v1"}
        declaration = declare_rf_products(bridge, sparse, now=1000.0)
        self.assertEqual(declaration["products"]["fft_frames"]["state"], "live")
        self.assertEqual(declaration["products"]["sparse_supports"]["state"], "stale")
        self.assertEqual(declaration["products"]["fft_frames"]["native_bin_width_hz"], 500.0)
        self.assertEqual(declaration["products"]["fft_frames"]["analysis_bin_width_hz"], 4000.0)
        self.assertEqual(declaration["raw_iq_scope"], "local_process_only")
        self.assertFalse(declaration["raw_iq_browser_exposed"])

    def test_absent_products_are_declared_stale(self):
        declaration = declare_rf_products({"config": {}}, None, now=1000.0)
        self.assertEqual(declaration["products"]["fft_frames"]["state"], "stale")
        self.assertEqual(declaration["products"]["sparse_supports"]["state"], "stale")


class SpectrumContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).parent / "scythe-web" / "SignalIntelligence" / "core.py"
        spec = importlib.util.spec_from_file_location("scythe_nerfengine_core", path)
        cls.core = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.core)

    def _product(self):
        return build_spectrum_frame({
            "timestamp": 1_788_159_000.125,
            "sequence": 42,
            "sensor_id": "NESDR-SMART-V5-14530058",
            "center_frequency_hz": 100_000_000,
            "sample_rate_hz": 2_048_000,
            "fft_size": 4096,
            "bins_dbfs": [-82.0] * 255 + [-35.0] + [-82.0] * 256,
        }, tuner_ppm=0.0, gain_db=29.7)

    def test_contract_is_bounded_and_contains_no_iq(self):
        product = self._product()
        self.assertEqual(product["schema"], "scythe.rf.spectrum.v1")
        self.assertEqual(len(product["power_db"]), 512)
        self.assertEqual(product["native_bin_width_hz"], 500.0)
        self.assertEqual(product["analysis_bin_width_hz"], 4000.0)
        self.assertFalse(any("iq" in key.lower() for key in product))
        with self.assertRaisesRegex(ValueError, "unknown spectrum fields"):
            validate_spectrum_frame({**product, "raw_iq": [0, 1]})

    def test_nerfengine_spectrum_path_does_not_repeat_fft(self):
        processor = self.core.SignalProcessor({"attention": {"enabled": False}})
        with patch.object(self.core.np.fft, "fft", side_effect=AssertionError("FFT repeated")):
            result = analyze_spectrum_product(processor, self._product())
        self.assertEqual(result["result"], "FEATURES_EXTRACTED")
        self.assertEqual(result["authority"], "experimental_inference")
        self.assertEqual(result["promotion"], "not_graph_evidence")
        self.assertGreater(result["features"]["peak_excess_db"], 40)

    def test_adapter_rejects_worker_evidence_promotion(self):
        class UnsafeWorker:
            def process_spectrum_frame(self, *_args, **_kwargs):
                return {"schema": "nerfengine.rf.analysis.v1",
                        "source_frame_id": _kwargs["frame_id"],
                        "authority": "observed", "promotion": "graph_evidence"}

        with self.assertRaisesRegex(ValueError, "crossed the graph-evidence boundary"):
            analyze_spectrum_product(UnsafeWorker(), self._product())


if __name__ == "__main__":
    unittest.main()
