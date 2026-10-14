# Metamorphic harness r4.1 — Gate D: replay execution protocol (design only; no execution authorized)

> **PROTOCOL DRAFT FOR REVIEW — NO CONNECTION, TRANSFER, DECLARATION, PREFLIGHT, LAUNCH, MONITORING OR RETRIEVAL IS AUTHORIZED BY THIS DOCUMENT.** It specifies the exact Gate D request so that it can be reviewed before any of those actions. This document adds itself only; it changes no production code, harness, runner, package, comparator, Gate A–C protocol or evidence, and amends nothing already merged — a **new** document, per the standing rule that frozen protocols are revised by a new revision, not edited in place.

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

## 5. Control 3 — memory and resource observability

A read-only, out-of-process monitor, independent of the harness and of `gate_c_preflight.py`, started immediately after `run_job.sh` is launched (§7 step 8), bound to the PID captured at that same step, and stopped only by the exact procedure below.

**5.1 PID and process-group binding.** `run_job.sh` is started so that its own direct child PID is captured synchronously at launch (e.g. `run_job.sh & echo $! > launch.pid`, the file written into the attempt's record directory, not the job directory). The monitor is given that PID as its sole argument and resolves, at every sample, the full descendant set rooted at it (via `/proc/<pid>/task/*/children`, re-walked each sample since the set can change) — it never discovers or matches a process by name, and it never uses `ptrace`.

**5.2 Verified startup; startup failure disposition.** The monitor's first action is to confirm that the given PID exists and is running under the expected interpreter path (cross-checked against the RECHECK record), and to write a `MONITOR_START_CONFIRMED` line carrying the bound PID and the harness's own `launch.pid` value, so the two can be independently compared. If this confirmation does not succeed within the first two sampling intervals, that is `MONITOR_FAILURE: STARTUP`, recorded immediately. Because an unmonitored run does not satisfy this gate's resource-observability control, the default posture on a startup failure is an immediate, documented **manual** stop of the harness (§5.6) — this is still a human action taken on the operator's or reviewer's decision, informed by the recorded failure, not an automated kill triggered by the monitor itself.

**5.3 What it samples**, at a fixed interval of no less than 5 seconds (chosen to avoid perturbing scheduling; it does not govern peak detection, see §5.4):
- the harness process tree's **current** sampled RSS, summed over the resolved descendant set (`/proc/<pid>/status` `VmRSS`);
- system `MemAvailable` and `SwapFree` from `/proc/meminfo`;
- a best-effort count of kernel OOM-kill events since the job started (§5.5).

**5.4 Peak RSS: kernel high-water mark, not sampling.** Periodic sampling can miss a short-lived peak between samples. The monitor instead reads the kernel-maintained high-water mark `VmHWM` from `/proc/<pid>/status` for the harness's own PID (and, for any child still present, its own `VmHWM`) at every sample and at stop, and reports that value — monotonic and not dependent on sampling cadence — as the authoritative peak. The maximum of the sampled `VmRSS` values is also reported, separately labelled, as a secondary, cadence-limited figure; the two are never conflated into one number. A child process that has already exited before a sample cannot contribute further to the summed `VmHWM`, so the monitor also keeps a running sum of the last known `VmHWM` of every PID it has ever seen in the tree, and reports that running sum alongside the live tree's total — the more conservative (larger) of the two is the one treated as authoritative.

**5.5 OOM observability may be unavailable; no privilege escalation to obtain it.** `dmesg` and `journalctl -k` can require privileges this SSH login does not have (e.g. `kernel.dmesg_restrict=1`). The monitor attempts a read-only check once at startup; if it is denied, the monitor records `OOM_OBSERVABILITY_UNAVAILABLE` explicitly in its log and in its final record, rather than silently reporting a zero count that would be indistinguishable from "checked, none observed." It must not use `sudo`, must not request elevated capabilities, and must not change `kernel.dmesg_restrict` or any other kernel security setting to obtain access. `MemAvailable`/`SwapFree` monitoring (world-readable via `/proc/meminfo`) remains mandatory regardless of OOM-log availability.

**5.6 On a low-memory or OOM observation**: the monitor records it and takes no other action — it does not signal, pause or kill the harness. Whether to let the job continue or to stop it is a human decision, made and recorded separately. A manual stop, if taken, is sending `SIGTERM` to the specific harness PID recorded in `launch.pid` — no other process is signalled by this procedure (§5.7 covers stopping the monitor itself).

**5.7 Stopping the monitor.** After the job terminates (sealed, failed, or manually stopped under §5.6), the monitor is stopped by sending `SIGTERM` specifically to the monitor's own PID, which it records in its log at its own startup (distinct from the harness PID in `launch.pid`). This is the only signal this protocol sends to the monitor process, and it is never generalized into signalling other processes.

**5.8 Independently checkable record.** The monitor's log is self-describing: it records its own start and stop times, the PID it bound to and the confirmation of §5.2, every sample (cadence timestamp, summed `VmRSS`, running `VmHWM`, `MemAvailable`, `SwapFree`, OOM status), and on exit whether it stopped cleanly under §5.7 or crashed. A reviewer can check the bound PID against `launch.pid` and the RECHECK record without re-running anything.

**5.9 Failure and retry discipline.** If the job completes despite a recorded low-memory or OOM observation, it is scored by its ordinary disposition with the observation attached; the comparison in §9 must be reviewed with that observation visible, never silently accepted. A `MONITOR_FAILURE` (startup or later crash) is recorded independently of the harness's own outcome and does not retroactively invalidate a harness result that completed and sealed correctly. No automatic retry of any kind: a failed, interrupted or monitor-failed attempt is preserved as-is, and a further attempt requires a new attempt label, a new Gate C `CHECK_ONLY` under §3, and the same explicit human authorization process as the first — never a silent relaunch.

Implementing this monitor as working code (with its own tests — missed/absent process, short-lived job, PID reuse, monitor startup failure, OOM observability unavailable) is a separate, later, reviewed change. This document specifies its required behavior; it does not ship it.

## 6. Control 4 — evidence retrieval scope (closes the Gate C attempt-312512 deviation)

A wildcard is acceptable here, in prose, to describe a category of file (as the cross-host protocol §4.1 itself does); it is never acceptable as the literal retrieval command. Retrieval is always by exact filename, built from a **frozen inventory assembled before any file is copied** (§6.2), never from a shell glob (`scp 'results_full/*.json'` is exactly the kind of implicit expansion this rules out) and never driven by discovering what the manifest happens to list beyond the categories named below (the attempt-312512 deviation was exactly manifest-driven expansion).

**6.1 Permitted categories, and nothing else:**
1. The fresh Gate C `CHECK_ONLY` record and the launch-time `RECHECK` record: `PREFLIGHT_RECORD.json`, `PREFLIGHT_RECORD.txt`, `PREFLIGHT_SHA256SUMS` — **not** any package-archive copy the tool's record layout may also contain. The local original archive is already the authority for the package digest, independently verified at transfer time; a second copy returned from the replay host adds no custody value and is explicitly excluded by default.
2. The §5 memory/resource monitor log and `launch.pid`.
3. From the sealed job: its disposition file, the top-level job manifest `JOB_SHA256SUMS`, `STATUS.txt`, `env.txt` — **not** `holdout/` (the fixture corpus is regenerated on the replay host and never transferred in either direction, per the cross-host protocol §3).
4. From `results_full/` and `results_smoke/`: each directory's own `MANIFEST.sha256` (written by the harness itself, listing every durable result file it produced with its digest — class X, per the cross-host protocol §4.2, and not itself compared for equality, only used here to build the inventory), `run_header.json`, `summary.json`, and every other file **that `MANIFEST.sha256` lists** under that results directory (the per-fixture `layerB/`, `phaseA/` and `phaseB/` records the cross-host protocol §4.1 describes by count).
5. The local SSH/SCP session transcripts (stdout/stderr) kept on the workstation during the session — not an artifact pulled from the replay host, but part of the same custody record.

**6.2 Building the frozen inventory.** Before any retrieval command is issued: read (read-only `cat`, not a glob) `JOB_SHA256SUMS`, `results_full/MANIFEST.sha256` and `results_smoke/MANIFEST.sha256` from the replay host. Intersect their listed paths with category 4 above (anything a manifest lists that is outside the named categories — there should be none, since the harness's own output is already exactly what §4.1 describes — is excluded, not fetched, and the discrepancy is recorded). Write the resulting concrete list, each entry with its path and the digest the manifest already records for it, to a local file **before** issuing any `scp`. This is the exact inventory the amendment requires: every source path, its destination, and its expected hash, fixed before transfer, not discovered during it.

**6.3 Retrieval and verification.** Copy exactly the files named in §6.1 items 1, 2, 3, 5 (fixed names, no inventory step needed — there is exactly one of each) and exactly the files named in the §6.2 inventory for item 4. Each copied file is verified against its expected digest (the manifest's, for item 4; the preflight tool's own `PREFLIGHT_SHA256SUMS`, for item 1) immediately after transfer, before anything else reads it.

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
8. Start **the unmodified `run_job.sh` from the RECHECK's own `extracted_package/` directory** (step 7's output — the freshest digest-verified copy, not a separately copied one), with `PYTHON` set to the exact absolute interpreter path recorded at `CHECK_ONLY`/`RECHECK`, `PYTHONDONTWRITEBYTECODE=1`, detached, writing to the new job directory, capturing its PID synchronously into `launch.pid` (`run_job.sh & echo $! > launch.pid`). One run only.
9. Immediately start the memory/resource monitor (§5) bound to the PID in `launch.pid`, confirming startup (§5.2) before treating the run as monitored.
10. Observe progress read-only (e.g., polling `STATUS.txt`); do not touch the job directory.
11. When the job terminates (sealed, failed, or manually stopped per §5.6/§8), stop the monitor by the exact §5.7 procedure.
12. Build the frozen retrieval inventory (§6.2), read-only, from `JOB_SHA256SUMS` and the two results-directory `MANIFEST.sha256` files.
13. Retrieve exactly the files named in §6.1/§6.2, by exact filename, verifying each against its expected digest (§6.3).

**Not permitted** (restates and extends the Gate C list): reading, listing, modifying or deleting any existing remote directory, including `~/r4_replay_312512` and the r2/r3 evidence directories — this prohibition is about filesystem trees and prior evidence, and does not reach the narrowly scoped, read-only `/proc` reads, interpreter checks and kernel-log reads that §5 and the Gate C preflight itself already require and permit; sudo or any privilege change; installing or upgrading software; killing or signalling any process other than (a) a documented manual stop of the harness PID under §5.6, or (b) the §5.7 `SIGTERM` to the monitor's own recorded PID; changing services, firewall, cron, or scheduling; any outbound connection other than the SSH session; any command, flag or pipeline not listed in steps 1–13 (the attempt-312512 `&& hostname` addition is exactly the kind of deviation this rules out going forward); wildcard or recursive copy in either direction (§6); re-launching after a failure without a new attempt label and a new §3 Gate C `CHECK_ONLY`.

## 8. Stop conditions

- `CHECK_ONLY` (step 6) or `RECHECK` (step 7) returns anything other than `PASS` → stop; no launch; no retry without a new attempt.
- The monitor fails to confirm startup (§5.2) → `MONITOR_FAILURE: STARTUP`, recorded; default posture is an immediate documented manual stop of the harness, since an unmonitored run does not satisfy this gate.
- The monitor (§5) observes available memory below the frozen 4 GB floor, an OOM-kill event, or records `OOM_OBSERVABILITY_UNAVAILABLE` → recorded; the harness is **not** automatically touched; a human decides whether to let it continue or to issue a documented manual stop (§5.6).
- **The declared 4-hour window elapses before the job completes.** This is recorded as a `WINDOW_OVERRUN_OBSERVED` event at the moment the window closes (a timestamp comparison, not a judgment call). It does not, by itself, authorize either continuing or stopping — both require a human decision, made and recorded at that point. Continuing requires a **new, separately dated confirmation of continued availability from the same operator identity named in the declaration**, given through the same trusted out-of-band channel as the original declaration (the operator-declaration workflow of `metamorphic_r4_gate_c_preflight.md` §9.2: the operator creates and keeps the artifact; neither the tool nor an assistant creates one on the operator's behalf). This confirmation is its own new, separately timestamped artifact — never an edit to the frozen `CHECK_ONLY`/`RECHECK` records or to the original declaration file, which remain immutable. Absent that confirmation, the default is a documented manual stop once reasonably practicable; the overrun itself still does not trigger an automated kill.
- The SSH session drops, or the host becomes unreachable → documented `REPLAY_INTERRUPTED`; no automatic reconnection or resume (there is no resume capability, per the cross-host protocol §6 "Availability").
- Any command output, file, or state not accounted for in steps 1–13 → stop immediately, document, no retrospective authorization.

## 9. Expected outputs and custody

Execution dispositions (`REPLAY_NOT_EXECUTED` / `REPLAY_INTERRUPTED` / `REPLAY_EXECUTION_FAILED` / `REPLAY_COMPLETED`), custody (`SEALED` / `NOT_SEALED`) and independent verification (`VERIFIED` / `FAILED` / `NOT_EVALUATED`) are exactly as defined in the cross-host protocol §5.1 — unchanged by this document. Once a completed, sealed, verified replay exists, the Gate B′ comparator (`tools/r4_package/compare_replay.py`) runs **locally**, against the retained local reference and the retrieved §6 artifacts, under the field classes and frozen tolerances of the cross-host protocol §4 exactly as merged. No tolerance, class or threshold is adjusted after seeing a result (cross-host protocol §7); a divergence triggers investigation, not relaxation.

The retrieved evidence is verified by digest against the replay host's own checksums before any comparison runs. Results — including the monitor log, both Gate C records for the fresh attempt, and the comparator's output — are proposed for commit as a redaction-aware evidence PR, following the same pattern as PR #169 (home-directory and host-specific paths redacted, with a redaction manifest recording original and committed digests).

## 10. What this document does not do

No connection, transfer, declaration, `CHECK_ONLY`, `RECHECK`, monitor start, launch, or retrieval is authorized by this document. It does not implement the §5 monitor (a separate, later, reviewed change). A Gate D authorization, when and if it is given, must independently name: the fresh attempt label, the fresh operator declaration, confirmation that the declared window is current at the moment of connection, and explicit acknowledgment of the §6 retrieval list as written — it does not inherit anything from attempt 312512 beyond what is cited by reference above.

## 11. Preserved, not resolved

The local full-run launch-authorization discrepancy (`docs/evidence/r4_full_local_2026-10-13/governance/LAUNCH_AUTHORIZATION_DISCREPANCY.md`) is unrelated to this protocol and is not resolved, narrowed, or implied-resolved by it. A future Gate D authorization, however it is phrased, addresses only the replay described here; it does not retroactively characterize the disputed local launch as authorized or unauthorized. That discrepancy is closed only by the owner's own dated addendum, as already recorded.
