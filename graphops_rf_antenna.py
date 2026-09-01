"""Operator antenna declaration for the NESDR signal chain.

The antenna cannot be detected. An SMA port carries no identity conductor, the
SMArt v5 has no bias tee to sense a DC load, and a receive-only path has no
reflectometer with which to measure return loss. The operator is therefore the
only instrument that can see what is attached, and every field here is
OPERATOR_DECLARED.

A declaration matters because it is part of the signal chain: products observed
through different antennas are not comparable. It is recorded with the time it
took effect and is never applied backwards over products already emitted.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
from typing import Any, Dict, Optional


ALLOWED_FIELDS = {"antenna_id", "feedline_id", "extension_mm", "note"}

# Mirrors scythe-web/rfAntennaDeclaration.js. The server keeps its own allow-list
# rather than trusting whatever identifier the browser sends.
ANTENNAS: Dict[str, Dict[str, Any]] = {
    "nesdr-smart-telescopic": {
        "label": "TELESCOPIC MAST",
        "vendor_description": "Telescopic antenna mast (variable frequency)",
        "resonance_hz": None, "adjustable": True,
    },
    "nesdr-smart-433-ism": {
        "label": "433 MHz ISM MAST",
        "vendor_description": "433MHz (ISM) antenna mast (fixed frequency)",
        "resonance_hz": 433e6, "adjustable": False,
    },
    "nesdr-smart-uhf": {
        "label": "UHF MAST",
        "vendor_description": "UHF antenna mast (fixed frequency)",
        # The vendor names the band and withholds the number. Preserve the omission.
        "resonance_hz": None, "adjustable": False,
    },
    "no-antenna": {
        "label": "NO ANTENNA / PORT TERMINATED",
        "vendor_description": "Nothing attached, or a 50 ohm termination",
        "resonance_hz": None, "adjustable": False,
    },
    "other": {
        "label": "OTHER (OPERATOR DESCRIBES)",
        "vendor_description": "An antenna outside the bundle, described by the operator",
        "resonance_hz": None, "adjustable": False,
    },
}

FEEDLINES: Dict[str, Dict[str, Any]] = {
    "direct": {"label": "DIRECT TO SMA", "length_m": 0.0},
    "nesdr-magnetic-base-rg58-2m": {"label": "MAGNETIC BASE · 2 m RG58", "length_m": 2.0},
}

MAX_EXTENSION_MM = 2000.0
SPEED_OF_LIGHT_M_S = 299_792_458.0
DECLARATION_AUTHORITY = "OPERATOR_DECLARED"

AUTODETECT_REASON = (
    "ANTENNA AUTO-DETECTION IS NOT PHYSICALLY AVAILABLE: SMA CARRIES NO IDENTITY "
    "CONDUCTOR, NO BIAS TEE IS FITTED TO SENSE A DC LOAD, AND A RECEIVE-ONLY PATH "
    "HAS NO REFLECTOMETER TO MEASURE RETURN LOSS."
)


class AntennaDeclarationRefused(ValueError):
    """The declaration was not recorded; the previous declaration still stands."""


def _finite(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise AntennaDeclarationRefused(f"{name} must be numeric") from exc
    if not math.isfinite(number):
        raise AntennaDeclarationRefused(f"{name} must be finite")
    return number


def validate_declaration(payload: Any, *, declared_at: Optional[float] = None) -> Dict[str, Any]:
    """Validate an operator antenna declaration into a bounded record."""
    if not isinstance(payload, dict):
        raise AntennaDeclarationRefused("antenna declaration must be an object")
    unknown = set(payload) - ALLOWED_FIELDS
    if unknown:
        raise AntennaDeclarationRefused(f"unknown declaration fields: {sorted(unknown)}")

    antenna_id = str(payload.get("antenna_id") or "").strip()
    if antenna_id not in ANTENNAS:
        raise AntennaDeclarationRefused(
            f"antenna_id must be one of {sorted(ANTENNAS)}"
        )
    feedline_id = str(payload.get("feedline_id") or "direct").strip()
    if feedline_id not in FEEDLINES:
        raise AntennaDeclarationRefused(f"feedline_id must be one of {sorted(FEEDLINES)}")

    antenna = ANTENNAS[antenna_id]
    extension_mm: Optional[float] = None
    raw_extension = payload.get("extension_mm")
    if raw_extension not in (None, ""):
        extension_mm = _finite(raw_extension, "extension_mm")
        if not 0 < extension_mm <= MAX_EXTENSION_MM:
            raise AntennaDeclarationRefused(
                f"extension_mm must be between 0 and {MAX_EXTENSION_MM:.0f}"
            )
        if not antenna["adjustable"]:
            raise AntennaDeclarationRefused(
                f"{antenna['label']} is a fixed mast; extension_mm does not describe it"
            )

    quarter_wave_hz = (
        round(SPEED_OF_LIGHT_M_S / (4.0 * (extension_mm / 1000.0)))
        if extension_mm else None
    )

    record = {
        "antenna_id": antenna_id,
        "label": antenna["label"],
        "vendor_description": antenna["vendor_description"],
        "feedline_id": feedline_id,
        "feedline_label": FEEDLINES[feedline_id]["label"],
        "feedline_length_m": FEEDLINES[feedline_id]["length_m"],
        "extension_mm": extension_mm,
        "quarter_wave_hz": quarter_wave_hz,
        "quarter_wave_authority": "DERIVED_INFERENCE" if quarter_wave_hz else "UNDECLARED",
        "resonance_hz": antenna["resonance_hz"],
        "resonance_authority": "VENDOR_DECLARED" if antenna["resonance_hz"] else "UNDECLARED",
        "note": str(payload.get("note") or "").strip()[:256],
        "declared_at": float(declared_at if declared_at is not None else time.time()),
        "authority": DECLARATION_AUTHORITY,
        "auto_detected": False,
        "auto_detection_note": AUTODETECT_REASON,
    }
    return record


def declaration_receipt(record: Dict[str, Any], *,
                        previous: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Bind the declaration to a hash and state exactly how far it reaches.

    A declaration is not retroactive. Products already emitted carry the antenna
    they were emitted with, and re-labelling them would manufacture provenance
    for observations nobody made under this antenna.
    """
    canonical = json.dumps(
        {key: record[key] for key in
         ("antenna_id", "feedline_id", "extension_mm", "note")},
        sort_keys=True, separators=(",", ":"),
    )
    changed = bool(previous) and previous.get("antenna_id") != record["antenna_id"]
    boundaries = [
        f"ANTENNA AUTHORITY // {DECLARATION_AUTHORITY} — THE RECEIVER DID NOT MEASURE THIS",
        "APPLIES FORWARD ONLY — PRODUCTS ALREADY EMITTED KEEP THE ANTENNA THEY WERE EMITTED WITH",
        AUTODETECT_REASON,
    ]
    if changed:
        boundaries.append(
            "SIGNAL CHAIN CHANGED — PRODUCTS OBSERVED BEFORE AND AFTER THIS DECLARATION "
            "ARE NOT DIRECTLY COMPARABLE"
        )
    return {
        "declarationHash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "declaredAt": record["declared_at"],
        "appliesFrom": record["declared_at"],
        "retroactive": False,
        "previousAntennaId": (previous or {}).get("antenna_id"),
        "signalChainChanged": changed,
        "autoDetected": False,
        "boundaries": boundaries,
    }


class AntennaDeclarationStore:
    """Holds the one current operator declaration.

    SparseAnalyzerConfig is frozen by design, so this store sits alongside it
    rather than mutating a configuration that products have already been hashed
    against.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._record: Optional[Dict[str, Any]] = None
        self._receipt: Optional[Dict[str, Any]] = None

    def bootstrap_from_env(self) -> Optional[Dict[str, Any]]:
        """Adopt SDRPP_ANTENNA_ID if it names a catalogue entry.

        An unset or unrecognised value leaves the antenna UNDECLARED. The
        analyzer's own default is the string "unspecified", which is not a
        declaration and must not be promoted into one.
        """
        candidate = str(os.getenv("SDRPP_ANTENNA_ID", "") or "").strip()
        if candidate not in ANTENNAS:
            return None
        with self._lock:
            if self._record is not None:
                return self._record
        return self.declare({"antenna_id": candidate,
                             "note": "Adopted from SDRPP_ANTENNA_ID at startup"})[0]

    def declare(self, payload: Any) -> tuple:
        record = validate_declaration(payload)
        with self._lock:
            receipt = declaration_receipt(record, previous=self._record)
            self._record = record
            self._receipt = receipt
        return record, receipt

    def current(self) -> Dict[str, Any]:
        with self._lock:
            if self._record is None:
                return {
                    "declared": False,
                    "antenna": None,
                    "receipt": None,
                    "state": "UNDECLARED",
                    "detail": "OPERATOR HAS NOT DECLARED AN ANTENNA",
                    "auto_detected": False,
                    "auto_detection_note": AUTODETECT_REASON,
                }
            return {
                "declared": True,
                "antenna": dict(self._record),
                "receipt": dict(self._receipt or {}),
                "state": "DECLARED",
                "detail": f"{self._record['label']} · {DECLARATION_AUTHORITY}",
                "auto_detected": False,
                "auto_detection_note": AUTODETECT_REASON,
            }

    def clear(self) -> None:
        with self._lock:
            self._record = None
            self._receipt = None


_STORE = AntennaDeclarationStore()


def get_antenna_store() -> AntennaDeclarationStore:
    return _STORE


def catalogue() -> Dict[str, Any]:
    """The declarable parts, with vendor omissions preserved as null."""
    return {
        "antennas": [{"id": key, **value} for key, value in ANTENNAS.items()],
        "feedlines": [{"id": key, **value} for key, value in FEEDLINES.items()],
        "autoDetectable": False,
        "autoDetectionNote": AUTODETECT_REASON,
        "catalogueAuthority": "VENDOR_DECLARED",
    }
