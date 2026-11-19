# §5.21 antenna-on at 13601 Highway 99, Everett: load-fitted baseline and canary repeat (2026-11-18/19)

Evidence record. It changes no frozen file, test or behaviour. Feasibility captures only: not catalogue runs, no entries, no promotions.
The venue, the antenna and the 50 ohm load are the owner's statement and were not verified. Earlier Hwy 99 canary repeats are in `521_CANARY_THRESHOLD_PROPOSAL_DRAFT.md` and `scripts/s521_canary_threshold/`; they are not recomputed here.

## What was captured
- **Load-fitted baseline** (2026-11-18 23:53-23:55 PST, Device 041): 8 tunings (0, 8, ... 56) x 4 retune positions (0, -100, +200, -50 kHz), gain 29.0 dB, E4000, 32 captures, all live.
- **Antenna-on canary repeat** (2026-11-19 00:03-00:05 PST, Device 042): the same 8 tunings and offsets, same gain, antenna connected (owner statement), 32 captures, all live.
- **A device drop between the two.** The first attempt at the antenna-on repeat found `rtl_tcp` refusing connections: the XTR had left the host (`usb 1-1: USB disconnect, device number 41`, a few minutes after the load-fitted set finished cleanly). No data was written. After a physical replug it enumerated as Device 042 and the repeat ran in a fresh directory.
  The load-fitted set was therefore taken on the previous device number and the antenna-on set on the new one, about 10 minutes apart; the receiver check is for the same unit and gain, with a replug in between.
- Same schedule file and gate (`rf_canary_gate.visit_gate`, ±256 kHz, 11.0 dB floor, 60 Hz match) as the earlier canary repeats.

## Gate result
| set | gate pass | weaker-side excess where both detected (min / median / max, dB) | sample std (median, LSB) | candidates per capture (median) | clipping (max) |
|---|---|---|---|---|---|
| load fitted | 32 / 32 | 15.3 / 16.7 / 18.8 | 0.76 | 2,174 | 0 |
| antenna-on | 31 / 32 | 10.6 / 13.2 / 15.0 (32 captures) | 1.06 | 577 | 0.00098% |

| tuning (LO) | pass | sample std (median) | candidates (median) | canary sides detected, of 8 |
|---|---|---|---|---|
| 0 (401.4 MHz) | 4/4 | 1.07 | 532 | 8 |
| 8 (406.9 MHz) | 4/4 | 0.97 | 611 | 8 |
| 16 (415.7 MHz) | 4/4 | 1.06 | 516 | 8 |
| 24 (425.2 MHz) | 4/4 | 1.02 | 639 | 8 |
| 32 (434.6 MHz) | 4/4 | 0.98 | 718 | 8 |
| 40 (444.3 MHz) | 3/4 | 2.41 | 1,800 | 8 |
| 48 (449.8 MHz) | 4/4 | 1.09 | 559 | 8 |
| 56 (457.3 MHz) | 4/4 | 1.03 | 571 | 8 |

The one refusal is `t040_d+200000.u8` (LO 444.5 MHz), `CANARY_BELOW_MIN_EXCESS` with weaker side 10.6 dB (other side 11.6 dB); the other three captures of tuning 40 passed at 11.5-12.2 dB weaker-side excess. Tuning 40 is the noisy one (sample std 2.4, 1,800 candidates).
The 401-435 MHz tunings that failed in the Hwy 99 repeats of 11-15 (12/32), 11-17 (14/32) and 11-17 (17/32) pass on every capture here, as they did on 11-16 (31/32); this is the fifth look at the lower band at this venue and the second clean one.

## Spectrum comparison (antenna-on against load fitted, d = 0 captures)
Method as in `521_ANTENNA_ON_12426_CANARY_REPEAT_AND_SPECTRUM.md` (4096-point Hann FFT averaged over the capture, 500 Hz bins; rise = antenna-on minus load fitted at the same tuning).
| tuning (centre) | median floor load / antenna-on (dB) | median rise | mean rise, +-0.8 MHz excluding +-12 kHz | bins >10 dB over median, antenna-on / load |
|---|---|---|---|---|
| 0 (401.37) | -11.0 / -6.8 | +4.2 | +3.7 | 9 / 30 |
| 8 (406.88) | -11.0 / -8.0 | +3.0 | +2.5 | 9 / 33 |
| 16 (415.72) | -10.0 / -6.9 | +3.1 | +2.7 | 5 / 25 |
| 24 (425.22) | -10.6 / -7.6 | +3.0 | +2.7 | 8 / 28 |
| 32 (434.62) | -11.1 / -6.3 | +4.9 | +4.3 | 5 / 33 |
| 40 (444.34) | -10.9 / -5.2 | +5.7 | +5.8 | 76 / 32 |
| 48 (449.79) | -11.0 / -6.6 | +4.4 | +4.0 | 26 / 29 |
| 56 (457.30) | -11.0 / -6.5 | +4.5 | +4.5 | 5 / 32 |

- The floor is raised by 3.0-5.7 dB at every tuning and is not concentrated at the low end: there is no step below 425 MHz as at 12426 (7.5-9.8 dB low, 2.7-3.8 dB high) or at Lynnwood (5.6-7.5 dB low, 2.4-4.1 dB high). The gate passes in spite of a 3-6 dB rise
  because the canary excess stays above 11 dB for all but one capture; the weakest-side excess here (median 13.2 dB) is 3.5 dB under the load's.
- Narrowband antenna-on energy (more than 12 dB over the local median and more than 10 dB above the load at the same frequency, over all four offsets, `spectrum_compare.json`, 69 ten-kHz cells): a cluster from about 443.9 to 445.4 MHz with cells up to
  +33.9 dB at 445.03 MHz and +31.5 dB at 445.04 MHz (445.05 MHz itself +12.5 dB in its cell); a second group at 450.0-450.6 MHz (+13 to +23 dB, strongest 450.53 MHz); and single cells at 400.89 MHz (+16.0), 425.02 MHz (+18.4) and 434.03 MHz (+12.6).
  Tuning 40 has 76 bins over 10 dB above its median antenna-on against 32 on the load.
- The 445.05 MHz carrier is present again, inside the cluster. A cluster of the same extent (about 443.4-445.4 MHz) was seen at 12426 on 11-18; it is weak at Lynnwood (one cell). 433.919 MHz is not present (434.03 MHz is a different frequency).
  This analysis does not resolve whether the cluster is one carrier with sidebands, a regular set of signals or a wideband emitter, and it does not locate any emitter. No emitter record is created or changed by this document.

## What this does and does not show
- At this venue, with a healthy receiver (32/32 on the load), the antenna side lowers the canary excess by a 3-6 dB broadband rise but the 31/32 gate result shows the canary held. One look, at one antenna position; it does not give a rate or identify the source.
- The same band passed or failed differently across the Hwy 99 repeats, as the earlier evidence records. This repeat is a clean case; it does not show the lower-band failures will not recur here.
- Nothing about the freeze changes on this evidence (`521_ANTENNA_ON_FREEZE_RECORD.md`, assumption 1).

## Evidence
- Load-fitted baseline (locked, 38-file manifest): archive `slope_hwy99_load_20261118`, tar.zst sha256 `b6886e2597f6c0498e63fc7d3f575a14e2317bdd00b938c3939cbf48ea3df767`, tar sha256 `826b6ec88a61f22c1720b30dc6861c4517960de9272d87dd8947b3f175872ddd`,
  268,482,560 bytes raw, 68,370,699 bytes compressed, verified on dedirock (zst hash, `zstd -t` and decompressed tar hash match the local files).
- Antenna-on canary repeat (locked, manifest verified): archive `slope_hwy99_antennaon_20261119_b`, tar.zst sha256 `05fd8898cd490a8556acb9284cc1a11a2f8d1b5364e0a4219db9bc07d9960f4d`, tar sha256 `d00f0f0d521859370c636fc865be4bc3c75db2ecbb4cdc40fe9183ff63d37877`,
  268,482,560 bytes raw, 86,698,337 bytes compressed, verified on dedirock the same way. The directory `slope_hwy99_antennaon_20261119` is the failed first attempt (no captures) and is not archived.
- In this repo (`scripts/s521_antenna_on_run/site_hwy99_20261119/`): `loadfitted_visits.json`, `loadfitted_gate_eval.py`, `antennaon_canary_repeat_visits.json`, `antennaon_gate_eval.py`, `spectrum_compare.py`, `spectrum_compare.json` (they keep the absolute paths they ran with).
