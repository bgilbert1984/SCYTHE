#!/usr/bin/env python3
"""Generate and package the deterministic SCYTHE regional ITM RF fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "scripts" / "global_data"))
from scythe_dataset_contract import atomic_write_json, package_job, sha256_file  # noqa: E402


DATASET_ID = "ntia-itm-sf-bay-area-v1"
ITM_REVISION = "183ad95bd813a8be11009df396e1c631356864b2"
ITM_SOURCE_TREE_SHA256 = "d655560f08c4720f4cec8b626c5f26388e8ca4cc63cb7bf48e6430507f7c0466"
TX_LONGITUDE = -122.4194
TX_LATITUDE = 37.7749
GRID_SIZE = 33
BOUNDS = (-122.5994, 37.5949, -122.2394, 37.9549)
SCALE_DB = 0.01
OFFSET_DB = 0.0
NO_DATA_RAW = 65535

MODEL_INPUTS = {
    "predictionMode": "AREA_TLS",
    "hTxMeters": 30.0,
    "hRxMeters": 1.5,
    "txSitingCriteria": 1,
    "rxSitingCriteria": 0,
    "deltaHMeters": 90.0,
    "climate": 5,
    "surfaceRefractivityNUnits": 301.0,
    "frequencyMHz": 900.0,
    "polarization": 1,
    "relativePermittivity": 15.0,
    "conductivitySiemensPerMeter": 0.005,
    "variabilityMode": 3,
    "timePercentage": 50.0,
    "locationPercentage": 50.0,
    "situationPercentage": 50.0,
    "minimumModelDistanceKm": 1.0,
}


def haversine_km(longitude: float, latitude: float) -> float:
    radius_km = 6371.0088
    phi1 = math.radians(TX_LATITUDE)
    phi2 = math.radians(latitude)
    dphi = phi2 - phi1
    dlambda = math.radians(longitude - TX_LONGITUDE)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * radius_km * math.asin(math.sqrt(a))


def run_itm(executable: Path, distances: list[float], inputs: dict[str, Any]) -> list[float]:
    command = [
        str(executable), str(inputs["hTxMeters"]), str(inputs["hRxMeters"]),
        str(inputs["txSitingCriteria"]), str(inputs["rxSitingCriteria"]),
        str(inputs["deltaHMeters"]), str(inputs["climate"]),
        str(inputs["surfaceRefractivityNUnits"]), str(inputs["frequencyMHz"]),
        str(inputs["polarization"]), str(inputs["relativePermittivity"]),
        str(inputs["conductivitySiemensPerMeter"]), str(inputs["variabilityMode"]),
        str(inputs["timePercentage"]),
    ]
    completed = subprocess.run(
        command,
        input="".join(f"{distance:.12f}\n" for distance in distances),
        text=True,
        capture_output=True,
        check=True,
    )
    losses = []
    for index, line in enumerate(completed.stdout.splitlines()):
        _, loss, status, warnings = line.split(",")
        if int(status) != 0 or int(warnings) != 0:
            raise RuntimeError(f"ITM point {index} failed: status={status}, warnings={warnings}")
        losses.append(float(loss))
    if len(losses) != len(distances):
        raise RuntimeError("ITM output row count did not match its input")
    return losses


def regression_check(executable: Path) -> dict[str, Any]:
    official_case = {
        **MODEL_INPUTS,
        "hTxMeters": 10.0,
        "hRxMeters": 1.0,
        "txSitingCriteria": 0,
        "rxSitingCriteria": 0,
        "deltaHMeters": 0.0,
        "frequencyMHz": 230.0,
        "polarization": 0,
        "conductivitySiemensPerMeter": 0.008,
        "variabilityMode": 0,
        "timePercentage": 87.0,
    }
    actual = run_itm(executable, [16.0], official_case)[0]
    expected = 152.5
    error = abs(actual - expected)
    if error > 0.1:
        raise RuntimeError(f"Official NTIA area regression failed: {actual} versus {expected}")
    return {
        "source": "NTIA/itm area.csv row 1",
        "expectedLossDbRoundedToTenths": expected,
        "actualLossDb": actual,
        "absoluteErrorDb": error,
        "toleranceDb": 0.1,
        "passed": True,
    }


def write_binary(path: Path, format_code: str, values: list[float | int]) -> None:
    path.write_bytes(struct.pack(f"<{len(values)}{format_code}", *values))


def generate(executable: Path, output: Path, schema: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    west, south, east, north = BOUNDS
    longitude_step = (east - west) / (GRID_SIZE - 1)
    latitude_step = (north - south) / (GRID_SIZE - 1)
    coordinates = [
        (west + x * longitude_step, south + y * latitude_step)
        for y in range(GRID_SIZE)
        for x in range(GRID_SIZE)
    ]
    distances = [haversine_km(longitude, latitude) for longitude, latitude in coordinates]
    valid_indices = [
        index for index, distance in enumerate(distances)
        if distance > MODEL_INPUTS["minimumModelDistanceKm"]
    ]
    solved = run_itm(executable, [distances[index] for index in valid_indices], MODEL_INPUTS)
    values = [math.nan] * len(coordinates)
    for index, loss in zip(valid_indices, solved, strict=True):
        values[index] = loss

    fine_size = GRID_SIZE * 2 - 1
    fine_coordinates = [
        (
            west + x * (east - west) / (fine_size - 1),
            south + y * (north - south) / (fine_size - 1),
        )
        for y in range(fine_size)
        for x in range(fine_size)
    ]
    fine_distances = [haversine_km(longitude, latitude) for longitude, latitude in fine_coordinates]
    fine_valid_indices = [
        index for index, distance in enumerate(fine_distances)
        if distance > MODEL_INPUTS["minimumModelDistanceKm"]
    ]
    fine_solved = run_itm(
        executable,
        [fine_distances[index] for index in fine_valid_indices],
        MODEL_INPUTS,
    )
    fine_values = [math.nan] * len(fine_coordinates)
    for index, loss in zip(fine_valid_indices, fine_solved, strict=True):
        fine_values[index] = loss
    shared_differences = []
    for coarse_y in range(GRID_SIZE):
        for coarse_x in range(GRID_SIZE):
            coarse = values[coarse_y * GRID_SIZE + coarse_x]
            fine = fine_values[(coarse_y * 2) * fine_size + coarse_x * 2]
            if not math.isnan(coarse) and not math.isnan(fine):
                shared_differences.append(abs(coarse - fine))

    quantized = [NO_DATA_RAW] * len(values)
    errors = []
    for index, value in enumerate(values):
        if math.isnan(value):
            continue
        raw = round((value - OFFSET_DB) / SCALE_DB)
        if not 0 <= raw < NO_DATA_RAW:
            raise RuntimeError(f"Loss {value} dB cannot be represented by fixture encoding")
        quantized[index] = raw
        errors.append(abs((SCALE_DB * raw + OFFSET_DB) - value))

    authoritative_path = output / "path-loss.float64le"
    compact_path = output / "path-loss.u16le"
    inputs_path = output / "solver-inputs.json"
    convergence_path = output / "convergence.json"
    metadata_path = output / "tile-metadata.json"
    write_binary(authoritative_path, "d", values)
    write_binary(compact_path, "H", quantized)
    atomic_write_json(inputs_path, {
        "datasetId": DATASET_ID,
        "transmitter": {
            "id": "sf-itm-tx",
            "longitudeDegrees": TX_LONGITUDE,
            "latitudeDegrees": TX_LATITUDE,
            "heightMeters": MODEL_INPUTS["hTxMeters"],
        },
        "modelInputs": MODEL_INPUTS,
        "boundsDegrees": list(BOUNDS),
        "shape": [GRID_SIZE, GRID_SIZE],
    })
    convergence = {
        "datasetId": DATASET_ID,
        "solverRegression": regression_check(executable),
        "nestedGridCheck": {
            "description": "ITM was independently evaluated on nested 33x33 and 65x65 grids and compared at every valid shared coordinate.",
            "coarseGridShape": [GRID_SIZE, GRID_SIZE],
            "fineGridShape": [fine_size, fine_size],
            "sharedValidCellCount": len(shared_differences),
            "maximumAbsoluteLossDifferenceDb": max(shared_differences),
            "toleranceDb": 1e-9,
            "passed": max(shared_differences) <= 1e-9,
        },
        "compactEncoding": {
            "scaleDb": SCALE_DB,
            "offsetDb": OFFSET_DB,
            "maximumAbsoluteQuantizationErrorDb": max(errors),
            "requiredMaximumErrorDb": SCALE_DB / 2,
            "passed": max(errors) <= SCALE_DB / 2 + 1e-12,
        },
        "validCellCount": len(valid_indices),
        "noDataCellCount": len(values) - len(valid_indices),
    }
    atomic_write_json(convergence_path, convergence)
    compact_hash = sha256_file(compact_path)
    metadata = {
        "format": "SCYTHE_GEODETIC_TILESET_V1",
        "datasetId": DATASET_ID,
        "quantity": "basic transmission loss",
        "units": "dB",
        "visualizationIsAuthoritative": False,
        "derivedFrom": "path-loss.float64le",
        "tiles": [{
            "id": "regional-z0",
            "path": "path-loss.u16le",
            "sha256": compact_hash,
            "sizeBytes": compact_path.stat().st_size,
            "shape": [GRID_SIZE, GRID_SIZE],
            "westDegrees": west,
            "southDegrees": south,
            "eastDegrees": east,
            "northDegrees": north,
            "lod": 0,
            "encoding": {
                "scalarType": "UINT16",
                "byteOrder": "LITTLE_ENDIAN",
                "scale": SCALE_DB,
                "offset": OFFSET_DB,
                "noDataRaw": NO_DATA_RAW,
            },
        }],
    }
    atomic_write_json(metadata_path, metadata)

    authoritative_hash = sha256_file(authoritative_path)
    job = {
        "solverSourceDirectory": None,
        "inputs": [{"sourcePath": "solver-inputs.json", "logicalPath": "solver-inputs.json"}],
        "assets": [
            {"sourcePath": "path-loss.float64le", "datasetPath": "path-loss.float64le", "role": "AUTHORITATIVE_VALUES", "mediaType": "application/octet-stream"},
            {"sourcePath": "path-loss.u16le", "datasetPath": "path-loss.u16le", "role": "DERIVED_VISUALIZATION", "mediaType": "application/octet-stream"},
            {"sourcePath": "tile-metadata.json", "datasetPath": "tile-metadata.json", "role": "OTHER", "mediaType": "application/json"},
            {"sourcePath": "convergence.json", "datasetPath": "convergence.json", "role": "OTHER", "mediaType": "application/json"},
            {"sourcePath": "solver-inputs.json", "datasetPath": "solver-inputs.json", "role": "OTHER", "mediaType": "application/json"},
        ],
        "manifest": {
            "schemaVersion": "1.0",
            "datasetId": DATASET_ID,
            "title": "NTIA ITM San Francisco Bay regional path-loss fixture",
            "description": "Deterministic NTIA ITM area-mode basic transmission loss on a small WGS84 grid; visualization remains non-authoritative.",
            "evidenceClass": "SOLVER_OUTPUT",
            "authority": {
                "solverName": "NTIA ITS Irregular Terrain Model",
                "solverVersion": "v1.4-43-g183ad95",
                "modelName": "Longley-Rice ITM Area Prediction Mode",
                "standardRevision": "ITM 1.2.2 algorithm",
                "sourceRevision": ITM_REVISION,
                "sourceTreeSha256": ITM_SOURCE_TREE_SHA256,
                "provenanceStatus": "COMPLETE",
                "solverLicense": "NTIA software disclaimer / US Government public domain",
                "datasetLicense": "CC0-1.0",
                "runId": "ntia-itm-sf-bay-area-v1-run-1",
                "deterministic": True,
                "executionEnvironment": "AlmaLinux 10 x86_64; GCC 14.3.1; IEEE-754",
                "inputHashes": [],
            },
            "spatialReference": {
                "type": "GEODETIC_GRID",
                "horizontalCrs": "EPSG:4326",
                "verticalDatum": "WGS84_ELLIPSOID",
                "coordinateOrder": "longitude,latitude,height",
                "heightUnits": "m",
                "ecefCompatible": True,
                "boundsDegrees": list(BOUNDS),
                "crossesAntimeridian": False,
            },
            "temporal": {
                "generatedUtc": "2026-08-02T00:00:00Z",
                "validFromUtc": None,
                "validToUtc": None,
                "statisticalTimePercentage": 50.0,
                "timeSemantics": "STATISTICAL_PERCENTAGE",
            },
            "physics": {
                "domain": "RF",
                "rf": {
                    "frequencyHz": MODEL_INPUTS["frequencyMHz"] * 1e6,
                    "bandwidthHz": 0.0,
                    "polarization": "vertical",
                    "transmitterHeightMeters": MODEL_INPUTS["hTxMeters"],
                    "receiverHeightMeters": MODEL_INPUTS["hRxMeters"],
                    "antennaPatternAssetPath": None,
                    "atmosphericModel": "ITM climate 5; N0 301 N-units",
                    "earthSpaceModel": "ITM area mode; delta-h 90 m",
                },
                "optical": None,
            },
            "quantity": {
                "name": "basic transmission loss",
                "definition": "NTIA ITM basic transmission loss A, excluding transmitter power and antenna gains",
                "units": "dB",
                "valueSemantics": "PATH_LOSS",
                "complexRepresentation": "NONE",
                "uncertainty": {
                    "kind": "NOT_QUANTIFIED",
                    "description": "ITM statistical variability is parameterized at 50 percent; no cell-wise uncertainty interval is asserted. Uint16 visualization quantization error is reported separately.",
                    "assetPath": None,
                },
            },
            "grid": {
                "representation": "CUSTOM_BINARY",
                "dimensions": [GRID_SIZE, GRID_SIZE],
                "resolution": [longitude_step, latitude_step],
                "noData": {"policy": "NAN", "value": None},
                "interpolation": "BILINEAR",
                "authoritativeAssetPath": "path-loss.float64le",
                "lodPolicy": {
                    "authoritativeValuesImmutable": True,
                    "derivedTilesAllowed": True,
                    "aggregationMethod": None,
                    "description": "The Float64 grid is immutable authority; Uint16 is a non-authoritative browser visualization transform.",
                },
            },
            "assets": [],
            "lineage": {
                "parentDatasetIds": [],
                "transformations": [{
                    "name": "linear-uint16-quantization",
                    "version": "1",
                    "parameters": {"scale": SCALE_DB, "offset": OFFSET_DB, "noDataRaw": NO_DATA_RAW},
                    "inputSha256": authoritative_hash,
                    "outputSha256": compact_hash,
                }],
            },
            "visualizationIsAuthoritative": False,
        },
    }
    job.pop("solverSourceDirectory")
    job_path = output / "package.job.json"
    atomic_write_json(job_path, job)
    manifest = package_job(job_path, schema)
    atomic_write_json(output / "manifest.json", manifest)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--itm-executable", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path,
        default=REPOSITORY_ROOT / "datasets" / DATASET_ID,
    )
    parser.add_argument(
        "--schema", type=Path,
        default=REPOSITORY_ROOT / "UnityProject" / "Docs" / "GlobalPropagationDataContract.schema.json",
    )
    arguments = parser.parse_args()
    generate(arguments.itm_executable.resolve(), arguments.output.resolve(), arguments.schema.resolve())
    print(f"[SCYTHE ITM FIXTURE] PASS {DATASET_ID} -> {arguments.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
