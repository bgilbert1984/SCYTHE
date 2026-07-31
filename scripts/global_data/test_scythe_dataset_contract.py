#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest


SCRIPT_DIRECTORY = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_DIRECTORY.parents[1]
MODULE_PATH = SCRIPT_DIRECTORY / "scythe_dataset_contract.py"
SCHEMA_PATH = (
    REPOSITORY_ROOT
    / "UnityProject"
    / "Docs"
    / "GlobalPropagationDataContract.schema.json"
)
JOB_PATH = (
    SCRIPT_DIRECTORY
    / "examples"
    / "meep-regression-reference.job.json"
)
MEEP_REFERENCE = (
    REPOSITORY_ROOT
    / "assets"
    / "meep-master"
    / "tests"
    / "array-slice-ll-ref.h5"
)

SPEC = importlib.util.spec_from_file_location("scythe_dataset_contract", MODULE_PATH)
CONTRACT = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(CONTRACT)


class GlobalContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = CONTRACT.package_job(JOB_PATH, SCHEMA_PATH)

    def test_reference_fixture_packages_and_validates(self) -> None:
        CONTRACT.validate_manifest(self.manifest, SCHEMA_PATH)
        self.assertEqual(self.manifest["evidenceClass"], "SYNTHETIC")
        self.assertEqual(
            self.manifest["authority"]["provenanceStatus"],
            "ARCHIVE_DIGEST_ONLY",
        )
        self.assertEqual(len(self.manifest["authority"]["sourceTreeSha256"]), 64)
        self.assertEqual(
            self.manifest["assets"][0]["sha256"],
            CONTRACT.sha256_file(MEEP_REFERENCE),
        )

    def test_presentation_cannot_be_authoritative(self) -> None:
        invalid = copy.deepcopy(self.manifest)
        invalid["visualizationIsAuthoritative"] = True
        with self.assertRaises(CONTRACT.ContractError):
            CONTRACT.validate_manifest(invalid, SCHEMA_PATH)

    def test_physical_output_requires_normalized_length_scale(self) -> None:
        invalid = copy.deepcopy(self.manifest)
        invalid["evidenceClass"] = "SOLVER_OUTPUT"
        invalid["physics"]["optical"]["wavelengthNanometers"] = 650.0
        with self.assertRaisesRegex(
            CONTRACT.ContractError,
            "metersPerSolverUnit",
        ):
            CONTRACT.validate_manifest(invalid, SCHEMA_PATH)

    def test_physical_optical_output_requires_wavelength(self) -> None:
        invalid = copy.deepcopy(self.manifest)
        invalid["evidenceClass"] = "SOLVER_OUTPUT"
        invalid["spatialReference"]["metersPerSolverUnit"] = 1e-6
        with self.assertRaisesRegex(
            CONTRACT.ContractError,
            "wavelengthNanometers",
        ):
            CONTRACT.validate_manifest(invalid, SCHEMA_PATH)

    def test_asset_integrity_detects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            dataset_root = Path(temporary)
            destination = dataset_root / self.manifest["assets"][0]["path"]
            destination.parent.mkdir(parents=True)
            shutil.copy2(MEEP_REFERENCE, destination)
            CONTRACT.verify_dataset_assets(self.manifest, dataset_root)

            destination.write_bytes(destination.read_bytes() + b"tampered")
            with self.assertRaisesRegex(
                CONTRACT.ContractError,
                "size mismatch",
            ):
                CONTRACT.verify_dataset_assets(self.manifest, dataset_root)

    def test_declared_asset_must_be_referenced_by_grid(self) -> None:
        invalid = copy.deepcopy(self.manifest)
        invalid["grid"]["authoritativeAssetPath"] = "fields/missing.h5"
        with self.assertRaisesRegex(
            CONTRACT.ContractError,
            "authoritativeAssetPath",
        ):
            CONTRACT.validate_manifest(invalid, SCHEMA_PATH)

    def test_exact_binary_lock_can_replace_solver_source_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            job = json.loads(JOB_PATH.read_text(encoding="utf-8"))
            job.pop("solverSourceDirectory")
            job["manifest"]["authority"]["sourceTreeSha256"] = None
            job["inputs"][0]["sourcePath"] = str(
                (
                    REPOSITORY_ROOT
                    / "solvers"
                    / "meep"
                    / "environment.explicit.txt"
                ).resolve()
            )
            job["assets"][0]["sourcePath"] = str(MEEP_REFERENCE.resolve())
            job_path = temporary_root / "binary-lock.job.json"
            job_path.write_text(json.dumps(job), encoding="utf-8")

            manifest = CONTRACT.package_job(job_path, SCHEMA_PATH)

            self.assertIsNone(manifest["authority"]["sourceTreeSha256"])
            self.assertEqual(
                manifest["authority"]["inputHashes"][0]["path"],
                "inputs/array-slice-ll.cpp",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
