# §5.21 antenna-on at 2608 196th St SW, Lynnwood: load-fitted baseline and canary repeat (2026-11-18)

Evidence record. It changes no frozen file, test or behaviour. Feasibility captures only: not catalogue runs, no entries, no promotions.
The venue, the antenna and the 50 ohm load are the owner's statement and were not verified. The antenna-on set is recorded as antenna connected on the owner's message "Antenna-on".
Earlier captures at this address (2026-11-16) are in `521_CANARY_THRESHOLD_PROPOSAL_DRAFT.md` and `scripts/s521_canary_threshold/` (`gate_eval_lynnwood.py`, `gate_eval_lynnwood_antoff.py`); they are not recomputed here.

## What was captured
- **Load-fitted baseline** (17:57-17:59 PST): 8 tunings (0, 8, ... 56) x 4 retune positions (0, -100, +200, -50 kHz), gain 29.0 dB, E4000 (Device 038), 32 captures, all live.
- **Antenna-on canary repeat** (18:19-18:20 PST, about 20 minutes later): the same 8 tunings and offsets, same gain, antenna connected, 32 captures, all live.
- Same schedule file and gate (`rf_canary_gate.visit_gate`, ±256 kHz, 11.0 dB floor, 60 Hz match) as the earlier canary repeats.

## Gate result
| set | gate pass | weaker-side excess where both detected (min / median / max, dB) | sample std (median, LSB) | candidates per capture (median) | clipping |
|---|---|---|---|---|---|
| load fitted | 32 / 32 | 14.4 / 16.6 / 17.2 | 0.77 | 2,252 | 0 |
| antenna-on | 18 / 32 | 10.2 / 11.9 / 15.3 (26 captures) | 1.29 | 316 | 0 |

| tuning (LO) | pass | sample std (median) | candidates (median) | canary sides detected, of 8 |
|---|---|---|---|---|
| 0 (401.4 MHz) | 0/4 | 1.51 | 220 | 5 |
| 8 (406.9 MHz) | 1/4 | 1.33 | 300 | 7 |
| 16 (415.7 MHz) | 2/4 | 1.47 | 257 | 7 |
| 24 (425.2 MHz) | 1/4 | 1.37 | 249 | 6 |
| 32 (434.6 MHz) | 2/4 | 1.27 | 330 | 8 |
| 40 (444.3 MHz) | 4/4 | 1.12 | 826 | 8 |
| 48 (449.8 MHz) | 4/4 | 1.00 | 858 | 8 |
| 56 (457.3 MHz) | 4/4 | 0.91 | 841 | 8 |

The receiver was healthy at this address about 20 minutes before the antenna-on set (the load-fitted set above matches the load-fitted sets at 12502 and 12426). The lower five tunings (401-434 MHz) fail or partly fail and the upper three pass on all 12 captures,
the same band structure as 2026-11-16 at this address. The lower tunings are partial (1-2 of 4 pass at tunings 8-32) rather than clean-cut, and their candidate counts (220-330) are low.

## Spectrum comparison (antenna-on against load fitted, d = 0 captures)
Method as in `521_ANTENNA_ON_12426_CANARY_REPEAT_AND_SPECTRUM.md` (4096-point Hann FFT averaged over the capture, 500 Hz bins; rise = antenna-on minus load fitted at the same tuning).
| tuning (centre) | median floor load / antenna-on (dB) | median rise | mean rise, +-0.8 MHz excluding +-12 kHz | bins >10 dB over median, antenna-on / load |
|---|---|---|---|---|
| 0 (401.37) | -10.9 / -3.4 | +7.5 | +7.0 | 3 / 29 |
| 8 (406.88) | -10.9 / -5.1 | +5.8 | +5.3 | 5 / 34 |
| 16 (415.72) | -9.8 / -3.8 | +6.1 | +5.7 | 5 / 22 |
| 24 (425.22) | -10.6 / -4.6 | +6.0 | +5.6 | 3 / 28 |
| 32 (434.62) | -11.1 / -5.4 | +5.6 | +5.3 | 4 / 33 |
| 40 (444.34) | -10.9 / -6.8 | +4.1 | +3.8 | 10 / 34 |
| 48 (449.79) | -11.0 / -7.2 | +3.7 | +3.4 | 18 / 31 |
| 56 (457.30) | -11.0 / -8.6 | +2.4 | +2.1 | 15 / 32 |

- The broadband floor rise is 5.6-7.5 dB at the lower five tunings and 2.4-4.1 dB at the upper three, and the gate result follows it (floor compression lowers the canary excess). Same direction as 12426 (7.5-9.8 dB low, 2.7-3.8 dB high) but with a smaller low-band rise
  and a step that is gradual from 435 to 457 MHz, not at 425 MHz. The source of the rise is not identified.
- Narrowband antenna-on energy (more than 12 dB over the local median and more than 10 dB above the load at the same frequency, over all four offsets, `spectrum_compare.json`): 415.01 MHz (+13.7 dB), 444.80 MHz (+12.0 dB), 445.05 MHz (+12.7 dB),
  450.00-450.01 MHz (+15.5 to +21.6 dB), 450.31 MHz (+13.9 dB) and 457.57 MHz (+12.3 dB). 445.05 MHz is present here as a single weak cell (+12.7 dB), not the broad 443.4-445.4 MHz cluster seen at 12426.
  No emitter record is created or changed by this document, and no location is claimed.

## What this does and does not show
- At this address, with a healthy receiver, the antenna side again lowers the canary excess in the lower band by a raised floor, as on 2026-11-16 here and at Hwy 99 and 12426. It is one repeat at one antenna position; it does not give a rate or identify the source.
- Nothing about the freeze changes on this evidence (`521_ANTENNA_ON_FREEZE_RECORD.md`, assumption 1).

## Evidence
- Load-fitted baseline (locked, 38-file manifest): archive `slope_lynnwood_load_20261118`, tar.zst sha256 `42895e9d41897d4bb27846e0bb09d14e6068555faedf92ed8cec27f69e3d03a1`, tar sha256 `b7f4a3d5ab243acea88260b20baebd4008e8a576dd40618cf84941f0010c7485`,
  268,482,560 bytes raw, 68,778,950 bytes compressed, verified on dedirock (zst hash, `zstd -t` and decompressed tar hash match the local files).
- Antenna-on canary repeat (locked, manifest verified): archive `slope_lynnwood_antennaon_20261118`, tar.zst sha256 `1364a60d0b3238ab8554a7b7f14685ec3889079455dbbba81d0854d194388cee`, tar sha256 `4bd378a75d29b3b6d2b151f7462ac69372ae68e8000ce5ee3df4db9095e473ab`,
  268,482,560 bytes raw, 94,525,103 bytes compressed, verified on dedirock the same way.
- In this repo (`scripts/s521_antenna_on_run/site_lynnwood_20261118/`): `loadfitted_visits.json`, `loadfitted_gate_eval.py`, `antennaon_canary_repeat_visits.json`, `antennaon_gate_eval.py`, `spectrum_compare.py`, `spectrum_compare.json` (they keep the absolute paths they ran with).
