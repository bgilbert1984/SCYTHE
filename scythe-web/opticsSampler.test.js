import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";

import { ScytheOpticsSampler } from "./opticsSampler.js";

const manifestPath = new URL(
  "../datasets/meep-slab-650nm-convergence-v1/manifest.json",
  import.meta.url,
);

async function sampler() {
  const descriptor = JSON.parse(await readFile(manifestPath, "utf8"));
  return new ScytheOpticsSampler({
    descriptor,
    tileIndex: {
      locate: (query) => ({ tileId: "phase-plane", u: 0.5, v: 0.5,
        depthPlaneIndex: query.depthPlaneIndex }),
    },
    tileLoader: {
      getTilePayload: async () => ({
        shape: [2, 2],
        values: new Float32Array([0, 2, 4, 6]),
      }),
    },
  });
}

test("optical sampling preserves solver evidence and depth-plane identity", async () => {
  const instance = await sampler();
  const wavelengthNanometers =
    instance.descriptor.physics.optical.wavelengthNanometers;
  const result = await instance.sample({
    longitudeDegrees: 0,
    latitudeDegrees: 0,
    heightMeters: 0,
    wavelengthNanometers,
    depthPlaneIndex: 3,
  });
  assert.equal(result.available, true);
  assert.equal(result.value, 3);
  assert.equal(result.depthPlaneIndex, 3);
  assert.equal(result.evidenceClass, "SOLVER_OUTPUT");
  assert.equal(result.visualizationIsAuthoritative, false);
});

test("optical sampler refuses a wavelength not represented by the dataset", async () => {
  const instance = await sampler();
  const result = await instance.sample({
    longitudeDegrees: 0,
    latitudeDegrees: 0,
    wavelengthNanometers: 500,
  });
  assert.equal(result.status, "OUTSIDE_WAVELENGTH");
  assert.equal("value" in result, false);
});
