/**
 * save.js — Versioned save files and verified replay (design §5.3, §7).
 *
 * A save is the case identity, the seed, and the ordered action log. State is never
 * stored; it is rebuilt by replay. A finished save also records its result, so a
 * load can prove the replay reproduces the same score.
 *
 * Pure: it parses and produces strings. Reading and writing files is the caller's job.
 */

const { replay } = require('./engine');
const { sha256Hex } = require('./hash');

const FORMAT = 'scythe-game-save';
const VERSION = 1;

/** Deterministic JSON: object keys sorted recursively, undefined dropped. */
function canonical(v) {
  if (Array.isArray(v)) return `[${v.map(canonical).join(',')}]`;
  if (v && typeof v === 'object') {
    const keys = Object.keys(v).filter((k) => v[k] !== undefined).sort();
    return `{${keys.map((k) => `${JSON.stringify(k)}:${canonical(v[k])}`).join(',')}}`;
  }
  return JSON.stringify(v);
}

function sortKeys(v) {
  if (Array.isArray(v)) return v.map(sortKeys);
  if (v && typeof v === 'object') {
    return Object.fromEntries(Object.keys(v).filter((k) => v[k] !== undefined).sort().map((k) => [k, sortKeys(v[k])]));
  }
  return v;
}

const caseHash = (caseData) => sha256Hex(canonical(caseData));
const checksumOf = (save) => {
  const { checksum, ...rest } = save;
  return sha256Hex(canonical(rest));
};

function resultOf(game) {
  const o = game.view().outcome;
  return {
    hypothesis: o.committed_hypothesis,
    confidence: o.stated_confidence,
    citations: o.citations,
    score: o.score.total,
    outcome: o.score.outcome,
    is_correct: o.is_correct,
  };
}

/** Build a save object from a game. Finished games also record their result. */
function createSave(game, caseData) {
  const save = {
    format: FORMAT,
    version: VERSION,
    contract_version: caseData.contract_version,
    case_id: caseData.case_id,
    case_hash: caseHash(caseData),
    seed: game.seed,
    actions: game.actionLog,
  };
  if (game.finished) save.result = resultOf(game);
  save.checksum = checksumOf(save);
  return save;
}

function serialize(save) {
  return JSON.stringify(sortKeys(save), null, 2);
}

const fail = (code, message) => ({ error: { code, message } });

/** Structure, version, and integrity only. It does not know the case. */
function parse(text) {
  let s;
  try {
    s = JSON.parse(text);
  } catch (e) {
    return fail('MALFORMED', 'This is not a valid save file (it is not readable JSON).');
  }
  if (!s || typeof s !== 'object' || s.format !== FORMAT) return fail('NOT_A_SAVE', 'This file is not a SCYTHE: GAME save.');
  if (!Number.isInteger(s.version)) return fail('MALFORMED', 'The save file has no valid version.');
  if (s.version > VERSION) return fail('UNSUPPORTED_VERSION', 'This save was made by a newer version of the game. Update the game to open it.');
  if (s.version < VERSION) return fail('UNSUPPORTED_VERSION', 'This save was made by an older version that can no longer be opened.');
  const ok = typeof s.case_id === 'string' && typeof s.contract_version === 'string' && typeof s.case_hash === 'string'
    && typeof s.checksum === 'string' && Number.isFinite(s.seed)
    && Array.isArray(s.actions) && s.actions.every((a) => a && typeof a === 'object' && !Array.isArray(a));
  if (!ok) return fail('MALFORMED', 'The save file is missing required fields.');
  if (checksumOf(s) !== s.checksum) return fail('CORRUPT', 'The save file is damaged or has been edited.');
  return { save: s };
}

/** Parse, check it belongs to this case, replay it, and verify any recorded result. */
function load(caseData, text) {
  const p = parse(text);
  if (p.error) return p;
  const s = p.save;
  if (s.contract_version !== caseData.contract_version) {
    return fail('CONTRACT_MISMATCH', `This save uses case format ${s.contract_version}, but this game uses ${caseData.contract_version}.`);
  }
  if (s.case_id !== caseData.case_id) return fail('WRONG_CASE', `This save is for the case "${s.case_id}", not "${caseData.case_id}".`);
  if (s.case_hash !== caseHash(caseData)) {
    return fail('CASE_CHANGED', 'The case has changed since this save was made, so it cannot be replayed faithfully.');
  }
  const { game, error } = replay(caseData, s.seed, s.actions);
  if (error) {
    return fail('REPLAY_FAILED', `Action ${error.index + 1} could not be replayed (${error.code}): ${error.message}`);
  }
  if (s.result) {
    if (!game.finished) return fail('RESULT_MISMATCH', 'The save records a result, but its actions do not finish the investigation.');
    if (canonical(resultOf(game)) !== canonical(s.result)) {
      return fail('RESULT_MISMATCH', 'Replaying this save does not reproduce its recorded result.');
    }
  }
  return { game, save: s, verified: Boolean(s.result) };
}

module.exports = { createSave, serialize, parse, load, caseHash, canonical, FORMAT, VERSION };
