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

## Not done

- Only ADS-B and AIS streaming use the contract, and activation is not yet driven by the operator (a panel click).
- No unified transport: the tools take the realtime socket, `WebSocketSync` and `fetch` as services; they do not open their own.
- Not run in a browser against a signed-in session; the tests cover the contract, the plugin, the boot wiring, and run the
  page's own `removeEntityMarker` against a fake collection.
