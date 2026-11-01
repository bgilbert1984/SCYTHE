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
MAT_NONE, MAT_CONCRETE, MAT_GLASS, MAT_GROUND = 0, 1, 2, 3
COMPOSITION_FIELDS = ("best_types", "best_mats", "dir_types", "dir_mats", "dir_power")
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
    # path composition (optional): interaction types and material codes per depth for the strongest
    # path and for the strongest direct-like path, plus the latter's power. Material codes:
    # 0 none, 1 concrete (walls, spandrels, roofs), 2 glass, 3 ground.
    best_types: np.ndarray = None   # (N, R, D) int8
    best_mats: np.ndarray = None    # (N, R, D) int8
    dir_types: np.ndarray = None    # (N, R, D) int8
    dir_mats: np.ndarray = None     # (N, R, D) int8
    dir_power: np.ndarray = None    # (N, R) float32, 0 if no direct-like path

    def save(self, path) -> None:
        np.savez_compressed(path, frequency_hz=self.frequency_hz, tx=self.tx, rx=self.rx,
                            power=self.power.astype(np.float32), tau_s=self.tau_s,
                            theta_r=self.theta_r.astype(np.float32),
                            phi_r=self.phi_r.astype(np.float32), ctype=self.ctype,
                            depth=self.depth, meta=np.array(repr(self.meta)),
                            total_power=(self.total_power if self.total_power is not None
                                         else np.array([])),
                            **{k: (getattr(self, k) if getattr(self, k) is not None else np.array([]))
                               for k in COMPOSITION_FIELDS})

    @staticmethod
    def load(path) -> "PathTable":
        z = np.load(Path(path), allow_pickle=False)
        return PathTable(float(z["frequency_hz"]), z["tx"], z["rx"], z["power"].astype(float),
                         z["tau_s"], z["theta_r"].astype(float), z["phi_r"].astype(float),
                         z["ctype"], z["depth"], {"repr": str(z["meta"])},
                         z["total_power"] if "total_power" in z.files and z["total_power"].size else None,
                         *[(z[k] if k in z.files and z[k].size else None) for k in COMPOSITION_FIELDS])


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


# ---------------------------------------------------------------------------
# Path composition
# ---------------------------------------------------------------------------
# strongest-path classes
(PC_NONE, PC_FREE, PC_GLASS_ONLY, PC_CONCRETE_THRU, PC_REFLECT_GLASS, PC_REFLECT_CONCRETE,
 PC_REFLECT_GROUND, PC_DIFFRACT, PC_OTHER) = range(9)
PC_NAMES = ("no path", "free line of sight", "through glass only", "through concrete (spandrel/pier/roof)",
            "reflection off glass", "reflection off concrete", "reflection off ground", "diffracted",
            "other multi-interaction")
# direct-like path crossing classes
(DC_NONE, DC_FREE, DC_GLASS, DC_CONCRETE) = range(4)
DC_NAMES = ("no direct-like path", "free", "glass only", "crosses concrete")


def _any_mat(mats, code):
    return (mats == code).any(-1)


def strongest_path_class(t: PathTable) -> np.ndarray:
    """(N, R) class of each link's strongest path from its interaction history."""
    ty, ma = t.best_types, t.best_mats
    has = (t.power > 0).any(-1)
    spec = ((ty & SPECULAR) > 0).any(-1)
    refr = ((ty & REFRACTION) > 0).any(-1)
    diff = ((ty & DIFFRACTION) > 0).any(-1)
    none_i = (ty == 0).all(-1)
    cls = np.full(has.shape, PC_OTHER, int)
    cls[diff] = PC_DIFFRACT
    only_refr = refr & ~spec & ~diff
    cls[only_refr & _any_mat(ma, MAT_CONCRETE)] = PC_CONCRETE_THRU
    cls[only_refr & ~_any_mat(ma, MAT_CONCRETE)] = PC_GLASS_ONLY
    # reflections: classify by the material of the reflecting interaction(s); ground wins if present
    refl = spec & ~diff
    mat_at_spec = np.where((ty & SPECULAR) > 0, ma, MAT_NONE)
    cls[refl & _any_mat(mat_at_spec, MAT_GLASS)] = PC_REFLECT_GLASS
    cls[refl & _any_mat(mat_at_spec, MAT_CONCRETE)] = PC_REFLECT_CONCRETE
    cls[refl & _any_mat(mat_at_spec, MAT_GROUND)] = PC_REFLECT_GROUND
    cls[none_i] = PC_FREE
    cls[~has] = PC_NONE
    return cls


def direct_crossing_class(t: PathTable) -> np.ndarray:
    """(N, R) what the strongest direct-like path (no reflection/diffraction) passes through."""
    ma = t.dir_mats
    has = t.dir_power > 0
    cls = np.full(has.shape, DC_GLASS, int)
    cls[_any_mat(ma, MAT_CONCRETE)] = DC_CONCRETE
    cls[(ma == 0).all(-1)] = DC_FREE
    cls[~has] = DC_NONE
    return cls
