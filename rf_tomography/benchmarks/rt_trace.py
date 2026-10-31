"""Trace emitters x candidate receiver sites with Sionna RT and save a PathTable.

    source ~/.venvs/sionna-rt/env.sh
    PYTHONPATH=. ~/.venvs/sionna-rt/bin/python -m rf_tomography.benchmarks.rt_trace \\
        --emitters-per-building 60 --facade concrete

Emitters follow the same truth distribution as the Gaussian-model experiments (uniform in the
footprint inset by 1 m, random floor, +-0.5 m height jitter). Receivers are every candidate
site of placement/sites.py. Output: benchmarks/results/rt/<name>.npz (git-ignored, regenerate).
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np

from ..geometry.scene import synthetic_manhattan_block
from ..placement.sites import candidate_sites
from ..propagation.sionna_scene import load_block, trace_paths


def sample_emitters(scene, per_building: int, seed: int):
    rng = np.random.default_rng(seed)
    pts, owner = [], []
    for b in scene.buildings:
        lo, hi = b.box
        for _ in range(per_building):
            k = int(rng.integers(b.n_floors))
            pts.append([rng.uniform(lo[0] + 1, hi[0] - 1), rng.uniform(lo[1] + 1, hi[1] - 1),
                        b.floor_centers_z()[k] + rng.uniform(-0.5, 0.5)])
            owner.append(b.name)
    return np.array(pts), owner


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--emitters-per-building", type=int, default=60)
    ap.add_argument("--frequency", type=float, default=915e6)
    ap.add_argument("--facade", default="concrete", choices=["concrete", "glass"])
    ap.add_argument("--samples", type=int, default=100_000)
    ap.add_argument("--keep-top", type=int, default=None,
                    help="keep only the strongest K paths per link (needed with --scattering)")
    ap.add_argument("--scattering", type=float, default=0.0,
                    help="diffuse fraction of reflected power (enables diffuse reflection if > 0)")
    ap.add_argument("--max-depth", type=int, default=3)
    ap.add_argument("--seed", type=int, default=21)
    ap.add_argument("--tx-batch", type=int, default=6)
    ap.add_argument("--name", default=None)
    ap.add_argument("--out", default=str(Path(__file__).parent / "results" / "rt"))
    a = ap.parse_args(argv)

    scene = synthetic_manhattan_block()
    sites = candidate_sites(scene)
    rx = np.array([s.pos for s in sites], float)
    tx, owner = sample_emitters(scene, a.emitters_per_building, a.seed)
    sc = load_block(scene, a.frequency, facade=a.facade, scattering=a.scattering)
    t0 = time.time()

    def prog(done, total):
        print(f"  traced {done}/{total} emitters, {time.time() - t0:.0f}s", flush=True)

    T = trace_paths(sc, tx, rx, tx_batch=a.tx_batch, samples_per_src=a.samples,
                    max_depth=a.max_depth, diffuse=a.scattering > 0,
                    keep_top=a.keep_top or (200 if a.scattering > 0 else None), seed=a.seed, progress=prog)
    T.meta.update(dict(facade=a.facade, scattering=a.scattering, owner=owner, sites=[s.name for s in sites],
                       seconds=round(time.time() - t0), seed=a.seed))
    name = a.name or (f"block_{int(a.frequency / 1e6)}MHz_{a.facade}"
                      + (f"_diffuse{int(round(a.scattering * 100))}" if a.scattering > 0 else ""))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    T.save(out / f"{name}.npz")
    print(f"saved {out / (name + '.npz')}  paths {T.power.shape}  {T.meta['seconds']} s")


if __name__ == "__main__":
    main()
