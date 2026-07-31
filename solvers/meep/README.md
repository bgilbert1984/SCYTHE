# SCYTHE pinned Meep solver environment

This environment is separate from Unity. It runs authoritative finite-domain studies and emits
unchanged HDF5 plus SCYTHE contract metadata.

The human-readable specification pins:

- Python 3.11;
- PyMeep 1.34.0, MPICH build `mpi_mpich_py311hef964db_0`;
- MPICH;
- HDF5 and h5py;
- NumPy;
- the JSON Schema validator used by the SCYTHE packaging gate.

After solving, `environment.explicit.txt` records the exact conda package URLs and build hashes selected
for every transitive dependency.

Create or recreate the environment:

```bash
/opt/conda/bin/conda env create \
  --prefix /workspaces/codespaces-blank/.solver-envs/meep-1.34.0 \
  --file /workspaces/codespaces-blank/solvers/meep/environment.yml \
  --solver libmamba
```

Run commands without shell activation:

```bash
/opt/conda/bin/conda run \
  --prefix /workspaces/codespaces-blank/.solver-envs/meep-1.34.0 \
  python -c 'import meep as mp; print(mp.__version__)'
```

The Conda PyMeep package is a Python interface and intentionally does not provide Meep's Scheme
interface. Guile is therefore outside this solver environment. libctl and other runtime libraries are
resolved transitively and pinned by the explicit lock.

## First convergence-gated study

Run the 650 nm dielectric-slab study from the repository root:

```bash
/opt/conda/bin/conda run \
  --prefix .solver-envs/meep-1.34.0 \
  python solvers/meep/studies/slab_convergence.py \
  --output datasets/meep-slab-650nm-convergence-v1 \
  --environment-lock solvers/meep/environment.explicit.txt \
  --environment-spec solvers/meep/environment.yml
```

The study executes vacuum-reference and dielectric-slab cases at 20, 30, and
40 pixels per micrometre. It emits solver output only when the normalized
transmission change between the two finest resolutions is at most 3%.

Package and validate the passing output:

```bash
/opt/conda/bin/conda run \
  --prefix .solver-envs/meep-1.34.0 \
  python scripts/global_data/scythe_dataset_contract.py package \
  --job datasets/meep-slab-650nm-convergence-v1/package.job.json \
  --output datasets/meep-slab-650nm-convergence-v1/manifest.json

/opt/conda/bin/conda run \
  --prefix .solver-envs/meep-1.34.0 \
  python scripts/global_data/scythe_dataset_contract.py validate \
  datasets/meep-slab-650nm-convergence-v1/manifest.json \
  --dataset-root datasets/meep-slab-650nm-convergence-v1
```
