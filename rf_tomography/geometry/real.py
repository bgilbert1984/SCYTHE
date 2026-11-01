"""Real building footprints (NYC Open Data) -> PolygonBuilding scene, plus receiver sites.

Data: NYC Building Footprints (dataset 5zhs-2jue, Office of Technology and Innovation), public
domain. A trimmed extract around 5th Ave / 42nd St is committed in data/ so everything runs
offline; `--fetch` re-downloads it. Footprints are photogrammetric outlines with the roof height
(feet) above ground; ground elevation is ignored (flat ground at z = 0), holes in footprints
(courtyards) are ignored, MultiPolygon parts become separate buildings that share a height.
Rooftop structures and setbacks are NOT modelled: each building is one prism of its roof height,
so tall towers with setbacks are over-simplified.

    .venv/bin/python -m rf_tomography.geometry.real --fetch    # needs network
"""
from __future__ import annotations

import argparse
import json
import math
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

from .scene import PolygonBuilding, Scene, _dist_to_edges, _point_in_polygon

DATA = Path(__file__).parent / "data" / "nyc_midtown_footprints.json"
ORIGIN = (40.7532, -73.9825)                    # lat, lon: near 5th Ave & 42nd St (Bryant Park)
BBOX = (40.7508, -73.9858, 40.7557, -73.9792)   # lat_min, lon_min, lat_max, lon_max (~540 m x 560 m)
FT = 0.3048
FLOOR_M = 3.7
DATASET = "https://data.cityofnewyork.us/resource/5zhs-2jue.json"
KEEP = ("bin", "doitt_id", "height_roof", "ground_elevation", "construction_year", "the_geom")


def fetch(path: Path = DATA, bbox=BBOX) -> int:
    q = urllib.parse.urlencode({"$where": "within_box(the_geom, %f, %f, %f, %f)" % bbox, "$limit": 5000})
    with urllib.request.urlopen(f"{DATASET}?{q}", timeout=60) as r:
        recs = json.loads(r.read())
    out = {"source": "NYC Open Data, Building Footprints (5zhs-2jue), Office of Technology and Innovation",
           "license": "NYC Open Data terms of use (public domain)", "bbox_lat_lon": list(bbox),
           "note": "trimmed to the fields rf_tomography uses; heights in feet",
           "records": [{k: r.get(k) for k in KEEP} for r in recs]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, separators=(",", ":")))
    return len(recs)


def to_enu(lon: float, lat: float, origin=ORIGIN) -> Tuple[float, float]:
    """Equirectangular projection about `origin`; error well under 0.1 m over 1 km."""
    lat0, lon0 = origin
    return ((lon - lon0) * 111_320.0 * math.cos(math.radians(lat0)), (lat - lat0) * 110_574.0)


def _rdp(pts: np.ndarray, tol: float) -> np.ndarray:
    """Douglas-Peucker for an open polyline."""
    if len(pts) < 3:
        return pts
    a, b = pts[0], pts[-1]
    ab = b - a
    n = np.hypot(*ab)
    rel = pts[1:-1] - a
    d = np.abs(ab[0] * rel[:, 1] - ab[1] * rel[:, 0]) / n if n > 1e-12 else np.hypot(*rel.T)
    i = int(np.argmax(d))
    if d[i] <= tol:
        return np.array([a, b])
    return np.vstack([_rdp(pts[:i + 2], tol)[:-1], _rdp(pts[i + 1:], tol)])


def _simplify_ring(ring: np.ndarray, tol: float) -> np.ndarray:
    """Simplify a closed ring (first point not repeated) keeping it a polygon."""
    j = int(np.argmax(np.hypot(*(ring - ring[0]).T)))          # split at the farthest vertex
    left = _rdp(np.vstack([ring[:j + 1]]), tol)
    right = _rdp(np.vstack([ring[j:], ring[:1]]), tol)
    out = np.vstack([left[:-1], right[:-1]])
    return out if len(out) >= 3 else ring


def _is_simple(p: np.ndarray) -> bool:
    n = len(p)
    a, b = p, np.roll(p, -1, axis=0)

    def ccw(x, y, z):
        return (z[1] - x[1]) * (y[0] - x[0]) > (y[1] - x[1]) * (z[0] - x[0])

    for i in range(n):
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue
            if ccw(a[i], a[j], b[j]) != ccw(b[i], a[j], b[j]) and ccw(a[i], b[i], a[j]) != ccw(a[i], b[i], b[j]):
                return False
    return True


def parse(records: List[dict], origin=ORIGIN, radius_m: float = 260.0, min_area: float = 30.0,
          simplify_m: float = 0.6) -> Tuple[List[PolygonBuilding], Dict[str, int]]:
    out: List[PolygonBuilding] = []
    stats = {"records": len(records), "parts": 0, "dropped_small": 0, "dropped_invalid": 0,
             "dropped_outside": 0, "no_height": 0}
    for r in records:
        geom = r.get("the_geom") or {}
        polys = geom.get("coordinates", []) if geom.get("type") == "MultiPolygon" else (
            [geom.get("coordinates")] if geom.get("type") == "Polygon" else [])
        h_ft = float(r.get("height_roof") or 0.0)
        year = int(r["construction_year"]) if r.get("construction_year") not in (None, "") else None
        for k, poly in enumerate(polys):
            stats["parts"] += 1
            ring = np.array([to_enu(lon, lat, origin) for lon, lat in poly[0]], float)
            if np.allclose(ring[0], ring[-1]):
                ring = ring[:-1]
            ring = _simplify_ring(ring, simplify_m) if simplify_m > 0 else ring
            x, y = ring[:, 0], ring[:, 1]
            area2 = float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))
            if len(ring) < 3 or abs(area2) / 2 < min_area:
                stats["dropped_small"] += 1
                continue
            if area2 < 0:
                ring = ring[::-1]
            if not _is_simple(ring):
                stats["dropped_invalid"] += 1
                continue
            if math.hypot(*ring.mean(0)) > radius_m:
                stats["dropped_outside"] += 1
                continue
            known = h_ft > 0
            stats["no_height"] += not known
            height = h_ft * FT if known else 12.0
            n_floors = max(1, int(round(height / FLOOR_M)))
            name = f"N{r.get('bin') or r.get('doitt_id')}" + (f"p{k}" if len(polys) > 1 else "")
            out.append(PolygonBuilding(name, tuple((float(a), float(b)) for a, b in ring), float(height),
                                       n_floors, "concrete", year, known))
    return out, stats


def load_buildings(path: Path = DATA, **kw) -> Tuple[List[PolygonBuilding], Dict[str, int]]:
    return parse(json.loads(Path(path).read_text())["records"], **kw)


def midtown_scene(policy: str = "mixed", **kw) -> Scene:
    from .mesh import assign_facades
    b, _ = load_buildings(**kw)
    return assign_facades(Scene(tuple(b)), policy)


def target_buildings(scene: Scene, min_floors: int = 20, max_n: int = 12) -> List[PolygonBuilding]:
    """Tallest known-height buildings with at least `min_floors` floors (emitter hosts)."""
    c = [b for b in scene.buildings if b.height_known and b.n_floors >= min_floors]
    return sorted(c, key=lambda b: -b.height)[:max_n]


def real_sites(scene: Scene, n_street: int = 40, n_roof: int = 14, spacing_m: float = 12.0,
               clearance_m: float = 3.0):
    """Receiver sites on the real block: street points (1.5 m, >= clearance from every wall,
    picked by farthest-point sampling so they spread over the open space) and rooftop points
    (representative interior point of the tallest buildings, 1 m above the roof)."""
    from ..placement.sites import Site
    lo = np.min([b.box[0][:2] for b in scene.buildings], 0)
    hi = np.max([b.box[1][:2] for b in scene.buildings], 0)
    gx, gy = np.meshgrid(np.arange(lo[0], hi[0], spacing_m), np.arange(lo[1], hi[1], spacing_m))
    pts = np.stack([gx.ravel(), gy.ravel()], 1)
    free = []
    for p in pts:
        if any(b.contains_xy(*p) or _dist_to_edges(b.poly, *p) < clearance_m for b in scene.buildings
               if abs(b.cx - p[0]) < b.width + 20 and abs(b.cy - p[1]) < b.depth + 20):
            continue
        free.append(p)
    free = np.array(free)
    chosen = [int(np.argmin(np.hypot(*(free - free.mean(0)).T)))]
    d = np.hypot(*(free - free[chosen[0]]).T)
    while len(chosen) < min(n_street, len(free)):
        chosen.append(int(np.argmax(d)))
        d = np.minimum(d, np.hypot(*(free - free[chosen[-1]]).T))
    sites = [Site(f"S{i}", (float(free[j, 0]), float(free[j, 1]), 1.5), "street") for i, j in enumerate(sorted(chosen))]
    for b in sorted([b for b in scene.buildings if b.height_known], key=lambda b: -b.height)[:n_roof]:
        x, y = b.interior_point()
        sites.append(Site(f"R{b.name}", (x, y, b.height + 1.0), "roof", b.name))
    return sites


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true")
    a = ap.parse_args(argv)
    if a.fetch:
        print("fetched", fetch(), "records ->", DATA)
    b, st = load_buildings()
    print(st, "buildings", len(b), "max height m", round(max(x.height for x in b), 1))


if __name__ == "__main__":
    main()
