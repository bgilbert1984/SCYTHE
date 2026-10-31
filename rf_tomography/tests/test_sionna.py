"""Ray-tracer validation. Skipped unless the sionna-rt venv is active:

    source ~/.venvs/sionna-rt/env.sh
    PYTHONPATH=. ~/.venvs/sionna-rt/bin/python -m unittest rf_tomography.tests.test_sionna
"""
import math
import unittest
from dataclasses import replace

import numpy as np

try:
    import sionna.rt  # noqa: F401  (needs libatomic + libLLVM, see env.sh)
    from rf_tomography.propagation.sionna_scene import load_block, trace_paths
    HAVE = True
except Exception:  # sionna / mitsuba / drjit not importable here
    HAVE = False

from rf_tomography.geometry.scene import PolygonBuilding, Scene, synthetic_manhattan_block
from rf_tomography.localization.forward import mean_measurements
from rf_tomography.measurements.impairments import _facade_images
from rf_tomography.measurements.models import Receiver
from rf_tomography.measurements.raytraced import C, SPECULAR, link_observables
from rf_tomography.propagation.model import PropagationParams

F = 915e6
LAM = C / F


@unittest.skipUnless(HAVE, "sionna-rt not available in this interpreter")
class RayTracerAgainstPhysics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = synthetic_manhattan_block()
        cls.sc = load_block(cls.s, F)
        cls.prop = PropagationParams()

    def trace(self, tx, rx):
        return trace_paths(self.sc, np.asarray(tx, float), np.asarray(rx, float), tx_batch=4)

    def angles(self, tx, rx):
        r = Receiver("r", tuple(rx), None, 1.0, 1.0, None)
        m = mean_measurements(np.asarray(tx, float), [r], [(0, "az"), (0, "el")], self.prop)[0]
        return m[0], m[1]

    def test_free_space_los_gain_delay_and_angles(self):
        for tx, rx in (([0, 0, 15.0], [0, 70, 1.5]), ([0, -60, 30.0], [0, 60, 2.0]),
                       ([-70, 0, 12.0], [70, 0, 1.5])):
            T = self.trace([tx], [rx])
            p = T.power[0, 0]
            k = int(np.where((T.depth[0, 0] == 0) & (p > 0))[0][0])   # the true LOS path
            d = float(np.linalg.norm(np.array(tx) - np.array(rx)))
            self.assertAlmostEqual(T.tau_s[0, 0, k] * C, d, places=3)  # float32 delays
            friis = (LAM / (4 * math.pi * d)) ** 2
            self.assertAlmostEqual(10 * math.log10(p[k] / friis), 0.0, delta=0.05)
            az, el = self.angles(tx, rx)
            self.assertAlmostEqual(T.phi_r[0, 0, k], az, places=4)
            self.assertAlmostEqual(math.pi / 2 - T.theta_r[0, 0, k], el, places=4)

    def test_ground_bounce_matches_image_source_generator(self):
        for tx, rx in (([0, 0, 15.0], [0, 70, 1.5]), ([0, -60, 30.0], [0, 60, 2.0]),
                       ([-70, 0, 12.0], [70, 0, 4.0])):
            T = self.trace([tx], [rx])
            img = [tx[0], tx[1], -tx[2]]
            d_img = float(np.linalg.norm(np.array(img) - np.array(rx)))
            ok = (T.ctype[0, 0] == SPECULAR) & (T.depth[0, 0] == 1) & (T.power[0, 0] > 0)
            k = [i for i in np.where(ok)[0] if abs(T.tau_s[0, 0, i] * C - d_img) < 0.01]
            self.assertEqual(len(k), 1, f"no ground-bounce path for {tx}->{rx}")
            az, el = self.angles(img, rx)
            self.assertLess(el, 0.0)  # the mirror image is below the horizon
            self.assertAlmostEqual(T.phi_r[0, 0, k[0]], az, places=3)
            self.assertAlmostEqual(math.pi / 2 - T.theta_r[0, 0, k[0]], el, places=3)
            self.assertLessEqual(T.power[0, 0, k[0]], (LAM / (4 * math.pi * d_img)) ** 2 * 1.001)

    def test_facade_image_in_generator_is_a_real_ray_traced_path(self):
        tx, rx = [-5.0, -45.0, 20.0], [-5.0, -20.0, 1.5]  # off-centre: images not equidistant
        recv = Receiver("r", tuple(rx), None, 1.0, 1.0, None)
        imgs = _facade_images(np.array(tx), recv, self.s, "none")
        self.assertGreater(len(imgs), 0)
        T = self.trace([tx], [rx])
        found = 0
        for img in imgs:
            d_img = float(np.linalg.norm(img - np.array(rx)))
            ok = (T.ctype[0, 0] == SPECULAR) & (T.depth[0, 0] == 1) & (T.power[0, 0] > 0)
            k = [i for i in np.where(ok)[0] if abs(T.tau_s[0, 0, i] * C - d_img) < 0.01]
            if k:
                found += 1
                az, el = self.angles(img, rx)
                self.assertAlmostEqual(T.phi_r[0, 0, k[0]], az, places=3)
        self.assertEqual(found, len(imgs), "generator image has no matching ray-traced path")

    def test_batching_does_not_change_results(self):
        tx = np.array([[0, 0, 15.0], [-40, -40, 60.0], [40, -40, 30.0]])
        rx = np.array([[0, 70, 1.5], [80, 0, 1.5]])
        a = trace_paths(self.sc, tx, rx, tx_batch=1)
        b = trace_paths(self.sc, tx, rx, tx_batch=3)
        oa, ob = link_observables(a), link_observables(b)
        np.testing.assert_allclose(oa.rss_db, ob.rss_db, atol=1e-4)
        np.testing.assert_allclose(oa.toa_m, ob.toa_m, atol=1e-4)

    def test_emitter_inside_building_loses_power_through_the_wall(self):
        out = self.trace([[0, 0, 15.0]], [[0, 70, 1.5]])
        ins = self.trace([[-40, -40, 15.0]], [[0, 70, 1.5]])
        d_out = np.linalg.norm([0, 70, 13.5])
        d_in = np.linalg.norm([40, 110, 13.5])
        fs = lambda d: 20 * math.log10(4 * math.pi * d / LAM)
        rss_out = link_observables(out).rss_db[0, 0]
        rss_in = link_observables(ins).rss_db[0, 0]
        extra = (-rss_in - fs(d_in)) - (-rss_out - fs(d_out))
        self.assertGreater(extra, 3.0)   # some excess loss from facade penetration / blockage


@unittest.skipUnless(HAVE, "sionna-rt not available in this interpreter")
class WindowsAndPolygonScenes(unittest.TestCase):
    """Per-floor window bands must change what leaves a building, by physics we can predict."""

    @classmethod
    def setUpClass(cls):
        cls.base = PolygonBuilding("W", ((-15, -15), (15, -15), (15, 15), (-15, 15)), 37.0, 10, "concrete")

    def rss(self, facade, z, rx=None):
        # default: receiver at the emitter's height so the direct ray crosses the wall horizontally
        # (a sloped ray would cross at a different height than the emitter's own)
        rx = (60.0, 0.0, z) if rx is None else rx
        sc = load_block(Scene((replace(self.base, facade=facade),)), F)
        T = trace_paths(sc, np.array([[0.0, 0.0, z]]), np.array([rx]), tx_batch=1, max_depth=2)
        return float(link_observables(T).rss_db[0, 0])

    def test_glass_band_leaks_far_more_than_spandrel_and_solid_concrete(self):
        fh, rho = 3.7, 0.60
        z_glass = 3 * fh + 0.6 * (1 - rho) * fh + 0.5 * rho * fh        # middle of a glazing band
        z_span = 3 * fh + 0.3 * (1 - rho) * fh                          # inside the lower spandrel
        glass, span = self.rss("curtain", z_glass), self.rss("curtain", z_span)
        concrete, all_glass = self.rss("concrete", z_glass), self.rss("glass_solid", z_glass)
        self.assertGreater(glass - span, 5.0)           # window vs opaque spandrel, same facade
        self.assertGreater(glass - concrete, 5.0)       # windowed tower vs solid concrete tower
        self.assertAlmostEqual(span, concrete, delta=3.0)   # spandrel behaves like the concrete wall
        self.assertGreaterEqual(all_glass, glass - 1.0)     # an all-glass wall is at least as leaky

    def test_a_second_building_in_the_way_costs_power_and_the_solver_geometry_agrees(self):
        blocker = PolygonBuilding("X", ((25, -15), (55, -15), (55, 15), (25, 15)), 37.0, 10, "concrete")
        scene = Scene((self.base, blocker))
        z = 20.0
        sc = load_block(scene, F)
        T = trace_paths(sc, np.array([[0.0, 0.0, z]]), np.array([[90.0, 0.0, z], [0.0, 90.0, z]]),
                        tx_batch=1, max_depth=3)
        behind, clear = link_observables(T).rss_db[0]
        # same 90 m distance, same exit wall; the blocked one also crosses two concrete walls of X
        self.assertGreater(clear - behind, 10.0)
        # the solver's own geometric test calls exactly these two links
        emitter = (0.0, 0.0, z)
        self.assertTrue(scene.segment_blocked(emitter, (90.0, 0.0, z), exclude=("W",)))
        self.assertFalse(scene.segment_blocked(emitter, (0.0, 90.0, z), exclude=("W",)))


if __name__ == "__main__":
    unittest.main()
