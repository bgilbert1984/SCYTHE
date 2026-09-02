# ADR 0002 — Channel diversity is physical, never synthetic

- **Status:** Proposed
- **Date:** 2026-09-01
- **Condensed from** `docs/SparseSCYTHE.md` §§"What SPIFFI actually does" through
  "What transfers to RF", retained in full as the source discussion.
- **Related:** [ADR 0003](0003-rf-emission-tracking-hierarchy.md)

## Context

SPIFFI [1] splits fluorescence into four **simultaneous** linear-polarization
channels landing on one sCMOS sensor, then derives spatial fluctuations from those
physically distinct channels instead of from a long temporal stack. Reported:
~1.7× single-shot lateral resolution improvement, bead FWHM ~294 → ~173 nm, axial
~710 → ~385 nm — and ~80 nm **only** after combining spatial *and* temporal
processing, not from the single-shot path alone.

That last distinction is the part that belongs in an evidence model. The central
transferable idea is one sentence:

> Derive more information from physically meaningful channel diversity, not from
> synthetic duplication of one channel.

## Decision

### 1. Three optical authority layers, never collapsed

| Layer | Authority |
|---|---|
| Four camera-channel intensities | `OBSERVED_POLARIMETRIC_IMAGE` |
| Background-corrected registered channels | `DERIVED_OPTICAL_PRODUCT` |
| SPIFFI reconstruction / orientation / DoLP | `DERIVED_OPTICAL_INFERENCE` |

`CellDetection` is not widened in place. A separate `CellOpticalObservation`
carries the polarimetric fields (channels, orientation, degree of linear
polarization, anisotropy, reconstruction order, spatial-resolution estimate,
channel-registration error, authority) so the base observation stays uncontaminated.

### 2. Virtual products are counted separately from observations

SPIFFI generates fluctuation images by spatial interpolation and shuffling. Those
are **not** independent camera measurements. The record must read:

```
4 OBSERVED POLARIZATION CHANNELS
20 VIRTUAL FLUCTUATION PRODUCTS
```

and never `24 OBSERVATIONS`, with
`{"observed_channel_count": 4, "virtual_product_count": 20,
  "virtual_products_independent": false,
  "augmentation_authority": "DETERMINISTIC_DERIVATION"}`.

Otherwise downstream statistics treat interpolation variants as biological
replication.

### 3. Probe orientation is not structure orientation

Fluorophore orientation relates to structure orientation only through linker
flexibility, rotational averaging, labelling density and dye aggregation. Keep
`PROBE ORIENTATION // DERIVED` and `STRUCTURE ORIENTATION // HYPOTHESIS` apart.

### 4. Disagreement between reconstructions is preserved

Widefield detection, SPIFFI single-shot detection and temporal-stack
reconstruction are compared, and where they disagree the disagreement is retained
rather than resolved by picking the prettiest reconstruction. The motivating gain
is fewer false CellOps graph operations — erroneous split, erroneous merge, false
disappearance/reappearance, identity switch, duplicated trajectory, wrong
parent-child assignment — because single-shot capture removes the inter-frame
motion that temporal-stack super-resolution introduces for fast structures
(mitochondrial fission/fusion, microtubule remodelling, membrane deformation,
vesicle transport, short-lived contacts).

### 5. Provenance is a hypergraph, and every reconstruction is traceable

Node types `OPTICAL_EXPOSURE`, `POLARIZATION_CHANNEL`, `CHANNEL_REGISTRATION`,
`SPIFFI_RECONSTRUCTION`, `ORIENTATION_ESTIMATE`, `ANISOTROPY_ESTIMATE`,
`CELL_DETECTION`, `CELL_TRAJECTORY`; edges `DERIVED_FROM`, `REGISTERED_TO`,
`RECONSTRUCTED_AS`, `SUPPORTS_DETECTION`, `CONTRADICTS_DETECTION`,
`ASSOCIATED_WITH_TRAJECTORY`.

Each reconstruction retains: raw exposure id · four channel hashes · channel
angles · camera and objective configuration · registration transform ·
background-subtraction parameters · resampling method · correlation order ·
software revision · calibration artifact · resolution estimate · uncertainty ·
signal-chain hash.

### 6. What transfers to RF — and what cannot

The architectural analogy is exact: simultaneous polarization channels beat
sequentially rotating one antenna. A future RF node with orthogonal H/V (and
possibly ±45°, circular) antennas, simultaneous receivers, a shared clock and
gain/phase calibration could produce Stokes-like products and
polarization-dependent classification.

**One NESDR with one antenna cannot reproduce this in software.** Rotating one
antenna between observations mixes polarization differences with transmitter and
channel variation, which is precisely the confound simultaneity removes. This
is why ADR 0003 records a null `polarization_compatibility` as *not measured*
rather than as agreement.

## Consequences

- Commercially the plausible product is a **retrofit** four-channel polarimetric
  relay plus calibration target and reconstruction software for an existing
  fluorescence microscope, not a microscope built from scratch. Required optics
  are conventional: high-NA objective, non-polarizing 50:50 splitter, achromatic
  half-wave plate, two polarizing splitters, relay optics, sCMOS, registration
  target, polarization standard, stable mounts.
- The monocle inherits the *principle*, not the four free-space arms: a
  micro-polarizer sensor giving intensity, DoLP, angle of linear polarization and
  channel disagreement. Those are optical classifications — glare vs diffuse
  structure, stressed transparent material, wet surfaces, vegetation, sky
  polarization — and never proof of material identity.

## Open decisions

1. Whether to run the paper's own demonstration data through CellOps before
   committing to optics: ingest four channels → reproduce the single-shot
   reconstruction → export widefield and SPIFFI detections → run identical
   tracking on both → compare identity switches, split/merge errors and
   trajectory continuity → represent disagreement in the hypergraph → validate
   against nanoruler or bead ground truth.
2. Whether the optical product boundary gets its own modules mirroring the RF
   contract split (`spiffi_capture` / `spiffi_registration` /
   `spiffi_reconstruction` / `spiffi_contract` / `cellops_spiffi_ingest`).

## References

1. "SPIFFI enables single-shot super-resolution and multidimensional imaging."
   Nature Methods. <https://doi.org/10.1038/s41592-026-03196-6>
