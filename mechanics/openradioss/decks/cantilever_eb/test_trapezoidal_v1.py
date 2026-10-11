"""Dedicated tests for extract_reaction_trapezoidal_v1.

The eight legacy tests in test_reaction_extractor.py validate the
uniform-cadence extract_reaction(); they do not exercise the v1
converter-format path. These tests cover:
  - irregular spacing: trapezoidal weighting, not sample mean
  - signed aggregation: signs preserved through the sum
  - endpoint handling: actual endpoints reported, no nominal extension
  - var/label channel resolution and cross-check refusal
"""

import csv
import os
import tempfile
import unittest

from reaction_extractor import (
    extract_reaction_trapezoidal_v1,
    IncompleteCoverageError,
)


def _write_csv(path, headers, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(headers)
        w.writerows(rows)


def _converter_header(node_id, var_num, title="wall reaction"):
    # Mimics th_to_csv label format
    return '"%s%40d%40s %d"' % (title, node_id, "var", var_num)


class TestTrapezoidalV1(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.csv_path = os.path.join(self.tmp, "test.csv")

    def _basic_headers(self):
        # nodes 1,2 with REACZ vars 53, 56 (as in the real deck)
        return ["time", _converter_header(1, 53), _converter_header(2, 56)]

    def test_irregular_spacing_time_weighted(self):
        """Trapezoidal mean must weight by dt, not count samples.

        Constant value 100 over [0.2, 0.3] then 300 over [0.3, 0.5]:
        time-weighted mean = (100*0.1 + 300*0.2)/0.3 = 233.333...
        Sample mean would differ if sample counts per segment differ.
        """
        headers = self._basic_headers()
        # Dense sampling in first segment, sparse in second
        rows = []
        t = 0.2
        while t < 0.3:
            rows.append([t, 50.0, 50.0])  # sum = 100
            t += 0.01
        t = 0.3
        while t <= 0.5:
            rows.append([t, 150.0, 150.0])  # sum = 300
            t += 0.1
        _write_csv(self.csv_path, headers, rows)

        rz, t0, t1, n, nn = extract_reaction_trapezoidal_v1(
            self.csv_path, [1, 2], [53, 56], t_start=0.2, t_end=0.5)
        # Time-weighted via trapezoidal: the 0.29->0.3 interval
        # interpolates 100->300, so integral = 100*0.09 + 200*0.01 + 300*0.2
        expected = (100*0.09 + 0.5*(100+300)*0.01 + 300*0.2) / 0.3
        self.assertAlmostEqual(rz, expected, places=6)
        # Sample mean would be different; verify we're not doing that
        sample_mean = (100*10 + 300*3) / 13
        self.assertNotAlmostEqual(rz, sample_mean, places=2)

    def test_signed_aggregation(self):
        """Positive and negative REACZ must sum with signs preserved."""
        headers = self._basic_headers()
        rows = [
            [0.25, 600.0, -100.0],  # sum = 500
            [0.35, 600.0, -100.0],
            [0.45, 600.0, -100.0],
        ]
        _write_csv(self.csv_path, headers, rows)
        rz, t0, t1, n, nn = extract_reaction_trapezoidal_v1(
            self.csv_path, [1, 2], [53, 56], t_start=0.2, t_end=0.5)
        self.assertAlmostEqual(rz, 500.0, places=9)

    def test_endpoints_reported_actual(self):
        """Observed endpoints must be the actual first/last timestamps."""
        headers = self._basic_headers()
        rows = [
            [0.2000024, 100.0, 100.0],
            [0.3000000, 100.0, 100.0],
            [0.4995024, 100.0, 100.0],
        ]
        _write_csv(self.csv_path, headers, rows)
        rz, t0, t1, n, nn = extract_reaction_trapezoidal_v1(
            self.csv_path, [1, 2], [53, 56], t_start=0.2, t_end=0.5)
        self.assertAlmostEqual(t0, 0.2000024, places=7)
        self.assertAlmostEqual(t1, 0.4995024, places=7)
        # Must NOT extend to nominal 0.50
        self.assertLess(t1, 0.50)

    def test_var_mismatch_refuses(self):
        """Wrong var numbers must raise, not silently map."""
        headers = self._basic_headers()  # vars 53, 56
        rows = [[0.25, 100.0, 100.0]]
        _write_csv(self.csv_path, headers, rows)
        # Ask for var 54 (which is REACY, not REACZ) for node 1
        with self.assertRaises(IncompleteCoverageError):
            extract_reaction_trapezoidal_v1(
                self.csv_path, [1, 2], [54, 56], t_start=0.2, t_end=0.5)

    def test_missing_node_refuses(self):
        """Absent wall node must raise, not become zero."""
        headers = ["time", _converter_header(1, 53)]  # only node 1
        rows = [[0.25, 100.0]]
        _write_csv(self.csv_path, headers, rows)
        with self.assertRaises(IncompleteCoverageError):
            extract_reaction_trapezoidal_v1(
                self.csv_path, [1, 2], [53, 56], t_start=0.2, t_end=0.5)

    def test_nan_refuses(self):
        """NaN value must raise, not become zero."""
        headers = self._basic_headers()
        rows = [
            [0.25, 100.0, 100.0],
            [0.35, float("nan"), 100.0],
        ]
        _write_csv(self.csv_path, headers, rows)
        with self.assertRaises(IncompleteCoverageError):
            extract_reaction_trapezoidal_v1(
                self.csv_path, [1, 2], [53, 56], t_start=0.2, t_end=0.5)

    def test_empty_window_refuses(self):
        """No samples in window must raise."""
        headers = self._basic_headers()
        rows = [[0.05, 100.0, 100.0]]
        _write_csv(self.csv_path, headers, rows)
        with self.assertRaises(IncompleteCoverageError):
            extract_reaction_trapezoidal_v1(
                self.csv_path, [1, 2], [53, 56], t_start=0.2, t_end=0.5)


if __name__ == "__main__":
    unittest.main()
