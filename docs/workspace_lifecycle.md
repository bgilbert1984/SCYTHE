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

## Not done

- No other subsystem uses the contract, and activation is not yet driven by the operator (a panel click).
- No unified transport: ADS-B still takes the realtime socket and `fetch` as services, it does not open its own.
- Not run in a browser against a signed-in session; the tests cover the contract, the plugin, the boot wiring, and run the
  page's own `removeEntityMarker` against a fake collection.
