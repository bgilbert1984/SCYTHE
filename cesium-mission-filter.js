/**
 * cesium-mission-filter.js — pure Mission-matching logic for the Cesium globe client.
 *
 * Deliberately standalone and browser-independent: no DOM, no Cesium, no Socket.IO. This is
 * step 1 of docs/research/scythe_mission_browser_rendering.md section 5.1 ("client-side
 * filtering against the existing stream, no server change") -- and ONLY step 1. It is not
 * wired into cesium-hypergraph-globe.js's rendering pipeline here.
 *
 * UPDATE (#201): the globe now listens for "entity_event" through cesium-entity-event-adapter.js,
 * which flattens entity_data. The event-name gap below is closed; wiring this filter in is still
 * deferred until it can be verified in a running browser.
 *
 * Why not wired in yet, stated plainly rather than worked around: cesium-hypergraph-globe.js
 * listens for Socket.IO events named "entity_update"/"entity_delete". The only server-side
 * emitter found anywhere in this repository (operator_session_manager.py, the
 * `self.websocket.emit('entity_event', event.to_dict())` call) emits a DIFFERENT event name
 * ("entity_event"), with a different payload shape (generic node data nested under
 * entity_data, not flattened to top-level lat/lon/etc. the way the client's handler expects).
 * No emitter for "entity_update" exists anywhere in this codebase. That generic-node rendering
 * path is therefore not demonstrably live today -- a pre-existing gap, independent of Mission
 * work, recorded in docs/research/scythe_mission_browser_rendering.md section 9 rather than
 * quietly worked around here. Wiring this module into that pipeline is deferred until it can be
 * verified end-to-end against a running browser, which this environment cannot do.
 *
 * Matching rules mirror the Python-side helpers this already has two independent
 * implementations of (graphops_graph_resolver.py's and rf_scythe_api_server.py's
 * _label_matches_mission): a node's mission_id/missionId label, under either labels or
 * metadata, matches a requested mission_id if it equals it (the scalar form -- most node
 * kinds) or contains it (the list form -- host nodes, which may be observed under several
 * missions; docs/research/scythe_mission_browser_rendering.md section 6). Exact
 * equality/membership only: no partial match, no mission-hierarchy expansion.
 */

'use strict';

/**
 * @param {*} value - a labels/metadata mission_id (or legacy missionId) value: a string, an
 *   array of strings, or absent (undefined/null).
 * @param {string} missionId - the mission being matched against. Must be truthy; this
 *   function does not define "no filter" behavior -- see shouldRenderNode() for that policy.
 * @returns {boolean}
 */
function valueMatchesMission(value, missionId) {
  if (Array.isArray(value)) {
    return value.includes(missionId);
  }
  return value === missionId;
}

/**
 * Checks a node-like object's mission attribution against one mission_id, across both the
 * canonical (mission_id) and legacy (missionId) key, and both the labels and metadata
 * sub-objects -- the same four-location check order used by rf_scythe_api_server.py's
 * mission_pred/_mission_filter_node and graphops_graph_resolver.py's resolver filter.
 *
 * @param {object} node - expected shape: { labels?: object, metadata?: object, ... }. Any
 *   missing/non-object labels or metadata is treated as empty, not an error.
 * @param {string} missionId - must be truthy (see shouldRenderNode() for the "no filter" case).
 * @returns {boolean}
 */
function matchesMission(node, missionId) {
  if (!missionId) {
    throw new Error('matchesMission requires a truthy missionId; use shouldRenderNode() for "no filter selected" semantics');
  }
  const labels = (node && typeof node.labels === 'object' && node.labels) || {};
  const metadata = (node && typeof node.metadata === 'object' && node.metadata) || {};
  return (
    valueMatchesMission(labels.mission_id, missionId) ||
    valueMatchesMission(labels.missionId, missionId) ||
    valueMatchesMission(metadata.mission_id, missionId) ||
    valueMatchesMission(metadata.missionId, missionId)
  );
}

/**
 * The actual render-decision policy: "no mission selected" shows everything (unchanged
 * behavior, matching every server-side mission_id filter in this project, which all treat an
 * empty/falsy mission_id as "no filter" rather than "match nothing"). Only when a mission IS
 * selected does matchesMission() get consulted.
 *
 * @param {object} node
 * @param {string|null|undefined} selectedMissionId - falsy means no filter is active.
 * @returns {boolean}
 */
function shouldRenderNode(node, selectedMissionId) {
  if (!selectedMissionId) {
    return true;
  }
  return matchesMission(node, selectedMissionId);
}

// Same dual-export convention as unified-render-scheduler.js: CommonJS for node --test,
// window globals for the plain <script> tag this file would eventually be loaded with.
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { valueMatchesMission, matchesMission, shouldRenderNode };
} else {
  window.valueMatchesMission = valueMatchesMission;
  window.matchesMission      = matchesMission;
  window.shouldRenderNode    = shouldRenderNode;
}
