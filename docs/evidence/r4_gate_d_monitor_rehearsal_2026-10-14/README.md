# Gate D monitor/launcher integration rehearsal — 2026-10-14

**This directory is a local rehearsal of the monitor/launcher handshake. It is not Gate D execution,
not a Gate C attempt, and not a connection of any kind to Dedirock.** No SSH access, remote host, new
operator declaration, or Gate C `CHECK_ONLY` is used or touched here. It authorizes nothing in
`docs/research/metamorphic_r4_gate_d_protocol.md` §10's list.

## What this is

Everything that can be rehearsed locally about the merged Gate D monitor
(`tools/r4_package/gate_d_monitor.py`, Revision 2, PR #175/#176) without the real r4.1 harness or a
remote host: the exact operational sequence of protocol §7 steps 8–9 — monitor started before the
harness, `MONITOR_READY` awaited, the harness launched with the `cmd & echo $! > launch.pid` idiom,
binding confirmed, the run observed to completion, the monitor stopped cleanly by `SIGTERM` to its own
PID — run end-to-end by `rehearsal_driver.py` against `standin_launcher.sh` (a stand-in for
`tools/r4_job/run_job.sh`'s PID-capture idiom and process-tree shape; it runs no production code and
is not the r4.1 harness).

It also exercises the specific safeguard raised during the PR #176 merge review: *"make monitor
readiness and successful process binding independently observable — a `MONITOR_READY` event alone
does not prove the harness has been attached to monitoring."* `rehearsal_driver.py`'s
`independent_verify_binding()` never imports or trusts `gate_d_monitor.py`'s own code; it re-derives,
from raw `/proc` state and its own separately written SHA-256 computation, whether the monitor's
self-reported binding is actually correct, and writes its findings to `INDEPENDENT_VERIFICATION.json`
— a file the monitor process never writes to and never reads.

## Scope: what was authorized, and what was not

Per the owner's decision record (`GATE_D_PROTOCOL_DESIGN_ACCEPTED_WITH_REQUIRED_AMENDMENTS`,
`PR_176_APPROVED_FOR_MERGE`, and the follow-on readiness assessment dated 2026-10-14): "Proceed with
the local integration rehearsal and frozen execution-manifest preparation now. No SSH access or remote
operations are authorized by that recommendation." This directory is exactly that — items 1 and 2 of
the readiness assessment's "what remains before launch" list. Items 3–5 (a fresh operator declaration,
a fresh Gate C `CHECK_ONLY`, and a Gate D authorization request) are explicitly **not** attempted here;
their status remains `AWAITING AUTHORIZATION` / `NOT AUTHORIZED` as recorded by the owner.

## Contents

- `standin_launcher.sh` — the local stand-in launcher (not `run_job.sh`; see its own header comment).
- `rehearsal_driver.py` — the rehearsal orchestrator and independent verifier (see its own module docstring).
- `scenarios/<name>/` — captured evidence for each of the four rehearsed scenarios (see `REHEARSAL_RECORD.md`):
  `DRIVER_LOG.txt` (the driver's own timestamped narration), `monitor/MONITOR_LOG.jsonl` (the monitor's
  unmodified, append-only log), `job/STATUS.txt` and `launch.pid` (where the scenario launched a
  harness), `INDEPENDENT_VERIFICATION.json` (the independent cross-check), `OUTCOME.txt` (pass/fail).
- `REHEARSAL_RECORD.md` — the rehearsal's results and interpretation.
- `FROZEN_EXECUTION_MANIFEST.md` — digests of the merged artifacts this rehearsal exercised, prepared
  per the readiness assessment's item 2; it is a record of what currently exists on `main`, not an
  authorization, a declaration, or a Gate C record.
- `REDACTION_MANIFEST.txt` — the one redaction applied (a local worktree path with no evidentiary value).

## Limits (carried forward from the owner's own "nonblocking observations")

- The startup-grace window used in this rehearsal (10 samples × 0.5s = 5s) is generous relative to
  production defaults specifically so the handshake's *correctness* could be rehearsed without racing
  the clock; it says nothing about whether the actual Gate D launch procedure's grace budget is
  sufficient on the real replay host under real load. That remains to be established before
  authorizing a real attempt, per the owner's own flagged limitation.
- The stand-in launcher's process-tree shape (one bash parent, sequential short-lived Python children)
  approximates `run_job.sh`'s shape but is not identical to it; this rehearsal demonstrates the
  handshake mechanism, not a guarantee that `run_job.sh`'s exact tree is covered in every detail.
- `peak_sampled_tree_rss_bytes` / `sum_observed_process_hwm_bytes` observed here are rehearsal-scale
  (megabytes, Python interpreter startup overhead) and have no bearing on the real r4.1 job's memory
  profile; they only demonstrate that the three peak-memory fields are populated, distinct, and
  non-conflated, per Revision 2 §5.4.
