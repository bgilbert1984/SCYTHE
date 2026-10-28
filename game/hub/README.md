# Player Hub

The player-facing entrypoint for SCYTHE: GAME: New Investigation, Continue, Career. It is separate
from the operator Mission Hub and shares nothing with it.

    node game/hub/build.js        # writes game/hub/dist/index.html
    # then open that file in a browser

The build inlines the runtime engine, the case, and the UI into one file. It opens from disk with
no server, no network (a Content-Security-Policy blocks it), and no install.

The in-progress investigation is kept in the browser's `localStorage` as a versioned save file (see
design §7), alongside the career history. If storage is blocked the page says so and still works for the
session. "Export save file" downloads the same file; "Open Save File" loads one, and a finished save
opens its debrief with the result re-verified by replay.

From the command line: `node game/runtime/cli.js --save run.json` writes a save, and
`node game/runtime/cli.js --replay run.json` verifies one (non-zero exit if it cannot be replayed).
