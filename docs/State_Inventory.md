# State inventory

A catalog of the places SCYTHE keeps state, written as supporting work for the mutation
boundary (ADR 0007), replayability (ADR 0008) and partial-failure (ADR 0009) decisions
tracked in #218. It decides nothing. It exists so that no store that holds authoritative
state is overlooked when those decisions are made.

## How to read this

**First pass, from a static read of the code.** Nothing was run on a live server. A row
says what the code shows; a blank or `?` means it was not established. The list is not
exhaustive: [Not yet inventoried](#not-yet-inventoried) names what was left out.

Two tables, kept apart on purpose:

- **Observed** is fact from the code: where the store is, whether it survives a restart,
  and who writes it.
- **Proposed classification** is judgment, and is for the owner to accept or change.

Terms used in the classification:

| Class | Meaning |
|---|---|
| Authoritative | other components are entitled to treat it as the record |
| Derived | rebuildable from an authoritative source |
| Transient | working state that is allowed to be lost on restart |
| Governance | records about actions, not about the world |

## Observed

| Store | Where | Survives restart? | Written by (as observed) |
|---|---|---|---|
| Hypergraph engine | `HypergraphEngine` in `hypergraph_engine.py`; in memory | only through `save_snapshot` / `load_snapshot` (the server loads one at startup if present) | `apply_graph_event` and the node/edge methods; guarded by WriteBus when `strict_no_bypass` is on; also by MCP tools and other routes |
| RF hypergraph store | `RFHypergraphStore` in `rf_scythe_api_server.py`; `nodes`, `hyperedges`; in memory | no ("In-memory storage") | server routes; not examined further |
| WriteBus state | `writebus_state.sqlite3`, or the operator DB path when the operator manager has one: idempotency, commit status and history, temporal context, dead-letter and repair records | yes | `WriteBus.commit` |
| Operator database | `operator_sessions.db`: operators, sessions, teams, rooms, room membership, `room_entities`, `entity_audit_log` | yes | operator manager; `room_entities` is the target of `WriteBus` room publishing |
| Control audit | `control_audit.sqlite3` under the data directory: `control_audit_events`, `control_audit_meta`; append-only, retention enforced by triggers | yes | `control_audit` via `Registry.execute` and `/api/sdr/*` |
| Proposal log | `DualAgentOrchestrator.decision_log`, a list; in memory | no | `propose_action`, `execute_proposal` |
| Recon system entities | `recon_system.entities` (and `.tasks`); in memory | no; rebuilt at startup by `rehydrate_recon_from_operator_db` from `room_entities` for `RECON_ENTITY`, `PCAP_HOST`, `NMAP_TARGET` | several routes; the live-ingest worker writes `RECON_ENTITY` records into it directly |
| Live-ingest queue | `live_ingest` deque, capped at 10,000; in memory | no | stream manager and `ws_ingest` enqueue; the server's live-ingest worker and `ingest_live_event` dequeue |
| RF solver evidence store | `RFSolverEvidenceStore`, a bounded deque (default 2,048); in memory | no | `ingest`; also called by `graphops_explain_coverage_cell` and its HTTP route |
| Authority database | `scythe_pg.py`: embedded Postgres through `pgserver`, with `scythe_authority.db` as the local SQLite path; `pcap_artifacts`, `sessions`, `bsg_groups`, `instance_state` | yes | mirrors of ingest results (`mirror_ingest_result`) |
| Server metrics and missions | `metrics.db` under the log directory: metrics, user interactions, missions, mission members, tasks, watchlist; plus satellites and registered queries | yes | server routes |
| Scene event log | `scene_events.db`: events, sessions, snapshots | yes | `SceneEventLog`; not examined further |
| Other local databases | `poi_database.db` (points of interest, KMZ sources), `map_cache.db` (arc, geo path, camera, node geo index), `runtime/graphops_ris_live.sqlite` (RIS observations), `new_hosts.db` (default path hard-coded under `/home/spectrcyde/NerfEngine`) | yes | their own modules |

## Proposed classification

For each store: the seven questions from the review of #218. A `?` is an open question,
not a default.

| Store | Authoritative? | Mutation boundary | Reconstructible? | Mutations audited? | Replay level (ADR 0008) | Failure semantics (ADR 0009) | Owning ADR |
|---|---|---|---|---|---|---|---|
| Hypergraph engine | `?` (see finding 1) | graph boundary (WriteBus), per ADR 0007 | partly, from snapshot and room store; extent not established | through WriteBus provenance for committed writes only | Forensic | partial possible; declare per tool | 0007, 0008, 0009 |
| RF hypergraph store | `?` | none documented | no | no | `?` | `?` | 0007 |
| WriteBus state | yes, for commit history | WriteBus itself | no | it is the commit record | Forensic | `FAILED_PARTIAL`, repair task, dead letter | 0009 |
| Operator database | yes, for operators, sessions, rooms; `room_entities` `?` | operator manager; `room_entities` through WriteBus | no | `entity_audit_log` for entities | Audit | `?` | 0007 |
| Control audit | yes (governance) | control audit | no | it is the audit | Audit (append-only) | success/error today; ADR 0009 proposes more | 0004, 0009 |
| Proposal log | should be: it carries the approval basis | MCP orchestrator | no | no durable copy | Audit | n/a | 0006, 0008 |
| Recon system entities | derived (see finding 2) | none for direct writes | yes for rows that came from `room_entities`; no for the rest | no | none, if it stays derived | best effort | 0007 |
| Live-ingest queue | transient | none | no | no | none | input lost on failed apply (ADR 0009 §4) | 0009 |
| RF solver evidence store | `?`: bounded, in memory, yet described as evidence | none documented | no | no | `?` | best effort | 0007 |
| Authority database | declared authoritative by its own module | mirror after ingest | no | `?` | Forensic | `?` | 0007, 0008 |
| Server metrics and missions | missions `?`; metrics derived | server routes | no | no | `?` | `?` | 0007 |
| Scene event log | `?` | `?` | `?` | itself an event log | `?` | `?` | 0008 |
| Other local databases | caches and local data; each `?` | their modules | caches yes | no | none | best effort | 0007 |

## Findings

Three things in the inventory affect the ADR work. They are observations from the code,
not decisions.

1. **There is no single authoritative store of the world.** `scythe_pg.py` states that
   embedded Postgres is "the authoritative source of truth" for PCAP artifacts, sessions,
   BSG groups and instance state, and that the hypergraph engine is "the analytical layer".
   ADR 0007 speaks of the graph boundary as if the hypergraph were the record. Both
   readings exist in the repository. Which store owns which fact has to be settled before a
   boundary can be said to be "the" boundary for it.
2. **A derived store holds content its source does not.** `recon_system.entities` is
   rebuilt at startup from the durable `room_entities`. The server's live-ingest worker
   adds `RECON_ENTITY` records straight into `entities`, with no room publish in that
   worker. Those rows do not come from the durable store and, from what the worker does,
   would not return after a restart. The worker also feeds the semantic-shadow and
   shadow-graph components. I did not search the rest of the server for a write-back.
3. **Two in-memory graph stores exist.** `HypergraphEngine` and `RFHypergraphStore` both
   hold nodes and edges. The inventory does not establish how they relate, and ADR 0007's
   "authoritative mutation" depends on it.

## Not yet inventoried

State that exists and was not examined, so its absence from the tables is not a statement:

- scope and streaming caches in the engine, and the rate-limit and trust state in the MCP
  layer
- `semantic_shadow`, `shadow_graph`, `adaptive_schema_engine` and the protocol-intel state
- RF bridge and SDR runtime and configuration state, including capture state
- in-process configuration summaries such as `GRAPHOPS_SENSOR_GROUNDING_LAST`
- files written outside SQLite (snapshots, exports, manifests, logs)
- the DuckDB scene store and other stores outside the modules listed above

## Using this

Before classifying a tool or route under ADR 0007, find its target state in the first
table. If it is not there, add it. A row with a `?` in *Authoritative?* is a question to
answer first, not a gap to fill with a default.
