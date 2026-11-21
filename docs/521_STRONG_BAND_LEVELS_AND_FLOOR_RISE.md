# §5.21 strong-band levels from the 2026-11-14/15 sets, read against the UHF floor rise (2026-11-21)

Evidence record. It changes no frozen file, test or behaviour. Analysis of existing feasibility captures only: nothing was recaptured, no catalogue entries, no promotions. The venues, the antenna and the 50 ohm load are the owner's statements and were not verified.
It follows `521_UNIFORM_GATE_RECOMPUTE_AND_FLOOR_RISE.md`, which left open what raises the antenna-side broadband floor and named front-end overload by strong out-of-band signals as one candidate.

## What was analysed
| set | what | when (PST) | venue |
|---|---|---|---|
| `saturation_20261114` | antenna-on, 36 captures: 93.3, 99.0, 102.0, 750 and 881 MHz at 29.0 dB, and 93.3 and 750 MHz also at 21.5 and 14.0 dB, 4 retune offsets each | 11-14 15:56 | 13601 Highway 99, as stated in its log |
| `load_strong_20261115` | the same plan with the 50 ohm load fitted (owner statement): the control | 11-15 21:37 | not stated in its log |
| `intermediate_20261115` | antenna-on, 32 captures: 750 MHz at 14.0, 11.5, 9.0, 6.5 and 4.0 dB and 93.3 MHz at 14.0, 1.5 and -1.0 dB | 11-15 08:13 | 12502 Mukilteo Speedway, as stated in its log |
Per capture: sample standard deviation of the u8 samples (LSB, about the 127.5 mid-point), the share of samples at 0 or 255, and the strongest line over the median of the averaged 8192-point Hann spectrum (±20 kHz around the centre excluded). Medians over the four retune offsets.
Power over load is the antenna-on sample power (std squared) over the load-fitted one at the same gain and centre. It is an **uncalibrated sample-domain power ratio**, not a measured RF input power.
The antenna-on and load captures are from **different sessions** (11-14 and 11-15, about 30 hours apart), so the ratio assumes the receiver was equally stable in both; it is not a simultaneous measurement of input power. Receiver gain settings in dB are as set on the tuner and were not verified as calibrated power steps.

## Levels at 13601 Highway 99, 2026-11-14 (antenna-on) against the load control
| centre | gain (dB) | std antenna-on (LSB) | std load (LSB) | samples at 0 or 255 | strongest line over median (dB) | power over load (dB) |
|---|---|---|---|---|---|---|
| 93.3 MHz | 29.0 | 103.1 | 0.76 | 45.0% | 28.9 | +42.6 |
| 93.3 MHz | 21.5 | 60.5 | 0.72 | 0.13% | 29.6 | +38.5 |
| 93.3 MHz | 14.0 | 25.5 | 0.71 | 0 | 29.6 | +31.1 |
| 99.0 MHz | 29.0 | 70.7 | 0.75 | 8.6% | 21.4 | +39.4 |
| 102.0 MHz | 29.0 | 96.8 | 0.77 | 36.4% | 26.7 | +42.0 |
| 750 MHz | 29.0 | 28.4 | 1.54 | 0.17% | 8.6 | +25.3 |
| 750 MHz | 21.5 | 13.6 | 0.86 | 0 | 7.8 | +24.0 |
| 750 MHz | 14.0 | 6.0 | 0.80 | 0 | 9.2 | +17.6 |
| 881 MHz | 29.0 | 33.4 | 1.28 | 2.6% | 7.8 | +28.4 |

- At the gain the UHF canary captures use (29.0 dB), tuned onto the broadcast FM band the ADC is clipped on 8.6% to 45% of samples; at 750 and 881 MHz the signals sit +25 to +28 dB over the load level with at most 2.6% clipping. The UHF canary captures at the same gain have a sample std of about 0.7 to 1.0 on the good antenna-on sets (1.4 to 1.9 on the bad ones) and no clipping.
- At 93.3 MHz the antenna-on power over load falls from +42.6 dB at 29.0 dB gain to +38.5 dB at 21.5 dB and +31.1 dB at 14.0 dB. The 29.0 dB point is compressed by the clipping; the 21.5 and 14.0 dB points are not clipped and differ by 7.4 dB for a 7.5 dB gain step.
- The load control's std at 750 and 881 MHz (1.54 and 1.28 at 29.0 dB) is above its UHF value (0.75); that is the load level used above, so the power over load at those two centres is relative to a slightly higher reference.

## 12502 on 2026-11-15, same plan at lower gains (no matching load capture except at 14.0 dB)
| centre | gain (dB) | std (LSB) | samples at 0 or 255 | strongest line over median (dB) |
|---|---|---|---|---|
| 93.3 MHz | 14.0 | 2.62 | 0 | 14.4 |
| 93.3 MHz | 1.5 | 0.80 | 0 | 9.6 |
| 93.3 MHz | -1.0 | 0.73 | 0 | 8.1 |
| 750 MHz | 14.0 | 2.74 | 0 | 11.8 |
| 750 MHz | 11.5 | 2.69 | 0 | 7.7 |
| 750 MHz | 9.0 | 2.42 | 0 | 8.6 |
| 750 MHz | 6.5 | 1.34 | 0 | 11.0 |
| 750 MHz | 4.0 | 1.31 | 0 | 10.3 |
At the shared gain of 14.0 dB the FM level at Hwy 99 (std 25.5) is 19.8 dB above that at 12502 (std 2.62), and 750 MHz is 6.8 dB above (6.0 against 2.74). The two sets are on different days, venues and antenna positions.

## Read against the UHF floor rise
UHF floor rise over the load-fitted baseline (lower band 401-435 MHz / upper band 444-457 MHz), from `521_UNIFORM_GATE_RECOMPUTE_AND_FLOOR_RISE.md`:
| venue and time | lower-band rise | upper-band rise | lower-band passes |
|---|---|---|---|
| Hwy 99, 11-14 15:27 (29 min before the saturation set) | +3.6 dB | +2.1 dB | 20 of 20 |
| 12502, 11-16 06:38 / 11-17 06:15 (nearest antenna-on UHF sets at 12502) | +1.5 / +3.7 dB | +1.8 / +2.6 dB | 20 of 20 each |
| Hwy 99, 11-15 20:43, 11-17 20:26, 11-17 22:36 (bad sets) | +9.5, +7.8, +7.5 dB | +3.2, +5.0, +2.8 dB | 0, 2, 5 of 20 |

- **What the FM clipping does and does not establish.** The clipping was measured with the receiver tuned onto the FM band. It shows a strong local signal environment. It does not quantify how much out-of-band FM energy reaches the nonlinear stages while the receiver is tuned to 401-457 MHz,
  nor the receiver's overload susceptibility there: with the tuner retuned, its input filtering, gain distribution and mixer response differ. These captures are not a direct front-end isolation test.
- On 2026-11-14 the Hwy 99 FM band was strong enough to clip the ADC at 29 dB when tuned onto it, and the UHF floor was raised by only +3.6 dB in the lower band, with every lower-band capture passing. So a strong out-of-band environment at this venue did not by itself produce the +6 to +9 dB lower-band rises seen on other days.
- The Hwy 99 FM level was about 20 dB above 12502's, while the two venues' UHF floor rises were similar (+3.6 dB against +1.5 to +3.7 dB). In this pair the UHF rise does not scale with the FM level.
- What the data cannot say: whether the strong-signal level or its coupling changed on the days of the large rises. No strong-band capture exists from a bad-floor day, and the pairs above are different days. This record does not exclude front-end overload; it shows that its presence at the venue is not sufficient.
- A harmonic check (UHF lines at multiples of FM frequencies) was not done: the 29 dB FM captures are clipped and would produce their own spurs, and the match test would mostly return chance matches.

## A discriminating test (proposed, not run)
This is a proposed experiment, not an authorization to operate hardware. Any hardware change would need its own record and the operator's authorization, and each configuration is a different configuration: results from one must not be pooled with another.
Gain steps, FM rejection and attenuation can help tell candidate mechanisms apart (external broadband noise, front-end overload, internally generated mixing, a change in the antenna or cable), but the response need not follow a simple linear rule: receiver noise, quantization, gain-stage placement and nonlinear mixing all shape it. A reduction with a filter would support an FM-related contribution; it would not by itself say which receiver stage produced the floor elevation.
1. **Confirm the episode.** Run the canary repeat (about 1.5 minutes) and proceed only if the lower-band rise over the load-fitted baseline is repeatable and about +6 dB or more. Keep the raw captures and note the conditions.
2. **Gain sweep** at 29.0, 21.5 and 14.0 dB, allowing for the settling after each change.
3. **FM band-stop filter in line**, same measurements. Measure the filter's insertion loss across the UHF band first, so that a general loss of antenna power is not read as an FM-specific effect.
4. **10 dB attenuator** (measured, not nominal) at the antenna, same measurements.
5. **Restore the baseline configuration.** If the elevated floor has gone before the sequence is finished, call the result inconclusive; do not attribute the change to the last intervention. Where the episode lasts, alternate configurations (baseline, filter, baseline, attenuator, baseline) rather than running each once.
At every configuration record the raw IQ power (sample std), the floor rise over the load-fitted baseline, clipping, the canary excess on both sides and the candidate count.

## Where the data and the code are
- In this repo (`scripts/s521_antenna_on_run/strong_band_20261121/`): `strong_levels.py` and its output `strong_levels.txt`. The script is kept as it ran: it has the capture machine's absolute paths hard-coded and writes `strong_levels.pkl` (spectra, not committed) to the working directory. It has not been re-run against the Hugging Face copies of the captures; a path-parameterised version that checks each source file's sha256 against the dataset's `captures.parquet` and writes deterministic CSV or JSON would be a separate change.
- The raw captures are in the public Hugging Face dataset (`https://huggingface.co/datasets/bgilbert1984/scythe-uhf-rtl-sdr-evidence`) under `data/canary_sets/saturation_20261114`, `load_strong_20261115` and `intermediate_20261115`. Those three sets have no canary-gate columns there because of their other gains and centres; the UHF set `strong_20261114` has them.
- No emitter record is created or changed, and no location is claimed. Nothing about the freeze changes on this evidence (`521_ANTENNA_ON_FREEZE_RECORD.md`, assumption 1).
