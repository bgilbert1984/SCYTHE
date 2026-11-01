"""Does a robust likelihood + NLOS rejection restore calibration under impairments?

    .venv/bin/python -m rf_tomography.benchmarks.robust

Every solver sees the *same* simulated observations (inference consumes no RNG), so
solver differences are paired. Success criterion from the design doc: 90% credible
floor sets contain the truth ~90% of the time without giving up accuracy on clean data.
Also reports NLOS-rejection recall / false-flag rate and sensitivity to wrong
solver hyperparameters (the solver's beliefs are NOT tuned to the truth presets).
"""
from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

import numpy as np

from ..geometry.scene import synthetic_manhattan_block
from ..measurements.impairments import PRESETS
from ..measurements.models import EMITTERS, SensingParams
from ..placement.objective import PlacementObjective, make_probes, restricted
from ..placement.optimizer import optimise, random_layouts
from ..placement.sites import all_candidates
from ..propagation.model import PropagationParams
from ..uncertainty.floor_posterior import CandidateGrid
from ..uncertainty.robust_posterior import RobustConfig
from .identifiability import aggregate, run_cell
from .placement import hand_layouts

SOLVERS = {
    "gaussian": None,
    "nlos_only": RobustConfig("nlos_only", col_model="gaussian"),
    "student": RobustConfig("student", col_model="student", pi_nlos=0.0),
    "mixture": RobustConfig("mixture", col_model="mixture", pi_nlos=0.0),
    "student+nlos": RobustConfig("student+nlos", col_model="student"),
    "mixture+nlos": RobustConfig("mixture+nlos", col_model="mixture"),
}
# Deliberately wrong beliefs, run on the moderate preset (truth: eps~0.10, ~10% false-LOS).
MISSET = {
    "default": RobustConfig("default"),
    "too_trusting(eps=.02,pi=.02)": RobustConfig("trusting", eps=0.02, pi_nlos=0.02),
    "too_paranoid(eps=.40,pi=.40)": RobustConfig("paranoid", eps=0.40, pi_nlos=0.40),
    "outlier_scale_x3": RobustConfig("scale3", sigma_out_angle_deg=45.0, sigma_out_toa_m=90.0,
                                     sigma_nlos_angle_deg=90.0, sigma_nlos_toa_m=180.0),
}


def cell(scene, rx, em, imp, solver, prop, grids, trials, seed):
    rng = np.random.default_rng(seed)
    rows = []
    for b in scene.buildings:
        rows += run_cell(scene, b, rx, em, SensingParams(), prop, trials, rng,
                         grids[b.name], imp, solver)
    a = aggregate(rows, float(np.mean([b.n_floors for b in scene.buildings])))
    a["overconfidence"] = a["mean_confidence"] - a["top1"]
    return a


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--emitter", default="lora_915M_200k")
    ap.add_argument("--trials", type=int, default=40, help="per building per cell")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=str(Path(__file__).parent / "results"))
    a = ap.parse_args(argv)

    scene, prop = synthetic_manhattan_block(), PropagationParams()
    em = next(e for e in EMITTERS if e.name == a.emitter)
    grids = {b.name: CandidateGrid.for_building(b) for b in scene.buildings}
    base, hand = all_candidates(scene), hand_layouts(scene)
    cands = base + [c for v in hand.values() for c in v]
    obj = PlacementObjective(scene, cands, make_probes(scene), em, SensingParams(), prop)
    view = restricted(obj, len(base))
    layouts = {"hand:street_array+roofs": [c.receiver() for c in hand["hand:street_array+roofs"]],
               "optimised@20": [cands[i].receiver() for i in optimise(view, 20)],
               "optimised@14": [cands[i].receiver() for i in optimise(view, 14)]}
    rnd = random_layouts(view, 14, 200, a.seed)
    med = rnd[int(np.argsort([obj.score(r) for r in rnd])[len(rnd) // 2])]
    layouts["random_median@14"] = [cands[i].receiver() for i in med]

    main_rows = []
    for lname, rx in layouts.items():
        for pname, imp in PRESETS.items():
            for sname, solver in SOLVERS.items():
                main_rows.append(dict(layout=lname, preset=pname, solver=sname,
                                      **cell(scene, rx, em, imp, solver, prop, grids, a.trials, a.seed)))
    mis_rows = []
    for lname in ("hand:street_array+roofs", "optimised@20"):
        for mname, solver in MISSET.items():
            for pname in ("none", "moderate", "severe"):
                mis_rows.append(dict(layout=lname, preset=pname, beliefs=mname,
                                     **cell(scene, layouts[lname], em, PRESETS[pname], solver,
                                            prop, grids, a.trials, a.seed)))

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    meta = dict(emitter=em.name, seed=a.seed, trials_per_building=a.trials,
                solvers={k: (v.__dict__ if v else "nominal gaussian") for k, v in SOLVERS.items()},
                note="SYNTHETIC; paired draws; solver beliefs fixed, not tuned to truth presets")
    (out / "robust.json").write_text(json.dumps(dict(meta=meta, main=main_rows, sensitivity=mis_rows), indent=2))

    f = lambda v: "-" if v is None else f"{v:.2f}"
    md = ["### Solvers x presets\n",
          "| layout | preset | solver | top-1 | top-3 | cov90 | 90% set | overconf | NLOS recall | false flag |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for r in main_rows:
        md.append(f"| {r['layout']} | {r['preset']} | {r['solver']} | {r['top1']:.2f} | {r['top3']:.2f} | "
                  f"{r['cov90']:.2f} | {r['mean_set90']:.1f} | {r['overconfidence']:+.2f} | "
                  f"{f(r['nlos_recall'])} | {f(r['nlos_false_flag_rate'])} |")
    md += ["\n### Wrong solver beliefs (mixture+nlos)\n",
           "| layout | preset | beliefs | top-1 | top-3 | cov90 | 90% set | overconf |", "|---|---|---|---|---|---|---|---|"]
    for r in mis_rows:
        md.append(f"| {r['layout']} | {r['preset']} | {r['beliefs']} | {r['top1']:.2f} | {r['top3']:.2f} | "
                  f"{r['cov90']:.2f} | {r['mean_set90']:.1f} | {r['overconfidence']:+.2f} |")
    (out / "robust.md").write_text("\n".join(md))
    print("\n".join(md))


if __name__ == "__main__":
    main()
