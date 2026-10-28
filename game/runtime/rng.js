/**
 * rng.js — Seeded randomness (design §5.3).
 *
 * Every random draw is derived from (case_id, save_seed, action_index), so the
 * same case, seed, and action log always reproduce the same outcomes.
 */

function fnv1a(str) {
  let h = 0x811c9dc5;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h >>> 0;
}

/** Deterministic float in [0, 1) for one draw. */
function draw(caseId, seed, actionIndex) {
  let a = fnv1a(`${caseId}|${seed}|${actionIndex}`);
  a = (a + 0x6d2b79f5) >>> 0;
  let t = Math.imul(a ^ (a >>> 15), 1 | a);
  t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
}

module.exports = { draw };
