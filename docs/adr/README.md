# Architecture Decision Records

Each ADR records one decision, its status, and what follows from it. A `Proposed`
ADR describes a decision that has been *reasoned to*, not one that has been
implemented — the repository states elsewhere what actually ships.

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-sparse-recovery-validation.md) | Sparse recovery is validated before it is believed | Proposed; §5 pattern Accepted, one application implemented |
| [0002](0002-polarimetric-channel-diversity.md) | Channel diversity is physical, never synthetic | Proposed |
| [0003](0003-rf-emission-tracking-hierarchy.md) | Detection, track, transmitter and location are four layers | Proposed |
| [0004](0004-mcp-control-plane.md) | MCP tools declare their effect, and policy follows the declaration | Accepted and implemented; Registry/ToolDef split undecided |
| [0007](0007-mutation-boundary.md) | Every authoritative mutation has exactly one documented boundary | Proposed; not decided |
| [0009](0009-partial-failure.md) | A partially applied mutation is represented, never silently discarded | Proposed; not decided |
| [0005](0005-registry-tooldef-execution-model.md) | Registry and ToolDef tools: one lifecycle, or an explicit boundary | Proposed; not decided |
| [0006](0006-mcp-approval-policy.md) | Approval is a policy tier per effect, and independent approval is expressible | Proposed; not decided |

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

Nothing else in ADRs 0001–0003 exists in code. ADR 0004 is the exception: decisions 1–5
are implemented and tested, and `docs/MCP_Tools.md` describes the current behavior.
