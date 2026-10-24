/**
 * The Sensors panel as a workspace tool (the eighth tool on the lifecycle contract in lifecycle.js; moved out of
 * command-ops-visualization.html as `SensorManager`).
 *
 * While active it keeps the sensors and their assignments to recon entities, loads and edits them through the REST API, applies the
 * sensor events the entity stream delivers (read by sensorEvents.js), draws the panel's list, and keeps a marker per sensor and a line
 * per assignment on the globe. Deactivating removes the listeners and everything drawn, and a request that finishes afterwards does
 * nothing.
 *
 * What the panel shows is decided here as plain strings and plain data (`render*`, `markerOf`, `assignmentLinks`, `flightTarget`), and
 * the page is touched only through services: `sensorView` (the panel's DOM), `sensorGlobe` (the Cesium markers, lines and camera).
 *
 * Kept as it was, including things that look unintended (see the tests that pin them):
 *  - The create form turns a 0 into "none" (`parseFloat(x) || null`), so a latitude or longitude of exactly 0, or a frequency of 0, cannot be
 *    entered; a sensor needs both coordinates to have a position.
 *  - A sensor's entity-position check uses `||` (an entity at latitude 0 reads as having none) while a sensor's uses `??`.
 *  - An existing marker only has its position updated: a changed label or type does not reach it.
 *  - Deleting a sensor removes its marker but does not redraw the assignment lines, so a line to it stays until the next redraw.
 *  - Moving a group swaps it with its neighbour in the full order, which includes groups that are not shown.
 *  - `flyTo` reports "Tracking ..." even when there is no globe, and then reports that it could not determine a position.
 *  - Loading replaces the sensors but not the assignments.
 *  - `unassign` and `emitActivity` have no caller on the page (the server routes exist); kept, exposed on the controls.
 *
 * Deliberate changes:
 *  - Every sensor, entity and error text that reaches HTML (the list, the group headers, the assign dialog, the globe's description
 *    balloon) is escaped. Sensors now arrive from other operators over the entity stream, and the old code put their labels into
 *    innerHTML as they came.
 *  - The list's buttons are handled by one listener on the list instead of being wired again after every redraw.
 *  - The stream subscription is removed on deactivate (the old one never was), and a request or dialog result that arrives afterwards
 *    is dropped.
 *  - Sensors and assignments start empty on each activation.
 *
 * Services it needs (see `requires`):
 *   httpFetch       (url, init?) => Promise<Response>
 *   operatorSession {token() => string|null}
 *   entityConsole   {info(text), alert(text)}
 *   reconEntities   () => the Auto-Recon entities by id (each may have `name` and `location`)
 *   entityStream    {on(event, handler), off(event, handler)}: the entity stream's handlers
 *   sensorView      the panel's DOM: see `workspaceBoot.js`
 *   sensorGlobe     the globe: see `workspaceBoot.js`
 */

import {classify, sensorOf, removedSensorId, assignmentOf} from "./sensorEvents.js";
import {escapeHtml} from "./htmlEscape.js";

export const SENSORS_ID = "sensors";
const STREAM_EVENTS = ["preexisting", "create", "update", "delete"];
const ENTITY_ALTITUDE_M = 2000;
const SENSOR_ALTITUDE_M = 10000;

export const NO_SENSORS = '<li class="list-item no-entities" style="color:#888; font-size:12px;">No sensors loaded</li>';

/** The sensor as the panel and the globe read it: a label, `position` as {lat, lon, alt_m}, and tx/rx configs. */
export function normalizeSensor(sensor) {
  const s = {...(sensor || {})};
  s.label = s.label || s.name || s.sensor_id;
  if (Array.isArray(s.position)) {
    s.position = {lat: s.position[0], lon: s.position[1], alt_m: s.position[2] || 0};
  } else if (!s.position && s.location) {
    s.position = {lat: s.location.lat, lon: s.location.lon ?? s.location.lng, alt_m: s.location.alt_m ?? s.location.alt ?? 0};
  }
  const firstBand = (radio) => radio?.bands_mhz?.[0]?.[0] ?? null;
  s.tx_config = s.tx_config || {frequency_mhz: firstBand(s.tx), power_dbm: s.tx?.max_eirp_dbm ?? null};
  s.rx_config = s.rx_config || {frequency_mhz: firstBand(s.rx), sensitivity_dbm: s.rx?.sensitivity_dbm ?? null, sample_rate_hz: s.rx?.sample_rate_hz ?? null};
  return s;
}

/** A sensor's {lat, lon}, or null if it has none (`??`: a latitude of 0 counts). */
export function positionOf(sensor) {
  if (!sensor || !sensor.position) return null;
  const lat = parseFloat(sensor.position.lat ?? sensor.position.latitude);
  const lon = parseFloat(sensor.position.lon ?? sensor.position.longitude);
  return Number.isNaN(lat) || Number.isNaN(lon) ? null : {lat, lon};
}

/** An entity's {lat, lon}, or null (`||`: a latitude or longitude of exactly 0 falls through to `latitude` / `longitude`). */
export function entityPositionOf(entity) {
  if (!entity || !entity.location) return null;
  const lat = parseFloat(entity.location.lat || entity.location.latitude);
  const lon = parseFloat(entity.location.lon || entity.location.longitude);
  return Number.isNaN(lat) || Number.isNaN(lon) ? null : {lat, lon};
}

/**
 * The list's structure: sensors grouped under the entity they are assigned to (a sensor assigned to several shows under the last), the
 * rest unassigned, and the display order of groups: `order` with any new group appended, filtered to the groups present.
 */
export function groupSensors(sensors, assignments, order) {
  const sensorToEntity = {};
  for (const entityId of Object.keys(assignments)) for (const sid of assignments[entityId]) sensorToEntity[sid] = entityId;
  const groups = {};
  const unassigned = [];
  for (const id of Object.keys(sensors)) {
    const entityId = sensorToEntity[id];
    if (entityId) (groups[entityId] ||= []).push(sensors[id]);
    else unassigned.push(sensors[id]);
  }
  const present = Object.keys(groups);
  const nextOrder = [...order, ...present.filter((id) => !order.includes(id))];
  return {groups, unassigned, order: nextOrder, shown: nextOrder.filter((id) => present.includes(id))};
}

/** The group order after moving `groupId` up or down one place (a swap with its neighbour). */
export function moveGroup(order, groupId, action) {
  const index = order.indexOf(groupId);
  if (index === -1) return order;
  const next = [...order];
  if (action === "up" && index > 0) [next[index - 1], next[index]] = [next[index], next[index - 1]];
  else if (action === "down" && index < next.length - 1) [next[index + 1], next[index]] = [next[index], next[index + 1]];
  return next;
}

const textOr = (value, fallback) => escapeHtml(value || fallback);

export function renderSensorItem(sensor) {
  const id = escapeHtml(sensor.sensor_id);
  const statusColor = sensor.status === "ACTIVE" ? "#55ff55" : sensor.status === "OFFLINE" ? "#888" : "#4a9eff";
  const statusText = escapeHtml(sensor.status || "UNKNOWN");
  const txFreq = sensor.tx_config?.frequency_mhz ? `${escapeHtml(sensor.tx_config.frequency_mhz)} MHz` : "—";
  const rxFreq = sensor.rx_config?.frequency_mhz ? `${escapeHtml(sensor.rx_config.frequency_mhz)} MHz` : "—";
  const txPower = sensor.tx_config?.power_dbm ? `${escapeHtml(sensor.tx_config.power_dbm)} dBm` : "—";
  const bandwidth = sensor.rx_config?.bandwidth_mhz ? `${escapeHtml(sensor.rx_config.bandwidth_mhz)} MHz` : "—";
  const gain = sensor.rx_config?.gain_db ? `${escapeHtml(sensor.rx_config.gain_db)} dB` : "—";
  return `<li class="list-item signal-card" data-sensor-id="${id}">
                    <div class="list-item-header">
                        <span class="list-item-title">${textOr(sensor.label, sensor.sensor_id)}</span>
                        <span style="background-color: ${statusColor}; color: #000; padding: 2px 6px; border-radius: 3px; font-size: 11px; font-weight: bold;">${statusText}</span>
                    </div>

                    <div class="feature-value" style="display: flex; justify-content: space-between; margin-bottom: 2px; font-size: 11px;">
                        <span class="feature-name" style="color: #aaa;">Type:</span>
                        <span>${textOr(sensor.type, "SDR")}</span>
                    </div>

                    ${sensor.tx_config ? `
                    <div class="feature-value" style="display: flex; justify-content: space-between; margin-bottom: 2px; font-size: 11px;">
                        <span class="feature-name" style="color: #aaa;">Tx Freq/Pwr:</span>
                        <span>${txFreq} / ${txPower}</span>
                    </div>` : ""}

                    ${sensor.rx_config ? `
                    <div class="feature-value" style="display: flex; justify-content: space-between; margin-bottom: 2px; font-size: 11px;">
                        <span class="feature-name" style="color: #aaa;">Rx Freq/BW:</span>
                        <span>${rxFreq} / ${bandwidth}</span>
                    </div>` : ""}

                    <div class="feature-value" style="display: flex; justify-content: space-between; margin-bottom: 5px; font-size: 11px;">
                        <span class="feature-name" style="color: #aaa;">Gain:</span>
                        <span>${gain}</span>
                    </div>

                    <div class="confidence-bar">
                         <div class="confidence-level" style="width: 100%; background-color: ${statusColor}; opacity: 0.5;"></div>
                    </div>

                    <div class="list-item-actions">
                        <button class="action-button sensor-track-btn" data-sensor-id="${id}">Track</button>
                        <button class="action-button sensor-assign-btn" data-sensor-id="${id}">Assign</button>
                        <button class="action-button sensor-delete-btn" data-sensor-id="${id}" style="background:#a33;">Delete</button>
                    </div>
                </li>`;
}

const MOVE_BUTTON = "cursor: pointer; background: rgba(255,255,255,0.1); border: 1px solid rgba(255,255,255,0.2); color: #fff; width: 24px; height: 24px; display:flex; align-items:center; justify-content:center; border-radius:3px; margin-left:4px;";

export function renderGroupHeader(entityName, entityId, index, count) {
  const group = escapeHtml(entityId);
  const first = index === 0, last = index === count - 1;
  return `<li class="sensor-group-header" style="background: linear-gradient(90deg, #2a2a40, #1a1a20); padding: 8px 10px; margin-top: 15px; margin-bottom: 8px; border-radius: 4px; font-weight: bold; color: #add8e6; display: flex; justify-content: space-between; align-items: center; border-left: 3px solid #4a9eff; box-shadow: 0 2px 4px rgba(0,0,0,0.2);">
                        <span><span style="color:#888; font-size:11px; margin-right:5px;">TASK FORCE:</span>${escapeHtml(entityName)}</span>
                        <div class="group-controls" style="display:flex;">
                            <button class="group-move-btn" data-action="up" data-group="${group}" ${first ? "disabled" : ""} style="${MOVE_BUTTON} opacity:${first ? 0.3 : 1};">▲</button>
                            <button class="group-move-btn" data-action="down" data-group="${group}" ${last ? "disabled" : ""} style="${MOVE_BUTTON} opacity:${last ? 0.3 : 1};">▼</button>
                        </div>
                     </li>`;
}

export const UNASSIGNED_HEADER = '<li class="sensor-group-header" style="background: #222; padding: 6px 10px; margin-top: 20px; margin-bottom: 8px; border-radius: 4px; font-weight: bold; color: #888; border-bottom: 1px dashed #444; font-size: 12px; text-transform: uppercase; letter-spacing: 1px;">Unassigned Sensors</li>';

/** The list's HTML: assigned groups (headed by the entity's name and id when it is known), then the unassigned sensors. */
export function renderSensorList(structure, reconEntities) {
  const {groups, unassigned, shown} = structure;
  const parts = [];
  shown.forEach((entityId, index) => {
    const entity = reconEntities[entityId];
    parts.push(renderGroupHeader(entity ? `${entity.name} (${entityId})` : entityId, entityId, index, shown.length));
    for (const sensor of groups[entityId]) parts.push(renderSensorItem(sensor));
  });
  if (unassigned.length > 0) {
    if (shown.length > 0) parts.push(UNASSIGNED_HEADER);
    for (const sensor of unassigned) parts.push(renderSensorItem(sensor));
  }
  return parts.join("");
}

/** What the globe needs to draw a sensor's marker (null if it has no usable position). */
export function markerOf(sensor) {
  const position = positionOf(sensor);
  if (!position) return null;
  return {
    id: sensor.sensor_id, label: sensor.label || sensor.sensor_id, type: sensor.type, ...position,
    description: `<div><strong>${escapeHtml(sensor.label || sensor.sensor_id)}</strong><br>Type: ${escapeHtml(sensor.type || "SDR")}<br>Tx: ${escapeHtml(sensor.tx_config?.frequency_mhz || "—")} MHz<br>Rx: ${escapeHtml(sensor.rx_config?.frequency_mhz || "—")} MHz</div>`,
  };
}

/** The assignment lines to draw: each sensor assigned to an entity, when both ends have a position. */
export function assignmentLinks(assignments, sensors, reconEntities) {
  const links = [];
  for (const entityId of Object.keys(assignments)) {
    const sensorIds = assignments[entityId];
    if (!sensorIds || sensorIds.length === 0) continue;
    const entity = entityPositionOf(reconEntities[entityId]);
    if (!entity) continue;
    for (const sid of sensorIds) {
      const sensor = positionOf(sensors[sid]);
      if (sensor) links.push({sensorId: sid, entityId, sensor, entity});
    }
  }
  return links;
}

/**
 * Where to fly for a sensor: the entity it is assigned to when that has a position, else the sensor. `messages` are what to tell the
 * console; `position` is null when neither has one.
 */
export function flightTarget(sensorId, sensor, assignments, reconEntities) {
  const messages = [];
  let label = sensor.label || sensorId;
  let position = null;
  const assigned = Object.entries(assignments).find(([, sids]) => sids.includes(sensorId));
  if (assigned) {
    const [entityId] = assigned;
    const entity = reconEntities[entityId];
    const where = entityPositionOf(entity);
    if (where) {
      position = {...where, altitude: ENTITY_ALTITUDE_M};
      label = `Entity ${entity.name || entityId}`;
      messages.push(`Tracking assigned entity: ${label}`);
    }
  }
  if (!position) {
    const where = positionOf(sensor);
    if (where) {
      position = {...where, altitude: SENSOR_ALTITUDE_M};
      messages.push(`Tracking sensor: ${label}`);
    }
  }
  return {position, label, messages};
}

export function createSensorPlugin({log = console} = {}) {
  let run = null;   // null while the tool is inactive

  const tool = {
    id: SENSORS_ID,
    title: "Sensors",
    requires: ["httpFetch", "operatorSession", "entityConsole", "reconEntities", "entityStream", "sensorView", "sensorGlobe"],
    activate(ctx) {
      const http = ctx.services.get("httpFetch");
      const session = ctx.services.get("operatorSession");
      const out = ctx.services.get("entityConsole");
      const recon = ctx.services.get("reconEntities");
      const stream = ctx.services.get("entityStream");
      const view = ctx.services.get("sensorView");
      const globe = ctx.services.get("sensorGlobe");
      const stale = () => ctx.signal.aborted;   // deactivated while a request or a dialog was open

      let sensors = {};        // sensor_id -> normalized sensor
      let assignments = {};    // entity_id -> [sensor_id, ...]
      let groupOrder = [];     // entity ids, in the order the list shows their groups

      const headers = () => {
        const token = session.token();
        return token ? {"Content-Type": "application/json", "X-Session-Token": token} : {"Content-Type": "application/json"};
      };

      function updateUI() {
        if (!view.hasList()) return;
        const ids = Object.keys(sensors);
        view.setCount(ids.length);
        if (ids.length === 0) { view.setList(NO_SENSORS); return; }
        const structure = groupSensors(sensors, assignments, groupOrder);
        groupOrder = structure.order;
        view.setList(renderSensorList(structure, recon()));
      }

      function removeMarker(sensorId) { globe.removeMarker(sensorId); }

      function visualize() {
        if (!globe.available()) return;
        const markers = [];
        for (const id of Object.keys(sensors)) {
          const marker = markerOf(sensors[id]);
          if (marker) markers.push(marker);
        }
        globe.syncMarkers(markers, Object.keys(sensors));
        globe.drawAssignments(assignmentLinks(assignments, sensors, recon()));
      }

      const forgetAssignments = (sensorId) => {
        for (const entityId of Object.keys(assignments)) assignments[entityId] = assignments[entityId].filter((sid) => sid !== sensorId);
      };

      async function load() {
        try {
          const response = await http("/api/sensors", {headers: headers()});
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const data = await response.json();
          if (stale()) return;
          sensors = {};
          for (const sensor of data.sensors || []) {
            const normalized = normalizeSensor(sensor);
            sensors[normalized.sensor_id] = normalized;
          }
          updateUI();
          visualize();
          out.info(`Loaded ${Object.keys(sensors).length} sensors`);
        } catch (error) {
          log.error?.("[SensorManager] Load error:", error);
          if (!stale()) out.alert(`Error loading sensors: ${error.message}`);
        }
      }

      async function upsert(sensorData) {
        try {
          const response = await http("/api/sensors", {method: "POST", headers: headers(), body: JSON.stringify(sensorData)});
          if (!response.ok) {
            const failure = await response.json();
            throw new Error(failure.error || `HTTP ${response.status}`);
          }
          const data = await response.json();
          if (stale()) return undefined;
          const normalized = normalizeSensor(data.sensor);
          sensors[normalized.sensor_id] = normalized;
          updateUI();
          visualize();
          out.info(`Sensor ${normalized.label} ${data.created ? "created" : "updated"}`);
          return normalized;
        } catch (error) {
          log.error?.("[SensorManager] Upsert error:", error);
          if (!stale()) out.alert(`Error saving sensor: ${error.message}`);
          throw error;
        }
      }

      async function deleteSensor(sensorId) {
        try {
          const response = await http(`/api/sensors/${sensorId}`, {method: "DELETE", headers: headers()});
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          if (stale()) return;
          delete sensors[sensorId];
          forgetAssignments(sensorId);
          updateUI();
          removeMarker(sensorId);
          out.info(`Sensor ${sensorId} deleted`);
        } catch (error) {
          log.error?.("[SensorManager] Delete error:", error);
          if (!stale()) out.alert(`Error deleting sensor: ${error.message}`);
        }
      }

      async function assign(sensorId, entityId) {
        try {
          const response = await http(`/api/recon/entity/${entityId}/assign_sensor`, {method: "POST", headers: headers(), body: JSON.stringify({sensor_id: sensorId})});
          if (!response.ok) {
            const failure = await response.json();
            throw new Error((failure && (failure.error || failure.message)) || `HTTP ${response.status}`);
          }
          if (stale()) return;
          if (!assignments[entityId]) assignments[entityId] = [];
          if (!assignments[entityId].includes(sensorId)) assignments[entityId].push(sensorId);
          updateUI();
          visualize();
          out.info(`Sensor ${sensorId} assigned to entity ${entityId}`);
        } catch (error) {
          log.error?.("[SensorManager] Assign error:", error);
          if (!stale()) out.alert(`Error assigning sensor: ${error.message}`);
        }
      }

      async function unassign(sensorId, entityId) {
        try {
          const response = await http(`/api/sensors/${sensorId}/unassign/${entityId}`, {method: "DELETE", headers: headers()});
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          if (stale()) return;
          if (assignments[entityId]) assignments[entityId] = assignments[entityId].filter((sid) => sid !== sensorId);
          updateUI();
          visualize();
          out.info(`Sensor ${sensorId} unassigned from entity ${entityId}`);
        } catch (error) {
          log.error?.("[SensorManager] Unassign error:", error);
          if (!stale()) out.alert(`Error unassigning sensor: ${error.message}`);
        }
      }

      async function emitActivity(sensorId, activityType, payload) {
        try {
          const response = await http(`/api/sensors/${sensorId}/activity`, {method: "POST", headers: headers(), body: JSON.stringify({activity_type: activityType, payload})});
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const data = await response.json();
          if (stale()) return undefined;
          out.info(`Sensor activity: ${activityType} from ${sensorId}`);
          return data;
        } catch (error) {
          log.error?.("[SensorManager] Activity error:", error);
          if (!stale()) out.alert(`Error emitting sensor activity: ${error.message}`);
          return undefined;
        }
      }

      function flyTo(sensorId) {
        const sensor = sensors[sensorId];
        if (!sensor) return;
        const target = flightTarget(sensorId, sensor, assignments, recon());
        for (const message of target.messages) out.info(message);
        if (target.position && globe.available()) globe.flyTo(target.position);
        else out.alert(`Could not determine position for ${target.label}`);
      }

      function populateEntityDropdown() {
        const entities = recon();
        view.setEntityOptions(Object.keys(entities).map((id) => ({id, label: entities[id].name || id})));
      }

      function assignDialog(sensorId) {
        const sensor = sensors[sensorId];
        if (!sensor) return;
        const entities = recon();
        const ids = Object.keys(entities);
        if (ids.length === 0) { out.alert("No recon entities available to assign sensor to"); return; }
        view.showAssignDialog({
          title: `Assign Sensor: ${sensor.label || sensorId}`,
          options: ids.map((id) => ({id, label: entities[id].name || id})),
          onConfirm: async (entityId) => { if (entityId && !stale()) await assign(sensorId, entityId); },
        });
      }

      function moveGroupBy(groupId, action) {
        if (groupOrder.indexOf(groupId) === -1) return;
        groupOrder = moveGroup(groupOrder, groupId, action);
        updateUI();
      }

      async function saveForm() {
        const raw = view.readForm();
        const number = (text) => parseFloat(text) || null;
        const label = raw.label || "";
        if (!label) { out.alert("Please enter a sensor label"); return; }
        const txFreq = number(raw.txFreq), rxFreq = number(raw.rxFreq), txPower = number(raw.txPower), rxSens = number(raw.rxSens);
        const lat = number(raw.lat), lon = number(raw.lon);
        const sensorData = {
          label, type: raw.type || "SDR",
          tx_config: txFreq ? {frequency_mhz: txFreq, power_dbm: txPower} : null,
          rx_config: rxFreq ? {frequency_mhz: rxFreq, sensitivity_dbm: rxSens} : null,
          position: lat !== null && lon !== null ? {lat, lon} : null,
        };
        try {
          const sensor = await upsert(sensorData);   // undefined if we were deactivated meanwhile
          if (raw.assignEntity && sensor) await assign(sensor.sensor_id, raw.assignEntity);
          if (stale()) return;
          view.hideForm();
          view.clearForm();
        } catch (error) {
          // already reported by upsert
        }
      }

      function applyEvent(event) {
        const kind = classify(event);
        if (kind === "sensor") {
          const sensor = normalizeSensor(sensorOf(event));
          sensors[sensor.sensor_id] = sensor;
          updateUI();
          visualize();
        } else if (kind === "sensor-removed") {
          const sensorId = removedSensorId(event);
          delete sensors[sensorId];
          forgetAssignments(sensorId);
          removeMarker(sensorId);
          updateUI();
          visualize();
        } else if (kind === "assignment" || kind === "assignment-removed") {
          const pair = assignmentOf(event);
          if (!pair) return;
          const {sensorId, entityId} = pair;
          if (kind === "assignment-removed") {
            if (assignments[entityId]) assignments[entityId] = assignments[entityId].filter((sid) => sid !== sensorId);
          } else {
            if (!assignments[entityId]) assignments[entityId] = [];
            if (!assignments[entityId].includes(sensorId)) assignments[entityId].push(sensorId);
          }
          updateUI();
          visualize();
        }
      }

      const onStreamEvent = ctx.guard(applyEvent);   // an event that is not about a sensor or an assignment is ignored there
      for (const type of STREAM_EVENTS) {
        stream.on(type, onStreamEvent);
        ctx.onCleanup(() => stream.off(type, onStreamEvent));
      }

      ctx.onCleanup(() => { globe.clear(); });   // registered first, so it runs last
      ctx.onCleanup(view.bind({
        load: () => load(),
        openForm: () => populateEntityDropdown(),
        save: () => saveForm(),
        track: (sensorId) => flyTo(sensorId),
        assign: (sensorId) => assignDialog(sensorId),
        remove: (sensorId) => { if (view.confirm(`Delete sensor ${sensorId}?`)) deleteSensor(sensorId); },
        moveGroup: (groupId, action) => moveGroupBy(groupId, action),
      }));
      ctx.onCleanup(() => { run = null; });
      run = {
        sensors: () => sensors, assignments: () => assignments,
        load, flyTo, populateEntityDropdown, unassign, emitActivity,
      };
    },
  };

  /** What the page reads and calls as `window.SensorManager`. The data is live; the calls do nothing while the tool is inactive. */
  const controls = {
    get sensors() { return run ? run.sensors() : {}; },
    get assignments() { return run ? run.assignments() : {}; },
    load: () => run?.load(),
    flyTo: (sensorId) => run?.flyTo(sensorId),
    populateEntityDropdown: () => run?.populateEntityDropdown(),
    unassign: (sensorId, entityId) => run?.unassign(sensorId, entityId),
    emitActivity: (sensorId, activityType, payload) => run?.emitActivity(sensorId, activityType, payload),
  };

  return {tool, controls};
}
