/**
 * What Auto-Recon decides, as plain functions (moved out of `AutoReconVisualization` in command-ops-visualization.html; step 4a of
 * docs/autorecon_extraction_plan.md). Nothing here touches the page, the globe or the network: the page's object calls these through
 * `window.ReconLogic` (set by the boot module) and does the drawing and the fetching itself.
 *
 * The HTML builders (`describeEntity`, `alertListHtml`, `reconRowHtml`) escape every field an entity carries; ids that go inside an inline
 * handler are passed as escaped JSON strings (see #327).
 *
 * Kept as it was, including things that look unintended (see the tests that pin them):
 *  - `normalizeEntityData` makes a shallow copy, so the entity shares its `location` object with the data it was made from, and the
 *    normalising writes (numbers for lat, lon and altitude) land on that object too.
 *  - `planSyncEvent` files a created or updated entity under the id the event carries, which need not equal the `entity_id` in its data.
 *  - An UPDATE for an entity not held creates it; a DELETE for one not held still counts as handled (the lists are refreshed).
 *  - An event type that is not CREATE, PREEXISTING, UPDATE or DELETE is ignored without refreshing anything.
 *
 * Deliberate changes:
 *  - The colour tables and the MLDC priority tables answer only for their own keys: an entity whose disposition is "constructor" gets the
 *    fallback colour, not the text of Object's constructor, and a stream event for an entity called "toString" is a CREATE.
 */

import {escapeHtml} from "./htmlEscape.js";

const DISPOSITION_CSS = {
  UNKNOWN: "#FFFF00", PENDING: "#FFFF00", ASSUMED_FRIEND: "#4444FF", FRIEND: "#0000FF", NEUTRAL: "#00FF00",
  SUSPICIOUS: "#FFA500", HOSTILE: "#FF0000", ELIMINATED: "#888888", JOKER: "#FF00FF", FAKER: "#800080",
};
const THREAT_CSS = {CRITICAL: "#FF0000", HIGH: "#FF4500", MEDIUM: "#FFA500", LOW: "#FFFF00", MINIMAL: "#00FF00", NONE: "#00FFFF"};

const own = (table, key) => (Object.hasOwn(table, key) ? table[key] : undefined);

/** The CSS colour of a disposition; grey for any other. */
export const dispositionCssColor = (disposition) => own(DISPOSITION_CSS, disposition) || "#AAAAAA";

/** The CSS colour of a threat level; grey for any other. */
export const threatCssColor = (threat) => own(THREAT_CSS, threat) || "#AAAAAA";

const idArgument = (id) => escapeHtml(JSON.stringify(String(id)));

/** The entity's description (the infobox and the Info panel): a small HTML card with two buttons. Throws on a missing threat level or disposition, as before. */
export function describeEntity(entity) {
  entity.threat_level.toLowerCase();
  entity.disposition.toLowerCase();
  return `
                    <div style="padding: 10px; font-family: sans-serif;">
                        <h3 style="margin: 0 0 10px 0; color: #00aaff;">🎯 ${escapeHtml(entity.name)}</h3>
                        <table style="width: 100%; border-collapse: collapse;">
                            <tr><td style="padding: 3px; color: #888;">Entity ID:</td><td style="padding: 3px;">${escapeHtml(entity.entity_id)}</td></tr>
                            <tr><td style="padding: 3px; color: #888;">Disposition:</td><td style="padding: 3px; color: ${dispositionCssColor(entity.disposition)};">${escapeHtml(entity.disposition)}</td></tr>
                            <tr><td style="padding: 3px; color: #888;">Threat Level:</td><td style="padding: 3px; color: ${threatCssColor(entity.threat_level)};">${escapeHtml(entity.threat_level)}</td></tr>
                            <tr><td style="padding: 3px; color: #888;">Position:</td><td style="padding: 3px;">${(Number(entity.location?.lat ?? 0)).toFixed(5)}°, ${(Number(entity.location?.lon ?? 0)).toFixed(5)}°</td></tr>
                            <tr><td style="padding: 3px; color: #888;">Distance:</td><td style="padding: 3px;">${(Number(entity.distance_nm ?? 0)).toFixed(2)} NM</td></tr>
                            <tr><td style="padding: 3px; color: #888;">Bearing:</td><td style="padding: 3px;">${(Number(entity.bearing_deg ?? 0)).toFixed(1)}°</td></tr>
                            <tr><td style="padding: 3px; color: #888;">Speed:</td><td style="padding: 3px;">${(entity.velocity?.speed_kts ?? 0).toFixed(1)} kts</td></tr>
                            <tr><td style="padding: 3px; color: #888;">Heading:</td><td style="padding: 3px;">${(entity.velocity?.heading_deg ?? 0).toFixed(1)}°</td></tr>
                            <tr><td style="padding: 3px; color: #888;">Ontology:</td><td style="padding: 3px;">${escapeHtml(entity.ontology)}</td></tr>
                            <tr><td style="padding: 3px; color: #888;">RF Emissions:</td><td style="padding: 3px;">${entity.rf_emissions ? "📡 Detected" : "None"}</td></tr>
                            <tr><td style="padding: 3px; color: #888;">IFF Response:</td><td style="padding: 3px;">${entity.iff_response ? "✅ Valid" : "❌ None"}</td></tr>
                        </table>
                        <div style="margin-top: 10px;">
                            <button onclick="AutoReconVisualization.createTaskForEntity(${idArgument(entity.entity_id)})" 
                                    style="padding: 5px 10px; background: #0066aa; color: white; border: none; border-radius: 3px; cursor: pointer;">
                                📋 Create Task
                            </button>
                            <button onclick="AutoReconVisualization.updateDisposition(${idArgument(entity.entity_id)})" 
                                    style="padding: 5px 10px; background: #aa6600; color: white; border: none; border-radius: 3px; cursor: pointer; margin-left: 5px;">
                                🏷️ Change Disposition
                            </button>
                        </div>
                    </div>
                `;
}

export const NO_ALERTS = '<div class="no-alerts">No active alerts</div>';

/** The alert panel's list; each row flies to its entity when clicked. Throws on an alert without a threat level or numbers, as before. */
export function alertListHtml(alerts) {
  if (alerts.length === 0) return NO_ALERTS;
  let html = "";
  alerts.forEach((alert) => {
    const threatClass = alert.threat_level.toLowerCase();
    html += `
                        <div class="alert-item threat-${escapeHtml(threatClass)}" onclick="AutoReconVisualization.flyToEntity(${idArgument(alert.entity_id)})">
                            <div class="alert-header">
                                <span class="alert-name">${escapeHtml(alert.name)}</span>
                                <span class="alert-threat">${escapeHtml(alert.threat_level)}</span>
                            </div>
                            <div class="alert-details">
                                <span>${escapeHtml(alert.disposition)}</span>
                                <span>${alert.distance_nm.toFixed(1)} NM @ ${alert.bearing_deg.toFixed(0)}°</span>
                            </div>
                        </div>
                    `;
  });
  return html;
}

/** One row of the Recon panel's list (the three buttons carry the id in `data-entity-id`; the page binds their clicks). */
export function reconRowHtml(e) {
  return `
                            <div class="list-item-header">
                                <span class="list-item-title">${escapeHtml(e.name)}</span>
                                <span class="status-dot" style="background-color:${e.disposition === "HOSTILE" ? "#ff4d4d" : "#4a9eff"}"></span>
                            </div>
                            <div class="list-item-subtitle">${escapeHtml(e.disposition)} | ${escapeHtml(e.threat_level)} | ${e.location.lat.toFixed(3)}, ${e.location.lon.toFixed(3)}</div>
                            <div class="list-item-actions">
                                <button class="action-button" data-entity-id="${escapeHtml(e.entity_id)}" data-action="track">Track</button>
                                <button class="action-button" data-entity-id="${escapeHtml(e.entity_id)}" data-action="task">Task</button>
                                <button class="action-button" data-entity-id="${escapeHtml(e.entity_id)}" data-action="info">Info</button>
                            </div>
                        `;
}

/**
 * An entity as the server's full list sends it, ready to keep, or null when it has no numeric location (the caller warns and skips it).
 * Some entities carry their fields nested under `data`; those are lifted to the top, keeping an id.
 */
export function normalizeLoaded(e) {
  let normalized = e;
  if (e.data && typeof e.data === "object") {
    normalized = {...e.data, entity_id: e.data.entity_id || e.id || e.entity_id};
  }
  if (!normalized.location || typeof normalized.location.lat !== "number" || typeof normalized.location.lon !== "number") return null;
  return normalized;
}

/** An entity from a sync event, with every field the page reads filled in. `nowMs` is the clock (for a made-up id and `last_update`). */
export function normalizeEntityData(data, nowMs) {
  const entity = {...data};
  entity.entity_id = entity.entity_id || data.id || `ENTITY-${nowMs}`;
  entity.location = entity.location || {};
  entity.location.lat = Number(entity.location.lat ?? data.lat ?? 0);
  entity.location.lon = Number(entity.location.lon ?? data.lon ?? 0);
  entity.location.altitude_m = Number(entity.location.altitude_m ?? entity.location.alt ?? 0);
  entity.name = entity.name || entity.entity_id;
  entity.disposition = entity.disposition || "UNKNOWN";
  entity.threat_level = entity.threat_level || "NONE";
  entity.velocity = entity.velocity || {speed_kts: 0, heading_deg: 0};
  entity.ontology = entity.ontology || "unknown";
  entity.distance_nm = entity.distance_nm || 0;
  entity.bearing_deg = entity.bearing_deg || 0;
  entity.last_update = entity.last_update || nowMs / 1000;
  return entity;
}

/**
 * What an EntitySync event does to the entity map `entities`:
 *   null                                  nothing: no event, an event for some other kind of entity, or a type it does not handle
 *   {id, kind: "upsert", entity}          keep `entity` under `id`
 *   {id, kind: "delete", existed}         drop `id` (`existed`: it was held)
 * The page applies it, redraws the marker if it is showing, and refreshes the lists.
 */
export function planSyncEvent(evt, entities, nowMs) {
  if (!evt) return null;
  const id = evt.entity_id;
  const type = evt.entity_type;
  const data = evt.entity_data || {};
  const eventType = evt.eventType || evt.event_type || "UPDATE";
  if (type !== "RECON_ENTITY" && type !== "NMAP_TARGET" && !id?.startsWith("AIS-") && !id?.startsWith("ENTITY-")) return null;
  const held = Object.hasOwn(entities, id) ? entities[id] : undefined;
  switch (eventType.toUpperCase()) {
    case "CREATE":
    case "PREEXISTING":
      return {id, kind: "upsert", entity: normalizeEntityData(data, nowMs)};
    case "UPDATE":
      return {id, kind: "upsert", entity: held ? {...held, ...normalizeEntityData(data, nowMs), entity_id: id} : normalizeEntityData(data, nowMs)};
    case "DELETE":
      return {id, kind: "delete", existed: Boolean(held)};
    default:
      return null;
  }
}

/** The sync event for a message on the entity stream, or null for the greeting and for a message with no entity id. */
export function streamEvent(raw, entities) {
  if (raw.type === "connected") return null;
  const entity = raw.entity || raw;
  if (!entity || !entity.entity_id) return null;
  const eventType = raw.type === "entity_deleted" ? "DELETE" : (Object.hasOwn(entities, entity.entity_id) && entities[entity.entity_id] ? "UPDATE" : "CREATE");
  return {event_type: eventType, entity_id: entity.entity_id, entity_type: entity.entity_type || "RECON_ENTITY", entity_data: entity};
}

const THREAT_PRIORITY = {CRITICAL: 0.95, HIGH: 0.85, SUSPICIOUS: 0.70, NONE: 0.30};
const DISPOSITION_PRIORITY = {HOSTILE: 0.90, SUSPICIOUS: 0.75, UNKNOWN: 0.50, FRIENDLY: 0.40, NEUTRAL: 0.35};

/** The label the MLDC text layer shows for an entity: where, how much it matters, how far to trust it, how fresh, how strong its RF. */
export function mldcLabel(entity, nowMs) {
  const disposition = entity.disposition;
  const threatLevel = entity.threat_level || "NONE";
  const priority = own(THREAT_PRIORITY, threatLevel) ?? own(DISPOSITION_PRIORITY, disposition) ?? 0.5;
  const trust = typeof entity.classification_confidence === "number" ? entity.classification_confidence : (entity.confidence ?? 0.8);
  const seenMs = entity.last_seen ? new Date(entity.last_seen).getTime() : nowMs;
  const ageSec = Math.max(0, (nowMs - seenMs) / 1000);
  const recency = Math.exp(-ageSec / 600);   // decays from 1 to 0 over ten minutes of staleness
  const rfConfidence = entity.rf_confidence ?? entity.signal_confidence ?? (entity.rf_emissions ? 0.85 : 0.5);
  const frequencyHz = entity.frequency_hz ?? (entity.rf_emissions?.frequency_mhz ? entity.rf_emissions.frequency_mhz * 1e6 : undefined);
  const color = disposition === "HOSTILE" ? [255, 60, 60] : disposition === "SUSPICIOUS" ? [255, 165, 0] : disposition === "FRIENDLY" ? [0, 220, 100] : [180, 210, 255];
  return {
    id: entity.entity_id, coordinates: [entity.location.lon, entity.location.lat], label: entity.name || entity.entity_id,
    priority, trust, recency, rfConfidence, frequencyHz, color,
  };
}

/** The running average after the `count`th sample (`count` counts the new one). */
export const runningAverage = (average, count, sample) => (average * (count - 1) + sample) / count;
