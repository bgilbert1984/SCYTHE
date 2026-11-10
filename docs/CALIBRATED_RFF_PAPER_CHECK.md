# Paper check: Calibrated RF-Fingerprinting Under Interference (arXiv 2609.20765v1)

Abdul-Quddoos, Li, Qian. Checked 2026-11-10 against the PDF (6 pages), the Hugging Face dataset
API and card, and the GitHub repo. A summary of the paper had been circulated; this note records
what held up and what did not. Nothing was downloaded from the dataset.

## What the paper is
Co-channel RF fingerprinting as multi-label classification: several transmitters overlap in time
and frequency, with different protocols. Conformal Risk Control (CRC) picks a probability
threshold that upper-bounds the expected false-negative rate at a chosen alpha.

## Verified against the PDF
| Item | Paper |
|---|---|
| Model | 1D CNN, 1.25M parameters, 5 conv layers + 2 dense, sigmoid outputs |
| Input | 1024-point Hann DFT, 4 channels: real, imaginary, magnitude, phase |
| Data | POWDER indoor lab; 6 TX (2 X310, 4 B210); X310 RX, gain 25; 2.425 GHz; 33.33 MS/s; 20 MHz |
| Size | 768 captures = 3 rounds x 4 gain levels x 64 on/off patterns; 20M samples each |
| Split | 5 radios trained; B210 4 held out as out-of-distribution (OOD) interference |
| Alpha | 0.05, 0.15, 0.25; candidate lambda grid step 0.01 |
| Accuracy | 0.73 to 0.97 depending on channel conditions |
| OOD cost | max recall loss 0.04; max precision loss 0.22 (per-device B210 precision up to 0.24) |
| Calibration | derived separately per gain and per channel occupancy |

## Corrections to the circulated summary
1. **"No code link" is wrong.** The PDF has none, but the dataset card links
   `TariqArshad/Calibrated_RF_Learning` (MIT per the GitHub API, last pushed 2026-09-17).
2. **The dataset is about 123 GB** (768 complex64 files of about 160 MB). Admission means a large download.
3. **"Recall lands at about 1 - alpha as designed" is too strong.** The paper reports test recall up
   to 0.05 below the target at alpha 0.25. The guarantee is on the expectation under exchangeability;
   robustness to OOD interference is measured, not proven.
4. **The collection date 2026-03-12** comes from one example capture's timestamp on the dataset
   card. The paper does not state a single collection day.
5. **Possible train/calibration/test leakage.** All three sets are "1024-sample examples sampled from
   every permutation"; the paper never says the splits are disjoint by capture. This is unstated, not
   shown, but it weakens the accuracy figures.
6. **X310 vs B210 is confounded** (the paper says so itself): the X310s are closest to the receiver,
   the B210s are 20+ ft away and nearer the OOD radio, and the two use different gain scales.
   Do not cite it as a receiver-class result.

## Licence (necessary, not sufficient)
- Dataset: MIT (card front matter and tag, checked 2026-11-10).
- Code repo: MIT (GitHub API).
- Admission to SCYTHE still goes through the normal fingerprint / characterise / publish pipeline.

## What it means for the detection gate
- CRC calibrates against ground-truth labels. The ambient band has none, so CRC cannot directly
  replace the SNR gate. It would need labelled or injected-signal calibration, redone per condition.
- It bounds misses (false negatives), not false alarms. The centre-spike skirt failure was a
  false-positive problem, which this guarantee would not have caught.
- The multi-label framing fits the ambient band, where overlapping emitters are normal.
- The distortion model names carrier leakage and spurious tones as fingerprint components,
  relevant to the signature-source thread.

## Limits of the paper
Single receiver, single indoor lab; no cross-receiver or cross-day test, so calibration transfer is
untested. Per-condition numbers are in Figures 3 to 7 (the text gives ranges only). The OOD test is
one held-out B210 in the same room, a mild OOD rather than a new environment.
