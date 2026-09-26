"""Section 5.20 3d, piece 2: final-dependent membership recovery.

The reopen path now reconciles the journal against the finals on disk and
reconstructs each stratum's sequence from the result, in place of the two
refusals that used to say recovery was unbuilt. These are the tests for that:
the pure classifier's truth table (no filesystem), the per-window verification
and classification against real member files, and the sequence reconstruction
the reconciliation feeds.

There is no live path that publishes a final yet -- the publisher is built and
not wired until piece 3 -- so a member and its journal intent are written here
directly, exactly as a crashed capture would have left them: a framed `.iqc`
whose name is its own file digest, and an INTENT that bound that digest. The
crash is expressed by which journal records are present, not by killing a
process, so a test states the surviving state rather than arranging for one.
"""

import hashlib
import os
import unittest

from rf_capture_format import (
    canonical_header_bytes, canonical_member_name, framing_prefix,
)
from rf_corpus_namespace import (
    CORPUS_FILE_MODE, ELIGIBLE_NAME, MANIFEST_NAME, open_corpus_namespace,
)
from rf_membership_journal import (
    ABANDON, COMMIT, JOURNAL_NAME, append_abandon, append_commit,
    append_intent, intent_record, read_membership_journal,
)
import rf_membership_recovery as R
from rf_membership_recovery import (
    ADOPTED, DISCARDED_UNVERIFIED, DISCARDED_UNWRITTEN,
    RECOVERY_ABANDONED_FINAL_PRESENT, RECOVERY_COMMITTED_FINAL_MISSING,
    RECOVERY_INTENT_MANIFEST_MISMATCH, RECOVERY_INTENT_SELF_CONTRADICTORY,
    RECOVERY_MEMBER_OBJECT_REFUSED, RECOVERY_MIXED_LIFETIME,
    RECOVERY_DESCRIPTOR_REFUSED, RECOVERY_STRAY_ENTRY,
    RECOVERY_UNACCOUNTED_FINAL, SETTLED_ABANDON,
    SETTLED_MEMBER, CommittedWindow, FinalOutcome, ReconciledMembership,
    RecoveryRefused, corpus_ring_lifetime, reconcile, reconstruct_sequences,
)
from test_rf_corpus_namespace import NamespaceFixture

_RESERVED = (MANIFEST_NAME, ELIGIBLE_NAME, JOURNAL_NAME)


def _member_image(first_sample_index, sample_count, payload):
    """A framed `.iqc` image carrying just the geometry recovery reads.

    Recovery does not validate the full header schema; it reads
    ``first_sample_index`` and ``sample_count`` and recomputes the digests. A
    minimal but well-framed header is therefore a faithful stand-in for what the
    publisher writes at step 4, without this test having to reproduce every
    header authority.
    """
    header = {"first_sample_index": first_sample_index,
              "sample_count": sample_count,
              "payload_sha256": hashlib.sha256(payload).hexdigest()}
    hb = canonical_header_bytes(header)
    return framing_prefix(hb) + hb + payload


class ClassifyTests(unittest.TestCase):
    """The pure core: a journal terminal and a final outcome to one class."""

    VERIFIED = FinalOutcome(present=True, verified=True,
                            first_sample_index=0, last_sample_index=1024)
    ABSENT = FinalOutcome(present=False, verified=False)
    UNVERIFIED = FinalOutcome(present=True, verified=False)

    def test_committed_with_a_verified_final_is_a_settled_member(self):
        self.assertEqual(R._classify(COMMIT, self.VERIFIED), SETTLED_MEMBER)

    def test_intent_only_with_a_verified_final_is_adopted(self):
        self.assertEqual(R._classify(None, self.VERIFIED), ADOPTED)

    def test_intent_only_with_no_final_is_discarded_unwritten(self):
        self.assertEqual(R._classify(None, self.ABSENT), DISCARDED_UNWRITTEN)

    def test_intent_only_with_a_failing_final_is_discarded_unverified(self):
        self.assertEqual(R._classify(None, self.UNVERIFIED),
                         DISCARDED_UNVERIFIED)

    def test_abandoned_with_no_verified_final_is_settled_abandon(self):
        self.assertEqual(R._classify(ABANDON, self.ABSENT), SETTLED_ABANDON)
        self.assertEqual(R._classify(ABANDON, self.UNVERIFIED), SETTLED_ABANDON)

    def test_committed_without_a_verified_final_contradicts(self):
        for outcome in (self.ABSENT, self.UNVERIFIED):
            with self.assertRaises(RecoveryRefused) as caught:
                R._classify(COMMIT, outcome)
            self.assertEqual(caught.exception.code,
                             RECOVERY_COMMITTED_FINAL_MISSING)

    def test_abandoned_with_a_verified_final_contradicts(self):
        with self.assertRaises(RecoveryRefused) as caught:
            R._classify(ABANDON, self.VERIFIED)
        self.assertEqual(caught.exception.code,
                         RECOVERY_ABANDONED_FINAL_PRESENT)


class SequenceReconstructionTests(unittest.TestCase):
    """The bridge to piece 1: reconciled membership to per-stratum sequences."""

    def _member(self, first, last, life="ring-a"):
        return CommittedWindow(window_id=f"w-{first}", first_sample_index=first,
                               last_sample_index=last, ring_lifetime_id=life)

    def test_an_empty_corpus_has_no_lifetime_and_no_sequences(self):
        empty = ReconciledMembership(
            members_by_stratum={"GAIN_STEPS": ()}, counts={})
        self.assertIsNone(corpus_ring_lifetime(empty))
        self.assertEqual(reconstruct_sequences(empty, corpus_id="corpus-a"), {})

    def test_one_lifetime_reconstructs_each_stratum(self):
        reconciled = ReconciledMembership(members_by_stratum={
            "GAIN_STEPS": (self._member(0, 1024), self._member(1024, 2048)),
            "RETUNE_TRANSIENTS": (self._member(0, 1024),),
        }, counts={})
        self.assertEqual(corpus_ring_lifetime(reconciled), "ring-a")
        sequences = reconstruct_sequences(reconciled, corpus_id="corpus-a")
        self.assertEqual(sequences["GAIN_STEPS"].accepted, 2)
        self.assertEqual(sequences["GAIN_STEPS"].previous_window_id, "w-1024")
        self.assertEqual(sequences["GAIN_STEPS"].previous_last_sample_index,
                         2048)
        self.assertEqual(sequences["RETUNE_TRANSIENTS"].accepted, 1)

    def test_members_spanning_two_lifetimes_refuse(self):
        reconciled = ReconciledMembership(members_by_stratum={
            "GAIN_STEPS": (self._member(0, 1024, life="ring-a"),),
            "RETUNE_TRANSIENTS": (self._member(0, 1024, life="ring-b"),),
        }, counts={})
        with self.assertRaises(RecoveryRefused) as caught:
            corpus_ring_lifetime(reconciled)
        self.assertEqual(caught.exception.code, RECOVERY_MIXED_LIFETIME)


class ReconcileFixture(NamespaceFixture):
    """A created corpus, then members and journal records written by hand."""

    LIFE = "ring-lifetime-recovery-001"

    def setUp(self):
        super().setUp()
        scope = self.create()
        self.manifest_sha256 = scope.manifest_sha256
        scope.release()
        self.dir_fd = os.open(self.path(), os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(self._close_dir)

    def _close_dir(self):
        try:
            os.close(self.dir_fd)
        except OSError:
            pass

    def write_member(self, first, sample_count=1024, payload=None):
        payload = bytes(32) if payload is None else payload
        image = _member_image(first, sample_count, payload)
        file_sha256 = hashlib.sha256(image).hexdigest()
        name = canonical_member_name(file_sha256)
        fd = os.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                     CORPUS_FILE_MODE, dir_fd=self.dir_fd)
        try:
            os.write(fd, image)
            os.fchmod(fd, CORPUS_FILE_MODE)
            os.fsync(fd)
        finally:
            os.close(fd)
        return {"name": name, "file_sha256": file_sha256,
                "payload_sha256": hashlib.sha256(payload).hexdigest(),
                "first_sample_index": first}

    def add_intent(self, window_id, *, first, member=None, file_sha256=None,
                   payload_sha256=None, name=None, stratum="GAIN_STEPS",
                   ring_lifetime_id=None, manifest_sha256=None,
                   previous_window_id=None):
        if member is not None:
            file_sha256 = member["file_sha256"]
            payload_sha256 = member["payload_sha256"]
            name = member["name"]
        append_intent(self.dir_fd, intent_record(
            manifest_sha256=manifest_sha256 or self.manifest_sha256,
            corpus_id="corpus-a", stratum=stratum, window_id=window_id,
            ring_lifetime_id=ring_lifetime_id or self.LIFE,
            expected_final_filename=name, file_sha256=file_sha256,
            payload_sha256=payload_sha256, previous_window_id=previous_window_id,
            first_sample_index=first,
            envelope_digest="blake2s:" + "d" * 32,
            capture_plan_digest="blake2s:" + "e" * 32))

    def reconcile(self):
        journal = read_membership_journal(self.dir_fd)
        entries = tuple(sorted(os.listdir(self.dir_fd)))
        return reconcile(self.dir_fd, journal=journal, entries=entries,
                         reserved_names=_RESERVED,
                         manifest_sha256=self.manifest_sha256)


class ReconcileTests(ReconcileFixture):
    """Classification and its action, against real files and records."""

    def test_an_empty_corpus_reconciles_to_nothing(self):
        reconciled = self.reconcile()
        self.assertEqual(reconciled.members_by_stratum["GAIN_STEPS"], ())
        self.assertTrue(all(v == 0 for v in reconciled.counts.values()))

    def test_a_committed_member_is_settled_and_counted(self):
        member = self.write_member(first=0)
        self.add_intent("w-1", first=0, member=member)
        append_commit(self.dir_fd, "w-1")
        reconciled = self.reconcile()
        self.assertEqual(reconciled.counts[SETTLED_MEMBER], 1)
        windows = reconciled.members_by_stratum["GAIN_STEPS"]
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].window_id, "w-1")
        self.assertEqual(windows[0].first_sample_index, 0)
        self.assertEqual(windows[0].last_sample_index, 1024)
        self.assertEqual(windows[0].ring_lifetime_id, self.LIFE)

    def test_an_intent_with_a_verified_final_is_adopted_and_committed(self):
        """The entry-14 hole: a verified final under an open intent. Adoption
        appends the COMMIT the crash lost, so the member becomes durable."""
        member = self.write_member(first=0)
        self.add_intent("w-1", first=0, member=member)
        reconciled = self.reconcile()
        self.assertEqual(reconciled.counts[ADOPTED], 1)
        self.assertEqual(len(reconciled.members_by_stratum["GAIN_STEPS"]), 1)
        # The adoption is durable: the journal now carries the COMMIT.
        after = read_membership_journal(self.dir_fd)
        self.assertEqual(after.terminals["w-1"], COMMIT)
        self.assertEqual(after.committed_by_stratum["GAIN_STEPS"], 1)

    def test_an_intent_with_no_final_is_discarded_and_abandoned(self):
        self.add_intent("w-1", first=0, file_sha256="a" * 64,
                        payload_sha256="b" * 64,
                        name=canonical_member_name("a" * 64))
        reconciled = self.reconcile()
        self.assertEqual(reconciled.counts[DISCARDED_UNWRITTEN], 1)
        self.assertEqual(reconciled.members_by_stratum["GAIN_STEPS"], ())
        after = read_membership_journal(self.dir_fd)
        self.assertEqual(after.terminals["w-1"], ABANDON)

    def test_an_abandoned_intent_with_no_final_is_settled(self):
        self.add_intent("w-1", first=0, file_sha256="a" * 64,
                        payload_sha256="b" * 64,
                        name=canonical_member_name("a" * 64))
        append_abandon(self.dir_fd, "w-1")
        reconciled = self.reconcile()
        self.assertEqual(reconciled.counts[SETTLED_ABANDON], 1)
        self.assertEqual(reconciled.members_by_stratum["GAIN_STEPS"], ())

    def test_a_committed_intent_whose_final_is_gone_contradicts(self):
        self.add_intent("w-1", first=0, file_sha256="a" * 64,
                        payload_sha256="b" * 64,
                        name=canonical_member_name("a" * 64))
        append_commit(self.dir_fd, "w-1")
        with self.assertRaises(RecoveryRefused) as caught:
            self.reconcile()
        self.assertEqual(caught.exception.code,
                         RECOVERY_COMMITTED_FINAL_MISSING)

    def test_a_member_no_intent_records_is_unaccounted(self):
        self.write_member(first=0)      # a final on disk, but no intent for it
        with self.assertRaises(RecoveryRefused) as caught:
            self.reconcile()
        self.assertEqual(caught.exception.code, RECOVERY_UNACCOUNTED_FINAL)

    def test_a_stray_non_member_entry_refuses(self):
        with open(os.path.join(self.path(), "notes.txt"), "w") as handle:
            handle.write("not a member")
        with self.assertRaises(RecoveryRefused) as caught:
            self.reconcile()
        self.assertEqual(caught.exception.code, RECOVERY_STRAY_ENTRY)

    def test_an_intent_bound_under_another_manifest_refuses(self):
        member = self.write_member(first=0)
        self.add_intent("w-1", first=0, member=member,
                        manifest_sha256="f" * 64)
        with self.assertRaises(RecoveryRefused) as caught:
            self.reconcile()
        self.assertEqual(caught.exception.code,
                         RECOVERY_INTENT_MANIFEST_MISMATCH)

    def test_a_member_whose_header_disagrees_with_its_intent_contradicts(self):
        """The bytes hash to the intent's file digest, so the header is fixed;
        an intent recording a different first_sample_index contradicts itself."""
        member = self.write_member(first=4096)
        # Bind the intent to the real digests but claim a different start.
        self.add_intent("w-1", first=0, file_sha256=member["file_sha256"],
                        payload_sha256=member["payload_sha256"],
                        name=member["name"])
        with self.assertRaises(RecoveryRefused) as caught:
            self.reconcile()
        self.assertEqual(caught.exception.code,
                         RECOVERY_INTENT_SELF_CONTRADICTORY)

    def test_a_symlink_at_a_member_name_refuses(self):
        member = self.write_member(first=0)
        os.unlink(member["name"], dir_fd=self.dir_fd)
        os.symlink("elsewhere", member["name"], dir_fd=self.dir_fd)
        self.add_intent("w-1", first=0, member=member)
        with self.assertRaises(RecoveryRefused) as caught:
            self.reconcile()
        self.assertEqual(caught.exception.code, RECOVERY_MEMBER_OBJECT_REFUSED)

    def test_a_wrong_file_at_a_member_name_is_discarded_unverified(self):
        """A file sitting at a member name whose bytes do not hash to it is the
        wrong file at that name: present, unverified, discarded. The canonical
        name only appears via a verified link on the live path, so this is the
        defensive corner, and it still resolves to an ABANDON rather than a
        member."""
        name = canonical_member_name("a" * 64)
        fd = os.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                     CORPUS_FILE_MODE, dir_fd=self.dir_fd)
        try:
            os.write(fd, b"bytes that do not hash to the name")
            os.fchmod(fd, CORPUS_FILE_MODE)
        finally:
            os.close(fd)
        self.add_intent("w-1", first=0, file_sha256="a" * 64,
                        payload_sha256="b" * 64, name=name)
        reconciled = self.reconcile()
        self.assertEqual(reconciled.counts[DISCARDED_UNVERIFIED], 1)
        self.assertEqual(reconciled.members_by_stratum["GAIN_STEPS"], ())
        after = read_membership_journal(self.dir_fd)
        self.assertEqual(after.terminals["w-1"], ABANDON)

    def test_a_closed_directory_descriptor_refuses(self):
        journal = read_membership_journal(self.dir_fd)
        with self.assertRaises(RecoveryRefused) as caught:
            reconcile(-1, journal=journal, entries=(), reserved_names=_RESERVED,
                      manifest_sha256=self.manifest_sha256)
        self.assertEqual(caught.exception.code, RECOVERY_DESCRIPTOR_REFUSED)

    def test_reconcile_is_stable_across_a_second_pass(self):
        """Adoption is idempotent: once the COMMIT is appended, the second
        reopen sees a settled member and takes no further action."""
        member = self.write_member(first=0)
        self.add_intent("w-1", first=0, member=member)
        self.reconcile()
        second = self.reconcile()
        self.assertEqual(second.counts[SETTLED_MEMBER], 1)
        self.assertEqual(second.counts[ADOPTED], 0)


class ReopenEndToEndTests(ReconcileFixture):
    """The whole reopen path: reconcile, then the scope carries the sequences."""

    def _reopen(self):
        self._close_dir()
        return open_corpus_namespace(corpus_id="corpus-a", root=self.root)

    def test_reopen_adopts_a_member_and_reconstructs_its_sequence(self):
        first_member = self.write_member(first=0)
        self.add_intent("w-1", first=0, member=first_member)
        append_commit(self.dir_fd, "w-1")
        second_member = self.write_member(first=1024)
        self.add_intent("w-2", first=1024, member=second_member,
                        previous_window_id="w-1")   # intent-only: the crash
        with self._reopen() as corpus:
            recovery = corpus.membership_recovery()
            self.assertEqual(recovery["ring_lifetime_id"], self.LIFE)
            self.assertEqual(recovery["classification_counts"][SETTLED_MEMBER], 1)
            self.assertEqual(recovery["classification_counts"][ADOPTED], 1)
            self.assertEqual(recovery["reconstructed_strata"]["GAIN_STEPS"], 2)
            data = corpus.to_dict()
            self.assertEqual(data["sequence_state"],
                             "DURABLE; RECONSTRUCTED AT REOPEN")

    def test_reopen_of_an_empty_corpus_carries_no_lifetime(self):
        with self._reopen() as corpus:
            recovery = corpus.membership_recovery()
            self.assertIsNone(recovery["ring_lifetime_id"])
            self.assertEqual(recovery["reconstructed_strata"], {})

    def test_reopen_refuses_an_unaccounted_member(self):
        self.write_member(first=0)      # a final with no intent
        with self.assertRaises(RecoveryRefused) as caught:
            self._reopen()
        self.assertEqual(caught.exception.code, RECOVERY_UNACCOUNTED_FINAL)


if __name__ == "__main__":
    unittest.main()
