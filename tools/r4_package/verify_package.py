#!/usr/bin/env python3
"""Independent verification of a frozen r4 package (shares no code with build_package.py).

    verify_package.py REPO FULL_SHA ARCHIVE EXTRACT_DIR [--python PATH]

Checks (each prints PASS/FAIL; exit 0 only if all PASS):
  1 archive digest equals its sidecar            2 safe extraction into a fresh directory (no absolute/.. paths, no links)
  3 extracted file set == inventory + the two generated manifests (nothing extra, nothing missing)
  4 `sha256sum -c SHA256SUMS`                    5 inventory digests == file digests
  6 CLEAN ARCHIVE COMPARISON: every file byte-equal to `git show FULL:path` AND to an independent `git archive FULL` extraction
    (fixture members: to the member of the equivalence-package tarball blob at FULL)
  7 SOURCE CLOSURE: in an isolated interpreter (-I -B) with only the package on the path, the harness and every test module import, and
    every repository-local module resolved lies inside the package; third-party imports are listed (numpy expected)
  8 every .py compiles, every .sh passes `bash -n`
  9 CONTAMINATION: no caches/bytecode/VCS dirs, no absolute local paths, no credential patterns, no symlinks, no oversized files
"""
import hashlib, io, json, os, re, shutil, subprocess, sys, tarfile, tempfile, traceback


def _crash(kind, value, tb):
    """An unhandled exception is a verifier FAILURE (exit 2, marker printed), never a rejection of the package under test."""
    traceback.print_exception(kind, value, tb)
    print("VERIFIER_CRASH:", kind.__name__, value); sys.stdout.flush(); os._exit(2)


sys.excepthook = _crash

repo, full, archive, dest = sys.argv[1:5]
py = sys.argv[sys.argv.index("--python") + 1] if "--python" in sys.argv else sys.executable
results, notes = [], []
sha = lambda b: hashlib.sha256(b).hexdigest()
def check(name, ok, detail=""):
    results.append(ok); print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
def git(*a, binary=True):
    return subprocess.run(["git", "-C", repo, *a], capture_output=True, check=True).stdout

# 1 archive digest
want = open(archive + ".sha256").read().split()[0]
with open(archive, "rb") as fh: got = sha(fh.read())
check("1 archive SHA-256 equals its recorded digest", got == want, got)

# 2 safe extraction into a FRESH directory
if os.path.exists(dest): shutil.rmtree(dest)
os.makedirs(dest)
with tarfile.open(archive) as tf:
    bad = [m.name for m in tf.getmembers() if m.name.startswith("/") or ".." in m.name.split("/") or m.issym() or m.islnk() or m.isdev()]
    check("2 extraction safe (no absolute paths, '..', links or devices)", not bad, str(bad[:3]))
    tf.extractall(dest, filter="data")
root = os.path.join(dest, "scythe_r4_1_pkg")
disk = sorted(os.path.relpath(os.path.join(r, f), root) for r, _, fs in os.walk(root) for f in fs)

# 3 file set
inv = json.load(open(os.path.join(root, "PACKAGE_INVENTORY.json")))
expected = set(inv["files"]) | {"PACKAGE_INVENTORY.json", "SHA256SUMS"}
check("3 extracted file set == inventory (+ the two manifests)", set(disk) == expected,
      f"{len(disk)} files; extra={sorted(set(disk) - expected)[:3]} missing={sorted(expected - set(disk))[:3]}")
check("3b inventory names the complete commit", inv["source_commit"] == full, inv["source_commit"])

# 4 sha256sum -c
r = subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=root, capture_output=True, text=True)
check("4 sha256sum -c SHA256SUMS", r.returncode == 0 and r.stdout.count(": OK") == len(expected) - 1, f"{r.stdout.count(': OK')} OK")
listed = {l.split(None, 1)[1].strip() for l in open(os.path.join(root, "SHA256SUMS")) if l.strip()}
check("4b SHA256SUMS lists every file except itself", listed == expected - {"SHA256SUMS"})

# 5 inventory digests
bad = [p for p, m in inv["files"].items() if sha(open(os.path.join(root, p), "rb").read()) != m["sha256"]]
check("5 inventory content digests match the files", not bad, str(bad[:3]))

# 6 clean archive comparison, two independent routes
EV = "docs/evidence/dedirock-equivalence-2026-10-12"
clean = tempfile.mkdtemp(prefix="clean-archive-")
def in_commit(path):
    return subprocess.run(["git", "-C", repo, "cat-file", "-e", f"{full}:{path}"], capture_output=True).returncode == 0
direct = {}                                   # packaged path -> repository path (entries that must exist at the commit)
mism, compared = [], 0
for p, m in sorted(inv["files"].items()):
    if m["origin"] == "generated":
        if p != "PACKAGE_IDENTITY.txt": mism.append(f"{p}: claims to be generated")
    elif ".tar.gz:" in m["origin"]:
        pass
    elif m["origin"].startswith("git:") and in_commit(m["origin"][4:].split("@")[0]):
        direct[p] = m["origin"][4:].split("@")[0]
    else:
        mism.append(f"{p}: origin {m['origin']!r} does not resolve to a path at the commit")
if direct:
    ga = subprocess.run(["git", "-C", repo, "archive", "--format=tar", full, "--", *sorted(set(direct.values()))], capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(ga)) as tf: tf.extractall(clean, filter="data")
nested = tarfile.open(fileobj=io.BytesIO(git("show", f"{full}:{EV}/dedirock_equiv_pkg.tar.gz")), mode="r:gz")
for p, m in sorted(inv["files"].items()):
    if m["origin"] == "generated" or p in [x.split(":")[0] for x in mism]:
        continue
    data = open(os.path.join(root, p), "rb").read()
    if ".tar.gz:" in m["origin"]:
        member = m["origin"].split(".tar.gz:")[1].split("@")[0]
        try: ref1 = ref2 = nested.extractfile(member).read()
        except (KeyError, AttributeError): mism.append(f"{p}: member {member} not in the equivalence package"); continue
    elif p in direct:
        ref1 = git("show", f"{full}:{direct[p]}"); ref2 = open(os.path.join(clean, direct[p]), "rb").read()
    else:
        continue
    compared += 1
    if not (data == ref1 == ref2): mism.append(f"{p}: differs from the commit")
check("6 CLEAN ARCHIVE COMPARISON: byte-identical to git show AND an independent git archive", not mism, f"{compared} files compared; mismatches={mism[:3]}")
shutil.rmtree(clean)

# 7 source closure in an isolated interpreter
probe = r'''
import sys, os, importlib, sysconfig, json
pkg = sys.argv[1]; sys.path.insert(0, pkg); sys.path.insert(0, os.path.join(pkg, "tools"))
for m in ("test_rf_metamorphic_controls", "test_rf_metamorphic_r4_controls", "test_rf_metamorphic_r4_job", "test_rf_metamorphic_r4_production_path"):
    importlib.import_module(m)
import importlib.util
spec = importlib.util.spec_from_file_location("h", os.path.join(pkg, "tools", "rf_metamorphic_harness.py")); h = importlib.util.module_from_spec(spec); spec.loader.exec_module(h)
std = {sysconfig.get_paths()["stdlib"], sysconfig.get_paths()["platstdlib"]}
site = tuple(p for p in sys.path if "site-packages" in p or "dist-packages" in p)
local, third, other = [], set(), []
for name, mod in list(sys.modules.items()):
    f = getattr(mod, "__file__", None)
    if not f: continue
    f = os.path.abspath(f)
    if f.startswith(os.path.abspath(pkg) + os.sep): local.append(os.path.relpath(f, pkg))
    elif any(f.startswith(os.path.abspath(s) + os.sep) for s in site): third.add(name.split(".")[0])
    elif any(f.startswith(os.path.abspath(s) + os.sep) for s in std): pass
    else: other.append(f)
print(json.dumps({"local": sorted(set(local)), "third_party": sorted(third), "other": sorted(set(other))}))
'''
env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}; env["PYTHONDONTWRITEBYTECODE"] = "1"
r = subprocess.run([py, "-I", "-B", "-c", probe, root], capture_output=True, text=True, env=env)
ok7 = r.returncode == 0
info = json.loads(r.stdout.strip().splitlines()[-1]) if ok7 else {}
pyfiles = {p for p in disk if p.endswith(".py")}
check("7 source closure: every import resolves inside the package (isolated interpreter)", ok7 and not info.get("other"), (r.stderr.strip()[-200:] if not ok7 else f"{len(info['local'])} local modules; third-party={info['third_party']}; outside={info['other']}"))
if ok7:
    closure_listed = {l.strip() for l in open(os.path.join(root, "tools/r4_job/PACKAGE_FILES.txt")) if l.strip() and not l.startswith("#")}
    check("7b every locally imported module is in the packaged closure list or is a tool/test the package carries", set(info["local"]) <= pyfiles)
    check("7c the closure list is fully present in the package", closure_listed <= set(disk), str(sorted(closure_listed - set(disk))[:3]))
    notes.append(f"third-party imports: {info['third_party']}")

# 10 r4.1 custody completeness: the files the replacement exists to carry are present and mutually consistent (derived independently of the builder)
need = ["tools/r4_job/verify_job.py", "tools/r4_job/generated_artifacts.sha256", "tools/r4_job/seal_job.py", "tools/r4_job/run_job.sh", "fixtures/fixture_hashes.sha256", "fixtures/synth521_holdout_index.json", "fixtures/generate_holdout.py"]
absent = [p for p in need if p not in disk]
check("10 r4.1 custody files present", not absent, str(absent))
if not absent:
    rd = lambda p: open(os.path.join(root, p), "rb").read()
    pin_ok = rd("tools/r4_job/generated_artifacts.sha256") == f"{sha(rd('fixtures/synth521_holdout_index.json'))}  index.json\n".encode()
    check("10b generated-artifact pin == digest of the frozen index, naming index.json only", pin_ok)
    fl = rd("fixtures/fixture_hashes.sha256").decode().splitlines()
    ident = dict(l.split(": ", 1) for l in rd("PACKAGE_IDENTITY.txt").decode().splitlines() if ": " in l)
    check("10c 36 fixture hashes, none naming index.json, digest equals the identity record", len(fl) == 36 and not any("index.json" in l for l in fl) and sha(rd("fixtures/fixture_hashes.sha256")) == ident.get("FIXTURE_HASH_MANIFEST_SHA256"))
    check("10d identity records the pin digest and revision r4.1", ident.get("GENERATED_ARTIFACT_PIN_SHA256") == sha(rd("tools/r4_job/generated_artifacts.sha256")) and ident.get("REVISION") == "r4.1")
    rj = rd("tools/r4_job/run_job.sh").decode()
    check("10e runner finalizes through the separate verifier", "finalize" in rj and "verify_job.py" in rj)

# 8 compile and shell syntax
badc = []
for p in sorted(pyfiles):
    rc = subprocess.run([py, "-B", "-c", "import sys;compile(open(sys.argv[1]).read(), sys.argv[1], 'exec')", os.path.join(root, p)], capture_output=True)
    if rc.returncode: badc.append(p)
for p in sorted(x for x in disk if x.endswith(".sh")):
    if subprocess.run(["bash", "-n", os.path.join(root, p)], capture_output=True).returncode: badc.append(p)
check("8 every .py compiles and every .sh passes bash -n", not badc, str(badc[:3]))

# 9 contamination
dirty = []
for p in disk:
    full_p = os.path.join(root, p)
    if os.path.islink(full_p): dirty.append(f"symlink {p}")
    if re.search(r"(^|/)(__pycache__|\.git|\.venv|\.pytest_cache)(/|$)|\.pyc$|\.DS_Store$|\.swp$", p): dirty.append(f"cache/vcs {p}")
    if os.path.getsize(full_p) > 5_000_000: dirty.append(f"oversized {p}")
    try: text = open(full_p, "rb").read().decode("utf-8")
    except UnicodeDecodeError: continue
    for pat, label in ((r"/home/[A-Za-z0-9_.-]+|/Users/[A-Za-z0-9_.-]+|claude-1000|/tmp/claude", "absolute local path"),
                       (r"-----BEGIN [A-Z ]*PRIVATE KEY", "private key block"), (r"ssh-(ed25519|rsa) AAAA", "ssh public/private key material"),
                       (r"AKIA[0-9A-Z]{16}", "aws key id"), (r"(?i)(api[_-]?key|passwd|password)\s*[:=]\s*['\"][^'\"]{6,}['\"]", "credential assignment"),
                       (r"bgilbert1984@|spectrcyde\.com", "operator/host identity")):
        for m in re.finditer(pat, text):
            line = text[:m.start()].count("\n") + 1; dirty.append(f"{label} in {p}:{line}: {m.group(0)[:50]!r}")
check("9 no caches, links, oversize files, absolute local paths, credential patterns or operator identities", not dirty, f"{len(dirty)} hit(s)")
for d in dirty[:25]: print("     -", d)

print("\nNOTES:", notes)
print("VERIFICATION", "PASS" if all(results) else "FAIL", f"({sum(results)}/{len(results)} checks)")
json.dump({"verdict": "PASS" if all(results) else "FAIL", "checks": len(results), "passed": sum(results), "notes": notes, "contamination_hits": dirty,
           "files": len(disk), "extract_dir": dest}, open(os.path.join(os.path.dirname(archive), "verification_summary.json"), "w"), indent=1)
sys.exit(0 if all(results) else 1)
