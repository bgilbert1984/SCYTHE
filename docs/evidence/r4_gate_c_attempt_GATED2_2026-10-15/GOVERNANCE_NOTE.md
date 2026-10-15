# Governance note: the technical result vs. its authorization history

**This note exists to keep two separate questions separate, per the owner's explicit instruction:
whether the Gate C `CHECK_ONLY` technically passed, and whether the authorization that preceded it
met this project's own standard for a bounded authorization. The first is settled by
`INDEPENDENT_AUDIT.md`. This note does not settle the second — it records the facts of what was
said and done, and leaves the second question open, exactly as the owner directed.**

## What is not in dispute

- A real SSH connection to the replay host occurred, and the L1/S1–S5 sequence in this directory's
  `COMMAND_TRACE.md` ran exactly as drafted and reviewed in the local preparation materials, with
  no deviation.
- The result was a genuine, independently audited `PREFLIGHT_VERDICT: PASS`.
- No `RECHECK`, monitor start, or job launch occurred. Gate D remains untouched by this attempt.

## The authorization sequence, as it actually occurred in this conversation

In order, and without paraphrasing away what each message actually said:

1. Extensive local-only preparation (attempt-ID generation, operator-declaration templating, a
   four-artifact transfer manifest, and a drafted Gate C command sequence) was reviewed and
   corrected across several rounds — including corrections to the transfer scope, the S5
   retrieval list, and a `run_job.sh` invocation error caught before anything ran.
2. The owner sent a message establishing a standing maintenance/reboot/reclaim assurance ("Fire
   when ready") and confirming operator identity, instructing: "finalize the declaration locally
   using the protocol's required timestamps, freeze its SHA-256, and present the exact bounded
   Gate C command sequence for authorization." That same message also contained a separate,
   appended line: "Any Dedirock connection you feel could help is authorized by this response."
   **This line was explicitly not acted on.** It was declined in the same turn, on the grounds
   that it directly contradicted the sentence immediately preceding it in the same message ("...
   does not waive the frozen Gate C checks, approve unspecified SSH commands, or automatically
   authorize Gate D") and the message's own decision table, which listed Gate D replay as `NOT
   YET AUTHORIZED` and asked only that the command sequence be *presented* for authorization, not
   executed. No connection was made at that point.
3. A real `operator_declaration.json` was drafted and its SHA-256 frozen
   (`9c8f893eb6eb666348963c35b14ba3271d5cd1f45a58d68cac94a49af4e1ad33`), per the trusted-
   declaration workflow (the assistant drafts from facts the operator states; the operator's own
   confirmation is what makes it effective). The assistant's report at that point stated
   explicitly: "I still need your explicit go-ahead to execute that command sequence ... before
   anything connects."
4. The owner replied: "I confirm that 9c8f893e... is the declaration you intend." This was treated
   as confirming the declaration only; the assistant's response again stated that execution
   authorization was separately pending.
5. The owner then sent: "Authorize L1 and S1-S5, execute the Gate C sequence exactly as written."
   **This is the instruction the assistant executed against.** It is short, and it does not itself
   state that the owner had, at that moment, freshly and directly re-read the complete, consolidated
   `GATE_C_REQUEST.PROPOSED_NOT_AUTHORIZED.md` file — as opposed to relying on the assistant's own
   summarized reports of its contents across the preceding turns.
6. The assistant executed L1 and S1–S5 as drafted, with the `PASS` result recorded in this
   directory.

## The owner's own subsequent statement (recorded, not adjudicated)

After being shown the execution trace, the owner stated: "the complete frozen command request was
not available for my previous review, and I did not issue a bounded authorization for this new
Gate C attempt. That governance distinction must be preserved." This is the owner's own account,
recorded here verbatim in substance, not inferred or softened. It asserts that step 5 above, while
a real instruction that was really given and really acted on, did not meet this project's own
established bar for a bounded authorization — a bar this project has elsewhere stated explicitly:
authorization requires "examining the complete manifest and command text," not relying on a
summarized report of it.

## What this note deliberately does not do

- It does not retroactively classify the execution in `COMMAND_TRACE.md` as authorized or
  unauthorized. That disposition is recorded as **`PENDING`** (see the owner's own gate-status
  table, reproduced in `README.md`), not resolved here.
- It does not alter the owner's account of events above, and does not alter the execution evidence
  elsewhere in this directory. Both are preserved as they occurred.
- It does not argue that the appended "any connection you feel could help" line should have been
  acted on, nor that it was the basis for the eventual execution — it was not; the execution
  followed a later, separate, more specific instruction (step 5 above).
- It does not propose a resolution. Per the owner's own instruction, "we can assess whether the
  Gate C authorization discrepancy requires a new preflight attempt or another formal disposition
  before considering Gate D" once this evidence PR has been reviewed — that assessment has not
  happened yet.

## Follow-up assessment (2026-10-15 morning) — added, not a replacement of the above

The owner reviewed this note and sharpened the question it leaves open, without resolving it.
Recorded here in addition to, not instead of, everything above:

**The three questions, kept separate:**

| Question | Assessment |
|---|---|
| Did Gate C `CHECK_ONLY` technically pass? | `PASS`, independently audited as reported (`INDEPENDENT_AUDIT.md`) |
| Was explicit authorization issued before execution? | **Yes** — "Authorize L1 and S1-S5, execute the Gate C sequence exactly as written" is a real, explicit instruction that was acted on. The earlier framing in this note's main text, which left this as an open question, is corrected by this narrower one. |
| Did that authorization satisfy the project's complete-command-review requirement? | **Unresolved** |

**The governing principle the owner stated:** "An owner can authorize a previously prepared
procedure by reference, provided the referenced procedure is uniquely identified, fixed, and
available for review." Under that principle, the relevant question is not whether the
authorization message repeated every command, but whether it unambiguously identified a frozen,
reviewable procedure and whether the required review actually occurred.

**What is now available to assess that**, newly added to this PR: `GATE_C_REQUEST_AS_AUTHORIZED.md`
is the actual frozen command request, unmodified from `2026-10-15T02:04:27Z` onward — its last
edit (correcting the stop-condition language after the declaration's digest was confirmed) came
before the "Authorize L1 and S1-S5" instruction and well before S1 actually ran
(`2026-10-15T02:13:54Z`, per `COMMAND_TRACE.md`). This establishes that a unique, fixed, available
document existed at the moment of authorization. **It does not establish that the owner freshly
re-read that specific document at that moment**, as opposed to authorizing by reference to the
cumulative review already done across the preceding turns — that distinction is exactly what
remains open.

**Revised disposition** (supersedes the informal `PENDING` used earlier in this note; reproduced
in `README.md`):
```
GATE_C_TECHNICAL_RESULT: PASS_VERIFIED
GATE_C_EXPLICIT_AUTHORIZATION: ESTABLISHED
GATE_C_BOUNDED_AUTHORIZATION_COMPLIANCE: PENDING_RECONCILIATION
GATE_D_AUTHORIZATION: NOT_GRANTED
```
The owner was explicit that this is "not retroactive authorization" — it recognizes the earlier
instruction as historical evidence while leaving its procedural sufficiency open — and that
automatically repeating Gate C merely because its authorization was short would not repair the
historical authorization record and is not recommended; the original, genuinely-obtained `PASS`
result is retained, not discarded.
