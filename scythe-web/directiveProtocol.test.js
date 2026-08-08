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

test("RF graph correlation requires both typed selections", () => {
  const correlation = {...request, directive: "correlate.rf-cell-graph",
    selection: [...request.selection, {kind: "graph-node", entityId: "burst-a", graphRevision: "graph-1"}]};
  assert.equal(validateDirectiveRequest(correlation), correlation);
  assert.throws(() => validateDirectiveRequest({...correlation, selection: request.selection}), /requires rf-cell and graph/);
});

test("GRAPH_DELTA requires exactly two same-clock time pins", () => {
  const delta = {...request, directive: "compare.graph-delta", selection: [
    {kind: "time-pin", timestamp: 100, clockId: "UTC"},
    {kind: "time-pin", timestamp: 200, clockId: "UTC"},
  ]};
  assert.equal(validateDirectiveRequest(delta), delta);
  assert.throws(() => validateDirectiveRequest({...delta, selection: delta.selection.slice(0, 1)}), /requires two time pins/);
  assert.throws(() => validateDirectiveRequest({...delta, selection: [delta.selection[0],
    {...delta.selection[1], clockId: "sensor-clock"}]}), /same clock/);
});

test("graph edge selections support provenance and contradiction directives", () => {
  const selection = [{kind: "graph-edge", entityId: "edge-a", graphRevision: "graph-1"}];
  for (const directive of ["trace.provenance-impact", "expose.contradictions"]) {
    const value = {...request, directive, selection};
    assert.equal(validateDirectiveRequest(value), value);
  }
});

test("lunar locations require an explicit Moon-fixed frame and absent M0 terrain authority", () => {
  const lunar = {...request, directive:"explain.lunar-location", selection:[{
    kind:"lunar-location", datasetId:"lunar-south-pole-reference-m0", locationId:"moon:-89:0",
    celestialBody:"MOON", referenceFrame:"MOON_ME_DE421", longitudeDegrees:0,
    latitudeDegrees:-89, heightMeters:0, spatialAuthority:"REFERENCE_ELLIPSOID_ONLY",
  }]};
  assert.equal(validateDirectiveRequest(lunar).selection[0].celestialBody, "MOON");
  assert.throws(() => validateDirectiveRequest({...lunar, selection:[{...lunar.selection[0],
    referenceFrame:"EPSG:4326"}]}), /lunar-location selection is incomplete/);
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
