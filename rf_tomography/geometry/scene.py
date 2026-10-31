"""Synthetic urban scene: axis-aligned building boxes in a local ENU frame (metres).

x = east, y = north, z = up from street level. Mapping to geoFrames.js
(WGS84/Cesium) is deliberately out of scope for Phase A.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import Iterable, Optional, Tuple

import numpy as np

FLOOR_HEIGHT_M = 3.7


@dataclass(frozen=True)
class Building:
    name: str
    cx: float
    cy: float
    width: float  # x extent
    depth: float  # y extent
    n_floors: int
    floor_height: float = FLOOR_HEIGHT_M

    @property
    def height(self) -> float:
        return self.n_floors * self.floor_height

    @property
    def box(self) -> Tuple[np.ndarray, np.ndarray]:
        lo = np.array([self.cx - self.width / 2, self.cy - self.depth / 2, 0.0])
        hi = np.array([self.cx + self.width / 2, self.cy + self.depth / 2, self.height])
        return lo, hi

    def floor_center_z(self, floor: int) -> float:
        """Floor numbering is 1-based; floor 1 spans z in [0, floor_height]."""
        if not 1 <= floor <= self.n_floors:
            raise ValueError(f"floor {floor} outside 1..{self.n_floors}")
        return (floor - 0.5) * self.floor_height

    def floor_centers_z(self) -> np.ndarray:
        return (np.arange(1, self.n_floors + 1) - 0.5) * self.floor_height

    # --- generic building interface (shared with PolygonBuilding) -------------------------
    is_box = True

    def blocks(self, p, q) -> bool:
        """True if the open segment p->q passes through the building."""
        return segment_hits_box(p, q, *self.box)

    def sample_xy(self, rng, inset: float = 1.0):
        """Uniform point in the footprint inset by `inset` m (draws x then y)."""
        lo, hi = self.box
        return rng.uniform(lo[0] + inset, hi[0] - inset), rng.uniform(lo[1] + inset, hi[1] - inset)

    def grid_xy(self, spacing: float) -> np.ndarray:
        """Cell-centre grid over the footprint, shape (n, 2)."""
        nx, ny = int(self.width // spacing), int(self.depth // spacing)
        xs = self.cx - self.width / 2 + (np.arange(nx) + 0.5) * self.width / nx
        ys = self.cy - self.depth / 2 + (np.arange(ny) + 0.5) * self.depth / ny
        X, Y = np.meshgrid(xs, ys, indexing="ij")
        return np.stack([X.ravel(), Y.ravel()], 1)


def _point_in_polygon(poly: np.ndarray, x: float, y: float) -> bool:
    """Ray casting; poly is (n, 2)."""
    xi, yi = poly[:, 0], poly[:, 1]
    xj, yj = np.roll(xi, 1), np.roll(yi, 1)
    cond = (yi > y) != (yj > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        xcross = (xj - xi) * (y - yi) / (yj - yi) + xi
    return bool(np.count_nonzero(cond & (x < xcross)) % 2)


def _points_in_polygon(poly: np.ndarray, pts: np.ndarray) -> np.ndarray:
    xi, yi = poly[:, 0][None, :], poly[:, 1][None, :]
    xj, yj = np.roll(poly[:, 0], 1)[None, :], np.roll(poly[:, 1], 1)[None, :]
    x, y = pts[:, 0][:, None], pts[:, 1][:, None]
    cond = (yi > y) != (yj > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        xcross = (xj - xi) * (y - yi) / (yj - yi) + xi
    return (np.count_nonzero(cond & (x < xcross), axis=1) % 2).astype(bool)


def _dist_to_edges(poly: np.ndarray, x: float, y: float) -> float:
    a, b = poly, np.roll(poly, -1, axis=0)
    ab = b - a
    t = np.clip(((x - a[:, 0]) * ab[:, 0] + (y - a[:, 1]) * ab[:, 1]) /
                np.maximum((ab ** 2).sum(1), 1e-12), 0.0, 1.0)
    px, py = a[:, 0] + t * ab[:, 0], a[:, 1] + t * ab[:, 1]
    return float(np.sqrt((px - x) ** 2 + (py - y) ** 2).min())


@dataclass(frozen=True)
class PolygonBuilding:
    """A real building: an arbitrary simple polygon footprint extruded from street level.

    Same interface as Building (so Scene, grids, sampling and the solvers need no special
    cases); `facade` names a style in geometry/mesh.py (windows are meshed per floor there).
    The footprint is stored counter-clockwise, without a repeated closing vertex.
    """
    name: str
    footprint: Tuple[Tuple[float, float], ...]
    height: float
    n_floors: int
    facade: str = "concrete"
    year: Optional[int] = None
    height_known: bool = True

    is_box = False

    @cached_property
    def poly(self) -> np.ndarray:
        return np.asarray(self.footprint, float)

    @property
    def floor_height(self) -> float:
        return self.height / self.n_floors

    @cached_property
    def box(self) -> Tuple[np.ndarray, np.ndarray]:
        lo, hi = self.poly.min(0), self.poly.max(0)
        return np.array([lo[0], lo[1], 0.0]), np.array([hi[0], hi[1], self.height])

    @property
    def cx(self) -> float:
        return float(self.poly[:, 0].mean())

    @property
    def cy(self) -> float:
        return float(self.poly[:, 1].mean())

    @property
    def width(self) -> float:
        lo, hi = self.box
        return float(hi[0] - lo[0])

    @property
    def depth(self) -> float:
        lo, hi = self.box
        return float(hi[1] - lo[1])

    @cached_property
    def area(self) -> float:
        x, y = self.poly[:, 0], self.poly[:, 1]
        return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))

    def floor_center_z(self, floor: int) -> float:
        if not 1 <= floor <= self.n_floors:
            raise ValueError(f"floor {floor} outside 1..{self.n_floors}")
        return (floor - 0.5) * self.floor_height

    def floor_centers_z(self) -> np.ndarray:
        return (np.arange(1, self.n_floors + 1) - 0.5) * self.floor_height

    def contains_xy(self, x: float, y: float) -> bool:
        return _point_in_polygon(self.poly, x, y)

    def interior_point(self) -> Tuple[float, float]:
        """A point well inside the footprint (grid search for the largest edge clearance)."""
        lo, hi = self.box
        gx, gy = np.meshgrid(np.linspace(lo[0], hi[0], 41), np.linspace(lo[1], hi[1], 41))
        pts = np.stack([gx.ravel(), gy.ravel()], 1)
        pts = pts[_points_in_polygon(self.poly, pts)]
        if not len(pts):
            return self.cx, self.cy
        best = max(pts, key=lambda q: _dist_to_edges(self.poly, q[0], q[1]))
        return float(best[0]), float(best[1])

    def sample_xy(self, rng, inset: float = 1.0):
        """Uniform point inside the footprint at least `inset` m from every wall (rejection)."""
        lo, hi = self.box
        for _ in range(5000):
            x = rng.uniform(lo[0], hi[0])
            y = rng.uniform(lo[1], hi[1])
            if self.contains_xy(x, y) and _dist_to_edges(self.poly, x, y) >= inset:
                return x, y
        return self.interior_point()

    def grid_xy(self, spacing: float) -> np.ndarray:
        lo, hi = self.box
        nx, ny = max(1, int((hi[0] - lo[0]) // spacing)), max(1, int((hi[1] - lo[1]) // spacing))
        xs = lo[0] + (np.arange(nx) + 0.5) * (hi[0] - lo[0]) / nx
        ys = lo[1] + (np.arange(ny) + 0.5) * (hi[1] - lo[1]) / ny
        X, Y = np.meshgrid(xs, ys, indexing="ij")
        pts = np.stack([X.ravel(), Y.ravel()], 1)
        pts = pts[_points_in_polygon(self.poly, pts)]
        return pts if len(pts) else np.array([self.interior_point()])

    def blocks(self, p, q, eps: float = 1e-6) -> bool:
        """True if the open segment p->q has a non-zero-length piece inside the prism."""
        lo, hi = self.box
        if not segment_hits_box(p, q, lo, hi, eps):          # cheap bounding-box reject
            return False
        p, q = np.asarray(p, float), np.asarray(q, float)
        d = q - p
        t0, t1 = eps, 1.0 - eps
        if abs(d[2]) < 1e-12:
            if p[2] <= 0.0 or p[2] >= self.height:
                return False
        else:
            ta, tb = (0.0 - p[2]) / d[2], (self.height - p[2]) / d[2]
            t0, t1 = max(t0, min(ta, tb)), min(t1, max(ta, tb))
            if t0 >= t1:
                return False
        a = self.poly
        e = np.roll(a, -1, axis=0) - a                       # edge vectors
        den = d[0] * e[:, 1] - d[1] * e[:, 0]
        ok = np.abs(den) > 1e-12
        w = a - p[:2]
        with np.errstate(divide="ignore", invalid="ignore"):
            t = (w[:, 0] * e[:, 1] - w[:, 1] * e[:, 0]) / den
            u = (w[:, 0] * d[1] - w[:, 1] * d[0]) / den
        hit = ok & (u >= 0.0) & (u <= 1.0) & (t > t0) & (t < t1)
        ts = np.sort(np.concatenate([[t0, t1], t[hit]]))
        for lo_t, hi_t in zip(ts[:-1], ts[1:]):
            if hi_t - lo_t > 1e-9 and _point_in_polygon(a, *(p[:2] + 0.5 * (lo_t + hi_t) * d[:2])):
                return True
        return False


@dataclass(frozen=True)
class Scene:
    buildings: Tuple[object, ...]   # Building | PolygonBuilding

    def by_name(self, name: str) -> Building:
        for b in self.buildings:
            if b.name == name:
                return b
        raise KeyError(name)

    def blockers(self, exclude: Iterable[Optional[str]] = ()) -> Tuple[Building, ...]:
        ex = {e for e in exclude if e}
        return tuple(b for b in self.buildings if b.name not in ex)

    def segment_blocked(self, p, q, exclude: Iterable[Optional[str]] = ()) -> bool:
        """True if open segment p->q passes through any non-excluded building.

        Own/host buildings are excluded by the caller: facade penetration loss
        is a per-building constant absorbed by the RSS nuisance offset.
        """
        return any(b.blocks(p, q) for b in self.blockers(exclude))


def segment_hits_box(p, q, lo, hi, eps: float = 1e-6) -> bool:
    """Slab test for segment p->q against an AABB, interior t in (eps, 1-eps)."""
    p = np.asarray(p, float)
    d = np.asarray(q, float) - p
    t0, t1 = eps, 1.0 - eps
    for ax in range(3):
        if abs(d[ax]) < 1e-12:
            if p[ax] <= lo[ax] or p[ax] >= hi[ax]:
                return False
        else:
            ta, tb = (lo[ax] - p[ax]) / d[ax], (hi[ax] - p[ax]) / d[ax]
            if ta > tb:
                ta, tb = tb, ta
            t0, t1 = max(t0, ta), min(t1, tb)
            if t0 >= t1:
                return False
    return True


def synthetic_manhattan_block() -> Scene:
    """Four towers around one intersection, mirroring the Phase-A mock-up.

    50x50 m footprints, 30 m street gaps (x=0 and y=0 corridors), floor counts
    38/27/44/31 as in the Tower A-D vibes image. Synthetic: not real Manhattan.
    """
    return Scene(
        (
            Building("A", -40.0, -40.0, 50.0, 50.0, 38),
            Building("B", 40.0, -40.0, 50.0, 50.0, 27),
            Building("C", -40.0, 40.0, 50.0, 50.0, 44),
            Building("D", 40.0, 40.0, 50.0, 50.0, 31),
        )
    )
