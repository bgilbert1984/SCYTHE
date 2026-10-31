"""Grid Bayesian floor posterior for one known building: P(floor | m, G).

Uniform prior over (x,y) footprint cells and floors; Gaussian likelihood from
forward.py; x,y marginalised per floor. Returns per-floor probabilities.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from ..geometry.scene import Building
from ..localization.forward import az_columns, mean_measurements, wrap_pi


@dataclass(frozen=True)
class CandidateGrid:
    points: np.ndarray  # (Nxy*F, 3), xy-major: index = ixy * F + floor_idx
    n_xy: int
    n_floors: int

    @staticmethod
    def for_building(b: Building, xy_spacing_m: float = 2.0) -> "CandidateGrid":
        nx, ny = int(b.width // xy_spacing_m), int(b.depth // xy_spacing_m)
        xs = b.cx - b.width / 2 + (np.arange(nx) + 0.5) * b.width / nx
        ys = b.cy - b.depth / 2 + (np.arange(ny) + 0.5) * b.depth / ny
        zs = b.floor_centers_z()
        X, Y = np.meshgrid(xs, ys, indexing="ij")
        xy = np.stack([X.ravel(), Y.ravel()], 1)
        pts = np.concatenate(
            [np.repeat(xy, len(zs), 0), np.tile(zs, len(xy))[:, None]], 1)
        return CandidateGrid(pts, len(xy), len(zs))


def floor_posterior(y: np.ndarray, grid: CandidateGrid, receivers: Sequence,
                    cols, cov: np.ndarray, prop) -> np.ndarray:
    H = mean_measurements(grid.points, receivers, cols, prop)
    R = y[None, :] - H
    azc = az_columns(cols)
    if azc:
        R[:, azc] = wrap_pi(R[:, azc])
    L = np.linalg.cholesky(cov)
    W = np.linalg.solve(L, R.T).T  # whitened residuals
    ll = -0.5 * np.einsum("ij,ij->i", W, W)
    ll = ll.reshape(grid.n_xy, grid.n_floors)
    m = ll.max()
    per_floor = np.exp(ll - m).sum(0)
    return per_floor / per_floor.sum()


def credible_set_size(p: np.ndarray, mass: float = 0.9) -> int:
    order = np.argsort(-p)
    return int(np.searchsorted(np.cumsum(p[order]), mass) + 1)


def in_credible_set(p: np.ndarray, truth_idx: int, mass: float = 0.9) -> bool:
    order = np.argsort(-p)
    k = credible_set_size(p, mass)
    return truth_idx in order[:k]
