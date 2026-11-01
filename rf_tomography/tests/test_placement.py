import unittest

import numpy as np

from rf_tomography.geometry.scene import synthetic_manhattan_block
from rf_tomography.localization.crlb import position_crlb
from rf_tomography.localization.forward import noise_covariance, plan_columns
from rf_tomography.measurements.models import EMITTERS, SensingParams
from rf_tomography.placement.objective import PlacementObjective, make_probes
from rf_tomography.placement.optimizer import optimise, random_layouts
from rf_tomography.placement.sites import all_candidates
from rf_tomography.propagation.model import PropagationParams


class PlacementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = synthetic_manhattan_block()
        cls.prop = PropagationParams()
        cls.em = EMITTERS[1]
        cls.sensing = SensingParams()
        cls.cands = all_candidates(cls.s)
        cls.probes = make_probes(cls.s, floors_per_building=4, xy_per_floor=2)
        cls.obj = PlacementObjective(cls.s, cls.cands, cls.probes, cls.em, cls.sensing, cls.prop)

    def test_analytic_matches_finite_difference_crlb(self):
        rng = np.random.default_rng(5)
        for _ in range(6):
            idx = [int(i) for i in rng.choice(len(self.cands), 6, replace=False)]
            rx = [self.cands[i].receiver() for i in idx]
            sz = self.obj.sigma_z(idx)
            for pi, pr in enumerate(self.probes):
                los = [not self.s.segment_blocked(pr.pos, r.pos, exclude=(pr.building, r.host_building))
                       for r in rx]
                dist = [float(np.linalg.norm(pr.pos - np.asarray(r.pos))) for r in rx]
                cols = plan_columns(rx, los)
                cov = noise_covariance(cols, rx, los, dist, self.em, self.sensing, self.prop)
                ref = np.sqrt(max(position_crlb(pr.pos, rx, cols, cov, self.prop)[2, 2], 0))
                if np.isfinite(ref) and ref < 1e3:
                    self.assertAlmostEqual(sz[pi] / ref, 1.0, delta=2e-3)
                else:
                    self.assertTrue(sz[pi] > 1e3 or not np.isfinite(sz[pi]))

    def test_empty_layout_scores_zero_and_more_receivers_never_hurt_much(self):
        self.assertEqual(self.obj.score([]), 0.0)
        idx = list(range(0, len(self.cands), 17))[:6]
        self.assertGreaterEqual(self.obj.score(idx) + 1e-9, self.obj.score(idx[:3]))

    def test_optimiser_respects_budget_and_beats_random(self):
        budget = 10.0
        sel = optimise(self.obj, budget)
        self.assertLessEqual(self.obj.cost(sel), budget)
        rnd = [self.obj.score(r) for r in random_layouts(self.obj, budget, 20, seed=1)]
        self.assertGreater(self.obj.score(sel), max(rnd))

    def test_deterministic(self):
        self.assertEqual(optimise(self.obj, 8.0), optimise(self.obj, 8.0))


if __name__ == "__main__":
    unittest.main()
