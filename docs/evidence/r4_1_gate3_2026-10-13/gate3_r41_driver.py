#!/usr/bin/env python3
"""Replacement Gate 3 driver for the frozen r4.1 package. Lives OUTSIDE the package. Never runs run_job.sh as a whole.
It does reuse the package's REAL finalization (the finalize() function text is extracted from the packaged run_job.sh and run in bash).

    gate3_r41_driver.py ok   ARCHIVE JOB_DIR PKG_DIR     one-fixture boundary-only validation
    gate3_r41_driver.py fail ARCHIVE JOB_DIR PKG_DIR     failed preflight (byte altered in a DISPOSABLE extraction)
Every stage must succeed before the next starts. The disposition is written beside the job (JOB_DIR.disposition.txt), never inside it.
"""
import hashlib, os, subprocess, sys, tarfile, datetime

mode, archive, job, pkgdir = sys.argv[1:5]
PY = "$HOME/SCYTHE/.venv/bin/python"
WANT = "2b2e3251b678a5e83735ee663176f7763482a6948a7d5cb57f244dac1142874a"
env = dict(os.environ, PYTHON=PY, PYTHONDONTWRITEBYTECODE="1", OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1", NUMEXPR_NUM_THREADS="1")
env.pop("PYTHONPATH", None)
for d in (job, pkgdir):
    if os.path.exists(d): sys.exit(f"exists, refusing: {d}")
now = lambda: datetime.datetime.now().astimezone().isoformat(timespec="seconds")
def digest(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
    return h.hexdigest()
if digest(archive) != WANT: sys.exit("archive digest mismatch; refusing to extract")
os.makedirs(pkgdir)
with tarfile.open(archive) as tf: tf.extractall(pkgdir, filter="data")
pkg = os.path.join(pkgdir, "scythe_r4_1_pkg")
os.makedirs(os.path.join(job, "logs"))
status = open(os.path.join(job, "STATUS.txt"), "a"); codes = open(os.path.join(job, "exit_codes.txt"), "a")
def flush(f): f.flush(); os.fsync(f.fileno())
def st(msg): status.write(f"{now()} {msg}\n"); flush(status)
def run(args, log):
    with open(os.path.join(job, "logs", log), "w") as fh:
        return subprocess.run(args, cwd=pkg, env=env, stdout=fh, stderr=subprocess.STDOUT).returncode
def finalize(computation, code, label):
    """terminal record -> exit codes -> flush -> the package's real finalize() (seal --require-fixtures, verify_job.py) -> nothing written afterwards."""
    codes.write(f"{label} exit={code}\n"); flush(codes)
    st(("DONE COMPUTATION_COMPLETED" if computation == "YES" else "FAILED") + f" gate3-r4.1 mode={mode} {label}"); status.close(); codes.close()
    r = subprocess.run(["bash", "-c", f'set -u; PY="{PY}"; JOB="{job}"; cd "{pkg}"; source <(sed -n "/^finalize()/,/^}}/p" tools/r4_job/run_job.sh); finalize {computation}'],
                       env=env, capture_output=True, text=True)
    print(r.stdout); print(r.stderr); print("FINALIZE rc", r.returncode, "COMPUTATION", computation); sys.exit(0 if (computation == "YES" and r.returncode == 0) else (code or 20))
def stage(code, label, rc):
    if rc: st(f"FAILED stage: {label}"); finalize("NO", code, label)

st(f"START gate3-r4.1 mode={mode} archive_sha256={WANT} job={job}"); st("OK archive digest verified before extraction; extracted by the driver into a disposable directory")
with open(os.path.join(job, "env.txt"), "w") as fh:
    subprocess.run(["bash", "-c", f'echo "uname: $(uname -a)"; echo "python: $({PY} -c "import sys,numpy;print(sys.version.replace(chr(10),chr(32)),numpy.__version__)")"; echo "threads OPENBLAS=1 OMP=1 MKL=1 NUMEXPR=1"; df -h {job}|tail -1'], stdout=fh, stderr=subprocess.STDOUT)
if mode == "fail":
    t = os.path.join(pkg, "tools", "r4_job", "CUSTODY.md"); b = bytearray(open(t, "rb").read()); b[10] ^= 1; open(t, "wb").write(b)
    st("INJECTED: one byte of tools/r4_job/CUSTODY.md altered in the disposable extraction")
stage(10, "package hash verification", run(["sha256sum", "-c", "SHA256SUMS"], "package_verify.txt")); st("OK package verified (sha256sum -c SHA256SUMS)")
if mode == "fail": finalize("NO", 99, "UNEXPECTED: injected failure was not detected")
stage(11, "bounded controls", run([PY, "-m", "unittest", "test_rf_metamorphic_controls", "test_rf_metamorphic_r4_controls", "test_rf_metamorphic_r4_production_path", "test_rf_metamorphic_r4_job"], "controls.txt"))
ran = [l for l in open(os.path.join(job, "logs/controls.txt")) if l.startswith("Ran ")][-1].strip(); st("OK bounded controls: " + ran)
stage(12, "fixture generation", run([PY, "fixtures/generate_holdout.py", "--spec", "fixtures/synth_521_holdout_frozen.json", "--outdir", os.path.join(job, "holdout")], "fixtures_generate.txt"))
stage(13, "fixture hash verification", run(["bash", "-c", f'cd "{job}/holdout" && sha256sum -c "{pkg}/fixtures/fixture_hashes.sha256"'], "fixtures_verify.txt"))
stage(16, "generated artifact (index.json) verification", run(["bash", "-c", f'cd "{job}/holdout" && sha256sum -c "{pkg}/tools/r4_job/generated_artifacts.sha256"'], "generated_verify.txt"))
hd = os.path.join(job, "holdout"); names = sorted(os.listdir(hd))
reg = [n for n in names if os.path.isfile(os.path.join(hd, n)) and not os.path.islink(os.path.join(hd, n))]
stage(17, "holdout is not exactly 37 regular files", 0 if len(names) == 37 == len(reg) else 1)
st(f"OK fixtures regenerated, {open(os.path.join(job, 'logs/fixtures_verify.txt')).read().count(': OK')}/36 hashes verified; index.json pin verified; holdout holds exactly {len(reg)} regular files")
subprocess.run(["cp", os.path.join(pkg, "fixtures/fixture_hashes.sha256"), os.path.join(job, "fixture_hashes.sha256")], check=True)
subprocess.run(["cp", os.path.join(pkg, "tools/r4_job/generated_artifacts.sha256"), os.path.join(job, "generated_artifacts.sha256")], check=True)
st("BEGIN one-fixture boundary-only run (--limit 1 --boundary-only)")
rc = run([PY, "tools/rf_metamorphic_harness.py", "--holdout", hd, "--hashes", os.path.join(pkg, "fixtures/fixture_hashes.sha256"), "--results-dir", os.path.join(job, "results_boundary"), "--limit", "1", "--boundary-only"], "harness.log")
codes.write(f"harness exit={rc}\n"); flush(codes)
grc = run([PY, "tools/r4_job/gate_results.py", os.path.join(job, "results_boundary"), "1", "0", "--boundary-only"], "gate.txt")
codes.write(f"gate exit={grc}\n"); flush(codes)
ok = rc == 0 and grc == 0
finalize("YES" if ok else "NO", 0 if ok else 21, f"harness_rc={rc} gate_rc={grc}")
