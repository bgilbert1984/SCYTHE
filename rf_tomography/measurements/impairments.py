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
from ..localization.forward import mean_measurements, wrap_pi


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
    # --- correlated / coherent mechanisms (see apply_reflections, apply_common_biases) ---
    reflect_prob: float = 0.0        # per LOS receiver: strongest path is a single bounce
    reflect_surfaces: str = "mixed"  # ground | facade | mixed
    el_bias_common_deg: float = 0.0  # sigma of ONE elevation bias shared by all receivers
    common_delay_mean_m: float = 0.0  # ONE exponential excess delay shared by all receivers

    @property
    def active(self) -> bool:
        return any((self.outlier_prob, self.false_los_prob, self.false_nlos_prob,
                    self.geometry_sigma_m, self.reflect_prob, self.el_bias_common_deg,
                    self.common_delay_mean_m))


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


# ---------------------------------------------------------------------------
# Correlated / coherent mechanisms
# ---------------------------------------------------------------------------

def _facade_images(truth, receiver, scene: Scene, emitter_building: str):
    """Image sources for valid single-bounce facade reflections emitter -> face -> receiver."""
    out = []
    for b in scene.buildings:
        if b.name in (emitter_building, receiver.host_building):
            continue
        lo, hi = b.box
        for axis in (0, 1):
            other = 1 - axis
            for c in (lo[axis], hi[axis]):
                if (truth[axis] - c) * (receiver.pos[axis] - c) <= 0:
                    continue  # emitter and receiver not on the same side of this face
                img = np.array(truth, float)
                img[axis] = 2 * c - truth[axis]
                rx = np.asarray(receiver.pos, float)
                t = (c - img[axis]) / (rx[axis] - img[axis])
                pt = img + t * (rx - img)
                if 0 < t < 1 and lo[other] <= pt[other] <= hi[other] and 0 <= pt[2] <= hi[2]:
                    out.append(img)
    return out


def apply_reflections(y, cols, receivers, truth, world_los, imp: ImpairmentModel,
                      rng: np.random.Generator, prop, world: Scene, emitter_building: str,
                      refl_out: set | None = None):
    """Replace a receiver's AoA/TOA columns by those of a single-bounce image source.

    Unlike independent outliers, the three columns of a reflected receiver stay mutually
    consistent: they describe a different, plausible emitter position (mirror image in the
    ground plane or in a neighbouring facade). Excess delay is the exact path-length
    difference, so it is one-sided. Models a direct path suppressed by local clutter that
    the building-level LOS test cannot see. Returns (y, n_reflected_receivers).
    """
    y = y.copy()
    n = 0
    for i in sorted({i for i, k in cols if k != "rss"}):
        if not world_los[i] or rng.random() >= imp.reflect_prob:
            continue
        ground = [np.array([truth[0], truth[1], -truth[2]])]
        facade = _facade_images(truth, receivers[i], world, emitter_building)
        if imp.reflect_surfaces == "ground":
            pool = ground
        elif imp.reflect_surfaces == "facade":
            pool = facade
        else:
            pool = ground if (rng.random() < 0.5 or not facade) else facade
        if not pool:
            continue
        img = pool[int(rng.integers(len(pool)))]
        js = [j for j, (ii, k) in enumerate(cols) if ii == i and k != "rss"]
        sub = [cols[j] for j in js]
        d = mean_measurements(img, receivers, sub, prop)[0] - mean_measurements(truth, receivers, sub, prop)[0]
        for q, (_, k) in enumerate(sub):
            if k == "az":
                d[q] = wrap_pi(d[q])
        y[js] += d
        n += 1
        if refl_out is not None:
            refl_out.add(i)
    return y, n


def apply_common_biases(y, cols, imp: ImpairmentModel, rng: np.random.Generator):
    """Errors shared by every receiver: elevation calibration bias, common excess delay.

    A common delay is indistinguishable from the emitter clock offset and should be
    harmless (positive control). A common elevation bias moves every floor estimate the
    same way and cannot be averaged out by adding receivers.
    """
    y = y.copy()
    if imp.el_bias_common_deg > 0:
        y[[j for j, (_, k) in enumerate(cols) if k == "el"]] += rng.normal(
            0, math.radians(imp.el_bias_common_deg))
    if imp.common_delay_mean_m > 0:
        y[[j for j, (_, k) in enumerate(cols) if k == "toa"]] += rng.exponential(imp.common_delay_mean_m)
    return y


# Assumed severities (not measurements).
CORRELATED_PRESETS = {
    "refl_mild": ImpairmentModel("refl_mild", reflect_prob=0.15),
    "refl_strong": ImpairmentModel("refl_strong", reflect_prob=0.35),
    "calib_bias": ImpairmentModel("calib_bias", el_bias_common_deg=1.0),
    "combined": ImpairmentModel("combined", reflect_prob=0.25, el_bias_common_deg=1.0,
                                common_delay_mean_m=20.0),
}
