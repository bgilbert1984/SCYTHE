# Frozen transfer manifest (as executed) — attempt `GATED2-20261015T005128Z-1b0b45`

The four artifacts actually transferred by S2 and verified by S3 (`COMMAND_TRACE.md`), per Gate D
protocol §7 step 4's "exactly four frozen inputs" — not the five-artifact scope attempt 312512
used before that protocol existed (`reference_PREFLIGHT_RECORD.json` and `--reference-preflight`
were deliberately not used; see the local preparation record for the full explanation).

| # | Filename | Local source (at transfer time) | Size (bytes) | SHA-256 | Governing provenance |
|---|---|---|---|---|---|
| 1 | `scythe_r4_1_pkg_8d34de9ac0b1.tar.gz` | `<REDACTED_LOCAL_HOME>/r41_package_build/final/` | 119721 | `2b2e3251b678a5e83735ee663176f7763482a6948a7d5cb57f244dac1142874a` | Built by `build_package.py` from source commit `8d34de9ac0b1f4758cda3412b364582b4271ffe1`, merged `main` via PR #166. |
| 2 | `scythe_r4_1_pkg_8d34de9ac0b1.tar.gz.sha256` | same directory | 102 | `ec6acabc5523852c07cd41802b9651d2c1009fc58fd74c4d4cc02a9f9a9fc043` | Sidecar of artifact 1, same provenance. |
| 3 | `gate_c_preflight.py` | `tools/r4_package/gate_c_preflight.py` (repo checkout at `main`/`54f325e`) | 20247 | `9e84bc77c49e37ed1e01e0c35ea72dcbf75b5752ab2d5d8a91ff53b62281452d` | Merged via PR #172–174, merge commit `622bec31a3c84e7c3be40e9cfa70671b06adc258`. |
| 4 | `operator_declaration.json` | `<REDACTED_LOCAL_HOME>/r4_gate_c_staging_GATED2-20261015T005128Z-1b0b45/operator_declaration.json` | 2731 | `9c8f893eb6eb666348963c35b14ba3271d5cd1f45a58d68cac94a49af4e1ad33` | Drafted by the assistant at the operator's explicit instruction, from facts the operator stated directly in-session (standing maintenance/reboot/reclaim assurance, confirmed operator identity); **confirmed by the operator as the intended declaration** before S2 ran. The copy committed in this directory is redacted (see `REDACTION_MANIFEST.txt`) and therefore carries a different digest (`b9544ac4...`) than this row's — this row records the digest that was actually transferred and actually checked live by `CHECK_ONLY` (S4), which is the authoritative one. |

All four digests above were independently re-verified, byte-for-byte, by S3's remote `stat`+
`sha256sum` loop (`COMMAND_TRACE.md`) — the transfer did not silently corrupt or substitute
anything.

`reference_PREFLIGHT_RECORD.json` was **not** transferred; its status, digest, and relationship to
attempt 312512 are recorded in the local preparation record (not part of this evidence PR, since
it was not used in this attempt). `gate_d_monitor.py` was **not** transferred; no monitor ran
during this attempt.
