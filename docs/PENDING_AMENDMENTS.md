# Pending Amendments

```text
Status:     NOT NORMATIVE — nothing here is in force
Authority:  NONE. This file records candidate changes; it does not make them.
Opens:      Nothing. Holding an amendment here opens no accepted document.
```

`SCYTHE_VERDICT_VOCABULARIES.md` §7 says an accepted contract acquires changes
when it next opens for a reason of its own, never as a sweep of edits to add
references. That is the right convention, and it is **lossy exactly when it
works**: the longer a document stays sealed, the more good amendments accumulate
somewhere that is not the repository.

This file is where they wait. Without it, "it will keep" is a claim about
somebody's memory rather than about the repo — which is where the fifth
rediscovery would have lived if recovery's row had not been written down.

**Each entry names its trigger**, because an amendment with no trigger is a
wish. An entry leaves this file when it lands or when it is rejected; a rejected
entry is recorded as rejected, with the reason, rather than deleted.

## How this file stays honest

**Trigger: read this before adding an entry.** The check is the **drain rate,
not the contents.**

A queue with a few entries and named triggers is a working queue. The same file
carrying eleven entries, several of whose triggers have fired without the
entries landing, is a **second contract that nobody accepted** — non-normative
by its header and load-bearing in fact, sitting adjacent to accepted documents
that are silent about it.

So the question to ask is never *is this list long?* It is: **has any entry's
trigger fired?** An entry whose document opened and closed again without it is
the failure, whatever the total. Either it lands, or it is recorded as rejected
with the reason. It does not wait for a second trigger.

This check is written here rather than remembered, for the reason the file
exists at all.

---

**One entry was written here that did not belong.** Slice 4 minted
`RETRY_REQUIRES_OPERATOR` in code and queued the contract amendment behind it,
which inverts the proposal → acceptance → implementation order this file exists
to protect rather than to route around. It was caught in review and became
Amendment C, and no entry for it ever reached `main`.

*An amendment with no trigger is a wish.* An amendment whose trigger has already
fired in code is not a queue entry at all — it is a note apologising for one.
Entry 6 is the legitimate shape: the rule lives in the reader because the reader
had to enforce something to be written, and its trigger is a slice that has not
started.

---

## 1. `SCYTHE_VERDICT_VOCABULARIES.md` — the drafting rule

**Trigger:** the next amendment to that document for a reason of its own.

Where a module's contract has to relate its executability state to a merit rule
it resembles, **lead with the local commitment and mention the resemblance as a
warning against importing it** — never lead with the foreign rule and then
withhold it.

Leading with the foreign rule asks a reader to hold a relation before they have
a reason to care about it, and leaves them unsure whether the local document is
bound by it. Leading with the local commitment gives the cross-reference exactly
one job.

Found by rewriting `PROMOTION_EXECUTION_CONTRACT.md` §7, which had it the wrong
way round. It generalizes: every module that adopts §1 will have a paragraph
shaped like that one, so the need recurs rather than expiring.

---

## 2. `RF_WALK_SURVEY_CONTRACT.md` §4 — conformance line

**Trigger:** whenever §4 next opens for a reason of its own.

Add a line declaring conformance to `SCYTHE_VERDICT_VOCABULARIES.md`. §4 is that
rule's first full statement, scoped to frames.

If §4 never opens again, admission conforms in fact and this entry expires
unlanded. That is the convention working, not a debt.

---

## 3. Recovery's contract — carry the `RESTART_NOT_OBSERVED` finding

**Trigger:** when a recovery contract exists.

`SCYTHE_VERDICT_VOCABULARIES.md` §7 records that `RESTART_NOT_OBSERVED` is in
merged code, is an executability code, sits among three merit verdicts, and is
declared as one nowhere. The finding is recorded there because recovery has no
contract to carry it. When one exists, it carries it, and the vocabularies
document's conformance table gets a normal row.

## 7. §17 slice 10 — the live source does not exist, and §17 still says it does

**Trigger:** the first persisted or supplied derived-evidence interface. Not a
date, and not "when convenient": the entry drains when something can produce
derived RF evidence without acquisition.

Slice 10 was authorized as live SHADOW observation. What landed is **slice 10a**,
an apparatus certification: production checkers — `check_walk_step` and its
contracts — driven over **constructed** inputs, because nothing in this
repository stores derived RF evidence and §13h I.3 forbids acquiring any.

The verdicts are genuine. A real `NUMERIC_BALANCE_EXCEEDED` fires once walk
displacement accumulates past its ceiling, which is the invariant apparatus
working rather than a fixture asserting that it does. **That does not make the
evidence live**, and the code now says so in three places rather than one: the
record carries `run_class: APPARATUS_CERTIFICATION`, a caller may demand
`require_live` and receive `DERIVED_EVIDENCE_UNAVAILABLE` instead of a
substitution, and a test asserts no derived-evidence source exists here.

**Amendment L (§13k) defines the producer, proposed 2026-09-12**, and its L.6
sequence is what this entry now waits on: the amendment, then slice 10c with no
live execution, then a separately authorized run, then one real artefact. The
entry drains at the artefact and at nothing earlier. A producer that has never
been run has produced nothing.

**Amendment J (§13i) defines the interface, proposed 2026-09-12.** It settles
the artefact class, makes `carries_samples` a finding rather than a claim, and
keeps the interface read-only so *no acquisition* stays untouched. It does not
drain this entry: an interface is not an artefact, and the entry waits on
evidence existing rather than on a way to read it.

**Two things are owed.**

1. A derived-evidence source: an artefact this repository persists or is
   supplied, readable without acquisition, from which real verdicts follow. Only
   then is `EVIDENCE_DERIVED_ARTEFACT` reachable and slice 10 completable.
2. §17 still reads *"10. Live SHADOW observation of the apparatus"*, which
   describes a stage that has not happened. It should record 10a as landed and
   10 as open, at the contract's next amendment for a reason of its own — the
   on-next-amendment convention, not a sweep.

**The fifth stage of the six-stage sequence is not complete**, and the thing most
likely to go wrong here is that it looks complete: there is a module, a record, a
green suite and a passing control. The entry exists because that appearance is
exactly what a queue is for.

---

## 9. `RF_Signal_Family_Classifier_Scope.md` §5.20 — publication step 1 asks for an attestation that does not exist

**Trigger:** when the persistence slice opens, or when §5.20 next opens for a
reason of its own — whichever is first. It must land before a captured byte is
written.

§5.20's publication protocol step 1 reads *"Hold the corpus ownership scope and
**verify the `IQWindow` against the live ring**."* No operation does that.

`BoundedIQRing.verify_window(window_id, digest)` takes **two strings**. It
proves this ring issued a window with that ID and that digest, under the current
epoch, and has not evicted it. It never sees an `IQWindow`, so a caller holding
a genuine pair can present a different object — different samples, different
metadata, a different interval — and verification returns `WINDOW_VERIFIED`.
Phase 3a demonstrates this rather than describing it
(`test_a_different_object_verifies_on_a_genuine_pair_of_strings`), and
`VERIFICATION_BINDS` / `VERIFICATION_DOES_NOT_BIND` say so in the module.

That is sufficient for its existing job — refusing a `source_window_hash` no
window ever carried. It is **not** sufficient at step 1, where the object's
bytes are about to become a file. The gap is exactly the one a two-string check
looks like it has already closed.

What §5.20 needs instead is an authoritative ring operation over the **exact
nominal object**: `type(x) is IQWindow`, every metadata field compared against
the issued record, the sample interval, and a digest **recomputed from the bytes
being published** rather than read off the object. Phase 3a exposes what that
operation will compare against — `first_sample_index`, `last_sample_index`,
`recorded_window()` — and deliberately does not build it, because it is capture
machinery and Phase 3a excludes capture.

Recorded here rather than corrected in place because §5.20 is accepted text.

**§5.24 carries the operation and was accepted 2026-09-16.** It finds that the
obvious shape — a function returning a verdict — reintroduces the hole, because
a verdict about a mutable object is a statement about the past; so attestation
mints a live scope that binds one immutable sample reference, and §5.20's typed
writers take that scope rather than an exact `IQWindow`. It also finds that
`samples.setflags(write=False)` is discouragement: an owning NumPy array can be
made writeable again, which was **run rather than assumed**.

**This entry stays open**, and acceptance does not narrow that. It drains when
five things land **together**: the code implementation, the immutable backing,
the opaque bound state, the measured peak-memory check and the static accessor
check. An attestation over an array whose write flag can be restored, or over a
payload reference a caller can replace, is the same defect one layer down; and a
memory claim inferred rather than measured is how the first draft of §5.24 came
to say something false. Neither a proposal nor an accepted contract satisfies
anything here — entry 8 records the first reading and entry 11 demonstrated the
second, twice.

---

## 10. `THERMAL_NO_INPUT` and `RECEIVER_SPURS` may be one population counted twice

**Trigger:** before **either** stratum is captured — not before whichever is
captured second. It is a property of the pair.

Nothing has ever checked that `THERMAL_NO_INPUT`'s tunings are free of the
receiver's own spurious products. An internal product whose baseband offset does
not move with the tuner — slope 0 in §5.21's terms, ``m = 1`` in the mixing
family — is in the analysis span at **every** tuning. If the receiver has one,
then a window captured as "terminated input, thermal noise only" contains it.

**The defect is not that Bonferroni breaks.** Bonferroni is valid under
arbitrary dependence, so two bounds over one population does not invalidate it.
The defects are three, and each is real on its own:

1. **Mislabelling.** `THERMAL_NO_INPUT` windows would carry a label that is
   false of their contents.
2. **Redundant alpha expenditure.** The family pays a thirteen-bound correction
   while two of the bounds test one population, so the procedure is more
   conservative than the coverage it actually buys — and every stratum's
   required n is larger for it.
3. **A population never covered.** The spur-free thermal case the stratum exists
   to test would not appear in the corpus at all, which is the one that cannot
   be repaired by re-labelling afterwards.

The check requires a spur catalogue, which §5.21 now governs — accepted
2026-09-14 — and which **does not yet exist**. Recorded separately from §5.21 because the obligation survives
that section being **rejected**: however spurs come to be identified,
`THERMAL_NO_INPUT` still has to be shown free of them, and that was true before
§5.21 was drafted.

---

## 14. Captured-window admission is named and not built

**Trigger:** before the first captured window is written to disk. Behind entry
9, which has to land first: a window cannot be admitted before the ring can
attest to the whole object.

§5.23's implementation enforces the envelope at **use time** — a chain the
frozen envelope does not admit does not promote. It does not enforce it at
**capture time**, and there is currently no way to: `rf_null_corpus` has no
writer, the persistence mechanism §5.20 governs is unbuilt, and a check with
nothing to check would be the inert admission entry 11 was drained for closing.

What is missing is one gate, at one place: after full-object ring attestation
and before a window is persisted, a window whose `signal_chain_hash` is not a
declared member of the corpus's `InstrumentChainEnvelope` **is not corpus**. It
is not re-labelled, not held aside and not counted — the lock names the
instruments the corpus is made of, and a window from another one is a window
from another experiment.

**Why this is exposed and the use-time gate was not.** No corpus exists, so
nothing can be admitted wrongly today. The moment one does, the order reverses:
a wrongly admitted window is inside the sample the published bound is computed
over, and unlike a wrongly licensed promotion it cannot be refused afterwards —
it has already changed the denominator.

---

## 15. The frozen slope tolerance governs no analysis

**Trigger:** before a spur catalogue entry can become usable. Not before the
catalogue exists — before anything reads a classification off one.

`CapturePlanDeclaration` freezes `PLAN_SLOPE_TOLERANCE = 0.01` into its digest,
and **no declaration in the repository refers to it.** Every other §5.22 constant
now governs a declared act: the retune deltas govern a signed per-visit retune,
the band-edge exclusion governs which baseband offsets an eligible trial may
carry, the persistence margin and the 7-of-8 requirement govern the repeats a
catalogued spur earned its entry with. The slope tolerance governs an
**analysis** — estimating a feature's slope across retunes and deciding whether
it matches an integer member of the affine mixing family — and there is no
analysis in this repository.

The repair, when the analysis exists:

> Before a spur catalogue entry can become usable, the slope analysis must apply
> the frozen `0.01` tolerance and record the measured slope and its residuals.

Recorded here rather than as a comment beside the constant, because **entry 11
is the demonstration of what a comment is worth**: a repair recorded in prose
waits exactly as long as the prose does. The persistence margin and the 7-of-8
requirement were on the same catalogue-analysis boundary and are now enforced by
`SpurPersistenceObservation`; slope enforcement is what remains owed.

## Drain record

A landed entry leaves the list above. It is recorded here in one line, because
the honesty check is the **drain rate** and a rate cannot be read from a list of
what is still pending. This is a record, not a queue: nothing here is waiting.

| entry | landed in | commit |
| --- | --- | --- |
| 2a — attestation inspects the mount, not the acquisition | `PROMOTION_EXECUTION_CONTRACT.md` §9 Amendment A | `42cc6b5` |
| 2b — two preconditions, not one | `PROMOTION_EXECUTION_CONTRACT.md` §9 Amendment A | `42cc6b5` |
| 2d — startup refusal or mode gate | `PROMOTION_EXECUTION_CONTRACT.md` §9 Amendment A | `42cc6b5` |
| 8 — `fenced` cited the ground Amendment B moved | `scythe_promotion_ledger_store.py` docstring | slice 4 |
| 6 — the record sequence was a reader-only rule | `PROMOTION_EXECUTION_CONTRACT.md` §13c D.1 | Amendment D |
| 7 — a valid ledger with no declared generation | `PROMOTION_EXECUTION_CONTRACT.md` §13c D.3 | Amendment D |
| 8 — the coordinator did not yet allocate | `scythe_promotion_ledger.py`, slice 6b | slice 6b |
| 4 — no exit from a `FAILED` reservation | `PROMOTION_EXECUTION_CONTRACT.md` §13e Amendment F | Amendment F |
| 5 — the name check needed a token source | `SCYTHE_VERDICT_VOCABULARIES.md` §3, *The mechanical step, as implemented* | slice 7 |
| 6 — a token declared twice collapsed | `test_scythe_verdict_vocabularies.py`, `DUPLICATE_DECLARATIONS` | slice 8, before its ceiling code |
| 8 — §5.19's "until §5.20 exists" read as satisfied by a proposal | `RF_Signal_Family_Classifier_Scope.md` §5.19 | `8469ed5` |
| 12 — six stale claims in accepted §5.19 and §5.20, four listed and two found | `RF_Signal_Family_Classifier_Scope.md` §5.19, §5.20 | `d0c030e` |
| 13 — two stale claims in code, and a test that guarded a citation | `rf_null_corpus.py`, `rf_validation_manifest.py` | `623669d` |
| 11 — the lock froze the method and not the instrument | `rf_promotion_envelope.py`, `rf_corpus_vocabulary.py`, `rf_validation_manifest.py` | `9e0efc8` |

Entry 11 is the one to reread before writing "the repair is" in any entry, and
before recording a drain. **Drained at `9e0efc8`** — and the route there is the
record, not the row.

**Its own proposed repair would not have worked.** It said to freeze the
`signal_chain_hash`, and `gain_db` is inside that hash, so a `GAIN_STEPS`
observation spans two hashes by construction. An entry may name a defect
correctly and prescribe a cure that does not exist.

**It was drained once prematurely, and the row was withdrawn.** The first
implementation added the lock field and left admission uncalled, which review
called *recordable rather than repaired*. The second enforced admission and was
recorded here as drained — and review caught that too: the capture-plan half
froze a permutation of integer labels with no declared tunings and no trial
allocation, so a lock could open before the corpus plan existed. The row was
written and then removed, which is the failure this table is supposed to be
immune to. **It is kept in this prose deliberately**: a drain rate is only
honest if a retracted drain costs something to record.

**What the drain finally required**, and it is narrower than the entry's own
title suggests: not an envelope field, but the lock field, the receipt
propagation **and** use-time promotion admission landing together — plus a
capture plan that actually declares the corpus, with frequency-bearing tunings,
governed retune deltas, complete strata and count reconciliation, a retained
eligible universe, a precommitted selection of the 5 561 trials, and completion
reconciled against that selection by identity. A lock field nobody enforces
makes a licensing defect recordable; a plan of seeds and indices freezes
nothing the corpus is made of.

**Captured-window admission is not part of this drain.** §5.23 governs what a
corpus may contain and what may later promote; nothing here inspects an IQ
window on its way to disk, and nothing can, because there is no way to disk.
That is entry 14, and entry 9 precedes it.

Entry 13 is the one worth rereading before adding a note anywhere. Its runtime
claim survived five merges **because the test guarding it checked the citation
rather than the claim** — `assertIn("5.20", note)` passes whatever the sentence
around "5.20" says. A note that asserts the status of a document cannot be
maintained by this repository; a note that asserts a property of its own module
can, and the drained fix makes `NO WRITER` true by the same check that would
fail if a writer appeared.

Entry 8 was written on the §5.20 proposal branch and drained before that
proposal was accepted: the correction it names was authorised as its own act and
landed first. It is in this table rather than absent from it — unlike
`RETRY_REQUIRES_OPERATOR` below — because it *did* become an obligation of the
repository, for the length of one review. **This row is only true once `8469ed5`
has merged**, which is why §5.20's own merge is ordered behind it.

An entry for `RETRY_REQUIRES_OPERATOR` was written on the slice-4 branch and
never merged; it is absent from this table because it was never a pending
obligation of the repository. Amendment C settled it before the code landed,
which is the order that made the queue entry unnecessary.

Entry 2c was not an amendment but an expectation — *a contract amended at slice 3
because a real filesystem disagreed with it is the process working*. It was
fulfilled early: the disagreement was found in the design review rather than by a
filesystem, and 2a is its result.
