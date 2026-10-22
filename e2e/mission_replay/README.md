# Mission replay invariants on the real page

Loads `cesium-hypergraph-globe.html?mission=m1` in headless Chromium with software WebGL and canned mission events, draws
edges through the page's own handlers, scrubs the mission timeline, and asserts which edges are hidden and how each edge
event was matched to a drawn edge. It checks logic, not appearance, so it does not depend on how Cesium renders.

```
python e2e/mission_replay/run.py
```

It reuses the canned-API server of `e2e/globe_picking/run.py`, so it needs the same dependencies (Playwright and its
Chromium) and network access for the Cesium, three.js and Socket.IO CDNs.

The page draws synthetic proximity arcs (`prox:<a>→<b>`) between nearby nodes from about 8 s after load. One of them can share
its ends with a real edge, which the matcher treats as ambiguous and leaves unmatched, so the harness turns that generator off
and removes what it drew, and tests only the edges it draws itself.

## What it asserts

| Drawn edge | Event | Expected |
|---|---|---|
| `edge-by-id` | `EDGE_CREATE` with the same id | matched **by id**; hidden before its creation |
| `drawn-B` | `EDGE_CREATE` with a different id, same two ends | matched **by endpoints**; hidden before its creation |
| `edge-C` | none | never hidden |
| `dup-1`, `dup-2` | one event whose ends match both | **unmatched** (ambiguous), neither hidden |
| none | an event naming no ends | **unmatched** |

Also: live hides nothing, every scrub position hides exactly the expected set and the drawn arcs agree, Live restores
everything, the tooltip reports `1 by id, 1 by their ends, 2 not matched`, and the page raises no script errors.

Exit status: 0 passed, 1 a case failed, 2 infrastructure failure.
