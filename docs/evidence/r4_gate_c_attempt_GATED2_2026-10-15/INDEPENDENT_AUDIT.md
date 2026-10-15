# Independent audit of the reported PASS

This audit re-derives its conclusions directly from the retrieved files in this directory
(`PREFLIGHT_RECORD.json`, `PREFLIGHT_RECORD.txt`, `PREFLIGHT_SHA256SUMS`), not from the live
console transcript alone — the same discipline the owner required of the Gate D monitor
rehearsal's `independent_verify_binding()` (PR #177), applied here to a Gate C record.

## 1. Checksum verification (independently recomputed, not copied from any prior claim)

```
$ sha256sum PREFLIGHT_RECORD.json PREFLIGHT_RECORD.txt
9a1cba494432f4c3f63119e37cf9085b57b1ccef8413879228cee561c8f4ab60  PREFLIGHT_RECORD.json
03e90882bd016a44a22c2ea1e680ab3420a71f382e816f09da11f08bc8648739  PREFLIGHT_RECORD.txt
```
Both equal the digests `PREFLIGHT_SHA256SUMS` itself records for them — confirmed again here,
independently of the verification already performed immediately after retrieval.

## 2. Field-by-field audit of `PREFLIGHT_RECORD.json`

Re-derived programmatically from the file as committed, not assumed from the console output:

| Check | Result |
|---|---|
| `verdict` field | `"PASS"` |
| Number of items in `items[]` | 16 |
| All 16 items have `status: "PASS"` | **True** — no item was omitted, truncated, or silently downgraded between the live console output and the written record |
| `attempt_label` | `"GATED2-20261015T005128Z-1b0b45"` — matches the authorized attempt |
| `preflight_mode` / `launch` | `"CHECK_ONLY"` / `"NOT_PERFORMED"` — confirms no launch occurred |
| `package_sha256` | equals the frozen package digest `2b2e3251...` (`docs/evidence/r4_1_packaging_provenance_2026-10-13/package_2b2e3251/PACKAGE_RECORD.txt`) |
| `requirements.run_job_sha256` | equals `02453e0d2b29dd635b0389373f851886293c0c8b37ec431538e01856ffa22d20` — **independently cross-checked** against `docs/research/metamorphic_r4_full_run_declaration.md` (line 16) and the locally-executed `docs/evidence/r4_full_local_2026-10-13/job/results_full/run_header.json`'s own recorded runner digest. Identical. |
| `requirements.harness_sha256` | equals `1ac4fe34260f6ba63056ed7dbf13f604d9b3f53f8ac77908f4c1ad052eac6687` — **independently cross-checked** against the same declaration document (line 17) and both `run_header.json` files from the actual local full run. Identical. This is a materially useful finding: the remote host's `CHECK_ONLY` is checking the replay package against the *same* harness and runner digests the local reference run actually executed, not a different or drifted pair. |
| `operator_declaration.sha256` (inside the record) | equals `9c8f893eb6eb666348963c35b14ba3271d5cd1f45a58d68cac94a49af4e1ad33` — the exact digest the operator confirmed before S2 ran. The live host's own check agrees with what was confirmed beforehand; nothing was substituted in transit. |
| `items[]` text, name-for-name and detail-for-detail | Compared directly against `COMMAND_TRACE.md`'s transcription of the live S4 console output — identical, including punctuation and ordering. |

No discrepancy was found between what was reported live during execution and what the retrieved,
digest-verified record actually contains.

## 3. What this audit does not establish

It establishes that this `CHECK_ONLY` record is authentic, internally consistent, and consistent
with independently-sourced digests already committed elsewhere in this repository. It does **not**
establish that the authorization preceding this attempt met the project's own bar for a reviewed,
bounded authorization — see `GOVERNANCE_NOTE.md` for that separate question, which this audit
deliberately does not answer. It also does not evaluate whether 5.2 GB of available memory is
sufficient headroom for the full replay; see `DEDIROCK_ENVIRONMENT_SNAPSHOT.md` for that
measurement, carried forward without characterizing it as a prediction of peak runtime demand, per
the owner's own instruction.
