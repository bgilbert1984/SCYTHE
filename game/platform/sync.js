/**
 * sync.js — Deciding between a local and a cloud save (design §7).
 *
 * Pure. Both inputs are verified saves ({ save, game } from runtime/save.js load).
 * A save is just case + seed + action log, so two saves of the same game are comparable
 * exactly: one is the other plus later moves, or they have diverged. Divergence is never
 * resolved silently, because either side could be the one the player wants.
 */

const { canonical } = require('../runtime/save');

const sameActions = (a, b, n) => a.slice(0, n).every((x, i) => canonical(x) === canonical(b[i]));

const describe = (s) => ({ actions: s.actions.length, finished: Boolean(s.result) });

/**
 * @returns { action, ... } where action is one of
 *   'none'       nothing to do (neither exists, or identical)
 *   'push'       only the local save exists, or it is ahead: write it to the cloud
 *   'pull'       only the cloud save exists, or it is ahead: use it locally
 *   'conflict'   different games or diverged moves; carries `local` and `cloud` summaries
 */
function decide(local, cloud) {
  if (!local && !cloud) return { action: 'none' };
  if (!cloud) return { action: 'push' };
  if (!local) return { action: 'pull' };
  const l = local.save;
  const c = cloud.save;
  if (l.checksum === c.checksum) return { action: 'none' };
  const conflict = { action: 'conflict', local: describe(l), cloud: describe(c) };
  if (l.case_id !== c.case_id || l.seed !== c.seed) return conflict;
  const n = Math.min(l.actions.length, c.actions.length);
  if (!sameActions(l.actions, c.actions, n)) return conflict;
  if (l.actions.length === c.actions.length) return conflict; // same moves, different record: do not guess
  return { action: l.actions.length > c.actions.length ? 'push' : 'pull' };
}

module.exports = { decide };
