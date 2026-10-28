/**
 * Isolation checks (design §6): the runtime must not reach live RF acquisition,
 * production services, hardware, the network, or the rest of the repository.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const GAME_ROOT = path.resolve(__dirname, '..');
const FORBIDDEN_MODULES = [
  'net', 'http', 'https', 'http2', 'dgram', 'dns', 'tls', 'child_process',
  'worker_threads', 'cluster', 'vm', 'os', 'fs', 'readline',
];
// Only the launcher and the Hub build script may touch the filesystem and stdin/stdout.
const TOOLS = new Set(['cli.js', 'build.js']);
const LAUNCHER_ONLY = new Set(['fs', 'path', 'readline']);
const FORBIDDEN_PATTERNS = [
  /process\.env/, /\bfetch\s*\(/, /XMLHttpRequest/, /WebSocket/, /\beval\s*\(/, /new Function\s*\(/,
  /localhost|127\.0\.0\.1/, /duckdb|fusionauth|orchestrator/i,
];

function sourceFiles(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((d) => {
    const p = path.join(dir, d.name);
    if (d.isDirectory()) return sourceFiles(p);
    return d.name.endsWith('.js') && !d.name.endsWith('.test.js') ? [p] : [];
  });
}

function requires(src) {
  return [...src.matchAll(/require\(\s*['"]([^'"]+)['"]\s*\)/g)].map((m) => m[1]);
}

const files = sourceFiles(GAME_ROOT);

test('there are runtime sources to check', () => {
  assert.ok(files.length >= 7);
});

test('every require stays inside game/ or is a permitted builtin', () => {
  for (const f of files) {
    const isLauncher = TOOLS.has(path.basename(f));
    for (const dep of requires(fs.readFileSync(f, 'utf8'))) {
      const name = dep.replace(/^node:/, '');
      if (dep.startsWith('.')) {
        const resolved = path.resolve(path.dirname(f), dep);
        assert.ok(resolved.startsWith(GAME_ROOT + path.sep), `${f} requires outside game/: ${dep}`);
      } else if (isLauncher && LAUNCHER_ONLY.has(name)) {
        // permitted
      } else {
        assert.ok(!FORBIDDEN_MODULES.includes(name), `${f} requires forbidden module ${dep}`);
        assert.fail(`${f} requires unlisted module ${dep}`);
      }
    }
  }
});

test('no network, environment, or service references in runtime sources', () => {
  for (const f of files) {
    const src = fs.readFileSync(f, 'utf8');
    for (const re of FORBIDDEN_PATTERNS) assert.ok(!re.test(src), `${f} matches ${re}`);
  }
});

test('only the launcher and build script perform I/O', () => {
  for (const f of files) {
    if (TOOLS.has(path.basename(f))) continue;
    const deps = requires(fs.readFileSync(f, 'utf8')).map((d) => d.replace(/^node:/, ''));
    for (const d of deps) assert.ok(d.startsWith('.'), `${f} is not pure: requires ${d}`);
  }
});

test('the built Hub page is self-contained and blocks the network', () => {
  const html = require('../hub/build').build();
  assert.match(html, /Content-Security-Policy/);
  assert.match(html, /default-src 'none'/);
  assert.ok(!/https?:\/\//i.test(html), 'built page must not reference any URL');
  assert.ok(!/<script[^>]+src=/i.test(html), 'built page must not load external scripts');
});
