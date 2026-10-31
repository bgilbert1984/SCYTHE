"""Does the robust solver survive errors it was NOT designed around?

    .venv/bin/python -m rf_tomography.benchmarks.correlated

The robust solver assumes independent heavy-tailed column errors. Here the truth is
coherent: single-bounce reflections (mutually consistent az/el/TOA of a mirror-image
emitter, one-sided delay), a common elevation calibration bias, and a common delay
(positive control: it is the clock offset and must be harmless). Solvers see identical
observations. Severities are assumptions; the independent 'moderate' preset is included
as a reference point.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

import numpy as np

from ..geometry.scene import synthetic_manhattan_block
from ..measurements.impairments import CORRELATED_PRESETS, PRESETS, ImpairmentModel
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
    "robust": RobustConfig("robust"),
    "robust_paranoid": RobustConfig("robust_paranoid", eps=0.40, pi_nlos=0.40),
}
SCENARIOS = {
    "none": PRESETS["none"],
    "moderate(indep. ref)": PRESETS["moderate"],
    **CORRELATED_PRESETS,
}
ABLATIONS = {
    "refl_ground_only(.35)": ImpairmentModel("g", reflect_prob=0.35, reflect_surfaces="ground"),
    "refl_facade_only(.35)": ImpairmentModel("f", reflect_prob=0.35, reflect_surfaces="facade"),
    "common_delay_only(40m)": ImpairmentModel("d", common_delay_mean_m=40.0),
    "el_bias_only(0.5deg)": ImpairmentModel("e5", el_bias_common_deg=0.5),
    "el_bias_only(2deg)": ImpairmentModel("e2", el_bias_common_deg=2.0),
}


CALIB_SCENARIOS = {
    "none": PRESETS["none"],
    "moderate(indep. ref)": PRESETS["moderate"],
    "el_bias(1deg)": ImpairmentModel("c1", el_bias_common_deg=1.0),
    "el_bias(2deg)": ImpairmentModel("c2", el_bias_common_deg=2.0),
    "el_bias(3deg)": ImpairmentModel("c3", el_bias_common_deg=3.0),
    "refl_strong": CORRELATED_PRESETS["refl_strong"],
    "combined+el_bias(2deg)": replace(CORRELATED_PRESETS["combined"], el_bias_common_deg=2.0),
}
CALIB_SOLVERS = {
    "robust": RobustConfig("robust"),
    "robust+el_offset_mean(2deg)": RobustConfig("robust_el_mean", el_offset_sigma_deg=2.0,
                                                el_offset_estimator="mean"),
    "robust+el_offset_median(2deg)": RobustConfig("robust_el_med", el_offset_sigma_deg=2.0),
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

    def sweep(scen, lnames):
        out = []
        for lname in lnames:
            for sname, imp in scen.items():
                for vname, solver in SOLVERS.items():
                    out.append(dict(layout=lname, scenario=sname, solver=vname,
                                    **cell(scene, layouts[lname], em, imp, solver, prop, grids,
                                           a.trials, a.seed)))
        return out

    main_rows = sweep(SCENARIOS, list(layouts))
    abl_rows = sweep(ABLATIONS, ["hand:street_array+roofs", "optimised@20"])

    calib_rows = []
    for lname in ("hand:street_array+roofs", "optimised@20", "optimised@14"):
        for sname, imp in CALIB_SCENARIOS.items():
            for vname, solver in CALIB_SOLVERS.items():
                calib_rows.append(dict(layout=lname, scenario=sname, solver=vname,
                                       **cell(scene, layouts[lname], em, imp, solver, prop,
                                              grids, a.trials, a.seed)))

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    meta = dict(emitter=em.name, seed=a.seed, trials_per_building=a.trials,
                scenarios={k: v.__dict__ for k, v in {**SCENARIOS, **ABLATIONS}.items()},
                note="SYNTHETIC; coherent errors the robust solver was not designed for; assumed severities")
    (out / "correlated.json").write_text(json.dumps(dict(meta=meta, main=main_rows,
                                                          ablations=abl_rows,
                                                          calibration=calib_rows), indent=2))
    f = lambda v: "-" if v is None else f"{v:.2f}"
    head = ("| layout | scenario | solver | top-1 | top-3 | cov90 | 90% set | overconf | floor bias | "
            "reflected/trial | refl flagged |\n|---|---|---|---|---|---|---|---|---|---|---|")
    fmt = lambda r: (f"| {r['layout']} | {r['scenario']} | {r['solver']} | {r['top1']:.2f} | "
                     f"{r['top3']:.2f} | {r['cov90']:.2f} | {r['mean_set90']:.1f} | "
                     f"{r['overconfidence']:+.2f} | {r['mean_floor_error']:+.2f} | "
                     f"{r['mean_reflected']:.2f} | {f(r['refl_recall'])} |")
    md = ["### Scenarios\n", head] + [fmt(r) for r in main_rows]
    md += ["\n### One mechanism at a time\n", head] + [fmt(r) for r in abl_rows]
    md += ["\n### Common elevation offset as a nuisance parameter\n", head] + [fmt(r) for r in calib_rows]
    (out / "correlated.md").write_text("\n".join(md))
    print("\n".join(md))


if __name__ == "__main__":
    main()
