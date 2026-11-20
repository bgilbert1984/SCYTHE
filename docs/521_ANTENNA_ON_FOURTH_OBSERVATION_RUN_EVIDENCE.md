# §5.21 antenna-on observation: fourth run, at 13601 Highway 99, Everett (2026-11-19)

Evidence record. It changes no frozen file, test or behaviour. Everything about the venue, the antenna and the 50 ohm load is the owner's statement and was not verified.
It is the fourth antenna-on run and the first at this address. Runs 1 and 2 were at 12502 Mukilteo Speedway and run 3 at 12426 Mukilteo Speedway (`521_ANTENNA_ON_FIRST_OBSERVATION_RUN_EVIDENCE.md`, `521_ANTENNA_ON_SECOND_OBSERVATION_RUN_EVIDENCE.md`, `521_ANTENNA_ON_THIRD_OBSERVATION_RUN_EVIDENCE.md`).

## What ran
- Run `521-UHF-XTR-E4K-ANTENNA-OBS-20261119-HWY1` by `rf_antenna_on_run.py` (code at main `f3948c2b`, after #504), 2026-11-19, finishing 21:12 PST (about 8 minutes), at 13601 Highway 99, Everett, WA 98204, antenna connected,
  gain 29.0 dB, receiver SN 71874552 (E4000), Device 047, same seed and 192-visit schedule as runs 1 to 3 (schedule digest `d35d706f...3636`).
- Authorization chain: frozen declaration sha256 `94b86ebe...2d795c`; run declaration digest `25deea1d49c660b5f6504fcb1ca4b07d9caa7159021148a728eda73af2ecdd47`;
  the launch authorization file recorded the operator's own messages ("run full antenna-on run (slice 3c) here", then the digest line, then the replug into a different port) verbatim. No time window.
- Evidence class `ANTENNA_ON_OBSERVATION_ONLY`: no catalogue entries, classifications or promotions.
- Before the run the XTR dropped twice (Device 45, then Device 46 about 36 seconds after enumerating, with nothing running on it). After a replug into a different USB port it enumerated as Device 047 and stayed attached with nothing running for over a minute before the run started.
  It stayed attached throughout the run. The cause of the drops is not diagnosed; this record does not show that the port made a difference.

## Result
| measure | run 1 (12502, 11-17) | run 2 (12502, 11-18) | run 3 (12426, 11-18) | run 4 (Hwy 99, 11-19) |
|---|---|---|---|---|
| visits acquired / refused | 192 / 0 | 192 / 0 | 192 / 0 | 192 / 0 |
| visits passing the frozen visit gate | 113 / 192 | 192 / 192 | 60 / 192 | 192 / 192 |
| tunings scored | 10 / 64 | 64 / 64 | 6 / 64 | 64 / 64 |
| refusals | 45 not detected, 34 below min excess | none | 91 not detected, 41 below min excess | none |
| weaker-side excess (min / median / max, dB) | 10.0 / 12.0 / 15.1 | 14.5 / 15.9 / 17.4 | 10.1 / 11.2 / 13.8 (101 visits) | 12.5 / 14.2 / 16.0 (192 visits) |
| sample std, median (LSB) | 1.27 first half, 1.57 second | 0.82, 0.80 | 1.71 | 0.96 first half, 0.92 second |
| candidates per visit, median | 524 / 419 | 1,052 / 1,076 | 864 | 603 (587 first half, 615 second) |
| clipping (max) | 0 | 0 | 0 | 0.00017% |

- Both canary sides were detected on all 192 visits. Every block of 16 visits passed 16/16, and the first and last 96 visits agree (weaker-side excess 14.0 and 14.3 dB median, sample std 0.96 and 0.92 LSB). There is no break and no drift.
  The maximum pass-rate-drop search is degenerate at 1.0 before and after.
- Lower band (LO below 435 MHz) 100/100 visits pass, median weaker-side excess 14.0 dB; upper band 92/92, 14.5 dB. There is no low-band step.
- Weaker-side excess matches this venue's canary repeats of the same evening (14.3 dB, 32/32; `521_ANTENNA_ON_HWY99_20261119_EVENING_CANARY_REPEAT.md`) and is about 2 dB under run 2's, which was at a different address; the load-fitted median at Hwy 99 that evening was 16.9 dB.

## What this does and does not show
- Four runs at three addresses: 113/192 and 10/64 (12502, 11-17), 192/192 and 64/64 (12502, 11-18), 60/192 and 6/64 (12426, 11-18), 192/192 and 64/64 (13601 Hwy 99, 11-19). Two clean runs, one poor, one broken at visit 96. Four runs give no rate.
- This record has no antenna-side measurement during the run, no reference channel and no run at this venue at another time. It does not identify why the same receiver, gate and schedule gave a clean result here and at 12502 on 11-18 but not at 12426, nor what changed the lower-band floor at Hwy 99 on the earlier repeats.
- The receiver was healthy on the load at this venue shortly before (32/32, 11-19 18:36 PST) and passed the same canary on the antenna in the repeat; that is a check before the run, not the receiver's state during it.
- The gate and the every-visit tuning gate gave an unrefused observation document. Nothing about the freeze needs to change on this evidence (`521_ANTENNA_ON_FREEZE_RECORD.md`, assumption 1).

## Evidence
- Run directory (locked, 196-file manifest): archive `antenna_on_obs_20261119_hwy99`, tar.zst sha256 `5b1bc83e5fb8ef2736ea526f24384d5f27a6e1cd7b3c9652b08161a872284bc4`, tar sha256 `ac4cf85c699473a706e93a57caa8495aeb9d98860884579ed830f91b7fd0094f`,
  1,610,987,520 bytes raw, 476,762,828 bytes compressed, verified on dedirock (zst hash, `zstd -t` and the decompressed tar hash all match the local files).
- The run declaration, launch authorization and run log are kept beside the run directory, not in it, and are not in the archive (run declaration sha256 begins `438b412f`, authorization `0f6069f0`, the latter recorded in the observation document).
- In this repo (`scripts/s521_antenna_on_run/`): `observation_run_20261119_hwy99_visits.json` (per-visit gate results, from `analyse_observation_run.py <run dir>` and the observation document).
