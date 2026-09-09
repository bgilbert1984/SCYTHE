# Verdict Vocabularies

```text
Status:     PROPOSED — not accepted
Authority:  NORMATIVE once accepted
Scope:      Every SCYTHE module that produces judgements
Instances:  RF_WALK_SURVEY_CONTRACT.md §4 "The vocabulary's range"  (admission)
            rf_capture_audit.py — RESTART_NOT_OBSERVED                (recovery)
            PROMOTION_EXECUTION_CONTRACT.md §4a                       (promotion)
```

This document states one rule. It exists because the same design need has been
rediscovered three times from scratch, and each rediscovery cost a review cycle
on a different module.

---

## 1. The rule

> **A module that produces judgements declares two vocabularies, not one:
> verdicts about the subject, and a sibling set about whether a verdict could be
> reached or acted on at all. The two sets are disjoint and are counted
> separately.**

Both sets are declared when the module declares its first verdict — not when
the second one is needed.

---

## 2. Why it keeps being rediscovered

Three times, a module has been built with one vocabulary, and three times a
condition has arrived that the vocabulary had no honest code for. Each time the
pressure was the same: **stretch an existing reason code to cover it.** Each
time the local fix was to refuse, and to name the missing category instead.

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

The pattern is not three coincidences. Every judgement rests on an apparatus,
and an apparatus has its own failure modes, which are not statements about the
thing being judged.

---

## 3. The two sets

**Merit codes** answer *what is true of the subject?* The subject is the thing
the module exists to judge — a frame, a capture, a finding.

**Executability codes** answer *could a verdict be reached, or acted on, at
all?* They describe the apparatus, the conditions, or the boundary — never the
subject.

The discriminating question is the one §4 already asks: **who repairs it, and
how?** A merit code is repaired by changing the subject or accepting the
judgement about it. An executability code is repaired by fixing the apparatus,
and the subject may be entirely sound.

---

## 4. Requirements

1. **Disjoint.** No code appears in both sets. A code that would need to is two
   codes.
2. **Separately counted.** Counts are reported per set and are never summed into
   a single "refusals" total. A merged count cannot answer either question:
   *how often do we judge this unfit* and *how often can we not judge at all*
   have different owners and different repairs.
3. **Both declared up front.** A module states both vocabularies when it states
   its first verdict. A module that declares only merit codes has not deferred
   the second set; it has decided to discover it under pressure.
4. **Neither is a fallback.** Adding an executability set does not open the merit
   set. Both stay closed. "No code applies" remains a defect in the vocabulary,
   to be repaired by amendment.
5. **An executability code never carries a disposition about the subject.** If
   the apparatus could not reach a verdict, there is no verdict to carry.

---

## 5. What this is not

- It is **not** a licence to move an inconvenient merit code into the second set.
  The test in §3 is the whole discipline; a code that says something about the
  subject stays a merit code however awkward.
- It is **not** a third category for uncertainty. `UNDETERMINED` is a verdict
  about the subject reached through a working apparatus, and the standing rule
  that it must not be converted into failure is unaffected. An apparatus that
  could not run produced no verdict at all — that is the second set.
- It does **not** require the two sets to live in the same module. In the
  promotion sequence they deliberately do not: merit belongs to the policy,
  executability to the coordinator, and that separation is the point.

---

## 6. Relationship to the walking-survey contract

`RF_WALK_SURVEY_CONTRACT.md` §4 "The vocabulary's range" is this rule's first
instance and states its argument in full, scoped to survey frames. That contract
is ACCEPTED and is not amended by this document; a back-reference from §4 would
be an amendment to an accepted contract and belongs to its own slice.

Where the two are read together, §4 governs admission and this document governs
the general case.
