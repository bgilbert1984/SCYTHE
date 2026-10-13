# r4 job evidence custody

How a finished r4 job directory is sealed, what the seal proves, and what it does not. Read this before treating a job directory as evidence.

## Terminal states

`STATUS.txt` ends with exactly one terminal record, written and flushed **before** anything is sealed:

| Terminal record | Meaning |
|---|---|
| `DONE harness_rc=0 gate_rc=0 …` | the full harness run and its acceptance gate both succeeded |
| `FAILED harness_rc=<a> gate_rc=<b> …` | the full run reached its end but the harness or the gate returned nonzero; both codes are preserved |
| `FAILED <stage> …` | an earlier stage failed (package verification, controls, fixtures, smoke, smoke gate); later stages did not run |

A consumer must read the *word* (`DONE` or `FAILED`), not merely the presence of a terminal line. The process exit status follows the same rule. Every failure path also seals its terminal record.

## Order of custody

1. write the terminal record (`DONE` or `FAILED`) and flush it
2. exit codes are already in `exit_codes.txt` (written before the terminal record)
3. `seal_job.py` writes `JOB_SHA256SUMS` over **every file in the job directory** except `holdout/`, **last**
4. nothing is written to the job directory afterwards

This corrects r3, whose runner appended its terminal line after generating the manifest (`STATUS_LOG_APPEND_AFTER_HASH`).
The sealer refuses to seal without a terminal `DONE`/`FAILED` record and refuses to overwrite an existing manifest.

## Fixtures: what is sealed and what is re-verified

The regenerated fixture set (`holdout/`, about 1.2 GB) is **not** hashed into `JOB_SHA256SUMS`. Instead:

- the fixture hash **list** (`fixture_hashes.sha256`) is copied into the job and is therefore **sealed** by the manifest: the *expected* hashes are bound;
- at **seal time** the actual files under `holdout/` are re-verified against that list, and the sealer **refuses to seal** on any mismatch, missing file or extra file;
- at **independent verification** the actual files are re-verified again against the sealed list.

So the manifest proves the list; the re-verification step proves the bytes. The fixture-generation log and the hash-verification log are records that a check happened — they are not a substitute for checking the current bytes.

## Independent verification

```
python3 tools/r4_job/seal_job.py JOB_DIR --verify [--require-fixtures]
```

It prints two separate outcomes and never merges them:

- `JOB_MANIFEST: VERIFIED | FAILED` — every manifest entry matches and no file is unlisted.
- `FIXTURE_BYTES: VERIFIED_AGAINST_SEALED_HASH_LIST | NOT_PRESENT_NOT_REVERIFIED | FIXTURE_LIST_MISSING | FAILED`.

A fixture changed after sealing, deleted, or added yields `FIXTURE_BYTES: FAILED` and a nonzero exit, even though the manifest alone cannot see it (this is exercised by a regression test).
If the fixture bytes are not present (for example the job directory was copied without `holdout/`), the outcome is `NOT_PRESENT_NOT_REVERIFIED` — an explicit statement that the bytes were not re-verified —
and `--require-fixtures` makes that, and a missing list, a failure. Regenerate the fixtures from the frozen package and re-run with `--require-fixtures` for a full independent verification.

## Limits

The seal is a SHA-256 manifest, not a signature: it detects accidental or unrecorded change after sealing; it does not authenticate who sealed it. Authenticity of the package source is carried separately by the package's own checksum manifest.
