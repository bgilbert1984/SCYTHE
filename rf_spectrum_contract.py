"""Versioned SCYTHE spectrum-product contract for local analysis workers."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any, Mapping


SPECTRUM_SCHEMA = "scythe.rf.spectrum.v1"
AUTHORITY = "derived_observation"
_REQUIRED = {
    "schema", "frame_id", "sensor_id", "captured_at", "center_frequency_hz",
    "sample_rate_hz", "fft_size", "window", "native_bin_width_hz",
    "analysis_bin_width_hz", "power_db", "clock_quality", "tuner_ppm",
    "gain_db", "signal_chain_hash", "authority",
}


def _number(value: Any, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(result) or (positive and result <= 0):
        raise ValueError(f"{name} must be finite" + (" and positive" if positive else ""))
    return result


def _captured_at(epoch: Any) -> str:
    timestamp = _number(epoch, "timestamp")
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def validate_spectrum_frame(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Reject malformed, unbounded, or authority-confused worker inputs."""
    if not isinstance(payload, Mapping):
        raise ValueError("spectrum frame must be an object")
    missing = sorted(_REQUIRED - set(payload))
    unknown = sorted(set(payload) - _REQUIRED)
    if missing:
        raise ValueError(f"missing spectrum fields: {', '.join(missing)}")
    if unknown:
        raise ValueError(f"unknown spectrum fields: {', '.join(unknown)}")
    if payload["schema"] != SPECTRUM_SCHEMA:
        raise ValueError("unsupported spectrum schema")
    if payload["authority"] != AUTHORITY:
        raise ValueError("spectrum authority must be derived_observation")
    for name in ("frame_id", "sensor_id", "captured_at", "window", "clock_quality"):
        if not isinstance(payload[name], str) or not payload[name].strip() or len(payload[name]) > 160:
            raise ValueError(f"{name} must be a bounded non-empty string")
    chain_hash = str(payload["signal_chain_hash"])
    if (not chain_hash.startswith("sha256:") or len(chain_hash) != 71
            or any(character not in "0123456789abcdef" for character in chain_hash[7:])):
        raise ValueError("signal_chain_hash must be a SHA-256 identifier")
    fft_number = _number(payload["fft_size"], "fft_size", positive=True)
    if not fft_number.is_integer():
        raise ValueError("fft_size must be an integer")
    fft_size = int(fft_number)
    if not 64 <= fft_size <= 65536 or fft_size & (fft_size - 1):
        raise ValueError("fft_size must be a power of two from 64 to 65536")
    for name in ("center_frequency_hz", "sample_rate_hz", "native_bin_width_hz", "analysis_bin_width_hz"):
        _number(payload[name], name, positive=name != "center_frequency_hz")
    _number(payload["tuner_ppm"], "tuner_ppm")
    if payload["gain_db"] is not None:
        _number(payload["gain_db"], "gain_db")
    bins = payload["power_db"]
    if not isinstance(bins, list) or not 16 <= len(bins) <= 4096:
        raise ValueError("power_db must contain 16 to 4096 bounded bins")
    clean_bins = [round(_number(value, "power_db bin"), 4) for value in bins]
    result = dict(payload)
    result["power_db"] = clean_bins
    result["fft_size"] = fft_size
    return result


def build_spectrum_frame(frame: Mapping[str, Any], *, tuner_ppm: float = 0.0,
                         gain_db: float | None = None, window: str = "hann") -> dict[str, Any]:
    """Build a worker product from a bounded bridge frame; never accepts IQ."""
    bins = frame.get("bins_dbfs")
    sample_rate = _number(frame.get("sample_rate_hz"), "sample_rate_hz", positive=True)
    fft_size = int(_number(frame.get("fft_size"), "fft_size", positive=True))
    if not isinstance(bins, list):
        raise ValueError("bridge frame has no bounded spectrum bins")
    identity = {
        "sensor_id": frame.get("sensor_id"), "sequence": frame.get("sequence"),
        "timestamp": frame.get("timestamp"), "center_frequency_hz": frame.get("center_frequency_hz"),
    }
    frame_hash = hashlib.sha256(json.dumps(identity, sort_keys=True, default=str).encode()).hexdigest()
    chain = {
        "sample_rate_hz": sample_rate, "fft_size": fft_size, "published_bins": len(bins),
        "window": window, "tuner_ppm": tuner_ppm, "gain_db": gain_db,
    }
    chain_hash = "sha256:" + hashlib.sha256(
        json.dumps(chain, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return validate_spectrum_frame({
        "schema": SPECTRUM_SCHEMA,
        "frame_id": f"rf-frame-{frame_hash[:24]}",
        "sensor_id": str(frame.get("sensor_id") or "unknown-sensor"),
        "captured_at": _captured_at(frame.get("timestamp")),
        "center_frequency_hz": frame.get("center_frequency_hz"),
        "sample_rate_hz": sample_rate,
        "fft_size": fft_size,
        "window": window,
        "native_bin_width_hz": sample_rate / fft_size,
        "analysis_bin_width_hz": sample_rate / len(bins),
        "power_db": bins,
        "clock_quality": str(frame.get("clock_quality") or "host_wall_clock"),
        "tuner_ppm": tuner_ppm,
        "gain_db": gain_db,
        "signal_chain_hash": chain_hash,
        "authority": AUTHORITY,
    })


def analyze_spectrum_product(processor: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Invoke a local spectrum worker and enforce its non-evidence boundary."""
    product = validate_spectrum_frame(payload)
    result = processor.process_spectrum_frame(
        product["power_db"],
        center_frequency_hz=product["center_frequency_hz"],
        sample_rate_hz=product["sample_rate_hz"],
        native_bin_width_hz=product["native_bin_width_hz"],
        analysis_bin_width_hz=product["analysis_bin_width_hz"],
        window=product["window"],
        signal_chain_hash=product["signal_chain_hash"],
        captured_at=product["captured_at"],
        frame_id=product["frame_id"],
    )
    if not isinstance(result, dict):
        raise ValueError("spectrum worker returned no result object")
    if result.get("schema") != "nerfengine.rf.analysis.v1":
        raise ValueError("spectrum worker returned an unsupported result schema")
    if result.get("source_frame_id") != product["frame_id"]:
        raise ValueError("spectrum worker result does not identify its source frame")
    if result.get("authority") != "experimental_inference" or result.get("promotion") != "not_graph_evidence":
        raise ValueError("spectrum worker crossed the graph-evidence boundary")
    return result
