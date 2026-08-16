import assert from "node:assert/strict";
import test from "node:test";

import {geographicArcWaypoints, geographicGraphPlacement} from "./geographicGraphProjection.js";

test("GeoIP becomes inferred display placement without mutating graph position", () => {
  const node = {id:"host:8.8.8.8",position:null,enrichment:{scope:"PUBLIC",geo:{latitude:37.4,
    longitude:-122.1,uncertaintyRadiusKm:20,source:{sha256:"abc"}}}};
  const placement = geographicGraphPlacement(node);
  assert.equal(placement.placementAuthority, "GEOIP_ESTIMATE");
  assert.equal(placement.placementEvidenceClass, "INFERRED");
  assert.equal(node.position, null);
});

test("private and multicast entities require an explicit sensor vantage", () => {
  const privateNode = {id:"host:10.0.0.2",kind:"network_host",enrichment:{scope:"PRIVATE"}};
  assert.equal(geographicGraphPlacement(privateNode), null);
  const placement = geographicGraphPlacement(privateNode, {latitude:47.6,longitude:-122.3,
    accuracyMeters:25,authority:"MEASURED_BROWSER_GEOLOCATION"});
  assert.equal(placement.placementAuthority, "VANTAGE_COLOCATED_DISPLAY");
  assert.equal(placement.inheritedVantageAuthority, "MEASURED_BROWSER_GEOLOCATION");
  assert.equal(placement.uncertaintyRadiusKm, .025);
});

test("unspecified addresses are never geographically projected", () => {
  assert.equal(geographicGraphPlacement({kind:"network_unspecified_address"},
    {latitude:1,longitude:2}), null);
});

test("display arcs take the short antimeridian path and rise above the globe", () => {
  const points = geographicArcWaypoints({latitude:10,longitude:179,heightMeters:0},
    {latitude:12,longitude:-179,heightMeters:0}, 8);
  assert.equal(points.length, 9);
  assert.ok(points[4].heightMeters > 20_000);
  assert.ok(points.every((point) => Math.abs(point.longitude) >= 179 || point.longitude === 180));
});

test("co-located endpoint estimates become a display loop rather than a zero-length route", () => {
  const points = geographicArcWaypoints({latitude:37.75,longitude:-97.82,heightMeters:0},
    {latitude:37.75,longitude:-97.82,heightMeters:0}, 8);
  assert.notEqual(points[2].longitude, points[0].longitude);
  assert.ok(points[4].heightMeters > 20_000);
  assert.deepEqual({lat:points.at(-1).latitude,lon:points.at(-1).longitude},
    {lat:points[0].latitude,lon:points[0].longitude});
});
