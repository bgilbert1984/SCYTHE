# AutoReconVisualization extraction plan

Status: plan only. Nothing here is implemented. It is written so each step can be agreed, built and merged on its own.

`AutoReconVisualization` is the largest object left in `command-ops-visualization.html`: about 1,500 lines (18065–19569), 52 members, 82 references.
It is also the one most of the rest of the page writes into. This plan separates its three jobs (the data, the drawing, the panel) and puts the
shared entity store behind one explicit door before any code moves.

## 1. What it does

| Job | Members | Notes |
|---|---|---|
| **Data and sync** | `loadEntities`, `loadAlerts`, `loadEntitiesBatch`, `loadNearestEntities`, `applyEntitySyncEvent`, `subscribeToEntitySync`, `_bindReconSSE`, `startAutoUpdate` / `stopAutoUpdate`, `setReferenceFromView`, `createTaskForEntity`, `updateDisposition`, `_normalizeEntityData`, `perfMetrics` | Seven REST routes, one SSE stream, the page's `EntitySync`, a 2 s poll. |
| **Drawing** | `addEntityMarker`, `addThreatIndicator`, `addProximityRings`, `addReferencePointMarker`, `addTaskLine`, `renderEntities`, `clearAllEntities`, `removeEntityMarker`, `updateEntityPosition`, `flyToEntity`, cluster styling and the three canvas icons, the altitude listener | Cesium entities in a clustered data source (from the render tool) and in `viewer.entities`. Also feeds `window.__MLDC__`. |
| **Panel** | `setupEventListeners`, `show` / `hide`, `updateAlertList`, `createEntityDescription` | Six checkboxes and buttons by id, the alert list, the entity description HTML. The recon list itself (`window.updateReconList`) is page code outside the object. |

## 2. What I found (read first)

1. **Two `removeEntityMarker`s; the pinned test pins the dead one.** Line 18895 and line 19517 both define it, and the later one wins. The existing
   test in `workspaceBoot.test.js` takes the first match, so it checks code that never runs. The live version removes from the data source if the marker
   is there, else from the viewer, and takes the altitude line and threat ring from the viewer only. The tested version checks both collections for all
   three. They agree for today's markers (the altitude line and threat ring are always in `viewer.entities`), but the test is not testing the page.
2. **The entity store is written from 10 other places**: the satellite button and toggle, the nmap geolocation, the AIS correlation, the upload and
   session re-ingest code, and the geo-point ingest. They do `.entities[id] = entity` and `.addEntityMarker(entity)`, and five of them first do
   `window.AutoReconVisualization = window.AutoReconVisualization || {}`, which creates a bare `{}` if the object does not exist yet. A boot module that sets `window.AutoReconVisualization` later would silently drop anything written to such a stub.
   `adoptEntitySyncStub` already solved the same problem for EntitySync.
3. **`subscribeToEntitySync` can run twice**: once from a 100 ms timer and once on `DOMContentLoaded`, and `EntitySync.on` is never undone. Each event
   is applied twice.
4. **Three unescaped HTML sinks carry data from other operators, scans and the entity stream** (more exposed than any module so far, since the
   entities include nmap targets and AIS and geolocated hosts): `createEntityDescription` (name, id, ontology, disposition, threat level, and the id inside
   inline `onclick`s), `updateAlertList` (name, id inside an `onclick`, threat level), and the page's `updateReconList` (name, disposition, ids in attributes).
5. **Dead or write-only:** `loadEntitiesBatch`, `loadNearestEntities`, `getPerformanceStats`, `showPerformanceMetrics` (no callers; the server routes exist),
   `_lastSnapshotMs` (written, never read, though a comment says `show()` reads it), the `typeof ...createEntity === 'function'` branches (no such method),
   `autoUpdate` (the checkbox state, read nowhere else). `perfMetrics` and `entities` are read by the metrics probe through `window.reconViz`.
6. **The client drives a server simulation.** Every auto-update tick calls `/api/recon/simulate` and then `/api/recon/changes`. The moving demo
   targets are produced by that call. That is a product behaviour to keep and not to move into a tool quietly.
7. **Ordering.** `initialize` reads `HierarchicalRenderManager.viewer` and creates its data source from it, so the render tool must have been
   initialized first (it is, 160 lines earlier in the page). The recon tool must keep that order.
8. **The marker's label math is pure and buried**: the MLDC priority, trust, recency and rf-confidence figures in `addEntityMarker` are plain functions of the entity.
9. **Unverified:** the Cesium description's buttons (`onclick="AutoReconVisualization.createTaskForEntity(...)"`) sit inside the infobox iframe. If
   its sandbox has no scripts, those buttons have never worked. I have not seen it run signed in; this needs one look before we decide whether to keep the
   names global or move to delegation.
10. **A partial update overwrites what the entity held** (found in step 4a, kept as it was). An UPDATE event (or stream message) is normalised before it is
    merged, and normalising fills every missing field with a default, so an update that carries only a name resets the held disposition to `UNKNOWN`, the threat
    level to `NONE`, the position to 0, 0 and the velocity to zero. The tests pin it (`planSyncEvent`). Whether the server ever sends partial updates is
    not known; if it does, the fix is to merge the raw data first and normalise the result.

## 3. Target shape

```
recon tool  (scythe-web/reconPlugin.js, tool id "recon")
├── pure:  normalizeLoaded, normalizeSynced, shouldHandle(event), eventFromStream(raw, known), mergeUpdate,
│          markerSpec, altitudeLineNeeded, threatNeeded, mldcLabel(entity, now), runningAverage,
│          describeEntity (escaped), alertRow (escaped), listRow (escaped)
└── services
    ├── reconStore    entities map: get / upsert / remove / all / onChange   (the one door the 10 writers use)
    ├── reconApi      over httpFetch: entities, alerts, simulate, changes, reference, task, batch, nearest, disposition
    ├── reconStream   over openEventSource, with the 5 s reconnect and resync
    ├── entityStream  (exists) the page's EntitySync handlers, with off
    ├── reconGlobe    clustered data source (via the render tool), markers, rings, reference marker, task lines, flights, icons, cluster styling
    ├── reconView     the panel by id: filters, counts, alert list, the six controls
    └── mldcLabels    window.__MLDC__, if present
```

## 4. Steps

Each step is its own PR, in this order. Steps 1 to 3 and 4a are merged (#326, #327, #328, #329); 4b is the `recon` tool (below, `reconApi` and `reconStream` were not needed: `httpFetch` and `openEventSource` already cover them). Step 4 was split in two: **4a** moves the pure logic (the
description, the lists' HTML, the normalising, the sync and stream decisions, the MLDC label, the running average) into `reconLogic.js` and routes
every write through the store, and **4b** moves the controller and the services into the `recon` tool. The first three change no drawing and are the cheapest to review. The last two are the extraction.

1. **Make the pinned test test the live code.** Delete the dead `removeEntityMarker`, point the test at the surviving one (keeping what it checks
   where that is still true), and guard `subscribeToEntitySync` against running twice. Small and behaviour-preserving, apart from the double subscription.
2. **Escape the three HTML sinks** (the description, the alert list, `updateReconList`), with ids passed to handlers as JSON or numbers as in #324.
   Tests run the page's own functions against hostile names and ids; one real-browser check as in #324.
3. **Put the entity store behind one door.** Add `reconStore`; move the 10 outside writers to it; have the boot adopt a stub that was created before it,
   as `adoptEntitySyncStub` does. No drawing moves; `AutoReconVisualization.entities` stays a view onto the store.
4. **Extract the controller (the data, the sync, the stream, auto-update, alerts, tasks, the panel) as the `recon` tool.** Drawing stays in a smaller page object that
   the tool calls through `reconGlobe`, so analysis and rendering are separate before either moves. Removes the dead members. Sets
   `window.AutoReconVisualization` to the tool's controls.
5. **Move the drawing into the `reconGlobe` service and delete the page object.** The altitude listener, the icons, the cluster styling, the MLDC feed and the flights go
   here. The metrics probe is pointed at the controls and `window.reconViz` goes.

## 5. What to pin (tests named `PINNED:`, changed only in their own PRs)

- Entities without a numeric `lat` and `lon` are skipped on load; the sync path instead coerces missing coordinates to 0 and draws them there.
- `loadEntities` replaces the whole store, `loadNearestEntities` clears and shows only the nearest.
- The marker id (`recon_entity_<id>`), the label text (`name\ndisposition`), and the rule for the altitude line (aircraft above 100 m and the altitude-lines detail on) and the threat ring (CRITICAL or HIGH).
- The sync event filter: `RECON_ENTITY`, `NMAP_TARGET`, or an id starting `AIS-` or `ENTITY-`; the stream's mapping of `entity_deleted` and unknown ids to DELETE and CREATE.
- The 2 s poll calls `simulate` and then `changes`, and the running-average arithmetic.
- Hiding clears everything and stops auto-update; showing reloads.
- An entity whose threat level or disposition is missing throws in the description (as today) until step 2 decides otherwise.

## 6. Risks

- **Boot ordering and stubs** (finding 2): the biggest chance of a silent loss. Step 3 exists to make it a tested case.
- **Many external callers use the global name** (about 40 references outside the object), including inline `onclick`s in generated HTML. The controls keep the name; only delegation can retire it.
- **Real-scene behaviour** (clustering, MLDC, flights, the infobox) cannot be tested without Cesium and a signed-in session. Each of steps 4 and 5 ends with a
  manual checklist, and neither should merge on test results alone.
- **Scope:** step 4 is the largest single move so far (about 900 lines). If it proves too large for one review it splits cleanly into data-and-sync, then panel.

## 7. Decisions needed

1. The two-step extraction (steps 4 and 5) as proposed, or one move?
2. Step 1: keep the live `removeEntityMarker` and drop the dead one (my recommendation), or adopt the tested one's both-collections behaviour?
3. Should the Cesium description's buttons stay as inline handlers (needs the global name) or move to delegation? One look in a signed-in browser would decide it.
4. Leave the client-side call to `/api/recon/simulate` as it is for now?
5. Dependencies tracked per step: the table from the render and POI PRs, with page globals split into read and written.
