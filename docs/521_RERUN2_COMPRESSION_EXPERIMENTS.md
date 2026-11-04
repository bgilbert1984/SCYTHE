# §5.21 RERUN2 — archive and compression experiments (2026-11-03/04)

Status: results record. Nothing here changes the archival default or any frozen authorization.

## 1. Archive and off-host copy

| Item | Value |
|---|---|
| Source | `catalogue_xtr_20261103_rerun2` on scythe-1 (192/192 visits, read-only; never modified by the experiments) |
| Archive | deterministic tar (sorted, fixed mtime/owner) piped to `zstd -T0 -6`; 496,394,024 B, 3.42x |
| Verification | `zstd -t`; decompressed tar hash equals the hash taken while writing; full extraction compared per file (all 199 SHA-256 hashes) and by tree (types, sizes, paths) |
| Off-host copy | a second machine (the operator workstation), re-verified there against the same hashes; read-only |

Only **two** copies exist. A third, independently located copy has not been made and needs a destination from the owner. The copy is checked by hash, but a restore test on a clean machine from the off-host copy alone has not been done beyond the hash verification.

Script: `scripts/s521_compression/mk_archive_rerun2.sh`, committed exactly as run (paths are the ones used on scythe-1).

## 2. Raw IQ compression (16 of 192 visits, 134 MB, four batches of four)

Every transform was inverted and every compressed stream decompressed and compared byte for byte before its size was counted. Ratio = original / compressed; aggregate = equal-size batches combined.

| Method | Aggregate ratio | Batch range |
|---|---|---|
| deinterleave I/Q + `zstd -19 --long` | 4.04 | 3.85 – 4.22 |
| deinterleave I/Q + `xz -6` | 4.04 | 3.84 – 4.22 |
| plain `zstd -19 --long` | 4.00 | 3.82 – 4.18 |
| deinterleave + `zstd -6` | 3.45 | 3.32 – 3.58 |
| plain `zstd -6` (archive default) | 3.41 | 3.27 – 3.54 |
| plain `zstd -3` | 3.32 | 3.18 – 3.46 |
| per-plane first difference + `xz -6` | 3.85 | 3.66 – 4.04 |
| nibble split + `xz -6` | 2.72 | 2.64 – 2.76 |
| bit-plane split + `xz -6` | 2.65 | 2.56 – 2.72 |
| bit-plane split + `zstd -3` | 2.33 | 2.23 – 2.40 |

Order-0 entropy (a bound, not an achieved codec): about 1.9 bits/sample for I and 1.7 for Q on average, a memoryless ceiling near 4.2x. Conditioning on the previous sample lowers it by about 5%; conditioning Q on I gains nothing measurable. This is a memoryless-model figure, not a limit on lossless compression in general.

Cost: `zstd -19 --long` is roughly 35–50x slower than `-6` (about 1.5 s/MB against 0.03–0.05 s/MB in these runs) for about 15% less storage than `-6`.

## 3. Text artefacts

| File | `zstd -6` | `zstd -19 --long` |
|---|---|---|
| `acquisition_journal.jsonl` (71.7 MB) | 3.3x | 3.8x |
| `catalogue.json` (13.4 MB) | 10.2x | 12.9x |

## 4. Deduplication

Gear-hash content-defined chunking (64 KiB average) and fixed 64 KiB chunking were run over all 196 files (1,696 MB) of RERUN2, and across 16 visits of RERUN2 against the earlier partial run with the same schedule and tunings. Both modes left **100.00% of bytes unique**, in both tests. The chunker was checked against a synthetic shifted duplicate and found it, so this is a property of the data (independent noise captures), not a chunker bug. Chunk sizes other than 64 KiB were not tested.

## 5. Conclusions and limits

- Keep `zstd -6` as the archive default. `zstd -19 --long` is a cold-storage option that should be justified by a cost model.
- I/Q deinterleaving adds about 1.4% and is not worth a new format. Delta, nibble and bit-plane transforms are worse than plain.
- Cross-capture deduplication saves nothing on terminated-input IQ. Any evidence-vault value is integrity, indexing and provenance, not storage.
- Applies to **terminated, ~1 LSB-wide input only**. Captures of real structured signals (carriers, bursts, modulated signals) may behave differently and should be benchmarked separately before any conclusion is carried over.
- 16 of 192 visits were sampled for codecs; dedup covered everything.
- Scripts: `scripts/s521_compression/exp_codec.py` (codecs, transforms, entropy bounds) and `exp_cdc.py` (chunking). They were run on disposable extractions, never on the preserved copies. Raw outputs are not committed; the tables above are the record.
