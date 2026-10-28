#!/usr/bin/env node
/**
 * build.js — Assemble the player Hub into one self-contained HTML file.
 *
 * Inlines the runtime engine, the case, the store, the styles, and the UI, so the
 * page opens straight from disk with no server, network, or install step.
 *
 *   node game/hub/build.js            -> game/hub/dist/index.html
 */

const fs = require('fs');
const path = require('path');

const GAME = path.resolve(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(GAME, p), 'utf8');

// Modules bundled for the browser. Keys are paths relative to game/.
const MODULES = [
  'runtime/rng.js',
  'cases/lib/posterior.js',
  'runtime/scoring.js',
  'runtime/engine.js',
  'hub/src/store.js',
];
const CASE_PATH = 'cases/signal_ghost/case.json';

const SHIM = `
var __defs = {}, __cache = {};
function __norm(p) {
  var out = [];
  p.split('/').forEach(function (s) {
    if (s === '..') out.pop(); else if (s && s !== '.') out.push(s);
  });
  return out.join('/');
}
function __req(fromDir) {
  return function (spec) {
    var key = __norm(fromDir + '/' + spec);
    if (!/\\.(js|json)$/.test(key)) key += '.js';
    if (!(key in __defs)) throw new Error('Module not bundled: ' + key);
    if (!(key in __cache)) {
      var m = { exports: {} };
      __cache[key] = m;
      __defs[key](m, m.exports, __req(key.split('/').slice(0, -1).join('/')));
    }
    return __cache[key].exports;
  };
}
`;

function build() {
  const defs = MODULES.map(
    (m) => `__defs[${JSON.stringify(m)}] = function (module, exports, require) {\n${read(m)}\n};`,
  );
  defs.push(`__defs[${JSON.stringify(CASE_PATH)}] = function (module) { module.exports = ${JSON.stringify(JSON.parse(read(CASE_PATH)))}; };`);
  const bundle = `${SHIM}\n${defs.join('\n')}
var __engine = __req('')('runtime/engine.js');
window.__game = {
  createGame: __engine.createGame, replay: __engine.replay, COSTS: __engine.COSTS,
  createStore: __req('')('hub/src/store.js').createStore,
  caseData: __req('')(${JSON.stringify(CASE_PATH)})
};`;
  // Guard against a literal </script> inside inlined content closing the tag early.
  const safe = (s) => s.replace(/<\/script/gi, '<\\/script');
  return read('hub/src/template.html')
    .replace('/*__STYLE__*/', () => read('hub/src/style.css'))
    .replace('/*__BUNDLE__*/', () => safe(bundle))
    .replace('/*__APP__*/', () => safe(read('hub/src/app.js')));
}

if (require.main === module) {
  const out = path.join(__dirname, 'dist', 'index.html');
  fs.mkdirSync(path.dirname(out), { recursive: true });
  fs.writeFileSync(out, build());
  process.stdout.write(`${out}\n`);
}

module.exports = { build };
