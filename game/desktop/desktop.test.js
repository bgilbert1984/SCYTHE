/**
 * Checks on the desktop shell that need no Electron binary and no Node 22:
 * what ships, what the shell is allowed to do, and what it refuses.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const ROOT = __dirname;
const read = (f) => fs.readFileSync(path.join(ROOT, f), 'utf8');
const pkg = JSON.parse(read('package.json'));
const main = read('main.js');
const smoke = read('smoke.js');

const requires = (src) => [...src.matchAll(/require\(\s*['"]([^'"]+)['"]\s*\)/g)].map((m) => m[1]);

test('electron and the packager are pinned to exact versions', () => {
  for (const name of ['electron', '@electron/packager']) {
    assert.match(pkg.devDependencies[name], /^\d+\.\d+\.\d+$/, `${name} must be an exact version`);
  }
});

test('the toolchain declares the Node version it needs', () => {
  assert.strictEqual(pkg.engines.node, '>=22.12.0');
});

test('a lockfile is committed so builds are reproducible', () => {
  assert.ok(fs.existsSync(path.join(ROOT, 'package-lock.json')));
});

test('the shell only loads the built Hub, sandboxed, with no Node access', () => {
  assert.match(main, /contextIsolation:\s*true/);
  assert.match(main, /sandbox:\s*true/);
  assert.match(main, /nodeIntegration:\s*false/);
  assert.match(main, /webSecurity:\s*true/);
  assert.ok(!/nodeIntegration:\s*true|sandbox:\s*false|contextIsolation:\s*false|webSecurity:\s*false/.test(main));
  assert.ok(!/--no-sandbox|no-sandbox/.test(main + smoke), 'the sandbox must stay on');
  assert.ok(!/preload/.test(main), 'the page needs no preload bridge');
});

test('the shell refuses network, popups, navigation, and permissions', () => {
  assert.match(main, /onBeforeRequest/);
  assert.match(main, /setWindowOpenHandler\(\(\) => \(\{ action: 'deny' \}\)\)/);
  assert.match(main, /will-navigate/);
  assert.match(main, /setPermissionRequestHandler/);
  assert.match(main, /setPermissionCheckHandler/);
  assert.match(main, /requestSingleInstanceLock/);
});

test('the shell requires only electron and small builtins, and uses no network or process APIs', () => {
  assert.deepStrictEqual(requires(main).sort(), ['./smoke', 'electron', 'path', 'url']);
  assert.deepStrictEqual(requires(smoke), ['electron']);
  for (const src of [main, smoke]) {
    assert.ok(!/child_process|\bnet\b|https?:\/\/(?!example\.com)/.test(src.replace(/\/\*[\s\S]*?\*\//g, '')), 'no network or process modules');
  }
});

test('the only environment variable the shell reads is the smoke-test switch', () => {
  const vars = [...(main + smoke).matchAll(/process\.env\.(\w+)/g)].map((m) => m[1]);
  assert.deepStrictEqual([...new Set(vars)], ['SIGNAL_GHOST_SMOKE']);
});

test('staging produces exactly the files that ship, and nothing from the repo', () => {
  const { stage } = require('./scripts/stage');
  const dir = stage();
  assert.deepStrictEqual(fs.readdirSync(dir).sort(), ['index.html', 'main.js', 'package.json', 'smoke.js']);
  const staged = JSON.parse(fs.readFileSync(path.join(dir, 'package.json'), 'utf8'));
  assert.strictEqual(staged.main, 'main.js');
  assert.strictEqual(staged.devDependencies, undefined);
  assert.strictEqual(staged.scripts, undefined);
  const html = fs.readFileSync(path.join(dir, 'index.html'), 'utf8');
  assert.match(html, /default-src 'none'/);
  assert.ok(!/https?:\/\//i.test(html));
});

test('the Linux install script is POSIX sh and handles install, uninstall, and bad flags', () => {
  const sh = read('scripts/install-linux.sh');
  assert.match(sh, /^#!\/bin\/sh/);
  assert.match(sh, /set -eu/);
  assert.match(sh, /--uninstall/);
  assert.match(sh, /--prefix/);
  assert.ok(!/\bsudo\b/.test(sh), 'installs per user, no root');
});

test('install script runs against a fake build and cleans up after itself', () => {
  const os = require('os');
  const { spawnSync } = require('child_process');
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'sg-install-'));
  const build = path.join(tmp, 'build');
  const prefix = path.join(tmp, 'pre fix'); // a space on purpose
  fs.mkdirSync(build);
  fs.copyFileSync(path.join(ROOT, 'scripts/install-linux.sh'), path.join(build, 'install-linux.sh'));
  fs.chmodSync(path.join(build, 'install-linux.sh'), 0o755);
  fs.writeFileSync(path.join(build, 'signal-ghost'), '#!/bin/sh\necho ok\n', { mode: 0o755 });
  const sh = (...a) => spawnSync(path.join(build, 'install-linux.sh'), a, { encoding: 'utf8' });

  assert.strictEqual(sh('--prefix', prefix).status, 0);
  const launcher = path.join(prefix, 'bin', 'signal-ghost');
  assert.strictEqual(spawnSync(launcher, { encoding: 'utf8' }).stdout.trim(), 'ok');
  const desktop = fs.readFileSync(path.join(prefix, 'share/applications/signal-ghost.desktop'), 'utf8');
  assert.match(desktop, /^Exec=".+signal-ghost"$/m);
  assert.ok(!fs.existsSync(path.join(prefix, 'share/signal-ghost/install-linux.sh')));

  assert.strictEqual(sh('--uninstall', '--prefix', prefix).status, 0);
  assert.ok(!fs.existsSync(launcher));
  assert.strictEqual(sh('--bogus').status, 2);
  fs.rmSync(tmp, { recursive: true });
});
