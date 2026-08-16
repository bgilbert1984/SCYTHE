import assert from "node:assert/strict";
import test from "node:test";

import {classifyFlowType, flowTypeStyle, graphNodeStyle, graphPurposeStyle,
  hostLivenessStyle} from "./evidenceStyles.js";

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

test("flow colors separate decoded protocol from tuple-inferred candidates", () => {
  const tls = {id:"flow:tls",kind:"network_flow",evidenceClass:"OBSERVED",
    labels:{tls_sni:"example.org",dest_port:"443",flow_type_basis:"OBSERVED_DECODED"}};
  const ssdp = {id:"flow:ssdp",kind:"network_flow",evidenceClass:"OBSERVED",
    labels:{dest_ip:"239.255.255.250",dest_port:"1900",proto:"udp"}};
  assert.equal(classifyFlowType(tls), "TLS");
  assert.equal(flowTypeStyle(tls).color, "#bb83ff");
  assert.equal(flowTypeStyle(tls).basis, "OBSERVED_DECODED");
  assert.equal(classifyFlowType(ssdp), "SERVICE_DISCOVERY");
  assert.equal(flowTypeStyle(ssdp).color, "#ff8c42");
});
