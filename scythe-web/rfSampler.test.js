import assert from "node:assert/strict";
import test from "node:test";

import { SAMPLE_STATUS, ScytheRfSampler } from "./rfSampler.js";

function descriptor(overrides = {}) {
  return {
    schemaVersion: "1.0",
    datasetId: "rf-regional-test-v1",
    title: "RF fixture",
    description: "Deterministic browser sampler fixture",
    evidenceClass: "SOLVER_OUTPUT",
    authority: {
      solverName: "ReferenceSolver",
      solverVersion: "1.0",
      modelName: "Fixture",
      standardRevision: null,
      sourceRevision: "fixture-commit",
      sourceTreeSha256: null,
      provenanceStatus: "COMPLETE",
      solverLicense: "TEST",
      datasetLicense: "TEST",
      runId: "fixture-run",
      deterministic: true,
      executionEnvironment: "node-test",
      inputHashes: [],
    },
    spatialReference: {
      type: "GEODETIC_GRID",
      horizontalCrs: "EPSG:4326",
      verticalDatum: "WGS84_ELLIPSOID",
      coordinateOrder: "longitude,latitude,height",
      heightUnits: "m",
      ecefCompatible: true,
      boundsDegrees: [0, 0, 1, 1],
      crossesAntimeridian: false,
    },
    temporal: {
      generatedUtc: "2026-01-01T00:00:00Z",
      validFromUtc: "2026-01-01T00:00:00Z",
      validToUtc: "2026-12-31T23:59:59Z",
      statisticalTimePercentage: 50,
      timeSemantics: "STATISTICAL_PERCENTAGE",
    },
    physics: {
      domain: "RF",
      rf: {
        frequencyHz: 100e6,
        bandwidthHz: 2e6,
        polarization: "H",
        transmitterHeightMeters: 30,
        receiverHeightMeters: 2,
        antennaPatternAssetPath: null,
        atmosphericModel: null,
        earthSpaceModel: null,
      },
      optical: null,
    },
    quantity: {
      name: "Median field strength",
      definition: "Fixture values",
      units: "dBuV/m",
      valueSemantics: "FIELD_STRENGTH",
      complexRepresentation: "NONE",
      uncertainty: {
        kind: "STATISTICAL",
        description: "One-sigma fixture uncertainty",
        assetPath: "uncertainty.bin",
      },
    },
    grid: {
      representation: "CUSTOM_BINARY",
      dimensions: [2, 2],
      resolution: [1, 1],
      noData: { policy: "NONE", value: null },
      interpolation: "BILINEAR",
      authoritativeAssetPath: "rf.bin",
      lodPolicy: {
        authoritativeValuesImmutable: true,
        derivedTilesAllowed: true,
        aggregationMethod: "none in fixture",
        description: "Fixture",
      },
    },
    assets: [
      {
        path: "rf.bin",
        role: "AUTHORITATIVE_VALUES",
        mediaType: "application/octet-stream",
        sha256: "a".repeat(64),
        sizeBytes: 16,
      },
    ],
    lineage: { parentDatasetIds: [], transformations: [] },
    visualizationIsAuthoritative: false,
    ...overrides,
  };
}

function sampler(manifest = descriptor()) {
  return new ScytheRfSampler({
    descriptor: manifest,
    tileIndex: { locate: () => ({ tileId: "fixture", u: 0.5, v: 0.5 }) },
    tileLoader: {
      getTilePayload: async () => ({
        shape: [2, 2],
        values: new Float32Array([0, 10, 20, 30]),
        uncertaintyValues: new Float32Array([1, 1, 3, 3]),
      }),
    },
  });
}

const query = {
  longitudeDegrees: 0.5,
  latitudeDegrees: 0.5,
  heightMeters: 10,
  utc: "2026-07-01T00:00:00Z",
  frequencyHz: 100e6,
};

test("bilinear sampling preserves provenance, evidence, and uncertainty", async () => {
  const result = await sampler().sample(query);
  assert.equal(result.status, SAMPLE_STATUS.OK);
  assert.equal(result.value, 15);
  assert.equal(result.uncertainty.value, 2);
  assert.equal(result.evidenceClass, "SOLVER_OUTPUT");
  assert.equal(result.visualizationIsAuthoritative, false);
  assert.equal(result.provenance.runId, "fixture-run");
});

test("out-of-band requests return unavailable and never invent a value", async () => {
  const result = await sampler().sample({ ...query, frequencyHz: 103e6 });
  assert.equal(result.status, SAMPLE_STATUS.OUTSIDE_FREQUENCY);
  assert.equal(result.available, false);
  assert.equal("value" in result, false);
});

test("coverage requires explicit comparison and matching physical units", async () => {
  const result = await sampler().sample({
    ...query,
    coverageThreshold: { value: 14, units: "dBuV/m", comparison: "GTE" },
  });
  assert.equal(result.coverage, true);

  await assert.rejects(
    sampler().sample({
      ...query,
      coverageThreshold: { value: 14, units: "dBm", comparison: "GTE" },
    }),
    /do not match/,
  );
});

test("RF sampler rejects optical-only datasets", () => {
  const manifest = descriptor({
    physics: {
      domain: "OPTICAL",
      rf: null,
      optical: {
        wavelengthNanometers: 650,
        frequencySolverUnits: 1,
        polarizationRepresentation: "FIELD_COMPONENTS",
        materialModel: "fixture",
        boundaryConditions: "fixture",
      },
    },
  });
  assert.throws(() => sampler(manifest), /requires an RF dataset/);
});

test("antimeridian grids require an adapter that declares support", () => {
  const manifest = descriptor({
    spatialReference: {
      ...descriptor().spatialReference,
      boundsDegrees: [170, -10, -170, 10],
      crossesAntimeridian: true,
    },
  });
  assert.throws(() => sampler(manifest), /supportsAntimeridian/);

  assert.doesNotThrow(() => new ScytheRfSampler({
    descriptor: manifest,
    tileIndex: {
      supportsAntimeridian: true,
      locate: () => null,
    },
    tileLoader: { getTilePayload: async () => null },
  }));
});
