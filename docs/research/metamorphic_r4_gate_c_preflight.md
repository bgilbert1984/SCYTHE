# Metamorphic harness r4.1 — Gate C: replay-host preflight and restricted transfer scope (draft)

> **DRAFT FOR REVIEW — NO CONNECTION TO, TRANSFER TO, OR EXECUTION ON ANY OTHER HOST IS AUTHORIZED BY THIS DOCUMENT.** It specifies what Gate C would do so that the request for it can be reviewed. Gate C and Gate D each need their own explicit authorization.
> **Amendment 1 (hardening, before any Gate C activity)** is §9: field classes, a digest-bound operator declaration, schema version 2. Adds a document, a preflight tool and its tests. Changes no production code, harness, runner, package, comparator, protocol or evidence.
> **Amendment 2** is §10: a second, optional operator-declaration schema (`gate-c-operator-declaration/2`) for a single-operator, single-purpose sandbox host — a standing declaration bound to a `sandbox_epoch` instead of a time window, valid until withdrawn. The original windowed schema (`/1`) is unchanged and still accepted; this is an addition, not a replacement.

## 1. Purpose
Establish, before any replay job exists, that the exact frozen package can be launched on the replay host without violating the custody, resource or isolation conditions, and **freeze the replay host's numerical environment** so that it cannot change between the preflight and the launch (protocol §5.3, §6). The preflight records environmental differences; it does not judge them.

## 2. Reference environment (the retained local run; for the environment table)
| Field | Reference |
|---|---|
| Host | WSL2 on a mobile workstation; kernel 6.18.33.2-microsoft-standard-WSL2; CPU Intel Core i5-8250U, 8 logical CPUs; x86-64 |
| Python | 3.12.13 (GCC 14.3.1 build); interpreter binary sha256 `8c515a32b1a5d3d807e53359901a4d09ec06819b488736641aabd6a12eefba63` |
| NumPy | 2.5.1; BLAS scipy-openblas 0.3.33.112 (Haswell target, dynamic arch); SIMD baseline X86_V2, dispatch found X86_V3; little-endian float64 |
| Threads | OPENBLAS/OMP/MKL/NUMEXPR = 1 |
Historical, **not assumed current**: the r3 run on the replay host recorded Python 3.12.14, NumPy 2.5.3 and an EL9 kernel (5.14.0-687.48.1.el9_8). Such differences, including a different SIMD dispatch (which can change last-bit rounding of vectorised kernels), are exactly what the table records.

## 3. The preflight tool
`tools/r4_package/gate_c_preflight.py` (stdlib only; sha256 `85c4f9e1a80abafe68be21b36abb60bdea09aa291aa45232a286bbdbad3356ad`), run **on the replay host with the declared interpreter**. It is derived from the local full-run preflight (external tool, sha256 `bf75a38cca4149e4213e98ce04abd162d8c34d3a69ea0a46b223a91155467481`, recorded in the local execution evidence); differences: repository-portable; no checkout-dependent verifiers (package integrity is the archive digest, `SHA256SUMS`, and the runner and harness digests);
the interpreter's version is **recorded, not required**; adds NumPy build information (BLAS, SIMD, compiler), CPU model, and the environment table against the reference record; adds the `RECHECK` mode; a non-Python executable that exits 0 is reported as a failed item, not a crash (found while testing).
It never creates the job directory, starts a job, writes outside `--record-dir`, deletes, or installs; a crash exits 2 as `GATE_C_TOOL_FAILURE` and is not a pass.

| Item | Check | Failure means |
|---|---|---|
| 1, 1a-1d | archive digest equals `2b2e3251…`; sidecar agrees; safe extraction; `sha256sum -c SHA256SUMS`; runner (`02453e0d…`) and harness (`1ac4fe34…`) digests | refuse |
| 2, 2a, 2b | `PYTHON` is an absolute executable file that imports NumPy and reports its identity; float64 is 8 bytes and little-endian (not permitted to differ) | refuse |
| 3, 3b-3f | job directory absolute and nonexistent; parent writable; not inside the package or record; at least 10 GB free on the job and temporary filesystems and 4 GB available memory, **measured** | refuse |
| 4 | no competing r4 job process | refuse |
| 5 | structured operator declaration, either schema (§9.2, §10): host available for the whole 4-hour window (`/1`) or a standing declaration valid until withdrawn (`/2`, §10) | pending until given at CHECK_ONLY; **missing at RECHECK is a refusal**; never silently passed |
| 6s, 6p, 6, 6r, 6a (`RECHECK` only) | prior record is schema 2 with a verifying checksum file and verdict PASS; the launch-time environment equals the record exactly (host, kernel, CPU, Python, NumPy and its path, interpreter binary digest, BLAS, SIMD, compiler); requirements, thresholds, package and job path equal the record's; a changed boot ID is reported | refuse the replay |
The record also states the local runtime baseline (total 1,491 s; controls 162 s; fixtures and pins 24 s; smoke 203 s; full 1,101 s) for comparison with the replay's stage durations, and an attempt ID bound to the package, host, boot ID, interpreter binary digest and job path.
Verdicts: `PASS`, `PASS_OPERATOR_CONFIRMATION_PENDING`, `FAIL`; a run with a declaration override (tests only) is permanently marked `INVALID_FOR_GATE_C_DECLARATION_OVERRIDE_*`.

## 4. Restricted transfer scope (what an authorization of Gate C would permit, exhaustively)
Steps run in this order; any deviation or unexpected output stops the gate and is recorded.
1. **Local, before any connection:** record the archive digest, the tool digest and the local dry-run result (§6). Prepare the exact remote interpreter path to be named in the authorization request.
2. **One new directory:** `test ! -e ~/r4_replay_<ATTEMPT> && mkdir ~/r4_replay_<ATTEMPT>` on the replay host. This is the only filesystem object created outside the tool's record directory (which is inside it).
3. **Three files copied into it:** the package archive, its `.sha256` sidecar and `gate_c_preflight.py`.
4. **Digest check of the three copied files** against the local digests (`sha256sum`).
5. **Run the tool** in `CHECK_ONLY` mode with the declared absolute interpreter (the tool is stdlib-only and runs under that same interpreter), job path and record path inside the new directory. Read-only apart from its record.
6. **Retrieve the record** (`PREFLIGHT_RECORD.*`, `PREFLIGHT_SHA256SUMS`, the logs; not the extracted package) to the workstation and verify its checksum file. The remote copy is retained.
Not permitted: reading, listing, modifying or deleting any existing remote directory (including the r2 and r3 evidence directories, packages and tarballs); sudo or any privilege change; installing or upgrading software; killing, stopping or signalling processes; changing services, firewall or cron; any outbound connection other than the SSH session; creating the job directory; starting any job; any command not listed above.
Access is through the SSH login that was separately authorized for this work; credentials are not recorded in the repository.

## 5. Records and dispositions
`GATE_C_PREFLIGHT`: `PASS` / `PASS_OPERATOR_CONFIRMATION_PENDING` / `FAIL` / `TOOL_FAILURE`, independent of everything else. A passing Gate C record does **not** authorize Gate D and **expires** at the launch-time `RECHECK`: if the `RECHECK` environment differs from the record by any field, the replay is refused and a new Gate C is needed. Failed and refused attempts are preserved, never repaired in place.
The environment table is attached to the review, so the reviewer sees the differences before deciding on Gate D.

## 6. Local dry run (this workstation; no other host)
The tool was run in `CHECK_ONLY` and `RECHECK` mode on the workstation against the real frozen archive with the declared interpreter and the retained reference record: every item passed, the job directory was not created, and the record's checksum file verified (`PREFLIGHT_SHA256SUMS` sha256 `d290871d0c339e8a85b8d5422b29bcebdb727710d7e4ba0f2796f5591c3e3753`; the record is local and not committed). This shows the tool works, not that the replay host passes.

## 7. Tests
`test_rf_r4_gate_c_preflight.py` (28 tests after Amendment 2; sha256 `9888fedc12201142c9a8a76cf7c6a9ca7dc5deb877f1dbdea1f8662cf21d79f0`) exercises the tool on a synthetic package: a clean pass that is marked invalid for Gate C under the test override; the frozen constants refusing the synthetic package without it; a pending operator item; a wrong digest, a tampered archive and a wrong runner digest; non-Python and relative interpreters; an existing job or record directory and a `LAUNCH` mode; insufficient space or memory; a competing process; `RECHECK` equal and per-field changed environments; a crash; no writes outside the record directory; standard-library-only with no launch, delete or install path; a standing (`/2`) declaration passing CHECK_ONLY and RECHECK with no expiry, including arbitrarily far in the future; every `/2` malformed/invalid case (wrong host, package, scope, non-`ACTIVE` status, declared in the future, a missing or non-positive-integer `sandbox_epoch`, withdrawal); `/2` identity-continuity and withdrawal at RECHECK; an unrecognized declaration schema refused outright.

## 9. Amendment 1: field classes, operator-declaration identity, schema version 2
**9.1 Field classes** (stored in every record as `field_classes`). A changed *measurement* is acceptable while the frozen *requirement* still holds; a changed requirement is not.
| Class | Fields | CHECK_ONLY → RECHECK rule |
|---|---|---|
| **FROZEN** | package, runner and harness digests; interpreter binary digest, Python and NumPy versions and path, BLAS, SIMD, compiler, CPU, kernel, host; every requirement (free-space and memory thresholds, window length, source commit); the job path; the declaration identity | exact equality |
| **REMEASURED** | free space (job, temporary), available memory, load, competing processes, job directory absence | measured again and judged against the FROZEN thresholds; recorded, never compared for equality (18 GB → 15 GB passes a 10 GB threshold; a threshold changed from 10 to 8 GB is refused) |
| **RECONFIRMED** | operator declaration validity | same identity, and independently valid now |
**9.2 Operator declaration.** A JSON file made by the operator (schema `gate-c-operator-declaration/1`): operator, host, package digest, window start and end (exactly the declared window length), declaration timestamp and the required scope text (`no planned maintenance, reboot or reclaim of the host during the window`). The tool records its **identity** (those fields plus the file's SHA-256) in the CHECK_ONLY record.
Trusted workflow: the operator creates and keeps the file; neither this tool nor an assistant creates an operator declaration (the local dry run used an explicitly labelled `DRY_RUN_NOT_AN_OPERATOR_DECLARATION`). Withdrawal is a marker file `<declaration>.withdrawn`. A signed declaration is a possible later enhancement; a digest-bound declaration is sufficient for this control given the workflow above.
CHECK_ONLY: valid declaration → pass; missing → pending; malformed, wrong host or package, wrong scope or window length, declared in the future, already expired or withdrawn → fail. RECHECK: the declaration must have **the same identity** as the frozen one, be inside its window now, and not be withdrawn; missing, changed, expired, not-yet-open or withdrawn → refusal.
**9.3 Schema version 2.** The record carries `schema_version`, `field_classes`, `requirements`, `measurements` and the declaration identity. RECHECK rejects a prior record that is older or incomplete, that does not verify against its own checksum file (it is an immutable reference), or whose verdict is not PASS (a pending CHECK_ONLY cannot be rechecked into a pass).
**9.4 Unchanged.** The interpreter version is still recorded, not required; no connection, transfer or execution is authorized; the historical launch-authorization discrepancy is untouched.

## 10. Amendment 2: standing declaration (schema `gate-c-operator-declaration/2`), `sandbox_epoch`
**Motivation.** Amendment 1's windowed declaration (§9.2) models a shared host where other people, or the operator's own forgetfulness, might interfere during a fixed interval — hence a bounded, repeatedly-renewed window. For a single-operator, single-purpose, ephemeral sandbox host with no other stakeholders, re-drafting and re-confirming that same window every few hours adds administrative churn without adding confidence: the thing actually being promised (no provider-side host migration, resize or reclaim that the operator knows of) doesn't become more or less true because a fixed number of hours elapsed — it's an event-driven fact (reality changed, or it didn't), not a time-driven one. This amendment adds a second declaration schema for that case; it does **not** remove or alter `/1`, which remains the right choice whenever more than one person's intent needs bounding.

**10.1 Schema.** `gate-c-operator-declaration/2`: `operator`, `host`, `package_sha256`, `status` (must be the literal string `ACTIVE`), `declared_at_utc`, `scope` (must be the literal text `sole administrator of this temporary sandbox; this declaration applies until explicitly withdrawn (no expiry)`), and `sandbox_epoch` (a positive integer). There is no `window_start_utc`/`window_end_utc`.

**10.2 Validity rule.** No time-based expiry at all: CHECK_ONLY and RECHECK both accept the declaration as long as `status` is `ACTIVE`, `declared_at_utc` is not in the future, the host and package match, the scope text matches exactly, and the `<path>.withdrawn` marker (unchanged from `/1`) does not exist. RECHECK additionally requires the declaration's full identity (every field above, plus `sandbox_epoch` and the file's own SHA-256) to equal the one frozen in the CHECK_ONLY record — this is the same identity-continuity check `/1` already had, applied unchanged; nothing about `/2` weakens it.

**10.3 What `sandbox_epoch` is for.** It is not verified against anything outside the declaration itself — Gate C does not keep separate ground truth about what the "current" epoch should be. It exists so the operator has an explicit, auditable way to say "the environment underneath this declaration changed materially" (a VM rebuild, a repurposing, a change of administrator) without inventing a new ad-hoc field: incrementing it and issuing a new declaration immediately invalidates any `CHECK_ONLY` record frozen against the old epoch, the same way changing the operator or host would. Routine operation never needs to touch it.

**10.4 Relationship to `boot_id` (protocol §4.1, Gate D Revision 5).** `sandbox_epoch` and `boot_id` continuity are deliberately separate, non-redundant checks operating at different lifetimes: `boot_id` is re-verified immediately before every launch and catches "did this specific attempt survive without a reboot between RECHECK and launch" (per-attempt, short-lived); `sandbox_epoch` is carried in the declaration identity and catches "has the operator declared the environment materially rebuilt or repurposed since this declaration was issued" (standing, long-lived). Neither substitutes for the other.

**10.5 What this does not loosen.** Artifact integrity (item 1), interpreter/environment freezing (item 2, §6), resource checks (item 3), the competing-process check (item 4), the FROZEN-requirements equality check at RECHECK (item 6r), and execution authorization itself are all unchanged by this amendment — only the operator-declaration validity rule gains a second, non-expiring option. A `/2` declaration being accepted is not, by itself, a weaker statement about *who* is responsible for it or *what* they're asserting; it is a different, honestly-scoped statement about *for how long* that assertion is meant to be trusted without being re-looked-at — "until withdrawn" rather than "for four hours," because an indefinite promise about third-party or provider-side events is not one anyone can honestly make, but an indefinite promise about the operator's own current knowledge, revocable the instant that knowledge changes, is.

## 8. What this does not do
No connection, transfer, execution, comparison or Gate D launch is authorized. It does not choose the replay interpreter: that path is named in the request for Gate C and then frozen by the `CHECK_ONLY` record.
