# Command trace — Gate C `CHECK_ONLY`, attempt `GATED2-20261015T005128Z-1b0b45`

Executed 2026-10-15, following `gate_c_request.md`'s L1/S1–S5 sequence from the local preparation
directory `<REDACTED_LOCAL_HOME>/r4_gate_c_staging_GATED2-20261015T005128Z-1b0b45/` exactly as
drafted and reviewed. No step deviated from its drafted text. `$SSHO` and `$TARGET` below match
the connection parameters drafted and reviewed beforehand.

```
SSHO="-i <REDACTED_LOCAL_HOME>/.ssh/id_ed25519_dedirock -o BatchMode=yes -o StrictHostKeyChecking=yes -o IdentitiesOnly=yes -o ConnectTimeout=20 -o ForwardAgent=no"
TARGET="bgilbert1984@<REDACTED_SSH_HOST>"
```

**A known methodological limitation of this trace, disclosed rather than papered over:** shell
exit codes (`$?`) were not explicitly captured after each command. Each step's success below is
inferred from the absence of any error text, from output matching the drafted expectation exactly,
and — for S3 and S5 — from an explicit digest comparison against the frozen manifest. This is
weaker than a captured exit code and is named here as a gap in the record, not hidden.

## Host-key verification, immediately before S1

```
$ ssh-keygen -F <REDACTED_SSH_HOST> -f <REDACTED_LOCAL_HOME>/.ssh/known_hosts
<REDACTED_SSH_HOST> ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEyakBovvMw8K07qKA+crrdS4zmlJLeQ1sAsBVvNTMew
```
Matched the single pinned entry, unchanged from attempt 312512's connection. No new trust
decision was made.

## S1 — create the remote workspace

```
ssh $SSHO $TARGET 'test ! -e ~/r4_replay_GATED2-20261015T005128Z-1b0b45 && mkdir ~/r4_replay_GATED2-20261015T005128Z-1b0b45 && echo S1_CREATED'
```
Output (verbatim, complete):
```
S1_CREATED
```
No `&& hostname` or any other addition. Output exactly matched the drafted expectation.

## S2 — transfer the four frozen inputs

```
scp $SSHO scythe_r4_1_pkg_8d34de9ac0b1.tar.gz scythe_r4_1_pkg_8d34de9ac0b1.tar.gz.sha256 gate_c_preflight.py operator_declaration.json $TARGET:r4_replay_GATED2-20261015T005128Z-1b0b45/
```
Output: none (silent `scp` completion; no error text appeared).

## S3 — verify remote sizes and digests

```
ssh $SSHO $TARGET 'cd ~/r4_replay_GATED2-20261015T005128Z-1b0b45 && for f in scythe_r4_1_pkg_8d34de9ac0b1.tar.gz scythe_r4_1_pkg_8d34de9ac0b1.tar.gz.sha256 gate_c_preflight.py operator_declaration.json; do printf "%s %s %s\n" "$(stat -c %s "$f")" "$(sha256sum "$f" | cut -c1-64)" "$f"; done'
```
Output (verbatim):
```
119721 2b2e3251b678a5e83735ee663176f7763482a6948a7d5cb57f244dac1142874a scythe_r4_1_pkg_8d34de9ac0b1.tar.gz
102 ec6acabc5523852c07cd41802b9651d2c1009fc58fd74c4d4cc02a9f9a9fc043 scythe_r4_1_pkg_8d34de9ac0b1.tar.gz.sha256
20247 9e84bc77c49e37ed1e01e0c35ea72dcbf75b5752ab2d5d8a91ff53b62281452d gate_c_preflight.py
2731 9c8f893eb6eb666348963c35b14ba3271d5cd1f45a58d68cac94a49af4e1ad33 operator_declaration.json
```
Compared against `TRANSFER_MANIFEST.md`'s frozen four-row table: **all four rows match exactly**
(size and digest, both). No mismatch.

## S4 — Gate C `CHECK_ONLY`

```
ssh $SSHO $TARGET '/home/bgilbert1984/SCYTHE/.venv/bin/python -I -B ~/r4_replay_GATED2-20261015T005128Z-1b0b45/gate_c_preflight.py --mode CHECK_ONLY --archive ~/r4_replay_GATED2-20261015T005128Z-1b0b45/scythe_r4_1_pkg_8d34de9ac0b1.tar.gz --python /home/bgilbert1984/SCYTHE/.venv/bin/python --job-dir ~/r4_replay_GATED2-20261015T005128Z-1b0b45/job --record-dir ~/r4_replay_GATED2-20261015T005128Z-1b0b45/record_check --attempt-label GATED2-20261015T005128Z-1b0b45 --operator-declaration ~/r4_replay_GATED2-20261015T005128Z-1b0b45/operator_declaration.json'
```
No `--reference-preflight` flag, matching the corrected scope (Gate D protocol §7 step 6).

Output (verbatim, complete — also preserved structurally in `PREFLIGHT_RECORD.txt`/`.json` in this
directory, retrieved by S5):
```
PASS    1 archive SHA-256 equals the declared package digest  2b2e3251b678a5e83735ee663176f7763482a6948a7d5cb57f244dac1142874a
PASS    1a archive sidecar records the declared digest
PASS    1b safe extraction (no absolute paths, '..', links, devices)  []
PASS    1c sha256sum -c SHA256SUMS in the extracted package  28 OK
PASS    1d runner and harness digests equal the declared ones
PASS    2 PYTHON is an absolute executable file  /home/bgilbert1984/SCYTHE/.venv/bin/python
PASS    2a the interpreter imports NumPy and reports its identity  [3, 12, 14] numpy 2.5.3
PASS    2b float64 is 8 bytes and the byte order is little-endian (not permitted to differ from the reference)  8 little
PASS    3 job directory is an absolute path that does not exist  /home/bgilbert1984/r4_replay_GATED2-20261015T005128Z-1b0b45/job
PASS    3b job directory parent exists and is writable  /home/bgilbert1984/r4_replay_GATED2-20261015T005128Z-1b0b45
PASS    3c job directory is not inside the package or the record directory
PASS    3d at least 10 GB free on the job filesystem (measured)  66.5 GB at /home/bgilbert1984/r4_replay_GATED2-20261015T005128Z-1b0b45
PASS    3e at least 10 GB free on the TMPDIR filesystem (measured)  66.5 GB at /tmp
PASS    3f at least 4 GB available memory  5.2 GB
PASS    4 no competing r4 job process is running  []
PASS    5 operator declaration: host available for the whole 4 h window, no planned maintenance, reboot or reclaim of the host during the window  operator 'Ben Gilbert', host dedirock-919498765, window 2026-10-15T02:00:00+00:00 to 2026-10-15T06:00:00+00:00, digest 9c8f893eb6eb6663...
[environment table, see DEDIROCK_ENVIRONMENT_SNAPSHOT.md]

PREFLIGHT_VERDICT: PASS
LAUNCH: NOT_PERFORMED (Gate D needs its own explicit authorization)
```
Host-reported execution timestamp (from the retrieved record, not from the local terminal clock):
`TIMESTAMP_UTC: 2026-10-15T02:13:54+00:00`.

## S5 — retrieve exactly three named files

```
RETRIEVAL_TS=20261015T021410Z   # captured via `date -u` immediately before this step
scp $SSHO $TARGET:r4_replay_GATED2-20261015T005128Z-1b0b45/record_check/PREFLIGHT_RECORD.json $TARGET:r4_replay_GATED2-20261015T005128Z-1b0b45/record_check/PREFLIGHT_RECORD.txt $TARGET:r4_replay_GATED2-20261015T005128Z-1b0b45/record_check/PREFLIGHT_SHA256SUMS <REDACTED_LOCAL_HOME>/r4_gate_c_returns/GATED2-20261015T005128Z-1b0b45_${RETRIEVAL_TS}/
```
Output: three files appeared in the destination directory; nothing else. The archive copy,
`interpreter.txt`, and `sha256sum_package.txt` — all listed inside `PREFLIGHT_SHA256SUMS` and
confirmed present in `record_check/` by S4's own output — were deliberately **not** retrieved.

Post-retrieval local verification against `PREFLIGHT_SHA256SUMS`'s own recorded digests:
```
OK   PREFLIGHT_RECORD.json
OK   PREFLIGHT_RECORD.txt
```
(`PREFLIGHT_SHA256SUMS` does not list its own digest — by the tool's own design — so there is
nothing to self-check it against beyond `scp`'s transport integrity.)

## What did not happen

No `RECHECK`. No monitor start. No `run_job.sh` invocation. No job directory was created (S4's own
item 3 confirms `~/r4_replay_GATED2-20261015T005128Z-1b0b45/job` did not exist at `CHECK_ONLY`
time, and nothing in this trace creates it afterward). No read, write, or listing of
`~/r4_replay_312512` or any other pre-existing remote path. No command, flag, or pipeline beyond
what is written above.
