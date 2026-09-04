"""The detector contract is frozen before the detector exists, and this checks it stayed that way."""

import unittest

import numpy as np

import test_rf_channelizer_snr as fixtures
from rf_channelizer import ChannelRequest, channelize
from rf_detector_contract import (
    ADMISSION_VALUE, COVARIATE_BLOCKS, PROHIBITED_INFERENCES, REQUIRED_CHANNEL_FIELDS,
    DetectorInputRefused, admits, contract_status, detector_input, qualified_snr_db,
    snr_stratum,
)
from rf_iq_ring import BoundedIQRing


def _product(snr_db, *, channel_bandwidth_hz=None, offset_hz=400_000.0):
    samples = fixtures._channel_with_snr(snr_db, count=524_288, rate_hz=2_048_000.0,
                                         bandwidth_hz=200_000.0, offset_hz=offset_hz)
    ring = BoundedIQRing(capacity_samples=524_288, sample_rate_hz=2_048_000.0,
                         signal_chain_hash=fixtures.CHAIN)
    ring.append(samples)
    acquisition = ring.acquire_window()
    request = ChannelRequest(capture_center_hz=100e6,
                             target_frequency_hz=100e6 + offset_hz,
                             expected_signal_chain_hash=fixtures.CHAIN,
                             expected_configuration_epoch=ring.configuration_epoch,
                             channel_bandwidth_hz=channel_bandwidth_hz)
    return channelize(acquisition.window, request, ring=ring).product.to_dict()


class AdmissionTests(unittest.TestCase):
    """One question decides admission, and it is not about signal strength."""

    def test_a_low_snr_channel_is_admitted_when_the_transformation_succeeded(self):
        """Cyclostationary methods work below where occupancy closes."""
        product = _product(-10.0, channel_bandwidth_hz=250_000.0)
        self.assertEqual(product["transformation"]["outcome"], ADMISSION_VALUE)
        # Neither covariate resolved, and the channel is still admissible.
        self.assertIsNone(product["occupancy"]["bandwidth_hz"])
        self.assertIsNone(product["snr"]["snr_db"])
        self.assertTrue(admits(product))
        view = detector_input(product)
        self.assertEqual(view["snr_stratum"], "SNR_UNRESOLVED")
        self.assertIsNone(view["qualified_snr_db"])

    def test_a_refused_transformation_is_not_admitted(self):
        product = _product(30.0, offset_hz=2_048_000.0)      # outside the span
        self.assertNotEqual(product["transformation"]["outcome"], ADMISSION_VALUE)
        self.assertFalse(admits(product))
        with self.assertRaises(DetectorInputRefused):
            detector_input(product)

    def test_admission_never_reads_the_snr_block(self):
        """A resolved and an unresolved SNR admit identically."""
        resolved = _product(30.0)
        unresolved = _product(-10.0, channel_bandwidth_hz=250_000.0)
        self.assertIsNotNone(resolved["snr"]["snr_db"])
        self.assertIsNone(unresolved["snr"]["snr_db"])
        self.assertTrue(admits(resolved))
        self.assertTrue(admits(unresolved))


class UnresolvedIsNotANumberTests(unittest.TestCase):
    """The ways a null quietly becomes a value, each refused by name."""

    def test_an_unresolved_snr_is_none_and_not_a_default(self):
        product = _product(-10.0, channel_bandwidth_hz=250_000.0)
        self.assertIsNone(qualified_snr_db(product))

    def test_a_refused_snr_is_not_reported_even_if_a_number_is_present(self):
        """A reason code outranks a value: a qualified SNR must be unqualified-free."""
        product = _product(30.0)
        self.assertIsNotNone(qualified_snr_db(product))
        product["snr"]["snr_reason_code"] = "OCCUPIED_POWER_NOT_ABOVE_NOISE"
        self.assertIsNone(qualified_snr_db(product))

    def test_unresolved_is_its_own_stratum_not_a_bucket_edge(self):
        self.assertEqual(snr_stratum(_product(-10.0, channel_bandwidth_hz=250_000.0)),
                         "SNR_UNRESOLVED")
        self.assertEqual(snr_stratum(_product(25.0)), "SNR_20_TO_30_DB")
        self.assertEqual(snr_stratum(_product(35.0)), "SNR_ABOVE_30_DB")

    def test_every_prohibited_inference_is_named_with_its_reason(self):
        for key in ("SNR_AS_ZERO", "SNR_AS_NEGATIVE_INFINITY", "SNR_AS_WEAK",
                    "SNR_AS_ADMISSION", "OCCUPANCY_AS_SYMBOL_RATE",
                    "TRANSFORMATION_AS_DETECTION"):
            self.assertIn(key, PROHIBITED_INFERENCES)
            self.assertGreater(len(PROHIBITED_INFERENCES[key]), 40)


class BoundedViewTests(unittest.TestCase):

    def test_the_view_carries_measurements_and_no_baseband(self):
        view = detector_input(_product(30.0))
        for field in REQUIRED_CHANNEL_FIELDS:
            self.assertIn(field, view)
        for block in COVARIATE_BLOCKS:
            self.assertIn(block, view)
        self.assertFalse(view["raw_iq_exposed"])
        for key, value in view.items():
            self.assertNotIsInstance(value, np.ndarray, key)

    def test_the_contract_declares_that_no_detector_exists(self):
        status = contract_status()
        self.assertEqual(status["state"], "FROZEN_NO_DETECTOR_IMPLEMENTED")
        self.assertFalse(status["detector_implemented"])
        self.assertFalse(status["baseband_transportable"])
        self.assertEqual(status["snr_role"],
                         "VALIDATION_COVARIATE_NOT_ADMISSION_AUTHORITY")
        self.assertEqual(status["decides"],
                         "REGISTERED_METHOD_STATISTIC_AGAINST_CALIBRATED_PFA")


if __name__ == "__main__":
    unittest.main()
