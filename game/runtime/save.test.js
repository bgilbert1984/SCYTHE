const test = require('node:test');
const assert = require('node:assert');
const crypto = require('node:crypto');
const { createGame, COSTS } = require('./engine');
const { createSave, serialize, parse, load, canonical, caseHash, FORMAT, VERSION } = require('./save');
const { sha256Hex } = require('./hash');
const { draw } = require('./rng');
const c = require('../cases/signal_ghost/case.json');

const reseal = (s) => {
  const { checksum, ...rest } = s;
  return { ...s, checksum: sha256Hex(canonical(rest)) };
};
const text = (s) => JSON.stringify(s);
const code = (r) => (r.error ? r.error.code : null);

function finishedGame(seed = 3) {
  const g = createGame(c, { seed });
  for (const a of [
    { type: 'inspect', evidence: 'E07' }, { type: 'inspect', evidence: 'E14' }, { type: 'inspect', evidence: 'E02' },
    { type: 'reevaluate', evidence: 'E02', variant: 'fog_morning' },
    { type: 'commit', hypothesis: 'H3', confidence: 0.8, citations: ['E14', 'E02'] },
  ]) assert.ok(g.dispatch(a).ok);
  return g;
}

test('sha256 matches Node crypto, including padding boundaries and unicode', () => {
  for (const t of ['', 'abc', 'a'.repeat(1000), 'héllo ✓ 😀', 'x'.repeat(55), 'x'.repeat(56), 'x'.repeat(63), 'x'.repeat(64), 'x'.repeat(65)]) {
    assert.strictEqual(sha256Hex(t), crypto.createHash('sha256').update(t).digest('hex'));
  }
});

test('canonical JSON ignores key order', () => {
  assert.strictEqual(canonical({ b: 1, a: [{ y: 1, x: 2 }] }), canonical({ a: [{ x: 2, y: 1 }], b: 1 }));
});

test('a save carries the case identity, seed, and action log, and round-trips', () => {
  const g = createGame(c, { seed: 9 });
  g.dispatch({ type: 'inspect', evidence: 'E01' });
  const save = createSave(g, c);
  assert.strictEqual(save.format, FORMAT);
  assert.strictEqual(save.version, VERSION);
  assert.strictEqual(save.case_id, 'signal_ghost');
  assert.strictEqual(save.contract_version, c.contract_version);
  assert.strictEqual(save.seed, 9);
  assert.strictEqual(save.case_hash, caseHash(c));
  assert.strictEqual(save.result, undefined);
  const r = load(c, serialize(save));
  assert.strictEqual(r.error, undefined);
  assert.deepStrictEqual(r.game.actionLog, g.actionLog);
  assert.strictEqual(r.verified, false);
});

test('serialization is deterministic', () => {
  const g = finishedGame();
  assert.strictEqual(serialize(createSave(g, c)), serialize(createSave(g, c)));
});

test('a finished save records its result and verifies on load', () => {
  const save = createSave(finishedGame(), c);
  assert.strictEqual(save.result.hypothesis, 'H3');
  const r = load(c, serialize(save));
  assert.strictEqual(r.verified, true);
  assert.strictEqual(r.game.view().outcome.score.total, save.result.score);
});

test('rejects unreadable, foreign, and incomplete files with clear codes', () => {
  assert.strictEqual(code(parse('{nope')), 'MALFORMED');
  assert.strictEqual(code(parse('{"hello":1}')), 'NOT_A_SAVE');
  assert.strictEqual(code(parse('null')), 'NOT_A_SAVE');
  const good = createSave(finishedGame(), c);
  assert.strictEqual(code(parse(text({ ...good, version: 'one' }))), 'MALFORMED');
  assert.strictEqual(code(parse(text({ ...good, seed: 'x' }))), 'MALFORMED');
  assert.strictEqual(code(parse(text({ ...good, actions: 'no' }))), 'MALFORMED');
});

test('rejects newer and older save versions', () => {
  const good = createSave(finishedGame(), c);
  const newer = parse(text(reseal({ ...good, version: VERSION + 1 })));
  assert.strictEqual(code(newer), 'UNSUPPORTED_VERSION');
  assert.match(newer.error.message, /newer version/);
  assert.strictEqual(code(parse(text(reseal({ ...good, version: 0 })))), 'UNSUPPORTED_VERSION');
});

test('detects a damaged or hand-edited save', () => {
  const good = createSave(finishedGame(), c);
  const edited = { ...good, actions: good.actions.map((a, i) => (i === 1 ? { ...a, evidence: 'E01' } : a)) };
  assert.strictEqual(code(parse(text(edited))), 'CORRUPT');
  assert.strictEqual(code(parse(serialize(good).slice(0, -20))), 'MALFORMED');
});

test('rejects a contract mismatch, a different case, and a changed case', () => {
  const good = createSave(finishedGame(), c);
  const r1 = load(c, text(reseal({ ...good, contract_version: 'game_case_9.9.9' })));
  assert.strictEqual(code(r1), 'CONTRACT_MISMATCH');
  assert.match(r1.error.message, /game_case_9\.9\.9/);
  assert.strictEqual(code(load(c, text(reseal({ ...good, case_id: 'other_case' })))), 'WRONG_CASE');
  const edited = JSON.parse(JSON.stringify(c));
  edited.evidence.find((e) => e.id === 'E14').likelihood.H3 = 0.5;
  assert.strictEqual(code(load(edited, serialize(good))), 'CASE_CHANGED');
});

test('reports the first action that cannot be replayed', () => {
  const good = createSave(finishedGame(), c);
  const bad = reseal({ ...good, actions: [{ type: 'inspect', evidence: 'E01' }, { type: 'inspect', evidence: 'E01' }], result: undefined });
  const r = load(c, text(bad));
  assert.strictEqual(code(r), 'REPLAY_FAILED');
  assert.match(r.error.message, /Action 2/);
  assert.match(r.error.message, /ALREADY_INSPECTED/);
});

test('detects a recorded result that replay does not reproduce', () => {
  const good = createSave(finishedGame(), c);
  const lying = reseal({ ...good, result: { ...good.result, score: 100 } });
  assert.strictEqual(code(load(c, text(lying))), 'RESULT_MISMATCH');
  const unfinished = reseal({ ...good, actions: good.actions.slice(0, 2) });
  assert.strictEqual(code(load(c, text(unfinished))), 'RESULT_MISMATCH');
});

/** A deterministic random player: picks any plausible action, whether or not it is accepted. */
function randomPlaythrough(seed) {
  const g = createGame(c, { seed });
  const ids = c.evidence.map((e) => e.id);
  const variants = c.variants.map((v) => v.variant_id);
  const hyps = ['H1', 'H2', 'H3'];
  const pick = (list, n) => list[Math.floor(draw('fuzz', seed, n) * list.length)];
  for (let n = 0; n < 40 && !g.finished; n++) {
    const kind = Math.floor(draw('kind', seed, n) * 9);
    const e = pick(ids, n * 7 + 1);
    const e2 = pick(ids, n * 7 + 2);
    const hy = pick(hyps, n * 7 + 3);
    const actions = [
      { type: 'inspect', evidence: e },
      { type: 'inspect', evidence: e },
      { type: 'compare', a: e, b: e2 },
      { type: 'corroborate', evidence: e },
      { type: 'reevaluate', evidence: e, variant: pick(variants, n * 7 + 4) },
      { type: 'link', evidence: e, hypothesis: hy, relation: pick(['supports', 'contradicts', 'neutral'], n * 7 + 5) },
      { type: 'reject', hypothesis: hy },
      { type: 'set_confidence', hypothesis: hy, value: Math.round(draw('c', seed, n) * 20) / 20 },
      { type: 'commit', hypothesis: hy, confidence: Math.round(draw('cc', seed, n) * 20) / 20, citations: [e, e2] },
    ];
    g.dispatch(actions[kind]);
  }
  if (!g.finished) {
    if (!g.view().evidence.some((e) => e.inspected)) g.dispatch({ type: 'inspect', evidence: 'E01' });
    const read = g.view().evidence.filter((e) => e.inspected).map((e) => e.id);
    const live = g.view().hypotheses.find((h) => !h.rejected).id;
    if (read.length) g.dispatch({ type: 'commit', hypothesis: live, confidence: 0.6, citations: read.slice(0, 2) });
  }
  return g;
}

test('replaying a save reproduces the final score and state exactly, across many random playthroughs', () => {
  let finished = 0;
  const outcomes = new Set();
  for (let seed = 0; seed < 150; seed++) {
    const g = randomPlaythrough(seed);
    const r = load(c, serialize(createSave(g, c)));
    assert.strictEqual(r.error, undefined, `seed ${seed}: ${r.error && r.error.message}`);
    assert.deepStrictEqual(r.game.view(), g.view(), `seed ${seed}: state differs`);
    if (g.finished) {
      finished++;
      assert.deepStrictEqual(r.game.view().outcome.score, g.view().outcome.score);
      assert.strictEqual(r.verified, true);
      outcomes.add(g.view().outcome.score.outcome);
    }
  }
  assert.ok(finished >= 100, `only ${finished} playthroughs finished`);
  assert.ok(outcomes.size >= 2, 'the random player should reach more than one outcome');
});

test('a replayed game accepts exactly the same further actions as the original', () => {
  const g = createGame(c, { seed: 5 });
  g.dispatch({ type: 'inspect', evidence: 'E06' });
  const r = load(c, serialize(createSave(g, c)));
  assert.deepStrictEqual(r.game.dispatch({ type: 'corroborate', evidence: 'E06' }), g.dispatch({ type: 'corroborate', evidence: 'E06' }));
});

test('budget is consistent after replay', () => {
  const g = createGame(c, { seed: 2 });
  g.dispatch({ type: 'inspect', evidence: 'E01' });
  const r = load(c, serialize(createSave(g, c)));
  assert.strictEqual(r.game.view().budget_remaining, c.budget - COSTS.inspect);
});
