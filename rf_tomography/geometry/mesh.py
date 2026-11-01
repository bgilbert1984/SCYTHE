"""Triangle meshes for extruded buildings: roofs, spandrel walls and per-floor window bands.

numpy only. The Sionna side (propagation/sionna_scene.py) writes these to OBJ files. Material
tags: "concrete" (roofs, spandrels, solid walls) and "glass" (windows).

Facade styles set the glazing ratio rho = glazed area / wall area:

    concrete       0.00  solid concrete wall (the earlier closed-box model)
    masonry        0.25  punched-window masonry (pre-war)
    curtain        0.60  curtain-wall office tower
    glass_curtain  0.80  modern glass tower
    glass_solid    1.00  all glass (the earlier glass closed-box model)

Window patterns (same rho, same glass and opaque materials, so only the shape of the glazing
differs; the glazed area is matched exactly):

    ribbon     continuous horizontal band, height rho * floor height (the original model)
    grid       punched windows, one per BAY_M-wide bay, aligned on every floor
    staggered  as grid, alternate floors shifted by half a bay (half windows at the wall ends)
    random     as grid, each window's position jittered randomly inside its bay (seeded)

A punched window is sqrt(rho) of the bay width and sqrt(rho) of the floor height. Walls are tiled
into non-overlapping quads (no coplanar faces): floor bands, piers and windows.

`assign_facades(scene, policy)` sets each building's style: "concrete" / "glass_solid" for the
uniform baselines, or "mixed", a heuristic from construction year and height (ASSUMPTION, not
measured): before 1945 masonry; from 1945 curtain wall when >= 60 m else masonry; from 2000
glass curtain; unknown year by height.
"""
from __future__ import annotations

import math
import zlib
from dataclasses import replace
from typing import Dict, List, Tuple

import numpy as np

from .scene import PolygonBuilding, Scene

BAY_M = 3.0                    # horizontal module of punched windows
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


PATTERNS = ("ribbon", "grid", "staggered", "random")


def with_pattern(scene: Scene, pattern: str) -> Scene:
    """Same footprints and styles, different window pattern (only affects glazed styles)."""
    if pattern not in PATTERNS:
        raise ValueError(f"unknown window pattern {pattern!r}")
    return Scene(tuple(replace(b, pattern=pattern) for b in scene.buildings))


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


def _hrect(mesh, p0, ux, x0, x1, z0, z1):
    """Quad on a wall: horizontal span [x0, x1] along edge direction ux from p0, heights [z0, z1]."""
    if x1 - x0 < 1e-9 or z1 - z0 < 1e-9:
        return
    _quad(mesh, p0 + ux * x0, p0 + ux * x1, z0, z1)


def _punched_wall(parts, p0, p1, b: PolygonBuilding, rho: float, wall_index: int) -> None:
    L = float(np.hypot(*(p1 - p0)))
    ux = (p1 - p0) / L
    n = max(1, int(round(L / BAY_M)))
    bay = L / n
    fh = b.floor_height
    wf = hf = math.sqrt(rho)
    w, h = wf * bay, hf * fh
    below = SPANDREL_BELOW_FRAC * (1.0 - hf) * fh
    rng = np.random.default_rng([zlib.crc32(b.name.encode()), wall_index])
    conc, glass = parts["concrete"], parts["glass"]
    prev_head = 0.0
    for k in range(b.n_floors):
        sill = k * fh + below
        head = sill + h
        _hrect(conc, p0, ux, 0.0, L, prev_head, sill)               # band between floors
        prev_head = head
        if b.pattern == "staggered" and k % 2 == 1:
            spans = [(0.0, w / 2)] + [(i * bay - w / 2, i * bay + w / 2) for i in range(1, n)] + [(L - w / 2, L)]
        elif b.pattern == "random":
            slack = bay - w
            spans = [(i * bay + (slack * rng.random()), i * bay + (slack * rng.random()) + w) for i in range(n)]
            spans = [(a, a + w) for a, _ in spans]
        else:                                                       # grid (and even floors of staggered)
            spans = [(i * bay + (bay - w) / 2, i * bay + (bay + w) / 2) for i in range(n)]
        x = 0.0
        for a, e in spans:
            _hrect(conc, p0, ux, x, a, sill, head)                  # pier
            _hrect(glass, p0, ux, a, e, sill, head)                 # window
            x = e
        _hrect(conc, p0, ux, x, L, sill, head)
    _hrect(conc, p0, ux, 0.0, L, prev_head, b.height)               # top band


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
        elif b.pattern != "ribbon":
            _punched_wall(parts, p0, p1, b, rho, i)
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
