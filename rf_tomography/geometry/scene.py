"""Synthetic urban scene: axis-aligned building boxes in a local ENU frame (metres).

x = east, y = north, z = up from street level. Mapping to geoFrames.js
(WGS84/Cesium) is deliberately out of scope for Phase A.
"""
from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True)
class Scene:
    buildings: Tuple[Building, ...]

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
        return any(segment_hits_box(p, q, *b.box) for b in self.blockers(exclude))


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
