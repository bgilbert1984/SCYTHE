# RF Signal Family Classifier — Scope

**Status:** Phases 0, 1a–1d and 2 implemented · Phase 3 (validation) is next,
sized by §5.18 at 66 732 null windows · Phase 4 deferred
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
rendered, never blanked — but restructured as **three independent axes plus a
reason code**, which is the shape `docs/SparseSCYTHE.md` argues for over an
ever-growing flat enumeration.

```
modulation             UNRESOLVED | AM_LIKE | FM_LIKE | FSK_LIKE | PSK_LIKE | QAM_LIKE
information_structure  NOT_ATTEMPTED | NO_SYMBOL_CLOCK_DETECTED | SYMBOL_CLOCK_LIKE_FEATURE
protocol               UNRESOLVED | CANDIDATE | CONFIRMED_BY_DECODER
reason_code            why this detection landed where it did
```

`DIGITAL` / `ANALOGUE` / `UNCLASSIFIED` survive as the three panel counters, but
they are now a **derived compatibility summary** (`derive_family`, authority
`DERIVED_SUMMARY`), never an observation and never submittable. See §3.5.

Two of the three axes have no detector at all, and are pinned at their defaults:

| Axis | Gate | State |
|---|---|---|
| `modulation` | needs a modulation classifier over an isolated channel | `NOT_IMPLEMENTED` |
| `information_structure` | needs a registered, validated method | Phase 2 target |
| `protocol` | `CANDIDATE` needs a hypothesis source; `CONFIRMED_BY_DECODER` needs decoder evidence | both `NOT_IMPLEMENTED` |

```
NOT_ATTEMPTED                        no classifier ran over this detection
INSUFFICIENT_WINDOW                  fewer samples than the configured window
CHANNELIZATION_FAILED                occupied bandwidth not estimable at this SNR
NO_SYMBOL_CLOCK_DETECTED             ran, found no significant cyclic feature
CONSTANT_ENVELOPE                    envelope variation below the test's floor —
                                     the known blind spot; DIGITAL and ANALOGUE
                                     both remain possible
NOISE_COMPATIBLE                     consistent with noise alone
STALE_WINDOW                         the verdict window does not cover this detection
FAMILY_NOT_DIRECTLY_CLAIMABLE        a summary was submitted as an observation
ANALOGUE_DETECTOR_NOT_IMPLEMENTED    a well-formed ANALOGUE claim, refused
MODULATION_DETECTOR_NOT_IMPLEMENTED  a modulation claim with no classifier behind it
PROTOCOL_HYPOTHESIS_NOT_IMPLEMENTED  a CANDIDATE with no hypothesis source
DECODER_NOT_IMPLEMENTED              CONFIRMED_BY_DECODER without decoder evidence
UNQUALIFIED_CLAIM                    an axis claim without the required evidence
METHOD_NOT_REGISTERED                the claimed method has no registered decision rule
METHOD_NOT_VALIDATED                 registered, but Phase 3 has not cleared it
METHOD_WRONG_AXIS                    registered against a different axis
DECISION_RULE_NOT_MET                the statistic did not pass the registered rule
SYMBOL_CLOCK_LIKE_FEATURE            the only route to DIGITAL — support, not proof
```

A refusal about **capability** is checked before a refusal about **evidence**: a
perfectly documented `PSK_LIKE` claim is still refused, and it is refused with
`MODULATION_DETECTOR_NOT_IMPLEMENTED` rather than with a complaint about its
evidence, because "your evidence was incomplete" would be a false explanation.

A detector that ran and concluded nothing may submit its own null reason code; it
may **not** choose the reason for a positive verdict.

### 3.5 The summary is derived, and ANALOGUE is not derivable

`derive_family` has exactly one rule that fires:

```python
if information_structure == "SYMBOL_CLOCK_LIKE_FEATURE":
    return "DIGITAL"
return "UNCLASSIFIED"
```

The axis split makes a second rule extremely tempting, and it is wrong:

```
FM_LIKE + NO_SYMBOL_CLOCK_DETECTED  ->  ANALOGUE     # NO
```

P25 C4FM is `FM_LIKE` and carries a symbol clock a squared-envelope test cannot
see, so it reports `NO_SYMBOL_CLOCK_DETECTED` for a reason that has nothing to do
with being analogue. That rule labels encrypted public-safety digital voice as
analogue voice. ANALOGUE stays unreachable until a positive analogue detector
asserts it directly.

The same reasoning governs `_structure_for_reason`: `CONSTANT_ENVELOPE` — where
the detector ran and hit its known blind spot — leaves `information_structure` at
`NOT_ATTEMPTED`, **not** at `NO_SYMBOL_CLOCK_DETECTED`. Recording a blind spot as
a negative result is precisely how a constant-envelope digital signal would
quietly accumulate evidence of being analogue.

A method is registered against one axis. A symbol-clock detector has no standing
to assert a modulation, and `METHOD_WRONG_AXIS` enforces that even for a method
that has passed Phase 3 validation on its own axis.

### 3.1 What a positive information-structure claim must carry

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

### Phase 1a — Bounded IQ ring — **DONE** *(`rf_iq_ring.py`)*
The first raw-IQ retention beyond one FFT block, shipped alone: no channelizer,
no DSP. `BoundedIQRing` + `IQWindow`, 39 tests covering all ten acceptance-gate
conditions in §5.5. Fixed 524,288-sample `complex64` allocation, made once;
`invalidate` zeroes and advances `configuration_epoch`; `append` invalidates on
its own when `signal_chain_hash` or `sample_rate_hz` changes, so the invariant
does not depend on every call site remembering it. Windows carry a
bridge-issued id and a bridge-computed digest; `verify_window` distinguishes
`WINDOW_NOT_ISSUED`, `DIGEST_MISMATCH`, `EPOCH_CHANGED` and `WINDOW_EVICTED`.
Neither the ring nor a window can be pickled, `repr`d into a log, or serialized
with its samples, and a process whose `SCYTHE_PROCESS_ROLE` is anything other
than `orchestrator` is refused **before** the allocation is made.

### Phase 1b — Channelizer *(standalone)* — **DONE** *(`rf_channelizer.py`)*
A pure `IQWindow → ChannelizedProduct` transformation. It imports `rf_iq_ring`
and nothing else from the capture path; `rf_bridge.py` is untouched by this
change, which is the seam that lets DSP and lifecycle be reviewed separately.

Coarse occupancy (stage 1) → channel selection (stage 2) → DDC, Kaiser-windowed
FIR, decimation. Eleven declared outcomes, ten of them refusals. 31 tests.

Two implementation notes worth keeping:

- **The occupancy walk runs over a Welch-averaged spectrum, not a periodogram.**
  A single periodogram of noise-like modulation has ~5.6 dB of bin-to-bin
  variation, so a −20 dB walk crosses the floor in the first spectral null and
  reports a 40 kHz signal as 234 Hz wide. That was the first behaviour observed
  and it is exactly the failure that would have produced confident, precise,
  wrong bandwidths. Averaging plus a three-bin run-length before declaring an
  edge fixes it.
- **Decimation is bounded by two things.** Nyquist sets the largest ratio that
  does not alias; the window length sets the largest ratio that still leaves a
  usable number of output samples. Taking only the first turns a narrow candidate
  into a 39-sample product and then reports the *window* as too short, blaming
  the wrong thing.

### Phase 1c — Bridge integration — **DONE** *(`rf_iq_retention.py`)*
`IQRetentionOwner` allocates, feeds, clears and publishes exactly one ring.
`rf_bridge.py` gains 51 lines and no DSP. 35 tests.

The ring is allocated **lazily, on the first block of samples**, which is what
makes `iq_retention_active` mean what §5.6 requires: an allocation exists and
samples are arriving. A build that imports `rf_iq_ring` and never captures
reports `NONE_BEYOND_ONE_FFT_BLOCK` with `inactive_reason: NO_SAMPLES_YET`.

Four reasons there may be no ring, each reported rather than blanked:
`DISABLED_BY_CONFIGURATION` (`SCYTHE_RF_IQ_RETENTION`, a kill switch, default
on), `NOT_CAPTURE_OWNER`, `PROCESS_ROLE_REFUSED`, `NO_SAMPLES_YET`.

Lifecycle wiring:

| Bridge event | Reason | Effect |
|---|---|---|
| `tune()` / centre-frequency change | `RETUNE` | clear |
| sample-rate change | `SAMPLE_RATE_CHANGE` | **reallocate** |
| sample-type change | `SIGNAL_CHAIN_CHANGE` | **reallocate** |
| IQ socket established | `RECONNECT` | clear |
| IQ socket lost | `DISCONNECT` | clear |
| `stop()` | `ORCHESTRATOR_STOP` | clear |

A rate or decode change alters both the required capacity and the meaning of
every retained sample, so the allocation is discarded rather than resized.
Within a configuration the ring is still allocated once and never grown.

`GAIN_CHANGE`, `DIRECT_SAMPLING_CHANGE` and `CLOCK_DISCONTINUITY` are published
as `unwired_invalidation_reasons`: this bridge has no gain control, no
direct-sampling control and no clock-discontinuity detector, so nothing calls
them. Declaring the gap beats letting the reason list imply full coverage.

One bridge action produces several clears — a retune clears for `RETUNE`, then
the stream restart clears for `ORCHESTRATOR_STOP` and again for `RECONNECT`. The
ring remembers only the last, which would leave the audit blaming a reconnect for
a retune's clear, so the owner keeps a bounded `invalidation_history`.
Suppressing the later clears would be worse: they really did happen.

### Phase 1d — Channelizer wired to the capture path — **DONE**
The owner issues windows; the channelizer never reaches into the ring. A
channelizer that periodically read "whatever is newest" out of a mutable buffer
would produce products whose contents depend on thread timing, and thread timing
is not evidence.

```text
decoded IQ -> append -> complete window issued -> verify_window()
           -> epoch and signal-chain check -> channelize -> bounded product
```

Frame-driven, because the FFT frame already carries the coarse peak that points
at a target — but only as a pointer. The channelizer still runs its own
occupancy estimate and records that candidate in its own fields, so the frame's
peak never becomes the selection truth.

**The lock is deliberately not held across the DSP.** Holding it would stall a
retune arriving on the API thread for the length of the transform, and would
make the retune-during-channelization race unobservable — the very race the
verification exists to catch. The window is a copy, so a concurrent
`invalidate()` cannot corrupt it; it can only make it stale, and staleness is
precisely what `verify_window` reports.

**One product per non-overlapping window.** `WINDOW_OVERLAP` is `NONE`, so a
fresh capacity's worth of samples must arrive before another window is issued.
Without that, a fast frame rate would emit near-identical products from
overlapping spans and the product count would describe the polling rate rather
than the signal.

Publication happens first and unconditionally; channelization is layered on top
and must never delay or suppress a spectrum product. A channelizer exception is
counted in `channelizer_errors` and swallowed. A *refusal* is not an error: it is
a verdict the channelizer reached, and it is recorded as a product.

`channelizer_state` is `INTEGRATED_NO_CLASSIFICATION`. Each state this field has
held named the missing half rather than the whole thing — `NOT_IMPLEMENTED`
understated a tested module, `AVAILABLE_NOT_INTEGRATED` understated a wired one,
and `INTEGRATED` alone would overstate products that nothing believes.

### Phase 2 — Symbol-clock detector — **DONE** *(`rf_symbol_clock.py`)*

**Cleared 2026-09-03**, and recorded in §5.12. This heading said *"not started"*
until 2026-09-14, alongside a second copy of itself — the duplicate is why the
staleness survived: a reader correcting one would leave the other. Only the
entry conditions were gated on Q4; the detector itself ships in shadow as
`squared-envelope-cyclic.v1`, `REGISTERED_NOT_VALIDATED`, `SHADOW_NO_PROMOTION`,
`digital_reachable: false`.

What it computes:
Squared-envelope cyclic spectrum on the isolated channel. Significance test
against a noise null (CFAR-style threshold on peak-to-sidelobe). Emits symbol-rate
estimate + detection statistic. Declares `CONSTANT_ENVELOPE` when envelope
variance is below the floor.

### Phase 3 — Validation — **next; sized by §5.18 at 66 732 null windows**
Required before DIGITAL ships at all. The estimate below predates the Bonferroni
correction in §5.12 and the finding in §5.18; no duration is quoted, because the
corpus is the work and most of it is now known to be larger than first written.

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

### 5.2 DIGITAL/ANALOGUE was one axis doing four jobs — **done**

"Digital" and "analogue" can each refer to the RF waveform, the information
encoding, the baseband content, or the service. FM broadcast carries analogue
audio *and* a digital RDS subcarrier; CPFSK is an FM-like waveform carrying
digital symbols. Split into the three axes in §3, with the counters retained as a
derived summary.

The split was made **before** Phase 1 rather than after Phase 2, on the reasoning
that it costs about a day now and touches nothing that has shipped a detector,
whereas after Phase 2 it would be a migration of a contract with live producers.

It also removes a genuine hazard rather than only tidying a field. Under one
label, P25 C4FM forces a wrong answer: the waveform is FM-like, the information
is symbol-structured, and a single value cannot say both. Under two axes they are
two rows and neither has to lie.

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

Until every one of those holds, a window hash is a label, not a binding.

**Status after Phase 1a.** The ring now issues window identities and computes
digests itself, and `verify_window` answers "was this issued here, does the
digest match, is it the same epoch, and do the samples still exist". Six of the
seven checks above are therefore available; the seventh — that the registered
detector consumed *that* window — needs a detector to exist. What has **not**
happened is the wiring: `rf_signal_family._check_window_hash` still validates
shape only, because binding it to the ring is a policy change to the admission
gate and deserves its own review rather than riding along with the mechanism.

### 5.5 The granted raw-IQ authority *(operator approval, 2026-09-02)*

Q1 and Q2 were approved on narrow terms, recorded here because the scope of the
permission is the load-bearing part:

```
PROCESS-LOCAL · VOLATILE · FIXED-CAPACITY
NON-PERSISTENT · NON-TRANSPORTABLE · NON-MODEL-CONTEXT
BRIDGE-OWNED · INVALIDATED ON SIGNAL-CHAIN CHANGE
```

> This is permission for a DSP working buffer, not permission for an IQ archive.

Binding properties, none of which are defaults to be revisited casually:

- fixed capacity allocated once, never grown; `complex64`, ~4.19 MB for 256 ms at
  2.048 MS/s;
- never serialized, pickled, JSON-encoded, logged or exposed — and never present
  in exceptions, diagnostics, MCP responses or Ollama capsules;
- cleared on retune, sample-rate change, gain-regime change, direct-sampling
  change, disconnect, shutdown and `signal_chain_hash` change;
- evicted regions overwritten, allocation best-effort zeroed on explicit clear;
- only the orchestrator-owned bridge may instantiate it; child processes and the
  Spectrum MCP receive derived products only;
- consumers borrow immutable or copy-isolated windows, never the writable ring;
- **no disk fallback, swap-oriented buffering or crash-dump facility** is
  authorized under this approval.

256 ms is a **registered detector configuration**, not a universal optimum:
524,288 samples, 4,194,304 bytes, 1/0.256 = 3.90625 Hz nominal cycle-frequency
resolution. Later validation may authorize longer or overlapping windows without
silently changing `squared-envelope-cyclic.v1`. Windows are non-overlapping
initially — overlap multiplies computation and correlates verdicts before there
is evidence it buys detection performance.

Phase 1 does not merge unless tests demonstrate all ten of:

1. capacity never exceeds 524,288 complex samples;
2. wraparound preserves chronological ordering;
3. incomplete windows return `INSUFFICIENT_WINDOW`;
4. every invalidation reason clears the ring;
5. pre-retune and post-retune samples can never share a window;
6. digests are bridge-generated and reproducible;
7. forged or expired window IDs cannot validate;
8. neither API serialization nor exception paths expose samples;
9. child-process mode cannot instantiate the ring;
10. sustained input maintains bounded memory.

### 5.6 The integration gate — what changes when the ring goes live

A class existing is not active retention. `iq_retention` stays
`NONE_BEYOND_ONE_FFT_BLOCK` until an allocated ring is actually receiving
samples, and it changes in the same commit that makes that true:

```json
{
  "iq_retention": "PROCESS_LOCAL_BOUNDED_RING",
  "iq_retention_active": true,
  "configured_retention_ms": 256,
  "effective_retention_ms": 256,
  "capacity_limited": false,
  "capacity_samples": 524288,
  "raw_iq_exposed": false,
  "channelizer_state": "AVAILABLE_NOT_INTEGRATED"
}
```

That commit is Phase 1c and contains no DSP. What it must make reviewable, with
nothing else in the diff to obscure it: who owns the ring, when it is allocated
and closed, which lifecycle events call `invalidate` and with which reason, how
`SCYTHE_PROCESS_ROLE` is enforced at the allocation site, and the status
transition above.

**Configured is not effective.** The allocation is fixed at 524,288 samples and
256 ms is a request against that ceiling; the two agree only at 2.048 MS/s. At
the device's nominal 2.4 MS/s the same allocation holds 218.453 ms, so a single
`retention_ms` would be a precise-looking number for a duration the ring does
not have. Both are published, and `capacity_limited` says which bound applied.
The nested ring block reports `effective_retention_ms` only, because a ring has
no configured request to fall short of.

**`AVAILABLE_NOT_INTEGRATED`, not `NOT_IMPLEMENTED`.** From Phase 1b the
channelizer exists and is tested; only the capture binding is missing. The state
names the missing half, because `NOT_IMPLEMENTED` understates it as badly as
`ACTIVE` would overstate it.

### 5.7 Two channelizer traps that are worth remembering

**The channelizer must not grade its own selection.** An occupied-bandwidth
estimate used to *choose* a channel cannot then be reported as evidence of how
well the signal *fits* it. `candidate_center_hz` / `candidate_bandwidth_hz`
record the coarse pass; `channel_center_hz` / `channel_bandwidth_hz` record what
was cut; `occupied_bandwidth_basis` is `SAME_WINDOW_AS_SELECTION`, and a test
asserts that the string `INDEPENDENT_WINDOW` appears nowhere in the module — no
code path may promote its own estimate.

**Tuning offset and carrier offset are different quantities.**
`tuning_offset_hz` records where the DDC was pointed; `frequency_offset_hz` is
where the carrier turned out to be within the resulting channel. Publishing only
their sum would fold selection error into the measurement and make a mistuned
channel look like a frequency-shifted emitter. The carrier estimate is a
power-weighted centroid over the occupied region rather than the peak bin,
because the peak bin of a noise-like band is a property of that noise
realisation, not of the emitter.

### 5.8 Q4 resolved — the false-DIGITAL gate is a manifest, not a decimal

**Approved 2026-09-02.** *Two numbers below were superseded on 2026-09-03 by
§5.12's simultaneous-confidence accounting: the confidence is per-bound
99.61538%, not 95%, and zero-failure n is 5,561, not 3,000. They are left as
written because §5.12 is the record of the change, and because the consequence
for the strata minima is still undecided — see §5.18.*

Maximum false-DIGITAL rate `0.001`, but the promotion rule is the *bound*, not
the observation:

```text
one-sided 95% upper confidence bound  <=  0.001        PROMOTES
observed false positives / trials     <=  0.001        DOES NOT
```

Zero false positives in 100 trials is not evidence of a sub-0.1% rate; by the
rule of three, zero failures needs roughly 3,000 independent null trials just to
place the 95% upper bound near 0.1%. A ratio of small integers is a hopeful
decimal wearing the costume of a measurement.

Target **≥ 10,000 null windows**, stratified, with a Wilson or exact
Clopper–Pearson upper bound reported **per stratum**:

```text
thermal / no input            adjacent-channel interference
stationary analogue FM        DC contamination
AM                            gain steps
constant-envelope digital     retune transients
dropped frames, timing gaps   overloaded / clipped input
receiver spurs                two-signal collisions
```

The aggregate must pass **and** no safety-critical stratum may hide behind a
large pile of easy thermal-noise windows. Constant-envelope digital is the
stratum that matters most here: it is the P25 C4FM trap in corpus form, and a
detector that fails it while passing on thermal noise has learned to recognise
quiet rather than to recognise structure.

Consequence for the ring: a stratum like *retune transients* or *dropped frames*
can only be constructed if the invalidation reasons that describe those events
are actually wired. `GAIN_CHANGE`, `DIRECT_SAMPLING_CHANGE` and
`CLOCK_DISCONTINUITY` are still declared-but-unwired, so the corresponding
strata cannot yet be built honestly.

---

### 5.9 SNR measured the filter, not the signal — **fixed 2026-09-03**

Found the first time the channelizer ran on live hardware. A product on a
broadcast FM station reported `snr_db: 106.505`. Against synthetic ground truth
the estimator read **108.7 dB for a true 20 dB channel**: a ~88 dB overstatement
whose slope was right and whose level was fiction.

The cause was the noise reference. The estimator took the floor as the median of
every bin outside the occupied region — but most of those bins lie outside the
channelizer's *own* passband, where its 90 dB FIR has already crushed them.
Instrumented on the filtered path (±256 kHz span, passband edge ±125.6 kHz,
occupied ±101.0 kHz):

```text
median of ALL out-of-occupied bins   -68.91 dB   <- what was used
  110-125 kHz ( 60 bins)              17.27 dB   <- the actual noise floor
  125-180 kHz (220 bins)             -10.18 dB   <- FIR transition
  180-256 kHz (304 bins)             -77.04 dB   <- FIR stopband
```

524 of 620 reference bins were in the transition or stopband, so the median
landed in the stopband and the published figure measured filter rejection. The
median — chosen so a second emitter could not quietly raise the floor — is
precisely what guaranteed the stopband won, because stopband bins outnumber real
noise bins. A defence against one contaminant admitted a larger one.

**The definition now in force** (`snr_basis:
OCCUPIED_EXCESS_POWER_OVER_LOCAL_PASSBAND_NOISE_V1`), over occupied bins `O` and
clean reference bins `R`:

```text
N0        = median of the LINEAR power over R      (not the median of dB)
P_noise   = N0 * |O|
P_signal  = max( sum(P_k for k in O) - P_noise, 0 )
SNR_dB    = 10 log10( P_signal / P_noise )
```

Subtracting the expected in-band noise barely moves a strong signal and is the
whole answer near a threshold. Measured on the estimator in isolation:

| true | excess-power | error | total-power | error |
|-----:|-------------:|------:|------------:|------:|
| −10.0 | −9.903 | +0.10 | 0.430 | **+10.43** |
| −5.0 | −4.938 | +0.06 | 1.215 | +6.22 |
| 0.0 | 0.044 | +0.04 | 3.040 | +3.04 |
| 10.0 | 10.031 | +0.03 | 10.449 | +0.45 |
| 20.0 | 20.027 | +0.03 | 20.077 | +0.08 |
| 40.0 | 40.025 | +0.02 | 40.032 | +0.03 |

Without the subtraction a −10 dB channel reads as +0.43 dB: noise counted as
signal, exactly where a detector's PFA would be quoted.

**A reference bin must be** inside the flat passband, outside the occupied
region, outside a guard of `NOISE_REFERENCE_GUARD_BINS = 3` around its edges,
outside the declared DC exclusion, finite and positive, and from the same Welch
spectrum and source window. The flat edge is `PASSBAND_REFERENCE_FRACTION =
0.85` of the cutoff, measured on the shipped 129-tap Kaiser β=8.6 design:

```text
0.80 x cutoff    0.00 dB      0.95 x cutoff   -1.95 dB
0.85 x cutoff   -0.02 dB      1.00 x cutoff   -5.98 dB
0.90 x cutoff   -0.37 dB      1.30 x cutoff  -94.00 dB
```

Budget: `>= 32` total, `>= 8` per side. One-sided estimation is permitted only
when a single side clears the full 32, and is never silent —
`noise_reference_sides: LEFT_ONLY | RIGHT_ONLY` with `snr_quality:
DEGRADED_ONE_SIDED`. Left and right counts and their median-power disagreement
are published on every product; no threshold is set on the disagreement, so
`REFERENCE_BINS_ASYMMETRIC` stays reserved rather than emitted.

**Measurement failure is not transformation failure.** An SNR that cannot be
defended does not refuse the channelization:

```text
outcome          CHANNELIZED
snr_db           null
snr_reason_code  INSUFFICIENT_CLEAN_REFERENCE_BINS
```

`SNR_REASON_CODES` names seven conditions; `_EMITTED_SNR_REASON_CODES` names the
three that are actually produced (`INSUFFICIENT_CLEAN_REFERENCE_BINS`,
`OCCUPIED_POWER_NOT_ABOVE_NOISE`, `CHANNEL_EDGE_LIMITED`). The other four are
reserved names, not claims that a detector exists, and the status payload splits
`snr_reason_codes_emitted` from `snr_reason_codes_reserved` so the distinction is
readable rather than remembered.

**Historical boundary.** `SNR_MEASUREMENT_REVISION =
passband-local-excess-power.v1` is part of the product digest, so a product under
the old definition cannot share an identity with one under the new. The prior
basis is declared in `SUPERSEDED_SNR_BASES` as `INVALID — NOT COMPARABLE ... NO
CORRECTION FACTOR EXISTS`. **No −88 dB correction may be applied to a retained
value**: the error depends on filter rejection, spectrum geometry and occupancy,
and only looked constant because one sweep held those fixed. Nothing persisted
the old figures — the ring and its products die with the process — so there is no
stored telemetry to quarantine, only a definition that must never be re-derived.

**A consequence for Phase 2.** The coarse selection walks 20 dB down from the
peak, so it refuses below roughly 20 dB in-channel SNR
(`OCCUPIED_BANDWIDTH_UNRESOLVED`) and the SNR estimator is never reached there.
An end-to-end sweep therefore *cannot* characterise the estimator near a
detection threshold; `test_rf_channelizer_snr.py` drives `_estimate_snr` directly
for that reason. Whether a symbol-clock detector should ever see windows the
channelizer will not measure is a Phase 2 question, not a fixed one.

**The reference budget is geometry-dependent, and often fails.** With
`CHANNEL_MARGIN = 1.25` the channel is only 25% wider than the occupied region,
and 0.85 of that leaves roughly `0.03 x bandwidth` per side. Whether 32 bins fit
depends on the Welch resolution, which depends on window length. At the
production 524,288-sample window a 200 kHz signal yields 22 bins per side and
resolves; the same signal in a 262,144-sample window yields 8 and does not. This
is reported honestly rather than papered over, but it means **`snr_db: null` is a
common and correct outcome, not a rare one**. Widening `CHANNEL_MARGIN` to buy
reference room would change every product hash and the DC and edge refusal rates,
so it is not done here.

### 5.10 A default feedline was physical evidence nobody gave

`SDRPP_ANTENNA_ID=nesdr-smart-uhf` was adopted at startup and the feedline
defaulted to `direct` — `DIRECT TO SMA`. Nothing in a receive-only path can tell
a mast screwed onto the SMA from the same mast on 2 m of RG58: no reflectometer,
no bias tee to sense a load, no identity conductor. The default was therefore
publishing a cable path as though it had been observed.

`undeclared` is now the first entry in `FEEDLINES` and the default in both the
Python catalogue and its `scythe-web/rfAntennaDeclaration.js` mirror, with
`feedline_label: FEEDLINE UNDECLARED`, `feedline_length_m: null` and
`feedline_authority: UNDECLARED`. A stated feedline still records as
`OPERATOR_DECLARED`. The antenna and the cable are separate declarations because
they are separate parts with separate losses, and one being known says nothing
about the other.

Note that `signal_chain_hash` at the time covered sensor, antenna, sample type
and sample rate — **not** the feedline. Two metres of RG58 is a real insertion
loss and arguably belongs in the chain identity; folding it in would change every
existing hash, so it was recorded as an open question rather than done quietly.

**Both halves of that open question have since been closed, and this paragraph is
kept for the history rather than as current fact.** Revision `v2` folded the
feedline into the chain identity; revision `v3` folded in the telescopic mast
extension (§5.17). Each bump set `PRIOR_SIGNAL_CHAIN_REVISION_COMPARABLE = False`
rather than reinterpreting older hashes.

### 5.11 Phase 2 entry conditions — **met 2026-09-03**

Six conditions, from the 2026-09-03 review.

**1. Push the SNR fix.** `10f6c4b` on `main`.

**2. Separate transformation from occupancy and SNR.** `to_dict()` now publishes
three verdicts, each fact appearing once:

```json
{"transformation": {"outcome": "CHANNELIZED", "channelized": true},
 "occupancy":      {"bandwidth_hz": null, "reason_code": "OCCUPANCY_EXCEEDS_FLAT_PASSBAND"},
 "snr":            {"snr_db": null, "snr_reason_code": "INSUFFICIENT_CLEAN_REFERENCE_BINS"}}
```

`ChannelRequest.channel_bandwidth_hz` lets a channel be cut when the coarse
occupancy walk finds no width, with `channel_selection_basis:
OPERATOR_REQUESTED_WIDTH` recording that the width was asked for rather than
measured. Without it the split would be cosmetic — below ~20 dB the walk fails
and no channel exists to hand a detector.

**A second instance of the same defect, found while doing this.** With a
requested width, the occupancy walk at −10 dB reported **285 kHz occupied inside
a 250 kHz channel**. The channel's own FIR skirt falls 20 dB before a weak signal
does, so the walk closed on the transition band and published the channelizer's
passband as the emitter's bandwidth. The same flat-passband boundary the noise
reference uses now bounds the walk: an edge beyond `0.85 × channel_bandwidth / 2`
yields `OCCUPANCY_EXCEEDS_FLAT_PASSBAND` and no width. `OCCUPANCY_REASON_CODES`
is its own namespace — the similarly named `OUTCOMES` entry is a *selection*
failure (no width to cut to, no channel), these are *measurement* failures on a
channel that was cut correctly.

| true SNR | transformation | occupancy | snr |
|---:|---|---|---|
| −10 dB | CHANNELIZED | null · EXCEEDS_FLAT_PASSBAND | null |
| 0 dB | CHANNELIZED | null · EXCEEDS_FLAT_PASSBAND | null |
| 10 dB | CHANNELIZED | null · EXCEEDS_FLAT_PASSBAND | null |
| 20 dB | CHANNELIZED | 201000.0 Hz | 20.543 dB |
| 30 dB | CHANNELIZED | 201000.0 Hz | 30.539 dB |

**3. Signal-chain manifest v2.** `signal_chain_manifest()` builds
`scythe.rf-signal-chain.v2`; `canonical_signal_chain_bytes()` serialises it with
sorted keys and no incidental whitespace; the hash is over those bytes. The
manifest is retained beside the hash and published as `signal_chain`, so a chain
identity can be read rather than reverse-engineered from an argument order. It
carries antenna, **feedline**, gain, direct sampling, `bias_tee: NOT_FITTED` and
`clock_quality: MODEL_DECLARED_0_5_PPM_TCXO`, each with its authority. Declaring
the feedline changes the hash — the system noticing the analogue instrument
changed. `signal_chain_hash_revision: v2`, `prior_revision_comparable: false`; v1
hashes are not reinterpreted. `SDRPP_FEEDLINE_ID` drives both the manifest and the
antenna bootstrap, so one variable cannot leave the two disagreeing.

**4. Channelizer wired to the bridge, baseband process-local.** Done in Phase 1d
(`63af4de`), before this review. Verified again live: 84 products, 0 errors, no
`ChannelizedProduct` field holds an array.

**5. Detector input contract frozen.** `rf_detector_contract.py`, written before
any detector exists so it constrains one rather than describing it. Admission is
`transformation.outcome == CHANNELIZED` and reads nothing else; occupancy and SNR
are covariates. `qualified_snr_db()` returns `None` rather than a default, and a
reason code outranks a value. `snr_stratum()` makes `SNR_UNRESOLVED` its own
stratum rather than a bucket edge, because in operation it is a large share of
products. Seven named `PROHIBITED_INFERENCES` cover each way a null becomes a
number — `SNR_AS_ZERO`, `SNR_AS_NEGATIVE_INFINITY`, `SNR_AS_WEAK`,
`SNR_AS_ADMISSION`, `OCCUPANCY_AS_SYMBOL_RATE`, `TRANSFORMATION_AS_DETECTION`,
`COVARIATE_AS_CONFIDENCE`.

**6. Q4 executable.** `rf_validation_manifest.py`. The gate is the exact
Clopper–Pearson one-sided 95% upper bound, with Wilson reported beside it and
never instead:

```text
0 failures in    100 trials  ->  0.029513      30x the approved rate
0 failures in  1 000 trials  ->  0.002991
0 failures in  2 996 trials  ->  0.000999      the rule of three, exactly
0 failures in 10 000 trials  ->  0.000300
1 failure  in 10 000 trials  ->  0.000474
```

The binomial tail is summed in log space: at 10,000+ trials `comb(n, k) · p**k`
overflows before its factors cancel, and a gate that breaks at the trial counts
the rule of three demands is not a gate. All twelve approved strata are declared
with their own minimums; both the aggregate bound **and** every stratum bound must
pass, so a large pile of thermal noise cannot carry a failing safety-critical
stratum — a test asserts exactly that. `GAIN_STEPS` and
`DROPPED_FRAMES_TIMING_GAPS` report `NOT_BUILDABLE`, block promotion, and refuse
to accumulate trials at all, because `GAIN_CHANGE` and `CLOCK_DISCONTINUITY` are
declared invalidation reasons that nothing calls.

**Not done, and deliberately.** `CHANNEL_MARGIN` stays at 1.25 for this revision.
Widening it to manufacture reference room would change adjacent-signal exposure,
filter design, DC behaviour and collision probability, and belongs in a new hashed
configuration measured against the Phase 3 evidence rather than chosen now.

### 5.12 Phase 2 — shadow detector, and what building it found

**Cleared 2026-09-03.** `squared-envelope-cyclic.v1` is implemented in
`rf_symbol_clock.py` and runs in shadow: `REGISTERED_NOT_VALIDATED`,
`SHADOW_NO_PROMOTION`, `digital_reachable: false`. All eight required outcomes
are declared, and `CONSTANT_ENVELOPE` maps to `NOT_ATTEMPTED` — never
`NO_SYMBOL_CLOCK_DETECTED`, because a test that could not run has no negative to
report. Exactly one outcome maps to a measured negative, and a test asserts that.

**GAIN_CHANGE and CLOCK_DISCONTINUITY are wired**, so both blocked strata are
buildable. `SDRPPBridge.set_gain` drives the tuner over rtl_tcp's control channel
and `IQRetentionOwner.set_gain_db` raises `GAIN_CHANGE`;
`ClockContinuityMonitor` compares decoded sample count against elapsed time on
every append. Only `DIRECT_SAMPLING_CHANGE` remains unwired, and
`wired_invalidation_sources` names what calls each of the other two so "wired" is
checkable rather than asserted.

The clock thresholds were measured before they were chosen. On the live NESDR
stream, 2-second arrival windows swing by up to **4.3%** — TCP and USB buffering,
not the oscillator — while cumulative drift over 20 seconds was **0.007%**. A 2%
instantaneous tolerance would have fired continuously on a healthy stream and
invalidated the ring for it. So the check is cumulative over 10 s at 1%, with a
separate 1 s gap detector, and `GAP` (transport) is kept distinct from `DRIFT`
(rate) because a corpus labelling one as the other would mislabel its own strata.
Live: zero discontinuities.

**Q4 gained simultaneous-confidence accounting.** Thirteen bounds each at 95% give
the family about 51% coverage, so the budget is split: `family_alpha 0.05`,
`tested_bound_count 13`, `per_bound_alpha 0.0038461538`, confidence 99.6154%,
zero-failure *n* rising from 3,000 to **5,561**. The promotion corpus is frozen
by `PromotionCorpusLock` over method revision, threshold, preprocessing and the
strata set — the last because the Bonferroni denominator depends on it. Without a
lock a report is `NO_LOCK_EXPLORATORY` and cannot promote; with one, a changed
threshold or preprocessing yields `CONFIGURATION_CHANGED_AFTER_FREEZE`.

One measured surprise: **Wilson is not a safe substitute at the corrected
confidence.** The usual claim that it is the less conservative of the two holds at
95% and reverses at 99.6154%, where it sits *above* the exact bound throughout
this gate's regime (0/10000: 0.000710 vs 0.000556). Harmless in a number nobody
gates on, and the reason the gate is exact.

**Provenance is separate from eligibility.** `transformation.outcome ==
CHANNELIZED` remains the only measurement-state admission condition and reads no
covariate. Structural validation is a second layer that refuses to look at a
mapping at all: it requires the typed `Channelization`, which cannot arrive over a
socket because it refuses to serialize. A body carrying
`{"outcome": "CHANNELIZED"}` is refused as `TYPED_PRODUCT`, not as an ineligible
product — reading `outcome` out of a decoded body would answer a question about
JSON as though it were a question about a capture. The product digest is now
recomputable from the product alone (`target_frequency_hz` was in the digest and
not on the record, so the digest was a label rather than a binding).

---

Building the detector surfaced four defects, three of them in things already
declared. This is the part worth reading.

**1. The registered threshold would have fired on every noise window.** 8.4 was
registered against no implementation. On an unaveraged periodogram the null
peak-to-median for pure noise is **~18**, from extreme-value statistics alone —
the maximum of *N* exponential bins over their median is about `ln(N)/ln 2`, which
for 129,500 bins is 17.0, measured 18.4. Welch averaging is not an optimisation
here; it is the difference between a statistic and a draw from an extreme-value
distribution. With 63 averages the null is mean 1.55, max 1.81 over 300 windows.
The threshold is now **2.5**, declared `PROVISIONAL_FROM_NULL_CHARACTERISATION`
with the characterisation attached: 300 windows can bound a rate near 1%, nowhere
near 0.001, and that is what the frozen promotion corpus is for.

**2. The registered minimum sample count was unreachable.** 262,144 samples,
against a channelizer that yields 16k–175k from a 524,288-sample window depending
on decimation. The detector would never have run on a real product. Measured, the
null is flat from 32,768 upward because the Welch segment scales with the window
and the average count stays at 63; what a short window costs is a higher *lowest
detectable symbol rate*, so that is published per verdict as `search_floor_hz`
rather than hidden behind one global minimum. A `NO_SYMBOL_CLOCK` from a short
window is a weaker negative than one from a long window, and the verdict says so.

**3. A global noise floor let a broad hump beat a line.** The squared envelope of
a linearly modulated signal has a discrete line at the symbol rate *and* a broad
continuous component from the random data, running from DC to roughly the symbol
rate. Against a global median the hump wins: on a 4 kHz raised-cosine signal the
six strongest bins were all within 1800–2400 Hz at near-equal power while the true
line at 4000 Hz sat at half that, and the detector reported **2187.5 Hz with a
statistic of 35** — a confident wrong symbol rate read off the signal's own data
noise. The floor is now local, and the search starts where a segment holds 96
symbol periods, which turns that wrong answer into no answer at a declared cost:
a minimum detectable symbol rate. A miss is a limitation; 2187.5 Hz was a
fabrication.

**4. The channelizer destroys the feature it was meant to isolate.** This one is
architectural. The squared-envelope timing line exists *only* because the pulse
has excess bandwidth, and it lives in exactly the spectral shoulders that a
channel cut at `CHANNEL_MARGIN = 1.25` times a **−20 dB** occupancy estimate puts
into the FIR skirt. Measured end to end:

| symbol rate | channel | decimation | samples | statistic before | after | verdict |
|---:|---:|---:|---:|---:|---:|---|
| 20 kBd | 30.6 kHz | 33 | 15,884 | 29.35 | — | INSUFFICIENT_WINDOW |
| 50 kBd | 75.0 kHz | 13 | 40,320 | **56.07** | **1.38** | NO_SYMBOL_CLOCK |
| 100 kBd | 153.8 kHz | 6 | 87,360 | 118.62 | 74.51 | SYMBOL_CLOCK_LIKE_FEATURE |
| 200 kBd | 306.6 kHz | 3 | 174,720 | 232.98 | 228.69 | SYMBOL_CLOCK_LIKE_FEATURE |

At 50 kBd a clean detection becomes no detection, with **nothing wrong at either
end**: the channelizer cut a correct channel and the detector correctly found no
feature in it. The feature is not in what the detector receives. This bears
directly on holding `CHANNEL_MARGIN` at 1.25 — that decision was made on
reference-bin grounds, before this evidence existed, and the measurement adds a
second and independent reason to revisit it in a new hashed configuration. It is
recorded in `KNOWN_FALSE_NEGATIVE_MODES` and asserted by a test, not fixed here.

**Known weaknesses are declared rather than smoothed over.**
`KNOWN_FALSE_POSITIVE_MODES` records that a slowly sloping cyclic spectrum beats
the local median from the slope alone (measured 4.07 against a threshold of 2.5 on
a random-walk envelope with no symbol structure), and that a periodic transport
artefact is a genuine cyclic feature this statistic cannot separate from slow
symbols. The mitigation for the first — taking the higher of the two side medians
— costs a factor of four on real signals (26.8 → 6.4 at 16 kBd), so it is not
applied. Shadow mode is what keeps both out of evidence, and the tests assert that
rather than pretending the statistic is cleverer than it is.

**The rtl_tcp header was never stripped.** Twelve bytes of `RTL0` + tuner type +
gain count were decoded as six complex samples at the head of every connection.
Tuner type and gain count are small integers, so most of those bytes are `0x00` —
negative full scale in offset-binary uint8 — and every reconnect began with a
full-scale transient that the FFT and the ring both saw as signal. It is now
consumed, and it is also the only place the device says what it is: live it
reports `R820T` with **29** gains, matching what `rtl_test` printed. The gain
table is `DRIFT`-proofed by that count — a driver table whose length disagrees
with the device refuses manual gain rather than setting the wrong value.

**Four test layers, from the beginning.** Algebra, end-to-end through the real
FIR and contract, metamorphic (amplitude scaling, phase rotation, time
translation and frequency offset must not change the verdict), and adversarial
(constant-envelope digital, DC spike, clipping harmonics, retune transient,
analogue FM with periodic content, periodic buffer artefact, sloping spectrum).
Defects 1, 3 and 4 above were found by layers 1 and 2. The stopband-SNR defect
that preceded them would have been caught most directly by **layer 1** — a
ground-truth oracle asking whether a synthetic 20 dB channel reads 20 dB — and
would also have been caught by layer 4, since "filter skirts" is on the
adversarial list. Layer 3 caught nothing, which is itself a result: metamorphic
invariance says the statistic behaves consistently under transformation, and a
number that is consistently wrong satisfies it perfectly.

Worth noting separately: three of the four new defects were in *declared
constants* — a threshold, a minimum sample count, a margin — not in logic. Every
one of them was a number registered against no implementation, or measured once
and generalised. That is why freezing the strata set alongside the threshold in
`PromotionCorpusLock` matters more than it first appeared: the constants are
where the unexamined claims live.

That is the lesson being carried: the previous suite checked that `_measure` was
called and that its outputs were plumbed, and never once asked whether the
number was right.

### 5.13 The channel that measures and the channel that analyses are not the same channel

The 50 kBd measurement in §5.12 — cyclic statistic `56.07` unchannelized,
`1.38` through the production channel — is not attenuation. The channelizer had
removed the feature the detector exists to find. The squared-envelope timing
line only exists because the pulse has **excess bandwidth**, and excess
bandwidth is exactly the spectral shoulder that a channel cut snug to a −20 dB
occupancy estimate puts into the FIR skirt.

The wrong repair is to move `CHANNEL_MARGIN`. Products already published under
the measurement lineage are comparable with each other; a margin chosen to help
a detector would retroactively change what every occupancy and SNR figure meant,
and it would do so silently, because the digest inputs would not have changed
shape.

**Two purposes, two lineages.**

| Product | Purpose | Width policy | Margin |
| --- | --- | --- | --- |
| `MEASUREMENT_CHANNEL` | occupancy, centroid, local SNR | `OCCUPANCY_FITTED_V1` | 1.25, **frozen** |
| `STRUCTURE_CHANNEL` | symbol-clock / cyclostationary analysis | `CYCLIC_STRUCTURE_PRESERVING_V1` | provisional, sweep-selected |

`ChannelRequest.channel_purpose` defaults to `MEASUREMENT_CHANNEL`, so an
existing caller gets the product it already got. The measurement lineage's
**digest formula is frozen**: its digest inputs end exactly where they ended,
verified by re-running the pre-change module against the post-change one on the
same window and comparing byte for byte (`chp-bb3edd31e79682a9` both sides). Any
other purpose appends its policy to the digest, which is what stops the two
lineages pooling — same window, same width, different purpose, different digest.

**The other half of the murder.** A wide input filter followed by aggressive
decimation destroys the cyclic feature just as thoroughly as a narrow filter,
and leaves cleaner paperwork: the channel width in the product looks generous
while the output rate cannot represent the cycle frequency at all. The
squared-envelope line sits at `α = R`, so the structure channel declares
`output_samples_per_candidate_symbol = 4.0` and the rate floor is checked
*before* decimation is chosen. A request that violates it is refused with
`STRUCTURE_RATE_UNSATISFIABLE`, never quietly delivered.

The floor is derived from the **measured** occupancy, not the requested width.
Deriving it from the request would let a caller lower the floor by asking for a
narrow channel — the requirement would then be a restatement of the request
rather than a fact about what the signal needs.

**The purpose reaches the verdict.** `NO_SYMBOL_CLOCK` from a measurement
channel is close to uninformative; the same outcome from a structure channel is
evidence. `rf_detector_contract.channel_purpose()` returns
`CHANNEL_PURPOSE_UNDECLARED` for a product that predates the split rather than
assuming the answer, and every `SymbolClockVerdict` carries it. Phase 3 must
stratify on it, which changes the tested bound count — the arithmetic is
computed in `PENDING_AMENDMENTS` and deliberately **not adopted**, because the
family is not that module's to redefine and a bound count that drifts while
nobody is looking is what `PromotionCorpusLock` exists to catch.

**Selecting the width from evidence.** `tools/rf_structure_channel_sweep.py`
runs the grid the review specified — margin × symbol rate × roll-off × SNR ×
offset × neighbours, 6,480 cells — through the production `channelize` on a real
`BoundedIQRing` window, with each candidate margin injected as a real
`ChannelPolicy`. A sweep that reimplemented the filter would be measuring the
sweep.

Two things had to be fixed in the harness before it measured anything, and both
are worth keeping:

*The reference was committing the fault under test.* The first version mixed to
baseband and decimated with **no** anti-alias filter, which folds the whole
2.048 MHz of noise into the output band and drives the reference statistic down.
Retention against that reference is not conservative, it is meaningless. The
reference is now a *wide channel* — 6× occupancy, capped per cell by Nyquist, by
the span edge and by the distance to DC, with the margin actually used published
so a cell whose reference could not clear the widest margin under test is
excluded rather than quietly averaged in.

*The first run answered the wrong question.* It requested `theoretical occupancy
× margin` and concluded that margin 1.25 **retains 166%** of the reference
statistic — while the production path at the same margin had been measured at
1.38 from 56.07. Both numbers were right. In production the margin multiplies
the **measured** occupancy, and the −20 dB walk closes inside the brick wall:
59.75 kHz measured against 69.1 kHz true for a 50 kBd β=0.35 signal, a ratio of
0.865. Requesting the theoretical width had silently removed the underestimate
that caused the problem. The margin is not the only term:

```
flat coverage = margin × (measured occupancy / true occupied) × 0.85
```

where 0.85 is `PASSBAND_REFERENCE_FRACTION`, the point at which the shipped FIR
is still flat. For that 50 kBd signal, margin 1.25 gives 0.918 — the flat
passband covers 92% of the signal and the shoulders carrying the timing line are
in the skirt. Margin 2.0 gives 1.47. **It is flat coverage, not margin, that
decides whether the feature survives**, and a margin chosen without the
occupancy underestimate beside it is a number chosen against the wrong variable.

*And the report was reading noise as evidence.* Retention is only defined where
there was something to retain. A β = 0 sinc has no excess bandwidth and so no
timing line; a −10 dB cell has a reference measuring noise. In both, the ratio of
two noise statistics sits near 1.0, and the first pass over 6,480 cells duly
concluded that the margin does not matter. The report now conditions on the
reference having found the true symbol clock, and states that condition in its
own output.

**The family stays at thirteen.** Channel-purpose aggregates were considered
and rejected on a structural argument, not an arithmetic one. The two lineages
are not two populations from which SCYTHE independently makes DIGITAL claims: a
measurement channel is cut for occupancy, centroid and SNR and cannot produce an
information-structure verdict at all. Bonferroni must cover the inferential
claims *eligible for promotion*, not every implementation dimension that appears
in provenance.

That argument holds only while exactly one lineage is eligible, so the
prohibition is enforced rather than assumed. `rf_symbol_clock.detect()` refuses a
verdict from any purpose other than `STRUCTURE_CHANNEL` — before any arithmetic —
with the outcome `CHANNEL_PURPOSE_NOT_ELIGIBLE` and axis value `NOT_ATTEMPTED`.
A measurement channel is still **admitted**: the contract's admission rule is
unchanged and still reads only `transformation.outcome`. It is admitted and then
refused a verdict, on the ground that it was never eligible for one. An
undeclared purpose is refused for the same reason — eligibility is a declaration,
not a default.

```json
{
  "validation_family_revision": "rf-digital-q4.v1",
  "simultaneous_control": "BONFERRONI",
  "family_alpha": 0.05,
  "tested_bound_count": 13,
  "per_bound_alpha": 0.003846153846,
  "minimum_zero_failure_trials_per_bound": 5561,
  "channel_purpose_eligible_for_promotion": "STRUCTURE_CHANNEL",
  "measurement_channel_verdict_production": "PROHIBITED"
}
```

Membership is **derived from `STRATA`**, not transcribed beside it: a
hand-written list would be a second source of truth for the one thing that may
not drift. The review named its members in operator vocabulary and the corpus
contract keys them differently in four places (`THERMAL_NOISE` →
`THERMAL_NO_INPUT`, `ANALOGUE_FM` → `STATIONARY_ANALOGUE_FM`, `ANALOGUE_AM` →
`AM`, `OVERLOADED_CLIPPED_INPUT` → `OVERLOADED_CLIPPED`); the corpus keys are
canonical because those are what a labelled window carries, and the mapping is
published so the correspondence can be audited rather than assumed.

`PromotionCorpusLock` now freezes the family revision and the eligible purpose
alongside the bound count, and `_corpus_state` reports
`FAMILY_REVISION_CHANGED_AFTER_FREEZE` and `ELIGIBLE_PURPOSE_CHANGED_AFTER_FREEZE`.
A bound count of 13 would not notice a family whose *membership* was rewritten at
the same size, and thirteen bounds do not cover fourteen chances at one
threshold.

**Six triggers would enlarge the family**, each of them a second path allowed to
emit the promoted claim: multiple structure-channel margins, multiple FIR
revisions, multiple threshold variants, alternate preprocessing paths, separate
detector decisions from measurement channels, or multiple methods each allowed to
emit it. And the selection rule is recorded explicitly: freezing one structure
configuration against development data and opening the corpus afterwards does not
enlarge the family, because only one hypothesis ever meets the corpus. Running
several configurations against the promotion corpus and keeping the best enlarges
it by exactly the number run — calling them configuration experiments does not
stop them being multiple hypothesis tests.

**The sweep selected 2.0, and it is now frozen.** 6,480 cells, scored on the 799
where a wide reference actually found the true symbol clock. Summary retained at
`docs/evidence/structure_channel_margin_sweep.json`.

| margin | p5 coverage | median retention | p5 retention | frac < 0.75 | contam dB | DC refusals |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1.25 | 0.93 | 1.695 | 0.485 | 0.072 | 0.00 | 87 |
| 1.50 | 1.11 | 1.450 | 0.358 | 0.078 | 0.00 | 123 |
| **2.00** | **1.47** | **1.262** | **0.897** | **0.034** | **0.01** | **177** |
| 2.50 | 1.72 | 1.200 | 0.861 | 0.018 | 0.07 | 279 |
| 3.00 | 2.07 | 1.066 | 0.845 | 0.020 | 0.23 | 303 |
| 4.00 | 2.78 | 1.030 | 0.208 | 0.164 | 0.50 | 360 |

1.25 and 1.5 fail the coverage gate's lower tail and the retention tail; 4.0
collapses. 2.0 is the **narrowest** margin passing all three declared criteria and
the cheapest of those that pass. Widening did **not** worsen adjacent-channel
false positives — the wrong-symbol-rate rate falls from 0.110 at 1.25 to 0.045 at
2.0 to 0.000 at 3.0, because the errors at narrow margins are half-rate reads off
a mangled spectrum rather than neighbour contamination. What widening does cost
is DC refusals, which double by 2.0 and treble by 3.0.

The margin arrives at the number it started at, which is worth being suspicious
of, so three caveats are published in `channelizer_status()` beside it:

- **The p5 sits on a cliff.** At 2.0 the four lowest retentions are 0.265, 0.286,
  0.323, 0.333 and the fifth is 0.748. The published 0.897 is decided by where
  the percentile index lands relative to that gap, not by a margin of safety. The
  robust form — fraction of cells below 0.75 — is 0.034 against 0.072 at 1.25,
  supporting the same choice for a better reason.
- **The residual tail is not a coverage failure.** Three of those four cells are
  20 kBd at 20 dB across all three offsets, with flat coverage 1.57 — well clear
  of the gate. The cause is decimation and window length at low symbol rate,
  already declared as `DECIMATION_LEAVES_TOO_FEW_SAMPLES`. Widening does not fix
  it and is not credited with doing so.
- **A different statistic would have chosen 2.5.** On fraction-below-0.75 alone,
  2.5 scores 0.018 against 2.0's 0.034. The declared criterion was the fifth
  percentile, declared before the run, and switching statistics after seeing
  which one changes the winner is the exact failure this project's validation
  rules exist to prevent.

Selection used development data only and the promotion corpus is unopened, so
under the recorded selection rule it does not enlarge the validation family.

### 5.14 Two declarations that are absences

**The threshold.** `2.5` is a development heuristic, not a decision boundary.
`threshold_declaration()` publishes it as `PROVISIONAL` under
`SYNTHETIC_CALIBRATION` authority with `promotion_eligible: false` and
`false_alarm_probability: null` — null and not a placeholder, because a number
there would be the most quotable false claim in the module. A crossing is
`THRESHOLD_EXCEEDED_IN_SHADOW_MODE` and nothing else; a test asserts that no
string field of any verdict contains the word DIGITAL, which caught the outcome
prose still reading "DIGITAL STRUCTURE IS SUPPORTED, NOT PROVEN". Every
qualifier in that sentence was correct and it was still the sentence someone
would quote with the qualifiers dropped.

**Direct sampling.** `DIRECT_SAMPLING_CHANGE` stays visibly unwired. Building a
control so the warning list comes out empty would make the empty list the lie.
What is published instead is the shape of the gap:

```json
{
  "direct_sampling": "UNDECLARED",
  "expected_capture_regime": "TUNER_QUADRATURE",
  "expected_regime_authority": "INFERRED_FROM_CONFIGURATION",
  "runtime_attestation": "UNAVAILABLE",
  "control": "NOT_IMPLEMENTED"
}
```

**The naming is the point, and position is not available.** The first version
led with `direct_sampling_regime: TUNER_QUADRATURE` beside an authority tag
reading `ASSUMED_FROM_ABSENT_CONTROL`. Every word of that was true, and the
regime still read as the primary fact the moment a UI or a log collector
flattened the object.

The obvious repair — put `UNDECLARED` first — turns out not to be a repair at
all: the status route serialises with sorted keys, so the object arrives
alphabetically and `attestation_note` leads on the wire regardless of build
order. Position cannot be relied on. What can is that **every field which is not
the state says so in its own name**: `expected_capture_regime`,
`expected_regime_authority`. A reader reaching for the first plausible key lands
on one that is self-qualifying. A test enforces that rule over the whole object
rather than asserting an order that transport discards.

An installed R820T does not prove the active stream uses it. SCYTHE does not
start `rtl_tcp` and cannot see its arguments, so there is no runtime attestation
to have, and that absence is published as `UNAVAILABLE` rather than left to be
noticed. None of it reaches the hashed signal-chain manifest, which still carries
`direct_sampling: UNDECLARED`: promoting an inference into the instrument's
identity would advance the chain hash on the strength of a guess. The control transaction is specified before the
control exists — stop, invalidate and discard the ring, change regime, advance
the manifest and hash, rebuild the channelizer configuration, reconnect, refuse
comparison with tuner-quadrature products — because the order is the whole
content: changing the regime while a ring holds samples captured under the
previous one produces a window that cannot be described.

**Clock continuity.** The monitor now reports `ZERO_DETECTED_DISCONTINUITIES`,
never "zero discontinuities", alongside
`detection_coverage: BOUNDED_BY_DRIFT_TOLERANCE_AND_CHECK_INTERVAL`. It compares
a sample count against elapsed wall time over a 10 s interval; a loss small
enough to stay inside the drift tolerance leaves no trace, and `rtl_tcp` hands
over a byte stream with no per-sample attestation against which one could be.
Detection coverage is not omniscience.

### 5.15 The receiver is a second instrument with a second chain

`WALKING PASSIVE GEOLOCATION` needs to know where the receiver was. The phone
supplying that is a **second sensor with its own failure modes**, and folding it
into `signal_chain_hash` would make a GPS fix change the identity of the
receiver and a gain step change the identity of a position. Neither is true.

```
signal_chain_hash           what instrument produced this measurement
receiver_state_chain_hash   where, when and in what orientation that
                            instrument was *believed* to be
```

`rf_receiver_state.py` implements build order item 1: the
`scythe.rf-receiver-state.v1` contract, its chain hash, the pose budget, the
four-state alignment gate and the `TIME_ALIGNED_WITH` join. Nothing collects a
position yet and nothing estimates a location; `receiver_state_status()` declares
`collection_implemented`, `posterior_implemented`, `planner_implemented` and
`body_shadow_implemented` all false.

**Course is not heading.** This is the most expensive available mistake here.
Course describes the direction the receiver is *translating*; heading describes
where the antenna is *pointing*. At 1.1 m/s they decouple completely and
destabilise for entirely different reasons — course from GNSS noise divided by a
small velocity, heading from magnetic disturbance and tilt. A body-shadow
experiment needs heading and gets nothing from course. `heading_source` is
`UNDECLARED` until something that actually measures orientation declares it, and
the constructor **discards a heading value supplied without such a source**
rather than carrying it. That refusal is at the constructor, not downstream,
because downstream is where a number becomes a bearing.

**Staleness is metres, not seconds.** The chain excludes the position itself,
exactly as the signal chain excludes centre frequency — a chain identity that
moved with every fix would make every state an incomparable island. What it does
contain is the *apparatus*: device, position authority, course and heading
sources, alignment method, mount. The gate is then a distance:

```
sigma_motion = v · sigma_t
sigma_pose   = sqrt(sigma_GNSS² + (v · sigma_t)² + sigma_mount²)
```

At 1.1 m/s a 42 ms uncertainty contributes **4.6 cm** and vanishes beside a 4.8 m
GNSS circle; at 20 m/s the same 42 ms contributes **0.84 m** and starts to
matter. A state goes `STALE` when the receiver could have moved further than its
own position circle inside the timing uncertainty — which arrives at 5 s on foot
and 160 ms at 30 m/s. A seconds-based cutoff would have to pick one and be wrong
for the other.

The mount term is 2.0 m and is a **declared unknown**, not a measured offset: the
antenna is on a two-metre magnetic base and its relationship to the operator is
`UNDECLARED`. It is in the budget so that it cannot be quietly forgotten, which
is why a nominally 4.8 m fix yields a 5.20 m pose.

**Breadcrumbs are never gated.** Every alignment state permits them, because
rendering where the operator walked is a record of the survey rather than an
inference about an emitter. Only `VERIFIED` and `BOUNDED` may update a surface,
and `BOUNDED` marks bearing-like evidence `CONDITIONAL` — time alignment does not
supply a verified heading source and so cannot on its own authorise directional
evidence.

Still to build, in order: phone collection with explicit source authorities;
bounded device-to-orchestrator clock exchange; the graph edges; an RF likelihood
adapter over `h3_heatmap.py` — reusing the H3 posterior substrate but **not** its
search-and-rescue priors, independence assumptions or movement models, and
selecting resolution from pose uncertainty because cells smaller than the GNSS
circle are decorative precision; the Fisher-information geometry metrics; the
planner on the same objective; the point-estimate gate; and the controlled
body-shadow rotation mode. `doma_rf_motion_model.py` stays out — it predicts
*emitter* trajectories and is the wrong tool for a receiver-motion posterior.

### 5.16 The rate the trace is labelled with is a launch argument

The bridge sends `rtl_tcp` two control opcodes: `SET_GAIN_MODE` (0x03) and
`SET_GAIN` (0x04). There is no `SET_SAMPLE_RATE`. The rate is whatever
`rtl_tcp` was started with via `-s`, and `rtl_tcp` never acknowledges what the
tuner actually applied — the `RTL0` header carries a tuner type and a gain
count, not a rate.

That makes `SDRPP_SAMPLE_RATE_HZ` a **claim**, and the claim is load-bearing:
`bin_width = sample_rate_hz / fft_size`, so it labels every frequency in the
trace. A configured rate and a confirmed rate produce identical-looking
spectra. Nothing in the pipeline would raise an error if they diverged; the
axis would simply be wrong.

Two separate defences, and they do different jobs.

The first removes drift. `scythe-rtl-tcp.service` and the orchestrator now read
the rate from one file, so the actual `-s` and the declared value cannot
disagree. `EnvironmentFile=` carries no leading `-`, so a missing file stops
the orchestrator rather than letting it fall back to the 1 MS/s default in
`RFBridgeConfig` and silently mislabel the axis by a factor of two.

The second is the one that matters for evidence. Single-sourcing removes
*disagreement between two configurations*; it does not turn a configuration
into a measurement. So the payload names its own authority:

```json
{
  "sample_rate_hz": 2048000,
  "sample_rate_authority": "SHARED_LAUNCH_CONFIGURATION",
  "runtime_attestation": "UNAVAILABLE",
  "native_bin_width_hz": 500.0
}
```

`native_bin_width_hz` is correct *conditional on the configured rate having
been applied*. This is the same shape as §5.14's direct-sampling block: a value
that is real, useful, and not attested, published with the qualifier attached
rather than left for a reader to infer.

Reaching `LAUNCH_CONFIG_CORROBORATED` — still not `USB_MEASURED` — would need a
capture handshake record: environment-file hash, the actual `rtl_tcp` command
line, process start time and PID, connection epoch, requested rate, and any
startup log line stating the applied rate. Estimating the rate from a known
broadcast station is explicitly **not** that. It would replace configuration
trust with transmitter trust and call the substitution a measurement.

#### What the bridge refuses to say about its own absence

A refused IQ connection is indistinguishable from a stopped `rtl_tcp`, a wrong
endpoint, a busy receiver, or — under WSL — a USB device that Windows has not
attached. The bridge therefore publishes reachability and declines the cause:

```json
{
  "transport_state": "DISCONNECTED",
  "sample_flow_state": "NONE",
  "availability": "SOURCE_DISCONNECTED",
  "unreachable_cause": "NOT_DETERMINABLE_FROM_THIS_PROCESS"
}
```

The single field became two axes on 2026-09-06, after `rtl_tcp` was observed to
survive USB removal: the process stays healthy, the socket stays established,
and nothing arrives on it. Reachability alone called that state connected. It
now reads `transport_state: CONNECTED` with `sample_flow_state: STARVED`, and
the derived `availability` is `SOURCE_STARVED`. The refusal to name a cause is
unchanged and now applies to both axes — a removed USB device, a wedged
`rtl_tcp` and a suspended host are indistinguishable from this side of the
socket. See §3-4 of `docs/RTL_TCP_BOOT_CAPTURE.md`.

`WAITING_FOR_USB` would have been the useful-sounding string, and it is the one
the operator most often wants. It is also a guess. The restart policy that
governs recovery is a property of a systemd unit the bridge never read, so it
is documented in `docs/RTL_TCP_BOOT_CAPTURE.md` rather than asserted by a
process with no access to it.

### 5.17 The same mast at two extensions is two instruments

`declaration_receipt` raised its comparability boundary on `antenna_id` alone:

```python
changed = bool(previous) and previous.get("antenna_id") != record["antenna_id"]
```

A telescopic mast retracted from 730 mm to 165 mm keeps the id
`nesdr-smart-telescopic` throughout. Its derived quarter wave moves from
102.7 MHz to 454.2 MHz — from the FM broadcast span to the 433 MHz ISM band.
That is not the same antenna with a different setting; it is a different
frequency response, and every relative-power product taken either side of the
change was taken through a different instrument.

The declaration hash already knew this. It was computed over `antenna_id`,
`feedline_id`, `extension_mm` and `note`, so the extension change moved the hash
while the boundary stayed silent — the same shape as a sample rate that drifts
away from the one the trace is labelled with. **A change that moves a hash and
raises no boundary is a signal-chain change that produces no complaint.**

The boundary now fires on `COMPARABILITY_FIELDS = ("antenna_id", "feedline_id",
"extension_mm")`. `feedline_id` is included because §5.10 already established
that a mast on 2 m of RG58 is not the same signal chain as the same mast on the
SMA port; leaving it out would have re-created the defect one field over.

#### One hash was answering two questions

Excluding `note` from the boundary while leaving it in the hash only moves the
contradiction. If prose is part of a hash that anything keys comparability from,
then fixing a typo invalidates products — and a receipt that stays quiet while a
downstream hash moves is not one system agreeing with itself. The hash is now
two:

| Hash | Covers | Answers |
| --- | --- | --- |
| `instrument_hash` | `antenna_id`, `feedline_id`, `extension_mm` | what did these products come through? |
| `declaration_hash` | the instrument fields, `authority`, `extension_authority`, `note` | what did the operator assert about it? |

`signalChainChanged` is true exactly when `instrument_hash` moved, and a test
asserts that equivalence across every field rather than asserting the two
symptoms separately. The receipt carries `instrumentHash`,
`previousInstrumentHash`, `changedFields`, `previousFeedlineId` and
`previousExtensionMm`.

`declared_at` is deliberately **not** in either hash. Two identical declarations
made a minute apart describe the same instrument and the same assertion, and a
hash that changes on every re-declaration is an event id rather than an identity.

#### What the product hashes could not see

Auditing the downstream side found the premise half-right and the consequence
worse than expected. `note` reaches neither `signal_chain_hash`: the retention
manifest hashes sensor, sample type, rate, antenna, feedline and gain, and the
sparse analyzer hashes tuner state plus `antenna_id`. Prose was never in a
product hash, so the contradiction had no downstream victim.

`extension_mm` was not in either one either. Products taken at 730 mm and at
165 mm carried **identical chain identities** — the receipt would complain and
the product hash would not, which is the same defect one layer down. Both now
carry the extension:

- `signal_chain_manifest` gains `antenna.extension_mm` and
  `antenna.extension_authority`, bumping `SIGNAL_CHAIN_REVISION` from `v2` to
  `v3` with `PRIOR_SIGNAL_CHAIN_REVISION_COMPARABLE = False`, as v1→v2 did for
  the feedline.
- `SparseAnalyzerConfig` gains `antenna_extension_mm`, carried into
  `_signal_chain` beside `antenna_id`.

The manifest takes the readable fields rather than embedding `instrument_hash` as
an opaque digest. Its stated purpose is to be retained beside its hash and simply
read; folding in a digest would satisfy the letter of "incorporate the instrument
hash" while hiding exactly which field moved. The invariant is the same and is
tested directly: the chain hash varies with every comparability field and is
invariant to the note.

A configured extension that does not validate hashes as
`REFUSED_UNUSABLE_VALUE`, distinct from `UNDECLARED`. An operator who typed
metres into a millimetre field is not an operator who declined to say, and
collapsing the two would hide a misconfiguration inside a legitimate omission.

#### A hash formula is not a live instrument

Fixing the formula would have fixed nothing on a running receiver. Both product
hashes sourced the antenna from `os.environ`: `IQRetentionOwner` built its
manifest from `antenna_id()` at construction, `SparseAnalyzerConfig.from_env()`
froze its copy at construction, and `IQRetentionOwner.status()` re-read the
environment on every call. The declaration endpoint touched none of them.

So an accepted declaration moved the receipt and nothing else. Every subsequent
window carried the chain hash the process booted with, while the receipt
announced that the instrument had changed — a contradiction the system published
rather than merely failed to notice.

The capture owner now holds one active instrument state, bootstrapped from the
environment and thereafter replaced only by declaration:

```
validate declaration          AntennaDeclarationStore.declare
  → replace active state      IQRetentionOwner.set_instrument   ─┐ one lock,
  → rebuild manifest + hash   _rebuild_chain_locked              │ whole
  → invalidate ring           SIGNAL_CHAIN_CHANGE                │ sequence
  → advance epoch             BoundedIQRing.configuration_epoch ─┘
  → follow in the analyzer    RFSparseAnalyzer.set_instrument
  → acknowledge receipt       declaration_receipt + persistence
```

The clear is held under the same lock as the swap. Doing it afterwards leaves a
window in which a product can be issued carrying the new chain hash under the old
epoch — a product that looks attributable and is not.

Three further leaks were found and closed while wiring this:

- `set_gain_db` rebuilt the manifest with only `gain_db=`, letting antenna,
  feedline and extension fall back to the environment. A gain change would have
  silently reverted the instrument to the boot-time one. Both rebuild sites now
  go through `_rebuild_chain_locked`, which reads only owner state.
- `status()` published `antenna_id()` and `feedline_id()` from the environment
  beside a chain hash computed from the declared instrument, so the payload could
  contradict itself.
- `AntennaDeclarationStore.declare` compared against nothing on a first runtime
  declaration and reported `signalChainChanged: false`, while the capture owner —
  which had bootstrapped from the environment — cleared its ring for the same
  event. The store now adopts the boot environment first, so both components
  answer one question the same way.

`SIGNAL_CHAIN_CHANGE` was in `INVALIDATION_REASONS` but had no wired source. It
now names one in `WIRED_REASON_SOURCES`, leaving `DIRECT_SAMPLING_CHANGE` as the
only deliberately unwired reason.

#### Active is not persisted

The runtime store is process-local, so an operator who declares an antenna and
then reboots loses it. Status distinguishes the two:

```
RUNTIME DECLARATION // ACTIVE
BOOT DECLARATION    // PERSISTED | PENDING_PERSISTENCE
```

`PENDING_PERSISTENCE` says the declaration is genuinely in force for products
being emitted now and that a restart would adopt a different instrument.
Persisting it is a separate operation on the unit environment, deliberately not
performed by the declaration endpoint: writing a systemd drop-in is not something
an HTTP handler should do on the strength of a form post.

#### The number is geometry, not resonance

`quarter_wave_hz` now travels with `quarter_wave_model: IDEAL_FREE_SPACE` and
`resonance_claim: NOT_MEASURED`. `c/4L` assumes free space and an infinite ground
plane; the magnetic base, the surface it is stuck to, body and vehicle proximity
and the stepped construction of a telescoping whip all move the real optimum, and
a practical monopole rule lands roughly 4.6% shorter. The model has to be named
or the figure reads as a property of this antenna.

`extension_authority` separates a length read off a ruler
(`OPERATOR_MEASURED`) from one arrived at by arithmetic (`OPERATOR_ESTIMATED`,
the default). Nothing can distinguish them, so `MEASURED` must be claimed and is
never assumed. It sits in `declaration_hash` and not in `instrument_hash`: a
better claim about the same geometry is not a different instrument.

Worth stating plainly, because the two get conflated: setting the mast to 173 mm
for a 433.92 MHz survey derives 433.226 MHz, not 433.92 MHz. 433.92 MHz wants
172.723 mm under this model, and a mast is set in whole millimetres. The derived
figure describes the geometry that was declared, never the frequency that was
intended.

The receipt now carries `changedFields`, `previousFeedlineId` and
`previousExtensionMm`, and names what moved:

```
MAST EXTENSION CHANGED 730 mm → 165 mm — DERIVED QUARTER WAVE 102.7 MHz → 454.2 MHz.
THE MAST IS THE SAME PART; THE INSTRUMENT IS NOT
```

The instrument panel prints `SIGNAL CHAIN CHANGED (EXTENSION_MM)` rather than the
bare phrase, because an unqualified "signal chain changed" reads as a swapped
antenna.

#### A millimetre field that accepted metres

The bound was `0 < extension_mm <= 2000`. `0.73` — 730 mm written by someone
thinking in metres — validated, and produced a quarter wave of 102.7 **GHz**,
roughly fifty-eight times the R820T's ceiling, with no complaint. The floor is
now `MIN_EXTENSION_MM = 10.0` in both the Python catalogue and its
`rfAntennaDeclaration.js` mirror, refused with the unit error named. This is a
unit guard, not a hardware claim: the module does not know the tuner's range and
does not assert one.

#### The declaration had no way to survive a restart

`AntennaDeclarationStore` is process-local and volatile, so an antenna declared
through the API lasts exactly as long as the orchestrator. On the observed
workstation the store held `nesdr-smart-telescopic` with the magnetic base while
the unit drop-in still said `SDRPP_ANTENNA_ID=nesdr-smart-uhf`; the next restart
would have silently reverted the signal chain to a different mast.

`bootstrap_from_env` now also reads `SDRPP_ANTENNA_EXTENSION_MM`, so the
extension is durable alongside the antenna and the feedline. An unusable value
refuses the **whole** bootstrap and leaves the antenna `UNDECLARED` rather than
degrading into the same mast with no extension — a visible omission instead of an
invisible substitution. The authority does not change: configuration is still the
operator speaking, and `OPERATOR_DECLARED` is what it records. Nothing here is
measured, and no amount of environment file makes it so.

### 5.18 The strata minima cannot meet the bound they are gated on — **RESOLVED: C**

*Found 2026-09-14 while scoping the Phase 3 null harness, and **accepted the same
day as option C**: every stratum needs 5,561 windows, and the corpus target
becomes 66,732.*

> **Cost does not resolve a contradiction in favour of the cheaper
> interpretation.**

*The accepted gate makes four claims at once — per-stratum, `0.001`, thirteen
simultaneous bounds, Bonferroni-adjusted confidence — and only C preserves all
four. **A** silently turns thirteen gates into one. **B** changes the meaning of
the published rate. Both were cheaper, and cheapness was not the question: the
minima were arithmetic that had fallen behind a correction, not a second opinion
about how much validation is enough.*

*The expensive physical strata stay expensive. **Their inconvenience is evidence
about the validation burden, not permission to weaken it** — a gate that relaxes
whenever meeting it is tedious is a gate that measures tedium.*

*The three readings are kept below rather than deleted, because the two that were
rejected are why C is right.*

**Every stratum fails its own bound at its declared minimum, with zero observed
false positives.** Not some — all twelve:

```text
obs = {stratum: (minimum_windows, 0) for stratum in STRATA}
evaluate(obs) -> every stratum bound above 0.001
```

The arithmetic is not in dispute. §5.12 corrected the zero-failure requirement
from 3,000 to **5,561** trials when the Bonferroni split took per-bound
confidence to 99.61538%. The `minimum_windows` column was never raised to match:

| trials, zero failures | exact upper bound | vs `MAX_FALSE_DIGITAL_RATE` |
| --- | --- | --- |
| 500 (`RECEIVER_SPURS`) | 0.011060 | 11× over |
| 750 | 0.007387 | 7× over |
| 1 000 | 0.005545 | 5× over |
| 1 500 | 0.003700 | 3.7× over |
| 2 000 (`THERMAL_NO_INPUT`, the largest) | 0.002776 | 2.8× over |
| **5 561** | **0.000999** | first *n* that clears |

`TARGET_TOTAL_NULL_WINDOWS` is 10 000 and the minima sum to 11 750, so the
aggregate clears comfortably — `0.000473` at zero failures. **The aggregate was
never the problem.** §5.8's own reasoning is why: *"an aggregate rate can be
bought with thermal noise"*, which is exactly what 11 750 windows dominated by
easy strata would do.

*This is the failure mode §5.8 was written to prevent, arriving through the
column §5.8 did not update. The gate refuses correctly today — `evaluate` marks
every stratum failed — so nothing has been promoted on a bad bound. What is
unresolved is what the gate is asking for.*

#### The three readings

**A — the minima are a construction floor, not the promotion condition.**
`minimum_windows` says how small a stratum may be and still be worth building;
promotion is decided by the aggregate bound. Corpus target stays ~11 750.
*Cost:* the per-stratum bounds become advisory, and §5.8's central argument —
that an aggregate can be bought — loses its teeth. The thirteenth bound would be
the only one that gates, making `TESTED_BOUND_COUNT = 13` and the Bonferroni
denominator wrong as well.

**B — per-stratum bounds gate, but at a rate above 0.001.**
Each stratum must clear some declared per-stratum ceiling, looser than the
aggregate's. Corpus target stays ~11 750; the strata table is already sized for
roughly 0.003–0.011 depending on the stratum.
*Cost:* a second rate has to be chosen and defended, and "the false-DIGITAL rate
is 0.001" stops being true of any individual condition — only of the mixture.
A reader would have to be told which number applies where.

**C — the minima are stale and every stratum needs 5 561. — ACCEPTED**
The literal reading of §5.12. Corpus target becomes **66 732** windows, twelve
strata at 5 561.
*Cost:* between five and six times the corpus, and several strata are expensive
per window — `RETUNE_TRANSIENTS` and `GAIN_STEPS` each need a real tuner
operation, not a synthesised buffer.

#### What C settles

```text
minimum_windows             5_561   for each of the twelve strata
TARGET_TOTAL_NULL_WINDOWS   66_732
```

**The aggregate remains the thirteenth bound and does not substitute for any
stratum.** It is an additional condition, never an alternative one — which is the
distinction A would have erased and the reason `TESTED_BOUND_COUNT` stays 13.

#### What is not in question

The detector stays `REGISTERED_NOT_VALIDATED` under every reading. No option
changes `MAX_FALSE_DIGITAL_RATE`, the exactness of Clopper–Pearson, or
`PromotionCorpusLock`. Whichever is chosen, the strata set is inside the lock —
so changing `minimum_windows` after a freeze yields
`STRATA_CHANGED_AFTER_FREEZE`, and this decision therefore belongs **before** a
corpus is frozen rather than after.

---

### 5.19 The synthetic harness, and the boundary it must not blur — **ACCEPTED**

**Approved 2026-09-14**, alongside §5.18. The synthetic-only half of the Phase 3
corpus may proceed while captured-window persistence is still unauthorised.

> **A generator and a receiver are two sources of windows, and a corpus that
> cannot tell them apart is a corpus that cannot be audited.**

#### What the harness may do

Generate and label synthetic windows. Nothing else: **no live acquisition and no
captured-IQ persistence.**

Window counts come from the **declared stratum plan** — `STRATA` and the
`minimum_windows` §5.18 set — never from a second constant transcribed beside
it. A duplicated target is two answers that drift, and the one in the harness
would be the one nobody re-derived. Tests may inject smaller plans, which is the
seam that keeps a suite from generating 66 732 windows to assert a shape.

#### What every synthetic record must say

Each identifies itself as **synthetic** and names its **generator configuration
and seed**. Not a flag added beside the data: an unreproducible synthetic window
is indistinguishable from a captured one that lost its provenance, and the seed
is what makes the claim checkable rather than decorative.

#### Four refusals

**No fallback from a missing captured stratum to synthetic data.** A stratum
that needs a tuner and has none is absent, and reports absent. Filling it is the
substitution every other refusal in this repository exists to prevent.

**A `PromotionCorpusLock` freezes configuration before the first promotion
window. No `CorpusCompletionReceipt` and no claim of corpus completion** are
permitted while any declared stratum is missing or awaiting capture.

This refusal originally read "no `PromotionCorpusLock` freeze … while
tuner-dependent strata are absent", which forbade the precommitment until after
the thing it precommits to. It is **corrected here rather than superseded
elsewhere**: an accepted section that says one thing and a later accepted
section that says another leaves two accepted answers, whichever one is labelled
superseded. What the original protected — that nothing may claim a corpus is
complete while a stratum is missing — is the second sentence, and is unchanged.

*This note once read "the document is ahead of the code here", because
`rf_null_corpus.py` still answered `may_freeze: False` with a `freeze_note`
naming `PromotionCorpusLock`. **Phase 3a closed that gap** (§5.20 correction
**C**, merged `d70fc4c`): both keys are gone with no alias, and `plan_state()`
now answers two questions —
`configuration_precommitment: CONFIGURATION_PRECOMMITMENT_AVAILABLE` and
`completion_eligibility: COMPLETION_BLOCKED_AWAITING_CAPTURE`.*

*The correction outlived its condition by five merges, which is the failure the
note was written to prevent, one level up. It is recorded here rather than
deleted, because a note that was true, then false, then removed leaves a reader
no way to tell which of those a similar note is now.*

**No generic payload writer.** The interface keeps future real-window ingestion
structurally separate from synthetic generation. One `write(payload)` accepting
both is exactly the hole that blurs the authority boundary — the same shape
§13k L.1 refused in the derived-evidence producer, for the same reason.

**No captured byte reaches disk under this section.** `RETUNE_TRANSIENTS`,
`GAIN_STEPS` and `RECEIVER_SPURS` are out of scope until §5.20 is accepted.

### 5.20 — captured-corpus persistence — **ACCEPTED**

```text
Status:     ACCEPTED 2026-09-14. The authority text is in force.
Authority:  §5.20 is accepted. It authorises no persistence code and no live capture.
            Both require corrections A-D and the controls below landed as code,
            under their own slice authorisation.
Opens:      GAIN_STEPS and RETUNE_TRANSIENTS become reachable in principle --
            two strata, 11 122 windows. RECEIVER_SPURS does not, and the corpus
            stays unfreezable. See *What acceptance does not unblock*.
Creates:    Nothing. No directory exists and no byte is written by acceptance.
```

*Proposed 2026-09-14 at `fd536cd`, with three contradictions the operator named
and a fourth found while checking them. Revised at `d4a35c7` after review found
seven details open — one of which, "drains the entire ring", described behaviour
`acquire_window` does not have and was concealing an obligation. Revised again
at `cf4d627` after review found five more, including a digest claim that was
simply wrong and an attestation field that would have forced `RECEIVER_SPURS` to
fabricate two declarations to satisfy a schema. Accepted on the corrected
substance at `f777847`.

Review of that acceptance found three contradictions **in the resulting
document** — one substantive: §5.19's refusal and accepted §5.20 gave opposite
answers about `PromotionCorpusLock`, and calling the older one superseded left
two accepted answers rather than one. Corrected at `8e290ac`. **This is the
acceptance commit, and it covers the corrected text**, including the amendment
to §5.19, which is itself accepted text and could not be changed without one.*

*Two acceptance commits, in order, because the first was accurate when it was
made and the text moved under it. Amending it to pretend otherwise would have
produced a cleaner history and a false one.*

*Those three SHAs are the heads that were **reviewed**, not ancestors of this
one: the branch was rebased onto `084b1d2` so that §5.19's corrected wording and
entry 8's drain could not be lost, and a rebase rewrites. `fd536cd` and
`d4a35c7` are reachable through PR #74's timeline and nowhere in `main`'s
history. Recorded because a SHA in a document that resolves to nothing is worse
than no SHA.*

*Three readings are pinned here so they are not recovered later by inference.
§5.19's "out of scope until §5.20 is accepted" is now satisfied — but
`RECEIVER_SPURS` remains unreachable behind a **second** gate, the missing
identification protocol, which §5.20 does not supply. And acceptance of an
authority is not authorisation of a slice: no persistence code and no live
capture are authorised by this commit. And the document is **ahead of the
code**: §5.19 now states the lock split that `rf_null_corpus.py` does not yet
implement, which is §5.20 correction **C** and is recorded in both places rather
than in neither.*

*§5.5 granted a DSP working buffer — process-local, volatile, fixed-capacity,
non-persistent. A validation corpus is persisted labelled data by definition, so
it is a different permission and not an extension of that one.*

#### What is granted

> SCYTHE may atomically publish verified, ring-issued validation windows for
> exactly `GAIN_STEPS`, `RETUNE_TRANSIENTS` and `RECEIVER_SPURS`, within one
> explicitly opened Phase 3 corpus. It grants no general recording, replay,
> export, capture or archive authority.

The narrowness is the point. "SCYTHE may save IQ" is a different permission and
is not requested here.

#### Three contradictions this must correct first — and a fourth

An authority written over code that contradicts it authorises the contradiction.
These are corrections to the *present* repository, verified against it on
2026-09-14 at `1c4ce23`, not predictions.

**A. The promotion geometry, and what actually registers it.** The window a
captured corpus is drawn from is **524 288 samples — 256 ms at 2.048 MS/s,
4 194 304 payload bytes** as `complex64`. That figure is `DEFAULT_CAPACITY_SAMPLES`
in `rf_iq_ring.py:79` — the **ring's fixed capacity** — and it is the window §6
Q2 approved at 4.19 MB. `rf_null_corpus.py:125` presently defaults
`GeneratorConfig.window_samples` to `262_144`.

The correction stands, but its stated reason must not: **262 144 and 524 288 are
not competing registered detector geometries.** The registered detector's
`minimum_sample_count` is **32 768** (`rf_signal_family.py:303`), and 262 144 is
recorded in `rf_symbol_clock.py:579` as a *superseded* minimum — "registered
against no implementation and unreachable through this channelizer". Calling
524 288 "the registered detector geometry" would restate a number the detector
contract does not hold.

So: 262 144 may remain a development and test seam for synthetic work, where a
shorter window costs half the arithmetic and proves the same properties.
**Promotion-corpus generation and capture must refuse any geometry but 256 ms**,
because that is the ring the windows come out of and the window the corpus was
sized against — not because a detector registered it.

Two consequences follow from the ring, and both belong in the capture rules.

A 524 288-sample acquisition **spans the entire ring capacity**, and that is all
it does: `acquire_window` **copies** the retained tail
(`_ordered_tail_locked`) and removes nothing — the held count is unchanged.
Nothing here drains anything. Which matters, because two back-to-back
acquisitions return **the same samples twice**, under two different `window_id`s
and with two different issue times. `WINDOW_OVERLAP = "NONE"` is therefore not a
property the ring confers by handing a window out.

**And it must not become a promise the caller makes.** "Wait for a full capacity
of new samples" is unfalsifiable from the corpus afterwards, which is the shape
every other refusal here exists to remove. Make it mechanical:

> `current.first_sample_index >= previous.first_sample_index + 524_288`

The ring already computes exactly this number — `first_index = self._total_appended
- requested` (`rf_iq_ring.py:468`) — and records it in `_WindowRecord` while
**`IQWindow` does not expose it**. Exposing it as `first_sample_index` is the
whole change; it is an index, not samples, so it carries into `to_dict()` without
touching `raw_iq_exposed`.

The index is comparable across invalidations, which is the property the rule
depends on and is worth stating because it is not obvious:
`_invalidate_locked` zeroes the buffer, the write index, the held count and the
newest-sample time and bumps the epoch, but **`_total_appended` is set to zero
only in `__init__` and incremented only in `append`**. It is monotonic for the
ring's lifetime.

The header records `first_sample_index`, the `previous_window_id`, and the
interval between them, so non-overlap is **auditable from the files alone**
rather than from the process that wrote them. The first accepted window in a
stratum has no predecessor: it records `previous_window_id: null` and no
interval, and that path needs its own control rather than a special case nobody
tests.

Two freshly issued window IDs over identical retained samples **refuse as
overlap** — which is the back-to-back case, caught because the rule is over
indices rather than over IDs, timestamps or digests, all three of which differ
while the samples are the same.

And after any invalidation the ring must **refill completely — 256 ms of
continuous stream** — before a complete window exists at all; `acquire_window`
reports `INSUFFICIENT_WINDOW` until it does.

**B. Two stratum descriptions describe windows the ring cannot issue.**
`rf_validation_manifest.py:295` defines `GAIN_STEPS` as "A gain change part-way
through the window". `GAIN_CHANGE` is in `INVALIDATION_REASONS`
(`rf_iq_ring.py:96`) and `IQRetentionOwner.set_gain_db` calls it
(`rf_iq_retention.py:786`), so the ring is cleared at the gain change and no
window can straddle it. The same holds for `RETUNE`. Redefine both as the
**first complete post-invalidation window**:

- `GAIN_STEPS` — first complete window after `GAIN_CHANGE`, linked to the
  before and after gain declarations.
- `RETUNE_TRANSIENTS` — first complete window after `RETUNE`, linked to the
  before and after tuning declarations.

Neither contains samples from both regimes. What each stratum tests is therefore
**settling behaviour in the first window of a new configuration**, which is the
honest version of the thing the old wording gestured at.

**D. The lock cannot see a stratum being redefined.** `_strata_digest()`
(`rf_validation_manifest.py:219`) hashes `key:minimum_windows:buildable` and
**not `description`**. So correction B — which changes what two strata *mean*
without changing their names, counts or buildability — is invisible to
`PromotionCorpusLock`. That is the identical gap `validation_family_revision`
was added to close: "the bound count alone would not notice a family whose
membership was rewritten while its size stayed the same."

Doing B now, before any lock exists, is free. Doing it after a corpus opened
would silently change what 5 561 recorded trials were trials *of*.

The resolution is a revision constant, **not** a hash over prose:

```python
STRATA_DEFINITION_REVISION = "rf-null-strata.v2"
```

frozen in `PromotionCorpusLock` and included in `_strata_digest()`.

**Descriptions are not hashed.** Editorial punctuation must not redefine a
population, and a digest over sentences fails in both directions at once: a
comma becomes a strata change, while a genuine redefinition that reuses the same
words stays invisible. The revision advances when the **operational meaning**
changes. B is such a change, which is why v2 exists and why the constant is
declared at the same time as the redefinition.

The redefinition and the revision must land **before** the first
`PromotionCorpusLock` is created.

**C. §5.19's freeze refusal creates a selection-lock catch-22.** §5.19 refuses
"`PromotionCorpusLock` freeze … while tuner-dependent strata are absent", and
`rf_null_corpus.py:401` says so at runtime. But the detector configuration must
be frozen **before** the promotion corpus sees its first window — otherwise
thresholds are tuned against the same windows that validate them, which is the
failure `PromotionCorpusLock` exists to prevent. The refusal as written forbids
the precommitment until after the thing it precommits to.

The implemented object already supports the correct reading: `PromotionCorpusLock`
holds `method_revision`, `decision_threshold`, `preprocessing_revision`, the
digests, the bound count and the per-bound alpha — **no window counts at all**.
It is a configuration precommitment and never was a completion certificate.
§5.19's refusal describes an object the code does not implement.

Split the two states explicitly:

| State | Created | Means |
|---|---|---|
| `PromotionCorpusLock` | **before** the first generated or captured promotion window | configuration precommitment |
| `CorpusCompletionReceipt` | only after all twelve strata satisfy their declared counts | the corpus is complete |

An incomplete corpus may be **configuration-frozen** but can never be
completion-certified or promotion-eligible.

§5.19's refusal now states this split directly — it was **corrected in place**
rather than superseded from here, because a superseded accepted rule is still an
accepted rule and two of them is one too many.

**Correction C is complete.** The code half landed in Phase 3a (`d70fc4c`):
`may_freeze` and `freeze_note` are gone with no compatibility alias,
`plan_state()` reports configuration precommitment and completion eligibility
separately, and `CorpusCompletionReceipt` exists — pure, filesystem-free, and
refusing any lock whose frozen declarations disagree with what is declared now.

#### The eight required definitions

| Decision | Proposed authority |
|---|---|
| **Representation** | One immutable `.iqc` file per `IQWindow`, in the exact framing declared below — magic, fixed format version, fixed-width header length, canonical UTF-8 JSON header, payload, EOF. Little-endian `complex64`, exactly 524 288 samples and 4 194 304 payload bytes. No compression, no pickle, no NumPy object arrays, no native-byte-order ambiguity. |
| **Directory** | `/home/spectrcyde/scythe-validation-corpus/captured-v1`, resolved before use and disjoint from the repository, ledger, derived-evidence and observation namespaces. Subdirectories derive only from `corpus_id` and the three closed stratum names. |
| **Permissions** | Corpus directories `0700`; files and manifests `0600`; owner must match the process UID. Local ext4 only for v1. Symlinks, unexpected hard links, wrong ownership, permissive modes, network filesystems and mount or device changes refuse **before** publication. |
| **Encryption** | **NONE in v1**, stated explicitly. Permissions and namespace isolation are access controls, not encryption. No status field or receipt may imply encrypted storage. Moving or copying the corpus to another medium is unauthorised. |
| **Retention** | An absolute `delete_not_after`, supplied **before** the corpus opens and bounded by `delete_not_after ≤ PromotionCorpusLock.opened_at + 90 days`. After a Phase 3 decision or an abandonment, deletion is due at the **earlier** of that fixed deadline or 30 days after the decision. An absent, unbounded or already-expired deadline refuses capture. |
| **Deletion** | Enumerate the **recognised** final files and their derived temporary names, `unlinkat` exactly those targets, and `fsync` each affected directory. **Never a recursive delete.** An unrecognised entry yields `NAMESPACE_NOT_EMPTY_UNEXPECTED_ENTRY`, is **preserved**, and withholds the empty-namespace receipt. On a namespace holding nothing else, report `NAMESPACE_REMOVED_BYTES_NOT_ATTESTED_DESTROYED`. **No claim of secure erasure**: ext4 journalling, SSD block remapping, snapshots and backups each defeat that conclusion. |
| **Access surface** | One orchestrator-owned writer and one offline Phase 3 reader. No HTTP route, no MCP method, no browser surface, no GraphOps message, no generic download, no arbitrary-path reader. Status exposes counts, digests, deadlines and refusal codes only. |
| **Exclusions** | Samples never enter logs, exceptions, `repr`, diagnostics, APIs, model context, observation records, Git history, CI artefacts, crash reports, swap-oriented fallback, cloud sync or ordinary backups. The repository must **reject** `.iqc` content rather than ignore it silently — an ignore rule hides the mistake it is meant to prevent. |

#### Why two of the eight rows are not what was first asked for

**The retention anchor had to move.** "No later than 90 days after the first
captured window" cannot be checked at the moment it is supplied: the first
capture has not happened, so the check defers to a future event. That is a
promise, not a bound. `PromotionCorpusLock.opened_at` **exists** when the
deadline is supplied and is already frozen in the lock, so
`delete_not_after ≤ opened_at + 90 days` is verifiable at the moment of the
decision it governs. The 30-day figure keeps its meaning as the normal case
rather than the limit: after a Phase 3 decision or an abandonment, deletion is
due at the earlier of the two.

**Deletion's authority is over files this corpus created**, identified from the
corpus's own records — not over everything in a directory. A routine that
removes what it does not recognise is a recursive delete with extra steps, and
it would be at its most destructive precisely when something unexpected has
happened, which is when it is least entitled to act. So an unrecognised entry
stops the deletion, is preserved, and withholds the receipt: the namespace is
not empty, and no receipt may say it is.

*The refusal codes named here — `NAMESPACE_NOT_EMPTY_UNEXPECTED_ENTRY` and
`NAMESPACE_REMOVED_BYTES_NOT_ATTESTED_DESTROYED` — are **provisional names**.
The mechanical check in `test_scythe_verdict_vocabularies.py` runs over declared
token tuples in code, so it has not seen these and cannot have passed them. They
are subject to it at declaration, and to renaming rather than to argument.*

#### The sample is fixed before it is collected

**Exactly 5 561 accepted files per captured stratum. Exactly 16 683 in total.**
The 5 562nd is refused **before anything is written** — not trimmed afterwards,
not accepted and excluded at analysis time.

The bound is a **zero-failure** bound. A corpus that observes a false DIGITAL
**fails**; it does not keep collecting until its bound improves. Continuing
after looking is optional stopping, and an upper bound computed on a sample
whose size depended on the results it saw is not the bound that was published —
it is a description of how long someone was willing to keep going, which is the
same failure `PromotionCorpusLock` exists to prevent one layer up.

It is also what makes the storage figure below a **boundary** rather than an
estimate. An uncapped corpus has no stated size, and "at least 80 GiB free"
would be a guess about someone's stopping behaviour.

*A naming note, recorded rather than fixed here.* `Stratum.minimum_windows` is
documented as "a floor for that stratum, not a share of a quota". For the three
captured strata the floor and the ceiling **coincide at 5 561, for unrelated
reasons**: the floor is what the bound requires, the ceiling is what optional
stopping forbids. The field name describes only the first of those, and a reader
who trusts the name would not find the second.

#### What it costs on disk

At exactly 16 683 captured windows the payload alone is **69 973 573 632 bytes — 65.17
GiB**, before headers, manifests and the temporary sibling each publication
creates. Preflight requires **at least 80 GiB free**, so filesystem overhead and
in-flight publication do not turn the last stratum into a disk-pressure
experiment.

The figure is the **declared total of the strata the corpus was opened for**,
not of what is reachable today. With `RECEIVER_SPURS` unreachable only 43.45 GiB
of it can currently be written — but a corpus sized against what happens to be
buildable this month would need re-preflighting the moment the twelfth stratum
arrived, and a check that passes because a stratum is missing is not a check.

*Recorded as an observation, not a guarantee: the pinned host reported 922 GiB
available on 2026-09-14. Preflight still checks, because a figure written in a
document is not a measurement of the filesystem at capture time.*

#### The `.iqc` framing

"A header and a payload" is a shape, not a representation. The exact framing:

```
offset            field
0                 magic             8 bytes, fixed
8                 format_version    uint16, little-endian, fixed
10                header_length     uint32, little-endian
14                header            header_length bytes, canonical UTF-8 JSON
14 + header_length payload          sample_count × 8 bytes, little-endian complex64
                  EOF               immediately after the payload
```

The fixed values, declared rather than described:

```python
IQC_MAGIC            = b"\x89SCYIQ\r\n"            # exactly 8 bytes
IQC_FORMAT_VERSION   = 1                            # uint16, little-endian
IQC_HEADER_SCHEMA    = "scythe.iq-capture-window.v1" # the JSON `schema` token
IQC_MAX_HEADER_BYTES = 65_536
```

The magic borrows PNG's construction for PNG's reasons: **byte 0 has its high
bit set**, so a seven-bit-clean transfer path corrupts it detectably, and the
trailing **`\r\n`** catches line-ending translation. A file that survived a
text-mode copy is not a corpus member and should not be readable as one.

`IQC_MAGIC` and `IQC_FORMAT_VERSION` are **not** the JSON `schema` string: a
reader must be able to reject a file it cannot parse **without parsing it**.
`header_length` is fixed-width, unsigned and little-endian for the same reason —
the header is located before it is read, never discovered by scanning.

`IQC_MAX_HEADER_BYTES` is checked **before allocating**. A `uint32` length field
can claim four gibibytes; a corrupt or hostile one must become a refusal, not an
allocation. The real header is on the order of a kilobyte.

Canonical header bytes:

```python
json.dumps(header, sort_keys=True, separators=(",", ":"),
           ensure_ascii=False, allow_nan=False).encode("utf-8")
```

`allow_nan=False` is load-bearing rather than tidy. Python's `json` emits bare
`NaN` and `Infinity` by default, which **no conforming parser accepts** — and a
`NaN` that reached the header would compare unequal to itself, so every digest
over it would verify correctly while the value it named meant nothing. Rejected
at serialisation **and** at parse. (`allow_nan=False` already appears in
`graphops_graph_resolver.py` and `graphops/evidence_fabric.py`, for this.)

The reader **re-serialises the parsed header and requires byte equality** with
what it read. Without that, two files with identical meaning and different
spacing are two different `file_sha256` values and two different filenames for
one window.

**EOF falls immediately after the payload.** A file with trailing bytes is
**refused, not truncated** — a reader that ignores what it did not expect is a
reader that can be appended to.

The header carries: schema and format revision · corpus and configuration-lock
identities · `STRATA_DEFINITION_REVISION` · `source` fixed to `CAPTURED` ·
stratum · ring-issued `window_id` and ring digest · signal-chain hash and
configuration epoch · sample count, sample rate, dtype, byte order and overlap
declaration · `first_sample_index`, `previous_window_id` and the sample interval
between them · capture times and the named clock authority · **the
stratum-specific attestation** (below) · `payload_sha256` · retention deadline.

#### The identity does not contain itself

```
payload_sha256 = SHA-256( the payload bytes )
file_sha256    = SHA-256( magic ‖ format_version ‖ header_length ‖ header ‖ payload )
```

The header carries `payload_sha256` and **never** `file_sha256`; the **filename
derives from `file_sha256`**. A digest cannot cover the header that carries it,
which is the non-recursion §13i J.7a already established for evidence records
and §16.53b records as a decision: *"a digest cannot cover the header that
carries it."* The same mistake in a new file format is still the same mistake.

Three digests, three jobs, none replacing another:

| digest | what it identifies | what it does **not** do |
| --- | --- | --- |
| ring BLAKE2s | the live source, at issue time | survive the ring moving on |
| `payload_sha256` | **the payload bytes, and nothing else** | bind the payload to the header |
| `file_sha256` | **the complete file** — it is what binds the canonical header and the payload together | exist inside the header |

The middle row was wrong in the previous revision, which claimed
`payload_sha256` bound samples to header. It does not: it is a digest over the
payload alone, and a payload could be moved under a different header without
disturbing it. **`file_sha256` is the binding**, because it is computed over the
canonical header and the payload as one span — which is also why it cannot live
inside the header, and why the filename is where it goes.

A file whose name does not match its own framed bytes is not a corpus member.

Publication protocol — a failure at any step leaves **no partial final file**:

1. Hold the corpus ownership scope and verify the `IQWindow` against the live ring.
2. Recheck descriptor, mount, directory ownership, mode, corpus limits and retention deadline.
3. Exclusively create a `0600` temporary sibling.
4. Write the canonical header and the exact payload.
5. `fsync` the file.
6. Publish **without replacement**.
7. `fsync` the directory.
8. Read the final file back and verify `payload_sha256` **and** `file_sha256` before counting it.

The ring binding is verified at step 1 and recorded in the header, because by
step 8 the ring may have moved on and can no longer confirm it — `verify_window`
would report `WINDOW_EVICTED` or `EPOCH_CHANGED` for a window that was entirely
genuine when it was taken.

Orphan temporaries are never corpus members and remain subject to the same
retention deadline.

#### The typed capture boundary

**Do not expose `write_window(window, stratum)`.** A generic writer with a label
argument is the hole §13k L.1 refused in the derived-evidence producer, for the
same reason: the label becomes a caller's claim rather than a control path's
attestation.

Three nominal entrypoints instead, each taking its event attestation from the
control path that produced it:

```
record_gain_step(window, gain_event, scope)
record_retune_transient(window, retune_event, scope)
record_receiver_spur(window, spur_attestation, scope)
```

Each accepts an exact `IQWindow`, verifies it against the ring, and refuses a
caller-supplied `source` label outright.

**The attestation is a closed union selected by stratum, not a universal
before/after field.** A gain change and a retune each have a control action with
a before and an after; a spur has neither, and a header that required them would
force `RECEIVER_SPURS` to fabricate two declarations to satisfy a schema:

| stratum | attestation | members |
| --- | --- | --- |
| `GAIN_STEPS` | `GainStepAttestation` | `event_id`, `gain_db_before`, `gain_db_after`, `invalidation_epoch` |
| `RETUNE_TRANSIENTS` | `RetuneAttestation` | `event_id`, `centre_hz_before`, `centre_hz_after`, `invalidation_epoch` |
| `RECEIVER_SPURS` | **no constructible member** | — |

Nominal types, `type(x) is T`, no generic mapping. The union is closed: a
stratum with no member has no way to produce a header, so
`record_receiver_spur` **refuses by construction** rather than by a check
someone could relax.

A tuner operation is evidence for a gain or retune event. **Having an RTL2838
attached is not evidence that a feature is an internal receiver spur** — that is
an identification, and it needs a protocol that says how a candidate is
established as internal rather than received.

#### What acceptance does not unblock

This follows from the union and is the most important consequence in the
section, so it is stated rather than left to be derived.

**Accepting §5.20 does not make the corpus buildable.** It makes **two** strata
reachable in principle:

| | windows | payload |
| --- | ---: | ---: |
| `GAIN_STEPS` + `RETUNE_TRANSIENTS` | 11 122 | 43.45 GiB |
| `RECEIVER_SPURS` — still unreachable | 5 561 | 21.72 GiB |
| declared total | 16 683 | 65.17 GiB |

`RECEIVER_SPURS` is **still unreachable, and no longer for this reason.** When
this was written it had no identification protocol at all. §5.21 supplies one and
is accepted; §5.22 supplies the parameters it deliberately left free and is
accepted too. What is missing now is **execution**: no catalogue exists, no
feasibility check has been run against an actual **S**, and no window has been
captured. A method nobody has run identifies nothing.

`plan_state()` continues to report `CORPUS_INCOMPLETE_AWAITING_CAPTURE` with
`completion_eligibility: COMPLETION_BLOCKED_AWAITING_CAPTURE`, and **no
`CorpusCompletionReceipt` can be issued**. *(This paragraph once said
`may_freeze` stays `False`. That key no longer exists — Phase 3a removed it with
no alias, because a key keeping the old name and the old meaning would be the
contradiction preserved under a synonym.)*

The twelfth stratum needed a section of its own and got **two** — §5.21 for how
a feature is discriminated, §5.22 for the estimand, the trial unit and the
eleven parameters. §5.20 must still not be read as having supplied either.

#### The observation boundary

**The capture writer must not import or invoke the detector**, enforced by a
static import-closure check — the instrument Amendment O already uses
(`_first_party_closure()`, `_dynamic_imports()`), which exists because importing
a module *executes its graph* and a pure function reached through an impure
module is not a pure function.

The sequence is fixed:

1. Collect the fixed sample **without reading any outcome**.
2. Issue the `CorpusCompletionReceipt`.
3. Evaluate **once**, offline.

A false DIGITAL then **fails that frozen corpus**. It cannot cause replacement
captures, additional trials, or a revised threshold.

The cap above forbids **continuing after looking**. This forbids **being able to
look** — which is the stronger of the two, and the only one that survives
someone deciding the first is inconvenient at 5 200 windows with a deadline. It
is the same move as §13i J's governing claim: a prohibition stops being a rule
someone must obey and becomes a shape the code cannot take.

#### The statistical boundary

§5.20 would authorise **retaining bytes**. It does not make trials independent
and it does not make labels true.

A subsequent capture-plan section must define how the 5 561 events per tuner
stratum are distributed across time, tuning, gain and receiver conditions.
Reusing overlapping windows, or drawing thousands of windows from one tuner
event, must not be counted as thousands of independent trials — the Clopper–Pearson
bound assumes independence it cannot check, and 5 561 windows from a handful of
events would produce a number with the right shape and no meaning.

#### What this section does not authorise

No directory is created and no byte is written by this text. It authorises no
live acquisition, no `rtl_tcp` connection, no capture session, and no change to
the position-attestation act: entry 7 of `PENDING_AMENDMENTS.md` and §17 slice 11
remain exactly as blocked as before.

#### What acceptance started, and what it did not

The acceptance decision and this commit are the whole of what has happened. No
directory exists, no byte has been written, and the three strata remain
uncaptured.

Corrections **A**, **B**, **C** and **D** land as code with their own negative
controls **before the first `PromotionCorpusLock` exists** — **D** especially,
because it is the one that stops being free afterwards.

Every rule here needs a control that fails when it is removed:

| control | what must break |
| --- | --- |
| capture cap | the 5 562nd window is accepted |
| framing | a trailing byte is not refused; a header over `IQC_MAX_HEADER_BYTES` is allocated; a bare `NaN` serialises |
| canonicality | a re-spaced header yields a second filename for one window |
| identity | a header quietly accepts a `file_sha256` field |
| non-overlap | two window IDs over identical retained samples are both accepted |
| first-in-stratum | the no-predecessor path is never exercised |
| retention | a deadline one second past `opened_at + 90 days` is accepted |
| deletion | an unrecognised entry survives a deletion pass, or a receipt is issued over a non-empty namespace |
| attestation union | `record_receiver_spur` constructs a header |
| observation boundary | the writer's import closure reaches the detector |

Each needs the collateral scan as well: a mutation that makes exactly its own
tests fail and no others.

### 5.21 — receiver-spur identification — **ACCEPTED**

```text
Status:     ACCEPTED 2026-09-14. The discrimination method is in force.
Authority:  §5.21 is accepted. It authorises no capture, no termination, no
            catalogue and no byte. It is a method, not a procedure: every
            decisive parameter lives in §5.22.
Opens:      Nothing on its own. It removes the gate §5.20 named — RECEIVER_SPURS
            now has a way to be identified — and removes no other obstacle.
Does not:   Make RECEIVER_SPURS achievable, or prove any feature internal.
            Every usable class is CONSISTENT_WITH_ something, at a governed
            confidence, under an OPERATOR_DECLARED termination that no software
            here can verify.
```

*Proposed 2026-09-14 at `695fb5a`. Review found four claims wrong and they were
corrected at `91e7369`: the retune partition was three cases where the mixing
family is affine with integer and rational slopes; the independence argument was
categorical where the real defect is unrepresentativeness; the labels claimed
internality where they can only claim consistency; and the signal-chain
asymmetry was **backwards** — `gain_db` is hashed into the manifest and
`set_gain_db` rebuilds the chain before invalidating, so `GAIN_STEPS` spans two
identities and `RETUNE_TRANSIENTS` spans one.

Review then found four more, corrected at `6d1cbf7`: "the same instrument turned
down" contradicted this repository's own semantics, where gain is inside the
identity; termination was simultaneously "the only test that decides" and an
observation that proves nothing; a monotonic sweep confounds tuner frequency
with time, so "no received emission behaves this way" was true of a *static*
emitter and asserted of every emitter; and the reconnect rule excluded
session-scoped features as "not a property of the receiver" when USB seams, PLL
settling and initialisation transients are receiver-system behaviour — and are
periodic and narrow, which makes them close to the exact population this stratum
exists to test. **Accepted on the corrected substance**; this is the acceptance
commit.*

*Two readings are pinned so they are not recovered later by inference. Accepting
a **method** is not accepting a **procedure**: K, R, the retune deltas, the
tolerances, the margins, the order, the coordinates and both accepted
termination confidences are §5.22's, and §5.21 sets none of them. And acceptance
proves nothing internal — it establishes how the question is asked, not what the
answer is for any feature.*

§5.20 left one sentence deliberately unresolved:

> Having an RTL2838 attached is not evidence that a feature is an internal
> receiver spur — that is an identification, and it needs a protocol that says
> how a candidate is established as internal rather than received.

This is that protocol. It is also the section that says plainly what the
protocol cannot buy.

#### What the stratum actually needs

**Not a catalogue of every spur.** `RECEIVER_SPURS` is a *null* population: a
set of windows a validated detector must not call DIGITAL. The claim each window
carries is **"this window contains at least one internal spurious product"** —
not "this feature is spur number seven". That is a much weaker claim, and it is
the one the protocol has to earn.

It is not a decorative stratum. `rf_symbol_clock` already registers
`PERIODIC_TRANSPORT_ARTEFACT` as a known false-positive mode — *"a periodic
buffer or USB seam is a genuine cyclic feature"* — and internal artefacts are
precisely the population most able to fool a symbol-clock detector, because
many of them **are** periodic and narrow. This stratum tests the detector where
it is weakest.

#### Two observable behaviours, and exactly what each can decide

**Retune.** Move the tuner and measure the **slope** — not the two cases an
earlier draft of this section named.

An internal mixing product appears at RF at ``m·f_LO + n·f_ref`` for integers
``m`` and ``n``, so after downconversion it sits at

```
f_bb = (m − 1)·f_LO + n·f_ref          slope s = Δf_bb / Δf_LO = m − 1
```

The family is **affine in both oscillators**, and its slopes are integers — or
rationals, where a divider chain puts ``f_VCO = 2^k · f_LO`` into the product.
Slope 0 (``m = 1``) and slope −1 (``m = 0``) are two members of it, not the
whole partition, and the earlier framing overstated what retune decides.

What retune actually settles is sharper than that framing and in one direction
stronger:

> **A received emission has slope exactly −1.** Nothing else about it is
> negotiable: its absolute frequency is fixed, so its baseband offset moves by
> −Δf and by nothing else.

| measured slope | what it can be |
| --- | --- |
| −1 (within tolerance) | a received emission **or** the ``m = 0`` internal product — retune cannot separate them |
| any other slope in the modelled family | **no received emission behaves this way**; consistent with internal mixing at ``m = s + 1`` |
| a slope outside the modelled family | `SPUR_CANDIDATE_UNRESOLVED` — refused, not rationalised |

So the undecidable set is exactly ``s = −1``, and termination is what decides
it. Every other slope is *consistent with* internal mixing — which is a
different claim from *proven internal*, and §5.21 keeps them different.

The slope is measured over **at least three declared retunes**, because two
points fit any line and a two-point slope is an assumption wearing a
measurement's clothes.

**And a monotonic sweep confounds tuner frequency with time.** If LO settings
are visited in order, an external signal that drifts, hops, or is itself being
retuned traces a baseband path that can fit any slope at all, including slopes
no receiver product could produce. The claim "no received emission behaves this
way" is then stronger than the experiment that produced it — true of a *static*
emitter and asserted of every emitter.

Three requirements follow, and all three are pinned to §5.22 rather than set
here:

- **Randomised or counterbalanced retune order**, so frequency and time are not
  the same variable.
- **Repeated visits to the same LO settings**, so a feature that moved between
  two visits to one setting is caught as non-stationary rather than fitted.
- **Signed baseband coordinates**, with wrap and folding at band edges handled
  explicitly. A magnitude loses the sign that distinguishes slope +1 from −1,
  and a feature folded at an edge reverses its apparent direction of travel —
  either one manufactures a slope out of bookkeeping.

*(The reference is 28.8 MHz on most R820T2 dongles and 24 MHz on some. The
device's own must be **declared**, never assumed: a protocol that guessed the
crystal would be inventing the instrument, which is the §5.15 mistake. The same
goes for the divider family that makes rational slopes possible.)*

**Termination.** Replace the antenna with a 50 Ω load.

A received emission drops by tens of dB. An internal product does not.

**Termination is the required discriminator for every usable class**, not an
optional corroboration for one of them. For slope −1 it is also the *only*
discriminator available, which is why that class carries the higher confidence
bar rather than a different requirement. An earlier draft had it both ways —
"the only test that decides" in one paragraph and "proves nothing, ingress
remains possible" in the next — which is not a nuance, it is two incompatible
statements about the same act.

It costs something structural:

> Terminating changes the antenna, which changes `signal_chain_manifest`, which
> changes `signal_chain_hash`, which raises `SIGNAL_CHAIN_CHANGE` and clears the
> ring.

So a spur attestation **spans two signal-chain identities** — and an earlier
draft of this section drew the wrong contrast from that. Corrected against the
code:

| stratum | chain identities spanned | why |
| --- | --- | --- |
| `RETUNE_TRANSIENTS` | **one** | `signal_chain_hash` deliberately excludes centre frequency — retuning is its own invalidation reason and folding it in would make every retune look like a different antenna |
| `GAIN_STEPS` | **two** | `gain_db` **is** hashed into the manifest, and `IQRetentionOwner.set_gain_db` calls `_rebuild_chain_locked()` **before** `invalidate("GAIN_CHANGE")` |
| `RECEIVER_SPURS` | **two** | the antenna is hashed too, and termination replaces it |

"The other two strata span one chain" was simply false. Two of the three span
two identities, and the spur case is distinguished not by *how many* but by
**what moved**:

| stratum | same receiver? | same front end? | what differs |
| --- | --- | --- | --- |
| `GAIN_STEPS` | yes | **yes** | the declared gain configuration |
| `RECEIVER_SPURS` | yes | **no** | the front end itself |

Stated that way rather than as "the same instrument turned down", which
contradicts this repository's own semantics: gain is *inside* the signal-chain
identity, and `set_gain_db` says in as many words that a window spanning a gain
change "would compare two different instruments". A phrase that quietly
redefined *instrument* to exclude gain would have undone §5.17's whole argument,
where the same mast at two extensions is two instruments.

§5.20's closed attestation union still needs a distinct spur member — for the
front-end difference, not for a redefinition of the word.

#### What software cannot establish here

**The termination is a physical act.** SCYTHE cannot verify that a 50 Ω load is
fitted. It can carry an `OPERATOR_DECLARED` claim and nothing stronger — the
same authority class as a position fix, and it should be labelled as bluntly.

**Ingress is not excluded by a load.** A poor termination, case pickup, or
USB-borne coupling can put a strong local emission into a "terminated" capture.
At the pinned host's venues that means broadcast FM tens of dB above everything
else. **Without a screened enclosure this cannot be ruled out**, and a protocol
that claimed otherwise would be asserting a property of a room nobody measured.

So the catalogue carries a **confidence class, not a boolean** — and the class
names say *consistent with*, because none of them is proof:

**Every usable class requires an accepted termination/ingress confidence.** The
two differ in how much *else* they have, and therefore in where the accepted
level is set — not in whether termination is required:

| class | established by | discriminators | usable |
| --- | --- | --- | --- |
| `CONSISTENT_WITH_INTERNAL_MIXING` | slope ≠ −1 inside the modelled family, **and** persists under declared termination | **two**, independent | yes, at the lower accepted confidence |
| `CONSISTENT_WITH_INTERNAL_REFERENCE` | slope −1, persists terminated, matches a rational multiple of the **declared** reference | **one** — a model match is not a second | yes, only at the higher accepted confidence |
| `SPUR_CANDIDATE_UNRESOLVED` | persists terminated, fits no modelled slope | — | no |
| `VANISHES_ON_DECLARED_TERMINATION` | the feature is gone with the load fitted | — | no |

The second row is the one an earlier draft got wrong by calling it
`SPUR_INTERNAL_CLOCK_LOCKED`. **A model match is not a discriminator.** A
broadcast carrier that happens to sit near ``n·f_ref`` is ingress that matches
the model, and matching it cannot silently upgrade an `OPERATOR_DECLARED`
termination into physical proof. The reference comb is dense enough across a
wide span that coincidence is ordinary rather than remarkable.

Both accepted levels are **governed rather than assumed**, and pinned to §5.22
with the rest of the free parameters. Shielding, a second site, or a genuine
second discriminator raises a confidence; a declaration on its own does not.

**Stability is a separate axis, not a filter.** Where a feature survives to is
recorded alongside its class rather than used to exclude it:

| stability | survives |
| --- | --- |
| `SESSION_SCOPED` | within one capture session, not across a reconnect |
| `RECONNECT_STABLE` | across `DISCONNECT` / `RECONNECT`, not across a power cycle |
| `POWER_CYCLE_STABLE` | across a power cycle |

An earlier draft excluded anything that did not survive a reconnect, on the
grounds that it "is not a property of the receiver". **That was wrong and is the
most consequential of these corrections.** A USB transport seam, a PLL settling
artefact, an initialisation transient — these are session-scoped and they are
receiver-system behaviour. Worse for the earlier rule: they are *periodic and
narrow*, which makes them the population `rf_symbol_clock`'s registered
`PERIODIC_TRANSPORT_ARTEFACT` mode describes, and therefore close to the exact
population this stratum exists to test. A filter that discarded them would have
thrown away the most dangerous members of the stratum for being insufficiently
permanent.

`SPUR_CANDIDATE_UNRESOLVED` is a real state and not a holding pen: a feature
that survives termination and fits no modelled slope is the one most likely to
be ingress, and it is refused for exactly that reason.

The four names are **provisional**. The mechanical check in
`test_scythe_verdict_vocabularies.py` runs over declared token tuples in code
and has not seen them, so it cannot have passed them.

#### The procedure

**Catalogue first, under declared termination.**

1. Declare the termination and record the terminated chain hash.
2. Sweep the tuner across the band in **K** steps, declaring each tuning.
3. At each tuning, wait for a full refill and acquire complete windows.
4. Retain features exceeding a declared margin above the local noise floor and
   persisting across **R** repeats at that tuning.
5. Classify by retune behaviour into the four classes above.
6. Repeat after a **reconnect** and after a **power cycle**, and record the
   resulting **stability** — `SESSION_SCOPED`, `RECONNECT_STABLE`,
   `POWER_CYCLE_STABLE`. This classifies; it does not exclude. `DISCONNECT` and
   `RECONNECT` are already invalidation reasons, so the epochs separate cleanly
   and a session-scoped feature is visible as such rather than as noise.
7. Optionally repeat on a second unit of the same model. A feature common to two
   units is a design artefact; one unique to a unit belongs to that unit, and
   the catalogue must say which, because a corpus built on one dongle's private
   birdie does not generalise to the model.

**Then capture stratum windows — also terminated.** The window must contain
internal products and *nothing else*, or it stops being a `RECEIVER_SPURS`
window and starts overlapping `TWO_SIGNAL_COLLISIONS` and
`ADJACENT_CHANNEL_INTERFERENCE`.

Which makes the distinction from `THERMAL_NO_INPUT` sharp and checkable:

| stratum | input | tuning |
| --- | --- | --- |
| `THERMAL_NO_INPUT` | terminated | where **no** catalogued spur falls in the span |
| `RECEIVER_SPURS` | terminated | where **at least one** catalogued spur falls in the span |

*Which hands `THERMAL_NO_INPUT` an obligation it does not currently have.*
Nothing has ever checked that its tunings are spur-free. A **slope-0** product
sits at a fixed baseband offset and is therefore in span at **every** tuning, so
if any exists, a naively captured "thermal, no input" window contains it.

The defect is not that Bonferroni breaks — it tolerates arbitrary dependence
and stays valid. It is three other things: `THERMAL_NO_INPUT` windows would be
**mislabelled**, two of thirteen bounds would spend **alpha on one population**
while the correction is paid for thirteen, and the **spur-free thermal
population the stratum was meant to cover would never be tested at all**. That
has to be settled before either stratum is captured, and it is a finding of this
section rather than a part of its proposal.

#### The part that may have no answer

**A protocol that identifies spurs perfectly does not make the stratum
achievable.**

A receiver has a *finite* number of internal spurious products. Call it **S** —
plausibly tens, not thousands. The stratum needs **5 561 windows**.

An earlier draft said flatly that five thousand observations of twenty spurs
"is not five thousand independent trials". **That was too categorical and is
withdrawn.** Repeated observations of one fixed spur *can* be independent
Bernoulli trials: if the random component — thermal noise, acquisition timing,
converter state — is independent between windows, then so are the trials. What
repetition fails to supply is not independence. It is **distinct spur
identity**, which makes the sample potentially *unrepresentative* rather than
automatically *dependent*. Those are different defects and they have different
remedies.

Which means the question cannot be answered at all until the **estimand** is
named, and naming it is the first thing §5.22 must do:

| if the estimand is | then repeated spurs |
| --- | --- |
| the false-DIGITAL rate over windows drawn from a **frozen operational distribution** | **can contribute** — subject to non-overlap, temporal blocking, randomised allocation of tuning and epoch, and dependence diagnostics that are reported rather than assumed |
| **generalisation across distinct internal products or receiver units** | cannot. Twenty spurs do not become 5 561 experimental units by being looked at more often |

The bound cannot check independence, which is why the diagnostics have to be
reported. But "cannot check" is not "is violated", and this section should not
have written the second while meaning the first.

Four ways out, none free, none chosen here:

1. **Redefine the trial unit** as `(spur, tuning, epoch)` and require 5 561
   distinct combinations. The multiplication reaches the number easily — a
   slope-0 product is in span at every tuning, so K tunings × E power cycles
   grows fast. **That is not a justification.** Reaching 5 561 by multiplying
   available axes answers an arithmetic question, and the open question is which
   estimand those units are units *of*. §5.22 chooses the estimand first and the
   trial unit second; doing it the other way round is how a corpus ends up
   measuring its own convenience.
2. **Accept a weaker bound for this stratum alone** and publish it as weaker.
   Honest, and it breaks the symmetry §5.18 chose option C to preserve.
3. **Use several receivers**, which changes what the corpus is about: a claim
   about a family of devices rather than about this instrument.
4. **Drop the stratum from the promotion family.** Not free and not local:
   `TESTED_BOUND_COUNT` falls 13 → 12, `PER_BOUND_ALPHA` rises to 0.0041666667,
   and **every other stratum's requirement falls with it** — the exact
   zero-failure minimum goes 5 558 → 5 478, and the corpus goes from twelve
   strata to eleven. It is also **forbidden after a `PromotionCorpusLock`
   exists**, because the lock freezes the strata set and the bound count, which
   is precisely what that lock is for.

Option 4 is why this section comes **before** the capture plan and before the
persistence mechanism. Deciding it after a lock is not a decision, it is a
corpus thrown away.

*A note on the numbers used above, which are not the ones in the code.*
`MINIMUM_TRIALS_FOR_ZERO_FAILURES_CORRECTED` is computed as
`ceil(-ln(alpha) / rate)` — the rule-of-three asymptotic, honestly labelled as
such in its comment — and yields **5 561**. Inverting the exact Clopper–Pearson
bound gives **5 558**: at n = 5 558 the exact upper bound is 0.00099998, already
under 0.001. The code is therefore **three windows per stratum conservative**,
36 across the corpus. That is the safe direction and is not a defect; it is
recorded because the constant is named a minimum and the exact minimum is a
different number, and because this section quotes both.

#### Every decisive parameter is pinned to §5.22, and none is set here

This section proposes a **method of discrimination**. It does not propose an
executable procedure, and accepting it must not be readable as accepting one.
Each of the following is named here, deliberately left free, and belongs to
§5.22 with the estimand:

| parameter | what it decides |
| --- | --- |
| **K** — number of tuning steps, and their spacing | how much of the band the catalogue covers, and whether slope estimates are conditioned on one corner of it |
| **R** — repeats per tuning | what "persists" means |
| retune deltas | the lever arm of every slope estimate |
| slope-matching tolerance | which slopes count as inside the modelled family |
| frequency-matching tolerance against ``n·f_ref`` | how often coincidence passes as a model match |
| persistence margin above the local noise floor | what counts as a feature at all |
| **retune order** — randomised or counterbalanced | whether frequency and time are separable at all |
| repeated visits per LO setting | whether a non-stationary emitter is caught or fitted |
| signed baseband coordinates, wrap and folding at band edges | whether an apparent slope is physical or bookkeeping |
| both accepted **termination/ingress confidences** | whether either `CONSISTENT_WITH_INTERNAL_*` class may attest anything |
| stability classes required per attesting spur | whether a `SESSION_SCOPED` product may carry a stratum window |

Fixed here, they would be numbers chosen before the estimand that gives them
meaning. §5.22 is where they land, and until then this section decides how to
tell two things apart and not how many times to look.

#### What this section does not authorise

No capture. No termination. No catalogue. No tuner operation, no `rtl_tcp`, no
persistence, no byte. It answers a question and creates no permission — and it
must be read alongside the fact that answering it may still leave
`RECEIVER_SPURS` unreachable at 5 561 units of whichever estimand §5.22
chooses.

#### What acceptance would require

An explicit acceptance decision and an acceptance commit. Then, in order and not
together: the **capture plan** (§5.22), which must resolve the independence
question above rather than inherit it; and only then the persistence mechanism,
with `PENDING_AMENDMENTS` entry 9 landed first, since §5.20's publication step 1
still asks for an attestation that does not exist.

### 5.22 — the capture plan — **ACCEPTED**

```text
Status:     ACCEPTED 2026-09-14. The estimand, the trial unit and the eleven
            parameters are in force.
Authority:  §5.22 is accepted. It authorises no capture, no tuner operation, no
            catalogue, no persistence and no byte.
Order:      Estimand, then trial unit, then parameters — now settled in that
            order rather than proposed in it.
Binds:      The promotion claim is instrument-, envelope- and
            distribution-scoped, and the promotion record must carry all three.
            The aggregate bound is design-weighted and must be described as
            such. Finding A must land before the first PromotionCorpusLock, and
            option 4 expires at that same moment.
```

*Proposed 2026-09-14 at `c216ef4` and accepted on that substance; this is the
acceptance commit. Naming the estimand was the whole of the work and it ruled
out the answer a reader would assume: nine of twelve strata are synthetic, so
the corpus cannot estimate an operational rate, and the claim promotion would
license is narrower than "the detector's false-DIGITAL rate".*

*Three readings are pinned. **The scope limits travel with the claim** — a
promoted DIGITAL verdict is covered for this chain, inside the declared
envelope, against the validation distribution, and nowhere else. **The aggregate
is design-weighted**; describing it as an operational rate needs an amendment,
not a rewording. And **`RECEIVER_SPURS` is retained under option 1** because the
estimand is a rate over a distribution that genuinely contains those spurs
repeatedly — never because the multiplication reaches 5 561. Option 4 remains
available until a lock exists and is unavailable after it.*

§5.20 deferred this and said why: *"retaining bytes does not make trials
independent and does not make labels true."* §5.21 refused to choose a trial
unit before an estimand existed. This names the estimand.

#### The estimand, and what naming it costs

The promotion rule is a bound on **the false-DIGITAL rate**. A rate is a rate
over *some distribution of windows*, and the corpus has never said which.

The attractive answer — *the rate over windows the deployed system will see* —
**is not available**, and the reason is structural rather than fixable by
wording:

> **Nine of the twelve strata are synthetic.** A synthesised window samples a
> generator's model of a condition. It does not sample operation. §5.19
> authorised that on purpose and was right to; but a corpus three-quarters
> synthesised cannot estimate an operational rate, and saying it does would be
> the substitution every refusal in this repository exists to prevent.

So the proposed estimand is narrower and says so:

> **The false-DIGITAL rate over the declared validation distribution**: this
> signal chain, inside a declared operating envelope, over a corpus that is
> explicitly part synthetic and part captured, with each stratum's rate
> conditional on that stratum.

Three scope limits follow, and the promotion record must carry all three rather
than leave them to be inferred:

1. **Instrument-scoped.** The claim is about this receiver and this chain.
2. **Envelope-scoped.** Bands, gains and conditions outside the declared
   envelope are not covered.
3. **Distribution-scoped.** It is a rate over the validation distribution, not
   over operation. An operational-rate claim needs a differently built corpus
   and is not what promotion would license.

The synthetic strata are plausibly *adversarial* — harder than the conditions
they stand for. "Plausibly" is doing real work in that sentence, and nobody has
validated it, so it buys a conjecture and not a scope extension.

#### Finding A — the lock freezes the method and not the instrument

`PromotionCorpusLock` carries `corpus_id`, `opened_at`, `method_revision`,
`decision_threshold`, `preprocessing_revision`, two digests, the bound count,
the per-bound alpha, the family revision, the strata-definition revision and the
eligible channel purpose. **There is no signal-chain field, no sensor, no
envelope.**

Under an instrument-scoped estimand that is a hole, not a nuance: a corpus
validated on one dongle and one antenna would license the same promoted claim
from a different chain, and nothing in the lock would notice. Phase 3a's
partition test would not catch it either — it checks that every field is
covered, not that every necessary field exists.

The repair is a lock field: the frozen `signal_chain_hash`, or a declared
envelope digest when the envelope legitimately spans more than one chain. It is
an amendment to §5.20 and to `rf_validation_manifest`, it must land **before the
first `PromotionCorpusLock` exists**, and it is recorded rather than made here.

**That repair is rejected.** `gain_db` is inside the chain identity and
`IQRetentionOwner.set_gain_db` rebuilds the chain *before* raising
`GAIN_CHANGE`, so a `GAIN_STEPS` observation spans two chain hashes by
construction and **no promotion corpus has one**. The settled ruling is an
enumerated envelope of explicitly declared chains, never a single chain hash and
never a Cartesian product of separately declared sets. §5.23 carries the
amendment and **was accepted 2026-09-15, this rejection with it**. **Finding A
closed at `9e0efc8`**, when use-time envelope admission was enforced over a
capture plan that declares the corpus. It did not close at the acceptance, and
it did not close when the instrument layer alone was enforced: a contract
describing an enforcement is not the enforcement, and an envelope over a plan
that freezes nothing the corpus is made of is not the scope it claims.

#### Finding B — the aggregate bound is design-weighted, not operational

The thirteenth bound is the aggregate over all strata. With **equal** counts —
5 561 each, by §5.18 option C — the aggregate estimates the rate under a
**uniform mixture over the twelve strata**.

Nothing is uniform about operation. Thermal-no-input is most of real running
time; a gain step is rare; two-signal collisions depend on where the antenna is.
The aggregate is therefore a **design-weighted** quantity: a real, checkable
number about a distribution the designers chose, and not the number a reader
will assume it is.

Reweighting to operational prevalence would need prevalence measurements that
**do not exist**, and inventing weights would put a fabricated distribution
underneath a published bound. So the proposal is to **declare it
design-weighted** and name the mixture, rather than to improve it with numbers
nobody measured.

#### The trial unit, chosen second

**One window**, and the conditions under which windows count:

| requirement | why | already enforced? |
| --- | --- | --- |
| non-overlapping intervals | two acquisitions over one span are one observation under two IDs | **yes** — Phase 3a's `first_sample_index` rule |
| drawn under a declared allocation | an undeclared allocation is a distribution chosen after seeing results | no |
| temporally blocked | 5 561 consecutive windows measure one afternoon | no |
| randomised over conditions, from a declared seed | order and condition must not be the same variable (§5.21) | no |
| dependence diagnostics reported | the bound assumes what it cannot check | no |

Twelve conditional rates and one design-weighted aggregate. Each stratum's bound
is a statement about windows **given** that stratum, which is what a stratified
corpus measures and is weaker and more honest than a rate about windows.

#### Dependence diagnostics at zero failures

The design expects **zero failures**, and that is exactly the case where the
obvious diagnostics are worthless:

> With zero failures the outcome sequence is 5 561 zeros. It has no variance, no
> runs structure and no autocorrelation. **Every outcome-based test for
> dependence passes trivially and proves nothing.**

So the diagnostics must be defined on the **continuous detector statistic** —
the cyclic peak-to-median sidelobe ratio the registered method already
computes — rather than on the binary verdict:

- its autocorrelation across consecutive windows within a block;
- its between-block versus within-block variance, which is where blocking either
  worked or did not;
- its distribution against the declared null model, since a statistic whose
  whole distribution has shifted is the interesting failure even at zero
  crossings of the threshold.

These are **reported**, not gated on. A diagnostic that silently passed would be
the zero-failure sequence's problem again, one level up.

#### `RECEIVER_SPURS` — retain, and say what the retention costs

§5.21 set out four ways forward and chose none. Under the estimand named above,
**option 1 is defensible** — the trial unit being `(spur, tuning, epoch)` with
repeated observation of catalogued spurs.

It is defensible **because the estimand is a rate over windows from a
distribution that genuinely contains those spurs repeatedly**, not because the
multiplication reaches 5 561. A deployed receiver meets its own spurious
products constantly; that recurrence is a fact about the distribution, not an
artefact of the sampling.

What it does not buy, stated in the promotion record rather than discovered
later:

> The `RECEIVER_SPURS` bound does **not** generalise across spur types or across
> receiver units. With **S** catalogued products it is a statement about those S,
> observed under the declared allocation.

Two hard requirements follow:

- **Feasibility is checked before the corpus opens.** Distinct
  `(spur, tuning, epoch)` combinations must reach 5 561 given the actual S, K and
  E. If the catalogue comes back small, that is discovered before a lock exists,
  not after 4 000 windows.
- **The allocation over spurs is declared**, including how many windows come
  from each stability class. `SESSION_SCOPED` products are the periodic ones —
  the `PERIODIC_TRANSPORT_ARTEFACT` population — and an allocation that
  happened to under-sample them would test the stratum where it is easiest.

**Option 4 stays open until the lock exists**, and not one moment past it.
Dropping the stratum moves `TESTED_BOUND_COUNT` 13 → 12, the per-bound alpha to
0.0041666667 and every stratum's exact minimum from 5 558 to 5 478. After a
freeze it is `STRATA_CHANGED_AFTER_FREEZE` and invalidates every bound already
read.

#### The eleven parameters, derived rather than asserted

§5.21 pinned these here. Each is proposed with the arithmetic that produced it,
so the argument is with the derivation and not with the number.

**Retune deltas.** A modelled product moves `|s|·Δf`. To keep `|s| ≤ 3` inside
the half-span of 1.024 MHz, `Δf ≤ 341 kHz`. Proposed: **Δf ∈ {50, 100, 200} kHz**,
each used in both directions.

**Slope tolerance.** Slope resolution is `2·bin/Δf`; at the 3.90625 Hz bin and
the smallest delta that is **1.6 × 10⁻⁴**. Proposed tolerance **0.01** — about
sixty times the resolution, generous enough to admit a real integer slope and far
too tight to admit a neighbouring one.

**K, the tuning count and spacing.** Proposed **K = 64** across the declared
envelope, at **pseudo-random spacing from a declared seed** — never uniform.
Uniform spacing at a divisor of the reference interval would systematically hit
or systematically miss the comb, and either way the catalogue would be an
artefact of the grid.

**R, repeats per tuning.** Proposed **R = 8**, with a feature required in **≥ 7
of 8** to count as persistent.

**Persistence margin.** Proposed **≥ 10 dB above the local median** of the
surrounding bins, measured per tuning rather than globally, since the noise floor
is not flat across the envelope.

**Reference-match tolerance — and the cap it forces.** The match window is
`n · f_ref · ppm`, so it **grows with the harmonic**. Requiring the window to stay
under 0.5 % of the span gives a usable harmonic range that depends entirely on
how well the reference is known:

| reference known to | usable harmonic `n` ≤ |
| --- | ---: |
| ±100 ppm (an uncalibrated dongle) | **3** |
| ±10 ppm | 35 |
| ±1 ppm (disciplined against a reference) | 355 |

At ±100 ppm and `n = 50` the match window is **7 % of the entire analysis span**,
at which point "it matches the comb" is barely a claim at all. So: the crystal's
ppm is **declared and justified**, the harmonic cap follows from it, and
`CONSISTENT_WITH_INTERNAL_REFERENCE` is unavailable above the cap. An
uncalibrated receiver earns almost no reference matches, which is the correct
outcome rather than an inconvenience.

**Retune order.** Randomised from a declared seed **and counterbalanced**: every
tuning appears in both halves of the session sequence.

**Repeated visits.** Each LO setting visited **≥ 3 times**, separated by **≥ 10**
other settings, so a non-stationary emitter is caught between visits rather than
fitted through them.

**Signed coordinates and folding.** Baseband offsets are **signed**, on
`[−f_s/2, +f_s/2)`. Features within **5 %** of either edge are excluded from
slope estimation: a folded feature reverses its apparent direction of travel, and
an unsigned magnitude cannot tell `+1` from `−1` at all.

**Accepted termination/ingress confidences.** Two levels, because §5.21 gives the
two usable classes different amounts of evidence. `CONSISTENT_WITH_INTERNAL_MIXING`
— two independent discriminators — is accepted at **declared termination plus a
repeat at a second time of day**. `CONSISTENT_WITH_INTERNAL_REFERENCE` — one
discriminator and a model match that is not a second — is accepted only at
**declared termination plus a second physical site or a shielded enclosure**.
Neither level is reachable by asserting it.

**Required stability per attesting spur.** Any class may attest, and the
allocation must draw from **every class the catalogue contains**, with the
per-class counts declared in advance.

#### What it costs in time

16 683 captured windows at 256 ms, non-overlapping, is **4 271 s — 71 minutes**
of sample time and nothing else. Every real cost is above that floor: refill
after each invalidation, retune settling, the repeated visits, the blocking
across sessions and power cycles, and the catalogue sweep before any stratum
window exists at all. The plan should state expected wall-clock in **days**, and
a plan that claims to finish in an afternoon has quietly dropped the blocking.

#### What this section does not authorise

No capture. No tuner operation, no `rtl_tcp`, no catalogue, no termination, no
persistence, no byte, no directory. It chooses an estimand and proposes a design;
it grants nothing, and `RECEIVER_SPURS` remains unreachable until §5.21's method
is accepted and a catalogue exists.

#### What acceptance would require

An explicit acceptance decision and an acceptance commit. Then, in order:

1. **Finding A as an amendment** — the lock must freeze the instrument or the
   envelope, before any lock exists.
2. **`PENDING_AMENDMENTS` entry 9** — the full-object ring attestation §5.20's
   publication step 1 already assumes and no operation performs.
3. Only then the persistence mechanism, and only then capture.

Finding B needs no amendment if the aggregate is *described* correctly; it needs
one the moment anything claims the aggregate is an operational rate.

---

### 5.23 — the lock envelope, in two layers — **ACCEPTED**

```text
Status:     ACCEPTED 2026-09-15. The two declarations, the receiver-identity
            authorities and the drain condition are in force as contract.
Authority:  §5.23 is accepted. It authorises no capture, no persistence, no
            tuner operation, no directory and no byte, and acceptance changes
            no behaviour: no code exists yet and none is authorised by this
            commit.
Order:      §13l. This is the acceptance commit. Merge follows it, and a
            code-only implementation follows the merge.
Drains:     Entry 11, at `9e0efc8`. The lock field, the receipt propagation
            and use-time envelope admission landed together, over a capture
            plan that declares the corpus. Captured-window admission is entry
            14 and is not part of this; entry 9 precedes it.
Carries:    Two amendments to accepted text, accepted here as part of this
            section's substance and named in the acceptance below — §5.22
            Finding A, and PENDING_AMENDMENTS entry 11.
```

*Proposed 2026-09-15 at `8d837a8`, after the implementation-first attempt at
`eb1b34d` was abandoned unmerged for arriving in the wrong order. Review held it
and returned three corrections, applied at `fa268f9`: a non-unique identifier
**does not** produce a class-scoped claim — it establishes only that the unit
cannot be recovered from the identifier, so one unidentified member of a class
validates one unit; two declared sets **do not** declare their Cartesian
product, which admitted chains nobody sampled; and entry 11 **cannot** drain
while cross-chain licensing stays reachable, since a field nobody enforces makes
the defect recordable rather than repaired. This is the acceptance commit.*

**What this acceptance accepts, named rather than left to the merge.** §13l is
explicit that a merge supplies no acceptance, so all three are listed here:

1. **§5.23 itself** — two declarations bound into the lock before the first
   window; `InstrumentChainEnvelope` as an explicit canonical set of
   `ChainMember`s, one member to one chain hash; `CapturePlanDeclaration`
   binding the materialized ordered schedule rather than its seed;
   self-validating declarations; the three receiver-identity authorities and
   the rule that carries them forward unchanged; and use-time promotion
   admission inside the implementation this section governs.
2. **The amendment to §5.22's Finding A** — the repair it proposed is rejected,
   and Finding A stays open until use-time envelope admission is enforced.
3. **The amendment to `PENDING_AMENDMENTS` entry 11** — the repair named there
   is rejected, and the entry's drain condition is narrowed to the licensing
   defect it actually names.

*Accepting 2 and 3 is not bookkeeping. Both edit text that was already accepted,
and an acceptance silent about them would leave two amendments to accepted text
travelling into `main` on a merge, which is the failure §13l was written for.*

**What acceptance does not start.** No implementation is authorised by this
commit. Nothing may be built until this section has merged, and what is built
then is the code-only slice described below — not capture, not persistence, not
a directory, not a byte.

*§5.22's Finding A recorded a hole in `PromotionCorpusLock` and proposed a
repair in one sentence. **The repair does not work**, and the replacement is
large enough to need its own type, its own refusals, its own statement of what
an instrument identity is worth, and an enforcement point it has to carry itself
before the entry behind it may drain. That is why this is a section and not a
paragraph.*

#### The repair Finding A proposed would not work

Finding A and entry 11 both said the same thing: *the repair is a frozen
`signal_chain_hash`, or a declared envelope digest when the envelope legitimately
spans more than one chain.*

**The first alternative would make the corpus unbuildable, and the second is not
a special case.**

`gain_db` is inside the signal-chain identity, and `IQRetentionOwner.set_gain_db`
calls `_rebuild_chain_locked()` *before* `invalidate("GAIN_CHANGE")`. So the two
windows a `GAIN_STEPS` observation is made of carry **different chain hashes by
construction** — §5.21's corrected table says so in the same words. §5.21's spur
protocol then swaps the front end for a declared termination, which is two more.

> **A promotion corpus never has one chain hash.** It has a set of them, and the
> set is the thing to freeze. "When the envelope legitimately spans more than one
> chain" describes every promotion corpus this family can build.

#### One envelope is two declarations

§5.22's estimand names three scopes, and the promotion record must carry all
three: **instrument-scoped**, **envelope-scoped**, **distribution-scoped**.

A set of admissible chain hashes covers the first. It does not cover the second,
and the reason is deliberate: `signal_chain_hash` **excludes the centre
frequency**, because retuning is its own invalidation reason and folding it into
the chain would make every retune look like a different antenna. That exclusion
is correct and this section does not touch it.

But bands and tunings are precisely what define the sampled distribution, and
§5.22 pins a great deal of it: **K = 64** tunings at pseudo-random spacing from a
declared seed, retune deltas of 50/100/200 kHz in both directions, **R = 8**
repeats with persistence in ≥ 7, each LO setting visited ≥ 3 times separated by
≥ 10 others, counterbalanced order, a ≥ 10 dB persistence margin, a 0.01 slope
tolerance, a declared crystal ppm and the harmonic cap it forces, and two
accepted confidence levels. **None of that is instrument identity, and all of it
is envelope.**

So: two layers, and the lock binds both.

##### The instrument layer is not a pair of sets, and must not be a product

Declaring gains and front ends separately and admitting their **Cartesian
product** would license combinations nobody sampled. A plan that validates

```text
(ANTENNA_A,   gain = 20 dB)
(TERMINATION, gain = 40 dB)
```

would, under a product rule, also admit `(ANTENNA_A, 40 dB)` and
`(TERMINATION, 20 dB)` — two further chain hashes, passing admission, lying
outside the validated distribution. **Two declared sets do not declare their
product**, and the convenience of writing them separately is exactly where the
widening enters.

So the layer freezes an **explicit canonical set of complete chain
declarations**:

```text
ChainMember(
    sensor identity, and its authority,
    sample type,
    sample rate,
    front-end declaration,
    gain declaration,
    the resulting signal_chain_hash,
)
```

The invariant is **one declared member ↔ one chain hash** — never *all
combinations exist*. A chain is written down or it is not admitted, and the
declaration is as long as the experiment actually was.

| layer | freezes | deliberately not in it |
| --- | --- | --- |
| `InstrumentChainEnvelope` | an explicit canonical set of `ChainMember`s, each a complete chain declaration carrying its own `signal_chain_hash`. Admission is membership in that set | the centre frequency, which the chain hash excludes and must keep excluding |
| `CapturePlanDeclaration` | the materialized ordered schedule (below), the persistence margin, the slope and reference-match tolerances, the declared ppm and its harmonic cap, and the two accepted confidence levels | anything the instrument layer already fixes |

##### A seed is provenance, not a plan

A seed determines a schedule only in the presence of the algorithm that consumed
it. Hold the seed alone and a generator revised afterwards turns the same seed
into a different plan, with nothing in the lock disagreeing. So the capture-plan
layer carries all three:

1. the seed **and the schedule-generator revision**;
2. the **complete ordered visit schedule**, materialized rather than implied;
3. the **allocation of each visit** to a chain member, a stratum, a block, a
   condition and a confidence requirement.

The implementation regenerates the schedule from the seed and the revision and
requires **byte-for-byte equality** with the materialized one. A forged schedule
and a drifted generator are then the same failure and are caught by the same
check — and neither is reachable by holding the seed alone.

`PromotionCorpusLock` carries both declarations and both digests, and **both must
be present before the first window** — not before the first promotion. A plan
declared after the windows exist is a plan fitted to them, which is the failure
the lock exists to prevent, moved one layer out.

**The naming is part of the repair.** One object called `EnvelopeDeclaration`
would name the whole of §5.22's envelope while freezing the instrument half of
it, and a reader checking whether the envelope was frozen would find a field with
the right name and the wrong contents.

#### A declaration must validate itself, not trust a factory

A validating `declare_envelope()` beside a publicly constructible declaration is
a locked door beside an open window. `type(x) is InstrumentChainEnvelope` passes
for a hand-built instance with no members at all, and `freeze_promotion_corpus`
checks nothing further, so a lock can be opened under an envelope that admits
nothing.

The nominal type gate is not the defect. **The defect is that the type does not
mean what the gate assumes it means.** So validation moves into `__post_init__`,
the factory becomes a convenience over a type that is already safe, and the tests
construct the dataclass **directly** as well as through the factory. A test that
exercises only the factory tests the door.

Six canonicalization decisions go with it:

| hole | decision |
| --- | --- |
| a generator of members is consumed by validation and empty afterwards | convert the input to a tuple **once, first**, then validate the tuple. The order *is* the fix |
| NaN or infinity as a gain or a feedline length | refused — `ENVELOPE_VALUE_NOT_FINITE`. NaN is not equal to itself, so an envelope holding one cannot reliably answer whether it admits its own declared member |
| `default=str` in the digest | removed. It converts a value the schema did not anticipate into a string instead of raising, which silently widens what may be declared and puts the widening in the digest |
| a member declared twice | refused — `ENVELOPE_DECLARATION_REPEATED`. A repeat admits nothing new, so it is a declaration error, and it should be read back to the operator rather than absorbed |
| two distinct members that hash to one chain | refused — `ENVELOPE_DECLARATION_COLLAPSED`, comparing the count of **explicit canonical members** against the count of **distinct hashes**. The identity does not distinguish two things the operator wrote as different, and the operator should learn that from a refusal rather than from a count |
| reordered members | **canonically sorted before hashing.** Otherwise one declared envelope has as many digests as it has orderings, while admitting one set |

The sort needs a total order over `ChainMember`, and its gain is
`Optional[float]` where `None < 1.0` raises in Python. So it sorts on a key that
makes the absence explicit rather than on the raw value: a sort that crashed on a
legitimately undeclared gain would be a worse defect than the one it fixes.

#### The instrument identity is a string, and the string has no authority

`signal_chain_manifest` gives the antenna an `authority`, the extension an
`extension_authority`, the feedline an `authority` and the gain an `authority`.
**`sensor_id` has none.** It is the one field in the manifest taken on trust, and
it is the field §5.22's first scope limit — *the claim is about this receiver* —
rests on entirely.

So each `ChainMember` declares the authority beside the identity, from a closed
set:

| authority | what it means | collision domain |
| --- | --- | --- |
| `RECEIVER_ATTESTED_UNIQUE` | a hardware identity read from the device and unique to that device | one unit |
| `RECEIVER_OPERATOR_INSTANCE` | an operator's governed custody declaration for one physical unit they hold | that operator's custody discipline, and nothing wider |
| `RECEIVER_INSTANCE_UNIQUENESS_UNATTESTED` | a VID/PID, a model name, or a serial the vendor ships identically on every unit — the identifier does not recover the unit | every unit of that class |

##### A non-unique identifier does not produce a class-scoped claim

This is the correction that matters most, because the first draft of this section
got it backwards and the error is attractive.

> A non-unique identifier establishes only that **the physical unit cannot be
> recovered from it**. Validating one unidentified member of a device class
> validates **one unit**, not the class.

So at `RECEIVER_INSTANCE_UNIQUENESS_UNATTESTED`:

- the corpus concerns **one physical receiver** whose instance identity the
  declared identifier does not establish;
- the identifier **cannot prove continuity** across a disconnect, a host change
  or a hardware substitution — a corpus may silently span two units, and nothing
  in the declaration would notice;
- **no class-wide generalization follows**, in either direction;
- **promotion requires** `RECEIVER_ATTESTED_UNIQUE`, or a governed
  `RECEIVER_OPERATOR_INSTANCE` custody declaration. An unattested identity is a
  state a corpus may be built in and not a state it may be promoted from.

A genuine class-scoped claim would need a **multi-unit sampling plan, unit
allocation and unit-level blocking**. It cannot be reached by weakening the
identity of one unit. That direction converts ignorance about *which* receiver
was tested into evidence about *every* receiver of the type, which is the
substitution this repository refuses everywhere else, wearing a disguise.

The rule that follows:

> **The lock, the completion receipt and the promotion record carry the authority
> forward unchanged.** No downstream step may present an unattested identity as
> an attested one, or an instance claim as a class one.

*The third authority was first named `RECEIVER_CLASS_NOT_UNIQUE`. That name
describes the identifier's collision domain accurately and invites precisely the
wrong inference — which this section then made, in its own prose, one paragraph
later. The name is part of the defect.*

**One implementation constraint, checked rather than assumed.**
`RECEIVER_INSTANCE_UNIQUENESS_UNATTESTED` and `RECEIVER_ATTESTED_UNIQUE` are a
negation pair under `is_negation_pair`, on the `UN` prefix. That is permitted
**only within one declared set** — `cross_set_collisions` excludes a candidate's
own tuple, on the same ground that keeps `GRAPH_RECORD_FOUND` beside
`GRAPH_RECORD_NOT_FOUND`. So the three authorities must be declared in **one
tuple**. Split across two, the mechanical name check rejects them.

#### Admission, and the one enforcement point this amendment must carry

An envelope in the lock makes the scope **recordable**. On its own it prevents
nothing, and that is not a caveat — it decides what this amendment has to build:

> **Entry 11 cannot drain while cross-chain licensing is still reachable.**
> Entry 11 exists because a corpus validated on one chain can license promotion
> on another. Adding a field nobody enforces makes that defect *recordable*, not
> *repaired*.

So the two enforcement points separate, and only one of them may wait:

| point | what it does | when |
| --- | --- | --- |
| **use-time promotion admission** | at evaluation, the chain presented with the thing being promoted must be a member of the corpus's frozen envelope, or the promotion reports why it is not | **in §5.23's implementation.** It is the property Finding A says the repair provides, so a repair that omits it does not close Finding A |
| **captured-window admission** | after entry 9's full-object ring attestation and before a window is persisted — a window whose chain is not a member is not corpus | later, with the persistence mechanism §5.20 governs. **No corpus exists**, so nothing is exposed in the meantime |

Entry 11 therefore drains when the lock field, the receipt propagation **and
use-time enforcement** land together — not when this section merges, and not when
the field alone appears. Captured-window admission enters the queue as its own
entry at that same moment.

*The alternative considered and not taken: split entry 11 into a drained "the
lock omitted the envelope" and an open "promotion does not enforce it". Rejected
because the unsplit entry names the **licensing** defect, and a sub-entry that
drains while the licensing is still reachable would read, in the drain record, as
though it had been fixed. The drain rate is the honesty check; a drain that
records half a repair corrupts the only number that measures this queue.*

#### What this section does not do

- It does not authorise capture, persistence, a tuner operation, a directory or
  a byte, and it changes no behaviour of any module.
- It does not amend `signal_chain_hash`. The centre frequency stays out.
- **It does not claim captured-window admission.** Until entry 9 and the
  persistence slice, nothing checks a window's chain on its way to disk — there
  is no way to disk.
- It does not produce a class-scoped claim, and no weakening of an instrument
  identity ever would.
- It does not drain entry 11, and it does not resolve Finding A. A proposal
  satisfies no obligation — the reading entry 8 records.

#### What acceptance required, and what it starts

*Written before acceptance and kept as written; the decision it asks for has
now been made, and the ordering it sets is the one in force.*

**The acceptance must say expressly that it accepts the two corrections to
earlier text** — §5.22's Finding A, and `PENDING_AMENDMENTS` entry 11 — **as part
of §5.23's substance.** They travel with this section because they reject the
repair it replaces. An acceptance silent about them would leave two amendments to
accepted text carried into `main` by a merge, and §13l is explicit that a merge
supplies no acceptance.

Then, in this order:

1. **The acceptance commit, before any implementation exists.** — *done, this
   commit.*
2. **Merge**, carrying the accepted contract to `main`. — *next.*
3. **A code-only implementation built on that merge** — the two declarations,
   self-validating and canonicalized, over explicit chain members; the
   receiver-identity authority; both bound into `PromotionCorpusLock` and the
   completion receipt; **and use-time promotion admission enforced.** — *done:
   `rf_promotion_envelope.py`, and `_corpus_state` refuses a chain the frozen
   envelope does not admit, and the capture plan declares the corpus it is a
   plan for. The first attempt at this step enforced the first and not the
   second and was abandoned unmerged.*
4. **Entry 11 drains at step 3**, because at step 3 the licensing defect is no
   longer reachable. Captured-window admission enters the queue as its own entry
   at the same time. — *done: entry 14 opened, and **entry 11 drained at
   `9e0efc8`** after one withdrawn drain that the queue records.*

Finding A stays open across all four steps and closes at step 3 — **closed at
`9e0efc8`**, when both layers landed and admission was enforced. It stayed open
across the acceptance and across one abandoned implementation, which is the
whole of what this finding was for.

---

## 6. Open questions for the operator

1. **Approve the bounded IQ ring** (§2.2)? First retention of raw IQ beyond one block.
2. **256 ms default window** — accept 4.19 MB and 3.9 Hz α resolution?
3. ~~**Ship Phase 0 alone first?**~~ **Done 2026-09-01.**
4. ~~**False-DIGITAL gate at <0.1% on noise**~~ **Resolved 2026-09-02**: rate
   `0.001` as a one-sided upper confidence bound over stratified null windows,
   per-stratum. See §5.8, as corrected by §5.12 (per-bound 99.61538%, n 5,561).
   **The corpus size this implies was reopened by §5.18 and resolved as 66 732.**
5. ~~**Split the DIGITAL/ANALOGUE axis** (§5.2)?~~ **Approved and done
   2026-09-02**, before Phase 1.

All five were resolved; **§5.18 reopened the sizing consequence of Q4's
resolution and settled it at 66 732 windows**, which was a question about the
corpus rather than about the rate. Q1 and Q2 were approved 2026-09-02; see §5.5 for the
terms of that approval, which are narrower than "raw IQ retention is now
allowed". Q4's resolution (§5.8) converts Phase 3's gate from a threshold into a
validation corpus that has to be built before a detector can be promoted — whose
size §5.18 found was never settled, and settled.
