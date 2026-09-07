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


ALLOWED_FIELDS = {"antenna_id", "feedline_id", "extension_mm",
                  "extension_authority", "note"}

# How the operator arrived at the extension. Defaulting to ESTIMATED is the
# only honest default: a number typed as a target ("173 mm should resonate at
# 433.92") is not a number read off a ruler, and nothing here can tell them
# apart. MEASURED has to be claimed, never assumed.
EXTENSION_AUTHORITIES = ("OPERATOR_MEASURED", "OPERATOR_ESTIMATED")
DEFAULT_EXTENSION_AUTHORITY = "OPERATOR_ESTIMATED"

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

# "undeclared" is the default, and it is deliberately not "direct". A mast on 2 m
# of RG58 is not the same signal chain as the same mast screwed onto the dongle,
# and nothing in a receive-only path can tell the two apart. Defaulting to
# "direct" would publish a cable path nobody attested to: configuration
# convenience printed as physical evidence.
FEEDLINES: Dict[str, Dict[str, Any]] = {
    "undeclared": {"label": "FEEDLINE UNDECLARED", "length_m": None},
    "direct": {"label": "DIRECT TO SMA", "length_m": 0.0},
    "nesdr-magnetic-base-rg58-2m": {"label": "MAGNETIC BASE · 2 m RG58", "length_m": 2.0},
}

# The lower bound is a unit guard, not a hardware claim. "0.73" is what a
# metre-thinking operator types into a millimetre field, and the old
# "greater than zero" test accepted it and derived a 102.7 GHz quarter wave
# without complaint. Nothing in the bundle is a centimetre of whip.
MIN_EXTENSION_MM = 10.0
MAX_EXTENSION_MM = 2000.0
SPEED_OF_LIGHT_M_S = 299_792_458.0
DECLARATION_AUTHORITY = "OPERATOR_DECLARED"

# The derived quarter wave is geometry, not evidence of resonance. c/4L assumes
# free space and an infinite ground plane; the magnetic base, the surface it is
# stuck to, body and vehicle proximity, and the stepped construction of a
# telescoping whip all move the real optimum. A practical monopole rule lands
# about 4.6% shorter. Naming the model is what keeps the number from being read
# as a measurement of this antenna.
QUARTER_WAVE_MODEL = "IDEAL_FREE_SPACE"
RESONANCE_CLAIM = "NOT_MEASURED"
# The third claim, kept apart from the other two. Geometry is declared, the
# quarter wave is arithmetic on it, and how the antenna actually responds at the
# frequency being received is neither. Without this field a reader holding a
# 368 mm mast and a 203.663 MHz reference can conclude the antenna is wrong at
# 100 MHz -- a conclusion this system has no basis for in either direction. A
# receive-only path has no reflectometer, so NOT_CALIBRATED is permanent here
# rather than pending.
RESPONSE_AT_TUNE = "NOT_CALIBRATED"

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
    feedline_id = str(payload.get("feedline_id") or "undeclared").strip()
    if feedline_id not in FEEDLINES:
        raise AntennaDeclarationRefused(f"feedline_id must be one of {sorted(FEEDLINES)}")

    antenna = ANTENNAS[antenna_id]
    extension_mm: Optional[float] = None
    raw_extension = payload.get("extension_mm")
    if raw_extension not in (None, ""):
        extension_mm = _finite(raw_extension, "extension_mm")
        if not MIN_EXTENSION_MM <= extension_mm <= MAX_EXTENSION_MM:
            raise AntennaDeclarationRefused(
                f"extension_mm must be between {MIN_EXTENSION_MM:.0f} and "
                f"{MAX_EXTENSION_MM:.0f} millimetres (a value below "
                f"{MIN_EXTENSION_MM:.0f} is usually metres typed into a "
                f"millimetre field)"
            )
        if not antenna["adjustable"]:
            raise AntennaDeclarationRefused(
                f"{antenna['label']} is a fixed mast; extension_mm does not describe it"
            )

    extension_authority = str(payload.get("extension_authority") or "").strip()
    if extension_mm is None:
        if extension_authority:
            raise AntennaDeclarationRefused(
                "extension_authority describes an extension_mm that was not declared"
            )
        extension_authority = "UNDECLARED"
    else:
        extension_authority = extension_authority or DEFAULT_EXTENSION_AUTHORITY
        if extension_authority not in EXTENSION_AUTHORITIES:
            raise AntennaDeclarationRefused(
                f"extension_authority must be one of {sorted(EXTENSION_AUTHORITIES)}"
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
        "feedline_authority": ("UNDECLARED" if feedline_id == "undeclared"
                               else DECLARATION_AUTHORITY),
        "extension_mm": extension_mm,
        "extension_authority": extension_authority,
        "quarter_wave_hz": quarter_wave_hz,
        "quarter_wave_authority": "DERIVED_INFERENCE" if quarter_wave_hz else "UNDECLARED",
        "quarter_wave_model": QUARTER_WAVE_MODEL if quarter_wave_hz else "UNDECLARED",
        # The geometry implies a frequency. It does not establish that the antenna
        # is resonant there, and no receive-only path can establish it.
        "resonance_claim": RESONANCE_CLAIM,
        "response_at_tune": RESPONSE_AT_TUNE,
        "resonance_hz": antenna["resonance_hz"],
        "resonance_authority": "VENDOR_DECLARED" if antenna["resonance_hz"] else "UNDECLARED",
        "note": str(payload.get("note") or "").strip()[:256],
        "declared_at": float(declared_at if declared_at is not None else time.time()),
        "authority": DECLARATION_AUTHORITY,
        "auto_detected": False,
        "auto_detection_note": AUTODETECT_REASON,
    }
    return record


# The fields that define the INSTRUMENT: the ones that change what arrives at the
# ADC. Everything else about a declaration is bookkeeping around them.
COMPARABILITY_FIELDS = ("antenna_id", "feedline_id", "extension_mm")

# The fields that define the DECLARATION: the instrument, plus who says so and
# why. "note" lives here and only here.
#
# One hash was previously doing both jobs, over the instrument fields AND the
# note. That made prose part of the identity of a physical chain: rewording a
# comment moved the hash, and any consumer keying comparability off that hash
# would have invalidated products because the operator fixed a typo. Splitting
# them is what makes the boundary rule complete rather than merely quiet --
# a receipt that stays silent while a downstream hash moves is not a system that
# agrees with itself.
DECLARATION_HASH_FIELDS = COMPARABILITY_FIELDS + (
    "authority", "extension_authority", "note",
)


def _digest(record: Dict[str, Any], fields) -> str:
    return hashlib.sha256(
        json.dumps({key: record[key] for key in fields},
                   sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def instrument_hash(record: Dict[str, Any]) -> str:
    """Identity of the physical chain, and nothing else.

    Two declarations with this hash in common describe the same instrument, so
    their products are comparable. Two that differ do not, whatever the prose
    around them says.
    """
    return _digest(record, COMPARABILITY_FIELDS)


def declaration_hash(record: Dict[str, Any]) -> str:
    """Identity of the statement the operator made about the instrument.

    Advances when the note or the authority behind the extension changes. That
    is a new statement about the same antenna, and it invalidates nothing.
    """
    return _digest(record, DECLARATION_HASH_FIELDS)


def _extension_label(extension_mm: Optional[float]) -> str:
    return "UNDECLARED" if not extension_mm else f"{extension_mm:.0f} mm"


def _quarter_wave_label(extension_mm: Optional[float]) -> str:
    """The resonance an extension implies, for boundary prose only.

    Still DERIVED_INFERENCE, still the ideal free-space quarter wave, and still
    silent about the ground plane the magnetic base may or may not be sitting on.
    """
    if not extension_mm:
        return "UNDECLARED"
    return f"{SPEED_OF_LIGHT_M_S / (4.0 * (extension_mm / 1000.0)) / 1e6:.1f} MHz"


def _changed_comparability_fields(previous: Optional[Dict[str, Any]],
                                  record: Dict[str, Any]) -> list:
    """Which instrument-defining fields moved. A first declaration changes nothing."""
    if not previous:
        return []
    return [field for field in COMPARABILITY_FIELDS
            if previous.get(field) != record.get(field)]


def declaration_receipt(record: Dict[str, Any], *,
                        previous: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Bind the declaration to a hash and state exactly how far it reaches.

    A declaration is not retroactive. Products already emitted carry the antenna
    they were emitted with, and re-labelling them would manufacture provenance
    for observations nobody made under this antenna.

    The comparability boundary fires on any instrument-defining field, not on the
    part number alone. A telescopic mast retracted from 730 mm to 165 mm is still
    "nesdr-smart-telescopic" and is not still the same antenna: its derived
    quarter wave moves from roughly 103 MHz to roughly 454 MHz. Relative-power
    products either side of that are not comparable, and saying so is the whole
    job of this receipt.
    """
    changed_fields = _changed_comparability_fields(previous, record)
    changed = bool(changed_fields)
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
    if "extension_mm" in changed_fields:
        before = (previous or {}).get("extension_mm")
        after = record.get("extension_mm")
        line = (
            f"MAST EXTENSION CHANGED {_extension_label(before)} → {_extension_label(after)} — "
            f"DERIVED QUARTER WAVE {_quarter_wave_label(before)} → {_quarter_wave_label(after)}"
        )
        if "antenna_id" not in changed_fields:
            line += ". THE MAST IS THE SAME PART; THE INSTRUMENT IS NOT"
        boundaries.append(line)
    if "feedline_id" in changed_fields:
        boundaries.append(
            "FEEDLINE CHANGED "
            f"{(previous or {}).get('feedline_label') or 'UNDECLARED'} → "
            f"{record['feedline_label']} — A DIFFERENT CABLE PATH IS A DIFFERENT LOSS, "
            "AND A RECEIVE-ONLY PATH HAS NOTHING WITH WHICH TO MEASURE IT"
        )
    return {
        "instrumentHash": instrument_hash(record),
        "previousInstrumentHash": (instrument_hash(previous) if previous else None),
        "declarationHash": declaration_hash(record),
        "declaredAt": record["declared_at"],
        "appliesFrom": record["declared_at"],
        "retroactive": False,
        "previousAntennaId": (previous or {}).get("antenna_id"),
        "previousFeedlineId": (previous or {}).get("feedline_id"),
        "previousExtensionMm": (previous or {}).get("extension_mm"),
        "changedFields": changed_fields,
        "signalChainChanged": changed,
        "autoDetected": False,
        "boundaries": boundaries,
    }


def declaration_persistence(record: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Whether the active declaration would survive a restart.

    Two different states that look identical in a status line otherwise. A
    declaration made through the API is real and is what products are hashed
    against, but it lives in this process only; the environment is what the next
    orchestrator reads. Saying ACTIVE without saying whether it is PERSISTED
    invites the operator to reboot and lose the instrument.
    """
    boot = {
        "antenna_id": str(os.getenv("SDRPP_ANTENNA_ID", "") or "").strip() or None,
        "feedline_id": str(os.getenv("SDRPP_FEEDLINE_ID", "") or "").strip() or "undeclared",
        "extension_mm": str(os.getenv("SDRPP_ANTENNA_EXTENSION_MM", "") or "").strip() or None,
    }
    if record is None:
        return {"runtime_declaration": "UNDECLARED", "boot_declaration": "UNDECLARED",
                "boot_environment": boot,
                "detail": "NO RUNTIME DECLARATION; THE BOOT ENVIRONMENT IS ALL THERE IS"}
    boot_extension = None
    if boot["extension_mm"]:
        try:
            boot_extension = float(boot["extension_mm"])
        except (TypeError, ValueError):
            boot_extension = "REFUSED_UNUSABLE_VALUE"
    persisted = (boot["antenna_id"] == record["antenna_id"]
                 and boot["feedline_id"] == record["feedline_id"]
                 and boot_extension == record["extension_mm"])
    return {
        "runtime_declaration": "ACTIVE",
        "boot_declaration": "PERSISTED" if persisted else "PENDING_PERSISTENCE",
        "boot_environment": boot,
        "detail": ("THE BOOT ENVIRONMENT DECLARES THE SAME INSTRUMENT" if persisted else
                   "THIS DECLARATION IS ACTIVE IN THIS PROCESS ONLY. THE BOOT "
                   "ENVIRONMENT STILL DECLARES A DIFFERENT INSTRUMENT AND A RESTART "
                   "WOULD ADOPT IT"),
        "persistence_authority": "OPERATOR_DECLARED",
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

        This store is process-local and volatile, so a declaration made through
        the API lasts exactly as long as the orchestrator does. The environment
        is what survives a restart, which makes these three variables the only
        durable statement of the signal chain.
        """
        candidate = str(os.getenv("SDRPP_ANTENNA_ID", "") or "").strip()
        if candidate not in ANTENNAS:
            return None
        with self._lock:
            if self._record is not None:
                return self._record
        # The cable is its own declaration and its own environment variable. An
        # antenna being named says nothing about how it is connected, and the
        # signal chain hashes the feedline, so guessing here would change an
        # instrument identity on the strength of a default.
        feedline = str(os.getenv("SDRPP_FEEDLINE_ID", "") or "").strip()
        payload = {"antenna_id": candidate,
                   "note": "Adopted from SDRPP_ANTENNA_ID at startup"}
        if feedline in FEEDLINES:
            payload["feedline_id"] = feedline
        # A telescopic mast without its extension is a part number, not an
        # instrument: the derived quarter wave stays UNDECLARED and every survey
        # taken through it is missing the one number that says which frequencies
        # it favours. The operator is still the only authority for it, but the
        # environment is where the answer has to live to outlive a reboot.
        extension = str(os.getenv("SDRPP_ANTENNA_EXTENSION_MM", "") or "").strip()
        if extension:
            payload["extension_mm"] = extension
        try:
            return self._record_declaration(payload)[0]
        except AntennaDeclarationRefused:
            # A configured extension that does not validate must not quietly
            # degrade into the same mast declared with no extension. That is the
            # identical part with a different frequency response and no
            # complaint, so refuse the whole bootstrap and stay UNDECLARED --
            # a visible omission rather than an invisible substitution.
            return None

    def _record_declaration(self, payload: Any) -> tuple:
        record = validate_declaration(payload)
        with self._lock:
            receipt = declaration_receipt(record, previous=self._record)
            self._record = record
            self._receipt = receipt
        return record, receipt

    def declare(self, payload: Any) -> tuple:
        """Record a declaration against the instrument that was actually in use.

        The boot environment is adopted first if nothing has been declared yet.
        Without that, the first runtime declaration compares against nothing and
        the receipt reports no signal-chain change -- while the capture owner,
        which did bootstrap from the environment, clears its ring because the
        instrument plainly changed. Two components disagreeing about whether the
        antenna moved is worse than either answer alone.
        """
        self.bootstrap_from_env()
        return self._record_declaration(payload)

    def current(self) -> Dict[str, Any]:
        with self._lock:
            if self._record is None:
                return {
                    "declared": False,
                    "antenna": None,
                    "receipt": None,
                    "state": "UNDECLARED",
                    "detail": "OPERATOR HAS NOT DECLARED AN ANTENNA",
                "persistence": declaration_persistence(None),
                    "auto_detected": False,
                    "auto_detection_note": AUTODETECT_REASON,
                }
            return {
                "declared": True,
                "antenna": dict(self._record),
                "receipt": dict(self._receipt or {}),
                "state": "DECLARED",
                "detail": f"{self._record['label']} · {DECLARATION_AUTHORITY}",
                "persistence": declaration_persistence(self._record),
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
