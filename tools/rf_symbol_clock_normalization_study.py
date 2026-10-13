#!/usr/bin/env python3
"""Shadow study: can the squared-envelope statistic be normalised against shaped noise?

``rf_symbol_clock`` is frozen.  Stationary band-limited noise at 30-50 kHz crosses
its 2.5 threshold because the smooth falling squared-envelope spectrum meets a
one-sided local-median floor at the lower search edge
(``STATIONARY_BANDLIMITED_NOISE_SLOPING_CYCLIC_SPECTRUM``).  This tool does not
change the detector.  It computes alternative statistics from the *same* production
channelized product and reports how well each separates noise from a real clock.

Candidates
----------
``baseline``      the frozen statistic, called as-is.
``edge_guard``    baseline, zeroed when the peak sits within EDGE_GUARD_BINS of the
                  search floor.  Cheap, but also blinds the detector to a real
                  clock at the floor, which is declared rather than hidden.
``detrended``     spectrum divided by a wide running median (edge-replicated), peak
                  chosen on the ratio, statistic = max ratio / median ratio.
``whitened``      spectrum divided by the mean cyclic spectrum of phase-randomised
                  surrogates of the same channelized samples (same PSD, Gaussian,
                  no cyclostationarity), same max/median form.

Thresholds are NOT carried over from the baseline: each candidate has its own scale.
For each candidate the threshold is set from a *calibration* split of the noise
trials (max over calibration noise, times a margin) and evaluated on a disjoint
*held-out* split.  Synthetic inputs only; nothing here is a false-alarm probability.

Usage
-----
    python3 tools/rf_symbol_clock_normalization_study.py --out study.json
    python3 tools/rf_symbol_clock_normalization_study.py --smoke
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Callable, Dict, List, Optional

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import test_rf_symbol_clock as fx  # noqa: E402  (fixtures; same inputs as the audit)
from rf_symbol_clock import (  # noqa: E402
    CYCLIC_SEGMENT_MAX, CYCLIC_SEGMENT_MIN, CYCLIC_TARGET_AVERAGES,
    DECISION_THRESHOLD, MAX_SYMBOL_RATE_FRACTION, MIN_SEARCH_BINS,
    MIN_SYMBOL_PERIODS_PER_SEGMENT, PEAK_GUARD_BINS, detect,
    squared_envelope_statistic,
)

NOISE_WIDTHS_HZ = (30_000.0, 40_000.0, 50_000.0, 60_000.0, 80_000.0, 100_000.0)
CLOCK_RATES_HZ = (50_000.0, 100_000.0, 200_000.0)
EDGE_GUARD_BINS = 32
TREND_HALF_WIDTH = 64
SURROGATES = 6
THRESHOLD_MARGIN = 1.15


def _cyclic_spectrum(samples: np.ndarray):
    """Same Welch construction as the frozen detector (duplicated, NOT test-enforced; re-check if the frozen detector changes)."""
    envelope = np.abs(samples.astype(np.complex64)) ** 2
    centred = envelope - float(envelope.mean())
    count = centred.size
    ideal = 1 << max(int(count // CYCLIC_TARGET_AVERAGES).bit_length() - 1, 0)
    segment = min(int(min(CYCLIC_SEGMENT_MAX, max(CYCLIC_SEGMENT_MIN, ideal))), count)
    window = np.hanning(segment)
    hop = max(1, segment // 2)
    acc = np.zeros(segment // 2 + 1)
    n = 0
    for start in range(0, count - segment + 1, hop):
        acc += np.abs(np.fft.rfft(centred[start:start + segment] * window)) ** 2
        n += 1
    low = MIN_SYMBOL_PERIODS_PER_SEGMENT
    high = min(acc.size, int(MAX_SYMBOL_RATE_FRACTION * segment))
    return (acc / n)[low:high], segment


def _running_median(x: np.ndarray, half: int) -> np.ndarray:
    padded = np.concatenate([np.full(half, x[:half].mean()), x,
                             np.full(half, x[-half:].mean())])
    windows = np.lib.stride_tricks.sliding_window_view(padded, 2 * half + 1)
    return np.median(windows, axis=1)


def _max_over_median(ratio: np.ndarray) -> float:
    peak = int(np.argmax(ratio))
    mask = np.ones(ratio.size, dtype=bool)
    mask[max(0, peak - PEAK_GUARD_BINS):peak + PEAK_GUARD_BINS + 1] = False
    return float(ratio[peak] / np.median(ratio[mask])), peak


def stat_baseline(samples, rate):
    s, _, res, _, floor = squared_envelope_statistic(samples, rate)
    return s


def stat_edge_guard(samples, rate):
    s, found, res, _, floor = squared_envelope_statistic(samples, rate)
    if s is None:
        return None
    if (found - floor) / res < EDGE_GUARD_BINS:
        return 0.0
    return s


def stat_detrended(samples, rate):
    from rf_symbol_clock_v2 import detrended_statistic
    return detrended_statistic(samples, rate)[0]


def stat_whitened(samples, rate, *, seed=1234):
    band, _ = _cyclic_spectrum(samples)
    if band.size < MIN_SEARCH_BINS:
        return None
    rng = np.random.default_rng(seed)
    spec = np.fft.fft(samples.astype(np.complex128))
    null = np.zeros_like(band)
    for _ in range(SURROGATES):
        phase = np.exp(2j * np.pi * rng.random(spec.size))
        surrogate = np.fft.ifft(np.abs(spec) * phase).astype(np.complex64)
        null += _cyclic_spectrum(surrogate)[0]
    null /= SURROGATES
    null = _running_median(null, 8)          # light smoothing only; keeps the slope
    if not np.all(null > 0):
        return None
    return _max_over_median(band / null)[0]


CANDIDATES: Dict[str, Callable] = {
    "baseline": stat_baseline,
    "edge_guard": stat_edge_guard,
    "detrended": stat_detrended,
    "whitened": stat_whitened,
}

CARRIER = np.exp(2j * np.pi * 400_000.0 * np.arange(524_288) / 2_048_000.0)


def _channelized(samples):
    result, ring = fx._real_channelization((samples * CARRIER).astype(np.complex64))
    if result.product.outcome != "CHANNELIZED":
        return None, None
    return result, ring


MASKS = ("raised", "gaussian", "brick")


def _shaped_noise(width_hz, mask, seed, count=524_288, rate_hz=2_048_000.0, noise=0.01):
    """Unit-power complex noise with the named spectral mask.

    ``raised`` is the fixture's own mask.  ``gaussian`` has a Gaussian PSD that is
    20 dB down at +/- width/2.  ``brick`` is flat to +/- width/2 and zero beyond.
    """
    if mask == "raised":
        return fx._band_limited_noise(width_hz, seed=seed)
    rng = np.random.default_rng(seed)
    white = rng.normal(0, 1, count) + 1j * rng.normal(0, 1, count)
    freq = np.fft.fftfreq(count, 1.0 / rate_hz)
    if mask == "gaussian":
        sigma2 = (width_hz / 2.0) ** 2 / (2.0 * np.log(100.0))
        amp = np.exp(-(freq ** 2) / (4.0 * sigma2))      # sqrt of the PSD
    else:
        amp = (np.abs(freq) <= width_hz / 2.0).astype(float)
    shaped = np.fft.ifft(np.fft.fft(white) * amp)
    shaped = shaped / np.sqrt(np.mean(np.abs(shaped) ** 2))
    shaped = shaped + noise * (rng.normal(0, 1, count) + 1j * rng.normal(0, 1, count))
    return shaped.astype(np.complex64)


def run_trial(kind: str, param: float, seed: int) -> Optional[Dict]:
    if kind.startswith("noise"):
        base = _shaped_noise(param, kind.split(":")[1], seed)
    else:
        base = fx._psk(param, count=524_288, rate_hz=2_048_000.0, noise=0.02, seed=seed)
    result, ring = _channelized(base)
    if result is None:
        return None
    samples = result.samples
    rate = _rate_of(result, ring)
    verdict = detect(result, ring=ring)
    if verdict.outcome == "INSUFFICIENT_WINDOW":
        # Production would not run the detector here (fewer than MINIMUM_SAMPLE_COUNT
        # samples).  Counting it as a crossing or a miss would be measuring the tool.
        return {"kind": kind, "param": param, "seed": seed, "gated": True}
    return {"kind": kind, "param": param, "seed": seed,
            "frozen_outcome": verdict.outcome,
            "stats": {name: fn(samples, rate) for name, fn in CANDIDATES.items()}}


def _rate_of(result, ring):
    from rf_detector_contract import admit_for_detection
    return float(admit_for_detection(result, ring=ring)["output_sample_rate_hz"])


def summarise(trials: List[Dict]) -> Dict:
    out = {}
    for name in CANDIDATES:
        noise = [t for t in trials if t["kind"].startswith("noise") and t["stats"][name] is not None]
        clock = [t for t in trials if t["kind"] == "clock" and t["stats"][name] is not None]
        cal = [t["stats"][name] for t in noise if t["seed"] % 2 == 0]
        held = [t for t in noise if t["seed"] % 2 == 1]
        threshold = (DECISION_THRESHOLD if name == "baseline"
                     else max(cal) * THRESHOLD_MARGIN) if cal else None
        by_width = {}
        for t in held:
            by_width.setdefault((t["kind"].split(":")[1], t["param"]), []).append(t["stats"][name])
        by_rate = {}
        for t in clock:
            by_rate.setdefault(t["param"], []).append(t["stats"][name])
        out[name] = {
            "threshold": threshold,
            "threshold_source": ("FROZEN" if name == "baseline"
                                 else "MAX_OF_CALIBRATION_NOISE_X_MARGIN"),
            "heldout_noise_crossings": {
                f"{m}@{int(w)}": f"{sum(v >= threshold for v in vals)}/{len(vals)}"
                for (m, w), vals in sorted(by_width.items())},
            "heldout_noise_max": {f"{m}@{int(w)}": round(max(vals), 3)
                                  for (m, w), vals in sorted(by_width.items())},
            "clock_detections": {
                str(int(r)): f"{sum(v >= threshold for v in vals)}/{len(vals)}"
                for r, vals in sorted(by_rate.items())},
            "clock_min": {str(int(r)): round(min(v), 3) for r, v in sorted(by_rate.items())},
        }
    out["unusable_trials"] = sum(1 for t in trials if any(v is None for v in t["stats"].values()))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--trials", type=int, default=40, help="seeds per cell")
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    n = 4 if args.smoke else args.trials
    trials = []
    refused = {}
    gated = {}
    for mask in MASKS:
        for width in NOISE_WIDTHS_HZ:
            for seed in range(n):
                t = run_trial(f"noise:{mask}", width, seed)
                if t and t.get("gated"):
                    gated[f"{mask}@{int(width)}"] = gated.get(f"{mask}@{int(width)}", 0) + 1
                elif t:
                    trials.append(t)
                else:
                    key = f"{mask}@{int(width)}"
                    refused[key] = refused.get(key, 0) + 1
    for rate in CLOCK_RATES_HZ:
        for seed in range(max(4, n // 4)):
            t = run_trial("clock", rate, seed)
            if t and not t.get("gated"):
                trials.append(t)
    summary = summarise(trials)
    doc = {"schema": "scythe.rf-symbol-clock-normalization-study.v0",
           "synthetic_only": True, "false_alarm_probability": None,
           "trial_count": len(trials), "channelizer_refusals": refused,
           "insufficient_window_gated": gated,
           "summary": summary}
    text = json.dumps(doc, indent=2)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
