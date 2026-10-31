"""Rebuild the scene and receiver sites a saved PathTable was traced in (from its metadata)."""
from __future__ import annotations

import ast
from typing import List, Tuple

import numpy as np

from ..geometry.real import midtown_scene, real_sites, target_buildings
from ..geometry.scene import synthetic_manhattan_block
from ..measurements.raytraced import PathTable
from ..placement.sites import TIERS, Candidate, candidate_sites


def table_meta(T: PathTable) -> dict:
    return ast.literal_eval(T.meta["repr"])


def scene_and_sites(meta: dict):
    """(scene, sites, hosts): hosts are the buildings emitters were placed in."""
    if meta.get("scene") == "midtown":
        scene = midtown_scene(meta.get("policy") or "mixed")
        return scene, real_sites(scene), target_buildings(scene)
    scene = synthetic_manhattan_block()
    return scene, candidate_sites(scene), list(scene.buildings)


def candidates_from(sites) -> List[Candidate]:
    return [Candidate(s, t) for s in sites for t in TIERS]


def grid_spacing(b, max_cells: int = 600, lo: float = 2.0, hi: float = 5.0) -> float:
    """Candidate-grid spacing: 2 m as before, coarser for big real footprints (<= ~max_cells xy cells)."""
    area = abs(getattr(b, "area", b.width * b.depth))
    return float(np.clip(np.sqrt(area / max_cells), lo, hi))


def spread_street_arrays(cands, k: int = 5) -> List[int]:
    """k street array candidates far apart from each other (farthest-point order from the centroid)."""
    idx = [i for i, c in enumerate(cands) if c.site.kind == "street" and c.tier == "array"]
    pos = np.array([cands[i].site.pos[:2] for i in idx])
    first = int(np.argmin(np.hypot(*(pos - pos.mean(0)).T)))
    chosen = [first]
    d = np.hypot(*(pos - pos[first]).T)
    while len(chosen) < k:
        chosen.append(int(np.argmax(d)))
        d = np.minimum(d, np.hypot(*(pos - pos[chosen[-1]]).T))
    return [idx[j] for j in chosen]
