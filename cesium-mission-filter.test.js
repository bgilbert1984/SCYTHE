'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');

const { valueMatchesMission, matchesMission, shouldRenderNode } = require('./cesium-mission-filter.js');

// ---- valueMatchesMission: the scalar-or-list primitive ----

test('valueMatchesMission: scalar equality', () => {
  assert.equal(valueMatchesMission('RFMAP-001', 'RFMAP-001'), true);
  assert.equal(valueMatchesMission('RFMAP-001', 'RFMAP-999'), false);
});

test('valueMatchesMission: list membership', () => {
  assert.equal(valueMatchesMission(['RFMAP-001', 'RFMAP-002'], 'RFMAP-001'), true);
  assert.equal(valueMatchesMission(['RFMAP-001', 'RFMAP-002'], 'RFMAP-002'), true);
  assert.equal(valueMatchesMission(['RFMAP-001', 'RFMAP-002'], 'RFMAP-999'), false);
});

test('valueMatchesMission: absent value never matches', () => {
  assert.equal(valueMatchesMission(undefined, 'RFMAP-001'), false);
  assert.equal(valueMatchesMission(null, 'RFMAP-001'), false);
});

test('valueMatchesMission: empty list matches nothing', () => {
  assert.equal(valueMatchesMission([], 'RFMAP-001'), false);
});

// ---- matchesMission: the four-location check (labels/metadata x mission_id/missionId) ----

test('matchesMission: canonical scalar mission_id in labels', () => {
  assert.equal(matchesMission({ labels: { mission_id: 'RFMAP-001' } }, 'RFMAP-001'), true);
});

test('matchesMission: legacy scalar missionId in labels (backward compatibility)', () => {
  assert.equal(matchesMission({ labels: { missionId: 'RFMAP-001' } }, 'RFMAP-001'), true);
});

test('matchesMission: canonical mission_id in metadata', () => {
  assert.equal(matchesMission({ metadata: { mission_id: 'RFMAP-001' } }, 'RFMAP-001'), true);
});

test('matchesMission: legacy missionId in metadata', () => {
  assert.equal(matchesMission({ metadata: { missionId: 'RFMAP-001' } }, 'RFMAP-001'), true);
});

test('matchesMission: list-valued mission_id (a host observed under several missions)', () => {
  const host = { labels: { ip: '10.0.0.1', mission_id: ['RFMAP-001', 'RFMAP-002'] } };
  assert.equal(matchesMission(host, 'RFMAP-001'), true);
  assert.equal(matchesMission(host, 'RFMAP-002'), true);
  assert.equal(matchesMission(host, 'RFMAP-999'), false);
});

test('matchesMission: a node with no mission attribution at all never matches', () => {
  assert.equal(matchesMission({ labels: { ip: '10.0.0.1' } }, 'RFMAP-001'), false);
  assert.equal(matchesMission({}, 'RFMAP-001'), false);
});

test('matchesMission: missing labels/metadata objects do not throw', () => {
  assert.equal(matchesMission({ id: 'x' }, 'RFMAP-001'), false);
  assert.equal(matchesMission(null, 'RFMAP-001'), false);
});

test('matchesMission: requires a truthy missionId -- that policy belongs to shouldRenderNode', () => {
  assert.throws(() => matchesMission({ labels: { mission_id: 'RFMAP-001' } }, ''), /truthy missionId/);
  assert.throws(() => matchesMission({ labels: { mission_id: 'RFMAP-001' } }, null), /truthy missionId/);
});

test('matchesMission: an unrelated single mission does not accidentally match via substring/partial logic', () => {
  assert.equal(matchesMission({ labels: { mission_id: 'RFMAP-0011' } }, 'RFMAP-001'), false);
});

// ---- shouldRenderNode: the actual render-decision policy ----

test('shouldRenderNode: empty selection renders everything (no filter active)', () => {
  assert.equal(shouldRenderNode({ labels: { mission_id: 'RFMAP-001' } }, null), true);
  assert.equal(shouldRenderNode({ labels: { mission_id: 'RFMAP-001' } }, undefined), true);
  assert.equal(shouldRenderNode({ labels: { mission_id: 'RFMAP-001' } }, ''), true);
  assert.equal(shouldRenderNode({}, ''), true); // even a node with no attribution at all
});

test('shouldRenderNode: a selected mission renders only matching nodes', () => {
  assert.equal(shouldRenderNode({ labels: { mission_id: 'RFMAP-001' } }, 'RFMAP-001'), true);
  assert.equal(shouldRenderNode({ labels: { mission_id: 'RFMAP-001' } }, 'RFMAP-999'), false);
});

test('shouldRenderNode: a selected mission hides an unattributed node, not shows it', () => {
  assert.equal(shouldRenderNode({ labels: { ip: '10.0.0.1' } }, 'RFMAP-001'), false);
});
