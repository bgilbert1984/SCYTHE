# Metamorphic span-boundary discrimination protocol (r4) — design

> **DESIGN ONLY — r4 IS NOT AUTHORIZED FOR EXECUTION.**
> This document specifies a separate research revision. It changes no production code, does not modify or
> reinterpret any r3 result, and authorizes no run, transfer or hardware contact. The offline, read-only geometry
> checks it cites (`tools/rf_metamorphic_r4_geometry_check.py`, outputs in this directory) are *not* the r4 experiment.

## 1. The narrow question

Can we deliberately construct an otherwise eligible candidate whose **bin-centre** frequency crosses SCYTHE's inclusive
usable-span boundary, and show that a **removed** span limit, a **widened** span limit and an incorrectly **strict**
boundary are each distinguishable *for the intended reason*?

The valuable outcome is not a fourth mutation kill. It is showing that the previously surviving mutant is distinguishable
because of the boundary, and not because of an unrelated selection rule.

## 2. Why r3 could not answer it

In the r3 experiment (cross-host verified) `span_limit_removed` **survived** every relation on all six mutant fixtures:

- the declared shifts (+1000, −2500, +100000 bins ≈ +3.9, −9.8, +390.6 kHz) never moved a candidate past the ±921.6 kHz
  usable half-span (every shift recorded *not applicable = 0*);
- no pinned fixture contains an out-of-span peak above the persistence margin, so the mutant's output equalled production's.

That is a harness coverage gap. It says nothing about whether production's span rule is correct.

## 2.1 Terminology: function-level versus production-path

- **Function-level**: a direct call to `detect_candidates` (or `measure_persistence`) on windows supplied by the harness. The r3 harness worked
  this way, and so does every measurement cited in this document — **including those at the production FFT length n = 524,288**.
- **Production-path**: input that passes through the sequencer's real acquisition or reanalysis path, which enforces the byte-exact
  1,048,576-byte (524,288-sample) window and the rest of the sequencer's checks.

Nothing in this document, and none of the 24 measured rows, exercises the production path. Where this text says "Layer B", it means the
metamorphic relation applied through the *production extraction function* (function-level) on the production length; production-path validation of
the same relation is a separate item for the r4 implementation review (§11) and is **not claimed here**.

## 3. Three things a boundary test must not conflate

| Concept | Meaning | Where it can differ |
|---|---|---|
| **Mathematical boundary** | the rational frequency `k·fs/n` compared with 921,600 Hz | exact for some `n` |
| **Computed value** | the IEEE-754 number `np.fft.fftfreq(n, 1/fs)[k]` | can differ from the rational by an ulp |
| **Production predicate** | `abs(freqs[1:-1]) <= usable`, evaluated on the *computed* values | the only one that decides admission |

**The oracle uses only the production predicate on the computed values.** No analytically idealized frequency, no epsilon to
manufacture equality, and never the reported (sub-bin) candidate frequency.

## 4. The production contract (read from `detect_candidates`)

- `usable = usable_half_span_hz()` = **921,600.0 Hz** (0.45 × fs, fs = 2,048,000 Hz).
- `freqs = np.fft.fftfreq(n, d=1/fs)`; local maxima are searched over bins `1 … n−2`.
- `in_span = np.abs(freqs[1:-1]) <= usable`: **inclusive**, on the **bin-centre** value.
- A candidate must also be a local maximum and exceed the persistence margin; the span rule is one of three.
- Sub-bin refinement uses a fixed `BIN_HZ = 3.90625` (valid only for n = 524,288).

### 4.1 What the expected verdict is about: admission, not retention

The span predicate decides **candidate admission** (`detect_candidates`). **Retention** (`measure_persistence`) is given a feature frequency
and applies only `|f| ≤ fs/2`; it does not apply the usable-span rule. Measured (`metamorphic_r4_retention_check_output.txt`): a tone at bin
235,930 (921,601.5625 Hz) and at 249,037 (972,800.78125 Hz) is **not admitted** by `detect_candidates`, yet `measure_persistence` reports
`qualifying=8, persistent=True` for both. Consequently:

- every r4 expected verdict is a statement about **admission**;
- a retention-based observation can never detect a span mutant, and r4 must not use one as evidence;
- "a candidate disappeared" is only meaningful if the paired control (§6) shows no *other* admission rule removed it.

## 5. Measured geometry (reproducible: `tools/rf_metamorphic_r4_geometry_check.py`; output committed)

Computed `np.fft.fftfreq` values and the predicate variants (`strict` is `<`; `widened` is `<= 1.01·usable` = 930,816 Hz):

**n = 524,288 (the production window)** — bin spacing 3.90625 Hz, usable/spacing = 235,929.6

| bin | computed Hz | production `<=` | strict `<` | widened | removed |
|---|---|---|---|---|---|
| 235,929 | 921,597.65625 | True | True | True | True |
| **235,930** | 921,601.5625 | **False** | False | **True** | True |
| 235,931 | 921,605.46875 | False | False | True | True |
| 249,037 (guard band) | 972,800.78125 | False | False | False | True |

No bin is on the boundary. Last in-span bin ±235,929, first out-of-span ±235,930; the guard band to Nyquist is 102.4 kHz = 26,214 bins.

**n = 524,280 — the floating-point trap** — usable/spacing = 235,926.0 exactly (rationally). The bin whose rational frequency equals 921,600
**computes to 921,600.0000000001 Hz**, so production's `<=` is **False** there: a mathematically inclusive boundary behaves as exclusive.
Production and the strict mutant therefore **agree** at that bin; a test built on the mathematical expectation would use the wrong oracle.
This geometry is a floating-point edge *diagnostic only*.

**n = 500,000** — bin spacing 4.096 Hz, usable/spacing = 225,000.0. Bin ±225,000 **computes to exactly 921,600.0**, `<=` is True, `<` is
False. Production includes it, the strict mutant excludes it, and `removed`/`widened` agree with production. (1,422 multiples of 20 in
[499,980, 550,000] are exact in this sense; whether a given `n` is exact is established by computing, never by arithmetic.)

Independent corroboration: the reviewer's NumPy check of the three boundary values agrees with these figures.

## 6. Two test layers

### Layer A — span-predicate correctness (no detector)

Evaluate the frequency grid directly against the inclusive production predicate and each variant. It isolates the floating-point boundary,
involves no candidate extraction, and is a **unit-level** result. A mutant distinguished here establishes only that the *predicate variants
differ on the grid*; it does **not** establish that the detector path distinguishes them.

### Layer B — metamorphic discrimination through the production extraction function (function-level, production length)

For a base candidate at bin `b`, apply the integer-bin circular shift `k = (T − b) mod n` (integer-modulo phase ramp,
`exp(2πj((k·i) mod n)/n)` with int64 `i`) so the spectrum is translated exactly. Target bins: ±235,929, ±235,930 (inside/outside pair), ±235,931 (neighbouring excluded bin),
±249,037 (guard band) on the production length. Expected production behaviour is computed *a priori* from Layer A and never from the detector:
predicate True ⇒ a candidate with `peak_bin == T`, same excess within 1e-9 dB, reported frequency equal to the base plus `(T−b)·Δ` within 1e-9 Hz;
predicate False ⇒ none at `T`, and the candidate count drops by exactly that candidate.

Required for every Layer B case:

1. the candidate would otherwise satisfy all detection and persistence preconditions (production-admitted in the base run, excess ≥ margin + 6 dB);
2. the **inside/outside pair differs only in controlled placement** (adjacent bins, identical tone power) — the *paired control*: production must show
   inside present and outside absent; if the inside case is absent, some other rule removed it and the pair is `PRECONDITION_NOT_MET`;
3. no DC, Nyquist, aliasing or unrelated rule explains a disappearance: `8 ≤ T ≤ n/2 − 8` or `n/2 + 8 ≤ T ≤ n − 8`, no other mapped candidate within ±8 bins, the
   achieved bin asserted equal to the intended bin;
4. the widened-span mutant is detected by an **actual out-of-span candidate** (±235,930 admitted by it, ±249,037 not);
5. the strict mutant receives credit **only** when the implementation's computed bin centre equals the boundary exactly.

### Layer B′ — function-level exact-boundary check at a non-production length (n = 500,000)

`detect_candidates` accepts any `n`, but **the sequencer's acquisition path refuses any window that is not byte-exact 1,048,576 bytes
(524,288 complex samples)**, and its sub-bin offset uses the fixed `BIN_HZ`. A 500,000-sample window therefore cannot occur on the production
acquisition path, and reported frequencies at that `n` are not production values. Layer B′ is a **synthetic, function-level** test of the predicate
inside `detect_candidates`; it is labelled as such everywhere and **never** presented as end-to-end coverage.

## 7. Mutants, expected detection conditions and credit

Single-edit mutants of the production source of `detect_candidates` (each edit asserted to match exactly once):

| Mutant | Edit | Detected by (measured, §5) |
|---|---|---|
| `span_limit_removed` (r3) | `in_span = np.ones(n-2, bool)` | any out-of-span case: ±235,930, ±249,037 (Layer B, production length) |
| `span_limit_widened` (new) | `usable` → `usable * 1.01` | ±235,930 and ±235,931 only; **not** ±249,037 |
| `span_limit_strict` (new) | `<= usable` → `< usable` | **only** ±225,000 at n = 500,000 (Layer B′ and Layer A) |

### 7.1 Attribution gate (required before any span-mutation credit)

A disappearance caused by a different rule must never earn span-mutation credit. For every Layer B case, the execution gate requires **all** of:

1. **Reference presence.** The target candidate at bin `T` is **present under the `span_limit_removed` mutant**. That mutant changes only the span rule, so
   its presence shows the candidate satisfies every non-span admission rule (local maximum, margin) at `T`.
2. **Paired eligibility.** The inside and outside observations of the pair both satisfy the non-span eligibility checks (§6 items 1–3): the inside case is
   present in production, and the outside case is present under the removed-span reference.
3. **Attribution.** An absence is attributed to the span rule only when `present(removed) ∧ absent(production)`. If the target is absent under the removed-span
   reference too, the case is `PRECONDITION_NOT_MET` (another rule removed it) and **no** mutant — removed, widened or strict — receives credit or blame for it.

The widened and strict mutants are credited only in cases where this attribution gate passed.

Credit rules:

- `MUTANT_DETECTED` only for a case whose production verdict is `RELATION_SATISFIED` and whose mutant verdict is `RELATION_VIOLATED`.
- **`span_limit_strict` is an equivalent mutant on the production acquisition geometry** (no bin on the boundary) and at n = 524,280 (float makes
  production exclusive there too). There it is recorded `MUTANT_SURVIVED_EQUIVALENT_ON_GRID`, with no detection credit and no survival blame.
- A kill at n = 500,000 is **function-level at a non-production length**. No kill in this document is production-path coverage; r4's summary keeps Layer A, Layer B (function-level, production length) and Layer B′ results separate, and reports production-path validation as its own item.
- A resource refusal earns no detection credit.

## 8. Corrections carried into r4 (proposed; **not** implemented here)

- **Sub-bin applicability.** r3 decided span applicability from the reported (sub-bin) frequency; production decides on the bin-centre. r4 uses the
  bin-centre predicate for every applicability decision. No r3 candidate came within a bin of the boundary, so r3's results are unaffected and are not
  reinterpreted.
- **Job-manifest sealing order.** r3's runner generated `JOB_SHA256SUMS` and then appended a terminal status line (`STATUS_LOG_APPEND_AFTER_HASH`). r4's runner writes
  and flushes the terminal status record first and generates the final job-level manifest last; the stale "12 bounded controls" label is replaced by the real count.

## 9. Validation before any evaluation (offline, bounded)

1. Layer A: the predicate variants on the grid for the three `n` (the committed output), re-derivable and compared exactly.
2. Layer B on the production length and Layer B′ on n = 500,000: observed presence equals the Layer A prediction in every row — **already confirmed
   for 24 rows with 0 disagreements** (`metamorphic_r4_geometry_check_output.txt`); those 24 rows are **function-level** observations (direct `detect_candidates` calls) at all three lengths, including n = 524,288, and are not evidence about the acquisition path. r4 re-runs them under its own package.
3. Exact-once source edits for all mutants; refusal tests for every precondition (DC/Nyquist proximity, collision, other-rule removal).
4. The retained r3 controls: durable output, comparators, identity/namespace, resource-complexity.

## 10. Fixtures

The 36 pinned fixtures (hash-verified, regenerated deterministically) for Layer B; deterministic synthetic one-tone windows for Layer B′ with a frozen
specification (seed, bin, amplitude 0.5, noise σ 0.01 per component) and recorded hashes. Synthetic windows are labelled synthetic.

## 11. Execution gate (later; not authorized by this document)

The gate includes the attribution gate of §7.1 for every Layer B case, and the r4 implementation review must say how production-path validation (§2.1) is obtained.

Package, hash and preflight as for r3; a two-fixture smoke with all six mutants and a gate; the full run; then an independent replay on a second host with
the three-class comparison. Each stage stops on a failed gate. None of this runs without separate authorization.

## 12. Decisions on the previously open questions

1. **Exact-boundary lengths.** One frozen exact geometry, **n = 500,000**, is the required Layer B′ discrimination test; it suffices for the narrowly stated
   strict-inequality question. n = 524,280 is retained as the negative floating-point diagnostic. Further exact lengths are optional robustness tests, not prerequisites.
2. **Guard-band control.** One point per sign, **±249,037**, is the required control; those points already separate removed-span behaviour from a 1%-widened span. A scan would answer a
   broader question and must not silently enlarge the frozen test.
3. **Scope.** r4 stays narrowly causal. Its required cases are the last admitted bin (±235,929), the first excluded bin (±235,930), the neighbouring excluded bin (±235,931) and the
   guard-band control (±249,037) on both signs, plus the exact-boundary function-level test (±225,000 with ±224,999 and ±225,001). A full span-edge scan is a separately specified regression experiment.

**Next gate.** After this design is merged, the next authorization gate is an implementation review of the r4 harness and its bounded tests, including how production-path
validation is to be done. It is not an automatic approval to execute r4.
