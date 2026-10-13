# r4 job evidence custody

How a finished r4 job directory is sealed, what the seal proves, and what it does not. Read this before treating a job directory as evidence.

## Terminal states

`STATUS.txt` ends with exactly one terminal record, written and flushed **before** anything is sealed:

| Terminal record | Meaning |
|---|---|
| `DONE COMPUTATION_COMPLETED harness_rc=0 gate_rc=0 …` | the full harness run and its acceptance gate both succeeded. **This is not acceptance** (see Dispositions) |
| `FAILED harness_rc=<a> gate_rc=<b> …` | the full run reached its end but the harness or the gate returned nonzero; both codes are preserved |
| `FAILED <stage> …` | an earlier stage failed (package verification, controls, fixtures, smoke, smoke gate); later stages did not run |

A consumer must read the *word* (`DONE` or `FAILED`), not merely the presence of a terminal line. The process exit status follows the same rule. Every failure path also seals its terminal record.

## Dispositions (r4.1): three separate statements

`DONE` only says the computation finished. Gate 3 of the first r4 package showed why that is not enough: the harness and its gate passed, then custody refused to seal. `run_job.sh` therefore reports three dispositions, written **outside** the sealed directory to `JOB_DIR.disposition.txt`:

| Disposition | Meaning | Produced by |
|---|---|---|
| `COMPUTATION_COMPLETED: YES/NO` | the harness and the scientific gate completed (terminal `DONE`) | `run_job.sh` |
| `EVIDENCE_SEALED: YES/NO` | the manifest was published and then **read back** and found consistent with the files and the fixture pins | `seal_job.py` |
| `JOB_ACCEPTED: YES/NO` | the separately implemented `verify_job.py` passed **and** the terminal record is `DONE` | `verify_job.py` |

A verified `FAILED` job is an intact record of a failure: independent verification passes, `JOB_ACCEPTED` is `NO`. `DONE` alone must never be read as `JOB_ACCEPTED`. If a completed computation cannot be sealed, `run_job.sh` exits 20.

## Order of custody

1. write the terminal record (`DONE` or `FAILED`) and flush it
2. exit codes are already in `exit_codes.txt` (written before the terminal record)
3. `seal_job.py` writes `JOB_SHA256SUMS` over **every file in the job directory** except `holdout/`, **last**
4. nothing is written to the job directory afterwards

This corrects r3, whose runner appended its terminal line after generating the manifest (`STATUS_LOG_APPEND_AFTER_HASH`).
The sealer refuses to seal without a terminal `DONE`/`FAILED` record and refuses to overwrite an existing manifest.

## Fixtures: what is sealed and what is re-verified

`holdout/` (about 1.2 GB) is **not** hashed into `JOB_SHA256SUMS`. Two pin lists are copied into the job and so **are** sealed:

- `fixture_hashes.sha256`: the 36 frozen `.npy` hashes (unchanged since the equivalence package; list digest `d42892ec…0b63e41`);
- `generated_artifacts.sha256` (r4.1): the separately pinned generated artifact `index.json`, written by the frozen generator (digest `f52adb19…88eca3`, identical to the committed `synth521_holdout_index.json`).

`holdout/` must contain **exactly** the 37 pinned files. The sealer refuses to seal, and both verifiers refuse the job, on any missing, modified, duplicate, unexpected or non-regular entry. The generated index is also
cross-checked against the fixtures it claims to describe (every pinned `.npy` named once, none invented, unique integer seeds), because a hash-pinned index that contradicts its fixtures is not evidence of consistency.
(It is a structural check; it does not re-derive tone frequencies from the IQ.) When the sealer sits in a package, the sealed pins must equal the package's own copies. The first r4 package (`a7fd10b1…`) lacked the second pin and could not seal a real generated directory.

So the manifest proves the lists; the re-verification step proves the bytes.

## Independent verification

```
python3 tools/r4_job/seal_job.py JOB_DIR --verify [--require-fixtures]   # the sealer's own check
python3 tools/r4_job/verify_job.py JOB_DIR --require-fixtures            # separately implemented; pins from the package, not the job; prints the three dispositions
```

It prints two separate outcomes and never merges them:

- `JOB_MANIFEST: VERIFIED | FAILED` — every manifest entry matches and no file is unlisted.
- `FIXTURE_BYTES: VERIFIED_AGAINST_SEALED_HASH_LIST | NOT_PRESENT_NOT_REVERIFIED | FIXTURE_LIST_MISSING | FAILED`.

A fixture changed after sealing, deleted, or added yields `FIXTURE_BYTES: FAILED` and a nonzero exit, even though the manifest alone cannot see it (this is exercised by a regression test).
If the fixture bytes are not present (for example the job directory was copied without `holdout/`), the outcome is `NOT_PRESENT_NOT_REVERIFIED` — an explicit statement that the bytes were not re-verified —
and `--require-fixtures` makes that, and a missing list, a failure. Regenerate the fixtures from the frozen package and re-run with `--require-fixtures` for a full independent verification.

## Limits

The seal is a SHA-256 manifest, not a signature: it detects accidental or unrecorded change after sealing; it does not authenticate who sealed it. Authenticity of the package source is carried separately by the package's own checksum manifest.
