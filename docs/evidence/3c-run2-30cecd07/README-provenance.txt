§5.20 3c-core, run 2 at 30cecd07 -- the table that certifies.

Produced on the dedirock host, not here. VERDICT: every control discriminated.
26/26 TEST_FAILURE, zeros none, duplicate sets none, collection uniform at 2254,
K2 n=42 and K6 n=39 both declared broad with their named direct witnesses
present, K9 subset K22 allowed with strict containment, suppression none,
completeness 26/26, working tree hash-identical throughout.

harness-as-run/ is the harness THAT PRODUCED THIS TABLE, as received, with its
own SHA256SUMS. It is kept beside the results because a results file cannot be
interpreted without the harness generation behind it --- a lesson from two hosts
running two generations at one checkpoint and neither noticing.

Do NOT read this table against docs/evidence/harness-bundle-30cecd07/. That
bundle POSTDATES this run: its review_sweep.py and runners were amended
afterwards so a run records its own controls module. The amendment changes no
verdict --- this table reviews identically under both --- but the bytes differ,
and the bundle is for runs after this one.

INDEPENDENTLY VERIFIED on the authoring host before this commit:
  the commit is a fast-forward from 64f5837 and touches one test file only
  focused module 61 tests, full suite 2254 green, one pre-existing skip
  jsonl sha256 4a48281d0c084744, controls_3c.py ed3f1859347d329d,
    controls-K25.patch 8ff9b646e09fe599 -- all three as quoted in HANDOFF.txt
  sweep.py, controls527.py, review_sweep.py and selftest.py byte-identical to
    the authoring host's copies, so the verdict is not a reviewer artifact
  K24 and K25 re-applied here from their controls: K24 fails the prohibition
    test alone, K25 the breadth test alone -- the claim run 1 falsified

WHAT RUN 1 FOUND, AND WHY IT IS NOT HERE
  Run 1 at 64f5837 did not certify: K24 and K25 were an exact duplicate pair,
  both killed by the prohibition test and nothing else. The cause was in the
  breadth test, which computed its own module count independently of the
  prohibition's scan, so no mutation could reach it. 30cecd07 couples both tests
  to one derived scope; K25 was amended to narrow that scope while a second
  module wires the publisher. Run 1 is archived on the authoring host under its
  own harness and is a valid failure record for 64f5837 alone.

K25 PATCHES A TEST MODULE, which is new in this harness. The row is evidence
that the two boundary tests are COUPLED -- not evidence about
rf_capture_publication. It is sound and not circular, because the two tests read
one helper and assert different things about it, but it should not be cited as a
production-behaviour control.
