/**
 * service.js — Storefront-agnostic platform services: achievements and cloud saves.
 *
 * Pure: a provider (Steam, or none) is injected and does the I/O. The service
 * verifies every save by replay before it trusts it, so achievements and cloud copies
 * come from legitimate play of this case, not from whatever the page sends.
 *
 * Provider contract (all methods may be async):
 *   name                      string
 *   unlock(id)                grant an achievement; granting twice is harmless
 *   unlocked()                ids already granted
 *   cloudRead()               text of the cloud save, or null
 *   cloudWrite(text)          store the cloud save
 *   cloudClear()              remove the cloud save
 */

const { load } = require('../runtime/save');
const { evaluate, DEFINITIONS } = require('./achievements');
const { decide } = require('./sync');

/** The provider used when no storefront is present: everything succeeds and nothing happens. */
const NO_PROVIDER = {
  name: 'none',
  available: false,
  unlock: async () => {},
  unlocked: async () => [],
  cloudRead: async () => null,
  cloudWrite: async () => {},
  cloudClear: async () => {},
};

function createPlatform(provider, caseData) {
  const p = provider || NO_PROVIDER;
  const verified = (text) => (typeof text === 'string' && text.length <= 1048576 ? load(caseData, text) : { error: { code: 'MALFORMED' } });

  // Provider failures must never break play: report them, do not throw.
  const safe = async (fn, fallback) => {
    try {
      return await fn();
    } catch (e) {
      return fallback;
    }
  };

  return {
    get name() {
      return p.name;
    },
    get available() {
      return p.available !== false;
    },

    /** A save was written locally. Grants any newly earned achievements and mirrors it to the cloud. */
    async onSave(text) {
      const r = verified(text);
      if (r.error) return { ok: false, error: r.error.code, unlocked: [] };
      const earned = evaluate(r.game);
      const have = new Set(await safe(() => p.unlocked(), []));
      const fresh = earned.filter((id) => !have.has(id));
      for (const id of fresh) await safe(() => p.unlock(id));
      // A finished investigation leaves the cloud slot; only the one in progress is synced.
      if (r.game.finished) await safe(() => p.cloudClear());
      else await safe(() => p.cloudWrite(text));
      const names = new Map(DEFINITIONS.map((d) => [d.id, d.name]));
      return { ok: true, unlocked: fresh.map((id) => ({ id, name: names.get(id) })) };
    },

    /**
     * At start-up: compare this device's save with the cloud's.
     * Returns { action: 'none'|'push'|'pull'|'conflict' }. 'pull' carries `text`, the cloud save to adopt.
     * 'conflict' carries both summaries and both texts: the caller asks the player, then either adopts
     * the cloud text or sends the local text back through onSave, which overwrites the cloud copy.
     */
    async onStart(localText) {
      const local = localText ? verified(localText) : null;
      const cloudText = await safe(() => p.cloudRead(), null);
      const cloud = cloudText ? verified(cloudText) : null;
      const l = local && !local.error ? local : null;
      const c = cloud && !cloud.error ? cloud : null;
      const d = decide(l, c);
      if (d.action === 'push') await safe(() => p.cloudWrite(localText));
      if (d.action === 'pull') return { action: 'pull', text: cloudText };
      if (d.action === 'conflict') return { ...d, localText, cloudText };
      return { action: d.action };
    },
  };
}

module.exports = { createPlatform, NO_PROVIDER };
