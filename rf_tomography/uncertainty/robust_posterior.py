"""Robust floor posterior: heavy-tailed columns + per-receiver latent NLOS state.

Same grid and prior as floor_posterior.py, different likelihood:

* RSS columns: unchanged Gaussian block (Tx-power offset marginalised exactly).
* AoA / TOA columns: per-column model `gaussian` | `mixture` | `student`
    mixture: (1-eps) N(0, s^2) + eps N(0, s_out^2)
    student: Student-t, nu degrees of freedom, scale s
* TOA clock offset: not marginalisable in closed form once columns are non-Gaussian,
  so it is profiled per candidate with the median residual (robust to <50% outliers).
* NLOS rejection: each receiver with AoA/TOA columns has a latent state. With prior
  pi_nlos all of its columns come from a wide NLOS component, otherwise from the
  per-column model above. The latent state is summed out (soft rejection); its
  posterior at the MAP candidate is returned so rejection can be audited.

The solver's hyperparameters are *beliefs*, set independently of the truth-side
presets in measurements/impairments.py. The outlier components are symmetric even
though true excess delay is one-sided: deliberate misspecification, not an oracle.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Sequence, Tuple

import numpy as np

from ..localization.forward import CLOCK_OFFSET_SIGMA_M, az_columns, mean_measurements, wrap_pi
from .floor_posterior import CandidateGrid

LOG2PI = math.log(2 * math.pi)


@dataclass(frozen=True)
class RobustConfig:
    name: str = "mixture+nlos"
    col_model: str = "mixture"          # gaussian | mixture | student
    eps: float = 0.15
    nu: float = 3.0
    sigma_out_angle_deg: float = 15.0
    sigma_out_toa_m: float = 30.0
    pi_nlos: float = 0.15
    sigma_nlos_angle_deg: float = 30.0
    sigma_nlos_toa_m: float = 60.0


def _log_normal(x, s):
    return -0.5 * (x / s) ** 2 - np.log(s) - 0.5 * LOG2PI


def _log_col(x, s, kind: str, cfg: RobustConfig):
    if cfg.col_model == "gaussian":
        return _log_normal(x, s)
    if cfg.col_model == "student":
        nu = cfg.nu
        c = math.lgamma((nu + 1) / 2) - math.lgamma(nu / 2) - 0.5 * math.log(nu * math.pi)
        return c - math.log(s) - (nu + 1) / 2 * np.log1p((x / s) ** 2 / nu)
    so = math.radians(cfg.sigma_out_angle_deg) if kind != "toa" else cfg.sigma_out_toa_m
    so = max(so, 3.0 * s)
    return np.logaddexp(math.log1p(-cfg.eps) + _log_normal(x, s),
                        math.log(cfg.eps) + _log_normal(x, so))


def robust_floor_posterior(y: np.ndarray, grid: CandidateGrid, receivers: Sequence, cols,
                           cov: np.ndarray, prop, cfg: RobustConfig
                           ) -> Tuple[np.ndarray, Dict[int, float]]:
    """Returns (per-floor probabilities, {receiver index: P(NLOS) at the MAP candidate})."""
    H = mean_measurements(grid.points, receivers, cols, prop)
    R = y[None, :] - H
    azc = az_columns(cols)
    if azc:
        R[:, azc] = wrap_pi(R[:, azc])
    N = R.shape[0]
    ll = np.zeros(N)

    rss = [j for j, (_, k) in enumerate(cols) if k == "rss"]
    if rss:
        L = np.linalg.cholesky(cov[np.ix_(rss, rss)])
        W = np.linalg.solve(L, R[:, rss].T).T
        ll += -0.5 * np.einsum("ij,ij->i", W, W)

    toa = [j for j, (_, k) in enumerate(cols) if k == "toa"]
    if toa:  # profile the emitter clock offset with the median residual
        R[:, toa] = R[:, toa] - np.median(R[:, toa], axis=1, keepdims=True)

    sig = np.sqrt(np.maximum(np.diag(cov), 1e-18))
    for j in toa:
        sig[j] = math.sqrt(max(cov[j, j] - CLOCK_OFFSET_SIGMA_M ** 2, 1e-12))

    by_rx: Dict[int, list] = {}
    for j, (i, k) in enumerate(cols):
        if k != "rss":
            by_rx.setdefault(i, []).append(j)

    post_nlos: Dict[int, np.ndarray] = {}
    for i, js in by_rx.items():
        lo = sum(_log_col(R[:, j], sig[j], cols[j][1], cfg) for j in js)
        if cfg.pi_nlos > 0:
            nl = sum(_log_normal(R[:, j], math.radians(cfg.sigma_nlos_angle_deg)
                                 if cols[j][1] != "toa" else cfg.sigma_nlos_toa_m) for j in js)
            a, b = math.log1p(-cfg.pi_nlos) + lo, math.log(cfg.pi_nlos) + nl
            rec = np.logaddexp(a, b)
            post_nlos[i] = np.exp(b - rec)
            ll += rec
        else:
            ll += lo

    c_star = int(np.argmax(ll))
    llg = ll.reshape(grid.n_xy, grid.n_floors)
    per_floor = np.exp(llg - llg.max()).sum(0)
    return per_floor / per_floor.sum(), {i: float(v[c_star]) for i, v in post_nlos.items()}
