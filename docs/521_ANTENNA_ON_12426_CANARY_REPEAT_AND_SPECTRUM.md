# §5.21 antenna-on at 12426 Mukilteo Speedway: canary repeat and spectrum comparison (2026-11-18)

Evidence record, an addendum to `521_ANTENNA_ON_THIRD_OBSERVATION_RUN_EVIDENCE.md`. It changes no frozen file, test or behaviour. Feasibility captures only: not catalogue runs, no entries, no promotions.
Everything about the venue, the antenna and the 50 ohm load is the owner's statement and was not verified.

## What was captured
- **Load-fitted baseline** (09:50-09:52 PST, before run 3): 8 tunings (0, 8, ... 56) x 4 retune positions (0, -100, +200, -50 kHz), gain 29.0 dB, E4000, 32 captures, all live.
- **Antenna-on canary repeat** (11:28-11:30 PST, about an hour after run 3): the same 8 tunings and offsets, same gain, antenna connected (owner statement), 32 captures, all live.
- Same schedule file, same gate (`rf_canary_gate.visit_gate`, ±256 kHz, 11.0 dB floor, 60 Hz match) as the earlier canary repeats. The `capture_log.json` of the antenna-on set records its purpose as antenna-on, same tunings as the load-fitted sets.
  The load-fitted set's `purpose` text was garbled by a label edit before it ran and is kept as captured (it still says feasibility only, load fitted and the venue).

## Canary repeat result (gate)
| set | gate pass | weaker-side excess where both detected (min / median / max, dB) | sample std (median, LSB) | candidates per capture (median) | clipping |
|---|---|---|---|---|---|
| load fitted | 32 / 32 | 15.0 / 16.6 / 17.6 | 0.77 | 2,299 | 0 |
| antenna-on | 20 / 32 | 12.5 / 13.9 / 15.1 (20 captures) | 1.41 | 1,130 | 0 |

| tuning (LO) | pass | sample std (median) | candidates (median) | sides detected over the 4 captures |
|---|---|---|---|---|
| 0 (401.4 MHz) | 0/4 | 1.90 | 243 | 1, 0, 0, 0 |
| 8 (406.9 MHz) | 0/4 | 1.87 | 350 | 0, 0, 0, 0 |
| 16 (415.7 MHz) | 0/4 | 1.95 | 1,062 | 1, 1, 1, 0 |
| 24 (425.2 MHz) | 4/4 | 1.02 | 1,072 | 2 each |
| 32 (434.6 MHz) | 4/4 | 0.94 | 1,190 | 2 each |
| 40 (444.3 MHz) | 4/4 | 1.60 | 3,473 | 2 each |
| 48 (449.8 MHz) | 4/4 | 1.12 | 2,062 | 2 each |
| 56 (457.3 MHz) | 4/4 | 1.11 | 1,548 | 2 each |

The result is all or nothing per tuning: the three lowest tunings fail on all 12 captures, the five from 425 MHz up pass on all 20, and the four retune positions of a tuning agree. Within this minute the pattern did not flicker.
In run 3 the same lower-band/upper-band direction showed (lower band 21/100 visits pass, upper 39/92); the run visited each tuning once per retune position in a mixed order, so the two sets are not compared tuning by tuning here.
The passing tunings' weaker-side excess (median 13.9 dB) is still below the load-fitted median (16.6 dB).

## Spectrum comparison (antenna-on against load fitted, d = 0 captures)
Power spectra from 4096-point Hann FFTs averaged over the capture (500 Hz bins), dB relative to the file's own scale; "rise" is antenna-on minus load fitted at the same tuning.
| tuning (centre) | median floor load / antenna-on (dB) | median rise | mean rise, +-0.8 MHz excluding +-12 kHz | bins >10 dB over median, antenna-on / load |
|---|---|---|---|---|
| 0 (401.37) | -10.9 / -1.2 | +9.8 | +9.1 | 5 / 34 |
| 8 (406.88) | -10.9 / -1.7 | +9.2 | +8.6 | 1 / 36 |
| 16 (415.72) | -9.8 / -2.3 | +7.5 | +7.5 | 24 / 23 |
| 24 (425.22) | -10.6 / -7.2 | +3.4 | +3.1 | 14 / 30 |
| 32 (434.62) | -11.0 / -8.2 | +2.8 | +2.6 | 11 / 35 |
| 40 (444.34) | -10.9 / -8.2 | +2.7 | +2.9 | 116 / 33 |
| 48 (449.79) | -10.9 / -7.2 | +3.7 | +3.5 | 34 / 33 |
| 56 (457.30) | -11.0 / -7.2 | +3.8 | +3.5 | 20 / 33 |

- The broadband floor rise is 7.5-9.8 dB at the three failing tunings and 2.7-3.8 dB at the five passing ones. The gate result follows it: a higher floor lowers the canary excess (floor compression), so the lower-band failures are
  explained by the raised floor, not by a missing canary. The same low-band pattern was seen at Hwy 99 and Lynnwood; its source is not identified here either. This is a floor comparison, not an attribution to any source.
- Tuning 40 (444.3 MHz) has 116 bins over 10 dB above its median antenna-on, against 33 on the load.
- Narrowband antenna-on energy (peak more than 12 dB over the local median and more than 10 dB above the load at the same frequency, over all four offsets, within 0.9 MHz of the centre, `spectrum_compare.json`), in 10 kHz cells:
  400.90-400.91 MHz (+16 to +20 dB); 415.74-416.00 MHz (+13 to +16 dB); 424.90-425.75 MHz (+12 to +14 dB); 443.39-445.44 MHz (+12 to +33 dB, many cells); 449.49-450.30 MHz (+12 to +15 dB); 458.17-458.18 MHz (+19 to +24 dB).
- 445.05 MHz: energy at 445.03 MHz (+24 dB over the local median and the load) is present, as at the Hwy 99 venue. It sits inside a larger cluster, about 443.4-445.4 MHz, with the strongest cell at 445.44 MHz (+33 dB) and cells about 65-70 kHz apart.
  This analysis does not resolve whether the cluster is one carrier with sidebands, a regular set of signals, or a wideband emitter. No emitter record is created or changed by this document, and no location is claimed.

## What this does and does not show
- The 12426 degradation is band-structured, a broadband rise concentrated below about 425 MHz, not a general loss, and it explains the gate result. The receiver was healthy at this site 24 minutes before run 3 (the load-fitted set above).
- It is one repeat taken about an hour after run 3, from one antenna position. It does not show that the pattern is stable over other times of day, nor what the source is. Nothing here separates the site, the antenna position or cable, or local sources.
- Nothing about the freeze changes on this evidence (`521_ANTENNA_ON_FREEZE_RECORD.md`, assumption 1: the 11.0 dB floor leaves little margin where the floor is raised, and `BANDS` already says to expect refused tunings at some sites).

## Evidence
- Antenna-on canary repeat (locked, 37-file manifest): archive `slope_muk12426_antennaon_20261118`, tar.zst sha256 `84ec5f3f443cae2ce77cbcd3a5da66434c8df1718c3fc95201480625adb13837`, tar sha256 `db7715cfb9dcdc02d224bc496543d8eb0cf002ca67ab45e1a4f8327462c8794d`,
  268,482,560 bytes raw, 100,196,450 bytes compressed, verified on dedirock (zst hash, `zstd -t` and decompressed tar hash match the local files).
- Load-fitted baseline: archive `slope_muk12426_load_20261118`, tar.zst sha256 `d25734d197391b547716133e36ab3bc1fed289828ab8944f58aad19a8b887b0d`, tar sha256 `2fc6580e099e1ddd043b607daafee94407ce2c4e560db3e7f69a73330f9671c5`, verified on dedirock.
- In this repo (`scripts/s521_antenna_on_run/site_12426_20261118/`): `antennaon_canary_repeat_visits.json` and `antennaon_gate_eval.py`; `loadfitted_visits.json` and `loadfitted_gate_eval.py`; `spectrum_compare.py` and `spectrum_compare.json`.
  The scripts keep the absolute paths they ran with; the gate scripts need `rf_canary_gate.py` and the sequencer on the path as before.
