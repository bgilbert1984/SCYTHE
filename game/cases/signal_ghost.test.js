const test = require('node:test');
const assert = require('node:assert');
const { validateCase } = require('./lib/validate');
const { referencePosterior, countedEvidence } = require('./lib/posterior');
const c = require('./signal_ghost/case.json');

const ALL = c.evidence.map((e) => e.id);
const REEVAL = { E02: 'fog_morning', E03: 'post_rain_evening' };
const FERRY_PATH = ['E01', 'E04', 'E06', 'E08', 'E09', 'W1'];
const top = (p) => Object.entries(p).sort((a, b) => b[1] - a[1])[0];

test('case validates against the contract', () => {
  assert.deepStrictEqual(validateCase(c), []);
});

test('validator rejects a broken case', () => {
  const bad = JSON.parse(JSON.stringify(c));
  bad.evidence[0].source = 'src_missing';
  bad.hypotheses[0].is_truth = true;
  const errs = validateCase(bad);
  assert.ok(errs.some((e) => e.includes('unknown source')));
  assert.ok(errs.some((e) => e.includes('exactly one hypothesis')));
});

test('truth is H3 and the prior is uniform', () => {
  assert.strictEqual(c.hypotheses.find((h) => h.is_truth).id, 'H3');
  assert.ok(Math.abs(c.prior.H1 - c.prior.H2) < 1e-9);
});

test('derived summaries are not double counted, in any inspection order', () => {
  assert.deepStrictEqual(countedEvidence(c, ['E01', 'E04', 'E08', 'E09']), ['E01', 'E04']);
  assert.deepStrictEqual(countedEvidence(c, ['E09', 'E08', 'E04', 'E01']), ['E01', 'E04']);
  assert.deepStrictEqual(countedEvidence(c, ['E08', 'E09']), ['E08']);
});

test('the ferry correlation alone does not support a confident conclusion', () => {
  const p = referencePosterior(c, { inspected: FERRY_PATH });
  assert.strictEqual(top(p)[0], 'H1');
  assert.ok(top(p)[1] < 0.5, `ferry-only path reached ${top(p)[1]}`);
});

test('corroborating the ferry correlation moves toward the truth', () => {
  const p = referencePosterior(c, { inspected: FERRY_PATH, corroborated: { E06: 0, W1: 0 } });
  assert.strictEqual(top(p)[0], 'H3');
});

test('reading everything without re-evaluating still misleads', () => {
  const p = referencePosterior(c, { inspected: ALL });
  assert.strictEqual(top(p)[0], 'H1');
});

test('the full path, including re-evaluation, identifies H3 with high confidence', () => {
  const p = referencePosterior(c, { inspected: ALL, reevaluated: REEVAL, corroborated: { E06: 0, W1: 0, W2: 0 } });
  assert.strictEqual(top(p)[0], 'H3');
  assert.ok(p.H3 > 0.95);
});

test('the posterior is deterministic and sums to 1', () => {
  const s = { inspected: ALL, reevaluated: REEVAL };
  const a = referencePosterior(c, s);
  assert.deepStrictEqual(a, referencePosterior(c, { ...s, inspected: [...ALL].reverse() }));
  assert.ok(Math.abs(Object.values(a).reduce((x, y) => x + y, 0) - 1) < 1e-9);
});

test('every evidence type, decision point and variant is present', () => {
  assert.deepStrictEqual([...new Set(c.evidence.map((e) => e.type))].sort(), ['device', 'log', 'sdr', 'witness']);
  assert.strictEqual(c.decision_points.length, 10);
  assert.ok(c.evidence.some((e) => e.result_kind === 'no_match'));
});
