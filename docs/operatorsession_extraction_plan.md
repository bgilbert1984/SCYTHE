# OperatorSession extraction plan

Status: plan only. Nothing here is implemented. It is written so each step can be agreed, built and merged on its own.

Progress: step 2 (escaping the operator's identity where it reaches HTML) is done, and step 1 (pinning the object, `scythe-web/pageOperatorSession.test.js`, and removing its dead code) is done, and step 3 (the three callbacks are now `login` / `restored` / `logout` events, with their effects in `wireOperatorSessionEffects`) is done. Steps 4 and 5 are not started.

`OperatorSession` is the last of the big page objects: about 315 lines in `command-ops-visualization.html`, 25 members. It is small, but it is the one every
other piece leans on. The `EntitySync`, rooms, recon, sensors and websocket-sync tools read its token through the `operatorSession` service, the presence tool reads
its operator list and id, and about fifty places in the page and one in `cesium-hypergraph-globe.js` read its properties directly. A mistake here does not break
one panel: it signs everything out, or leaves everything signed in. This plan is therefore more about pinning what it does today, and deciding what it should
do, than about moving code.

## 1. What it does

| Job | Members | Notes |
|---|---|---|
| **State** | `operatorId`, `operatorName` (it holds the email), `callsign`, `role`, `teamId`, `sessionToken`, `sessionId`, `isAuthenticated` | Written only by `_setSessionData` and `clearSession`. Nothing outside the object writes to it. |
| **Restore and validate** | `init`, `validateSession` | `init` asks `SCYTHE_AUTH.restoreSession()` for a token and validates it with `SCYTHE_AUTH.validateSession`. |
| **Login, register, logout** | `login`, `register`, `logout`, `clearSession`, `_setSessionData` | Three REST routes (`/api/operator/login`, `/register`, `/logout`). `register` logs in afterwards. |
| **Persistence** | `_setSessionData`, `clearSession`, `getCookie`, `setCookie`, `clearCookie` | `localStorage.operatorSession`, `localStorage.scythe_session_token`, and the cookie `operatorSessionToken`. |
| **Heartbeat** | `startHeartbeat`, `stopHeartbeat`, `HEARTBEAT_INTERVAL` | Every 30 s a POST to `/api/operator/heartbeat` with the camera's centre, altitude and heading. |
| **Presence feed** | `getActiveOperators` | `/api/operator/active`; read through the presence tool's `presenceSource` service. |
| **Effects** | `onLoginSuccess`, `onSessionRestored`, `onLogout` | The glue: console message, `updateOperatorStatusUI()`, GraphOps rebind or reset, `WebSocketSync` or `EntitySync` connect or disconnect, `RoomManager` fetch or reset, `populateRooms()`. The comments say "can be overridden"; nothing overrides them. |

The page's login and register dialog (`showOperatorLoginDialog`, `doOperatorLogin`, `doOperatorRegister`) and `updateOperatorStatusUI` are page code outside the object.

## 2. What I found (read first)

1. **There are two session stores.** `SCYTHE_AUTH` (`assets/js/shared_auth.js`) is the real authority: the page's `requireAuth` guard redirects to `login.html` without a
   token, the login page (`login.html`, through `assets/js/login_page.js`) signs in through it, and it keeps the token under per-instance keys (`scythe:<instance>:operatorSession` and a matching cookie, or `sessionStorage`
   for the broker role) and in `ScytheTransport`. `OperatorSession` keeps its own copy under different, un-namespaced keys (`operatorSession`, `scythe_session_token`, the cookie
   `operatorSessionToken`) and never updates `SCYTHE_AUTH` or `ScytheTransport`. `cesium-hypergraph-globe.js` reads `scythe_session_token` and `SCYTHE_AUTH.restoreSession()`.
2. **Logout clears only the legacy copy.** `clearSession` removes `localStorage.operatorSession` and the cookie `operatorSessionToken`; it does not call `SCYTHE_AUTH.logout()` and
   does not clear the transport's token. The per-instance token survives an in-page logout, and the next load can restore it
   through `requireAuth`. Reproduced in step 1 in a browser with the real `shared_auth.js`, a dummy token and local routes: after `OperatorSession.logout()` the `SCYTHE_AUTH` stores and
   `scythe_session_token` are still there, `SCYTHE_AUTH.isAuthenticated()` is still true, and after a reload `restoreSession()` still returns the token. Whether the page then signs in again
   depends on the role: for the broker role `requireAuth` also asks the server, which has dropped the session; for an instance server the check is local (finding 3). The `EntitySync` 401 path (`operatorSession.clear`) goes through the same `clearSession`. What logout should clear is decision 1.
3. **Validation on the client is a local check.** For an instance server `SCYTHE_AUTH.validateSession` decodes the token and checks `exp` (and optionally the issuer and audience);
   only the broker role asks the server. So `isAuthenticated` is for showing and hiding things, not a security boundary. The server has to enforce, as it does on each request.
4. **An unescaped sink:** `updateOperatorStatusUI` puts `OperatorSession.callsign` into `innerHTML` and `OperatorSession.role` into a class attribute. Operators choose their callsign when they register.
5. **Dead or odd:** `getCookie` has no caller; the panel hook `OperatorSession.refreshPresence` calls a method that does not exist, so it never runs; the page's `sessionToken()` helper falls back to `SCYTHE_AUTH.getSessionToken?.()`,
   a function `SCYTHE_AUTH` does not have; `_setSessionData` logs its line twice; `operatorName` holds the email.
6. **Who reads what.** Outside the object, in the page: `sessionToken` 23 times, `isAuthenticated` 9, `callsign` 4, `role` 3, `teamId` 2, `operatorName` 1; methods `init` (once, at viewer init), `login`,
   `register`, `logout` (the logout button's inline `onclick`) and the `typeof OperatorSession !== 'undefined'` guards. In the boot module: `sessionToken`, `clearSession`, `getActiveOperators`, `operatorId`.
7. **The token travels in some URLs** (`/api/entities/stream?token=`, and the socket.io URL that the websocket tool builds), because `EventSource` and the socket handshake cannot send a header. That is as it was; the plan does not change it.
8. **The heartbeat reads the camera** (`window.viewer.camera.positionCartographic`, `Cesium.Math`), so it belongs behind the `cameraView` service the AIS tool already uses.
9. **Ordering:** `OperatorSession.init()` runs inside the viewer init (after the boot module), and starts the heartbeat and the effects only if the restored token validates. The boot module's tools read the token lazily, so they do not need it at activation.
10. **The login dialog may be vestigial.** Without a valid token the page redirects before it can be used; the dialog is reachable after a logout, or by the "Login" buttons in the operator panel.

## 3. Target shape

```
session tool  (scythe-web/sessionPlugin.js, tool id "session")
├── pure:  operatorFromLogin(data), operatorFromClaims(claims), statusBadge(operator) (escaped), heartbeatBody(view), heartbeatView(camera)
└── services
    ├── httpFetch        (exists)
    ├── sessionStore     SCYTHE_AUTH + ScytheTransport + (per decision 2) the legacy keys: restore() / persist(token) / forget() / validate(token)
    ├── cameraView       (exists; adds the heartbeat's centre, altitude and heading)
    ├── sessionConsole   the page's console messages, escaped
    └── session events   login | restored | logout, as on/off, instead of the three callbacks
boot module (the subscribers; the tool no longer knows about them)
├── on login/restored: GraphOps rebind, connect (WebSocketSync if it is on offer, else EntitySync), RoomManager fetch, populateRooms, status UI
└── on logout:         GraphOps reset, disconnect both, RoomManager reset, status UI
controls  window.OperatorSession (same names): sessionToken, isAuthenticated, callsign, role, teamId, operatorId, operatorName, sessionId as read-only getters;
          init, login, register, logout, clearSession, getActiveOperators
```

The reads of `window.OperatorSession.sessionToken` in classic page scripts stay as they are (a classic script cannot use a service); the getters keep them working.

## 4. Steps

Each step is its own PR, in this order. The first three change no storage and no session logic.

1. **Pin it.** Tests that run the page's own `OperatorSession` against fakes (`fetch`, `localStorage`, `document.cookie`, `SCYTHE_AUTH`, `setInterval`, `Cesium`): restore with a good and a bad token,
   login, register, logout, `clearSession`, what each persists and removes, the heartbeat body, the three callbacks' effects. Remove only the code with no effect: `getCookie`, the doubled log line, the
   `refreshPresence` hook, the `getSessionToken` fallback. No behaviour changes.
2. **Escape the status UI** (`updateOperatorStatusUI`: callsign as text, role as a safe class), with tests against hostile callsigns and roles and one real-browser check, as in #327.
3. **Turn the callbacks into events and move the effects out.** Inside the page object: `on`/`off`, `login`/`restored`/`logout` events, and the GraphOps, stream, room and UI wiring moved into subscribers
   next to the object. Tests pin the order of effects. The object's state and storage are untouched.
4. **Extract the tool** (state, restore, validate, login, register, logout, heartbeat, active operators) as the `session` tool with `sessionStore` doing exactly what the object does now (legacy keys
   included, unless decision 1 or 2 says otherwise), the subscribers moved to the boot module, `window.OperatorSession` set to the tool's controls, and the `operatorSession` service pointing at it.
   Differential test against the old object, as for the others.
5. **Only if decisions 1 and 2 ask for it: make `sessionStore` the one store.** Logout clears what `SCYTHE_AUTH` keeps too; the legacy keys are written, or not, as decided. This is the one step that
   changes behaviour users can see, so it is its own PR with its own manual checklist.

## 5. What to pin (tests named `PINNED:`, changed only in their own PRs)

- `clearSession` removes `localStorage.operatorSession` and the cookie `operatorSessionToken`, and does not touch `SCYTHE_AUTH`'s keys or the transport token (step 5 decides).
- `_setSessionData` writes `localStorage.operatorSession` (as `{sessionToken}`), the cookie (7 days) and `scythe_session_token`, and each write is allowed to fail without stopping the others.
- `init` does nothing without a token; with one it keeps it, validates it, and only a valid one starts the heartbeat and the restored effects; an invalid or throwing validation clears the session.
- `register` logs in afterwards, and returns login's result; a registration the server refuses returns its message.
- `login` and `logout` call their effects only on success; `logout` clears and runs its effects even if the request fails; `logout` does nothing without a token.
- The heartbeat does nothing without a token, sends `current_view: null` without a viewer, and a failing request is only logged.
- On login or restore: WebSocketSync connects if `useWebSocket`, else EntitySync; the rooms are fetched; on logout both are disconnected and the rooms are reset.
- The 401 path (`operatorSession.clear`) is `clearSession` without the logout effects.

## 6. Risks

- **Silent sign-out or sign-in.** The biggest risk. Everything reads the token lazily, so a wrong getter shows up as 401s on the streams and the room lists, not as an error at load. Step 4's differential and a signed-in checklist are the guard.
- **Storage keys other pages read.** `cesium-hypergraph-globe.js` reads `scythe_session_token` and `SCYTHE_AUTH.restoreSession()`, and several standalone pages (`cesium-hypergraph-globe.html`, `user_account.html`, `rf_scythe_home.html`, the password pages) use `SCYTHE_AUTH`. Step 4 keeps every key exactly; only step 5 may change them.
- **I cannot sign in from here.** Every test uses opaque dummy tokens (`"tok"`) and fakes. Nothing is forged, and nothing is sent to the live server. The manual checklist below is the only check of the real flow.
- **The effects order** (GraphOps first, then the streams, then the rooms, then `populateRooms`) is load-bearing in ways the code does not say; step 3 exists to pin it before it moves.
- **`SCYTHE_AUTH` is shared** by `login.html` and the other pages; this plan does not change it.

## 7. Decisions needed

1. **Logout:** should it also clear `SCYTHE_AUTH`'s storage and the transport token (finding 2)? Recommended: yes, as step 5, after the tool exists, so the change is one small PR. The alternative is to leave it and track it as an issue.
2. **The legacy keys** (`operatorSession`, `scythe_session_token`, the `operatorSessionToken` cookie): keep writing them, or let `SCYTHE_AUTH`'s keys be the only copy? Recommended: find the readers first (the globe reads one), then retire the ones nothing needs.
3. **The in-page login dialog:** keep it (the plan keeps it by default), or remove it in favour of `login.html`?
4. **Escaping the status UI** (step 2): recommended, as with every other panel.
5. **Steps 2 and 3 split or together:** they are independent; split keeps each review small.
6. **Server-side questions** that came up while reading are tracked separately from this plan.

## 8. Manual checklist for step 4 (and 5), signed in

1. Load the page signed in: the status badge shows the callsign and role, the Operators panel lists operators (and you are marked "(You)"), the streams connect, and the rooms load.
2. In the browser's network tab: a heartbeat POST every 30 s, carrying the token header and a `current_view` that follows the camera.
3. Click Logout: the badge becomes "Not logged in", the Login button returns, both streams disconnect, the room lists empty, and GraphOps resets.
4. Reload after logout, and note what happens (today the page may restore the session: decision 1). Compare with what step 5 intends.
5. Log in through the in-page dialog (if it is kept): the badge, the streams and the rooms come back; register the same way with a test callsign.
6. Let a request return 401 (for example by letting the session expire): the session clears and the streams stop retrying forever.
7. Open a second tab: it restores from the stored session and shows the same operator.
