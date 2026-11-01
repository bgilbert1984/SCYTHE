"""Receiver placement optimised against the robust solver with Monte Carlo in the loop.

    OMP_NUM_THREADS=1 .venv/bin/python -m rf_tomography.benchmarks.placement_mc --budget 14

Search: greedy by marginal MC score per cost, then first-improvement swap/add over the
60 best single receivers, run from BOTH the greedy result and the analytic-CRLB layout;
the better (in-sample) result wins. The analytic layout, a random median layout and the
MC layout are then compared on fresh held-out draws (different seed, finer 2 m grid, the
independent run_cell path), under scenarios that include ones unseen in design.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

from ..geometry.scene import synthetic_manhattan_block
from ..measurements.impairments import CORRELATED_PRESETS, PRESETS
from ..measurements.models import EMITTERS, SensingParams
from ..placement.mc_objective import MCObjective
from ..placement.objective import PlacementObjective, make_probes, restricted
from ..placement.optimizer import greedy, optimise, random_layouts, swap_search
from ..placement.sites import all_candidates
from ..propagation.model import PropagationParams
from ..uncertainty.floor_posterior import CandidateGrid
from ..uncertainty.robust_posterior import RobustConfig
from .robust import cell

DESIGN = {"moderate": PRESETS["moderate"], "refl_mild": CORRELATED_PRESETS["refl_mild"]}
HELD_OUT = {"none": PRESETS["none"], "moderate*": PRESETS["moderate"], "refl_mild*": CORRELATED_PRESETS["refl_mild"],
            "severe": PRESETS["severe"], "refl_strong": CORRELATED_PRESETS["refl_strong"],
            "combined": CORRELATED_PRESETS["combined"]}  # * = also a design scenario (fresh draws)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, required=True)
    ap.add_argument("--emitter", default="lora_915M_200k")
    ap.add_argument("--design-trials", type=int, default=24, help="per building per design scenario")
    ap.add_argument("--eval-trials", type=int, default=80, help="per building per held-out cell")
    ap.add_argument("--pool", type=int, default=60)
    ap.add_argument("--swap-rounds", type=int, default=3)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=str(Path(__file__).parent / "results"))
    a = ap.parse_args(argv)
    t0 = time.time()

    scene, prop = synthetic_manhattan_block(), PropagationParams()
    em = next(e for e in EMITTERS if e.name == a.emitter)
    cands = all_candidates(scene)
    solver = RobustConfig()

    # analytic baseline (nominal Gaussian CRLB objective) and a random median layout
    an = PlacementObjective(scene, cands, make_probes(scene), em, SensingParams(), prop)
    analytic = optimise(an, a.budget)
    rnd = random_layouts(an, a.budget, 200, a.seed)
    sc = [an.score(r) for r in rnd]
    random_med = rnd[int(np.argsort(sc)[len(sc) // 2])]

    mc = MCObjective(scene, cands, em, DESIGN, solver, trials_per_building=a.design_trials,
                     seed=101)
    g = greedy(mc, a.budget)
    single = sorted(range(len(cands)), key=lambda i: -mc.score([i]))[:a.pool]
    pool = sorted(set(single) | set(g) | set(analytic))
    from_greedy = swap_search(mc, g, a.budget, a.swap_rounds, pool)
    from_analytic = swap_search(mc, analytic, a.budget, a.swap_rounds, pool)
    best_name, best = max((("greedy+swap", from_greedy), ("analytic+swap", from_analytic)),
                          key=lambda t: mc.score(t[1]))

    layouts = {"mc_optimised": best, "analytic_optimised": analytic, "random_median": random_med}
    grids = {b.name: CandidateGrid.for_building(b) for b in scene.buildings}
    results = []
    for lname, idx in layouts.items():
        ins = mc.evaluate(idx)
        row = dict(layout=lname, cost=mc.cost(idx), receivers=[cands[i].label for i in idx],
                   design_score=mc.score(idx),
                   design={k: v for k, v in ins.items() if k != "_score"}, held_out={})
        for sname, imp in HELD_OUT.items():
            r = cell(scene, [cands[i].receiver() for i in idx], em, imp, solver, prop, grids,
                     a.eval_trials, a.seed + 1000)
            row["held_out"][sname] = {k: r[k] for k in ("top1", "top3", "cov90", "mean_set90",
                                                         "mean_p_truth", "mean_floor_error")}
        results.append(row)

    meta = dict(budget=a.budget, emitter=em.name, design_scenarios=list(DESIGN),
                design_trials_per_building=a.design_trials, eval_trials_per_building=a.eval_trials,
                pool=a.pool, swap_rounds=a.swap_rounds, mc_evaluations=len(mc._memo),
                winner=best_name, seconds=round(time.time() - t0),
                greedy=[cands[i].label for i in g],
                note="SYNTHETIC; design in-sample, held-out on fresh draws; * = design scenario")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tag = f"placement_mc_B{int(a.budget)}"
    (out / f"{tag}.json").write_text(json.dumps(dict(meta=meta, results=results), indent=2))
    md = [f"### Budget {a.budget:g} (search winner: {best_name}, {meta['mc_evaluations']} MC layout evaluations, "
          f"{meta['seconds']} s)\n",
          "| layout | scenario | top-1 | top-3 | cov90 | 90% set | p(truth) | floor bias |",
          "|---|---|---|---|---|---|---|---|"]
    for r in results:
        for sname, v in r["held_out"].items():
            md.append(f"| {r['layout']} | {sname} | {v['top1']:.2f} | {v['top3']:.2f} | {v['cov90']:.2f} | "
                      f"{v['mean_set90']:.1f} | {v['mean_p_truth']:.2f} | {v['mean_floor_error']:+.2f} |")
    md.append("")
    for r in results:
        md.append(f"- {r['layout']} (cost {r['cost']:.0f}, design score {r['design_score']:.3f}): "
                  + ", ".join(r["receivers"]))
    (out / f"{tag}.md").write_text("\n".join(md))
    print("\n".join(md))


if __name__ == "__main__":
    main()
