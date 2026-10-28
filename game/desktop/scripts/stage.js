#!/usr/bin/env node
/**
 * stage.js — Assemble exactly what ships inside the app into game/desktop/.stage.
 *
 * The shipped app is the built Hub page, the shell, and a minimal package.json. It does
 * not include the repository, node_modules, or any SCYTHE service code.
 */

const fs = require('fs');
const path = require('path');
const { build } = require('../../hub/build');

const ROOT = path.resolve(__dirname, '..');
const STAGE = path.join(ROOT, '.stage');

function stage() {
  const pkg = JSON.parse(fs.readFileSync(path.join(ROOT, 'package.json'), 'utf8'));
  fs.rmSync(STAGE, { recursive: true, force: true });
  fs.mkdirSync(STAGE, { recursive: true });
  fs.writeFileSync(path.join(STAGE, 'index.html'), build());
  for (const f of ['main.js', 'smoke.js']) fs.copyFileSync(path.join(ROOT, f), path.join(STAGE, f));
  fs.writeFileSync(path.join(STAGE, 'package.json'), `${JSON.stringify({
    name: pkg.name,
    productName: pkg.productName,
    version: pkg.version,
    description: pkg.description,
    main: 'main.js',
  }, null, 2)}\n`);
  return STAGE;
}

if (require.main === module) process.stdout.write(`${stage()}\n`);

module.exports = { stage, STAGE };
