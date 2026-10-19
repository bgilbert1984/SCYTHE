#!/usr/bin/env python3
"""r4 full-run preflight, CHECK_ONLY. Read-only with respect to the job: it NEVER creates the job directory and NEVER starts run_job.sh.
Everything it writes goes to ATTEMPT_DIR (the external preflight record), which is checksummed last.

  r4_preflight.py --mode CHECK_ONLY --archive ARCHIVE --python /abs/python --job-dir /abs/new/job --attempt-dir /abs/new/record_dir --repo CHECKOUT_OF_MAIN
                  [--operator-confirms-powered-awake]

Implements declaration sections 2-4 of docs/research/metamorphic_r4_full_run_declaration.md (merged). Exit 0 iff every machine-checkable item PASSes.
The operator-confirmation item (host powered, sleep/hibernate suspended: not detectable from WSL) is recorded as PENDING unless the flag is given; PENDING does not hide a failure.
"""
import argparse, datetime, hashlib, json, os, platform, re, secrets, shutil, socket, subprocess, sys, tarfile, tempfile

DECL = {"package_sha256": "2b2e3251b678a5e83735ee663176f7763482a6948a7d5cb57f244dac1142874a", "source_commit": "8d34de9ac0b1f4758cda3412b364582b4271ffe1",
        "python": (3, 12, 13), "numpy": "2.5.1", "min_free_gb": 10, "min_mem_gb": 4,
        "run_job_sha256": "02453e0d2b29dd635b0389373f851886293c0c8b37ec431538e01856ffa22d20",
        "harness_sha256": "1ac4fe34260f6ba63056ed7dbf13f604d9b3f53f8ac77908f4c1ad052eac6687"}
ap = argparse.ArgumentParser()
ap.add_argument("--mode", required=True); ap.add_argument("--archive", required=True); ap.add_argument("--python", required=True)
ap.add_argument("--job-dir", required=True); ap.add_argument("--attempt-dir", required=True); ap.add_argument("--repo", required=True)
ap.add_argument("--operator-confirms-powered-awake", action="store_true")
a = ap.parse_args()
if a.mode != "CHECK_ONLY": sys.exit("only --mode CHECK_ONLY is implemented; launching is not authorized")
if os.path.exists(a.attempt_dir): sys.exit("attempt dir exists; refusing to overwrite a preflight record")
if not (os.path.isabs(a.attempt_dir) and os.path.isabs(a.job_dir) and os.path.isabs(a.python)): sys.exit("PYTHON, job dir and attempt dir must be absolute paths")
os.makedirs(a.attempt_dir)
now = lambda: datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
sha = lambda b: hashlib.sha256(b).hexdigest()
def fsha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
    return h.hexdigest()
results, rec = [], {}
def item(name, ok, detail="", pending=False):
    results.append((name, "PENDING" if pending else ("PASS" if ok else "FAIL"), detail)); print(f"{results[-1][1]:7s} {name}  {detail}")
def sh(args, log, **k):
    r = subprocess.run(args, capture_output=True, text=True, **k)
    with open(os.path.join(a.attempt_dir, log), "w") as fh: fh.write(f"$ {' '.join(map(str, args))}\n{r.stdout}\n--- stderr\n{r.stderr}\n--- rc {r.returncode}\n")
    return r

# ---- 7 (recorded first): attempt identity binding
host = {"hostname": socket.gethostname(), "kernel": platform.release(), "machine": platform.machine(), "boot_id": open("/proc/sys/kernel/random/boot_id").read().strip()}
attempt_id = f"R4FULL-{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{DECL['package_sha256'][:12]}-{host['hostname']}-{secrets.token_hex(4)}"

# ---- 1 package
arc_copy = os.path.join(a.attempt_dir, os.path.basename(a.archive)); shutil.copyfile(a.archive, arc_copy)       # verifiers write beside the archive; work on a copy
side = a.archive + ".sha256"; side_ok = os.path.isfile(side) and open(side).read().split()[0] == DECL["package_sha256"]
if os.path.isfile(side): shutil.copyfile(side, arc_copy + ".sha256")          # the package verifier requires the builder's digest sidecar
d = fsha(arc_copy); item("1 archive SHA-256 equals the declared package digest", d == DECL["package_sha256"], d)
item("1a archive sidecar records the declared digest", side_ok)
ex = os.path.join(a.attempt_dir, "extracted_package"); os.makedirs(ex)
with tarfile.open(arc_copy) as tf:
    bad = [m.name for m in tf.getmembers() if m.name.startswith("/") or ".." in m.name.split("/") or m.issym() or m.islnk() or m.isdev()]
    item("1b safe extraction (no absolute paths, '..', links, devices)", not bad, str(bad[:3]))
    if not bad: tf.extractall(ex, filter="data")
pkg = os.path.join(ex, "scythe_r4_1_pkg")
r = sh(["sha256sum", "-c", "SHA256SUMS"], "sha256sum_package.txt", cwd=pkg) if os.path.isdir(pkg) else None
item("1c sha256sum -c SHA256SUMS in the extracted package", bool(r) and r.returncode == 0, f"{r.stdout.count(': OK')} OK" if r else "no package dir")
item("1d runner and harness digests equal the declared ones", os.path.isdir(pkg) and fsha(os.path.join(pkg, "tools/r4_job/run_job.sh")) == DECL["run_job_sha256"] and fsha(os.path.join(pkg, "tools/rf_metamorphic_harness.py")) == DECL["harness_sha256"])

# ---- 2 interpreter
py = a.python; ok_py = os.path.isabs(py) and os.access(py, os.X_OK); pyinfo = {}
if ok_py:
    code = "import sys,numpy,json;print(json.dumps({'version':list(sys.version_info[:3]),'full':sys.version,'numpy':numpy.__version__,'numpy_file':numpy.__file__,'prefix':sys.prefix,'executable':sys.executable}))"
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    r = sh([py, "-I", "-c", code], "interpreter.txt", env=env)
    if r.returncode == 0: pyinfo = json.loads(r.stdout.strip().splitlines()[-1])
real = os.path.realpath(py) if ok_py else None
pyinfo.update({"given_path": py, "resolved_path": real, "binary_sha256": fsha(real) if real and os.path.isfile(real) else None})
item("2 PYTHON is an absolute executable path", ok_py, py)
item("2b Python is exactly 3.12.13 and NumPy exactly 2.5.1 (otherwise the run is refused)", tuple(pyinfo.get("version", ())) == DECL["python"] and pyinfo.get("numpy") == DECL["numpy"], f"{pyinfo.get('version')} numpy {pyinfo.get('numpy')}")
rec["interpreter"] = pyinfo

# ---- 3 job directory and resources
item("3 job directory is an absolute path that does not exist", os.path.isabs(a.job_dir) and not os.path.exists(a.job_dir), a.job_dir)
par = os.path.dirname(a.job_dir.rstrip("/")); item("3b job directory parent exists and is writable", os.path.isdir(par) and os.access(par, os.W_OK), par)
item("3c job directory is not inside the package or the record directory", not (a.job_dir + "/").startswith((pkg + "/", a.attempt_dir + "/")) and not (pkg + "/").startswith(a.job_dir + "/"))
def free_gb(path):
    s = os.statvfs(path); return s.f_bavail * s.f_frsize / 1e9
tmpdir = os.environ.get("TMPDIR") or tempfile.gettempdir()
fj, ft = free_gb(par) if os.path.isdir(par) else -1, free_gb(tmpdir)
item(f"3d at least {DECL['min_free_gb']} GB free on the job filesystem (measured, not inferred)", fj >= DECL["min_free_gb"], f"{fj:.1f} GB at {par}")
item(f"3e at least {DECL['min_free_gb']} GB free on the TMPDIR filesystem", ft >= DECL["min_free_gb"], f"{ft:.1f} GB at {tmpdir}")
mem = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo") if ":" in l}
item(f"3f at least {DECL['min_mem_gb']} GB available memory", mem["MemAvailable"] / 1e6 >= DECL["min_mem_gb"], f"{mem['MemAvailable'] / 1e6:.1f} GB")

# ---- 4 separate verification targets
repo_head = sh(["git", "-C", a.repo, "rev-parse", "HEAD"], "repo_head.txt").stdout.strip(); rec["repo_head"] = repo_head
status = sh(["git", "-C", a.repo, "status", "--porcelain"], "repo_status.txt").stdout.strip()
item("4 verification repository checkout is clean", status == "", repo_head)
if os.path.isdir(pkg) and py and ok_py:
    r = sh([py, os.path.join(a.repo, "tools/r4_package/verify_package.py"), a.repo, DECL["source_commit"], arc_copy, os.path.join(a.attempt_dir, "verify_extract"), "--python", py], "verify_package.txt")
    last = [l for l in r.stdout.splitlines() if l.startswith("VERIFICATION")]
    item("4a package verifier: the frozen ARCHIVE against its recorded source commit", r.returncode == 0, last[-1] if last else f"rc {r.returncode}")
else: item("4a package verifier", False, "package or interpreter unavailable")
pristine = os.path.join(a.attempt_dir, "pristine_evidence"); os.makedirs(pristine)
g = subprocess.run(["git", "-C", a.repo, "archive", "--format=tar", "HEAD", "docs/evidence", "docs/research/r4_package_tools_provenance.md"], capture_output=True)
subprocess.run(["tar", "-x", "-C", pristine], input=g.stdout, check=True)
r = sh([py, os.path.join(a.repo, "tools/r4_package/verify_committed_evidence.py"), os.path.join(pristine, "docs/evidence"), os.path.join(pristine, "docs/research/r4_package_tools_provenance.md")], "verify_committed_evidence.txt")
item("4b evidence verifier: the COMMITTED HISTORICAL EVIDENCE (not the package) from a pristine archive", r.returncode == 0, " | ".join(l for l in r.stdout.splitlines() if l.startswith(("COMMITTED", "ORIGINAL", "EVIDENCE_V"))))

# ---- 5 host
ac = [open(f"/sys/class/power_supply/{n}/online").read().strip() for n in os.listdir("/sys/class/power_supply") if os.path.exists(f"/sys/class/power_supply/{n}/online")] if os.path.isdir("/sys/class/power_supply") else []
item("5 AC power reported online (where the platform reports it)", (not ac) or "1" in ac, f"online flags {ac}")
item("5b host powered and awake for the whole window; sleep, hibernate and lid-close actions suspended (not detectable from WSL)", a.operator_confirms_powered_awake,
     "operator confirmation given" if a.operator_confirms_powered_awake else "OPERATOR CONFIRMATION REQUIRED before any launch", pending=not a.operator_confirms_powered_awake)
rec["host"] = host; rec["load_avg"] = os.getloadavg(); rec["uptime_s"] = float(open("/proc/uptime").read().split()[0]); rec["nproc"] = os.cpu_count()

# ---- 6 competing r4 job
pat = re.compile(r"run_job\.sh|rf_metamorphic_harness|generate_holdout|test_rf_metamorphic_r4|r4_job/(seal|verify|gate)")
mine = {os.getpid(), os.getppid()}
comp = []
for pid in filter(str.isdigit, os.listdir("/proc")):
    try: cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="replace")
    except OSError: continue
    if int(pid) not in mine and pat.search(cmd) and "r4_preflight" not in cmd and "pgrep" not in cmd: comp.append((pid, cmd[:120]))
item("6 no competing r4 job process is running", not comp, str(comp[:3]))

# ---- record
rec.update({"preflight_mode": "CHECK_ONLY", "launch": "NOT_PERFORMED", "attempt_id_candidate": attempt_id, "timestamp_utc": now(), "declaration": DECL | {"python": list(DECL["python"])},
            "package_sha256": d, "job_dir": a.job_dir, "attempt_dir": a.attempt_dir, "items": [{"name": n, "status": s, "detail": x} for n, s, x in results]})
fails = [n for n, s, _ in results if s == "FAIL"]; pend = [n for n, s, _ in results if s == "PENDING"]
verdict = "FAIL" if fails else ("PASS_OPERATOR_CONFIRMATION_PENDING" if pend else "PASS")
rec["verdict"] = verdict
with open(os.path.join(a.attempt_dir, "PREFLIGHT_RECORD.json"), "w") as fh: json.dump(rec, fh, indent=1, default=str)
txt = [f"R4 FULL-RUN PREFLIGHT RECORD  (mode CHECK_ONLY; run_job.sh NOT started; job directory NOT created)", f"ATTEMPT_ID_CANDIDATE: {attempt_id}  (bound to package {d}, host {host['hostname']}/{host['boot_id']}, job dir {a.job_dir})",
       f"TIMESTAMP_UTC: {rec['timestamp_utc']}", f"INTERPRETER: given {py} resolved {real} binary sha256 {pyinfo.get('binary_sha256')} numpy {pyinfo.get('numpy')} at {pyinfo.get('numpy_file')}", ""]
txt += [f"{s:7s} {n}  {x}" for n, s, x in results] + ["", f"PREFLIGHT_VERDICT: {verdict}", "LAUNCH: NOT_PERFORMED (a launch needs its own explicit authorization and a fresh LAUNCH-mode record)"]
with open(os.path.join(a.attempt_dir, "PREFLIGHT_RECORD.txt"), "w") as fh: fh.write("\n".join(txt) + "\n")
lines = []
for root, _, fs in os.walk(a.attempt_dir):
    for f in sorted(fs):
        p = os.path.join(root, f); rel = os.path.relpath(p, a.attempt_dir)
        if rel != "PREFLIGHT_SHA256SUMS" and not rel.startswith(("extracted_package/", "verify_extract/", "pristine_evidence/")): lines.append(f"{fsha(p)}  {rel}")
with open(os.path.join(a.attempt_dir, "PREFLIGHT_SHA256SUMS"), "w") as fh: fh.write("\n".join(sorted(lines, key=lambda l: l.split()[1])) + "\n")
print("\n".join(txt[-3:])); sys.exit(1 if fails else 0)
