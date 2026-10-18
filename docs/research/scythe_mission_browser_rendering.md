# SCYTHE Mission — browser rendering (draft, architectural convergence)

> **DRAFT. No code changes, no Cesium/JS file touched, no server wiring made.** Nothing here is
> verified against a running browser — this environment cannot run one, and that limitation is
> stated rather than worked around. This document exists to close one gap named explicitly in
> the local invariants crosswalk after PR #193 ("Mission identity reflected in browser rendering:
> not yet demonstrated") — but researching it surfaced a larger prerequisite problem than
> "which Cesium file to edit," described in §1–§3 before any rendering design is proposed.

## 1. A second, pre-existing Mission system was found, and it is more mature than PRs #188–#193

Before this document, "Mission" in this repository meant `scythe_mission.py` (PR #188) and its
threading through `graphops_rf_ingest.py` (#189), `pcap_ingest.py` (#190), `HypergraphEngine`
(#192), and `graphops_graph_resolver.py` (#193) — call this the **domain Mission**. Searching
`rf_scythe_api_server.py` (21,042 lines) while researching this document found a second,
independent, already-operational system — call it the **operational Mission**:

| | Operational Mission (`rf_scythe_api_server.py`) | Domain Mission (`scythe_mission.py` + PRs #188–#193) |
|---|---|---|
| Storage | DB-backed (`_save_mission_to_db`/`_load_mission_from_db`) | In-memory Python objects; no persistence layer yet |
| Schema | Loose: `mission_id`, `name`, `owner`, `status` (`open`/`closed`), free-form `metadata` | Strict: typed `mission_type` enum, `MissionParameters`, frozen identity, canonical digest |
| Lifecycle | `POST /api/missions` (create), `GET`/`PATCH /api/missions/<id>`, `POST .../end`, `.../join`, `.../leave`, `GET .../operators`, `.../tasks`, `.../watchlist` | Construct-once, immutable; no server/API lifecycle of its own yet beyond what PR #193 added |
| Node/edge tagging convention | **camelCase** `missionId`, in `labels` and/or `metadata` | **snake_case** `mission_id`, in `labels` and `metadata.provenance` |
| Live graph access | `GET /api/missions/<id>/subgraph` (bounded snapshot); `GET /api/missions/<id>/diff/stream` (SSE, scoped diffs via `SubgraphDiffGenerator`/`QueryPredicate`) | `GET /api/graphops/selection/graph?mission_id=...` (bounded snapshot only, no streaming) |
| Multi-user / collaboration | Yes — operators join/leave, session tokens, tasks, watchlists | No concept of this |

Neither system was built with the other in mind. This document's first job is naming that
plainly rather than silently picking a side or quietly merging them.

## 2. The naming mismatch is a real, verified gap — not hypothetical

`rf_scythe_api_server.py`'s mission-scoped predicates (`/api/missions/<id>/subgraph`, lines
~5488–5556; `/api/missions/<id>/diff/stream`, lines ~5557–5708) check exactly:

```python
labels.get('missionId') == mission_id
node_or_edge.get('metadata', {}).get('missionId') == mission_id
```

`pcap_ingest.py`'s `_mission_label()` (PR D, #192) and `_provenance()` (PR C, #190) write exactly:

```python
labels["mission_id"] = config.mission_id
metadata["provenance"]["mission_id"] = config.mission_id
```

These do not match. **A node that PR C/D labeled with `mission_id` is invisible to the existing,
already-live `/api/missions/<id>/subgraph` and `/diff/stream` endpoints**, and a node tagged by
whatever already writes `missionId` (several call sites deeper in `rf_scythe_api_server.py`, e.g.
around lines 11193–15314, which inconsistently accept *either* spelling depending on the call
site) would not be found by `HypergraphEngine.nodes_by_mission()` or
`graphops_graph_resolver.py`'s `mission_id` filter (PR #193). Two real systems, two real
conventions, zero overlap today. This was found by reading both sides directly, not inferred.

## 3. The Cesium globe itself has no mission-awareness at all, on either side

`cesium-hypergraph-globe.js` (6,109 lines) and `cesium-hypergraph-globe.html` (4,355 lines) were
searched for `mission` (case-insensitive): no match beyond an unrelated `CLUSTER` visual-effect
name. Confirmed separately: this file never calls `/api/graphops/selection/graph` (PR #193's
endpoint) or anything under `/api/missions/*` (the operational system's endpoints) today. Its real
graph transport is Socket.IO with an SSE fallback (`/api/graphops/stream`, `EventSource`, around
line 3132) feeding a generic node/edge rendering pipeline, plus a separate WebSocket
subscription protocol in `edge_streaming.py` (`subscribe_edges`/`unsubscribe_edges`, a typed
`scope: {"type": "cluster" | "node", "id": ..., ...}` request). Neither transport has ever carried
a mission concept. So "browser rendering of Mission" is not a small gap in an otherwise-finished
pipeline — it is three unconnected pieces: an operational backend, a domain backend, and a
frontend that knows about neither.

## 4. Open question this document does not resolve: one Mission concept, or two?

- **Option A — reconcile into one.** Pick one casing (likely `mission_id`, since four of the
  five domain-side modules already use it and it is the newer, actively-developed convention),
  update the operational system's predicates to also accept it, and treat `name`/`owner`/`status`/
  `tasks`/`watchlist` as fields that belong on top of the domain `Mission` type rather than a
  separate record.
- **Option B — keep two concepts, linked by a shared identifier.** The operational Mission is a
  *collaboration session* (who's online, what they're doing right now); the domain Mission is a
  *provenance record* (what was collected, under what intent, with what identity guarantees).
  They would reference each other by ID rather than merge.
- **Option C — do nothing yet.** Leave both as they are; a browser rendering feature built on
  top of the operational system's already-working `/diff/stream` would work today for
  *operationally* tagged nodes, independent of whether the domain-side mismatch is ever fixed.

This document does not pick one. Each has real cost: A is an identifier migration across two
modules with already-shipped tests; B adds a second shared-identifier relationship to track; C
means PRs #188–#193's work stays invisible to the one browser-facing mission feature that
already exists until someone decides.

## 5. What browser rendering could look like (draft; ordered by risk, not by ambition)

**5.1 — Lowest risk: client-side filtering against the existing stream, no server change.**
The browser already receives unfiltered node/edge events over Socket.IO/SSE
(`/api/graphops/stream`). A client-side filter — "only render nodes whose `labels.missionId` (or,
once reconciled, `mission_id`) equals the selected mission" — needs zero server changes, works
today against the operational system's existing tagging, and can be built and tested independent
of §4's identifier question. Cost: no bandwidth savings (every node still reaches the browser);
value: proves the rendering concept cheaply.

**5.2 — Medium: a `GET /api/missions/<id>/subgraph`-backed initial view.** The operational
system's bounded-snapshot endpoint already exists and already works for `missionId`-tagged
nodes. A "jump to mission" interaction could call it once for an initial bounded set, then layer
5.1's client-side filter on top of the live stream for updates. No new backend surface.

**5.3 — Higher cost, not recommended yet: a new WebSocket mission scope.** `edge_streaming.py`'s
`EdgeScope` currently dispatches on `scope_type` ("cluster" or "node") using only the edge's own
`nodes` list — no node-label lookup is needed for either existing type. A `"mission"` scope would
need to look up each edge member node's `labels` via the engine to decide inclusion, which is a
materially different (and more expensive, if done per-edge-per-tick) filter than the existing two.
Worth doing eventually for real bandwidth savings at scale; not worth doing before §4 is decided,
since it would have to pick a casing convention to key the lookup on.

**Recommendation, not a decision:** start with 5.1. It needs no resolution of §4, no new server
code, and would be the first thing in this entire series actually exercised in a browser — which
nothing before this document has been.

## 6. What this draft does not do

No Cesium/JS file is edited. No new WebSocket scope type is implemented. No identifier
reconciliation is performed or chosen. Nothing here was checked against a running browser or dev
server — stated plainly rather than implied otherwise. No Gate C/Gate D protocol is touched; GATED3
remains frozen and unauthorized.

## 7. Open questions

- Which of Options A/B/C (§4) is correct — this is a product/architecture decision, not something
  derivable from the code alone.
- If A: does the operational system's looser schema (free-form `metadata`, `open`/`closed`
  status) get absorbed into `MissionParameters`, or stay a separate overlay?
- Whether RF-ingested observations (still unlabeled at all, per PR B's known gap) should ever be
  reachable through a mission-filtered browser view, or whether that gap needs closing first.
- Real performance characteristics of a node-label-aware edge scope (§5.3) at the node/edge counts
  this engine actually carries in practice — not measured here.
- Whether the operational system's multi-operator session concept (join/leave, tasks, watchlist)
  has any place in a provenance-oriented "Mission" at all, or whether it's a UI/collaboration
  layer that should stay separate regardless of how §4 resolves.
