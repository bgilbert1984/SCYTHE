import assert from "node:assert/strict";
import test from "node:test";

import {validateViewIntent, viewIntentFromPlan} from "./viewIntent.js";

function plan(effect) {
  return {protocolVersion: "1.0", directiveId: "dir-1", planId: "plan-1", status: "completed",
    evidencePosture: "mixed", effects: [effect], queries: [], jobs: [], proposals: [], claims: [],
    supportingEvidence: [], contradictingEvidence: [], assumptions: [], falsifiers: [], mutations: [], refusals: []};
}

test("validated provenance effects route to an allow-listed view", () => {
  const intent = viewIntentFromPlan(plan({effectId: "effect-1", type: "view.show-graph-provenance",
    targets: [{kind: "graph-node", id: "host:a"}], parameters: {path: {graphRevision: "graph-1",
      nodes: [{id: "host:a"}], edges: [], sources: []}, executed: true, caveat: "adjacency only"},
    styleToken: "STATIC_SOLVER_OUTPUT", authorityImpact: "none", reversible: true}));
  assert.equal(intent.view, "provenance"); assert.equal(intent.graphRevision, "graph-1");
  assert.deepEqual(intent.focusEntityIds, ["host:a"]);
});

test("ViewIntent rejects arbitrary views and cross-effect routing", () => {
  const base = {version: "1.0", view: "temporal", title: "TEMPORAL WAKE", planId: "p", directiveId: "d",
    effectType: "view.show-graph-provenance", evidencePosture: "mixed", focusEntityIds: [],
    graphRevision: null, payload: {}, boundary: "none"};
  assert.throws(() => validateViewIntent(base), /cannot route/);
  assert.throws(() => validateViewIntent({...base, view: "run-javascript"}), /allow-listed/);
});
