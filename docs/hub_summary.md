# Hub summary

`rf_scythe_home.html` is the Mission Hub: an operator console, not a product page. It answers what state each SCYTHE instance is in
and where to go next, and nothing else. It shows what each instance reports about itself before anything is visualized.

## The page

- **State bar:** the operator (from the session), instances running of total, whether the orchestrator is reachable, and a UTC clock.
  The instance and orchestrator figures come from the same poll as the cards, so they cannot disagree with them.
- **Left navigation:** Dashboard, New instance, Connect to an instance, Documentation, Account (dialog) and the account page, Logout.
- **Dashboard:** the fleet line, then one card per instance.
- **Kept from before, unchanged:** the auth gate, the account dialog (profile edit, TOTP), the new-instance and join dialogs, and
  terminating an instance from the join list.

## Removed, and why

The hero banner and slogans; the nine demo cards (RF Visualization, the Cesium client card, LHC, Remora fleet, X-37B, JWST, missile
demo, tactical demo, multi-plane infographic); the Mastodon, X, WordPress and About links; the animated background and its credit; the
loading screen (a timer, not a loading state); the copyright footer; and the old **System Status** panel, which was static HTML with
invented values ("SDR Interface: Melted Down", "NeRF Engine: Online"). Per-instance health below replaces it. No file was deleted: the
demo pages are still in the repository and only the page's references to them are gone. They belong in documentation, not on a console.

The fleet line adds up only what instances reported and says how many did not, so a total is never shown as complete when it is not.

Logic in `scythe-web/hubSummary.js` (pure), the panel in `scythe-web/hubSummaryView.js`, checked on the real page by
`e2e/hub_summary/run.py`.

## Where the data comes from

| Shown | Source | Needs a login |
|---|---|---|
| status, socket ready, last health check | `GET /api/scythe/instances` (orchestrator, polled every 10 s) | no |
| nodes, edges, sessions, BSGs | that response's `authority.authoritative_state` (the instance's `/api/authority/state`), else its `info` | no |
| graph engine, authority store, LLM | `authority.health` | no |
| write gate and its reason | `authority.write_access` | no |
| missions: counts by status, the five newest | `GET /scythe/i/<id>/api/missions` through the orchestrator's proxy, **only when a card is expanded** | no |

`GET /api/missions` has no limit and a long-lived instance can hold thousands, so missions are not polled with the instance list;
an expanded card re-reads them once a minute.

## Rules

- **Nothing is inferred.** A field an instance does not report is `not reported` or `unknown`, never a default that looks healthy.
- A health check older than 30 s is `stale`; a dead instance is `down`; an orchestrator that does not answer is said, and a later failed
  poll keeps the last report and says so.
- The write gate means "an operator session exists", not "this operator may write"; the panel says what the instance says.
- Links go to that instance's views and carry the mission, through the deep-link contract (`docs/deep_link_contract.md`).

## Not shown, and why

Evidence totals, "second operator waiting", pending approvals and Gate D. None is reported without a per-instance login (approvals
need an instance session) or at all (see `docs/globe_command_instrument_backend_audit.md`).

## Go to… (Ctrl+K)

`scythe-web/commandPalette.js` (pure) and `commandPaletteView.js` (the dialog) give the hub a jump list, opened with Ctrl+K / Cmd+K or the nav's "Go to…" button.

It offers only what exists:

- the hub's own actions (New instance, Connect, Account, Documentation, Logout);
- Globe, Regional and Mission Ops for every instance that is **running**;
- a mission's views only when the hub has **already loaded** that instance's mission list (by expanding its card). The palette never fetches, because `GET /api/missions` has no limit.

An instance that is not running, or whose id cannot go in a URL, offers nothing, and the palette says how many were left out. Mission links are the deep links of `deep_link_contract.md`; an unusable mission id yields a link without it.

Matching: every word of the query must appear in the label; labels that start with, then have a word starting with, the first word rank first. It does not search entities, alerts or evidence, because the hub has no source for them.
