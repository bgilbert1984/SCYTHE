# Proposed Gate C `CHECK_ONLY` request — attempt GATED2-20261015T005128Z-1b0b45

**PROPOSED_NOT_AUTHORIZED.** This is a draft for review. No SSH connection has been made to
produce it. It takes attempt-312512's actually-reviewed procedure as its starting point and
corrects the two disclosed deviations (see
`<REDACTED_LOCAL_HOME>/r4_gate_c_audit_312512/GATE_C_DEVIATION_ADDENDUM.md`) rather than repeating them.
Nothing below is authorized by its own existence; a separate, explicit authorization message is
required before any step runs, per the owner's "STEPS 3–4 APPROVED — LOCAL ONLY" instruction.

**Revision note (second pass):** this draft now also corrects a scope mismatch found on closer
reading of the already-merged Gate D protocol §7 steps 3–7, which define the transfer and
`CHECK_ONLY` invocation for a Gate-C-run-in-service-of-Gate-D in **four**-artifact terms (no
`reference_PREFLIGHT_RECORD.json`, no `--reference-preflight`), and in a **three**-file retrieval
terms for §6.1 item 1 (`PREFLIGHT_RECORD.json`, `PREFLIGHT_RECORD.txt`, `PREFLIGHT_SHA256SUMS` —
not `interpreter.txt` or `sha256sum_package.txt`). The first pass of this draft, written from
attempt 312512's broader actual retrieval instead of the governing protocol text, is corrected
below. See `TRANSFER_MANIFEST.PROPOSED_NOT_AUTHORIZED.md`'s "Scope discipline — corrected finding"
for the full explanation. S1's output marker (`&& echo S1_CREATED`) is unchanged and, per the
owner's confirmation, is acceptable to carry into a real authorization provided it is quoted
there verbatim, exactly as it appears below — never approved "by reference to this draft" alone.

## Identity

- Attempt label: `GATED2-20261015T005128Z-1b0b45`
- Remote workspace (new; nothing else is touched): `~/r4_replay_GATED2-20261015T005128Z-1b0b45`
- SSH target: `bgilbert1984@<REDACTED_SSH_HOST>` (historically documented; the host identity is
  re-confirmed by the preflight tool's own environment checks at `CHECK_ONLY`, not assumed from
  this label)
- SSH options (fixed; no ad hoc additions at connection time — attempt 312512 added
  `-o LogLevel=VERBOSE` to S1 outside the authorized text, which is not repeated here):
  `-i <REDACTED_LOCAL_HOME>/.ssh/id_ed25519_dedirock -o BatchMode=yes -o StrictHostKeyChecking=yes -o IdentitiesOnly=yes -o ConnectTimeout=20 -o ForwardAgent=no`
- Remote interpreter path: `/home/bgilbert1984/SCYTHE/.venv/bin/python` (historically documented;
  `gate_c_preflight.py` independently re-verifies the interpreter's own binary digest, version and
  NumPy at `CHECK_ONLY` — this path is not trusted on the strength of this label alone)

## Trusted host-key verification procedure

`StrictHostKeyChecking=yes` only — never `accept-new`, never `no`. The host key is already pinned
locally from a prior session, not freshly trusted now:

```
$ ssh-keygen -F <REDACTED_SSH_HOST> -f <REDACTED_LOCAL_HOME>/.ssh/known_hosts
<REDACTED_SSH_HOST> ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEyakBovvMw8K07qKA+crrdS4zmlJLeQ1sAsBVvNTMew
```

matching the fingerprint recorded during attempt 312512's connection
(`ED25519 SHA256:IkWqDRpZli7Q99pMJ7t/GzVP2f48ZOSmGF/eTwKeoy8`, per the deviation addendum). The
procedure for this attempt is: confirm this exact entry is still the sole `<REDACTED_SSH_HOST>` line in
`known_hosts` before S1, and let `StrictHostKeyChecking=yes` refuse the connection outright on any
mismatch — never silently accept a changed key. No command in this request adds `-o
LogLevel=VERBOSE` or any other flag to surface the fingerprint a second time; its presence here is
read from the local file, not from a new connection.

## Permitted read/write paths

- **Write:** `~/r4_replay_GATED2-20261015T005128Z-1b0b45/` only (created fresh by S1) and its own
  `record_check/` subdirectory, created by the tool itself during S4. No other path is written,
  touched, or created.
- **Read (remote):** only the four named files inside
  `~/r4_replay_GATED2-20261015T005128Z-1b0b45/` (S3) and only the three named output files inside
  `~/r4_replay_GATED2-20261015T005128Z-1b0b45/record_check/` (S5, enumerated below — never derived
  by reading `PREFLIGHT_SHA256SUMS` and copying whatever it lists).
- **Explicitly not permitted:** reading, listing, modifying or deleting
  `~/r4_replay_312512`, `~/r4_gate_c_*` or any other pre-existing remote path; this carries forward
  the Gate D protocol's own protection of historical evidence (§7 "Not permitted") to this Gate C
  request, since the same replay host holds attempt 312512's remote state and it must stay
  undisturbed.

## Commands

**L1 (local, before any connection).** Verify the four source artifacts against
`TRANSFER_MANIFEST.PROPOSED_NOT_AUTHORIZED.md` (digests already freshly measured there) and write
a local manifest file. No remote action.

**S1.** Create exactly one new remote directory; nothing else.
```
ssh $SSHO bgilbert1984@<REDACTED_SSH_HOST> 'test ! -e ~/r4_replay_GATED2-20261015T005128Z-1b0b45 && mkdir ~/r4_replay_GATED2-20261015T005128Z-1b0b45 && echo S1_CREATED'
```
Expected output: `S1_CREATED`. No `&& hostname` or any other command appended — this is the
exact correction of attempt 312512's deviation 1. This exact string, including the `&& echo
S1_CREATED` marker, is what must be quoted verbatim in any real authorization message — the owner
has confirmed the marker itself is acceptable, but only as explicit, quoted text, never as an
unstated addition improvised at execution time.

**S2.** Transfer the four frozen inputs by explicit filename (never a glob) — per Gate D protocol
§7 step 4's own "exactly four frozen inputs" list, not attempt 312512's five:
```
scp $SSHO scythe_r4_1_pkg_8d34de9ac0b1.tar.gz scythe_r4_1_pkg_8d34de9ac0b1.tar.gz.sha256 gate_c_preflight.py operator_declaration.json bgilbert1984@<REDACTED_SSH_HOST>:r4_replay_GATED2-20261015T005128Z-1b0b45/
```
Not permitted to run until the operator confirms the declaration's SHA-256 (drafted; see "Stop
conditions" below). Expected output: four files transferred, no error.

**S3.** Verify remote sizes and digests of exactly those four named files (no glob, no directory
listing):
```
ssh $SSHO bgilbert1984@<REDACTED_SSH_HOST> 'cd ~/r4_replay_GATED2-20261015T005128Z-1b0b45 && for f in scythe_r4_1_pkg_8d34de9ac0b1.tar.gz scythe_r4_1_pkg_8d34de9ac0b1.tar.gz.sha256 gate_c_preflight.py operator_declaration.json; do printf "%s %s %s\n" "$(stat -c %s "$f")" "$(sha256sum "$f" | cut -c1-64)" "$f"; done'
```
Expected output: four lines, each matching the local manifest's size and digest exactly. Any
mismatch is a stop condition.

**S4.** Run `CHECK_ONLY` under the declared interpreter — **without** `--reference-preflight`,
per Gate D protocol §7 step 6's own invocation, which does not name that flag:
```
ssh $SSHO bgilbert1984@<REDACTED_SSH_HOST> '/home/bgilbert1984/SCYTHE/.venv/bin/python -I -B ~/r4_replay_GATED2-20261015T005128Z-1b0b45/gate_c_preflight.py --mode CHECK_ONLY --archive ~/r4_replay_GATED2-20261015T005128Z-1b0b45/scythe_r4_1_pkg_8d34de9ac0b1.tar.gz --python /home/bgilbert1984/SCYTHE/.venv/bin/python --job-dir ~/r4_replay_GATED2-20261015T005128Z-1b0b45/job --record-dir ~/r4_replay_GATED2-20261015T005128Z-1b0b45/record_check --attempt-label GATED2-20261015T005128Z-1b0b45 --operator-declaration ~/r4_replay_GATED2-20261015T005128Z-1b0b45/operator_declaration.json'
```
Expected output: the tool's own PASS/FAIL/PENDING item list, ending in an overall verdict; a
`record_check/` directory containing `PREFLIGHT_RECORD.json`, `PREFLIGHT_RECORD.txt`,
`PREFLIGHT_SHA256SUMS`, `interpreter.txt`, `sha256sum_package.txt`, and the tool's own internal
archive copy inside its record layout — confirmed by reading `gate_c_preflight.py` itself (its
`sh()` calls and its `PREFLIGHT_SHA256SUMS`-writing loop, which excludes only
`extracted_package/`): these are the complete and only contents of `record_check/`, no more, no
fewer. Only three of these five are retrieved — see S5. `--job-dir` names a path that must not
already exist; the tool does not create the job itself in `CHECK_ONLY` mode.

**S5.** Retrieve only these three explicitly named files from `record_check/`, individually, by
exact remote source and local destination — per Gate D protocol §6.1 item 1, and correcting both
attempt 312512's deviation 2 (which retrieved the tool's internal archive copy) and this draft's
own first-pass error (which also proposed retrieving `interpreter.txt` and `sha256sum_package.txt`,
neither of which §6.1 item 1 names):

| Remote source | Local destination |
|---|---|
| `bgilbert1984@<REDACTED_SSH_HOST>:r4_replay_GATED2-20261015T005128Z-1b0b45/record_check/PREFLIGHT_RECORD.json` | `~/r4_gate_c_returns/GATED2-20261015T005128Z-1b0b45_<retrieval-timestamp>/PREFLIGHT_RECORD.json` |
| `bgilbert1984@<REDACTED_SSH_HOST>:r4_replay_GATED2-20261015T005128Z-1b0b45/record_check/PREFLIGHT_RECORD.txt` | `~/r4_gate_c_returns/GATED2-20261015T005128Z-1b0b45_<retrieval-timestamp>/PREFLIGHT_RECORD.txt` |
| `bgilbert1984@<REDACTED_SSH_HOST>:r4_replay_GATED2-20261015T005128Z-1b0b45/record_check/PREFLIGHT_SHA256SUMS` | `~/r4_gate_c_returns/GATED2-20261015T005128Z-1b0b45_<retrieval-timestamp>/PREFLIGHT_SHA256SUMS` |

```
scp $SSHO bgilbert1984@<REDACTED_SSH_HOST>:r4_replay_GATED2-20261015T005128Z-1b0b45/record_check/PREFLIGHT_RECORD.json bgilbert1984@<REDACTED_SSH_HOST>:r4_replay_GATED2-20261015T005128Z-1b0b45/record_check/PREFLIGHT_RECORD.txt bgilbert1984@<REDACTED_SSH_HOST>:r4_replay_GATED2-20261015T005128Z-1b0b45/record_check/PREFLIGHT_SHA256SUMS ~/r4_gate_c_returns/GATED2-20261015T005128Z-1b0b45_<retrieval-timestamp>/
```
Confirmed none of these three is the archive copy (`scythe_r4_1_pkg_8d34de9ac0b1.tar.gz`, excluded)
or `extracted_package/` (a directory, also excluded, and never individually `scp`'d even if it
were permitted). `interpreter.txt` and `sha256sum_package.txt` are real files `CHECK_ONLY`
produces and are listed inside `PREFLIGHT_SHA256SUMS`, but they are diagnostic logs, not part of
§6.1's named category — they are deliberately **not** retrieved by this request; a future need
for them would be its own flagged addition, not an improvisation at retrieval time. This request
names its three files directly; it does not derive the retrieval list by reading
`PREFLIGHT_SHA256SUMS` first. After retrieval, verify each file locally against
`PREFLIGHT_SHA256SUMS`'s own recorded digest for it.

## Stop conditions (no repair, no retry)

- **Resolved.** `operator_declaration.json` exists at
  `<REDACTED_LOCAL_HOME>/r4_gate_c_staging_GATED2-20261015T005128Z-1b0b45/operator_declaration.json`
  (window `2026-10-15T02:00:00Z`–`06:00:00Z`; SHA-256
  `9c8f893eb6eb666348963c35b14ba3271d5cd1f45a58d68cac94a49af4e1ad33`, 2731 bytes; format-validated
  locally against `gate_c_preflight.py`'s own `check_declaration()` logic), and the operator has
  explicitly confirmed, in-session, that this exact digest is the declaration they intend. Per the
  trusted-declaration workflow (`metamorphic_r4_gate_c_preflight.md` §9.2), **this declaration is
  now effective** for the stated window. This clears the one precondition on S2 that this stop
  condition previously named — it does **not**, by itself, authorize S2 or any other step to run;
  that is a separate decision, still pending (see "What this draft does not do").
- Any S1 output other than exactly `S1_CREATED` (including the directory already existing) → stop;
  do not delete, do not retry under the same label, do not fall back to a different directory name.
- Any S3 digest or size mismatch → stop; do not re-transfer in place; a corrupted transfer under
  this label is a documented failure of this attempt, not something to patch over.
- `CHECK_ONLY`'s verdict is anything other than `PASS` → stop; do not re-run `CHECK_ONLY` under the
  same label hoping for a different measurement; a fresh attempt label and fresh declaration would
  be required for another try, exactly as the Gate D protocol already requires for Gate D itself.
- Any S5 local-verification mismatch after retrieval → stop; flag the discrepancy; do not discard
  the mismatched file silently.
- At every step: if the command as issued differs even slightly from the text above (an appended
  flag, an extra pipe, a wildcard), stop and treat it as a scope deviation requiring its own
  disclosure — not something to complete and explain afterward, which is exactly what happened
  with attempt 312512's two deviations.
- This request governs `CHECK_ONLY` only. It does not request, and does not become, a `RECHECK`,
  a job launch, or any Gate D action. Gate D RECHECK or replay remains **NOT AUTHORIZED** regardless
  of this `CHECK_ONLY`'s outcome.

## What this draft does not do

It does not create the remote directory, transfer anything, or run `CHECK_ONLY`. The operator
declaration is now confirmed and effective (above). That is a precondition satisfied, not an
authorization granted: issuing this document as an actual authorization to connect and run
L1/S1–S5 is a separate decision, not yet given. **No SSH connection is made on the strength of
any open-ended statement that "any connection that could help is authorized"** — each step above
still requires the operator's own explicit, step-by-step or whole-sequence go-ahead, exactly as
every prior step in this protocol has required.
