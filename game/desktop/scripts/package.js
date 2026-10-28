#!/usr/bin/env node
/**
 * package.js — Build a distributable app folder (and, on Linux, a tarball).
 *
 *   node scripts/package.js linux     -> out/signal-ghost-linux-x64/ and .tar.gz
 *   node scripts/package.js win32     -> out/signal-ghost-win32-x64/
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
  const { packager } = require('@electron/packager');
  const pkg = JSON.parse(fs.readFileSync(path.join(ROOT, 'package.json'), 'utf8'));

  const appDir = stage();
  const [dir] = await packager({
    dir: appDir,
    out: OUT,
    name: pkg.name,
    executableName: pkg.name,
    platform,
    arch: 'x64',
    appVersion: pkg.version,
    asar: true,
    overwrite: true,
    prune: false,
    quiet: true,
  });
  process.stdout.write(`${dir}\n`);

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
