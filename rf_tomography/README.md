# rf_tomography — Vertical RF Tomography research engine

Synthetic-only, sense-only. Separate from `scythe-web`; no radios, no production,
no governance bypass. Output is **simulated** and must never be shown as measured
evidence. Design notes: `docs/VERTICAL_RF_TOMOGRAPHY.md`.

## Phase A status: identifiability baseline

Question answered first: *for a given receiver layout and emitter class, can floors
be told apart at all?* No neural model yet; the baseline is a classical Gaussian
measurement model (RSS, azimuth/elevation AoA, TOA/TDOA with nuisance offsets) and
an exact grid posterior `P(floor | m, G)` with x,y marginalised.

    .venv/bin/python -m unittest rf_tomography.tests.test_baseline
    .venv/bin/python -m rf_tomography.benchmarks.identifiability --trials 40
    # -> benchmarks/results/identifiability_baseline.{json,md}  (deterministic, seed 7)

Layout: `geometry/` scene + LOS · `propagation/` log-distance baseline ·
`measurements/` receivers/emitters · `localization/` forward model + CRLB ·
`uncertainty/` floor posterior · `benchmarks/` layouts + runner ·
`placement/` receiver-placement optimiser · `measurements/impairments.py` NLOS/multipath truth generator · `uncertainty/robust_posterior.py` robust solver · `placement/mc_objective.py` Monte-Carlo placement objective · `correlation/` (empty: Phase C).

### Results (40 trials x 4 buildings per cell, nominal assumptions)

| layout | top-1 | top-3 | verdict |
|---|---|---|---|
| street_rss_az (RSS + 1 m azimuth) | 0.04-0.07 | 0.10-0.16 | unidentifiable |
| street_tdoa (+ 10 ns synced TOA) | 0.07-0.12 | 0.21-0.29 | unidentifiable |
| street_vertical_array (+ 3 m vertical aperture) | 0.55-0.64 | 0.90-0.96 | floor_ranked_top3 |
| street_plus_rooftops (+ 2 rooftop vertical arrays) | 0.74-0.82 | 0.99-1.00 | floor_resolved |

With 3 m / 3 deg of *unmodelled* bias, vertical_array drops to band_only,
rooftops to top-3, and 90% credible sets under-cover (0.73-0.86): the solver becomes
overconfident exactly when geometry/NLOS error is present (the TRACE motivation).

Sensitivity to the assumed angular multipath floor (915 MHz emitter, ad hoc run):

| angle floor | vertical_array top-1/top-3 | +rooftops top-1/top-3 |
|---|---|---|
| 1 deg | 0.83 / 1.00 | 0.97 / 1.00 |
| 2 deg | 0.58 / 0.97 | 0.81 / 1.00 |
| 5 deg | 0.27 / 0.71 | 0.51 / 0.98 |
| 8 deg | 0.20 / 0.50 | 0.48 / 0.91 |

## What these numbers do and do not mean

- **Best-case bound.** Solver model equals the generating model (nominal run),
  LOS state is known, noise is Gaussian, truth is within ~1 m of the candidate grid.
  Real urban multipath is not Gaussian. Failure here predicts failure in the field;
  success here predicts nothing.
- **Driven by assumptions, not measurements.** The 2 deg angular and 3 m range
  multipath floors, 10 ns sync, and path-loss exponent are placeholders. Result
  quality tracks the angle floor strongly (table above). Calibrate before citing.
- **Vertical baseline is the lever.** Ground-level receivers give essentially no
  vertical information (sigma_z 30-100 m). Vertical-aperture AoA and rooftop
  receivers at different heights are what create floor discrimination.
- **Emitter bandwidth barely matters in this model**, because sync error and the
  multipath floor dominate the TOA CRLB. Do not read this as "narrowband TDOA works":
  it assumes 0.1 s coherent integration and a Gaussian timing floor.
- Per-building problem only (building known). Cross-building association is Phase C.
- Success targets from the design doc (top-3 >= 90%, exact >= 70%, 90% intervals
  contain truth ~90%) are applied as verdict thresholds; they are engineering
  targets, not published figures.

## Receiver-placement optimiser

Chooses receivers from 57 candidate sites (street, rooftop corners, facade windows)
x 3 capability tiers (`rss_az`, `tdoa`, `array`) under a cost budget. Costs are
**assumptions**: tier 1/2/4, plus access cost street 0 / roof 1 / facade 2.

Search uses a fast analytic objective: per-probe expected floor-correct probability
`erf(h / (2 sqrt2 sigma_z))` from the position CRLB, with per-receiver Fisher
contributions precomputed (nuisance offsets via Woodbury). Greedy-by-gain-per-cost,
then swap local search; deterministic, about 4 s per budget. The analytic Jacobian is
unit-tested against the finite-difference CRLB (agreement within 0.2%).

    .venv/bin/python -m unittest rf_tomography.tests.test_placement
    .venv/bin/python -m rf_tomography.benchmarks.placement   # ~1 min -> results/placement.{json,md}

Validation against the full grid-posterior Monte Carlo (915 MHz emitter, 120 trials per
layout, so top-1 standard error is about +-0.045; differences below ~0.1 are noise):

| layout | cost | MC top-1 | MC top-3 | top-1 / top-3 with unmodelled bias |
|---|---|---|---|---|
| optimised, 5 receivers | 14 | 0.66 | 0.97 | 0.48 / 0.90 |
| optimised, 6 receivers | 20 | 0.78 | 1.00 | 0.52 / 0.95 |
| hand-built: 5 street arrays | 20 | 0.54 | 0.92 | 0.32 / 0.73 |
| hand-built: 5 street arrays + 2 roof arrays | 30 | 0.79 | 1.00 | 0.51 / 0.97 |
| random, median of 200 | 14 | 0.51 | 0.86 | 0.32 / 0.71 |
| random, best of 200 (by analytic score) | 14 | 0.57 | 1.00 | 0.44 / 0.91 |

- The analytic score ranks all 19 evaluated layouts like Monte Carlo does
  (Spearman 0.96 nominal, 0.94 for the robust objective vs. mismatch Monte Carlo).
- Optimised layouts match the hand-built roof layout at about two thirds of the cost.
  The recurring pattern: one street-level array next to the intersection, a rooftop
  receiver on a tall tower, and cheap RSS/azimuth receivers for geometry diversity.
- The optimiser is only modestly better than the best of 200 random layouts
  (0.64 vs 0.63 analytic at cost 14). Most of the value is avoiding bad layouts.
- Designing for the mismatch (`robust=True`, inflating variances by the unmodelled
  bias) picks nearly the same layouts and does not help. Placement cannot fix a
  model that is wrong: under unmodelled bias every layout loses ~0.15-0.25 top-1 and
  90% credible sets cover only ~0.78. That needs the model-error handling in Next.
- Weakest regions at cost 14: upper floors of the tallest tower (C:high 0.51) and
  low floors of A and C (~0.58). Spend extra budget there.
- In-sample caveat: probes are one fixed set of 96 points, and the scene, noise
  floors and costs are all synthetic.

## Impairment truth generator (NLOS, heavy-tailed multipath, geometry error)

`measurements/impairments.py` corrupts the *world*, never the solver. Mechanisms:
(1) a truly-NLOS receiver classified LOS has its AoA/TOA columns trusted although they
carry exponential excess delay and a wide angle error; (2) a truly-LOS receiver
classified NLOS loses its columns; (3) LOS AoA/TOA columns become reflected-path
outliers with some probability; (4) world building footprints are offset from the model,
so true LOS differs from the solver's. With impairments off the RNG stream is untouched:
all earlier results are bit-identical (checked). Severities in `PRESETS` are
**assumptions** (moderate: 10% outliers, 10% false-LOS, 5% false-NLOS, 1.5 m geometry;
severe: 25% / 25% / 10% / 4 m).

    .venv/bin/python -m unittest rf_tomography.tests.test_impairments
    .venv/bin/python -m rf_tomography.benchmarks.impairments   # ~45 s -> results/impairments.{json,md}

915 MHz emitter, 160 trials per cell (top-1 standard error about +-0.04), nominal Gaussian
solver unaware of the impairments:

| layout | none top-1 / cov90 | moderate | severe |
|---|---|---|---|
| hand: 5 street arrays + 2 roof arrays | 0.78 / 0.98 | 0.54 / 0.81 | 0.30 / 0.44 |
| optimised, cost 20 | 0.75 / 0.97 | 0.60 / 0.84 | 0.28 / 0.48 |
| optimised, cost 14 | 0.64 / 0.96 | 0.50 / 0.87 | 0.37 / 0.66 |
| random median, cost 14 | 0.51 / 0.94 | 0.32 / 0.84 | 0.24 / 0.62 |

(cov90 is the fraction of trials whose 90% credible floor set contains the truth.)

- **The failure is overconfidence, not just lower accuracy.** Under severe impairments the
  90% sets contain the truth only 44-66% of the time. The posterior stays sharp (set
  sizes barely change) while being wrong, which is exactly what the design doc says
  SCYTHE must not do.
- **Heavy-tailed multipath outliers do most of the damage**, then trusted-NLOS receivers.
  Classifying a LOS receiver as NLOS and the 1.5 m geometry error barely matter
  (the ablation table is in `results/impairments.md`). Geometry insensitivity is partly
  a property of this scene: 30 m street gaps mean small footprint shifts rarely flip LOS.
- **More angle/TOA columns can mean more exposure.** Under severe impairments the
  smaller optimised@14 layout (0.37 top-1) beats the richer layouts (0.28-0.30). This is
  about two standard errors, so treat it as suggestive, not established. A Gaussian
  likelihood gives every trusted column full weight, so one outlier on a high-weight
  column can dominate.
- Layouts optimised for nominal conditions are not optimal here, but the optimised and
  hand-built layouts still beat random ones at moderate severity.

## Robust solver (outlier mixture / Student-t + soft NLOS rejection)

`uncertainty/robust_posterior.py` keeps the grid, prior and Gaussian RSS block, and changes
the AoA/TOA likelihood: per-column `mixture` (inlier + wide outlier component) or `student`
(nu=3), plus a per-receiver latent NLOS state whose columns all come from a wide component
(prior 0.15), summed out so rejection is soft. The TOA clock offset is profiled with the
median residual. The posterior NLOS probability at the MAP candidate is returned so
rejection can be audited. Solver beliefs are fixed (eps 0.15, outlier 15 deg / 30 m, NLOS
30 deg / 60 m) and not tuned to the truth presets; outlier components are symmetric
although true excess delay is one-sided, so the solver is deliberately misspecified.

    .venv/bin/python -m unittest rf_tomography.tests.test_robust
    .venv/bin/python -m rf_tomography.benchmarks.robust    # ~3 min -> results/robust.{json,md}

All solvers see identical simulated observations (paired). 160 trials per cell, top-1
standard error about +-0.04. Table: top-1 / 90%-set coverage (target ~0.90).

| layout | preset | gaussian (old) | mixture | mixture+NLOS |
|---|---|---|---|---|
| hand: street + roof arrays | none | 0.78 / 0.98 | 0.78 / 0.99 | 0.78 / 0.99 |
| | moderate | 0.54 / 0.81 | 0.71 / 0.97 | 0.72 / 0.97 |
| | severe | 0.30 / 0.44 | 0.61 / 0.92 | 0.63 / 0.94 |
| optimised, cost 20 | none | 0.75 / 0.97 | 0.72 / 0.99 | 0.72 / 0.99 |
| | moderate | 0.60 / 0.84 | 0.72 / 0.98 | 0.71 / 0.98 |
| | severe | 0.28 / 0.48 | 0.46 / 0.89 | 0.43 / 0.89 |
| optimised, cost 14 | severe | 0.37 / 0.66 | 0.49 / 0.89 | 0.46 / 0.89 |
| random median, cost 14 | severe | 0.24 / 0.62 | 0.29 / 0.91 | 0.29 / 0.93 |

- **Calibration is restored.** Coverage returns to 0.89-0.94 at severe and is slightly
  conservative (0.95-0.99) at moderate and on clean data. Overconfidence goes from
  +0.25..+0.50 to about 0. This meets the "90% intervals contain the truth" target.
- **The price is honest width, not accuracy.** Mean 90% set size grows (e.g. 1.7 -> 2.8
  floors for the roof layout, 2.9 -> 4.6 for optimised@14, 4.1 -> 6.7 for random at severe).
  Top-1 recovers most of the lost ground at moderate (0.54 -> 0.72 against 0.78 clean).
  At severe it stays well below clean (information is genuinely destroyed), but top-3
  and band accuracy stay usable for the better layouts (roof layout top-3 0.93).
- **Column-level robustness does most of the work.** Explicit NLOS rejection adds little on
  top of it: mixture alone is as accurate (differences are within noise) and
  mixture+NLOS flags 44-93% of trusted-NLOS receivers with 0-6% false flags at the
  default. `nlos_only` (Gaussian columns + NLOS state) recovers about half the damage and
  at severe falsely flags 29-36% of clean receivers because it cannot explain isolated
  outliers. Student-t and mixture are indistinguishable at this sample size.
- **No meaningful cost on clean data** for the roof layout; the optimised layouts give up
  about 0.03 top-1 (within noise) and gain wider sets.
- **Rank reversal disappears.** With the robust solver richer layouts are no longer hurt
  more under impairments (severe: roof layout 0.63 vs optimised@14 0.46).
- **Wrong beliefs: be paranoid, not trusting.** Setting eps and pi to 0.40 costs only
  width (sets 3.5-4.1 floors at severe) and stays calibrated. Setting them to 0.02 holds
  up on the roof layout but undercovers on optimised@20 at severe (cov90 0.79 vs 0.89).
  Tripling the outlier scales is harmless. Beliefs 4x too low on outlier rate are the
  expensive error.

Limitations: the truth generator and these solver families share the same idea of what
an outlier looks like, severities are assumptions, and real multipath is correlated across
columns and receivers in ways neither models. Treat these as an existence proof that the
design target is reachable, not a field result.

## Correlated / coherent multipath test (errors the robust solver was not built for)

The robust solver assumes independent heavy-tailed column errors. `measurements/impairments.py`
also generates coherent errors (all off by default; earlier results unchanged, checked):

- **Single-bounce reflections** (`reflect_prob`): a receiver's az/el/TOA are replaced by those
  of the mirror-image emitter in the ground plane or in a neighbouring facade. Columns stay
  mutually consistent, delay excess is the exact one-sided path-length difference, RSS
  untouched. A ground bounce flips the elevation sign, so it makes the emitter look *lower*.
  Facade images need a face hit within its extent, emitter and receiver on the same side, and
  **both legs unobstructed** (this last check was missing in the first version; ray tracing
  exposed it, see "Sionna RT" below; all results in this section were regenerated afterwards).
- **Common elevation bias** (`el_bias_common_deg`): one calibration/tilt offset shared by every
  array. Averaging over receivers cannot remove it.
- **Common delay** (`common_delay_mean_m`): positive control. It is the clock offset, so both
  solvers must be invariant (unit-tested exactly, and confirmed in the benchmark).

    .venv/bin/python -m unittest rf_tomography.tests.test_correlated
    .venv/bin/python -m rf_tomography.benchmarks.correlated   # ~6 min -> results/correlated.{json,md}

Top-1 / 90%-set coverage, 160 trials per cell (top-1 SE about +-0.04), identical observations
per solver. Severities are assumptions (refl_mild 15% / refl_strong 35% per LOS receiver).

| layout | scenario | gaussian | robust | robust, paranoid beliefs |
|---|---|---|---|---|
| roofs | refl_mild | 0.43 / 0.49 | 0.84 / 0.99 | 0.81 / 0.99 |
| roofs | refl_strong | 0.13 / 0.14 | 0.66 / 0.94 | 0.62 / 0.93 |
| optimised@20 | refl_strong | 0.14 / 0.18 | 0.53 / 0.88 | 0.54 / 0.88 |
| optimised@14 | refl_strong | 0.23 / 0.34 | 0.53 / 0.87 | 0.54 / 0.88 |
| random median@14 | refl_strong | 0.24 / 0.52 | 0.33 / 0.85 | 0.35 / 0.86 |
| roofs | ground bounce only | 0.09 / 0.09 | 0.51 / 0.89 | |
| roofs | facade bounce only | 0.66 / 0.77 | 0.76 / 0.98 | |
| roofs | common delay only | 0.72 / 0.98 | 0.71 / 0.98 | |
| roofs | el bias 1 deg | 0.76 / 0.91 | 0.73 / 0.93 | |
| roofs | el bias 2 deg | 0.59 / 0.78 | 0.57 / 0.81 | |
| optimised@20 | el bias 2 deg | 0.51 / 0.83 | 0.50 / 0.88 | |

- **The Gaussian solver collapses under reflections** (coverage 0.09-0.77) with a large
  *downward* floor bias (up to -11 floors for ground bounces).
- **The robust solver largely survives**: coverage 0.85-0.99 although its independent-outlier
  model does not describe reflections. A reflected receiver is inconsistent with the other
  receivers' direct paths, so the per-receiver NLOS state flags it (24-98% of reflected
  receivers depending on layout and reflector; facade bounces are caught best, ground bounces
  and weak layouts worst).
- **Not fully fixed.** A residual downward bias of 1.0-2.6 floors remains for ground bounces and
  strong reflections, hidden by wider sets (3.4-8.5 floors under strong reflections). The robust
  solver is honest about it, not accurate. Coverage dips below target for the cost-14/20 layouts
  under strong reflections (0.87-0.88) and ground-bounce-only on optimised@20 (0.88).
- **Common delay is harmless**, as designed. A 1 deg common elevation bias is harmless (below the
  assumed 2 deg angular floor); 2-3 deg breaks coverage for *both* solvers (0.66-0.88), because
  a systematic bias is not an outlier and robust likelihoods do not help.
- **Mitigation tried and rejected as a default:** `RobustConfig.el_offset_sigma_deg` treats a
  common elevation offset as a marginalised nuisance (like Tx power and clock offset). It
  restores coverage for the pure calibration case (0.81 -> 0.96 roofs, 0.88 -> 0.95 optimised@20)
  but (a) costs about 0.02-0.10 top-1 even with no impairment, (b) cuts reflection-only
  coverage from 0.87-0.94 to 0.57-0.79 (median estimator) or to 0.26-0.57 (weighted mean,
  which reflected receivers contaminate). It stays off by default. The right fix is probably
  physical (calibrate array tilt) or a joint reflection + offset model.
- Not covered: temporally persistent multipath (the same reflection across snapshots, where
  averaging does not help), reflectors other than ground and building faces, and reflections
  that also affect RSS.

## Placement under the robust solver (Monte Carlo in the search loop)

The analytic objective assumes Gaussian noise, so it cannot score layouts under the robust
solver and impairments. `placement/mc_objective.py` runs the real solver instead, made cheap
by common random numbers: for each fixed simulated emitter the full observation vector is
generated once as if *every* candidate receiver were deployed (same generator as the other
benchmarks, via `simulate_observation`), a layout is a column subset with its sub-covariance,
and mean measurements are cached per candidate. About 0.5 s per layout. Checked against the
independent `run_cell` path on fresh draws: agreement within ~0.03 on p(true floor), top-1 and
coverage. The refactor that exposed `simulate_observation` is bit-identical to before.

Search: greedy by gain per cost, then first-improvement swap/add over the 60 best single
receivers, started from both the greedy result and the analytic layout. Design scenarios:
moderate (independent) and refl_mild (coherent); 96 trials each. Score = mean p(true floor).

    OMP_NUM_THREADS=1 .venv/bin/python -m rf_tomography.benchmarks.placement_mc --budget 14   # ~6 min
    OMP_NUM_THREADS=1 .venv/bin/python -m rf_tomography.benchmarks.placement_mc --budget 20   # ~10 min
    OMP_NUM_THREADS=1 .venv/bin/python -m rf_tomography.benchmarks.placement_mc_paired        # ~3 min

Mean probability assigned to the true floor on 400 fresh trials per scenario, robust solver,
paired differences with standard errors (`results/placement_mc_paired.md`):

| budget | scenario | MC-optimised | analytic-optimised | random median | MC - analytic | MC - random |
|---|---|---|---|---|---|---|
| 14 | none | 0.51 | 0.49 | 0.31 | +0.019 (0.015) | +0.195 |
| 14 | moderate | 0.43 | 0.45 | 0.28 | -0.024 (0.016) | +0.146 |
| 14 | refl_mild | 0.42 | 0.45 | 0.29 | -0.030 (0.016) | +0.125 |
| 14 | refl_strong* | 0.35 | 0.31 | 0.24 | +0.033 (0.017) | +0.103 |
| 20 | none | 0.59 | 0.60 | 0.40 | -0.011 (0.016) | +0.190 |
| 20 | refl_mild | 0.54 | 0.58 | 0.36 | -0.042 (0.014) | +0.173 |
| 20 | severe* | 0.41 | 0.42 | 0.29 | -0.011 (0.016) | +0.127 |
| 20 | combined* | 0.43 | 0.45 | 0.31 | -0.021 (0.015) | +0.118 |

(* not a design scenario.)

- **Monte Carlo search does not beat the analytic layout.** Differences have mixed sign
  (-0.042 to +0.033), mostly within one or two standard errors; at budget 20 they lean negative.
  In-sample the MC layout looks better (design score 0.471 vs 0.455 at cost 14, 0.590 vs 0.566 at
  cost 20), so the search is fitting the 96 design draws. **An earlier version of this section
  reported a small consistent MC gain (+0.006 to +0.04). That did not replicate:** it came from the
  first, over-permissive facade-reflection generator plus selection on the design draws, and
  was retracted after the generator was fixed (see "Sionna RT") and the searches re-run.
- **The analytic (Gaussian CRLB) search, 4 s, is as good as the 10-minute one** for the robust
  solver in this scene. Use it.
- **The optimum is flat and unstable.** The layouts found by the two searches share little (e.g.
  at cost 14 the MC layout is `FBxH:array, Sx0:array, RC--:tdoa, Sx-20:rss_az`, the analytic one
  `Sx20:array, RC+-:tdoa, RB-+:array, Sy-60/60:rss_az`) and rerunning after the generator fix
  changed the MC layouts. Many different layouts perform about equally.
- **Layout still matters a lot.** Both beat the median random layout by 0.10-0.20 in p(true
  floor), with tighter sets (cost 14, refl_strong: 5.7-5.9 floors vs 8.7) and better coverage
  at cost 20 under strong reflections (0.89-0.92 vs 0.82).
- **Placement does not remove the reflection bias.** Optimised layouts still sit 1.0-3.1 floors
  low under strong reflections, and coverage there is 0.84-0.92.
- Caveats: costs and tiers are assumptions; one emitter class, one scene; the fresh-bank
  comparison shares the generator, the 3 m grid and the solver with the search (the draws are new).

## Sionna RT scenes (ray-traced physics instead of assumed errors)

`propagation/sionna_scene.py` builds the *same* four-tower `Scene` in Sionna RT (cube buildings,
ground plane, ITU-R P.2040 permittivity/conductivity evaluated at the scene frequency), traces
many emitters to many receivers in batches, and returns a numpy `PathTable`
(`measurements/raytraced.py`: per-path power, absolute delay, arrival angles, interaction
bitmask). The core stays numpy-only; Sionna lives in its own venv.

**Environment (CPU only, no root).** The system lacks `libatomic.so.1`, which Dr.Jit needs, so a
private copy is extracted from the RHEL package; the LLVM backend needs `DRJIT_LIBLLVM_PATH`:

    /usr/bin/python3.12 -m venv ~/.venvs/sionna-rt && ~/.venvs/sionna-rt/bin/pip install sionna-rt
    cd /tmp && dnf download libatomic && rpm2cpio libatomic-*.x86_64.rpm | cpio -idm
    mkdir -p ~/.venvs/sionna-rt/extlib && cp -P usr/lib64/libatomic.so.1* ~/.venvs/sionna-rt/extlib/
    # ~/.venvs/sionna-rt/env.sh (source it):
    export LD_LIBRARY_PATH="$HOME/.venvs/sionna-rt/extlib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    export DRJIT_LIBLLVM_PATH=/lib64/libLLVM.so.21.1

Tested with sionna-rt 2.2.0, mitsuba 3.9.1, drjit 1.5.0. Memory: path discovery converges by
50k rays per emitter; diffuse tracing needs about 2 GB per process at 300k rays with 2 emitters
per batch. 500k rays x 4 emitters used 4 GB and was OOM-killed on this 7.6 GB host, and the
kernel also killed an unrelated benchmark running at the same time (silently; the failure looked
like a stale result file). Do not run diffuse tracing next to other jobs.

    source ~/.venvs/sionna-rt/env.sh
    PYTHONPATH=. ~/.venvs/sionna-rt/bin/python -m unittest rf_tomography.tests.test_sionna   # physics validation
    PYTHONPATH=. ~/.venvs/sionna-rt/bin/python -m rf_tomography.benchmarks.rt_trace --facade glass            # ~3 min, 400 emitters x 57 sites
    PYTHONPATH=. ~/.venvs/sionna-rt/bin/python -m rf_tomography.benchmarks.rt_trace --facade concrete --scattering 0.3 --samples 300000 --tx-batch 2
    .venv/bin/python -m rf_tomography.benchmarks.rt_calibration    # any venv, reads results/rt/*.npz (git-ignored, regenerate)
    OMP_NUM_THREADS=1 .venv/bin/python -m rf_tomography.benchmarks.rt_eval   # ~8 min

**Validation against hand-computable physics** (`tests/test_sionna.py`): LOS gain matches Friis to
0.05 dB, delay to 1 mm (Sionna delays are float32), arrival angles to ~1e-4 rad and follow the
convention "from the receiver toward the source"; the ground-bounce path exists at exactly the
image-source delay and angles (elevation below the horizon); batching changes nothing.
**The cross-check of my image-source reflection generator found a real bug**: it accepted
facade reflections whose legs cross the reflecting building. Fixed, regression-tested, and
all downstream results regenerated (see commit `607fc847`).

**What the ray-traced city says about the error model** (400 emitters x 57 sites, 915 MHz;
`results/rt_calibration_*.md`). "Solver-LOS" is the solver's own geometric belief.

| quantity | assumed so far | concrete facades | glass facades |
|---|---|---|---|
| strongest path is direct, solver-LOS links | outliers 10-25% | 0.98 | 0.97 |
| P(elevation error > 5 deg), solver-LOS | 0.01 Gaussian + outliers | 0.009 | 0.019 |
| P(TOA excess > 10 m), solver-LOS | 0.10-0.25 outliers | 0.002 | 0.000 |
| angle floor with a 3 m / 1 m aperture (beam peak, errors < 5 deg) | 2 deg | 0.4 / 0.5 deg | 0.7 / 0.8 deg |
| strongest path is a reflection, street receivers | 15-35% (refl_mild/strong) | 0.02 | 0.08 |
| path-loss exponent / RSS residual sigma, solver-LOS | 2.7 / 6 dB | 4.6 / 8.7 dB | 2.9 / 4.1 dB |
| solver-NLOS: strongest path direct / first arrival direct | not trusted | 0.24 / 0.73 | 0.29 / 1.00 |
| solver-NLOS: P(TOA excess > 10 m) | 0.20-0.25 | 0.20 | 0.00 |

- **The assumed impairments are harsher than the tracer produces.** Angular and delay floors are
  2.5-10x smaller than assumed (2 deg, 3 m), angle outlier rates 3-30x smaller (delay outliers
  far more), reflection-dominated receivers 2-15x rarer. The 'moderate' and 'severe' presets should be read as stress tests, not as this city.
- **Where it does bite:** street-level receivers under glass facades, where the ground bounce
  dominates the strongest path 8% of the time. That is the physical version of the reflection
  failure the correlated benchmark simulates.
- **Facade material matters more than any noise parameter I assumed.** Concrete gives a path-loss
  exponent of 4.6 (emitters inside a thick-walled box), far from the 2.7 the solver assumes; glass
  is close. NLOS delay is untrustworthy behind concrete and nearly usable behind glass.
- **NLOS angles are unusable** in both (median azimuth error 20-38 deg), consistent with the
  solver ignoring them.
- 30% diffuse scattering changes none of this noticeably (the strongest path stays direct), so
  the first-order picture is not an artifact of a purely specular tracer.

**The solvers on ray-traced observations** (`rt_eval.py`; 400 emitters, no synthetic multipath at
all; RSS = total power, TOA = first arrival within 20 dB, AoA = strongest path or the beam
peak; estimation noise and both nuisance offsets added; the solver is unchanged and knows
nothing about the tracer). Top-1 / 90%-set coverage:

| table | AoA | layout | gaussian | robust |
|---|---|---|---|---|
| concrete | strongest path | analytic@20 | 0.86 / 0.92 | 0.92 / 0.99 |
| concrete | strongest path | 5 street arrays | 0.99 / 1.00 | 0.97 / 1.00 |
| concrete | strongest path | random median@14 | 0.83 / 0.99 | 0.83 / 1.00 |
| glass | strongest path | analytic@20 | 0.92 / 0.97 | 0.96 / 1.00 |
| glass | strongest path | 5 street arrays | 0.83 / 0.87 | 0.93 / 0.98 |
| glass | beam peak | analytic@20 | 0.86 / 0.94 | 0.94 / 1.00 |
| glass | beam peak | 5 street arrays | 0.65 / 0.78 | 0.73 / 0.98 |
| glass | beam peak | random median@14 | 0.70 / 0.94 | 0.73 / 0.99 |

- **The robust solver is at least as good as the Gaussian one in every one of the 8 ray-traced
  settings for the optimised layouts**, and it is the one that keeps 90% coverage where it matters:
  5 street arrays under glass go from coverage 0.78-0.88 (Gaussian, floor bias -0.7 to -1.4) to
  0.98-0.99 (robust, bias -0.2 to -0.5).
- **Both solvers are conservative on this data**: coverage is ~1.0 and sets are 1.9-3 floors wide
  while top-1 is 0.9+, because the solver's assumed floors (2 deg, 3 m) are several times larger
  than what the tracer produces. Calibrating the floors to ray-traced statistics should sharpen
  it; not done yet, and real clutter would push the truth back toward the assumed values.
- **Layout ranking is not invariant to the physics.** The 5 street arrays are the best layout on
  concrete (0.92-0.99) and the worst of the three structured layouts on glass with beam-peak
  AoA (0.73 vs 0.91-0.94), because of the ground bounce. The analytic@20 layout (it includes
  rooftop receivers) is the only one that stays at 0.92-0.96 across all 8 settings. A placement that relies on street-level
  vertical arrays should not be trusted before the facade material is known.
- Placement quality still matters: random median@14 is 0.70-0.84 top-1 with sets of 3.6-4.5
  floors (7 for the paranoid solver) vs 0.88-0.96 (robust) and 1.9-2.5 floors for the
  optimised layouts.

**Limits of all of the above.** One synthetic scene, one frequency (915 MHz), concrete/glass
extrapolated below 1 GHz from P.2040, emitters inside *closed* boxes (no windows; wall
penetration is a thin-slab model), no foliage, vehicles or indoor clutter, specular + 30%
diffuse scattering only. Observables are my own simplified sensor models (AoA without path
phases, a fixed first-arrival threshold). The scene is not real Manhattan geometry. These are
physically-grounded cross-checks of the earlier assumptions, not field results.

## Calibrating the solver to the ray-traced statistics

`benchmarks/rt_solver_calibration.py` fits the solver's beliefs to each facade table from the
links the solver itself would trust (geometric LOS), then re-tests it. Fitted: angular floor and
outlier rate (beam-peak AoA, 3 m / 1 m apertures pooled), delay floor and outlier rate, the
receiver-level non-direct rate (-> NLOS prior), the NLOS angle/delay widths, and the log-distance
RSS model (exponent, LOS sigma, NLOS offset+sigma). Outlier/NLOS beliefs are doubled (safety factor
2); floors are clamped at 0.25 deg / 0.5 m so a clean table can never produce a "perfect" sensor
(the 3 m / 10 ns hardware sync noise is separate and unchanged).

    OMP_NUM_THREADS=1 .venv/bin/python -m rf_tomography.benchmarks.rt_solver_calibration   # ~4 min -> results/rt_solver_calibration.{json,md}

Evaluation is out of sample for the emitters: fit on even-numbered emitters, test on the 200
odd ones; and cross-table: fit on the *other* facade table. All variants see identical observations
(beam-peak AoA, ray-traced RSS/TOA plus estimation noise). Fitted beliefs (even emitters):

| belief | default | concrete | glass |
|---|---|---|---|
| angle floor (deg) | 2.0 | 0.40 | 0.63 |
| delay floor (m) | 3.0 | 0.5 (clamped; measured ~0) | 0.5 (clamped) |
| angle outlier rate | 0.10-0.25 | 0.020 | 0.036 |
| delay outlier rate | 0.10-0.25 | 0.002 | 0.000 |
| non-direct rate, solver-LOS | 0.10-0.25 | 0.023 | 0.036 |
| path-loss exponent | 2.7 | 4.5 | 2.85 |
| RSS sigma LOS / NLOS incl. offset (dB) | 6 / 8 | 8.8 / 8.2 | 4.1 / 5.7 |
| robust eps / pi_nlos | 0.15 / 0.15 | 0.04 / 0.05 | 0.07 / 0.07 |

On the odd (test) emitters, top-1 / 90%-set coverage / mean 90%-set size in floors:

| table | layout | robust, default beliefs | robust, calibrated (own) | robust, calibrated (other table) | gaussian, calibrated (own) |
|---|---|---|---|---|---|
| concrete | analytic@14 | 0.90 / 1.00 / 2.4 | 0.98 / 1.00 / 1.1 | 0.97 / 1.00 / 1.1 | 0.89 / 0.89 / 1.0 |
| concrete | analytic@20 | 0.94 / 1.00 / 1.9 | 0.99 / 0.99 / 1.0 | 0.98 / 1.00 / 1.0 | 0.89 / 0.89 / 1.0 |
| concrete | 5 street arrays | 0.94 / 1.00 / 3.0 | 0.94 / 0.96 / 1.1 | 0.94 / 0.98 / 1.2 | 0.94 / 0.95 / 1.1 |
| concrete | random median@14 | 0.80 / 1.00 / 4.5 | 0.89 / 0.97 / 1.8 | 0.89 / 0.97 / 2.0 | 0.84 / 0.96 / 1.4 |
| glass | analytic@14 | 0.92 / 1.00 / 2.4 | 0.97 / 0.99 / 1.1 | 0.94 / 0.98 / 1.1 | 0.90 / 0.92 / 1.1 |
| glass | analytic@20 | 0.94 / 1.00 / 1.9 | 0.97 / 0.99 / 1.0 | 0.96 / 0.97 / 1.1 | 0.88 / 0.89 / 1.1 |
| glass | 5 street arrays | 0.77 / 0.98 / 3.5 | 0.81 / 0.93 / 1.6 | 0.83 / 0.90 / 1.4 | 0.69 / 0.73 / 1.3 |
| glass | random median@14 | 0.73 / 1.00 / 4.7 | 0.80 / 0.96 / 2.2 | 0.77 / 0.91 / 2.1 | 0.73 / 0.86 / 1.7 |

- **On a world that is as clean as the tracer, calibration works and transfers.** The robust solver
  with fitted beliefs identifies the floor 97-99% of the time with credible sets of about one floor,
  against 1.9-2.4 floors for the default beliefs, with coverage still 0.99-1.00 on the analytic layouts.
  Beliefs fitted on the *other* facade material do almost as well on the analytic layouts
  (top-1 0.94-0.98, coverage 0.97-1.00; weaker layouts 0.77-0.89 with coverage 0.90-0.98),
  because what matters (angle/delay floors, outlier rates) is similar across the two; the very
  different RSS exponent (4.5 vs 2.85) barely matters.
- **The Gaussian solver cannot use the calibration.** With fitted floors it is sharp (1.0-1.1 floors)
  but its coverage is 0.89-0.92 on the analytic layouts (0.73 on glass street arrays), with a
  +0.25 to +0.29 floor bias on concrete, because the 2-4% outliers the tracer produces are not Gaussian.
- **Street arrays under glass stay the weak case** (top-1 0.81-0.83, bias about -0.35 floors,
  coverage 0.90-0.93): calibration sharpens the sets but cannot remove the ground-bounce bias.

**Stress test: what if reality is dirtier than the tracer?** The tracer has no clutter, so fitting
to it makes the solver sharp exactly where it may be wrong. Extra noise and heavy-tailed outliers
were added on top of the ray-traced observations (mild: +1 deg, +2 m, 5% outliers; strong: +2 deg,
+4 m, 15% outliers; the severities are assumptions) and three belief sets compared, all robust
solvers: default, calibrated, and calibrated with floors x3. Coverage / mean set size, analytic@20:

| table | stress | default beliefs | calibrated | calibrated, floors x3 |
|---|---|---|---|---|
| concrete | none | 1.00 / 1.9 | 0.99 / 1.0 | 1.00 / 1.2 |
| concrete | mild | 0.99 / 2.0 | 0.89 / 1.3 | 0.96 / 1.4 |
| concrete | strong | 0.95 / 2.5 | 0.66 / 1.9 | 0.84 / 1.9 |
| glass | none | 1.00 / 1.9 | 0.98 / 1.0 | 1.00 / 1.6 |
| glass | mild | 0.99 / 2.0 | 0.94 / 1.3 | 0.98 / 1.7 |
| glass | strong | 0.94 / 2.4 | 0.68 / 1.8 | 0.89 / 2.0 |

- **Calibration to an ideal tracer is fragile.** With mild extra dirt coverage falls to 0.82-0.94
  across layouts (default beliefs stay at 0.97-1.00); with strong dirt it falls to 0.58-0.78 and
  top-1 is *worse* than the default's (concrete analytic@20: 0.41 vs 0.55), i.e. confidently wrong.
- **Calibrated with floors x3 is the practical compromise**: 0.93-0.98 coverage under mild stress
  with sets of 1.4-3.2 floors (default: 2.0-3.7), but 0.81-0.89 under strong stress, where only
  the default's larger floors still cover (0.92-0.95). The Gaussian solver is overconfident under
  any stress (coverage 0.40-0.75).
- **So the right floors cannot be chosen from simulation alone.** They depend on how dirty the real
  world is, which needs field data. The method (fit from observed vs. direct-path residuals,
  inflate, test out of sample) is what this establishes; the fitted numbers are properties of this
  idealised tracer.

## Real footprints, mixed facades, windows (Midtown)

**Status: scene, validation, ray-traced calibration and solver evaluation are done (results below).**

- **Footprints** (`geometry/real.py`): NYC Building Footprints (public domain, OTI dataset
  `5zhs-2jue`), a trimmed extract around 5th Ave / 42nd St committed in `geometry/data/`
  (`--fetch` re-downloads it; the repo's root `.gitignore` ignores `data/`, so the package
  `.gitignore` un-ignores this file). 183 records -> 138 buildings within 260 m, median 47 m,
  40 with 20+ floors, tallest 281 m. Heights are roof heights in feet; ground is flat, courtyard
  holes are ignored, setbacks and rooftop structures are **not** modelled (each building is one
  prism of its roof height).
- **Polygon buildings** (`geometry/scene.py`): `PolygonBuilding` has the same interface as the box
  `Building` (segment blocking, truth sampling, candidate grids), so solvers and benchmarks need
  no special cases. Tested: a rectangular polygon blocks exactly like a box on 6,000 random
  segments; an L-shaped footprint handles the notch, arms, roof and ground correctly. All
  earlier synthetic results are bit-identical after the refactor (24 configurations compared).
- **Windows and mixed facades** (`geometry/mesh.py`): ear-clipping triangulation for concave
  footprints, outward-facing wall quads, and per-floor window bands (spandrel / glass /
  spandrel) with a glazing ratio per style: concrete 0, masonry 0.25, curtain wall 0.60, glass
  curtain 0.80, all-glass 1.0. Windows are continuous ribbons with the same glazed *area* as
  punched windows. "mixed" assigns styles by construction year and height (**a heuristic, not
  measured**: pre-1945 masonry; 1945+ curtain wall if >= 60 m else masonry; 2000+ glass curtain):
  118 masonry / 8 curtain / 12 glass-curtain in this extract. "concrete" and "glass_solid"
  reuse the same footprints as uniform baselines. The windowed scene is ~81k triangles
  (the solid ones ~2k).
- **Sionna scene** (`propagation/sionna_scene.py`): scenes with polygon buildings are written as
  per-material OBJ meshes. Tracing costs about 0.6 s/emitter at depth 3 and 1-3 s at depth 5
  with 100-400k rays (under 1 GB at depth 5). In this dense block, deep street links need many
  interactions: the fraction of links with no path at all was 43% at depth 3, 21% at depth 5
  and 11% at depth 7 (400k rays; 6 emitters x 54 sites), so depth matters and links without any path are treated as "no measurement".
- **Window physics, tested against prediction** (emitter at the centre of a 30 m x 30 m tower,
  receiver 60 m away at the same height so the ray crosses the wall horizontally): a curtain-wall tower leaks -69.1 dB at
  glass height, identical to an all-glass tower (-69.1 dB), and -77.0 dB at spandrel height,
  identical to a solid concrete tower (-77.4 dB): windows are worth about 8 dB and the spandrel
  behaves like concrete. A second building in the path costs > 10 dB and the solver's geometric
  test agrees with the tracer about which link is blocked. (A first version of that test was wrong:
  a sloped ray crosses the wall at a different height than the emitter's own. Worth remembering
  when reasoning about floor-dependent leakage.)
- **Tooling:** `rt_trace --scene midtown --policy {mixed,concrete,glass_solid}` (12 tallest
  buildings x N emitters, 54 sites: 40 street + 14 rooftop); `rt_calibration` and `rt_eval`
  rebuild the scene from the table metadata (`benchmarks/scenes.py`), use adaptive grid spacing
  for large footprints, and `rt_eval` takes `--aoa`, `--solvers`, `--emitter-stride`.

    source ~/.venvs/sionna-rt/env.sh
    PYTHONPATH=. ~/.venvs/sionna-rt/bin/python -m rf_tomography.benchmarks.rt_trace --scene midtown --policy mixed \
        --emitters-per-building 40 --max-depth 5 --samples 200000 --tx-batch 2      # ~20 min
    .venv/bin/python -m rf_tomography.benchmarks.rt_calibration
    OMP_NUM_THREADS=1 .venv/bin/python -m rf_tomography.benchmarks.rt_eval --tables <midtown tables> \
        --solvers gaussian robust --emitter-stride 2 --out-name rt_eval_midtown

**Results on Midtown** (480 emitters in the 12 tallest buildings, mean 46.6 floors so chance is 2%
top-1 / 6% top-3; 54 sites = 40 street + 14 rooftop; 915 MHz; depth 5; no scattering). Tables:
`results/rt_calibration_midtown_*.md`, `results/rt_eval_midtown.md`.

*Ray-traced observables vs the same links on the synthetic block (solver-LOS links, i.e. the
links the solver trusts; only 40% of Midtown links are solver-LOS):*

| quantity | synthetic block (concrete / glass) | Midtown solid concrete | Midtown solid glass | Midtown mixed with windows |
|---|---|---|---|---|
| strongest path is direct | 0.98 / 0.97 | 0.92 | 0.87 | **0.76** |
| P(elevation error > 5 deg) | 0.009 / 0.019 | 0.017 | 0.030 | **0.095** |
| P(azimuth error > 5 deg) | 0.020 / 0.033 | 0.047 | 0.088 | **0.128** |
| angle floor, 3 m vertical beam peak (deg) | 0.38 / 0.69 | 0.70 | 1.21 | 1.18 |
| P(TOA excess > 10 m) | 0.002 / 0.000 | 0.021 | 0.009 | 0.019 |
| path-loss exponent / RSS sigma (dB) | 4.6 / 8.7 ; 2.9 / 4.1 | 3.0 / 11.7 | 2.4 / 8.3 | 2.6 / 10.1 |
| solver-NLOS: strongest path direct | 0.24 / 0.29 | 0.28 | 0.33 | 0.25 |

- **Windows make the real city much dirtier than either solid-wall baseline on identical
  footprints**: direct path strongest 0.76 vs 0.92 / 0.87, angle outliers 3-5x. A plausible
  mechanism (not separately verified): the direct ray crosses opaque spandrel while a bounce
  leaves through a glass band, so the strongest path often is not the direct one. Mixed
  glass-and-opaque is worse than either extreme.
- **Midtown is dirtier than the synthetic block even with solid walls** (denser, more bounces).
  The first-pass finding "the ray-traced city is cleaner than assumed" was a property of the
  synthetic block. On Midtown with windows the angular statistics (floor ~1.2 deg, 10-13% outliers)
  are close to my original 'moderate' assumptions for angles; delay outliers (2%) and RSS sigma
  (10 dB vs 6) are not.
- Street and rooftop receivers are about equally affected (street: direct 0.78, P(el>5) 0.11;
  roof: 0.74, 0.084). The synthetic block's street-glass ground-bounce effect is a smaller part
  of the story here.

*Solver evaluation* (240 emitters per table, default beliefs, beam-peak AoA; top-1 / top-3 /
90%-set coverage / mean set size in floors), robust solver unless noted:

| layout | mixed with windows | solid concrete | solid glass |
|---|---|---|---|
| analytic@20 (incl. rooftops) | 0.43 / 0.77 / 0.96 / 8.7 | 0.54 / 0.83 / 0.95 / 8.3 | 0.51 / 0.82 / 0.97 / 8.2 |
| analytic@20, Gaussian solver | 0.36 / 0.65 / **0.77** / 6.7 | 0.47 / 0.74 / 0.87 / 6.9 | 0.46 / 0.73 / 0.90 / 6.9 |
| analytic@14 | 0.41 / 0.67 / 0.93 / 11.9 | 0.46 / 0.68 / 0.95 / 11.4 | 0.39 / 0.69 / 0.95 / 10.9 |
| 5 street arrays | 0.15 / 0.42 / 0.94 / 21.4 | 0.29 / 0.53 / 0.97 / 21.2 | 0.09 / 0.40 / 0.98 / 21.2 |
| random median@14 | 0.06 / 0.23 / 0.96 / 26.6 | 0.16 / 0.38 / 0.97 / 25.6 | 0.07 / 0.21 / 0.97 / 25.7 |

- **The problem is much harder here than on the synthetic block**: best layout top-1 0.43-0.54
  (0.92-0.96 on the block), top-3 0.77-0.83, 90% sets of 8-9 floors, floor error about 2 floors
  (7-9 m). It is still ~20x chance, and the sets are honest (coverage 0.93-0.97). Part of the
  drop is simply taller buildings (46 floors on average vs 27-44), part the denser NLOS, part the
  windows (-0.08 to -0.11 top-1 vs the solid baselines).
- **The robust solver matters more here**: with windows the Gaussian solver covers only 0.76-0.77
  (robust 0.93-0.96) and loses 0.05-0.12 top-3 across the analytic layouts.
- **Placement matters a lot**: layouts with rooftop receivers (analytic) reach 0.43-0.54; five
  street-level arrays reach 0.09-0.29 with ~21-floor sets, close to a random layout.
- **A positive floor bias of +0.3 to +1.2 floors** for the analytic layouts (the synthetic block
  had a negative one). I have not diagnosed it.
- Caveats: one seed, 240 emitters (top-1 SE about 0.03), default (uncalibrated) beliefs, the
  analytic layouts were chosen under the Gaussian CRLB objective, links with no path within 5
  interactions are dropped, and the window model is a continuous ribbon (an assumption that may
  exaggerate leakage geometry). `rt_solver_calibration` has not yet been generalised to this
  scene, so calibrated beliefs for Midtown are not evaluated.

**Limits of the scene itself.** Prisms without setbacks; flat ground; emitters inside *empty*
buildings (no interior walls or furniture); ribbon windows; facade styles from a heuristic; no
foliage, vehicles, street furniture or scattering by default; receiver sites chosen by me
(farthest-point street points, rooftops of the 14 tallest buildings); depth-limited NLOS.

## Next

0. Make `identifiability`, `placement`, `impairments`, `robust`, `correlated`, `placement_mc` and
   `rt_eval` read their assumptions from one calibrated config.
1. Measured-data calibration: the fit-and-inflate procedure is in place
   (`rt_solver_calibration.calibrate`), but the right inflation depends on real clutter. Needs real
   observed-vs-direct-path residuals (or a clutter model validated against measurements).
2. Midtown follow-ups: calibrate the solver's beliefs on the Midtown tables (default beliefs are
   only partly matched: angles yes, delay and RSS no), diagnose the +1 floor bias, test whether
   punched windows (instead of ribbons) change the dirtiness, then the rest of the realism list: setbacks and rooftop structures, interior walls, other
   frequencies (433 MHz, 2.4 GHz), coherent path phases for AoA, and feeding RT reflection
   statistics back into the correlated generator (its refl_mild/refl_strong are 2-15x the rates
   measured on the synthetic block).
3. Joint reflection + calibration-offset model, and a vertical-bias check that does not rely on
   posterior width alone (placement cannot fix the residual 1-2 floor downward bias).
4. Placement that is robust to the facade material (the street-array layout is not).
5. Emitters in more than one building (Phase C cross-building correlation).
6. Add GLocFM-style learned scoring and RF-3DGS fields only if they beat this baseline.
   (Both papers report indoor/Wi-Fi results: RF-3DGS ~2 ms render after ~3 min training;
   GLocFM -49.5%/-48.8% mean 3D error on its synthetic and NeRF2 sets. Neither is outdoor.)
