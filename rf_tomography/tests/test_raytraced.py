"""Observable extraction from path tables (numpy only; runs in the core venv)."""
import math
import unittest

import numpy as np

from rf_tomography.measurements.raytraced import (C, REFRACTION, SPECULAR, PathTable,
                                                  direct_like, link_observables)


def table():
    # one emitter, one receiver, 4 paths: weak early direct (through a wall), strong late
    # reflection, medium reflection, invalid padding
    tx = np.array([[0.0, 0.0, 10.0]])
    rx = np.array([[30.0, 40.0, 0.0]])
    d = float(np.linalg.norm(tx[0] - rx[0]))
    P = np.array([[[1e-12, 1e-9, 1e-10, 0.0]]])
    tau = np.array([[[d / C, (d + 20) / C, (d + 5) / C, np.nan]]])
    theta = np.array([[[0.6, 1.9, 1.2, 0.0]]])
    phi = np.array([[[0.5, -2.0, 1.0, 0.0]]])
    ctype = np.array([[[REFRACTION, SPECULAR, SPECULAR | REFRACTION, 0]]], dtype=np.int8)
    depth = np.array([[[1, 1, 2, 0]]], dtype=np.int8)
    return PathTable(915e6, tx, rx, P, tau, theta, phi, ctype, depth, {}), d


class Observables(unittest.TestCase):
    def test_direct_like_mask(self):
        m = direct_like(np.array([0, REFRACTION, SPECULAR, SPECULAR | REFRACTION, 8]))
        self.assertEqual(m.tolist(), [True, True, False, False, False])

    def test_rss_is_total_power_and_aoa_is_strongest_path(self):
        t, d = table()
        o = link_observables(t)
        self.assertAlmostEqual(o.rss_db[0, 0], 10 * math.log10(1e-12 + 1e-9 + 1e-10), places=9)
        self.assertAlmostEqual(o.az[0, 0], -2.0)
        self.assertAlmostEqual(o.el[0, 0], math.pi / 2 - 1.9)
        self.assertFalse(o.strongest_direct[0, 0])
        self.assertTrue(o.has_direct[0, 0])
        self.assertEqual(o.n_paths[0, 0], 3)

    def test_first_arrival_respects_detection_threshold(self):
        t, d = table()
        # direct-like path is 30 dB below the strongest: invisible at 20 dB, found at 40 dB
        o20 = link_observables(t, toa_threshold_db=20.0)
        o40 = link_observables(t, toa_threshold_db=40.0)
        self.assertAlmostEqual(o20.toa_m[0, 0] - d, 5.0, places=6)
        self.assertAlmostEqual(o40.toa_m[0, 0] - d, 0.0, places=6)
        self.assertTrue(o40.first_direct[0, 0])
        self.assertFalse(o20.first_direct[0, 0])

    def test_rss_uses_stored_total_when_table_is_truncated(self):
        t, _ = table()
        t.total_power = np.array([[1e-7]])   # power of paths that were dropped from the table
        o = link_observables(t)
        self.assertAlmostEqual(o.rss_db[0, 0], 10 * math.log10(1e-7), places=9)
        import tempfile, os
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "t.npz")
            t.save(p)
            self.assertAlmostEqual(PathTable.load(p).total_power[0, 0], 1e-7, places=12)

    def test_no_path_link(self):
        t, _ = table()
        t.power[:] = 0
        o = link_observables(t)
        self.assertEqual(o.rss_db[0, 0], -np.inf)
        self.assertTrue(np.isnan(o.toa_m[0, 0]))
        self.assertEqual(o.n_paths[0, 0], 0)

    def test_geometric_reference_points_from_receiver_to_source(self):
        t, d = table()
        o = link_observables(t)
        self.assertAlmostEqual(o.az_geo[0, 0], math.atan2(-40.0, -30.0))
        self.assertAlmostEqual(o.el_geo[0, 0], math.asin(10.0 / d))

    def test_save_load_roundtrip(self):
        import tempfile, os
        t, _ = table()
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "t.npz")
            t.save(p)
            u = PathTable.load(p)
        np.testing.assert_allclose(u.power, t.power, rtol=1e-6)
        np.testing.assert_array_equal(u.ctype, t.ctype)
        self.assertEqual(u.frequency_hz, 915e6)



def two_path_table(el1_deg, el2_deg, p1=1.0, p2=1.0, az1_deg=0.0, az2_deg=0.0):
    z = np.zeros
    th = np.radians([90 - el1_deg, 90 - el2_deg])[None, None, :]
    ph = np.radians([az1_deg, az2_deg])[None, None, :]
    return PathTable(915e6, np.zeros((1, 3)), np.zeros((1, 3)), np.array([[[p1, p2]]]),
                     np.array([[[1e-7, 1.1e-7]]]), th, ph, z((1, 1, 2), np.int8), z((1, 1, 2), np.int8), {})


class BeamformedAoA(unittest.TestCase):
    def test_single_path_is_exact(self):
        from rf_tomography.measurements.raytraced import beamformed_aoa
        t = two_path_table(12.0, 12.0, 1.0, 0.0)
        self.assertAlmostEqual(math.degrees(beamformed_aoa(t, 3.0, "el")[0, 0]), 12.0, delta=0.26)

    def test_close_paths_merge_inside_a_beamwidth_and_split_outside(self):
        from rf_tomography.measurements.raytraced import beamformed_aoa
        # 3 m at 915 MHz: beamwidth ~ 6.6 deg. Two equal paths 2 deg apart merge near the middle.
        near = math.degrees(beamformed_aoa(two_path_table(10.0, 12.0), 3.0, "el")[0, 0])
        self.assertAlmostEqual(near, 11.0, delta=0.6)
        # 25 deg apart: resolved, the (slightly stronger) first path wins
        far = math.degrees(beamformed_aoa(two_path_table(10.0, 35.0, 1.0, 0.8), 3.0, "el")[0, 0])
        self.assertAlmostEqual(far, 10.0, delta=0.6)

    def test_pull_toward_weaker_nearby_path_is_bounded_and_grows_with_its_power(self):
        from rf_tomography.measurements.raytraced import beamformed_aoa
        a = math.degrees(beamformed_aoa(two_path_table(10.0, 13.0, 1.0, 0.1), 3.0, "el")[0, 0])
        b = math.degrees(beamformed_aoa(two_path_table(10.0, 13.0, 1.0, 0.5), 3.0, "el")[0, 0])
        self.assertTrue(10.0 <= a < b < 13.0)

    def test_larger_aperture_resolves_what_a_small_one_merges(self):
        from rf_tomography.measurements.raytraced import beamformed_aoa
        t = two_path_table(10.0, 14.0, 1.0, 0.8)
        small = math.degrees(beamformed_aoa(t, 1.0, "el")[0, 0])
        big = math.degrees(beamformed_aoa(t, 12.0, "el")[0, 0])
        self.assertGreater(abs(small - 10.0), abs(big - 10.0))

    def test_azimuth_axis_and_no_path_link(self):
        from rf_tomography.measurements.raytraced import beamformed_aoa
        t = two_path_table(5.0, 5.0, 1.0, 0.0, az1_deg=-170.0, az2_deg=-170.0)
        self.assertAlmostEqual(math.degrees(beamformed_aoa(t, 3.0, "az")[0, 0]), -170.0, delta=0.26)
        t.power[:] = 0
        self.assertTrue(np.isnan(beamformed_aoa(t, 3.0, "az")[0, 0]))


if __name__ == "__main__":
    unittest.main()
