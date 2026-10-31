import math
import unittest
from dataclasses import replace

import numpy as np

from rf_tomography.benchmarks.layouts import layouts
from rf_tomography.geometry.scene import synthetic_manhattan_block
from rf_tomography.localization.forward import mean_measurements, noise_covariance, plan_columns
from rf_tomography.measurements.impairments import (CORRELATED_PRESETS, ImpairmentModel,
                                                    _facade_images, apply_common_biases,
                                                    apply_reflections)
from rf_tomography.measurements.models import EMITTERS, Receiver, SensingParams
from rf_tomography.propagation.model import PropagationParams
from rf_tomography.uncertainty.floor_posterior import CandidateGrid, floor_posterior
from rf_tomography.uncertainty.robust_posterior import RobustConfig, robust_floor_posterior


class Geometry(unittest.TestCase):
    def setUp(self):
        self.s = synthetic_manhattan_block()
        self.prop = PropagationParams()
        self.truth = np.array([-40.0, -40.0, 60.0])  # inside building A
        self.rx = [Receiver("r", (0.0, 0.0, 1.5), None, 3.0, 3.0, 10e-9)]
        self.cols = plan_columns(self.rx, [True])
        self.y0 = mean_measurements(self.truth, self.rx, self.cols, self.prop)[0]

    def _reflect(self, surface, seed=0):
        imp = ImpairmentModel(reflect_prob=1.0, reflect_surfaces=surface)
        return apply_reflections(self.y0, self.cols, self.rx, self.truth, [True], imp,
                                 np.random.default_rng(seed), self.prop, self.s, "A", set())

    def test_ground_bounce_flips_elevation_and_adds_exact_path_excess(self):
        y, n = self._reflect("ground")
        self.assertEqual(n, 1)
        k = {kind: j for j, (_, kind) in enumerate(self.cols)}
        d_dir = np.linalg.norm(self.truth - self.rx[0].pos)
        d_img = np.linalg.norm(np.array([-40, -40, -60.0]) - self.rx[0].pos)
        self.assertAlmostEqual(y[k["toa"]] - self.y0[k["toa"]], d_img - d_dir, places=9)
        self.assertGreater(y[k["toa"]], self.y0[k["toa"]])           # one-sided
        self.assertAlmostEqual(y[k["az"]], self.y0[k["az"]], places=9)  # azimuth unchanged
        self.assertGreater(self.y0[k["el"]], 0.0)
        self.assertLess(y[k["el"]], 0.0)                              # image is below the horizon
        self.assertEqual(y[k["rss"]], self.y0[k["rss"]])              # RSS untouched

    def test_facade_images_are_valid_single_bounce_geometry(self):
        imgs = _facade_images(self.truth, self.rx[0], self.s, "A")
        self.assertGreater(len(imgs), 0)
        for img in imgs:
            # image differs from truth only along one horizontal axis, same height
            self.assertEqual(img[2], self.truth[2])
            self.assertEqual(int(img[0] != self.truth[0]) + int(img[1] != self.truth[1]), 1)
        # emitter sits in A (x,y in [-65,-15]); its own faces would give x or y in {-90, 10}
        for img in imgs:
            self.assertFalse(img[1] == -40.0 and img[0] in (-90.0, 10.0))
            self.assertFalse(img[0] == -40.0 and img[1] in (-90.0, 10.0))

    def test_reflection_is_off_when_probability_zero_and_deterministic(self):
        y, n = apply_reflections(self.y0, self.cols, self.rx, self.truth, [True],
                                 ImpairmentModel(reflect_prob=0.0), np.random.default_rng(1),
                                 self.prop, self.s, "A")
        self.assertEqual(n, 0)
        np.testing.assert_array_equal(y, self.y0)
        a, _ = self._reflect("mixed", 5)
        b, _ = self._reflect("mixed", 5)
        np.testing.assert_array_equal(a, b)

    def test_common_biases_touch_only_their_columns(self):
        rng = np.random.default_rng(2)
        imp = ImpairmentModel(el_bias_common_deg=2.0, common_delay_mean_m=20.0)
        y = apply_common_biases(self.y0, self.cols, imp, rng)
        for j, (_, kind) in enumerate(self.cols):
            if kind in ("rss", "az"):
                self.assertEqual(y[j], self.y0[j])
            else:
                self.assertNotEqual(y[j], self.y0[j])
        self.assertGreater(y[[kd for _, kd in self.cols].index("toa")], self.y0[[kd for _, kd in self.cols].index("toa")])


class SolverInvariance(unittest.TestCase):
    """A delay common to all receivers is the clock offset; solvers must not care."""

    def test_common_delay_does_not_change_posterior(self):
        s = synthetic_manhattan_block()
        prop, em, sens = PropagationParams(), EMITTERS[1], SensingParams()
        b = s.by_name("B")
        grid = CandidateGrid.for_building(b)
        rx = layouts(s)["street_plus_rooftops"]
        truth = np.array([b.cx + 2, b.cy - 4, b.floor_centers_z()[12]])
        los = [not s.segment_blocked(truth, r.pos, exclude=("B", r.host_building)) for r in rx]
        cols = plan_columns(rx, los)
        dist = [float(np.linalg.norm(truth - np.asarray(r.pos))) for r in rx]
        cov = noise_covariance(cols, rx, los, dist, em, sens, prop)
        mu = mean_measurements(truth, rx, cols, prop)[0]
        shifted = apply_common_biases(mu, cols, ImpairmentModel(common_delay_mean_m=50.0),
                                      np.random.default_rng(3))
        self.assertGreater(np.abs(shifted - mu).max(), 1.0)
        np.testing.assert_allclose(floor_posterior(mu, grid, rx, cols, cov, prop),
                                   floor_posterior(shifted, grid, rx, cols, cov, prop), atol=1e-6)
        cfg = RobustConfig()
        np.testing.assert_allclose(robust_floor_posterior(mu, grid, rx, cols, cov, prop, cfg)[0],
                                   robust_floor_posterior(shifted, grid, rx, cols, cov, prop, cfg)[0],
                                   atol=1e-6)


class ElevationOffsetNuisance(unittest.TestCase):
    def test_offset_aware_solver_removes_common_bias_but_keeps_truth_without_it(self):
        s = synthetic_manhattan_block()
        prop, em, sens = PropagationParams(), EMITTERS[1], SensingParams()
        b = s.by_name("B")
        grid = CandidateGrid.for_building(b)
        rx = layouts(s)["street_plus_rooftops"]
        k = 12
        truth = np.array([b.cx + 2, b.cy - 4, b.floor_centers_z()[k]])
        los = [not s.segment_blocked(truth, r.pos, exclude=("B", r.host_building)) for r in rx]
        cols = plan_columns(rx, los)
        dist = [float(np.linalg.norm(truth - np.asarray(r.pos))) for r in rx]
        cov = noise_covariance(cols, rx, los, dist, em, sens, prop)
        mu = mean_measurements(truth, rx, cols, prop)[0]
        biased = mu.copy()
        biased[[j for j, (_, kd) in enumerate(cols) if kd == "el"]] += math.radians(3.0)
        F = np.arange(b.n_floors)
        err = lambda p: abs(float(p @ F) - k)
        plain = robust_floor_posterior(biased, grid, rx, cols, cov, prop, RobustConfig(pi_nlos=0.0))[0]
        aware_cfg = RobustConfig(pi_nlos=0.0, el_offset_sigma_deg=3.0)
        aware = robust_floor_posterior(biased, grid, rx, cols, cov, prop, aware_cfg)[0]
        self.assertLess(err(aware), err(plain))
        clean = robust_floor_posterior(mu, grid, rx, cols, cov, prop, aware_cfg)[0]
        self.assertLessEqual(abs(int(np.argmax(clean)) - k), 1)
        self.assertAlmostEqual(clean.sum(), 1.0, places=9)


if __name__ == "__main__":
    unittest.main()
