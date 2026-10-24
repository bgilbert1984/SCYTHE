import test from "node:test";
import assert from "node:assert/strict";
import {createWorkspace} from "./lifecycle.js";
import {createPoiPlugin, POI_ID, pinColorName, parseMetadata, describePoi, entitySpec} from "./poiPlugin.js";

const quiet = {warn() {}, debug() {}, log() {}, error() {}};
const settle = () => new Promise((resolve) => setImmediate(resolve));
const gate = () => { let open; const promise = new Promise((resolve) => { open = resolve; }); return {promise, open}; };

const P1 = {id: 1, name: "Tower", latitude: 36, longitude: -86, altitude: 5, category: "military", source_file: "a.kmz", metadata: '{"time_begin":"t0","view_range":12000}'};
const P2 = {id: 2, name: "Depot", latitude: 35, longitude: -85, category: "imported"};

function fakeGlobe() {
  const globe = {
    viewer: null, attached: 0, detached: 0, entities: [], removedAll: 0, removed: [], shown: [], entityShown: [], flights: [], pointFlights: [],
    attach(viewer) { globe.viewer = viewer; globe.attached++; },
    detach() { globe.detached++; globe.viewer = null; },
    available: () => Boolean(globe.viewer),
    add(spec) { const handle = {spec}; globe.entities.push(handle); return handle; },
    removeAll() { globe.removedAll++; globe.entities = []; },
    remove(handle) { globe.removed.push(handle); globe.entities = globe.entities.filter((e) => e !== handle); },
    setShown(v) { globe.shown.push(v); },
    setEntityShown(handle, v) { handle.shown = v; globe.entityShown.push([handle.spec.id, v]); },
    flyTo(handle) { globe.flights.push(handle.spec.id); },
    flyToPoints(points) { globe.pointFlights.push(points); },
  };
  return globe;
}

/** `replies` maps "METHOD url" (GET by default) to a function returning the JSON body, or a Promise of it. */
function setup(replies = {}) {
  const globe = fakeGlobe();
  const calls = [], messages = [];
  const workspace = createWorkspace({log: quiet});
  workspace.provide("httpFetch", async (url, init = {}) => {
    const key = `${init.method || "GET"} ${url}`;
    calls.push({key, init});
    const reply = replies[key];
    if (!reply) throw new Error(`no reply for ${key}`);
    const body = await reply(init);
    return {json: async () => { if (body instanceof Error) throw body; return body; }};
  });
  workspace.provide("poiConsole", {message: (text, kind) => messages.push([text, kind])});
  workspace.provide("poiGlobe", globe);
  const poi = createPoiPlugin({log: quiet});
  workspace.register(poi.tool);
  const loaded = (pois = [P1, P2], categories = {military: 1, imported: 1}) => ({"GET /api/poi/visualization": () => ({status: "ok", pois, categories})});
  return {workspace, globe, calls, messages, controls: poi.controls, loaded};
}

async function started(extra = {}) {
  const s = setup({...setup().loaded(), ...extra});
  await s.workspace.activate(POI_ID);
  s.controls.initialize({id: "viewer"});
  await settle();
  return s;
}

// --- pure pieces ---

test("pinColorName: the named categories, yellow for any other", () => {
  assert.deepEqual(["intelligence", "military", "infrastructure", "commercial", "government", "general"].map(pinColorName),
    ["RED", "MAROON", "ORANGE", "ROYALBLUE", "PURPLE", "YELLOW"]);
  assert.deepEqual(["residential", "transportation", "imported", "manual", "x", undefined].map(pinColorName), Array(6).fill("YELLOW"));
});

test("parseMetadata: a JSON string is parsed, one that is not JSON is empty, anything else passes through", () => {
  assert.deepEqual(parseMetadata('{"a":1}'), {a: 1});
  assert.deepEqual(parseMetadata("not json"), {});
  const o = {b: 2};
  assert.equal(parseMetadata(o), o);
  assert.equal(parseMetadata(undefined), undefined);
  assert.equal(parseMetadata(null), null);
});

test("describePoi: name, description, category, coordinates, source and the metadata that is there", () => {
  const html = describePoi({...P1, description: "A tower"}, {time_begin: "t0", time_end: "t1", view_range: 12000});
  assert.match(html, /<h3[^>]*>Tower<\/h3>/);
  assert.match(html, /<p[^>]*>A tower<\/p>/);
  assert.match(html, /Category:<\/td><td[^>]*>military</);
  assert.match(html, /Coordinates:<\/td><td[^>]*>36\.000000, -86\.000000</);
  assert.match(html, /Source:<\/td><td[^>]*>a\.kmz</);
  assert.match(html, /Time Begin:<\/td><td[^>]*>t0</);
  assert.match(html, /Time End:<\/td><td[^>]*>t1</);
  assert.match(html, /View Range:<\/td><td[^>]*>12\.0 km</);
  const bare = describePoi(P2, undefined);
  assert.doesNotMatch(bare, /<p |Source:|Time Begin|Time End|View Range/);
  assert.doesNotMatch(describePoi(P2, {}), /Time Begin|Time End|View Range/);
});

test("describePoi escapes what the POI carries: name, description, category, source file and times", () => {
  const evil = "<img src=x onerror=alert(1)>";
  const html = describePoi({id: 1, name: evil, description: evil, category: evil, source_file: evil, latitude: 1, longitude: 2}, {time_begin: evil, time_end: evil});
  assert.doesNotMatch(html, /<img/);
  assert.equal(html.split("&lt;img src=x onerror=alert(1)&gt;").length - 1, 6);
});

test("PINNED: a latitude that is not a number makes the description throw, as it did", () => {
  assert.throws(() => describePoi({...P2, latitude: "36"}, {}), TypeError);
});

test("entitySpec: id, position (altitude 0 by default), pin colour, label only when labels are on, properties", () => {
  const spec = entitySpec(P1, "military", {time_begin: "t0"}, true);
  assert.equal(spec.id, "poi_1");
  assert.deepEqual([spec.name, spec.lon, spec.lat, spec.alt, spec.pinColor, spec.label], ["Tower", -86, 36, 5, "MAROON", "Tower"]);
  assert.deepEqual(spec.properties, {poiId: 1, category: "military", sourceFile: "a.kmz", metadata: {time_begin: "t0"}});
  const plain = entitySpec(P2, "imported", {}, false);
  assert.equal(plain.alt, 0);
  assert.equal(plain.label, null);
  assert.equal(plain.pinColor, "YELLOW");
});

// --- the tool ---

test("registered but inactive, the controls are empty and do nothing", async () => {
  const {controls, globe, calls} = setup();
  assert.deepEqual([controls.pois, controls.categories, controls.categoryVisibility, controls.visible], [[], {}, {}, true]);
  assert.equal(await controls.addPOI("x", 1, 2), null);
  assert.equal(await controls.deletePOI(1), false);
  assert.ok(controls.loadPOIs() instanceof Promise);
  await controls.loadPOIs();
  controls.setVisible(false);
  controls.setCategoryVisible("a", false);
  controls.toggleLabels(false);
  controls.flyToPOI(1);
  controls.flyToAllPOIs();
  assert.deepEqual(calls, []);
  assert.deepEqual([globe.attached, globe.entities.length, globe.shown], [0, 0, []]);
});

test("initialize attaches the viewer and loads: a pin per POI, with messages", async () => {
  const {controls, globe, calls, messages} = await started();
  assert.deepEqual(globe.viewer, {id: "viewer"});
  assert.deepEqual(calls.map((c) => c.key), ["GET /api/poi/visualization"]);
  assert.deepEqual(globe.entities.map((e) => e.spec.id), ["poi_1", "poi_2"]);
  assert.equal(controls.pois.length, 2);
  assert.deepEqual(controls.categories, {military: 1, imported: 1});
  assert.deepEqual(controls.categoryVisibility, {military: true, imported: true});
  assert.deepEqual(messages, [["Loading Points of Interest...", "info"], ["Loaded 2 POIs in 2 categories", "response"]]);
});

test("a POI with no category is a general one, and a POI at latitude or longitude 0 gets no pin (PINNED)", async () => {
  const none = {id: 3, name: "N", latitude: 1, longitude: 2};
  const {globe} = await started({"GET /api/poi/visualization": () => ({status: "ok", pois: [none, {...none, id: 4, latitude: 0}, {...none, id: 5, longitude: 0}], categories: {}})});
  assert.deepEqual(globe.entities.map((e) => e.spec.id), ["poi_3"]);
  assert.equal(globe.entities[0].spec.properties.category, "general");
  assert.equal(globe.entities[0].spec.pinColor, "YELLOW");
});

test("metadata arrives as a JSON string and is handed on parsed", async () => {
  const {globe} = await started();
  assert.deepEqual(globe.entities[0].spec.properties.metadata, {time_begin: "t0", view_range: 12000});
  assert.match(globe.entities[0].spec.description, /View Range/);
});

test("a server that says it is not ok: a message and nothing drawn", async () => {
  const failing = setup({"GET /api/poi/visualization": () => ({status: "error", message: "no poi"})});
  await failing.workspace.activate(POI_ID);
  failing.controls.initialize({});
  await settle();
  assert.deepEqual(failing.messages, [["Loading Points of Interest...", "info"], ["POI system not available", "alert"]]);
  assert.deepEqual([failing.globe.entities.length, failing.controls.pois], [0, []]);
});

test("a reply that is not JSON, or a network error, is reported as an error message", async () => {
  const bad = setup({"GET /api/poi/visualization": () => new Error("Unexpected token <")});
  await bad.workspace.activate(POI_ID);
  bad.controls.initialize({});
  await settle();
  assert.deepEqual(bad.messages.at(-1), ["Error loading POIs: Unexpected token <", "alert"]);
  const down = setup();   // no reply for the GET: the fetch itself throws
  await down.workspace.activate(POI_ID);
  down.controls.initialize({});
  await settle();
  assert.match(down.messages.at(-1)[0], /^Error loading POIs: no reply/);
});

test("PINNED: loading stops at the first POI whose description cannot be built, and reports an error", async () => {
  const {globe, messages, controls} = await started({"GET /api/poi/visualization": () => ({status: "ok", pois: [P2, {...P2, id: 9, latitude: "35"}, {...P2, id: 10}], categories: {}})});
  assert.deepEqual(globe.entities.map((e) => e.spec.id), ["poi_2"]);
  assert.match(messages.at(-1)[0], /^Error loading POIs: /);
  assert.equal(controls.pois.length, 3);   // the records were stored before the entities were made
});

test("categories start visible, and a reload sets every category the server names visible again", async () => {
  const s = await started();
  s.controls.setCategoryVisible("military", false);
  assert.equal(s.controls.categoryVisibility.military, false);
  await s.controls.loadPOIs();
  assert.equal(s.controls.categoryVisibility.military, true);
});

test("setVisible shows or hides the layer; setCategoryVisible hides the pins of one category (and only them) with the layer's state", async () => {
  const s = await started();
  s.controls.setVisible(false);
  assert.equal(s.controls.visible, false);
  assert.deepEqual(s.globe.shown, [false]);
  s.controls.setCategoryVisible("military", true);
  assert.deepEqual(s.globe.entityShown, [["poi_1", false]]);   // shown, but the layer is hidden
  s.controls.setVisible(true);
  s.globe.entityShown.length = 0;
  s.controls.setCategoryVisible("imported", false);
  assert.deepEqual(s.globe.entityShown, [["poi_2", false]]);
  s.controls.setCategoryVisible("nothing", false);   // no POI in it: nothing to do, but it is remembered
  assert.equal(s.controls.categoryVisibility.nothing, false);
  assert.equal(s.globe.entityShown.length, 1);
});

test("toggleLabels recreates the entities with or without a label", async () => {
  const s = await started();
  assert.deepEqual(s.globe.entities.map((e) => e.spec.label), ["Tower", "Depot"]);
  s.controls.toggleLabels(false);
  assert.deepEqual(s.globe.entities.map((e) => e.spec.label), [null, null]);
  assert.equal(s.globe.removedAll, 2);   // the load, then this
  s.controls.toggleLabels(true);
  assert.deepEqual(s.globe.entities.map((e) => e.spec.label), ["Tower", "Depot"]);
});

test("PINNED: showing or hiding the labels brings back the pins of a hidden category, though its checkbox state stays", async () => {
  const s = await started();
  s.controls.setCategoryVisible("military", false);
  assert.equal(s.globe.entities[0].shown, false);
  s.controls.toggleLabels(false);
  assert.equal(s.controls.categoryVisibility.military, false);
  assert.equal(s.globe.entities[0].spec.id, "poi_1");
  assert.equal(s.globe.entities[0].shown, undefined);   // a new entity, shown
});

test("flyToPOI flies to the pin and says so; an unknown id does nothing", async () => {
  const s = await started();
  s.messages.length = 0;
  s.controls.flyToPOI(2);
  assert.deepEqual(s.globe.flights, ["poi_2"]);
  assert.deepEqual(s.messages, [["Flying to POI: Depot", "info"]]);
  s.controls.flyToPOI(99);
  s.controls.flyToPOI("2");   // the page's inline handler passes the id it printed
  assert.deepEqual(s.globe.flights, ["poi_2", "poi_2"]);
});

test("flyToAllPOIs flies to the bounding points of every POI, altitude 0 by default; nothing to fly to does nothing", async () => {
  const s = await started();
  s.messages.length = 0;
  s.controls.flyToAllPOIs();
  assert.deepEqual(s.globe.pointFlights, [[{lon: -86, lat: 36, alt: 5}, {lon: -85, lat: 35, alt: 0}]]);
  assert.deepEqual(s.messages, [["Flying to view all 2 POIs", "info"]]);
  const empty = await started({"GET /api/poi/visualization": () => ({status: "ok", pois: [], categories: {}})});
  empty.controls.flyToAllPOIs();
  assert.deepEqual(empty.globe.pointFlights, []);
});

test("PINNED: flyToAllPOIs includes a POI that has no pin (latitude 0)", async () => {
  const s = await started({"GET /api/poi/visualization": () => ({status: "ok", pois: [{...P2, latitude: 0}], categories: {}})});
  s.controls.flyToAllPOIs();
  assert.equal(s.globe.pointFlights[0].length, 1);
});

test("addPOI posts the fields (category manual and no description by default), reloads, and returns the new id", async () => {
  const s = await started({"POST /api/poi/add": () => ({status: "ok", poi_id: 7})});
  s.messages.length = 0;
  const id = await s.controls.addPOI("Site", 36.5, -86.5);
  assert.equal(id, 7);
  const post = s.calls.find((c) => c.key === "POST /api/poi/add");
  assert.deepEqual(post.init.headers, {"Content-Type": "application/json"});
  assert.deepEqual(JSON.parse(post.init.body), {name: "Site", latitude: 36.5, longitude: -86.5, category: "manual", description: ""});
  assert.equal(s.calls.filter((c) => c.key === "GET /api/poi/visualization").length, 2);   // loaded again
  assert.deepEqual(s.messages.map((m) => m[0]), ['POI "Site" added successfully', "Loading Points of Interest...", "Loaded 2 POIs in 2 categories"]);
  await s.controls.addPOI("Site2", 1, 2, "government", "d");
  assert.deepEqual(JSON.parse(s.calls.filter((c) => c.key === "POST /api/poi/add").at(-1).init.body), {name: "Site2", latitude: 1, longitude: 2, category: "government", description: "d"});
});

test("addPOI that the server refuses, or that fails: null and a message, no reload", async () => {
  const refused = await started({"POST /api/poi/add": () => ({status: "error", message: "bad"})});
  refused.messages.length = 0;
  assert.equal(await refused.controls.addPOI("x", 1, 2), null);
  assert.deepEqual(refused.messages, [["Failed to add POI: bad", "alert"]]);
  assert.equal(refused.calls.filter((c) => c.key === "GET /api/poi/visualization").length, 1);
  const broken = await started({"POST /api/poi/add": () => new Error("boom")});
  broken.messages.length = 0;
  assert.equal(await broken.controls.addPOI("x", 1, 2), null);
  assert.deepEqual(broken.messages, [["Error adding POI: boom", "alert"]]);
});

test("deletePOI removes the pin and the record, not the counts, and says so", async () => {
  const s = await started({"DELETE /api/poi/delete/1": () => ({status: "ok"})});
  s.messages.length = 0;
  assert.equal(await s.controls.deletePOI(1), true);
  assert.deepEqual(s.controls.pois.map((p) => p.id), [2]);
  assert.deepEqual(s.globe.entities.map((e) => e.spec.id), ["poi_2"]);
  assert.deepEqual(s.controls.categories, {military: 1, imported: 1});   // PINNED: not reloaded
  assert.deepEqual(s.messages, [["POI deleted successfully", "response"]]);
  s.controls.flyToPOI(1);
  assert.deepEqual(s.globe.flights, []);
});

test("deletePOI of a POI that has a record but no pin still removes the record", async () => {
  const s = await started({"GET /api/poi/visualization": () => ({status: "ok", pois: [{...P2, latitude: 0}], categories: {}}), "DELETE /api/poi/delete/2": () => ({status: "ok"})});
  assert.equal(await s.controls.deletePOI(2), true);
  assert.deepEqual(s.controls.pois, []);
  assert.deepEqual(s.globe.removed, []);
});

test("deletePOI that the server refuses says so; one that fails on the network logs only (PINNED)", async () => {
  const refused = await started({"DELETE /api/poi/delete/1": () => ({status: "error", message: "locked"})});
  refused.messages.length = 0;
  assert.equal(await refused.controls.deletePOI(1), false);
  assert.deepEqual(refused.messages, [["Failed to delete POI: locked", "alert"]]);
  assert.equal(refused.controls.pois.length, 2);
  const broken = await started({"DELETE /api/poi/delete/1": () => new Error("boom")});
  broken.messages.length = 0;
  assert.equal(await broken.controls.deletePOI(1), false);
  assert.deepEqual(broken.messages, []);
});

test("a viewer handed over before the tool is active is used when it activates, once", async () => {
  const s = setup(setup().loaded());
  s.controls.initialize({id: "early"});
  assert.equal(s.globe.attached, 0);
  await s.workspace.activate(POI_ID);
  await settle();
  assert.deepEqual(s.globe.viewer, {id: "early"});
  assert.equal(s.globe.entities.length, 2);
  await s.workspace.deactivate(POI_ID);
  await s.workspace.activate(POI_ID);
  await settle();
  assert.equal(s.globe.attached, 1);
});

test("deactivating takes the layer off the viewer and empties the controls", async () => {
  const s = await started();
  await s.workspace.deactivate(POI_ID);
  assert.equal(s.globe.detached, 1);
  assert.deepEqual([s.controls.pois, s.controls.categories, s.controls.visible], [[], {}, true]);
  assert.equal(await s.controls.deletePOI(1), false);
});

test("an answer from the server that arrives after deactivation changes nothing", async () => {
  const held = gate();
  const s = setup({"GET /api/poi/visualization": () => held.promise});
  await s.workspace.activate(POI_ID);
  s.controls.initialize({});
  await settle();
  await s.workspace.deactivate(POI_ID);
  const before = [...s.messages];
  held.open({status: "ok", pois: [P1], categories: {military: 1}});
  await settle();
  assert.deepEqual(s.globe.entities, []);
  assert.deepEqual(s.messages, before);
});

test("a late add, delete or failure after deactivation changes nothing", async () => {
  const addGate = gate(), delGate = gate();
  const s = await started({"POST /api/poi/add": () => addGate.promise, "DELETE /api/poi/delete/1": () => delGate.promise});
  const adding = s.controls.addPOI("x", 1, 2);
  const deleting = s.controls.deletePOI(1);
  await settle();
  await s.workspace.deactivate(POI_ID);
  s.messages.length = 0;
  addGate.open({status: "ok", poi_id: 5});
  delGate.open({status: "ok"});
  assert.equal(await adding, null);
  assert.equal(await deleting, false);
  assert.deepEqual(s.messages, []);
  assert.equal(s.calls.filter((c) => c.key === "GET /api/poi/visualization").length, 1);   // no reload
});

test("activating again after deactivating starts from nothing", async () => {
  const s = await started();
  await s.workspace.deactivate(POI_ID);
  await s.workspace.activate(POI_ID);
  assert.deepEqual(s.controls.pois, []);
  s.controls.initialize({id: "second"});
  await settle();
  assert.equal(s.controls.pois.length, 2);
});

test("a server reply with no pois or categories is an empty set", async () => {
  const s = await started({"GET /api/poi/visualization": () => ({status: "ok"})});
  assert.deepEqual([s.controls.pois, s.controls.categories, s.globe.entities], [[], {}, []]);
});

test("a reload without the POIs the server no longer has forgets their pins: flying to one does nothing", async () => {
  let pois = [P1, P2];
  const s = await started({"GET /api/poi/visualization": () => ({status: "ok", pois, categories: {}})});
  pois = [P2];
  await s.controls.loadPOIs();
  s.controls.flyToPOI(1);
  assert.deepEqual(s.globe.flights, []);
  s.controls.flyToPOI(2);
  assert.deepEqual(s.globe.flights, ["poi_2"]);
});

test("a load before the viewer is handed over keeps the POIs but draws nothing, and flying to them does nothing", async () => {
  const s = setup(setup().loaded());
  await s.workspace.activate(POI_ID);
  await s.controls.loadPOIs();
  assert.equal(s.controls.pois.length, 2);
  assert.deepEqual(s.globe.entities, []);
  s.controls.flyToAllPOIs();
  assert.deepEqual(s.globe.pointFlights, []);
});

test("hiding a category that has a POI with no pin (latitude 0) skips it", async () => {
  const s = await started({"GET /api/poi/visualization": () => ({status: "ok", pois: [{...P2, latitude: 0}, P1], categories: {}})});
  s.controls.setCategoryVisible("imported", false);
  assert.deepEqual(s.globe.entityShown, []);
});

test("a failure that arrives after deactivation is not reported: load, add and delete", async () => {
  const loadGate = gate(), addGate = gate(), delGate = gate();
  const s = setup({"GET /api/poi/visualization": () => loadGate.promise, "POST /api/poi/add": () => addGate.promise, "DELETE /api/poi/delete/1": () => delGate.promise});
  await s.workspace.activate(POI_ID);
  s.controls.initialize({});
  const adding = s.controls.addPOI("x", 1, 2);
  const deleting = s.controls.deletePOI(1);
  await settle();
  await s.workspace.deactivate(POI_ID);
  s.messages.length = 0;
  loadGate.open(new Error("late"));
  addGate.open(new Error("late"));
  delGate.open(new Error("late"));
  assert.equal(await adding, null);
  assert.equal(await deleting, false);
  await settle();
  assert.deepEqual(s.messages, []);
});
