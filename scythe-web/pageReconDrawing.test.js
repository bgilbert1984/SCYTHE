import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import * as ReconLogic from "./reconLogic.js";
import {createReconStore} from "./reconStore.js";

const page = readFileSync(new URL("../command-ops-visualization.html", import.meta.url), "utf8");

const objectStart = page.search(/^\s*const ReconDrawing = \{/m);
const objectEnd = page.search(/^\s*window\.ReconDrawing = ReconDrawing;/m);
assert.ok(objectStart > 0 && objectEnd > objectStart);
const reconObject = page.slice(objectStart, objectEnd);

/** A member of the page's ReconDrawing object (function or async function), as source: from `name: ` to the member's closing brace. */
function memberSource(name) {
  const header = new RegExp(`^            ${name}: (?:async )?function\\(`, "mg");
  const found = [...reconObject.matchAll(header)];
  assert.equal(found.length, 1, `${name} is defined once in the object`);
  const end = reconObject.indexOf("\n            }", found[0].index);
  return reconObject.slice(found[0].index, end + "\n            }".length).trim();
}

/** The page object with some of its members, sharing a real store's map. */
function recon(names, extra = {}, globals = {}) {
  const store = createReconStore();
  const calls = [];
  const object = {
    visible: true, entities: store.entities, entityMarkers: {}, perfMetrics: {entityUpdates: 0, updateCount: 0, lastUpdateMs: 0, avgUpdateMs: 0},
    updateInterval: null, _reconSSE: null, lastUpdateTimestamp: null,
    removeEntityMarker: (id) => calls.push(["remove", id]), addEntityMarker: (e) => calls.push(["add", e.entity_id]),
    updateEntityPosition: (e) => calls.push(["position", e.entity_id]), loadAlerts: () => calls.push(["alerts"]), loadEntities: () => calls.push(["load"]),
    renderEntities: () => calls.push(["render"]),
    ...extra,
  };
  const els = {"recon-entity-count": {}};
  const scope = {
    ReconLogic, ReconStore: store, console: {log() {}, debug() {}, warn() {}, error() {}}, document: {getElementById: (id) => els[id]},
    window: {updateReconList: () => calls.push(["list"])}, addConsoleMessage: () => {}, ...globals,
  };
  const keys = Object.keys(scope);
  const source = `return {${names.map(memberSource).join(",\n")}};`;
  Object.assign(object, new Function(...keys, source)(...keys.map((k) => scope[k])));
  return {object, store, calls, els, scope};
}

/** `addEntityMarker` against a stand-in for Cesium that accepts anything. */
function marker({mldc = true} = {}) {
  const anything = () => new Proxy(function () {}, {get: (t, k) => (k === Symbol.toPrimitive ? () => 0 : anything()), apply: () => anything(), construct: () => ({})});
  const labelled = [];
  const win = mldc ? {__MLDC__: {setLabelData: (labels) => labelled.push(labels)}} : {};
  const added = [];
  const viewer = {entities: {getById: () => null, add: (spec) => { added.push(spec); return spec; }}};
  const r = recon(["addEntityMarker"], {viewer, dataSource: null, showLabels: true, DISPOSITION_COLORS: {}, createEntityIcon: () => "icon", createEntityDescription: () => "desc", addThreatIndicator() {}},
    {Cesium: anything(), HierarchicalRenderManager: undefined, window: win, Date: {now: () => 1700000000000}});
  return {...r, labelled, win, added};
}
const located = (id, extra = {}) => ({entity_id: id, name: id.toUpperCase(), disposition: "HOSTILE", threat_level: "HIGH", location: {lat: 36, lon: -86, altitude_m: 0}, ...extra});

test("a marker carries the entity's description (from ReconLogic) and its id and name", () => {
  const {object, added} = marker();
  object.addEntityMarker(located("a"));
  assert.equal(added[0].description, ReconLogic.describeEntity(located("a")));
  assert.equal(added[0].id, "recon_entity_a");
  assert.equal(added[0].name, "A (a)");
});

test("a marker for an entity pushes its label to the MLDC layer, which keeps every entity's", () => {
  const {object, labelled, win} = marker();
  object.addEntityMarker(located("a"));
  object.addEntityMarker(located("b"));
  assert.deepEqual(Object.keys(win._mldcLabelMap), ["a", "b"]);
  assert.deepEqual(win._mldcLabelMap.a, ReconLogic.mldcLabel(located("a"), 1700000000000));
  assert.equal(labelled.length, 2);
  assert.deepEqual(labelled[1].map((l) => l.id), ["a", "b"]);
});

test("without the MLDC layer a marker makes no label map", () => {
  const {object, win, added} = marker({mldc: false});
  object.addEntityMarker(located("a"));
  assert.equal(added.length, 1);
  assert.equal("_mldcLabelMap" in win, false);
});

test("no marker, and no label, for an entity without a numeric location", () => {
  const {object, win, added} = marker();
  object.addEntityMarker(located("a", {location: {lat: "1", lon: 2}}));
  assert.equal(added.length, 0);
  assert.equal("_mldcLabelMap" in win, false);
});

/** Some of the drawing's members, bound to a fake drawing with its collaborators recorded. */
function drawing(names, extra = {}, globals = {}) {
  const calls = [];
  const object = {
    viewer: null, visible: false, showProximityRings: true, showLabels: true, dataSource: null, referencePoint: {lat: 0, lon: 0},
    entities: {},
    setupClusterStyling() { calls.push(["styling"]); }, setupAltitudeListener() { calls.push(["altitudeListener"]); },
    clearAllEntities() { calls.push(["clear"]); }, addReferencePointMarker() { calls.push(["reference"]); }, addProximityRings() { calls.push(["rings"]); },
    addEntityMarker(e) { calls.push(["marker", e.entity_id]); }, updateProximityRingsVisibility() { calls.push(["ringsVisibility"]); },
    updateLabelVisibility() { calls.push(["labelVisibility"]); },
    ...extra,
  };
  const scope = {console: {log() {}, warn() {}}, HierarchicalRenderManager: undefined, ...globals};
  const keys = Object.keys(scope);
  Object.assign(object, new Function(...keys, `return {${names.map(memberSource).join(",\n")}};`)(...keys.map((k) => scope[k])));
  return {object, calls};
}

test("attaching keeps the viewer, makes the clustered data source once and styles it, binds the altitude listener, and returns the drawing", () => {
  const made = [];
  const render = {viewer: {id: "v"}, createClusteredDataSource: (name, options) => { const s = {name, options}; made.push(s); return s; }};
  const {object, calls} = drawing(["attach"], {}, {HierarchicalRenderManager: render});
  assert.equal(object.attach({id: "v"}), object);
  object.attach({id: "v2"});
  assert.equal(object.viewer.id, "v2");
  assert.deepEqual(made, [{name: "Recon_Entities", options: {pixelRange: 45, minimumClusterSize: 2}}]);
  assert.equal(object.dataSource, made[0]);
  assert.deepEqual(calls, [["styling"], ["altitudeListener"], ["altitudeListener"]]);
});

test("attaching without the render tool's viewer makes no data source", () => {
  const {object, calls} = drawing(["attach"], {}, {HierarchicalRenderManager: {viewer: null, createClusteredDataSource() { throw new Error("no"); }}});
  object.attach({id: "v"});
  assert.equal(object.dataSource, null);
  assert.deepEqual(calls, [["altitudeListener"]]);
  const {object: bare} = drawing(["attach"]);
  bare.attach({id: "v"});
  assert.equal(bare.dataSource, null);
});

test("the setters set what the tool decides, and the visibility ones update what is drawn", () => {
  const {object, calls} = drawing(["setVisible", "setShowRings", "setShowLabels", "setReferencePoint"]);
  object.setVisible(true);
  assert.equal(object.visible, true);
  object.setShowRings(false);
  assert.equal(object.showProximityRings, false);
  object.setShowLabels(false);
  assert.equal(object.showLabels, false);
  object.setReferencePoint({lat: 5, lon: 6});
  assert.deepEqual(object.referencePoint, {lat: 5, lon: 6});
  assert.deepEqual(calls, [["ringsVisibility"], ["labelVisibility"]]);
});

test("the camera position is the camera's, in degrees", () => {
  const Cesium = {Cartographic: {fromCartesian: (p) => ({latitude: p.lat, longitude: p.lon})}, Math: {toDegrees: (r) => r * 100}};
  const {object} = drawing(["cameraPosition"], {viewer: {camera: {position: {lat: 0.5, lon: 0.25}}}}, {Cesium});
  assert.deepEqual(object.cameraPosition(), {lat: 50, lon: 25});
});

test("drawing the layer needs a viewer and a visible layer, and says whether it drew", () => {
  const none = drawing(["renderEntities"], {visible: true});
  assert.equal(none.object.renderEntities(), false);
  const hidden = drawing(["renderEntities"], {viewer: {}, visible: false});
  assert.equal(hidden.object.renderEntities(), false);
  assert.deepEqual([none.calls, hidden.calls], [[], []]);
});

test("drawing the layer clears, marks the reference point, draws the rings if wanted, and draws each entity, without touching the panel's list", () => {
  const updateReconList = () => { throw new Error("the drawing does not refresh the list"); };
  const entities = {a: {entity_id: "a"}, b: {entity_id: "b"}};
  const full = drawing(["renderEntities"], {viewer: {}, visible: true, entities}, {window: {updateReconList}});
  assert.equal(full.object.renderEntities(), true);
  assert.deepEqual(full.calls, [["clear"], ["reference"], ["rings"], ["marker", "a"], ["marker", "b"]]);
  const noRings = drawing(["renderEntities"], {viewer: {}, visible: true, showProximityRings: false, entities});
  noRings.object.renderEntities();
  assert.deepEqual(noRings.calls, [["clear"], ["reference"], ["marker", "a"], ["marker", "b"]]);
});

test("the rings are drawn only when the render tool says the altitude is low enough", () => {
  const detail = (shown) => ({shouldShowDetail: (what) => { assert.equal(what, "rings"); return shown; }});
  const low = drawing(["renderEntities"], {viewer: {}, visible: true}, {HierarchicalRenderManager: detail(true)});
  low.object.renderEntities();
  assert.deepEqual(low.calls, [["clear"], ["reference"], ["rings"]]);
  const high = drawing(["renderEntities"], {viewer: {}, visible: true}, {HierarchicalRenderManager: detail(false)});
  high.object.renderEntities();
  assert.deepEqual(high.calls, [["clear"], ["reference"]]);
});

test("the drawing's entities are the store's, and the altitude listener is bound once", () => {
  assert.match(reconObject, /get entities\(\) \{ return ReconStore\.entities; \}/);
  const {object} = drawing(["setupAltitudeListener"], {setupAltitudeListener: undefined}, {document: {addEventListener(event) { (object._events ||= []).push(event); }}});
  object.setupAltitudeListener();
  object.setupAltitudeListener();
  assert.deepEqual(object._events, ["altitudeChanged"]);
});
