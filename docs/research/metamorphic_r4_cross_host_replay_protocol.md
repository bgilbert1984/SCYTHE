# Metamorphic harness r4.1 — cross-host replay comparison protocol (Gate B draft)

> **PROTOCOL DRAFT FOR REVIEW — NO TRANSFER, NO EXECUTION AND NO CONNECTION TO ANY OTHER HOST IS AUTHORIZED BY THIS DOCUMENT.**
> It adds only this document. It changes no production code, harness, runner, package, tool or evidence. Tolerances below are fixed **before** any second-host result exists and may not be revised after one is observed (§7).

> **Revision 2 (before any Gate C or D activity):** added the tolerance cap and the reclassification of grid constants to class E (§4.2, §4.3) after inspecting the retained local reference for large-magnitude numeric fields, and added the local runtime baseline (§6). No second-host result exists, so no rule was fitted to one.

## 1. Question and non-question

**Question.** Does a second host (Dedirock) reproduce the complete retained local r4.1 verdict: the same categorical outcomes in every layer, relation, mutant and refusal, and numerical measurements that agree within frozen tolerances, from the same frozen package, fixtures and evaluation order?
**Not the question.** Whether both runs exit successfully, whether the detector is correct on real signals, or whether anything should change in the detector. Two sealed jobs that both end `JOB_ACCEPTED: YES` can still disagree on a verdict; that disagreement is the finding this protocol exists to detect.

## 2. Gates

| Gate | Content | Status |
|---|---|---|
| A | Local evidence published (PR #169, merge `f0e2ef6`; disposition `R4_1_SINGLE_HOST_SYNTHETIC_EXPERIMENT_VERIFIED`) | **done** |
| B | This protocol: canonical fields, equivalence classes, tolerances, environment differences, refusal classification, comparator requirements | draft |
| B′ | Comparator tool implemented and tested against synthetic perturbations (§8), reviewed in its own PR | not started |
| C | Dedirock preflight: package identity, interpreter, resources, custody, restricted transfer scope (§6) | not authorized |
| D | One sealed replay job on Dedirock; comparison against the retained reference | not authorized |

Each of C and D needs its own explicit decision after the previous gate's record is reviewed. Authorizing a gate does not authorize the next.

## 3. What is frozen (identical on both hosts, by exact comparison)

| Item | Value |
|---|---|
| Package SHA-256 | `2b2e3251b678a5e83735ee663176f7763482a6948a7d5cb57f244dac1142874a` (source `8d34de9ac0b1f4758cda3412b364582b4271ffe1`) |
| Runner / harness | the package's unmodified `run_job.sh` (`02453e0d…`) and harness (`1ac4fe34…`); harness and production-module digests in `run_header.json` must equal the reference's |
| Fixtures | the 36 pinned hashes (list digest `d42892ec…`), `index.json` `f52adb19…`, `holdout/` exactly 37 regular files, regenerated on the second host by the frozen generator (the corpus is never transferred) |
| Evaluation order and scope | identity → Layer A → B′ → B (36) → Phase A (36) → Phase B (6 mutants × first 6 fixtures); smoke gate first, as the runner does |
| Declared parameters | the whole `declared` block of `run_header.json` (tolerances, caps, plans, specification digest) and `thread_env` (all four limits `1`) |
| Reference | the retained local sealed job (attempt `R4FULL-20261014T023003Z-2b2e3251b678-SCYTHE-30d8a2f0`, job manifest `ff5f86a2…21d2`), compared in its original form on the workstation and cross-checked against the committed copy |

A mismatch in any frozen item is a provenance failure: **the comparison is refused**, not scored (§5).

## 4. Canonical comparison

### 4.1 Compared artifacts
`results_full/` and `results_smoke/`: `identity.json`, `layerA.json`, `layerBprime.json`, `layerB/*.json` (36), `phaseA/*.json` (36), `phaseB/*.json` (36), `run_header.json`, `summary.json`; plus the fixture hash verification outcome, the bounded-controls result (test count and `OK`), the gate outputs (`GATE PASS` and its info lines) and the exit codes.
The two result trees must contain **exactly the same set of file names**; a missing or extra record is `INCOMPARABLE`.

### 4.2 Field classes (every leaf of every compared JSON belongs to exactly one)
| Class | Rule | Examples |
|---|---|---|
| **E: exact** | equal as values and as JSON types (`int` vs `float` is a difference) | all verdict and credit strings, statuses and reasons, `production_path`, booleans, integers (bins, candidate counts, case counts, qualifying counts), bin lists, strings including `bin_centre_hz`, the `declared` block, `thread_env`, digests, key sets; **and the floating-point constants that define the grid or the synthetic specification, not measurements**: `layerA.json` `usable_hz` and `geometries/*/bin_spacing_hz`, and `layerBprime.json` `synthetic_spec/*` |
| **N: numeric** | floating-point leaf; passes if `abs(L − R) <= A + Rr · max(abs(L), abs(R))` | `anchor_excess_db`, `max_*_dev_*`, `ROLL` deviations, excess lists (e.g. Phase B `lost`), `ideal_minus_usable_hz` |
| **X: environmental, excluded** | recorded, never compared for equality | `run_header.argv` (paths), `run_header.numpy` and interpreter strings (compared as environment, §5), timestamps, durations (`t=…s`), `STATUS.txt`, `env.txt`, host names, `MANIFEST.sha256` and `JOB_SHA256SUMS` (they hash host-specific fields) |

Lists are compared element by element with equal length required. Unknown fields not in a class table above are **E** (the default is exact).

### 4.3 Frozen numerical tolerances
```
A    = 1e-9    absolute, in the field's own unit (Hz, dB or bins): the harness's own declared equality tolerance (TOL_HZ = TOL_DB = 1e-9)
Rr   = 1e-12   relative: about 4,500 float64 ulps, an allowance for reassociation inside library FFT and reduction routines
Cap  = 1e-8    the effective tolerance A + Rr * max(abs(L), abs(R)) is never larger than 10 * A
```
All three were fixed from the harness's declared constants and float64 arithmetic, **not** from any Dedirock output. They are applied to every N field alike; no field gets a bespoke tolerance.
**Why the cap (found by inspecting the retained local reference, before any second-host result exists).** Of the 76 floating-point leaf paths in the reference, 5 carry raw large-magnitude values (frequencies of order 1e5 to 1e6 Hz: the Phase B `lost` frequency lists and a Phase B `max_freq_dev_hz` of a violating mutant; plus two grid constants, now class E). For such values the relative term alone would allow about 1e-6 Hz of slack, 900 times the harness's own 1e-9 Hz equality tolerance, which could swallow a real difference. With the cap, large-magnitude frequencies are held to 1e-8 Hz (about 86 float64 ulps at 1e6), and the relative term only matters for magnitudes above 1e3. Every other path has a maximum magnitude below 1e3 (the largest is a 1000x scale factor), where the relative term adds at most 1e-9.
Many N fields are deviation residuals of order 1e-10 to 1e-14 sitting beside the 1e-9 and 1e-6 verdict thresholds. Because of this, **class E (the verdicts derived from them) is always compared too**: a residual pair that is numerically close but falls on opposite sides of a verdict threshold is a categorical difference and is reported as such, never absorbed by the tolerance.
Context, not assumption: the r3 cross-host comparison (62 files, 9,624 leaves) was bit-identical. That does not enter any rule here.

### 4.4 Per-leaf and per-run outcomes
Per leaf: `IDENTICAL`, `WITHIN_TOLERANCE` (N only, differs but passes), `NUMERICAL_DIFFERENCE` (N, fails), `CATEGORICAL_DIFFERENCE` (E differs), `STRUCTURAL_DIFFERENCE` (key set, type, list length or file set differs).
The comparator always reports, per field class and per N field path, the **maximum absolute difference, the maximum relative difference and the number of leaves compared**, so the distribution is visible whether or not anything fails.

## 5. Dispositions

### 5.1 Replay execution (reported independently of equivalence)
`REPLAY_NOT_EXECUTED` (preflight refused) · `REPLAY_INTERRUPTED` · `REPLAY_EXECUTION_FAILED` (nonzero harness or gate) · `REPLAY_COMPLETED`; then custody `SEALED / NOT_SEALED` and independent verification `VERIFIED / FAILED / NOT_EVALUATED`, exactly as for the local run. `JOB_ACCEPTED: YES` is required to enter the comparison and says nothing about equivalence.

### 5.2 Comparison result
| Result | Condition |
|---|---|
| `COMPARISON_REFUSED_PROVENANCE_MISMATCH` | any frozen item of §3 differs, or either job is not sealed and verified |
| `INCOMPARABLE` | a structural difference (file set, key set, type, list length) |
| `SEMANTIC_DIVERGENCE` | at least one class-E difference, any layer |
| `NUMERICAL_DIVERGENCE` | no class-E or structural difference, at least one N leaf outside tolerance |
| `EQUIVALENT_WITHIN_DECLARED_TOLERANCE` | no E or structural difference; every N leaf passes; at least one differs |
| `EQUIVALENT_BITWISE` | every compared leaf is identical |
| `COMPARATOR_FAILURE` | the comparator crashed; never counted as, or as evidence of, equivalence or divergence |

Precedence is the order of the table (refusal first). The overall statement is `R4_1_CROSS_HOST_REPLAY_EQUIVALENT` only for the last two results, and only within the synthetic scope; otherwise `R4_1_CROSS_HOST_REPLAY_NOT_ESTABLISHED` with the result above. A divergence is recorded with its fields; **no cause is asserted** without a further, separately authorized isolation experiment.

### 5.3 Environmental differences (recorded, never hidden)
Expected and permitted: host name, kernel and OS, CPU model and core count, Python patch version, NumPy version, linked numerical libraries, absolute paths, clocks, durations. The Gate C record lists them for both hosts **before** execution; the comparison report prints that table beside the result.
Not permitted to differ: package, fixtures, harness, `declared` block, `thread_env`, float64 dtype, byte order. The replay environment is whatever the Gate C preflight records; if the launch-time environment differs from the Gate C record, the replay is refused. Prior agreement between the hosts (r3) is not assumed; a different NumPy or Python is simply a recorded difference, and a divergence found with a different NumPy is reported as environment-**associated**, not environment-**caused**.

## 6. Dedirock preflight (Gate C) and transfer scope, for later authorization

- **Transfer scope** (restricted): the package archive (about 120 KB) with its digest sidecar and the preflight tool, into a **new** directory named for the replay attempt. Nothing is written elsewhere; no existing directory (including the r2/r3 evidence directories) is read, modified or removed; no deletion, installation, privilege escalation or service change. The fixture corpus is regenerated remotely, never transferred.
- **Preflight (remote, read-only except its own directory):** archive digest and safe extraction; `sha256sum -c`; runner and harness digests; explicit absolute interpreter path with exact Python/NumPy versions recorded **as environment, not as a pass condition other than being recorded and then frozen for launch**; thread limits; at least 10 GB free on the job and temporary filesystems, at least 4 GB available memory (measured); job directory nonexistent; no competing r4 job; load recorded; an attempt ID bound to package, host identity, interpreter identity and job path, checksummed before launch.
- **Availability:** a server host is expected to stay up, but there is no resume. A 4-hour window is declared; an interruption yields an interrupted-attempt record and no inferred result. Exceeding the window is recorded as an overrun and is not by itself a failure or a kill.
- **Runtime baseline.** The Gate C record states the local reference's actual stage durations so that a slower host is judged against a baseline, not just a window: total 24 min 51 s (2026-10-13 19:30:11 to 19:55:02 PDT; job ID timestamps are UTC), of which bounded controls 162 s, fixture regeneration and pin checks about 24 s, smoke run and gate about 3 min 23 s, full harness run and gate about 18 min 21 s. The replay reports its own stage durations and their ratios to these; a ratio is information, not a verdict.
- **Execution (Gate D):** the unmodified `run_job.sh`, `PYTHON` explicit, `PYTHONDONTWRITEBYTECODE=1`, started detached; progress observed read-only. One run only.
- **Return path:** the sealed job without `holdout/`, its disposition file and the preflight record are copied back to the workstation and verified by digest before any comparison; the remote copies are retained. The comparator runs locally on the retained reference and the returned copy.

## 7. No post-hoc changes
Classes, tolerances, outcomes and the comparator behaviour are fixed by this document as merged. If a replay shows that a rule was badly chosen, the replay result stands under the rules as written, and a **revised protocol** with a **new replay** is required; re-scoring the same results under new tolerances is not allowed. Failed, interrupted and refused attempts are preserved.

## 8. Comparator requirements (Gate B′, a separate reviewed PR)
A standalone tool (stdlib only, no code shared with the harness) that implements §4–5 and refuses on §3. It must be shown to behave on synthetic perturbations of the retained local job before it is used on any replay:
the job compared with itself → `EQUIVALENT_BITWISE`; a copy with an N leaf changed just inside and just outside `A + Rr·max` → `EQUIVALENT_WITHIN_DECLARED_TOLERANCE` / `NUMERICAL_DIVERGENCE`; a changed verdict, credit, status, bin or count, an `int` turned into a `float`, a residual moved across 1e-9 or 1e-6 → `SEMANTIC_DIVERGENCE`;
a missing or extra record or key, a changed list length → `INCOMPARABLE`; X-class fields changed → no effect; a different package digest, fixture hash or `declared` value → `COMPARISON_REFUSED_PROVENANCE_MISMATCH`; an unsealed or failed-verification job → refusal; a crash → `COMPARATOR_FAILURE`, never equivalence.

## 9. What a positive result would and would not mean
It would show that, for these 36 frozen synthetic fixtures and this implementation, a second x86-64 host with a possibly different NumPy reproduces the first host's complete verdict within the frozen rules. It would not establish behaviour on other architectures or library stacks, on real IQ, at weak signal levels, or production-path coverage of the boundary sweep, and it would not validate the detector itself.
