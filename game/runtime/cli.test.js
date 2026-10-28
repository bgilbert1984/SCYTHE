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

test('launcher saves a game and replays it from the file with the result verified', () => {
  const fs = require('fs');
  const os = require('os');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'game-save-'));
  const file = path.join(dir, 'run.json');
  const played = play(['inspect E07', 'inspect E14', 'commit H3 0.8 E14'], ['--seed', '4', '--save', file]);
  assert.strictEqual(played.status, 0);
  assert.match(played.stdout, /Saved to/);
  const replayed = spawnSync(process.execPath, [CLI, '--replay', file], { encoding: 'utf8', env: {} });
  assert.strictEqual(replayed.status, 0);
  assert.match(replayed.stdout, /Replayed 3 actions \(seed 4\)/);
  assert.match(replayed.stdout, /Recorded result reproduced exactly/);
  fs.rmSync(dir, { recursive: true });
});

test('launcher refuses a tampered save with a non-zero exit', () => {
  const fs = require('fs');
  const os = require('os');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'game-save-'));
  const file = path.join(dir, 'run.json');
  play(['inspect E14', 'commit H3 0.8 E14'], ['--seed', '4', '--save', file]);
  fs.writeFileSync(file, fs.readFileSync(file, 'utf8').replace('"E14"', '"E01"'));
  const r = spawnSync(process.execPath, [CLI, '--replay', file], { encoding: 'utf8', env: {} });
  assert.strictEqual(r.status, 1);
  assert.match(r.stdout, /CORRUPT/);
  fs.rmSync(dir, { recursive: true });
});
