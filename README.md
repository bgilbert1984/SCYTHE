# SCYTHE

### A command operations visualization layer for RF intelligence, PCAP analysis, hypergraph reasoning, and geospatial situational awareness.

[Open the live SCYTHE instance](https://neurosphere-2.tail52f848.ts.net/) | [Launch the visualization](command-ops-visualization.html)

SCYTHE is a high-density operational interface for turning signal, network, and geospatial telemetry into an explorable command picture. It blends a 3D globe, hypergraph overlays, packet-derived entities, RF activity, route intelligence, and live backend streams into one operator-facing workspace.

This repository contains the command operations visualization bundle and runtime surface: the browser experience, orchestrator, API server, render schedulers, Cesium/MapLibre/deck.gl integrations, RF overlays, PCAP graph tooling, WriteBus-backed graph coordination, and supporting UI modules used by the SCYTHE runtime.

## Why SCYTHE

Modern signal environments do not fit neatly into a table. Operators need to see relationships, movement, uncertainty, geography, RF behavior, and packet evidence at the same time.

SCYTHE is designed for that fused view:

- **Command globe:** Cesium-powered 3D visualization for entities, arcs, RF regions, geo paths, and operational overlays.
- **Hypergraph intelligence:** Graph and hypergraph panels for inspecting entities, relationships, inferred structure, and session context.
- **PCAP-to-picture workflow:** Network captures become hosts, flows, geographic anchors, and analyzable graph objects.
- **RF-aware visualization:** RF cones, emitters, field overlays, voxel-style activity, and cluster intelligence are first-class UI concepts.
- **Live operations fabric:** Socket.IO, SSE, and API hooks keep the command view connected to the active SCYTHE backend.
- **Instance-aware runtime:** The interface supports scoped runtime identity, API base resolution, and per-instance bootstrap behavior.

## Experience

SCYTHE is built for operators who need density without losing spatial intuition. The visualization can render:

- Geospatial routes, traceroutes, and entity movement on a 3D globe.
- Validated and speculative activity layers through deck.gl overlays.
- PCAP-derived recon entities, flow arcs, and hypergraph sessions.
- RF activity, cluster intelligence, heatmaps, and contextual autopsy views.
- Operational panels for graph operations, entity publishing, room state, and live system feedback.

## Technology

The bundle is intentionally browser-native and modular:

- **CesiumJS** for the primary 3D globe.
- **MapLibre GL** for vector map rendering.
- **deck.gl** for high-throughput overlay layers.
- **Socket.IO and SSE** for live backend updates.
- **SCYTHE runtime modules** for auth, transport, graph visualization, route ecology, RF overlays, and command workflows.

## Quick Start

For a static preview of the command operations UI:

```bash
python3 -m http.server 8080
```

Then open:

```text
http://localhost:8080/command-ops-visualization.html
```

The static page can load the UI shell, but live telemetry, authentication, graph writes, PCAP workflows, and instance bootstrap features require a running SCYTHE backend that serves the expected `/api/*`, `/stream/*`, and Socket.IO endpoints.

For the orchestrated runtime:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python3 scythe_orchestrator.py --host 0.0.0.0 --port 5000
```

The orchestrator serves `rf_scythe_home.html`, spawns isolated child instances with `rf_scythe_api_server.py`, and routes instance traffic through stable `/scythe/i/<instance_id>/...` URLs. Optional live-data integrations use environment variables such as `AISSTREAM_API_KEY`, `N2YO_API_KEY`, `FUSIONAUTH_API_KEY`, and `OLLAMA_URL`.

## Repository Map

```text
command-ops-visualization.html  Main command center entry point
rf_scythe_home.html             Orchestrator home and instance launcher
scythe_orchestrator.py          Multi-instance supervisor and reverse proxy
rf_scythe_api_server.py         Child instance API, Socket.IO, graph, and RF server
writebus.py                     Canonical single-writer commit coordinator
registries/                     PCAP, recon, and detection registry modules
assets/js/                      Shared SCYTHE transport and auth helpers
cesium-*.js                     Cesium integrations, safety patches, and visualization helpers
maplibre-deck-cesium.js         MapLibre, deck.gl, and Cesium bridge
unified-render-scheduler.js     Coordinated render loop for mixed visualization layers
*Route*.js / *Inference*.js     Route ecology and inference support modules
network-visualization.css       Network and graph visualization styling
missile-operations.css          Operational simulation panel styling
urh-integration.css             RF and URH integration styling
```

## Deployment Notes

SCYTHE is normally served by its backend runtime, which provides bootstrap configuration, identity exchange, API routing, and live event streams. The frontend looks for runtime values through `window.__SCYTHE_BOOTSTRAP__`, instance-aware paths, and SCYTHE auth helpers.

If `api/bootstrap.js` is not present in a static clone, the interface falls back to its client-side bootstrap path where possible. Full functionality still depends on the backend runtime.

## Built For

- RF and spectrum-aware situational awareness.
- PCAP investigation and network reconnaissance visualization.
- Hypergraph-centered entity and relationship analysis.
- Geospatial command dashboards.
- Experimental intelligence workflows where signal, network, and spatial data need to share the same screen.

## Status

This repository is a visualization bundle for the SCYTHE command interface. Backend services, data stores, and operational ingestion pipelines are expected to be provided by a SCYTHE server instance.
