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
now also generates coherent errors (all off by default; earlier results unchanged, checked):

- **Single-bounce reflections** (`reflect_prob`): a receiver's az/el/TOA are replaced by those
  of the mirror-image emitter in the ground plane or in a neighbouring facade (valid-geometry
  checked). Columns stay mutually consistent, delay excess is the exact one-sided path-length
  difference, RSS untouched. A ground bounce flips the elevation sign, so it makes the
  emitter look *lower*. Models a direct path suppressed by clutter the building-level LOS
  test cannot see.
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
| roofs | refl_mild | 0.44 / 0.57 | 0.74 / 0.96 | 0.73 / 0.98 |
| roofs | refl_strong | 0.21 / 0.25 | 0.59 / 0.91 | 0.57 / 0.92 |
| optimised@20 | refl_strong | 0.17 / 0.23 | 0.49 / 0.90 | 0.48 / 0.91 |
| optimised@14 | refl_strong | 0.16 / 0.29 | 0.34 / 0.83 | 0.33 / 0.84 |
| random median@14 | refl_strong | 0.17 / 0.46 | 0.33 / 0.89 | 0.32 / 0.89 |
| roofs | ground bounce only | 0.09 / 0.09 | 0.51 / 0.89 | |
| roofs | facade bounce only | 0.42 / 0.60 | 0.65 / 0.96 | |
| roofs | common delay only | 0.72 / 0.98 | 0.71 / 0.98 | |
| roofs | el bias 1 deg | 0.76 / 0.91 | 0.73 / 0.93 | |
| roofs | el bias 2 deg | 0.59 / 0.78 | 0.57 / 0.81 | |
| optimised@20 | el bias 2 deg | 0.51 / 0.83 | 0.50 / 0.88 | |

- **The Gaussian solver collapses under reflections** (coverage 0.09-0.60) with a large
  *downward* floor bias (-3 to -11 floors for ground bounces).
- **The robust solver largely survives**: coverage 0.83-0.98 although its independent-outlier
  model does not describe reflections. A reflected receiver is inconsistent with the other
  receivers' direct paths, so the per-receiver NLOS state flags it (31-97% of reflected
  receivers depending on layout; facade bounces are caught best, ground bounces worst).
- **Not fully fixed.** A residual downward bias of 1-2.6 floors remains for ground bounces and
  strong reflections, hidden by wider sets (4-10 floors for the weaker layouts). The robust
  solver is honest about it, not accurate. Coverage dips below target for the cost-14 layout
  (0.83) and ground-bounce-only on optimised@20 (0.88).
- **Common delay is harmless**, as designed. A 1 deg common elevation bias is harmless (below the
  assumed 2 deg angular floor); 2-3 deg breaks coverage for *both* solvers (0.66-0.88), because
  a systematic bias is not an outlier and robust likelihoods do not help.
- **Mitigation tried and rejected as a default:** `RobustConfig.el_offset_sigma_deg` treats a
  common elevation offset as a marginalised nuisance (like Tx power and clock offset). It
  restores coverage for the pure calibration case (0.81 -> 0.96 roofs, 0.88 -> 0.95 optimised@20)
  but (a) costs 0.02-0.10 top-1 even with no impairment, (b) cuts reflection-only
  coverage from 0.90-0.91 to 0.68-0.77 (median estimator) or to 0.39-0.40 (weighted mean,
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
benchmarks, via the new `simulate_observation`), a layout is a column subset with its
sub-covariance, and mean measurements are cached per candidate. About 0.5 s per layout
instead of seconds. Checked against the independent `run_cell` path on fresh draws: agreement
within ~0.03 on p(true floor), top-1 and coverage. The refactor that exposed
`simulate_observation` is bit-identical to before (24 configurations compared).

Search: greedy by gain per cost, then first-improvement swap/add over the 60 best single
receivers, started from both the greedy result and the analytic layout. Design scenarios:
moderate (independent) and refl_mild (coherent); 96 trials each. Score = mean p(true floor).

    OMP_NUM_THREADS=1 .venv/bin/python -m rf_tomography.benchmarks.placement_mc --budget 14   # ~5 min
    OMP_NUM_THREADS=1 .venv/bin/python -m rf_tomography.benchmarks.placement_mc --budget 20   # ~9 min
    OMP_NUM_THREADS=1 .venv/bin/python -m rf_tomography.benchmarks.placement_mc_paired        # ~2 min

Mean probability assigned to the true floor, 400 fresh trials per scenario, robust solver
(paired differences; standard errors in parentheses). Full tables in `results/placement_mc_*.md`.

| budget | scenario | MC-optimised | analytic-optimised | random median | MC - analytic | MC - random |
|---|---|---|---|---|---|---|
| 14 | none | 0.50 | 0.49 | 0.31 | +0.009 (0.006) | +0.185 |
| 14 | refl_mild | 0.45 | 0.43 | 0.26 | +0.015 (0.006) | +0.186 |
| 14 | severe* | 0.34 | 0.33 | 0.20 | +0.013 (0.007) | +0.142 |
| 14 | combined* | 0.36 | 0.35 | 0.25 | +0.017 (0.007) | +0.117 |
| 20 | none | 0.63 | 0.60 | 0.40 | +0.024 (0.012) | +0.225 |
| 20 | refl_mild | 0.56 | 0.55 | 0.35 | +0.015 (0.012) | +0.213 |
| 20 | severe* | 0.46 | 0.42 | 0.27 | +0.040 (0.014) | +0.193 |
| 20 | refl_strong* | 0.45 | 0.45 | 0.27 | -0.003 (0.013) | +0.180 |

(* not a design scenario.)

- **The Monte Carlo search finds a small, real improvement, not a large one.** The paired
  difference over the analytic layout is positive in 11 of 12 budget-by-scenario cells,
  +0.006 to +0.04 in p(true floor), typically 1-3 standard errors; relative gain ~2-9%.
  In-sample the gain looked about twice as large (design score 0.495 vs 0.464 at cost 14,
  0.621 vs 0.582 at cost 20): roughly half of it was selection on the design draws.
  The smaller 80-trial `run_cell` held-out runs in `placement_mc_B*.md` cannot resolve a
  difference this small (top-1 is a tie within +-0.03).
- **The analytic objective was already close to right for the robust solver.** The layouts
  share a core in all four cases: roof of tower C near the intersection (`RC+-:tdoa`), roof of
  B (`RB-+:array`) and a street array at x=+20 (`Sx20:array`). MC swaps the cheap street
  RSS/azimuth receivers for more street arrays (e.g. `Sy+-40:array`) and drops a second roof.
  Many different layouts perform about equally, a flat optimum.
- **Layout still matters a lot.** Both optimised layouts beat the median random layout by
  0.12-0.23 in p(true floor), and by 0.10-0.25 in top-1 under severe, strong-reflection and
  combined scenarios, with tighter sets (cost 14, refl_strong: 5.3-6.6 floors vs 8.8) and
  better coverage at cost 20 (0.89 vs 0.78).
- **Placement does not remove the reflection bias.** Optimised layouts still sit 1.4-2.4 floors
  low under strong reflections, and coverage there is 0.85-0.89, below target.
- Cost: 732 and 1166 Monte Carlo layout evaluations (4.4 and 9 min). Worth it as a check; the
  analytic search (4 s) gets within a few percent.
- Caveats: costs and tiers are assumptions; one emitter class, one scene; the fresh-bank
  comparison shares the generator, the 3 m grid and the solver with the search (the draws
  are new). Design used truth positions anywhere in the footprint but a coarser 3 m grid.

## Next

0. Make `identifiability`, `placement`, `impairments`, `robust`, `correlated` and `placement_mc`
   read their assumptions from one calibrated config once real receiver data exist.
1. Replace the log-distance model with Sionna RT scenes (same Scene/Receiver API); also the
   source of real reflection geometry and delay spread.
2. Joint reflection + calibration-offset model, and a vertical-bias check that does not rely
   on posterior width alone (placement cannot fix the residual 1-2 floor downward bias).
3. Emitters in more than one building (Phase C cross-building correlation).
4. Add GLocFM-style learned scoring and RF-3DGS fields only if they beat this baseline.
   (Both papers report indoor/Wi-Fi results: RF-3DGS ~2 ms render after ~3 min training;
   GLocFM -49.5%/-48.8% mean 3D error on its synthetic and NeRF2 sets. Neither is outdoor.)
