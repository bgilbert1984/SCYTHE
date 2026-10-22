# Command-instrument proposal: backend audit

**Status:** audit only. No code was changed and nothing here is a design.
**Purpose:** a 15-item proposal asks the globe/command pages to show mission identity, revisions,
WriteBus provenance, evidence quality, lifecycle, governance and Registry execution. This lists,
for each field those views would need, whether the backend produces it today, where it comes
from, and what is missing. The browser should not render a field the backend does not supply,
because a made-up value would break the rule that display must not silently change meaning.

**Method:** read the code at the repository state of branch `mission-live-edge-delete`
(2026-10-22). Nothing was run against a live server. Where a claim rests on reading and not on
running, it says so. "Not found" means a `grep` over the named files returned nothing, not that
it cannot exist elsewhere.

**Verdict vocabulary**

| Verdict | Meaning |
|---|---|
| EXISTS | Produced and readable over HTTP or the socket today |
| PARTIAL | Produced server-side but not reachable, not mission-scoped, or not per-object |
| MISSING | No producer found |

## 1. Summary

| Proposal item | Verdict | One line |
|---|---|---|
| 2. Mission identity (name, id, owner, status) | EXISTS | `GET /api/missions/<id>` |
| 2. Mission UUID / revision / evidence revision | MISSING | no revision counter on a mission; `mission_id` is the only identity |
| 2. Operator list and roles | EXISTS | `GET /api/missions/<id>/operators` |
| 3/13. Observation vs interpretation | PARTIAL | inferable from `event_type` and node `method`, not an explicit field |
| 3/13. Confidence | PARTIAL | edges carry `weight`; node confidence lives in free-form `metadata` |
| 3/13. Solver inputs, "derived from" | PARTIAL | `evidence_refs` and `caused_by_event_id` exist on WriteBus writes only |
| 3/13. Graph revision | PARTIAL | `graphRevision` exists in the graph resolver, not on events or the globe feed |
| 4. Evidence timeline | PARTIAL | mission events exist; no stage vocabulary beyond graph event types |
| 5. Hypergraph typed edges | PARTIAL | edge `kind` and `weight` exist; semantics are convention, not schema |
| 6/14. Evidence heat and uncertainty | MISSING | no confirmed, contradicted or weak state per object |
| 7. Mission lifecycle (6 states) | MISSING | mission `status` is `open` or `closed` only |
| 8. Governance banner | PARTIAL | approval phase and modes exist; no "read only", "hardware locked" or "Gate D" flag |
| 9. Registry execution flow, bypass detection | PARTIAL | bypass raises server-side for guarded methods; nothing is exposed to a client |
| 10. WriteBus ledger feed | PARTIAL | commit status tables exist; no read route |
| 11. Mission diff | PARTIAL | an SSE diff stream exists; it diffs graph state, not mission revisions |
| 12. Semantic layers | MISSING | no layer taxonomy beyond the above |
| 15. Four-way category (observation, interpretation, annotation, hypothesis) | PARTIAL | observation and graph-write are distinguishable; the other two are not |

The two findings that matter most:

1. **Mission events carry no revision, WriteBus id or per-event hash.** Every provenance row
   that depends on them stays "not recorded" until the backend adds them (section 3).
2. **Live graph pushes are not mission-scoped.** The live feed writes events under
   `session_id='global'` with no `mission_id`, so what the globe receives live is not
   necessarily what `GET /api/missions/<id>/events` returns (section 4).

## 2. Mission identity and lifecycle

**Operational mission record.** `GET /api/missions/<id>` returns the row from the `missions`
table: `mission_id`, `name`, `owner`, `status`, `metadata` (free JSON), `created_at`,
`updated_at`. Created in `create_mission`; table defined near `rf_scythe_api_server.py:1816`.

- `status` is set to `'open'` on create and `'closed'` by `POST /api/missions/<id>/end`. Those
  are the only two values the code writes.
- There is no `revision`, `version`, `uuid` or `evidence_revision` column. `updated_at` changes
  on `PATCH` and `end`, so it is a timestamp, not a counter.
- `metadata` is free-form and merged on `PATCH`. A client could store anything there, but
  nothing reads it as lifecycle.

**Domain mission (`scythe_mission.py`).** A frozen `Mission` with `mission_type`,
`mission_parameters`, `classification`, `region`, `start_after`, `expire_after` and
`gate_d_request_sha256`, plus `digest()` as a canonical SHA-256 identity. This is the only
stable "mission UUID" candidate. It is not persisted and not served over HTTP: `create_mission`
validates `mission_id` against `is_valid_mission_id` but does not construct a domain `Mission`
or store its digest. `docs/research/scythe_mission_browser_rendering.md` §1 records that the two
systems are linked only by sharing an id namespace.

**Signal lifecycle** (`docs/research/scythe_mission_definition.md` §11:
Observed, Clustered, Localized, Characterized, Tracked, Attributed, Archived) is specified as
`labels["lifecycle_state"]` on a graph node. `grep` for `lifecycle_state` across `*.py` found no
producer, so this is a design, not a field you can read.

**Proposal item 7** lists Planning, Collection, Analysis, Awaiting Approval, Approved,
Archived. That is a *mission* lifecycle, which does not exist, and it differs from the *signal*
lifecycle above. They should not be conflated when this is scoped.

| Needed by UI | Today | Gap |
|---|---|---|
| Mission name, owner, status | EXISTS | none |
| Mission lifecycle beyond open/closed | MISSING | a state column and a legal-transition rule; owner decision |
| Mission revision | MISSING | a counter that increments on each committed write, or a head hash |
| Mission UUID / digest | MISSING over HTTP | persist and serve `Mission.digest()` |
| Evidence revision | MISSING | undefined; needs a definition before it can be produced |

## 3. Mission events (what the timeline and inspector read)

`GET /api/missions/<id>/events?limit&offset[&include_total=1]` returns `asdict(TacticalEvent)`.
Fields: `timestamp` (ms), `event_type`, `entity_id`, `session_id`, `lat`, `lon`, `alt`,
`payload`, `seq`, `mission_id`. Source: `scene_duckdb_store.py` (`TacticalEvent`) and
`mission_events` in the server.

- Ordering is by `seq`, a monotonic insert order. It is **not** a graph revision and not a
  mission revision. It is local to the DuckDB store.
- There is no per-event hash, no WriteBus idempotency key, and no `commit_status` on the row.
  Whether `payload` carries them depends on the writer and was not verified.
- The route returns the **oldest** events first by `limit`/`offset`; the globe panel reads the
  newest 200 using `tailOffset` with `include_total`. A mission with more than 200 events is
  shown through a window, which the scrubber already treats conservatively.
- The event-type vocabulary the globe understands (`NODE_CREATE`, `NODE_UPDATE`, `EDGE_CREATE`,
  `EDGE_UPDATE`, `EDGE_DELETE`, `HYPEREDGE_CREATE`, `NODE_DELETE`) comes from the graph event
  path. Mission ingest (`POST` events) accepts any string for `event_type`, so other types can
  appear (for example `entity.move` in the store's own self-test). Nothing enforces a
  vocabulary.

**Interpretation vs observation.** No event or node field says "this is an interpretation".
What exists:

- `node_geo_index.method` (`observed`, `neighbor_inferred`, `recon_fallback`) says how a
  *position* was obtained. It is the only explicit evidence-quality field the globe uses.
- WriteBus injects `metadata.provenance_write` on every committed write (`_inject_provenance`),
  and `metadata.provenance_rule` when an inference engine supplied `rule_id` or `engine`.
  The presence of `provenance_rule` is the nearest thing to "this is an inference". It exists
  only on writes that went through WriteBus.
- `_trust_metadata` derives `author_class` (`operator`, `system`, `automated`, `anonymous`) from
  the operator id *prefix*. That is a naming convention (`SYSTEM:`, `system_`, `auto_`,
  `ingest_`), not an authenticated claim.

| Needed by UI | Today | Gap |
|---|---|---|
| Event time, type, entity, order | EXISTS | none |
| Per-event WriteBus id / fingerprint | PARTIAL | `write_fingerprint` is in write metadata; not on the event row or route |
| Observation vs interpretation, explicit | MISSING | an explicit `evidence_class` (or equivalent) set by the writer |
| Operator annotation as a distinct kind | MISSING | no annotation event type found |
| Hypothesis / prediction as a distinct kind | MISSING | no event type or label found |
| Per-event operator | PARTIAL | `Provenance.operator_id` exists on WriteBus writes; not on the row |

## 4. Live feed vs mission scope

The live path in `rf_scythe_api_server.py` (around line 4290) does two things for each graph
event: appends a `TacticalEvent` with `session_id='global'` and **no `mission_id`**, and emits a
Socket.IO `graph_event` with `event_type`, `entity_id`, `entity_kind`, `sequence_id` and `data`.

Consequences, from reading the code (not exercised against a running server):

- `append_batch` resolves `mission_id` through `capture_sessions`. A `'global'` session is not
  bound to a mission unless something explicitly binds it, so live events will normally have
  `mission_id = NULL` and **will not appear** in `GET /api/missions/<id>/events`.
- The globe replays mission events from that route and applies live events from the socket.
  The two sources can therefore disagree: an edge deleted live may have no matching mission
  event, so a later scrub cannot see it. PR #282 handles this by keeping deleted edges in
  memory for the page's lifetime, which does not survive a reload.
- `sequence_id` on the socket event is the graph event sequence. It is the closest thing to a
  graph revision the live feed has. It is not on the stored mission event, so the two cannot be
  correlated by sequence.
- `data` may carry `metadata.provenance_write.mission_id` for writes that went through
  WriteBus. Whether the live push includes that, and whether the globe can use it to decide
  mission membership, was not verified.

This is the most important thing to decide before building anything that mixes live and
replayed evidence in one view.

**Update (`live_mission_binding.py`):** a live event that declares exactly one valid mission
(in `labels`, `metadata`, `meta`, or WriteBus `provenance_write`) is now stored under
`live:<mission_id>` with that `mission_id`, so it appears in the mission's events. Everything
else stays in `'global'` unbound. The shared `'global'` session is deliberately not bound,
because a session's binding backfills every event already in it. An event naming two missions,
or any invalid id, stays unbound.

**Update (`graph_event_fields.py`):** measuring the real pipeline showed the drain read every event
with `getattr`, but `WriteBus.publish_bus` publishes a plain dict (`ENTITY_UPSERT`), so each
WriteBus event was stored and pushed to clients blank (`event_type=''`, `entity_id=''`, `{}`).
The drain now reads objects and dicts alike. A WriteBus commit whose context sets `mission_id` is
now stored under that mission. The pcap flush context now carries `IngestConfig.mission_id`, so a
missioned ingest binds. The MCP handler and `POST /api/pcap/batch_ingest` now accept an optional
`mission_id`, validated like `POST /api/missions` (400 when invalid). Still open: no caller supplies
one yet (the CLI, `tak_ml_gemma_runner` and the FTP-credentials route do not), and the other `GraphEventBus` subscribers (map-cache node and edge, line
~4158 onward) have the same `getattr`-only reading. A dict event also never receives the bus's
`sequence_id`, so it reads 0.

## 5. Revisions

Three different "revision" notions exist or are implied. They are not the same thing.

| Notion | Where | Exposed to a client? |
|---|---|---|
| Graph revision (`graphRevision`) | `graphops_graph_resolver.py` (pinned projection), `graphops_director.py` (cited in evidence refs `graph:<rev>:<entity>`), `graphops_infrastructure_contradictions.py` (hashed into a contradiction `revision`) | Per `docs/research/scythe_mission_browser_rendering.md`, via `GET /api/graphops/selection/graph?mission_id=`. A route grep over `rf_scythe_api_server.py` found only `/api/graphops/dag` and `/api/graphops/tutorial`, so that route is served elsewhere or not at all. **Unverified.** |
| Write fingerprint | `WriteBus._idempotency_key`, stored in `metadata.write_fingerprint` and the idempotency/commit-status tables | Not directly |
| Event `seq` | DuckDB store | Yes, on mission events, but store-local |

There is **no mission revision** (a counter or head hash covering a mission's committed writes)
and no **evidence revision**. Items 2, 3, 10 and 13 each want one. Defining either is a design
decision, not a wiring task: it has to say what increments it and what it covers.

## 6. WriteBus ledger

`writebus.py` keeps two SQLite tables, `writebus_commit_status` (latest status per
idempotency key) and `writebus_commit_status_history` (one row per status change, with
`created_at`, `errors_json`, `context_json`). Statuses: `PENDING_GRAPH`, `GRAPH_APPLIED`,
`ROOM_PERSISTED`, `BUS_PUBLISHED`, `AUDITED`, `COMMITTED`, `FAILED_PARTIAL`, `REJECTED`, and the
`*_SKIPPED` variants.

That is a real, ordered, append-style history of every commit stage, with entity id and type.
It is the natural source for a "live WriteBus feed" (item 10). What is missing:

- **No HTTP read route.** A route grep for `writebus`, `commit` and `ledger` in
  `rf_scythe_api_server.py` found none. The tables are for "recovery and operator diagnostics"
  per the class docstring and are read in-process.
- **Not mission-scoped.** The table has `entity_id`, `entity_type`, `room_name` and a
  `context_json` blob. `mission_id` is in `Provenance` but whether it reaches `context_json` was
  not verified. A mission filter may need a column.
- **Not a revision.** Rows are per write, keyed by hash. There is no monotonic ledger number
  the UI could show as "Mission Revision 183".
- ADR 0009 (partial failure) is `Proposed`. `FAILED_PARTIAL` exists but its representation is
  still being decided, so a feed should show the status verbatim and not interpret it.

## 7. Governance

Reachable today (all require an authenticated identity via `_rf_bridge_principal`):

| Route | Gives |
|---|---|
| `GET /api/mcp/governance` | `lifecycle_enabled`, `remote_phase_control_enabled`, the orchestrator `phase` (`value`, `name`), per-effect `approval_modes`, overrides, `pending_ttl_seconds`, `caller` |
| `GET /api/mcp/proposals/pending` | pending proposals with `proposed_by`, `approval_mode`, `own`, `expires_at` |
| `GET /api/mcp/proposals/recent` | proposals in any state with `approved_by`, `denied_by`, `executed_by`, timestamps, `reason` |
| `GET /api/missions/<id>/operators` | `operator_id`, `role`, `joined_at` per member |
| `GET /api/operator/session` | the caller's session and operator record |

Not found (grep for `mutation_enabled`, `read_only`, `READ_ONLY`, `hardware.*lock`,
`second_operator` and `two_operator` in the server returned nothing):

- A single "mutation disabled", "authority read only" or "hardware locked" state.
- A "second operator connected" signal. The pending-proposal list implies a second operator is
  needed to approve, and `own` marks the caller's proposals, but nothing reports whether a
  second operator is currently online. (`GET /api/operator/active` exists and lists active
  operators; whether that is the right source was not checked.)
- **Gate D** is a replay-mission protocol, not a runtime switch. It appears as
  `Mission.gate_d_request_sha256` (only valid for `mission_type == REPLAY`) and as the
  `tools/r4_package/gate_d_monitor.py` resource monitor. Neither is served as "authorized /
  not authorized". A banner reading "Gate D: NOT AUTHORIZED" would have no source.

So the banner can honestly show the approval phase, approval modes and the caller's identity
now. The rest needs a decision about what the states mean and where they are stored.

## 8. Registry execution and bypass

`Registry.execute` (`mcp_registry.py`) runs: mode check, parameter validation, rate limit,
`control_audit.begin_execution`, the tool, `finish_execution`, return-schema check, then
`_audit_log`. Findings:

- `_audit_log` writes one JSON line through `logger.info("[MCP_AUDIT] ...")` including
  `uuid`, `tool`, `mutates_state`, `effect`, `params`, `proposal_id` and `principal`. This is a
  log line. It is not queryable, so there is nothing for a UI to read.
- `control_audit.py` is a durable append-only SQLite audit (UPDATE refused, DELETE refused
  inside retention) with `started` and `success`/`error` rows per execution. It records only
  tools that mutate and only when configured. It documents a CLI (`list`, `purge`); no HTTP
  route to read it was found.
- `Registry.get_metrics()` returns counters (`invocations`, `rate_limit_hits`, `mutations`,
  `errors`) per tool. A route that serves it was not looked for.
- **Bypass detection exists only for guarded methods.** With `strict_no_bypass=True`, WriteBus
  wraps a fixed list of methods on the hypergraph and operator manager so they raise
  `WriteBusKernelViolation` outside `WriteBus.commit()`. ADR 0007 states this guarantees nothing
  about other stores or methods, and records that tool paths currently disagree with the
  boundary. A violation is an exception. Whether it is logged or counted anywhere a UI could
  read was not found.
- ADR 0005 lists HTTP routes that are explicit exceptions to the single execution surface.
  "Lights up red when a call bypasses Registry.execute" would flag those routes constantly,
  because they are documented exceptions, unless the exception list is itself machine-readable.

The accurate statement for a UI today is: an executed, audited tool call can be shown if the
audit is exposed. A bypass cannot be shown, because the browser cannot observe what did not go
through the Registry and the server records no such event.

## 9. Evidence quality, contradiction, confidence

- **Confidence.** `HGEdge.weight` is documented as an edge's confidence
  (`scythe_mission_definition.md` §10). Nodes get `confidence` from `metadata`
  (`hypergraph_engine.py` reads `float(md_in.get('confidence', 1.0))`, defaulting to 1.0). A
  default of 1.0 means *absent confidence reads as certain*. A UI that displays it must not
  distinguish "recorded as 1.0" from "never recorded" without a presence flag, and today there
  is none.
- **Confidence over time.** `hypergraph_engine.py` has a confidence mutation trace emitter
  (around line 1160). It is not served.
- **Contradiction.** `graphops_infrastructure_contradictions.py` builds a contradiction report
  keyed to a `graphRevision`. `GET /api/infrastructure/reality-divergence` serves a different
  thing (physical vs fabric graph divergence). A route that serves the contradiction report was
  not found in `rf_scythe_api_server.py`; `mcp_server.py` serves `GraphOpsDirector`, whose
  output includes world hypotheses `W0_OBSERVED` to `W4_ADVERSARIAL`. These are per-analysis
  results, not a per-node state.
- **Confirmed / weak / unknown.** No field. "Confirmed" would need an operator confirmation
  event, which does not exist (section 3).

The Reality Prism data (`observedWorld`, alternative worlds) is the richest existing source for
an "explanation" panel, but it is produced per query by the director, not stored per object.

## 10. Mission diff

`GET /api/missions/<id>/diff/stream` is an SSE stream of subgraph diffs scoped by mission label
(`labels.mission_id` or `missionId`, same in `metadata`). It requires a session `token` query
parameter. It diffs graph state over time, not between two revisions or two missions, and it
depends on nodes and edges carrying the mission label.

Mission A vs Mission B needs either two pinned projections (the resolver supports
revision-pinned selection) or stored revisions. Neither is exposed as a comparison.

## 11. What the browser can honestly show today

Everything below is supported by a field that exists:

1. Mission name, id, owner, status, created and updated times, and operator roles.
2. Event timeline: type, entity, time and order from mission events.
3. Per-node position provenance (`method`), with "not recorded" otherwise.
4. WriteBus provenance for writes that went through it (`provenance_write`,
   `write_fingerprint`, `trust.author_class`), if the globe is fed that metadata.
5. Approval phase, modes, pending and recent proposals, and the caller's identity.
6. Edge `kind` and `weight`, with the caveat in section 9.

Four-way iconography (item 15) can be built for the categories that are distinguishable: graph
write, observation, correlation and inference. Operator annotation and hypothesis need a
producer first.

## 12. Backend work the proposal implies

In rough dependency order. Each is a backend decision; none is a browser task.

| # | Work | Unblocks |
|---|---|---|
| 1 | ~~Decide whether live graph events belong to a mission, and bind them~~ Done for events that declare their mission; see the update in section 4 | live and replay agreeing; items 4, 10, 11 |
| 2 | Define mission revision (what increments it) and serve it; expose `sequence_id` on stored events | items 2, 3, 10, 13 |
| 3 | Expose WriteBus commit status read-only, mission-filtered | item 10 |
| 4 | Add an explicit evidence class (observation, interpretation, annotation, hypothesis) set by the writer | items 3, 12, 13, 15 |
| 5 | Add a confidence-presence flag so "unrecorded" differs from 1.0 | items 6, 14 |
| 6 | Define the mission lifecycle states and legal transitions | item 7 |
| 7 | Define what "read only", "hardware locked" and "Gate D authorized" mean and where they are stored | item 8 |
| 8 | Serve Registry execution audit and a bypass/violation record | item 9 |
| 9 | Persist and serve `Mission.digest()` | item 2 |

Items 6 to 8 touch governance and are the owner's call. They are listed because the proposal
needs them, not because this audit recommends a particular design.

## 13. Not examined

- A live server. No endpoint here was called, so response shapes come from reading handlers.
- Whether `data` on the socket `graph_event` carries `provenance_write`.
- `mcp_server.py` routes beyond finding `GraphOpsDirector`.
- Whether `/api/graphops/selection/graph` exists.
- `command-ops-visualization.html` beyond noting it already has a `missions-panel` with a
  mission header (`#mission-header`, around line 5332) that is separate from the globe page's
  panel.
- Frontend consumers of any field above other than the globe panel built in PRs #276 to #282.
