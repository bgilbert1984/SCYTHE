"""Paired fresh-draw comparison of the layouts found by placement_mc.py.

    OMP_NUM_THREADS=1 .venv/bin/python -m rf_tomography.benchmarks.placement_mc_paired

Reads results/placement_mc_B*.json, builds a NEW observation bank (different seed, 100
trials per building per scenario, six scenarios incl. ones unseen in design) and reports,
per scenario, the mean probability assigned to the true floor for each layout and the
paired difference MC-optimised minus analytic-optimised (and minus random) with its
standard error. The design search used a different seed, so this is out-of-sample for
the search, though it shares the generator, the 3 m grid and the solver.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from ..geometry.scene import synthetic_manhattan_block
from ..measurements.impairments import CORRELATED_PRESETS, PRESETS
from ..measurements.models import EMITTERS
from ..placement.mc_objective import MCObjective
from ..placement.sites import all_candidates
from ..uncertainty.robust_posterior import RobustConfig, robust_floor_posterior

SCENARIOS = {"none": PRESETS["none"], "moderate": PRESETS["moderate"],
             "refl_mild": CORRELATED_PRESETS["refl_mild"], "severe": PRESETS["severe"],
             "refl_strong": CORRELATED_PRESETS["refl_strong"],
             "combined": CORRELATED_PRESETS["combined"]}


def per_trial_p(o: MCObjective, idx):
    ch = np.array(sorted(idx))
    out = {}
    for sn, trials in o.bank.items():
        v = []
        for t in trials:
            sel = np.where(np.isin(t["col_rx"], ch))[0]
            cols = [t["cols"][j] for j in sel]
            H = np.stack([o.H[t["b"]][c] for c in cols], 1)
            p, _ = robust_floor_posterior(t["y"][sel], o.grids[t["b"]], o.receivers, cols,
                                          t["cov"][np.ix_(sel, sel)], o.prop, o.solver, H)
            v.append(p[t["k"]])
        out[sn] = np.array(v)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=100)
    ap.add_argument("--seed", type=int, default=909)
    ap.add_argument("--emitter", default="lora_915M_200k")
    ap.add_argument("--dir", default=str(Path(__file__).parent / "results"))
    a = ap.parse_args(argv)
    scene = synthetic_manhattan_block()
    cands = all_candidates(scene)
    label = {c.label: i for i, c in enumerate(cands)}
    em = next(e for e in EMITTERS if e.name == a.emitter)
    o = MCObjective(scene, cands, em, SCENARIOS, RobustConfig(), trials_per_building=a.trials,
                    seed=a.seed)
    md, js = [], {}
    for f in sorted(Path(a.dir).glob("placement_mc_B*.json")):
        if f.name.endswith("_paired.json"):
            continue
        B = json.loads(f.read_text())["meta"]["budget"]
        res = {r["layout"]: [label[l] for l in r["receivers"]] for r in json.loads(f.read_text())["results"]}
        pt = {k: per_trial_p(o, v) for k, v in res.items()}
        md += [f"### Budget {B:g}: mean p(true floor), {4 * a.trials} fresh trials per scenario\n",
               "| scenario | mc_optimised | analytic | random | MC - analytic (SE) | MC - random (SE) |",
               "|---|---|---|---|---|---|"]
        js[str(B)] = {}
        for sn in SCENARIOS:
            d = pt["mc_optimised"][sn] - pt["analytic_optimised"][sn]
            r = pt["mc_optimised"][sn] - pt["random_median"][sn]
            se = lambda x: x.std(ddof=1) / np.sqrt(len(x))
            md.append(f"| {sn} | {pt['mc_optimised'][sn].mean():.3f} | {pt['analytic_optimised'][sn].mean():.3f} | "
                      f"{pt['random_median'][sn].mean():.3f} | {d.mean():+.3f} ({se(d):.3f}) | {r.mean():+.3f} ({se(r):.3f}) |")
            js[str(B)][sn] = dict(mc=float(pt["mc_optimised"][sn].mean()),
                                  analytic=float(pt["analytic_optimised"][sn].mean()),
                                  random=float(pt["random_median"][sn].mean()),
                                  diff_vs_analytic=float(d.mean()), se_vs_analytic=float(se(d)),
                                  diff_vs_random=float(r.mean()), se_vs_random=float(se(r)))
        md.append("")
    Path(a.dir, "placement_mc_paired.json").write_text(json.dumps(js, indent=2))
    Path(a.dir, "placement_mc_paired.md").write_text("\n".join(md))
    print("\n".join(md))


if __name__ == "__main__":
    main()
