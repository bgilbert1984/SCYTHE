# RF Corpus Observatory — Research Roadmap (DRAFT)

**Status:** draft for review · **Date:** 2026-10-24 · **Author:** Spectrcyde (dictated); SCYTHE (scribed)
**Quarantine:** every imported corpus is provenance: EXTERNAL, admission: INADMISSIBLE.
HF corpora are test vectors and content, never §5.21 catalogue evidence.

## Framing question

Not "is this dataset good?" —

"What latent instrumentation signatures exist across public RF datasets?"

The deliverable is a table per corpus, reproducible from a frozen schema,
citable independent of SCYTHE. Strategic target: SCYTHE as a reference point
for RF dataset quality — *"how much of this dataset reflects the signal of
interest, and how much reflects the receiver that recorded it?"*

---

## Phase 1 — RF Corpus Observatory (generalized hygiene / fingerprinting)

```
Dataset → Metadata (receiver, bandwidth, center freq, noise-only captures)
        → Fingerprint extraction → Trust profile
```

**Fingerprint schema v1 (freeze before corpus #2 runs).**
Nine metrics, each with a frozen definition and acceptance language:

| Metric | What it measures |
|---|---|
| Reference comb | coherent harmonic teeth in noise/empty captures (TrevTron: 149/150 Hz teeth, 100/100 noise samples) |
| LO leakage | DC-bin energy excess in noise captures |
| DC offset | mean I/Q offset in LSB |
| Clock drift | carrier-frequency drift across capture |
| Gain compression | clipping/saturation statistics |
| Spectral tilt | passband power slope |
| Periodic interference | non-comb cyclostationary structure |
| Empty-frame entropy | information content of nominally empty frames |
| Receiver uniqueness score | **undefined — must be defined before corpus #2.** Needs a reference population: distance in fingerprint space against what? |

Without a frozen schema the trust table quietly decays as definitions evolve.
The receiver uniqueness score is the one metric with no working definition;
everything else is cheap spectral statistics or already proven (comb on
TrevTron; DC offset already in the XTR baseline metric set).

**Output:** per corpus — Fingerprint Report; trust table of the form

| Dataset | Fingerprint | Confidence | Trust |
|---|---|---|---|
| TrevTron | Comb | High | External test vector |
| Dataset B | LO bleed | Medium | Receiver-dependent |
| Dataset C | None | High | Preferred benchmark |

**Corpus admission, first sweep (as of 2026-10-24):**
- TrevTron/rtl-ml-dataset — public, already fingerprinted (comb). Reference corpus. First sweep runs the full nine-metric battery on it today.
- Gated (access request required, user's call): Morty0311/starlink-d2c-x300-duplex-iq, alirezaaminzadeh/device-rf-fingerprint, sohelimi/cvnet-rf.
- Synthetic negative controls — verified on the Hub 2026-10-24: **alirezaaminzadeh/radioml-2018-samples** (public, ungated, MIT; 550 windows, 50/class × 11 modulations, 1024 complex samples each, fields iq_i/iq_q/modulation/snr_db/label). GNU-Radio-synthesized, so the fingerprint battery should return "no receiver chain" — or recover whatever synthesis artifacts RadioML is known to carry (Panoradio notes documented flaws; official site marks it "erratic"). Either result validates the metric definitions.
- Excluded (wrong modality): RFUAV, Francesco/radio-signal (spectrogram images); nvidia/aerial-isac-srs-iq (tabular); Katherinezml/radar_jamming (single .rar, unverified provenance, likely synthetic).

---

## Phase 2 — Detector Transferability

Characterize Detector × Receiver × Dataset × Behavior:

```
Detector → Receiver → Dataset → Behavior
```

| Detector | Dataset | Receiver fingerprint | Agreement |
|---|---|---|---|
| Hann v2 | TrevTron | Comb | (per class) |
| Hann v2 | Corpus B | LO leakage | (per class) |
| Hann v2 | Corpus C | None | (per class) |

**Hard rule: report agreement per class, never aggregate.** TrevTron is the
warning — 100% on six signal classes, 0% on noise. A single "98%" cell hides
exactly the structure the matrix exists to expose. Low agreement is
ambiguous between detector failure and label error; the receiver-fingerprint
column is what lets them apart — that disambiguation is the matrix's
scientific contribution.

One elegant pairing already available: alirezaaminzadeh's synthetic
radioml-2018-samples vs. his gated device-rf-fingerprint corpus — same
uploader, synthetic vs. real receiver. Clean isolation of the acquisition
variable, pending the access decision.

---

## Phase 3 — Corpus Registry + Provenance metadata

External Corpus Registry — SBOM for datasets:

```
Corpus → License → Hash → Quarantine → Known artifacts → Known receiver → Citation → Trust score
```

Every import auto-generates an ObservationSet:
`External / Inadmissible / Source / License / Hash / Import date.`
This succeeds where the stood-down HF plan failed: provenance is what an
ObservationSet already holds (`source_filename`, `source_sha256`,
`preprocessing_history`); license and the External/Inadmissible marks are
additive fields in the #189/#190 pattern. No observation payloads.

**Open design question:** ObservationSet is per-artifact — is an import one
set per file, or one per corpus? The five-years-from-now question ("where
did these observations originate?") wants per-file sets rolled up under the
registry entry.

Ordering rationale: registry third, after Phases 1–2 have hardened schema v1.
Then every subsequent experiment inherits provenance automatically.

---

## Phase 4 — Safari multimodal prototype (ATC + ADS-B + transcript timeline)

Priority raised: not for the audio, for the synchronization.

```
ATC audio + ADS-B + timestamps → Mission timeline
13:41:02 → Aircraft appears → ATC instruction → Heading changes → Speed change → Altitude response
```

The Mission holds multiple evidence modalities:
`Observation → Position → Audio → Transcript → Inference` — without touching IQ.

This is stronger than "content": a transcript is a derived observation, and
ASR on ATC phraseology will garble callsigns — its confidence belongs in the
record. But the timeline gives something rare: inference checkable against
physics. ATC says turn, the ADS-B track turns or it doesn't. First Safari
claim verifiable rather than merely displayable.

**Material in hand:** tartanaviation-atc-adsb (ATC audio matched to ADS-B
tracks, CC-BY-4.0, CMU AirLab) plus pre-segmented
tartanaviation-atc-adsb-utterances variant. Staged 2026-10-24:
`assets/speech1.wav` (11.7 s, 97% voice band) and `assets/speech2.wav`
(15.0 s, 96% voice band), both untracked. *Editor's note (2026-10-24):* the
MANIFEST and `timeline_seed.json` described with this staging, and the
`~/workspace/airbandscan_trial/` directory, were not found on the serving host;
the two WAVs are the only staged files that exist. Their licence/hash record has to
be regenerated before they count as provenance. **Open leg:** no transcripts ship with the corpus;
Phase 4's transcript leg needs an ASR piece, and if any of this goes public,
CC-BY-4.0 requires crediting CMU AirLab.

---

## Phase 5 — Mission smoke tests

Regression tests only, not research. The #189–#192 wiring is already
verified end-to-end; re-running it on external data buys little.

---

## Standardized pipeline (every corpus, no custom analysis)

```
Download → Fingerprint → Characterize → Publish report
```

Identical outputs per corpus:
**Fingerprint Report → Detector Report → Trust Report → Mission Import Report.**
HF is treated as a scientific benchmark repository, not a download source.

---

## Longer-term vision — Corpus Explorer

A SCYTHE page: `Corpus → Fingerprint → Noise profile → Artifacts → Spectrum
→ Receiver signature → License → Recommended use`. Click a corpus, see comb,
clock drift, sample rate, IQ statistics, receiver family, confidence, known
issues — for anyone evaluating public RF datasets.

## Open decisions (user's call)

1. Corpora admitted to the first sweep (TrevTron + which others).
2. Whether to pursue access to the gated three.
3. Receiver uniqueness score: definition before corpus #2.
4. ObservationSet granularity: per-file sets under a registry entry, or per-corpus.
5. Where this doc lives (workspace vs. SCYTHE repo docs/).
