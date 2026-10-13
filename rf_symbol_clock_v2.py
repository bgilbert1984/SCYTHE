"""``squared-envelope-cyclic.v2-detrended``: a shadow revision, alongside v1.

Why a second module and not an edit
-----------------------------------
``squared-envelope-cyclic.v1`` is registered in the signal-family registry, named in
validation manifests and pinned by characterisation tests that describe its known
false crossings.  Rewriting it in place would silently change what those records
refer to.  v2 is therefore a separate method id and revision, in shadow, with its
own provisional threshold; v1 is untouched.  v2 is **not** registered in
``rf_signal_family`` and no validation manifest names it.

What changed relative to v1
---------------------------
v1 divides the peak of the averaged squared-envelope spectrum by the median of a
one-sided 129-bin window around that peak.  A smooth falling spectrum (stationary
band-limited noise) meets that window at the lower search edge and the peak exceeds
its own local median from the slope alone.

v2 divides the whole search band by a wide running median first (edge-replicated),
chooses the peak on that ratio, and reports ``max(ratio) / median(ratio)`` with the
peak neighbourhood excluded from the denominator.  Detrending reduces the observed
slope-driven excess under the tested conditions; residual lower-edge concentration
remains and is not established to be eliminated.

Development evidence (synthetic only; ``tools/rf_symbol_clock_normalization_study.py``;
seeds 0-99; the threshold below is development-informed)
--------------------------------------------------------------------------
Noise-only, production channelizer, raised-edge / Gaussian / brick-wall masks at
30-100 kHz, 60 seeds per cell: no v2 statistic above 3.30.  Raised-cosine QPSK at
50 / 100 / 200 kBd: no v2 statistic below 23.8.  v1 crossed its threshold on every
raised-edge trial at 30-50 kHz, on every Gaussian trial at 30-60 kHz (statistic up
to 25), and on part of the brick-wall 30-40 kHz cells.

Frozen holdout evidence (seeds >= 1000): docs/evidence/symbol-clock-v2-frozen-eval-2026-10-12/.
The largest v2 noise statistic there was 3.43 (the development maximum above is 3.30), and
weak-signal sensitivity was not established because the channelizer refused those inputs.

What it does not establish
--------------------------
The threshold is provisional, set above the observed synthetic noise maximum with
margin, and is not a false-alarm probability.  Nothing here is validated against
real emitters, other pulse shapes, or the periodic transport artefact, which a
cyclic detector cannot separate from slow symbols.  Shadow only; it never promotes.
"""

from __future__ import annotations

import dataclasses
import math
from typing import Any, Dict, Optional, Tuple

import numpy as np

from rf_detector_contract import DetectorInputRefused, admit_for_detection
from rf_symbol_clock import (
    CONSTANT_ENVELOPE_CV, CYCLIC_SEGMENT_MAX, CYCLIC_SEGMENT_MIN,
    CYCLIC_TARGET_AVERAGES, ELIGIBLE_CHANNEL_PURPOSE, MAX_SYMBOL_RATE_FRACTION,
    MINIMUM_SAMPLE_COUNT, MIN_SEARCH_BINS, MIN_SYMBOL_PERIODS_PER_SEGMENT,
    PEAK_GUARD_BINS, SymbolClockVerdict, THRESHOLD_EXCEEDED, THRESHOLD_NOT_COMPARED,
    THRESHOLD_NOT_EXCEEDED, _verdict,
)

METHOD_ID = "squared-envelope-cyclic.v2-detrended"
METHOD_REVISION = "squared-envelope-cyclic.v2-detrended"
SUPERSEDES_NOTHING = "v1 REMAINS REGISTERED AND UNCHANGED; v2 IS A PARALLEL SHADOW REVISION"
NULL_MODEL = "CHANNELIZED_NOISE_PLUS_NONCYCLIC_SIGNAL_DETRENDED"

# Half-width of the running median that models the smooth, non-cyclic part of the
# squared-envelope spectrum.  A line is a few bins wide, so a 129-bin median does
# not absorb it; a spectral slope varies slowly across 129 bins, so it is removed.
TREND_HALF_WIDTH = 64
DECISION_THRESHOLD = 5.0
THRESHOLD_BASIS = "PROVISIONAL_ABOVE_OBSERVED_SYNTHETIC_NOISE_MAX_X1.5"
THRESHOLD_STATUS = "PROVISIONAL"
FALSE_ALARM_PROBABILITY = None
VALIDATION_STATUS = "REGISTERED_NOT_VALIDATED"
SHADOW_MODE = True
PROMOTION_STATE = "SHADOW_NO_PROMOTION"

OBSERVED_SYNTHETIC = {
    "noise_statistic_max": 3.295,
    "noise_cells": "raised|gaussian|brick x 30-100 kHz, 60 seeds per cell",
    "clock_statistic_min": 23.818,
    "clock_cells": "raised-cosine QPSK 50/100/200 kBd, noise 0.02",
    "note": "SYNTHETIC. NOT A FALSE-ALARM PROBABILITY AND NOT A VALIDATION",
}


def _running_median(values: np.ndarray, half: int) -> np.ndarray:
    padded = np.concatenate([np.full(half, values[:half].mean()), values,
                             np.full(half, values[-half:].mean())])
    windows = np.lib.stride_tricks.sliding_window_view(padded, 2 * half + 1)
    return np.median(windows, axis=1)


def detrended_statistic(samples: np.ndarray, sample_rate_hz: float,
                        ) -> Tuple[Optional[float], Optional[float], Optional[float],
                                   float, Optional[float]]:
    """``(statistic, symbol_rate_hz, resolution_hz, envelope_cv, search_floor_hz)``.

    Same return shape as ``rf_symbol_clock.squared_envelope_statistic`` so a caller
    can swap them, and the same Welch construction and search band.
    """
    envelope = np.abs(np.asarray(samples, dtype=np.complex64)) ** 2
    mean = float(envelope.mean())
    if not mean > 0.0:
        return None, None, None, 0.0, None
    envelope_cv = float(envelope.std() / mean)
    if envelope_cv < CONSTANT_ENVELOPE_CV:
        return None, None, None, envelope_cv, None

    centred = envelope - mean
    count = centred.size
    ideal = 1 << max(int(count // CYCLIC_TARGET_AVERAGES).bit_length() - 1, 0)
    segment = min(int(min(CYCLIC_SEGMENT_MAX, max(CYCLIC_SEGMENT_MIN, ideal))), count)
    if segment < CYCLIC_SEGMENT_MIN:
        return None, None, None, envelope_cv, None
    window = np.hanning(segment)
    hop = max(1, segment // 2)
    accumulator = np.zeros(segment // 2 + 1, dtype=np.float64)
    averages = 0
    for start in range(0, count - segment + 1, hop):
        accumulator += np.abs(np.fft.rfft(centred[start:start + segment] * window)) ** 2
        averages += 1
    if averages == 0:
        return None, None, None, envelope_cv, None
    spectrum = accumulator / averages
    resolution = sample_rate_hz / segment

    low = MIN_SYMBOL_PERIODS_PER_SEGMENT
    search_floor = low * resolution
    high = min(spectrum.size, int(MAX_SYMBOL_RATE_FRACTION * segment))
    if high - low < MIN_SEARCH_BINS:
        return None, None, resolution, envelope_cv, search_floor
    band = spectrum[low:high]
    trend = _running_median(band, TREND_HALF_WIDTH)
    if not np.all(trend > 0.0):
        return None, None, resolution, envelope_cv, search_floor
    ratio = band / trend
    peak = int(np.argmax(ratio))
    mask = np.ones(ratio.size, dtype=bool)
    mask[max(0, peak - PEAK_GUARD_BINS):peak + PEAK_GUARD_BINS + 1] = False
    reference = float(np.median(ratio[mask]))
    if not reference > 0.0:
        return None, None, resolution, envelope_cv, search_floor
    return (float(ratio[peak]) / reference, float((low + peak) * resolution),
            resolution, envelope_cv, search_floor)


def _v2(outcome: str, view: Optional[Dict[str, Any]], *,
        statistic: Optional[float] = None, **kwargs) -> SymbolClockVerdict:
    base = _verdict(outcome, view, statistic=statistic, **kwargs)
    comparison = (THRESHOLD_NOT_COMPARED if statistic is None
                  else THRESHOLD_EXCEEDED if statistic >= DECISION_THRESHOLD
                  else THRESHOLD_NOT_EXCEEDED)
    return dataclasses.replace(
        base, method_id=METHOD_ID, method_revision=METHOD_REVISION,
        decision_threshold=DECISION_THRESHOLD, threshold_comparison=comparison,
        threshold_status=THRESHOLD_STATUS, null_model=NULL_MODEL)


def detect(channelization: Any, *, ring: Any = None) -> SymbolClockVerdict:
    """Shadow verdict from the detrended statistic.  Gating is identical to v1."""
    view: Optional[Dict[str, Any]] = None
    try:
        try:
            view = admit_for_detection(channelization, ring=ring)
        except DetectorInputRefused:
            return _v2("SOURCE_PRODUCT_UNVERIFIED", None)
        if view.get("channel_purpose") != ELIGIBLE_CHANNEL_PURPOSE:
            return _v2("CHANNEL_PURPOSE_NOT_ELIGIBLE", view)
        samples = channelization.samples
        rate = view["output_sample_rate_hz"]
        if not isinstance(rate, (int, float)) or not math.isfinite(rate) or rate <= 0:
            return _v2("TIMING_QUALITY_INSUFFICIENT", view, sample_count=int(samples.size))
        if samples.size < MINIMUM_SAMPLE_COUNT:
            return _v2("INSUFFICIENT_WINDOW", view, sample_count=int(samples.size))
        statistic, symbol_rate, resolution, cv, floor_hz = detrended_statistic(
            samples, float(rate))
        common = dict(envelope_cv=cv, resolution_hz=resolution,
                      search_floor_hz=floor_hz, sample_count=int(samples.size))
        if cv < CONSTANT_ENVELOPE_CV:
            return _v2("CONSTANT_ENVELOPE", view, **common)
        if statistic is None:
            return _v2("INSUFFICIENT_WINDOW", view, **common)
        outcome = ("SYMBOL_CLOCK_LIKE_FEATURE" if statistic >= DECISION_THRESHOLD
                   else "NO_SYMBOL_CLOCK")
        return _v2(outcome, view, statistic=round(statistic, 4),
                   symbol_rate_hz=symbol_rate, **common)
    except Exception:
        return _v2("DETECTOR_ERROR", view)


def detector_status() -> Dict[str, Any]:
    return {
        "method_id": METHOD_ID,
        "method_revision": METHOD_REVISION,
        "state": "IMPLEMENTED_SHADOW_MODE",
        "relationship_to_v1": SUPERSEDES_NOTHING,
        "registered_in_signal_family": False,
        "shadow_mode": SHADOW_MODE,
        "promotion_state": PROMOTION_STATE,
        "validation_status": VALIDATION_STATUS,
        "decision_threshold": DECISION_THRESHOLD,
        "threshold_basis": THRESHOLD_BASIS,
        "threshold_status": THRESHOLD_STATUS,
        "false_alarm_probability": FALSE_ALARM_PROBABILITY,
        "trend_half_width_bins": TREND_HALF_WIDTH,
        "observed_synthetic": dict(OBSERVED_SYNTHETIC),
        "digital_reachable": False,
        "raw_iq_exposed": False,
    }
