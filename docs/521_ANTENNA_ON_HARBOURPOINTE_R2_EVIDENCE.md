# §5.21 antenna-on observation: Harbour Pointe run R2 (2026-11-22)

Evidence record. It changes no frozen file, test or behaviour. Everything about the venue, the antenna and the 50 ohm load is the owner's statement and was not verified.

## What ran
- Run `521-ANTENNA-ON-HARBOURPOINTE-20261122-R2` by `rf_antenna_on_run.py` (code identical to main `ce453336`, after #494: no diff in `rf_antenna_on_run.py`, `rf_spur_catalogue_sequencer.py`, `rf_canary_gate.py`
  or `rf_antenna_on_declaration.py`), 2026-11-22 from 14:03:55 to 14:11:24 PST, at Harbour Pointe Retail Town Center, 10100 Mukilteo Speedway, Mukilteo, WA 98275, antenna connected, gain 29.0 dB,
  receiver SN 71874552 (E4000), rtl_tcp on 127.0.0.1:1234 at 2,048,000 S/s.
- Antenna: the owner states the length was 580 mm (584 mm was stated for the first attempt). The length is not in the confirmed run declaration, whose antenna note says "length and placement not stated";
  it is recorded in the launch authorization's `operator_statements` only. The 50 ohm load was not fitted (owner statement, made for the first attempt and not restated for R2).
- Authorization chain: frozen declaration sha256 `94b86ebe...2d795c` and tier `c46f56a6...d699a` (`521_ANTENNA_ON_FREEZE_RECORD.md`); run declaration digest
  `c62fd0310d5121ee5733043c98f9b3613149cbe215e9dbfaafa82a1cc5c69ed0`; launch authorization file sha256 `00f6e9c420f7f0e5240d2dd27e3b1547f07d6556774cfe66b3ca3d5713cffd3d`, status AUTHORIZED on the owner's own
  confirmation naming that digest (recorded verbatim in the file); no time window (the owner waived it on 2026-11-17). The 192-visit schedule matched the frozen digest `d35d706f...3636`.
- Evidence class `ANTENNA_ON_OBSERVATION_ONLY`: no catalogue entries, classifications or promotions.
- First attempt (`521-ANTENNA-ON-HARBOURPOINTE-20261122`, declaration digest `dfc9fc238265a40ed96eefdf3c39d743f485e2cd6c4adcdc2564f4936a97a0b6`, authorization sha256 `d6c7116f...4510`) stopped with
  `ConnectionResetError` during the settle read of visit 0, before any window was acquired. The kernel log shows the usbip connection closing and the device disconnecting about 20 s after its attach; the cause is not
  identified. The XTR was re-attached on a different USB port and the run restarted under a new run id and output directory, with a new digest and a new owner confirmation. The first attempt's directory holds only
  `declaration.json` and `schedule.json`.
- Before R2: stream-liveness check on the new port `live: True`, match fraction 0.277, 15 distinct values (the earlier port gave 0.256, 18).

## Result
| measure | value |
|---|---|
| visits acquired / refused | 192 / 0 |
| visits passing the frozen visit gate | 73 / 192 |
| tunings scored (all 3 visits pass) | 12 / 64 (tunings 33, 40, 41, 43, 45, 46, 51, 52, 54, 55, 57, 58) |
| refusals | 93 `CANARY_NOT_DETECTED`, 26 `CANARY_BELOW_MIN_EXCESS` (no clipping refusal; clipping at most 0.00019%) |
| visits with both sides detected | 99; weaker-side excess 10.0 / 11.7 / 14.5 dB (min / median / max) |
| weakest passing visit | 11.0 dB (median of passing visits 11.9 dB) |
| tunings by visits passed | 3 passed: 12; 2: 14; 1: 9; 0: 29 |

## Structure of the failures: band, not time
The pass rate is the same in both halves of the run (36/96 then 37/96) and by blocks of 16 visits it swings between 1 and 10 with no step (8, 1, 9, 3, 10, 5, 6, 6, 5, 5, 9, 6). That differs from the 2026-11-17 Mukilteo run,
where a break at visit 96 split the run in two. The weaker-side excess is steady (median 11.8 dB first half, 11.5 dB second), candidates per visit are steady (median 593.5 and 598.5) and the sample std is steady
(median 1.50 and 1.46 LSB). The script's changepoint search lands on its lower bound (visit 10) and is not evidence of a break.

The split is by band. Visits with LO below 435 MHz passed 8/100 (4/51 in the first half, 4/49 in the second); visits at or above 435 MHz passed 65/92 (32/45 and 33/47). Of the 100 lower-band visits, 81 were
`CANARY_NOT_DETECTED` and 11 `CANARY_BELOW_MIN_EXCESS`; of the 92 upper-band visits, 12 and 15. The eight lower-band passes were at 422.356, 422.639, 425.271, 432.970, 433.220, 434.416, 434.516 and 434.904 MHz.
No tuning below 435 MHz was scored; all 12 scored tunings are at or above 435.1 MHz.

Because the band is not confounded with visit order here (the schedule mixes the bands at random and both halves show the same split), this is the clearest band-dependent result so far. It is the pattern the freeze record
already lists as an accepted assumption (401-435 MHz refused in part at venues tried, assumption 3); at this venue it is nearly total in the lower band. The cause is not identified: a band-dependent antenna-side
change, a band-dependent receiver effect and a local interferer are not separated by this run.

## No receiver check was made
The 2026-11-17 record paired its run with a load-fitted check of the same receiver. This run has none: the 50 ohm load was not fitted afterwards and the XTR was disconnected after the run. The receiver's own state during and
after R2 is therefore not shown, and the lower-band result cannot be assigned to the antenna side on this evidence alone. A load-fitted set at the same tunings, at this or another venue, would separate the two.

## What this means for the frozen scope
- The gate and the every-visit tuning gate refused the lower band as designed, and the run finished with an honest observation document. Nothing about the freeze needs to change on this evidence.
- Yield depends on the band as well as the venue: 12/64 tunings scored here, 10/64 on 11-17 and 0 scored below 435 MHz in this run.
- Two attempts were needed because of one USB disconnect. The dongle has now wedged or dropped twice in one session (a hung `rtl_sdr`/`rtl_tcp` in uninterruptible sleep needing a Windows-side detach, then this disconnect);
  the usbip link, the port and the host are all candidates and none is identified.

## Evidence
- Published in the dataset `bgilbert1984/scythe-uhf-rtl-sdr-evidence` as `data/runs/run_20261122_harbourpointe_r2/` (commit `71993726002d1628e33190360202661e15d2e5c8`): the 192 spool windows, journal, observation document,
  schedule, declaration, run declaration, launch authorization and a 196-file manifest. The 192 spool hashes were checked against the journal before upload and a sample of 10 files was re-downloaded and matched
  `SHA256SUMS`. There is no `run.log` for this run. The first attempt is not in the dataset.
- Local run directory: `~/s521_runs/antenna_on_harbourpointe_20261122_r2` (1.6 GB). No `tar | zstd` archive and no off-host copy under the 2026-11-10 archive scheme has been made.
- In this repo (`scripts/s521_antenna_on_run/`): `observation_run_20261122_harbourpointe_r2_visits.json` (per-visit gate results, same shape as the 11-17 file); the numbers above come from
  `analyse_observation_run.py <run dir>`, which needs the raw windows for the sample-std lines.
