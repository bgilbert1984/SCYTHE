/**
 * achievements.js — Achievement definitions and their rules (design §9).
 *
 * Pure. Achievements are derived from a replayed game, never from a claim by the
 * page: the caller replays the save (runtime/save.js) and passes the verified game.
 * Ids are the stable API names to register in the Steamworks partner site.
 */

const DEFINITIONS = [
  {
    id: 'ACH_FIRST_FINDING',
    name: 'On the Record',
    description: 'Commit your first finding.',
    rule: (g) => g.finished,
  },
  {
    id: 'ACH_SOUND_AND_RIGHT',
    name: 'Sound and Right',
    description: 'Reach the right conclusion on evidence that supported it, without overclaiming.',
    rule: (g) => g.finished && g.outcome.score.outcome === 'sound_correct',
  },
  {
    id: 'ACH_CALIBRATED',
    name: 'Knows What It Knows',
    description: 'Commit a finding whose stated confidence matched what the evidence supported.',
    rule: (g) => g.finished && g.outcome.score.calibration === 'well_calibrated',
  },
  {
    id: 'ACH_SHOWED_WORK',
    name: 'Show Your Work',
    description: 'Cite three pieces of evidence that each support your finding.',
    rule: (g) => g.finished && g.outcome.score.citations.filter((c) => c.status === 'supports').length >= 3,
  },
  {
    id: 'ACH_CHANGED_CONDITIONS',
    name: 'Different Weather, Different Story',
    description: 'Re-evaluate evidence under other conditions.',
    rule: (g) => g.actions.some((a) => a.type === 'reevaluate'),
  },
  {
    id: 'ACH_RULED_OUT',
    name: 'Process of Elimination',
    description: 'Rule out an explanation and still reach the right conclusion.',
    rule: (g) => g.finished && g.outcome.is_correct && g.actions.some((a) => a.type === 'reject'),
  },
  {
    id: 'ACH_LEAN',
    name: 'Lean Investigation',
    description: 'Reach a sound, correct finding with at least half your action points unspent.',
    rule: (g) => g.finished && g.outcome.score.outcome === 'sound_correct' && g.outcome.budget_remaining * 2 >= g.budget,
  },
  {
    id: 'ACH_EXCELLENT',
    name: 'Case Closed',
    description: 'Score 90 or more.',
    rule: (g) => g.finished && g.outcome.score.total >= 90,
  },
];

/** Ids earned by this game, in definition order. */
function evaluate(game) {
  const v = game.view();
  const facts = { finished: v.finished, outcome: v.outcome, actions: game.actionLog, budget: v.budget };
  return DEFINITIONS.filter((d) => d.rule(facts)).map((d) => d.id);
}

/** The registration manifest for the Steamworks partner site (names and descriptions only). */
function manifest() {
  return DEFINITIONS.map(({ id, name, description }) => ({ id, name, description }));
}

module.exports = { DEFINITIONS, evaluate, manifest };
