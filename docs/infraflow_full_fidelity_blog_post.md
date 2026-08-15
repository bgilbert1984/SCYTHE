# The Network Learned to Disagree: SCYTHE InfraFlow and Full-Fidelity GraphOps

**Date:** August 13, 2026  
**Author:** SCYTHE Core Engineering Team  
**Category:** Clarktech, GraphOps, Network Operations, Evidence-Centered Computing

---

SCYTHE's live hypergraph began as a way to see normalized network activity.
Today it has become something more ambitious: an evidence-partitioned
infrastructure instrument that can preserve disagreement between the data
plane, control plane, declared infrastructure, inferred geography, and model
interpretation.

The latest Clarktech expansion connects live Suricata summaries to a bounded
hypergraph, enriches observed hosts without turning estimates into facts,
projects network activity onto Cesium, attaches versioned PeeringDB and RIPE
RIS evidence, and gives GraphOps an explicitly acknowledged path to Ollama
Cloud.

The memorable part is not that an LLM can discuss a traceroute. The memorable
part is that SCYTHE now decides—deterministically—what that discussion is
allowed to mean.

Open the operational instrument at:

```text
http://127.0.0.1:5001/scythe-web/regional-rf-demo.html
```

## A Live Network, Without the Raw-Packet Firehose

The production path begins with Suricata's `eve.json` and ends in a selectable
SCYTHE-Web topology:

```text
Suricata eve.json
        |
        v
Eve Streamer normalization and bounded replay
        |
        v
protobuf / gRPC transport
        |
        v
SCYTHE orchestrator and child WriteBus
        |
        v
HypergraphEngine
        |
        v
bounded graph API and live SCYTHE-Web topology
```

The pipeline survives file rotation, handles end-of-file tailing correctly,
and replays a bounded recent window after startup. Ordinary Suricata summaries
enter as `OBSERVED`; controlled `test_*` records remain `SYNTHETIC`. Raw packet
payloads are not exposed to the graph surface.

The live view separates what exists from what fits on screen:

```text
DETECTED // 506 NODES // 7495 EDGES
DISPLAYED // 200 / 506 NODES // 300 / 7495 EDGES
```

Adaptive relevance selects the displayed subgraph using activity, selected
context, explicit signals, new arrivals, network diversity, and stable
context. A display limit no longer masquerades as an observation limit.

Host liveness is also independent of visual purpose. Round-robin ICMP probes
produce a small green, red, or unknown badge while the node body retains its
GraphOps purpose color. Activity and reachability can now coexist without one
overwriting the other.

## The Globe Stopped Shouting

As infrastructure evidence accumulated, many hosts and network domains landed
on identical or nearby GeoIP estimates. Rendering every label independently
turned useful context into a knot of overlapping characters.

SCYTHE now applies adaptive Cesium screen-space clustering. Two or more nearby
display entities collapse into a circular marker whose number reports the
unique observed hosts represented by the cluster. As the operator zooms in,
the cluster separates automatically.

Hovering the marker reveals a bounded host list with:

- graph host identity;
- inferred network organization;
- inferred place context;
- retained evidence class;
- the network domains represented by the marker.

The tooltip carries an important boundary:

```text
BOUNDARY // SCREEN-SPACE PROXIMITY;
ASN OWNERSHIP AND GEOIP LOCATION REMAIN INFERRED
```

Two marks sharing pixels do not prove two devices share a building, city, or
physical route. The visualization has learned to reduce clutter without
quietly increasing authority.

## InfraFlow: Four Layers That Refuse to Collapse

The Infrastructure Lens projects the bounded live graph into separate evidence
layers:

| Layer | Source | Epistemic posture |
|---|---|---|
| Data plane | Eve flows and bounded traceroute | Observed at the SCYTHE vantage |
| Control plane | RIPE RIS Live | Observed at a named collector vantage; non-authoritative for data-plane routing |
| Declared infrastructure | PeeringDB | Self-reported networks, policies, IXs, and facilities |
| Geographic/network enrichment | Local GeoIP and prefix databases | Inferred, versioned, and uncertainty-bearing |

The old embedded demonstration adjacency model is disabled. SCYTHE does not
promote a plausible AS path into an observed route.

PeeringDB ingestion is bounded to ASNs already present in the operator's
environment. Records retain retrieval time, record update time, authentication
posture, authority, and a normalized dataset SHA-256. Facility co-location and
shared IX presence remain declarations; neither establishes traffic or
adjacency.

RIS Live announcements and withdrawals remain parallel to the traceroute hop
graph. Each normalized record includes collector identity, collector receive
time, peer, prefix, AS path, origin ASN, message identity, and explicit
collector-vantage authority. Raw BGP bytes are not requested.

## Control-Plane Memory With a Clock

RIS observations are now persisted in a bounded SQLite WAL store rather than
disappearing with the process. The operational limits are explicit:

```text
RETENTION // 7 DAYS
MAXIMUM // 50,000 NORMALIZED OBSERVATIONS
API VIEW // INDEPENDENTLY BOUNDED
```

GraphOps time pins can apply `since` and `until` bounds to the Infrastructure
Lens and contradiction engine. Persisted rows are re-filtered against the
current environment's ASN and prefix scope before display or Cloud disclosure.
Evidence retained from yesterday's network cannot silently become evidence for
today's network.

This detail mattered immediately. A broad RIS IPv6 super-prefix such as
`2000::/3` overlapped many unrelated local prefixes and initially produced
false origin disagreements. The comparator now permits an origin comparison
only when the RIS prefix is equal to or more specific than the local
prefix-to-AS claim. The broad observation remains visible as control-plane
evidence; it simply cannot manufacture a contradiction.

## A Contradiction Engine That Does Not Declare a Winner

SCYTHE now identifies deterministic cross-layer tensions such as:

- `ORIGIN_DISAGREEMENT` between local prefix enrichment and a relevant RIS
  announcement;
- `WITHDRAWAL_WITH_DATA_PLANE_ACTIVITY` when observed graph activity overlaps
  a collector-vantage withdrawal;
- `ORIGIN_CHANGE_OBSERVED` and `AS_PATH_CHANGE_OBSERVED` within a selected
  collector/prefix/time window.

Every finding retains both claims, their source revisions, alternatives, a
falsifying observation, and an authority boundary. Red cards in the
Infrastructure Lens and red Cesium halos make the tension visible.

What they do not do is equally important:

```text
ORIGIN DISAGREEMENT != ROUTE HIJACK
COLLECTOR WITHDRAWAL != GLOBAL UNREACHABILITY
CONTROL-PLANE CHANGE != DATA-PLANE CAUSE
```

Negative conclusions are withheld until SCYTHE can prove continuous collector
coverage, subscription continuity, disconnect gaps, sensor capability, and
temporal alignment. Missing evidence is recorded as a withheld test rather
than converted into model confidence.

## Full-Fidelity Cloud, With a Smaller and More Honest Aperture

Operators can explicitly choose `ASK CLOUD // FULL FIDELITY`. The browser sends
only a pinned selection, a retained trace-evidence reference, the operator's
question, and acknowledgement of exact disclosure. The server resolves and
constructs the evidence capsule independently.

Exact selected IPs, hop addresses, RTTs, timestamps, graph identities, and
included infrastructure records may be disclosed. Credentials, authorization
headers, cookies, process environment, raw packet payloads, unrelated files,
and directive execution authority remain excluded.

As InfraFlow grew, the complete infrastructure snapshot reached hundreds of
kilobytes and exceeded the Cloud model's context window. SCYTHE answered with a
selection-focused exact-record projection:

```text
CAPSULE PROJECTION // SELECTION_FOCUSED_EXACT_RECORDS
INCLUDED // EXACT VALUES AND EVIDENCE CLASSES
OMITTED // COUNTED, NOT SUMMARIZED, NOT USED FOR INFERENCE
SOURCE // SHA-256 BOUND
```

The projection retains the selected network domain, its observed domain flows,
relevant PeeringDB declarations, relevant RIS observations, and relevant
contradiction findings. It reports source, included, and omitted counts and
binds the result to the sanitized complete source snapshot.

There is no unrelated control-plane fallback. If Amazon AS16509 has no
prefix- or origin-relevant RIS record in the bounded window, the capsule
contains zero RIS observations. It does not fill the space with Fastly,
Microsoft, or another environment ASN merely because those records are
available.

## The Model Answered—Then the Validator Disagreed

A recent Cloud report demonstrated why deterministic post-generation controls
matter. The model:

- confused Fastly AS54113 with Amazon;
- treated an unrelated Fastly IPv6 announcement as relevant to Amazon AS16509;
- promoted sub-40 ms RTTs into a "short-haul" claim;
- treated the last responding traceroute hop as the end of the packet path;
- inferred the absence of a VPN or distant leg from incomplete visibility.

Those claims sounded plausible. They were not supported.

GraphOps now enforces additional validators after generation:

```text
RTT MAGNITUDE != PHYSICAL PATH LENGTH
LAST RESPONDING TTL != END OF PACKET PATH
UNOBSERVED VPN OR RELAY != EVIDENCE OF ABSENCE
GEOIP CITY SEQUENCE != PHYSICAL ITINERARY
```

Unsupported content is replaced with an explicit refusal, validation
constraints are attached, and confidence is capped. A corrected end-to-end run
against the same host class completed with zero unrelated RIS observations,
zero unrelated control-plane changes, and a topology-absence inference
withheld at confidence `0.200`.

This is the Clarktech principle in its most practical form: the model is
allowed to interpret evidence, but it is not the final authority on what the
evidence can establish.

## Verification

The latest production acceptance completed with:

```text
53 GraphOps Python tests // PASS
82 SCYTHE-Web tests // PASS
Chromium live-page acceptance // PASS
Ollama Cloud full-fidelity request // HTTP 200
Eve bootstrap replay // 256 COMMITTED
SERVICES // ACTIVE
```

The Cloud disclosure receipt now reports exact IP/location counts, graph scope,
included infrastructure classes, unresolved tensions, withheld tests, capsule
projection counts, exclusions, model authority, and content hashes.

## What Comes Next

The immediate frontier is not “more AI.” It is better observational coverage:

1. Record RIS collector session intervals, acknowledgements, and disconnect
   gaps so bounded absence tests can become possible.
2. Attach a versioned CAIDA AS-relationship dataset under its research
   inference license and provenance.
3. Compare repeated fixed-flow traceroutes with concurrent multi-collector RIS
   windows without collapsing control plane into data plane.
4. Let operators promote an evidence tension into a dedicated GraphOps
   investigation tab with competing explanations and targeted falsifiers.
5. Preserve the same exact-record projection and disclosure receipts as new
   infrastructure sources enter the system.

SCYTHE can now watch a network, remember what collectors observed, display
where sources disagree, ask a Cloud model for interpretation, and reject the
model when it crosses the evidence boundary.

The network did not merely become visible. It learned how to disagree without
losing its provenance.
