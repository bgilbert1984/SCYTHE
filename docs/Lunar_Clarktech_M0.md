# Lunar Clarktech M0

Status: implemented and live  
Instrument: `http://127.0.0.1:5001/scythe-web/lunar-ops-demo.html`  
Dataset: `lunar-south-pole-reference-m0`

## Outcome

M0 establishes a Moon-native, token-free GraphOps vertical slice. An operator
can click the lunar south-pole view, produce a typed body-fixed selection, send
the `explain.lunar-location` directive through the stable orchestrator, and
receive a validated `view.show-lunar-prism` effect.

This phase establishes coordinate and evidence discipline before terrain or
ephemeris computation. It does not present a flattened Earth globe as the Moon,
and it does not derive measurements from attractive reference imagery.

## Authority boundary

| Property | M0 declaration |
|---|---|
| Celestial body | `MOON` |
| Body-fixed frame | `MOON_ME_DE421` |
| Longitude | East-positive degrees |
| Latitude | Planetocentric degrees |
| Reference radius | 1,737,400 m |
| Spatial authority | `REFERENCE_ELLIPSOID_ONLY` |
| Terrain authority | `ABSENT_M0` |
| Elevation | Not asserted |
| SPICE kernels | Not ingested |
| LOLA DEM | Not ingested |

The selection height is zero on the reference ellipsoid. It is not a claim that
the physical surface has zero elevation. M0 refuses elevation, slope,
illumination, Earth visibility, link delay, and terrain-occultation claims.

## Components

```text
Moon surface click
        |
        v
typed lunar-location selection
MOON + MOON_ME_DE421 + ellipsoid-only authority
        |
        v
GraphOps explain.lunar-location
        |
        v
checksum-verifying LunarEvidenceResolver
        |
        v
view.show-lunar-prism
        |
        v
sparse evidence + explicit refusals + falsifier
```

- `scythe-web/celestialBodies.js` owns celestial-body definitions and
  body-fixed coordinate conversion.
- `scythe-web/lunarDataset.js` validates the exact M0 manifest and verifies all
  packaged files before use.
- `scythe-web/lunarWorld.js` creates the lunar ellipsoid, local imagery layer,
  polar coordinate grid, and selection event.
- `lunar_evidence_resolver.py` independently validates the selected body,
  frame, coordinate bounds, manifest, and artifact hashes on the server.
- `graphops_director.py` compiles the selection into a sparse, non-mutating
  EffectPlan with claims, limitations, refusals, and a falsifier.

## Pinned visual references

The authoritative manifest is
`datasets/lunar-south-pole-reference-m0/manifest.json`.

| Artifact | Role | SHA-256 |
|---|---|---|
| LROC multi-temporal south-pole illumination image | `REFERENCE_VISUALIZATION` | `b41ba69dd4981c7d077781c4fc189d22d32aed97f31a5220a4cf37b58079b036` |
| LOLA slope map image | `REFERENCE_VISUALIZATION` | `6d8b8c59bfd162ee1adb661c9ef13af4985e7a780c93a3b042f5931bb1ccfc25` |
| Cesium Moon reference texture | `ILLUSTRATIVE_BASE_TEXTURE` | `380fa69424e1cd2268816e346dc56393bd1c809b8e0cb078e705ec469a5847db` |

These images establish traceable visual context only. They are never queried
for elevation or treated as registered raster cells.

## Live interaction contract

The browser emits only a reference and coordinates:

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

The server resolves the manifest and files independently. A browser-supplied
display value cannot become evidence. The response is `partially-completed`
with `sparse` evidence because the registered terrain needed to answer more is
absent.

## Verification

```bash
.venv/bin/python -m unittest test_lunar_evidence_resolver test_graphops_director
npm --prefix scythe-web test
sha256sum datasets/lunar-south-pole-reference-m0/*.{png,jpg}
```

Browser acceptance verifies HTTP 200, a lunar globe radius of 1,737,400 m,
three checksum-verified assets, a successful live surface click, and a Lunar
Reality Prism containing `TERRAIN AUTHORITY // ABSENT_M0` and `ELEVATION // NOT
ASSERTED`.

## M1 handoff: registered terrain

M1 should ingest one bounded NASA Planetary Geology, Geophysics and Geochemistry
Laboratory LOLA south-polar cloud-optimized GeoTIFF product. The ingestion job
must pin the source URL, bytes, SHA-256, CRS, body-fixed frame, longitude
convention, nodata behavior, units, vertical datum, resampling method, and any
tiling transform. It should emit a browser-efficient local height product plus
a machine-readable lineage record back to the original COG.

Only after the registered tile overlaps the selected coordinate may GraphOps
upgrade `terrainAuthority` and assert elevation or slope. A later ephemeris
phase should pin a complete NAIF SPICE kernel set and its coverage intervals
before computing Earth visibility, solar geometry, or communications windows.

The first M1 signature directive is:

> Reveal the terrain that can occult an Earth link from this location, and show
> every frame, kernel, sample, and interpolation step that makes the answer
> true.
