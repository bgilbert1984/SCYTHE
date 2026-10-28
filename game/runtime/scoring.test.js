const test = require('node:test');
const assert = require('node:assert');
const { createGame } = require('./engine');
const { calibrationScore, WEIGHTS } = require('./scoring');
const { referencePosterior } = require('../cases/lib/posterior');
const { validateCase } = require('../cases/lib/validate');
const c = require('../cases/signal_ghost/case.json');

const inspect = (ids) => ids.map((evidence) => ({ type: 'inspect', evidence }));
const FERRY = inspect(['E01', 'E04', 'E06', 'E08', 'E09', 'W1']);
const FULL = [
  ...inspect(['E07', 'E14', 'E11', 'E13', 'E02', 'E03']),
  { type: 'reevaluate', evidence: 'E02', variant: 'fog_morning' },
  { type: 'reevaluate', evidence: 'E03', variant: 'post_rain_evening' },
];

function play(actions, commit, seed = 1) {
  const g = createGame(c, { seed });
  for (const a of actions) assert.ok(g.dispatch(a).ok, JSON.stringify(a));
  const r = g.dispatch({ type: 'commit', ...commit });
  assert.ok(r.ok, JSON.stringify(r.error));
  return r.result;
}

test('weights sum to 1', () => {
  assert.ok(Math.abs(Object.values(WEIGHTS).reduce((a, b) => a + b, 0) - 1) < 1e-9);
});

test('unsupported certainty scores worse than honest uncertainty, on the same evidence', () => {
  const sure = play(FERRY, { hypothesis: 'H1', confidence: 0.9, citations: ['E06', 'W1'] });
  const honest = play(FERRY, { hypothesis: 'H1', confidence: 0.4, citations: ['E06', 'W1'] });
  assert.ok(honest.score.total > sure.score.total + 20, `${honest.score.total} vs ${sure.score.total}`);
  assert.strictEqual(sure.score.calibration, 'overconfident');
  assert.strictEqual(honest.score.calibration, 'well_calibrated');
});

test('being too sure costs more than being equally too unsure', () => {
  assert.ok(calibrationScore(0.95, 0.75) < calibrationScore(0.55, 0.75));
  assert.strictEqual(calibrationScore(0.75, 0.75), 100);
});

test('a thorough, well-calibrated finding scores highest', () => {
  const full = play(FULL, { hypothesis: 'H3', confidence: 0.95, citations: ['E14', 'E11', 'E13'] });
  const thin = play(inspect(['E14']), { hypothesis: 'H3', confidence: 0.75, citations: ['E14'] });
  const under = play(FULL, { hypothesis: 'H3', confidence: 0.5, citations: ['E14', 'E11', 'E13'] });
  assert.ok(full.score.total > thin.score.total);
  assert.ok(full.score.total > under.score.total + 20);
  assert.strictEqual(under.score.calibration, 'underconfident');
});

test('guessing early never pays: no support means no efficiency credit and an Unsupported outcome', () => {
  const lucky = play(inspect(['E01']), { hypothesis: 'H3', confidence: 0.9, citations: ['E01'] });
  const full = play(FULL, { hypothesis: 'H3', confidence: 0.95, citations: ['E14', 'E11', 'E13'] });
  assert.strictEqual(lucky.is_correct, true);
  assert.strictEqual(lucky.score.outcome, 'unsupported');
  assert.strictEqual(lucky.score.components.efficiency, 0);
  assert.ok(lucky.score.total < full.score.total - 40);
});

test('all four outcomes are reachable', () => {
  const got = new Set([
    play(FULL, { hypothesis: 'H3', confidence: 0.95, citations: ['E14', 'E11', 'E13'] }).score.outcome,
    play(inspect(['E14']), { hypothesis: 'H3', confidence: 1, citations: ['E14'] }).score.outcome,
    play(FERRY, { hypothesis: 'H1', confidence: 0.4, citations: ['E06'] }).score.outcome,
    play(inspect(['E01']), { hypothesis: 'H1', confidence: 0.5, citations: ['E01'] }).score.outcome,
  ]);
  assert.deepStrictEqual([...got].sort(), ['correct_overconfident', 'sound_correct', 'sound_wrong', 'unsupported']);
});

test('citations: summaries are double counted, unhelpful and contrary items are flagged', () => {
  const r = play(
    [...inspect(['E01', 'E04', 'E08', 'E10', 'E14', 'E11'])],
    { hypothesis: 'H3', confidence: 0.8, citations: ['E01', 'E04', 'E08', 'E10', 'E14', 'E11'] },
  );
  const status = Object.fromEntries(r.score.citations.map((x) => [x.id, x.status]));
  assert.strictEqual(status.E08, 'double_counted');
  assert.strictEqual(status.E10, 'against');
  assert.strictEqual(status.E01, 'not_diagnostic');
  assert.strictEqual(status.E14, 'supports');
  assert.strictEqual(status.E11, 'supports');
});

test('citing more supporting evidence raises the citation score; padding with junk lowers it', () => {
  const base = [...inspect(['E14', 'E11', 'E13', 'E01', 'E07'])];
  const three = play(base, { hypothesis: 'H3', confidence: 0.9, citations: ['E14', 'E11', 'E13'] });
  const one = play(base, { hypothesis: 'H3', confidence: 0.9, citations: ['E14'] });
  const padded = play(base, { hypothesis: 'H3', confidence: 0.9, citations: ['E14', 'E11', 'E13', 'E01', 'E07'] });
  assert.ok(three.score.components.citation > one.score.components.citation);
  assert.ok(three.score.components.citation > padded.score.components.citation);
});

test('Brier is reported against the true outcome', () => {
  assert.ok(Math.abs(play(inspect(['E14']), { hypothesis: 'H3', confidence: 0.8, citations: ['E14'] }).score.brier - 0.04) < 1e-9);
  assert.ok(Math.abs(play(inspect(['E14']), { hypothesis: 'H1', confidence: 0.8, citations: ['E14'] }).score.brier - 0.64) < 1e-9);
});

test('scores are integers in range, deterministic, and part of the outcome', () => {
  const a = play(FULL, { hypothesis: 'H3', confidence: 0.9, citations: ['E14'] }, 4);
  const b = play(FULL, { hypothesis: 'H3', confidence: 0.9, citations: ['E14'] }, 4);
  assert.deepStrictEqual(a.score, b.score);
  assert.ok(Number.isInteger(a.score.total) && a.score.total >= 0 && a.score.total <= 100);
  assert.deepStrictEqual(Object.keys(a.player_confidence).sort(), ['H1', 'H2', 'H3']);
});

test('no-match can be weak evidence; unresolved never is (cf. #214)', () => {
  const base = { inspected: ['W2'] };
  const none = referencePosterior(c, base);
  const noMatch = referencePosterior(c, { ...base, corroborated: { W2: 1 } });
  assert.notDeepStrictEqual(noMatch, none, 'a no-match with authored likelihood should move the posterior');
  const w1 = { inspected: ['W1'] };
  assert.deepStrictEqual(referencePosterior(c, { ...w1, corroborated: { W1: 1 } }), referencePosterior(c, w1));
  assert.deepStrictEqual(referencePosterior(c, { inspected: ['E06'], corroborated: { E06: 1 } }), referencePosterior(c, { inspected: ['E06'] }));
});

test('the validator rejects an unresolved outcome that carries a likelihood', () => {
  const bad = JSON.parse(JSON.stringify(c));
  bad.evidence.find((e) => e.id === 'W1').corroboration.outcomes[1].likelihood = { H1: 0.5, H2: 0.5, H3: 0.5 };
  assert.ok(validateCase(bad).some((e) => e.includes('unresolved')));
});
