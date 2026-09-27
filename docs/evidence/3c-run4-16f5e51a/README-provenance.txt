§5.20 K-series, run 4 at 16f5e51a -- the first table at main after 3d.

Produced on the authoring host, 2026-09-27 22:04 to 22:52 +0530, from
~/SCYTHE-sweep/3c-k-run4-16f5e51a/ with SCYTHE_REPO pointing at the authoring
checkout; launch.sh is the exact environment, SCYTHE_BRANCH=main. Run 3 at
3a5b7540 certified the 3c-wire tree; this run certifies the tree after 3d
(#114), with the controls #116 revised for it: K24 retired, K27 in its place
under the inverted boundary, K25 narrowed to the sanctioned site.

VERDICT: every control discriminated. 26/26 TEST_FAILURE, zeros none,
duplicate sets none, not measured none, collection uniform at 2370, K2 n=66
and K6 n=63 both declared broad with their named direct witnesses present, K9
subset K22 allowed with strict containment, 24/24 narrow controls with a
unique witness, suppression none, completeness 26/26 (controls_3c), working
tree hash-identical throughout. review-3c-16f5e51a.out is review_sweep.py's
own output over sweep-3c.jsonl.

WHAT THIS RUN MEASURED FOR THE FIRST TIME
  K27 -> test_only_the_namespace_wires_the_publisher, nothing else.
  K25 -> test_the_boundary_scan_reads_every_production_module, nothing else.
  Each is the witness #116 predicted from the boundary module alone, now
  measured against the full suite: one witness per control, no overlap, so
  the retirement of K24 cost the series nothing it could still measure.

  K2 and K6 are broader than at 3a5b7540 (42 and 39 there). 3d wired the
  publisher into the live capture path, so the tests downstream of a broken
  publish or a misdirected readback now include admission's and the
  namespace's. Both were declared broad before launch with a named direct
  witness, and both witnesses are present; BROAD stays an absolute tripwire
  and was not moved.

WHICH TREE THIS CERTIFIES
  16f5e51a is main at launch: 66a544bf (3d) plus two evidence-only merges,
  #115 and #116. The controlled files are those of 66a544bf; nothing under
  docs/ is collected by the suite. The BASELINE row records the checkpoint
  and every control row carries it.

LAUNCH GATES, from sweep-3c.out
  branch 'main' resolved through 'main' to 16f5e51a (the authoring checkout's
  local main was fast-forwarded to origin/main before launch; stale, it
  resolved to 8b768ca4 and would have measured the wrong tree -- the
  resolution line exists so that is visible, and it was read).
  inventory 26 controls, mutation audit 26 controls no problems,
  BASELINE ZERO_DISCRIMINATION 2370 tests exit 0 99.6s -- accepted at
  expected=2370 -- bound 1800s (floor; 10 x 99.6s is below it), slowest row
  102.8s.

SKIP COLUMN
  The BASELINE row records two skips, as run 3 did; no control added one.

harness-as-run/ is the harness THAT PRODUCED THIS TABLE, with its own
SHA256SUMS: byte-identical to docs/evidence/harness-bundle-30cecd07/ at
16f5e51a, launched unmodified with BUILD derived from SCYTHE_SCRATCH. It is
kept beside the results all the same, because the bundle directory is for runs
after this one; evidence is read against the harness that produced it.

HARNESS ACCEPTANCE ON THIS HOST
  selftest-3c-run4.out: selftest.py from harness-as-run/, run here before
  launch against the authoring checkout's HEAD (98b2acbb): 25 cases, 0
  failures, HARNESS ACCEPTANCE: PASS. The shared modules are the bytes
  harness-as-run/selftest.out accepted at 6aeee056.

INDEPENDENTLY VERIFIED before this commit
  sweep-3c.jsonl sha256 4704a72f11263674; 27 rows, one checkpoint,
  controls_module controls_3c recorded in the baseline row
  harness-as-run/ verifies against its SHA256SUMS and against the bundle
  committed at 16f5e51a
  scratch worktrees removed by the runner; the build worktree is clean
