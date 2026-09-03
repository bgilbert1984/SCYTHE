# Architecture Decision Records

Each ADR records one decision, its status, and what follows from it. A `Proposed`
ADR describes a decision that has been *reasoned to*, not one that has been
implemented — the repository states elsewhere what actually ships.

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-sparse-recovery-validation.md) | Sparse recovery is validated before it is believed | Proposed; §5 pattern Accepted, one application implemented |
| [0002](0002-polarimetric-channel-diversity.md) | Channel diversity is physical, never synthetic | Proposed |
| [0003](0003-rf-emission-tracking-hierarchy.md) | Detection, track, transmitter and location are four layers | Proposed |

## Relationship to `docs/SparseSCYTHE.md`

These three ADRs are condensed **from** `docs/SparseSCYTHE.md`, which braids three
separate decisions — RF sparse estimation, polarimetric imaging, and RF emission
tracking — into one conversation-derived document. A single ADR would have
flattened them, so the discussion was split along its own seams.

The source document is **retained unchanged**, but it is not the decision surface.
Three layers, and they are not interchangeable:

```text
ADRs             = normative decisions        (what SCYTHE has decided to do)
SparseSCYTHE.md  = non-normative design history (why, and what was considered)
Code + tests     = implemented reality        (what actually runs today)
```

Where an ADR and `SparseSCYTHE.md` disagree, **the ADR governs.** The source
document records what was reasoned at the time, including branches that were
argued and then not taken; treating it as authoritative would give implementers
two competing specifications and no way to tell which one is current. Read the ADR
for the decision. Read `SparseSCYTHE.md` for the argument, the worked examples and
the material that did not survive compression. Read the code for what exists.

A decision that changes belongs in the ADR — amended in place or superseded by a
new one. It does not belong in an edit to the history.

## What is implemented

One thing: the `{outcome, reason_code}` shape of ADR 0001 §5, and only in
`rf_signal_family.py` for the **signal-family** contract. The sparse estimator's
own `NULL_OUTCOMES` has not migrated. See
`docs/RF_Signal_Family_Classifier_Scope.md` for what Phase 0 actually shipped.

Nothing else in these ADRs exists in code.
