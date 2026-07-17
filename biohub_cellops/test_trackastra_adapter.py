import unittest
from dataclasses import dataclass

from biohub_cellops.submission_guard import (
    KAGGLE_SUBMISSION_COLUMNS,
    KaggleSubmissionCompiler,
    KaggleSubmissionValidationError,
)
from biohub_cellops.trackastra_adapter import TrackastraCellOpsAdapter


@dataclass(frozen=True)
class NodeRow:
    dataset: str
    node_id: int
    t: int
    z: int
    y: int
    x: int


@dataclass(frozen=True)
class EdgeRow:
    dataset: str
    source_id: int
    target_id: int


class TestTrackastraCellOpsAdapter(unittest.TestCase):
    def test_adapts_notebook_dataclasses_and_compiles(self):
        nodes = [
            NodeRow("movie_a", 1, 0, 10, 20, 30),
            NodeRow("movie_a", 2, 1, 11, 21, 31),
            NodeRow("movie_b", 1, 0, 5, 6, 7),
        ]
        edges = [EdgeRow("movie_a", 1, 2)]

        cells, links = TrackastraCellOpsAdapter.adapt(nodes, edges)
        rows = KaggleSubmissionCompiler.compile(cells, links)

        self.assertEqual(len(cells), 3)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].link_type, "continuation")
        self.assertTrue(all(list(row) == KAGGLE_SUBMISSION_COLUMNS for row in rows))
        self.assertEqual([row["row_type"] for row in rows], ["node", "node", "node", "edge"])

    def test_marks_two_outgoing_edges_as_division(self):
        nodes = [
            NodeRow("movie_a", 1, 0, 10, 20, 30),
            NodeRow("movie_a", 2, 1, 11, 21, 31),
            NodeRow("movie_a", 3, 1, 9, 19, 29),
        ]
        edges = [EdgeRow("movie_a", 1, 2), EdgeRow("movie_a", 1, 3)]

        _, links = TrackastraCellOpsAdapter.adapt(nodes, edges)

        self.assertEqual([link.link_type for link in links], ["division", "division"])

    def test_rejects_dangling_trackastra_edge(self):
        nodes = [NodeRow("movie_a", 1, 0, 10, 20, 30)]
        edges = [EdgeRow("movie_a", 1, 99)]

        with self.assertRaises(KaggleSubmissionValidationError):
            TrackastraCellOpsAdapter.adapt(nodes, edges)


if __name__ == "__main__":
    unittest.main()
