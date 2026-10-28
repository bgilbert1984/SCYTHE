const test = require('node:test');
const assert = require('node:assert');
const { createGame, replay, COSTS } = require('./engine');
const c = require('../cases/signal_ghost/case.json');

const fresh = (seed = 1) => createGame(c, { seed });
const ok = (g, a) => {
  const r = g.dispatch(a);
  assert.ok(r.ok, `expected ok for ${JSON.stringify(a)}, got ${JSON.stringify(r.error)}`);
  return r;
};
const code = (g, a) => {
  const r = g.dispatch(a);
  assert.strictEqual(r.ok, false);
  return r.error.code;
};
const snapshot = (g) => JSON.stringify([g.view(), g.actionLog]);

test('player view never exposes ground truth', () => {
  const g = fresh();
  ok(g, { type: 'inspect', evidence: 'E14' });
  const text = JSON.stringify(g.view());
  assert.ok(!text.includes('likelihood'));
  assert.ok(!text.includes('is_truth'));
  assert.ok(!text.includes('"debrief"'));
  assert.ok(!text.includes('corroboration'));
  assert.strictEqual(g.view().outcome, null);
});

test('uninspected evidence shows only its title', () => {
  const e = fresh().view().evidence.find((x) => x.id === 'E14');
  assert.strictEqual(e.inspected, false);
  assert.strictEqual(e.reading, undefined);
});

test('actions cost budget and are refused when it runs out', () => {
  const g = fresh();
  assert.strictEqual(g.view().budget_remaining, c.budget);
  ok(g, { type: 'inspect', evidence: 'E01' });
  assert.strictEqual(g.view().budget_remaining, c.budget - COSTS.inspect);
  const ids = c.evidence.map((e) => e.id).filter((id) => id !== 'E01');
  let i = 0;
  while (g.view().budget_remaining > 0) ok(g, { type: 'inspect', evidence: ids[i++] });
  assert.strictEqual(code(g, { type: 'inspect', evidence: ids[i] }), 'INSUFFICIENT_BUDGET');
});

test('rejected actions leave state untouched', () => {
  const g = fresh();
  ok(g, { type: 'inspect', evidence: 'E01' });
  const before = snapshot(g);
  for (const a of [
    { type: 'inspect', evidence: 'E01' },
    { type: 'inspect', evidence: 'E99' },
    { type: 'compare', a: 'E01', b: 'E02' },
    { type: 'link', evidence: 'E02', hypothesis: 'H1', relation: 'supports' },
    { type: 'link', evidence: 'E01', hypothesis: 'H1', relation: 'maybe' },
    { type: 'nonsense' },
    null,
    { type: 'inspect' },
  ]) assert.strictEqual(g.dispatch(a).ok, false);
  assert.strictEqual(snapshot(g), before);
});

test('rejections carry explicit codes', () => {
  const g = fresh();
  assert.strictEqual(code(g, { type: 'inspect', evidence: 'E99' }), 'UNKNOWN_EVIDENCE');
  assert.strictEqual(code(g, { type: 'fly' }), 'UNKNOWN_ACTION');
  assert.strictEqual(code(g, null), 'MALFORMED_ACTION');
  assert.strictEqual(code(g, { type: 'link', evidence: 'E01', hypothesis: 'H1', relation: 'supports' }), 'NOT_INSPECTED');
  assert.strictEqual(code(g, { type: 'reject', hypothesis: 'H9' }), 'UNKNOWN_HYPOTHESIS');
  assert.strictEqual(code(g, { type: 'set_confidence', hypothesis: 'H1', value: 2 }), 'BAD_CONFIDENCE');
});

test('compare reveals shared upstream between a summary and its source', () => {
  const g = fresh();
  for (const id of ['E01', 'E08', 'E14', 'E02']) ok(g, { type: 'inspect', evidence: id });
  assert.strictEqual(ok(g, { type: 'compare', a: 'E08', b: 'E01' }).result.shared_upstream, true);
  assert.strictEqual(code(g, { type: 'compare', a: 'E01', b: 'E08' }), 'ALREADY_COMPARED');
  const r = ok(g, { type: 'compare', a: 'E14', b: 'E02' }).result;
  assert.strictEqual(r.shared_upstream, false);
  assert.strictEqual(r.agreement, 'conflict');
});

test('re-evaluation is gated on discovering the conditions', () => {
  const g = fresh();
  ok(g, { type: 'inspect', evidence: 'E02' });
  assert.strictEqual(code(g, { type: 'reevaluate', evidence: 'E02', variant: 'fog_morning' }), 'VARIANT_NOT_DISCOVERED');
  ok(g, { type: 'inspect', evidence: 'E07' });
  const r = ok(g, { type: 'reevaluate', evidence: 'E02', variant: 'fog_morning' }).result;
  assert.strictEqual(r.changed, true);
  assert.strictEqual(code(g, { type: 'reevaluate', evidence: 'E02', variant: 'fog_morning' }), 'ALREADY_REEVALUATED');
});

test('re-evaluating where conditions do not matter still costs budget', () => {
  const g = fresh();
  ok(g, { type: 'inspect', evidence: 'E07' });
  ok(g, { type: 'inspect', evidence: 'E14' });
  const before = g.view().budget_remaining;
  const r = ok(g, { type: 'reevaluate', evidence: 'E14', variant: 'fog_morning' });
  assert.strictEqual(r.result.changed, false);
  assert.strictEqual(g.view().budget_remaining, before - COSTS.reevaluate);
});

test('corroboration is seeded and deterministic', () => {
  const run = (seed) => {
    const g = fresh(seed);
    ok(g, { type: 'inspect', evidence: 'W2' });
    return ok(g, { type: 'corroborate', evidence: 'W2' }).result.kind;
  };
  assert.strictEqual(run(7), run(7));
  const kinds = new Set(Array.from({ length: 40 }, (_, s) => run(s)));
  assert.deepStrictEqual([...kinds].sort(), ['no_match', 'result']);
});

test('items without corroboration cannot be corroborated; each only once', () => {
  const g = fresh();
  ok(g, { type: 'inspect', evidence: 'E01' });
  assert.strictEqual(code(g, { type: 'corroborate', evidence: 'E01' }), 'NOT_CORROBORATABLE');
  ok(g, { type: 'inspect', evidence: 'E06' });
  ok(g, { type: 'corroborate', evidence: 'E06' });
  assert.strictEqual(code(g, { type: 'corroborate', evidence: 'E06' }), 'ALREADY_CORROBORATED');
});

test('hypotheses: reject, restore, and never reject the last one', () => {
  const g = fresh();
  ok(g, { type: 'reject', hypothesis: 'H1' });
  assert.strictEqual(code(g, { type: 'reject', hypothesis: 'H1' }), 'HYPOTHESIS_REJECTED');
  ok(g, { type: 'reject', hypothesis: 'H2' });
  assert.strictEqual(code(g, { type: 'reject', hypothesis: 'H3' }), 'LAST_HYPOTHESIS');
  const before = g.view().budget_remaining;
  ok(g, { type: 'restore', hypothesis: 'H1' });
  assert.strictEqual(g.view().budget_remaining, before - COSTS.restore);
  assert.strictEqual(code(g, { type: 'restore', hypothesis: 'H1' }), 'NOT_REJECTED');
});

test('commit requires inspected citations and a live hypothesis', () => {
  const g = fresh();
  ok(g, { type: 'inspect', evidence: 'E14' });
  assert.strictEqual(code(g, { type: 'commit', hypothesis: 'H3', confidence: 0.8, citations: [] }), 'EMPTY_CITATIONS');
  assert.strictEqual(code(g, { type: 'commit', hypothesis: 'H3', confidence: 0.8, citations: ['E11'] }), 'UNINSPECTED_CITATION');
  assert.strictEqual(code(g, { type: 'commit', hypothesis: 'H3', confidence: 1.5, citations: ['E14'] }), 'BAD_CONFIDENCE');
  ok(g, { type: 'reject', hypothesis: 'H1' });
  assert.strictEqual(code(g, { type: 'commit', hypothesis: 'H1', confidence: 0.5, citations: ['E14'] }), 'HYPOTHESIS_REJECTED');
});

test('commit ends the game and reveals the outcome', () => {
  const g = fresh();
  ok(g, { type: 'inspect', evidence: 'E14' });
  const r = ok(g, { type: 'commit', hypothesis: 'H3', confidence: 0.75, citations: ['E14', 'E14'] }).result;
  assert.strictEqual(r.is_correct, true);
  assert.deepStrictEqual(r.citations, ['E14']);
  assert.ok(Math.abs(r.reference_posterior.H3 - 0.75) < 1e-9);
  assert.ok(r.debrief.truth);
  assert.strictEqual(g.finished, true);
  assert.strictEqual(code(g, { type: 'inspect', evidence: 'E01' }), 'GAME_OVER');
  assert.strictEqual(g.view().outcome.committed_hypothesis, 'H3');
});

test('a wrong commit is reported as incorrect', () => {
  const g = fresh();
  ok(g, { type: 'inspect', evidence: 'E01' });
  const r = ok(g, { type: 'commit', hypothesis: 'H1', confidence: 0.9, citations: ['E01'] }).result;
  assert.strictEqual(r.is_correct, false);
});

test('same case + seed + action log reproduces the same state', () => {
  const g = fresh(11);
  const actions = [
    { type: 'inspect', evidence: 'E07' },
    { type: 'inspect', evidence: 'E06' },
    { type: 'inspect', evidence: 'E02' },
    { type: 'corroborate', evidence: 'E06' },
    { type: 'reevaluate', evidence: 'E02', variant: 'fog_morning' },
    { type: 'link', evidence: 'E02', hypothesis: 'H3', relation: 'supports' },
    { type: 'reject', hypothesis: 'H2' },
    { type: 'commit', hypothesis: 'H3', confidence: 0.7, citations: ['E02', 'E06'] },
  ];
  actions.forEach((a) => ok(g, a));
  const { game, error } = replay(c, 11, g.actionLog);
  assert.strictEqual(error, null);
  assert.strictEqual(snapshot(game), snapshot(g));
});

test('replay reports the first rejected action', () => {
  const { error } = replay(c, 1, [{ type: 'inspect', evidence: 'E01' }, { type: 'inspect', evidence: 'E01' }]);
  assert.strictEqual(error.index, 1);
  assert.strictEqual(error.code, 'ALREADY_INSPECTED');
});

test('the view is a copy: mutating it does not change the game', () => {
  const g = fresh();
  ok(g, { type: 'inspect', evidence: 'E01' });
  const v = g.view();
  v.evidence[0].reading = 'tampered';
  v.budget_remaining = 99;
  assert.notStrictEqual(g.view().evidence[0].reading, 'tampered');
  assert.notStrictEqual(g.view().budget_remaining, 99);
});
