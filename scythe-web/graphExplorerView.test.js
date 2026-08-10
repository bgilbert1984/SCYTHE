import assert from "node:assert/strict";
import test from "node:test";

import {buildExplorerQuery, explorerEntityKind, formatExplorerCounts} from "./graphExplorerView.js";

test("explorer query is bounded and includes explicit focus and epoch time", () => {
  const query = buildExplorerQuery({text: " google ", protocol: "TCP", start: "2026-08-09T10:00",
    focusEnabled: true, focusId: "host:8.8.8.8", depth: 9, nodeLimit: 100, edgeLimit: 150,
    nodeOffset: 100, edgeOffset: 150});
  assert.equal(query.get("q"), "google"); assert.equal(query.get("protocol"), "tcp");
  assert.equal(query.get("focus_id"), "host:8.8.8.8"); assert.equal(query.get("depth"), "2");
  assert.ok(Number.isFinite(Number(query.get("start")))); assert.equal(query.get("node_offset"), "100");
  assert.equal(buildExplorerQuery({focusEnabled: true, focusId: "host:a", depth: 0}).get("depth"), "0");
});

test("explorer counts never confuse available, matched, and returned", () => {
  const value = formatExplorerCounts({counts: {availableNodes: 385, availableEdges: 2700,
    scannedNodes: 385, scannedEdges: 2700, matchedNodes: 21, matchedEdges: 48,
    returnedNodes: 20, returnedEdges: 30}, scanTruncated: false});
  assert.match(value, /AVAILABLE \/\/ 385 NODES · 2700 EDGES/);
  assert.match(value, /SCANNED \/\/ 385 NODES · 2700 ELIGIBLE EDGES/);
  assert.match(value, /MATCHED \/\/ 21 NODES · 48 EDGES/);
  assert.match(value, /RETURNED \/\/ 20 NODES · 30 EDGES/);
});

test("explorer selections preserve graph entity type", () => {
  assert.equal(explorerEntityKind({kind: "network_flow"}), "graph-edge");
  assert.equal(explorerEntityKind({kind: "network_burst"}), "event");
  assert.equal(explorerEntityKind({kind: "network_host"}), "graph-node");
});
