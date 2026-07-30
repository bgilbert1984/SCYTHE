# Meep → SCYTHE authoritative dataset handoff

## Status of the bundled source

SCYTHE contains a Meep source archive at:

```text
/workspaces/codespaces-blank/assets/meep-master
```

The archive is useful, but it is not yet a reproducible executable solver installation.

| Item | Observed state |
|---|---|
| Version from `configure.ac` | `1.35.0-beta` |
| Version from `codemeta.json` | `1.11` (stale/inconsistent) |
| VCS revision | unavailable; archive contains no `.git` directory |
| Deterministic source-tree SHA-256 | `6184e2e843be20e9544b11f20bef4f0d82b303499fcd143bb02259f60887cf1a` |
| License declared by archive | `GPL-2.0-or-later` |
| Existing regression HDF5 | present |
| Runnable `meep` Python module | absent |
| MPI compiler/runtime | absent |
| HDF5 compiler/tooling | absent |
| `h5py` | absent |
| Guile/libctl toolchain | absent |

The tree digest identifies the exact archive contents without pretending it is a Git revision. A future
authoritative installation should replace `VCS_REVISION_UNAVAILABLE_IN_SOURCE_ARCHIVE` with an exact
upstream commit or signed release identifier.

## Scientific role

Meep is a finite-domain FDTD solver. Its SCYTHE role is appropriate for:

- optical phase and field-component grids;
- intensity or energy-density grids whose definitions are recorded;
- polarization components;
- near-to-far transformations;
- material and geometry studies;
- convergence-tested antenna or optical structures.

It is not a planetary propagation solver. A Meep dataset may be locally registered into a global
scene, but registration does not make the simulated domain global.

## One-way boundary

```text
Meep input + exact source revision
                 ↓
         Meep solver execution
                 ↓
    native HDF5 field/DFT output
                 ↓
SCYTHE contract packager (hashes only)
                 ↓
validated sidecar manifest + unchanged HDF5
                 ↓
     derived visualization products
                 ↓
       CesiumJS / Unity consumers
```

The contract packager never executes Meep, changes field values, invents physical scale, or converts
rendered colors back into scientific quantities.

## Contract tooling

Schema:

```text
UnityProject/Docs/GlobalPropagationDataContract.schema.json
```

Packager and validator:

```text
scripts/global_data/scythe_dataset_contract.py
```

Run the bundled regression fixture:

```bash
python3 scripts/global_data/scythe_dataset_contract.py package \
  --job scripts/global_data/examples/meep-regression-reference.job.json \
  --output /tmp/meep-regression-reference.manifest.json

python3 scripts/global_data/scythe_dataset_contract.py validate \
  /tmp/meep-regression-reference.manifest.json
```

Verify an assembled dataset and every payload:

```bash
python3 scripts/global_data/scythe_dataset_contract.py validate \
  dataset/manifest.json \
  --dataset-root dataset
```

Run contract tests:

```bash
python3 scripts/global_data/test_scythe_dataset_contract.py
```

## Required metadata before `SOLVER_OUTPUT`

A physical Meep export must declare:

- exact Meep release/commit and source-tree hash;
- simulation input hashes;
- execution environment;
- deterministic run identifier;
- physical wavelength;
- meters per Meep solver length unit;
- axes, origin, and optional geodetic registration;
- material model and boundary conditions;
- exact HDF5 dataset definitions and units;
- grid dimensions and resolution;
- uncertainty or convergence-study status;
- asset hashes, sizes, lineage, and dataset license.

If physical wavelength or length scale is absent, the validator only accepts the data as
`SYNTHETIC` or `ILLUSTRATIVE`.

## Existing fixture boundary

The example job wraps Meep's bundled `array-slice-ll-ref.h5`. The associated C++ source declares a
126-sample complex `Hz` slice and a 126×38 `Sy` slice at 20 samples per solver length unit.

The fixture is labeled `SYNTHETIC` because:

- it was not executed in this Codespace;
- it has no attached physical length scale;
- the archive has no VCS revision;
- it has no uncertainty or convergence record suitable for a SCYTHE physical claim.

Its purpose is to prove hashing, packaging, validation, and rejection behavior—not optical physics.

## Cesium and Unity consumer boundary

Cesium and Unity may:

- verify and load immutable assets;
- select time, frequency, depth, and LOD;
- sample declared quantities according to the interpolation policy;
- derive presentation tiles with recorded lineage;
- style every layer from its evidence class.

They may not:

- relabel normalized values as physical units;
- infer missing vertical datums or wavelength;
- treat interpolated LOD values as the authoritative grid;
- mix synthetic drills into solver layers;
- claim a ray-marched volume is a propagation calculation;
- silently replace missing solver output with random data.

`visualizationIsAuthoritative` is fixed to `false` by the schema.

## Solver installation gate

Before running new Meep studies, create a separate, pinned solver environment with MPI, parallel
HDF5, Meep's Python bindings, NumPy, and h5py. Record the environment lockfile or container digest in
`authority.executionEnvironment`.

Do not link the Meep solver into the Unity player. Unity consumes validated outputs only.
