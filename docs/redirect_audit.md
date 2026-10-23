# Navigation audit: where a page sends the browser

Scope: every `location.href =`, `location.assign/replace` and `window.open` in the repository's own pages and scripts (not the vendored Cesium, `node_modules` or the bundled `assets/cell-architecture-studio-main`), found with a grep on 2026-10-23. Static reading only; nothing was exploited.

| Where | Destination comes from | Disposition |
|---|---|---|
| `assets/js/login_page.js` (3 places) | `?next=` | **Fixed in #295**: `safeNext` (same-origin path only). |
| `assets/js/shared_auth.js` `login()` | `?instance=` (or the instance in `next`'s path), put in `/scythe/i/<id>/…` | **Fixed here**: the id must match the instance-id rule or sign-in ignores it (login page) / refuses (`login()`). An id such as `../../x`, `a/b` or `a?x=1` used to be placed in the path as it was. |
| `rf_scythe_home.html` spawn status | the new-instance response (`name`, `port`, `instance_id`, `visualization_url`) | **Fixed here**: shown as text; the link is offered, and opened automatically, only if it is an `http(s)` URL (a `javascript:` value was linked and opened after 2 s). |
| `rf_scythe_home.html` `joinInstance` | an instance id from the orchestrator's own list | Left: the orchestrator is the trusted source and the id is one it issued. It is not validated on this side. |
| `assets/js/shared_auth.js` `requireAuth` | the page's own path and query, as `next` for login | Left: it only builds `next`, which login now checks. |
| `command-ops-visualization.html` ~34807 | `authorize_url` from `/api/cloud/oauth/start` | **Not changed.** `window.open` of a server-supplied URL with no scheme check. The server is the trusted source, so this is a hardening gap, not a known path. |
| `command-ops-visualization.html` ~35264 | `authorization_url` passed through `apiPath`, opened `noopener,noreferrer` | Left. |
| `scythe-web/commandPaletteView.js` | links built by `instanceDeepLink` (validated id) | Left. |
| `reset-password.html`, `register_page.js`, `user_account.html`, `cesium-hypergraph-globe.html` nav button, emergency redirect in command-ops | fixed literal paths | Left. |

Not covered: anchors (`<a href>`) whose target comes from data, `<form action>`, `meta refresh`, and server-side redirects (the orchestrator's proxy and login handlers). A grep for those was not done.
