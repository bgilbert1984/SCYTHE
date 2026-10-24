/**
 * Operator presence as a workspace tool (the twelfth tool on the lifecycle contract in lifecycle.js; moved out of command-ops-visualization.html
 * as `OperatorPresence`).
 *
 * Every 10 seconds, once the page has handed over its viewer with `initialize(viewer)`, it asks the server who is on, lists them in the Operators
 * panel, and marks on the globe where each of the others is looking. The Refresh button asks at once, and the "Show operator positions on map"
 * checkbox turns the markers on and off. Both are inline `onclick` / `onchange` attributes in the page that call `OperatorPresence` by name.
 *
 * What is shown is decided here as plain strings and data (`renderPresenceList`, `indicatorsOf`, `roleIcon`, `roleColorName`); the page is touched
 * only through services: `presenceSource` (the operator session's list of active operators and the current operator), `presenceView` (the list's
 * element) and `presenceGlobe` (the Cesium markers).
 *
 * Kept as it was, including things that look unintended (see the tests that pin them):
 *  - A view whose `center` is not a pair makes the refresh throw after the list is built but before it is drawn, so one malformed operator stops the list
 *    updating; the error is logged.
 *  - Nothing is polled until `initialize` has been called, but the Refresh button works before that.
 *
 * Deliberate changes:
 *  - Unchecking "Show operator positions on map" now takes the markers down, and they stay down while it is unchecked (each refresh keeps the set empty). The
 *    old code returned before touching the markers when the checkbox was off, so unchecking it left them on the map, frozen, until reload.
 *  - Callsigns, roles and ids are escaped where they reach HTML. They come from other operators (a callsign is chosen at registration), and the old
 *    code put them into `innerHTML` as they were.
 *  - Deactivating stops the polling and removes the markers, and a refresh that finishes afterwards does nothing. The old `stopTracking` had no caller.
 *  - The markers are worked out before the old ones are taken down, so a malformed view leaves the previous markers instead of a half-drawn set.
 *  - Gone: the unused `stopTracking` and `startTracking` as public methods, and the `altitude` the old marker code read and never used.
 *
 * Services it needs (see `requires`):
 *   presenceSource  {operators() => Promise<operator[]>, myId() => the current operator's id}
 *   presenceView    {setList(html) => whether the list exists}
 *   presenceGlobe   {attach(viewer), available(), replace(indicators), clear()}: see `workspaceBoot.js`
 */

import {escapeHtml} from "./htmlEscape.js";

export const PRESENCE_ID = "presence";
export const POLL_MS = 10000;

const ROLE_COLORS = {admin: "RED", supervisor: "ORANGE", operator: "CYAN", observer: "GRAY"};
const ROLE_ICONS = {admin: "👑", supervisor: "⭐", operator: "👤", observer: "👁️"};

export const NO_OPERATORS = '<div class="empty-message">No active operators</div>';

/** The Cesium colour a role is drawn in (a name on `Cesium.Color`); white for an unknown role. */
export const roleColorName = (role) => ROLE_COLORS[role] || "WHITE";

/** The icon a role is listed with; the plain operator's for an unknown role. */
export const roleIcon = (role) => ROLE_ICONS[role] || "👤";

/** The Operators panel's list: one row per operator, the current one marked "(You)". */
export function renderPresenceList(operators, myId) {
  if (operators.length === 0) return NO_OPERATORS;
  return operators.map((op) => {
    const me = op.operator_id === myId;
    return `
                        <div class="operator-presence-item ${me ? "current-operator" : ""}" data-operator-id="${escapeHtml(op.operator_id)}">
                            <span class="operator-icon">${roleIcon(op.role)}</span>
                            <span class="operator-callsign">${escapeHtml(op.callsign)}${me ? " (You)" : ""}</span>
                            <span class="operator-role">${escapeHtml(op.role)}</span>
                        </div>
                    `;
  }).join("");
}

/**
 * The markers for the globe: the other operators who have a view with a centre, where they are looking. `center` is `[lat, lon]`.
 * Throws if a centre is not a pair (as the old code did).
 */
export function indicatorsOf(operators, myId) {
  const out = [];
  for (const op of operators) {
    if (!(op.current_view && op.operator_id !== myId)) continue;
    if (!op.current_view.center) continue;
    const [lat, lon] = op.current_view.center;
    out.push({id: op.operator_id, lat, lon, label: `👤 ${op.callsign}`, color: roleColorName(op.role)});
  }
  return out;
}

export function createPresencePlugin({log = console} = {}) {
  let run = null;     // null while the tool is inactive
  let early = null;   // [viewer] handed over while the tool was inactive: used when it activates

  const tool = {
    id: PRESENCE_ID,
    title: "Operator presence",
    requires: ["presenceSource", "presenceView", "presenceGlobe"],
    activate(ctx) {
      const source = ctx.services.get("presenceSource");
      const view = ctx.services.get("presenceView");
      const globe = ctx.services.get("presenceGlobe");
      const stale = () => ctx.signal.aborted;   // deactivated while the server was answering

      let operators = {};   // operator_id -> operator, as of the last refresh
      let visible = true;
      let tracking = false;

      function updateIndicators() {
        if (!globe.available()) return;
        globe.replace(visible ? indicatorsOf(Object.values(operators), source.myId()) : []);
      }

      function updateList() {
        view.setList(renderPresenceList(Object.values(operators), source.myId()));
      }

      async function fetchActiveOperators() {
        try {
          const list = await source.operators();
          if (stale()) return;
          operators = {};
          list.forEach((op) => { operators[op.operator_id] = op; });
          updateIndicators();
          updateList();
        } catch (error) {
          log.error?.("[OperatorPresence] Error fetching operators:", error);
        }
      }

      function initialize(viewer) {
        globe.attach(viewer);
        if (tracking) return;
        tracking = true;
        ctx.interval(() => { fetchActiveOperators(); }, POLL_MS);
        fetchActiveOperators();
      }

      function toggleVisibility(shown) {
        visible = shown;
        updateIndicators();
      }

      ctx.onCleanup(() => { globe.clear(); });   // registered first, so it runs last
      ctx.onCleanup(() => { run = null; });
      run = {operators: () => operators, initialize, fetchActiveOperators, toggleVisibility};
      if (early) { const [viewer] = early; early = null; initialize(viewer); }
    },
  };

  /** What the page calls as `window.OperatorPresence`, from its init code and from the inline handlers in the Operators panel. */
  const controls = {
    initialize(viewer) { if (run) run.initialize(viewer); else early = [viewer]; },
    get operators() { return run ? run.operators() : {}; },
    fetchActiveOperators: () => (run ? run.fetchActiveOperators() : Promise.resolve()),
    toggleVisibility: (visible) => run?.toggleVisibility(visible),
  };

  return {tool, controls};
}
