import assert from "node:assert/strict";
import test from "node:test";

import { seededTopologyPosition, stableTopologyLayout } from "./liveHypergraph3DView.js";

test("topology seeds are deterministic and explicitly independent of graph geography", () => {
  assert.deepEqual(seededTopologyPosition("host:192.0.2.1"), seededTopologyPosition("host:192.0.2.1"));
  assert.notDeepEqual(seededTopologyPosition("host:192.0.2.1"), seededTopologyPosition("host:192.0.2.2"));
});

test("retained nodes do not jump when a live neighbor arrives", () => {
  const original = stableTopologyLayout(new Map(), [{id: "host:a"}], []);
  const before = {...original.get("host:a")};
  const next = stableTopologyLayout(original, [{id: "host:a"}, {id: "event:new"}],
    [{id: "flow:1", nodes: ["host:a", "event:new"]}]);
  assert.deepEqual(next.get("host:a"), before);
  assert.ok(next.has("event:new"));
});

test("removed positions leave the retained topology state", () => {
  const previous = new Map([["host:a", {x: 1, y: 2, z: 3}], ["host:b", {x: 4, y: 5, z: 6}]]);
  const next = stableTopologyLayout(previous, [{id: "host:b"}], []);
  assert.equal(next.has("host:a"), false); assert.deepEqual(next.get("host:b"), {x: 4, y: 5, z: 6});
});
