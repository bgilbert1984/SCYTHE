import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { validateContractBoundary } from "./contractLoader.js";
import {
  createRegionalTileIndex,
  createRegionalTileLoader,
  loadRegionalTileMetadata,
} from "./regionalRfDataset.js";
import { ScytheRfSampler } from "./rfSampler.js";

const datasetDirectory = new URL(
  "../datasets/ntia-itm-sf-bay-area-v1/",
  import.meta.url,
);

async function fixtureFetch(url) {
  const name = new URL(url).pathname.split("/").at(-1);
  try {
    return new Response(await readFile(new URL(name, datasetDirectory)), { status: 200 });
  } catch {
    return new Response("not found", { status: 404 });
  }
}

test("regional ITM adapter binds Uint16 scale/offset to checksummed contract lineage", async () => {
  const manifest = JSON.parse(await readFile(new URL("manifest.json", datasetDirectory), "utf8"));
  const descriptor = validateContractBoundary(manifest);
  const binding = {
    id: "regional-rf",
    kind: "RF",
    contractUrl: "https://fixture.local/datasets/ntia-itm-sf-bay-area-v1/manifest.json",
  };
  const options = { fetchImpl: fixtureFetch, cryptoImpl: webcrypto };
  const metadata = await loadRegionalTileMetadata(descriptor, binding, options);
  assert.deepEqual(metadata.tiles[0].encoding, {
    scalarType: "UINT16",
    byteOrder: "LITTLE_ENDIAN",
    scale: 0.01,
    offset: 0,
    noDataRaw: 65535,
  });

  const sampler = new ScytheRfSampler({
    descriptor,
    tileIndex: await createRegionalTileIndex(descriptor, binding, options),
    tileLoader: await createRegionalTileLoader(descriptor, binding, options),
  });
  const sample = await sampler.sample({
    longitudeDegrees: -122.5994,
    latitudeDegrees: 37.5949,
    heightMeters: 1.5,
    utc: "2026-08-02T00:00:00Z",
    frequencyHz: 900_000_000,
    coverageThreshold: { value: 145, units: "dB", comparison: "LTE" },
  });
  const authoritativeBytes = await readFile(new URL("path-loss.float64le", datasetDirectory));
  const authoritative = authoritativeBytes.readDoubleLE(0);
  assert.equal(sample.available, true);
  assert.equal(sample.evidenceClass, "SOLVER_OUTPUT");
  assert.equal(sample.visualizationIsAuthoritative, false);
  assert.ok(Math.abs(sample.value - authoritative) <= 0.005);
});

test("regional adapter rejects scale not bound by contract lineage", async () => {
  const manifest = JSON.parse(await readFile(new URL("manifest.json", datasetDirectory), "utf8"));
  manifest.lineage.transformations[0].parameters.scale = 0.02;
  const descriptor = validateContractBoundary(manifest);
  await assert.rejects(
    loadRegionalTileMetadata(descriptor, {
      contractUrl: "https://lineage-mismatch.local/manifest.json",
    }, { fetchImpl: fixtureFetch, cryptoImpl: webcrypto }),
    /scale\/offset are not bound by contract lineage/,
  );
});
