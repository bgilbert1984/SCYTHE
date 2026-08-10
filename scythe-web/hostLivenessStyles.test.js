import assert from "node:assert/strict";
import test from "node:test";

import {graphNodeStyle} from "./evidenceStyles.js";

test("measured host liveness overrides color but leaves non-host evidence styling alone", () => {
  assert.equal(graphNodeStyle({kind: "network_host", evidenceClass: "OBSERVED",
    liveness: {state: "active"}}).color, "#38f28f");
  assert.equal(graphNodeStyle({kind: "network_host", evidenceClass: "OBSERVED",
    liveness: {state: "inactive"}}).color, "#ff4f64");
  assert.equal(graphNodeStyle({kind: "event", evidenceClass: "OBSERVED",
    liveness: {state: "inactive"}}).color, "#b7ffdc");
});
