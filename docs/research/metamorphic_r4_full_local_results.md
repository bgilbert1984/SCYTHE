# Metamorphic harness r4.1 — first full single-host synthetic experiment: results and evidence

> **Disposition: R4_1_SINGLE_HOST_SYNTHETIC_EXPERIMENT_VERIFIED.** Limited to the declared synthetic fixtures, the frozen implementation and the execution environment below. Not cross-host; not real IQ; no weak-signal claim; Layers A, B and B′ are function-level evidence, not production-path coverage.
> The original sealed job, execution record and preflight records remain the custody authority. The repository holds redacted copies only (see §5). This change adds evidence and a verifier; it changes no production code, harness, runner, package or earlier evidence, and authorizes no remote activity.

## 1. Identity of the run
| Item | Value |
|---|---|
| Attempt ID | `R4FULL-20261014T023003Z-2b2e3251b678-SCYTHE-30d8a2f0` |
| Package SHA-256 | `2b2e3251b678a5e83735ee663176f7763482a6948a7d5cb57f244dac1142874a` (not committed; rebuilt byte-identically by `tools/r4_package/build_package.py`) |
| Source commit | `8d34de9ac0b1f4758cda3412b364582b4271ffe1` |
| Runner | unmodified `tools/r4_job/run_job.sh` (`02453e0d…`), `PYTHON` explicit, `PYTHONDONTWRITEBYTECODE=1` |
| Interpreter | virtual-environment python, resolved binary `/usr/bin/python3.12` sha256 `8c515a32b1a5d3d807e53359901a4d09ec06819b488736641aabd6a12eefba63`; Python 3.12.13, NumPy 2.5.1; thread limits 1/1/1/1 |
| Host | `SCYTHE` (WSL2, kernel 6.18.33.2-microsoft-standard-WSL2), boot id `aaf25e41-e4bf-4c1a-81c9-e978cb25e860` |
| Window | 2026-10-13 19:30:11 → 19:55:02 PDT (about 25 minutes), no interruption |
| Preflight dispositions | `record_check_only_1`: **FAIL** (digest sidecar missing for the package verifier; retained); `record_check_only_2`: `PASS_OPERATOR_CONFIRMATION_PENDING`; `record_prelaunch_3` (launch preflight): **PASS** |
| Job manifest (`JOB_SHA256SUMS`) SHA-256 | `ff5f86a29971a1870d3040fbe03a8a3e4dfc4c63448b6848e853f1aadc2721d2` (150 files; fixtures excluded and re-verified at seal time) |
| Execution record checksum file (`EXEC_SHA256SUMS`) SHA-256 | `abf1fb9a0368c542a69b590da601df88a6f981f2c7eb9b2e43c373cddf54b507` |
| Operator confirmation file SHA-256 | `cae1e6c27ef46c4bd6066fd9088c5b98109109dc025454c9cbb3aa25b202555d` (as recorded at the time of the run; `operator/`) |

## 2. Dispositions (reported independently)
| Dimension | Result |
|---|---|
| Computational execution | **COMPLETED** (harness rc 0, gate rc 0; smoke gate PASS) |
| Evidence custody | **SEALED** (`EVIDENCE_SEALED: YES`, manifest read back) |
| Independent verification | **VERIFIED** (`verify_job.py` PASS; separately implemented inventory check PASS: 150 job files, 37/37 fixture-directory files) |
| Job acceptance | `JOB_ACCEPTED: YES`: an intact, successfully executed job; **not** a statement that every relation or mutant outcome was favourable |
| Bounded controls | 88 tests, 0 failures; 36/36 fixture hashes; index pin verified; `holdout/` exactly 37 regular files |
| Identity and namespace | `IDENTITY_AND_NAMESPACE_VERIFIED` (36 fixtures) |
| Production relations | **13 detector relations × 36 fixtures: all `RELATION_SATISFIED`**; **5 persistence relations × 52 candidates: all satisfied**; 0 violated, 0 numerically ambiguous |
| Phase B | 36 mutant records, all `COMPLETED`, 0 `RESOURCE_LIMIT_REFUSAL`; the production verdict is `RELATION_SATISFIED` in every record |
| Scientific interpretation | supported only within the evaluated synthetic conditions |

## 3. Exact counts
All counts are recomputed from the stored records by `analysis/summarize_counts.py` (output `analysis/COUNTS.txt`, identical when run on the original job directory and on the committed copy). Credits: `MUTANT_DETECTED`; `MUTANT_SURVIVED_EQUIVALENT_ON_GRID` (the variant's predicate agrees with production at the target: neither detection credit nor an undetected defect); `PRECONDITION_NOT_MET` (no credit, no blame).

```
LAYER A (grid and predicates; detector involved: False)
  n=500000: computed-exactly-on-boundary bins [225000], ideal-on-boundary bins [225000]
  n=524280: computed-exactly-on-boundary bins [], ideal-on-boundary bins [235926]
  n=524288: computed-exactly-on-boundary bins [], ideal-on-boundary bins []
LAYER B' (function-level; production_path=False; 12 cases; production non-satisfied: 0)
  by mutant and credit:
    span_limit_removed | MUTANT_DETECTED: 6
    span_limit_removed | MUTANT_SURVIVED_EQUIVALENT_ON_GRID: 6
    span_limit_strict | MUTANT_DETECTED: 2
    span_limit_strict | MUTANT_SURVIVED_EQUIVALENT_ON_GRID: 10
    span_limit_widened | MUTANT_DETECTED: 6
    span_limit_widened | MUTANT_SURVIVED_EQUIVALENT_ON_GRID: 6
  by length, mutant and credit:
    n=500000 | span_limit_removed | MUTANT_DETECTED: 2
    n=500000 | span_limit_removed | MUTANT_SURVIVED_EQUIVALENT_ON_GRID: 4
    n=500000 | span_limit_strict | MUTANT_DETECTED: 2
    n=500000 | span_limit_strict | MUTANT_SURVIVED_EQUIVALENT_ON_GRID: 4
    n=500000 | span_limit_widened | MUTANT_DETECTED: 2
    n=500000 | span_limit_widened | MUTANT_SURVIVED_EQUIVALENT_ON_GRID: 4
    n=524280 | span_limit_removed | MUTANT_DETECTED: 4
    n=524280 | span_limit_removed | MUTANT_SURVIVED_EQUIVALENT_ON_GRID: 2
    n=524280 | span_limit_strict | MUTANT_SURVIVED_EQUIVALENT_ON_GRID: 6
    n=524280 | span_limit_widened | MUTANT_DETECTED: 4
    n=524280 | span_limit_widened | MUTANT_SURVIVED_EQUIVALENT_ON_GRID: 2
LAYER B (function-level, production length; production_path=False; 36 fixtures; 288 cases evaluated; production non-satisfied: 0)
  by mutant and credit:
    span_limit_removed | MUTANT_DETECTED: 216
    span_limit_removed | MUTANT_SURVIVED_EQUIVALENT_ON_GRID: 72
    span_limit_strict | MUTANT_SURVIVED_EQUIVALENT_ON_GRID: 288
    span_limit_widened | MUTANT_DETECTED: 144
    span_limit_widened | MUTANT_SURVIVED_EQUIVALENT_ON_GRID: 144
  by family, mutant and credit:
    HA | span_limit_removed | MUTANT_DETECTED: 120
    HA | span_limit_removed | MUTANT_SURVIVED_EQUIVALENT_ON_GRID: 40
    HA | span_limit_strict | MUTANT_SURVIVED_EQUIVALENT_ON_GRID: 160
    HA | span_limit_widened | MUTANT_DETECTED: 80
    HA | span_limit_widened | MUTANT_SURVIVED_EQUIVALENT_ON_GRID: 80
    HB | span_limit_removed | MUTANT_DETECTED: 96
    HB | span_limit_removed | MUTANT_SURVIVED_EQUIVALENT_ON_GRID: 32
    HB | span_limit_strict | MUTANT_SURVIVED_EQUIVALENT_ON_GRID: 128
    HB | span_limit_widened | MUTANT_DETECTED: 64
    HB | span_limit_widened | MUTANT_SURVIVED_EQUIVALENT_ON_GRID: 64
  not evaluated:
    HB | PRECONDITION_NOT_MET | COLLISION_WITH_OTHER_MAPPED_CANDIDATE: 128
PHASE A (production relations; 36 fixtures)
  detector relations by verdict:
    CONJ | RELATION_SATISFIED: 36
    NOISE weak | RELATION_SATISFIED: 36
    ONE_REPEAT_REPLACED (detector) | RELATION_SATISFIED: 36
    PERMUTE reverse | RELATION_SATISFIED: 36
    PERMUTE rot3 | RELATION_SATISFIED: 36
    PHASE 0.449 | RELATION_SATISFIED: 36
    PHASE 1.571 | RELATION_SATISFIED: 36
    SCALE x0.001 | RELATION_SATISFIED: 36
    SCALE x0.37 | RELATION_SATISFIED: 36
    SCALE x1000 | RELATION_SATISFIED: 36
    SHIFT +1000 bins | RELATION_SATISFIED: 36
    SHIFT +100000 bins | RELATION_SATISFIED: 36
    SHIFT -2500 bins | RELATION_SATISFIED: 36
  persistence relations by verdict (one per candidate):
    CONJ | RELATION_SATISFIED: 52
    ONE_REPEAT_REPLACED (persistence) | RELATION_SATISFIED: 52
    PERMUTE (qualifying count, multiset of excesses) | RELATION_SATISFIED: 52
    PHASE 0.449 | RELATION_SATISFIED: 52
    SCALE x0.37 | RELATION_SATISFIED: 52
```

**Phase B (r3 relations, six mutants × first six fixtures; per-relation cells in `analysis/COUNTS.txt`; 13 relations × 6 fixtures = 78 cells per mutant):**

| Mutant | Cells detected | Cells survived | Relations that detect it |
|---|---|---|---|
| `absolute_floor` | 38 | 40 | CONJ, NOISE weak, SCALE ×0.001/×0.37/×1000, SHIFT +1000/+100000/−2500 bins |
| `first_repeat_only` | 18 | 60 | ONE_REPEAT_REPLACED (detector), PERMUTE reverse, PERMUTE rot3 |
| `sign_dropped` | 6 | 72 | CONJ |
| `span_limit_removed` | 0 | 78 | none under the r3 relations |
| `span_limit_widened` | 0 | 78 | none under the r3 relations |
| `span_limit_strict` | 0 | 78 | none under the r3 relations |

## 4. Interpretation (within the stated scope)
- The r3 production relationships are preserved by r4.1 on all 36 synthetic fixtures with no violation, ambiguity or resource refusal.
- The three non-span mutants are detected by r3 relations. The span mutants survive the r3 relations, as in r3, which is not a failure of r4: they are discriminated by the r4 boundary layers, where the production-length Layer B gives `span_limit_removed` 216 detections and `span_limit_widened` 144, with `span_limit_strict` equivalent in all 288 cases.
  Equivalent cases carry neither detection credit nor blame, and the 128 `PRECONDITION_NOT_MET` cases (all Family B, `COLLISION_WITH_OTHER_MAPPED_CANDIDATE`) carry neither.
- `span_limit_strict` is detected only at the synthetic n = 500,000 geometry (2 cases, Layer B′): function-level evidence, since an input of that length cannot occur on the acquisition path.

## 5. Redaction and verification
Repository copies replace the workstation home-directory prefix by `$HOME` (32 of 198 files). `REDACTION_MANIFEST.txt` records every file's original SHA-256, committed SHA-256 and replacement count; its own SHA-256 is anchored here: **`00f51b61b1fb94a484abf04ed489d3e2a2e2e057c5f7ce6eee99b4c5fabc19a3`**.
`tools/r4_package/verify_full_run_evidence.py` (sha256 `de039585107bca158d91a405b22527e565604f58061a826d5ea5ff7ade0bf76a`) run on a pristine `git archive` reports three layers separately:
```
COMMITTED_EVIDENCE_INTEGRITY: VERIFIED
ORIGINAL_MANIFEST_ENTRIES_VS_RECORDED_ORIGINAL_DIGESTS: CONSISTENT
ORIGINAL_JOB_INTEGRITY: NOT_REVERIFIED_FROM_THIS_CHECKOUT
```
Unredacted files (all but 32) equal the sealed originals byte for byte, as the original manifests attest; the 32 redacted files are tied to their sealed original digests through the declared transformation. On the author's workstation, with the uncommitted prefix, reversing the substitution reproduced all 32 recorded original digests; CI reports `REDACTION_TRANSFORM_REVERSAL: NOT_EVALUATED`.
No historical manifest was rewritten. The generated fixture corpus (1.2 GB) and the archive copies are not committed.

## 6. Governance item (separate from the result)
The launch-authorization record is disputed. Recorded fields: `USER_LAUNCH_AUTHORIZATION_REQUEST: PRESENT` (2026-10-14T02:29:53.439Z), `OPERATOR_POWER_CONFIRMATION: RECORDED`, `ASSISTANT_LAUNCH_AUTHORIZATION: NOT_ESTABLISHED_IN_REVIEWED_RECORD`, `GOVERNANCE_DISPOSITION: DISPUTED_AWAITING_RECONCILIATION_BY_THE_OWNER`; see `governance/LAUNCH_AUTHORIZATION_DISCREPANCY.md`. A user request is not an assistant-issued authorization; this is not a finding of misconduct. It is recorded, not resolved, and does not alter the computational evidence.

## 7. Not established
Cross-host reproducibility (the next experimental gate, not yet authorized to execute), behaviour on real IQ, weak-signal sensitivity, production-path coverage of the boundary sweep, and anything outside the 36 frozen synthetic fixtures.
