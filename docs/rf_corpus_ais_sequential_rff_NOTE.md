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
- Worth reading next (references as listed in v1): AIS-specific [2]–[4];
  impersonation [33], [34] (contents unverified). [12] and [17] are looked up
  below.
- Last ~29.6k characters of the v1 page were read; no figures or the PDF were
  inspected.

## References [12] and [17] (read in full 2026-10-30)

Full text read from the arXiv PDFs (2005.00954, 2305.05285); titles, authors
and venues match the v1 bibliography. Neither is an intake candidate.

### [12] Hanna, Karunaratne, Cabric — open-set transmitter authorization

IEEE TCCN 7(1):59–72, 2021 (venue per the v1 bibliography); arXiv 2005.00954;
earlier SPAWC 2020 version arXiv 2002.07777.

- **Data:** WiFi preambles from the ORBIT testbed grid; 163 Atheros
  5212/9220/9280 transmitters; one USRP N210 receiver; channel 11 (2462 MHz),
  25 Msps, 1 s captures, >300,000 packets; first 256 IQ samples, unit
  average magnitude. Five captures on five days; the first was two months
  earlier with fewer transmitters; the last day is held out for testing.
- **Methods:** OvA, OpMx, DClass (open-set); Disc, AutoEnc (anomaly detection).
- **Headline:** 98% same-day / 80% different-day outlier-detection accuracy is
  for **OvA** with 10 authorized + 50 known unauthorized transmitters, not a
  generic result.
- **Day shift:** about 15% drop in AUC/accuracy for all methods; OvA/OpMx lose
  about 15% accuracy, AutoEnc about 5%. The drop comes from authorized
  signals being rejected, not missed outliers. Mitigation offered: collect
  authorized-transmitter data over several days; the paper says further
  approaches are needed to close the gap.
- **Split discipline:** transmitters randomly partitioned into authorized /
  known-unauthorized / unseen sets (10 realizations, same for all methods);
  70% of authorized + all known-unauthorized for train/val (80/20), remaining
  30% of authorized + unseen for the same-day test; the different-day test
  uses the held-out last day only (a true day-level holdout).
- **Release:** no code link, license, or data statement found in the text.

### [17] AlHazbi, Sciancalepore, Oligeri — Day-After-Tomorrow

ACSAC 2023, pp. 439–450, doi 10.1145/3627106.3627192; arXiv 2305.05285.

- **Setup:** USRP X310 radios, ResNet50, BPSK, six transmitters, fixed
  receiver. DS1: wired, power-cycled between measurements (78 measurements,
  13 runs, 3 days). DS2: wired, six non-stop 3-day measurements (18 days, 0.256
  Msps), no power cycling. DS3: wireless, 900 MHz, 10 m non-line-of-sight
  indoors with people moving (72 measurements, 4 days, three runs per day,
  power-cycled between measurements).
- **Findings:** power cycling the radios between training and testing cuts
  accuracy to about 0.5 (DS1 wired: 0.3–0.6, variance about 0.3); without power
  cycling it is about 1 in the controlled case. So the effect is not only
  channel variability.
- **Mitigation:** converting IQ samples to images before classification takes
  wireless accuracy from about 0.45 to 0.85 and roughly halves variance.
- **Limitations (authors):** only USRP X310 tested; cites other work seeing
  the same effect on N210, B210 and LoRa. The image-based approach shares the
  known weakness to an attacker injecting a small number of samples (exact
  bound garbled in the text extraction; check the PDF before quoting it).
- **Release:** "available on request" by email to the authors; no license.
  Informal permission only, so it fails the intake gate (same as NetSTAR).

### Takeaways for SCYTHE

- Record **receiver and transmitter power-cycle and firmware state** per
  capture, not only the date; [17] shows power cycling is a primary driver of
  cross-day collapse, alongside the channel.
- WiSig spans several days; whether radios were power-cycled between its
  captures is unknown, so do not read its cross-day drop as pure channel effect.
- [12]'s day-level holdout is the model for leakage checks; a packet-level
  split (or separating the paired equalized copies in WiSig SingleDay) leaks.
- Both support the AIS paper's point that same-session accuracy overstates
  deployed performance.
