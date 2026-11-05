# §5.21 Tier A — estimator vs spec, and the nominal-frequency figures (record)

Companion to `docs/521_LTE_PSS_CONFIRMATION_RECORD.md` and `docs/521_LTE_PSS_CONFIRMATION_PROCEDURE.md` (sections 3.3 and 5).
Written 2026-11-06 by the assistant. **One reader (the assistant). No human second reader has signed this document.** The
estimator source was not changed (the calibration plan's frozen hash includes it).

## 1. Estimator output against the spec (item 4)
Script: the snippet in section 5 below, run with the repository `.venv` against `main` at b5da4062.

| Check | Result |
|---|---|
| `pss_sequence(u)` for u = 25, 29, 34, all 62 values, against d_u(n) computed from the TS 36.211 6.11.1.1 formula as transcribed in the PSS record | max difference 9.1e-13. Agrees. (The 15 values printed in the procedure were already compared in the PSS record.) |
| `PSS_ROOTS` | (25, 29, 34). Agrees with Table 6.11.1.1-1 |
| Mapping k = n-31 (n <= 30), n-30 (n >= 31) | Agrees with 6.11.1.2 / 6.12 as transcribed; DC unused |
| `CP_LEN` (10, 9, 9, 9, 9, 9, 9), `SLOT` 960, `BLOCK` 9600 | 160/144 T_s x 1.92/30.72 = 10/9; 15360 T_s = 960 samples; 9600 samples = 5.000 ms. Agrees |

## 2. Finding: the estimator does not refuse a TDD-position PSS (A7)
The procedure (A7) expects the estimator to refuse a TDD cell rather than mis-measure it. It does not do that. In
`estimate_lte_carrier` the PSS test is run on **all 70 symbols of the 5 ms block** (`for j in range(10): for s in range(7)`), and the
best score over those rows is used, so the PSS symbol position within the slot is never used. The "last symbol of slots 0 and 10"
placement (A3) is an assumption of the synthetic generator only, not a dependency of the estimator.

Test (synthetic, same generator with the PSS moved to the TDD position, third symbol of subframes 1 and 6; all other symbols FDD-like
downlink; true offset 3000 Hz; SNR 10 dB; seeds 1-4):

| Signal | Outcome |
|---|---|
| FDD position (control) | detected, root 29, offsets 2993.96 / 3005.99 / 2993.51 Hz (seeds 1-3) |
| TDD position | detected, root 29, offsets 2994.37 / 3005.80 / 2992.30 Hz (seeds 1-3), vote share 0.76-0.86 |

Reading: on this synthetic signal a TDD-position PSS is measured, with the same accuracy, not refused. This is **not** a statement
about a real TDD cell. Real TDD has uplink subframes and special subframes with no CP structure, the CP correlation was not tested
there, and nothing here exercises it. What it does show: the estimator would not refuse on PSS position, so a TDD cell is excluded
today only by the operator choosing FDD channels. Options for the owner (none taken): (a) accept and state the limit in the plan;
(b) restrict the PSS search to the FDD symbol positions, which changes the estimator and so requires re-freezing any plan; (c) test on
a real or realistic TDD recording first. Until one is chosen the PSS record's A7 line should be read as "placement differs in the
spec; the code does not refuse it".

## 3. TS 36.101 channel raster and EARFCN (item 6, part 1)
```
Document: TS 36.101 V20.1.0 (2026-09)   file 36101-k10.zip   downloaded 2026-11-05 ~22:23 UTC
URL:      https://www.3gpp.org/ftp/Specs/archive/36_series/36.101/36101-k10.zip
SHA-256:  9f9a56f5d0535e5f77da0b4154c3056ed6e0d09c4e0ee22b2beb9676de0144ad   (9,446,195 bytes)
```
Read from the .docx text (the equation here is plain text, not a picture):
- 5.7.2: "The channel raster is 100 kHz for all bands, which means that the carrier centre frequency must be an integer multiple of
  100 kHz."
- 5.7.3: downlink **F_DL = F_DL_low + 0.1 (N_DL - N_Offs-DL)** in MHz, with F_DL_low and N_Offs-DL from Table 5.7.3-1; EARFCN range 0-262143.
  This matches the relation quoted in the PSS procedure section 5.
- Table 5.7.3-1 rows (F_DL_low MHz, N_Offs-DL, N_DL range), read for the bands likely to be used: band 2 (1930, 600, 600-1199); 3 (1805, 1200,
  1200-1949); 4 (2110, 1950, 1950-2399); 5 (869, 2400, 2400-2649); 7 (2620, 2750, 2750-3449); 12 (729, 5010, 5010-5179); 13 (746, 5180,
  5180-5279); 14 (758, 5280, 5280-5379); 17 (734, 5730, 5730-5849); 20 (791, 6150, 6150-6449); 25 (1930, 8040, 8040-8689); 26 (859, 8690,
  8690-9039); 28 (758, 9210, 9210-9659); 41 (2496, 39650, 39650-41589); 71 (617, 68586, 68586-68935). Bands 66 and others were not extracted;
  read the table for any band actually used.
- Worked check: band 12, N_DL = 5110 -> 729 + 0.1 x (5110 - 5010) = 739.0 MHz.

Not established: that a given operator's cell is on the raster point (the raster is a UE-side requirement), and that the carrier
centre in the estimator (DC of the 6.12 grid) is the frequency F_DL names. The latter is the 6.12/6.13 convention (carrier frequency f_0
upconversion of the baseband signal whose grid excludes DC); it was read in 6.12 but 6.13 was not checked against 36.101's definition of
"carrier centre". Row values should be re-read per band used; this record is a transcription by one reader.

## 4. TS 36.104 frequency error (item 6, part 2)
```
Document: TS 36.104 V19.2.0 (2025-12), Release 19   file 36104-j20.zip   downloaded 2026-11-05 ~22:23 UTC
URL:      https://www.3gpp.org/ftp/Specs/archive/36_series/36.104/36104-j20.zip
SHA-256:  e053f9ea42e4e4ff64244225e4fbbf81c7eddde661d8539f290696628df5d7d5   (2,516,590 bytes)
```
Clause 6.5.1 (Frequency error), minimum requirement 6.5.1.1: "the modulated carrier frequency of each E-UTRA carrier configured by
the BS shall be accurate to within the accuracy range given in Table 6.5.1-1 observed over a period of one subframe (1ms)."
Table 6.5.1-1: **Wide Area BS +-0.05 ppm; Medium Range BS +-0.1 ppm; Local Area BS +-0.1 ppm; Home BS +-0.25 ppm.**

Consequences for the calibration procedure (`docs/521_XTR_PPM_CALIBRATION_PROCEDURE.md`):
- The "on the order of 0.05 ppm" figure is confirmed **for Wide Area base stations only**. It is a minimum requirement (a limit), not a
  measurement of a particular transmitter. Medium/Local Area are twice as loose and Home BS five times, so the cell's BS class matters; a
  small cell identified at the site would not support 0.05 ppm.
- It is a requirement on the transmitted carrier vs the assigned frequency, observed over 1 ms; it says nothing about long-term drift or
  about a network's actual practice.
- Not checked: TS 45.005 (GSM BTS), which the calibration procedure also names. Do not cite a GSM figure from this record.
- The amendment text may cite "TS 36.104 V19.2.0 clause 6.5.1, Table 6.5.1-1" for the Wide Area figure once a human second reader has
  checked the table.

## 5. Reproduction snippet
```
.venv/bin/python - <<'PY'
import cmath, math, numpy as np, rf_lte_carrier_estimator as L
spec = lambda u, n: cmath.exp(-1j*math.pi*u*(n*(n+1) if n <= 30 else (n+1)*(n+2))/63)
w = max(abs(L.pss_sequence(u)[n-31 if n <= 30 else n-30] - spec(u, n)) for u in (25, 29, 34) for n in range(62))
print(w, L.PSS_ROOTS, L.CP_LEN, L.SLOT, L.BLOCK)
PY
```
The TDD experiment copies `synthetic_lte_clean` with the PSS condition `s == 6 and slot % 10 == 0` replaced by `s == 2 and slot % 10 == 2`
and runs `estimate_lte_carrier` on it (N and FS from `test_rf_ppm_calibration`; noise from `add_noise`, 10 dB).

## 6. Status
Done: estimator constants and sequence agree with the transcribed spec; TS 36.101 raster/EARFCN relation and TS 36.104 frequency-error
table read. Not done: any human second reading of this document; real TDD behaviour; per-band re-read of Table 5.7.3-1; TS 45.005;
Check B; the XTR calibration run. `pss_definition_confirmed` and the plan are unchanged.
