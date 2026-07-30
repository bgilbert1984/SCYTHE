#!/usr/bin/env python3
"""Package and validate SCYTHE propagation dataset sidecar manifests.

This tool does not execute a solver, transform field values, or create visualization
tiles. It records immutable file hashes and solver provenance around existing output.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Iterable


EXCLUDED_TREE_PARTS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    "autom4te.cache",
}


class ContractError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_tree_sha256(root: Path) -> str:
    """Hash relative paths and contents for a VCS-less source archive."""
    if not root.is_dir():
        raise ContractError(f"Solver source directory does not exist: {root}")

    digest = hashlib.sha256()
    files = sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and not any(part in EXCLUDED_TREE_PARTS for part in path.relative_to(root).parts)
    )
    if not files:
        raise ContractError(f"Solver source directory contains no files: {root}")

    for path in files:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError(f"Could not read JSON {path}: {error}") from error


def validate_manifest(manifest: Any, schema_path: Path) -> None:
    try:
        import jsonschema
        from jsonschema import FormatChecker
    except ImportError as error:
        raise ContractError(
            "jsonschema is required for contract validation. "
            "Install the Python jsonschema package."
        ) from error

    schema = load_json(schema_path)
    validator_class = jsonschema.validators.validator_for(schema)
    validator_class.check_schema(schema)
    validator = validator_class(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(manifest), key=lambda item: list(item.path))
    if errors:
        details = []
        for error in errors:
            location = ".".join(str(part) for part in error.absolute_path) or "<root>"
            details.append(f"{location}: {error.message}")
        raise ContractError("Contract validation failed:\n  " + "\n  ".join(details))

    _validate_cross_references(manifest)


def _validate_cross_references(manifest: dict[str, Any]) -> None:
    asset_paths = {asset["path"] for asset in manifest["assets"]}
    authoritative = manifest["grid"]["authoritativeAssetPath"]
    if authoritative not in asset_paths:
        raise ContractError(
            "grid.authoritativeAssetPath does not reference a declared asset: "
            f"{authoritative}"
        )

    uncertainty_path = manifest["quantity"]["uncertainty"]["assetPath"]
    if uncertainty_path is not None and uncertainty_path not in asset_paths:
        raise ContractError(
            f"quantity.uncertainty.assetPath is not a declared asset: {uncertainty_path}"
        )

    if manifest["spatialReference"]["type"] == "LOCAL_CARTESIAN":
        units = manifest["spatialReference"]["distanceUnits"]
        scale = manifest["spatialReference"]["metersPerSolverUnit"]
        if units == "m" and scale not in (None, 1, 1.0):
            raise ContractError("Meter-valued local coordinates cannot declare a non-unit scale.")
        if units == "solver_length_unit" and scale is None:
            if manifest["evidenceClass"] not in {"SYNTHETIC", "ILLUSTRATIVE"}:
                raise ContractError(
                    "Physical solver output in normalized coordinates must declare "
                    "metersPerSolverUnit."
                )

    physics = manifest["physics"]
    if physics["domain"] in {"OPTICAL", "RF_AND_OPTICAL"}:
        optical = physics["optical"]
        if (
            optical["wavelengthNanometers"] is None
            and manifest["evidenceClass"] not in {"SYNTHETIC", "ILLUSTRATIVE"}
        ):
            raise ContractError(
                "Physical optical output must declare wavelengthNanometers."
            )


def verify_dataset_assets(manifest: dict[str, Any], dataset_root: Path) -> None:
    if not dataset_root.is_dir():
        raise ContractError(f"Dataset root does not exist: {dataset_root}")

    for asset in manifest["assets"]:
        relative = Path(asset["path"])
        path = (dataset_root / relative).resolve()
        try:
            path.relative_to(dataset_root.resolve())
        except ValueError as error:
            raise ContractError(f"Dataset asset escapes its root: {asset['path']}") from error

        if not path.is_file():
            raise ContractError(f"Dataset asset is missing: {asset['path']}")
        actual_size = path.stat().st_size
        if actual_size != asset["sizeBytes"]:
            raise ContractError(
                f"Dataset asset size mismatch for {asset['path']}: "
                f"expected {asset['sizeBytes']}, found {actual_size}"
            )
        actual_hash = sha256_file(path)
        if actual_hash != asset["sha256"]:
            raise ContractError(
                f"Dataset asset checksum mismatch for {asset['path']}: "
                f"expected {asset['sha256']}, found {actual_hash}"
            )


def _resolve(base: Path, value: str, field: str) -> Path:
    if not value:
        raise ContractError(f"{field} cannot be empty.")
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def _safe_dataset_path(value: str) -> str:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value or not value:
        raise ContractError(f"Unsafe dataset asset path: {value}")
    return path.as_posix()


def package_job(job_path: Path, schema_path: Path) -> dict[str, Any]:
    job = load_json(job_path)
    if not isinstance(job, dict) or not isinstance(job.get("manifest"), dict):
        raise ContractError("Packaging job requires a manifest object.")

    base = job_path.parent
    manifest = copy.deepcopy(job["manifest"])
    authority = manifest.get("authority")
    if not isinstance(authority, dict):
        raise ContractError("Packaging job manifest requires authority metadata.")

    source_directory = _resolve(
        base,
        job.get("solverSourceDirectory", ""),
        "solverSourceDirectory",
    )
    authority["sourceTreeSha256"] = source_tree_sha256(source_directory)

    packaged_inputs = []
    for item in _require_list(job, "inputs"):
        source = _resolve(base, item.get("sourcePath", ""), "inputs.sourcePath")
        if not source.is_file():
            raise ContractError(f"Input does not exist: {source}")
        packaged_inputs.append(
            {
                "path": _safe_dataset_path(item.get("logicalPath", "")),
                "sha256": sha256_file(source),
            }
        )
    authority["inputHashes"] = packaged_inputs

    packaged_assets = []
    for item in _require_list(job, "assets"):
        source = _resolve(base, item.get("sourcePath", ""), "assets.sourcePath")
        if not source.is_file():
            raise ContractError(f"Dataset asset does not exist: {source}")
        packaged_assets.append(
            {
                "path": _safe_dataset_path(item.get("datasetPath", "")),
                "role": item.get("role"),
                "mediaType": item.get("mediaType"),
                "sha256": sha256_file(source),
                "sizeBytes": source.stat().st_size,
            }
        )
    manifest["assets"] = packaged_assets

    validate_manifest(manifest, schema_path)
    return manifest


def _require_list(job: dict[str, Any], field: str) -> list[dict[str, Any]]:
    value = job.get(field)
    if not isinstance(value, list):
        raise ContractError(f"Packaging job requires {field} array.")
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ContractError(f"{field}[{index}] must be an object.")
    return value


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, indent=2, sort_keys=False) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def build_parser(default_schema: Path) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--schema",
        type=Path,
        default=default_schema,
        help="Global propagation JSON Schema.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    package = subcommands.add_parser(
        "package",
        help="Hash existing solver outputs and emit a validated sidecar manifest.",
    )
    package.add_argument("--job", type=Path, required=True)
    package.add_argument("--output", type=Path, required=True)

    validate = subcommands.add_parser(
        "validate",
        help="Validate an existing dataset sidecar manifest.",
    )
    validate.add_argument("manifest", type=Path)
    validate.add_argument(
        "--dataset-root",
        type=Path,
        help="Also verify every declared asset's existence, size, and SHA-256.",
    )

    tree_hash = subcommands.add_parser(
        "tree-hash",
        help="Compute a deterministic digest for a VCS-less source archive.",
    )
    tree_hash.add_argument("directory", type=Path)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    script_path = Path(__file__).resolve()
    default_schema = (
        script_path.parents[2]
        / "UnityProject"
        / "Docs"
        / "GlobalPropagationDataContract.schema.json"
    )
    parser = build_parser(default_schema)
    arguments = parser.parse_args(argv)
    schema_path = arguments.schema.resolve()

    try:
        if arguments.command == "package":
            manifest = package_job(arguments.job.resolve(), schema_path)
            atomic_write_json(arguments.output.resolve(), manifest)
            print(
                f"[SCYTHE GLOBAL CONTRACT] PASS package "
                f"{manifest['datasetId']} -> {arguments.output.resolve()}"
            )
        elif arguments.command == "validate":
            manifest = load_json(arguments.manifest.resolve())
            validate_manifest(manifest, schema_path)
            if arguments.dataset_root is not None:
                verify_dataset_assets(manifest, arguments.dataset_root.resolve())
            print(
                f"[SCYTHE GLOBAL CONTRACT] PASS validate "
                f"{manifest['datasetId']}"
            )
        elif arguments.command == "tree-hash":
            print(source_tree_sha256(arguments.directory.resolve()))
        else:
            parser.error(f"Unknown command {arguments.command}")
    except ContractError as error:
        print(f"[SCYTHE GLOBAL CONTRACT] FAIL {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
