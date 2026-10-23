# Deep-link contract

How one SCYTHE page hands its context to another through the URL alone. Implemented in `scythe-web/deepLink.js`
(parsing, building, selection) and `scythe-web/deepLinkNav.js` (the row of links); checked on the real pages by
`e2e/deep_link/run.py`.

| Parameter | Meaning | Valid when |
|---|---|---|
| `mission` | the mission the page shows | `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`, the server's rule (`scythe_mission.is_valid_mission_id`) |
| `select` | the object to select once it is on the page | 1 to 256 characters, no control characters, not blank |

Rules:

- **Nothing else travels in a link.** A session or bootstrap token is never forwarded; each page authenticates for itself.
- **A value that is present but invalid, or given twice, is ignored and said so**, never repaired or guessed. The page shows
  "ignored in link: mission" and does not forward it.
- **Links are relative**, so they keep whatever prefix the page is served under (`/scythe/i/<instance>/`). The home page is not a
  target: its path from an instance prefix is not relative.
- **A page forwards what it was given even if it cannot use it.** Mission Ops does not load a mission from the server (its mission
  panel shows a static example package), so it only carries `mission` and `select` on to the Globe and Regional.

| Page | `mission` | `select` |
|---|---|---|
| Globe | drives the mission viewport and governance | selects the node once the graph has loaded it (waits up to 30 s) |
| Regional | drives the governance panel | carried in its links; it has no node selection to apply it to yet |
| Mission Ops | carried in its links | carried in its links |

## Known limits

- Opening Mission Ops with a `bootstrap_token` makes it strip the query after the token exchange. The links capture the context
  before that, but this path is not covered by the harness.
- Without a session Mission Ops redirects to `/login.html?next=<path+query>`; the context survives in `next`, but whether
  `login.html` honours `next` was not checked.
- The context is a hint to the page, not an authority. Whether the mission exists, and whether the operator may see it, is still
  decided by the server.
