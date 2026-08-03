# SCYTHE NTIA ITM fixture pipeline

This pipeline pins the official [NTIA ITS Irregular Terrain Model](https://github.com/NTIA/itm)
at commit `183ad95bd813a8be11009df396e1c631356864b2`
(`v1.4-43-g183ad95`). It generates a small San Francisco Bay area-mode
path-loss fixture. The fixture is authoritative only as immutable output of
the declared model and inputs; it is not a measurement or a site-specific
claim about current RF coverage.

Install a C++ compiler if this AlmaLinux VM does not yet have one:

```bash
sudo dnf install gcc-c++
```

Build and regression-check the pinned solver, then regenerate the fixture:

```bash
./solvers/itm/build_ntia_itm_linux.sh
.venv/bin/python solvers/itm/generate_regional_fixture.py \
  --itm-executable solvers/itm/build/scythe-itm-area
```

The build helper verifies the clean upstream source-tree SHA-256 before making
Linux-only include-separator changes in a disposable copy. Its final line must
report approximately `152.505191390576` dB, matching the first official NTIA
`area.csv` regression value of 152.5 dB within its published one-decimal
precision.

The generator independently evaluates nested 33×33 and 65×65 grids, checks
every shared valid coordinate, verifies the official regression value, and
enforces the compact encoding error bound. It writes:

- `path-loss.float64le`: authoritative solver values;
- `path-loss.u16le`: derived browser visualization values;
- `tile-metadata.json`: checksum-bound `scale`, `offset`, byte order, shape,
  bounds, and no-data semantics;
- `convergence.json`: regression, nested-grid, and quantization results;
- `manifest.json`: output of the Python Global Contract v1 gate.

Cells within 1 km of the transmitter are no-data because the declared ITM
model range begins above 1 km. The contract reports solver uncertainty as
`NOT_QUANTIFIED`; the independently bounded Uint16 quantization error is not
misrepresented as physical uncertainty.
