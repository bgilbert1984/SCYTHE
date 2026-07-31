# SCYTHE Meep 650 nm dielectric-slab study

## Result

The study passed its predeclared numerical convergence gate and produced SCYTHE's first physical
dataset classified as `SOLVER_OUTPUT`.

Dataset:

```text
datasets/meep-slab-650nm-convergence-v1/
```

Authoritative payload:

```text
meep-slab-650nm.h5
SHA-256 600de612f97d1da193583db8d8f2c57313601b7d45af5efd8c1f2e13d6fbf373
```

The package and asset-integrity validators pass. Packaging does not change the accepted HDF5
checksum.

Independent executions produced bit-identical numerical datasets:

```text
ez_0.r  d97bfd9e2475c05923cc784d45b06c931c07dfbe98812fd71cfadd9dbe4279d2
ez_0.i  3576ff276576513b0e63b6d616934e37cd09f2a723bee6c917a7e93dd42791e1
```

The native HDF5 files themselves are not byte-identical because Meep/HDF5 records object timestamps
in container metadata. `field-payload-hashes.json` therefore separates the reproducibility claim
about scientific array values from the integrity checksum of the accepted container.

## Experiment

- Solver: Meep `1.34.0`
- Conda build: `mpi_mpich_py311hef964db_0`
- Execution: one MPI rank
- Model: two-dimensional FDTD
- Polarization: TE, `Ez`
- Wavelength: 650 nm
- Physical scale: one solver length unit = 1 µm
- Material: lossless, nondispersive dielectric with refractive index 1.5
- Slab thickness: 400 nm
- Boundaries: 0.5 µm x-directed PML, periodic y
- Randomness: none

The authoritative HDF5 is written directly by Meep's `output_dft` routine and contains complex `Ez`
as real and imaginary datasets. It is never reopened for modification. The companion
`grid-metadata.json` contains the coordinates and cubature weights returned by Meep's
`get_array_metadata` routine.

Phase and relative intensity are declared consumer derivations, exactly `angle(Ez)` and
`abs(Ez)**2`. They are not stored in or promoted to the authoritative HDF5.

## Convergence evidence

The tested scalar is transmitted flux through the slab divided by a separate vacuum-reference flux
at the same spatial resolution.

| Resolution (pixels/µm) | Vacuum flux | Slab flux | Normalized transmission | Relative change |
|---:|---:|---:|---:|---:|
| 20 | 0.6495563826 | 0.6382395875 | 0.9825776555 | — |
| 30 | 0.6240865412 | 0.6071899269 | 0.9729258473 | 0.9920% |
| 40 | 0.6376287031 | 0.6179234408 | 0.9690960238 | 0.3952% |

Acceptance requires the relative change between the two finest resolutions to be no more than 3%.
The observed value is 0.3952%, so the study passes.

## Provenance

`environment.yml` records the human-readable environment. `environment.explicit.txt` freezes exact
package URLs and builds. `environment-packages.json` records the URL, build, SHA-256, and MD5 from
all 134 installed Conda package records. The solver script, both environment descriptions, package
manifest, convergence evidence, and native HDF5 are hashed by the contract pipeline.

The bundled `/assets/meep-master` archive was not used to execute this study. It remains a separately
audited source fixture with ambiguous internal version metadata.

## Claim boundary

This result is solver output, not measurement and not a validation against laboratory data. The
convergence test quantifies one discretization indicator—normalized transmitted flux—and does not
prove universal convergence of every grid sample.

The dataset is a finite local optical domain with no WGS84, ECEF, vertical datum, camera
calibration, or terrain registration. Cesium and Unity may consume it only as a local,
evidence-labeled optical layer until an explicit registration transform is produced and validated.
