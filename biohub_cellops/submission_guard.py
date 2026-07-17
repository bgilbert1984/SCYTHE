"""Canonical Kaggle node/edge submission compilation and validation."""

import csv
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

import numpy as np


KAGGLE_SUBMISSION_COLUMNS = [
    "id",
    "dataset",
    "row_type",
    "node_id",
    "t",
    "z",
    "y",
    "x",
    "source_id",
    "target_id",
]


class KaggleSubmissionValidationError(ValueError):
    """Raised when CellOps state cannot satisfy the competition CSV contract."""


# Backward-compatible exception name for callers that imported the old guard.
SubmissionValidationException = KaggleSubmissionValidationError


class KaggleSubmissionCompiler:
    """Compile CellOps detections and links into Kaggle node/edge submission rows.

    Cell objects must expose ``id``, ``embryo_id``, ``t``, ``z``, ``y``, and ``x``.
    Link objects must expose ``id``, ``embryo_id``, ``source_cell_id``, and
    ``target_cell_id``. ``embryo_id`` is treated as Kaggle's exact dataset value.
    """

    columns = KAGGLE_SUBMISSION_COLUMNS

    @classmethod
    def compile(
        cls,
        cells: Sequence[Any],
        links: Sequence[Any],
    ) -> List[Dict[str, Any]]:
        if not cells:
            raise KaggleSubmissionValidationError("Cannot compile an empty Kaggle submission.")

        cells_by_key: Dict[Tuple[str, str], Any] = {}
        for cell in cells:
            dataset = str(cell.embryo_id).strip()
            cell_id = str(cell.id).strip()
            if not dataset:
                raise KaggleSubmissionValidationError(f"Cell '{cell.id}' has an empty dataset identifier.")
            if not cell_id:
                raise KaggleSubmissionValidationError("A cell has an empty internal ID.")
            key = (dataset, cell_id)
            if key in cells_by_key:
                raise KaggleSubmissionValidationError(
                    f"Duplicate cell ID '{cell_id}' in dataset '{dataset}'."
                )
            if not all(np.isfinite(float(value)) for value in (cell.z, cell.y, cell.x)):
                raise KaggleSubmissionValidationError(
                    f"Cell '{cell_id}' in dataset '{dataset}' has non-finite coordinates."
                )
            cells_by_key[key] = cell

        ordered_keys = sorted(
            cells_by_key,
            key=lambda key: (key[0], int(cells_by_key[key].t), key[1]),
        )
        node_ids = {key: node_id for node_id, key in enumerate(ordered_keys, start=1)}

        rows: List[Dict[str, Any]] = []
        for key in ordered_keys:
            cell = cells_by_key[key]
            rows.append({
                "id": len(rows),
                "dataset": key[0],
                "row_type": "node",
                "node_id": node_ids[key],
                "t": int(cell.t),
                "z": int(round(float(cell.z))),
                "y": int(round(float(cell.y))),
                "x": int(round(float(cell.x))),
                "source_id": -1,
                "target_id": -1,
            })

        edge_keys = set()
        ordered_links = sorted(
            links,
            key=lambda link: (
                str(link.embryo_id),
                str(link.source_cell_id),
                str(link.target_cell_id),
            ),
        )
        for link in ordered_links:
            dataset = str(link.embryo_id).strip()
            source_key = (dataset, str(link.source_cell_id).strip())
            target_key = (dataset, str(link.target_cell_id).strip())
            if source_key not in node_ids or target_key not in node_ids:
                raise KaggleSubmissionValidationError(
                    f"Edge '{link.id}' references a cell missing from dataset '{dataset}'."
                )
            edge_key = (dataset, node_ids[source_key], node_ids[target_key])
            if edge_key in edge_keys:
                raise KaggleSubmissionValidationError(
                    f"Duplicate edge in dataset '{dataset}': {edge_key[1]} -> {edge_key[2]}."
                )
            edge_keys.add(edge_key)
            rows.append({
                "id": len(rows),
                "dataset": dataset,
                "row_type": "edge",
                "node_id": -1,
                "t": -1,
                "z": -1,
                "y": -1,
                "x": -1,
                "source_id": node_ids[source_key],
                "target_id": node_ids[target_key],
            })

        cls.validate(rows)
        return rows

    @classmethod
    def validate(cls, rows: Sequence[Dict[str, Any]]) -> bool:
        if not rows:
            raise KaggleSubmissionValidationError("Kaggle submission has no rows.")

        if [row.get("id") for row in rows] != list(range(len(rows))):
            raise KaggleSubmissionValidationError("Submission row IDs must be consecutive from zero.")

        node_ids = set()
        node_datasets: Dict[int, str] = {}
        node_times: Dict[int, int] = {}
        edge_keys = set()
        outgoing_counts: Dict[Tuple[str, int], int] = {}
        for row in rows:
            if list(row.keys()) != cls.columns:
                raise KaggleSubmissionValidationError(
                    f"Submission columns must be exactly {cls.columns}."
                )
            if not str(row["dataset"]).strip():
                raise KaggleSubmissionValidationError("Every row must have a dataset identifier.")
            if row["row_type"] == "node":
                if row["node_id"] in node_ids or row["node_id"] < 1:
                    raise KaggleSubmissionValidationError(
                        f"Invalid or duplicate node_id: {row['node_id']}."
                    )
                if row["source_id"] != -1 or row["target_id"] != -1:
                    raise KaggleSubmissionValidationError("Node rows require -1 edge placeholders.")
                if any(
                    not isinstance(row[column], int) or row[column] < 0
                    for column in ("t", "z", "y", "x")
                ):
                    raise KaggleSubmissionValidationError(
                        "Node time and coordinates must be non-negative integers."
                    )
                node_ids.add(row["node_id"])
                node_datasets[row["node_id"]] = row["dataset"]
                node_times[row["node_id"]] = row["t"]
            elif row["row_type"] == "edge":
                if any(row[column] != -1 for column in ("node_id", "t", "z", "y", "x")):
                    raise KaggleSubmissionValidationError("Edge rows require -1 node placeholders.")
                edge_key = (row["dataset"], row["source_id"], row["target_id"])
                if edge_key in edge_keys:
                    raise KaggleSubmissionValidationError(f"Duplicate edge: {edge_key}.")
                edge_keys.add(edge_key)
            else:
                raise KaggleSubmissionValidationError(f"Unknown row_type: {row['row_type']}.")

        for row in rows:
            if row["row_type"] != "edge":
                continue
            if row["source_id"] not in node_ids or row["target_id"] not in node_ids:
                raise KaggleSubmissionValidationError("An edge references an unknown node_id.")
            if (
                node_datasets[row["source_id"]] != row["dataset"]
                or node_datasets[row["target_id"]] != row["dataset"]
            ):
                raise KaggleSubmissionValidationError("An edge crosses dataset boundaries.")
            if node_times[row["source_id"]] >= node_times[row["target_id"]]:
                raise KaggleSubmissionValidationError("An edge must point forward in time.")
            source_key = (row["dataset"], row["source_id"])
            outgoing_counts[source_key] = outgoing_counts.get(source_key, 0) + 1
            if outgoing_counts[source_key] > 2:
                raise KaggleSubmissionValidationError(
                    f"Node {row['source_id']} has more than two outgoing edges."
                )
        return True

    @classmethod
    def read_csv(cls, file_path: Union[str, Path]) -> List[Dict[str, Any]]:
        with Path(file_path).open(newline="", encoding="utf-8") as input_file:
            reader = csv.DictReader(input_file)
            if reader.fieldnames != cls.columns:
                raise KaggleSubmissionValidationError(
                    f"CSV columns {reader.fieldnames} do not match {cls.columns}."
                )
            rows = []
            for line_number, raw_row in enumerate(reader, start=2):
                try:
                    rows.append({
                        "id": int(raw_row["id"]),
                        "dataset": raw_row["dataset"],
                        "row_type": raw_row["row_type"],
                        "node_id": int(raw_row["node_id"]),
                        "t": int(raw_row["t"]),
                        "z": int(raw_row["z"]),
                        "y": int(raw_row["y"]),
                        "x": int(raw_row["x"]),
                        "source_id": int(raw_row["source_id"]),
                        "target_id": int(raw_row["target_id"]),
                    })
                except (TypeError, ValueError) as exc:
                    raise KaggleSubmissionValidationError(
                        f"CSV line {line_number} contains an invalid integer: {exc}"
                    ) from exc
        cls.validate(rows)
        return rows

    @classmethod
    def validate_dataset_context(
        cls,
        rows: Sequence[Dict[str, Any]],
        dataset_shapes: Mapping[str, Sequence[int]],
    ) -> Dict[str, Dict[str, Any]]:
        """Validate dataset coverage and node bounds against ``(T, Z, Y, X)`` shapes."""
        cls.validate(rows)
        normalized_shapes = {}
        for dataset, shape in dataset_shapes.items():
            if len(shape) != 4:
                raise KaggleSubmissionValidationError(
                    f"Dataset '{dataset}' shape must be (T, Z, Y, X), got {tuple(shape)}."
                )
            normalized = tuple(int(value) for value in shape)
            if any(value <= 0 for value in normalized):
                raise KaggleSubmissionValidationError(
                    f"Dataset '{dataset}' has a non-positive volume dimension: {normalized}."
                )
            normalized_shapes[str(dataset)] = normalized

        expected = set(normalized_shapes)
        actual = {str(row["dataset"]) for row in rows}
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        if missing or extra:
            raise KaggleSubmissionValidationError(
                f"Dataset coverage mismatch; missing={missing}, unexpected={extra}."
            )

        report = {
            dataset: {
                "shape_tzyx": shape,
                "nodes": 0,
                "edges": 0,
                "edge_node_ratio": 0.0,
            }
            for dataset, shape in normalized_shapes.items()
        }
        for row in rows:
            dataset = str(row["dataset"])
            if row["row_type"] == "edge":
                report[dataset]["edges"] += 1
                continue
            t_size, z_size, y_size, x_size = normalized_shapes[dataset]
            limits = {"t": t_size, "z": z_size, "y": y_size, "x": x_size}
            for coordinate, upper_bound in limits.items():
                if row[coordinate] >= upper_bound:
                    raise KaggleSubmissionValidationError(
                        f"Node {row['node_id']} in dataset '{dataset}' has {coordinate}="
                        f"{row[coordinate]} outside [0, {upper_bound})."
                    )
            report[dataset]["nodes"] += 1

        for dataset, counts in report.items():
            if counts["nodes"] == 0:
                raise KaggleSubmissionValidationError(
                    f"Dataset '{dataset}' has no node rows."
                )
            counts["edge_node_ratio"] = round(counts["edges"] / counts["nodes"], 6)
        return report

    @classmethod
    def write_csv(
        cls,
        rows: Sequence[Dict[str, Any]],
        file_path: Union[str, Path],
        sample_submission_path: Optional[Union[str, Path]] = None,
        dataset_shapes: Optional[Mapping[str, Sequence[int]]] = None,
    ) -> Path:
        cls.validate(rows)
        if dataset_shapes is not None:
            cls.validate_dataset_context(rows, dataset_shapes)
        if sample_submission_path is not None:
            with Path(sample_submission_path).open(newline="", encoding="utf-8") as sample_file:
                sample_columns = next(csv.reader(sample_file), None)
            if sample_columns != cls.columns:
                raise KaggleSubmissionValidationError(
                    f"Sample submission columns {sample_columns} do not match {cls.columns}."
                )

        output_path = Path(file_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", newline="", encoding="utf-8") as output_file:
            writer = csv.DictWriter(output_file, fieldnames=cls.columns)
            writer.writeheader()
            writer.writerows(rows)

        if len(cls.read_csv(output_path)) != len(rows):
            raise KaggleSubmissionValidationError("Serialized submission failed row-count validation.")
        return output_path


class SubmissionGuard:
    """Compatibility facade over the canonical Kaggle submission compiler."""

    REQUIRED_COLUMNS = KAGGLE_SUBMISSION_COLUMNS

    def validate_rows(self, rows: Sequence[Dict[str, Any]]) -> bool:
        return KaggleSubmissionCompiler.validate(rows)

    def validate_csv(self, file_path: Union[str, Path]) -> bool:
        KaggleSubmissionCompiler.read_csv(file_path)
        return True
