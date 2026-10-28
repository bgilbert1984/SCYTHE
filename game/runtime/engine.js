/**
 * engine.js — Isolated game runtime (design §3, §6).
 *
 * Pure and in-memory. It takes an authored case and a seed, and accepts player
 * actions. It has no network, process, filesystem, or environment access, and
 * shares nothing with live RF acquisition or production services.
 *
 * Invalid actions are rejected explicitly with a code and leave state untouched.
 * Player-visible state comes only from view(); ground truth never leaves the engine
 * until the game is committed.
 */

const { draw } = require('./rng');
const { referencePosterior } = require('../cases/lib/posterior');
const { scoreFinding } = require('./scoring');

const COSTS = {
  inspect: 1,
  compare: 1,
  corroborate: 2,
  reevaluate: 2,
  restore: 1,
  link: 0,
  unlink: 0,
  reject: 0,
  set_confidence: 0,
  commit: 0,
};
const RELATIONS = ['supports', 'contradicts', 'neutral'];
const START_VARIANT = 'clear_day';

class Rejection {
  constructor(code, message) {
    this.code = code;
    this.message = message;
  }
}
const reject = (code, message) => {
  throw new Rejection(code, message);
};

const isId = (v) => typeof v === 'string' && v.length > 0;

const LEAN_RATIO = 1.25;

/** Hypotheses an item actually leans toward. A neutral item leans toward none, so it corroborates nothing. */
function favoredSet(e) {
  const entries = Object.entries(e.likelihood);
  return new Set(entries
    .filter(([h, v]) => {
      const others = entries.filter(([o]) => o !== h).map(([, x]) => x);
      return v >= 0.5 && v >= LEAN_RATIO * (others.reduce((a, b) => a + b, 0) / others.length);
    })
    .map(([h]) => h));
}

function sharesUpstream(a, b) {
  const da = new Set(a.derived_from || []);
  const db = new Set(b.derived_from || []);
  if (da.has(b.id) || db.has(a.id)) return true;
  for (const x of da) if (db.has(x)) return true;
  return false;
}

class Game {
  constructor(caseData, { seed = 0 } = {}) {
    this._case = caseData;
    this._seed = seed;
    this._evidence = new Map(caseData.evidence.map((e) => [e.id, e]));
    this._sources = new Map(caseData.sources.map((s) => [s.id, s]));
    this._hyps = new Map(caseData.hypotheses.map((h) => [h.id, h]));
    this._variants = new Map(caseData.variants.map((v) => [v.variant_id, v]));

    this._budget = caseData.budget;
    this._inspected = new Set();
    this._compared = new Set();
    this._comparisons = []; // { a, b, agreement, shared_upstream } as revealed to the player
    this._corroborated = {}; // evidenceId -> outcome index
    this._reevaluated = {}; // evidenceId -> variantId (only where a reevaluation exists)
    this._reevalTried = new Set(); // "evidenceId|variantId"
    this._discovered = new Set([START_VARIANT]);
    this._links = {}; // evidenceId -> { hypothesisId -> relation }
    this._rejected = new Set();
    this._confidence = Object.fromEntries(caseData.hypotheses.map((h) => [h.id, null]));
    this._notes = {}; // evidenceId -> [revealed note]
    this._log = [];
    this._outcome = null;
  }

  get seed() {
    return this._seed;
  }

  get finished() {
    return this._outcome !== null;
  }

  get actionLog() {
    return this._log.map((a) => ({ ...a }));
  }

  /** Apply one action. Never throws on bad player input; state is untouched on failure. */
  dispatch(action) {
    try {
      if (this.finished) reject('GAME_OVER', 'The investigation is finished.');
      if (!action || typeof action !== 'object' || !isId(action.type)) {
        reject('MALFORMED_ACTION', 'An action must be an object with a type.');
      }
      if (!(action.type in COSTS)) reject('UNKNOWN_ACTION', `Unknown action "${action.type}".`);
      const cost = COSTS[action.type];
      if (cost > this._budget) {
        reject('INSUFFICIENT_BUDGET', `${action.type} costs ${cost}; ${this._budget} remaining.`);
      }
      const result = this[`_do_${action.type}`](action);
      this._budget -= cost;
      this._log.push({ ...action });
      return { ok: true, cost, result };
    } catch (e) {
      if (e instanceof Rejection) return { ok: false, error: { code: e.code, message: e.message } };
      throw e;
    }
  }

  _ev(id, field = 'evidence') {
    if (!isId(id)) reject('MALFORMED_ACTION', `${field} must be an id.`);
    const e = this._evidence.get(id);
    if (!e) reject('UNKNOWN_EVIDENCE', `No evidence "${id}".`);
    return e;
  }

  _inspectedEv(id, field) {
    const e = this._ev(id, field);
    if (!this._inspected.has(id)) reject('NOT_INSPECTED', `Inspect ${id} first.`);
    return e;
  }

  _hyp(id) {
    if (!isId(id)) reject('MALFORMED_ACTION', 'hypothesis must be an id.');
    const h = this._hyps.get(id);
    if (!h) reject('UNKNOWN_HYPOTHESIS', `No hypothesis "${id}".`);
    return h;
  }

  _liveHyp(id) {
    const h = this._hyp(id);
    if (this._rejected.has(id)) reject('HYPOTHESIS_REJECTED', `${id} is rejected.`);
    return h;
  }

  _note(id, note) {
    (this._notes[id] = this._notes[id] || []).push(note);
  }

  _do_inspect({ evidence }) {
    const e = this._ev(evidence);
    if (this._inspected.has(e.id)) reject('ALREADY_INSPECTED', `${e.id} is already inspected.`);
    this._inspected.add(e.id);
    for (const v of e.unlocks_reevaluation || []) this._discovered.add(v);
    return this._evidenceView(e);
  }

  _do_compare({ a, b }) {
    const ea = this._inspectedEv(a, 'a');
    const eb = this._inspectedEv(b, 'b');
    if (ea.id === eb.id) reject('SAME_EVIDENCE', 'Compare two different items.');
    const key = [ea.id, eb.id].sort().join('|');
    if (this._compared.has(key)) reject('ALREADY_COMPARED', `${ea.id} and ${eb.id} are already compared.`);
    this._compared.add(key);
    const fa = favoredSet(ea);
    const fb = favoredSet(eb);
    let agreement = 'unclear';
    if (fa.size && fb.size) agreement = [...fa].some((h) => fb.has(h)) ? 'agree' : 'conflict';
    const result = { a: ea.id, b: eb.id, shared_upstream: sharesUpstream(ea, eb), agreement };
    this._comparisons.push(result);
    this._note(ea.id, { kind: 'compare', with: eb.id, ...result });
    this._note(eb.id, { kind: 'compare', with: ea.id, ...result });
    return result;
  }

  _do_corroborate({ evidence }) {
    const e = this._inspectedEv(evidence);
    if (e.id in this._corroborated) reject('ALREADY_CORROBORATED', `${e.id} is already corroborated.`);
    if (!e.corroboration) {
      // Asking costs budget and returns nothing, so a dead end is not revealed for free.
      this._corroborated[e.id] = -1;
      const note = 'No independent source could be found to check this item.';
      this._note(e.id, { kind: 'corroboration', result_kind: 'unresolved', note });
      return { evidence: e.id, kind: 'unresolved', note };
    }
    const r = draw(this._case.case_id, this._seed, this._log.length);
    let acc = 0;
    let idx = e.corroboration.outcomes.length - 1;
    for (let i = 0; i < e.corroboration.outcomes.length; i++) {
      acc += e.corroboration.outcomes[i].weight;
      if (r < acc) {
        idx = i;
        break;
      }
    }
    const o = e.corroboration.outcomes[idx];
    this._corroborated[e.id] = idx;
    const result = { evidence: e.id, kind: o.kind, note: o.note };
    this._note(e.id, { kind: 'corroboration', result_kind: o.kind, note: o.note });
    return result;
  }

  _do_reevaluate({ evidence, variant }) {
    const e = this._inspectedEv(evidence);
    if (!isId(variant)) reject('MALFORMED_ACTION', 'variant must be an id.');
    if (!this._variants.has(variant)) reject('UNKNOWN_VARIANT', `No variant "${variant}".`);
    if (!this._discovered.has(variant)) {
      reject('VARIANT_NOT_DISCOVERED', `Conditions "${variant}" are not yet known. Find a source that describes them.`);
    }
    const tried = `${e.id}|${variant}`;
    if (this._reevalTried.has(tried)) reject('ALREADY_REEVALUATED', `${e.id} is already re-evaluated under ${variant}.`);
    this._reevalTried.add(tried);
    const re = (e.reevaluations || {})[variant];
    if (re) this._reevaluated[e.id] = variant;
    const note = re ? re.note : 'These conditions do not change how this reading should be interpreted.';
    this._note(e.id, { kind: 'reevaluation', variant, changed: Boolean(re), note });
    return { evidence: e.id, variant, changed: Boolean(re), note };
  }

  _do_link({ evidence, hypothesis, relation }) {
    const e = this._inspectedEv(evidence);
    const h = this._liveHyp(hypothesis);
    if (!RELATIONS.includes(relation)) reject('BAD_RELATION', `relation must be one of ${RELATIONS.join(', ')}.`);
    (this._links[e.id] = this._links[e.id] || {})[h.id] = relation;
    return { evidence: e.id, hypothesis: h.id, relation };
  }

  _do_unlink({ evidence, hypothesis }) {
    const e = this._ev(evidence);
    const h = this._hyp(hypothesis);
    if (!(this._links[e.id] && this._links[e.id][h.id])) reject('NO_SUCH_LINK', `${e.id} is not linked to ${h.id}.`);
    delete this._links[e.id][h.id];
    return { evidence: e.id, hypothesis: h.id };
  }

  _do_reject({ hypothesis }) {
    const h = this._liveHyp(hypothesis);
    if (this._hyps.size - this._rejected.size <= 1) reject('LAST_HYPOTHESIS', 'At least one hypothesis must remain.');
    this._rejected.add(h.id);
    return { hypothesis: h.id };
  }

  _do_restore({ hypothesis }) {
    const h = this._hyp(hypothesis);
    if (!this._rejected.has(h.id)) reject('NOT_REJECTED', `${h.id} is not rejected.`);
    this._rejected.delete(h.id);
    return { hypothesis: h.id };
  }

  _do_set_confidence({ hypothesis, value }) {
    const h = this._liveHyp(hypothesis);
    if (typeof value !== 'number' || !(value >= 0 && value <= 1)) reject('BAD_CONFIDENCE', 'value must be a number in [0, 1].');
    this._confidence[h.id] = value;
    return { hypothesis: h.id, value };
  }

  _do_commit({ hypothesis, confidence, citations }) {
    const h = this._liveHyp(hypothesis);
    if (typeof confidence !== 'number' || !(confidence >= 0 && confidence <= 1)) {
      reject('BAD_CONFIDENCE', 'confidence must be a number in [0, 1].');
    }
    if (!Array.isArray(citations) || citations.length === 0) {
      reject('EMPTY_CITATIONS', 'Cite at least one inspected evidence item.');
    }
    for (const id of citations) {
      this._ev(id, 'citation');
      if (!this._inspected.has(id)) reject('UNINSPECTED_CITATION', `Cannot cite ${id}: it has not been inspected.`);
    }
    const unique = [...new Set(citations)].sort();
    const state = this._posteriorState();
    const posterior = referencePosterior(this._case, state);
    const score = scoreFinding(
      this._case,
      { hypothesis: h.id, confidence, citations: unique, budgetRemaining: this._budget },
      state,
      posterior,
    );
    this._outcome = {
      committed_hypothesis: h.id,
      stated_confidence: confidence,
      citations: unique,
      is_correct: Boolean(h.is_truth),
      reference_posterior: posterior,
      player_confidence: { ...this._confidence },
      score,
      budget_remaining: this._budget,
      debrief: this._case.debrief,
    };
    return this._outcome;
  }

  _posteriorState() {
    return {
      inspected: [...this._inspected],
      reevaluated: { ...this._reevaluated },
      corroborated: { ...this._corroborated },
    };
  }

  _evidenceView(e) {
    const base = { id: e.id, type: e.type, title: e.title, inspected: this._inspected.has(e.id) };
    if (!base.inspected) return base;
    const s = this._sources.get(e.source);
    return {
      ...base,
      time: e.time,
      conditions: this._variants.get(e.variant).label,
      reading: e.reading,
      limits: e.limits,
      source: { label: s.label, reliability: s.reliability, limits: s.limits },
      notes: (this._notes[e.id] || []).map((n) => ({ ...n })),
      shares_upstream_with: this._sharedWith(e.id),
      independent_agreement: this._independentAgreement(e.id),
    };
  }

  /** Items this one is known to share an upstream source with, from the player's own comparisons. */
  _sharedWith(id) {
    return this._comparisons
      .filter((c) => c.shared_upstream && (c.a === id || c.b === id))
      .map((c) => (c.a === id ? c.b : c.a))
      .sort();
  }

  /** Compared items that agree with this one and are independent of it. Counts only what the player has learned. */
  _independentAgreement(id) {
    return this._comparisons.filter((c) => (c.a === id || c.b === id) && c.agreement === 'agree' && !c.shared_upstream).length;
  }

  /** Everything the player may see. Never includes likelihoods, truth, or unrevealed outcomes. */
  view() {
    const c = this._case;
    return {
      case_id: c.case_id,
      title: c.title,
      briefing: c.briefing,
      budget: c.budget,
      budget_remaining: this._budget,
      finished: this.finished,
      conditions_known: c.variants.filter((v) => this._discovered.has(v.variant_id)).map((v) => ({ ...v })),
      hypotheses: c.hypotheses.map((h) => ({
        id: h.id,
        claim: h.claim,
        rejected: this._rejected.has(h.id),
        confidence: this._confidence[h.id],
      })),
      evidence: c.evidence.map((e) => this._evidenceView(e)),
      links: JSON.parse(JSON.stringify(this._links)),
      known_dependencies: this._comparisons.filter((c) => c.shared_upstream).map((c) => [c.a, c.b].sort()),
      outcome: this._outcome ? JSON.parse(JSON.stringify(this._outcome)) : null,
    };
  }
}

function createGame(caseData, opts) {
  return new Game(caseData, opts);
}

/** Rebuild a game from a case, seed, and action log. Stops at the first rejected action. */
function replay(caseData, seed, actionLog) {
  const g = new Game(caseData, { seed });
  for (let i = 0; i < actionLog.length; i++) {
    const r = g.dispatch(actionLog[i]);
    if (!r.ok) return { game: g, error: { index: i, ...r.error } };
  }
  return { game: g, error: null };
}

module.exports = { createGame, replay, COSTS };
