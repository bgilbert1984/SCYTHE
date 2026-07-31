import assert from "node:assert/strict";
import test from "node:test";

import { formatSampleForHud, operatorGeodetic } from "./monocleOverlayLayer.js";

test("unavailable samples are rendered as no validated data", () => {
  const model = formatSampleForHud({
    available: false,
    status: "NO_DATA",
    reason: "masked cell",
    evidenceClass: "SOLVER_OUTPUT",
  });
  assert.equal(model.value, "NO VALIDATED SOLVER DATA");
  assert.equal(model.detail, "masked cell");
  assert.equal(model.evidenceClass, "SOLVER_OUTPUT");
});

test("operator position uses Cesium WGS84 conversion", () => {
  const viewer = { scene: { camera: { positionWC: { x: 1, y: 2, z: 3 } } } };
  const Cesium = {
    Ellipsoid: {
      WGS84: {
        cartesianToCartographic: (position) => {
          assert.deepEqual(position, { x: 1, y: 2, z: 3 });
          return { longitude: 1, latitude: 0.5, height: 123 };
        },
      },
    },
    Math: { toDegrees: (radians) => radians * 180 / Math.PI },
  };
  const result = operatorGeodetic(viewer, Cesium);
  assert.equal(result.longitudeDegrees, 180 / Math.PI);
  assert.equal(result.latitudeDegrees, 90 / Math.PI);
  assert.equal(result.heightMeters, 123);
});
