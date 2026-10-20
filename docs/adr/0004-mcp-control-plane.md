# ADR 0004 — MCP tools declare their effect, and policy follows the declaration

- **Status:** Accepted and implemented for decisions 1–5. Decision 6 is **not decided**;
  it is recorded here so the gap is visible. Open decisions are listed at the end.
- **Date:** 2026-10-20
- **Supersedes nothing.** Records decisions made across #222–#237. The audit that led to
  them is #216 and #218.
- **Related:** `docs/MCP_Tools.md` (current behavior), #218, #220, #228, #236

## Context

The MCP layer started as callable functions with loose contracts. The audit found that
the approval boundary depended on facts nobody had stated or checked:

- A tool counted as a mutation if its author set `mutates_state`, and that flag had been
  read as "calls an engine mutation method". A tool could change state outside the local
  graph (a remote service, a training set, hardware) and be marked non-mutating.
- `propose_action` auto-approved any tool whose **name** contained `"query"`.
- The auth step returned a bool, so who made a call was lost before a proposal existed.
  The proposal log was in memory, had no operator field, and was not linked to the
  `[MCP_AUDIT]` line, which had its own uuid.
- Direct `/api/sdr/*` hardware routes reached the same hardware without the proposal flow
  or any record.
- The orchestrator kept an empty registry, so registry tools could not run through it at
  all (#224).

Each of these made an authorization or accountability answer depend on inference.

## Decision

### 1. A tool declares one effect, and the declaration is checked

Every tool declares an effect from a small taxonomy: `READ_ONLY`, `AUDIT_ONLY`,
`LOCAL_STATE`, `EXTERNAL_STATE`, `HARDWARE_CONTROL`, `TRAINING_DATA`
(`mcp_effects.py`). A tool mutates state when it causes an externally observable state
transition, including outside the local graph. An effect that contradicts
`mutates_state` is rejected when the tool is constructed.

`mutates_state` is kept alongside `effect` instead of being derived from it. The
redundancy is what lets a validator catch drift; revisit it once the metadata has been
stable for a while.

### 2. Approval is derived from the declared effect, never from a name

Non-mutating effects (`READ_ONLY`, `AUDIT_ONLY`) are approved without the mutation
checks. Everything else needs review: any mutating effect, a tool with no declared
effect, and an unknown tool. The taxonomy stays at six categories; no further effect
classes or approval tiers are added without a concrete use case.

### 3. The caller's identity is carried to the proposal and the audit record

Authentication produces a `Principal`, held in a context variable for the request and
read when a proposal is created, executed and audited. Its `kind` says how strong the
identification is, and only an operator session identifies an individual. No role-based
access design is introduced: the principal is recorded, not yet used to authorize an
action.

### 4. Control actions have a durable, append-only audit, separate from the graph

State-changing executions write a `started` row and a `success` or `error` row to a
SQLite store (`control_audit.py`). Append-only is enforced in the database: updates are
refused and deletes are refused inside the retention window. The store is deliberately
not WriteBus: an RF retune may create no graph mutation and still needs an accountable
history. The direct `/api/sdr/*` routes write the same record as MCP.

### 5. A metadata contract is validated in CI; runtime capability is a separate concern

`validate_tool_contract` checks, for every registered tool: object schemas, an explicit
effect, `mutates_state` consistent with the effect, an approval policy that matches the
effect, and unique names. It covers Registry tools as served and the `ToolDef` tools. It
does **not** check that a backing implementation exists. Whether a tool is supported at
runtime is tracked in #220, so a metadata failure and a capability failure stay
distinguishable. Every `graphops_*` `ToolDef` tool declares an effect (#239). The four
read-only analysis tools were audited from the code, and `test_graphops_tool_effects.py`
pins what the audit relied on. `graphops_directive` is `READ_ONLY` as wired, because its
director is built without an engine; it is not `READ_ONLY` unconditionally.

### 6. Registry tools and ToolDef tools: not decided

Two classes of tool exist. Registry tools have proposal, approval, rate limit and audit.
`ToolDef` tools (the `graphops_*` tools) run through `ToolDef.fn`; a mutating one is
refused when called directly and, when proposed, is approved and then fails with
`Unknown tool`. Whether they are meant to share one lifecycle, and if so how, is the
question in #236. This ADR takes no position.

## Consequences

- A new Registry tool cannot reach CI without a declared effect, a consistent flag and a
  matching approval policy.
- Approval and audit answer from declared metadata, so a wrong declaration is now the
  failure mode. Declarations are reviewed by hand; the validator checks consistency, not
  truth.
- `REQUIRES_REVIEW` currently means automated policy evaluation over a caller-supplied
  confidence, executor trust and graph stability. The proposer can execute. The name is
  kept for when a second-principal rule is introduced (#228).
- Some tools still write to the engine directly rather than through WriteBus, and
  whether that is acceptable per tool is open (#218).
- The audit store is tamper-evident policy, not protection from a privileged host user.
- `READ_ONLY` means no state transition. It does not mean no external communication:
  `graphops_investigate` and the DSL embedding verbs send graph-derived text to a remote
  model endpoint. The effect taxonomy answers "can this tool change state?" and not "can
  it disclose information to another system?". A data-flow classification may be needed
  to tell computational reads from outbound transfer. That is not part of this ADR.

## Open decisions

| Decision | Issue |
|---|---|
| Whether any effect (especially `HARDWARE_CONTROL`) requires a second principal | #228 |
| Which mutations may bypass WriteBus; behavior under `strict_no_bypass`; replayability | #218 |
| Whether `ToolDef` tools join the proposal lifecycle, are promoted, or stay direct | #236 |
| Explicit supported/unsupported capability declarations in place of `hasattr` probes | #220 |
| Whether outbound data flows (prompts or embedding requests to a remote model) need their own classification | not yet raised; relevant to #220 |
