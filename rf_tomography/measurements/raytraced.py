"""Ray-traced path tables and the observables a receiver would extract from them.

numpy only. Tables are produced by propagation/sionna_scene.py (Sionna RT, separate venv)
and consumed anywhere. Conventions match the rest of rf_tomography: local ENU metres,
azimuth = atan2(dy, dx) and elevation = asin(dz / d) of the direction pointing from the
receiver TOWARD the source (verified against Sionna's theta_r/phi_r in tests).

Path type bitmask (OR over the interactions of a path), same codes as sionna.rt:
SPECULAR=1, DIFFUSE=2, REFRACTION=4, DIFFRACTION=8. A path with no interaction at all, or
only refractions (it passes through walls), is "direct-like": it points at the source.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

import numpy as np

SPECULAR, DIFFUSE, REFRACTION, DIFFRACTION = 1, 2, 4, 8
C = 299_792_458.0


@dataclass
class PathTable:
    frequency_hz: float
    tx: np.ndarray        # (N, 3) emitter positions
    rx: np.ndarray        # (R, 3) receiver positions
    power: np.ndarray     # (N, R, K) linear path power |a|^2 for unit Tx power, 0 = no path
    tau_s: np.ndarray     # (N, R, K) absolute delay [s]
    theta_r: np.ndarray   # (N, R, K) zenith angle of arrival [rad], toward the source
    phi_r: np.ndarray     # (N, R, K) azimuth of arrival [rad]
    ctype: np.ndarray     # (N, R, K) int8 bitmask of interaction types
    depth: np.ndarray     # (N, R, K) int8 number of interactions
    meta: Dict[str, object] = None
    total_power: np.ndarray = None  # (N, R) sum over ALL paths, set when the table keeps only the top-K

    def save(self, path) -> None:
        np.savez_compressed(path, frequency_hz=self.frequency_hz, tx=self.tx, rx=self.rx,
                            power=self.power.astype(np.float32), tau_s=self.tau_s,
                            theta_r=self.theta_r.astype(np.float32),
                            phi_r=self.phi_r.astype(np.float32), ctype=self.ctype,
                            depth=self.depth, meta=np.array(repr(self.meta)),
                            total_power=(self.total_power if self.total_power is not None
                                         else np.array([])))

    @staticmethod
    def load(path) -> "PathTable":
        z = np.load(Path(path), allow_pickle=False)
        return PathTable(float(z["frequency_hz"]), z["tx"], z["rx"], z["power"].astype(float),
                         z["tau_s"], z["theta_r"].astype(float), z["phi_r"].astype(float),
                         z["ctype"], z["depth"], {"repr": str(z["meta"])},
                         z["total_power"] if "total_power" in z.files and z["total_power"].size else None)


def direct_like(ctype: np.ndarray) -> np.ndarray:
    """True where a path has no reflection/diffuse/diffraction (may cross walls)."""
    return (ctype & (SPECULAR | DIFFUSE | DIFFRACTION)) == 0


@dataclass
class LinkObservables:
    rss_db: np.ndarray          # (N, R) 10log10 of total path power; -inf if no path
    toa_m: np.ndarray           # (N, R) first-arrival delay x c; nan if no path
    az: np.ndarray              # (N, R) azimuth of the strongest path [rad]
    el: np.ndarray              # (N, R) elevation of the strongest path [rad]
    strongest_direct: np.ndarray  # (N, R) bool: strongest path is direct-like
    first_direct: np.ndarray      # (N, R) bool: first-arrival path is direct-like
    strongest_ctype: np.ndarray   # (N, R) int
    has_direct: np.ndarray        # (N, R) bool: a direct-like path exists
    n_paths: np.ndarray           # (N, R)
    dist_m: np.ndarray            # (N, R) straight-line distance
    az_geo: np.ndarray            # (N, R) geometric azimuth
    el_geo: np.ndarray            # (N, R) geometric elevation


def link_observables(t: PathTable, toa_threshold_db: float = 20.0) -> LinkObservables:
    """Sensor-style observables: total power, first arrival within `toa_threshold_db` of
    the strongest path, AoA of the strongest path."""
    P = t.power
    valid = P > 0
    N, R, K = P.shape
    Pm = np.where(valid, P, 0.0)
    total = Pm.sum(-1) if t.total_power is None else t.total_power
    with np.errstate(divide="ignore"):
        rss_db = 10 * np.log10(total)
    imax = np.argmax(Pm, axis=-1)
    take = lambda a: np.take_along_axis(a, imax[..., None], -1)[..., 0]
    pmax = take(Pm)
    detect = valid & (Pm >= pmax[..., None] * 10 ** (-toa_threshold_db / 10))
    tau = np.where(detect, t.tau_s, np.inf)
    ifirst = np.argmin(tau, axis=-1)
    takef = lambda a: np.take_along_axis(a, ifirst[..., None], -1)[..., 0]
    has_any = valid.any(-1)
    toa_m = np.where(has_any, takef(t.tau_s) * C, np.nan)
    d = t.tx[:, None, :] - t.rx[None, :, :]
    dist = np.linalg.norm(d, axis=-1)
    dl = direct_like(t.ctype) & valid
    return LinkObservables(
        rss_db=np.where(has_any, rss_db, -np.inf), toa_m=toa_m,
        az=take(t.phi_r), el=math.pi / 2 - take(t.theta_r),
        strongest_direct=take(dl.astype(int)).astype(bool) & has_any,
        first_direct=takef(dl.astype(int)).astype(bool) & has_any,
        strongest_ctype=take(t.ctype).astype(int), has_direct=dl.any(-1),
        n_paths=valid.sum(-1), dist_m=dist,
        az_geo=np.arctan2(d[..., 1], d[..., 0]),
        el_geo=np.arcsin(np.clip(d[..., 2] / np.maximum(dist, 1e-9), -1, 1)))


def beamformed_aoa(t: PathTable, aperture_m: float, axis: str, half_width_deg: float = 25.0,
                   step_deg: float = 0.25) -> np.ndarray:
    """AoA a finite aperture would report: the peak of the power-weighted beam response.

    Non-coherent Bartlett model: S(a) = sum_k P_k * sinc^2(L (sin a - sin a_k) / lambda) for
    elevation (vertical array) and sinc^2(L sin(a - a_k) / lambda) for azimuth (horizontal
    array steered near the strongest path). Paths inside one beamwidth merge and pull the peak
    toward their power centroid; paths outside it are rejected. Path phases are not stored, so
    interference between paths is ignored. Returns (N, R) angles [rad]; nan for links
    without paths. `axis` is "el" or "az".
    """
    lam = C / t.frequency_hz
    N, R, K = t.power.shape
    grid = np.radians(np.arange(-half_width_deg, half_width_deg + step_deg / 2, step_deg))
    out = np.full((N, R), np.nan)
    for e in range(N):
        P = t.power[e]                                  # (R, K)
        has = P.sum(-1) > 0
        if not has.any():
            continue
        ang = (math.pi / 2 - t.theta_r[e]) if axis == "el" else t.phi_r[e]   # (R, K)
        strongest = np.take_along_axis(ang, np.argmax(P, -1)[:, None], -1)[:, 0]   # (R,)
        a = strongest[:, None] + grid[None, :]                                # (R, G)
        if axis == "el":
            d = np.sin(a)[:, :, None] - np.sin(ang)[:, None, :]
        else:
            d = np.sin(a[:, :, None] - ang[:, None, :])
        S = (P[:, None, :] * np.sinc(aperture_m * d / lam) ** 2).sum(-1)       # (R, G)
        peak = a[np.arange(R), np.argmax(S, -1)]
        out[e] = np.where(has, peak, np.nan)
    if axis == "az":
        out = (out + np.pi) % (2 * np.pi) - np.pi
    return out
