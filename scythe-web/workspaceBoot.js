import {createWorkspace} from "./lifecycle.js";
import {createAdsbPlugin} from "./adsbPlugin.js";

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
  workspace.register(createAdsbPlugin({log: win.console}));
  win.ScytheWorkspace = workspace;
  win.dispatchEvent(new win.Event("scythe:workspace_ready"));
  return workspace;
}
