/**
 * smoke.js — Launch self-check, run only when SIGNAL_GHOST_SMOKE=1.
 *
 * Plays a few moves in the real page, checks the sandbox and offline guarantees from
 * inside the renderer, prints one JSON line, and exits. Used by scripts/smoke-run.js.
 */

const { app } = require('electron');

const CHECKS = `(async () => {
  const q = (s) => document.querySelector(s);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const out = { title: document.title, hasGame: typeof window.__game };
  out.continueEnabledAtStart = !q('[data-key=continue]').disabled;
  if (out.continueEnabledAtStart) {
    q('[data-key=continue]').click();
  } else {
    q('[data-key=new]').click();
  }
  await sleep(50);
  const before = q('[role=img]').getAttribute('aria-label');
  if (!out.continueEnabledAtStart) {
    q('[data-key=ev-E07]').click(); q('[data-key=inspect]').click();
    await sleep(50);
  }
  out.budgetBefore = before;
  out.budgetAfter = q('[role=img]').getAttribute('aria-label');
  out.restored = /restored/i.test(q('.msg').textContent);
  out.nodeLeaked = [typeof require, typeof process, typeof module].filter((t) => t !== 'undefined');
  try { out.fetchBlocked = await fetch('https://example.com/').then(() => false, () => true); } catch (e) { out.fetchBlocked = true; }
  try { out.xhrBlocked = await new Promise((res) => { const x = new XMLHttpRequest(); x.onerror = () => res(true); x.onload = () => res(false); x.open('GET', 'https://example.com/'); x.send(); }); } catch (e) { out.xhrBlocked = true; }
  out.popupBlocked = window.open('https://example.com/') === null;
  const url = location.href;
  location.assign('https://example.com/');
  await sleep(100);
  out.navigationBlocked = location.href === url;
  out.storage = (() => { try { localStorage.setItem('probe', '1'); return localStorage.getItem('probe') === '1'; } catch (e) { return false; } })();
  return out;
})()`;

async function run(win, ses) {
  const external = [];
  ses.webRequest.onBeforeRequest((details, callback) => {
    const allowed = /^(file|data|blob|devtools):/.test(details.url);
    if (!allowed) external.push(details.url);
    callback({ cancel: !allowed });
  });
  const report = await win.webContents.executeJavaScript(CHECKS);
  report.externalRequestsAttempted = external.length;
  report.electron = process.versions.electron;
  report.packaged = app.isPackaged;
  process.stdout.write(`SMOKE ${JSON.stringify(report)}\n`);
  app.quit();
}

module.exports = { run };
