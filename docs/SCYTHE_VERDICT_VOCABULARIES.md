# Verdict Vocabularies

```text
Status:      PROPOSED — not accepted
Authority:   NORMATIVE once accepted, over the rule in §1 only
Scope:       Every SCYTHE module that produces judgements
Conformance: Declared per module, in that module's own contract, when that
             contract is next amended for its own reasons (§7). This document
             opens no accepted contract.
```

This document states one rule. It exists because the same design need has been
rediscovered three times from scratch, and because a fourth instance was found
already in the tree, placed correctly by someone who had no name for why.

---

## 1. The rule

> **A module that produces judgements declares two vocabularies, not one:
> verdicts about the subject, and a sibling set about whether a verdict could be
> reached or acted on at all. The two sets are disjoint, are named disjointly,
> and are counted separately.**

Both sets are declared when the module declares its first verdict — not when the
second one is needed.

---

## 2. The evidence

### 2.1 The instance that predates the rule

`BUDGET_EXHAUSTED` lives in `scythe_promotion_ledger` (the coordinator) and not
in `scythe_promotion_policy` (the refusal vocabulary), where every other reason
for not promoting a finding lives.

**Nobody argued it there.** It was placed under local pressure, by someone with
no name for the distinction, and it turns out to sit exactly where this rule
would put it: a budget refusal says nothing about the finding, which may be
entirely promotable.

This is recorded as **found, not designed**, and it is the strongest evidence
available that the distinction is real rather than a scheme imposed on the code.
A rule invented and then illustrated is a preference with examples attached. A
rule whose evidence includes an instance that predates it was independently
derived under local pressure and then found to fit — which is what it means for
a distinction to be in the problem rather than in the describer.

It is also one member deep and unnamed, which is precisely the failure this
document addresses: the set existed, and the next module still started with one
vocabulary.

### 2.2 Three rediscoveries

Three times, a module was built with one vocabulary, and three times a condition
arrived that the vocabulary had no honest code for. Each time the pressure was
the same: **stretch an existing reason code to cover it.** Each time the local
fix was to refuse, and to name the missing category instead.

**Admission.** `RF_WALK_SURVEY_CONTRACT.md` §4 declared dispositions and reason
codes about survey frames, then met an input that was not a survey frame at all
— missing a §2 field, or carrying one structurally invalid. It has no
disposition and no reason code, because it never entered the domain where
verdicts exist. §4's own argument is the general one:

> Collapsing the two would let producer bugs contaminate refusal counts — and
> those counts are exactly the measurement that must stay clean once a store
> persists facts keyed by reason, because "how often are frames arriving
> unaligned" and "how often is the producer emitting malformed payloads" are
> different questions with different owners and different repairs.

**Recovery.** `RESTART_NOT_OBSERVED` sat among three verdicts about the target
process while itself being a statement about the *evidence*: the other three say
what happened to the capture, this one says the apparatus could not tell. It was
separated after the fact.

**Promotion.** A durable-ceiling refusal has nowhere to live except a refusal
vocabulary about a finding's merits. A ceiling refusal says nothing about the
finding, which may be entirely promotable.

The pattern is not three coincidences plus a lucky accident. Every judgement
rests on an apparatus, and an apparatus has its own failure modes, which are not
statements about the thing being judged.

---

## 3. The two sets

**Merit codes** answer *what is true of the subject?* The subject is the thing
the module exists to judge — a frame, a capture, a promotion request.

**Executability codes** answer *could a verdict be reached, or acted on, at
all?* They describe the apparatus, the conditions, or the boundary — never the
subject.

**The discriminating question is the one §4 already asks: who repairs it, and
how?** A merit code is repaired by changing the subject or accepting the
judgement about it. An executability code is repaired by fixing the apparatus,
and the subject may be entirely sound.

**The default is stay put.** A code belongs to the set its module first placed it
in unless the repair test positively says the apparatus is what changes. The rule
creates one new place to put things; it does not create a reason to move things
there.

---

## 4. Requirements

1. **Disjoint.** No code appears in both sets. A code that would need to is two
   codes.
2. **Named disjointly.** The two sets must not use names that differ only
   cosmetically. Near-identical names across the boundary are read as the same
   concept regardless of what the contract says, and the confusion arrives later
   than the rename would have. §6 records the case that established this.
3. **Separately counted.** Counts are reported per set and are never summed into
   a single "refusals" total. A merged count cannot answer either question:
   *how often do we judge this unfit* and *how often can we not judge at all*
   have different owners and different repairs.
4. **Both declared up front.** A module states both vocabularies when it states
   its first verdict. A module that declares only merit codes has not deferred
   the second set; it has decided to discover it under pressure.
5. **Neither is a fallback.** Adding an executability set does not open the merit
   set. Both stay closed. "No code applies" remains a defect in the vocabulary,
   to be repaired by amendment.
6. **An executability code never carries a disposition about the subject.** If
   the apparatus could not reach a verdict, there is no verdict to carry.

---

## 5. Two worked examples

The rule is taught by both directions or it becomes a licence. One example of a
pair that separates teaches only *separate things*; a reader who learns only that
will start separating.

### 5.1 A pair that separates

`DUPLICATE_PROMOTION` and `IDENTITY_UNRESOLVED` describe **the same promotion
identity in adjacent states**, and belong to different sets.

| | set | says | repaired by |
| --- | --- | --- | --- |
| `DUPLICATE_PROMOTION` | merit | this finding was already recorded | nothing — it is already in the graph |
| `IDENTITY_UNRESOLVED` | executability | we do not know whether it was recorded | reconciling the ledger against the graph |

The naive reading puts them together, because both end in *not promoted now*.
The repair test pulls them apart: one is a fact about the subject's history, the
other a fact about the apparatus, and in the second the finding may be perfectly
promotable once the apparatus is fixed.

### 5.2 A pair that does not separate

`AUTHORITY_INSUFFICIENT` and `MODEL_RESPONSE_USED_AS_AUTHORITY` are both **merit**
codes, and the tempting split is wrong.

The temptation: `MODEL_RESPONSE_USED_AS_AUTHORITY` is obviously about the
finding's provenance, but `AUTHORITY_INSUFFICIENT` is about the *requester's
permissions* — and "we declined to act because of who asked" sounds exactly like
executability.

Apply the repair test and both give the same answer. **Neither is repaired by
fixing anything.** No apparatus changes: not the coordinator, not the ledger, not
the bus, not the clock. Both are repaired by a differently constituted request,
and a differently constituted request is a different subject.

The reason runs deeper than the test. **The subject under judgement is the
promotion request, not the verdict alone.** Who asserts a finding is part of what
is being judged — that is the policy's founding commitment, the one that makes a
model response not an authority. A rule that moved `AUTHORITY_INSUFFICIENT` out
of merit would be quietly denying it.

### 5.3 Where the line actually falls

`AUTHORITY_INSUFFICIENT` (merit) and `LEDGER_NOT_OWNED` (executability) both read
as *refused on a permission condition*, and they are on opposite sides.

- `AUTHORITY_INSUFFICIENT` — **who asked** is a property of the request. Repair:
  a different requester makes a different, valid request.
- `LEDGER_NOT_OWNED` — **whether this process may write at all** is a property of
  the deployment. Repair: fix the deployment. The request was fine.

Two codes that sound alike, one on each side, and the repair test separates them
without appeal to intuition. That is the whole discipline.

---

## 6. Not a third category for uncertainty

This is the guardrail most likely to be tested, and the one this document's own
first application came closest to breaking.

`UNDETERMINED` is a **verdict about the subject reached through a working
apparatus.** The standing rule that it must not be converted into failure is
unaffected. An apparatus that could not run produced no verdict at all — that is
the second set, and it is a different situation with a different repair.

**The naming case that established Requirement §4.2.** The promotion execution
contract originally called a reservation with no terminal record `INDETERMINATE`
— an executability state, correctly placed. But `INDETERMINATE` is already a
merit-side token in merged code: it is a comparison outcome in
`scythe_invariant_ledger` (`COMPARISONS`), and `INDETERMINATE_AS_FAILURE` is a
merit refusal reason in `scythe_promotion_policy`.

Two words one prefix apart, doing opposite jobs, with the standing rule naming
only one of them. The state was renamed `UNRESOLVED`, which also pairs with the
`IDENTITY_UNRESOLVED` code and with the reconciliation that resolves it.

Disjoint sets whose names are not visibly disjoint are disjoint only in the
document. Renaming is cheap while a contract is `PROPOSED` and expensive after.

---

## 7. Conformance

A document governing three modules needs its relationship to those modules named,
or it is advisory prose that each module is free to have already contradicted.

**Each module's contract acquires a conformance line when that contract is next
amended for its own reasons.** Not as a wave of edits opening accepted documents
to add references.

- A contract that never opens again is not out of conformance. If admission
  conforms in fact, `RF_WALK_SURVEY_CONTRACT.md` stays sealed and says nothing
  about this document.
- This document therefore **opens no accepted contract.** §4 of the survey
  contract is this rule's first full statement, scoped to frames, and is
  deliberately left untouched: a back-reference added into an `ACCEPTED` document
  would be an amendment made to give a new rule reach it does not otherwise have.
- Where the two are read together, §4 governs admission and this document governs
  the general case. Nothing in §4 is modified, narrowed or extended by that.

This is slower than a sweep and much harder to get wrong.

**Current conformance:**

| module | contract | status |
| --- | --- | --- |
| promotion | `PROMOTION_EXECUTION_CONTRACT.md` §5 | declared — `PROPOSED`, opening now |
| admission | `RF_WALK_SURVEY_CONTRACT.md` §4 | conforms in fact; sealed, no line |
| recovery | none | conforms in fact; line due when a contract exists |
