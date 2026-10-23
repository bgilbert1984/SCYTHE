# Hub summary on the real home page

Serves the repository root the way the orchestrator does, including the `/scythe/i/<instance>/` prefix, answers
`/api/scythe/instances` and an instance's `/api/missions` with canned data, opens `rf_scythe_home.html` in headless Chromium
(software WebGL), and checks what the instrument panel shows. It checks logic, not appearance.

```
python e2e/hub_summary/run.py
```

It reuses the canned-API server of `e2e/globe_picking/run.py`, so it needs the same dependencies (Playwright and its Chromium) and
network access for the page's CDN scripts. The home page redirects to `login.html` without a session, so the harness seeds a canned
one that the canned `/api/operator/session` accepts; the page's own auth gate runs and passes.

## What it asserts

| Case | Expected |
|---|---|
| three instances (running, one that reports almost nothing, dead) | one card each |
| counts | taken from the authority state: `2183 nodes · 8417 edges · 2 sessions · 4 BSGs` |
| health | instance ok, graph engine ok, authority store `postgres`, LLM down, writes locked with the instance's own reason |
| an instance that reports nothing | `not reported` counts and `unknown` chips, not defaults that look healthy |
| a dead instance | shown down, no Missions button |
| links | into that instance's views under `scythe/i/<id>/` |
| missions before a card is expanded | none fetched |
| expanding a card | 7 missions counted by status (`4 open, 2 closed, 1 other`), the 5 newest listed newest first, and it says it is showing the newest few |
| a mission's link | carries `?mission=`, and following it opens that instance's globe on that mission |
| orchestrator returns 500 | "Orchestrator unreachable (500)", not an empty list |

Exit status: 0 passed, 1 a case failed, 2 infrastructure failure.
