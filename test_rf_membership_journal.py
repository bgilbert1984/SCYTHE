"""§5.26 membership-journal core over real temporary filesystems."""

import hashlib
import json
import os
import pathlib
import shutil
import stat
import struct
import tempfile
import unittest
from unittest import mock

import rf_membership_journal as journal
from rf_membership_journal import (
    ABANDON, COMMIT, INTENT, CORPUS_MEMBER_CAPACITY, INTENT_FIELDS,
    IQJ_MAX_RECORD_BYTES, IQJ_MAX_RECORDS, JOURNAL_ABANDONMENT_LIMIT_REACHED,
    JOURNAL_ALREADY_PRESENT, JOURNAL_CONTRADICTORY_TERMINAL,
    JOURNAL_DUPLICATE_INTENT, JOURNAL_DUPLICATE_TERMINAL,
    JOURNAL_FIELD_NO_AUTHORITY, JOURNAL_FIELD_NOT_EMITTED,
    JOURNAL_FILE_MODE, JOURNAL_NAME, JOURNAL_NOT_CANONICAL,
    JOURNAL_DESCRIPTOR_REFUSED,
    JOURNAL_NOT_FOUND, JOURNAL_RECORD_LIMIT_REACHED,
    JOURNAL_RECORD_TOO_LARGE, JOURNAL_RECORD_TYPE_REFUSED,
    JOURNAL_TERMINAL_WITHOUT_INTENT,
    MAX_ABANDONED_ATTEMPTS, RECORD_TYPES, TERMINAL_FIELDS, JournalRefused,
    append_intent, canonical_record_bytes, create_membership_journal,
    frame_record, intent_record, journal_declaration,
    _encode_canonical,
    read_membership_journal, terminal_record,
)
from rf_capture_admission import CAPTURED_STRATA
from rf_validation_manifest import MINIMUM_WINDOWS_PER_STRATUM


_REAL_FSYNC = os.fsync


def _intent(window_id="window-a", **changes):
    fields = dict(
        manifest_sha256="a" * 64,
        corpus_id="corpus-a",
        stratum="GAIN_STEPS",
        window_id=window_id,
        ring_lifetime_id="ring-a",
        expected_final_filename="b" * 64 + ".iqc",
        file_sha256="b" * 64,
        payload_sha256="c" * 64,
        previous_window_id=None,
        first_sample_index=0,
        envelope_digest="blake2s:" + "d" * 32,
        capture_plan_digest="blake2s:" + "e" * 32,
    )
    fields.update(changes)
    return intent_record(**fields)


class JournalFixture(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="scythe-journal-test-")
        self.addCleanup(self._cleanup)
        self.dir_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(self._close_directory)

    def _close_directory(self):
        if self.dir_fd is not None:
            os.close(self.dir_fd)
            self.dir_fd = None

    def _cleanup(self):
        self._close_directory()
        shutil.rmtree(self.root, ignore_errors=True)
        if os.path.exists(self.root):                  # pragma: no cover
            raise AssertionError(f"{self.root} outlived its test")

    def create(self):
        create_membership_journal(self.dir_fd)

    def path(self):
        return os.path.join(self.root, JOURNAL_NAME)

    def write_raw(self, data):
        with open(self.path(), "ab") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())


class DeclarationTests(unittest.TestCase):
    def test_the_three_bounds_are_derived_from_the_corpus_capacity(self):
        expected = len(CAPTURED_STRATA) * MINIMUM_WINDOWS_PER_STRATUM
        self.assertEqual(CORPUS_MEMBER_CAPACITY, expected)
        self.assertEqual(MAX_ABANDONED_ATTEMPTS, 2 * expected)
        self.assertEqual(IQJ_MAX_RECORDS, 6 * expected)
        self.assertEqual(IQJ_MAX_RECORD_BYTES, 65_536)

    def test_the_record_vocabulary_is_closed_and_uppercase(self):
        self.assertEqual(RECORD_TYPES, ("INTENT", "COMMIT", "ABANDON"))

    def test_the_three_exact_schemas_match_the_accepted_table(self):
        self.assertEqual(frozenset(_intent()), INTENT_FIELDS)
        self.assertEqual(frozenset(terminal_record(COMMIT, "w")),
                         TERMINAL_FIELDS)
        self.assertEqual(frozenset(terminal_record(ABANDON, "w")),
                         TERMINAL_FIELDS)

    def test_status_exposes_bounds_and_no_record_contents(self):
        status = journal_declaration()
        self.assertEqual(status["name"], JOURNAL_NAME)
        self.assertEqual(status["max_records"], IQJ_MAX_RECORDS)
        self.assertNotIn("records", status)


class CanonicalRecordTests(unittest.TestCase):
    def test_framing_is_length_body_and_raw_sha256(self):
        body = canonical_record_bytes(_intent())
        framed = frame_record(_intent())
        self.assertEqual(framed[:4], struct.pack("<I", len(body)))
        self.assertEqual(framed[4:4 + len(body)], body)
        self.assertEqual(framed[-32:], hashlib.sha256(body).digest())

    # Split by PROPERTY, not by name. One test per parameter, each with a
    # stimulus the other two cannot move. Three tests all asserting whole-byte
    # equality against a four-parameter `json.dumps` would be three names for
    # one witness: every parameter mutation breaks every one of them, and the
    # sweep reported `DUPLICATE SETS` for exactly that reason.

    def test_non_ascii_is_carried_as_utf_8_not_escaped(self):
        """`ensure_ascii=False` alone. A single key keeps ordering out of it,
        and escaping is invisible to the separators."""
        body = _encode_canonical({"alpha": "café"})
        self.assertIn("café".encode("utf-8"), body)
        self.assertNotIn(b"\\u00e9", body)

    def test_keys_are_serialised_in_sorted_order(self):
        """`sort_keys=True` alone, asserted as an offset relation. Inserted out
        of order, all-ASCII, so neither escaping nor spacing can affect it."""
        body = _encode_canonical({"zeta": "z", "alpha": "a"})
        self.assertLess(body.index(b"alpha"), body.index(b"zeta"))

    def test_separators_carry_no_whitespace(self):
        """`separators=(",", ":")` alone. All-ASCII and order-agnostic, so the
        other two parameters leave this green."""
        body = _encode_canonical({"zeta": "z", "alpha": "a"})
        self.assertNotIn(b", ", body)
        self.assertNotIn(b": ", body)

    def test_a_non_finite_value_is_refused_by_the_encoder(self):
        """`allow_nan=False`, witnessed at the seam that can reach it.

        Unreachable through `canonical_record_bytes`: every permitted field is
        an exact string, integer or `None` and extras refuse, so validation
        rejects a float first --- identically whether the parameter is True or
        False. The sweep proved the parameter discriminated nothing. It is the
        backstop for the day a numeric field is added, so it is tested where it
        is reachable rather than deleted or left unproven.
        """
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(JournalRefused) as caught:
                _encode_canonical({"alpha": value})
            self.assertEqual(caught.exception.code, JOURNAL_NOT_CANONICAL)

    def test_validation_still_precedes_the_encoder_seam(self):
        """The seam exists to witness the encoder, never to skip the checks."""
        with self.assertRaises(JournalRefused) as caught:
            canonical_record_bytes({"record_type": INTENT})
        self.assertEqual(caught.exception.code, JOURNAL_FIELD_NOT_EMITTED)

    def test_the_journal_uses_the_manifest_json_form(self):
        """The whole form, kept as the integration statement over the three
        property tests above --- not as their replacement."""
        body = canonical_record_bytes(_intent(corpus_id="café"))
        self.assertEqual(body, json.dumps(
            _intent(corpus_id="café"), sort_keys=True,
            separators=(",", ":"), ensure_ascii=False,
            allow_nan=False).encode("utf-8"))

    def test_a_missing_field_refuses(self):
        record = _intent()
        del record["manifest_sha256"]
        with self.assertRaises(JournalRefused) as caught:
            canonical_record_bytes(record)
        self.assertEqual(caught.exception.code, JOURNAL_FIELD_NOT_EMITTED)

    def test_an_extra_field_refuses(self):
        record = _intent()
        record["answer"] = 42
        with self.assertRaises(JournalRefused) as caught:
            canonical_record_bytes(record)
        self.assertEqual(caught.exception.code, JOURNAL_FIELD_NO_AUTHORITY)

    def test_an_unknown_record_type_refuses(self):
        with self.assertRaises(JournalRefused) as caught:
            terminal_record("DONE", "window-a")
        self.assertEqual(caught.exception.code, JOURNAL_RECORD_TYPE_REFUSED)

    def test_terminal_records_repeat_no_intent_binding(self):
        self.assertEqual(terminal_record(COMMIT, "window-a"),
                         {"record_type": COMMIT, "window_id": "window-a"})


class CreationTests(JournalFixture):
    def test_an_empty_file_is_the_canonical_zero_record_journal(self):
        self.create()
        self.assertEqual(os.path.getsize(self.path()), 0)
        state = read_membership_journal(self.dir_fd)
        self.assertEqual(state.records, ())
        self.assertFalse(state.torn_tail_truncated)

    def test_missing_and_empty_are_distinct(self):
        with self.assertRaises(JournalRefused) as caught:
            read_membership_journal(self.dir_fd)
        self.assertEqual(caught.exception.code, JOURNAL_NOT_FOUND)

    def test_creation_is_exclusive(self):
        self.create()
        with self.assertRaises(JournalRefused) as caught:
            self.create()
        self.assertEqual(caught.exception.code, JOURNAL_ALREADY_PRESENT)

    def test_the_mode_is_set_explicitly_under_a_hostile_umask(self):
        previous = os.umask(0o300)
        try:
            self.create()
        finally:
            os.umask(previous)
        self.assertEqual(stat.S_IMODE(os.stat(self.path()).st_mode),
                         JOURNAL_FILE_MODE)

    def test_creation_fsyncs_the_opened_regular_file(self):
        seen = []

        def recording(fd):
            info = os.fstat(fd)
            seen.append((info.st_dev, info.st_ino, stat.S_ISREG(info.st_mode)))
            return _REAL_FSYNC(fd)

        with mock.patch("rf_membership_journal.os.fsync", side_effect=recording):
            self.create()
        created = os.stat(self.path())
        self.assertEqual(seen, [(created.st_dev, created.st_ino, True)])


class AppendAndReadTests(JournalFixture):
    def setUp(self):
        super().setUp()
        self.create()

    def test_an_intent_is_appended_fsynced_and_read_exactly(self):
        expected = _intent()
        state = append_intent(self.dir_fd, expected)
        self.assertEqual(state.records, (expected,))
        reread = read_membership_journal(self.dir_fd)
        self.assertEqual(reread.records, (expected,))
        self.assertEqual(reread.valid_bytes, os.path.getsize(self.path()))

    def test_two_appends_both_survive_and_keep_their_order(self):
        """J20's separating witness. A single append to an empty file lands at
        offset zero whether or not the descriptor appends, so one record proves
        nothing; the second is what a non-appending write overwrites.

        Independent of J19's hazard: validating after the write leaves both
        records present and ordered.
        """
        first = _intent(window_id="window-a")
        second = _intent(window_id="window-b")
        append_intent(self.dir_fd, first)
        append_intent(self.dir_fd, second)
        state = read_membership_journal(self.dir_fd)
        self.assertEqual(state.records, (first, second))

    def test_a_zero_length_write_is_refused_rather_than_looping(self):
        """J21's branch. The guard exists to refuse exactly this; that a regular
        file rarely produces it is a reason to inject it, not to leave the
        refusal unproven."""
        real_write = os.write

        def stalled(fd, data):
            return 0

        with mock.patch.object(os, "write", stalled):
            with self.assertRaises(JournalRefused) as caught:
                append_intent(self.dir_fd, _intent())
        self.assertEqual(caught.exception.code, JOURNAL_DESCRIPTOR_REFUSED)
        self.assertEqual(os.write, real_write)

    def test_committed_entries_are_counted_by_stratum(self):
        """J24's branch. Nothing asserted this accounting at all, so a mutation
        that stopped counting commits discriminated nothing."""
        append_intent(self.dir_fd, _intent(window_id="window-a"))
        state = journal._append_terminal(self.dir_fd, COMMIT, "window-a")
        stratum = _intent()["stratum"]
        self.assertEqual(state.committed_by_stratum[stratum], 1)
        self.assertEqual(
            sum(state.committed_by_stratum.values()), 1)

    def test_the_stratum_cap_counts_commits_and_not_intents(self):
        """The cap is over committed entries --- not files, not intents. An
        intent alone must not advance it."""
        append_intent(self.dir_fd, _intent(window_id="window-a"))
        state = read_membership_journal(self.dir_fd)
        stratum = _intent()["stratum"]
        self.assertEqual(state.committed_by_stratum[stratum], 0)
        journal._append_terminal(self.dir_fd, ABANDON, "window-a")
        state = read_membership_journal(self.dir_fd)
        self.assertEqual(state.committed_by_stratum[stratum], 0)

    def test_capacity_for_the_terminal_is_checked_before_the_intent(self):
        with mock.patch.object(journal, "IQJ_MAX_RECORDS", 1):
            with self.assertRaises(JournalRefused) as caught:
                append_intent(self.dir_fd, _intent())
        self.assertEqual(caught.exception.code, JOURNAL_RECORD_LIMIT_REACHED)
        self.assertEqual(os.path.getsize(self.path()), 0)

    def test_a_window_id_is_spent_after_an_intent(self):
        append_intent(self.dir_fd, _intent())
        with self.assertRaises(JournalRefused) as caught:
            append_intent(self.dir_fd, _intent())
        self.assertEqual(caught.exception.code, JOURNAL_DUPLICATE_INTENT)

    def test_the_append_is_fsynced(self):
        seen = []

        def recording(fd):
            info = os.fstat(fd)
            seen.append((info.st_dev, info.st_ino))
            return _REAL_FSYNC(fd)

        with mock.patch("rf_membership_journal.os.fsync", side_effect=recording):
            append_intent(self.dir_fd, _intent())
        target = os.stat(self.path())
        self.assertIn((target.st_dev, target.st_ino), seen)

    def test_noncanonical_complete_bytes_refuse_instead_of_becoming_a_torn_tail(self):
        record = _intent()
        body = json.dumps(record, sort_keys=False, indent=1).encode("utf-8")
        self.write_raw(struct.pack("<I", len(body)) + body
                       + hashlib.sha256(body).digest())
        size = os.path.getsize(self.path())
        with self.assertRaises(JournalRefused) as caught:
            read_membership_journal(self.dir_fd)
        self.assertEqual(caught.exception.code, JOURNAL_NOT_CANONICAL)
        self.assertEqual(os.path.getsize(self.path()), size)


class TornTailTests(JournalFixture):
    def setUp(self):
        super().setUp()
        self.create()
        append_intent(self.dir_fd, _intent())
        self.intact_size = os.path.getsize(self.path())

    def assert_tail_is_truncated(self, tail):
        self.write_raw(tail)
        state = read_membership_journal(self.dir_fd)
        self.assertTrue(state.torn_tail_truncated)
        self.assertEqual(len(state.records), 1)
        self.assertEqual(os.path.getsize(self.path()), self.intact_size)

    def test_an_unparseable_complete_record_refuses_without_truncating(self):
        """J12's branch. A record whose digest is correct but whose body is not
        JSON, with valid bytes after it, is corruption --- not a torn append.

        The digest must agree, or this would be caught by the digest branch
        instead and prove nothing about this one.
        """
        bad = b'{"record_type":'                       # complete, unparseable
        framed = (struct.pack("<I", len(bad)) + bad
                  + hashlib.sha256(bad).digest())
        self.write_raw(framed)
        self.write_raw(frame_record(_intent(window_id="window-b")))
        before = os.path.getsize(self.path())
        with self.assertRaises(JournalRefused) as caught:
            read_membership_journal(self.dir_fd)
        self.assertEqual(caught.exception.code, JOURNAL_NOT_CANONICAL)
        self.assertEqual(os.path.getsize(self.path()), before)

    def test_the_truncation_is_issued_before_the_journal_fsync(self):
        """J15's branch. The protocol was issued, which is what a test can see;
        crash survival is not observable from inside the process.

        Recorded by opened-object identity so another file's `fsync` cannot
        stand in for the journal's.
        """
        order = []
        real_truncate, real_fsync = os.ftruncate, os.fsync
        journal_fds = set()
        real_open = os.open

        def watched_open(*args, **kwargs):
            fd = real_open(*args, **kwargs)
            if args and args[0] == JOURNAL_NAME:
                journal_fds.add(fd)
            return fd

        def watched_truncate(fd, length):
            if fd in journal_fds:
                order.append("truncate")
            return real_truncate(fd, length)

        def watched_fsync(fd):
            if fd in journal_fds:
                order.append("fsync")
            return real_fsync(fd)

        self.write_raw(b"\x05\x00")                    # a torn length prefix
        with mock.patch.object(os, "open", watched_open), \
             mock.patch.object(os, "ftruncate", watched_truncate), \
             mock.patch.object(os, "fsync", watched_fsync):
            state = read_membership_journal(self.dir_fd)
        self.assertTrue(state.torn_tail_truncated)
        self.assertIn("truncate", order)
        self.assertIn("fsync", order)
        self.assertLess(order.index("truncate"), order.index("fsync"))

    def test_a_fragmented_read_is_accumulated_not_truncated(self):
        """J13's branch. `os.read` may return fewer bytes than asked for; the
        loop must accumulate. A version returning the first fragment would see
        a short body, fail the digest, and truncate a record that was intact.
        """
        real_read = os.read

        def fragmented(fd, count):
            return real_read(fd, 1 if count > 1 else count)

        with mock.patch.object(os, "read", fragmented):
            state = read_membership_journal(self.dir_fd)
        self.assertFalse(state.torn_tail_truncated)
        self.assertEqual(len(state.records), 1)
        self.assertEqual(os.path.getsize(self.path()), self.intact_size)

    def test_a_short_length_is_truncated(self):
        self.assert_tail_is_truncated(b"\x05\x00")

    def test_a_short_body_is_truncated(self):
        self.assert_tail_is_truncated(struct.pack("<I", 20) + b"{}")

    def test_a_short_digest_is_truncated(self):
        body = canonical_record_bytes(terminal_record(COMMIT, "window-a"))
        self.assert_tail_is_truncated(
            struct.pack("<I", len(body)) + body + b"short")

    def test_a_digest_disagreement_is_truncated(self):
        body = canonical_record_bytes(terminal_record(COMMIT, "window-a"))
        self.assert_tail_is_truncated(
            struct.pack("<I", len(body)) + body + b"x" * 32)

    def test_an_overbound_length_refuses_before_allocating(self):
        self.write_raw(struct.pack("<I", IQJ_MAX_RECORD_BYTES + 1))
        with self.assertRaises(JournalRefused) as caught:
            read_membership_journal(self.dir_fd)
        self.assertEqual(caught.exception.code, JOURNAL_RECORD_TOO_LARGE)
        self.assertEqual(os.path.getsize(self.path()), self.intact_size + 4)

    def test_a_digest_disagreement_before_later_bytes_is_not_called_a_torn_tail(self):
        body = canonical_record_bytes(terminal_record(COMMIT, "window-a"))
        corrupt = struct.pack("<I", len(body)) + body + b"x" * 32
        self.write_raw(corrupt + frame_record(terminal_record(COMMIT, "window-a")))
        size = os.path.getsize(self.path())
        with self.assertRaises(JournalRefused) as caught:
            read_membership_journal(self.dir_fd)
        self.assertEqual(caught.exception.code, JOURNAL_NOT_CANONICAL)
        self.assertEqual(os.path.getsize(self.path()), size)


class IntrinsicConsistencyTests(JournalFixture):
    def setUp(self):
        super().setUp()
        self.create()

    def write_records(self, *records):
        self.write_raw(b"".join(frame_record(record) for record in records))

    def test_a_terminal_without_an_intent_refuses(self):
        self.write_records(terminal_record(COMMIT, "window-a"))
        with self.assertRaises(JournalRefused) as caught:
            read_membership_journal(self.dir_fd)
        self.assertEqual(caught.exception.code, JOURNAL_TERMINAL_WITHOUT_INTENT)

    def test_a_duplicate_intent_refuses(self):
        self.write_records(_intent(), _intent())
        with self.assertRaises(JournalRefused) as caught:
            read_membership_journal(self.dir_fd)
        self.assertEqual(caught.exception.code, JOURNAL_DUPLICATE_INTENT)

    def test_a_duplicate_terminal_refuses(self):
        self.write_records(_intent(), terminal_record(COMMIT, "window-a"),
                           terminal_record(COMMIT, "window-a"))
        with self.assertRaises(JournalRefused) as caught:
            read_membership_journal(self.dir_fd)
        self.assertEqual(caught.exception.code, JOURNAL_DUPLICATE_TERMINAL)

    def test_commit_and_abandon_for_one_window_refuse(self):
        self.write_records(_intent(), terminal_record(COMMIT, "window-a"),
                           terminal_record(ABANDON, "window-a"))
        with self.assertRaises(JournalRefused) as caught:
            read_membership_journal(self.dir_fd)
        self.assertEqual(caught.exception.code,
                         JOURNAL_CONTRADICTORY_TERMINAL)

    def test_the_abandonment_budget_is_counted_from_abandons(self):
        self.write_records(_intent(), terminal_record(ABANDON, "window-a"))
        with mock.patch.object(journal, "MAX_ABANDONED_ATTEMPTS", 1):
            with self.assertRaises(JournalRefused) as caught:
                append_intent(self.dir_fd, _intent("window-b"))
        self.assertEqual(caught.exception.code,
                         JOURNAL_ABANDONMENT_LIMIT_REACHED)

    def test_a_refused_terminal_is_validated_before_any_byte_is_written(self):
        append_intent(self.dir_fd, _intent())
        journal._append_terminal(self.dir_fd, COMMIT, "window-a")
        before = pathlib.Path(self.path()).read_bytes()
        with self.assertRaises(JournalRefused) as caught:
            journal._append_terminal(self.dir_fd, COMMIT, "window-a")
        self.assertEqual(caught.exception.code, JOURNAL_DUPLICATE_TERMINAL)
        self.assertEqual(pathlib.Path(self.path()).read_bytes(), before)


if __name__ == "__main__":                            # pragma: no cover
    unittest.main()
