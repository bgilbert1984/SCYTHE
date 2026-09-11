"""Slice 7: reconciliation, publication, and which generation is real."""

import ast
import json
import os
import tempfile
import unittest

import scythe_promotion_lineage as lineage_module
from scythe_promotion_ledger_ownership import LedgerOwnership
from scythe_promotion_ledger_store import (
    AVAILABLE, COMMITTED, FAILED, RECONCILED_COMMITTED, RECONCILED_RELEASED,
    read_ledger,
)
from scythe_promotion_ledger_writer import LedgerWriter
from scythe_promotion_lineage import (
    FSYNC_DIRECTORY, FSYNC_FILE, GENERATION_CHAIN_BROKEN,
    GENERATION_LINEAGE_FORKED, GENERATION_PUBLICATION_UNCERTAIN,
    OPEN_EXCLUSIVE, PUBLICATION_STEPS, RENAME, WRITE_HEADER, Lineage,
    LineageError, Syscalls, generation_path,
)
from scythe_promotion_reconciliation import (
    ADAPTER_DENIED_CREATION, CEILING_REACHED, GRAPH_RECORD_FOUND,
    GRAPH_RECORD_NOT_FOUND, LEDGER_TORN, NOT_RECONCILABLE, OperatorRequest,
    ReconciliationRefused, close_generation, reconcile_identity,
)

GOOD_MOUNTS = (("/", "ext4"),)
OWNER = {"boot_id": "boot-a", "pid": 4242, "start_ticks": 99}
OPERATOR = OperatorRequest(operator="operator-1", request_id="req-1")


class Recorder(Syscalls):
    """Records the order of the five steps, and can fail after any of them.

    The order is the assertion. A correct-looking file proves nothing: every
    wrong ordering produces one on a machine that did not crash.
    """

    def __init__(self, fail_at=None):
        self.calls = []
        self.fail_at = fail_at

    def _step(self, name):
        self.calls.append(name)
        if self.fail_at == name:
            raise OSError(5, f"injected failure at {name}")

    def open_exclusive(self, path):
        self.calls.append(OPEN_EXCLUSIVE)
        fd = super().open_exclusive(path)
        if self.fail_at == OPEN_EXCLUSIVE:
            os.close(fd)
            raise OSError(5, "injected failure at OPEN_EXCLUSIVE")
        return fd

    def write(self, fd, data):
        super().write(fd, data)
        self._step(WRITE_HEADER)

    def fsync(self, fd):
        super().fsync(fd)
        self._step(FSYNC_FILE)

    def rename(self, source, target):
        super().rename(source, target)
        self._step(RENAME)

    def fsync_directory(self, path):
        super().fsync_directory(path)
        self._step(FSYNC_DIRECTORY)


class LineageTestCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.dir = self._dir.name
        self.root = os.path.join(self.dir, "promotion")
        self.owner = LedgerOwnership(lineage_root=self.root, mounts=GOOD_MOUNTS,
                                     refused_prefixes=(),
                                     devices={os.stat(self.dir).st_dev: "ext4"})
        self.addCleanup(self.owner.release)
        self.assertTrue(self.owner.acquire())

    def _writer(self, ordinal=0):
        return LedgerWriter(path=generation_path(self.root, ordinal),
                            ownership=self.owner.scope, refused_prefixes=())

    def _generation(self, ordinal=0, identifier="gen-1"):
        writer = self._writer(ordinal)
        with writer.owned() as session:
            session.initialize()
            session.declare_generation(identifier, OWNER)
        return writer

    def _reserve(self, writer, identity, outcome=None):
        with writer.owned() as session:
            seq = session.append_reserved({"identity": identity})
            if outcome is not None:
                session.append_terminal(outcome, seq)
        return seq

    def _lineage(self, syscalls=None):
        return Lineage(root=self.root,
                       syscalls=syscalls if syscalls else Syscalls())


class PublicationOrderTests(LineageTestCase):
    """§13e F.6: five steps, and the order is what is asserted."""

    def test_publication_performs_the_five_steps_in_order(self):
        self._generation()
        recorder = Recorder()
        line = self._lineage(recorder)
        close_generation(line, OWNER, generation="gen-2", reason=LEDGER_TORN,
                         request=OPERATOR, nonce="n1")
        self.assertEqual(recorder.calls, list(PUBLICATION_STEPS))

    def test_the_file_is_synced_before_the_rename(self):
        """A rename whose target is not durable can survive a crash as a
        correctly-named file full of nothing."""
        self._generation()
        recorder = Recorder()
        close_generation(self._lineage(recorder), OWNER, generation="gen-2",
                         reason=LEDGER_TORN, request=OPERATOR, nonce="n1")
        self.assertLess(recorder.calls.index(FSYNC_FILE),
                        recorder.calls.index(RENAME))

    def test_the_directory_is_synced_after_the_rename(self):
        """The rename's visibility is not durable until the directory is."""
        self._generation()
        recorder = Recorder()
        close_generation(self._lineage(recorder), OWNER, generation="gen-2",
                         reason=LEDGER_TORN, request=OPERATOR, nonce="n1")
        self.assertLess(recorder.calls.index(RENAME),
                        recorder.calls.index(FSYNC_DIRECTORY))

    def test_nothing_is_written_under_the_final_name_before_the_rename(self):
        """The reason a temporary exists: a partial header under the published
        name is the malformed candidate D.3 cannot classify."""
        self._generation()
        final = generation_path(self.root, 1)
        seen = []

        class Watcher(Recorder):
            def write(self, fd, data):
                seen.append(os.path.exists(final))
                super().write(fd, data)

        close_generation(self._lineage(Watcher()), OWNER, generation="gen-2",
                         reason=LEDGER_TORN, request=OPERATOR, nonce="n1")
        self.assertEqual(seen, [False])


class PublicationFailureTests(LineageTestCase):
    """Failure after each stage leaves the predecessor authoritative, unless
    publication completed durably."""

    def _fail_at(self, step):
        self._generation()
        recorder = Recorder(fail_at=step)
        line = self._lineage(recorder)
        with self.assertRaises(Exception) as caught:
            close_generation(line, OWNER, generation="gen-2",
                             reason=LEDGER_TORN, request=OPERATOR, nonce="n1")
        return line, recorder, caught.exception

    def test_a_failure_at_each_stage_before_the_rename_supersedes_nothing(self):
        for step in (OPEN_EXCLUSIVE, WRITE_HEADER, FSYNC_FILE):
            with self.subTest(step=step):
                self.setUp()
                line, _recorder, _exc = self._fail_at(step)
                self.assertEqual(line.authoritative().identifier, "gen-1")
                self.assertFalse(os.path.exists(generation_path(self.root, 1)))

    def test_a_failure_at_the_rename_supersedes_nothing(self):
        line, _recorder, _exc = self._fail_at(RENAME)
        # The rename itself ran before the injected failure, so the file exists
        # -- and it is a complete, valid successor. This is the case the
        # directory fsync is about, not a case the predecessor survives.
        self.assertEqual(line.authoritative().identifier, "gen-2")

    def test_a_failure_at_the_directory_sync_is_uncertain_not_a_success(self):
        line, recorder, error = self._fail_at(FSYNC_DIRECTORY)
        self.assertEqual(error.code, GENERATION_PUBLICATION_UNCERTAIN)
        self.assertEqual(recorder.calls, list(PUBLICATION_STEPS))

    def test_a_partial_temporary_is_not_a_generation(self):
        line, _recorder, _exc = self._fail_at(WRITE_HEADER)
        partials = [n for n in os.listdir(self.dir) if n.endswith(".partial")]
        self.assertEqual(len(partials), 1)
        self.assertEqual(len(line.published()), 1)

    def test_a_leftover_temporary_is_preserved_by_a_later_attempt(self):
        """Never interpreted, never repaired, never deleted -- it may be the
        only record of what someone was doing when the machine stopped."""
        line, _recorder, _exc = self._fail_at(FSYNC_FILE)
        leftover = [n for n in os.listdir(self.dir) if n.endswith(".partial")][0]
        with open(os.path.join(self.dir, leftover), "rb") as handle:
            before = handle.read()
        close_generation(self._lineage(), OWNER, generation="gen-2",
                         reason=LEDGER_TORN,
                         request=OperatorRequest(operator="op", request_id="r2"),
                         nonce="n2")
        with open(os.path.join(self.dir, leftover), "rb") as handle:
            self.assertEqual(handle.read(), before)


class AuthoritativeSelectionTests(LineageTestCase):
    """§13e F.6a."""

    def _close(self, request_id="req-1", generation="gen-2", nonce="n1"):
        return close_generation(
            self._lineage(), OWNER, generation=generation, reason=LEDGER_TORN,
            request=OperatorRequest(operator="op", request_id=request_id),
            nonce=nonce)

    def test_one_generation_is_authoritative_on_its_own(self):
        self._generation()
        self.assertEqual(self._lineage().authoritative().identifier, "gen-1")

    def test_the_successor_becomes_authoritative(self):
        self._generation()
        self._close()
        self.assertEqual(self._lineage().authoritative().identifier, "gen-2")

    def test_a_headerless_file_is_not_a_generation(self):
        self._generation()
        with open(generation_path(self.root, 1), "wb"):
            pass
        self.assertEqual(self._lineage().authoritative().identifier, "gen-1")

    def test_a_fork_is_refused_and_not_resolved(self):
        """Two unsuperseded generations. Never chosen between by timestamp,
        filename or directory order."""
        self._generation()
        self._generation(ordinal=1, identifier="gen-other")
        with self.assertRaises(LineageError) as caught:
            self._lineage().authoritative()
        self.assertEqual(caught.exception.code, GENERATION_LINEAGE_FORKED)
        self.assertIn("timestamp", caught.exception.detail)

    def test_closing_again_supersedes_the_new_head_and_is_not_a_fork(self):
        """A second closure is legitimate: it acts on the current head. The
        first version of this test assumed otherwise and was wrong."""
        self._generation()
        self._close(request_id="req-1")
        self._close(request_id="req-2", generation="gen-3", nonce="n2")
        self.assertEqual(self._lineage().authoritative().identifier, "gen-3")
        self.assertEqual(len(self._lineage().chain()), 3)

    def test_two_successors_of_one_predecessor_are_a_fork(self):
        """The real case: someone published against a stale predecessor."""
        self._generation()
        self._close(request_id="req-1")
        rival = _forge_successor(self.root, ordinal=2, generation="gen-rival",
                                 predecessor="gen-1")
        self.assertTrue(os.path.exists(rival))
        with self.assertRaises(LineageError) as caught:
            self._lineage().authoritative()
        self.assertEqual(caught.exception.code, GENERATION_LINEAGE_FORKED)

    def test_a_second_successor_of_the_current_head_is_refused_at_publication(self):
        self._generation()
        line = self._lineage()
        head = line.authoritative()
        _forge_successor(self.root, ordinal=1, generation="gen-rival",
                         predecessor="gen-1")
        with self.assertRaises(LineageError) as caught:
            line.find_successor(head, "req-9")
        self.assertEqual(caught.exception.code, GENERATION_LINEAGE_FORKED)

    def test_a_missing_predecessor_breaks_the_chain(self):
        self._generation()
        self._close()
        os.unlink(generation_path(self.root, 0))
        with self.assertRaises(LineageError) as caught:
            self._lineage().chain()
        self.assertEqual(caught.exception.code, GENERATION_CHAIN_BROKEN)


class RediscoveryTests(LineageTestCase):
    """§13e F.6/F.7: a retry finds, and does not republish."""

    def test_the_same_request_rediscovers_its_successor(self):
        self._generation()
        first = close_generation(self._lineage(), OWNER, generation="gen-2",
                                 reason=LEDGER_TORN, request=OPERATOR, nonce="n1")
        self.assertFalse(first["rediscovered"])
        second = close_generation(self._lineage(), OWNER, generation="gen-2",
                                  reason=LEDGER_TORN, request=OPERATOR, nonce="n2")
        self.assertTrue(second["rediscovered"])
        self.assertEqual(second["path"], first["path"])
        self.assertEqual(len(self._lineage().published()), 2)

    def test_rediscovery_creates_no_temporary(self):
        self._generation()
        close_generation(self._lineage(), OWNER, generation="gen-2",
                         reason=LEDGER_TORN, request=OPERATOR, nonce="n1")
        before = sorted(os.listdir(self.dir))
        close_generation(self._lineage(), OWNER, generation="gen-2",
                         reason=LEDGER_TORN, request=OPERATOR, nonce="n2")
        self.assertEqual(sorted(os.listdir(self.dir)), before)


class ImmutabilityTests(LineageTestCase):
    """§13e F.4, F.5."""

    def test_the_predecessor_is_byte_identical_after_closure(self):
        writer = self._generation()
        self._reserve(writer, "promotion:aa", COMMITTED)
        path = generation_path(self.root, 0)
        with open(path, "rb") as handle:
            before = handle.read()
        stat_before = os.stat(path)
        close_generation(self._lineage(), OWNER, generation="gen-2",
                         reason=LEDGER_TORN, request=OPERATOR, nonce="n1")
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), before)
        self.assertEqual(os.stat(path).st_size, stat_before.st_size)

    def test_the_successor_records_the_predecessors_length_and_digest(self):
        writer = self._generation()
        self._reserve(writer, "promotion:aa", COMMITTED)
        close_generation(self._lineage(), OWNER, generation="gen-2",
                         reason=LEDGER_TORN, request=OPERATOR, nonce="n1")
        head = self._lineage().authoritative()
        block = head.supersedes
        self.assertEqual(block["generation"], "gen-1")
        self.assertEqual(block["bytes"],
                         os.path.getsize(generation_path(self.root, 0)))
        self.assertTrue(block["digest"].startswith("blake2s:"))
        self.assertEqual(block["closed_because"], LEDGER_TORN)

    def test_the_fence_is_the_union_over_the_chain(self):
        writer = self._generation()
        self._reserve(writer, "promotion:aa", COMMITTED)
        close_generation(self._lineage(), OWNER, generation="gen-2",
                         reason=CEILING_REACHED, request=OPERATOR, nonce="n1")
        self.assertIn("promotion:aa", self._lineage().fenced())

    def test_a_torn_predecessor_is_still_read_for_fencing(self):
        """Torn means not appendable, never not readable."""
        writer = self._generation()
        self._reserve(writer, "promotion:aa", COMMITTED)
        with open(generation_path(self.root, 0), "ab") as handle:
            handle.write(b"0000004a 1234abcd {\"kind\":\"RESER")
        close_generation(self._lineage(), OWNER, generation="gen-2",
                         reason=LEDGER_TORN, request=OPERATOR, nonce="n1")
        self.assertIn("promotion:aa", self._lineage().fenced())


class AuthoritySplitTests(LineageTestCase):
    """§13e F.1, F.2: who may release what, and on whose evidence."""

    def _prepare(self, identity, outcome=None):
        writer = self._generation()
        self._reserve(writer, identity, outcome)
        return writer

    def _reconcile(self, writer, identity, evidence, request=OPERATOR):
        read = read_ledger(writer.path)
        with writer.owned() as session:
            return reconcile_identity(session, read, identity,
                                      evidence=evidence, request=request)

    def test_a_committed_identity_cannot_be_reconciled(self):
        writer = self._prepare("promotion:aa", COMMITTED)
        with self.assertRaises(ReconciliationRefused) as caught:
            self._reconcile(writer, "promotion:aa", GRAPH_RECORD_FOUND)
        self.assertEqual(caught.exception.code, NOT_RECONCILABLE)

    def test_a_failed_reservation_is_released_on_the_adapters_attestation(self):
        writer = self._prepare("promotion:bb", FAILED)
        result = self._reconcile(writer, "promotion:bb", ADAPTER_DENIED_CREATION)
        self.assertEqual(result["kind"], RECONCILED_RELEASED)
        read = read_ledger(writer.path)
        self.assertIn("promotion:bb", read.released)
        self.assertNotIn("promotion:bb", read.fenced)

    def test_a_failed_reservation_is_not_released_without_that_attestation(self):
        writer = self._prepare("promotion:bb", FAILED)
        for evidence in (GRAPH_RECORD_FOUND, GRAPH_RECORD_NOT_FOUND):
            with self.subTest(evidence=evidence):
                with self.assertRaises(ReconciliationRefused) as caught:
                    self._reconcile(writer, "promotion:bb", evidence)
                self.assertEqual(caught.exception.code, NOT_RECONCILABLE)

    def test_an_unresolved_reservation_needs_an_operators_inspection(self):
        writer = self._prepare("promotion:cc")
        with self.assertRaises(ReconciliationRefused) as caught:
            self._reconcile(writer, "promotion:cc", ADAPTER_DENIED_CREATION)
        self.assertEqual(caught.exception.code, NOT_RECONCILABLE)
        self.assertIn("never attested", caught.exception.detail)

    def test_an_unresolved_reservation_found_in_the_graph_stays_fenced(self):
        writer = self._prepare("promotion:cc")
        result = self._reconcile(writer, "promotion:cc", GRAPH_RECORD_FOUND)
        self.assertEqual(result["kind"], RECONCILED_COMMITTED)
        read = read_ledger(writer.path)
        self.assertIn("promotion:cc", read.committed)
        self.assertIn("promotion:cc", read.fenced)

    def test_an_unresolved_reservation_absent_from_the_graph_is_released(self):
        writer = self._prepare("promotion:cc")
        self._reconcile(writer, "promotion:cc", GRAPH_RECORD_NOT_FOUND)
        read = read_ledger(writer.path)
        self.assertNotIn("promotion:cc", read.fenced)

    def test_the_record_names_the_evidence_and_the_operator(self):
        writer = self._prepare("promotion:cc")
        self._reconcile(writer, "promotion:cc", GRAPH_RECORD_NOT_FOUND)
        payloads = _payloads(writer.path)
        record = [p for p in payloads if p["kind"] == RECONCILED_RELEASED][0]
        self.assertEqual(record["evidence"], GRAPH_RECORD_NOT_FOUND)
        self.assertEqual(record["operator"], "operator-1")
        self.assertEqual(record["request_id"], "req-1")

    def test_reconciling_twice_is_refused_by_the_reader(self):
        writer = self._prepare("promotion:cc")
        self._reconcile(writer, "promotion:cc", GRAPH_RECORD_NOT_FOUND)
        read = read_ledger(writer.path)
        with writer.owned() as session:
            session.append_reconciliation(RECONCILED_RELEASED, 1,
                                          {"evidence": GRAPH_RECORD_NOT_FOUND})
        self.assertEqual(read_ledger(writer.path).readability, "LEDGER_UNREADABLE")

    def test_evidence_outside_the_closed_set_is_refused(self):
        writer = self._prepare("promotion:cc")
        with self.assertRaises(ReconciliationRefused):
            self._reconcile(writer, "promotion:cc", "IT SEEMED FINE TO ME")

    def test_there_is_no_notes_field(self):
        source = open(
            __import__("scythe_promotion_reconciliation").__file__,
            encoding="utf-8").read()
        tree = ast.parse(source)
        keys = {n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        for absent in ("notes", "note", "comment", "reason_text", "message"):
            self.assertNotIn(absent, keys, absent)

    def test_an_operator_identifier_is_bounded(self):
        with self.assertRaises(ReconciliationRefused):
            OperatorRequest(operator="x" * 65, request_id="r")
        with self.assertRaises(ReconciliationRefused):
            OperatorRequest(operator="", request_id="r")


class ScopeTests(unittest.TestCase):
    def setUp(self):
        with open(lineage_module.__file__, encoding="utf-8") as handle:
            self.tree = ast.parse(handle.read())

    def test_the_lineage_never_removes_a_temporary(self):
        called = {n.func.attr for n in ast.walk(self.tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        for verb in ("unlink", "remove", "rmtree", "truncate", "ftruncate"):
            self.assertNotIn(verb, called, verb)

    def test_nothing_here_promotes_or_calls_an_adapter(self):
        modules = {n.module for n in ast.walk(self.tree)
                   if isinstance(n, ast.ImportFrom)}
        self.assertNotIn("scythe_promotion_ledger", modules)
        self.assertNotIn("scythe_promotion_policy", modules)


def _forge_successor(root, *, ordinal, generation, predecessor):
    """A successor written directly, to build a fork the protocol prevents."""
    from scythe_promotion_ledger_store import (
        FRAME_VERSION, HEADER, LEDGER_SCHEMA, frame_of,
    )
    path = generation_path(root, ordinal)
    with open(path, "wb") as handle:
        handle.write(frame_of({
            "kind": HEADER, "seq": 0, "schema": LEDGER_SCHEMA,
            "frame_version": FRAME_VERSION, "generation": generation,
            "owner": OWNER,
            "supersedes": {"generation": predecessor, "path": "x", "bytes": 1,
                           "digest": "blake2s:00", "closed_because": "LEDGER_TORN",
                           "closed_by": "rival", "request_id": "rival-req"}}))
    return path


def _payloads(path):
    with open(path, "rb") as handle:
        data = handle.read()
    return [json.loads(line[18:].decode("utf-8"))
            for line in data.split(b"\n") if line]


if __name__ == "__main__":
    unittest.main()
