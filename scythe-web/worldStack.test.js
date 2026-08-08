import assert from "node:assert/strict";
import test from "node:test";

import { WorldStack } from "./worldStack.js";

test("world stack applies and reverts hypothesis state without promoting evidence", () => {
  const writes = [];
  const store = {snapshot: () => ({worldStack: null}), replaceWorldStack: (value) => writes.push(value)};
  const stack = new WorldStack({store});
  const parameters = {investigationId: "investigation:1", executed: true,
    observedWorld: {worldId: "W0_OBSERVED"},
    worlds: [{worldId: "W1", evidenceClass: "COUNTERFACTUAL"}],
    boundary: "No causal verdict."};
  const receipt = stack.apply(parameters);
  assert.equal(receipt, null);
  assert.equal(stack.current.worlds[0].evidenceClass, "COUNTERFACTUAL");
  stack.revert(receipt);
  assert.equal(stack.current, null);
  assert.equal(writes.length, 2);
});
