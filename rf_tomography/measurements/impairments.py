"""Truth-side impairments the solver does NOT model.

Everything here acts on the *world* (measurement generation), never on the
inference model. Four mechanisms:

1. Geometry error: the world's buildings are offset from the model the solver
   uses, so real line-of-sight differs from what the solver computes.
2. LOS classification error: a truly-NLOS receiver is classified LOS
   (`false_los_prob`) and its angle/TOA columns enter the solver as if direct-path;
   a truly-LOS receiver is classified NLOS (`false_nlos_prob`) and loses its columns.
3. NLOS corruption: columns from a truly-NLOS receiver that the solver trusted carry
   positive excess delay (exponential) and a large angle error (Gaussian).
4. Heavy-tailed LOS multipath: each LOS angle/TOA column is, with `outlier_prob`,
   replaced by a reflected-path outlier (positive exponential TOA excess, angle
   error with sigma `angle_outlier_deg`) on top of the nominal Gaussian noise.

All parameter values are ASSUMPTIONS chosen to be plausible urban ranges, not
measurements. Calibrate against real data before quoting results.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import List, Sequence, Tuple

import numpy as np

from ..geometry.scene import Scene


@dataclass(frozen=True)
class ImpairmentModel:
    name: str = "none"
    outlier_prob: float = 0.0
    toa_excess_mean_m: float = 15.0
    angle_outlier_deg: float = 10.0
    nlos_toa_excess_mean_m: float = 30.0
    nlos_angle_sigma_deg: float = 20.0
    false_los_prob: float = 0.0
    false_nlos_prob: float = 0.0
    geometry_sigma_m: float = 0.0

    @property
    def active(self) -> bool:
        return any((self.outlier_prob, self.false_los_prob, self.false_nlos_prob,
                    self.geometry_sigma_m))


PRESETS = {
    "none": ImpairmentModel("none"),
    "moderate": ImpairmentModel("moderate", outlier_prob=0.10, toa_excess_mean_m=15.0,
                                angle_outlier_deg=10.0, nlos_toa_excess_mean_m=30.0,
                                nlos_angle_sigma_deg=20.0, false_los_prob=0.10,
                                false_nlos_prob=0.05, geometry_sigma_m=1.5),
    "severe": ImpairmentModel("severe", outlier_prob=0.25, toa_excess_mean_m=25.0,
                              angle_outlier_deg=20.0, nlos_toa_excess_mean_m=60.0,
                              nlos_angle_sigma_deg=35.0, false_los_prob=0.25,
                              false_nlos_prob=0.10, geometry_sigma_m=4.0),
}


def perturb_scene(scene: Scene, sigma_m: float, rng: np.random.Generator) -> Scene:
    """World geometry: each building's footprint centre offset by N(0, sigma) in x, y."""
    if sigma_m <= 0:
        return scene
    return Scene(tuple(replace(b, cx=b.cx + rng.normal(0, sigma_m),
                               cy=b.cy + rng.normal(0, sigma_m)) for b in scene.buildings))


def classify_los(world_los: Sequence[bool], imp: ImpairmentModel,
                 rng: np.random.Generator) -> List[bool]:
    """What the solver *believes* about each receiver's LOS state."""
    out = []
    for w in world_los:
        u = rng.random()
        out.append((u >= imp.false_nlos_prob) if w else (u < imp.false_los_prob))
    return out


def corrupt_measurements(y: np.ndarray, cols: Sequence[Tuple[int, str]],
                         world_los: Sequence[bool], imp: ImpairmentModel,
                         rng: np.random.Generator,
                         bad_out: set | None = None) -> Tuple[np.ndarray, int, int]:
    """Add NLOS corruption and heavy-tailed multipath to AoA/TOA columns of y (copy).

    Returns (y, n_nlos_corrupted_receivers, n_outlier_columns). If bad_out is a set, the
    indices of the NLOS-corrupted receivers are added to it (for auditing a solver).
    """
    y = y.copy()
    bad_rx, n_out = set(), 0
    for j, (i, kind) in enumerate(cols):
        if kind == "rss":
            continue
        if not world_los[i]:  # solver trusted a receiver that has no direct path
            bad_rx.add(i)
            if kind == "toa":
                y[j] += rng.exponential(imp.nlos_toa_excess_mean_m)
            else:
                y[j] += rng.normal(0, math.radians(imp.nlos_angle_sigma_deg))
        elif imp.outlier_prob and rng.random() < imp.outlier_prob:
            n_out += 1
            if kind == "toa":
                y[j] += rng.exponential(imp.toa_excess_mean_m)
            else:
                y[j] += rng.normal(0, math.radians(imp.angle_outlier_deg))
    if bad_out is not None:
        bad_out.update(bad_rx)
    return y, len(bad_rx), n_out
