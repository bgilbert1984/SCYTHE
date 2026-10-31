import math
import unittest

import numpy as np

from rf_tomography.benchmarks.layouts import layouts
from rf_tomography.geometry.scene import synthetic_manhattan_block
from rf_tomography.localization.forward import mean_measurements, noise_covariance, plan_columns
from rf_tomography.measurements.models import EMITTERS, SensingParams
from rf_tomography.propagation.model import PropagationParams
from rf_tomography.uncertainty.floor_posterior import CandidateGrid, floor_posterior
from rf_tomography.uncertainty.robust_posterior import RobustConfig, robust_floor_posterior


class RobustSolver(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s = synthetic_manhattan_block()
        cls.prop, cls.em, cls.sens = PropagationParams(), EMITTERS[1], SensingParams()
        b = s.by_name("B")
        cls.grid = CandidateGrid.for_building(b)
        cls.rx = layouts(s)["street_plus_rooftops"]
        cls.k = 14
        cls.truth = np.array([b.cx + 3.0, b.cy - 5.0, b.floor_centers_z()[cls.k]])
        los = [not s.segment_blocked(cls.truth, r.pos, exclude=("B", r.host_building)) for r in cls.rx]
        cls.cols = plan_columns(cls.rx, los)
        dist = [float(np.linalg.norm(cls.truth - np.asarray(r.pos))) for r in cls.rx]
        cls.cov = noise_covariance(cls.cols, cls.rx, los, dist, cls.em, cls.sens, cls.prop)
        cls.mu = mean_measurements(cls.truth, cls.rx, cls.cols, cls.prop)[0]

    def post(self, y, cfg=None):
        if cfg is None:
            return floor_posterior(y, self.grid, self.rx, self.cols, self.cov, self.prop), {}
        return robust_floor_posterior(y, self.grid, self.rx, self.cols, self.cov, self.prop, cfg)

    def test_all_models_normalised_and_find_truth_on_clean_data(self):
        for cfg in (RobustConfig(col_model="gaussian", pi_nlos=0.0), RobustConfig(),
                    RobustConfig(col_model="student")):
            p, _ = self.post(self.mu, cfg)
            self.assertAlmostEqual(p.sum(), 1.0, places=9)
            self.assertLessEqual(abs(int(np.argmax(p)) - self.k), 1, cfg.name)

    def test_single_gross_outlier_recovered_by_robust_not_gaussian(self):
        j = next(j for j, (i, kd) in enumerate(self.cols) if kd == "el")
        y = self.mu.copy()
        y[j] += math.radians(40.0)
        gauss, _ = self.post(y)
        for cfg in (RobustConfig(pi_nlos=0.0), RobustConfig(col_model="student", pi_nlos=0.0)):
            rob, _ = self.post(y, cfg)
            self.assertGreater(rob[self.k] + rob[max(self.k - 1, 0)] + rob[self.k + 1],
                               gauss[self.k] + gauss[max(self.k - 1, 0)] + gauss[self.k + 1])
            self.assertLessEqual(abs(int(np.argmax(rob)) - self.k), 1)

    def test_nlos_receiver_is_flagged_and_clean_ones_are_not(self):
        bad = next(i for i, kd in self.cols if kd == "el")
        y = self.mu.copy()
        for j, (i, kd) in enumerate(self.cols):
            if i == bad and kd != "rss":
                y[j] += 40.0 if kd == "toa" else math.radians(35.0)
        p, flags = self.post(y, RobustConfig())
        self.assertGreater(flags[bad], 0.5)
        self.assertTrue(all(v < 0.5 for i, v in flags.items() if i != bad), flags)
        self.assertLessEqual(abs(int(np.argmax(p)) - self.k), 1)

    def test_clean_data_flags_nothing(self):
        _, flags = self.post(self.mu, RobustConfig())
        self.assertTrue(all(v < 0.5 for v in flags.values()), flags)


if __name__ == "__main__":
    unittest.main()
