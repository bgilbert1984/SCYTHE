import assert from "node:assert/strict";
import test from "node:test";

import {deriveContextualActions, InvestigationContext} from "./investigationContext.js";
import {SelectionModel} from "./selectionModel.js";

test("contextual actions expose requirements without inventing selection context", () => {
  const empty = deriveContextualActions();
  assert.equal(empty.find((item) => item.id === "trace.provenance-impact").enabled, false);
  const graph = deriveContextualActions([{kind: "graph-node", entityId: "host:a"}]);
  assert.equal(graph.find((item) => item.id === "trace.provenance-impact").enabled, true);
  assert.deepEqual(graph.find((item) => item.id === "correlate.rf-cell-graph").missing, ["RF cell"]);
  const complete = deriveContextualActions([{kind: "rf-cell"}, {kind: "graph-node"},
    {kind: "time-pin"}, {kind: "time-pin"}]);
  assert.equal(complete.every((item) => item.enabled), true);
});

test("investigation context shares selection and plan state with subscribers", () => {
  const selectionModel = new SelectionModel(); const writes = [];
  const store = {captureSelections: (...args) => writes.push(["selection", ...args]),
    recordPlan: (plan) => writes.push(["plan", plan.planId])};
  const context = new InvestigationContext({selectionModel, store}); const snapshots = [];
  context.subscribe((snapshot) => snapshots.push(snapshot));
  selectionModel.upsert({kind: "graph-node", entityId: "host:a", graphRevision: "graph-1"});
  context.refresh("graph-1");
  context.recordPlan({planId: "plan-1", directiveId: "dir-1", status: "completed", evidencePosture: "mixed"},
    {view: "provenance"});
  assert.equal(snapshots.at(-1).viewIntent.view, "provenance");
  assert.equal(writes[0][0], "selection"); assert.deepEqual(writes[1], ["plan", "plan-1"]);
});
