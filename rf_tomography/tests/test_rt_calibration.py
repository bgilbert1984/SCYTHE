import math
import unittest

import numpy as np

from rf_tomography.benchmarks.rt_solver_calibration import calibrate
from rf_tomography.geometry.scene import synthetic_manhattan_block
from rf_tomography.measurements.raytraced import C, PathTable
from rf_tomography.placement.sites import candidate_sites


def exact_direct_table(scene, sites, n_exp=3.1, n_emit=24, seed=0):
    """Every link is one exact direct path with power ~ d^-n: nothing to calibrate but the slope."""
    rng = np.random.default_rng(seed)
    b = scene.by_name("A")
    lo, hi = b.box
    tx = np.column_stack([rng.uniform(lo[0] + 1, hi[0] - 1, n_emit), rng.uniform(lo[1] + 1, hi[1] - 1, n_emit),
                          rng.uniform(5, 120, n_emit)])
    rx = np.array([s.pos for s in sites], float)
    d = tx[:, None, :] - rx[None, :, :]
    dist = np.linalg.norm(d, axis=-1)
    el = np.arcsin(d[..., 2] / dist)
    az = np.arctan2(d[..., 1], d[..., 0])
    P = 10 ** (-(-40.0 + n_exp * 10 * np.log10(dist)) / 10)
    sh = P.shape + (1,)
    T = PathTable(915e6, tx, rx, P.reshape(sh), (dist / C).reshape(sh), (math.pi / 2 - el).reshape(sh),
                  az.reshape(sh), np.zeros(sh, np.int8), np.zeros(sh, np.int8), {})
    return T, {"el3": el, "az3": az, "az1": az}, ["A"] * n_emit


class CalibrateTests(unittest.TestCase):
    def test_recovers_pathloss_exponent_and_clamps_floors_on_a_perfectly_clean_table(self):
        s = synthetic_manhattan_block()
        sites = candidate_sites(s)
        T, bf, owner = exact_direct_table(s, sites)
        sensing, prop, cfg, rep = calibrate(T, s, owner, sites, np.ones(len(T.tx), bool), bf, safety=2.0)
        self.assertAlmostEqual(prop.path_loss_exp, 3.1, places=3)
        self.assertLess(rep["rss_sigma_los_db"], 1e-6)
        self.assertEqual(sensing.multipath_angle_sigma_deg, 0.25)    # clamp: never claim perfect angles
        self.assertEqual(sensing.multipath_range_sigma_m, 0.5)       # clamp: never claim perfect delays
        self.assertEqual(cfg.eps, 0.02)                              # floor on outlier belief
        self.assertEqual(cfg.pi_nlos, 0.02)                          # floor on NLOS prior

    def test_subset_mask_uses_only_the_selected_emitters(self):
        s = synthetic_manhattan_block()
        sites = candidate_sites(s)
        T, bf, owner = exact_direct_table(s, sites)
        mask = np.arange(len(T.tx)) % 2 == 0
        *_, rep_half = calibrate(T, s, owner, sites, mask, bf)
        *_, rep_all = calibrate(T, s, owner, sites, np.ones(len(T.tx), bool), bf)
        self.assertLess(rep_half["n_links_los"], rep_all["n_links_los"])
        self.assertAlmostEqual(rep_half["n_links_los"] / rep_all["n_links_los"], 0.5, delta=0.1)

    def test_outliers_raise_the_beliefs(self):
        s = synthetic_manhattan_block()
        sites = candidate_sites(s)
        T, bf, owner = exact_direct_table(s, sites)
        rng = np.random.default_rng(3)
        bad = rng.random(bf["el3"].shape) < 0.2          # 20% of beam-peak elevations are 20 deg off
        bf2 = dict(bf, el3=np.where(bad, bf["el3"] + math.radians(20.0), bf["el3"]))
        sensing, prop, cfg, rep = calibrate(T, s, owner, sites, np.ones(len(T.tx), bool), bf2)
        self.assertGreater(rep["angle_outlier_rate"], 0.05)
        self.assertGreater(cfg.eps, 0.02)
        self.assertGreaterEqual(cfg.sigma_out_angle_deg, 8.0)


if __name__ == "__main__":
    unittest.main()
