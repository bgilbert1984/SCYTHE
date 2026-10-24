import test from "node:test";
import assert from "node:assert/strict";
import {createWorkspace} from "./lifecycle.js";
import {createRenderPlugin, RENDER_ID, THRESHOLDS, CLUSTER_DEFAULTS, detailLevel, shouldShowDetail, clusterSettings} from "./renderPlugin.js";

const quiet = {warn() {}, debug() {}, log() {}, error() {}};

/** A fake globe: the camera altitude is whatever the test sets, `camera()` fires the change listeners. */
function fakeGlobe() {
  const globe = {
    viewerHandedOver: null, height: 0, listeners: new Set(), sources: [], removed: [], unsubscribed: 0,
    attach(viewer) { globe.viewerHandedOver = viewer; },
    available: () => Boolean(globe.viewerHandedOver),
    viewer: () => globe.viewerHandedOver,
    altitude: () => globe.height,
    onCameraChanged(fn) { globe.listeners.add(fn); return () => { globe.unsubscribed++; globe.listeners.delete(fn); }; },
    addDataSource(name, clustering) { const source = {name, clustering, in: true}; globe.sources.push(source); return source; },
    removeDataSource(source) { if (source.in) { source.in = false; globe.removed.push(source); } },
    camera(height) { globe.height = height; for (const fn of [...globe.listeners]) fn(); },
  };
  return globe;
}

function setup() {
  const globe = fakeGlobe();
  const announced = [];
  const workspace = createWorkspace({log: quiet});
  workspace.provide("renderGlobe", globe);
  workspace.provide("renderEvents", {altitudeChanged: (detail) => announced.push(detail)});
  const render = createRenderPlugin({log: quiet});
  workspace.register(render.tool);
  return {workspace, globe, announced, controls: render.controls};
}

// --- pure pieces ---

test("detailLevel: above 5000 km is GLOBAL, above 500 km REGIONAL, above 50 km LOCAL, else DETAILED", () => {
  assert.deepEqual(THRESHOLDS, {GLOBAL: 5000000, REGIONAL: 500000, LOCAL: 50000, DETAILED: 10000});
  assert.deepEqual([6e6, 5000001, 1e6, 500001, 100000, 50001, 40000, 10000, 0, -5].map(detailLevel),
    ["GLOBAL", "GLOBAL", "REGIONAL", "REGIONAL", "LOCAL", "LOCAL", "DETAILED", "DETAILED", "DETAILED", "DETAILED"]);
});

test("PINNED: a level is chosen with >, so exactly 5000000, 500000 and 50000 m belong to the level below", () => {
  assert.deepEqual([5000000, 500000, 50000].map(detailLevel), ["REGIONAL", "LOCAL", "DETAILED"]);
});

test("shouldShowDetail: the table of what shows at each level", () => {
  const table = (altitude) => ["labels", "rings", "trails", "altitudeLines", "hyperedges"].map((type) => shouldShowDetail(altitude, type));
  assert.deepEqual(table(6e6), [false, false, false, false, false]);   // GLOBAL
  assert.deepEqual(table(1e6), [false, false, false, false, true]);    // REGIONAL
  assert.deepEqual(table(1e5), [true, false, false, true, true]);      // LOCAL
  assert.deepEqual(table(1e3), [true, true, true, true, true]);        // DETAILED
});

test("shouldShowDetail: a kind it does not know always shows", () => {
  assert.deepEqual([6e6, 1e6, 1e5, 0].map((altitude) => shouldShowDetail(altitude, "sparkles")), [true, true, true, true]);
  assert.equal(shouldShowDetail(6e6, undefined), true);
});

test("clusterSettings: the defaults for what a layer leaves out", () => {
  assert.deepEqual(CLUSTER_DEFAULTS, {enabled: true, pixelRange: 45, minimumClusterSize: 2, clusterBillboards: true, clusterLabels: true, clusterPoints: true});
  assert.deepEqual(clusterSettings(), CLUSTER_DEFAULTS);
  assert.deepEqual(clusterSettings({}), CLUSTER_DEFAULTS);
  assert.deepEqual(clusterSettings({pixelRange: 50, minimumClusterSize: 3}), {...CLUSTER_DEFAULTS, pixelRange: 50, minimumClusterSize: 3});
});

test("PINNED: a layer's pixelRange or minimumClusterSize of 0 gives the default, and the flags are on unless exactly false", () => {
  const zero = clusterSettings({pixelRange: 0, minimumClusterSize: 0});
  assert.equal(zero.pixelRange, 45);
  assert.equal(zero.minimumClusterSize, 2);
  const off = clusterSettings({enabled: false, clusterBillboards: false, clusterLabels: false, clusterPoints: false});
  assert.deepEqual([off.enabled, off.clusterBillboards, off.clusterLabels, off.clusterPoints], [false, false, false, false]);
  const odd = clusterSettings({enabled: 0, clusterBillboards: null, clusterLabels: "", clusterPoints: undefined});
  assert.deepEqual([odd.enabled, odd.clusterBillboards, odd.clusterLabels, odd.clusterPoints], [true, true, true, true]);
});

// --- the tool ---

test("registered but inactive, the controls say what the page saw before initialize: everything shows, nothing is created, nothing is announced", () => {
  const {controls, announced} = setup();
  assert.equal(controls.viewer, null);
  assert.equal(controls.currentAltitude, 0);
  assert.deepEqual(["labels", "rings", "trails", "altitudeLines", "hyperedges", "x"].map(controls.shouldShowDetail), [true, true, true, true, true, true]);
  assert.equal(controls.createClusteredDataSource("AIS_Vessels", {}), null);
  controls.updateVisibilityForAltitude();
  assert.deepEqual(announced, []);
});

test("initialize hands over the viewer, reads the altitude, and returns the controls; the first altitude is not announced", async () => {
  const {workspace, globe, announced, controls} = setup();
  await workspace.activate(RENDER_ID);
  globe.height = 120000;
  const viewer = {id: "v"};
  assert.equal(controls.initialize(viewer), controls);
  assert.equal(controls.viewer, viewer);
  assert.equal(controls.currentAltitude, 120000);
  assert.equal(controls.shouldShowDetail("rings"), false);
  assert.equal(controls.shouldShowDetail("labels"), true);
  assert.deepEqual(announced, []);   // PINNED
});

test("a camera change announces {altitude, level}, and the layers' answers follow it", async () => {
  const {workspace, globe, announced, controls} = setup();
  await workspace.activate(RENDER_ID);
  controls.initialize({});
  globe.camera(9e6);
  globe.camera(20000);
  assert.deepEqual(announced, [{altitude: 9e6, level: "GLOBAL"}, {altitude: 20000, level: "DETAILED"}]);
  assert.equal(controls.currentAltitude, 20000);
  assert.equal(controls.shouldShowDetail("trails"), true);
  globe.camera(7e5);
  assert.equal(controls.shouldShowDetail("trails"), false);
});

test("updateVisibilityForAltitude announces the current altitude without a camera change", async () => {
  const {workspace, globe, announced, controls} = setup();
  await workspace.activate(RENDER_ID);
  globe.height = 60000;
  controls.initialize({});
  controls.updateVisibilityForAltitude();
  assert.deepEqual(announced, [{altitude: 60000, level: "LOCAL"}]);
});

test("initialize with no viewer listens to nothing", async () => {
  const {workspace, globe, announced, controls} = setup();
  await workspace.activate(RENDER_ID);
  controls.initialize(null);
  assert.equal(globe.listeners.size, 0);
  globe.camera(9e6);
  assert.deepEqual(announced, []);
  assert.equal(controls.createClusteredDataSource("x"), null);
});

test("a viewer handed over before the tool is active is used when it activates", async () => {
  const {workspace, globe, controls} = setup();
  globe.height = 8e6;
  const viewer = {id: "early"};
  assert.equal(controls.initialize(viewer), controls);
  assert.equal(controls.viewer, null);
  await workspace.activate(RENDER_ID);
  assert.equal(controls.viewer, viewer);
  assert.equal(controls.currentAltitude, 8e6);
  assert.equal(globe.listeners.size, 1);
});

test("createClusteredDataSource: a data source with the layer's settings, handed back to it; null without a viewer", async () => {
  const {workspace, globe, controls} = setup();
  await workspace.activate(RENDER_ID);
  assert.equal(controls.createClusteredDataSource("AIS_Vessels", {pixelRange: 50}), null);   // active, but no viewer yet
  controls.initialize({});
  const source = controls.createClusteredDataSource("AIS_Vessels", {pixelRange: 50, minimumClusterSize: 3});
  assert.equal(source, globe.sources[0]);
  assert.equal(source.name, "AIS_Vessels");
  assert.deepEqual(source.clustering, {...CLUSTER_DEFAULTS, pixelRange: 50, minimumClusterSize: 3});
  assert.deepEqual(controls.createClusteredDataSource("Recon_Entities").clustering, CLUSTER_DEFAULTS);   // options are optional
});

test("initialize again replaces the camera subscription instead of adding a second", async () => {
  const {workspace, globe, announced, controls} = setup();
  await workspace.activate(RENDER_ID);
  controls.initialize({});
  controls.initialize({});
  assert.equal(globe.listeners.size, 1);
  assert.equal(globe.unsubscribed, 1);
  globe.camera(9e6);
  assert.equal(announced.length, 1);   // not twice
});

test("deactivating unsubscribes the camera and takes down every data source, including two with one name", async () => {
  const {workspace, globe, announced, controls} = setup();
  await workspace.activate(RENDER_ID);
  controls.initialize({});
  controls.createClusteredDataSource("AIS_Vessels");
  controls.createClusteredDataSource("AIS_Vessels");
  controls.createClusteredDataSource("Recon_Entities");
  await workspace.deactivate(RENDER_ID);
  assert.equal(globe.listeners.size, 0);
  assert.deepEqual(globe.sources.map((s) => s.in), [false, false, false]);
  assert.equal(globe.removed.length, 3);
  globe.camera(9e6);
  assert.deepEqual(announced, []);
  assert.equal(controls.viewer, null);
  assert.equal(controls.currentAltitude, 0);
  assert.equal(controls.shouldShowDetail("labels"), true);
  assert.equal(controls.createClusteredDataSource("x"), null);
  controls.updateVisibilityForAltitude();
  assert.deepEqual(announced, []);
});

test("a camera change that arrives as the tool is deactivated is dropped", async () => {
  const {workspace, globe, announced, controls} = setup();
  await workspace.activate(RENDER_ID);
  controls.initialize({});
  const listener = [...globe.listeners][0];
  await workspace.deactivate(RENDER_ID);
  globe.height = 9e6;
  listener();
  assert.deepEqual(announced, []);
});

test("activating again after deactivating starts from nothing: no sources, a new subscription once the viewer is handed over", async () => {
  const {workspace, globe, controls} = setup();
  await workspace.activate(RENDER_ID);
  controls.initialize({});
  controls.createClusteredDataSource("a");
  await workspace.deactivate(RENDER_ID);
  await workspace.activate(RENDER_ID);
  assert.equal(globe.listeners.size, 0);
  controls.initialize({});
  assert.equal(globe.listeners.size, 1);
  await workspace.deactivate(RENDER_ID);
  assert.equal(globe.removed.length, 1);   // the first source was not taken down twice
});

test("a viewer handed over early is used once: a later activation does not use it again", async () => {
  const {workspace, globe, controls} = setup();
  controls.initialize({id: "early"});
  await workspace.activate(RENDER_ID);
  assert.equal(globe.listeners.size, 1);
  await workspace.deactivate(RENDER_ID);
  await workspace.activate(RENDER_ID);
  assert.equal(globe.listeners.size, 0);
});
