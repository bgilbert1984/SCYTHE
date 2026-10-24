# Workspace tools and their lifecycle

`command-ops-visualization.html` initializes every subsystem on load, whether or not the operator uses it. The first step
away from that is a contract for a *tool*: something that is registered once, woken when needed, and torn down completely.
`scythe-web/lifecycle.js` is that contract; ADS-B (`scythe-web/adsbPlugin.js`) is the pilot.

## The contract

```js
workspace.provide("entityMarkers", {upsert, remove});     // services the page offers
workspace.register({
  id: "adsb", title: "ADS-B",
  requires: ["entityMarkers", "realtimeSocket", "fetchJson"],
  activate(ctx) { /* take resources; ctx.onCleanup(undo) as each is taken */ },
  deactivate() {},   // optional extra teardown
  dispose() {},      // optional, once, when retired for good
});
await workspace.activate("adsb");  await workspace.deactivate("adsb");  await workspace.dispose("adsb");
```

- **States:** `inactive -> activating -> active -> deactivating -> inactive`, and `disposed` (terminal).
- **Idempotent and race-safe:** activating an active tool does nothing; concurrent activations share one attempt;
  deactivating mid-activation waits for it, then tears down; activating mid-deactivation waits, then activates once.
- **Rollback:** a failed `activate` runs the cleanups taken so far, leaves the tool `inactive`, and keeps the error
  (`workspace.error(id)`).
- **Teardown never stops half way:** `deactivate()` first, then cleanups last-registered-first; every step runs, errors are
  collected and returned (and logged), never thrown mid-way.
- **Services are declared:** a tool can only `ctx.services.get()` what it listed in `requires`, and is refused (nothing of it
  runs) while one is missing.

## Scope helpers on `ctx` (API v1)

`ctx` is also a *scope*: it owns an abort signal and the resources taken under it. Helpers so a tool cannot forget the
bookkeeping the first three tools each did by hand:

- `ctx.signal` is aborted when the tool deactivates. `ctx.guard(fn)` is `fn` that does nothing once it is aborted (late events,
  promise continuations; for an `async fn` it guards the start, so a continuation after an `await` checks `ctx.signal.aborted`).
- `ctx.listen(target, event, fn, options?)` subscribes now and unsubscribes on close, for `on`/`off` emitters and DOM-style targets.
- `ctx.interval(fn, ms)` / `ctx.timeout(fn, ms)` are cleared on close and guarded; they return a `stop()`. Timers come from
  `createWorkspace({timers})` (default `globalThis`), so tests drive a fake clock.
- `ctx.scope()` is a child scope with its own signal and cleanups, for something opened and closed on its own (chat's connection);
  it closes with its parent, or alone with `scope.close()`. A child's cleanup errors are rethrown (an `AggregateError` for several)
  so the parent's `deactivate()` reports them.

Order and timing: teardown aborts the signal first, then runs the tool's `deactivate()` hook, then the cleanups last-registered
first. Aborting is synchronous; releasing resources is awaited, so a "closed" assertion must await. A resource registered after
its scope closed is undone at once. Closing twice runs each cleanup once.

This API is treated as v1: the names (`listen`, `guard`, `interval`, `timeout`, `scope`), the child-scope `AggregateError` policy and the
frozen `ctx` were decided in #301, and change only if migrating the remaining tools exposes a real ergonomic problem, not for
terminology.

ADS-B, chat and AIS streaming use the helpers. AIS keeps one explicit `ctx.signal.aborted` check, after the `await` on starting the
server stream: the helpers can drop a late callback, but only the tool knows that a late *answer* means something was started on the
server and must be stopped. Where a guard looks redundant it was removed (a handler that goes through `run`, which is nulled first),
and every guard that remains has a test that fires an event in the gap between two awaited cleanups.

## What the pilot changed

- ADS-B moved out of `AISStreamManager.init` / `handleAircraftUpdate` into `adsbPlugin.js`. It is still activated where
  `AISStreamManager` starts, so **nothing loads lazily yet**; the difference is that it can now be deactivated, and doing so
  stops listening, drops a seed that resolves late, and removes every aircraft it drew
  (`AutoReconVisualization.removeEntityMarker`).
- Fixed: aircraft entities had no `threat_level`, so `createEntityDescription` threw
  (`Cannot read properties of undefined (reading 'toLowerCase')`) for every aircraft.
- Changed: the REST seed now rejects on an HTTP error instead of parsing an error page as JSON; a track with a missing or
  non-numeric position is skipped.

## Second pilot: AIS streaming (`scythe-web/aisStreamPlugin.js`)

ADS-B has no state worth tearing down. AIS streaming does: a server-side upstream stream, a bounding box that follows the
camera, and three listeners. Moving it behind the contract showed what the old `AISStreamManager` never cleaned up:

- **Listeners were never removed:** the `ais_update` socket handler, the `WebSocketSync` `connected` handler and the Cesium
  `camera.changed` listener (whose remove function was discarded). They stacked on every `init`. Now each is registered with
  `ctx.onCleanup` and removed on deactivate.
- **The server's stream was never stopped on teardown.** Deactivating now stops it if this tool started it, and a start
  request still in flight when the tool is deactivated is stopped when it lands (it is never announced as running).
- **Double start:** two calls before the first answered made two server requests; now one.
- **Activated after the socket connected:** the `connected` event has gone by, so the tool checks `syncEvents.connected` and
  starts instead of waiting. At page startup this changes nothing (the socket is not connected yet).
- The tool owns the stream, not the vessels: vessels already handed to the renderer stay, because the renderer also holds
  vessels loaded from the REST archive.

`AISStreamManager` remains as a shim: `init` activates the tools; `checkAndStartStream` / `stopStreaming` (called by the AIS
toggle) delegate to the tool's controls and do nothing while it is inactive; `handleVesselUpdate` is still the renderer adapter
the tool forwards vessels to.

**Kept as it was, and pinned by tests, though it looks unintended:** zooming out does **not** stop the stream. The old guard
was `else if (this.streamSocket)`, a field declared `null` and never assigned, so the branch never ran. Making zoom-out stop
the stream means also deciding how it resumes on zoom-in, which is a behaviour change (it starts and stops the upstream
AIS feed), so it is left for a decision. Also unchanged: a bounding-box update re-posts to the *start* endpoint.

**Changed:** an HTTP error from the stream endpoints is now an error instead of parsing the body. A failed stop leaves the
stream marked active so it can be retried, as before.

**Not covered:** the operator's "search visible area" action starts the server stream on its own path, so the tool does not
know about a stream started that way and deactivation will not stop it.

## Third pilot: room chat (`scythe-web/chatPlugin.js`)

ADS-B and AIS are streams. Chat adds timers, an `EventSource` with a polling fallback, DOM listeners, a hook into another
object (`RoomManager`) and room switching. The tool owns *one connection object*; closing it is the only way anything stops
(the event stream, the poll interval and the fallback timer all belong to it), and a late result for a closed connection is
dropped. What that fixed in the old `LiveChat`:

- The 3 s fallback timeout was never cleared; the poll interval lived on `connect._pollTimer` (a property on a function).
- A history fetch that landed after a room switch wrote the old room's messages into the new room's feed.
- `init` added its click / Enter / callsign listeners every time it ran, and the `RoomManager` hook retried every 500 ms
  forever and was never unhooked. Both now go through `ctx.onCleanup`; the lookup retry is cancelled by deactivation.

Rendering stays with the page (`LiveChat.addMsg`, which escapes everything it prints; now exported as `window.LiveChat` so the
tool's view adapter can reach it). `LiveChat.init/connect/switchRoom/send` remain as shims that delegate to the tool and do
nothing while it is inactive. Timers, `EventSource`, `RoomManager` and the DOM are services, so the tests run the whole
fallback sequence on a fake clock.

**Kept as it was, and pinned by a test:** messages delivered by the stream do not advance the poll cursor (`since`), so if the
stream drops and polling takes over, messages already shown from the stream can be shown again. Fixing it means deciding what
the cursor is (server timestamp of the newest message seen from any transport), which is a behaviour change.

**Changed:** the history and poll requests now reject on an HTTP error instead of parsing an error page (both were already
swallowed); a failed send keeps the text, as before.

## Fourth tool: metrics logging (`scythe-web/metricsPlugin.js`)

The page's `MetricsLogger` (261 lines of `command-ops-visualization.html`) is now the `metrics` tool. It is the first tool moved
only to shrink the page: what it logs did not change. Why it was a good first extraction: it reached the rest of the page only
through `window.*` reads, and it had the defects the contract exists for (its collection interval and `beforeunload` listener
were never released).

- Services: `httpFetch` (the page's `fetch`, read at call time) and `metricsProbe` (the gauges the page's renderers expose, the
  browser environment, and the unload target). `gauges` is a generator, so a gauge that throws part way keeps the ones before it.
- The page keeps its names: `window.metricsLogger` (the Metrics Explorer reads `_interactionLog` and `_lastMetrics`),
  `window.logMetric`, `window.logInteraction`. They do nothing until the tool is active, and every caller on the page already
  tests for them first. The boot module activates the tool; there is no page code that used to start it.
- Teardown: the interval and unload hook are released, then the buffer is flushed. The session id, buffer and interaction log
  survive a deactivate/activate.
- Pinned, not changed: a flush ignores the HTTP status (a 500 drops the batch); `session_end` reports seconds since page load.
- Not moved: the Metrics Explorer's own reads of `/api/metrics/session` and `/api/metrics/query`.

## Fifth tool: Metrics Explorer (`scythe-web/metricsExplorerPlugin.js`)

The Metrics Explorer panel (308 lines of `command-ops-visualization.html`) is now the `metrics-explorer` tool. It was all DOM reads
and `innerHTML` writes with nothing worth keeping as state, so it splits in two: what to show is built as plain strings in the
plugin (`render*`, tested without a DOM), and the page's elements are touched only by the `explorerView` service in `workspaceBoot.js`.

- Services: `httpFetch` (shared with the metrics tool), `explorerView`, and `liveSources` (the metrics tool's in-memory logs and the
  page's counters). `window.MetricsExplorer.fetchAllMetrics` is what `showPanel('metrics')` calls.
- The boot module activates it 2 s after `DOMContentLoaded`, as the old `init` was delayed.
- Teardown: the Refresh button and filter listeners and the 30 s interval are released (the old object kept them), and a response
  that lands after deactivation is dropped.
- One deliberate change: server and user text (module and metric names, interaction action and target) is HTML-escaped.
- Pinned, not changed: an unused `/api/metrics/query?module=interaction` request on every interactions refresh (its failure stops the
  list updating); a failed session request ends the refresh; zero heap or update time shows `--`.
- The recon-counter and heap fallback in the live stats never runs on the page (the metrics logger's `_lastMetrics` is always truthy), as before.

## Sixth tool: Propagation Analysis (`scythe-web/propagationPlugin.js`)

The Propagation panel (334 lines of `command-ops-visualization.html`) is now the `propagation-ui` tool. Like the explorer it splits in
two: what to show is built as plain strings in the plugin (`render*`, tested without a DOM), and the page's elements are touched only
by the `propagationView` service in `workspaceBoot.js`. Unlike the explorer it also owns state (the calculation history and the paths
it drew) and drives an engine, `RFPropagationManager`, which stays on the page and is reached through the `propagationEngine` service.

- Services: `propagationEngine`, `propagationView`, and `activityLog` (the metrics tool's `logMetric` / `logInteraction`, if present).
  `window.PropagationUI.updateAtmosphericDisplay` is what `showPanel('propagation')` calls.
- The boot module activates it 2.5 s after `DOMContentLoaded`, as the old `init` was delayed.
- Teardown: the four button listeners and two slider listeners are released, the paths this tool drew are removed from the engine, and a
  calculation or visualization that finishes after deactivation is dropped (a visualization the engine already drew is taken back down).
- Deliberate changes: error text is HTML-escaped; a missing panel element is skipped instead of throwing out of `showPanel`; path ids come
  from a counter that outlives an activation.
- Pinned, not changed: inputs are `parseFloat`ed and not validated (an empty box sends `NaN`); a result that cannot be displayed is
  already in the history; an empty visualization still uses up a path id.

## Seventh tool: the entity stream (`scythe-web/entitySyncPlugin.js`)

`EntitySync` (a 270-line object in `command-ops-visualization.html`) is now the `entity-sync` tool. It holds the entity cache and the
event handlers other modules register, opens the server-sent-events stream on `connect(since)`, and reconnects with a growing delay.
Events from the WebSocket path go through the same `ingest(eventType, data)`, which replaced a copy of the cache logic in
`WebSocketSync._handleEntityEvent`.

- Services: `httpFetch`, `openEventSource`, `operatorSession` (`token()`, `clear()`), and `entityConsole` (the page's console messages).
- `window.EntitySync` is a small stub from the moment the page's scripts run, because modules subscribe while the page loads, before the
  boot module has run. The boot module activates the tool, replaces the stub, and replays what the stub kept: the subscriptions, and a
  `connect` (made once the tool is active).
- Handlers (`on` / `off`) outlive an activation, because the modules that subscribe do not know about the lifecycle. The cache and the
  connection belong to an activation: deactivating closes the stream, cancels a pending probe or reconnect, and empties the cache.
- Deliberate changes: the status probe is aborted once its status is known (it used to leave a second stream open on every connect); a new
  `connect` replaces one in progress (two used to open two streams and lose the first); `disconnect` cancels a pending reconnect; an event
  without an entity id is not cached; WebSocket events reach `EntitySync`'s handlers before `WebSocketSync`'s own; the unused
  `deleteEntity`, `getEntity`, `getAllEntities`, `getEntitiesByType` and the write-only `lastEventId` are gone.
- Not changed: a reconnect carries no `since`, so nothing is replayed after an outage; and 15 subscriptions in the page are never removed
  (`off` now works, but no caller uses it, so subscribing twice still doubles the handlers).

## Eighth tool: sensors (`scythe-web/sensorPlugin.js`)

`SensorManager` (an 899-line object in `command-ops-visualization.html`) is now the `sensors` tool: the Sensors panel's list and create
form, the REST calls, the assignments to recon entities, the sensor events from the entity stream (read by `sensorEvents.js`, see #314), and
a marker per sensor and a line per assignment on the globe. What the panel shows is plain strings and plain data in the plugin (`render*`,
`markerOf`, `assignmentLinks`, `flightTarget`); the page is touched only through two services in `workspaceBoot.js`: `sensorView` (the panel's
DOM) and `sensorGlobe` (the Cesium markers, lines and camera).

- Services: `httpFetch`, `operatorSession`, `entityConsole`, `reconEntities`, `entityStream` (`on` / `off` of the entity stream's handlers),
  `sensorView`, `sensorGlobe`. `window.SensorManager` exposes the live `sensors` and `assignments` (read by the LPI, Fusion and recon code),
  `load`, `flyTo`, `populateEntityDropdown` (renamed from `_populateEntityDropdown`), and the two methods nothing calls, `unassign` and `emitActivity`.
- The boot module activates it. The stream subscription is made on activation and removed on deactivation (the old one never was).
- Teardown: the panel's listeners, the stream subscription, an open assign dialog, and every marker and line are removed; a request or dialog
  result that arrives afterwards is dropped. Sensors and assignments start empty on each activation.
- Deliberate changes: every sensor, entity and error text that reaches HTML is escaped (the list, the group headers, the assign dialog and the
  globe's description balloon; sensors now arrive from other operators, and the old code put their labels into `innerHTML`); the list's
  buttons use one listener instead of being wired after every redraw.
- Pinned, not changed (tests named `PINNED:`): the create form turns a 0 into "none", so a coordinate or frequency of exactly 0 cannot be
  entered; an entity at latitude or longitude 0 reads as having no position (`||`, where a sensor uses `??`); an existing marker's label and type
  are not updated, only its position; deleting a sensor leaves its assignment lines until the next redraw; moving a group swaps it with its
  neighbour in the full order, shown or not; loading replaces the sensors but not the assignments; only the server's `error` text is read on a failed save.

## Ninth tool: fusion overlays (`scythe-web/fusionPlugin.js`)

`FusionVisualization` (a 238-line object in `command-ops-visualization.html`) is now the `fusion` tool. While active it listens to the entity
stream for sensor activity (read by `sensorEvents.js`) and draws what a measurement says for a few seconds: an angle-of-arrival ray from a
sensor (3 s), a time-difference line between two sensors (3 s), a traffic-flow arc between two resolved IPs (10 s), and a geo-resolved IP as a
point (60 s). What is drawn is plain data in the plugin (`aoaEndpoint`, `protocolColor`, `flowWidth`, `ipPosition`, ...); the page is touched
through `fusionData` (the Sensors panel's sensors, read when it draws) and `fusionGlobe` (the Cesium entities, in `workspaceBoot.js`).

- Each overlay's lifetime is owned by the globe service, so deactivating the tool removes what is showing and cancels the pending removals (the
  old timers could not be cancelled), after the stream subscription is removed. Without a viewer nothing is drawn and nothing throws.
- The old one-second retry loop that waited for the entity stream is gone, and so is the raw edge-event branch of `handleActivity`, which the
  stream handler never reached (it passes the flat form from `activityOf`).
- Pinned, not changed (tests named `PINNED:`): a sensor at latitude or longitude 0 gets no AoA ray, and positions are read from `lat` / `lon` or
  `location`, not the panel's normalized `position`; a TDoA sensor with no altitude and no `location` throws; an empty protocol histogram throws;
  any `172.x` address is "private" and drawn at Los Angeles with `192.168.` and `10.`; the TDoA label carries a `position` Cesium ignores.
- `window.FusionVisualization` is assigned, with `updateGeoNode(payload)` and `drawFlow(sensorId, payload)`. The page's PCAP simulation has always called them
  (guarded by `if (window.FusionVisualization)`), but nothing ever assigned the global, so the simulation's geo points and traffic flows were never drawn
  locally; they are now. They do nothing while the tool is inactive and share its remembered IP positions with the stream events.

## Tenth tool: viewsheds (`scythe-web/viewshedPlugin.js`)

`ViewshedManager` (a 490-line object in `command-ops-visualization.html`) is now the `viewsheds` tool. A viewshed is what one observer could see:
a marker at the observer, a wedge for its field of view, and a fan of rays (36 by default), each ending where the terrain blocks it. The page
creates them for recon entities and cell towers from the Settings panel and changes `DEFAULT_CONFIG` (field of view and range) from two sliders.
The geometry and the line-of-sight test are plain functions in the plugin (`wedgePoints`, `rayEnds`, `haversine`, `checkLineOfSight`); the page is
touched through `viewshedGlobe` (the Cesium entities), `terrainModel` (RFPropagationManager's elevation estimate) and `activityLog` (the metrics
tool's `logMetric`). `window.ViewshedManager` is the tool's controls; the page still calls `initialize(viewer)` to hand over its viewer (a viewer
handed over before the tool is active is kept), and still keeps `window.viewshedManager` as an alias, which the metrics probe reads.

- Deactivating removes every viewshed's entities (each viewshed keeps the handles of what it drew).
- Removed as dead after a repository-wide search: `raycastEntities`, `enabled`, `updateViewshed`, `setViewshedVisible`, `getActiveViewsheds`, and the
  asynchronous `_checkLineOfSight` (the version that would sample real terrain). The page no longer links `propagationManager` onto the manager; the
  `terrainModel` service looks RFPropagationManager up when it is used.
- Pinned, not changed (tests named `PINNED:`): without a terrain model rays are blocked at random (beyond 60 % of the range, 30 % are); a ray count of 1
  puts its ray at NaN; creating a viewshed with an id that is showing is refused by Cesium and the error reaches the caller; the wedge uses degrees
  offset by `distance / 111320` at any latitude.

## Eleventh tool: LPI (`scythe-web/lpiPlugin.js`)

`LPIManager` (a 390-line object in `command-ops-visualization.html`) is now the `lpi` tool. While active it listens to the entity stream for sensor
activity (read by `sensorEvents.js`), keeps the latest 500 events per sensor, and shows them: a feed of the 50 newest across sensors, one status card
per sensor (a classification or a candidate in the last 10 s, else idle), and a details box for a clicked row. A detection or classification also
pulses on the globe at its sensor (2 s) and a classification floats its top class (4 s). The "Run simulation" button asks the server to simulate a
detection and shows the returned trace at once. What is shown is plain strings and data in the plugin; the page is touched through `lpiView` (the
panel's DOM), `lpiGlobe` (the Cesium pulse and label, which animate and time out themselves), `lpiSensors` (the Sensors panel's sensors and camera),
`frames` (animation frames) and `clock`.

- Deactivating removes the stream subscription, the buttons' listeners, pending frames, and every pulse and label (the old animation loops and removal
  timers could not be cancelled). The feed's rows use one listener on the list instead of an `onclick` each.
- Everything from an event or a sensor is escaped where it reaches HTML (sensor ids, kinds, class names): events arrive from other operators now.
- Removed: `config.pulseDuration` (never read) and `state.artifacts`, an evidence-pointer index written for every event and read by nothing, which only
  grew. `window.LPIManager` is no longer assigned; nothing on the page read it.
- Pinned, not changed (tests named `PINNED:`): Clear empties the feed and the count but not the latest-per-sensor data, so a status card can return; a zero
  SNR shows as `--` and is not remembered; a recent classification with no classes or no SNR throws while its card renders; a sensor at latitude or
  longitude 0 gets no pulse; the simulation's request carries no session token and picks its sensor, family and SNR at random.

## Twelfth tool: operator presence (`scythe-web/presencePlugin.js`)

`OperatorPresence` (a 160-line object in `command-ops-visualization.html`) is now the `presence` tool. Once the page has handed over its viewer with
`initialize(viewer)` it asks the server who is on every 10 s, lists them in the Operators panel (the current operator marked "(You)"), and marks on the
globe where each of the others is looking. The Refresh button and the "Show operator positions on map" checkbox are inline `onclick` / `onchange`
attributes in the page that call `OperatorPresence` by name; the boot module sets `window.OperatorPresence` for them, for the viewer init, and for the
panel's active-operator count. What is shown is plain strings and data in the plugin (`renderPresenceList`, `indicatorsOf`); the page is touched
through `presenceSource` (the operator session), `presenceView` (the list element) and `presenceGlobe` (the Cesium markers).

- Deactivating stops the polling and takes the markers down (the old `stopTracking` had no caller); a refresh that finishes afterwards does nothing. A
  viewer handed over before the tool is active is kept and used when it activates.
- Callsigns, roles and ids are escaped where they reach HTML: a callsign is chosen by the operator who registers it, so it comes from other operators.
- The markers are worked out before the old ones come down, so a malformed view leaves the previous markers instead of a half-drawn set.
- Unchecking "Show operator positions on map" takes the markers down and keeps them down while it is unchecked (the old code left them on the map, frozen,
  until reload).
- Pinned, not changed (tests named `PINNED:`): one operator whose view centre is not a pair stops the list updating (the error is logged); nothing is polled
  until `initialize` has been called, though the Refresh button works before that.

## Not done

- Only ADS-B, AIS streaming, chat, metrics, the metrics explorer, propagation, the entity stream, sensors, the fusion overlays, the viewsheds, LPI and operator presence use the contract, and activation is not yet driven by the operator (a panel click).
- No unified transport: the tools take the realtime socket, `WebSocketSync` and `fetch` as services; they do not open their own.
- Not run in a browser against a signed-in session; the tests cover the contract, the plugin, the boot wiring, and run the
  page's own `removeEntityMarker` against a fake collection.
