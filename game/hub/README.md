# Player Hub

The player-facing entrypoint for SCYTHE: GAME: New Investigation, Continue, Career. It is separate
from the operator Mission Hub and shares nothing with it.

    node game/hub/build.js        # writes game/hub/dist/index.html
    # then open that file in a browser

The build inlines the runtime engine, the case, and the UI into one file. It opens from disk with
no server, no network (a Content-Security-Policy blocks it), and no install.

Saves are a prototype: the in-progress investigation (case, seed, action log) and the career history
are kept in the browser's `localStorage`. If storage is blocked the page says so and still works for
the session. The real save format is #361.
