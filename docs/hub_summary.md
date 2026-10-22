# Hub summary

The instrument panel on `rf_scythe_home.html`: what each SCYTHE instance reports about itself, before anything is visualized.
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
