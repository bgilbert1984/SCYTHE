'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');

const { pickNode } = require('./node-picking.js');

// A camera on +X looking at the origin: orthographic-ish column-major matrix mapping y,z (metres) to NDC x,y.
// clip = (z / S, y / S, depth, 1): the node at (R, y, z) lands at NDC (z/S, y/S). w is constant 1.
const S = 1e7;
const R = 6.4e6;
const viewProj = [
  0, 0, 0, 0,        // column 0 (x -> nothing on screen)
  0, 1 / S, 0, 0,    // column 1: y -> clip.y
  1 / S, 0, 0, 0,    // column 2: z -> clip.x
  0, 0, 0, 1,        // column 3: w = 1
];
const W = 1000, H = 1000;
const pixelOf = (y, z) => ({ px: (z / S * 0.5 + 0.5) * W, py: (1 - (y / S * 0.5 + 0.5)) * H });

function nodes(list) {
  const n = list.length;
  const out = { position: new Float32Array(n * 3), conf: new Float32Array(n).fill(1), lifecycle: new Float32Array(n).fill(1),
    cluster: new Float32Array(n).fill(1), visible: new Float32Array(n).fill(1) };
  list.forEach(([y, z], i) => { out.position.set([R, y, z], i * 3); });
  return out;
}
const pick = (n, at, extra = {}) => pickNode({ ...pixelOf(...at), width: W, height: H, viewProj, nodes: n, count: n.conf.length,
  heightScale: 0, ...extra });

test('a click on a node picks it, and a click on empty space picks nothing', () => {
  const n = nodes([[0, 0], [1e6, 1e6]]);
  assert.equal(pick(n, [0, 0]), 0);
  assert.equal(pick(n, [1e6, 1e6]), 1);
  assert.equal(pick(n, [-3e6, 3e6]), -1);
});

test('a click just inside the drawn radius hits and just outside misses', () => {
  const n = nodes([[0, 0]]);
  const { px, py } = pixelOf(0, 0);
  const at = (dx) => pickNode({ px: px + dx, py, width: W, height: H, viewProj, nodes: n, count: 1, heightScale: 0 });
  assert.equal(at(13), 0);     // radius is 14 px at full confidence
  assert.equal(at(15), -1);
});

test('the nearest of two overlapping nodes wins', () => {
  const n = nodes([[0, 0], [0, 1.2e5]]);                  // 6 px apart
  const { px, py } = pixelOf(0, 1.2e5);
  assert.equal(pickNode({ px, py, width: W, height: H, viewProj, nodes: n, count: 2, heightScale: 0 }), 1);
});

test('hidden, not-yet-emerged and behind-the-camera nodes cannot be picked', () => {
  const n = nodes([[0, 0]]);
  n.visible[0] = 0;
  assert.equal(pick(n, [0, 0]), -1);
  n.visible[0] = 1; n.lifecycle[0] = 0;
  assert.equal(pick(n, [0, 0]), -1);
  n.lifecycle[0] = 1;
  const behind = [0, 0, 0, 0, 0, 1 / S, 0, 0, 1 / S, 0, 0, 0, 0, 0, 0, -1];   // w = -1
  assert.equal(pickNode({ ...pixelOf(0, 0), width: W, height: H, viewProj: behind, nodes: n, count: 1, heightScale: 0 }), -1);
});

test('a faint node keeps a minimum hit radius so it can still be clicked', () => {
  const n = nodes([[0, 0]]);
  n.conf[0] = 0.01;
  const { px, py } = pixelOf(0, 0);
  const hit = (dx) => pickNode({ px: px + dx, py, width: W, height: H, viewProj, nodes: n, count: 1, heightScale: 0 });
  assert.equal(hit(9), 0);
  assert.equal(hit(11), -1);
});

test('only the live nodes are considered', () => {
  const n = nodes([[0, 0], [1e6, 0]]);
  assert.equal(pickNode({ ...pixelOf(1e6, 0), width: W, height: H, viewProj, nodes: n, count: 1, heightScale: 0 }), -1);
});

test('a node on the far side of the Earth is not pickable, but one on the near side is', () => {
  const n = nodes([[0, 0]]);                                   // at (R, 0, 0)
  const at = pixelOf(0, 0);
  const near = pickNode({ ...at, width: W, height: H, viewProj, nodes: n, count: 1, heightScale: 0, cameraPosition: [2e7, 0, 0] });
  const far = pickNode({ ...at, width: W, height: H, viewProj, nodes: n, count: 1, heightScale: 0, cameraPosition: [-2e7, 0, 0] });
  assert.equal(near, 0);
  assert.equal(far, -1);
});
