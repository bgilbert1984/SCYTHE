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

## 4. `PROMOTION_EXECUTION_CONTRACT.md` §8 — no exit from a `FAILED` reservation

**Trigger:** slice 7 (reconciliation and generations).

§2 says re-promotion after a failure is an operator action taken against the
graph. §8's table covers only unresolved reservations — a `RESERVED` with no
terminal record. A `RESERVED` resolved by `FAILED` is fenced (correctly: the
write may have landed) and has **no defined path back**, because
`RECONCILED_RELEASED` is defined against unresolved reservations only.

Found by implementing the read path, which has to place every reservation in
exactly one of committed / write-failed / unresolved and found the third state
had an exit while the second did not.

Either §8 extends to `FAILED` reservations, or §2's "operator action" is
narrowed to say what it actually is. Not resolved here: the slice that writes
reconciliation records is the one that has to answer it.

---

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
against raw uppercase text. This repository has now met that false-positive
class four times; it is the same shape as a raw-text scan hitting a docstring.

A false positive is the safe direction — it costs a rename that was not needed —
so this is a refinement and not a defect.

---

## 6. `PROMOTION_EXECUTION_CONTRACT.md` §9 — the record sequence is a reader-only rule

**Trigger:** slice 6 (the ledger write path), before the writer needs `next_seq`.

§9 requires per-record framing — a length and a checksum — and says nothing
about record sequence numbers. The read path now enforces a stronger rule that
the writer has to honour and that no accepted document states:

> One sequence across every record kind, header included, strictly increasing.

It was chosen for a reason worth writing down: `next_seq` has to be answerable
from the **last record of the file**, whatever kind that record is, and a
per-kind counter makes *the last record* a question with three answers. Strictly
increasing gives global uniqueness for free, and subsumes the narrower
duplicate-reservation check the reader had before.

Gaps are permitted deliberately. A gap is what a writer that took a sequence
number and crashed before framing the record leaves behind; refusing it would
make a lost record render the whole ledger unreadable rather than merely lost.

This is queued rather than amended because slice 6 opens §9 for its own reasons.
A rule the writer must obey that lives only in the reader is the second contract
nobody accepted — which is exactly what this file exists to prevent, so it is
also the entry most worth watching drain.

---

## 7. `PROMOTION_EXECUTION_CONTRACT.md` §10 — a valid ledger with no declared generation

**Trigger:** slice 6 (the ledger write path).

§10 says a ledger created by this contract's own initialization is empty and
valid. §9 says the holder writes its `ProcessIdentity` into a header record. A
crash between the two leaves a zero-byte ledger: valid per §10, fencing nothing,
and with **no generation identifier**, which is the thing C1 is a lifetime total
over (§11).

The read path reports it as readable with `header_present: false` and
`generation: null`. It does **not** refuse ARMED on that basis, because doing so
would mint an executability code, and minting one needs §5 amended — outside
what slice 3 was authorized to do.

The exit exists and is in slice 6: the writer takes ownership and writes the
header. So this is a gap in what is *stated*, not a trap. §10 should say whether
a headerless ledger refuses ARMED, and if it does, under which code.

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

Entry 2c was not an amendment but an expectation — *a contract amended at slice 3
because a real filesystem disagreed with it is the process working*. It was
fulfilled early: the disagreement was found in the design review rather than by a
filesystem, and 2a is its result.
