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
