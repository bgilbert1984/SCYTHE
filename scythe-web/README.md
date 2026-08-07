# SCYTHE-Web monocle boundary

This directory contains the browser-native SCYTHE instrument boundary.
It is a read-only consumer of datasets that have already passed the Global
Propagation Data Contract v1 Python gate.

The modules are:

- `scytheWebConfig.js`: immutable client defaults and contract-version gate.
- `contractLoader.js`: full Contract v1 field validation, Python-equivalent
  cross-reference checks, and frozen descriptor normalization.
- `tileIndex.js`: deterministic geodetic lookup, including explicit
  antimeridian support.
- `tileLoader.js`: bounded caching, mandatory SHA-256 verification, and
  explicit Float32 or scaled Uint16 binary decoding.
- `geoFrames.js`: WGS84/ECEF/local-ENU helpers backed by Cesium transforms.
- `rfSampler.js`: deterministic RF sampling and coverage classification.
- `opticsSampler.js`: deterministic optical quantity/depth-plane sampling.
- `evidenceStyles.js`: evidence-preserving Cesium and HUD styles.
- `monocleOverlayLayer.js`: fixed-step Cesium camera sampling, RF coverage
  cells/point footprints, declared range geometry, physically dimensioned
  uncertainty halos, optical cues, and an evidence-labelled browser HUD.
- `scenarioManifestWeb.js`: validated dataset, transmitter, time-window, and
  operator-view bindings.
- `browser-entry.js`: opt-in integration with `cesium-hypergraph-globe.html`.
- `directiveProtocol.js`: strict GraphOps Directive Request and EffectPlan v1
  validation with allow-listed effect and style tokens.
- `selectionModel.js`: typed, revisioned browser selections.
- `effectRuntime.js`: transactional apply/revert behavior for declarative
  browser effects.
- `visualEffects.js`: Cesium implementations of allow-listed Clarktech visual
  effects, including the reversible coverage-threshold lens.
- `realityPrism.js`: authority, lineage, display-difference, threshold, and
  falsifier presentation for a selected RF cell.
- `graphOverlayLayer.js`: bounded, revision-pinned geospatial graph nodes and
  inferred relationships with typed graph selection events.

The core has no propagation model and no random fallback. It does not guess a
binary tile layout. Use `GeodeticTileIndex` plus `VerifiedTileLoader` when the
dataset has explicit per-tile bounds, hashes, and a supported decoder, or
provide a dataset-specific adapter:

```js
const tileIndex = {
  // Return null outside the dataset, or a tile ID plus grid/normalized coords.
  locate(query) {
    return { tileId: "z0/x0/y0", u: 0.25, v: 0.75 };
  },
};

const tileLoader = {
  // The adapter must verify its asset checksum before returning this payload.
  async getTilePayload(tileId) {
    return {
      shape: [width, height],
      values: new Float32Array(width * height),
      validMask: new Uint8Array(width * height),
      uncertaintyValues: new Float32Array(width * height),
    };
  },
};
```

Complex optical quantities must expose either `realValues` plus
`imaginaryValues`, or `magnitudeValues` plus `phaseValues`, exactly as declared
by `quantity.complexRepresentation`. The sampler derives phase and relative
intensity from those samples; it never interprets a complex field as a scalar.

Compact Uint16 tiles require checksum-bound tile metadata and never infer
physical scaling:

```js
const tile = {
  id: "z0/x0/y0",
  shape: [256, 256],
  encoding: {
    scalarType: "UINT16",
    byteOrder: "LITTLE_ENDIAN",
    scale: 0.01,
    offset: -200,
    noDataRaw: 65535
  }
};
```

This encoding belongs in a metadata asset covered by the dataset contract's
SHA-256 and lineage. It is not an undeclared extension to Contract v1.

`regionalRfDataset.js` implements that boundary for the packaged NTIA ITM
fixture. It verifies `tile-metadata.json`, matches each compact tile to its
`DERIVED_VISUALIZATION` contract asset, and refuses scale/offset values that do
not match the authority-to-visualization lineage hashes and parameters.

To activate it alongside `cesium-hypergraph-globe.html`, define the opt-in
configuration before `scythe-web/browser-entry.js` executes:

```html
<script>
window.SCYTHE_WEB_CONFIG = {
  enabled: true,
  contractUrl: "/datasets/regional-rf-v1/manifest.json",
  scenario: {
    datasets: [{
      id: "regional-rf",
      kind: "RF",
      contractUrl: "/datasets/regional-rf-v1/manifest.json"
    }],
    activeTransmitterId: "tx-01",
    coverageThreshold: { value: -100, units: "dBm", comparison: "GTE" },
    coverageFootprintMeters: 50,
    transmitters: [{
      id: "tx-01",
      label: "TX 01",
      longitudeDegrees: -122.4,
      latitudeDegrees: 37.8,
      heightMeters: 30,
      frequencyHz: 2400000000,
      rangeMeters: 1000
    }]
  },
  createTileIndex: descriptor => createRegionalTileIndex(descriptor),
  createTileLoader: descriptor => createVerifiedBinaryLoader(descriptor)
};
</script>
```

If no configuration is supplied, the module only exposes `window.SCYTHEWeb`;
it does not create overlays or invent sample data.

The end-to-end regional demo can be served from the repository root:

```bash
python -m http.server 8765 --bind 127.0.0.1
```

Open `http://127.0.0.1:8765/scythe-web/regional-rf-demo.html`. It shows the
contract-backed TX and range ring, sampled coverage cells, evidence class,
basic transmission loss, uncertainty status, and pinned solver provenance.
Every overlay and the page banner state that the visualization is
non-authoritative.

Clicking a coverage cell now sends only a typed dataset/tile/location reference
and the non-authoritative display value to GraphOps. The server verifies the
manifest, tile metadata, and authoritative Float64 asset hashes, samples the
authority independently, and returns a declarative EffectPlan. The Reality
Prism exposes any display quantization difference. The threshold control applies
a reversible classification lens to the browser view without changing source
values or evidence authority.

When a SCYTHE instance is active, the regional demo also loads its bounded
graph through the orchestrator. Select an RF cell and then a graph node to
preview the generated `FOCUS`, bounded `EXPAND`, and `RF_CORRELATE` plan.
Execution searches for measured RF observations at the modeled frequency. A
solver cell is never treated as event-time evidence: absent measured support is
rendered as `TEMPORAL_EVIDENCE: ABSENT`, while any temporal match is rendered
as a dashed `INFERRED` correlation fiber explicitly labelled “not causation.”

The existing globe loads `scythe-web/browser-entry.js` as an ES module at the
end of `cesium-hypergraph-globe.html`. Activation remains deliberately opt-in.

Run the dependency-free unit tests with:

```bash
cd scythe-web
npm test
```

Run the real Chromium integration harness with:

```bash
npm install
npx playwright install chromium
npm run test:browser
```

On minimal Linux installations, Playwright's native browser libraries must be
installed by the system administrator first. `npm run test:all` runs both the
unit and browser suites.
