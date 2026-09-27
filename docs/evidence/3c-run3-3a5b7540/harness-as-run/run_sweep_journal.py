import json, sys, subprocess, time, shutil, hashlib
from pathlib import Path
import os
S = os.environ.get("SCYTHE_SCRATCH", "/tmp/claude-1000/-home-spectrcyde/8cbdba26-1530-4b3a-b2b7-1259a6aff70b/scratchpad")
sys.path.insert(0, S)
from sweep import (REPO, WORKTREES, PYTHON, SUITE_FULL, git, checkpoint_blobs,
                   run_control, working_tree_hashes, classify, expand, unittest_argv,
                   require_sane_baseline, resolve_branch,
                   control_timeout, TIMEOUT, TIMEOUT_FACTOR)
from controls_journal import (CONTROLS, INTRODUCES, ACCEPTED_IN_SCOPE,
                           ACCEPTED_DEFERRED, check_inventory,
                           check_mutations)

# The slice branch. It is deleted when the slice merges, so it is named
# here rather than hardcoded; SCYTHE_BRANCH selects the next one.
BRANCH = os.environ.get("SCYTHE_BRANCH", "feat/5.26-journal-core-repair")
COMMIT = resolve_branch(BRANCH)
WATCHED = sorted({rel for _c, ps in CONTROLS for rel, _o, _n in ps})
BASE = checkpoint_blobs(COMMIT, WATCHED)

# Audited against the code they will mutate, before anything runs. The anchor
# check proves a mutation APPLIES; this proves it mutates rather than wrecks.
# `A2` once ran an entire sweep raising NameError on every reconstruction --- it
# classified TEST_FAILURE with a large n and looked healthy to every check then
# in place, because its anchor matched exactly once.
_SOURCES = {rel: git("show", f"{COMMIT}:{rel}") for rel in WATCHED}
_INVENTORY = check_inventory(ACCEPTED_IN_SCOPE)
if _INVENTORY:
    for _problem in _INVENTORY:
        print("INVENTORY: " + _problem, flush=True)
    raise SystemExit("refusing to launch: inventory")
print("inventory: %d controls, %d accepted in scope, %d deferred (%s)"
      % (len(CONTROLS), len(ACCEPTED_IN_SCOPE), len(ACCEPTED_DEFERRED),
         ", ".join(sorted(ACCEPTED_DEFERRED))), flush=True)
_AUDIT = check_mutations(_SOURCES, CONTROLS, INTRODUCES)
if _AUDIT:
    for _problem in _AUDIT:
        print("MUTATION AUDIT: " + _problem, flush=True)
    raise SystemExit("refusing to launch: %d mutation problem(s)" % len(_AUDIT))
print("mutation audit: %d controls, no problems" % len(CONTROLS), flush=True)
# The tree whose integrity matters is the BUILD worktree: the new modules
# exist only on the branch, and a leak from a disposable worktree would land
# there rather than in main's checkout.
BUILD = Path(S) / "build3br"   # this host's worktree for the slice branch
import hashlib as _h
def _build_hashes(paths):
    return {p: _h.sha256((BUILD / p).read_bytes()).hexdigest() for p in paths}
BEFORE = _build_hashes(WATCHED)
out = Path(S) / "sweep-journal.jsonl"
out.write_text("")

# The unmutated baseline, in its own worktree, so "collection degraded" is
# measured against a number this checkpoint actually produces.
tree = WORKTREES / "BASELINE"
WORKTREES.mkdir(parents=True, exist_ok=True)
shutil.rmtree(tree, ignore_errors=True)
subprocess.run(["git","worktree","add","--detach",str(tree),COMMIT],
               cwd=str(REPO), capture_output=True, check=True)
t0 = time.time()
# `-v` is load-bearing, not diagnostic: the skip regex in `classify`
# matches verbose per-test lines, so a non-verbose baseline records an
# EMPTY skip set and every control then reports its own legitimate
# skips as newly suppressed. `run_control` has always passed it; the
# baseline did not, which made the suppression column uninformative.
proc = subprocess.run(unittest_argv(PYTHON, SUITE_FULL, tree), cwd=str(tree),
                      capture_output=True, text=True, timeout=4 * TIMEOUT)
BASELINE_ELAPSED = round(time.time()-t0, 1)
cls, count, ids = classify(proc, BASELINE_ELAPSED)
BASELINE_SKIPS = getattr(classify, "last_skipped", [])
subprocess.run(["git","worktree","remove","--force",str(tree)], cwd=str(REPO),
               capture_output=True)
shutil.rmtree(tree, ignore_errors=True)
print(f"BASELINE  {cls}  {count} tests  exit {proc.returncode}  "
      f"{BASELINE_ELAPSED}s", flush=True)
# 2322 is what 655bbaa (feat/5.20-3c-wire, integrated onto main) collects,
# exactly: af188f1's 2306 plus the 16 tests 3c-wire brought in. The journal's
# controlled file is blob-identical to a1cffd8 there, so the J-series
# certification carries, but an exact-match gate written for an earlier tree
# refuses this one. MANIFEST.txt carries the table (2245 / 2254 / 2306 / 2322).
# A baseline that differs from 2322 means a different generation of the suite,
# which no floor can report; the floor moves with the checkpoint and is never
# lowered.
require_sane_baseline(cls, count, 2322, ids=ids, proc=proc,
                      report=Path(S) / "baseline-refused-journal.out",
                      expected=2322)
# Each control's bound is derived from the baseline this host just produced,
# not from a constant calibrated elsewhere: a fixed 1800s makes a healthy
# control on a slower or loaded host read as TIMED_OUT.
CONTROL_TIMEOUT = control_timeout(BASELINE_ELAPSED)
print(f"TIMEOUT   controls bounded at {CONTROL_TIMEOUT}s  "
      f"(floor {TIMEOUT}s, {TIMEOUT_FACTOR} x baseline {BASELINE_ELAPSED}s)",
      flush=True)
BASELINE_COUNT = count
with out.open("a") as fh:
    fh.write(json.dumps({"control":"BASELINE","classification":cls,
                         "tests_ran":count,"checkpoint":COMMIT,
                         "exit_code":proc.returncode,"failing":ids,
                         # so suppression is computable from the file alone,
                         # without the runner's in-memory state
                         "skipped":BASELINE_SKIPS,"skips_added":[],
                         "controls_module":"controls_journal",
                         "elapsed_s":BASELINE_ELAPSED,
                         "control_timeout_s":CONTROL_TIMEOUT})+"\n")

for cid, patches in CONTROLS:
    rec = run_control(cid, patches, COMMIT, BASE, timeout=CONTROL_TIMEOUT)
    rec["skipped"] = getattr(classify, "last_skipped", [])
    rec["baseline_tests"] = BASELINE_COUNT
    rec["collection_degraded"] = (
        rec.get("tests_ran") is not None
        and BASELINE_COUNT is not None
        and rec["tests_ran"] < BASELINE_COUNT)
    # Computed BEFORE the write. It was set afterwards, so the field the
    # console flagged on never reached the file and no archived run can be
    # re-examined for suppression without re-running it.
    rec["skips_added"] = sorted(set(rec["skipped"]) - set(BASELINE_SKIPS))
    with out.open("a") as fh:
        fh.write(json.dumps(rec)+"\n")
    flag = "  COLLECTION-DEGRADED" if rec["collection_degraded"] else ""
    if rec["skips_added"]:
        flag += f"  SUPPRESSED:{len(rec['skips_added'])}"
    print(f"{rec['classification']:26} {cid[:52]:52} "
          f"n={len(rec.get('failing') or []):<3} ran={rec.get('tests_ran')} "
          f"{rec.get('elapsed_s')}s{flag}", flush=True)

AFTER = _build_hashes(WATCHED)
print("\nworking tree hash-identical throughout:", BEFORE == AFTER, flush=True)
subprocess.run(["git","worktree","prune"], cwd=str(REPO), capture_output=True)
