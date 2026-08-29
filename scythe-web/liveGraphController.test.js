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
  assert.equal(updates.at(-1).available, false); assert.equal(updates.at(-1).graph.graphRevision, "stable");
  assert.equal(updates.at(-1).retained, true);
  assert.match(updates.at(-1).message, /DEGRADED \/\/ RETAINING LAST SNAPSHOT/);
  assert.equal(controller.snapshot.graphRevision, "stable"); controller.destroy();
});

test("status separates complete detected counts from the bounded display", async () => {
  const fetchImpl = async (url) => {
    if (url.includes("eve/status")) return new Response(JSON.stringify({status: "ok", committed: 12}));
    return new Response(JSON.stringify({status: "ok", graphRevision: "many",
      detectedNodeCount: 847, detectedEdgeCount: 4219,
      displayedNodeCount: 200, displayedEdgeCount: 300,
      ranking: {lens: "ADAPTIVE_RELEVANCE", suppressedNodes: 647, suppressedEdges: 3919},
      nodeCount: 200, edgeCount: 300, nodes: [], edges: []}));
  };
  const controller = new LiveGraphController({fetchImpl, refreshMilliseconds: 60_000});
  const updates = []; controller.subscribe((update) => updates.push(update)); await controller.start();
  assert.match(updates.at(-1).message, /DETECTED \/\/ 847 NODES \/\/ 4219 EDGES/);
  assert.match(updates.at(-1).message,
    /DISPLAYED \/\/ 200 \/ 847 NODES \/\/ 300 \/ 4219 EDGES \/\/ BOUNDED 300N·600E/);
  assert.match(updates.at(-1).message, /DETAIL \/\/ OVERVIEW/);
  assert.match(updates.at(-1).message,
    /LENS \/\/ ADAPTIVE_RELEVANCE \/\/ SUPPRESSED 647N·3919E/);
  controller.destroy();
});

test("controller sends a bounded focus id for adaptive selection", async () => {
  const urls = []; const updates = [];
  const fetchImpl = async (url) => { urls.push(url);
    if (url.includes("eve/status")) return new Response("{}");
    return new Response(JSON.stringify({status: "ok", graphRevision: "focused", nodes: [], edges: []})); };
  const controller = new LiveGraphController({fetchImpl, refreshMilliseconds: 60_000});
  controller.subscribe((update) => updates.push(update));
  await controller.start(); controller.running = false;
  controller.setFocus("host:203.0.113.7"); await controller.refresh();
  assert.ok(urls.some((url) => url.includes("focus_id=host%3A203.0.113.7")));
  assert.ok(urls.some((url) => url.includes("node_limit=400&edge_limit=800")));
  assert.equal(updates.at(-1).changed, true);
  assert.equal(updates.at(-1).detail.tier, "FOCUSED");
  controller.destroy();
});

test("operator max detail requests the bounded 500-node 1000-edge tier", async () => {
  const urls = [];
  const fetchImpl = async (url) => { urls.push(url);
    if (url.includes("eve/status")) return new Response("{}");
    return new Response(JSON.stringify({status:"ok",graphRevision:"max",nodes:[],edges:[]})); };
  const controller = new LiveGraphController({fetchImpl, refreshMilliseconds:60_000});
  controller.setMaxDetail(true); await controller.refresh();
  assert.ok(urls.some((url) => url.includes("node_limit=500&edge_limit=1000")));
  assert.deepEqual(controller.detailState(), {tier:"MAX",tierId:"max",requestedTier:"MAX",
    nodeLimit:500,edgeLimit:1000,maxDetailRequested:true,performanceLimited:false});
  controller.destroy();
});

test("sustained slow frames step max detail down one tier at a time", () => {
  const controller = new LiveGraphController({slowFrameMilliseconds:20, slowFrameBudget:3});
  controller.setMaxDetail(true);
  assert.equal(controller.detailState().tier, "MAX");
  assert.equal(controller.reportFrameTime(35), false);
  assert.equal(controller.reportFrameTime(35), false);
  assert.equal(controller.reportFrameTime(35), true);
  assert.equal(controller.detailState().tier, "FOCUSED");
  controller.reportFrameTime(35); controller.reportFrameTime(35); controller.reportFrameTime(35);
  assert.equal(controller.detailState().tier, "OVERVIEW");
  assert.equal(controller.detailState().performanceLimited, true);
  controller.destroy();
});

test("multicast groups are excluded from unicast liveness rotation", async () => {
  let livenessCalls = 0;
  const fetchImpl = async (url) => {
    if (url.includes("eve/status")) return new Response(JSON.stringify({status:"ok"}));
    if (url.includes("host-liveness")) { livenessCalls += 1; return new Response("{}", {status:400}); }
    return new Response(JSON.stringify({status:"ok",graphRevision:"multicast",nodes:[
      {id:"host:ff02::1:3",kind:"network_multicast_group",enrichment:{scope:"MULTICAST"}},
    ],edges:[]}));
  };
  const controller = new LiveGraphController({fetchImpl,refreshMilliseconds:60_000});
  const updates=[]; controller.subscribe((value)=>updates.push(value)); await controller.start();
  assert.equal(livenessCalls,0);
  assert.match(updates.at(-1).message,/HOST PING \/\/ 0 ACTIVE \/\/ 0 INACTIVE \/\/ 0 UNKNOWN/);
  controller.destroy();
});
