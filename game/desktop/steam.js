/**
 * steam.js — Steamworks provider for the platform service (platform/service.js).
 *
 * Runs in the main process only. steamworks.js is loaded lazily and optionally: if it
 * is not installed, Steam is not running, or the app was not launched through Steam,
 * the game falls back to the no-op provider and plays exactly as it does offline.
 */

const CLOUD_FILE = 'signal_ghost_in_progress.json';

/** Wrap a steamworks.js client in the provider contract. `ids` are the game's achievement ids. */
function providerFor(client, ids) {
  const cloudOn = () => client.cloud.isEnabledForApp() && client.cloud.isEnabledForAccount();
  return {
    name: 'steam',
    available: true,
    async unlock(id) {
      client.achievement.activate(id);
    },
    async unlocked() {
      return ids.filter((id) => client.achievement.isActivated(id));
    },
    async cloudRead() {
      return cloudOn() && client.cloud.fileExists(CLOUD_FILE) ? client.cloud.readFile(CLOUD_FILE) : null;
    },
    async cloudWrite(text) {
      if (cloudOn() && !client.cloud.writeFile(CLOUD_FILE, text)) throw new Error('Steam Cloud write failed');
    },
    async cloudClear() {
      if (cloudOn() && client.cloud.fileExists(CLOUD_FILE)) client.cloud.deleteFile(CLOUD_FILE);
    },
  };
}

/**
 * @returns { provider } connected to Steam, or { provider: null, reason } when unavailable.
 * `load` is injectable so tests need no native module.
 */
function connect(appId, ids, load = () => require('steamworks.js')) {
  if (!Number.isInteger(appId) || appId <= 0) return { provider: null, reason: 'no Steam app id configured' };
  let sw;
  try {
    sw = load();
  } catch (e) {
    return { provider: null, reason: 'steamworks.js is not installed' };
  }
  try {
    return { provider: providerFor(sw.init(appId), ids) };
  } catch (e) {
    return { provider: null, reason: 'Steam is not running or this app was not launched through Steam' };
  }
}

module.exports = { connect, providerFor, CLOUD_FILE };
