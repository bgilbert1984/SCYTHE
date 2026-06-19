# Edge Streaming Protocol Specification

## Overview

Edge Streaming is a **pull-based WebSocket protocol** for on-demand hypergraph edge delivery. Instead of sending the entire graph to the browser, edges are **subscribed to by scope** and streamed only when needed.

**Key principle:** Edges do not flow unless explicitly requested.

---

## Architecture

```
Browser ─────────────┐
                     │ pull-based
                     ↓
Server (HypergraphEngine)
    │
    ├─ Maintains bounded, decayed edge set
    ├─ Filters by scope on subscription
    └─ Streams only applicable edges every ~1sec
```

### Why this works with decay

**Without decay:**
- Edge count grows unbounded
- Streaming becomes firehose
- WebSocket becomes DOS vector

**With decay:**
- Edge sets naturally bounded
- Subscriptions self-quiet over time
- Streaming is efficient and safe

---

## Wire Protocol

### Client ➜ Server: Subscribe to edges

```json
{
  "op": "subscribe_edges",
  "scope": {
    "type": "cluster",
    "id": 7,
    "min_weight": 0.15,
    "since_secs": 300
  }
}
```

**Scope types:**

| Type      | Fields                          | Meaning                           |
|-----------|----------------------------------|----------------------------------|
| `cluster` | `id` (int), `min_weight`, `since_secs` | All edges touching cluster node  |
| `node`    | `id` (str), `depth` (int)        | All edges touching node (depth=1) |
| `time`    | `since_secs` (int), `min_weight` | All edges active in time window  |

**Parameters:**

- `min_weight` (float, default 0.01): only include edges with weight ≥ this
- `since_secs` (int, default 300): only edges from last N seconds
- `depth` (int for node scope, default 1): 1-hop or multi-hop neighbors

---

### Server ➜ Client: Subscription confirmed

```json
{
  "op": "subscribed",
  "scope_id": "scope-abc123def456",
  "scope": {
    "type": "cluster",
    "id": 7,
    "min_weight": 0.15,
    "since_secs": 300
  }
}
```

Keep the `scope_id` for later unsubscribe.

---

### Server ➜ Client: Edges delivered

Streamed periodically (~1 second tick) while subscription is active:

```json
{
  "op": "edges",
  "scope_id": "scope-abc123def456",
  "edges": [
    {
      "id": "edge-001",
      "src": "node-123",
      "dst": "node-456",
      "kind": "co_location",
      "weight": 0.44,
      "timestamp": 1772133000.123
    },
    {
      "id": "edge-002",
      "src": "node-123",
      "dst": "node-789",
      "kind": "dns_query",
      "weight": 0.71,
      "timestamp": 1772133001.456
    }
  ],
  "timestamp": 1772133002.789
}
```

**Edge fields:**

- `id` (str): unique edge identifier
- `src`, `dst` (str): node IDs connected by this edge
- `kind` (str): edge relationship type (e.g., "co_location", "dns_query")
- `weight` (float): **base** edge strength (pre‑decay).  Decay is applied client‑side
  via shader or manually; server may also filter by effective weight.
- `last_seen` (float): Unix seconds when the edge was most recently reinforced.  Used
  by the client shader to compute recency pulses and decay.
- `reinforcement_count` (int): how many times the edge has been upserted; useful for
  heartbeat intensity or analytics.
- `first_seen` (float, optional): original timestamp when the edge was created.
- `timestamp` (float): retained for backwards compatibility (same as `last_seen`).

**Delivery semantics:**

- Only sent if there are edges matching the scope
- Weight reflects decay: old, unreinforced edges have low weight
- Same connection serves all active subscriptions (no per-subscription WS required)

---

### Client ➜ Server: Unsubscribe

### Client ➜ Server: Scrub a subscription

```json
{
  "op": "scrub_edges",
  "scope_id": "scope-abc123def456",
  "timestamp": 1709251912
}
```

This instructs the server to evaluate the scope as if the current time were the
provided timestamp.  The backend will use `scrub_time` when filtering and
computing effective weights; edges older than `since_secs` relative to the
scrub time may reappear.  For performant scrubbing the client should prefer
GPU-based evaluation using the raw weight/last_seen fields and simply adjust
its `uScrubTime` uniform (see shader examples later).


```json
{
  "op": "unsubscribe_edges",
  "scope_id": "scope-abc123def456"
}
```

---

### Server ➜ Client: Unsubscribe confirmed

```json
{
  "op": "unsubscribed",
  "scope_id": "scope-abc123def456",
  "success": true
}
```

---

### Either direction: Error

```json
{
  "op": "error",
  "message": "scope_id required" | "scope not supported" | …
}
```

### Server ➜ Client: Warning (cap exceeded)

When a subscription would yield more edges than `MAX_EDGES_PER_SCOPE`, the
server trims the list to the highest-weight edges and sends a warning first:

```json
{
  "op": "warning",
  "scope_id": "scope-abc123def456",
  "message": "12345 edges exceeds cap 5000, truncated to top weights"
}
```

Clients may use this to adjust their `min_weight` or display a UI indicator.


---

## Integration with LOD (Level of Detail)

Edge streaming naturally complements the existing **edge LOD system**:

| Camera State | Edge Source              | Scope                  | Rationale                |
|--------------|--------------------------|------------------------|--------------------------|
| Far (z>million m) | None                  | N/A                    | Too small to see         |
| Mid (z=100km) | Static cluster edges    | Pre-computed graph     | Low cost, high coverage  |
| Near (z=10km) | **WebSocket stream**     | `{type: "cluster", ...}` | Live, focused data     |
| Inspect (z<1km) | Node-depth-1 stream    | `{type: "node", depth: 1}` | Drill-in details    |

**Implementation pattern:**

```javascript
// When user zooms in
function onCameraMove(cameraAltitude) {
    if (currentEdgeScopeId && cameraAltitude > 100000) {
        // Far: unsubscribe
        EdgeStreamingClient.unsubscribe(currentEdgeScopeId);
    } else if (!currentEdgeScopeId && cameraAltitude < 100000) {
        // Near: subscribe to cluster edges
        EdgeStreamingClient.subscribe({
            type: "cluster",
            id: viewedClusterId,
            min_weight: 0.15,
            since_secs: 300
        });
    }
}

// Listen for incoming edges
EdgeStreamingClient.onEdges((scopeId, edges) => {
    updateNodeEdgesBuffer(edges);
    renderToGPU();
});
```

---

## Frontend: Rendering streamed edges

### Simple pattern: replace BufferGeometry

When edges arrive, rebuild the GPU buffer:

```javascript
function updateNodeEdges(edgeList) {
    // Don't keep old edges; replace wholesale
    const positions = new Float32Array(edgeList.length * 2 * 3);
    let i = 0;

    for (const edge of edgeList) {
        const nodeA = nodePositions[edge.src];
        const nodeB = nodePositions[edge.dst];

        // Store A→B edge as line segment
        positions.set([
            nodeA.x, nodeA.y, nodeA.z,
            nodeB.x, nodeB.y, nodeB.z
        ], i);
        i += 6;
    }

    // Rebuild BufferAttribute
    edgeGeometry.setAttribute("position",
        new THREE.BufferAttribute(positions, 3));

    // Trigger render
    requestAnimationFrame(() => renderer.render(scene, camera));
}

// Wire up the callback
EdgeStreamingClient.onEdges((scopeId, edges) => {
    updateNodeEdges(edges);
});
```

### Advanced: incremental updates

For sparse updates, accumulate edges instead of replacing:

```javascript
let edgeMap = new Map();  // edge.id → edge

EdgeStreamingClient.onEdges((scopeId, edges) => {
    // Track which scope this batch came from
    for (const edge of edges) {
        edgeMap.set(edge.id, edge);
    }

    // Rebuild only if needed (check timestamp or count)
```

### GPU Shader for heartbeat & scrub

Below is a minimal GLSL fragment shader that demonstrates the heartbeat
pulse effect and time-scrubbing evaluation.  The uniforms `uNow`,
`uScrubTime` and `uDecayLambda` are provided by the renderer; attributes
`v_weight` and `v_lastseen` come from the streamed edge data.

```glsl
#version 300 es
precision mediump float;
in vec4 v_color;
in float v_weight;
in float v_lastseen;
uniform float uNow;
uniform float uScrubTime;
uniform float uDecayLambda;
out vec4 outColor;

float effectiveWeight(float base, float last, float evalTime) {
    float dt = evalTime - last;
    return base * exp(-uDecayLambda * dt);
}

void main(){
  float eval = (uScrubTime > 0.0) ? uScrubTime : uNow;
  float w = effectiveWeight(v_weight, v_lastseen, eval);
  float recency = clamp(1.0 - (eval - v_lastseen) / 10.0, 0.0, 1.0);
  float pulse = sin(eval * 4.0) * 0.1 * recency;
  float brightness = w + pulse;
  outColor = vec4(vec3(0.6, 0.8, 1.0) * brightness, w);
}
```

This shader:

* computes decay on the fly using the current or scrubbed time
* adds a sine pulse whose amplitude decays over ~10 s recency window
* outputs alpha = effective weight so edges disappear when they decay

Operators may plug this into the `WebGLHypergraphRenderer` as shown in
`command-ops-visualization.html`.
    if (edges.length > 0) {
        rebuildEdgeBuffer(Array.from(edgeMap.values()));
    }
});
```

---

## Backend: Edge selection logic

The server implements scope matching in `edge_streaming.py`:

```python
def matches_edge(self, edge, engine, now: float) -> Optional[float]:
    """Return effective_weight if edge matches this scope, else None."""

    # Age filter (respect since_secs)
    age = now - (edge.timestamp or now)
    if age > self.since_secs:
        return None

    # Weight filter (respect min_weight, applies decay)
    eff_w = edge.weight  # Already decayed by engine if configured
    if eff_w < self.min_weight:
        return None

    # Topology filter (scope-specific)
    if self.scope_type == "cluster":
        if self.cluster_id not in edge.nodes:
            return None
    elif self.scope_type == "node":
        if self.node_id not in edge.nodes:
            return None

    return eff_w
```

This leverages the **existing decay logic** from `HypergraphEngine`:

```python
# In decay loop (runs every ~60 sec):
for edge in engine.edges:
    age = now - edge.timestamp
    edge.weight *= math.exp(-decay_lambda * age)
```

---

## Example usage sequences

### Scenario 1: Operator zooms into cluster 7

**Step 1:** User zooms camera to altitude 50 km over cluster 7
```javascript
EdgeStreamingClient.subscribe({
    type: "cluster",
    id: 7,
    min_weight: 0.15,
    since_secs: 300
});
```

**Step 2:** Server responds with `scope_id: "scope-xyz789"`

**Step 3:** Server begins 1-second tick, sending edges:
```json
{
  "op": "edges",
  "scope_id": "scope-xyz789",
  "edges": [
    { "id": "e1", "src": "node-101", "dst": "node-201", "weight": 0.44 },
    { "id": "e2", "src": "node-101", "dst": "node-301", "weight": 0.71 }
  ]
}
```

**Step 4:** Frontend renders edges to GPU buffer (3D green lines between nodes)

**Step 5:** As time passes, decay reduces weights. Edges below `min_weight` disappear automatically.

### Scenario 2: Operator zooms back out

**Step 1:** Camera altitude rises above 100 km
```javascript
EdgeStreamingClient.unsubscribe("scope-xyz789");
```

**Step 2:** Server confirms, stops sending edges

**Step 3:** Frontend switches to static cluster graph (no live edges)

### Scenario 3: New PCAP reinforces edges

**Step 1:** PCAP ingestion creates/reinforces edges in cluster 7

**Step 2:** Server decay loop runs (1 min tick)

**Step 3:** Operator previously subscribed to cluster 7 **automatically sees updated weights** in next 1-second tick

**No re-subscribe needed** — the scope stays active, weight just changed.

---

## Performance characteristics

### Typical sizes

| Metric                | Value                    | Why                       |
|-----------------------|--------------------------|--------------------------|
| Edges per cluster     | 50–500                   | Pruned by decay + LOD     |
| JSON message size     | 2–10 KB                  | Bounded edge count        |
| Server tick latency   | ~10 ms                   | Simple iteration, no build |
| Network round-trip    | ~50 ms (local)          | WebSocket efficiency     |

### Scalability

| Load                  | Behavior                  |
|-----------------------|--------------------------|
| 1 subscriber          | ~10 KB/sec (1 scope, 1 tick/sec) |
| 10 subscribers        | ~100 KB/sec total (still <1 Mbps) |
| 100 subscribers       | ~1 Mbps (acceptable LAN)  |
| 500+ subscribers      | Use Redis pub/sub layer (horizontal) |

---

## Implementation checklist

- [x] **Server:** `edge_streaming.py` module with `EdgeScope` and `EdgeStreamingManager`
- [x] **Server:** Flask-SocketIO handlers (`subscribe_edges`, `unsubscribe_edges`)
- [x] **Server:** Background streaming tick loop
- [x] **Frontend:** `EdgeStreamingClient` JavaScript module
- [x] **Frontend:** `onEdges()` callback for receiving edge batches
- [ ] **Frontend:** Integration with camera LOD system
- [ ] **Frontend:** THREE.js BufferGeometry rendering
- [ ] **Testing:** Decay verification (edges fade, prune as expected)
- [ ] **Testing:** Reinforcement verification (re-ingested edges gain weight back)
- [ ] **Testing:** Multi-scope subscription (multiple clusters at once)
- [ ] **Ops:** Monitor edge count distribution (should stay bounded)

---

## Debugging

### Check server-side subscriptions

```python
from edge_streaming import get_edge_streaming_manager
mgr = get_edge_streaming_manager()
print(mgr.subscriptions)  # { ws_id: { scope_id: EdgeScope, ... } }
```

### Check frontend connections

```javascript
// In browser console
console.log(EdgeStreamingClient.getSubscriptions());
console.log(EdgeStreamingClient.getActiveEdges());
```

### Verify decay is working

```bash
curl http://localhost:5000/api/hypergraph/status | jq .edges.total_weight
# Should decrease over time as decay runs
```

---

## Future enhancements

1. **GPU-side filtering** — send raw edges to GPU, filter by weight shader-side
2. **Time scrubbing** — request edges "as of T-5min" for temporal analysis
3. **Confidence visualization** — pulse opacity as weights decay
4. **Multi-layer subscriptions** — subscribe cluster *and* node simultaneously
5. **Redis pub/sub** — horizontal scaling for 1000+ browser clients
