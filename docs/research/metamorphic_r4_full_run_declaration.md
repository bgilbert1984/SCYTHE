# Metamorphic harness r4 — frozen execution declaration for the first full single-host run

> **FROZEN EXECUTION DECLARATION — AWAITING EXPLICIT RUN AUTHORIZATION**
> Merging this document is **not** authorization to run. The run requires a separate, explicit decision after this declaration and the final execution preflight have been reviewed.
> This change adds only this document. It changes no production code, harness, runner, package content, existing evidence or Dedirock configuration.

## 1. What is declared

One complete, local, single-host execution of the frozen r4.1 package on the mobile workstation (scythe-1), using the package's **unmodified** `tools/r4_job/run_job.sh`. Cross-host replay, Dedirock activity, real-IQ validation and deletion of retained evidence are outside this declaration.

| Parameter | Declared value |
|---|---|
| Package SHA-256 | `2b2e3251b678a5e83735ee663176f7763482a6948a7d5cb57f244dac1142874a` |
| Source commit | `8d34de9ac0b1f4758cda3412b364582b4271ffe1` |
| Package record | `docs/evidence/r4_1_packaging_provenance_2026-10-13/package_2b2e3251/PACKAGE_RECORD.txt`; built by the external r4.1 tools recorded in `docs/research/r4_package_tools_provenance.md` (the repository builder rebuilds it byte-identically) |
| Runner | the package's `tools/r4_job/run_job.sh`, sha256 `02453e0d2b29dd635b0389373f851886293c0c8b37ec431538e01856ffa22d20` |
| Harness | `tools/rf_metamorphic_harness.py` (revision `metamorphic-harness.r4`), sha256 `1ac4fe34260f6ba63056ed7dbf13f604d9b3f53f8ac77908f4c1ad052eac6687` |
| Sealer / independent verifier / gate | `seal_job.py` `9e7380ae…6be52`, `verify_job.py` `ec94db2e…32b1a`, `gate_results.py` `2dd3c880…c8e4` (full digests in the package's `SHA256SUMS`) |
| Fixture hash list | 36 hashes, sha256 `d42892ec15932b1883e8c59391625e1cdcd7d2171f588a275521b2b0d0b63e41` (unchanged since the equivalence package) |
| Generated index pin | `index.json` = `f52adb1974dce642bc62bf748add6733036db1cbc7bcba482691a221ad88eca3`; `holdout/` must hold exactly 37 regular files |
| Python / NumPy | **3.12.13 / 2.5.1** |
| Thread limits | `OPENBLAS_NUM_THREADS=1`, `OMP_NUM_THREADS=1`, `MKL_NUM_THREADS=1`, `NUMEXPR_NUM_THREADS=1` (the runner exports them; the preflight confirms them) |
| Environment | `PYTHONDONTWRITEBYTECODE=1` so that running the controls does not add bytecode to the extracted package (an environment variable, not a runner change) |

## 2. Interpreter rule

`PYTHON` must be set to an **explicit absolute path** of the verified interpreter before `run_job.sh` is invoked. The runner's built-in default is a host path and must never be relied on. The protocol does not name the workstation's home directory.

Preflight resolves `PYTHON` (`realpath`), requires `sys.version_info[:3] == (3, 12, 13)` and `numpy.__version__ == "2.5.1"`, and records the **resolved path, `sys.version`, NumPy version and the interpreter binary's SHA-256** in the local execution evidence (outside the sealed job directory).
**If the declared Python or NumPy is unavailable, the run is refused.** No other environment is substituted, and no version drift is tolerated.

## 3. Preflight (all must pass; any failure refuses the run and starts nothing)

1. The archive's SHA-256 equals the declared package digest; it is extracted into a **new, empty directory** (safe extraction: no absolute paths, `..`, links or devices), and `sha256sum -c SHA256SUMS` passes there. The extracted package is not modified afterwards.
2. The interpreter rule of §2.
3. The job directory is an absolute path that **does not exist**, on a filesystem satisfying §4. `run_job.sh` itself refuses an existing job directory; nothing is overwritten or deleted, before, during or after the run.
4. The package-tools verifier and the redaction-aware evidence verifier from the merged tools pass on the declared package record (artifact integrity only).
5. Host condition (§4): powered, not on battery-only operation if that can be detected, and sleep/hibernation suspended.
6. No other r4 job is running; the workstation's other load is recorded.

The preflight output is saved beside, not inside, the job directory, and checksummed with the final execution record.

## 4. Resources and availability

scythe-1 is a mobile workstation. The one-shot job has **no resume capability**.

- **Availability.** The host must remain powered and awake for the whole run. The operator confirms this before launch (power connected, sleep/hibernate and lid-close actions disabled for the duration, no planned reboot). Expected duration is well under an hour from the prior runs (the r3 full harness took about 3.6 minutes on a single Dedirock vCPU; r4 adds Layers A, B and B′, and the controls and fixture regeneration add a few minutes), but a **2-hour window** is declared to avoid inference from that estimate.
- **Interruption.** An unexpected shutdown, kill or loss of the WSL instance produces an **interrupted-attempt record**: the job directory is preserved as it stands, a record of the interruption and what was observed is written *outside* it, and **no scientific result is inferred** from partial output. A retry is a new attempt with a new job directory and attempt identifier, never a resume.
- **Disk.** Measured needs: the regenerated fixtures are 1.2 GB; the bounded controls generate a second 1.2 GB fixture set in the temporary directory (the real-generator integration tests); prior full-run results were about 2 MB and logs are small. The declared requirement is **at least 10 GB free on the filesystem of the job directory and on the filesystem of `TMPDIR`**, measured by the preflight with `df` (not inferred from the package size) and recorded. Below that, the run is refused.
- **Memory.** At least 4 GB available RAM at launch (recorded). The harness holds one fixture's windows at a time; observed peak is to be recorded after the run, not assumed.
- **Mutant resource caps** are those frozen in the harness (candidate cap 1,000,000; 900 s per mutant run). A cap hit is a `RESOURCE_LIMIT_REFUSAL`, never detection credit.

## 5. Execution structure (as implemented by the unmodified runner)

| Stage | What runs | Stop rule |
|---|---|---|
| Package verification | `sha256sum -c SHA256SUMS` | fail → `FAILED`, sealed, exit 10 |
| Bounded controls | the four packaged control modules (88 tests at this commit) | fail → `FAILED`, exit 11 |
| Fixtures | real generator → 36 fixtures + `index.json`; 36 hashes verified; index pin verified | fail → exit 12 / 13 / 16 |
| Smoke | harness `--limit 2 --mutant-fixtures 2` (identity, Layers A, B′, B, Phase A, and all six mutants on the 2 fixtures) | fail → exit 14 |
| **Smoke acceptance gate** | `gate_results.py … 2 2` | **must PASS; failure stops the job before the full run**, exit 15 |
| Full run | harness on all 36 fixtures, `--mutant-fixtures 6` | see below |
| Full gate | `gate_results.py … 36 6` | |
| Terminal record, exit codes | `DONE COMPUTATION_COMPLETED` only if the harness **and** the gate both returned 0, otherwise `FAILED` with both codes | written and fsynced before sealing |
| Sealing and verification | `finalize`: `seal_job.py --require-fixtures`, then `verify_job.py --require-fixtures` | nothing is written to the job directory afterwards |

Inside the full harness run the order is fixed: identity and namespace control first (a failure stops the run) → Layer A (grid/predicate, no detector) → Layer B′ (synthetic, non-production lengths, function-level) → Layer B (all 36 pinned fixtures, production length, function-level) →
**Phase A on all 36 fixtures (production relations) before Phase B** → Phase B (the six source mutants on the **first six** fixtures, 36 mutant records). Layers A, B and B′ are recorded in separate files and never claim production-path coverage.
The runner proceeds automatically from a passed smoke gate to the full run **only because** the whole one-shot job will already have received explicit execution authorization; this declaration is not that authorization.

## 6. Dispositions reported independently

| Dimension | Values |
|---|---|
| Computational execution | `COMPLETED` / `INTERRUPTED` / `FAILED` |
| Evidence custody | `SEALED` / `NOT_SEALED` (`EVIDENCE_SEALED`) |
| Independent verification | `VERIFIED` / `FAILED` / `NOT_EVALUATED` (`verify_job.py`, plus a separately implemented inventory check) |
| Production relation (per relation, per fixture) | `RELATION_SATISFIED` / `RELATION_VIOLATED` / `PRECONDITION_NOT_MET` / `NUMERICAL_AMBIGUITY` (and `TRANSFORM_OR_ORACLE_UNVERIFIED`, `NOT_COMPARABLE` where the harness emits them) |
| Mutation result (per mutant) | `MUTANT_DETECTED` / `MUTANT_SURVIVED` / `MUTANT_SURVIVED_EQUIVALENT_ON_GRID` / `RESOURCE_LIMIT_REFUSAL` |
| Scientific interpretation | supported only within the evaluated synthetic conditions |

`JOB_ACCEPTED: YES` establishes an intact, successfully executed job. It does **not** establish that all relations passed or that every mutant was detected: a `RELATION_VIOLATED` on production is a finding, not an acceptance failure, and a surviving non-equivalent mutant is a coverage finding.
A relation violation is never conflated with an execution failure, a refused precondition, numerical ambiguity or mutant equivalence, and an equivalent mutant is never counted as an undetected defect in that grid's coverage.

## 7. Expected observations (pre-registered; not gating)

These are expectations stated before execution so that deviations are visible. They are not pass/fail criteria beyond the integrity gates.

- Layer A and Layer B′ are deterministic: their results should equal those recorded in the one-fixture Gate 3 (`R4_1_PACKAGED_CUSTODY_VALIDATION_PASS`), including the strict-mutant detection at n = 500,000 (function-level only) and its equivalence at n = 524,288 and 524,280.
- On production geometry (Layer B): `span_limit_removed` and `span_limit_widened` are distinguishable from production where their predicates differ; `span_limit_strict` is equivalent on that grid. Blanket mutant kills are **not** expected.
- Phase B: the three non-span mutants (`absolute_floor`, `sign_dropped`, `first_repeat_only`) were detected by the r3 relations on synthetic fixtures; whether they are detected again is a result, not an assumption. The span mutants are attributed by the r4 boundary relation, not by the r3 relations.
- Production relations: the r3 relations are expected to hold as in r3, with any violation, ambiguity or refused precondition reported per fixture. This expectation is a hypothesis under test.

## 8. Custody and evidence

One-shot job directory; the harness never overwrites a results directory; no cleanup or deletion is authorized, including of earlier attempts, r2/r3 records and Dedirock evidence.
The final execution record (outside the sealed directory) holds: the resolved interpreter identity (§2), preflight output (§3), the three dispositions (`JOB_DIR.disposition.txt`), the independent inventory-check output, the job manifest digest, and its own checksum file.
Nothing is appended to the job directory after `JOB_SHA256SUMS` is published. If the run is interrupted, only the interrupted-attempt record of §4 is produced.

## 9. What this run would and would not establish

If completed, sealed and independently verified, it supports a **single-host** statement about whether r4 preserves the r3 production metamorphic relations and adds correctly attributed span-boundary discrimination across the 36 frozen synthetic fixtures.
It would **not** establish cross-host reproducibility, behaviour on real IQ, weak-signal sensitivity, or production-path coverage of the boundary sweep (which is function-level by design).

## 10. Next decision

After this declaration is reviewed and merged, and the final execution preflight is reviewed, the next decision is whether to authorize one complete local r4 experiment on scythe-1. Not before.
