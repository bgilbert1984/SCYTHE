#!/usr/bin/env python3
"""Relation-by-fixture and mutant-by-relation views of a metamorphic r3 results_full directory (read-only).
Usage: analyse_r3_results.py RESULTS_FULL_DIR
Shows applicability and numerical deviation per relation (so a clean sweep can be told from a vacuous one),
the informational roll, and the complete mutant-by-relation matrix.
"""
import collections, glob, json, os, sys
rd = sys.argv[1]
A = [json.load(open(f)) for f in sorted(glob.glob(os.path.join(rd, "phaseA", "*.json")))]
B = [json.load(open(f)) for f in sorted(glob.glob(os.path.join(rd, "phaseB", "*.json")))]
print("fixtures", len(A), "| candidates per fixture:", dict(collections.Counter(a["candidates"] for a in A)),
      "| total candidates", sum(a["candidates"] for a in A))
agg = collections.defaultdict(lambda: dict(v=collections.Counter(), f=0.0, e=0.0, na=0, ent=0, nb=0, st=0, bd=0, lost=0, head=set()))
for a in A:
    for k, d in a["relations"].items():
        g = agg[k]; g["v"][d["verdict"]] += 1
        g["f"] = max(g["f"], d.get("max_freq_dev_hz", 0) or 0, d.get("max_freq_dev_bins", 0) or 0)
        g["e"] = max(g["e"], d.get("max_excess_dev_db", 0) or 0)
        g["na"] += d.get("not_applicable", 0); g["ent"] += d.get("unobservable_entrants", 0); g["nb"] += d.get("n_base", 0)
        g["st"] += d.get("strong", 0); g["bd"] += d.get("near_boundary_skipped", 0); g["lost"] += len(d.get("lost", []))
        if "candidates_with_headroom" in d: g["head"].add(d["candidates_with_headroom"])
print("\nrelation                           verdicts                          max_freq_dev  max_excess_dev_dB  n_base n/a entrants strong near-margin")
for k, g in agg.items():
    print(f"{k:34s} {dict(g['v'])!s:34s} {g['f']:11.2e} {g['e']:17.2e} {g['nb']:6d} {g['na']:3d} {g['ent']:8d} {g['st']:6d} {g['bd']:6d}")
ps = collections.defaultdict(collections.Counter)
for a in A:
    for c in a.get("persistence", {}).values():
        for k, d in c.items(): ps[k][d["verdict"]] += 1
print("\npersistence relations (per candidate):"); [print(f"  {k:52s} {dict(v)}") for k, v in ps.items()]
roll = [a["ROLL"]["max_freq_dev_bins"] for a in A]
print("\nROLL (informational only): max freq deviation in bins min %.4f max %.4f; candidate-count changes: %d" %
      (min(roll), max(roll), sum(a["ROLL"]["n_base"] != a["ROLL"]["n_other"] for a in A)))
print("\nphase B: %d records; statuses %s" % (len(B), dict(collections.Counter(b["status"] for b in B))))
cands = collections.defaultdict(list); M = collections.defaultdict(lambda: collections.defaultdict(collections.Counter)); rels = []
for b in B:
    cands[b["mutant"]].append(b["mutant_candidates"])
    for k, d in b["relations"].items():
        if k not in rels: rels.append(k)
        M[b["mutant"]][k][d["credit"]] += 1
print("mutant candidates per fixture (min..max):", {m: (min(v), max(v)) for m, v in cands.items()})
print("\nmutant x relation  (D=MUTANT_DETECTED  S=MUTANT_SURVIVED  -=NOT_COMPARABLE; counts over the mutant fixtures)")
print(f"{'relation':34s}" + "".join(f"{m[:18]:>20s}" for m in M))
for k in rels:
    print(f"{k:34s}" + "".join(f"{'D%d S%d -%d' % (M[m][k]['MUTANT_DETECTED'], M[m][k]['MUTANT_SURVIVED'], M[m][k]['NOT_COMPARABLE']):>20s}" for m in M))
