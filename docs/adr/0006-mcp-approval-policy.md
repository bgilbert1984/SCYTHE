# ADR 0006 — Approval is a policy tier per effect, and independent approval is expressible

- **Status:** Proposed; partly implemented. Read the three groups separately:
  - **Implemented:** the approval-mode table and the deployment floor ("a deployment may
    raise, never lower") in `mcp_effects.py` (`MINIMUM_APPROVAL_MODE`,
    `required_approval_mode`, checked by `validate_tool_contract`); the deployment setting
    `MCP_APPROVAL_MODES` (validated at startup, so a bad value stops the handler); the mode
    recorded on every proposal as `approval_mode`; the `pending_approval` state; the
    approval action `orchestrate/approve` (approve or deny, by an identified operator who
    differs from the proposer, with a note); the execution-time check, which recomputes the
    required mode from the tool's effect and the current setting and verifies the recorded
    approval and an unchanged parameter digest; the transition audit rows
    (`proposal.created`, `.approved`, `.denied`, `.expired`, keyed by `proposal_id`, with
    `control_audit.reconstruct_proposal` to read a history back); and the pending TTL
    (`MCP_APPROVAL_TTL_SECONDS`, default 900) with pruning. With the settings unset, the
    mapping is today's, and the only visible change is the additional lifecycle rows.
    The audit rows are evidence only: no decision reads them.
  - **Accepted architecture (reviewed and agreed 2026-10-21):** the independent
    proposal, approval and execution transitions (Decision 3), the `SECOND_PRINCIPAL`
    lifecycle (Decision 6), and that a halting action is exempt from independent approval
    (Decision 7). **Implemented for halting actions:** `Tool(halts=True)`, checked by
    `validate_tool_contract`, caps the mode at `AUTOMATED`; `rf_capture_control` is split into
    `rf_capture_start` and `rf_capture_stop`, and only the stop tool halts.
  - **Not yet implemented:** durable pending proposals (a restart drops them, which fails
    closed; the audit keeps the record, #219), role checks on who may approve, and any change
    of default. The approval action is exposed beyond `/mcp` as `GET /api/mcp/proposals/pending`
    and `POST /api/mcp/proposals/<id>/decision`, with a list and buttons in the SDR++ panel.
    The `/api/sdr/*` control routes now enter the lifecycle as `operator_command`
    (ADR 0005 Decision 5); with a raised `HARDWARE_CONTROL` mode their start, tune and config
    requests answer 409 `pending_approval`, and a second operator approves them from the SDR++ panel.

  Whether `HARDWARE_CONTROL` or `EXTERNAL_STATE` default to `SECOND_PRINCIPAL` remains a
  policy choice to be made with #228, sequenced after those mechanisms and after the
  `/api/sdr/*` routes join the lifecycle.
- **Date:** 2026-10-20
- **Resolves:** the policy question in #228. Builds on [ADR 0004](0004-mcp-control-plane.md)
  (approval derives from the declared effect) and [ADR 0005](0005-registry-tooldef-execution-model.md)
  (execution semantics belong to the control plane).
- **Related:** #219, #221, #223, #227, #229, #236, `docs/MCP_Tools.md`

## Context

Today approval for every mutating effect is one computed gate (`mcp_safety.py`,
`_check_mutation_approval`). A proposal is approved when the caller-supplied
`confidence >= 0.70`, executor trust is not below 0.30, and the graph is neither
oscillating nor entropically unstable. Drift only warns.

None of those checks says who may command a tool. The facts that make this matter, from
the code as it stands:

- `propose_action` records `proposed_by` and stops. It approves in the same call, so a
  proposal is `approved` before any other principal could see it.
- `orchestrate/decide` does not decide anything: it returns the status of a proposal
  (`check_proposal`). There is no approver action to call.
- `execute_proposal` runs an `approved` proposal and records the executor. The proposer
  and the executor can be the same principal, and are, in the run reported in #228
  (`rf_tune` at confidence 0.9, two calls, one operator session).
- The name `REQUIRES_REVIEW` overstates this. #229 documented the real meaning (automated
  policy evaluation); the name was kept.
- The control audit does **not** record the proposer. A row holds one principal (the
  actor of that row, the executor for an execution) and a `proposal_id`; the proposer
  exists only in the in-memory proposal (`proposed_by`) and is lost on restart. An
  approver would have nowhere to be recorded. (An earlier version of this ADR said the
  audit already held both; that was wrong.)
- Authorization is authentication only. Any authenticated principal can call
  `orchestrate/propose` and `orchestrate/execute`; nothing checks that they may command
  the tool.
- `agent_id` on `orchestrate/propose` is supplied by the caller. It is recorded, and it is
  not an identity: only the `Principal` established by authentication is.
- Mutations execute only above Phase 0, and the phase can be changed remotely only when
  `MCP_ALLOW_REMOTE_PHASE_CONTROL` is set. A default deployment therefore stays in Phase 0
  and executes no mutation through the orchestrator.
- Proposals are stored in memory in the current implementation, which limits restart
  behavior. That is an implementation constraint: the approval model permits durable
  pending approvals and does not require volatile proposals.
- The direct `/api/sdr/*` hardware routes reach the same hardware without a proposal at
  all. They write the same audit record (#227) but are not gated by anything in this
  ADR. (Written when that was true; they now enter the lifecycle as `operator_command`.)

Nothing here is an authentication bypass: the caller was authenticated. The gap is that
authentication answers "who is asking", and the approval step does not answer "may they".

## Decision (proposed)

### 1. Name three approval modes, as stages and not as trust levels

The modes describe which stages exist between authorization and execution. They are not
a ranking of how trusted a tool is.

| Mode | Meaning |
|---|---|
| `DIRECT` | Authorization is sufficient; no approval stage exists. |
| `AUTOMATED` | Authorization is followed by policy evaluation (today's `_check_mutation_approval`). |
| `SECOND_PRINCIPAL` | Authorization is followed by policy evaluation and independent approval by another identified principal. |

The declared effect determines the **minimum** approval mode that policy requires. The
mapping is data, kept in one place (`mcp_effects.py`), and the existing validator checks
that a tool's approval agrees with its effect. This ADR defines the mechanism, not the
table: the mapping below is the current default, and changing a row (for example moving
`HARDWARE_CONTROL` to `SECOND_PRINCIPAL`) is a policy change that does not require
amending this ADR.

### 2. Invariants

- **No execution path may bypass the approval semantics associated with the declared
  effect.** A path that does is an exception to reconcile, not a parallel design. The
  direct `/api/sdr/*` routes are such a path today (see Out of scope); this ADR makes
  that divergence explicit instead of silent.
- **Caller-supplied metadata may contribute to audit records but should not, by itself,
  authorize an irreversible action.** Confidence is the case #228 found. The same rule
  covers any similar field a caller can set, such as risk, urgency, severity or
  certainty.
- **A deployment may require a stricter approval mode than the architectural minimum
  for an effect, never a weaker one.** The declared effect sets the floor. A deployment
  may raise it (for example to `SECOND_PRINCIPAL`) and may not lower it, so no
  configuration can undo a guarantee that the declared effect establishes. The one
  exception is a halting tool (Decision 7), which the tool's author declares in code and
  which a deployment cannot grant.

### 3. Proposal, approval and execution are independent transitions

Proposal, approval and execution are independent lifecycle transitions. Approval
authorizes execution but is not execution. Execution remains subject to all runtime
policy: phase, mutation budget, parameter integrity and authorization. Approval is a
necessary condition for execution, never a substitute for execution-time policy.

### 4. The current default mapping preserves current behavior

| Effect | Mode today and by default |
|---|---|
| `READ_ONLY`, `AUDIT_ONLY` | `DIRECT` |
| `LOCAL_STATE` | `AUTOMATED` |
| `TRAINING_DATA` | `AUTOMATED` |
| `EXTERNAL_STATE` | `AUTOMATED` |
| `HARDWARE_CONTROL` | `AUTOMATED` |

This ADR proposes to make it *possible*, not to make it so, that `HARDWARE_CONTROL` (and
optionally `EXTERNAL_STATE`) require `SECOND_PRINCIPAL`. Whether to turn that on is a
policy decision recorded separately. Deciding the capability and deciding the setting
are different decisions, and only the first is proposed here.

### 5. Caller-supplied confidence is recorded, not trusted for hardware

Applying the invariant above: confidence stays stored, audited and shown to a reviewer.
For `HARDWARE_CONTROL` it should not by itself decide whether hardware may be actuated. Changing that alters behavior for
current callers, so it is its own change after this ADR is accepted.

### 6. What `SECOND_PRINCIPAL` must mean, if enabled

A second-principal rule is only as strong as the identity behind it, so it carries
conditions. The mechanism below was reviewed and agreed on 2026-10-21; it is not
implemented.

**States.** `proposed` leads to `rejected`, `approved`, or `pending_approval`. A pending
proposal becomes `approved`, `denied` or `expired`. An approved proposal executes as
today. `pending_approval`, `denied` and `expired` are new, and `orchestrate/decide` keeps
meaning "report status".

**Choosing the mode.** When a proposal is created, the mode is
`required_approval_mode(effect, deployment_settings)`. For `SECOND_PRINCIPAL` the automated
checks run first (a failure is `rejected` and the approver is not asked); a pass leaves
the proposal `pending_approval`. The proposal records its `approval_mode`. The deployment
setting comes from one source read at startup, an environment variable
(for example `MCP_APPROVAL_MODES=HARDWARE_CONTROL=SECOND_PRINCIPAL`, parsed like
`MCP_ALLOW_REMOTE_PHASE_CONTROL`). A value below the floor, an unknown effect or an
unknown mode stops startup. Unset means the floor, so there is no behavior change by
default.

**Who may approve.** The approver must be an identified principal (`operator_session`, per
`mcp_principal.py`); `loopback`, `internal_token`, `anonymous` and `unspecified` cannot
approve, because they do not identify an individual. The approver's `operator_id` must
differ from the proposer's, so two sessions of one operator are one principal. A proposer
that is not an identified operator (an internal automation) may create the proposal, and
any identified operator may approve it. `agent_id` is never used as an identity. The
approver may also execute: the control is independent approval, not separation of
execution.

**The approval action.** A distinct action (`orchestrate/approve`: `proposal_id`, a
decision of approve or deny, an optional note) is the only way out of `pending_approval`.
One function in the orchestrator enforces the preconditions for every transport; how it
is exposed (RPC, REST, CLI or UI) is not decided here. On approval the proposal records
the approver, the time, the note and a digest of the parameters approved.

**Execution.** `execute_proposal` requires, for a `SECOND_PRINCIPAL` proposal, a recorded
approver who differs from the proposer, and parameters that still match the approved
digest. This repeats the approval-time checks, so a defect that sets `approved` directly
still cannot execute. Phase, budget and the executor check apply as for any proposal.

**Audit.** Transitions are written as rows of the existing control-audit table, with no
schema change: a proposal-created row (principal: the proposer), and an approved or denied
row (principal: the approver, the note in the summary). Execution rows already carry
`proposal_id`. **`proposal_id` is the documented stable join key** for reconstructing a
proposal's history (proposer, approver, executor) from the audit, and it must not be
reused or re-derived.

**Durability.** Proposals stay in memory in the first implementation. A restart drops
pending proposals, which fails closed: the proposer proposes again, and the audit rows keep
the record. A pending proposal expires after a TTL (proposed default: 15 minutes,
configurable) so a stale proposal cannot be approved after its context has changed.
Durable pending proposals need a store that keeps parameters unredacted, which the audit
store deliberately does not; that is tracked with #219.

**Unsatisfiable configurations.** A deployment with a raised mode and no identified
operators cannot approve anything, so the effect cannot execute through MCP. That is the
intended fail-closed outcome; a warning is logged at startup.

**Order of work.** First, the mode on the proposal, the deployment setting and
`pending_approval` with no way out (a deployment that opts in stops executing and
nothing bypasses it). Then the approval action, the execution check, the audit rows and
the TTL. Changing a default is a separate policy decision after both, and the
`/api/sdr/*` routes joining the lifecycle is a separate design, because it changes the
workflow for live hardware (a click becomes a proposal that another operator approves).

### 7. A halting action is not held for a second principal

Stopping equipment must remain available. A tool that only ceases activity, such as
stopping a capture, stays inside the lifecycle: it is proposed, authorized and audited
like any other mutation. It is exempt only from independent approval: its required mode is
capped at `AUTOMATED` whatever a deployment sets.

- The exemption is a declaration in the tool's own code (proposed name: `halts`), checked
  by `validate_tool_contract`, and never a deployment setting. A deployment can therefore
  not use it to weaken policy, which keeps the "raise, never lower" invariant intact.
- It is narrow. A tool may declare `halts` only if its sole purpose is to stop or
  withdraw activity, it mutates state, and it cannot start, configure or retarget
  anything. Tools that start, tune, configure, transmit or change training data are never
  halting.
- **Mechanism (implemented):** `rf_capture_control` took `action: start | stop` and the mode
  is per tool, so it was split into `rf_capture_start` and `rf_capture_stop`, with `halts=True`
  on the stop tool. `rf_capture_control` is retired with no alias: an alias that accepts
  `stop` would carry the exemption back into a tool that can also start. The alternative,
  one tool with an action-level rule in the approval step, would have spread the exemption
  into policy code. `validate_tool_contract` requires a halting tool to mutate state, not
  to be `TRAINING_DATA`, and to take no parameters (so it cannot be told where or how to
  act); `test_mcp_halting_action` pins the set of halting tools, so adding one is a
  deliberate change. The exemption removes only the second-principal requirement: an
  `autonomous` stop is still refused in Phase 0, and the proposal, authorization, digest
  check and audit still apply.

### 8. Out of scope, deliberately

- **Role-based access.** "Operator may approve" is an identity rule, not a role system.
  Who is entitled to approve is not designed here. Likewise, who may *propose* or
  *execute* is still authentication only.
- **`/api/sdr/*` and other direct routes.** Second-principal approval on MCP does not
  constrain a route that never enters the proposal flow, which contradicts the invariant
  above until reconciled. The `/api/sdr/*` routes will join the lifecycle as
  `operator_command` ([ADR 0005](0005-registry-tooldef-execution-model.md), Decision 5);
  until that lands, enabling `SECOND_PRINCIPAL` for hardware would gate one door of two.
  Even then, a deployment that raises `HARDWARE_CONTROL` also needs a way for a second
  operator to approve (REST or UI): approval is exposed only over `/mcp`, so a raised mode
  would otherwise stop the first-party radio interface.
- **Fail-closed audit, durable proposals, anonymous/loopback hardware
  control outside development** — the other questions in #219.
- **A data-flow or communication attribute.** Separate (#220 observation).

## Alternatives considered

- **Require a second principal for hardware now.** Rejected as a first move: it changes
  behavior for current callers, needs the new state, action, durable proposals and an
  answer for the direct routes, and its strength depends on identified operators existing
  in every deployment that drives hardware.
- **Keep as is and rename the tier.** Cheap and honest, and partially done by #229. It
  leaves no way to express independent approval, so the question returns with the first
  deployment that needs it.
- **Ignore confidence for every mutating effect.** Broader than the evidence. #228 shows
  the weakness for hardware; the other effects were not examined.

## Consequences if accepted

- `REQUIRES_REVIEW` could be retired in favor of the three modes, or kept as an alias for
  `AUTOMATED` plus `SECOND_PRINCIPAL`. That choice is cosmetic and should not block the
  rest.
- Approval policy becomes visible in one table instead of inferred from one function.
- Together with ADR 0005 option B, a mutating `ToolDef` tool takes its mode from its
  declared effect, with no separate rule for the class.
- A deployment without identified operators cannot satisfy `SECOND_PRINCIPAL`; enabling it
  there makes the effect unexecutable through MCP. That is fail-closed and should be
  documented as such, not treated as a bug.
- The audit store remains tamper-evident policy, not protection from a privileged host
  user. A second principal does not change that.

## Not decided here

| Decision | Issue |
|---|---|
| Whether to enable `SECOND_PRINCIPAL` for `HARDWARE_CONTROL` in a given deployment | #228 follow-up |
| The mechanism for exempting halting actions | decided and implemented: `halts`, split `rf_capture_stop` (Decision 7) |
| Whether the direct `/api/sdr/*` routes join the proposal flow | decided in ADR 0005 Decision 5 and implemented (#236, #218) |
| Durable proposals; fail-closed audit; loopback/anonymous hardware control | #219 |
| Who is entitled to approve (roles) | not tracked |
