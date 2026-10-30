# WiSig SingleDay — intake record

**Status:** admitted for non-commercial research validation only. Not admitted
for commercial use. Format validated; data-level leakage checks run (see below), split-level
leakage checks still open.
**Verified:** 2026-10-30. Docs only: no frozen-registry or schema-v1 change.
Rights are recorded in the provisional `rights_assessment` sidecar below
(`unverified` = fail-closed).

## Provenance

| Field | Value |
|---|---|
| Corpus | WiSig (Hanna, Karunaratne, Cabric), subset **SingleDay** |
| Paper | IEEE Access 10:22808–22818 (2022), doi 10.1109/ACCESS.2022.3154790 |
| Provider page | https://cores.ee.ucla.edu/downloads/datasets/wisig/ (UCLA CORES Lab) |
| Hosting | Google Drive only (file id `1lWf9BuUZTSNcABVFWYoBT_-EH8ctXEcZ`); no mirror |
| Cited by | TrustAgent-RFF (arXiv 2610.24930) as its WiSig source |
| Downloaded | 2026-10-30, `SingleDay.pkl.zip`, 1,119,463,474 bytes (= Content-Length) |
| SHA-256 | `bb4af37ebbdbfa2c99e1fe589c6a5be7f03f101cdaf3fc9440245e3b48a17965` |
| Hash basis | first observation; the provider publishes no checksum |
| Local copy | session scratchpad (ephemeral); the corpus is **not** in this repo |

## Rights

Dataset license (provider page): **CC BY-NC-SA 4.0**. Code repos under
`github.com/WiSig-dataset/*` (examples, subset-creation, process-raw,
capture-commands): BSD-3-Clause (LICENSE.md text read for `wisig-examples`;
the other three by GitHub's detected license only).

Archive-level terms: the zip holds a single member, `SingleDay.pkl`; no
LICENSE, COPYING or README, empty zip comment. Nothing narrows or extends the
provider-page license.

```yaml
rights_assessment:
  schema_version: "1.0-draft"
  research_use: granted            # CC BY-NC-SA 4.0, non-commercial
  commercial_use: denied           # NonCommercial; no separate authorization held
  redistribution: unverified       # SA/BY terms apply to adaptations; policy here is no redistribution
  model_artifact_release: unverified  # whether trained models/fingerprint assets are "adaptations" is not settled by the license text
  evidence:
    - source_url: https://cores.ee.ucla.edu/downloads/datasets/wisig/
      verified_at: "2026-10-30"
      statement: "The WiSig dataset is licensed under a Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International License."
```

Obligations: cite the WiSig paper (page also lists the ORBIT testbed paper);
attribute; share-alike on adaptations. Models trained on the data and derived
fingerprint assets get a separate rights assessment before any release.

## Safety inspection (no code executed)

- `pickletools.genops` over the zip stream (nothing extracted): protocol 3,
  16,035 opcodes; only three imports — `numpy.core.multiarray._reconstruct`,
  `numpy.ndarray`, `numpy.dtype`. No `os`/`subprocess`/`builtins`/`eval`.
- Loaded with a restricted `pickle.Unpickler` allowing exactly those globals;
  nothing blocked. Do the same for any later load; never plain `pickle.load`.

## Structure (decoded)

- Top-level dict: `tx_list`, `rx_list`, `capture_date_list`, `equalized_list`,
  `max_sig`, `data`.
- `data[tx 28][rx 10][date 1][equalized 2]` → ndarray `(800, 256, 2)` float64;
  560 arrays, 800 packets each = 448,000 packets, 256 samples, I/Q last axis.
- TX IDs: 28 (`1-11` … `8-3`). RX IDs: 10 (`1-1`, `13-13`, `14-7`, `2-1`,
  `2-20`, `20-1`, `7-14`, `7-7`, `8-13`, `8-8`). Capture date: `2021_03_23`
  only. `equalized_list` = `[0, 1]`. `max_sig` = 800.
- All 560 arrays finite (no NaN/inf); first array mean |x| ≈ 0.016, max ≈ 0.060.
- Not in the pickle: sample rate, center frequency (take from the paper).

## Leakage checks (data-level, run 2026-10-30)

Run on the restricted-unpickler load; archive SHA-256 re-verified before the
run. These test the data, not a specific split: no detector or split exists
yet, so the checklist item below stays open. Scripts were session-scratchpad
only (`wisig_leakage.py`, `wisig_leakage2.py`; not in the repo).

- **Exact duplicates:** none. All 448,000 packets are unique when hashed
  byte-for-byte (within arrays, across equalized/unequalized copies, across
  receivers and transmitters).
- **Equalized vs unequalized copies:** the earlier claim that the two copies
  of a TX/RX pair hold the *same packets* is **not supported**. Index-matched
  correlation of a CFO proxy averages 0.036 (shuffled ≈ 0); log-power 0.067 vs
  0.003. 29 of 280 pairs show |corr| > 0.3 (e.g. `14-10`→`20-1` at −0.829),
  most show none. Equalization may destroy these proxies, so this neither
  proves the copies differ nor that they match. Unresolved; settle it from the
  WiSig paper's description of the equalized set or a direct equalization test.
- **Temporal structure:** packet order carries slow drift. Lag-1
  autocorrelation over packet index averages 0.38 for the CFO proxy (max 0.96)
  and 0.105 for log-power (max 0.82). A random packet-level split puts
  near-neighbors on both sides and inflates accuracy; **split by contiguous
  packet blocks**.
- **Cross-receiver alignment:** rare. 5 of 1,260 (TX × RX-pair) combinations
  show index-matched CFO correlation > 0.5 (max 0.924, `6-15` between `14-7`
  and `8-13`); three of the five involve receiver `14-7`. No evidence of a
  systematic same-transmission-seen-by-several-receivers layout; the five are
  unexplained.
- **Node `14-7`:** appears in both `tx_list` and `rx_list`. The
  `14-7`→`14-7` array holds real data (mean magnitude 0.0447, the highest of
  any receiver for that transmitter), not zeros: consistent with a co-located
  or same-node link, but ID semantics are unconfirmed against the paper.
- **Single day:** cannot test cross-day shift; that needs the larger subsets.

**Implications for any split:** hold out contiguous packet blocks; keep the
equalized and unequalized copies of a pair on the same side until their
relationship is settled; treat `14-7` carefully (not as a split group key, and
not on both sides of a receiver-disjoint split) until its meaning is known.

## Intake checklist

- [x] Provider-backed license recorded (CC BY-NC-SA 4.0)
- [x] Archive-level terms checked (none present)
- [x] Commercial permission independently classified (`denied`)
- [~] Sample format and metadata validated — shapes/dtypes/finiteness yes;
      sample rate, center frequency, ID semantics outstanding
- [~] SHA-256 and provenance recorded — recorded here; hash is first-observation
- [~] Receiver, transmitter and capture-session leakage checks pass —
      data-level checks run (no duplicates; temporal drift and unresolved
      equalized-copy and `14-7` questions found); no split to test yet

## Open items

- Resolve node-ID semantics (`14-7`) and take sample rate / center frequency
  from the paper.
- Settle the equalized-vs-unequalized relationship (WiSig paper or a direct
  equalization test); explain the five high-correlation cross-receiver combos.
- Run split-level leakage checks (contiguous-block splits) once a split and
  detector exist.
- Rights for redistribution and model artifacts stay `unverified` until
  assessed separately.
- Separate: ORACLE rights field unread (Northeastern repository 403);
  ORACLE/NetSTAR permission requests drafted, unsent
  (`rf_corpus_oracle_netstar_permission_requests_DRAFT.md`).
