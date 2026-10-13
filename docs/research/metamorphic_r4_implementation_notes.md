# Metamorphic harness r4 — implementation notes (for implementation review)

> **IMPLEMENTATION FOR REVIEW — THE r4 EXPERIMENT HAS NOT BEEN EXECUTED AND IS NOT AUTHORIZED TO RUN.**
> No production code is modified. The merged r3 evidence is untouched. Nothing here transfers or executes anything on another host.
> The only runs performed are offline unit tests, one production-path test, and one bounded validation on a single pinned fixture
> (`docs/research/r4_bounded_validation/`, non-evidentiary).

Implements the protocol frozen in `metamorphic_r4_span_boundary_design.md` (PR #164).

## 1. What is in this change

| File | Role |
|---|---|
| `tools/rf_metamorphic_harness.py` | revision **r4**: the r3 harness (relations, durable output, identity/namespace, keyed matching, cardinality guard, resource caps) plus the span-boundary protocol |
| `test_rf_metamorphic_r4_controls.py` | 18 bounded controls: oracle, attribution gate, credit rules, preconditions, layer separation |
| `test_rf_metamorphic_r4_production_path.py` | 8 tests through the real sequencer entry point (see §3) |
| `test_rf_metamorphic_r4_job.py` | 9 evidence-custody tests for the job runner (sealing order) |
| `test_rf_metamorphic_controls.py` | the r3 controls (17), unchanged, still passing against r4 |
| `tools/r4_job/{run_job.sh,seal_job.py,gate_results.py,PACKAGE_FILES.txt}` | runner with corrected sealing order, fail-closed sealer, acceptance gate, package source closure |
| `docs/research/r4_bounded_validation/` | outputs of the one-fixture bounded validation (non-evidentiary) |

The r3 harness lineage: the r3 source is preserved in the merged r3 evidence package (`package/rf_metamorphic_harness.r3.py`, PR #163).

## 2. The five acceptance gates

**A. Oracle fidelity.** `span_ok(n, b)` is `abs(np.fft.fftfreq(n, d=1.0/SAMPLE_RATE_HZ)[b]) <= usable`: the production expression, evaluated on the
computed bin-centre value. No sub-bin substitution, no idealised frequency, no epsilon. Controls: bit-identical to the production predicate across the whole grid
for the three lengths; an AST check that the oracle's *code* contains no `isclose`/`allclose`/`finfo`/`eps`/`Fraction`/`round`. r3's applicability used the reported
(sub-bin) frequency; `keyed_compare` now uses the bin-centre predicate (control: a candidate whose reported frequency exceeds the usable span while its bin centre is
inside is applicable). Recorded as an r4 correction; r3's results were unaffected (no candidate came within a bin of the boundary).

**B. Causal attribution.** `judge()` implements §7.1 of the design. Credit requires that the target is present under the span-removed reference and the paired
control holds (the inside case of the same anchor and sign is present in production *and* under the reference). Otherwise the case is `PRECONDITION_NOT_MET` and no variant is
credited or blamed. A span mutant indistinguishable from production at a target (its predicate agrees there) is `MUTANT_SURVIVED_EQUIVALENT_ON_GRID`. A production violation blocks
span-mutant credit. Non-span mutants are never credited by the boundary relation. Controls cover each branch, including the strict mutant on all three geometries.

**C. Geometry separation.** Three layers, separate result files and separate summary sections, none claiming production-path coverage:
`layerA.json` (grid and predicates, no detector), `layerB/<fixture>.json` (pinned fixtures, production length, function-level),
`layerBprime.json` (synthetic, non-production lengths, function-level). The strict-inequality kill at n = 500,000 appears only in Layer B′. The gate re-derives every
stored verdict and credit from the stored observations and rejects a detection credited where a variant is indistinguishable.

**D. Execution fidelity.** Retained from r3: whole-object identity and namespace verdict first (a failure stops the run), exact-once source mutations (now six), deterministic
transformations, integer-modulo phase ramps, declared tolerances, cardinality guards, resource caps, durable temp+fsync+rename output with a checksummed manifest, and the
floor-once vectorised one-repeat check.

**E. Evidence custody.** `run_job.sh` writes the terminal status record, then exit codes, then **seals last** with `seal_job.py`, and writes nothing afterwards (the r3 defect was
`STATUS_LOG_APPEND_AFTER_HASH`). `seal_job.py` refuses to seal without a terminal `DONE`/`FAILED` record and refuses to overwrite a manifest; every failure path also seals a
terminal record. The control count in the status line is read from the unittest output, not hard-coded. Nine tests, including a static ordering check of the script.

## 3. Production-path validation

**Entry point examined:** `rf_spur_catalogue_sequencer.run_epoch(decl, tuner)`.

| Stage | Object or bytes | Check |
|---|---|---|
| acquisition | `RtlTcpTuner` reading from an injected `socket_factory`; `acquire_visit` is duck-typed on the tuner | 8 windows per visit |
| admission | `_decode_iq(raw)` | the window must be **byte-exact 1,048,576 bytes** (524,288 samples), else `VisitRefused`; offset-binary uint8 to complex64 |
| detection | `detect_candidates(windows)` | contains the span predicate |
| retention | `measure_persistence(...)` per candidate | does **not** apply the span rule |
| surfacing | the acquisition journal record `visit_acquired` | `candidates` (with `peak_bin`) and `retained` |

**Test seam (existing, not added):** `FakeSocket` + `RtlTcpTuner(socket_factory=...)`, the pattern `test_rf_521_reanalysis_equivalence.py` already uses. The test patches the
same four **administrative** gates that test patches (`generate_tunings`, `generate_visit_schedule`, `check_declaration_authorized`, `admit_schedule`) so a single visit runs
without the 64-tuning invariant and the authorization binding; those have their own tests and are not part of detection. The "mutant" runs replace `seq.detect_candidates` with a
single-edit variant of its own source; nothing else changes. No production code is modified, and no hardware is involved.

**Result.** Classification: **`PRODUCTION_PATH_EXERCISED_FOR_ADMISSION_WITH_ADMINISTRATIVE_GATES_PATCHED`**. Through the real `run_epoch`, synthetic CU8 IQ at the exact 524,288-sample length:
- the eligible inside-bin tone (bin ±235,929) **is admitted and retained**;
- the equivalent outside-bin tone (±235,930) is **not admitted**;
- the same outside tone **is admitted when only the span predicate is removed** (the attribution gate, on the production path);
- the strict mutant behaves exactly like production on this geometry (equivalent mutant);
- a 500,000-sample window is **refused by `_decode_iq`**: n = 500,000 can never occur on the acquisition path and is correctly confined to function-level Layer B′.

**Not exercised (unresolved seam limits):** a real `rtl_tcp` socket and receiver; the authorization binding, schedule admission and 64-tuning invariant (patched); hardware effects
(gain, clipping, drift); multi-visit association and classification (a one-visit epoch makes the analysis trivial); the reanalysis-from-spool path (`reanalyze_from_spool`), which runs
the same `detect_candidates`. No test seam in production code was added or is proposed; none is needed for admission.

## 4. Bounded validation (one fixture; non-evidentiary)

`python3 tools/rf_metamorphic_harness.py … --limit 1 --boundary-only` produced: identity and namespace verified; Layer A; Layer B′ 12 cases; Layer B 8 cases (0 preconditions refused,
0 production violations); the acceptance gate passes after re-deriving every verdict and credit. The expected-versus-observed matrix is `docs/research/r4_bounded_validation/matrix.txt`:

- Layer B′, n = 500,000: bin ±225,000 — production admits; **`span_limit_strict` excludes it and is detected (function-level)**; removed and widened agree with production there.
- Layer B′, n = 524,280: bin ±235,926 — production **excludes** it (computed 921,600.0000000001 Hz); `span_limit_strict` is **equivalent** (no credit); removed and widened are detected.
- Layer B, production length: ±235,930 and ±235,931 — removed and widened detected; strict equivalent. ±249,037 — removed detected; widened equivalent (still excluded at 930,816 Hz); strict equivalent.
- Every inside case: all variants agree with production (equivalent).

## 5. What this change does not do

- It does **not** run the r4 experiment: not on the 36 fixtures, not the six-mutant Phase B, not the cross-host replay, not on Dedirock. Layer B has been run on one fixture only.
- It does not re-run the r3 relation set under r4 beyond the unchanged r3 controls; the r3-versus-r4 applicability correction is covered by a unit control, not by a re-baseline.
- It does not package anything (`PACKAGE_FILES.txt` lists the source closure for a later, separately authorized package).
- It does not establish production-path coverage of the full 36-fixture boundary sweep: that sweep is function-level by design, and the production-path claim is limited to the admission test above.
- It does not modify the detector, retention, or any threshold.

## 6. For the review gate

Please confirm: the oracle and attribution logic (§2 A, B); the layer separation (§2 C); that the production-path classification in §3 is the right label and the patched administrative gates are acceptable;
and whether the package, the one-fixture bounded validation, or the 36-fixture run is the next authorization. Execution remains behind its own gate.
