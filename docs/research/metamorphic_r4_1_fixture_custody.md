# Metamorphic harness r4.1 — fixture-custody correction

> **THE r4 EXPERIMENT HAS NOT BEEN EXECUTED. This change is NOT authorized to run anything on another host.** No production detector, retention, protocol, relation, mutant definition, fixture hash or merged evidence is modified.

## 1. Why r4.1 exists: the failed Gate 3 attempt (preserved, not repaired)

A one-fixture packaged validation of the first frozen r4 package was run locally. Its disposition, as recorded:

```
ATTEMPT: R4_GATE3_PACKAGE_A7FD10B1   (package sha256 a7fd10b1c43dde66d60804c3f23209b4b37e7129abc99cd6dc4f3c6cba9efb7c, source f2b92f6fbe078ff718193d6280387af0739c91ed)
DISPOSITION: FAILED_PACKAGED_CUSTODY_INTEGRATION
CONTROLS: PASS (68 tests)          FIXTURE_HASHES: 36/36
IDENTITY_AND_NAMESPACE: VERIFIED   BOUNDARY_GATE: PASS (--limit 1 --boundary-only)
JOB_SEALING: FAILED                ROOT_CAUSE: UNPINNED_GENERATED_INDEX_JSON
SCIENTIFIC_ACCEPTANCE: NOT_ASSIGNED   GATE3: FAIL
```

The frozen generator writes `holdout/index.json` next to the 36 `.npy` fixtures. The fixture hash list pins only the 36, and the sealer (correctly, failing closed) refuses a fixture directory containing a file the list does not name.
The first r4 tests used a stub interpreter whose fake generator wrote only fixtures, so no test combined the real generator with the real sealer. Diagnostic copies (`index.json` deleted from a copy; one fixture byte flipped; a late `STATUS.txt` append; a deliberate preflight failure) behaved as designed, which isolates the cause; the deletion is a diagnostic, not the repair.
The original job directory and package are retained unchanged as a historical failed artifact.

## 2. The correction (pin, do not ignore)

| Item | r4 | r4.1 |
|---|---|---|
| Pins | `fixture_hashes.sha256` (36 `.npy`) | unchanged, **plus** `tools/r4_job/generated_artifacts.sha256` pinning `index.json` = `f52adb1974dce642bc62bf748add6733036db1cbc7bcba482691a221ad88eca3` |
| `holdout/` contents | every listed file; no extras | **exactly 37** pinned files; missing / modified / duplicate / unexpected / non-regular entries refused |
| Index | not checked | cross-checked against the pinned `.npy` set (named once each, none invented, unique seeds) |
| Package authority | job's copy of the list | the sealed pins must equal the package's own copies |
| Sealing | writes manifest | writes, **reads back**, prints `EVIDENCE_SEALED` (not acceptance) |
| Verification | `seal_job.py --verify` | plus `verify_job.py`, a separate implementation using the coreutils `sha256sum`, pins taken from the package, printing `INDEPENDENT_VERIFICATION`, `TERMINAL_STATUS`, `JOB_ACCEPTED` |
| Dispositions | `DONE` / `FAILED` only | `COMPUTATION_COMPLETED`, `EVIDENCE_SEALED`, `JOB_ACCEPTED` reported separately; `DONE` is never acceptance |

**Pinned index digest and determinism.** The generator is unchanged. Its `index.json` is plain `json.dump` output of values defined in the frozen spec; it is byte-identical to the committed `synth521_holdout_index.json` of the merged equivalence package (the pin equals that file's digest) and to the index the failed Gate 3 run regenerated on this workstation. A Dedirock-side regeneration was not performed (no execution there is authorized); the fixtures it produced earlier matched the same 36 hashes. Cross-host index equality is therefore expected but not demonstrated here.

## 3. Regression tests (`test_rf_metamorphic_r4_job.py`)

`RealGeneratorIntegration` runs the **real** frozen generator once (about 40 s, 1.2 GB temporary) and then, on that directory, the real sealer and the separate verifier:

| Control | Result |
|---|---|
| real generator → 36 fixtures + index | PASS |
| existing 36 hashes unchanged (list digest `d42892ec…`) and all match | PASS |
| generated index equals pinned digest and committed frozen index | PASS |
| index describes the fixture set | PASS |
| unmodified directory seals; both verifiers pass; `DONE` accepted | PASS |
| `FAILED` terminal record seals and verifies but is `JOB_ACCEPTED: NO` | PASS |
| missing / modified `index.json`, unexpected extra file, modified `.npy` — refused **before** sealing and **after** sealing | REFUSED |
| hash-pinned index contradicting its fixtures; duplicate / overlapping pins; list without pin; non-regular entries | REFUSED (synthetic) |
| completed computation that cannot be sealed → `DONE`, `EVIDENCE_SEALED: NO`, `JOB_ACCEPTED: NO`, exit 20 | PASS |

## 4. What this does not do

It does not run, re-run or amend the r4 experiment; does not create the replacement package; does not authorize the replacement Gate 3 or any Dedirock activity. A new frozen package with a new digest must be built from the reviewed revision of this change, and the replacement Gate 3 needs its own authorization.
