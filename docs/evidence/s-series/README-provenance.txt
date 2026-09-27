§5.21 S-series -- twenty-one controls over the catalogue analysis.

controls_521.py is the controls module; run_sweep_521.py the runner (BUILD
S/w521, expected=2399); verify_s_series.py the per-control witness verifier
that purges __pycache__ around apply and restore, because a same-size mutation
inside one second is invisible to the bytecode cache.  Both sweeps were
produced on the authoring host from ~/SCYTHE-sweep/521-s-run{1,2}-<sha>/ with
SCYTHE_REPO pointing at the authoring checkout and SCYTHE_BRANCH=
feat/5.21-controls; each launch.sh is the exact environment.  The harness that
produced both tables is byte-identical to docs/evidence/harness-bundle-30cecd07/
(sweep.py 754148d7, review_sweep.py 1ae0cdeb, selftest.py a99cb17c,
controls527.py fee9570e), launched unmodified with BUILD derived from
SCYTHE_SCRATCH.

RUN 1 at f9359ee3 -- NOT certified.  sweep-run1-f9359ee3/
  2026-09-28 00:50 to 01:27 +0530.  21/21 TEST_FAILURE, zeros none, duplicate
  sets none, collection uniform at 2399, 19/19 narrow controls with a unique
  witness, completeness 21/21.  Refused on one flag: S18 (the reconstruction
  authority list stops naming residuals_hz) measured n=194, over the BROAD=40
  tripwire, and only S1 had been declared broad.  The breadth is real and was
  predictable from the module: every plan in the suite carries a catalogue,
  every entry carries its slope record, and a derived key the authority list no
  longer names refuses every rebuilt plan.  It was not predicted, so the run is
  kept as the failure record it is.  Nothing else in the table was in doubt,
  and no row of it was reused.

RUN 2 at 94e7840b -- certified.  sweep-run2-94e7840b/
  2026-09-28 01:30 to 02:06 +0530.  94e7840b is f9359ee3 plus one commit:
  S18 declared broad in BROAD_DECLARED with the direct witness
  test_the_plan_rebuilds_to_the_exact_nominal_object.  No control, anchor,
  production file or test changed between the runs; the declaration is the
  whole diff (git diff f9359ee3 94e7840b -- controls_521.py).

  VERDICT: every control discriminated.  21/21 TEST_FAILURE, zeros none,
  duplicate sets none, not measured none, collection uniform at 2399, S1
  n=370 and S18 n=194 both declared broad with their named direct witnesses
  present, 19/19 narrow controls with a unique witness, suppression none,
  completeness 21/21 (controls_521), working tree hash-identical throughout.
  review_521_run2.txt is review_sweep.py's own output over sweep-521.jsonl.

  Per-row breadth, both runs identical row for row: S1 370, S2 2, S3 2,
  S12 3, S13 2, S18 194, every other control exactly 1 -- the witness the
  module names and nothing else.

WHAT THE SERIES MEASURES
  S1-S5    the frozen tolerance, its width, the |s|<=3 family bound, the
           three-distinct-deltas floor and the declared-delta membership
  S6-S7    baseband folding and the residual record
  S8-S11   the class support table (mixing rejects -1, reference is exactly
           -1, unresolved is exactly no match) and tuning == persistence
  S12-S17  the thermal-is-spur-free check, the whole span not the usable
           half, the tolerance margin over the excursion, prediction by the
           matched integer, the spur-window eligibility and the no-catalogue
           refusal
  S18      reconstruction re-derives the slope record and reads none of it
  S19-S21  the fit uses every observation, the intercept is a fit not a mean,
           and the anchor lookup for an undeclared tuning
  S13, S14, S15 and S19 were subsumed by S20 until the default fixture deltas
  were made zero-mean; S20's witness stands on skewed deltas, S14's on slope
  0.05 at a 20.5 MHz excursion.  That is why the fixture looks the way it does.

WHICH TREE THIS CERTIFIES
  The controlled files are rf_promotion_envelope.py and
  rf_corpus_reconstruction.py as merged to main by #118 (gates moved to 2392
  by #119, main f4a5e3d6 at branch time).  The branch adds the seven S-series
  witnesses to test_rf_promotion_envelope.py (2392 -> 2399) and moves both
  gates in the same commit; the production modules are byte-identical to main.
  A squash merge of this branch gives main the tree of 94e7840b plus this
  evidence, so the certification carries.

LAUNCH GATES, from sweep-521.out (run 2)
  branch 'feat/5.21-controls' resolved through 'feat/5.21-controls' to
  94e7840b; inventory 21 controls, mutation audit 21 controls no problems,
  BASELINE ZERO_DISCRIMINATION 2399 tests exit 0 100.7s -- accepted at
  expected=2399 -- bound 1800s (floor; 10 x 100.7s is below it), slowest row
  101.5s.

SKIP COLUMN
  Every row, both runs, records the same two skips
  (test_a_non_crossing_says_so_in_the_same_vocabulary,
  test_the_real_preflight_passes_where_the_pinned_tree_exists); no control
  added or removed one.

HARNESS ACCEPTANCE ON THIS HOST
  selftest-521-run{1,2}.out: selftest.py from the bundle, run before each
  launch against the authoring checkout's HEAD (98b2acbb): 25 cases, 0
  failures, HARNESS ACCEPTANCE: PASS, both times.

INDEPENDENTLY VERIFIED before this commit
  run 2 sweep-521.jsonl sha256 81ee33a5cbba02ff9fcc1e1c3647cca507c79142950c5cf009908167ee4840eb;
  22 rows, one checkpoint, controls_module controls_521 in the baseline row
  controls_521-as-run.py in each run directory is byte-identical to the
  controls_521.py committed at that run's checkpoint
  scratch worktrees removed by the runner; the build worktree is clean at
  94e7840b
