"""Do the solvers still work on ray-traced observations?

    .venv/bin/python -m rf_tomography.benchmarks.rt_eval

Observations come from a Sionna path table, not from the Gaussian/impairment generators:
RSS = total path power, TOA = first arrival (within 20 dB of the strongest path), AoA =
strongest path, for every candidate receiver site. A measurement-estimation noise (array
CRB, timing CRLB + 10 ns sync, 0.5 dB RSS) and the two nuisance offsets (Tx power, emitter
clock) are added on top. The solver is unchanged and knows nothing about the tracer: it
assumes the log-distance RSS model, direct-path AoA/TOA, its own multipath floors, and
decides LOS from the same geometric box test as before.

Caveat: the tracer has no diffuse scattering or clutter (see rt_calibration.py), so direct
paths are exact. Results are a first physical cross-check, not a field result.
"""
from __future__ import annotations

import argparse
import ast
import json
from dataclasses import replace
from pathlib import Path

import numpy as np

from ..geometry.scene import synthetic_manhattan_block
from ..localization.forward import column_sigma, noise_covariance, plan_columns
from ..measurements.models import EMITTERS, SensingParams
from ..measurements.raytraced import PathTable, beamformed_aoa, coherent_aoa, link_observables
from ..placement.objective import PlacementObjective, make_probes
from ..placement.optimizer import optimise, random_layouts
from ..placement.sites import all_candidates
from .scenes import candidates_from, grid_spacing, scene_and_sites, spread_street_arrays, table_meta
from ..propagation.model import PropagationParams
from ..uncertainty.floor_posterior import CandidateGrid, credible_set_size, floor_posterior, in_credible_set
from ..uncertainty.robust_posterior import RobustConfig, robust_floor_posterior

SOLVERS = {"gaussian": None, "robust": RobustConfig("robust"),
           "robust_paranoid": RobustConfig("robust_paranoid", eps=0.40, pi_nlos=0.40)}
EST = SensingParams(multipath_range_sigma_m=0.0, multipath_angle_sigma_deg=0.0)  # estimation noise only
RSS_EST_DB = 0.5


def make_layouts(scene, cands, em, prop, seed, hosts=None):
    """analytic@14/20 (Gaussian CRLB objective), a random median layout, and 5 street arrays.
    `hosts`: buildings that contain emitters (probes are placed there; default all)."""
    an = PlacementObjective(scene, cands, make_probes(scene, buildings=hosts), em, SensingParams(), prop)
    label = {c.label: i for i, c in enumerate(cands)}
    out = {f"analytic@{B}": optimise(an, B) for B in (14, 20)}
    rnd = random_layouts(an, 14, 200, seed)
    sc = [an.score(r) for r in rnd]
    out["random_median@14"] = rnd[int(np.argsort(sc)[len(sc) // 2])]
    legacy = [f"{n}:array" for n in ("Sx0", "Sx80", "Sx-80", "Sy80", "Sy-80")]
    out["street_arrays(5)"] = ([label[n] for n in legacy] if all(n in label for n in legacy)
                               else spread_street_arrays(cands, 5))
    return out


def run(T, scene, owner, em, prop, cands, layouts, seed, solvers, aoa="strongest", stride=1,
        grid_spacing_adapt=False, paired_noise=False, return_trials=False, rss_mode="model", snaps=1):
    """aoa: 'strongest' = AoA of the strongest path (idealised); 'beamformed' = peak of the
    aperture's power-weighted beam response (1 m / 3 m horizontal, 3 m vertical tiers);
    'oracle' = the geometrically exact direct-path bearings and range (a diagnostic: it removes
    the angle and delay multipath errors but keeps RSS, geometry and solver). With
    return_trials=True the per-emitter records are returned too. rss_mode: 'model' = ray-traced
    total power (default); 'drop' = no RSS columns at all; 'oracle' = the solver's own
    log-distance model evaluated at the exact range (removes RSS model mismatch). 'coherent' = the
    same apertures as 'beamformed' but from the complex sum of the paths with random phases
    (measurements.raytraced.coherent_aoa), averaged over `snaps` independent fading snapshots
    (None = the infinite-snapshot limit)."""
    o = link_observables(T, 20.0)
    orig = table_meta(T).get("emitter_index") or list(range(len(T.tx)))   # emitter ids in the sampled set
    bf = ({"el": beamformed_aoa(T, 3.0, "el"), 1.0: beamformed_aoa(T, 1.0, "az"),
           3.0: beamformed_aoa(T, 3.0, "az")} if aoa == "beamformed" else
          {"el": coherent_aoa(T, 3.0, "el", snaps, seed, orig), 1.0: coherent_aoa(T, 1.0, "az", snaps, seed, orig),
           3.0: coherent_aoa(T, 3.0, "az", snaps, seed, orig)} if aoa == "coherent" else None)
    site_idx = {s: j for j, s in enumerate(ast.literal_eval(T.meta["repr"])["sites"])}
    grids = {n: CandidateGrid.for_building(scene.by_name(n), grid_spacing(scene.by_name(n)) if grid_spacing_adapt else 2.0)
             for n in set(owner)}
    rng = np.random.default_rng(seed)
    rows = {(l, s): [] for l in layouts for s in solvers}
    KC = {"rss": 0, "toa": 1, "az": 2, "el": 3}
    for e in range(0, len(T.tx), stride):
        b = scene.by_name(owner[e])
        k = int(T.tx[e, 2] // b.floor_height)
        if paired_noise:   # noise is a pure function of (seed, emitter, link, kind): identical across tables
            re_ = np.random.default_rng([seed, orig[e]])
            p0, t0 = re_.normal(0, 10.0), re_.normal(0, 200.0)
            nz = lambda jj, kind, sd: np.random.default_rng([seed, orig[e], jj, KC[kind]]).normal(0, sd)
        else:
            p0, t0 = rng.normal(0, 10.0), rng.normal(0, 200.0)   # nuisance offsets, shared by all receivers
            nz = lambda jj, kind, sd: rng.normal(0, sd)
        for lname, idx in layouts.items():
            rx = [cands[i].receiver() for i in idx]
            j = [site_idx[cands[i].site.name] for i in idx]
            los = [not scene.segment_blocked(T.tx[e], np.array(r.pos), exclude=(owner[e], r.host_building)) for r in rx]
            alive = [bool(o.n_paths[e, jj] > 0) for jj in j]
            cols = [c for c in plan_columns(rx, los) if alive[c[0]] and not (rss_mode == "drop" and c[1] == "rss")]
            if not cols:
                continue
            dist = [float(o.dist_m[e, jj]) for jj in j]
            y = np.empty(len(cols))
            for q, (i, kind) in enumerate(cols):
                jj = j[i]
                if kind == "rss":
                    rss0 = (-10.0 * prop.path_loss_exp * np.log10(o.dist_m[e, jj])) if rss_mode == "oracle" else o.rss_db[e, jj]
                    y[q] = rss0 + p0 + nz(jj, "rss", RSS_EST_DB)
                elif kind == "toa":
                    toa = o.dist_m[e, jj] if aoa == "oracle" else o.toa_m[e, jj]
                    y[q] = toa + t0 + nz(jj, "toa", column_sigma("toa", rx[i], True, dist[i], em, EST, prop))
                else:
                    if aoa == "oracle":
                        base = o.az_geo[e, jj] if kind == "az" else o.el_geo[e, jj]
                    elif bf is None:
                        base = o.az[e, jj] if kind == "az" else o.el[e, jj]
                    else:
                        base = bf[rx[i].horizontal_aperture_m][e, jj] if kind == "az" else bf["el"][e, jj]
                    y[q] = base + nz(jj, kind, column_sigma(kind, rx[i], True, dist[i], em, EST, prop))
            cov = noise_covariance(cols, rx, los, dist, em, SensingParams(), prop)
            for sname, cfg in solvers.items():
                if cfg is None:
                    pr = floor_posterior(y, grids[b.name], rx, cols, cov, prop)
                else:
                    pr, _ = robust_floor_posterior(y, grids[b.name], rx, cols, cov, prop, cfg)
                order = np.argsort(-pr)
                rank = int(np.where(order == k)[0][0])
                used = [j[i] for i, _ in {(c[0], 0) for c in cols}]
                rec = dict(top1=rank == 0, top3=rank < 3, p=float(pr[k]),
                           cov=in_credible_set(pr, k), size=credible_set_size(pr),
                           err=float(pr @ np.arange(len(pr))) - k)
                if return_trials:
                    los_used = [j[i] for i in {c[0] for c in cols if c[1] != "rss"}]
                    rec.update(e=int(e), owner=owner[e], k=k, nf=len(pr), mode_err=int(order[0]) - k,
                               frac_direct=float(np.mean([o.strongest_direct[e, jj] for jj in los_used])) if los_used else float("nan"),
                               mean_dist=float(np.mean([o.dist_m[e, jj] for jj in used])),
                               n_los=len(los_used), z=float(T.tx[e, 2]))
                rows[(lname, sname)].append(rec)
    if return_trials:
        return rows
    out = []
    for (lname, sname), r in rows.items():
        m = lambda key: float(np.mean([x[key] for x in r]))
        out.append(dict(layout=lname, solver=sname, n=len(r), top1=m("top1"), top3=m("top3"), cov90=m("cov"),
                        set90=m("size"), p_truth=m("p"), floor_bias=m("err"),
                        floor_mae=float(np.mean([abs(x["err"]) for x in r]))))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", nargs="+", default=None)
    ap.add_argument("--emitter", default="lora_915M_200k")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--aoa", nargs="+", default=None, choices=["strongest", "beamformed"])
    ap.add_argument("--solvers", nargs="+", default=list(SOLVERS))
    ap.add_argument("--emitter-stride", type=int, default=1, help="use every n-th emitter")
    ap.add_argument("--out-name", default="rt_eval")
    ap.add_argument("--paired-noise", action="store_true",
                    help="deterministic per-link noise keyed on the original emitter id (tables become paired)")
    ap.add_argument("--dir", default=str(Path(__file__).parent / "results"))
    a = ap.parse_args(argv)
    prop = PropagationParams()
    em = next(e for e in EMITTERS if e.name == a.emitter)
    tables = a.tables or sorted(str(p) for p in Path(a.dir, "rt").glob("block_*.npz"))
    solvers = {k: v for k, v in SOLVERS.items() if k in a.solvers}
    md, js, cache = [], {}, {}
    for tp in tables:
        T = PathTable.load(tp)
        meta = table_meta(T)
        scene, sites, hosts = scene_and_sites(meta)
        real = meta.get("scene") == "midtown"
        key = "midtown" if real else "block"
        if key not in cache:
            cands = candidates_from(sites) if real else all_candidates(scene)
            cache[key] = (cands, make_layouts(scene, cands, em, prop, a.seed, hosts if real else None))
        cands, layouts = cache[key]
        for aoa in (a.aoa or (("beamformed",) if real else ("strongest", "beamformed"))):
            res = run(T, scene, meta["owner"], em, prop, cands, layouts, a.seed, solvers, aoa,
                      a.emitter_stride, real, a.paired_noise)
            js[f"{Path(tp).stem}|{aoa}"] = res
            md += [f"### {Path(tp).stem}: {meta['facade']} facades, scattering {meta.get('scattering', 0.0)}, "
                   f"{len(T.tx)} ray-traced emitters, AoA = {aoa}\n",
                   "| layout | solver | top-1 | top-3 | cov90 | 90% set | p(truth) | floor bias | floor MAE |",
                   "|---|---|---|---|---|---|---|---|---|"]
            for r in res:
                md.append(f"| {r['layout']} | {r['solver']} | {r['top1']:.2f} | {r['top3']:.2f} | {r['cov90']:.2f} | "
                          f"{r['set90']:.1f} | {r['p_truth']:.2f} | {r['floor_bias']:+.2f} | {r['floor_mae']:.2f} |")
            md.append("")
    Path(a.dir, f"{a.out_name}.json").write_text(json.dumps(js, indent=2))
    Path(a.dir, f"{a.out_name}.md").write_text("\n".join(md))
    print("\n".join(md))


if __name__ == "__main__":
    main()
