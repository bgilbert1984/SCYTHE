"""Validation boundary for live measured-RF spectral summaries.

The GraphOps ingest path accepts no raw IQ and never upgrades client claims.
RFObservationStore derives SNR and stable evidence identity after validation.
"""

from __future__ import annotations

import math
from typing import Any, Dict


ALLOWED_FIELDS = {
    "sensor_id", "sequence", "timestamp", "center_frequency_hz",
    "peak_frequency_hz", "sample_rate_hz", "peak_dbfs", "noise_floor_dbfs",
    "observation_origin",
}

# Frames arriving over HTTP were not produced by this process's IQ bridge. They
# must not inherit the bridge's source label, or a hand-entered value becomes
# indistinguishable from a receiver measurement once it is retained.
OBSERVATION_ORIGINS = {"OPERATOR_SYNTHETIC", "EXTERNAL_SENSOR"}
DEFAULT_OBSERVATION_ORIGIN = "EXTERNAL_SENSOR"


def _finite(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _origin(value: Any) -> str:
    if value is None:
        return DEFAULT_OBSERVATION_ORIGIN
    origin = str(value).strip().upper()
    if origin not in OBSERVATION_ORIGINS:
        raise ValueError(f"observation_origin must be one of {sorted(OBSERVATION_ORIGINS)}")
    return origin


def validate_measured_rf_frame(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("measured RF frame must be an object")
    unknown = set(payload) - ALLOWED_FIELDS
    if unknown:
        raise ValueError(f"unknown measured RF fields: {sorted(unknown)}")
    sensor_id = payload.get("sensor_id")
    if not isinstance(sensor_id, str) or not 1 <= len(sensor_id) <= 128:
        raise ValueError("sensor_id must contain 1-128 characters")
    frame = {
        "sensor_id": sensor_id,
        "sequence": int(payload.get("sequence", 0)),
        "timestamp": _finite(payload.get("timestamp"), "timestamp"),
        "center_frequency_hz": _finite(payload.get("center_frequency_hz"), "center_frequency_hz"),
        "peak_frequency_hz": _finite(payload.get("peak_frequency_hz"), "peak_frequency_hz"),
        "sample_rate_hz": _finite(payload.get("sample_rate_hz"), "sample_rate_hz"),
        "peak_dbfs": _finite(payload.get("peak_dbfs"), "peak_dbfs"),
        "noise_floor_dbfs": _finite(payload.get("noise_floor_dbfs"), "noise_floor_dbfs"),
        "observation_origin": _origin(payload.get("observation_origin")),
    }
    if not 0 < frame["center_frequency_hz"] <= 1e12 or not 0 < frame["peak_frequency_hz"] <= 1e12:
        raise ValueError("RF frequencies must be between 0 and 1 THz")
    if not 0 < frame["sample_rate_hz"] <= 1e10:
        raise ValueError("sample_rate_hz must be between 0 and 10 GHz")
    if not -300 <= frame["peak_dbfs"] <= 20 or not -300 <= frame["noise_floor_dbfs"] <= 20:
        raise ValueError("dBFS values are outside the accepted range")
    return frame


def ingest_measured_rf(store: Any, payload: Any) -> Dict[str, Any]:
    frame = validate_measured_rf_frame(payload)
    observation = store.ingest_frame(frame)
    return {
        "status": "accepted" if observation else "filtered",
        "observation": observation,
        "authority": "MEASURED_SPECTRAL_SUMMARY",
        "evidenceClass": "OBSERVED" if observation else None,
        "rawIqAccepted": False,
        "filterReason": None if observation else "below SNR threshold or within sensor/frequency cooldown",
    }
