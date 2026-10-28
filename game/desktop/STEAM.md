# Steam integration (#363)

Achievements and Steam Cloud saves, behind a storefront-agnostic service. **Not yet tested against Steam
itself**: this was built and checked without a Steam client, a partner account, or an app id.

## How it works

    page (sandboxed)  --scythePlatform.saved(text)-->  main process  --> platform/service.js --> provider
                                                                                              (Steam, or none)

- The page can call exactly three functions: `status()`, `saved(text)`, `start(text)`. Each takes and
  returns plain data. The main process only answers the game page.
- `platform/service.js` **replays every save** (case hash, checksum, every action) before it trusts it.
  A forged or edited save earns nothing and is not copied to the cloud.
- **Achievements** (`platform/achievements.js`) are computed from the replayed game. Granting twice is harmless.
- **Cloud saves** hold only the investigation in progress, as the same save file the Hub exports. At start-up
  the two copies are compared by action log: if one is the other plus later moves, the longer wins; if they
  differ in any other way the player is asked. A finished game leaves the cloud slot.
- Everything is optional. With no Steam, no app id, or no `steamworks.js`, the game runs exactly as before
  and the shell logs why on stderr.

## Build a Steam package

Needs Node >= 22.12 and a Steam app id. Valve's public test app is `480`.

    npm run package:linux -- --steam --app-id 480
    npm run package:win   -- --steam --app-id 480

`--steam` bundles `steamworks.js@0.4.0` (unpacked from the asar, since it loads native libraries) and writes
the app id into the shipped `steam.config.json`. Without it, the app contains no Steam code beyond a
loader that gives up quietly, and the committed config has `appId: 0`. `SIGNAL_GHOST_STEAM_APPID`
overrides the id for local runs.

## Upload

    node scripts/steam-build.js vdf --app-id N --linux-depot D1 --win-depot D2
    steamcmd +login <account> +run_app_build out/steam/app_build.vdf +quit

Register the launch options (`signal-ghost` on Linux, `signal-ghost.exe` on Windows) in the partner site.
`node scripts/steam-build.js achievements` prints the ids, names, and descriptions to register there.
Achievement ids must match exactly.

## Checked here

- Unit tests with a fake Steam client: contract, forged saves, conflicts, provider failures, disabled Steam Cloud.
- A `--steam` Linux package builds, `steamworks.js` loads from the unpacked asar, and with no Steam running the
  game falls back and passes the smoke test (online and with no network interface).
- The Hub's bridge handling in Chromium with a stub bridge: forwarding saves, showing unlocks, restoring a
  cloud save, ignoring a damaged one.

## Not checked

- **Anything against real Steam**: `init` succeeding, achievements appearing, Steam Cloud syncing between
  two machines, the overlay. The done criterion for #363 needs a test app and a Steam client.
- The Steam **overlay under Electron** (design decision 1 flagged this).
- Windows and macOS builds. The `--steam` package also carries the other platforms' Steam libraries
  (a few MB), which could be pruned.
- **Storefront assets** (capsule art, screenshots, trailer, store text) are not made.
- The Steam Cloud quota, file-name settings, and the "Steam Cloud" toggle in the partner site must be
  configured by hand for the file `signal_ghost_in_progress.json`.
