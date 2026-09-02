# ADR 0003 — Detection, track, transmitter and location are four layers

- **Status:** Proposed
- **Date:** 2026-09-01
- **Condensed from** `docs/SparseSCYTHE.md` §§"Direct conceptual mapping" through
  "Best next SCYTHE module", retained in full as the source discussion.
- **Related:** [ADR 0001](0001-sparse-recovery-validation.md),
  [ADR 0002](0002-polarimetric-channel-diversity.md),
  `docs/RF_Signal_Family_Classifier_Scope.md`

## Context

Cell tracking and signal tracking are the same problem: persistent identity under
noisy, incomplete observation. A bright fluorescent region is not a cell; a
spectral peak is not a transmitter. Both are observations from which persistent
entities must be *inferred*.

| Cell imaging | RF |
|---|---|
| Camera exposure | FFT observation window |
| Polarization channel | Antenna polarization / receiver channel |
| Cell centroid | Peak frequency and observation time |
| Cell morphology | Bandwidth, spectral shape, sidebands |
| Molecular orientation | RF polarization state |
| Cell trajectory | Frequency-time track |
| Cell division | One transmitter begins emitting related carriers |
| Cell fusion / overlap | Multiple emitters in one spectral region |
| Identity switch | Wrongly associating an emission with another transmitter |
| Imaging artifact | Receiver spur, overload, alias, gain transient |

The transferable discipline, in one sentence:

> Do not classify isolated detections in isolation. Maintain identity hypotheses
> over time, preserve splits and collisions, and let later evidence repair earlier
> uncertainty.

## Decision

### 1. Four persistent graph layers, never collapsed

```
RF_DETECTION            an individual measurement
EMISSION_TRACK          detections linked across time and frequency
TRANSMITTER_HYPOTHESIS  one physical source potentially owning several tracks
LOCATION_HYPOTHESIS     a probability distribution over geographic space
```

A carrier at 433.920 MHz is an observation. A repeating series of matching bursts
is an emission track. "Garage-door remote ABC123" is a transmitter hypothesis. Its
location is a further inference.

```
sensor:NESDR-14530058
  └─ OBSERVED → detection:rfd-001
       └─ ASSOCIATED_WITH → track:433-hopset-7
            └─ POSSIBLY_EMITTED_BY → transmitter:hyp-19
                 └─ POSSIBLY_LOCATED_IN → georegion:posterior-44
```

**Every edge carries its own confidence and authority.** The confidence of a
detection must never propagate into transmitter identity or location. Collapsing
these layers would make the graph pleasantly simple and evidentially radioactive.

### 2. Association keeps its components, and a null is not a match

Detections associate on frequency proximity and drift, bandwidth, spectral-envelope
similarity, burst cadence, modulation family, sideband spacing, occupied-channel
sequence, clock offset or symbol timing, protocol fingerprint, polarization,
received-power evolution, sensor coverage and time continuity — combined as a
weighted score whose **components are retained, not just the total**:

```json
{"frequency_continuity": 0.94, "spectral_shape_similarity": 0.87,
 "cadence_similarity": 0.91, "modulation_compatibility": 0.76,
 "polarization_compatibility": null, "combined_score": 0.88}
```

A `null` polarization value means *it was not measured* — not that polarization
matched. Per ADR 0002, one NESDR with one antenna cannot measure it at all.

### 3. Tracks have declared states

`STATIONARY` · `DRIFTING` · `HOPPING` · `BURSTING` · `SWEEPING` · `MULTICARRIER` ·
`CHIRPED` · `INTERMITTENT`. An emission traverses feature space `(f_t, B_t, P_t,
M_t)` the way a cell traverses physical space.

### 4. Branching preserves competing explanations

Relationships `PARENT_CARRIER`, `HARMONIC_OF`, `SIDEBAND_OF`,
`FREQUENCY_HOP_SUCCESSOR`, `SIMULCAST_WITH`, `CLOCK_COHERENT_WITH`,
`PROTOCOL_SESSION_OF`, `POSSIBLY_SAME_TRANSMITTER` are spectral relationships, not
physical identity. Two harmonically related signals may come from one transmitter,
receiver intermodulation, LO leakage, an overloaded frontend, or unrelated
emitters. GraphOps retains the competing worlds rather than choosing:

```
WORLD A // COMMON TRANSMITTER
WORLD B // RECEIVER INTERMODULATION
WORLD C // INDEPENDENT EMITTERS
```

### 5. Collisions stay ambiguous until evidence resolves them

Two signals in one analysis bin, simultaneous analogue and digital energy, partial
co-channel overlap, a weak emission beneath a strong carrier, sidebands crossing
neighbours, or overload-generated false components yield
`{"outcome": "INSUFFICIENT_EVIDENCE", "reason_code": "COLLISION_UNRESOLVED",
  "candidate_tracks": [...]}` — the shape ADR 0001 §5 adopts. Later observations
may resolve it, as separated cells restore their trajectories.

### 6. Re-identification separates stable from volatile features

| More stable | Less stable |
|---|---|
| hardware clock offset · carrier-frequency offset · turn-on transient · PA nonlinearity · I/Q imbalance · spectral regrowth · symbol-rate error · burst timing · persistent modulation parameters | received power · exact centre frequency · channel · packet identifiers · IP address · position · antenna orientation |

This parallels GraphOps identity across IP and ASN churn: volatile attributes
change while deeper physical traits persist. A match stays
`authority: DERIVED_IDENTITY_HYPOTHESIS` unless independently corroborated.

### 7. A track is not a location

| Configuration | Supports | Cannot determine |
|---|---|---|
| One stationary NESDR | presence, spectral character, temporal behaviour, relative power change, coarse fox-hunting under controlled movement | bearing, range, latitude/longitude, altitude |
| One mobile receiver | an RSSI trajectory over verified GPS positions → a probabilistic location surface | a position, where attenuation and antenna orientation dominate power |
| Multiple receivers | RSSI/path-loss, TDOA, AoA, Doppler, polarization consistency, propagation models | anything, without the metadata below |

Required per receiver: coordinates and uncertainty · clock quality · antenna
pattern and orientation · gain and cable loss · centre-frequency calibration ·
observation timestamp · signal-chain hash. Without those, localization turns into
an expensive horoscope.

## Consequences

The signal-family contract in `rf_signal_family.py` already supplies honest
association features — `symbol_rate_hz`, `method`, verdict window, `confidence`,
`reason_code` — and already withholds `emitter_identity`, `content`,
`modulation_order` and constant-envelope digital. Tracking therefore inherits a
classifier that cannot contaminate identity association with weak family labels;
symbol-rate stability across a channel change becomes a usable association feature
precisely because a DIGITAL verdict is hard to obtain.

## Open decisions

1. **First release scope**, proposed as: associate bounded detections into
   frequency-time tracks · show lifecycle and gaps · support stationary, drift,
   hopping and bursting states · preserve ambiguous associations · **create no
   geographic claim** · anchor every track to the NESDR sensor location · let
   GraphOps compare competing transmitter hypotheses.
2. Module split, proposed as `rf_track_model.py`, `rf_track_associator.py`,
   `rf_transmitter_hypotheses.py`, `rf_location_hypotheses.py`, with
   `rfTrackView.js`, `rfTrackTimeline.js`, `rfTrackEvidencePanel.js`.
3. Sequencing against the Phase 1–3 classifier work, which the source document
   argues should come first so that classification features are trustworthy before
   they are used for association.

## The target statement

Not "there was energy at 433.92 MHz", but:

> A persistent emission process appeared 87 times, changed channels four times,
> retained the same cadence and oscillator behaviour, may match two earlier
> tracks, and was observed by these receivers — but its physical transmitter
> identity and location remain provisional.
