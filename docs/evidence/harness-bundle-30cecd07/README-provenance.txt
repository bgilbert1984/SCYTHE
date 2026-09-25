This bundle POSTDATES the run-2 table in docs/evidence/3c-run2-30cecd07/.

That table was produced by the harness in that directory's harness-as-run/. This
one differs in review_sweep.py and both runners: a run now records its own
controls module so a reviewer cannot be pointed at the wrong slice's controls.
The amendment changes no verdict -- run 2's jsonl reviews identically under both
-- but the bytes differ, and evidence is read against the harness that produced
it, never against a later one.

The files are committed unedited so SHA256SUMS, these bytes and selftest.out
correspond. Two host-local paths therefore survive in the runners; MANIFEST.txt
names them.
