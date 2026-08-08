# SCYTHE Clarktech Expansion

Status: active implementation
Scope: GraphOps executable effects, causal-world interaction, SCYTHE-Web, and
authority-safe visual reasoning  
Companion catalog: [GraphOps_Directives.md](GraphOps_Directives.md)

## Implementation status

The first vertical slice was implemented on 2026-08-05:

- Canonical Directive Request and EffectPlan v1 JSON Schemas.
- Matching Python and JavaScript boundary validation.
- A deterministic `GraphOpsDirector` with preview and execute endpoints.
- Server-side RF-cell resolution against checksum-verified Float64 authority.
- Rejection of forged derived-display asset hashes.
- Typed browser RF-cell selections.
- An allow-listed transactional effect runtime with rollback.
- A Reality Prism showing authority, display difference, threshold decision,
  provenance, interpolation, and falsifier.
- A reversible coverage-threshold lens and explicit undo control.
- The generic `graphops_directive` MCP tool.

This completes the recommended “Reality Prism + threshold lens” foundation.
The next planned work begins Phase 2: bounded graph selection, graph overlays,
DSL preview, and read-only RF/graph correlation effects.

Phase 2A was implemented on 2026-08-06:

- Bounded, revision-pinned graph snapshots through the stable orchestrator URL.
- Explicit selection of the populated visualization graph rather than an empty
  parallel analysis engine.
- Geospatial graph-node and inferred-edge rendering in SCYTHE-Web.
- Typed RF-cell plus graph-node selection.
- Deterministic `FOCUS`, bounded `EXPAND`, and `RF_CORRELATE` DSL preview.
- Separate preview and execute controls.
- Dashed correlation fibers labelled `INFERRED` and “not causation.”
- Structural `TEMPORAL_EVIDENCE: ABSENT` output when measured RF support is
  missing or the selected solver cell is temporally incomparable.
- Stable primary-instance proxying fixed for instances whose healthy lifecycle
  state is `ready` rather than `running`.

Phase 2B was implemented on 2026-08-06:

- Graph edges and event-like nodes are selectable typed references pinned to a
  graph revision.
- Paired same-clock time pins compile and execute bounded `GRAPH_DELTA` queries.
- Delta results explicitly declare `CURRENT_GRAPH_TIMESTAMP_PROJECTION`; they
  do not claim historical removals without retained immutable snapshots.
- Bounded provenance-impact traversal exposes graph adjacency and declared
  sources without treating adjacency as causality.
- Explicit contradiction relations remain separate and render as red dashed
  overlays; no synthetic consensus is produced.
- The stable orchestrator accepts validated measured-RF spectral summaries,
  derives `OBSERVED` evidence IDs, synchronizes them to the selected graph
  instance, and structurally rejects raw IQ fields.
- A missing graph instance now returns an explicit HTTP 200 empty snapshot so
  the standalone RF instrument continues operating without console-breaking
  503 responses.
- The regional globe uses the same optional Cesium World Terrain negotiation
  as Command Ops while retaining token-free OpenStreetMap imagery and an
  ellipsoid fallback.

Lunar M0 was implemented on 2026-08-06:

- A token-free, Moon-native Cesium instrument runs on a 1,737,400 m lunar
  ellipsoid without allowing WGS84 coordinates into the view.
- Lunar selections carry the explicit `MOON_ME_DE421` body-fixed frame and
  `REFERENCE_ELLIPSOID_ONLY` spatial authority.
- Three locally packaged visual references are SHA-256 verified before the
  instrument becomes ready.
- The `explain.lunar-location` directive compiles a proof-carrying
  `view.show-lunar-prism` effect through the stable GraphOps endpoint.
- The Lunar Reality Prism refuses elevation, slope, lighting, Earth visibility,
  and RF occultation claims because M0 has no registered LOLA terrain or pinned
  SPICE kernel set.
- LROC illumination and LOLA slope imagery remain labelled reference panels;
  neither is sampled as a geospatial measurement surface.

The next bounded frontiers are Lunar M1 registered LOLA terrain, a pinned SPICE
kernel set, retained immutable graph snapshots, clock calibration evidence,
contradiction adjudication receipts, and persistent RF observation storage
across orchestrator restarts.

## 1. Executive intent

The Clarktech expansion turns the SCYTHE globe from a passive dashboard into a
causal instrument. An operator should be able to point at a visible condition,
state a directive, inspect the GraphOps plan it compiles into, and cause a
bounded change in the view, analysis, or a sandboxed world.

The central idea is not “more animation.” It is **proof-carrying effects**:

- Every visual effect identifies the evidence and authority behind it.
- Every analytic effect exposes its generated DSL and temporal scope.
- Every simulation runs in a visibly separate counterfactual world.
- Every state mutation is declared, recorded, and reversible.
- Every external or evidentiary mutation crosses an authority gate.
- Missing data remains missing; model agreement never becomes evidence.

The signature interaction is:

> Determine what must be true for this red coverage gap and this network burst
> to share a cause.

SCYTHE should construct competing causal worlds, expose their assumptions and
contradictions, render the differences, and propose the least expensive
observation that separates them. It must not imply that temporal correlation,
solver output, or a visually compelling animation proves causality.

## 2. Meaning of “executable affects”

An **effect** is a typed, bounded state transition requested by GraphOps. An
**affect** is the intended change in analyst attention or posture: isolate,
compare, doubt, inspect, test, or decide. Clarktech effects are allowed to guide
attention, but they must do so through a stable visual grammar rather than
unconstrained dramatic styling.

Examples:

- “Isolate measured evidence” produces an evidence filter, not a new claim.
- “Expose disagreement” highlights contradictions, not a consensus verdict.
- “Move this transmitter” creates a shadow-world solver job, not a change to
  the observed transmitter.
- “Tune to this emitter” creates an authority-gated proposal, not an immediate
  device command.

Executable effects do not include arbitrary browser JavaScript, arbitrary CSS,
unvalidated Cesium entities, or natural-language instructions interpreted as
code.

## 3. Current foundation

SCYTHE already contains important pieces of the expansion:

- `scythe-web/contractLoader.js` validates propagation-data contracts.
- `scythe-web/tileLoader.js` verifies checksums and decodes declared layouts.
- `scythe-web/rfSampler.js` performs deterministic sampling and coverage
  classification without invented fallback data.
- `scythe-web/evidenceStyles.js` distinguishes evidence classes visually.
- `scythe-web/monocleOverlayLayer.js` renders RF cells, provenance, declared
  geometry, uncertainty, and the current coverage-cell selection gesture.
- `graphops_copilot.py` compiles investigative intent into GraphOps DSL and
  exposes read-only MCP tools.
- `graph_query_dsl.py` and `subgraph_diff.py` provide existing query and graph
  difference capabilities that should be reused.
- `mcp_orchestrator.py`, `mcp_safety.py`, and `mcp_registry.py` provide proposal,
  execution, mutation, and autonomy boundaries.
- `rf_solver_evidence.py` keeps solver evidence separate from measured RF
  observations.
- The current “Explain this coverage cell” interaction demonstrates a complete
  browser-to-GraphOps round trip.

The current limitations are equally important:

1. The coverage interaction is hard-coded rather than protocol-driven.
2. SCYTHE-Web has no generic directive client or effect runtime.
3. There is no typed multi-selection model for RF cells, graph entities,
   assertions, regions, paths, and time pins.
4. There is no browser world stack or reversible effect ledger.
5. There is no general graph/evidence overlay owned by SCYTHE-Web.
6. Browser-submitted display values are correctly marked non-authoritative, but
   the backend does not yet independently dereference every selection against
   a server-held contract and authoritative asset.
7. The directive catalog is richer than the currently executable DSL, solver
   jobs, and visual vocabulary.

## 4. Non-negotiable invariants

### 4.1 Evidence invariants

1. `OBSERVED`, `MEASURED`, `SOLVER_OUTPUT`, `INFERRED`, `SYNTHETIC`,
   `COUNTERFACTUAL`, and `ILLUSTRATIVE` remain distinguishable.
2. A visual effect cannot change an evidence class.
3. A counterfactual cannot be merged into the observed world.
4. Browser display tiles cannot be promoted to authoritative assets.
5. Model confidence, model agreement, and visual salience are not evidence.
6. An inference from absence must prove that the relevant sensor was capable,
   active, positioned, and temporally aligned.
7. No-data and sparse-data confidence clamps remain structural backend rules.

### 4.2 Execution invariants

1. The browser executes only allow-listed effect types.
2. Effect parameters are schema-validated before preview and before apply.
3. Read-only visual effects may execute locally.
4. Read-only analytic effects execute through GraphOps/MCP or a constrained API.
5. Simulation effects execute as sandboxed, immutable jobs.
6. Operational effects use the orchestrator proposal/decision/execution flow.
7. Every applied effect emits an audit record and, where applicable, an undo
   token.
8. Replaying an effect with the same idempotency key produces the same state or
   returns the existing result.

### 4.3 Visual invariants

1. Animation never implies authority by itself.
2. Static solver data does not pulse like live telemetry.
3. Missing data is rendered as a hole or explicit unknown state, not silently
   interpolated.
4. Uncertainty is not encoded as confidence.
5. Contradictions remain visible until adjudicated.
6. Color is never the sole carrier of evidence class or state.
7. Reduced-motion and high-contrast modes preserve the same semantics.

## 5. Target architecture

```text
Operator gesture / directive / API client
                    |
                    v
          Selection + Directive Request
                    |
                    v
         GraphOps Directive Compiler
          |         |          |
          |         |          +-- authority assessment
          |         +------------- DSL / solver job compilation
          +----------------------- evidence dereference
                    |
                    v
            Proof-carrying EffectPlan
          |          |          |          |
          v          v          v          v
      View effect  Analysis   Shadow     Proposal
       runtime      query      world       gate
          |          |          |          |
          +----------+----------+----------+
                    |
                    v
        Effect ledger + evidence references
```

The architecture has four planes:

- **Evidence plane:** immutable or append-only observations, measurements,
  solver products, lineage, and provenance.
- **Directive plane:** operator intent, typed selections, compilation, and
  refusal.
- **Effect plane:** reversible view state, analytic results, and world-stack
  transitions.
- **Authority plane:** proposals, decisions, mutation budgets, and execution.

No plane may silently inherit the authority of another.

## 6. Effect classes

| Class | Examples | Execution location | Default authority |
|---|---|---|---|
| Presentation | Dim, isolate, highlight, camera, timeline, threshold coloring | Browser | Read-only and reversible |
| Analysis | `RF_CORRELATE`, `GRAPH_DELTA`, provenance traversal, clustering | Backend | Read-only |
| Simulation | Move transmitter, remove sensor, shift timestamps, antenna hypothesis | Sandboxed job runner | Counterfactual only |
| Investigation state | Create/freeze/discard shadow world, pin evidence, save comparison | Browser plus investigation store | Scoped mutation |
| Evidentiary state | Promote, invalidate, merge, adjudicate a claim | Authority service | Human review required |
| Operational | Tune SDR, start capture, alter external system | Orchestrator | Explicit proposal required |

Presentation effects may alter what is visible. They may not alter underlying
evidence or persist conclusions. Investigation-state effects may persist an
operator workspace, but do not mutate source evidence.

## 7. Directive protocol

### 7.1 Directive request

The browser sends references and coordinates, not authoritative assertions.

```json
{
  "protocolVersion": "1.0",
  "directiveId": "dir-0187",
  "directive": "explain.coverage-cell",
  "utterance": "Explain why this cell is red",
  "selection": [
    {
      "kind": "rf-cell",
      "datasetId": "ntia-itm-sf-bay-area-v1",
      "tileId": "regional-z0",
      "gridCoordinate": [17, 11],
      "displayAssetHash": "sha256:...",
      "displayValue": 151.2,
      "displayUnits": "dB"
    }
  ],
  "viewContext": {
    "timeWindow": ["2026-08-05T01:00:00Z", "2026-08-05T01:00:10Z"],
    "camera": {"longitude": -122.4, "latitude": 37.8, "heightMeters": 9000},
    "activeWorldId": "observed"
  },
  "requestedMode": "preview",
  "idempotencyKey": "operator-session:dir-0187"
}
```

`displayValue` is diagnostic input only. The backend dereferences
`datasetId`, `tileId`, and `gridCoordinate`, validates the contract and hashes,
and reads the authoritative value when the directive requires authority.

### 7.2 Effect plan

```json
{
  "protocolVersion": "1.0",
  "directiveId": "dir-0187",
  "planId": "plan-a8f4",
  "status": "completed",
  "summary": "The cell is classified as a gap at the selected threshold.",
  "evidencePosture": "solver-backed",
  "effects": [],
  "queries": [],
  "jobs": [],
  "proposals": [],
  "claims": [],
  "supportingEvidence": [],
  "contradictingEvidence": [],
  "assumptions": [],
  "falsifiers": [],
  "mutations": [],
  "refusals": [],
  "undoToken": null,
  "expiresAt": "2026-08-05T02:00:00Z"
}
```

Required top-level semantics:

- `status`: `completed`, `partially-completed`, `refused`, or `unavailable`.
- `evidencePosture`: `no-data`, `sparse`, `inference-heavy`,
  `solver-backed`, `measurement-backed`, or `mixed`.
- `mutations`: exact state changes performed or proposed.
- `refusals`: machine-readable authority or capability failures.
- `expiresAt`: prevents stale plans from changing a newer investigation.

### 7.3 Browser effect

```json
{
  "effectId": "effect-41",
  "type": "view.set-evidence-filter",
  "phase": "preview",
  "targets": [{"kind": "world", "id": "observed"}],
  "parameters": {
    "include": ["OBSERVED", "MEASURED"],
    "dimOthers": true,
    "dimOpacity": 0.12
  },
  "styleToken": "EVIDENCE_ISOLATION",
  "evidenceRefs": [],
  "authorityImpact": "none",
  "reversible": true,
  "ttlMilliseconds": 300000
}
```

The browser rejects unknown `type`, `styleToken`, target kind, parameters, or
protocol version. The server cannot supply raw colors, shader source, HTML,
CSS, JavaScript, URLs, Cesium constructors, or DOM selectors.

### 7.4 Effect lifecycle

```text
received -> validated -> previewed -> applied -> reverted
                    \-> refused
                    \-> expired
                    \-> failed
```

Preview and apply are distinct. A preview may animate or highlight the intended
change but cannot mutate investigation, evidence, simulation, or operational
state.

## 8. Initial allow-listed effect vocabulary

### 8.1 View effects

- `view.set-evidence-filter`
- `view.highlight-targets`
- `view.focus-selection`
- `view.set-camera`
- `view.sync-cameras`
- `view.set-time-window`
- `view.pin-time`
- `view.set-coverage-threshold`
- `view.show-provenance-path`
- `view.show-contradictions`
- `view.show-uncertainty`
- `view.show-memory-scars`
- `view.show-causal-requirements`
- `view.clear-effect`

### 8.2 Analysis effects

- `analysis.preview-dsl`
- `analysis.execute-dsl`
- `analysis.rf-correlate`
- `analysis.graph-delta`
- `analysis.provenance-impact`
- `analysis.rank-worlds`
- `analysis.design-observation`

These are represented in an EffectPlan but executed by the directive service,
not by browser code.

### 8.3 World effects

- `world.create-shadow`
- `world.freeze-observed`
- `world.attach-job-result`
- `world.set-active`
- `world.compare`
- `world.discard-shadow`

### 8.4 Proposal effects

- `proposal.create-capture`
- `proposal.create-tune`
- `proposal.create-claim-review`
- `proposal.create-claim-merge`
- `proposal.create-evidence-invalidation`

Proposal effects never call the underlying mutating tool directly.

## 9. Typed selection model

Directives should operate on stable typed selections:

| Selection kind | Stable identity | Important context |
|---|---|---|
| `rf-cell` | dataset, tile, grid coordinate | threshold, display transform, frequency |
| `graph-node` | node ID plus graph revision | labels, evidence references |
| `graph-edge` | edge ID plus graph revision | source, target, assertion provenance |
| `assertion` | claim ID | authority, contradictions, dependencies |
| `event` | event ID | observed time, ingestion time, uncertainty |
| `region` | GeoJSON geometry hash | CRS, altitude range, time scope |
| `path` | ordered control points hash | spatial/network/temporal interpretation |
| `time-pin` | timestamp plus source clock | uncertainty and clock identity |
| `transmitter` | scenario transmitter ID | observed, declared, or counterfactual status |
| `world` | world ID and revision | world class and parent |

Selections contain references. They do not copy or reclassify evidence.

Multi-selection enables the signature interaction: one `rf-cell` plus one
`event` or graph burst becomes a typed causal-comparison request.

## 10. SCYTHE-Web module plan

### 10.1 `directiveProtocol.js`

Responsibilities:

- Validate request and EffectPlan protocol versions.
- Validate effect types and parameters.
- Reject executable content and undeclared fields.
- Normalize refusal, expiry, and partial-completion states.
- Provide stable serialization for hashing and audit.

### 10.2 `selectionModel.js`

Responsibilities:

- Own primary and secondary selections.
- Convert Cesium picks into typed references.
- Support cells, nodes, edges, assertions, regions, paths, and time pins.
- Preserve graph/dataset revisions.
- Emit selection changes without executing directives.

### 10.3 `directiveClient.js`

Responsibilities:

- Submit preview and execute requests.
- Bind requests to the current instance API base.
- Handle authentication, cancellation, expiry, and idempotency.
- Stream long-running job and query status when supported.
- Never retry a mutating request without its idempotency key.

### 10.4 `effectRuntime.js`

Responsibilities:

- Maintain an allow-listed effect registry.
- Validate, preview, apply, revert, expire, and replay effects.
- Serialize effect application through a deterministic queue.
- Record before/after state and undo tokens.
- Refuse effects targeted at stale world or graph revisions.

### 10.5 `visualEffects.js`

Responsibilities:

- Implement semantic style tokens using Cesium primitives and materials.
- Never accept arbitrary style source from the server.
- Maintain reduced-motion and high-contrast equivalents.
- Distinguish live observation, static evidence, inference, contradiction,
  counterfactual, uncertainty, and missing data.

### 10.6 `worldStack.js`

Responsibilities:

- Represent observed, reconstructed, and counterfactual worlds.
- Track immutable parent references and world revisions.
- Ensure observed evidence cannot be edited through a shadow world.
- Attach solver job outputs only to their destination world.
- Compare, freeze, activate, and discard worlds.

### 10.7 `realityPrism.js`

Responsibilities:

- Render assertion origin, authority, freshness, dependencies,
  contradictions, assumptions, and falsifier.
- Provide links to source evidence and generated DSL.
- Show “unknown” explicitly when lineage cannot be resolved.

### 10.8 `directivePanel.js`

Responsibilities:

- Display the operator utterance and compiled directive.
- Separate result, evidence, effects, jobs, and proposals.
- Preview the generated DSL before execution.
- Provide apply, refuse, cancel, undo, and promote-for-review controls.
- Never reduce an authority decision to a decorative confirmation dialog.

### 10.9 `graphOverlayLayer.js`

Responsibilities:

- Render selected graph entities and relationships in Cesium.
- Maintain evidence-aware edge materials.
- Render temporal and RF correlations distinctly from causal claims.
- Support graph-node, graph-edge, and burst selection.
- Consume bounded subgraphs rather than mirroring the full engine graph.

Existing `MonocleOverlayLayer` should remain responsible for contract-backed
dataset instrumentation. Directive effects should compose around it rather
than embedding a growing command system inside that class.

## 11. Backend module plan

### 11.1 `graphops_director.py`

The director is the primary directive compiler. It should:

1. Validate the directive and selections.
2. Resolve references from server-held evidence and contracts.
3. Determine evidence posture and authority requirements.
4. Compile deterministic DSL, job specifications, and visual effects.
5. Execute only read-only operations requested in execute mode.
6. Create proposals for mutating operations.
7. Return one EffectPlan envelope.

Compilation should prefer explicit directive identifiers. Natural language may
select or parameterize a directive, but it must compile into a known operation
before anything executes.

### 11.2 `graphops_effect_schema.py`

- Canonical protocol constants and JSON Schema.
- Python validation equivalent to `directiveProtocol.js`.
- Effect allow-list and parameter constraints.
- Cross-field rules for evidence, authority, reversibility, and expiry.

### 11.3 `graphops_evidence_resolver.py`

- Resolve RF cell references against verified manifests and assets.
- Resolve graph entities against explicit graph revisions.
- Resolve RF observations against the measured observation store.
- Return typed evidence records and authority labels.
- Reject browser-provided authority and stale identifiers.

### 11.4 `graphops_world_store.py`

- Persist world metadata and immutable parentage.
- Store evidence references rather than copied authority.
- Track simulation jobs and counterfactual products.
- Enforce world-class transition rules.
- Support investigation-scoped retention and deletion.

### 11.5 `graphops_job_runner.py`

- Execute allow-listed propagation and analytic job types.
- Use bounded resources, immutable inputs, and content-addressed outputs.
- Record solver identity, version, input hashes, environment, and run ID.
- Support cancellation and deterministic result reuse.
- Never attach a job output to the observed world.

### 11.6 API and MCP surfaces

Proposed constrained endpoints:

```text
POST /api/graphops/directives/preview
POST /api/graphops/directives/execute
GET  /api/graphops/directives/<directive_id>
POST /api/graphops/effects/<plan_id>/acknowledge
POST /api/graphops/effects/<plan_id>/revert
GET  /api/graphops/worlds
POST /api/graphops/worlds/<world_id>/discard
GET  /api/graphops/jobs/<job_id>
POST /api/graphops/jobs/<job_id>/cancel
```

Equivalent MCP tools should use the same director and schemas. HTTP and MCP
must not develop independent directive semantics.

## 12. World stack

### 12.1 World classes

- **Observed world:** append-only measured and observed evidence references.
- **Reconstructed world:** inferred relationships over observed evidence.
- **Counterfactual world:** altered assumptions or inputs with simulated
  products.
- **Comparison world:** ephemeral derived view containing differences and
  invariants; it owns no evidence.

### 12.2 World identity

```json
{
  "worldId": "world-shadow-0042",
  "worldClass": "COUNTERFACTUAL",
  "revision": 3,
  "parentWorldId": "observed",
  "createdByDirectiveId": "dir-0188",
  "assumptions": ["transmitter moved 2.1 km northeast"],
  "evidenceRefs": ["rf-observed-..."],
  "productRefs": ["solver-output-..."],
  "authority": "SANDBOX_ONLY"
}
```

### 12.3 Transition rules

- Observed -> reconstructed: references may be added; observations are not
  copied or changed.
- Observed/reconstructed -> counterfactual: assumptions must be explicit.
- Counterfactual -> observed: prohibited.
- Counterfactual -> human review package: permitted.
- Shadow discard: deletes world metadata and unreferenced derived products,
  never source evidence.
- Claim promotion: creates an adjudication proposal, not an automatic merge.

## 13. Visual grammar

Clarktech visual effects encode epistemic state.

| Visual channel | Meaning | Must not mean |
|---|---|---|
| Brightness | Evidentiary authority/selection priority | Model confidence alone |
| Saturation | Freshness | Threat severity |
| Line pattern | Evidence class or relationship type | Decorative variation |
| Halo width | Quantified uncertainty | General suspicion |
| Pulse | Live arrival or bounded replay cursor | Static solver importance |
| Magenta fracture | Contradiction | Generic warning |
| Transparent hole | Missing/no-data region | Low value |
| Layer separation | Causal-world disagreement | Physical distance |
| Persistent trail | Repetition/persistence over time | Predicted future path |

Recommended semantic tokens:

- `EVIDENCE_ISOLATION`
- `LIVE_OBSERVATION`
- `STATIC_SOLVER_OUTPUT`
- `INFERRED_RELATIONSHIP`
- `COUNTERFACTUAL_PRODUCT`
- `CAUSAL_REQUIREMENT`
- `CAUSAL_DISAGREEMENT`
- `CONTRADICTION`
- `INVARIANT_ACROSS_WORLDS`
- `UNCERTAINTY_BOUNDARY`
- `MISSING_DATA`
- `STALE_EVIDENCE`
- `MEMORY_SCAR`
- `AUTHORITY_GATE`

Each token has color, pattern, geometry, text/icon, reduced-motion, and
high-contrast definitions. No meaning depends on color alone.

## 14. Signature interaction walkthrough

Directive:

> Determine what must be true for this red coverage gap and this network burst
> to share a cause.

### 14.1 Selection

The operator selects:

1. An `rf-cell` from a verified solver dataset.
2. A graph burst/event with an evidence-backed time range.

The interface shows both evidence classes before enabling execution.

### 14.2 Compilation

The director resolves the selected RF cell from authoritative assets, resolves
the graph burst at a fixed graph revision, and compiles a bounded plan such as:

```text
RF_CORRELATE freq=900MHz window=5s
FOCUS "<resolved burst entity>"
EXPAND neighbors depth=2 limit=100
ASSESS
```

The exact supported DSL must be generated from existing verbs. Unsupported
causal operations remain explicit director-level analysis rather than invented
DSL syntax.

### 14.3 Competing worlds

At minimum, construct:

- `W0`: coincidence/null world.
- `W1`: shared benign infrastructure or environmental cause.
- `W2`: shared equipment/configuration/clock cause.
- `W3`: adversarial coordination hypothesis.

Each world returns:

- Required assumptions.
- Supporting and contradicting evidence.
- Predicted observation if true.
- Evidence still missing.
- Falsifier.
- Explanatory-economy score.

Scores reflect evidence coverage, contradictions, assumption count, source
independence, freshness, and null expectation. They are not causal
probabilities unless a validated probabilistic model exists.

### 14.4 Visual execution

- Shared facts remain fixed and cyan-marked.
- World-specific entities separate into translucent layers.
- Assumptions appear as labelled gates on dashed causal-requirement fibers.
- Contradictions appear as magenta fractures.
- Removing an assumption previews which world loses support.
- A time-window band shows correlation uncertainty and clock tolerance.
- The least expensive discriminating observation is rendered as a proposal,
  not silently initiated.

### 14.5 Result contract

The result must clearly state one of:

- No shared cause is supported by current evidence.
- One or more shared-cause worlds remain viable but unproven.
- Available evidence contradicts a shared cause.
- Evidence is insufficient or temporally incomparable.

“The worlds share a cause” is not a valid output without an applicable causal
model and supporting evidence.

## 15. Directive-to-effect mapping

| Directive | Primary effect | Backend work | Authority |
|---|---|---|---|
| Explain why this cell is red | Reality Prism + provenance path | Authoritative cell dereference | Read-only |
| Isolate measured portions | Evidence filter | None | Local |
| Reveal authoritative value | Display/authority comparison | Manifest and asset verification | Read-only |
| Reclassify at 135 dB | Threshold comparison overlay | Optional server verification | Local/read-only |
| Expose disagreements | World separation + contradiction overlay | World diff | Read-only |
| Extract shared facts | Invariant highlighting | World intersection | Read-only |
| Move transmitter here | Shadow world preview | Propagation solver job | Counterfactual |
| Remove this sensor | Dependency fade + shadow result | Support recomputation | Counterfactual |
| Shift events by five seconds | Temporal comparison | Re-run bounded correlation | Counterfactual |
| Reveal preceding events | Timeline neighborhood | Bounded temporal query | Read-only |
| Trace earliest evidence | Provenance/time path | Temporal provenance traversal | Read-only |
| Connect cell to graph node | Correlation fibers | `RF_CORRELATE` | Read-only |
| Circle a region | Geofence/focus volume | Scoped evidence summary | Local/read-only |
| Draw a path | Corridor highlight | Bounded relationship query | Read-only |
| Long-press assertion | Reality Prism | Claim resolution | Read-only |
| Place another receiver | Candidate-location overlay | Information-gain job | Proposed experiment |
| Explain Autopilot escalation | Tier trace | Card/dedup/cooldown resolution | Read-only |
| Create shadow world | Layer creation | World metadata write | Investigation mutation |
| Promote claim for review | Review-card preview | Proposal creation | Human gate |
| Tune or capture | Authority-gate visualization | Orchestrator proposal | Operational gate |

## 16. Authority and mutation matrix

| Mutation target | Direct execution? | Required record |
|---|---|---|
| Camera/view/filter | Yes | Local effect ledger |
| Coverage classification view | Yes | Threshold and prior state |
| Selection state | Yes | Selection revision |
| Saved investigation layout | Yes, authenticated | Workspace audit record |
| Shadow-world metadata | Yes, authenticated | World transition record |
| Solver job | Yes in sandbox | Inputs, hashes, solver provenance |
| Reconstructed claim | No automatic promotion | Claim and evidence references |
| Observed/measured evidence | No rewrite | Append-only correction workflow |
| SDR tuning/capture | No | Orchestrator proposal and decision |
| External system action | No | Orchestrator proposal and decision |

## 17. Security and trust model

Threats to address:

1. **Effect injection:** a compromised server attempts to send script, shader,
   URL, CSS, or arbitrary entity definitions.
2. **Authority laundering:** a browser display value is presented as an
   authoritative value.
3. **Selection substitution:** a stale cell or graph node resolves differently
   after a graph/dataset revision.
4. **Replay:** an old approved effect or proposal is applied to new state.
5. **Visual persuasion:** animation or salience obscures weak evidence.
6. **World leakage:** counterfactual products enter observed evidence.
7. **Mutation ambiguity:** an analytic directive causes collection or device
   control as a side effect.
8. **Resource exhaustion:** a gesture launches an unbounded graph or solver job.

Controls:

- Strict schemas with `additionalProperties: false`.
- Allow-listed effect and style tokens.
- Dataset hashes and graph/world revisions in selections.
- Server-side evidence dereferencing.
- Expiring plans and idempotency keys.
- Content Security Policy compatible with ES modules.
- Bounded query depth, node count, time windows, job resources, and output.
- Separate preview, execute, proposal, and decision operations.
- Audit records signed or chained where practical.
- Prominent evidence labels in every world and effect.

## 18. Audit and observability

Every directive should emit a structured event:

```json
{
  "event": "graphops.directive.completed",
  "directiveId": "dir-0187",
  "planId": "plan-a8f4",
  "operatorId": "operator-reference",
  "selectionHashes": ["sha256:..."],
  "compiledDsl": ["RF_CORRELATE freq=900MHz window=5s"],
  "effectsApplied": ["effect-41"],
  "jobsCreated": [],
  "proposalsCreated": [],
  "mutations": [],
  "evidencePosture": "mixed",
  "startedAt": "...",
  "completedAt": "..."
}
```

Metrics:

- Directive preview and execution latency.
- Refusal and partial-completion counts by reason.
- Effect validation failures.
- Stale plan and stale selection rejections.
- Applied/reverted/expired effect counts.
- Query and solver resource usage.
- Proposal creation, approval, refusal, and execution counts.
- Frequency of no-data and sparse-data outcomes.
- Counterfactual worlds created and discarded.

Logs must not contain raw authentication tokens, unbounded evidence payloads,
or raw IQ data.

## 19. Performance budgets

Initial targets on the current CPU-only workstation:

- Local effect validation: under 5 ms.
- Local visual preview: first frame under 50 ms.
- Coverage threshold reclassification: under 100 ms for the current regional
  grid.
- Directive preview without LLM: under 500 ms.
- Bounded graph query acknowledgment: under 250 ms.
- Progressive analytic result: first useful update under 2 seconds.
- LLM interpretation: asynchronous; never blocks local undo or camera control.
- Cesium steady frame rate: target 30 FPS with Clarktech overlays active.
- Effect ledger: bounded per investigation with explicit retention.

The deterministic compiler and visual effects should remain useful when Ollama
is unavailable. LLM interpretation is enrichment, not a runtime dependency for
known directives.

## 20. Testing strategy

### 20.1 Protocol tests

- Python and JavaScript accept and reject the same fixtures.
- Unknown protocol versions, fields, effects, styles, and target kinds fail.
- Executable content and arbitrary URLs fail.
- Plans without evidence labels or mutation declarations fail.

### 20.2 Effect runtime tests

- Preview does not mutate persistent state.
- Apply/revert restores byte-equivalent view state where practical.
- Duplicate idempotency keys do not duplicate effects.
- Expired and stale-revision effects fail closed.
- Destroying a layer removes handlers, entities, and effects.

### 20.3 Evidence tests

- Browser display values cannot override server-dereferenced authority.
- Solver output remains distinct from observation/measurement.
- Counterfactual products cannot enter the observed world.
- No-data and sparse-data confidence clamps survive LLM output.
- Missing data never creates a coverage value.

### 20.4 Visual semantic tests

- Each semantic token has normal, high-contrast, and reduced-motion forms.
- Static solver data never receives the live-observation animation.
- Contradictions remain distinguishable without color.
- Screenshots verify world separation, Reality Prism, uncertainty, and no-data.

### 20.5 Integration tests

- Cell selection -> directive preview -> authoritative resolution -> visual
  effect -> revert.
- Cell + burst -> DSL preview -> bounded execution -> causal-world comparison.
- Transmitter drag -> shadow world -> job -> counterfactual overlay -> discard.
- Capture directive -> proposal -> refusal in observe-only mode.
- Restart/reconnect restores investigation state without replaying expired
  effects.

### 20.6 Adversarial tests

- Inject script/CSS/shader strings into every effect field.
- Replay old plans against new graph and world revisions.
- Submit forged provenance and authoritative browser values.
- Request unbounded graph depth, time span, grid size, and solver resources.
- Attempt to merge counterfactual evidence into observed state.

## 21. Delivery roadmap

### Phase 0 — Protocol and authority hardening

Deliverables:

- Canonical directive and EffectPlan schemas.
- Matching Python/JavaScript validators and shared fixtures.
- Server-side RF cell resolver for the regional dataset.
- Replacement of browser-value trust with reference-based dereferencing.
- Generic preview endpoint backed by one director implementation.

Acceptance criteria:

- The existing coverage-cell explanation works through the new protocol.
- Altering the browser display value cannot alter the authoritative result.
- Unknown effects and fields fail closed.
- Current RF and SCYTHE-Web tests remain green.

### Phase 1 — Local effect runtime

Deliverables:

- `selectionModel.js`, `effectRuntime.js`, and `visualEffects.js`.
- Effect ledger, preview/apply/revert/expire lifecycle.
- Evidence isolation, target highlighting, focus camera, threshold comparison,
  uncertainty, and provenance-path effects.
- Directive panel with DSL/effect preview and undo.

Initial executable directives:

1. Isolate the measured portions of this picture.
2. Reclassify coverage at a selected threshold.
3. Reveal the authoritative value beneath this display value.
4. Long-press an assertion/cell to open its Reality Prism.

Acceptance criteria:

- Every effect is reversible.
- No effect changes evidence or authority.
- Reduced-motion and high-contrast modes preserve semantics.
- The runtime remains useful without Ollama.

### Phase 2 — Graph selection and read-only GraphOps effects

Deliverables:

- Bounded `graphOverlayLayer.js`.
- Graph node, edge, burst, time-pin, region, and path selections.
- DSL preview and read-only execution through the director.
- RF correlation, graph delta, provenance impact, and contradiction effects.
- Reuse of `graph_query_dsl.py` and `subgraph_diff.py`.

Initial executable directives:

1. Connect an RF cell to a graph node.
2. Pin two moments and generate a graph delta.
3. Reveal events preceding this burst.
4. Expose contradictions instead of consensus.

Acceptance criteria:

- Queries are bounded and revision-pinned.
- Correlation rendering is visually distinct from causal support.
- Generated DSL is always visible.
- Empty results produce an explicit no-data state.

### Phase 3 — World stack and causal holograms

Deliverables:

- Browser and backend world stores.
- Observed, reconstructed, counterfactual, and comparison worlds.
- Synchronized cameras and causal-difference overlays.
- Invariant, disagreement, assumption-gate, and falsifier effects.
- Signature causal-world interaction.

Acceptance criteria:

- World parentage and assumptions are visible.
- Counterfactual products cannot enter observed evidence.
- Removing an assumption deterministically updates world support.
- Discarding a shadow world leaves source evidence unchanged.

### Phase 4 — Sandboxed solver effects

Deliverables:

- Allow-listed job runner and content-addressed job store.
- Transmitter-move, antenna-hypothesis, sensor-removal, temporal-shift, and
  threshold jobs.
- Progressive job status and cancellation.
- Complete solver provenance for every output.

Acceptance criteria:

- Jobs have bounded CPU, memory, time, and output.
- Identical deterministic inputs reuse verified results.
- All products are labelled `COUNTERFACTUAL` or `SOLVER_OUTPUT` as applicable.
- A job failure cannot damage observed-world state.

### Phase 5 — Observation design and authority proposals

Deliverables:

- Information-gain and discriminating-observation planner.
- Receiver-placement, capture-duration, and frequency-selection overlays.
- Orchestrator proposals for tune, capture, claim review, and merge.
- Visual authority-gate and proposal-decision history.

Acceptance criteria:

- Passive observations are ranked before environmental changes.
- Proposed actions do not execute through the directive endpoint.
- Observe-only and shadow modes remain enforceable.
- Human decisions and authority requirements are explicit.

### Phase 6 — Investigation persistence and replay

Deliverables:

- Saved investigation layouts and world stacks.
- Deterministic effect replay against matching revisions.
- Comparison with prior investigations without importing conclusions.
- Exportable adjudication packages.

Acceptance criteria:

- Stale effects cannot replay against changed evidence silently.
- Prior conclusions remain historical references, not current facts.
- Exports include evidence, contradictions, assumptions, falsifiers, DSL,
  mutations, and authority history.

## 22. Recommended first vertical slice

Build one coherent slice before implementing the full directive catalog:

### “Reality Prism + threshold lens”

1. Select a coverage cell through the typed selection model.
2. Preview `explain.coverage-cell` through the generic director.
3. Resolve both derived display value and authoritative Float64 value on the
   server.
4. Show their difference, quantization transform, threshold rule, provenance,
   uncertainty, and neighboring gradient in the Reality Prism.
5. Allow a threshold slider to preview a classification-only effect.
6. Render old and proposed boundaries simultaneously.
7. Apply or revert the threshold lens locally.
8. Record the effect with no evidence mutation.

Why this slice comes first:

- It closes the current browser-authority gap.
- It proves the directive protocol and effect lifecycle.
- It is deterministic and works without an LLM.
- It yields an immediately visible Clarktech interaction.
- It exercises provenance, authority, selection, preview, apply, undo, and
  accessibility without requiring a solver job or operational proposal.

## 23. Proposed repository layout

```text
SCYTHE/
  graphops_director.py
  graphops_effect_schema.py
  graphops_evidence_resolver.py
  graphops_world_store.py
  graphops_job_runner.py
  schemas/
    graphops-directive-request-v1.schema.json
    graphops-effect-plan-v1.schema.json
  test_graphops_director.py
  test_graphops_effect_schema.py
  test_graphops_world_store.py
  scythe-web/
    directiveProtocol.js
    directiveProtocol.test.js
    directiveClient.js
    selectionModel.js
    selectionModel.test.js
    effectRuntime.js
    effectRuntime.test.js
    visualEffects.js
    visualEffects.test.js
    worldStack.js
    worldStack.test.js
    realityPrism.js
    directivePanel.js
    graphOverlayLayer.js
```

Schemas are canonical. Python and JavaScript implementations must pass the
same fixture corpus.

## 24. Decisions to make deliberately

1. **World persistence:** memory-only during early phases or DuckDB-backed from
   Phase 3 onward. Recommendation: memory-only through Phase 2, persistent and
   investigation-scoped in Phase 3.
2. **Effect transport:** request/response initially, SSE for query/job progress,
   WebSocket only when bidirectional collaboration requires it.
3. **Graph revision identity:** define a stable engine revision or snapshot hash
   before allowing replayable graph effects.
4. **Authoritative RF sampling:** decide whether the backend reads Float64
   assets directly or uses a reusable Python contract sampler. Recommendation:
   build the reusable sampler to avoid endpoint-specific binary logic.
5. **Collaboration:** defer shared live world manipulation until the single-user
   effect ledger is deterministic.
6. **LLM role:** keep compilation of known directives deterministic; use Ollama
   for interpretation, hypothesis wording, and constrained parameter extraction.

## 25. Definition of done

The Clarktech expansion is mature when an operator can:

1. Select any visible material claim and inspect its Reality Prism.
2. Issue a catalog directive and preview exactly what will execute.
3. See evidence classes, contradictions, assumptions, and freshness directly
   in the globe.
4. Apply and undo visual effects deterministically.
5. Run bounded GraphOps analysis with visible generated DSL.
6. Create and discard counterfactual worlds without contaminating observation.
7. Compare causal worlds and design a discriminating observation.
8. Convert operational intent into an authority-gated proposal.
9. Reopen an investigation with its evidence and effect history intact.
10. Explain every claim, visual state, job, mutation, refusal, and authority
    transition without relying on hidden model reasoning.

The final standard is simple:

> The globe may be astonishing, but it must remain interrogable. Every light,
> fracture, trail, world, and action must be able to answer: “Why are you here,
> what evidence supports you, what would falsify you, and what authority did you
> change?”

## 26. Implemented live sensor-to-hypergraph slice

The Eve Streamer expansion establishes the first continuous external-event
path into the Clarktech interaction surface. Normalized Suricata summaries now
travel over a loopback protobuf stream, cross a strict GraphOps ingestion
boundary, commit through WriteBus, and appear in the regional demo's separate
live network hypergraph. Nodes and edges participate in the existing typed,
revision-pinned selection, delta, provenance, and contradiction interactions.

This slice preserves the central formalities:

- no raw packet or payload material enters the GraphOps endpoint;
- controlled-feed events are synthetic, never observed;
- production Suricata summaries are observed, never solver output;
- absent network geolocation remains absent instead of becoming pseudo-geo;
- the browser layout is a visual index, not an authority surface;
- every graph mutation carries WriteBus provenance and bounded system trust.

Operational details and the deliberate Suricata cutover are codified in
[`Eve_Live_Hypergraph.md`](Eve_Live_Hypergraph.md).

The production cutover completed on 2026-08-07. Because the WSL2 guest is
NAT-isolated behind a virtual interface, the sensor runs against the Windows
host's Npcap Wi-Fi device and publishes bounded, UTC-stamped, daily-rotated EVE
metadata to the existing unprivileged WSL tailer. Production graph entities
are `OBSERVED`; the original controlled feed and service definition remain
available for deterministic rollback.

The live selection boundary retains the 32 most recently served bounded graph
snapshots. This prevents high-rate ingress from invalidating a click before its
directive arrives while preserving exact revision semantics; the resolver
rejects selections whose rendered snapshot is no longer retained.

## 27. Implemented live Three.js causal chamber

The regional demo now offers two views of the same bounded, revision-pinned
network graph: an accessible SVG topology and a Three.js causal chamber. A
single `LiveGraphController` polls the graph and Eve status endpoints, so the
renderers cannot drift onto different revisions or independently widen the
query. Three.js is pinned locally at `0.158.0`; failure to import or initialize
WebGL leaves the SVG view operational.

The chamber is topology space, never geography. It ignores graph positions for
layout, preserves retained-node coordinates between live revisions, and gives
new nodes deterministic seeds near already rendered neighbors. It therefore
avoids both IP-derived pseudo-geolocation and random two-second rearrangement.
Its visual vocabulary is evidence-bearing:

- color comes from the shared `evidenceStyles.js` registry;
- shape provides a second, non-color evidence cue;
- non-solid evidence relations use dashed or dotted line material;
- hyperedges remain first-class selectable hubs with member spokes;
- fresh nodes bloom once on arrival, while retained evidence remains still;
- reduced-motion preference removes the arrival animation.

Nodes, dyadic edges, and hyperedges dispatch the existing typed
`scythe-web:graph-selection` event with entity ID, observed time, and the exact
render revision. They therefore enter the same resolver, retained-snapshot,
provenance, contradiction, delta, and RF-correlation boundaries as selections
from the SVG panel. The 3D presentation creates no new analytic or mutation
authority.

Lifecycle controls suspend rendering when the chamber or document is hidden,
respond to container resize, and dispose controls, geometry, material, renderer,
and WebGL context on teardown. Tooltips use text content rather than interpreting
graph metadata as HTML.
