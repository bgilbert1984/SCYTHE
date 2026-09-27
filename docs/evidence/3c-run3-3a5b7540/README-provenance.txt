§5.20 3c-wire, run 3 at 3a5b7540 -- the table that certifies the merged tree.

Produced on this host (the authoring host), 2026-09-26 10:33 to 11:14 +0530,
from ~/SCYTHE-sweep/3c-k-run3-3a5b7540/ with SCYTHE_REPO pointing at the
authoring checkout; launch.sh is the exact environment. It postdates the merge
of #112: the table was not in the PR, and it was not committed with the merge.
This directory closes that gap.

VERDICT: every control discriminated. 26/26 TEST_FAILURE, zeros none,
duplicate sets none, not measured none, collection uniform at 2322, K2 n=42 and
K6 n=39 both declared broad with their named direct witnesses present, K9
subset K22 allowed with strict containment, 24/24 narrow controls with a unique
witness, suppression none, completeness 26/26 (controls_3c), working tree
hash-identical throughout. review-3c-3a5b7540.out is review_sweep.py's own
output over sweep-3c.jsonl, produced 2026-09-27 from harness-as-run/.

WHICH TREE THIS CERTIFIES
  3a5b7540 was the head of feat/5.20-3c-wire. #112 squash-merged it as
  624938d2, and `git diff 3a5b7540 624938d2` is empty: the whole tree is
  identical, so this table certifies the 3c-wire tree as it sits in main's
  history at 624938d2. The branch is deleted; the checkpoint hash in every row
  still names an object main's history reaches.

  It does NOT carry to 66a544bf (3d, #114). 3d reflowed `_record` -- the
  trailing comma K24's anchor ends on is gone -- and removed K24's witness,
  test_no_production_module_is_wired_to_the_publisher, because 3d wires the
  publisher. The pre-flight anchor audit against 66a544bf reports K24's anchor
  occurring 0 times. A K-series at or after 3d needs K24 retired, and K25's
  premise reconsidered, before it can launch. controls_journal audits clean at
  66a544bf.

harness-as-run/ is the harness THAT PRODUCED THIS TABLE, with its own
SHA256SUMS. Every file in it is byte-identical to
docs/evidence/harness-bundle-30cecd07/ at 3a5b7540, at 624938d2 and at
66a544bf -- the bundle #112 carried, with the skip-column repair -- and the
runner was launched unmodified: BUILD is derived from SCYTHE_SCRATCH, which
launch.sh sets. It is kept beside the results all the same, because the bundle
directory is for runs after this one and will move when K24 is retired;
evidence is read against the harness that produced it.

LAUNCH GATES, from sweep-3c.out
  inventory 26 controls, mutation audit 26 controls no problems,
  BASELINE ZERO_DISCRIMINATION 2322 tests exit 0 89.1s -- accepted at
  expected=2322 -- bound 1800s (floor; 10 x 89.1s is below it), slowest row
  91.7s.

SKIP COLUMN
  The BASELINE row records two skips: the symbol-clock geometry skip every
  earlier table lists, and test_the_real_preflight_passes_where_the_pinned_tree_exists,
  a docstring test that skips on a host without the pinned observation tree.
  This is the first table produced with the repaired pattern (3a5b7540 itself),
  so it is the first whose skip column can see the second one. Earlier tables'
  single skip is one the old pattern could see, not the suite's count.

HARNESS ACCEPTANCE ON THIS HOST
  selftest-3c-run3.out: selftest.py from harness-as-run/, run here before
  launch against the authoring checkout's HEAD at the time (98b2acbb): 25
  cases, 0 failures, HARNESS ACCEPTANCE: PASS. It is a second acceptance of the
  same bytes harness-as-run/selftest.out accepted at 6aeee056.

INDEPENDENTLY VERIFIED before this commit
  sweep-3c.jsonl sha256 0d1c3870ea006786; 27 rows, one checkpoint,
  controls_module controls_3c recorded in the baseline row
  harness-as-run/ byte-identical to the committed bundle at all three
  checkpoints named above, file by file
  K24 and K25 killed by one witness each: K24 the prohibition test alone,
  K25 the breadth test alone, as at run 2
