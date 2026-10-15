# Rehearsal record

Run locally (no Dedirock access), 2026-10-15T00:06:28Z–00:06:47Z (a second and third full repeat run,
not retained as committed evidence, reproduced the same outcome for all four scenarios immediately
beforehand — see "Repeatability" below). Monitor commit: `7f00777821f03dc79aa3a58c9fb9ff61dcf21031`
(merge of PR #176; see `FROZEN_EXECUTION_MANIFEST.md`).

| Scenario | Exercises | Result |
|---|---|---|
| `happy_path` | Full handshake: `MONITOR_READY` before the harness exists, binding once it is launched, sampling to completion, clean `SIGTERM` stop | **PASS** |
| `short_lived_process` | The same handshake, but the harness ends quickly while still being monitored: `HARNESS_PROCESS_ENDED` (`pid_reused: false`) observed before the clean stop | **PASS** |
| `startup_failure_pid_never_appears` | `launch.pid` is deliberately never created: `MONITOR_FAILURE: STARTUP`, exit 3, `MONITOR_STOP(clean=False, reason="STARTUP_FAILURE")` | **PASS** |
| `startup_failure_wrong_digest` | A deliberately wrong `--interpreter-sha256`, with a real, long-lived stand-in harness running: `MONITOR_FAILURE: STARTUP`, and the harness independently confirmed still running (never signalled) | **PASS** |

All four scenarios also produced `INDEPENDENT_VERIFICATION.json` with `"all_pass": true` (or, for the
two startup-failure scenarios, the explicit note that absence of binding is itself the verified fact).
Every finding in that file was computed by `rehearsal_driver.py`'s own code — re-reading
`launch.pid`, re-resolving `/proc/<pid>`, and re-computing the interpreter's SHA-256 from scratch —
never by parsing and trusting the monitor's self-authored `MONITOR_LOG.jsonl` claims alone.

## What was independently confirmed, concretely (happy_path)

From `scenarios/happy_path/INDEPENDENT_VERIFICATION.json`:
- `MONITOR_READY` was observed in the log, **and** the named `monitor_pid` was independently confirmed
  to be a live OS process at that moment (re-checked via `/proc`, not asserted by the monitor).
- The monitor's claimed `bound_pid` (156963 in this run) independently matched the contents the driver
  itself read from `launch.pid` — the same cross-check the protocol's §5.2 describes, performed here by
  a process other than the monitor.
- The driver re-read `matched_exe` (`/usr/bin/python3.12`) from disk and recomputed its SHA-256 with its
  own code, and it matched the expected digest — the same fact the monitor itself claims, verified a
  second, independent way.

## Repeatability

Immediately before the committed run, the same four scenarios were run twice more (not retained, to
avoid committing redundant evidence) and produced identical pass/fail outcomes each time. The
handshake is not timing-flaky at this rehearsal's startup-grace budget (10 samples × 0.5s).

## What this does not establish

- It does not establish that the real `run_job.sh` on the real replay host satisfies the same timing —
  see `README.md` "Limits" and the owner's own flagged "narrow binding window" observation.
- It does not constitute a Gate C `CHECK_ONLY`/`RECHECK`, an operator declaration, or any part of a
  Gate D authorization request. Gate D remote execution remains **NOT AUTHORIZED**.
