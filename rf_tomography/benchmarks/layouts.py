"""Receiver layouts for the synthetic block (Phase A)."""
from __future__ import annotations

from ..geometry.scene import Scene
from ..measurements.models import Receiver

SYNC = 10e-9  # GPSDO-class sync, 10 ns


def _street(sync, hap, vap):
    pts = [(0, 0), (80, 0), (-80, 0), (0, 80), (0, -80)]
    return [Receiver(f"S{i}", (x, y, 1.5), None, hap, vap, sync)
            for i, (x, y) in enumerate(pts)]


def layouts(scene: Scene):
    roofB = scene.by_name("B").height
    roofD = scene.by_name("D").height
    roofs = [
        Receiver("RB", (22.0, -40.0, roofB + 1.0), "B", 3.0, 3.0, SYNC),
        Receiver("RD", (22.0, 40.0, roofD + 1.0), "D", 3.0, 3.0, SYNC),
    ]
    return {
        "street_rss_az": _street(None, 1.0, 0.0),
        "street_tdoa": _street(SYNC, 1.0, 0.0),
        "street_vertical_array": _street(SYNC, 3.0, 3.0),
        "street_plus_rooftops": _street(SYNC, 3.0, 3.0) + roofs,
    }
