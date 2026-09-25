The §5.26 sweep harness, as merged from both hosts.

WHAT THIS IS NOT. This is not the harness that produced the certification
tables at a1cffd8. Those ran BEFORE this merge, and their provenance is in
docs/evidence/5.26-journal-sweep-a1cffd8/ (the record, and the 254-line fold
patch that was applied afterwards). Use this bundle for runs at or after
5797b18f; do not attribute the a1cffd8 tables to it.

WHY IT IS IN THE REPOSITORY. The harness deliberately lived outside the repo
for the whole slice. It is committed here because two hosts diverged into two
harness generations without either noticing: a stale bundle ran against a newer
checkpoint, its launch gate accepted the mismatch because it was a floor rather
than an equality, and the resulting table looked healthy while carrying
pre-repair controls. Two of its rows differed for that reason alone. A results
file is uninterpretable without the harness generation that produced it, so the
two now travel together.

SHA256SUMS covers the six harness files and MANIFEST.txt. selftest.out is the
HARNESS ACCEPTANCE: PASS for exactly these bytes -- 24 cases, 0 failures. The
files are committed unedited so that correspondence holds; two host-local
details are therefore left as they are rather than cleaned up:

  run_sweep_journal.py  BUILD = Path(S) / "build3br"
      the worktree name this host holds for the slice branch. Set it to
      whatever the running host uses.

  run_sweep_journal.py  S = os.environ.get("SCYTHE_SCRATCH", "<a session path>")
      the default is one machine's scratch directory. Set SCYTHE_SCRATCH.

MANIFEST.txt carries the expected test count, the declared broad controls, what
was folded from where, and the one limitation that remains declared rather than
fixed.
