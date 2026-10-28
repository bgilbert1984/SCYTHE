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
const preload = read('preload.js');
const steamSrc = read('steam.js');

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
  assert.match(main, /preload:\s*path\.join\(__dirname, 'preload\.js'\)/);
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
  assert.deepStrictEqual(requires(main).sort(), ['./smoke', './steam', './steam.config.json', 'electron', 'fs', 'path', 'url']);
  assert.deepStrictEqual(requires(smoke), ['electron']);
  assert.deepStrictEqual(requires(preload), ['electron']);
  assert.deepStrictEqual(requires(steamSrc), ['steamworks.js'], 'Steam is the only optional native dependency');
  for (const src of [main, smoke, preload, steamSrc]) {
    assert.ok(!/child_process|\bnet\b|https?:\/\/(?!example\.com)/.test(src.replace(/\/\*[\s\S]*?\*\//g, '')), 'no network or process modules');
  }
});

test('the only environment variables the shell reads are the smoke switch and the Steam app id', () => {
  const vars = [...(main + smoke + preload + steamSrc).matchAll(/process\.env\.(\w+)/g)].map((m) => m[1]);
  assert.deepStrictEqual([...new Set(vars)].sort(), ['SIGNAL_GHOST_SMOKE', 'SIGNAL_GHOST_STEAM_APPID']);
});

test('the page-to-main bridge is three data-only calls and exposes nothing else', () => {
  const exposed = [...preload.matchAll(/^\s+(\w+): \(/gm)].map((m) => m[1]);
  assert.deepStrictEqual(exposed, ['status', 'saved', 'start']);
  assert.strictEqual((preload.match(/exposeInMainWorld/g) || []).length, 1);
  assert.ok([...preload.matchAll(/ipcRenderer(.?)/g)].slice(1).every((m) => m[1] === '.'), 'ipcRenderer is only ever called, never exposed');
  assert.ok(!/ipcRenderer\.(on|send|once)/.test(preload), 'the page cannot subscribe to or send raw messages');
});

test('every IPC handler refuses callers other than the game page', () => {
  const handlers = [...main.matchAll(/ipcMain\.handle\('([^']+)', (\w+)\(/g)];
  assert.deepStrictEqual(handlers.map((m) => m[1]), ['platform:status', 'platform:saved', 'platform:start']);
  assert.ok(handlers.every((m) => m[2] === 'only'), 'each handler is wrapped in the sender check');
  assert.match(main, /e\.senderFrame\.url !== PAGE_URL/);
});

test('Steam features are off by default and ship no app id', () => {
  assert.deepStrictEqual(JSON.parse(read('steam.config.json')), { appId: 0 });
});

test('staging produces exactly the files that ship, and nothing from the repo', () => {
  const { stage } = require('./scripts/stage');
  const dir = stage();
  assert.deepStrictEqual(fs.readdirSync(dir).sort(), ['game', 'index.html', 'main.js', 'package.json', 'preload.js', 'smoke.js', 'steam.config.json', 'steam.js']);
  const shipped = (d) => fs.readdirSync(d, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? shipped(path.join(d, e.name)) : [path.join(d, e.name)]));
  const files = shipped(path.join(dir, 'game')).map((f) => path.relative(dir, f));
  assert.ok(files.every((f) => !f.endsWith('.test.js') && !f.endsWith('cli.js')), 'no tests or CLI ship');
  for (const f of ['game/runtime/engine.js', 'game/runtime/save.js', 'game/platform/service.js', 'game/cases/signal_ghost/case.json']) {
    assert.ok(files.includes(f), f);
  }
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

test('Steam depot files are generated from validated ids and never invent an app id', () => {
  const { vdf } = require('./scripts/steam-build');
  const r = vdf({ appId: 480, depots: { linux: 481, win32: 482 }, contentBase: '/b', description: 'say "hi"' });
  assert.deepStrictEqual(Object.keys(r.files).sort(), ['app_build.vdf', 'depot_build_481.vdf', 'depot_build_482.vdf']);
  assert.match(r.files['app_build.vdf'], /"AppID" "480"/);
  assert.match(r.files['app_build.vdf'], /"Desc" "say \\"hi\\""/);
  assert.match(r.files['depot_build_481.vdf'], /"ContentRoot" "\/b\/signal-ghost-linux-x64"/);
  for (const bad of [{ appId: 0 }, { appId: 'x' }, { appId: 480, depots: {} }, { appId: 480, depots: { linux: 0 } }, { appId: 480, depots: { linux: 5, win32: 5 } }]) {
    assert.ok(vdf({ depots: { linux: 1 }, contentBase: '/b', description: '', ...bad }).error, JSON.stringify(bad));
  }
});

test('a Steam build needs an explicit app id, and the plain build ships without steamworks', () => {
  assert.match(read('scripts/package.js'), /--steam needs --app-id/);
  assert.match(read('scripts/package.js'), /unpack: '\*\*\/steamworks\.js\/dist\/\*\*'/);
  assert.ok(!('steamworks.js' in { ...pkg.dependencies, ...pkg.devDependencies }), 'steamworks.js is added only for --steam builds');
});
