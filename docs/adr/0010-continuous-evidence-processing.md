# ADR 0010 — Intelligence is produced continuously from observations; epochs stay only where a contract needs them

- **Status:** Proposed. Nothing here is decided or implemented. The vocabulary and the three
  rules are for the owner to accept, change or reject.
- **Date:** 2026-11-04
- **Builds on:** [ADR 0007](0007-mutation-boundary.md) (one boundary per mutation),
  [ADR 0008](0008-mutation-replayability.md) (what must be reconstructible),
  [ADR 0009](0009-partial-failure.md) (partial work is represented)
- **Related:** `docs/RF_SPUR_CATALOGUE_PROTOCOL.md` (§5.21), PR #431, `rf_walk_transitions.py`,
  `rf_capture_admission.py`, `rf_promotion_envelope.py`

## Context

The §5.21 catalogue run is a bounded controlled experiment: 192 visits per epoch, a final
catalogue, and distinct epochs on purpose. The protocol requires them to answer a scientific
question: does a feature survive a reconnect or a power cycle (`SESSION_SCOPED`,
`RECONNECT_STABLE`, `POWER_CYCLE_STABLE`)? That is a test, not a property every observation
needs.

The risk is the converse: a bounded-run shape leaks into the live platform, so that useful
output waits for a run to close, a receiver to restart or a fixed interval to finish.

"Epoch" already means several unrelated things in this repository (read from the code, not
exhaustively):

| Use | Where | Meaning |
|---|---|---|
| Experimental epoch | `RF_SPUR_CATALOGUE_PROTOCOL.md`, `rf_spur_catalogue_sequencer.py` | a prescribed trial boundary (reconnect, power cycle) |
| Configuration epoch | `rf_walk_transitions.py`, `rf_capture_admission.py`, `rf_iq_retention.py`, `scythe_derived_evidence.py` (`configuration_epoch`) | period over which hardware configuration and signal chain are stable; a step preserves it, a change requires a new one; the IQ ring advances it |
| Invalidation epoch | `rf_capture_admission.py` | counter attached to gain-step and retune attestations |
| Evidence epoch | `inference_exhaustion_ledger.py` | key component for inference attempts per (entity, rule) |
| Unix time | `graphops_flow_evidence.py` (`observedAtEpoch`) | a timestamp, not a boundary |

Removing "epoch" wholesale would break the second and third rows, which are working safety
contracts. Keeping it everywhere would keep the first row's cost on every consumer.

## Decision (proposed)

1. **The observation is the atomic evidence unit.** Each carries its own origin, sensor,
   configuration identity (signal-chain hash and configuration revision), event time, a
   per-source ingest sequence, a content hash and the authorization it was captured under. An
   observation is durable whether or not its session has ended.
2. **Continuous processing does not depend on acquisition closing.** Analysis runs over
   bounded, possibly overlapping windows of the journal and emits findings as evidence
   arrives. A finding is `PROVISIONAL`, then `CORROBORATED` once its declared evidence is
   met, and is *verified within a named scope* only when a specified test and provenance are
   satisfied. Later evidence produces a revision or a superseding finding; it never edits
   the earlier one. A snapshot freezes a journal offset, processing version and
   configuration chain without stopping acquisition.
3. **Epochs remain mandatory only where a specific scientific or safety contract requires
   them.** Experimental epochs, configuration epochs and lifecycle tests keep their meaning.
   Time passing never substitutes for a lifecycle test: months of uninterrupted observation
   is not `POWER_CYCLE_STABLE`.

### Vocabulary: four concepts, four names

| Concept | Keep? |
|---|---|
| Acquisition session: receiver activity from start to stop | yes |
| Configuration epoch: stable hardware/signal-chain period | yes, unchanged |
| Experimental epoch: a prescribed trial boundary | yes, for bounded experiments |
| Processing epoch: accumulation must finish before downstream use | **not a universal requirement** |

New code should say which of these it means. This ADR does not rename existing fields.

### Two evidence dimensions, kept separate

| Dimension | Establishes |
|---|---|
| Operational persistence | how consistently a feature appears in eligible observations over time |
| Lifecycle stability | whether it survives reconnect, power cycle or another prescribed intervention |

A feature can have high operational persistence and remain `SESSION_SCOPED`. Operational
findings are a **new** finding type; they do not change the existing catalogue classification
contract or its stability classes.

### Constraints the design must respect

- Overlapping windows are not independent confirmation; corroboration counts independent
  evidence, not evaluations.
- A sensor gap is recorded as missing coverage, never as proof a signal disappeared.
- Late events use event-time watermarks and explicit revisions.
- Event time and ingest order are different things. A per-source sequence and a clock-domain
  identifier support replay; neither proves global ordering across independent sensors.
- §5.21's final slope classification needs several distinct retune observations. A live
  processor may report preliminary features earlier but may not assign the final scientific
  classification before that evidence exists.
- Changes reach the graph through the existing governed mutation path (ADR 0007). Continuous
  processing does not bypass proposal, approval or execution checks, and does not widen any
  RF-control authorization.

## What this does not change

- The §5.21 protocol, the frozen authorizations and the RERUN2 evidence.
- RERUN2's label. If the owner accepts the proposed reading, `521-UHF-XTR-EPOCH1-RERUN2`
  keeps `EPOCH1` in its identifier (history is not renamed) and is recorded as a bounded
  catalogue, `SESSION_SCOPED`, cross-lifecycle verification not established. That is a
  separate decision, not made here.
- Stage 1 hardware, receiver calibration, or the unconfirmed ppm accuracy figure.

## Proposed first test (replay only)

Feed a disposable replay of RERUN2's existing visits into an incremental processor, emit
provisional results as they arrive, and compare the final result to the frozen batch
catalogue. No acquisition, hardware or new authorization is involved.

| Requirement | Target |
|---|---|
| Final scientific result | exact parity with batch processing for equivalent inputs |
| Intermediate results | available before replay completes |
| Restart recovery | no missing or duplicated committed observations |
| Replay | identical outputs from pinned inputs and versions |
| Configuration change | invalidates incompatible state |
| Governance | no bypass of proposal, approval or execution checks |
| Latency | measure p50/p95/p99 first; set thresholds after the baseline |

Phasing, if accepted: (A) an append-only observation journal with idempotent ingest, explicit
gaps and configuration identity; (B) incremental feature extraction by sensor, signal chain
and feature; (C) graph reconciliation of findings and revisions through the governed path;
(D) on-demand hash-linked checkpoints.

## Open questions

- Which existing mission/session/observation-set types carry the observation fields, and
  which are new? (Not surveyed here.)
- How is "independent" defined for corroboration across overlapping windows and across
  sensors?
- Where does the journal live relative to the WriteBus, and what is its retention?
- Does an operational finding need its own classification vocabulary, or only a state?

## Consequences

- Live intelligence no longer waits for a run boundary, and lifecycle claims stay as strong
  as the tests behind them.
- Cost: one more finding type with revision semantics, and a discipline of naming which
  "epoch" a piece of code means.
- Not demonstrated: no incremental processor exists, and the replay parity target is unmeasured.
