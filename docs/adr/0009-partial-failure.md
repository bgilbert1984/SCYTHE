# ADR 0009 — A partially applied mutation is represented, never silently discarded

- **Status:** Proposed. Nothing here is decided or implemented. The vocabulary and the
  rule are for the owner to accept, change or reject.
- **Date:** 2026-10-20
- **Resolves:** the `ingest_live_event` item in #218, generalized to all mutations.
- **Builds on:** [ADR 0004](0004-mcp-control-plane.md) (audit), [ADR 0007](0007-mutation-boundary.md)
  (one boundary per mutation), [ADR 0008](0008-mutation-replayability.md) (the outcome
  evidence property).
- **Related:** #218, #219, #220, `docs/MCP_Tools.md`

## Context

A mutation can do part of its work. A batch applies some items and not others. A commit
changes the graph but fails to publish. A handler is asked for something its engine cannot
do. The question is not any one handler: it is how the system says "this was applied
partly", so that the caller, the audit and a later reader agree on what happened.

Two representations exist today, and they are not alike.

**WriteBus has a partial-failure model.** A commit moves through recorded statuses, one of
which is `FAILED_PARTIAL`. The commit envelope records which steps completed
(`graph_applied`, `persisted`, `bus_published`, `audited`, `mutation_started`). A partial
failure emits a repair task naming the steps that still need repair and writes a dead-letter
record (`handle_partial_failure`, `writebus.py`). This is a worked-out answer for the graph
boundary.

**The MCP execution path does not.** From a static read:

- The control audit has `started`, then `success` or `error`. A handler that returns
  normally is `success`, whatever the return value says. A `started` row with no terminal
  row means the process died mid-call.
- A handler signals problems three different ways: it raises; it returns an error-shaped
  value (`{"ok": False, "error": ...}`, as `reinforce_edge` and `get_edge` do); or it
  returns a count that happens to be short (`{"committed": 0, "requested": 5}`). Only the
  first is audited as an error.
- `ingest_live_event` swallows every per-event exception and continues. In the ADR 0007
  probe, a boundary violation was swallowed this way, the response looked like an ordinary
  partial commit, and the events had already left the queue.
- A tool whose backing method is missing can return success with a zero count (#220).

So the same word, "success", covers: everything applied, nothing applied, and some applied.

## Decision (proposed)

### 1. The invariant

**A mutation either applies completely, or its partial application is explicit in its
result and in its audit. It is never silently discarded.**

Completeness and explicitness are the two permitted shapes. A third state, where some work
was lost and the record says success, is not permitted.

### 2. Outcomes are named, and "success" means complete

A mutation's recorded outcome is one of:

| Outcome | Meaning |
|---|---|
| `success` | every requested change was applied |
| `partial` | some were applied and some were not; the result says which |
| `rejected` | nothing was applied because a precondition or a boundary refused it |
| `error` | the mutation failed unexpectedly; the extent of any change is stated if known |
| `unknown` | the outcome cannot be established (for example a `started` row with no terminal row) |

Control-audit `success` and `error` are today's subset. Adding `partial`, `rejected` and
`unknown` is the proposed extension; how they are stored is implementation.

### 3. The result states what was and was not applied

A `partial` or `rejected` result carries enough to act on: the count requested, applied
and not applied, and for each unapplied item a reason or a reference to where its failure
is recorded. A count short of the request, with no reason, does not satisfy this.

### 4. Failures that lose input are the serious case

If work is consumed before it is applied (a queue dequeue, a claimed job), a failure to
apply must leave the input recoverable or recorded: returned to the queue, written to a
dead-letter record, or listed in the result. Consuming an input and then discarding it on
error is the case the invariant exists for.

### 5. The audit outcome follows the result, not the return

A handler returning without raising is not by itself `success`. The outcome recorded in the
audit is derived from the result the handler reports under §2. A handler that reports
nothing about completeness is, at most, complete only if the tool states that it is
all-or-nothing.

### 6. WriteBus's model is the reference, not a requirement

The graph boundary already satisfies this invariant by its own mechanism: recorded step
state, a repair task and a dead-letter record. The MCP layer should reuse the same ideas
(per-step state, a repair or dead-letter record for lost input) rather than inventing a
second vocabulary. Whether the two are unified, or the MCP layer only maps onto the
WriteBus statuses where a tool commits through it, is left to implementation.

### 7. Atomicity is a legitimate choice

A tool may instead be all-or-nothing, rolling back or validating before it changes
anything. It then needs only `success`, `rejected` or `error`, and should say it is atomic.
The ADR does not prefer one over the other; it requires that the tool's choice be
declared.

## Out of scope

- **Retry, backoff and recovery policy.** This ADR requires failures to be representable,
  not that anything retries.
- **Fail-closed audit when the audit write itself fails** (#219).
- **Whether `ingest_live_event` should keep per-event isolation.** That is a decision about
  that tool, taken against this invariant, with its boundary classification (ADR 0007).
- **Capability absence** (a tool whose backing method does not exist) is #220. It matters
  here only because the current symptom is "success" with a zero count.

## Alternatives considered

- **Make every mutation atomic.** Rejected as a rule: batch ingest and multi-step commits
  are partial by nature, and forcing atomicity either blocks useful work or hides the
  partial state inside a rollback that cannot always be done.
- **Keep success and error, and rely on the result body.** Rejected: that is today's state,
  and it is how a short count passes as success. The audit is the record a later reader
  trusts.
- **Treat any short count as an error.** Rejected: it discards the distinction between
  "some applied" and "none applied", which is the information the invariant protects.

## Consequences if accepted

- "Success" in the audit regains a precise meaning, and a reader can trust it.
- Handlers that signal failure by return value or by silent skipping become visibly
  non-conforming. The change is per handler, after the boundary classification.
- The audit outcome set grows, and consumers that match on `success`/`error` need to
  tolerate the new values.
- Unconsumed or lost work becomes findable (a dead-letter or repair record) instead of
  vanishing.
- No behavior changes until an outcome mechanism is built.

## Not decided here

| Decision | Issue |
|---|---|
| Each mutating tool's atomic-or-partial declaration | #218 |
| Whether `ingest_live_event` keeps per-event isolation | #218 |
| How the new outcomes are stored in the control audit | #218, #219 |
| Unification with WriteBus statuses | #218 |
