"""Contract-backed RF solver evidence for GraphOps.

Solver samples are deliberately separate from measured RF observations.  They
may support explanation and hypothesis generation, but never assert sensor
truth or authorize an operational action.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
import hashlib
import math
import threading
import time
from typing import Any, Callable, Deque, Dict, Optional


@dataclass(frozen=True)
class RFSolverEvidence:
    evidence_id: str
    sampled_at: float
    dataset_id: str
    tile_id: str
    longitude_degrees: float
    latitude_degrees: float
    height_meters: float
    frequency_hz: float
    quantity: str
    value: float
    units: str
    coverage: bool
    coverage_threshold: float
    transmitter_id: str
    provenance: Dict[str, Any]
    evidence_class: str = "SOLVER_OUTPUT"
    visualization_is_authoritative: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class RFSolverEvidenceStore:
    """Thread-safe bounded store with strict solver-output authority checks."""

    def __init__(self, maxlen: int = 2048):
        self._items: Deque[RFSolverEvidence] = deque(maxlen=max(16, int(maxlen)))
        self._subscribers: list[Callable[[Dict[str, Any]], None]] = []
        self._lock = threading.RLock()

    def subscribe(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        with self._lock:
            if callback not in self._subscribers:
                self._subscribers.append(callback)

    def ingest(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if payload.get("evidence_class", "SOLVER_OUTPUT") != "SOLVER_OUTPUT":
            raise ValueError("RF solver evidence_class must be SOLVER_OUTPUT")
        if payload.get("visualization_is_authoritative", False) is not False:
            raise ValueError("RF solver visualization cannot be authoritative")
        dataset_id = str(payload.get("dataset_id", "")).strip()
        tile_id = str(payload.get("tile_id", "")).strip()
        quantity = str(payload.get("quantity", "")).strip()
        units = str(payload.get("units", "")).strip()
        provenance = dict(payload.get("provenance") or {})
        if not dataset_id or not tile_id or not quantity or not units:
            raise ValueError("dataset_id, tile_id, quantity, and units are required")
        if not provenance.get("solverName") or not provenance.get("runId"):
            raise ValueError("provenance must include solverName and runId")

        numeric = {}
        for key in ("longitude_degrees", "latitude_degrees", "height_meters",
                    "frequency_hz", "value", "coverage_threshold"):
            numeric[key] = float(payload.get(key, 0.0))
            if not math.isfinite(numeric[key]):
                raise ValueError(f"{key} must be finite")
        if not -180 <= numeric["longitude_degrees"] <= 180:
            raise ValueError("longitude_degrees is out of range")
        if not -90 <= numeric["latitude_degrees"] <= 90:
            raise ValueError("latitude_degrees is out of range")

        sampled_at = float(payload.get("sampled_at", time.time()))
        digest = hashlib.blake2s(
            (f"{dataset_id}:{tile_id}:{numeric['longitude_degrees']:.7f}:"
             f"{numeric['latitude_degrees']:.7f}:{numeric['value']:.8g}:"
             f"{sampled_at:.6f}").encode(), digest_size=8,
        ).hexdigest()
        item = RFSolverEvidence(
            evidence_id=f"rf-solver-{digest}", sampled_at=sampled_at,
            dataset_id=dataset_id, tile_id=tile_id,
            longitude_degrees=numeric["longitude_degrees"],
            latitude_degrees=numeric["latitude_degrees"],
            height_meters=numeric["height_meters"],
            frequency_hz=numeric["frequency_hz"], quantity=quantity,
            value=numeric["value"], units=units,
            coverage=bool(payload.get("coverage")),
            coverage_threshold=numeric["coverage_threshold"],
            transmitter_id=str(payload.get("transmitter_id", "unknown")),
            provenance=provenance,
        )
        with self._lock:
            self._items.append(item)
            subscribers = list(self._subscribers)
        result = item.to_dict()
        for callback in subscribers:
            try:
                callback(dict(result))
            except Exception:
                # Evidence remains stored even when an optional consumer fails.
                pass
        return result

    def query(self, *, evidence_id: Optional[str] = None, limit: int = 100) -> list[Dict[str, Any]]:
        with self._lock:
            items = list(self._items)
        result = []
        for item in reversed(items):
            if evidence_id and item.evidence_id != evidence_id:
                continue
            result.append(item.to_dict())
            if len(result) >= min(max(int(limit), 1), 500):
                break
        return result


def explain_coverage_cell(evidence: Dict[str, Any]) -> Dict[str, Any]:
    """Produce a deterministic, authority-labelled GraphOps explanation."""
    value = float(evidence["value"])
    threshold = float(evidence["coverage_threshold"])
    covered = bool(evidence["coverage"])
    relation = "at or below" if covered else "above"
    return {
        "directive": "Explain this coverage cell",
        "status": "SUPPORTED_BY_SOLVER_OUTPUT",
        "finding_class": "INFERRED",
        "evidence_class": "SOLVER_OUTPUT",
        "evidence_id": evidence["evidence_id"],
        "explanation": (
            f"The solver reports {value:.2f} {evidence['units']} {evidence['quantity']}, "
            f"which is {relation} the {threshold:.2f} {evidence['units']} coverage threshold; "
            f"the cell is therefore rendered as {'covered' if covered else 'a coverage gap'}."
        ),
        "provenance": dict(evidence["provenance"]),
        "authority_boundary": (
            "Modeled solver output, not a measurement. The browser visualization is "
            "non-authoritative and cannot independently justify an operational action."
        ),
        "assumptions": [
            "The selected frequency, transmitter, receiver height, and statistical model inputs match the manifest.",
            "The configured path-loss threshold is the intended coverage decision rule.",
        ],
        "falsifier": "Collect calibrated field measurements at this cell and compare them with the modeled threshold decision.",
    }


_STORE = RFSolverEvidenceStore()


def get_rf_solver_evidence_store() -> RFSolverEvidenceStore:
    return _STORE
