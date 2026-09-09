"""The ledger read path. Slice 3: learn what the fence says, never move it."""

import ast
import inspect
import json
import os
import tempfile
import unittest

import scythe_promotion_ledger_store as store_module
from scythe_promotion_ledger_store import (
    ATTESTED_BY_FILESYSTEM_POLICY, AVAILABLE, COMMITTED, FAILED,
    FIDELITY_DEGRADED_NOT_ARMABLE, FIDELITY_FULL, FIDELITY_UNSEEDED, HEADER,
    LEDGER_TORN, LEDGER_UNAVAILABLE, LEDGER_UNREADABLE,
    LOCK_EXCLUSION_UNATTESTED, RESERVATION_DURABILITY_UNATTESTED, RESERVED,
    UNATTESTED, UNAVAILABLE, CapabilityAttestation, LedgerFrameError,
    LedgerStore, attest, filesystem_type, frame_of, parse_frame, read_ledger,
    refuse_location,
)

GOOD_MOUNTS = (("/", "ext4"), ("/state", "ext4"))
# Path-specific: only /mnt/c is unlisted, so it exercises longest-match lookup.
BAD_MOUNTS = (("/", "ext4"), ("/mnt/c", "drvfs"))
# Whole-tree: for store-level tests, whose ledger sits wherever tempfile put it.
UNLISTED_MOUNTS = (("/", "drvfs"),)


def _reserved(seq, identity):
    return {"kind": RESERVED, "seq": seq, "identity": identity,
            "target_graph": "scythe.graphops.evidence",
            "record_class": "INVARIANT_FINDING",
            "incarnation": {"boot_id": "b", "pid": 1, "start_ticks": 2},
            "monotonic_ns": 1000 * seq, "utc_display": "2026-09-09T00:00:00Z"}


def _ledger(*payloads, torn=None):
    body = b"".join(frame_of(p) for p in payloads)
    if torn is not None:
        body += torn
    return body


def _write(directory, body, name="ledger.jsonl"):
    path = os.path.join(directory, name)
    with open(path, "wb") as handle:
        handle.write(body)
    return path


class FramingTests(unittest.TestCase):
    def test_a_frame_round_trips(self):
        payload = _reserved(1, "promotion:aa")
        self.assertEqual(parse_frame(frame_of(payload).rstrip(b"\n")), payload)

    def test_truncation_is_caught_by_the_declared_length(self):
        line = frame_of(_reserved(1, "promotion:aa")).rstrip(b"\n")
        with self.assertRaises(LedgerFrameError) as caught:
            parse_frame(line[:-5])
        self.assertIn("bytes", str(caught.exception))

    def test_corruption_that_preserves_length_is_caught_by_the_checksum(self):
        """Which is why the frame carries both and not either."""
        line = bytearray(frame_of(_reserved(1, "promotion:aa")).rstrip(b"\n"))
        line[-3] = line[-3] ^ 0x01
        with self.assertRaises(LedgerFrameError) as caught:
            parse_frame(bytes(line))
        self.assertIn("checksum", str(caught.exception))

    def test_a_header_truncated_inside_itself_does_not_parse(self):
        for cut in (0, 3, 9, 17):
            with self.subTest(cut=cut), self.assertRaises(LedgerFrameError):
                parse_frame(frame_of(_reserved(1, "x")).rstrip(b"\n")[:cut])

    def test_an_unrecognised_record_kind_is_refused_not_skipped(self):
        """A ledger written by a coordinator that knows more kinds is not one
        this slice may read past. RECONCILED_RELEASED is the real case."""
        with self.assertRaises(LedgerFrameError) as caught:
            parse_frame(frame_of({"kind": "RECONCILED_RELEASED", "seq": 2,
                                  "reserves": 1}).rstrip(b"\n"))
        self.assertIn("does not understand", str(caught.exception))


class LocationTests(unittest.TestCase):
    def test_a_relative_path_is_refused(self):
        self.assertIn("PATH_NOT_ABSOLUTE", refuse_location("state/ledger"))

    def test_tmp_is_refused_however_it_is_mounted(self):
        """On tmpfs the ledger vanishes on reboot, which turns 'a missing
        ledger refuses ARMED' into 'ARMED always refuses after reboot'."""
        self.assertIn("PATH_UNDER_TMP", refuse_location("/tmp/ledger"))
        self.assertIn("PATH_UNDER_DEV_SHM", refuse_location("/dev/shm/ledger"))

    def test_the_working_tree_is_refused(self):
        refusals = refuse_location("/srv/SCYTHE/state", repo_root="/srv/SCYTHE")
        self.assertIn("PATH_INSIDE_WORKING_TREE", refusals)

    def test_a_working_tree_on_an_allowlisted_filesystem_is_still_refused(self):
        """Not a filesystem question. A checkout could move the fence."""
        attested = attest("/srv/SCYTHE/state", repo_root="/srv/SCYTHE",
                          mounts=(("/", "ext4"),))
        self.assertEqual(attested.observed_filesystem, "ext4")
        self.assertFalse(attested.armable)

    def test_an_ordinary_state_directory_is_not_refused(self):
        self.assertEqual(refuse_location("/var/lib/scythe", repo_root="/srv/SCYTHE"), ())

    def test_the_default_prefix_policy_is_the_strict_one(self):
        """The override exists for tests; the default must not have moved."""
        self.assertIn("/tmp", store_module.REFUSED_PREFIXES)
        self.assertIn("/dev/shm", store_module.REFUSED_PREFIXES)
        store = LedgerStore(path="/tmp/scythe/ledger.jsonl", mounts=GOOD_MOUNTS)
        self.assertIn("PATH_UNDER_TMP", store.attestation().location_refusals)
        self.assertFalse(store.attestation().armable)


class AttestationTests(unittest.TestCase):
    def test_the_filesystem_is_read_from_the_mount_table(self):
        self.assertEqual(filesystem_type("/mnt/c/state", mounts=BAD_MOUNTS), "drvfs")
        self.assertEqual(filesystem_type("/state/x", mounts=GOOD_MOUNTS), "ext4")

    def test_the_longest_matching_mount_point_wins(self):
        mounts = (("/", "ext4"), ("/mnt", "ext4"), ("/mnt/c", "drvfs"))
        self.assertEqual(filesystem_type("/mnt/c/deep/path", mounts=mounts), "drvfs")

    def test_an_unlisted_filesystem_attests_nothing(self):
        attested = attest("/mnt/c/state", mounts=BAD_MOUNTS)
        self.assertEqual(attested.lock_exclusion, UNATTESTED)
        self.assertEqual(attested.reservation_durability, UNATTESTED)
        self.assertFalse(attested.armable)

    def test_an_allowlisted_filesystem_attests_both(self):
        attested = attest("/state", mounts=GOOD_MOUNTS)
        self.assertEqual(attested.lock_exclusion, ATTESTED_BY_FILESYSTEM_POLICY)
        self.assertEqual(attested.reservation_durability,
                         ATTESTED_BY_FILESYSTEM_POLICY)
        self.assertTrue(attested.armable)

    def test_two_codes_are_published_not_one(self):
        """One lookup supplies both facts; they remain two claims."""
        refusals = attest("/mnt/c/state", mounts=BAD_MOUNTS).refusals()
        self.assertIn(LOCK_EXCLUSION_UNATTESTED, refusals)
        self.assertIn(RESERVATION_DURABILITY_UNATTESTED, refusals)

    def test_the_claims_are_separately_representable(self):
        """They fail together here and come apart on ordinary filesystems."""
        split = CapabilityAttestation(
            directory="/x", observed_filesystem="somefs",
            lock_exclusion=UNATTESTED,
            reservation_durability=ATTESTED_BY_FILESYSTEM_POLICY)
        self.assertEqual(split.refusals(), (LOCK_EXCLUSION_UNATTESTED,))
        self.assertFalse(split.armable)

    def test_the_observed_filesystem_is_recorded_not_only_the_verdict(self):
        published = attest("/mnt/c/state", mounts=BAD_MOUNTS).as_dict()
        self.assertEqual(published["observed_filesystem"], "drvfs")
        self.assertIn("ext4", published["allowlist"])

    def test_an_unknown_mount_is_refused_rather_than_assumed(self):
        attested = attest("/elsewhere", mounts=(("/nowhere", "ext4"),))
        self.assertIsNone(attested.observed_filesystem)
        self.assertFalse(attested.armable)


class ReadTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.dir = self._dir.name

    def test_an_absent_ledger_is_not_an_empty_one(self):
        read = read_ledger(os.path.join(self.dir, "nothing.jsonl"))
        self.assertEqual(read.readability, LEDGER_UNAVAILABLE)
        self.assertEqual(read.fenced, ())
        self.assertIn("does not create", read.detail)

    def test_the_read_path_never_creates_the_ledger(self):
        path = os.path.join(self.dir, "nothing.jsonl")
        read_ledger(path)
        LedgerStore(path=path, mounts=GOOD_MOUNTS).assessment()
        self.assertFalse(os.path.exists(path))

    def test_committed_failed_and_unresolved_are_reconstructed(self):
        path = _write(self.dir, _ledger(
            {"kind": HEADER, "generation": "g1", "schema": "x"},
            _reserved(1, "promotion:aa"), {"kind": COMMITTED, "seq": 2, "reserves": 1},
            _reserved(3, "promotion:bb"), {"kind": FAILED, "seq": 4, "reserves": 3},
            _reserved(5, "promotion:cc")))
        read = read_ledger(path)
        self.assertEqual(read.readability, AVAILABLE)
        self.assertEqual(read.generation, "g1")
        self.assertEqual(read.committed, ("promotion:aa",))
        self.assertEqual(read.write_failed, ("promotion:bb",))
        self.assertEqual(read.unresolved, ("promotion:cc",))

    def test_a_failed_write_still_fences(self):
        """accepted=False covers a rejection, a timeout, and a landed write
        whose acknowledgement was lost. They are indistinguishable."""
        path = _write(self.dir, _ledger(
            _reserved(1, "promotion:bb"), {"kind": FAILED, "seq": 2, "reserves": 1}))
        self.assertIn("promotion:bb", read_ledger(path).fenced)

    def test_c1_counts_reservations_not_confirmed_writes(self):
        path = _write(self.dir, _ledger(
            _reserved(1, "promotion:aa"), {"kind": FAILED, "seq": 2, "reserves": 1},
            _reserved(3, "promotion:bb"), {"kind": FAILED, "seq": 4, "reserves": 3}))
        read = read_ledger(path)
        self.assertEqual(read.committed, ())
        self.assertEqual(read.reservations_total, 2)

    def test_a_torn_tail_loads_as_unresolved_and_is_not_discarded(self):
        path = _write(self.dir, _ledger(
            _reserved(1, "promotion:aa"), {"kind": COMMITTED, "seq": 2, "reserves": 1},
            torn=b"0000004a 1234abcd {\"kind\":\"RESER"))
        read = read_ledger(path)
        self.assertTrue(read.torn_tail)
        self.assertEqual(read.readability, LEDGER_TORN)
        # Counted although its identity cannot be read: discarding it would
        # assert no reservation was made, which is write-first semantics.
        self.assertEqual(read.reservations_total, 2)
        self.assertIn("cannot be read", read.detail)

    def test_a_bad_record_in_the_middle_is_corruption_not_a_torn_tail(self):
        """The writer got past it, so it is not a crash artefact."""
        good = frame_of(_reserved(1, "promotion:aa"))
        broken = bytearray(frame_of(_reserved(3, "promotion:bb")))
        broken[-4] = broken[-4] ^ 0x01
        path = _write(self.dir, good + bytes(broken) + frame_of(
            _reserved(5, "promotion:cc")))
        read = read_ledger(path)
        self.assertEqual(read.readability, LEDGER_UNREADABLE)
        self.assertFalse(read.torn_tail)
        self.assertEqual(read.fenced, ())

    def test_a_terminal_record_for_no_reservation_is_unreadable(self):
        path = _write(self.dir, _ledger({"kind": COMMITTED, "seq": 2, "reserves": 9}))
        self.assertEqual(read_ledger(path).readability, LEDGER_UNREADABLE)

    def test_a_reservation_resolved_twice_is_unreadable(self):
        path = _write(self.dir, _ledger(
            _reserved(1, "promotion:aa"),
            {"kind": COMMITTED, "seq": 2, "reserves": 1},
            {"kind": FAILED, "seq": 3, "reserves": 1}))
        self.assertEqual(read_ledger(path).readability, LEDGER_UNREADABLE)

    def test_a_header_after_the_first_record_is_unreadable(self):
        path = _write(self.dir, _ledger(
            _reserved(1, "promotion:aa"), {"kind": HEADER, "generation": "g2"}))
        self.assertEqual(read_ledger(path).readability, LEDGER_UNREADABLE)

    def test_an_empty_ledger_is_readable_and_fences_nothing(self):
        path = _write(self.dir, b"")
        read = read_ledger(path)
        self.assertEqual(read.readability, AVAILABLE)
        self.assertEqual(read.fenced, ())


class ShadowTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.dir = self._dir.name
        self.path = _write(self.dir, _ledger(
            {"kind": HEADER, "generation": "g1"},
            _reserved(1, "promotion:aa"), {"kind": COMMITTED, "seq": 2, "reserves": 1},
            _reserved(3, "promotion:cc")))

    def _store(self, mounts=GOOD_MOUNTS, path=None):
        # The prefix policy is overridden because every real temporary file is
        # under a prefix it refuses. LocationTests pins the default.
        return LedgerStore(path=path or self.path, mounts=mounts,
                           refused_prefixes=())

    def test_shadow_seeds_from_real_history(self):
        """A SHADOW starting empty under-counts refusals and over-counts
        promotions -- it would measure a system that does not exist."""
        self.assertEqual(self._store().seed_for_shadow(),
                         ("promotion:aa", "promotion:cc"))

    def test_a_shadow_read_leaves_the_ledger_byte_identical(self):
        with open(self.path, "rb") as handle:
            before = handle.read()
        stat_before = os.stat(self.path)
        store = self._store()
        store.assessment()
        store.seed_for_shadow()
        store.shadow_fidelity()
        with open(self.path, "rb") as handle:
            after = handle.read()
        self.assertEqual(before, after)
        self.assertEqual(stat_before.st_size, os.stat(self.path).st_size)
        self.assertEqual(stat_before.st_mtime_ns, os.stat(self.path).st_mtime_ns)

    def test_an_unreadable_ledger_never_reports_full_fidelity(self):
        """Row 3 must not collapse into row 1: a SHADOW that cannot seed is not
        a SHADOW with nothing to seed from."""
        missing = self._store(path=os.path.join(self.dir, "gone.jsonl"))
        self.assertEqual(missing.shadow_fidelity(), FIDELITY_UNSEEDED)
        self.assertEqual(missing.seed_for_shadow(), ())

    def test_the_four_states_are_distinguishable(self):
        torn = _write(self.dir, _ledger(_reserved(1, "promotion:aa"),
                                        torn=b"0000000a"), name="torn.jsonl")
        states = {
            "allowlisted": self._store().shadow_fidelity(),
            "unlisted": self._store(mounts=UNLISTED_MOUNTS).shadow_fidelity(),
            "unreadable": self._store(
                path=os.path.join(self.dir, "gone.jsonl")).shadow_fidelity(),
        }
        self.assertEqual(states["allowlisted"], FIDELITY_FULL)
        self.assertEqual(states["unlisted"], FIDELITY_DEGRADED_NOT_ARMABLE)
        self.assertEqual(states["unreadable"], FIDELITY_UNSEEDED)
        self.assertEqual(self._store(path=torn).armed_capability()[0], UNAVAILABLE)


class AssessmentTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = _write(self._dir.name, _ledger(_reserved(1, "promotion:aa")))

    def _store(self, mounts, path=None):
        return LedgerStore(path=path or self.path, mounts=mounts,
                           refused_prefixes=())

    def test_an_unlisted_filesystem_starts_shadow_and_refuses_armed(self):
        published = self._store(UNLISTED_MOUNTS).assessment()
        self.assertEqual(published["mode"], "SHADOW")
        self.assertEqual(published["armed_capability"], UNAVAILABLE)
        self.assertIn(LOCK_EXCLUSION_UNATTESTED, published["armed_refusals"])
        self.assertIn(RESERVATION_DURABILITY_UNATTESTED, published["armed_refusals"])
        self.assertEqual(published["ledger_readability"], AVAILABLE)
        self.assertEqual(published["shadow_fidelity"], FIDELITY_DEGRADED_NOT_ARMABLE)

    def test_a_missing_ledger_refuses_armed(self):
        published = self._store(GOOD_MOUNTS,
                                path=os.path.join(self._dir.name, "gone")).assessment()
        self.assertEqual(published["armed_capability"], UNAVAILABLE)
        self.assertIn(LEDGER_UNAVAILABLE, published["armed_refusals"])

    def test_the_assessment_declares_what_this_slice_does_not_do(self):
        published = self._store(GOOD_MOUNTS).assessment()
        self.assertFalse(published["writes"])
        self.assertFalse(published["creates"])
        self.assertFalse(published["locks"])

    def test_generation_totals_are_published(self):
        published = self._store(GOOD_MOUNTS).assessment()
        self.assertEqual(published["generation_totals"]["reservations"], 1)
        self.assertEqual(published["generation_totals"]["unresolved"], 1)

    def test_the_assessment_is_serialisable(self):
        json.dumps(self._store(UNLISTED_MOUNTS).assessment())


class ScopeTests(unittest.TestCase):
    """The slice boundary, enforced rather than promised.

    AST, not raw text: a raw scan cannot tell a docstring saying the module
    does not append from a call that appends. That false positive has recurred
    three times in this repository.
    """

    def setUp(self):
        with open(inspect.getsourcefile(store_module), encoding="utf-8") as handle:
            self.tree = ast.parse(handle.read())

    def _calls(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call):
                target = node.func
                if isinstance(target, ast.Attribute):
                    yield target.attr, node
                elif isinstance(target, ast.Name):
                    yield target.id, node

    def test_nothing_that_could_move_the_fence_is_called(self):
        """Qualified where the name has a harmless homonym.

        An unqualified ban on `replace` flags str.replace, which is the same
        false-positive class as scanning raw text and hitting a docstring. The
        filesystem verbs are matched only on os./shutil./fcntl.; the ones with
        no benign meaning here are matched on any receiver.
        """
        any_receiver = {"flock", "lockf", "fsync", "fdatasync", "truncate",
                        "write", "writelines", "mktemp"}
        qualified = {"makedirs", "mkdir", "rename", "replace", "unlink",
                     "remove", "chmod", "open"}
        modules = {"os", "shutil", "fcntl", "io", "pathlib", "Path"}
        found = set()
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call):
                continue
            target = node.func
            if isinstance(target, ast.Name) and target.id in any_receiver:
                found.add(target.id)
            elif isinstance(target, ast.Attribute):
                if target.attr in any_receiver:
                    found.add(target.attr)
                elif (target.attr in qualified
                      and isinstance(target.value, ast.Name)
                      and target.value.id in modules):
                    found.add(f"{target.value.id}.{target.attr}")
        self.assertEqual(found, set())

    def test_every_open_is_read_only(self):
        for name, node in self._calls():
            if name != "open":
                continue
            modes = [a.value for a in node.args[1:] if isinstance(a, ast.Constant)]
            modes += [k.value.value for k in node.keywords
                      if k.arg == "mode" and isinstance(k.value, ast.Constant)]
            self.assertTrue(modes, "open() with no explicit mode")
            for mode in modes:
                self.assertNotIn("w", mode)
                self.assertNotIn("a", mode)
                self.assertNotIn("+", mode)
                self.assertNotIn("x", mode)

    def test_no_module_that_writes_or_locks_is_imported(self):
        imported = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split(".")[0])
        self.assertEqual(imported & {"fcntl", "shutil", "sqlite3", "tempfile",
                                     "subprocess", "socket", "urllib", "requests"},
                         set())

    def test_the_store_exposes_no_write_method(self):
        surface = {name for name in dir(LedgerStore) if not name.startswith("_")}
        for forbidden in ("append", "write", "reserve", "commit", "reconcile",
                          "acquire", "create", "arm"):
            self.assertNotIn(forbidden, surface)


if __name__ == "__main__":
    unittest.main()
