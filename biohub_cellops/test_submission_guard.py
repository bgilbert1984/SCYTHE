import unittest
import tempfile
import os
import csv
from biohub_cellops.submission_guard import SubmissionGuard, SubmissionValidationException

class TestSubmissionGuard(unittest.TestCase):
    def setUp(self):
        self.guard = SubmissionGuard()

    def test_valid_single_track(self):
        """Verifies validation passes on a perfectly normal sequential track."""
        rows = [
            {"cell_id": "c1", "parent_id": "", "embryo_id": "emb1", "t": 1, "z": 10.0, "y": 20.0, "x": 30.0},
            {"cell_id": "c2", "parent_id": "c1", "embryo_id": "emb1", "t": 2, "z": 10.5, "y": 20.1, "x": 29.8},
            {"cell_id": "c3", "parent_id": "c2", "embryo_id": "emb1", "t": 3, "z": 10.9, "y": 20.3, "x": 29.5}
        ]
        self.assertTrue(self.guard.validate_rows(rows))

    def test_valid_mitosis(self):
        """Verifies validation passes on a healthy, mass-conserved cellular binary split."""
        rows = [
            {"cell_id": "parent", "parent_id": "", "embryo_id": "emb1", "t": 1, "z": 10.0, "y": 20.0, "x": 30.0},
            {"cell_id": "d1", "parent_id": "parent", "embryo_id": "emb1", "t": 2, "z": 10.5, "y": 18.5, "x": 30.1},
            {"cell_id": "d2", "parent_id": "parent", "embryo_id": "emb1", "t": 2, "z": 9.5, "y": 21.5, "x": 29.9}
        ]
        self.assertTrue(self.guard.validate_rows(rows))

    def test_duplicate_cell_id(self):
        """Asserts that duplicate cell_id values within the submission throw an exception."""
        rows = [
            {"cell_id": "c1", "parent_id": "", "embryo_id": "emb1", "t": 1, "z": 10.0, "y": 20.0, "x": 30.0},
            {"cell_id": "c1", "parent_id": "", "embryo_id": "emb1", "t": 2, "z": 11.0, "y": 21.0, "x": 31.0}
        ]
        with self.assertRaises(SubmissionValidationException) as context:
            self.guard.validate_rows(rows)
        self.assertIn("Duplicate cell_id", str(context.exception))

    def test_missing_edge_source(self):
        """Asserts that pointing to a non-existent parent_id fails validation."""
        rows = [
            {"cell_id": "c2", "parent_id": "non_existent", "embryo_id": "emb1", "t": 2, "z": 10.5, "y": 20.1, "x": 29.8}
        ]
        with self.assertRaises(SubmissionValidationException) as context:
            self.guard.validate_rows(rows)
        self.assertIn("Dangling edge reference", str(context.exception))

    def test_cycle(self):
        """Asserts that circular tracking connections throw an exception."""
        rows = [
            {"cell_id": "c1", "parent_id": "c3", "embryo_id": "emb1", "t": 4, "z": 10.0, "y": 20.0, "x": 30.0},
            {"cell_id": "c2", "parent_id": "c1", "embryo_id": "emb1", "t": 5, "z": 10.5, "y": 20.1, "x": 29.8},
            {"cell_id": "c3", "parent_id": "c2", "embryo_id": "emb1", "t": 6, "z": 10.9, "y": 20.3, "x": 29.5}
        ]
        with self.assertRaises(SubmissionValidationException) as context:
            self.guard.validate_rows(rows)
        self.assertIn("Lineage cycle detected", str(context.exception))

    def test_triple_mitosis(self):
        """Asserts that a parent splitting into 3 daughter cells is flagged as a biological violation."""
        rows = [
            {"cell_id": "parent", "parent_id": "", "embryo_id": "emb1", "t": 1, "z": 10.0, "y": 20.0, "x": 30.0},
            {"cell_id": "d1", "parent_id": "parent", "embryo_id": "emb1", "t": 2, "z": 10.5, "y": 18.5, "x": 30.1},
            {"cell_id": "d2", "parent_id": "parent", "embryo_id": "emb1", "t": 2, "z": 9.5, "y": 21.5, "x": 29.9},
            {"cell_id": "d3", "parent_id": "parent", "embryo_id": "emb1", "t": 2, "z": 10.0, "y": 20.0, "x": 30.0}
        ]
        with self.assertRaises(SubmissionValidationException) as context:
            self.guard.validate_rows(rows)
        self.assertIn("Mitotic division violation", str(context.exception))

    def test_nan_coordinate(self):
        """Asserts that non-finite coordinates (NaN or Inf) fail validation."""
        rows = [
            {"cell_id": "c1", "parent_id": "", "embryo_id": "emb1", "t": 1, "z": float('nan'), "y": 20.0, "x": 30.0}
        ]
        with self.assertRaises(SubmissionValidationException) as context:
            self.guard.validate_rows(rows)
        self.assertIn("illegal non-finite", str(context.exception))

    def test_bad_timing(self):
        """Asserts that a child having a timestamp at or before its parent fails validation."""
        rows = [
            {"cell_id": "parent", "parent_id": "", "embryo_id": "emb1", "t": 3, "z": 10.0, "y": 20.0, "x": 30.0},
            {"cell_id": "child", "parent_id": "parent", "embryo_id": "emb1", "t": 2, "z": 10.5, "y": 20.1, "x": 29.8}
        ]
        with self.assertRaises(SubmissionValidationException) as context:
            self.guard.validate_rows(rows)
        self.assertIn("Temporal violation", str(context.exception))

    def test_csv_serialization_read_write(self):
        """Ensures the guard can successfully read and validate exported CSV files on disk."""
        rows = [
            {"cell_id": "c1", "parent_id": "", "embryo_id": "emb1", "t": "1", "z": "10.0", "y": "20.0", "x": "30.0"},
            {"cell_id": "c2", "parent_id": "c1", "embryo_id": "emb1", "t": "2", "z": "10.5", "y": "20.1", "x": "29.8"}
        ]
        
        # Write to temp file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, newline='', encoding='utf-8') as tmp:
            writer = csv.DictWriter(tmp, fieldnames=SubmissionGuard.REQUIRED_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
            tmp_path = tmp.name
            
        try:
            # Validate CSV file
            self.assertTrue(self.guard.validate_csv(tmp_path))
        finally:
            os.remove(tmp_path)

if __name__ == "__main__":
    unittest.main()
