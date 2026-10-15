#!/usr/bin/env python3
"""Local integration rehearsal of the Gate D launcher/monitor handshake (docs/research/
metamorphic_r4_gate_d_protocol.md section 5/7, Revision 2, merged PR #175/#176). Stdlib only.

Exercises the exact documented sequence -- monitor started first, MONITOR_READY awaited, then the
harness stand-in launched with the "cmd & echo $! > launch.pid" idiom, PID binding confirmed, the run
observed to completion, the monitor stopped cleanly by SIGTERM to its own PID -- using
standin_launcher.sh in this same directory (NOT the r4.1 harness, NOT run_job.sh; no production code
is executed; no Dedirock connection of any kind). It also performs INDEPENDENT verification: rather
than trusting the monitor's own self-authored log, this driver re-derives the same facts from raw OS
state with its own, separate code, and writes its findings to a separate file the monitor itself never
touches -- so monitor readiness and successful binding are each confirmed by a second, independent
observer, not solely by the monitor's own say-so (the safeguard requested in the PR #176 merge review).

    rehearsal_driver.py SCENARIO RECORD_DIR
        SCENARIO in: happy_path | short_lived_process | startup_failure_pid_never_appears |
                     startup_failure_wrong_digest

Writes RECORD_DIR/{monitor/, job/, launch.pid, INDEPENDENT_VERIFICATION.json, DRIVER_LOG.txt} and
exits 0 if the scenario's expected outcome was observed, 1 otherwise.
"""
import hashlib, json, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
MONITOR_TOOL = os.path.join(REPO_ROOT, "tools", "r4_package", "gate_d_monitor.py")
STANDIN = os.path.join(HERE, "standin_launcher.sh")
INTERVAL = 0.5
GRACE_SAMPLES = 10  # generous: this rehearses correctness of the handshake, not production timing


def log(driver_log, *parts):
    line = f"{now_iso()} " + " ".join(str(p) for p in parts)
    print(line)
    driver_log.write(line + "\n")
    driver_log.flush()


def now_iso():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def read_log_events(log_path):
    if not os.path.exists(log_path):
        return []
    with open(log_path) as f:
        return [json.loads(l) for l in f if l.strip()]


def wait_for_event(log_path, event_name, timeout, driver_log):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        evs = [e for e in read_log_events(log_path) if e["event"] == event_name]
        if evs:
            return evs[0]
        time.sleep(0.05)
    log(driver_log, f"TIMEOUT waiting for {event_name}")
    return None


def independent_verify_binding(record_dir, launch_pid_file, expected_sha256, driver_log):
    """Re-derives, from raw OS state and this driver's OWN code (never importing or trusting
    gate_d_monitor.py), whether the monitor's claimed binding is actually correct. Returns a dict
    written verbatim to INDEPENDENT_VERIFICATION.json -- a record the monitor process never writes to
    and never reads, so it is checkable independently of the monitor's own self-report."""
    monitor_log_path = os.path.join(record_dir, "MONITOR_LOG.jsonl")
    events = read_log_events(monitor_log_path)
    result = {"checked_at": now_iso(), "findings": []}

    ready = [e for e in events if e["event"] == "MONITOR_READY"]
    result["findings"].append({
        "check": "MONITOR_READY observed independently in the monitor's log",
        "pass": len(ready) == 1,
        "detail": ready[0] if ready else None,
    })
    if ready:
        monitor_pid = ready[0]["monitor_pid"]
        monitor_alive_at_ready_check_time = os.path.exists(f"/proc/{monitor_pid}")
        result["findings"].append({
            "check": "the monitor PID named in MONITOR_READY is independently confirmed to be a live "
                     "OS process (re-checked via /proc by this driver, not asserted by the monitor)",
            "pass": monitor_alive_at_ready_check_time,
            "detail": {"monitor_pid": monitor_pid},
        })

    confirmed = [e for e in events if e["event"] == "MONITOR_START_CONFIRMED"]
    if not confirmed:
        result["findings"].append({"check": "MONITOR_START_CONFIRMED present", "pass": False, "detail": None})
        return result

    c = confirmed[0]
    bound_pid, matched_pid, matched_exe = c["bound_pid"], c["matched_pid"], c["matched_exe"]

    try:
        with open(launch_pid_file) as f:
            pid_file_contents = int(f.read().split()[0])
    except (OSError, ValueError) as exc:
        pid_file_contents = None
        result["findings"].append({"check": "launch-pid-file independently readable", "pass": False,
                                    "detail": str(exc)})

    result["findings"].append({
        "check": "the monitor's claimed bound_pid independently matches the launcher's own launch.pid "
                 "contents (cross-checked by this driver reading the file itself, not by parsing the "
                 "monitor's claim alone)",
        "pass": pid_file_contents is not None and bound_pid == pid_file_contents,
        "detail": {"bound_pid": bound_pid, "launch_pid_file_contents": pid_file_contents},
    })

    exe_still_present = os.path.isfile(matched_exe)
    recomputed_digest = sha256_file(matched_exe) if exe_still_present else None
    result["findings"].append({
        "check": "this driver independently re-reads matched_exe from disk and recomputes its SHA-256 "
                 "itself (separate code path from gate_d_monitor.py's own sha256_file), and it matches "
                 "the expected interpreter digest",
        "pass": exe_still_present and recomputed_digest == expected_sha256,
        "detail": {"matched_exe": matched_exe, "recomputed_sha256": recomputed_digest,
                   "expected_sha256": expected_sha256, "exe_still_present": exe_still_present},
    })

    task_dir_exists = os.path.exists(f"/proc/{matched_pid}/task")
    result["findings"].append({
        "check": "the matched_pid named in MONITOR_START_CONFIRMED independently resolves to a real "
                 "process in /proc at the time of this check (it may have since exited; recorded either way)",
        "pass": True,  # informational: either state is valid depending on timing, but it must resolve to a fact, not a guess
        "detail": {"matched_pid": matched_pid, "process_still_present_at_check_time": task_dir_exists},
    })

    result["all_pass"] = all(f["pass"] for f in result["findings"])
    return result


def run_scenario(scenario, record_dir):
    os.makedirs(record_dir, exist_ok=False)
    driver_log = open(os.path.join(record_dir, "DRIVER_LOG.txt"), "w")
    monitor_record_dir = os.path.join(record_dir, "monitor")
    job_dir = os.path.join(record_dir, "job")
    launch_pid_file = os.path.join(record_dir, "launch.pid")
    monitor_log_path = os.path.join(monitor_record_dir, "MONITOR_LOG.jsonl")

    py = sys.executable
    expected_sha256 = sha256_file(os.path.realpath(py))
    log(driver_log, "SCENARIO", scenario, "python", py, "sha256", expected_sha256)

    if scenario == "startup_failure_wrong_digest":
        expected_sha256_for_monitor = hashlib.sha256(b"deliberately wrong digest for this rehearsal").hexdigest()
    else:
        expected_sha256_for_monitor = expected_sha256

    # -- step 8 (Revision 2): start the monitor FIRST, before the harness exists --
    monitor_args = [py, MONITOR_TOOL, "--launch-pid-file", launch_pid_file, "--record-dir", monitor_record_dir,
                     "--interpreter-sha256", expected_sha256_for_monitor, "--interval-seconds", str(INTERVAL),
                     "--startup-grace-samples", str(GRACE_SAMPLES), "--test-mode"]
    monitor_proc = subprocess.Popen(monitor_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    log(driver_log, "monitor started, pid", monitor_proc.pid)

    ready = wait_for_event(monitor_log_path, "MONITOR_READY", timeout=10, driver_log=driver_log)
    if ready is None:
        monitor_proc.kill()
        out, err = monitor_proc.communicate(timeout=5)
        log(driver_log, "FAIL: MONITOR_READY never observed; monitor stdout:", out, "stderr:", err)
        driver_log.close()
        return False
    log(driver_log, "MONITOR_READY observed:", ready)

    harness_proc = None
    if scenario == "startup_failure_pid_never_appears":
        log(driver_log, "deliberately never creating", launch_pid_file)
    elif scenario == "short_lived_process":
        # -- step 9: launch the stand-in harness with the exact PID-capture idiom, running one stage
        # long enough for the monitor's binding scan to reliably catch it (1.5s spans several
        # sampling intervals at this rehearsal's fast cadence) but short enough overall that the run
        # ends, and HARNESS_PROCESS_ENDED is observed, well before the monitor is stopped --
        harness_proc = subprocess.Popen(["bash", STANDIN, job_dir, "1.5", "1"])
        with open(launch_pid_file, "w") as f:
            f.write(str(harness_proc.pid) + "\n")
        log(driver_log, "short-lived stand-in harness started, pid", harness_proc.pid)
    elif scenario == "startup_failure_wrong_digest":
        # deliberately long-lived relative to the monitor's startup-grace budget (10 * 0.5s = 5s), so
        # the stand-in harness is still unambiguously running when the monitor declares STARTUP_FAILURE
        # -- the point being checked is that the monitor never touches it, not that it happened to end
        harness_proc = subprocess.Popen(["bash", STANDIN, job_dir, "2", "10"])
        with open(launch_pid_file, "w") as f:
            f.write(str(harness_proc.pid) + "\n")
        log(driver_log, "stand-in harness started (deliberately long-lived), pid", harness_proc.pid)
    else:
        harness_proc = subprocess.Popen(["bash", STANDIN, job_dir, "1", "4"])
        with open(launch_pid_file, "w") as f:
            f.write(str(harness_proc.pid) + "\n")
        log(driver_log, "stand-in harness started, pid", harness_proc.pid)

    confirmed = None
    if scenario not in ("startup_failure_pid_never_appears", "startup_failure_wrong_digest"):
        confirmed = wait_for_event(monitor_log_path, "MONITOR_START_CONFIRMED", timeout=15, driver_log=driver_log)

    outcome_ok = False
    if scenario in ("startup_failure_pid_never_appears", "startup_failure_wrong_digest"):
        monitor_proc.wait(timeout=20)
        events = read_log_events(monitor_log_path)
        fail_events = [e for e in events if e["event"] == "MONITOR_FAILURE"]
        stop_events = [e for e in events if e["event"] == "MONITOR_STOP"]
        outcome_ok = (monitor_proc.returncode == 3 and len(fail_events) == 1
                      and fail_events[0]["phase"] == "STARTUP" and len(stop_events) == 1
                      and not stop_events[0]["clean"])
        log(driver_log, "expected STARTUP_FAILURE: rc", monitor_proc.returncode, "fail_events", fail_events,
            "outcome_ok", outcome_ok)
        if scenario == "startup_failure_wrong_digest":
            still_running = harness_proc is not None and harness_proc.poll() is None
            log(driver_log, "harness left untouched by monitor (never signalled):", still_running)
            outcome_ok = outcome_ok and still_running
            if harness_proc is not None:
                harness_proc.kill(); harness_proc.wait(timeout=5)
        verification = independent_verify_binding(monitor_record_dir, launch_pid_file,
                                                    expected_sha256_for_monitor, driver_log)
        # for a startup failure, independent confirmation of NON-binding matters just as much as
        # confirmation of binding would for a success case
        verification["note"] = "startup failure scenario: absence of MONITOR_START_CONFIRMED is the expected, verified fact"
    else:
        if confirmed is None:
            log(driver_log, "FAIL: MONITOR_START_CONFIRMED never observed")
            monitor_proc.kill()
            if harness_proc is not None:
                harness_proc.kill(); harness_proc.wait(timeout=5)
            driver_log.close()
            return False
        log(driver_log, "MONITOR_START_CONFIRMED observed:", confirmed)

        verification = independent_verify_binding(monitor_record_dir, launch_pid_file,
                                                    expected_sha256_for_monitor, driver_log)
        log(driver_log, "independent verification all_pass:", verification.get("all_pass"))

        # let the stand-in harness run to completion, observed read-only (poll STATUS.txt, per protocol step 10)
        harness_proc.wait(timeout=30)
        log(driver_log, "stand-in harness exited rc", harness_proc.returncode)
        if scenario == "short_lived_process":
            ended = wait_for_event(monitor_log_path, "HARNESS_PROCESS_ENDED", timeout=10, driver_log=driver_log)
            log(driver_log, "HARNESS_PROCESS_ENDED observed:", ended)

        time.sleep(INTERVAL * 2)  # let at least one more sample land before stopping the monitor
        monitor_proc.send_signal(__import__("signal").SIGTERM)
        monitor_proc.wait(timeout=20)
        events = read_log_events(monitor_log_path)
        stop_events = [e for e in events if e["event"] == "MONITOR_STOP"]
        samples = [e for e in events if e["event"] == "SAMPLE"]
        outcome_ok = (monitor_proc.returncode == 0 and len(stop_events) == 1 and stop_events[0]["clean"]
                      and stop_events[0]["reason"] == "SIGTERM" and len(samples) > 0
                      and verification.get("all_pass"))
        if scenario == "short_lived_process":
            outcome_ok = outcome_ok and ended is not None and ended["pid_reused"] is False
        log(driver_log, "clean stop: rc", monitor_proc.returncode, "stop_events", stop_events,
            "sample_count", len(samples), "outcome_ok", outcome_ok)

    with open(os.path.join(record_dir, "INDEPENDENT_VERIFICATION.json"), "w") as f:
        json.dump(verification, f, indent=2, default=str)

    with open(os.path.join(record_dir, "OUTCOME.txt"), "w") as f:
        f.write(f"scenario={scenario}\noutcome_ok={outcome_ok}\nchecked_at={now_iso()}\n")

    log(driver_log, "SCENARIO", scenario, "RESULT", "PASS" if outcome_ok else "FAIL")
    driver_log.close()
    return outcome_ok


if __name__ == "__main__":
    scenario, record_dir = sys.argv[1], os.path.abspath(sys.argv[2])
    ok = run_scenario(scenario, record_dir)
    sys.exit(0 if ok else 1)
