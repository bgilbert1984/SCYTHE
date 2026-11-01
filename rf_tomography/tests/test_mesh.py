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



class PunchedWindows(unittest.TestCase):
    """Patterns differ only in the SHAPE of the glazing: area matched, walls tiled without overlap."""

    @staticmethod
    def tower(pattern, facade="curtain", n=12, h=44.4, seed_name="T"):
        return PolygonBuilding(seed_name, ((0, 0), (31, 0), (31, 17), (0, 17)), h, n, facade, pattern=pattern)

    def areas(self, b):
        m = building_meshes(b)
        return (triangle_areas(*m["glass"]).sum() if "glass" in m else 0.0,
                triangle_areas(*m["concrete"]).sum() - abs(b.area))        # concrete minus the roof

    def test_glazed_area_is_identical_for_every_pattern_and_equals_rho_times_wall_area(self):
        for facade in ("masonry", "curtain", "glass_curtain"):
            walls = 2 * (31 + 17) * 44.4
            for pattern in ("ribbon", "grid", "staggered", "random"):
                g, c = self.areas(self.tower(pattern, facade))
                self.assertAlmostEqual(g, GLAZING[facade] * walls, places=6, msg=(facade, pattern))
                self.assertAlmostEqual(g + c, walls, places=6, msg=(facade, pattern))     # no gaps, no overlap in area

    def test_walls_are_tiled_exactly_once_by_point_sampling(self):
        rng = np.random.default_rng(0)
        for pattern in ("grid", "staggered", "random"):
            b = self.tower(pattern)
            m = building_meshes(b)
            # south wall: y = 0, x in [0, 31], z in [0, H]: count the (axis-aligned) wall quads covering random points
            quads = []
            for mat in ("concrete", "glass"):
                vv, ff = m[mat]
                for tri in ff:
                    p = vv[tri]
                    if np.allclose(p[:, 1], 0.0):
                        quads.append((p[:, 0].min(), p[:, 0].max(), p[:, 2].min(), p[:, 2].max(), mat))
            # a quad = two triangles with identical bounding box: counted twice, so divide by two
            for _ in range(1500):
                x, z = rng.uniform(0.01, 30.99), rng.uniform(0.01, 44.39)
                hit = sum(1 for x0, x1, z0, z1, _m in quads if x0 < x < x1 and z0 < z < z1)
                self.assertEqual(hit, 2, (pattern, x, z))

    def test_windows_stay_inside_their_wall_and_inside_their_floor(self):
        for pattern in ("grid", "staggered", "random"):
            b = self.tower(pattern)
            v, f = building_meshes(b)["glass"]
            south = np.isclose(v[:, 1], 0.0)
            self.assertTrue(np.all(v[:, 0] >= -1e-9) and np.all(v[:, 0] <= 31 + 1e-9))
            fh = b.floor_height
            local = np.mod(v[:, 2], fh)
            rho = GLAZING["curtain"]
            sill = SPAN * (1 - math.sqrt(rho)) * fh
            ok = np.isclose(local, sill, atol=1e-6) | np.isclose(local, sill + math.sqrt(rho) * fh, atol=1e-6)
            self.assertTrue(ok.all())

    def test_stagger_shifts_alternate_floors_and_random_is_seeded_and_jittered(self):
        def starts(b, floor):
            v, f = building_meshes(b)["glass"]
            sel = np.isclose(v[:, 1], 0.0) & (np.floor(v[:, 2] / b.floor_height + 1e-6) == floor)
            return np.unique(np.round(v[sel, 0], 6))
        g = self.tower("grid")
        s = self.tower("staggered")
        self.assertTrue(np.allclose(starts(g, 2), starts(g, 3)))
        self.assertFalse(np.allclose(starts(s, 2)[:3], starts(s, 3)[:3]))
        r1, r2, r3 = self.tower("random"), self.tower("random"), self.tower("random", seed_name="U")
        self.assertTrue(np.array_equal(starts(r1, 4), starts(r2, 4)))                    # deterministic
        self.assertFalse(np.array_equal(starts(r1, 4), starts(r3, 4)))                   # depends on the building
        self.assertFalse(np.allclose(starts(r1, 4), starts(g, 4)))                       # actually jittered

    def test_solid_styles_ignore_the_pattern_and_with_pattern_only_changes_pattern(self):
        from rf_tomography.geometry.mesh import with_pattern
        a = building_meshes(self.tower("grid", "concrete"))
        b = building_meshes(self.tower("ribbon", "concrete"))
        self.assertEqual(len(a["concrete"][1]), len(b["concrete"][1]))
        sc = with_pattern(Scene((self.tower("ribbon"),)), "staggered")
        self.assertEqual((sc.buildings[0].pattern, sc.buildings[0].facade), ("staggered", "curtain"))
        with self.assertRaises(ValueError):
            with_pattern(sc, "diagonal")


SPAN = 0.6   # SPANDREL_BELOW_FRAC (kept literal here so the test does not just mirror the implementation)


if __name__ == "__main__":
    unittest.main()
