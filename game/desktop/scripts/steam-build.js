#!/usr/bin/env node
/**
 * steam-build.js — Files for Valve's SteamPipe uploader and the Steamworks partner site.
 *
 *   node scripts/steam-build.js vdf --app-id N --linux-depot D1 --win-depot D2 [--out DIR]
 *       writes app_build.vdf and one depot_build_<id>.vdf per depot, for use with
 *       steamcmd +run_app_build. Content roots point at out/signal-ghost-<platform>-x64.
 *   node scripts/steam-build.js achievements
 *       prints the achievement ids, names, and descriptions to register in the partner site.
 *
 * Nothing here talks to Steam. Uploading needs steamcmd and a partner account.
 */

const fs = require('fs');
const path = require('path');
const { manifest } = require('../../platform/achievements');

const ROOT = path.resolve(__dirname, '..');
const quote = (s) => `"${String(s).replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`;
const posInt = (v) => (/^[1-9]\d*$/.test(String(v)) ? Number(v) : null);

/** Returns { files: { name: text } } or { error }. */
function vdf({ appId, depots, contentBase, description }) {
  if (!posInt(appId)) return { error: '--app-id must be a positive integer' };
  const entries = Object.entries(depots);
  if (!entries.length || entries.some(([, id]) => !posInt(id))) return { error: 'give at least one depot id, as a positive integer' };
  if (new Set(entries.map(([, id]) => String(id))).size !== entries.length) return { error: 'depot ids must be different' };

  const files = {};
  const depotLines = [];
  for (const [platform, id] of entries) {
    const name = `depot_build_${id}.vdf`;
    depotLines.push(`    ${quote(id)} ${quote(name)}`);
    files[name] = [
      '"DepotBuildConfig"', '{',
      `  "DepotID" ${quote(id)}`,
      `  "ContentRoot" ${quote(path.join(contentBase, `signal-ghost-${platform}-x64`))}`,
      '  "FileMapping"', '  {',
      '    "LocalPath" "*"', '    "DepotPath" "."', '    "recursive" "1"', '  }',
      '  "FileExclusion" "*.pdb"', '}', '',
    ].join('\n');
  }
  files['app_build.vdf'] = [
    '"AppBuild"', '{',
    `  "AppID" ${quote(appId)}`,
    `  "Desc" ${quote(description)}`,
    '  "BuildOutput" "steam-output"',
    '  "Depots"', '  {', ...depotLines, '  }', '}', '',
  ].join('\n');
  return { files };
}

function main(argv) {
  const cmd = argv[0];
  const opt = (flag) => (argv.indexOf(flag) > 0 ? argv[argv.indexOf(flag) + 1] : undefined);
  if (cmd === 'achievements') {
    process.stdout.write(`${JSON.stringify(manifest(), null, 2)}\n`);
    return 0;
  }
  if (cmd === 'vdf') {
    const depots = {};
    if (opt('--linux-depot')) depots.linux = opt('--linux-depot');
    if (opt('--win-depot')) depots.win32 = opt('--win-depot');
    const r = vdf({
      appId: opt('--app-id'), depots, contentBase: path.join(ROOT, 'out'),
      description: opt('--desc') || `SIGNAL GHOST ${require('../package.json').version}`,
    });
    if (r.error) {
      process.stderr.write(`${r.error}\n`);
      return 2;
    }
    const out = path.resolve(opt('--out') || path.join(ROOT, 'out', 'steam'));
    fs.mkdirSync(out, { recursive: true });
    for (const [name, text] of Object.entries(r.files)) fs.writeFileSync(path.join(out, name), text);
    process.stdout.write(`${Object.keys(r.files).map((f) => path.join(out, f)).join('\n')}\n`);
    return 0;
  }
  process.stderr.write('usage: steam-build.js vdf --app-id N [--linux-depot D] [--win-depot D] [--out DIR] | achievements\n');
  return 2;
}

if (require.main === module) process.exitCode = main(process.argv.slice(2));

module.exports = { vdf };
