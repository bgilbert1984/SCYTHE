# §5.21 antenna-on observation: Highway 99 run (2026-11-26)

Evidence record. It changes no frozen file, test or behaviour. Everything about the venue, the antenna and the 50 ohm load is the owner's statement and was not verified.

## What ran
- Run `521-ANTENNA-ON-HWY99-20261126` by `rf_antenna_on_run.py` (the run-path files `rf_antenna_on_run.py`, `rf_spur_catalogue_sequencer.py`, `rf_canary_gate.py` and `rf_antenna_on_declaration.py` are unchanged since the Harbour Pointe R2 run),
  2026-11-26 from 18:38:18 to 18:46:22 PST, at 13601 Highway 99, Everett, WA 98204 (the same street address as the 2026-11-19 `HWY1` run and the 2026-11-24 O'Finnigans run), antenna connected, gain 29.0 dB,
  receiver SN 71874552 (E4000), rtl_tcp on 127.0.0.1:1234 at 2,048,000 S/s, started fresh after the dongle was re-attached.
- Antenna: the owner states 583 mm, the same figure as for the 2026-11-24 run. The length is not in the confirmed run declaration, whose antenna note says "length and placement not stated"; it is recorded in the launch authorization's
  `operator_statements` only. The 50 ohm load was off, i.e. not fitted (owner statement).
- Authorization chain: frozen declaration sha256 `94b86ebe...2d795c`; run declaration digest `4c1f0dc2d24fd01ab071462f9c91c92343dd0d14a9f59d097eab1f391ee678f1`, which the owner computed from a command supplied in the session (the digest
  was checked against the declaration file before launch); launch authorization file sha256 `f30acfd96e610ec816f0b43e9f52ac30584b635a2c16bbbcaafeeaaa070c48f6`, status AUTHORIZED on the owner's own confirmation naming that digest
  (recorded verbatim in the file). The declaration is the 6th Ave declaration with only `run_id`, `site` and `output_dir` changed (same seed, same 192-visit schedule).
- Evidence class `ANTENNA_ON_OBSERVATION_ONLY`: no catalogue entries, classifications or promotions.
- No earlier attempt: the run started on the first try. Before launch the XTR was absent from the host and the stale `rtl_tcp` from the previous run was replaced.

## Result
| measure | value |
|---|---|
| visits acquired / refused | 192 / 0 (every visit on its first attempt) |
| visits passing the frozen visit gate | 165 / 192 |
| tunings scored (all 3 visits pass) | 43 / 64 |
| refusals | 17 `CANARY_BELOW_MIN_EXCESS`, 7 `CANARY_NOT_DETECTED`, 3 `CLIPPING_ABOVE_LIMIT` (0.001-0.002% clipped, visits 47, 54 and 70, just over the 0.001% limit); the largest clipping elsewhere is 0.0009% |
| visits with both sides detected | 185; weaker-side excess 10.3 / 12.3 / 17.7 dB (min / median / max) |
| weakest passing visit | 11.0 dB (median of passing visits 12.3 dB) |
| tunings by visits passed | 3 passed: 43; 2: 15; 1: 6; 0: none |
| sample std (first 4 MiB of each window) | 0.95 / 1.29 / 3.34 LSB (min / median / max) |
| candidates per visit | median 495.5 |

## Structure
The pass rate is steady in time (85/96 then 80/96; by blocks of 16 visits 14, 15, 15, 13, 15, 13, 13, 12, 15, 15, 12, 13 of 16) with no step. Bands: visits with LO below 435 MHz passed 86/100 (44/51 early, 42/49 late) and at or above
435 MHz 79/92 (41/45 and 38/47), so there is no band split.

## Comparison and what is not shown
This is the second run at this address with this owner-stated antenna length, two days after the O'Finnigans run (161/192, 42/64 tunings, 583 mm, load off). The two agree closely on every measure above: 165 against 161 visits, 43 against 42
tunings, a median weaker-side excess of 12.3 against 12.4 dB, a median of about 500 candidates per visit in both, and a pass rate that is steady in time and across bands. Both are well below the 192/192 of the 2026-11-19 `HWY1` run at the
same address; the cause of that difference is not identified from these runs (date, antenna state and surroundings all differ, and no receiver check exists for any of them).

The two runs use the same schedule, so they are a repeat measurement of the same 64 tunings. This record does not compare which individual tunings passed in each run; that comparison is not made here.

**No receiver check was made.** As for the other runs after the load-fitted baselines of 2026-11-18/19, there is no load-fitted set at the same tunings after this run, so the receiver's own state during the run is not shown and no refusal
can be assigned to the antenna side on this evidence alone.

## What this means for the frozen scope
- The gate and the every-visit tuning gate behaved as designed and the run finished with an honest observation document. Nothing about the freeze needs to change on this evidence.
- Yield: 43/64 tunings scored here, against 42/64 on 11-24 at this address, and 12/64, 0/64 and 10/64 at other venues.

## Evidence
- Published in the dataset `bgilbert1984/scythe-uhf-rtl-sdr-evidence` as `data/runs/run_20261126_hwy99/` (commit `e81f1790a31184da06e0e1138bbc7d0abbc10128`): the 192 spool windows, journal, observation document, schedule, declaration, run declaration,
  launch authorization, run log and a manifest. The 192 spool hashes were checked against the journal before upload and a sample of 10 files was re-downloaded and matched `SHA256SUMS`.
- Local run directory: `~/s521_runs/antenna_on_hwy99_20261126` (1.6 GB). No `tar | zstd` archive and no off-host copy under the 2026-11-10 archive scheme has been made.
- In this repo (`scripts/s521_antenna_on_run/`): `observation_run_20261126_hwy99_visits.json` (per-visit gate results, same shape as the other runs' files).
