'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');

const { entityEventToUpdate } = require('./cesium-entity-event-adapter.js');

const ev = (event_type, extra = {}) => ({
  event_type, entity_id: 'n1', entity_type: 'rf', entity_data: {}, provenance: {}, ...extra,
});

test('CREATE/UPDATE/PREEXISTING with position [lat, lon, alt] become a flattened node_update', () => {
  for (const t of ['CREATE', 'UPDATE', 'PREEXISTING']) {
    const out = entityEventToUpdate(ev(t, { entity_data: { position: [37.5, -122.25, 10], power: -60 } }));
    assert.equal(out.type, 'node_update');
    assert.equal(out.entity_id, 'n1');
    assert.equal(out.lat, 37.5);
    assert.equal(out.lon, -122.25);
    assert.equal(out.power, -60);
  }
});

test('flat lat/lon and latitude/longitude are accepted', () => {
  assert.equal(entityEventToUpdate(ev('UPDATE', { entity_data: { lat: 1, lon: 2 } })).lon, 2);
  assert.equal(entityEventToUpdate(ev('UPDATE', { entity_data: { latitude: '3', longitude: '4' } })).lat, 3);
});

test('a node with no usable position is skipped rather than plotted at 0,0', () => {
  assert.equal(entityEventToUpdate(ev('CREATE', { entity_data: {} })), null);
  assert.equal(entityEventToUpdate(ev('CREATE', { entity_data: { position: [0, 0, 0] } })), null);
  assert.equal(entityEventToUpdate(ev('CREATE', { entity_data: { position: ['x', 'y'] } })), null);
});

test('DELETE becomes node_remove keyed by entity_id', () => {
  assert.deepEqual(entityEventToUpdate(ev('DELETE')), { type: 'node_remove', id: 'n1' });
});

test('edges and hyperedges on the same event are ignored', () => {
  assert.equal(entityEventToUpdate(ev('CREATE', { entity_type: 'hyperedge', entity_data: { position: [1, 2] } })), null);
  assert.equal(entityEventToUpdate(ev('CREATE', { entity_data: { nodes: ['a', 'b'], position: [1, 2] } })), null);
  assert.equal(entityEventToUpdate(ev('DELETE', { entity_type: 'edge' })), null);
});

test('non-entity events and malformed payloads yield null', () => {
  for (const t of ['HEARTBEAT', 'ROOM_CREATED', 'OPERATOR_JOINED']) {
    assert.equal(entityEventToUpdate(ev(t, { entity_data: { lat: 1, lon: 2 } })), null);
  }
  assert.equal(entityEventToUpdate(null), null);
  assert.equal(entityEventToUpdate({ event_type: 'CREATE' }), null);
});
