"""An identity that signed in on the hub gets an operator record on the instance, always with the `operator` role.

Temporary databases only; no server is started.
"""
import contextlib
import io
import os
import sqlite3
import tempfile
import unittest

from operator_session_manager import OperatorRole, OperatorSessionManager

HUB_ID = "1633f386-8ba9-4fb3-90d6-607545a984cf"


def quietly(call, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return call(*args, **kwargs)


class Provisioning(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.db = os.path.join(self.dir.name, "operators.db")
        self.m = quietly(OperatorSessionManager, db_path=self.db)

    def row(self, operator_id):
        with contextlib.closing(sqlite3.connect(self.db)) as c:
            return c.execute("SELECT callsign, email, password_hash, role FROM operators WHERE operator_id=?",
                             (operator_id,)).fetchone()

    def ensure(self, operator_id=HUB_ID, callsign="bgilbert1984"):
        return quietly(self.m.ensure_operator, operator_id, callsign)

    def test_creates_an_operator_the_manager_can_look_up(self):
        operator = self.ensure()
        self.assertEqual((operator.operator_id, operator.callsign, operator.role), (HUB_ID, "bgilbert1984", OperatorRole.OPERATOR))
        self.assertIs(self.m.get_operator(HUB_ID), operator)
        self.assertEqual(self.row(HUB_ID)[3], "operator")

    def test_the_role_is_never_anything_but_operator(self):
        for hostile in ("admin", "supervisor", "root"):
            self.assertEqual(self.ensure(f"id-{hostile}", hostile).role, OperatorRole.OPERATOR)
        self.assertEqual(self.row("id-admin")[3], "operator")

    def test_nothing_can_log_in_to_it_directly(self):
        self.ensure()
        self.assertEqual(self.row(HUB_ID)[2], "")
        for guess in ("", "x", "bgilbert1984"):
            self.assertIsNone(quietly(self.m.authenticate, "bgilbert1984", guess))

    def test_second_call_returns_the_same_record_and_changes_nothing(self):
        first = self.ensure()
        second = self.ensure(callsign="someone-else")
        self.assertIs(first, second)
        self.assertEqual(self.row(HUB_ID)[0], "bgilbert1984")

    def test_an_existing_record_is_kept_with_its_role(self):
        admin = quietly(self.m.bootstrap_admin, "boss", "boss@x.test", "a-long-enough-password")
        fresh = quietly(OperatorSessionManager, db_path=self.db)   # cache empty, record on disk
        got = quietly(fresh.ensure_operator, admin.operator_id, "different")
        self.assertEqual((got.callsign, got.role), ("boss", OperatorRole.ADMIN))

    def test_a_taken_callsign_does_not_adopt_that_account(self):
        local = quietly(self.m.register_operator, "bgilbert1984", "me@x.test", "pw-123456-xx")
        hub = self.ensure()
        self.assertNotEqual(hub.operator_id, local.operator_id)
        self.assertEqual(hub.callsign, "bgilbert1984-1633f3")
        self.assertEqual(self.row(local.operator_id)[0], "bgilbert1984")
        self.assertEqual(self.row(local.operator_id)[3], "operator")

    def test_missing_or_hostile_names_get_a_safe_callsign(self):
        for i, name in enumerate((None, "", "   ", "\n\t")):
            op = self.ensure(f"0000000{i}-aaaa-bbbb-cccc-dddddddddddd", name)
            self.assertTrue(op.callsign.startswith("hub-"), op.callsign)
        long = self.ensure("ffffffff-aaaa-bbbb-cccc-dddddddddddd", "x" * 500)
        self.assertEqual(len(long.callsign), 48)

    def test_two_identities_with_the_same_name_both_get_records(self):
        a = self.ensure("aaaaaaaa-0000-0000-0000-000000000001", "alex")
        b = self.ensure("bbbbbbbb-0000-0000-0000-000000000002", "alex")
        self.assertEqual({a.callsign, b.callsign}, {"alex", "alex-bbbbbb"})

    def test_bad_ids_create_nothing(self):
        for bad in (None, "", "   ", 7, "x" * 129):
            self.assertIsNone(quietly(self.m.ensure_operator, bad, "n"))
        with contextlib.closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM operators").fetchone()[0], 0)

    def test_survives_a_restart(self):
        self.ensure()
        fresh = quietly(OperatorSessionManager, db_path=self.db)
        self.assertEqual(fresh.get_operator(HUB_ID).callsign, "bgilbert1984")


if __name__ == "__main__":
    unittest.main()
