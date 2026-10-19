import hashlib, json, os, re, subprocess, sys
F, full, PY = sys.argv[1:4]
sha = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
b = json.load(open("out1/build_summary.json")); v = json.load(open(os.path.join(F, "verification_summary.json")))
vo = open(os.path.join(F, "verification_output.txt")).read(); neg = open(os.path.join(F, "negative_controls_output.txt")).read()
checks = [l for l in vo.splitlines() if re.match(r"^(PASS|FAIL) \d", l)]
passed = sum(l.startswith("PASS") for l in checks)
nfx = len([l for l in open("extract_final/scythe_r4_pkg/fixtures/fixture_hashes.sha256") if l.strip()])
pyv = subprocess.run([PY, "-c", "import sys,numpy;print(sys.version.split()[0]+', numpy '+numpy.__version__)"], capture_output=True, text=True).stdout.strip()
rejected = sum("REJECTED" in l for l in neg.splitlines()); accepted_bad = sum("ACCEPTED" in l for l in neg.splitlines())
assert passed == len(checks) == 13 and rejected == 7 and accepted_bad == 0, (passed, len(checks), rejected, accepted_bad)
rec = f"""EXPERIMENT: SCYTHE_METAMORPHIC_RF_R4
STAGE: FROZEN_PACKAGE_PREPARATION
SOURCE_COMMIT: {full}
SOURCE_CLOSURE: VERIFIED
PACKAGE_SHA256: {b['archive_sha256']}
PACKAGE_CONTENTS: {v['files']} files
FIXTURE_HASH_MANIFEST: {b['fixture_hash_manifest_sha256']}  ({nfx} fixture hashes)
CLEAN_ARCHIVE_COMPARISON: {'PASS' if 'PASS 6 CLEAN ARCHIVE COMPARISON' in vo else 'FAIL'}
INDEPENDENT_EXTRACTION_VERIFICATION: {'PASS' if v['verdict'] == 'PASS' else 'FAIL'}
EXECUTION_AUTHORIZED: NO
DEDIROCK_TRANSFER_AUTHORIZED: NO

ARCHIVE: {b['archive']}   (deterministic: two independent builds are byte-identical)
SOURCE: the complete commit above (merged to main as PR #165). The package was built only from "git archive" / "git show" of that commit and never from a working tree.
CONTENTS: the r4 harness; the production modules its execution imports (source closure from tools/r4_job/PACKAGE_FILES.txt); the bounded controls; the job runner
  (run_job.sh, seal_job.py, gate_results.py, CUSTODY.md); the fixture generator and frozen specification (members of the equivalence-package tarball blob at the same commit);
  the {nfx}-line fixture hash manifest; the r4 design and implementation notes; SHA256SUMS (inventory with content digests); PACKAGE_INVENTORY.json (per-file provenance);
  PACKAGE_IDENTITY.txt. Raw r3 results are NOT included (no declared dependency on them).

VERIFICATION ({passed} of {len(checks)} checks passed; verification_output.txt):
  archive digest; safe extraction into a fresh directory; extracted file set == inventory; sha256sum -c; inventory digests; clean-archive comparison of every file
  against BOTH "git show" and an independent "git archive" extraction (23 files compared; the 3 generated files are excluded); source closure resolved in an isolated
  interpreter (-I -B) with only the package on the path (11 repository-local modules, all inside the package; third-party: numpy, plus the venv's _distutils_hack shim,
  which is not a dependency); every .py compiles and every .sh passes bash -n; no caches, links, oversize files, absolute local paths, credential patterns or operator identities.
VERIFIER NEGATIVE CONTROLS (negative_controls_output.txt): {rejected} deliberately damaged packages (byte-tampered with and without refreshed manifests; closure module removed;
  undeclared file; absolute path; bytecode cache; credential pattern) are all REJECTED through named checks; 0 accepted. A first run rejected two of them only by crashing (a lookup of a
  non-existent path); the verifier was corrected to report them as explicit failures and the controls were re-run.

DEPENDENCIES (what the package needs from its host; found by this verification, not hidden):
  numpy (imports verified with Python {pyv}; the earlier r3 runs used numpy 2.5.1 and 2.5.3). Only these interpreters have been used; other versions are untested.
  bash and GNU coreutils (sha256sum, nproc, df, uname, sync, mkdir, cp) used by run_job.sh.
  OBSERVATION: run_job.sh defaults its interpreter to $HOME/SCYTHE/.venv/bin/python when PYTHON is unset, a host-specific path. Gate 3 must set PYTHON explicitly; the package carries no interpreter.
  No missing or unexpected repository dependency was found.

BUILD TOOLS (outside the package; digests recorded):
  build_package.py {sha('build_package.py')}
  verify_package.py {sha('verify_package.py')}
  neg_controls.py {sha('neg_controls.py')}
  make_record.py {sha('make_record.py')}

LIMITS: this establishes artifact integrity and the completeness of the source closure. It does not establish that the r4 experiment passes, that the boundary relation holds across the
  36 fixtures, or that two environments agree. The packaged unit controls and the one-shot runner were NOT executed here (Gate 3); only imports and compilation were exercised.
"""
open(os.path.join(F, "PACKAGE_RECORD.txt"), "w").write(rec); print(rec)
