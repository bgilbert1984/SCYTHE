# ADR 0007 — Every authoritative mutation has exactly one documented boundary

- **Status:** Proposed. Nothing here is decided or implemented. The classification and
  the choice of boundary are for the owner to accept, change or reject.
- **Date:** 2026-10-20
- **Resolves:** the mutation-boundary question in #218. Replayability and partial-failure
  semantics are named here and left to their own decisions (see
  [Adjacent questions](#adjacent-questions)).
- **Builds on:** [ADR 0004](0004-mcp-control-plane.md) (effects), [ADR 0005](0005-registry-tooldef-execution-model.md)
  (execution semantics belong to the control plane), [ADR 0006](0006-mcp-approval-policy.md) (approval).
- **Related:** #213, #218, #236, `docs/MCP_Tools.md`

## Context

The control-plane ADRs say who may invoke a tool, how it is approved, and how the call is
audited. They do not say **where the state change itself must happen**. That is the
mutation boundary, and today the repository has more than one answer.

### What exists

- `WriteBus` is described as "the only sanctioned writer that touches both
  persistence/broadcast AND the hypergraph" (`writebus.py`). The live server constructs it
  with `strict_no_bypass=True` (`rf_scythe_api_server.py`).
- `strict_no_bypass` is narrower than its name. It wraps a **fixed list of methods on two
  objects** so they raise `WriteBusKernelViolation` outside `WriteBus.commit()`: on the
  hypergraph engine, `apply_graph_event`, `add_node`, `update_node`, `remove_node`,
  `add_edge`, `remove_edge`, the three `add_geo_*` methods, `add_node_from_rf` and
  `add_edge_from_rf`; on the operator manager, `publish_to_room`, `delete_from_room`,
  `broadcast_entity_event`. It also removes the graph-event bus subscriber that applies
  events to the engine. It guarantees nothing about any other store, other engine methods,
  or state outside those two objects.
- The MCP mutation tools were never classified against this boundary (#218).
- Other control actions (RF retune, `/api/sdr/*`) change state that is not the graph at
  all and have their own durable audit (`control_audit.py`), deliberately separate from
  WriteBus (ADR 0004 §4).

### What running the combination shows

An in-process probe (a real `HypergraphEngine` guarded by a real `WriteBus` with
`strict_no_bypass=True`, a stub operator manager, and the registry tools as served; not the
live server, no network) was run against the MCP mutation tools that reach a guarded
method. The live server passes the same global engine to WriteBus and to `/mcp`
(static read, not exercised there).

| Tool | Without the guard | With `strict_no_bypass=True` |
|---|---|---|
| `ingest_live_event` (1 queued event) | `{committed: 1, requested: 5}`, node created | `{committed: 0, requested: 5}`, **no node**, event already dequeued |
| `decay_now` (one stale edge) | `{edges_pruned: 1}` | raises `WriteBusKernelViolation` from `remove_edge` |

So under the production setting, these two approved, audited, "successful" tool calls
either do nothing or fail. `ingest_live_event` is the sharper case: its per-event
`except Exception: continue` swallows the violation, the response looks like a normal
partial commit, and the events have already been removed from the queue. Nobody is told
the boundary rejected them.

Two further observations from the same read, recorded because they bear on what "the
boundary" covers, not as part of this decision: `reinforce_edge` and `prune_below_weight`
handlers probe for engine methods that `HypergraphEngine` does not define (so they report
failure or zero, #220), and `decay_now`'s `lambda` argument is accepted by
`decay_edges` but not used in the persistence computation, which reads the engine's own
`decay_lambda`.

None of this is evidence of an exploit. It is evidence that the boundary and the
control plane were built separately and have not been reconciled.

## Decision (proposed)

### 1. The invariant

**Every authoritative mutation has exactly one documented mutation boundary.**

A mutation is *authoritative* when other components are entitled to treat its result as
the system's state of record, as opposed to scratch, cache or per-request state. The
boundary is the single place such a change is made and from which it is attributable. The
ADR does not choose the mechanism: today's boundary is WriteBus for the graph, and the
control audit for non-graph control actions. If WriteBus were replaced, the invariant would
still hold.

### 2. Classification is required, and has four outcomes

Each mutating tool, and each non-MCP route that reaches the same state (#236), is
classified by what it changes, using the categories already in #218:

| Category | Boundary |
|---|---|
| Durable graph mutation, evidence mutation | the graph boundary (WriteBus today); evidence also carries provenance |
| Runtime control (decay, tuning, config, hardware) | an explicit, documented control path |
| Ephemeral analysis (scope, cache) | a documented exception, not a bypass |
| Training data, external state | a documented path to the external system, with the audit already required by ADR 0004 |

"Documented" means recorded where a reviewer will look (`docs/MCP_Tools.md` or the tool
itself) with the reason. The test for an exception is whether the state is authoritative,
not whether the code calls an engine method.

### 3. A guard without a classification is a defect in the guard

A mutation that the production guard rejects, yet that is approved and audited as
successful, is a contradiction between two layers. The resolution is a classification
decision per tool, in one of two directions, never silence:

- the mutation is authoritative graph state, so the tool routes through the boundary; or
- it is runtime control with a different documented boundary, so the engine operation it
  performs is reachable by that path in the guarded configuration.

Which direction applies to `ingest_live_event` and `decay_now` is the owner's decision.
This ADR does not make it.

### 4. `strict_no_bypass` is described by what it does

Documentation should say that it guards the listed methods on two objects and nothing
else, and the guarded list should be treated as part of the contract: a new engine
mutation method that is not in the list is outside the guarantee until added. Whether to
make the guard cover more is a separate, later decision.

### 5. A test pins the combination

The probe above is the shape of a regression test: build the guarded engine, run each
reachable mutating tool, and assert the outcome is one the classification allows
(performed through the boundary, or documented as outside it). Without it, tool and guard
can drift apart again with no signal. This is implementation, noted so the invariant is
checkable and not only stated.

## Adjacent questions

Deliberately not decided here. Each can be its own ADR if it grows.

- **Replayability.** "Replayable" names several different guarantees: deterministic replay,
  operational replay, audit replay and forensic reconstruction. Which of these a given
  mutation owes, and the evidence it must retain (parameters, software version,
  deployment, proposal, principal, policy version, effect declaration, referenced
  artifacts), is open. The bar in #218 stays: explainability appropriate to the mutation.
- **Partial-failure semantics.** The architectural question is how a partially applied
  mutation is represented. Either mutations are atomic, or partial application is
  explicit in the result and the audit. A failure must not be silently discarded. The
  `ingest_live_event` finding is the first concrete case; the principle should hold for
  future handlers.

## Alternatives considered

- **Route every mutating tool through WriteBus.** Rejected as a blanket rule: RF retune
  and scope state are not graph mutations, and ADR 0004 already separated their audit
  from WriteBus for that reason.
- **Relax `strict_no_bypass`.** Rejected here as the answer to a tool's classification.
  Loosening a guard to make an unclassified tool pass removes the signal that found the
  problem.
- **Do nothing until a tool is reported broken.** The finding above is the cost of
  that: two tools were approved, audited and reported as working while the configuration
  the server ships with prevents them.

## Consequences if accepted

- A reviewer can ask of any mutation: which boundary, and where is that written down?
- The two tools above get a classification before any code change, and the code change
  follows from it.
- `strict_no_bypass` stops being read as "no mutation bypasses WriteBus", which it does not
  guarantee.
- The decision on #236's HTTP routes has somewhere to land: they are classified like tools.

## Not decided here

| Decision | Issue |
|---|---|
| Boundary for `ingest_live_event`, `decay_now`, and the other mutating tools | #218 |
| Whether the guard's list grows | #218 |
| Replayability requirements | #218 |
| Partial-failure representation | #218 |
| Whether the direct HTTP routes join the control plane | #236 |
