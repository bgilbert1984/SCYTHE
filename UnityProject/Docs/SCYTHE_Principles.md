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
complex-baseband phase rotation before deterministic AWGN. The v0.3 model excluded antenna patterns,
polarization, occlusion, reflection, diffraction, and multipath; obstacles were spatial references rather
than RF propagation geometry.

## Multi-emitter environment milestone

Each transmitter has its own stable id, carrier frequency, symbol rate, modulation, power, position,
motion definition, and radiating state. The HUD reports links independently. The field map adds
isotropic power densities as an **incoherent visualization**; it does not model phase-coherent
interference between emitters.

Static colliders explicitly marked as RF occluders contribute a configurable scalar loss in dB:

```text
L_occlusion = blocker_count * loss_dB_per_blocker
amplitude_multiplier = 10 ^ (-L_occlusion / 20)
```

This is a geometric line-of-sight attenuation approximation. It is not a material model and does not
claim reflection, transmission coefficients, edge diffraction, scattering, multipath, or full-wave
electromagnetic behavior. The HUD and scenario manifest identify the approximation directly.

Transmitter motion and events are functions of deterministic simulation time. Events execute once in
manifest order and are not scheduled from rendering frame time.

## Optical ingestion and fusion boundary

A scenario may declare a solver-produced dataset below `Assets/OpticalDatasets/`. A declared dataset
must contain valid metadata plus matching `phase.exr` and `intensity.exr` assets; invalid or incomplete
declared data stops the build. If no dataset is declared, the runtime states
`NO SOLVER DATASET BUNDLED` and does not invent fallback physics.

The first fusion overlay is explicitly labeled **dataset space / unregistered**. Displaying an optical
texture over the monocle does not establish camera calibration, depth registration, pose registration,
or physical alignment with RF samples.

## Global propagation boundary

Planetary visualization does not turn Unity, Cesium, a shader, a voxel renderer, or a ray marcher into
a propagation solver. Global and regional layers must validate against
[`GlobalPropagationDataContract.schema.json`](./GlobalPropagationDataContract.schema.json) before
consumer integration.

Every global layer declares exactly one evidence class:

- **MEASURED**
- **SOLVER_OUTPUT**
- **REDUCED_ORDER**
- **SYNTHETIC**
- **ILLUSTRATIVE**

The contract records the solver/model authority, exact source identity, input and output hashes,
coordinate reference and vertical datum, time semantics, RF/optical parameters, quantity definition,
units, uncertainty, interpolation, no-data rules, and immutable-value LOD policy.

Derived Cesium or Unity tiles remain presentation products. They preserve lineage to the authoritative
asset and never replace it. `visualizationIsAuthoritative` is always false.
