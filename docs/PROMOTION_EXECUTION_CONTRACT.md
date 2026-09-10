# Promotion Execution Contract

```text
Status:                 ACCEPTED — nothing implemented
Accepted:               2026-09-08, after review amendments cafda8d
Amendment A:            §9 filesystem capability — ACCEPTED 2026-09-09,
                        amendment 42cc6b5
Amendment B:            §13a three-state writer result — ACCEPTED 2026-09-10
Amendment C:            §13b RETRY_REQUIRES_OPERATOR — PROPOSED 2026-09-10
Authority:              NORMATIVE
Constrains:             Step 4 of the promotion sequence (execution adapter)
Depends on:             SCYTHE_VERDICT_VOCABULARIES.md  (ACCEPTED — §5 declares
                        conformance)
                        scythe_promotion_policy.py      (v2 identity, MERGED)
                        scythe_promotion_ledger.py      (shadow coordinator, MERGED)
                        rf_capture_recovery.ProcessIdentity (MERGED, reused)
Atomic reservation:     NOT_IMPLEMENTED
Durable ledger:         NOT_IMPLEMENTED
Reconciliation:         NOT_IMPLEMENTED
Execution adapter:      NOT_IMPLEMENTED
ARMED graph mutation:   REQUIRES SEPARATE EXPLICIT AUTHORIZATION (step 8)
```

This document is the promise a Step 4 implementation must satisfy. It is written
before the implementation exists so that it constrains one rather than describing
one.

Two defects gate Step 4, both found by review of the merged shadow ledger:

1. `PromotionCoordinator.evaluate()` performs the duplicate check, the budget
   check and the ledger append without a coordinator-level lock. Two concurrent
   ARMED evaluations can both pass and write the same identity.
2. The promoted lists are in-memory and unbounded. They disappear on restart.
   That is acceptable for a read-only shadow slice and insufficient as the
   authoritative idempotency ledger for real GraphOps writes.

Neither is fixed by the other. §3–§4 answer the first, §6–§13 the second.

---

## 1. Terminology

| term | meaning |
| --- | --- |
| **identity** | A promotion identity, `promotion_identity()` under digest revision v2. |
| **reservation** | A durable claim on one identity. Not a claim that a record exists. |
| **terminal record** | The `COMMITTED` or `FAILED` entry that resolves a reservation. |
| **unresolved reservation** | A reservation with no terminal record. Neither success nor failure. **Not** `INDETERMINATE`, which is a merit-side token in merged code — see §7. |
| **reconciliation** | The operator act that resolves an unresolved reservation against the graph (§8). |
| **generation** | One lifetime of the ledger. Ended only by an explicit operator act (§11). |
| **incarnation** | `ProcessIdentity(boot_id, pid, start_ticks)` — the existing type, reused. |
| **the ledger** | The durable, single-writer, append-only file defined in §6–§9. |
| **the window** | The process-local budget structure defined in §6. Not the ledger. |

---

## 2. What a reservation means

> **A reservation is a claim on an identity. It is not a claim that a record
> exists in the graph.**

This is the load-bearing definition, and everything below follows from it.

The reason it must be a claim on the identity and not on the record is that the
adapter boundary cannot always tell the two apart. A timeout and a write that
landed whose acknowledgement was lost are indistinguishable from outside the
bus.

*Amended by §13a (B.1, B.3): the boundary reports `CREATED`, `NOT_CREATED` or
`UNKNOWN`, and this paragraph now describes the `UNKNOWN` case. A rejection the
adapter can definitely attest to is `NOT_CREATED`, which fences on the closing
rule of this section rather than on ambiguity.*

So **retaining the reservation across a failed write is not a convenience; it is
forced.** Releasing it would mean re-promoting an identity whose record may
already exist — a duplicate in GraphOps, which is precisely the outcome the
ledger exists to prevent. Nothing downstream can tell a duplicate from a second
real finding, which is why the asymmetry runs the way it does:

- A lost finding is recoverable. The check can be re-run against the same
  evidence and will produce the same identity.
- A duplicate record is not recoverable. It is indistinguishable from evidence.

Re-promotion after a failure is therefore an operator action taken against the
graph (§8), never an automatic retry. This holds for a definitely-failed write
as well, and there it is the whole reason (§13a B.3). The coordinator names that
refusal `RETRY_REQUIRES_OPERATOR` (§13b), and §13b C.4 records that the
operation the name refers to does not exist until slice 7.

---

## 3. The critical section

```
  ┌── coordinator lock held ───────────────────────────────────┐
  │  1. snapshot the identity set                              │
  │  2. decide_promotion(..., already_promoted=snapshot)       │
  │  3. executability checks: budget, ceilings, ledger state   │
  │  4. append RESERVED to the ledger, fsync                   │
  │  5. add to the window                                      │
  │  6. audit PROMOTION_ATTEMPTED                              │
  └── release ─────────────────────────────────────────────────┘
     7. call the writer                    <- outside the lock
     8. reacquire, append COMMITTED or FAILED  <- no fsync required (§7)
     9. audit PROMOTION_RECORDED / PROMOTION_FAILED / the unresolved event
```

**Steps 1–3 must be atomic with 4–5** or the checks decide against state that has
already moved. Two races exist today and both are closed by this, not by one:
concurrent evaluation of the *same* identity (both pass step 2), and concurrent
evaluation of *distinct* identities against a nearly-spent budget (all pass
step 3).

**Step 4 must be durable before step 7.** Reserve-before-write only survives a
crash if the reservation is on disk before the writer is called. An unsynced
reservation gives write-first semantics with extra steps, and write-first is the
ordering that produces duplicates. The fsync latency is inside the critical
section by necessity; that cost is one of the reasons the budget exists.

**Step 8 reacquires the lock** and does not touch the window: it was spent at
step 5, and spending it again would measure the adapter's latency as promotion
rate (§13a B.8). An `UNKNOWN` answer or an exception writes no terminal record
at all and leaves the identity `RESERVED` (§13a B.2).

**The lock is released before step 7** because the writer is unbounded external
I/O. Holding across it would serialize every evaluation behind the slowest bus
call, and a writer that re-entered the coordinator would deadlock while holding
a half-made reservation.

**The lock is not conditional on mode.** SHADOW takes the same lock over its
simulated structures. A SHADOW that is not locked measures a concurrency
behaviour ARMED will not have, which is the off-policy evaluation error already
established for the budget: shadow must simulate the thing it is shadowing.

---

## 4. Lock discipline

**A plain `threading.Lock`, not an `RLock`.** An `RLock` would let a re-entrant
call from inside the critical section pass the duplicate check while a
reservation is half-made — silently, and only under the interleaving that is
hardest to reproduce. Nothing inside the critical section may call out, so
re-entry is a bug that should be impossible by construction; a plain `Lock`
makes it a deadlock rather than a wrong answer.

**Lock order is coordinator → audit, never the reverse.** `PromotionAudit` holds
its own `RLock` and is called at step 6, inside the coordinator lock.
`PromotionAudit` does not know the coordinator exists and must not learn:
nothing holding the audit lock may call back into the coordinator.

---

## 5. The coordinator's executability vocabulary

**Conformance:** this contract conforms to `SCYTHE_VERDICT_VOCABULARIES.md`, and
this section is its declaration. The line was added while this contract was
`PROPOSED` and open, which is the only moment its own §7 allows: no accepted
contract is opened to add a reference.

This section instantiates that rule for the promotion sequence. The two vocabularies live in **different modules**, deliberately:

| | **merit** | **executability** |
| --- | --- | --- |
| owner | `scythe_promotion_policy` | `scythe_promotion_ledger` (the coordinator) |
| answers | is this finding fit to promote? | could we act on it at all? |
| examples | `VERDICT_NOT_PROMOTABLE`, `CAPSULE_UNBOUND`, `DUPLICATE_PROMOTION` | `BUDGET_EXHAUSTED`, `DURABLE_CEILING_REACHED`, `UNRESOLVED_CEILING_REACHED`, `IDENTITY_UNRESOLVED`, `RETRY_REQUIRES_OPERATOR`, `LEDGER_UNAVAILABLE`, `LEDGER_NOT_OWNED`, `LEDGER_TORN`, `LOCK_EXCLUSION_UNATTESTED`, `RESERVATION_DURABILITY_UNATTESTED` |
| repaired by | changing the finding, or accepting the judgement | fixing the apparatus; the finding may be sound |

The coordinator **returns** `IDENTITY_UNRESOLVED` on a second evaluation of an
unresolved identity, and never `DUPLICATE_PROMOTION` (§13a B.4). It returns
`RETRY_REQUIRES_OPERATOR` on a second evaluation of a definitely-failed one
(§13b), which is the third answer the three stored states require.

`BUDGET_EXHAUSTED` already lives in the coordinator rather than the policy. That
separation was made ad hoc and is the executability vocabulary's first member,
created before there was a name for the set it belonged to.

**The ceilings are enforced here, not as policy refusal reasons.** A policy
reason is a verdict about a finding's merits; a ceiling refusal says nothing
about the finding, which may be entirely promotable. Putting it in the policy
vocabulary would contaminate refusal-by-reason counts with executability
conditions, and downstream nothing could distinguish *we judged this not worth
promoting* from *we declined to act at all*.

**The boundary case, which tests the rule.** `DUPLICATE_PROMOTION` and
`IDENTITY_UNRESOLVED` describe the same identity in adjacent states and belong
to different sets:

- `DUPLICATE_PROMOTION` — **merit.** This finding was already recorded. A fact
  about the subject's history. The repair is nothing; it is already in the graph.
- `IDENTITY_UNRESOLVED` — **executability.** We do not know whether it was
  recorded. A fact about the apparatus. The repair is reconciliation (§8), and
  the finding may be perfectly promotable once we know.

**Counts are reported per set and never summed.** `status()` publishes
`merit_refusals` and `executability_refusals` separately.

---

## 6. Two structures, not one

`_promoted: List[(identity, monotonic_ns)]` currently serves both idempotency and
rate limiting. Its in-memory replacement is `_Posture` (§13a B.6). They have different persistence semantics and must be separated
before either can be made durable.

| | **identity set** | **window** |
| --- | --- | --- |
| answers | may this identity be promoted? | have too many been promoted lately? |
| clock | none | `time.monotonic()` |
| durable | **yes** — authoritative | **no** — process-local |
| survives restart | yes | no, by declaration |

**The window cannot be persisted, and the reason is this host's own clock rule.**
The budget is anchored on `time.monotonic()`, whose zero is per-boot. A persisted
`monotonic_ns` from a previous boot is not comparable to the current one — it is
not stale, it is meaningless. The UTC field recorded beside it cannot rescue the
join either: on this host UTC is display metadata only, because the wall clock
takes ~23.5 h steps that retroactively re-render past timestamps. Persisting the
window would produce a number that looks like a rate and is not one.

So the window resets on restart, `status()` publishes
`budget_window_survives_restart: false`, and the cross-restart bound is provided
by a different mechanism entirely — §11, not a longer window.

---

## 7. Two-phase records

Each promotion writes two ledger entries.

```json
{"kind":"RESERVED","seq":41,"identity":"promotion:…","target_graph":"…",
 "record_class":"INVARIANT_FINDING","incarnation":{"boot_id":"…","pid":…,
 "start_ticks":…},"monotonic_ns":…,"utc_display":"…","len":…,"crc":"…"}
{"kind":"COMMITTED","seq":42,"reserves":41,"len":…,"crc":"…"}
```

`RESERVED` **must** be fsynced (§3). The terminal record **need not** be: losing
it degrades the reservation to unresolved, which is the safe direction.

**A `RESERVED` with no terminal record is `UNRESOLVED`.** The write may have
landed and may not have. An `UNKNOWN` result and an exception from the writer
both produce exactly this, and neither produces a `FAILED` record (§13a B.1,
B.2).

**This contract's own rule: an `UNRESOLVED` reservation is never recorded as a
failure.** A write that may have landed is not a write that did not, and the
ledger has no basis for the stronger claim.

A reader will recognise the shape of the standing rule about `UNDETERMINED`, and
should not import it. The two are parallel and not the same. `UNDETERMINED` is a
verdict about a finding, reached through an apparatus that worked; `UNRESOLVED`
is the apparatus reporting that it cannot say. They belong to different
vocabularies (§5), and a rule that crossed that boundary would be the conflation
the naming below exists to prevent.

**The state is `UNRESOLVED` and deliberately not `INDETERMINATE`.** That name is
already merit-side in merged code — a comparison outcome in
`scythe_invariant_ledger`, and the root of `INDETERMINATE_AS_FAILURE` in
`scythe_promotion_policy`. An executability state one prefix away from
`UNDETERMINED`, whose standing rule names only the merit sense, would be read as
the same concept whatever this contract said. `UNRESOLVED` also pairs with the
`IDENTITY_UNRESOLVED` code and with the reconciliation that resolves it.

Therefore an unresolved reservation:

- **blocks re-promotion of its own identity, and only its own** — `IDENTITY_UNRESOLVED`;
- **is surfaced and counted** in `status()`, from the identity map and never
  from the bounded audit ring (§13a B.5);
- **is never auto-retried and never auto-released.** It is released only by §8.

**One unresolved reservation does not halt ARMED.** The containment is already complete:
the reservation exists, that identity is blocked, no duplicate can reach the
graph. Halting ARMED globally on one unresolved reservation would convert a contained
uncertainty into a total stop, and the predictable result is an operator under
pressure clearing unresolved reservations carelessly to get ARMED back — destroying the
thing the record was protecting.

**The rate of unresolved reservations is a different signal, and it is not
identity-scoped.** An adapter timing out on every write produces unresolved reservations
indefinitely, each individually contained, collectively meaning the graph
boundary is not working. That is bounded in §11 as a ceiling, using the same
mechanism as the reservation ceiling — two ceilings over one durable structure,
not two kinds of ceiling.

---

## 8. Reconciliation

Unresolved reservations accumulate monotonically and gate ARMED through §11. Without a
named operation to resolve one, "never auto-released" would mean "released by
hand-editing a file" — unaudited, outside §9's single-writer rule, and performed
by exactly the person that rule protects. The ceiling would be a trap with no
exit.

**Reconciliation is an operator determination made by looking at the graph, and
recorded in the ledger.** The ledger records *that a determination was made and
by whom*; it never re-derives one, because the ledger is precisely the thing
that does not know.

| record | meaning | effect |
| --- | --- | --- |
| `RECONCILED_COMMITTED` | the write landed | the reservation becomes a normal promotion; the identity stays fenced |
| `RECONCILED_RELEASED` | the write did not land | the identity is freed and may be promoted again |

- Both are **appended and checksummed like any other record**, never a hand
  edit. §9's rule that no other component appends is not suspended for an
  operator.
- `RECONCILED_RELEASED` is the **only** path that frees an identity.
- Reconciliation requires a **ledger authority**, distinct from the policy's
  promotion authorities: it acts on the ledger, not on a finding's merits. The
  same authority ends a generation (§11).
- **Reconciliation clears the unresolved ceiling, and does not refund the
  reservation ceiling.** A spent reservation stays spent whichever way the
  determination goes. Refunding it would let a timing-out adapter plus a diligent
  operator restore unlimited amplification through the counter that exists to
  stop it.

---

## 9. Ownership, location and the write path

**Exactly one writer.** Two coordinator processes sharing one ledger reproduce
the race of §3 one level up, where a mutex cannot reach it.

- The writing coordinator holds `fcntl.flock(fd, LOCK_EX | LOCK_NB)` for its
  entire lifetime, acquired before ARMED is reachable.
- Failure to acquire refuses ARMED (`LEDGER_NOT_OWNED`). It does **not** refuse
  SHADOW, which opens the ledger read-only (§12).
- The holder writes its `ProcessIdentity` into a header record, so a reader can
  name the owner rather than infer one. This is the same incarnation type the
  recovery work already uses; a second identity type would be a second answer to
  a question that has one.

**Location: explicitly configured, in a directory owned by the coordinator's
state.** Never defaulted into the working tree, and never under `/tmp`.

- In the tree, a checkout could silently move the fence.
- On tmpfs, the ledger vanishes on reboot, which turns §10's *a missing ledger
  refuses ARMED* into *ARMED always refuses after reboot* — and the pressure that
  creates is to weaken the rule rather than to fix the path.

**Two write-path details, because they are where reserve-before-write actually
fails:**

1. **fsync on the file is not sufficient for a newly created ledger.** The file's
   existence is not durable until the **parent directory** is fsynced. A crash in
   that window leaves a missing ledger, which correctly refuses ARMED — for a
   reason nobody will diagnose.
2. **Per-record framing.** Each record carries a length and a checksum so a torn
   tail is *detected* rather than inferred from a parse failure. §13 governs what
   is then done with it.

**No other component appends.** Not the checker, not the adapter, not the model
path. The standing rule that the checker must not call WriteBus directly applies
here in the same shape: a validation tool that can move the fence is no longer a
validation tool.

### Amendment A — the gate is on ARMED, not on starting

*Proposed and accepted 2026-09-09, in that order. Settles the question this
contract left open, and lands `PENDING_AMENDMENTS.md` entries 2a, 2b and 2d.*

`flock` returning success and `fsync` returning success are claims about the
**mount**, not about the file. On a mount that does not exclude, two coordinators
with different configured directories both acquire successfully, so the check
*can I acquire my own lock* answers yes in exactly the case that matters. §9 as
originally written left it open whether that condition refuses ARMED or refuses
the process.

**It refuses ARMED. The coordinator starts.**

- SHADOW does not append, reserve, fsync, or claim exclusive ownership. It
  exercises none of the capabilities in question.
- Refusing to start would discard useful observation because a capability the
  process is not using is unavailable.
- ARMED still fails closed. Nothing is weakened.
- This is §9 as accepted, made explicit rather than changed: failure to acquire
  ownership refuses ARMED and does **not** refuse SHADOW.

**The refusal is published at startup, not discovered at the moment of arming.**
A mode gate that stayed silent until someone tried to arm would be the same
design with the operator's cost moved ten minutes later, for no gain.

```json
{
  "mode": "SHADOW",
  "armed_capability": "UNAVAILABLE",
  "armed_refusals": ["LOCK_EXCLUSION_UNATTESTED",
                     "RESERVATION_DURABILITY_UNATTESTED"],
  "ledger_readability": "AVAILABLE",
  "shadow_fidelity": "DEGRADED_FILESYSTEM_NOT_ARMABLE"
}
```

### Amendment A — four states, never collapsed

| condition | startup | SHADOW | ARMED |
| --- | --- | --- | --- |
| ledger readable, allowlisted filesystem | start | full fidelity | eligible on the other conditions |
| ledger readable, unlisted filesystem | start degraded | read-only observation | refused |
| ledger missing or structurally unreadable | start degraded | no seeded simulation; **explicitly unavailable**, not silently empty | refused |
| torn tail | start degraded | surfaces the unknown reservation | refused until reconciled (§13) |

Row 3 is the one that must not collapse into row 1. A SHADOW that cannot seed is
not a SHADOW with nothing to seed from: it is an observation whose fidelity is
unknown, and reporting it as full fidelity would make §12's whole argument false
in the one case where it matters.

### Amendment A — attestation inspects the mount

The coordinator identifies the **filesystem type backing the configured
directory** at startup and refuses ARMED on anything not allowlisted. It does not
infer capability from a successful `flock` (see above), and it does not attempt
to demonstrate exclusion, which would require a second process.

| | |
| --- | --- |
| **allowlisted** | `ext4`; `xfs` and `btrfs` only once explicitly tested on this deployment |
| **refused for ARMED** | `drvfs`, `9p`, `cifs`, `nfs`, `fuse`, `overlay`, and anything unrecognised |

The list is deliberately narrow and will refuse some sound configurations. That
is the correct direction: a refused ARMED on a good host is recoverable by
extending the allowlist after testing; an accepted ARMED on a host that does not
exclude **is** the race §3 exists to prevent, arriving one level below where the
mutex can see it. It also states the requirement in terms an operator can act on,
which *verify your lock semantics* does not.

The observed type is recorded, not only the verdict, so an operator can see what
was refused rather than only that something was.

### Amendment A — two claims, one lookup

```text
lock_exclusion:          ATTESTED_BY_FILESYSTEM_POLICY | UNATTESTED
reservation_durability:  ATTESTED_BY_FILESYSTEM_POLICY | UNATTESTED
```

These fail on the same mounts *here*, so one lookup supplies both. They remain
two claims. A filesystem that fsyncs honestly and locks badly, or the reverse, is
entirely ordinary; §7's reserve-before-write depends on the first and §3's mutex
on the second, and one precondition covering both would tie two independent
guarantees to whichever was checked. **One lookup is an implementation
convenience and not a merge of the requirements.**

### Amendment A — the codes were renamed before minting

The names proposed for these codes were `LOCK_SEMANTICS_UNVERIFIED`,
`FSYNC_DURABILITY_UNVERIFIED` and `VERIFIED_BY_FILESYSTEM_POLICY`. The
substring-root check required by `SCYTHE_VERDICT_VOCABULARIES.md` §3 refuses
them: **`UNVERIFIED` is a merit-side token**, one of `COORDINATE_KINDS` in
`scythe_invariant_ledger`.

The collision is semantic and not only lexical, which makes it worse than the
`INDETERMINATE` case. The merit `UNVERIFIED` means *present, and its authority is
not established* — very nearly what these codes want to say about a filesystem.
A reader meeting both would have every reason to think they were one concept.

Renamed to `UNATTESTED` / `ATTESTED_BY_FILESYSTEM_POLICY`. `armed_capability`
takes `AVAILABLE` / `UNAVAILABLE` rather than the more natural `ELIGIBLE` /
`REFUSED`, because `PROMOTION_ELIGIBLE` and `PROMOTION_REFUSED` are merit
dispositions. The rule says the name is changed rather than argued for, and these
were changed.

---

## 10. Restart

| rebuilt from the ledger | not rebuilt |
| --- | --- |
| the identity set | the window (§6) |
| unresolved reservations | the audit ring (in-memory, bounded, by design) |
| the generation totals (§11) | |

**A missing ledger is a missing fence, and ARMED must be refused**
(`LEDGER_UNAVAILABLE`). Starting from an empty identity set after the file is
lost would silently re-enable every promotion ever made. The empty-file case and
the missing-file case are therefore distinguished: a ledger created by this
contract's own initialization is empty and valid; a ledger that is absent where
one was configured is a refusal.

---

## 11. Two ceilings, one mechanism

A crash loop restarts the process, and §6 resets the window on restart. Without a
second bound, a crash loop would refill the budget on every restart — the
amplification the breaker exists to prevent, arriving through the breaker's own
reset path.

Both bounds are **one-sided ceilings on durable totals**, clock-free, and are the
`BoundedCeiling` invariant class this repository already defines. Two ceilings
over one durable structure, not two kinds of ceiling.

```
C1   reservations made in this generation        <=  RESERVATION_CEILING
C2   outstanding unreconciled reservations     <=  UNRESOLVED_CEILING
```

**C1 counts reservations, not confirmed writes.** If it counted only successes,
an adapter timing out forever would burn unlimited reservations while the counter
stayed at zero — the amplification arriving through the counter that exists to
stop it.

**C1 has no per-boot scope of any kind.** It is a running total over the
generation, reset only by an explicit operator act that starts a new one, under
the same ledger authority as reconciliation (§8). Any per-boot scoping would
reintroduce the refill path this section closes.

**C2 is cleared by reconciliation** (§8), not by time and not by restart.

**On the value of C1.** It is not a rate limit and must not be tuned near the
expected promotion rate. It is a *this has gone wrong* bound: roughly an order of
magnitude above the highest plausible legitimate lifetime total for one
generation. **If it ever fires during correct operation, it was set wrong — and
that is its calibration test.**

Both refusals are executability codes (§5): `DURABLE_CEILING_REACHED`,
`UNRESOLVED_CEILING_REACHED`.

---

## 12. SHADOW against the durable ledger

SHADOW **reads** the ledger and **appends nothing**. It seeds its simulated
identity set from real history.

Without seeding, SHADOW measures a system that does not exist: starting from an
empty history it systematically under-counts `WOULD_BE_REFUSED` and over-counts
`WOULD_PROMOTE`, and the whole purpose of the shadow slice is to predict what
ARMED would do. This is the same argument that gave SHADOW a simulated budget in
the first place.

SHADOW does not take the exclusive lock, so it may run beside an ARMED writer.
It therefore reads a file that is being appended to, and must tolerate a torn
tail exactly as startup does (§13).

**Testable property:** the ledger file is byte-identical before and after a
SHADOW run.

---

## 13. A torn tail is unresolved, not absent

A crash mid-append leaves a partial record. §9's framing makes that detectable.
What follows is the same question as the adapter's lost acknowledgement, and gets
the same answer.

> **A torn tail loads as an unresolved reservation of unknown identity. It is
> never discarded.**

Discarding it would assert that no reservation was made — which is exactly
write-first semantics reappearing at the storage layer, one level below where §2
excluded it.

Because its identity cannot be read, it cannot fence anything. So unlike a
well-formed unresolved reservation, which blocks only its own identity (§7), a torn tail
**refuses ARMED until it is reconciled** (`LEDGER_TORN`) and counts toward C2.
This is not a special case: it is §7's containment argument applied to a
reservation whose containment radius is unknown.

**Growth is bounded by refusal, not by pruning.** At the current budget the
ledger accrues on the order of 1,150 records/day. Compaction is a separate slice
with its own correctness argument — pruning an identity set is deleting a fence,
and it must be shown that the pruned identities can never recur. Until then C1
serves as the bound.

---

## 13a. Amendment B — the writer's answer has three states, not two

*Proposed 2026-09-10 and accepted 2026-09-10, in that order. Settles what §3
step 7 and §7 left open: what an exception from the writer means, and what
`accepted=False` actually claims. Touches §2, §3, §5, §6 and §7; each carries a
pointer back to here.*

### B.1 A Boolean cannot carry the answer

`WriteResult(accepted: bool)` has to represent three outcomes and has two
values:

```python
CREATED     = "CREATED"       # the adapter attests a record was created
NOT_CREATED = "NOT_CREATED"   # the adapter attests that none was
UNKNOWN     = "UNKNOWN"       # the adapter cannot say
```

§2 resolved the shortfall by collapsing the last two into `accepted=False` and
fencing on the strength of the ambiguity. That is right where the ambiguity is
real and wrong where it is not: an adapter that receives an explicit rejection
**knows** no record was created, and reporting that identically to a timeout
discards the knowledge at the one boundary that has it.

`NOT_CREATED` requires a definite attestation. Timeout, lost acknowledgement,
transport interruption, cancellation, an unexpected exception and any ambiguous
negative response are `UNKNOWN`. **An adapter that is unsure reports `UNKNOWN`,
never `NOT_CREATED`** — the direction of that default is the whole safety
property, and an adapter author who gets it backwards produces duplicates
silently.

### B.2 An exception from the writer is `UNKNOWN`

Caught, never re-raised. No terminal record is written and the identity stays
`RESERVED`. Two reasons, and the second is the one that is easy to miss:

- An exception is not evidence that nothing was written. A socket that raised on
  read may have delivered its request.
- `evaluate()` raising hands the caller **no idempotency key**, and the key is
  the only handle on a reservation that now exists and fences an identity. A
  caller cannot reconcile what it was never told the name of.

The returned shape:

```json
{"outcome": "PROMOTION_OUTCOME_UNRESOLVED",
 "executability_code": "IDENTITY_UNRESOLVED",
 "idempotency_key": "promotion:…",
 "detail": {"exception_type": "TimeoutError"}}
```

**The exception message is discarded, not truncated.** Adapter exception text is
arbitrary and carries endpoints, payload fragments and credentials; the class
name is the part that describes the apparatus. This is the standing rule about
what may cross a boundary, applied to a narrower pipe than the model path.

### B.3 What survives in §2, and what changes

The load-bearing definition survives unchanged, and is **strengthened**: it no
longer rests on the boundary's ambiguity. What changes is the argument for
retaining a reservation across a *definite* failure.

| the adapter says | terminal record | fences | on what ground |
| --- | --- | --- | --- |
| `CREATED` | `COMMITTED` | yes | the record exists |
| `NOT_CREATED` | `FAILED` | yes | §2's closing rule: re-promotion after a failure is an operator action, never an automatic retry |
| `UNKNOWN`, or an exception | none — stays `RESERVED` | yes | forced: the record may exist |

§2's paragraph beginning *"The reason it must be a claim on the identity"* now
describes the `UNKNOWN` row only. The other two rows fence for reasons of their
own, and both reasons are stronger than the one they replace.

An automatic retry on `NOT_CREATED` was rejected rather than overlooked: it is a
retry loop inside the coordinator, bounded only by the budget, and the budget
exists to bound a thousand distinct findings rather than one finding a thousand
times.

### B.4 `IDENTITY_UNRESOLVED` is identity-scoped, and is not `DUPLICATE_PROMOTION`

A second evaluation of an unresolved identity returns the executability code.
Every unrelated identity proceeds, subject to the shared budget. §5 and §7
already say this; Amendment B adds only that the coordinator must **return** it
rather than merely honour it.

The fencing behaviour is identical under either name, which is exactly why the
wrong one here would never be noticed: the identity map makes the fence
effective, and the code is the only thing that makes its reason honest.

### B.5 The audit ring is not the unresolved set

`MAX_AUDIT_RECORDS = 64`, bounded by design (§6). A reservation whose writer
never answered ages out of the ring while the reservation itself persists
forever. The ring therefore cannot answer *which reservations are unresolved*.

State belongs in the identity map. The ring stays a bounded observation of
transitions, and §7's requirement that unresolved reservations be surfaced and
counted is met from the map.

### B.6 `_Posture` is §6's in-memory expression

```python
@dataclass
class _Posture:
    identities: Dict[str, str]   # identity -> RESERVED | COMMITTED | FAILED
    window: Deque[int]           # monotonic_ns, pruned from the left
```

Instantiated twice and independently, real and shadow. Named for the posture
rather than for either structure, because the alternative is a mode ternary at
five call sites and that is where such a bug hides.

**Exactly three stored states.** `RESERVED` with no terminal record *is* the
in-memory representation of unresolved; a fourth stored `UNRESOLVED` would be a
second name for one fence with no observable transition between them.

The identity map does not prune. The deque does, from the left, inside the lock
— which makes the budget check O(1) amortised where it is currently an O(n) scan
of an unbounded list, so §6's split **shortens** the critical section rather
than lengthening it. Lifetime growth of the map is §11's problem and durability
is §9's; neither is solved here and neither is hidden.

### B.7 Accessor lock discipline

Public `promoted_keys`, `policy_keys` and `status()` acquire the coordinator
lock. The critical section uses `_..._unlocked` helpers only.

This is §4's *nothing inside the critical section may call out*, applied to the
object's own public surface — which is the direction a later refactor
reintroduces, because calling your own property does not look like calling out.
A plain `Lock` turns the mistake into a deadlock rather than a wrong answer, so
the helpers must exist rather than be a convention.

### B.8 The terminal update reacquires the lock

Writer I/O happens with the lock released (§3). The `RESERVED` →
`COMMITTED`/`FAILED` transition reacquires it.

**The window is not touched at terminal time.** It was spent at reservation (§3
step 5), and spending it again would count one promotion twice — inflating the
rate measure with the adapter's latency rather than with the promotion rate.

### B.9 A deadlock is tested without stranding a thread

The re-entry test uses a subprocess, or asserts on a non-blocking second
acquisition of the plain lock. A test that leaves a deadlocked thread inside the
main test process leaves the lock held for every test that follows it.

---

## 13b. Amendment C — `RETRY_REQUIRES_OPERATOR`

*Proposed 2026-09-10. **Not yet accepted.** Names the executability code that
Amendment B's third state requires and §5 does not list. Touches §5 and §2.*

### C.1 The state Amendment B created, and the answer it left missing

Before Amendment B, `accepted=False` covered a rejection and a lost
acknowledgement alike, and one fence with one reason covered both. B.1 split
them. `NOT_CREATED` now means the adapter **attests that no record was
created** — and that makes a second evaluation of a failed identity a question
with three plausible answers, none of them true:

| candidate | why it is false |
| --- | --- |
| `DUPLICATE_PROMOTION` | merit; claims the finding is already in the graph, and B.3 says it is not |
| `IDENTITY_UNRESOLVED` | executability; claims we do not know, and here we do |
| release the identity | not a refusal at all — an automatic retry loop bounded only by the budget |

The third is the one worth naming as rejected rather than overlooked. The budget
exists to bound a thousand *different* findings arriving at once (§6); it was
never a retry limiter, and a failing adapter would consume it repeatedly on one
finding while the graph stayed empty.

### C.2 The code

```
RETRY_REQUIRES_OPERATOR
```

**Executability**, and §5's set gains it. It says nothing about the finding,
which may be entirely promotable; it says the apparatus recorded a definite
failure and will not act again on its own. The repair is an operator's, which is
the question the two vocabularies are told apart by.

Returned when, and only when, the identity's stored state is `FAILED` — that is,
a `RESERVED` resolved by a `NOT_CREATED` attestation (§13a B.3). An identity
that is `RESERVED` with no terminal record returns `IDENTITY_UNRESOLVED`, and an
identity that is `COMMITTED` reaches the policy and returns
`DUPLICATE_PROMOTION`. Three states, three answers, and none of them borrowed.

### C.3 It fences that identity and nothing else

Exactly the containment §7 already gives an unresolved reservation, for the same
reason. One failed write is contained: that identity is blocked, no duplicate
can reach the graph, and every unrelated identity proceeds subject to the shared
budget.

A global halt on a definite failure would be worse here than for an unresolved
one, because a definite failure is the *better*-understood condition — the
apparatus told us exactly what happened. Converting the best-understood outcome
into a total stop would make the system most fragile where it is best informed.

The rate of failed writes is a different signal and is not identity-scoped. It
is bounded by C2's mechanism in §11, not by this code.

### C.4 It authorizes no retry operation

**This amendment names a refusal. It creates no way out of it.**

There is no operation an operator can perform against a `FAILED` reservation:
§8's reconciliation is defined against unresolved reservations only, which is
the gap `PENDING_AMENDMENTS.md` entry 4 has carried since the read path was
written. The operator-controlled exit remains **slice 7**, and until it exists
`RETRY_REQUIRES_OPERATOR` describes a repair that cannot yet be carried out.

That is a worse state than having the exit, and a better one than the refusal
being silent or wearing a false name. It is recorded here rather than left to be
discovered: a code whose repair does not exist should say so in the document
that mints it, or the next reader will assume the repair is somewhere they have
not looked.

---

## 14. What this does not do

- It does **not** make the graph write idempotent. It prevents *this coordinator*
  from writing an identity twice. Anything else invoking the adapter is outside
  the fence.
- It does **not** detect a record present in the graph but absent from the
  ledger. The ledger is not a mirror of the graph and must never be read as one.
- It does **not** adjudicate unresolved reservations. It surfaces them and
  records an operator's determination (§8).
- It does **not** synchronize two coordinators. It refuses the second (§9).
- It is **not evidence.** It records claims on identities. A reservation is not a
  finding, and a promotion is not a confirmation — the verdict was already true
  or false before anything was written.

---

## 15. Acceptance tests

Step 4 is not authorized until these pass. Concurrency tests are stated as
observable outcomes, not as timing.

**Atomicity**

1. N threads evaluate one identity → exactly 1 write, N−1 `DUPLICATE_PROMOTION`.
2. N threads evaluate N distinct identities, budget B < N → exactly B promotions,
   N−B `PROMOTION_SUPPRESSED`.
3. A lock-observing proxy asserts the writer is invoked with the coordinator lock
   **not** held, and the reservation appended with it held. Deterministic; does
   not depend on scheduling.
4. A writer that re-enters `evaluate()` does not deadlock.
5. A blocked writer on identity A does not block evaluation of identity B.
6. 1 and 2 hold identically in SHADOW against the simulated structures.

**Durability**

7. The writer, when called, observes the identity already present and fsynced in
   the ledger file.
8. Creating a new ledger fsyncs the parent directory, not only the file.
9. A ledger holding a lone `RESERVED` record → that identity alone is refused on
   restart with `IDENTITY_UNRESOLVED`; other identities still promote; ARMED is
   not halted.
10. Restart rebuilds the identity set; a previously committed identity is refused.
11. A missing ledger file refuses ARMED rather than starting empty.
12. A second coordinator on the same ledger path refuses ARMED.
13. A configured path in the working tree or under `/tmp` is refused.

**Capability attestation (Amendment A)**

*A collision worth naming rather than renumbering: these tests were `13a`–`13e`
before Amendment B took the section number `§13a`. Test numbers here carry no
`§`, section numbers always do, and renumbering either would break references
already written down. Introduced by Amendment B, noticed by Amendment C.*

13a. An unlisted filesystem starts the coordinator, permits SHADOW, refuses
    ARMED, and publishes both `LOCK_EXCLUSION_UNATTESTED` and
    `RESERVATION_DURABILITY_UNATTESTED` — two codes, not one.
13b. The capability assessment is published at startup, before any attempt to arm.
13c. The four states of Amendment A are distinguishable; in particular a ledger
    that cannot be read reports `shadow_fidelity` as degraded and never as full.
13d. Attestation reads the filesystem type of the configured directory. A
    successful `flock` on an unlisted filesystem attests nothing.
13e. The observed filesystem type is recorded alongside the verdict.

**Torn tail**

14. A torn tail is detected by framing, loads as unresolved with an unknown
    identity, is not discarded, refuses ARMED, and counts toward C2.

**Reconciliation**

15. `RECONCILED_COMMITTED` keeps the identity fenced; `RECONCILED_RELEASED` frees
    it; nothing else frees it.
16. Reconciliation clears C2 and does **not** refund C1.
17. Reconciliation requires the ledger authority and is refused without it.

**Ceilings**

18. C1 counts reservations: a writer that always fails still advances it.
19. C1 does not reset on restart; only a new generation resets it, and only under
    the ledger authority.
20. C1 and C2 refuse with executability codes, never policy refusal reasons.

**Vocabularies**

21. The merit and executability sets are disjoint, and `status()` reports their
    counts separately without a summed total.
22. `DUPLICATE_PROMOTION` and `IDENTITY_UNRESOLVED` are distinct, in different
    sets, and reachable in the states §5 describes.

**SHADOW**

23. A SHADOW run leaves the ledger byte-identical, and seeds its simulated set
    from it.
24. `status()` reports `budget_window_survives_restart: false`, and the window is
    empty after a restart.
25. Ledger records carry both `monotonic_ns` and `utc_display`, and no decision
    path reads the UTC field — AST scan, matching the existing scope tests.

**Amendment B (§13a)**

26a. A writer returning `UNKNOWN` leaves the identity `RESERVED`, writes no
    terminal record, and returns `IDENTITY_UNRESOLVED`.
26b. A writer that raises does the same, and `evaluate()` does not propagate the
    exception.
26c. The returned detail carries the exception **class name only**; a message
    containing a URL, a payload fragment or a credential does not appear in the
    result, the audit entry or `status()`.
26d. `NOT_CREATED` writes `FAILED` and fences; a second evaluation of that
    identity is refused.
26e. A second evaluation of an unresolved identity returns
    `IDENTITY_UNRESOLVED` and **not** `DUPLICATE_PROMOTION`; an unrelated
    identity in the same posture proceeds.
26f. The unresolved set is recoverable from `status()` after more than
    `MAX_AUDIT_RECORDS` intervening events have aged the transition out of the
    ring.
26g. Two concurrent evaluations of one identity produce exactly one reservation;
    the test forces the interleaving and fails before the lock is added.
26h. Concurrent evaluations of distinct identities against a nearly-spent budget
    produce exactly the budget, not more.
26i. The writer observes the coordinator lock **not** held; a writer that
    re-enters `evaluate()` completes rather than deadlocking.
26j. A re-entrant call from inside the critical section deadlocks rather than
    answering — asserted without stranding a thread in the test process.
26k. SHADOW loses the same race before the lock and wins it after; the lock is
    not conditional on mode.
26l. The window is spent once per promotion: a slow writer does not consume
    budget twice.
26m. AST — no body inside the critical section names a lock-acquiring public
    accessor.

**Amendment C (§13b)**

27a. A second evaluation of a `FAILED` identity returns
    `RETRY_REQUIRES_OPERATOR`, and neither `DUPLICATE_PROMOTION` nor
    `IDENTITY_UNRESOLVED`.
27b. The three stored states produce three distinct answers, reachable in one
    test that walks all three.
27c. A failed identity fences itself and nothing else: an unrelated identity in
    the same posture proceeds.
27d. `RETRY_REQUIRES_OPERATOR` is counted as executability and never appears in
    `merit_refusals`.

---

## 16. Decisions

Recorded rather than left open, so the reasoning survives the review that
produced it.

1. **An unresolved reservation blocks only its own identity.** Global halt was rejected:
   it converts a contained uncertainty into a total stop and creates pressure to
   clear unresolved reservations carelessly (§7). Rate is bounded separately by C2 (§11).
2. **C1 is a lifetime total over the generation, counting reservations.** Not
   per-boot in any form; not successes only (§11).
3. **The ledger path is explicitly configured, never the working tree, never
   `/tmp`,** and the parent directory is fsynced on creation (§9).
4. **The ceilings are coordinator-enforced, not policy refusal reasons** (§5).
5. **Reconciliation is a named, audited, authority-gated ledger operation** (§8).
   Added because §7's "never auto-released" otherwise meant "released by hand".
6. **The state is `UNRESOLVED`, not `INDETERMINATE`** (§7). Disjoint sets whose
   names are not visibly disjoint are disjoint only in the document.
7. **Filesystem capability is a mode gate on ARMED, not a startup refusal, and is
   published at startup** (§9 Amendment A). Refusing to start would discard
   observation because of a capability SHADOW does not use; staying silent until
   someone arms would move the operator's cost later for no gain.
8. **Attestation inspects the mount against an allowlist, not the success of an
   acquisition** (§9 Amendment A). A non-excluding mount grants every lock.
9. **The writer's answer has three states** (§13a B.1). A Boolean had to carry
   `CREATED`/`NOT_CREATED`/`UNKNOWN` and could not; collapsing the last two
   discarded the one piece of knowledge the boundary actually has.
10. **An exception from the writer is `UNKNOWN`, caught and not re-raised**
    (§13a B.2). Raising returns no idempotency key, and the key is the only
    handle on a reservation that already fences an identity.
11. **The exception message is discarded rather than truncated** (§13a B.2).
    Truncation is a length policy applied to arbitrary adapter text; the class
    name is the part that is about the apparatus.
12. **Exactly three stored identity states** (§13a B.6). A stored `UNRESOLVED`
    beside `RESERVED` would be two names for one fence with no observable
    transition between them.
13. **`NOT_CREATED` still fences, and is not retried automatically** (§13a B.3).
    An automatic retry is a loop bounded only by the budget, which exists to
    bound many findings once rather than one finding many times.
14. **A definite failure gets its own code rather than borrowing one** (§13b).
    Three stored states need three answers; the two existing codes are each
    false about a failed identity, and in opposite directions.
15. **The code is minted without its repair, and says so** (§13b C.4). A refusal
    whose repair does not exist should be named in the document that mints it,
    or the next reader assumes the repair is somewhere they have not looked.

---

## 17. Slice order

Following the accepted pattern — amendment first, implementation only after the
amendment is accepted:

1. `SCYTHE_VERDICT_VOCABULARIES.md`, accepted.
2. This contract, accepted.
3. Coordinator executability vocabulary (§5), over the existing in-memory
   structures. **Not yet landed** — the durable-ledger read path of slice 5
   landed first, out of order.
4. Atomic reservation (§3, §4, §13a) — closes the race without introducing a
   file. Depends on slice 3 for `IDENTITY_UNRESOLVED` and the unresolved audit
   event.
5. Durable ledger: format, framing, read path, restart (§7, §9, §10, §13).
   Read path **landed** 2026-09-10 (`bd9e9ca`), ahead of slices 3 and 4, and
   stays disconnected from the coordinator until slice 6.
6. Durable ledger: write path, fsync discipline, ownership lock.
7. Reconciliation and generations (§8, §11).
8. Ceilings C1 and C2 (§11).
9. Execution adapter with one fixed WriteBus schema.
10. Live SHADOW observation.
11. Separate explicit authorization before any ARMED graph mutation.

**Slices 4 and 5 stay separate**, and this is the boundary to protect if anything
is compressed: the lock is correct and testable without durability, and
durability is where the subtle bugs are.
