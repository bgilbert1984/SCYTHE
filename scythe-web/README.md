# SCYTHE-Web monocle boundary

This directory contains the browser-native SCYTHE instrument boundary.
It is a read-only consumer of datasets that have already passed the Global
Propagation Data Contract v1 Python gate.

The modules are:

- `scytheWebConfig.js`: immutable client defaults and contract-version gate.
- `contractLoader.js`: safety-critical browser validation and normalization.
- `tileIndex.js`: deterministic geodetic lookup, including explicit
  antimeridian support.
- `tileLoader.js`: bounded caching, mandatory SHA-256 verification, and
  explicit binary decoding.
- `geoFrames.js`: WGS84/ECEF/local-ENU helpers backed by Cesium transforms.
- `rfSampler.js`: deterministic RF sampling and coverage classification.
- `opticsSampler.js`: deterministic optical quantity/depth-plane sampling.
- `evidenceStyles.js`: evidence-preserving Cesium and HUD styles.
- `monocleOverlayLayer.js`: fixed-step Cesium camera sampling, bearing
  geometry, and an evidence-labelled browser HUD.
- `scenarioManifestWeb.js`: validated dataset, transmitter, time-window, and
  operator-view bindings.
- `browser-entry.js`: opt-in integration with `cesium-hypergraph-globe.html`.

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

To activate it alongside `cesium-hypergraph-globe.html`, define the opt-in
configuration before `scythe-web/browser-entry.js` executes:

```html
<script>
window.SCYTHE_WEB_CONFIG = {
  enabled: true,
  contractUrl: "/datasets/regional-rf-v1/manifest.json",
  scenario: {
    activeTransmitterId: "tx-01",
    coverageThreshold: { value: -100, units: "dBm", comparison: "GTE" },
    transmitters: [{
      id: "tx-01",
      label: "TX 01",
      longitudeDegrees: -122.4,
      latitudeDegrees: 37.8,
      heightMeters: 30
    }]
  },
  createTileIndex: descriptor => createRegionalTileIndex(descriptor),
  createTileLoader: descriptor => createVerifiedBinaryLoader(descriptor)
};
</script>
```

If no configuration is supplied, the module only exposes `window.SCYTHEWeb`;
it does not create overlays or invent sample data.

The existing globe loads `scythe-web/browser-entry.js` as an ES module at the
end of `cesium-hypergraph-globe.html`. Activation remains deliberately opt-in.

Run the dependency-free unit tests with:

```bash
cd scythe-web
npm test
```
