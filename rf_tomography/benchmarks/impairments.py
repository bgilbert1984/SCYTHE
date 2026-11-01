"""Robustness of floor inference to NLOS / non-Gaussian multipath / geometry error.

    .venv/bin/python -m rf_tomography.benchmarks.impairments

The solver is the nominal Gaussian grid posterior from identifiability.py and is
deliberately unaware of the impairments. Reported per layout x preset: accuracy,
90% credible-set coverage (should be ~0.90), and mean top-1 confidence minus top-1
accuracy (positive = overconfident). A second table switches on one mechanism at a
time to show which one does the damage. Severity values are assumptions.
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
from .identifiability import aggregate, run_cell
from .placement import hand_layouts

ABLATIONS = {
    "outliers_only": lambda m: replace(PRESETS["none"], outlier_prob=m.outlier_prob,
                                       toa_excess_mean_m=m.toa_excess_mean_m,
                                       angle_outlier_deg=m.angle_outlier_deg),
    "false_los_only": lambda m: replace(PRESETS["none"], false_los_prob=m.false_los_prob,
                                        nlos_toa_excess_mean_m=m.nlos_toa_excess_mean_m,
                                        nlos_angle_sigma_deg=m.nlos_angle_sigma_deg),
    "false_nlos_only": lambda m: replace(PRESETS["none"], false_nlos_prob=m.false_nlos_prob),
    "geometry_only": lambda m: replace(PRESETS["none"], geometry_sigma_m=m.geometry_sigma_m),
}


def cell(scene, rx, em, imp, prop, grids, trials, seed):
    rng = np.random.default_rng(seed)
    rows = []
    for b in scene.buildings:
        rows += run_cell(scene, b, rx, em, SensingParams(), prop, trials, rng, grids[b.name], imp)
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
    base = all_candidates(scene)
    hand = hand_layouts(scene)
    cands = base + [c for v in hand.values() for c in v]
    obj = PlacementObjective(scene, cands, make_probes(scene), em, SensingParams(), prop)
    view = restricted(obj, len(base))
    layouts = {
        "hand:street_array": [c.receiver() for c in hand["hand:street_array"]],
        "hand:street_array+roofs": [c.receiver() for c in hand["hand:street_array+roofs"]],
    }
    for B in (14, 20):
        layouts[f"optimised@{B}"] = [cands[i].receiver() for i in optimise(view, B)]
    rnd = random_layouts(view, 14, 200, a.seed)
    med = rnd[int(np.argsort([obj.score(r) for r in rnd])[len(rnd) // 2])]
    layouts["random_median@14"] = [cands[i].receiver() for i in med]

    main_rows = []
    for lname, rx in layouts.items():
        for pname, imp in PRESETS.items():
            main_rows.append(dict(layout=lname, preset=pname,
                                  **cell(scene, rx, em, imp, prop, grids, a.trials, a.seed)))
    abl_rows = []
    mod = PRESETS["moderate"]
    for lname in ("hand:street_array+roofs", "optimised@20"):
        for aname, mk in ABLATIONS.items():
            abl_rows.append(dict(layout=lname, ablation=aname,
                                 **cell(scene, layouts[lname], em, mk(mod), prop, grids,
                                        a.trials, a.seed)))

    meta = dict(emitter=em.name, seed=a.seed, trials_per_building=a.trials,
                presets={k: v.__dict__ for k, v in PRESETS.items()},
                note="SYNTHETIC; impairment severities are assumptions; solver is unaware of them")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "impairments.json").write_text(json.dumps(dict(meta=meta, main=main_rows,
                                                           ablations=abl_rows), indent=2))
    fmt = lambda r, k: (f"| {r['layout']} | {r[k]} | {r['top1']:.2f} | {r['top3']:.2f} | "
                        f"{r['band_acc']:.2f} | {r['cov90']:.2f} | {r['mean_set90']:.1f} | "
                        f"{r['overconfidence']:+.2f} | {r['mean_nlos_trusted']:.2f} | "
                        f"{r['mean_outlier_cols']:.2f} |")
    head = ("| layout | {} | top-1 | top-3 | band | cov90 | 90% set | overconf | NLOS trusted | outlier cols |\n"
            "|---|---|---|---|---|---|---|---|---|---|")
    md = ["### Presets\n", head.format("preset")] + [fmt(r, "preset") for r in main_rows]
    md += ["\n### One mechanism at a time (moderate severity)\n", head.format("mechanism")]
    md += [fmt(r, "ablation") for r in abl_rows]
    (out / "impairments.md").write_text("\n".join(md))
    print("\n".join(md))


if __name__ == "__main__":
    main()
