# Frozen execution-bundle manifest (prep only — not an authorization)

Per the owner's readiness assessment (2026-10-14), item 2: "record the merged monitor SHA-256,
launcher identity, package digest, comparator version, reference evidence, and exact retrieval
inventory." This is a record of what currently exists, merged, on `main` — it is **not** a Gate C
`CHECK_ONLY`/`RECHECK` record, not an operator declaration, and not a Gate D authorization. A real
Gate D attempt still requires its own fresh declaration (protocol §3) and its own fresh `CHECK_ONLY`
(protocol §3 step 2) run on the actual replay host at the time of that attempt — nothing below
substitutes for either.

## Repository state

- Gate D protocol, Revision 2 (merged): `docs/research/metamorphic_r4_gate_d_protocol.md`
  SHA-256 `819135a3acaa8c2d03f892e7a2bde84319b71f0a88c7cdf5ed96c15d57952313`
- Gate D monitor (merged): `tools/r4_package/gate_d_monitor.py`
  SHA-256 `8970c2490af313074669874f9fc3f9177bebf72af88e1473fb55e19204f742e4`
- Gate D monitor tests (merged): `test_rf_r4_gate_d_monitor.py`
  SHA-256 `8cfcd6677632da86fa9df39ba88290f97e948cf355309aad79f9f4c264d4ae84`
- Cross-host comparator: `tools/r4_package/compare_replay.py`
  SHA-256 `54ea4e2c5283be4a3adb14f471b19de07055b99aae9bfb0af093cc03b0b27d9c`
- Gate C preflight tool: `tools/r4_package/gate_c_preflight.py`
  SHA-256 `9e84bc77c49e37ed1e01e0c35ea72dcbf75b5752ab2d5d8a91ff53b62281452d`
- Launcher identity: `tools/r4_job/run_job.sh` (as packaged; the live replay host's copy is the one
  `gate_c_preflight.py` extracts and digest-verifies fresh at `CHECK_ONLY`/`RECHECK`, per protocol §7
  step 4 — this entry records what the current frozen package carries, not a substitute for that
  re-verification)
  SHA-256 `02453e0d2b29dd635b0389373f851886293c0c8b37ec431538e01856ffa22d20`
- Merge commit carrying all of the above together: `7f00777821f03dc79aa3a58c9fb9ff61dcf21031`
  (PR #176, amendment commit `c3b4d40e19780c32aad8eb74eef38f74e2aef21b`)

## Reference package (frozen, local; from existing provenance evidence)

From `docs/evidence/r4_1_packaging_provenance_2026-10-13/package_2b2e3251/PACKAGE_RECORD.txt`
(unchanged by this rehearsal, cited by reference only):

- `PACKAGE_SHA256`: `2b2e3251b678a5e83735ee663176f7763482a6948a7d5cb57f244dac1142874a`
- `SOURCE_COMMIT`: `8d34de9ac0b1f4758cda3412b364582b4271ffe1`
- `EXECUTION_AUTHORIZED`: NO; `DEDIROCK_TRANSFER_AUTHORIZED`: NO (as recorded there — unchanged)

This package predates the Gate D protocol and monitor; a real Gate D attempt's fresh `CHECK_ONLY`
(protocol §3 step 2) will record the package digest actually transferred for that attempt, which must
be independently re-verified at that time, not assumed equal to the digest above.

## Reference evidence (cited, not reproduced here)

- Gate C execution, attempt 312512: `PASS`; historical; not reusable as a new launch's baseline
  (protocol §2, §3). Its artifacts are not present in this worktree/branch.
- Local r4.1 full-run evidence: `docs/evidence/r4_full_local_2026-10-13/`.
- Packaging provenance: `docs/evidence/r4_1_packaging_provenance_2026-10-13/`.

## Retrieval inventory (template, not populated)

Protocol §6.1/§6.2 already specify the exact permitted categories and the method for building a
concrete, frozen inventory (read `JOB_SHA256SUMS` and the two `results_*/MANIFEST.sha256` files,
intersect with the permitted categories, write the resulting list before any transfer). That inventory
can only be built from a real attempt's actual manifests on the actual replay host — it is not
something this local rehearsal can freeze in advance, and this manifest does not attempt to.

## What this manifest does not do

It does not authorize a connection, declaration, `CHECK_ONLY`, `RECHECK`, launch, monitoring, or
retrieval. Gate D remote execution remains **NOT AUTHORIZED**.
