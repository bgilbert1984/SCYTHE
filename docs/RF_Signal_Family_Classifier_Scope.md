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
  "availability": "SOURCE_UNREACHABLE",
  "unreachable_cause": "NOT_DETERMINABLE_FROM_THIS_PROCESS"
}
```

`WAITING_FOR_USB` would have been the useful-sounding string, and it is the one
the operator most often wants. It is also a guess. The restart policy that
governs recovery is a property of a systemd unit the bridge never read, so it
is documented in `docs/RTL_TCP_BOOT_CAPTURE.md` rather than asserted by a
process with no access to it.

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
