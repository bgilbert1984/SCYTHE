# §5.21 13601 Highway 99, Everett: site record, repeat 4 gate shape, and the public dataset release (2026-11-20)

Evidence record. It changes no frozen file, test or behaviour. The venue, the position, the antenna and the 50 ohm load are the owner's statements and were not verified.

## Site record (owner statement)
- Venue: 13601 Highway 99, Everett, WA 98204.
- Receiver position stated by the owner on 2026-11-20: **47.87471, -122.27177** (latitude, longitude, decimal degrees). It is the position of the receiving site, as stated; it was not surveyed or checked here.
- It is not the position of any emitter. No emitter record is created or changed by this document, and the 443.9-445.4 MHz narrowband cluster seen at this venue stays unattributed (`521_ANTENNA_ON_HWY99_20261119_EVENING_CANARY_REPEAT.md`).
- The earlier Hwy 99 captures carry the address only; the coordinates are not in the capture logs and are not in the published dataset.

## Repeat 4 (2026-11-17 20:26 PST): per-tuning gate shape, recomputed
`slope_hwy99_repeat4_20261117/visits_repeat4.json`, 32 captures (8 tunings x 4 retune offsets), gain 29.0 dB, antenna-on. Read back on 2026-11-20 from the per-capture gate results; not recaptured.
| tuning index (LO) | pass |
|---|---|
| 0 (401.4 MHz) | 0/4 |
| 8 (406.9 MHz) | 0/4 |
| 16 (415.7 MHz) | 0/4 |
| 24 (425.2 MHz) | 0/4 |
| 32 (434.6 MHz) | 2/4 |
| 40 (444.3 MHz) | 4/4 |
| 48 (449.8 MHz) | 4/4 |
| 56 (457.3 MHz) | 4/4 |

14/32 pass. The 18 refusals are 16 `CANARY_NOT_DETECTED` (a canary side not detected) and 2 `CANARY_BELOW_MIN_EXCESS`.

Comparison with the other captures that had the lower-band pattern, same read-back method:
| set | pass | lower five tunings (401-435 MHz) | upper three (444-457 MHz) | refusals |
|---|---|---|---|---|
| Hwy 99 repeat, 2026-11-15 | 12/32 | 0/4 at each of the five | 4/4 at each | 20 not detected |
| Hwy 99 repeat 4, 2026-11-17 | 14/32 | 0, 0, 0, 0, 2 of 4 | 4/4 at each | 16 not detected, 2 below floor |
| Lynnwood, 2026-11-16 | 19/32 | 2, 3, 0, 2, 0 of 4 | 4/4 at each | 8 not detected, 5 below floor |

The failures sit in the same 401-435 MHz band in all three and the upper three tunings pass every capture. The Hwy 99 sets are the closest match to each other (whole tunings fail or pass); Lynnwood's lower tunings partly pass, so "same shape" holds for the band, not for the tuning-by-tuning pattern.
The 2026-11-18/19 repeats and run 4 at this venue were clean in the lower band (`521_ANTENNA_ON_HWY99_20261119_CANARY_REPEAT.md`, `521_ANTENNA_ON_HWY99_20261119_EVENING_CANARY_REPEAT.md`, `521_ANTENNA_ON_FOURTH_OBSERVATION_RUN_EVIDENCE.md`). The cause of the difference is not identified; this record adds no measurement that separates site, antenna position, cabling or local sources.

## Public dataset release
- Published 2026-11-19/20 as a public Hugging Face dataset, `bgilbert1984/scythe-uhf-rtl-sdr-evidence`, license CC0-1.0.
- Contents: 1,672 raw IQ captures (904 files from 27 canary and feasibility sets, 768 visits from the four 192-visit observation runs), a captures table with the per-file gate results where they exist, a sets table, the per-set logs and scripts, about 14 GB, 1,831 files on the Hub.
- Built on dedirock from the archives already verified there, not from the local directories. A separate local build from the original directories produced identical metadata, including every capture's sha256.
- Hub-side check after the upload: every file in the build manifest is present, all 1,674 large files carry the same sha256 as the manifest, and all sizes match.
- The dataset card states the site addresses as the operator's labels and describes the data as feasibility and observation-only captures, not a catalogue and not ground truth, with no emitter identified. Files are published as captured, which includes the capture machine's paths.
- The dataset viewer had not finished indexing at upload time and its rendering was not confirmed.
