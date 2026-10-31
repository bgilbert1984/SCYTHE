"""Monte-Carlo placement objective for the *robust* solver (common random numbers).

The analytic objective assumes Gaussian noise, so it cannot score a layout under the
robust solver and impairments. This objective runs the real thing, made cheap by:

* One observation bank: for each fixed simulated emitter, the full observation vector is
  generated once as if EVERY candidate receiver were deployed (same truth-side generator
  as the benchmarks, including impairments). A layout is a subset of those columns, with
  the sub-covariance. Joint-Gaussian marginals and per-receiver independence of the
  impairments make this statistically equivalent to simulating that layout alone, and
  all layouts are compared on identical draws (common random numbers).
* Cached mean measurements per (building, receiver, kind) on the candidate grid.
* A coarser candidate grid than the held-out benchmarks (default 3 m vs 2 m).

Score = mean over design scenarios of the mean probability assigned to the true floor.
Design trials are in-sample: always confirm on fresh draws (benchmarks/placement_mc.py).
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

import numpy as np

from ..benchmarks.identifiability import simulate_observation
from ..localization.forward import mean_measurements
from ..measurements.impairments import ImpairmentModel
from ..measurements.models import EmitterClass, SensingParams
from ..propagation.model import PropagationParams
from ..uncertainty.floor_posterior import CandidateGrid
from ..uncertainty.robust_posterior import RobustConfig, robust_floor_posterior
from .sites import Candidate


class MCObjective:
    def __init__(self, scene, cands: Sequence[Candidate], emitter: EmitterClass,
                 scenarios: Dict[str, ImpairmentModel], solver: RobustConfig,
                 sensing: SensingParams | None = None, prop: PropagationParams | None = None,
                 trials_per_building: int = 24, xy_spacing_m: float = 3.0, seed: int = 101):
        self.scene, self.cands = scene, list(cands)
        self.solver, self.prop = solver, prop or PropagationParams()
        sensing = sensing or SensingParams()
        self.receivers = [c.receiver() for c in self.cands]
        self.grids = {b.name: CandidateGrid.for_building(b, xy_spacing_m) for b in scene.buildings}
        self.H: Dict[str, Dict[Tuple[int, str], np.ndarray]] = {}
        for b in scene.buildings:
            hb: Dict[Tuple[int, str], np.ndarray] = {}
            for i, r in enumerate(self.receivers):
                kinds = ["rss"] + (["az"] if r.horizontal_aperture_m > 0 else []) \
                    + (["el"] if r.vertical_aperture_m > 0 else []) \
                    + (["toa"] if r.sync_sigma_s is not None else [])
                for kd in kinds:
                    hb[(i, kd)] = mean_measurements(self.grids[b.name].points, self.receivers,
                                                    [(i, kd)], self.prop)[:, 0]
            self.H[b.name] = hb
        self.bank: Dict[str, List[dict]] = {}
        rng = np.random.default_rng(seed)
        for sname, imp in scenarios.items():
            trials = []
            for b in scene.buildings:
                for _ in range(trials_per_building):
                    ob = simulate_observation(scene, b, self.receivers, emitter, sensing,
                                              self.prop, rng, imp)
                    trials.append(dict(b=b.name, k=ob.k, cols=ob.cols, y=ob.y, cov=ob.cov,
                                       col_rx=np.array([i for i, _ in ob.cols])))
            self.bank[sname] = trials
        self._memo: Dict[Tuple[int, ...], dict] = {}

    # --- scoring -----------------------------------------------------------------------
    def evaluate(self, idx: Sequence[int]) -> dict:
        """Per-scenario mean p(true floor), top-1, top-3, cov90 for the layout (memoised)."""
        key = tuple(sorted(idx))
        if key in self._memo:
            return self._memo[key]
        chosen = np.array(key, int)
        out = {}
        for sname, trials in self.bank.items():
            p_t, t1, t3, cv = [], [], [], []
            for t in trials:
                sel = np.where(np.isin(t["col_rx"], chosen))[0]
                if sel.size == 0:
                    F = self.grids[t["b"]].n_floors
                    p_t.append(1.0 / F); t1.append(0.0); t3.append(0.0); cv.append(1.0)
                    continue
                cols = [t["cols"][j] for j in sel]
                H = np.stack([self.H[t["b"]][c] for c in cols], 1)
                p, _ = robust_floor_posterior(t["y"][sel], self.grids[t["b"]], self.receivers, cols,
                                              t["cov"][np.ix_(sel, sel)], self.prop, self.solver, H)
                order = np.argsort(-p)
                rank = int(np.where(order == t["k"])[0][0])
                size = int(np.searchsorted(np.cumsum(p[order]), 0.9) + 1)
                p_t.append(float(p[t["k"]])); t1.append(rank == 0); t3.append(rank < 3)
                cv.append(rank < size)
            out[sname] = dict(p_truth=float(np.mean(p_t)), top1=float(np.mean(t1)),
                              top3=float(np.mean(t3)), cov90=float(np.mean(cv)))
        out["_score"] = float(np.mean([v["p_truth"] for k, v in out.items()]))
        self._memo[key] = out
        return out

    def score(self, idx: Sequence[int]) -> float:
        return self.evaluate(idx)["_score"]

    def cost(self, idx: Sequence[int]) -> float:
        return float(sum(self.cands[i].cost for i in idx))
