# MCP tools

Reference for how a tool is defined, authorized, approved, executed and audited in
SCYTHE's MCP layer. It describes current behavior. Open questions are listed under
[Current limitations](#current-limitations) and are not decided here.

## Purpose

An MCP tool is a named operation a caller can invoke over `/mcp` (`tools/call`) or,
for state-changing tools, through the orchestrator proposal flow.

There are two classes of tool, held in different places:

| Class | Defined by | Held in | Lifecycle |
|---|---|---|---|
| Registry tool | `mcp_registry.Tool` | `MCPHandler._registry` and the orchestrator's registry | validation, rate limit, durable audit, proposal flow |
| ToolDef tool | `mcp_server.ToolDef` | `MCPHandler._tools` only (the `graphops_*` tools) | runs through `ToolDef.fn`; see [limitations](#current-limitations) |

RF tools (`rf_mcp.py`) are Registry tools registered after `build_registry`.

## Terminology

The four steps are kept separate on purpose.

- **Authorize**: authenticate the caller and decide whether they may invoke the API at
  all. The result is a `Principal` (`mcp_principal.py`).
- **Approve**: policy evaluation of a proposal before execution. Currently automated for
  every effect that needs it; no second principal is involved.
- **Execute**: perform the operation.
- **Audit**: durably record what happened.

## Lifecycle

1. **Registration.** A tool is registered with its schema, `mutates_state` and `effect`.
   `Registry.register` refuses a duplicate name.
2. **Authorization.** The auth step establishes a `Principal`, carried for the duration
   of the request. Its `kind` states how strong the identification is
   (`operator_session`, `internal_token`, `loopback`, `anonymous`, `unspecified`). Only
   `operator_session` identifies an individual.
3. **Proposal and approval (state-changing tools).** A direct `tools/call` of a tool with
   `mutates_state` is refused. The caller uses `orchestrate/propose`,
   `orchestrate/decide` and `orchestrate/execute`. Approval is decided from the tool's
   declared effect (see below), never from its name. `HARDWARE_CONTROL` requires
   `SECOND_PRINCIPAL` unless the deployment says otherwise (`MCP_APPROVAL_MODES`, which can also raise any
   other effect; `HARDWARE_CONTROL=AUTOMATED` restores single-operator behavior explicitly). For hardware
   the caller's `confidence` is recorded but does not decide. Pending and approved-but-unexecuted proposals
   live in memory: a restart drops them (they can no longer be decided or run) and the audit records them as
   `proposal.expired`, orphaned by restart. Approval authority is granted
   explicitly: only operators whose `operator_id` is listed in `MCP_APPROVERS` may approve or deny, and an
   empty list means nobody can (a hardware proposal then cannot run). When an effect requires
   `SECOND_PRINCIPAL`, a proposal that passes the
   automated checks waits as `pending_approval` until a different identified operator
   calls `orchestrate/approve` (`proposal_id`, `decision` of `approve` or `deny`, an optional
   `note`); execution then re-checks the approval and that the parameters are unchanged.
   `orchestrate/decide` only reports status. The same action is available over REST for first-party
   interfaces: `GET /api/mcp/proposals/pending` lists what is waiting and
   `POST /api/mcp/proposals/<id>/decision` (`decision`, optional `note`) approves and runs it, or denies it.
   Two read-only routes describe the lifecycle for display and decide nothing: `GET /api/mcp/governance`
   (whether the lifecycle is enabled, the phase, the effective approval mode of each effect and the deployment's
   overrides, the pending TTL, whether remote phase changes are enabled) and `GET /api/mcp/proposals/recent`
   (the lifecycle state of recent proposals, newest first, with who proposed, approved, denied or executed them and
   why one did not run). Both need an identified operator session (the governance route also accepts the internal token).
   A tool its author declares `halts` (only
   `rf_capture_stop`: it takes no parameters and only ceases activity) is capped at `AUTOMATED` and is never
   held for a second principal; it is still proposed, authorized and audited (ADR 0006 Decision 7).
4. **Execution.** `Registry.execute` validates parameters, enforces the rate limit and
   runs the tool. The orchestrator also applies the autonomy phase to `autonomous` proposals (every
   `/mcp` proposal; an `operator_command` proposal, assigned only by server code, skips the phase
   rules and keeps approval, the digest check and the audit; ADR 0005 Decision 5): in phase 0 a
   mutating tool is blocked as observe-only, phase 1 returns a shadow estimate, and
   phases 2 and 3 execute within a mutation budget.
5. **Audit.** Every `Registry.execute` writes an `[MCP_AUDIT]` log line carrying the
   tool, effect, proposal ID and principal. When `control_audit` is configured, state-changing
   executions are also written to a durable, append-only SQLite store as a `started` row
   and a `success` or `error` row sharing an `execution_id`. Each row carries a `source`: `mcp` by default, or the name an adapter sets with
   `source_scope` (for example `api.sdr.tune`), so one effect reached two ways stays distinguishable. That store is separate from
   the graph and does not go through WriteBus. The same store records a proposal's
   lifecycle as `proposal.created`, `proposal.approved`, `proposal.denied` and
   `proposal.expired` rows (written for proposals whose approval mode is above `DIRECT`),
   each carrying the `initiator` class and the `proposal_id` that also appears on the execution rows, so the
   proposer, approver and executor can be read back after a restart
   (`control_audit.reconstruct_proposal`). The `result_summary` of a `HARDWARE_CONTROL` execution keeps the
   result's nested scalars under dotted keys (for example `bridge.bridge_state`), so the row says what state
   the hardware ended in; it is bounded to 60 keys, 3 levels and 256 characters, and secret-named keys are
   redacted. Other effects keep the scalar-only summary. These rows are evidence only: no approval or
   execution decision reads them. A `pending_approval` proposal expires after
   `MCP_APPROVAL_TTL_SECONDS` (default 900) and can no longer be approved.

## Effect taxonomy

A tool declares one effect (`mcp_effects.py`). A tool mutates state when it causes an
externally observable state transition, including state outside the local graph. "Does
not call an engine mutation method" is not the test.

| Effect | Meaning | Approval |
|---|---|---|
| `READ_ONLY` | no state transition | not needed |
| `AUDIT_ONLY` | writes only records about its own call | not needed |
| `LOCAL_STATE` | this process's graph, stores, queues, caches | proposal flow |
| `EXTERNAL_STATE` | a remote service or database | proposal flow |
| `HARDWARE_CONTROL` | tuner, capture bridge, other devices | proposal flow |
| `TRAINING_DATA` | feedback that changes a model's training set | proposal flow |

Anything not positively known to be non-mutating needs review: an unknown tool, a tool
with no declared effect, and any mutating effect.

## Current invariants

Enforced by tests, not by convention.

- Every Registry tool declares its effect explicitly. `mutates_state` must agree with the
  effect, and the approval policy must match it.
- `validate_tool_contract` (`mcp_effects.py`) checks schema, explicit effect, flag
  consistency, approval consistency and name uniqueness. It runs in CI over the Registry
  tools as served and over the `ToolDef` tools.
- The same check runs at startup (`MCPHandler.assert_contract`): after `register_mcp_routes`
  registers its tools, and again in `rf_scythe_api_server` after the embedding and GraphOps
  registrations. A violation raises `ToolContractError` and stops the server; it is not
  caught by the log-and-continue blocks around the registrations. An integration that cannot
  load registers no tools and is not a violation. A tool that never declared an effect is
  still a violation, and (before this check) was served with the conservative review policy.
- Approval is derived from the declared effect.
- Principal, proposal ID and effect are carried into the audit record.
- `/api/sdr/*` hardware routes are adapters into the lifecycle (`operator_command`), so they leave the same
  proposal and execution rows as MCP, with `source` `api.sdr.*`.
- Every `graphops_*` `ToolDef` tool declares an effect. The four read-only analysis tools
  (`graphops_dsl_exec`, `graphops_investigate`, `graphops_directive`, `graphops_suggest`)
  were audited from the code, and `test_graphops_tool_effects.py` pins what that audit
  relied on: the DSL verb set, the engine accessors the verbs may touch, the agent's
  disabled sensor-stream path, and the engine-less director.

The validator checks metadata only. It does not check that a backing implementation
exists.

## Mutation boundary classification

Per ADR 0007 (Proposed), each mutating tool has one documented boundary. Classified so far
(#218); `test_mcp_mutation_boundary.py` pins both against a `strict_no_bypass=True` engine.

| Tool | Category | Boundary | Level (ADR 0008) | Partial failure (ADR 0009) |
|---|---|---|---|---|
| `ingest_live_event` | durable graph mutation | `WriteBus.commit`, one commit per event, carrying the principal and proposal id (`correlation_id`) | Audit: the commit records provenance; the event payload is not retained beyond the graph | Partial by nature. Result reports `committed`, `dequeued`, `rejected` and the first 50 `rejected_events` with reasons. Rejected events are not requeued but are no longer lost silently |
| `decay_now` | runtime control with a graph effect (maintenance) | Documented exception: `HypergraphEngine.decay_edges_pruned` runs inside `writebus.maintenance_scope()`, which permits only that pruning under `strict_no_bypass` | Forensic: pruned edge ids are logged (`[DECAY_PRUNED]`) and their SHA-256 is in the audited result | Prunes edge by edge; an exception part-way propagates and the ids pruned before it are not reported |

Notes:

- The maintenance exception applies to every `decay_edges` caller: the tool, the engine's
  background thread and the server's post-ingest hook. Before this, under
  `strict_no_bypass` the last two failed silently (they swallow exceptions); that was
  inferred from the guard, not observed on a live server.
- `ingest_live_event` refuses, before dequeuing, if WriteBus is not initialised or is bound
  to a different engine.
- Open: the server's live-ingest worker also drains the same queue and writes
  `RECON_ENTITY` records directly into `recon_system.entities`, outside the graph boundary
  and the control audit. The two consumers race for events. Unclassified.
- Open: the other mutating Registry tools, and the direct HTTP routes not yet in the
  lifecycle (#236).
- Technical debt noticed, not changed: `decay_now`'s `lambda` does not affect which edges
  are pruned (the persistence score reads the engine's own `decay_lambda`).

## Current limitations

- **Registry and ToolDef split.** The orchestrator knows only Registry tools. A mutating
  `ToolDef` tool is approved by a proposal and then fails with `Unknown tool`, and a
  direct call is refused. Non-mutating `ToolDef` tools run directly, without the
  Registry's rate limit, mode check or audit record. Tracked in #236.
- **Capability versus implementation.** Handlers probe their engine with `hasattr` and
  can return success when the backing method is missing. Tracked in #220.
- **Approval semantics.** `REQUIRES_REVIEW` is automated policy evaluation over a
  caller-supplied confidence, executor trust and graph stability. No independent approver
  is required, and the proposer can execute. Tracked in #228.
- **State boundary.** Two tools are classified (below); the other mutating tools are not
  yet, and replayability of mutations is open. Tracked in #218.
- **Data egress is not an effect.** `graphops_investigate` sends prompts built from graph
  data to the configured Ollama endpoint, which can be Ollama Cloud. The DSL embedding
  verbs call an Ollama embedding endpoint. Neither changes state on the remote, so they
  are `READ_ONLY`, but the taxonomy has no way to say a tool sends data out. Recorded as
  an observation on #220; no change is proposed.

## Related issues

| Topic | Issue |
|---|---|
| Mutation boundary, replayability | #218 |
| RF control audit chain | #219 |
| Capability metadata integrity and runtime capability | #220 |
| Approval policy hardening | #221 |
| Approval policy decisions (second principal) | #228 |
| Orchestrator and `ToolDef` tools | #236 |
