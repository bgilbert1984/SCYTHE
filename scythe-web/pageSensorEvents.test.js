import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import * as sensorEvents from "./sensorEvents.js";
import {bootWorkspace} from "./workspaceBoot.js";

const page = readFileSync(new URL("../command-ops-visualization.html", import.meta.url), "utf8");

/** The source of `object.member` as an object-literal entry, cut out of the page. */
function member(objectMarker, name) {
  const start = page.indexOf(objectMarker);
  assert.ok(start > 0, `${objectMarker} not found`);
  const at = page.indexOf(`            ${name}: `, start);
  assert.ok(at > 0, `${name} not found after ${objectMarker}`);
  const next = [page.indexOf("\n            },\n", at), page.indexOf("\n            }\n        };", at)].filter((i) => i > 0);
  const end = Math.min(...next);
  const closing = page.startsWith("\n            },", end) ? "\n            }" : "\n            }";
  return page.slice(at, end + closing.length);
}
const build = (objectMarker, names, scope, extra = {}) =>
  new Function(...Object.keys(scope), `return {${names.map((n) => member(objectMarker, n)).join(",\n")}};`)(...Object.values(scope));

const wire = (eventType, entityId, entityType, entityData) => ({event_type: eventType, entity_id: entityId, entity_type: entityType, entity_data: entityData, sequence_id: 1});
const sensorBody = {sensor_id: "s1", label: "Roof", position: [36.1, -86.7, 10], tx: {bands_mhz: [[915, 915]], max_eirp_dbm: 20}};
const edge = {id: "edge:sensor_assigned:sensor:s1->recon:E1", kind: "sensor_assigned", nodes: ["sensor:s1", "recon:E1"]};

function sensorManager() {
  const calls = [];
  const window = {ScytheSensorEvents: sensorEvents};
  const manager = build("const SensorManager = {", ["_normalize", "applyEntitySyncEvent", "subscribeToEntitySync"], {window, EntitySync: undefined, console: {log() {}, warn() {}}});
  Object.assign(manager, {
    sensors: {}, assignments: {}, calls,
    _updateUI: () => calls.push("ui"), _visualize: () => calls.push("viz"), _removeMarker: (id) => calls.push(["marker", id]),
  });
  return {manager, calls, window};
}

test("SensorManager: a SENSOR event adds the sensor under its sensor_id, normalized, and redraws", () => {
  const {manager, calls} = sensorManager();
  manager.applyEntitySyncEvent(wire("CREATE", "sensor:s1", "SENSOR", sensorBody));
  assert.deepEqual(Object.keys(manager.sensors), ["s1"]);
  assert.equal(manager.sensors.s1.label, "Roof");
  assert.deepEqual(manager.sensors.s1.position, {lat: 36.1, lon: -86.7, alt_m: 10});   // _normalize ran
  assert.equal(manager.sensors.s1.tx_config.frequency_mhz, 915);
  assert.deepEqual(calls, ["ui", "viz"]);
});

test("SensorManager: an update replaces the sensor; the registry's nested form works too", () => {
  const {manager} = sensorManager();
  manager.applyEntitySyncEvent(wire("CREATE", "sensor:s1", "SENSOR", sensorBody));
  manager.applyEntitySyncEvent(wire("UPDATE", "sensor:s1", "SENSOR", {...sensorBody, label: "Roof 2"}));
  assert.equal(manager.sensors.s1.label, "Roof 2");
  manager.applyEntitySyncEvent(wire("CREATE", "sensor:s2", "SENSOR", {id: "sensor:s2", type: "SENSOR", sensor: {sensor_id: "s2", label: "Mast"}}));
  assert.deepEqual(Object.keys(manager.sensors).sort(), ["s1", "s2"]);
});

test("SensorManager: a removal drops the sensor, its assignments and its marker", () => {
  const {manager, calls} = sensorManager();
  manager.applyEntitySyncEvent(wire("CREATE", "sensor:s1", "SENSOR", sensorBody));
  manager.applyEntitySyncEvent(wire("CREATE", edge.id, "SENSOR_ASSIGNMENT", edge));
  manager.assignments.E2 = ["s1", "s9"];
  calls.length = 0;
  manager.applyEntitySyncEvent(wire("CREATE", "sensor:s1", "SENSOR_TOMBSTONE", {deleted: true}));
  assert.deepEqual(manager.sensors, {});
  assert.deepEqual(manager.assignments, {E1: [], E2: ["s9"]});
  assert.deepEqual(calls, [["marker", "s1"], "ui", "viz"]);
  manager.applyEntitySyncEvent(wire("DELETE", "sensor:s9", "sensor", {}));   // the bridge's form, for a sensor we never had
  assert.deepEqual(manager.assignments, {E1: [], E2: []});
});

test("SensorManager: an assignment adds the sensor to the entity once, and redraws the list as well as the map", () => {
  const {manager, calls} = sensorManager();
  manager.applyEntitySyncEvent(wire("CREATE", edge.id, "SENSOR_ASSIGNMENT", edge));
  manager.applyEntitySyncEvent(wire("UPDATE", edge.id, "SENSOR_ASSIGNMENT", edge));
  assert.deepEqual(manager.assignments, {E1: ["s1"]});
  assert.deepEqual(calls, ["ui", "viz", "ui", "viz"]);
});

test("SensorManager: an assignment tombstone and a DELETE of the edge both remove it; an unknown entity is fine", () => {
  const {manager} = sensorManager();
  manager.assignments.E1 = ["s1", "s2"];
  manager.applyEntitySyncEvent(wire("CREATE", edge.id, "SENSOR_ASSIGNMENT_TOMBSTONE", {deleted: true, from: "sensor:s1", to: "recon:E1"}));
  assert.deepEqual(manager.assignments, {E1: ["s2"]});
  manager.applyEntitySyncEvent(wire("DELETE", "edge:sensor_assigned:sensor:s2->recon:E1", "edge", {}));
  assert.deepEqual(manager.assignments, {E1: []});
  manager.applyEntitySyncEvent(wire("DELETE", "edge:sensor_assigned:sensor:s2->recon:NEVER", "edge", {}));
  assert.deepEqual(manager.assignments, {E1: []});
});

test("SensorManager: events it does not handle, and an assignment that names no ends, change nothing", () => {
  const {manager, calls} = sensorManager();
  manager.applyEntitySyncEvent(wire("CREATE", "x", "RECON_ENTITY", {}));
  manager.applyEntitySyncEvent(wire("CREATE", "x", "SENSOR_ACTIVITY", {sensor_id: "s1"}));
  manager.applyEntitySyncEvent(wire("CREATE", "x", "SENSOR_ASSIGNMENT", {}));
  assert.deepEqual([manager.sensors, manager.assignments, calls], [{}, {}, []]);
});

test("SensorManager: without the helpers (the boot module has not run) nothing happens", () => {
  const {manager, window, calls} = sensorManager();
  delete window.ScytheSensorEvents;
  manager.applyEntitySyncEvent(wire("CREATE", "sensor:s1", "SENSOR", sensorBody));
  assert.deepEqual([manager.sensors, calls], [{}, []]);
});

test("SensorManager subscribes to the four entity events and passes sensor and assignment events, but not activity or others, on", () => {
  const registered = {};
  const window = {ScytheSensorEvents: sensorEvents};
  const EntitySync = {on: (event, handler) => { registered[event] = handler; }};
  const manager = build("const SensorManager = {", ["subscribeToEntitySync"], {window, EntitySync, console: {log() {}, warn() {}}});
  const applied = [];
  manager.applyEntitySyncEvent = (event) => applied.push(event.entity_id);
  manager.subscribeToEntitySync();
  assert.deepEqual(Object.keys(registered).sort(), ["create", "delete", "preexisting", "update"]);
  registered.create(wire("CREATE", "sensor:s1", "SENSOR", sensorBody));
  registered.preexisting(wire("PREEXISTING", edge.id, "SENSOR_ASSIGNMENT", edge));
  registered.delete(wire("DELETE", "sensor:s1", "sensor", {}));
  registered.update(wire("UPDATE", "a1", "SENSOR_ACTIVITY", {sensor_id: "s1"}));
  registered.update(wire("UPDATE", "e1", "RECON_ENTITY", {}));
  assert.deepEqual(applied, ["sensor:s1", edge.id, "sensor:s1"]);
  delete window.ScytheSensorEvents;
  registered.create(wire("CREATE", "sensor:s2", "SENSOR", sensorBody));   // no helpers yet: ignored, not thrown
  assert.equal(applied.length, 3);
});

const registryActivity = {id: "edge:activity:aoa_measured:s1:1:abc", kind: "aoa_measured", nodes: ["sensor:s1", "recon:E1"], metadata: {payload: {bearing_deg: 41}}};
const restActivity = {activity_id: "activity:s1:1", entity_type: "SENSOR_ACTIVITY", kind: "detection", nodes: ["sensor:s1"], sensor_id: "s1", payload: {snr_db: 12}};

function subscriberOf(objectMarker, memberName, receiver) {
  const registered = {};
  const window = {ScytheSensorEvents: sensorEvents};
  const EntitySync = {on: (event, handler) => { registered[event] = [...(registered[event] || []), handler]; }};
  const object = build(objectMarker, [memberName], {window, EntitySync, console: {log() {}, warn() {}}, setTimeout: () => 0});
  Object.assign(object, receiver);
  object[memberName]();
  return {registered, window};
}

test("LPIManager hands an activity event on in the flat form and ignores everything else", () => {
  const seen = [];
  const {registered, window} = subscriberOf("const LPIManager = {", "subscribeToEntitySync", {handleActivityEvent: (a) => seen.push(a)});
  assert.deepEqual(Object.keys(registered).sort(), ["create", "update"]);
  registered.create[0](wire("CREATE", restActivity.activity_id, "SENSOR_ACTIVITY", restActivity));
  registered.update[0](wire("UPDATE", registryActivity.id, "SENSOR_ACTIVITY", registryActivity));
  registered.create[0](wire("CREATE", "sensor:s1", "SENSOR", sensorBody));
  assert.equal(seen.length, 2);
  assert.deepEqual([seen[0].sensor_id, seen[0].activity_type, seen[0].payload], ["s1", "detection", {snr_db: 12}]);
  assert.deepEqual([seen[1].sensor_id, seen[1].activity_type, seen[1].payload], ["s1", "aoa_measured", {bearing_deg: 41}]);
  delete window.ScytheSensorEvents;
  registered.create[0](wire("CREATE", restActivity.activity_id, "SENSOR_ACTIVITY", restActivity));   // no helpers: ignored
  assert.equal(seen.length, 2);
});

test("FusionVisualization does the same through handleActivity", () => {
  const seen = [];
  const {registered, window} = subscriberOf("const FusionVisualization = {", "init", {handleActivity: (a) => seen.push(a)});
  assert.deepEqual(Object.keys(registered).sort(), ["create", "update"]);
  registered.create[0](wire("CREATE", registryActivity.id, "SENSOR_ACTIVITY", registryActivity));
  registered.update[0](wire("UPDATE", restActivity.activity_id, "SENSOR_ACTIVITY", restActivity));
  registered.create[0](wire("CREATE", edge.id, "SENSOR_ASSIGNMENT", edge));
  assert.deepEqual(seen.map((a) => [a.sensor_id, a.activity_type]), [["s1", "aoa_measured"], ["s1", "detection"]]);
  delete window.ScytheSensorEvents;
  registered.create[0](wire("CREATE", registryActivity.id, "SENSOR_ACTIVITY", registryActivity));
  assert.equal(seen.length, 2);
});

test("the flat form is what handleActivity's second branch reads, so FusionVisualization uses the payload", () => {
  const a = sensorEvents.activityOf(wire("CREATE", registryActivity.id, "SENSOR_ACTIVITY", registryActivity));
  assert.ok(a.sensor_id && a.activity_type);                       // `data.sensor_id && data.activity_type`
  assert.equal(a.entity_data, undefined);                          // so the edge branch (data.entity_data.kind) is not taken
  assert.match(member("const FusionVisualization = {", "handleActivity"), /data\.sensor_id && data\.activity_type/);
});

test("the page no longer reads the event shape that never matched, and the boot exposes the helpers", () => {
  assert.doesNotMatch(page, /event\.entity\?\.type \|\| event\.type/);
  assert.doesNotMatch(page, /const entity = evt\.entity \|\| evt/);
  assert.doesNotMatch(page, /entity\.type === 'SENSOR_ACTIVITY'/);
  const win = {console: {warn() {}, log() {}, debug() {}}, Event: class { constructor(t) { this.type = t; } }, dispatchEvent() {}, fetch: async () => ({ok: true, json: async () => ({})})};
  bootWorkspace(win);
  assert.equal(win.ScytheSensorEvents, sensorEvents);
});
