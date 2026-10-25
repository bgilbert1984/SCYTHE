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
   declared effect (see below), never from its name. When a deployment requires
   `SECOND_PRINCIPAL` for an effect (`MCP_APPROVAL_MODES`), a proposal that passes the
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
   (`control_audit.reconstruct_proposal`). These rows are evidence only: no approval or
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
- **State boundary.** Some tools write to the engine directly rather than through
  WriteBus, and whether that is acceptable per tool is undecided. Replayability of
  mutations is also open. Tracked in #218.
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
