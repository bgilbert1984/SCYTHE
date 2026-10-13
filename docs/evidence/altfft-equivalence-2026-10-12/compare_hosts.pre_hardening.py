#!/usr/bin/env python3
"""Compare two dedirock_equivalence.py result files at the pinned 1e-9 tolerance.

Usage: compare_hosts.py results_a.json results_b.json
Exit 0 only if code hashes match and every compared field agrees.
"""
import json, sys
TOL = 1e-9
a, b = (json.load(open(p)) for p in sys.argv[1:3])
bad = []
if a["code"] != b["code"]:
    bad.append("code hashes differ")
pa, pb = a["per_fixture"], b["per_fixture"]
if [f["name"] for f in pa] != [f["name"] for f in pb]:
    bad.append("fixture lists differ")
n = 0; maxf = maxe = maxm = 0.0
for x, y in zip(pa, pb):
    for k in ("fixture_sha256_16", "candidate_count", "matched_tones", "unmatched_count"):
        if x[k] != y[k]:
            bad.append(f"{x['name']}: {k}")
    if len(x["candidates"]) != len(y["candidates"]):
        bad.append(f"{x['name']}: candidate list length {len(x['candidates'])} != {len(y['candidates'])}")
    for p, q in zip(x["candidates"], y["candidates"]):
        n += 1
        for k in ("peak_bin", "qualifying_repeats", "persistent", "matched_tone"):
            if p[k] != q[k]:
                bad.append(f"{x['name']}: {k}")
        df = abs(p["baseband_hz"] - q["baseband_hz"]); de = abs(p["excess_db"] - q["excess_db"])
        dm = abs((p.get("match_error_hz") or 0) - (q.get("match_error_hz") or 0))
        maxf, maxe, maxm = max(maxf, df), max(maxe, de), max(maxm, dm)
        if max(df, de, dm) > TOL:
            bad.append(f"{x['name']}: numeric delta {df} {de} {dm}")
unmatched = sum(any(c["persistent"] and c["matched_tone"] is None for c in f["candidates"]) for f in pb)
worst = max((c.get("match_error_hz") or 0) for f in pb for c in f["candidates"])
print(f"fixtures={len(pa)} candidates={n} max_freq_delta={maxf} max_excess_delta={maxe} "
      f"max_match_error_delta={maxm} exact_equal={pa == pb}")
print(f"unmatched_persistent_fixtures={unmatched} max_tone_match_error_hz={worst:.4f}")
print("MISMATCHES:", bad or "none")
sys.exit(1 if bad else 0)
