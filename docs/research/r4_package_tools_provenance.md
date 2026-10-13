# r4 packaging tools — reproducible source record and provenance

> **No r4 experiment is run or authorized by this change. Nothing is executed on, or transferred to, another host. The frozen package `2b2e3251…`, the failed Gate 3 attempt and all earlier evidence are unchanged.**

## 1. Two provenance chains (kept distinct)

**Historical chain.** The packages `a7fd10b1…` (r4, failed Gate 3) and `2b2e3251…` (r4.1) were built and verified with **external tools** that lived outside the repository. Those tools remain the historical authority for those packages and are
preserved verbatim here, with their original digests:

| Tool | r4 (package `a7fd10b1…`) `external_tools_r4_for_a7fd10b1/` | r4.1 (package `2b2e3251…`) `external_tools/` |
|---|---|---|
| `build_package.py` | `3aadea49…ff64c` (full: `3aadea492f8aabcb6089c75aa847f0190ae6571dba9e640e4fd9f5cc812ff64c`) | `b78aee2ed803b357b8e091664b45584c7568e67053d9f1656d2f169f74af0ef8` |
| `verify_package.py` | `2054065aea04f2169bd9dfc264146903d2cfa09482ce70491e081b3c0b171e73` | `dae1bfd5c517a28e9fd23705ba5575884e35ce1ba5234d4efe76ff2e88244371` |
| `neg_controls.py` | `61a3900fe91afc49ccec864f6c3307fc7c3355e8b01509a53d78e0e126712943` | `3c09c6dcff396f9d1b7f48447d20850d5cb032fbf88216c8a838a4527f206c41` |
| `make_record.py` | `fefb020ea9d997defe87ff7f36e58398230fa939db487c36eb798780633a9f3a` | `87ac5a36e080e1f83b035d4fa29cda007c3db08deb06ac0a54eca47af2a36299` |

(All under `docs/evidence/r4_1_packaging_provenance_2026-10-13/`.) The package records there are the originals. Package provenance must keep pointing at these files, **not** at the tools below.

**Future chain.** `tools/r4_package/` is the authority only for packages built after this change is reviewed and accepted. A package record produced by the repository tools states the chain explicitly:
source commit → builder → inventory digest → package digest → independent verifier → check count → verdict, plus the git revision of the checkout that held the tools.

## 2. Functional changes from the external r4.1 versions

| File | Repository digest | Change |
|---|---|---|
| `build_package.py` | `b78aee2e…` (**identical** to the external r4.1 builder) | none |
| `verify_package.py` | `4fd221f5335efd19226a204cfec82f9e3d6b7935b5495314bd6bfe23da0fdb75` | an unhandled exception now prints `VERIFIER_CRASH` and exits **2**; every integrity decision is unchanged |
| `neg_controls.py` | `318bd63393797e59a9e3b5aa9fb1315db6573c0705b454d6ac03ad0f20e0ae1e` | parameterised (`PYTHON FULL_SHA REPO BASE_ARCHIVE WORKDIR`, no hard-coded relative paths); a case is REJECTED only if the verifier exits 1, prints `VERIFICATION FAIL` and names at least one failed check; a crash or any other outcome is `CONTROL_FAILURE` / `ACCEPTED (BAD)` and the script exits nonzero |
| `make_record.py` | `f7004f590daf16c50c63128af243a8720ac15c62e2fd7f53dcac67fe3404481f` | parameterised (`OUTDIR FULL_SHA PYTHON WORKDIR`); count assertions relaxed from exactly 18/11 to at least 18/11 with no accepted-bad and no control failure; adds the PROVENANCE CHAIN section; tool digests read from the tools' own location |

The builder reads only `git archive`/`git show` of one commit. It is specific to the r4.1 layout (`scythe_r4_1_pkg`, the closure list, the equivalence-package members); a later revision needs its own reviewed builder change.

## 3. Reproducing the existing package

`tools/r4_package/build_package.py` rebuilt from source commit `8d34de9ac0b1f4758cda3412b364582b4271ffe1` produces an archive whose SHA-256 is **`2b2e3251b678a5e83735ee663176f7763482a6948a7d5cb57f244dac1142874a`**, byte-identical to the frozen package.
`test_rf_r4_package_tools.py` asserts this when that commit is present in the clone and **skips with an explicit message otherwise** (for example a shallow CI clone); byte identity is claimed only where the test runs.

## 4. Controls (`test_rf_r4_package_tools.py`)

Two builds of the commit under test are byte-identical; verification passes from an independently extracted clean archive; eleven damaged packages (byte-tampered with and without refreshed manifests, closure module removed, undeclared file, absolute path, bytecode cache,
credential pattern, verifier removed, wrong index pin, sealer tampered, index pin removed) are each rejected through named checks, and refreshed manifests cannot hide a difference from the clean-archive comparison; the source closure resolves from a different working directory with the repository checkout
absent from the import path; a verifier crash exits 2 and is never counted as a rejection (including a fake verifier that raises or exits 1 without naming a failure); builder and verifier import no local code, only the standard library, and the verifier's code never references the builder or its outputs;
the provenance digests and redaction manifest below are self-consistent.

## 5. Gate 3 evidence preserved

`docs/evidence/r4_1_gate3_2026-10-13/` holds the replacement Gate 3 (package `2b2e3251…`, local, one fixture, `--boundary-only`): 88 bounded controls passed; 37/37 fixture-directory files verified; computation, sealing and independent acceptance all `YES`; the failed-preflight job sealed with `JOB_ACCEPTED: NO`;
five fixture/status tamper controls refused. Digests: **Gate 3 record listing `GATE3_R41_SHA256SUMS` = `05785f94d76c54a132785842bf1f9a7b0b761c9b3957f48451ee1ae92d65c2fd`**; **job manifest `JOB_SHA256SUMS` = `2104894ce26198e66b45fa30b7ac7c528691bfe2d27be83d903ac18621675882`**.
Disposition: `R4_1_PACKAGED_CUSTODY_VALIDATION_PASS`: local packaged custody validation only. It does **not** establish full r4 scientific acceptance or cross-host reproducibility.
The earlier failed attempt (`R4_GATE3_PACKAGE_A7FD10B1`, `FAILED_PACKAGED_CUSTODY_INTEGRATION`) is retained unchanged (`failed_gate3_a7fd10b1/`) and is not reclassified.

**Redactions.** Private absolute paths (the author's home-directory prefix) in copied logs and scripts were replaced by `$HOME`. `REDACTION_MANIFEST.txt` lists every copied file with its original and committed SHA-256 and the replacement count; files with zero replacements are byte-identical to the originals.
For redacted files, the digests in `GATE3_R41_SHA256SUMS` (an unmodified copy) refer to the originals recorded in that manifest.

## 6. What this does not do

It does not modify the frozen package, run the r4 experiment, authorize a merge, a single-host full run, a cross-host replay, or any Dedirock activity; each needs its own decision.
