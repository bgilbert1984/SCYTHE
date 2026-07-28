# SCYTHE optical datasets

Place solver-produced datasets in a named directory below this folder and set
`opticalDatasetRelativeDirectory` in the scenario manifest to that directory name.

Required files:

```text
dataset-name/
  metadata.json
  phase.exr
  intensity.exr
```

Optional files follow the contract in `Docs/OpticalDataContract.schema.json`:

```text
  polarization.exr
  depth_planes/
  lane_masks/
```

The build validates declared metadata, required EXR assets, dimensions, and depth-plane counts. A
declared invalid or incomplete dataset stops the build. No synthetic fallback is presented as solver
output.
