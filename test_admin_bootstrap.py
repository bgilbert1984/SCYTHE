"""The first administrator is made on the host, once, and never by starting a server or over HTTP.

Everything runs on a temporary database with throwaway passwords; no server is started.
"""
import contextlib
import io
import os
import re
import sqlite3
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import operator_session_manager as osm
from operator_session_manager import AdminAlreadyExists, AdminBootstrapError, OperatorRole, OperatorSessionManager
from tools import scythe_admin

GOOD = "correct-horse-battery"


def quietly(call, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return call(*args, **kwargs)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.db = os.path.join(self.dir.name, "operators.db")

    def manager(self):
        return quietly(OperatorSessionManager, db_path=self.db)

    def rows(self, sql="SELECT callsign, role FROM operators ORDER BY callsign", args=()):
        if not os.path.exists(self.db):
            return []   # the command stopped before it opened the database
        with contextlib.closing(sqlite3.connect(self.db)) as conn:
            return conn.execute(sql, args).fetchall()


class NoSeededAdministrator(Base):
    def test_a_new_database_has_no_operators_at_all(self):
        self.manager()
        self.assertEqual(self.rows(), [])

    def test_starting_again_still_creates_none(self):
        self.manager()
        self.manager()
        self.assertEqual(self.rows(), [])

    def test_the_old_fixed_password_is_nowhere_in_the_source(self):
        text = Path(osm.__file__).read_text(encoding="utf8")
        self.assertNotIn("admin123", text)
        self.assertNotIn("_ensure_default_admin", text)

    def test_an_existing_database_keeps_its_accounts(self):
        manager = self.manager()
        quietly(manager.register_operator, "KEEP-1", "keep@example.invalid", GOOD, OperatorRole.SUPERVISOR)
        self.manager()
        self.assertEqual(self.rows(), [("KEEP-1", "supervisor")])


class Bootstrap(Base):
    def test_it_creates_one_administrator_and_records_it(self):
        manager = self.manager()
        operator = quietly(manager.bootstrap_admin, "ROOT-1", "root@example.invalid", GOOD)
        self.assertEqual((operator.callsign, operator.role), ("ROOT-1", OperatorRole.ADMIN))
        self.assertIs(manager.operators[operator.operator_id], operator, "the running manager knows it too")
        self.assertEqual(self.rows(), [("ROOT-1", "admin")])
        audit = self.rows("SELECT event, operator_id, callsign FROM admin_audit_log")
        self.assertEqual(audit, [("bootstrap_admin_created", operator.operator_id, "ROOT-1")])

    def test_the_password_is_stored_hashed_and_can_log_in(self):
        manager = self.manager()
        quietly(manager.bootstrap_admin, "ROOT-1", "root@example.invalid", GOOD)
        stored = self.rows("SELECT password_hash FROM operators")[0][0]
        self.assertNotIn(GOOD, stored)
        self.assertIsNotNone(quietly(manager.authenticate, "ROOT-1", GOOD))
        self.assertIsNone(quietly(manager.authenticate, "ROOT-1", GOOD + "x"))

    def test_it_is_closed_once_an_administrator_exists_even_for_another_callsign(self):
        manager = self.manager()
        quietly(manager.bootstrap_admin, "ROOT-1", "root@example.invalid", GOOD)
        with self.assertRaises(AdminAlreadyExists):
            quietly(manager.bootstrap_admin, "ROOT-2", "root2@example.invalid", GOOD)
        self.assertEqual(self.rows(), [("ROOT-1", "admin")])
        self.assertEqual(len(self.rows("SELECT * FROM admin_audit_log")), 1)

    def test_it_is_closed_for_a_second_manager_on_the_same_database(self):
        quietly(self.manager().bootstrap_admin, "ROOT-1", "root@example.invalid", GOOD)
        with self.assertRaises(AdminAlreadyExists):
            quietly(self.manager().bootstrap_admin, "ROOT-2", "root2@example.invalid", GOOD)

    def test_it_never_raises_an_existing_account(self):
        manager = self.manager()
        quietly(manager.register_operator, "USER-1", "user@example.invalid", GOOD)
        for callsign, email in (("USER-1", "other@example.invalid"), ("OTHER", "user@example.invalid")):
            with self.assertRaises(AdminBootstrapError) as raised:
                quietly(manager.bootstrap_admin, callsign, email, GOOD)
            self.assertNotIsInstance(raised.exception, AdminAlreadyExists)
        self.assertEqual(self.rows(), [("USER-1", "operator")], "the account kept its role and nothing was added")
        self.assertEqual(self.rows("SELECT * FROM admin_audit_log"), [])

    def test_a_non_administrator_does_not_close_it(self):
        manager = self.manager()
        quietly(manager.register_operator, "SUP-1", "sup@example.invalid", GOOD, OperatorRole.SUPERVISOR)
        quietly(manager.bootstrap_admin, "ROOT-1", "root@example.invalid", GOOD)
        self.assertEqual(self.rows(), [("ROOT-1", "admin"), ("SUP-1", "supervisor")])

    def test_weak_or_missing_input_creates_nothing(self):
        manager = self.manager()
        for callsign, email, password in (("R", "r@example.invalid", "short"), ("R", "r@example.invalid", ""), ("R", "r@example.invalid", None),
                                          ("R", "r@example.invalid", "x" * (osm.MIN_BOOTSTRAP_PASSWORD_LENGTH - 1)), ("", "r@example.invalid", GOOD),
                                          ("R", "", GOOD), ("  ", "r@example.invalid", GOOD), ("R", "  ", GOOD)):
            with self.assertRaises(AdminBootstrapError, msg=repr((callsign, email, password))):
                quietly(manager.bootstrap_admin, callsign, email, password)
        self.assertEqual(self.rows(), [])
        quietly(manager.bootstrap_admin, "R", "r@example.invalid", "x" * osm.MIN_BOOTSTRAP_PASSWORD_LENGTH)   # the minimum itself is accepted
        self.assertEqual(self.rows(), [("R", "admin")])

    def test_two_callers_at_once_make_exactly_one_administrator(self):
        managers = [self.manager(), self.manager()]
        outcomes, start = [], threading.Barrier(2)

        def attempt(manager, name):
            start.wait()
            try:
                quietly(manager.bootstrap_admin, name, f"{name}@example.invalid", GOOD)
                outcomes.append("created")
            except AdminAlreadyExists:
                outcomes.append("closed")

        threads = [threading.Thread(target=attempt, args=(m, f"ROOT-{i}")) for i, m in enumerate(managers)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(sorted(outcomes), ["closed", "created"])
        self.assertEqual(len(self.rows("SELECT * FROM operators WHERE role = 'admin'")), 1)


class Command(Base):
    def run_command(self, *argv, stdin_passwords=None):
        out, err = io.StringIO(), io.StringIO()
        patch = mock.patch.object(scythe_admin.getpass, "getpass", side_effect=list(stdin_passwords or []))
        with patch, contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = scythe_admin.main(["bootstrap-admin", "--callsign", "ROOT-1", "--email", "root@example.invalid", "--db", self.db, *argv])
        return code, out.getvalue() + err.getvalue()

    def password_file(self, text, mode=0o600):
        path = os.path.join(self.dir.name, "pw")
        with open(path, "w") as handle:
            handle.write(text)
        os.chmod(path, mode)
        return path

    def test_asked_twice_it_creates_the_administrator_and_prints_no_password(self):
        code, output = self.run_command(stdin_passwords=[GOOD, GOOD])
        self.assertEqual(code, 0, output)
        self.assertIn("Created administrator ROOT-1", output)
        self.assertNotIn(GOOD, output)
        self.assertEqual(self.rows(), [("ROOT-1", "admin")])

    def test_two_different_passwords_create_nothing(self):
        code, output = self.run_command(stdin_passwords=[GOOD, GOOD + "x"])
        self.assertEqual(code, 1)
        self.assertEqual(self.rows(), [])

    def test_a_password_file_is_read_without_its_trailing_newline(self):
        code, output = self.run_command("--password-file", self.password_file(GOOD + "\n"))
        self.assertEqual(code, 0, output)
        self.assertIsNotNone(quietly(self.manager().authenticate, "ROOT-1", GOOD))

    def test_a_password_file_others_can_read_is_refused_and_nothing_is_created(self):
        for mode in (0o644, 0o640, 0o660, 0o604, 0o666):
            code, output = self.run_command("--password-file", self.password_file(GOOD, mode))
            self.assertEqual(code, 1, oct(mode))
            self.assertIn("chmod 600", output)
            self.assertNotIn(GOOD, output)
        self.assertEqual(self.rows(), [])

    def test_a_pipe_is_refused_without_being_opened(self):
        path = os.path.join(self.dir.name, "fifo")
        os.mkfifo(path, 0o600)   # opening it would wait for a writer for ever
        with self.assertRaises(AdminBootstrapError):
            scythe_admin.read_password_file(path)

    def test_a_directory_or_missing_file_is_refused(self):
        self.assertEqual(self.run_command("--password-file", self.dir.name)[0], 1)
        self.assertEqual(self.run_command("--password-file", os.path.join(self.dir.name, "absent"))[0], 1)

    def test_a_second_run_exits_2_and_changes_nothing(self):
        self.run_command("--password-file", self.password_file(GOOD))
        code, output = self.run_command("--password-file", self.password_file(GOOD))
        self.assertEqual(code, 2)
        self.assertIn("already exists", output)
        self.assertEqual(self.rows(), [("ROOT-1", "admin")])

    def test_the_password_cannot_be_given_on_the_command_line_not_even_by_abbreviation(self):
        for flag in ("--password", "--password-fil", "--pass"):
            err = io.StringIO()
            with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as raised:
                scythe_admin.main(["bootstrap-admin", "--callsign", "R", "--email", "r@example.invalid", "--db", self.db, flag, GOOD])
            self.assertEqual(raised.exception.code, 2, flag)
            self.assertNotIn(GOOD, err.getvalue(), flag)
        self.assertEqual(self.rows(), [])


class NotReachableOverHttp(unittest.TestCase):
    def test_no_server_or_route_calls_the_bootstrap(self):
        root = Path(osm.__file__).resolve().parent
        callers = [path.name for path in root.glob("*.py")
                   if path.name not in ("operator_session_manager.py", "test_admin_bootstrap.py") and re.search(r"\bbootstrap_admin\s*\(", path.read_text(encoding="utf8", errors="replace"))]
        self.assertEqual(callers, [], "only tools/scythe_admin.py (on the host) may call it")


if __name__ == "__main__":
    unittest.main()
