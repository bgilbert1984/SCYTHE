import math
import unittest

import numpy as np

from rf_tomography.geometry.mesh import (GLAZING, assign_facades, building_meshes, scene_meshes,
                                         style_for, triangle_areas, triangulate_polygon)
from rf_tomography.geometry.scene import PolygonBuilding, Scene

L_RING = ((0, 0), (40, 0), (40, 15), (15, 15), (15, 40), (0, 40))


def poly_area(p):
    p = np.asarray(p, float)
    return 0.5 * abs(np.dot(p[:, 0], np.roll(p[:, 1], -1)) - np.dot(p[:, 1], np.roll(p[:, 0], -1)))


class Triangulation(unittest.TestCase):
    def test_area_is_preserved_for_convex_concave_and_clockwise_input(self):
        shapes = [((0, 0), (10, 0), (10, 5), (0, 5)), L_RING, L_RING[::-1],
                  ((0, 0), (10, 0), (10, 10), (5, 4), (0, 10))]                 # arrow-head concavity
        for ring in shapes:
            tri = triangulate_polygon(np.asarray(ring, float))
            self.assertEqual(len(tri), len(ring) - 2)
            pts = np.asarray(ring, float)
            area = sum(0.5 * ((pts[b, 0] - pts[a, 0]) * (pts[c, 1] - pts[a, 1]) -
                              (pts[b, 1] - pts[a, 1]) * (pts[c, 0] - pts[a, 0])) for a, b, c in tri)
            self.assertAlmostEqual(area, poly_area(ring), places=9)            # all CCW, no overlap

    def test_no_triangle_leaves_the_polygon(self):
        from rf_tomography.geometry.scene import _point_in_polygon
        pts = np.asarray(L_RING, float)
        for a, b, c in triangulate_polygon(pts):
            cx, cy = pts[[a, b, c]].mean(0)
            self.assertTrue(_point_in_polygon(pts, cx, cy))


class Meshes(unittest.TestCase):
    def b(self, facade, n=10, h=37.0):
        return PolygonBuilding("T", L_RING, h, n, facade)

    def test_wall_and_roof_areas(self):
        for facade in GLAZING:
            m = building_meshes(self.b(facade))
            areas = {k: triangle_areas(*v).sum() for k, v in m.items()}
            perim = sum(math.hypot(L_RING[(i + 1) % 6][0] - L_RING[i][0], L_RING[(i + 1) % 6][1] - L_RING[i][1]) for i in range(6))
            walls = perim * 37.0
            glass = areas.get("glass", 0.0)
            self.assertAlmostEqual(glass, GLAZING[facade] * walls, places=6)
            self.assertAlmostEqual(areas.get("concrete", 0.0) - poly_area(L_RING), walls - glass, places=6)

    def test_walls_face_outward(self):
        b = self.b("concrete", n=1, h=10.0)
        v, f = building_meshes(b)["concrete"]
        n = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]])
        c = v[f].mean(1)
        walls = np.abs(n[:, 2]) < 1e-9
        for ni, ci in zip(n[walls], c[walls]):
            # stepping slightly along the normal must leave the footprint
            from rf_tomography.geometry.scene import _point_in_polygon
            q = ci[:2] + 0.05 * ni[:2] / np.linalg.norm(ni[:2])
            self.assertFalse(_point_in_polygon(np.asarray(L_RING, float), *q))
        self.assertTrue(np.all(n[~walls][:, 2] > 0))                          # roof faces up

    def test_windows_alternate_per_floor_with_the_right_heights(self):
        b = PolygonBuilding("T", ((0, 0), (10, 0), (10, 8), (0, 8)), 30.0, 10, "curtain")
        v, f = building_meshes(b)["glass"]
        z = np.unique(np.round(v[:, 2], 9))
        fh, rho = 3.0, GLAZING["curtain"]
        sill = 0.6 * (1 - rho) * fh
        np.testing.assert_allclose(z[:2], [sill, sill + rho * fh])
        self.assertEqual(len(f), 10 * 4 * 2)                                  # floors x walls x 2 triangles

    def test_scene_meshes_merge_and_policies(self):
        s = Scene((self.b("concrete"), PolygonBuilding("U", ((50, 0), (60, 0), (60, 10), (50, 10)), 20.0, 5, "glass_solid", 2010)))
        m = scene_meshes(s)
        self.assertEqual(set(m), {"concrete", "glass"})
        v, f = m["concrete"]
        self.assertTrue(f.max() < len(v))
        old = PolygonBuilding("O", L_RING, 40.0, 10, year=1920)
        tall = PolygonBuilding("K", L_RING, 120.0, 30, year=1975)
        new = PolygonBuilding("N", L_RING, 120.0, 30, year=2015)
        self.assertEqual((style_for(old), style_for(tall), style_for(new)), ("masonry", "curtain", "glass_curtain"))
        self.assertEqual({b.facade for b in assign_facades(Scene((old, tall, new)), "glass_solid").buildings}, {"glass_solid"})
        self.assertEqual([b.facade for b in assign_facades(Scene((old, tall, new)), "mixed").buildings],
                         ["masonry", "curtain", "glass_curtain"])


if __name__ == "__main__":
    unittest.main()
