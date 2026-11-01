import unittest

import numpy as np

from rf_tomography.benchmarks.rt_patterns import boot_ratio, subset
from rf_tomography.benchmarks.scenes import table_meta
from rf_tomography.measurements.raytraced import PathTable


def table(n=6, r=3, k=2, d=3):
    rng = np.random.default_rng(0)
    t = PathTable(915e6, rng.normal(size=(n, 3)), rng.normal(size=(r, 3)), rng.random((n, r, k)),
                  rng.random((n, r, k)), rng.random((n, r, k)), rng.random((n, r, k)),
                  np.zeros((n, r, k), np.int8), np.zeros((n, r, k), np.int8),
                  {"repr": repr({"owner": list("ABCDEF")[:n], "sites": ["s0", "s1", "s2"], "scene": "synthetic"})},
                  total_power=rng.random((n, r)))
    t.best_types = rng.integers(0, 5, (n, r, d)).astype(np.int8)
    t.best_mats = rng.integers(0, 4, (n, r, d)).astype(np.int8)
    t.dir_types = rng.integers(0, 5, (n, r, d)).astype(np.int8)
    t.dir_mats = rng.integers(0, 4, (n, r, d)).astype(np.int8)
    t.dir_power = rng.random((n, r)).astype(np.float32)
    return t


class Subset(unittest.TestCase):
    def test_subset_slices_every_array_and_remembers_original_emitter_ids(self):
        t = table()
        s = subset(t, [0, 2, 4])
        self.assertEqual(s.power.shape[0], 3)
        np.testing.assert_array_equal(s.tx, t.tx[[0, 2, 4]])
        np.testing.assert_array_equal(s.best_mats, t.best_mats[[0, 2, 4]])
        np.testing.assert_array_equal(s.dir_power, t.dir_power[[0, 2, 4]])
        np.testing.assert_array_equal(s.total_power, t.total_power[[0, 2, 4]])
        m = table_meta(s)
        self.assertEqual(m["owner"], ["A", "C", "E"])
        self.assertEqual(m["emitter_index"], [0, 2, 4])

    def test_subsetting_twice_keeps_the_original_ids(self):
        s = subset(subset(table(), [0, 2, 4]), [1, 2])
        self.assertEqual(table_meta(s)["emitter_index"], [2, 4])
        self.assertEqual(table_meta(s)["owner"], ["C", "E"])

    def test_tables_without_composition_survive(self):
        t = table()
        for f in ("best_types", "best_mats", "dir_types", "dir_mats", "dir_power"):
            setattr(t, f, None)
        s = subset(t, [1, 3])
        self.assertIsNone(s.best_mats)
        self.assertEqual(s.power.shape[0], 2)


class Bootstrap(unittest.TestCase):
    def test_boot_ratio_is_a_ratio_of_sums_over_the_resampled_emitters(self):
        num, den = np.array([1.0, 0.0, 3.0]), np.array([2.0, 2.0, 4.0])
        self.assertAlmostEqual(boot_ratio(num, den, np.array([0, 1, 2])), 4 / 8)
        self.assertAlmostEqual(boot_ratio(num, den, np.array([2, 2, 2])), 3 / 4)
        self.assertAlmostEqual(boot_ratio(num, den, np.array([1])), 0.0)


if __name__ == "__main__":
    unittest.main()
