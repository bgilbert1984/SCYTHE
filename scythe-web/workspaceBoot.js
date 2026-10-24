import {createWorkspace} from "./lifecycle.js";
import {createAdsbPlugin} from "./adsbPlugin.js";
import {createAisStreamPlugin} from "./aisStreamPlugin.js";
import {createChatPlugin} from "./chatPlugin.js";

/**
 * Create the workspace for command-ops-visualization.html, provide the page's services, and register its tools. Nothing is
 * activated here: the page decides when (today ADS-B is activated where AISStreamManager starts, so behaviour is unchanged).
 * Services read the page's globals at call time because the renderer and socket are created after this module loads.
 */
export function bootWorkspace(win) {
  const workspace = createWorkspace({log: win.console});
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
  workspace.provide("timers", {
    setInterval: (fn, ms) => win.setInterval(fn, ms), clearInterval: (id) => win.clearInterval(id),
    setTimeout: (fn, ms) => win.setTimeout(fn, ms), clearTimeout: (id) => win.clearTimeout(id),
  });
  workspace.provide("chatView", chatView(win));
  workspace.provide("roomChanges", roomChanges(win));
  const chat = createChatPlugin({log: win.console});
  workspace.register(chat.tool);
  win.ScytheChat = chat.controls;   // what the page's LiveChat calls
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
