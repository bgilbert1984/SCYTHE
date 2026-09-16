"""§5.24: is this precisely the window the ring issued, and can it still change?

`verify_window` answers a narrower question about two strings and says so.
These are the tests for the second operation -- the one whose answer is a live
scope rather than a verdict, because a verdict about a mutable object is a
statement about the past.
"""

import ast
import json
import os
import pathlib
import pickle
import tracemalloc
import unittest
from dataclasses import replace

import numpy as np

import rf_iq_ring as ring_module
from rf_iq_ring import (
    ATTESTATION_DIGEST_MISMATCH, ATTESTATION_EPOCH_CHANGED,
    ATTESTATION_METADATA_MISMATCH, ATTESTATION_NOT_AN_IQ_WINDOW,
    ATTESTATION_REPRESENTATION_INVALID, ATTESTATION_RING_CLOSED,
    ATTESTATION_SCOPE_NOT_ACTIVE, ATTESTATION_SCOPE_NOT_MINTED,
    ATTESTATION_WINDOW_EVICTED, ATTESTATION_WINDOW_NOT_ISSUED,
    ATTESTED_METADATA_FIELDS, BYTES_PER_SAMPLE, STORAGE_DTYPE,
    ATTESTATION_WRITE_TARGET_INVALID,
    AttestationRefused, AttestedIQWindowScope, BoundedIQRing, IQWindow,
    RawIQNotTransportable, _window_digest,
)

CAPACITY = 4096


def _ring(capacity=CAPACITY, chain="blake2s:chain-a"):
    ring = BoundedIQRing(capacity_samples=capacity, sample_rate_hz=2_048_000.0,
                         signal_chain_hash=chain)
    ring.append(np.arange(capacity, dtype=STORAGE_DTYPE))
    return ring


def _window(ring, count=CAPACITY):
    acquired = ring.acquire_window(count)
    assert acquired, acquired.reason_code
    return acquired.window


def _immutable(values):
    """An array backed by immutable bytes, as `acquire_window` issues."""
    payload = np.asarray(values, dtype=STORAGE_DTYPE).tobytes(order="C")
    return np.frombuffer(payload, dtype=STORAGE_DTYPE)


def _drain(scope):
    """Write the attested bytes through a pipe and read them back.

    The only way to see the payload is to have it written somewhere, which is
    the point: a test that could fetch a handle would be testing the hole.
    A pipe keeps this process-local -- no file, no directory, no byte on disk.
    """
    read_fd, write_fd = os.pipe()
    try:
        written = scope._write_payload_to_fd(write_fd)
        os.close(write_fd)
        write_fd = None
        chunks, total = [], 0
        while total < written:
            chunk = os.read(read_fd, 1 << 16)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        return written, b"".join(chunks)
    finally:
        if write_fd is not None:
            os.close(write_fd)
        os.close(read_fd)


class AttestationAcceptsWhatTheRingIssuedTests(unittest.TestCase):

    def test_a_genuine_window_attests(self):
        ring = _ring()
        window = _window(ring)
        with ring.attest_window(window) as attested:
            self.assertTrue(attested.active)
            self.assertEqual(attested.window_id, window.window_id)
            self.assertEqual(attested.digest, window.digest)

    def test_the_scope_binds_the_bytes_that_were_attested(self):
        ring = _ring()
        window = _window(ring)
        with ring.attest_window(window) as attested:
            written, payload = _drain(attested)
        self.assertEqual(written, CAPACITY * BYTES_PER_SAMPLE)
        self.assertEqual(payload, window.samples.tobytes())

    def test_the_digest_is_recomputed_from_the_bound_bytes(self):
        """Not read off the object. An object supplying its own comparands
        proves nothing, so the recomputation uses the record's chain, epoch and
        sample count."""
        ring = _ring()
        window = _window(ring)
        with ring.attest_window(window) as attested:
            _written, payload = _drain(attested)
        self.assertEqual(
            _window_digest(window.signal_chain_hash,
                           window.configuration_epoch,
                           window.sample_count, payload),
            window.digest)


class TheObjectMustBeTheOneIssuedTests(unittest.TestCase):
    """What the two-string check cannot ask."""

    def test_a_subclass_refuses(self):
        class NearlyAnIQWindow(IQWindow):
            pass

        ring = _ring()
        window = _window(ring)
        impostor = NearlyAnIQWindow(**{f: getattr(window, f)
                                       for f in window.__dataclass_fields__})
        with self.assertRaises(AttestationRefused) as caught:
            ring.attest_window(impostor)
        self.assertEqual(caught.exception.code, ATTESTATION_NOT_AN_IQ_WINDOW)

    def test_a_mapping_carrying_the_same_strings_refuses(self):
        ring = _ring()
        window = _window(ring)
        with self.assertRaises(AttestationRefused) as caught:
            ring.attest_window(window.to_dict())
        self.assertEqual(caught.exception.code, ATTESTATION_NOT_AN_IQ_WINDOW)

    def test_changed_samples_on_a_genuine_pair_refuse(self):
        """The demonstration `verify_window` cannot make: the ID and the digest
        are the ring's own, and the bytes are not."""
        ring = _ring()
        window = _window(ring)
        other = _immutable(np.arange(CAPACITY, 2 * CAPACITY))
        swapped = replace(window, samples=other)
        self.assertEqual(swapped.window_id, window.window_id)
        self.assertEqual(swapped.digest, window.digest)
        self.assertTrue(ring.verify_window(swapped.window_id, swapped.digest))
        with self.assertRaises(AttestationRefused) as caught:
            ring.attest_window(swapped)
        self.assertEqual(caught.exception.code, ATTESTATION_DIGEST_MISMATCH)

    def test_every_metadata_field_changed_alone_refuses(self):
        ring = _ring()
        window = _window(ring)
        altered = {
            "configuration_epoch": window.configuration_epoch + 1,
            "first_sample_index": window.first_sample_index + 1,
            "sample_count": window.sample_count - 1,
            "sample_rate_hz": window.sample_rate_hz * 2,
            "start_time": window.start_time + 1.0,
            "end_time": window.end_time + 1.0,
            "signal_chain_hash": "blake2s:someone-elses-chain",
            "digest": "blake2s:" + "0" * 64,
        }
        for field, value in altered.items():
            with self.subTest(field=field):
                with self.assertRaises(AttestationRefused) as caught:
                    ring.attest_window(replace(window, **{field: value}))
                self.assertEqual(caught.exception.code,
                                 ATTESTATION_METADATA_MISMATCH)

    def test_the_declared_field_list_covers_the_window(self):
        """`ATTESTED_METADATA_FIELDS` is a list somebody can read. This is the
        test that fails if a field is added to `IQWindow` and not to it."""
        ring = _ring()
        window = _window(ring)
        for field in ATTESTED_METADATA_FIELDS:
            self.assertTrue(hasattr(window, field), field)
        uncompared = (set(window.__dataclass_fields__)
                      - set(ATTESTED_METADATA_FIELDS)
                      - {"window_id", "samples"})
        self.assertEqual(uncompared, set(),
                         f"{uncompared} are on IQWindow and compared nowhere")

    def test_an_unissued_window_refuses(self):
        ring = _ring()
        other = _ring(chain="blake2s:chain-b")
        with self.assertRaises(AttestationRefused) as caught:
            ring.attest_window(_window(other))
        self.assertEqual(caught.exception.code, ATTESTATION_WINDOW_NOT_ISSUED)

    def test_a_closed_ring_refuses(self):
        ring = _ring()
        window = _window(ring)
        ring.close()
        with self.assertRaises(AttestationRefused) as caught:
            ring.attest_window(window)
        self.assertEqual(caught.exception.code, ATTESTATION_RING_CLOSED)

    def test_epoch_change_and_eviction_keep_distinct_refusals(self):
        ring = _ring()
        window = _window(ring)
        ring.invalidate("RETUNE")
        with self.assertRaises(AttestationRefused) as caught:
            ring.attest_window(window)
        self.assertEqual(caught.exception.code, ATTESTATION_EPOCH_CHANGED)

        fresh = _ring()
        evicted = _window(fresh, CAPACITY // 2)
        fresh.append(np.zeros(CAPACITY, dtype=STORAGE_DTYPE))
        with self.assertRaises(AttestationRefused) as caught:
            fresh.attest_window(evicted)
        self.assertEqual(caught.exception.code, ATTESTATION_WINDOW_EVICTED)


class TheRepresentationIsExactTests(unittest.TestCase):
    """A coerced array is a different file, so nothing here converts anything."""

    def setUp(self):
        self.ring = _ring()
        self.window = _window(self.ring)

    def _refuses(self, samples):
        with self.assertRaises(AttestationRefused) as caught:
            self.ring.attest_window(replace(self.window, samples=samples))
        self.assertEqual(caught.exception.code,
                         ATTESTATION_REPRESENTATION_INVALID)

    def test_a_wrong_dtype_refuses(self):
        payload = np.zeros(CAPACITY, dtype="complex128").tobytes()
        self._refuses(np.frombuffer(payload, dtype="complex128"))

    def test_a_reversed_byte_order_refuses(self):
        payload = self.window.samples.tobytes()
        self._refuses(np.frombuffer(payload, dtype=">c8"))

    def test_a_second_dimension_refuses(self):
        payload = self.window.samples.tobytes()
        flat = np.frombuffer(payload, dtype=STORAGE_DTYPE)
        self._refuses(flat.reshape(2, CAPACITY // 2))

    def test_a_short_array_refuses(self):
        payload = self.window.samples[:-1].tobytes()
        self._refuses(np.frombuffer(payload, dtype=STORAGE_DTYPE))

    def test_a_non_contiguous_array_refuses(self):
        payload = np.zeros(CAPACITY * 2, dtype=STORAGE_DTYPE).tobytes()
        self._refuses(np.frombuffer(payload, dtype=STORAGE_DTYPE)[::2])

    def test_a_writeable_array_refuses(self):
        self._refuses(np.array(self.window.samples, dtype=STORAGE_DTYPE))

    def test_an_owning_frozen_array_refuses(self):
        """The distinction the whole backing change turns on: this array's
        write flag can be restored, so freezing it is discouragement."""
        owning = np.array(self.window.samples, dtype=STORAGE_DTYPE)
        owning.setflags(write=False)
        self.assertFalse(owning.flags.writeable)
        owning.setflags(write=True)          # the point: this succeeds
        owning.setflags(write=False)
        self._refuses(owning)

    def test_samples_that_are_not_an_array_refuse(self):
        self._refuses([complex(0, 0)] * CAPACITY)


class ImmutableBackingTests(unittest.TestCase):

    def test_an_issued_payload_cannot_be_made_writeable(self):
        window = _window(_ring())
        self.assertFalse(window.samples.flags.writeable)
        self.assertFalse(window.samples.flags.owndata)
        with self.assertRaises(ValueError):
            window.samples.setflags(write=True)

    def test_a_write_target_that_is_not_a_descriptor_refuses(self):
        ring = _ring()
        with ring.attest_window(_window(ring)) as attested:
            with self.assertRaises(AttestationRefused) as caught:
                attested._write_payload_to_fd("a file, surely")
            self.assertEqual(caught.exception.code,
                             ATTESTATION_WRITE_TARGET_INVALID)

    def test_replacing_the_window_samples_cannot_change_the_scope(self):
        """The scope captured the array at mint time and never re-reads the
        object, so reflection on a frozen dataclass changes nothing it writes."""
        ring = _ring()
        window = _window(ring)
        with ring.attest_window(window) as attested:
            _n, before = _drain(attested)
            object.__setattr__(window, "samples",
                               _immutable(np.arange(CAPACITY, 2 * CAPACITY)))
            _n, after = _drain(attested)
            self.assertEqual(after, before)

    def test_replacing_window_metadata_cannot_change_the_scope(self):
        ring = _ring()
        window = _window(ring)
        with ring.attest_window(window) as attested:
            object.__setattr__(window, "digest", "blake2s:" + "f" * 64)
            object.__setattr__(window, "signal_chain_hash", "blake2s:elsewhere")
            self.assertNotEqual(attested.digest, window.digest)
            self.assertNotEqual(attested.signal_chain_hash,
                                window.signal_chain_hash)


class TheBoundStateIsNotAnAttributeTests(unittest.TestCase):
    """Immutable bytes stop modification; they do not stop substitution."""

    def test_a_scope_cannot_be_constructed_through_the_public_api(self):
        with self.assertRaises(AttestationRefused) as caught:
            AttestedIQWindowScope("att-anything")
        self.assertEqual(caught.exception.code, ATTESTATION_SCOPE_NOT_MINTED)

    def test_replacing_the_handle_refuses_rather_than_writing_something_else(self):
        ring = _ring()
        scope = ring.attest_window(_window(ring))
        scope._handle = "att-forged"
        self.assertFalse(scope.active)
        with self.assertRaises(AttestationRefused) as caught:
            scope._payload_nbytes()
        self.assertEqual(caught.exception.code, ATTESTATION_SCOPE_NOT_MINTED)

    def test_borrowing_another_live_scopes_handle_refuses(self):
        """The entry names the object it was minted for, so a handle stolen
        from a live scope resolves to state that is not this scope's."""
        ring = _ring()
        first = ring.attest_window(_window(ring))
        second = ring.attest_window(_window(ring))
        second._handle = first._handle
        self.assertFalse(second.active)
        with self.assertRaises(AttestationRefused):
            second.window_id
        self.assertTrue(first.active)

    def test_the_scope_carries_no_payload_attribute(self):
        ring = _ring()
        scope = ring.attest_window(_window(ring))
        self.assertEqual(set(AttestedIQWindowScope.__slots__),
                         {"_handle", "__weakref__"})
        self.assertFalse(hasattr(scope, "__dict__"))


class ScopeLifetimeTests(unittest.TestCase):

    def test_an_exited_scope_is_inert(self):
        ring = _ring()
        with ring.attest_window(_window(ring)) as attested:
            pass
        self.assertFalse(attested.active)
        for call in (lambda: attested.window_id,
                     lambda: attested.to_dict(),
                     lambda: attested._payload_nbytes(),
                     lambda: _drain(attested)):
            with self.assertRaises(AttestationRefused) as caught:
                call()
            self.assertEqual(caught.exception.code, ATTESTATION_SCOPE_NOT_ACTIVE)

    def test_an_exception_inside_the_scope_still_ends_it(self):
        ring = _ring()
        scope = ring.attest_window(_window(ring))
        with self.assertRaises(ZeroDivisionError):
            with scope:
                1 / 0
        self.assertFalse(scope.active)

    def test_an_ended_scope_holds_no_raw_iq(self):
        ring = _ring()
        with ring.attest_window(_window(ring)) as attested:
            handle = attested._handle
        state = ring_module._SCOPE_REGISTRY.get(handle)
        self.assertIsNotNone(state, "the entry stays so the code can say ENDED")
        self.assertIsNone(state.payload)
        self.assertIsNone(state.samples)

    def test_the_scope_spans_a_header_and_a_complete_payload_write(self):
        """§5.24: a scope that closed after the header would leave the payload
        crossing the boundary unattested."""
        ring = _ring()
        written = bytearray()
        with ring.attest_window(_window(ring)) as attested:
            written += json.dumps(attested.to_dict(), sort_keys=True).encode()
            self.assertTrue(attested.active)
            _n, payload = _drain(attested)
            written += payload
            self.assertTrue(attested.active)
        self.assertGreater(len(written), CAPACITY * BYTES_PER_SAMPLE)

    def test_the_registry_does_not_grow_without_bound(self):
        ring = _ring()
        for _ in range(40):
            with ring.attest_window(_window(ring)):
                pass
        self.assertLess(len(ring_module._SCOPE_REGISTRY), 40)


class NoPayloadEscapesTests(unittest.TestCase):

    def test_the_scope_dictionary_carries_no_samples(self):
        ring = _ring()
        with ring.attest_window(_window(ring)) as attested:
            data = attested.to_dict()
            self.assertFalse(data["raw_iq_exposed"])
            text = json.dumps(data, sort_keys=True)
            for key, value in data.items():
                self.assertIsInstance(
                    value, (int, float, str, bool, type(None)),
                    f"{key} carries a {type(value).__name__}")
            self.assertNotIn("payload", text)

    def test_the_representation_withholds_the_payload(self):
        ring = _ring()
        with ring.attest_window(_window(ring)) as attested:
            self.assertIn("withheld", repr(attested))
            self.assertNotIn("complex", repr(attested).lower())

    def test_a_scope_refuses_pickling(self):
        ring = _ring()
        with ring.attest_window(_window(ring)) as attested:
            with self.assertRaises(RawIQNotTransportable):
                pickle.dumps(attested)

    def test_a_refusal_detail_carries_no_samples(self):
        ring = _ring()
        window = _window(ring)
        with self.assertRaises(AttestationRefused) as caught:
            ring.attest_window(replace(window, samples=[1, 2, 3]))
        self.assertNotIn("complex", caught.exception.detail.lower())
        self.assertLess(len(str(caught.exception)), 400)

    def test_ring_status_still_carries_no_contents(self):
        """Every value is a scalar or a label. `capacity_samples` is a count,
        so a substring check for "samples" would fail on a field that is
        exactly what it should be -- the citation-check mistake entry 13
        records. The property is that nothing here is array-like.
        """
        ring = _ring()
        with ring.attest_window(_window(ring)):
            status = ring.status()
            json.dumps(status, sort_keys=True)
        for key, value in status.items():
            with self.subTest(key=key):
                if isinstance(value, (list, tuple)):
                    # A list of labels is fine -- `invalidation_reasons` is one.
                    # A list of numbers would be contents.
                    self.assertTrue(all(isinstance(item, str) for item in value),
                                    f"{key} carries a numeric sequence")
                else:
                    self.assertIsInstance(
                        value, (int, float, str, bool, type(None)),
                        f"{key} carries a {type(value).__name__}")


class NoPayloadHandleEscapesTests(unittest.TestCase):
    """§5.24: "a byte handle that outlives the scope is a scope that ended
    without ending".

    Immutable backing solved mutation and left this open. A returned read-only
    `memoryview` keeps its own reference to the backing object, so clearing the
    registry entry on exit was bookkeeping -- the capability had already left,
    and `release()` on one view does not reach a slice a consumer made from it.
    Access is therefore an **action** that writes and returns a count.
    """

    PAYLOAD_TYPES = (bytes, bytearray, memoryview, np.ndarray)

    def test_no_scope_method_returns_a_payload_handle(self):
        """Called rather than reasoned about: every zero-argument method on a
        live scope is invoked and its result checked. This is the test that
        fails if an accessor is reintroduced under any name."""
        ring = _ring()
        checked = 0
        with ring.attest_window(_window(ring)) as attested:
            for name in dir(attested):
                if name in ("__init__", "__enter__", "__exit__", "__reduce__",
                            "__class__", "__init_subclass__", "__subclasshook__",
                            "__new__", "__sizeof__", "__dir__"):
                    continue
                try:
                    value = getattr(attested, name)
                except Exception:
                    continue
                if callable(value):
                    try:
                        value = value()
                    except TypeError:
                        continue
                    except Exception:
                        continue
                checked += 1
                self.assertNotIsInstance(
                    value, self.PAYLOAD_TYPES,
                    f"{name} returned a {type(value).__name__} -- a payload "
                    "handle that outlives the scope")
        self.assertGreater(checked, 5, "nothing was actually exercised")

    def test_the_write_action_returns_only_a_count(self):
        ring = _ring()
        with ring.attest_window(_window(ring)) as attested:
            written, payload = _drain(attested)
        self.assertIsInstance(written, int)
        self.assertEqual(written, len(payload))

    def test_nothing_readable_survives_the_scope(self):
        """The demonstration the old accessor failed: whatever a caller holds
        after exit must not still read the samples."""
        ring = _ring()
        window = _window(ring)
        with ring.attest_window(window) as attested:
            held = [getattr(attested, name, None) for name in dir(attested)
                    if not callable(getattr(attested, name, None))]
        for value in held:
            self.assertNotIsInstance(value, self.PAYLOAD_TYPES)
        for call in (lambda: attested._payload_nbytes(),
                     lambda: _drain(attested)):
            with self.assertRaises(AttestationRefused):
                call()

    def test_a_count_is_not_a_resumable_offset(self):
        """Partial writes complete inside the action. Handing back an offset
        would be the handle again with an integer in front of it."""
        ring = _ring()
        with ring.attest_window(_window(ring)) as attested:
            expected = attested._payload_nbytes()
            written, payload = _drain(attested)
        self.assertEqual(written, expected)
        self.assertEqual(len(payload), expected)


class MeasuredCostTests(unittest.TestCase):
    """§5.24 part four. The peak is measured, not inferred from final ownership
    -- inferring it is how the section's first draft came to say something
    false."""

    def test_acquisition_peaks_at_two_windows_and_retains_one(self):
        count = 65_536
        ring = BoundedIQRing(capacity_samples=count, sample_rate_hz=2_048_000.0,
                             signal_chain_hash="blake2s:chain-a")
        ring.append(np.zeros(count, dtype=STORAGE_DTYPE))
        window_bytes = count * BYTES_PER_SAMPLE

        tracemalloc.start()
        try:
            base = tracemalloc.get_traced_memory()[0]
            window = ring.acquire_window(count)
            current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertTrue(window)

        peak_windows = (peak - base) / window_bytes
        retained_windows = (current - base) / window_bytes
        # One authorised transient, bounded at one window. Three copies alive
        # would mean the payload was materialised again somewhere.
        self.assertLess(peak_windows, 2.5, f"peak was {peak_windows:.2f} windows")
        self.assertGreater(peak_windows, 1.5)
        self.assertLess(retained_windows, 1.5,
                        f"retained {retained_windows:.2f} windows after issuance")

    def test_attestation_copies_nothing(self):
        """A read-only memoryview hashes identically to the bytes it views, so
        recomputing the digest needs no copy of its own."""
        count = 65_536
        ring = BoundedIQRing(capacity_samples=count, sample_rate_hz=2_048_000.0,
                             signal_chain_hash="blake2s:chain-a")
        ring.append(np.zeros(count, dtype=STORAGE_DTYPE))
        window = ring.acquire_window(count).window
        window_bytes = count * BYTES_PER_SAMPLE

        tracemalloc.start()
        try:
            base = tracemalloc.get_traced_memory()[0]
            with ring.attest_window(window) as attested:
                self.assertEqual(attested._payload_nbytes(), window_bytes)
                _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLess((peak - base) / window_bytes, 0.1,
                        "attestation allocated a copy of the payload")


class RestrictedAccessorTests(unittest.TestCase):
    """§5.24 part five. Python module privacy enforces nothing, so what is
    claimed is what is checked: no public accessor, and every production
    reference to the private ones statically restricted."""

    ACCESSORS = ("_write_payload_to_fd", "_payload_nbytes")
    ALLOWED = {"rf_iq_ring.py"}

    def _production_modules(self):
        return [path for path in sorted(pathlib.Path(".").glob("*.py"))
                if not path.name.startswith("test_")]

    def test_there_is_no_public_accessor(self):
        public = [name for name in dir(AttestedIQWindowScope)
                  if not name.startswith("_")]
        self.assertEqual(sorted(public), ["active", "configuration_epoch",
                                          "digest", "sample_count",
                                          "signal_chain_hash", "to_dict",
                                          "window_id"])

    def test_the_payload_action_is_statically_restricted(self):
        offenders = {}
        for path in self._production_modules():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            hits = sorted({
                node.attr for node in ast.walk(tree)
                if isinstance(node, ast.Attribute) and node.attr in self.ACCESSORS
            } | {
                node.name for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.name in self.ACCESSORS
            })
            if hits and path.name not in self.ALLOWED:
                offenders[path.name] = hits
        self.assertEqual(offenders, {},
                         f"payload accessor referenced outside {self.ALLOWED}")

    def test_the_check_would_notice_a_new_call_site(self):
        """A scanner that found nothing anywhere would pass vacuously."""
        found = False
        for path in self._production_modules():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            if any(isinstance(node, ast.FunctionDef)
                   and node.name in self.ACCESSORS for node in ast.walk(tree)):
                found = True
        self.assertTrue(found, "the accessor was not found where it is defined")


if __name__ == "__main__":
    unittest.main()
