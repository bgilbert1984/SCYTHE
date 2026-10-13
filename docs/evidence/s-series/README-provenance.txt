§5.21 S-series -- thirty-three controls over the catalogue analysis:
twenty-one over the slope analysis and entry 10 (runs 1 and 2), twelve over
entry 17's reference-comb match (runs 3 and 4).

controls_521.py is the controls module; run_sweep_521.py the runner (BUILD
S/w521, expected= the checkpoint's count); verify_s_series.py the per-control
witness verifier that purges __pycache__ around apply and restore, because a
same-size mutation inside one second is invisible to the bytecode cache.  All
four sweeps were produced on the authoring host from
~/SCYTHE-sweep/521-s-run{1,2,3,4}-<sha>/ with SCYTHE_REPO pointing at the
authoring checkout and SCYTHE_BRANCH= the branch under test (feat/5.21-controls
for runs 1-2, feat/5.21-controls-s22 for runs 3-4); each launch.sh is the exact
environment.  The harness that produced all four tables is byte-identical to
docs/evidence/harness-bundle-30cecd07/ (sweep.py 754148d7, review_sweep.py
1ae0cdeb, selftest.py a99cb17c, controls527.py fee9570e), launched unmodified
with BUILD derived from SCYTHE_SCRATCH.

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

RUN 3 at edf2c41a -- NOT certified.  sweep-run3-edf2c41a/
  2026-09-28 06:36 to 07:36 +0530.  edf2c41a is main 39da055e (#121, entry
  17) plus S22..S35 -- fourteen controls over the reference-comb match, S28,
  S33 and S34 declared broad before launch -- and one witness, the
  call-counting test for the catalogue's match loop.  35/35 TEST_FAILURE,
  zeros none, not measured none, collection uniform at 2422, five broad rows
  all declared with their witnesses present, completeness 35/35.  Refused on
  two flags.  DUPLICATE SETS: S33 and S34 (each drops one of entry 17's
  derived keys from the reconstruction authority list) failed exactly S18's
  set of 196 tests.  Every rebuild in the suite refuses with
  RECONSTRUCTION_FIELD_NO_AUTHORITY whichever key is missing, so the three are
  one regime measured three times, and the reviewer refuses equal sets under
  any allowance.  NO UNIQUE WITNESS: S25 (the comb match is not applied)
  shared both of its failing tests -- the boundary test with S27, because it
  also asserted the at-the-window case S27 moves, and the catalogue test with
  S29.  Kept as the failure record it is; no row of it was reused.

RUN 4 at 21de3d9c -- certified.  sweep-run4-21de3d9c/
  2026-09-28 07:42 to 08:40 +0530.  21de3d9c is edf2c41a plus one commit:
  S33 and S34 RETIRED (EARNED_IDS excludes them and the inventory gate refuses
  them if they reappear; S18 stays as the measurement of the derived-key
  regime), and the comb-boundary witness split so that the at-the-window case
  is its own test (test_a_reference_entry_at_the_window_is_on_the_comb).  No
  production file changed between the runs; the diff is the controls module,
  the one test, the gates (2422 -> 2423), and run 3's archive.

  VERDICT: every control discriminated.  33/33 TEST_FAILURE, zeros none,
  duplicate sets none, not measured none, collection uniform at 2423, S1
  n=378, S18 n=196 and S28 n=376 declared broad with their named direct
  witnesses present, 30/30 narrow controls with a unique witness, suppression
  none, completeness 33/33 (controls_521), working tree hash-identical
  throughout.  review_521_run4.txt is review_sweep.py's own output over
  sweep-521.jsonl.

  Per-row breadth, runs 3 and 4 identical for every surviving control: S1
  378, S2 2, S3 2, S9 2, S12 3, S13 2, S18 196, S24 2, S25 2, S27 2, S28 376,
  S29 3, every other control exactly 1.  S1 and S18 are two wider than at
  94e7840b (370, 194) because the entry-17 witnesses are in the suite; S9 is
  one wider because the slope-before-harmonic test is.

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
  S22-S27  entry 17: the harmonic is required on the reference class and
           refused on every other, the cap is applied, the comb match is
           applied, the window grows with the harmonic, at the window is
           inside it
  S28      the RF position is the anchor plus the intercept (broad)
  S29-S32  the catalogue matches every entry against its comb (witnessed by
           counting the calls, because every refusal the match can raise is
           also raised by calling it directly), and the plan refuses a
           disagreeing comb, a disagreeing anchor and an undeclared anchor
  S35      an anchor is a positive finite frequency
  S33, S34 RETIRED after run 3: duplicates of S18, see above.
  S13, S14, S15 and S19 were subsumed by S20 until the default fixture deltas
  were made zero-mean; S20's witness stands on skewed deltas, S14's on slope
  0.05 at a 20.5 MHz excursion.  That is why the fixture looks the way it does.

WHICH TREE THIS CERTIFIES
  Runs 1-2: rf_promotion_envelope.py and rf_corpus_reconstruction.py as merged
  to main by #118 (gates 2392 by #119, main f4a5e3d6 at branch time); the
  branch added seven witnesses (2392 -> 2399), production modules
  byte-identical to main; merged as #120 (007f8e5d).
  Runs 3-4: the same two files as merged to main by #121 (39da055e, entry 17,
  2421).  The branch adds two witnesses (2421 -> 2423) and the controls;
  the production modules are byte-identical to main.  A squash merge of this
  branch gives main the tree of 21de3d9c plus this evidence, so the
  certification carries.

LAUNCH GATES, from sweep-521.out
  run 2: branch 'feat/5.21-controls' resolved to 94e7840b; inventory 21,
  mutation audit 21 no problems, BASELINE ZERO_DISCRIMINATION 2399 tests exit
  0 100.7s accepted at expected=2399, bound 1800s (floor), slowest row 101.5s.
  run 4: branch 'feat/5.21-controls-s22' resolved through the local branch to
  21de3d9c; inventory 33, mutation audit 33 no problems, BASELINE
  ZERO_DISCRIMINATION 2423 tests exit 0 100.3s accepted at expected=2423,
  bound 1800s (floor; 10 x 100.3s is below it), slowest row 104.3s.

SKIP COLUMN
  Every row, all four runs, records the same two skips
  (test_a_non_crossing_says_so_in_the_same_vocabulary,
  test_the_real_preflight_passes_where_the_pinned_tree_exists); no control
  added or removed one.

HARNESS ACCEPTANCE ON THIS HOST
  selftest-521-run{1,2,3,4}.out: selftest.py from the bundle, run before each
  launch against the authoring checkout's HEAD (98b2acbb): 25 cases, 0
  failures, HARNESS ACCEPTANCE: PASS, all four times.  Runs 3 and 4 were
  launched by selftest-then-launch.sh, which starts the sweep only on PASS.

INDEPENDENTLY VERIFIED before this commit
  run 2 sweep-521.jsonl sha256 81ee33a5cbba02ff9fcc1e1c3647cca507c79142950c5cf009908167ee4840eb;
  run 4 sweep-521.jsonl sha256 4124eaf05507967383e8df5225515be9b19fc5d05d2bb70fb1424b7bb927090f;
  22 and 34 rows, one checkpoint each, controls_module controls_521 in the
  baseline row
  controls_521-as-run.py and run_sweep_521.py in each run directory are
  byte-identical to the files committed at that run's checkpoint
  scratch worktrees removed by the runner; the build worktree is clean at
  each checkpoint
