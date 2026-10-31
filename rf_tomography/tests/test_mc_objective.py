import unittest

import numpy as np

from rf_tomography.benchmarks.identifiability import run_cell, simulate_observation
from rf_tomography.geometry.scene import synthetic_manhattan_block
from rf_tomography.measurements.impairments import PRESETS
from rf_tomography.measurements.models import EMITTERS, SensingParams
from rf_tomography.placement.mc_objective import MCObjective
from rf_tomography.placement.optimizer import swap_search
from rf_tomography.placement.sites import all_candidates
from rf_tomography.propagation.model import PropagationParams
from rf_tomography.uncertainty.floor_posterior import CandidateGrid
from rf_tomography.uncertainty.robust_posterior import RobustConfig, robust_floor_posterior


class MCObjectiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = synthetic_manhattan_block()
        cls.c = all_candidates(cls.s)
        cls.prop = PropagationParams()
        cls.obj = MCObjective(cls.s, cls.c, EMITTERS[1], {"moderate": PRESETS["moderate"]},
                              RobustConfig(), trials_per_building=3, xy_spacing_m=4.0, seed=5)

    def test_cached_H_matches_direct_computation(self):
        t = self.obj.bank["moderate"][0]
        idx = [0, 7, 30]
        sel = np.where(np.isin(t["col_rx"], idx))[0]
        cols = [t["cols"][j] for j in sel]
        grid = self.obj.grids[t["b"]]
        H = np.stack([self.obj.H[t["b"]][col] for col in cols], 1)
        a, _ = robust_floor_posterior(t["y"][sel], grid, self.obj.receivers, cols,
                                      t["cov"][np.ix_(sel, sel)], self.prop, RobustConfig(), H)
        b, _ = robust_floor_posterior(t["y"][sel], grid, self.obj.receivers, cols,
                                      t["cov"][np.ix_(sel, sel)], self.prop, RobustConfig())
        np.testing.assert_allclose(a, b, atol=1e-12)

    def test_deterministic_memoised_and_order_independent(self):
        a = self.obj.score([3, 40, 90])
        b = self.obj.score([90, 3, 40])
        self.assertEqual(a, b)
        self.assertGreaterEqual(a, 0.0)
        self.assertLessEqual(a, 1.0)

    def test_empty_layout_is_chance_level(self):
        ev = self.obj.evaluate([])
        self.assertEqual(ev["moderate"]["top1"], 0.0)

    def test_better_layout_scores_higher_than_a_single_street_receiver(self):
        street = next(i for i, k in enumerate(self.c) if k.site.kind == "street" and k.tier == "rss_az")
        rich = [i for i, k in enumerate(self.c) if k.site.name in ("Sx20", "RC+-", "RB-+")
                and k.tier in ("array", "tdoa")]
        self.assertGreater(self.obj.score(rich), self.obj.score([street]))

    def test_swap_pool_restricts_candidates(self):
        pool = [3, 40, 90]
        out = swap_search(self.obj, [3], 6.0, max_rounds=2, pool=pool)
        self.assertTrue(set(out) <= set(pool))

    def test_simulate_observation_shapes_are_consistent(self):
        b = self.s.by_name("B")
        rx = [k.receiver() for k in self.c[:6]]
        ob = simulate_observation(self.s, b, rx, EMITTERS[1], SensingParams(), self.prop,
                                  np.random.default_rng(4), PRESETS["moderate"])
        self.assertEqual(len(ob.y), len(ob.cols))
        self.assertEqual(ob.cov.shape, (len(ob.cols), len(ob.cols)))


if __name__ == "__main__":
    unittest.main()
