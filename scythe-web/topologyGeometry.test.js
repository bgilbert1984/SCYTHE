import assert from "node:assert/strict";
import test from "node:test";

import {separateNewSpatialPoint, separatePlanarNodes, topologyEdgeGeometry} from "./topologyGeometry.js";

test("planar collision relaxation creates clear deterministic node spacing", () => {
  const nodes = [{id:"a",kind:"network_host"},{id:"b",kind:"network_host"},{id:"c",kind:"network_host"}];
  const initial = new Map(nodes.map((node) => [node.id, {x:100,y:100}]));
  const first = separatePlanarNodes(nodes, initial, 400, 300);
  const second = separatePlanarNodes(nodes, initial, 400, 300);
  assert.deepEqual(first, second);
  for (const a of nodes) for (const b of nodes) if (a.id < b.id)
    assert.ok(Math.hypot(first.get(a.id).x-first.get(b.id).x, first.get(a.id).y-first.get(b.id).y) >= 29);
});

test("edge geometry clears node bodies and preserves eight pixels around an arrow", () => {
  const geometry = topologyEdgeGeometry({x:0,y:0},{x:80,y:0},10,10,14);
  assert.deepEqual(geometry.start,{x:14,y:0}); assert.deepEqual(geometry.end,{x:66,y:0});
  assert.equal(geometry.arrowVisible,true); assert.ok(geometry.arrowLength <= 14);
  assert.equal(topologyEdgeGeometry({x:0,y:0},{x:30,y:0},10,10,14).arrowVisible,false);
});

test("new spatial points move away while retained positions remain untouched", () => {
  const occupied = new Map([["a",{x:1,y:2,z:3}]]); const before = {...occupied.get("a")};
  const point = separateNewSpatialPoint("b", {x:1,y:2,z:3}, occupied, 32);
  assert.deepEqual(occupied.get("a"), before);
  assert.ok(Math.hypot(point.x-1,point.y-2,point.z-3)>=32);
});
