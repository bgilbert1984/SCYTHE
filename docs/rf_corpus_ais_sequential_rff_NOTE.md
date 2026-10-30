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
- Other references: [4], [12], [17], [33], [34] read in full; [3] known from
  its abstract record only; [2] unread (see the sections below).
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

## References [33], [34] (read in full) and [2]–[4] (2026-10-30)

### [33] Albousayri & Hamdaoui — "Replicating the Signature"

arXiv 2607.05549 (2026), 10 pages.

- **Attack:** synthesizes signals replicating a target device's hardware
  impairments, sent from a different transceiver, via unsupervised impairment
  estimation plus signal-processing-based generation; payload is controllable.
  Three access models (full, partial, none); needs no knowledge of the victim
  classifier.
- **Results:** BLE testbed, USRP B210 receivers, over the air (LoS and NLoS).
  Targeted attack success above 97% across all three access models; the paper
  claims over 80% better than baselines. At 31 enrolled devices the minimum
  success falls to 39.6–50.3% (still easy for untargeted impersonation).
- **Defense lead:** phase-derivative features were the most robust against
  this attack; recurrent networks were most sensitive to low SNR.
- **Release:** states BLE datasets (31 IoT devices, varied environments,
  receivers, channels) are "made publicly available"; no URL or license in the
  text, so unverified. Would need the license-first intake gate.

### [34] Xu, Li, Peng, Hu — "Collusion-Driven Impersonation Attack"

arXiv 2509.22154 (2025), 9 pages.

- **Attack:** attacker synchronizes with a colluding receiver to match the
  legitimate transmitter's centralized log power spectrum (CLPS); a VAE-based
  generator with a multi-objective loss produces the spoofed signals.
- **Results:** over 95% success across channel conditions (SNR, K-factor), in
  **simulation only**: 10 simulated devices distinguished by I/Q imbalance,
  55,000 train + 5,500 test samples. No over-the-air evidence.
- **Future work (authors):** adversarial training and active defenses at the
  legitimate receiver.
- **Release:** no code or data link found in the text.

### [3] and [2] (not read in full)

- **[3]** Li et al., Ocean Engineering 366:127784 (2026), doi
  10.1016/j.oceaneng.2026.127784. Known from the TRID abstract record only:
  FedAMN, federated learning for AIS transmitter identification without
  sharing raw IQ; 99.75% on "AIS-10" (15 samples/class, 10 clients), 99.89% on
  "USRP-10". Code: github.com/xmuLdz/FedAMN holds trainers only; GitHub
  reports no license and no dataset is included; no data links in the record;
  (c) Elsevier. No usable AIS corpus.
- **[2]** Deng et al., IEEE TIFS 18:2303–2317 (2023): no arXiv or open version
  found, paywalled; unread. Secondhand (a 2026 paper, unverified): GLFormer, a
  lightweight transformer, 96.3% (transient) / 89.4% (steady-state) over 50
  vessels at a Xiamen wharf. AIS paper v1 says it has no public code.

### [4] Chen, Li, Sun — vessel identity verification from AIS physical features

Frontiers in Marine Science 13 (2026), doi 10.3389/fmars.2026.1858626; open
access (CC BY); read in full from the PDF.

- **Method:** AIS baseband IQ segments, feature fusion, metric-constrained
  embedding (center loss + consistency constraint); hybrid open-set rule =
  distance to class centers + softmax confidence gating; no unknown-class
  training samples.
- **Data:** about 6 h from one RTL-SDR and one antenna setup near Yangshan
  Port, Shanghai; 20-minute sessions; 21 MMSI classes; 8:1:1 stratified split.
  Independent hold-out collected later: 12 vessels (10 known, 2 unknown),
  used only for final cross-acquisition evaluation.
- **Results:** closed-set accuracy 0.9747; abstract: rejection of unknown
  vessels above 90%. Open-set (Table 10): softmax-threshold AUROC 0.8549 /
  FPR@95TPR 0.7218; distance-threshold 0.9022 / 0.4516; hybrid **0.9434 /
  0.2298**, TPR@FPR 5% = 0.7213. Ablations: without center loss AUROC 0.8351;
  without consistency 0.9030. Under cross-acquisition shift the main loss is
  conservative rejection of known vessels, not confusion among known classes.
- **Authors' limits:** future work on real sea areas, larger MMSI/device sets,
  domain adaptation, uncertainty calibration; threshold reliability and
  feature stability under channel/device-state change dominate open-set
  performance.
- **Release:** "The raw data… will be made available by the authors, without
  undue reservation": on request, no URL, no license. Fails the intake gate.
- **vs 2610.19115:** same task at much smaller scale (6 h / 21 classes vs 13
  days / 67 vessels); a single later hold-out, not a ≥10-day window; no
  insider-spoof test. Its 0.9434 AUROC is unknown-vessel rejection only.

### Takeaways

- [4] is a second independent study supporting physical-feature AIS identity
  verification, but its numbers are not comparable to the sequential paper's.
- The AIS paper's insider test uses a real enrolled transmitter, so its 0.991
  does not cover a synthesized or replayed fingerprint. [33] (real hardware,
  high success) and [34] (simulation only) show that attacker class is
  feasible; record it as an untested threat in any deployment plan.
- [33] points to phase-derivative features as a more robust choice; [33]'s BLE
  data is the only potential corpus here and is unverified.
- None of [2]–[4] provides a usable AIS IQ corpus ([4] data on request only).
