# §5.21 uniform gate recompute and floor-rise table over the canary capture sets (2026-11-20)

Evidence record. It changes no frozen file, test or behaviour. Analysis of existing feasibility captures only: nothing was recaptured, no catalogue entries, no promotions. The venues, the antenna and the 50 ohm load are the owner's statements and were not verified.

## What was done
- Every capture set with the 8-tuning layout (tunings 0, 8, ... 56 x 4 retune offsets, gain 29.0 dB, 32 captures) was re-run through the frozen visit gate (`rf_canary_gate.visit_gate`, `canary-1`, ±256 kHz, 11.0 dB, 60 Hz) with **one code version**: `rf_canary_gate.py` and `rf_spur_catalogue_sequencer.py` at main. 23 sets, 736 captures.
  The checkout the earlier per-set `gate_eval.py` scripts imported has byte-identical copies of both files (same sha256), so no version difference was ever present.
- **Result against the earlier per-capture results:** 21 of the 23 sets had stored results; all 672 of their captures were reproduced exactly (pass/fail, reasons, both canary excesses, sample std, clipping, candidate count: no differences). `feasibility_20261114` and `strong_20261114` had no stored gate results; both pass 32/32 on the recompute.
- **Floor level** per capture: the median of the averaged 4096-point Hann power spectrum (dB on the file's own u8 scale). **Floor rise** at a tuning is the set's median floor over its four captures minus the pooled median floor of all load-fitted captures at that tuning.
  Per-tuning medians of the eight load-fitted sets lie within about ±0.2 dB of the pooled value at every tuning, so the reference is stable. The table averages the rise over the five lower tunings (401-435 MHz) and the three upper (444-457 MHz).
- Not included: `intermediate_20261115`, `saturation_20261114`, `load_gains_20261115`, `load_strong_20261115` (other gains or strong-band centres, no canary layout).

## Table (sets in order of capture start, PST)
| set | site | condition | start (PST) | gate pass | lower-band pass (of 20) | upper-band pass (of 12) | weaker-side excess, median (dB) | sample std, median (LSB) | lower-band floor rise (dB) | upper-band floor rise (dB) |
|---|---|---|---|---|---|---|---|---|---|---|
| feasibility_20261114 | not stated | antenna-on | 11-14 13:32 | 32/32 | 20 | 12 | 16.4 | 0.72 | +0.8 | +0.8 |
| strong_20261114 | Hwy99 | antenna-on | 11-14 15:27 | 32/32 | 20 | 12 | 13.6 | 1.03 | +3.6 | +2.1 |
| phinney_20261115 | Phinney | antenna-on | 11-15 16:03 | 32/32 | 20 | 12 | 14.8 | 0.89 | +2.1 | +2.3 |
| hwy99_repeat_20261115 | Hwy99 | antenna-on | 11-15 20:43 | 12/32 | 0 | 12 | 13.8 | 1.92 | +9.5 | +3.2 |
| hwy99_antoff_20261115 | Hwy99 | load fitted | 11-15 21:13 | 32/32 | 20 | 12 | 16.7 | 0.73 | -0.2 | -0.2 |
| hwy99_repeat2_20261116 | Hwy99 | antenna-on | 11-15 22:03 | 31/32 | 20 | 11 | 14.4 | 0.91 | +2.2 | +2.7 |
| mukilteo_20261116 | Muk12502 | antenna-on | 11-16 06:38 | 32/32 | 20 | 12 | 15.4 | 0.81 | +1.5 | +1.8 |
| lynnwood_20261116 | Lynnwood | antenna-on | 11-16 17:39 | 19/32 | 7 | 12 | 13.0 | 1.49 | +6.8 | +2.9 |
| lynnwood_antoff_20261116 | Lynnwood | load fitted | 11-16 18:26 | 32/32 | 20 | 12 | 16.7 | 0.75 | -0.0 | -0.0 |
| mukilteo2_20261117 | Muk12502 | antenna-on | 11-17 06:15 | 32/32 | 20 | 12 | 14.0 | 0.96 | +3.7 | +2.6 |
| mukilteo_antoff_20261117 | Muk12502 | load fitted | 11-17 18:07 | 32/32 | 20 | 12 | 16.8 | 0.74 | -0.1 | -0.2 |
| hwy99_repeat4_20261117 | Hwy99 | antenna-on | 11-17 20:26 | 14/32 | 2 | 12 | 13.2 | 1.62 | +7.8 | +5.0 |
| hwy99_repeat5_20261117 | Hwy99 | antenna-on | 11-17 22:36 | 17/32 | 5 | 12 | 13.6 | 1.40 | +7.5 | +2.8 |
| mukilteo_antoff_20261118 | Muk12502 | load fitted | 11-18 07:04 | 32/32 | 20 | 12 | 16.5 | 0.77 | +0.1 | +0.0 |
| muk12426_load_20261118 | Muk12426 | load fitted | 11-18 09:50 | 32/32 | 20 | 12 | 16.6 | 0.77 | +0.1 | +0.1 |
| muk12426_antennaon_20261118 | Muk12426 | antenna-on | 11-18 11:28 | 20/32 | 8 | 12 | 13.9 | 1.41 | +6.5 | +3.7 |
| muk12426_antennaon2_20261118 | Muk12426 | antenna-on | 11-18 12:21 | 20/32 | 8 | 12 | 14.5 | 1.20 | +6.1 | +3.1 |
| lynnwood_load_20261118 | Lynnwood | load fitted | 11-18 17:57 | 32/32 | 20 | 12 | 16.6 | 0.77 | +0.1 | +0.0 |
| lynnwood_antennaon_20261118 | Lynnwood | antenna-on | 11-18 18:19 | 18/32 | 6 | 12 | 11.9 | 1.29 | +6.5 | +3.8 |
| hwy99_load_20261118 | Hwy99 | load fitted | 11-18 23:53 | 32/32 | 20 | 12 | 16.7 | 0.76 | -0.0 | +0.0 |
| hwy99_antennaon_20261119_b | Hwy99 | antenna-on | 11-19 00:03 | 31/32 | 20 | 11 | 13.2 | 1.06 | +3.2 | +5.6 |
| hwy99_load_20261119 | Hwy99 | load fitted | 11-19 18:36 | 32/32 | 20 | 12 | 16.9 | 0.75 | -0.0 | +0.0 |
| hwy99_antennaon_20261119_eve | Hwy99 | antenna-on | 11-19 19:45 | 32/32 | 20 | 12 | 14.3 | 0.94 | +2.6 | +2.1 |

`feasibility_20261114` has no venue in its capture log (`not stated`); the other sets' venues are as stated in their logs (Hwy 99 = 13601 Highway 99, Everett; Muk12502 and Muk12426 = 12502 and 12426 Mukilteo Speedway; Lynnwood = 2608 196th St SW; Phinney = 6015 Phinney Ave N).

## What the table shows
- **Load-fitted sets:** all 8 pass 32/32 and have a lower-band rise between -0.15 and +0.11 dB. The receiver baseline is flat over five days and four addresses.
- **Antenna-on, lower band:** the lower-band passes follow the lower-band floor rise (correlation -0.96 over the 15 antenna-on sets). Every set with a rise up to +3.7 dB passed all 20 lower-band captures; every set at +6.1 dB or more failed most of them (0-8 of 20 pass). No set lies between +3.7 and +6.1 dB.
  With a load-fitted weaker-side excess of about 16.7 dB and an 11.0 dB floor, a rise of about 5.7 dB is where the weaker side should reach the floor; the data are consistent with that and have no set near it to test it.
- **Upper band:** the upper tunings passed in all sets except two single refusals (Hwy 99 2026-11-15 22:03 and 2026-11-19 00:03, 11 of 12), with a rise of +0.8 to +5.6 dB.
- **Same site, different hours:** Hwy 99 on 2026-11-15 had a lower-band rise of +9.5 dB at 20:43 (0/20) and +2.2 dB at 22:03 (20/20), with a clean load-fitted set between them. The antenna-side floor can change within 80 minutes at one address.
- **No pattern by site or time of day** is visible: the two clean morning sets are both at 12502, failing sets lie between 11:28 and 22:36, and the sets of one site are not independent. 15 antenna-on sets is not a rate.

## What this does and does not show
- It shows the gate result follows the broadband floor rise, as the floor-compression explanation says (`521_ANTENNA_ON_12426_CANARY_REPEAT_AND_SPECTRUM.md`). It does not show what raises the floor. Source, antenna position, cabling, local interference and front-end overload from strong out-of-band signals all remain open; the strong-band sets of 2026-11-14 were not used here.
- The floor rise is one number per tuning from a median of the averaged spectrum; narrowband energy (such as the 443.9-445.4 MHz cluster) barely moves it. No emitter record is created or changed, and no location is claimed.
- Nothing about the freeze changes on this evidence (`521_ANTENNA_ON_FREEZE_RECORD.md`, assumption 1).

## Where the data and the code are
- In this repo (`scripts/s521_antenna_on_run/uniform_gate_20261120/`): `uniform_recompute.py` (per-capture gate and floor), `uniform_results.json` (per-capture results), `tables.py` (identity check against the stored results and the set table), `floor_rise_table.json`. The scripts keep the absolute paths they ran with.
- Hugging Face dataset (`https://huggingface.co/datasets/bgilbert1984/scythe-uhf-rtl-sdr-evidence`), updated 2026-11-20: `gate_passed` and the other gate columns now come from this recompute for the 23 sets (`gate_recomputed_uniform`); new columns `floor_db` and `floor_rise_db`; new table `metadata/floor_rise_by_set.parquet`; reference floors in `metadata/floor_reference.json`; the dataset card no longer says the gate results came from different script copies.
  The raw IQ files were not changed.
