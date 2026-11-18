# §5.21 antenna-on observation: third run, at a second site (12426 Mukilteo Speedway, 2026-11-18)

Evidence record. It changes no frozen file, test or behaviour. Everything about the venue, the antenna and the 50 ohm load is the owner's statement and was not verified.
It is the third antenna-on run and the first at this address; the first two (`521_ANTENNA_ON_FIRST_OBSERVATION_RUN_EVIDENCE.md`, `521_ANTENNA_ON_SECOND_OBSERVATION_RUN_EVIDENCE.md`) were at 12502 Mukilteo Speedway.

## What ran
- Run `521-UHF-XTR-E4K-ANTENNA-OBS-20261118-MUK3` by `rf_antenna_on_run.py` (code at main `bd9a4606`, after #498), 2026-11-18 about 10:16:00 to 10:23:33 PST, at 12426 Mukilteo Speedway,
  Mukilteo, WA 98275, antenna connected, gain 29.0 dB, receiver SN 71874552 (E4000), same seed and 192-visit schedule as runs 1 and 2 (schedule digest `d35d706f...3636`).
- Authorization chain: frozen declaration sha256 `94b86ebe...2d795c`; run declaration digest `a0224c647f884e534d766f79b664bac2c1ac9898f8607fd4ad717ada0e6bfd69`;
  launch authorization file recorded the operator's own messages "Antenna-on at 12426 Mukilteo Speedway" and "Run declaration digest a0224c64...bfd69" verbatim. No time window.
- Evidence class `ANTENNA_ON_OBSERVATION_ONLY`: no catalogue entries, classifications or promotions.

## Result
| measure | run 1 (12502, 11-17) | run 2 (12502, 11-18) | run 3 (12426, 11-18) |
|---|---|---|---|
| visits acquired / refused | 192 / 0 | 192 / 0 | 192 / 0 |
| visits passing the frozen visit gate | 113 / 192 | 192 / 192 | 60 / 192 |
| tunings scored | 10 / 64 | 64 / 64 | 6 / 64 (26, 32, 35, 40, 42, 49) |
| refusals | 45 not detected, 34 below min excess | none | 91 `CANARY_NOT_DETECTED`, 41 `CANARY_BELOW_MIN_EXCESS` (no clipping refusal; clipping 0) |
| sides detected per visit (2 / 1 / 0) | not tabulated | 192 / 0 / 0 | 101 / 36 / 55 |
| weaker-side excess where both detected (min / median / max, dB) | 10.0 / 12.0 / 15.1 | 14.5 / 15.9 / 17.4 | 10.1 / 11.2 / 13.8 (101 visits) |
| sample std, median (LSB) | 1.27 first half, 1.57 second | 0.82 first half, 0.80 second | 1.71 (min 1.17, max 5.8) |
| candidates per visit, median | 524 / 419 | 1,052 / 1,076 | 864 |

- Weakest passing visit 11.0 dB; median of passing visits 11.8 dB. Tunings by visits passed: 3 of 3: 6; 2: 13; 1: 16; 0: 29.
- Lower band (LO below 435 MHz) 21/100 visits pass and upper band 39/92.
- Time structure: pass rate by block of 16 visits is 4, 2, 5, 1, 8, 5, 7, 7, 9, 1, 4, 7 of 16. The maximum pass-rate-drop search puts a changepoint at visit 142 (0.34 before, 0.24 after), but with blocks swinging between 1 and 9 of 16 that is not
  a step like run 1's (95% to 23% at visit 96); excess (10.1-13.8 dB), sample std and candidates are the same in the first and last 96 visits. The failure is persistent through the run, not a break within it.
- Compared with run 1, where the pass rate was high before the break, this run's weaker-side excess sits at 11.2 dB median, at the 11.0 dB floor, for the whole run; most visits fall on either side of it, which is the gate's margin assumption playing out at a venue that is always near the floor.

## Receiver check (load fitted, at this site, before the run)
Before the run, with the 50 ohm load fitted (owner statement) at 12426 Mukilteo Speedway, the 8 tunings of the canary repeats (0, 8, ... 56, four retune positions each, gain 29.0 dB, Device 035) were captured once, 09:50-09:52 PST, 24 minutes before the run started:
32/32 pass the gate, weaker-side excess 15.0 / 16.6 / 17.6 dB, sample std median 0.77 LSB (0.76-0.85), no clipping, median 2,299 candidates. This matches the two load-fitted sets at 12502 (32/32, 15.5-17.5 dB, std 0.74-0.77).
The receiver was therefore healthy at this site shortly before the run. The antenna-on run's weaker-side excess is 5.4 dB lower at the median and its sample std more than twice as high, which places the change on the antenna side, as in the earlier antenna-on failures at Hwy 99 (11-15), Lynnwood (11-16)
and run 1. It is a check before the run and does not show the receiver's state during it. The source of the antenna-side change is not identified.

## What this does and does not show
- Same gate, scope, receiver, schedule and day as run 2, a different address a short distance away: 192/192 and 64/64 at 12502, 60/192 and 6/64 at 12426. Run 2's clean result is therefore not a property of the receiver or of the day.
- Why 12426 is so much worse is not identified. This record has no antenna-side measurement, no reference channel and no second visit to 12426 at another time, so site, antenna position or cabling, local interference, and time of day all remain open.
  Three runs at two addresses give no rate.
- The gate and the every-visit tuning gate did their job: they refused or left unscored what the canary could not vouch for, and the run finished with an honest observation document. Nothing about the freeze needs to change on this evidence.
  The yield dependence on conditions at the venue is the accepted assumption on margin (`521_ANTENNA_ON_FREEZE_RECORD.md`, assumption 1).

## Evidence
- Run directory (locked, 199-file manifest): archive `antenna_on_obs_20261118_mukilteo_12426`, tar.zst sha256 `04ef2c473c701396af09636a221485df5469679fe7b49993cff8f522d48938b6`, tar sha256 `4982642e661b83d169dc9bcfa4a4eb34943419729fbc23caa32e64436286564e`,
  670,972,005 bytes compressed, verified on dedirock (zst hash, `zstd -t` and the decompressed tar hash all match the local files).
- Load-fitted set: archive `slope_muk12426_load_20261118`, tar.zst sha256 `d25734d197391b547716133e36ab3bc1fed289828ab8944f58aad19a8b887b0d`, tar sha256 `2fc6580e099e1ddd043b607daafee94407ce2c4e560db3e7f69a73330f9671c5`, 69,009,772 bytes compressed,
  verified on dedirock (zst and tar hashes match). Its `capture_log.json` `purpose` text was garbled by a label edit before the capture; the file is kept as captured and says feasibility only, load fitted, and the venue.
- In this repo (`scripts/s521_antenna_on_run/`): `observation_run_20261118_12426_visits.json` (per-visit gate results, from `analyse_observation_run.py <run dir>`).
