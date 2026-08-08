import assert from "node:assert/strict";
import test from "node:test";

import { InvestigationStore } from "./investigationStore.js";

test("investigation store persists bounded selections, plans, and reversible worlds", () => {
  const memory = new Map();
  const storage = {getItem: (key) => memory.get(key) ?? null, setItem: (key, value) => memory.set(key, value)};
  const store = new InvestigationStore({storage, maxPlans: 2});
  store.captureSelections([{kind: "rf-cell", datasetId: "rf"}], "graph-1");
  for (const id of ["p1", "p2", "p3"]) store.recordPlan({planId: id, directiveId: id,
    status: "completed", summary: id, evidencePosture: "mixed"});
  const previous = store.snapshot().worldStack;
  store.replaceWorldStack({investigationId: "investigation:1", worlds: [{worldId: "W1"}]});
  assert.equal(previous, null); assert.equal(store.snapshot().plans.length, 2);
  assert.equal(store.snapshot().investigationId, "investigation:1");
  const restored = new InvestigationStore({storage, maxPlans: 2});
  assert.equal(restored.snapshot().graphRevision, "graph-1");
  assert.equal(restored.snapshot().worldStack.worlds[0].worldId, "W1");
});
