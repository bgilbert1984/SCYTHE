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
  assert.equal(Object.isFrozen(validated), true);
  assert.equal(Object.isFrozen(validated.authority), true);
});

test("browser boundary rejects authoritative visualization claims", async () => {
  const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
  manifest.visualizationIsAuthoritative = true;
  assert.throws(
    () => validateContractBoundary(manifest),
    /visualizationIsAuthoritative must be false/,
  );
});
