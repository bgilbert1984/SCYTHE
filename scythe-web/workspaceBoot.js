import {createWorkspace} from "./lifecycle.js";
import {createAdsbPlugin} from "./adsbPlugin.js";
import {createAisStreamPlugin} from "./aisStreamPlugin.js";
import {createChatPlugin} from "./chatPlugin.js";
import {createMetricsPlugin} from "./metricsPlugin.js";
import {createMetricsExplorerPlugin} from "./metricsExplorerPlugin.js";
import {createPropagationPlugin} from "./propagationPlugin.js";
import {createEntitySyncPlugin} from "./entitySyncPlugin.js";
import * as sensorEvents from "./sensorEvents.js";
import {createSensorPlugin} from "./sensorPlugin.js";
import {escapeHtml} from "./htmlEscape.js";

/**
 * Create the workspace for command-ops-visualization.html, provide the page's services, and register its tools. Nothing is
 * activated here: the page decides when (today ADS-B is activated where AISStreamManager starts, so behaviour is unchanged).
 * Services read the page's globals at call time because the renderer and socket are created after this module loads.
 */
export function bootWorkspace(win) {
  const workspace = createWorkspace({log: win.console, timers: {   // read from the window at call time (the tests fake them)
    setInterval: (fn, ms) => win.setInterval(fn, ms), clearInterval: (id) => win.clearInterval(id),
    setTimeout: (fn, ms) => win.setTimeout(fn, ms), clearTimeout: (id) => win.clearTimeout(id),
  }});
  workspace.provide("entityMarkers", {
    upsert: (entity) => win.AutoReconVisualization?.addEntityMarker?.(entity),
    remove: (entityId) => win.AutoReconVisualization?.removeEntityMarker?.(entityId),
  });
  workspace.provide("realtimeSocket", () => win.WebSocketSync?.socket || null);
  workspace.provide("fetchJson", async (url) => {
    const response = await win.fetch(url);
    if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
    return response.json();
  });
  workspace.provide("syncEvents", {
    on: (event, fn) => win.WebSocketSync?.on(event, fn),
    off: (event, fn) => win.WebSocketSync?.off(event, fn),
    get connected() { return Boolean(win.WebSocketSync?.connected); },
  });
  workspace.provide("cameraView", {
    altitude: () => win.viewer?.camera?.positionCartographic?.height ?? null,
    // AISStream format: [[[lon_sw, lat_sw], [lon_ne, lat_ne]]]
    boundingBox() {
      try {
        const rect = win.viewer?.camera?.computeViewRectangle?.();
        if (!rect) return null;
        const deg = win.Cesium.Math.toDegrees;
        return [[[deg(rect.west), deg(rect.south)], [deg(rect.east), deg(rect.north)]]];
      } catch (error) {
        win.console.warn("[AISStream] Could not compute bbox:", error);
        return null;
      }
    },
    onChange(fn) {   // Cesium's Event.addEventListener returns the function that removes the listener
      const changed = win.viewer?.camera?.changed;
      if (!changed) return () => {};
      const remove = changed.addEventListener(fn);
      return typeof remove === "function" ? remove : () => changed.removeEventListener?.(fn);
    },
  });
  const postJson = async (url, body) => {
    const response = await win.fetch(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: body === undefined ? undefined : JSON.stringify(body)});
    if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
    return response.json();
  };
  workspace.provide("postJson", postJson);
  workspace.provide("aisApi", {
    start: (bbox) => postJson("/api/ais/stream/start", {bounding_box: bbox}),
    stop: () => postJson("/api/ais/stream/stop"),
  });
  workspace.provide("vesselSink", {upsert: (vessel) => win.AISStreamManager?.handleVesselUpdate?.(vessel)});
  workspace.provide("notify", (text) => win.addConsoleMessage?.(text, "response"));
  workspace.register(createAdsbPlugin({log: win.console}));
  const ais = createAisStreamPlugin({log: win.console});
  workspace.register(ais.tool);
  win.ScytheAisStream = ais.controls;   // what the page's AIS toggle calls

  // --- chat ---
  workspace.provide("openEventSource", (url) => new win.EventSource(url));   // read at call time: the page wraps EventSource for instance scoping
  workspace.provide("chatView", chatView(win));
  workspace.provide("roomChanges", roomChanges(win));
  const chat = createChatPlugin({log: win.console});
  workspace.register(chat.tool);
  win.ScytheChat = chat.controls;   // what the page's LiveChat calls

  // --- metrics ---
  workspace.provide("httpFetch", (url, init) => win.fetch(url, init));   // read at call time: the page patches fetch for instance scoping
  workspace.provide("metricsProbe", metricsProbe(win));
  const metrics = createMetricsPlugin({log: win.console});
  workspace.register(metrics.tool);
  win.metricsLogger = metrics.controls;   // the Metrics Explorer reads _interactionLog and _lastMetrics from it
  win.logMetric = (module, name, value, meta) => metrics.controls.log(module, name, value, meta);
  win.logInteraction = (action, target, details) => metrics.controls.logInteraction(action, target, details);

  // --- metrics explorer ---
  workspace.provide("explorerView", explorerView(win));
  workspace.provide("liveSources", liveSources(win));
  const explorer = createMetricsExplorerPlugin({log: win.console});
  workspace.register(explorer.tool);
  win.MetricsExplorer = explorer.controls;   // what showPanel('metrics') calls

  // --- propagation analysis ---
  workspace.provide("propagationEngine", propagationEngine(win));
  workspace.provide("propagationView", propagationView(win));
  workspace.provide("activityLog", {   // the metrics tool's loggers, if the page has them
    metric: (module, name, value) => win.logMetric?.(module, name, value),
    interaction: (action, target) => win.logInteraction?.(action, target),
  });
  const propagation = createPropagationPlugin({log: win.console});
  workspace.register(propagation.tool);
  win.PropagationUI = propagation.controls;   // what showPanel('propagation') calls

  // --- entity stream ---
  workspace.provide("operatorSession", {token: () => win.OperatorSession?.sessionToken || null, clear: () => win.OperatorSession?.clearSession?.()});
  workspace.provide("entityConsole", {
    info: (text) => win.addConsoleMessage?.(text, "response"),
    alert: (text) => win.addConsoleMessage?.(text, "alert"),
  });
  const entitySync = createEntitySyncPlugin({log: win.console});
  workspace.register(entitySync.tool);
  adoptEntitySyncStub(win, entitySync.controls);
  win.ScytheSensorEvents = sensorEvents;   // how LPIManager and FusionVisualization read the stream's sensor events

  // --- sensors ---
  workspace.provide("reconEntities", () => win.AutoReconVisualization?.entities || {});
  workspace.provide("entityStream", {on: (event, handler) => win.EntitySync?.on(event, handler), off: (event, handler) => win.EntitySync?.off(event, handler)});
  workspace.provide("sensorView", sensorView(win));
  workspace.provide("sensorGlobe", sensorGlobe(win));
  const sensors = createSensorPlugin({log: win.console});
  workspace.register(sensors.tool);
  win.SensorManager = sensors.controls;   // what the page's other modules read and call
  win.ScytheWorkspace = workspace;
  win.dispatchEvent(new win.Event("scythe:workspace_ready"));
  return workspace;
}

/** The chat panel's DOM, by the ids the page gives it. Rendering a message stays with the page's LiveChat.addMsg. */
function chatView(win) {
  const byId = (id) => win.document.getElementById(id);
  const saveCallsign = (text) => { try { win.localStorage.setItem("rpChatCallsign", text); } catch (error) { /* storage blocked */ } };
  return {
    showConnecting(room) {
      const label = byId("lc-room-label");
      if (label) label.textContent = room;
      const feed = byId("lc-feed");
      if (feed) feed.innerHTML = '<div style="color:#383838;font-size:10px;text-align:center;padding:8px;">Connecting…</div>';
    },
    hideConnecting() { byId("lc-feed")?.querySelector('div[style*="Connecting"]')?.remove(); },
    addMessage: (message) => win.LiveChat?.addMsg?.(message),
    callsign: () => (byId("lc-callsign")?.value || "").trim(),
    messageText: () => (byId("lc-msg-input")?.value || "").trim(),
    clearMessage() { const input = byId("lc-msg-input"); if (input) input.value = ""; },
    saveCallsign,
    restoreCallsign() {
      let stored = null;
      try { stored = win.localStorage.getItem("rpChatCallsign"); } catch (error) { /* storage blocked */ }
      const input = byId("lc-callsign");
      if (input && stored) input.value = stored;
    },
    bind({send, callsignChanged}) {
      const sendButton = byId("lc-send-btn"), messageInput = byId("lc-msg-input"), callsignInput = byId("lc-callsign");
      const onKey = (event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); send(); } };
      sendButton?.addEventListener("click", send);
      messageInput?.addEventListener("keydown", onKey);
      callsignInput?.addEventListener("change", callsignChanged);
      return () => {
        sendButton?.removeEventListener("click", send);
        messageInput?.removeEventListener("keydown", onKey);
        callsignInput?.removeEventListener("change", callsignChanged);
      };
    },
  };
}

/** RoomManager's current-room changes. RoomManager may not exist yet, so look for it (300 ms, then every 500 ms) until it
 *  does or the subscription is cancelled; the old code retried forever and never unhooked. */
function roomChanges(win) {
  return {
    subscribe(fn) {
      let timer = null, attached = false;
      const handler = (change) => { if (change && change.roomId) fn(change.roomId); };
      const attach = () => {
        timer = null;
        const manager = win.RoomManager;
        if (manager && manager.on) { manager.on("current_room_changed", handler); attached = true; }
        else timer = win.setTimeout(attach, 500);
      };
      timer = win.setTimeout(attach, 300);
      return () => {
        if (timer !== null) { win.clearTimeout(timer); timer = null; }
        if (attached) win.RoomManager?.off?.("current_room_changed", handler);
      };
    },
  };
}

/** What the metrics tool reads from the page: the renderers' counters and the browser's memory. Missing pieces are skipped. */
function metricsProbe(win) {
  const gauge = (module, name, value) => ({module, name, value});
  return {
    unloadTarget: win,
    environment: () => ({userAgent: win.navigator.userAgent, screenWidth: win.innerWidth, screenHeight: win.innerHeight}),
    *gauges({performance, hypergraph}) {
      if (performance) {
        const recon = win.reconViz;
        if (recon && recon.perfMetrics) {
          yield gauge("recon", "update_count", recon.perfMetrics.updateCount);
          yield gauge("recon", "last_update_ms", recon.perfMetrics.lastUpdateMs);
          yield gauge("recon", "avg_update_ms", recon.perfMetrics.avgUpdateMs);
          const entities = recon.entities;   // a plain object or a Cesium EntityCollection
          yield gauge("recon", "entity_count", entities?.values ? entities.values.length : Object.keys(entities || {}).length);
        }
        if (win.aisViz && win.aisViz.vessels) yield gauge("ais", "vessel_count", Object.keys(win.aisViz.vessels).length);
        if (win.viewer && win.viewer.scene) {
          yield gauge("cesium", "entity_count", win.viewer.entities.values.length);
          let height = null;
          try { height = win.Cesium.Cartographic.fromCartesian(win.viewer.camera.position).height; } catch (error) { /* no camera position yet */ }
          if (height !== null) yield gauge("cesium", "camera_altitude_m", height);
        }
        if (win.hierarchicalRenderer) yield gauge("render", "current_altitude", win.hierarchicalRenderer.currentAltitude || 0);
        if (win.viewshedManager) yield gauge("viewshed", "active_count", Object.keys(win.viewshedManager.viewsheds).length);
      }
      if (hypergraph && win.hypergraphVisualizer) {
        const graph = win.hypergraphVisualizer;
        if (graph.nodes) yield gauge("hypergraph", "node_count", graph.nodes.length);
        if (graph.edges) yield gauge("hypergraph", "edge_count", graph.edges.length);
      }
      const memory = win.performance && win.performance.memory;
      if (memory) {
        yield gauge("browser", "heap_used_mb", memory.usedJSHeapSize / (1024 * 1024));
        yield gauge("browser", "heap_total_mb", memory.totalJSHeapSize / (1024 * 1024));
      }
    },
  };
}

/** The Metrics Explorer panel's DOM, by the ids the page gives it. Every lookup happens when it is needed: the panel may not exist yet. */
function explorerView(win) {
  const byId = (id) => win.document.getElementById(id);
  const NAME_FILTER = "metrics-filter-name";
  return {
    setText(id, text) { const element = byId(id); if (element) element.textContent = text; },
    setHtml(id, html) { const element = byId(id); if (!element) return false; element.innerHTML = html; return true; },
    filters: () => ({
      module: byId("metrics-filter-module")?.value || "",
      name: byId(NAME_FILTER)?.value || "",
      limit: byId("metrics-filter-limit")?.value || "50",
    }),
    /** Rebuild the metric-name filter from the server's names, keeping the current choice if it is still there. */
    setMetricNames(names) {
      const select = byId(NAME_FILTER);
      if (!select) return;
      const current = select.value;
      select.innerHTML = '<option value="">All Metrics</option>';
      for (const name of names) {
        const option = win.document.createElement("option");
        option.value = name;
        option.textContent = name;
        if (name === current) option.selected = true;
        select.appendChild(option);
      }
    },
    panelActive: () => Boolean(byId("metrics-panel")?.classList.contains("active")),
    bind({refresh, filtersChanged}) {
      const onRefresh = () => refresh(), onFilter = () => filtersChanged();
      const button = byId("refresh-metrics-btn");
      const filters = ["metrics-filter-module", NAME_FILTER, "metrics-filter-limit"].map(byId);
      button?.addEventListener("click", onRefresh);
      for (const filter of filters) filter?.addEventListener("change", onFilter);
      return () => {
        button?.removeEventListener("click", onRefresh);
        for (const filter of filters) filter?.removeEventListener("change", onFilter);
      };
    },
  };
}

/** What the explorer shows beyond the server's answers: the metrics tool's in-memory logs, and the page's own counters. */
function liveSources(win) {
  return {
    interactionLog: () => win.metricsLogger?._interactionLog || null,
    lastMetrics: () => win.metricsLogger?._lastMetrics || null,
    recon: () => win.RF_SCYTHE?.AutoReconVisualization || null,
    heapBytes: () => (win.performance?.memory ? win.performance.memory.usedJSHeapSize : null),
  };
}

/** RFPropagationManager, looked up when used: the page creates it before this module runs, but a failed script would leave it out. */
function propagationEngine(win) {
  const manager = () => win.RFPropagationManager;
  return {
    available: () => Boolean(manager()),
    atmosphere: () => ({conditions: manager().atmosphericConditions, classification: manager().classifyAtmosphere()}),
    setAtmosphere: (conditions) => manager().setAtmosphericConditions(conditions),
    calculate: (tx, rx) => manager().calculatePathLoss(tx, rx),
    visualize: (id, tx, rx) => manager().visualizePropagationPath(id, tx, rx),
    removePath: (id) => manager().removePropagationPath(id),
    clearAll: () => manager().clearAllPaths(),
  };
}

/** The Propagation panel's DOM, by the ids the page gives it. Every lookup happens when it is needed; a missing element is skipped. */
function propagationView(win) {
  const byId = (id) => win.document.getElementById(id);
  const text = (id) => byId(id)?.value ?? "";
  const place = (prefix) => (names) => Object.fromEntries(Object.entries(names).map(([key, id]) => [key, text(`${prefix}-${id}`)]));
  const tx = place("tx"), rx = place("rx");
  return {
    setText(id, value) { const element = byId(id); if (element) element.textContent = value; },
    setHtml(id, html) { const element = byId(id); if (!element) return false; element.innerHTML = html; return true; },
    setColor(id, color) { const element = byId(id); if (element) element.style.color = color; },
    atmosphereInputs: () => ({humidity: text("atm-humidity"), temperature: text("atm-temp"), gradient: text("atm-gradient")}),
    pathInputs: () => ({
      tx: tx({lat: "lat", lon: "lon", height: "height", frequency: "freq", power: "power"}),
      rx: rx({lat: "lat", lon: "lon", height: "height", sensitivity: "sensitivity"}),
    }),
    /** The sliders show their value as they move (no tool logic); the four buttons call back. */
    bind({apply, calculate, visualize, clear}) {
      const bound = [];
      const on = (id, type, handler) => {
        const element = byId(id);
        if (!element) return;
        element.addEventListener(type, handler);
        bound.push(() => element.removeEventListener(type, handler));
      };
      const label = (id, unit) => (event) => { const shown = byId(id); if (shown) shown.textContent = event.target.value + unit; };
      on("atm-humidity", "input", label("atm-humidity-val", "%"));
      on("atm-temp", "input", label("atm-temp-val", "°C"));
      on("apply-atmosphere-btn", "click", () => apply());
      on("calculate-path-btn", "click", () => calculate());
      on("visualize-path-btn", "click", () => visualize());
      on("clear-paths-btn", "click", () => clear());
      return () => { for (const off of bound) off(); };
    },
  };
}

/**
 * `window.EntitySync` is a stub from the moment the page's scripts run, so modules that subscribe while the page loads (before this
 * module has run) are not lost. The real controls replace it here, and what the stub kept is replayed: the subscriptions, and a
 * `connect` (which the tool makes once it is active).
 */
function adoptEntitySyncStub(win, controls) {
  const stub = win.EntitySync;
  win.EntitySync = controls;
  if (!stub || !stub._stub) return;
  for (const [event, handler] of stub._queued) controls.on(event, handler);
  if (stub._pendingConnect) controls.connect(...stub._pendingConnect);
}

/** The Sensors panel's DOM, by the ids the page gives it. Every lookup happens when it is needed; a missing element is skipped. */
function sensorView(win) {
  const byId = (id) => win.document.getElementById(id);
  const FORM_INPUTS = ["sensor-label", "sensor-tx-freq", "sensor-rx-freq", "sensor-tx-power", "sensor-rx-sens", "sensor-lat", "sensor-lon"];
  let dialog = null, dialogDone = null;
  const closeDialog = () => {
    const open = dialog;
    dialog = null;
    if (open && open.parentNode) open.parentNode.removeChild(open);
  };
  return {
    hasList: () => Boolean(byId("sensorList")),
    setList(html) { const list = byId("sensorList"); if (list) list.innerHTML = html; },
    setCount(count) { const element = byId("sensors-panel-count"); if (element) element.textContent = count; },
    /** The create form's entity choice: "-- None --" and then one option per entity. */
    setEntityOptions(options) {
      const select = byId("sensor-assign-entity");
      if (!select) return;
      select.innerHTML = '<option value="">-- None --</option>';
      for (const {id, label} of options) {
        const option = win.document.createElement("option");
        option.value = id;
        option.textContent = label;
        select.appendChild(option);
      }
    },
    readForm: () => ({
      label: byId("sensor-label")?.value || "", type: byId("sensor-type")?.value || "",
      txFreq: byId("sensor-tx-freq")?.value, rxFreq: byId("sensor-rx-freq")?.value, txPower: byId("sensor-tx-power")?.value,
      rxSens: byId("sensor-rx-sens")?.value, lat: byId("sensor-lat")?.value, lon: byId("sensor-lon")?.value,
      assignEntity: byId("sensor-assign-entity")?.value || null,
    }),
    hideForm() { const form = byId("sensor-create-form"); if (form) form.style.display = "none"; },
    clearForm() { for (const id of FORM_INPUTS) { const input = byId(id); if (input) input.value = ""; } },
    confirm: (text) => win.confirm(text),
    /** A modal choice of one entity; `onConfirm(entityId)` may be async, and the dialog stays until it finishes. */
    showAssignDialog({title, options, onConfirm}) {
      closeDialog();
      const element = win.document.createElement("div");
      element.className = "sensor-assign-dialog";
      element.style.cssText = "position:fixed;left:50%;top:50%;transform:translate(-50%,-50%);background:rgba(20,20,20,0.98);padding:16px;border-radius:8px;border:1px solid #444;z-index:99999;min-width:300px;";
      const optionsHtml = options.map(({id, label}) => `<option value="${escapeHtml(id)}">${escapeHtml(label)}</option>`).join("");
      element.innerHTML = `
                    <h4 style="margin:0 0 12px 0;color:#4a9eff;">${escapeHtml(title)}</h4>
                    <div style="margin-bottom:12px;">
                        <label style="font-size:11px;color:#888;">Select Recon Entity</label>
                        <select id="assign-entity-select" style="width:100%;padding:8px;background:#222;border:1px solid #444;border-radius:4px;color:#fff;">
                            ${optionsHtml}
                        </select>
                    </div>
                    <div style="display:flex;gap:8px;justify-content:flex-end;">
                        <button class="action-button assign-cancel-btn" style="background:#444;">Cancel</button>
                        <button class="action-button assign-confirm-btn" style="background:#4a9eff;">Assign</button>
                    </div>
                `;
      win.document.body.appendChild(element);
      dialog = element;
      element.querySelector(".assign-cancel-btn").addEventListener("click", () => { if (dialog === element) closeDialog(); });
      element.querySelector(".assign-confirm-btn").addEventListener("click", async () => {
        const entityId = element.querySelector("#assign-entity-select").value;
        try { await onConfirm(entityId); } finally { if (dialog === element) closeDialog(); }
      });
    },
    /** The panel's buttons: load, the create form (open, cancel, save), and one listener on the list for its buttons. */
    bind({load, openForm, save, track, assign, remove, moveGroup}) {
      const bound = [];
      const on = (element, type, handler) => {
        if (!element) return;
        element.addEventListener(type, handler);
        bound.push(() => element.removeEventListener(type, handler));
      };
      const form = byId("sensor-create-form");
      on(byId("sensorLoadBtn"), "click", () => load());
      if (form) {
        on(byId("sensorCreateBtn"), "click", () => { form.style.display = form.style.display === "none" ? "block" : "none"; openForm(); });
        on(byId("sensor-cancel-btn"), "click", () => { form.style.display = "none"; });
      }
      on(byId("sensor-save-btn"), "click", () => save());
      on(byId("sensorList"), "click", (event) => {
        const within = (selector) => event.target?.closest?.(selector);
        const move = within(".group-move-btn");
        if (move) { event.stopPropagation(); moveGroup(move.getAttribute("data-group"), move.getAttribute("data-action")); return; }
        const track_ = within(".sensor-track-btn"), assign_ = within(".sensor-assign-btn"), remove_ = within(".sensor-delete-btn");
        if (track_) track(track_.getAttribute("data-sensor-id"));
        else if (assign_) assign(assign_.getAttribute("data-sensor-id"));
        else if (remove_) remove(remove_.getAttribute("data-sensor-id"));
      });
      return () => { for (const off of bound) off(); closeDialog(); };
    },
  };
}

/** The sensors' markers and assignment lines on the Cesium globe, and its camera. Nothing happens while the page has no viewer. */
function sensorGlobe(win) {
  const markers = new Map();   // sensor id -> Cesium entity
  const ASSIGNMENT_PREFIX = "sensor-assignment-";
  const removeAssignmentLines = () => {
    win.viewer.entities.values.filter((e) => e.id && e.id.startsWith(ASSIGNMENT_PREFIX)).forEach((e) => win.viewer.entities.remove(e));
  };
  const removeMarker = (id) => {
    const marker = markers.get(id);
    if (!marker) return;
    if (win.viewer) win.viewer.entities.remove(marker);
    markers.delete(id);
  };
  return {
    available: () => Boolean(win.viewer),
    removeMarker,
    /** Remove the markers of sensors that are gone (`knownIds`); move the markers that exist; add the rest. */
    syncMarkers(list, knownIds) {
      const {Cesium, viewer} = win;
      for (const id of [...markers.keys()]) if (!knownIds.includes(id)) removeMarker(id);
      const colors = {SDR: Cesium.Color.CYAN, RADAR: Cesium.Color.ORANGE, CAMERA: Cesium.Color.YELLOW, ACOUSTIC: Cesium.Color.PURPLE, SIGINT: Cesium.Color.LIME};
      for (const m of list) {
        const position = Cesium.Cartesian3.fromDegrees(m.lon, m.lat, 100);
        if (markers.has(m.id)) { markers.get(m.id).position = position; continue; }
        const color = colors[m.type] || Cesium.Color.CYAN;
        markers.set(m.id, viewer.entities.add({
          id: `sensor-${m.id}`, position,
          point: {pixelSize: 14, color, outlineColor: Cesium.Color.WHITE, outlineWidth: 2},
          label: {
            text: m.label, font: "11px sans-serif", style: Cesium.LabelStyle.FILL_AND_OUTLINE, outlineWidth: 2,
            verticalOrigin: Cesium.VerticalOrigin.BOTTOM, pixelOffset: new Cesium.Cartesian2(0, -18), fillColor: color, outlineColor: Cesium.Color.BLACK,
          },
          description: m.description,
        }));
      }
    },
    /** Replace the dashed line from each sensor to the entity it is assigned to; points under a metre apart get none. */
    drawAssignments(links) {
      const {Cesium, viewer} = win;
      removeAssignmentLines();
      for (const link of links) {
        const entityPosition = Cesium.Cartesian3.fromDegrees(link.entity.lon, link.entity.lat, 100);
        const sensorPosition = Cesium.Cartesian3.fromDegrees(link.sensor.lon, link.sensor.lat, 100);
        if (Cesium.Cartesian3.distance(entityPosition, sensorPosition) < 1.0) continue;
        viewer.entities.add({
          id: `${ASSIGNMENT_PREFIX}${link.sensorId}-${link.entityId}`,
          polyline: {
            positions: [sensorPosition, entityPosition], width: 2, arcType: Cesium.ArcType.GEODESIC,
            material: new Cesium.PolylineDashMaterialProperty({color: Cesium.Color.CYAN.withAlpha(0.6), dashLength: 16}),
          },
        });
      }
    },
    flyTo({lat, lon, altitude}) {
      const {Cesium} = win;
      const options = {
        destination: Cesium.Cartesian3.fromDegrees(lon, lat, altitude), duration: 1.5,
        orientation: {heading: Cesium.Math.toRadians(0), pitch: Cesium.Math.toRadians(-45), roll: 0.0},
      };
      if (typeof win.flyToWithReset === "function") win.flyToWithReset(options);
      else win.viewer.camera.flyTo(options);
    },
    /** Take down everything this drew. */
    clear() {
      for (const id of [...markers.keys()]) removeMarker(id);
      if (win.viewer) removeAssignmentLines();
    },
  };
}
