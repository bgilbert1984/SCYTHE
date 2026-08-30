import assert from "node:assert/strict";
import test from "node:test";

import {flowAnimationBudget, flowMotion} from "./evidenceStyles.js";

test("temporal counter deltas retain measured animation authority", () => {
  const result = flowMotion({labels:{motion_basis:"OBSERVED_SURICATA_COUNTER_DELTA",
    motion_interval_ms:"900",motion_forward_delta_packets:"5",motion_reverse_delta_packets:"2",
    flow_pkts_toserver:"50",flow_pkts_toclient:"20"}});
  assert.equal(result.measured,true); assert.equal(result.observedSummary,false);
  assert.equal(result.basis,"OBSERVED_SURICATA_COUNTER_DELTA"); assert.equal(result.forwardPackets,5);
});

test("a one-shot Suricata flow summary is animatable but explicitly not a live rate", () => {
  const result = flowMotion({labels:{motion_basis:"INSUFFICIENT_TEMPORAL_COUNTERS",
    flow_pkts_toserver:"7",flow_pkts_toclient:"1"}});
  assert.equal(result.measured,false); assert.equal(result.observedSummary,true); assert.equal(result.animatable,true);
  assert.equal(result.basis,"OBSERVED_SURICATA_FLOW_SUMMARY"); assert.match(result.label,/NOT A LIVE RATE/);
});

test("flow animation budget grows with detail but stays bounded", () => {
  assert.equal(flowAnimationBudget(0),48); assert.equal(flowAnimationBudget(600),120);
  assert.equal(flowAnimationBudget(1000),200); assert.equal(flowAnimationBudget(5000),200);
});
