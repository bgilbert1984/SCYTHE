# GraphOps InfraFlow

InfraFlow is an evidence-partitioned infrastructure view over the bounded live Eve hypergraph. It does not promote the legacy `command-ops-visualization.html` demonstration model into operational truth.

## Contract

`GET /api/graphops/infrastructure/snapshot` returns `graphops.infrastructure.v1` with deliberately separate layers:

- `observedFlows`: aggregated live graph edges. Traffic presence is `OBSERVED`; an arc is not an observed route.
- `domains`: endpoint network ownership and geographic centroids. ASN ownership comes from local prefix enrichment and geography from GeoIP, so both remain `INFERRED`.
- `controlPlaneEvidence`: prefix-relevant RIS observations at named collector vantages. These remain parallel to the hop graph and are non-authoritative for data-plane routing.
- `peeringdbEvidence`: versioned, ASN-bounded, self-reported networks, facilities, exchanges, and policy declarations.
- `modeledPathCandidates`: empty in production. The legacy embedded adjacency reference is disabled rather than promoted into evidence.
- `infrastructureContradictions`: deterministic unresolved source disagreements, observed control-plane changes, and explicitly withheld tests.
- Cesium entities: uncertain endpoint regions and geodesic connectors. These are `DISPLAY_ONLY_NOT_ROUTE`.

The snapshot is bounded to 500 graph nodes, 1,000 graph edges, 128 infrastructure domains, 256 aggregated flows, 16 retained member-edge IDs per flow, 256 returned RIS observations, and 200 contradiction findings. Counts describe the bounded projection, not all traffic ever observed.

## Operator surfaces

The live hypergraph includes an **Infrastructure Lens** tab. Domain cards select a representative observed host; flow cards select a retained source graph edge. Both enter the ordinary revision-pinned GraphOps selection path.

While the lens is active, the Cesium globe renders:

- cyan points at inferred domain centroids;
- translucent uncertainty regions using GeoIP accuracy radius;
- cyan geodesic endpoint connectors for observed graph flow aggregates;
- interactive orange `FAC <id>` PeeringDB facility markers. Hovering discloses
  bounded declared metadata; clicking opens the Cesium entity and an
  Infrastructure Lens evidence panel with any current observed hosts whose ASN
  matches the facility's declared presence.

The connectors communicate association and activity at a glance. They do not claim the physical packet path, BGP AS path, cable, exchange point, relay, or device location.
Facility interaction likewise remains `PEERINGDB_SELF_REPORTED`: declared
co-location does not prove traffic, path, or device presence.

## Full-Fidelity Cloud

An explicitly acknowledged Full-Fidelity Cloud investigation now includes the focused bounded infrastructure snapshot. The disclosure receipt counts exact infrastructure domains, observed infrastructure flows, and modeled path candidates separately.

`evidenceCompatibility` compares the operator's question with available evidence classes. Requests involving temporal freshness, inference from absence, quantization, or interpolation are refused when the capsule lacks source freshness, sensor capability/coverage, encoding lineage, interpolation metadata, or neighboring authoritative samples. Full fidelity preserves exact values; it does not make incomplete evidence sufficient.

## Production control-plane cutover

Do not relabel the embedded AS adjacency model as live routing. A production adapter should ingest separately versioned reference/control-plane sources, retain retrieval time and hashes, and expose source-specific freshness. Suitable next sources include PeeringDB for facilities/exchanges and RIPE RIS Live for BGP control-plane messages. Those sources still do not reveal a packet's physical cable path.

Recommended next contract revision:

1. Add observed or timestamped BGP control-plane evidence from RIS Live.
2. Add versioned PeeringDB exchange/facility records.
3. Replace the embedded adjacency model or retain it only as an explicitly historical baseline.
4. Add repeated-flow byte/packet deltas with defined window semantics.
5. Compare candidate paths against observations as contradictions without synthesizing consensus.

## PeeringDB and RIS control-plane expansion

The production expansion adds two parallel evidence contracts:

- `GET /api/graphops/infrastructure/peeringdb/v1/snapshot` returns only records for ASNs already present in the bounded SCYTHE environment. `graphops.peeringdb.v1` records PeeringDB record update times, retrieval time, a normalized content SHA-256 dataset revision, authentication posture, and explicit `PEERINGDB_SELF_REPORTED` authority. The cache refreshes after six hours and can serve a visibly stale fallback when the provider is unavailable.
- `GET /api/graphops/infrastructure/control-plane/v1/snapshot` returns a persisted, time-windowed, bounded `graphops.ris-live.v1` view. The RIS subscription contains at most 32 observed prefixes, requests no raw BGP bytes, and keeps a 512-record memory view backed by the bounded SQLite store. Every record carries collector identity, collector receive time, peer ASN, prefix, AS path, origin, message ID, and `CONTROL_PLANE_OBSERVATION` authority.

Provider contracts: [PeeringDB API specification](https://docs.peeringdb.com/api_specs/), [PeeringDB API-key authentication](https://docs.peeringdb.com/howto/api_keys/), and [RIPE RIS Live protocol manual](https://ris-live.ripe.net/manual/).

The composite infrastructure endpoint includes both sources and disables the legacy embedded adjacency model. Its reference catalog reports CAIDA relationships as `NOT_ATTACHED`; SCYTHE does not synthesize that research dataset.

The resulting layers are:

| Layer | Source | Evidence posture |
|---|---|---|
| Data plane | Eve flows and bounded traceroute | Observed at the SCYTHE vantage |
| Control plane | RIPE RIS Live | Observed at the named collector vantage; non-authoritative for the data plane |
| Declared infrastructure | PeeringDB | Self-reported network, IX, facility, and policy presence |
| AS relationships | CAIDA | Not attached until a versioned dataset is installed |

The Infrastructure Lens renders PeeringDB cards in orange, RIS cards in magenta, and unresolved evidence tensions in red. Each selectable card opens a revision-pinned observed host in the same ASN, keeping external evidence attached to a server-owned graph selection. Cesium uses orange declared-presence rings/dashes, magenta control-plane dashes, and red tension halos. All overlays are toggleable and display-only.

Cesium point entities use adaptive 45-pixel screen-space clustering at groups of two or more. A cluster marker reports the number of unique observed hosts represented by its network-domain members. Hovering shows a bounded list of host identities, inferred organization/place context, and evidence classes. The tooltip explicitly labels clustering as screen proximity: marker overlap does not assert physical co-location, and GeoIP remains inferred.

Full-Fidelity capsules disclose these bounded layers and count PeeringDB networks, declared IX memberships, and RIS observations independently. Compatibility enforcement distinguishes generic infrastructure, PeeringDB declarations, RIS/BGP control-plane observations, and unavailable CAIDA relationships.

## Persisted control-plane windows

Normalized RIS observations are persisted in `runtime/graphops_ris_live.sqlite` using SQLite WAL. Message IDs are immutable and idempotent. Retention is bounded to seven days and 50,000 observations; API responses remain independently bounded.

Both the composite endpoint and the dedicated control-plane endpoint accept epoch-second `since` and `until` bounds. The composite window must be increasing and no wider than seven days. Selecting two UTC time pins in SCYTHE-Web applies that same window to InfraFlow polling.

Persisted rows are re-filtered against the current graph's bounded prefix/ASN scope before any API response or Cloud disclosure. A retained observation from a prior environment therefore does not become evidence for a new environment merely because it remains inside retention.

`GET /api/graphops/infrastructure/contradictions/v1?since=...&until=...` exposes the deterministic comparison contract independently. Current findings include:

- `ORIGIN_DISAGREEMENT`: local prefix-to-AS enrichment differs from an announced RIS origin for an overlapping prefix.
- `WITHDRAWAL_WITH_DATA_PLANE_ACTIVITY`: observed graph activity overlaps a collector-vantage withdrawal.
- `ORIGIN_CHANGE_OBSERVED` and `AS_PATH_CHANGE_OBSERVED`: more than one variant appeared at a collector for a prefix in the selected window.

Each finding retains both claims and their source revisions, plausible alternatives, a falsifying observation, and a boundary. An origin disagreement is never relabelled as a hijack. A collector withdrawal is never relabelled as global unreachability.

Negative conclusions are withheld until SCYTHE records continuous collector-session coverage, subscription acknowledgements, disconnect gaps, and filter continuity for the entire requested window. PeeringDB-versus-CAIDA and traceroute-versus-control-plane comparisons are likewise withheld when their required revision-pinned source is absent.
