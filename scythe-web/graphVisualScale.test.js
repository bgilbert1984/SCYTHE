import assert from "node:assert/strict";
import test from "node:test";

import {GRAPH_VISUAL_SCALE_BOUNDARY, graphFlowGroupScale, graphFlowScale,
  graphNodeScale} from "./graphVisualScale.js";

test("node activity scaling is logarithmic and bounded", () => {
  const baseline = graphNodeScale({kind: "network_host"});
  const active = graphNodeScale({kind: "network_host", display: {activityScore: 3}});
  const extreme = graphNodeScale({kind: "network_host", display: {activityScore: 1e30}});
  assert.equal(baseline.cesiumPixels, 8);
  assert.ok(active.cesiumPixels > baseline.cesiumPixels);
  assert.equal(extreme.cesiumPixels, 12.4);
  assert.ok(extreme.cesiumPixels <= 14);
  assert.equal(active.basis, "ADAPTIVE_RELEVANCE_ACTIVITY_SCORE");
});

test("flow scaling prefers measured deltas and cannot grow without bound", () => {
  const measured = graphFlowScale({labels: {motion_basis: "OBSERVED_SURICATA_COUNTER_DELTA",
    motion_forward_delta_packets: "64", motion_reverse_delta_packets: "4",
    motion_forward_delta_bytes: "96000", packets: "999999999"}}, 1);
  assert.equal(measured.packets, 68);
  assert.equal(measured.basis, "MEASURED_TEMPORAL_COUNTER_DELTA");
  assert.ok(measured.arrowPixels > 6 && measured.arrowPixels <= 14);
  const extreme = graphFlowScale({labels: {packets: 1e50, bytes: 1e60}}, 1000000);
  assert.equal(extreme.topologyWidth, 6); assert.equal(extreme.cesiumWidth, 7);
  assert.equal(extreme.arrowPixels, 14);
});

test("missing counters retain a readable baseline without inventing activity", () => {
  const scale = graphFlowScale({labels: {}}, 1);
  assert.equal(scale.intensity, 0); assert.equal(scale.arrowPixels, 6);
  assert.equal(scale.basis, "READABILITY_BASELINE");
  assert.match(GRAPH_VISUAL_SCALE_BOUNDARY, /NOT TRAFFIC, LATENCY, OR ROUTE/);
});

test("flow groups combine bounded counters instead of borrowing one representative", () => {
  const group = graphFlowGroupScale([
    {labels: {packets: 4}}, {labels: {packets: 12}}, {labels: {}},
  ]);
  assert.equal(group.packets, 16); assert.equal(group.aggregateCount, 3);
  assert.equal(group.basis, "BOUNDED_MIXED_OBSERVED_COUNTERS");
});
