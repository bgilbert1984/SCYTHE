# Metamorphic harness r4.1 — Gate D: replay execution protocol (design only; no execution authorized)

> **PROTOCOL DRAFT FOR REVIEW — NO CONNECTION, TRANSFER, DECLARATION, PREFLIGHT, LAUNCH, MONITORING OR RETRIEVAL IS AUTHORIZED BY THIS DOCUMENT.** It specifies the exact Gate D request so that it can be reviewed before any of those actions. This document adds itself only; it changes no production code, harness, runner, package, comparator, Gate A–C protocol or evidence, and amends nothing already merged — a **new** document, per the standing rule that frozen protocols are revised by a new revision, not edited in place.

## 1. What this is and is not

This elaborates `metamorphic_r4_cross_host_replay_protocol.md` §6 ("Execution (Gate D)" and "Return path") into an exact, reviewable request: commands, monitoring method, stop conditions, expected outputs and custody. It does not reopen §3's frozen items, §4's field classes or tolerances, or §5's disposition vocabulary — those stand as merged. It does not resolve the local launch-authorization discrepancy (§11).

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

A read-only, out-of-process monitor, independent of the harness and of `gate_c_preflight.py`, started after `RECHECK` passes and before `run_job.sh` is invoked, stopped after the job terminates (success, failure, or manual stop):

- **What it samples**, at a fixed interval of no less than 5 seconds (to avoid perturbing scheduling): the harness process tree's current and peak RSS (via `/proc/<pid>/status` and children, walked by process-group/PID, not `ptrace`); system `MemAvailable` and `SwapFree` from `/proc/meminfo`; a count of kernel OOM-kill events since the job started (via `dmesg` or `journalctl -k`, read-only, filtered for `oom`/`Out of memory`).
- **Where it writes**: a single append-only log in the Gate D attempt's own record directory (not inside the harness job directory), each line timestamped. It is retrieved as its own named artifact (§6).
- **What it must not do**: attach a debugger or tracer to the harness, change its niceness, cgroup or scheduling class, or write anywhere outside its own log file. It observes; it does not participate in the measured execution path.
- **On a low-memory observation** (available memory below the frozen 4 GB floor, or an OOM-kill count greater than zero): the monitor records it and takes no other action. It does not signal, pause or kill the harness. Whether to let the job continue or to stop it is a **human decision**, made and recorded separately (by the reviewer or operator, using a documented manual stop — e.g., `kill` of the recorded harness PID — never an automated threshold response).
- **If the job completes despite a recorded low-memory observation**, the completion is scored by its ordinary disposition, with the observation attached to the record; the comparison in §9 must be reviewed with that observation visible, not silently accepted.
- **If the monitor itself fails or crashes**, that is a distinct `MONITOR_FAILURE`, recorded independently of the harness's own outcome. It does not retroactively invalidate a harness result that completed and sealed correctly, and it does not, by itself, authorize an automatic retry.
- **No automatic retry of any kind.** A failed, interrupted or monitor-failed attempt is preserved as-is. A further attempt requires a new attempt label, a new Gate C `CHECK_ONLY` under §3, and the same explicit human authorization process as the first — never a silent relaunch.

Implementing this monitor as working code (with its own tests, analogous to the Gate B′ comparator or the Gate C preflight tool) is a separate, later, reviewed change. This document specifies its required behavior; it does not ship it.

## 6. Control 4 — evidence retrieval scope (closes the Gate C attempt-312512 deviation)

Retrieval is an explicit, named list of files, copied by exact name. No `scp -r`, no globbing, and no expansion driven by the content of a checksum manifest (the manifest is used only to **verify** the named files already decided on below, never to **decide** what else to fetch — the deviation found at attempt 312512 was exactly manifest-driven expansion).

Permitted return artifacts for a Gate D attempt, and nothing else:
1. The fresh Gate C `CHECK_ONLY` record and the launch-time `RECHECK` record: `PREFLIGHT_RECORD.json`, `PREFLIGHT_RECORD.txt`, `PREFLIGHT_SHA256SUMS` — **not** any package-archive copy the tool's record layout may also contain. The local original archive is already the authority for the package digest, independently verified at transfer time; a second copy returned from the replay host adds no custody value and is explicitly excluded by default.
2. The §5 memory/resource monitor log.
3. From the sealed job: its disposition file, job manifest (`MANIFEST.sha256` / `JOB_SHA256SUMS`), `STATUS.txt`, `env.txt` — **not** `holdout/` (the fixture corpus is regenerated on the replay host and never transferred in either direction, per the cross-host protocol §3).
4. The named result files the comparator operates on: `results_full/*.json`, `results_smoke/*.json`, `run_header.json`, `summary.json` (the exact set in the cross-host protocol §4.1).
5. The local SSH/SCP session transcripts (stdout/stderr) kept on the workstation during the session — not an artifact pulled from the replay host, but part of the same custody record.

If a future need for the archive copy, or any file outside this list, is identified, it is added to this list **by a revision of this document**, reviewed before the next Gate D attempt — never improvised during one.

## 7. Exact command sequence

Steps run in this order; any deviation or unexpected output stops the gate here and is recorded, with no retrospective authorization, exactly as for Gate C.

**Local, before any connection**
1. Confirm the fresh operator declaration (§3) is present, valid now, and names the correct package digest and host.
2. Record the local package archive digest, the `gate_c_preflight.py` tool digest, and this attempt's label.

**On the replay host, over the authorized SSH login**
3. `test ! -e ~/r4_replay_<NEW_ATTEMPT> && mkdir ~/r4_replay_<NEW_ATTEMPT>` — a new directory, distinct from `~/r4_replay_312512`, which is left untouched.
4. Copy in: the package archive, its `.sha256` sidecar, `gate_c_preflight.py`, the new operator-declaration file, and the unmodified `run_job.sh` plus harness from the extracted package (needed to launch; already covered by the package digest check).
5. `sha256sum -c` the copied-in files against the local digests.
6. Run `gate_c_preflight.py --mode CHECK_ONLY` with the new declaration (§3 step 2). Must return `PASS`.
7. Immediately before launch, run `gate_c_preflight.py --mode RECHECK` against that record (§4). Must return `PASS`, or stop.
8. Start the memory/resource monitor (§5) as a separate background process, writing only to its own log in the attempt's record directory.
9. Start the unmodified `run_job.sh`, with `PYTHON` set to the exact absolute interpreter path recorded at `CHECK_ONLY`/`RECHECK`, `PYTHONDONTWRITEBYTECODE=1`, detached, in the new job directory. One run only.
10. Observe progress read-only (e.g., polling `STATUS.txt`); do not touch the job directory.
11. When the job terminates (sealed, failed, or manually stopped per §5/§8), stop the monitor.
12. Retrieve exactly the §6 list, by exact filename, verifying each against the checksums already on the replay host.

**Not permitted** (restates and extends the Gate C list): reading, listing, modifying or deleting any existing remote directory, including `~/r4_replay_312512` and the r2/r3 evidence directories; sudo or any privilege change; installing or upgrading software; killing or signalling any process other than a documented manual stop of the harness under §5; changing services, firewall, cron, or scheduling; any outbound connection other than the SSH session; any command, flag or pipeline not listed in steps 1–12 (the attempt-312512 `&& hostname` addition is exactly the kind of deviation this rules out going forward); wildcard or recursive copy in either direction; re-launching after a failure without a new attempt label and a new §3 Gate C `CHECK_ONLY`.

## 8. Stop conditions

- `CHECK_ONLY` (step 6) or `RECHECK` (step 7) returns anything other than `PASS` → stop; no launch; no retry without a new attempt.
- The monitor (§5) observes available memory below the frozen 4 GB floor, or any OOM-kill event → recorded; the harness is **not** automatically touched; a human decides whether to let it continue or to issue a documented manual stop.
- The declared 4-hour window elapses before the job completes → the attempt is documented as an overrun per the cross-host protocol §6 runtime baseline; continuing past the window requires a **new, contemporaneous** operator confirmation that the host remains available, not an inference from the original declaration.
- The SSH session drops, or the host becomes unreachable → documented `REPLAY_INTERRUPTED`; no automatic reconnection or resume (there is no resume capability, per the cross-host protocol §6 "Availability").
- Any command output, file, or state not accounted for in steps 1–12 → stop immediately, document, no retrospective authorization.

## 9. Expected outputs and custody

Execution dispositions (`REPLAY_NOT_EXECUTED` / `REPLAY_INTERRUPTED` / `REPLAY_EXECUTION_FAILED` / `REPLAY_COMPLETED`), custody (`SEALED` / `NOT_SEALED`) and independent verification (`VERIFIED` / `FAILED` / `NOT_EVALUATED`) are exactly as defined in the cross-host protocol §5.1 — unchanged by this document. Once a completed, sealed, verified replay exists, the Gate B′ comparator (`tools/r4_package/compare_replay.py`) runs **locally**, against the retained local reference and the retrieved §6 artifacts, under the field classes and frozen tolerances of the cross-host protocol §4 exactly as merged. No tolerance, class or threshold is adjusted after seeing a result (cross-host protocol §7); a divergence triggers investigation, not relaxation.

The retrieved evidence is verified by digest against the replay host's own checksums before any comparison runs. Results — including the monitor log, both Gate C records for the fresh attempt, and the comparator's output — are proposed for commit as a redaction-aware evidence PR, following the same pattern as PR #169 (home-directory and host-specific paths redacted, with a redaction manifest recording original and committed digests).

## 10. What this document does not do

No connection, transfer, declaration, `CHECK_ONLY`, `RECHECK`, monitor start, launch, or retrieval is authorized by this document. It does not implement the §5 monitor (a separate, later, reviewed change). A Gate D authorization, when and if it is given, must independently name: the fresh attempt label, the fresh operator declaration, confirmation that the declared window is current at the moment of connection, and explicit acknowledgment of the §6 retrieval list as written — it does not inherit anything from attempt 312512 beyond what is cited by reference above.

## 11. Preserved, not resolved

The local full-run launch-authorization discrepancy (`docs/evidence/r4_full_local_2026-10-13/governance/LAUNCH_AUTHORIZATION_DISCREPANCY.md`) is unrelated to this protocol and is not resolved, narrowed, or implied-resolved by it. A future Gate D authorization, however it is phrased, addresses only the replay described here; it does not retroactively characterize the disputed local launch as authorized or unauthorized. That discrepancy is closed only by the owner's own dated addendum, as already recorded.
