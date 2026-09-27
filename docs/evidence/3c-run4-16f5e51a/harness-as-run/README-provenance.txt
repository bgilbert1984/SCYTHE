This bundle POSTDATES the run-2 table in docs/evidence/3c-run2-30cecd07/.

That table was produced by the harness in that directory's harness-as-run/. This
one differs in review_sweep.py and both runners: a run now records its own
controls module so a reviewer cannot be pointed at the wrong slice's controls.
It also differs in sweep.py and selftest.py: the skip pattern now reads the
two-line form `unittest -v` uses for a test with a docstring, and S11 measures
it. Neither amendment changes a verdict -- run 2's jsonl reviews identically
under both, and the count gate never read the skip column -- but the bytes
differ, and evidence is read against the harness that produced it, never
against a later one.

It differs again in controls_3c.py and both runners, for runs at or after 3d:
K24 is retired, K27 carries its mutation under the inverted boundary, K25
narrows to the sanctioned site, and both gates assert 2370. MANIFEST.txt has
the measurements. Nothing here changes a verdict already recorded: the tables
at 30cecd07 and 3a5b7540 were produced by the controls they name.

The files are committed unedited so SHA256SUMS, these bytes and selftest.out
correspond. Two host-local paths therefore survive in the runners; MANIFEST.txt
names them.
