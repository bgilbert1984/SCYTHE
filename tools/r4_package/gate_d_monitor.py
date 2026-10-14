#!/usr/bin/env python3
"""Gate D read-only resource monitor (design: docs/research/metamorphic_r4_gate_d_protocol.md section 5,
Revision 2). Stdlib only.

    gate_d_monitor.py --launch-pid-file /abs/launch.pid --record-dir /abs/new-dir --interpreter-sha256 HEX
                       [--interval-seconds 5] [--startup-grace-samples 2] [--oom-check-command "CMD ..." --test-mode]

All arguments are validated -- paths absolute, --interpreter-sha256 exactly 64 hex characters,
--startup-grace-samples a positive integer, --interval-seconds positive -- before --record-dir is
created, so a malformed invocation never leaves a record directory behind. Production invocations
(no --test-mode) refuse an --interval-seconds below 5 and refuse --oom-check-command outright: both
exist only so this tool's own tests can run fast and inject a fake OOM-log source, and neither is
permitted in a real Gate D attempt.

Startup order (so the monitor can be started *before* the harness, per the protocol's launch
sequencing -- it must never require the launched process to already exist just to come up):
record directory and log are established, then MONITOR_READY is emitted -- that is the signal that
it is now safe to launch exactly one authorized job. Only after MONITOR_READY does the monitor begin
polling --launch-pid-file; it never discovers or matches a process by name, and it never uses
ptrace. Once the file is readable, it resolves the full descendant set rooted at that PID (via
/proc/<pid>/task/*/children, re-walked every sample) and looks for some process in that tree whose
executable's SHA-256 equals --interpreter-sha256 (the digest already frozen by the Gate C RECHECK).
Both conditions -- the file appearing, and a matching process appearing in its tree -- must be
satisfied within --startup-grace-samples sampling intervals after MONITOR_READY, or this is
MONITOR_FAILURE: STARTUP, exit 3 -- the harness is never touched by this tool, and a startup failure
is never silently retried.

Samples every --interval-seconds (>=5 in a production invocation): the live descendant tree's summed
VmRSS (the only truly *simultaneous* measurement this tool can make, so the running max of it across
samples -- peak_sampled_tree_rss_bytes -- is reported as the best available concurrent peak; sampling
cadence can still miss a short-lived spike between samples, which is why this is "sampled", not
"authoritative"); each PID's kernel-tracked VmHWM high-water mark is also tracked, but only as a
*nonconcurrent* aggregate (sum_observed_process_hwm_bytes = sum of every PID's own best-seen VmHWM,
including PIDs that have since exited) -- processes counted in that sum may never have coexisted, so
it is never added to or confused with the simultaneous figure. A third field, peak_job_memory_bytes,
is reserved for an independent whole-job measurement (e.g. a cgroup accounting file) if one is ever
wired up for a given host; this tool does not implement one, so it is always null, and that is
recorded, not hidden. System MemAvailable/SwapFree are read every sample. OOM-kill observability
(journalctl -k, then dmesg; never sudo, never a kernel-setting change) is re-checked every sample, not
assumed stable once seen: a source that stops answering mid-run is recorded as a transition to
UNAVAILABLE, and one that resumes afterward is recorded as a transition back to AVAILABLE with a
fresh baseline, so the record never shows a stale AVAILABLE next to an absent count. The reported
count, oom_related_log_lines_since_baseline, is exactly that -- a count of matching log lines, not a
count of verified unique OOM-kill incidents (one real event can log more than one matching line, and
the log source itself may not retain full history).

The bound PID is re-identified every sample by its /proc/<pid>/stat start time, so a reused PID is
detected as the harness having ended, not silently monitored as if it were still the same process.
It never signals, pauses or kills the harness: on a low-memory or OOM observation it only records.
It stops only on SIGTERM to its own PID, writing a final MONITOR_STOP record (clean) before exiting 0;
any other exception is MONITOR_STOP (not clean), exit 2. It writes only its own append-only
MONITOR_LOG.jsonl inside --record-dir (which must not already exist; its parent is not created).
No ptrace, no niceness/cgroup change, no other file.
"""
import argparse, hashlib, json, os, re, shlex, signal, subprocess, sys, time, traceback

LOG_NAME = "MONITOR_LOG.jsonl"
OOM_PATTERN = re.compile(r"oom|out of memory", re.IGNORECASE)
INTERPRETER_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
PRODUCTION_INTERVAL_FLOOR_SECONDS = 5.0
_log_fh = None


def now_iso():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def emit(event, **fields):
    rec = {"ts": now_iso(), "event": event, **fields}
    if _log_fh:
        _log_fh.write(json.dumps(rec, default=str) + "\n")
        _log_fh.flush()
        os.fsync(_log_fh.fileno())
    return rec


def _crash(kind, value, tb):
    traceback.print_exception(kind, value, tb)
    try:
        emit("MONITOR_FAILURE", phase="RUNTIME", detail=f"{kind.__name__}: {value}")
        emit("MONITOR_STOP", clean=False, reason="CRASH")
    except Exception:
        pass
    print("MONITOR_FAILURE: CRASH", kind.__name__, value)
    sys.stdout.flush()
    os._exit(2)


sys.excepthook = _crash


def sha256_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for b in iter(lambda: f.read(1 << 20), b""):
                h.update(b)
    except OSError:
        return None
    return h.hexdigest()


def read_launch_pid(path):
    with open(path) as f:
        return int(f.read().split()[0])


def read_starttime(pid):
    """Field 22 of /proc/<pid>/stat (clock ticks since boot); identifies this PID number across reuse."""
    try:
        text = open(f"/proc/{pid}/stat").read()
    except OSError:
        return None
    after = text[text.rfind(")") + 2:]
    fields = after.split()
    try:
        return int(fields[19])
    except (IndexError, ValueError):
        return None


def children_of(pid):
    kids = set()
    task_dir = f"/proc/{pid}/task"
    try:
        tids = os.listdir(task_dir)
    except OSError:
        return kids
    for tid in tids:
        try:
            kids.update(int(x) for x in open(f"{task_dir}/{tid}/children").read().split())
        except OSError:
            continue
    return kids


def resolve_tree(root_pid):
    """Every currently-live PID in the descendant tree rooted at root_pid, root included; [] if root is gone."""
    if not os.path.exists(f"/proc/{root_pid}"):
        return []
    seen = {root_pid}
    frontier = [root_pid]
    while frontier:
        nxt = []
        for p in frontier:
            for c in children_of(p):
                if c not in seen:
                    seen.add(c)
                    nxt.append(c)
        frontier = nxt
    return [p for p in seen if os.path.exists(f"/proc/{p}")]


def read_proc_status(pid):
    try:
        text = open(f"/proc/{pid}/status").read()
    except OSError:
        return None
    out = {}
    for key in ("VmRSS", "VmHWM"):
        m = re.search(rf"^{key}:\s*(\d+)\s*kB", text, re.MULTILINE)
        out[key] = int(m.group(1)) * 1024 if m else 0
    return out


def read_meminfo():
    out = {}
    for line in open("/proc/meminfo"):
        if ":" in line:
            k, v = line.split(":", 1)
            parts = v.split()
            if parts:
                out[k] = int(parts[0])
    return {"mem_available_kb": out.get("MemAvailable", 0), "swap_free_kb": out.get("SwapFree", 0)}


def find_interpreter_match(pids, expected_sha256):
    for pid in pids:
        exe = None
        try:
            exe = os.path.realpath(f"/proc/{pid}/exe")
        except OSError:
            continue
        if not exe or not os.path.isfile(exe):
            continue
        if sha256_file(exe) == expected_sha256:
            return (pid, exe)
    return None


def read_oom_source(oom_check_command):
    candidates = [shlex.split(oom_check_command)] if oom_check_command else [
        ["journalctl", "-k", "--no-pager", "-o", "cat"], ["dmesg"],
    ]
    for cmd in candidates:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.returncode == 0:
            return r.stdout
    return None


def count_oom_related_lines(text):
    """A count of matching log lines, not of verified unique OOM-kill incidents: one real event can
    log more than one matching line, and the log source may not retain full history."""
    return sum(1 for line in text.splitlines() if OOM_PATTERN.search(line))


def parse_args(argv=None):
    ap = argparse.ArgumentParser()
    for f in ("--launch-pid-file", "--record-dir", "--interpreter-sha256"):
        ap.add_argument(f, required=True)
    ap.add_argument("--interval-seconds", type=float, default=5.0)
    ap.add_argument("--startup-grace-samples", type=int, default=2)
    ap.add_argument("--oom-check-command", default="",
                     help="tests only: overrides the OOM-log source command; requires --test-mode")
    ap.add_argument("--test-mode", action="store_true",
                     help="tests only: lifts the >=5s interval floor and permits --oom-check-command; "
                          "never pass this in a real Gate D attempt")
    a = ap.parse_args(argv)

    for p in (a.launch_pid_file, a.record_dir):
        if not os.path.isabs(p):
            sys.exit("--launch-pid-file and --record-dir must be absolute paths")
    if not INTERPRETER_SHA256_RE.match(a.interpreter_sha256):
        sys.exit("--interpreter-sha256 must be exactly 64 hex characters")
    a.interpreter_sha256 = a.interpreter_sha256.lower()
    if a.startup_grace_samples <= 0:
        sys.exit("--startup-grace-samples must be a positive integer")
    if a.interval_seconds <= 0:
        sys.exit("--interval-seconds must be positive")
    if a.interval_seconds < PRODUCTION_INTERVAL_FLOOR_SECONDS and not a.test_mode:
        sys.exit(f"--interval-seconds below {PRODUCTION_INTERVAL_FLOOR_SECONDS} requires --test-mode "
                  "(a production Gate D attempt must not sample more often than every 5 seconds)")
    if a.oom_check_command and not a.test_mode:
        sys.exit("--oom-check-command is test-only and requires --test-mode")
    if os.path.exists(a.record_dir):
        sys.exit("record dir exists; refusing to overwrite a monitor record")
    if not os.path.isdir(os.path.dirname(a.record_dir.rstrip("/"))):
        sys.exit("the parent of --record-dir must already exist; refusing to create parent directories")
    return a


def main():
    global _log_fh
    a = parse_args()

    os.mkdir(a.record_dir)
    _log_fh = open(os.path.join(a.record_dir, LOG_NAME), "a")

    stop_requested = {"flag": False}

    def handle_sigterm(signum, frame):
        stop_requested["flag"] = True

    signal.signal(signal.SIGTERM, handle_sigterm)

    emit("MONITOR_START", monitor_pid=os.getpid(), launch_pid_file=a.launch_pid_file, record_dir=a.record_dir,
         interval_seconds=a.interval_seconds, startup_grace_samples=a.startup_grace_samples, test_mode=a.test_mode)

    initial_oom_source = read_oom_source(a.oom_check_command)
    oom_available = initial_oom_source is not None
    oom_baseline = count_oom_related_lines(initial_oom_source) if oom_available else None
    emit("OOM_OBSERVABILITY", available=oom_available,
         reason=None if oom_available else "SOURCE_UNAVAILABLE_AT_STARTUP")

    emit("MONITOR_READY", monitor_pid=os.getpid())

    root_pid, root_starttime, confirmed, last_bind_detail = None, None, False, "launch pid file never appeared"
    for _ in range(max(1, a.startup_grace_samples)):
        try:
            candidate_pid = read_launch_pid(a.launch_pid_file)
        except (OSError, ValueError) as exc:
            last_bind_detail = f"launch pid file not yet readable: {exc}"
            time.sleep(a.interval_seconds)
            continue
        tree = resolve_tree(candidate_pid)
        match = find_interpreter_match(tree, a.interpreter_sha256)
        if match:
            root_pid = candidate_pid
            confirmed = True
            root_starttime = read_starttime(root_pid)
            emit("MONITOR_START_CONFIRMED", bound_pid=root_pid, matched_pid=match[0], matched_exe=match[1],
                 interpreter_sha256_expected=a.interpreter_sha256, root_starttime=root_starttime)
            break
        last_bind_detail = (f"pid {candidate_pid} read from the launch pid file, but no process in its "
                             "descendant tree matched the expected interpreter digest")
        time.sleep(a.interval_seconds)
    if not confirmed:
        emit("MONITOR_FAILURE", phase="STARTUP", detail=last_bind_detail)
        emit("MONITOR_STOP", clean=False, reason="STARTUP_FAILURE", samples_taken=0)
        sys.exit(3)

    samples_taken, vmhwm_history, ended_logged = 0, {}, False
    peak_sampled_tree_rss_bytes = 0
    while not stop_requested["flag"]:
        current_starttime = read_starttime(root_pid)
        root_alive = current_starttime is not None and current_starttime == root_starttime
        tree = resolve_tree(root_pid) if root_alive else []
        if not tree and not ended_logged:
            emit("HARNESS_PROCESS_ENDED", pid_reused=bool(os.path.exists(f"/proc/{root_pid}")) and not root_alive)
            ended_logged = True

        vmrss_sum = 0
        for pid in tree:
            st = read_proc_status(pid)
            if st is None:
                continue
            vmrss_sum += st["VmRSS"]
            vmhwm_history[pid] = max(vmhwm_history.get(pid, 0), st["VmHWM"])
        peak_sampled_tree_rss_bytes = max(peak_sampled_tree_rss_bytes, vmrss_sum)
        sum_observed_process_hwm_bytes = sum(vmhwm_history.values())

        fresh_oom = read_oom_source(a.oom_check_command)
        oom_lines = None
        if oom_available:
            if fresh_oom is None:
                oom_available = False
                emit("OOM_OBSERVABILITY", available=False, reason="SOURCE_FAILED_MID_RUN")
            else:
                oom_lines = max(0, count_oom_related_lines(fresh_oom) - oom_baseline)
        elif fresh_oom is not None:
            oom_available = True
            oom_baseline = count_oom_related_lines(fresh_oom)
            emit("OOM_OBSERVABILITY", available=True, reason="SOURCE_REACQUIRED")
            oom_lines = 0

        emit("SAMPLE", pid_tree=sorted(tree), vmrss_tree_bytes=vmrss_sum,
             peak_sampled_tree_rss_bytes=peak_sampled_tree_rss_bytes,
             sum_observed_process_hwm_bytes=sum_observed_process_hwm_bytes,
             peak_job_memory_bytes=None,
             oom_observability="AVAILABLE" if oom_available else "UNAVAILABLE",
             oom_related_log_lines_since_baseline=oom_lines,
             **read_meminfo())
        samples_taken += 1
        time.sleep(a.interval_seconds)

    emit("MONITOR_STOP", clean=True, reason="SIGTERM", samples_taken=samples_taken,
         peak_sampled_tree_rss_bytes=peak_sampled_tree_rss_bytes,
         sum_observed_process_hwm_bytes=sum(vmhwm_history.values()),
         peak_job_memory_bytes=None)
    _log_fh.close()
    sys.exit(0)


if __name__ == "__main__":
    main()
