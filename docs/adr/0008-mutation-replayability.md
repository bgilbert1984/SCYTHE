# ADR 0008 — Every mutation owes a declared level of explainability

- **Status:** Proposed. Nothing here is decided or implemented. The levels, and which
  mutations owe which, are for the owner to accept, change or reject.
- **Date:** 2026-10-20
- **Resolves:** the replayability item in #218.
- **Builds on:** [ADR 0004](0004-mcp-control-plane.md) (audit), [ADR 0006](0006-mcp-approval-policy.md)
  (proposal and approval record), [ADR 0007](0007-mutation-boundary.md) (one boundary per
  mutation).
- **Related:** #213, #218, #219, `docs/MCP_Tools.md`

## Context

#218 asks: if this mutation happened yesterday, can we explain why the graph looks
different today? "Replayable" is used loosely for that. It covers guarantees that differ
in what they need and in what they cost, so the word alone does not tell an implementer
what to build.

What is recorded today, from a static read of the code:

| Record | Holds | Does not hold |
|---|---|---|
| Control audit (`control_audit.py`) | action, effect, parameters (secrets redacted), result and summary, principal kind and id, session, proposal id, execution id, software version, deployment id, host id; append-only | the state before or after; a link to a graph commit; the approval rationale |
| WriteBus provenance (`writebus.py`) | source, mission, operator, session, request id, model version, evidence references, timestamp, causal and parent-fingerprint links, correlation id; idempotency and commit status in SQLite | the proposal; the policy that approved it |
| Proposal log (`mcp_safety.py`) | proposed-by, effect, confidence, justification, approval reason, execution result | durability: it is in memory in the current implementation |
| `[MCP_AUDIT]` log line | tool, effect, proposal id, principal | anything durable by itself |

The two durable stores answer different questions and are not joined: a control-audit row
has no pointer to the WriteBus commit it caused, and a commit has no pointer to the
proposal that authorized it, other than what a caller chose to put in `correlation_id`.
Whether that holds for a given path was not traced end to end.

Mutations outside both (the cases ADR 0007 asks to be classified) leave only the log line.

## Decision (proposed)

### 1. The invariant

**Every mutation is replayable to a declared level, or is explicitly declared
non-replayable with a reason.** Silence is not a valid state.

The bar stays the one #218 set: explainability appropriate to the mutation, not that every
mutation be reproducible. A scratch cache write and an evidence write do not owe the same.

### 2. Name four levels, because they are different guarantees

| Level | Question it answers | Needs |
|---|---|---|
| **Audit** | Who did what, when, under whose authority, with what result? | the audit record |
| **Forensic** | What exactly changed, and from what inputs? | audit, plus the inputs or a reference to them, and the resulting state change or a way to derive it |
| **Operational** | Can the same change be performed again? | forensic, plus the version of the code and policy that performed it, and inputs that are still obtainable |
| **Deterministic** | Does re-running produce the same state? | operational, plus all sources of nondeterminism (time, randomness, ordering, external responses) captured or controlled |

Each level includes the one above it. Deterministic replay is the most expensive and is
not the default expectation; most mutations should owe Audit or Forensic.

### 3. A mutation declares its level, alongside its effect

The level is declared with the mutation's classification (ADR 0007), in the same place the
effect is declared, so a reviewer can see the promise and a validator can check that the
required evidence exists. Defaults by category are a proposal, not a requirement of this
ADR:

| Category (ADR 0007) | Default level |
|---|---|
| Durable graph and evidence mutation | Forensic |
| Runtime control (decay, tuning, hardware) | Audit |
| Ephemeral analysis | declared non-replayable, with reason |
| Training data, external state | Forensic, since the effect leaves the system |

"Declared non-replayable" is an allowed answer and is recorded. It is not the same as
omitted.

### 4. The evidence a level needs is a property list, not a schema

For any declared level, the following must be recoverable. Where they are stored is
implementation.

- the action and its original parameters (redacted where secret)
- the principal, the proposal, and the approval decision with its basis
- the effect declaration in force
- the software version and deployment that executed it
- the policy version that approved it
- references to artifacts the action consumed (for example captures), by stable identity
- the outcome, including partial outcomes (see ADR 0007, partial-failure)
- a link between the control record and the state-changing commit, in both directions

Policy version and the approval basis are not recorded durably today; naming them here
makes the gap visible, not urgent.

### 5. The records must be joinable

The invariant is not met by two complete stores that cannot be connected. A mutation's
control record and its commit must share an identifier that a later reader can follow
without guessing. Which identifier is implementation; that one exists is the requirement.

## Out of scope

- **Storage, retention, and hash chaining of the audit.** Tamper-evidence beyond the
  existing append-only rule is a separate decision (#219).
- **Which level each tool owes.** The table in §3 is a default. The per-tool declaration is
  made with the ADR 0007 classification.
- **Partial-failure representation.** The outcome property in §4 depends on it. It is its
  own decision.
- **Making the graph itself reconstructible from the log.** That is event sourcing as an
  architecture, not a requirement here.

## Alternatives considered

- **Require deterministic replay of everything.** Rejected: it forces capturing external
  responses and time for mutations that need only an accountable record, and it would be
  claimed rather than achieved.
- **Leave "replayable" undefined.** Rejected: the word will be read as the strongest level
  by one person and the weakest by another, and neither will be checkable.
- **One global level for all mutations.** Rejected: it either over-burdens control actions
  or under-serves evidence.

## Consequences if accepted

- A reviewer can ask of a mutation: which level, and where is the evidence for it?
- Two gaps become explicit rather than implicit: no durable policy and approval basis, and
  no join between the control audit and a WriteBus commit.
- Ephemeral state can be honestly non-replayable instead of pretending otherwise.
- A validator could later check that a mutation which declares Forensic has the evidence
  properties above. That is implementation and is not committed to here.
- Nothing changes in behavior until a declaration mechanism is built.

## Not decided here

| Decision | Issue |
|---|---|
| The level each mutating tool and route owes | #218 |
| The mechanism joining control audit and commit | #218, #219 |
| Durable record of the approval basis and policy version | #228, #219 |
| Partial-failure representation | #218 |
