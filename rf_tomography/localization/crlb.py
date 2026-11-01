"""Analytic Cramer-Rao bound on emitter position for a given receiver plan."""
from __future__ import annotations

import numpy as np

from .forward import mean_measurements, az_columns, wrap_pi


def position_crlb(point, receivers, cols, cov, prop, step_m: float = 0.1) -> np.ndarray:
    """3x3 position CRLB (x,y,z) via central finite differences; inf if singular."""
    p = np.asarray(point, float)
    azc = az_columns(cols)
    M = len(cols)
    Hj = np.empty((M, 3))
    for ax in range(3):
        e = np.zeros(3)
        e[ax] = step_m
        hp = mean_measurements(p + e, receivers, cols, prop)[0]
        hm = mean_measurements(p - e, receivers, cols, prop)[0]
        diff = hp - hm
        if azc:
            diff[azc] = wrap_pi(diff[azc])
        Hj[:, ax] = diff / (2 * step_m)
    J = Hj.T @ np.linalg.solve(cov, Hj)
    if np.linalg.matrix_rank(J, tol=1e-12 * max(1.0, np.abs(J).max())) < 3:
        return np.full((3, 3), np.inf)
    return np.linalg.inv(J)
