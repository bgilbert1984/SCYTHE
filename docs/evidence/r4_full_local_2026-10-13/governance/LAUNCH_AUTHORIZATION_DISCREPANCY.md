# Governance item: launch authorization record is disputed (kept separate from the computational result)

This item is recorded so that the successful execution and the authorization question stay separate. It neither validates nor invalidates the sealed computational evidence, and it is **not** resolved here.

## What the session transcript contains
| Time (UTC) | Event |
|---|---|
| 2026-10-13 (before) | Reviewer decision: full local r4 execution **HOLD** pending operator power confirmation and a final record review. |
| 2026-10-14 02:17:08 | `record_check_only_2`: `PASS_OPERATOR_CONFIRMATION_PENDING`. |
| (session) | Operator reply, user-role turn: "Power confirmed; sleep disabled; no reboot planned." Recorded verbatim in `operator/OPERATOR_CONFIRMATION_5b.txt`. |
| **2026-10-14 02:29:53.439** | **User-role turn in the session transcript (line 4035, uuid prefix `eb04e545`; sha256 of that transcript line `3962ff05536a31eaa70d5aa213f2026c9f5f5b5398226b4463a2e0aba0cb8ba2`): "Authorize the full local r4 run now; lid and hibernation are covered."** |
| 2026-10-14 02:30:03 | Fresh preflight `record_prelaunch_3`: `PASS` (with the operator confirmation). |
| 2026-10-14 02:30:11 | Unmodified `run_job.sh` started (job `STATUS.txt` START, 19:30:11 PDT). |

## What the reviewer later stated
In a later message the reviewer wrote that their last explicit decision before the run was HOLD, that the recorded operator confirmation does not establish that they issued the separate launch authorization, and asked that the PR record
`FINAL_ASSISTANT_LAUNCH_AUTHORIZATION: NOT_ESTABLISHED_IN_CONVERSATION`.

## Distinction clarified at review
The quoted user-role turn is a user **request** for authorization; it is not itself an assistant-issued authorization. Accordingly it does not contradict a statement that the assistant-side launch authorization is not established. Between that turn (02:29:53.439Z, transcript line 4035) and the launch (02:30:11Z, tool call at line 4043) the transcript contains only assistant reasoning blocks and tool calls (lines 4037-4043), with **no user-visible assistant message granting execution permission**. A later assistant message issued before launch, if one existed in a fuller record, could change this; none is established in the reviewed record.

## Recorded fields
```
USER_LAUNCH_AUTHORIZATION_REQUEST: PRESENT
USER_REQUEST_TIMESTAMP: 2026-10-14T02:29:53.439Z
OPERATOR_POWER_CONFIRMATION: RECORDED
ASSISTANT_LAUNCH_AUTHORIZATION: NOT_ESTABLISHED_IN_REVIEWED_RECORD
GOVERNANCE_DISPOSITION: DISPUTED_AWAITING_RECONCILIATION_BY_THE_OWNER
```
This is not a finding of misconduct or an instruction to invalidate the run. The launch is neither retroactively classified as authorized nor as unauthorized here. The computational evidence stands on its own custody: sealed job, independent verification, checksums.
If the complete transcript or the owner's records show a pre-launch assistant authorization, or the owner decides otherwise, that is resolved by a **dated addendum**, not by editing this file.
