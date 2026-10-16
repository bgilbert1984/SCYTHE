# Metamorphic harness r4.1 — Gate D: replay execution protocol (design only; no execution authorized)

> **PROTOCOL DRAFT FOR REVIEW — NO CONNECTION, TRANSFER, DECLARATION, PREFLIGHT, LAUNCH, MONITORING OR RETRIEVAL IS AUTHORIZED BY THIS DOCUMENT.** It specifies the exact Gate D request so that it can be reviewed before any of those actions. This document adds itself only; it changes no production code, harness, runner, package, comparator, Gate A–C protocol or evidence, and amends nothing already merged — a **new** document, per the standing rule that frozen protocols are revised by a new revision, not edited in place.

> **Revision history.** Revision 1 merged as PR #175 (`8ae6d66a`). **Revision 2** (PR #176) corrects two issues found while building the §5 monitor against Revision 1's text, before any Gate D attempt ran: (a) §5.4's peak-memory figure conflated a true simultaneous measurement with a nonconcurrent historical sum under one "authoritative" label, which could overstate actual concurrent memory use; (b) §5.2/§7 step 9 required the monitor to bind to a launch PID that, under the step 8-then-9 ordering, might not exist yet when the monitor starts, with no readiness signal of its own. Both are corrected below (§5.2, §5.4, §5.5, §7 steps 8–9 as they stood in Revision 2) by the same reviewer who approved Revision 1, as part of the separately reviewed monitor-implementation PR that Revision 1 itself called for. No other section changes; §3, §4, §6, §8 (apart from the matching STARTUP-failure wording), §9, §10, §11 are unchanged from Revision 1. This revision does not authorize anything Revision 1 did not, and does not retroactively characterize any prior attempt.
>
> **Revision 3** (this text, PR #181) corrects a genuine execution gap found while drafting the first real Gate D request (attempt `GATED3-20261016T011954Z-7dc517`, local draft, never authorized): `gate_d_monitor.py` resolves PID/RSS state by directly opening `/proc/<pid>/...` on the host it runs on, so it must run **on Dedirock itself**, over the authorized SSH session — it cannot observe a remote host's `/proc` from the operator's workstation. Revision 2's §7 step 4, however, names **exactly four** frozen Gate C inputs to transfer, and does not include the monitor; there was no protocol-authorized way to get it onto the replay host. This revision adds a new, narrowly scoped subsection within Control 3, **§5.10** — a distinct monitor-staging-and-verification operation, after `CHECK_ONLY`/`RECHECK` succeed and immediately before monitor startup, never folded into the Gate C four-input boundary set by §7 step 4, which remains exactly as merged. This inserts one new step into §7's command sequence (new step 8); **every step from former step 8 onward is renumbered by one** (monitor start 8→9, launch 9→10, observe 10→11, stop monitor 11→12, build inventory 12→13, retrieve 13→14) — §7's own step 1–7 numbering, and every other section's cross-references to steps 1–7, are unchanged. References elsewhere in this repository to "protocol §7 steps 8–9" (e.g. the Gate D monitor rehearsal evidence, PR #177, which rehearsed the monitor's PID-binding and readiness logic against a local stand-in harness, not a remote one) describe Revision 2's numbering and remain accurate as history; they do not describe this revision's renumbered steps. §5.2's cross-reference to "§7 step 8" is corrected to "§7 step 9" to match. This revision also corrects the forward-looking authorization language in §10, per a gap identified in the Gate C bounded-authorization addendum (`docs/evidence/r4_gate_c_attempt_GATED2_2026-10-15/governance/GATE_C_BOUNDED_AUTHORIZATION_ADDENDUM_2026-10-15.md`): an authorization may now unambiguously reference a frozen, already-reviewed Gate D request document without reproducing any command verbatim. Two documentation errors found at review, before merge, are also corrected here: §5.2's readiness
cross-reference named step 9 (monitor start itself) as what proceeds only after `MONITOR_READY`,
when it is step 10 (the harness launch) that does; and §10 described the §5 monitor as not yet
implemented, when it has existed as merged code since PR #176 — corrected to say this protocol
does not stage or execute that already-implemented monitor, rather than that the monitor does not
exist. §5.10 also gains item 7, recording that the monitor's own detachment mechanism on the
replay host (surviving the SSH session, remaining reachable for §5.7's `SIGTERM`) is an execution
detail to be validated by a local process-lifecycle rehearsal before any real request's step 9
text is frozen — not asserted correct by this revision, and not itself a further protocol gap
blocking this merge. No change to §2, §3, §4, §6 (apart from one clarifying cross-reference in §6.1 item 5), §9, or §11; no change to any frozen scientific tolerance, the r4.1 package, the comparison protocol, or any historical evidence. This revision does not authorize anything, retroactively characterize any prior attempt, or itself permit any Dedirock connection.
>
> **Revision 4** (this text, PR #182) closes a small retrieval-scope gap §5.10 item 7's own
> validation (a local-only process-lifecycle rehearsal, run after Revision 3 merged, not yet
> published as evidence) surfaced: the detachment idiom it validates (`setsid ...
> </dev/null >>LOG 2>&1 & ... ; disown`) redirects the monitor's and the harness's stdout/stderr to
> `monitor_stdio.log`/`harness_stdio.log` on the replay host — diagnostic custody for a startup
> failure that would otherwise be lost, since neither file is covered by §6.1's existing
> categories. Per the owner's explicit instruction ("amend the protocol before requesting their
> retrieval" — not named per-request, which would have been the alternative), this revision adds
> both as a new §6.1 item 6 and updates §6.3's retrieval list to match. No pre-known expected
> digest exists for either file (they are freshly generated output, not a frozen input); each is
> digested and the digest recorded as custody immediately on retrieval, the same treatment §6.1
> items 2, 3 and 5 already receive. No other section changes; no change to any frozen scientific
> tolerance, the r4.1 package, the comparison protocol, the Gate C four-input transfer boundary
> (§7 step 4, unchanged by this or any revision so far), or any historical evidence. This revision
> does not authorize anything, retroactively characterize any prior attempt, or itself permit any
> Dedirock connection.
>
> **Revision 5** (this text, PR #183) resolves, by a narrowly scoped protocol clarification rather
> than by a single request document's own interpretation, a nuance raised during review of the
> draft GATED3 request: §4's "immediately before `run_job.sh` is invoked, and in the same shell
> session with no intervening reboot" was read informally as meaning host continuity only, without
> the protocol itself ever saying so. Adds §4.1, naming and separating three kinds of continuity:
> **host continuity** (no reboot — already enforced between `CHECK_ONLY` and `RECHECK` via
> `RECHECK`'s own FROZEN `boot ID` field, but, before this revision, never re-verified between
> `RECHECK` (§7 step 7) and launch (§7 step 10), across the two intervening, separately-invoked
> steps 8 and 9); **execution continuity** (the frozen numerical environment and declaration
> identity — already, and still only, enforced at `RECHECK`'s own moment by its FROZEN-field
> table, not independently re-verified again closer to launch by this revision); and **session
> continuity** (separate SSH invocations for steps 6 through 10, which this protocol has always
> used and explicitly permits, conditioned on host continuity being independently re-established
> immediately before launch). Closes the real gap host continuity had by requiring a `boot_id`
> comparison immediately before step 10, added as a new §8 stop condition on mismatch. States
> explicitly, per the gap this clarification exists to close, that a `boot_id` match proves only
> the absence of a reboot — it is not represented as proof that no relevant process or
> configuration changed, which remains execution continuity's, not host continuity's, concern. §4
> itself gains the new §4.1 subsection described above; its existing table and opening sentence
> are otherwise unchanged. No change to §2, §3, §5, §6, §9, §10, or §11, apart from one new §8
> stop condition (the pre-launch `boot_id` mismatch, matching §4.1's requirement); no change to
> any frozen scientific tolerance, the r4.1 package, the comparison protocol, the Gate C
> four-input transfer boundary, or any historical evidence. This revision does not authorize
> anything, retroactively characterize any prior attempt, or itself permit any Dedirock connection.

## 1. What this is and is not

This elaborates `metamorphic_r4_cross_host_replay_protocol.md` §6 ("Execution (Gate D)" and "Return path") into an exact, reviewable request: commands, monitoring method, stop conditions, expected outputs and custody. Section numbers below (§3–§11) are this document's own, except where a cross-reference explicitly names `metamorphic_r4_cross_host_replay_protocol.md` or `metamorphic_r4_gate_c_preflight.md` by filename. This document does not reopen the cross-host protocol's own §3 (what is frozen), §4 (field classes and tolerances), or §5 (disposition vocabulary) — those stand as merged. It does not resolve the local launch-authorization discrepancy (§11, this document).

## 2. Preconditions, as they stand today

| Item | Status |
|---|---|
| Gate A (local evidence) | done, PR #169 |
| Gate B (comparison protocol) | done, PR #170 |
| Gate B′ (comparator) | done, PR #171, sanity-checked bitwise-equivalent against itself |
| Gate C tooling | done, PR #172–174, merged `622bec3` |
| Gate C execution, attempt 312512 | `PASS`, local operator declaration window `2026-10-14T06:12:25Z`–`10:12:25Z`; read-only audit 18/18 passed; two scope deviations disclosed, neither retroactively authorized |
| Gate D | **not authorized**; this document is preparation only |

The attempt-312512 declaration window is **not** a standing authorization for Gate D. If it has lapsed by the time Gate D is requested — and `10:12:25Z` on 2026-10-14 is in the past for any request made after that moment — a **fresh** operator declaration and a **fresh** Gate C `CHECK_ONLY` are required before Gate D can even be preflighted; the existing attempt-312512 record cannot be stretched to cover a new window.

## 3. Control 1 — operator window and declaration refresh

A changed operator declaration is a changed **identity** under `gate-c-operator-declaration/1` (operator, host, package digest, window start/end, declaration timestamp, scope text, plus the file's own SHA-256). The Gate C tool's `RECHECK` mode requires the frozen `CHECK_ONLY` record's declaration identity to equal the one presented at `RECHECK`, or it refuses (`metamorphic_r4_gate_c_preflight.md` §9.2, §9.4). That rule is the correct one and this protocol does not change it — which means a new declaration cannot be fed into `RECHECK` against the attempt-312512 record and must not be made to look identical by construction:

1. The operator drafts and keeps a **new** declaration file, same schema, naming the actual intended window (exactly 4 hours, as before).
2. A **fresh Gate C `CHECK_ONLY`** is run on the replay host under a **new attempt label**, with that new declaration. This re-measures the REMEASURED fields (free space, memory, load, competing processes) and re-records the FROZEN fields; it does not assume any of attempt 312512's measurements still hold.
3. Only that fresh `CHECK_ONLY` record is eligible to be the basis of the `RECHECK` immediately before launch (§4). Attempt 312512's record is retained as history and is not reused as the launch-time baseline.

This closes the gap the reviewer identified: RECHECK must never be asked to treat a new declaration as identical to an old one, because step 2 means RECHECK is always run against a record that already carries the current declaration's identity.

## 4. Control 2 — RECHECK semantics restated for Gate D

Immediately before `run_job.sh` is invoked, and in the same shell session with no intervening reboot of the replay host, `gate_c_preflight.py --mode RECHECK` runs against the fresh `CHECK_ONLY` record from §3:

| Class | Fields | Rule at this RECHECK |
|---|---|---|
| FROZEN | package/runner/harness digests; interpreter binary digest, Python/NumPy versions and path, BLAS, SIMD, compiler, CPU, kernel, host, boot ID; every requirement and threshold; the job path; the declaration identity | exact equality to the fresh `CHECK_ONLY` record, or refuse — no launch |
| REMEASURED | free space (job, temp), available memory, load, competing processes, job-directory absence | measured again, judged against the same thresholds (≥10 GB, ≥4 GB), recorded whether or not it changed |
| RECONFIRMED | operator declaration | same identity as §3, and independently still valid (inside its window, not withdrawn) at this moment |

A `RECHECK` refusal stops here. Nothing downstream (monitor, launch) starts.

**4.1 Three distinct kinds of continuity (new in Revision 5), kept named separately on purpose.**
This protocol's own step sequence (§7) runs `RECHECK` (step 7) immediately before launch (step 10)
only in the sense that no other step intervenes that is permitted to change host or job state —
not in the sense of one continuously held terminal session; steps 6 through 10 are, and are meant
to be, separate SSH invocations (§7's own text has always structured them that way). That
distinction matters enough to name its three parts explicitly, rather than leave "immediately
before... in the same shell session" to be read informally each time a request is drafted:

- **Host continuity** — the replay host itself has not rebooted between two points in time.
  `RECHECK`'s own FROZEN-field table already establishes this between `CHECK_ONLY` (§3) and
  `RECHECK` (§7 step 7), via the `boot ID` field's exact-equality requirement. It does **not**,
  by itself, cover the gap between `RECHECK` returning `PASS` and launch (§7 step 10) actually
  starting — §7 steps 8 and 9 (monitor staging and start) occur in that gap, each a separate SSH
  invocation. **A request's own command sequence must independently re-confirm host continuity
  immediately before step 10**, by comparing `/proc/sys/kernel/random/boot_id` (read-only,
  already within the access §6.1 item 1's record-reading permission already establishes) against
  the `boot_id` `RECHECK` itself recorded — a mismatch is a stop condition (§8), exactly as a
  `RECHECK` FROZEN-field mismatch already is. This closes a real gap: nothing before Revision 5
  re-verified host continuity past the moment `RECHECK` itself ran.
- **Execution continuity** — the frozen numerical environment (package digest, runner/harness
  digests, interpreter binary digest and version, BLAS/SIMD/compiler, every requirement and
  threshold) and the operator declaration's identity have not changed. This is exactly what
  `RECHECK`'s FROZEN-field table already enforces, *as of the moment `RECHECK` itself runs* — it
  is not independently re-verified again between `RECHECK` and launch by this revision. Host
  continuity (above) and execution continuity are deliberately not conflated: **a `boot_id` match
  proves the host did not reboot; it does not, by itself, prove that every relevant process or
  configuration remained unchanged** (a service could restart, a file could be modified, a
  competing process could start, all without a reboot). The narrow, cheap `boot_id` check this
  revision adds catches the single most disruptive event between `RECHECK` and launch; it is not
  represented as a substitute for `RECHECK`'s own, more complete FROZEN-field verification, which
  remains anchored to `RECHECK`'s own moment and is unchanged by this revision.
- **Session continuity** — whether the commands implementing a Gate D step sequence run within
  one continuously held shell/SSH session or across several separate ones. This protocol permits,
  and its own §7 sequence has always used, separate SSH invocations for steps 6 through 10 — that
  is session continuity's status under this protocol, stated explicitly rather than left to a
  single request document's own interpretation (a request document may not itself decide this
  question; it can only comply with what this protocol already permits). That permission is
  conditioned on host continuity being independently re-established immediately before launch, as
  the new requirement above now requires — separate SSH invocations are permitted *because*, not
  instead of, that evidence being preserved.

## 5. Control 3 — memory and resource observability

A read-only, out-of-process monitor, independent of the harness and of `gate_c_preflight.py`, **started before** `run_job.sh` is launched (§7 step 10 is now preceded by the monitor's own staging, start, and readiness signal at steps 8–9; see §7 and §5.10), bound to the PID captured once `run_job.sh` starts, and stopped only by the exact procedure below.

**5.1 PID and process-group binding.** `run_job.sh` is started so that its own direct child PID is captured synchronously at launch (e.g. `run_job.sh & echo $! > launch.pid`, the file written into the attempt's record directory, not the job directory). The monitor is given the **path** to that file (not a PID) as its argument, because the monitor starts first and the file does not exist yet when it does. Once the file is readable, the monitor resolves, at every sample, the full descendant set rooted at the PID it names (via `/proc/<pid>/task/*/children`, re-walked each sample since the set can change) — it never discovers or matches a process by name, and it never uses `ptrace`.

**5.2 Readiness handshake, verified startup, and startup failure disposition.** The monitor must not require the launched process to exist in order to come up: its own initialization (recording directory and log established) completes and emits an explicit `MONITOR_READY` record **before** it has read the launch-PID file even once. `MONITOR_READY` is the signal that it is now safe to launch exactly one authorized job — step 9 (start the monitor and wait for this record) does not itself proceed past that wait until `MONITOR_READY` is emitted, and it is **§7 step 10, the harness launch, that may proceed only after it** (step 9 was step 8, and the harness launch was step 9, as of Revision 2; both renumbered by one — to 9 and 10 respectively — by Revision 3's inserted monitor-staging step; see the Revision history note and §5.10). Only after `MONITOR_READY` does the monitor begin polling the launch-PID file and, once it is readable, confirming that the named PID's descendant tree contains a process running under the expected interpreter path (cross-checked against the RECHECK record) — writing a `MONITOR_START_CONFIRMED` line carrying the bound PID and the harness's own `launch.pid` value, so the two can be independently compared. Both the file becoming readable and the interpreter match must happen within the startup grace period, or that is `MONITOR_FAILURE: STARTUP`, recorded immediately. A startup failure is never permission to relaunch — it is a documented failure of this attempt, full stop (§5.9). Because an unmonitored run does not satisfy this gate's resource-observability control, the default posture on a startup failure is an immediate, documented **manual** stop of the harness (§5.6) — this is still a human action taken on the operator's or reviewer's decision, informed by the recorded failure, not an automated kill triggered by the monitor itself.

**5.3 What it samples**, at a fixed interval of no less than 5 seconds in any actual Gate D attempt (chosen to avoid perturbing scheduling; it does not govern peak detection, see §5.4) — a production invocation refuses an interval below 5 seconds, and refuses any test-only override of the OOM-log source (§5.5), outright:
- the harness process tree's **current** sampled RSS, summed over the resolved descendant set (`/proc/<pid>/status` `VmRSS`);
- system `MemAvailable` and `SwapFree` from `/proc/meminfo`;
- a best-effort count of OOM-related kernel log lines since the job started (§5.5).

**5.4 Peak memory: three distinct, separately labelled measurements, never collapsed into one.** Periodic sampling of `VmRSS` can miss a short-lived peak between samples, but it is the only measurement that reflects memory actually held *simultaneously* across the process tree at a given instant. The kernel-maintained per-process high-water mark `VmHWM` does not share that property: summing the `VmHWM` of every process the monitor has ever observed in the tree — including ones that have since exited — counts memory that may never have been held at the same moment by those processes together. Conflating the two into a single "authoritative peak" overstates concurrent memory use. The monitor therefore reports three fields, never merged:
- `peak_sampled_tree_rss_bytes` — the running maximum, across all samples, of the live descendant tree's summed `VmRSS`. This is the best available *simultaneous* measurement, limited only by sampling cadence (a spike shorter than the sampling interval can still be missed).
- `sum_observed_process_hwm_bytes` — the sum of every observed PID's own best-seen `VmHWM`, explicitly labelled a **nonconcurrent aggregate**: it exists so that a process that exited between samples is not silently dropped from the record, not to assert that the summed processes coexisted at their individual peaks.
- `peak_job_memory_bytes` — reserved for an independent, whole-job peak measurement (e.g. a cgroup accounting file), if a valid mechanism exists on the replay host. No such mechanism is used by this protocol today; the field is always reported as explicitly absent (not a silent zero) rather than omitted.

**5.5 OOM observability may be unavailable, and may change mid-run; no privilege escalation to obtain it.** `dmesg` and `journalctl -k` can require privileges this SSH login does not have (e.g. `kernel.dmesg_restrict=1`), and a source that answers at startup is not guaranteed to keep answering for the life of the attempt. The monitor checks the source at startup and again at **every** sample — not once — and records any change of state explicitly: a source that stops answering mid-run is logged as a transition to unavailable (never left showing a stale "available" status beside an absent or frozen count), and one that resumes afterward is logged as a transition back to available with a fresh baseline. `OOM_OBSERVABILITY_UNAVAILABLE` is recorded explicitly whenever the source cannot be read, rather than silently reporting a zero count that would be indistinguishable from "checked, none observed." The reported count is of **matching OOM-related log lines**, not of verified unique OOM-kill incidents: one real kill can produce more than one matching line, and the log source itself may not retain the attempt's full history, so the count is never represented as an incident count. The monitor must not use `sudo`, must not request elevated capabilities, and must not change `kernel.dmesg_restrict` or any other kernel security setting to obtain access. `MemAvailable`/`SwapFree` monitoring (world-readable via `/proc/meminfo`) remains mandatory regardless of OOM-log availability.

**5.6 On a low-memory or OOM observation**: the monitor records it and takes no other action — it does not signal, pause or kill the harness. Whether to let the job continue or to stop it is a human decision, made and recorded separately. A manual stop, if taken, is sending `SIGTERM` to the specific harness PID recorded in `launch.pid` — no other process is signalled by this procedure (§5.7 covers stopping the monitor itself).

**5.7 Stopping the monitor.** After the job terminates (sealed, failed, or manually stopped under §5.6), the monitor is stopped by sending `SIGTERM` specifically to the monitor's own PID, which it records in its log at its own startup (distinct from the harness PID in `launch.pid`). This is the only signal this protocol sends to the monitor process, and it is never generalized into signalling other processes.

**5.8 Independently checkable record.** The monitor's log is self-describing: it records `MONITOR_READY`, its own start and stop times, the PID it bound to and the confirmation of §5.2, every sample (cadence timestamp, `peak_sampled_tree_rss_bytes`, `sum_observed_process_hwm_bytes`, `peak_job_memory_bytes`, `MemAvailable`, `SwapFree`, OOM observability status and line count), every OOM-observability transition, and on exit whether it stopped cleanly under §5.7 or crashed. A reviewer can check the bound PID against `launch.pid` and the RECHECK record without re-running anything.

**5.9 Failure and retry discipline.** If the job completes despite a recorded low-memory or OOM observation, it is scored by its ordinary disposition with the observation attached; the comparison in §9 must be reviewed with that observation visible, never silently accepted. A `MONITOR_FAILURE` (startup or later crash) is recorded independently of the harness's own outcome and does not retroactively invalidate a harness result that completed and sealed correctly. No automatic retry of any kind: a failed, interrupted or monitor-failed attempt is preserved as-is, and a further attempt requires a new attempt label, a new Gate C `CHECK_ONLY` under §3, and the same explicit human authorization process as the first — never a silent relaunch.

This monitor is implemented as working code at `tools/r4_package/gate_d_monitor.py`, with its own synthetic tests (missed/absent process, short-lived job, PID reuse, readiness-before-launch binding, monitor startup failure, OOM observability unavailable/mid-run loss/reacquisition, and rejection of invalid or production-unsafe arguments before any record directory is created), reviewed separately as PR #176. Nothing in that implementation, or in this revision, authorizes running it against Dedirock.

**5.10 Monitor staging and on-host verification (new in Revision 3).** `gate_d_monitor.py` resolves the harness's descendant process tree and its memory by opening `/proc/<pid>/stat`, `/proc/<pid>/status`, `/proc/<pid>/task/*/children` and `/proc/meminfo` directly — local filesystem reads with no remote-observation form. **The monitor therefore runs on Dedirock itself**, as a second process over the same authorized SSH login used for every other Gate D step — never from the operator's workstation to observe a process on another host, and this protocol does not describe or permit any alternative deployment of it.

This is a Gate D control, not a Gate C one: it does not add a fifth item to §7 step 4's four frozen Gate C inputs, which stands exactly as merged and is not reopened by this revision. Staging the monitor is its own distinct operation, bound by its own verification and its own hard stop:

1. **Source and identity.** The exact local file `tools/r4_package/gate_d_monitor.py` from the frozen `main` commit this attempt's request names, identified by its own SHA-256 (recorded in the request alongside the other frozen-input digests). No other file implements or substitutes for it.
2. **Destination.** `~/r4_replay_<ATTEMPT>/gate_d_monitor.py`, inside the same new, attempt-scoped remote workspace §7 step 3 creates — not a shared or reused location, and not inside `record_check/`, `record_recheck/`, or the job directory.
3. **Transfer command.** A single, explicit `scp` of that one named file, after `RECHECK` (§7 step 7) has returned `PASS` and before the monitor is started (§7 step 9) — never a glob, never bundled with the four Gate C inputs of step 4, and never transferred before `CHECK_ONLY` has even run once (there is nothing to stage for if `CHECK_ONLY` has not already passed).
4. **On-host verification.** Immediately after transfer, `sha256sum -c` the copied file against the digest recorded in step 1, over the same SSH session. **A mismatch is a hard stop: no replacement, overwrite, or retry of the transfer under the same attempt label.** A new attempt requires a new attempt label and a new §3 Gate C `CHECK_ONLY`, exactly as §5.9 and §8 already require for every other Gate D failure.
5. **No environment mutation.** Staging a single Python script that imports only the standard library (confirmed by reading `gate_d_monitor.py`'s own imports: `argparse`, `json`, `os`, `re`, `signal`, `subprocess`, `sys`, `time`) requires no package installation, no interpreter upgrade or reinstallation, and no virtual-environment change. This step is explicitly prohibited from doing any of those three things — it is a file copy and a checksum check, nothing else — precisely so it cannot silently change the numerical environment that `RECHECK` (§4, §7 step 7) already froze moments earlier. If the monitor ever needs a dependency beyond the standard library, that is a change to the monitor's own implementation, reviewed and merged before the next Gate D attempt, never an on-host `pip install` improvised during one.
6. **Interpreter.** The monitor runs under the exact same absolute interpreter path recorded at `CHECK_ONLY`/`RECHECK` — no second interpreter is introduced, consistent with item 5's no-mutation rule.
7. **Detachment, a precondition on authorization, not just on this section's design.** Starting the monitor (§7 step 9) over the same SSH login that will later close or move on to other steps means the monitor must survive that session's eventual end, remain reachable by its own recorded PID for the exact §5.7 `SIGTERM`, and keep writing its record directory without interruption. This protocol does not assert that any particular shell idiom (e.g. a bare trailing `&`) achieves that — the exact detachment mechanism **must be validated by a local process-lifecycle rehearsal, including SSH-equivalent shell teardown, before it is frozen into any real Gate D request's step 9 text**, let alone authorized. This is an execution detail to be closed before authorization is sought, not a further protocol gap like the one this revision closes; it does not block merging this revision, which only establishes that the monitor runs on Dedirock at all.

## 6. Control 4 — evidence retrieval scope (closes the Gate C attempt-312512 deviation)

A wildcard is acceptable here, in prose, to describe a category of file (as the cross-host protocol §4.1 itself does); it is never acceptable as the literal retrieval command. Retrieval is always by exact filename, built from a **frozen inventory assembled before any file is copied** (§6.2), never from a shell glob (`scp 'results_full/*.json'` is exactly the kind of implicit expansion this rules out) and never driven by discovering what the manifest happens to list beyond the categories named below (the attempt-312512 deviation was exactly manifest-driven expansion).

**6.1 Permitted categories, and nothing else:**
1. The fresh Gate C `CHECK_ONLY` record and the launch-time `RECHECK` record: `PREFLIGHT_RECORD.json`, `PREFLIGHT_RECORD.txt`, `PREFLIGHT_SHA256SUMS` — **not** any package-archive copy the tool's record layout may also contain. The local original archive is already the authority for the package digest, independently verified at transfer time; a second copy returned from the replay host adds no custody value and is explicitly excluded by default.
2. The §5 memory/resource monitor log and `launch.pid`.
3. From the sealed job: its disposition file, the top-level job manifest `JOB_SHA256SUMS`, `STATUS.txt`, `env.txt` — **not** `holdout/` (the fixture corpus is regenerated on the replay host and never transferred in either direction, per the cross-host protocol §3).
4. From `results_full/` and `results_smoke/`: each directory's own `MANIFEST.sha256` (written by the harness itself, listing every durable result file it produced with its digest — class X, per the cross-host protocol §4.2, and not itself compared for equality, only used here to build the inventory), `run_header.json`, `summary.json`, and every other file **that `MANIFEST.sha256` lists** under that results directory (the per-fixture `layerB/`, `phaseA/` and `phaseB/` records the cross-host protocol §4.1 describes by count).
5. The local SSH/SCP session transcripts (stdout/stderr) kept on the workstation during the session — not an artifact pulled from the replay host, but part of the same custody record. **(Clarified in Revision 3.)** This already covers the monitor-staging transfer and its on-host checksum verification (§5.10, §7 step 8): their SSH/SCP transcript is part of the same session record, not a separate retrieval category.
6. **(New in Revision 4.)** The redirected-stdio log files the validated detachment idiom of §5.10 item 7 writes on the replay host: the monitor's own (`monitor_stdio.log`, written by §7 step 9's `setsid ... </dev/null >>LOG 2>&1 &`) and the harness's own (`harness_stdio.log`, written the same way by §7 step 10). Both exist specifically to catch anything written to stdout/stderr before the monitor's own structured JSONL log exists (e.g. an argument-validation failure at startup) or by `run_job.sh` outside its own `STATUS.txt`/`logs/` — diagnostic custody for a failure mode category 2 and 3 above do not otherwise cover. Unlike items 1 and 4, neither file has a pre-known expected digest to verify against (they are freshly generated output, not a frozen input); each is digested and the digest recorded as custody immediately on retrieval, the same treatment items 2, 3 and 5 already receive.

**6.2 Building the frozen inventory.** Before any retrieval command is issued: read (read-only `cat`, not a glob) `JOB_SHA256SUMS`, `results_full/MANIFEST.sha256` and `results_smoke/MANIFEST.sha256` from the replay host. Intersect their listed paths with category 4 above (anything a manifest lists that is outside the named categories — there should be none, since the harness's own output is already exactly what §4.1 describes — is excluded, not fetched, and the discrepancy is recorded). Write the resulting concrete list, each entry with its path and the digest the manifest already records for it, to a local file **before** issuing any `scp`. This is the exact inventory the amendment requires: every source path, its destination, and its expected hash, fixed before transfer, not discovered during it.

**6.3 Retrieval and verification.** Copy exactly the files named in §6.1 items 1, 2, 3, 5, 6 (fixed names, no inventory step needed — there is exactly one of each) and exactly the files named in the §6.2 inventory for item 4. Each copied file is verified against its expected digest (the manifest's, for item 4; the preflight tool's own `PREFLIGHT_SHA256SUMS`, for item 1) immediately after transfer, before anything else reads it; item 6's two files have no pre-known expected digest (per item 6's own note) and are instead digested and recorded as custody immediately on retrieval.

If a future need for the archive copy, or any file outside §6.1, is identified, it is added **by a revision of this document**, reviewed before the next Gate D attempt — never improvised during one.

## 7. Exact command sequence

Steps run in this order; any deviation or unexpected output stops the gate here and is recorded, with no retrospective authorization, exactly as for Gate C.

**Local, before any connection**
1. Confirm the fresh operator declaration (§3) is present, valid now, and names the correct package digest and host.
2. Record the local package archive digest, the `gate_c_preflight.py` tool digest, and this attempt's label.

**On the replay host, over the authorized SSH login**
3. `test ! -e ~/r4_replay_<NEW_ATTEMPT> && mkdir ~/r4_replay_<NEW_ATTEMPT>` — a new directory, distinct from `~/r4_replay_312512`, which is left untouched.
4. Copy in exactly four frozen inputs: the package archive, its `.sha256` sidecar, `gate_c_preflight.py`, and the new operator-declaration file. **Nothing else is transferred at this step.** `run_job.sh` and the harness are not copied separately — no extraction exists yet to copy them from, and creating one here would leave an unaccounted second copy outside the tool's own verification. The only extractions that exist are the ones `gate_c_preflight.py` itself performs, fresh, into its own `--record-dir/extracted_package/`, once at `CHECK_ONLY` (step 6) and once more at `RECHECK` (step 7); each is digest-verified by that same run (its items 1b/1c/1d).
5. `sha256sum -c` the four copied-in files against the local digests.
6. Run `gate_c_preflight.py --mode CHECK_ONLY` with the new declaration (§3 step 2). Must return `PASS`. This produces the CHECK_ONLY record and its own `extracted_package/` (not used for launch; retained as part of the record).
7. Immediately before launch, run `gate_c_preflight.py --mode RECHECK` against that record (§4). Must return `PASS`, or stop. This produces a second, fresh `extracted_package/` under the RECHECK's own `--record-dir`, verified seconds before launch.
8. **(New in Revision 3.)** Stage and verify the Gate D monitor, per §5.10: `scp` the single named `gate_d_monitor.py` file into `~/r4_replay_<ATTEMPT>/`, then `sha256sum -c` it over the same SSH session against the digest recorded in the request. A mismatch is a hard stop — no replacement, overwrite, or retry under this attempt label (§5.10 item 4, §8).
9. Start the memory/resource monitor (§5), **running on Dedirock itself** (§5.10) and pointed at the `launch.pid` path the next step will write (which does not exist yet). Wait for its `MONITOR_READY` record before proceeding — this is the readiness handshake of §5.2, and it is what makes it safe for the monitor to be started before the job it will bind to exists.
10. Start **the unmodified `run_job.sh` from the RECHECK's own `extracted_package/` directory** (step 7's output — the freshest digest-verified copy, not a separately copied one), with `PYTHON` set to the exact absolute interpreter path recorded at `CHECK_ONLY`/`RECHECK`, `PYTHONDONTWRITEBYTECODE=1`, detached, writing to the new job directory, capturing its PID synchronously into `launch.pid` (`run_job.sh & echo $! > launch.pid`). One run only. Then confirm the monitor's `MONITOR_START_CONFIRMED` record (§5.2) before treating the run as monitored; a startup failure at this point is §8's stop condition, never a silent relaunch.
11. Observe progress read-only (e.g., polling `STATUS.txt`); do not touch the job directory.
12. When the job terminates (sealed, failed, or manually stopped per §5.6/§8), stop the monitor by the exact §5.7 procedure.
13. Build the frozen retrieval inventory (§6.2), read-only, from `JOB_SHA256SUMS` and the two results-directory `MANIFEST.sha256` files.
14. Retrieve exactly the files named in §6.1/§6.2, by exact filename, verifying each against its expected digest (§6.3).

**Not permitted** (restates and extends the Gate C list): reading, listing, modifying or deleting any existing remote directory, including `~/r4_replay_312512` and the r2/r3 evidence directories — this prohibition is about filesystem trees and prior evidence, and does not reach the narrowly scoped, read-only `/proc` reads, interpreter checks and kernel-log reads that §5 and the Gate C preflight itself already require and permit; sudo or any privilege change; installing or upgrading software **(including, per §5.10 item 5, while staging the monitor — no `pip install`, no interpreter change, no virtual-environment modification of any kind)**; killing or signalling any process other than (a) a documented manual stop of the harness PID under §5.6, or (b) the §5.7 `SIGTERM` to the monitor's own recorded PID; changing services, firewall, cron, or scheduling; any outbound connection other than the SSH session; any command, flag or pipeline not listed in steps 1–14 (the attempt-312512 `&& hostname` addition is exactly the kind of deviation this rules out going forward); wildcard or recursive copy in either direction (§6); treating the monitor-staging transfer of step 8 as license to transfer any other file outside §7 step 4's four Gate C inputs and the one monitor file named here; re-launching after a failure without a new attempt label and a new §3 Gate C `CHECK_ONLY`.

## 8. Stop conditions

- `CHECK_ONLY` (step 6) or `RECHECK` (step 7) returns anything other than `PASS` → stop; no launch; no retry without a new attempt.
- **(New in Revision 3.)** The monitor-staging checksum (step 8, §5.10 item 4) does not match → hard stop; no launch; no replacement, overwrite, or retry of the transfer under this attempt label; no retry at all without a new attempt label and a new §3 Gate C `CHECK_ONLY`.
- **(New in Revision 5.)** The `boot_id` comparison required immediately before launch (§4.1, host continuity) does not match the `boot_id` `RECHECK` recorded → hard stop; no launch; no retry without a new attempt label and a new §3 Gate C `CHECK_ONLY`, exactly as a `RECHECK` FROZEN-field mismatch already requires.
- The monitor fails to confirm startup (§5.2) → `MONITOR_FAILURE: STARTUP`, recorded; default posture is an immediate documented manual stop of the harness, since an unmonitored run does not satisfy this gate.
- The monitor (§5) observes available memory below the frozen 4 GB floor, an OOM-kill event, or records `OOM_OBSERVABILITY_UNAVAILABLE` → recorded; the harness is **not** automatically touched; a human decides whether to let it continue or to issue a documented manual stop (§5.6).
- **The declared 4-hour window elapses before the job completes.** This is recorded as a `WINDOW_OVERRUN_OBSERVED` event at the moment the window closes (a timestamp comparison, not a judgment call). It does not, by itself, authorize either continuing or stopping — both require a human decision, made and recorded at that point. Continuing requires a **new, separately dated confirmation of continued availability from the same operator identity named in the declaration**, given through the same trusted out-of-band channel as the original declaration (the operator-declaration workflow of `metamorphic_r4_gate_c_preflight.md` §9.2: the operator creates and keeps the artifact; neither the tool nor an assistant creates one on the operator's behalf). This confirmation is its own new, separately timestamped artifact — never an edit to the frozen `CHECK_ONLY`/`RECHECK` records or to the original declaration file, which remain immutable. Absent that confirmation, the default is a documented manual stop once reasonably practicable; the overrun itself still does not trigger an automated kill.
- The SSH session drops, or the host becomes unreachable → documented `REPLAY_INTERRUPTED`; no automatic reconnection or resume (there is no resume capability, per the cross-host protocol §6 "Availability").
- Any command output, file, or state not accounted for in steps 1–14 → stop immediately, document, no retrospective authorization.

## 9. Expected outputs and custody

Execution dispositions (`REPLAY_NOT_EXECUTED` / `REPLAY_INTERRUPTED` / `REPLAY_EXECUTION_FAILED` / `REPLAY_COMPLETED`), custody (`SEALED` / `NOT_SEALED`) and independent verification (`VERIFIED` / `FAILED` / `NOT_EVALUATED`) are exactly as defined in the cross-host protocol §5.1 — unchanged by this document. Once a completed, sealed, verified replay exists, the Gate B′ comparator (`tools/r4_package/compare_replay.py`) runs **locally**, against the retained local reference and the retrieved §6 artifacts, under the field classes and frozen tolerances of the cross-host protocol §4 exactly as merged. No tolerance, class or threshold is adjusted after seeing a result (cross-host protocol §7); a divergence triggers investigation, not relaxation.

The retrieved evidence is verified by digest against the replay host's own checksums before any comparison runs. Results — including the monitor log, both Gate C records for the fresh attempt, and the comparator's output — are proposed for commit as a redaction-aware evidence PR, following the same pattern as PR #169 (home-directory and host-specific paths redacted, with a redaction manifest recording original and committed digests).

## 10. What this document does not do

No connection, transfer, declaration, `CHECK_ONLY`, `RECHECK`, monitor start, launch, or retrieval is authorized by this document. **(Corrected in Revision 3.)** The §5 monitor is already implemented, as working code reviewed and merged separately (`tools/r4_package/gate_d_monitor.py`, PR #176); this protocol document does not itself stage or execute it — doing either remains contingent on the explicit authorization described below. A Gate D authorization, when and if it is given, must independently name: the fresh attempt label, the fresh operator declaration, confirmation that the declared window is current at the moment of connection, and explicit acknowledgment of the §6 retrieval list as written — it does not inherit anything from attempt 312512 beyond what is cited by reference above.

**Authorization by reference (corrected in Revision 3).** A gap identified in the Gate C precedent
(`docs/evidence/r4_gate_c_attempt_GATED2_2026-10-15/governance/GATE_C_BOUNDED_AUTHORIZATION_ADDENDUM_2026-10-15.md`)
is closed here, prospectively, rather than left for a future Gate D request to re-litigate: a Gate D
authorization is valid when it unambiguously references a specific Gate D request document — named
by its attempt label and, once frozen, by that request document's own SHA-256 — that has already
been presented for review. **The owner is not required to reproduce any individual command from
that request verbatim for the reference to be a valid, bounded authorization.** This does not lower
what authorization must still establish: the exact frozen request document, unmodified from the
digest referenced; a fresh, confirmed operator declaration naming a window current at the moment of
connection; and an explicit, separate instruction to execute — never inferred from silence, from
general trust in the operator's standing availability assurance, or from language that merely
authorizes presenting a request for review. Whether authorization-by-reference is sufficient for a given Gate D request is a choice that
request's own text must make explicitly, as this protocol document now requires of itself: a
request that says reference-by-label-and-digest is sufficient may be authorized that way; a
request that is silent on the question, or that states individual commands must be quoted
verbatim, is held to that stricter requirement instead — the default remains verbatim quoting
unless the request affirmatively says otherwise.

## 11. Preserved, not resolved

The local full-run launch-authorization discrepancy (`docs/evidence/r4_full_local_2026-10-13/governance/LAUNCH_AUTHORIZATION_DISCREPANCY.md`) is unrelated to this protocol and is not resolved, narrowed, or implied-resolved by it. A future Gate D authorization, however it is phrased, addresses only the replay described here; it does not retroactively characterize the disputed local launch as authorized or unauthorized. That discrepancy is closed only by the owner's own dated addendum, as already recorded.
