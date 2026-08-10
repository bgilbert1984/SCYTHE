import assert from "node:assert/strict";
import test from "node:test";

import { LiveGraphController } from "./liveGraphController.js";

test("shared controller polls bounded graph once and publishes revision changes", async () => {
  const calls = []; let revision = "graph-1";
  const fetchImpl = async (url) => {
    calls.push(url);
    if (url.includes("eve/status")) return new Response(JSON.stringify({status: "ok", committed: 7}));
    if (url.includes("host-liveness")) return new Response(JSON.stringify({state: "active",
      rttMs: 4.2, tool: "ping", evidenceClass: "MEASURED", observedAt: 10}));
    return new Response(JSON.stringify({status: "ok", graphRevision: revision, nodeCount: 1,
      edgeCount: 0, nodes: [{id: "host:a"}], edges: []}));
  };
  const controller = new LiveGraphController({fetchImpl, refreshMilliseconds: 60_000});
  const first = []; const second = [];
  controller.subscribe((update) => first.push(update)); controller.subscribe((update) => second.push(update));
  await controller.start();
  assert.equal(calls.length, 3);
  assert.equal(first[0].changed, true); assert.equal(second[0].graph.graphRevision, "graph-1");
  assert.equal(first[0].graph.nodes[0].liveness.state, "active");
  await controller.refresh();
  assert.equal(first.at(-1).changed, false);
  revision = "graph-2"; await controller.refresh();
  assert.equal(first.at(-1).changed, true); assert.equal(first.at(-1).graph.graphRevision, "graph-2");
  controller.destroy();
});

test("round robin requires two consecutive failures before painting a host inactive", async () => {
  let failedPings = 0;
  const fetchImpl = async (url) => {
    if (url.includes("eve/status")) return new Response("{}");
    if (url.includes("host-liveness")) {
      failedPings += 1;
      return new Response(JSON.stringify({state: "inactive", evidenceClass: "MEASURED",
        tool: "ping", observedAt: failedPings}));
    }
    return new Response(JSON.stringify({status: "ok", graphRevision: "stable", nodeCount: 1,
      edgeCount: 0, nodes: [{id: "host:a", kind: "network_host"}], edges: []}));
  };
  const controller = new LiveGraphController({fetchImpl, refreshMilliseconds: 60_000});
  const updates = []; controller.subscribe((update) => updates.push(update));
  await controller.start();
  assert.equal(updates.at(-1).graph.nodes[0].liveness.state, "unknown");
  await controller.refresh();
  assert.equal(updates.at(-1).graph.nodes[0].liveness.state, "inactive");
  assert.match(updates.at(-1).message, /1 INACTIVE/);
  controller.destroy();
});

test("controller retains the last bounded snapshot when the endpoint is unavailable", async () => {
  let fail = false;
  const fetchImpl = async (url) => {
    if (url.includes("eve/status")) return new Response("{}", {status: 200});
    if (fail) return new Response(JSON.stringify({status: "unavailable"}), {status: 503});
    return new Response(JSON.stringify({status: "ok", graphRevision: "stable", nodes: [], edges: []}));
  };
  const controller = new LiveGraphController({fetchImpl, refreshMilliseconds: 60_000}); const updates = [];
  controller.subscribe((update) => updates.push(update)); await controller.start(); fail = true; await controller.refresh();
  assert.equal(updates.at(-1).available, false); assert.equal(updates.at(-1).graph.status, "unavailable");
  assert.equal(controller.snapshot.graphRevision, "stable"); controller.destroy();
});
