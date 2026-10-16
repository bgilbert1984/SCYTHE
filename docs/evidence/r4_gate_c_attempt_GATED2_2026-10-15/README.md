# Gate C `CHECK_ONLY` attempt `GATED2-20261015T005128Z-1b0b45` — evidence, 2026-10-15

**This directory is a redaction-aware evidence record of one real Gate C `CHECK_ONLY` run against
Dedirock, following the PR #169 evidence pattern.** It is not Gate D execution, not a `RECHECK`,
not a job launch, and not a reauthorization of anything. Prepared per the owner's "Approve
preparation of a redaction-aware Gate C evidence PR, locally only" instruction (2026-10-15).

## Contents

- `GATE_C_REQUEST_AS_AUTHORIZED.md` — the frozen command request as it stood from
  2026-10-15T02:04:27Z onward (its last edit before the "Authorize L1 and S1-S5" instruction and
  before S1 ran), redacted. Included so the authorization-reconciliation question in
  `GOVERNANCE_NOTE.md` can be assessed against the actual document, not a description of it.
- `COMMAND_TRACE.md` — the exact L1/S1–S5 commands, their outputs, and a disclosed limitation
  (shell exit codes were not explicitly captured).
- `TRANSFER_MANIFEST.md` — the frozen four-artifact manifest as actually transferred and verified,
  including the operator declaration's digest.
- `operator_declaration.json` — the declaration actually used, as transferred and checked live by
  `CHECK_ONLY`, redacted (SSH hostname only; see `REDACTION_MANIFEST.txt`).
- `PREFLIGHT_RECORD.json`, `PREFLIGHT_RECORD.txt`, `PREFLIGHT_SHA256SUMS` — the original files
  returned by `CHECK_ONLY`, committed byte-for-byte as retrieved (digest-verified in
  `INDEPENDENT_AUDIT.md`).
- `INDEPENDENT_AUDIT.md` — independently recomputed checksums and a field-by-field audit of the
  reported `PASS`, cross-checked against independently-sourced digests already committed
  elsewhere in this repository.
- `DEDIROCK_ENVIRONMENT_SNAPSHOT.md` — the numerical environment Dedirock reported (CPU, kernel,
  NumPy/BLAS/SIMD, available memory, uptime, load, and the local reference run's own timing
  baseline for context).
- `GOVERNANCE_NOTE.md` — **read this before treating this attempt as a precedent for anything.**
  It separates the technical result (a genuine, audited `PASS`) from an open question the owner
  raised about whether the authorization that preceded execution met this project's own standard
  for a bounded authorization. That question is recorded as open, not resolved, here — see
  `governance/GATE_C_BOUNDED_AUTHORIZATION_ADDENDUM_2026-10-15.md` for the resolution.
- `governance/GATE_C_BOUNDED_AUTHORIZATION_ADDENDUM_2026-10-15.md` — the dated addendum that
  resolves `GATE_C_BOUNDED_AUTHORIZATION_COMPLIANCE` without reopening or editing anything above.
- `REDACTION_MANIFEST.txt` — the one class of redaction applied (the literal SSH hostname, never
  previously committed to this repository) and why other identifiers already disclosed elsewhere
  in this repository's history (`dedirock-919498765`, `/home/bgilbert1984/...`) were not redacted
  again.

## Gate C bounded-authorization question: resolved by a dated addendum (2026-10-15)

`GATE_C_BOUNDED_AUTHORIZATION_COMPLIANCE`, left `PENDING_RECONCILIATION` in the tables below, is
resolved in `governance/GATE_C_BOUNDED_AUTHORIZATION_ADDENDUM_2026-10-15.md` — read that file for
the current, final disposition of that one field. Nothing else on this page or in
`GOVERNANCE_NOTE.md` is changed by that addendum.

## Gate status (the owner's merge-review disposition, 2026-10-15, supersedes the tables below)

**PR #178 is `APPROVED FOR MERGE`**, with two qualifications recorded in
`GOVERNANCE_NOTE.md`'s "Merge review qualifications" section and preserved past the merge: (1)
`GATE_C_REQUEST_AS_AUTHORIZED.md` itself states the S1 command must be quoted verbatim in any real
authorization, which the authorization actually given did not do — a real procedural
inconsistency, not just an open question of whether the file was reread; and (2) `PASS_VERIFIED`
below is scoped strictly to the documented preflight verification (`INDEPENDENT_AUDIT.md`), not to
any broader claim of cryptographic proof of the remote host's identity or independent attestation
of every remote operation.

| Item | Disposition |
|---|---|
| `GATE_C_TECHNICAL_RESULT` | `PASS_VERIFIED` (scoped to the documented preflight verification only — see qualification 2 above) |
| `GATE_C_EXPLICIT_AUTHORIZATION` | `ESTABLISHED` — "Authorize L1 and S1-S5, execute the Gate C sequence exactly as written" was a real, explicit instruction that was acted on |
| `GATE_C_BOUNDED_AUTHORIZATION_COMPLIANCE` | `PENDING_RECONCILIATION` — the authorization given did not quote S1 verbatim as the frozen request itself requires; this is a real inconsistency, to be adjudicated separately (see qualification 1 above), not a new Gate C run |
| `GATE_D_AUTHORIZATION` | `NOT_GRANTED` |

## Gate status at the time this PR was first drafted (the owner's earlier table, superseded above but preserved for the record)

| Gate | Disposition |
|---|---|
| New Gate C technical preflight | `PASS_REPORTED` |
| Gate C independent evidence audit | `PENDING` → addressed by `INDEPENDENT_AUDIT.md` in this PR |
| Gate C authorization reconciliation | `PENDING` — see `GOVERNANCE_NOTE.md`; not resolved by this PR |
| Gate D RECHECK | `NOT AUTHORIZED` |
| Gate D monitor or replay | `NOT AUTHORIZED` |
| Historical local launch dispute | `UNRESOLVED` |

## What this PR does not do

It does not reauthorize anything. It does not connect to Dedirock again, run `RECHECK`, transfer
the monitor, create a job directory, or launch anything — none of that occurred while preparing
this PR, per the owner's explicit stop instruction. It does not resolve the Gate C authorization
reconciliation question `GOVERNANCE_NOTE.md` raises, and it does not resolve the pre-existing,
unrelated local launch-authorization discrepancy
(`docs/evidence/r4_full_local_2026-10-13/governance/LAUNCH_AUTHORIZATION_DISCREPANCY.md`).
