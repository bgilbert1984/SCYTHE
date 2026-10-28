/**
 * store.js — Persistence for the player Hub.
 *
 * Keeps one in-progress investigation as a versioned save file (runtime/save.js:
 * case identity, seed, action log, checksum) and a career history. State is
 * rebuilt by replay (design §7).
 *
 * Takes any Storage-like object. If it is missing or throws (private windows,
 * blocked site data), it falls back to memory and reports persistent = false.
 */

const { replay } = require('../../runtime/engine');
const { createSave, serialize, load } = require('../../runtime/save');

const LEGACY_STORE_VERSION = 1;
const SAVE_KEY = 'scythe_game_save';
const CAREER_KEY = 'scythe_game_career';

function createStore(storage) {
  const memory = new Map();
  let persistent = Boolean(storage);

  const read = (k) => {
    if (persistent) {
      try {
        return storage.getItem(k);
      } catch (e) {
        persistent = false;
      }
    }
    return memory.has(k) ? memory.get(k) : null;
  };
  const write = (k, v) => {
    memory.set(k, v);
    if (persistent) {
      try {
        storage.setItem(k, v);
      } catch (e) {
        persistent = false;
      }
    }
  };
  const remove = (k) => {
    memory.delete(k);
    if (persistent) {
      try {
        storage.removeItem(k);
      } catch (e) {
        persistent = false;
      }
    }
  };
  const parse = (raw) => {
    try {
      return JSON.parse(raw);
    } catch (e) {
      return undefined;
    }
  };

  return {
    get persistent() {
      return persistent;
    },

    saveInProgress(game, caseData) {
      write(SAVE_KEY, serialize(createSave(game, caseData)));
    },

    /**
     * Rebuild the saved investigation. Returns { game, save }, { error: { code, message } },
     * or {} when nothing is saved.
     */
    loadInProgress(caseData) {
      const raw = read(SAVE_KEY);
      if (raw === null) return {};
      const legacy = parse(raw);
      if (legacy && typeof legacy === 'object' && legacy.format === undefined && legacy.store_version === LEGACY_STORE_VERSION) {
        return upgradeLegacy(caseData, legacy);
      }
      return load(caseData, raw);
    },

    hasInProgress() {
      return read(SAVE_KEY) !== null;
    },

    clearInProgress() {
      remove(SAVE_KEY);
    },

    recordResult(entry) {
      const list = this.career();
      list.push(entry);
      write(CAREER_KEY, JSON.stringify(list));
    },

    career() {
      const raw = read(CAREER_KEY);
      if (raw === null) return [];
      const list = parse(raw);
      return Array.isArray(list) ? list : [];
    },
  };
}

/** Saves written by the first Hub (before the save file format) had no checksum or case hash. */
function upgradeLegacy(caseData, old) {
  const fail = (code, message) => ({ error: { code, message } });
  if (old.contract_version !== caseData.contract_version) {
    return fail('CONTRACT_MISMATCH', `This save uses case format ${old.contract_version}, but this game uses ${caseData.contract_version}.`);
  }
  if (old.case_id !== caseData.case_id) return fail('WRONG_CASE', `This save is for the case "${old.case_id}", not "${caseData.case_id}".`);
  if (!Array.isArray(old.actions) || !Number.isFinite(old.seed)) return fail('MALFORMED', 'The saved investigation is damaged.');
  const { game, error } = replay(caseData, old.seed, old.actions);
  if (error) return fail('REPLAY_FAILED', `Action ${error.index + 1} could not be replayed (${error.code}): ${error.message}`);
  return { game, save: createSave(game, caseData) };
}

module.exports = { createStore, SAVE_KEY, CAREER_KEY };
