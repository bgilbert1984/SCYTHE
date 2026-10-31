import math
import unittest
from dataclasses import replace

import numpy as np

from rf_tomography.benchmarks.identifiability import aggregate, run_cell
from rf_tomography.benchmarks.layouts import layouts
from rf_tomography.geometry.scene import synthetic_manhattan_block
from rf_tomography.measurements.impairments import (PRESETS, ImpairmentModel, classify_los,
                                                    corrupt_measurements, perturb_scene)
from rf_tomography.measurements.models import EMITTERS, SensingParams
from rf_tomography.propagation.model import PropagationParams
from rf_tomography.uncertainty.floor_posterior import CandidateGrid


class GeneratorStatistics(unittest.TestCase):
    def test_classifier_flip_rates(self):
        imp = ImpairmentModel(false_los_prob=0.2, false_nlos_prob=0.1)
        rng = np.random.default_rng(0)
        n = 20000
        obs_nlos = classify_los([False] * n, imp, rng)
        obs_los = classify_los([True] * n, imp, rng)
        self.assertAlmostEqual(np.mean(obs_nlos), 0.2, delta=0.01)       # false LOS
        self.assertAlmostEqual(1 - np.mean(obs_los), 0.1, delta=0.01)    # false NLOS

    def test_nlos_corruption_is_positive_delay_and_wide_angle(self):
        imp = ImpairmentModel(false_los_prob=1.0, nlos_toa_excess_mean_m=30.0, nlos_angle_sigma_deg=20.0)
        rng = np.random.default_rng(1)
        cols = [(0, "toa"), (0, "az")]
        d_toa, d_az = [], []
        for _ in range(8000):
            y, nbad, _ = corrupt_measurements(np.zeros(2), cols, [False], imp, rng)
            d_toa.append(y[0]); d_az.append(y[1]); self.assertEqual(nbad, 1)
        self.assertGreaterEqual(min(d_toa), 0.0)
        self.assertAlmostEqual(np.mean(d_toa), 30.0, delta=1.5)
        self.assertAlmostEqual(np.std(d_az), math.radians(20.0), delta=0.01)

    def test_los_outliers_are_heavy_tailed_and_rss_untouched(self):
        imp = ImpairmentModel(outlier_prob=0.1, angle_outlier_deg=15.0)
        rng = np.random.default_rng(2)
        errs, rss = [], []
        for _ in range(30000):
            y, nbad, _ = corrupt_measurements(np.zeros(2), [(0, "az"), (0, "rss")], [True], imp, rng)
            errs.append(y[0]); rss.append(y[1]); self.assertEqual(nbad, 0)
        e = np.array(errs)
        self.assertEqual(max(abs(np.array(rss))), 0.0)
        self.assertAlmostEqual(np.mean(e != 0), 0.1, delta=0.01)
        kurt = np.mean((e - e.mean()) ** 4) / np.var(e) ** 2
        self.assertGreater(kurt, 10.0)  # Gaussian = 3; a 10% mixture is far heavier

    def test_geometry_perturbation_moves_buildings(self):
        s = synthetic_manhattan_block()
        w = perturb_scene(s, 3.0, np.random.default_rng(3))
        self.assertNotEqual([b.cx for b in w.buildings], [b.cx for b in s.buildings])
        self.assertIs(perturb_scene(s, 0.0, np.random.default_rng(3)), s)


class SolverImpact(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = synthetic_manhattan_block()
        cls.b = cls.s.by_name("B")
        cls.grid = CandidateGrid.for_building(cls.b)
        cls.prop = PropagationParams()

    def _agg(self, imp, n=40, seed=4):
        rows = run_cell(self.s, self.b, layouts(self.s)["street_plus_rooftops"], EMITTERS[1],
                        SensingParams(), self.prop, n, np.random.default_rng(seed), self.grid, imp)
        return aggregate(rows, self.b.n_floors)

    def test_inactive_model_matches_no_model(self):
        a = run_cell(self.s, self.b, layouts(self.s)["street_tdoa"], EMITTERS[1], SensingParams(),
                     self.prop, 6, np.random.default_rng(9), self.grid)
        b = run_cell(self.s, self.b, layouts(self.s)["street_tdoa"], EMITTERS[1], SensingParams(),
                     self.prop, 6, np.random.default_rng(9), self.grid, PRESETS["none"])
        self.assertEqual(a, b)

    def test_severe_impairments_hurt_and_overconfidence_shows(self):
        clean, sev = self._agg(PRESETS["none"]), self._agg(PRESETS["severe"])
        self.assertLess(sev["top1"], clean["top1"])
        self.assertLess(sev["cov90"], clean["cov90"])
        self.assertGreater(sev["mean_nlos_trusted"] + sev["mean_outlier_cols"], 0.0)


if __name__ == "__main__":
    unittest.main()
