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
    extract_reaction, extract_reaction_trapezoidal_v1,
    IncompleteCoverageError, NonUniformSamplingError,
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


def write_raw_csv(path, headers, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(headers)
        w.writerows(rows)


class TestReviewFixes(unittest.TestCase):
    """Regression tests for the 2026-10-11 review findings."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "th.csv")

    def _rows(self, n_cols, val="250.0"):
        return [["%.6f" % t] + [val] * n_cols
                for t in (0.20, 0.25, 0.30)]

    def test_header_node_is_whole_integer_not_substring(self):
        """Node 1 must not claim a 'REACZ node 51' column."""
        write_raw_csv(self.path, ["time", "REACZ node 51"], self._rows(1))
        with self.assertRaises(IncompleteCoverageError):
            extract_reaction(self.path, [1])

    def test_header_matching_is_order_independent(self):
        """Nodes 1 and 51 each resolve to their own column."""
        write_raw_csv(self.path, ["time", "REACZ node 51", "REACZ node 1"],
                      [["0.200000", "100.0", "7.0"],
                       ["0.250000", "100.0", "7.0"],
                       ["0.300000", "100.0", "7.0"]])
        rz, n, k = extract_reaction(self.path, [1, 51])
        self.assertAlmostEqual(rz, 107.0)
        self.assertEqual(k, 2)

    def test_ambiguous_header_refused(self):
        """A header naming two wall nodes is refused, not guessed."""
        write_raw_csv(self.path, ["time", "REACZ nodes 1 2"], self._rows(1))
        with self.assertRaises(IncompleteCoverageError):
            extract_reaction(self.path, [1, 2])

    def test_non_finite_spellings_refused(self):
        for bad in ("+inf", "Infinity", "-Infinity", "+nan", "NaN", "inf",
                    "", "n/a"):
            rows = self._rows(2)
            rows[1][2] = bad
            write_raw_csv(self.path, ["time", "REACZ node 1", "REACZ node 2"],
                          rows)
            with self.assertRaises(IncompleteCoverageError, msg=repr(bad)):
                extract_reaction(self.path, [1, 2])

    def test_dt_tolerance_is_a_parameter(self):
        """Default stays frozen at 1e-9; a caller may pass an explicit one."""
        times = [0.20, 0.2005, 0.2010 + 2e-8, 0.2015]
        rows = [["%.9f" % t, "10.0"] for t in times]
        write_raw_csv(self.path, ["time", "REACZ node 1"], rows)
        with self.assertRaises(NonUniformSamplingError):
            extract_reaction(self.path, [1])
        rz, n, k = extract_reaction(self.path, [1], dt_tolerance=1e-6)
        self.assertAlmostEqual(rz, 10.0)

    def test_v1_non_finite_refused(self):
        headers = ["time", '"wall reaction" 1 var 53', '"wall reaction" 2 var 56']
        for bad in ("+inf", "Infinity", "+nan", "nan", ""):
            rows = self._rows(2)
            rows[1][1] = bad
            write_raw_csv(self.path, headers, rows)
            with self.assertRaises(IncompleteCoverageError, msg=repr(bad)):
                extract_reaction_trapezoidal_v1(self.path, [1, 2], [53, 56])

    def test_v1_finite_still_works(self):
        headers = ["time", '"wall reaction" 1 var 53', '"wall reaction" 2 var 56']
        write_raw_csv(self.path, headers, self._rows(2))
        rz, t0, t1, n, k = extract_reaction_trapezoidal_v1(
            self.path, [1, 2], [53, 56])
        self.assertAlmostEqual(rz, 500.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
