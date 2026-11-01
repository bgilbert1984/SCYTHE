"""Candidate receiver sites, capability tiers, and (assumed) deployment costs."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from ..geometry.scene import Scene
from ..measurements.models import Receiver

SYNC = 10e-9

# tier -> (horizontal aperture m, vertical aperture m, sync sigma s, cost)
TIERS = {
    "rss_az": (1.0, 0.0, None, 1.0),
    "tdoa": (1.0, 0.0, SYNC, 2.0),
    "array": (3.0, 3.0, SYNC, 4.0),
}
# site class -> access cost (ASSUMPTION: roof access and facade/tenant cooperation cost more)
ACCESS_COST = {"street": 0.0, "roof": 1.0, "facade": 2.0}


@dataclass(frozen=True)
class Site:
    name: str
    pos: Tuple[float, float, float]
    kind: str  # street | roof | facade
    host: Optional[str] = None


@dataclass(frozen=True)
class Candidate:
    site: Site
    tier: str

    @property
    def cost(self) -> float:
        return TIERS[self.tier][3] + ACCESS_COST[self.site.kind]

    @property
    def label(self) -> str:
        return f"{self.site.name}:{self.tier}"

    def receiver(self) -> Receiver:
        ha, va, sync, _ = TIERS[self.tier]
        return Receiver(self.label, self.site.pos, self.site.host, ha, va, sync)


def candidate_sites(scene: Scene) -> List[Site]:
    sites: List[Site] = []
    for t in range(-120, 121, 20):  # street corridors x=0 and y=0
        sites.append(Site(f"Sx{t}", (float(t), 0.0, 1.5), "street"))
        if t != 0:
            sites.append(Site(f"Sy{t}", (0.0, float(t), 1.5), "street"))
    for b in scene.buildings:
        for sx in (-1, 1):
            for sy in (-1, 1):  # four roof corners, inset 3 m, 1 m above roof
                sites.append(Site(f"R{b.name}{'+' if sx > 0 else '-'}{'+' if sy > 0 else '-'}",
                                  (b.cx + sx * (b.width / 2 - 3), b.cy + sy * (b.depth / 2 - 3),
                                   b.height + 1.0), "roof", b.name))
        # facades toward the intersection (x=0 and y=0 corridors), two heights
        fx = b.cx - (b.width / 2 if b.cx > 0 else -b.width / 2)
        fy = b.cy - (b.depth / 2 if b.cy > 0 else -b.depth / 2)
        for frac, tag in ((1 / 3, "L"), (2 / 3, "H")):
            z = b.height * frac
            sites.append(Site(f"F{b.name}x{tag}", (fx, b.cy, z), "facade", b.name))
            sites.append(Site(f"F{b.name}y{tag}", (b.cx, fy, z), "facade", b.name))
    return sites


def all_candidates(scene: Scene) -> List[Candidate]:
    return [Candidate(s, t) for s in candidate_sites(scene) for t in TIERS]
