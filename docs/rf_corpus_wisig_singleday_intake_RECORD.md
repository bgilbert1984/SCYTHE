# WiSig SingleDay — intake record

**Status:** admitted for non-commercial research validation only. Not admitted
for commercial use. Format validated; leakage checks not yet run.
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

## Leakage notes (inputs to the checks, not results)

- Each TX/RX pair appears twice (equalized 0/1) with the **same packets**:
  keep both copies in one split, or a packet-level split leaks.
- `14-7` is in both `tx_list` and `rx_list`. Meaning of node IDs unconfirmed;
  check the paper's naming before using IDs as split groups.
- One day only: cannot test cross-day shift; that needs the larger subsets.

## Intake checklist

- [x] Provider-backed license recorded (CC BY-NC-SA 4.0)
- [x] Archive-level terms checked (none present)
- [x] Commercial permission independently classified (`denied`)
- [~] Sample format and metadata validated — shapes/dtypes/finiteness yes;
      sample rate, center frequency, ID semantics outstanding
- [~] SHA-256 and provenance recorded — recorded here; hash is first-observation
- [ ] Receiver, transmitter and capture-session leakage checks pass — not run

## Open items

- Resolve node-ID semantics (`14-7`) and take sample rate / center frequency
  from the paper.
- Run leakage checks once a split and detector exist.
- Rights for redistribution and model artifacts stay `unverified` until
  assessed separately.
- Separate: ORACLE rights field unread (Northeastern repository 403);
  ORACLE/NetSTAR permission requests drafted, unsent
  (`rf_corpus_oracle_netstar_permission_requests_DRAFT.md`).
