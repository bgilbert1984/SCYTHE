# Architecture Decision Records

Each ADR records one decision, its status, and what follows from it. A `Proposed`
ADR describes a decision that has been *reasoned to*, not one that has been
implemented — the repository states elsewhere what actually ships.

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-sparse-recovery-validation.md) | Sparse recovery is validated before it is believed | Proposed, §5 Accepted |
| [0002](0002-polarimetric-channel-diversity.md) | Channel diversity is physical, never synthetic | Proposed |
| [0003](0003-rf-emission-tracking-hierarchy.md) | Detection, track, transmitter and location are four layers | Proposed |

## Relationship to `docs/SparseSCYTHE.md`

These three ADRs are condensed **from** `docs/SparseSCYTHE.md`, which braids three
separate decisions — RF sparse estimation, polarimetric imaging, and RF emission
tracking — into one conversation-derived document. A single ADR would have
flattened them, so the discussion was split along its own seams.

The source document is **retained unchanged and is not superseded.** Compression
loses nuance, and where an ADR and the source disagree in detail the source is the
record of what was actually reasoned. Read the ADR for the decision; read
`SparseSCYTHE.md` for the argument, the worked examples and the material that did
not survive the summary.

## What is implemented

Only ADR 0001 §5 — the `{outcome, reason_code}` shape — and it is implemented in
`rf_signal_family.py` for the **signal-family** contract, not yet in the sparse
estimator's own `NULL_OUTCOMES`. See
`docs/RF_Signal_Family_Classifier_Scope.md` for what Phase 0 actually shipped.

Nothing else in these ADRs exists in code.
