# Experiment E: Stage 0B Results (ambient, XTR on scythe-1)

Date: 2026-11-02. Companion to `EXPERIMENT_E_BENCH_PLAN.md` (Stage 0B) and `PASSIVE_TUNING_SENSOR.md` §8.1.

> **Correction (2026-11-02, later the same day): the raw captures behind this note were lost.** The files described under "Data and reproduction" were not at `/tmp/exp_e_stage0b/` on scythe-1 when we went to archive them, and a filesystem-wide search found nothing. We do not know whether they were deleted or never saved there. **The numbers in this note therefore cannot be reproduced from stored data and should be treated as unconfirmed.** A re-capture was taken and analysed (see "Re-capture" and "Re-capture analysis" below); it is not an exact replication, and its conclusions differ from this note's reading.

**Status: precondition partly met, weak evidence.** At a fixed tuning word, two output-byte toggle-rate statistics track in-band power. Nothing here shows an external emission, and no controlled stimulus was used.

## Setup

- DUT: Nooelec SMArt XTR v5 (SN 71874552, E4000), served by the `scythe-rtl-tcp` user service on scythe-1. Captures were taken as a localhost client of `rtl_tcp` (port 1234) with direct sampling set to the Q-branch (rtl_tcp command 0x09 = 2). Direct sampling and the center frequency were restored to off / 100 MHz after each run.
- Note: the host's env file says the XTR has not been through its section-5.21 reference declaration or physical inspection, and that the env file alone does not authorize catalogue or corpus-capture contact. The owner directed these captures.
- Input: 50 ohm terminator for the first checks, then a telescopic dipole with magnetic base (320 mm, later 584 mm).
- Rate 2.048 MS/s, `rtl_tcp` default gain, 8-bit I/Q. Stats per 0.1 s window, inference on 5 s block means (windows are not independent). Code: `scripts/experiment_e/stage0_stats.py`. The regression scripts below were not committed.

## Findings

1. **Direct sampling works on the XTR's Q-branch.** With the dipole connected, noise std rose from about 0.2 LSB (terminated) to 3.8-17.7 LSB, and the noise floor rose about 27 dB at 1 MHz. The I-branch stayed near the terminated level (std 0.79), so `-D 2` is the correct selection. Narrow peaks about 19 dB above the median appear near 1.116 and 1.395 MHz. They were not demodulated and may be aliases of strong VHF signals, since direct sampling has no anti-alias filter. No WWV carrier was seen at 10 MHz and nothing identifiable at 7.074 MHz.
2. **Cross-condition design confounds tuning with content.** Five 60 s captures (12.5 MHz twice, 1.0, 7.074 and 5.0 MHz) differ in tuning word, passband content and amplitude at once. The 12.5 MHz repeat (the null) was flagged on one of five statistics (I-to-next-I toggle rate, d about 1.8), so a single flag at 12 blocks is not trustworthy. Other conditions differed far more than the null (e.g. AM-band Hamming weight d about -45).
3. **Hamming weight is a distribution-position statistic.** Mean byte is 127.37 in every file, so DC offset does not differ across conditions. Hamming weight follows the width of the sample distribution (fewer bytes at 127/128 means lower weight), and within a capture it tracks the window mean (r about -0.96 in the AM files). It did not respond to in-band power in the regression. Use it as a diagnostic, not a leakage statistic.
4. **Fixed-tuning regression (center held at 1.000 MHz).** Per-window log in-band power against the byte statistics, tested against 1000 circular shifts of the series (multiples of 5 s), after removing a linear trend:

| Statistic | 320 mm, 60 s | 584 mm, 120 s |
|---|---|---|
| Hamming weight | r +0.005, p 0.65 | r +0.003, p 0.79 |
| Q to next-I toggle rate | r +0.39, p 0.001 | r +0.23, p 0.001 |
| I to next-I toggle rate | r -0.31, p 0.001 | r -0.17, p 0.001 |

   The Q to next-I and I to next-I results are essentially unchanged after also removing window mean (mean and mean squared). Removing window standard deviation as well is degenerate: log power and log standard deviation correlate at 1.0000, so the regressor is removed. That variant cannot test the question.

## What this does and does not show

- Shown: at a fixed tuning word, the two inter-sample toggle rates move with in-band power, in the same direction, in two files with two antennas, robust to window mean.
- Not shown: any external emission. Adjacent-byte toggle rate depends on how amplitude and spectrum fill the 8-bit codes, so any receiver would show this with no leak.
- Weak leverage: in-band power varied only by a factor of about 1.95 (320 mm) and 1.08 (584 mm) across 0.1 s windows, because averaging washes out the AM modulation. The fitted slopes are extrapolations and are not quoted.
- Multiplicity: 16 tests were run across both files. Only the repeated Q to next-I result is treated as solid.
- Ambient signals are not a controlled stimulus. Propagation, occupancy and temperature drift are uncontrolled. Only a linear trend was removed.

## Gate decision

By the Stage 0B semantics the output-byte proxy was demonstrated (toggle rates only), at weak strength. The re-capture reproduces the signs of the toggle correlations but also shows they follow from each window's spectrum plus quantisation ("Re-capture analysis"), so the proxy is not demonstrated as a leakage statistic. Stage 1 may proceed because it tests the real question; it needs a controlled keyed source with a wide power range, and the first Stage 1 question is whether an external emission follows the byte-level dependence.

## Data and reproduction

**Original captures: lost.** The raw u8 I/Q files (`q1`, `q2`, `am`, `hf7`, `hf5`, `am584`; about 1.7 GB) were written to `/tmp/exp_e_stage0b/` on scythe-1 and are not there. Nothing in this note was archived with a hash, so it cannot be checked against them.

## Re-capture (2026-11-02)

Taken on the owner's direction with `scripts/experiment_e/stage0b_recapture.py`, which writes straight to a durable directory, hashes the stream while recording, and logs the run. Location (scythe-1 only so far; no off-host copy yet):

`/home/spectrcyde/exp_e_evidence/stage0b_recapture_20261102/` (read-only), `MANIFEST.json` sha256 `18e157ceaa515dee2dcb0ec4c5a4aa4e87529432f546dd51b3950d589cf3859c`.

| Capture | Centre | Length | sha256 (first 12) |
|---|---|---|---|
| q1 | 12.5 MHz | 60 s | 8b1dcdbcb0bd |
| am | 1.0 MHz | 60 s | 7f567f5dd12c |
| hf7 | 7.074 MHz | 60 s | 80c134236202 |
| hf5 | 5.0 MHz | 60 s | 54ed285a8572 |
| q2 | 12.5 MHz | 60 s | 5f24b2d2e201 |
| am_120s | 1.0 MHz | 120 s | 5b56e5b996ff |

Same settings as above: 2.048 MS/s, `rtl_tcp` default gain, direct sampling on the Q-branch. The hash taken while streaming equals the hash read back from disk for all six files. The tuner was restored (direct sampling off, 100 MHz) afterwards.

Differences from the original run that matter for comparison:
- **The antenna or terminator was not verified.** Software cannot see it, so `capture_log.json` records it as "NOT VERIFIED". If it differs from the 320 mm / 584 mm dipole used originally, the results will differ for that reason alone.
- `am_120s` stands in for `am584`; there is no separate terminated-input capture.
- The original `stage0b_capture.py` is unchanged and still writes to `/tmp`. Use `stage0b_recapture.py <dir>` instead.

## Re-capture analysis (2026-11-02)

Run on the hash-verified off-host copy. Scripts: `stage0_stats.py`, `stage0b_reanalyze.py`, `confound_suite.py --surrogate`.

**1. Cross-condition comparison (5 s blocks, `q1` baseline).** The same-tuning null `q2` vs `q1` (both 12.5 MHz) flags **all four** byte statistics (e.g. `tog_ii` d = -3.06). The original null flagged one of five. Between-capture drift exceeds the within-capture block spread, so this test is too liberal for comparing captures. `hf5` vs `q1` differences are the same size as that null drift and mean nothing. `am` and `hf7` differ from `q1` far more than the null does (d up to 62), but tuning, passband content and amplitude still change together, so the design cannot say which.

**2. Fixed-tuning regression at 1.0 MHz (finding 4), against per-window log power.**

| Statistic | Original, 320 mm / 584 mm | Re-capture `am` (60 s) | Re-capture `am_120s` |
|---|---|---|---|
| Q to next-I toggle rate | +0.39 / +0.23 | +0.66 | +0.59 |
| I to next-I toggle rate | -0.31 / -0.17 | -0.92 | -0.90 |
| Hamming weight | +0.005 / +0.003 | +0.32 | +0.28 |

Same signs for the two toggle statistics, much larger magnitudes, and Hamming weight now clearly non-zero. All p = 0.001, the floor for 1000 shifts. Power varied only x1.16 and x1.11 (original x1.95 and x1.08), and the regressor is still the variance of the same bytes. Removing the window mean changes nothing.

**3. Signal structure differs from the original description.** In every re-capture file I and Q have identical mean (127.37) and identical spread (1.1 to 2.4 LSB), with corr(I,Q) = 0: they behave as quadrature parts of one signal, which is consistent with the RTL2832U's digital mixer in direct sampling (an inference; not checked against the chip). The original note says the I-branch stayed at the terminated level (std 0.79) while Q carried the signal. At 1.0 MHz the samples are strongly coloured (lag-1 autocorrelation -0.71). Whether the receiver state differs from the original run, or the original description was incomplete, is unresolved; the antenna was not verified either.

**4. What explains the toggle correlations.** An iid Gaussian receiver at the observed operating point (mean 127.37, sd 2.37) gives `tog_ii` slope +0.003 per decade against the real -0.93, and the opposite sign for Hamming weight: sample width alone does not explain it. A **spectral surrogate** does. Per real window it keeps the exact complex power spectrum (including I/Q structure) and the DC bin, randomises the phases and re-quantises to bytes (`confound_suite.py --surrogate`):

| Statistic | Real slope | Surrogate slope | Window-by-window correlation, real vs surrogate |
|---|---|---|---|
| I to next-I toggle | -0.93 | -0.97 | 0.98 |
| Q to next-I toggle | +0.22 | +0.24 | 0.69 |
| Hamming weight | +0.28 | -0.10 | 0.68 |

(`am`, 600 windows; `am_120s` agrees.) The I-to-next-I toggle rate is reproduced almost exactly by each window's power spectrum plus quantisation; the Q-to-next-I rate is reproduced in slope, with a window-by-window correlation of 0.69. The surrogate does **not** reproduce the dependence of Hamming weight on power. That is not evidence of a leak: the surrogate replaces the ADC's real code structure with rounded continuous values (its mean levels differ from the real ones by up to 8%), and Hamming weight is the statistic most sensitive to the distribution position at the 127/128 code boundary.

The parametric no-leak scenario `quadrature_colored` in the suite (one coloured complex signal, sd 2.37, lag-1 autocorrelation -0.7, power range x1.95) agrees for the I-to-next-I toggle (r = -0.97, flagged in every seed, against -0.92 real) but not for the Q-to-next-I toggle (+0.09 against +0.66) or the sign of Hamming weight. It is a cruder model than the real-data surrogate, which is the better test.

**Reading.** The toggle correlations in this note are what a receiver with no external emission produces, to first order, because both the regressor and the statistics are functions of each window's spectrum. They are not evidence of a frequency-selective external emission, and the original observation that Hamming weight did not respond to power is not replicated. Nothing in the existing data can discriminate a leak from receiver physics; that needs a controlled stimulus whose level is known independently of the receiver bytes.

Scripts in `scripts/experiment_e/` (run on scythe-1 with `~/SCYTHE/.venv/bin/python -` fed on stdin over ssh, since they need numpy and a localhost `rtl_tcp` client; do not stream the raw captures back through the ssh session, which stalled at 8 MB):

- `direct_sampling_check.py`: 2 s captures per setting, PSD summary (finding 1).
- `stage0b_capture.py`: the five 60 s captures (finding 2). Superseded by `stage0b_recapture.py`, which does not use `/tmp`. Then `python3 -I stage0_stats.py --rate 2048000 --block-s 5 q1=... ` on the files.
- `stage0b_dccheck.py`: DC offset and Hamming-weight check (finding 3).
- `stage0b_recapture.py` (writes to a durable directory), `stage0b_reanalyze.py` (the regression on stored files), and `confound_suite.py` (`--selftest`, full suite, `--surrogate FILE`): added after the original captures were lost.
- `stage0b_regress.py`: captures `am584` and runs the fixed-tuning regression (finding 4). It writes to `/tmp/exp_e_stage0b/` and restores direct sampling off / 100 MHz in a `finally` block.
- `stage0b_partial.py`: the covariate-removal rerun, including the degenerate variant.

These scripts hard-code `/tmp/exp_e_stage0b` and `127.0.0.1:1234`, and they change the live `rtl_tcp` tuner state while they run. Stop other `rtl_tcp` consumers first, and confirm the XTR is cleared for contact.
