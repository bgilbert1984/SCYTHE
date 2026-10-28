/**
 * store.js — Prototype persistence for the player Hub.
 *
 * Stores one in-progress investigation (case, seed, action log) and a career
 * history. The action log is the whole save: state is rebuilt by replay
 * (design §7). The final save file format is #361; this is deliberately small.
 *
 * Takes any Storage-like object. If it is missing or throws (private windows,
 * blocked site data), it falls back to memory and reports persistent = false.
 */

const STORE_VERSION = 1;
const SAVE_KEY = 'scythe_game_save';
const CAREER_KEY = 'scythe_game_career';
const CONTRACT_VERSION = 'game_case_0.1.0';

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

    saveInProgress({ case_id, seed, actions }) {
      write(SAVE_KEY, JSON.stringify({
        store_version: STORE_VERSION,
        contract_version: CONTRACT_VERSION,
        case_id,
        seed,
        actions,
      }));
    },

    /** Returns { save } or { error } or {} when there is nothing saved. */
    loadInProgress(expectedCaseId) {
      const raw = read(SAVE_KEY);
      if (raw === null) return {};
      const s = parse(raw);
      if (!s || typeof s !== 'object' || !Array.isArray(s.actions) || !Number.isFinite(s.seed)) {
        return { error: 'The saved investigation is damaged.' };
      }
      if (s.store_version !== STORE_VERSION || s.contract_version !== CONTRACT_VERSION) {
        return { error: 'The saved investigation was made with a different version of the game.' };
      }
      if (s.case_id !== expectedCaseId) return { error: 'The saved investigation is for a different case.' };
      return { save: s };
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

module.exports = { createStore, STORE_VERSION, SAVE_KEY, CAREER_KEY };
