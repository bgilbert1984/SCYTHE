# corpus-observatory (design only)

**Status:** placeholder. There is no implementation here and none should be added until the
detector exists in a place this repo can reference. Everything in this directory is a design
input to [`docs/rf_corpus_observatory_roadmap_DRAFT.md`](../../docs/rf_corpus_observatory_roadmap_DRAFT.md).

## Why design only

The Observatory measures detectors against receiver fingerprints. Its first measurement needs
the Hann detector bundle and the TrevTron data. A search of this repo (2026-10-24) found no hit
for TrevTron, AirBandScan, `SCYTHE_REPLAY_BLOCKED` or the Hann bundle; the reference comb and the
mission wiring (#189–#192) are here. Building a harness with nothing to run would fix the
interface to a guess.

## Contents

| File | Holds |
|---|---|
| [`trust_dimensions.md`](trust_dimensions.md) | what a trust report says, and why it is not one number |
| [`fingerprint_schema.md`](fingerprint_schema.md) | the nine v1 metrics and the freeze rule |
| [`quarantine_policy.md`](quarantine_policy.md) | EXTERNAL / INADMISSIBLE, and what that forbids |
| [`detector_interface.md`](detector_interface.md) | what a detector must expose to be benchmarked |

## Unblocking

Locate the Hann detector bundle and the corpus copy, or confirm they are not in this repo and
treat the detector as a new component. Until then nothing here runs.
