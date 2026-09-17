"""§5.24: is this precisely the window the ring issued, and can it still change?

`verify_window` answers a narrower question about two strings and says so.
These are the tests for the second operation -- the one whose answer is a live
scope rather than a verdict, because a verdict about a mutable object is a
statement about the past.
"""

import ast
import json
import os
import queue
import threading
import time
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


# The exemption teardown earns, and nothing else does. §5.25 made it explicit
# to `__exit__` before its implementation relied on this check.
PAYLOAD_STORE_EXEMPT = "__exit__"


def _payload_guard(source, class_name="AttestedIQWindowScope"):
    """Every `.payload` access in one class, and whether it is guarded.

    Returns `(offenders, loads, stores, methods)`. A load must sit inside a
    `with <state>.lock:` block over the same variable, and be preceded **inside
    that block** by a `_state()` re-resolution. A store must sit under the same
    lock, and is exempt from re-resolution **only in `PAYLOAD_STORE_EXEMPT`**.

    Taking a source string rather than reading the module is the seam that lets
    the tightening be tested: a check that only ever runs against the one file
    it passes on cannot show that it would refuse anything.
    """
    tree = ast.parse(source)
    scope_class = next(node for node in ast.walk(tree)
                       if isinstance(node, ast.ClassDef)
                       and node.name == class_name)
    offenders, counts = [], {"loads": 0, "stores": 0}

    def lock_holder(item):
        """The variable X in `with X.lock:`, or None."""
        expr = item.context_expr
        if (isinstance(expr, ast.Attribute) and expr.attr == "lock"
                and isinstance(expr.value, ast.Name)):
            return expr.value.id
        return None

    def resolution_lines(body, held):
        """Lines where `held = self._state()` happens in this block."""
        lines = []
        for node in ast.walk(ast.Module(body=body, type_ignores=[])):
            if (isinstance(node, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == held
                            for t in node.targets)
                    and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Attribute)
                    and node.value.func.attr == "_state"):
                lines.append(node.lineno)
        return lines

    def walk(node, method, held, resolved_at):
        if isinstance(node, ast.With):
            for item in node.items:
                holder = lock_holder(item)
                if holder is not None:
                    inner_resolved = resolution_lines(node.body, holder)
                    for child in node.body:
                        walk(child, method, holder,
                             min(inner_resolved) if inner_resolved else None)
                    return
            for child in node.body:
                walk(child, method, held, resolved_at)
            return
        if isinstance(node, ast.Attribute) and node.attr == "payload":
            owner = (node.value.id if isinstance(node.value, ast.Name)
                     else "<expression>")
            if held is None:
                offenders.append((method, node.lineno, "outside any state lock"))
            elif owner != held:
                offenders.append((method, node.lineno,
                                  f"guarded on {held} but reads {owner}"))
            elif isinstance(node.ctx, ast.Load):
                if resolved_at is None or node.lineno < resolved_at:
                    offenders.append((method, node.lineno,
                                      "no _state() re-resolution before the read"))
                else:
                    counts["loads"] += 1
            elif method == PAYLOAD_STORE_EXEMPT:
                counts["stores"] += 1
            else:
                offenders.append((
                    method, node.lineno,
                    f"a payload store outside {PAYLOAD_STORE_EXEMPT}; the "
                    "teardown exemption does not travel"))
        for child in ast.iter_child_nodes(node):
            walk(child, method, held, resolved_at)

    methods = 0
    for node in scope_class.body:
        if isinstance(node, ast.FunctionDef):
            methods += 1
            for child in node.body:
                walk(child, node.name, None, None)
    return offenders, counts["loads"], counts["stores"], methods


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
            _drain(scope)
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


class TeardownIsTerminalTests(unittest.TestCase):
    """Two lifetime failures the first implementation had, both demonstrated
    before they were repaired.

    Immutable backing and an opaque handle closed mutation and substitution.
    They left teardown resolving through the same mutable field it was meant to
    be immune to, and left a blocked write holding a local view across an exit.
    """

    def test_a_replaced_handle_cannot_survive_or_be_restored_after_exit(self):
        """Replacement must stay a refusal. Resolving teardown through the
        handle turned it into delayed capability recovery: the genuine entry
        stayed live, and restoring the field brought the scope back with its
        payload intact."""
        ring = _ring()
        with ring.attest_window(_window(ring)) as scope:
            genuine = scope._handle
            scope._handle = "replacement"

        scope._handle = genuine
        self.assertFalse(scope.active)
        with self.assertRaises(AttestationRefused) as caught:
            _drain(scope)
        self.assertEqual(caught.exception.code, ATTESTATION_SCOPE_NOT_ACTIVE)

    def test_a_replaced_handle_still_leaves_no_raw_iq_behind(self):
        ring = _ring()
        with ring.attest_window(_window(ring)) as scope:
            genuine = scope._handle
            scope._handle = "replacement"
        state = ring_module._SCOPE_REGISTRY.get(genuine)
        self.assertIsNotNone(state)
        self.assertFalse(state.active)
        self.assertIsNone(state.payload)
        self.assertIsNone(state.samples)

    def test_teardown_finds_the_scope_by_identity_not_by_handle(self):
        ring = _ring()
        scope = ring.attest_window(_window(ring))
        genuine = scope._handle
        scope._handle = "somewhere-else"
        scope.__exit__(None, None, None)
        self.assertFalse(ring_module._SCOPE_REGISTRY[genuine].active)


class WriteAndExitAreMutuallyExclusiveTests(unittest.TestCase):
    """§5.24: the scope stays active across the whole write.

    A liveness check taken once, followed by a blocking partial write over a
    local view, let the evidence bytes cross the boundary after the scope
    reported itself ended -- the lifetime inversion in time rather than in
    reference.
    """

    WRITE_GATE_TIMEOUT = 2.0

    def test_an_exit_waits_for_a_blocked_write_to_finish(self):
        """The claimed ordering, tested deterministically::

            write acquires the scope-state lock
                -> exit cannot deactivate the scope
                -> write completes
                -> exit acquires the lock and terminates the scope

        `os.write` is gated by an event rather than by a full pipe. An earlier
        version blocked a real kernel write on a shrunken pipe, and when the
        assertion failed the test aborted before draining it -- stranding a
        non-daemon thread on `os.write` so the suite could never exit. A control
        that converts a crisp assertion into an indefinite hang has produced no
        result at all.
        """
        ring = _ring()
        scope = ring.attest_window(_window(ring))
        scope.__enter__()

        entered_write = threading.Event()
        release_write = threading.Event()
        exit_finished = threading.Event()
        errors: "queue.Queue" = queue.Queue()
        real_write = ring_module.os.write

        def blocked_write(fd, view):
            entered_write.set()
            if not release_write.wait(timeout=self.WRITE_GATE_TIMEOUT):
                raise TimeoutError("the test did not release the write")
            return len(view)

        def run_write():
            try:
                scope._write_payload_to_fd(123)
            except BaseException as exc:            # noqa: BLE001 - reported below
                errors.put(exc)

        def run_exit():
            try:
                scope.__exit__(None, None, None)
            except BaseException as exc:            # noqa: BLE001 - reported below
                errors.put(exc)
            finally:
                exit_finished.set()

        writer = threading.Thread(target=run_write, daemon=True)
        exiter = threading.Thread(target=run_exit, daemon=True)
        ring_module.os.write = blocked_write
        try:
            writer.start()
            self.assertTrue(entered_write.wait(timeout=self.WRITE_GATE_TIMEOUT),
                            "the write never started")
            exiter.start()
            # The bytes are still crossing, so the scope has not ended.
            self.assertFalse(exit_finished.wait(timeout=0.3),
                             "the scope ended while a write held the payload")
            self.assertTrue(scope.active)
        finally:
            # Released whatever happened above, so a failed assertion cannot
            # strand a thread. Daemon threads are the containment belt, not the
            # cleanup mechanism -- a leaked daemon would turn a deadlock into a
            # green suite, which is why both are joined and checked below.
            release_write.set()
            writer.join(timeout=5)
            exiter.join(timeout=5)
            ring_module.os.write = real_write

        self.assertFalse(writer.is_alive(), "the write thread did not terminate")
        self.assertFalse(exiter.is_alive(), "the exit thread did not terminate")
        if not errors.empty():
            raise errors.get()
        self.assertTrue(exit_finished.is_set())
        self.assertFalse(scope.active)

    def test_a_partial_write_is_completed_internally(self):
        """The loop keeps calling until the payload is done, rather than
        handing back a resumable offset."""
        ring = _ring()
        scope = ring.attest_window(_window(ring))
        scope.__enter__()
        calls = []
        real_write = ring_module.os.write

        def short_write(fd, view):
            calls.append(len(view))
            return min(len(view), 1024)

        ring_module.os.write = short_write
        try:
            written = scope._write_payload_to_fd(123)
        finally:
            ring_module.os.write = real_write
            scope.__exit__(None, None, None)
        expected = CAPACITY * BYTES_PER_SAMPLE
        self.assertEqual(written, expected)
        self.assertGreater(len(calls), 1, "a short write was not resumed")
        self.assertEqual(calls[0], expected)
        self.assertEqual(sum(min(n, 1024) for n in calls), expected)

    def test_a_stalled_write_refuses_rather_than_looping(self):
        ring = _ring()
        scope = ring.attest_window(_window(ring))
        scope.__enter__()
        real_write = ring_module.os.write
        ring_module.os.write = lambda fd, view: 0
        try:
            with self.assertRaises(AttestationRefused) as caught:
                scope._write_payload_to_fd(123)
        finally:
            ring_module.os.write = real_write
            scope.__exit__(None, None, None)
        self.assertEqual(caught.exception.code,
                         ring_module.ATTESTATION_WRITE_INCOMPLETE)

    def test_a_write_after_an_exit_refuses_rather_than_racing(self):
        ring = _ring()
        scope = ring.attest_window(_window(ring))
        with scope:
            pass
        with self.assertRaises(AttestationRefused) as caught:
            _drain(scope)
        self.assertEqual(caught.exception.code, ATTESTATION_SCOPE_NOT_ACTIVE)

    def test_concurrent_mints_do_not_corrupt_the_registry(self):
        """The registry is one module-global dictionary and rings do not share
        a lock, so minting from several threads writes it concurrently."""
        rings = [_ring(chain=f"blake2s:chain-{index}") for index in range(4)]
        scopes, errors = [], []
        barrier = threading.Barrier(len(rings))

        def mint(ring):
            try:
                barrier.wait(5)
                for _ in range(8):
                    with ring.attest_window(_window(ring)) as attested:
                        scopes.append(attested.window_id)
            except Exception as exc:       # pragma: no cover - failure path
                errors.append(exc)

        threads = [threading.Thread(target=mint, args=(ring,)) for ring in rings]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(20)
        self.assertEqual(errors, [])
        self.assertEqual(len(scopes), len(rings) * 8)


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
        for call in (lambda: _drain(attested),):
            with self.assertRaises(AttestationRefused):
                call()

    def test_a_count_is_not_a_resumable_offset(self):
        """Partial writes complete inside the action. Handing back an offset
        would be the handle again with an integer in front of it."""
        ring = _ring()
        with ring.attest_window(_window(ring)) as attested:
            # Derived from the attested metadata, which is where a writer's
            # canonical header gets it: the ring's authoritative record, not
            # the payload object's own account of itself.
            expected = attested.sample_count * BYTES_PER_SAMPLE
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
                self.assertEqual(attested.sample_count * BYTES_PER_SAMPLE,
                                 window_bytes)
                _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLess((peak - base) / window_bytes, 0.1,
                        "attestation allocated a copy of the payload")


class RestrictedAccessorTests(unittest.TestCase):
    """§5.24 part five. Python module privacy enforces nothing, so what is
    claimed is what is checked: no public accessor, and every production
    reference to the private ones statically restricted."""

    # §5.25 added the second restricted action. §5.20's header carries
    # `payload_sha256` and the header is written **before** the payload, so the
    # digest cannot be a by-product of the write -- it has to be taken while
    # the scope is live and before anything is created. It is policed exactly
    # like the first: no public accessor, and every production call site inside
    # the allowed set.
    ACCESSORS = ("_write_payload_to_fd", "_payload_sha256")
    DELETED = ("_payload_nbytes",)
    ALLOWED = {"rf_iq_ring.py", "rf_capture_admission.py"}

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

    def test_the_deleted_accessor_has_not_come_back(self):
        """Entry 16 drained by deletion, not by rehabilitation.

        `_payload_nbytes()` had no production caller, read
        `state.payload.nbytes` outside the state lock, and added a second
        private surface to police. A writer derives the declared length from
        attested metadata instead, which is stronger: the expected count comes
        from the ring's authoritative record and the completed write reconciles
        against it independently.

        Checked at runtime **and** statically, because an attribute check alone
        would miss a method defined under a different name that returns the
        same thing -- and a source check alone would miss one added by
        assignment.
        """
        ring = _ring()
        with ring.attest_window(_window(ring)) as attested:
            for name in self.DELETED:
                self.assertFalse(
                    hasattr(attested, name),
                    f"{name} was deleted under entry 16 and has returned")
                self.assertNotIn(name, dir(AttestedIQWindowScope))
        source = pathlib.Path("rf_iq_ring.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        defined = {node.name for node in ast.walk(tree)
                   if isinstance(node, ast.FunctionDef)}
        for name in self.DELETED:
            self.assertNotIn(name, defined, f"{name} is defined again")

    def test_the_digest_action_returns_a_digest_and_never_the_bytes(self):
        """§5.25's payload-action authority, and why it is not the deleted one.

        `_payload_nbytes()` answered a question the attested metadata already
        answered and had no caller. This answers one nothing else can -- only
        the bytes know their own digest -- and a writer has to have it before
        the header it goes in is serialised.
        """
        import hashlib
        ring = _ring()
        window = _window(ring)
        with ring.attest_window(window) as attested:
            digest = attested._payload_sha256()
            self.assertEqual(type(digest), str)
            self.assertEqual(len(digest), 64)
            self.assertEqual(digest,
                             hashlib.sha256(window.samples.tobytes()).hexdigest())
            self.assertNotEqual(digest, attested.digest)

    def test_the_digest_action_refuses_on_an_ended_scope(self):
        ring = _ring()
        attested = ring.attest_window(_window(ring))
        attested.__exit__(None, None, None)
        with self.assertRaises(AttestationRefused) as caught:
            attested._payload_sha256()
        self.assertEqual(caught.exception.code, ATTESTATION_SCOPE_NOT_ACTIVE)

    def test_payload_reads_are_structurally_guarded(self):
        """A **structural tripwire**, and deliberately not the concurrency proof.

        What it establishes: every read of `.payload` in this class happens
        inside a `with <state>.lock:` block over *the same variable* whose
        payload is read, and every **load** is preceded inside that block by a
        `_state()` re-resolution. A teardown **store** -- clearing the payload
        -- is exempt from re-resolution, because re-resolving would raise on the
        very scope being ended.

        §5.25 tightened that exemption. It used to be categorical: *every*
        payload store was exempt, which silently licensed a future method to
        mutate `state.payload` under the right lock without ever proving it was
        the teardown operation. `AttestedIQWindowScope.__exit__` earns the
        exemption for a precise reason -- it invalidates state it has already
        found by object identity -- and **that justification does not travel**.

        What it does **not** establish: that the lock is the right one at
        runtime, that the object was not aliased before the block, or that exit
        and the operation are genuinely mutually exclusive. Those are live
        properties and `test_an_exit_waits_for_a_blocked_write_to_finish` is
        their proof; this only stops the shape regressing between runs.

        It replaced a check that matched the method *name*: a control re-adding
        the identical body as `_payload_size` failed zero tests. Name-matching
        polices a spelling, not a hazard.
        """
        offenders, loads, stores, methods = _payload_guard(
            pathlib.Path("rf_iq_ring.py").read_text(encoding="utf-8"))

        self.assertGreater(methods, 5, "the class was not actually walked")
        self.assertGreater(loads, 0,
                           "no guarded payload load was found, so this check "
                           "would pass on a class that never touches a payload")
        self.assertGreater(stores, 0,
                           "no exempt store was found, so the exemption this "
                           "test narrows would be narrowing nothing")
        self.assertEqual(
            offenders, [],
            "; ".join(f"{name}:{line} {why}" for name, line, why in offenders))

    def test_the_store_exemption_is_explicit_to_teardown(self):
        """Control 7's discrimination, and the reason the walker takes a string.

        Two classes differing only in the **name of the method** that clears
        the payload. Both hold the right lock; both would have passed the
        categorical form. Only the one whose store is in `__exit__` passes now.

        A check that only ever ran against the file it was written for could
        not show this: it would report `[]` either way, and reintroducing the
        categorical exemption would fail zero tests -- which is exactly the
        failure `_payload_size` demonstrated one layer up.
        """
        def source(method):
            return (
                "class AttestedIQWindowScope:\n"
                "    def _state(self):\n"
                "        return self._handle\n"
                "    def _a(self):\n"
                "        state = self._state()\n"
                "        with state.lock:\n"
                "            state = self._state()\n"
                "            return len(state.payload)\n"
                "    def _b(self):\n"
                "        pass\n"
                "    def _c(self):\n"
                "        pass\n"
                "    def _d(self):\n"
                "        pass\n"
                f"    def {method}(self):\n"
                "        state = self._own_state()\n"
                "        with state.lock:\n"
                "            state.payload = None\n")

        clean, loads, stores, methods = _payload_guard(source("__exit__"))
        self.assertEqual(clean, [])
        self.assertEqual((loads, stores), (1, 1))
        self.assertEqual(methods, 6)

        offenders, _loads, stores, _methods = _payload_guard(
            source("_discard_payload"))
        self.assertEqual(stores, 0, "the store was counted as exempt")
        self.assertEqual([(name, why) for name, _line, why in offenders],
                         [("_discard_payload",
                           "a payload store outside __exit__; the teardown "
                           "exemption does not travel")])

    def test_a_writer_can_derive_the_declared_length_without_it(self):
        """The replacement, exercised: length from the attested record, and the
        written count reconciled against it."""
        ring = _ring()
        with ring.attest_window(_window(ring)) as attested:
            declared = attested.sample_count * BYTES_PER_SAMPLE
            written, payload = _drain(attested)
        self.assertEqual(declared, CAPACITY * BYTES_PER_SAMPLE)
        self.assertEqual(written, declared)
        self.assertEqual(len(payload), declared)

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
