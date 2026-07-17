import csv
import tempfile
import unittest
from pathlib import Path

from biohub_cellops.submission_guard import (
    KAGGLE_SUBMISSION_COLUMNS,
    KaggleSubmissionCompiler,
    SubmissionGuard,
    SubmissionValidationException,
)


def node(row_id, dataset, node_id, t, z=10, y=20, x=30):
    return {
        "id": row_id,
        "dataset": dataset,
        "row_type": "node",
        "node_id": node_id,
        "t": t,
        "z": z,
        "y": y,
        "x": x,
        "source_id": -1,
        "target_id": -1,
    }


def edge(row_id, dataset, source_id, target_id):
    return {
        "id": row_id,
        "dataset": dataset,
        "row_type": "edge",
        "node_id": -1,
        "t": -1,
        "z": -1,
        "y": -1,
        "x": -1,
        "source_id": source_id,
        "target_id": target_id,
    }


class TestSubmissionGuard(unittest.TestCase):
    def setUp(self):
        self.guard = SubmissionGuard()

    def test_valid_node_edge_rows(self):
        rows = [node(0, "movie", 1, 0), node(1, "movie", 2, 1), edge(2, "movie", 1, 2)]
        self.assertTrue(self.guard.validate_rows(rows))

    def test_valid_binary_division(self):
        rows = [
            node(0, "movie", 1, 0),
            node(1, "movie", 2, 1),
            node(2, "movie", 3, 1),
            edge(3, "movie", 1, 2),
            edge(4, "movie", 1, 3),
        ]
        self.assertTrue(self.guard.validate_rows(rows))

    def test_rejects_third_outgoing_edge(self):
        rows = [
            node(0, "movie", 1, 0),
            node(1, "movie", 2, 1),
            node(2, "movie", 3, 1),
            node(3, "movie", 4, 1),
            edge(4, "movie", 1, 2),
            edge(5, "movie", 1, 3),
            edge(6, "movie", 1, 4),
        ]
        with self.assertRaises(SubmissionValidationException):
            self.guard.validate_rows(rows)

    def test_rejects_cross_dataset_edge(self):
        rows = [node(0, "a", 1, 0), node(1, "b", 2, 1), edge(2, "a", 1, 2)]
        with self.assertRaises(SubmissionValidationException):
            self.guard.validate_rows(rows)

    def test_rejects_non_forward_edge(self):
        rows = [node(0, "movie", 1, 1), node(1, "movie", 2, 1), edge(2, "movie", 1, 2)]
        with self.assertRaises(SubmissionValidationException):
            self.guard.validate_rows(rows)

    def test_csv_round_trip(self):
        rows = [node(0, "movie", 1, 0), node(1, "movie", 2, 1), edge(2, "movie", 1, 2)]
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "submission.csv"
            KaggleSubmissionCompiler.write_csv(rows, path)
            self.assertTrue(self.guard.validate_csv(path))
            with path.open(newline="", encoding="utf-8") as output_file:
                self.assertEqual(next(csv.reader(output_file)), KAGGLE_SUBMISSION_COLUMNS)

    def test_dataset_context_reports_coverage_and_counts(self):
        rows = [node(0, "movie", 1, 0), node(1, "movie", 2, 1), edge(2, "movie", 1, 2)]
        report = KaggleSubmissionCompiler.validate_dataset_context(
            rows,
            {"movie": (2, 11, 21, 31)},
        )
        self.assertEqual(report["movie"]["nodes"], 2)
        self.assertEqual(report["movie"]["edges"], 1)
        self.assertEqual(report["movie"]["edge_node_ratio"], 0.5)

    def test_dataset_context_rejects_missing_dataset_and_out_of_bounds_node(self):
        rows = [node(0, "movie", 1, 0)]
        with self.assertRaises(SubmissionValidationException):
            KaggleSubmissionCompiler.validate_dataset_context(
                rows,
                {"movie": (2, 11, 21, 31), "missing": (2, 11, 21, 31)},
            )

        out_of_bounds = [node(0, "movie", 1, 0, z=11)]
        with self.assertRaises(SubmissionValidationException):
            KaggleSubmissionCompiler.validate_dataset_context(
                out_of_bounds,
                {"movie": (2, 11, 21, 31)},
            )


if __name__ == "__main__":
    unittest.main()
