import {createWorkspace} from "./lifecycle.js";
import {createAdsbPlugin} from "./adsbPlugin.js";
import {createAisStreamPlugin} from "./aisStreamPlugin.js";
import {createChatPlugin} from "./chatPlugin.js";
import {createMetricsPlugin} from "./metricsPlugin.js";
import {createMetricsExplorerPlugin} from "./metricsExplorerPlugin.js";

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
