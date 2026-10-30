# Corpus provenance notes (RadioML family)

Running notes on what is known about the quality of specific corpora, kept apart from the trust
dimensions so each entry names its source and how far it was checked. Entries feed the *label
reliability* and *provenance* dimensions in `trust_dimensions.md`. They are not scores.

| Corpus | Note | Source | Checked |
|---|---|---|---|
| RadioML 2018.01 | Panoradio's notes document flaws; the official site marks it "erratic". Synthetic (GNU Radio), so the negative-control rule in `fingerprint_schema.md` applies. | roadmap, Phase 1 | Hub listing verified 2026-10-24; the Panoradio note is as the roadmap quotes it |
| RML2016.10a | Described as having known acquisition artifacts. A scaling experiment reached ~0.69 train and ~0.47 test accuracy; the conjugate lag product operator lifted held-out accuracy to ~0.49. | Crasmaru, arXiv:2610.08592v3 | Numbers read in the paper's HTML. The "known artifacts" wording is not in the text I read; it comes from the summary that prompted this entry |
| RML22 | Exists as an artifact-corrected successor to RML2016.10a; ~0.50 held-out accuracy in the same paper. | arXiv:2610.08592v3 (accuracy); "artifact-corrected" from the prompting summary | Accuracy read in the paper; the successor/correction claim not independently checked |

Caveats:
- The accuracies are one author's results with one model family. They show a ceiling for that model
  on that corpus. They do not rank corpora and are not evidence of label error.
- The paper's "VT-CNN2 parity" claim has no baseline number in the text. Do not carry it forward.
- Before relying on the RML22 correction, read the RML22 release notes and record what was changed.

Open: confirm both "known artifacts" and "artifact-corrected" against the corpus authors' own
documentation, then replace the "Checked" cells.
