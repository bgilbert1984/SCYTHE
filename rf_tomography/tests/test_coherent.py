"""Coherent (complex-sum) array AoA model."""
import math
import unittest

import numpy as np

from rf_tomography.measurements.raytraced import PathTable, beamformed_aoa, coherent_aoa


def table(els_deg, powers, n=1, az_deg=0.0):
    """One link per emitter with paths at the given elevations (receiver sees them at those angles)."""
    K = len(els_deg)
    P = np.tile(np.array(powers, float), (n, 1, 1))
    th = np.tile(math.pi / 2 - np.radians(np.array(els_deg, float)), (n, 1, 1))
    ph = np.full((n, 1, K), math.radians(az_deg))
    z = np.zeros((n, 1, K))
    return PathTable(915e6, np.zeros((n, 3)), np.zeros((1, 3)), P, z, th, ph,
                     np.zeros((n, 1, K), np.int8), np.zeros((n, 1, K), np.int8), {})


class CoherentTest(unittest.TestCase):
    def test_single_path_exact(self):
        for el in (-12.0, 0.0, 7.5, 20.0):
            t = table([el], [1.0], n=3)
            for ns in (1, 8, None):
                got = np.degrees(coherent_aoa(t, 3.0, "el", ns))
                self.assertTrue(np.all(np.abs(got - el) <= 0.26), (el, ns, got))

    def test_infinite_snapshot_limit_is_noncoherent(self):
        rng = np.random.default_rng(0)
        for _ in range(20):
            els = rng.uniform(-15, 15, 3)
            pw = rng.uniform(0.1, 1.0, 3)
            t = table(els, pw)
            a = np.degrees(coherent_aoa(t, 3.0, "el", None))[0, 0]
            b = np.degrees(beamformed_aoa(t, 3.0, "el"))[0, 0]
            self.assertLess(abs(a - b), 0.6, (els, pw, a, b))

    def test_single_snapshot_interference_spreads_and_averaging_removes_it(self):
        # two equal paths 2 deg apart (well inside the ~19 deg beamwidth of 3 m at 915 MHz): the
        # non-coherent peak is deterministic, one coherent snapshot moves with the relative phase
        t = table([0.0, 2.0], [1.0, 1.0], n=200)
        one = np.degrees(coherent_aoa(t, 3.0, "el", 1, seed=1))[:, 0]
        many = np.degrees(coherent_aoa(t, 3.0, "el", 64, seed=1))[:, 0]
        self.assertGreater(one.std(), 3 * many.std())
        self.assertGreater(one.std(), 1.0)

    def test_horizontal_array_uses_azimuth(self):
        t = table([0.0, 0.0], [1.0, 0.01], az_deg=30.0)
        t.phi_r[0, 0, 1] = math.radians(40.0)
        got = np.degrees(coherent_aoa(t, 3.0, "az", None))[0, 0]
        self.assertLess(abs(got - 30.0), 1.0)

    def test_deterministic_and_keyed_on_emitter_id(self):
        t = table([0.0, 3.0], [1.0, 0.8], n=6)
        a = coherent_aoa(t, 3.0, "el", 1, seed=5)
        self.assertTrue(np.array_equal(a, coherent_aoa(t, 3.0, "el", 1, seed=5)))
        ids = [100 + i for i in range(6)]
        b = coherent_aoa(t, 3.0, "el", 1, seed=5, emitter_ids=ids)
        self.assertTrue(np.array_equal(b[2:4], coherent_aoa(table([0.0, 3.0], [1.0, 0.8], n=2), 3.0, "el", 1,
                                                            seed=5, emitter_ids=ids[2:4])))

    def test_no_path_is_nan(self):
        t = table([0.0], [0.0])
        self.assertTrue(np.isnan(coherent_aoa(t, 3.0, "el", 1)).all())


if __name__ == "__main__":
    unittest.main()
