/**
 * Auto-Recon as a workspace tool (the fifteenth tool on the lifecycle contract in lifecycle.js; the controller moved out of
 * command-ops-visualization.html as `AutoReconVisualization`, step 4b of docs/autorecon_extraction_plan.md).
 *
 * It owns what Auto-Recon does, and the drawing (markers, rings, icons, clusters, the camera: `reconGlobe.js`) is reached
 * only through the `reconGlobe` service. Once the page has handed over its viewer with `initialize(viewer)` the tool wires the Recon panel's
 * controls. "Show Recon entities" loads the entities from the server (`/api/recon/entities`) and the alerts, and draws them; "Auto-update"
 * opens the entity stream (`/api/recon/entities/stream`) and every two seconds asks the server to move its simulated targets
 * (`/api/recon/simulate`) and for what changed since (`/api/recon/changes`). It also keeps the store in step with EntitySync events, sets the
 * reference point from the camera, creates tasks and changes dispositions.
 *
 * What is decided is in `reconLogic.js` (the normalising, the sync and stream decisions, the HTML, the running average); the entities live in
 * the store (`reconStore.js`), passed in by the boot module as `store`, so they are there before the tool is active and after it is not.
 * The page calls the controls as `window.AutoReconVisualization` (the inline handlers in the descriptions and alert rows, the panel hooks,
 * the other modules that add markers). The controls do nothing while the tool is inactive, except `entities`, `createEntityDescription` and the getters.
 *
 * Kept as it was, including things that look unintended (see the tests that pin them):
 *  - The auto-update polls the server's simulator (`/api/recon/simulate`) from the client, every 2 s, whether or not anything changed.
 *  - Hiding the layer stops the auto-update (and says so) even if it was not running.
 *  - A reload from the server drops entities that only the client has (a satellite added by hand).
 *  - A stream that fails is retried after 5 s, with a full reload if the layer is showing.
 *  - The "Show Recon entities" checkbox shows and hides the layer, and the other five controls act whether or not the layer is showing.
 *  - A partial update merges after normalising (reconLogic.js, plan finding 10).
 *
 * Deliberate changes:
 *  - The tool subscribes to EntitySync once per activation and unsubscribes when it deactivates. The old code subscribed from a timer and again on
 *    DOMContentLoaded (step 1 guarded the second), and never unsubscribed.
 *  - Stopping the auto-update cancels a pending stream retry (the old code rebound the stream five seconds later even though the update had been stopped).
 *  - Deactivating stops the auto-update and the stream, cancels a pending retry, removes the panel's listeners, takes the drawing down, and an answer from
 *    the server that arrives afterwards changes nothing.
 *  - Entity names, task ids and the server's messages are escaped where they reach the console and the notifications (`reconConsole`), which put them
 *    in as HTML.
 *  - Gone: `subscribeToEntitySync` as a public method, `updateAlertList`, `setupEventListeners`, the unused `currentIdx`.
 *
 * Services it needs (see `requires`):
 *   httpFetch        (url, init?) => Promise<Response>
 *   openEventSource  (url) => EventSource
 *   operatorSession  {token() => string|null, clear()}
 *   entityStream     {on(event, handler), off(event, handler)}: EntitySync's handlers (the boot module's controls, there from the moment it runs)
 *   reconGlobe       the drawing: {attach(viewer), detach(), available(), render() => drawn, addMarker(entity), removeMarker(id), updatePosition(entity), clear(),
 *                    flyTo(id), addTaskLine(task), createEntityIcon(...), setVisible(shown), setShowRings(shown), setShowLabels(shown),
 *                    setReferencePoint(point), cameraPosition() => {lat, lon}}: see `reconGlobe.js`
 *   reconView        the panel: {bind(handlers) => unbind, setFiltersShown(shown), setEntityCount(n), setAlertCount(n), setAlertList(html()), refreshList(),
 *                    askDisposition(message, current) => string|null}
 *   reconConsole     {message(text, kind), notify(title, text, kind)}: text, not HTML
 */

import {alertListHtml, describeEntity, normalizeLoaded, planSyncEvent, runningAverage, streamEvent} from "./reconLogic.js";

export const RECON_ID = "recon";
export const POLL_MS = 2000;
export const RETRY_MS = 5000;

const STREAM_URL = "/api/recon/entities/stream";
const SYNC_EVENTS = ["preexisting", "create", "update", "delete"];
const DISPOSITIONS = ["UNKNOWN", "PENDING", "ASSUMED_FRIEND", "FRIEND", "NEUTRAL", "SUSPICIOUS", "HOSTILE"];
const JSON_HEADERS = {"Content-Type": "application/json"};

const newMetrics = () => ({updateCount: 0, lastUpdateMs: 0, avgUpdateMs: 0, entityUpdates: 0});

export function createReconPlugin({log = console, store, clock = {}} = {}) {
  const perf = clock.perf || (() => performance.now());
  const wall = clock.wall || (() => Date.now());
  let run = null;     // null while the tool is inactive
  let early = null;   // [viewer] handed over while the tool was inactive: used when it activates

  const tool = {
    id: RECON_ID,
    title: "Auto-Recon",
    requires: ["httpFetch", "openEventSource", "operatorSession", "entityStream", "reconGlobe", "reconView", "reconConsole"],
    activate(ctx) {
      const http = ctx.services.get("httpFetch");
      const openSource = ctx.services.get("openEventSource");
      const session = ctx.services.get("operatorSession");
      const entityStream = ctx.services.get("entityStream");
      const globe = ctx.services.get("reconGlobe");
      const view = ctx.services.get("reconView");
      const out = ctx.services.get("reconConsole");
      const stale = () => ctx.signal.aborted;   // deactivated while the server was answering
      const json = async (url, init) => (await http(url, init)).json();

      let visible = false;
      let alerts = [];
      let stopTick = null;      // stops the auto-update's interval while it is running
      let source = null;        // the open entity stream (only while the auto-update runs)
      let stopRetry = null;     // cancels the pending stream retry
      let lastUpdateTimestamp = null;
      let unbindPanel = null;
      let perfMetrics = newMetrics();

      /** Draws the layer; when it was drawn, refreshes the panel's list. */
      function render() {
        if (globe.render()) view.refreshList();
      }

      async function loadEntities() {
        try {
          out.message("Loading Auto-Recon entities...", "command");
          const data = await json("/api/recon/entities");
          if (stale()) return;
          if (data.status === "ok") {
            store.clear();   // in place: the store's map is the one everything reads
            data.entities.forEach((e) => {
              const normalized = normalizeLoaded(e);
              if (!normalized) {
                log.warn?.(`[AutoRecon] Skipping entity ${e.entity_id || e.id || "unknown"}: missing valid location data`);
                return;
              }
              store.upsert(normalized);
            });
            view.setEntityCount(data.entity_count);
            render();
            loadAlerts();
            out.message(`Loaded ${data.entity_count} reconnaissance entities`, "response");
            out.notify("Recon Data Loaded", `Tracking ${data.entity_count} entities`, "info");
          }
        } catch (error) {
          if (stale()) return;
          log.error?.("Error loading recon entities:", error);
          out.message("Error loading recon entities: " + error.message, "alert");
        }
      }

      async function loadAlerts() {
        try {
          const data = await json("/api/recon/alerts");
          if (stale()) return;
          if (data.status === "ok") {
            alerts = data.alerts;
            view.setAlertCount(data.alert_count);
            view.setAlertList(() => alertListHtml(alerts));
            if (data.alert_count > 0) out.message(`⚠️ ${data.alert_count} proximity alerts active!`, "alert");
          }
        } catch (error) {
          if (stale()) return;
          log.error?.("Error loading alerts:", error);
        }
      }

      function applyEntitySyncEvent(evt) {
        const plan = planSyncEvent(evt, store.entities, wall());
        if (!plan) return;
        log.log?.(`[AutoRecon] EntitySync ${evt.eventType || evt.event_type || "UPDATE"}: ${evt.entity_id} (${evt.entity_type || ""})`);
        if (plan.kind === "upsert") {
          store.upsert(plan.entity, plan.id);
          if (visible) {
            globe.removeMarker(plan.id);   // the old one, if any
            globe.addMarker(store.entities[plan.id]);
          }
        } else if (plan.existed) {
          store.remove(plan.id);
          if (visible) globe.removeMarker(plan.id);
        }
        view.refreshList();
        view.setEntityCount(Object.keys(store.entities).length);
      }

      function bindStream() {
        if (source) return;
        try {
          const src = openSource(STREAM_URL);
          src.onmessage = (event) => {
            if (stale()) return;
            try {
              applyEntitySyncEvent(streamEvent(JSON.parse(event.data), store.entities));   // no event (the greeting, no id): nothing to apply
            } catch (error) {
              log.debug?.("[AutoRecon] SSE parse error:", error);
            }
          };
          src.onerror = () => {
            if (stale()) return;
            log.warn?.("[AutoRecon] SSE stream disconnected");
            src.close();
            source = null;
            stopRetry = ctx.timeout(() => {
              if (visible) loadEntities();
              bindStream();
            }, RETRY_MS);
          };
          source = src;
          log.log?.("[AutoRecon] SSE stream connected");
        } catch (error) {
          log.warn?.("[AutoRecon] SSE bind failed:", error);
        }
      }

      async function tick() {
        if (!visible) return;
        const started = perf();
        try {
          await http("/api/recon/simulate");   // the server moves its simulated targets
          const response = await http(`/api/recon/changes?since=${lastUpdateTimestamp}`);
          const data = await response.json();
          if (stale()) return;
          if (data.status === "ok") {
            lastUpdateTimestamp = data.timestamp;
            if (data.entity_count > 0) {
              data.entities.forEach((e) => {
                store.upsert(e);
                globe.updatePosition(e);
              });
              perfMetrics.entityUpdates += data.entity_count;
            }
            const duration = perf() - started;
            perfMetrics.updateCount++;
            perfMetrics.lastUpdateMs = duration;
            perfMetrics.avgUpdateMs = runningAverage(perfMetrics.avgUpdateMs, perfMetrics.updateCount, duration);
            loadAlerts();   // cached on the server
          }
        } catch (error) {
          if (stale()) return;
          log.error?.("Error updating recon entities:", error);
        }
      }

      function startAutoUpdate() {
        if (stopTick) return;
        bindStream();
        lastUpdateTimestamp = wall() / 1000;
        stopTick = ctx.interval(tick, POLL_MS);
        out.message("Recon auto-update started (incremental mode)", "command");
      }

      function stopAutoUpdate() {
        if (stopTick) {
          stopTick();
          stopTick = null;
        }
        stopRetry?.();   // clearing a timer that has fired does nothing
        if (source) {
          source.close();
          source = null;
        }
        out.message("Recon auto-update stopped", "command");
      }

      function show() {
        visible = true;
        globe.setVisible(true);
        view.setFiltersShown(true);
        loadEntities();
      }

      function hide() {
        visible = false;
        globe.setVisible(false);
        view.setFiltersShown(false);
        globe.clear();
        stopAutoUpdate();
      }

      async function setReferenceFromView() {
        if (!globe.available()) return;
        try {
          const {lat, lon} = globe.cameraPosition();
          const data = await json("/api/recon/reference", {method: "POST", headers: JSON_HEADERS, body: JSON.stringify({lat, lon})});
          if (stale()) return;
          if (data.status === "ok") {
            globe.setReferencePoint(data.reference_point);
            out.message(`Reference point set to: ${lat.toFixed(4)}, ${lon.toFixed(4)}`, "response");
            loadEntities();   // with the new reference
          }
        } catch (error) {
          if (stale()) return;
          log.error?.("Error setting reference point:", error);
          out.message("Error setting reference point: " + error.message, "alert");
        }
      }

      async function createTaskForEntity(entityId) {
        try {
          const data = await json("/api/recon/task", {
            method: "POST", headers: JSON_HEADERS,
            body: JSON.stringify({entity_id: entityId, task_type: "INVESTIGATE", priority: 5}),
          });
          if (stale()) return;
          if (data.status === "ok") {
            out.message(`Task ${data.task.task_id} created for ${data.task.entity_name}`, "response");
            out.notify("Task Created", `Investigation task assigned: ${data.task.task_id}`, "info");
            globe.addTaskLine(data.task);
          } else {
            out.message("Failed to create task: " + data.message, "alert");
          }
        } catch (error) {
          if (stale()) return;
          log.error?.("Error creating task:", error);
          out.message("Error creating task: " + error.message, "alert");
        }
      }

      async function updateDisposition(entityId) {
        const entity = store.entities[entityId];
        if (!entity) return;
        const answer = view.askDisposition(`Update disposition for ${entity.name}\nCurrent: ${entity.disposition}\n\nOptions: ${DISPOSITIONS.join(", ")}`, entity.disposition);
        if (!answer || answer.toUpperCase() === entity.disposition) return;
        try {
          const headers = {...JSON_HEADERS};
          const token = session.token();
          if (token) headers["X-Session-Token"] = token;   // for provenance
          const data = await json(`/api/recon/entity/${entityId}/disposition`, {method: "POST", headers, body: JSON.stringify({disposition: answer.toUpperCase()})});
          if (stale()) return;
          if (data.status === "ok") {
            store.upsert(data.entity, entityId);
            out.message(`${entity.name} disposition changed to ${answer.toUpperCase()}`, "response");
            render();   // for the colours
          } else {
            out.message("Failed to update disposition: " + data.message, "alert");
          }
        } catch (error) {
          if (stale()) return;
          log.error?.("Error updating disposition:", error);
          out.message("Error updating disposition: " + error.message, "alert");
        }
      }

      function initialize(viewer) {
        globe.attach(viewer);
        log.log?.("Auto-Recon Visualization initialized");
        if (unbindPanel) return;
        unbindPanel = view.bind({
          showEntities: (checked) => { if (checked) show(); else hide(); },
          showRings: (checked) => globe.setShowRings(checked),
          showLabels: (checked) => globe.setShowLabels(checked),
          autoUpdate: (checked) => { if (checked) startAutoUpdate(); else stopAutoUpdate(); },
          setReference: () => setReferenceFromView(),
          checkAlerts: () => loadAlerts(),
        });
        ctx.onCleanup(() => { unbindPanel(); unbindPanel = null; });
      }

      // EntitySync: once per activation, undone on deactivation
      const handler = (data) => applyEntitySyncEvent(data);
      for (const event of SYNC_EVENTS) {
        entityStream.on(event, handler);
        ctx.onCleanup(() => entityStream.off(event, handler));
      }
      log.log?.("[AutoRecon] Subscribed to EntitySync for real-time updates");

      ctx.onCleanup(() => { globe.detach(); });   // registered early, so it runs late
      ctx.onCleanup(() => { run = null; });
      ctx.onCleanup(() => { if (source) { source.close(); source = null; } });
      run = {
        initialize, show, hide, loadEntities, loadAlerts, render, createTaskForEntity, updateDisposition,
        globe, state: () => ({visible, alerts, perfMetrics}),
      };
      if (early) { const [viewer] = early; early = null; initialize(viewer); }
    },
  };

  /** What the page calls as `window.AutoReconVisualization`. */
  const controls = {
    initialize(viewer) { if (run) run.initialize(viewer); else early = [viewer]; return controls; },
    get entities() { return store.entities; },
    get visible() { return run ? run.state().visible : false; },
    get alerts() { return run ? run.state().alerts : []; },
    get perfMetrics() { return run ? run.state().perfMetrics : newMetrics(); },
    createEntityDescription: (entity) => describeEntity(entity),
    show: () => run?.show(),
    hide: () => run?.hide(),
    loadEntities: () => (run ? run.loadEntities() : Promise.resolve()),
    loadAlerts: () => (run ? run.loadAlerts() : Promise.resolve()),
    renderEntities: () => run?.render(),
    createTaskForEntity: (entityId) => (run ? run.createTaskForEntity(entityId) : Promise.resolve()),
    updateDisposition: (entityId) => (run ? run.updateDisposition(entityId) : Promise.resolve()),
    addEntityMarker: (entity) => run?.globe.addMarker(entity),
    removeEntityMarker: (entityId) => run?.globe.removeMarker(entityId),
    flyToEntity: (entityId) => run?.globe.flyTo(entityId),
    createEntityIcon: (...args) => run?.globe.createEntityIcon(...args),
  };

  return {tool, controls};
}
