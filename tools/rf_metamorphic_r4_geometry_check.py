#!/usr/bin/env python3
"""Offline, read-only geometry check for the r4 span-boundary design. NOT the r4 experiment.

Layer A: the frequency grid and the span predicate variants, evaluated directly (no detector).
Layer B: the PRODUCTION `detect_candidates` and single-edit mutants of its source, called directly on synthetic
         one-tone windows whose tone sits exactly on a chosen bin, to confirm the design's predictions.
EVERY Layer B row is FUNCTION-LEVEL (a direct call to detect_candidates), including those at the production length n = 524,288;
none of them exercises the sequencer's acquisition path, which refuses any window that is not byte-exact 1,048,576 bytes
(524,288 complex samples). At n != 524,288 the rows are additionally non-production geometries: detect_candidates scales sub-bin
offsets by a fixed BIN_HZ = 3.90625, so reported frequencies there are not production values.
    python3 tools/rf_metamorphic_r4_geometry_check.py > output.txt
"""
import inspect, os, sys
from fractions import Fraction
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import rf_spur_catalogue_sequencer as seq

FS = seq.SAMPLE_RATE_HZ
U = float(seq.usable_half_span_hz())
PRED = {"production(<=)": lambda f: abs(f) <= U,
        "strict(<)": lambda f: abs(f) < U,
        "widened(<=1.01u)": lambda f: abs(f) <= U * 1.01,
        "removed": lambda f: True}
EDITS = {"strict(<)": ("in_span = np.abs(freqs[1:-1]) <= usable", "in_span = np.abs(freqs[1:-1]) < usable"),
         "widened(<=1.01u)": ("usable = usable_half_span_hz()", "usable = usable_half_span_hz() * 1.01"),
         "removed": ("in_span = np.abs(freqs[1:-1]) <= usable", "in_span = np.ones(n - 2, dtype=bool)")}


def variant(edit):
    src = inspect.getsource(seq.detect_candidates)
    if edit is not None:
        old, new = edit
        assert src.count(old) == 1, old
        src = src.replace(old, new)
    ns = dict(seq.__dict__); exec(compile(src, "<variant>", "exec"), ns)
    return ns["detect_candidates"]


FUNCS = {"production(<=)": variant(None), **{k: variant(e) for k, e in EDITS.items()}}

print(f"usable half-span = {U!r} Hz ; fs = {FS} Hz ; production window n = 524288 samples (WINDOW_BYTES = {seq.WINDOW_BYTES} bytes)")
print("\n=== LAYER A: frequency grid and predicate variants (no detector) ===")
for n in (524288, 524280, 500000):
    f = np.fft.fftfreq(n, 1.0 / FS); kc = int(round(U * n / FS)); d = FS / n
    print(f"\nn={n}  bin spacing {d!r} Hz   usable/spacing = {U / d!r}")
    print(f"{'bin':>8s} {'np.fft.fftfreq value':>26s} {'ideal rational - U':>20s}  " + "  ".join(f"{k:>16s}" for k in PRED))
    for k in range(kc - 2, kc + 3):
        ideal = Fraction(k * FS, n) - Fraction(U)
        row = f"{k:8d} {f[k]!r:>26s} {float(ideal):20.3e}  " + "  ".join(f"{str(p(f[k])):>16s}" for p in PRED.values())
        print(row)
    kb = [k for k in range(kc - 2, kc + 3) if Fraction(k * FS, n) == Fraction(U)]
    print(f"  bin whose IDEAL (rational) frequency equals U: {kb or 'none'}", end="")
    if kb:
        print(f" ; computed value equals U exactly: {f[kb[0]] == U}  (<= evaluates {f[kb[0]] <= U})")
    else:
        print()

def windows(n, b, amp=0.5, sigma=0.01):
    m = np.arange(n, dtype=np.int64); tone = amp * np.exp(2j * np.pi * ((b * m) % n) / n)
    return [tone + sigma * (np.random.default_rng(1000 + i).normal(size=n) + 1j * np.random.default_rng(2000 + i).normal(size=n))
            for i in range(8)]

print("\n=== LAYER B: production detect_candidates and single-edit mutants on one-tone windows (function-level) ===")
print("Each row: tone placed exactly on bin b; 'P' = a candidate with peak_bin == b is listed, '-' = not listed;")
print("predicted = the Layer A predicate for that variant evaluated on the computed bin-centre frequency.\n")
plans = [(524288, [100000, 235928, 235929, 235930, 235931, 249037]), (524280, [235925, 235926, 235927]), (500000, [224999, 225000, 225001])]
bad = 0
print(f"{'n':>7s} {'bin':>7s} {'f(bin) Hz':>16s}  " + "  ".join(f"{k:>18s}" for k in FUNCS) + "   all-predictions-match")
for n, bins in plans:
    f = np.fft.fftfreq(n, 1.0 / FS)
    for b0 in bins:
        for b in (b0, n - b0):
            w = windows(n, b); cells = []; ok = True
            for name, fn in FUNCS.items():
                got = any(c.peak_bin == b for c in fn(w)); pred = PRED[name](f[b])
                ok &= (got == pred); cells.append(f"{'P' if got else '-'}(pred {'P' if pred else '-'})")
            bad += not ok
            print(f"{n:7d} {b:7d} {f[b]:16.5f}  " + "  ".join(f"{c:>18s}" for c in cells) + f"   {ok}")
print(f"\nrows whose observed presence disagrees with the Layer A prediction: {bad}")
