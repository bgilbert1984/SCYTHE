# Deep-link contract on the real pages

Opens the globe, the regional page and Mission Ops in headless Chromium (software WebGL, canned API) with `?mission=` and
`?select=`, and checks what each page does with them. It checks logic, not appearance.

```
python e2e/deep_link/run.py
```

It reuses the canned-API server of `e2e/globe_picking/run.py`, so it needs the same dependencies (Playwright and its
Chromium) and network access for the Cesium, three.js and Socket.IO CDNs.

Mission Ops redirects to `login.html` without a session, so the harness seeds a canned one (`sessionStorage`
`scythe:orchestrator:brokerSession`) that the canned `/api/operator/session` accepts. The page's own auth guard runs and passes;
it is not stubbed.

## What it asserts

| Page | Opened with | Expected |
|---|---|---|
| Globe | `?mission=m1&select=n3` | `n3` is selected once it is on the globe; the mission comes from the link; the links to Regional and Mission Ops carry both |
| Globe | then selecting `n5`, then clearing | the links follow the selection and drop it |
| Globe | `?mission=bad%20id&select=n3` | "ignored in link: mission" is shown; the mission is not used and is not forwarded; the selection still is |
| Regional | `?mission=m1&select=tower-7` | links to the Globe and Mission Ops are relative to its own directory (`../…`) and carry both; the mission reaches its governance panel |
| Mission Ops | `?mission=m1&select=n3` | its Globe Intel and Regional RF links carry the context |
| Globe, clicking Regional | | lands on the regional page with the context, whose link back to the globe carries it on |

Exit status: 0 passed, 1 a case failed, 2 infrastructure failure (Playwright or Chromium missing, a page or its CDN scripts did not load).
