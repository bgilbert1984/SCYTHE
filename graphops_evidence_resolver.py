"""Server-side resolution of contract-backed RF-cell selections."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import struct
from typing import Any, Dict


class EvidenceResolutionError(ValueError):
    pass


class RFCellEvidenceResolver:
    def __init__(self, dataset_root: Path | None = None):
        self.dataset_root = (dataset_root or Path(__file__).resolve().parent / "datasets").resolve()

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _safe_child(root: Path, relative: str) -> Path:
        path = (root / relative).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError as exc:
            raise EvidenceResolutionError("dataset asset escapes its root") from exc
        return path

    def resolve(self, selection: Dict[str, Any]) -> Dict[str, Any]:
        dataset_dir = self._safe_child(self.dataset_root, selection["datasetId"])
        manifest_path = dataset_dir / "manifest.json"
        if not manifest_path.is_file():
            raise EvidenceResolutionError("dataset manifest not found")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("datasetId") != selection["datasetId"]:
            raise EvidenceResolutionError("dataset identity mismatch")
        if manifest.get("evidenceClass") != "SOLVER_OUTPUT" or manifest.get("visualizationIsAuthoritative") is not False:
            raise EvidenceResolutionError("RF directive requires non-authoritative SOLVER_OUTPUT visualization")
        if manifest.get("spatialReference", {}).get("type") != "GEODETIC_GRID":
            raise EvidenceResolutionError("RF-cell resolver requires GEODETIC_GRID")

        metadata_path = dataset_dir / "tile-metadata.json"
        metadata_asset = next((item for item in manifest.get("assets", []) if item.get("path") == "tile-metadata.json"), None)
        if (not metadata_asset or not metadata_path.is_file() or
                metadata_path.stat().st_size != metadata_asset["sizeBytes"] or
                self._sha256(metadata_path) != metadata_asset["sha256"]):
            raise EvidenceResolutionError("tile metadata integrity check failed")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        tile = next((item for item in metadata.get("tiles", []) if item.get("id") == selection["tileId"]), None)
        if not tile:
            raise EvidenceResolutionError("selected tile is not declared")
        display_hash = str(selection.get("displayAssetHash") or "").removeprefix("sha256:")
        if display_hash and display_hash != tile.get("sha256"):
            raise EvidenceResolutionError("selected display asset hash does not match the contract")
        lon, lat = float(selection["longitudeDegrees"]), float(selection["latitudeDegrees"])
        if not (tile["westDegrees"] <= lon <= tile["eastDegrees"] and tile["southDegrees"] <= lat <= tile["northDegrees"]):
            raise EvidenceResolutionError("selection is outside the declared tile")

        authority_path = manifest["grid"]["authoritativeAssetPath"]
        asset = next((item for item in manifest.get("assets", []) if item.get("path") == authority_path), None)
        if not asset or asset.get("role") != "AUTHORITATIVE_VALUES":
            raise EvidenceResolutionError("authoritative asset is not declared")
        path = self._safe_child(dataset_dir, authority_path)
        if not path.is_file() or path.stat().st_size != asset["sizeBytes"] or self._sha256(path) != asset["sha256"]:
            raise EvidenceResolutionError("authoritative asset integrity check failed")

        width, height = (int(value) for value in manifest["grid"]["dimensions"][:2])
        if path.stat().st_size != width * height * 8:
            raise EvidenceResolutionError("authoritative Float64 grid size mismatch")
        u = (lon - tile["westDegrees"]) / (tile["eastDegrees"] - tile["westDegrees"])
        v = (lat - tile["southDegrees"]) / (tile["northDegrees"] - tile["southDegrees"])
        x, y = u * (width - 1), v * (height - 1)
        x0, y0 = math.floor(x), math.floor(y)
        x1, y1 = min(width - 1, x0 + 1), min(height - 1, y0 + 1)
        with path.open("rb") as stream:
            def value_at(px: int, py: int) -> float:
                stream.seek((py * width + px) * 8)
                return struct.unpack("<d", stream.read(8))[0]
            samples = [value_at(x0, y0), value_at(x1, y0), value_at(x0, y1), value_at(x1, y1)]
        if not all(math.isfinite(value) for value in samples):
            raise EvidenceResolutionError("authoritative cell contains no-data")
        tx, ty = x - x0, y - y0
        top = samples[0] * (1 - tx) + samples[1] * tx
        bottom = samples[2] * (1 - tx) + samples[3] * tx
        value = top * (1 - ty) + bottom * ty
        display = selection.get("displayValue")
        return {
            "selection": dict(selection), "datasetId": manifest["datasetId"],
            "tileId": tile["id"], "quantity": manifest["quantity"]["name"],
            "units": manifest["quantity"]["units"], "authoritativeValue": value,
            "displayValue": display, "displayDelta": None if display is None else float(display) - value,
            "authorityAsset": authority_path, "authorityAssetSha256": asset["sha256"],
            "interpolation": manifest["grid"]["interpolation"],
            "gridCoordinate": [x, y], "evidenceClass": "SOLVER_OUTPUT",
            "visualizationIsAuthoritative": False, "provenance": dict(manifest["authority"]),
            "lineage": dict(manifest.get("lineage") or {}),
        }
