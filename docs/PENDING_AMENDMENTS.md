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

## 2. `PROMOTION_EXECUTION_CONTRACT.md` §9 — two filesystem preconditions

**Trigger:** slice 3 (durable ledger, read path) — the first slice where §9 and
§13 meet a real filesystem.

§9 requires `fcntl.flock(LOCK_EX | LOCK_NB)` and §7 requires fsync durability.
Both are currently **asserted rather than observed**, and on this host (WSL2)
both are live questions: a path on a Windows-backed mount gives `flock` that
does not reliably exclude and `fsync` guarantees that are not the ones the
contract reasons about.

### 2a. The check cannot be acquisition success

A lock that returns success without excluding is a fact about the **deployment**,
not about one file at one moment, and it holds for every operation the
coordinator performs for as long as it runs there. So the precondition is
per-startup, not per-write — but it also cannot be a property of the ledger path
alone.

**The case that matters is two processes with different configured directories
on the same non-excluding mount.** A check that asks *can I acquire my own lock*
answers yes in exactly that case. Whatever slice 3 verifies, it must verify the
**mount's semantics**, not the acquisition's success.

Verifying exclusion directly generally requires a second process. The version
that does not: **identify the filesystem type behind the configured directory at
startup and refuse ARMED on anything not on an allowlist.** Cruder, and it will
refuse some working configurations — which is the correct direction for this
failure. A refused ARMED on a sound host is recoverable by extending the
allowlist; an accepted ARMED on a host that does not exclude *is* the race §3
exists to prevent, arriving one level below where the mutex can see it. It also
states the requirement in terms an operator can act on, which *verify your lock
semantics* does not.

### 2b. Two preconditions, not one

fsync durability and lock exclusion fail on the same mounts **here**, so one
check would catch both today. They must still be stated as two claims with two
preconditions.

They will come apart. A network filesystem that fsyncs honestly and locks badly,
or the reverse, is entirely ordinary. §7's reserve-before-write depends on the
first; §3's mutex depends on the second. One precondition covering both would
tie two independent guarantees to whichever one was checked, and the failure
would surface as the other one silently not holding.

Slice 3 may verify both with a single lookup. That is an implementation
convenience and not a merge of the requirements.

### 2c. Expect the amendment

An accepted contract amended at slice 3 because a real filesystem disagreed with
it is the process working. A contract that survives to slice 7 unamended is more
likely to mean nothing checked it than that it was right. §13's
torn-tail-loads-as-`UNRESOLVED` is the other claim in this class: a statement
about what a partially written file looks like on the host actually running it.

The torn tail announces itself when tested. The lock does not — it returns
success. That asymmetry is why 2a is the item to design for.

---

## 3. `RF_WALK_SURVEY_CONTRACT.md` §4 — conformance line

**Trigger:** whenever §4 next opens for a reason of its own.

Add a line declaring conformance to `SCYTHE_VERDICT_VOCABULARIES.md`. §4 is that
rule's first full statement, scoped to frames.

If §4 never opens again, admission conforms in fact and this entry expires
unlanded. That is the convention working, not a debt.

---

## 4. Recovery's contract — carry the `RESTART_NOT_OBSERVED` finding

**Trigger:** when a recovery contract exists.

`SCYTHE_VERDICT_VOCABULARIES.md` §7 records that `RESTART_NOT_OBSERVED` is in
merged code, is an executability code, sits among three merit verdicts, and is
declared as one nowhere. The finding is recorded there because recovery has no
contract to carry it. When one exists, it carries it, and the vocabularies
document's conformance table gets a normal row.
