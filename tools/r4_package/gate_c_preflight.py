#!/usr/bin/env python3
"""Gate C preflight for the replay host (stdlib only; runs ON the replay host). Derived from the local full-run preflight; differences in docs/research/metamorphic_r4_gate_c_preflight.md.

  gate_c_preflight.py --mode CHECK_ONLY --archive A --python /abs/python --job-dir /abs/new --record-dir /abs/new/record [--reference-preflight JSON] [--operator-declaration FILE]
  gate_c_preflight.py --mode RECHECK    --archive A --python /abs/python --job-dir /abs/new --record-dir /abs/new/recheck --prior-record DIR --operator-declaration FILE

CHECK_ONLY records the replay environment (it is NOT required to equal the reference: protocol section 5.3) and checks package identity, interpreter, resources, job-directory isolation and absence
of a competing job. RECHECK, run immediately before launch, requires the environment to equal the CHECK_ONLY record exactly (otherwise the replay is refused).
Field classes (record schema 2): FROZEN (must be equal at RECHECK: package, runner and harness digests, every requirement/threshold, the job path, the interpreter and numerical environment, the declaration identity);
REMEASURED (free space, memory, load, competing processes, job-directory absence: measured again and judged against the FROZEN thresholds, never compared for equality); RECONFIRMED (the operator declaration: same identity,
and independently still valid at RECHECK: inside its window, not withdrawn). A changed measurement is acceptable while the frozen requirement still holds; a changed requirement is refused.
Run it as `<PYTHON> -I -B gate_c_preflight.py ...`; its own interpreter probe also runs with -B so that importing NumPy cannot write bytecode into the interpreter's site-packages. The parent of --record-dir must already exist (no parent is created).
It never creates the job directory, never starts a job, never writes outside --record-dir, never deletes, never installs. Exit 0 iff no item FAILED; a crash exits 2 (GATE_C_TOOL_FAILURE).
"""
import argparse, datetime, hashlib, json, os, platform, re, secrets, shutil, socket, subprocess, sys, tarfile, tempfile, traceback


def _crash(kind, value, tb):
    traceback.print_exception(kind, value, tb); print("GATE_C_TOOL_FAILURE:", kind.__name__, value); sys.stdout.flush(); os._exit(2)


sys.excepthook = _crash
DECL = {"package_sha256": "2b2e3251b678a5e83735ee663176f7763482a6948a7d5cb57f244dac1142874a", "source_commit": "8d34de9ac0b1f4758cda3412b364582b4271ffe1", "min_free_gb": 10, "min_mem_gb": 4,
        "run_job_sha256": "02453e0d2b29dd635b0389373f851886293c0c8b37ec431538e01856ffa22d20", "harness_sha256": "1ac4fe34260f6ba63056ed7dbf13f604d9b3f53f8ac77908f4c1ad052eac6687",
        "window_hours": 4, "local_runtime_baseline_s": {"total": 1491, "bounded_controls": 162, "fixtures_and_pins": 24, "smoke_and_gate": 203, "full_harness_and_gate": 1101}}
ap = argparse.ArgumentParser()
for f in ("--mode", "--archive", "--python", "--job-dir", "--record-dir"): ap.add_argument(f, required=True)
ap.add_argument("--reference-preflight"); ap.add_argument("--prior-record"); ap.add_argument("--declaration"); ap.add_argument("--operator-declaration"); ap.add_argument("--now-utc"); ap.add_argument("--attempt-label", default="")
a = ap.parse_args()
if a.mode not in ("CHECK_ONLY", "RECHECK"): sys.exit("modes: CHECK_ONLY or RECHECK; launching is not part of this tool")
override = False
if a.declaration:                       # tests only: a declaration override makes the verdict permanently invalid for Gate C
    DECL.update(json.load(open(a.declaration))); override = True
if a.now_utc and not override: sys.exit("--now-utc is a test hook and is only honoured together with --declaration (which invalidates the verdict)")
SCHEMA_VERSION = 2
SCOPE = "no planned maintenance, reboot or reclaim of the host during the window"
REQUIREMENT_KEYS = ("package_sha256", "source_commit", "min_free_gb", "min_mem_gb", "run_job_sha256", "harness_sha256", "window_hours")
for p in (a.python, a.job_dir, a.record_dir):
    if not os.path.isabs(p): sys.exit("PYTHON, job dir and record dir must be absolute paths")
if os.path.exists(a.record_dir): sys.exit("record dir exists; refusing to overwrite a preflight record")
if a.mode == "RECHECK" and not a.prior_record: sys.exit("RECHECK needs --prior-record")
if not os.path.isdir(os.path.dirname(a.record_dir.rstrip('/'))): sys.exit('the parent of --record-dir (the Gate C workspace) must already exist; refusing to create parent directories')
os.mkdir(a.record_dir)
sha = lambda b: hashlib.sha256(b).hexdigest()
def fsha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
    return h.hexdigest()
results = []
def item(name, ok, detail="", pending=False):
    results.append((name, "PENDING" if pending else ("PASS" if ok else "FAIL"), detail)); print(f"{results[-1][1]:7s} {name}  {detail}")
def sh(args, log, **k):
    r = subprocess.run(args, capture_output=True, text=True, **k)
    with open(os.path.join(a.record_dir, log), "w") as fh: fh.write(f"$ {' '.join(map(str, args))}\n{r.stdout}\n--- stderr\n{r.stderr}\n--- rc {r.returncode}\n")
    return r

now = datetime.datetime.fromisoformat(a.now_utc) if a.now_utc else datetime.datetime.now(datetime.timezone.utc)
# ---- package
arc = os.path.join(a.record_dir, os.path.basename(a.archive)); shutil.copyfile(a.archive, arc)
d = fsha(arc); item("1 archive SHA-256 equals the declared package digest", d == DECL["package_sha256"], d)
side = a.archive + ".sha256"; item("1a archive sidecar records the declared digest", os.path.isfile(side) and open(side).read().split()[0] == DECL["package_sha256"])
ex = os.path.join(a.record_dir, "extracted_package"); os.makedirs(ex)
with tarfile.open(arc) as tf:
    bad = [m.name for m in tf.getmembers() if m.name.startswith("/") or ".." in m.name.split("/") or m.issym() or m.islnk() or m.isdev()]
    item("1b safe extraction (no absolute paths, '..', links, devices)", not bad, str(bad[:3]))
    if not bad: tf.extractall(ex, filter="data")
roots = os.listdir(ex); pkg = os.path.join(ex, roots[0]) if len(roots) == 1 else None
r = sh(["sha256sum", "-c", "SHA256SUMS"], "sha256sum_package.txt", cwd=pkg) if pkg else None
item("1c sha256sum -c SHA256SUMS in the extracted package", bool(r) and r.returncode == 0, f"{r.stdout.count(': OK')} OK" if r else "no package directory")
item("1d runner and harness digests equal the declared ones", bool(pkg) and fsha(os.path.join(pkg, "tools/r4_job/run_job.sh")) == DECL["run_job_sha256"] and fsha(os.path.join(pkg, "tools/rf_metamorphic_harness.py")) == DECL["harness_sha256"])

# ---- interpreter and numerical environment (recorded; frozen for launch by RECHECK)
py = a.python; ok_py = os.access(py, os.X_OK) and os.path.isfile(py); info = {}
if ok_py:
    code = ("import sys,json,numpy,platform\ntry: cfg=numpy.show_config(mode='dicts')\nexcept Exception as e: cfg={'error':str(e)}\n"
            "print(json.dumps({'version':list(sys.version_info[:3]),'full':sys.version,'numpy':numpy.__version__,'numpy_file':numpy.__file__,'prefix':sys.prefix,'byteorder':sys.byteorder,"
            "'float64_itemsize':numpy.dtype('float64').itemsize,'machine':platform.machine(),'blas':cfg.get('Build Dependencies',{}).get('blas'),'simd':cfg.get('SIMD Extensions'),'compilers':cfg.get('Compilers',{}).get('c')},default=str))")
    r = sh([py, "-I", "-B", "-c", code], "interpreter.txt", env={**{k: v for k, v in os.environ.items() if k != "PYTHONPATH"}, "PYTHONDONTWRITEBYTECODE": "1"})
    try: info = json.loads(r.stdout.strip().splitlines()[-1]) if r.returncode == 0 else {}
    except (IndexError, ValueError): info = {}            # not a Python interpreter (e.g. an executable that exits 0 silently): reported by item 2a, never a crash
real = os.path.realpath(py) if ok_py else None
info.update({"given_path": py, "resolved_path": real, "binary_sha256": fsha(real) if real and os.path.isfile(real) else None})
item("2 PYTHON is an absolute executable file", ok_py, py)
item("2a the interpreter imports NumPy and reports its identity", bool(info.get("numpy")), f"{info.get('version')} numpy {info.get('numpy')}")
item("2b float64 is 8 bytes and the byte order is little-endian (not permitted to differ from the reference)", info.get("float64_itemsize") == 8 and info.get("byteorder") == "little", f"{info.get('float64_itemsize')} {info.get('byteorder')}")
cpu = next((l.split(":", 1)[1].strip() for l in open("/proc/cpuinfo") if l.startswith("model name")), "unknown") if os.path.exists("/proc/cpuinfo") else "unknown"
env = {"hostname": socket.gethostname(), "kernel": platform.release(), "os": platform.platform(), "machine": platform.machine(), "cpu_model": cpu, "logical_cpus": os.cpu_count(), "euid": os.geteuid(),
       "python_full": info.get("full"), "python_version": info.get("version"), "numpy": info.get("numpy"), "numpy_file": info.get("numpy_file"), "interpreter_binary_sha256": info.get("binary_sha256"),
       "blas": info.get("blas"), "simd": info.get("simd"), "compiler": info.get("compilers")}
boot_id = open("/proc/sys/kernel/random/boot_id").read().strip()

# ---- job directory and resources
item("3 job directory is an absolute path that does not exist", not os.path.exists(a.job_dir), a.job_dir)
par = os.path.dirname(a.job_dir.rstrip("/")); item("3b job directory parent exists and is writable", os.path.isdir(par) and os.access(par, os.W_OK), par)
item("3c job directory is not inside the package or the record directory", not (a.job_dir + "/").startswith((pkg + "/" if pkg else "//", a.record_dir + "/")))
free = lambda p: (lambda s: s.f_bavail * s.f_frsize / 1e9)(os.statvfs(p))
tmpdir = os.environ.get("TMPDIR") or tempfile.gettempdir(); fj, ft = (free(par) if os.path.isdir(par) else -1), free(tmpdir)
item(f"3d at least {DECL['min_free_gb']} GB free on the job filesystem (measured)", fj >= DECL["min_free_gb"], f"{fj:.1f} GB at {par}")
item(f"3e at least {DECL['min_free_gb']} GB free on the TMPDIR filesystem (measured)", ft >= DECL["min_free_gb"], f"{ft:.1f} GB at {tmpdir}")
mem = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo") if ":" in l}
item(f"3f at least {DECL['min_mem_gb']} GB available memory", mem["MemAvailable"] / 1e6 >= DECL["min_mem_gb"], f"{mem['MemAvailable'] / 1e6:.1f} GB")

# ---- competing job
pat = re.compile(r"run_job\.sh|rf_metamorphic_harness|generate_holdout|test_rf_metamorphic_r4|r4_job/(seal|verify|gate)")
comp = []
for pid in filter(str.isdigit, os.listdir("/proc")):
    try: cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="replace")
    except OSError: continue
    if int(pid) != os.getpid() and pat.search(cmd) and "gate_c_preflight" not in cmd: comp.append((pid, cmd[:100]))
item("4 no competing r4 job process is running", not comp, str(comp[:3]))

# ---- operator declaration (RECONFIRMED): a structured, digest-bound statement made by the operator (the trusted workflow is documented; the tool never creates one)
def utc(x): return datetime.datetime.fromisoformat(x.replace("Z", "+00:00"))
def check_declaration(path, mode, prior_identity):
    """Return (status, detail, identity). CHECK_ONLY: missing -> PENDING. RECHECK: missing, changed, expired, withdrawn or invalid -> FAIL."""
    if not path: return ("PENDING" if mode == "CHECK_ONLY" else "FAIL"), "no operator declaration given", None
    if not os.path.isfile(path): return "FAIL", "operator declaration file missing", None
    raw = open(path, "rb").read(); ident = {"sha256": sha(raw)}
    try:
        o = json.loads(raw); need = ("schema", "operator", "host", "package_sha256", "window_start_utc", "window_end_utc", "declared_at_utc", "scope")
        if o.get("schema") != "gate-c-operator-declaration/1" or any(not isinstance(o.get(k), str) or not o[k].strip() for k in need): return "FAIL", "operator declaration is malformed or incomplete", ident
        start, end, at = utc(o["window_start_utc"]), utc(o["window_end_utc"]), utc(o["declared_at_utc"])
    except (ValueError, TypeError, AttributeError) as exc: return "FAIL", f"operator declaration unreadable: {exc}", ident
    ident.update({k: o[k] for k in ("operator", "host", "package_sha256", "window_start_utc", "window_end_utc", "declared_at_utc")})
    why = []
    if o["host"] != env["hostname"]: why.append(f"declared host {o['host']!r} is not this host")
    if o["package_sha256"] != d: why.append("declaration names a different package")
    if o["scope"] != SCOPE: why.append("declaration scope text differs from the required scope")
    if end - start != datetime.timedelta(hours=DECL["window_hours"]): why.append(f"window is not exactly {DECL['window_hours']} h")
    if at > now: why.append("declared in the future")
    if os.path.exists(path + ".withdrawn"): why.append("declaration WITHDRAWN")
    if mode == "CHECK_ONLY" and not now < end: why.append("window already expired")
    if mode == "RECHECK" and not start <= now < end: why.append("launch-time is outside the declared window (expired or not yet open)")
    if mode == "RECHECK":
        if not prior_identity: why.append("the CHECK_ONLY record holds no declaration identity")
        elif prior_identity != ident: why.append("declaration identity differs from the one frozen in the CHECK_ONLY record")
    return ("FAIL" if why else "PASS"), ("; ".join(why) or f"operator {o['operator']!r}, host {o['host']}, window {o['window_start_utc']} to {o['window_end_utc']}, digest {ident['sha256'][:16]}..."), ident

prior = None
if a.mode == "RECHECK":
    pj = os.path.join(a.prior_record, "PREFLIGHT_RECORD.json"); prior = json.load(open(pj))
st, detail, decl_ident = check_declaration(a.operator_declaration, a.mode, (prior or {}).get("operator_declaration"))
results.append(("5 operator declaration" + (" (reconfirmed at launch time)" if a.mode == "RECHECK" else "") + f": host available for the whole {DECL['window_hours']} h window, {SCOPE}", st, detail)); print(f"{st:7s} {results[-1][0]}  {detail}")

# ---- environment table against the reference, and the RECHECK equality
ref_env = None
if a.reference_preflight:
    rr = json.load(open(a.reference_preflight)); ri = rr.get("interpreter", {})
    ref_env = {"hostname": rr.get("host", {}).get("hostname"), "kernel": rr.get("host", {}).get("kernel"), "logical_cpus": rr.get("nproc"), "python_version": ri.get("version"), "numpy": ri.get("numpy"),
               "interpreter_binary_sha256": ri.get("binary_sha256"), "numpy_file": ri.get("numpy_file")}
if a.mode == "RECHECK":
    sums = os.path.join(a.prior_record, "PREFLIGHT_SHA256SUMS"); bad_sum = []
    if not os.path.isfile(sums): bad_sum.append("PREFLIGHT_SHA256SUMS missing")
    else:
        for line in open(sums).read().split("\n"):
            if line.strip():
                dg, nm = line.split(None, 1); pth = os.path.join(a.prior_record, nm.strip())
                if not os.path.isfile(pth) or fsha(pth) != dg: bad_sum.append(nm.strip())
    item("6s the prior record is schema 2 and its checksum file verifies (an immutable reference; older or incomplete records are rejected)", prior.get("schema_version") == SCHEMA_VERSION and not bad_sum,
         f"schema_version={prior.get('schema_version')!r}" + (f"; checksum problems {bad_sum[:3]}" if bad_sum else ""))
    want_verdict = "INVALID_FOR_GATE_C_DECLARATION_OVERRIDE_PASS" if override else "PASS"
    item("6p the prior record's verdict is PASS (a pending or failed CHECK_ONLY cannot be rechecked into a pass)", prior.get("verdict") == want_verdict, str(prior.get("verdict")))
    diffs = [f"{k}: {prior.get('environment', {}).get(k)!r} -> {env.get(k)!r}" for k in env if prior.get("environment", {}).get(k) != env.get(k)]
    item("6 FROZEN: the launch-time environment equals the Gate C CHECK_ONLY record exactly (otherwise the replay is refused)", not diffs and prior.get("package_sha256") == d, "; ".join(diffs[:4]) or "identical")
    req_now = {k: DECL[k] for k in REQUIREMENT_KEYS}; req_prior = prior.get("requirements", {})
    item("6r FROZEN: requirements, thresholds, package and job path equal the record's (a changed measurement may vary; a changed requirement may not)", req_prior == req_now and prior.get("job_dir") == a.job_dir,
         "; ".join(f"{k}: {req_prior.get(k)!r} -> {req_now[k]!r}" for k in req_now if req_prior.get(k) != req_now[k]) or ("job path differs" if prior.get("job_dir") != a.job_dir else "identical"))
    item("6a the host has not rebooted since the CHECK_ONLY record (informational: a reboot is reported, not failed)", True, "same boot" if prior["host"]["boot_id"] == boot_id else "BOOT ID CHANGED since the CHECK_ONLY record")

attempt_id = f"R4REPLAY-{now.strftime('%Y%m%dT%H%M%SZ')}-{DECL['package_sha256'][:12]}-{env['hostname']}-{secrets.token_hex(4)}" + (f"-ATTEMPT{a.attempt_label}" if a.attempt_label else "")
fails = [n for n, s, _ in results if s == "FAIL"]; pend = [n for n, s, _ in results if s == "PENDING"]
verdict = "FAIL" if fails else ("PASS_OPERATOR_CONFIRMATION_PENDING" if pend else "PASS")
if override: verdict = "INVALID_FOR_GATE_C_DECLARATION_OVERRIDE_" + verdict
fclass = {"FROZEN": ["package_sha256", "requirements", "job_dir", "environment", "operator_declaration identity"],
          "REMEASURED": ["free space (job, temp)", "available memory", "load", "competing processes", "job directory absent"], "RECONFIRMED": ["operator declaration validity (window, withdrawal)"]}
measured = {"free_job_gb": round(fj, 3), "free_tmp_gb": round(ft, 3), "mem_available_gb": round(mem["MemAvailable"] / 1e6, 3)}
rec = {"attempt_label": a.attempt_label, "schema_version": SCHEMA_VERSION, "field_classes": fclass, "requirements": {k: DECL[k] for k in REQUIREMENT_KEYS}, "measurements": measured, "operator_declaration": decl_ident, "preflight_mode": a.mode, "launch": "NOT_PERFORMED", "attempt_id_candidate": attempt_id, "timestamp_utc": now.isoformat(timespec="seconds"), "package_sha256": d, "job_dir": a.job_dir, "record_dir": a.record_dir,
       "host": {"hostname": env["hostname"], "kernel": env["kernel"], "boot_id": boot_id}, "environment": env, "reference_environment": ref_env, "load_avg": os.getloadavg(), "uptime_s": float(open("/proc/uptime").read().split()[0]),
       "declaration": {k: v for k, v in DECL.items()}, "local_runtime_baseline_s": DECL["local_runtime_baseline_s"], "items": [{"name": n, "status": s, "detail": x} for n, s, x in results], "verdict": verdict}
json.dump(rec, open(os.path.join(a.record_dir, "PREFLIGHT_RECORD.json"), "w"), indent=1, default=str)
tab = ["ENVIRONMENT TABLE (reference = the retained local run; replay = this host). Differences are recorded, not judged (protocol section 5.3).", f"{'field':28s} {'reference':44s} replay"]
for k in ("hostname", "kernel", "logical_cpus", "python_version", "numpy", "interpreter_binary_sha256", "numpy_file"):
    rv = ref_env.get(k) if ref_env else "(no reference given)"; tab.append(f"{k:28s} {str(rv)[:44]:44s} {str(env.get(k))[:70]}" + ("" if rv == env.get(k) or not ref_env else "   <- differs"))
tab += [f"{'cpu_model':28s} {'(not in the reference record)':44s} {cpu}", f"{'simd (numpy)':28s} {'':44s} {json.dumps(env.get('simd'))[:120]}", f"{'blas (numpy)':28s} {'':44s} {json.dumps(env.get('blas'))[:120]}"]
txt = [f"GATE C PREFLIGHT RECORD (mode {a.mode}; no job started; job directory not created)", f"ATTEMPT_ID_CANDIDATE: {attempt_id}  (bound to package {d}, host {env['hostname']}/{boot_id}, interpreter {info.get('binary_sha256')}, job dir {a.job_dir})",
       f"TIMESTAMP_UTC: {rec['timestamp_utc']}", ""] + [f"{s:7s} {n}  {x}" for n, s, x in results] + [""] + tab + ["", f"PREFLIGHT_VERDICT: {verdict}", "LAUNCH: NOT_PERFORMED (Gate D needs its own explicit authorization)"]
open(os.path.join(a.record_dir, "PREFLIGHT_RECORD.txt"), "w").write("\n".join(txt) + "\n")
lines = []
for root, _, fs in os.walk(a.record_dir):
    for f in sorted(fs):
        p = os.path.join(root, f); rel = os.path.relpath(p, a.record_dir)
        if rel != "PREFLIGHT_SHA256SUMS" and not rel.startswith("extracted_package/"): lines.append(f"{fsha(p)}  {rel}")
open(os.path.join(a.record_dir, "PREFLIGHT_SHA256SUMS"), "w").write("\n".join(sorted(lines, key=lambda l: l.split(None, 1)[1])) + "\n")
print("\n".join(txt[-4:])); sys.exit(1 if fails else 0)
