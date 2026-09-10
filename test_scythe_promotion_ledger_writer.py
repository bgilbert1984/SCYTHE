"""Slice 6: the write path, and the gate it cannot open."""

import ast
import json
import os
import tempfile
import unittest

import scythe_promotion_ledger_writer as writer_module
from scythe_promotion_ledger_store import (
    AVAILABLE, COMMITTED, FAILED, FRAME_VERSION, HEADER, LEDGER_SCHEMA,
    LEDGER_TORN, LEDGER_UNAVAILABLE, LEDGER_UNREADABLE, RESERVED,
    frame_of, read_ledger,
)
from scythe_promotion_ledger_writer import (
    APPEND_REFUSALS, LEDGER_GENERATION_UNDECLARED, LEDGER_NOT_OWNED,
    LedgerWriteRefused, LedgerWriter, OwnershipScope, no_ownership,
)

OWNER = {"boot_id": "boot-a", "pid": 4242, "start_ticks": 99}


def granting(path):
    """The test seam. Slice 6b's producer holds flock for the same span."""
    return OwnershipScope(path)


def _reservation(identity):
    return {"identity": identity, "target_graph": "scythe.graphops.evidence",
            "record_class": "INVARIANT_FINDING",
            "incarnation": dict(OWNER), "monotonic_ns": 1,
            "utc_display": "2026-09-10T00:00:00Z"}


class WriterTestCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.dir = self._dir.name
        self.path = os.path.join(self.dir, "ledger.jsonl")

    def _writer(self, ownership=granting):
        return LedgerWriter(path=self.path, ownership=ownership,
                            refused_prefixes=())

    def _initialized(self):
        with self._writer().owned() as session:
            session.initialize()
            session.declare_generation("gen-1", OWNER)
        return self._writer()


class GateTests(WriterTestCase):
    """The append path is unreachable without an injected live scope."""

    def test_the_default_producer_produces_nothing(self):
        self.assertIsNone(no_ownership(self.path))
        self.assertIs(LedgerWriter(path=self.path).ownership, no_ownership)

    def test_owning_refuses_without_a_producer(self):
        with self.assertRaises(LedgerWriteRefused) as caught:
            with LedgerWriter(path=self.path).owned():
                self.fail("the session must not open")
        self.assertEqual(caught.exception.code, LEDGER_NOT_OWNED)

    def test_no_file_is_created_by_a_refused_session(self):
        with self.assertRaises(LedgerWriteRefused):
            with LedgerWriter(path=self.path).owned():
                pass
        self.assertFalse(os.path.exists(self.path))

    def test_a_session_retained_past_its_scope_is_inert(self):
        """§13c D.4: a reference held past the owning span answers *no*."""
        writer = self._initialized()
        with writer.owned() as session:
            escaped = session
            escaped.append_reserved(_reservation("promotion:aa"))
        before = _bytes(self.path)
        with self.assertRaises(LedgerWriteRefused) as caught:
            escaped.append_reserved(_reservation("promotion:bb"))
        self.assertEqual(caught.exception.code, LEDGER_NOT_OWNED)
        self.assertEqual(_bytes(self.path), before)

    def test_a_scope_expires_even_when_the_session_raised(self):
        """A session that survived its own failure would be a scope whose
        lifetime the caller decides."""
        writer = self._initialized()
        escaped = []
        with self.assertRaises(ValueError):
            with writer.owned() as session:
                escaped.append(session)
                raise ValueError("something else went wrong")
        self.assertFalse(escaped[0].scope.live)

    def test_a_scope_for_one_ledger_does_not_admit_a_write_to_another(self):
        other = os.path.join(self.dir, "other.jsonl")
        self._initialized()
        writer = LedgerWriter(path=self.path, refused_prefixes=(),
                              ownership=lambda _p: OwnershipScope(other))
        with self.assertRaises(LedgerWriteRefused) as caught:
            with writer.owned() as session:
                session.append_reserved(_reservation("promotion:aa"))
        self.assertEqual(caught.exception.code, LEDGER_NOT_OWNED)

    def test_status_declares_that_no_production_producer_exists(self):
        status = LedgerWriter(path=self.path).status()
        self.assertEqual(status["production_ownership_producer"],
                         "NOT_IMPLEMENTED")
        self.assertFalse(status["repairs_torn_ledgers"])
        self.assertFalse(status["declares_generations_implicitly"])
        json.dumps(status)


class RoundTripTests(WriterTestCase):
    """What the writer writes, slice 5's reader reads -- unchanged."""

    def test_a_written_ledger_is_read_by_the_merged_reader(self):
        writer = self._initialized()
        with writer.owned() as session:
            first = session.append_reserved(_reservation("promotion:aa"))
            session.append_terminal(COMMITTED, first)
            second = session.append_reserved(_reservation("promotion:bb"))
            session.append_terminal(FAILED, second)
            session.append_reserved(_reservation("promotion:cc"))
        read = read_ledger(self.path)
        self.assertEqual(read.readability, AVAILABLE)
        self.assertTrue(read.header_present)
        self.assertEqual(read.generation, "gen-1")
        self.assertEqual(read.committed, ("promotion:aa",))
        self.assertEqual(read.write_failed, ("promotion:bb",))
        self.assertEqual(read.unresolved, ("promotion:cc",))
        self.assertEqual(read.reservations_total, 3)

    def test_the_reader_was_not_changed_to_accept_it(self):
        """The bytes are framed by the reader's own frame_of. One
        implementation of the format means no second opinion about it."""
        writer = self._initialized()
        with writer.owned() as session:
            session.append_reserved(_reservation("promotion:aa"))
        with open(self.path, "rb") as handle:
            lines = [line for line in handle.read().split(b"\n") if line]
        for line in lines:
            payload = json.loads(line[18:].decode("utf-8"))
            self.assertEqual(line + b"\n", frame_of(payload))

    def test_a_failed_write_is_never_readable_as_a_promotion(self):
        writer = self._initialized()
        with writer.owned() as session:
            seq = session.append_reserved(_reservation("promotion:bb"))
            session.append_terminal(FAILED, seq)
        read = read_ledger(self.path)
        self.assertNotIn("promotion:bb", read.committed)
        self.assertIn("promotion:bb", read.fenced)

    def test_an_unresolved_reservation_is_never_readable_as_a_promotion(self):
        writer = self._initialized()
        with writer.owned() as session:
            session.append_reserved(_reservation("promotion:cc"))
        read = read_ledger(self.path)
        self.assertEqual(read.committed, ())
        self.assertEqual(read.unresolved, ("promotion:cc",))

    def test_free_text_never_reaches_a_durable_record(self):
        """Exception messages, returned values and adapter prose have no field
        here to arrive in."""
        writer = self._initialized()
        with writer.owned() as session:
            session.append_reserved(_reservation("promotion:aa"))
        blob = _bytes(self.path).decode("utf-8")
        for leak in ("token=", "hunter2", "Traceback", "https://"):
            self.assertNotIn(leak, blob)


class SequenceTests(WriterTestCase):
    """§13c D.1."""

    def test_one_sequence_across_every_kind_strictly_increasing(self):
        writer = self._initialized()
        with writer.owned() as session:
            reserved = session.append_reserved(_reservation("promotion:aa"))
            terminal = session.append_terminal(COMMITTED, reserved)
        seqs = [p["seq"] for p in _payloads(self.path)]
        self.assertEqual(seqs, sorted(set(seqs)))
        self.assertEqual(seqs, [0, reserved, terminal])
        self.assertEqual(read_ledger(self.path).readability, AVAILABLE)

    def test_next_seq_is_last_seq_plus_one_and_not_a_count(self):
        writer = self._initialized()
        with writer.owned() as session:
            session._next_seq = 40      # a crashed writer took 1..39
            session.append_reserved(_reservation("promotion:aa"))
        with self._writer().owned() as session:
            self.assertEqual(session.next_seq(), 41)

    def test_a_gap_is_legal_and_the_reader_accepts_it(self):
        writer = self._initialized()
        with writer.owned() as session:
            session._next_seq = 90
            session.append_reserved(_reservation("promotion:aa"))
        self.assertEqual(read_ledger(self.path).readability, AVAILABLE)
        with self._writer().owned() as session:
            self.assertGreater(session.next_seq(), 90)

    def test_the_number_is_rebuilt_from_the_file_after_restart(self):
        """§10. A counter kept beside the ledger is a second answer that can
        disagree with the first.

        The gap is load-bearing. Without one, a count of records and the highest
        sequence number agree, and the test passes against an implementation
        that counts -- which is the implementation D.1 forbids, because counting
        reissues a number a crashed writer already took.
        """
        writer = self._initialized()
        with writer.owned() as session:
            session._next_seq = 70      # a crashed writer took the numbers below
            session.append_reserved(_reservation("promotion:aa"))
        highest = max(p["seq"] for p in _payloads(self.path))
        self.assertGreater(highest, len(_payloads(self.path)))
        fresh = LedgerWriter(path=self.path, ownership=granting,
                             refused_prefixes=())
        with fresh.owned() as session:
            self.assertEqual(session.next_seq(), highest + 1)

    def test_allocation_and_append_share_one_scope(self):
        """The span D.4 protects: no gap between proving ownership and using
        the number that proof authorised."""
        writer = self._initialized()
        with writer.owned() as session:
            taken = session.next_seq()
            self.assertEqual(session.append_reserved(_reservation("x")), taken)


class TornLedgerTests(WriterTestCase):
    """§13c D.2: append-ineligible, and never repaired."""

    def _torn(self):
        writer = self._initialized()
        with writer.owned() as session:
            session.append_reserved(_reservation("promotion:aa"))
        with open(self.path, "ab") as handle:
            handle.write(b"0000004a 1234abcd {\"kind\":\"RESER")
        return writer

    def test_a_torn_ledger_refuses_every_append(self):
        writer = self._torn()
        self.assertEqual(read_ledger(self.path).readability, LEDGER_TORN)
        with self.assertRaises(LedgerWriteRefused) as caught:
            with writer.owned() as session:
                session.append_reserved(_reservation("promotion:bb"))
        self.assertEqual(caught.exception.code, LEDGER_TORN)

    def test_a_refused_append_leaves_the_file_byte_identical(self):
        writer = self._torn()
        before = _bytes(self.path)
        stat_before = os.stat(self.path)
        for attempt in (lambda s: s.append_reserved(_reservation("promotion:bb")),
                        lambda s: s.append_terminal(COMMITTED, 1),
                        lambda s: s.declare_generation("gen-2", OWNER),
                        lambda s: s.next_seq()):
            with self.assertRaises(LedgerWriteRefused):
                with writer.owned() as session:
                    attempt(session)
        self.assertEqual(_bytes(self.path), before)
        self.assertEqual(os.stat(self.path).st_size, stat_before.st_size)

    def test_the_writer_never_truncates_repairs_or_normalizes(self):
        """A writer that repaired the file it is about to append to can destroy
        evidence to make its own next operation legal."""
        source = open(writer_module.__file__, encoding="utf-8").read()
        tree = ast.parse(source)
        called = {node.func.attr for node in ast.walk(tree)
                  if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Attribute)}
        for verb in ("truncate", "ftruncate", "remove", "unlink", "rename",
                     "replace", "rmtree", "seek"):
            self.assertNotIn(verb, called, verb)
        self.assertNotIn("O_TRUNC", source)
        self.assertNotIn("O_RDWR", source)

    def test_an_unreadable_ledger_refuses_too(self):
        writer = self._initialized()
        with open(self.path, "ab") as handle:
            handle.write(b"garbage that is not a frame\n")
            handle.write(frame_of({"kind": RESERVED, "seq": 9,
                                   "identity": "promotion:zz"}))
        self.assertEqual(read_ledger(self.path).readability, LEDGER_UNREADABLE)
        with self.assertRaises(LedgerWriteRefused) as caught:
            with writer.owned() as session:
                session.append_reserved(_reservation("promotion:bb"))
        self.assertEqual(caught.exception.code, LEDGER_UNREADABLE)


class GenerationTests(WriterTestCase):
    """§13c D.3: never implicit, never inferred, never adopted."""

    def test_initialize_creates_an_empty_ledger_and_declares_nothing(self):
        with self._writer().owned() as session:
            self.assertTrue(session.initialize())
        read = read_ledger(self.path)
        self.assertEqual(read.readability, AVAILABLE)
        self.assertFalse(read.header_present)
        self.assertIsNone(read.generation)
        self.assertEqual(os.path.getsize(self.path), 0)

    def test_an_append_to_a_generationless_ledger_is_refused(self):
        with self._writer().owned() as session:
            session.initialize()
        before = _bytes(self.path)
        with self.assertRaises(LedgerWriteRefused) as caught:
            with self._writer().owned() as session:
                session.append_reserved(_reservation("promotion:aa"))
        self.assertEqual(caught.exception.code, LEDGER_GENERATION_UNDECLARED)
        self.assertEqual(_bytes(self.path), before)
        self.assertEqual(os.path.getsize(self.path), 0)

    def test_the_refusal_is_answered_and_not_removed(self):
        """A writer that started a generation on its own would clear the
        refusal by removing the condition rather than answering it."""
        with self._writer().owned() as session:
            session.initialize()
        with self.assertRaises(LedgerWriteRefused):
            with self._writer().owned() as session:
                session.append_reserved(_reservation("promotion:aa"))
        self.assertFalse(read_ledger(self.path).header_present)
        with self._writer().owned() as session:
            session.declare_generation("gen-1", OWNER)
        self.assertEqual(read_ledger(self.path).generation, "gen-1")
        with self._writer().owned() as session:
            session.append_reserved(_reservation("promotion:aa"))
        self.assertEqual(read_ledger(self.path).unresolved, ("promotion:aa",))

    def test_a_generation_is_never_derived(self):
        with self._writer().owned() as session:
            session.initialize()
            for bad in (None, "", 7):
                with self.assertRaises(LedgerWriteRefused):
                    session.declare_generation(bad, OWNER)

    def test_a_second_generation_is_refused(self):
        writer = self._initialized()
        with self.assertRaises(LedgerWriteRefused) as caught:
            with writer.owned() as session:
                session.declare_generation("gen-2", OWNER)
        self.assertEqual(caught.exception.code, LEDGER_GENERATION_UNDECLARED)
        self.assertEqual(read_ledger(self.path).generation, "gen-1")

    def test_the_header_names_the_owner_rather_than_inferring_one(self):
        self._initialized()
        header = _payloads(self.path)[0]
        self.assertEqual(header["kind"], HEADER)
        self.assertEqual(header["owner"], OWNER)
        self.assertEqual(header["schema"], LEDGER_SCHEMA)
        self.assertEqual(header["frame_version"], FRAME_VERSION)

    def test_an_owner_that_is_not_a_process_identity_is_refused(self):
        with self._writer().owned() as session:
            session.initialize()
            with self.assertRaises(LedgerWriteRefused):
                session.declare_generation("gen-1", {"pid": 1})

    def test_initialize_does_not_overwrite_an_existing_ledger(self):
        writer = self._initialized()
        before = _bytes(self.path)
        with writer.owned() as session:
            self.assertFalse(session.initialize())
        self.assertEqual(_bytes(self.path), before)


class ScopeTests(unittest.TestCase):
    """What this module is allowed to be."""

    def setUp(self):
        with open(writer_module.__file__, encoding="utf-8") as handle:
            self.source = handle.read()
        self.tree = ast.parse(self.source)

    def test_no_public_append_takes_an_ownership_flag(self):
        """§13c D.4. A parameter a caller can pass is one a caller can pass
        wrongly, and the call site does not show whether the claim was true."""
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            names = [a.arg for a in node.args.args + node.args.kwonlyargs]
            for name in names:
                self.assertNotIn("attested", name.lower(), node.name)
                self.assertNotIn("owned", name.lower(), node.name)
                self.assertNotIn("force", name.lower(), node.name)

    def test_every_append_path_requires_the_scope(self):
        sessions = [n for n in ast.walk(self.tree)
                    if isinstance(n, ast.ClassDef) and n.name == "_WriteSession"]
        self.assertEqual(len(sessions), 1)
        public = [n for n in sessions[0].body
                  if isinstance(n, ast.FunctionDef) and not n.name.startswith("__")]
        self.assertTrue(public)
        # _read and _require_appendable read; they mutate nothing and need no
        # gate. Everything that can reach the file does.
        for method in public:
            if method.name in ("_require_scope", "_read", "_require_appendable"):
                continue
            calls = {n.func.attr for n in ast.walk(method)
                     if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
            self.assertTrue(
                "_require_scope" in calls or "next_seq" in calls
                or "_take_seq" in calls or "_prepare" in calls or "_append" in calls,
                f"{method.name} reaches no gate")

    def test_the_module_takes_no_lock_of_its_own(self):
        """Read from the AST, not the text.

        `fcntl` appears in this module's own docstring, describing what slice 6b
        will do. A raw-text scan reports that as a violation -- the fifth time
        that false-positive class has surfaced in this repository, and the same
        shape PENDING_AMENDMENTS.md entry 5 records for the name check. Prose
        about a thing is not the thing.
        """
        imported = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            if isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
        self.assertNotIn("fcntl", imported)
        self.assertNotIn("threading", imported)
        called = {n.func.attr for n in ast.walk(self.tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        self.assertNotIn("flock", called)
        self.assertNotIn("lockf", called)

    def test_the_coordinator_is_not_imported(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ImportFrom):
                self.assertNotEqual(node.module, "scythe_promotion_ledger")

    def test_the_refusal_set_is_declared(self):
        self.assertIn(LEDGER_NOT_OWNED, APPEND_REFUSALS)
        self.assertIn(LEDGER_GENERATION_UNDECLARED, APPEND_REFUSALS)


def _bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


def _payloads(path):
    return [json.loads(line[18:].decode("utf-8"))
            for line in _bytes(path).split(b"\n") if line]


if __name__ == "__main__":
    unittest.main()
