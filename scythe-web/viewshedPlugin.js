/**
 * The line-of-sight viewsheds as a workspace tool (the tenth tool on the lifecycle contract in lifecycle.js; moved out of
 * command-ops-visualization.html as `ViewshedManager`).
 *
 * A viewshed is what one observer could see: a marker at the observer, a wedge for its field of view, and a fan of rays, each ending where
 * the terrain blocks it. The page creates them for recon entities and cell towers from the Settings panel (`createEntityViewshed`,
 * `createTowerViewshed`), clears them, and changes `DEFAULT_CONFIG` (the field of view and the range) from two sliders.
 *
 * What is drawn is decided here as plain data (`wedgePoints`, `rayEnds`, `haversine`, `checkLineOfSight`), and the page is touched only
 * through services: `viewshedGlobe` (the Cesium entities), `terrainModel` (the elevation estimate, from RFPropagationManager) and
 * `activityLog` (the metrics tool's `logMetric`).
 *
 * `initialize(viewer)` is still how the page hands over its viewer, as before; nothing is drawn, and `createViewshed` returns null,
 * until it has. A viewer handed over before the tool is active is kept and used when it activates.
 *
 * Kept as it was, including things that look unintended (see the tests that pin them):
 *  - A ray is "blocked" by the terrain model when the estimated elevation at any of 9 points along it is above the straight line from the
 *    observer down to sea level at the ray's end. Without a terrain model the rays are blocked at random: beyond 60 % of the range, 30 % of
 *    them are, ending 40-70 % of the way out.
 *  - Creating a viewshed with an id that is already showing adds entities with duplicate ids, which Cesium refuses (the error reaches the caller).
 *  - A ray count of 1 puts the one ray at NaN (the angle step divides by zero).
 *  - The wedge's coordinates are degrees offset by `distance / 111320`, whatever the latitude.
 *
 * Deliberate changes:
 *  - Deactivating removes every viewshed's entities (each viewshed keeps the handles of what it drew). The old object had no way to.
 *  - The unused `raycastEntities` and `enabled`, the unused `updateViewshed`, `setViewshedVisible` and `getActiveViewsheds`, and the unused
 *    asynchronous `_checkLineOfSight` (the one that would sample real terrain) are gone: nothing in the repository called them.
 *  - The page no longer links `propagationManager` onto the manager; the `terrainModel` service looks RFPropagationManager up when it is used.
 *  - Positions are kept as degrees instead of being converted to Cartesian3 and back, which differs only in the last digits.
 *
 * Services it needs (see `requires`):
 *   viewshedGlobe  {attach(viewer), available(), drawObserver(), drawCoverage(), drawRay(), remove(handle)}: see `workspaceBoot.js`
 *   terrainModel   {available(), initialize(viewer), elevation(lat, lon)}
 *   activityLog    {metric(module, name, value)}
 */

export const VIEWSHED_ID = "viewsheds";
const METERS_PER_DEGREE = 111320;
const WEDGE_SEGMENTS = 24;
const TERRAIN_SAMPLES = 10;
const EARTH_RADIUS_M = 6371000;

export const DEFAULT_CONFIG = Object.freeze({
  horizontalFov: 60,      // degrees
  verticalFov: 45,        // degrees
  distance: 20000,        // meters
  visibleColor: Object.freeze({name: "GREEN", alpha: 0.25}),
  hiddenColor: Object.freeze({name: "RED", alpha: 0.25}),
  lineColor: Object.freeze({name: "YELLOW", alpha: 0.6}),
  rayCount: 36,           // rays per viewshed
  verticalRays: 8,
});

const radians = (degrees) => (degrees * Math.PI) / 180;

/** The flat `[lon, lat, ...]` outline of the field-of-view wedge: the observer, 25 points along the arc, the observer again. */
export function wedgePoints({lon, lat}, direction, horizontalFov, distance) {
  const start = direction - horizontalFov / 2;
  const end = direction + horizontalFov / 2;
  const degrees = distance / METERS_PER_DEGREE;
  const points = [lon, lat];
  for (let i = 0; i <= WEDGE_SEGMENTS; i++) {
    const angle = radians(start + (end - start) * (i / WEDGE_SEGMENTS));
    points.push(lon + degrees * Math.sin(angle), lat + degrees * Math.cos(angle));
  }
  points.push(lon, lat);
  return points;
}

/** Where each of `rayCount` rays would end if nothing blocked it, spread evenly across the field of view. */
export function rayEnds({lon, lat}, direction, horizontalFov, distance, rayCount) {
  const start = direction - horizontalFov / 2;
  const step = horizontalFov / (rayCount - 1);
  const degrees = distance / METERS_PER_DEGREE;
  const ends = [];
  for (let i = 0; i < rayCount; i++) {
    const angle = radians(start + step * i);
    ends.push({lon: lon + degrees * Math.sin(angle), lat: lat + degrees * Math.cos(angle)});
  }
  return ends;
}

/** The great-circle distance in meters. */
export function haversine(lat1, lon1, lat2, lon2) {
  const dLat = radians(lat2 - lat1);
  const dLon = radians(lon2 - lon1);
  const a = Math.sin(dLat / 2) * Math.sin(dLat / 2) + Math.cos(radians(lat1)) * Math.cos(radians(lat2)) * Math.sin(dLon / 2) * Math.sin(dLon / 2);
  return EARTH_RADIUS_M * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

/**
 * Whether the line from `start` (lon, lat, height) down to `end` is blocked, and where the ray should stop. With a terrain model it samples
 * the estimated elevation at 9 points along the line; without one it blocks at random (see the header).
 */
export function checkLineOfSight(start, end, maxRange, terrain, random = Math.random) {
  const distance = haversine(start.lat, start.lon, end.lat, end.lon);
  if (terrain) {
    for (let i = 1; i < TERRAIN_SAMPLES; i++) {
      const fraction = i / TERRAIN_SAMPLES;
      const lat = start.lat + (end.lat - start.lat) * fraction;
      const lon = start.lon + (end.lon - start.lon) * fraction;
      const lineHeight = start.height + (end.height - start.height) * fraction;
      const terrainHeight = terrain.elevation(lat, lon);
      if (terrainHeight > lineHeight) return {blocked: true, lon, lat, height: terrainHeight};
    }
    return {blocked: false, lon: end.lon, lat: end.lat, height: end.height};
  }
  const blocked = distance > maxRange * 0.6 && random() > 0.7;
  if (blocked) {
    const ratio = 0.4 + random() * 0.3;
    return {blocked: true, lon: start.lon + (end.lon - start.lon) * ratio, lat: start.lat + (end.lat - start.lat) * ratio, height: start.height * (1 - ratio)};
  }
  return {blocked: false, lon: end.lon, lat: end.lat, height: end.height};
}

export function createViewshedPlugin({log = console, random = () => Math.random()} = {}) {
  let run = null;   // null while the tool is inactive
  const config = {...DEFAULT_CONFIG};   // the page changes the field of view and the range here, and that outlives an activation
  let early = null;   // [viewer] handed over while the tool was inactive: used when it activates

  const tool = {
    id: VIEWSHED_ID,
    title: "Viewsheds",
    requires: ["viewshedGlobe", "terrainModel", "activityLog"],
    activate(ctx) {
      const globe = ctx.services.get("viewshedGlobe");
      const terrain = ctx.services.get("terrainModel");
      const activity = ctx.services.get("activityLog");
      let viewsheds = {};   // id -> {id, position, direction, horizontalFov, verticalFov, distance, visibleColor, hiddenColor, handles}

      function initialize(viewer) {
        globe.attach(viewer);
        if (terrain.available()) {
          if (viewer) terrain.initialize(viewer);
          log.log?.("ViewshedManager initialized with terrain-aware propagation");
        } else {
          log.log?.("ViewshedManager initialized (no propagation manager available)");
        }
      }

      function castRays(viewshed, options) {
        const {position} = viewshed;
        const start = {lon: position.lon, lat: position.lat, height: position.height};
        let blocked = 0, clear = 0;
        rayEnds(position, viewshed.direction, viewshed.horizontalFov, viewshed.distance, options.rayCount).forEach((end, i) => {
          const result = checkLineOfSight(start, {...end, height: 0}, options.distance, terrain.available() ? terrain : null, random);
          if (result.blocked) blocked++; else clear++;
          viewshed.handles.push(globe.drawRay({
            id: `viewshed_ray_${viewshed.id}_${i}`, from: start, to: result, blocked: result.blocked,
            visibleColor: viewshed.visibleColor, hiddenColor: viewshed.hiddenColor,
          }));
        });
        activity.metric("viewshed", "rays_cast", options.rayCount);
        activity.metric("viewshed", "rays_blocked", blocked);
        activity.metric("viewshed", "rays_clear", clear);
        activity.metric("viewshed", "visibility_percent", (clear / options.rayCount) * 100);
      }

      function createViewshed(id, options) {
        if (!globe.available()) return null;
        const merged = Object.assign({}, config, options);
        const viewshed = {
          id, position: {lon: merged.lon, lat: merged.lat, height: merged.height}, direction: merged.direction || 0,   // both creators below give a height
          horizontalFov: merged.horizontalFov, verticalFov: merged.verticalFov, distance: merged.distance,
          visibleColor: merged.visibleColor, hiddenColor: merged.hiddenColor, handles: [],
        };
        viewshed.handles.push(globe.drawObserver({id: `viewshed_observer_${id}`, position: viewshed.position, label: `📡 LOS: ${id}`}));
        const wedge = globe.drawCoverage({
          id: `viewshed_coverage_${id}`, points: wedgePoints(viewshed.position, viewshed.direction, viewshed.horizontalFov, viewshed.distance),
          height: viewshed.position.height, visibleColor: viewshed.visibleColor, lineColor: merged.lineColor,
        });
        if (wedge) viewshed.handles.push(wedge);
        castRays(viewshed, merged);
        viewsheds[id] = viewshed;
        log.log?.(`Viewshed created: ${id}`);
        return viewshed;
      }

      function removeViewshed(id) {
        const viewshed = viewsheds[id];   // only called with an id that is showing
        for (const handle of viewshed.handles) globe.remove(handle);
        delete viewsheds[id];
        log.log?.(`Viewshed removed: ${id}`);
      }

      function createEntityViewshed(entity) {
        if (!entity || !entity.location) return null;
        const radar = entity.ontology && entity.ontology.includes("radar");
        return createViewshed(`entity_${entity.entity_id}`, {
          lat: entity.location.lat, lon: entity.location.lon, height: entity.location.altitude_m || 100,
          direction: entity.velocity ? entity.velocity.heading_deg : 0, horizontalFov: radar ? 90 : 60, distance: radar ? 50000 : 20000,
        });
      }

      function createTowerViewshed(tower) {
        if (!tower || !tower.position) return null;
        return createViewshed(`tower_${tower.name || "unknown"}`, {
          lat: tower.position[1], lon: tower.position[0], height: tower.height || 50, direction: 0, horizontalFov: 360, distance: tower.range || 15000,
        });
      }

      function clearAllViewsheds() {
        for (const id of Object.keys(viewsheds)) removeViewshed(id);
      }

      ctx.onCleanup(() => { clearAllViewsheds(); });   // registered first, so it runs last
      ctx.onCleanup(() => { run = null; });
      run = {viewsheds: () => viewsheds, initialize, createEntityViewshed, createTowerViewshed, clearAllViewsheds};
      if (early) { const [viewer] = early; early = null; initialize(viewer); }
    },
  };

  /** What the page calls as `window.ViewshedManager` / `window.viewshedManager`. The calls do nothing while the tool is inactive. */
  const controls = {
    initialize(viewer) { if (run) run.initialize(viewer); else early = [viewer]; return controls; },
    get viewsheds() { return run ? run.viewsheds() : {}; },
    get DEFAULT_CONFIG() { return config; },   // live: the Settings sliders change `horizontalFov` and `distance`
    createEntityViewshed: (entity) => (run ? run.createEntityViewshed(entity) : null),
    createTowerViewshed: (tower) => (run ? run.createTowerViewshed(tower) : null),
    clearAllViewsheds: () => run?.clearAllViewsheds(),
  };

  return {tool, controls};
}
