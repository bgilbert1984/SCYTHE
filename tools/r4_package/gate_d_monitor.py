#!/usr/bin/env python3
"""Gate D read-only resource monitor (design: docs/research/metamorphic_r4_gate_d_protocol.md section 5). Stdlib only.

    gate_d_monitor.py --launch-pid-file /abs/launch.pid --record-dir /abs/new-dir --interpreter-sha256 HEX
                       [--interval-seconds 5] [--startup-grace-samples 2] [--oom-check-command "CMD ..."]

Binds to the PID written in --launch-pid-file (never discovers a process by name). Confirms startup by finding, within
--startup-grace-samples sampling intervals, some process in the bound PID's descendant tree whose executable's SHA-256
equals --interpreter-sha256 (the digest already frozen by the Gate C RECHECK); a bound PID that no longer exists, or for
which no such process appears in time, is MONITOR_FAILURE: STARTUP, exit 3 -- the harness is never touched by this tool.
Samples every --interval-seconds (no less than 5 in an actual Gate D attempt; this tool does not enforce that floor so it
can be tested quickly): summed VmRSS of the live descendant tree, each PID's kernel-tracked VmHWM high-water mark (both a
live-tree total and a running sum over every PID ever seen, the larger reported as authoritative -- sampling cadence can
miss a short peak, VmHWM cannot), system MemAvailable/SwapFree, and a best-effort OOM-kill count (journalctl -k, then
dmesg; never sudo, never a kernel-setting change; unavailable is recorded as OOM_OBSERVABILITY_UNAVAILABLE, never a silent
zero). The bound PID is re-identified every sample by its /proc/<pid>/stat start time, so a reused PID is detected as the
harness having ended, not silently monitored as if it were still the same process.
It never signals, pauses or kills the harness: on a low-memory or OOM observation it only records. It stops only on
SIGTERM to its own PID, writing a final MONITOR_STOP record (clean) before exiting 0; any other exception is MONITOR_STOP
(not clean), exit 2. It writes only its own append-only MONITOR_LOG.jsonl inside --record-dir (which must not already
exist; its parent is not created). No ptrace, no niceness/cgroup change, no other file.
"""
import argparse, hashlib, json, os, re, shlex, signal, subprocess, sys, time, traceback

LOG_NAME = "MONITOR_LOG.jsonl"
OOM_PATTERN = re.compile(r"oom|out of memory", re.IGNORECASE)
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


def count_oom_events(text):
    return sum(1 for line in text.splitlines() if OOM_PATTERN.search(line))


def main():
    global _log_fh
    ap = argparse.ArgumentParser()
    for f in ("--launch-pid-file", "--record-dir", "--interpreter-sha256"):
        ap.add_argument(f, required=True)
    ap.add_argument("--interval-seconds", type=float, default=5.0)
    ap.add_argument("--startup-grace-samples", type=int, default=2)
    ap.add_argument("--oom-check-command", default="", help="tests only: override the OOM-log source command")
    a = ap.parse_args()
    for p in (a.launch_pid_file, a.record_dir):
        if not os.path.isabs(p):
            sys.exit("--launch-pid-file and --record-dir must be absolute paths")
    if os.path.exists(a.record_dir):
        sys.exit("record dir exists; refusing to overwrite a monitor record")
    if not os.path.isdir(os.path.dirname(a.record_dir.rstrip("/"))):
        sys.exit("the parent of --record-dir must already exist; refusing to create parent directories")
    os.mkdir(a.record_dir)
    _log_fh = open(os.path.join(a.record_dir, LOG_NAME), "a")

    stop_requested = {"flag": False}

    def handle_sigterm(signum, frame):
        stop_requested["flag"] = True

    signal.signal(signal.SIGTERM, handle_sigterm)

    emit("MONITOR_START", monitor_pid=os.getpid(), launch_pid_file=a.launch_pid_file, record_dir=a.record_dir,
         interval_seconds=a.interval_seconds, startup_grace_samples=a.startup_grace_samples)

    try:
        root_pid = read_launch_pid(a.launch_pid_file)
    except (OSError, ValueError) as exc:
        emit("MONITOR_FAILURE", phase="STARTUP", detail=f"cannot read launch pid file: {exc}")
        emit("MONITOR_STOP", clean=False, reason="STARTUP_FAILURE", samples_taken=0)
        sys.exit(3)

    oom_source = read_oom_source(a.oom_check_command)
    oom_available = oom_source is not None
    initial_oom_count = count_oom_events(oom_source) if oom_available else None
    emit("OOM_OBSERVABILITY", available=oom_available)

    confirmed, root_starttime = False, None
    for _ in range(max(1, a.startup_grace_samples)):
        tree = resolve_tree(root_pid)
        match = find_interpreter_match(tree, a.interpreter_sha256)
        if match:
            confirmed = True
            root_starttime = read_starttime(root_pid)
            emit("MONITOR_START_CONFIRMED", bound_pid=root_pid, matched_pid=match[0], matched_exe=match[1],
                 interpreter_sha256_expected=a.interpreter_sha256, root_starttime=root_starttime)
            break
        time.sleep(a.interval_seconds)
    if not confirmed:
        emit("MONITOR_FAILURE", phase="STARTUP",
             detail="no process in the bound PID's descendant tree matched the expected interpreter digest within the startup grace period")
        emit("MONITOR_STOP", clean=False, reason="STARTUP_FAILURE", samples_taken=0)
        sys.exit(3)

    samples_taken, vmhwm_history, ended_logged, peak_authoritative = 0, {}, False, 0
    while not stop_requested["flag"]:
        current_starttime = read_starttime(root_pid)
        root_alive = current_starttime is not None and current_starttime == root_starttime
        tree = resolve_tree(root_pid) if root_alive else []
        if not tree and not ended_logged:
            emit("HARNESS_PROCESS_ENDED", pid_reused=bool(os.path.exists(f"/proc/{root_pid}")) and not root_alive)
            ended_logged = True

        vmrss_sum, vmhwm_live_sum = 0, 0
        for pid in tree:
            st = read_proc_status(pid)
            if st is None:
                continue
            vmrss_sum += st["VmRSS"]
            vmhwm_live_sum += st["VmHWM"]
            vmhwm_history[pid] = max(vmhwm_history.get(pid, 0), st["VmHWM"])
        running_sum = sum(vmhwm_history.values())
        peak_authoritative = max(peak_authoritative, running_sum, vmhwm_live_sum)

        oom_events = None
        if oom_available:
            fresh = read_oom_source(a.oom_check_command)
            if fresh is not None:
                oom_events = max(0, count_oom_events(fresh) - initial_oom_count)

        emit("SAMPLE", pid_tree=sorted(tree), vmrss_bytes=vmrss_sum, vmhwm_live_bytes=vmhwm_live_sum,
             vmhwm_running_sum_bytes=running_sum, peak_authoritative_bytes=peak_authoritative,
             oom_observability="AVAILABLE" if oom_available else "UNAVAILABLE", oom_events_since_start=oom_events,
             **read_meminfo())
        samples_taken += 1
        time.sleep(a.interval_seconds)

    emit("MONITOR_STOP", clean=True, reason="SIGTERM", samples_taken=samples_taken, peak_authoritative_bytes=peak_authoritative)
    _log_fh.close()
    sys.exit(0)


if __name__ == "__main__":
    main()
