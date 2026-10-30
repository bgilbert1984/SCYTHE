# Sequential RF fingerprinting for AIS (arXiv 2610.19115) — reading note

**Status:** idea source only. **Not a corpus candidate** (no data or code release).
**Do not cite the numbers yet:** v1 was superseded by a withdrawn v2 (27 Oct
2026, comment: "The authors are withdrawing this version to update the
experimental results. A revised version will be posted"). v1 (13 Oct 2026) is
the only text read. Re-read the revision before citing.
**Verified:** 2026-10-30, against the v1 HTML and the arXiv abstract page. The
v1 HTML page itself shows no withdrawal notice; the abstract page does.

## What it is

A GEM-CUSUM sequential detector over kNN distances (k=10, unnormalized
features) in a frozen 1D ResNet-18 feature space, on AIS bursts grouped by
claimed MMSI. Input is the central 512 samples of 48 kHz IQ, unit-power
normalized; the MMSI field never enters the model. One pipeline, two tests:
open-set (hardware matches any enrolled vessel?) and claimed-identity
(hardware matches the claimed vessel?). Encoder: plain cross-entropy on
enrolled vessels.

## Reported results (v1, provisional)

- Istanbul Strait, one RTL-SDR, 13 days (16–29 Sep 2026); ≥10-day window has
  67 vessels (35 enrolled, 32 unknown); headline uses 31 unknown (some had <3
  trials).
- Open-set AUROC 0.956 (95% CI 0.913–0.989) vs best per-burst 0.899.
- Refreshed threshold: 5.3% false alarms at 88.8% detection (reuse of the
  enrollment threshold: 20.9% FA). Fig. 12 caption gives five-seed means of
  5.1% FA / 86.4% detection; the gap is unexplained in the text. Quote the
  five-seed figures if citing.
- Insider spoof: 0.991 (claimed-identity) vs 0.500 (open-set).
- Median delay 16.4 bursts (caught trials only); 27.5 ms per burst, one CPU
  thread.
- Nominal drift log α + 1 = −1.354 at α = 0.095, measured −1.352 within
  session; −0.74 per burst at ≥10 days against the predicted −1.35.
- Ablations: plain cross-entropy beats ARPL/centre loss/SupCon-style
  objectives cross-day; DANN on capture time collapses cross-day rejection to
  0.731; fourteen classical impairments reach 0.562; content-free frame tail
  gives 0.873 open-set / 0.995 insider.

## Authors' stated limitations

One receiver, one site, 13 days. Insider is a real transmitter, not
synthesized or replayed. Labels are the decoded MMSIs, which an attacker
controls. A few look-alike vessels cause most failures. Consecutive trials
are correlated. The GLFormer baseline is a reimplementation (0.43M vs 0.52M
parameters, no regularization).

## What to borrow

- **Sequential per-stream decision** at a controlled false-alarm rate, with
  the two-test structure (open-set + claimed-identity). Open-set alone scores
  insider spoofs at chance, so a deployment needs both. The authors never
  evaluated the combination.
- **Drift diagnostic:** the excess of mean evidence over the closed-form
  nominal drift is a label-free recalibration monitor (Appendix A: contaminated
  calibration pushes drift positive). Applies to any long-running stream.
- **Evaluation discipline:** vessel-balanced metrics, so loud look-alikes
  cannot dominate an aggregate.
- **For transferability work:** cluster-tightening objectives hurt under
  drift; within-session time/identity confounding is something the
  fingerprint schema should detect.

## Caveats for SCYTHE

- The refresh slice must be attack-free, recognized only by claimed MMSI; in
  deployment that needs an out-of-band trust anchor. A system-design point.
- Mixed streams (spoofer plus genuine vessel under one MMSI) are untested.
- Single receiver and site: the data cannot answer the Observatory's receiver
  question.
- Nothing here runs on scythe-1; what is portable is the method, not a model.

## Release status and follow-ups

- No data or code availability statement, no data license, no ethics
  statement; MMSIs in their data are anonymized. The only license shown is the
  CC BY 4.0 badge on the paper page (the paper, not data). Nothing to put
  through the corpus intake gate.
- Worth reading next (references as listed in v1, contents unverified): Hanna
  et al. [12] (dataset considerations for open-set authorization), Alhazbi
  et al. [17] (fingerprint performance over time); AIS-specific [2]–[4];
  impersonation [33], [34].
- Last ~29.6k characters of the v1 page were read; no figures or the PDF were
  inspected.
