1. Make effect declarations mandatory (highest value)
Right now you've added explicit effects for reachable mutating tools and a regression test.
I'd eventually strengthen that into a startup invariant:
Every registered tool must explicitly declare an effect.

Not just mutating ones.
For example:
READ_ONLY
LOCAL_STATE
EXTERNAL_STATE
TRAINING_DATA
HARDWARE_CONTROL
AUDIT_ONLY

Inference was a great migration aid, but I'd aim to eliminate it over time.
2. Replace mutates_state with derived semantics
Today you have two pieces of metadata:
mutates_state
effect

Those can drift.
Eventually I'd rather have something like
effect = Effect.LOCAL_STATE


and derive
mutates_state = effect != READ_ONLY


or
effect.is_mutating()


One source of truth is easier to maintain.
3. Capability advertisement
This ties into #217.
Instead of just exposing tool schemas, expose something like:
{
  "tool": "decay_now",
  "effect": "LOCAL_STATE",
  "approval": "required",
  "implemented": true,
  "writebus": false
}

An autonomous agent could plan correctly without probing behavior.
4. Tool versioning
Schemas evolve.
Eventually it may be useful to attach
tool_version

or
api_revision

to each registered tool.
That makes audit replay much easier.
5. Approval reason codes
Instead of free-form strings like
approved because...

I'd eventually standardize them.
Example:
APPROVED_EFFECT_POLICY

DENIED_PHASE

DENIED_CAPABILITY

DENIED_APPROVAL

DENIED_BUDGET

Machines can consume those much more reliably.
6. Idempotence metadata
This one is surprisingly useful.
Some tools can safely run twice.
Some absolutely cannot.
Example metadata:
idempotent=True

or

idempotent=False

An orchestrator can safely retry the first category after transient failures.
7. Preconditions
Closely related.
Instead of discovering failures by execution:
requires:
    scope_exists
    hardware_connected
    proposal_present

That gives planners much better information.
8. Transaction grouping
Eventually you may want
proposal
    ↓
tool A
tool B
tool C

to produce one audit transaction rather than unrelated rows.
Not urgent, but worth keeping in mind.
9. Mutation budget visibility
You discovered the Phase-2 mutation budget almost by accident.
I'd expose it.
For example
remaining_mutation_budget

or
mutation_budget_remaining

Agents can then avoid proposing work that cannot execute.
10. Stable tool IDs
Today names appear to be the identifiers.
If names ever change, historical audit interpretation becomes harder.
An immutable identifier alongside the display name would future-proof things.
What I would not change
I would resist adding:
- more approval tiers,
- more effect categories,
- more policy knobs,
until there's a concrete use case.
The current taxonomy is compact and understandable.
Overall assessment
The MCP layer has evolved considerably. It started as a collection of callable functions with relatively loose contracts. After this series of changes, it's beginning to resemble a governed control plane:
- explicit effect taxonomy,
- policy-driven authorization,
- principal propagation,
- durable audit,
- proposal linkage,
- registry consistency,
- regression tests enforcing architectural invariants.
The remaining work is mostly refinement rather than foundational repair. The biggest architectural item still unresolved, in my view, is the mutation boundary: deciding which operations are intended to be local engine mutations, which must go through WriteBus, and which should never bypass it under strict_no_bypass. Once that boundary is explicit, the control plane will have a much clearer contract for both human operators and autonomous GraphOps agents.