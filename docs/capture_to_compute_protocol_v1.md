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
| T3 | Modification of an input after verification | verified input lives in a verifier-owned, content-addressed directory exposed to the worker only through a read-only bind mount; the bytes are re-hashed before and after the run (§7). A privileged host administrator is outside this control (§2.1) |
| T4 | Forged or altered provenance (wrong device, gain, time) | signed canonical manifest, key pinned out of band (§4.3). SHA256SUMS and SSH do not provide this |
| T5 | Compute host compromised or buggy | it holds no key that can sign captures, no credential that can push to SCYTHE, and no SDR control path. Its results are verifiable *as returned artifacts* but are not thereby trusted computations (§9.1) |
| T6 | Compromised analysis job escalating to upload/transfer credentials, or exfiltrating data | transfer and analysis run as different identities; the analysis identity has no transfer credential and is subject to OS-enforced egress denial (§8) |
| T7 | Result presented as accepted evidence automatically | admission is a separate authority (§9); no code path from `RESULT_VERIFIED` to admission |
| T8 | Silent environment drift changes results | result manifest records code commit, module hashes, package versions and executed FFT backend identity; a mismatch against the pin is a refusal |
| T9 | Stale or replayed manifest | manifest carries a capture id and monotonic issue time; intake refuses duplicates with different digests |
| T10 | Disk exhaustion or hostile file at intake | size cap declared in the manifest and enforced; intake directory is non-executable and not world-readable |

Not defended in v1: a malicious SCYTHE host or stolen signing key (handled by revocation and rotation, §4.4, not prevented by this protocol), physical access, and side channels.

### 2.1 Recorded trust limitations

- A read-only bind mount enforces the *worker* boundary. It is not a guarantee against modification by a privileged administrator or a compromised kernel on the compute host. Rehashing before and after a job detects such a change after the fact; it does not prevent it.
- Hash verification shows that bytes arrived intact. It does not show that the computation that produced a result was honest (§9.1).

## 3. Roles, identities and filesystem boundaries

SCYTHE is the **capture authority**; the compute host is **compute only**.

| Role | Host | Identity | May | May not |
|---|---|---|---|---|
| Capture authority | SCYTHE | `capture` | operate the SDR, write manifests, request signatures, push approved packages | read the signing private key |
| Signing service | SCYTHE | `scythe-sign` (dedicated) | read the Ed25519 private key and sign canonical manifests and authorization records | be readable by, or share a key with, the transfer or analysis identities |
| Transfer receiver | compute | `scythe-intake` (dedicated, restricted) | accept uploads into `incoming/` via a forced-command SFTP subsystem or equivalent restricted service | read other directories, run commands, read results, write `verified/` |
| Intake verifier | compute | `scythe-verify` | read `incoming/`, verify, publish to `verified/` | write signing keys, push anywhere |
| Analysis worker | compute | `scythe-worker` (unprivileged) | read `verified/`, write only its own `jobs/<job_id>/out/` | write `verified/` or `incoming/`, open network connections, hold any transfer or signing credential |
| Result retriever | SCYTHE | `capture` | pull `results/` over SSH read-only | — |

Directory layout on the compute host (illustrative):

```
incoming/<capture_id>/      scythe-intake: write-only; not executable; not readable by worker
verified/sha256/<digest>/   scythe-verify: write; scythe-worker: read-only (bind mount); content-addressed
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
  capture.manifest.json manifest, stored as its RFC 8785 (JCS) canonical form
  capture.manifest.sig  detached Ed25519 signature over the signing bytes (§4.3)
  authorization.json    signed authorization record for this release (§4.5)
```

### 4.2 Required manifest fields

A missing or empty required field is `FIELD_MISSING` and the package is refused. Because JCS requires I-JSON, integers must lie within ±(2^53 − 1); a quantity that can exceed that (e.g. a byte count) is carried as a decimal string, and the schema states which fields are strings.

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
| `signature_key` | `{key_id, algorithm: "Ed25519", fingerprint}` of the signing key (§4.4) |
| `provenance` | per-field map: each field is `RECORDED_AT_ACQUISITION` or `IMPORTED_HISTORICAL` with its source (§10.1); `signed_at_acquisition` is a boolean and must be truthful |
| `limits` | free-text statement of what the capture does not establish |

Metadata must be recorded at acquisition time, not reconstructed later.

### 4.3 Integrity and authenticity are separate claims

- **Integrity:** sha256 over the IQ, and byte count equality. This detects corruption and
  substitution *relative to the manifest*. A `SHA256SUMS` file proves nothing about who wrote it.
- **Authenticity:** an Ed25519 signature made on SCYTHE by `scythe-sign`. SSH authenticates the
  *endpoint*; it does not vouch for the provenance claims in the manifest.
- **Signing bytes (exact):** `S = ASCII("scythe.capture-manifest.v1\n") || JCS(M)`, where `M` is the
  manifest JSON object and `JCS(M)` is its UTF-8 encoding under RFC 8785 (JSON Canonicalization
  Scheme) with the signature file excluded. The prefix is a domain separator so a manifest signature
  cannot be replayed as any other signed object. The signature is `Ed25519(sk, S)`. The same
  construction, with prefix `scythe.authorization.v1\n`, signs authorization records (§4.5). A
  verifier recomputes `JCS(M)` from the parsed object and rejects a file whose bytes are not
  already canonical, so there is exactly one byte string to verify.
- Intake verifies the signature **before** trusting any manifest field, including `iq_sha256`.

### 4.4 Signing-key custody, registry and rotation

- A dedicated Ed25519 capture-manifest key exists on the SCYTHE acquisition host. The private key
  never leaves that host and is readable only by `scythe-sign`, not by the transfer or analysis
  identities, and not on the compute host at all.
- Each signed manifest records `key_id`, algorithm and fingerprint (`SHA-256` of the raw public key).
- The compute host pins public keys through a **key registry** provisioned over an independently
  authenticated channel (fingerprint read and compared by a person at provisioning; the comparison is recorded).
  Each registry entry carries `key_id`, algorithm, fingerprint, public key, `active_from`,
  `retired_on` (optional) and `status` ∈ {`ACTIVE`, `RETIRED`, `REVOKED`}.
- **Rotation** is an explicit, audited registry update with activation and retirement dates. Old
  public keys are kept so historical manifests stay verifiable. A manifest signed with a key that
  was not `ACTIVE` at `issued_utc`, or that is unknown, is refused (`KEY_UNKNOWN`).
- **Revocation** is an explicit audited registry update. Manifests signed by a `REVOKED` key are
  refused (`KEY_REVOKED`) unless an authorized reviewer re-signs them under a new key after review.
  A suspected compromise triggers revocation first and investigation second.

### 4.5 Authorization record

`authorization_ref` is not a pointer to a document; it resolves to a signed, verifiable
**authorization record** that binds exactly:

| Field | Meaning |
|---|---|
| `capture_id`, `iq_sha256` | the specific capture and its digest |
| `destination` | the compute host identity the release is approved for |
| `purpose` | the approved use (e.g. `TRANSPORT_TEST`, `RETAINED_IQ_REANALYSIS`) |
| `job_definition_sha256` | the exact analysis-job definition approved |
| `approver`, `approved_utc`, `not_before`, `expires` | a named authorized reviewer and the validity window |
| `key_id`, signature | signed with the same construction as §4.3 |

A named authorized reviewer approves the release of that capture digest, to that destination, for
that purpose, with that job definition. The system then enforces the authorization **mechanically
at source release and again at destination intake**: any field that does not match the package,
the destination or the job, or a record that is absent, expired, not yet valid, revoked or
signed by an unknown key, yields `NOT_AUTHORIZED`. Human approval decides; the machine only
refuses to proceed without a matching record.

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
| `INTAKE_VERIFIED` | signature valid, byte count and sha256 equal, fields present, id/digest not already bound to different bytes; input published to the content-addressed `verified/` directory | intake record with computed digests |
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
| `SIGNATURE_INVALID` | signature does not verify, or the manifest bytes are not canonical JCS |
| `KEY_UNKNOWN` | signing key absent from the registry or not `ACTIVE` at `issued_utc` |
| `KEY_REVOKED` | signing key is `REVOKED` |
| `FIELD_MISSING` | a required manifest field is absent or empty |
| `HASH_MISMATCH` | computed sha256 differs from the manifest |
| `INCOMPLETE` | byte count differs from `iq_bytes` or the upload did not finish |
| `SIZE_EXCEEDED` | larger than `max_bytes` |
| `JOB_ID_REPLAY` | `capture_id`/`job_id` already bound to a different digest |
| `INPUT_NOT_IMMUTABLE` | published input could not be exposed read-only, or its digest changed between the pre-run and post-run re-hash |
| `PIN_MISMATCH` | code commit, module hashes or package versions differ from the pinned job definition |
| `OUTPUT_ESCAPE` | worker output outside its job directory |
| `RESULT_HASH_MISMATCH` | result files differ from the result manifest |
| `RESULT_INPUT_MISMATCH` | result manifest names a different input digest |
| `UPLOAD_RESUME_MISMATCH` | resumed upload does not match the stored prefix digest, capture ID or expected object digest |
| `COMPLETED_OBJECT_REPLACEMENT` | a resumed or repeated upload attempted to replace an already-published object |
| `CLOCK_SKEW_EXCEEDED` | source clock uncertainty or source-versus-receiver skew exceeds the declared bound |
| `RESULT_ACK_MISSING` | results exist but SCYTHE has not recorded a verified retrieval acknowledgment |
| `NOT_AUTHORIZED` | authorization record absent, expired, not yet valid, revoked, unknown-key, or its capture digest, destination, purpose or job-definition digest does not match |

A refusal is a recorded outcome, not an error to retry silently.

## 7. Transfer workflow (SCYTHE-initiated push)

1. SCYTHE selects an approved capture; the operator or an authorized policy approves its release. Custody leaves SCYTHE only on this approval.
2. `SOURCE_VERIFIED` is recorded.
3. SCYTHE pushes the package to `incoming/<capture_id>.partial/` as `scythe-intake` (forced-command SFTP or equivalent), then renames it to `incoming/<capture_id>/` only when complete, so a partial upload is never visible as a finished one.
4. Intake verification (§5) runs as `scythe-verify`.
5. On success the verifier publishes the input into a **verifier-owned, content-addressed
   directory**, `verified/sha256/<iq_sha256>/`, which `scythe-intake` and `scythe-worker` cannot write.
   The verifier re-hashes the *published* bytes and compares them with the manifest. The worker sees
   the input only through a **read-only bind mount** into its sandbox, and has no write access to the
   underlying storage. The same bytes are re-hashed immediately before and after the job. v1
   deliberately does not rely on filesystem snapshots or immutable attributes (`chattr +i`), which add
   filesystem-specific behavior and operational complexity. Because the directory is addressed by its
   content hash, a later upload cannot change which bytes a given digest names. Limitation: this
   enforces the worker boundary, not protection against privileged host modification (§2.1).
6. A job's identity is `(job_id, input_sha256, job_definition_sha256)`. Re-using a `job_id`
   with different bytes or a different definition is `JOB_ID_REPLAY`.

## 8. One-shot execution contract

The analysis worker runs a single job definition (pinned file, recorded by digest):

- inputs: exactly one verified capture, referenced by `capture_id` and re-hashed immediately before and after the run (a change is `INPUT_NOT_IMMUTABLE`);
- code: a named commit and the sha256 of every module the job imports from SCYTHE, checked against the pin before the run (`PIN_MISMATCH` otherwise);
- environment: Python and package versions recorded, including the **executed** FFT callable's module and function names (not the runner's self-reported `fft_backend`, which has been shown to be uninformative);
- parameters: explicit, recorded, with the frozen tolerances and thresholds unchanged;
- **OS-enforced egress denial** for the worker identity, e.g. a network namespace with no routable interface, or a default-drop firewall rule scoped to that uid; absence of credentials alone is not isolation. The configuration is part of the recorded job environment; no write access outside `jobs/<job_id>/out/`; bounded CPU/memory/time;
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

### 9.1 Result integrity is not computational trust

A verified hash shows the returned bytes are the bytes the compute host produced and that the
result manifest is consistent. It does not show that a compromised or faulty compute host performed
an honest calculation. A result is therefore verifiable *as an artifact*; whether it can be relied
on as a calculation depends on further controls: re-running on a second host (as the Dedirock
equivalence work did for the synthetic fixtures), independent reanalysis of a sample, and pinned
environments. Until such a control has been applied to a given result, the result record says so.

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
| A11 | Worker attempts to write outside its output directory, or to open a network connection | denied by the configured filesystem and egress controls |
| A12 | Re-upload under the same `capture_id` after publication | does not change the input a job reads |
| A13 | Job pin differs from the recorded pin | `PIN_MISMATCH` |
| A14 | Result retrieved; result hashes recomputed | `RESULT_VERIFIED` |
| A15 | Result file altered after the job | `RESULT_HASH_MISMATCH` |
| A16 | Result manifest names a different input digest | `RESULT_INPUT_MISMATCH` |
| A17 | Completed, verified job | **no** admission record created |
| A18 | Compute host attempts any connection to the SCYTHE host or SDR | refused: no credential and no configured route. This demonstrates the configured isolation; it does not prove connections are categorically impossible |
| A22 | Upload dropped mid-transfer, then resumed | prefix verified before continuing; completes with matching digest |
| A23 | Resume attempt whose prefix differs from the stored prefix | `UPLOAD_RESUME_MISMATCH`; partial quarantined |
| A24 | Second upload (resumed or fresh) for an already-published capture ID with different bytes | `COMPLETED_OBJECT_REPLACEMENT`; published object unchanged |
| A25 | Source offline when the job finishes | results retained; no outbound attempt by the compute host; retrieved and acknowledged later |
| A26 | SSH transfer of results completes but no acknowledgment is recorded | package stays unacknowledged (`RESULT_ACK_MISSING`); not deleted |
| A27 | Receipt with a bad signature, or naming a different digest | source treats the upload as unconfirmed |
| A28 | Signing key reported lost; revocation initiated without the device | key `REVOKED`; later-signed packages `KEY_REVOKED` and quarantined |
| A29 | Source clock uncertainty or skew above the declared bound | `CLOCK_SKEW_EXCEEDED` recorded; timestamp not altered |
| A19 | Authorization record absent, expired, revoked, or with a mismatched digest / destination / purpose / job definition | `NOT_AUTHORIZED` at source release and again at intake |
| A20 | Manifest signed by an unknown, retired-before-issue, or revoked key | `KEY_UNKNOWN` / `KEY_REVOKED` |
| A21 | Manifest bytes not in canonical JCS form (same content, different whitespace or key order) | `SIGNATURE_INVALID` |

### 10.1 Historical provenance of the fixture

The October 11 XTR capture **predates** this protocol and was not signed at acquisition. Its test
manifest must say so. Fields taken from the recorded provenance (`MANIFEST.md`, the
`<file>.iq.sha256` sidecar, the `.err` device banner, and the evidence commit `4ae886d`) are marked
`IMPORTED_HISTORICAL` with that source; only fields genuinely produced at packaging time
(`issued_utc`, `issuer`, `signature_key`, `authorization_ref`, `max_bytes`) are marked as such.
`signed_at_acquisition` is `false`, and the signature attests only that the named issuer imported
the recorded metadata at `issued_utc` and that the bytes match the recorded digest — **not** that
the capture was signed or attested when it was made. A newly signed manifest must never imply otherwise.

The first analysis is deliberately trivial and read-only — byte count, sha256 and a bounded
spectrum summary — so a failure is attributable to transport, not to detector behaviour. Negative
tests (A4–A9, A12, A15, A16) must be run against the verifier as a *control*, as the evidence comparators
now are: a verifier that cannot be made to refuse has not been tested.

## 11. Design decisions and remaining questions

Resolved for v1 (design level):

1. **Input immutability:** verifier-owned content-addressed directory, read-only bind mount into an unprivileged sandbox, re-hash before and after; no reliance on snapshots or `chattr +i`; privileged-host limitation recorded (§2.1, §7).
2. **Signing keys:** dedicated Ed25519 key on SCYTHE, readable only by `scythe-sign`; `key_id`, algorithm and fingerprint in every manifest; audited key registry with activation/retirement dates; explicit revocation (§4.4).
3. **Authorization:** human release approval plus mechanical verification at source release and at intake, against a signed record binding capture digest, destination, purpose and job definition (§4.5).

Still open:

4. Retention and deletion of verified inputs on the compute host (default: delete on instruction only, never on a timer).
5. Size and rate limits, and whether the compute host may hold more than one 245 MB capture at a time.
6. Clock trust: manifest times are asserted by SCYTHE; the compute host adds its own receive time in the audit record; how skew is flagged.
7. Who the authorized reviewers are, how they are enrolled, and how their keys are managed (the authorization key may be distinct from the capture-manifest key).
8. Which second control (second host re-run, sampled independent reanalysis) is required before a returned result is treated as a trusted calculation (§9.1).
9. The declared bounds for clock uncertainty and source-versus-receiver skew (§12.5), and whether a breach refuses or quarantines.
10. How the retrieval acknowledgment key is held relative to the signing key, given that both live on a travelling device.


## 12. Field mobility (mobile acquisition host)

The acquisition host (`scythe-1`) is a mobile workstation that is powered on only during sessions: it
takes samples, pushes them, and then moves or powers down while the compute host works. v1 therefore
cannot assume the source is online when intake refuses a package or when a job finishes. This section
adds the required behavior; it does not change the trust boundary of §3 (the compute host still has no
credential for, and no route to, the acquisition host).

| Condition | Required v1 behavior |
|---|---|
| Source temporarily offline | The compute host retains completed results **and** refusal records durably in `results/` for later SCYTHE-initiated retrieval. It never pushes them. |
| Source custody | The source deletes nothing until the result is `RESULT_VERIFIED` **and** an explicit release authorization is recorded. |
| Interrupted upload | Uploads are resumable and integrity-checked, with atomic publication (§12.3). |
| Mobile signing-key exposure | Full-disk encryption, a protected signing identity, and an explicit compromise response (§12.4). |
| Clock and location | Clock uncertainty is recorded and bounded; location data is minimized and access-controlled (§12.5). |

### 12.1 Intake receipt

Upload completion is not intake. Before the source leaves a location it should wait for an
`INTAKE_VERIFIED` **receipt**, which contains: the capture ID, the capture (IQ) digest, the manifest
digest, the receiving host's identity (its registered key ID), the verification time, and an
**authenticated acknowledgment** (a signature by the receiving host over the canonical receipt bytes,
constructed like §4.3 with prefix `scythe.intake-receipt.v1\n`). The source verifies that signature
against a pinned intake-host key. A receipt proves that an intake copy passed verification at that
moment. It is **not** proof that the only remaining copy will survive: the acquisition copy is kept
until the retention and release policy permits deletion. If no receipt arrives, the source treats the
upload as unconfirmed and keeps retrying while it is online.

### 12.2 Offline results and refusal records

Result and refusal packages are stored durably on the compute host. SCYTHE pulls them when it is next
online, recomputes every hash (§9), and then writes a **retrieval acknowledgment** (signed by the
SCYTHE authorization or release key) that names the result digest and the retrieval time. The compute
host records the acknowledgment and only then marks the package `RETRIEVED_ACKNOWLEDGED`. It must not
infer receipt from an SSH or SFTP transfer having finished, and it must not delete or garbage-collect
an unacknowledged package (`RESULT_ACK_MISSING` is the state of a package that has none). Retention and
deletion on the compute host remain explicit, two-step and receipted (§11).

### 12.3 Resumable uploads

A resumable upload is bound to one capture ID and one expected object digest (both taken from the
signed manifest). Chunks are written into `incoming/<capture_id>.partial/` with a running prefix digest
recorded by the receiver. On reconnect the receiver reports the length and prefix digest it holds, the
sender verifies that prefix against its own bytes, and only then continues; any mismatch is
`UPLOAD_RESUME_MISMATCH` and the partial is quarantined, never repaired in place. Publication is a single
atomic rename after the full digest verifies. A completed object is never replaced: a later upload, whether
resumed or fresh, for an already-published capture ID is `COMPLETED_OBJECT_REPLACEMENT` unless the bytes are
identical (idempotent re-upload is acknowledged without rewriting).

### 12.4 Mobile signing key

A laptop that travels can be lost or stolen while unlocked, so encryption at rest is necessary but not
sufficient. v1 requires: full-disk encryption on the acquisition host; a **passphrase-protected** signing
identity (not an unlocked key file); **short-lived authorization records** (§4.5 `expires` kept short, so a
stolen key cannot authorize releases for long); and a documented **compromise response** that can be
initiated **independently of the missing device**, by the registry administrator over the out-of-band
channel: (1) mark the key `REVOKED` in the registry, effective from a stated time; (2) quarantine every
capture signed under that key since the last known-good time (`KEY_REVOKED`); (3) re-verify quarantined
packages by independent means and re-sign under a new key only after review; (4) record the incident
and the new key's activation. The device's loss does not require the device's cooperation to revoke.

### 12.5 Clock and location

The manifest records the **clock source and uncertainty** (for example NTP/GPS-disciplined or free-running,
time of last synchronization, estimated uncertainty). The receiver records its own receipt time in the audit
record. When the source's asserted time and the receiver's time differ by more than a declared bound, or the
source's declared uncertainty exceeds it, intake records `CLOCK_SKEW_EXCEEDED` (a refusal or a quarantine, by
policy), and the timestamp is never silently "corrected".

Precise GPS coordinates are **not mandatory** in generic RF capture manifests. If a capture's purpose requires
them, they go in a **separate, protected location record** linked from the manifest only by its digest and
disclosed to authorized roles only; the compute job does not receive it unless the job definition and the
authorization record name it. The default manifest carries no location beyond what the purpose declares.

## 13. Authorization status

| Item | Status |
|---|---|
| This design document | proposed, for review |
| Any transfer of IQ to a compute host | **not authorized** |
| Accounts, keys, services or configuration on a compute host | **not authorized** |
| Software installation on a compute host | **not authorized** |
| Fixture-based tests in §10 | defined only; not run |
| Signing keys, key registry, authorization records | design only; none created |
| Field-mobility behaviors (§12) | design only; nothing implemented or deployed |
| Automatic evidence admission | **prohibited by design** |
