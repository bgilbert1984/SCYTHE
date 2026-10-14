"""Synthetic failure-injection tests for tools/r4_package/gate_d_monitor.py (docs/research/metamorphic_r4_gate_d_protocol.md section 5, Revision 2).
No Dedirock connection, transfer, preflight, or replay execution: everything here runs a short-lived local child process."""
import hashlib, json, os, pathlib, shutil, signal, subprocess, sys, tempfile, time, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "tools", "r4_package", "gate_d_monitor.py")
sha = lambda b: hashlib.sha256(b).hexdigest()
PY_SHA256 = sha(pathlib.Path(sys.executable).read_bytes())


def read_log(record_dir):
    return [json.loads(l) for l in pathlib.Path(record_dir, "MONITOR_LOG.jsonl").read_text().splitlines()]


def events(log, name):
    return [e for e in log if e["event"] == name]


class GateDMonitor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gated-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.n = 0
        self.children = []

    def tearDown(self):
        for p in self.children:
            try:
                p.kill(); p.wait(timeout=5)
            except Exception:
                pass

    def record_dir(self):
        self.n += 1
        return os.path.join(self.tmp, f"rec{self.n}")

    def pid_file_path(self):
        self.n += 1
        return os.path.join(self.tmp, f"launch{self.n}.pid")

    def pid_file(self, pid):
        path = self.pid_file_path()
        pathlib.Path(path).write_text(str(pid) + "\n")
        return path

    def spawn_sleeper(self, seconds=30):
        p = subprocess.Popen([sys.executable, "-c", f"import time; time.sleep({seconds})"])
        self.children.append(p)
        return p

    def run_monitor(self, launch_pid_file, interpreter_sha256=None, record_dir=None, extra=(), interval=0.2,
                     grace=3, wait=True, test_mode=True):
        record_dir = record_dir or self.record_dir()
        args = [sys.executable, TOOL, "--launch-pid-file", launch_pid_file, "--record-dir", record_dir,
                "--interpreter-sha256", interpreter_sha256 if interpreter_sha256 is not None else PY_SHA256,
                "--interval-seconds", str(interval), "--startup-grace-samples", str(grace)]
        if test_mode:
            args.append("--test-mode")
        args += list(extra)
        p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if wait:
            try:
                out, err = p.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                p.kill(); out, err = p.communicate()
            return p.returncode, out, err, record_dir
        return p, record_dir

    # -- happy path --------------------------------------------------------
    def test_happy_path_confirms_samples_then_clean_stop_on_sigterm(self):
        child = self.spawn_sleeper(30)
        pid_file = self.pid_file(child.pid)
        proc, record_dir = self.run_monitor(pid_file, wait=False)
        time.sleep(1.5)  # let it confirm startup and take a few samples
        proc.send_signal(signal.SIGTERM)
        out, err = proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, 0, err)
        log = read_log(record_dir)
        self.assertEqual(len(events(log, "MONITOR_START")), 1)
        self.assertEqual(len(events(log, "MONITOR_READY")), 1)
        confirmed = events(log, "MONITOR_START_CONFIRMED")
        self.assertEqual(len(confirmed), 1)
        self.assertEqual(confirmed[0]["bound_pid"], child.pid)
        samples = events(log, "SAMPLE")
        self.assertGreater(len(samples), 0)
        for s in samples:
            self.assertIn(child.pid, s["pid_tree"])
            self.assertGreaterEqual(s["vmrss_tree_bytes"], 0)
            self.assertGreaterEqual(s["peak_sampled_tree_rss_bytes"], s["vmrss_tree_bytes"])
            self.assertGreaterEqual(s["sum_observed_process_hwm_bytes"], 0)
            self.assertIsNone(s["peak_job_memory_bytes"])
        stop = events(log, "MONITOR_STOP")
        self.assertEqual(len(stop), 1)
        self.assertTrue(stop[0]["clean"])
        self.assertEqual(stop[0]["reason"], "SIGTERM")
        self.assertEqual(events(log, "HARNESS_PROCESS_ENDED"), [])  # the harness was still running at stop
        child.kill(); child.wait(timeout=5)

    # -- MONITOR_READY precedes binding; the monitor can start before the harness does ------
    def test_monitor_ready_emitted_before_pid_file_exists_then_binds_once_it_appears(self):
        pid_file = self.pid_file_path()
        self.assertFalse(os.path.exists(pid_file))
        proc, record_dir = self.run_monitor(pid_file, grace=20, interval=0.2, wait=False)
        time.sleep(0.4)
        log = read_log(record_dir)
        self.assertEqual(len(events(log, "MONITOR_READY")), 1)
        self.assertEqual(events(log, "MONITOR_START_CONFIRMED"), [])  # not yet: pid file still absent
        child = self.spawn_sleeper(10)
        pathlib.Path(pid_file).write_text(str(child.pid) + "\n")
        time.sleep(1.0)
        proc.send_signal(signal.SIGTERM)
        out, err = proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, 0, err)
        log = read_log(record_dir)
        confirmed = events(log, "MONITOR_START_CONFIRMED")
        self.assertEqual(len(confirmed), 1)
        self.assertEqual(confirmed[0]["bound_pid"], child.pid)
        child.kill(); child.wait(timeout=5)

    def test_startup_failure_when_pid_file_never_appears(self):
        pid_file = self.pid_file_path()
        rc, out, err, record_dir = self.run_monitor(pid_file, grace=2, interval=0.1)
        self.assertEqual(rc, 3, err)
        log = read_log(record_dir)
        self.assertEqual(len(events(log, "MONITOR_READY")), 1)
        fail = events(log, "MONITOR_FAILURE")
        self.assertEqual(len(fail), 1)
        self.assertEqual(fail[0]["phase"], "STARTUP")
        self.assertIn("not yet readable", fail[0]["detail"])
        self.assertEqual(events(log, "MONITOR_START_CONFIRMED"), [])

    # -- absent / short-lived process --------------------------------------
    def test_absent_pid_is_startup_failure(self):
        # a pid number guaranteed not to exist (reserved/above any real process)
        rc, out, err, record_dir = self.run_monitor(self.pid_file(2**22))
        self.assertEqual(rc, 3, err)
        log = read_log(record_dir)
        fail = events(log, "MONITOR_FAILURE")
        self.assertEqual(len(fail), 1)
        self.assertEqual(fail[0]["phase"], "STARTUP")
        self.assertEqual(events(log, "MONITOR_START_CONFIRMED"), [])
        stop = events(log, "MONITOR_STOP")[0]
        self.assertFalse(stop["clean"])
        self.assertEqual(stop["reason"], "STARTUP_FAILURE")

    def test_short_lived_process_dead_before_confirmation_is_startup_failure(self):
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait(timeout=10)  # guaranteed exited and reaped before the monitor ever looks
        rc, out, err, record_dir = self.run_monitor(self.pid_file(p.pid))
        self.assertEqual(rc, 3, err)
        log = read_log(record_dir)
        self.assertEqual(events(log, "MONITOR_START_CONFIRMED"), [])
        self.assertEqual(events(log, "MONITOR_FAILURE")[0]["phase"], "STARTUP")

    # -- wrong interpreter: monitor fails, harness is never touched, never a permit-to-relaunch --
    def test_wrong_interpreter_digest_is_startup_failure_and_harness_is_left_running(self):
        child = self.spawn_sleeper(30)
        wrong_digest = sha(b"not the interpreter")
        rc, out, err, record_dir = self.run_monitor(self.pid_file(child.pid), interpreter_sha256=wrong_digest)
        self.assertEqual(rc, 3, err)
        log = read_log(record_dir)
        self.assertEqual(events(log, "MONITOR_START_CONFIRMED"), [])
        self.assertIsNone(child.poll())  # still running: the monitor never signalled it
        child.kill(); child.wait(timeout=5)

    # -- normal end mid-run (not a reuse) -----------------------------------
    def test_harness_ends_normally_mid_run_then_clean_stop_on_sigterm(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.6)"])
        self.children.append(child)
        pid_file = self.pid_file(child.pid)
        proc, record_dir = self.run_monitor(pid_file, wait=False)
        child.wait(timeout=10)
        time.sleep(1.0)  # let the monitor notice the process is gone
        proc.send_signal(signal.SIGTERM)
        out, err = proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, 0, err)
        log = read_log(record_dir)
        self.assertEqual(len(events(log, "MONITOR_START_CONFIRMED")), 1)
        ended = events(log, "HARNESS_PROCESS_ENDED")
        self.assertEqual(len(ended), 1)
        self.assertFalse(ended[0]["pid_reused"])  # gone, not replaced
        self.assertTrue(events(log, "MONITOR_STOP")[0]["clean"])

    # -- PID-reuse discriminator (the OS cannot be made to reuse a PID on demand, so this exercises
    # the helper directly, out-of-process, so that gate_d_monitor's module-level sys.excepthook
    # installation never contaminates this test process) --
    def test_starttime_helper_distinguishes_process_identity(self):
        helper = (
            "import sys; sys.path.insert(0, %r)\n"
            "from gate_d_monitor import read_starttime\n"
            "print(read_starttime(int(sys.argv[1])))\n"
        ) % os.path.join(HERE, "tools", "r4_package")
        helper_path = os.path.join(self.tmp, "starttime_helper.py")
        pathlib.Path(helper_path).write_text(helper)

        def starttime_of(pid):
            r = subprocess.run([sys.executable, helper_path, str(pid)], capture_output=True, text=True, timeout=10)
            return None if r.stdout.strip() == "None" else int(r.stdout.strip())

        a = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
        self.children.append(a)
        t_a = starttime_of(a.pid)
        self.assertIsNotNone(t_a)
        b = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
        self.children.append(b)
        t_b = starttime_of(b.pid)
        self.assertIsNotNone(t_b)
        if a.pid == b.pid:  # would only happen under real OS pid reuse, not forced here
            self.assertNotEqual(t_a, t_b)
        b.kill(); b.wait(timeout=5)
        a.kill(); a.wait(timeout=5)
        self.assertIsNone(starttime_of(a.pid))  # gone: a live process later reusing the same number would differ

    # -- OOM observability -------------------------------------------------
    def test_oom_observability_unavailable_when_source_fails(self):
        child = self.spawn_sleeper(5)
        pid_file = self.pid_file(child.pid)
        rc, out, err, record_dir = self.run_monitor(pid_file, extra=["--oom-check-command", "false"])
        log = read_log(record_dir)
        self.assertFalse(events(log, "OOM_OBSERVABILITY")[0]["available"])
        for s in events(log, "SAMPLE"):
            self.assertEqual(s["oom_observability"], "UNAVAILABLE")
            self.assertIsNone(s["oom_related_log_lines_since_baseline"])

    def test_oom_observability_available_counts_increase(self):
        counter = os.path.join(self.tmp, "oom_counter.txt")
        pathlib.Path(counter).write_text("0")
        script = os.path.join(self.tmp, "oom_source.py")
        pathlib.Path(script).write_text(
            "import sys\n"
            f"p = {counter!r}\n"
            "n = int(open(p).read())\n"
            "open(p, 'w').write(str(n + 1))\n"
            "print('oom-kill event ' * n)\n"
        )
        child = self.spawn_sleeper(5)
        pid_file = self.pid_file(child.pid)
        rc, out, err, record_dir = self.run_monitor(pid_file, extra=["--oom-check-command", f"{sys.executable} {script}"], interval=0.3, wait=True)
        log = read_log(record_dir)
        self.assertTrue(events(log, "OOM_OBSERVABILITY")[0]["available"])
        samples = events(log, "SAMPLE")
        self.assertTrue(samples)
        counts = [s["oom_related_log_lines_since_baseline"] for s in samples]
        self.assertTrue(all(c is not None for c in counts))
        self.assertEqual(counts, sorted(counts))  # non-decreasing as the fake source's count climbs

    def test_oom_observability_transitions_to_unavailable_then_reacquires_mid_run(self):
        # a stateful fake source: succeeds on calls 1-2, fails on calls 3-4, succeeds again from call 5
        state = os.path.join(self.tmp, "oom_calls.txt")
        pathlib.Path(state).write_text("0")
        script = os.path.join(self.tmp, "flaky_oom_source.py")
        pathlib.Path(script).write_text(
            "import sys\n"
            f"p = {state!r}\n"
            "n = int(open(p).read()) + 1\n"
            "open(p, 'w').write(str(n))\n"
            "if 3 <= n <= 4:\n"
            "    sys.exit(1)\n"
            "print('oom-kill event')\n"
        )
        child = self.spawn_sleeper(10)
        pid_file = self.pid_file(child.pid)
        proc, record_dir = self.run_monitor(
            pid_file, extra=["--oom-check-command", f"{sys.executable} {script}"], interval=0.3, wait=False,
        )
        time.sleep(2.2)  # several sampling intervals: enough to cross the fail-then-recover window
        proc.send_signal(signal.SIGTERM)
        out, err = proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, 0, err)
        log = read_log(record_dir)
        availability_events = events(log, "OOM_OBSERVABILITY")
        reasons = [e.get("reason") for e in availability_events]
        self.assertIn("SOURCE_FAILED_MID_RUN", reasons)
        self.assertIn("SOURCE_REACQUIRED", reasons)
        samples = events(log, "SAMPLE")
        statuses = [s["oom_observability"] for s in samples]
        self.assertIn("UNAVAILABLE", statuses)
        self.assertIn("AVAILABLE", statuses)
        for s in samples:
            if s["oom_observability"] == "UNAVAILABLE":
                self.assertIsNone(s["oom_related_log_lines_since_baseline"])
        child.kill(); child.wait(timeout=5)

    # -- crash path: never reported as a clean stop -------------------------
    def test_malformed_oom_command_is_monitor_failure_not_clean(self):
        child = self.spawn_sleeper(5)
        pid_file = self.pid_file(child.pid)
        rc, out, err, record_dir = self.run_monitor(pid_file, extra=["--oom-check-command", "'unterminated"])
        self.assertEqual(rc, 2, err)
        log = read_log(record_dir)
        stop = events(log, "MONITOR_STOP")
        self.assertEqual(len(stop), 1)
        self.assertFalse(stop[0]["clean"])
        self.assertEqual(stop[0]["reason"], "CRASH")
        self.assertIn("MONITOR_FAILURE: CRASH", out + err)

    # -- production-mode guardrails: refused before any record dir is created ----------
    def test_oom_check_command_without_test_mode_is_rejected(self):
        child = self.spawn_sleeper(5)
        record_dir = self.record_dir()
        rc, out, err, _ = self.run_monitor(self.pid_file(child.pid), record_dir=record_dir,
                                            extra=["--oom-check-command", "false"], test_mode=False)
        self.assertNotEqual(rc, 0)
        self.assertIn("--test-mode", out + err)
        self.assertFalse(os.path.exists(record_dir))

    def test_fast_interval_without_test_mode_is_rejected(self):
        child = self.spawn_sleeper(5)
        record_dir = self.record_dir()
        rc, out, err, _ = self.run_monitor(self.pid_file(child.pid), record_dir=record_dir,
                                            interval=1.0, test_mode=False)
        self.assertNotEqual(rc, 0)
        self.assertIn("--test-mode", out + err)
        self.assertFalse(os.path.exists(record_dir))

    def test_production_interval_floor_is_exactly_five_seconds(self):
        # 5.0 itself is accepted without --test-mode (only below the floor is rejected); use a PID that
        # will never match so this exits quickly as a startup failure rather than actually waiting 5s+.
        record_dir = self.record_dir()
        args = [sys.executable, TOOL, "--launch-pid-file", self.pid_file(2**22), "--record-dir", record_dir,
                "--interpreter-sha256", PY_SHA256, "--interval-seconds", "5.0", "--startup-grace-samples", "1"]
        r = subprocess.run(args, capture_output=True, text=True, timeout=15)
        self.assertNotIn("--test-mode", r.stdout + r.stderr)  # rejected for a different reason (startup failure), not the floor
        self.assertTrue(os.path.exists(record_dir))

    def test_invalid_interpreter_sha256_rejected_before_record_dir_created(self):
        child = self.spawn_sleeper(5)
        record_dir = self.record_dir()
        rc, out, err, _ = self.run_monitor(self.pid_file(child.pid), record_dir=record_dir,
                                            interpreter_sha256="not-a-digest")
        self.assertNotEqual(rc, 0)
        self.assertIn("64 hex", out + err)
        self.assertFalse(os.path.exists(record_dir))

    def test_nonpositive_interval_rejected_before_record_dir_created(self):
        child = self.spawn_sleeper(5)
        record_dir = self.record_dir()
        rc, out, err, _ = self.run_monitor(self.pid_file(child.pid), record_dir=record_dir, interval=0)
        self.assertNotEqual(rc, 0)
        self.assertIn("positive", out + err)
        self.assertFalse(os.path.exists(record_dir))

    def test_nonpositive_startup_grace_samples_rejected_before_record_dir_created(self):
        child = self.spawn_sleeper(5)
        record_dir = self.record_dir()
        rc, out, err, _ = self.run_monitor(self.pid_file(child.pid), record_dir=record_dir, grace=0)
        self.assertNotEqual(rc, 0)
        self.assertIn("positive integer", out + err)
        self.assertFalse(os.path.exists(record_dir))

    # -- custody of the record directory ------------------------------------
    def test_record_dir_must_not_already_exist(self):
        child = self.spawn_sleeper(5)
        record_dir = self.record_dir()
        os.makedirs(record_dir)
        rc, out, err, _ = self.run_monitor(self.pid_file(child.pid), record_dir=record_dir)
        self.assertNotEqual(rc, 0)
        self.assertIn("refusing to overwrite", out + err)

    def test_record_dir_parent_must_already_exist(self):
        child = self.spawn_sleeper(5)
        record_dir = os.path.join(self.tmp, "missing-parent", "rec")
        rc, out, err, _ = self.run_monitor(self.pid_file(child.pid), record_dir=record_dir)
        self.assertNotEqual(rc, 0)
        self.assertFalse(os.path.exists(record_dir))
        self.assertIn("parent", out + err)

    def test_relative_paths_are_rejected(self):
        child = self.spawn_sleeper(5)
        args = [sys.executable, TOOL, "--launch-pid-file", "launch.pid", "--record-dir", "rec", "--interpreter-sha256", PY_SHA256]
        r = subprocess.run(args, capture_output=True, text=True, cwd=self.tmp)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("absolute", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
