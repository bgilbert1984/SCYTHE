"""Phase 1 acceptance gate for the bounded raw-IQ ring.

The ten conditions the operator set for this change to merge, one test class per
theme.  This is the first raw-IQ retention in SCYTHE, so the tests are about what
the buffer cannot do at least as much as what it can.
"""

import json
import os
import pickle
import unittest

import numpy as np

from rf_iq_ring import (
    BYTES_PER_SAMPLE, DEFAULT_CAPACITY_SAMPLES, DEFAULT_SAMPLE_RATE_HZ,
    DEFAULT_WINDOW_MS, INVALIDATION_REASONS, MAX_TRACKED_WINDOWS,
    NOMINAL_CYCLE_RESOLUTION_HZ, STORAGE_DTYPE, WINDOW_OVERLAP,
    BoundedIQRing, IQWindow, RawIQNotTransportable, RawIQRetentionRefused,
)


def _ring(capacity=1024, rate=1024.0, chain="chain-a"):
    return BoundedIQRing(capacity_samples=capacity, sample_rate_hz=rate,
                         signal_chain_hash=chain)


def _block(n, value=None, start=0):
    if value is not None:
        return np.full(n, value, dtype=STORAGE_DTYPE)
    return np.arange(start, start + n, dtype=STORAGE_DTYPE)


class ApprovedConfigurationTests(unittest.TestCase):
    """1. Capacity never exceeds 524,288 complex samples."""

    def test_the_default_allocation_is_exactly_the_approved_one(self):
        ring = BoundedIQRing()
        self.assertEqual(ring.capacity_samples, 524_288)
        self.assertEqual(ring.allocated_bytes, 4_194_304)
        self.assertEqual(DEFAULT_CAPACITY_SAMPLES * BYTES_PER_SAMPLE, 4_194_304)
        status = ring.status()
        self.assertEqual(status["storage_dtype"], "complex64")
        self.assertAlmostEqual(status["retention_ms"], DEFAULT_WINDOW_MS, places=6)
        self.assertAlmostEqual(status["cycle_resolution_hz"],
                               NOMINAL_CYCLE_RESOLUTION_HZ, places=6)
        self.assertAlmostEqual(DEFAULT_SAMPLE_RATE_HZ * 0.256, 524_288.0)

    def test_the_allocation_is_made_once_and_never_grows(self):
        ring = _ring(capacity=1024)
        allocated = ring.allocated_bytes
        buffer_id = id(ring._buffer)
        for _ in range(64):
            ring.append(_block(4096))          # four times capacity, every time
        self.assertEqual(ring.allocated_bytes, allocated)
        self.assertEqual(id(ring._buffer), buffer_id, "the buffer was reallocated")
        self.assertEqual(ring.status()["held_samples"], 1024)

    def test_a_window_larger_than_the_ring_is_refused_rather_than_grown(self):
        ring = _ring(capacity=1024)
        ring.append(_block(1024))
        self.assertEqual(ring.acquire_window(2048).reason_code, "WINDOW_TOO_LARGE")
        self.assertEqual(ring.status()["window_overlap"], WINDOW_OVERLAP)


class ChronologyTests(unittest.TestCase):
    """2. Wraparound preserves chronological ordering."""

    def test_a_window_spanning_the_wrap_point_is_still_in_order(self):
        ring = _ring(capacity=8)
        ring.append(_block(5, start=0))         # 0..4, no wrap yet
        ring.append(_block(5, start=5))         # 5..9, wraps at index 8
        acquired = ring.acquire_window(8)
        self.assertTrue(acquired)
        self.assertEqual([int(v.real) for v in acquired.window.samples],
                         [2, 3, 4, 5, 6, 7, 8, 9])

    def test_repeated_wraps_never_reorder_or_duplicate(self):
        ring = _ring(capacity=16)
        for start in range(0, 400, 7):
            ring.append(_block(7, start=start))
        samples = [int(v.real) for v in ring.acquire_window(16).window.samples]
        self.assertEqual(samples, sorted(samples), "samples came back out of order")
        self.assertEqual(len(set(samples)), 16, "a sample was duplicated across the wrap")
        self.assertEqual(samples[-1], 405)

    def test_a_window_timestamp_spans_its_own_samples(self):
        ring = _ring(capacity=1024, rate=1024.0)
        ring.append(_block(1024), {"timestamp": 100.0})
        window = ring.acquire_window(512).window
        self.assertAlmostEqual(window.end_time, 101.0, places=6)
        self.assertAlmostEqual(window.duration_s, 0.5, places=6)
        self.assertAlmostEqual(window.start_time, 100.5, places=6)


class InsufficientWindowTests(unittest.TestCase):
    """3. Incomplete windows return INSUFFICIENT_WINDOW."""

    def test_a_partly_filled_ring_refuses_rather_than_padding(self):
        ring = _ring(capacity=1024)
        self.assertEqual(ring.acquire_window(512).reason_code, "INSUFFICIENT_WINDOW")
        ring.append(_block(511))
        acquired = ring.acquire_window(512)
        self.assertEqual(acquired.reason_code, "INSUFFICIENT_WINDOW")
        self.assertIsNone(acquired.window)
        self.assertFalse(acquired)
        self.assertIn("511 OF 512", acquired.detail)
        ring.append(_block(1))
        self.assertTrue(ring.acquire_window(512))

    def test_the_refusal_is_a_result_and_serializes_without_samples(self):
        ring = _ring(capacity=1024)
        payload = ring.acquire_window(512).to_dict()
        self.assertIsNone(payload["window"])
        self.assertIn("HAS NOT REFILLED", payload["reason"])
        json.dumps(payload)


class InvalidationTests(unittest.TestCase):
    """4 and 5. Every reason clears, and no window spans a configuration change."""

    def test_every_declared_reason_clears_the_ring_and_advances_the_epoch(self):
        for reason in INVALIDATION_REASONS:
            ring = _ring(capacity=64)
            ring.append(_block(64, value=1.0))
            self.assertEqual(ring.status()["held_samples"], 64, reason)
            epoch = ring.invalidate(reason)
            status = ring.status()
            self.assertEqual(status["held_samples"], 0, reason)
            self.assertEqual(status["state"], "INVALIDATED", reason)
            self.assertEqual(status["last_invalidation_reason"], reason)
            self.assertEqual(epoch, 1, reason)
            # Zeroed, not merely index-reset: an explicit clear must not leave the
            # previous signal chain sitting in the process image.
            self.assertTrue(np.all(ring._buffer == 0), reason)

    def test_an_unnamed_reason_raises_rather_than_clearing_quietly(self):
        ring = _ring(capacity=64)
        ring.append(_block(64))
        for bad in ("", "BECAUSE", "retune please", None):
            with self.assertRaises(ValueError):
                ring.invalidate(bad)
        self.assertEqual(ring.status()["held_samples"], 64, "a refused clear still cleared")

    def test_pre_retune_and_post_retune_samples_can_never_share_a_window(self):
        ring = _ring(capacity=64)
        ring.append(_block(64, value=1.0))
        self.assertTrue(ring.acquire_window(64))
        ring.invalidate("RETUNE")
        ring.append(_block(63, value=2.0))
        # 63 old-configuration samples are still physically absent, not merely
        # unlabelled: the request is refused rather than blended.
        self.assertEqual(ring.acquire_window(64).reason_code, "INSUFFICIENT_WINDOW")
        ring.append(_block(1, value=2.0))
        samples = ring.acquire_window(64).window.samples
        self.assertTrue(np.all(samples == 2.0), "a pre-retune sample survived into a window")

    def test_a_changed_signal_chain_clears_without_being_asked(self):
        """The invariant does not depend on every call site remembering it."""
        ring = _ring(capacity=64, chain="chain-a")
        ring.append(_block(64, value=1.0))
        ring.append(_block(8, value=2.0), {"signal_chain_hash": "chain-b"})
        status = ring.status()
        self.assertEqual(status["last_invalidation_reason"], "SIGNAL_CHAIN_CHANGE")
        self.assertEqual(status["signal_chain_hash"], "chain-b")
        self.assertEqual(status["held_samples"], 8)
        self.assertEqual(status["configuration_epoch"], 1)

    def test_a_changed_sample_rate_clears_without_being_asked(self):
        ring = _ring(capacity=64, rate=1024.0)
        ring.append(_block(64))
        ring.append(_block(8), {"sample_rate_hz": 2048.0})
        status = ring.status()
        self.assertEqual(status["last_invalidation_reason"], "SAMPLE_RATE_CHANGE")
        self.assertEqual(status["sample_rate_hz"], 2048.0)
        self.assertEqual(status["held_samples"], 8)


class DigestTests(unittest.TestCase):
    """6. Digests are bridge-generated and reproducible."""

    def test_identical_samples_under_one_configuration_digest_identically(self):
        first, second = _ring(capacity=64), _ring(capacity=64)
        first.append(_block(64, start=0))
        second.append(_block(64, start=0))
        self.assertEqual(first.acquire_window(64).window.digest,
                         second.acquire_window(64).window.digest)

    def test_the_same_samples_under_a_different_signal_chain_do_not(self):
        """Products through different antennas are not comparable evidence."""
        a, b = _ring(capacity=64, chain="chain-a"), _ring(capacity=64, chain="chain-b")
        a.append(_block(64))
        b.append(_block(64))
        self.assertNotEqual(a.acquire_window(64).window.digest,
                            b.acquire_window(64).window.digest)

    def test_a_digest_is_algorithm_qualified_and_matches_the_phase_0_shape(self):
        from rf_signal_family import DIGEST_LENGTHS, _check_window_hash
        ring = _ring(capacity=64)
        ring.append(_block(64))
        digest = ring.acquire_window(64).window.digest
        algorithm, _, hexdigest = digest.partition(":")
        self.assertIn(algorithm, DIGEST_LENGTHS)
        self.assertEqual(len(hexdigest), DIGEST_LENGTHS[algorithm])
        # The gate that only ever checked shape now has something real to check.
        self.assertEqual(_check_window_hash(digest), [])

    def test_a_caller_cannot_supply_its_own_window_identity(self):
        """The bridge issues both halves; neither is an argument."""
        import inspect
        parameters = set(inspect.signature(BoundedIQRing.acquire_window).parameters)
        self.assertEqual(parameters, {"self", "duration_samples"})


class WindowVerificationTests(unittest.TestCase):
    """7. Forged or expired window IDs cannot validate."""

    def _issued(self, capacity=64):
        ring = _ring(capacity=capacity)
        ring.append(_block(capacity))
        return ring, ring.acquire_window(capacity).window

    def test_a_window_this_ring_issued_and_still_holds_verifies(self):
        ring, window = self._issued()
        verification = ring.verify_window(window.window_id, window.digest)
        self.assertTrue(verification)
        self.assertEqual(verification.reason_code, "WINDOW_VERIFIED")

    def test_a_forged_identifier_does_not_verify_however_well_formed(self):
        ring, window = self._issued()
        for forged in ("iqw-0-1-deadbeefcafe", "", "iqw-9-9-000000000000"):
            result = ring.verify_window(forged, window.digest)
            self.assertFalse(result, forged)
            self.assertEqual(result.reason_code, "WINDOW_NOT_ISSUED", forged)
        self.assertIn("A CORRECTLY SHAPED IDENTIFIER IS NOT AN ISSUED ONE",
                      ring.verify_window("iqw-0-1-x", "blake2s:" + "0" * 64).reason)

    def test_a_real_identifier_with_an_invented_digest_does_not_verify(self):
        ring, window = self._issued()
        result = ring.verify_window(window.window_id, "blake2s:" + "a" * 64)
        self.assertEqual(result.reason_code, "DIGEST_MISMATCH")

    def test_a_window_that_predates_an_invalidation_expires(self):
        ring, window = self._issued()
        ring.invalidate("GAIN_CHANGE")
        result = ring.verify_window(window.window_id, window.digest)
        self.assertEqual(result.reason_code, "EPOCH_CHANGED")
        self.assertIn("GAIN_CHANGE", result.detail)

    def test_a_window_whose_samples_have_been_overwritten_expires(self):
        ring, window = self._issued(capacity=64)
        # Still entirely held: the ring has not yet advanced past its first sample.
        self.assertTrue(ring.verify_window(window.window_id, window.digest))
        ring.append(_block(1))
        result = ring.verify_window(window.window_id, window.digest)
        self.assertEqual(result.reason_code, "WINDOW_EVICTED")
        self.assertIn("NO LONGER EXIST", result.reason)

    def test_a_closed_ring_verifies_nothing(self):
        ring, window = self._issued()
        ring.close()
        self.assertEqual(ring.verify_window(window.window_id, window.digest).reason_code,
                         "RING_CLOSED")
        self.assertEqual(ring.acquire_window(64).reason_code, "RING_CLOSED")


class NonTransportabilityTests(unittest.TestCase):
    """8. Neither API serialization nor exception paths expose samples."""

    MARKER = 1234.5

    def _ring_with_marker(self):
        ring = _ring(capacity=64)
        ring.append(_block(64, value=self.MARKER))
        return ring

    def test_status_is_metadata_only_and_json_serializable(self):
        ring = self._ring_with_marker()
        ring.acquire_window(64)
        payload = json.dumps(ring.status())
        self.assertNotIn("1234", payload, "a sample value reached the status API")
        self.assertNotIn("samples", ring.status(), "status published a samples key")
        self.assertIn('"raw_iq_exposed": false', payload)
        status = ring.status()
        self.assertFalse(status["transportable"])
        self.assertFalse(status["model_context_eligible"])
        self.assertEqual(status["persistence"], "NONE")
        self.assertEqual(status["disk_fallback"], "NOT_IMPLEMENTED_AND_NOT_AUTHORIZED")
        self.assertEqual(status["crash_dump_facility"], "NOT_IMPLEMENTED_AND_NOT_AUTHORIZED")

    def test_a_window_serializes_its_identity_and_not_its_content(self):
        window = self._ring_with_marker().acquire_window(64).window
        payload = json.dumps(window.to_dict())
        self.assertNotIn("1234", payload)
        self.assertNotIn("samples", window.to_dict())
        self.assertIn("digest", window.to_dict())

    def test_neither_the_ring_nor_a_window_can_be_pickled(self):
        ring = self._ring_with_marker()
        window = ring.acquire_window(64).window
        for target in (ring, window):
            with self.assertRaises(RawIQNotTransportable):
                pickle.dumps(target)

    def test_reprs_withhold_samples_because_reprs_reach_logs(self):
        ring = self._ring_with_marker()
        window = ring.acquire_window(64).window
        for text in (repr(ring), repr(window), str(window)):
            self.assertNotIn("1234", text)
            self.assertIn("withheld", text)

    def test_exception_messages_carry_no_sample_values(self):
        ring = self._ring_with_marker()
        with self.assertRaises(ValueError) as bad_reason:
            ring.invalidate("SOMETHING")
        ring.close()
        with self.assertRaises(RuntimeError) as closed:
            ring.append(_block(8, value=self.MARKER))
        for error in (bad_reason, closed):
            self.assertNotIn("1234", str(error.exception))

    def test_a_consumer_receives_a_frozen_copy_not_the_writable_ring(self):
        ring = _ring(capacity=64)
        ring.append(_block(64, value=1.0))
        window = ring.acquire_window(64).window
        self.assertFalse(window.samples.flags.writeable)
        with self.assertRaises(ValueError):
            window.samples[0] = 9.0
        # And it does not alias the buffer, so later appends cannot change it.
        ring.append(_block(64, value=7.0))
        self.assertTrue(np.all(window.samples == 1.0))

    def test_a_closed_ring_zeroes_its_allocation(self):
        ring = self._ring_with_marker()
        ring.close()
        self.assertTrue(np.all(ring._buffer == 0))
        self.assertEqual(ring.status()["state"], "CLOSED")


class ProcessOwnershipTests(unittest.TestCase):
    """9. Child-process mode cannot instantiate the ring."""

    def setUp(self):
        self._previous = os.environ.get("SCYTHE_PROCESS_ROLE")

    def tearDown(self):
        if self._previous is None:
            os.environ.pop("SCYTHE_PROCESS_ROLE", None)
        else:
            os.environ["SCYTHE_PROCESS_ROLE"] = self._previous

    def test_a_child_process_is_refused_before_anything_is_allocated(self):
        for role in ("child", "CHILD", "spectrum_mcp", "worker"):
            os.environ["SCYTHE_PROCESS_ROLE"] = role
            with self.assertRaises(RawIQRetentionRefused) as refused:
                _ring()
            self.assertIn("may not retain raw IQ", str(refused.exception))
            self.assertIn("derived products only", str(refused.exception))

    def test_the_orchestrator_may_allocate_one(self):
        os.environ["SCYTHE_PROCESS_ROLE"] = "orchestrator"
        self.assertEqual(_ring(capacity=64).status()["owner"], "ORCHESTRATOR_BRIDGE")

    def test_the_ring_is_not_reachable_from_the_observation_or_mcp_surfaces(self):
        """No import path carries raw IQ out to a caller that must not have it."""
        import graphops_rf_ingest
        import rf_mcp
        for module in (graphops_rf_ingest, rf_mcp):
            self.assertFalse(hasattr(module, "BoundedIQRing"), module.__name__)


class BoundedMemoryTests(unittest.TestCase):
    """10. Sustained input maintains bounded memory."""

    def test_sustained_append_and_acquire_grows_nothing(self):
        ring = _ring(capacity=1024)
        allocated = ring.allocated_bytes
        for index in range(200):
            ring.append(_block(1024, start=index * 1024))
            ring.acquire_window(1024)
        status = ring.status()
        self.assertEqual(ring.allocated_bytes, allocated)
        self.assertEqual(status["held_samples"], 1024)
        self.assertEqual(status["issued_windows"], 200)
        # Window records are metadata only and capped, so remembering what was
        # issued cannot itself become the leak.
        self.assertEqual(status["tracked_windows"], MAX_TRACKED_WINDOWS)
        self.assertLessEqual(len(ring._windows), MAX_TRACKED_WINDOWS)

    def test_the_oldest_tracked_window_is_forgotten_rather_than_accumulated(self):
        ring = _ring(capacity=64)
        ring.append(_block(64))
        first = ring.acquire_window(64).window
        for _ in range(MAX_TRACKED_WINDOWS + 4):
            ring.append(_block(64))
            ring.acquire_window(64)
        self.assertEqual(ring.verify_window(first.window_id, first.digest).reason_code,
                         "WINDOW_NOT_ISSUED")

    def test_oldest_sample_age_tracks_the_fill_rather_than_the_capacity(self):
        ring = _ring(capacity=1024, rate=1024.0)
        self.assertIsNone(ring.status()["oldest_sample_age_ms"])
        ring.append(_block(256), {"timestamp": 10.0})
        self.assertAlmostEqual(ring.status()["oldest_sample_age_ms"], 250.0, places=3)
        ring.append(_block(768), {"timestamp": 10.25})
        self.assertAlmostEqual(ring.status()["oldest_sample_age_ms"], 1000.0, places=3)


class RingStateTests(unittest.TestCase):
    """The published state must never overstate what the ring holds."""

    def test_state_moves_from_invalidated_through_filling_to_ready(self):
        ring = _ring(capacity=64)
        self.assertEqual(ring.status()["state"], "INVALIDATED")
        ring.append(_block(32))
        self.assertEqual(ring.status()["state"], "FILLING")
        ring.append(_block(32))
        self.assertEqual(ring.status()["state"], "READY")
        ring.invalidate("DISCONNECT")
        self.assertEqual(ring.status()["state"], "INVALIDATED")

    def test_an_oversized_block_keeps_its_tail_and_reports_what_it_kept(self):
        ring = _ring(capacity=64)
        kept = ring.append(_block(200, start=0))
        self.assertEqual(kept, 64, "the retained count must not claim the whole block")
        self.assertEqual([int(v.real) for v in ring.acquire_window(64).window.samples],
                         list(range(136, 200)))

    def test_an_empty_block_is_a_no_op_rather_than_an_error(self):
        ring = _ring(capacity=64)
        self.assertEqual(ring.append(np.array([], dtype=STORAGE_DTYPE)), 0)
        self.assertEqual(ring.status()["held_samples"], 0)


if __name__ == "__main__":
    unittest.main()
