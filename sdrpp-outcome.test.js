'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');

const { describeResponse, describeProposal, formatFrequency } = require('./sdrpp-outcome.js');

test('executed control response is ok', () => {
  const r = describeResponse(200, { status: 'ok', outcome: 'executed', proposal_id: 'p1' });
  assert.deepEqual([r.ok, r.kind, r.proposalId], [true, 'executed', 'p1']);
});

test('a pending approval is never ok, whatever its status code', () => {
  for (const code of [200, 409]) {
    const r = describeResponse(code, { status: 'not_executed', outcome: 'pending_approval', proposal_id: 'p2' });
    assert.equal(r.ok, false);
    assert.equal(r.kind, 'pending_approval');
    assert.match(r.message, /second operator/);
  }
});

test('a 2xx without status ok is not success', () => {
  assert.equal(describeResponse(200, {}).ok, false);
  assert.equal(describeResponse(200, null).ok, false);
});

test('status codes map to kinds and carry the server reason', () => {
  assert.equal(describeResponse(400, { message: 'frequency_hz is required' }).message, 'frequency_hz is required');
  assert.equal(describeResponse(400, {}).kind, 'invalid');
  assert.equal(describeResponse(401, {}).kind, 'unauthorized');
  assert.equal(describeResponse(403, { message: 'the approver must differ from the proposer' }).kind, 'rejected');
  assert.equal(describeResponse(409, { outcome: 'rejected', message: 'graph oscillation' }).message, 'graph oscillation');
  assert.equal(describeResponse(503, {}).kind, 'unavailable');
  assert.equal(describeResponse(500, {}).message, 'HTTP 500');
});

test('a denied decision is ok and says denied', () => {
  const r = describeResponse(200, { status: 'ok', outcome: 'denied' });
  assert.deepEqual([r.ok, r.kind], [true, 'denied']);
});

test('formatFrequency', () => {
  assert.equal(formatFrequency(145350000), '145.350 MHz');
  assert.equal(formatFrequency(1.5e9), '1.500000 GHz');
  assert.equal(formatFrequency('x'), '?');
});

test('describeProposal reads as who, what and how long', () => {
  const now = 1000;
  assert.equal(describeProposal({ tool: 'rf_tune', params: { frequency_hz: 145.35e6, mode: 'NFM' },
    proposed_by: { callsign: 'ALPHA', operator_id: 'op-1' }, expires_at: now + 600 }, now),
  'ALPHA: tune to 145.350 MHz NFM · open 10 min');
  assert.equal(describeProposal({ tool: 'rf_capture_start', params: {}, proposed_by: { operator_id: 'op-1' },
    expires_at: now + 61 }, now), 'op-1: start the capture · open 2 min');
  assert.match(describeProposal({ tool: 'rf_stream_configure', params: { fft_size: 1024 } }, now), /fft_size=1024/);
  assert.match(describeProposal({ tool: 'other', params: {} }, now), /^an operator: other/);
});

test('an expired proposal reads as zero minutes, not negative', () => {
  assert.match(describeProposal({ tool: 'rf_capture_stop', expires_at: 10 }, 5000), /open 0 min/);
});
