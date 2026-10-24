# TartanAviation voice + ADS-B: design (no implementation)

**Status:** design proposal. No parser, importer or UI exists for this. It exists to make the mission
model explicit and settle provenance before any code.

## Goal

Show ATC audio and ADS-B tracks on one mission timeline, and let an operator see whether what was said
matches what the aircraft did. The point is the synchronisation, not the audio.

## Principle

A correlation is an **interpretation**, not an observation. "ATC said turn left" and "the track turned
left" are two observations; "the turn answers the instruction" is an inference that can be wrong and
carries its own confidence. The record keeps the three apart, and the interpretation is never stored
as if it were measured.

## Layers

```
External dataset (Tier 1, EXTERNAL / INADMISSIBLE)
  -> Import                  per-file, hash and licence recorded
  -> Mission                 the container the timeline belongs to
  -> ObservationSet          one per source file
       -> Audio observation  clip, time span, sample rate
       -> ADS-B observation  position/altitude/speed samples with timestamps
  -> Interpretation layer    derived, versioned, carries confidence
       -> Transcript         ASR output (a derived observation, not the audio)
       -> Instruction event  parsed from the transcript
       -> Voice <-> position correlation
  -> Visualization only      timeline and map; reads, never writes back
```

Nothing below "Import" modifies anything above it.

## Tiers

| Tier | What | Rule |
|---|---|---|
| 1 | Public dataset material (this design) | EXTERNAL / INADMISSIBLE; viz and benchmark content; never evidence |
| 2 | Operator-supplied recordings | operator provenance; still not catalogue evidence unless admitted by the existing path |
| 3 | Live feeds | the existing ADS-B tool (`scythe-web/adsbPlugin.js`) already draws live aircraft as viz content |

Only Tier 1 is in scope here. The live ADS-B tool is the display path to reuse, not to rewrite.

## Import model

`ObservationSet` already holds `source_filename`, `source_sha256` and `preprocessing_history`
(`scythe_mission.py`). Licence and the External/Inadmissible marks are additive fields following the
pattern of #189/#190.

Decision proposed: **one ObservationSet per source file**, rolled up under a registry entry for the
corpus. The question "where did this observation originate?" then has a per-file answer and a
per-corpus answer. This is open decision 4 in the roadmap and is the user's call.

Preprocessing (VAD segmentation, resampling) is appended to `preprocessing_history`; a segment is a
derived artifact with the parent's hash recorded, not a replacement for it.

## Interpretation layer

- **Transcript.** No transcripts ship with the corpus, so this needs an ASR component that does not
  exist here. ASR output on ATC phraseology garbles callsigns; word and callsign confidence are stored,
  and a low-confidence callsign is not resolved to an aircraft silently.
- **Instruction event.** Heading, altitude, speed and frequency instructions parsed from the
  transcript. Parsing is a second fallible step with its own confidence.
- **Correlation.** Candidate match between an instruction and a track change in a time window. Stored
  as a claim: instruction, track, window, and outcome (consistent, inconsistent, not determinable).
  "Not determinable" is a legitimate outcome and must not be rounded to either of the others.

Each derived record names the tool and version that produced it, so it can be re-derived and compared.

## What it can honestly claim

Because the track has physics, a correlation can be checked: the instruction says turn, the track turns
or it does not. That makes this the first claim in this area that is verifiable rather than merely
displayable. It does not make the audio evidence, and a consistent outcome is not proof the aircraft
heard the instruction.

## Display

Timeline plus map, read-only. Instruction events appear as marks on the timeline with their
confidence; inconsistent and not-determinable outcomes are visible, not hidden. The panel is a
workspace tool under the lifecycle contract (`docs/workspace_lifecycle.md`).

## Known material

TartanAviation ATC + ADS-B (`twangodev/tartanaviation-atc-adsb`): public, ungated, CC-BY-4.0, CMU
AirLab, per its Hub metadata about 1,867 parquet files. Its card describes it as mostly silence, so
voice segments need VAD. Verified from API metadata only; the content has not been inspected by this
repo's tooling.

Staged locally: `assets/speech1.wav` (11.7 s) and `assets/speech2.wav` (15.0 s), 16 kHz mono 16-bit,
untracked. A MANIFEST and a `timeline_seed.json` described with this staging were not found on the
serving host; hash and licence must be regenerated before these count as provenance. If any of this is
published, CC-BY-4.0 requires crediting CMU AirLab.

## Open questions

1. ObservationSet granularity (per file under a registry entry, or per corpus).
2. Which ASR, and where it runs. It is a new component with its own failure modes.
3. Time base: how audio clip time aligns with ADS-B timestamps, and what error is tolerated.
4. Whether correlation outcomes are persisted or computed on view.
