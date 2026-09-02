# ADR 0001 — Sparse recovery is validated before it is believed

- **Status:** Proposed, except §5 (Accepted and implemented)
- **Date:** 2026-09-01
- **Supersedes nothing.** Condensed from `docs/SparseSCYTHE.md` §§1–8, which is
  retained in full as the source discussion.
- **Related:** [ADR 0003](0003-rf-emission-tracking-hierarchy.md),
  `docs/RF_Signal_Family_Classifier_Scope.md`

## Context

SCYTHE's sparse estimator already follows the skeleton of the MIMO-FMCW
micro-motion paper [1]:

```
bounded FFT frames → temporal background → residual spectrum → candidate regions
  → peak-track carrier model → OMP periodic-amplitude model
  → support or explicit null outcome
```

The paper's value to SCYTHE is **not** its OMP routine and **not** its 97%
flight-mode classification figure. That figure is simulation-derived at modelled
conditions, and the authors name real-hardware validation as future work; it must
never become a SCYTHE claim. The value is a testing doctrine:

> A sparse result is believable only when its recovery conditions, competing
> models, compression stability, error behaviour and physical observability are
> explicit.

## Decision

### 1. Compression is a measured choice, not a doctrine

Replace the single 0.5 compression regression with a matrix:

| Axis | Values |
|---|---|
| Compression | 1.00, 0.75, 0.50, 0.25 |
| Sampling | uniform decimation, deterministic random, contiguous gap, jittered timestamps |
| SNR | −10, −5, 0, 5, 10, 20 dB |
| Signals | stationary, linear drift, periodic amplitude, two-signal collision, noise |
| Seeds | ≥ 20 per condition |

Measured: support hit rate · false-support rate · carrier-frequency RMSE ·
drift-rate RMSE · modulation-rate RMSE · support-family confusion · null-outcome
accuracy · residual reduction · runtime · seed stability.

The paper finds random sampling improves incoherence when targets are separable
but *loses* to uniform sampling when targets share a range cell. SCYTHE should
expect the analogue when emissions share an analysis bin. "Random is better" is
therefore a hypothesis to measure, not a policy to adopt.

### 2. Dictionary coherence gates identifiability

OMP returns a respectable answer from a dictionary whose atoms are
indistinguishable. Compute mutual coherence `μ(D) = max_{i≠j} |dᵢᴴdⱼ| / (‖dᵢ‖‖dⱼ‖)`
per window:

| μ | Verdict |
|---|---|
| < 0.60 | GOOD |
| 0.60–0.80 | AMBIGUOUS |
| > 0.80 | INSUFFICIENT_IDENTIFIABILITY |

A family is not emitted because its coefficient won by three decimal places.

### 3. M0 and M1 are separated explicitly

```python
m0 = fit_nuisance_model(window)      # noise_only, stationary_carrier,
residual = subtract_nuisance(...)    # linear_drift, gain_step, retune_transient
m1 = recover_periodic_support(residual)   # periodic_amplitude
decision = compare_models(m0, m1)
```

The comparison that matters is **improvement over the best nuisance model**, not
improvement over zero. A record therefore carries both fits and the increment:

```json
{"null_model": {"family": "stationary_carrier", "fit_score": 0.81},
 "alternative_model": {"family": "periodic_amplitude", "fit_score": 0.87},
 "incremental_residual_reduction": 0.06,
 "outcome": "NO_SUPPORT",
 "reason_code": "ALTERNATIVE_DOES_NOT_MATERIALLY_BEAT_NULL"}
```

This prevents persistent carriers, retune artifacts and AGC changes leaking into
the periodic model.

### 4. Time is measured, never assumed

Periodic atoms are built on **actual FFT timestamps**, not on an assumption of
uniform spacing — bridge cadence jitters, frames are deliberately compressed,
sockets interrupt, the orchestrator restarts. Windows publish
`available_frames`, `retained_frames`, `median_interval_ms`,
`interval_jitter_ms_rms`, `largest_gap_ms`, `sampling_pattern_hash`, and
`time_basis: measured_frame_timestamps`.

**Missing timestamps are never reconstructed as evenly spaced.**

### 5. Ambiguity outcomes are reason codes under a stable vocabulary — *implemented*

`NULL_OUTCOMES` extends with `MODEL_AMBIGUOUS`, `COLLISION_UNRESOLVED` and
`TIMING_QUALITY_INSUFFICIENT`, expressed as `{outcome, reason_code}` rather than
as an ever-growing flat enumeration.

**This shape is Accepted and shipped** — in `rf_signal_family.py`, where the
signal-family contract uses a three-value family plus a reason code. The sparse
estimator's own `NULL_OUTCOMES` has *not* yet been restructured; it remains a
published contract and its migration is a separate change.

### 6. Collisions are declared, not resolved by force

Two candidates in one bin, two unresolvable modulation rates, a strong carrier
masking a weak periodic component, supports that change family across seeds, or
solutions that swing after removing one frame — all yield a declared collision
with a frequency region and a boundary statement, never a forced label.

### 7. Supports need resampling stability, not just thresholds

```python
support_probability = matching_support_count / resample_count
emit = (snr_db >= min_snr_db and persistence >= min_persistence
        and residual_reduction >= min_residual_reduction
        and support_probability >= min_support_probability)
```

Expensive, so applied to candidate windows only — not to every noise window.

### 8. Validation output is not evidence

```
LIVE EVIDENCE STORE    measured and derived operational records
VALIDATION STORE       simulations, injections, replay results
GRAPHOPS               receives live evidence only
MODEL REGISTRY         receives benchmark summaries
```

The scenario generator produces **FFT products, not synthetic IQ** — that tests
the estimator at its actual boundary instead of testing a duplicate frontend.
A `VALIDATION` mode in the spectrum panel must be visually unmistakable
(`SIMULATION / REPLAY · NOT LIVE RF · NOT GRAPH EVIDENCE`) and the live waterfall
must stop or be clearly partitioned while it runs. Synthetic rows in the live
waterfall would be a provenance crime wearing attractive colours.

Benchmark output carries `authority: SIMULATION_VALIDATION` and
`promotion: NOT_LIVE_EVIDENCE`.

## Validation matrix and promotion gates

A support counts as correct only if all relevant properties are correct.
Frequency hits are judged against the **published analysis bin width**, not the
native transform width — peak downsampling destroyed 500 Hz resolution, so
`frequency_hit = error_hz <= analysis_bin_width_hz / 2`. Rate hits use
`max(0.5 Hz, 5% of expected)`.

| Metric | Promotion requirement |
|---|--:|
| Null false-support rate | ≤ 0.5% |
| Stationary-carrier hit rate at ≥ 10 dB | ≥ 95% |
| Drift-family hit rate at ≥ 10 dB | ≥ 90% |
| Periodic-amplitude hit rate at ≥ 10 dB | ≥ 85% |
| Family stability at 0.75 compression | ≥ 90% |
| Family stability at 0.50 compression | ≥ 75% |
| Seed-to-seed disagreement | ≤ 10% |
| Collision correctly declared ambiguous | ≥ 90% |

These are engineering gates, not claims of universal performance. False-support
rate on nulls is the hardest constraint, because a false support manufactures a
claim.

GraphOps accepts sparse results only against a passing validation manifest
(`estimator_revision`, `scenario_suite_hash`, `test_count`,
`null_false_support_rate`, `minimum_supported_compression`, status). Without one:
`GRAPHOPS // SPARSE SUPPORT QUARANTINED · REASON // ESTIMATOR REVISION NOT
VALIDATED`. Even with one, authority stays `DERIVED_INFERENCE` — **validation
improves confidence in a method; it does not turn inference into observation.**

## What does not transfer to one NESDR

The paper's instrument is an active MIMO-FMCW radar. The Nooelec is a passive,
single-channel receiver. It therefore cannot obtain from this method: target
range · radial velocity from a known transmitted waveform · angle of arrival ·
blade length · radar cross section · blade count · micro-Doppler attributable to
a drone · multi-target spatial separation · flight mode.

A measured periodicity may originate from transmitter scheduling, TDMA, switching
supplies, rotating machinery, telemetry cadence, fading, receiver gain behaviour,
interference — or an actual moving platform. SCYTHE may report the periodicity.
It may not leap from "23.4 Hz amplitude modulation" to "quadcopter rotor."

**Hardware tiers.** Tier 1 (current NESDR): occupancy, carrier/drift recovery,
periodic amplitude, burst cadence, compression experiments, time correlation with
GraphOps. Tier 2 (multiple synchronized passive receivers, common clock, phase
calibration, known baselines): passive AoA, TDOA, cross-sensor periodicity —
noting that independent NESDRs are not phase coherent merely because their
timestamps look similar. Tier 3 (MIMO-FMCW radar) is where the paper's physical
claims become reproducible, and at that point SCYTHE is the evidence and
orchestration layer *around* a radar, not software that grants radar observables
to RTL-SDR hardware.

## Real-hardware acceptance sequence

Receive-only, in order: noise-floor run (terminated input if practical; record
temperature and gain regime) → known stationary carrier → controlled retune
(the transient must not become a periodic support) → periodic-amplitude source of
known rate → frame-loss injection → multi-hour soak for false-support rate,
reconnect behaviour and memory bounds.

**Do not use an uncontrolled mystery transmission as the first validation.** If
the truth is unknown, the experiment can only prove that SCYTHE produced an
answer.

## Consequences

- Estimator work is gated behind a benchmark that does not yet exist, which
  delays new detection families. That is the intent: Pass 2 would otherwise
  generate more observations without establishing when recovery is trustworthy.
- A replay ring of **bounded FFT products** (not raw IQ) is needed to rerun frames
  against new estimator revisions — bounded by frame count and age, non-persistent
  by default, each result carrying `execution_mode: REPLAY` and
  `authority: DERIVED_REANALYSIS`.

## Open decisions

1. Whether Pass 1.5 precedes Pass 2 completion, as the source document argues.
2. Whether the sparse estimator's published `NULL_OUTCOMES` migrates to the
   `{outcome, reason_code}` shape now shipped in `rf_signal_family.py`.
3. Recommended order, unresolved: hardware trace acceptance → scenario generator
   → compression/SNR matrix → M0-vs-M1 comparison → stability selection →
   validation panel → GraphOps promotion manifest → Pass 2.

## References

1. Rai, C., Alex, S. J., Chattopadhyay, A. "Multi-Target Micro-Motion Parameter
   Estimation using MIMO-FMCW Radar with Limited Measurements." arXiv:2608.05216
   (5 August 2026). <https://arxiv.org/abs/2608.05216>
