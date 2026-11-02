# Experiment E: Stage 0B Results (ambient, XTR on scythe-1)

Date: 2026-11-02. Companion to `EXPERIMENT_E_BENCH_PLAN.md` (Stage 0B) and `PASSIVE_TUNING_SENSOR.md` §8.1.

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

By the Stage 0B semantics the output-byte proxy was demonstrated (toggle rates only), at weak strength. Stage 1 may proceed, but it needs a controlled keyed source with a wide power range, and the first Stage 1 question is whether an external emission follows the byte-level dependence.

## Data and reproduction

Captures are raw u8 I/Q on scythe-1 under `/tmp/exp_e_stage0b/` (`q1`, `q2`, `am`, `hf7`, `hf5`, `am584`; about 1.7 GB), not in the repo.

Scripts in `scripts/experiment_e/` (run on scythe-1 with `~/SCYTHE/.venv/bin/python -` fed on stdin over ssh, since they need numpy and a localhost `rtl_tcp` client; do not stream the raw captures back through the ssh session, which stalled at 8 MB):

- `direct_sampling_check.py`: 2 s captures per setting, PSD summary (finding 1).
- `stage0b_capture.py`: the five 60 s captures (finding 2). Then `python3 -I stage0_stats.py --rate 2048000 --block-s 5 q1=... ` on the files.
- `stage0b_dccheck.py`: DC offset and Hamming-weight check (finding 3).
- `stage0b_regress.py`: captures `am584` and runs the fixed-tuning regression (finding 4). It writes to `/tmp/exp_e_stage0b/` and restores direct sampling off / 100 MHz in a `finally` block.
- `stage0b_partial.py`: the covariate-removal rerun, including the degenerate variant.

These scripts hard-code `/tmp/exp_e_stage0b` and `127.0.0.1:1234`, and they change the live `rtl_tcp` tuner state while they run. Stop other `rtl_tcp` consumers first, and confirm the XTR is cleared for contact.
