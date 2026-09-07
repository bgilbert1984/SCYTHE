# RF Walking-Survey Contract

```text
Status:                 ACCEPTED
Accepted:               2026-09-07, after review correction 1df55c5
Authority:              NORMATIVE
Implemented foundation: rf_receiver_state.py
Survey ingestion:       NOT_IMPLEMENTED
Surface update:         NOT_IMPLEMENTED
Emitter localization:   NOT_IMPLEMENTED
```

This document is the promise a walking-survey implementation must satisfy. It is
written before an implementation exists, deliberately, so that it constrains one
rather than describing one.

`spectrum_survey.py` exists in the working tree and is **refused**: see §10. It
was written against a design conversation rather than against a contract, and it
implements the wrong authority topology — an unconditional ingest-and-mutate
pipeline. That is not a defect list; it is a different shape.

`docs/SCYTHE_Sensor_Fusion.md` is local provenance, not a specification. It is
deliberately not committed: contradictory examples sitting beside a normative
contract are read as current guidance by repository search and by model context,
and several of its examples contradict this document directly.

---

## 1. Authorities and terminology

Seven terms, kept apart because collapsing any two of them is how a survey turns
into a claim nobody made.

**Receiver state** — where the receiver was, how fast it was moving, which way it
was pointed, and how well any of that is known. Owned by `rf_receiver_state.py`.
Its identity is the **receiver-state chain hash** over the *apparatus* of
positioning: device, position authority, course source, heading source,
alignment method, mount orientation and mount uncertainty. It excludes the
position itself, exactly as the signal chain excludes the centre frequency — an
identity that changed with every fix would make every state its own incomparable
island.

**Signal chain** — what the RF passed through on its way to a number. Identity is
the **signal-chain hash**, `scythe.rf-signal-chain.v2` at revision `v3`, over
sensor, sample type, sample rate, antenna, feedline, mast extension and gain.
`PRIOR_SIGNAL_CHAIN_REVISION_COMPARABLE` is `false`.

These two identities are **separate and are never merged.** A phone that changes
its heading source has not changed its antenna. A mast that extends has not
changed its GNSS receiver. A single fused hash would make each of those look
like the other, and would make a comparability refusal unable to say which
apparatus actually changed.

**Survey frame** — one bounded, geo-referenced power product from one observer,
carrying its own timing bounds, both chain identities, and its power unit. Never
raw IQ. See §2.

**Breadcrumb** — a record of where the operator walked. It is a record of the
*survey*, not an inference about an emitter, and it is therefore never gated on
time alignment. A track drawn from GNSS is true whether or not the RF joined to
it.

**Surface contribution** — a frame's admission into the H3 power surface. Gated.
See §5.

**Posterior** — the surface itself: received power conditioned on the receiver's
path and apparatus. It answers *what did this receiver, on this path, with this
antenna, hear here*. It does not answer where anything transmitted from.

**Emitter claim** — an assertion that a transmitter is at a location. Nothing in
this contract produces one. See §8.

---

## 2. Frame identity and timing

Every survey frame carries, and is refused without:

| Field | Meaning |
|---|---|
| `frame_id` | Stable identity for idempotent replay |
| `observer_id` | Which observer produced it |
| `acquisition_start_monotonic_ns` | Monotonic bound, opening |
| `acquisition_end_monotonic_ns` | Monotonic bound, closing |
| `monotonic_source_id` | Which clock those bounds are on |
| `alignment_status` | One of `ALIGNMENT_STATES` (§5) |
| `alignment_uncertainty_ms` | Declared, or the frame is `UNVERIFIED` |
| `alignment_method` | From `ALIGNMENT_METHODS` |
| `receiver_state_chain_hash` | §1 |
| `signal_chain_hash` | §1 |
| `configuration_epoch` | The capture epoch the samples belong to |
| `power_unit` | §3 |

**Monotonic time is the join axis. UTC is display metadata and nothing else.**

This is not a preference. On 2026-09-06 this host took a wall-clock step of
roughly 23½ hours, which retroactively re-rendered every `dmesg --ctime` line in
the kernel buffer — the same event printed as `Sat Sep 5 14:05:02` and later as
`Sun Sep 6 13:40:33`. Any join computed against wall clock across that step
would have been silently wrong, and would have looked fine. A frame whose
timestamps are UTC-only is `TIME_ALIGNMENT_UNVERIFIED` by construction.

Two monotonic bounds, not one instant. A sweep takes time; at walking pace an
observer crosses cell boundaries during one. A single timestamp asserts an
instantaneity the acquisition did not have.

`monotonic_source_id` exists because two monotonic clocks are not comparable
merely by both being monotonic. A frame whose bounds come from a different clock
than the receiver state's is not aligned, however precise each clock is.

**`configuration_epoch`** is carried because the IQ ring advances an epoch on
every invalidation, and a product must be attributable to the epoch that
produced it. An invalidation that cannot be joined to the products it
invalidated is an invalidation that did not protect anything.

---

## 3. Power units

**`DBFS` is the default and requires nothing.** It is what an RTL-SDR produces:
a number relative to the converter's full scale, meaningful only against another
number from the same chain.

**`DBM` is admissible only with a named calibration identity**, and the frame
must carry all of:

```text
calibration_id            a name, not a number
calibration_revision      calibrations are superseded, not amended
frequency_range_hz        the span it was established over
gain_state                the tuner gain it was established at
antenna_id                the antenna it was established with
feedline_id               the feedline it was established with
extension_mm              the mast geometry it was established at
uncertainty_db            what it does not know
```

A frame claiming `DBM` outside the calibration's frequency range, gain state or
antenna/feedline configuration is `POWER_UNIT_UNSUPPORTED`. The calibration is
not extrapolated; a calibration that is stretched is not a calibration.

**A generic per-observer offset is prohibited.** Adding a scalar to a dBFS
number does not produce dBm — it produces a dBFS number that has been moved, and
naming the result `dbm` is the whole error. The prohibition is specific because
the pattern is attractive: a `spectrum_offset(observer_id, band_id, gain_db,
offset_db)` table bootstrapped by dwelling two dongles on a broadcast carrier
produces exactly this, and it produces it in a form that looks calibrated.

Cross-observer merging in `DBFS` is permitted **only within one signal-chain
hash.** Two dongles are two instruments; merging their dBFS numbers into one
cell manufactures a boundary artefact and calls it a hotspot.

---

## 4. Admission verdicts

Every frame receives **exactly one disposition** and **zero or more reason
codes**. Two levels, not one.

```text
disposition — exactly one:
  SURFACE_ELIGIBLE
  BREADCRUMB_ONLY
  FRAME_REFUSED

reason_codes — zero or more, closed vocabulary:
  TIME_ALIGNMENT_UNVERIFIED
  RECEIVER_STATE_STALE
  SIGNAL_CHAIN_UNBOUND
  RECEIVER_STATE_UNBOUND
  PRODUCT_LINEAGE_UNBOUND
  POWER_UNIT_UNSUPPORTED
  RAW_IQ_PRESENT
```

One level cannot carry both facts. An earlier draft of this section required
exactly one outcome per frame while §10 required a frame failing two lineage
groups to report both — a contradiction no test could resolve, and one an
implementation could satisfy in either direction while claiming compliance:
drop the second diagnostic, or emit two outcomes. Both would be wrong, and
neither would look wrong.

The two levels answer different questions. **The disposition says what happens
to the frame**, and a frame has one fate. **The reason codes say why**, and
there may be several reasons — a frame can be missing its signal chain *and* its
sweep-plan revision, and an operator repairing only the first has not repaired
the frame.

### Rules

| Disposition | Breadcrumb | Surface | Reason codes |
|---|---|---|---|
| `SURFACE_ELIGIBLE` | yes | yes | **none** — a passing frame has no refusal reason |
| `BREADCRUMB_ONLY` | yes | no | one or more; **every** applicable reason, not the first found |
| `FRAME_REFUSED` | **no** | no | `RAW_IQ_PRESENT`, exclusively |

`SURFACE_ELIGIBLE` carries no reason code. A verdict that passed and still names
a refusal reason is two verdicts in a coat.

`BREADCRUMB_ONLY` carries every applicable reason. Reporting only the first
failed gate would make repair iterative for no reason: the operator fixes the
signal chain, resubmits, and discovers the power unit was also wrong.

`FRAME_REFUSED` is reached only by `RAW_IQ_PRESENT`, and carries no other reason
code. A frame containing samples is not evaluated further — there is nothing to
learn from gating a frame that will not be retained, and gating it anyway would
mean parsing more of a payload that already violated the boundary.

### Reason codes

| Reason | Meaning |
|---|---|
| `TIME_ALIGNMENT_UNVERIFIED` | Nothing joined the observation to a receiver state |
| `RECEIVER_STATE_STALE` | The state is too old for the observation at this speed |
| `SIGNAL_CHAIN_UNBOUND` | The signal-chain hash or its required explanatory fields are missing |
| `RECEIVER_STATE_UNBOUND` | The receiver-state chain hash or receiver-state identity is missing |
| `PRODUCT_LINEAGE_UNBOUND` | The sweep-plan or processing revision is missing |
| `POWER_UNIT_UNSUPPORTED` | `DBM` without a qualifying calibration, or an unknown unit |
| `RAW_IQ_PRESENT` | The frame carried samples |

**Three unbound reasons, not one.** §1 insists that receiver-state identity and
signal-chain identity are separate and never merged; collapsing every missing §6
field into a single reason would undo that insistence at the moment it matters
most, leaving an operator told only that *something* about the frame's lineage
was absent. The three answer different questions and are repaired by different
people:

| Reason | What is missing | Who repairs it |
|---|---|---|
| `SIGNAL_CHAIN_UNBOUND` | signal-chain hash, or antenna / feedline / extension / gain / sample rate | the RF apparatus declaration |
| `RECEIVER_STATE_UNBOUND` | receiver-state chain hash, or the positioning identity behind it | the positioning apparatus declaration |
| `PRODUCT_LINEAGE_UNBOUND` | `sweep_plan_revision` or `processing_revision` | the survey configuration |

A frame missing fields from more than one group carries a reason code for each,
so a single repair cannot appear to be the whole remedy. No group's absence may
produce another group's reason code.

### The raw-IQ refusal is scoped to the frame

No breadcrumb is derived from the rejected survey frame. A receiver-state
observation received independently through its own valid ingestion path is
**neither deleted nor invalidated** by this refusal.

Atomic rejection means the frame is rejected whole — not that a malformed RF
frame can reach backwards into positioning evidence it did not produce. A frame
that violated the sample boundary is not partially retained, and retaining its
pose would reward the violation with a record; but a pose that arrived correctly
by another route was never part of that frame.

Every disposition other than `FRAME_REFUSED` keeps the breadcrumb, because the
operator's track is independently sourced from the pose and does not depend on
the RF being usable. **A refusal is a result, not a missing value**, and both
levels of the verdict are recorded as one.

---

## 5. Receiver-state gate

This contract **imports** the alignment semantics already implemented in
`rf_receiver_state.py`. It does not restate them. A second table would be a
competing authority, and the two would drift.

Normative source:

```python
ALIGNMENT_STATES        = ("VERIFIED", "BOUNDED", "UNVERIFIED", "STALE")
ALIGNMENT_CAPABILITIES  # breadcrumbs / heatmap_update / bearing_like_evidence
JOIN_REFUSALS           # NO_RECEIVER_STATE, NO_POSITION, ALIGNMENT_NOT_ATTEMPTED,
                        # ALIGNMENT_UNBOUNDED, SIGNAL_CHAIN_CHANGED,
                        # RECEIVER_STATE_CHAIN_CHANGED
time_align(observation, state) -> TimeAlignedJoin
may_update_posterior(join)     -> bool
```

**A survey frame enters the surface only through `time_align`, and only when
`may_update_posterior` returns true.** No other path exists. An implementation
that computes its own eligibility has replaced this contract with itself.

`VERIFIED` and `BOUNDED` may update the surface; `UNVERIFIED` and `STALE` may
not. All four permit breadcrumbs. `BOUNDED` propagates its offset's contribution
to pose uncertainty rather than discarding it, and its bearing-like evidence is
`CONDITIONAL` — time alignment does not supply a verified heading source, and a
contract that let it would be laundering one unknown into another.

Staleness is measured in **metres of possible movement**, not seconds:
`STALE_MOTION_RATIO` against the GNSS circle, floored by
`MIN_POSITION_ACCURACY_M`. A stationary observer's state ages slowly; a walking
one's does not.

`DEFAULT_MOUNT_UNCERTAINTY_M` remains in the budget. The antenna is on a two
metre magnetic base and the operator's relationship to it is `UNDECLARED`; that
is a declared unknown carried forward, not a measured offset, and it is in the
budget so that it cannot be forgotten.

---

## 6. Product comparability

Two survey products are comparable only if **all** of these agree. Any
difference makes them separate products, not two samples of one:

```text
signal_chain_hash            what the RF passed through
receiver_state_chain_hash    the apparatus of positioning
antenna_id
feedline_id
extension_mm
gain_db
sample_rate_hz
power_unit                   plus calibration_id + revision when DBM
sweep_plan_revision          which bands, bin widths and dwell
processing_revision          which FFT, window, decimation and quantization
```

The first two are hashes and the rest are their inputs or their peers; both
travel, because a hash that cannot be explained is a hash nobody can act on.

`sweep_plan_revision` and `processing_revision` are here because a survey is not
only its apparatus. Changing a bin width or a window function changes what a
power number means as surely as changing an antenna does, and neither appears in
either chain hash today.

**`extension_mm` is load-bearing and was the subject of a merged fix.** A
telescopic mast retracted from 730 mm to 165 mm keeps its `antenna_id` while its
derived quarter wave moves from the FM broadcast span to the 433 MHz ISM band.
That is not one antenna with a setting; it is a different frequency response,
and every relative-power product either side of the change was taken through a
different instrument.

Comparability failures are routed by which identity is absent, per §4: the
reason codes `SIGNAL_CHAIN_UNBOUND`, `RECEIVER_STATE_UNBOUND` or
`PRODUCT_LINEAGE_UNBOUND` at admission, under a `BREADCRUMB_ONLY` disposition;
`SIGNAL_CHAIN_CHANGED` or `RECEIVER_STATE_CHAIN_CHANGED` at join. A frame
missing fields from more than one group carries a reason code for each, so a
single repair cannot appear to be the whole remedy.

---

## 7. Mutation boundary

**Frame acceptance does not authorize anything to be written.** These are four
separate authorities, and a frame arriving satisfies at most the first.

| Authority | Granted by |
|---|---|
| Breadcrumb retention | An admitted frame, per §4 |
| Surface update | `may_update_posterior` returning true, per §5 |
| GraphOps evidence | An explicit promotion decision, never frame arrival |
| Emitter claim | Nothing in this contract. See §8 |

A pipeline whose ingest path ends in `bus.commit(...)` has collapsed all four
into the first. That is the specific topology this contract exists to prevent,
and it is why `spectrum_survey.py` is refused rather than patched: teaching an
unconditional ingest-and-mutate pipeline to refuse what it was built to accept is
more expensive and less safe than building one that starts by refusing.

Surface storage is not graph mutation. The H3 store is a bounded survey product
and may be written by an admitted, eligible frame. Promoting anything from it
into GraphOps is a separate decision with a separate gate, made by something
that has read the surface — not by the ingest path that fed it.

### Channel lineage and detector eligibility

`MEASUREMENT_CHANNEL` products **may** supply survey power measurements. They
are **not** eligible for information-structure detector verdicts. Only
`STRUCTURE_CHANNEL` is eligible for that detector path. Surface eligibility does
not constitute GraphOps or detector promotion.

The distinction matters in both directions, and stating only the prohibition
gets it wrong. A walking survey's power products ordinarily *come from* the
measurement lineage — that is what a measurement channel is for. An implementer
told only that measurement verdicts are prohibited could reasonably route survey
power through `STRUCTURE_CHANNEL` to stay compliant, and would thereby
contaminate a detector-oriented lineage with measurement traffic and subject
survey power to detector-oriented filtering. Both channels would then be wrong
about what they contain.

What is prohibited is narrower and specific: using the measurement lineage to
produce a digital-structure detector verdict. A power number is a measurement;
a claim about information structure is a detector output; the channels exist so
that a reader can tell which they are holding.

---

## 8. Localization boundary

**An H3 power surface is a receiver-path-conditioned power posterior.** Each
cell holds what *this receiver*, on *this path*, with *this antenna at this
extension*, observed while it was there. It is conditioned on coverage: a bright
cell may be a strong signal, or the only cell the operator stood in for ninety
seconds. It is conditioned on antenna pattern, on mast geometry, on body
shadowing, and on the mount uncertainty in §5.

It is **not** an emitter location, and no rendering may imply that it is.
Specifically prohibited:

- labelling a cell, hotspot or projected entity as an emitter or transmitter;
- exporting a cell as a point feature — including CoT — that a consumer will
  read as a located emitter;
- colouring or elevating a surface in a way that reads as a source rather than
  as received power;
- filling unobserved cells from a model without rendering them as visibly
  distinct from observed ones, with their uncertainty shown.

**No point estimate is produced until three gates pass**, and none of them is
implemented:

1. **Geometry.** Observations must subtend adequate angular diversity about the
   candidate. A track down one street constrains a bearing, not a position, and
   a point estimate from collinear observations is an artefact of the path.
2. **Repeatability.** The candidate must survive independent passes. One pass
   cannot separate a real emitter from a transient or from a body-shadow lobe.
3. **Multipath and refusal.** The estimator must be able to return *no
   estimate*, and that refusal must be a first-class outcome rather than a
   fallback to the strongest cell.

Until all three exist and pass, the only honest products are the surface and its
uncertainty. A detected carrier is rendered as **an observation anchored to the
receiver**, never as a geographic emitter.

A stitched wideband survey must not be presented as a simultaneous capture.

---

## 9. Raw-IQ boundary

The standing grant is unchanged and is repeated here because this is where it
would be eroded:

```text
PROCESS-LOCAL · VOLATILE · FIXED-CAPACITY / NON-PERSISTENT · NON-TRANSPORTABLE
NON-MODEL-CONTEXT / BRIDGE-OWNED · INVALIDATED ON SIGNAL-CHAIN CHANGE
```

That is permission for a DSP working buffer. It is not permission for an IQ
archive. No disk fallback, swap-oriented buffering or crash-dump facility is
added under it.

For walking survey specifically, all of the following are prohibited:

- **a transported snippet.** No IQ in a survey frame, a grounding burst, a
  batch, or an attachment, at any size or duty cycle;
- **a reference masquerading as authorization.** An `evidence_ref` naming an IQ
  buffer is a name, not a grant. Nothing may resolve it into transported
  samples, and its presence must never be read as permission for the retrieval
  it describes;
- **persistence.** No IQ on disk, in the H3 store, in the graph, or in any
  survey product;
- **model context.** No IQ reaches GraphOps model context or Ollama Cloud;
- **a grounding-lane exception.** There is none. "On demand" is the request
  path, not an exemption from the boundary.

A frame carrying samples is `FRAME_REFUSED` with the single reason code
`RAW_IQ_PRESENT`, in whole (§4). Receiver-state evidence ingested independently
is untouched by that refusal.

---

## 10. Implementation gate

`spectrum_survey.py` is **`REFUSED_AS_IMPLEMENTATION_BEFORE_CONTRACT`**. It
remains untracked. It is not patched incrementally, and its WriteBus path is not
borrowed.

What it got right is recorded so a rewrite does not lose it: per-hop pose
interpolation to the hop midpoint; H3 resolution matched to fix accuracy with
the resolution stored; `DERIVED_INFERENCE` on anomaly edges with an explicit
"not an emitter-identity claim"; allow-listed bounded fields with unknown-field
rejection; residual-against-baseline rather than absolute power as the anomaly
score; edge identity keyed so repeated observations reinforce one edge.

What makes it the wrong shape, rather than a list of bugs: it has no monotonic
time axis, no receiver-state gate, no signal chain on the frame, no separation
between breadcrumb and surface, and its ingest path terminates in a WriteBus
commit for every accepted frame. Four of this contract's authorities are one
authority in it.

A replacement is admissible only when tests demonstrate **each** of:

- **exactly one disposition on every frame**, with `SURFACE_ELIGIBLE` carrying
  no reason code and `FRAME_REFUSED` carrying only `RAW_IQ_PRESENT`;
- every reason code in §4 reachable, and a `BREADCRUMB_ONLY` frame carrying
  **every** applicable reason rather than the first one found;
- `FRAME_REFUSED` discarding the whole frame, and that refusal leaving an
  independently ingested receiver-state observation neither deleted nor
  invalidated;
- a `UNVERIFIED` and a `STALE` join producing breadcrumbs and **no** surface
  contribution;
- a `BOUNDED` join propagating its uncertainty into the recorded pose
  uncertainty rather than discarding it;
- each of the three unbound reasons reached by its own missing field group:
  `SIGNAL_CHAIN_UNBOUND` for a missing chain hash or apparatus field,
  `RECEIVER_STATE_UNBOUND` for a missing receiver-state identity, and
  `PRODUCT_LINEAGE_UNBOUND` for a missing `sweep_plan_revision` or
  `processing_revision` — with a frame failing two groups carrying both codes
  under one disposition, and no field group able to produce another group's code;
- a `MEASUREMENT_CHANNEL` product supplying a survey power measurement and being
  refused an information-structure detector verdict, and survey power **not**
  routed through `STRUCTURE_CHANNEL` to obtain one;
- `DBM` refused without a qualifying calibration, and refused outside the
  calibration's frequency range, gain state and antenna configuration;
- a generic per-observer offset having no path into a `DBM` product;
- ingest completing with **zero** WriteBus mutations;
- no point estimate emitted with any of the §8 gates unimplemented;
- an IQ-bearing frame refused whole, and an `evidence_ref` never resolving to
  transported samples.

Until then: survey ingestion `NOT_IMPLEMENTED`, surface update
`NOT_IMPLEMENTED`, emitter localization `NOT_IMPLEMENTED` — as the header says.
