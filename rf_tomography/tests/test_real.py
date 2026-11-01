import math
import unittest

import numpy as np

from rf_tomography.benchmarks.scenes import grid_spacing, spread_street_arrays, candidates_from
from rf_tomography.geometry.mesh import assign_facades
from rf_tomography.geometry.real import (FLOOR_M, FT, ORIGIN, _is_simple, _simplify_ring, load_buildings,
                                         midtown_scene, parse, real_sites, target_buildings, to_enu)
from rf_tomography.geometry.scene import PolygonBuilding, _dist_to_edges


def rec(lon0, lat0, w_deg, h_deg, height_ft="300", year="1960", bin_="1", extra_pts=()):
    ring = [[lon0, lat0], [lon0 + w_deg, lat0], [lon0 + w_deg, lat0 + h_deg], [lon0, lat0 + h_deg], [lon0, lat0]]
    for p in extra_pts:
        ring.insert(1, list(p))
    return {"bin": bin_, "height_roof": height_ft, "construction_year": year,
            "the_geom": {"type": "MultiPolygon", "coordinates": [[ring]]}}


class Projection(unittest.TestCase):
    def test_enu_scale_and_orientation(self):
        x, y = to_enu(ORIGIN[1], ORIGIN[0] + 0.001)
        self.assertAlmostEqual(x, 0.0, places=6)
        self.assertAlmostEqual(y, 110.574, places=2)                    # 0.001 deg of latitude
        x, y = to_enu(ORIGIN[1] + 0.001, ORIGIN[0])
        self.assertAlmostEqual(x, 111.32 * math.cos(math.radians(ORIGIN[0])), places=2)
        self.assertAlmostEqual(y, 0.0, places=6)


class Parsing(unittest.TestCase):
    def test_height_floors_orientation_and_naming(self):
        lon, lat = ORIGIN[1] + 0.0003, ORIGIN[0] + 0.0003
        b, st = parse([rec(lon, lat, 0.0002, 0.0002, height_ft="300", year="1931", bin_="42")])
        self.assertEqual(len(b), 1)
        self.assertEqual(b[0].name, "N42")
        self.assertAlmostEqual(b[0].height, 300 * FT)
        self.assertEqual(b[0].n_floors, round(300 * FT / FLOOR_M))
        self.assertEqual(b[0].year, 1931)
        self.assertGreater(b[0].area, 0)                                # counter-clockwise
        self.assertAlmostEqual(b[0].width, 0.0002 * 111_320 * math.cos(math.radians(ORIGIN[0])), delta=0.05)

    def test_drops_small_outside_and_flags_missing_height(self):
        lon, lat = ORIGIN[1] + 0.0002, ORIGIN[0] + 0.0002
        recs = [rec(lon, lat, 0.00002, 0.00002),                         # ~2 m x 2 m
                rec(ORIGIN[1] + 0.01, lat, 0.0002, 0.0002),              # ~800 m away
                rec(lon, lat, 0.0002, 0.0002, height_ft="0", bin_="9")]  # no height
        b, st = parse(recs)
        self.assertEqual((st["dropped_small"], st["dropped_outside"], st["no_height"]), (1, 1, 1))
        self.assertEqual(len(b), 1)
        self.assertFalse(b[0].height_known)
        self.assertAlmostEqual(b[0].height, 12.0)

    def test_simplification_removes_collinear_points_and_keeps_shape(self):
        ring = np.array([[0, 0], [5, 0.01], [10, 0], [10, 10], [0, 10]], float)
        out = _simplify_ring(ring, 0.6)
        self.assertEqual(len(out), 4)
        self.assertTrue(_is_simple(out))
        self.assertFalse(_is_simple(np.array([[0, 0], [10, 10], [10, 0], [0, 10]], float)))   # bow-tie


class CommittedExtract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scene = midtown_scene("mixed")

    def test_scene_is_plausible_midtown(self):
        b = self.scene.buildings
        self.assertGreater(len(b), 100)
        self.assertGreater(max(x.height for x in b), 200.0)
        self.assertGreater(sum(x.n_floors >= 20 for x in b), 20)
        for x in b:
            self.assertGreater(x.area, 0)                                # all CCW
            self.assertGreaterEqual(len(x.footprint), 3)
            self.assertTrue(_is_simple(x.poly), x.name)
        self.assertEqual(len({x.name for x in b}), len(b))               # unique names

    def test_policies(self):
        self.assertEqual({x.facade for x in assign_facades(self.scene, "concrete").buildings}, {"concrete"})
        self.assertEqual({x.facade for x in assign_facades(self.scene, "glass_solid").buildings}, {"glass_solid"})
        self.assertGreater(len({x.facade for x in self.scene.buildings}), 1)          # mixed

    def test_targets_are_tall_known_and_sorted(self):
        t = target_buildings(self.scene)
        self.assertEqual(len(t), 12)
        self.assertTrue(all(x.n_floors >= 20 and x.height_known for x in t))
        self.assertEqual([x.height for x in t], sorted([x.height for x in t], reverse=True))

    def test_sites_are_deterministic_clear_of_walls_and_rooftops_are_on_roofs(self):
        a, b = real_sites(self.scene), real_sites(self.scene)
        self.assertEqual([(s.name, s.pos) for s in a], [(s.name, s.pos) for s in b])
        by = {x.name: x for x in self.scene.buildings}
        for s in a:
            if s.kind == "street":
                self.assertEqual(s.pos[2], 1.5)
                for x in self.scene.buildings:
                    self.assertFalse(x.contains_xy(*s.pos[:2]))
                    self.assertGreaterEqual(_dist_to_edges(x.poly, *s.pos[:2]), 3.0 - 1e-9)
            else:
                h = by[s.host]
                self.assertAlmostEqual(s.pos[2], h.height + 1.0)
                self.assertTrue(h.contains_xy(*s.pos[:2]))
        self.assertEqual(sum(s.kind == "roof" for s in a), 14)
        self.assertEqual(sum(s.kind == "street" for s in a), 40)

    def test_helpers(self):
        c = candidates_from(real_sites(self.scene))
        idx = spread_street_arrays(c, 5)
        self.assertEqual(len(set(idx)), 5)
        self.assertTrue(all(c[i].site.kind == "street" and c[i].tier == "array" for i in idx))
        big = max(self.scene.buildings, key=lambda x: x.area)
        self.assertLessEqual(grid_spacing(big), 5.0)
        self.assertGreaterEqual(grid_spacing(big), 2.0)
        self.assertEqual(grid_spacing(PolygonBuilding("s", ((0, 0), (10, 0), (10, 10), (0, 10)), 10.0, 3)), 2.0)


if __name__ == "__main__":
    unittest.main()
