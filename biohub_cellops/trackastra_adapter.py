"""Adapters from Trackastra notebook rows to CellOps runtime records."""

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any, List, Tuple

from biohub_cellops.core import CellDetection, CellTrackLink
from biohub_cellops.submission_guard import KaggleSubmissionValidationError


def _records(value: Any) -> Iterable[Any]:
    """Accept lists of objects/mappings or a pandas-like DataFrame."""
    if hasattr(value, "columns") and hasattr(value, "to_dict"):
        return value.to_dict(orient="records")
    return value


def _field(record: Any, name: str) -> Any:
    if isinstance(record, Mapping):
        if name not in record:
            raise KaggleSubmissionValidationError(f"Trackastra record is missing '{name}'.")
        return record[name]
    if not hasattr(record, name):
        raise KaggleSubmissionValidationError(f"Trackastra record is missing '{name}'.")
    return getattr(record, name)


class TrackastraCellOpsAdapter:
    """Convert notebook-style ``NodeRow``/``EdgeRow`` values into CellOps objects.

    Node IDs only need to be unique within a dataset. The adapter creates stable
    internal string IDs and preserves the exact Trackastra dataset name so the
    canonical submission compiler can assign global Kaggle node IDs afterward.
    """

    source_run = "trackastra"

    @classmethod
    def adapt(
        cls,
        node_rows: Iterable[Any],
        edge_rows: Iterable[Any],
        confidence: float = 1.0,
    ) -> Tuple[List[CellDetection], List[CellTrackLink]]:
        nodes = list(_records(node_rows))
        edges = list(_records(edge_rows))
        if not nodes:
            raise KaggleSubmissionValidationError("Trackastra produced no node rows.")

        cells: List[CellDetection] = []
        cell_ids = {}
        for node in nodes:
            dataset = str(_field(node, "dataset")).strip()
            node_id = int(_field(node, "node_id"))
            if not dataset:
                raise KaggleSubmissionValidationError("Trackastra node has an empty dataset.")
            key = (dataset, node_id)
            if key in cell_ids:
                raise KaggleSubmissionValidationError(
                    f"Duplicate Trackastra node_id {node_id} in dataset '{dataset}'."
                )
            internal_id = f"trackastra:{dataset}:{node_id}"
            cell_ids[key] = internal_id
            cells.append(CellDetection(
                id=internal_id,
                embryo_id=dataset,
                t=int(_field(node, "t")),
                z=float(_field(node, "z")),
                y=float(_field(node, "y")),
                x=float(_field(node, "x")),
                confidence=float(confidence),
                source_run=cls.source_run,
                metadata={"trackastra_node_id": node_id},
            ))

        edge_specs = []
        for edge in edges:
            dataset = str(_field(edge, "dataset")).strip()
            source_id = int(_field(edge, "source_id"))
            target_id = int(_field(edge, "target_id"))
            source_key = (dataset, source_id)
            target_key = (dataset, target_id)
            if source_key not in cell_ids or target_key not in cell_ids:
                raise KaggleSubmissionValidationError(
                    f"Trackastra edge {source_id} -> {target_id} references a missing node "
                    f"in dataset '{dataset}'."
                )
            edge_specs.append((dataset, source_id, target_id))

        if len(edge_specs) != len(set(edge_specs)):
            raise KaggleSubmissionValidationError("Trackastra produced duplicate edges.")
        outgoing = Counter((dataset, source_id) for dataset, source_id, _ in edge_specs)

        links = [
            CellTrackLink(
                id=f"trackastra-edge:{dataset}:{source_id}:{target_id}",
                embryo_id=dataset,
                source_cell_id=cell_ids[(dataset, source_id)],
                target_cell_id=cell_ids[(dataset, target_id)],
                confidence=float(confidence),
                link_type="division" if outgoing[(dataset, source_id)] == 2 else "continuation",
                source_run=cls.source_run,
                metadata={
                    "trackastra_source_id": source_id,
                    "trackastra_target_id": target_id,
                },
            )
            for dataset, source_id, target_id in edge_specs
        ]
        return cells, links

