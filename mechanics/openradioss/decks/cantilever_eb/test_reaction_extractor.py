"""Synthetic TH fixtures for the committed REACZ extractor.

Each fixture exposes a specific bug class:
- mean_vs_sum: verifies the extractor SUMS (not means) across nodes
- sign_loss: verifies signed (not absolute) summation
- missing_node: verifies refusal when a wall node is absent
- duplicate_node: verifies refusal on duplicate node IDs in the request
- wrong_window: verifies time-window selection
- nan_value: verifies refusal on absent/NaN values (not zero-substitution)
- nonuniform_dt: verifies refusal on non-uniform sampling

Provenance: synthetic fixtures only. No measured data.
"""

import csv
import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from reaction_extractor import (
    extract_reaction, IncompleteCoverageError, NonUniformSamplingError,
    T_START, T_END,
)


def write_th_csv(path, times, node_ids, values):
    """Write a synthetic TH CSV.

    values: dict node_id -> list of REACZ per time step.
    """
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        headers = ["time"] + [
            "REACZ node %d" % nid for nid in node_ids]
        w.writerow(headers)
        for i, t in enumerate(times):
            row = ["%.6f" % t] + [
                "%.6f" % values[nid][i] for nid in node_ids]
            w.writerow(row)


def uniform_times(dt=0.0005, t_max=0.6):
    n = int(t_max / dt) + 1
    return [i * dt for i in range(n)]


class TestReactionExtractor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "th.csv")

    def test_mean_vs_sum(self):
        """4 wall nodes at 250 N each must sum to 1000 N, not mean to 250 N."""
        times = uniform_times()
        nids = [1, 2, 3, 4]
        # Each node carries 250 N constant in the window
        vals = {nid: [250.0]*len(times) for nid in nids}
        write_th_csv(self.path, times, nids, vals)
        rz, ns, nn = extract_reaction(self.path, nids)
        self.assertAlmostEqual(rz, 1000.0, places=6,
            msg="Extractor must SUM across nodes, not average")
        self.assertEqual(nn, 4)

    def test_sign_loss(self):
        """Signed sum: +600 and -100 must give 500, not 700."""
        times = uniform_times()
        nids = [1, 2]
        vals = {1: [600.0]*len(times), 2: [-100.0]*len(times)}
        write_th_csv(self.path, times, nids, vals)
        rz, _, _ = extract_reaction(self.path, nids)
        self.assertAlmostEqual(rz, 500.0, places=6,
            msg="Extractor must use SIGNED sum, not absolute values")

    def test_missing_node(self):
        """Absent wall node must raise, not become zero."""
        times = uniform_times()
        # TH has nodes 1,2,3 but wall requires 1,2,3,4
        nids_th = [1, 2, 3]
        vals = {nid: [250.0]*len(times) for nid in nids_th}
        write_th_csv(self.path, times, nids_th, vals)
        with self.assertRaises(IncompleteCoverageError):
            extract_reaction(self.path, [1, 2, 3, 4])

    def test_duplicate_node_ids(self):
        """Duplicate node IDs in the request must raise."""
        times = uniform_times()
        nids = [1, 2, 3]
        vals = {nid: [250.0]*len(times) for nid in nids}
        write_th_csv(self.path, times, nids, vals)
        with self.assertRaises(IncompleteCoverageError):
            extract_reaction(self.path, [1, 2, 2, 3])

    def test_wrong_window(self):
        """Samples outside [0.20, 0.50] must not contribute."""
        times = uniform_times()
        nids = [1, 2]
        # 1000 N in window, 0 N outside — mean must be 1000
        vals = {}
        for nid in nids:
            vals[nid] = [
                500.0 if T_START <= t <= T_END else 0.0
                for t in times]
        write_th_csv(self.path, times, nids, vals)
        rz, ns, _ = extract_reaction(self.path, nids)
        self.assertAlmostEqual(rz, 1000.0, places=6,
            msg="Only samples in [0.20, 0.50] s may contribute")
        # Verify sample count matches expected window coverage
        expected = sum(1 for t in times if T_START <= t <= T_END)
        self.assertEqual(ns, expected)

    def test_nan_value(self):
        """NaN REACZ must raise, not become zero."""
        times = uniform_times()
        nids = [1, 2]
        vals = {1: [500.0]*len(times), 2: [500.0]*len(times)}
        # Corrupt one value in the window
        mid = len(times) // 2
        vals[2][mid] = float("nan")
        # Write manually to preserve nan
        with open(self.path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["time"] + ["REACZ node %d" % n for n in nids])
            for i, t in enumerate(times):
                w.writerow(["%.6f" % t, "%.6f" % vals[1][i],
                            "nan" if i == mid else "%.6f" % vals[2][i]])
        with self.assertRaises(IncompleteCoverageError):
            extract_reaction(self.path, nids)

    def test_nonuniform_dt(self):
        """Non-uniform timesteps must raise (sample mean invalid)."""
        times = uniform_times()
        # Perturb a sample INSIDE the [0.20, 0.50] window
        # t=0.30 is at index 600; perturb beyond tolerance (survives %.6f)
        times[600] += 1e-4
        nids = [1, 2]
        vals = {nid: [500.0]*len(times) for nid in nids}
        write_th_csv(self.path, times, nids, vals)
        with self.assertRaises(NonUniformSamplingError):
            extract_reaction(self.path, nids)

    def test_empty_window(self):
        """No samples in window must raise."""
        times = [0.01, 0.02, 0.03]  # all before window
        nids = [1, 2]
        vals = {nid: [500.0]*3 for nid in nids}
        write_th_csv(self.path, times, nids, vals)
        with self.assertRaises(IncompleteCoverageError):
            extract_reaction(self.path, nids)


if __name__ == "__main__":
    unittest.main(verbosity=2)
