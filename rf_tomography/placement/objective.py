"""Fast analytic placement objective.

For each (candidate, probe emitter) we precompute that receiver's Fisher
contribution. Contributions add across receivers, except for the two nuisance
offsets (unknown Tx power for RSS, unknown emitter clock for TOA), which couple
receivers through a rank-one covariance term and are handled with the Woodbury
identity:  F_block = A - b b^T / (1/s^2 + S),  A=sum w J^T J, b=sum w J, S=sum w.

Score per probe: expected floor-correct probability under a Gaussian error model,
    P = erf( floor_height / (2*sqrt(2)*sigma_z) ),
sigma_z from the 3x3 position CRLB (x, y, z all unknown). Objective = mean over probes.

This is a *local, unimodal* approximation. It is validated against the Monte-Carlo
grid posterior in benchmarks/placement.py rather than trusted.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import erf, sqrt
from typing import Dict, List, Sequence

import numpy as np

from ..geometry.scene import Scene
from ..localization.forward import CLOCK_OFFSET_SIGMA_M, analytic_jacobian, column_sigma
from ..measurements.models import EmitterClass, SensingParams
from ..propagation.model import PropagationParams
from .sites import Candidate

_ERF = np.vectorize(erf)


@dataclass(frozen=True)
class Probe:
    building: str
    floor: int
    pos: np.ndarray


def make_probes(scene: Scene, floors_per_building: int = 8, xy_per_floor: int = 3,
                seed: int = 11) -> List[Probe]:
    rng = np.random.default_rng(seed)
    probes = []
    for b in scene.buildings:
        floors = np.unique(np.round(np.linspace(1, b.n_floors, floors_per_building)).astype(int))
        lo, hi = b.box
        for f in floors:
            for _ in range(xy_per_floor):
                probes.append(Probe(b.name, int(f), np.array(
                    [rng.uniform(lo[0] + 3, hi[0] - 3), rng.uniform(lo[1] + 3, hi[1] - 3),
                     b.floor_center_z(int(f))])))
    return probes


class PlacementObjective:
    def __init__(self, scene: Scene, candidates: Sequence[Candidate], probes: Sequence[Probe],
                 emitter: EmitterClass, sensing: SensingParams, prop: PropagationParams,
                 robust: bool = False):
        self.cands, self.probes, self.prop = list(candidates), list(probes), prop
        self.fh = scene.buildings[0].floor_height
        C, P = len(self.cands), len(self.probes)
        self.A = {k: np.zeros((C, P, 3, 3)) for k in ("rss", "toa", "ang")}
        self.b = {k: np.zeros((C, P, 3)) for k in ("rss", "toa")}
        self.S = {k: np.zeros((C, P)) for k in ("rss", "toa")}
        self.los = np.zeros((C, P), bool)
        for ci, c in enumerate(self.cands):
            r = c.receiver()
            kinds = ["rss"] + (["az"] if r.horizontal_aperture_m > 0 else []) \
                + (["el"] if r.vertical_aperture_m > 0 else []) \
                + (["toa"] if r.sync_sigma_s is not None else [])
            for pi, pr in enumerate(self.probes):
                own = pr.building
                los = not scene.segment_blocked(pr.pos, r.pos, exclude=(own, r.host_building))
                self.los[ci, pi] = los
                dist = float(np.linalg.norm(pr.pos - np.asarray(r.pos)))
                for kind in kinds:
                    if kind != "rss" and not los:
                        continue
                    s = column_sigma(kind, r, los, dist, emitter, sensing, prop, robust)
                    J = analytic_jacobian(pr.pos, r.pos, kind, prop)
                    w = 1.0 / (s * s)
                    key = "ang" if kind in ("az", "el") else kind
                    self.A[key][ci, pi] += w * np.outer(J, J)
                    if key != "ang":
                        self.b[key][ci, pi] += w * J
                        self.S[key][ci, pi] += w

    def sigma_z(self, idx: Sequence[int]) -> np.ndarray:
        """(P,) vertical position std-dev [m] for the layout of candidate indices."""
        idx = list(idx)
        P = len(self.probes)
        F = self.A["ang"][idx].sum(0).copy() if idx else np.zeros((P, 3, 3))
        for key, s0 in (("rss", self.prop.unknown_offset_sigma_db), ("toa", CLOCK_OFFSET_SIGMA_M)):
            if not idx:
                continue
            A = self.A[key][idx].sum(0)
            b = self.b[key][idx].sum(0)
            S = self.S[key][idx].sum(0)
            F += A - np.einsum("pi,pj->pij", b, b) / (1.0 / s0 ** 2 + S)[:, None, None]
        out = np.full(P, np.inf)
        for p in range(P):
            Fp = F[p]
            if np.linalg.matrix_rank(Fp, tol=1e-12 * max(1.0, np.abs(Fp).max())) == 3:
                out[p] = sqrt(max(np.linalg.inv(Fp)[2, 2], 0.0))
        return out

    def per_probe_score(self, idx: Sequence[int]) -> np.ndarray:
        sz = self.sigma_z(idx)
        with np.errstate(divide="ignore"):
            return np.where(np.isfinite(sz), _ERF(self.fh / (2 * sqrt(2) * np.maximum(sz, 1e-9))), 0.0)

    def score(self, idx: Sequence[int]) -> float:
        return float(self.per_probe_score(idx).mean())

    def cost(self, idx: Sequence[int]) -> float:
        return float(sum(self.cands[i].cost for i in idx))


def restricted(obj: PlacementObjective, n: int) -> PlacementObjective:
    """View of obj that exposes only its first n candidates (for searching a subset)."""
    v = object.__new__(PlacementObjective)
    v.__dict__.update(obj.__dict__)
    v.cands = obj.cands[:n]
    for k in ("A", "b", "S"):
        setattr(v, k, {kk: vv[:n] for kk, vv in getattr(obj, k).items()})
    v.los = obj.los[:n]
    return v
