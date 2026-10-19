#!/usr/bin/env python3
"""Report exact (bitwise) float differences against the baseline, unrounded.

Usage: diagnose_exactness.py baseline.json candidate.json
Prints the count of float fields that are not exactly equal, the maximum absolute
deltas, and the first differing field with repr() of both values.
"""
import json, sys
a, b = (json.load(open(p))["per_fixture"] for p in sys.argv[1:3])
n = 0; first = None; maxf = maxe = 0.0
for x, y in zip(a, b):
    for p, q in zip(x["candidates"], y["candidates"]):
        for k in ("baseband_hz", "excess_db", "match_error_hz"):
            if p.get(k) != q.get(k):
                n += 1
                first = first or (x["name"], k, repr(p.get(k)), repr(q.get(k)))
        maxf = max(maxf, abs(p["baseband_hz"] - q["baseband_hz"]))
        maxe = max(maxe, abs(p["excess_db"] - q["excess_db"]))
print(f"inexact_float_fields={n} max_freq_delta={maxf!r} max_excess_delta={maxe!r}")
print(f"first_inexact={first}")
