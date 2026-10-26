/**
 * cesium-entity-event-adapter.js — maps the server's Socket.IO "entity_event" payload
 * (operator_session_manager.py: EntityEvent.to_dict()) onto the update records the globe's
 * _queueUpdate()/_applyUpdates() pipeline consumes.
 *
 * Pure and browser-independent, like cesium-mission-filter.js, so it is unit-tested with
 * node --test. Fixes #201: the globe used to listen for "entity_update"/"entity_delete",
 * which nothing emits.
 *
 * entity_event payload: {event_type, entity_id, entity_type, entity_data, provenance, ...}
 * with event_type one of PREEXISTING/CREATE/UPDATE/DELETE (rendered) or HEARTBEAT, ROOM_ and
 * OPERATOR_ prefixed types (not entity changes). Node records carry position as [lat, lon, alt]
 * (rf_scythe_api_server.py add_node), or flat lat/lon.
 *
 * Returns {type:'node_update', ...} | {type:'node_remove', id} | null (nothing to render).
 * Edges and hyperedges arrive on the same event and are deliberately ignored: they have their
 * own channel ('edges') in the globe.
 */
'use strict';

const NODE_EVENTS = new Set(['PREEXISTING', 'CREATE', 'UPDATE']);

function isEdgeLike(entityType, data) {
  const t = String(entityType || '').toLowerCase();
  if (t.includes('edge')) return true;
  return Array.isArray(data.nodes) || (data.src != null && data.dst != null);
}

function coordsOf(data) {
  let lat = data.lat ?? data.latitude;
  let lon = data.lon ?? data.longitude;
  if ((lat == null || lon == null) && Array.isArray(data.position) && data.position.length >= 2) {
    [lat, lon] = data.position;
  }
  lat = Number(lat);
  lon = Number(lon);
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
  // [0,0,0] is the server's "no position" placeholder; plotting it would pile nodes on Null Island.
  if (lat === 0 && lon === 0) return null;
  return { lat, lon };
}

/**
 * @param {object} ev - the entity_event payload.
 * @returns {object|null}
 */
function entityEventToUpdate(ev) {
  if (!ev || typeof ev !== 'object' || !ev.entity_id) return null;
  const data = (ev.entity_data && typeof ev.entity_data === 'object') ? ev.entity_data : {};
  if (isEdgeLike(ev.entity_type, data)) return null;

  if (ev.event_type === 'DELETE') {
    return { type: 'node_remove', id: ev.entity_id };
  }
  if (!NODE_EVENTS.has(ev.event_type)) return null;

  const pos = coordsOf(data);
  if (!pos) return null;
  return {
    ...data,
    type: 'node_update',
    entity_id: ev.entity_id,
    entity_type: ev.entity_type,
    lat: pos.lat,
    lon: pos.lon,
  };
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { entityEventToUpdate };
} else {
  window.entityEventToUpdate = entityEventToUpdate;
}
