#!/usr/bin/env node
/**
 * smoke-run.js — Launch a packaged build and verify it, twice, on one profile.
 *
 *   node scripts/smoke-run.js [path/to/signal-ghost[.exe]] [--offline] [--display]
 *
 * Run 1 starts an investigation and spends an action point. Run 2 reopens the same
 * profile and must offer Continue and restore it. Both runs check the sandbox and that
 * network, popups, and navigation are blocked. With --offline the app runs in a network
 * namespace with no interfaces (Linux: unshare -Un), so it could not reach the network
 * even if it tried. On Windows there is no such switch; block the executable in the firewall instead.
 *
 * Headless flags are for Linux machines with no display; pass --display to use a real one. Windows always
 * uses the real desktop session.
 */

const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const args = process.argv.slice(2);
const offline = args.includes('--offline');
const display = args.includes('--display') || process.platform !== 'linux';
const bin = path.resolve(args.find((a) => !a.startsWith('--')) || path.join(__dirname, '..', 'out', 'signal-ghost-linux-x64', 'signal-ghost'));

if (offline && process.platform !== 'linux') {
  process.stderr.write('--offline needs Linux network namespaces; on Windows block the executable in the firewall.\n');
  process.exit(2);
}
if (!fs.existsSync(bin)) {
  process.stderr.write(`not found: ${bin}\n`);
  process.exit(2);
}

const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'signal-ghost-smoke-'));

function launch() {
  const reportFile = path.join(profile, `report-${Date.now()}.txt`);
  const flags = [`--user-data-dir=${profile}`, `--smoke-report=${reportFile}`, ...(display ? [] : ['--ozone-platform=headless'])];
  // Own uid inside the namespace: Chromium refuses to run its sandbox as root.
  const [cmd, argv] = offline
    ? ['unshare', ['-Un', `--map-user=${process.getuid()}`, `--map-group=${process.getgid()}`, bin, ...flags]]
    : [bin, flags];
  const r = spawnSync(cmd, argv, { env: { ...process.env, SIGNAL_GHOST_SMOKE: '1' }, encoding: 'utf8', timeout: 90000 });
  const text = fs.existsSync(reportFile) ? fs.readFileSync(reportFile, 'utf8') : r.stdout || '';
  const line = text.split('\n').find((l) => l.startsWith('SMOKE '));
  return { status: r.status, signal: r.signal, report: line ? JSON.parse(line.slice(6)) : null, stderr: r.stderr };
}

const failures = [];
const expect = (cond, msg) => { if (!cond) failures.push(msg); };
const runs = [launch(), launch()];

runs.forEach((r, i) => {
  const n = `run ${i + 1}`;
  expect(r.status === 0, `${n}: exit status ${r.status}${r.signal ? ` (${r.signal})` : ''}`);
  expect(r.report, `${n}: no SMOKE report${r.stderr ? ` (stderr: ${r.stderr.split('\n').filter(Boolean).slice(-2).join(' | ')})` : ''}`);
  if (!r.report) return;
  const p = r.report;
  expect(p.title === 'SIGNAL GHOST', `${n}: title was "${p.title}"`);
  expect(p.hasGame === 'object', `${n}: game bundle did not load`);
  expect(p.nodeLeaked.length === 0, `${n}: Node globals leaked into the page: ${p.nodeLeaked}`);
  expect(p.fetchBlocked && p.xhrBlocked, `${n}: network request not blocked`);
  expect(p.popupBlocked && p.navigationBlocked, `${n}: popup or navigation not blocked`);
  expect(p.storage, `${n}: localStorage unavailable`);
  // The page's CSP stops these before they reach the request layer, so none should get that far.
  expect(p.bridge === 'saved,start,status', `${n}: the platform bridge is ${JSON.stringify(p.bridge)}`);
  expect(p.platform && p.platform.name === 'none' && p.platform.available === false, `${n}: expected no storefront in the smoke run, got ${JSON.stringify(p.platform)}`);
  expect(p.externalRequestsAttempted === 0, `${n}: ${p.externalRequestsAttempted} external requests reached the network layer`);
});
if (runs[0].report && runs[1].report) {
  expect(runs[0].report.continueEnabledAtStart === false, 'run 1: a fresh profile should not offer Continue');
  expect(runs[0].report.budgetAfter === '11 of 12 action points left', `run 1: budget after inspect was "${runs[0].report.budgetAfter}"`);
  expect(runs[1].report.continueEnabledAtStart === true, 'run 2: Continue should be offered after a restart');
  expect(runs[1].report.restored && runs[1].report.budgetAfter === '11 of 12 action points left', 'run 2: the saved investigation was not restored');
}

fs.rmSync(profile, { recursive: true, force: true });
process.stdout.write(`${offline ? 'offline ' : ''}smoke: ${failures.length ? 'FAIL' : 'PASS'}\n`);
failures.forEach((f) => process.stdout.write(`  - ${f}\n`));
if (!failures.length) process.stdout.write(`  Electron ${runs[0].report.electron}, packaged=${runs[0].report.packaged}, saved game survived a restart\n`);
process.exit(failures.length ? 1 : 0);
