# NetSTAR BLE (2611.00332) — corpus ingest plan — DRAFT

**Status:** plan only. Nothing contacted, downloaded, or filed. This authorizes
nothing: the corpus is in the blocked registry (no named license) and stays
there until the rights gate in Stage 0 is passed.
**Drafted:** 2026-11-07. **Docs only:** no frozen-registry or schema-v1 change.
**Sources:** the paper PDF (`docs/Dataset and Experimental Testbed 2611.00332v1.pdf`,
read 2026-11-07), `rf_corpus_oracle_netstar_permission_requests_DRAFT.md`,
`rf_corpus_wisig_singleday_intake_RECORD.md` (the precedent this plan follows),
`rf_corpus_observatory_roadmap_DRAFT.md`.

Every "paper says" below is unconfirmed against the data until Stage 2.

## What is being ingested (paper says)

| Item | Value |
|---|---|
| Transmitters | 31 Seeed XIAO ESP32-C3, all hardcoded to one physical address (`0x12:55:55:55:55:55`) |
| Receivers | 2 Ettus USRP B210 (Rx1, Rx2), 6 MS/s, LE 1M uncoded PHY |
| Channels | Ch1 2.406, Ch2 2.408, Ch14 2.434, Ch32 2.470 GHz (extended advertising on data channels) |
| Scenarios | 16: D1–D15, D11 = D11-a/b (Table II) |
| Per-frame layout | complex64; 91 samples retained before the preamble; silence removed, frames aligned by preamble cross-correlation |
| Per-device matrix | `X_k` ∈ ℂ^(D×N); temperature `T_k` ∈ ℝ^D float32 for D1–D3 only; NumPy arrays |
| Hosting | `research.engr.oregonstate.edu/hamdaoui/datasets` (paper's link; not fetched) |

Scale, from Table II (complex64 = 8 B): D1–D3 are 33,000 frames × 4,096 samples
per device ≈ **33.5 GB each**; D4–D15 (13 sets incl. D11-a/b) are 3,000 × 1,664
≈ **1.24 GB each**. Total ≈ **117 GB**. This host has ~61 GB free, so the
whole set cannot be staged at once (Stage 1).

## Stage 0 — Rights gate (blocks everything below)

- Provider release note (Aug 2025) asks only for citation; no license, and
  the lab's code repo has none (per the permission-request draft). The 2611
  paper states no license either (checked 2026-11-07: no CC/licence text).
- The NetSTAR request (LoRa + BLE, one message) is drafted and **unsent**.
  Sending is a separate decision.
- Acceptance criteria are the existing ones: a named license or a written
  grant citable in `rights_assessment`. Silence is not a finding; admit-or-drop
  on silence is the owner's call.
- Until then, fail-closed record:

```yaml
rights_assessment:
  schema_version: "1.0-draft"
  research_use: unverified     # release note: "cite"; no use terms
  commercial_use: unverified
  redistribution: unverified   # policy here: no redistribution regardless
  model_artifact_release: unverified
  evidence:
    - source_url: https://research.engr.oregonstate.edu/hamdaoui/datasets
      verified_at: "<fill on read>"
      statement: "Please cite our work in case the datasets were used"
```

- **Decision for the owner:** is "cite" plus a research-only intent enough to
  admit for non-commercial validation, as the LoRa note's "can be downloaded
  and used for research" was treated? The BLE note does not say that. Default
  in this plan: wait for the reply.
- The permission draft says the data is on a Box folder whose contents and any
  terms file inside are unreadable by automated fetch; a terms file may exist
  there. Read it by hand before sending the request.

## Stage 1 — Acquisition and provenance

1. Confirm **which release** this is. The Aug 2025 release note describes three
   scenarios plus a separate temperature set; the 2611 paper describes 16.
   Record release name, date and URL for every file.
2. Stage outside the repo, one scenario at a time. Order: D4–D15 first
   (~16 GB), then D1–D3 singly (33.5 GB each, deleting or subsampling after
   fingerprinting), or stream from the host if it supports ranges.
3. Per file: byte length vs `Content-Length`, SHA-256 (first observation; the
   provider publishes no checksum), download date.
4. The repo gets only the intake record and metrics, not the corpus
   (same rule as WiSig).

## Stage 2 — Format validation (no untrusted code)

- NumPy only: read headers without loading; `np.load(..., allow_pickle=False)`;
  refuse object dtypes. If any file is a pickle or zip of pickles, use the
  restricted-unpickler procedure from the WiSig record.
- Check against the paper: 31 devices; dtype complex64; frames per device and
  samples per frame vs Table II; finiteness; `T_k` present only in D1–D3 and
  length equal to the frame count.
- **Inconsistencies in the paper to resolve against the files** (record the
  answer in the intake record, not here):
  - Section III-C names the cross-receiver sets D7/D8 (wired) and D10 (Rx1
    wireless) / D11-a,b (Rx2). Table II has D7 = Ch32, D9 = Rx1 wireless Ch1,
    D10 = Ch2. Section V uses D9 vs D11-a. Table II plus the file names win.
  - Temperature is "injected into the last two bytes of the link layer
    payload" yet stored as float32 (four bytes). Check what is actually in
    the payload versus `T_k`.
  - Table II "Location" mixes line-of-sight/NLoS (D1–D11) with distance
    (D12–D15); D12 is 1 m and D15 is 1.5 m, so "location" is not monotone in
    distance.

## Stage 3 — Leakage and hygiene checks (data level)

Following the WiSig checks; each is a question, not a prediction.

1. Exact duplicate frames within and across scenarios and devices.
2. **Same-capture temporal structure**: each (device, scenario) is one
   continuous 6- or 10-minute run. Lag-1 autocorrelation of a CFO proxy over
   frame index. A random frame-level split is a same-capture split; use
   contiguous blocks.
3. **Temperature as a label leak (D1–D3):** the telemetry rides in the
   transmitted payload, and warm-up order differs per device. Test whether
   `T_k` alone, or payload bytes alone, classify devices. If so, D1–D3 results
   need the temperature bytes masked.
4. **Payload fixed vs random:** D4–D15 use a fixed payload, D1–D3 random.
   With one address for all devices, check the fixed payload carries no
   device-specific bytes.
5. **Receiver and session confounds:** one session per (device, scenario)
   means scenario and day are confounded with the channel realization.
   D11-a vs D11-b is the only same-condition repeat. Treat the 31 devices as
   not independent of their scenario's run order.
6. **Silence removed**: noise-only captures do not exist in this corpus
   (Stage 4 consequence).
7. Cross-day and cross-year pairs only where the paper says so: D11-a/b (days),
   D8/D11 vs D1–D3 (~1 year, paper's wording; confirm dates from file
   metadata, not prose).

## Stage 4 — Mapping to the Observatory

Axis map (paper's design; to be confirmed in Stage 2):

| Axis | Sets | Note |
|---|---|---|
| Thermal | D1–D3 (warm-up) vs D4–D15 (stable) | Rx2 only for D1–D3 |
| Receiver | D4/D8 (wired), D9/D11 (wireless), Rx1 vs Rx2 | closest receiver-only control is wired D4 vs D8 |
| Channel | D4–D7 (wired), D9/D10 (wireless) | |
| Location/range | D12–D15 outdoor | single receiver |
| Aging | D11-a vs D11-b (days); D8/D11 vs D1–D3 (~year) | |

Fingerprint schema v1 applicability (proposal; freeze only after review):

| Metric | Applicable? |
|---|---|
| Reference comb, LO leakage, empty-frame entropy | **N/A — no noise-only captures** (silence removed) |
| DC offset, spectral tilt, gain compression | computable on frames; mixes TX and RX effects |
| Clock drift | per-frame CFO is a TX+RX quantity; the wired Rx1/Rx2 pair is the cleanest test |
| Periodic interference | limited to the frame window |
| Receiver uniqueness | undefined (roadmap open decision 3) |

Out-of-band metrics are N/A-with-reason: BLE occupies ~2 MHz of a 6 MS/s
capture. Report them as not-computable, not as zero.

## Stage 5 — Registry entry

- Registry entry per corpus; ObservationSet per file under it (roadmap open
  decision 4 — still the owner's call).
- Fields: source, release, license (`unverified` until Stage 0), SHA-256,
  import date, `External / Inadmissible` markers.
- No observation payloads in the registry.

## Stage 6 — First runs once admitted

1. **Reproduction check, not a result:** train on D12, test D13–D15, PD vs IQ
   features, one architecture. The paper reports PD 94.68% / IQ 84.73%
   (averaged over unseen domains and four architectures). If a single
   architecture lands far from that, the ingest is wrong before the science is.
2. Receiver axis: D9 ↔ D11-a, and wired D4 ↔ D8.
3. Thermal: train D9/D4, test D1–D3, with the Stage 3.3 temperature check
   applied first.
4. Aging: D11-a ↔ D11-b, then ~year pair.
5. Report each as its own claim. Cross-lifecycle is a separate claim from
   within-session (same stance as RERUN2 being SESSION_SCOPED).

## Limits to state in any report

All 31 transmitters are one hardware model; both receivers are one model; BLE
only. It cannot separate chipset-family signature from individual-device
signature. Scenarios are single runs, not replicated (except D11).

## Open items

- Rights reply (Stage 0) and whether the request is sent at all.
- Which release; whether the Box folder holds a terms file.
- Disk plan for D1–D3 (117 GB total vs ~61 GB free).
- The D7/D9/D10 labelling conflict and the temperature byte-count conflict.
- The BLE "Replicating the Signature" paper (arXiv 2607.05549) says its data is
  "publicly available" and cites this corpus; whether its attack captures are in
  these releases is unconfirmed (from the permission draft).
- Roadmap §10 out-of-band metrics were referred to in the request but not found
  in `rf_corpus_observatory_roadmap_DRAFT.md`; this plan treats them as defined
  elsewhere.
