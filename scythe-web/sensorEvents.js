/**
 * Sensor events as the entity stream delivers them, read in one place.
 *
 * Every event the stream delivers (server-sent events and the WebSocket path alike) is `EntityEvent.to_dict()` in
 * operator_session_manager.py: `{event_type, entity_id, entity_type, entity_data, provenance, timestamp, sequence_id}`. The three page
 * modules that listen for sensor events (SensorManager, LPIManager, FusionVisualization) were written against a different shape
 * (`event.entity.type`, `event.event`, `entity.type`), so none of their checks ever matched. These functions read the real one.
 *
 * What the server puts in `entity_data`, by `entity_type` (an event is classified by its top-level `entity_type`, compared exactly: the
 * graph-event bridge also broadcasts the same writes with a lower-case node kind such as `sensor` and node data, which is not the
 * entity and is ignored):
 *   SENSOR                      the sensor. Three producers: the REST route (the sensor dict itself, with `sensor_id`), and
 *                               sensor_registry.upsert_sensor (`{id, type, sensor: {..., sensor_id}, node, timestamp}`).
 *   SENSOR_TOMBSTONE            `{entity_id: "sensor:<id>", deleted: true, ...}` after a sensor is deleted.
 *   SENSOR_ASSIGNMENT           an edge `{id: "edge:sensor_assigned:sensor:<s>->recon:<r>", kind: "sensor_assigned", nodes: ["sensor:<s>", "recon:<r>"]}`.
 *   SENSOR_ASSIGNMENT_TOMBSTONE `{entity_id: <edge id>, deleted: true, from: "sensor:<s>", to: "recon:<r>"}`.
 *   SENSOR_ACTIVITY             two shapes: sensor_registry.emit_activity (an edge `{id, kind, nodes, metadata: {payload}}`) and the REST
 *                               route (flat: `{activity_id, kind, sensor_id, nodes, payload, ...}`).
 * A DELETE event from the graph-event bridge has an empty `entity_data`; a sensor or an assignment is recognised by its entity id.
 */

const SENSOR = "sensor:";
const RECON = "recon:";
const ASSIGNMENT_EDGE = "edge:sensor_assigned:";

const strip = (id, prefix) => (String(id).startsWith(prefix) ? String(id).slice(prefix.length) : String(id));
const isObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
const bodyOf = (wire) => (isObject(wire?.entity_data) ? wire.entity_data : {});
const idOf = (wire) => (wire?.entity_id === undefined || wire?.entity_id === null ? "" : String(wire.entity_id));
const isDelete = (wire) => String(wire?.event_type ?? wire?.eventType ?? "").toUpperCase() === "DELETE";

/** What an event is about: "sensor", "sensor-removed", "assignment", "assignment-removed", "activity", or null for anything else. */
export function classify(wire) {
  if (!isObject(wire)) return null;
  const type = wire.entity_type;
  const id = idOf(wire);
  const removed = isDelete(wire);
  if (type === "SENSOR_TOMBSTONE" || (removed && (type === "SENSOR" || id.startsWith(SENSOR)))) return "sensor-removed";
  if (type === "SENSOR_ASSIGNMENT_TOMBSTONE" || (removed && (type === "SENSOR_ASSIGNMENT" || id.startsWith(ASSIGNMENT_EDGE)))) return "assignment-removed";
  if (removed) return null;
  if (type === "SENSOR") return "sensor";
  if (type === "SENSOR_ASSIGNMENT") return "assignment";
  if (type === "SENSOR_ACTIVITY") return "activity";
  return null;
}

/** The sensor in a "sensor" event, with `sensor_id` set (from the sensor, else from the node id). */
export function sensorOf(wire) {
  const body = bodyOf(wire);
  const sensor = isObject(body.sensor) ? body.sensor : body;
  return {...sensor, sensor_id: sensor.sensor_id ?? strip(idOf(wire), SENSOR)};
}

/** The id of the sensor a "sensor-removed" event is about. */
export const removedSensorId = (wire) => strip(idOf(wire), SENSOR);

/** `{sensorId, entityId}` of an assignment or its removal, or null if the event does not say both. */
export function assignmentOf(wire) {
  const body = bodyOf(wire);
  let sensorNode, reconNode;
  if (Array.isArray(body.nodes)) {
    sensorNode = body.nodes.find((n) => String(n).startsWith(SENSOR));
    reconNode = body.nodes.find((n) => String(n).startsWith(RECON));
  } else if (body.from !== undefined || body.to !== undefined) {
    sensorNode = body.from;
    reconNode = body.to;
  } else if (idOf(wire).startsWith(ASSIGNMENT_EDGE)) {
    [sensorNode, reconNode] = idOf(wire).slice(ASSIGNMENT_EDGE.length).split("->");
  }
  if (!sensorNode || !reconNode) return null;
  return {sensorId: strip(sensorNode, SENSOR), entityId: strip(reconNode, RECON)};
}

/**
 * A "activity" event in the flat form LPIManager and FusionVisualization read:
 * `{entity_id, sensor_id, activity_type, kind, payload, nodes, timestamp}`, or null if it is not an activity or names no sensor.
 */
export function activityOf(wire) {
  if (classify(wire) !== "activity") return null;
  const body = bodyOf(wire);
  const nodes = Array.isArray(body.nodes) ? body.nodes : [];
  const sensorNode = nodes.find((n) => String(n).startsWith(SENSOR));
  const sensorId = body.sensor_id ?? (sensorNode === undefined ? undefined : strip(sensorNode, SENSOR));
  if (sensorId === undefined || sensorId === null || sensorId === "") return null;
  const payload = isObject(body.payload) ? body.payload : isObject(body.metadata?.payload) ? body.metadata.payload : {};
  return {
    entity_id: idOf(wire), sensor_id: String(sensorId), activity_type: body.activity_type ?? body.kind, kind: body.kind, payload, nodes,
    timestamp: payload.timestamp ?? body.timestamp ?? wire.timestamp,
  };
}
