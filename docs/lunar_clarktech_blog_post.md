# The Globe Looked Up: SCYTHE GraphOps Establishes a Lunar Foothold

**Date:** August 6, 2026  
**Author:** SCYTHE Core Engineering Team  
**Category:** Clarktech, GraphOps, Lunar Operations, Evidence-Centered Computing

---

SCYTHE began this cycle with a deceptively simple ambition: make the globe do
more than display information.

We wanted an operator to point at a condition, issue a directive, and receive
an executable visual response whose claims could survive inspection. That meant
every dramatic effect needed an evidence class. Every answer needed provenance.
Every simulation needed a boundary. Every unknown needed permission to remain
unknown.

Today, that architecture has left Earth.

Our latest Clarktech expansion introduces a Moon-native, token-free lunar
operations instrument inside SCYTHE-Web. An operator can click the lunar south
pole, resolve a location in an explicit Moon-fixed coordinate frame, send that
selection through GraphOps, and receive a proof-carrying Lunar Reality Prism.

This is not a terrestrial globe wearing a gray texture. It is the first bounded
lunar world in SCYTHE's growing world stack.

Open the instrument at:

```text
http://127.0.0.1:5001/scythe-web/lunar-ops-demo.html
```

---

## From Dashboard to Causal Instrument

The work leading to the Moon began with the regional RF demonstration. Its
first Reality Prism independently traced a visible coverage cell back to
checksum-verified solver output:

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

That interaction established the governing pattern for Clarktech: the browser
sends a typed reference, the server resolves authority independently, and the
result returns as a declarative EffectPlan. The browser may change what an
operator sees. It may not silently change what the evidence means.

Phase 2 widened that vocabulary. Graph nodes, edges, event-like entities, and
paired time pins became typed selections. GraphOps gained bounded graph
expansion, RF correlation, `GRAPH_DELTA`, provenance traversal, and explicit
contradiction overlays. Missing temporal evidence remained structural instead
of being repaired with plausible prose.

The result is a display that can focus attention without laundering inference
into fact.

## The Network Became Alive

The next expansion connected the bundled Eve Streamer to SCYTHE's graph plane.
Normalized Suricata event summaries now cross a loopback protobuf stream,
enter the active instance through its WriteBus, and appear in the regional demo
as a live, selectable hypergraph.

```text
Suricata eve.json
        |
        v
Eve Streamer normalization
        |
        v
SCYTHE protobuf ingress
        |
        v
WriteBus + HypergraphEngine
        |
        v
Live SCYTHE-Web hypergraph
```

The boundary is intentionally narrow. Raw packet payloads and IQ data do not
cross it. Network hosts are not assigned invented geographic positions. The 2D
layout describes topology, not location. Controlled test events remain
`SYNTHETIC`; ordinary validated summaries remain `OBSERVED`.

This gives GraphOps a living network view without manufacturing spatial or
causal authority.

## Then the Globe Looked Up

Bringing the Moon into this architecture required more than replacing Earth
imagery. Earth assumptions are remarkably easy to inherit: WGS84 coordinates,
Earth radii, terrestrial terrain providers, and map services can quietly enter
a scene long after the pixels look lunar.

Lunar M0 closes those doors explicitly.

The new instrument declares:

| Property | Lunar M0 contract |
|---|---|
| Celestial body | `MOON` |
| Reference radius | 1,737,400 m |
| Body-fixed frame | `MOON_ME_DE421` |
| Longitude convention | East-positive |
| Latitude type | Planetocentric |
| Spatial authority | `REFERENCE_ELLIPSOID_ONLY` |
| Terrain authority | `ABSENT_M0` |

The Cesium scene is constructed around a lunar ellipsoid, not the default Earth
ellipsoid. The base layer is local and token-free. Surface picking is resolved
against the Moon. Every selected location carries its celestial body, frame,
dataset, coordinates, height interpretation, and authority into GraphOps.

No Earth WGS84 coordinate enters the view.

## A Lunar Click Becomes an Executable Directive

Clicking the surface emits a selection shaped like this:

```json
{
  "kind": "lunar-location",
  "datasetId": "lunar-south-pole-reference-m0",
  "locationId": "moon:-87.8723:0.0000",
  "celestialBody": "MOON",
  "referenceFrame": "MOON_ME_DE421",
  "longitudeDegrees": 0.0,
  "latitudeDegrees": -87.8723,
  "heightMeters": 0,
  "spatialAuthority": "REFERENCE_ELLIPSOID_ONLY"
}
```

SCYTHE then compiles `explain.lunar-location`. The backend verifies the
manifest and every local artifact independently, rejects incompatible bodies or
frames, and produces an allow-listed `view.show-lunar-prism` effect.

The resulting prism identifies the selected frame and location, enumerates the
supporting artifacts, displays their SHA-256 identities, declares the available
authority, and supplies a falsifier.

The signature is not merely what the prism reveals. It is what the prism
refuses:

```text
LUNAR REALITY PRISM
BODY // MOON
FRAME // MOON_ME_DE421
SPATIAL AUTHORITY // REFERENCE_ELLIPSOID_ONLY
TERRAIN AUTHORITY // ABSENT_M0
ELEVATION // NOT ASSERTED
EVIDENCE // DERIVED_VISUALIZATION
BOUNDARY // REFERENCE IMAGERY IS NOT A SAMPLE SURFACE
```

M0 does not infer elevation from brightness. It does not derive slope from a
published image panel. It does not claim Earth visibility, sunlight, link
delay, or terrain occultation without the registered products needed to
compute them.

That refusal is a feature. In Clarktech, an unknown protected from invention is
more valuable than an impressive answer built on an undeclared assumption.

## Three Images, Three Verifiable Identities

The instrument packages three visual references:

- A NASA LROC multi-temporal south-pole illumination image.
- A NASA LOLA slope visualization.
- A Cesium lunar reference texture used as the illustrative base layer.

Each artifact is pinned by path, role, source, credit, and SHA-256 checksum.
The browser verifies all three before declaring the world ready, and the server
verifies them again when resolving evidence.

The LROC and LOLA panels are useful visual context, but they remain
`REFERENCE_VISUALIZATION`. The base texture remains
`ILLUSTRATIVE_BASE_TEXTURE`. None of them is promoted into a sampleable terrain
surface.

This distinction is central to the larger SCYTHE design: provenance describes
not only where data came from, but also what operations that data is permitted
to support.

## Clarktech Is Becoming a World Stack

The Earth RF instrument, live network hypergraph, and lunar south-pole view now
share a common operational grammar:

1. A gesture creates a typed selection.
2. A directive compiler determines what can be resolved.
3. Evidence is dereferenced at its authoritative boundary.
4. GraphOps returns an EffectPlan rather than arbitrary executable code.
5. The view applies an allow-listed, reversible effect.
6. Claims, assumptions, contradictions, limitations, and falsifiers remain
   attached to the result.

This is the foundation of the SCYTHE world stack: observed worlds,
reconstructed worlds, and counterfactual worlds can eventually coexist without
silently inheriting one another's authority.

A future directive might ask:

> Show what must be true for this network burst, this terrestrial coverage
> gap, and this lunar communications shadow to share an operational cause.

The system should not answer with one dazzling animation. It should construct
competing worlds, expose the assumptions required by each, and identify the
least expensive observation capable of separating them.

## Built to Return Tomorrow

The expansion is also operationally persistent. The SCYTHE orchestrator and
Eve Streamer are installed as enabled WSL user services. The orchestrator
starts on port 5001 with the configured Ollama endpoint, brings up its graph and
streaming infrastructure, and accepts isolated SCYTHE child instances. The Eve
stream reconnects to the local protobuf ingress and resumes its bounded
production Suricata feed.

The lunar interface therefore belongs to the running system—not merely to a
development screenshot.

## Verification, Not Vibes

The completed phase passed:

- 15 relevant Python resolver and GraphOps tests.
- 32 SCYTHE-Web module tests.
- Two existing real-Chromium integration tests.
- Live HTTP and GraphOps endpoint acceptance.
- A real browser surface-click-to-Reality-Prism interaction.
- Independent checksum verification of all three lunar assets.
- A lunar ellipsoid radius check on all three axes.
- Browser error inspection with no application errors.

The most important acceptance criterion was semantic: the live result asserted
only what M0 could prove and explicitly refused the rest.

## Next: Give the Moon Terrain Authority

Lunar M1 will ingest a bounded, registered LOLA south-polar elevation product.
The source bytes, checksum, coordinate reference, Moon-fixed frame, longitude
convention, nodata behavior, units, vertical datum, and resampling steps must all
survive into the local runtime product.

Only then may GraphOps assert elevation or slope for an overlapping selection.

A subsequent ephemeris phase will pin a complete NAIF SPICE kernel set and its
coverage intervals. That will open the door to defensible Earth visibility,
solar geometry, communications windows, and terrain-aware link analysis.

The next signature directive is already waiting:

> Reveal the terrain that can occult an Earth link from this location, and show
> every frame, kernel, sample, and interpolation step that makes the answer
> true.

SCYTHE has not conquered the Moon. It has done something more useful: it has
established the first honest coordinate, evidence, and execution boundary from
which lunar operations can grow.

The globe looked up. GraphOps followed.

---

## Project References

- `docs/Lunar_Clarktech_M0.md` — implementation and authority specification.
- `docs/Clarktech_expansion.md` — Clarktech architecture and roadmap.
- `docs/GraphOps_Directives.md` — executable directive catalog.
- `docs/Eve_Live_Hypergraph.md` — live network-event transport and boundary.
- `scythe-web/lunar-ops-demo.html` — Lunar South Pole Operations M0.
