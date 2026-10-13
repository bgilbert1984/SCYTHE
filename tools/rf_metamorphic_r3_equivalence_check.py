#!/usr/bin/env python3
"""r2-versus-r3 equivalence of the one-repeat worst-case check on the real pinned fixtures.

For every fixture: production strong candidates -> r2 reference (floor recomputed per call) versus r3 batch
(floor once).  For the first K fixtures also a BOUNDED subset of the absolute_floor mutant's candidates
(every k-th by bin, 300 in total): the full mutant would take the r2 reference hours.
Passes iff every value agrees within 1e-9 dB and every headroom decision is identical.

    python3 tools/rf_metamorphic_r3_equivalence_check.py --holdout DIR --hashes FILE --out result.json
"""
import argparse, json, os, sys, time
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rf_metamorphic_harness as H

ap = argparse.ArgumentParser()
ap.add_argument("--holdout", required=True); ap.add_argument("--hashes", required=True)
ap.add_argument("--out"); ap.add_argument("--mutant-fixtures", type=int, default=2)
ap.add_argument("--subset", type=int, default=300)
a = ap.parse_args()
names = H.load_fixtures(a.holdout, a.hashes)
mut = H.build_variant(H.MUTATIONS["absolute_floor"])
rows, worst_diff, bad = [], 0.0, []
t0 = time.time()
for i, name in enumerate(names):
    w = H.load_windows(a.holdout, name)
    n = len(w[0])
    hann = 0.5 * (1.0 - np.cos(2.0 * np.pi * np.arange(n) / (n - 1)))
    sp = np.abs(np.fft.fft(np.stack(w) * hann[None, :], axis=1)) ** 2
    sets = {"production": [b for b in H.prod(w) if b[1] >= H.STRONG_DB]}
    if i < a.mutant_fixtures:
        mb = sorted(H.as_tuples(mut(w)), key=lambda c: c[2])
        step = max(1, len(mb) // a.subset)
        sets["absolute_floor_subset"] = [b for b in mb[::step][:a.subset] if b[1] >= H.STRONG_DB]
        sets["absolute_floor_total_candidates"] = len(mb)
    for label, strong in sets.items():
        if label.endswith("total_candidates"):
            continue
        bins = [b[2] for b in strong]
        ref = [H.worst_case_excess_after_loss(sp, b) for b in bins]
        new = H.worst_case_excess_batch(sp, bins)
        d = max((abs(x - y) for x, y in zip(ref, new)), default=0.0)
        dec_ref = [e - H.PLAN_PERSISTENCE_MARGIN_DB >= H.ONE_REPEAT_HEADROOM_DB for e in ref]
        dec_new = [e - H.PLAN_PERSISTENCE_MARGIN_DB >= H.ONE_REPEAT_HEADROOM_DB for e in new]
        worst_diff = max(worst_diff, d)
        if dec_ref != dec_new or d > 1e-9:
            bad.append((name, label))
        rows.append({"fixture": name, "set": label, "bins": len(bins), "max_abs_diff_db": d,
                     "decisions_identical": dec_ref == dec_new, "tolerant": sum(dec_new)})
    print(f"{name}: " + ", ".join(f"{r['set']}={r['bins']} diff={r['max_abs_diff_db']:.2e}" for r in rows[-2:] if r['fixture'] == name), flush=True)
res = {"verdict": "EQUIVALENT" if not bad else "NOT_EQUIVALENT", "max_abs_diff_db": worst_diff,
       "tolerance_db": 1e-9, "failures": bad, "fixtures": len(names), "rows": rows,
       "elapsed_s": time.time() - t0}
if a.out:
    with open(a.out, "w") as fh:
        json.dump(res, fh, indent=1)
print("EQUIVALENCE:", res["verdict"], "max |diff| dB =", worst_diff)
sys.exit(0 if not bad else 1)
