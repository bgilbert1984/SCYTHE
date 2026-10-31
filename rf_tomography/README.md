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
`correlation/` (empty: Phase C).

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

## Next

1. Replace the log-distance model with Sionna RT scenes (same Scene/Receiver API).
2. Add NLOS classification error and non-Gaussian multipath to the truth generator.
3. Receiver-placement optimiser: maximise top-3 accuracy over candidate layouts.
4. Add GLocFM-style learned scoring and RF-3DGS fields only if they beat this baseline.
   (Both papers report indoor/Wi-Fi results: RF-3DGS ~2 ms render after ~3 min training;
   GLocFM -49.5%/-48.8% mean 3D error on its synthetic and NeRF2 sets. Neither is outdoor.)
