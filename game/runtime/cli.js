#!/usr/bin/env node
/**
 * cli.js — Text launcher for the isolated runtime.
 *
 * Proves the game starts and plays offline with no SCYTHE services. The only I/O
 * is reading the case file and stdin/stdout. Usage:
 *
 *   node game/runtime/cli.js [--seed N] [--case path/to/case.json] [--save file]
 *   node game/runtime/cli.js --replay file [--case path/to/case.json]
 *
 * --save writes a save file when the game ends or you quit. --replay verifies a save by
 * replaying it and reports the result; it exits non-zero if the save cannot be replayed.
 */

const fs = require('fs');
const path = require('path');
const readline = require('readline');
const { createGame, COSTS } = require('./engine');
const { createSave, serialize, load } = require('./save');

const HELP = `Commands:
  status                              show budget, hypotheses, and evidence
  inspect <E>                         read an item                       (cost ${COSTS.inspect})
  compare <A> <B>                     compare two inspected items        (cost ${COSTS.compare})
  corroborate <E>                     ask for independent confirmation   (cost ${COSTS.corroborate})
  reevaluate <E> <variant>            re-read under other conditions     (cost ${COSTS.reevaluate})
  link <E> <H> supports|contradicts|neutral
  unlink <E> <H>
  reject <H> | restore <H>            (restore costs ${COSTS.restore})
  confidence <H> <0-1>
  commit <H> <0-1> <E> [<E>...]       finish: hypothesis, confidence, cited evidence
  help | quit`;

function parseArgs(argv) {
  const out = { seed: 0, casePath: path.join(__dirname, '..', 'cases', 'signal_ghost', 'case.json') };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--seed') out.seed = Number(argv[++i]);
    else if (argv[i] === '--case') out.casePath = path.resolve(argv[++i]);
    else if (argv[i] === '--save') out.savePath = path.resolve(argv[++i]);
    else if (argv[i] === '--replay') out.replayPath = path.resolve(argv[++i]);
  }
  if (!Number.isFinite(out.seed)) throw new Error('--seed must be a number');
  return out;
}

function toAction(line) {
  const [cmd, ...a] = line.trim().split(/\s+/);
  switch (cmd) {
    case 'inspect': return { type: 'inspect', evidence: a[0] };
    case 'compare': return { type: 'compare', a: a[0], b: a[1] };
    case 'corroborate': return { type: 'corroborate', evidence: a[0] };
    case 'reevaluate': return { type: 'reevaluate', evidence: a[0], variant: a[1] };
    case 'link': return { type: 'link', evidence: a[0], hypothesis: a[1], relation: a[2] };
    case 'unlink': return { type: 'unlink', evidence: a[0], hypothesis: a[1] };
    case 'reject': return { type: 'reject', hypothesis: a[0] };
    case 'restore': return { type: 'restore', hypothesis: a[0] };
    case 'confidence': return { type: 'set_confidence', hypothesis: a[0], value: Number(a[1]) };
    case 'commit': return { type: 'commit', hypothesis: a[0], confidence: Number(a[1]), citations: a.slice(2) };
    default: return null;
  }
}

function printStatus(v, out) {
  out(`${v.title}  |  budget ${v.budget_remaining}/${v.budget}`);
  for (const h of v.hypotheses) {
    const conf = h.confidence === null ? '-' : h.confidence;
    out(`  ${h.id}${h.rejected ? ' [rejected]' : ''} (conf ${conf}): ${h.claim}`);
  }
  for (const e of v.evidence) out(`  ${e.inspected ? '*' : ' '} ${e.id} [${e.type}] ${e.title}`);
  out(`  Conditions known: ${v.conditions_known.map((c) => c.variant_id).join(', ')}`);
}

function printResult(action, r, out) {
  if (action.type === 'inspect') {
    out(`${r.id}  ${r.title}\n  ${r.reading}\n  Limits: ${r.limits}\n  Source: ${r.source.label} (${r.source.reliability}). ${r.source.limits}\n  Conditions: ${r.conditions}, ${r.time}`);
  } else if (action.type === 'commit') {
    out(`Committed ${r.committed_hypothesis} at ${r.stated_confidence}. ${r.is_correct ? 'Correct.' : 'Incorrect.'}`);
    out(`Reference posterior: ${Object.entries(r.reference_posterior).map(([h, p]) => `${h}=${p.toFixed(2)}`).join(' ')}`);
    out(`Truth: ${r.debrief.truth}`);
  } else {
    out(JSON.stringify(r));
  }
}

function run(game, input, output) {
  const out = (s) => output.write(`${s}\n`);
  const rl = readline.createInterface({ input, terminal: false });
  out(game.view().briefing);
  out('Type "help" for commands.');
  rl.on('line', (line) => {
    const t = line.trim();
    if (!t) return;
    if (t === 'quit') return rl.close();
    if (t === 'help') return out(HELP);
    if (t === 'status') return printStatus(game.view(), out);
    const action = toAction(t);
    if (!action) return out(`Unknown command. Type "help".`);
    const r = game.dispatch(action);
    if (!r.ok) return out(`Rejected (${r.error.code}): ${r.error.message}`);
    printResult(action, r.result, out);
    if (game.finished) rl.close();
  });
  return new Promise((resolve) => rl.on('close', resolve));
}

/** Replay a save file and describe it. Returns { ok, lines }. */
function replaySave(caseData, text) {
  const r = load(caseData, text);
  if (r.error) return { ok: false, lines: [`Cannot replay (${r.error.code}): ${r.error.message}`] };
  const v = r.game.view();
  const lines = [`Replayed ${r.save.actions.length} actions (seed ${r.save.seed}). Budget left ${v.budget_remaining}/${v.budget}.`];
  if (v.outcome) {
    const o = v.outcome;
    lines.push(`Committed ${o.committed_hypothesis} at ${o.stated_confidence}: ${o.score.outcome_text}. Score ${o.score.total}.`);
    lines.push(r.verified ? 'Recorded result reproduced exactly.' : 'No recorded result to verify against.');
  } else {
    lines.push('Investigation in progress.');
  }
  return { ok: true, lines };
}

if (require.main === module) {
  const args = parseArgs(process.argv.slice(2));
  const caseData = JSON.parse(fs.readFileSync(args.casePath, 'utf8'));
  if (args.replayPath) {
    const r = replaySave(caseData, fs.readFileSync(args.replayPath, 'utf8'));
    process.stdout.write(`${r.lines.join('\n')}\n`);
    process.exitCode = r.ok ? 0 : 1;
  } else {
    const game = createGame(caseData, { seed: args.seed });
    run(game, process.stdin, process.stdout).then(() => {
      if (args.savePath) {
        fs.writeFileSync(args.savePath, serialize(createSave(game, caseData)));
        process.stdout.write(`Saved to ${args.savePath}\n`);
      }
    });
  }
}

module.exports = { run, toAction, parseArgs, replaySave };
