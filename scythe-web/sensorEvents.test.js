import test from "node:test";
import assert from "node:assert/strict";
import {classify, sensorOf, removedSensorId, assignmentOf, activityOf} from "./sensorEvents.js";

// An event as EntityEvent.to_dict() in operator_session_manager.py builds it (checked against the real class).
const wire = (eventType, entityId, entityType, entityData, extra = {}) => ({
  event_type: eventType, entity_id: entityId, entity_type: entityType, entity_data: entityData,
  provenance: {source_description: "Alice"}, timestamp: "2026-10-25T00:00:00Z", sequence_id: 5, ...extra,
});

// What the server puts in entity_data (see the header of sensorEvents.js for where each comes from).
const restSensor = {sensor_id: "s1", node_id: "sensor:s1", entity_type: "SENSOR", type: "SENSOR", label: "Roof", position: [36.1, -86.7, 10]};
const registrySensor = {id: "sensor:s2", type: "SENSOR", sensor: {sensor_id: "s2", id: "s2", node_id: "sensor:s2", type: "sensor", label: "Mast"}, node: {id: "sensor:s2", kind: "sensor"}, timestamp: 1};
const assignmentEdge = {id: "edge:sensor_assigned:sensor:s1->recon:ENTITY-0001", kind: "sensor_assigned", nodes: ["sensor:s1", "recon:ENTITY-0001"], weight: 1};
const assignmentTombstone = {entity_id: "edge:sensor_assigned:sensor:s1->recon:ENTITY-0001", type: "SENSOR_ASSIGNMENT_TOMBSTONE", deleted: true, from: "sensor:s1", to: "recon:ENTITY-0001"};
const registryActivity = {id: "edge:activity:aoa_measured:s1:1:abc", kind: "aoa_measured", nodes: ["sensor:s1", "recon:ENTITY-0001"], metadata: {payload: {bearing_deg: 41, timestamp: 100}, emitted_at: 2}, timestamp: 3};
const restActivity = {activity_id: "activity:s1:1", entity_type: "SENSOR_ACTIVITY", kind: "detection", nodes: ["sensor:s1"], sensor_id: "s1", payload: {snr_db: 12, timestamp: 200}, metadata: {sensor_name: "Roof"}, timestamp: 4};

test("classify: each entity type, by the top-level entity_type", () => {
  assert.equal(classify(wire("CREATE", "sensor:s1", "SENSOR", restSensor)), "sensor");
  assert.equal(classify(wire("UPDATE", "sensor:s1", "SENSOR", restSensor)), "sensor");
  assert.equal(classify(wire("PREEXISTING", "sensor:s1", "SENSOR", restSensor)), "sensor");
  assert.equal(classify(wire("CREATE", assignmentEdge.id, "SENSOR_ASSIGNMENT", assignmentEdge)), "assignment");
  assert.equal(classify(wire("CREATE", registryActivity.id, "SENSOR_ACTIVITY", registryActivity)), "activity");
});

test("classify: tombstones and DELETE events are removals", () => {
  assert.equal(classify(wire("CREATE", "sensor:s1", "SENSOR_TOMBSTONE", {deleted: true})), "sensor-removed");
  assert.equal(classify(wire("DELETE", "sensor:s1", "SENSOR", {})), "sensor-removed");
  assert.equal(classify(wire("DELETE", "sensor:s1", "sensor", {})), "sensor-removed");   // the graph-event bridge: kind, empty data
  assert.equal(classify(wire("CREATE", assignmentTombstone.entity_id, "SENSOR_ASSIGNMENT_TOMBSTONE", assignmentTombstone)), "assignment-removed");
  assert.equal(classify(wire("DELETE", assignmentEdge.id, "SENSOR_ASSIGNMENT", {})), "assignment-removed");
  assert.equal(classify(wire("DELETE", assignmentEdge.id, "edge", {})), "assignment-removed");
  assert.equal(classify(wire("DELETE", "s1", "SENSOR", {})), "sensor-removed");                // by type when the id has no prefix
  assert.equal(classify(wire("DELETE", "x", "SENSOR_ASSIGNMENT", {})), "assignment-removed");  // likewise
});

test("classify: the bridge's node events (lower-case kind, node data), unrelated events, and non-events are ignored", () => {
  assert.equal(classify(wire("UPDATE", "sensor:s1", "sensor", {id: "sensor:s1", kind: "sensor", position: [1, 2, 3]})), null);
  assert.equal(classify(wire("CREATE", "ENTITY-1", "RECON_ENTITY", {})), null);
  assert.equal(classify(wire("DELETE", "ENTITY-1", "RECON_ENTITY", {})), null);
  assert.equal(classify(wire("DELETE", registryActivity.id, "SENSOR_ACTIVITY", {})), null);   // an activity is never removed
  assert.equal(classify(null), null);
  assert.equal(classify("SENSOR"), null);
  assert.equal(classify({}), null);
});

test("classify: the shape the page modules used to expect is not the wire shape and is not matched", () => {
  assert.equal(classify({entity: {type: "SENSOR"}, event: "CREATE", entity_id: "s1"}), null);
  assert.equal(classify({type: "SENSOR_ACTIVITY"}), null);
});

test("classify: the event type may arrive as eventType, in any case", () => {
  assert.equal(classify({eventType: "delete", entity_id: "sensor:s1", entity_type: "SENSOR"}), "sensor-removed");
});

test("sensorOf: the REST dict as it is, or the registry's canonical sensor, always with a sensor_id", () => {
  assert.deepEqual(sensorOf(wire("CREATE", "sensor:s1", "SENSOR", restSensor)), restSensor);
  assert.equal(sensorOf(wire("CREATE", "sensor:s2", "SENSOR", registrySensor)).label, "Mast");
  assert.equal(sensorOf(wire("CREATE", "sensor:s2", "SENSOR", registrySensor)).sensor_id, "s2");
  assert.equal(sensorOf(wire("CREATE", "sensor:s3", "SENSOR", {label: "No id"})).sensor_id, "s3");   // from the node id
  assert.equal(sensorOf(wire("CREATE", "s4", "SENSOR", {})).sensor_id, "s4");                          // no prefix to strip
  assert.equal(sensorOf(wire("CREATE", "sensor:s5", "SENSOR", undefined)).sensor_id, "s5");
  assert.equal(sensorOf(wire("CREATE", "sensor:s6", "SENSOR", {sensor_id: "kept"})).sensor_id, "kept");
});

test("removedSensorId strips the node prefix", () => {
  assert.equal(removedSensorId(wire("DELETE", "sensor:s1", "sensor", {})), "s1");
  assert.equal(removedSensorId(wire("DELETE", "s1", "sensor", {})), "s1");
});

test("assignmentOf: from the edge's nodes, from a tombstone's from/to, or from the edge id", () => {
  const expected = {sensorId: "s1", entityId: "ENTITY-0001"};
  assert.deepEqual(assignmentOf(wire("CREATE", assignmentEdge.id, "SENSOR_ASSIGNMENT", assignmentEdge)), expected);
  assert.deepEqual(assignmentOf(wire("CREATE", assignmentTombstone.entity_id, "SENSOR_ASSIGNMENT_TOMBSTONE", assignmentTombstone)), expected);
  assert.deepEqual(assignmentOf(wire("DELETE", assignmentEdge.id, "SENSOR_ASSIGNMENT", {})), expected);
  assert.deepEqual(assignmentOf(wire("CREATE", "unparseable", "SENSOR_ASSIGNMENT_TOMBSTONE", {from: "sensor:s1", to: "recon:ENTITY-0001"})), expected);   // from/to, not the id
  assert.deepEqual(assignmentOf(wire("CREATE", "x", "SENSOR_ASSIGNMENT", {nodes: ["recon:R", "other", "sensor:S"]})), {sensorId: "S", entityId: "R"});
});

test("assignmentOf: an event that does not name both ends is null", () => {
  assert.equal(assignmentOf(wire("CREATE", "x", "SENSOR_ASSIGNMENT", {nodes: ["sensor:s1"]})), null);
  assert.equal(assignmentOf(wire("CREATE", "x", "SENSOR_ASSIGNMENT", {nodes: ["recon:r"]})), null);
  assert.equal(assignmentOf(wire("CREATE", "x", "SENSOR_ASSIGNMENT", {})), null);
  assert.equal(assignmentOf(wire("DELETE", "edge:sensor_assigned:sensor:s1", "edge", {})), null);
  assert.equal(assignmentOf(wire("CREATE", "x", "SENSOR_ASSIGNMENT_TOMBSTONE", {from: "sensor:s1"})), null);
});

test("activityOf: the registry's edge form becomes the flat form, payload from metadata", () => {
  const out = activityOf(wire("CREATE", registryActivity.id, "SENSOR_ACTIVITY", registryActivity));
  assert.deepEqual(out, {
    entity_id: registryActivity.id, sensor_id: "s1", activity_type: "aoa_measured", kind: "aoa_measured",
    payload: {bearing_deg: 41, timestamp: 100}, nodes: ["sensor:s1", "recon:ENTITY-0001"], timestamp: 100,
  });
});

test("activityOf: the REST route's flat form keeps its sensor_id and payload; kind is the activity type", () => {
  const out = activityOf(wire("UPDATE", restActivity.activity_id, "SENSOR_ACTIVITY", restActivity));
  assert.equal(out.sensor_id, "s1");
  assert.equal(out.activity_type, "detection");
  assert.deepEqual(out.payload, {snr_db: 12, timestamp: 200});
  assert.equal(out.timestamp, 200);
});

test("activityOf: an explicit activity_type wins over kind; the timestamp falls back to the body's, then the event's", () => {
  assert.equal(activityOf(wire("CREATE", "a", "SENSOR_ACTIVITY", {sensor_id: "s", kind: "k", activity_type: "t"})).activity_type, "t");
  assert.equal(activityOf(wire("CREATE", "a", "SENSOR_ACTIVITY", {sensor_id: "s", payload: {}, timestamp: 7})).timestamp, 7);
  assert.equal(activityOf(wire("CREATE", "a", "SENSOR_ACTIVITY", {sensor_id: "s"}, {timestamp: "evt"})).timestamp, "evt");
});

test("activityOf: a missing payload is empty, a non-object payload is ignored, and no sensor means null", () => {
  assert.deepEqual(activityOf(wire("CREATE", "a", "SENSOR_ACTIVITY", {sensor_id: "s", kind: "k"})).payload, {});
  assert.deepEqual(activityOf(wire("CREATE", "a", "SENSOR_ACTIVITY", {sensor_id: "s", payload: [1]})).payload, {});
  assert.equal(activityOf(wire("CREATE", "a", "SENSOR_ACTIVITY", {kind: "k", nodes: ["recon:r"]})), null);
  assert.equal(activityOf(wire("CREATE", "a", "SENSOR_ACTIVITY", {sensor_id: ""})), null);
  assert.equal(activityOf(wire("CREATE", "a", "SENSOR_ACTIVITY", {})), null);
});

test("activityOf: anything that is not an activity is null", () => {
  assert.equal(activityOf(wire("CREATE", "sensor:s1", "SENSOR", restSensor)), null);
  assert.equal(activityOf(wire("DELETE", registryActivity.id, "SENSOR_ACTIVITY", registryActivity)), null);
  assert.equal(activityOf(undefined), null);
});
