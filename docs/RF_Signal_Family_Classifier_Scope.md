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

### Phase 2 — Symbol-clock detector *(not started)*
Gated on Q4, which is now a validation manifest rather than a threshold — see
§5.8. The capture wiring is done as of Phase 1d.

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

**Approved 2026-09-02.** Maximum false-DIGITAL rate `0.001`, but the promotion
rule is the *bound*, not the observation:

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

Note that `signal_chain_hash` covers sensor, antenna, sample type and sample
rate — **not** the feedline. Two metres of RG58 is a real insertion loss and
arguably belongs in the chain identity; folding it in would change every existing
hash, so it is recorded as an open question rather than done quietly.

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

## 6. Open questions for the operator

1. **Approve the bounded IQ ring** (§2.2)? First retention of raw IQ beyond one block.
2. **256 ms default window** — accept 4.19 MB and 3.9 Hz α resolution?
3. ~~**Ship Phase 0 alone first?**~~ **Done 2026-09-01.**
4. ~~**False-DIGITAL gate at <0.1% on noise**~~ **Resolved 2026-09-02**: rate
   `0.001` as a one-sided 95% upper confidence bound over ≥10,000 stratified
   null windows, per-stratum. See §5.8.
5. ~~**Split the DIGITAL/ANALOGUE axis** (§5.2)?~~ **Approved and done
   2026-09-02**, before Phase 1.

All five are now resolved. Q1 and Q2 were approved 2026-09-02; see §5.5 for the
terms of that approval, which are narrower than "raw IQ retention is now
allowed". Q4's resolution (§5.8) converts Phase 3's gate from a threshold into a
validation corpus that has to be built before a detector can be promoted.
