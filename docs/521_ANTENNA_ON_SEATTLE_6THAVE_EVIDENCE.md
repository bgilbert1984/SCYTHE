# §5.21 antenna-on observation: 1400 6th Ave, Seattle run R2 (2026-11-26)

Evidence record. It changes no frozen file, test or behaviour. Everything about the venue, the antenna and the 50 ohm load is the owner's statement and was not verified.

## What ran
- Run `521-ANTENNA-ON-6THAVE-20261126-R2` by `rf_antenna_on_run.py` (the run-path files `rf_antenna_on_run.py`, `rf_spur_catalogue_sequencer.py`, `rf_canary_gate.py` and `rf_antenna_on_declaration.py` are unchanged since the Harbour Pointe R2 run),
  2026-11-26 from 13:59:54 to 14:07:26 PST, at 1400 6th Ave, Seattle, WA 98101, antenna connected, gain 29.0 dB, receiver SN 71874552 (E4000), rtl_tcp on 127.0.0.1:1234 at 2,048,000 S/s.
- Antenna: the owner states 538 mm (583 mm was stated for the first attempt). The length is not in the confirmed run declaration, whose antenna note says "length and placement not stated"; it is recorded in the launch authorization's
  `operator_statements` only. The 50 ohm load was off, i.e. not fitted (owner statement).
- Authorization chain: frozen declaration sha256 `94b86ebe...2d795c`; run declaration digest `90bf66acb832842e03450498543b02fb24297406881275b4527b5c334cc6000c`; launch authorization file sha256
  `a763df205440285b911c63d537579c9f60f2f9e87a441f33448cf273cdef9d51`, status AUTHORIZED on the owner's own confirmation naming that digest (recorded verbatim in the file). One confirmation message omitted the digest value and was not
  acted on; the next one named it. The declaration is the O'Finnigans declaration with only `run_id`, `site` and `output_dir` changed (same seed, same 192-visit schedule).
- Evidence class `ANTENNA_ON_OBSERVATION_ONLY`: no catalogue entries, classifications or promotions.
- First attempt (`521-ANTENNA-ON-6THAVE-20261126`, digest `c61dea3b925de4b1aa48eb41905be11f8db0d4e3dac9d9e903f91ece82d22303`) stopped with `ConnectionResetError` at visit 0, before any window. The `rtl_tcp` it connected to had been started two
  days earlier; the dongle had since been re-attached (bus device 003 instead of 002) and that process's log showed `rtlsdr_demod_write_reg failed with -4`. That `rtl_tcp` was stopped, a new one started, and the run restarted under a new run
  id, directory, digest and owner confirmation. The first attempt's directory holds only `declaration.json` and `schedule.json`.

## Result
| measure | value |
|---|---|
| visits acquired / refused | 192 / 0 |
| visits passing the frozen visit gate | 0 / 192 |
| tunings scored | 0 / 64 |
| refusals | 192 `CANARY_NOT_DETECTED`, of which 39 also `CLIPPING_ABOVE_LIMIT` (clipping 0.0000-0.113%, median 0.00002%) |
| canary lines detected | none on both sides on any visit; one side only (+256 kHz, 10.1-10.2 dB, below the 11.0 dB minimum) on visits 43 and 47 (456.9 and 459.8 MHz) |
| clipping refusals by band | 28/100 below 435 MHz, 11/92 at or above |
| sample std (first 4 MiB of each window) | 1.8 / 6.0 / 22.1 LSB (min / median / max); other venues' runs sit near 1.2 LSB |
| candidates per visit | median 1 (0 on 61 visits, 1 on 37, 2 on 21); other venues' runs have medians of several hundred |

## What the data do and do not show
- This is not a repeat of the EPOCH1 failure: the windows are not periodic (lag-184 match about 0.10-0.15 on the visits sampled), the sample std varies from visit to visit, and the values span a wide range (visit 191 touches 0 and 255). The receiver
  was delivering varying signal, and the frozen gate refused every visit as designed.
- The receiver's own canary lines were not found on any visit, while the sample level and clipping are higher than at the other venues. That is consistent with strong input at this gain (an urban site), but this run does not show it: no
  attenuated or load-fitted comparison was made, and no cause is identified. No claim is made about which emitters are present.
- The weak-signal candidate count collapsing to 0-3 per visit follows the same pattern and is recorded as observed.
- The `analyse_observation_run.py` helper stops with an empty-sequence error on its "both detected" line for this run, because no visit has both sides detected. The numbers above were taken from the observation document directly; the script is not changed here.

**No receiver check was made.** There is no load-fitted set at the same tunings after this run, so the receiver's own state is not shown and the 0/192 cannot be assigned to the antenna side or to the receiver on this evidence alone. A load-fitted
set at the same tunings, and a lower-gain antenna-on set at this site, would separate the possibilities.

## What this means for the frozen scope
- The gate and the every-visit tuning gate refused all visits, and the run finished with an honest observation document. Nothing about the freeze needs to change on this evidence.
- Yield by venue so far: 192/192 (Highway 99, 11-19), 161/192 (Highway 99, 11-24), 73/192 (Harbour Pointe), 60/192 (12426), 113/192 (12502, 11-17), 0/192 (1400 6th Ave). An urban downtown site is the first with no gate passes.

## Evidence
- Published in the dataset `bgilbert1984/scythe-uhf-rtl-sdr-evidence` as `data/runs/run_20261126_sea6thave_r2/` (commit `8f4a5b52f3168d21d836f0bf8a42d2fd277e8c14`): the 192 spool windows, journal, observation document, schedule, declaration, run declaration,
  launch authorization, run log and a manifest. The 192 spool hashes were checked against the journal before upload and a sample of 10 files was re-downloaded and matched `SHA256SUMS`.
- Local run directory: `~/s521_runs/antenna_on_6thave_20261126_r2` (1.6 GB). No `tar | zstd` archive and no off-host copy under the 2026-11-10 archive scheme has been made.
- In this repo (`scripts/s521_antenna_on_run/`): `observation_run_20261126_sea6thave_r2_visits.json` (per-visit gate results, same shape as the other runs' files).
