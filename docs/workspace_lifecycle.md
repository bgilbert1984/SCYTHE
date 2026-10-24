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

## Not done

- Only ADS-B, AIS streaming, chat and metrics use the contract, and activation is not yet driven by the operator (a panel click).
- No unified transport: the tools take the realtime socket, `WebSocketSync` and `fetch` as services; they do not open their own.
- Not run in a browser against a signed-in session; the tests cover the contract, the plugin, the boot wiring, and run the
  page's own `removeEntityMarker` against a fake collection.
