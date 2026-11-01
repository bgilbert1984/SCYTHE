"""Passwords are stored with PBKDF2 and a per-hash salt; old SHA-256 rows still log in and are replaced.

Temporary databases and throwaway passwords only; no server is started.
"""
import base64
import contextlib
import hashlib
import io
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import operator_session_manager as osm
from operator_session_manager import OperatorSessionManager

PW = "correct-horse-battery"
DEFAULT_ITERATIONS = osm.PASSWORD_HASH_ITERATIONS
FAST = 1000   # iterations for tests that do not check the default


def quietly(call, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return call(*args, **kwargs)


def legacy(password):
    return hashlib.sha256(f"command-ops-salt{password}".encode()).hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.db = os.path.join(self.dir.name, "operators.db")
        p = mock.patch.object(osm, "PASSWORD_HASH_ITERATIONS", FAST)
        p.start()
        self.addCleanup(p.stop)
        self.m = quietly(OperatorSessionManager, db_path=self.db)

    def stored(self, callsign):
        with contextlib.closing(sqlite3.connect(self.db)) as c:
            return c.execute("SELECT password_hash FROM operators WHERE callsign=?", (callsign,)).fetchone()[0]

    def put(self, callsign, hashed):
        with contextlib.closing(sqlite3.connect(self.db)) as c:
            c.execute("INSERT INTO operators (operator_id, callsign, email, password_hash, role, team_id, created_at)"
                      " VALUES (?,?,?,?,?,?,?)", ("id-" + callsign, callsign, callsign + "@x", hashed, "operator", None, "2026-01-01"))
            c.commit()

    def login(self, callsign, password):
        return quietly(self.m.authenticate, callsign, password)


class Format(Base):
    def test_default_work_factor_is_at_least_the_published_minimum(self):
        self.assertGreaterEqual(DEFAULT_ITERATIONS, 600_000)

    def test_stored_value_names_scheme_cost_salt_and_digest(self):
        kind, iterations, salt, digest = self.m._hash_password(PW).split("$")
        self.assertEqual((kind, iterations), ("pbkdf2_sha256", str(FAST)))
        self.assertEqual(len(base64.b64decode(salt)), 16)
        self.assertEqual(len(base64.b64decode(digest)), 32)

    def test_same_password_gets_a_different_hash_each_time(self):
        self.assertNotEqual(self.m._hash_password(PW), self.m._hash_password(PW))

    def test_the_password_does_not_appear_in_the_stored_value(self):
        self.assertNotIn(PW, self.m._hash_password(PW))

    def test_the_old_fixed_salt_is_not_used(self):
        self.assertNotEqual(self.m._hash_password(PW), legacy(PW))


class Verify(Base):
    def test_right_password_matches_and_needs_no_replacement(self):
        self.assertEqual(self.m._verify_password(PW, self.m._hash_password(PW)), (True, False))

    def test_wrong_password_does_not_match(self):
        self.assertEqual(self.m._verify_password("nope", self.m._hash_password(PW)), (False, False))

    def test_a_lower_cost_hash_matches_and_is_marked_for_replacement(self):
        old = osm._encode_password_hash(PW, b"s" * 16, FAST - 1)
        self.assertEqual(self.m._verify_password(PW, old), (True, True))

    def test_wrong_password_against_a_lower_cost_hash_is_not_marked_for_replacement(self):
        old = osm._encode_password_hash(PW, b"s" * 16, FAST - 1)
        self.assertEqual(self.m._verify_password("nope", old), (False, False))

    def test_characters_outside_base64_are_rejected_not_skipped(self):
        kind, it, salt, digest = osm._encode_password_hash(PW, b"s" * 16, FAST).split("$")
        for fields in ((salt[:4] + "!" + salt[4:], digest), (salt, digest[:4] + "!" + digest[4:])):
            self.assertEqual(self.m._verify_password(PW, "$".join((kind, it) + fields)), (False, False))

    def test_legacy_digest_matches_and_is_marked_for_replacement(self):
        self.assertEqual(self.m._verify_password(PW, legacy(PW)), (True, True))
        self.assertEqual(self.m._verify_password("nope", legacy(PW)), (False, False))

    def test_empty_and_missing_stored_values_never_match(self):
        for stored in ("", None):
            for pw in ("", PW):
                self.assertEqual(self.m._verify_password(pw, stored), (False, False))

    def test_the_stored_hash_itself_is_not_a_password(self):
        stored = self.m._hash_password(PW)
        self.assertEqual(self.m._verify_password(stored, stored), (False, False))
        self.assertEqual(self.m._verify_password(legacy(PW), legacy(PW)), (False, False))

    def test_damaged_values_do_not_match_or_raise(self):
        good = self.m._hash_password(PW)
        kind, it, salt, digest = good.split("$")
        for bad in ("pbkdf2_sha256$x$" + salt + "$" + digest,
                    "pbkdf2_sha256$0$" + salt + "$" + digest,
                    "pbkdf2_sha256$-5$" + salt + "$" + digest,
                    f"pbkdf2_sha256${osm.PASSWORD_HASH_MAX_ITERATIONS + 1}${salt}${digest}",
                    "pbkdf2_sha256$%s$$%s" % (it, digest),
                    "pbkdf2_sha256$%s$%s$" % (it, salt),
                    "pbkdf2_sha256$%s$%s" % (it, salt),
                    "pbkdf2_sha256$%s$%s$%s$extra" % (it, salt, digest),
                    "pbkdf2_sha256$%s$!!!$%s" % (it, digest),
                    "plaintext", legacy(PW).upper(), legacy(PW)[:-1]):
            self.assertEqual(self.m._verify_password(PW, bad), (False, False), bad)

    def test_an_absurd_cost_is_refused_without_computing_it(self):
        with mock.patch.object(osm.hashlib, "pbkdf2_hmac") as kdf:
            self.m._verify_password(PW, f"pbkdf2_sha256${osm.PASSWORD_HASH_MAX_ITERATIONS + 1}$c2FsdA==$ZGlnZXN0")
        kdf.assert_not_called()

    def test_non_string_password_does_not_match(self):
        self.assertEqual(self.m._verify_password(None, self.m._hash_password(PW)), (False, False))


class Login(Base):
    def test_registered_operator_logs_in_with_the_right_password_only(self):
        quietly(self.m.register_operator, "alpha", "alpha@x", PW)
        self.assertIsNotNone(self.login("alpha", PW))
        self.assertIsNone(self.login("alpha", "wrong"))
        self.assertTrue(self.stored("alpha").startswith("pbkdf2_sha256$"))

    def test_unknown_callsign_and_empty_password_fail(self):
        quietly(self.m.register_operator, "alpha", "alpha@x", PW)
        self.assertIsNone(self.login("ghost", PW))
        self.assertIsNone(self.login("ghost", ""))
        self.assertIsNone(self.login("alpha", ""))

    def test_blank_stored_hash_cannot_be_logged_into(self):
        self.put("fa", "")
        for pw in ("", PW):
            self.assertIsNone(self.login("fa", pw))

    def test_stored_hash_cannot_be_replayed_as_the_password(self):
        quietly(self.m.register_operator, "alpha", "alpha@x", PW)
        self.assertIsNone(self.login("alpha", self.stored("alpha")))
        self.put("old", legacy(PW))
        self.assertIsNone(self.login("old", legacy(PW)))

    def test_legacy_row_logs_in_and_is_replaced_by_a_strong_hash(self):
        self.put("old", legacy(PW))
        self.assertIsNotNone(self.login("old", PW))
        self.assertTrue(self.stored("old").startswith("pbkdf2_sha256$"))
        self.assertIsNotNone(self.login("old", PW))
        self.assertIsNone(self.login("old", "wrong"))

    def test_failed_login_leaves_a_legacy_row_untouched(self):
        self.put("old", legacy(PW))
        self.assertIsNone(self.login("old", "wrong"))
        self.assertEqual(self.stored("old"), legacy(PW))

    def test_a_lower_cost_row_is_raised_to_the_current_cost(self):
        self.put("low", osm._encode_password_hash(PW, b"s" * 16, FAST - 1))
        self.assertIsNotNone(self.login("low", PW))
        self.assertEqual(self.stored("low").split("$")[1], str(FAST))

    def test_a_current_row_is_not_rewritten_by_a_login(self):
        quietly(self.m.register_operator, "alpha", "alpha@x", PW)
        before = self.stored("alpha")
        self.login("alpha", PW)
        self.assertEqual(self.stored("alpha"), before)

    def test_unknown_callsign_does_the_same_hashing_work_as_a_wrong_password(self):
        quietly(self.m.register_operator, "alpha", "alpha@x", PW)
        self.m._unknown_callsign_hash()      # the decoy is made once, on first use
        with mock.patch.object(osm.hashlib, "pbkdf2_hmac", wraps=hashlib.pbkdf2_hmac) as kdf:
            self.login("alpha", "wrong")
            known = kdf.call_count
            kdf.reset_mock()
            self.login("ghost", "wrong")
            self.assertEqual(kdf.call_count, known)
            self.assertEqual(known, 1)

    def test_nobody_can_log_in_as_the_decoy(self):
        self.assertIsNone(self.login("ghost", ""))
        self.assertIsNone(self.login("ghost", self.m._unknown_callsign_hash()))


class Rooms(Base):
    def test_private_room_password_uses_the_new_format_and_still_gates_entry(self):
        quietly(self.m.register_operator, "alpha", "alpha@x", PW)
        session = self.login("alpha", PW)
        room = quietly(self.m.create_room, "r1", "tactical", session.operator_id, is_private=True, password="roompw")
        self.assertTrue(room.password_hash.startswith("pbkdf2_sha256$"))
        join = lambda pw: quietly(self.m.join_room, room.room_id, session.session_id, pw)
        self.assertFalse(join("wrong")[0])
        self.assertFalse(join("")[0])
        self.assertTrue(join("roompw")[0])

    def test_legacy_room_hash_still_admits_and_is_replaced(self):
        quietly(self.m.register_operator, "alpha", "alpha@x", PW)
        session = self.login("alpha", PW)
        room = quietly(self.m.create_room, "r1", "tactical", session.operator_id, is_private=True, password="roompw")
        room.password_hash = legacy("roompw")
        with contextlib.closing(sqlite3.connect(self.db)) as c:
            c.execute("UPDATE rooms SET password_hash=? WHERE room_id=?", (room.password_hash, room.room_id))
            c.commit()
        self.assertFalse(quietly(self.m.join_room, room.room_id, session.session_id, "wrong")[0])
        self.assertEqual(room.password_hash, legacy("roompw"))
        self.assertTrue(quietly(self.m.join_room, room.room_id, session.session_id, "roompw")[0])
        self.assertTrue(room.password_hash.startswith("pbkdf2_sha256$"))
        with contextlib.closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(c.execute("SELECT password_hash FROM rooms WHERE room_id=?", (room.room_id,)).fetchone()[0],
                             room.password_hash)


if __name__ == "__main__":
    unittest.main()
