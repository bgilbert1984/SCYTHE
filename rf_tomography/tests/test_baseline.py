import unittest
from dataclasses import replace

import numpy as np

from rf_tomography.benchmarks.identifiability import aggregate, run_cell
from rf_tomography.benchmarks.layouts import layouts
from rf_tomography.geometry.scene import synthetic_manhattan_block
from rf_tomography.localization.crlb import position_crlb
from rf_tomography.localization.forward import noise_covariance, plan_columns
from rf_tomography.measurements.models import EMITTERS, SensingParams
from rf_tomography.propagation.model import PropagationParams
from rf_tomography.uncertainty.floor_posterior import CandidateGrid


class SceneTests(unittest.TestCase):
    def test_los_blocking(self):
        s = synthetic_manhattan_block()
        # A (-40,-40) to D (40,40) emitters' line crosses the open intersection only diagonally,
        # but a point in A to a ground rx behind B must be blocked by B.
        self.assertTrue(s.segment_blocked((-40, -40, 10), (100, -40, 10), exclude=("A",)))
        self.assertFalse(s.segment_blocked((-40, -40, 10), (0, 0, 1.5), exclude=("A",)))
        # Over the roofline is clear.
        self.assertFalse(s.segment_blocked((-40, -40, 100), (100, -40, 130), exclude=("A",)))


class CellTests(unittest.TestCase):
    def setUp(self):
        self.s = synthetic_manhattan_block()
        self.prop = PropagationParams()
        self.b = self.s.by_name("B")
        self.grid = CandidateGrid.for_building(self.b)
        self.rx = layouts(self.s)

    def _cell(self, layout, em, sensing=SensingParams(), n=12, seed=1):
        rng = np.random.default_rng(seed)
        return run_cell(self.s, self.b, self.rx[layout], em, sensing, self.prop, n, rng, self.grid)

    def test_deterministic(self):
        a = self._cell("street_tdoa", EMITTERS[3], n=4)
        b = self._cell("street_tdoa", EMITTERS[3], n=4)
        self.assertEqual(a, b)

    def test_beats_random_baseline(self):
        rows = self._cell("street_plus_rooftops", EMITTERS[3], n=20)
        agg = aggregate(rows, self.b.n_floors)
        self.assertGreater(agg["top3"], agg["random_top3"])

    def test_weaker_sensing_is_not_better(self):
        strong = aggregate(self._cell("street_plus_rooftops", EMITTERS[3], n=30), 27)
        weak = aggregate(self._cell("street_rss_az", EMITTERS[0], n=30), 27)
        self.assertGreaterEqual(strong["top3"], weak["top3"])

    def test_crlb_shrinks_with_sync_quality(self):
        pt = np.array([40.0, -40.0, 50.0])
        em = EMITTERS[3]
        sig = []
        for sync in (100e-9, 1e-9):
            rx = [replace(r, sync_sigma_s=sync) for r in self.rx["street_tdoa"]]
            los = [True] * len(rx)
            cols = plan_columns(rx, los)
            cov = noise_covariance(cols, rx, los, [100.0] * len(rx), em,
                                   SensingParams(multipath_range_sigma_m=0.1), self.prop)
            sig.append(position_crlb(pt, rx, cols, cov, self.prop)[2, 2])
        self.assertLess(sig[1], sig[0])


if __name__ == "__main__":
    unittest.main()
