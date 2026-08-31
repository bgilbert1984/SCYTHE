"""Truthful, machine-readable declarations for bounded RF products."""

from __future__ import annotations

from datetime import datetime, timezone
import math
import time
from typing import Any, Mapping, Optional


def _finite(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _iso_utc(epoch: Any) -> Optional[str]:
    value = _finite(epoch)
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _fresh(latest: Any, *, now: float, maximum_age_s: float) -> bool:
    value = _finite(latest)
    return value is not None and -1.0 <= now - value <= maximum_age_s


def declare_rf_products(
    bridge_status: Mapping[str, Any],
    sparse_status: Optional[Mapping[str, Any]],
    *,
    now: Optional[float] = None,
) -> dict[str, Any]:
    """Declare transformation health independently from socket connectivity."""
    observed_at = time.time() if now is None else float(now)
    config = bridge_status.get("config") or {}
    sample_rate = _finite(config.get("sample_rate_hz"))
    fft_size = int(config.get("fft_size") or 0)
    published_bins = int(config.get("max_bins") or 0)
    frame_rate = _finite(config.get("frames_per_second")) or 1.0
    fft_max_age = max(5.0, 3.0 / frame_rate)
    latest_frame = bridge_status.get("latest_frame_at")

    sparse = sparse_status or {}
    sparse_window = _finite((sparse.get("config") or {}).get("window_seconds"))
    if sparse_window is None:
        sparse_window = _finite(sparse.get("window_seconds")) or 4.0
    sparse_max_age = max(10.0, 2.5 * sparse_window)
    latest_sparse = sparse.get("latest_observed_end")

    return {
        "capture_owner": bridge_status.get("capture_owner", "orchestrator"),
        "raw_iq_scope": "local_process_only",
        "raw_iq_browser_exposed": False,
        "products": {
            "fft_frames": {
                "state": "live" if _fresh(latest_frame, now=observed_at, maximum_age_s=fft_max_age) else "stale",
                "fft_size": fft_size or None,
                "published_bins": published_bins or None,
                "native_bin_width_hz": None if not sample_rate or not fft_size else round(sample_rate / fft_size, 6),
                "analysis_bin_width_hz": None if not sample_rate or not published_bins else round(sample_rate / published_bins, 6),
                "latest_frame_at": _iso_utc(latest_frame),
                "freshness_limit_seconds": fft_max_age,
                "authority": "derived_observation",
            },
            "sparse_supports": {
                "state": "live" if _fresh(latest_sparse, now=observed_at, maximum_age_s=sparse_max_age) else "stale",
                "model": "M1",
                "dictionary_revision": sparse.get("dictionary_revision"),
                "latest_outcome": sparse.get("latest_outcome"),
                "latest_window_at": _iso_utc(latest_sparse),
                "freshness_limit_seconds": sparse_max_age,
                "authority": "derived_inference",
            },
        },
    }
