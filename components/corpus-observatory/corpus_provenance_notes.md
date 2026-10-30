# Corpus provenance notes (RadioML family)

Running notes on what is known about the quality of specific corpora, kept apart from the trust
dimensions so each entry names its source and how far it was checked. Entries feed the *label
reliability* and *provenance* dimensions in `trust_dimensions.md`. They are not scores.

| Corpus | Note | Source | Checked |
|---|---|---|---|
| RadioML 2018.01 | Panoradio's notes document flaws; the official site marks it "erratic". Synthetic (GNU Radio), so the negative-control rule in `fingerprint_schema.md` applies. | roadmap, Phase 1 | Hub listing verified 2026-10-24; the Panoradio note is as the roadmap quotes it |
| RML2016.10a | Accuracy only: a scaling experiment reached ~0.69 train and ~0.47 test accuracy; the conjugate lag product operator lifted held-out accuracy to ~0.49. Per an IEEE DataPort entry it emulates multipath fading, LO offsets and frequency-selective fading. "Known acquisition artifacts" is **unconfirmed**: no source found. | Crasmaru, arXiv:2610.08592v3; IEEE DataPort listing | Numbers read in the paper's HTML. The "known artifacts" wording is not in the text I read and no other source states it (searched 2026-10-31) |
| RML22 | A successor generated from GNU Radio code derived from the RadioML/DeepSig generator. The early-exits paper (arXiv:2308.11100) says it was "created based on" RML2016.10a and addresses "some of its shortcomings", without listing them. ~0.50 held-out accuracy in the CNet paper. Whether it is "artifact-corrected" is **unconfirmed**. Documented differences: 420,000 signals, 10 classes, 21 SNR levels (-20 to +20 dB, 2 dB steps), against 220,000 signals, 11 classes, 20 SNR levels for RML2016.10a. | venkateshsathya/RML22 README; arXiv:2308.11100; arXiv:2610.08592v3 (accuracy) | Generator README read 2026-10-31: no release notes (Releases empty), no named RML2016.10a flaws, no stated licence. Sizes are from a secondary comparison table. The RML22 paper itself was not read |

Caveats:
- The accuracies are one author's results with one model family. They show a ceiling for that model
  on that corpus. They do not rank corpora and are not evidence of label error.
- The paper's "VT-CNN2 parity" claim has no baseline number in the text. Do not carry it forward.
- RML22 has no release notes. Do not describe it as artifact-corrected until the RML22 paper or its
  authors say what was changed.
- The RML22 generator repo states no software licence. Treat reuse of its code as unlicensed, and
  check the dataset's own terms (a Google Drive copy is linked from the README) before ingesting it.
- The generator can produce five variants (clean, AWGN, clock effects, fading, all artifacts), so
  "RML22" needs the variant named when a report cites it.

Open: read the RML22 paper for what it changed relative to RML2016.10a, and the dataset terms.
Confirm or drop "known acquisition artifacts" for RML2016.10a against the DeepSig documentation.
