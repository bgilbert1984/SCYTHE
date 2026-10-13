# Capture-to-Compute Protocol v1

> **PROPOSED — NOT AUTHORIZED FOR DEPLOYMENT.**
> This is a design document. It authorizes no transfer of captured IQ, no account, key or
> service on any compute host, no installation, and no configuration change. Each of those needs
> its own explicit authorization after this design and its threat model have been reviewed.

## 1. Purpose and scope

Separate **acquisition** (the SCYTHE host, which owns the SDR and the capture authority) from
**analysis** (a compute host, initially Dedirock, which has more compute). The protocol moves an
approved, hash-verified capture to the compute host, runs a pinned one-shot analysis there, and
returns a verifiable result — without giving the compute host any control over acquisition and
without letting a completed job become an accepted scientific result by itself.

In scope: custody and integrity of a capture package, the transfer boundary, the job contract,
result verification, and the separation of transfer success from evidence admission.

Out of scope: signal classification, detector validation, scheduling or orchestration
infrastructure (no Kubernetes, Redis or queues in v1), and any live-streaming path.

Design principles carried over from the existing evidence discipline: record before you use,
refuse rather than guess, an unverified input is not evidence, and a passing run is not an
authorization.

## 2. Threat model

Protected: capture integrity, provenance claims, the SDR control path, and the evidence store.

| # | Threat | Control |
|---|---|---|
| T1 | Corruption or truncation in transit | sha256 + byte count verified at intake; truncation → `INCOMPLETE` |
| T2 | Substitution of a different file under the same job id | job ids are bound to the manifest digest; a replay with different bytes is refused (`JOB_ID_REPLAY`) |
| T3 | Modification of an input after verification | verified input is an immutable snapshot; the analysis identity cannot write to it (see §5.3) |
| T4 | Forged or altered provenance (wrong device, gain, time) | signed canonical manifest, key pinned out of band (§4.3). SHA256SUMS and SSH do not provide this |
| T5 | Compute host compromised or buggy | it holds no key that can sign captures, no credential that can push to SCYTHE, and no SDR control path; its results are untrusted until re-verified |
| T6 | Compromised analysis job escalating to upload/transfer credentials | transfer and analysis run as different identities; the analysis identity has no transfer credential |
| T7 | Result presented as accepted evidence automatically | admission is a separate authority (§9); no code path from `RESULT_VERIFIED` to admission |
| T8 | Silent environment drift changes results | result manifest records code commit, module hashes, package versions and executed FFT backend identity; a mismatch against the pin is a refusal |
| T9 | Stale or replayed manifest | manifest carries a capture id and monotonic issue time; intake refuses duplicates with different digests |
| T10 | Disk exhaustion or hostile file at intake | size cap declared in the manifest and enforced; intake directory is non-executable and not world-readable |

Not defended in v1: a malicious SCYTHE host or stolen signing key (detect by out-of-band key
fingerprint comparison and rotation, not by this protocol), physical access, and side channels.

## 3. Roles, identities and filesystem boundaries

SCYTHE is the **capture authority**; the compute host is **compute only**.

| Role | Host | Identity | May | May not |
|---|---|---|---|---|
| Capture authority | SCYTHE | `capture` | operate the SDR, write manifests, sign them, push approved packages | — |
| Transfer receiver | compute | `scythe-intake` (dedicated, restricted) | accept uploads into `incoming/` via a forced-command SFTP subsystem or equivalent restricted service | read other directories, run commands, read results, write `verified/` |
| Intake verifier | compute | `scythe-verify` | read `incoming/`, verify, publish to `verified/` | write signing keys, push anywhere |
| Analysis worker | compute | `scythe-worker` (unprivileged) | read `verified/`, write only its own `jobs/<job_id>/out/` | write `verified/` or `incoming/`, open network connections, hold any transfer or signing credential |
| Result retriever | SCYTHE | `capture` | pull `results/` over SSH read-only | — |

Directory layout on the compute host (illustrative):

```
incoming/<capture_id>/      scythe-intake: write-only; not executable; not readable by worker
verified/<capture_id>/      scythe-verify: write; scythe-worker: read-only; immutable once published
jobs/<job_id>/out/          scythe-worker: write; sealed (read-only) at job end
results/<job_id>/           scythe-verify: write (copy of sealed out + result manifest)
audit/                      append-only transition records
```

Network: the compute host accepts inbound SSH from SCYTHE only. It initiates no connection to
SCYTHE. SCYTHE never exposes a service to the compute host.

## 4. Capture package and manifest

### 4.1 Package

```
<capture_id>/
  capture.iq            raw bytes, exactly as recorded
  capture.manifest.json canonical JSON (RFC 8785-style: sorted keys, no insignificant whitespace, UTF-8)
  capture.manifest.sig  detached signature over the manifest bytes
```

### 4.2 Required manifest fields

A missing or empty required field is `FIELD_MISSING` and the package is refused.

| Field | Notes |
|---|---|
| `schema` | `scythe.capture-manifest.v1` |
| `capture_id` | unique, never reused for different bytes |
| `iq_file`, `iq_bytes`, `iq_sha256` | declared name, byte count, sha256 |
| `iq_format` | e.g. `u8-interleaved-iq` |
| `sample_rate_hz`, `center_frequency_hz`, `gain_db` | as applied, from the device banner where available |
| `receiver` | make, model, serial, tuner |
| `input_state` | e.g. `TERMINATED_50_OHM` or `ANTENNA:<id>`; no default |
| `captured_utc`, `captured_local` | start time of the capture |
| `capture_tool`, `capture_tool_version`, `scythe_commit` | what produced the bytes |
| `authorization_ref` | pointer to the decision authorizing this capture and its processing |
| `max_bytes` | intake size cap |
| `issued_utc`, `issuer` | manifest issue time and signing identity |
| `limits` | free-text statement of what the capture does not establish |

Metadata must be recorded at acquisition time, not reconstructed later.

### 4.3 Integrity and authenticity are separate claims

- **Integrity:** sha256 over the IQ and over the manifest, byte count equality. This detects
  corruption and substitution *relative to the manifest*. A `SHA256SUMS` file proves nothing about who wrote it.
- **Authenticity:** an Ed25519 signature over the canonical manifest, made by a capture-authority
  key held on the SCYTHE host (not on the compute host). The verifying public key is pinned on the
  compute host out of band (fingerprint compared by a human at provisioning and recorded).
  SSH authenticates the *endpoint*; it does not vouch for the provenance claims in the manifest.
- Intake verifies the signature **before** trusting any manifest field, including `iq_sha256`.
- Key compromise or rotation is handled by a new key fingerprint and a new pin; old captures
  remain verifiable against the key fingerprint recorded with them.

## 5. State machine

```
CAPTURED → SOURCE_VERIFIED → TRANSFER_STAGED → INTAKE_VERIFIED
        → JOB_COMPLETED → RESULT_RETURNED → RESULT_VERIFIED
        → ADMISSION_DECISION   (separate authority; see §9)
```

Any failure produces a refusal record and the state does **not** advance. A refused package
is never partially processed.

| State | Entered when | Evidence recorded |
|---|---|---|
| `CAPTURED` | bytes and manifest written at acquisition | manifest, device banner, local sha256 |
| `SOURCE_VERIFIED` | SCYTHE re-hashes the file, checks every required field, verifies its own signature | verification record |
| `TRANSFER_STAGED` | package fully uploaded into `incoming/` | transfer log: source and destination sizes and times |
| `INTAKE_VERIFIED` | signature valid, byte count and sha256 equal, fields present, id/digest not already bound to different bytes; snapshot published to `verified/` | intake record with computed digests |
| `JOB_COMPLETED` | one-shot job exited and its output directory was sealed | job record: exit status, timings |
| `RESULT_RETURNED` | SCYTHE pulled the result package | transfer log |
| `RESULT_VERIFIED` | SCYTHE recomputed result hashes and checked the result manifest against the input digest and the pin | verification record |
| `ADMISSION_DECISION` | an authorized reviewer accepts, rejects or defers | decision record, authority, reason |

Each transition appends one line to an append-only audit record containing UTC time, state,
`capture_id`, `job_id`, actor identity and the digests it relied on. The audit file is hashed
into a periodic checkpoint that SCYTHE holds.

## 6. Refusal vocabulary

| Code | Meaning |
|---|---|
| `MANIFEST_MISSING` | no manifest, or no signature |
| `SIGNATURE_INVALID` | signature does not verify against the pinned key |
| `FIELD_MISSING` | a required manifest field is absent or empty |
| `HASH_MISMATCH` | computed sha256 differs from the manifest |
| `INCOMPLETE` | byte count differs from `iq_bytes` or the upload did not finish |
| `SIZE_EXCEEDED` | larger than `max_bytes` |
| `JOB_ID_REPLAY` | `capture_id`/`job_id` already bound to a different digest |
| `INPUT_NOT_IMMUTABLE` | snapshot could not be made read-only |
| `PIN_MISMATCH` | code commit, module hashes or package versions differ from the pinned job definition |
| `OUTPUT_ESCAPE` | worker output outside its job directory |
| `RESULT_HASH_MISMATCH` | result files differ from the result manifest |
| `RESULT_INPUT_MISMATCH` | result manifest names a different input digest |
| `NOT_AUTHORIZED` | no valid `authorization_ref` for this capture and processing |

A refusal is a recorded outcome, not an error to retry silently.

## 7. Transfer workflow (SCYTHE-initiated push)

1. SCYTHE selects an approved capture; the operator or an authorized policy approves its release. Custody leaves SCYTHE only on this approval.
2. `SOURCE_VERIFIED` is recorded.
3. SCYTHE pushes the package to `incoming/<capture_id>.partial/` as `scythe-intake` (forced-command SFTP or equivalent), then renames it to `incoming/<capture_id>/` only when complete, so a partial upload is never visible as a finished one.
4. Intake verification (§5) runs as `scythe-verify`.
5. On success the verifier publishes an **immutable snapshot** into `verified/<capture_id>/`.
   Plain filesystem write permission is not a write-once guarantee, so publication must do all of:
   copy (or hard-link) from a source the uploader can no longer write to; hash the *published* bytes
   and compare with the manifest; make the published files non-writable for every identity
   including the owner (read-only bind mount, immutable attribute, or equivalent); and record the
   published inode/device and digest. Later upload attempts to the same `capture_id` cannot change what a job reads.
6. A job's identity is `(job_id, input_sha256, job_definition_sha256)`. Re-using a `job_id`
   with different bytes or a different definition is `JOB_ID_REPLAY`.

## 8. One-shot execution contract

The analysis worker runs a single job definition (pinned file, recorded by digest):

- inputs: exactly one verified capture, referenced by `capture_id` and re-hashed immediately before and after the run (a change is `INPUT_NOT_IMMUTABLE`);
- code: a named commit and the sha256 of every module the job imports from SCYTHE, checked against the pin before the run (`PIN_MISMATCH` otherwise);
- environment: Python and package versions recorded, including the **executed** FFT callable's module and function names (not the runner's self-reported `fft_backend`, which has been shown to be uninformative);
- parameters: explicit, recorded, with the frozen tolerances and thresholds unchanged;
- network off, no outbound credentials, no write access outside `jobs/<job_id>/out/`, bounded CPU/memory/time;
- exits when done; no daemon, no retry loop.

### Result package

```
results/<job_id>/
  out/...               analysis outputs
  result.manifest.json  input_sha256, capture_id, job_definition_sha256, code commit, module hashes,
                        package versions, executed FFT callable identity, parameters, start/end UTC,
                        exit status, sha256 and size of every output file
  result.sha256         digests of the above
```

## 9. Return, verification and admission

SCYTHE pulls `results/<job_id>/` read-only, then independently recomputes every output hash,
checks `input_sha256` against its own `CAPTURED` record, and checks the pin fields. Only then is
`RESULT_VERIFIED` recorded.

**Transfer success is not classification and not admission.** `RESULT_VERIFIED` means the result
is authentic and intact, nothing more. Admission into the SCYTHE evidence store is a separate
decision by a separate authority and must cite the method's validation status; a result from an
unvalidated or shadow-mode method (for example the symbol-clock detectors) stays labelled
shadow regardless of how cleanly it transferred. There is no automatic path from `RESULT_VERIFIED`
to admission, and a test asserts that none exists.

## 10. Acceptance tests — first fixture

**Fixture:** the retained, terminated-input XTR baseline capture `f400.iq`
(2026-10-11, Nooelec SMArt XTR, 400 MHz, 2,048,000 S/s, 29.0 dB, 245,760,000 bytes,
sha256 `bb346a376eee9d12d55b1f62119fa520d0a8b41fb89322a295008765035a0c5b`), with a manifest built from the
existing provenance record. 400 MHz is an operational choice, not a claim that it is better suited to
transport testing. These tests exercise transport and custody only.

| # | Test | Expected |
|---|---|---|
| A1 | Exact IQ and manifest transferred | `INTAKE_VERIFIED` |
| A2 | Source and destination sha256 equal | PASS |
| A3 | Byte count equals `iq_bytes` | PASS |
| A4 | Manifest absent | `MANIFEST_MISSING`, no state advance |
| A5 | Signature absent or invalid | `SIGNATURE_INVALID` |
| A6 | One IQ byte flipped | `HASH_MISMATCH` |
| A7 | Upload truncated | `INCOMPLETE` |
| A8 | A required field removed (each field, in turn) | `FIELD_MISSING` |
| A9 | Same `job_id`, different bytes | `JOB_ID_REPLAY` |
| A10 | Worker attempts to modify the verified input | denied by the filesystem; input digest unchanged |
| A11 | Worker attempts to write outside its output directory, or to open a network connection | denied |
| A12 | Re-upload under the same `capture_id` after publication | does not change the input a job reads |
| A13 | Job pin differs from the recorded pin | `PIN_MISMATCH` |
| A14 | Result retrieved; result hashes recomputed | `RESULT_VERIFIED` |
| A15 | Result file altered after the job | `RESULT_HASH_MISMATCH` |
| A16 | Result manifest names a different input digest | `RESULT_INPUT_MISMATCH` |
| A17 | Completed, verified job | **no** admission record created |
| A18 | Compute host attempts any connection to the SCYTHE host or SDR | impossible (no credential, no route) |

The first analysis is deliberately trivial and read-only — byte count, sha256 and a bounded
spectrum summary — so a failure is attributable to transport, not to detector behaviour. Negative
tests (A4–A9, A12, A15, A16) must be run against the verifier as a *control*, as the evidence comparators
now are: a verifier that cannot be made to refuse has not been tested.

## 11. Open questions for review before any authorization

1. Immutable publication mechanism on the compute host (read-only bind mount vs immutable attribute vs a snapshot-capable filesystem); this depends on what the host supports and who may administer it.
2. Signing-key custody and rotation on SCYTHE, and how the fingerprint is conveyed out of band.
3. Whether `authorization_ref` is checked mechanically (against a signed authorization list) or by a human at release.
4. Retention and deletion of verified inputs on the compute host (default: delete on instruction only, never on a timer).
5. Size and rate limits, and whether the compute host's disk may hold more than one 245 MB capture at a time.
6. Clock trust: manifest times are asserted by SCYTHE; whether the compute host should add its own receive time (it does, in the audit record) and how skew is flagged.

## 12. Authorization status

| Item | Status |
|---|---|
| This design document | proposed, for review |
| Any transfer of IQ to a compute host | **not authorized** |
| Accounts, keys, services or configuration on a compute host | **not authorized** |
| Software installation on a compute host | **not authorized** |
| Fixture-based tests in §10 | defined only; not run |
| Automatic evidence admission | **prohibited by design** |
