# SCYTHE simulation principles

## Scientific boundary

- Unity visualizes precomputed physics and documented reduced-order models.
- Unity shaders are not described as Maxwell, FDTD, RCWA, or quantum solvers.
- Approximation, normalization, coordinate systems, and units are explicit.
- A CPU reference implementation precedes each performance-oriented GPU implementation.
- Synthetic data is labeled synthetic and never presented as solver output.

## Evidence labels

Every user-visible claim belongs to one of four levels:

- **Demonstrated** — supported by an identified experiment or validated source.
- **Simulated** — produced by an identified model with parameters and provenance.
- **Hypothesized** — a research target that has not been demonstrated.
- **Illustrative** — presentation or narrative content without scientific meaning.

## Reproducibility

- Every scenario has a versioned manifest and deterministic seed.
- RF signal generation uses sample indices rather than rendering frame time.
- Generated datasets record their solver, solver version, physical sampling, units, and provenance.
- Validation failures stop automated builds rather than being hidden by the presentation layer.

## Optical dataset contract

An optical dataset is incomplete without `metadata.json`. The authoritative schema is
[`OpticalDataContract.schema.json`](./OpticalDataContract.schema.json).

The intended directory layout is:

```text
dataset/
  metadata.json
  phase.exr
  intensity.exr
  polarization.exr
  depth_planes/
  lane_masks/
```

Phase is stored as float32 radians. Unity computes phase differences with wrap-safe angular
subtraction. Intensity is either in `W/m^2` or explicitly normalized. Polarization is documented as
Stokes or complex Jones data. Depth-plane positions and lane-label semantics live in metadata.

## First RF milestone

The first executable model contains one transmitter, one receiver, selectable ASK/FSK/BPSK/QPSK
complex-baseband modulation, an isotropic free-space attenuation approximation, deterministic AWGN,
decoded-bit comparison, and an explicitly labeled power-density overlay.

## Mobile spatial-probe milestone

The receiver is mounted on a first-person operator whose translation advances on the fixed simulation
clock. Position, range, bearing, received power, and radial velocity are sampled from scene transforms.
The declared one-way Doppler model is:

```text
f_d = v_radial * f_carrier / c
```

Positive radial velocity means motion toward the transmitter. The channel applies this offset as a
complex-baseband phase rotation before deterministic AWGN. The model still excludes antenna patterns,
polarization, occlusion, reflection, diffraction, and multipath; obstacles are spatial references rather
than RF propagation geometry.
