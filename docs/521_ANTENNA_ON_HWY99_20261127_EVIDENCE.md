# §5.21 antenna-on observation: Highway 99 run with a load-fitted baseline before it (2026-11-27)

Evidence record. It changes no frozen file, test or behaviour. Everything about the venue, the antenna and the 50 ohm load is the owner's statement and was not verified.

## What ran
- **Load-fitted baseline first.** `hwy99_load_20261127` (canary set: 8 tunings at 4 retune offsets, 29.0 dB, E4000, 32 captures): the owner stated "Load On" and did not restate the venue, taken as 13601 Highway 99. The frozen gate passed 32/32
  with both canary lines detected on every capture and no clipping. The floor sat about +0.5 dB above the pooled load-fitted reference (the earlier load sets are within about 0.2 dB), the weaker-side excess was 16.2 dB median against 16.6-16.9 dB
  in the earlier load sets, and the sample std 0.83 LSB against 0.75-0.77. The cause of the +0.5 dB is not identified. The set is in the dataset (commit `7f3def90a96d2535c7de4ac587455f3429646cce`).
- **The run.** `521-ANTENNA-ON-HWY99-20261127` by `rf_antenna_on_run.py` (the run-path files `rf_antenna_on_run.py`, `rf_spur_catalogue_sequencer.py`, `rf_canary_gate.py` and `rf_antenna_on_declaration.py` are unchanged since the Harbour Pointe R2 run),
  2026-11-27 from 20:29:49 to 20:37:21 PST, at O'Finnigans Pub, 13601 Highway 99, Everett, WA 98204 (the same street address as the 2026-11-19 `HWY1`, 2026-11-24 and 2026-11-26 runs), antenna connected, gain 29.0 dB, receiver SN 71874552 (E4000),
  rtl_tcp on 127.0.0.1:1234 at 2,048,000 S/s. The XTR re-enumerated on the USB bus after the antenna was refitted, so the `rtl_tcp` used for the baseline was replaced with a fresh one before the run.
- Antenna: the owner states 583 mm, the same figure as for the 2026-11-24 and 2026-11-26 runs. The length is not in the confirmed run declaration, whose antenna note says "length and placement not stated"; it is recorded in the launch authorization's
  `operator_statements` only. The 50 ohm load was off, i.e. not fitted (owner statement).
- Authorization chain: frozen declaration sha256 `94b86ebe...2d795c`; run declaration digest `19e8dfe74f218c0d9d7bad8c99d36561981e1c36d14945e8174749bc028bc5df`; launch authorization file sha256
  `53bc22c473b6af4c64a410f3a17480d6ea528963b8d77a6b1a51f802ed713821`, status AUTHORIZED on the owner's own confirmation naming that digest, the venue, the antenna length and the load state (recorded verbatim in the file). An earlier message
  that said "Load On" beside this digest was not acted on, because it contradicted an antenna-on declaration; the owner chose the baseline first and then confirmed again. The declaration is the Highway 99 declaration of 2026-11-26 with only
  `run_id` and `output_dir` changed (same seed, same 192-visit schedule).
- Evidence class `ANTENNA_ON_OBSERVATION_ONLY`: no catalogue entries, classifications or promotions.
- No earlier attempt: the run started on the first try.

## Result
| measure | value |
|---|---|
| visits acquired / refused | 192 / 0 (every visit on its first attempt) |
| visits passing the frozen visit gate | 192 / 192 |
| tunings scored (all 3 visits pass) | 64 / 64 |
| refusals | none |
| visits with both sides detected | 192; weaker-side excess 14.0 / 15.8 / 17.2 dB (min / median / max) |
| weakest passing visit | 14.0 dB (median of passing visits 15.8 dB) |
| clipping | at most 0.0002% (limit 0.001%) |
| sample std (first 4 MiB of each window) | 0.75 / 0.89 / 2.32 LSB (min / median / max) |
| candidates per visit | median 1178.5 |

## Structure
The pass rate is 16 of 16 in every block of 16 visits, in both halves and in both bands (lower band 100/100, upper band 92/92); there is nothing to compare across time or band.

## Comparison and what is not shown
At this street address the four runs differ: 192/192 on 2026-11-19 (`HWY1`, weaker-side median 14.2 dB, candidates median 603), 161/192 on 2026-11-24 (12.5 dB, 513.5), 165/192 on 2026-11-26 (12.3 dB, 495.5) and 192/192 now (15.8 dB, 1178.5).
The weaker-side excess and the candidate count move together in that order, and this run's weaker-side median (15.8 dB) is within 0.4 dB of the load-fitted baseline taken earlier the same evening (16.2 dB), the second highest
among the nine observation runs (the 2026-11-18 run at 12502 Mukilteo Speedway reached 15.9 dB; the others are 11.2-14.2 dB, and the 6th Ave run has no both-sides-detected visits). This record states that coincidence and does not explain it: what differed between the dates (time of day, surroundings, antenna state, receiver state) is not separated by these runs, and the owner-stated antenna length was the same
for the last three.

**No load-fitted set was taken after the run.** The baseline before it shows a floor +0.5 dB above the earlier reference, and whether that offset held through the run is not shown. A set after the run would bracket it.

## What this means for the frozen scope
- The gate and the every-visit tuning gate passed everything, and the run finished with an honest observation document. Nothing about the freeze needs to change on this evidence.
- Yield: 64/64 tunings scored, against 43/64 and 42/64 at this address on 11-26 and 11-24, and 12/64, 0/64 and 10/64 at other venues.

## Evidence
- Published in the dataset `bgilbert1984/scythe-uhf-rtl-sdr-evidence` as `data/runs/run_20261127_hwy99/` (commit `6426d013b1282882a87d860cc1a438e61619ddb5`): the 192 spool windows, journal, observation document, schedule, declaration, run declaration,
  launch authorization, run log and a manifest. The 192 spool hashes were checked against the journal before upload and a sample of 10 files was re-downloaded and matched `SHA256SUMS`.
- Local run directory: `~/s521_runs/antenna_on_hwy99_20261127` (1.6 GB). No `tar | zstd` archive and no off-host copy under the 2026-11-10 archive scheme has been made.
- In this repo (`scripts/s521_antenna_on_run/`): `observation_run_20261127_hwy99_visits.json` (per-visit gate results, same shape as the other runs' files).
