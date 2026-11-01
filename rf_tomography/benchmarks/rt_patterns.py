"""Ribbon vs punched windows at matched glazed area, on identical emitters and sites.

    OMP_NUM_THREADS=1 .venv/bin/python -m rf_tomography.benchmarks.rt_patterns

Tables (all the same 240 emitters, 54 sites, footprints, styles, materials; only the facade changes):
solid concrete and solid glass (references, every other emitter of the 480-emitter tables) and the
mixed-style facade with a continuous ribbon / regular grid / staggered / randomised punched windows.
Reports link statistics, path composition, the spandrel/glass mechanism test, and solver
performance, with paired emitter-level bootstrap intervals (links within an emitter are correlated,
so link-level intervals would be far too tight). Noise is a pure function of (emitter, link, kind),
so solver differences are paired too.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

import numpy as np

from ..measurements.models import EMITTERS
from ..measurements.raytraced import (COMPOSITION_FIELDS, DC_CONCRETE, DC_GLASS, PC_NAMES, PathTable,
                                      direct_crossing_class, link_observables, strongest_path_class)
from ..propagation.model import PropagationParams
from .rt_calibration import beam_arrays, deg, solver_los, wrap
from .rt_eval import SOLVERS, make_layouts, run
from .scenes import candidates_from, scene_and_sites, table_meta

REF = "mixed ribbon"
LAYOUTS = ("analytic@20", "analytic@14", "street_arrays(5)")


def subset(T: PathTable, idx) -> PathTable:
    idx = np.asarray(idx)
    meta = table_meta(T)
    meta["owner"] = [meta["owner"][i] for i in idx]
    prev = meta.get("emitter_index") or list(range(len(T.tx)))
    meta["emitter_index"] = [int(prev[i]) for i in idx]
    kw = dict(total_power=None if T.total_power is None else T.total_power[idx])
    for f in COMPOSITION_FIELDS:
        v = getattr(T, f)
        kw[f] = None if v is None else v[idx]
    return PathTable(T.frequency_hz, T.tx[idx], T.rx, T.power[idx], T.tau_s[idx], T.theta_r[idx], T.phi_r[idx],
                     T.ctype[idx], T.depth[idx], {"repr": repr(meta)}, **kw)


def boot_ratio(num, den, idx):
    return float(num[idx].sum() / max(den[idx].sum(), 1e-12))


def link_stats(T, scene, owner, sites, beams):
    """Per-emitter numerators/denominators for rates over solver-LOS links."""
    o = link_observables(T, 20.0)
    valid = o.n_paths > 0
    los = solver_los(scene, T, owner, sites) & valid
    d_el, d_az = np.abs(deg(o.el - o.el_geo)), np.abs(deg(wrap(o.az - o.az_geo)))
    b_el = np.abs(deg(beams["el3"] - o.el_geo))
    cnt = lambda m: m.sum(1).astype(float)
    out = {"los_links": cnt(los), "valid_links": cnt(valid), "all_links": np.full(len(T.tx), float(T.power.shape[1])),
           "strongest_direct": cnt(los & o.strongest_direct), "el_gt5": cnt(los & (d_el > 5)),
           "az_gt5": cnt(los & (d_az > 5)), "beam_el_gt5": cnt(los & (b_el > 5))}
    comp = {}
    if T.best_types is not None:
        pc, dc = strongest_path_class(T), direct_crossing_class(T)
        for k, name in enumerate(PC_NAMES):
            comp[name] = cnt(los & (pc == k))
        has_dir = los & (T.dir_power > 0)
        for lab, code in (("glass", DC_GLASS), ("concrete", DC_CONCRETE)):
            m = has_dir & (dc == code)
            comp[f"direct crosses {lab}: links"] = cnt(m)
            comp[f"direct crosses {lab}: strongest not direct"] = cnt(m & ~o.strongest_direct)
        deficit = np.where(has_dir & ~o.strongest_direct, 10 * np.log10(np.maximum(T.power.max(-1), 1e-30) / np.maximum(T.dir_power, 1e-30)), np.nan)
        comp["_deficit_db"] = deficit
    return out, comp


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--boot", type=int, default=1000)
    ap.add_argument("--aoa", default="beamformed", choices=["beamformed", "strongest", "oracle", "coherent"])
    ap.add_argument("--snaps", type=int, default=1, help="coherent AoA: independent fading snapshots averaged (0 = infinite)")
    ap.add_argument("--no-links", action="store_true", help="skip link statistics / beam arrays (solver part only)")
    ap.add_argument("--layouts", nargs="+", default=list(LAYOUTS))
    ap.add_argument("--out-name", default="rt_patterns_midtown")
    ap.add_argument("--dir", default=str(Path(__file__).parent / "results"))
    a = ap.parse_args(argv)
    R = Path(a.dir, "rt")
    spec = {"solid concrete": ("midtown_915MHz_concrete.npz", True), "solid glass": ("midtown_915MHz_glass_solid.npz", True),
            REF: ("midtown_915MHz_mixed_ribbon_s2.npz", False), "mixed grid": ("midtown_915MHz_mixed_grid_s2.npz", False),
            "mixed staggered": ("midtown_915MHz_mixed_staggered_s2.npz", False), "mixed random": ("midtown_915MHz_mixed_random_s2.npz", False)}
    em, prop = EMITTERS[1], PropagationParams()
    tabs, scenes = {}, {}
    for lab, (fn, thin) in spec.items():
        T = PathTable.load(R / fn)
        if thin:
            T = subset(T, np.arange(0, len(T.tx), 2))
        tabs[lab] = T
        scenes[lab] = scene_and_sites(table_meta(T))
    n = len(tabs[REF].tx)
    assert all(len(T.tx) == n for T in tabs.values()), "tables must share the same emitters"
    assert all(np.allclose(T.tx, tabs[REF].tx) for T in tabs.values()), "emitter positions differ"
    sc0, sites0, hosts0 = scenes[REF]
    cands = candidates_from(sites0)
    lay = make_layouts(sc0, cands, em, prop, a.seed, hosts0)
    layouts = {k: lay[k] for k in a.layouts}
    rng = np.random.default_rng(0)
    boots = [rng.integers(0, n, n) for _ in range(a.boot)]
    stats, comps, trials = {}, {}, {}
    for lab, T in tabs.items():
        sc, sites, _ = scenes[lab]
        if not a.no_links:
            beams = beam_arrays(T)
            stats[lab], comps[lab] = link_stats(T, sc, table_meta(T)["owner"], sites, beams)
        trials[lab] = run(T, sc, table_meta(T)["owner"], em, prop, cands, layouts, a.seed,
                          {k: SOLVERS[k] for k in ("gaussian", "robust")}, a.aoa, 1, True, True, True,
                          snaps=a.snaps or None)
        print("done", lab, flush=True)

    def ci(vals_fn):
        v = np.array([vals_fn(b) for b in boots])
        return float(np.mean(v)), float(np.quantile(v, 0.025)), float(np.quantile(v, 0.975))

    md, js = [], {}
    if not a.no_links:
        md, js = ["### Link statistics (solver-LOS links; mean [95% paired-bootstrap CI over emitters])\n",
                  "| facade | usable links / all | strongest direct | P(el err > 5 deg) | P(az err > 5 deg) | P(beam el err > 5 deg) |", "|---|---|---|---|---|---|"], {}
        for lab, st in stats.items():
            f = lambda num: ci(lambda b: boot_ratio(st[num], st["los_links"], b))
            sd, e5, a5, be5 = f("strongest_direct"), f("el_gt5"), f("az_gt5"), f("beam_el_gt5")
            usable = ci(lambda b: boot_ratio(st["valid_links"], st["all_links"], b))
            js[lab] = dict(strongest_direct=sd, el_gt5=e5, az_gt5=a5, beam_el_gt5=be5, usable=usable, los_links=int(st["los_links"].sum()))
            c = lambda t: f"{t[0]:.3f} [{t[1]:.3f}, {t[2]:.3f}]"
            md.append(f"| {lab} | {usable[0]:.2f} | {c(sd)} | {c(e5)} | {c(a5)} | {c(be5)} |")
        md += ["", "Paired difference vs `%s` (same emitters; 95%% bootstrap CI):" % REF, "",
               "| facade | d strongest direct | d P(el err > 5) | d P(az err > 5) |", "|---|---|---|---|"]
        for lab, st in stats.items():
            if lab == REF:
                continue
            r = stats[REF]
            row = []
            for k in ("strongest_direct", "el_gt5", "az_gt5"):
                m, lo, hi = ci(lambda b: boot_ratio(st[k], st["los_links"], b) - boot_ratio(r[k], r["los_links"], b))
                row.append(f"{m:+.3f} [{lo:+.3f}, {hi:+.3f}]")
            md.append(f"| {lab} | " + " | ".join(row) + " |")
        md += ["", "### Strongest-path composition (solver-LOS links, share of links)\n", "| path class | " + " | ".join(l for l in comps if comps[l]) + " |",
               "|---|" + "---|" * sum(1 for l in comps if comps[l])]
        for name in PC_NAMES:
            vals = []
            for lab, c in comps.items():
                if c:
                    vals.append(f"{c[name].sum() / stats[lab]['los_links'].sum():.3f}")
            if any(float(v) > 0 for v in vals):
                md.append(f"| {name} | " + " | ".join(vals) + " |")
        md += ["", "### Mechanism test: does the strongest path avoid the direct one when the direct path crosses concrete?\n",
               "P(strongest path is NOT the direct-like path | what the strongest direct-like path crosses), solver-LOS links:\n",
               "| facade | direct crosses glass only | direct crosses concrete | median power deficit of direct vs strongest, when not strongest (dB) |", "|---|---|---|---|"]
        for lab, c in comps.items():
            if not c:
                continue
            g = ci(lambda b: boot_ratio(c["direct crosses glass: strongest not direct"], c["direct crosses glass: links"], b))
            k = ci(lambda b: boot_ratio(c["direct crosses concrete: strongest not direct"], c["direct crosses concrete: links"], b))
            dfc = c["_deficit_db"]
            md.append(f"| {lab} | {g[0]:.3f} [{g[1]:.3f}, {g[2]:.3f}] (n={int(c['direct crosses glass: links'].sum())}) | "
                      f"{k[0]:.3f} [{k[1]:.3f}, {k[2]:.3f}] (n={int(c['direct crosses concrete: links'].sum())}) | {np.nanmedian(dfc):.1f} |")
            js[lab]["mechanism"] = dict(glass=g, concrete=k)
    md += ["", f"### Solver performance (240 emitters, AoA = {a.aoa}{f' x{a.snaps} snapshots' if a.aoa == 'coherent' else ''}, default beliefs, paired noise)\n",
           "Differences are vs `%s`, same emitters (95%% bootstrap CI over emitters).\n" % REF,
           "| layout | solver | facade | top-1 | top-3 | cov90 | set | d top-1 vs ribbon | d p(truth) vs ribbon |", "|---|---|---|---|---|---|---|---|---|"]
    for lay_name in layouts:
        for sv in ("robust", "gaussian"):
            ref = trials[REF][(lay_name, sv)]
            pe = {r["e"]: r for r in ref}
            for lab in tabs:
                rows = trials[lab][(lay_name, sv)]
                m = lambda key: float(np.mean([r[key] for r in rows]))
                es = sorted(pe)
                t1 = np.array([float(next(r for r in rows if r["e"] == e)["top1"]) - float(pe[e]["top1"]) for e in es]) if lab != REF else None
                pp = np.array([next(r for r in rows if r["e"] == e)["p"] - pe[e]["p"] for e in es]) if lab != REF else None
                d1 = dp = "-"
                if lab != REF:
                    bt = np.array([t1[b % len(es)].mean() for b in boots]); bp = np.array([pp[b % len(es)].mean() for b in boots])
                    d1 = f"{t1.mean():+.3f} [{np.quantile(bt, .025):+.3f}, {np.quantile(bt, .975):+.3f}]"
                    dp = f"{pp.mean():+.3f} [{np.quantile(bp, .025):+.3f}, {np.quantile(bp, .975):+.3f}]"
                md.append(f"| {lay_name} | {sv} | {lab} | {m('top1'):.2f} | {m('top3'):.2f} | {m('cov'):.2f} | {m('size'):.1f} | {d1} | {dp} |")
                js.setdefault(lab, {}).setdefault("solver", {})[f"{lay_name}|{sv}"] = dict(
                    top1=m("top1"), top3=m("top3"), cov90=m("cov"), set90=m("size"), bias=m("err"))
    Path(a.dir, f"{a.out_name}.json").write_text(json.dumps(js, indent=2, default=float))
    Path(a.dir, f"{a.out_name}.md").write_text("\n".join(md))
    print("\n".join(md))


if __name__ == "__main__":
    main()
