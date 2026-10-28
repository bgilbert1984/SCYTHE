/**
 * scoring.js — Scoring a committed finding (design §3.5, §3.6).
 *
 * Pure and deterministic. Rewards being right and appropriately sure; penalises
 * unsupported certainty more than honest uncertainty.
 *
 * All constants are *initial* playtest-tunable values (design §10).
 */

const { countedEvidence, effectiveLikelihood } = require('../cases/lib/posterior');

const WEIGHTS = { correctness: 0.35, calibration: 0.35, citation: 0.2, efficiency: 0.1 };

const CALIBRATION_TOLERANCE = 0.15; // |stated - reference| within this reads as "well calibrated"
const OVERCONFIDENCE_FACTOR = 1.5; // being too sure costs more than being too unsure
const CALIBRATION_SCALE = 0.25; // squared weighted gap at which calibration reaches 0
const SUPPORT_RATIO = 1.5; // a citation supports a claim if it favours it by at least this factor
const SUPPORT_MARGIN = 0.05; // the evidence must favour the claim by this much over every rival
const FULL_CREDIT_CITATIONS = 3; // supporting citations needed for full citation credit
const BAD_CITATION_PENALTY = 0.2; // credit lost per citation that does not help the claim

const OUTCOMES = {
  sound_correct: 'Sound and correct',
  correct_overconfident: 'Correct, but overconfident',
  sound_wrong: 'Sound, but wrong: the evidence was misleading',
  unsupported: 'Unsupported: your evidence pointed elsewhere',
};
const CALIBRATION = {
  well_calibrated: 'Well calibrated',
  overconfident: 'Overconfident',
  underconfident: 'Underconfident',
};

/** How each cited item bears on the committed claim. */
function classifyCitations(c, committed, citations, state) {
  const byId = new Map(c.evidence.map((e) => [e.id, e]));
  const unique = [...new Set(citations)].sort();
  const counted = new Set(countedEvidence(c, unique));
  const others = c.hypotheses.map((h) => h.id).filter((id) => id !== committed);
  return unique.map((id) => {
    if (!counted.has(id)) return { id, status: 'double_counted' };
    const lk = effectiveLikelihood(byId.get(id), state);
    const ratio = lk[committed] / (others.reduce((a, o) => a + lk[o], 0) / others.length);
    if (ratio >= SUPPORT_RATIO) return { id, status: 'supports' };
    if (ratio <= 1 / SUPPORT_RATIO) return { id, status: 'against' };
    return { id, status: 'not_diagnostic' };
  });
}

function calibrationScore(stated, reference) {
  const gap = stated > reference ? OVERCONFIDENCE_FACTOR * (stated - reference) : reference - stated;
  return 100 * Math.max(0, 1 - (gap * gap) / CALIBRATION_SCALE);
}

function calibrationLabel(stated, reference) {
  if (stated - reference > CALIBRATION_TOLERANCE) return 'overconfident';
  if (reference - stated > CALIBRATION_TOLERANCE) return 'underconfident';
  return 'well_calibrated';
}

/**
 * @param c         the case
 * @param finding   { hypothesis, confidence, citations, budgetRemaining }
 * @param state     the same state referencePosterior takes (for effective likelihoods)
 * @param posterior the reference posterior p* at commit
 */
function scoreFinding(c, finding, state, posterior) {
  const { hypothesis, confidence, citations, budgetRemaining } = finding;
  const isTruth = Boolean(c.hypotheses.find((h) => h.id === hypothesis).is_truth);
  const reference = posterior[hypothesis];
  const rivalBest = Math.max(...Object.entries(posterior).filter(([id]) => id !== hypothesis).map(([, p]) => p));
  const supported = reference >= rivalBest + SUPPORT_MARGIN;
  const calLabel = calibrationLabel(confidence, reference);

  let outcome;
  if (!supported) outcome = 'unsupported';
  else if (!isTruth) outcome = 'sound_wrong';
  else outcome = calLabel === 'overconfident' ? 'correct_overconfident' : 'sound_correct';

  const report = classifyCitations(c, hypothesis, citations, state);
  const supports = report.filter((r) => r.status === 'supports').length;
  const bad = report.filter((r) => r.status !== 'supports').length;
  const citationCredit = Math.max(0, Math.min(1, supports / FULL_CREDIT_CITATIONS) - BAD_CITATION_PENALTY * bad);

  const components = {
    correctness: isTruth ? 100 : 0,
    calibration: calibrationScore(confidence, reference),
    citation: 100 * citationCredit,
    // Only credited when the evidence supports the conclusion, so guessing early never pays.
    efficiency: supported ? 100 * (budgetRemaining / c.budget) * reference : 0,
  };
  const total = Object.entries(WEIGHTS).reduce((a, [k, w]) => a + w * components[k], 0);

  return {
    total: Math.round(total),
    components: Object.fromEntries(Object.entries(components).map(([k, v]) => [k, Math.round(v)])),
    weights: { ...WEIGHTS },
    outcome,
    outcome_text: OUTCOMES[outcome],
    calibration: calLabel,
    calibration_text: CALIBRATION[calLabel],
    brier: (confidence - (isTruth ? 1 : 0)) ** 2,
    citations: report,
  };
}

module.exports = { scoreFinding, classifyCitations, calibrationScore, WEIGHTS, OUTCOMES, CALIBRATION };
