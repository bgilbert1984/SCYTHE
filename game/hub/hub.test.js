const test = require('node:test');
const assert = require('node:assert');
const vm = require('node:vm');
const { build } = require('./build');
const { createStore, SAVE_KEY } = require('./src/store');
const { createGame } = require('../runtime/engine');
const c = require('../cases/signal_ghost/case.json');

const memStorage = () => {
  const m = new Map();
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k), _m: m };
};
const played = (seed = 5) => {
  const g = createGame(c, { seed });
  g.dispatch({ type: 'inspect', evidence: 'E07' });
  g.dispatch({ type: 'inspect', evidence: 'E02' });
  return g;
};

test('store round-trips an in-progress save and rebuilds the game by replay', () => {
  const g = played();
  const s = createStore(memStorage());
  s.saveInProgress(g, c);
  const r = s.loadInProgress(c);
  assert.strictEqual(r.error, undefined);
  assert.deepStrictEqual(r.game.actionLog, g.actionLog);
  assert.deepStrictEqual(r.game.view(), g.view());
  assert.strictEqual(r.save.seed, 5);
});

test('store saves a real, verifiable save file', () => {
  const st = memStorage();
  createStore(st).saveInProgress(played(), c);
  const raw = JSON.parse(st.getItem(SAVE_KEY));
  assert.strictEqual(raw.format, 'scythe-game-save');
  assert.ok(raw.checksum && raw.case_hash);
});

test('store reports nothing saved, then clears', () => {
  const s = createStore(memStorage());
  assert.deepStrictEqual(s.loadInProgress(c), {});
  s.saveInProgress(played(), c);
  assert.strictEqual(s.hasInProgress(), true);
  s.clearInProgress();
  assert.strictEqual(s.hasInProgress(), false);
});

test('store rejects damaged, edited, and mismatched saves with a clear message', () => {
  const st = memStorage();
  const s = createStore(st);
  st.setItem(SAVE_KEY, '{not json');
  assert.strictEqual(s.loadInProgress(c).error.code, 'MALFORMED');
  s.saveInProgress(played(), c);
  const raw = JSON.parse(st.getItem(SAVE_KEY));
  st.setItem(SAVE_KEY, JSON.stringify({ ...raw, seed: 6 }));
  assert.strictEqual(s.loadInProgress(c).error.code, 'CORRUPT');
  const other = JSON.parse(JSON.stringify(c));
  other.evidence[0].likelihood.H1 = 0.9;
  st.setItem(SAVE_KEY, JSON.stringify(raw));
  assert.strictEqual(s.loadInProgress(other).error.code, 'CASE_CHANGED');
});

test('store upgrades a save written by the first Hub', () => {
  const g = played(8);
  const st = memStorage();
  st.setItem(SAVE_KEY, JSON.stringify({ store_version: 1, contract_version: c.contract_version, case_id: c.case_id, seed: 8, actions: g.actionLog }));
  const r = createStore(st).loadInProgress(c);
  assert.strictEqual(r.error, undefined);
  assert.deepStrictEqual(r.game.view(), g.view());
  assert.strictEqual(r.save.format, 'scythe-game-save');
  st.setItem(SAVE_KEY, JSON.stringify({ store_version: 1, contract_version: 'game_case_9.9.9', case_id: c.case_id, seed: 8, actions: [] }));
  assert.strictEqual(createStore(st).loadInProgress(c).error.code, 'CONTRACT_MISMATCH');
});

test('store falls back to memory when storage is missing or throws', () => {
  const broken = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); }, removeItem() { throw new Error('blocked'); } };
  for (const storage of [null, broken]) {
    const s = createStore(storage);
    s.saveInProgress(played(), c);
    assert.strictEqual(s.persistent, false);
    assert.strictEqual(s.hasInProgress(), true);
    assert.strictEqual(s.loadInProgress(c).error, undefined);
    s.recordResult({ hypothesis: 'H3' });
    assert.strictEqual(s.career().length, 1);
  }
});

test('career accumulates results and survives a corrupt value', () => {
  const st = memStorage();
  const s = createStore(st);
  s.recordResult({ hypothesis: 'H1' });
  s.recordResult({ hypothesis: 'H3' });
  assert.deepStrictEqual(s.career().map((r) => r.hypothesis), ['H1', 'H3']);
  st.setItem('scythe_game_career', 'garbage');
  assert.deepStrictEqual(s.career(), []);
});

test('the built bundle loads in a bare context and exposes a working game', () => {
  const html = build();
  const bundle = html.split('<script>')[1].split('</script>')[0];
  const ctx = { window: {} };
  vm.createContext(ctx);
  vm.runInContext(bundle, ctx);
  const g = ctx.window.__game;
  assert.strictEqual(g.caseData.case_id, 'signal_ghost');
  const game = g.createGame(g.caseData, { seed: 1 });
  assert.strictEqual(game.dispatch({ type: 'inspect', evidence: 'E14' }).ok, true);
});

test('the page offers New Investigation, Continue, and Career, and no operator links', () => {
  const html = build();
  for (const t of ['New Investigation', 'Continue Investigation', 'Career']) assert.ok(html.includes(t), t);
  assert.ok(!/mission[ _-]?hub|operator (hub|dashboard)/i.test(html), 'player page must not reference the operator Hub');
});

test('the workbench surfaces provenance, independence, and the case board', () => {
  const html = build();
  for (const t of ['Independent agreement', 'not independent', 'Reliability', 'No evidence linked yet', 'share an upstream source']) {
    assert.ok(html.includes(t), t);
  }
});

test('the page can export and open save files', () => {
  const html = build();
  for (const t of ['Export save file', 'Open Save File', 'Replay verified']) assert.ok(html.includes(t), t);
});

test('the bundled save module works in a bare context', () => {
  const html = build();
  const ctx = { window: {} };
  vm.createContext(ctx);
  vm.runInContext(html.split('<script>')[1].split('</script>')[0], ctx);
  const g = ctx.window.__game;
  const game = g.createGame(g.caseData, { seed: 3 });
  game.dispatch({ type: 'inspect', evidence: 'E14' });
  game.dispatch({ type: 'commit', hypothesis: 'H3', confidence: 0.7, citations: ['E14'] });
  const text = g.serialize(g.createSave(game, g.caseData));
  const r = g.loadSave(g.caseData, text);
  assert.strictEqual(r.verified, true);
  assert.strictEqual(r.game.view().outcome.score.total, game.view().outcome.score.total);
});
