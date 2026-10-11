# The Corpus Learned to Refuse: Compelled Admission and the Reference Comb

**Date:** September 27, 2026
**Author:** SCYTHE Core Engineering Team
**Category:** RF Capture, Evidence-Centered Computing, §5.20, §5.21

---

Four entries drained this week — 14, 15, 10, and 17 — and they rhyme. In §5.20, the corpus learned
that admission is the only path to membership — not a check a caller can route
around, but a construction no caller can reach past. In §5.21, the catalogue
learned what a spur is allowed to mean: a measured slope, a declared harmonic,
a reference comb that governs the reference class. The memorable part is not
that SCYTHE captures RF. The memorable part is that SCYTHE now decides —
deterministically, and with a proof for each decision — what it means for a
capture to count, and what it means for a spur to be one.

## Entry 14: Admission Is the Only Path to Membership

Entry 14 asked for compelled admission, and §5.20 3d built it in four pieces,
merged as PR #114. Each piece was verified by the full curated suite before
the next began, and the last piece was proven by a mutation control set swept
in the formal harness. The final state: `compelled_path_to_membership: True`.

**Piece 1 — the sequence has somewhere durable to stand.** `CapturedStratumSequence`
was process-local: a reopened corpus started every stratum at zero, which is
continuity by amnesia. `reconstruct_stratum_sequence` rebuilds a stratum's
sequence from its reconciled membership — verified history, oldest first. It
restores; it does not advance. And the sequence is bound to a ring lifetime
id, because a sample index means nothing across two rings: a history from
another lifetime is refused at reconstruction, never adopted as continuity,
and `_admit` refuses a sequence whose lifetime does not match the window's
attestation. Two refusals carry this — `ADMISSION_RING_LIFETIME_MISMATCH` and
`ADMISSION_SEQUENCE_HISTORY_REFUSED` — both in the precondition regime, so a
refusal leaves the creator uncalled. Suite green at 2337 tests.

**Piece 2 — reopen reconciles.** The membership journal's core had said the
six final-dependent recovery classifications "remain in slice 3d." They are
built now, in `rf_membership_recovery.py`, one per window, from its journal
terminal state and the outcome of verifying its expected final:
`SETTLED_MEMBER`, `ADOPTED`, `DISCARDED_UNWRITTEN`, `DISCARDED_UNVERIFIED`,
`SETTLED_ABANDON`, `UNACCOUNTED_FINAL`. The pure `_classify` core takes no
descriptor and reads no file; the impure path establishes the outcome and
performs the action. The entry-14 hole — a verified final under an open
intent, the crash between step 8 and the COMMIT — classifies as ADOPTED and
appends the COMMIT, making durable a membership that was already true. A
member no intent records, and a stray non-member entry, refuse before any
terminal is written, so a directory holding something unaccountable leaves the
journal untouched. Nine `RecoveryRefused` codes, this module's own regime.
Suite green at 2364 tests.

**Piece 3 — the publisher is wired.** Creation moved inside the namespace.
`_record` admits, frames the header, computes `file_sha256` from the live
scope before the file exists — the identity the intent binds and step 8
recomputes — and calls `corpus.commit_window`. The commit brackets the work:
append INTENT first (reserving the terminal, so any later crash is a state
recovery reconciles), create the temporary inside the corpus directory, write
it, run steps 5–8, append COMMIT on a verified final or ABANDON on a
publication failure. The directory descriptor never leaves the namespace.
Temporaries are dot-prefixed siblings carrying their own digest —
`.partial-<file_sha256>.iqc` — so a lister sees they are not members and
recovery can tie each one to exactly one intent: an orphan under a discarded
intent is removed, a redundant leftover beside a member is removed, and a
partial no intent accounts for is still a stray. The boundary test asserts the
publisher is wired to exactly one production module. Suite green at 2367.

**Piece 4 — compulsion, then the proof.** First the sequence was taken away
from the caller: `record_gain_step` no longer accepts one, so nothing a
caller hands in can bypass the cap or forge a chain. The scope mints the
sequence on the first verified commit, pins the corpus to a single ring
lifetime, and advances the count after the COMMIT. Then `commit_window`
stopped being a public method callable with loose data. The one thing that
stands for a window now is a `WindowAdmission` — a capability minted by
admission after every precondition passed, constructible nowhere else, refused
without the module-private key (`ADMISSION_TICKET_UNCONSTRUCTIBLE`). A commit
cannot be reached without admitting, by construction, not by a check a caller
could route around.

Then the D-series proved it: seven controls, D1–D7, each a mutation that opens
one side route to membership — a forged ticket, a commit window that admits
anything, a removed cap, a window from a dead ring, an adopted member no
intent records, a hand-constructed verified final, a COMMIT the disk does not
support — each killed by a test already standing in the suite. The formal
harness sweep ran the whole suite against every mutant: baseline
ZERO_DISCRIMINATION, 2370 tests, all seven `TEST_FAILURE`, working tree
hash-identical throughout, reviewer verdict COMPLETENESS 7/7. No side route to
membership survives its witness.

With the proof in hand, `namespace_status` reports
`compelled_path_to_membership: True` — and `section_implemented` stays
False, because production creation is a separate authorisation, still
withheld. Entry 14 is drained. The four pieces are in: durable sequence,
recovery reconciled into reopen, the publisher wired, compulsion proven.

The K-series came along: K24 retired, K27 carrying its mutation under 3d's
boundary, and run 4 producing the first kill table at main after 3d.

## Entry 10 and Entry 17: The Catalogue Learns What a Spur Means

§5.21 turned the same discipline on the spur catalogue. Two entries fell to the
catalogue analysis in #118. Entry 15 was the tolerance: `SpurSlopeEstimate`
fits a line to a product's signed baseband offset observed at the LO settings
the schedule visits — the tuning's centre plus each declared retune delta —
over at least three distinct retunes. The observations are held; the measured
slope, the intercept, and every residual are derived from them and recorded;
and §5.22's frozen `PLAN_SLOPE_TOLERANCE` decides, in exactly one place,
whether the slope is an integer member of the affine mixing family bounded by
`PLAN_MAX_MIXING_SLOPE`. Two retunes refuse. An undeclared delta refuses. A
feature that moved between two visits to one setting shows as residual rather
than being fitted through. `CataloguedSpur` carries one slope and refuses a
classification the slope does not support — a slope matching nothing cannot
be `CONSISTENT_WITH_INTERNAL_MIXING`, a slope matching −1 can be the
reference class and nothing usable else. The tolerance governs something now,
in exactly one place, and the queue no longer lists the entry.

Entry 10 was the thermal-versus-spur overlap, and it fell to the in-span
check. `catalogued_spurs_in_span` says which products the retune model puts
inside the analysis span at an LO, and `CapturePlanDeclaration` refuses a
captured `THERMAL_NO_INPUT` window at any visit where that is non-empty — and
a `RECEIVER_SPURS` window at any visit where the eligible units name no
product. A slope-0 product is in span everywhere, so a receiver with one
cannot capture a thermal window anywhere: entry 10's third defect as a
refusal before the corpus. The check binds when the stratum is captured,
which is the trigger the entry named.

Entry 17 fell to the reference comb. A slope record now carries its anchor
tuning's centre, so every catalogued product has a derived RF position — the
anchor plus the fitted intercept. A reference-class entry declares the
harmonic it claims, and any other class that declares one is refused. The
catalogue declares a `ReferenceComb` holding the reference and its ppm, and
refuses every reference-class entry that sits above the harmonic cap or
outside the window `n · f_ref · ppm`, before anything reads the class off it.
The plan refuses a catalogue whose comb disagrees with its own declared
reference, and any entry anchored at a tuning it does not declare or at a
centre it declares differently. Reconstruction re-derives the cap and the RF
position and reads neither.

Two consequences are worth knowing. With the fixture's 28.8 MHz reference at
1 ppm and the UHF bands, exactly one generated tuning holds a comb harmonic
in its span — so reference entries anchor there. At 100 ppm the cap is 3 and
no reference class can exist above 86.4 MHz, so that fixture holds the other
three classes — which is the outcome §5.22 predicts. And a slope-minus-one
feature that persists terminated and matches no harmonic is now catalogued as
nothing, since it fits a modelled slope and matches no comb. The protocol
treats it as an ingress finding about the site.

The S-series certified the catalogue analysis at #120 — 21 controls over
everything #118 landed, each undone and killed by a named witness, swept
formally at 94e7840b: 2399 tests, 21/21 killed. At the entry 17 tree its
anchors still audit clean and its verifier still kills all 21, but the new
reference-comb checks sit outside its coverage; they await S22 onward.
PR #121 merged at 2421 tests, zero failures, two host skips, gates
re-summed in the same commit.

The live-catalogue protocol is written up for review at
`docs/RF_SPUR_CATALOGUE_PROTOCOL.md`: the five bounds, the operator
declarations and what software can check about each, the seven steps as acts
the ring can see, the refusal table exactly as implemented, the artefact
layout, what the run cannot establish, and what would have to exist first. It
is proposed, not accepted — its status block says it authorises no device
contact. A catalogue runner and feature retention do not exist. Writing
S22 onward and sweeping them at main is separate work, as is the live run
itself.

## What This Week Established

A corpus whose membership admits no side route, proven by mutation. A
catalogue whose every spur carries a measured slope and a derived position,
whose reference class answers to a declared comb. Four entries drained with
their proofs attached — the D-series sweep evidence under
`docs/evidence/d-series/`, the S-series certification at #120 — and the
refusal tables written exactly as implemented.

What remains is honest work, listed in the open: S22 and the sweep at main,
the catalogue runner, the live run, and production creation, still withheld
by separate authorisation. The instrument knows what it can claim. That is
the whole point.
