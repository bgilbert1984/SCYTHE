/**
 * validate.js — Structural and referential checks for a game case file.
 *
 * Contract: game_case_0.1.0 (see game/GAME_DESIGN.md §5). Pure, no I/O.
 */

const CONTRACT_VERSION = 'game_case_0.1.0';
const EVIDENCE_TYPES = ['sdr', 'log', 'device', 'witness'];
const RELIABILITY = ['weak', 'moderate', 'strong'];
const OUTCOME_KINDS = ['result', 'no_match', 'unresolved'];

function isSortedById(items) {
  for (let i = 1; i < items.length; i++) {
    if (!(items[i - 1].id < items[i].id)) return false;
  }
  return true;
}

function validateCase(c) {
  const errors = [];
  const err = (m) => errors.push(m);

  if (c.contract_version !== CONTRACT_VERSION) err(`contract_version must be ${CONTRACT_VERSION}`);
  for (const k of ['case_id', 'env_id', 'title', 'briefing']) {
    if (typeof c[k] !== 'string' || !c[k]) err(`${k} must be a non-empty string`);
  }
  if (!Number.isInteger(c.budget) || c.budget < 1) err('budget must be a positive integer');

  const hyps = c.hypotheses || [];
  const hypIds = new Set(hyps.map((h) => h.id));
  if (hyps.length !== 3) err('exactly 3 hypotheses required');
  if (hyps.filter((h) => h.is_truth).length !== 1) err('exactly one hypothesis must be the truth');

  const prior = c.prior || {};
  let priorSum = 0;
  for (const id of hypIds) {
    if (typeof prior[id] !== 'number') err(`prior missing for ${id}`);
    else priorSum += prior[id];
  }
  if (Math.abs(priorSum - 1) > 1e-9) err(`prior must sum to 1 (got ${priorSum})`);

  const variants = c.variants || [];
  const variantIds = new Set(variants.map((v) => v.variant_id));
  if (variants.length !== 3) err('exactly 3 variants required');

  const sources = c.sources || [];
  const sourceIds = new Set(sources.map((s) => s.id));
  for (const s of sources) {
    if (!RELIABILITY.includes(s.reliability)) err(`source ${s.id}: bad reliability`);
  }

  const evidence = c.evidence || [];
  const evIds = new Set(evidence.map((e) => e.id));
  if (evidence.length < 12 || evidence.length > 16) err(`evidence count must be 12-16 (got ${evidence.length})`);
  const typesSeen = new Set(evidence.map((e) => e.type));
  for (const t of EVIDENCE_TYPES) if (!typesSeen.has(t)) err(`no evidence of type ${t}`);

  const checkLikelihood = (where, lk) => {
    if (!lk) return err(`${where}: missing likelihood`);
    for (const id of hypIds) {
      const v = lk[id];
      if (typeof v !== 'number' || v <= 0 || v >= 1) err(`${where}: likelihood ${id} must be in (0,1)`);
    }
    for (const k of Object.keys(lk)) if (!hypIds.has(k)) err(`${where}: unknown hypothesis ${k}`);
  };

  for (const e of evidence) {
    const w = `evidence ${e.id}`;
    if (!EVIDENCE_TYPES.includes(e.type)) err(`${w}: bad type`);
    if (!sourceIds.has(e.source)) err(`${w}: unknown source ${e.source}`);
    if (!variantIds.has(e.variant)) err(`${w}: unknown variant ${e.variant}`);
    for (const k of ['title', 'reading', 'limits']) if (!e[k]) err(`${w}: missing ${k}`);
    checkLikelihood(w, e.likelihood);
    for (const d of e.derived_from || []) {
      if (!evIds.has(d)) err(`${w}: derived_from unknown ${d}`);
      if (d === e.id) err(`${w}: derived_from itself`);
    }
    for (const v of e.unlocks_reevaluation || []) {
      if (!variantIds.has(v)) err(`${w}: unlocks unknown variant ${v}`);
    }
    for (const [v, r] of Object.entries(e.reevaluations || {})) {
      if (!variantIds.has(v)) err(`${w}: reevaluation for unknown variant ${v}`);
      if (!r.note) err(`${w}: reevaluation ${v} missing note`);
      checkLikelihood(`${w} reevaluation ${v}`, r.likelihood);
    }
    if (e.corroboration) {
      const outs = e.corroboration.outcomes || [];
      if (!outs.length) err(`${w}: corroboration has no outcomes`);
      const sum = outs.reduce((a, o) => a + o.weight, 0);
      if (Math.abs(sum - 1) > 1e-9) err(`${w}: corroboration weights must sum to 1 (got ${sum})`);
      for (const o of outs) {
        if (!OUTCOME_KINDS.includes(o.kind)) err(`${w}: bad outcome kind ${o.kind}`);
        if (!o.note) err(`${w}: outcome missing note`);
        if (o.kind === 'result') checkLikelihood(`${w} outcome`, o.likelihood);
        if (o.kind === 'no_match' && o.likelihood) checkLikelihood(`${w} outcome`, o.likelihood);
        if (o.kind === 'unresolved' && o.likelihood) err(`${w}: an unresolved outcome must not carry a likelihood`);
      }
    }
  }

  const dps = c.decision_points || [];
  if (dps.length < 6 || dps.length > 10) err(`decision_points must be 6-10 (got ${dps.length})`);

  const d = c.debrief || {};
  for (const k of ['truth', 'pivotal_decision', 'pivotal_explanation']) if (!d[k]) err(`debrief.${k} missing`);
  if (d.pivotal_decision && !dps.some((p) => p.id === d.pivotal_decision)) err('debrief.pivotal_decision is not a decision point');
  for (const id of d.key_evidence || []) if (!evIds.has(id)) err(`debrief.key_evidence unknown ${id}`);
  for (const m of d.misleading_clues || []) {
    if (!m.why) err('debrief misleading clue missing why');
    for (const id of m.evidence || []) if (!evIds.has(id)) err(`debrief misleading clue unknown evidence ${id}`);
  }
  if (!(d.misleading_clues || []).length) err('debrief needs at least one misleading clue');

  for (const [name, list] of [['hypotheses', hyps], ['sources', sources], ['evidence', evidence], ['decision_points', dps]]) {
    if (!isSortedById(list)) err(`${name} must be sorted by id (determinism)`);
  }
  if (!isSortedById(variants.map((v) => ({ id: v.variant_id })))) err('variants must be sorted by variant_id');

  return errors;
}

module.exports = { validateCase, CONTRACT_VERSION };
