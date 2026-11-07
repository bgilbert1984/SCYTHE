# SNR-regime diagnostic for the v2-Hann detector — plan — DRAFT

**Status:** plan only. Nothing run, no code written, no data touched, no hardware.
The sequencer (`seq-7.2-1-hann`) is read-only throughout.
**Drafted:** 2026-11-07. **Docs only.**
**Origin:** FuseAMC (arXiv 2611.05937; checked against the PDF 2026-11-07) shows an
SNR-conditioned view of two estimators and where each fails. This plan borrows
that *view*, not the model.

## 1. The question

Where, as a function of how far a feature sits above the floor and where it
falls on the bin grid, does `detect_candidates` + `measure_persistence` stop
agreeing with ground truth, and would any other classical estimator cover that
region?

What is **not** being claimed: FuseAMC is a modulation classifier on
RadioML frames. The v2-Hann detector is already purely classical (Hann FFT,
median-of-repeats floor, local-maximum rule, log-power parabola; one-bin
retention). There is no learned low-SNR leg to fuse with, so "fusion" is a later
question at most. If it is ever asked, FuseAMC's own Table V says a plain
posterior average held up better than the learned combiner under channel shift
(0.505 vs 0.470), so test the average first.

## 2. What "SNR" means here

The axis is the feature's **excess over the tuning-local median, in dB** (the
quantity the 10 dB `PLAN_PERSISTENCE_MARGIN_DB` is compared with). It is not the
channel SNR that `rf_detector_contract.snr_stratum` bins (`BELOW_0 … ABOVE_30`,
plus `UNRESOLVED`). Those are different quantities; do not reuse the strata
names for this axis.

The second axis is the tone's **offset from the nearest bin centre**, 0–0.5 bin
(bin = 3.90625 Hz at 2.048 MS/s, 0.256 s windows). It belongs in the diagnostic
because the existing write-up (`hann_half_a_bin_blog_post.md`) already found it
matters: a Hann window loses about 1.4 dB at half a bin, and retention reads one
bin.

## 3. What is already known (so the plan can state a hypothesis)

From the blog post, on two excess levels only (nominal 12 and 21 dB, 30 trials
per offset):

- the 7.2 interpolator is unbiased within ±0.035 bin;
- at 12 dB with the exact bin, persistent fraction falls from about 0.77 on-bin
  to 0.13 at half a bin; at 21 dB it is 100% at every offset;
- reading the better of the two adjacent bins does not help at half a bin.

**Hypothesis (falsifiable, fixed before running):** the failure region is a band
of roughly 10–14 dB excess, widest at offsets above 0.3 bin, and is a
**retention** effect, not a detection effect. Detection should stay near 1 down
to the 10 dB margin plus noise scatter. If the grid shows detection failing
first, or failure at high excess, the hypothesis is wrong and that is the
finding.

## 4. Design

**Geometry (fixed by the sequencer, not negotiable):** 2.048 MS/s,
8 windows × 524,288 complex samples per visit, Hann window, 8 repeats.

**Synthetic injection grid.** One coherent tone in complex Gaussian noise.

| Axis | Values |
|---|---|
| On-bin excess | 6 to 30 dB, 1 dB steps (25 levels) |
| Offset from bin centre | 0 to 0.5 bin, 0.05 steps (11 levels) |
| Trials per cell | 200 (the blog used 30) |
| Noise-only cells | same trial count, for the false-positive rate |

Excess is calibrated through the estimator's own floor: generate, measure the
on-bin excess at offset 0, and scale until the measured value hits the target.
Do not trust a nominal amplitude.

**Per cell, record:** detection rate (a candidate within ±1 bin of truth);
persistent rate (from `measure_persistence`'s own verdict, not re-derived);
frequency error in bins; excess-estimate bias; and for noise-only cells the
candidates and persistent features per visit.

**Reporting rule:** per cell and per excess band, **never one aggregate** (the
roadmap's hard rule for the Observatory matrix). A grid summarized as a single
accuracy is not an output of this diagnostic.

**Cost:** about 55,000 visits at 8 × 524,288 samples. Not estimated here;
time one visit first.

## 5. Pre-committed criteria (numbers for the owner to fix before the run)

FuseAMC's useful habit was fixing margins before the final run and reporting the
miss. Proposed, not decided:

- **Failure region:** any contiguous set of cells where the persistent rate is
  below `P_FAIL` (proposed 0.95) at a tone the generator placed above the
  10 dB margin.
- **Second-estimator win:** it must raise the persistent rate inside the failure
  region by at least `Δ_MIN` points with a paired-bootstrap 95% lower bound
  above zero, **and** must not raise the noise-only false-positive rate above
  the baseline's.
- A miss is reported with its numbers, as FuseAMC reported its one seed at
  +0.0497.

## 6. Candidate second estimator (offline comparison only)

The natural classical contender is a retention read that is insensitive to
where the tone falls on the grid: for example the power summed over the peak
bin and its two neighbours, or a flat-top window. This is a **different
retention estimator**. Adopting it would change §5.21 and needs its own
protocol amendment; this diagnostic only measures it, in scratch, beside the
frozen one on identical windows. Nothing in `rf_spur_retention_estimator.py` or
the sequencer changes.

## 7. Real-data anchors, and a correction to "no new data needed"

- The only recorded IQ at the sequencer's geometry that the repo's own evidence
  names is the **terminated-input set**: six 120 s captures, 50 Ω load, NESDR
  SMArt v5 (serial 14530058), 100 / 101 / 100.5 MHz, 58 visits each, 348 in all.
  It is the real noise-only false-positive reference. It also holds two usable
  cases: the strong ~100.8 MHz feature (persistent in 58/58, a high-excess
  control) and the +825 kHz line at 11–12 dB (34 vs 31 persistent visits across
  revisions, a real marginal case).
  **Location (confirmed 2026-11-07):** `/var/tmp/scythe-sweep/thermal_windows/`
  on this host, not in the repo. Six `THERMAL_NO_INPUT_epoch{1..6}_20260930_*.iq`,
  each 491,520,000 bytes (= 120 s × 2.048 MS/s × 2 B, uint8 IQ), with a `.json`
  sidecar each (centre, receiver, termination, md5). Centres: epochs 1–4 at
  100.0 MHz, epoch 5 at 101.0 MHz (retune), epoch 6 at 100.5 MHz. All six md5s
  were re-computed and match their sidecars. Caveats: `/var/tmp` is scratch
  (it can be cleaned), the files are in no repo and have no committed hash
  record, and the sidecars say the gain was the rtl_tcp default (no `-g`), so
  gain is undeclared.
- **`docs/evidence/xtr-baseline-2026-10-11/` is not this set.** It is a different
  receiver (Nooelec SMArt XTR, E4000, a different site), holds only derived
  results (4096-point FFT spectra, 8 segments of capture/8, 500 Hz bins), and
  its raw `.iq` captures are not in the repo. It cannot drive this detector,
  whose geometry is 8 × 524,288 samples. It remains a useful separate control
  for receiver-internal artifacts (its +2.5 kHz E4000 feature is tuning-dependent).
- **RadioML and TrevTron do not fit this detector.** RadioML 2016.10a frames are
  128 samples (2018.01A: 1,024); the detector needs 8 windows of 524,288. TrevTron
  is not in this repo. So the earlier note that replication "needs no new data,
  TrevTron plus RadioML 2018 suffice" holds for the AMC-style comparison, not for
  this detector. Here the synthetic grid needs no new data; the real anchor is
  the terminated-input set.
- `components/corpus-observatory/README.md` still says (2026-10-24) the Hann
  detector bundle is not in this repo. `seq-7.2-1-hann` is, since PR #132. And
  `detector_interface.md` proposes `classify(window) -> {label, score}`, whereas
  `detect_candidates` takes eight windows and returns candidates, so the
  Observatory interface does not describe this detector yet.

## 8. A line worth a test, not a result

FuseAMC's most damaging shift was carrier offset (−0.238 at 0.01/sample). For
this detector the nearest analogue is a frequency error moving a tone across the
bin grid, i.e. the offset axis above: at 100.8 MHz, 1 ppm is 100.8 Hz, about
25.8 bins. Whether an XTR ppm bound is also a bin-offset budget depends on how
the declared baseband frequency is set (retention reads the frequency it is
given). Check that against the sequencer before claiming it.

## 9. Outputs and provenance

- Scratch only until reviewed; a result, if run, goes under `docs/evidence/` with
  a SHA-256 for every file, as the dedirock equivalence record did.
- Record: sequencer revision (`seq-7.2-1-hann`, source hash from
  `docs/evidence/dedirock-equivalence-2026-10-12/RECORD.txt`), Python, NumPy and
  FFT backend, generator seed.
- Optional: repeat on dedirock and compare, as for the 36-fixture equivalence run.

## 10. Open decisions (owner's call)

1. `P_FAIL` and `Δ_MIN`; the hypothesis wording in §3.
2. Whether §6 (the second estimator) is in scope, or the diagnostic stops at the
   failure map.
3. Whether to commit a hash record for the six terminated-input captures (and
   copy them off `/var/tmp`) before any run; they live only there.
   Related: whether to take a fresh terminated capture at a **declared gain**
   (the existing sidecars say rtl_tcp default, no `-g`, so gain is undeclared).
   scythe-1 has the terminator fitted; it is needed only for this, for extra
   terminated visits beyond the 348, or for an optional tuning-dependent-artifact
   check. It is not needed for the synthetic grid. Not verified: that scythe-1's
   dongle is serial 14530058, the one used for the six captures. No hardware
   touched.
4. Whether to run on one host or two.
5. Nothing runs until a go-ahead.
