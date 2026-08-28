# The Working Instrument: A Show-and-Tell of Live SCYTHE

**Date:** August 26, 2026  
**Author:** SCYTHE Core Engineering Team  
**Category:** Clarktech, GraphOps, Command Operations, Evidence-Centered Computing  
**Audience:** Operators, research institutions, and anyone who has been watching the subsystem posts and asking what the whole machine actually is

---

The last several SCYTHE posts were close-ups.

We showed a Reality Prism that could refuse to launder a display tile into authority. We showed a live Eve hypergraph that could disagree with PeeringDB. We showed a Moon that was not Earth wearing a gray texture. We showed an MCP workbench that could name forty-eight tools without handing the browser the keys. We showed CellOps taking the same kinetic skeleton and tracking mitotic forks instead of emitters.

Those were not separate products. They were views of one working instrument.

This post is the show-and-tell. Not a roadmap. Not a pitch deck. The system as it runs: orchestrator, child instance, WriteBus, hypergraph, GraphOps, SCYTHE-Web, and the evidence contract that keeps the globe from becoming a liar.

Open the live command surface:

```text
https://neurosphere-2.tail52f848.ts.net/
```

Open the regional instrument:

```text
http://127.0.0.1:5001/scythe-web/regional-rf-demo.html
```

Open the lunar instrument:

```text
http://127.0.0.1:5001/scythe-web/lunar-ops-demo.html
```

---

## What SCYTHE Is

SCYTHE is a command operations instrument for fused RF, network, and geospatial evidence.

It is not a dashboard that paints every available number onto a globe. It is not a chatbot with a map attached. It is a revision-bearing workspace in which an operator can see a phenomenon, select it as a typed object, ask a bounded question, and receive an answer whose claims can be inspected, reversed, and falsified.

The governing rule is simple enough to print on the HUD:

```text
THE BROWSER MAY CHANGE WHAT YOU SEE.
IT MAY NOT SILENTLY CHANGE WHAT THE EVIDENCE MEANS.
```

That rule is implemented, not hoped for. Display tiles are not authority assets. GeoIP is not device location. A geodesic connector is not a cable. An LLM report is not an observation. A live graph snapshot is not the entire graph. A coverage color is not a field measurement.

When those distinctions collapse, visualization becomes theater. SCYTHE is built so they do not collapse.

---

## The Machine, End to End

A working SCYTHE stack is a small fleet of sovereign processes, not a monolith with a web UI bolted on.

```text
Operator browser
        |
        v
scythe_orchestrator.py                 // identity, instance lifecycle, reverse proxy
        |
        |  /scythe/i/<instance_id>/...
        v
rf_scythe_api_server.py                // child instance: graph, RF, Socket.IO, GraphOps
        |
        +--> WriteBus                  // single-writer commit coordinator
        |         |
        |         v
        |    HypergraphEngine          // entities, flows, traces, geospatial envelopes
        |
        +--> GraphOpsDirector          // typed directives -> EffectPlan v1
        |         |
        |         +--> RFCellEvidenceResolver
        |         +--> GraphSelectionResolver
        |         +--> LunarEvidenceResolver
        |
        +--> MCPHandler                // JSON-RPC tools, guarded mutations
        |
        v
SCYTHE-Web                             // read-only instrument boundary
        |
        +--> Cesium globe / world stack
        +--> live hypergraph viewports
        +--> Reality Prism / Lunar Prism
        +--> contextual MCP workbench
```

The orchestrator is the supervisor. Each child instance is a sovereign analytic workspace: its own hypergraph, inference ledger, behavioral model, port, PID, and session history. There is no shared memory between instances and no cross-contamination of graph state. Stable URLs of the form `/scythe/i/<instance_id>/...` keep Funnel, reverse-proxy, and operator bookmarks pointed at identity rather than a lucky port.

Docker Compose can bring the same shape up with host networking so dynamic child ports remain reachable. Ollama sits beside the orchestrator for local interpretation. Optional live integrations — AIS, satellite, FusionAuth, TAK-ML — attach through environment, not through the browser inventing credentials.

The static preview still exists. `command-ops-visualization.html` can be served with a local HTTP server and the globe shell will load. Live telemetry, authentication, graph writes, PCAP workflows, and instance bootstrap still require the backend. That is intentional. The pretty page is not the instrument.

---

## WriteBus: One Writer, Many Claims

Every serious graph system eventually discovers the same failure mode: two well-meaning ingest paths, two slightly different entity IDs, one silent overwrite, and a visualization that now believes a guess.

SCYTHE's answer is WriteBus.

WriteBus is the canonical single-writer commit coordinator. Ingest paths do not poke the hypergraph. They propose. The bus applies graph mutation, room persistence, event publication, and audit as an ordered commit with explicit status:

```text
PENDING_GRAPH
GRAPH_APPLIED
ROOM_PERSISTED | ROOM_SKIPPED
BUS_PUBLISHED  | BUS_SKIPPED
AUDITED        | AUDIT_SKIPPED
COMMITTED
FAILED_PARTIAL
REJECTED
```

A kernel scope exists so privileged commit code can run without opening a general mutation API to the rest of the process. Kernel violations are permission errors, not log lines.

This is why Eve summaries, PCAP sessions, RF observations, and operator graph edits can share a picture without sharing a write path. The picture is allowed to be dense. The commit is not allowed to be casual.

---

## How Evidence Enters the Picture

SCYTHE does not ingest "data." It ingests typed observations with an authority class, a bound, and a refusal to carry payload that does not belong on the graph surface.

### Network, from Suricata to selectable topology

The live network path is deliberately narrow:

```text
Suricata eve.json
        |
        v
Eve Streamer normalization and bounded replay
        |
        v
protobuf / gRPC on loopback
        |
        v
orchestrator /api/graphops/eve/events
        |
        v
active child WriteBus
        |
        v
HypergraphEngine
        |
        v
bounded graph API
        |
        v
scythe-web/liveHypergraphView.js
```

Only the protobuf event summary crosses into GraphOps. Unknown fields, raw packet bytes, and payload fields are rejected. Each accepted event becomes two `network_host` nodes and one stable five-tuple `network_flow` edge. Ordinary Suricata types are `OBSERVED`. Records beginning with `test` or `synthetic` remain `SYNTHETIC`. Network entities declare `geospatialAuthority: ABSENT`. The 2D layout is topology. It is labelled `NOT GEOLOCATION` because it is not geolocation.

The live view also refuses a second lie: that whatever fits on screen is whatever exists.

```text
DETECTED  // 506 NODES // 7495 EDGES
DISPLAYED // 200 / 506 NODES // 300 / 7495 EDGES
```

Snapshots are bounded server-side. The child retains recent served revisions so a click can resolve against the graph the operator actually saw. Stale revisions are rejected. GraphOps does not silently rebase a selection onto the newest live graph.

### Packets, from capture to ledgered session

PCAP is a different sensor, with a different honesty contract:

```text
PCAP file
        |
        v
deterministic sessionization (5-tuple + time bucket)
        |
        v
SESSION subgraph
        |
        v
ledger registration
        |
        v
HypergraphEngine  (source = pcap_ingest)
```

PCAPs are not evidence until sessionized. Sessions are not knowledge until ledgered. Knowledge is not safe until exhaustion is enforced. Session IDs are deterministic: the same capture produces the same sessions. Optional GeoIP and protocol-intel scoring can enrich; they cannot promote an address into a building.

### RF, from solver output to coverage cell

RF does not arrive as a pretty heatmap and then get reverse-engineered into meaning. Regional coverage is a contract-gated dataset: checksum-verified tiles, declared encoding, authority asset distinct from display asset, NTIA ITM path-loss as solver output. The browser samples. The server resolves. The Reality Prism reports both the authoritative value and the display delta, then prints the boundary in operator-facing type:

```text
GRAPHOPS // COMPLETED // SOLVER-BACKED

REALITY PRISM // RF CELL
BASIC TRANSMISSION LOSS // 142.0998 dB
DISPLAY // 142.1033 dB
DISPLAY - AUTHORITY // 0.003500 dB
DECISION // COVERED
THRESHOLD // 145 dB LTE
SOLVER // NTIA ITS Irregular Terrain Model
BOUNDARY // DISPLAY VISUALIZATION IS NOT AUTHORITATIVE
```

A color changed. The evidence did not.

---

## GraphOps: The Globe as a Hypothesis Compiler

GraphOps is the reason the globe can be asked a question.

The browser does not send a prompt and hope. It sends a typed Directive Request: protocol version, allow-listed directive, revision-bearing selection, idempotency key, preview or execute. The director compiles an EffectPlan. Effects are reversible, TTL-bounded, and forbidden from claiming authority impact they do not have.

The allow-list is small on purpose:

```text
explain.coverage-cell
reclassify.coverage-threshold
correlate.rf-cell-graph
compare.graph-delta
trace.provenance-impact
expose.contradictions
compare.causal-worlds
explain.lunar-location
```

Those verbs are enough to do the work that matters in a fused picture:

- explain why a cell is red without promoting interpolation into measurement;
- reclassify coverage by threshold without rewriting solver values;
- correlate an RF cell with graph context without pretending correlation is cause;
- diff two graph revisions instead of overwriting memory;
- trace provenance until the trail actually ends;
- expose contradictions instead of averaging them away;
- stack competing causal worlds with assumptions, predicted observations, and falsifiers;
- resolve a lunar click in a Moon-fixed frame instead of WGS84 with a grey texture.

The World Stack is the operator-visible consequence. Observed world `W0` stays pinned to a graph revision and clock. Competing worlds declare their evidence class, their assumptions, what they predict, and what would kill them. The boundary is rendered as text because hiding it would be a product decision, not a visual one.

```text
CAUSAL WORLD STACK // PREVIEW
W0 OBSERVED // <graphRevision> // <clockId>
W1 // BENIGN TERRAIN // supported
W2 // ADVERSARIAL MINIMUM // hypothesized
BOUNDARY // DISPLAY IS NOT AUTHORITY
```

This is Clarktech in one sentence: every dramatic effect needs an evidence class, every answer needs provenance, every simulation needs a boundary, and every unknown needs permission to remain unknown.

---

## InfraFlow: The Network Is Allowed to Disagree

Once live hosts, GeoIP, BGP, and facility records share a screen, the temptation is to produce a single "true" map of the internet. SCYTHE refuses.

InfraFlow partitions infrastructure evidence and keeps the partitions visible:

| Layer | What it is | Authority |
|---|---|---|
| Observed flows | Live graph edges, aggregated | `OBSERVED` traffic presence; not an observed route |
| Domains | ASN ownership, GeoIP centroids | `INFERRED` |
| RIPE RIS Live | Prefix-relevant control-plane messages | `CONTROL_PLANE_OBSERVATION` |
| PeeringDB | Versioned, ASN-bounded self-report | `PEERINGDB_SELF_REPORTED` |
| Modeled path candidates | Empty in production | Not promoted from the legacy demo model |
| Contradictions | Deterministic unresolved disagreements | Findings, not consensus |
| Cesium connectors | Uncertain regions and geodesic arcs | `DISPLAY_ONLY_NOT_ROUTE` |

A host can be live, inferred, declared, and contradicted at the same time. Reachability badges do not overwrite GraphOps purpose colors. Control-plane change is not data-plane proof. A facility record is not a traceroute. The globe can show association at a glance without claiming fiber, exchange, relay, or device location.

That disagreement is the product.

---

## SCYTHE-Web: A Read-Only Instrument Boundary

`scythe-web/` is not "the frontend." It is a browser-native instrument boundary: a read-only consumer of datasets that have already passed the Global Propagation Data Contract v1 gate.

It has no propagation model. It has no random fallback. It will not guess a binary tile layout. Compact encodings require checksum-bound metadata. Complex optical quantities must expose the representation they declared. Lunar coordinates use an explicit Moon-fixed frame so Earth ellipsoids cannot be reused by accident.

The regional RF demo is the master terrestrial viewport. It is a dual-panel cockpit: live hypergraph on one side, GraphOps directives on the other, both independently resizable, both persistent across reload. The hypergraph itself is not one view. It is nine prisms over the same revision:

1. **3D Causal Chamber** — Three.js gravity-well topology, SVG fallback if WebGL is absent  
2. **2D Accessible** — revision-pinned SVG  
3. **Location Estimates** — GeoIP centroids with uncertainty rings  
4. **Infrastructure Lens** — InfraFlow domain and flow cards  
5. **Graph Explorer** — truthful available / matched / returned counts  
6. **Autopilot** — sentinel patrol without sentinel authority  
7. **Semantic** — FAISS similarity that will admit an empty corpus  
8. **Spectrum** — RF bridge and FFT summary  
9. **Events** — bounded tactical history  

The legend is an epistemic contract. Edge length is layout separation. It is never latency, fiber, or transit speed.

The lunar demo is the first extra-terrestrial world in the same stack: token-free Moon globe, polar reference grid, typed surface selection, sparse-evidence Lunar Prism. Same director. Same EffectPlan. Different celestial body, named as such.

---

## Models May Interpret. They May Not Govern.

SCYTHE uses language models. It does not appoint them as officers.

Local **ASK OLLAMA** remains local. **ASK CLOUD // FULL FIDELITY** is an explicit disclosure path, not a convenience toggle. The browser submits a question, a pinned selection, a trace evidence ID, and an acknowledgement. It cannot supply or edit the capsule. The server resolves an immutable graph revision, bounded measurements, and a revision-bound evidence capsule. Secrets, raw packets, unbounded graph state, and directive authority stay out.

After generation, prose is validated deterministically:

- interface GeoIP cannot become a physical itinerary;
- differential RTT cannot become segment propagation time;
- a last responding TTL cannot become "the path ended here";
- failure to observe a tunnel cannot become proof that no tunnel exists;
- uncorroborated GeoIP is confidence-capped, not narratively upgraded.

The model is interpretive only. It cannot run probes, mutate the graph, execute directives, or promote an inference into an observation. The receipt reports capsule identity, hashes, scope, withheld tests, and the authority boundary. The capsule and the cloud credential never return to the browser.

The contextual MCP workbench follows the same pattern. Forty-eight server tools exist. The browser sees four bounded windows — Autopilot, Semantic, graph operations, RF evidence — as a fixed read-only projection. Guarded capabilities remain guarded. A panel can describe an action. It cannot bypass proposal and approval.

```text
Visible phenomenon
        |
        v
Typed, revision-bearing selection
        |
        v
Contextual workbench / GraphOps directive
        |
        v
Server-owned evidence
        |
        v
Bounded receipt
        |
        v
Reversible view, unchanged authority
```

---

## Other Worlds, Same Skeleton

The instrument is not only RF and packets.

**Biohub CellOps** maps the same tracking skeleton onto embryonic centroids: speculative fast/slow matching, Kalman-plus-tissue-flow prediction, and a SubmissionGuard that treats cyclic lineage, impossible mitotic forks, and NaN coordinates as contract violations rather than leaderboard surprises. The physics changed units. The refusal to invent continuity did not.

**Unity** exists as a generated Unity 6 player for Linux and Windows, built from the C# in `UnityProject/`. It is another viewport onto command geometry, not a second source of truth.

**Route ecology, host cognition, epistemic confidence, replay, and counterfactual engines** hang off the same graph and clock. They are allowed to be speculative. They are required to say so.

Diversity of surface is not diversity of authority. There is one WriteBus per instance. There is one revision per selection. There is one place a claim can become a fact, and the globe is not that place.

---

## How to Hold It

For a static shell:

```bash
python3 -m http.server 8080
# http://localhost:8080/command-ops-visualization.html
```

For the working instrument:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python3 scythe_orchestrator.py --host 0.0.0.0 --port 5000
```

Docker:

```bash
cp .env.example .env
docker compose up -d
# http://localhost:5001
```

The orchestrator serves `rf_scythe_home.html`, spawns isolated children from `rf_scythe_api_server.py`, and routes `/scythe/i/<instance_id>/...`. Full function depends on bootstrap, identity, `/api/*`, `/stream/*`, and Socket.IO. If those are absent, the UI should degrade. It should not hallucinate a live graph.

---

## What to Notice When You Look

If you only click around for a minute, notice these things. They are the show.

1. **A coverage cell can explain itself.** The Prism shows solver value, display value, delta, threshold, and the sentence that display is not authority.
2. **A host can be selected against the revision you saw.** If that revision has expired, the system says stale. It does not quietly retarget.
3. **Detected and displayed are different numbers.** Density is not completeness.
4. **InfraFlow will not pick a winner.** Self-report, control plane, and data plane can remain in tension on purpose.
5. **Private and multicast addresses are not dumped on the equator.** Unlocated stays unlocated, or it is placed with an explicit non-geolocation contract.
6. **The Moon is a body, not a texture.** The click is Moon-fixed. Earth math is not reused by default.
7. **Cloud answers come with a receipt.** If a sentence cannot survive the epistemic checks, it does not survive as a claim.
8. **Autopilot can patrol without ruling.** Suggestions are visible. Authority stays with the operator and the bus.

That is the treat. Not that SCYTHE can draw a lot of objects. Plenty of systems can draw objects. The working system is the one that can draw them, name their evidence class, keep their disagreements, and still let an operator act.

---

## Status

SCYTHE is a working command instrument: multi-instance orchestration, WriteBus-backed hypergraph, live Eve and PCAP ingest, solver-backed RF, GraphOps directives, InfraFlow, Full-Fidelity interpretation, MCP workbench, terrestrial and lunar SCYTHE-Web worlds, and optional Unity and CellOps surfaces.

It is built for operators who need density without surrendering spatial intuition, and for research environments where a compelling visual explanation is not sufficient. A result must remain inspectable, repeatable, and falsifiable.

The globe is allowed to be beautiful.

It is not allowed to become the evidence.


Updated todo list

I’ll read the Clarktech design notes so the answer is grounded in where SCYTHE already is, not a generic wishlist.

Searched for files matching `**/ClarkTech*`, 1 match

Read , lines 1 to 80

Read , lines 1 to 100

Clarktech already has the rare thing: a globe that is allowed to refuse. If I had creative freedom, I would not add more spectacle. I would make **refusal compositional** — a system that can hold competing worlds, kill them in public, and compile the next observation that would actually decide something.

The direction I would take is not “more AI on the map.” It is **a causal court with a field kit**.

## 1. Stop stacking worlds. Start holding trials.

`compare.causal-worlds` already builds four hypotheses and withholds the verdict. That is the most Clarktech sentence in the codebase.

I would make that the operating system.

Every interesting selection would open a **case**, not a tooltip:

- `W0` remains the observed revision and clock.
- Each world is a defendant: assumptions, predicted observation, falsifier, cost-to-test.
- The UI’s job is not to pick a winner. It is to keep the docket honest.
- When a world dies, it gets an **obituary receipt**: what killed it, which evidence class did the killing, what invariant survived.

No synthetic consensus. Ever. The prize is the **invariant ledger** — claims that remain after every surviving world has been stressed. That ledger would be the only thing allowed to look “settled,” and it would still carry a falsifier.

Clarktech’s aesthetic should become: *beautiful explanations go on trial*.

## 2. Compile the next measurement, not the next paragraph.

GraphOps can already explain a red cell and name a falsifier. I would make **observation compilation** the signature directive.

Something like:

```text
distinguish.worlds
  -> cheapest observation that splits W1 from W2
  -> collection task
  -> expected evidence class
  -> what remains undecidable even if it succeeds
```

Not “go ping this host.” A bounded campaign:

- RF: where to stand, what threshold, what would count as coverage vs artifact
- Network: which hop to re-measure, how many repeats, what timing claim is still illegal
- Lunar: which SPICE/LOLA authority is still absent, so the task is “register terrain,” not “guess slope from a PNG”
- Operator: what must be approved, what the browser is forbidden to trigger

The globe becomes a **proposal engine for reality**, with the same EffectPlan discipline you already have: preview, execute, reversible, no authority impact unless the bus says so.

That is the Clarktech product hiding in the current HUD copy.

## 3. Make clocks first-class worlds.

The notes already point at clock calibration evidence. I would treat **time as a celestial body**.

Every sensor, Suricata feed, GeoIP lookup, PeeringDB retrieval, and operator click would carry a `clockId`. Correlation would never be “these two things happened.” It would be:

```text
SAME CLOCK
COMPARABLE CLOCKS // OFFSET BOUND
INCOMPARABLE CLOCKS // TEMPORAL_EVIDENCE ABSENT
```

A “network burst caused this RF gap” world would be illegal until the clocks are comparable. The visualization would show **time shear**: two events that look simultaneous in layout space but are temporally incomparable.

That single move would prevent half the lies geospatial intelligence likes to tell.

## 4. Absence as geometry.

You already print `TEMPORAL_EVIDENCE: ABSENT` instead of writing plausible prose. I would promote absence to a renderable layer.

- Unlocated hosts: not equator-dumped, not hidden — an **unlocated dock**
- Missing RF support: a hole with a hash, not a fade
- Lunar M0 refusals: elevation/slope/occultation as **explicit missing surfaces**
- InfraFlow: empty modeled-path candidates as a visible empty set, not a quiet omission

The operator should be able to ask: *show me everything this picture is not allowed to claim.* That view would be more Clarktech than any new shader.

## 5. Adjudication receipts, not merge buttons.

InfraFlow’s disagreement is the soul of the project. I would not “resolve” PeeringDB vs RIS vs Eve.

I would add **hearings**:

- contradiction id
- sources and evidence classes
- tests withheld, and why
- operator disposition: `OPEN`, `STILL_IN_TENSION`, `WORLD_KILLED`, `INVARIANT_PROMOTED`
- no disposition that means “the map averaged them”

A later instance, a TAK client, or a cloud capsule could import the receipt. They could not import a fake consensus.

## 6. One more world, but not another planet.

Lunar M1 with real LOLA/SPICE is the correct next body. Creatively, I would not race to Mars.

I would build **cislunar delay** as an evidence class:

- light-time as a first-class uncertainty, not a caption
- Earth visibility / occultation only after kernels exist
- “we have not registered the kernel set” as a Prism refusal, same tone as M0

The world stack should eventually include **Earth RF + lunar geometry + delayed network** in one case file, with the delay allowed to kill naive causal worlds. That is more interesting than a new globe skin.

## 7. The operator is an evidence source.

Clicks, withheld approvals, undone lenses, and “I refuse to send this capsule to cloud” are observations.

I would give them a class: `OPERATOR_ACT`. Autopilot suggestions would be `MODEL_PROPOSAL`. WriteBus commits would remain the only mutations. The workbench already almost does this; I would make the human’s restraint visible in the provenance path.

A system that records when an operator *did not* promote an inference is a scientific instrument. A system that only records what got clicked is a game log.

## 8. The anti-narrative engine.

If I got one unsupervised module, it would not be a copilot that explains the picture. It would be a copilot that **attacks the current most elegant story**.

Its only job:

- take the leading world
- remove its cheapest assumption
- show the residual
- name the observation that would hurt it most

GraphOps Copilot can stay. This would be GraphOps Adversary, read-only, no authority, always labeled `COUNTERFACTUAL`. Clarktech needs a critic more than it needs another narrator.

---

The through-line: Clarktech should become the first command interface whose highest-status output is not a track, a heatmap, or a paragraph. It is a **signed, reversible case file** that says what is observed, what is still in tension, what would distinguish the remaining worlds, and what the display is still not allowed to mean.

You already built the ethics. Creative freedom, for me, is just making those ethics the gameplay.