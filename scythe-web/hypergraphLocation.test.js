import assert from "node:assert/strict";
import test from "node:test";

import {locationBoundary, locationEstimate, locationEstimates, projectLocation} from "./hypergraphLocation.js";

test("location estimates require valid coordinate-bearing enrichment and stay inferred", () => {
  const node = {id: "host:8.8.8.8", enrichment: {geo: {latitude: 47.61, longitude: -122.33,
    city: "Seattle", country: "United States", uncertaintyRadiusKm: 500}}};
  assert.deepEqual(locationEstimate(node), {node, latitude: 47.61, longitude: -122.33,
    uncertaintyRadiusKm: 500, place: "Seattle, United States", evidenceClass: "INFERRED",
    authority: "GEOIP_ESTIMATE"});
  assert.equal(locationEstimate({enrichment: {geo: {latitude: 91, longitude: 0}}}), null);
  assert.equal(locationEstimate({enrichment: {geo: {city: "Seattle"}}}), null);
});

test("location projection has stable geographic bounds", () => {
  assert.deepEqual(projectLocation(90, -180, 396, 216), {x: 18, y: 18});
  assert.deepEqual(projectLocation(-90, 180, 396, 216), {x: 378, y: 198});
  assert.deepEqual(projectLocation(0, 0, 396, 216), {x: 198, y: 108});
});

test("location collection and boundary distinguish unlocated nodes", () => {
  const graph = {nodes: [{enrichment: {geo: {latitude: 1, longitude: 2}}}, {}]};
  assert.equal(locationEstimates(graph).length, 1);
  assert.match(locationBoundary(1, 2), /1 GEOIP-PLOTTED \/\/ 1 UNLOCATED/);
  assert.match(locationBoundary(1, 2), /NOT PHYSICAL DEVICE LOCATION/);
});
