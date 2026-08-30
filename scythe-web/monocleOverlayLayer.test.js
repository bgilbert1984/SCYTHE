import assert from "node:assert/strict";
import test from "node:test";

import { coverageCellDetail, formatSampleForHud, operatorGeodetic } from "./monocleOverlayLayer.js";

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

test("coverage-cell detail preserves contract, display, uncertainty, and solver fields", () => {
  const detail = coverageCellDetail({id:"scythe-web:coverage:2:3",properties:{
    datasetId:"itm",tileId:"z0",longitudeDegrees:-122.3,latitudeDegrees:47.8,heightMeters:0,
    frequencyHz:900e6,quantity:"path_loss",value:141.2,displayValue:141.2,displayDelta:0,units:"dB",
    coverage:true,coverageThreshold:145,coverageComparison:"LTE",coverageThresholdUnits:"dB",
    evidenceClass:"SOLVER_OUTPUT",visualizationIsAuthoritative:false,displayAssetHash:"abc",
    uncertaintyKind:"MODEL_BOUND",uncertaintyValue:2.5,uncertaintyUnits:"dB",
    solverName:"ITM",solverVersion:"1",sourceRevision:"src",runId:"run"}});
  assert.equal(detail.dataset_id,"itm"); assert.equal(detail.display_delta,0);
  assert.deepEqual(detail.uncertainty,{kind:"MODEL_BOUND",value:2.5,units:"dB"});
  assert.deepEqual(detail.provenance,{solverName:"ITM",solverVersion:"1",sourceRevision:"src",runId:"run"});
  assert.equal(coverageCellDetail({id:"not-coverage"}),null);
});
