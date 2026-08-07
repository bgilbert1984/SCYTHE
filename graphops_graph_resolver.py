"""Bounded, revision-pinned graph selection support for Clarktech effects."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable


class GraphResolutionError(ValueError):
    pass


def _mapping(value: Any) -> Dict[str, Any]:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    return dict(value) if isinstance(value, dict) else {}


def _node_id(node: Dict[str, Any], fallback: str = "") -> str:
    return str(node.get("id") or node.get("node_id") or node.get("entity_id") or fallback)


def _position(node: Dict[str, Any]) -> list[float] | None:
    position = node.get("position")
    if isinstance(position, (list, tuple)) and len(position) >= 2:
        try:
            return [float(position[0]), float(position[1]), float(position[2] if len(position) > 2 else 0.0)]
        except (TypeError, ValueError):
            pass
    for source in (node, node.get("metadata") or {}, node.get("labels") or {}):
        if not isinstance(source, dict):
            continue
        lat = source.get("lat", source.get("latitude"))
        lon = source.get("lon", source.get("longitude"))
        try:
            if lat is not None and lon is not None:
                return [float(lat), float(lon), float(source.get("alt", 0.0) or 0.0)]
        except (TypeError, ValueError):
            continue
    return None


class GraphSelectionResolver:
    def __init__(self, engine: Any):
        self.engine = engine

    def _nodes(self) -> list[Dict[str, Any]]:
        nodes = getattr(self.engine, "nodes", {}) or {}
        if isinstance(nodes, dict):
            return [{**_mapping(value), "_fallback_id": str(key)} for key, value in nodes.items()]
        return [_mapping(value) for value in nodes]

    def _edges(self) -> list[Dict[str, Any]]:
        edges = getattr(self.engine, "edges", None)
        if edges is None:
            edges = getattr(self.engine, "hyperedges", {}) or {}
        if isinstance(edges, dict):
            return [{**_mapping(value), "_fallback_id": str(key)} for key, value in edges.items()]
        return [_mapping(value) for value in edges]

    def revision(self) -> str:
        nodes = sorted(({
            "id": _node_id(node, node.get("_fallback_id", "")),
            "kind": node.get("kind") or node.get("type"), "position": _position(node),
            "observedAt": node.get("observed_at") or node.get("timestamp") or node.get("created_at"),
        } for node in self._nodes()), key=lambda item: item["id"])
        edges = sorted(({
            "id": str(edge.get("id") or edge.get("edge_id") or edge.get("_fallback_id", "")),
            "kind": edge.get("kind") or edge.get("type"), "nodes": self._edge_nodes(edge),
            "timestamp": edge.get("timestamp"),
        } for edge in self._edges()), key=lambda item: item["id"])
        payload = json.dumps({"nodes": nodes, "edges": edges}, separators=(",", ":"),
                             sort_keys=True, default=str)
        return "graph-" + hashlib.blake2s(payload.encode(), digest_size=8).hexdigest()

    @staticmethod
    def _normalize_node(node: Dict[str, Any]) -> Dict[str, Any]:
        node_id = _node_id(node, node.get("_fallback_id", ""))
        metadata = dict(node.get("metadata") or {})
        declared = str(metadata.get("evidence_class") or "").upper()
        if metadata.get("source") == "test_generator" or metadata.get("generated") is True:
            evidence_class = "SYNTHETIC"
        elif declared in {"OBSERVED", "MEASURED", "SYNTHETIC", "INFERRED"}:
            evidence_class = declared
        else:
            evidence_class = "INFERRED"
        return {
            "id": node_id, "kind": str(node.get("kind") or node.get("type") or "entity"),
            "position": _position(node), "labels": dict(node.get("labels") or {}),
            "metadata": metadata, "evidenceClass": evidence_class,
            "observedAt": node.get("observed_at") or node.get("timestamp") or node.get("created_at"),
        }

    @staticmethod
    def _edge_nodes(edge: Dict[str, Any]) -> list[str]:
        nodes = edge.get("nodes")
        if isinstance(nodes, (list, tuple)):
            return [str(value) for value in nodes]
        source = edge.get("source", edge.get("src"))
        target = edge.get("target", edge.get("dst"))
        return [str(value) for value in (source, target) if value is not None]

    def snapshot(self, *, node_limit: int = 200, edge_limit: int = 300) -> Dict[str, Any]:
        node_limit = min(max(int(node_limit), 1), 500)
        edge_limit = min(max(int(edge_limit), 1), 1000)
        nodes = [self._normalize_node(node) for node in self._nodes()[:node_limit]]
        allowed = {node["id"] for node in nodes}
        edges = []
        for edge in self._edges():
            members = self._edge_nodes(edge)
            if members and all(member in allowed for member in members):
                edges.append({
                    "id": str(edge.get("id") or edge.get("edge_id") or edge.get("_fallback_id", "")),
                    "kind": str(edge.get("kind") or edge.get("type") or "edge"),
                    "nodes": members, "timestamp": edge.get("timestamp"),
                })
            if len(edges) >= edge_limit:
                break
        return {
            "status": "ok", "graphRevision": self.revision(), "nodes": nodes,
            "edges": edges, "bounded": True, "nodeLimit": node_limit,
            "edgeLimit": edge_limit, "nodeCount": len(nodes), "edgeCount": len(edges),
        }

    def resolve(self, selection: Dict[str, Any]) -> Dict[str, Any]:
        requested_revision = selection.get("graphRevision")
        revision = self.revision()
        if requested_revision and requested_revision != revision:
            raise GraphResolutionError("graph selection is stale; graph revision changed")
        entity_id = str(selection.get("entityId", ""))
        node = next((self._normalize_node(candidate) for candidate in self._nodes()
                     if _node_id(candidate, candidate.get("_fallback_id", "")) == entity_id), None)
        if node is None:
            raise GraphResolutionError("selected graph entity was not found")
        incident = []
        for edge in self._edges():
            if entity_id in self._edge_nodes(edge):
                incident.append({
                    "id": str(edge.get("id") or edge.get("edge_id") or edge.get("_fallback_id", "")),
                    "kind": str(edge.get("kind") or edge.get("type") or "edge"),
                    "nodes": self._edge_nodes(edge), "timestamp": edge.get("timestamp"),
                })
            if len(incident) >= 50:
                break
        return {"graphRevision": revision, "node": node, "incidentEdges": incident, "bounded": True}
