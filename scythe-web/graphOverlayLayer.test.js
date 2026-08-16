import assert from "node:assert/strict";
import test from "node:test";

import { GraphOverlayLayer, summarizeGraphCluster } from "./graphOverlayLayer.js";

function fixture() {
  const values = [];
  const entities = {
    values,
    add(value) { values.push(value); return value; },
    removeById(id) { const index = values.findIndex((item) => item.id === id); if (index >= 0) values.splice(index, 1); },
  };
  class Color {
    withAlpha() { return this; }
    static fromCssColorString() { return new Color(); }
  }
  const Cesium = {
    Cartesian3: {fromDegrees: (lon, lat, height) => ({lon, lat, height})},
    Cartesian2: class { constructor(x, y) { this.x = x; this.y = y; } },
    Color: Object.assign(Color, {BLACK: new Color()}),
    DistanceDisplayCondition: class { constructor(near, far) { this.near = near; this.far = far; } },
    PolylineDashMaterialProperty: class { constructor(value) { Object.assign(this, value); } },
  };
  const events = [];
  const container = {dispatchEvent: (event) => events.push(event), ownerDocument: {defaultView: {
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } },
  }}};
  return {viewer: {entities, scene: {canvas: {}}}, Cesium, events, container};
}

test("bounded graph overlay renders geospatial nodes and inferred edges", async () => {
  const {viewer, Cesium, events, container} = fixture();
  const graph = {status: "ok", graphRevision: "graph-1", nodes: [
    {id: "a", kind: "burst", position: [37.8, -122.4, 0], metadata: {}},
    {id: "b", kind: "host", position: [37.81, -122.41, 0], metadata: {}},
  ], edges: [{id: "e", kind: "flow", nodes: ["a", "b"]}]};
  const layer = new GraphOverlayLayer({viewer, Cesium, container,
    fetchImpl: async () => new Response(JSON.stringify(graph), {status: 200})});
  await layer.start();
  assert.equal(viewer.entities.values.filter((entity) => entity.id.startsWith("scythe-web:graph-node:")).length, 2);
  assert.equal(viewer.entities.values.filter((entity) => entity.id.startsWith("scythe-web:graph-edge:")).length, 1);
  assert.equal(viewer.entities.values.filter((entity) => entity.id.startsWith("scythe-web:graph-direction:")).length, 1);
  assert.equal(viewer.entities.values.find((entity) => entity.id.startsWith("scythe-web:graph-edge:")).polyline.width, 1.5);
  assert.equal(events.at(-1).detail.graphRevision, "graph-1");
  layer.destroy();
  assert.equal(viewer.entities.values.length, 0);
});

test("graph overlay consumes the shared controller without a second graph poll", async () => {
  const {viewer, Cesium, container} = fixture();
  const graph = {status: "ok", graphRevision: "shared-1", nodes: [
    {id: "a", kind: "event", position: [37.8, -122.4, 0], evidenceClass: "OBSERVED"},
  ], edges: []};
  let listener; let starts = 0; let fetches = 0;
  const controller = {
    subscribe(callback) { listener = callback; return () => { listener = null; }; },
    async start() { starts += 1; listener({kind: "snapshot", graph, available: true, changed: true}); },
  };
  const layer = new GraphOverlayLayer({viewer, Cesium, container, controller,
    fetchImpl: async () => { fetches += 1; throw new Error("must not poll"); }});
  await layer.start();
  assert.equal(starts, 1); assert.equal(fetches, 0);
  assert.equal(layer.graphRevision, "shared-1");
  assert.equal(viewer.entities.values.length, 1);
  layer.destroy(); assert.equal(listener, null);
});

test("screen clusters count hosts and retain a bounded evidence-labelled hover list", () => {
  const entities = [
    {properties: {graphEntityId: "host:192.0.2.1", graphKind: "network_host", evidenceClass: "OBSERVED",
                  organization: "Example Network", placeLabel: "Seattle, Washington"}},
    {properties: {graphEntityId: "host:192.0.2.2", graphKind: "network_host", evidenceClass: "INFERRED"}},
    {properties: {graphEntityId: "event:burst-1", graphKind: "event", evidenceClass: "OBSERVED"}},
  ];
  const summary = summarizeGraphCluster(entities);
  assert.equal(summary.entityCount, 3); assert.equal(summary.hostCount, 2); assert.equal(summary.markerCount, 2);
  assert.match(summary.text, /SCREEN CLUSTER \/\/ 3 ENTITIES \/\/ 2 HOSTS/);
  assert.match(summary.text, /host:192\.0\.2\.1 \/\/ Example Network \/\/ Seattle, Washington \/\/ GRAPH UNAVAILABLE · PLACEMENT OBSERVED/);
  assert.doesNotMatch(summary.text, /event:burst-1/);
  assert.match(summary.text, /SCREEN-SPACE PROXIMITY; GEOIP REMAINS INFERRED/);
});

test("GeoIP-enriched hosts project onto Cesium without acquiring graph position authority", async () => {
  const {viewer, Cesium, container} = fixture();
  const node = {id:"host:203.0.113.7",kind:"network_host",position:null,evidenceClass:"OBSERVED",
    enrichment:{scope:"PUBLIC",geo:{latitude:47.61,longitude:-122.33,uncertaintyRadiusKm:20,
      authority:"GEOIP_ESTIMATE"}}};
  const graph = {status:"ok",graphRevision:"geo-1",nodes:[node],edges:[]};
  const layer = new GraphOverlayLayer({viewer,Cesium,container,
    fetchImpl:async()=>new Response(JSON.stringify(graph),{status:200})});
  await layer.start();
  const marker = viewer.entities.values.find((entity)=>entity.id.startsWith("scythe-web:graph-node:"));
  assert.deepEqual(marker.position,{lon:-122.33,lat:47.61,height:500});
  assert.equal(marker.properties.placementAuthority,"GEOIP_ESTIMATE");
  assert.equal(marker.properties.evidenceClass,"INFERRED");
  assert.equal(node.position,null);
  layer.destroy();
});
