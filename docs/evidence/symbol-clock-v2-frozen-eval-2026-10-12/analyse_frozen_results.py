#!/usr/bin/env python3
"""Per-cell views of a frozen-evaluation result file (read-only; no thresholds touched).

Usage: analyse_frozen_results.py frozen_full_attempt2.json
Prints: completeness, refusals by cell, G1-G4 cells, sensitivity coverage by noise level,
the v1-vs-v2 clock statistics, and where the largest v2 noise statistics sit relative to the
lower search edge.
"""
import collections, json, sys
d = json.load(open(sys.argv[1])); ev = d["evaluation"]; T = d["trials"]
print("trials", len(T), "status", ev["counts"], "errors", sum(t["status"] == "ERROR" for t in T))
print("protocol", d["protocol_sha256"]); print("runner", d["runner_sha256"]); print("numpy", d["numpy"])
print("\nnon-analysed by cell:")
c = collections.Counter()
for t in T:
    if t["status"] != "ANALYSED":
        c[(t["kind"], t.get("family") or "clock", int(t.get("width_hz") or t.get("rate_hz") or 0), t["status"])] += 1
for k, v in sorted(c.items()): print(" ", k, v)
print("\nG1:", ev["G1_pass"]);  [print(" ", k, v) for k, v in ev["G1"].items()]
print("\nG2:", ev["G2_pass"]);  [print(" ", k, v) for k, v in ev["G2"].items()]
print("\nG3:", ev["G3_pass"]);  [print(" ", k, v) for k, v in ev["G3"].items()]
print("\nG4 blocking:", ev["G4_blocking_finding"]); [print(" ", k, v) for k, v in ev["G4"].items()]
sens = collections.defaultdict(lambda: [0, 0])
for t in T:
    if t["kind"] == "sensitivity": sens[(t["sigma"], t["beta"])][t["status"] != "ANALYSED"] += 1
print("\nsensitivity coverage (analysed, refused) by (noise_sigma, rolloff):"); [print(" ", k, v) for k, v in sorted(sens.items())]
rows = []
for t in T:
    if t["status"] == "ANALYSED" and t["kind"] in ("adverse", "novel") and t["v2"][1] is not None:
        res, f = t["v2"][3], t["v2"][2]
        rows.append((t["v2"][1], t["kind"], t["family"], int(t["width_hz"]), (f - 96 * res) / res))
rows.sort(reverse=True)
print("\ntop v2 noise statistics (stat, kind, family, width, offset from lower search edge in bins):")
for r in rows[:8]: print("  %.3f %s %s@%d offset=%.1f" % r)
big = [r for r in rows if r[0] >= 2.0]
print("noise trials with v2>=2.0:", len(big), " within 5 bins of lower edge:", sum(r[4] <= 5 for r in big))
