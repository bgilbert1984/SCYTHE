# §5.21 antenna-on at 13601 Highway 99, Everett: evening load-fitted baseline and canary repeat (2026-11-19)

Evidence record, an addendum to `521_ANTENNA_ON_HWY99_20261119_CANARY_REPEAT.md`. It changes no frozen file, test or behaviour. Feasibility captures only: not catalogue runs, no entries, no promotions.
The venue, the antenna and the 50 ohm load are the owner's statement and were not verified.

## What was captured
- **Load-fitted baseline** (2026-11-19 18:36-18:38 PST, Device 044): 8 tunings (0, 8, ... 56) x 4 retune positions (0, -100, +200, -50 kHz), gain 29.0 dB, E4000, 32 captures, all live.
- **Antenna-on canary repeat** (2026-11-19 19:45 PST start, Device 045): the same 8 tunings and offsets, same gain, antenna connected (owner statement), 32 captures, all live.
- **A device drop between the two.** After the load-fitted set finished cleanly, the XTR left the host (`usb 1-1: USB disconnect, device number 44`) and `lsusb` showed no Realtek device. Nothing was captured on the dropped device. After a physical replug it enumerated as Device 045 and the repeat ran in a fresh directory.
  The load-fitted set and the antenna-on set are therefore on different device numbers, with a replug in between. The cause of the drops is not diagnosed.
- This is the same venue as `521_ANTENNA_ON_HWY99_20261119_CANARY_REPEAT.md` about 19 hours later (that repeat: 2026-11-19 00:03 PST). Same schedule file and gate (`rf_canary_gate.visit_gate`, ±256 kHz, 11.0 dB floor, 60 Hz match).

## Gate result
| set | gate pass | weaker-side excess where both detected (min / median / max, dB) | sample std (median, LSB) | candidates per capture (median) | clipping (max) |
|---|---|---|---|---|---|
| load fitted | 32 / 32 | 15.6 / 16.9 / 18.0 | 0.75 | 2,115 | 0 |
| antenna-on | 32 / 32 | 12.3 / 14.3 / 15.5 (32 captures) | 0.94 | 701 | 0.0001% |

| tuning (LO) | pass | weaker-side excess (median, dB) | sample std (median) | candidates (median) |
|---|---|---|---|---|
| 0 (401.4 MHz) | 4/4 | 14.2 | 0.94 | 585 |
| 8 (406.9 MHz) | 4/4 | 14.6 | 0.88 | 714 |
| 16 (415.7 MHz) | 4/4 | 14.3 | 0.93 | 671 |
| 24 (425.2 MHz) | 4/4 | 13.6 | 0.97 | 574 |
| 32 (434.6 MHz) | 4/4 | 13.9 | 0.97 | 602 |
| 40 (444.3 MHz) | 4/4 | 13.9 | 1.04 | 901 |
| 48 (449.8 MHz) | 4/4 | 14.4 | 0.94 | 868 |
| 56 (457.3 MHz) | 4/4 | 15.2 | 0.82 | 923 |

No refusals. The lower band (401-435 MHz) passes on every capture, as at the 00:03 repeat (31/32) and unlike the Hwy 99 repeats of 11-15 (12/32) and 11-17 (14/32, 17/32). Including this one, the lower band has been clean at this venue on 11-16, 11-19 00:03 and 11-19 evening.
The weaker-side excess is 2.6 dB under the load-fitted median; the earlier repeat's gap was 3.5 dB.

## Spectrum comparison (antenna-on against load fitted, d = 0 captures)
Method as in `521_ANTENNA_ON_12426_CANARY_REPEAT_AND_SPECTRUM.md` (4096-point Hann FFT averaged over the capture, 500 Hz bins; rise = antenna-on minus load fitted at the same tuning).
| tuning (centre) | median floor load / antenna-on (dB) | median rise | mean rise, +-0.8 MHz excluding +-12 kHz | bins >10 dB over median, antenna-on / load |
|---|---|---|---|---|
| 0 (401.37) | -11.0 / -8.0 | +3.0 | +2.6 | 7 / 30 |
| 8 (406.88) | -11.0 / -8.7 | +2.3 | +1.9 | 10 / 30 |
| 16 (415.72) | -10.0 / -8.2 | +1.8 | +1.5 | 8 / 22 |
| 24 (425.22) | -10.7 / -7.8 | +2.9 | +2.5 | 8 / 26 |
| 32 (434.62) | -11.1 / -7.8 | +3.4 | +2.9 | 9 / 29 |
| 40 (444.34) | -11.0 / -7.9 | +3.1 | +2.9 | 30 / 33 |
| 48 (449.79) | -11.0 / -9.0 | +2.0 | +1.7 | 16 / 30 |
| 56 (457.30) | -11.1 / -9.5 | +1.5 | +1.2 | 12 / 32 |

- The floor is raised by 1.5-3.4 dB at every tuning, with no low-band step. That is smaller than at the 00:03 repeat (3.0-5.7 dB), 12426 (7.5-9.8 dB low) or Lynnwood (5.6-7.5 dB low). The weaker-side excess is still 2.6 dB under the load's, so the floor rise accounts for part of the gap at most; this analysis does not apportion it.
- Narrowband antenna-on energy (peak more than 12 dB over the local median and more than 10 dB above the load at the same frequency, `spectrum_compare.json`): 434.03 MHz (+15.5 dB); a group at 444.75-445.43 MHz (+12 to +19.5 dB; 445.05 MHz +15.9 dB, 445.06 MHz +16.3 dB, strongest 445.36 MHz +19.5 dB);
  and 449.99-450.00 MHz (+16.5, +20.2 dB) and 450.30 MHz (+14.9 dB).
- 445.05 MHz is present again, inside a group of lines about 445.01-445.43 MHz. The cluster is narrower and weaker here than the 443.9-445.4 MHz cluster at the 00:03 repeat (up to +33.9 dB) and at 12426. This analysis does not resolve whether it is one carrier with sidebands, a regular set of signals or a wideband emitter,
  and it does not locate any emitter. No emitter record is created or changed by this document. 433.919 MHz is not present (434.03 MHz is a different frequency).

## What this does and does not show
- The receiver was healthy at this venue on the load (32/32, excess and std matching the earlier load-fitted sets, including across the replug). The antenna side lowered the excess by 2.6 dB and raised the floor by 1.5-3.4 dB, and the canary held at every tuning.
- Two repeats at this venue, 19 hours apart, were both clean in the lower band. Earlier repeats here were not. Three or four looks give no rate, and nothing here identifies what changes the antenna-side floor.
- Nothing about the freeze changes on this evidence (`521_ANTENNA_ON_FREEZE_RECORD.md`, assumption 1).

## Evidence
- Load-fitted baseline (locked, 38-file manifest): archive `slope_hwy99_load_20261119`, tar.zst sha256 `dd2e53c185826422f36f506faa67896d2ab250216a5e9b9492f9e4e23bf09f51`, tar sha256 `01a4b277552703c9f2a62d33ef2787cb38572756d7220b1ee0ed939242e2d86a`,
  268,482,560 bytes raw, 68,197,355 bytes compressed, verified on dedirock (zst hash, `zstd -t` and decompressed tar hash match the local files).
- Antenna-on canary repeat (locked, 37-file manifest): archive `slope_hwy99_antennaon_20261119_eve`, tar.zst sha256 `3ca30d0dc1c2f71fafcb617f946812bb47dd4891e3df7b4e1ace771dac918bc1`, tar sha256 `ef11f0d9eeb22cdde3b0ca6c6b0b41f66c690b5397143771dece2c1f757cd70f`,
  268,482,560 bytes raw, 79,272,846 bytes compressed, verified on dedirock the same way.
- In this repo (`scripts/s521_antenna_on_run/site_hwy99_20261119_eve/`): `loadfitted_visits.json`, `loadfitted_gate_eval.py`, `antennaon_canary_repeat_visits.json`, `antennaon_gate_eval.py`, `spectrum_compare.py`, `spectrum_compare.json` (they keep the absolute paths they ran with).
