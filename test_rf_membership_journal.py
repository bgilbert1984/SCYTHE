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
    JOURNAL_NOT_FOUND, JOURNAL_RECORD_LIMIT_REACHED,
    JOURNAL_RECORD_TOO_LARGE, JOURNAL_RECORD_TYPE_REFUSED,
    JOURNAL_TERMINAL_WITHOUT_INTENT,
    MAX_ABANDONED_ATTEMPTS, RECORD_TYPES, TERMINAL_FIELDS, JournalRefused,
    append_intent, canonical_record_bytes, create_membership_journal,
    frame_record, intent_record, journal_declaration,
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

    def test_the_journal_uses_the_manifest_json_form(self):
        body = canonical_record_bytes(_intent(corpus_id="café"))
        self.assertIn("café".encode("utf-8"), body)
        self.assertNotIn(b"\\u00e9", body)
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
