# §5.21 antenna-on observation: O'Finnigans Pub run (2026-11-24)

Evidence record. It changes no frozen file, test or behaviour. Everything about the venue, the antenna and the 50 ohm load is the owner's statement and was not verified.

## What ran
- Run `521-ANTENNA-ON-OFINNIGANS-20261124` by `rf_antenna_on_run.py` (code at main; the run path files `rf_antenna_on_run.py`, `rf_spur_catalogue_sequencer.py`, `rf_canary_gate.py` and `rf_antenna_on_declaration.py` are unchanged since the Harbour Pointe R2 run),
  2026-11-24 from 22:38:05 to 22:45:37 PST, at O'Finnigans Pub, 13601 Highway 99, Everett, WA 98204 (the same street address as the 2026-11-19 `HWY1` run), antenna connected, gain 29.0 dB,
  receiver SN 71874552 (E4000), rtl_tcp on 127.0.0.1:1234 at 2,048,000 S/s, started by hand with `rtl_tcp -d 71874552 -a 127.0.0.1 -p 1234 -s 2048000 -f 100000000`.
- Antenna: the owner states the extension was 583 mm. The length is not in the confirmed run declaration, whose antenna note says "length and placement not stated"; it is recorded in the launch authorization's `operator_statements` only.
  The 50 ohm load was not fitted (owner statement, "Load Off", made earlier in the same session and not restated in the confirmation).
- Authorization chain: frozen declaration sha256 `94b86ebe...2d795c`; run declaration digest `5fbac799dd0ae83ba35b0225092404a99a520f040451fa6c2586fe9f00c6daed`; launch authorization file sha256
  `bc8dac845470868387e0d576c716c4079294174bc3e366e731aba3085efbdf94`, status AUTHORIZED on the owner's own confirmation naming that digest, the venue and the antenna length (recorded verbatim in the file).
  The run declaration is the Harbour Pointe R2 declaration with only `run_id`, `site` and `output_dir` changed (same seed, so the same 192-visit schedule).
- Evidence class `ANTENNA_ON_OBSERVATION_ONLY`: no catalogue entries, classifications or promotions.
- No earlier attempt: the run started on the first try, with no USB drop or refused visit.

## Result
| measure | value |
|---|---|
| visits acquired / refused | 192 / 0 (every visit on its first attempt) |
| visits passing the frozen visit gate | 161 / 192 |
| tunings scored (all 3 visits pass) | 42 / 64 |
| refusals | 25 `CANARY_BELOW_MIN_EXCESS`, 5 `CANARY_NOT_DETECTED`, 1 `CLIPPING_ABOVE_LIMIT` (0.43% clipped, visit 130; the largest clipping elsewhere is 0.0006%) |
| visits with both sides detected | 187; weaker-side excess 10.1 / 12.4 / 14.7 dB (min / median / max) |
| weakest passing visit | 11.0 dB (median of passing visits 12.7 dB) |
| tunings by visits passed | 3 passed: 42; 2: 15; 1: 5; 0: 2 |

## Structure
The pass rate is steady in time (84/96 then 77/96; by blocks of 16 visits 13, 12, 14, 16, 15, 14, 15, 12, 9, 14, 14, 13 of 16). The script's changepoint search puts a weak break at visit 122 (0.89 before, 0.76 after); with one block at 9/16
that is within what these block counts show and it is not read as a break. Weaker-side excess (median 12.5 and 12.4 dB), candidates per visit (median 531.5 and 504.0) and sample std (median 1.2 LSB both halves) are steady.

There is a mild band difference, much smaller than at Harbour Pointe: visits with LO below 435 MHz passed 80/100 (44/51 early, 36/49 late) and at or above 435 MHz 81/92 (40/45 and 41/47). Of the 20 lower-band refusals 15 were
`CANARY_BELOW_MIN_EXCESS`, 4 `CANARY_NOT_DETECTED` and 1 clipping; of the 11 upper-band refusals 10 and 1. The lower-band pass count is lower in the second half (44/51 to 36/49); with one run this is not shown to be a trend.

## Comparison and what is not shown
At this street address the 2026-11-19 `HWY1` run passed 192/192 and this run 161/192. The two differ in date, in the owner-stated antenna state (this run: 583 mm extension, load off; HWY1's antenna state is as its own record states) and in the recorded surroundings, and the cause of the difference is not identified from either run. The owner mentions the pub has new carpet, a square tile pattern; no claim is made that this matters.

**No receiver check was made.** As for Harbour Pointe R2, there is no load-fitted set at the same tunings after this run and no such set was taken, so the receiver's own state during the run is not shown and no refusal can be
assigned to the antenna side on this evidence alone.

## What this means for the frozen scope
- The gate and the every-visit tuning gate behaved as designed and the run finished with an honest observation document. Nothing about the freeze needs to change on this evidence.
- Yield: 42/64 tunings scored here, against 12/64 at Harbour Pointe on 11-22 and 10/64 on 11-17.

## Evidence
- Published in the dataset `bgilbert1984/scythe-uhf-rtl-sdr-evidence` as `data/runs/run_20261124_hwy99_ofinnigans/` (commit `4d77836e551214c3d04da816f5b06c8f3a5c27f9`): the 192 spool windows, journal, observation document, schedule, declaration, run declaration,
  launch authorization, run log and a manifest. The 192 spool hashes were checked against the journal before upload and a sample of 10 files was re-downloaded and matched `SHA256SUMS`.
- Local run directory: `~/s521_runs/antenna_on_ofinnigans_20261124` (1.6 GB). No `tar | zstd` archive and no off-host copy under the 2026-11-10 archive scheme has been made.
- In this repo (`scripts/s521_antenna_on_run/`): `observation_run_20261124_ofinnigans_visits.json` (per-visit gate results, same shape as the Harbour Pointe file); the numbers above come from `analyse_observation_run.py <run dir>`.
