#!/usr/bin/env node
/**
 * package.js — Build a distributable app folder (and, on Linux, a tarball).
 *
 *   node scripts/package.js linux     -> out/signal-ghost-linux-x64/ and .tar.gz
 *   node scripts/package.js win32     -> out/signal-ghost-win32-x64/
 *                                      + SCYTHE-GAME-win-x64-portable.zip and .sha256
 *   ... --steam --app-id N            -> the same, with steamworks.js bundled and Steam features on for app N
 *
 * Needs Node >= 22.12 (Electron's installer and @electron/packager require it).
 */

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { spawnSync } = require('child_process');
const { stage } = require('./stage');

const ROOT = path.resolve(__dirname, '..');
const OUT = path.join(ROOT, 'out');
const PLATFORMS = ['linux', 'win32'];

const optionValue = (flag) => {
  const i = process.argv.indexOf(flag);
  return i > 0 ? process.argv[i + 1] : undefined;
};

/** Bundle steamworks.js into the staged app and point the shell at the Steam app id. */
function addSteam(appDir, appId) {
  const r = spawnSync('npm', ['install', '--omit=dev', '--ignore-scripts', '--no-audit', '--no-fund', 'steamworks.js@0.4.0'], { cwd: appDir, stdio: 'inherit' });
  if (r.status !== 0) process.exit(r.status || 1);
  fs.writeFileSync(path.join(appDir, 'steam.config.json'), `${JSON.stringify({ appId })}\n`);
}

function portableReadme(version) {
  return [
    `SIGNAL GHOST ${version} (SCYTHE: GAME), Windows x64 preview`,
    '',
    'Extract this folder anywhere and run signal-ghost.exe. Nothing is installed.',
    'The game is offline: it makes no network connections and needs no account or Steam.',
    '',
    'Your saved investigation and career history are kept in %APPDATA%\\signal-ghost.',
    'Delete that folder to reset. "Export save file" in the debrief writes a file you can keep or share.',
    '',
    'Check the download: the .sha256 file next to the zip holds its SHA-256.',
    '  PowerShell:  Get-FileHash SCYTHE-GAME-win-x64-portable.zip -Algorithm SHA256',
    '',
    'This build is not code-signed, so Windows SmartScreen may warn on first run.',
    '',
  ].join('\r\n');
}

async function main() {
  const platform = process.argv[2];
  if (!PLATFORMS.includes(platform)) {
    process.stderr.write(`usage: package.js ${PLATFORMS.join('|')}\n`);
    process.exit(2);
  }
  const [major, minor] = process.versions.node.split('.').map(Number);
  if (major < 22 || (major === 22 && minor < 12)) {
    process.stderr.write(`Node >= 22.12 is required (this is ${process.versions.node}).\n`);
    process.exit(2);
  }
  const withSteam = process.argv.includes('--steam');
  const appId = Number(optionValue('--app-id') || 0);
  if (withSteam && !(Number.isInteger(appId) && appId > 0)) {
    process.stderr.write('--steam needs --app-id <Steam app id> (480 is Valve\'s public test app).\n');
    process.exit(2);
  }
  const { packager } = require('@electron/packager');
  const pkg = JSON.parse(fs.readFileSync(path.join(ROOT, 'package.json'), 'utf8'));

  const appDir = stage();
  if (withSteam) addSteam(appDir, appId);
  const [dir] = await packager({
    dir: appDir,
    out: OUT,
    name: pkg.name,
    executableName: pkg.name,
    platform,
    arch: 'x64',
    appVersion: pkg.version,
    // Native Steam libraries cannot be loaded from inside an asar archive.
    asar: withSteam ? { unpack: '**/steamworks.js/dist/**' } : true,
    overwrite: true,
    prune: false,
    quiet: true,
  });
  process.stdout.write(`${dir}\n`);

  if (platform === 'win32') {
    fs.writeFileSync(path.join(dir, 'README.txt'), portableReadme(pkg.version));
    const archive = path.join(OUT, 'SCYTHE-GAME-win-x64-portable.zip');
    fs.rmSync(archive, { force: true });
    // Windows ships bsdtar (which writes zip with -a); elsewhere use zip(1).
    const r = process.platform === 'win32'
      ? spawnSync('tar', ['-a', '-cf', archive, '-C', OUT, path.basename(dir)], { stdio: 'inherit' })
      : spawnSync('zip', ['-qr', archive, path.basename(dir)], { cwd: OUT, stdio: 'inherit' });
    if (r.status !== 0) process.exit(r.status || 1);
    const sum = crypto.createHash('sha256').update(fs.readFileSync(archive)).digest('hex');
    fs.writeFileSync(`${archive}.sha256`, `${sum}  ${path.basename(archive)}\n`);
    process.stdout.write(`${archive}\n${sum}\n`);
  }

  if (platform === 'linux') {
    fs.copyFileSync(path.join(__dirname, 'install-linux.sh'), path.join(dir, 'install-linux.sh'));
    fs.chmodSync(path.join(dir, 'install-linux.sh'), 0o755);
    const archive = `${dir}.tar.gz`;
    const r = spawnSync('tar', ['-czf', archive, '-C', OUT, path.basename(dir)], { stdio: 'inherit' });
    if (r.status !== 0) process.exit(r.status || 1);
    const sum = crypto.createHash('sha256').update(fs.readFileSync(archive)).digest('hex');
    fs.writeFileSync(`${archive}.sha256`, `${sum}  ${path.basename(archive)}\n`);
    process.stdout.write(`${archive}\n${sum}\n`);
  }
}

main().catch((e) => {
  process.stderr.write(`${e.stack || e}\n`);
  process.exit(1);
});
