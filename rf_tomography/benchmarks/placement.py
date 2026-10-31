"""Receiver-placement optimiser benchmark + Monte-Carlo validation.

    .venv/bin/python -m rf_tomography.benchmarks.placement

Optimises a layout under a cost budget using the fast analytic objective, then
checks every layout (optimised, random, hand-built) against the full grid-posterior
Monte Carlo from identifiability.py. The analytic score is only worth using if it
ranks layouts the way Monte Carlo does; the Spearman correlation is reported.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

import numpy as np

from ..geometry.scene import synthetic_manhattan_block
from ..measurements.models import EMITTERS, SensingParams
from ..placement.objective import PlacementObjective, make_probes
from ..placement.optimizer import optimise, random_layouts
from ..placement.sites import ACCESS_COST, Candidate, Site, all_candidates
from ..propagation.model import PropagationParams
from ..uncertainty.floor_posterior import CandidateGrid
from .identifiability import aggregate, run_cell


def spearman(a, b):
    ra, rb = np.argsort(np.argsort(a)), np.argsort(np.argsort(b))
    return float(np.corrcoef(ra, rb)[0, 1])


def mc(scene, receivers, em, sensing, prop, grids, trials, seed):
    rng = np.random.default_rng(seed)
    rows = []
    for b in scene.buildings:
        rows += run_cell(scene, b, receivers, em, sensing, prop, trials, rng, grids[b.name])
    return aggregate(rows, float(np.mean([b.n_floors for b in scene.buildings])))


def hand_layouts(scene):
    def street(tier):
        return [Candidate(Site(f"S{x},{y}", (float(x), float(y), 1.5), "street"), tier)
                for x, y in [(0, 0), (80, 0), (-80, 0), (0, 80), (0, -80)]]
    roofs = [Candidate(Site("RB", (22.0, -40.0, scene.by_name("B").height + 1), "roof", "B"), "array"),
             Candidate(Site("RD", (22.0, 40.0, scene.by_name("D").height + 1), "roof", "D"), "array")]
    return {"hand:street_tdoa": street("tdoa"), "hand:street_array": street("array"),
            "hand:street_array+roofs": street("array") + roofs}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--budgets", type=float, nargs="+", default=[6, 10, 14, 20])
    ap.add_argument("--emitter", default="lora_915M_200k")
    ap.add_argument("--trials", type=int, default=30, help="MC trials per building per layout")
    ap.add_argument("--random-layouts", type=int, default=200)
    ap.add_argument("--mismatch-range-m", type=float, default=3.0)
    ap.add_argument("--mismatch-angle-deg", type=float, default=3.0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=str(Path(__file__).parent / "results"))
    a = ap.parse_args(argv)

    scene, prop = synthetic_manhattan_block(), PropagationParams()
    em = next(e for e in EMITTERS if e.name == a.emitter)
    nominal = SensingParams()
    mism = replace(nominal, unmodelled_range_bias_m=a.mismatch_range_m,
                   unmodelled_angle_bias_deg=a.mismatch_angle_deg)
    hand = hand_layouts(scene)
    base = all_candidates(scene)
    extra = [c for v in hand.values() for c in v]
    cands = base + extra
    probes = make_probes(scene)
    grids = {b.name: CandidateGrid.for_building(b) for b in scene.buildings}
    # Candidate-index set the optimiser may use (hand-built extras are scored, not searched).
    nbase = len(base)
    obj = PlacementObjective(scene, cands, probes, em, nominal, prop)
    robj = PlacementObjective(scene, cands, probes, em, mism, prop, robust=True)

    def search(o, budget):
        # restrict search to base candidates by temporarily slicing
        v = object.__new__(PlacementObjective)
        v.__dict__.update(o.__dict__)
        v.cands = o.cands[:nbase]
        for k in ("A", "b", "S"):
            setattr(v, k, {kk: vv[:nbase] for kk, vv in getattr(o, k).items()})
        v.los = o.los[:nbase]
        return optimise(v, budget)

    def describe(idx):
        return [cands[i].label for i in idx]

    entries = []

    def evaluate(kind, o, idx, budget, trials):
        rx = [cands[i].receiver() for i in idx]
        nom = mc(scene, rx, em, nominal, prop, grids, trials, a.seed)
        mis = mc(scene, rx, em, mism, prop, grids, trials, a.seed)
        entries.append(dict(kind=kind, budget=budget, cost=obj.cost(idx), receivers=describe(idx),
                            analytic_nominal=obj.score(idx), analytic_robust=robj.score(idx),
                            mc_nominal=nom, mc_mismatch=mis))

    for B in a.budgets:
        evaluate("opt_nominal", obj, search(obj, B), B, a.trials)
        evaluate("opt_robust", robj, search(robj, B), B, a.trials)
        rl = random_layouts(_View(obj, nbase), B, a.random_layouts, a.seed)
        sc = [obj.score(r) for r in rl]
        evaluate("random_median", obj, rl[int(np.argsort(sc)[len(sc) // 2])], B, a.trials)
        evaluate("random_best_of_%d" % a.random_layouts, obj, rl[int(np.argmax(sc))], B, a.trials)
    for name, v in hand.items():
        idx = [cands.index(c) for c in v]
        evaluate(name, obj, idx, obj.cost(idx), a.trials)

    rho_nom = spearman([e["analytic_nominal"] for e in entries],
                       [e["mc_nominal"]["top1"] for e in entries])
    rho_mis = spearman([e["analytic_robust"] for e in entries],
                       [e["mc_mismatch"]["top1"] for e in entries])

    # Where does the mid-budget optimum still fail? per building x height third.
    mid = next(e for e in entries if e["kind"] == "opt_nominal" and e["budget"] == sorted(a.budgets)[len(a.budgets) // 2])
    midx = [next(i for i, c in enumerate(cands) if c.label == l) for l in mid["receivers"]]
    pp = obj.per_probe_score(midx)
    breakdown = {}
    for b in scene.buildings:
        for band, (lo, hi) in {"low": (0, .33), "mid": (.33, .66), "high": (.66, 1.01)}.items():
            m = [pp[i] for i, p in enumerate(probes)
                 if p.building == b.name and lo <= (p.floor - 1) / (b.n_floors - 1) < hi]
            breakdown[f"{b.name}:{band}"] = float(np.mean(m)) if m else None

    meta = dict(emitter=em.name, seed=a.seed, trials_per_building=a.trials,
                probes=len(probes), candidates=nbase,
                tier_costs={"rss_az": 1, "tdoa": 2, "array": 4}, access_costs=ACCESS_COST,
                spearman_analytic_vs_mc_top1_nominal=rho_nom,
                spearman_robust_analytic_vs_mc_top1_mismatch=rho_mis,
                note="SYNTHETIC; costs are assumptions; analytic objective is a local approximation")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "placement.json").write_text(json.dumps(dict(meta=meta, entries=entries,
                                                         breakdown_mid_budget=breakdown), indent=2))
    md = ["| kind | budget | n rx | analytic | MC top-1 | MC top-3 | MC top-1 (mismatch) | MC top-3 (mismatch) | cov90 (mismatch) |",
          "|---|---|---|---|---|---|---|---|---|"]
    for e in entries:
        md.append(f"| {e['kind']} | {e['cost']:.0f} | {len(e['receivers'])} | {e['analytic_nominal']:.2f} | "
                  f"{e['mc_nominal']['top1']:.2f} | {e['mc_nominal']['top3']:.2f} | "
                  f"{e['mc_mismatch']['top1']:.2f} | {e['mc_mismatch']['top3']:.2f} | "
                  f"{e['mc_mismatch']['cov90']:.2f} |")
    md.append(f"\nSpearman(analytic, MC top-1): nominal {rho_nom:.2f}; robust-vs-mismatch {rho_mis:.2f}\n")
    md.append("Chosen layouts:\n")
    for e in entries:
        if e["kind"].startswith("opt"):
            md.append(f"- {e['kind']} @{e['budget']:.0f}: {', '.join(e['receivers'])}")
    md.append(f"\nPer-region analytic score, optimum at budget {mid['budget']:.0f}: "
              + ", ".join(f"{k}={v:.2f}" for k, v in breakdown.items() if v is not None))
    (out / "placement.md").write_text("\n".join(md))
    print("\n".join(md))


class _View:
    """Read-only view exposing the first n candidates for random_layouts."""
    def __init__(self, o, n):
        self.cands = o.cands[:n]


if __name__ == "__main__":
    main()
