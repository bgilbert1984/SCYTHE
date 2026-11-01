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

from ..geometry.mesh import with_pattern
from ..geometry.real import midtown_scene, real_sites, target_buildings
from ..geometry.scene import synthetic_manhattan_block
from ..placement.sites import candidate_sites
from ..propagation.sionna_scene import load_block, trace_paths


def sample_emitters(scene, per_building: int, seed: int, hosts=None):
    """Emitters in `hosts` (default: every building), uniform in the footprint inset by 1 m,
    random floor, +-0.5 m height jitter."""
    rng = np.random.default_rng(seed)
    pts, owner = [], []
    for b in (hosts if hosts is not None else scene.buildings):
        for _ in range(per_building):
            k = int(rng.integers(b.n_floors))
            x, y = b.sample_xy(rng, 1.0)
            pts.append([x, y, b.floor_centers_z()[k] + rng.uniform(-0.5, 0.5)])
            owner.append(b.name)
    return np.array(pts), owner


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", default="synthetic", choices=["synthetic", "midtown"])
    ap.add_argument("--policy", default="mixed", choices=["mixed", "concrete", "glass_solid"],
                    help="midtown facade policy (windows are meshed per floor for 'mixed')")
    ap.add_argument("--pattern", default="ribbon", choices=["ribbon", "grid", "staggered", "random"],
                    help="window pattern for glazed styles (same glazed area for all)")
    ap.add_argument("--emitter-stride", type=int, default=1,
                    help="trace every n-th emitter of the sampled set (original indices are kept in the metadata)")
    ap.add_argument("--emitters-per-building", type=int, default=60)
    ap.add_argument("--frequency", type=float, default=915e6)
    ap.add_argument("--facade", default="concrete", choices=["concrete", "glass"])
    ap.add_argument("--samples", type=int, default=100_000)
    ap.add_argument("--keep-top", type=int, default=None,
                    help="keep only the strongest K paths per link (needed with --scattering)")
    ap.add_argument("--scattering", type=float, default=0.0,
                    help="diffuse fraction of reflected power (enables diffuse reflection if > 0)")
    ap.add_argument("--max-depth", type=int, default=3)
    ap.add_argument("--no-diffraction", action="store_true")
    ap.add_argument("--seed", type=int, default=21)
    ap.add_argument("--tx-batch", type=int, default=6)
    ap.add_argument("--name", default=None)
    ap.add_argument("--out", default=str(Path(__file__).parent / "results" / "rt"))
    a = ap.parse_args(argv)

    if a.scene == "midtown":
        scene = with_pattern(midtown_scene(a.policy), a.pattern)
        sites = real_sites(scene)
        tx, owner = sample_emitters(scene, a.emitters_per_building, a.seed, target_buildings(scene))
        sc = load_block(scene, a.frequency, scattering=a.scattering)
    else:
        scene = synthetic_manhattan_block()
        sites = candidate_sites(scene)
        tx, owner = sample_emitters(scene, a.emitters_per_building, a.seed)
        sc = load_block(scene, a.frequency, facade=a.facade, scattering=a.scattering)
    keep = np.arange(len(tx))[::a.emitter_stride]
    tx, owner = tx[keep], [owner[i] for i in keep]
    rx = np.array([s.pos for s in sites], float)
    t0 = time.time()

    def prog(done, total):
        print(f"  traced {done}/{total} emitters, {time.time() - t0:.0f}s", flush=True)

    T = trace_paths(sc, tx, rx, tx_batch=a.tx_batch, samples_per_src=a.samples,
                    max_depth=a.max_depth, diffraction=not a.no_diffraction, diffuse=a.scattering > 0,
                    keep_top=a.keep_top or (200 if a.scattering > 0 else None), seed=a.seed, progress=prog)
    T.meta.update(dict(scene=a.scene, policy=a.policy if a.scene == 'midtown' else None,
                       pattern=a.pattern if a.scene == 'midtown' else None, emitter_index=[int(i) for i in keep],
                       facade=a.facade if a.scene == 'synthetic' else a.policy, scattering=a.scattering, owner=owner, sites=[s.name for s in sites],
                       seconds=round(time.time() - t0), seed=a.seed))
    stem = "midtown" if a.scene == "midtown" else "block"
    suffix = ((f"_{a.pattern}" if a.scene == "midtown" and a.policy == "mixed" else "")
              + (f"_s{a.emitter_stride}" if a.emitter_stride > 1 else ""))
    name = a.name or (f"{stem}_{int(a.frequency / 1e6)}MHz_{a.policy if a.scene == 'midtown' else a.facade}{suffix}"
                      + (f"_diffuse{int(round(a.scattering * 100))}" if a.scattering > 0 else ""))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    T.save(out / f"{name}.npz")
    print(f"saved {out / (name + '.npz')}  paths {T.power.shape}  {T.meta['seconds']} s")


if __name__ == "__main__":
    main()
