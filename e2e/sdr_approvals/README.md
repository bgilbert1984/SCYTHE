# SDR approvals: two-operator browser walkthrough

An end-to-end check of the radio approval flow (ADR 0005 Decisions 4 and 5, ADR 0006 Decisions 6 and 7) as an operator
experiences it: two signed-in operators in headless Chromium, the real Flask app and MCP lifecycle behind them, and a
fake radio.

```
python e2e/sdr_approvals/run.py            # screenshots go to e2e/sdr_approvals/out/ (git-ignored)
```

Expected result: **11/11 passed**, exit status 0. That is the reference lifecycle behaviour.

## What it verifies

| # | Guarantee |
|---|-----------|
| 1 | With a second principal required, Scan Spectrum is held: no stream is opened, the bridge is not started, the audit shows only the pending proposal. |
| 2 | The Approvals panel is fully visible and its Approve and Deny buttons are reachable by a **real pointer click** (a scripted click would hide an obstructed button). |
| 3 | The approver sees the proposer's command with Approve/Deny; the proposer sees it as their own, with no buttons. |
| 4 | Approving runs the command; the audit chain is created, approved, executed, with proposer, approver and executor recorded as separate principals. |
| 5 | A held Tune is reported as waiting, not as success; denying it executes nothing and the audit ends at denied. |
| 6 | An approved Tune reaches the bridge with the requested frequency. |
| 7 | Approval is bound to the **operator**, not the browser session: the same operator's second session cannot approve (403). |
| 8 | Disconnect (a halting action) is exempt from the second-principal requirement and leaves nothing pending. |
| 9 | With the lifecycle unavailable a control request is a 503 and the UI says so. |
| 10 | A Tune with no frequency is a 400 and the UI shows a plain message, with no Python exception text. |
| 11 | Malformed stream settings are a 400 and never become a proposal: validation precedes approval. |

Reference screenshots (pending, approved, denied, lifecycle unavailable, validation error) are in `reference/`.
They are evidence for reviews and release notes, not an assertion: the run does not compare pixels.

## What it does not verify

- The login page. Operator sessions are seeded (`sessionStorage`) and the session check is answered by the test.
- A real radio, SDR++, or the real `rf_bridge` (including its own validation). The bridge is a fake that records calls.
- The default deployment (no approval modes). That is covered by `test_sdr_route_lifecycle.py` and the MCP tests.
- Expiry of pending proposals, the 20-second badge refresh, roles (ADR 0006 Decision 8), autonomous or agent proposals.
- The 3D globe. Cesium is not usable in headless Chromium; the harness dismisses its render-error dialog.
- Pixel layout beyond the reachability checks in case 2.

## Execution assumptions

- `HARDWARE_CONTROL=SECOND_PRINCIPAL` (the recommended setting for deployments that control hardware). The server sets it.
- Three operator sessions: ALPHA (`tok-alpha`), ALPHA's second session (`tok-alpha2`), BRAVO (`tok-bravo`).
- Phase 0 (observe-only): operator commands are exempt from the phase; that is part of what is being checked.
- Port 5099 on loopback; the harness server runs from the repository root and writes a temporary control-audit database.
- The page loads some scripts from public CDNs (Cesium, MapLibre); the run needs outbound network access.
- Test-only endpoints exist in the harness server process only: `/__bridge_calls`, `/__audit`, `/__lifecycle/<on|off>`.

## Dependencies

- **Server interpreter**: the project's own (`.venv/bin/python` if present, else `--server-python`). It must have the
  repository's requirements, including `jsonschema`. Without it the registry skips parameter validation and cases 10
  and 11 would be meaningless, so the server refuses to start.
- **Driver interpreter**: any Python 3 with Playwright: `pip install -r e2e/sdr_approvals/requirements.txt` then
  `playwright install chromium`. This can be a separate virtualenv; it does not need the project's requirements.

## Reading a failure

`run.py` exits **0** when every case passes, **1** when a case fails, **2** when it could not run.

| Category | Exit | Typical signs | First thing to check |
|----------|------|---------------|----------------------|
| Infrastructure failure | 2 | `INFRASTRUCTURE:` line: Playwright or Chromium missing, server exited or did not answer, page never became ready, `jsonschema` missing | `out/server.log`; the dependency list above |
| Functional regression | 1 | A case prints `FAIL` with its detail; the lifecycle no longer reaches the expected state | `git log` for the lifecycle, route and UI files; the detail line names the audit rows and bridge calls it saw |
| Intentional policy change | 1 | The expected results changed because lifecycle policy changed (for example a different approval default, or exemption rules) | The ADR and PR that changed the policy; update the case and this table in the same PR |

A case that cannot complete (for example a button no click can reach) is reported as `FAIL ... did not complete`, not as
infrastructure: that is how the Approvals panel placement defect showed up. After a failure, later cases may fail
too, because the cases share state; fix the first failure first.

`10/11 passed` therefore means: the harness ran, and one lifecycle guarantee no longer holds or was deliberately changed.
It does not mean the harness is broken (that is exit 2).

## Updating

Change a case only together with the behaviour it pins, and say so in the PR. Regenerate `reference/` by copying the
relevant files from `out/` after a green run. Do not state tool counts or other inventory numbers in this document;
they are measured at a commit, not invariants.
