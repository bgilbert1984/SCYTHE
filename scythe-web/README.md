# SCYTHE-Web monocle boundary

This directory contains the first browser-native SCYTHE instrument skeleton.
It is a read-only consumer of datasets that have already passed the Global
Propagation Data Contract v1 Python gate.

The two primary modules are:

- `rfSampler.js`: deterministic sampling of validated RF tiles.
- `monocleOverlayLayer.js`: fixed-step Cesium camera sampling and an
  evidence-labelled browser HUD.

The core intentionally has no propagation model and no random fallback. It
also does not guess a binary tile layout. A dataset-specific adapter must
provide:

```js
const tileIndex = {
  // Return null outside the dataset, or a tile ID plus grid/normalized coords.
  locate(query) {
    return { tileId: "z0/x0/y0", u: 0.25, v: 0.75 };
  },
};

const tileLoader = {
  // The adapter verifies its asset checksum before returning this payload.
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

Run the dependency-free unit tests with:

```bash
cd scythe-web
npm test
```
