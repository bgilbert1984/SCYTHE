import assert from "node:assert/strict";
import test from "node:test";

import { validateContractBoundary } from "./contractLoader.js";
import { readFile } from "node:fs/promises";

const manifestPath = new URL(
  "../datasets/meep-slab-650nm-convergence-v1/manifest.json",
  import.meta.url,
);

test("browser boundary accepts the Python-validated Meep contract", async () => {
  const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
  const validated = validateContractBoundary(manifest);
  assert.equal(validated.datasetId, "meep-slab-650nm-convergence-v1");
  assert.equal(validated.descriptorType, "SCYTHE_DATASET_DESCRIPTOR_V1");
  assert.equal(validated.solver.name, "Meep");
  assert.equal(validated.samplingPolicy.interpolation, "BILINEAR");
  assert.equal(validated.quantityDescriptor.units, "Meep normalized electric-field units");
  assert.equal(validated.epistemics.visualizationIsAuthoritative, false);
  assert.equal(validated.integrity.assets[0].sha256, manifest.assets[0].sha256);
  assert.deepEqual(validated.integrity.lineage, manifest.lineage);
  assert.equal(Object.isFrozen(validated), true);
  assert.equal(Object.isFrozen(validated.authority), true);
});

test("browser boundary rejects authoritative visualization claims", async () => {
  const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
  manifest.visualizationIsAuthoritative = true;
  assert.throws(
    () => validateContractBoundary(manifest),
    /visualizationIsAuthoritative.*must be false/,
  );
});

test("browser boundary mirrors Python cross-reference checks", async () => {
  const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
  manifest.quantity.uncertainty.assetPath = "missing.json";
  assert.throws(() => validateContractBoundary(manifest), /must reference a declared asset/);
});

test("browser boundary matches Python semantics for an authoritative path role", async () => {
  const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
  const asset = manifest.assets.find((item) => item.path === manifest.grid.authoritativeAssetPath);
  asset.role = "OTHER";
  assert.equal(validateContractBoundary(manifest).datasetId, manifest.datasetId);
});

test("browser boundary rejects schema-unknown properties", async () => {
  const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
  manifest.grid.browserScale = 10;
  assert.throws(() => validateContractBoundary(manifest), /not allowed by Contract v1/);
});
