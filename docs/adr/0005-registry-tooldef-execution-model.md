# ADR 0005 — Registry and ToolDef tools: one lifecycle, or an explicit boundary

- **Status:** Accepted (architecture), 2026-10-20: the principle and Option B as the
  long-term direction. Amended 2026-10-21 with the mutation invariant (Decision 4).
  **Partially compliant.** Option B is implemented for every MCP tool (#249, #250, #251);
  interim step C was not taken. The invariant is the rule and the listed HTTP exceptions
  are known non-compliance, not accepted alternatives.
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

Consequences observed on `main` when this ADR was accepted (see #236 for the evidence;
items 1 and 2 are resolved by #249–#251, item 3 is not):

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

## The direct HTTP routes

The first version of this ADR left the HTTP routes undecided and listed three possible
outcomes: route through the shared lifecycle, record to the control audit only, or
document each as an accepted exception. The precedent was `/api/sdr/*` (#227): the routes
stayed and now write the same control-audit record as MCP.

With the lifecycle implemented, tested and exercised, that latitude is withdrawn.
"Control-audit only" and "accepted exception" are no longer architecturally sanctioned
steady states, because they preserve two mutation paths with different guarantees. The
rule is Decision 4. The routes below are known exceptions to it.

## Decision

Accepted as architecture. Accepting it means future work is evaluated against it; it does
not mean any tool has been migrated.

1. **B is the target:** one execution contract, two classes, adapter-based. Treat it
   as a stepping stone toward **A**, tool by tool, where a tool's semantics fit the
   Registry.
2. **C** was an optional interim step and was not taken: B removed the approve-then-fail
   behavior directly.
3. Which HTTP routes mutate state, and in what order they migrate, is tracked per route;
   the canonical record is the HTTP route inventory on #236
   (https://github.com/bgilbert1984/SCYTHE/issues/236#issuecomment-6292702102).
4. **The mutation invariant.** Every externally initiated state mutation SHALL execute
   through the lifecycle pipeline, which is currently realized by `Registry.execute`.
   Transports (MCP, HTTP, UI, CLI, or future interfaces) are adapters that invoke
   the lifecycle; they are not independent mutation mechanisms and MUST NOT bypass
   proposal, approval, audit or lifecycle enforcement. The review question for new code
   is "can this mutate state?", not "which transport is it?".

### Implementation status against the invariant

Architecture and migration are separate: the invariant is decided; compliance is partial.

| | Status |
|---|---|
| MCP `Registry` tools | compliant |
| MCP `ToolDef` tools, mutating and non-mutating | compliant (`adapt_tooldef`, #249, #250) |
| `/api/sdr/*` (`status`, `start`, `stop`, `tune`, `config`) | **known exception**: writes a control-audit record directly, no proposal or lifecycle (#227) |
| `POST /api/embedding/entity` | **known exception**: performs the `embed_entity` mutation with no proposal, approval or audit record |
| `POST /api/sensor/eve/ground`, chat auto-grounding, `POST /api/graphops/directives/explain-coverage-cell` | **known exceptions** (Context, item 3) |
| other non-GET `/api/...` routes | **not yet classified**; the inventory on #236 is a static first pass |

Exceptions are temporary compatibility interfaces. Each should migrate to the lifecycle or
be retired. Migrating a UI-facing route changes what the caller sees: under the lifecycle
a mutation can be blocked by the current phase, held for approval, or return a proposal
instead of an effect, and that must be stated in the migrating change.

Rationale for B over A as the first move: A rewrites twelve tools to change where they
live; B changes how they are executed, which is the thing that is wrong. A can follow
without redoing B.

## What B required, and where it is implemented

| Requirement | Implementation |
|---|---|
| A single execution entry point used by `MCPHandler.tools/call` and `execute_proposal` | `Registry.execute` |
| An adapter from `ToolDef` to what that entry point needs (schema, `effect`, `mutates_state`) | `adapt_tooldef` in `mcp_registry.py` (#249, #250) |
| Non-mutating `ToolDef` tools stay directly callable but gain the audit record and rate limit | `MCPHandler._handle_tools_call` (#250) |
| A test over both classes that the lifecycle steps run identically | `test_mcp_tooldef_parity`, and `test_mcp_lifecycle_invariant` (#251), which enumerates every tool: non-mutating tools are executed by the Registry exactly once, direct calls to mutating tools are refused, and mutating tools execute exactly once via propose then execute |
| `validate_tool_contract` keeps covering both classes | unchanged |

The invariant test covers MCP only. No test yet asserts Decision 4 for HTTP routes.

## Consequences if accepted

- The `Unknown tool` failure disappears: a mutating `GraphOps` tool is either executable
  through the lifecycle or refused with a truthful reason.
- Audit coverage of the `graphops_*` tools becomes uniform with Registry tools.
- The HTTP routes remain a known gap, recorded above, until each exception migrates or is retired.
- If mutating `ToolDef` tools become executable through the orchestrator, the approval
  semantics defined by #228 determine whether execution needs only automated policy
  evaluation or an independent approving principal. Implementations of B should define how
  mutating `ToolDef` tools participate in that policy, so the execution model should not be
  finalized without considering #228.

## Not decided here

| Decision | Issue |
|---|---|
| Order and mechanism of migrating each HTTP exception | #236 (inventory), #218 (classification of what mutates) |
| Whether a second principal is required for any effect | #228 |
| Capability probes in place of `hasattr` | #220 |
| Data-egress classification | #220 (observation) |
