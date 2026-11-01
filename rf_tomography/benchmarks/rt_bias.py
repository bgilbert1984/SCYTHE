"""Where does the floor bias come from?

    OMP_NUM_THREADS=1 .venv/bin/python -m rf_tomography.benchmarks.rt_bias

Signed floor error = posterior-mean floor - true floor (positive = estimated too high), robust
solver, default beliefs, paired noise, every other emitter. Diagnostics:

 * AoA mode: 'beamformed' (as in rt_eval), 'strongest' path, and 'oracle' (the geometrically exact
   direct-path bearings and range). If the bias vanishes under the oracle, the cause is the
   measurement model (multipath), not floor enumeration, coordinates or the solver.
 * mean vs mode error (a bounded posterior mean regresses toward the building middle).
 * bias by position in the building, building height, range, direct-dominance, facade style.
 * link-level signed elevation residual (beam peak - geometric) by receiver kind and emitter height.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from ..measurements.models import EMITTERS
from ..measurements.raytraced import PathTable, link_observables
from ..placement.sites import all_candidates
from ..propagation.model import PropagationParams
from .rt_calibration import beam_arrays, deg, solver_los
from .rt_eval import SOLVERS, make_layouts, run
from .scenes import candidates_from, scene_and_sites, table_meta

LAYOUTS = ("analytic@20", "street_arrays(5)")


def grp(trials, key_fn, labels):
    out = {}
    for lab in labels:
        t = [r for r in trials if key_fn(r) == lab]
        if t:
            out[lab] = dict(n=len(t), bias=float(np.mean([r["err"] for r in t])), mode_bias=float(np.mean([r["mode_err"] for r in t])),
                            mae=float(np.mean([abs(r["err"]) for r in t])), cov90=float(np.mean([r["cov"] for r in t])),
                            set90=float(np.mean([r["size"] for r in t])), top1=float(np.mean([r["top1"] for r in t])))
    return out


def link_el_residuals(T, scene, owner, sites, bf_el):
    o = link_observables(T, 20.0)
    los = solver_los(scene, T, owner, sites) & (o.n_paths > 0)
    d = deg(bf_el - o.el_geo)
    kind = np.array([s.kind for s in sites])
    hz = np.array([T.tx[e, 2] / scene.by_name(owner[e]).height for e in range(len(T.tx))])
    res = {}
    for kd in ("street", "roof"):
        for lab, lo, hi in (("low third", 0, 1 / 3), ("mid third", 1 / 3, 2 / 3), ("high third", 2 / 3, 1.01)):
            m = los & (kind[None, :] == kd) & ((hz >= lo) & (hz < hi))[:, None] & np.isfinite(d)
            if m.sum() > 20:
                x = d[m]
                core = x[np.abs(x) < 5]
                res[f"{kd} / emitter in {lab}"] = dict(links=int(m.sum()), mean_all=float(x.mean()), median=float(np.median(x)),
                                                      mean_core=float(core.mean()), frac_pos_gt5=float((x > 5).mean()),
                                                      frac_neg_lt5=float((x < -5).mean()))
    return res


def rss_matrix(a):
    """Which measurement drives the bias? angles/range exact or not x RSS as traced / dropped / model-exact."""
    em, prop = EMITTERS[1], PropagationParams()
    tabs = a.tables or [str(Path(a.dir, "rt", f"midtown_915MHz_{p}.npz")) for p in ("mixed", "concrete", "glass_solid")]
    out, md, cache = {}, ["### Which measurement drives the floor bias? (analytic@20, robust, beam-peak or exact bearings)\n",
                          "Bias = mean(posterior-mean floor - true floor) in floors; position groups are thirds of the building.\n",
                          "| table | bearings+range | RSS | all | low third | mid third | high third | MAE | top-1 | cov90 | set |", "|---|---|---|---|---|---|---|---|---|---|---|"], {}
    for tp in tabs:
        T = PathTable.load(tp)
        meta = table_meta(T)
        scene, sites, hosts = scene_and_sites(meta)
        if "mid" not in cache:
            cands = candidates_from(sites)
            lay = make_layouts(scene, cands, em, prop, a.seed, hosts)
            cache["mid"] = (cands, {"analytic@20": lay["analytic@20"]})
        cands, layouts = cache["mid"]
        name = Path(tp).stem
        out[name] = {}
        for aoa in ("beamformed", "oracle"):
            for rss in ("model", "drop", "oracle"):
                rows = run(T, scene, meta["owner"], em, prop, cands, layouts, a.seed, {"robust": SOLVERS["robust"]}, aoa,
                           a.stride, True, True, True, rss)
                tr = rows[("analytic@20", "robust")]
                pos = lambda r: "low" if r["k"] / r["nf"] < 1 / 3 else "mid" if r["k"] / r["nf"] < 2 / 3 else "high"
                gb = {g: float(np.mean([r["err"] for r in tr if pos(r) == g])) for g in ("low", "mid", "high")}
                v = dict(bias=float(np.mean([r["err"] for r in tr])), low=gb["low"], mid=gb["mid"], high=gb["high"],
                         mae=float(np.mean([abs(r["err"]) for r in tr])), top1=float(np.mean([r["top1"] for r in tr])),
                         cov90=float(np.mean([r["cov"] for r in tr])), set90=float(np.mean([r["size"] for r in tr])), n=len(tr))
                out[name][f"{aoa}|{rss}"] = v
                lab_a = "traced (beam peak, first arrival)" if aoa == "beamformed" else "exact"
                lab_r = {"model": "traced power", "drop": "none (dropped)", "oracle": "solver's own model, exact range"}[rss]
                md.append(f"| {name.replace('midtown_915MHz_', '')} | {lab_a} | {lab_r} | {v['bias']:+.2f} | {v['low']:+.2f} | {v['mid']:+.2f} | {v['high']:+.2f} | {v['mae']:.2f} | {v['top1']:.2f} | {v['cov90']:.2f} | {v['set90']:.1f} |")
                print(md[-1], flush=True)
    Path(a.dir, "rt_bias_rss.json").write_text(json.dumps(out, indent=2))
    Path(a.dir, "rt_bias_rss.md").write_text("\n".join(md))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--rss-matrix", action="store_true", help="run the angles x RSS ablation instead")
    ap.add_argument("--tables", nargs="+", default=None)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--stride", type=int, default=2)
    ap.add_argument("--aoa", nargs="+", default=["beamformed", "strongest", "oracle"])
    ap.add_argument("--out-name", default="rt_bias_midtown")
    ap.add_argument("--dir", default=str(Path(__file__).parent / "results"))
    a = ap.parse_args(argv)
    if a.rss_matrix:
        return rss_matrix(a)
    em, prop = EMITTERS[1], PropagationParams()
    tabs = a.tables or [str(Path(a.dir, "rt", f"midtown_915MHz_{p}.npz")) for p in ("mixed", "concrete", "glass_solid")]
    cache, result, md = {}, {}, []
    for tp in tabs:
        T = PathTable.load(tp)
        meta = table_meta(T)
        scene, sites, hosts = scene_and_sites(meta)
        if "mid" not in cache:
            cands = candidates_from(sites)
            lay = make_layouts(scene, cands, em, prop, a.seed, hosts)
            cache["mid"] = (cands, {k: lay[k] for k in LAYOUTS})
        cands, layouts = cache["mid"]
        name = Path(tp).stem
        style = {b.name: b.facade for b in scene.buildings}
        res = {"by_aoa": {}, "groups": {}}
        for aoa in a.aoa:
            rows = run(T, scene, meta["owner"], em, prop, cands, layouts, a.seed, {"robust": SOLVERS["robust"]}, aoa,
                       a.stride, True, True, True)
            for (lay_name, _), trials in rows.items():
                res["by_aoa"][f"{aoa} | {lay_name}"] = dict(
                    n=len(trials), bias=float(np.mean([t["err"] for t in trials])), mode_bias=float(np.mean([t["mode_err"] for t in trials])),
                    mae=float(np.mean([abs(t["err"]) for t in trials])), top1=float(np.mean([t["top1"] for t in trials])),
                    cov90=float(np.mean([t["cov"] for t in trials])))
                if aoa in ("beamformed", "oracle") and lay_name == "analytic@20":
                    sfx = "" if aoa == "beamformed" else " [oracle bearings]"
                    G = res["groups"]
                    pos = lambda r: "low third" if r["k"] / r["nf"] < 1 / 3 else "mid third" if r["k"] / r["nf"] < 2 / 3 else "high third"
                    q = np.quantile([r["mean_dist"] for r in trials], [1 / 3, 2 / 3])
                    qs = np.quantile([r["size"] for r in trials], [1 / 3, 2 / 3])
                    G["position in building" + sfx] = grp(trials, pos, ["low third", "mid third", "high third"])
                    G["90% set size" + sfx] = grp(trials, lambda r: "narrow" if r["size"] <= qs[0] else "medium" if r["size"] <= qs[1] else "wide", ["narrow", "medium", "wide"])
                    G["receivers with line of sight" + sfx] = grp(trials, lambda r: "0-2" if r["n_los"] <= 2 else "3-4" if r["n_los"] <= 4 else "5+", ["0-2", "3-4", "5+"])
                    G["mean range to used receivers" + sfx] = grp(trials, lambda r: "near" if r["mean_dist"] < q[0] else "mid" if r["mean_dist"] < q[1] else "far", ["near", "mid", "far"])
                    if aoa == "beamformed":
                        G["building size"] = grp(trials, lambda r: "<= 40 floors" if r["nf"] <= 40 else "> 40 floors", ["<= 40 floors", "> 40 floors"])
                        G["direct-dominant links"] = grp(trials, lambda r: "frac_direct >= 0.8" if r["frac_direct"] >= 0.8 else "frac_direct < 0.8", ["frac_direct >= 0.8", "frac_direct < 0.8"])
                        G["facade style of host"] = grp(trials, lambda r: style[r["owner"]], sorted(set(style[r["owner"]] for r in trials)))
                    # shrinkage test: is the bias just the posterior mean regressing to the building middle?
                    mid = [(r["nf"] - 1) / 2 for r in trials]
                    G["mean estimate vs truth" + sfx] = {"all": dict(
                        n=len(trials), bias=float(np.mean([r["err"] for r in trials])), mode_bias=float(np.mean([r["mode_err"] for r in trials])),
                        mae=float(np.mean([abs(r["err"]) for r in trials])), cov90=float(np.mean([r["cov"] for r in trials])),
                        set90=float(np.mean([r["size"] for r in trials])), top1=float(np.mean([r["top1"] for r in trials])),
                        slope_est_on_truth=float(np.polyfit([r["k"] / r["nf"] for r in trials], [(r["k"] + r["err"]) / r["nf"] for r in trials], 1)[0]),
                        frac_pull_to_middle=float(np.mean([abs(r["k"] + r["err"] - m) < abs(r["k"] - m) for r, m in zip(trials, mid)])))}
        arr = beam_arrays(T)
        res["link_el_residual_deg"] = link_el_residuals(T, scene, meta["owner"], sites, arr["el3"])
        result[name] = res
        md += [f"### {name}\n", "| AoA | layout | n | mean bias | mode bias | MAE | top-1 | cov90 |", "|---|---|---|---|---|---|---|---|"]
        for k, v in res["by_aoa"].items():
            aoa_, lay_ = k.split(" | ")
            md.append(f"| {aoa_} | {lay_} | {v['n']} | {v['bias']:+.2f} | {v['mode_bias']:+.2f} | {v['mae']:.2f} | {v['top1']:.2f} | {v['cov90']:.2f} |")
        for gname, g in res["groups"].items():
            md += ["", f"Bias by {gname} (analytic@20, robust):", "", "| group | n | mean bias | mode bias | MAE | top-1 | cov90 | set |", "|---|---|---|---|---|---|---|---|"]
            md += [f"| {lab} | {v['n']} | {v['bias']:+.2f} | {v['mode_bias']:+.2f} | {v['mae']:.2f} | {v['top1']:.2f} | {v['cov90']:.2f} | {v['set90']:.1f} |" for lab, v in g.items()]
        md += ["", "Signed elevation residual (beam peak - geometric), solver-LOS links, degrees:", "",
               "| receiver / emitter height | links | mean | median | mean of |err|<5 | P(> +5) | P(< -5) |", "|---|---|---|---|---|---|---|"]
        md += [f"| {lab} | {v['links']} | {v['mean_all']:+.2f} | {v['median']:+.2f} | {v['mean_core']:+.2f} | {v['frac_pos_gt5']:.3f} | {v['frac_neg_lt5']:.3f} |"
               for lab, v in res["link_el_residual_deg"].items()]
        md.append("")
    Path(a.dir, f"{a.out_name}.json").write_text(json.dumps(result, indent=2))
    Path(a.dir, f"{a.out_name}.md").write_text("\n".join(md))
    print("\n".join(md))


if __name__ == "__main__":
    main()
