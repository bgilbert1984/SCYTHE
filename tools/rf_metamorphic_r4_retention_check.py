#!/usr/bin/env python3
"""Offline, read-only check: does retention (measure_persistence) apply the usable-span predicate? (r4 design evidence; not the r4 experiment)
Output: for bins on/just outside/well outside the usable span, whether detect_candidates ADMITS the tone and what measure_persistence reports."""
import os, sys
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import rf_spur_catalogue_sequencer as seq
from rf_spur_retention_estimator import measure_persistence, RetentionRefused
FS = seq.SAMPLE_RATE_HZ; n = 524288; u = float(seq.usable_half_span_hz())

def windows(b, amp=0.5, sigma=0.01):
    m = np.arange(n, dtype=np.int64); tone = amp * np.exp(2j * np.pi * ((b * m) % n) / n)
    return [tone + sigma * (np.random.default_rng(1000 + i).normal(size=n) + 1j * np.random.default_rng(2000 + i).normal(size=n)) for i in range(8)]

print("RETENTION (measure_persistence) is given a feature frequency; it does not apply the usable-span predicate (only |f| <= fs/2):")
for b in (235929, 235930, 249037):
    f = np.fft.fftfreq(n, 1 / FS)[b]; w = windows(b)
    adm = any(c.peak_bin == b for c in seq.detect_candidates(w))
    try:
        o = measure_persistence(tuning_id="r4-geometry", windows=w, sample_rate_hz=float(FS), feature_baseband_hz=float(f))
        ret = f"qualifying={o.qualifying()} persistent={o.persistent()}"
    except RetentionRefused as e:
        ret = f"REFUSED {e.code}"
    print(f"  bin {b} f={f:.5f} Hz  in_span={abs(f) <= u!s:5s}  ADMITTED by detect_candidates: {adm!s:5s}  retention measurement: {ret}")
