import assert from "node:assert/strict";
import test from "node:test";
import { validateDirectiveRequest, validateEffectPlan } from "./directiveProtocol.js";
import { EffectRuntime } from "./effectRuntime.js";

const request = {
  protocolVersion: "1.0", directiveId: "dir-1", directive: "explain.coverage-cell",
  selection: [{kind: "rf-cell", datasetId: "fixture", tileId: "z0", longitudeDegrees: 0, latitudeDegrees: 0}],
  requestedMode: "preview", idempotencyKey: "key-1",
};

test("directive protocol rejects unknown fields and directives", () => {
  assert.equal(validateDirectiveRequest(request), request);
  assert.throws(() => validateDirectiveRequest({...request, script: "alert(1)"}), /unknown fields/);
  assert.throws(() => validateDirectiveRequest({...request, directive: "execute.javascript"}), /allow-listed/);
});

test("effect plan rejects executable and authority-changing effects", () => {
  const base = {protocolVersion: "1.0", directiveId: "dir-1", planId: "plan-1", status: "completed",
    effects: [], queries: [], jobs: [], proposals: [], claims: [], supportingEvidence: [],
    contradictingEvidence: [], assumptions: [], falsifiers: [], mutations: [], refusals: []};
  assert.equal(validateEffectPlan(base), base);
  assert.throws(() => validateEffectPlan({...base, effects: [{effectId: "x", type: "view.run-javascript",
    parameters: {}, styleToken: "THRESHOLD_LENS", authorityImpact: "none", reversible: true}]}), /allow-listed/);
});

test("effect runtime applies and reverses a plan transactionally", async () => {
  const calls = [];
  const runtime = new EffectRuntime().register("view.highlight-targets", {
    apply: () => { calls.push("apply"); return "receipt"; },
    revert: (effect, receipt) => calls.push(`revert:${receipt}`),
  });
  const effect = {effectId: "effect-1", type: "view.highlight-targets", targets: [{kind: "rf-cell", id: "cell-1"}], parameters: {},
    styleToken: "THRESHOLD_LENS", authorityImpact: "none", reversible: true};
  const plan = {protocolVersion: "1.0", directiveId: "dir-1", planId: "plan-1", status: "completed",
    effects: [effect], queries: [], jobs: [], proposals: [], claims: [], supportingEvidence: [],
    contradictingEvidence: [], assumptions: [], falsifiers: [], mutations: [], refusals: []};
  await runtime.applyPlan(plan);
  await runtime.revertPlan("plan-1");
  assert.deepEqual(calls, ["apply", "revert:receipt"]);
});
