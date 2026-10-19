# Dated governance addendum: resolving `GATE_C_BOUNDED_AUTHORIZATION_COMPLIANCE`

**Date:** 2026-10-15. **Supersedes** the `PENDING_RECONCILIATION` disposition carried in
`../README.md` and `../GOVERNANCE_NOTE.md` for the single field named above. Per this project's own
convention (`docs/evidence/r4_full_local_2026-10-13/governance/LAUNCH_AUTHORIZATION_DISCREPANCY.md`,
closing line), a disputed governance question is resolved by a **dated addendum**, not by editing
the original record. `GOVERNANCE_NOTE.md` is therefore left exactly as it stands; this file is the
resolution, linked from it.

## The question being resolved

Whether the instruction actually given before Gate C `CHECK_ONLY` execution — "Authorize L1 and
S1-S5, execute the Gate C sequence exactly as written" — satisfied this project's own standard for
a bounded authorization, given that the frozen `GATE_C_REQUEST_AS_AUTHORIZED.md` itself states (its
"Revision note (second pass)" and its S1 command block) that the S1 command "is what must be
quoted verbatim in any real authorization message... never approved 'by reference to this draft'
alone" — a condition the authorization actually given did not meet.

## The two facts, kept distinct (the owner's own framing, adopted as the resolution)

1. **`GATE_C_EXPLICIT_AUTHORIZATION: ESTABLISHED`.** A real, explicit, pre-execution instruction to
   execute was given, identifying a unique, fixed, available procedure (`GATE_C_REQUEST_AS_AUTHORIZED.md`,
   last edited 2026-10-15T02:04:27Z, ~9.5 minutes before S1 ran). This is settled fact, not
   reopened by this addendum.
2. **A narrow, real procedural inconsistency exists and is not erased.** The frozen request's own
   text required the S1 command to be quoted verbatim in the authorization itself. The authorization
   given incorporated L1/S1–S5 by reference to the already-reviewed draft, rather than quoting S1's
   text. This is a genuine deviation from a condition the governing document itself states, not
   merely a question of whether the owner had reread the file at that moment.

These two facts do not cancel each other, and this addendum does not collapse them into a single
pass/fail verdict beyond what is recorded below. The technical result they both bear on —
`PREFLIGHT_VERDICT: PASS`, independently audited in `../INDEPENDENT_AUDIT.md` — is unaffected by
either fact and is not reopened here.

## Disposition

```
GATE_C_TECHNICAL_RESULT: PASS_VERIFIED                              (unchanged — scoped per
                                                                       GOVERNANCE_NOTE.md
                                                                       qualification 2)
GATE_C_EXPLICIT_AUTHORIZATION: ESTABLISHED                           (unchanged)
GATE_C_BOUNDED_AUTHORIZATION_COMPLIANCE:
    AUTHORIZATION_SUBSTANCE_SATISFIED_WITH_ACKNOWLEDGED_FORMAL_DEVIATION
GATE_C_BOUNDED_AUTHORIZATION_DEVIATION: S1_NOT_QUOTED_VERBATIM_AS_REQUIRED_BY_FROZEN_REQUEST
GATE_C_RECORD_STATUS: CLOSED — not reopened by a repeat Gate C run
GATE_D_AUTHORIZATION: NOT_GRANTED                                    (unchanged; this addendum
                                                                       grants nothing toward
                                                                       Gate D)
```

**Reasoning for this disposition, kept narrow and not generalized beyond this one attempt:**

- The authorization identified a unique, fixed, reviewable procedure and was given by the owner
  with full opportunity for review — the substance the bounded-authorization standard protects
  against (an assistant acting on a vague, unreviewed, or self-authorized instruction) did not
  occur.
- The specific formal requirement the frozen request imposed on itself — verbatim quoting of S1 —
  was not met. That is recorded as a real deviation, not retroactively waived and not hidden. It is
  the reason `GATE_C_BOUNDED_AUTHORIZATION_COMPLIANCE` is not simply marked `SATISFIED`.
- Per the owner's own instruction, this deviation does not invalidate the technical `PASS`, and no
  repeat Gate C run is ordered to "fix" it — repeating a technically successful, genuinely
  authorized-in-substance attempt would not repair a formal-authorization-text gap and would waste
  a real, audited result.
- This disposition is therefore a closed record of a one-time, acknowledged formal gap in an
  otherwise substantively authorized attempt — not a precedent that future authorizations may
  incorporate frozen command text "by reference" when the frozen text itself demands verbatim
  quoting.

## Forward-looking correction (binding on future Gate C/Gate D requests under this protocol)

To prevent this exact ambiguity from recurring: any future frozen Gate C or Gate D request that
itself states a command must be quoted verbatim in its authorization must have that requirement
either (a) satisfied literally — the authorizing instruction quotes the exact command text — or
(b) the frozen request's own drafted text must be revised, before authorization is sought, to state
explicitly that authorization by unambiguous reference to a uniquely identified, fixed, and
already-reviewed draft is sufficient for that step. Silence on this point, as occurred here, is
resolved this one time by this addendum and is not to be relied on again.

## What this addendum does not do

- It does not reopen or alter `GOVERNANCE_NOTE.md`, `README.md`'s original tables, `COMMAND_TRACE.md`,
  or any other file in `..` — all stand exactly as merged in PR #178/#179.
- It does not grant, imply, or move toward Gate D authorization. `GATE_D_AUTHORIZATION: NOT_GRANTED`
  is unchanged.
- It does not resolve the separate, pre-existing, unrelated
  `docs/evidence/r4_full_local_2026-10-13/governance/LAUNCH_AUTHORIZATION_DISCREPANCY.md` dispute.
- It does not authorize or schedule a next Gate D request; per the owner's own framing, that
  remains a distinct future step, requiring its own fresh operator declaration and Gate C
  validation under the governing protocol, to be separately proposed and separately authorized.
