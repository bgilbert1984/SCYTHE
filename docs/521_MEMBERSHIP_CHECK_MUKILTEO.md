# §5.21 membership check: do the same candidates survive? (Mukilteo, 2026-11-18)

Evidence record and a **negative result**. It changes no frozen file, test or behaviour, and touches no recon entity. It follows `521_CANDIDATE_COUNT_GAP_MARGIN_TEST.md`, which compared counts only.

## Three quantities, kept apart
| Quantity | Meaning | Measured here |
|---|---|---|
| Detection density | how many candidates pass the threshold | counts only (medians below) |
| Membership stability | share of reference candidates with a one-to-one counterpart in the other capture of the same visit | `paired` |
| Tuning discrimination | `delta` = paired survival minus the same survival against the 7 OTHER tunings of the other capture (same retune) | `delta` with 95% interval |

They belong in separate telemetry fields. A count says nothing about membership, and an overlap that a different-tuning control reproduces is fixed baseband structure shared by every tuning, not information about the tuning.

## Inputs and method (fixed before the v2 results were seen)
- Sets (all 32 visits = 8 tunings x 4 retunes, gain 29.0 dB, E4000): load-fitted 11-17 `slope_mukilteo_antoff_20261117` (MANIFEST sha256 `257dd82a...`), antenna-on 11-16 `slope_mukilteo_20261116` (`c69f2c1c...`), antenna-on 11-17 `slope_mukilteo2_20261117` (`1d9b26c8...`). Full manifest hashes, the script hash, seed and counts are in each JSON.
- Candidates: `detect_candidates` at the 10 dB margin, unchanged; offsets with |baseband| < 12 kHz excluded (the near-DC cloud); frequency tolerance 60 Hz (the canary match tolerance) and 500 Hz.
- **One-to-one matching** (maximum matching on sorted offsets), so a dense cluster is not counted several times. Survival strata use the reference candidate's excess: (10,12], (12,14], (14,17], (17,inf) dB.
- Uncertainty: 95% percentile interval of a bootstrap over the 8 tuning blocks (5000 resamples, seed 7535); candidates and visits inside a tuning are correlated and are not resampled individually.
- Scripts: `scripts/s521_canary_threshold/membership_test_v2.py` (this analysis), outputs `membership_v2_*.json` and `membership_v2_output.txt`. `membership_test.py` and `membership_mukilteo_tol{60,500}.json` are the first version, kept as the record of what was run first; it matched many-to-one and had no uncertainty, so its survival figures (e.g. 75% above 14 dB, "everything above 17 dB") are superseded by the table below.

## Results (60 Hz; 500 Hz is in the JSON and agrees in pattern)
Counts outside the cloud, median per visit: load-fitted 499, antenna-on 11-16 64, antenna-on 11-17 49.5. The 2x total-count gap is mostly the cloud; outside it the gap is about 8x, and a 12 dB margin leaves load-fitted at about 40, below antenna-on.

Reference = load-fitted 11-17; reference candidates n = 13,767 / 1,025 / 89 / 113 in the four strata (all visits):

| Reference excess | Other = antenna-on 11-16 (paired / control / delta) | Other = antenna-on 11-17, same day (paired / control / delta) |
|---|---|---|
| 10-12 dB | 0.078 / 0.080 / -0.002 [-0.022, 0.019] | 0.033 / 0.034 / -0.001 [-0.025, 0.033] |
| 12-14 dB | 0.202 / 0.220 / -0.018 [-0.068, 0.032] | 0.122 / 0.096 / 0.026 [-0.040, 0.115] |
| 14-17 dB | 0.752 / 0.361 / **0.391 [0.215, 0.548]** | 0.783 / 0.359 / **0.424 [0.219, 0.596]** |
| above 17 dB | 0.893 / 0.341 / **0.552 [0.476, 0.643]** | 0.930 / 0.343 / **0.587 [0.426, 0.738]** |

Same condition, different days (antenna-on 11-16 as reference, 11-17 as other; n = 1,691 / 251 / 169 / 117): overall delta 0.122 [0.057, 0.186]; 10-12 dB 0.045 [-0.016, 0.116]; 12-14 dB 0.302 [0.173, 0.444]; 14-17 dB 0.263 [0.181, 0.340]; above 17 dB 0.694 [0.474, 0.878].

## What it shows
1. **Strong candidates (above 14 dB) survive and carry tuning-specific information**: delta 0.39-0.59 with intervals excluding 0, in the cross-day and the same-day pairing. "Everything above 17 dB survives" was wrong: it is 89-93% (interval up to 0.99), on 113 candidates.
2. **Weak candidates (10-14 dB load-fitted) do not survive better than the control**: delta spans 0 in both pairings. Most of what a lowered margin adds is not the same population antenna-on keeps. This is why the count match at 12 dB does not extend to membership.
3. **The overlap is mostly fixed baseband structure.** Over all strata the paired and control survival are equal (delta 0.005-0.008, intervals span 0). The earlier "69% vs 61%" has no tuning-specific part beyond what the strong strata contribute.
4. Antenna-on against itself across days keeps more (delta 0.12 overall, 0.30 at 12-14 dB) than load-fitted against antenna-on: the antenna-on candidate population outside the cloud is partly stable day to day.

## Limits
- One venue; load-fitted and antenna-on 11-16 are different days. The same-day pairing (11-17) agrees, so the day gap is not what drives the result.
- The control is the other 7 tunings at the same retune. It removes shared structure at the same offsets, but it may still contain other systematic effects, so delta is an excess over this control, not a measured antenna contribution.
- Strata above 14 dB hold only 89 and 113 reference candidates in total, so those estimates rest on few candidates.
- 60 Hz and 500 Hz were fixed beforehand; no other tolerance was tried.
- What the surviving candidates are (spurs, intermodulation, ambient signals) is not tested.

## Consequence for downstream use
A scored tuning may drive revisit priority, uncertainty reduction and evidence collection. These results do not allow it to promote candidates into recon entities, to be read as an emitter identity, or to authorise a tuning. The detector's margin should not be lowered to "recover" antenna-on membership. A second antenna-on observation run should keep the same thresholds, tolerances, receiver configuration and exclusion region, record gain, time, environment and antenna placement, and interleave load-fitted and antenna-on measurements on one day. That run needs its own launch authorization.
