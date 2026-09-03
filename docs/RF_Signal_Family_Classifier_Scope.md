# RF Signal Family Classifier — Scope

**Status:** Phase 0 implemented 2026-09-01 · Phases 1–4 proposed, not implemented
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

## 3. Outcome vocabulary — **shipped**, `rf_signal_family.py`

Mirrors the sparse analyzer's `NULL_OUTCOMES` precedent — a null result is
rendered, never blanked — but restructured as **a small stable family plus a
reason code**, which is the shape `docs/SparseSCYTHE.md` argues for over an
ever-growing flat enumeration. The three counters stay compatible; the reason
carries the detail.

```
family       DIGITAL | ANALOGUE | UNCLASSIFIED     ← the three counters
reason_code  why this detection landed where it did
```

```
NOT_ATTEMPTED                      no classifier ran over this detection
INSUFFICIENT_WINDOW                fewer samples than the configured window
CHANNELIZATION_FAILED              occupied bandwidth not estimable at this SNR
NO_SYMBOL_CLOCK                    ran, found no significant cyclic feature
CONSTANT_ENVELOPE                  envelope variation below the test's floor —
                                   the known blind spot; DIGITAL and ANALOGUE
                                   both remain possible
NOISE_COMPATIBLE                   consistent with noise alone
STALE_WINDOW                       the verdict window does not cover this detection
ANALOGUE_DETECTOR_NOT_IMPLEMENTED  a well-formed ANALOGUE claim, refused
UNQUALIFIED_CLAIM                  a family claim without the required evidence
METHOD_NOT_REGISTERED              the claimed method has no registered decision rule
METHOD_NOT_VALIDATED               registered, but Phase 3 has not cleared it
DECISION_RULE_NOT_MET              the statistic did not pass the registered rule
SYMBOL_CLOCK_LIKE_FEATURE          the only route to DIGITAL — support, not proof
```

`ANALOGUE` is reserved and unreachable in v1: `CLAIMABLE_FAMILIES == ("DIGITAL",)`,
and a fully evidenced ANALOGUE claim is refused on the family alone, before any
other check. A detector that ran and concluded nothing may submit its own null
reason code; it may **not** choose the reason for a DIGITAL verdict.

### 3.1 What a DIGITAL claim must carry

**Structural evidence** — missing any of these is `UNQUALIFIED_CLAIM`, with the
specific refusals attached:

| Field | Refused because |
|---|---|
| `authority == DERIVED_INFERENCE` | a family is reasoned to, never observed |
| `method` | an unnamed method cannot be audited or repeated |
| `confidence ∈ [0,1]` | the existing contract at `rf_bridge.py:181` already demands it |
| `symbol_rate_hz > 0` | **DIGITAL is claimable only on a symbol clock, never on spectral shape** |
| `window_start` / `window_end` | a classification covers an interval; a frame is an instant |

A detection falling outside its own verdict window is refused as `STALE_WINDOW`,
so a verdict cannot drift forward onto detections it never analysed.

### 3.2 The decision rule — evidence-shaped fields are not evidence

The first cut of this gate demanded that a `detection_statistic` *exist* and
stopped there. An external review of 2026-09-01 showed the hole precisely:

```json
{"family": "DIGITAL", "method": "anything.v1",
 "confidence": 0.99, "detection_statistic": -999}
```

passed as DIGITAL. The statistic was present, so the gate was satisfied — and
the statistic being present is not the statistic being significant.

The registry, not the submitter, now owns the decision rule. A method must be
**registered**, must be **validated**, and the claim must **pass that method's
own rule**:

| Field | Checked against the registry |
|---|---|
| `method` | must be a registered method — an arbitrary string has no decision rule |
| `method_revision` | must match the pinned revision; a silently changed detector may not reuse a registration |
| `detection_statistic` + `decision_threshold` | must reach the registered minimum; **a submitter may not lower the bar** |
| `statistic_direction` | must match the registered sense; a submitter may not reverse the test |
| `estimated_false_alarm_probability` | must not exceed the registered maximum |
| `null_model` | must match — a statistic is significant only relative to the null it was measured against |
| `sample_count` | must reach the registered minimum window |
| `source_window_hash` | must be an algorithm-qualified lowercase hex digest (`sha256:<64 hex>`); a bare string cannot be recomputed |
| `calibration_revision` | must match; an uncalibrated confidence is decorative |

**Consequence, and the point of it.** `squared-envelope-cyclic.v1` is registered
so its rule is fixed before anyone writes code that would prefer a looser one —
but it is `REGISTERED_NOT_VALIDATED`, because Phase 3 has not run. No method is
validated, so **live DIGITAL is unreachable in this build by the gate itself**,
not by convention. Review item 7 — *validate false-digital behaviour before
enabling any live DIGITAL result* — is enforced rather than documented.

The status payload publishes `digital_reachable: false` with its reason, and the
panel renders `DIGITAL VERDICT // UNREACHABLE` so a zero in the DIGITAL column
is never mistaken for a quiet band.

### 3.3 Where a claim may come from

A second review pass asked that `estimated_false_alarm_probability` be computed
by the registered detector rather than accepted from an arbitrary caller. That
boundary already holds and is now test-locked: `signal_classification` is not in
`graphops_rf_ingest.ALLOWED_FIELDS`, and the validator rejects unknown fields, so
an HTTP caller cannot attach a family claim, a statistic, a false-alarm
probability or a window hash to an ingested frame. Such frames are retained as
`UNCLASSIFIED / NOT_ATTEMPTED`.

The status payload declares this as `classification_trust:
BRIDGE_LOCAL_DETECTOR_ONLY`. Classifications are computed in the bridge process
beside the IQ; nothing else may assert one.

`source_window_hash` is checked for **shape** only at this phase — there is no
window record to bind against until Phase 1 owns an IQ ring. When there is, this
check should additionally confirm the digest names a window the bridge actually
retained.

### 3.4 Support, not proof

A significant cyclostationary feature is strong positive evidence for digital
structure. It is not certainty: periodic analogue processes, interference,
receiver artifacts, subcarriers and channelizer leakage can all produce apparent
cyclic features. The positive outcome is therefore named
`SYMBOL_CLOCK_LIKE_FEATURE` and reads *"DIGITAL STRUCTURE SUPPORTED, NOT
PROVEN"*. Phase 3's corpus is what would make it trustworthy, not its name.

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

### Phase 0 — Evidence contract and null outcomes — **DONE**
Outcome vocabulary, admission gate, status payload, panel and ticker copy.
Shipped before any DSP. No IQ, no retention, no DSP risk.

| File | |
|---|---|
| `rf_signal_family.py` | vocabulary, method registry, decision rule, declared absences |
| `rf_bridge.py` | store delegates to the gate; `classification_reasons` + `classifier` in `stats()` |
| `scythe-web/rfClassificationOutcomes.js` | reason labels, classifier state, panel lines |
| `scythe-web/nesdrSpectrumView.js` | classification line renders the reason, carries `data-classifier-state` |
| `scythe-web/systemEvidenceTicker.js` | `RF CLASSIFIER //` line |
| `test_rf_signal_family.py` · `scythe-web/rfClassificationOutcomes.test.js` | 20 + 10 tests |
| `.github/workflows/repository-hygiene.yml` | RF test/compile steps globbed so a new module joins CI by existing |

The ticker line that prompted this scope now reads:

```
RF DETECTIONS // DIGITAL 0 · ANALOGUE 0 · UNCLASSIFIED 0 · RETAINED EVENTS 0
CLASSIFIER STATE // NOT_IMPLEMENTED · PHASE 0 SHIPS THE EVIDENCE CONTRACT ONLY.
  NO CHANNELIZER AND NO SYMBOL-CLOCK DETECTOR ARE RUNNING, SO EVERY RETAINED
  DETECTION IS UNCLASSIFIED BY CONSTRUCTION AND NOT BY MEASUREMENT.
ANALOGUE DETECTOR // NOT_IMPLEMENTED · ANALOGUE REQUIRES A POSITIVE DETECTOR...
```

An undeclared classifier block renders as `UNDECLARED`, never as a working one:
a build that forgets to declare its classifier must not read as a build that has
one.

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

## 5. Carried forward from the 2026-09-01 review

Addressed in this pass: CI coverage (globbed, so it cannot recur), the
registered-method/threshold/calibration contract (§3.2), support-not-proof
phrasing (§3.3), and the broken `sandbox:` references in `docs/SparseSCYTHE.md`.

Still open, and deliberately not done unilaterally:

### 5.1 The bounded IQ ring needs a lifecycle, not just a size *(Phase 1)*

The review is right that a size limit is not a boundary. Samples from two
incompatible signal-chain regimes must never enter one classification window.
Proposed contract, for sign-off with Q1 below:

```python
class BoundedIQRing:
    def clear(self, reason): ...
    def append(self, block): ...
    def snapshot_for_channelizer(self, window): ...
```

Mandatory clear reasons: `RETUNE`, `SAMPLE_RATE_CHANGE`, `GAIN_CHANGE`,
`DISCONNECT`, `RECONNECT`, `CAPTURE_OWNER_CHANGE`, `ORCHESTRATOR_STOP`,
`CLOCK_DISCONTINUITY`. Memory-only, fixed-size, continuously overwritten, never
serialized, never logged, never returned by a status API, excluded from crash
dumps where practical. This aligns with `signal_chain_hash`: a ring that spans a
retune is the same error as comparing products across antennas.

### 5.2 DIGITAL/ANALOGUE is one axis doing four jobs *(before Phase 4)*

"Digital" and "analogue" can each refer to the RF waveform, the information
encoding, the baseband content, or the service. FM broadcast carries analogue
audio *and* a digital RDS subcarrier; CPFSK is an FM-like waveform carrying
digital symbols. Proposed internal model, with the three counters retained for
compatibility:

```
MODULATION            AM_LIKE · FM_LIKE · FSK_LIKE · PSK_LIKE · QAM_LIKE
                      · MULTICARRIER_LIKE · UNKNOWN
INFORMATION STRUCTURE SYMBOL_CLOCK_DETECTED · NO_SYMBOL_CLOCK_DETECTED
                      · CONSTANT_ENVELOPE_BLIND_SPOT · NOT_ATTEMPTED
                      · INSUFFICIENT_EVIDENCE
PROTOCOL              UNRESOLVED · CANDIDATE · CONFIRMED_BY_DECODER
```

This is a contract change to something shipped one commit ago and it interacts
with Q1 and Q2, so it is proposed rather than applied.

### 5.3 `docs/SparseSCYTHE.md` should become an ADR — **done**

Split into three ADRs along the document's own seams, because it braids three
decisions and one ADR would have flattened two of them into an appendix of the
first: [0001](adr/0001-sparse-recovery-validation.md) sparse-recovery validation,
[0002](adr/0002-polarimetric-channel-diversity.md) physical channel diversity,
[0003](adr/0003-rf-emission-tracking-hierarchy.md) the emission-tracking
hierarchy. The source is retained unchanged and is non-normative: the ADRs are the
decision surface, `SparseSCYTHE.md` is design history, code and tests are what
runs.

### 5.4 Hash shape is validated; hash *ownership* is not *(Phase 1)*

Phase 0 validates that `source_window_hash` is an algorithm-qualified lowercase
hex digest of a declared length. That is a syntax check. A correctly shaped but
entirely invented digest still passes, so the field currently proves that a caller
knows the format — not that the window exists.

This is acceptable only while the sole claim path is bridge-local and no window
record exists to check against. Once `BoundedIQRing` ships, the bridge must issue
the window identifier and compute the digest itself, and admission must verify:

- the digest names a window the live ring actually holds;
- `window_start` / `window_end` match that record;
- `sample_count` matches the samples in it;
- `signal_chain_hash` matches the regime the window was captured under;
- the registered detector consumed *that* window, not a re-derived one;
- the record has not expired and does not straddle a ring `clear()` boundary.

Until every one of those holds, a window hash is a label, not a binding. The
`BoundedIQRing` change is what makes the check possible, which is another reason
it lands before the detector rather than beside it.

---

## 6. Open questions for the operator

1. **Approve the bounded IQ ring** (§2.2)? First retention of raw IQ beyond one block.
2. **256 ms default window** — accept 4.19 MB and 3.9 Hz α resolution?
3. ~~**Ship Phase 0 alone first?**~~ **Done 2026-09-01.**
4. **False-DIGITAL gate at <0.1% on noise** — right threshold, or stricter?
5. **Split the DIGITAL/ANALOGUE axis** (§5.2) into modulation / information
   structure / protocol before Phase 1, or carry the single axis to Phase 4?
   Splitting later means migrating a published contract; splitting now costs a day
   and touches nothing that ships yet.

Q1 and Q2 gate Phase 1. Q4 gates Phase 3. Q5 is cheapest to answer before Phase 1
and gets more expensive with every phase that ships against the current shape.
