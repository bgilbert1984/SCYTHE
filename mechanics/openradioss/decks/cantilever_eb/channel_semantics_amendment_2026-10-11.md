# Channel semantics amendment, cantilever static runs (2026-10-11)

**Status: documentation only.** This record does not amend the frozen estimator, thresholds, protocols or any
committed result, and it does not change the verification verdict, which remains **NOT_VERIFIED**
(`results_2026-10-08.md`). It adds no digest to, and replaces nothing in, `rerun_sha256sums_2026-10-08.txt`.

## 1. Decision recorded

For the retained cantilever time-history CSVs (coarse `cantilever-rerun-20261007`; medium and fine
`cantilever-mf-20261008`) the `REACX/REACY/REACZ` wall-reaction channels are **cumulative impulse**,

    S(t) = integral from 0 to t of F_reaction(tau) d tau        [N s]

They are not instantaneous force. The earlier "299.75 N" came from reading the channel as force; it was an
interpretation error, not evidence that the solver applied 30% of the load. The mean reaction force over an
interval is

    F_mean[t1,t2] = ( S(t2) - S(t1) ) / ( t2 - t1 )             using the ACTUAL sample times

and a time-average of S itself is not a force. This is what `extract_reaction_trapezoidal_v1(channel_kind="impulse")` computes.

## 2. Evidence classes

| Class | Claim | Basis |
|---|---|---|
| SOURCE_VERIFIED | The engine accumulates reaction force x time step into the reaction array and never resets it | OpenCourant `33e685176cccf0c539a3ce07aa2096985a284e2a` (the commit in the starter and engine banners): `engine/source/output/th/bcs1th.F:145` (`FTHREAC += FTHREAC0*MS*DT12`, `FTHREAC0 = -A`); `engine/source/output/reaction_forces_th.F:65` (`+= IFLAG*MS*A*DT12`); `engine/source/engine/resol.F:6506` (`DT12 = HALF*(DT1+DT2)`); `resol.F:1929,1946` (allocated and zeroed once, no other zeroing in `engine/`); `engine/source/output/th/thnod.F:191` (writer copies the value unchanged) |
| PHYSICS_CORROBORATED | The CSV channel is that impulse, unscaled and with positive sign | Exact timestamps (steps 4.96e-4 to 5.004e-4 s): S = 5000 t^2 on the ramp, S(0.1) ~ 50, then slope 999.95 to 1000.02 with intercept -50.0. Momentum identity Pz(t) = S(t) + integral(F_applied dt) holds to 0.0025 / 0.0011 / 0.0006 N s (coarse / medium / fine); read as force the residual is ~348 N s. Fitted scale k = 0.9999929 / 0.9999965 / 0.9999982. External work from the face displacements and nodal loads matches the `EXTERNAL WORK` column to 0.001%. `IE + KE + HG` equals `W_ext` to ~1e-6 |
| CONVERTER_SOURCE_UNAVAILABLE | `th_to_csv` passes the impulse through without differentiating or rescaling | Its source is not in the recorded tree (`tools/th_to_csv/README.md` points to an external repo). Observed only: reconversion of the retained `T01` files reproduces both CSVs byte-for-byte; adding a `/TH/TITLE` card (run `cantilever-thtitle-20261008`) leaves every header and numeric column identical. Not tied to an upstream commit. **Not elevated to SOURCE_VERIFIED** because the CSV agrees |

The retrospective digests of the `T01`, `.rst`, `.out`, animation and deck-copy files
(`SUPPLEMENTAL_MANIFEST.cantilever-mf-20261008.RETROSPECTIVE_2026-10-11`) were computed on 2026-10-11 and carry the label
`RETROSPECTIVE_DIGEST_2026-10-11`. They do not attest that those files are unchanged since the solver ran.

## 3. Contract for mechanics evidence

1. A reaction channel used as evidence must declare its `channel_kind` (`force` or `impulse`) and the basis.
2. Where the channel is impulse, force is the endpoint difference over the actual time span, never a time-average of S.
3. The choice must rest on evidence about the channel, not on which reading closes a balance.

## 4. What this does not establish (all remain open)

- **Overall benchmark: NOT_VERIFIED.** Channel semantics is one question among several.
- **Dynamic `f1` gate:** not verified.
- **Invocation provenance:** starter, engine and converter commands and environments for the medium and fine runs were not retained
  (`COMMAND_NOT_RETAINED`, `ENVIRONMENT_NOT_RETAINED`). `mechanics/openradioss/run.sh` is documentation and does not describe what ran.
- **Converter source:** unavailable, see section 2.
- **`/DEF_SOLID` warning (ID 100214):** the starter warned in every run. Traced to a legacy-layout field in a card the current parser
  treats as unused; consequence not evidenced. Explained by inference, not closed.
- **`/MAT/ELAST` Poisson's ratio:** the deck writes nu = 0.3 in 10-character fields where the card definition requires 20-character
  fields. The starter echoed nu = 0 (shear modulus E/2) and raised no warning, in all four decks. **Unresolved.** The model the solver
  ran is not the model `analytical_reference.json` and `NOTES.md` describe. Effects other than the shear-deflection term (~0.045% of the
  Euler-Bernoulli deflection) were not estimated.

Detail and source references for the last two items: `SOLVER_INPUT_REVIEW.md` (sha256 `8555e3a2d3b5a5aaa678bbd917e7c988dc4491ca57fd2dc6e9c592d0d57f9e0a`);
channel investigation: `CHANNEL_SEMANTICS_INVESTIGATION.md` (sha256 `34e8ba7bb75f7ca317bb2bc7000e3a59c2a395126ce6fbb7c3f635bfb3cf84e7`).

## 5. Verification ledger

| Item | Status |
|---|---|
| Channel semantics (retained CSVs) | **SEMANTICS_VERIFIED** (source + physics; converter source unavailable) |
| Static force and energy identities | corroborated within the measured residuals |
| Material-card acceptance | **unresolved** (`/DEF_SOLID` warning explained by inference; `/MAT/ELAST` nu = 0 vs 0.3 open) |
| Dynamic experiment (`f1`) | not verified |
| Historical invocation provenance | not retained |
| **Overall benchmark** | **NOT_VERIFIED** |
