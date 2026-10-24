/**
 * Points of interest as a workspace tool (the fourteenth tool on the lifecycle contract in lifecycle.js; moved out of
 * command-ops-visualization.html as `POIVisualization`).
 *
 * Once the page has handed over its viewer with `initialize(viewer)` it loads the points of interest from the server (`/api/poi/visualization`),
 * puts a pin and a label on the globe for each, and answers the POI panel: show or hide all pins, show or hide the labels, show or hide one
 * category, fly to one pin or to all of them, add a POI, delete one, reload. The panel's own code (the list, the counts, the category filters, the
 * form, the KMZ import) is still in the page and reads `pois`, `categories`, `visible` and `categoryVisibility` from the controls.
 *
 * What is decided is plain data and functions here (`pinColorName`, `parseMetadata`, `describePoi`, `entitySpec`); the page is touched only through
 * three services: `httpFetch` (the page's fetch), `poiGlobe` (the Cesium data source, entities and camera flights) and `poiConsole` (the page's
 * console messages).
 *
 * Kept as it was, including things that look unintended (see the tests that pin them):
 *  - A POI at latitude or longitude exactly 0 gets no pin (`!poi.latitude`).
 *  - Hiding a category, or the POI layer, is not remembered by the entities: showing or hiding the labels, or reloading, recreates every entity, so a
 *    hidden category's pins come back (its checkbox state in `categoryVisibility` stays).
 *  - `createEntities` stops at the first POI whose description cannot be built (a latitude that is not a number), and the load reports an error.
 *  - Deleting a POI removes its pin and its record but does not reload, so `categories` (the counts) are as they were.
 *  - A delete that fails on the network is logged to the console only; the other failures also show a console message.
 *  - A response is not checked for `ok`: a reply that is not JSON is the error.
 *  - Every category the pin colours do not name (residential, transportation, imported, manual) gets the general pin colour, yellow.
 *
 * Deliberate changes:
 *  - The name, description, category, source file and times a POI carries are escaped where they reach the entity's description (HTML). They come
 *    from the server's POI store and from KMZ files an operator imports, and the old code put them in as they were.
 *  - Deactivating takes the data source off the viewer (with every pin), and an answer from the server that arrives afterwards changes nothing.
 *  - Gone, after a search of the repository found no caller: `getStatus`, the unused `CATEGORY_COLORS` (computed per pin and never read), the
 *    `window.poiViz` alias (written, never read).
 *  - Before `initialize`, `setVisible` and the like do nothing (they threw a TypeError on the missing data source).
 *
 * Services it needs (see `requires`):
 *   httpFetch   (url, init) => Promise<Response>
 *   poiConsole  {message(text, kind)}
 *   poiGlobe    {attach(viewer), detach(), available(), add(spec) => handle, removeAll(), remove(handle), setShown(visible), setEntityShown(handle, shown),
 *                flyTo(handle), flyToPoints(points)}: see `workspaceBoot.js`
 */

import {escapeHtml} from "./htmlEscape.js";

export const POI_ID = "poi";

const PIN_COLORS = {intelligence: "RED", military: "MAROON", infrastructure: "ORANGE", commercial: "ROYALBLUE", government: "PURPLE", general: "YELLOW"};

/** The Cesium colour (a name on `Cesium.Color`) of a category's pin; yellow for any other. */
export const pinColorName = (category) => PIN_COLORS[category] || "YELLOW";

/** A POI's metadata as an object: a JSON string is parsed, one that does not parse is empty, anything else is returned as it is. */
export function parseMetadata(metadata) {
  if (typeof metadata !== "string") return metadata;
  try { return JSON.parse(metadata); } catch (error) { return {}; }
}

const row = (label, value) => `<tr><td style="padding: 3px; color: #888;">${label}</td><td style="padding: 3px;">${value}</td></tr>`;

/** The entity's description: a small HTML card. Text the POI carries is escaped. Throws if the latitude or longitude is not a number (as the old code did). */
export function describePoi(poi, metadata) {
  let desc = `<div style="padding: 10px; font-family: Arial, sans-serif;">`;
  desc += `<h3 style="margin: 0 0 10px 0; color: #ffd700;">${escapeHtml(poi.name)}</h3>`;
  if (poi.description) desc += `<p style="margin: 5px 0;">${escapeHtml(poi.description)}</p>`;
  desc += `<table style="width: 100%; border-collapse: collapse;">`;
  desc += row("Category:", escapeHtml(poi.category));
  desc += row("Coordinates:", `${poi.latitude.toFixed(6)}, ${poi.longitude.toFixed(6)}`);
  if (poi.source_file) desc += row("Source:", escapeHtml(poi.source_file));
  if (metadata) {
    if (metadata.time_begin) desc += row("Time Begin:", escapeHtml(metadata.time_begin));
    if (metadata.time_end) desc += row("Time End:", escapeHtml(metadata.time_end));
    if (metadata.view_range) desc += row("View Range:", `${(metadata.view_range / 1000).toFixed(1)} km`);
  }
  desc += `</table></div>`;
  return desc;
}

/** What the globe needs to draw one POI (plain data; the label is null when labels are off). */
export function entitySpec(poi, category, metadata, showLabels) {
  return {
    id: `poi_${poi.id}`, name: poi.name, description: describePoi(poi, metadata),
    lon: poi.longitude, lat: poi.latitude, alt: poi.altitude || 0,
    pinColor: pinColorName(category), label: showLabels ? poi.name : null,
    properties: {poiId: poi.id, category, sourceFile: poi.source_file, metadata},
  };
}

export function createPoiPlugin({log = console} = {}) {
  let run = null;     // null while the tool is inactive
  let early = null;   // [viewer] handed over while the tool was inactive: used when it activates

  const tool = {
    id: POI_ID,
    title: "Points of interest",
    requires: ["httpFetch", "poiConsole", "poiGlobe"],
    activate(ctx) {
      const http = ctx.services.get("httpFetch");
      const out = ctx.services.get("poiConsole");
      const globe = ctx.services.get("poiGlobe");
      const stale = () => ctx.signal.aborted;   // deactivated while the server was answering

      let pois = [];
      let categories = {};
      let categoryVisibility = {};
      let entities = {};   // poi id -> {handle: the globe's, name}
      let visible = true;
      let showLabels = true;

      function createPoiEntity(poi) {
        if (!globe.available() || !poi.latitude || !poi.longitude) return;
        const category = poi.category || "general";
        const metadata = parseMetadata(poi.metadata);
        entities[poi.id] = {handle: globe.add(entitySpec(poi, category, metadata, showLabels)), name: poi.name};
      }

      function createEntities() {
        globe.removeAll();
        entities = {};
        pois.forEach(createPoiEntity);
        log.log?.(`[POI] Created ${Object.keys(entities).length} POI entities`);
      }

      async function loadPOIs() {
        try {
          out.message("Loading Points of Interest...", "info");
          const response = await http("/api/poi/visualization");
          const data = await response.json();
          if (stale()) return;
          if (data.status === "ok") {
            pois = data.pois || [];
            categories = data.categories || {};
            Object.keys(categories).forEach((cat) => { categoryVisibility[cat] = true; });
            createEntities();
            out.message(`Loaded ${pois.length} POIs in ${Object.keys(categories).length} categories`, "response");
            log.log?.("[POI] Loaded POIs:", pois.length);
          } else {
            log.warn?.("[POI] Failed to load POIs:", data.message);
            out.message("POI system not available", "alert");
          }
        } catch (error) {
          if (stale()) return;
          log.error?.("[POI] Error loading POIs:", error);
          out.message("Error loading POIs: " + error.message, "alert");
        }
      }

      function initialize(viewer) {
        globe.attach(viewer);
        log.log?.("[POI] Visualization initialized");
        loadPOIs();
      }

      function flyToPOI(poiId) {
        const entity = entities[poiId];
        if (!entity) return;   // a pin exists only while the globe is attached
        globe.flyTo(entity.handle);
        out.message(`Flying to POI: ${entity.name}`, "info");
      }

      function flyToAllPOIs() {
        if (!globe.available() || pois.length === 0) return;
        globe.flyToPoints(pois.map((poi) => ({lon: poi.longitude, lat: poi.latitude, alt: poi.altitude || 0})));
        out.message(`Flying to view all ${pois.length} POIs`, "info");
      }

      function setVisible(shown) {
        visible = shown;
        globe.setShown(shown);
        log.log?.(`[POI] Visibility set to ${shown}`);
      }

      function setCategoryVisible(category, shown) {
        categoryVisibility[category] = shown;
        pois.forEach((poi) => {
          if (poi.category === category && entities[poi.id]) globe.setEntityShown(entities[poi.id].handle, shown && visible);
        });
      }

      function toggleLabels(show) {
        showLabels = show;
        createEntities();
      }

      async function addPOI(name, latitude, longitude, category = "manual", description = "") {
        try {
          const response = await http("/api/poi/add", {
            method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({name, latitude, longitude, category, description}),
          });
          const data = await response.json();
          if (stale()) return null;
          if (data.status === "ok") {
            out.message(`POI "${name}" added successfully`, "response");
            await loadPOIs();
            return data.poi_id;
          }
          out.message("Failed to add POI: " + data.message, "alert");
          return null;
        } catch (error) {
          if (stale()) return null;
          log.error?.("[POI] Error adding POI:", error);
          out.message("Error adding POI: " + error.message, "alert");
          return null;
        }
      }

      async function deletePOI(poiId) {
        try {
          const response = await http(`/api/poi/delete/${poiId}`, {method: "DELETE"});
          const data = await response.json();
          if (stale()) return false;
          if (data.status === "ok") {
            out.message("POI deleted successfully", "response");
            if (entities[poiId]) {
              globe.remove(entities[poiId].handle);
              delete entities[poiId];
            }
            pois = pois.filter((p) => p.id !== poiId);
            return true;
          }
          out.message("Failed to delete POI: " + data.message, "alert");
          return false;
        } catch (error) {
          log.error?.("[POI] Error deleting POI:", error);
          return false;
        }
      }

      ctx.onCleanup(() => { globe.detach(); });   // registered first, so it runs last
      ctx.onCleanup(() => { run = null; });
      run = {
        state: () => ({pois, categories, categoryVisibility, visible}),
        initialize, loadPOIs, addPOI, deletePOI, flyToPOI, flyToAllPOIs, setVisible, setCategoryVisible, toggleLabels,
      };
      if (early) { const [viewer] = early; early = null; initialize(viewer); }
    },
  };

  /** What the page calls as `window.POIVisualization`, from its init, the POI panel and the inline handlers. The calls do nothing while the tool is inactive. */
  const controls = {
    initialize(viewer) { if (run) run.initialize(viewer); else early = [viewer]; },
    get pois() { return run ? run.state().pois : []; },
    get categories() { return run ? run.state().categories : {}; },
    get categoryVisibility() { return run ? run.state().categoryVisibility : {}; },
    get visible() { return run ? run.state().visible : true; },
    loadPOIs: () => (run ? run.loadPOIs() : Promise.resolve()),
    addPOI: (...args) => (run ? run.addPOI(...args) : Promise.resolve(null)),
    deletePOI: (poiId) => (run ? run.deletePOI(poiId) : Promise.resolve(false)),
    flyToPOI: (poiId) => run?.flyToPOI(poiId),
    flyToAllPOIs: () => run?.flyToAllPOIs(),
    setVisible: (shown) => run?.setVisible(shown),
    setCategoryVisible: (category, shown) => run?.setCategoryVisible(category, shown),
    toggleLabels: (show) => run?.toggleLabels(show),
  };

  return {tool, controls};
}
