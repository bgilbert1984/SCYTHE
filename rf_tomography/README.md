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
`placement/` receiver-placement optimiser · `measurements/impairments.py` NLOS/multipath truth generator · `correlation/` (empty: Phase C).

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

## Next

0. Make `identifiability` and `placement` read their sensing assumptions from one
   calibrated config once real receiver data exist.
1. Replace the log-distance model with Sionna RT scenes (same Scene/Receiver API).
2. Robust solver (the obvious next test): outlier-mixture or Student-t likelihood plus NLOS
   rejection, evaluated on `benchmarks/impairments.py`; success = cov90 back near 0.90.
   Then re-run placement under the robust solver, since optimal layouts will likely change.
3. Add GLocFM-style learned scoring and RF-3DGS fields only if they beat this baseline.
   (Both papers report indoor/Wi-Fi results: RF-3DGS ~2 ms render after ~3 min training;
   GLocFM -49.5%/-48.8% mean 3D error on its synthetic and NeRF2 sets. Neither is outdoor.)
