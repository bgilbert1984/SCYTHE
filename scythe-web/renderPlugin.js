/**
 * Level of detail and clustering as a workspace tool (the thirteenth tool on the lifecycle contract in lifecycle.js; moved out of
 * command-ops-visualization.html as `HierarchicalRenderManager`).
 *
 * The page hands over its viewer with `initialize(viewer)`. From then on the camera's altitude decides a detail level (GLOBAL, REGIONAL,
 * LOCAL, DETAILED), every camera change announces it as an `altitudeChanged` event on the document, and the layers ask `shouldShowDetail(type)`
 * whether labels, rings, trails, altitude lines or hyperedges should show at that level. The AIS and recon layers also ask for a clustered
 * data source (`createClusteredDataSource`), which the manager adds to the viewer.
 *
 * What is decided is plain data and functions here (`detailLevel`, `shouldShowDetail`, `clusterSettings`); the page is touched only through
 * two services: `renderGlobe` (the viewer: camera altitude and change events, data sources) and `renderEvents` (the `altitudeChanged` event).
 *
 * Kept as it was, including things that look unintended (see the tests that pin them):
 *  - Before `initialize` the altitude is 0, which is DETAILED, so `shouldShowDetail` answers true for everything.
 *  - A level is chosen with `>` (exactly 5000000 m is REGIONAL, not GLOBAL).
 *  - The initial altitude is read at `initialize` but not announced; only a camera change announces.
 *  - A layer's `pixelRange` or `minimumClusterSize` of 0 gives the default (`||`); `enabled` and the three `cluster*` flags are on unless they are
 *    exactly `false`.
 *  - The `altitudeChanged` event carries `{altitude, level}`.
 *
 * Deliberate changes:
 *  - Gone, after a search of the repository found nothing that used them: the quadtree (`SpatialQuadtree`, `createQuadtree`, `getVisibleEntities`;
 *    the AIS and recon layers built one and never read it), `setupClusterStyling` and `createClusterIcon` (both layers have their own),
 *    `getAdaptivePixelRange`, `THRESHOLDS` and `CLUSTER_CONFIG` as page properties, and `getDetailLevel` as a public method.
 *  - Deactivating unsubscribes from the camera and removes every data source this created (the old `destroy` had no caller); calling
 *    `initialize` again replaces the camera subscription instead of adding a second.
 *  - While the tool is inactive `viewer` is null, `createClusteredDataSource` returns null and `updateVisibilityForAltitude` does nothing.
 *
 * Services it needs (see `requires`):
 *   renderGlobe   {attach(viewer), available(), viewer(), altitude(), onCameraChanged(fn) => unsubscribe, addDataSource(name, clustering), removeDataSource(source)}
 *   renderEvents  {altitudeChanged({altitude, level})}: see `workspaceBoot.js`
 */

export const RENDER_ID = "render";

/** Altitude thresholds in meters: above each is the level named. */
export const THRESHOLDS = Object.freeze({GLOBAL: 5000000, REGIONAL: 500000, LOCAL: 50000, DETAILED: 10000});

export const CLUSTER_DEFAULTS = Object.freeze({
  enabled: true, pixelRange: 45, minimumClusterSize: 2, clusterBillboards: true, clusterLabels: true, clusterPoints: true,
});

/** The detail level for a camera altitude in meters. */
export function detailLevel(altitude) {
  if (altitude > THRESHOLDS.GLOBAL) return "GLOBAL";
  if (altitude > THRESHOLDS.REGIONAL) return "REGIONAL";
  if (altitude > THRESHOLDS.LOCAL) return "LOCAL";
  return "DETAILED";
}

/** Whether a kind of detail shows at an altitude. An unknown kind always shows. */
export function shouldShowDetail(altitude, detailType) {
  const level = detailLevel(altitude);
  switch (detailType) {
    case "labels": return level === "LOCAL" || level === "DETAILED";
    case "rings":
    case "trails": return level === "DETAILED";
    case "altitudeLines": return level === "LOCAL" || level === "DETAILED";
    case "hyperedges": return level !== "GLOBAL";
    default: return true;
  }
}

/** The clustering a layer asked for, with the defaults for what it left out. */
export function clusterSettings(options = {}) {
  return {
    enabled: options.enabled !== false,
    pixelRange: options.pixelRange || CLUSTER_DEFAULTS.pixelRange,
    minimumClusterSize: options.minimumClusterSize || CLUSTER_DEFAULTS.minimumClusterSize,
    clusterBillboards: options.clusterBillboards !== false,
    clusterLabels: options.clusterLabels !== false,
    clusterPoints: options.clusterPoints !== false,
  };
}

export function createRenderPlugin({log = console} = {}) {
  let run = null;     // null while the tool is inactive
  let early = null;   // [viewer] handed over while the tool was inactive: used when it activates

  const tool = {
    id: RENDER_ID,
    title: "Level of detail",
    requires: ["renderGlobe", "renderEvents"],
    activate(ctx) {
      const globe = ctx.services.get("renderGlobe");
      const events = ctx.services.get("renderEvents");
      let altitude = 0;
      let unsubscribe = null;
      const sources = [];

      function updateVisibilityForAltitude() {
        events.altitudeChanged({altitude, level: detailLevel(altitude)});
      }

      function initialize(viewer) {
        globe.attach(viewer);
        if (unsubscribe) { unsubscribe(); unsubscribe = null; }
        if (!globe.available()) return;
        unsubscribe = globe.onCameraChanged(ctx.guard(() => {
          altitude = globe.altitude();
          updateVisibilityForAltitude();
        }));
        altitude = globe.altitude();
        log.log?.("HierarchicalRenderManager initialized");
      }

      function createClusteredDataSource(name, options) {
        if (!globe.available()) return null;
        const source = globe.addDataSource(name, clusterSettings(options));
        sources.push(source);
        log.log?.(`Created clustered data source: ${name}`);
        return source;
      }

      ctx.onCleanup(() => { for (const source of sources) globe.removeDataSource(source); });   // registered first, so it runs last
      ctx.onCleanup(() => { if (unsubscribe) unsubscribe(); });
      ctx.onCleanup(() => { run = null; });
      run = {
        altitude: () => altitude, viewer: () => globe.viewer(), initialize, createClusteredDataSource, updateVisibilityForAltitude,
        shouldShowDetail: (type) => shouldShowDetail(altitude, type),
      };
      if (early) { const [viewer] = early; early = null; initialize(viewer); }
    },
  };

  /** What the page calls as `window.HierarchicalRenderManager`. The calls do little while the tool is inactive (see the header). */
  const controls = {
    initialize(viewer) { if (run) run.initialize(viewer); else early = [viewer]; return controls; },
    get viewer() { return run ? run.viewer() : null; },
    get currentAltitude() { return run ? run.altitude() : 0; },
    shouldShowDetail: (type) => (run ? run.shouldShowDetail(type) : shouldShowDetail(0, type)),
    createClusteredDataSource: (name, options) => (run ? run.createClusteredDataSource(name, options) : null),
    updateVisibilityForAltitude: () => run?.updateVisibilityForAltitude(),
  };

  return {tool, controls};
}
