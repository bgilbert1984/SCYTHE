"""Deterministic, bounded pre-interpretation evidence traversal for GraphOps."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from copy import deepcopy
import json
import math
import re
from typing import Any, Iterable, Optional

from graphops_graph_resolver import PinnedGraphView


VERSION = "graphfusion.traversal.v2"


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False, default=str)


def _digest(prefix: str, value: Any) -> str:
    return prefix + sha256(_canonical(value).encode()).hexdigest()


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _bounded(value: Any, *, depth: int = 0) -> Any:
    if depth >= 4:
        return "[DEPTH BOUNDED]"
    if isinstance(value, dict):
        return {str(key)[:96]: _bounded(item, depth=depth + 1)
                for key, item in list(sorted(value.items()))[:32]}
    if isinstance(value, (list, tuple)):
        return [_bounded(item, depth=depth + 1) for item in list(value)[:24]]
    if isinstance(value, str):
        return value[:1024]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:1024]


@dataclass(frozen=True)
class SemanticSeed:
    entity_id: str
    similarity: float
    provider: str
    description: str | None = None
    protocol_anomaly_score: float | None = None
    protocol_violations: tuple[str, ...] = ()
    resolution: str = "UNRESOLVED"

    def to_dict(self) -> dict[str, Any]:
        result = {
            "entityId": self.entity_id,
            "similarity": round(self.similarity, 6),
            "provider": self.provider,
            "resolution": self.resolution,
        }
        if self.description:
            result["description"] = self.description
        if self.protocol_anomaly_score is not None:
            result["protocolAnomalyScore"] = self.protocol_anomaly_score
        if self.protocol_violations:
            result["protocolViolations"] = list(self.protocol_violations)
        return result


@dataclass(frozen=True)
class SemanticSearchResult:
    seeds: tuple[SemanticSeed, ...]
    state: dict[str, Any]


class SemanticSeedProvider:
    """Expose existing TurboQuant/FAISS retrieval as structured search leads."""

    def __init__(self, *, executor: Any, embedding_engine: Any = None):
        self.executor = executor
        self.embedding_engine = embedding_engine

    def search(self, question: str, *, limit: int = 8,
               projection_ids: Optional[set[str]] = None) -> list[SemanticSeed]:
        return list(self.search_with_receipt(
            question, limit=limit, projection_ids=projection_ids).seeds)

    def search_with_receipt(self, question: str, *, limit: int = 8,
                            projection_ids: Optional[set[str]] = None
                            ) -> SemanticSearchResult:
        limit = min(max(int(limit), 1), 16)
        projection_ids = projection_ids or set()
        embedding_model = str(
            getattr(self.embedding_engine, "_model", None) or
            getattr(self.executor, "_embedding_model", None) or
            "UNAVAILABLE"
        )

        def _result(seeds: list[SemanticSeed], state: dict[str, Any]
                    ) -> SemanticSearchResult:
            records = [item.to_dict() for item in seeds]
            state = {**state, "seedSetHash": _digest("seed-", records)}
            return SemanticSearchResult(tuple(seeds), state)

        vector = self.executor._embed_intent(question)
        if vector is not None:
            try:
                from turbo_quant_store import embedding_store
                store = embedding_store()
                if len(store) > 0:
                    seeds = []
                    matches, state = store.search_with_receipt(
                        vector, k=limit, embedding_model=embedding_model)
                    for entity_id, similarity in matches:
                        entity_id = str(entity_id)
                        node = self.executor._get_node(entity_id) or {}
                        labels = node.get("labels") or {}
                        violations = labels.get("protocol_violations") or ()
                        if isinstance(violations, str):
                            violations = (violations,)
                        seeds.append(SemanticSeed(
                            entity_id=entity_id, similarity=float(similarity),
                            provider="turboquant",
                            protocol_anomaly_score=_finite(labels.get("protocol_anomaly_score")),
                            protocol_violations=tuple(str(item) for item in
                                                      list(violations)[:16]),
                            resolution=("RESOLVED_IN_PROJECTION" if entity_id in projection_ids
                                        else "OUTSIDE_RETAINED_PROJECTION"),
                        ))
                    if seeds:
                        return _result(seeds, state)
            except Exception:
                pass
        if self.embedding_engine is None:
            return _result([], {
                "provider": "none", "providerRevision": "none",
                "embeddingModel": embedding_model, "indexCount": 0,
            })
        try:
            results = self.embedding_engine.search_similar(question, k=limit) or []
        except Exception:
            return _result([], {
                "provider": "faiss", "providerRevision": "unavailable",
                "embeddingModel": embedding_model, "indexCount": 0,
            })
        seeds = []
        for result in results[:limit]:
            entity_id = str(result.get("entity_id") or "")
            if not entity_id:
                continue
            seeds.append(SemanticSeed(
                entity_id=entity_id,
                similarity=float(result.get("similarity") or 0.0),
                provider="faiss",
                description=str(result.get("description") or "")[:200] or None,
                resolution=("RESOLVED_IN_PROJECTION" if entity_id in projection_ids
                            else "OUTSIDE_RETAINED_PROJECTION"),
            ))
        try:
            stats = self.embedding_engine.stats() or {}
        except Exception:
            stats = {}
        metadata_identity = []
        for index, item in sorted(
                getattr(self.embedding_engine, "_meta", {}).items(),
                key=lambda pair: str(pair[0])):
            metadata_identity.append({
                "index": index,
                "entityId": item.get("entity_id"),
                "description": item.get("description"),
                "model": item.get("model"),
                "createdAt": item.get("created_at"),
            })
        stable_state = {
            "embeddingModel": stats.get("model") or embedding_model,
            "dimension": stats.get("dim"),
            "indexCount": stats.get("total_vectors", len(results)),
            "metadataHash": _digest("meta-", metadata_identity),
        }
        return _result(seeds, {
            "provider": "faiss",
            "providerRevision": _digest("faiss-", stable_state),
            **stable_state,
        })

    @staticmethod
    def render_legacy(seeds: Iterable[SemanticSeed]) -> str:
        seeds = list(seeds)
        if not seeds:
            return ""
        lines = ["[Semantic Memory — similar historical entities:]"]
        for seed in seeds[:5]:
            details = []
            if seed.protocol_anomaly_score is not None:
                details.append(f"proto_anomaly={seed.protocol_anomaly_score:.2f}")
            if seed.protocol_violations:
                details.append(f"violations={list(seed.protocol_violations)}")
            if seed.description:
                details.append(seed.description)
            lines.append(f"  [{seed.similarity:.2f}] {seed.entity_id}" +
                         (": " + " ".join(details) if details else ""))
        return "\n".join(lines)


@dataclass(frozen=True)
class RetrievalPolicy:
    max_hops: int = 2
    semantic_seed_limit: int = 6
    candidate_path_limit: int = 48
    admitted_path_limit: int = 8
    node_budget: int = 96
    edge_budget: int = 160
    operator_candidate_floor: int = 16
    synthetic_allowed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "maxHops": self.max_hops,
            "semanticSeeds": self.semantic_seed_limit,
            "candidateLimit": self.candidate_path_limit,
            "pathLimit": self.admitted_path_limit,
            "nodeBudget": self.node_budget,
            "edgeBudget": self.edge_budget,
            "operatorCandidateFloor": self.operator_candidate_floor,
            "syntheticAllowed": self.synthetic_allowed,
        }


class GraphFusionEvidenceFabric:
    """Build deterministic, hyperedge-preserving evidence paths over one view."""

    _AUTHORITY = {"MEASURED": 1.0, "OBSERVED": .95, "CONTRADICTED": .72,
                  "INFERRED": .55, "SYNTHETIC": .10}

    def __init__(self, policy: RetrievalPolicy | None = None):
        self.policy = policy or RetrievalPolicy()

    @staticmethod
    def _blocked(entity: dict[str, Any], *, synthetic_allowed: bool) -> bool:
        evidence = str(entity.get("evidenceClass") or "INFERRED").upper()
        metadata = entity.get("metadata") or {}
        return ((evidence == "SYNTHETIC" and not synthetic_allowed) or
                evidence == "DISPLAY_ONLY" or metadata.get("display_only") is True)

    @staticmethod
    def _contradiction(edge: dict[str, Any]) -> bool:
        metadata = edge.get("metadata") or {}
        return (str(edge.get("evidenceClass") or "").upper() == "CONTRADICTED" or
                bool(edge.get("contradictions")) or bool(metadata.get("contradictions")) or
                "contradict" in str(edge.get("kind") or "").lower())

    @staticmethod
    def _temporal(edge: dict[str, Any]) -> bool:
        text = " ".join((str(edge.get("kind") or ""),
                         str((edge.get("labels") or {}).get("event_type") or ""),
                         str((edge.get("metadata") or {}).get("status") or ""))).lower()
        return any(token in text for token in ("delta", "change", "supersed", "transition"))

    @staticmethod
    def _question_score(question: str, entities: list[dict[str, Any]]) -> float:
        query = {token for token in re.findall(r"[a-z0-9_:.-]+", question.lower()) if len(token) > 2}
        if not query:
            return 0.0
        body = " ".join(_canonical(item).lower() for item in entities)
        matched = sum(token in body for token in query)
        return min(1.0, matched / max(1, min(len(query), 6)))

    def _score(self, question: str, seed_score: float,
               entities: list[dict[str, Any]], hops: int) -> tuple[float, str, list[str]]:
        edges = [item for item in entities if item.get("_stepType") == "edge"]
        contradiction = any(self._contradiction(item) for item in edges)
        temporal = any(self._temporal(item) for item in edges)
        role = ("EXPLICIT_CONTRADICTION" if contradiction else
                "TEMPORAL_CONTEXT" if temporal else
                "RELATIONAL_SUPPORT")
        authority = (sum(self._AUTHORITY.get(str(item.get("evidenceClass") or "INFERRED").upper(), .4)
                         for item in edges) / len(edges)) if edges else .4
        relational = 1.0 / max(1, hops)
        relevance = self._question_score(question, entities)
        tension = 1.0 if contradiction else 0.0
        score = (.30 * relevance + .23 * relational + .22 * authority +
                 .15 * max(0.0, min(seed_score, 1.0)) + .10 * tension)
        reasons = [f"{hops} bounded graph hop{'s' if hops != 1 else ''}",
                   f"authority prior {authority:.2f}"]
        if contradiction:
            reasons.append("explicit contradiction signal")
        if temporal:
            reasons.append("explicit temporal-change relation")
        return round(score, 6), role, reasons

    @staticmethod
    def _path_overlap(left: dict[str, Any], right: dict[str, Any]) -> float:
        a = set(left["edgeIds"]); b = set(right["edgeIds"])
        return len(a & b) / len(a | b) if a and b else 0.0

    def build(self, *, question: str, view: PinnedGraphView, mode: str,
              semantic_seeds: Iterable[SemanticSeed] = (),
              semantic_state: Optional[dict[str, Any]] = None,
              auxiliary_state: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        nodes = {item["id"]: item for item in view.nodes}
        edges = {item["id"]: item for item in view.edges}
        adjacency: dict[str, list[str]] = {node_id: [] for node_id in nodes}
        for edge_id, edge in edges.items():
            if self._blocked(edge, synthetic_allowed=self.policy.synthetic_allowed):
                continue
            for node_id in edge.get("nodes") or []:
                if node_id in adjacency:
                    adjacency[node_id].append(edge_id)
        for values in adjacency.values():
            values.sort()

        seeds = []
        if view.selection_kind == "graph-edge":
            selected_edge = edges.get(view.selection_entity_id) or {}
            explicit_ids = [item for item in selected_edge.get("nodes", []) if item in nodes]
        else:
            explicit_ids = [view.selection_entity_id] if view.selection_entity_id in nodes else []
        for entity_id in explicit_ids:
            seeds.append((entity_id, "OPERATOR_SELECTION", 1.0))
        semantic_seeds = list(semantic_seeds)
        for seed in semantic_seeds:
            if seed.resolution == "RESOLVED_IN_PROJECTION" and seed.entity_id in nodes:
                seeds.append((seed.entity_id, "SEMANTIC_RETRIEVAL", seed.similarity))
        deduplicated = {}
        for entity_id, origin, score in seeds:
            current = deduplicated.get(entity_id)
            if (current is None or origin == "OPERATOR_SELECTION" or
                    (current[1] != "OPERATOR_SELECTION" and score > current[2])):
                deduplicated[entity_id] = (entity_id, origin, score)
        operator_seeds = sorted(
            (item for item in deduplicated.values()
             if item[1] == "OPERATOR_SELECTION"), key=lambda item: item[0])
        semantic_roots = sorted(
            (item for item in deduplicated.values()
             if item[1] == "SEMANTIC_RETRIEVAL"),
            key=lambda item: (-item[2], item[0]))
        seeds = [*operator_seeds, *semantic_roots]

        candidates = []
        nodes_visited: set[str] = set()
        edges_inspected: set[str] = set()

        def _traverse(seed_pool, pool_limit):
            if pool_limit <= 0:
                return
            pool_start = len(candidates)
            for seed_id, origin, seed_score in seed_pool:
                frontier = [(seed_id, [{"type": "node", "id": seed_id}], {seed_id}, [])]
                nodes_visited.add(seed_id)
                for _depth in range(self.policy.max_hops):
                    next_frontier = []
                    for tail, steps, visited, path_edges in frontier:
                        for edge_id in adjacency.get(tail, []):
                            if len(edges_inspected) >= self.policy.edge_budget:
                                break
                            edges_inspected.add(edge_id)
                            edge = edges[edge_id]
                            for member in sorted(edge.get("nodes") or []):
                                if member == tail or member in visited or member not in nodes:
                                    continue
                                if self._blocked(nodes[member], synthetic_allowed=self.policy.synthetic_allowed):
                                    continue
                                if len(nodes_visited) >= self.policy.node_budget and member not in nodes_visited:
                                    continue
                                nodes_visited.add(member)
                                candidate_steps = [*steps, {"type": "edge", "id": edge_id},
                                                   {"type": "node", "id": member}]
                                edge_ids = [*path_edges, edge_id]
                                evidence = []
                                for step in candidate_steps:
                                    item = deepcopy((nodes if step["type"] == "node" else edges)[step["id"]])
                                    item["_stepType"] = step["type"]
                                    evidence.append(item)
                                score, role, reasons = self._score(
                                    question, seed_score, evidence, len(edge_ids))
                                identity = {"projectionHash": view.projection_hash,
                                            "seed": seed_id, "steps": candidate_steps}
                                candidates.append({
                                    "pathId": _digest("path-", identity), "seedId": seed_id,
                                    "seedOrigin": origin, "score": score, "role": role,
                                    "steps": candidate_steps, "edgeIds": edge_ids,
                                    "admissionReasons": reasons,
                                    "authorityCeiling": min(
                                        (str(item.get("evidenceClass") or "INFERRED").upper()
                                         for item in evidence if item["_stepType"] == "edge"),
                                        key=lambda value: self._AUTHORITY.get(value, .4), default="NONE"),
                                    "evidence": [{"type": item.pop("_stepType"), **_bounded(item)}
                                                 for item in evidence],
                                })
                                next_frontier.append((member, candidate_steps,
                                                      visited | {member}, edge_ids))
                                if (len(candidates) >= self.policy.candidate_path_limit or
                                        len(candidates) - pool_start >= pool_limit):
                                    break
                            if (len(candidates) >= self.policy.candidate_path_limit or
                                    len(candidates) - pool_start >= pool_limit):
                                break
                        if (len(candidates) >= self.policy.candidate_path_limit or
                                len(candidates) - pool_start >= pool_limit):
                            break
                    frontier = next_frontier
                    if (not frontier or len(candidates) >= self.policy.candidate_path_limit or
                            len(candidates) - pool_start >= pool_limit):
                        break
                if (len(candidates) >= self.policy.candidate_path_limit or
                        len(candidates) - pool_start >= pool_limit):
                    break

        operator_limit = (self.policy.candidate_path_limit if not semantic_roots else
                          min(max(1, self.policy.operator_candidate_floor),
                              self.policy.candidate_path_limit))
        _traverse(operator_seeds, operator_limit)
        _traverse(semantic_roots, self.policy.candidate_path_limit - len(candidates))

        ranked = sorted(candidates, key=lambda item: (-item["score"], item["pathId"]))
        admitted = []
        operator_candidates = [item for item in ranked
                               if item["seedOrigin"] == "OPERATOR_SELECTION"]
        if operator_candidates and self.policy.admitted_path_limit > 0:
            admitted.append(operator_candidates[0])
        contradictions = [item for item in ranked if item["role"] == "EXPLICIT_CONTRADICTION"]
        if (contradictions and contradictions[0] not in admitted and
                len(admitted) < self.policy.admitted_path_limit):
            admitted.append(contradictions[0])
        for candidate in ranked:
            if candidate in admitted:
                continue
            if admitted and max(self._path_overlap(candidate, item) for item in admitted) > .75:
                continue
            if candidate["seedOrigin"] == "SEMANTIC_RETRIEVAL" and candidate["role"] == "RELATIONAL_SUPPORT":
                candidate = {**candidate, "role": "DIVERGENT_BRANCH"}
            admitted.append(candidate)
            if len(admitted) >= self.policy.admitted_path_limit:
                break

        semantic_state = deepcopy(semantic_state or {
            "provider": "none", "providerRevision": "none", "indexCount": 0,
            "seedSetHash": _digest("seed-", [item.to_dict() for item in semantic_seeds]),
        })
        auxiliary_state = deepcopy(auxiliary_state or {
            "mode": "CONTAINED", "replayable": True,
            "liveProvidersUsed": [],
        })
        traversal_identity = {
            "version": VERSION, "graphRevision": view.graph_revision,
            "projectionHash": view.projection_hash,
            "questionDigest": sha256(question.encode()).hexdigest(), "mode": mode,
            "policy": self.policy.to_dict(),
            "semanticSeeds": [item.to_dict() for item in semantic_seeds],
            "semanticState": semantic_state,
            "auxiliaryEvidence": auxiliary_state,
            "admittedPaths": [{key: path[key] for key in
                               ("pathId", "role", "steps", "admissionReasons")}
                              for path in admitted],
        }
        traversal_hash = _digest("trav-", traversal_identity)
        return {
            "mode": mode, "version": VERSION,
            "graph": {"revision": view.graph_revision,
                      "detectedNodes": view.detected_node_count,
                      "detectedEdges": view.detected_edge_count},
            "projection": view.to_receipt(),
            "semanticSeeds": [item.to_dict() for item in semantic_seeds],
            "semanticState": semantic_state,
            "auxiliaryEvidence": auxiliary_state,
            "traversal": {"hash": traversal_hash, "maxHops": self.policy.max_hops,
                          "seeds": len(seeds), "nodesVisited": len(nodes_visited),
                          "edgesInspected": len(edges_inspected),
                          "candidatePaths": len(candidates), "admittedPaths": len(admitted)},
            "paths": admitted,
            "boundary": ("RELATIONAL PATHS ARE BOUNDED EVIDENCE CHAINS, NOT CAUSAL PROOF; "
                         "SEMANTIC SEEDS ARE SEARCH LEADS, NOT GRAPH RELATIONSHIPS"),
        }

    @staticmethod
    def render_context(retrieval: dict[str, Any]) -> str:
        disclosed = {
            "version": retrieval.get("version"), "mode": retrieval.get("mode"),
            "graph": retrieval.get("graph"), "projection": retrieval.get("projection"),
            "traversal": retrieval.get("traversal"),
            "paths": retrieval.get("paths"), "boundary": retrieval.get("boundary"),
        }
        return "GRAPHFUSION PINNED EVIDENCE PATHS (JSON):\n" + json.dumps(
            disclosed, sort_keys=True, default=str)
