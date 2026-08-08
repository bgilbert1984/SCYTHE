"""Checksum-bound M0 lunar reference evidence resolver.

M0 deliberately has no registered terrain tile.  The resolver validates the
selected Moon-fixed coordinate and packaged reference artifacts, but it never
derives elevation or illumination values from visualization pixels.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict


class LunarEvidenceError(ValueError):
    pass


class LunarEvidenceResolver:
    def __init__(self, dataset_root: str | Path | None = None):
        self.dataset_root = Path(dataset_root or Path(__file__).parent / "datasets")

    @staticmethod
    def _finite(value: Any, name: str) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise LunarEvidenceError(f"{name} must be numeric") from exc
        if not math.isfinite(number):
            raise LunarEvidenceError(f"{name} must be finite")
        return number

    def resolve(self, selection: Dict[str, Any]) -> Dict[str, Any]:
        dataset_id = str(selection.get("datasetId", ""))
        if dataset_id != "lunar-south-pole-reference-m0":
            raise LunarEvidenceError("unsupported lunar reference dataset")
        if selection.get("celestialBody") != "MOON" or selection.get("referenceFrame") != "MOON_ME_DE421":
            raise LunarEvidenceError("lunar selection body or reference frame is invalid")
        longitude = self._finite(selection.get("longitudeDegrees"), "longitudeDegrees")
        latitude = self._finite(selection.get("latitudeDegrees"), "latitudeDegrees")
        if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
            raise LunarEvidenceError("lunar coordinates are out of range")
        dataset_dir = self.dataset_root / dataset_id
        manifest_path = dataset_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (manifest.get("schemaVersion") != "SCYTHE_LUNAR_REFERENCE_V1" or
                manifest.get("viewer", {}).get("terrainAuthority") != "ABSENT_M0"):
            raise LunarEvidenceError("lunar M0 manifest authority boundary is invalid")
        artifacts = []
        for asset in manifest.get("assets", []):
            path = dataset_dir / asset["path"]
            if path.parent != dataset_dir or not path.is_file():
                raise LunarEvidenceError(f"lunar asset path is invalid: {asset.get('id')}")
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != asset.get("sha256"):
                raise LunarEvidenceError(f"lunar asset checksum mismatch: {asset.get('id')}")
            artifacts.append({key: asset.get(key) for key in (
                "id", "path", "role", "sha256", "sourceUrl", "productPage", "credit",
                "instrument", "mission")})
        return {
            "datasetId": dataset_id,
            "locationId": selection.get("locationId"),
            "celestialBody": "MOON", "referenceFrame": "MOON_ME_DE421",
            "longitudeDegrees": longitude, "latitudeDegrees": latitude,
            "heightMeters": 0.0, "spatialAuthority": "REFERENCE_ELLIPSOID_ONLY",
            "terrainAuthority": "ABSENT_M0", "elevationMeters": None,
            "evidenceClass": "DERIVED_VISUALIZATION", "artifacts": artifacts,
            "authoritativeSources": manifest.get("authoritativeSources", []),
            "limitations": manifest.get("limitations", []),
        }
