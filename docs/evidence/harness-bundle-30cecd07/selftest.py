"""Harness acceptance checks. The instrument is measured before it measures."""
import sys, json, hashlib
import os
sys.path.insert(0, os.environ.get("SCYTHE_SCRATCH", "/tmp/claude-1000/-home-spectrcyde/8cbdba26-1530-4b3a-b2b7-1259a6aff70b/scratchpad"))
from sweep import (REPO, git, checkpoint_blobs, run_control, working_tree_hashes,
                   MUTATION_DID_NOT_APPLY, MUTATION_AMBIGUOUS, TIMED_OUT,
                   CRASHED_BEFORE_COLLECTION, HARNESS_ERROR,
                   ZERO_DISCRIMINATION, TEST_FAILURE,
                   control_timeout, TIMEOUT, TIMEOUT_FACTOR, require_sane_baseline)

COMMIT = git("rev-parse", "HEAD")
M, N, T = "rf_corpus_manifest.py", "rf_corpus_namespace.py", "test_rf_corpus_namespace.py"
WATCHED = [M, N, T, "rf_promotion_envelope.py"]
BASE = checkpoint_blobs(COMMIT, WATCHED)
SMALL = ["test_rf_corpus_namespace.py"]

BEFORE = working_tree_hashes(WATCHED)

CASES = [
 # (id, patches, suite, timeout, expected)
 # A behavioural mutation, not a constant nothing asserts. The first version
 # of this case changed IQM_MAX_BODY_BYTES and was correctly reported
 # ZERO_DISCRIMINATION -- the harness was right and the case was wrong.
 ("S1-applies-once", [(M, "    if len(framed) > end:", "    if False:")],
  SMALL, 600, TEST_FAILURE),
 ("S2-missing-anchor", [(M, "this text does not exist anywhere", "x")], SMALL, 600,
  MUTATION_DID_NOT_APPLY),
 ("S3-ambiguous-anchor", [(M, "    raise ManifestRefused(", "    raise ManifestRefused(")],
  SMALL, 600, MUTATION_AMBIGUOUS),
 # A process that dies before unittest can report. A module-level *exception*
 # is NOT this: unittest wraps an import failure as a synthetic failing test,
 # so a detonation classifies as TEST_FAILURE with one failing id -- which S4b
 # records against the real suite, because the real sweep needs to know.
 ("S4-process-dies", [(M, "from __future__ import annotations",
                       "from __future__ import annotations\nimport os as _os; _os._exit(3)")],
  SMALL, 600, CRASHED_BEFORE_COLLECTION),
 ("S4b-import-detonation", [(M, "from __future__ import annotations",
                             "from __future__ import annotations\nraise ImportError('selftest detonation')")],
  None, 900, TEST_FAILURE),
 ("S5-timeout", [(T, "import ast\n", "import ast\nimport time as _t; _t.sleep(120)\n")],
  SMALL, 15, TIMED_OUT),
 ("S6-green", [(M, "# -- refusals ---------------------------------------------------------------",
                "# -- refusals (comment touched only) ---------------------------------------")],
  SMALL, 600, ZERO_DISCRIMINATION),
 # `Ran 0 tests` with a clean exit, which is evidence of nothing and must not
 # read as a green run. Twice wrong before this:
 #
 #   * the first version passed `--pattern` alongside explicit files, which is
 #     a usage error rather than an empty run;
 #   * the second anchored on `...manifest.v1`, gone since the schema bump, so
 #     it reported MUTATION_DID_NOT_APPLY at every current checkpoint. It was
 #     asserted to pass on the authoring machine without being run there.
 #
 # The suite is a module with no TestCase: `Ran 0 tests` through the ordinary
 # path, which `classify` maps to HARNESS_ERROR on the count before it reaches
 # the exit code (5 on 3.12). Diagnosed and repaired on the host that runs it.
 ("S7-zero-tests", [(M, 'IQM_SCHEMA = "scythe.iq-corpus-manifest.v2"',
                     'IQM_SCHEMA = "scythe.iq-corpus-manifest.v2"  # noqa')],
  ["rf_corpus_manifest.py"], 600, HARNESS_ERROR),
 # The discover spelling, which `unittest_argv` exists to make work. Without
 # it the child saw `-v discover -p PATTERN`, `unittest.main` never entered
 # discover mode, and `-p` was an unrecognized argument: exit 2, reported
 # CRASHED_BEFORE_COLLECTION. `expand` had always refused to glob a discover
 # pattern and that care was unreachable. Kept as a case because a fix nothing
 # exercises is the shape of the defect it repaired.
 ("S7b-discover-empty", [(M, 'IQM_SCHEMA = "scythe.iq-corpus-manifest.v2"',
                          'IQM_SCHEMA = "scythe.iq-corpus-manifest.v2"  # noqa2')],
  ["discover", "-p", "test_nothing_matches_this_*.py"], 600, HARNESS_ERROR),
]

# -- S8: the checkers check out ---------------------------------------------
#
# These ran once, on one machine, in a terminal. That is the shape of every
# unread guarantee this work has found --- `ATTESTATION_REFUSALS` was wrong for
# a whole slice because nothing read it. Here instead, so every box re-proves
# the audit and the reviewer's duplicate rule before every sweep.

def _s8():
    import itertools as _it
    from controls527 import check_mutations as _cm
    src = {M: git("show", f"{COMMIT}:{M}")}
    cases = [
        ("S8a-undeclared-name", [(M, "    if len(framed) > end:",
                                  "    if len(framed) > end and _nope:")], {},
         "introduces"),
        ("S8b-declared-name", [(M, "    if len(framed) > end:",
                                "    if len(framed) > end and _nope:")],
         {"S8b-declared-name": ("_nope",)}, None),
        ("S8c-unused-declaration", [(M, "    if len(framed) > end:",
                                     "    if False:")],
         {"S8c-unused-declaration": ("_never",)}, "does not use"),
        # The regression the fragment-tokenising regex fallback caused: a
        # mutation touching only a STRING must not report English words.
        ("S8d-string-only", [(M, 'IQM_SCHEMA = "scythe.iq-corpus-manifest.v2"',
                              'IQM_SCHEMA = "scythe.iq-corpus-manifest.v2"  # x')],
         {}, None),
        # A DEAD anchor once passed this audit silently: the count check set an
        # `incomplete` flag and continued without reporting, deferring to an
        # "anchor check" that only ran per control DURING the sweep. A stale
        # bundle aimed at a newer checkpoint therefore spent a baseline before
        # reporting MUTATION_DID_NOT_APPLY --- and the skipped control got no
        # identifier or syntax audit either.
        ("S8f-dead-anchor",
         [(M, "IQM_SCHEMA = 'this line does not exist at any checkpoint'",
              "IQM_SCHEMA = 'x'")], {}, "occurs 0 times"),
        ("S8g-ambiguous-anchor", [(M, "import ", "import  ")], {},
         "not once"),
    ]
    results = []
    for cid, patches, intro, expect in cases:
        out = _cm(src, [(cid, patches)], intro)
        if expect is None:
            good = not out
            detail = "CLEAN" if good else out[0]
        else:
            good = bool(out) and expect in out[0]
            detail = out[0] if out else "no problem reported"
        results.append((cid, good, detail))

    # The reviewer must not count empty failing sets as duplicates of one
    # another: eight zeros otherwise reported twenty-eight duplicate pairs.
    zero_sets = {"Z1": set(), "Z2": set(), "Z3": {"t"}, "Z4": {"t"}}
    dup = [(a, b) for a, b in _it.combinations(sorted(zero_sets), 2)
           if zero_sets[a] and zero_sets[a] == zero_sets[b]]
    results.append(("S8e-zeros-are-not-duplicates",
                    dup == [("Z3", "Z4")], f"pairs={dup}"))
    return results


for _cid, _good, _detail in _s8():
    print(f"{'PASS' if _good else 'FAIL'}  {_cid:30} {_detail}", flush=True)
    ok = globals().get("ok", True) and _good
    globals()["ok"] = ok

print(f"checkpoint {COMMIT}\n")
ok = True
RECS = {}
for cid, patches, suite, timeout, expected in CASES:
    rec = run_control(cid, patches, COMMIT, BASE, suite=suite, timeout=timeout)
    RECS[cid] = rec
    got = rec.get("classification")
    good = got == expected
    ok &= good
    print(f"{'PASS' if good else 'FAIL'}  {cid:22} expected {expected:26} got {got}"
          f"  ({rec.get('tests_ran')} tests, exit {rec.get('exit_code')}, "
          f"{rec.get('elapsed_s')}s)", flush=True)
    if not good:
        print("      ", json.dumps({k: v for k, v in rec.items()
                                    if k not in ("failing",)}), flush=True)


def check(cid, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"{'PASS' if good else 'FAIL'}  {cid:22} {detail}", flush=True)

# S8-timeout: the control bound is relative to the baseline this host produced.
# A fixed 1800s, calibrated on one host, makes a healthy control on a host three
# times slower under load read as TIMED_OUT -- an empty failing set that a
# reviewer counting zeros takes for an unwitnessed mutation. The floor holds on
# a fast host, the bound scales on a slow one, and the bound in force is
# recorded in every row so an archived TIMED_OUT row can be read for what it was.
check("S8-timeout-floor", control_timeout(60.0) == TIMEOUT,
      f"baseline 60s -> {control_timeout(60.0)}s (floor {TIMEOUT}s)")
check("S8-timeout-scales", control_timeout(400.0) == TIMEOUT_FACTOR * 400,
      f"baseline 400s -> {control_timeout(400.0)}s")
check("S8-timeout-unknown", control_timeout(None) == TIMEOUT,
      "no baseline elapsed -> floor")
check("S8-timeout-recorded", RECS["S5-timeout"].get("timeout_s") == 15,
      f"S5 row records timeout_s={RECS['S5-timeout'].get('timeout_s')}")

# S10: the baseline gate. It refuses a failing baseline and writes the report;
# it passes a clean one silently; and a clean baseline above the floor but off
# the expected count refuses as a different generation, which a floor cannot.
import subprocess as _sp, tempfile as _tf
_rep = os.path.join(_tf.mkdtemp(), "refused.out")
_fake = _sp.CompletedProcess(["x"], 1, "Ran 3 tests\n", "FAIL: t (m.T)\n")
try:
    require_sane_baseline(TEST_FAILURE, 3, 2, ids=["t (m.T)"], proc=_fake, report=_rep)
    _refused = False
except SystemExit as _e:
    _refused = _rep in str(_e)
check("S10-gate-refuses", _refused and os.path.exists(_rep)
      and "t (m.T)" in open(_rep).read(), f"a failing baseline refuses and writes {_rep}")
try:
    require_sane_baseline(ZERO_DISCRIMINATION, 3, 2, ids=[], proc=_fake, report=_rep + ".2")
    _passed = not os.path.exists(_rep + ".2")
except SystemExit:
    _passed = False
check("S10-gate-passes", _passed, "a clean baseline above the gate passes and writes nothing")
try:
    require_sane_baseline(ZERO_DISCRIMINATION, 2245, 2245, expected=2240)
    _stale = False
except SystemExit as _e:
    _stale = "different generations" in str(_e)
check("S10-gate-generations", _stale,
      "a clean baseline above the floor but off the expected count refuses")

# S11: the skip column sees docstring tests. `unittest -v` reports a test that
# has a docstring on two lines -- the id, then the docstring's first line with
# the verdict -- and the first skip pattern required the verdict on the id
# line. On a host without the pinned observation tree, the position-act suite
# skipped one such test and the column recorded one skip where unittest counted
# two. The text below is unittest's own output for six tests, captured rather
# than composed, so the pattern is measured against the shape it must read.
from sweep import classify as _classify
_verbose = (
    "test_decorated_plain (t.T.test_decorated_plain) ... skipped 'decorated plain'\n"
    "test_decorated_skip (t.T.test_decorated_skip)\n"
    "Decorated, with docstring. ... skipped 'decorated'\n"
    "test_doc_ok (t.T.test_doc_ok)\n"
    "A docstring that passes. ... ok\n"
    "test_doc_skip (t.T.test_doc_skip)\n"
    "First line of the docstring. ... skipped 'doc reason'\n"
    "test_plain_ok (t.T.test_plain_ok) ... ok\n"
    "test_plain_skip (t.T.test_plain_skip) ... skipped 'plain reason'\n"
    "\n----------------------------------------------------------------------\n"
    "Ran 6 tests in 0.000s\n\nOK (skipped=4)\n")
_cls, _n, _ids = _classify(_sp.CompletedProcess(["x"], 0, "", _verbose), 0.1)
_want = ["test_decorated_plain", "test_decorated_skip", "test_doc_skip",
         "test_plain_skip"]
check("S11-skips-docstring", _classify.last_skipped == _want
      and (_cls, _n, _ids) == (ZERO_DISCRIMINATION, 6, []),
      f"skipped={_classify.last_skipped}  ({_cls}, {_n} tests)")

AFTER = working_tree_hashes(WATCHED)
unchanged = BEFORE == AFTER
print(f"\n{'PASS' if unchanged else 'FAIL'}  the original working tree is hash-identical throughout")
for p in WATCHED:
    if BEFORE[p] != AFTER[p]:
        print("      CHANGED:", p)
print("\nHARNESS ACCEPTANCE:", "PASS" if (ok and unchanged) else "FAIL")
