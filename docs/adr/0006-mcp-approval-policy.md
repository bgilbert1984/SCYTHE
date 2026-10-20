# ADR 0006 — Approval is a policy tier per effect, and independent approval is expressible

- **Status:** Proposed. Nothing here is decided or implemented. The default is a
  proposal for the owner to accept, change or reject.
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
- The control audit already records the proposer and the executor separately, so a
  second-principal rule needs no schema change.
- Proposals live in memory. A pending approval would not survive a restart unless that is
  addressed.
- The direct `/api/sdr/*` hardware routes reach the same hardware without a proposal at
  all. They write the same audit record (#227) but are not gated by anything in this
  ADR.

Nothing here is an authentication bypass: the caller was authenticated. The gap is that
authentication answers "who is asking", and the approval step does not answer "may they".

## Decision (proposed)

### 1. Name three approval modes and stop calling them all "review"

| Mode | Meaning |
|---|---|
| `DIRECT` | no approval step; only authorization of the caller |
| `AUTOMATED` | policy checks decide (today's `_check_mutation_approval`) |
| `SECOND_PRINCIPAL` | automated checks must pass **and** a different identified principal approves |

An effect maps to a mode. The mapping is data, in one place (`mcp_effects.py`), and the
existing validator checks that a tool's approval agrees with its effect.

### 2. The default mapping preserves current behavior

| Effect | Mode today and by default |
|---|---|
| `READ_ONLY`, `AUDIT_ONLY` | `DIRECT` |
| `LOCAL_STATE` | `AUTOMATED` |
| `TRAINING_DATA` | `AUTOMATED` |
| `EXTERNAL_STATE` | `AUTOMATED` |
| `HARDWARE_CONTROL` | `AUTOMATED` |

The one change this ADR proposes to make *possible*, not to make, is that
`HARDWARE_CONTROL` (and optionally `EXTERNAL_STATE`) can be configured to
`SECOND_PRINCIPAL`. Whether to turn it on is a deployment decision recorded separately.
Deciding the capability and deciding the setting are different decisions, and only the
first is proposed here.

### 3. Caller-supplied confidence is recorded, not trusted for hardware

Confidence stays stored, audited and shown to a reviewer. For `HARDWARE_CONTROL` it should
not by itself decide whether hardware may be actuated. Changing that alters behavior for
current callers, so it is its own change after this ADR is accepted.

### 4. What `SECOND_PRINCIPAL` must mean, if enabled

A second-principal rule is only as strong as the identity behind it, so it carries
conditions:

- The approver must be an **identified** principal (`operator_session`, per
  `mcp_principal.py`). `loopback`, `internal_token`, `anonymous` and `unspecified` cannot
  approve, because they do not identify an individual.
- The approver's `operator_id` must differ from the proposer's. Two sessions of one
  operator are one principal.
- A new proposal state, `pending_approval`, sits between `proposed` and `approved`, and
  a distinct approve/reject action exists. `orchestrate/decide` keeps its read-only
  meaning; the approver action is new, so existing clients are not reinterpreted.
- The approver's identity, time and optional note are recorded on the proposal and carried
  into the control-audit row (the schema already holds proposer and executor; an approver
  field is the one addition, and should be confirmed against the schema before
  implementation).
- Execution requires the recorded approval, and the executor check still applies.

### 5. Out of scope, deliberately

- **Role-based access.** "Operator may approve" is an identity rule, not a role system.
  Who is entitled to approve is not designed here.
- **`/api/sdr/*` and other direct routes.** Second-principal approval on MCP does not
  constrain a route that never enters the proposal flow. Whether those routes join it is
  tracked with the HTTP-path question in #236 and #218. Enabling `SECOND_PRINCIPAL` for
  hardware without addressing them would gate one door of two.
- **Fail-closed audit, durable proposal lifecycle events, anonymous/loopback hardware
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
| Whether the direct `/api/sdr/*` routes join the proposal flow | #236, #218 |
| Durable proposals; fail-closed audit; loopback/anonymous hardware control | #219 |
| Who is entitled to approve (roles) | not tracked |
