const test = require('node:test');
const assert = require('node:assert');
const { createGame } = require('../runtime/engine');
const { createSave, serialize } = require('../runtime/save');
const { DEFINITIONS, evaluate, manifest } = require('./achievements');
const { decide } = require('./sync');
const { createPlatform, NO_PROVIDER } = require('./service');
const steam = require('../desktop/steam');
const c = require('../cases/signal_ghost/case.json');

const WIN = [
  { type: 'inspect', evidence: 'E07' }, { type: 'inspect', evidence: 'E14' }, { type: 'inspect', evidence: 'E02' },
  { type: 'reevaluate', evidence: 'E02', variant: 'fog_morning' },
  { type: 'commit', hypothesis: 'H3', confidence: 0.8, citations: ['E14', 'E02'] },
];

function play(actions, seed = 3) {
  const g = createGame(c, { seed });
  for (const a of actions) assert.ok(g.dispatch(a).ok, JSON.stringify(a));
  return g;
}
const text = (g) => serialize(createSave(g, c));

/** An in-memory provider that records what it was asked to do. */
function fakeProvider(over = {}) {
  const p = {
    name: 'fake', available: true, granted: new Set(), cloud: null, calls: [],
    async unlock(id) { p.calls.push(`unlock:${id}`); p.granted.add(id); },
    async unlocked() { return [...p.granted]; },
    async cloudRead() { return p.cloud; },
    async cloudWrite(t) { p.calls.push('write'); p.cloud = t; },
    async cloudClear() { p.calls.push('clear'); p.cloud = null; },
    ...over,
  };
  return p;
}

test('achievement ids are unique, stable-looking, and documented', () => {
  const ids = DEFINITIONS.map((d) => d.id);
  assert.strictEqual(new Set(ids).size, ids.length);
  for (const d of DEFINITIONS) {
    assert.match(d.id, /^ACH_[A-Z_]+$/);
    assert.ok(d.name && d.description);
  }
  assert.deepStrictEqual(manifest().map((m) => m.id), ids);
  assert.ok(manifest().every((m) => Object.keys(m).sort().join() === 'description,id,name'));
});

test('an unfinished game earns only what it has actually done', () => {
  assert.deepStrictEqual(evaluate(createGame(c, { seed: 1 })), []);
  const g = play([WIN[0], WIN[1], WIN[2], { type: 'reevaluate', evidence: 'E02', variant: 'fog_morning' }]);
  assert.deepStrictEqual(evaluate(g), ['ACH_CHANGED_CONDITIONS']);
});

test('a sound, correct finding earns the finishing achievements', () => {
  const got = evaluate(play(WIN));
  for (const id of ['ACH_FIRST_FINDING', 'ACH_SOUND_AND_RIGHT', 'ACH_CHANGED_CONDITIONS']) assert.ok(got.includes(id), id);
});

test('a wrong or unsupported finding does not earn the quality achievements', () => {
  const g = play([
    { type: 'inspect', evidence: 'E01' },
    { type: 'commit', hypothesis: 'H1', confidence: 0.95, citations: ['E01'] },
  ]);
  const got = evaluate(g);
  assert.ok(got.includes('ACH_FIRST_FINDING'));
  for (const id of ['ACH_SOUND_AND_RIGHT', 'ACH_RULED_OUT', 'ACH_LEAN', 'ACH_EXCELLENT', 'ACH_CALIBRATED']) assert.ok(!got.includes(id), id);
});

test('evaluation is deterministic and order of definitions is kept', () => {
  assert.deepStrictEqual(evaluate(play(WIN)), evaluate(play(WIN)));
  const order = DEFINITIONS.map((d) => d.id);
  const got = evaluate(play(WIN));
  assert.deepStrictEqual(got, order.filter((id) => got.includes(id)));
});

test('decide: nothing, one side, identical', () => {
  const a = { save: createSave(play(WIN.slice(0, 2)), c) };
  assert.strictEqual(decide(null, null).action, 'none');
  assert.strictEqual(decide(a, null).action, 'push');
  assert.strictEqual(decide(null, a).action, 'pull');
  assert.strictEqual(decide(a, a).action, 'none');
});

test('decide: the longer history of the same game wins; divergence and other games are conflicts', () => {
  const short = { save: createSave(play(WIN.slice(0, 2)), c) };
  const long = { save: createSave(play(WIN.slice(0, 3)), c) };
  assert.strictEqual(decide(long, short).action, 'push');
  assert.strictEqual(decide(short, long).action, 'pull');
  const other = { save: createSave(play([WIN[0], { type: 'inspect', evidence: 'E01' }, WIN[2]]), c) };
  const d = decide(other, long);
  assert.strictEqual(d.action, 'conflict');
  assert.deepStrictEqual(d.local, { actions: 3, finished: false });
  const differentSeed = { save: createSave(play(WIN.slice(0, 3), 4), c) };
  assert.strictEqual(decide(differentSeed, long).action, 'conflict');
});

test('onSave grants new achievements once and reports them by name', async () => {
  const p = fakeProvider();
  const svc = createPlatform(p, c);
  const r1 = await svc.onSave(text(play(WIN)));
  assert.ok(r1.ok && r1.unlocked.length >= 3);
  assert.ok(r1.unlocked.every((a) => a.id && a.name));
  const r2 = await svc.onSave(text(play(WIN)));
  assert.deepStrictEqual(r2.unlocked, []);
  assert.strictEqual(p.calls.filter((x) => x.startsWith('unlock:')).length, r1.unlocked.length);
});

test('onSave mirrors an unfinished save to the cloud and clears it when finished', async () => {
  const p = fakeProvider();
  const svc = createPlatform(p, c);
  const partial = text(play(WIN.slice(0, 2)));
  await svc.onSave(partial);
  assert.strictEqual(p.cloud, partial);
  await svc.onSave(text(play(WIN)));
  assert.strictEqual(p.cloud, null);
});

test('onSave refuses anything that does not replay: edited, malformed, oversized', async () => {
  const p = fakeProvider();
  const svc = createPlatform(p, c);
  const forged = JSON.parse(text(play(WIN)));
  forged.actions.pop();
  forged.actions.push({ type: 'commit', hypothesis: 'H3', confidence: 0.5, citations: ['E14'] });
  for (const bad of [JSON.stringify(forged), 'not json', '', null, 42, 'x'.repeat(2 * 1048576)]) {
    const r = await svc.onSave(bad);
    assert.strictEqual(r.ok, false);
    assert.deepStrictEqual(r.unlocked, []);
  }
  assert.deepStrictEqual(p.calls, []);
});

test('a save made for a changed case is not trusted for achievements', async () => {
  const p = fakeProvider();
  const svc = createPlatform(p, { ...c, budget: c.budget + 1 });
  const r = await svc.onSave(text(play(WIN)));
  assert.strictEqual(r.ok, false);
  assert.deepStrictEqual(p.calls, []);
});

test('provider failures never break play', async () => {
  const boom = async () => { throw new Error('steam down'); };
  const svc = createPlatform(fakeProvider({ unlock: boom, unlocked: boom, cloudRead: boom, cloudWrite: boom, cloudClear: boom }), c);
  const r = await svc.onSave(text(play(WIN)));
  assert.strictEqual(r.ok, true);
  // An unreadable cloud looks empty, so the local save is offered to it; the failed write is swallowed.
  assert.deepStrictEqual(await svc.onStart(text(play(WIN.slice(0, 2)))), { action: 'push' });
});

test('onStart: pushes a local-only save, pulls a cloud-only save, and flags a conflict', async () => {
  const mine = text(play(WIN.slice(0, 3)));
  const p = fakeProvider();
  const svc = createPlatform(p, c);
  assert.deepStrictEqual(await svc.onStart(mine), { action: 'push' });
  assert.strictEqual(p.cloud, mine);

  const q = fakeProvider({ cloud: mine });
  assert.deepStrictEqual(await createPlatform(q, c).onStart(null), { action: 'pull', text: mine });

  const theirs = text(play([WIN[0], { type: 'inspect', evidence: 'E01' }, WIN[2]]));
  const r = await createPlatform(fakeProvider({ cloud: theirs }), c).onStart(mine);
  assert.strictEqual(r.action, 'conflict');
  assert.strictEqual(r.localText, mine);
  assert.strictEqual(r.cloudText, theirs);
});

test('onStart ignores a damaged cloud save rather than adopting it', async () => {
  const mine = text(play(WIN.slice(0, 3)));
  const p = fakeProvider({ cloud: '{"format":"scythe-game-save","version":1}' });
  const r = await createPlatform(p, c).onStart(mine);
  assert.strictEqual(r.action, 'push');
  assert.strictEqual(p.cloud, mine);
});

test('with no provider the service is inert and reports unavailable', async () => {
  const svc = createPlatform(null, c);
  assert.strictEqual(svc.available, false);
  assert.strictEqual(svc.name, NO_PROVIDER.name);
  assert.deepStrictEqual(await svc.onStart(null), { action: 'none' });
  const r = await svc.onSave(text(play(WIN)));
  assert.ok(r.ok);
});

/** A stand-in for the steamworks.js client. */
function fakeClient({ cloud = true } = {}) {
  const files = new Map();
  const on = new Set();
  return {
    files, on,
    achievement: { activate: (n) => { on.add(n); return true; }, isActivated: (n) => on.has(n) },
    cloud: {
      isEnabledForApp: () => cloud, isEnabledForAccount: () => cloud,
      fileExists: (n) => files.has(n), readFile: (n) => files.get(n),
      writeFile: (n, t) => { files.set(n, t); return true; }, deleteFile: (n) => files.delete(n),
    },
  };
}

test('steam provider maps the contract onto the client', async () => {
  const client = fakeClient();
  const ids = DEFINITIONS.map((d) => d.id);
  const p = steam.providerFor(client, ids);
  assert.deepStrictEqual(await p.unlocked(), []);
  await p.unlock('ACH_FIRST_FINDING');
  assert.deepStrictEqual(await p.unlocked(), ['ACH_FIRST_FINDING']);
  assert.strictEqual(await p.cloudRead(), null);
  await p.cloudWrite('abc');
  assert.strictEqual(client.files.get(steam.CLOUD_FILE), 'abc');
  assert.strictEqual(await p.cloudRead(), 'abc');
  await p.cloudClear();
  assert.strictEqual(client.files.size, 0);
});

test('steam provider leaves the cloud alone when the player has Steam Cloud off', async () => {
  const client = fakeClient({ cloud: false });
  const p = steam.providerFor(client, []);
  await p.cloudWrite('abc');
  assert.strictEqual(client.files.size, 0);
  assert.strictEqual(await p.cloudRead(), null);
});

test('steam end to end: a full game unlocks achievements and leaves no cloud save', async () => {
  const client = fakeClient();
  const svc = createPlatform(steam.providerFor(client, DEFINITIONS.map((d) => d.id)), c);
  await svc.onSave(text(play(WIN.slice(0, 3))));
  assert.ok(client.files.has(steam.CLOUD_FILE));
  await svc.onSave(text(play(WIN)));
  assert.ok(!client.files.has(steam.CLOUD_FILE));
  assert.ok(client.on.has('ACH_SOUND_AND_RIGHT'));
});

test('steam connect degrades to no provider instead of throwing', () => {
  assert.strictEqual(steam.connect(0, [], () => ({})).provider, null);
  assert.match(steam.connect(480, [], () => { throw new Error('missing'); }).reason, /not installed/);
  assert.match(steam.connect(480, [], () => ({ init: () => { throw new Error('no steam'); } })).reason, /not running/);
  assert.ok(steam.connect(480, [], () => ({ init: () => fakeClient() })).provider);
});
