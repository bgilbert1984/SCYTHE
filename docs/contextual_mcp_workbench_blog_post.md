# From Visualization to Scientific Instrument: SCYTHE's Contextual MCP Workbench

**Date:** August 14, 2026  
**Author:** SCYTHE Core Engineering Team  
**Audience:** National Laboratory Research Institutions  
**Category:** GraphOps, Scientific Computing, Human–Machine Teaming, Evidence-Centered Operations

---

Scientific visualization becomes substantially more valuable when an operator
can move from a visible anomaly to the exact computational capability needed to
investigate it—without losing provenance, widening the evidence scope, or
silently authorizing an action.

That is the purpose of SCYTHE's latest Clarktech advancement: a contextual
Model Context Protocol workbench embedded directly within the live RF and
network hypergraph instrument.

The workbench connects SCYTHE-Web to the considerable machinery already
running inside a SCYTHE server: live graph analytics, GraphOps Copilot,
autonomous observation patrols, RF instrumentation, semantic memory, tactical
event history, and guarded control capabilities. It does so through a narrow,
server-owned interface rather than exposing a general-purpose tool console to
the browser.

The result is a transition from dashboard to scientific instrument.

```text
Visible phenomenon
        |
        v
Typed, revision-bearing selection
        |
        v
Contextual workbench panel
        |
        v
Fixed read-only MCP projection
        |
        v
Bounded evidence + tool receipt
        |
        v
Revision-retaining GraphOps investigation
```

Open the instrument at:

```text
http://127.0.0.1:5001/scythe-web/regional-rf-demo.html
```

## Why This Matters to Research Institutions

National laboratories routinely work across boundaries that conventional
analytics products flatten together:

- measurement versus model output;
- data-plane behavior versus control-plane observation;
- self-reported infrastructure versus independently observed infrastructure;
- live state versus a retained historical revision;
- interpretation versus execution authority;
- locally held evidence versus deliberately disclosed external evidence.

SCYTHE treats these distinctions as part of the data model and interaction
contract. A model can interpret evidence, but it cannot promote an inference
into an observation. A visualization can focus attention, but it cannot alter
the authoritative value. An MCP tool can describe an available action, but a
browser panel cannot bypass the orchestrator's proposal and approval boundary.

This is particularly important in research environments where a compelling
visual explanation is not sufficient. A result must remain inspectable,
repeatable, and falsifiable.

## Forty-Eight Capabilities, Four Bounded Windows

The active SCYTHE server currently exposes 48 MCP tools:

| Capability family | Tool count | Representative functions |
|---|---:|---|
| Core graph operations | 24 | hot entities, recent edges, scope statistics, temporal scrubbing, graph export |
| GraphOps Copilot | 7 | bounded investigation, directive compilation, entity parsing, coverage explanation |
| GraphOps Autopilot | 5 | patrol status, suggestions, observations, analyst cards, feedback |
| RF evidence and control | 7 | bridge status, spectrum summaries, RF queries, correlation, guarded tuning and capture |
| Semantic memory | 5 | entity embedding, similarity search, anomaly matching, identity stitching, clustering |

SCYTHE-Web does not present those tools as a flat command catalog. The new
workbench organizes the read-only portion into four operator-facing panels:

### AUTOPILOT

The Autopilot panel exposes the GraphOps Sentinel's runtime state, queued
suggestions, and low-confidence observation log.

This makes autonomous patrol visible without granting it autonomous authority.
An operator can inspect why a pattern was raised, select implicated entities,
and carry the finding into a GraphOps investigation. Analyst feedback remains
a guarded capability because it can affect downstream training and TAK-ML
workflows.

```text
AUTOPILOT // OK // 3/3 READ TOOLS
MCP // graphops_autopilot_status
MCP // graphops_suggestion_queue
MCP // graphops_observation_log

GUARDED CAPABILITY
graphops_submit_feedback // ANALYST REVIEW REQUIRED
```

### SEMANTIC

The Semantic panel provides bounded access to SCYTHE's FAISS-backed semantic
memory. When a graph entity is selected and the semantic corpus contains
vectors, the panel can retrieve behaviorally similar entities while retaining
the selected graph revision.

The current system also handles the scientifically honest empty state. If
semantic memory contains zero vectors, it reports that fact and does not
repeatedly invoke an embedding model to manufacture an appearance of insight.
Admission of a selected entity into semantic memory is shown as a proposal
requiring an explicit corpus decision.

This creates a foundation for questions such as:

- Have we observed a structurally similar route anomaly before?
- Which entities share a behavioral signature across collection sessions?
- Is the selected event near an established cluster or genuinely novel?
- What evidence distinguishes the nearest analogue from the present case?

Similarity remains a retrieval aid. It is not identity, attribution, or
causality.

### SPECTRUM

The Spectrum panel exposes the RF bridge state, the latest bounded FFT summary,
and recent RF observations. Raw IQ is not returned through this interface.

```text
MCP // rf_bridge_status
MCP // rf_spectrum_snapshot // BOUNDED FFT SUMMARY
MCP // rf_observations_query // OBSERVED

GUARDED CAPABILITIES
rf_tune            // ORCHESTRATOR APPROVAL REQUIRED
rf_capture_control // ORCHESTRATOR APPROVAL REQUIRED
```

This distinction supports laboratory workflows in which observation and
instrument control may have different authorization, safety, scheduling, and
configuration-management requirements. The display can show that tuning or
capture is possible without making the visualization itself a control surface.

### EVENTS

The Events panel provides a compact operational view of graph metrics, highly
connected entities, recent edges, and the bounded neighborhood of the selected
entity.

Entity references returned by MCP become selectable objects. Selecting one
focuses the same entity across the live hypergraph, infrastructure lens, graph
explorer, and GraphOps dialog. The system therefore connects multiple
representations without creating separate, conflicting selection states.

Event ingestion and TAK-ML execution remain guarded proposals.

## A Deliberately Narrow MCP Boundary

The browser does not receive a generic `tools/call` facility. Instead, it sends
one of four panel names plus an optional typed graph selection to the
orchestrator:

```json
{
  "panel": "events",
  "selection": {
    "kind": "graph-node",
    "entityId": "host:203.0.113.8",
    "graphRevision": "graph-a"
  }
}
```

The server—not the browser—chooses the exact MCP tools and arguments associated
with that panel. Unknown request fields and unsupported panels are refused.
Returned dictionaries, arrays, strings, and nesting depth are bounded before
they reach the visualization.

This architecture provides several useful properties:

1. **Capability confinement.** Adding a tool to the MCP registry does not
   automatically make it browser-callable.
2. **Argument ownership.** The server controls time windows, collection limits,
   similarity counts, and whether FFT bins are included.
3. **Mutation isolation.** Direct execution of mutating MCP tools remains
   disabled; guarded capabilities are descriptive proposals only.
4. **Reduced UI authority.** Browser code cannot silently transform a
   read-oriented scientific display into an administration console.
5. **Auditable evolution.** Each new exposed capability requires an explicit
   server-side mapping and test.

This is defense in depth, not a claim of formal security certification. A
production deployment still requires institutional authentication,
authorization, network segmentation, audit retention, software assurance, and
configuration control appropriate to its environment.

## Revision Retention Without Temporal Overclaiming

Graph selections carry the revision that the operator actually saw. When a
workbench result is opened in GraphOps, that selection revision is retained in
the investigation tab.

MCP results, however, are live observations unless a particular tool returns a
historical snapshot. SCYTHE states this difference directly:

```text
BOUNDARY // LIVE OBSERVATIONAL MCP RESULTS;
THE SELECTION REVISION IS RETAINED,
BUT RESULTS ARE NOT HISTORICAL SNAPSHOT CLAIMS
```

This small distinction prevents a common analytical error: displaying a
historical selection beside a current metric and implying that both describe
the same instant.

For laboratory reproducibility, the next logical refinement is a receipt that
records the selected graph revision, MCP tool version, normalized arguments,
result hash, server generation time, and any retained dataset identifiers. The
current workbench establishes the interaction boundary needed for that richer
receipt.

## One Selection, Several Scientific Representations

The workbench participates in SCYTHE's shared selection fabric.

```text
                     +--> 3D causal chamber
                     |
Selected entity -----+--> 2D accessible topology
                     |
                     +--> Cesium location estimate
                     |
                     +--> InfraFlow evidence layers
                     |
                     +--> Graph Explorer neighborhood
                     |
                     +--> Contextual MCP workbench
                     |
                     +--> Revision-bearing GraphOps tab
```

An Autopilot finding can therefore become a highlighted graph entity. A hot
entity returned by the Events panel can become a bounded host investigation.
A semantic analogue can be selected and compared without copying identifiers
between interfaces. A Spectrum observation can be interpreted alongside an RF
cell while its evidence class remains visible.

The visual effect is useful, but the more consequential outcome is analytical
continuity: each representation refers to the same typed object rather than an
informal label reconstructed by the user.

## Building on the InfraFlow and Full-Fidelity Foundation

This release builds on SCYTHE's recent infrastructure and reasoning work:

- normalized Suricata summaries enter a bounded live hypergraph;
- adaptive relevance separates detected graph size from displayed graph size;
- host liveness remains independent of analytical purpose;
- traceroute measurements remain distinct from inferred GeoIP geography;
- PeeringDB declarations and RIPE RIS control-plane observations remain
  parallel to the measured data plane;
- contradictions are preserved rather than averaged away;
- Full-Fidelity Evidence Capsules can disclose exact bounded evidence to an
  explicitly selected cloud model while excluding credentials, raw packet
  payloads, unrelated files, and directive authority;
- local Ollama remains available for private, interpretive investigations.

The contextual workbench gives those layers a common operational doorway. It
does not collapse them into a single confidence score or a single AI answer.

## Verification

The implementation was tested at the server, module, browser, and live-service
levels:

```text
GraphOps and server tests           56 passed
SCYTHE-Web unit tests               85 passed
Packaged Chromium integration tests 3 passed
Live Chromium acceptance            passed with no console errors
Live workbench panels                AUTOPILOT / SEMANTIC / SPECTRUM / EVENTS
Mutation path                        unavailable through workbench endpoint
```

The browser integration test verifies more than rendering. It confirms that:

- the selected entity and graph revision enter the workbench request;
- bounded MCP evidence appears in the panel;
- guarded capabilities are visibly separated;
- `OPEN IN GRAPHOPS` transfers the evidence into an investigation;
- the original graph revision survives that transition.

## Research Directions

For national laboratory environments, this architecture opens several
practical research directions.

### Reproducible Instrument Receipts

Bind every workbench projection to content hashes, tool versions, model
identities, clock sources, and retained graph revisions. This would allow an
investigation to be replayed or compared across software baselines.

### Facility-Local Capability Profiles

Define institution-controlled profiles for air-gapped, restricted-network,
and externally connected deployments. The same user interface could expose a
different allow-list without changing evidence semantics.

### Multi-Instrument Correlation

Extend the selection fabric across RF receivers, network sensors, AIS,
geospatial layers, optical instruments, and simulation products. Compatibility
checks should refuse cross-domain correlation when time, location, calibration,
or provenance requirements do not align.

### Human–Machine Teaming Studies

Measure whether contextual capability presentation reduces operator error,
time-to-falsifier, and unsupported causal conclusions compared with a flat tool
catalog or unconstrained conversational interface.

### HPC and Campaign-Scale Analysis

Keep interactive selection and provenance local while routing approved,
content-addressed analytical jobs to institutional clusters. Results should
return as evidence-bearing artifacts, not detached prose.

### Formal Policy Enforcement

Move the current allow-list and proposal boundaries toward declarative policy:
tool authority, data classification, operator role, instrument state, network
destination, and required approvals could all become machine-verifiable inputs
to execution decisions.

## The Larger Principle

The central advance is not that SCYTHE can call 48 tools.

It is that a researcher can select a phenomenon, see only the capabilities that
make sense for that context, inspect the evidence each capability returns, and
carry the result into a continuing investigation—while the system preserves
the boundaries between observation, inference, interpretation, and action.

For research institutions, that boundary is not friction. It is part of the
instrument.

SCYTHE's visual language now reaches the server's deeper machinery. The
machine has become more capable, but its claims have not become less
accountable.

