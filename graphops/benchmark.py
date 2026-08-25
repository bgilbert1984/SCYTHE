"""Evidence-set-first evaluation primitives for GraphFusion relational lift.

This module is intentionally independent of runtime retrieval modes and model
answers.  Benchmark fixtures supply relationship IDs judged relevant in
advance; the evaluator compares recovered evidence constituents only.
"""

from __future__ import annotations

import json
from typing import Any, Iterable


def path_constituents(paths: Iterable[dict[str, Any]]) -> set[str]:
    """Return stable graph edge IDs represented by admitted evidence paths."""
    result: set[str] = set()
    for path in paths:
        edge_ids = path.get("edgeIds")
        if edge_ids is None:
            edge_ids = [step.get("id") for step in path.get("steps", [])
                        if step.get("type") == "edge"]
        result.update(str(item) for item in edge_ids if item)
    return result


def canonical_path_signature(path: dict[str, Any], *, mode: str = "identity") -> str:
    """Serialize one ordered relational composition without flattening its steps.

    `identity` preserves exact node/edge IDs and is the replay benchmark default.
    `kind` uses the evidence records' entity kinds for corpus families where
    fixture IDs are intentionally variable.
    """
    if mode not in {"identity", "kind"}:
        raise ValueError("path signature mode must be 'identity' or 'kind'")
    steps = list(path.get("steps") or [])
    if not steps:
        steps = [{"type": "edge", "id": item}
                 for item in path.get("edgeIds") or []]
    evidence_by_key = {}
    for item in path.get("evidence") or []:
        evidence_by_key[(str(item.get("type")), str(item.get("id")))] = item
    signature = []
    for step in steps:
        step_type = str(step.get("type") or "entity")
        step_id = str(step.get("id") or "")
        if mode == "identity":
            value = step_id
        else:
            evidence = evidence_by_key.get((step_type, step_id), {})
            value = str(evidence.get("kind") or evidence.get("entityKind") or "UNKNOWN")
        signature.append((step_type, value))
    return json.dumps(signature, ensure_ascii=False, separators=(",", ":"))


def path_signatures(paths: Iterable[dict[str, Any]], *, mode: str = "identity") -> set[str]:
    """Return canonical ordered signatures for a collection of paths."""
    return {canonical_path_signature(path, mode=mode) for path in paths}


def operator_root_coverage(paths: Iterable[dict[str, Any]],
                           expected_root_ids: Iterable[str]) -> dict[str, Any]:
    """Diagnose whether admitted paths represent every selected hyperedge member."""
    expected = {str(item) for item in expected_root_ids}
    represented = {
        str(path.get("seedId")) for path in paths
        if path.get("seedOrigin") == "OPERATOR_SELECTION" and path.get("seedId")
    }
    covered = expected & represented
    return {
        "expectedRoots": sorted(expected),
        "representedRoots": sorted(covered),
        "missingRoots": sorted(expected - covered),
        "coverage": round(len(covered) / max(1, len(expected)), 6),
    }


def _fixture_signatures(values: Iterable[str | dict[str, Any]], *, mode: str) -> set[str]:
    return {
        canonical_path_signature(value, mode=mode) if isinstance(value, dict) else str(value)
        for value in values
    }


def evaluate_relational_lift(
    *,
    operator_evidence_ids: Iterable[str],
    semantic_evidence_ids: Iterable[str],
    graph_paths: Iterable[dict[str, Any]],
    fused_paths: Iterable[dict[str, Any]],
    relevant_fixture_ids: Iterable[str],
    operator_paths: Iterable[dict[str, Any]] = (),
    semantic_paths: Iterable[dict[str, Any]] = (),
    relevant_path_signatures: Iterable[str | dict[str, Any]] = (),
    path_signature_mode: str = "identity",
) -> dict[str, Any]:
    """Compare O, S, G and F against fixture relationship identities.

    `semantic_evidence_ids` are evaluator-resolved relationship identities, not
    semantic entity hits.  This makes the semantic-only arm comparable to graph
    paths without pretending that a seed itself establishes a relationship.
    """
    graph_paths = list(graph_paths)
    fused_paths = list(fused_paths)
    edge_evidence = {
        "operator": {str(item) for item in operator_evidence_ids},
        "semantic": {str(item) for item in semantic_evidence_ids},
        "graph": path_constituents(graph_paths),
        "fused": path_constituents(fused_paths),
    }
    relevant_edges = {str(item) for item in relevant_fixture_ids}
    edge_recovered = {name: values & relevant_edges
                      for name, values in edge_evidence.items()}
    independent_edges = (edge_recovered["operator"] | edge_recovered["semantic"] |
                         edge_recovered["graph"])
    edge_exclusive = edge_recovered["fused"] - independent_edges
    edge_denominator = max(1, len(relevant_edges))

    path_evidence = {
        "operator": path_signatures(operator_paths, mode=path_signature_mode),
        "semantic": path_signatures(semantic_paths, mode=path_signature_mode),
        "graph": path_signatures(graph_paths, mode=path_signature_mode),
        "fused": path_signatures(fused_paths, mode=path_signature_mode),
    }
    relevant_paths = _fixture_signatures(
        relevant_path_signatures, mode=path_signature_mode)
    path_adjudicated = bool(relevant_paths)
    path_recovered = {name: values & relevant_paths
                      for name, values in path_evidence.items()}
    independent_paths = (path_recovered["operator"] | path_recovered["semantic"] |
                         path_recovered["graph"])
    path_exclusive = path_recovered["fused"] - independent_paths
    path_denominator = max(1, len(relevant_paths))
    return {
        # Existing names remain aliases for the conservative edge-level metric.
        "evidenceSets": {name: sorted(values) for name, values in edge_evidence.items()},
        "relevantRecovered": {name: sorted(values)
                              for name, values in edge_recovered.items()},
        "relevantRecall": {
            name: round(len(values) / edge_denominator, 6)
            for name, values in edge_recovered.items()
        },
        "fusionExclusive": sorted(edge_exclusive),
        "fusionExclusiveCount": len(edge_exclusive),
        "edgeEvidenceSets": {name: sorted(values) for name, values in edge_evidence.items()},
        "edgeRelevantRecovered": {name: sorted(values)
                                  for name, values in edge_recovered.items()},
        "edgeRelevantRecall": {
            name: round(len(values) / edge_denominator, 6)
            for name, values in edge_recovered.items()
        },
        "edgeFusionExclusive": sorted(edge_exclusive),
        "edgeFusionExclusiveCount": len(edge_exclusive),
        "pathSignatureMode": path_signature_mode,
        "pathMetricsStatus": "ADJUDICATED" if path_adjudicated else "NOT_ADJUDICATED",
        "pathEvidenceSets": {name: sorted(values) for name, values in path_evidence.items()},
        "pathRelevantRecovered": {name: sorted(values)
                                  for name, values in path_recovered.items()},
        "pathRelevantRecall": {
            name: round(len(values) / path_denominator, 6)
            for name, values in path_recovered.items()
        },
        "pathFusionExclusive": sorted(path_exclusive),
        "pathFusionExclusiveCount": len(path_exclusive),
        "compositionLift": sorted(path_exclusive),
        "compositionLiftCount": len(path_exclusive),
        "fusedUnsupportedCandidates": sorted(edge_evidence["fused"] - relevant_edges),
        "fusedUnsupportedPathCandidates": (
            sorted(path_evidence["fused"] - relevant_paths) if path_adjudicated else []),
    }


def admitted_path_overlap(left: Iterable[dict[str, Any]],
                          right: Iterable[dict[str, Any]]) -> float:
    """Backward-compatible constituent overlap across prompt paraphrases."""
    a = path_constituents(left)
    b = path_constituents(right)
    if not a and not b:
        return 1.0
    return round(len(a & b) / max(1, len(a | b)), 6)


def constituent_overlap(left: Iterable[dict[str, Any]],
                        right: Iterable[dict[str, Any]]) -> float:
    """Explicit name for the existing flattened-edge paraphrase metric."""
    return admitted_path_overlap(left, right)


def path_signature_overlap(left: Iterable[dict[str, Any]],
                           right: Iterable[dict[str, Any]], *,
                           mode: str = "identity") -> float:
    """Jaccard overlap of complete ordered compositions across paraphrases."""
    a = path_signatures(left, mode=mode)
    b = path_signatures(right, mode=mode)
    if not a and not b:
        return 1.0
    return round(len(a & b) / max(1, len(a | b)), 6)
