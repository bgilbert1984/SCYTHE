# Hann, Half a Bin: Runtime Equivalence, Recorded IQ, and a Scalloping Finding We Chose Not to Ship

**Date:** October 11, 2026
**Author:** SCYTHE Core Engineering Team
**Category:** RF Capture, §5.21, Detector Validation, Pre-Catalogue Evidence

The v2-Hann detector merged to main as PR #132 (`68283192`): Hann windowing
before the FFT, and a log-power parabola for sub-bin interpolation. The
change is detector-only. Retention was already Hann-windowed in production;
the fix made detection consistent with it. This post covers what happened
after the merge: a runtime-equivalence check on a second host, the first
side-by-side of the old and new detector on recorded hardware IQ, and one
small disagreement between them that turned out to be about measurement
geometry, not about either detector. Nothing here is a catalogue
declaration. The historical catalogue stays on `seq-7.1-1`.

## Equivalence on dedirock

A detector validated on one machine has only been validated on one machine.
Before putting real recordings through it, we packaged the 36 frozen holdout
fixtures (a generator, a frozen manifest, and a SHA-256 for every fixture),
checked out the merge commit on dedirock, and ran the production
`detect_candidates` and `measure_persistence` against them. The runner
records its own environment: Python 3.12.14, NumPy 2.5.3, the pocketfft
backend.

The comparison against the reviewed reference covered candidate counts,
peak bins, frequencies, excess values, and retention decisions:

- 36 fixtures, 52 candidates, **0 mismatches** at the fixed 1e-9 Hz/dB
  tolerances.
- Largest frequency difference: exactly 0. Largest excess difference:
  7.1e-15 dB, about 140,000 times inside the tolerance.
- Family A: 20/20 in tolerance, 0 unmatched persistent. Family B: 16/16
  strong and 16/16 weak tones recovered, 0 false persistent.

The tolerances were fixed in advance, and nobody touched them. A mismatch
would have meant investigating the FFT backend, not loosening the bar.

## Both estimators on real hardware noise

The only recorded IQ at the §5.21 geometry (2.048 MS/s, CU8, 8 windows of
524,288 samples per visit) is the pre-catalogue terminated-input set: six
120-second captures with a 50 Ω load on the NESDR SMArt v5, at 100, 101 and
100.5 MHz centers. Each file yields 58 non-overlapping visits, 348 in all.
We ran `seq-7.1-1` (commit `398eeb23`) and `seq-7.2-1-hann` (`68283192`)
on identical bytes and kept the outputs in separate files. The retention
estimator is byte-identical between the two commits, so the detector is the
only variable.

| epoch | center | candidates 7.1 → 7.2 | persistent 7.1 / 7.2 |
|-------|--------|----------------------|----------------------|
| 1 | 100.0 MHz | 217 → 116 | 108 / 108 |
| 2 | 100.0 MHz | 223 → 116 | 111 / 111 |
| 3 | 100.0 MHz | 239 → 116 | 112 / 112 |
| 4 | 100.0 MHz | 237 → 116 | 114 / 114 |
| 5 | 101.0 MHz | 429 → 116 | 92 / 89 |
| 6 | 100.5 MHz | 272 → 61 | 58 / 58 |

Two things stand out. First, the Hann window does what the Hann window is
for: candidate counts roughly halve, and in epochs 1–4 and 6 every
persistent decision is identical, so the extra 7.1 candidates were
non-persistent leakage. Second, the known ~100.8 MHz feature is persistent
in all 58 visits of every epoch under both estimators. Suppressing leakage
did not suppress the real signal.

The terminated-input set exercises the detector on real hardware noise. It
shows no diverse signals, so it is a detector-behavior comparison, not a
catalogue run.

## The one disagreement: three visits at +825 kHz

Across 348 visits there was exactly one place where the persistent decision
differed. In epoch 5, a line at +825 kHz was persistent in 34 visits under
7.1 and 31 under 7.2.

The line is real: 824,970 Hz in 57 or 58 of 58 visits, stable to a few
tenths of a hertz, about 11–12 dB above the floor. That is only 1–2 dB over
the 10 dB persistence margin. The two estimators agreed on its frequency to
within a hertz. The obvious suspects were wrong:

- *Leakage the old detector falsely kept?* No. The line is 1.025 MHz from
  the strong −200 kHz feature, far past any Hann sidelobe, and it is stable
  in frequency and level.
- *Noise corrupting the new parabola fit?* No. The 7.2 estimate had *less*
  scatter (0.29 Hz against 0.44 Hz). The difference was a systematic shift
  of about 0.9 Hz.

The mechanism was in retention. The retention estimator reads **one bin**,
the one nearest the declared frequency. At 3.90625 Hz per bin, the
line sits about 0.4 bin from the center of its nearest bin, close to the
rounding edge at 824,970.70 Hz. A Hann window loses about 1.4 dB at a
half-bin offset, so a tone 11–12 dB above the floor can dip under 10 dB in
the bin retention happens to read. Rerunning retention on the three flipped
visits settled it: reading the bin at 824,969 or 824,970 Hz gave 7, 7 and 8
qualifying repeats (persistent); the bin at 824,971 Hz gave 5, 6 and 5. The
detector's one-hertz change in the reported frequency moved the retention
read across the rounding edge.

## A synthetic sweep to separate the causes

We injected a coherent tone into complex Gaussian noise at offsets of 0 to
0.5 bin in steps of 0.05 (30 trials each), at two levels, nominally 12 dB
and 21 dB on-bin excess, and ran both detector revisions on identical
windows. What it showed:

- **Interpolator bias.** The 7.2 log-power parabola is unbiased, within
  ±0.035 bin. The 7.1 power-domain parabola pulls estimates toward the bin
  center by up to 0.29 bin. 7.2's scatter is 0.07–0.09 bin at 12 dB and
  0.02–0.03 bin at 21 dB. Its frequency is also the better one on the
  recorded +825 kHz line.
- **Retention behavior.** With the exact bin, a 12 dB tone's persistent
  fraction falls from about 0.77 on-bin to 0.13 at half a bin. At 21 dB it
  is 100% at every offset. Reading the better of the two adjacent bins does
  not help, because at half a bin both are equally low.

So the detector is not the problem. A half-bin tone at marginal SNR loses
about 1.4 dB to scalloping in a one-bin read, and no detector can recover
that.

## A prototype fix, and why it is not shipping

We prototyped a scalloping-corrected retention: read the same bin, then add
back the Hann scalloping loss at the detector's declared sub-bin offset,
`−20·log10|sinc(δ)/(1−δ²)|`. With the correction switched off, the
prototype reproduces the original retention exactly.

On the synthetic sweep, the corrected persistent fraction at 12 dB stays at
0.6–0.8 out to 0.45 bin (against 0.25 uncorrected there), with repeat-count
scatter unchanged. It does under-correct at exactly half a bin (0.47
against 0.68 with the true offset), because the detector's offset noise can
only fold toward the bin center there. On the recordings:

- Epoch 5's +825 kHz line goes from 31 to 49 of 58 visits under 7.2.
- The −774 kHz line, a stable tone in epochs 1–4, gains five visits that
  all sat at 6 qualifying repeats and 11.5–12.6 dB. Those are known-tone
  recoveries, not noise peaks.
- No other candidate in any epoch changes persistence, under either
  estimator.

The coupling result matters most. Fed 7.1's frequencies, the same correction
barely helps (+825 kHz goes from 34 to 37 visits), because 7.1's bias toward
the bin center shrinks the offset the correction uses: a mean correction of
0.20 dB against 0.92 dB with 7.2. The correction needs 7.2's unbiased
frequency. They ship together or not at all.

We are not shipping it now. Correcting the read changes what "10 dB" means,
from "10 dB above the local floor at the nearest bin" to "10 dB estimated
true peak power". That would lower the effective threshold by up to 1.42 dB
for off-bin tones and rewrite the comparison with the `seq-7.1-1`
catalogue. There is no active catalogue run that needs the new definition,
and a second version of the threshold with no consumer is a cost with no
benefit. The prototype, sweep data and findings are kept in
`~/scallop_retention_proto/` as a reference. If the XTR's captures show
marginal tones lost the same way, the revision goes forward as a reviewed
proposal.

## What this does and doesn't establish

- v2-Hann gives runtime-equivalent results on a second host to numerical
  precision on the frozen holdout.
- On terminated-input recordings it suppresses leakage and keeps the known
  feature. The recordings are 50 Ω loads, not ambient spectrum.
- Its frequency estimate is unbiased on synthetic tones.
- Retention at marginal SNR and half-bin offsets has a known blind spot:
  quantified, not fixed, by design.
- It says nothing yet about ambient signals, and nothing about the XTR.

## What's next

The recorded-IQ comparison is done. New hardware captures stay gated on XTR
commissioning: identity, then gain discovery, then capture-health checks.
When real XTR data arrives, we will first check whether it shows the same
marginal-tone loss, and only then decide whether the scalloping correction
belongs in the next catalogue revision.
