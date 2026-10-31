"""What does a ray-traced city say about the error model the other experiments assume?

    .venv/bin/python -m rf_tomography.benchmarks.rt_calibration [--table results/rt/NAME.npz]

Reads a PathTable made by rt_trace.py, defines the observables a sensor would extract (total
power, first arrival within a detection threshold of the strongest path, AoA of the strongest
path), and compares them with the direct-path geometry the solver assumes. 'Solver-LOS' is the
solver's own belief: the geometric box test with the same exclusions as the simulations.

Caveat that matters: this tracer has specular reflection, refraction through walls and
diffraction, but NO diffuse scattering, foliage, vehicles or indoor clutter. A direct path is
therefore exact, so the angular/delay spreads here are LOWER bounds on real ones.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

import numpy as np

from ..measurements.raytraced import (DIFFRACTION, REFRACTION, SPECULAR, PathTable, beamformed_aoa,
                                      link_observables)
from .scenes import scene_and_sites

wrap = lambda x: (x + np.pi) % (2 * np.pi) - np.pi
deg = np.degrees


def solver_los(scene, T, owner, sites):
    L = np.zeros((len(T.tx), len(T.rx)), bool)
    for i, p in enumerate(T.tx):
        for j, r in enumerate(T.rx):
            L[i, j] = not scene.segment_blocked(p, r, exclude=(owner[i], sites[j].host))
    return L


def fit_pathloss(rss_db, dist):
    x = 10 * np.log10(dist)
    A = np.stack([np.ones_like(x), -x], 1)
    coef, *_ = np.linalg.lstsq(A, rss_db, rcond=None)
    res = rss_db - A @ coef
    return float(coef[1]), float(coef[0]), float(res.std())


def q(x, qs=(0.5, 0.9, 0.99)):
    return [float(np.quantile(x, v)) for v in qs] if len(x) else [float("nan")] * len(qs)


def table_meta(T: PathTable) -> dict:
    return ast.literal_eval(T.meta["repr"])


def beam_arrays(T: PathTable) -> dict:
    """Beamformed AoA for the aperture tiers used in placement/sites.py (3 m array, 1 m azimuth)."""
    return {"el3": beamformed_aoa(T, 3.0, "el"), "az3": beamformed_aoa(T, 3.0, "az"),
            "az1": beamformed_aoa(T, 1.0, "az")}


def analyse(T: PathTable, scene, owner, sites, thr_db=20.0, bf=None):
    o = link_observables(T, thr_db)
    los = solver_los(scene, T, owner, sites)
    valid = o.n_paths > 0
    d_az, d_el = np.abs(deg(wrap(o.az - o.az_geo))), np.abs(deg(o.el - o.el_geo))
    d_toa = o.toa_m - o.dist_m
    out = dict(n_emitters=int(len(T.tx)), n_sites=int(len(T.rx)), links=int(valid.sum()),
               threshold_db=thr_db, frequency_MHz=T.frequency_hz / 1e6, facade=table_meta(T).get("facade"),
               scattering=float(table_meta(T).get("scattering", 0.0)))
    for name, m in (("solver_LOS", los & valid), ("solver_NLOS", ~los & valid)):
        e = {}
        e["links"] = int(m.sum())
        e["strongest_is_direct"] = float(o.strongest_direct[m].mean())
        e["first_arrival_is_direct"] = float(o.first_direct[m].mean())
        e["az_err_deg_q50_90_99"] = q(d_az[m]); e["el_err_deg_q50_90_99"] = q(d_el[m])
        e["P_el_err_gt_5deg"] = float((d_el[m] > 5).mean()); e["P_az_err_gt_5deg"] = float((d_az[m] > 5).mean())
        e["P_el_err_gt_10deg"] = float((d_el[m] > 10).mean())
        e["toa_excess_m_q50_90_99"] = q(d_toa[m]); e["P_toa_excess_gt_3m"] = float((d_toa[m] > 3).mean())
        e["P_toa_excess_gt_10m"] = float((d_toa[m] > 10).mean())
        n, c0, sig = fit_pathloss(o.rss_db[m], o.dist_m[m])
        e["pathloss_exponent"], e["rss_offset_db"], e["rss_resid_sigma_db"] = n, c0, sig
        ct = T.ctype[np.arange(len(T.tx))[:, None], np.arange(len(T.rx))[None, :], np.argmax(T.power, -1)]
        dp = np.take_along_axis(T.depth, np.argmax(T.power, -1)[..., None], -1)[..., 0]
        kinds = {"direct-like (may cross walls)": (ct & (SPECULAR | DIFFRACTION)) == 0,
                 "one specular bounce": ((ct & SPECULAR) > 0) & ((ct & DIFFRACTION) == 0) & (dp - ((ct & REFRACTION) > 0) <= 1),
                 "diffraction involved": (ct & DIFFRACTION) > 0}
        kinds["other multi-bounce"] = ~(kinds["direct-like (may cross walls)"] | kinds["one specular bounce"] | kinds["diffraction involved"])
        e["strongest_path_census"] = {k: float(v[m].mean()) for k, v in kinds.items()}
        if bf is not None:
            for key, ang, geo, is_az in (("el3", bf["el3"], o.el_geo, False), ("az3", bf["az3"], o.az_geo, True),
                                         ("az1", bf["az1"], o.az_geo, True)):
                err = deg(wrap(ang - geo) if is_az else ang - geo)[m]
                err = err[np.isfinite(err)]
                core = err[np.abs(err) < 5]
                e["beam_" + key] = dict(P_gt_5deg=float((np.abs(err) > 5).mean()),
                                        core_sigma_deg=float(np.sqrt((core ** 2).mean())) if len(core) else None,
                                        abs_q50_90_99=q(np.abs(err)))
        out[name] = e
    # same-distance comparison of power, trusted vs not
    out["nlos_extra_loss_db_at_equal_distance"] = (
        out["solver_LOS"]["rss_offset_db"] - out["solver_NLOS"]["rss_offset_db"])
    # by receiver site kind, among solver-LOS links
    by = {}
    kinds_arr = np.array([s.kind for s in sites])
    for kd in ("street", "roof", "facade"):
        m = los & valid & (kinds_arr[None, :] == kd)
        if m.sum():
            by[kd] = dict(links=int(m.sum()), strongest_is_direct=float(o.strongest_direct[m].mean()),
                          P_el_err_gt_5deg=float((d_el[m] > 5).mean()), P_az_err_gt_5deg=float((d_az[m] > 5).mean()),
                          P_toa_excess_gt_10m=float((d_toa[m] > 10).mean()))
    out["by_site_kind_solver_LOS"] = by
    return out


def render(res, thr_sens):
    L, N = res["solver_LOS"], res["solver_NLOS"]
    f = lambda x: f"{x:.2f}"
    md = [f"### Ray-traced observables vs the assumed error model "
          f"({res['frequency_MHz']:.0f} MHz, {res['facade']} facades, scattering {res['scattering']:.1f}, {res['n_emitters']} emitters x {res['n_sites']} sites, "
          f"{res['links']} links, first-arrival threshold {res['threshold_db']:.0f} dB)\n",
          "| quantity | solver-LOS links | solver-NLOS links | assumed in the Gaussian/impairment experiments |",
          "|---|---|---|---|",
          f"| links | {L['links']} | {N['links']} | |",
          f"| strongest path is direct-like | {f(L['strongest_is_direct'])} | {f(N['strongest_is_direct'])} | LOS: ~1 minus outliers (10-25%); NLOS: AoA/TOA not trusted |",
          f"| first arrival is direct-like | {f(L['first_arrival_is_direct'])} | {f(N['first_arrival_is_direct'])} | |",
          f"| elevation error q50 / q90 / q99 (deg) | {' / '.join(f'{v:.2f}' for v in L['el_err_deg_q50_90_99'])} | {' / '.join(f'{v:.1f}' for v in N['el_err_deg_q50_90_99'])} | Gaussian 2 deg floor: 1.35 / 3.3 / 5.2 |",
          f"| azimuth error q50 / q90 / q99 (deg) | {' / '.join(f'{v:.2f}' for v in L['az_err_deg_q50_90_99'])} | {' / '.join(f'{v:.1f}' for v in N['az_err_deg_q50_90_99'])} | same |",
          f"| P(elevation error > 5 deg) | {f(L['P_el_err_gt_5deg'])} | {f(N['P_el_err_gt_5deg'])} | Gaussian 0.01; with outliers 0.10-0.25 |",
          f"| P(azimuth error > 5 deg) | {f(L['P_az_err_gt_5deg'])} | {f(N['P_az_err_gt_5deg'])} | same |",
          f"| TOA excess q50 / q90 / q99 (m) | {' / '.join(f'{v:.1f}' for v in L['toa_excess_m_q50_90_99'])} | {' / '.join(f'{v:.1f}' for v in N['toa_excess_m_q50_90_99'])} | 3 m Gaussian floor (+3 m sync); outliers exp. mean 15-25 m |",
          f"| P(TOA excess > 10 m) | {f(L['P_toa_excess_gt_10m'])} | {f(N['P_toa_excess_gt_10m'])} | Gaussian ~0.02; with outliers 0.10-0.25 |",
          f"| path-loss exponent n | {f(L['pathloss_exponent'])} | {f(N['pathloss_exponent'])} | 2.7 |",
          f"| RSS residual sigma (dB) | {f(L['rss_resid_sigma_db'])} | {f(N['rss_resid_sigma_db'])} | 6 (LOS) / 8 (NLOS) |",
          f"| strongest path: direct / one bounce / diffraction / multi | " + " / ".join(f"{v:.2f}" for v in L['strongest_path_census'].values()) + " | " + " / ".join(f"{v:.2f}" for v in N['strongest_path_census'].values()) + " | |",
          "", f"Mean power offset between solver-LOS and solver-NLOS fits: {res['nlos_extra_loss_db_at_equal_distance']:+.1f} dB.", "",
          "By receiver site kind (solver-LOS links):", "", "| site kind | links | strongest direct | P(el err>5 deg) | P(az err>5 deg) | P(TOA excess>10 m) |", "|---|---|---|---|---|---|"]
    for k, v in res["by_site_kind_solver_LOS"].items():
        md.append(f"| {k} | {v['links']} | {f(v['strongest_is_direct'])} | {f(v['P_el_err_gt_5deg'])} | {f(v['P_az_err_gt_5deg'])} | {f(v['P_toa_excess_gt_10m'])} |")
    if "beam_el3" in L:
        md += ["", "Angle of arrival as a finite aperture would report it (power-weighted beam peak; "
               "no path phases, so a non-coherent approximation):", "",
               "| aperture | links | core sigma (errors < 5 deg) | P(error > 5 deg) | abs error q50 / q90 / q99 (deg) | assumed |",
               "|---|---|---|---|---|---|"]
        for lab, key, assumed in (("3 m vertical (elevation)", "beam_el3", "2 deg floor"), ("3 m horizontal (azimuth)", "beam_az3", "2 deg floor"),
                                  ("1 m horizontal (azimuth)", "beam_az1", "2 deg floor")):
            for grp, g in (("solver-LOS", L), ("solver-NLOS", N)):
                b = g[key]
                cs = "n/a" if b["core_sigma_deg"] is None else f"{b['core_sigma_deg']:.2f}"
                md.append(f"| {lab}, {grp} | {g['links']} | {cs} | {b['P_gt_5deg']:.3f} | "
                          + " / ".join(f"{v:.2f}" for v in b["abs_q50_90_99"]) + f" | {assumed if grp == 'solver-LOS' else 'not trusted'} |")
    md += ["", "Sensitivity of the solver-LOS delay outlier rate to the first-arrival threshold:", "",
           "| threshold (dB below strongest) | P(TOA excess > 10 m) | strongest-or-first direct |", "|---|---|---|"]
    for t, v in thr_sens.items():
        md.append(f"| {t} | {f(v['P_toa_excess_gt_10m'])} | {f(v['first_arrival_is_direct'])} |")
    return "\n".join(md)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", nargs="+", default=None)
    ap.add_argument("--dir", default=str(Path(__file__).parent / "results"))
    a = ap.parse_args(argv)
    tables = a.table or sorted(str(p) for pat in ("block_*.npz", "midtown_*.npz")
                               for p in Path(a.dir, "rt").glob(pat))
    for tp in tables:
        T = PathTable.load(tp)
        scene, sites, _ = scene_and_sites(table_meta(T))
        owner = table_meta(T)["owner"]
        bf = beam_arrays(T)
        res = analyse(T, scene, owner, sites, 20.0, bf)
        sens = {f"{t} dB": analyse(T, scene, owner, sites, float(t))["solver_LOS"] for t in (10, 20, 30)}
        name = Path(tp).stem
        Path(a.dir, f"rt_calibration_{name}.json").write_text(json.dumps(dict(result=res, threshold_sensitivity=sens), indent=2))
        md = render(res, sens)
        Path(a.dir, f"rt_calibration_{name}.md").write_text(md)
        print(md, "\n")


if __name__ == "__main__":
    main()
