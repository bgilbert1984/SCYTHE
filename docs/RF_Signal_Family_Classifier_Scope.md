# RF Signal Family Classifier — Scope

**Status:** proposed, not implemented
**Consumes:** `signal_classification` at `rf_bridge.py:181` (gate already exists and is strict)
**Governs:** `RF DETECTIONS // DIGITAL n · ANALOGUE n · UNCLASSIFIED n`

---

## 1. The two findings that shape everything below

### 1.1 Only a symbol clock justifies DIGITAL

Every cheap discriminator people reach for — spectral flatness, steep shoulders,
constant occupied bandwidth, low envelope variance — is **circumstantial**. A flat
brick-shaped spectrum is equally consistent with an unresolved multi-carrier, a
wideband noise source, or a nearby switching supply.

There is exactly one piece of evidence that is *positive and falsifiable*: a
**detectable symbol clock**. A digital transmission is cyclostationary — its
statistics repeat at the symbol rate — and that produces a discrete line at
cycle frequency `α = R_s`. Analogue modulation has no symbol clock and produces
no such line. A symbol-rate estimate is a claim that can be checked, refuted, and
carried forward as evidence.

**Decision: DIGITAL is claimable only on a significant cyclic feature with an
associated symbol-rate estimate.** No flatness heuristic may set the family.

### 1.2 ANALOGUE must not become the leftover bucket

This is the failure mode that would make the counter actively wrong.

The natural symbol-clock test is the **squared-envelope cyclic spectrum**: take
`|x(t)|²`, remove its mean, FFT it, look for a discrete line. It is cheap
(`O(N log N)`) and it works well. It has a specific blind spot — **constant-envelope
modulations have no envelope variation to analyse**, so the test finds nothing.

The constant-envelope set includes:

| Signal | Family | Squared-envelope test |
|---|---|---|
| GMSK / GFSK / CPFSK | **DIGITAL** | finds nothing |
| P25 C4FM, DMR, dPMR | **DIGITAL** | finds nothing |
| FM broadcast, NOAA weather | **ANALOGUE** | finds nothing |

A trunked digital radio system and an FM broadcast carrier look **identical** to
this detector. If "not digital" were allowed to mean ANALOGUE, SCYTHE would
confidently label P25 as analogue voice. That is a manufactured claim of exactly
the kind this system exists to refuse.

**Decision: ANALOGUE requires its own positive detector, and v1 does not ship one.**
`ANALOGUE` stays structurally 0, and the status payload says *why* — the absence
is declared, not merely displayed.

---

## 2. What the pipeline can actually see

`IQFFTProcessor._transform(block, timestamp)` is the only place complex IQ exists.
`ingest_frame` downstream sees the FFT product dict, never samples. So the
classifier **must live inside the bridge process** and emit only a bounded verdict.

### 2.1 One FFT block is far too short

At 2.048 MS/s with `fft_size=4096`, one block is **2.00 ms**. Symbol-rate
estimation needs hundreds of symbols:

| Window | Samples | Blocks | α resolution | IQ ring |
|---|---|---|---|---|
| 128 ms | 262,144 | 64 | 7.8 Hz | 2.10 MB |
| **256 ms** | **524,288** | **128** | **3.9 Hz** | **4.19 MB** |
| 512 ms | 1,048,576 | 256 | 2.0 Hz | 8.39 MB |
| 1024 ms | 2,097,152 | 512 | 1.0 Hz | 16.78 MB |

At the proposed 256 ms default: 1200 baud → 307 symbols, 9600 baud → 2458 symbols.
Comfortable.

**Classification cadence is therefore not FFT cadence.** A verdict covers a window;
a frame is an instant. Every verdict must carry `classified_window_start` /
`classified_window_end`, and a frame older than a declared staleness bound carries
**no** classification rather than a stale one.

### 2.2 This is the first time IQ is retained past one block — needs sign-off

Channelizing around a detection requires the samples that produced it, so the
classifier needs a **bounded process-local IQ ring** (~4.19 MB at 256 ms).

This does not breach the project rule. Raw IQ still never enters browser
transport, GraphOps model context, Ollama Cloud, or permanent graph storage. But
it is a new retention of raw IQ in memory and should be an explicit, reviewed
decision rather than something that arrives inside a DSP commit.

### 2.3 A detection is one signal; the capture is 2.048 MHz wide

An envelope test run across the whole span mixes every emitter in band. To
classify a *specific* detection the classifier must first isolate it: estimate
occupied bandwidth around the peak, digitally downconvert, low-pass, decimate.
**Channelization is a prerequisite, not an optimisation.**

---

## 3. Outcome vocabulary

Mirrors the sparse analyzer's `NULL_OUTCOMES` precedent — a null result is
rendered, never blanked.

```
NOT_ATTEMPTED           classifier disabled or never ran
INSUFFICIENT_WINDOW     fewer samples than the configured window
CHANNELIZATION_FAILED   occupied bandwidth not estimable at this SNR
NO_SYMBOL_CLOCK         ran, found no significant cyclic feature
CONSTANT_ENVELOPE       envelope variation below the test's floor — the known
                        blind spot; DIGITAL and ANALOGUE both remain possible
NOISE_COMPATIBLE        consistent with noise alone
DIGITAL                 significant cyclic feature + symbol-rate estimate
```

`ANALOGUE` is reserved and unreachable in v1.

Status payload declares its own limits, matching `claims_withheld` convention:

```python
"classifier_outcomes":  [...],
"claims_withheld":      ["analogue_family", "constant_envelope_digital",
                         "modulation_order", "emitter_identity"],
"analogue_detector":    "NOT_IMPLEMENTED",
"analogue_detector_note": "ANALOGUE REQUIRES A POSITIVE DETECTOR. IT IS NOT "
                          "INFERRED FROM THE ABSENCE OF A SYMBOL CLOCK.",
```

---

## 4. Phases

### Phase 0 — Evidence contract and null outcomes *(~0.5 day)*
Outcome vocabulary, status payload, ticker copy. **Ships before any DSP** and
immediately upgrades the ticker from a bare `0` to a stated reason. No IQ, no
retention, no risk.

### Phase 1 — Channelizer *(~1–2 days)*
Occupied-bandwidth estimate (99% power or −20 dB) around a detection peak; DDC,
low-pass, decimate to that bandwidth. Bounded IQ ring. Testable standalone
against synthetic signals at known centre/bandwidth.

### Phase 2 — Symbol-clock detector *(~2–3 days)*
Squared-envelope cyclic spectrum on the isolated channel. Significance test
against a noise null (CFAR-style threshold on peak-to-sidelobe). Emits symbol-rate
estimate + detection statistic. Declares `CONSTANT_ENVELOPE` when envelope
variance is below the floor.

### Phase 3 — Validation *(~2–3 days; the corpus is the work)*
Required before DIGITAL ships at all.

- **Synthetic labelled corpus:** BPSK/QPSK/QAM/FSK at controlled SNR and symbol
  rates, vs AM/FM/SSB, vs constant-envelope digital, vs **noise-only controls**.
- **Real known emitters** as an independent check: ADS-B 1090 (digital),
  FM broadcast 88–108 (analogue), NOAA weather (analogue FM).
- **Gating metric is not accuracy.** It is the **false-DIGITAL rate on noise and
  on analogue inputs**, because a false DIGITAL manufactures a claim. Proposed
  gate: <0.1% of noise-only windows yield DIGITAL.
- **Confidence calibration.** The contract already demands `confidence ∈ [0,1]`,
  so we owe a calibrated number: map the detection statistic through a curve
  fitted on the corpus so confidence ≈ P(DIGITAL | statistic), checked with a
  reliability diagram. An uncalibrated score in that field is decorative.

### Phase 4 — Positive ANALOGUE detector *(deferred, ~1 week+)*
FM/AM demodulation, then speech-band statistics on the baseband: energy in
300–3400 Hz, syllabic envelope near 3–5 Hz, silence gaps. Only covers *voice*
analogue. Start only after Phase 3 gives a clean operating point.

---

## 5. Open questions for the operator

1. **Approve the bounded IQ ring** (§2.2)? First retention of raw IQ beyond one block.
2. **256 ms default window** — accept 4.19 MB and 3.9 Hz α resolution?
3. **Ship Phase 0 alone first?** It makes the zeros self-explanatory with no DSP risk.
4. **False-DIGITAL gate at <0.1% on noise** — right threshold, or stricter?
