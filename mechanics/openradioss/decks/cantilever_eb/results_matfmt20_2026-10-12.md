# Cantilever corrected-family (nu = 0.3) results — 2026-10-12

This is a NEW record. It does not amend, replace or reinterpret `results_2026-10-08.md`,
the historical manifests, or the original dynamic verdict, none of which are modified
by this change. Historical results were produced with nu = 0 (see "Why a corrected
family exists") and keep their verdicts as recorded.

Digest authority in this document: every digest below was taken on 2026-10-12 from
files written by the sessions that ran the experiments (`SHA256SUMS.contemporaneous`
in each experiment directory). They are NEW_BASELINE digests, not historical ones.
Historical digests are cited only as the identities they already had.

## Scoped classification

| Evidence object | Status |
|---|---|
| Historical nu = 0 static and dynamic results | Historical verdicts preserved |
| Corrected nu = 0.3 static family | STATIC_GATES_PASS |
| Corrected nu = 0.3 dynamic experiment | DYNAMIC_F1_PASS_ACCEPTED_V3 |
| Corrected cantilever numerical benchmark | VERIFIED_AGAINST_ACCEPTED_GATES |
| Independent prospective protocol validation | NOT_ESTABLISHED |
| Unqualified, end-to-end solver verification | NOT_VERIFIED |

`VERIFIED_AGAINST_ACCEPTED_GATES` is a bounded numerical result: the corrected family
satisfied the frozen static criteria and the accepted dynamic criterion, with no
tolerance adjusted to obtain a pass. It does not claim that the OpenCourant solver, the
time-history converter or the SCYTHE mechanics infrastructure is independently verified,
and it does not promote the historical benchmark. The historical record keeps its
NOT_VERIFIED classification.

## Why a corrected family exists

The generator wrote `/MAT/ELAST` data in 10-character fields; the card is defined with
two 20-character fields per line (`%20lg%20lg`). The starter read E correctly and
Poisson's ratio as 0 (G = E/2 = 1.0E+11 instead of 7.6923E+10) with no warning, in every
historical deck. Controlled medium-mesh experiment: the historical encoding reproduced
the historical CSV byte-for-byte (`9ade8dd1…e5187a9`), the 20-character encoding made the
starter report nu = 0.3, G = 0.7692E+11. Generator change and tests: PR #149
(`--material-layout fields20`; the default `legacy10` is retained for historical
reconstruction only and must not be used for new nu = 0.3 experiments).

## Corrected material state confirmed by the starter

From each experiment's `gate2_record.json` (starter output, not the deck text):
rho 7850, E 2.0E+11, nu 0.3, G 0.7692E+11 (expected 7.6923E+10, the starter prints four
digits), solid formulation 1, small strain 4, corotational 1, hourglass viscosity 0.1.
One starter warning (ID 100214, `/DEF_SOLID`, see limitations), 0 errors. For the
dynamic run `all_match` is true.

## Frozen acceptance criteria and results

Static criteria are those frozen 2026-10-05; the dynamic criterion is protocol v2 as
amended (see "Protocol revision"). Static results use the frozen estimator
(`reaction_extractor.py`, channel kind `impulse`, per `estimator_amendment_2026-10-08.json`
and `channel_semantics_amendment_2026-10-11.md`).

| Gate | Frozen criterion | Corrected result | Verdict |
|---|---|---|---|
| G1 | Fine-mesh deflection error <= 2% | 1.421% | PASS |
| G2 | Strictly decreasing mesh error | 32.887% > 6.469% > 1.421% | PASS |
| G3 | Reaction within +-1% on every mesh | maximum 0.330% (coarse) | PASS |
| Dynamic f1 | Relative error vs 40.769 Hz within +-2% | 0.778% (f1 = 40.45200 Hz) | PASS |
| Dynamic adequacy | Seven checks | 7 of 7 | PASS |

### Static detail (reference analytical tip deflection 3.2 mm, load 1000 N)

| Mesh | Cycles | u (mm) | e_defl | Rz (N) | e_reac |
|---|---|---|---|---|---|
| coarse | 136,825 | -4.252371 | 32.887% | 996.6959 | 0.330% |
| medium | 273,636 | -3.406993 | 6.469% | 999.8852 | 0.011% |
| fine | 547,265 | -3.245476 | 1.421% | 999.8878 | 0.011% |

Observed order from u differences 2.39 (recorded, not gated). Each run ended with normal
termination.

Historical nu = 0 values for comparison (from `results_2026-10-08.md`, unchanged):
e_defl 32.83% / 6.74% / 1.71%, Rz 996.445 / 999.889 / 999.914 N, order 2.38. The
two families are not mixed in any gate evaluation.

### Dynamic detail (fine mesh)

Engine normal termination, 2,955,184 cycles, end time 2.7 s, exit 0, 1539.8 s wall time
(2026-10-12T00:37:33+05:30 to 01:03:13+05:30), `OMP_NUM_THREADS=1`.

| Adequacy check | Value | Threshold | Verdict |
|---|---|---|---|
| Sampling uniformity | max deviation 1 us (4001 samples, fs 2000 Hz) | <= 2.0602 us (inclusive) | PASS |
| Release A impulse | -1.99986 N s | within 0.1 of -2.0 | PASS |
| Release B impulse | 0.00100 N s | within 0.1 of 0 | PASS |
| Frequency resolution | df_raw 0.49988 Hz, window 2.0000004 s | <= 0.5 Hz, >= 2.0 s | PASS |
| Signal strength | amplitude ratio 314.5; block10/block1 0.9727 | >= 100; >= 0.01 | PASS |
| Competing modes | prominence 37.67 (competing peak 41.615 Hz) | >= 10 | PASS |
| Stability across windows | halves 40.45197 / 40.45197 Hz, rel diff 6.1e-8 | <= 0.005 | PASS |

Frozen estimate f1 = 40.452004 Hz; reference 40.769 Hz; relative error 0.7775%.
Response check (mean DZ over 0.3 to 0.5 s = -3.2455 mm vs frozen static fine -3.254806 mm,
within 5%; 170 zero crossings after t = 0.6 s): satisfied.

## Protocol revision and the post-observation history of Amendment 2

Protocol: `dynamic_protocol_2026-10-08-v2.json` (git 12857053, frozen 2026-10-09 before
any run) + amendment 1 (`097c6c79`) + amendment 2, marked FINAL (`7ba80949`, committed
2026-10-10).

- The historical nu = 0 run (2026-10-09) was evaluated under the strict v2 sampling rule.
  Its maximum sampling deviation was exactly 1 us, and the rule returned **INCONCLUSIVE**.
  That verdict is preserved.
- Amendment 2 replaced the sampling rule after that observation. A v3 reanalysis of the
  historical run (labelled REANALYSIS, same data) gave f1 = 40.37677 Hz (0.962% below the
  reference) and all checks pass. It is not a new measurement.
- The corrected-family run shows the same exactly-1-us sampling deviation. Under the
  strict v2 rule it would also have been INCONCLUSIVE; its PASS therefore rests on
  Amendment 2.
- The corrected run is the first NEW measurement evaluated under the final rules. It is
  **not a pre-registered test** of Amendment 2, and independent prospective protocol
  validation is NOT_ESTABLISHED.
- Analysis control: the unmodified retained `dynamic_f1_analysis_v3.py` rerun on the
  historical CSV reproduced the recorded v3 result (66 fields, 0 differences). The
  analysis applied to the corrected run is that script with exactly one line changed, the
  CSV path.

## Artifacts by experiment

Experiment directories (host dedirock-919498765, outside the repository) and the copies
under `~/ci_audit_slice1/`. Each carries `SHA256SUMS.contemporaneous` (contemporaneous
manifest, not historical), executable digests, per-stage invocation records, and the
starter/engine output.

| Experiment | Directory | Copy |
|---|---|---|
| medium, arms A (historical encoding) and B (corrected) | `/var/tmp/scythe-sweep/cantilever-matfmt-20261012` | `matfmt_experiment/` |
| coarse B, fine B | `/var/tmp/scythe-sweep/cantilever-matfmt-cf-20261012` | `matfmt_coarsefine_experiment/` |
| dynamic B (fine mesh) | `/var/tmp/scythe-sweep/cantilever-dynamic-matfmt-20261012` | `matfmt_dynamic_experiment/` (95 files, 71 MB, verified) |

### Input decks (all committed beside this record; the dynamic pair was added after this record was first written)

| Deck | SHA-256 |
|---|---|
| `cantilever_coarse_matfmt20_0000.rad` | `8bf74526591d23b2da2d4f4235c7f21871c1c3ad327c185550998176c36a5143` |
| `cantilever_medium_matfmt20_0000.rad` | `508c28bf3b0f42be8d8609a71b799c8a87802e6beb87dba19948f0cd4788088c` |
| `cantilever_fine_matfmt20_0000.rad` | `b07b181c255d7e4f412b58c1606afccf8eef5d349c1b483e8a9105d0ac370ada` |
| `cantilever_{coarse,medium,fine}_matfmt20_0001.rad` (engine; identical to historical `_0001`) | `94894b74ecfb825cdb41c32f2f2cc803d440b121914d1cf3312c270134c49402` |
| dynamic starter deck `cantilever_dynamic_matfmt20_0000.rad` (corrected) | `042a6f2089ef5a3a2ce8eb859a2ead085306c1cccde7fa8d332219f56ccbbb1d` |
| dynamic engine deck `cantilever_dynamic_matfmt20_0001.rad` (identical to historical `cantilever_dynamic_0001.rad`) | `2bcee7d089f858200dba7dc5563c8203d7f84bb9dc4f510df0167fd9ef691cc4` |

The dynamic deck derives from the retained historical dynamic deck (`e4f8535c…0c39`, the
bytes the 2026-10-09 solver read) with only the two `/MAT/ELAST` data lines re-encoded
(`gate1_record.json`, `gate1_diff.txt`). It is committed as
`cantilever_dynamic_matfmt20_000{0,1}.rad` and is not generator output (see
`deck_family_matfmt20_identity.json`, `dynamic_decks_provenance`).

### Executables (verified before and after every run)

| Executable | SHA-256 |
|---|---|
| `starter_linux64_gf` | `8b7709aef7b78ffb9a7387653603345578c193984365870a1c0cbba0bb9ba13b` |
| `engine_linux64_gf` | `06cee40f73c3d39648c712e24142ebb58b1e7ab0e248ee6e8f2d399e5f92ec9e` |
| `th_to_csv_linux64_gf` | `704d647331ae4befd329d18b991d644f2af930abd117c5d204c23925f75fc8db` |

Environment: OpenCourant 33e685176cccf0c539a3ce07aa2096985a284e2a; `RAD_CFG_PATH` =
`~/OpenCourant/run/OpenCourant/hm_cfg_files`; `OMP_NUM_THREADS=1`; `OMP_STACKSIZE=400m`;
unlimited stack.

### Raw time histories and converted CSV (SHA-256)

| Run | Raw history (`…T01`) | Converted CSV (`…T01.csv`) |
|---|---|---|
| medium A (historical encoding, control) | `7bce61cac3654e938fa90bfc37e6e8866630aa321ef8258bdd371c5e9d7e8738` | `9ade8dd1ec9173dfbf49a699e545fc511b4001e0b155b9142152cd533e5187a9` |
| medium B | `045d9279e981f3c571ecac0f406dad3b4ecd7e1a209e230b62c6efa023efc660` | `be29fbeeecfc83dec8b53263078e6c5e78a5bf86ea772b8112fc66c2e9161cca` |
| coarse B | `ccd34f1812a14fd0f874df560ac7145f11cc6209f8982258ecfca184df6e8b96` | `d6e25b130c6012a03c66c06fcc9dda0d1014b569256b549e06b16f7247c951bf` |
| fine B | `c80f436c7f4cf7e5c7585fc4cc32ed958ba43719093f23e952c0dd6f80783fff` | `ca3cadc21252e52d88a36918cf257a6c1d5dcd6f24dc3e9532fa9cce3c6b695a` |
| dynamic B | `e148d9a3f3967b4cf58a389bce69ef0599372a081cfcfb09d27f7b948cb01139` | `8f4c48e8aaf5e590f2347272a94abbe44f86cb232c94a54c22a5d1c68cf3e517` |

The dynamic converted CSV is 37,304,633 bytes. The full displacement history extracted
from it is `B/analysis/nu03_dynamic_timeseries.csv`
(`211da6256933f4475722deeb90740ea46b3c1e3406920fdb78aff22e89b5468a`).

### Analysis code and result records (SHA-256)

| File | SHA-256 |
|---|---|
| `reaction_extractor.py` (this directory; frozen estimator) | `c5c1fc1f0080b3fbc006b106d46c4885d438b4ea42dbd4acab8f5f168d1b3c3d` |
| `analytical_reference.json` | `4cde0c1f2a591117b94b0b7795ec88fa60e9328611b0a1c130a6f20828bd2005` |
| `tools/analyze_family.py` (coarse/medium/fine) | `fed5d820152fa433f575bd2d273fdf33418f890513e093a7a5182879f2e715f7` |
| `tools/analyze_arms.py` (medium A/B) | `17025cb1725bbb98bd12f327d0144e3e2b80f884a3b12ef2758d2faa22db8ec1` |
| retained `dynamic_f1_analysis_v3.py` (unmodified) | `0c26d1bdeeb6cbc6cfce98a892db838f4cffd3237e0024944bf65f2168fd51a4` |
| `tools/dynamic_f1_analysis_v3_corrected_run.py` (one line changed) | `d7bda05a66f5f6df4dbef3316e1f7efb8a6ff99b5483a1b1edf0f3d678d8ebf3` |
| `family_corrected_nu03.json` (static gates, corrected) | `9595737f60d8afb0ad9cc1f391ad02131f300292d591254e5c463781e2e5bb50` |
| `family_historical_nu0.json` (static gates, historical, same analysis) | `fa15ebbaae51f5573ff9a649af694d3db5832fd246bf68c24377010ec3445408` |
| `analysis_v3_corrected_results.json` (dynamic verdict record) | `df90d0ec727f538fa5aaca24d526799eb1e714a8a05695164b992ea5a129b3cf` |
| `B/analysis/nu03_dynamic_diagnostics.json` (supplementary) | `5a8ea81e83b8febfcd91b8a13796cc2a1afbe5e4507ba61d777df244b82b1127` |
| `B/analysis/full_run_identities.json` (supplementary) | `e441a0d5bbc563bb6d7aa8fd0f6a04b0d3c1a819dc2e62f2e7bc1480a16b0207` |
| `deck_family_matfmt20_identity.json` (this directory) | `1b68ce0c7e67a905c724a6c6e6ea9828ed0461fde2b4b3c474c36e45b56c6e1a` |
| `dynamic_protocol_2026-10-08-v2.json` | `b319ca666acf88bb5620c249c1102b9d4832dc7c16bbe7903a3f22f89c8bc999` |
| `dynamic_protocol_v2_amendment_1.json` | `76f36201695561e7695f6ed9fb60cd568a3c89486932e35d939922f5eb6030c0` |
| `dynamic_protocol_v2_amendment_2.json` | `8744b2cf2b663926b82d289fe16c73db0b73eb7ed996e16cd1250e25b3cbbddf` |

## Observations that are recorded, not gated

Supplementary diagnostics corroborate the dynamic result; they are not part of the
frozen criterion and are not retroactively made gates.

- Three independent frequency estimators agree within 0.0017 Hz: frozen 40.45200,
  damped-sinusoid least squares 40.45200, zero-crossing counting 40.45033.
- Window sensitivity: the windows [0.6,2.6], [0.8,2.6], [1.0,2.6], [1.2,2.6], [0.6,1.6],
  [1.6,2.6] and [2.1,2.6] s all give f1 within 0.00003 Hz of 40.45200; only the short
  [0.6,1.1] s window differs (40.468 Hz), as its coarse resolution implies.
- Whole-run identities: momentum residual 0.0012 N s against |Pz| up to 6.63 N s,
  scale fit 0.9999999, energy balance 6.2e-7 of peak work. The independent work estimate
  differs by 0.0042 J, limited by two samples across the 1 ms release; this is the same
  in both families.
- Effect of correcting nu on the fine dynamic frequency: 40.37677 Hz (historical,
  v3 reanalysis) to 40.45200 Hz (+0.0752 Hz, +0.19%). Fine static stiffening is +0.29%,
  which would suggest about +0.14% in frequency at constant mass. Same sign and rough
  size; this is not a test.

### Hourglass energy share (observation, not interpreted)

The hourglass energy relative to peak external work in the dynamic run rose from 4.43%
(historical nu = 0) to 6.99% (corrected nu = 0.3). The whole-run energy balance and
momentum identities hold to the figures above in both families, and the f1 gate passes.
This record does not treat the 7.0% share as a failed energy-balance check and does not
interpret it further. Hourglass stabilization influences the numerical behaviour of
reduced-integration solids, so a stabilization-sensitivity study is the natural follow-up
before broader claims about predictive accuracy; it has not been run.

For context, the static end-of-run ratio (HG energy / external work, as stored in the
family records) is 0.396% / 0.0119% / 0.0016% (coarse/medium/fine) for the corrected
family against 0.287% / 0.0075% / 0.00077% for the historical one. The static and
dynamic figures use different normalisations and times and are not directly comparable.

### Coarse-versus-fine Poisson-sensitivity reversal (unexplained)

Correcting nu from 0 to 0.3 changed the tip deflection by -0.0019 mm on the coarse mesh
(more flexible, close to the roughly -0.0014 mm shear-compliance estimate) but by
+0.0086 mm on medium (+0.25% stiffer) and +0.0093 mm on fine (+0.29% stiffer). The sign
reversal between coarse and the finer meshes is **unexplained**. Candidate hypotheses
(root-resolution and clamping effects, reduced integration with hourglass control) are
untested. It is not an acceptance failure: all three static gates pass for both families.

## Limitations on scope and provenance (kept visible)

- **Converter source unavailable.** The `th_to_csv` source is not in the OpenCourant
  clone. Channel semantics (REACX/Y/Z are cumulative impulse, N s) are verified from
  solver source plus momentum, work and energy identities, scoped to the solver outputs;
  the converter's own behaviour is established by its observed output and executable
  digest, not by source review.
- **`/DEF_SOLID` warning 100214.** The generator writes a legacy-layout card; the starter
  warns and ignores it. All fields are zero, so the result equals having no card.
  Classification EXPLAINED_INFERRED: the closed hm_reader trigger rule, the `Icpre` echo
  (0 versus 3) and the default `Imas` are not traced to source. The warning appears in
  historical and corrected runs alike.
- **Historical invocation provenance.** The commands and environment of the historical
  runs were not retained (COMMAND_NOT_RETAINED, ENVIRONMENT_NOT_RETAINED). The corrected
  experiments record both, and reproduced a historical CSV byte-for-byte with the
  historical encoding, which ties the two families together for the medium mesh only.
- **Dynamic Arm A not rerun.** The historical nu = 0 dynamic run and CSV exist; the
  historical analysis was reproduced exactly instead.
- **Reference.** The dynamic reference is Euler-Bernoulli; a systematic offset from the 3D
  model is within what the protocol already accepts.
- **Manifests.** All manifests cited here are contemporaneous. Retrospective and
  contemporaneous digests carry less authority than historical ones.
- **Not established:** independent prospective validation of the dynamic protocol,
  stabilization sensitivity, and unqualified end-to-end solver verification.
