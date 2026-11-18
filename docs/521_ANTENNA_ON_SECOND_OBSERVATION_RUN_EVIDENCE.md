# §5.21 antenna-on observation: second run under the frozen scope (2026-11-18)

Evidence record. It changes no frozen file, test or behaviour. Everything about the venue, the antenna and the 50 ohm load is the owner's statement and was not verified.
It is the second run of two; it is described next to the first (`521_ANTENNA_ON_FIRST_OBSERVATION_RUN_EVIDENCE.md`) as two observations, not as a trend.

## What ran
- Run `521-UHF-XTR-E4K-ANTENNA-OBS-20261118-MUK2` by `rf_antenna_on_run.py` (code at main `2f752bd7`, after #497), 2026-11-18 about 06:46:50 to 06:54:22 PST, at 12502 Mukilteo Speedway,
  Mukilteo, WA 98275, antenna connected, gain 29.0 dB, receiver SN 71874552 (E4000), same seed and 192-visit schedule as run 1 (schedule digest `d35d706f...3636`).
- Authorization chain: frozen declaration sha256 `94b86ebe...2d795c`; run declaration digest `2413a6ed620d3a07dbda4a94c45cb8448314d8d1ab7e061c6c57a567efc45408`;
  launch authorization file sha256 `8c3412b3ac15af03e56e2863c3b8cec02b5aa470f44345f92b4dccf0423b243e`, status AUTHORIZED on the operator's own confirmation naming that digest (recorded verbatim in the file). No time window.
- Evidence class `ANTENNA_ON_OBSERVATION_ONLY`: no catalogue entries, classifications or promotions.

## Result
| measure | run 1 (11-17) | run 2 (11-18) |
|---|---|---|
| visits acquired / refused | 192 / 0 | 192 / 0 |
| visits passing the frozen visit gate | 113 / 192 | 192 / 192 |
| tunings scored | 10 / 64 | 64 / 64 |
| refusals | 45 `CANARY_NOT_DETECTED`, 34 `CANARY_BELOW_MIN_EXCESS` | none |
| weaker-side excess, first 96 visits (min / median / max, dB) | 10.0 / 12.4 / 15.1 (95 both detected) | 14.5 / 15.9 / 17.1 (96) |
| weaker-side excess, last 96 visits | median 10.8 (52 both detected) | 14.8 / 16.0 / 17.4 (96) |
| sample std, first / last 96 (median, LSB) | 1.27 / 1.57 | 0.82 / 0.80 (min 0.74, max 1.17 in both) |
| candidates per visit, first / last 96 (median) | 524 / 419 | 1,052 / 1,076 |
| clipping | at most 0.000012% | 0 refusals; per-visit values in the visits file |

Pass rate by block of 16 visits is 16/16 in all twelve blocks. The max-pass-rate-drop changepoint search used on run 1 finds nothing (1.0 before and after); excess and sample std are the same in both halves.
Lower and upper bands (LO below / at or above 435 MHz) both pass everything: 51/51 and 45/45 early, 49/49 and 47/47 late. The weakest visit is 14.5 dB, 3.5 dB above the 11.0 dB floor.

## Receiver check (load fitted, the same morning)
After the run the 50 ohm load was fitted (owner statement) and the same 8 tunings as the run 1 check (0, 8, ... 56, four retune positions each, gain 29.0 dB, Device 034) were captured once, about 07:04-07:06 PST, roughly 10 minutes after the run ended:
32/32 pass the gate, weaker-side excess 15.5 / 16.5 / 17.5 dB, sample std median 0.77 LSB (0.76-0.85), no clipping, median 2,316 candidates. The run 1 load-fitted set was 32/32, excess 15.6 / 16.8 / 17.5 dB, std median 0.74, 2,008 candidates.
The receiver was healthy on both days, and the antenna-on weaker-side excess in run 2 (median about 16 dB) is close to the load-fitted value (16.5 dB), so in run 2 the antenna side did not lower the canary excess measurably.
The antenna side still has fewer candidates per visit than the load (about 1,060 against 2,316), as in run 1 (about 470 against 2,008). The check shows the receiver's state afterwards, not during the run.

## What this does and does not show
- The same gate, scope, receiver, venue and schedule gave 113/192 visits and 10/64 tunings on 11-17 and 192/192 and 64/64 on 11-18. Run 1's failures were therefore not a fixed property of the site, the gate or the receiver.
- Why the two runs differ is not identified. They were taken on different days, and nothing recorded here (no antenna-side measurement, no external reference) separates antenna-side conditions, ambient RF or something else.
  Run 1's break was inside the run; run 2 had none. Two runs do not give a rate, and a clean run does not show the first run's failure cannot recur.
- Both runs support the accepted assumption on margin (`521_ANTENNA_ON_FREEZE_RECORD.md`, assumption 1): scored yield from a run depends on conditions at the venue, and the 11.0 dB floor sits within reach of the conditions seen on 11-17.
  Nothing about the freeze needs to change on this evidence.
- No load-fitted capture was taken before either run, so the receiver is not separately cleared during the runs.

## Evidence
- Run directory (locked, 199-file manifest): archive `antenna_on_obs_20261118_mukilteo`, tar.zst sha256 `bf60f9c41013cac6b471b4e56bdd9b384d4cd513d955591946c68c2bf22c6d41`, tar sha256 `443a2902daef203b3ad925b6566e3d9e008b152bf5f64c3020ff601d4dc19764`,
  1,610,886,455 bytes raw, 430,389,385 bytes compressed, verified on dedirock (see the PR for the far-side check).
- Load-fitted set: archive `slope_mukilteo_antoff_20261118`, tar.zst sha256 `38bc38238cea801b12803cc10eafeac1006b8d9d606e46a9eff57fb6449fe362`, tar sha256 `f086e4e4b6e63ba7d4ddf8e72bf35176536d8f6aa3340d379375b2d9658ea120`,
  268,470,731 bytes raw, 69,008,970 bytes compressed.
- In this repo (`scripts/s521_antenna_on_run/`): `observation_run_20261118_visits.json` (per-visit gate results, from `analyse_observation_run.py <run dir>`), `loadfitted_visits_20261118.json`,
  `loadfitted_gate_eval_20261118.py` and `loadfitted_spectrum_20261118.json`.
