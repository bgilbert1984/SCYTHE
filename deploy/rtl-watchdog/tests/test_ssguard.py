#!/usr/bin/env python3
"""Tests for the ss-guard rtl_tcp watchdog.

Drives the EXACT deployed guard script
(deploy/rtl-watchdog/scythe-rtl-watchdog.sh) against fake rtl_tcp servers
and a fake systemctl. Four cases, per the authorization:

  1. healthy stream         -> no action, exit 0, guard sends zero bytes
  2. listener absent        -> restart -> recovery, exit 1
  3. RTL0 magic but no I/Q  -> restart -> recovery, exit 1 (the wedge:
     systemctl active + listening socket, but no data flows)
  4. 3 restarts/hour spent  -> ALERT, no restart, exit 2

Run from deploy/rtl-watchdog/:
    python3 tests/test_ssguard.py

Requires 127.0.0.1:1234 free; the module skips otherwise (e.g. on scythe-1
itself, where the real rtl_tcp lives -- these tests must never touch it).
"""
import os
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
GUARD = os.path.join(os.path.dirname(TESTS_DIR), "scythe-rtl-watchdog.sh")
PORT = 1234


def port_free():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", PORT))
        return True
    except OSError:
        return False
    finally:
        s.close()


def rtl_sdr_running():
    return subprocess.run(
        ["pgrep", "-x", "rtl_sdr"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def setUpModule():
    assert os.path.isfile(GUARD), "guard script not found: %s" % GUARD
    if not port_free():
        raise unittest.SkipTest(
            "127.0.0.1:%d in use (real rtl_tcp?) -- refusing to test against it"
            % PORT)
    if rtl_sdr_running():
        raise unittest.SkipTest("rtl_sdr is running on this host")


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ssguard-test-")
        self.fakestate = os.path.join(self.tmp, "fakestate")
        os.makedirs(self.fakestate)
        fakebin = os.path.join(self.tmp, "fakebin")
        os.makedirs(fakebin)
        fake_ctl = os.path.join(fakebin, "systemctl")
        shutil.copy(os.path.join(TESTS_DIR, "fake_systemctl.py"), fake_ctl)
        os.chmod(fake_ctl, os.stat(fake_ctl).st_mode | stat.S_IXUSR)
        self.env = dict(os.environ)
        self.env["PATH"] = fakebin + os.pathsep + self.env.get("PATH", "")
        self.env["XDG_STATE_HOME"] = self.tmp
        self.env["SSGUARD_TEST_FAKESTATE"] = self.fakestate
        self.env["SSGUARD_TEST_SRCDIR"] = TESTS_DIR
        self.env["SSGUARD_TEST_PORT"] = str(PORT)
        self.env["SSGUARD_TEST_RECFILE"] = os.path.join(self.tmp, "rec.bin")
        self._write("svcstate", "active")
        self._write("servermode", "none")

    def tearDown(self):
        self._stop_server()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers ---------------------------------------------------------
    def _write(self, name, text):
        with open(os.path.join(self.fakestate, name), "w") as f:
            f.write(text)

    def _fake_ctl(self, *args):
        return subprocess.run(
            ["systemctl"] + list(args), env=self.env,
            capture_output=True, text=True, timeout=60)

    def _start_server(self, mode):
        """Start a fake server via the fake systemctl (as a restart would).

        The setup restart is scrubbed from the call log so assertions count
        only restarts the guard itself performs.
        """
        self._write("servermode", mode)
        r = self._fake_ctl("--user", "restart")
        self.assertEqual(r.returncode, 0)
        self._write("servermode", "none")
        open(os.path.join(self.fakestate, "systemctl.calls"), "w").close()

    def _stop_server(self):
        pidfile = os.path.join(self.fakestate, "server.pid")
        try:
            with open(pidfile) as f:
                pid = int(f.read().strip())
        except (FileNotFoundError, ValueError):
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    def run_guard(self):
        return subprocess.run(
            ["bash", GUARD], env=self.env,
            capture_output=True, text=True, timeout=120)

    def log_text(self):
        p = os.path.join(self.tmp, "scythe-watchdog", "watchdog.log")
        try:
            with open(p) as f:
                return f.read()
        except FileNotFoundError:
            return ""

    def restarts_invoked(self):
        p = os.path.join(self.fakestate, "systemctl.calls")
        try:
            with open(p) as f:
                return sum(1 for l in f if l.startswith("--user restart"))
        except FileNotFoundError:
            return 0

    def wait_recfile(self, timeout=5):
        """Wait for the fake server to record the (closed) connection."""
        rec = self.env["SSGUARD_TEST_RECFILE"]
        end = time.time() + timeout
        while time.time() < end:
            if os.path.exists(rec):
                return os.path.getsize(rec)
            time.sleep(0.1)
        self.fail("fake server never recorded the guard connection")

    # -- the four cases --------------------------------------------------
    def test_healthy_no_action(self):
        self._start_server("healthy")
        r = self.run_guard()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.restarts_invoked(), 0)
        log = self.log_text()
        self.assertNotIn("UNHEALTHY", log)
        self.assertNotIn("ALERT", log)
        # The boundary: a health check must never emit tuner commands.
        self.assertEqual(self.wait_recfile(), 0)

    def test_listener_absent_recovery(self):
        # No server; a restart must bring one up (servermode=healthy).
        self._write("servermode", "healthy")
        r = self.run_guard()
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertEqual(self.restarts_invoked(), 1)
        log = self.log_text()
        self.assertIn("UNHEALTHY (NO_LISTENER", log)
        self.assertIn("RECOVERED", log)

    def test_rtl0_but_no_iq_recovery(self):
        # The wedge: listening socket + RTL0 magic, but no I/Q flows.
        self._start_server("stall")
        self._write("servermode", "healthy")  # restart recovers to healthy
        r = self.run_guard()
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertEqual(self.restarts_invoked(), 1)
        log = self.log_text()
        # socket.timeout is TimeoutError on 3.10+, "timeout" before.
        self.assertRegex(log, r"FAIL:(TimeoutError|timeout)")
        self.assertIn("RECOVERED", log)

    def test_rate_limit_alert(self):
        now = int(time.time())
        statedir = os.path.join(self.tmp, "scythe-watchdog")
        os.makedirs(statedir, exist_ok=True)
        with open(os.path.join(statedir, "restarts.log"), "w") as f:
            f.write("\n".join(str(now) for _ in range(3)) + "\n")
        # No server listening: genuine failure, but budget spent.
        r = self.run_guard()
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(self.restarts_invoked(), 0)
        log = self.log_text()
        self.assertIn("ALERT", log)
        self.assertIn("cooldown", log)


if __name__ == "__main__":
    unittest.main(verbosity=2)
