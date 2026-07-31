#!/usr/bin/env python3
"""Run SCYTHE's first physically scaled, convergence-gated Meep study.

The experiment is a two-dimensional TE (Ez) plane wave at 650 nm crossing a
lossless, 400 nm-thick dielectric slab with refractive index 1.5. A vacuum
reference and slab case are executed at each spatial resolution. Meep writes
the final complex field with ``output_dft``; no later step modifies that HDF5.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
from typing import Any

import h5py
import meep as mp
import numpy as np


METERS_PER_SOLVER_UNIT = 1.0e-6
WAVELENGTH_NANOMETERS = 650.0
WAVELENGTH_SOLVER_UNITS = (
    WAVELENGTH_NANOMETERS * 1.0e-9 / METERS_PER_SOLVER_UNIT
)
CENTER_FREQUENCY = 1.0 / WAVELENGTH_SOLVER_UNITS
RESOLUTIONS = (20, 30, 40)
CONVERGENCE_RELATIVE_TOLERANCE = 0.03

CELL_SIZE = mp.Vector3(4.0, 1.5, 0)
PML_THICKNESS = 0.5
SLAB_THICKNESS = 0.4
SLAB_INDEX = 1.5
SOURCE_X = -1.25
TRANSMISSION_MONITOR_X = 1.10
DECAY_PROBE_X = 1.25
FIELD_REGION_CENTER = mp.Vector3(0, 0, 0)
FIELD_REGION_SIZE = mp.Vector3(3.0, 1.0, 0)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def conda_package_manifest(environment_prefix: Path) -> dict[str, Any]:
    records = []
    for record_path in sorted((environment_prefix / "conda-meta").glob("*.json")):
        record = json.loads(record_path.read_text(encoding="utf-8"))
        records.append(
            {
                "name": record["name"],
                "version": record["version"],
                "build": record["build"],
                "channel": record.get("channel"),
                "subdir": record.get("subdir"),
                "url": record.get("url"),
                "sha256": record.get("sha256"),
                "md5": record.get("md5"),
            }
        )
    if not records:
        raise RuntimeError(f"No Conda package records found in {environment_prefix}")
    return {
        "format": "SCYTHE_CONDA_PACKAGE_MANIFEST_1",
        "environmentPrefixRecordedForAuditOnly": str(environment_prefix),
        "packages": records,
    }


def run_case(
    resolution: int,
    with_slab: bool,
    capture_fields: bool,
    native_hdf5_stem: Path | None = None,
) -> dict[str, Any]:
    geometry = []
    if with_slab:
        geometry.append(
            mp.Block(
                center=mp.Vector3(),
                size=mp.Vector3(SLAB_THICKNESS, mp.inf, mp.inf),
                material=mp.Medium(index=SLAB_INDEX),
            )
        )

    simulation = mp.Simulation(
        cell_size=CELL_SIZE,
        boundary_layers=[mp.PML(PML_THICKNESS, direction=mp.X)],
        geometry=geometry,
        sources=[
            mp.Source(
                src=mp.GaussianSource(
                    frequency=CENTER_FREQUENCY,
                    fwidth=0.25 * CENTER_FREQUENCY,
                ),
                component=mp.Ez,
                center=mp.Vector3(SOURCE_X, 0, 0),
                size=mp.Vector3(0, CELL_SIZE.y, 0),
            )
        ],
        resolution=resolution,
        dimensions=2,
        k_point=mp.Vector3(),
        force_complex_fields=False,
    )

    flux_monitor = simulation.add_flux(
        CENTER_FREQUENCY,
        0,
        1,
        mp.FluxRegion(
            center=mp.Vector3(TRANSMISSION_MONITOR_X, 0, 0),
            size=mp.Vector3(0, CELL_SIZE.y, 0),
        ),
    )
    field_monitor = None
    if capture_fields:
        field_monitor = simulation.add_dft_fields(
            [mp.Ez],
            CENTER_FREQUENCY,
            0,
            1,
            center=FIELD_REGION_CENTER,
            size=FIELD_REGION_SIZE,
        )

    simulation.run(
        until_after_sources=mp.stop_when_fields_decayed(
            30,
            mp.Ez,
            mp.Vector3(DECAY_PROBE_X, 0, 0),
            1.0e-9,
        )
    )
    flux = float(mp.get_fluxes(flux_monitor)[0])
    result: dict[str, Any] = {
        "resolutionPixelsPerSolverUnit": resolution,
        "fluxMeepUnits": flux,
        "runTimeSolverUnits": float(simulation.meep_time()),
    }

    if field_monitor is not None:
        ez = np.asarray(
            simulation.get_dft_array(field_monitor, mp.Ez, 0),
            dtype=np.complex128,
        )
        if ez.ndim != 2:
            raise RuntimeError(f"Expected a 2D Ez array, received shape {ez.shape}")
        x, y, _z, weights = simulation.get_array_metadata(
            dft_cell=field_monitor
        )
        x = np.asarray(x, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        weights = np.asarray(weights, dtype=np.float64)
        if ez.shape != (x.size, y.size) or weights.shape != ez.shape:
            raise RuntimeError(
                "Meep field and coordinate metadata disagree: "
                f"Ez {ez.shape}, x {x.shape}, y {y.shape}, weights {weights.shape}"
            )
        result["ez"] = ez
        result["x"] = x
        result["y"] = y
        result["integration_weights"] = weights
        if native_hdf5_stem is None:
            raise RuntimeError("Final field capture requires a native HDF5 output stem.")
        native_hdf5_stem.parent.mkdir(parents=True, exist_ok=True)
        simulation.output_dft(field_monitor, str(native_hdf5_stem))
        result["native_hdf5_path"] = Path(f"{native_hdf5_stem}.h5")

    simulation.reset_meep()
    return result


def convergence_results(
    native_hdf5_stem: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records = []
    final_fields: dict[str, Any] | None = None
    for resolution in RESOLUTIONS:
        reference = run_case(resolution, with_slab=False, capture_fields=False)
        slab = run_case(
            resolution,
            with_slab=True,
            capture_fields=resolution == RESOLUTIONS[-1],
            native_hdf5_stem=(
                native_hdf5_stem if resolution == RESOLUTIONS[-1] else None
            ),
        )
        if reference["fluxMeepUnits"] <= 0:
            raise RuntimeError(
                f"Non-positive vacuum reference flux at resolution {resolution}"
            )
        transmission = slab["fluxMeepUnits"] / reference["fluxMeepUnits"]
        records.append(
            {
                "resolutionPixelsPerSolverUnit": resolution,
                "vacuumReferenceFluxMeepUnits": reference["fluxMeepUnits"],
                "slabFluxMeepUnits": slab["fluxMeepUnits"],
                "normalizedTransmission": transmission,
                "vacuumRunTimeSolverUnits": reference["runTimeSolverUnits"],
                "slabRunTimeSolverUnits": slab["runTimeSolverUnits"],
            }
        )
        if resolution == RESOLUTIONS[-1]:
            final_fields = slab

    for index, record in enumerate(records):
        if index == 0:
            record["relativeChangeFromPrevious"] = None
            continue
        previous = records[index - 1]["normalizedTransmission"]
        current = record["normalizedTransmission"]
        record["relativeChangeFromPrevious"] = abs(current - previous) / abs(current)

    last_change = records[-1]["relativeChangeFromPrevious"]
    passed = bool(last_change <= CONVERGENCE_RELATIVE_TOLERANCE)
    summary = {
        "metric": "normalized transmitted flux through dielectric slab",
        "comparison": "absolute consecutive change divided by current value",
        "toleranceRelative": CONVERGENCE_RELATIVE_TOLERANCE,
        "finestPairRelativeChange": last_change,
        "passed": passed,
        "records": records,
    }
    if final_fields is None:
        raise RuntimeError("Final field capture was not produced.")
    return records, {"summary": summary, "fields": final_fields}


def grid_metadata(fields: dict[str, Any]) -> dict[str, Any]:
    return {
        "format": "SCYTHE_MEEP_GRID_METADATA_1",
        "source": "Meep Simulation.get_array_metadata(dft_cell=field_monitor)",
        "metersPerSolverUnit": METERS_PER_SOLVER_UNIT,
        "xSolverUnits": fields["x"].tolist(),
        "ySolverUnits": fields["y"].tolist(),
        "integrationWeightsSolverUnitsSquared": fields[
            "integration_weights"
        ].tolist(),
    }


def hdf5_payload_hashes(path: Path) -> dict[str, Any]:
    datasets = {}
    with h5py.File(path, "r") as source:
        for name in sorted(source.keys()):
            values = np.asarray(source[name][...])
            datasets[name] = {
                "shape": list(values.shape),
                "dtype": str(values.dtype),
                "sha256": hashlib.sha256(
                    values.tobytes(order="C")
                ).hexdigest(),
            }
    return {
        "format": "SCYTHE_HDF5_DATASET_PAYLOAD_HASHES_1",
        "sourceAsset": path.name,
        "note": (
            "Hashes cover uncompressed dataset value bytes in C order. They "
            "exclude HDF5 container metadata such as object timestamps."
        ),
        "datasets": datasets,
    }


def build_package_job(
    hdf5_path: Path,
    convergence_path: Path,
    grid_metadata_path: Path,
    payload_hashes_path: Path,
    environment_manifest_path: Path,
    script_path: Path,
    environment_lock_path: Path,
    environment_spec_path: Path,
    fields: dict[str, Any],
    generated_utc: str,
) -> dict[str, Any]:
    x_spacing = float(np.median(np.diff(fields["x"])))
    y_spacing = float(np.median(np.diff(fields["y"])))
    job_directory = hdf5_path.parent

    def relative_source(path: Path) -> str:
        return Path(os.path.relpath(path, job_directory)).as_posix()

    return {
        "inputs": [
            {
                "sourcePath": relative_source(script_path),
                "logicalPath": "inputs/slab_convergence.py",
            },
            {
                "sourcePath": relative_source(environment_lock_path),
                "logicalPath": "inputs/environment.explicit.txt",
            },
            {
                "sourcePath": relative_source(environment_spec_path),
                "logicalPath": "inputs/environment.yml",
            },
            {
                "sourcePath": relative_source(environment_manifest_path),
                "logicalPath": "inputs/environment-packages.json",
            },
        ],
        "assets": [
            {
                "sourcePath": relative_source(hdf5_path),
                "datasetPath": "meep-slab-650nm.h5",
                "role": "AUTHORITATIVE_VALUES",
                "mediaType": "application/x-hdf5",
            },
            {
                "sourcePath": relative_source(convergence_path),
                "datasetPath": "convergence.json",
                "role": "UNCERTAINTY",
                "mediaType": "application/json",
            },
            {
                "sourcePath": relative_source(grid_metadata_path),
                "datasetPath": "grid-metadata.json",
                "role": "COORDINATES",
                "mediaType": "application/json",
            },
            {
                "sourcePath": relative_source(payload_hashes_path),
                "datasetPath": "field-payload-hashes.json",
                "role": "OTHER",
                "mediaType": "application/json",
            },
        ],
        "manifest": {
            "schemaVersion": "1.0",
            "datasetId": "meep-slab-650nm-convergence-v1",
            "title": "Meep 650 nm dielectric slab convergence study",
            "description": (
                "Complex Ez, phase, and relative intensity for a 650 nm TE plane "
                "wave crossing a 400 nm lossless dielectric slab (n=1.5)."
            ),
            "evidenceClass": "SOLVER_OUTPUT",
            "authority": {
                "solverName": "Meep",
                "solverVersion": mp.__version__,
                "modelName": "Two-dimensional FDTD dielectric slab transmission",
                "standardRevision": None,
                "sourceRevision": (
                    "conda-forge:pymeep-1.34.0-"
                    "mpi_mpich_py311hef964db_0"
                ),
                "sourceTreeSha256": None,
                "provenanceStatus": "COMPLETE",
                "solverLicense": "GPL-2.0-or-later",
                "datasetLicense": "CC-BY-4.0",
                "runId": "scythe-meep-slab-650nm-r20-30-40-v1",
                "deterministic": True,
                "executionEnvironment": (
                    f"{platform.platform()}; Python {platform.python_version()}; "
                    "single MPI rank; exact Conda package URLs and package hashes "
                    "are recorded as inputs"
                ),
                "inputHashes": [],
            },
            "spatialReference": {
                "type": "LOCAL_CARTESIAN",
                "axes": "x-right,y-up; two-dimensional Meep cell",
                "distanceUnits": "solver_length_unit",
                "metersPerSolverUnit": METERS_PER_SOLVER_UNIT,
                "originDescription": (
                    "Center of the dielectric slab and simulation cell; no "
                    "geodetic registration."
                ),
                "geodeticRegistration": None,
            },
            "temporal": {
                "generatedUtc": generated_utc,
                "validFromUtc": None,
                "validToUtc": None,
                "statisticalTimePercentage": None,
                "timeSemantics": "STATIC",
            },
            "physics": {
                "domain": "OPTICAL",
                "rf": None,
                "optical": {
                    "wavelengthNanometers": WAVELENGTH_NANOMETERS,
                    "frequencySolverUnits": CENTER_FREQUENCY,
                    "polarizationRepresentation": "FIELD_COMPONENTS",
                    "materialModel": (
                        "Lossless nondispersive slab, refractive index 1.5, "
                        "thickness 0.4 solver units (400 nm)."
                    ),
                    "boundaryConditions": (
                        "0.5 solver-unit x-directed PML; periodic y boundary; "
                        "normally incident Ez line source."
                    ),
                },
            },
            "quantity": {
                "name": "Complex Ez optical field",
                "definition": (
                    "Native Meep output_dft frequency-domain Ez real and imaginary "
                    "components at 650 nm. Phase and relative intensity are declared "
                    "consumer derivations angle(Ez) and abs(Ez)^2, not authoritative "
                    "datasets in this package."
                ),
                "units": "Meep normalized electric-field units",
                "valueSemantics": "COMPLEX_AMPLITUDE",
                "complexRepresentation": "REAL_IMAGINARY",
                "uncertainty": {
                    "kind": "NUMERICAL_CONVERGENCE",
                    "description": (
                        "Normalized transmitted flux is compared at 20, 30, and "
                        "40 pixels per micrometre; the finest-pair relative change "
                        "must not exceed 3%."
                    ),
                    "assetPath": "convergence.json",
                },
            },
            "grid": {
                "representation": "HDF5",
                "dimensions": [int(fields["ez"].shape[0]), int(fields["ez"].shape[1])],
                "resolution": [x_spacing, y_spacing],
                "noData": {"policy": "NONE", "value": None},
                "interpolation": "BILINEAR",
                "authoritativeAssetPath": "meep-slab-650nm.h5",
                "lodPolicy": {
                    "authoritativeValuesImmutable": True,
                    "derivedTilesAllowed": True,
                    "aggregationMethod": (
                        "Any future visualization tile must be separately labeled "
                        "and retain lineage to this HDF5 checksum."
                    ),
                    "description": (
                        "The native HDF5 values are immutable; LOD products may "
                        "support visualization but cannot replace them."
                    ),
                },
            },
            "assets": [],
            "lineage": {
                "parentDatasetIds": [],
                "transformations": [],
            },
            "visualizationIsAuthoritative": False,
        },
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--environment-lock", type=Path, required=True)
    parser.add_argument("--environment-spec", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    output_directory = arguments.output.resolve()
    output_directory.mkdir(parents=True, exist_ok=True)
    environment_prefix = Path(sys.prefix).resolve()
    script_path = Path(__file__).resolve()
    environment_lock_path = arguments.environment_lock.resolve()
    environment_spec_path = arguments.environment_spec.resolve()
    if not environment_lock_path.is_file() or not environment_spec_path.is_file():
        raise RuntimeError("Environment lock and specification must both exist.")

    mp.verbosity(0)
    generated_utc = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    pending_hdf5_stem = output_directory / ".meep-slab-650nm.pending"
    pending_hdf5_path = Path(f"{pending_hdf5_stem}.h5")
    if pending_hdf5_path.exists():
        pending_hdf5_path.unlink()
    records, result = convergence_results(pending_hdf5_stem)
    convergence = result["summary"]
    fields = result["fields"]

    convergence_document = {
        "format": "SCYTHE_NUMERICAL_CONVERGENCE_1",
        "generatedUtc": generated_utc,
        "experiment": {
            "wavelengthNanometers": WAVELENGTH_NANOMETERS,
            "metersPerSolverUnit": METERS_PER_SOLVER_UNIT,
            "slabThicknessMeters": SLAB_THICKNESS * METERS_PER_SOLVER_UNIT,
            "slabRefractiveIndex": SLAB_INDEX,
            "polarization": "Ez (two-dimensional TE)",
            "randomnessUsed": False,
        },
        **convergence,
    }
    convergence_path = output_directory / "convergence.json"
    atomic_json(convergence_path, convergence_document)
    if not convergence["passed"]:
        if pending_hdf5_path.exists():
            pending_hdf5_path.unlink()
        print(
            "[SCYTHE MEEP] FAIL convergence: "
            f"{convergence['finestPairRelativeChange']:.6g} > "
            f"{CONVERGENCE_RELATIVE_TOLERANCE:.6g}",
            file=sys.stderr,
        )
        return 2

    if fields["native_hdf5_path"] != pending_hdf5_path:
        raise RuntimeError("Meep native output path did not match the requested path.")
    hdf5_path = output_directory / "meep-slab-650nm.h5"
    os.replace(pending_hdf5_path, hdf5_path)

    environment_manifest_path = output_directory / "environment-packages.json"
    atomic_json(
        environment_manifest_path,
        conda_package_manifest(environment_prefix),
    )
    grid_metadata_path = output_directory / "grid-metadata.json"
    atomic_json(grid_metadata_path, grid_metadata(fields))
    payload_hashes_path = output_directory / "field-payload-hashes.json"
    payload_hashes = hdf5_payload_hashes(hdf5_path)
    atomic_json(payload_hashes_path, payload_hashes)

    job_path = output_directory / "package.job.json"
    atomic_json(
        job_path,
        build_package_job(
            hdf5_path,
            convergence_path,
            grid_metadata_path,
            payload_hashes_path,
            environment_manifest_path,
            script_path,
            environment_lock_path,
            environment_spec_path,
            fields,
            generated_utc,
        ),
    )
    run_summary = {
        "passed": True,
        "hdf5": str(hdf5_path),
        "hdf5Sha256BeforePackaging": sha256_file(hdf5_path),
        "hdf5DatasetPayloads": payload_hashes["datasets"],
        "packageJob": str(job_path),
        "finestPairRelativeChange": convergence["finestPairRelativeChange"],
        "fieldShape": list(fields["ez"].shape),
        "records": records,
    }
    atomic_json(output_directory / "run-summary.json", run_summary)
    print(
        "[SCYTHE MEEP] PASS "
        f"finest-pair change={convergence['finestPairRelativeChange']:.6g}; "
        f"field shape={fields['ez'].shape}; "
        f"HDF5 SHA-256={run_summary['hdf5Sha256BeforePackaging']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
