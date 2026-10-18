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

## 4. Decided: Option B — two concepts, linked by a shared identifier namespace

**Resolved, not left open.** PR #195 reconciled the `mission_id`/`missionId` identifier split
(§2) by making reads on the operational side accept both conventions and writes dual-write both —
a prerequisite regardless of which option below was chosen. A follow-up PR then picked **Option
B** over A: `scythe_mission.Mission` is deliberately frozen/immutable (PRs #188, #191); folding
the operational system's mutable lifecycle (`join`/`leave`/`status`/`tasks`/`watchlist`) into it
would mean either breaking that invariant or building a parallel mutable wrapper — a materially
larger redesign than linking two already-working systems. The two concepts stay separate:

- The operational Mission (`rf_scythe_api_server.py`) is the *collaboration session* — who's
  online, tasks, watchlist, live diff stream.
- The domain Mission (`scythe_mission.py`) is the *provenance record* — intent, identity,
  immutability guarantees.
- They share one identifier **namespace**, not a merged schema: `POST /api/missions` now rejects
  an explicitly supplied `mission_id` that would not be constructible as a domain
  `scythe_mission.Mission` (validated via the same `is_valid_mission_id()` PR B/#189 already
  introduced for `graphops_rf_ingest.py`). An auto-generated operational `mission_id` already
  satisfies that rule by construction. This guarantees that if a domain `Mission` object is ever
  constructed to describe the same real-world mission as an existing operational record, nothing
  about the identifier shape stops it — without requiring that pairing to actually happen for
  every mission, or merging either schema into the other.

**What this does not do:** it does not require every operational mission to have a corresponding
domain `Mission` object, or vice versa — the link is a compatibility guarantee, not an
enforced 1:1 pairing. `tasks`/`watchlist`/`join`/`leave` remain entirely on the operational side.
No code reads a domain `Mission` object to populate an operational mission record, or the
reverse. Whether a future feature ever needs that live cross-reference is left to when a real
feature actually needs it, consistent with this series' standing discipline of not building
ahead of demonstrated need.

## 4-prior. Original framing (superseded above, kept for history)

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
**Revised after verification — see section 7:** the premise that "the browser already receives
unfiltered node/edge events over Socket.IO" does not hold for the generic hypergraph-node path
specifically (that listener has no live server-side emitter). The pure matching logic itself
(`cesium-mission-filter.js`) was still built and genuinely tested — see section 7 — but it is
**not wired into any rendering pipeline**, because there is no verified-live pipeline to wire it
into yet. What was originally planned as "step 1, done" is actually "step 1a (the matching
primitive) done; step 1b (wiring it to something that fires) blocked on section 7's gap."

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

## 6. Host-dedup mission-attribution semantics (decided)

**The question.** `pcap_ingest.py` deduplicates host nodes by IP across the whole engine
(`_host_id(ip)` is purely IP-derived). Before this section, a host observed under a second
mission kept only the first mission's label — a real, previously-recorded limitation (§3 of the
original review that led to this document). Decided here, before writing the fix, per the
recommendation that these semantics matter more than the implementation:

- **Can one host participate in multiple missions? Yes.** A host is shared network
  infrastructure, not something a mission owns. Two unrelated captures observing the same IP
  address is normal, expected, and already happens in this pre-existing dedup design — the bug
  was losing that fact, not the fact itself.
- **Is attribution temporal? Append-only, not latest-wins, not expiring.** Once a mission has
  observed a host, that fact is never removed. A later mission observing the same host *adds* to
  the record; it never replaces or ages out an earlier one. This matches the project's established
  evidence philosophy (`scythe_architectural_invariants.md` §6: "evidence is immutable; derived
  products reference it, they do not replace it") applied to attribution instead of content.
- **Evidentiary, not authoritative.** `mission_id` on a host node means "this host was *observed*
  while collecting evidence under these missions," not "this host *belongs to* or *is controlled
  by* this mission." A host's mission list is a custody fact, not an ownership claim.
- **Observations and hosts are allowed to disagree, by design.** A session/`pcap_artifact` node
  belongs to exactly one mission (the one whose collection produced it) and keeps the existing
  **scalar** `mission_id` label. A host node can belong to many and uses a **list**. This is a
  deliberate type asymmetry between node kinds, not an inconsistency — documented explicitly so a
  future reader doesn't "fix" one into matching the other.
- **Mission merge/split: out of scope, not answered.** No mission merge/split feature exists
  anywhere in this codebase (operational or domain). Inventing an answer for a feature that
  doesn't exist would be exactly the kind of premature design this series has avoided elsewhere;
  left for whenever such a feature is actually proposed.

**Mechanism.** A host node's `labels.mission_id` is a list that only ever grows:
`record_observation`-style accumulation (`current + [mission_id]` if not already present), never
a replacement. `HypergraphEngine.nodes_by_mission()` needed **no change** — `label_index` already
indexes list-valued labels generically (confirmed directly in `hypergraph_engine.py`'s `add_node`/
`update_node`, which loop over `isinstance(v, (list, tuple, set))` labels). The two places that did
a direct `labels.get("mission_id") == mission_id` equality check against raw node dicts —
`graphops_graph_resolver.py`'s resolver filter (PR #193) and `rf_scythe_api_server.py`'s mission
predicates (PR #195) — needed a small fix to also match mission_id as a list member, not just an
exact scalar equal.

## 7. The generic hypergraph-node Socket.IO path is not demonstrably live (verified architectural gap)

**This is an observation, not an accusation or a TODO.** Found while starting the client-side
mission filter (section 5.1), independent of Mission work:

- `cesium-hypergraph-globe.js` listens for Socket.IO events named `entity_update` and
  `entity_delete`.
- The only server-side emitter found anywhere in this repository —
  `operator_session_manager.py`'s `self.websocket.emit('entity_event', event.to_dict())` — emits
  a **different event name** (`entity_event`, not `entity_update`), with a **different payload
  shape** (`{event_type, entity_id, entity_type, entity_data, provenance, timestamp}`, with the
  generic node data nested under `entity_data`, not flattened to top-level `lat`/`lon`/etc. the
  way `_applyNodeUpdate()` reads it).
- No emitter for the literal string `entity_update` exists anywhere in this codebase, in any
  language.

**Conclusion:** the generic hypergraph-node rendering path this document's section 5.1 assumed
was "the existing stream" is not demonstrably reachable by any live server code today. This is a
**precondition failure, not a Mission-filter problem** — two separate questions got conflated:
"does Mission-matching logic work?" (yes, verified — see `cesium-mission-filter.test.js`, 16/16
passing) and "does the Cesium client currently receive generic node updates?" (not through this
path, as far as this repository's own code shows).

**What this means for section 5's ordering.** Rewiring the listener to the real event name and
unwrapping its real payload shape is a materially different, larger, riskier change than "add a
filter to code that already runs" — it would be changing an apparently-inactive protocol path
into an active one, which is exactly the kind of change that looks correct in a diff and then
fails in ways only a running browser would reveal (coordinate extraction, clustering, deletion
semantics, performance under real event volume). That work is deferred, explicitly, until it can
be verified end-to-end against a running browser — not attempted here on static reasoning alone.
Static reasoning is sufficient for the matching primitive; it is not sufficient for a WebSocket
protocol correction.

**Also not investigated here:** whether the recon-entity, UAV, or RF-classification Socket.IO
channels (which do appear to have real emitters, per the file's other `_socket.on(...)` handlers)
could serve as an alternate integration point. Deliberately not pursued as a substitute here —
filtering one of those channels would demonstrate "Mission filtering works on an unrelated
stream," not "Mission filtering for the hypergraph renderer," which would be answering a
different question than the one this document set out to answer.

## 8. What this draft does not do

No `cesium-hypergraph-globe.js`/`.html` file is edited, and `cesium-mission-filter.js` is not
wired into its rendering pipeline — see section 7 for why. No new WebSocket scope type is
implemented. No identifier reconciliation is performed or chosen (that was §4/PR #195, already
done). Nothing here was checked against a running browser or dev server — stated plainly rather
than implied otherwise. No Gate C/Gate D protocol is touched; GATED3 remains frozen and
unauthorized.

## 9. Open questions

- §4 is resolved (Option B); the remaining questions are about what to build on top of that
  decision, not about the decision itself.
- §6 is resolved (host attribution is evidentiary, append-only, list-valued for hosts only).
- §7's gap (no live emitter for the `entity_update` the client listens for) blocks any further
  Cesium rendering work until either the client is rewired to the real `entity_event` shape, or
  a different, genuinely-live integration point is chosen — and verifying either needs a running
  browser this environment doesn't have.
- `RFObservation` (`rf_bridge.py`) now carries `mission_id` — PR B's storage gap is closed — but
  `rf_bridge.py` has no `HypergraphEngine`/`RFHypergraphAdapter` reference at all, so RF
  observations still never reach the graph this document's `/api/graphops/selection/graph`
  filter (§4/§6) queries, labeled or not. Whether RF observations should ever be bridged into
  the shared hypergraph (e.g. via the existing but unused `RFHypergraphAdapter`), and therefore
  become reachable through a mission-filtered browser view, is still open and undone.
- Real performance characteristics of a node-label-aware edge scope (§5.3) at the node/edge counts
  this engine actually carries in practice — not measured here.
- Whether the operational system's multi-operator session concept (join/leave, tasks, watchlist)
  should ever gain a live cross-reference to a domain `Mission` object describing the same
  real-world mission, or whether the identifier-namespace guarantee (§4) is sufficient on its own
  indefinitely — not decided, and not needed until a concrete feature asks for it.
