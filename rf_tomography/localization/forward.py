"""Forward measurement model + noise covariance for the identifiability baseline.

Measurement kinds per receiver:
  rss  dB       P0 - 10 n log10(d)          (always available)
  az   rad      atan2(dy, dx)               (horizontal aperture, LOS only)
  el   rad      asin(dz / d)                (vertical aperture, LOS only)
  toa  metres   d  (+ unknown common emitter clock offset)  (synced Rx, LOS only)

Unknown transmit power (rss) and emitter clock offset (toa) are handled as
nuisance parameters by adding a broad rank-one term to the noise covariance,
which is equivalent to marginalising them out under a flat-ish prior.

Simplifications (all optimistic => this is an *upper bound* on identifiability):
LOS/NLOS state is assumed known to the solver, SNR is known, noise is Gaussian,
array errors use a single-source narrowband CRB-style term plus a multipath floor.
"""
from __future__ import annotations

import math
from typing import List, Sequence, Tuple

import numpy as np

from ..measurements.models import EmitterClass, Receiver, SensingParams
from ..propagation.model import C, PropagationParams, snr_db_at, wavelength

Col = Tuple[int, str]  # (receiver index, kind)
CLOCK_OFFSET_SIGMA_M = 1.0e3


def plan_columns(receivers: Sequence[Receiver], los: Sequence[bool]) -> List[Col]:
    cols: List[Col] = []
    for i, r in enumerate(receivers):
        cols.append((i, "rss"))
        if not los[i]:
            continue
        if r.horizontal_aperture_m > 0:
            cols.append((i, "az"))
        if r.vertical_aperture_m > 0:
            cols.append((i, "el"))
        if r.sync_sigma_s is not None:
            cols.append((i, "toa"))
    return cols


def mean_measurements(points: np.ndarray, receivers: Sequence[Receiver],
                      cols: Sequence[Col], prop: PropagationParams) -> np.ndarray:
    """(N,3) candidate points -> (N,M) noiseless measurements (rss excludes offset)."""
    pts = np.atleast_2d(points)
    out = np.empty((pts.shape[0], len(cols)))
    cache = {}
    for j, (i, kind) in enumerate(cols):
        if i not in cache:
            d = pts - np.asarray(receivers[i].pos, float)
            cache[i] = (d, np.maximum(np.linalg.norm(d, axis=1), 1e-6))
        d, dist = cache[i]
        if kind == "rss":
            out[:, j] = -10.0 * prop.path_loss_exp * np.log10(dist)
        elif kind == "az":
            out[:, j] = np.arctan2(d[:, 1], d[:, 0])
        elif kind == "el":
            out[:, j] = np.arcsin(np.clip(d[:, 2] / dist, -1, 1))
        else:
            out[:, j] = dist
    return out


def angular_columns(cols: Sequence[Col]) -> List[int]:
    return [j for j, (_, k) in enumerate(cols) if k in ("az", "el")]


def az_columns(cols: Sequence[Col]) -> List[int]:
    return [j for j, (_, k) in enumerate(cols) if k == "az"]


def wrap_pi(x: np.ndarray) -> np.ndarray:
    return (x + np.pi) % (2 * np.pi) - np.pi


def noise_covariance(cols: Sequence[Col], receivers: Sequence[Receiver],
                     los: Sequence[bool], dist_to_truth: Sequence[float],
                     emitter: EmitterClass, sensing: SensingParams,
                     prop: PropagationParams) -> np.ndarray:
    lam = wavelength(emitter.freq_hz)
    n_snap = max(1.0, emitter.bandwidth_hz * sensing.t_obs_s)
    beta_rms = emitter.bandwidth_hz / math.sqrt(12.0)
    mp_ang = math.radians(sensing.multipath_angle_sigma_deg)
    M = len(cols)
    C_ = np.zeros((M, M))
    rss_idx, toa_idx = [], []
    for j, (i, kind) in enumerate(cols):
        r = receivers[i]
        snr = 10 ** (snr_db_at(emitter.ref_snr_db, dist_to_truth[i], prop) / 10.0)
        if kind == "rss":
            s = prop.shadow_sigma_db_los if los[i] else prop.shadow_sigma_db_nlos
            rss_idx.append(j)
        elif kind in ("az", "el"):
            ap = r.horizontal_aperture_m if kind == "az" else r.vertical_aperture_m
            s_arr = lam / (2 * math.pi * ap * math.sqrt(2.0 * snr * n_snap))
            s = math.hypot(s_arr, mp_ang)
        else:
            s_crlb_t = 1.0 / (2 * math.pi * beta_rms * math.sqrt(2.0 * snr * n_snap))
            s = math.hypot(math.hypot(C * r.sync_sigma_s, C * s_crlb_t),
                           sensing.multipath_range_sigma_m)
            toa_idx.append(j)
        C_[j, j] = s * s
    if rss_idx:
        C_[np.ix_(rss_idx, rss_idx)] += prop.unknown_offset_sigma_db ** 2
    if toa_idx:
        C_[np.ix_(toa_idx, toa_idx)] += CLOCK_OFFSET_SIGMA_M ** 2
    return C_
