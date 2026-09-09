# Promotion Execution Contract

```text
Status:                 PROPOSED — not accepted, nothing implemented
Authority:              NORMATIVE once accepted
Constrains:             Step 4 of the promotion sequence (execution adapter)
Depends on:             scythe_promotion_policy.py  (v2 identity, MERGED)
                        scythe_promotion_ledger.py  (shadow coordinator, MERGED)
                        rf_capture_recovery.ProcessIdentity (MERGED, reused)
Atomic reservation:     NOT_IMPLEMENTED
Durable ledger:         NOT_IMPLEMENTED
Execution adapter:      NOT_IMPLEMENTED
ARMED graph mutation:   REQUIRES SEPARATE EXPLICIT AUTHORIZATION (step 6)
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

Neither is fixed by the other. §3 answers the first, §5–§9 the second.

---

## 1. Terminology

| term | meaning |
| --- | --- |
| **identity** | A promotion identity, `promotion_identity()` under digest revision v2. |
| **reservation** | A durable claim on one identity. Not a claim that a record exists. |
| **terminal record** | The `COMMITTED` or `FAILED` entry that resolves a reservation. |
| **indeterminate reservation** | A reservation with no terminal record. Neither success nor failure. |
| **incarnation** | `ProcessIdentity(boot_id, pid, start_ticks)` — the existing type, reused, not re-invented. |
| **the ledger** | The durable, single-writer, append-only file defined in §5. |
| **the window** | The process-local budget structure defined in §5. Not the ledger. |

---

## 2. What a reservation means

> **A reservation is a claim on an identity. It is not a claim that a record
> exists in the graph.**

This is the load-bearing definition, and everything below follows from it.

The reason it must be a claim on the identity and not on the record is that the
adapter boundary cannot tell the two apart. `WriteResult(accepted=False)` is
returned for a rejection, for a timeout, and for a write that landed whose
acknowledgement was lost. Those are indistinguishable from outside the bus.

So **retaining the reservation across a failed write is not a convenience; it is
forced.** Releasing it would mean re-promoting an identity whose record may
already exist — a duplicate in GraphOps, which is precisely the outcome the
ledger exists to prevent. Nothing downstream can tell a duplicate from a second
real finding, which is why the asymmetry runs the way it does:

- A lost finding is recoverable. The check can be re-run against the same
  evidence and will produce the same identity.
- A duplicate record is not recoverable. It is indistinguishable from evidence.

Re-promotion after a failure is therefore an operator action taken against the
graph, never an automatic retry.

---

## 3. The critical section

```
  ┌── coordinator lock held ───────────────────────────────────┐
  │  1. snapshot the identity set                              │
  │  2. decide_promotion(..., already_promoted=snapshot)       │
  │  3. budget check against the window                        │
  │  4. append RESERVED to the ledger, fsync                   │
  │  5. add to the window                                      │
  │  6. audit PROMOTION_ATTEMPTED                              │
  └── release ─────────────────────────────────────────────────┘
     7. call the writer                    <- outside the lock
     8. append COMMITTED or FAILED         <- no fsync required (§6)
     9. audit PROMOTION_RECORDED / PROMOTION_FAILED
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
its own `RLock` and is called at step 6, inside the coordinator lock. `PromotionAudit`
does not know the coordinator exists and must not learn: nothing holding the
audit lock may call back into the coordinator.

---

## 5. Two structures, not one

`_promoted: List[(identity, monotonic_ns)]` currently serves both idempotency and
rate limiting. They have different persistence semantics and must be separated
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
by a different mechanism entirely — §9, not a longer window.

---

## 6. Two-phase records

Each promotion writes two ledger entries.

```json
{"kind":"RESERVED","seq":41,"identity":"promotion:…","target_graph":"…",
 "record_class":"INVARIANT_FINDING","incarnation":{"boot_id":"…","pid":…,
 "start_ticks":…},"monotonic_ns":…,"utc_display":"…","crc":"…"}
{"kind":"COMMITTED","seq":42,"reserves":41,"crc":"…"}
```

`RESERVED` **must** be fsynced (§3). The terminal record **need not** be: losing
it degrades the reservation to indeterminate, which is the safe direction.

**A `RESERVED` with no terminal record is `INDETERMINATE`.** The write may have
landed and may not have. This is the project's existing vocabulary and its
existing rule applies without amendment: an `UNDETERMINED` result must not be
converted into a failure. Therefore an indeterminate reservation:

- **blocks re-promotion of its identity** — fail closed against duplicates;
- **is surfaced and counted** in `status()`;
- **is never auto-retried and never auto-released.**

Adjudication is an operator action, taken by looking at the graph. The ledger
cannot perform it, because the ledger is exactly the thing that does not know.

---

## 7. Ownership

**Exactly one writer.** Two coordinator processes sharing one ledger reproduce
the race of §3 one level up, where a mutex cannot reach it.

- The writing coordinator holds `fcntl.flock(fd, LOCK_EX | LOCK_NB)` for its
  entire lifetime, acquired before ARMED is reachable.
- Failure to acquire refuses ARMED. It does **not** refuse SHADOW, which opens
  the ledger read-only (§10).
- The holder writes its `ProcessIdentity` into a header record, so a reader can
  name the owner rather than infer one. This is the same incarnation type the
  recovery work already uses; a second identity type would be a second answer to
  a question that has one.

**Location: outside the repository.** A configured state directory, following
the pattern already used for the ARMED acceptance snapshots. A promotion ledger
inside the tree would put claims about graph records under version control,
where a checkout could silently move the fence.

**No other component appends.** Not the checker, not the adapter, not the model
path. The standing rule that the checker must not call WriteBus directly applies
here in the same shape: a validation tool that can move the fence is no longer a
validation tool.

---

## 8. Restart

| rebuilt from the ledger | not rebuilt |
| --- | --- |
| the identity set | the window (§5) |
| indeterminate reservations | the audit ring (in-memory, bounded, by design) |
| the durable total (§9) | |

**A missing ledger is a missing fence, and ARMED must be refused.** Starting from
an empty identity set after the file is lost would silently re-enable every
promotion ever made. The empty-file case and the missing-file case are therefore
distinguished: a ledger created by this contract's own initialization is empty
and valid; a ledger that is absent where one was configured is a refusal.

---

## 9. The cross-restart bound is a ceiling, not a window

A crash loop restarts the process, and §5 resets the window on restart. Without
a second bound, a crash loop would refill the budget on every restart — the
amplification the breaker exists to prevent, arriving through the breaker's own
reset path.

The bound is a **one-sided ceiling on the durable total**, not a longer window:

```
total records written to target graph G by this ledger  <=  CEILING
```

Clock-free, so it survives restarts where a window cannot, and it is the
`BoundedCeiling` invariant class this repository already defines rather than a
second rate concept. Reaching it refuses promotion and requires explicit
re-authorization; it does not decay.

This needs one new refusal reason in `scythe_promotion_policy`
(`DURABLE_CEILING_REACHED`), which is a policy amendment and therefore its own
slice, ordered before the implementation.

---

## 10. SHADOW against the durable ledger

SHADOW **reads** the ledger and **appends nothing**. It seeds its simulated
identity set from real history.

Without seeding, SHADOW measures a system that does not exist: starting from an
empty history it systematically under-counts `WOULD_BE_REFUSED` and over-counts
`WOULD_PROMOTE`, and the whole purpose of the shadow slice is to predict what
ARMED would do. This is the same argument that gave SHADOW a simulated budget in
the first place.

SHADOW does not take the exclusive lock, so it may run beside an ARMED writer.
It therefore reads a file that is being appended to, and must tolerate a
truncated final line exactly as startup does (§11).

**Testable property:** the ledger file is byte-identical before and after a
SHADOW run.

---

## 11. Corruption and growth

**A truncated final line refuses ARMED and permits SHADOW.** A crash mid-append
leaves a partial record whose identity cannot be read. The one thing the ledger
must not do is guess which identity was in flight, so it declares the condition
rather than resolving it. Each line carries a checksum so truncation is detected
rather than inferred from a parse error.

**Growth is bounded by refusal, not by pruning.** At the current budget the
ledger accrues on the order of 1,150 records/day. Compaction is a separate slice
with its own correctness argument — pruning an identity set is deleting a fence,
and it must be shown that the pruned identities can never recur. Until then the
ledger declares a maximum record count and refuses ARMED beyond it.

---

## 12. What this does not do

- It does **not** make the graph write idempotent. It prevents *this coordinator*
  from writing an identity twice. Anything else invoking the adapter is outside
  the fence.
- It does **not** detect a record present in the graph but absent from the
  ledger. The ledger is not a mirror of the graph and must never be read as one.
- It does **not** adjudicate indeterminate reservations. It surfaces them (§6).
- It does **not** synchronize two coordinators. It refuses the second (§7).
- It is **not evidence.** It records claims on identities. A reservation is not a
  finding, and a promotion is not a confirmation — the verdict was already true
  or false before anything was written.

---

## 13. Acceptance tests

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
8. A ledger holding a lone `RESERVED` record → that identity is refused on
   restart, reported as indeterminate, neither auto-retried nor auto-released.
9. Restart rebuilds the identity set; a previously committed identity is refused.
10. A SHADOW run leaves the ledger byte-identical, and seeds its simulated set
    from it.
11. A truncated final line refuses ARMED, permits SHADOW, and is surfaced.
12. A second coordinator on the same ledger path refuses ARMED.
13. A missing ledger file refuses ARMED rather than starting empty.
14. `status()` reports `budget_window_survives_restart: false`, and the window is
    empty after a restart.
15. The durable ceiling refuses promotion and does not decay.
16. Ledger records carry both `monotonic_ns` and `utc_display`, and no decision
    path reads the UTC field — AST scan, matching the existing scope tests.

---

## 14. Questions for the reviewer

1. **Indeterminate reservations and ARMED.** Proposed: an indeterminate
   reservation blocks its own identity, and ARMED start is refused while any
   exist unless the coordinator is constructed with an explicit acknowledged
   count. The alternative — block only the identity, let ARMED proceed — is less
   fail-closed but keeps ARMED reachable after a single bus timeout.
2. **The ceiling.** Its value, and whether it is per target graph or global.
3. **Ledger location.** Which state directory, given the existing snapshot path
   convention.
4. **Where the ceiling is enforced.** Proposed: as a policy refusal reason, since
   refusals are the policy's vocabulary and the coordinator does not own them.

---

## 15. Slice order

Following the accepted pattern — amendment first, implementation only after the
amendment is accepted:

1. This contract, accepted.
2. Policy amendment: `DURABLE_CEILING_REACHED` refusal reason (§9).
3. Atomic reservation over the existing in-memory structures (§3, §4) — closes
   the race without introducing a file.
4. Durable ledger, read path and restart (§5–§8, §10, §11).
5. Durable ledger, write path.
6. Execution adapter with one fixed WriteBus schema.
7. Live SHADOW observation.
8. Separate explicit authorization before any ARMED graph mutation.

Steps 3 and 4 are separable and should stay separate: the lock is correct
without the file, and the file is testable without the adapter.
