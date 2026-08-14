import assert from "node:assert/strict";
import test from "node:test";
import {InfrastructureController} from "./infrastructureController.js";

test("infrastructure controller requests bounded evidence and carries focus", async () => {
  const calls = [];
  const fetchImpl = async (url) => { calls.push(String(url)); return {ok: true, status: 200,
    json: async () => ({status: "ok", schemaVersion: "graphops.infrastructure.v1",
      graphRevision: "graph-a", domains: [], observedFlows: []})}; };
  const controller = new InfrastructureController({fetchImpl, refreshMilliseconds: 60000});
  controller.setFocus("host:20.1.1.1");
  assert.equal(controller.setWindow(1000, 1100), true);
  const updates = []; controller.subscribe((update) => updates.push(update));
  await controller.refresh();
  assert.match(calls[0], /node_limit=500/);
  assert.match(calls[0], /edge_limit=1000/);
  assert.match(calls[0], /focus_id=host%3A20.1.1.1/);
  assert.match(calls[0], /since=1000/);
  assert.match(calls[0], /until=1100/);
  assert.equal(updates[0].snapshot.graphRevision, "graph-a");
  controller.destroy();
});

test("infrastructure controller refuses reversed and over-retention windows", () => {
  const controller = new InfrastructureController({fetchImpl: async () => {}});
  assert.equal(controller.setWindow(200, 100), false);
  assert.equal(controller.setWindow(0, 8 * 24 * 60 * 60), false);
  assert.equal(controller.since, null);
});

test("infrastructure controller retains last snapshot across an unavailable refresh", async () => {
  let fail = false;
  const fetchImpl = async () => {
    if (fail) throw new Error("offline");
    return {ok: true, json: async () => ({status: "empty", graphRevision: "graph-empty", domains: []})};
  };
  const controller = new InfrastructureController({fetchImpl});
  await controller.refresh(); fail = true;
  const retained = await controller.refresh();
  assert.equal(retained.graphRevision, "graph-empty");
});
