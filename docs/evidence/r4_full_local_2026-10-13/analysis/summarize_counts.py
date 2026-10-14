#!/usr/bin/env python3
"""Recompute the exact per-layer, per-mutant, per-relation counts of the sealed r4 full run from its stored records (stdlib only).

    summarize_counts.py RESULTS_FULL_DIR      -> prints the COUNTS table (deterministic; sorted keys)
"""
import collections, glob, json, os, sys

rd = sys.argv[1]
load = lambda p: json.load(open(p))
out = []
def table(title, counter, indent="  "):
    out.append(title)
    for k in sorted(counter): out.append(f"{indent}{k}: {counter[k]}")

ident = load(os.path.join(rd, "identity.json")); out.append(f"identity verdict: {ident['verdict']} ({len(ident['per_fixture'])} fixtures)")
la = load(os.path.join(rd, "layerA.json"))
out.append("LAYER A (grid and predicates; detector involved: %s)" % la["detector_involved"])
for n, g in sorted(la["geometries"].items()): out.append(f"  n={n}: computed-exactly-on-boundary bins {g['computed_exactly_on_boundary']}, ideal-on-boundary bins {g['ideal_on_boundary_bins']}")

lp = load(os.path.join(rd, "layerBprime.json")); c = collections.Counter(); byn = collections.Counter(); pv = 0
for case in lp["cases"]:
    pv += case["judgments"]["production"]["verdict"] != "RELATION_SATISFIED"
    for m, j in case["judgments"].items():
        if m != "production": c[f"{m} | {j['credit']}"] += 1; byn[f"n={case['n']} | {m} | {j['credit']}"] += 1
out.append(f"LAYER B' (function-level; production_path={lp['production_path']}; {len(lp['cases'])} cases; production non-satisfied: {pv})")
table("  by mutant and credit:", c, "    "); table("  by length, mutant and credit:", byn, "    ")

c = collections.Counter(); fam = collections.Counter(); pre = collections.Counter(); ev = pv = nfix = 0; kinds = collections.Counter()
for f in sorted(glob.glob(os.path.join(rd, "layerB", "*.json"))):
    r = load(f); nfix += 1; assert r["production_path"] is False
    for case in r["cases"]:
        if case["status"] != "EVALUATED":
            pre[f"{os.path.basename(f)[:2]} | {case['status']} | {case.get('reason')}"] += 1; continue
        ev += 1; pv += case["judgments"]["production"]["verdict"] != "RELATION_SATISFIED"
        for m, j in case["judgments"].items():
            if m != "production": c[f"{m} | {j['credit']}"] += 1; fam[f"{os.path.basename(f)[:2]} | {m} | {j['credit']}"] += 1
out.append(f"LAYER B (function-level, production length; production_path=False; {nfix} fixtures; {ev} cases evaluated; production non-satisfied: {pv})")
table("  by mutant and credit:", c, "    "); table("  by family, mutant and credit:", fam, "    "); table("  not evaluated:", pre, "    ")

rel = collections.Counter(); amb = 0; pers = collections.Counter(); nA = 0
for f in sorted(glob.glob(os.path.join(rd, "phaseA", "*.json"))):
    a = load(f); nA += 1
    for k, v in a["relations"].items(): rel[f"{k} | {v[0] if isinstance(v, list) else v['verdict']}"] += 1
    for cand, rels in (a.get("persistence") or {}).items():
        for rk, rv in rels.items(): pers[f"{rk} | {rv['verdict']}"] += 1
out.append(f"PHASE A (production relations; {nA} fixtures)"); table("  detector relations by verdict:", rel, "    "); table("  persistence relations by verdict (one per candidate):", pers, "    ")
b = collections.Counter(); st = collections.Counter(); per = collections.Counter()
for f in sorted(glob.glob(os.path.join(rd, "phaseB", "*.json"))):
    e = load(f); st[e["status"]] += 1
    for k, v in (e.get("relations") or {}).items(): per[f"{e['mutant']} | {k} | production={v['production_verdict']} mutant={v['mutant_verdict']} credit={v['credit']}"] += 1
out.append("PHASE B (mutants on the first fixtures)"); table("  record status:", st, "    "); table("  mutant | relation | outcome:", per, "    ")
print("\n".join(out))
