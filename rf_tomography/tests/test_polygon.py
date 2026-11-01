import unittest

import numpy as np

from rf_tomography.geometry.scene import Building, PolygonBuilding, Scene
from rf_tomography.uncertainty.floor_posterior import CandidateGrid


def rect_polygon(b: Building, name="P"):
    x0, y0 = b.cx - b.width / 2, b.cy - b.depth / 2
    x1, y1 = b.cx + b.width / 2, b.cy + b.depth / 2
    return PolygonBuilding(name, ((x0, y0), (x1, y0), (x1, y1), (x0, y1)), b.height, b.n_floors)


L_SHAPE = PolygonBuilding("L", ((0, 0), (40, 0), (40, 15), (15, 15), (15, 40), (0, 40)), 60.0, 16)


class PolygonVsBox(unittest.TestCase):
    def test_rectangular_polygon_blocks_exactly_like_a_box(self):
        b = Building("B", 10.0, -5.0, 30.0, 20.0, 12)
        pb = rect_polygon(b)
        rng = np.random.default_rng(0)
        agree = n_block = 0
        for _ in range(6000):
            p = rng.uniform(-60, 60, 3) * np.array([1, 1, 0.8]) + np.array([0, 0, 25])
            q = rng.uniform(-60, 60, 3) * np.array([1, 1, 0.8]) + np.array([0, 0, 25])
            a, c = b.blocks(p, q), pb.blocks(p, q)
            agree += a == c
            n_block += a
        self.assertEqual(agree, 6000)
        self.assertGreater(n_block, 500)          # the comparison actually exercised both outcomes
        self.assertLess(n_block, 5500)

    def test_geometry_properties_match(self):
        b = Building("B", 10.0, -5.0, 30.0, 20.0, 12)
        pb = rect_polygon(b)
        self.assertAlmostEqual(pb.area, 600.0)
        self.assertAlmostEqual(pb.cx, 10.0)
        self.assertAlmostEqual(pb.width, 30.0)
        self.assertAlmostEqual(pb.floor_height, b.floor_height)
        np.testing.assert_allclose(pb.floor_centers_z(), b.floor_centers_z())


class NonConvex(unittest.TestCase):
    def test_notch_is_open_arm_is_blocked_roof_is_clear(self):
        # the L has its notch around (30, 30); both arms are solid
        self.assertFalse(L_SHAPE.blocks((50, 30, 10), (20.5, 30, 10)))      # ends in the notch (outside the L)
        self.assertTrue(L_SHAPE.blocks((50, 7, 10), (-10, 7, 10)))          # through the long arm
        self.assertTrue(L_SHAPE.blocks((7, 50, 10), (7, -10, 10)))          # through the tall arm
        self.assertTrue(L_SHAPE.blocks((50, 30, 10), (-10, 30, 10)))        # clips the tall arm at y=30
        self.assertFalse(L_SHAPE.blocks((50, 7, 70), (-10, 7, 70)))         # above the roof
        self.assertFalse(L_SHAPE.blocks((50, 7, -5), (-10, 7, -5)))         # below ground
        # a chord across the notch that never enters the L's interior
        self.assertFalse(L_SHAPE.blocks((41, 41, 10), (16, 16, 10)))
        # inside the building to outside: blocked (it crosses the facade)
        self.assertTrue(L_SHAPE.blocks((5, 5, 10), (60, 5, 10)))

    def test_contains_and_sampling_respect_the_footprint_and_inset(self):
        rng = np.random.default_rng(1)
        for _ in range(400):
            x, y = L_SHAPE.sample_xy(rng, inset=2.0)
            self.assertTrue(L_SHAPE.contains_xy(x, y))
            self.assertFalse(x > 15 and y > 15)                               # never in the notch
            self.assertGreaterEqual(min(x, y, 40 - x if y < 15 else 15 - x, 40 - y if x < 15 else 15 - y), 2.0 - 1e-9)
        self.assertAlmostEqual(L_SHAPE.area, 40 * 15 + 15 * 25)

    def test_grid_is_inside_and_candidate_grid_shape(self):
        xy = L_SHAPE.grid_xy(3.0)
        self.assertTrue(all(L_SHAPE.contains_xy(x, y) for x, y in xy))
        g = CandidateGrid.for_building(L_SHAPE, 3.0)
        self.assertEqual(g.points.shape, (len(xy) * 16, 3))
        self.assertEqual(g.n_floors, 16)

    def test_mixed_scene_and_exclusions(self):
        box = Building("X", 100.0, 0.0, 20.0, 20.0, 5)
        s = Scene((L_SHAPE, box))
        self.assertTrue(s.segment_blocked((7, 50, 10), (7, -10, 10)))
        self.assertFalse(s.segment_blocked((7, 50, 10), (7, -10, 10), exclude=("L",)))
        self.assertTrue(s.segment_blocked((50, 0, 5), (150, 0, 5)))
        self.assertFalse(s.segment_blocked((50, 0, 5), (150, 0, 5), exclude=("X",)))


if __name__ == "__main__":
    unittest.main()
