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
| [0005](0005-registry-tooldef-execution-model.md) | Registry and ToolDef tools: one lifecycle, or an explicit boundary | Accepted (architecture); mutation invariant stated; MCP compliant, HTTP exceptions listed |
| [0006](0006-mcp-approval-policy.md) | Approval is a policy tier per effect, and independent approval is expressible | Proposed; mechanism mature, default mapping pending #228 |
| [0007](0007-mutation-boundary.md) | Every authoritative mutation has exactly one documented boundary | Proposed; not decided |
| [0008](0008-mutation-replayability.md) | Every mutation owes a declared level of explainability | Proposed; not decided |
| [0009](0009-partial-failure.md) | A partially applied mutation is represented, never silently discarded | Proposed; not decided |

## Where to look

The MCP control-plane ADRs each own one concern. Status is in the table above and can
differ per ADR; a proposal records a direction, not a settled rule.

| Concern | ADR |
|---|---|
| Effects: what a tool may do, and how it is audited | [0004](0004-mcp-control-plane.md) |
| Execution model: how every tool runs | [0005](0005-registry-tooldef-execution-model.md) |
| Approval: who must approve before it runs | [0006](0006-mcp-approval-policy.md) |
| Mutation boundary: where state may change | [0007](0007-mutation-boundary.md) |
| Replayability: what must be reconstructible | [0008](0008-mutation-replayability.md) |
| Failure semantics: how partial work is represented | [0009](0009-partial-failure.md) |

Questions that come up when working on a tool, and where the answer comes from:

| Question | Source of truth |
|---|---|
| Can this tool mutate? | its declared effect, checked by `validate_tool_contract` (0004) |
| How is it executed? | 0005 |
| Who must approve it? | the minimum mode for its effect, 0006 |
| Where may it change state? | its classification under 0007 |
| What must be replayable? | its declared level, 0008 |
| How are failures represented? | 0009 |
| Where is the state it touches kept? | [`docs/State_Inventory.md`](../State_Inventory.md) |
| What does the system do today? | the code and tests, then [`docs/MCP_Tools.md`](../MCP_Tools.md) |

An ADR in the table above states a decision or a proposal. For current behavior, the
code governs.

## How ADRs 0004–0009 relate

The MCP control-plane ADRs build on one another. Arrows read "builds on" and follow each
ADR's own *Builds on* line.

```text
0004  Control-plane invariants (effects, audit, principal)
  ├── 0005  Execution model (Registry vs ToolDef)
  │     └── 0006  Approval semantics (modes per effect)
  └── 0007  Mutation boundary (builds on 0004, 0005, 0006)
        ├── 0008  Replayability (also builds on 0006)
        └── 0009  Partial-failure semantics (also builds on 0008)
```

0004 is accepted and implemented. 0005 is accepted as architecture; every MCP tool, of
either class, now runs through `Registry.execute` (#249, #250, and the invariant test
`test_mcp_lifecycle_invariant`), and the invariant that every externally initiated mutation goes through that
lifecycle is stated, with the HTTP routes that do not yet comply listed as exceptions. 0006–0009 are proposals: each frames a decision and none is chosen. Policy that the ADRs deliberately leave open (the `HARDWARE_CONTROL`
approval default, the boundary for a given tool) is tracked in #228, #236 and #218.

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
