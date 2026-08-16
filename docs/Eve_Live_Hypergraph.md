# Eve Streamer live hypergraph

## Outcome

`assets/eve-streamer-main` now feeds normalized network-event summaries into
the active SCYTHE instance and the regional RF demo renders them as a live 2D
hypergraph:

```text
Suricata eve.json
  -> eve-streamer JSON tail + normalizer
  -> protobuf client stream on 127.0.0.1:50051
  -> SCYTHE gRPC EveEventStreamServicer
  -> stable orchestrator /api/graphops/eve/events
  -> active child WriteBus
  -> HypergraphEngine
  -> /api/graphops/selection/graph
  -> scythe-web/liveHypergraphView.js
```

Open:

```text
http://127.0.0.1:5001/scythe-web/regional-rf-demo.html
```

The lower-left `LIVE NETWORK HYPERGRAPH` panel refreshes every two seconds.
Clicking a host or flow emits the same revision-pinned GraphOps selection event
used by the Phase 2 directive interactions.

Because production ingress can advance the graph between a render and a click,
the child retains the 32 most recently served bounded snapshots. A directive
may resolve a selection against its exact rendered revision while it remains in
that cache; unknown or expired revisions are still rejected as stale. GraphOps
does not silently rebase the selection onto the newest live graph.

## Authority boundary

Only the protobuf event summary crosses into GraphOps. The HTTP boundary
accepts an exact, bounded schema and rejects unknown fields, raw packet bytes,
and payload fields. Each accepted event becomes two `network_host` nodes and
one stable five-tuple `network_flow` edge through WriteBus.

- Normal Suricata event types are `OBSERVED`.
- Event types beginning with `test` or `synthetic` are `SYNTHETIC`.
- Network entities declare `geospatialAuthority: ABSENT` and have no position.
- The 2D layout is topology only; it is explicitly labelled `NOT GEOLOCATION`.
- Snapshot responses are bounded to 500 nodes and 1,000 edges server-side; the
  demo requests 200 nodes and 300 edges.
- The Eve gRPC method is unauthenticated only on the existing loopback-bound
  gRPC server. Its content is validated again before WriteBus mutation.

## User service

The enabled WSL user service is:

```text
~/.config/systemd/user/eve-streamer.service
```

It starts after `scythe-orchestrator.service`, exposes its otherwise-unused
receiver only on `127.0.0.1:50052`, and ships normalized batches to SCYTHE on
`127.0.0.1:50051`.

The orchestrator user service supplies
`--bootstrap-instance-name "Clarktech Live GraphOps"`. After each WSL/workstation
start, the orchestrator waits for its own HTTP listener, checks the instance
registry, and creates one named child only when the registry is empty. The
check is idempotent across connection and POST retries, so it neither creates a
second child when one is already registered nor bypasses the normal instance
creation lifecycle. This child is the WriteBus destination required by the Eve
ingress endpoint; active services without an active child correctly report
`graphInstanceActive: false` and cannot populate the live graph.

Inspect it with:

```bash
systemctl --user status eve-streamer.service
curl http://127.0.0.1:8081/capture/metrics
curl http://127.0.0.1:5001/api/graphops/eve/status
curl 'http://127.0.0.1:5001/api/graphops/selection/graph?node_limit=200&edge_limit=300'
```

The originally bundled executable was older than the checked-in source and its
scanner stopped permanently at its first EOF. The source tailer now retries
after EOF, bounds records to 1 MiB, handles same-inode appends and log rotation,
and has a regression test. `bin/eve-streamer` was rebuilt from that source with
the repository's declared Go 1.24 toolchain. The service uses explicit
`suricata` mode, disables privileged-mode fallback, and binds both gRPC and HTTP
metrics to loopback.

The shipper also owns a resilient gRPC client stream. If the orchestrator's
receiver generation exits or restarts, a failed send discards the poisoned
stream, reconnects with capped exponential backoff (100 ms through 5 s), and
retries the same normalized batch. Retries remain bounded by the existing
in-memory event channel and are interruptible during service shutdown. Receiver
replay safety derives from the event ID generated before batching; raw packets
are never added to the retry path. Recovery is visible in
`runtime/eve-streamer.log` as `remote send attempt ... reconnecting`, followed
by `remote stream recovered ...`.

### Flow activity capsules

Clicking a `network_flow` line in either live hypergraph view now prepares a
revision-pinned GraphOps flow evidence capsule. Preparation is local: it does
not contact Ollama Cloud. The GraphOps panel displays the exact transport tuple,
directional counters, endpoint context, evidence class, and any allow-listed
Suricata Eve dissections available for that flow. The operator must still use
`ASK CLOUD // FULL FIDELITY` and confirm the exact disclosure before transmission.

The streamer retains a bounded decoded vocabulary: application protocol, flow
identity/state/counters, TCP state/flags, DNS name/type/result, HTTP host/path/
method/status, TLS SNI/version/JA3 hash, and Suricata alert signature/category/
severity. Raw packet payloads, packet bytes, authorization material, cookies,
and a complete packet sequence do not enter this path. GraphOps treats decoded
fields as observed sensor summaries while application purpose, user intent, and
maliciousness remain model interpretations. Missing decoded fields are never
treated as evidence that the corresponding activity was absent on the wire.

The orchestrator additionally retains an in-memory temporal dissection ring
for each recently observed flow: at most the latest 32 accepted, deduplicated
Eve summaries. The ring is a sidecar rather than graph-edge metadata, so normal
200-node/300-edge polling and immutable graph snapshots do not multiply the
sequence payload across every displayed edge. Only the explicitly selected
flow receives its ordered ring, inter-arrival cadence, window, omitted-event
count, post-selection exclusion count, and decoded fields. Events newer than a
retained selection revision are excluded. The ring remains payload-free and is explicitly labelled
`BOUNDED_DECODED_EVENT_TAIL; NOT A COMPLETE PACKET SEQUENCE`. A restart clears
the sidecar; bounded Eve bootstrap replay begins rebuilding it.

Live 2D and Three.js flow edges use a shared display taxonomy: security signal,
DNS, HTTP, TLS, TLS/QUIC candidate, service discovery, ICMP, and other transport.
Color communicates that display type while dash pattern continues to communicate
evidence class. Decoded protocol fields carry an `OBSERVED_DECODED` basis;
port/multicast candidates carry `INFERRED_TUPLE`. Hover text exposes the basis,
and neither classification changes the edge's underlying evidence authority.

Every two-member flow also carries a static source-to-destination chevron in
the 2D and Three.js views. That orientation is an observed Eve tuple, not a
claim about local ingress or egress. `INBOUND`, `OUTBOUND`, `EAST_WEST`, and
`EXTERNAL_TRANSIT` are assigned only against the current capture-adapter
boundary written to `runtime/sensor-boundary.json`; SCYTHE deliberately does
not equate RFC1918 space with the local sensor zone. The Windows Suricata
startup task refreshes this boundary on every run, including when Suricata is
already active, and the server reads it on demand so Wi-Fi roaming does not
require an orchestrator restart.

When two accepted summaries provide monotonic Suricata directional counters,
one bounded particle per active direction moves along the edge. Its rate is
derived from the measured counter interval and its size only summarizes the
packet delta. No delta means no animation. Reduced-motion clients always retain
the static chevron, direction color, and complete hover wording without moving
particles. Flow capsules carry the same boundary classification and compact
counter delta while the latest-32 sidecar remains the authority for sequence
and cadence analysis.

### Cesium Live Geo projection

The regional RF page projects the same bounded live graph onto Cesium through
a display-only geographic adapter. This adapter never copies GeoIP coordinates
into `node.position` and never changes the content-addressed graph revision.
Every marker instead carries two independent claims: the graph entity's
evidence class and its placement evidence class.

- Explicit graph positions are accepted unless their metadata declares
  `geospatialAuthority: ABSENT`.
- Public-host GeoIP coordinates render as `INFERRED // GEOIP_ESTIMATE`, with
  local database identity and accuracy radius retained.
- Private and multicast entities remain unlocated until the operator presses
  `SET VANTAGE` and grants browser geolocation. They are then marked
  `VANTAGE_COLOCATED_DISPLAY`; this does not claim individual device location.
- Unspecified addresses are never projected.
- Clearing the vantage immediately removes the sensor anchor and every
  vantage-co-located entity. The exact browser position remains display-side
  and is not written into the graph.

Cesium marker bodies retain adaptive relevance colors; measured host liveness
is a distinct marker outline. Accuracy circles show placement uncertainty.
Screen-space clustering displays the number of hosts and a bounded hover list
with graph evidence, placement evidence, organization, place label, and
uncertainty.

Flow edges whose endpoints both have usable display placements become elevated
arcs. Flow type controls arc color, evidence class controls solid/dashed style,
and a short colored arrow segment communicates observed tuple direction plus
the sensor-boundary-relative direction class. Measured Suricata counter deltas
may drive a bounded forward or reverse particle; reduced-motion clients retain
the static arrow. Global clutter is bounded by grouping non-focused flows by
endpoint ASN or geographic cell, flow type, and operational direction. A
focused graph edge remains individual and selectable.

Every marker and arc declares the geographic boundary:

`ENDPOINT PLACEMENTS ARE INFERRED OR VANTAGE-COLOCATED; ARC IS NOT A PHYSICAL ROUTE`

The `VISIBLE`, `HOSTS`, `FLOWS`, `UNCERTAINTY`, `DIRECTION`, `MOTION`,
`LOCAL ZONE`, and `AGGREGATE` controls affect Cesium presentation only. They
do not mutate the graph, execute GraphOps directives, or change evidence.

### Non-unicast address context

Eve endpoints that are multicast or unspecified are typed as
`network_multicast_group` or `network_unspecified_address`, rather than as
ordinary hosts. They are excluded from ICMP liveness rotation and unicast
traceroute. Selecting one prepares passive GraphOps context instead: known-group
semantics (including mDNS, LLMNR, and SSDP), incident flows, decoded protocol
counts, and bounded observed senders/receivers. An unspecified address is
explained as a capture/binding wildcard or sentinel requiring source-record
inspection—not as a remote device. These investigations can be sent to the
local interpretive Ollama path; they do not invent a unique multicast responder.

## Production sensor deployment

Production cutover completed on 2026-08-07. WSL2 is using its default NAT
network architecture and exposes only a virtual `eth0`, so capturing there
would observe the WSL guest boundary rather than the Windows host's active
Wi-Fi traffic. The production sensor therefore runs on Windows against the
Npcap Wi-Fi device and writes date-rotated EVE files into:

```text
C:\Users\benja\SCYTHE-Suricata-8.0.6\runtime\log\eve-YYYY-MM-DD.json
```

The WSL service follows the files through this literal glob:

```text
/mnt/c/Users/benja/SCYTHE-Suricata-8.0.6/runtime/log/eve-*.json
```

This is a host sensor, not a claim of visibility into every Wi-Fi station or
every LAN packet. It sees traffic Npcap delivers from the selected Windows
adapter. Switched, encrypted, remote, or otherwise unavailable traffic remains
absent.

The deployment uses Suricata 8.0.6 from the official OISF Windows installer:

```text
MSI SHA-256 // ac7e2db129fcbc5136bc15e4a40befa6435253523780d5d4e97e3e7b172ab442
MSI SIGNER // Open Information Security Foundation Inc.
EXE SHA-256 // 10f4922e317e8776bc0c8554b03dbd6eff62a36950ec1dd17b9f1fc27d992623
CONFIG SHA-256 // befb57d4710dbf8e403846346a0203dc3869a6da2f54752d31b00b6be320135a
```

The installer was administratively extracted into a user-owned directory; it
did not silently install or replace a machine-wide service. The existing Npcap
driver has `AdminOnly=0`, so the sensor can capture without granting packet
privileges to Eve Streamer or the SCYTHE Python services.

The production EVE allow-list is:

- `alert`, without packet or payload logging;
- `http`, without extended headers or bodies;
- `dns` requests and responses;
- `tls`, without extended certificate material;
- `flow` summaries.

File extraction, SMTP, broad protocol logs, raw packets, HTTP bodies, and EVE
stats are disabled. No detection rules are currently supplied, so `alert`
records will remain absent until a separately reviewed ruleset is installed.
Community Flow ID is enabled.

Suricata's Windows timestamp offset was incorrect when it inherited local DST.
The startup boundary now forces `TZ=UTC`, and acceptance requires each emitted
timestamp to be within 30 seconds of the independently observed UTC clock.

The user startup hook is:

```text
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\SCYTHE-Suricata-Startup.cmd
```

It invokes the checksum-pinned launcher at
`assets/eve-streamer-main/scripts/windows/start_scythe_suricata.ps1`, resolves
the current Npcap GUID for the named `Wi-Fi` adapter, validates the deployed
configuration, and refuses to start a modified executable.

The launcher tolerates Windows boot ordering: after configuration validation
it waits up to 120 seconds for the named adapter to report `Up`, verifies that
Suricata remains running two seconds after launch, and records the outcome in
`runtime/suricata-startup.log`. This prevents a hidden Startup-folder process
from silently exiting while Wi-Fi is still initializing.

## Rotation and retention

Suricata performs native daily EVE rotation. Eve Streamer accepts the literal
`eve-*.json` pattern, selects the newest file at startup, and switches to a
newer matching file from byte zero when rotation occurs. Existing exact-path
same-inode append and replacement behavior is preserved. Both modes have Go
regression tests.

Native rotation prevents an unbounded active file, but removal of old daily
files remains a deployment policy. No automatic deletion is enabled in this
phase; retention must be chosen explicitly before a cleanup task is installed.

At service start, production mode replays at most the newest 256 newline-
delimited Eve records before continuing at EOF. This bounded bootstrap restores
recent topology when the graph instance is fresh but the Windows sensor is
quiet after a workstation restart. Replayed records retain their original
observation timestamps and `OBSERVED` evidence class, and carry
`ingest_mode=BOOTSTRAP_REPLAY`; UI status reports them separately from the
committed total. Eve event IDs are deterministic content hashes, so restarting
only Eve Streamer cannot turn the same record into a second observation; those
attempts increment `DEDUPLICATED`, not `COMMITTED`, and leave graph revision
unchanged.

The former controlled feed remains available at:

```text
/home/spectrcyde/SCYTHE/assets/eve-streamer-main/runtime/eve-live.json
```

Its `test_*` records remain `SYNTHETIC`. The pre-cutover service definition is
preserved at `~/.config/systemd/user/eve-streamer.service.controlled`.

## Rollback

Restore the controlled feed without deleting production evidence:

```bash
systemctl --user stop eve-streamer.service
cp ~/.config/systemd/user/eve-streamer.service.controlled \
   ~/.config/systemd/user/eve-streamer.service
systemctl --user daemon-reload
systemctl --user start eve-streamer.service
```

Stopping the Windows sensor is optional for this rollback because the
controlled Eve service no longer reads its files. Use the exact deployed
executable path when stopping it; do not terminate unrelated processes named
Suricata.

## Failure semantics

- No active child: status remains available, ingest returns service unavailable,
  and the browser shows an explicit empty or unavailable state.
- Invalid event: the event is rejected and counted without graph mutation.
- ion or terrain failure: unrelated to the topology panel; the OSM fallback and
  2D network view remain independent.
- Missing geolocation: the network node stays in the topology panel and is not
  projected onto Cesium.
- Missing WebGL or Three.js: the shared live snapshot remains visible and
  selectable through the SVG fallback.
- Replayed event ID: WriteBus idempotency prevents a second authoritative write.

## Verification record

The production acceptance probe produced live DNS/TLS/flow records on the
Windows Wi-Fi adapter, crossed protobuf/gRPC into a fresh child, and produced
two hosts plus a stable `network_flow` edge. Every materialized entity was
`OBSERVED`, carried `geospatialAuthority: ABSENT`, and had a source timestamp
within the UTC acceptance window. The bounded graph API returned a pinned graph
revision with no rejected records.

Verification includes:

- valid OISF Authenticode signature on the downloaded MSI;
- SHA-256 identities for the MSI, executable, configuration, and startup files;
- native Suricata configuration validation;
- real host Wi-Fi capture through Npcap;
- UTC timestamp acceptance;
- Go EOF, exact-path rotation, and date-glob rotation tests;
- Python ingestion, WriteBus, and GraphOps tests;
- SCYTHE-Web unit and Chromium integration tests;
- live regional-demo browser acceptance.
- live node selection and executed provenance traversal against the exact
  retained render revision, with no HTTP, console, or page errors.
- live Three.js rendering of the bounded 200-node/300-edge production snapshot,
  2D/3D mode switching, and executed provenance traversal from a selectable 3D
  graph edge.
