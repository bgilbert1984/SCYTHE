# Promotion Execution Contract

```text
Status:                 ACCEPTED — nothing implemented
Accepted:               2026-09-08, after review amendments cafda8d
Amendment A:            §9 filesystem capability — ACCEPTED 2026-09-09,
                        amendment 42cc6b5
Amendment B:            §13a three-state writer result — ACCEPTED 2026-09-10
Amendment C:            §13b RETRY_REQUIRES_OPERATOR — ACCEPTED 2026-09-10
Amendment D:            §13c sequence, generation, gated write — ACCEPTED
                        2026-09-10
Amendment E:            §13d ownership, seeding, durability — ACCEPTED
                        2026-09-10, E.6 on the drafter's recommendation
Amendment F:            §13e reconciliation by supersession — ACCEPTED
                        2026-09-10, after review strengthened F.6 and added
                        F.10
Amendment G:            §13f the two ceilings, declared — ACCEPTED 2026-09-11,
                        after review renamed the C2 refusal
Amendment H:            §13g the execution boundary — ACCEPTED 2026-09-12
Amendment I:            §13h what live SHADOW can observe — ACCEPTED
                        2026-09-12, after review added I.2a, the bound maxima
                        and I.4a
Amendment J:            §13i derived evidence — ACCEPTED 2026-09-12, after
                        review qualified J.3 and set exact bounds
Amendment K:            §13j record-rate bound — ACCEPTED 2026-09-12
Amendment L:            §13k the producer — ACCEPTED 2026-09-12, after
                        review corrected L.1
Amendment M:            §13l the run that is not a capture — PROPOSED
                        2026-09-12
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
graph (§8), never an automatic retry. §13e F.2 says what that action is and on
whose authority it rests. This holds for a definitely-failed write
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
| examples | `VERDICT_NOT_PROMOTABLE`, `CAPSULE_UNBOUND`, `DUPLICATE_PROMOTION` | `BUDGET_EXHAUSTED`, `DURABLE_CEILING_REACHED`, `OUTSTANDING_RESERVATION_CEILING_REACHED`, `IDENTITY_UNRESOLVED`, `RETRY_REQUIRES_OPERATOR`, `NOT_RECONCILABLE`, `ADAPTER_NOT_CONFORMANT`, `GENERATION_CHAIN_BROKEN`, `GENERATION_LINEAGE_FORKED`, `GENERATION_PUBLICATION_UNCERTAIN`, `LEDGER_UNAVAILABLE`, `LEDGER_NOT_OWNED`, `LEDGER_TORN`, `LEDGER_GENERATION_UNDECLARED`, `OWNERSHIP_LOST`, `RESERVATION_NOT_DURABLE`, `LOCK_EXCLUSION_UNATTESTED`, `RESERVATION_DURABILITY_UNATTESTED` |
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
  entire lifetime, acquired before ARMED is reachable. *Amended by §13d E.1: the
  lock is taken on a sidecar whose name is derived, because a ledger that does
  not yet exist cannot be opened to lock it. Amended again by §13e F.10: it is
  derived from the **lineage root**, not from one generation file, or two
  processes can lock two generations and each publish a successor.*
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
3. **One strictly increasing sequence across every record kind** (§13c D.1),
   allocated inside the critical section, and **never appended to a torn
   ledger** (§13c D.2).

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
| `next_seq`, as `last_seq + 1` (§13c D.1) | |
| the identity map, as a cache of the ledger (§13d E.4, E.5) | |
| the authoritative generation, from the supersession chain (§13e F.6) | |

*Amended by §13c D.3: an empty ledger is valid and readable, and refuses ARMED
under `LEDGER_GENERATION_UNDECLARED` because it declares no generation for C1 to
total over.*

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

Both bounds are **one-sided ceilings on durable totals**, clock-free, and are
ceilings of the kind `BoundedCeiling` describes — one-sided rather than
two-sided, and undeclared without a published accounting basis. *Amended by §13f
G.1: the class itself is **not** reused, because its violations are merit
findings and these refusals are executability codes.* Two ceilings over one
durable structure, not two kinds of ceiling.

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
*Amended by §13f G.3: C2 counts **unresolved** reservations — `RESERVED` with
neither a valid terminal nor a valid reconciliation record — across the whole
lineage, and generation closure does not clear it. `FAILED` does not count.*

**On the value of C1.** It is not a rate limit and must not be tuned near the
expected promotion rate. It is a *this has gone wrong* bound: roughly an order of
magnitude above the highest plausible legitimate lifetime total for one
generation. **If it ever fires during correct operation, it was set wrong — and
that is its calibration test.**

Both refusals are executability codes (§5): `DURABLE_CEILING_REACHED`,
`OUTSTANDING_RESERVATION_CEILING_REACHED`. Both are checked **before** the durable append and
a refusal writes no record (§13f G.5); both totals are seeded from the ledger
(§13f G.6); the values are contract-declared constants (§13f G.4).

---

## 12. SHADOW against the durable ledger

SHADOW **reads** the ledger and **appends nothing**. It seeds its simulated
identity set from real history.

Without seeding, SHADOW measures a system that does not exist: starting from an
empty history it systematically under-counts `WOULD_BE_REFUSED` and over-counts
`WOULD_PROMOTE`. *Amended by §13h I.1: the purpose stated here — to predict what
ARMED would do — is not achievable, because ARMED's rate depends on a requester
that does not exist. SHADOW observes the apparatus instead, and says so in its
own record.* This is the same argument that gave SHADOW a simulated budget in
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

*Proposed 2026-09-10 and accepted 2026-09-10, in that order. Names the
executability code that Amendment B's third state requires and §5 does not list.
Touches §5 and §2.*

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

## 13c. Amendment D — what the writer must know before it may write

*Proposed 2026-09-10 and accepted 2026-09-10, after review strengthened D.2,
D.3 and D.4. Settles `PENDING_AMENDMENTS.md` entries 6 and 7, whose trigger is
slice 6, and records one finding and one scope division that working out
`next_seq` produced. Touches §9, §10 and §17.*

### D.1 The record sequence (entry 6)

> **One sequence across every record kind, the header included, strictly
> increasing. Gaps are permitted.**

The reader has enforced this since slice 5 and no accepted document has said it,
which is the shape `PENDING_AMENDMENTS.md` exists to prevent standing.

`next_seq` has to be answerable from **the last record of the file**, whatever
kind that record is. A per-kind counter makes *the last record* a question with
three answers, and a writer that had to scan for the last record of its own kind
would be reading the whole file to append one line. Strictly increasing gives
global uniqueness for free and subsumes the narrower duplicate-reservation check
the reader carried before.

**Gaps are permitted deliberately.** A gap is what a writer that took a sequence
number and crashed before framing the record leaves behind. Refusing gaps would
make a lost record render the entire ledger unreadable rather than merely lost —
converting the recoverable failure into the unrecoverable one, which is the
inversion this contract exists to prevent.

**Allocation is inside the critical section.** `next_seq` is taken under the
coordinator lock, in the same section as the reservation (§3 steps 3–4). Two
evaluations allocating outside it would reproduce §3's race one level down,
where the reservation is atomic and the number written beside it is not.

§10 gains `next_seq` to its rebuilt-from-the-ledger column: it is
`last_seq + 1`, read at startup, and never persisted separately. A counter kept
beside the ledger is a second answer that can disagree with the first.

### D.2 A torn ledger is never appended to

**Found by asking where `next_seq` comes from after a crash**, which is the
question §13 does not reach.

§13 says a torn tail loads as unresolved and is not discarded. That invites the
reading that the writer simply carries on after it. It must not, and the reason
is the reader's own rule: slice 5 treats a malformed record with well-formed
records *after* it as corruption rather than a torn tail, because the writer got
past it. So appending to a torn ledger converts a detected, contained torn tail
into `LEDGER_UNREADABLE` — losing every identity in the file to save one append.

`LEDGER_TORN` already refuses ARMED, so nothing changes in behaviour. What
changes is that the reason is written down, because the behaviour currently
holds by a coincidence of two rules meeting rather than by either one saying so.

The repair is reconciliation's (slice 7), not the writer's. **`LEDGER_TORN` is
append-ineligible, and the writer must neither truncate, repair, normalize nor
otherwise modify a torn ledger** — not the tail, not the records before it, not
the file's length. A writer that repaired the file it is about to append to is a
writer that can destroy evidence to make its own next operation legal, and it
would do so at exactly the moment the evidence is most load-bearing.

Evidence preservation wins over availability here, and the trade is deliberate:
a torn ledger that refuses every append is a coordinator that cannot promote,
which is recoverable. A torn ledger that has been tidied is a set of identities
nobody can audit, which is not.

### D.3 A ledger with no declared generation (entry 7)

§10 says a ledger created by this contract's own initialization is empty and
valid. §9 says the holder writes its `ProcessIdentity` into a header record. A
crash between the two leaves a zero-byte ledger: valid, fencing nothing, and
carrying **no generation identifier** — which is the thing C1 is a lifetime
total over (§11).

**It stays readable and valid, and it refuses ARMED**, under a new
executability code:

```
LEDGER_GENERATION_UNDECLARED
```

Not `LEDGER_UNREADABLE`: the file is perfectly readable and says, correctly,
that nothing has been promoted. Not silence either — a ceiling whose scope is
undeclared is a ceiling that cannot be enforced, and ARMED must fail closed on
every condition that can be observed.

SHADOW is unaffected and seeds from an empty fence, which is the truth about
that ledger rather than a degradation of it.

**The exit exists and is the writer's**: the owning coordinator writes the
header, and the generation is declared from that moment. Recorded here because
§13b C.4 established the rule — a refusal whose repair does not exist should say
so in the document that mints it — and this one's repair *does* exist, in the
slice that mints the code.

**The writer must not silently initialize, infer or adopt a generation because
the file is empty.** Establishing a generation is an explicit governed
operation, under the same authority that ends one (§11) and that performs
reconciliation (§8). An empty file is an invitation to treat initialization as a
side effect of the first write, and a generation that began as a side effect is
one nobody authorized, dated or can name the boundary of — while C1 is a
lifetime total over exactly that boundary.

So the refusal stands until a generation is established *deliberately*. A writer
that started one on its own would clear `LEDGER_GENERATION_UNDECLARED` by
removing the condition rather than by answering it.

### D.4 §17 slice 6 divides, and the write path lands behind a closed gate

§17's slice 6 reads *write path, fsync discipline, ownership lock*. Those are
two slices, and the boundary between them is the same one §17 already protects
between slices 4 and 5: **the mechanism is testable before its guard, and the
guard is where the subtle failures are.**

| | 6 | 6b |
| --- | --- | --- |
| record framing and append | ✓ | |
| fsync discipline, parent directory included | ✓ | |
| sequence allocation (D.1) | ✓ | |
| header and generation (D.3) | ✓ | |
| `fcntl.flock` ownership for the process lifetime | | ✓ |
| `LEDGER_NOT_OWNED` becomes reachable | | ✓ |

**Slice 6's write path must be unreachable without an ownership proof it cannot
yet obtain.** Every append is gated on one; slice 6 supplies no way to produce
one, so the only caller that can reach an append is a test that injects it —
the same seam `mounts` and `refused_prefixes` already use.

This is deliberately stronger than *we will add the lock next*. A write path
that works and is merely not yet guarded is a write path someone can call. One
that refuses every append until a later slice teaches it to prove ownership
cannot be used early by accident, and the refusal is `LEDGER_NOT_OWNED`, which
§5 already names.

#### The proof is a live scope, not a flag

A boolean, a token, or a recorded attestation would reintroduce the hazard the
gate exists to close, one level up: ownership could be attested, released, and
the append performed afterwards against a ledger this process no longer owns.
The check would pass and the guarantee would be gone — a
time-of-check-to-time-of-use hole with none of the difficulty that usually
accompanies one.

The ownership proof must therefore be:

- **bound to the specific ledger**, so a proof for one file cannot admit a write
  to another;
- **valid across the whole allocate-and-append critical section**, not
  re-checked at two points with a gap between them — the gap is the hole;
- **incapable of being retained and reused** after ownership ends: a reference
  held past the owning scope is inert, and using it is a refusal rather than a
  silent success;
- **not producible by production code before slice 6b**, and reachable in slice 6
  only through the test seam.

**Slice 6 exposes no generally callable append that takes
`ownership_attested=True`.** A parameter a caller can pass is a parameter a
caller can pass wrongly, and the reviewer of the call site cannot see whether
the claim was true. The append is internal and takes a live scope; slice 6b
invokes it from inside the scope where the lock is actually held, which is the
only place the scope can exist.

### D.5 What slice 6 still does not do

SHADOW does not append (§12), and the split above does not change that.
Reconciliation is slice 7 and this amendment supplies none of it — in
particular, `RETRY_REQUIRES_OPERATOR` names a repair that slice 6 **must not
fabricate merely because the token now exists**. Ceilings are slice 8, the
adapter slice 9, ARMED slice 11.

---

## 13d. Amendment E — how ownership is obtained, and what it costs to hold

*Proposed 2026-09-10 and accepted 2026-09-10, in that order. Settles what slice
6b needs and §9 does not supply: how a lock is acquired on a ledger that may not exist, what a
scope's liveness is derived from, and what the durable record's failure means
for the reservation beside it. Touches §3, §9, §10 and §17.*

Three of the four decisions below were taken in review. The fourth, E.6, is
mine and is flagged as such.

### E.1 The bootstrap, and the sidecar lock

Acquiring ownership requires opening the ledger. Creating the ledger requires
ownership. Opening a ledger that does not exist fails — verified on this host,
`ENOENT` — so §9's *acquire the lock, then write* cannot start from nothing.

**Ownership is taken on a sidecar lock file**, `<ledger path>.lock`, created if
absent. The ledger is then created and written under it.

**Its name is derived from the ledger path and is never configured.** That is
the load-bearing part. A separately configured lock file reproduces §9
Amendment A's failure in a new place: two coordinators, one ledger, two
different locks, both acquired, both satisfied. Deriving the name forecloses it
rather than documenting against it.

Its contents are **diagnostic only** — the owner's `ProcessIdentity`, written
for whoever is reading `lsof` at 3am. The authoritative owner is the header
record (§9), and a second authority would be a second answer to a question that
has one.

The alternative considered and rejected was opening the ledger itself with
`O_CREAT` to acquire the lock. It does not formally violate §13c D.3, which
governs generations rather than files — but it makes the ledger appear as a
consequence of asking whether we own it, which is the shape D.3 exists to
discourage one level up, and it makes `initialize()` vestigial.

### E.2 Ownership is two conditions, and the attestation gates the scope

§9 Amendment A established that `flock` returning success on a mount that does
not exclude attests nothing. A scope is therefore minted only when **both** hold:

1. `fcntl.flock(fd, LOCK_EX | LOCK_NB)` succeeded on the sidecar, and
2. the filesystem backing the configured directory is on the allowlist.

**The attestation gates scope creation, not only ARMED.** Checking it at arming
only would let slice 6b mint scopes on `drvfs`, where the lock does not exclude
— every check reporting success while the guarantee is absent, which is the
condition Amendment A was written for. A gate that is checked somewhere other
than where it is relied on is not a gate.

### E.3 A scope's liveness is derived from the owner, never local

§9 holds `flock` for the process's entire lifetime; §13c D.4 makes a scope valid
for the allocate-and-append span. Both are right about different objects: the
lock is acquired once and never re-acquired, and a **scope is a window onto that
holding**, minted per session and closed at the end of it.

So a scope is live when its session is open **and the owner still holds the
lock**. A scope whose liveness were purely local would keep authorising appends
after ownership ended — the fd closed, a lease expired on a filesystem that has
them — which is exactly the hole D.4 closed at the session level, reappearing
one level up. `OWNERSHIP_LOST` is that condition, and it is terminal for the
process: ownership is acquired once, so an owner that has lost it does not
reacquire.

Holding for the process lifetime also removes a lock-ordering question rather
than answering one: no `flock` is taken inside the coordinator's critical
section, so there is no third lock to order against coordinator → audit (§4).

### E.4 The durable append **is** the reservation

Once the coordinator holds a ledger, §3 step 4 writes the record and step 5
records it in memory, **in that order, and the second does not happen if the
first refused.**

A refusal — `LEDGER_TORN`, `LEDGER_NOT_OWNED`, `LEDGER_GENERATION_UNDECLARED`,
`OWNERSHIP_LOST` — ends the evaluation with that executability code and takes no
reservation at all. The alternative, reserving in memory and proceeding, yields
a fence that exists until the next restart and then does not, which is the
failure that looks like success.

The in-memory identity map becomes a **cache of the ledger** rather than the
authority. §6's table already says the identity set is durable; this is the
sentence that makes it true.

### E.5 Restart seeding belongs to slice 6b

`_Posture.identities` is rebuilt from `read_ledger()` at startup: committed →
`COMMITTED`, write-failed → `FAILED`, unresolved → `RESERVED`. The window is not
rebuilt (§6) — a persisted `monotonic_ns` from a previous boot is meaningless
rather than stale.

**This is not an optimisation and cannot be deferred.** A coordinator that
writes durably and seeds an empty identity set re-promotes every identity in the
file on its next start. That is strictly worse than the in-memory-only
coordinator that preceded it, because it has a durable ledger and ignores it —
the fence is visible in the file and absent from the behaviour, which is the
hardest kind of absence to notice.

§10 has required this rebuild since the contract was accepted. Slice 6b is where
it stops being a description.

### E.6 A write that lands and cannot be synced

*Not ruled on in review. This is the drafter's recommendation, marked so that a
later reader does not mistake it for a settled decision.*

`os.write` succeeds and `os.fsync` then fails. The record may be durable and may
not be: the `UNKNOWN` shape of §13a B.1, one layer down, and the bytes are
already in the file either way.

Proposed: **fence the identity in memory, refuse the evaluation with
`RESERVATION_NOT_DURABLE`, and stop appending in this process** until the ledger
is re-read. A process that cannot make a reservation durable should stop making
reservations — if it carries on, later records are durable while an earlier one
may not be, and the file stops supporting the ordering argument that
reserve-before-write rests on.

The identity is fenced rather than released for the usual reason: the record may
have reached the disk, and a released identity may be promoted again.

### E.7 What slice 6b contains

| in 6b | not in 6b |
| --- | --- |
| the sidecar lock and the ownership producer (E.1, E.2) | reconciliation (§8), which is slice 7 |
| owner-derived scope liveness (E.3) | the ceilings (§11), slice 8 |
| the coordinator's ledger connection (E.4) | the execution adapter, slice 9 |
| restart seeding (E.5) | any ARMED path, slice 11 |
| `RESERVATION_NOT_DURABLE` (E.6) | any repair of a torn ledger (§13c D.2) |

`FAILED` records become writable in 6b, and **the exit from a `FAILED`
reservation must not be invented here** merely because the records now exist.
That is `PENDING_AMENDMENTS.md` entry 4 and it belongs to slice 7.

Durable writes remain unreachable in production after 6b: SHADOW does not append
(§12), and ARMED cannot be constructed without an adapter. 6b makes the path
reachable *in shape*, which is why it can land before slice 9 rather than after.

---

## 13e. Amendment F — reconciliation supersedes, and never repairs

*Proposed 2026-09-10, strengthened in review, and accepted 2026-09-10. Settles
§8 and lands `PENDING_AMENDMENTS.md` entry 4, open since the read path and the
oldest debt in the queue. Touches §2, §8, §9, §10, §11, §13d and §17.*

**Name check, run before any token here was written.** The repository's
mechanical check is known-incomplete — its merit universe is a hand-listed five
pairs and omits `rf_capture_recovery` entirely (entry 5) — so this sweep was run
against the merit set **extended with all 21 tokens that module declares**, and
separately against every module-level token in the tree.

Every token below is clear against merit + recovery and against the
executability set. The whole-tree sweep produced two hits, both judged rather
than cleared:

| hit | judgement |
| --- | --- |
| `RECONCILED_COMMITTED` / `COMMITTED` | **not a collision, and deliberate.** `COMMITTED` is a ledger record kind and `RECONCILED_COMMITTED` is another one; they are the same family, which is what the naming is for |
| `GENERATION_CLOSED` / `CLOSED` | **not a collision.** `CLOSED` is an `rf_iq_ring` buffer state — a different subject in a different domain, and neither is a verdict vocabulary |

Those two are exactly the false-positive cost entry 5 predicts from a discovered
universe, shown rather than argued. The earlier candidate
`GENERATION_SUPERSEDED` was **not** clear: it collides with recovery's
`SUPERSEDED`, which the incomplete checker reported as clear.

**The governing rule, from which the rest follows:**

> Reconciliation is **append-only with respect to existing evidence.** A torn or
> failed generation is preserved byte-for-byte. A governed operation records why
> it was closed and establishes a successor. Nothing truncates, edits or tidies
> a predecessor.

That keeps §13c D.2 intact while finally making `RETRY_REQUIRES_OPERATOR`
actionable — the two things that looked like they were in tension.

### F.1 What may be reconciled, and what may not

| stored state | reconcilable | why |
| --- | --- | --- |
| `RESERVED`, no terminal record | **yes** | we do not know whether the record reached the graph, and an operator can look |
| `FAILED` | **yes** | the adapter attested no record was created; what remains is whether it may be promoted again |
| `COMMITTED` | **no** | the record exists. There is no uncertainty to resolve and nothing an operator could discover |

Reconciling a `COMMITTED` identity is refused with `NOT_RECONCILABLE`. It is not
an error of authority but of subject: the operation has nothing to act on, and a
ledger that let it proceed would be recording a decision about a settled fact.

### F.2 The two outcomes, and the asymmetry between them

```
RECONCILED_COMMITTED   the record exists in the graph; the identity stays fenced
RECONCILED_RELEASED    it does not; the identity becomes promotable again
```

**The two reconcilable states reach `RECONCILED_RELEASED` on different
authority, and the ledger records which.**

- A `FAILED` reservation is released on the **adapter's** attestation. `NOT_CREATED`
  already means *no record was created* (§13a B.1), so nothing further need be
  established. This is where Amendment B's third state finally pays for itself:
  under the old Boolean, no failure could ever be released, because none of them
  could be distinguished from a lost acknowledgement.
- An `UNRESOLVED` reservation is released on an **operator's inspection of the
  graph**. Nothing in this system knows whether the record landed, and no amount
  of ledger reasoning will produce the answer — it is out-of-band by
  construction.

Recording which of the two was relied on is what makes a later audit able to ask
the right question of the right party.

### F.3 Per-identity reconciliation appends; a broken generation is closed

Two operations, and the distinction is whether the *ledger* is healthy:

**Reconciling an identity** appends a record to the current generation. The
ledger is fine; one reservation was uncertain. No new generation.

**Closing a generation** is for when the ledger itself cannot be used: it is
torn, unreadable, or has reached a ceiling (§11). A successor is established and
the predecessor is never written to again.

The split answers a question §8 left joined. It also falls out of §13c D.2
rather than being chosen: a torn ledger **cannot be appended to**, so per-identity
reconciliation is not available there, and supersession is the only path that
exists. §9 Amendment A's shape again — the rule and the mechanism agreeing
because they are the same rule.

### F.4 A closed generation is immutable, and the successor says so

The successor's header carries a `supersedes` block:

```json
{"kind":"HEADER","seq":0,"schema":"…","frame_version":"pl1",
 "generation":"gen-2","owner":{…},
 "supersedes":{"generation":"gen-1","path":"…","bytes":40961,
               "digest":"blake2s:…","closed_because":"LEDGER_TORN",
               "closed_by":"operator-id","request_id":"…"}}
```

The predecessor is **never opened for writing again**. Its length and digest are
recorded in the successor, so the predecessor is not merely preserved by
convention — a later reader can prove it has not changed, and a predecessor that
*has* changed is detectable rather than quietly authoritative.

### F.5 The identity set is the union over the chain

**The successor does not copy the predecessor's identities forward.**

Copying would make the predecessor ceremonial — preserved, and load-bearing for
nothing. Reading the chain makes it load-bearing: a missing or altered
predecessor is `GENERATION_CHAIN_BROKEN`, which refuses ARMED, rather than a
silently smaller fence.

A **torn predecessor is still read for fencing**, up to its tear. Torn means *not
appendable*, never *not readable* — §13 has said that since the read path, and
this is the first place the distinction does real work.

### F.6 Publication, and what makes a successor real

*Strengthened in review. The first draft said the transition was atomic "when
the successor's header reaches disk", which is a claim and not a protocol. A
crash **during** the header write leaves a nonzero, malformed candidate, and
§13c D.3 settles only the zero-byte case. An `fsync` on the file does not make
its directory entry durable either.*

> **The predecessor remains authoritative until a complete, valid successor
> header is durably published.**

**Publication is a five-step protocol, and every step is load-bearing:**

1. **Create a temporary sibling** exclusively (`O_CREAT | O_EXCL`), under a name
   that is never scanned as a generation.
2. **Write the complete header.**
3. **`fsync` the file.**
4. **Atomically rename** it to its final deterministic name.
5. **`fsync` the containing directory.**

Why each one:

- A partial header written under the *final* name would be the malformed
  candidate D.3 cannot classify. Writing under a name nothing scans **removes
  the question instead of answering it** — the same move as deriving the sidecar
  name rather than validating a configured one.
- `fsync` before the rename, because a rename whose target contents are not yet
  durable can survive a crash as a correctly-named file full of nothing.
- `fsync` the directory, because the rename's **visibility** is not durable until
  the directory is. This is §9's *the parent directory must be fsynced* rule
  arriving in the place it actually bites.

**A temporary file supersedes nothing**, whatever its contents. Zero-byte,
partial, malformed, or complete-but-never-renamed — all the same: not a
generation, because it does not carry the name a generation is published under.

**Leftover temporaries are evidence and are preserved.** Never interpreted,
never repaired, never deleted — not even by a later attempt, which uses a
distinct temporary name derived from its own `request_id`. This is deliberately
*unlike* §13d E.1's sidecar rule, and the difference is the point: a sidecar is a
lock artefact and its removal costs nothing, while a partial successor is the
record of an attempt that may be the only evidence of what someone was doing
when the machine stopped.

**Retry rediscovers; it does not republish.** An operator whose acknowledgement
was lost retries, and the operation first looks for an already-published
successor of this predecessor:

- one exists with **this** `request_id` — report it and stop. The work is done.
- one exists with a **different** `request_id` — refuse. Publishing a second
  would be the fork this section exists to prevent.
- none exists — proceed with the protocol above.

### F.6a Selecting the authoritative generation

Startup reads every published generation header and follows the `supersedes`
chain. There is still no pointer file.

| valid successors of the predecessor | conclusion |
| --- | --- |
| zero | the predecessor is authoritative |
| exactly one | the successor is authoritative |
| more than one | `GENERATION_LINEAGE_FORKED` — refuse |

**A fork is never resolved by timestamp, filename, or directory order.** Every
one of those is a property of the filesystem's bookkeeping rather than of the
evidence, and choosing by one would make the fence depend on which file happened
to be written second. A fork means two processes believed they owned this
lineage, and the right response to that is to stop, not to pick.

### F.6b A publication whose durability is unknown

A crash — or an error — **after the rename and before the directory `fsync`**
leaves the initiating process unable to say whether the publication is durable.
The rename may be visible now and absent after a reboot.

That process must **stop all further generation operations** under
`GENERATION_PUBLICATION_UNCERTAIN`, and resuming requires re-reading durable
state rather than trusting anything it believes it just did. It is the shape of
§13d E.6 one level up: a process that cannot establish the durability of what it
wrote stops writing, rather than continuing on the assumption that worked last
time.

A later reader is unaffected — it sees either a published successor or not, and
both are well-defined states. The uncertainty belongs to the writer alone, which
is why the halt is process-local and not a property of the ledger.

### F.6c Closing ends the process's ownership

**The original reason for this no longer holds, and the rule survives anyway.**

The first draft argued that the successor is a different file with a different
lock, so ownership could not carry across. Under §13e F.10 the lock is on the
lineage and does not change, so that argument is gone.

The rule stands on a better one: re-entering service after a close requires
re-seeding the identity map from the new authoritative generation, and that is
precisely the startup path (§13d E.5). Keeping one path is worth more than
saving one restart, and a second in-process route to a seeded coordinator is a
second place for §13d E.5's failure — a coordinator that writes durably and
starts from an empty fence — to reappear.

### F.7 Idempotency, and what an operator may say

Every reconciliation carries an operator identity and a `request_id`, both
bounded.

**A repeat of the same `request_id` against the same identity is reported, not
re-applied**: the ledger returns the record it already holds. A *different*
`request_id` against an identity that is no longer reconcilable is refused with
`NOT_RECONCILABLE`, which is the state check of F.1 doing the work — the
identity moved, so the operation has nothing to act on.

Idempotency therefore rests on the stored state, and the `request_id` exists so a
repeat can be *recognised* rather than merely refused. An operator who lost a
response and retried should be told *this already happened*, not *you may not do
that*.

**The evidence is a closed token set. There is no notes field, ever.**

```
GRAPH_RECORD_FOUND         the operator inspected the graph and the record is there
GRAPH_RECORD_NOT_FOUND     the operator inspected the graph and it is not
ADAPTER_DENIED_CREATION    the adapter attested NOT_CREATED; no inspection needed
LEDGER_TORN                the generation was closed because it was torn
LEDGER_UNREADABLE          closed because it could not be parsed
CEILING_REACHED            closed because C1 or C2 was reached (§11)
```

No free text, no exception messages, no returned values, no operator prose. The
rule is the one §13a B.2 and §13d E.6 already apply to adapter output, and it
applies here for a stronger reason: this is the record of a human decision about
evidence, and a free-text field is where the reasoning goes to stop being
checkable.

### F.8 Ownership and attestation are required, unchanged

A reconciliation record is an append, so it needs a live ledger-bound scope —
which already requires `flock` **and** the mount attestation, checked on the
descriptor (§13d E.1–E.3). Closing a generation additionally requires ownership
of the predecessor, and creates the successor under it.

Nothing here weakens those. Reconciliation is the most authority-bearing
operation in this contract, and it is the last place to relax the conditions on
writing.

### F.9 What this does not do

It does not promote anything, and it does not call the adapter. `RECONCILED_RELEASED`
makes an identity promotable again; whether it is promoted is a later
evaluation's decision, under the budget and the ceilings like any other.

It defines no automatic trigger. Every operation here is an operator's act, as
§2 has said since the contract was accepted.

### F.10 Ownership covers the lineage, not one generation file

*This amends §13d E.1.*

E.1 derived the sidecar from **the ledger path**. With supersession that is
wrong: two processes holding locks on two different generation files can each
publish a successor, and each one's lock excludes nobody who matters. It is §9
Amendment A's failure a third time — the check succeeding while the thing it was
meant to exclude happens beside it.

**The sidecar is derived from the lineage root**, the configured stable identity
of the ledger family, and generation files are named deterministically beneath
it. One lineage, one lock, whatever generation is current.

The derivation rule from E.1 is unchanged and now matters more: the root is
configured, the sidecar and the generation names are **derived**, and nothing in
the configuration can name a second lock for the same lineage.

---

## 13f. Amendment G — the two ceilings, declared

*Proposed 2026-09-11 and accepted 2026-09-11, after review renamed the C2
refusal rather than judging it. Settles §11 before slice 8 is written. Touches
§5, §11 and §12.*

### G.1 `BoundedCeiling` is not reused

§11 said the ceilings "are the `BoundedCeiling` invariant class this repository
already defines." Read literally that contradicts §5, and the contradiction is
in merged code rather than in prose:

```python
# scythe_invariant_ledger._ceiling_findings
return (Finding(NUMERIC_BALANCE_EXCEEDED, ...),)
```

`BoundedCeiling` is merit-side apparatus. It is declared on a
`TransitionContract`, evaluated by `check_transition` over coordinate mappings,
and a violation is a **merit finding about a subject**. §5 says the ceilings are
enforced in the coordinator and are executability codes. Importing the class
would route an executability condition through the merit vocabulary's finding
type — the contamination §5 exists to forbid, arriving by reuse rather than by
carelessness.

**Only the discipline is reused**, and it is `BoundedCeiling`'s best idea: *a
ceiling with no published accounting basis cannot be checked; its headroom would
absorb effects nobody named.* So every ceiling here declares five things, and a
ceiling missing any of them is not declared:

| | what it must state |
| --- | --- |
| **subject** | what is counted |
| **accounting source** | where the count is read from |
| **scope** | over what the total runs |
| **reset rule** | what returns it to zero, and under whose authority |
| **refusal** | which executability code it produces |

### G.2 C1 — the catastrophe ceiling

| | |
| --- | --- |
| subject | **every durable reservation in the authoritative generation, regardless of terminal outcome** |
| accounting source | `RESERVED` records in that generation's file, a torn tail included |
| scope | one generation |
| reset | publication of a valid successor generation (§13e F.6) |
| refusal | `DURABLE_CEILING_REACHED` |

"Regardless of terminal outcome" is the load-bearing clause and §11 already gave
the reason: if C1 counted only successes, an adapter timing out forever would
burn unlimited reservations while the counter stayed at zero.

**The exit already exists.** `CEILING_REACHED` is a declared closure reason
(§13e F.7), so C1 firing is answered by an operator closing the generation and
the successor starting at zero. Slice 7 put the exit in place without inventing
the ceiling, which is the shape entry 4 was opened to catch the absence of.

### G.3 C2 — the operational circuit breaker

| | |
| --- | --- |
| subject | **unresolved reservations: `RESERVED` with neither a valid terminal record nor a valid reconciliation record** |
| accounting source | those records across the complete validated lineage |
| scope | the lineage, **not** the generation |
| decrement | a valid reconciliation record only, under §13e F.2's authority rules |
| refusal | `OUTSTANDING_RESERVATION_CEILING_REACHED` — see G.8 for why it is not named after the state it counts |

**`FAILED` does not count toward C2.** It is resolved and not unresolved: the
adapter answered. C1 already bounds it, and counting it here would make C2 fire
for an adapter that is working correctly and rejecting.

**C2 is lineage-wide, and this is the sharp edge.** If closing a generation
cleared it, an operator facing C2 could clear it by closing — converting *too
many writes went unanswered, the graph boundary is not working* into *close the
generation and carry on*. That is §11's own refill-through-the-reset-path
failure and §7's warning about an operator under pressure, arriving together. An
unresolved reservation in a closed predecessor was never reconciled; it is still
outstanding, and the chain is where it stays visible.

### G.4 The two are calibrated by opposite tests

> **C1 should never fire. C2 is meant to.**

§11 gave C1 its calibration test — *if it ever fires during correct operation, it
was set wrong.* That test is **wrong for C2**, which is not a *this has gone
wrong* bound but the designed detector for the condition §7 describes. C2 firing
is the mechanism working.

```
RESERVATION_CEILING = 10_000     # C1
UNRESOLVED_CEILING  = 32         # C2
```

**Contract-declared constants, not runtime knobs.** They are not configurable at
startup, by environment, or by argument. Changing either requires a reviewed
amendment, and `status()` publishes a **ceiling configuration identity** so a
value that changed without one is visible rather than inferred.

The basis, recorded so a later reader can argue with the arithmetic rather than
the number:

- **C1.** The budget is 8 per 600 s. A generation saturating that for a year is
  ~420 000, which would itself be pathological. Real promotions are findings,
  plausibly hundreds a year. 10 000 is roughly an order of magnitude above
  plausible and two below saturation. It is the value most worth revisiting
  against real data, and the one that costs nothing to have set too high.
- **C2.** Each one is an adapter call that never answered. A handful is a bad
  day; thirty-two is a boundary that has stopped working, and noticing later is
  worth nothing.

### G.5 Where the check happens, and what a refusal costs

**Both ceilings are checked before the durable append**, with the other
executability checks (§3 step 3). A reservation refused by a ceiling must not
reach the file — otherwise C1 would count the reservations it refused, and the
ceiling would raise itself every time it fired.

**A ceiling refusal writes no record at all**, durable or in-memory. This is
§13d E.4's rule reached from the other side: there, a refused durable append
takes no in-memory reservation; here, a refused in-memory check takes no durable
one.

### G.6 Seeded from the ledger, and checked against it

Both totals are **seeded from the ledger at startup** and maintained under the
coordinator lock, exactly as the identity map is (§13d E.4, E.5). Memory is a
cache of the file.

What keeps a cache honest is not a promise. **A test recomputes both totals from
the files and compares them to the maintained values**, and that comparison is
the guarantee — the same trade E.4 made, with the same guard.

### G.7 Durable and simulated are published apart

SHADOW cannot generate new C2 events: it has no adapter that could fail to
answer. **It can still observe a real one.** A coordinator starting in SHADOW
reads a lineage that may hold unresolved reservations from an earlier authorized
run, and reporting that as zero would be a false statement about the durable
record rather than an honest statement about simulation.

So the two are published under different names and different authorities, and
neither may stand in for the other:

```json
{"durable_reservations_in_generation": 1204,
 "durable_unresolved_in_lineage": 3,
 "shadow_simulated_reservations": 17,
 "shadow_unresolved_simulation": "NOT_SIMULABLE",
 "shadow_simulation_note": "SHADOW HAS NO ADAPTER THAT COULD FAIL TO ANSWER",
 "ceiling_configuration_identity": "blake2s:…"}
```

A SHADOW-only hypothetical C1 must not masquerade as the durable one. The
durable fields are read from the ledger under any mode; the simulated fields are
SHADOW's own arithmetic and are named as such.

### G.8 Three collisions the check found in §5's own codes

The mechanical check (slice 7) was run over the whole tree against these names.
§5's two ceiling codes were declared when this contract was accepted, **before
the check existed**, and have never been through it. Two hits, both judged:

| hit | judgement |
| --- | --- |
| `DURABLE_CEILING_REACHED` / `CEILING_REACHED` | **not a collision, and deliberate.** `CEILING_REACHED` is §13e F.7's closure reason; the two name the same ceiling event from two sides, and renaming either would hide the link an operator needs |
| `UNRESOLVED_CEILING_REACHED` / `UNRESOLVED` | **rejected, not judged.** See below |

**The C2 code was renamed rather than judged.** `UNRESOLVED_CEILING_REACHED`
would have added a *third* use of a term this repository already overloads: an
unclassified modulation in `rf_signal_family`, and a reservation whose write was
never answered here. A judgement would have made that permanent on the grounds
that the surrounding prose says "unresolved" — which is a reason to keep the
prose, not to keep the name.

**So the code names its subject and the prose keeps the state.** §11 and G.3
still define C2 over *unresolved* reservations, because that is what they are.
The refusal is `OUTSTANDING_RESERVATION_CEILING_REACHED`, which says what was
counted rather than borrowing the word for what each one is. Checked against the
discovered universe before adoption: its only hit is `CEILING_REACHED`, the same
deliberate link `DURABLE_CEILING_REACHED` has.

A judgement should record a resemblance worth keeping. Neither of the two above
is the *third* meaning of an already-doubled word.

A further candidate was rejected rather than judged: the SHADOW capability value
was going to be `SIMULATION_UNAVAILABLE_WITHOUT_ADAPTER`, which is a negation
pair with `AVAILABLE`/`UNAVAILABLE` in the store. `NOT_SIMULABLE` is clear.

### G.8a The checker cannot see a token declared twice

Finding the above exposed a limit in the mechanical check, and it is a real one.

The check holds tokens as a **set of strings**. One identical token declared by
two unrelated closed sets collapses to one member, so the check cannot tell
*this word means one thing* from *this word means two things in two domains*.
`UNRESOLVED` is exactly that case and the check reported it as a single
neighbour.

It is worse than blindness. `cross_set_collisions` clears a hit when the
candidate and the hit share a declaring set — and a token declared in several
sets clears against **any** of them, so a genuine cross-domain neighbour can be
skipped because the same word is also declared somewhere harmless.

The scale is not one case: **31 tokens in this tree are declared by more than one
module.** Some are deliberate — `COMMITTED` in the coordinator and the reader are
one concept in two places — and some are two concepts wearing one word.

**The repair:** the check keeps declaration provenance, a multimap of token to
declaring module and set, and a **cross-domain duplicate declaration requires a
recorded judgement** exactly as a collision does. `PENDING_AMENDMENTS.md` carries
the obligation, and it is implemented **before** any slice-8 ceiling code — the
check is what slice 8's names will be argued from, and it is known-incomplete
again until then.

### G.9 What slice 8 does not do

No adapter, no live SHADOW, no ARMED constructor, no new closure reason —
`CEILING_REACHED` exists — and no change to §13e's authority rules.

---

## 13g. Amendment H — the execution boundary

*Proposed 2026-09-12 and accepted 2026-09-12, in that order — but the proposal
merged before the acceptance, and that is recorded here rather than tidied away.
The omission was noticed on `main` and repaired by a forward acceptance commit,
not a rewritten merge: treating the conversation as acceptance while the
document said otherwise would have left a contract contradicting itself, which
is worse than a visible two-step. Nothing in H changed between the two.*

*The first contract in this document whose subject is **outside this
repository**. Every previous amendment bounded something SCYTHE does to itself
and could be settled by reading merged code; this one is a claim about what
another system will accept, and it lands before an implementation can imply it.
Touches §2, §5, §7, §11 and §17.*

> **GraphOps success is a claim requiring evidence. GraphOps failure is not
> proof that nothing happened.**

Everything below follows from those two sentences being different shapes.

### H.1 One reservation addresses one operation, forever

Every durable reservation deterministically yields a **stable operation
identity**:

```
promotion_operation_id =
    SHA-256( schema_version || lineage_id || generation_id || reservation_seq
             || subject_identity || canonical_payload_digest )
```

**The adapter may not mint a fresh identity on retry.** One reservation always
addresses the same GraphOps operation, so a second attempt is the *same*
operation rather than a new one, and GraphOps can refuse it without SCYTHE
having to know whether the first arrived. Different payload bytes under one
operation identity are an **integrity conflict**, not a new version.

**SHA-256 rather than blake2s, deliberately.** Everything internal here uses
blake2s — `verdict_digest`, the successor digest, the ceiling configuration
identity. This one crosses a boundary and must be computable by a system that
did not choose our hash. Recorded so a later reader tidying for consistency
finds the reason before the edit.

**GraphOps must enforce atomic create-if-absent on this identity.** If it
cannot, the adapter is not conformant and cannot support ARMED
(`ADAPTER_NOT_CONFORMANT`). This is not a preference: without it, *the same
reservation attempted twice* and *two reservations* are indistinguishable at the
far end, and the fence this contract spent eight slices building stops at our
side of the wire.

### H.2 The canonical command

One closed, versioned structure carrying only fields GraphOps has agreed to
accept: schema version, `promotion_operation_id`, lineage and generation
identifiers, reservation sequence, subject identity, canonical payload, payload
digest, and contract/configuration identity. Serialization is deterministic.

**Secrets, credentials, headers, URLs, exception text and GraphOps response
bodies never enter the ledger or structured evidence.** The rule §13a B.2 set
for exception messages and §13d E.6 for write outcomes, applied where the
temptation is largest: a response body is the one artefact that would make
debugging easy, and it is arbitrary text from another system.

### H.3 The adapter result is closed

```python
@dataclass(frozen=True)
class WriteResult:
    outcome: Literal["CREATED", "NOT_CREATED", "UNKNOWN"]
    operation_id: str
    payload_digest: str
    evidence_code: str
    receipt_digest: str | None
```

No free-text detail, no raw body. `evidence_code` is a closed set, and it is
what makes the three-state result auditable: the outcome says what we concluded,
the evidence code says what we concluded it *from*.

### H.4 Classification, and what each state must be able to show

**`CREATED` — only on authoritative evidence** that GraphOps holds the mutation
under the expected operation identity *and* payload digest:

- a valid creation receipt (`CREATION_RECEIPT_VALID`), or
- an idempotent already-exists whose stored digest matches exactly
  (`IDEMPOTENT_MATCH`).

**Already-exists with a different digest is not success.** It is
`IDEMPOTENT_DIGEST_CONFLICT`, classified `UNKNOWN`, and it halts the adapter:
something under our operation identity is not what we sent, and no further
attempt can improve that.

**`NOT_CREATED` — only on authoritative evidence** that the mutation did not
occur: local validation failed before transport (`LOCAL_VALIDATION_REFUSED`);
the transport attests no request bytes were submitted (`SUBMISSION_NEVER_BEGAN`);
a defined, authenticated, mutation-free rejection (`MUTATION_FREE_REJECTION`); or
a strongly consistent lookup proving absence where the GraphOps contract
guarantees absence is authoritative (`AUTHORITATIVE_ABSENCE`).

> **DNS failure and connection refusal are `NOT_CREATED` only if the transport
> seam can positively attest that submission never began — never because
> creation seems unlikely.**

That sentence is the whole amendment in miniature. A connection refused *looks*
like nothing happened, and looking like nothing happened is exactly the evidence
this contract does not accept. The seam must *know*, and if it does not, the
answer is `UNKNOWN`.

**`UNKNOWN` — every ambiguous boundary**, and the list is long on purpose:
timeout after submission may have begun; connection loss after any request bytes
were sent (`SUBMISSION_BOUNDARY_CROSSED`); cancellation during submission;
service failure without a mutation-free guarantee; a malformed
(`RECEIPT_MALFORMED`) or unauthenticated (`RECEIPT_UNAUTHENTICATED`) receipt; a
receipt whose operation identity or digest does not match
(`RECEIPT_IDENTITY_MISMATCH`); a conflicting existing object; a weakly
consistent or unavailable read-back (`LOOKUP_INCONCLUSIVE`); an adapter
exception with no stronger bounded evidence (`TRANSPORT_INTERRUPTED`).

**When uncertain, `UNKNOWN`. No silent retry.**

### H.5 The coordinator sequence, unchanged, and what each result costs

§3's order stands: checks and the durable reservation under the lock, exactly
**one** adapter attempt with the lock released, the terminal record under the
reacquired lock, and posture updated only after that record is durable.

| the adapter says | ledger | consequence |
| --- | --- | --- |
| `CREATED` | append `COMMITTED` | the identity is promoted |
| `NOT_CREATED` | append `FAILED` | fenced; release still requires §13e F.2's authority |
| `UNKNOWN` | **append nothing** | the reservation stays unresolved and counts toward C2 |

**A crash after GraphOps created the object and before the committed record is
appended leaves the reservation unresolved on restart, and never triggers
re-execution.** That is reserve-before-write paying out at the far boundary: the
ledger under-claims, an operator reconciles, and nothing duplicates.

**A terminal-record failure after `CREATED`** leaves the graph object in place
and the ledger unable to say so. §13d E.6 already halts appends in that process;
the object is preserved, the reservation is unresolved, and the repair is
reconciliation. Losing the terminal record is the safe direction precisely
because this is what it costs.

### H.6 No automatic retry after ambiguity

**Automatic retry is permitted only where the adapter can prove submission never
began** — and in that case the result was already `NOT_CREATED`, so there is no
ambiguity to retry through. **Once an attempt is `UNKNOWN`, SCYTHE does not
invoke GraphOps again for that reservation.**

Resolution is Amendment F's, using the stable operation identity: an exact
identity-and-digest match reconciles committed; authoritative absence makes the
identity eligible for an operator-governed release; a conflict or an
inconclusive lookup leaves it unresolved.

**Slice 9 must not automate that authority merely because the adapter has a
query method.** Having the means is not having the authority, and the adapter
acquiring a lookup is exactly when that distinction stops being obvious.

### H.7 Conformance is declared, not configured

**An endpoint does not imply a capability.** An adapter is conformant only when a
versioned declaration establishes: atomic idempotency-key enforcement; stable
operation lookup; receipt authentication or otherwise authoritative provenance;
payload-digest echo or lookup; consistency semantics; mutation-free rejection
codes; maximum request and response sizes; timeout and cancellation behaviour.

That declaration is **hashed into the adapter's configuration identity**, as the
ceilings are (§13f G.4). A changed GraphOps contract requires a new reviewed
identity and not a quiet environment-variable edit — the same rule, for the same
reason, at a boundary where the other party can change without telling us.

### H.8 What slice 9 may and may not do

**May:** the adapter protocol, a command encoder, a transport seam, receipt
validation, result classification, coordinator composition behind the existing
unreachable ARMED boundary, and a deterministic fake GraphOps for conformance
tests.

**May not:** a production ARMED constructor, live credentials, an enabled
endpoint, live SHADOW, automatic reconciliation, Step 4 authority, or any graph
mutation from the current process.

### H.9 The name check

Sixteen candidates were run against the discovered universe before any was
written here. Fifteen are clear. One was **rejected rather than judged**:
`GRAPHOPS_CONFORMANCE_UNDECLARED` is a negation pair with `OPERATOR_DECLARED`,
`MODEL_DECLARED` and `UNDECLARED` across the RF modules. The refusal is
`ADAPTER_NOT_CONFORMANT`, which is clear and says what is wrong with the adapter
rather than what is missing from a file.

---

## 13h. Amendment I — what live SHADOW can actually observe

*Proposed 2026-09-12 and accepted 2026-09-12, after review required synthetic
input to be non-executable by construction (I.2a), the bounds to be capped by
the contract (I.5), and the digest claim to be narrowed (I.4a). Narrows §12's
claim before slice 10 implements it, because the claim as written cannot be met.
Touches §12 and §17.*

### I.1 SHADOW cannot predict what ARMED would do

§12 says the whole purpose of the shadow slice is to predict what ARMED would
do. That is not achievable, and the reason is structural rather than a matter of
effort.

`decide_promotion` requires a `PromotionRequest`. **Nothing in production builds
one.** `PromotionRequest` and `CapsuleIdentity` are constructed in exactly two
places in this tree: the module that defines them, and tests. `PromotionCoordinator`
has never been instantiated outside a test either.

Real verdicts do exist — `rf_walk_transitions` and `rf_sparse_accounting` call
`check_transition` over real evidence and produce genuine `InvariantVerdict`s.
**The checks are live and the asking is not.**

And the asking cannot be supplied, because `scythe_promotion_policy` already
rules it out: *frame arrival, a completed check, a model's commentary — none of
these is an ask, and the absence of one is not a refusal either.* A SHADOW that
synthesized a request per verdict would measure a system in which every finding
is requested, which is not the system and never will be by that rule.

> **ARMED's promotion rate depends on a requester that does not exist. It is
> undefined, not merely unobserved, and SHADOW cannot forecast it.**

### I.2 What it observes instead: the apparatus

Slice 10 observes the machinery against real verdicts — whether seeding works on
a real ledger, whether the budget and the ceilings engage, whether promotion
identity is stable across real evidence, and whether the file is byte-identical
afterwards.

That is a smaller claim than "live SHADOW observation" sounds like, and it is
written here rather than left to a code comment so the record cannot quietly
grow back into a forecast.

The record states in its own text that it is an apparatus observation and
`NOT_A_PREDICTION`. A reader who takes a promotion count from it and calls it a
rate is then contradicting the document they are reading, which is the most a
document can do.

### I.2a Synthetic input is non-executable by construction

**`SYNTHETIC_REQUEST` may not be a flag on an otherwise executable
`PromotionRequest`.** That is the ownership-boolean problem again (§13d E.4): a
flag can be omitted or falsified, and the call site does not show whether the
claim was true.

The observation path uses a **distinct type**, `ObservationSubject`, and produces
a **distinct outcome**, `ObservationOutcome`. Five prohibitions, and each is
structural rather than a rule someone must remember:

| | how it is prevented |
| --- | --- |
| cannot reach the adapter | the observer holds none, and the adapter refuses both types by name |
| cannot create a durable reservation | the observer holds no ownership scope, and without one no append exists (§13c D.4) |
| cannot mutate durable posture | the observer holds no `LedgerWriter`; it reads through `read_ledger` and `Lineage` |
| cannot become a production `PromotionRequest` | no conversion exists, and the synthetic path **never produces a `PromotionDecision`** — every act path takes one, so there is nothing for an act path to accept |
| is refused at every execution boundary | the write session and the adapter reject `ObservationSubject` and `ObservationOutcome` explicitly, so a future wiring mistake fails loudly rather than relying on absence |

Pure policy evaluation and in-memory simulation may be reused. What may not be
reused is the production ask, and the guarantee is that the observation path has
no value to hand one.

§12's testable property is unchanged and finally testable where it was meant to
be: slice 5 proved byte-identity against a temporary directory, and §12's claim
is about a live run beside a possible writer.

### I.3 Verdicts come from checks, never from acquisition

The source is the existing transition checkers over evidence that already
exists. **Slice 10 initiates no capture and opens no `rtl_tcp`.**

The raw-IQ and loopback-binding constraints are therefore not engaged carefully
— they are not engaged at all, which is the stronger position. A slice that
acquires in order to observe would have to argue that it handled raw IQ
correctly; this one does not have the buffer.

### I.4 The observation record, and how it is published

Written **once, at the end of a bounded run**, to a path **outside the complete
ledger-lineage namespace**, and never appended to.

Not merely differently named, and not merely outside the root by prefix: the
resolved output path and the resolved lineage namespace are compared after
symlink resolution, and containment refuses with `OBSERVATION_PATH_REFUSED`.
`Lineage.published()` would ignore a differently-named file by pattern, but
relying on a filename pattern to keep a non-ledger out of the ledger is the
near-miss this contract has spent nine slices removing. **A file that
accumulates is a second ledger nobody accepted.**

**Published by §13e F.6's protocol**, because it is the same problem: create a
temporary sibling exclusively, write it complete, `fsync` the file, rename
atomically to the final name, `fsync` the directory. Exclusive creation, never
overwrite and never append — a second run at one path is
`OBSERVATION_PUBLICATION_REFUSED`, not an extension of the first.

**Bounded structured failure codes only.** No exception strings, no credentials,
no paths that carry secrets, no arbitrary source data. The rule §13a B.2 set for
exception messages, applied to a record whose whole purpose is to be read later
by someone who was not there.

**What the record does not claim.** A best-effort terminal record is written for
a recoverable exception (I.7). It claims nothing about surviving `SIGKILL`,
power loss, or a failure of its own publication — an observation cannot report
its own violent end, and a record that implied otherwise would be the only
untrue thing in it.

It carries: verdicts seen and their dispositions; simulated promotions and
refusals by code, with the two vocabularies counted separately (§5); the durable
ceiling totals read from the real ledger and the simulated ones named apart
(§13f G.7); what it seeded from; the digests before and after; and the
configuration identities already defined, so the record says which contract it
was taken under.

### I.4a What equal digests prove, and what they do not

**Equal digests show the ledger was unchanged during the observation interval.
They do not attribute that to the observer.**

§12 permits SHADOW to run beside an ARMED writer. So byte-identity is evidence
that *nothing wrote*, not that *this process did not write* — and where another
writer may run, the two are different claims. The record must not make the
second one from the first.

So the record states whether the lineage was **quiescent**:

```
LINEAGE_QUIESCENT       nothing changed across the interval
LINEAGE_NOT_QUIESCENT   something did, and the observer makes no claim about why
```

Under `LINEAGE_NOT_QUIESCENT` the record reports the change and **draws no
conclusion about its cause**. The observer cannot tell its own writes from
another process's, and the honest report of that is silence about attribution
rather than a confident sentence about a file it does not own.

### I.5 Two bounds, both capped by the contract

The run ends on whichever arrives first:

```
VERDICT_COUNT_REACHED          a declared number of verdicts observed
OBSERVATION_DURATION_REACHED   a declared monotonic duration elapsed
```

**Monotonic, never the wall clock.** On this host UTC takes ~23.5 h steps that
retroactively re-render past timestamps, which is why §6 will not persist a
window and why a duration bound measured in UTC would be a number that looks
like a duration and is not one.

Reaching either bound is recorded as a normal ending. Neither is an error, and a
run that ends because it saw everything it was asked to see has succeeded.

**A configurable bound is not a bound.** "Configured count and duration" permits
a billion verdicts or several years, which is an unbounded run with a number
attached. Both requested limits are checked against **contract-declared
maxima**:

```
MAX_OBSERVATION_VERDICTS   = 1_000
MAX_OBSERVATION_DURATION_S = 900        # 15 monotonic minutes

1 <= requested_verdict_limit   <= MAX_OBSERVATION_VERDICTS
0 <  requested_duration        <= MAX_OBSERVATION_DURATION_S
```

**Both limits stay active**; the first reached ends the run. A missing or
out-of-range limit is `OBSERVATION_LIMIT_INVALID` and **refuses before
observation begins** — nothing is read, nothing is written, and no record is
published, because a run that never started has nothing to report.

The maxima are contract-declared constants and not runtime knobs, for §13f G.4's
reason: a ceiling a deployment can raise is one that will be raised at the
moment it first binds.

### I.6 Foreground, in-process, and it stops by itself

**No daemon, no background process, no scheduled task.** The Windows Scheduled
Task remains documentation-only, and this is not the slice that changes it.

Everything since slice 3 has been provable in a test; this one executes against
real data, which changes the risk in kind rather than in degree. This repository
carries a wedged PID from the last time something ran and did not stop, and the
recovery work exists because of it. A bounded foreground run cannot become that.

### I.7 A run that fails still writes its record

If the observation ends on an exception rather than on a bound, it **still
writes the record**, marked `OBSERVATION_INTERRUPTED` and naming the bound it did
not reach.

An observation that produces nothing when it fails is indistinguishable from one
that never started, and the difference between those two is the only thing the
record was for.

### I.8 What slice 10 must not do

No ARMED constructor, no adapter invocation, no ledger append, no capture, no
`rtl_tcp`, no recovery arming, no scheduled task, no background process, and
nothing that touches PID `315535`.

### I.9 The name check

Eight candidates run against the discovered universe before any was written.
All eight clear, and none required a judgement.

---

## 13i. Amendment J — derived evidence, and why it cannot carry samples

*Proposed 2026-09-12 and accepted 2026-09-12, after review found the governing
claim overstated: a closed schema proves conformance to a representation and not
semantic origin. J.3 now carries a qualified three-state assessment, J.7 and
J.7a specify the read and the digest, and J.8 sets exact bounds. Defines the
artefact class `PENDING_AMENDMENTS.md` entry 7 waits on. Touches §12, §17, and
the merged `CapsuleIdentity`.*

> **A closed coordinate schema cannot carry raw IQ, because no field is declared
> for it.**

The prohibition stops being a rule someone must obey and becomes a shape the
artefact cannot take. Everything else here is in service of that sentence being
true rather than aspirational.

### J.1 What this is under the Q1 grant

*The drafter's reading, recorded as such. The grant is the operator's and this
section does not extend it.*

The standing raw-IQ grant is **process-local, volatile, fixed-capacity,
non-persistent, non-transportable, non-model-context, bridge-owned, and
invalidated on signal-chain change** — permission for a DSP working buffer and
explicitly *"not permission for an IQ archive"*, with *"no disk fallback, swap-
oriented buffering or crash dump facility"* added under it.

A derived-evidence artefact is none of those things. It carries **coordinates**:
named scalars the invariant apparatus already operates on, which have never
contained a sample because `check_transition` has never been able to receive
one. Persisting coordinates is not persisting IQ, and the artefact is not a
fallback for the buffer — it cannot hold what the buffer holds.

**The reading is recorded rather than assumed** because the grant's language is
about intent as much as content, and a later reader should find the argument
rather than infer that nobody noticed the question.

### J.2 Three of the four pieces are already merged

This amendment defines less than it looks like, and that is the strongest thing
about it.

`rf_walk_survey_metadata.find_sample_bearing_field` already walks a payload
recursively, skips `evidence_refs` because its values are names, and returns the
first sample-bearing key anywhere inside. The structural check this artefact
needs exists and is tested.

That module already separates `RAW_IQ_FRAME` from `METADATA_ASSESSED`: **the
category is not new, only its persistence is.**

And `CapsuleIdentity` already carries `carries_samples`, which the policy already
refuses. The type the reader must produce is fixed.

### J.3 `carries_samples` is reader-derived, and qualified

**This amends a merged type's contract**, and it is the change with the most
reach in this amendment.

Today `carries_samples` is a value passed to a constructor: a caller asserts it
and the policy refuses on the assertion. **Callers may not supply it for an
artefact-derived capsule.** The reader produces a closed assessment instead:

```
SAMPLE_BEARING_DETECTED      a sample-bearing field was found
DERIVED_SCHEMA_CONFORMANT    the artefact passed the structural exclusion profile
SAMPLE_STATUS_UNVERIFIABLE   the profile could not be applied in full
```

**Only `DERIVED_SCHEMA_CONFORMANT` may derive `carries_samples=False`**, and it
means exactly one thing:

> the artefact passed the accepted structural exclusion profile. It is **not** a
> cryptographic proof that a dishonest producer never encoded samples.

`SAMPLE_BEARING_DETECTED` and `SAMPLE_STATUS_UNVERIFIABLE` both **refuse before
either checker runs**. Not a degraded observation and not a partial yield: a
verdict derived from evidence whose sample status is unknown would carry that
uncertainty into a promotion identity, and nothing downstream could recover it.

**Origin is attested, not inferred.** The separately authorized writer (J.4)
must attest that every coordinate came from the declared aggregation and checker
pipeline. The reader validates that attestation and the schema; it does **not**
claim to reconstruct provenance from values. That is the distinction §13g H.7
draws for GraphOps conformance: some properties can only be declared by the
party that has them, and a consumer that inferred them would be inventing
authority.

The flag stops being asserted by a caller and starts being established by a
check whose limits are written beside it — which is more than it had, and less
than the first draft of this section claimed.

### J.4 Read-only, and that is the whole shape

**The interface is a reader. There is no writer.**

Slice 10 gains the ability to consume an artefact; nothing here gains the
ability to produce one. §13h I.3's *no acquisition* is therefore untouched
rather than carefully preserved — there is no acquiring path to preserve it
against.

```
derived_walk_verdicts(path) -> (verdict, subject, capsule) triples
```

The same triple slice 10a's constructed source already yields, so
`EVIDENCE_DERIVED_ARTEFACT` becomes reachable by substitution and nothing else
in slice 10 changes.

**Writing artefacts is a separate concern and a separate authorization.** A
writer is a new durable output from the acquisition path, and it deserves its
own act rather than arriving inside a reader's amendment.

### J.5 The closed schema, and four refusals

Framing reuses `frame_of` — length, CRC, JSON, one record per line — because the
reasons for framing are identical and a second format is a second thing to get
wrong. The **kind set is separate**, so an artefact can never be read as a
generation.

One `ARTEFACT_PROVENANCE` record first, then transition records. Provenance
carries the schema, artefact identity, a content digest, and the capture facts
the verdicts need: `device_id`, `signal_chain_hash`, `configuration_epoch`,
`monotonic_source_id`.

Every coordinate value is refused unless it passes all four:

| refusal | what it stops |
| --- | --- |
| `COORDINATE_NOT_IN_SCHEMA` | an undeclared name; the set is closed like the ledger's record kinds |
| `NON_SCALAR_COORDINATE` | a list or mapping, which is how samples arrive |
| `OVERSIZED_COORDINATE` | a string beyond a declared length |
| `SAMPLE_BEARING_FIELD_FOUND` | the recursive check, over the whole record |
| `RECORD_INTERVAL_REFUSED` | a high-rate record stream under the declared timeline (§13j) |

**The third is the one worth arguing for.** The first two are obvious, and a
sample blob base64-encoded into one long string passes both of them. A declared
maximum string length is what makes "no samples" hold against the encoding that
was designed to get binary through text.

### J.6 The signal chain is recorded, not verified

Q1's grant invalidates on signal-chain change, and a reader has **no live
receiver to compare against**. The artefact is evidence about a past chain
state; the observation record carries which one and claims nothing about whether
that chain is current.

Saying so is the honest version of a check that cannot be performed here. A
reader that compared the recorded hash against something it invented would be
manufacturing the authority §13g H.7 refuses endpoints for implying.

### J.7 One descriptor, one read, and a digest over what was consumed

The artefact is read-only and immutable, so it needs no ownership and no lock:
there is no second writer to exclude, because there is no writer.

What it needs is the read itself to be honest:

- **one descriptor**, opened once;
- the target must be a **regular file** — not a FIFO, not a device, not a
  directory;
- read **within the fixed bounds below**, never to exhaustion;
- the **digest is computed over the exact bytes consumed**, not over a later
  re-read of the path.

**The path is never reopened during one read.** A path is a name and the name
can be repointed between opens; a digest taken from a second open would attest
bytes that the verdicts did not come from. This is §13d E.2's lesson one layer
down — attest the descriptor, not the path.

### J.7a The digest is not recursive

A digest that covered the header that carries it cannot be computed. So:

```
content_digest = hash( the exact framed evidence-record bytes )
artifact_id    = hash( schema identity || canonical provenance || content_digest )
```

The provenance record carries **both** and is **excluded from `content_digest`**.

Five refusals follow directly, and each closes a way the two could disagree:

| refusal | what it stops |
| --- | --- |
| trailing bytes after the last record | content outside the digest's reach |
| more than one provenance record | two headers, two identities, one file |
| a duplicate artefact identity | the same name over different content |
| a record count that does not match the declared one | records added or removed beneath the digest |
| `CONTENT_DIGEST_MISMATCH` | the bytes consumed are not the bytes attested |

### J.8 Bounds, exactly

```
MAX_ARTEFACT_RECORDS       = 1_000       evidence records
MAX_ARTEFACT_BYTES         = 4_194_304   4 MiB total
MAX_RECORD_BYTES           = 16_384      16 KiB per framed record
MAX_STRING_BYTES           = 256         UTF-8 bytes per ordinary string field
```

**Numeric scalars are finite and bounded, and five shapes are refused**, each
because it is a way to carry more than a measurement:

- `NaN` and the infinities — an evidentiary coordinate has a value or it has a
  declared absence, and §13a's identity rules already refuse non-finite
  coordinates for the same reason;
- **excessive precision** — a float's mantissa is sixty-odd bits of anywhere the
  producer likes, and precision far beyond the instrument is the covert archive
  this profile is least able to see;
- **booleans masquerading as integers** — `bool` subclasses `int`, and the check
  must be ordered accordingly;
- **numeric strings** — `"1.0"` is a string that reads as a number, and
  accepting it would let the string bound and the numeric bound each assume the
  other applied.

Contract-declared, not runtime knobs (§13f G.4). **An unbounded artefact is an
unbounded run wearing a file**, and slice 10's own bounds would be satisfied
while the read that feeds them was not.

### J.9 What this does not do

No writer, no acquisition, no socket, no capture trigger, no change to the Q1
buffer, and **no fallback to constructed evidence when an artefact is missing**.
Slice 10a's `DERIVED_EVIDENCE_UNAVAILABLE` exists precisely so absence is
reported rather than filled, and a reader that quietly substituted would undo
the distinction the previous slice was held back to make.

### J.10 The name check

Ten candidates against the discovered universe. Six clear; **four rejected rather
than judged**, each landing on ground that is already crowded:
`UNDECLARED_COORDINATE` and `COORDINATE_NOT_DECLARED` against the five
`DECLARED` tokens across the RF modules, `SAMPLE_FREEDOM_VERIFIED` against
`VERIFIED` and `UNVERIFIED`, and every name containing `HEADER` against the
ledger's record kind — which is why the provenance record is called what it is.

---

## 13j. Amendment K — a record-rate bound, and what it does not prove

*Proposed 2026-09-12 and accepted 2026-09-12, in that order. Adds the fifth
entry to §13i J.5's structural exclusion profile, so slice 10b's
one-sample-per-record control has something real to break. Touches §13i only.*

### K.1 Why this exists, and the control that could not be written

§13i J.1 records the counterexample its own correction was built on: coordinates
**emitted at sample cadence** pass a closed scalar schema. J.5's four refusals do
not reach it, and the contract said so and stopped there.

Slice 10b was then asked for a control breaking *one-sample-per-record rejection
through the contract's cadence constraints* — and there were none. The control
would have had to invent the constraint in the implementation and then break the
thing it invented, which is the shape of a test that cannot fail. So the rule
lands here first.

### K.2 The bound

```
MINIMUM_RECORD_INTERVAL_NS = 1_000_000     # one millisecond
```

**Per independently declared evidence series**, and for each one:

- every record carries `observed_monotonic_ns`;
- values **strictly increase**;
- consecutive values differ by **at least** `MINIMUM_RECORD_INTERVAL_NS`;
- a missing, repeated, backward or sub-floor timestamp **refuses the artefact**
  with `RECORD_INTERVAL_REFUSED`.

**Integer monotonic values only.** No wall clock, and no floating-point
conversion for the comparison. On this host UTC takes ~23.5 h steps, and a float
conversion of a nanosecond count loses the low bits at exactly the magnitudes
that matter — a comparison that rounded two records into agreement would refuse
nothing while appearing to check.

### K.3 Per series, not across the artefact

The rule applies **within a series or transition family**, never globally.

A walk step and a sparse decomposition may legitimately produce derived
coordinates at the same monotonic instant: they are different observations of
one moment, not a stream. A global floor would refuse an honest artefact for
containing two families, which is the false positive that teaches an author to
widen the bound until it stops meaning anything.

### K.4 What this establishes, and what it does not

> **The artefact does not contain a high-rate record stream under its declared
> monotonic timeline.**

That is the whole claim. It is a **plausibility and information-rate bound**, and
it is not:

- proof of honest origin;
- proof that covert encoding is impossible;
- a reason to relax anything else in the profile.

A producer can space encoded scalars a millisecond apart, or falsify timestamps
outright. **Producer provenance remains separately authorized and trusted**
(§13i J.3, J.4) — the reader validates an attestation and a schema, and this
bound narrows what an unattested artefact can be without making it safe.

It is C1's shape (§11): a *this has gone wrong* bound set far from anything
legitimate, whose firing during correct operation would mean it was set wrong.
One millisecond is three orders of magnitude above a 1 Msps sample interval and
two below a plausible walk step, which is the gap that makes it cheap.

### K.5 The name check

Six candidates against the discovered universe before any was written. All six
clear; none required a judgement.

---

## 13k. Amendment L — the producer, and how far it is kept from a capture

*Proposed 2026-09-12 and accepted 2026-09-12, in this order and recorded rather
than tidied: the proposal merged before acceptance, as Amendment H's did; a
status flip was prepared; review then found L.1 overclaiming what a Python
annotation guarantees; the corrected text is what is accepted. The correction is
the reason the flip should not have been taken at face value, which is why the
order is kept.*

*Defines the separately authorized producer §13i J.4 deferred, so
`PENDING_AMENDMENTS.md` entry 7 can eventually drain. Touches §13i, §13j and
§17.*

**A durable writer aimed near the capture path must not appear as one
conversational shrug.** That is why this is a contract before it is a slice, and
why the slice is before the run. Three separate acts, each refusable on its own.

### L.1 The producer consumes typed checker inputs, and nothing else

The producer receives **exactly what the existing checkers receive**:

- walk signatures, as passed to `check_walk_step`;
- sparse-energy coordinates, as passed to `check_decomposition`.

It must **never** receive an IQ buffer, a byte stream, a sample array, a capture
handle, an SDR object, a socket, or a generic mapping.

**Family-specific entrypoints, never `write_record(dict)`.** A generic mapping
parameter is a hole shaped like anything, and a named family entrypoint is not.

#### L.1a An annotation is not a gate

*Corrected in review. The first text said the parameter's **type** refuses a
buffer before any check runs. In Python it does not.*

```python
def record_walk_step(before: WalkSignature, after: WalkSignature) -> None:
    ...
```

That signature refuses nothing at the call boundary. Annotations are metadata
unless a runtime validator or an enforced static checker supplies the refusal,
and a bytes-like object, a mock, or a structurally compatible impostor passes it
unremarked. **Writing the annotation and believing it is the whole guard is the
failure this correction exists to name.**

So the boundary is stated at the strength it has, in four parts:

1. **Typed family-specific entrypoints constrain the API** and make misuse
   visible to static analysis.
2. **Runtime nominal-type validation is authoritative.** It is what actually
   refuses.
3. **Validation happens before any artefact file is opened or created.** A
   refusal after a descriptor exists is a refusal that has already touched the
   filesystem.
4. **Static typing is defence in depth, not an execution boundary** — the same
   asymmetry L.4 draws between the producer's checks and the reader's.

#### L.1b What the runtime gate rejects

Generic mappings and sequences; bytes-like and buffer-protocol objects;
**subclasses and proxies with uncontrolled accessors**; and duck-typed
substitutes. **Only the exact accepted immutable coordinate and signature types
enter serialization.**

The subclass clause is the one that is easy to get wrong. `isinstance` admits a
subclass, and a subclass may override `__getitem__`, `keys`, or `__iter__` to
return whatever it likes at the moment the serializer asks — so the check is
**nominal**: the type *is* the accepted type, not merely compatible with it.
Duck typing is precisely what must not be honoured here, because the property
being protected is what the object *is* rather than what it can do.

#### L.1c What the producer records

Checker inputs and the resulting verdict identity. It does not duplicate or
reinterpret checker mathematics: a second implementation of a rule is a second
rule that can disagree (§13i J.3's reasoning, in the other direction).

### L.2 Provenance is attested upstream, never verified here

Each artefact declares: producer schema and version, transition family, device
identity, signal-chain hash, configuration epoch, monotonic-source identity,
contract and configuration identity, a bounded run identity, and the exact
content digest.

**These are upstream attestations, not facts the producer established.** It
cannot verify a device identity or a signal-chain hash; it can only record what
a component told it. So the artefact records **which component supplied each
claim** — a claim whose source is unnamed is a claim nobody can later question,
and an artefact full of those is a provenance record that proves only that
someone typed something.

This is §13g H.7's rule reaching a third party: some properties can only be
declared by whoever has them, and a recorder that presented them as its own
findings would be manufacturing authority it does not hold.

**`carries_samples` stays reader-derived (§13i J.3). The producer never writes
that boolean and never accepts it as input.** The one flag the reader must
establish for itself is the one the writer has no way to touch.

### L.3 Publication, by the accepted protocol

§13e F.6's five steps, unchanged: exclusive temporary sibling, complete bounded
write, file `fsync`, atomic rename to a deterministic final name, directory
`fsync`.

**No overwrite, no append, no mutable *current artefact* pointer**, and **no
cleanup of foreign temporaries** — a temporary this run did not create may be
another run's evidence, which is §13e F.6's rule about leftovers arriving where
it applies a second time.

Repeating a run identity **rediscovers** the existing artefact; a digest conflict
under one identity is `REQUEST_DIGEST_CONFLICT` and refuses. **No ownership lock
is required**: artefacts are immutable and request-addressed, so there is no
second writer to exclude from one name.

### L.4 Fail-closed, and the reader stays authoritative

Before publication the producer applies the **same structural exclusions the
reader applies** (§13i J.5, §13j K.2): closed coordinate names, scalar-only
values, string and numeric-precision bounds, record, frame and total-size
bounds, the recursive sample-bearing scan, and the per-series cadence floor.

**These are defence in depth and nothing more. The reader repeats every one and
remains authoritative for admission.** A producer check that the reader did not
repeat would be a guarantee held by the party with the most reason to be wrong
about it.

Any forbidden value **aborts publication**. **No truncated artefact receives a
final name** — that is what the temporary sibling is for. A failed temporary is
preserved as bounded diagnostic evidence and is **never readable as evidence**:
it has no final name, and the reader only reads final names.

### L.5 Reachability

The producer implementation is:

- **disabled by default**, and refusing with `PRODUCER_NOT_ENABLED`;
- foreground, in-process, explicitly invoked;
- bounded by **1,000 records** and **900 monotonic seconds**;
- incapable of initiating capture;
- incapable of starting `rtl_tcp`;
- incapable of invoking the adapter;
- unscheduled and non-daemonized.

**It must not attach to, signal, inspect through `/proc`, restart, or otherwise
touch PID 315535.**

Disabled-by-default is the one that carries the others. Everything above is a
property of code that has to be reachable to matter, and a producer that does
nothing until someone turns it on cannot be reached by an accident, a default, or
a test that forgot where it was running.

### L.6 The required sequence

Each step is separately refusable, and the ordering is the point:

1. accept and merge this amendment;
2. implement the producer as **slice 10c**, with **no live execution**;
3. validate it entirely against controlled typed inputs and negative controls;
4. **separately authorize one bounded foreground production run**;
5. produce one real derived artefact;
6. read it through the merged 10b reader;
7. run the bounded observation and publish its record;
8. verify `EVIDENCE_DERIVED_ARTEFACT`, `LIVE_OBSERVATION`, and a lineage
   correctly reported as quiescent or not — and **unattributed either way**
   (§13h I.4a).

Only then is stage five assessable.

**Entry 7 drains when a real conforming artefact exists** — not when this
amendment lands, and not when slice 10c does. A producer that has never been run
has produced nothing, and the entry has always been about evidence rather than
about the means to make it.

**Slice 11 remains unreachable throughout.** No authorization for ARMED or any
GraphOps mutation can rest on constructed evidence, and none of the work above
changes that until step 8 answers.

### L.7 The name check

Ten candidates against the discovered universe. Eight clear; **two rejected
rather than judged**: `PRODUCER_DISABLED` against `DISABLED`, which is one of
the three postures shared by recovery and promotion, and `ATTESTED_UPSTREAM`
against `UNATTESTED` and the two filesystem attestation codes.

---

## 13l. Amendment M — the run that is not a capture

*Proposed 2026-09-12. **Not yet accepted.** Scopes the single bounded act §13k
L.6 step 5 requires, so `PENDING_AMENDMENTS.md` entry 7 can drain. Touches §13i,
§13k and §17.*

### M.1 What this run is, and the finding that made it small

A walk signature needs no IQ. `signal_chain_hash` takes **declared
configuration** — sensor identity, sample type, rate, antenna, feedline,
extension, gain — and `receiver_state_chain_hash` hashes a manifest of position,
speed and orientation authorities. **Neither touches a sample**, and
`check_walk_step` never sees one either. A walk verdict is about displacement
against a kinematic budget.

So the act is classified:

```
LIVE_POSITION_ATTESTATION       real bounded observation over real attested positions
INSTRUMENT_CONFIGURED_IDLE      the instrument is declared, and idle
RF_MEASUREMENT_NOT_PERFORMED    no sample was taken, and none was needed
```

It is **not** an RF measurement, a capture run, a receiver-performance test, or a
prediction of future ARMED behaviour.

**A capture was considered and refused.** It would have activated the whole Q1
surface — the volatile buffer, `rtl_tcp`'s binding, the ring's invalidation
rules, recovery's posture — and changed **no verdict input**, because the checker
never sees a sample either way. A live observation does not become more
meaningful because an SDR was consuming samples nobody looks at.

The RTL2838's presence **may be declared**. The run must not open, tune, reset,
claim, or otherwise communicate with it.

### M.2 Position authority, recorded as a chain

The positions are real, observed during the walk, and entered by the operator.
`rf_receiver_state.POSITION_AUTHORITIES` already carries the conservative value
this needs:

> `OPERATOR_DECLARED` — **typed or placed by a person, not measured**

That is the accepted authority, and it is not elevated because a phone displayed
the number first. **A fix read off a screen and typed in is a typed fix.**

The chain is recorded structurally, in bounded codes and never as prose:

| | |
| --- | --- |
| position source | `PHONE_DISPLAYED_FIX` |
| transfer | `HAND_TRANSCRIPTION` |
| accepted authority | `OPERATOR_DECLARED` |

Three fields rather than one, because collapsing them would lose the step where
the authority actually degrades. The phone may hold a genuine GNSS fix; what
reaches the artefact is what a person read and retyped, and the record should
say where the measurement stopped.

### M.3 Time authority, and the join that is refused

Each accepted fix is stamped with the **observation process's** `monotonic_ns`,
and the monotonic source is identified.

**That is the host's ingestion time, not the phone receiver's fix time**, and the
artefact says so. The two differ by however long the transcription took.

**Phone wall-clock timestamps are not combined with host monotonic values, and
no elapsed time is inferred across the two authorities.** This is §6's rule
arriving in a new place: a duration computed across two clocks whose relationship
nobody established is a number that looks like a duration and is not one.

### M.4 A declared configuration must not imply it was exercised

The manifest may carry intended values — sample rate, gain — **only when each is
labelled**:

```
CONFIGURED_NOT_EXERCISED
```

*Named `CONFIGURED_NOT_EXERCISED` rather than `DECLARED_NOT_EXERCISED`: the
latter is a negation pair with `UNDECLARED` and with
`LEDGER_GENERATION_UNDECLARED`. The name is changed rather than argued for.*

The signal-chain hash then identifies **the declared idle configuration**, not a
measured chain state. The artefact and the observation record both carry
`RF_MEASUREMENT_NOT_PERFORMED`.

**The current schema cannot express this, so this amendment adds it.**
`PROVENANCE_FIELDS` in `scythe_derived_evidence` is closed and carries no
measurement status; the artefact provenance gains `measurement_status` and
`instrument_state`, and the reader refuses an artefact whose provenance omits
them. Adding a field to a closed set is a schema change, and it lands here
rather than in the run.

### M.5 Bounds

```
12 accepted fixes
15 second target spacing
240 monotonic seconds maximum
```

Both active; whichever arrives first ends the run normally.

**Ordinary walking. No displacement violation is to be manufactured.** A live run
that produces no `NUMERIC_BALANCE_EXCEEDED` is valid and complete: the purpose is
to observe the apparatus honestly, not to bait an invariant until it fires for
the record. A ceiling induced on purpose demonstrates that the author can arrange
its conditions, which was never in doubt.

### M.6 Location minimization, and a tension the schema cannot resolve

Only what the checker genuinely requires is persisted.

**That requirement includes the coordinates.** `with_step_measures` computes
displacement from latitude and longitude, and §13k L.1 forbids the producer
recording checker mathematics — so the artefact must carry the fixes and must
not carry the displacement. **The sensitive datum is exactly the one the design
requires.**

This cannot be resolved in the schema. Storing displacement instead would
duplicate checker mathematics; storing offsets from a discarded origin would
make the signature a claim about positions that were not the real ones. So it is
resolved by **decision at authorization time**, and the authorizing act must pin:

- the exact output directory;
- whether absolute coordinates are retained after the observation;
- the artefact retention policy;
- the observation record's destination.

A retention choice made in the open is the honest form of a trade that has no
technical answer.

### M.7 Prohibitions

The run must not open any SDR device; start or connect to `rtl_tcp`; allocate or
persist an IQ buffer; open a socket; invoke the GraphOps adapter; construct
ARMED; touch PID 315535; start a subprocess, thread, daemon or scheduled task;
or **fall back to constructed evidence if a real fix is unavailable**.

The last one is the one that would be tempting at the moment it mattered. A run
short of fixes ends on its bounds with what it has, or does not run.

### M.8 The sequence

1. propose and accept this amendment, and **merge it before any live act**;
2. verify the producer and observer can represent the idle and non-measurement
   declarations;
3. pin the paths and the retention choice;
4. **separately authorize one bounded foreground run**;
5. produce, read, and observe the artefact;
6. verify `EVIDENCE_DERIVED_ARTEFACT`, `LIVE_OBSERVATION` and
   `RF_MEASUREMENT_NOT_PERFORMED`.

**Entry 7 drains only when those facts exist in the published record.** Slice 11
remains unreachable until that completed record is reviewed.

### M.9 The name check

Seven candidates. Five clear; **two rejected rather than judged** —
`DECLARED_NOT_EXERCISED` against `UNDECLARED`, and `OPERATOR_TRANSCRIPTION`
against the `OPERATOR` promotion authority. No new position authority was minted:
`OPERATOR_DECLARED` already said the needed thing, and a fourth value would have
been a second name for it.

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

**Amendment D (§13c)**

28a. A record written by the write path is accepted by slice 5's reader without
    any change to the reader — the same bytes, read by the code already merged.
28b. `next_seq` is `last_seq + 1` over every record kind, and a header, a
    reservation and a terminal record allocated in sequence are strictly
    increasing.
28c. Concurrent evaluations allocate distinct, strictly increasing sequence
    numbers; the test forces the interleaving and fails before allocation moves
    inside the critical section.
28d. `next_seq` after restart is rebuilt from the file and never from a
    persisted counter; a ledger with a gap yields a number above the gap.
28e. A torn ledger is never appended to, and no append truncates, rewrites or
    tidies a torn tail. The file is byte-identical after a refused append.
28f. A zero-byte ledger is readable, fences nothing, and refuses ARMED with
    `LEDGER_GENERATION_UNDECLARED`; SHADOW is unaffected.
28g. A ledger whose header the writer has written declares a generation, and
    the refusal is gone.
28h. Every append refuses with `LEDGER_NOT_OWNED` unless a live ownership scope
    is supplied, and slice 6 provides no production path that produces one.
28k. A scope retained past the end of its ownership is inert: a later append
    through it refuses with `LEDGER_NOT_OWNED` rather than succeeding.
28l. A scope bound to one ledger does not admit a write to another.
28m. AST — the public surface exposes no append taking an ownership flag, and
    no append that can be called without a scope.
28n. The writer does not establish a generation on an empty ledger: after an
    attempted append to a zero-byte ledger with no generation, the file is
    byte-identical and the refusal stands.

**Amendment E (§13d)**

29a. A second coordinator on one ledger is refused `LEDGER_NOT_OWNED`, while its
    SHADOW observation continues.
29b. The sidecar's name is derived from the ledger path and cannot be configured
    — AST, and a test that two writers for one ledger lock the same file.
29c. A scope is refused on an unattested mount even though `flock` succeeded.
29d. A scope goes dead when the owner loses the lock, not only when its session
    ends; the refusal is `OWNERSHIP_LOST`.
29e. A refused durable append takes no in-memory reservation: the identity is
    absent from the identity map and from `status()`.
29f. A fresh coordinator against an existing ledger refuses an identity
    committed before the restart, and admits one never promoted.
29g. An unresolved reservation written before a restart is still fenced after
    it, and is reported as unresolved rather than as failed.
29h. The window is not rebuilt at startup, and `budget_window_survives_restart`
    stays false.
29i. A write whose `fsync` fails fences the identity, refuses with
    `RESERVATION_NOT_DURABLE`, and stops further appends in that process.

**Amendment F (§13e)**

30a. A `COMMITTED` identity cannot be reconciled — `NOT_RECONCILABLE`.
30b. `RECONCILED_COMMITTED` leaves the identity fenced; `RECONCILED_RELEASED`
    makes it promotable again, and a subsequent evaluation promotes it.
30c. The record names which authority the release rested on: the adapter's
    attestation for a `FAILED` reservation, an operator's inspection for an
    unresolved one.
30d. Reconciling an identity appends to the current generation and starts no new
    one.
30e. A torn generation cannot be reconciled per-identity and can only be closed.
30f. A closed predecessor is **byte-identical** afterwards, and its recorded
    length and digest match it.
30g. The fenced set is the union over the chain: an identity committed in a
    closed predecessor is still refused after supersession.
30h. A torn predecessor is still read for fencing, up to its tear.
30i. A missing or altered predecessor is `GENERATION_CHAIN_BROKEN` and refuses
    ARMED.
30j. A temporary successor — zero-byte, partial, malformed, or complete but
    never renamed — supersedes nothing, and the predecessor stays authoritative.
30k. A leftover temporary is preserved byte-for-byte across a later attempt,
    which uses a distinct temporary name derived from its own `request_id`.
30l. Publication follows create-exclusive, write, `fsync` file, rename, `fsync`
    directory — asserted by the observed syscall order, not by the outcome.
30m. A retry after a lost acknowledgement rediscovers the published successor
    and creates no second one; a different `request_id` against an already
    superseded predecessor is refused.
30n. Two valid successors of one predecessor are `GENERATION_LINEAGE_FORKED`,
    and no timestamp, filename or directory order resolves it.
30o. A failure between the rename and the directory `fsync` stops further
    generation operations under `GENERATION_PUBLICATION_UNCERTAIN`.
30p. One sidecar covers the lineage: two coordinators on two generation files of
    the same lineage contend for the same lock.
30q. Closing a generation ends the closing process's ownership.
30r. A repeated `request_id` is reported with the existing record and applied
    once; a different one against a settled identity is `NOT_RECONCILABLE`.
30s. AST — no reconciliation record carries a free-text field, and the evidence
    token set is closed.
30t. Reconciliation calls no adapter and promotes nothing.

**Amendment G (§13f)**

31a. `BoundedCeiling` is not imported by the coordinator — AST — and no ceiling
    refusal is a merit finding.
31o. The C2 refusal names its subject: `OUTSTANDING_RESERVATION_CEILING_REACHED`
    is declared, and no ceiling code contains the component `UNRESOLVED`.

**Amendment H (§13g)**

32a. A creation receipt with matching operation identity and payload digest is
    `CREATED`.
32b. An existing operation with an exactly matching digest is `CREATED`, and no
    second object is created.
32c. An existing operation with a **different** digest is `UNKNOWN` and halts
    the adapter.
32d. A proven pre-submission refusal is `NOT_CREATED`.
32e. A timeout before any proven submission is classified from **positive
    transport evidence**, never from elapsed time.
32f. A timeout after one submitted byte is `UNKNOWN`.
32g. A disconnect after a complete request is `UNKNOWN`.
32h. A malformed, oversized or unauthenticated receipt is `UNKNOWN`.
32i. A created graph object followed by a coordinator crash leaves the
    reservation unresolved after restart, with no automatic retry.
32j. A terminal-record failure after `CREATED` preserves the graph object, halts
    appends in that process, and leaves the repair to reconciliation.
32k. The adapter is invoked with the coordinator lock **released**.
32l. A ceiling refusal invokes no adapter.
32m. SHADOW invokes no adapter.
32n. Raw responses, credentials, headers, URLs and exception strings reach
    neither the ledger nor `status()` nor the audit ring.
32o. The operation identity is derived from immutable ledger facts and is
    **identical** across two attempts at one reservation.
32p. A conformance declaration is required before ARMED is supportable, and its
    hash is part of the adapter's configuration identity.
32q. Once an attempt is `UNKNOWN`, no further adapter call is made for that
    reservation — AST and behaviour.

**Amendment I (§13h)**

33a. The observation record declares itself `NOT_A_PREDICTION`.
33b. The record carries the digest before and after, and states
    `LINEAGE_QUIESCENT` or `LINEAGE_NOT_QUIESCENT`; under the second it draws no
    conclusion about the cause.
33k. `ObservationSubject` and `ObservationOutcome` are distinct types, and the
    write session and the adapter refuse both by name.
33l. The synthetic path produces no `PromotionDecision` — AST over its public
    functions' returns.
33m. The observer holds no ownership scope, no `LedgerWriter` and no adapter,
    so no append exists for it to make.
33n. A verdict limit outside `1..MAX_OBSERVATION_VERDICTS`, or a duration
    outside `0 < d <= MAX_OBSERVATION_DURATION_S`, is `OBSERVATION_LIMIT_INVALID`
    and refuses **before** anything is read or written; no record is published.
33o. Both maxima are contract-declared constants: no argument, environment
    variable or setter changes them — AST and behaviour.
33p. The resolved output path is compared to the resolved lineage namespace
    after symlink resolution; containment is `OBSERVATION_PATH_REFUSED`.
33q. The record is published by §13e F.6's five steps, in that order, asserted
    through the same syscall seam.
33r. A second run at one path is `OBSERVATION_PUBLICATION_REFUSED` and leaves
    the first record byte-identical.

**Amendment J (§13i)**

34a. A coordinate not in the closed schema is `COORDINATE_NOT_IN_SCHEMA`.
34b. A list, tuple or mapping value is `NON_SCALAR_COORDINATE`.
34c. A string beyond `MAX_COORDINATE_CHARS` is `OVERSIZED_COORDINATE` — tested
    with base64-encoded bytes, which pass the first two refusals.
34d. `find_sample_bearing_field` is run over every record, and a hit anywhere,
    including nested, is `SAMPLE_BEARING_DETECTED`.
34e. Only `DERIVED_SCHEMA_CONFORMANT` derives `carries_samples=False`; a caller
    cannot supply the field, and `SAMPLE_STATUS_UNVERIFIABLE` does not derive it.
34f. `SAMPLE_BEARING_DETECTED` and `SAMPLE_STATUS_UNVERIFIABLE` each yield **no
    verdicts at all**, refusing before either checker runs.
34m. The reader opens **one descriptor**, requires a regular file, and refuses a
    FIFO, a device and a directory.
34n. The digest is computed over the bytes consumed; the path is not reopened
    during one read, and repointing it between opens changes no attested digest.
34o. `content_digest` excludes the provenance record, and `artifact_id` is
    derived from schema identity, canonical provenance and `content_digest`.
34p. Trailing bytes, a second provenance record, a duplicate artefact identity,
    a record-count mismatch and `CONTENT_DIGEST_MISMATCH` are each refused.
34q. `NaN`, the infinities, excessive precision, a `bool` where an integer is
    declared, and a numeric string are each refused as coordinate values.
34r. `MAX_ARTEFACT_RECORDS`, `MAX_ARTEFACT_BYTES`, `MAX_RECORD_BYTES` and
    `MAX_STRING_BYTES` are contract-declared and not configurable — AST.

**Amendment K (§13j)**

35a. A series whose consecutive records differ by less than
    `MINIMUM_RECORD_INTERVAL_NS` is `RECORD_INTERVAL_REFUSED`.
35b. A missing, repeated or backward `observed_monotonic_ns` refuses the
    artefact; strictly increasing is required, not merely non-decreasing.
35c. Two series may carry records at the same monotonic instant without refusal;
    the rule is per series and never global.
35d. The comparison uses integers only — AST: no `float()` and no wall clock on
    the interval path.
35e. Lowering or removing the floor makes the sub-millisecond record-stream test
    **stop refusing**, which is the control that replaces the unfalsifiable one.

**Amendment L (§13k)**

36a. The producer's entrypoints are family-specific: AST — no public function
    takes a bare mapping, and none accepts bytes, an array or a handle.
36b. A capture handle, socket, SDR object, byte stream, buffer-protocol object,
    generic mapping or sequence is refused by the **runtime nominal-type gate**,
    before any artefact file is opened or created.
36l. A subclass or proxy of an accepted type is refused: the check is nominal,
    not `isinstance`, so an override of `keys`, `__getitem__` or `__iter__`
    cannot reach the serializer.
36m. **Removing the runtime gate while leaving the annotations intact** lets a
    bytes-like object and a structurally compatible impostor reach behaviour
    they were previously refused from — the control that proves the annotation
    was never the boundary.

**Amendment M (§13l)**

37a. An artefact provenance omitting `measurement_status` or `instrument_state`
    is refused by the reader; the fields are required, not optional.
37b. The run's artefact and its observation record both carry
    `RF_MEASUREMENT_NOT_PERFORMED` and `INSTRUMENT_CONFIGURED_IDLE`.
37c. A manifest value labelled `CONFIGURED_NOT_EXERCISED` does not imply a
    measurement: the signal-chain hash identifies the declared idle
    configuration.
37d. The accepted position authority is `OPERATOR_DECLARED`, and the record
    carries the source and transfer separately — `PHONE_DISPLAYED_FIX` and
    `HAND_TRANSCRIPTION`.
37e. A phone-displayed fix is never recorded as `DEVICE_GNSS` or `DEVICE_FUSED`.
37f. Each fix is stamped with the observation process's `monotonic_ns` and names
    its monotonic source; no elapsed time is computed across a phone wall clock
    and a host monotonic value — AST.
37g. The run ends on 12 accepted fixes or 240 monotonic seconds, whichever is
    first, and both endings are normal.
37h. A run that produces no `NUMERIC_BALANCE_EXCEEDED` is valid and complete.
37i. A missing fix ends the run on its bounds and never substitutes constructed
    evidence.
37j. AST — the run opens no SDR device, starts no `rtl_tcp`, allocates no IQ
    buffer, opens no socket, starts no subprocess or thread, and names no PID.
36c. Every provenance claim names the component that supplied it; a claim with
    no named source refuses publication.
36d. The producer neither writes nor accepts `carries_samples` — AST and
    signature.
36e. Publication follows §13e F.6's five steps in order, through the same
    syscall seam.
36f. A forbidden value aborts publication and **no file receives the final
    name**; the temporary is preserved and is not readable as evidence.
36g. A foreign temporary is never removed by a later run.
36h. Repeating a run identity rediscovers the existing artefact; a differing
    digest under one identity is `REQUEST_DIGEST_CONFLICT`.
36i. The producer is disabled by default and refuses with
    `PRODUCER_NOT_ENABLED`.
36j. AST — the producer imports no socket, no subprocess, no threading, no
    bridge, no ring, no adapter, and names no PID.
36k. Every structural exclusion the producer applies is also applied by the
    reader, and the reader's refusal is what admission depends on.
34g. The reader exposes no write path: AST — no write-mode `open`, no `os.write`,
    no rename, no unlink.
34h. An artefact beyond `MAX_ARTEFACT_BYTES` or `MAX_ARTEFACT_RECORDS` is
    refused before any record is yielded.
34i. The artefact's kind set is disjoint from the ledger's: a `Lineage` rooted
    at its directory discovers no generation, and `read_ledger` refuses it.
34j. The observation record names the artefact by **digest**, not only by path.
34k. The recorded `signal_chain_hash` is reported and never compared against
    anything the reader invented.
34l. A missing artefact is `DERIVED_EVIDENCE_UNAVAILABLE` and never a fallback
    to constructed evidence.
33c. The record is written outside the lineage root, and a `Lineage` rooted
    there does not discover it as a generation.
33d. The record is written once and never appended to: a second run at one path
    is refused rather than extending the first.
33e. A run ends on `VERDICT_COUNT_REACHED` or `OBSERVATION_DURATION_REACHED`,
    whichever is first, and both are normal endings.
33f. The duration bound is monotonic; no decision path reads a wall clock — AST.
33g. A run that ends on an exception still writes its record, marked
    `OBSERVATION_INTERRUPTED` and naming the bound it did not reach.
33h. The observer initiates no capture and opens no socket — AST.
33i. The observer appends nothing to the ledger and constructs no ARMED
    coordinator and no adapter — AST.
33j. Merit and executability counts are reported separately in the record, and
    durable and simulated ceiling totals are named apart.
31b. Every declared ceiling states subject, accounting source, scope, reset rule
    and refusal; a ceiling missing any of them is refused at construction.
31c. C1 counts reservations regardless of terminal outcome: a writer that never
    answers advances it.
31d. C1 resets on publication of a valid successor, and **C2 does not**.
31e. C2 counts `RESERVED` with neither a valid terminal nor a valid
    reconciliation record; a `FAILED` reservation does not advance it.
31f. C2 counts across the whole validated lineage, including closed
    predecessors.
31g. C2 decreases only on a valid reconciliation record, and not on a terminal
    one arriving late, a restart, or a generation closure.
31h. A ceiling refusal writes no record, durable or in-memory: the file is
    byte-identical and the identity map unchanged.
31i. Both totals are rebuilt from the ledger at startup and survive a restart.
31j. A test recomputes both totals from the files and compares them to the
    maintained values.
31k. The ceiling values are not configurable: no constructor argument,
    environment variable or setter changes them — AST and behaviour.
31l. `status()` publishes the ceiling configuration identity, and it changes
    when a declared value changes.
31m. Durable and simulated totals are published under different names; a SHADOW
    coordinator reports a **non-zero** durable C2 when the lineage holds
    unresolved reservations from an earlier run, and `NOT_SIMULABLE` for its own
    C2 simulation.
31n. Calibration: replacing `RESERVATION_CEILING` with `8` makes ordinary
    budget-valid operation reach the catastrophe ceiling, and the calibration
    test fails.
28i. A failed or unresolved write is never readable as a committed promotion:
    the reader's `committed` set contains only identities whose terminal record
    is `COMMITTED`.
28j. Exception messages, returned values and adapter free text do not reach any
    durable record.

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
16. **Sequence gaps are legal** (§13c D.1). Refusing them would make a lost
    record render the whole ledger unreadable — converting the recoverable
    failure into the unrecoverable one.
17. **A torn ledger is never appended to, and never repaired by the writer**
    (§13c D.2). Appending turns a contained torn tail into `LEDGER_UNREADABLE`
    by the reader's own corruption rule; repairing it lets a writer destroy
    evidence to make its own next operation legal.
18. **An empty ledger refuses ARMED rather than being silent or unreadable**
    (§13c D.3). It is readable and correct; what it lacks is the generation C1
    totals over, and a ceiling with an undeclared scope cannot be enforced.
19. **§17 slice 6 divides, and the write path lands behind a closed gate**
    (§13c D.4). A write path that merely lacks its guard is one someone can
    call; one that refuses every append until a later slice teaches it to prove
    ownership cannot be used early by accident.
20. **The ownership proof is a live scope, not a flag** (§13c D.4). A boolean or
    a recorded attestation permits attest-release-append, which passes the check
    with the guarantee already gone — a time-of-check-to-time-of-use hole with
    none of the difficulty that usually accompanies one.
21. **A generation is never established as a side effect of a write** (§13c
    D.3). A generation that began as a side effect is one nobody authorized or
    dated, and C1 is a lifetime total over exactly that boundary.
22. **Ownership is taken on a derived sidecar, not the ledger** (§13d E.1). A
    ledger that does not exist cannot be opened to lock it, and a *configured*
    lock file would reproduce Amendment A's two-coordinators failure exactly.
23. **The mount attestation gates scope creation, not only ARMED** (§13d E.2).
    A gate checked somewhere other than where it is relied on is not a gate.
24. **Restart seeding lands with the connection, not after it** (§13d E.5). A
    coordinator with a durable ledger it does not read at startup re-promotes
    everything in it — worse than having no ledger, because it looks durable.
25. **A refused durable append takes no reservation at all** (§13d E.4). The
    alternative is a fence that exists until the next restart and then does
    not.
26. **Reconciliation supersedes and never repairs** (§13e). It keeps §13c D.2
    intact while making `RETRY_REQUIRES_OPERATOR` actionable, which looked like
    a tension and was not.
27. **The successor does not copy the predecessor's identities forward**
    (§13e F.5). Copying makes the predecessor ceremonial; reading the chain
    makes a missing predecessor detectable instead of a silently smaller fence.
28. **There is no pointer to the live generation** (§13e F.6a). The authoritative
    generation is the one nothing supersedes, and a pointer would be a second
    answer that can disagree with the first.
29. **Closing a generation ends the closing process's ownership** (§13e F.6c) —
    and the reason changed under review. The first was *the successor is a
    different lock*, which F.10 removed. The rule stands on re-seeding being the
    startup path, and one path being worth more than one saved restart.
31. **Publication is a protocol, not a moment** (§13e F.6). "The header reaches
    disk" does not say what happens to a header half-written under its final
    name, and `fsync` on a file says nothing about its directory entry.
32. **A fork is never resolved by timestamp, filename or directory order**
    (§13e F.6a). Each is a property of the filesystem's bookkeeping rather than
    of the evidence.
33. **Leftover temporaries are preserved, unlike sidecars** (§13e F.6). A
    sidecar is a lock artefact; a partial successor may be the only record of
    what someone was doing when the machine stopped.
34. **One lineage, one lock** (§13e F.10, amending §13d E.1). Locks on two
    generation files of one lineage exclude nobody who matters.
35. **`BoundedCeiling` is not reused, only its discipline** (§13f G.1). Its
    violations are merit findings; these refusals are executability codes, and
    reuse would be §5's contamination arriving through a shared class.
36. **C2 is lineage-wide** (§13f G.3). Were it per-generation, an operator
    facing C2 could clear it by closing the generation — the refill-through-the-
    reset-path failure §11 was written to close.
37. **C1 should never fire and C2 is meant to** (§13f G.4). One calibration test
    does not fit both, and §11's belongs to C1 alone.
38. **The values are contract-declared, not configurable** (§13f G.4). A ceiling
    a deployment can raise is a ceiling that will be raised at the moment it
    first fires, which is the moment it is doing its job.
39. **The C2 code names its subject rather than the state it counts** (§13f
    G.8). `UNRESOLVED` is already doubled in this tree, and a judgement would
    have made a third use permanent on the grounds that the prose uses the word
    — which is a reason to keep the prose.
40. **Durable and simulated totals are never published under one name** (§13f
    G.7).
41. **GraphOps failure is not proof that nothing happened** (§13g). A refused
    connection looks like nothing happened, and looking like nothing happened
    is the evidence this contract does not accept.
42. **One reservation addresses one operation identity, forever** (§13g H.1). A
    fresh identity on retry makes *the same reservation twice* and *two
    reservations* indistinguishable at the far end, and the fence stops at our
    side of the wire.
43. **SHA-256 for the operation identity, not blake2s** (§13g H.1). Everything
    internal uses blake2s; this one must be computable by a system that did not
    choose our hash. Recorded so a tidy-up finds the reason before the edit.
44. **Having a query method is not having the authority to use it** (§13g H.6).
    The adapter acquiring a lookup is exactly when that stops being obvious.
45. **SHADOW observes the apparatus, not a promotion rate** (§13h I.1). §12's
    stated purpose is unachievable: ARMED's rate depends on a requester that
    does not exist, and synthesizing one would measure a system in which every
    finding is requested — which the policy's own rule forbids.
46. **The observer acquires nothing** (§13h I.3). A slice that captured in order
    to observe would have to argue it handled raw IQ correctly; this one has no
    buffer to argue about.
47. **Synthetic input is a distinct type, not a flagged request** (§13h I.2a).
    A flag can be omitted or falsified and the call site does not show whether
    the claim was true — the ownership-boolean problem, one layer up.
48. **A configurable bound is not a bound** (§13h I.5). Contract-declared
    maxima, because a limit a deployment can raise will be raised at the moment
    it first binds.
49. **Equal digests prove nothing wrote, not that we did not** (§13h I.4a).
    §12 permits SHADOW beside an ARMED writer, so the observer cannot tell its
    own writes from another's, and the honest report of that is silence about
    attribution.
50. **A failed observation still writes its record** (§13h I.7).
52. **A closed schema proves representation, not origin** (§13i). The first
    draft claimed it could not carry raw IQ; one sample per scalar record, a
    covert archive in numeric mantissas, or coordinates at sample cadence all
    pass it. The guarantee is stated at the strength it has.
53. **`carries_samples` is reader-derived and qualified** (§13i J.3). Only
    `DERIVED_SCHEMA_CONFORMANT` derives it false, meaning the structural
    exclusion profile passed — never that a dishonest producer is impossible.
53a. **Origin is attested by the producer, never inferred by the reader** (§13i
    J.3). Some properties can only be declared by the party that has them.
53b. **A digest cannot cover the header that carries it** (§13i J.7a). The
    content digest excludes provenance, and the artefact identity is derived
    from it rather than the reverse.
53d. **A cadence floor bounds information rate, not honesty** (§13j K.4). It
    establishes that the artefact holds no high-rate record stream under its
    declared timeline, and a producer can space scalars a millisecond apart or
    falsify timestamps outright.
53f. **Family-specific entrypoints, never a generic mapping** (§13k L.1). A
    mapping parameter is a hole shaped like anything.
53j. **An annotation is not a gate** (§13k L.1a). In Python it refuses nothing
    at the call boundary; runtime nominal validation is what refuses, and
    static typing is defence in depth. Writing the annotation and believing it
    is the whole guard is the failure the correction names.
53k. **The type check is nominal, not `isinstance`** (§13k L.1b). A subclass may
    override an accessor and return anything at the moment the serializer asks,
    so what is protected is what the object *is* rather than what it can do.
53g. **Provenance is attested upstream and names its source** (§13k L.2). A
    claim whose source is unnamed is one nobody can later question.
53h. **Producer checks are defence in depth; the reader stays authoritative**
    (§13k L.4). A check the reader did not repeat would be a guarantee held by
    the party with the most reason to be wrong about it.
53l. **A capture would have changed no verdict input** (§13l M.1). The checker
    never sees a sample, so activating the Q1 surface would have bought a
    signal chain that measured and an artefact identical in every field the
    verdict depends on.
53m. **A fix read off a screen and typed in is a typed fix** (§13l M.2). The
    authority degrades at transcription, and the record names the step where it
    did rather than reporting where the number was born.
53n. **The sensitive datum is the one the design requires** (§13l M.6). The
    artefact must carry the coordinates and must not carry the displacement, so
    minimization is a retention decision made in the open rather than a schema
    problem with a solution.
53o. **An induced ceiling demonstrates nothing** (§13l M.5). A run producing no
    violation is complete; baiting the invariant proves only that its conditions
    can be arranged.
53i. **Disabled by default carries the other reachability rules** (§13k L.5). A
    producer that does nothing until someone turns it on cannot be reached by an
    accident, a default, or a test that forgot where it was running.
53e. **The rate rule is per series, never global** (§13j K.3). Two families may
    legitimately observe one moment, and a global floor would refuse an honest
    artefact — the false positive that teaches an author to widen a bound until
    it means nothing.
53c. **Attest the descriptor, not the path** (§13i J.7). A path is a name and
    can be repointed between opens; a digest from a second open attests bytes
    the verdicts did not come from.
54. **A declared maximum string length is part of the sample bar** (§13i J.5).
    A base64 blob is one long string and passes both the obvious checks.
55. **The interface is a reader; writing artefacts is a separate act** (§13i
    J.4). *No acquisition* is then untouched rather than carefully preserved.
56. **The signal chain is recorded and not verified** (§13i J.6). A reader with
    no live receiver that compared against something it invented would be
    manufacturing authority. One that
    produces nothing when it fails cannot be told from one that never started,
    and that difference is what the record was for.
51. **An endpoint does not imply a capability** (§13g H.7). Conformance is a
    reviewed declaration hashed into configuration identity, at a boundary where
    the other party can change without telling us. SHADOW cannot generate new unresolved reservations and can certainly
    observe real ones, and reporting zero would be a false statement about the
    record rather than an honest one about simulation.
30. **Operator evidence is a closed token set with no notes field** (§13e F.7).
    This is the record of a human decision about evidence, and free text is
    where the reasoning goes to stop being checkable.

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
6. Durable ledger: write path, fsync discipline, sequence and generation
   (§13c D.1, D.3). The append is gated on an ownership attestation this slice
   cannot produce (§13c D.4).
6b. Durable ledger: `fcntl.flock` ownership for the process lifetime, which is
   what makes the gate above openable — by supplying the live scope, from
   inside which the internal append is invoked. Also the coordinator
   connection and restart seeding (§13d E.4, E.5), because a durable ledger
   the coordinator does not read at startup is worse than none.
7. Reconciliation and generations (§8, §11, §13e). Per-identity reconciliation
   and generation closure are two operations, and the second ends the closing
   process's ownership.
8. Ceilings C1 and C2 (§11).
9. Execution adapter with one fixed WriteBus schema (§13g). Stable operation
   identity, closed command, closed result, evidence-based classification, and
   a deterministic fake GraphOps for conformance tests.
10. Live SHADOW observation of the **apparatus** (§13h), bounded, foreground,
    in-process, initiating no capture, and writing one record outside the
    lineage.
11. Separate explicit authorization before any ARMED graph mutation.

**Slices 4 and 5 stay separate**, and this is the boundary to protect if anything
is compressed: the lock is correct and testable without durability, and
durability is where the subtle bugs are.
