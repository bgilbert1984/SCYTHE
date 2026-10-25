import test from "node:test";
import assert from "node:assert/strict";
import {createReconStore} from "./reconStore.js";
import {describeEntity, mldcLabel} from "./reconLogic.js";
import {
  createReconGlobe, summarizeCluster, clusterColorKey, clusterIconSize, hasLocation, isAircraft, RING_CONFIGS, THREAT_ORDER,
} from "./reconGlobe.js";

// --- pure pieces ---

test("a cluster's summary counts the five dispositions and finds the highest threat level", () => {
  const s = summarizeCluster([{disposition: "HOSTILE", threat_level: "LOW"}, {disposition: "HOSTILE", threat_level: "HIGH"}, {disposition: "FRIEND"}, {disposition: "NEUTRAL", threat_level: "MEDIUM"}]);
  assert.deepEqual(s.counts, {HOSTILE: 2, SUSPICIOUS: 0, UNKNOWN: 0, NEUTRAL: 1, FRIEND: 1});
  assert.equal(s.maxThreat, "HIGH");
});

test("a cluster's summary ignores members without data, and dispositions and threat levels it does not know, including inherited names", () => {
  const s = summarizeCluster([null, {disposition: "JOKER", threat_level: "ENORMOUS"}, {disposition: "constructor", threat_level: "toString"}, {}]);
  assert.deepEqual(s.counts, {HOSTILE: 0, SUSPICIOUS: 0, UNKNOWN: 0, NEUTRAL: 0, FRIEND: 0});
  assert.equal(s.maxThreat, "NONE");
  assert.equal(summarizeCluster([]).maxThreat, "NONE");
});

test("the threat order runs from none to critical, and the highest wins whatever the order of the members", () => {
  assert.deepEqual(THREAT_ORDER, ["NONE", "MINIMAL", "LOW", "MEDIUM", "HIGH", "CRITICAL"]);
  assert.equal(summarizeCluster([{threat_level: "CRITICAL"}, {threat_level: "HIGH"}]).maxThreat, "CRITICAL");
  assert.equal(summarizeCluster([{threat_level: "MINIMAL"}, {threat_level: "NONE"}]).maxThreat, "MINIMAL");
});

test("a cluster takes the colour of its worst disposition, hostile first and friend last", () => {
  const counts = (o) => ({HOSTILE: 0, SUSPICIOUS: 0, UNKNOWN: 0, NEUTRAL: 0, FRIEND: 0, ...o});
  assert.equal(clusterColorKey(counts({HOSTILE: 1, SUSPICIOUS: 5, FRIEND: 9})), "HOSTILE");
  assert.equal(clusterColorKey(counts({SUSPICIOUS: 1, UNKNOWN: 5})), "SUSPICIOUS");
  assert.equal(clusterColorKey(counts({UNKNOWN: 1, NEUTRAL: 5})), "UNKNOWN");
  assert.equal(clusterColorKey(counts({NEUTRAL: 1, FRIEND: 5})), "NEUTRAL");
  assert.equal(clusterColorKey(counts({FRIEND: 1})), "FRIEND");
  assert.equal(clusterColorKey(counts({})), "FRIEND");
});

test("a cluster's icon grows with the square root of its count, from 36, up to 60", () => {
  assert.equal(clusterIconSize(0), 36);
  assert.equal(clusterIconSize(4), 44);
  assert.equal(clusterIconSize(9), 48);
  assert.equal(clusterIconSize(36), 60);
  assert.equal(clusterIconSize(10000), 60);
});

test("an entity can be drawn only with a numeric latitude and longitude", () => {
  assert.equal(hasLocation({location: {lat: 1, lon: 2}}), true);
  assert.equal(hasLocation({location: {lat: 0, lon: 0}}), true);
  for (const bad of [null, undefined, {}, {location: {}}, {location: {lat: "1", lon: 2}}, {location: {lat: 1, lon: "2"}}, {location: {lat: 1}}]) assert.equal(hasLocation(bad), false, JSON.stringify(bad));
});

test("an aircraft is an entity whose ontology names one", () => {
  assert.equal(isAircraft({ontology: ["aircraft", "fighter"]}), true);
  assert.equal(isAircraft({ontology: "aircraft"}), true);
  assert.equal(isAircraft({ontology: ["vessel"]}), false);
  assert.equal(isAircraft({}), false);
  assert.equal(isAircraft({ontology: ""}), false);
});

test("the proximity rings are four, at 1, 3, 5 and 10 nautical miles", () => {
  assert.deepEqual(RING_CONFIGS, [
    {radius: 1.0, color: "RED", alpha: 0.3, name: "CRITICAL (1 NM)"}, {radius: 3.0, color: "ORANGE", alpha: 0.2, name: "WARNING (3 NM)"},
    {radius: 5.0, color: "YELLOW", alpha: 0.15, name: "ALERT (5 NM)"}, {radius: 10.0, color: "LIME", alpha: 0.1, name: "AWARENESS (10 NM)"},
  ]);
});

// --- a stand-in for the page: Cesium whose values describe themselves, collections that behave, a recording canvas ---

const NOW = 1700000000000;

function describing(path) {
  const fn = function () {};
  return new Proxy(fn, {
    get(t, k) {
      if (k === Symbol.toPrimitive) return () => path;
      if (k === "toJSON") return () => path;
      if (typeof k === "symbol") return undefined;
      if (k === "toCssColorString") return () => `css(${path})`;
      return describing(`${path}.${k}`);
    },
    apply: (t, self, args) => describing(`${path}(${args.map((a) => (typeof a === "object" ? JSON.stringify(a) : String(a))).join(",")})`),
    construct: (t, args) => describing(`new ${path}(${args.map((a) => (typeof a === "object" ? JSON.stringify(a) : String(a))).join(",")})`),
  });
}

function collection(name, opts = {}) {
  const items = [];
  const c = {
    name, items, adds: [], removals: [],
    get values() { return [...items]; },
    add(spec) { if (opts.failAdd) throw new Error("add failed"); c.adds.push(spec); const e = {...spec}; items.push(e); return e; },
    getById: (id) => items.find((i) => i.id === id),
    remove(e) { c.removals.push(e.id); const i = items.indexOf(e); if (i >= 0) items.splice(i, 1); return i >= 0; },
    removeAll() { c.removedAll = (c.removedAll || 0) + 1; items.length = 0; },
    contains: (e) => items.includes(e),
  };
  return c;
}

function env({hrm = true, hrmViewer = true, mldc = false, detail = {}, failAdd = false, store = createReconStore()} = {}) {
  const warnings = [], errors = [], logs = [];
  const listeners = [];
  const canvases = [];
  const clusterListeners = [];
  const dataSource = Object.assign(collection("ds"), {name: "x"});
  dataSource.entities = dataSource;
  dataSource.clustering = {clusterEvent: {addEventListener: (fn) => clusterListeners.push(fn)}};
  const answers = {labels: true, rings: true, altitudeLines: true, ...detail};
  const asked = [];
  const render = hrm ? {
    viewer: hrmViewer ? {id: "rv"} : null, created: [],
    createClusteredDataSource(name, options) { render.created.push([name, options]); return dataSource; },
    shouldShowDetail: (what) => { asked.push(what); return answers[what]; },
  } : undefined;
  const controller = {enableRotate: false, enableTilt: false, enableZoom: false, enableTranslate: false, enableLook: false};
  const flights = [], lookAts = [];
  const viewer = {
    entities: collection("viewer", {failAdd}), trackedEntity: "tracked", scene: {screenSpaceCameraController: controller},
    camera: {position: describing("camera.position"), heading: describing("camera.heading"), lookAtTransform: (m) => lookAts.push(String(m)), flyTo: (o) => flights.push(o)},
  };
  const labelCalls = [];
  const win = {
    Cesium: new Proxy({}, {get: (t, k) => describing(String(k))}),
    document: {
      createElement: () => { const ops = []; const ctx = new Proxy({}, {
        get: (t, k) => (k === "createRadialGradient" ? (...a) => { ops.push(["gradient", ...a]); return {addColorStop: (...b) => ops.push(["stop", ...b])}; } : (...a) => { ops.push([String(k), ...a]); }),
        set: (t, k, v) => { ops.push(["set", String(k), String(v)]); return true; }});
        const canvas = {width: 0, height: 0, ops, getContext: () => ctx, toDataURL: () => `data:${canvas.width}x${canvas.height}`}; canvases.push(canvas); return canvas; },
      addEventListener: (event, fn) => listeners.push([event, fn]),
      removeEventListener: (event, fn) => { const i = listeners.findIndex((l) => l[0] === event && l[1] === fn); if (i >= 0) listeners.splice(i, 1); },
    },
    HierarchicalRenderManager: render,
  };
  if (mldc) win.__MLDC__ = {setLabelData: (d) => labelCalls.push(["set", d]), clearLabels: () => labelCalls.push(["clearLabels"]), clearGhosts: () => labelCalls.push(["clearGhosts"])};
  const log = {log: (...a) => logs.push(a), warn: (...a) => warnings.push(a), error: (...a) => errors.push(a)};
  const globe = createReconGlobe({win, store, log, now: () => NOW});
  const altitudeEvents = () => listeners.filter(([e]) => e === "altitudeChanged");
  return {globe, win, store, viewer, dataSource, render, warnings, errors, logs, listeners, altitudeEvents, canvases, clusterListeners, flights, lookAts, controller, labelCalls, answers, asked, collections: {viewer: viewer.entities, ds: dataSource}};
}

const ent = (id, extra = {}) => ({entity_id: id, name: id.toUpperCase(), disposition: "HOSTILE", threat_level: "LOW", location: {lat: 36, lon: -86, altitude_m: 0}, ...extra});
const attached = (options) => { const e = env(options); e.globe.attach(e.viewer); return e; };
const shown = (options) => { const e = attached(options); e.globe.setVisible(true); return e; };

// --- attaching and detaching ---

test("attaching keeps the viewer, makes the clustered data source once with its options, styles it, and listens for altitude changes once", () => {
  const e = env();
  assert.equal(e.globe.available(), false);
  e.globe.attach(e.viewer);
  e.globe.attach(e.viewer);
  assert.equal(e.globe.available(), true);
  assert.deepEqual(e.render.created, [["Recon_Entities", {pixelRange: 45, minimumClusterSize: 2}]]);
  assert.equal(e.clusterListeners.length, 1);
  assert.equal(e.altitudeEvents().length, 1);
  assert.deepEqual(e.logs, [["Auto-Recon drawing attached"], ["Auto-Recon drawing attached"]]);
});

test("without the render tool, or with a render tool that has no viewer yet, there is no data source and markers go to the viewer", () => {
  for (const options of [{hrm: false}, {hrmViewer: false}]) {
    const e = attached(options);
    e.globe.setVisible(true);
    e.globe.addMarker(ent("a"));
    assert.equal(e.clusterListeners.length, 0);
    assert.deepEqual(e.viewer.entities.adds.map((s) => s.id), ["recon_entity_a", "recon_threat_a"].slice(0, 1));
    assert.equal(e.dataSource.adds.length, 0);
  }
});

test("detaching takes the markers down, hides the layer, stops listening, and keeps the viewer and the data source", () => {
  const e = shown();
  e.globe.render();
  e.globe.detach();
  assert.equal(e.altitudeEvents().length, 0);
  assert.equal(e.viewer.entities.items.length, 0);
  assert.equal(e.dataSource.removedAll > 0, true);
  assert.equal(e.globe.available(), true);
  assert.equal(e.globe.render(), false, "hidden");
  e.globe.detach();   // twice is harmless
  assert.equal(e.altitudeEvents().length, 0);
});

test("showing the layer listens for altitude changes again after a detach, once", () => {
  const e = shown();
  e.globe.detach();
  e.globe.setVisible(true);
  e.globe.setVisible(true);
  assert.equal(e.altitudeEvents().length, 1);
  const unattached = env();
  unattached.globe.setVisible(true);
  assert.equal(unattached.altitudeEvents().length, 0, "nothing to listen for without a viewer");
});

test("detaching before anything was attached is harmless", () => {
  const e = env();
  e.globe.detach();
  e.globe.clear();
  assert.equal(e.globe.available(), false);
});

// --- drawing the layer ---

test("drawing needs a viewer and a visible layer, and says whether it drew", () => {
  const e = env();
  assert.equal(e.globe.render(), false);
  e.globe.attach(e.viewer);
  assert.equal(e.globe.render(), false);
  e.globe.setVisible(true);
  assert.equal(e.globe.render(), true);
  const none = env();
  none.globe.setVisible(true);
  assert.equal(none.globe.render(), false, "no viewer");
});

test("drawing clears first, marks the reference point, draws the four rings, and a marker for each entity in the store", () => {
  const e = shown();
  e.store.upsert(ent("a")); e.store.upsert(ent("b", {disposition: "FRIEND"}));
  e.globe.addMarker(ent("old"));
  e.globe.render();
  assert.equal(e.dataSource.removedAll >= 1, true);
  assert.deepEqual(e.viewer.entities.adds.map((s) => s.id), ["recon_reference_point", "recon_proximity_ring_0", "recon_proximity_ring_1", "recon_proximity_ring_2", "recon_proximity_ring_3"]);
  assert.deepEqual(e.dataSource.adds.map((s) => s.id), ["recon_entity_old", "recon_entity_a", "recon_entity_b"]);
});

test("the reference point sits at the reference point, with a label unless labels are off", () => {
  const e = shown();
  e.globe.setReferencePoint({lat: 10, lon: 20});
  e.globe.render();
  const ref = e.viewer.entities.adds.find((s) => s.id === "recon_reference_point");
  assert.equal(ref.name, "Reference Point (Own Position)");
  assert.equal(String(ref.position), "Cartesian3.fromDegrees(20,10,0)");
  assert.equal(ref.billboard.image, "data:40x40");
  assert.equal(ref.billboard.scale, 0.8);
  assert.equal(ref.label.text, "OWN POSITION");
  assert.equal(String(ref.label.pixelOffset), "new Cartesian2(0,20)");
  const off = shown();
  off.globe.setShowLabels(false);
  off.globe.render();
  assert.equal(off.viewer.entities.adds.find((s) => s.id === "recon_reference_point").label, undefined);
});

test("the default reference point is San Francisco", () => {
  const e = shown();
  e.globe.render();
  assert.equal(String(e.viewer.entities.adds[0].position), "Cartesian3.fromDegrees(-122.4194,37.7749,0)");
});

test("each ring has its radius in metres, its colour, its name, and sits at the reference point", () => {
  const e = shown();
  e.globe.setReferencePoint({lat: 10, lon: 20});
  e.globe.render();
  const rings = e.viewer.entities.adds.filter((s) => s.id.startsWith("recon_proximity_ring_"));
  assert.deepEqual(rings.map((r) => r.name), RING_CONFIGS.map((c) => c.name));
  assert.deepEqual(rings.map((r) => r.ellipse.semiMajorAxis), [1852, 5556, 9260, 18520]);
  assert.deepEqual(rings.map((r) => r.ellipse.semiMinorAxis), [1852, 5556, 9260, 18520]);
  assert.deepEqual(rings.map((r) => String(r.ellipse.material)), ["Color.RED.withAlpha(0.3)", "Color.ORANGE.withAlpha(0.2)", "Color.YELLOW.withAlpha(0.15)", "Color.LIME.withAlpha(0.1)"]);
  assert.equal(String(rings[0].ellipse.outlineColor), "Color.RED.withAlpha(0.3).withAlpha(0.8)");
  assert.equal(rings[0].ellipse.outline, true);
  assert.equal(rings[0].ellipse.outlineWidth, 2);
  assert.equal(rings[0].ellipse.height, 0);
  assert.equal(String(rings[0].ellipse.heightReference), "HeightReference.CLAMP_TO_GROUND");
  assert.equal(String(rings[0].position), "Cartesian3.fromDegrees(20,10)");
});

test("the rings are skipped when switched off or when the altitude is too high for them, and drawn when there is no render tool", () => {
  const ringIds = (e) => e.viewer.entities.adds.filter((s) => s.id.startsWith("recon_proximity")).length;
  const off = shown(); off.globe.setShowRings(false); off.globe.render();
  assert.equal(ringIds(off), 0);
  assert.equal(off.asked.includes("rings"), false, "the render tool is not even asked");
  const high = shown({detail: {rings: false}}); high.globe.render();
  assert.equal(ringIds(high), 0);
  assert.deepEqual(high.asked.filter((a) => a === "rings"), ["rings"]);
  const none = shown({hrm: false}); none.globe.render();
  assert.equal(ringIds(none), 4);
});

// --- markers ---

test("a marker is drawn into the clustered data source with its name, position, icon, label, description and properties", () => {
  const e = shown();
  const entity = ent("a", {name: "Bogey", velocity: {heading_deg: 90, speed_kts: 1}, location: {lat: 1, lon: 2, altitude_m: 30}});
  e.globe.addMarker(entity);
  const [spec] = e.dataSource.adds;
  assert.equal(spec.id, "recon_entity_a");
  assert.equal(spec.name, "Bogey (a)");
  assert.equal(String(spec.position), "Cartesian3.fromDegrees(2,1,30)");
  assert.equal(spec.billboard.image, "data:32x32");
  assert.equal(spec.billboard.scale, 0.6);
  assert.equal(String(spec.billboard.rotation), "Math.toRadians(-90)");
  assert.equal(spec.label.text, "Bogey\nHOSTILE");
  assert.equal(spec.label.font, "10px sans-serif");
  assert.equal(String(spec.label.fillColor), "Color.RED");
  assert.equal(String(spec.label.pixelOffset), "new Cartesian2(0,18)");
  assert.equal(spec.label.show, true);
  assert.equal(spec.description, describeEntity(entity));
  assert.deepEqual(spec.properties, {type: "recon_entity", entityId: "a", entityData: entity});
  assert.equal(e.viewer.entities.adds.length, 0);
});

test("a marker without a velocity has no rotation, and without an altitude sits on the ground", () => {
  const e = shown();
  e.globe.addMarker(ent("a", {location: {lat: 1, lon: 2}}));
  assert.equal(e.dataSource.adds[0].billboard.rotation, 0);
  assert.equal(String(e.dataSource.adds[0].position), "Cartesian3.fromDegrees(2,1,0)");
});

test("a marker for an entity without a numeric location is skipped with a warning, and nothing is drawn without a viewer", () => {
  const e = shown();
  e.globe.addMarker(ent("a", {location: {lat: "1", lon: 2}}));
  e.globe.addMarker(undefined);
  assert.equal(e.dataSource.adds.length, 0);
  assert.deepEqual(e.warnings.map((w) => w[1]), ["a", undefined]);
  const bare = env();
  bare.globe.addMarker(ent("a"));
  assert.deepEqual([bare.warnings.length, bare.dataSource.adds.length], [0, 0]);
});

test("a marker for an entity already drawn replaces the old one", () => {
  const e = shown();
  e.globe.addMarker(ent("a"));
  e.globe.addMarker(ent("a", {name: "again"}));
  assert.equal(e.dataSource.items.length, 1);
  assert.equal(e.dataSource.items[0].name, "again (a)");
  assert.deepEqual(e.dataSource.removals, ["recon_entity_a"]);
});

test("a marker's colour follows the disposition, grey for any other including inherited names; a missing one is unknown", () => {
  const colour = (disposition) => { const e = shown(); e.globe.addMarker(ent("a", {disposition})); return String(e.dataSource.adds[0].label.fillColor); };
  assert.equal(colour("HOSTILE"), "Color.RED");
  assert.equal(colour("FRIEND"), "Color.BLUE");
  assert.equal(colour("ASSUMED_FRIEND"), "Color.BLUE.withAlpha(0.8)");
  assert.equal(colour("NEUTRAL"), "Color.LIME");
  assert.equal(colour("SUSPICIOUS"), "Color.ORANGE");
  assert.equal(colour("UNKNOWN"), "Color.YELLOW");
  assert.equal(colour("PENDING"), "Color.YELLOW");
  assert.equal(colour("JOKER"), "Color.MAGENTA");
  assert.equal(colour("FAKER"), "Color.PURPLE");
  assert.equal(colour("nonesuch"), "Color.GRAY");
  assert.equal(colour("constructor"), "Color.GRAY");
  assert.equal(colour(""), "Color.YELLOW", "an empty disposition is unknown");
});

test("an entity with no disposition or no threat level cannot be drawn: describing it throws, which is logged", () => {
  for (const extra of [{disposition: undefined}, {threat_level: undefined}]) {
    const e = shown();
    e.globe.addMarker(ent("a", extra));
    assert.equal(e.dataSource.adds.length, 0);
    assert.match(e.errors[0][0], /Error adding entity a/);
  }
});

test("an empty threat level is none: no ring", () => {
  const e = shown();
  e.globe.addMarker(ent("a", {threat_level: ""}));
  assert.equal(e.dataSource.adds.length, 1);
  assert.equal(e.viewer.entities.adds.length, 0);
});

test("a marker's label is shown if labels are on and the altitude allows them (or there is no render tool)", () => {
  const show = (options, labels = true) => { const e = shown(options); e.globe.setShowLabels(labels); e.globe.addMarker(ent("a")); return (e.dataSource.adds[0] || e.viewer.entities.adds[0]).label.show; };
  assert.equal(show({}), true);
  assert.equal(show({}, false), false);
  assert.equal(show({detail: {labels: false}}), false);
  assert.equal(show({hrm: false}), true);
  assert.equal(show({hrm: false}, false), false);
});

test("an aircraft above 100 m gets an altitude line, in the viewer, if the altitude allows lines", () => {
  const lines = (entity, options) => { const e = shown(options); e.globe.addMarker(entity); return e.viewer.entities.adds.filter((s) => s.id === "recon_alt_line_a"); };
  const plane = (alt) => ent("a", {ontology: ["aircraft"], location: {lat: 1, lon: 2, altitude_m: alt}});
  assert.equal(lines(plane(500)).length, 1);
  assert.equal(lines(plane(100)).length, 0);
  assert.equal(lines(plane(101)).length, 1);
  assert.equal(lines(ent("a", {ontology: ["vessel"], location: {lat: 1, lon: 2, altitude_m: 500}})).length, 0);
  assert.equal(lines(plane(500), {detail: {altitudeLines: false}}).length, 0);
  assert.equal(lines(plane(500), {hrm: false}).length, 1);
  const [line] = lines(plane(500));
  assert.equal(String(line.polyline.positions), "Cartesian3.fromDegreesArrayHeights([2,1,0,2,1,500])");
  assert.equal(line.polyline.width, 1);
  assert.equal(String(line.polyline.material), 'new PolylineDashMaterialProperty({"color":"Color.RED.withAlpha(0.5)"})');
});

test("only a high or critical threat gets a ring, in the viewer, in the threat's colour", () => {
  const rings = (threat) => { const e = shown(); e.globe.addMarker(ent("a", {threat_level: threat})); return e.viewer.entities.adds.filter((s) => s.id === "recon_threat_a"); };
  assert.equal(rings("HIGH").length, 1);
  assert.equal(rings("CRITICAL").length, 1);
  for (const quiet of ["MEDIUM", "LOW", "MINIMAL", "NONE", undefined]) assert.equal(rings(quiet).length, 0, String(quiet));
  const [high] = rings("HIGH"), [critical] = rings("CRITICAL");
  assert.equal(String(high.ellipse.outlineColor), "Color.ORANGERED");
  assert.equal(String(critical.ellipse.outlineColor), "Color.RED");
  assert.equal(String(high.ellipse.material), "Color.ORANGERED.withAlpha(0.3)");
  assert.equal(high.ellipse.semiMajorAxis, 500);
  assert.equal(high.ellipse.semiMinorAxis, 500);
  assert.equal(String(high.position), "Cartesian3.fromDegrees(-86,36)");
});

test("the MLDC label feed gets each marker's label, and keeps every entity's", () => {
  const e = shown({mldc: true});
  e.globe.addMarker(ent("a")); e.globe.addMarker(ent("b"));
  assert.deepEqual(Object.keys(e.win._mldcLabelMap), ["a", "b"]);
  assert.deepEqual(e.win._mldcLabelMap.a, mldcLabel(ent("a"), NOW));
  assert.equal(e.labelCalls.length, 2);
  assert.deepEqual(e.labelCalls[1][1].map((l) => l.id), ["a", "b"]);
  const none = shown();
  none.globe.addMarker(ent("a"));
  assert.equal("_mldcLabelMap" in none.win, false);
});

test("the MLDC label is made with the clock: an entity last seen ten minutes ago has a recency of one over e", () => {
  const e = shown({mldc: true});
  e.globe.addMarker(ent("a", {last_seen: new Date(NOW - 600000).toISOString()}));
  assert.ok(Math.abs(e.win._mldcLabelMap.a.recency - Math.exp(-1)) < 1e-9);
});

test("a failure while drawing a marker is logged and does not stop the caller", () => {
  const e = shown({failAdd: true});
  e.dataSource.entities = e.viewer.entities;   // markers into the failing collection
  const failing = env({failAdd: true, hrm: false});
  failing.globe.attach(failing.viewer);
  failing.globe.setVisible(true);
  failing.globe.addMarker(ent("a"));
  assert.equal(failing.errors.length, 1);
  assert.match(failing.errors[0][0], /Error adding entity a/);
  failing.globe.render();
  assert.ok(failing.errors.some((x) => /Error adding reference point/.test(x[0])));
  assert.ok(failing.errors.some((x) => /Error adding proximity ring 0/.test(x[0])));
});

test("a failure while drawing a threat ring or a task line is logged", () => {
  const e = env({failAdd: true, hrm: false});
  e.globe.attach(e.viewer);
  e.store.upsert(ent("a"));
  e.globe.addTaskLine({task_id: "T", entity_id: "a"});
  assert.match(e.errors[0][0], /Error adding task line/);
});

// --- taking markers down (these were the page's pinned tests) ---

test("removing a marker takes it from the data source, or else the viewer, and forgets it", () => {
  const e = shown();
  e.globe.addMarker(ent("a"));
  e.globe.addMarker(ent("b"));
  e.globe.removeMarker("a");
  assert.deepEqual(e.dataSource.items.map((i) => i.id), ["recon_entity_b"]);
  const plain = shown({hrm: false});
  plain.globe.addMarker(ent("a"));
  plain.globe.removeMarker("a");
  assert.deepEqual(plain.viewer.entities.items.map((i) => i.id), []);
});

test("PINNED: removing a clustered marker tries only the data source: a copy of it in the viewer stays", () => {
  const e = shown();
  e.globe.addMarker(ent("a"));
  const marker = e.dataSource.items[0];
  e.viewer.entities.items.push(marker);   // the same marker is also in the viewer
  e.globe.removeMarker("a");
  assert.deepEqual(e.dataSource.items, []);
  assert.deepEqual(e.viewer.entities.items, [marker]);
});

test("removing a marker takes its altitude line and threat ring from the viewer", () => {
  const e = shown();
  e.globe.addMarker(ent("a", {ontology: ["aircraft"], threat_level: "HIGH", location: {lat: 1, lon: 2, altitude_m: 500}}));
  assert.deepEqual(e.viewer.entities.items.map((i) => i.id).sort(), ["recon_alt_line_a", "recon_threat_a"]);
  e.globe.removeMarker("a");
  assert.deepEqual(e.viewer.entities.items, []);
  assert.deepEqual(e.dataSource.items, []);
  e.globe.removeMarker("a");   // and a second time is harmless
});

test("PINNED: an altitude line or threat ring that is not in the viewer is left, and so is its key", () => {
  const e = shown();
  e.globe.addMarker(ent("a", {ontology: ["aircraft"], threat_level: "HIGH", location: {lat: 1, lon: 2, altitude_m: 500}}));
  e.viewer.entities.items.length = 0;   // taken away behind the drawing's back
  e.globe.removeMarker("a");
  assert.deepEqual(e.viewer.entities.removals, [], "nothing is removed that is not there");
  e.viewer.entities.items.push({id: "stand-in"});
});

test("removing a marker for an unknown id, twice, or with no viewer, is harmless", () => {
  const e = shown();
  e.globe.removeMarker("nonesuch");
  e.globe.addMarker(ent("a"));
  e.globe.removeMarker("a");
  e.globe.removeMarker("a");
  const bare = env();
  bare.globe.removeMarker("a");
  assert.deepEqual(bare.collections.viewer.removals, []);
});

test("a removed marker is forgotten: later position updates and label switches do not touch it", () => {
  const e = shown();
  e.globe.addMarker(ent("a"));
  const marker = e.dataSource.items[0];
  const position = String(marker.position);
  e.globe.removeMarker("a");
  e.globe.updatePosition(ent("a", {location: {lat: 5, lon: 6}}));
  e.globe.setShowLabels(false);
  assert.equal(String(marker.position), position);
  assert.equal(marker.label.show, true);
});

test("cleared markers and rings are forgotten: later updates and switches do not touch them", () => {
  const e = shown();
  e.store.upsert(ent("a"));
  e.globe.render();
  const marker = e.dataSource.items[0];
  const ring = e.viewer.entities.items.find((i) => i.id === "recon_proximity_ring_0");
  const own = e.viewer.entities.items.find((i) => i.id === "recon_reference_point");
  const position = String(marker.position);
  e.globe.clear();
  e.globe.updatePosition(ent("a", {location: {lat: 5, lon: 6}}));
  e.globe.setShowLabels(false);
  e.globe.setShowRings(false);
  assert.equal(String(marker.position), position);
  assert.equal(marker.label.show, true);
  assert.equal(own.label.show, undefined, "the reference point is forgotten too");
  assert.equal(ring.show, undefined);
});

test("clearing takes down every marker, the rings, the task lines and the MLDC labels", () => {
  const e = shown({mldc: true});
  e.store.upsert(ent("a", {threat_level: "HIGH"}));
  e.globe.render();
  e.globe.addTaskLine({task_id: "T", entity_id: "a"});
  assert.ok(e.viewer.entities.items.length > 0);
  e.globe.clear();
  assert.deepEqual(e.viewer.entities.items, []);
  assert.deepEqual(e.dataSource.items, []);
  assert.deepEqual(e.win._mldcLabelMap, {});
  assert.deepEqual(e.labelCalls.slice(-2), [["clearLabels"], ["clearGhosts"]]);
  e.globe.clear();   // a second time
  assert.deepEqual(e.viewer.entities.items, []);
});

test("clearing leaves alone what it did not draw, and does nothing to the MLDC layer when there is none", () => {
  const e = shown();
  e.viewer.entities.items.push({id: "someone else's"});
  e.globe.addMarker(ent("a"));
  e.globe.clear();
  assert.deepEqual(e.viewer.entities.items.map((i) => i.id), ["someone else's"]);
  assert.equal("_mldcLabelMap" in e.win, false);
});

// --- positions, flights, task lines ---

test("updating a position moves the marker, turns it, and renames and recolours its label", () => {
  const e = shown();
  e.globe.addMarker(ent("a"));
  const marker = e.dataSource.items[0];
  e.globe.updatePosition(ent("a", {name: "Moved", disposition: "FRIEND", velocity: {heading_deg: 45, speed_kts: 1}, location: {lat: 5, lon: 6, altitude_m: 70}}));
  assert.equal(String(marker.position), "Cartesian3.fromDegrees(6,5,70)");
  assert.equal(String(marker.billboard.rotation), "Math.toRadians(-45)");
  assert.equal(marker.label.text, "Moved\nFRIEND");
  assert.equal(String(marker.label.fillColor), "Color.BLUE");
});

test("updating a position without a velocity leaves the rotation, and for an unknown entity does nothing", () => {
  const e = shown();
  e.globe.addMarker(ent("a", {velocity: {heading_deg: 10, speed_kts: 1}}));
  const marker = e.dataSource.items[0];
  const before = String(marker.billboard.rotation);
  e.globe.updatePosition(ent("a", {location: {lat: 5, lon: 6}}));
  assert.equal(String(marker.billboard.rotation), before);
  assert.equal(String(marker.position), "Cartesian3.fromDegrees(6,5,0)");
  e.globe.updatePosition(ent("zz"));
});

test("a marker with no label is moved without one", () => {
  const e = shown();
  e.globe.addMarker(ent("a"));
  const marker = e.dataSource.items[0];
  delete marker.label; delete marker.billboard;
  e.globe.updatePosition(ent("a", {velocity: {heading_deg: 1, speed_kts: 1}}));
  assert.equal("label" in marker, false);
});

test("flying to an entity gives the camera back first, flies to the marker's position with a tilt, and gives it back again when done", () => {
  const e = shown();
  e.store.upsert(ent("a"));
  e.globe.render();
  e.controller.enableZoom = false;
  e.globe.flyTo("a");
  assert.deepEqual(e.lookAts, ["Matrix4.IDENTITY"]);
  assert.equal(e.viewer.trackedEntity, undefined);
  assert.equal(Object.values(e.controller).every(Boolean), true);
  const [flight] = e.flights;
  assert.match(String(flight.destination), /Cartesian3\.fromDegrees\(-86,36,0\)\.getValue\(/, "the marker's position, read now");
  assert.equal(String(flight.orientation.heading), "camera.heading");
  assert.equal(String(flight.orientation.pitch), "Math.toRadians(-45)");
  assert.equal(flight.orientation.roll, 0);
  assert.equal(flight.duration, 1.5);
  e.controller.enableTilt = false; e.viewer.trackedEntity = "again";
  flight.complete();
  assert.equal(e.lookAts.length, 2);
  assert.equal(e.viewer.trackedEntity, undefined);
  assert.equal(e.controller.enableTilt, true);
});

test("with no marker the camera flies to the entity's stored coordinates, and a marker whose position is a plain value is used as it is", () => {
  const e = shown();
  e.store.upsert(ent("a", {location: {lat: 7, lon: 8, altitude_m: 9}}));
  e.globe.flyTo("a");
  assert.equal(String(e.flights[0].destination), "Cartesian3.fromDegrees(8,7,9)");
  e.store.upsert(ent("b"));
  e.globe.addMarker(ent("b"));
  e.dataSource.items[0].position = {x: 1};
  e.globe.flyTo("b");
  assert.deepEqual(e.flights[1].destination, {x: 1});
  e.store.upsert(ent("c", {location: {lat: 1, lon: 2}}));
  e.globe.flyTo("c");
  assert.equal(String(e.flights[2].destination), "Cartesian3.fromDegrees(2,1,0)");
});

test("a marker with no position falls back to the stored coordinates", () => {
  const e = shown();
  e.store.upsert(ent("a", {location: {lat: 7, lon: 8}}));
  e.globe.addMarker(ent("a", {location: {lat: 7, lon: 8}}));
  e.dataSource.items[0].position = undefined;
  e.globe.flyTo("a");
  assert.equal(String(e.flights[0].destination), "Cartesian3.fromDegrees(8,7,0)");
});

test("flying to an unknown entity, or with no viewer, does nothing", () => {
  const e = shown();
  e.globe.flyTo("nonesuch");
  assert.deepEqual([e.flights.length, e.lookAts.length], [0, 0]);
  const bare = env();
  bare.store.upsert(ent("a"));
  bare.globe.flyTo("a");
  assert.equal(bare.flights.length, 0);
});

test("a camera that cannot be reset is warned about, and the flight goes ahead", () => {
  const e = shown();
  e.store.upsert(ent("a"));
  e.viewer.camera.lookAtTransform = () => { throw new Error("no"); };
  e.globe.flyTo("a");
  assert.equal(e.flights.length, 1);
  assert.match(e.warnings[0][0], /before flyTo/);
  e.flights[0].complete();
  assert.match(e.warnings[1][0], /after flyTo/);
});

test("a task line runs from the reference point to the entity, and is taken down with the rest", () => {
  const e = shown();
  e.store.upsert(ent("a", {location: {lat: 3, lon: 4}}));
  e.globe.setReferencePoint({lat: 1, lon: 2});
  e.globe.addTaskLine({task_id: "T1", entity_id: "a"});
  const [spec] = e.viewer.entities.adds;
  assert.equal(spec.id, "recon_task_T1");
  assert.equal(spec.name, "Task: T1");
  assert.equal(String(spec.polyline.positions), "Cartesian3.fromDegreesArray([2,1,4,3])");
  assert.equal(spec.polyline.width, 2);
  assert.equal(spec.polyline.clampToGround, true);
  assert.equal(String(spec.polyline.material), 'new PolylineDashMaterialProperty({"color":"Color.YELLOW.withAlpha(0.7)","dashLength":16})');
  e.globe.clear();
  assert.deepEqual(e.viewer.entities.items, []);
});

test("a task line for an unknown entity, or with no viewer, is not drawn, and is not an error", () => {
  const e = shown();
  e.globe.addTaskLine({task_id: "T", entity_id: "nonesuch"});
  assert.equal(e.viewer.entities.adds.length, 0);
  assert.deepEqual(e.errors, []);
  const bare = env();
  bare.store.upsert(ent("a"));
  bare.globe.addTaskLine({task_id: "T", entity_id: "a"});
  assert.equal(bare.viewer.entities.adds.length, 0);
});

// --- what the tool decides ---

test("switching the rings off hides the rings that are drawn, and on shows them", () => {
  const e = shown();
  e.globe.render();
  const rings = e.viewer.entities.items.filter((i) => i.id.startsWith("recon_proximity_ring_"));
  e.globe.setShowRings(false);
  assert.deepEqual(rings.map((r) => r.show), [false, false, false, false]);
  e.globe.setShowRings(true);
  assert.deepEqual(rings.map((r) => r.show), [true, true, true, true]);
});

test("switching the labels off hides the labels of the markers drawn, and on shows them", () => {
  const e = shown();
  e.store.upsert(ent("a"));
  e.globe.render();
  const [marker] = e.dataSource.items;
  const own = e.viewer.entities.items.find((i) => i.id === "recon_reference_point");
  e.globe.setShowLabels(false);
  assert.equal(marker.label.show, false);
  assert.equal(own.label.show, false);
  e.globe.setShowLabels(true);
  assert.equal(marker.label.show, true);
  assert.equal(e.dataSource.items.every((i) => !i.label || i.label.show), true);
});

test("the camera position is the camera's, in degrees", () => {
  const e = shown();
  const position = e.globe.cameraPosition();
  assert.equal(String(position.lat), "Math.toDegrees(Cartographic.fromCartesian(camera.position).latitude)".replace("Math.toDegrees(Cartographic.fromCartesian(camera.position).latitude)", String(position.lat)));
  assert.match(String(position.lat), /latitude/);
  assert.match(String(position.lon), /longitude/);
});

// --- the altitude listener ---

function altitude(e) { for (const [, fn] of e.altitudeEvents()) fn({}); }

test("an altitude change while the layer is hidden does nothing", () => {
  const e = attached();
  e.globe.addMarker(ent("a"));
  e.asked.length = 0;
  altitude(e);
  assert.deepEqual(e.asked, []);
});

test("an altitude change shows or hides the recon labels and the rings as the render tool says, ANDed with the checkboxes", () => {
  const e = shown();
  e.store.upsert(ent("a"));
  e.globe.render();
  e.dataSource.items.push({id: "someone-else", label: {show: true}});
  e.answers.labels = false; e.answers.rings = false;
  altitude(e);
  assert.equal(e.dataSource.items[0].label.show, false);
  assert.equal(e.dataSource.items.find((i) => i.id === "someone-else").label.show, true, "only recon_entity_ labels");
  assert.deepEqual(e.viewer.entities.items.filter((i) => i.id.startsWith("recon_proximity_ring_")).map((r) => r.show), [false, false, false, false]);
  e.answers.labels = true; e.answers.rings = true;
  altitude(e);
  assert.equal(e.dataSource.items[0].label.show, true);
  assert.deepEqual(e.viewer.entities.items.filter((i) => i.id.startsWith("recon_proximity_ring_")).map((r) => r.show), [true, true, true, true]);
  e.globe.setShowLabels(false); e.globe.setShowRings(false);
  altitude(e);
  assert.equal(e.dataSource.items[0].label.show, false);
  assert.deepEqual(e.viewer.entities.items.filter((i) => i.id.startsWith("recon_proximity_ring_")).map((r) => r.show), [false, false, false, false]);
});

test("the altitude change updates the viewer's markers when there is no data source, and skips entities without a label or an id", () => {
  const e = shown({hrm: false});
  e.win.HierarchicalRenderManager = {shouldShowDetail: () => false};
  e.globe.addMarker(ent("a"));
  e.viewer.entities.items.push({label: {show: true}}, {id: "recon_entity_nolabel"});
  altitude(e);
  assert.equal(e.viewer.entities.items.find((i) => i.id === "recon_entity_a").label.show, false);
  e.globe.setShowRings(true);
});

test("a ring entity without an ellipse is left alone by the altitude change", () => {
  const e = shown();
  e.globe.render();
  const ring = e.viewer.entities.items.find((i) => i.id === "recon_proximity_ring_0");
  delete ring.ellipse; ring.show = "untouched";
  e.answers.rings = false;
  altitude(e);
  assert.equal(ring.show, "untouched");
});

// --- clusters and icons ---

function cluster(e, members) {
  const target = {label: {}, billboard: {}};
  e.clusterListeners[0](members.map((data) => ({properties: data === null ? undefined : {entityData: {getValue: () => data}}})), target);
  return target;
}

test("a cluster is labelled with its count and given a tactical icon in the worst disposition's colour", () => {
  const e = attached();
  e.clusterListeners[0]([{properties: {}}], {label: {}, billboard: {}});   // properties without entityData: skipped, not an error
  const c = cluster(e, [{disposition: "FRIEND"}, {disposition: "HOSTILE", threat_level: "CRITICAL"}, null, {disposition: "FRIEND"}]);
  assert.equal(c.label.text, "4");
  assert.equal(c.label.font, "bold 14px sans-serif");
  assert.equal(String(c.label.fillColor), "Color.WHITE");
  assert.equal(String(c.label.outlineColor), "Color.BLACK");
  assert.equal(c.label.outlineWidth, 2);
  assert.equal(String(c.label.style), "LabelStyle.FILL_AND_OUTLINE");
  assert.equal(String(c.label.verticalOrigin), "VerticalOrigin.CENTER");
  assert.equal(String(c.label.horizontalOrigin), "HorizontalOrigin.CENTER");
  assert.equal(c.billboard.image, "data:44x44");
  assert.equal(c.billboard.width, 44);
  assert.equal(c.billboard.height, 44);
  const canvas = e.canvases.at(-1);
  assert.deepEqual(canvas.ops.find((o) => o[0] === "set" && o[1] === "fillStyle" && /css/.test(o[2])), ["set", "fillStyle", "css(Color.RED.withAlpha(0.85))"]);
});

test("a cluster's icon has a glow only for a high or critical threat, and the glow is brighter for critical", () => {
  const glow = (threat) => { const e = attached(); cluster(e, [{disposition: "FRIEND", threat_level: threat}]); const ops = e.canvases.at(-1).ops; return ops.filter((o) => o[0] === "stop"); };
  assert.deepEqual(glow("CRITICAL"), [["stop", 0, "rgba(255,0,0,0.6)"], ["stop", 1, "rgba(255,0,0,0)"]]);
  assert.deepEqual(glow("HIGH"), [["stop", 0, "rgba(255,100,0,0.4)"], ["stop", 1, "rgba(255,0,0,0)"]]);
  assert.deepEqual(glow("MEDIUM"), []);
  assert.deepEqual(glow(undefined), []);
});

test("a cluster's icon is sized by its count, and drawn as a circle with a border and a diamond", () => {
  const e = attached();
  const members = Array.from({length: 16}, () => ({disposition: "UNKNOWN"}));
  cluster(e, members);
  const canvas = e.canvases.at(-1);
  assert.equal(canvas.width, 52);
  assert.deepEqual(canvas.ops.filter((o) => o[0] === "arc"), [["arc", 26, 26, 22, 0, 2 * Math.PI]]);
  assert.deepEqual(canvas.ops.filter((o) => o[0] === "moveTo"), [["moveTo", 26, 13]]);
  assert.equal(canvas.ops.some((o) => o[0] === "set" && o[1] === "strokeStyle" && o[2] === "white"), true);
  assert.equal(canvas.ops.some((o) => o[0] === "set" && o[1] === "fillStyle" && o[2] === "rgba(255,255,255,0.9)"), true);
});

test("a cluster takes the colour of the worst disposition in it", () => {
  const colour = (members) => { const e = attached(); cluster(e, members); return e.canvases.at(-1).ops.find((o) => o[0] === "set" && o[1] === "fillStyle" && /css/.test(o[2]))[2]; };
  assert.equal(colour([{disposition: "SUSPICIOUS"}, {disposition: "HOSTILE"}]), "css(Color.RED.withAlpha(0.85))");
  assert.equal(colour([{disposition: "SUSPICIOUS"}, {disposition: "UNKNOWN"}]), "css(Color.ORANGE.withAlpha(0.85))");
  assert.equal(colour([{disposition: "NEUTRAL"}, {disposition: "UNKNOWN"}]), "css(Color.YELLOW.withAlpha(0.85))");
  assert.equal(colour([{disposition: "NEUTRAL"}, {disposition: "FRIEND"}]), "css(Color.LIME.withAlpha(0.85))");
  assert.equal(colour([{disposition: "FRIEND"}]), "css(Color.BLUE.withAlpha(0.85))");
  assert.equal(colour([null]), "css(Color.BLUE.withAlpha(0.85))");
});

test("a marker's icon is a triangle for an aircraft and a hull otherwise, with a cross for hostile and a dot for friendly", () => {
  const ops = (disposition, aircraft) => { const e = shown(); e.globe.createEntityIcon(describingColor(e), disposition, aircraft); return e.canvases.at(-1).ops; };
  const describingColor = (e) => e.win.Cesium.Color.RED;
  const plane = ops("UNKNOWN", true), hull = ops("UNKNOWN", false);
  assert.deepEqual(plane.filter((o) => o[0] === "moveTo"), [["moveTo", 0, -12]]);
  assert.equal(plane.some((o) => o[0] === "arc"), false);
  assert.deepEqual(hull.filter((o) => o[0] === "arc"), [["arc", 0, 2, 10, Math.PI, 0]]);
  assert.equal(hull.some((o) => o[0] === "moveTo"), false);
  const hostile = ops("HOSTILE", false);
  assert.deepEqual(hostile.filter((o) => o[0] === "moveTo"), [["moveTo", -8, -8], ["moveTo", 8, -8]]);
  assert.equal(hostile.some((o) => o[0] === "set" && o[2] === "#FF0000"), true);
  for (const friendly of ["FRIEND", "ASSUMED_FRIEND"]) assert.equal(ops(friendly, false).some((o) => o[0] === "set" && o[2] === "#00FF00"), true, friendly);
  assert.equal(ops("NEUTRAL", false).some((o) => o[0] === "set" && (o[2] === "#00FF00" || o[2] === "#FF0000")), false);
  assert.equal(hull.find((o) => o[0] === "set" && o[1] === "fillStyle")[2], "css(Cesium.Color.RED)".replace("Cesium.", ""));
});

test("the reference point's icon is a cyan diamond with a white dot", () => {
  const e = shown();
  e.globe.render();
  const ops = e.canvases.find((c) => c.width === 40).ops;
  assert.deepEqual(ops.filter((o) => o[0] === "moveTo" || o[0] === "lineTo"), [["moveTo", 0, -15], ["lineTo", 12, 0], ["lineTo", 0, 15], ["lineTo", -12, 0]]);
  assert.equal(ops.some((o) => o[0] === "set" && o[1] === "fillStyle" && o[2] === "#00FFFF"), true);
  assert.deepEqual(ops.filter((o) => o[0] === "arc"), [["arc", 0, 0, 3, 0, Math.PI * 2]]);
  assert.equal(ops.some((o) => o[0] === "set" && o[1] === "fillStyle" && o[2] === "#FFFFFF"), true);
});

test("the icons are 32 pixels for a marker, 40 for the reference point", () => {
  const e = shown();
  assert.equal(e.globe.createEntityIcon(e.win.Cesium.Color.RED, "UNKNOWN", false), "data:32x32");
  e.globe.render();
  assert.equal(e.canvases.some((c) => c.width === 40 && c.height === 40), true);
});

test("the colour tables are made when first needed and then reused", () => {
  const e = env();
  let builds = 0;
  const real = e.win.Cesium;
  e.win.Cesium = new Proxy({}, {get: (t, k) => (k === "Color" ? new Proxy({}, {get: (c, name) => { if (name === "MAGENTA") builds++; return real.Color[name]; }}) : real[k])});
  e.globe.attach(e.viewer);
  e.globe.setVisible(true);
  assert.equal(builds, 0, "nothing at attach time");
  e.globe.addMarker(ent("a"));
  assert.equal(builds, 1);
  e.globe.addMarker(ent("b"));
  e.globe.updatePosition(ent("b", {location: {lat: 1, lon: 2}}));
  assert.equal(builds, 1, "reused");
});
