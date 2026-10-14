import hashlib, json, os, re, subprocess, sys
F, full, PY, WORK = sys.argv[1:5]   # F: output dir; WORK: the build/verification working directory (build_summary.json, extracted package)
sha = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
b = json.load(open(os.path.join(WORK, "build_summary.json"))); v = json.load(open(os.path.join(F, "verification_summary.json")))
vo = open(os.path.join(F, "verification_output.txt")).read(); neg = open(os.path.join(F, "negative_controls_output.txt")).read()
checks = [l for l in vo.splitlines() if re.match(r"^(PASS|FAIL) \d", l)]
passed = sum(l.startswith("PASS") for l in checks)
nfx = len([l for l in open(os.path.join(WORK, "extract_final/scythe_r4_1_pkg/fixtures/fixture_hashes.sha256")) if l.strip()])
pyv = subprocess.run([PY, "-c", "import sys,numpy;print(sys.version.split()[0]+', numpy '+numpy.__version__)"], capture_output=True, text=True).stdout.strip()
rejected = sum("REJECTED" in l for l in neg.splitlines()); accepted_bad = sum("ACCEPTED" in l for l in neg.splitlines())
assert passed == len(checks) >= 18 and rejected >= 11 and accepted_bad == 0 and 'CONTROL_FAILURE' not in neg, (passed, len(checks), rejected, accepted_bad)
_here = os.path.dirname(os.path.abspath(__file__))
_rev = subprocess.run(["git", "-C", _here, "rev-parse", "HEAD"], capture_output=True, text=True)
tools_rev = _rev.stdout.strip() if _rev.returncode == 0 else "UNKNOWN (not run from a git checkout)"
inv_sha = sha(os.path.join(WORK, "extract_final/scythe_r4_1_pkg/PACKAGE_INVENTORY.json"))
rec = f"""EXPERIMENT: SCYTHE_METAMORPHIC_RF_R4
REVISION: r4.1 (replaces package a7fd10b1c43dde66d60804c3f23209b4b37e7129abc99cd6dc4f3c6cba9efb7c, which stays a historical failed artifact)
STAGE: FROZEN_PACKAGE_PREPARATION
SOURCE_COMMIT: {full}
SOURCE_CLOSURE: VERIFIED
PACKAGE_SHA256: {b['archive_sha256']}
PACKAGE_CONTENTS: {v['files']} files
FIXTURE_HASH_MANIFEST: {b['fixture_hash_manifest_sha256']}  ({nfx} fixture hashes)
GENERATED_ARTIFACT_PIN: {b['generated_pin_sha256']}  (pins holdout/index.json = f52adb1974dce642bc62bf748add6733036db1cbc7bcba482691a221ad88eca3; the directory must hold exactly 37 files)
CLEAN_ARCHIVE_COMPARISON: {'PASS' if 'PASS 6 CLEAN ARCHIVE COMPARISON' in vo else 'FAIL'}
INDEPENDENT_EXTRACTION_VERIFICATION: {'PASS' if v['verdict'] == 'PASS' else 'FAIL'}
EXECUTION_AUTHORIZED: NO
DEDIROCK_TRANSFER_AUTHORIZED: NO

ARCHIVE: {b['archive']}   (deterministic: two independent builds are byte-identical)
SOURCE: the complete commit above (merged to main as PR #166, disposition R4_1_FIXTURE_CUSTODY_CORRECTION_ACCEPTED). The package was built only from "git archive" / "git show" of that commit and never from a working tree.
CONTENTS: the r4 harness; the production modules its execution imports (source closure from tools/r4_job/PACKAGE_FILES.txt); the bounded controls; the job runner
  (run_job.sh, seal_job.py, the separately implemented verify_job.py, gate_results.py, generated_artifacts.sha256, CUSTODY.md); the fixture generator and frozen specification (members of the equivalence-package tarball blob at the same commit);
  the {nfx}-line fixture hash manifest; the r4 design, implementation and r4.1 custody notes; SHA256SUMS (inventory with content digests); PACKAGE_INVENTORY.json (per-file provenance);
  PACKAGE_IDENTITY.txt. Raw r3 results are NOT included (no declared dependency on them).

VERIFICATION ({passed} of {len(checks)} checks passed; verification_output.txt):
  archive digest; safe extraction into a fresh directory; extracted file set == inventory; sha256sum -c; inventory digests; clean-archive comparison of every file
  against BOTH "git show" and an independent "git archive" extraction (26 files compared; the 3 generated files are excluded); r4.1 custody completeness (verify_job.py and the pin present; the pin equals the digest of the frozen index and names only index.json; 36 fixture hashes none naming the index; identity records the pin and revision); source closure resolved in an isolated
  interpreter (-I -B) with only the package on the path (11 repository-local modules, all inside the package; third-party: numpy, plus the venv's _distutils_hack shim,
  which is not a dependency); every .py compiles and every .sh passes bash -n; no caches, links, oversize files, absolute local paths, credential patterns or operator identities.
VERIFIER NEGATIVE CONTROLS (negative_controls_output.txt): {rejected} deliberately damaged packages (byte-tampered with and without refreshed manifests; closure module removed;
  undeclared file; absolute path; bytecode cache; credential pattern; plus the four r4.1 cases) are all REJECTED through named checks; 0 accepted. Four cases are specific to r4.1 (verifier removed; wrong index pin; sealer tampered; index pin removed). Every case is rejected by a named check, not by a crash.

DEPENDENCIES (what the package needs from its host; found by this verification, not hidden):
  numpy (imports verified with Python {pyv}; the earlier r3 runs used numpy 2.5.1 and 2.5.3). Only these interpreters have been used; other versions are untested.
  bash and GNU coreutils (sha256sum, nproc, df, uname, sync, mkdir, cp) used by run_job.sh.
  OBSERVATION (unchanged from the first package): run_job.sh defaults its interpreter to $HOME/SCYTHE/.venv/bin/python when PYTHON is unset, a host-specific path. Gate 3 must set PYTHON explicitly; the package carries no interpreter.
  No missing or unexpected repository dependency was found.

PROVENANCE CHAIN (future chain: packages built with the reviewed repository tools):
  SOURCE_COMMIT {full} -> BUILDER (tools/r4_package/build_package.py, sha256 below) -> INVENTORY sha256 {inv_sha} -> PACKAGE_SHA256 {b['archive_sha256']}
  -> INDEPENDENT VERIFIER (tools/r4_package/verify_package.py, sha256 below) -> {passed}/{len(checks)} checks -> {'PASS' if v['verdict'] == 'PASS' else 'FAIL'}
  TOOLS_REVISION (git HEAD of the checkout holding the tools): {tools_rev}

BUILD TOOLS (digests recorded):
  build_package.py {sha(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'build_package.py'))}
  verify_package.py {sha(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'verify_package.py'))}
  neg_controls.py {sha(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'neg_controls.py'))}
  make_record.py {sha(os.path.abspath(__file__))}

LIMITS: this establishes artifact integrity and the completeness of the source closure. It does not establish that the r4 experiment passes, that the boundary relation holds across the
  36 fixtures, or that two environments agree. The packaged unit controls (which include the real-generator integration test) and the runner were NOT executed here; only imports and compilation were exercised.
  The replacement Gate 3, the full run, cross-host replay and any Dedirock transfer each need their own authorization. The external build tools are not yet in a reviewed source record.
"""
open(os.path.join(F, "PACKAGE_RECORD.txt"), "w").write(rec); print(rec)
