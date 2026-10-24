import test from "node:test";
import assert from "node:assert/strict";
import {createWorkspace} from "./lifecycle.js";
import {
  createSensorPlugin, SENSORS_ID, NO_SENSORS, UNASSIGNED_HEADER, normalizeSensor, positionOf, entityPositionOf, groupSensors, moveGroup,
  renderSensorItem, renderGroupHeader, renderSensorList, markerOf, assignmentLinks, flightTarget,
} from "./sensorPlugin.js";

const quiet = {warn() {}, debug() {}, log() {}, error() {}};
const settle = () => new Promise((resolve) => setImmediate(resolve));
const gate = () => { let open; const promise = new Promise((resolve) => { open = resolve; }); return {promise, open}; };
const wire = (eventType, entityId, entityType, entityData) => ({event_type: eventType, entity_id: entityId, entity_type: entityType, entity_data: entityData, sequence_id: 1});

const restSensor = (id, extra = {}) => ({sensor_id: id, label: `Sensor ${id}`, type: "SDR", status: "ACTIVE", position: [36.1, -86.7, 10], tx: {bands_mhz: [[915, 915]], max_eirp_dbm: 20}, ...extra});

// --- pure pieces ---

test("normalizeSensor: label, position from an array or a location, and tx/rx configs from the radio blocks", () => {
  const s = normalizeSensor({sensor_id: "a", name: "Named", position: [1, 2, 3], tx: {bands_mhz: [[433, 434]], max_eirp_dbm: 7}, rx: {bands_mhz: [[100, 200]], sensitivity_dbm: -90, sample_rate_hz: 2400000}});
  assert.equal(s.label, "Named");
  assert.deepEqual(s.position, {lat: 1, lon: 2, alt_m: 3});
  assert.deepEqual(s.tx_config, {frequency_mhz: 433, power_dbm: 7});
  assert.deepEqual(s.rx_config, {frequency_mhz: 100, sensitivity_dbm: -90, sample_rate_hz: 2400000});
  assert.equal(normalizeSensor({sensor_id: "b"}).label, "b");
  assert.equal(normalizeSensor({sensor_id: "b", label: "L", name: "N"}).label, "L");
  assert.deepEqual(normalizeSensor({position: [1, 2]}).position, {lat: 1, lon: 2, alt_m: 0});
  assert.deepEqual(normalizeSensor({location: {lat: 5, lng: 6, alt: 7}}).position, {lat: 5, lon: 6, alt_m: 7});
  assert.deepEqual(normalizeSensor({location: {lat: 5, lon: 6, alt_m: 8, alt: 7}}).position, {lat: 5, lon: 6, alt_m: 8});
  assert.deepEqual(normalizeSensor({position: {lat: 1, lon: 2}, location: {lat: 9, lon: 9}}).position, {lat: 1, lon: 2});   // an object position wins
  assert.deepEqual(normalizeSensor({}).tx_config, {frequency_mhz: null, power_dbm: null});
  const given = {frequency_mhz: 1};
  assert.equal(normalizeSensor({tx_config: given, tx: {bands_mhz: [[2, 2]]}}).tx_config, given);
  assert.deepEqual(normalizeSensor(undefined).rx_config, {frequency_mhz: null, sensitivity_dbm: null, sample_rate_hz: null});
});

test("positionOf: lat/lon or latitude/longitude, a latitude of 0 counts, anything unusable is null", () => {
  assert.deepEqual(positionOf({position: {lat: "1.5", lon: "2.5"}}), {lat: 1.5, lon: 2.5});
  assert.deepEqual(positionOf({position: {latitude: 3, longitude: 4}}), {lat: 3, lon: 4});
  assert.deepEqual(positionOf({position: {lat: 0, lon: 0}}), {lat: 0, lon: 0});
  assert.equal(positionOf({position: {lat: "x", lon: 1}}), null);
  assert.equal(positionOf({position: {lat: 1}}), null);
  assert.equal(positionOf({}), null);
  assert.equal(positionOf(undefined), null);
});

test("PINNED: entityPositionOf uses ||, so a latitude or longitude of exactly 0 reads as missing", () => {
  assert.deepEqual(entityPositionOf({location: {lat: 5, lon: 6}}), {lat: 5, lon: 6});
  assert.deepEqual(entityPositionOf({location: {latitude: 7, longitude: 8}}), {lat: 7, lon: 8});
  assert.equal(entityPositionOf({location: {lat: 0, lon: 6}}), null);
  assert.equal(entityPositionOf({location: {lat: 5, lon: 0}}), null);
  assert.equal(entityPositionOf({}), null);
  assert.equal(entityPositionOf(null), null);
});

test("groupSensors: grouped by assigned entity (the last assignment wins), the rest unassigned, new groups appended to the order", () => {
  const sensors = {a: {sensor_id: "a"}, b: {sensor_id: "b"}, c: {sensor_id: "c"}};
  const r = groupSensors(sensors, {E1: ["a"], E2: ["a", "b"]}, ["E9"]);
  assert.deepEqual(Object.keys(r.groups), ["E2"]);
  assert.deepEqual(r.groups.E2.map((s) => s.sensor_id), ["a", "b"]);
  assert.deepEqual(r.unassigned.map((s) => s.sensor_id), ["c"]);
  assert.deepEqual(r.order, ["E9", "E2"]);
  assert.deepEqual(r.shown, ["E2"]);
  assert.deepEqual(groupSensors(sensors, {}, []).shown, []);
  assert.deepEqual(groupSensors(sensors, {E1: ["a"]}, ["E1", "E5"]).order, ["E1", "E5"]);   // an existing group is not added twice
});

test("moveGroup swaps with the neighbour in the full order, stays within bounds, ignores unknown ids", () => {
  assert.deepEqual(moveGroup(["a", "b", "c"], "b", "up"), ["b", "a", "c"]);
  assert.deepEqual(moveGroup(["a", "b", "c"], "b", "down"), ["a", "c", "b"]);
  assert.deepEqual(moveGroup(["a", "b", "c"], "a", "up"), ["a", "b", "c"]);
  assert.deepEqual(moveGroup(["a", "b", "c"], "c", "down"), ["a", "b", "c"]);
  assert.deepEqual(moveGroup(["a", "b"], "z", "up"), ["a", "b"]);
  assert.deepEqual(moveGroup(["a", "b"], "z", "down"), ["a", "b"]);
  assert.deepEqual(moveGroup(["a", "b"], "a", "sideways"), ["a", "b"]);
  const order = ["a", "b"];
  moveGroup(order, "b", "up");
  assert.deepEqual(order, ["a", "b"]);   // not mutated
});

test("renderSensorItem: label, status colour, tx/rx rows only when configured, dashes for missing values, buttons carry the id", () => {
  const html = renderSensorItem(normalizeSensor(restSensor("s1", {rx: {bands_mhz: [[100, 100]]}})));
  assert.match(html, /data-sensor-id="s1"/);
  assert.match(html, /list-item-title">Sensor s1</);
  assert.match(html, /background-color: #55ff55[^>]*>ACTIVE</);
  assert.match(html, /Tx Freq\/Pwr:[\s\S]*915 MHz \/ 20 dBm/);
  assert.match(html, /Rx Freq\/BW:[\s\S]*100 MHz \/ —/);
  assert.match(html, /Gain:[\s\S]*<span>—<\/span>/);
  assert.equal((html.match(/data-sensor-id="s1"/g) || []).length, 4);   // the item and its three buttons
  const bare = renderSensorItem({sensor_id: "x"});
  assert.doesNotMatch(bare, /Tx Freq|Rx Freq/);
  assert.match(bare, /list-item-title">x</);
  assert.match(bare, /UNKNOWN/);
  assert.match(bare, /Type:<\/span>\s*<span>SDR</);
});

test("renderSensorItem: OFFLINE is grey, anything else blue", () => {
  assert.match(renderSensorItem({sensor_id: "x", status: "OFFLINE"}), /background-color: #888/);
  assert.match(renderSensorItem({sensor_id: "x", status: "IDLE"}), /background-color: #4a9eff/);
});

test("renderSensorItem escapes every field that came from a sensor", () => {
  const evil = `<img src=x onerror=alert(1)>`;
  const html = renderSensorItem({sensor_id: `a"${evil}`, label: evil, type: evil, status: evil, tx_config: {frequency_mhz: evil, power_dbm: evil}, rx_config: {frequency_mhz: evil, bandwidth_mhz: evil, gain_db: evil}});
  assert.doesNotMatch(html, /<img/);
  assert.doesNotMatch(html, /data-sensor-id="a"</);
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
});

test("renderGroupHeader: the first group cannot move up and the last cannot move down; name and id are escaped", () => {
  const first = renderGroupHeader("Name", "E1", 0, 3);
  assert.match(first, /data-action="up" data-group="E1" disabled/);
  assert.match(first, /data-action="down" data-group="E1"  style/);
  const last = renderGroupHeader("Name", "E1", 2, 3);
  assert.match(last, /data-action="down" data-group="E1" disabled/);
  const only = renderGroupHeader("Name", "E1", 0, 1);
  assert.equal((only.match(/disabled/g) || []).length, 2);
  const html = renderGroupHeader("<b>n</b>", `E"1`, 1, 3);
  assert.doesNotMatch(html, /<b>/);
  assert.match(html, /data-group="E&quot;1"/);
});

test("renderSensorList: groups with the entity's name, then unassigned under a header only when there are groups", () => {
  const sensors = {a: {sensor_id: "a", label: "A"}, b: {sensor_id: "b", label: "B"}};
  const grouped = renderSensorList(groupSensors(sensors, {E1: ["a"]}, []), {E1: {name: "Falcon"}});
  assert.match(grouped, /TASK FORCE:<\/span>Falcon \(E1\)/);
  assert.ok(grouped.indexOf("list-item-title\">A<") < grouped.indexOf("Unassigned Sensors"));
  assert.ok(grouped.indexOf("Unassigned Sensors") < grouped.indexOf("list-item-title\">B<"));
  const flat = renderSensorList(groupSensors(sensors, {}, []), {});
  assert.doesNotMatch(flat, /Unassigned Sensors/);
  assert.equal((flat.match(/signal-card/g) || []).length, 2);
  assert.match(renderSensorList(groupSensors(sensors, {E2: ["a"]}, []), {}), /TASK FORCE:<\/span>E2</);   // an unknown entity shows its id
  assert.match(UNASSIGNED_HEADER, /Unassigned Sensors/);
});

test("PINNED: a known entity without a name is headed 'undefined (id)'", () => {
  const html = renderSensorList(groupSensors({a: {sensor_id: "a"}}, {E1: ["a"]}, []), {E1: {}});
  assert.match(html, /undefined \(E1\)/);
});

test("markerOf: null without a position; the description balloon is escaped", () => {
  assert.equal(markerOf({sensor_id: "x"}), null);
  const m = markerOf({sensor_id: "s1", label: "<i>L</i>", type: "RADAR", position: {lat: 1, lon: 2}, tx_config: {frequency_mhz: 915}});
  assert.deepEqual([m.id, m.label, m.type, m.lat, m.lon], ["s1", "<i>L</i>", "RADAR", 1, 2]);   // the label is data for Cesium, not HTML
  assert.doesNotMatch(m.description, /<i>/);
  assert.match(m.description, /&lt;i&gt;L&lt;\/i&gt;/);
  assert.match(m.description, /Tx: 915 MHz<br>Rx: — MHz/);
  const evil = markerOf({sensor_id: "s", position: {lat: 1, lon: 2}, type: "<u>t</u>", tx_config: {frequency_mhz: "<s>1</s>"}, rx_config: {frequency_mhz: "<em>2</em>"}});
  assert.doesNotMatch(evil.description, /<u>|<s>|<em>/);
  assert.match(evil.description, /Type: &lt;u&gt;t&lt;\/u&gt;/);
  assert.match(m.description, /Type: RADAR/);
  assert.equal(markerOf({sensor_id: "s2", position: {lat: 1, lon: 2}}).label, "s2");
  assert.match(markerOf({sensor_id: "s2", position: {lat: 1, lon: 2}}).description, /Type: SDR/);
});

test("assignmentLinks: only assignments whose entity and sensor both have a position", () => {
  const sensors = {a: {position: {lat: 1, lon: 2}}, b: {}, c: {position: {lat: 3, lon: 4}}};
  const recon = {E1: {location: {lat: 10, lon: 20}}, E2: {}, E3: {location: {lat: 30, lon: 40}}};
  const links = assignmentLinks({E1: ["a", "b", "missing"], E2: ["a"], E3: [], E4: ["a"], E5: undefined, E6: null}, sensors, recon);
  assert.deepEqual(links, [{sensorId: "a", entityId: "E1", sensor: {lat: 1, lon: 2}, entity: {lat: 10, lon: 20}}]);
  assert.deepEqual(assignmentLinks({E3: undefined, E1: ["a"]}, sensors, {E3: {location: {lat: 1, lon: 1}}, E1: recon.E1}).map((l) => l.sensorId), ["a"]);   // no list for a located entity
  assert.deepEqual(assignmentLinks({E3: ["c"]}, sensors, recon), [{sensorId: "c", entityId: "E3", sensor: {lat: 3, lon: 4}, entity: {lat: 30, lon: 40}}]);
});

test("flightTarget: the assigned entity first (2 km), else the sensor (10 km), else nowhere", () => {
  const sensor = {label: "Roof", position: {lat: 1, lon: 2}};
  const recon = {E1: {name: "Falcon", location: {lat: 10, lon: 20}}};
  const toEntity = flightTarget("s1", sensor, {E1: ["s1"]}, recon);
  assert.deepEqual(toEntity.position, {lat: 10, lon: 20, altitude: 2000});
  assert.equal(toEntity.label, "Entity Falcon");
  assert.deepEqual(toEntity.messages, ["Tracking assigned entity: Entity Falcon"]);
  const toSensor = flightTarget("s1", sensor, {}, recon);
  assert.deepEqual(toSensor.position, {lat: 1, lon: 2, altitude: 10000});
  assert.deepEqual(toSensor.messages, ["Tracking sensor: Roof"]);
  const nowhere = flightTarget("s1", {label: "Roof"}, {}, recon);
  assert.equal(nowhere.position, null);
  assert.deepEqual([nowhere.label, nowhere.messages], ["Roof", []]);
  assert.equal(flightTarget("s1", {}, {}, {}).label, "s1");
  assert.equal(flightTarget("s1", sensor, {E1: ["s1"]}, {E1: {name: "N"}}).position.altitude, 10000);   // the entity has no position
  assert.equal(flightTarget("s1", sensor, {E1: ["s1"]}, {E1: {location: {lat: 3, lon: 4}}}).label, "Entity E1");   // no name: its id
  const both = flightTarget("s1", sensor, {E1: ["s1"], E2: ["s1"]}, {E1: {location: {lat: 3, lon: 4}}, E2: {location: {lat: 5, lon: 6}}});
  assert.equal(both.position.lat, 3);   // the first assignment found
  assert.equal(flightTarget("s1", sensor, {E1: ["other"], E2: ["s1"]}, {E1: {location: {lat: 3, lon: 4}}, E2: {location: {lat: 5, lon: 6}}}).position.lat, 5);
});

test("PINNED: an assigned entity at latitude 0 is skipped and the sensor is used", () => {
  const t = flightTarget("s1", {label: "Roof", position: {lat: 1, lon: 2}}, {E1: ["s1"]}, {E1: {name: "N", location: {lat: 0, lon: 5}}});
  assert.deepEqual(t.position, {lat: 1, lon: 2, altitude: 10000});
});

// --- the tool ---

function setup({token = "tok", responses = {}, globeAvailable = true, recon = {}, hasList = true} = {}) {
  const state = {requests: [], out: [], view: [], globe: [], stream: new Map(), recon, token, globeAvailable, hasList, confirmAnswer: true, bound: null, unbound: 0, form: {label: "", type: "", assignEntity: null}};
  const http = async (url, init = {}) => {
    state.requests.push({url, init});
    const key = `${init.method || "GET"} ${url}`;
    const response = typeof responses[key] === "function" ? await responses[key](init) : responses[key];
    return response || {ok: true, status: 200, json: async () => ({})};
  };
  const ok = (body) => ({ok: true, status: 200, json: async () => body});
  const fail = (status, body) => ({ok: false, status, json: async () => body});
  const view = {
    hasList: () => state.hasList,
    setList: (html) => state.view.push(["list", html]),
    setCount: (n) => state.view.push(["count", n]),
    setEntityOptions: (o) => state.view.push(["options", o]),
    readForm: () => state.form,
    hideForm: () => state.view.push(["hide"]),
    clearForm: () => state.view.push(["clear"]),
    confirm: (text) => { state.view.push(["confirm", text]); return state.confirmAnswer; },
    showAssignDialog: (d) => { state.dialog = d; state.view.push(["dialog", d.title, d.options]); },
    bind(handlers) { state.bound = handlers; return () => { state.unbound++; }; },
  };
  const globe = {
    available: () => state.globeAvailable,
    syncMarkers: (markers, known) => state.globe.push(["markers", markers, known]),
    drawAssignments: (links) => state.globe.push(["links", links]),
    removeMarker: (id) => state.globe.push(["remove", id]),
    flyTo: (target) => state.globe.push(["fly", target]),
    clear: () => state.globe.push(["clear"]),
  };
  const workspace = createWorkspace({log: quiet});
  workspace.provide("httpFetch", http);
  workspace.provide("operatorSession", {token: () => state.token});
  workspace.provide("entityConsole", {info: (t) => state.out.push(["info", t]), alert: (t) => state.out.push(["alert", t])});
  workspace.provide("reconEntities", () => state.recon);
  workspace.provide("entityStream", {
    on: (event, handler) => state.stream.set(event, [...(state.stream.get(event) || []), handler]),
    off: (event, handler) => state.stream.set(event, (state.stream.get(event) || []).filter((h) => h !== handler)),
  });
  workspace.provide("sensorView", view);
  workspace.provide("sensorGlobe", globe);
  const plugin = createSensorPlugin({log: quiet});
  workspace.register(plugin.tool);
  const emit = (event) => { for (const type of ["preexisting", "create", "update", "delete"]) for (const h of state.stream.get(type) || []) h(event); };
  return {workspace, controls: plugin.controls, state, ok, fail, emit};
}
const up = async (t) => { await t.workspace.activate(SENSORS_ID); return t; };
const lists = (t) => t.state.view.filter((v) => v[0] === "list").map((v) => v[1]);

test("while inactive the controls do nothing and the data is empty", async () => {
  const t = setup();
  assert.deepEqual(t.controls.sensors, {});
  assert.deepEqual(t.controls.assignments, {});
  assert.equal(t.controls.load(), undefined);
  assert.equal(t.controls.flyTo("s"), undefined);
  assert.equal(t.controls.populateEntityDropdown(), undefined);
  assert.equal(t.controls.unassign("s", "e"), undefined);
  assert.equal(t.controls.emitActivity("s", "k", {}), undefined);
  assert.deepEqual(t.state.requests, []);
});

test("activating subscribes to the four entity events and binds the panel; deactivating undoes both and clears the globe", async () => {
  const t = await up(setup());
  assert.deepEqual([...t.state.stream.keys()].sort(), ["create", "delete", "preexisting", "update"]);
  assert.equal(typeof t.state.bound.load, "function");
  t.emit(wire("CREATE", "sensor:s1", "SENSOR", restSensor("s1")));
  assert.deepEqual(await t.workspace.deactivate(SENSORS_ID), []);
  assert.deepEqual(t.controls.sensors, {});
  t.state.requests.length = 0;
  assert.equal(t.controls.load(), undefined);
  assert.deepEqual(t.state.requests, []);
  for (const handlers of t.state.stream.values()) assert.deepEqual(handlers, []);
  assert.equal(t.state.unbound, 1);
  assert.deepEqual(t.state.globe.at(-1), ["clear"]);
  assert.deepEqual(t.controls.sensors, {});
});

test("load sends the session token, replaces the sensors with normalized ones, redraws, and reports", async () => {
  const t = setup({responses: {"GET /api/sensors": {ok: true, status: 200, json: async () => ({sensors: [restSensor("s1"), restSensor("s2", {position: undefined})]})}}});
  await up(t);
  await t.controls.load();
  assert.equal(t.state.requests[0].url, "/api/sensors");
  assert.deepEqual(t.state.requests[0].init.headers, {"Content-Type": "application/json", "X-Session-Token": "tok"});
  assert.deepEqual(Object.keys(t.controls.sensors), ["s1", "s2"]);
  assert.deepEqual(t.controls.sensors.s1.position, {lat: 36.1, lon: -86.7, alt_m: 10});
  assert.deepEqual(t.state.out, [["info", "Loaded 2 sensors"]]);
  assert.deepEqual(t.state.view[0], ["count", 2]);
  const markers = t.state.globe.find((g) => g[0] === "markers");
  assert.deepEqual(markers[1].map((m) => m.id), ["s1"]);     // s2 has no position
  assert.deepEqual(markers[2], ["s1", "s2"]);                // but it is known
});

test("load without a session token sends no token header", async () => {
  const t = await up(setup({token: null}));
  await t.controls.load();
  assert.deepEqual(t.state.requests[0].init.headers, {"Content-Type": "application/json"});
});

test("load: a failed request reports it and keeps the sensors; a late one after deactivation does nothing", async () => {
  const t = await up(setup({responses: {"GET /api/sensors": {ok: false, status: 503, json: async () => ({})}}}));
  await t.controls.load();
  assert.deepEqual(t.state.out, [["alert", "Error loading sensors: HTTP 503"]]);
  const hold = gate();
  const late = setup({responses: {"GET /api/sensors": async () => { await hold.promise; return {ok: true, status: 200, json: async () => ({sensors: [restSensor("s1")]}), }; }}});
  await up(late);
  const pending = late.controls.load();
  await late.workspace.deactivate(SENSORS_ID);
  late.state.view.length = 0;
  late.state.out.length = 0;
  hold.open();
  await pending;
  assert.deepEqual([late.state.view, late.state.out], [[], []]);
  const hold2 = gate();
  const lateFail = setup({responses: {"GET /api/sensors": async () => { await hold2.promise; throw new Error("offline"); }}});
  await up(lateFail);
  const p2 = lateFail.controls.load();
  await lateFail.workspace.deactivate(SENSORS_ID);
  hold2.open();
  await p2;
  assert.deepEqual(lateFail.state.out, []);
});

test("PINNED: loading replaces the sensors but leaves the assignments", async () => {
  const t = await up(setup({responses: {"GET /api/sensors": {ok: true, status: 200, json: async () => ({sensors: [restSensor("s1")]})}}}));
  t.emit(wire("CREATE", "edge:sensor_assigned:sensor:s9->recon:E1", "SENSOR_ASSIGNMENT", {nodes: ["sensor:s9", "recon:E1"]}));
  await t.controls.load();
  assert.deepEqual(t.controls.assignments, {E1: ["s9"]});
  assert.deepEqual(Object.keys(t.controls.sensors), ["s1"]);
});

test("the list shows 'No sensors loaded' with a zero count, and nothing is touched when the panel has no list", async () => {
  const t = await up(setup({responses: {"GET /api/sensors": {ok: true, status: 200, json: async () => ({sensors: []})}}}));
  await t.controls.load();
  assert.deepEqual(t.state.view.slice(0, 2), [["count", 0], ["list", NO_SENSORS]]);
  const bare = await up(setup({hasList: false, responses: {"GET /api/sensors": {ok: true, status: 200, json: async () => ({sensors: [restSensor("s1")]})}}}));
  await bare.controls.load();
  assert.deepEqual(bare.state.view, []);
  assert.equal(Object.keys(bare.controls.sensors).length, 1);
});

test("load: a response with no sensors list is an empty set", async () => {
  const t = await up(setup({responses: {"GET /api/sensors": {ok: true, status: 200, json: async () => ({})}}}));
  await t.controls.load();
  assert.deepEqual(t.controls.sensors, {});
  assert.deepEqual(t.state.out, [["info", "Loaded 0 sensors"]]);
});

// saving through the create form

const FORM = {label: "Roof", type: "RADAR", txFreq: "915", rxFreq: "433", txPower: "20", rxSens: "-90", lat: "36.1", lon: "-86.7", assignEntity: null};
const created = (extra = {}) => ({ok: true, status: 200, json: async () => ({created: true, sensor: restSensor("s7", {label: "Roof", ...extra})})});

test("saving sends the form as numbers, stores the sensor, reports 'created', hides and clears the form", async () => {
  const t = await up(setup({responses: {"POST /api/sensors": created()}}));
  t.state.form = FORM;
  await t.state.bound.save();
  const request = t.state.requests[0];
  assert.deepEqual(JSON.parse(request.init.body), {
    label: "Roof", type: "RADAR", tx_config: {frequency_mhz: 915, power_dbm: 20}, rx_config: {frequency_mhz: 433, sensitivity_dbm: -90}, position: {lat: 36.1, lon: -86.7},
  });
  assert.equal(request.init.headers["X-Session-Token"], "tok");
  assert.ok("s7" in t.controls.sensors);
  assert.deepEqual(t.state.out, [["info", "Sensor Roof created"]]);
  assert.deepEqual(t.state.view.slice(-2), [["hide"], ["clear"]]);
});

test("saving reports 'updated' when the server says it was not created", async () => {
  const t = await up(setup({responses: {"POST /api/sensors": {ok: true, status: 200, json: async () => ({sensor: restSensor("s7", {label: "Roof"})})}}}));
  t.state.form = FORM;
  await t.state.bound.save();
  assert.deepEqual(t.state.out, [["info", "Sensor Roof updated"]]);
});

test("saving needs a label; type defaults to SDR; empty or zero numbers become null, which drops the radio blocks and the position", async () => {
  const t = await up(setup({responses: {"POST /api/sensors": created()}}));
  t.state.form = {...FORM, label: ""};
  await t.state.bound.save();
  assert.deepEqual(t.state.out, [["alert", "Please enter a sensor label"]]);
  assert.deepEqual(t.state.requests, []);
  t.state.out.length = 0;
  t.state.form = {label: "X", type: "", txFreq: "", rxFreq: "0", txPower: "5", rxSens: "x", lat: "0", lon: "10", assignEntity: null};
  await t.state.bound.save();
  assert.deepEqual(JSON.parse(t.state.requests[0].init.body), {label: "X", type: "SDR", tx_config: null, rx_config: null, position: null});   // PINNED: 0 is "none"
});

test("saving with an entity chosen assigns the new sensor to it", async () => {
  const t = await up(setup({responses: {"POST /api/sensors": created(), "POST /api/recon/entity/E1/assign_sensor": {ok: true, status: 200, json: async () => ({})}}}));
  t.state.form = {...FORM, assignEntity: "E1"};
  await t.state.bound.save();
  assert.deepEqual(t.state.requests.map((r) => r.url), ["/api/sensors", "/api/recon/entity/E1/assign_sensor"]);
  assert.deepEqual(JSON.parse(t.state.requests[1].init.body), {sensor_id: "s7"});
  assert.deepEqual(t.controls.assignments, {E1: ["s7"]});
});

test("a failed save reports the server's error text, keeps the form, and does not assign", async () => {
  const t = await up(setup({responses: {"POST /api/sensors": {ok: false, status: 400, json: async () => ({error: "bad label", message: "ignored"})}}}));
  t.state.form = {...FORM, assignEntity: "E1"};
  await t.state.bound.save();
  assert.deepEqual(t.state.out, [["alert", "Error saving sensor: bad label"]]);
  assert.deepEqual(t.state.view.filter((v) => v[0] === "hide" || v[0] === "clear"), []);
  assert.equal(t.state.requests.length, 1);
  const noText = await up(setup({responses: {"POST /api/sensors": {ok: false, status: 500, json: async () => ({})}}}));
  noText.state.form = FORM;
  await noText.state.bound.save();
  assert.deepEqual(noText.state.out, [["alert", "Error saving sensor: HTTP 500"]]);
  const onlyMessage = await up(setup({responses: {"POST /api/sensors": {ok: false, status: 400, json: async () => ({message: "only message"})}}}));
  onlyMessage.state.form = FORM;
  await onlyMessage.state.bound.save();
  assert.deepEqual(onlyMessage.state.out, [["alert", "Error saving sensor: HTTP 400"]]);   // PINNED: only `error` is read
});

test("a save that finishes after deactivation changes and reports nothing", async () => {
  const hold = gate();
  const t = await up(setup({responses: {"POST /api/sensors": async () => { await hold.promise; return created(); }}}));
  t.state.form = FORM;
  const pending = t.state.bound.save();
  await settle();
  await t.workspace.deactivate(SENSORS_ID);
  t.state.view.length = 0;
  hold.open();
  await pending;
  assert.deepEqual([t.state.out, t.state.view], [[], []]);
  const hold2 = gate();
  const failing = await up(setup({responses: {"POST /api/sensors": async () => { await hold2.promise; return {ok: false, status: 500, json: async () => ({})}; }}}));
  failing.state.form = FORM;
  const p2 = failing.state.bound.save();
  await settle();
  await failing.workspace.deactivate(SENSORS_ID);
  hold2.open();
  await p2;
  assert.deepEqual(failing.state.out, []);
});

test("a stale assignment after a save does not hide or clear the form", async () => {
  const hold = gate();
  const t = await up(setup({responses: {"POST /api/sensors": created(), "POST /api/recon/entity/E1/assign_sensor": async () => { await hold.promise; return {ok: true, status: 200, json: async () => ({})}; }}}));
  t.state.form = {...FORM, assignEntity: "E1"};
  const pending = t.state.bound.save();
  await settle();
  await t.workspace.deactivate(SENSORS_ID);
  t.state.view.length = 0;
  hold.open();
  await pending;
  assert.deepEqual(t.state.view, []);
});

test("opening the form fills the entity choice from the recon entities (name, else id)", async () => {
  const t = await up(setup({recon: {E1: {name: "Falcon"}, E2: {}}}));
  t.state.bound.openForm();
  assert.deepEqual(t.state.view[0], ["options", [{id: "E1", label: "Falcon"}, {id: "E2", label: "E2"}]]);
  t.controls.populateEntityDropdown();
  assert.equal(t.state.view.length, 2);
});

// delete

test("delete asks first; on yes it removes the sensor, its assignments and its marker, redraws the list but not the lines", async () => {
  const t = await up(setup({responses: {"DELETE /api/sensors/s1": {ok: true, status: 200, json: async () => ({})}}}));
  t.emit(wire("CREATE", "sensor:s1", "SENSOR", restSensor("s1")));
  t.emit(wire("CREATE", "edge:sensor_assigned:sensor:s1->recon:E1", "SENSOR_ASSIGNMENT", {nodes: ["sensor:s1", "recon:E1"]}));
  t.state.confirmAnswer = false;
  t.state.bound.remove("s1");
  assert.deepEqual(t.state.view.at(-1), ["confirm", "Delete sensor s1?"]);
  assert.equal(t.state.requests.length, 0);
  t.state.confirmAnswer = true;
  t.state.view.length = 0;
  t.state.globe.length = 0;
  t.state.bound.remove("s1");
  await settle();
  assert.equal(t.state.requests[0].init.method, "DELETE");
  assert.deepEqual(t.controls.sensors, {});
  assert.deepEqual(t.controls.assignments, {E1: []});
  assert.deepEqual(t.state.globe, [["remove", "s1"]]);   // PINNED: no redraw of the assignment lines
  assert.deepEqual(t.state.out.at(-1), ["info", "Sensor s1 deleted"]);
  assert.deepEqual(t.state.view.filter((v) => v[0] === "list").length, 1);
});

test("a failed or late delete keeps the sensor and reports only while active", async () => {
  const t = await up(setup({responses: {"DELETE /api/sensors/s1": {ok: false, status: 403, json: async () => ({})}}}));
  t.emit(wire("CREATE", "sensor:s1", "SENSOR", restSensor("s1")));
  t.state.bound.remove("s1");
  await settle();
  assert.ok("s1" in t.controls.sensors);
  assert.deepEqual(t.state.out.at(-1), ["alert", "Error deleting sensor: HTTP 403"]);
  const hold = gate();
  const late = await up(setup({responses: {"DELETE /api/sensors/s1": async () => { await hold.promise; return {ok: true, status: 200, json: async () => ({})}; }}}));
  late.emit(wire("CREATE", "sensor:s1", "SENSOR", restSensor("s1")));
  late.state.bound.remove("s1");
  await settle();
  await late.workspace.deactivate(SENSORS_ID);
  late.state.globe.length = 0;
  hold.open();
  await settle();
  assert.deepEqual(late.state.globe, []);
  assert.deepEqual(late.state.out, []);
});

// assign, unassign, activity

test("assign posts the sensor id, records the assignment once, redraws, and reports", async () => {
  const t = await up(setup({responses: {"POST /api/recon/entity/E1/assign_sensor": {ok: true, status: 200, json: async () => ({})}}}));
  t.emit(wire("CREATE", "sensor:s1", "SENSOR", restSensor("s1")));
  t.state.view.length = 0;
  t.state.globe.length = 0;
  t.state.recon = {E1: {name: "Falcon"}};
  t.state.bound.assign("s1");
  t.state.dialog.onConfirm("E1");
  await settle();
  t.state.dialog.onConfirm("E1");
  await settle();
  assert.deepEqual(t.controls.assignments, {E1: ["s1"]});
  assert.deepEqual(t.state.out.filter((o) => /assigned/.test(o[1])).length, 2);
  assert.ok(t.state.view.some((v) => v[0] === "list"));
  assert.ok(t.state.globe.some((g) => g[0] === "links"));
});

test("assign: a failure reports the server's error, else message, else the status; and does not record", async () => {
  for (const [body, expected] of [[{error: "e", message: "m"}, "e"], [{message: "m"}, "m"], [{}, "HTTP 409"], [null, "HTTP 409"]]) {
    const t = await up(setup({responses: {"POST /api/recon/entity/E1/assign_sensor": {ok: false, status: 409, json: async () => body}}}));
    t.emit(wire("CREATE", "sensor:s1", "SENSOR", restSensor("s1")));
    t.state.recon = {E1: {}};
    t.state.bound.assign("s1");
    await t.state.dialog.onConfirm("E1");
    assert.deepEqual(t.state.out.at(-1), ["alert", `Error assigning sensor: ${expected}`]);
    assert.deepEqual(t.controls.assignments, {});
  }
});

test("the assign dialog: no recon entities is an alert; an unknown sensor opens nothing; an empty choice assigns nothing; a late confirm assigns nothing", async () => {
  const t = await up(setup());
  t.emit(wire("CREATE", "sensor:s1", "SENSOR", restSensor("s1", {label: "<b>Roof</b>"})));
  t.state.bound.assign("s1");
  assert.deepEqual(t.state.out.at(-1), ["alert", "No recon entities available to assign sensor to"]);
  t.state.bound.assign("nope");
  assert.equal(t.state.view.filter((v) => v[0] === "dialog").length, 0);
  t.state.recon = {E1: {name: "Falcon"}, E2: {}};
  t.state.bound.assign("s1");
  assert.deepEqual(t.state.view.at(-1), ["dialog", "Assign Sensor: <b>Roof</b>", [{id: "E1", label: "Falcon"}, {id: "E2", label: "E2"}]]);
  await t.state.dialog.onConfirm("");
  assert.equal(t.state.requests.length, 0);
  const dialog = t.state.dialog;
  await t.workspace.deactivate(SENSORS_ID);
  await dialog.onConfirm("E1");
  assert.equal(t.state.requests.length, 0);
});

test("unassign deletes the assignment, removes it locally, redraws, and reports; failures report", async () => {
  const t = await up(setup({responses: {"DELETE /api/sensors/s1/unassign/E1": {ok: true, status: 200, json: async () => ({})}, "DELETE /api/sensors/s2/unassign/E1": {ok: false, status: 404, json: async () => ({})}}}));
  t.emit(wire("CREATE", "edge:sensor_assigned:sensor:s1->recon:E1", "SENSOR_ASSIGNMENT", {nodes: ["sensor:s1", "recon:E1"]}));
  t.emit(wire("CREATE", "edge:sensor_assigned:sensor:s2->recon:E1", "SENSOR_ASSIGNMENT", {nodes: ["sensor:s2", "recon:E1"]}));
  await t.controls.unassign("s1", "E1");
  assert.deepEqual(t.controls.assignments, {E1: ["s2"]});
  assert.deepEqual(t.state.out.at(-1), ["info", "Sensor s1 unassigned from entity E1"]);
  await t.controls.unassign("s2", "E1");
  assert.deepEqual(t.controls.assignments, {E1: ["s2"]});
  assert.deepEqual(t.state.out.at(-1), ["alert", "Error unassigning sensor: HTTP 404"]);
  await t.controls.unassign("s1", "NOPE");   // an entity with no assignments: fine
});

test("emitActivity posts the activity and returns the answer; a failure reports and returns nothing", async () => {
  const t = await up(setup({responses: {"POST /api/sensors/s1/activity": {ok: true, status: 200, json: async () => ({status: "ok"})}, "POST /api/sensors/s2/activity": {ok: false, status: 500, json: async () => ({})}}}));
  assert.deepEqual(await t.controls.emitActivity("s1", "detection", {snr_db: 3}), {status: "ok"});
  assert.deepEqual(JSON.parse(t.state.requests[0].init.body), {activity_type: "detection", payload: {snr_db: 3}});
  assert.deepEqual(t.state.out.at(-1), ["info", "Sensor activity: detection from s1"]);
  assert.equal(await t.controls.emitActivity("s2", "x", {}), undefined);
  assert.deepEqual(t.state.out.at(-1), ["alert", "Error emitting sensor activity: HTTP 500"]);
});

// fly to

test("flyTo reports, then flies; with no globe it reports the position as unknown; an unknown sensor does nothing", async () => {
  const t = await up(setup({recon: {E1: {name: "Falcon", location: {lat: 10, lon: 20}}}}));
  t.emit(wire("CREATE", "sensor:s1", "SENSOR", restSensor("s1")));
  t.state.out.length = 0;
  t.state.globe.length = 0;
  t.controls.flyTo("nope");
  assert.deepEqual([t.state.out, t.state.globe], [[], []]);
  t.controls.flyTo("s1");
  assert.deepEqual(t.state.out, [["info", "Tracking sensor: Sensor s1"]]);
  assert.deepEqual(t.state.globe, [["fly", {lat: 36.1, lon: -86.7, altitude: 10000}]]);
  t.emit(wire("CREATE", "edge:sensor_assigned:sensor:s1->recon:E1", "SENSOR_ASSIGNMENT", {nodes: ["sensor:s1", "recon:E1"]}));
  t.state.out.length = 0;
  t.state.bound.track("s1");
  assert.deepEqual(t.state.out, [["info", "Tracking assigned entity: Entity Falcon"]]);
  t.state.globeAvailable = false;
  t.state.out.length = 0;
  t.controls.flyTo("s1");
  assert.deepEqual(t.state.out, [["info", "Tracking assigned entity: Entity Falcon"], ["alert", "Could not determine position for Entity Falcon"]]);
});

test("flyTo for a sensor with no position says so", async () => {
  const t = await up(setup());
  t.emit(wire("CREATE", "sensor:s1", "SENSOR", {sensor_id: "s1", label: "Lost"}));
  t.state.out.length = 0;
  t.controls.flyTo("s1");
  assert.deepEqual(t.state.out, [["alert", "Could not determine position for Lost"]]);
  assert.equal(t.state.globe.filter((g) => g[0] === "fly").length, 0);
});

// group moves

test("moving a group reorders the list; an unknown group changes nothing", async () => {
  const t = await up(setup());
  for (const [s, e] of [["s1", "E1"], ["s2", "E2"]]) {
    t.emit(wire("CREATE", `sensor:${s}`, "SENSOR", restSensor(s)));
    t.emit(wire("CREATE", `edge:sensor_assigned:sensor:${s}->recon:${e}`, "SENSOR_ASSIGNMENT", {nodes: [`sensor:${s}`, `recon:${e}`]}));
  }
  const firstHeader = (html) => html.match(/TASK FORCE:<\/span>(E\d)/)[1];
  assert.equal(firstHeader(lists(t).at(-1)), "E1");
  t.state.bound.moveGroup("E1", "down");
  assert.equal(firstHeader(lists(t).at(-1)), "E2");
  const count = lists(t).length;
  t.state.bound.moveGroup("NOPE", "up");
  assert.equal(lists(t).length, count);
});

// stream events

test("a SENSOR event adds the sensor under its sensor_id, normalized, and redraws the list and the globe", async () => {
  const t = await up(setup());
  t.emit(wire("CREATE", "sensor:s1", "SENSOR", restSensor("s1")));
  assert.deepEqual(Object.keys(t.controls.sensors), ["s1"]);
  assert.deepEqual(t.controls.sensors.s1.position, {lat: 36.1, lon: -86.7, alt_m: 10});
  assert.equal(t.controls.sensors.s1.tx_config.frequency_mhz, 915);
  assert.ok(t.state.view.some((v) => v[0] === "list"));
  assert.ok(t.state.globe.some((g) => g[0] === "markers"));
  t.emit(wire("UPDATE", "sensor:s1", "SENSOR", restSensor("s1", {label: "Renamed"})));
  assert.equal(t.controls.sensors.s1.label, "Renamed");
  t.emit(wire("CREATE", "sensor:s2", "SENSOR", {id: "sensor:s2", type: "SENSOR", sensor: {sensor_id: "s2", label: "Mast"}}));
  assert.equal(t.controls.sensors.s2.label, "Mast");
});

test("a removal drops the sensor, its assignments and its marker", async () => {
  const t = await up(setup());
  t.emit(wire("CREATE", "sensor:s1", "SENSOR", restSensor("s1")));
  t.emit(wire("CREATE", "edge:sensor_assigned:sensor:s1->recon:E1", "SENSOR_ASSIGNMENT", {nodes: ["sensor:s1", "recon:E1"]}));
  t.state.globe.length = 0;
  t.emit(wire("CREATE", "sensor:s1", "SENSOR_TOMBSTONE", {deleted: true}));
  assert.deepEqual(t.controls.sensors, {});
  assert.deepEqual(t.controls.assignments, {E1: []});
  assert.deepEqual(t.state.globe.slice(0, 2), [["remove", "s1"], ["markers", [], []]]);
  t.emit(wire("DELETE", "sensor:s9", "sensor", {}));
});

test("an assignment adds the sensor to the entity once; a tombstone or a DELETE of the edge removes it; both redraw list and globe", async () => {
  const t = await up(setup());
  const edge = "edge:sensor_assigned:sensor:s1->recon:E1";
  t.emit(wire("CREATE", edge, "SENSOR_ASSIGNMENT", {nodes: ["sensor:s1", "recon:E1"]}));
  t.emit(wire("UPDATE", edge, "SENSOR_ASSIGNMENT", {nodes: ["sensor:s1", "recon:E1"]}));
  assert.deepEqual(t.controls.assignments, {E1: ["s1"]});
  t.state.view.length = 0;
  t.state.globe.length = 0;
  t.emit(wire("CREATE", edge, "SENSOR_ASSIGNMENT_TOMBSTONE", {deleted: true, from: "sensor:s1", to: "recon:E1"}));
  assert.deepEqual(t.controls.assignments, {E1: []});
  t.emit(wire("CREATE", edge, "SENSOR_ASSIGNMENT", {nodes: ["sensor:s1", "recon:E1"]}));
  t.emit(wire("DELETE", edge, "edge", {}));
  assert.deepEqual(t.controls.assignments, {E1: []});
  t.emit(wire("DELETE", "edge:sensor_assigned:sensor:s1->recon:NEVER", "edge", {}));
  assert.ok(t.state.globe.filter((g) => g[0] === "links").length >= 3);
});

test("events that are not sensor or assignment events, and assignments naming no ends, change nothing", async () => {
  const t = await up(setup());
  t.emit(wire("CREATE", "x", "RECON_ENTITY", {}));
  t.emit(wire("CREATE", "a", "SENSOR_ACTIVITY", {sensor_id: "s1"}));
  t.emit(wire("CREATE", "e", "SENSOR_ASSIGNMENT", {}));
  t.emit({entity: {type: "SENSOR"}, event: "CREATE"});
  assert.deepEqual([t.controls.sensors, t.controls.assignments, t.state.view, t.state.globe], [{}, {}, [], []]);
});

test("events after deactivation are ignored", async () => {
  const t = await up(setup());
  const handlers = [...t.state.stream.values()].flat();
  await t.workspace.deactivate(SENSORS_ID);
  t.state.view.length = 0;
  for (const h of handlers) h(wire("CREATE", "sensor:s1", "SENSOR", restSensor("s1")));
  assert.deepEqual(t.state.view, []);
});

// the globe

test("without a globe nothing is synced or drawn, but the list still is", async () => {
  const t = await up(setup({globeAvailable: false}));
  t.emit(wire("CREATE", "sensor:s1", "SENSOR", restSensor("s1")));
  assert.deepEqual(t.state.globe, []);
  assert.ok(t.state.view.some((v) => v[0] === "list"));
});

test("the globe gets markers only for sensors with a position, every sensor id, and the links", async () => {
  const t = await up(setup({recon: {E1: {location: {lat: 5, lon: 6}}}}));
  t.emit(wire("CREATE", "sensor:s1", "SENSOR", restSensor("s1")));
  t.emit(wire("CREATE", "sensor:s2", "SENSOR", {sensor_id: "s2"}));
  t.emit(wire("CREATE", "edge:sensor_assigned:sensor:s1->recon:E1", "SENSOR_ASSIGNMENT", {nodes: ["sensor:s1", "recon:E1"]}));
  const markers = t.state.globe.filter((g) => g[0] === "markers").at(-1);
  assert.deepEqual(markers[1].map((m) => m.id), ["s1"]);
  assert.deepEqual(markers[2], ["s1", "s2"]);
  const links = t.state.globe.filter((g) => g[0] === "links").at(-1)[1];
  assert.deepEqual(links.map((l) => [l.sensorId, l.entityId]), [["s1", "E1"]]);
});

test("the list is headed by the entity's name when the recon entity is known", async () => {
  const t = await up(setup({recon: {E1: {name: "Falcon"}}}));
  t.emit(wire("CREATE", "sensor:s1", "SENSOR", restSensor("s1")));
  t.emit(wire("CREATE", "edge:sensor_assigned:sensor:s1->recon:E1", "SENSOR_ASSIGNMENT", {nodes: ["sensor:s1", "recon:E1"]}));
  assert.match(lists(t).at(-1), /Falcon \(E1\)/);
});

test("load is retried by the Load button: the handler calls it", async () => {
  const t = await up(setup({responses: {"GET /api/sensors": {ok: true, status: 200, json: async () => ({sensors: [restSensor("s1")]})}}}));
  await t.state.bound.load();
  assert.deepEqual(Object.keys(t.controls.sensors), ["s1"]);
});
