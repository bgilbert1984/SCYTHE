# GraphFusion Phase Zero Implementation

Status: implemented behind a server-owned ablation mode

GraphFusion turns one GraphOps question into one deterministic evidence transaction before model interpretation.

## Invariant

```text
ONE QUESTION
  = ONE SELECTION-AWARE PINNED GRAPH VIEW
  + ONE PINNED DSL EXECUTOR
  + ONE SERVER-OWNED RETRIEVAL POLICY
  + ONE DETERMINISTIC HYPEREDGE-AWARE TRAVERSAL
  + ZERO MODEL-CREATED EVIDENCE
```

## Provenance identities

Every fused local investigation now distinguishes:

- `G`, the complete normalized graph revision;
- `P`, the exact bounded, selection-aware projection available to the investigation;
- `T`, the deterministic traversal over that projection;
- model and model route, which remain interpretive only.

Cloud capsule identity `C` remains separate. Phase Zero does not recompute or disclose GraphFusion paths through the Full-Fidelity Cloud route. A later phase may carry the child-produced traversal artifact across that boundary after relational lift is demonstrated.

## Transaction boundary

`GraphSelectionResolver.pin_selection()` captures the current graph once, or retrieves a retained historical projection. The selected node or hyperedge is forced into the bounded projection before remaining capacity is adaptively filled.

The resulting `PinnedGraphView` contains detected and retained counts, the complete graph revision, projection hash, selection-rebase status and canonical node/edge payloads. Its `PinnedGraphEngine` adapter supplies the read interface used by the existing GraphOps DSL. Consequently, model-directed `FOCUS`, `EXPAND`, `TRACE` and related read operations cannot drift into a newer graph during the same investigation.

## Retrieval

The Phase Zero traversal is deliberately small:

- maximum depth: 2 graph hops;
- maximum visited nodes: 96;
- maximum inspected edges: 160;
- maximum candidate paths: 48;
- maximum admitted paths: 8;
- synthetic evidence excluded by default;
- display-only evidence blocked;
- hyperedges retained as explicit path steps;
- semantic results treated as transient search leads, never graph relationships.

Initial deterministic roles are limited to claims the current schema can establish:

- `RELATIONAL_SUPPORT`;
- `EXPLICIT_CONTRADICTION`;
- `TEMPORAL_CONTEXT` when an explicit change relation exists;
- `DIVERGENT_BRANCH` for admitted semantic-seeded branches.

Paths and receipts do not establish causality.

## Ablation modes

The child server owns the mode through `SCYTHE_GRAPHOPS_RETRIEVAL_MODE`:

| Mode | Graph transaction | Mandatory traversal | Semantic retrieval |
| --- | --- | --- | --- |
| `legacy` | live legacy behavior | no | legacy prose RAG |
| `pinned_legacy` | pinned | no | legacy prose RAG |
| `pinned_graph` | pinned | yes | disabled |
| `pinned_fused` | pinned | yes | structured seeds |

Aliases `baseline`, `graph` and `fused` map to `legacy`, `pinned_graph` and `pinned_fused`. The default is `pinned_fused`.

The four modes separate the benefit of transactional consistency from the benefit of mandatory retrieval. The legacy baseline already permits model-directed graph DSL operations, so GraphFusion is evaluated as deterministic pre-model retrieval rather than “graph versus no graph.”

In `pinned_graph` and `pinned_fused`, model-directed `VECTOR_SEARCH` and `CLUSTER_SIMILAR` verbs are refused. Graph DSL reads remain available against P, but semantic candidates can enter the fused transaction only through the structured, server-owned seed provider. This keeps the graph-only ablation free of hidden semantic retrieval and prevents the model from widening fused semantic scope after T is hashed.

## Response contract

Local conversation responses carry a backward-compatible `retrieval` object containing:

- complete detected graph counts;
- projection limits, retained counts, truncation and `projectionHash`;
- traversal budgets and inspected/admitted counts;
- structured semantic seed resolution;
- admitted path steps, roles and evidence;
- `traversalHash` and epistemic boundary.

SCYTHE-Web renders this as a Traversal Receipt in the existing GraphOps investigation tab. The browser still submits only the operator question and pinned selection reference; it cannot provide or mutate retrieval evidence.

## Gate for the next phase

Do not introduce assertion persistence, bitemporal edges, entity auto-merge, an evidence-dependency graph or Cloud path disclosure until `pinned_fused` repeatedly demonstrates novel, relevant and correctly scoped relational evidence beyond the existing baseline.

Benchmark evidence sets before prose:

- relevant relationships recovered;
- explicit contradictions surfaced;
- unsupported evidentiary promotions;
- phrasing-invariant path constituents;
- retrieval latency and context size;
- stability of `P` and `T` for identical inputs.

If relational lift is absent, tune or remove GraphFusion rather than expanding its storage architecture.
