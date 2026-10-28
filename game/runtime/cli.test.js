const test = require('node:test');
const assert = require('node:assert');
const path = require('path');
const { spawnSync } = require('child_process');

const CLI = path.join(__dirname, 'cli.js');
const play = (lines, args = []) =>
  spawnSync(process.execPath, [CLI, ...args], { input: `${lines.join('\n')}\n`, encoding: 'utf8', env: {} });

test('launcher plays a mission offline with an empty environment', () => {
  const r = play(['inspect E07', 'inspect E14', 'commit H3 0.8 E14'], ['--seed', '2']);
  assert.strictEqual(r.status, 0);
  assert.match(r.stdout, /Committed H3 at 0.8\. Correct\./);
});

test('launcher reports rejected actions without crashing', () => {
  const r = play(['inspect E99', 'link E01 H1 supports', 'bogus', 'quit']);
  assert.strictEqual(r.status, 0);
  assert.match(r.stdout, /Rejected \(UNKNOWN_EVIDENCE\)/);
  assert.match(r.stdout, /Rejected \(NOT_INSPECTED\)/);
  assert.match(r.stdout, /Unknown command/);
});
