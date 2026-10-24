import {createWorkspace} from "./lifecycle.js";
import {createAdsbPlugin} from "./adsbPlugin.js";
import {createAisStreamPlugin} from "./aisStreamPlugin.js";

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
  win.ScytheWorkspace = workspace;
  win.dispatchEvent(new win.Event("scythe:workspace_ready"));
  return workspace;
}
