"""Phase-A identifiability baseline.

For every (receiver layout x emitter class x building) draw synthetic emitters at
random floors, simulate measurements under the stated noise model, compute the
exact floor posterior, and report ranking accuracy, calibration, and a verdict.

This is a best-case bound: the solver's model equals the generating model
(unless --mismatch is set) and LOS state is known. Failing here means no learned
model should be expected to succeed on the same data.

    .venv/bin/python -m rf_tomography.benchmarks.identifiability --trials 40
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import replace
from pathlib import Path

import numpy as np

from ..geometry.scene import Building, Scene, synthetic_manhattan_block
from ..localization.crlb import position_crlb
from ..localization.forward import (angular_columns, az_columns, mean_measurements,
                                    noise_covariance, plan_columns)
from ..measurements.impairments import (ImpairmentModel, classify_los,
                                        corrupt_measurements, perturb_scene)
from ..measurements.models import EMITTERS, EmitterClass, SensingParams
from ..propagation.model import PropagationParams, snr_db_at
from ..uncertainty.robust_posterior import RobustConfig, robust_floor_posterior
from ..uncertainty.floor_posterior import (CandidateGrid, credible_set_size,
                                           floor_posterior, in_credible_set)
from .layouts import layouts

XY_JITTER_M = 1.0   # truth xy jitter within a 2 m cell
Z_JITTER_M = 0.5    # truth z jitter around floor centre


def run_cell(scene: Scene, building: Building, receivers, emitter: EmitterClass,
             sensing: SensingParams, prop: PropagationParams, n_trials: int,
             rng: np.random.Generator, grid: CandidateGrid,
             imp: ImpairmentModel | None = None, solver: RobustConfig | None = None):
    imp = imp or ImpairmentModel()
    rows = []
    F = building.n_floors
    lo, hi = building.box
    for _ in range(n_trials):
        k = int(rng.integers(F))
        truth = np.array([rng.uniform(lo[0] + 1, hi[0] - 1), rng.uniform(lo[1] + 1, hi[1] - 1),
                          building.floor_centers_z()[k] + rng.uniform(-Z_JITTER_M, Z_JITTER_M)])
        world = perturb_scene(scene, imp.geometry_sigma_m, rng) if imp.geometry_sigma_m else scene
        world_los = [not world.segment_blocked(truth, r.pos, exclude=(building.name, r.host_building))
                     for r in receivers]
        # What the solver believes. With impairments off this is the truth and no RNG is used.
        los = classify_los(world_los, imp, rng) if imp.active else world_los
        dists = [float(np.linalg.norm(truth - np.asarray(r.pos))) for r in receivers]
        cols = plan_columns(receivers, los)
        cov = noise_covariance(cols, receivers, los, dists, emitter, sensing, prop)
        cov_world = (noise_covariance(cols, receivers, world_los, dists, emitter, sensing, prop)
                     if world_los != los else cov)
        mu = mean_measurements(truth, receivers, cols, prop)[0]
        y = mu + np.linalg.cholesky(cov_world) @ rng.standard_normal(len(cols))
        if sensing.unmodelled_range_bias_m or sensing.unmodelled_angle_bias_deg:
            rb = rng.normal(0, sensing.unmodelled_range_bias_m, len(receivers))
            ab = rng.normal(0, math.radians(sensing.unmodelled_angle_bias_deg), len(receivers))
            for j, (i, kind) in enumerate(cols):
                y[j] += rb[i] if kind == "toa" else ab[i] if kind in ("az", "el") else 0.0
        n_bad = n_out = 0
        bad_set: set = set()
        if imp.active:
            y, n_bad, n_out = corrupt_measurements(y, cols, world_los, imp, rng, bad_set)
        flags = {}
        if solver is None:
            p = floor_posterior(y, grid, receivers, cols, cov, prop)
        else:
            p, flags = robust_floor_posterior(y, grid, receivers, cols, cov, prop, solver)
        part = {i for i, kd in cols if kd != "rss"}
        flagged = {i for i, v in flags.items() if v > 0.5}
        order = np.argsort(-p)
        rank = int(np.where(order == k)[0][0])
        third = lambda idx: min(2, idx * 3 // F)
        band_p = np.bincount([third(i) for i in range(F)], weights=p, minlength=3)
        zs = math.sqrt(max(position_crlb(truth, receivers, cols, cov, prop)[2, 2], 0.0))
        rows.append(dict(top1=rank == 0, top3=rank < 3, p_truth=float(p[k]),
                         cov90=in_credible_set(p, k), set90=credible_set_size(p),
                         band=int(np.argmax(band_p)) == third(k),
                         crlb_sigma_z=zs, n_los=int(sum(world_los)),
                         n_cols=len(cols), conf=float(p.max()),
                         n_nlos_trusted=n_bad, n_outliers=n_out,
                         nlos_tp=len(flagged & bad_set), nlos_fp=len(flagged - bad_set),
                         nlos_bad=len(bad_set), nlos_clean=len(part - bad_set)))
    return rows


def aggregate(rows, n_floors_mean: float):
    f = lambda key: float(np.mean([r[key] for r in rows]))
    zs = [r["crlb_sigma_z"] for r in rows if np.isfinite(r["crlb_sigma_z"])]
    out = dict(n=len(rows), top1=f("top1"), top3=f("top3"), band_acc=f("band"),
               mean_p_truth=f("p_truth"), cov90=f("cov90"), mean_set90=f("set90"),
               median_crlb_sigma_z_m=float(np.median(zs)) if zs else None,
               mean_los_receivers=f("n_los"), mean_confidence=f("conf"),
               mean_nlos_trusted=f("n_nlos_trusted"), mean_outlier_cols=f("n_outliers"),
               random_top1=1.0 / n_floors_mean, random_top3=min(1.0, 3.0 / n_floors_mean))
    sm = lambda key: float(sum(r[key] for r in rows))
    out["nlos_recall"] = sm("nlos_tp") / sm("nlos_bad") if sm("nlos_bad") else None
    out["nlos_false_flag_rate"] = sm("nlos_fp") / sm("nlos_clean") if sm("nlos_clean") else None
    t1, t3, bd = out["top1"], out["top3"], out["band_acc"]
    out["verdict"] = ("floor_resolved" if t1 >= 0.70 else
                      "floor_ranked_top3" if t3 >= 0.90 else
                      "band_only" if bd >= 0.90 else "unidentifiable")
    return out


def run(trials: int, seed: int, sensing: SensingParams, scene: Scene | None = None):
    scene = scene or synthetic_manhattan_block()
    prop = PropagationParams()
    grids = {b.name: CandidateGrid.for_building(b) for b in scene.buildings}
    results = []
    for li, (lname, rx) in enumerate(layouts(scene).items()):
        for ei, em in enumerate(EMITTERS):
            rng = np.random.default_rng([seed, li, ei])
            rows = []
            for b in scene.buildings:
                rows += run_cell(scene, b, rx, em, sensing, prop, trials, rng, grids[b.name])
            nf = float(np.mean([b.n_floors for b in scene.buildings]))
            results.append(dict(layout=lname, emitter=em.name, **aggregate(rows, nf)))
    return results


def to_markdown(results, title: str) -> str:
    h = ("| layout | emitter | top-1 | top-3 | band | p(truth) | 90% set | cov90 | CRLB σz m | verdict |\n"
         "|---|---|---|---|---|---|---|---|---|---|\n")
    body = "".join(
        f"| {r['layout']} | {r['emitter']} | {r['top1']:.2f} | {r['top3']:.2f} | {r['band_acc']:.2f} | "
        f"{r['mean_p_truth']:.2f} | {r['mean_set90']:.1f} | {r['cov90']:.2f} | "
        f"{r['median_crlb_sigma_z_m']:.1f} | {r['verdict']} |\n"
        if r["median_crlb_sigma_z_m"] is not None else
        f"| {r['layout']} | {r['emitter']} | {r['top1']:.2f} | {r['top3']:.2f} | {r['band_acc']:.2f} | "
        f"{r['mean_p_truth']:.2f} | {r['mean_set90']:.1f} | {r['cov90']:.2f} | ∞ | {r['verdict']} |\n"
        for r in results)
    return f"### {title}\n\n{h}{body}"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=40, help="trials per building per cell")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--mismatch-range-m", type=float, default=3.0)
    ap.add_argument("--mismatch-angle-deg", type=float, default=3.0)
    ap.add_argument("--out", default=str(Path(__file__).parent / "results"))
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    nominal = SensingParams()
    mism = replace(nominal, unmodelled_range_bias_m=a.mismatch_range_m,
                   unmodelled_angle_bias_deg=a.mismatch_angle_deg)
    res = {"nominal": run(a.trials, a.seed, nominal), "mismatch": run(a.trials, a.seed, mism)}
    meta = dict(seed=a.seed, trials_per_building=a.trials, sensing=nominal.__dict__,
                mismatch=dict(range_m=a.mismatch_range_m, angle_deg=a.mismatch_angle_deg),
                note="SYNTHETIC best-case bound; not measured evidence")
    (out / "identifiability_baseline.json").write_text(json.dumps(dict(meta=meta, **res), indent=2))
    md = (to_markdown(res["nominal"], "Nominal (solver model == world)") + "\n"
          + to_markdown(res["mismatch"],
                        f"Mismatch (unmodelled {a.mismatch_range_m} m range bias, "
                        f"{a.mismatch_angle_deg}° angle bias)"))
    (out / "identifiability_baseline.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
