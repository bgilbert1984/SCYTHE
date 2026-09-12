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

## 6. `test_scythe_verdict_vocabularies.py` — a token declared twice collapses

**Trigger:** before any slice-8 ceiling code. Not *with* it, and not after.

The check holds tokens as a **set of strings**. One identical token declared by
two unrelated closed sets collapses to one member, so the check cannot tell *this
word means one thing* from *this word means two things in two domains*.
`UNRESOLVED` — an unclassified modulation in `rf_signal_family`, and a
reservation whose write was never answered in the promotion ledger — is exactly
that, and the check reported it as one neighbour.

It is worse than blindness. `cross_set_collisions` clears a hit when the
candidate and the hit share a declaring set, and a token declared in several
sets clears against **any** of them. A genuine cross-domain neighbour can
therefore be skipped because the same word is also declared somewhere harmless
— a false *negative* produced by the mechanism added to prevent false positives.

**Scale, measured rather than guessed: 31 tokens in this tree are declared by
more than one module.** Some are deliberate (`COMMITTED` in the coordinator and
the reader are one concept in two places); some are two concepts wearing one
word (`NOISE_COMPATIBLE` in `NULL_REASON_CODES` and `NULL_OUTCOMES`).

**The repair.** Provenance is already collected — `discovered_tokens` returns a
multimap of token to declaring `module.SET`. What is missing is using it:

1. a cross-domain duplicate declaration requires a **recorded judgement**,
   exactly as a collision does;
2. same-set clearing must not fire on a token whose *other* declaration is in an
   unrelated domain.

Recorded now and implemented before slice 8's ceiling code, because that check is
what slice 8's names will be argued from. Until then it is known-incomplete
again — the second time, and the first repair is what surfaced this one.

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
| 5 — the name check needed a token source | `SCYTHE_VERDICT_VOCABULARIES.md` §3, *The mechanical step, as implemented* | slice 7 |

An entry for `RETRY_REQUIRES_OPERATOR` was written on the slice-4 branch and
never merged; it is absent from this table because it was never a pending
obligation of the repository. Amendment C settled it before the code landed,
which is the order that made the queue entry unnecessary.

Entry 2c was not an amendment but an expectation — *a contract amended at slice 3
because a real filesystem disagreed with it is the process working*. It was
fulfilled early: the disagreement was found in the design review rather than by a
filesystem, and 2a is its result.
