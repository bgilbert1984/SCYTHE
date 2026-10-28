/**
 * posterior.js — Reference posterior p* for an authored case (design §3.4).
 *
 * Pure and deterministic. The player never sees this until the debrief.
 *
 * state = {
 *   inspected:    ids of inspected evidence,
 *   reevaluated:  { [evidenceId]: variantId },
 *   corroborated: { [evidenceId]: outcomeIndex }
 * }
 */

/**
 * Evidence that counts toward p*. A derived item (summary of other evidence)
 * counts only if none of its sources are already counted, and only one derived
 * item per shared source counts. Resolved by id order so the result does not
 * depend on inspection order.
 */
function countedEvidence(c, inspected) {
  const byId = new Map(c.evidence.map((e) => [e.id, e]));
  const ids = [...new Set(inspected)].filter((id) => byId.has(id)).sort();
  const primaries = ids.filter((id) => !(byId.get(id).derived_from || []).length);
  const counted = new Set(primaries);
  const claimed = new Set();
  for (const id of ids) {
    const parents = byId.get(id).derived_from || [];
    if (!parents.length) continue;
    if (parents.some((p) => counted.has(p) || claimed.has(p))) continue;
    counted.add(id);
    parents.forEach((p) => claimed.add(p));
  }
  return [...counted].sort();
}

function effectiveLikelihood(e, state) {
  const variant = (state.reevaluated || {})[e.id];
  if (variant && e.reevaluations && e.reevaluations[variant]) return e.reevaluations[variant].likelihood;
  const idx = (state.corroborated || {})[e.id];
  if (idx !== undefined && e.corroboration) {
    const o = e.corroboration.outcomes[idx];
    // A result always informs. A no-match informs only if authored as weak evidence;
    // an unresolved outcome (the check could not be made) never does (cf. #214).
    if (o && (o.kind === 'result' || (o.kind === 'no_match' && o.likelihood))) return o.likelihood;
  }
  return e.likelihood;
}

function referencePosterior(c, state) {
  const byId = new Map(c.evidence.map((e) => [e.id, e]));
  const post = {};
  for (const h of c.hypotheses) post[h.id] = c.prior[h.id];
  for (const id of countedEvidence(c, state.inspected || [])) {
    const lk = effectiveLikelihood(byId.get(id), state);
    for (const h of c.hypotheses) post[h.id] *= lk[h.id];
  }
  const total = c.hypotheses.reduce((a, h) => a + post[h.id], 0);
  for (const h of c.hypotheses) post[h.id] /= total;
  return post;
}

module.exports = { referencePosterior, countedEvidence, effectiveLikelihood };
