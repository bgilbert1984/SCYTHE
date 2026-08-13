import assert from "node:assert/strict";
import test from "node:test";

import {graphNodeStyle, graphPurposeStyle, hostLivenessStyle} from "./evidenceStyles.js";

test("measured host liveness overrides color but leaves non-host evidence styling alone", () => {
  assert.equal(graphNodeStyle({kind: "network_host", evidenceClass: "OBSERVED",
    liveness: {state: "active"}}).color, "#38f28f");
  assert.equal(graphNodeStyle({kind: "network_host", evidenceClass: "OBSERVED",
    liveness: {state: "inactive"}}).color, "#ff4f64");
  assert.equal(graphNodeStyle({kind: "event", evidenceClass: "OBSERVED",
    liveness: {state: "inactive"}}).color, "#b7ffdc");
});

test("adaptive purpose owns the node body while liveness is a separate badge", () => {
  const node = {kind: "network_host", evidenceClass: "OBSERVED",
    display: {selectionPurpose: "MOST_ACTIVE"}, liveness: {state: "inactive"}};
  assert.equal(graphPurposeStyle(node).color, "#00d4ff");
  assert.equal(hostLivenessStyle(node).color, "#ff4f64");
  assert.equal(hostLivenessStyle({kind: "event", liveness: {state: "active"}}), null);
});
