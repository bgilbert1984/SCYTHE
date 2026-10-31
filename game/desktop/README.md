# Desktop prototype

An Electron shell around the built player Hub. The shipped app contains the Hub page, the shell, a smoke check, a small preload bridge, and the engine
code needed to verify saves: no repository, no `node_modules`, no SCYTHE
service code, no hardware access. It runs sandboxed with no Node access and refuses every network
request, popup, navigation, and permission prompt.

## Build (needs Node >= 22.12)

Electron 45 and `@electron/packager` require Node 22.12 or newer. On older Node, `npm install` only
warns and the Electron binary is not downloaded.

    cd game/desktop
    npm install                  # also fetches the Electron binary (postinstall)
    npm run package:linux        # out/signal-ghost-linux-x64/ and .tar.gz (+ .sha256)
    npm run package:win          # out/signal-ghost-win32-x64/ and SCYTHE-GAME-win-x64-portable.zip (+ .sha256)

`npm start` runs the app from source in a window. Output is in `out/`, which is git-ignored.

## Verify

    npm run smoke                              # launch twice on one profile, check sandbox + restart
    node scripts/smoke-run.js --offline        # same, with no network interface at all (Linux)
    node scripts/smoke-run.js path/to/signal-ghost
    node --test game/                          # repo-level checks (no Electron needed)

The smoke run starts an investigation, quits, relaunches on the same profile, and requires Continue to
restore it. It also checks from inside the page that Node globals are absent and that `fetch`, XHR,
popups, and navigation are blocked. `--offline` runs the app inside an empty network namespace
(`unshare -Un`). Without a display it uses `--ozone-platform=headless`, which runs the page but cannot
render pixels; pass `--display` on a machine with a screen.

## Install (Linux)

    tar -xzf signal-ghost-linux-x64.tar.gz
    cd signal-ghost-linux-x64
    ./install-linux.sh                 # per user, into ~/.local; --prefix DIR to change
    ./install-linux.sh --uninstall     # saved games are kept

Verify the download with `sha256sum -c signal-ghost-linux-x64.tar.gz.sha256`.

## Known limits

- **Windows is smoke-tested in CI, not yet by a person.** The `game-windows` workflow builds on a
  Windows runner, extracts `SCYTHE-GAME-win-x64-portable.zip` to a clean folder, launches it twice on
  one profile, and repeats with outbound traffic firewalled for the executable. That is not a clean
  Windows 11 machine, a visual check, keyboard-only or screen-reader testing, or SmartScreen behaviour.
  Saves live in `%APPDATA%\signal-ghost`.
- **No installer yet** for either platform (AppImage/deb, NSIS/MSI), and no code signing.
- **Linux sandbox.** The app keeps Chromium's sandbox on and relies on unprivileged user namespaces.
  Hosts that disable them (some hardened distributions) will refuse to start it. It does not fall
  back to `--no-sandbox` silently.
- **Size:** about 274 MB unpacked, 124 MB compressed, almost all Electron itself.
- **Steam** is integrated but untested against Steam itself; see [STEAM.md](STEAM.md).
