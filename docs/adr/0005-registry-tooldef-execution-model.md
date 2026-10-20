# ADR 0005 — Registry and ToolDef tools: one lifecycle, or an explicit boundary

- **Status:** Accepted (architecture), 2026-10-20: the principle and Option B as the
  long-term direction. **Not implemented.** No `ToolDef` tool has been adapted, the
  interim step C is not decided, and the HTTP routes are not decided.
- **Date:** 2026-10-20
- **Resolves:** decision 6 of [ADR 0004](0004-mcp-control-plane.md), tracked in #236.
- **Related:** #218 (mutation boundary), #220 (capability), #227 (`/api/sdr/*` parity),
  #228 (approval), #235 (`ToolDef` tools under the validator), `docs/MCP_Tools.md`

## Context

Two classes of MCP tool exist and they are not equivalent.

| | Registry tool | `ToolDef` tool |
|---|---|---|
| Defined by | `mcp_registry.Tool` | `mcp_server.ToolDef` |
| Held in | `MCPHandler._registry` and the orchestrator's registry | `MCPHandler._tools` only |
| Members | the control-plane tools, including the RF tools | the 12 `graphops_*` tools |
| Execution | `Registry.execute`: validation, rate limit, mode check, `[MCP_AUDIT]`, control audit | `ToolDef.fn(params)` |
| Orchestrator | proposed, approved, executed | not known |

Consequences observed on `main` (see #236 for the evidence):

1. A mutating `ToolDef` tool cannot be called directly (refused) and cannot be run by
   proposal: the proposal is **approved** with the declared effect, then
   `execute_proposal` fails with `Unknown tool`. The behavior fails closed, but the
   approval is meaningless and the error is misleading.
2. A non-mutating `ToolDef` tool runs directly, with no rate limit, mode check or audit
   record.
3. Two of the mutating behaviors have direct HTTP routes that reach the same state change
   outside any proposal, approval or control-audit record (static read, #236 comment):
   `POST /api/sensor/eve/ground` (and the chat path's automatic grounding) calls
   `sensor_stream_tool(check_only=False)`; `POST /api/graphops/directives/explain-coverage-cell`
   ingests into the RF solver evidence store.

Since #235 every `ToolDef` tool declares an effect and passes `validate_tool_contract`,
so the metadata is no longer the problem. The open question is the execution model.

The principle this ADR proposes: **execution semantics are a property of the control
plane, not of the tool implementation class.** Effect classification, approval, audit and
principal propagation should not depend on whether a tool is a Registry `Tool` or a
`ToolDef`. Only registration and discovery may differ. This is the direction of #220,
#221, #225, #226, #227 and #232–#235.

Registry tools are control-plane actions (decay, retune, ingest). GraphOps tools are
application services (investigation, explanation, feedback). They may not want identical
semantics, and that is a reason to decide deliberately rather than by drift.

## Options

**A. Promote `ToolDef` tools into the Registry.** Each `graphops_*` tool becomes a
`Tool`. One class, one lifecycle, one place to look.
- For: removes the split instead of managing it; the orchestrator, audit and rate limit
  apply with no new code path.
- Against: `ToolDef.fn(params)` and `Tool` handlers have different signatures, schemas
  (`input_schema` vs `parameters`/`returns`) and construction, so each tool is a rewrite
  and a regression risk. Read-only analysis tools would acquire rate limits and audit
  they may not need.

**B. Keep two classes behind one execution contract.** The orchestrator and
`tools/call` route both classes through a shared entry point that provides the same
validation, rate limit, mode check, proposal handling and audit record. `ToolDef` is
adapted, not rewritten.
- For: smaller change than A; preserves the application-service shape; a stepping stone
  toward A, since each tool can later move without changing callers.
- Against: two classes remain, and the adapter becomes a second place where lifecycle
  rules can drift. It needs a test that proves both classes get identical lifecycle
  behavior.

**C. Reject `ToolDef` execution in the orchestrator, explicitly.** A proposal for a
mutating `ToolDef` tool is refused up front with a stated reason (for example "tool class
not orchestratable") instead of approved and then failed.
- For: smallest change; turns a misleading failure into a truthful one; keeps the
  fail-closed behavior.
- Against: it decides nothing about whether these tools *should* be orchestratable, and
  leaves the HTTP routes as the only working path to the mutations.

These options are about MCP execution. They do not decide the HTTP routes (below).

## Separate decision: the direct HTTP routes

Whichever option is chosen, promoting, adapting or rejecting `ToolDef` tools leaves
`/api/sensor/eve/ground`, the chat auto-grounding and `/api/graphops/directives/explain-coverage-cell`
where they are. The precedent is `/api/sdr/*` (#227): the routes stayed and now write the
same control-audit record as MCP. That is a smaller, independent decision with three
possible outcomes: route through the shared lifecycle, record to the control audit only,
or document each as an accepted exception. Which mutations may bypass the canonical
boundary is classified in #218, not here. This ADR only requires that the question is
answered for these routes before #236 or #218 is closed.

## Decision

Accepted as architecture. Accepting it means future work is evaluated against it; it does
not mean any tool has been migrated.

1. **B is the target:** one execution contract, two classes, adapter-based. Treat it
   as a stepping stone toward **A**, tool by tool, where a tool's semantics fit the
   Registry.
2. **C** may land first as an interim step. It is optional and not decided here: it is independent of B,
   cheap, and removes the approve-then-fail behavior now. It must be described as interim
   so it is not mistaken for the decision. C on its own leaves two execution models
   permanently.
3. The HTTP routes are handled as their own change and are not decided here, following the `/api/sdr/*` precedent,
   tracked in #218.

Rationale for B over A as the first move: A rewrites twelve tools to change where they
live; B changes how they are executed, which is the thing that is wrong. A can follow
without redoing B.

## What B would require

Stated so the cost is visible, not as a design:

- A single execution entry point used by both `MCPHandler.tools/call` and
  `execute_proposal`.
- An adapter from `ToolDef` to what that entry point needs (schema for validation,
  `effect`, `mutates_state`).
- Non-mutating `ToolDef` tools stay directly callable but gain the audit record and rate
  limit, or an explicit, documented exemption per tool.
- A test asserting that for every tool in both classes, the lifecycle steps run
  identically (a mutating tool is refused directly, approved by proposal, executed once,
  audited once).
- `validate_tool_contract` keeps covering both classes; no change expected.

## Consequences if accepted

- The `Unknown tool` failure disappears: a mutating `GraphOps` tool is either executable
  through the lifecycle or refused with a truthful reason.
- Audit coverage of the `graphops_*` tools becomes uniform with Registry tools.
- The HTTP routes remain a known gap until their own decision lands.
- If mutating `ToolDef` tools become executable through the orchestrator, the approval
  semantics defined by #228 determine whether execution needs only automated policy
  evaluation or an independent approving principal. Implementations of B should define how
  mutating `ToolDef` tools participate in that policy, so the execution model should not be
  finalized without considering #228.

## Not decided here

| Decision | Issue |
|---|---|
| Disposition of the direct HTTP routes | #218 |
| Whether a second principal is required for any effect | #228 |
| Capability probes in place of `hasattr` | #220 |
| Data-egress classification | #220 (observation) |
