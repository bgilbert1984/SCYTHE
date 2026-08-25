"""Evidence-set-first evaluation primitives for GraphFusion relational lift.

This module is intentionally independent of runtime retrieval modes and model
answers.  Benchmark fixtures supply relationship IDs judged relevant in
advance; the evaluator compares recovered evidence constituents only.
"""

from __future__ import annotations

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


def evaluate_relational_lift(
    *,
    operator_evidence_ids: Iterable[str],
    semantic_evidence_ids: Iterable[str],
    graph_paths: Iterable[dict[str, Any]],
    fused_paths: Iterable[dict[str, Any]],
    relevant_fixture_ids: Iterable[str],
) -> dict[str, Any]:
    """Compare O, S, G and F against fixture relationship identities.

    `semantic_evidence_ids` are evaluator-resolved relationship identities, not
    semantic entity hits.  This makes the semantic-only arm comparable to graph
    paths without pretending that a seed itself establishes a relationship.
    """
    evidence = {
        "operator": {str(item) for item in operator_evidence_ids},
        "semantic": {str(item) for item in semantic_evidence_ids},
        "graph": path_constituents(graph_paths),
        "fused": path_constituents(fused_paths),
    }
    relevant = {str(item) for item in relevant_fixture_ids}
    recovered = {name: values & relevant for name, values in evidence.items()}
    independent = recovered["operator"] | recovered["semantic"] | recovered["graph"]
    exclusive = recovered["fused"] - independent
    denominator = max(1, len(relevant))
    return {
        "evidenceSets": {name: sorted(values) for name, values in evidence.items()},
        "relevantRecovered": {name: sorted(values) for name, values in recovered.items()},
        "relevantRecall": {
            name: round(len(values) / denominator, 6)
            for name, values in recovered.items()
        },
        "fusionExclusive": sorted(exclusive),
        "fusionExclusiveCount": len(exclusive),
        "fusedUnsupportedCandidates": sorted(evidence["fused"] - relevant),
    }


def admitted_path_overlap(left: Iterable[dict[str, Any]],
                          right: Iterable[dict[str, Any]]) -> float:
    """Jaccard overlap of relationship constituents across prompt paraphrases."""
    a = path_constituents(left)
    b = path_constituents(right)
    if not a and not b:
        return 1.0
    return round(len(a & b) / max(1, len(a | b)), 6)
