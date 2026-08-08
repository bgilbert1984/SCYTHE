# GraphOps Causal-Hologram Interactions

These interactions treat the globe as a hypothesis compiler rather than a
conventional dashboard. Each answer must distinguish observed, measured,
solver-derived, inferred, synthetic, and counterfactual information.

## Signature interaction

> Show me what must be true for this red coverage gap and this network burst
> to share a cause.

GraphOps should construct competing causal worlds, identify the assumptions
required by each world, show supporting and contradicting evidence, and propose
the least expensive observation that would distinguish them.

## Explain a visible condition

- **Why is this cell red?** Trace its threshold decision, path-loss value,
  interpolation, source tile, contract lineage, transmitter geometry, and
  neighboring-field gradient.
- **Why did this region change color?** Compare current and previous field
  states and separate new evidence from threshold, camera, configuration, or
  rendering changes.
- **What is the weakest assumption behind this overlay?** Rank assumptions by
  how much the conclusion changes when each is removed.
- **Which parts of this picture are measured?** Visually isolate measured
  evidence and dim solver output, inference, and illustration.
- **What would make this conclusion wrong?** Produce explicit falsifiers and
  the observations needed to test them.
- **Show me the authoritative value beneath this display value.** Follow the
  derived visualization back to its immutable dataset asset without promoting
  the display tile to authority.

## Compare competing worlds

- **Show me the strongest benign explanation.** Construct terrain, equipment,
  clock, data-quality, and configuration explanations before escalating to an
  adversarial hypothesis.
- **Show me the adversarial explanation with the fewest assumptions.** Find the
  minimum causal chain that could produce the evidence while clearly labeling
  it as a hypothesis.
- **Where do the leading explanations disagree?** Render only cells, entities,
  edges, and predicted observations that differ among the shadow worlds.
- **What do all plausible worlds agree on?** Extract the invariant facts that
  survive every viable explanation.
- **Which world explains the most evidence with the fewest unsupported
  claims?** Rank worlds by coverage, contradictions, assumptions, and stale
  evidence rather than model confidence alone.
- **Tear apart the benign and adversarial worlds.** Display them side by side
  with synchronized cameras and a causal-difference overlay.

## Ask counterfactual questions

- **What would coverage look like if this transmitter moved here?** Run a
  sandboxed propagation scenario and keep its results in a counterfactual
  world.
- **What transmitter location best explains this field?** Infer candidate
  regions, not a fabricated point estimate, and show geometric ambiguity.
- **What changes if the antenna is directional?** Compare omnidirectional and
  candidate antenna-pattern worlds.
- **What changes if the threshold is 135 dB instead of 145 dB?** Reclassify the
  display without changing the underlying solver values.
- **Remove this sensor. Does the conclusion survive?** Recompute evidence
  support to expose dependence on a single source.
- **Shift the network events by five seconds. Does the correlation survive?**
  Test sensitivity to clock error and coincidence-window selection.
- **Assume this emitter is benign. What remains unexplained?** Subtract the
  selected cause and expose residual anomalies.

## Navigate space and time

- **Replay the first moment these worlds diverged.** Locate and animate the
  earliest evidence transition that separates their causal histories.
- **Show the field's memory scars.** Display persistent, repeated, or slowly
  decaying RF structures across prior windows.
- **What appeared immediately before this burst?** Query a bounded temporal
  neighborhood across RF, network, sensor, and operator events.
- **Follow this anomaly backward to its earliest supporting evidence.** Traverse
  provenance and temporal edges without replacing missing history with prose.
- **Where will uncertainty grow next?** Project data staleness and sensor
  coverage loss, not physical events, unless a validated predictive model is
  available.
- **Compare this hour with the same hour yesterday.** Normalize for sensor
  availability and configuration changes before reporting behavioral drift.

## Correlate RF and graph behavior

- **Which graph entities became active during this RF transition?** Run a
  time-bounded `RF_CORRELATE` query and report the null expectation for chance
  coincidence.
- **Does this emitter repeatedly precede the same network behavior?** Search
  historical sequences and distinguish recurrence from causal evidence.
- **Which network burst has the strongest temporal support from RF evidence?**
  Rank candidates using measured timestamps, uncertainty windows, and source
  independence.
- **Is this one actor changing infrastructure or several similar actors?** Use
  identity stitching and embedding similarity while displaying conflicting
  identifiers.
- **Show me synchronized activity that lacks a shared network path.** Search for
  coordination that is visible in time or RF space but not ordinary graph
  topology.
- **What RF observation would most reduce uncertainty about this cluster?**
  Recommend a frequency, location, duration, and required evidence quality.

## Interrogate evidence and provenance

- **Who or what asserted this edge?** Reveal the originating sensor, solver,
  model, operator, import, or transformation.
- **Which claims depend on this dataset?** Compute the downstream provenance
  impact before a dataset is replaced or invalidated.
- **Show contradictions, not consensus.** Surface mutually incompatible claims
  and retain both until adjudicated.
- **Which conclusion is using stale evidence?** Compare claim time semantics
  against source freshness and the requested analysis window.
- **What was inferred from absence?** Identify negative-evidence claims and
  verify that the relevant sensor was capable and active.
- **Could quantization or interpolation explain this anomaly?** Compare the
  display transform with authoritative values and neighboring raw samples.
- **What evidence has only one independent source?** Detect apparent
  corroboration that ultimately shares the same upstream origin.

## Direct investigation through gestures

- **Circle a region:** Create a geofenced `FOCUS` scope and summarize evidence
  classes before offering hypotheses.
- **Draw a path:** Trace spatial, network, and temporal relationships along the
  corridor.
- **Connect an RF cell to a graph node:** Compile an `RF_CORRELATE` plan and show
  the generated DSL before execution.
- **Pin two moments on the timeline:** Generate a `GRAPH_DELTA` comparison.
- **Drag a transmitter:** Create a counterfactual world and rerun only the
  affected propagation products.
- **Pull two world layers apart:** Reveal causal and evidentiary differences.
- **Long-press an assertion:** Open its Reality Prism: origin, authority,
  confidence, contradictions, freshness, and falsifier.

## Ask the system to design an observation

- **Where should I place one additional receiver?** Optimize for information
  gain across the leading hypotheses, subject to operator-provided constraints.
- **What is the shortest useful capture?** Estimate the minimum duration needed
  to distinguish the predicted temporal signatures.
- **Which frequency should be sampled next?** Rank frequencies by disagreement
  among worlds rather than by visual salience.
- **What evidence can be collected without changing the environment?** Prefer
  passive and read-only observations.
- **Give me the cheapest test that could falsify the leading hypothesis.**
  Return a bounded experiment with expected outcomes for every viable world.
- **What should remain unknown after this test?** State the experiment's limits
  before it is performed.

## Govern Copilot and Autopilot

- **Explain why Autopilot escalated this event.** Show tier thresholds,
  detector inputs, evidence references, cooldowns, and deduplication decisions.
- **Re-run this card without model interpretation.** Produce the deterministic
  DSL and rule-based result for comparison.
- **Have the Skeptic attack this card.** Generate benign explanations,
  counterexamples, missing evidence, and a discriminating test.
- **Show model disagreement.** Compare model outputs without converting
  agreement into evidence.
- **What did Dream Mode investigate while idle?** List counterfactual experiments
  and questions only; never silently promote them to observations.
- **Why was no alert issued?** Explain discarded, observation-only, cooldown,
  deduplicated, or insufficient-evidence outcomes.
- **What action is prohibited here?** State the authority boundary and the
  additional human decision or evidence required to cross it.

## Interaction contract

Every interaction should return, where applicable:

1. The requested result or world comparison.
2. Evidence class and authority for every material claim.
3. Supporting and contradicting evidence references.
4. Assumptions and sensitivity to those assumptions.
5. Temporal scope and source freshness.
6. A falsifier or discriminating observation.
7. The generated GraphOps DSL or solver job specification.
8. A clear boundary between analysis, simulation, and authorized action.

No interaction may promote a browser visualization, model agreement, or
counterfactual result into measured or authoritative evidence.


