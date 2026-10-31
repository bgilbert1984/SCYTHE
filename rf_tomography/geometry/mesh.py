"""Triangle meshes for extruded buildings: roofs, spandrel walls and per-floor window bands.

numpy only. The Sionna side (propagation/sionna_scene.py) writes these to OBJ files. Material
tags: "concrete" (roofs, spandrels, solid walls) and "glass" (windows).

Facade styles (glazing ratio = fraction of each floor's height that is glass, as a continuous
horizontal ribbon: a simplification of punched windows with the same glazed area):

    concrete       0.00  solid concrete wall (the earlier closed-box model)
    masonry        0.25  punched-window masonry (pre-war)
    curtain        0.60  curtain-wall office tower
    glass_curtain  0.80  modern glass tower
    glass_solid    1.00  all glass (the earlier glass closed-box model)

`assign_facades(scene, policy)` sets each building's style: "concrete" / "glass_solid" for the
uniform baselines, or "mixed", a heuristic from construction year and height (ASSUMPTION, not
measured): before 1945 masonry; from 1945 curtain wall when >= 60 m else masonry; from 2000
glass curtain; unknown year by height.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Dict, List, Tuple

import numpy as np

from .scene import PolygonBuilding, Scene

GLAZING = {"concrete": 0.0, "masonry": 0.25, "curtain": 0.60, "glass_curtain": 0.80, "glass_solid": 1.0}
SPANDREL_BELOW_FRAC = 0.6      # share of the opaque part of a floor that lies below the glazing


def style_for(b: PolygonBuilding) -> str:
    y = b.year
    if y is None or y <= 0:
        return "curtain" if b.height >= 60 else "masonry"
    if y < 1945:
        return "masonry"
    if y < 2000:
        return "curtain" if b.height >= 60 else "masonry"
    return "glass_curtain"


def assign_facades(scene: Scene, policy: str) -> Scene:
    if policy in ("concrete", "glass_solid"):
        return Scene(tuple(replace(b, facade=policy) for b in scene.buildings))
    if policy == "mixed":
        return Scene(tuple(replace(b, facade=style_for(b)) for b in scene.buildings))
    raise ValueError(f"unknown facade policy {policy!r}")


def triangulate_polygon(poly: np.ndarray) -> np.ndarray:
    """Ear clipping for a simple polygon (any orientation); returns (m, 3) vertex indices
    in counter-clockwise order. O(n^2), fine for building footprints."""
    pts = np.asarray(poly, float)
    n = len(pts)
    area2 = float(np.dot(pts[:, 0], np.roll(pts[:, 1], -1)) - np.dot(pts[:, 1], np.roll(pts[:, 0], -1)))
    idx = list(range(n)) if area2 > 0 else list(range(n - 1, -1, -1))

    def cross(o, a, b):
        return (pts[a, 0] - pts[o, 0]) * (pts[b, 1] - pts[o, 1]) - (pts[a, 1] - pts[o, 1]) * (pts[b, 0] - pts[o, 0])

    def inside(p, a, b, c):
        return cross(a, b, p) >= -1e-12 and cross(b, c, p) >= -1e-12 and cross(c, a, p) >= -1e-12

    tris: List[Tuple[int, int, int]] = []
    guard = 0
    while len(idx) > 3 and guard < 10 * n * n:
        guard += 1
        clipped = False
        for k in range(len(idx)):
            a, b, c = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            if cross(a, b, c) <= 1e-12:
                continue                                  # reflex or degenerate corner
            if any(inside(v, a, b, c) for v in idx if v not in (a, b, c)):
                continue
            tris.append((a, b, c))
            idx.pop(k)
            clipped = True
            break
        if not clipped:                                    # numerical trouble: drop a collinear vertex
            tris_left = len(idx)
            idx.pop(int(np.argmin([abs(cross(idx[k - 1], idx[k], idx[(k + 1) % tris_left])) for k in range(tris_left)])))
    if len(idx) == 3:
        tris.append(tuple(idx))
    return np.asarray(tris, int)


def _quad(mesh, p0, p1, z0, z1):
    """Outward-facing wall quad above the CCW footprint edge p0->p1 (normal = (dy, -dx))."""
    v, f = mesh
    base = len(v)
    v.extend([(p0[0], p0[1], z0), (p1[0], p1[1], z0), (p1[0], p1[1], z1), (p0[0], p0[1], z1)])
    f.extend([(base, base + 1, base + 2), (base, base + 2, base + 3)])


def building_meshes(b: PolygonBuilding) -> Dict[str, Tuple[np.ndarray, np.ndarray]]:
    """material -> (vertices (V,3), faces (F,3)) for one building."""
    rho = GLAZING[b.facade]
    parts = {"concrete": ([], []), "glass": ([], [])}
    ring = b.poly if b.area > 0 else b.poly[::-1]
    H, fh = b.height, b.floor_height
    for i in range(len(ring)):
        p0, p1 = ring[i], ring[(i + 1) % len(ring)]
        if np.hypot(*(p1 - p0)) < 1e-6:
            continue
        if rho <= 0.0:
            _quad(parts["concrete"], p0, p1, 0.0, H)
        elif rho >= 1.0:
            _quad(parts["glass"], p0, p1, 0.0, H)
        else:
            solid = (1.0 - rho) * fh
            below, above = SPANDREL_BELOW_FRAC * solid, (1.0 - SPANDREL_BELOW_FRAC) * solid
            for k in range(b.n_floors):
                z0 = k * fh
                _quad(parts["concrete"], p0, p1, z0, z0 + below)
                _quad(parts["glass"], p0, p1, z0 + below, z0 + below + rho * fh)
                _quad(parts["concrete"], p0, p1, z0 + below + rho * fh, z0 + fh)
    tri = triangulate_polygon(ring)
    v, f = parts["concrete"]
    base = len(v)
    v.extend([(x, y, H) for x, y in ring])
    f.extend([(base + a, base + bb, base + c) for a, bb, c in tri])
    return {m: (np.asarray(v, float).reshape(-1, 3), np.asarray(f, int).reshape(-1, 3))
            for m, (v, f) in parts.items() if f}


def scene_meshes(scene: Scene) -> Dict[str, Tuple[np.ndarray, np.ndarray]]:
    """Merge every building's meshes per material."""
    out: Dict[str, List] = {}
    for b in scene.buildings:
        for m, (v, f) in building_meshes(b).items():
            vs, fs, n = out.setdefault(m, [[], [], 0])
            vs.append(v)
            fs.append(f + n)
            out[m][2] = n + len(v)
    return {m: (np.concatenate(vs, 0), np.concatenate(fs, 0)) for m, (vs, fs, _) in out.items()}


def triangle_areas(v: np.ndarray, f: np.ndarray) -> np.ndarray:
    return 0.5 * np.linalg.norm(np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]]), axis=1)
