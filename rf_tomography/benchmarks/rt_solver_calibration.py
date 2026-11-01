"""Calibrate the solver's assumed error model to ray-traced statistics, then re-test it.

    OMP_NUM_THREADS=1 .venv/bin/python -m rf_tomography.benchmarks.rt_solver_calibration

For each facade table (concrete, glass) the solver's beliefs are fitted from the ray-traced
links the solver itself would trust (geometric LOS): angular floor and outlier rate from the
beamformed AoA, delay floor/outliers from first arrival, receiver-level non-direct rate for the
NLOS prior, and the log-distance RSS model (exponent, LOS sigma, NLOS offset+sigma). Beliefs are
inflated by a safety factor (earlier result: err on the paranoid side).

Evaluation is out-of-sample and paired: fit on even-numbered emitters, test on odd ones; also
fit on the OTHER facade table and test here (how material-specific is a calibration?). Every
solver variant sees identical observations. Observations use beam-peak AoA.
"""
from __future__ import annotations

import argparse
import ast
import json
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np

from ..geometry.scene import synthetic_manhattan_block
from ..localization.forward import column_sigma, noise_covariance, plan_columns
from ..measurements.models import EMITTERS, SensingParams
from ..measurements.raytraced import PathTable, link_observables
from ..placement.sites import candidate_sites, all_candidates
from ..propagation.model import PropagationParams
from ..uncertainty.floor_posterior import CandidateGrid, credible_set_size, floor_posterior, in_credible_set
from ..uncertainty.robust_posterior import RobustConfig, robust_floor_posterior
from .rt_calibration import beam_arrays, deg, fit_pathloss, solver_los, wrap
from .rt_eval import EST, RSS_EST_DB, make_layouts


def calibrate(T: PathTable, scene, owner, sites, subset, bf, safety: float = 2.0):
    """Fit solver beliefs on the emitters selected by boolean mask `subset`."""
    o = link_observables(T, 20.0)
    los = solver_los(scene, T, owner, sites)
    valid = (o.n_paths > 0) & subset[:, None]
    mL, mN = los & valid, ~los & valid
    fin = lambda x: x[np.isfinite(x)]
    ang = lambda m: np.concatenate([fin(deg(bf["el3"] - o.el_geo)[m]), fin(deg(wrap(bf["az3"] - o.az_geo))[m]),
                                    fin(deg(wrap(bf["az1"] - o.az_geo))[m])])
    a = ang(mL)
    core = a[np.abs(a) < 5]
    out_a = a[np.abs(a) > 5]
    ex = fin((o.toa_m - o.dist_m)[mL])
    core_t, out_t = ex[np.abs(ex) < 10], ex[ex > 10]
    sigma_ang = max(0.25, float(np.sqrt((core ** 2).mean())))
    sigma_toa = max(0.5, float(np.sqrt((core_t ** 2).mean())))
    p_out = max(float((np.abs(a) > 5).mean()), float((ex > 10).mean()))
    p_nontrust = 1.0 - float(o.strongest_direct[mL].mean())
    an = ang(mN)
    ex_n = fin((o.toa_m - o.dist_m)[mN])
    n_los, c_los, s_los = fit_pathloss(o.rss_db[mL], o.dist_m[mL])
    resid_n = o.rss_db[mN] - (c_los - n_los * 10 * np.log10(o.dist_m[mN]))
    resid_n = fin(resid_n)
    s_nlos = float(np.sqrt(resid_n.mean() ** 2 + resid_n.std() ** 2))
    cfg = RobustConfig(
        "calibrated", eps=float(np.clip(safety * p_out, 0.02, 0.4)),
        pi_nlos=float(np.clip(safety * p_nontrust, 0.02, 0.3)),
        sigma_out_angle_deg=float(np.clip(np.sqrt((out_a ** 2).mean()), 8, 60)) if len(out_a) else 15.0,
        sigma_out_toa_m=float(np.clip(np.sqrt((out_t ** 2).mean()), 15, 100)) if len(out_t) else 30.0,
        sigma_nlos_angle_deg=float(np.clip(np.sqrt((an ** 2).mean()), 20, 90)),
        sigma_nlos_toa_m=float(np.clip(np.sqrt((ex_n[ex_n > 0] ** 2).mean()), 10, 100)) if (ex_n > 0).any() else 60.0)
    sensing = SensingParams(multipath_angle_sigma_deg=sigma_ang,
                            multipath_range_sigma_m=sigma_toa)
    prop = PropagationParams(path_loss_exp=n_los, shadow_sigma_db_los=s_los, shadow_sigma_db_nlos=s_nlos)
    report = dict(n_links_los=int(mL.sum()), n_links_nlos=int(mN.sum()),
                  angle_core_sigma_deg=sigma_ang, angle_outlier_rate=float((np.abs(a) > 5).mean()),
                  toa_core_sigma_m=sigma_toa, toa_outlier_rate=float((ex > 10).mean()),
                  non_direct_rate_solver_los=p_nontrust, pathloss_exp=n_los, rss_sigma_los_db=s_los,
                  rss_nlos_offset_db=float(resid_n.mean()), rss_sigma_nlos_total_db=s_nlos,
                  safety=safety, robust=asdict(cfg))
    return sensing, prop, cfg, report


def evaluate(T, scene, owner, em, layouts, cands, variants, test_mask, bf, seed, stress=None):
    """variants: name -> (kind, cfg|None, prop, sensing). Same observations for every variant."""
    o = link_observables(T, 20.0)
    site_idx = {s: j for j, s in enumerate(ast.literal_eval(T.meta["repr"])["sites"])}
    grids = {b.name: CandidateGrid.for_building(b) for b in scene.buildings}
    world_prop = PropagationParams()
    rng = np.random.default_rng(seed)
    srng = np.random.default_rng(seed + 99)   # separate stream: unstressed results are unaffected
    rows = {(l, v): [] for l in layouts for v in variants}
    for e in np.where(test_mask)[0]:
        b = scene.by_name(owner[e])
        k = int(T.tx[e, 2] // b.floor_height)
        p0, t0 = rng.normal(0, 10.0), rng.normal(0, 200.0)
        for lname, idx in layouts.items():
            rx = [cands[i].receiver() for i in idx]
            j = [site_idx[cands[i].site.name] for i in idx]
            los = [not scene.segment_blocked(T.tx[e], np.array(r.pos), exclude=(owner[e], r.host_building)) for r in rx]
            alive = [bool(o.n_paths[e, jj] > 0) for jj in j]
            cols = [c for c in plan_columns(rx, los) if alive[c[0]]]
            if not cols:
                continue
            dist = [float(o.dist_m[e, jj]) for jj in j]
            y = np.empty(len(cols))
            for q, (i, kind) in enumerate(cols):
                jj = j[i]
                if kind == "rss":
                    y[q] = o.rss_db[e, jj] + p0 + rng.normal(0, RSS_EST_DB)
                elif kind == "toa":
                    y[q] = o.toa_m[e, jj] + t0 + rng.normal(0, column_sigma("toa", rx[i], True, dist[i], em, EST, world_prop))
                else:
                    base = bf[rx[i].horizontal_aperture_m][e, jj] if kind == "az" else bf["el"][e, jj]
                    y[q] = base + rng.normal(0, column_sigma(kind, rx[i], True, dist[i], em, EST, world_prop))
            if stress:   # reality dirtier than the tracer: extra noise + heavy-tailed outliers
                for q, (i, kind) in enumerate(cols):
                    if kind == "rss":
                        continue
                    if kind == "toa":
                        y[q] += srng.normal(0, stress["extra_toa_m"])
                        if srng.random() < stress["outlier_prob"]:
                            y[q] += srng.exponential(20.0)
                    else:
                        y[q] += srng.normal(0, np.radians(stress["extra_angle_deg"]))
                        if srng.random() < stress["outlier_prob"]:
                            y[q] += srng.normal(0, np.radians(15.0))
            for vname, (kind_s, cfg, prop, sensing) in variants.items():
                cov = noise_covariance(cols, rx, los, dist, em, sensing, prop)
                if kind_s == "gaussian":
                    pr = floor_posterior(y, grids[b.name], rx, cols, cov, prop)
                else:
                    pr, _ = robust_floor_posterior(y, grids[b.name], rx, cols, cov, prop, cfg)
                order = np.argsort(-pr)
                rank = int(np.where(order == k)[0][0])
                rows[(lname, vname)].append((rank == 0, rank < 3, float(pr[k]), in_credible_set(pr, k),
                                             credible_set_size(pr), float(pr @ np.arange(len(pr))) - k))
    return rows


def summarise(rows, base_name="robust_default"):
    out = []
    for (l, v), r in rows.items():
        a = np.array(r, float)
        base = np.array(rows[(l, base_name)], float)
        d = a[:, 2] - base[:, 2]
        out.append(dict(layout=l, solver=v, n=len(a), top1=a[:, 0].mean(), top3=a[:, 1].mean(), p_truth=a[:, 2].mean(),
                        cov90=a[:, 3].mean(), set90=a[:, 4].mean(), bias=a[:, 5].mean(),
                        mae=np.abs(a[:, 5]).mean(), d_p_vs_robust_default=d.mean(),
                        se=d.std(ddof=1) / np.sqrt(len(d)) if len(d) > 1 else 0.0))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--emitter", default="lora_915M_200k")
    ap.add_argument("--safety", type=float, default=2.0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--dir", default=str(Path(__file__).parent / "results"))
    a = ap.parse_args(argv)
    scene, prop0 = synthetic_manhattan_block(), PropagationParams()
    em = next(e for e in EMITTERS if e.name == a.emitter)
    cands, sites = all_candidates(scene), candidate_sites(scene)
    layouts = make_layouts(scene, cands, em, prop0, a.seed)
    tabs = {n: PathTable.load(Path(a.dir, "rt", f"block_915MHz_{n}.npz")) for n in ("concrete", "glass")}
    meta = {n: ast.literal_eval(t.meta["repr"]) for n, t in tabs.items()}
    arrs = {n: beam_arrays(t) for n, t in tabs.items()}   # computed once per table
    bfs = {n: {"el": a_["el3"], 1.0: a_["az1"], 3.0: a_["az3"]} for n, a_ in arrs.items()}
    N = len(tabs["concrete"].tx)
    even, odd = (np.arange(N) % 2 == 0), (np.arange(N) % 2 == 1)
    full = np.ones(N, bool)
    cal_all = {n: calibrate(tabs[n], scene, meta[n]["owner"], sites, full, arrs[n], a.safety) for n in tabs}
    result, md, params = {}, [], {}
    for tgt in ("concrete", "glass"):
        other = "glass" if tgt == "concrete" else "concrete"
        own = calibrate(tabs[tgt], scene, meta[tgt]["owner"], sites, even, arrs[tgt], a.safety)
        cross = cal_all[other]
        variants = {"gaussian_default": ("gaussian", None, prop0, SensingParams()),
                    "robust_default": ("robust", RobustConfig("robust"), prop0, SensingParams()),
                    "gaussian_cal_own": ("gaussian", None, own[1], own[0]),
                    "robust_cal_own": ("robust", own[2], own[1], own[0]),
                    "gaussian_cal_other": ("gaussian", None, cross[1], cross[0]),
                    "robust_cal_other": ("robust", cross[2], cross[1], cross[0])}
        rows = evaluate(tabs[tgt], scene, meta[tgt]["owner"], em, layouts, cands, variants, odd, bfs[tgt], a.seed)
        res = summarise(rows)
        result[tgt] = res
        params[f"{tgt}_fit_on_even"] = own[3]
        md += [f"### {tgt} facades: test on odd emitters (n={int(odd.sum())}), beam-peak AoA\n",
               "| layout | solver | top-1 | top-3 | cov90 | 90% set | p(truth) | bias | MAE | d p(truth) vs robust_default (SE) |",
               "|---|---|---|---|---|---|---|---|---|---|"]
        for r in res:
            md.append(f"| {r['layout']} | {r['solver']} | {r['top1']:.2f} | {r['top3']:.2f} | {r['cov90']:.2f} | {r['set90']:.1f} | "
                      f"{r['p_truth']:.2f} | {r['bias']:+.2f} | {r['mae']:.2f} | {r['d_p_vs_robust_default']:+.3f} ({r['se']:.3f}) |")
        md.append("")
    # --- stress test: calibrated beliefs vs a dirtier world --------------------------------
    STRESS = {"none": None, "mild (+1 deg, +2 m, 5% outliers)": dict(extra_angle_deg=1.0, extra_toa_m=2.0, outlier_prob=0.05),
              "strong (+2 deg, +4 m, 15% outliers)": dict(extra_angle_deg=2.0, extra_toa_m=4.0, outlier_prob=0.15)}
    stress_res, smd = {}, ["### Stress test: calibrated beliefs vs a dirtier world (test = odd emitters)\n",
                           "| table | stress | layout | solver | top-1 | top-3 | cov90 | 90% set | bias |", "|---|---|---|---|---|---|---|---|---|"]
    lay_s = {k: layouts[k] for k in ("analytic@14", "analytic@20", "street_arrays(5)")}
    for tgt in ("concrete", "glass"):
        own = calibrate(tabs[tgt], scene, meta[tgt]["owner"], sites, even, arrs[tgt], a.safety)
        wide = (replace(own[0], multipath_angle_sigma_deg=own[0].multipath_angle_sigma_deg * 3,
                        multipath_range_sigma_m=own[0].multipath_range_sigma_m * 3), own[1], own[2])
        variants = {"robust_default": ("robust", RobustConfig("robust"), prop0, SensingParams()),
                    "robust_cal_own": ("robust", own[2], own[1], own[0]),
                    "robust_cal_own_floors_x3": ("robust", wide[2], wide[1], wide[0]),
                    "gaussian_cal_own": ("gaussian", None, own[1], own[0])}
        for sname, st in STRESS.items():
            rows = evaluate(tabs[tgt], scene, meta[tgt]["owner"], em, lay_s, cands, variants, odd, bfs[tgt], a.seed, st)
            for r in summarise(rows):
                stress_res.setdefault(tgt, {}).setdefault(sname, []).append(r)
                smd.append(f"| {tgt} | {sname} | {r['layout']} | {r['solver']} | {r['top1']:.2f} | {r['top3']:.2f} | "
                           f"{r['cov90']:.2f} | {r['set90']:.1f} | {r['bias']:+.2f} |")
    params["fit_on_all"] = {n: v[3] for n, v in cal_all.items()}
    rep = ["### Calibrated beliefs (default -> fitted), safety factor %.1f\n" % a.safety,
           "| parameter | default | concrete (even emitters) | glass (even emitters) |", "|---|---|---|---|"]
    d = params
    C, G = d["concrete_fit_on_even"], d["glass_fit_on_even"]
    rows_ = [("angle floor (deg)", "2.0", "angle_core_sigma_deg"), ("delay floor (m)", "3.0", "toa_core_sigma_m"),
             ("angle outlier rate", "0.10-0.25", "angle_outlier_rate"), ("delay outlier rate", "0.10-0.25", "toa_outlier_rate"),
             ("non-direct rate (solver-LOS)", "0.10-0.25", "non_direct_rate_solver_los"),
             ("path-loss exponent", "2.7", "pathloss_exp"), ("RSS sigma LOS (dB)", "6", "rss_sigma_los_db"),
             ("RSS sigma NLOS incl. offset (dB)", "8", "rss_sigma_nlos_total_db")]
    for lab, dflt, key in rows_:
        rep.append(f"| {lab} | {dflt} | {C[key]:.3f} | {G[key]:.3f} |")
    rep.append(f"| robust eps / pi_nlos | 0.15 / 0.15 | {C['robust']['eps']:.2f} / {C['robust']['pi_nlos']:.2f} | {G['robust']['eps']:.2f} / {G['robust']['pi_nlos']:.2f} |")
    rep.append("")
    Path(a.dir, "rt_solver_calibration.json").write_text(json.dumps(dict(params=params, results=result, stress=stress_res), indent=2, default=float))
    Path(a.dir, "rt_solver_calibration.md").write_text("\n".join(rep + md + [""] + smd))
    print("\n".join(rep + md + [""] + smd))


if __name__ == "__main__":
    main()
