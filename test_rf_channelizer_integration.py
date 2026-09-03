"""Phase 1d: the seam between the ring and the channelizer.

Phase 1b proved the transform.  Phase 1c proved the ownership.  What is left is
the join, and every question here is about the join rather than about signal
processing: which window the channelizer got, whether it was still the window it
was issued by the time it was used, and what happens to the bridge when the
answer is no.

The recurring shape of these tests is to interleave a lifecycle event between
acquisition and channelization.  That interval is the only place the guarantee
can fail, so it is the only place worth testing hard.
"""

import json
import logging
import unittest
from unittest.mock import patch

import numpy as np

import rf_iq_retention
from rf_bridge import IQFFTProcessor, RFBridgeConfig
from rf_channelizer import ChannelizedProduct
from rf_iq_retention import IQRetentionOwner

# These tests deliberately provoke channelizer failures; the traceback logging
# is the behaviour under test, not output worth reading.
logging.getLogger("scythe.rf.retention").setLevel(logging.CRITICAL)

RATE = 2_048_000.0
CENTRE = 100_000_000.0
TARGET = CENTRE + 400_000.0
# 8 ms rather than 256: a full window is filled in every test here, and the
# arithmetic is the same at either size.
WINDOW_MS = 8.0


def _owner(**kwargs):
    defaults = dict(sensor_id="NESDR-SMART-V5", sample_type="uint8",
                    sample_rate_hz=RATE, owns_capture=True, window_ms=WINDOW_MS)
    defaults.update(kwargs)
    return IQRetentionOwner(**defaults)


def _tone(count, offset_hz=400_000.0, noise=0.001, seed=7):
    """A clean off-centre carrier: enough for the channelizer to succeed."""
    rng = np.random.default_rng(seed)
    n = np.arange(count)
    signal = 0.5 * np.exp(2j * np.pi * offset_hz * n / RATE)
    signal = signal + noise * (rng.standard_normal(count) + 1j * rng.standard_normal(count))
    return signal.astype(np.complex64)


def _fill(owner, **kwargs):
    """Append exactly one window's worth of samples."""
    capacity = owner._capacity()
    owner.append(_tone(capacity, **kwargs), 1000.0)
    return capacity


class WindowIssuanceTests(unittest.TestCase):
    """The owner issues windows; the channelizer never reaches into the ring."""

    def test_a_partial_window_produces_no_product_at_all(self):
        owner = _owner()
        owner.append(_tone(1024), 1000.0)
        self.assertFalse(owner.window_ready())
        self.assertIsNone(owner.maybe_channelize(capture_center_hz=CENTRE,
                                                 target_frequency_hz=TARGET))
        self.assertEqual(owner.channelizer_block()["products_total"], 0)

    def test_a_complete_window_is_channelized_into_a_bounded_product(self):
        owner = _owner()
        _fill(owner)
        product = owner.maybe_channelize(capture_center_hz=CENTRE,
                                         target_frequency_hz=TARGET)
        self.assertIsInstance(product, ChannelizedProduct)
        self.assertEqual(product.outcome, "CHANNELIZED")
        self.assertFalse(product.raw_iq_exposed)

    def test_exactly_one_product_comes_from_each_source_window(self):
        """WINDOW_OVERLAP is NONE, so a second call cannot re-cut the same span."""
        owner = _owner()
        _fill(owner)
        first = owner.maybe_channelize(capture_center_hz=CENTRE, target_frequency_hz=TARGET)
        self.assertIsNotNone(first)
        for _ in range(5):
            self.assertIsNone(owner.maybe_channelize(capture_center_hz=CENTRE,
                                                     target_frequency_hz=TARGET))
        _fill(owner)
        second = owner.maybe_channelize(capture_center_hz=CENTRE, target_frequency_hz=TARGET)
        self.assertIsNotNone(second)
        self.assertNotEqual(first.source_window_id, second.source_window_id)
        block = owner.channelizer_block()
        self.assertEqual(block["products_total"], 2)
        self.assertEqual(block["windows_issued"], 2)

    def test_a_product_count_would_otherwise_describe_the_polling_rate(self):
        """Fifty offers over one window's worth of samples still yield one product."""
        owner = _owner()
        capacity = owner._capacity()
        block = capacity // 50
        produced = 0
        for index in range(50):
            owner.append(_tone(block, seed=index), 1000.0)
            if owner.maybe_channelize(capture_center_hz=CENTRE, target_frequency_hz=TARGET):
                produced += 1
        self.assertLessEqual(produced, 1)

    def test_the_window_handed_over_is_a_copy_the_ring_cannot_change(self):
        owner = _owner()
        _fill(owner)
        window = owner.acquire_window().window
        before = np.array(window.samples[:16])
        owner.invalidate("RETUNE")          # zeroes the ring's buffer
        np.testing.assert_array_equal(window.samples[:16], before)
        with self.assertRaises(ValueError):
            window.samples[0] = 0           # and the consumer cannot write to it


class ProvenanceInterleavingTests(unittest.TestCase):
    """Everything that can happen between acquisition and use, and does."""

    def test_a_retune_during_channelization_refuses_the_window(self):
        owner = _owner()
        _fill(owner)
        window = owner.acquire_window().window
        owner.invalidate("RETUNE")
        product = owner.channelize_window(window, capture_center_hz=CENTRE,
                                          target_frequency_hz=TARGET)
        self.assertEqual(product.outcome, "SOURCE_WINDOW_EXPIRED")
        self.assertEqual(product.sample_count, 0)

    def test_eviction_between_acquisition_and_verification_refuses_the_window(self):
        """The digest still matches; the samples behind it are gone."""
        owner = _owner()
        _fill(owner)
        window = owner.acquire_window().window
        _fill(owner, seed=11)               # overwrites every sample of it
        product = owner.channelize_window(window, capture_center_hz=CENTRE,
                                          target_frequency_hz=TARGET)
        self.assertEqual(product.outcome, "SOURCE_WINDOW_EXPIRED")

    def test_a_disconnect_with_a_partial_window_yields_nothing(self):
        owner = _owner()
        owner.append(_tone(owner._capacity() // 2), 1000.0)
        owner.invalidate("DISCONNECT")
        self.assertFalse(owner.window_ready())
        self.assertIsNone(owner.maybe_channelize(capture_center_hz=CENTRE,
                                                 target_frequency_hz=TARGET))
        self.assertEqual(owner.channelizer_block()["products_total"], 0)

    def test_samples_arriving_after_a_clear_do_not_complete_the_old_window(self):
        """Half a window, a disconnect, then half a window is not a window."""
        owner = _owner()
        half = owner._capacity() // 2
        owner.append(_tone(half), 1000.0)
        owner.invalidate("DISCONNECT")
        owner.append(_tone(half, seed=3), 1001.0)
        self.assertFalse(owner.window_ready())

    def test_no_product_ever_carries_an_epoch_the_ring_has_left(self):
        owner = _owner()
        products = []
        for index in range(3):
            _fill(owner, seed=index)
            products.append(owner.maybe_channelize(capture_center_hz=CENTRE,
                                                   target_frequency_hz=TARGET))
            owner.invalidate("RETUNE")
        self.assertTrue(all(p is not None for p in products))
        epochs = [p.configuration_epoch for p in products]
        self.assertEqual(epochs, sorted(epochs))
        self.assertEqual(len(set(epochs)), 3, "each product belongs to its own epoch")
        self.assertTrue(all(p.outcome == "CHANNELIZED" for p in products))

    def test_a_forged_window_is_refused_as_a_forgery_not_an_expiry(self):
        owner = _owner()
        _fill(owner)
        genuine = owner.acquire_window().window
        forged = type(genuine)(
            window_id="iqw-0-1-deadbeefcafe", configuration_epoch=genuine.configuration_epoch,
            start_time=genuine.start_time, end_time=genuine.end_time,
            sample_count=genuine.sample_count, sample_rate_hz=genuine.sample_rate_hz,
            digest=genuine.digest, signal_chain_hash=genuine.signal_chain_hash,
            samples=genuine.samples)
        product = owner.channelize_window(forged, capture_center_hz=CENTRE,
                                          target_frequency_hz=TARGET)
        self.assertEqual(product.outcome, "SOURCE_WINDOW_UNVERIFIED")


class FailureContainmentTests(unittest.TestCase):
    """Analysis may fail. Capture may not notice."""

    def test_a_channelizer_exception_is_counted_and_swallowed(self):
        owner = _owner()
        _fill(owner)
        with patch.object(rf_iq_retention, "channelize",
                          side_effect=RuntimeError("transform exploded")):
            self.assertIsNone(owner.maybe_channelize(capture_center_hz=CENTRE,
                                                     target_frequency_hz=TARGET))
        block = owner.channelizer_block()
        self.assertEqual(block["channelizer_errors"], 1)
        self.assertEqual(block["products_total"], 0)

    def test_fft_frames_keep_being_published_while_channelization_throws(self):
        """The bridge published spectra before the channelizer existed."""
        config = RFBridgeConfig(sample_type="uint8", sample_rate_hz=RATE,
                                center_frequency_hz=CENTRE, fft_size=1024,
                                frames_per_second=60.0)
        owner = _owner()
        processor = IQFFTProcessor(config, sample_sink=owner.append)
        payload = bytes(range(256)) * 64
        with patch.object(rf_iq_retention, "channelize",
                          side_effect=RuntimeError("transform exploded")):
            frames = []
            for _ in range(40):
                frames.extend(processor.feed(payload))
                owner.maybe_channelize(capture_center_hz=CENTRE, target_frequency_hz=TARGET)
        self.assertTrue(frames, "spectrum frames must survive a broken channelizer")
        self.assertTrue(all("bins_dbfs" in frame for frame in frames))

    def test_a_refusal_is_recorded_as_a_product_not_as_an_error(self):
        """A refusal is a verdict the channelizer reached, not a fault."""
        owner = _owner()
        _fill(owner)
        window = owner.acquire_window().window
        owner.invalidate("RETUNE")
        owner.channelize_window(window, capture_center_hz=CENTRE, target_frequency_hz=TARGET)
        block = owner.channelizer_block()
        self.assertEqual(block["channelizer_errors"], 0)
        self.assertEqual(block["products_by_outcome"], {"SOURCE_WINDOW_EXPIRED": 1})


class BasebandContainmentTests(unittest.TestCase):
    """Complex samples end at the process boundary, and before it at the API."""

    MARKER = 4321.0

    def _marked_owner(self):
        owner = _owner()
        capacity = owner._capacity()
        samples = _tone(capacity)
        samples[:64] = np.complex64(self.MARKER)
        owner.append(samples, 1000.0)
        return owner

    def test_no_channelized_sample_reaches_the_status_payload(self):
        owner = self._marked_owner()
        owner.maybe_channelize(capture_center_hz=CENTRE, target_frequency_hz=TARGET)
        payload = json.dumps(owner.status())
        self.assertNotIn("4321", payload)
        self.assertIn('"baseband_retained": false', payload)

    def test_the_product_has_no_field_that_could_hold_baseband(self):
        owner = self._marked_owner()
        product = owner.maybe_channelize(capture_center_hz=CENTRE, target_frequency_hz=TARGET)
        for name, value in product.to_dict().items():
            self.assertNotIsInstance(value, np.ndarray, f"{name} holds an array")
            self.assertNotIsInstance(value, complex, f"{name} holds a complex sample")

    def test_the_owner_keeps_no_reference_to_the_channelized_samples(self):
        owner = self._marked_owner()
        owner.maybe_channelize(capture_center_hz=CENTRE, target_frequency_hz=TARGET)
        # The Channelization that held the baseband was local to the call. What
        # survives on the owner is a bounded deque of dicts.
        for record in owner._products:
            self.assertIsInstance(record, dict)
            self.assertTrue(all(not isinstance(v, np.ndarray) for v in record.values()))

    def test_the_bounded_product_history_cannot_grow_without_limit(self):
        owner = _owner()
        for index in range(rf_iq_retention.MAX_TRACKED_PRODUCTS + 5):
            _fill(owner, seed=index)
            owner.maybe_channelize(capture_center_hz=CENTRE, target_frequency_hz=TARGET)
        self.assertEqual(len(owner._products), rf_iq_retention.MAX_TRACKED_PRODUCTS)
        self.assertEqual(owner.channelizer_block()["products_total"],
                         rf_iq_retention.MAX_TRACKED_PRODUCTS + 5)

    def test_the_status_declares_that_nothing_classifies_these_products(self):
        block = _owner().channelizer_block()
        self.assertEqual(block["state"], "INTEGRATED_NO_CLASSIFICATION")
        self.assertEqual(block["classification"], "NOT_DERIVED_FROM_PRODUCTS")
        self.assertIn("NO DETECTOR CONSUMES THEM", block["note"])


if __name__ == "__main__":
    unittest.main()
