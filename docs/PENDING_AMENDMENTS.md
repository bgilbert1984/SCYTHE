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

## 5. `SCYTHE_VERDICT_VOCABULARIES.md` §3 — the name check needs a token source

**Trigger:** the next amendment to that document for a reason of its own.

The substring-root check works and has now caught two real collisions. It also
has a false-positive class: **uppercase English prose reads as a token.**

Running it for §9 Amendment A reported `FAILED` as a merit-side collision. The
hit was inside a `VERDICT_NOTES` string — *"THIS IS NOT A FAILED TRANSITION"* —
which is prose, not a minted name. `FAILED` is free, and the check said
otherwise.

The rule should say the check runs against **declared token tuples**
(`COORDINATE_KINDS`, `REFUSALS`, `DISPOSITIONS`, `COMPARISONS`, …) and not
against raw uppercase text.

This repository has now met that false-positive class **seven** times. Four
preceded this entry. The fifth was slice 6's scope test, asserting that the write
path imports no `fcntl` and matching the word in a docstring describing what 6b
would do. The sixth and seventh were slice 6b's, matching `ARMED` inside a
sentence saying that failing to acquire refuses ARMED, and `append` inside
`halt_appends` and `O_APPEND`.

**All three were written after this entry existed, by the author who wrote it.**
That is the finding worth keeping: knowing the class does not prevent writing
another one, because a text scan is the shortest thing to type and passes on the
first file you try it on. Reading declared names from the AST is the only
durable form, and it belongs in §3 rather than in an author's memory.

**A second defect, found while drafting Amendment F.** The check's merit universe
is a hand-written list of five `(module, tuple)` pairs, and it is incomplete.
`rf_capture_recovery` declares `SUPERSESSION_STATES` and `RECOVERY_OUTCOMES` —
eight merit-side tokens — and none of them is in the list.

The consequence was live. `GENERATION_SUPERSEDED` was checked, reported **clear**,
and collides with recovery's `SUPERSEDED`. It was caught only because the drafter
opened `rf_capture_recovery.py` for an unrelated reason and recognised the word
— which is exactly the *found by looking* failure §3 exists to replace.

A hand-listed universe is a check that stays silent about whatever nobody
remembered to add. Either the universe is discovered — every module-level
UPPER_SNAKE tuple in the tree, minus a declared exclusion list — or an omission
has to fail loudly rather than pass quietly. The trade is real: discovery
produces false positives against tuples that are not vocabularies, and §3's own
rule says a check that cries wolf is one an author learns to skip.

Not fixed here: slice 7 is authorized as contract work only, and this is a test
change. It is the first thing slice 7's code should do, and until then the check
is known-incomplete rather than trusted.

A false positive is the safe direction — it costs a rename that was not needed —
so this is a refinement and not a defect.

**Update, slice 3 (2026-09-10).** The mechanical check now exists as
`test_scythe_verdict_vocabularies.py`, reading declared token tuples from the
AST and never raw text. The prose false-positive class is pinned by a test. The
entry stays queued because the *document* still describes the check that was
run by hand; the amendment is now a matter of writing down what the code does.

Building it surfaced a second thing §3 does not settle. **"Substring root" does
not say whether the unit is characters or words**, and the two answers differ on
a case that already occurred:

| pair | by character | by word |
| --- | --- | --- |
| `INDETERMINATE` / `INDETERMINATE_AS_FAILURE` | collision | collision |
| `UNVERIFIED` / `LOCK_SEMANTICS_UNVERIFIED` | collision | collision |
| `UNVERIFIED` / `VERIFIED_BY_FILESYSTEM_POLICY` | collision | **clear** |
| `RESERVED` / `UNRESERVED` | collision | clear |

The by-hand check read characters and refused `VERIFIED_BY_FILESYSTEM_POLICY`;
the word-level check clears it. Reading characters is not the fix — it also
reports every accidental spelling overlap, and a name check that cries wolf is
one an author learns to skip, which is the outcome §3 exists to prevent.

The implementation settles it with **two checks**: word-level containment, plus
a narrow negation-pair check for tokens differing by an `UN`/`NON`/`NOT` prefix
on a shared word. Two codes that read as each other's negation are the worst
neighbours across two vocabularies whatever their components say. All three
historical cases are reproduced and all four minted names clear. §3 should say
this rather than leave "substring root" to be read either way.

---

---

---

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

An entry for `RETRY_REQUIRES_OPERATOR` was written on the slice-4 branch and
never merged; it is absent from this table because it was never a pending
obligation of the repository. Amendment C settled it before the code landed,
which is the order that made the queue entry unnecessary.

Entry 2c was not an amendment but an expectation — *a contract amended at slice 3
because a real filesystem disagreed with it is the process working*. It was
fulfilled early: the disagreement was found in the design review rather than by a
filesystem, and 2a is its result.
