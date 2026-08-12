import assert from "node:assert/strict";
import test from "node:test";

import {buildOfflineHypergraphBundle} from "./offlineHypergraphBundle.js";

const graph = {
  graphRevision: "graph-test-1", capturedAt: 123, snapshotAuthority: "RETAINED_IMMUTABLE_GRAPH_STATE",
  nodes: [{id: "host:8.8.8.8", kind: "network_host", evidenceClass: "OBSERVED", observedAt: 1700000000,
    labels: {ip: "8.8.8.8"}, enrichment: {network: {asn: 15169, organization: "Google LLC"}},
    metadata: {note: "</script><script>not executable</script>"}}],
  edges: [{id: "flow:1", kind: "network_flow", evidenceClass: "OBSERVED", nodes: ["host:8.8.8.8"]}],
};

test("offline bundle is self-contained, revision-pinned, hashed, and script-safe", async () => {
  const html = await buildOfflineHypergraphBundle(graph, {exportedAt: "2026-08-11T00:00:00.000Z"});
  assert.match(html, /SCYTHE \/\/ OFFLINE LIVE HYPERGRAPH/);
  assert.match(html, /graph-test-1/);
  assert.match(html, /scythe\.offline-live-hypergraph\.v1/);
  assert.match(html, /NON_GEOGRAPHIC_DETERMINISTIC_LAYOUT/);
  assert.match(html, /RAW PACKETS NOT EXPOSED/);
  assert.match(html, /SHA-256 \/\/ VERIFYING/);
  assert.match(html, /<canvas aria-label="Offline non-geographic hypergraph topology">/);
  assert.match(html, /2D ACCESSIBLE/);
  assert.doesNotMatch(html, /https?:\/\//);
  assert.doesNotMatch(html, /<script>not executable<\/script>/);
  assert.match(html, /\\u003c\/script>/);
  const digest = html.match(/"snapshotSha256":"([a-f0-9]{64})"/)?.[1];
  assert.equal(digest?.length, 64);
});

test("offline bundle rejects missing graph snapshots", async () => {
  await assert.rejects(() => buildOfflineHypergraphBundle(null), /snapshot is required/);
});
