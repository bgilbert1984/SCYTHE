# §5.21 antenna-on at 12426 Mukilteo Speedway: second canary repeat (2026-11-18)

Evidence record, an addendum to `521_ANTENNA_ON_12426_CANARY_REPEAT_AND_SPECTRUM.md` and `521_ANTENNA_ON_THIRD_OBSERVATION_RUN_EVIDENCE.md`. It changes no frozen file, test or behaviour.
Feasibility capture only: not a catalogue run, no entries, no promotions. The venue and antenna state are the owner's statement and were not verified.

## What was captured
The antenna-on canary repeat was taken again at 12426 Mukilteo Speedway, Mukilteo, WA 98275 at 12:21-12:23 PST, about an hour after the first repeat (11:28-11:30) and about two hours after run 3 (10:16-10:23): the same 8 tunings
(0, 8, ... 56), the same four retune positions (0, -100, +200, -50 kHz), gain 29.0 dB, E4000, antenna connected (owner statement), 32 captures, all live. Same gate and evaluation script as the first repeat, nothing moved or re-seated in between (owner did not report any change).

## Result
| set | gate pass | weaker-side excess where both detected (min / median / max, dB) | sample std (median, LSB) | candidates per capture (median) | clipping |
|---|---|---|---|---|---|
| load fitted (09:50) | 32 / 32 | 15.0 / 16.6 / 17.6 | 0.77 | 2,299 | 0 |
| antenna-on repeat 1 (11:28) | 20 / 32 | 12.5 / 13.9 / 15.1 (20 captures) | 1.41 | 1,130 | 0 |
| antenna-on repeat 2 (12:21) | 20 / 32 | 10.4 / 14.5 / 15.4 (21 captures) | 1.20 | 1,125 | 0 |

| tuning (LO) | pass, repeat 1 | pass, repeat 2 | sample std (median), repeat 1 / 2 | candidates (median), repeat 1 / 2 |
|---|---|---|---|---|
| 0 (401.4 MHz) | 0/4 | 0/4 | 1.90 / 1.80 | 243 / 417 |
| 8 (406.9 MHz) | 0/4 | 0/4 | 1.87 / 1.77 | 350 / 656 |
| 16 (415.7 MHz) | 0/4 | 0/4 | 1.95 / 1.81 | 1,062 / 1,364 |
| 24 (425.2 MHz) | 4/4 | 4/4 | 1.02 / 0.93 | 1,072 / 1,106 |
| 32 (434.6 MHz) | 4/4 | 4/4 | 0.94 / 0.92 | 1,190 / 1,082 |
| 40 (444.3 MHz) | 4/4 | 4/4 | 1.60 / 1.39 | 3,473 / 3,338 |
| 48 (449.8 MHz) | 4/4 | 4/4 | 1.12 / 1.06 | 2,062 / 2,783 |
| 56 (457.3 MHz) | 4/4 | 4/4 | 1.11 / 0.99 | 1,548 / 1,250 |

- The pass or fail of every one of the 32 captures is the same in the two repeats: the three lowest tunings fail on all 12 captures, the five from 425 MHz up pass on all 20. The lowest passing weaker-side excess is 12.5 dB in repeat 1 and 13.6 dB in repeat 2.
- Weaker-side excess where both sides were detected (including the failing captures that were detected but below the 11.0 dB floor) has min 10.4 dB, median 14.5 dB in repeat 2, against 12.5 and 13.9 dB in repeat 1. Sample std is lower in repeat 2 (1.20 against 1.41 LSB), so conditions were slightly quieter, which did not change any capture's outcome.
- On the failing tunings the number of canary sides detected was 4 of 12 in repeat 1 (tunings 0, 8, 16: 1, 0, 3) and 6 of 12 in repeat 2 (1, 1, 4): still mostly not detected.

## What this does and does not show
- The 12426 band-structured pattern (`521_ANTENNA_ON_12426_CANARY_REPEAT_AND_SPECTRUM.md`: broadband floor rise of 7.5-9.8 dB at 401-416 MHz against 2.7-3.8 dB at 425-457 MHz) held over about two hours across run 3 and two repeats.
  It looks like a stable property of this location and antenna setup in this window, not a flicker. Two repeats an hour apart do not give a rate, and neither shows how it behaves at other times of day.
- It does not identify the source and does not separate the site from the antenna position or cable; every capture was from one antenna position. Only moving or re-seating the antenna and repeating can do that.
- The gate captures were not re-compared with a spectrum for this repeat; the spectrum comparison of repeat 1 is not extended here.
- Nothing about the freeze changes on this evidence (`521_ANTENNA_ON_FREEZE_RECORD.md`, assumption 1).

## Evidence
- Locked set (37-file manifest): archive `slope_muk12426_antennaon2_20261118`, tar.zst sha256 `cda111c02bb56bf5c32388ced104033659e47940e2fd81b9eab15ae8a5dbf017`, tar sha256 `0965a61d6ca6c4bc01d7c399c9dcf427107c5394e068a845e71ada587704f322`,
  268,482,560 bytes raw, 97,059,263 bytes compressed, verified on dedirock (zst hash, `zstd -t` and decompressed tar hash match the local files).
- Repeat 1 and the load-fitted baseline: archives and hashes in `521_ANTENNA_ON_12426_CANARY_REPEAT_AND_SPECTRUM.md`.
- In this repo (`scripts/s521_antenna_on_run/site_12426_20261118/`): `antennaon_canary_repeat2_visits.json` and `antennaon_canary_repeat2_gate_eval.py` (it keeps the absolute paths it ran with).
