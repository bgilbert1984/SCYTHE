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
- Not in the pickle: sample rate and center frequency; taken from the paper
  (25 Msps, 2462 MHz; see Capture facts).

## Leakage checks (data-level, run 2026-10-30)

Run on the restricted-unpickler load; archive SHA-256 re-verified before the
run. These test the data, not a specific split: no detector or split exists
yet, so the checklist item below stays open. Scripts were session-scratchpad
only (`wisig_leakage.py`, `wisig_leakage2.py`; not in the repo).

- **Exact duplicates:** none. All 448,000 packets are unique when hashed
  byte-for-byte (within arrays, across equalized/unequalized copies, across
  receivers and transmitters).
- **Equalized vs unequalized copies:** both versions are derived from the
  *same detected packets* (WiSig paper §IV-B, Fig. 5): the non-equalized set
  keeps the first 256 IQ samples of each packet; the equalized set resamples
  25→20 Msps, corrects frequency offset, estimates the channel from the
  L-LTF, equalizes with MMSE, reapplies the offset, resamples back, and keeps
  the first 256 samples. A packet is dropped from the equalized set if the
  L-STF/L-LTF preamble is misdetected, so the equalized count "might be
  slightly smaller", and the two sets are not guaranteed index-aligned. This
  explains the index-matched test: correlation of a CFO proxy averages 0.036
  (shuffled ≈ 0), 29 of 280 pairs |corr| > 0.3 (e.g. `14-10`→`20-1` at
  −0.829). Net: the copies share source packets even where indices differ, so
  keep both copies of a pair on the same side of any split. How the subset
  scripts pick the 800 signals per pair (random or first-N) is not stated in
  the paper and not checked in `wisig-subset-creation`.
- **Temporal structure:** packet order carries slow drift. Lag-1
  autocorrelation over packet index averages 0.38 for the CFO proxy (max 0.96)
  and 0.105 for log-power (max 0.82). Cause (paper §III): each capture is
  0.512 s at 25 Msps, one transmitter at a time, so all packets of a TX/RX
  pair come from one half-second window; the paper's "same-day" accuracies
  above 99% use train and test data "collected in the same 0.5s capture". A
  random packet-level split is therefore a same-capture split; contiguous
  blocks reduce but do not remove this. A genuine out-of-capture test needs a
  different day, which SingleDay cannot give.
- **Cross-receiver alignment:** rare. 5 of 1,260 (TX × RX-pair) combinations
  show index-matched CFO correlation > 0.5 (max 0.924, `6-15` between `14-7`
  and `8-13`); three of the five involve receiver `14-7`. Per the paper all
  USRP receivers record the same transmitter's transmission at once, but
  operate independently with no time or frequency synchronization, so packet
  indices are not aligned across receivers and the five are probably
  coincidence or partial overlap (unconfirmed). A receiver-disjoint split
  still shares transmission events (same physical packets seen by several
  receivers); whether anything in them lets a classifier exploit that is not
  tested (payload is random bytes with identical spoofed MAC/IP).
- **Node `14-7`:** IDs are ORBIT grid coordinates (x-y); each grid node is a
  roof-mounted PC with WiFi radios and some also have USRPs (paper §III). So
  `14-7` as TX and as RX is one grid location hosting both a WiFi module and a
  USRP. This explains why the `14-7`→`14-7` array holds real data (mean
  magnitude 0.0447, the highest of any receiver for that transmitter) — the
  closest link. Coverage varies: the paper notes "No data from Rx 14-7" for at
  least one transmitter in Fig. 15.
- **Single day:** cannot test cross-day shift; that needs the larger subsets.

**Implications for any split:** hold out contiguous packet blocks and accept
that a within-capture split still overstates accuracy; keep the equalized and
unequalized copies of a pair on the same side; treat a TX and an RX sharing an
ID (`14-7`) as one site, not as independent groups; a receiver-disjoint split
still shares transmission events.

## Capture facts (WiSig paper)

- Testbed: ORBIT grid (20×20, ~1 m spacing). Transmitters: Atheros 5212,
  9220, 9280, 9580 WiFi modules; receivers: USRP B210, X310, N210.
- WiFi channel 13 (2462 MHz, 20 MHz), 25 Msps, 0.512 s captures, mostly
  line of sight, SNR > 10 dB; random-byte UDP payload, all transmitters
  configured with the same spoofed MAC and IP address.
- Capture days: 1, 8, 15, 23 March 2021. SingleDay (28 TX, 10 RX, 800 signals,
  p = 1.0, one day, 1.0 GB) matches the loaded pickle; its date `2021_03_23`
  is the fourth day.
- The paper states a download link and no license; the license evidence is the
  provider page only.

## Intake checklist

- [x] Provider-backed license recorded (CC BY-NC-SA 4.0)
- [x] Archive-level terms checked (none present)
- [x] Commercial permission independently classified (`denied`)
- [x] Sample format and metadata validated — shapes/dtypes/finiteness from
      the file; sample rate (25 Msps), center frequency (2462 MHz) and ID
      semantics (ORBIT grid coordinates) from the WiSig paper
- [~] SHA-256 and provenance recorded — recorded here; hash is first-observation
- [~] Receiver, transmitter and capture-session leakage checks pass —
      data-level checks run (no duplicates; same-capture temporal drift; shared
      source packets between equalized copies; `14-7` explained); no split to
      test yet

## Open items

- Sample rate (25 Msps) and center frequency (2462 MHz) are now taken from
  the paper; no further format metadata outstanding.
- Check how the subset scripts select the 800 signals per pair; explain the
  five high-correlation cross-receiver combos.
- Run split-level leakage checks (contiguous-block splits) once a split and
  detector exist.
- Rights for redistribution and model artifacts stay `unverified` until
  assessed separately.
- Separate: ORACLE rights field unread (Northeastern repository 403);
  ORACLE/NetSTAR permission requests drafted, unsent
  (`rf_corpus_oracle_netstar_permission_requests_DRAFT.md`).
