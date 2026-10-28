const test = require('node:test');
const assert = require('node:assert');
const vm = require('node:vm');
const { build } = require('./build');
const { createStore, SAVE_KEY } = require('./src/store');
const { createGame, replay } = require('../runtime/engine');
const c = require('../cases/signal_ghost/case.json');

const memStorage = () => {
  const m = new Map();
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k), _m: m };
};
const save = (actions, seed = 5) => ({ case_id: c.case_id, seed, actions });

test('store round-trips an in-progress save and rebuilds the game by replay', () => {
  const g = createGame(c, { seed: 5 });
  g.dispatch({ type: 'inspect', evidence: 'E07' });
  g.dispatch({ type: 'inspect', evidence: 'E02' });
  const s = createStore(memStorage());
  s.saveInProgress(save(g.actionLog));
  const { save: loaded } = s.loadInProgress(c.case_id);
  const { game, error } = replay(c, loaded.seed, loaded.actions);
  assert.strictEqual(error, null);
  assert.deepStrictEqual(game.actionLog, g.actionLog);
});

test('store reports nothing saved, then clears', () => {
  const s = createStore(memStorage());
  assert.deepStrictEqual(s.loadInProgress(c.case_id), {});
  s.saveInProgress(save([]));
  assert.strictEqual(s.hasInProgress(), true);
  s.clearInProgress();
  assert.strictEqual(s.hasInProgress(), false);
});

test('store rejects damaged, mismatched, and foreign saves with a clear message', () => {
  const st = memStorage();
  const s = createStore(st);
  st.setItem(SAVE_KEY, '{not json');
  assert.match(s.loadInProgress(c.case_id).error, /damaged/);
  s.saveInProgress(save([]));
  const raw = JSON.parse(st.getItem(SAVE_KEY));
  st.setItem(SAVE_KEY, JSON.stringify({ ...raw, contract_version: 'game_case_9.9.9' }));
  assert.match(s.loadInProgress(c.case_id).error, /different version/);
  st.setItem(SAVE_KEY, JSON.stringify({ ...raw, case_id: 'other' }));
  assert.match(s.loadInProgress(c.case_id).error, /different case/);
});

test('store falls back to memory when storage is missing or throws', () => {
  const broken = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); }, removeItem() { throw new Error('blocked'); } };
  for (const storage of [null, broken]) {
    const s = createStore(storage);
    s.saveInProgress(save([]));
    assert.strictEqual(s.persistent, false);
    assert.strictEqual(s.hasInProgress(), true);
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
