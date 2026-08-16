"""Bounded, revision-pinned graph selection support for Clarktech effects."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import time
from collections import Counter, defaultdict, OrderedDict
from copy import deepcopy
from threading import RLock
from typing import Any, Dict, Iterable


class GraphResolutionError(ValueError):
    pass


_SNAPSHOT_CACHE: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
_SNAPSHOT_CACHE_LOCK = RLock()
_SNAPSHOT_CACHE_LIMIT = 32

_ADAPTIVE_PURPOSES = (
    "SELECTED_CONTEXT", "MOST_ACTIVE", "EXPLICIT_SIGNAL",
    "NEW_ARRIVAL", "NETWORK_DIVERSITY", "STABLE_CONTEXT",
)
_PURPOSE_SCHEDULE = (
    "MOST_ACTIVE", "MOST_ACTIVE", "NETWORK_DIVERSITY", "NEW_ARRIVAL",
    "MOST_ACTIVE", "STABLE_CONTEXT", "EXPLICIT_SIGNAL", "MOST_ACTIVE",
    "NETWORK_DIVERSITY", "NEW_ARRIVAL", "MOST_ACTIVE", "EXPLICIT_SIGNAL",
    "MOST_ACTIVE", "NETWORK_DIVERSITY", "NEW_ARRIVAL", "MOST_ACTIVE",
    "STABLE_CONTEXT", "EXPLICIT_SIGNAL",
)


def _display_signal(node: Dict[str, Any]) -> float:
    metadata = node.get("metadata") or {}
    score = 1.0 if node.get("evidenceClass") == "CONTRADICTED" else 0.0
    for key in ("anomaly_score", "threat_score", "risk_score", "severity"):
        try:
            score = max(score, float(metadata.get(key) or 0.0))
        except (TypeError, ValueError):
            continue
    return score


def _network_bucket(node: Dict[str, Any]) -> str:
    labels = node.get("labels") or {}
    value = str(labels.get("ip") or "").strip()
    try:
        address = ipaddress.ip_address(value)
        prefix = 24 if address.version == 4 else 48
        return str(ipaddress.ip_network(f"{address}/{prefix}", strict=False))
    except ValueError:
        return f"kind:{node.get('kind', 'entity')}"


def _adaptive_rank_nodes(nodes: list[Dict[str, Any]], edges: list[Dict[str, Any]],
                         limit: int, focus_id: str = "") -> tuple[list[Dict[str, Any]], Dict[str, int]]:
    """Weighted-fair display ranking; presentation metadata never changes evidence authority."""
    by_id = {node["id"]: node for node in nodes}
    peers = {node_id: set() for node_id in by_id}
    protocols = {node_id: set() for node_id in by_id}
    activity = {node_id: 0.0 for node_id in by_id}
    newest = max((_timestamp(edge.get("observedAt")) or -1 for edge in edges), default=-1)
    for edge in edges:
        members = [member for member in edge.get("nodes", []) if member in by_id]
        observed = _timestamp(edge.get("observedAt"))
        weight = math.exp(-max(0.0, newest - observed) / 120.0) if observed is not None and newest >= 0 else 0.2
        protocol = str((edge.get("labels") or {}).get("proto") or "unknown").lower()
        for member in members:
            activity[member] += weight
            protocols[member].add(protocol)
            peers[member].update(other for other in members if other != member)
    activity_score = {node_id: math.log1p(activity[node_id]) +
                      0.35 * math.log1p(len(peers[node_id])) +
                      0.2 * math.log1p(len(protocols[node_id])) for node_id in by_id}
    stable_key = lambda node: (hashlib.blake2s(node["id"].encode(), digest_size=8).hexdigest(), node["id"])
    candidates: Dict[str, list[Dict[str, Any]]] = {
        "MOST_ACTIVE": sorted(nodes, key=lambda node: (-activity_score[node["id"]], node["id"])),
        "EXPLICIT_SIGNAL": sorted((node for node in nodes if _display_signal(node) > 0),
                                  key=lambda node: (-_display_signal(node), node["id"])),
        "NEW_ARRIVAL": sorted((node for node in nodes if newest >= 0 and
                               (_timestamp(node.get("observedAt")) or -1) >= newest - 60),
                              key=lambda node: (-(_timestamp(node.get("observedAt")) or -1), node["id"])),
        "STABLE_CONTEXT": sorted(nodes, key=stable_key),
    }
    buckets: Dict[str, list[Dict[str, Any]]] = defaultdict(list)
    for node in nodes:
        buckets[_network_bucket(node)].append(node)
    for values in buckets.values():
        values.sort(key=lambda node: (-activity_score[node["id"]], node["id"]))
    diverse = []
    while any(buckets.values()):
        for bucket in sorted(buckets):
            if buckets[bucket]:
                diverse.append(buckets[bucket].pop(0))
    candidates["NETWORK_DIVERSITY"] = diverse
    focus_nodes = set()
    if focus_id in by_id:
        focus_nodes.add(focus_id)
    else:
        focus_edge = next((edge for edge in edges if edge.get("id") == focus_id), None)
        focus_nodes.update(member for member in (focus_edge or {}).get("nodes", []) if member in by_id)
    selected: list[Dict[str, Any]] = []
    selected_ids = set()
    purpose_counts = Counter()

    def take(node: Dict[str, Any], purpose: str) -> None:
        if node["id"] in selected_ids or len(selected) >= limit:
            return
        ranked = deepcopy(node)
        ranked["display"] = {"selectionPurpose": purpose,
                             "activityScore": round(activity_score[node["id"]], 4),
                             "adaptiveRank": len(selected) + 1}
        selected.append(ranked); selected_ids.add(node["id"]); purpose_counts[purpose] += 1

    for node_id in sorted(focus_nodes):
        take(by_id[node_id], "SELECTED_CONTEXT")
    cursors = Counter()
    while len(selected) < min(limit, len(nodes)):
        progressed = False
        for purpose in _PURPOSE_SCHEDULE:
            pool = candidates[purpose]
            while cursors[purpose] < len(pool) and pool[cursors[purpose]]["id"] in selected_ids:
                cursors[purpose] += 1
            if cursors[purpose] < len(pool):
                take(pool[cursors[purpose]], purpose); cursors[purpose] += 1; progressed = True
            if len(selected) >= min(limit, len(nodes)):
                break
        if not progressed:
            break
    for node in candidates["MOST_ACTIVE"]:
        take(node, "MOST_ACTIVE")
    return selected, {purpose: purpose_counts.get(purpose, 0) for purpose in _ADAPTIVE_PURPOSES}


def _adaptive_rank_edges(edges: list[Dict[str, Any]], allowed: set[str], limit: int,
                         focus_id: str = "") -> list[Dict[str, Any]]:
    candidates = [edge for edge in edges if edge.get("nodes") and
                  all(member in allowed for member in edge["nodes"])]
    def score(edge: Dict[str, Any]) -> tuple:
        metadata = edge.get("metadata") or {}; labels = edge.get("labels") or {}
        signal = 1 if edge.get("evidenceClass") == "CONTRADICTED" or edge.get("contradictions") else 0
        try: reinforcement = float(metadata.get("reinforcement_count") or 1)
        except (TypeError, ValueError): reinforcement = 1
        try: volume = float(labels.get("bytes") or 0) + float(labels.get("packets") or 0)
        except (TypeError, ValueError): volume = 0
        return (-int(edge.get("id") == focus_id), -signal,
                -(_timestamp(edge.get("observedAt")) or -1), -reinforcement, -volume, edge["id"])
    candidates.sort(key=score)
    selected = []; selected_ids = set(); pair_counts = Counter(); hub_counts = Counter()
    def take(edge: Dict[str, Any], purpose: str) -> None:
        if edge["id"] in selected_ids or len(selected) >= limit:
            return
        ranked = deepcopy(edge); ranked["display"] = {"selectionPurpose": purpose,
                                                       "adaptiveRank": len(selected) + 1}
        selected.append(ranked); selected_ids.add(edge["id"])
        pair_counts[tuple(sorted(edge["nodes"]))] += 1
        hub_counts.update(edge["nodes"])
    for edge in candidates:
        members = edge["nodes"]; pair = tuple(sorted(members))
        forced = edge["id"] == focus_id or focus_id in members or edge.get("evidenceClass") == "CONTRADICTED"
        if forced or (pair_counts[pair] < 4 and all(hub_counts[node] < 30 for node in members)):
            take(edge, "SELECTED_CONTEXT" if forced else "RECENT_DIVERSE_FLOW")
    for edge in candidates:
        if all(hub_counts[node] < 100 for node in edge["nodes"]):
            take(edge, "RECENT_FLOW")
    for edge in candidates:
        take(edge, "CAPACITY_FILL")
    return selected


def _remember_snapshot(snapshot: Dict[str, Any]) -> None:
    revision = snapshot["graphRevision"]
    with _SNAPSHOT_CACHE_LOCK:
        # A revision is content-addressed. Preserve the first capture time so a
        # later poll of identical state cannot rewrite temporal history.
        if revision not in _SNAPSHOT_CACHE:
            _SNAPSHOT_CACHE[revision] = deepcopy(snapshot)
        _SNAPSHOT_CACHE.move_to_end(revision)
        while len(_SNAPSHOT_CACHE) > _SNAPSHOT_CACHE_LIMIT:
            _SNAPSHOT_CACHE.popitem(last=False)


def _cached_snapshot(revision: str) -> Dict[str, Any] | None:
    with _SNAPSHOT_CACHE_LOCK:
        snapshot = _SNAPSHOT_CACHE.get(revision)
        if snapshot is not None:
            _SNAPSHOT_CACHE.move_to_end(revision)
            return deepcopy(snapshot)
    return None


def _retained_snapshots() -> list[Dict[str, Any]]:
    with _SNAPSHOT_CACHE_LOCK:
        return sorted((deepcopy(item) for item in _SNAPSHOT_CACHE.values()),
                      key=lambda item: float(item.get("capturedAt", 0.0)))


def _mapping(value: Any) -> Dict[str, Any]:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    return dict(value) if isinstance(value, dict) else {}


def _node_id(node: Dict[str, Any], fallback: str = "") -> str:
    return str(node.get("id") or node.get("node_id") or node.get("entity_id") or fallback)


def _edge_id(edge: Dict[str, Any]) -> str:
    explicit = edge.get("id") or edge.get("edge_id") or edge.get("_fallback_id")
    if explicit:
        return str(explicit)
    nodes = GraphSelectionResolver._edge_nodes(edge)
    seed = json.dumps({"kind": edge.get("kind") or edge.get("type") or "edge",
                       "nodes": nodes, "timestamp": edge.get("timestamp") or edge.get("observed_at")},
                      sort_keys=True, separators=(",", ":"), default=str)
    return "edge-" + hashlib.blake2s(seed.encode(), digest_size=8).hexdigest()


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


def _timestamp(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _evidence_class(value: Dict[str, Any]) -> str:
    metadata = dict(value.get("metadata") or {})
    declared = str(value.get("evidenceClass") or metadata.get("evidence_class") or
                   metadata.get("obs_class") or "").upper()
    if metadata.get("source") == "test_generator" or metadata.get("generated") is True:
        return "SYNTHETIC"
    if declared in {"OBSERVED", "MEASURED", "SYNTHETIC", "INFERRED", "CONTRADICTED"}:
        return declared
    return "INFERRED"


class GraphSelectionResolver:
    def __init__(self, engine: Any):
        self.engine = engine
        self._pinned_nodes: list[Dict[str, Any]] | None = None
        self._pinned_edges: list[Dict[str, Any]] | None = None
        self._pinned_revision: str | None = None

    def _nodes(self) -> list[Dict[str, Any]]:
        if self._pinned_nodes is not None:
            return deepcopy(self._pinned_nodes)
        nodes = getattr(self.engine, "nodes", {}) or {}
        if isinstance(nodes, dict):
            result = [{**_mapping(value), "_fallback_id": str(key)} for key, value in nodes.items()]
        else:
            result = [_mapping(value) for value in nodes]
        attached = getattr(self.engine, "hypergraph_engine", None)
        attached_nodes = getattr(attached, "nodes", {}) if attached is not None else {}
        for key, value in (attached_nodes.items() if isinstance(attached_nodes, dict) else []):
            item = {**_mapping(value), "_fallback_id": str(key)}
            metadata = item.get("metadata") or {}
            if metadata.get("source") == "eve-streamer":
                result.append(item)
        deduplicated = {}
        for item in result:
            deduplicated[_node_id(item, item.get("_fallback_id", ""))] = item
        return list(deduplicated.values())

    def _edges(self) -> list[Dict[str, Any]]:
        if self._pinned_edges is not None:
            return deepcopy(self._pinned_edges)
        edges = getattr(self.engine, "edges", None)
        if edges is None:
            edges = getattr(self.engine, "hyperedges", {}) or {}
        if isinstance(edges, dict):
            result = [{**_mapping(value), "_fallback_id": str(key)} for key, value in edges.items()]
        else:
            result = [_mapping(value) for value in edges]
        attached = getattr(self.engine, "hypergraph_engine", None)
        attached_edges = getattr(attached, "edges", {}) if attached is not None else {}
        for key, value in (attached_edges.items() if isinstance(attached_edges, dict) else []):
            item = {**_mapping(value), "_fallback_id": str(key)}
            metadata = item.get("metadata") or {}
            if metadata.get("source") == "eve-streamer":
                result.append(item)
        deduplicated = {}
        for item in result:
            deduplicated[_edge_id(item)] = item
        return list(deduplicated.values())

    def revision(self) -> str:
        if self._pinned_revision is not None:
            return self._pinned_revision
        # Revisions cover the complete normalized selection state, including
        # evidence and provenance metadata. A metadata/evidence transition must
        # never reuse a revision whose retained snapshot says something else.
        nodes = sorted((self._normalize_node(node) for node in self._nodes()),
                       key=lambda item: item["id"])
        edges = sorted((self._normalize_edge(edge) for edge in self._edges()),
                       key=lambda item: item["id"])
        return self._revision_from_normalized(nodes, edges)

    @staticmethod
    def _revision_from_normalized(nodes: list[Dict[str, Any]],
                                  edges: list[Dict[str, Any]]) -> str:
        """Hash one captured graph state without fetching it a second time."""
        payload = json.dumps({"nodes": nodes, "edges": edges}, separators=(",", ":"),
                             sort_keys=True, default=str)
        return "graph-" + hashlib.blake2s(payload.encode(), digest_size=8).hexdigest()

    @staticmethod
    def _normalize_node(node: Dict[str, Any]) -> Dict[str, Any]:
        node_id = _node_id(node, node.get("_fallback_id", ""))
        metadata = dict(node.get("metadata") or {})
        return {
            "id": node_id, "kind": str(node.get("kind") or node.get("type") or "entity"),
            "position": _position(node), "labels": dict(node.get("labels") or {}),
            "metadata": metadata, "evidenceClass": _evidence_class(node),
            "observedAt": _timestamp(node.get("observed_at") or metadata.get("observed_at") or
                                     node.get("timestamp") or node.get("updated_at") or node.get("created_at")),
        }

    @staticmethod
    def _edge_nodes(edge: Dict[str, Any]) -> list[str]:
        nodes = edge.get("nodes")
        if isinstance(nodes, (list, tuple)):
            return [str(value) for value in nodes]
        source = edge.get("source", edge.get("src"))
        target = edge.get("target", edge.get("dst"))
        return [str(value) for value in (source, target) if value is not None]

    @classmethod
    def _normalize_edge(cls, edge: Dict[str, Any]) -> Dict[str, Any]:
        metadata = dict(edge.get("metadata") or {})
        contradictions = edge.get("contradictions", edge.get("contradicts", metadata.get("contradictions", [])))
        if isinstance(contradictions, str):
            contradictions = [contradictions]
        if not isinstance(contradictions, (list, tuple)):
            contradictions = []
        return {
            "id": _edge_id(edge),
            "kind": str(edge.get("kind") or edge.get("type") or "edge"),
            "nodes": cls._edge_nodes(edge),
            "observedAt": _timestamp(edge.get("observed_at") or edge.get("timestamp") or edge.get("created_at")),
            "timestamp": _timestamp(edge.get("timestamp") or edge.get("observed_at") or edge.get("created_at")),
            "labels": dict(edge.get("labels") or {}),
            "metadata": metadata,
            "evidenceClass": _evidence_class(edge),
            "contradictions": [str(item) for item in contradictions[:20]],
        }

    def snapshot(self, *, node_limit: int = 200, edge_limit: int = 300,
                 focus_id: str = "") -> Dict[str, Any]:
        node_limit = min(max(int(node_limit), 1), 500)
        edge_limit = min(max(int(edge_limit), 1), 1000)
        # Detection counts describe the complete deduplicated engine surface;
        # display counts below describe only the bounded payload returned to a client.
        all_nodes = self._nodes()
        all_edges = self._edges()
        detected_node_count = len(all_nodes)
        detected_edge_count = len(all_edges)
        normalized_nodes = [self._normalize_node(node) for node in all_nodes]
        normalized_edges = [self._normalize_edge(edge) for edge in all_edges]
        ranked_nodes, _ = _adaptive_rank_nodes(normalized_nodes, normalized_edges, 500)
        canonical_nodes = [{key: value for key, value in node.items() if key != "display"}
                           for node in ranked_nodes]
        canonical_allowed = {node["id"] for node in canonical_nodes}
        canonical_edges = [{key: value for key, value in edge.items() if key != "display"}
                           for edge in _adaptive_rank_edges(
                               normalized_edges, canonical_allowed, 1000)]
        graph_revision = (self._pinned_revision or self._revision_from_normalized(
            sorted(normalized_nodes, key=lambda item: item["id"]),
            sorted(normalized_edges, key=lambda item: item["id"])))
        canonical = {
            "status": "ok", "graphRevision": graph_revision, "nodes": canonical_nodes,
            "edges": canonical_edges, "bounded": True, "nodeLimit": 500,
            "edgeLimit": 1000, "nodeCount": len(canonical_nodes), "edgeCount": len(canonical_edges),
            "detectedNodeCount": detected_node_count, "detectedEdgeCount": detected_edge_count,
            "displayedNodeCount": len(canonical_nodes), "displayedEdgeCount": len(canonical_edges),
            "capturedAt": time.time(), "snapshotAuthority": "RETAINED_IMMUTABLE_GRAPH_STATE",
        }
        _remember_snapshot(canonical)
        retained = _cached_snapshot(canonical["graphRevision"]) or canonical
        # Enrichment is a read-time display sidecar. It is deliberately absent
        # from the content-addressed revision and retained evidence snapshot.
        from ip_enrichment import enrich_graph_node
        ranked_nodes, purpose_counts = _adaptive_rank_nodes(
            retained["nodes"], retained["edges"], node_limit, str(focus_id or ""))
        nodes = [enrich_graph_node(node) for node in ranked_nodes]
        allowed = {node["id"] for node in nodes}
        edges = _adaptive_rank_edges(retained["edges"], allowed, edge_limit, str(focus_id or ""))
        return {**retained, "nodes": nodes, "edges": edges, "nodeLimit": node_limit,
                "edgeLimit": edge_limit, "nodeCount": len(nodes), "edgeCount": len(edges),
                "detectedNodeCount": detected_node_count,
                "detectedEdgeCount": detected_edge_count,
                "displayedNodeCount": len(nodes), "displayedEdgeCount": len(edges),
                "ranking": {"lens": "ADAPTIVE_RELEVANCE", "activityDecaySeconds": 120,
                            "focusId": str(focus_id or "") or None,
                            "purposeCounts": purpose_counts,
                            "suppressedNodes": max(0, detected_node_count - len(nodes)),
                            "suppressedEdges": max(0, detected_edge_count - len(edges)),
                            "ordinaryPairCap": 4, "ordinaryHubSoftCap": 30}}

    def resolve(self, selection: Dict[str, Any]) -> Dict[str, Any]:
        requested_revision = selection.get("graphRevision")
        revision = self.revision()
        if requested_revision and requested_revision != revision:
            snapshot = _cached_snapshot(str(requested_revision))
            if snapshot is None:
                raise GraphResolutionError("graph selection is stale; retained snapshot is unavailable")
            self._pinned_nodes = snapshot["nodes"]
            self._pinned_edges = snapshot["edges"]
            self._pinned_revision = snapshot["graphRevision"]
            revision = self._pinned_revision
        entity_id = str(selection.get("entityId", ""))
        selection_kind = selection.get("kind", "graph-node")
        if selection_kind == "graph-edge":
            edge = next((self._normalize_edge(candidate) for candidate in self._edges()
                         if self._normalize_edge(candidate)["id"] == entity_id), None)
            if edge is None:
                raise GraphResolutionError("selected graph edge was not found")
            nodes_by_id = {item["id"]: item for item in map(self._normalize_node, self._nodes())}
            members = [nodes_by_id[node_id] for node_id in edge["nodes"] if node_id in nodes_by_id]
            positions = [member["position"] for member in members if member.get("position")]
            edge["position"] = ([sum(item[index] for item in positions) / len(positions) for index in range(3)]
                                if positions else None)
            from ip_enrichment import enrich_graph_node
            return {"graphRevision": revision, "selectionKind": selection_kind, "edge": edge,
                    "memberNodes": [enrich_graph_node(item) for item in members[:20]],
                    "incidentEdges": [edge], "bounded": True}
        node = next((self._normalize_node(candidate) for candidate in self._nodes()
                     if _node_id(candidate, candidate.get("_fallback_id", "")) == entity_id), None)
        if node is None:
            raise GraphResolutionError("selected graph entity was not found")
        incident = []
        for edge in self._edges():
            if entity_id in self._edge_nodes(edge):
                incident.append(self._normalize_edge(edge))
            if len(incident) >= 50:
                break
        from ip_enrichment import enrich_graph_node
        return {"graphRevision": revision, "selectionKind": selection_kind,
                "node": enrich_graph_node(node), "incidentEdges": incident, "bounded": True}

    def explore(self, *, query: str = "", protocol: str = "", start: Any = None,
                end: Any = None, focus_id: str = "", depth: int = 1,
                node_limit: int = 100, edge_limit: int = 150,
                node_offset: int = 0, edge_offset: int = 0) -> Dict[str, Any]:
        """Search a bounded current-graph scan without widening the live view."""
        query = str(query or "").strip().lower()
        protocol = str(protocol or "").strip().lower()
        focus_id = str(focus_id or "").strip()
        if len(query) > 128:
            raise GraphResolutionError("query exceeds 128 characters")
        if protocol and (len(protocol) > 32 or not all(char.isalnum() or char in "_-" for char in protocol)):
            raise GraphResolutionError("protocol is invalid")
        depth = min(max(int(depth), 0), 2)
        node_limit = min(max(int(node_limit), 1), 200)
        edge_limit = min(max(int(edge_limit), 1), 300)
        node_offset = min(max(int(node_offset), 0), 10_000)
        edge_offset = min(max(int(edge_offset), 0), 50_000)

        def pin(value: Any, name: str) -> float | None:
            if value in (None, ""):
                return None
            parsed = _timestamp(value)
            if parsed is None:
                raise GraphResolutionError(f"{name} must be a finite epoch timestamp")
            return parsed

        start_time = pin(start, "start"); end_time = pin(end, "end")
        if start_time is not None and end_time is not None and start_time > end_time:
            start_time, end_time = end_time, start_time

        raw_nodes = self._nodes(); raw_edges = self._edges()
        node_scan_limit = 2_000; edge_scan_limit = 10_000
        from ip_enrichment import enrich_graph_node
        nodes = [enrich_graph_node(self._normalize_node(item)) for item in raw_nodes[:node_scan_limit]]
        node_ids = {item["id"] for item in nodes}
        edges = [self._normalize_edge(item) for item in raw_edges[:edge_scan_limit]]
        edges = [item for item in edges if item["nodes"] and all(member in node_ids for member in item["nodes"])]

        def in_window(entity: Dict[str, Any]) -> bool:
            if start_time is None and end_time is None:
                return True
            observed = entity.get("observedAt")
            return observed is not None and (start_time is None or observed >= start_time) and (end_time is None or observed <= end_time)

        def searchable(entity: Dict[str, Any]) -> str:
            enrichment = entity.get("enrichment") or {}
            network = enrichment.get("network") or {}; geo = enrichment.get("geo") or {}
            values = [entity.get("id"), entity.get("kind"), entity.get("evidenceClass"),
                      *(entity.get("labels") or {}).values(), network.get("asn"),
                      network.get("organization"), network.get("prefix"), geo.get("city"),
                      geo.get("region"), geo.get("country"), geo.get("countryCode")]
            return " ".join(str(value).lower() for value in values if value not in (None, ""))

        window_nodes = [item for item in nodes if in_window(item)]
        window_edges = [item for item in edges if in_window(item)]
        protocol_edges = [item for item in window_edges if not protocol or
                          str((item.get("labels") or {}).get("proto") or "").lower() == protocol]
        protocol_node_ids = ({member for edge in protocol_edges for member in edge["nodes"]}
                             if protocol else {item["id"] for item in window_nodes})
        matching_node_ids = {item["id"] for item in window_nodes
                             if item["id"] in protocol_node_ids and (not query or query in searchable(item))}
        matching_edge_ids = {item["id"] for item in protocol_edges if not query or query in searchable(item)}
        if query:
            directly_matching_nodes = set(matching_node_ids)
            directly_matching_edges = set(matching_edge_ids)
            matching_node_ids.update(member for edge in protocol_edges if edge["id"] in directly_matching_edges
                                     for member in edge["nodes"])
            matching_edge_ids.update(edge["id"] for edge in protocol_edges
                                     if any(member in directly_matching_nodes for member in edge["nodes"]))

        focus_found = False
        if focus_id:
            focus_nodes: set[str] = set(); focus_edges: set[str] = set()
            edge_focus = next((item for item in protocol_edges if item["id"] == focus_id), None)
            if focus_id in node_ids:
                focus_nodes.add(focus_id); frontier = {focus_id}; focus_found = True
            elif edge_focus:
                focus_edges.add(focus_id); focus_nodes.update(edge_focus["nodes"])
                frontier = set(edge_focus["nodes"]); focus_found = True
            else:
                frontier = set()
            for _ in range(depth):
                next_frontier = set()
                for edge in protocol_edges:
                    if any(member in frontier for member in edge["nodes"]):
                        focus_edges.add(edge["id"]); next_frontier.update(edge["nodes"])
                next_frontier -= focus_nodes; focus_nodes.update(next_frontier); frontier = next_frontier
            matching_node_ids &= focus_nodes
            matching_edge_ids &= focus_edges

        def newest(entity: Dict[str, Any]):
            observed = entity.get("observedAt")
            return (-(observed if observed is not None else -1), entity["id"])

        matched_nodes = sorted((item for item in window_nodes if item["id"] in matching_node_ids), key=newest)
        matched_edges = sorted((item for item in protocol_edges if item["id"] in matching_edge_ids), key=newest)
        returned_nodes = matched_nodes[node_offset:node_offset + node_limit]
        returned_edges = matched_edges[edge_offset:edge_offset + edge_limit]
        unknown_time_excluded = (sum(item.get("observedAt") is None for item in [*nodes, *edges])
                                 if start_time is not None or end_time is not None else 0)
        return {
            "status": "ok", "graphRevision": self.revision(), "bounded": True,
            "nodes": returned_nodes, "edges": returned_edges,
            "query": {"text": query, "protocol": protocol, "start": start_time, "end": end_time,
                      "focusId": focus_id, "depth": depth},
            "counts": {"availableNodes": len(raw_nodes), "availableEdges": len(raw_edges),
                       "scannedNodes": len(nodes), "scannedEdges": len(edges),
                       "matchedNodes": len(matched_nodes), "matchedEdges": len(matched_edges),
                       "returnedNodes": len(returned_nodes), "returnedEdges": len(returned_edges),
                       "nodeOffset": node_offset, "edgeOffset": edge_offset,
                       "unknownTimeExcluded": unknown_time_excluded},
            "limits": {"nodeScan": node_scan_limit, "edgeScan": edge_scan_limit,
                       "nodeReturn": node_limit, "edgeReturn": edge_limit},
            "scanTruncated": len(raw_nodes) > node_scan_limit or len(raw_edges) > edge_scan_limit,
            "focus": {"requested": bool(focus_id), "found": focus_found,
                      "id": focus_id or None, "depth": depth},
            "temporalSemantics": "ENTITY_OBSERVED_AT_FILTER",
            "boundary": "SEARCH AND NEIGHBORHOOD RESULTS ARE A BOUNDED GRAPH INDEX; ADJACENCY IS NOT CAUSALITY",
        }

    def delta(self, start: float, end: float, *, limit: int = 100) -> Dict[str, Any]:
        start = float(start); end = float(end)
        if not math.isfinite(start) or not math.isfinite(end) or start == end:
            raise GraphResolutionError("time pins must be finite and distinct")
        if start > end:
            start, end = end, start
        limit = min(max(int(limit), 1), 200)
        self.snapshot(node_limit=500, edge_limit=1000)
        retained = _retained_snapshots()
        if not retained:
            raise GraphResolutionError("no retained immutable graph snapshots are available")

        def snapshot_at(pin: float) -> tuple[Dict[str, Any], bool]:
            prior = [item for item in retained if float(item.get("capturedAt", 0.0)) <= pin]
            if prior:
                selected = prior[-1]
                return selected, abs(float(selected["capturedAt"]) - pin) < 1e-6
            return retained[0], False

        before, start_exact = snapshot_at(start)
        after, end_exact = snapshot_at(end)
        before_nodes = {item["id"]: item for item in before["nodes"]}
        after_nodes = {item["id"]: item for item in after["nodes"]}
        before_edges = {item["id"]: item for item in before["edges"]}
        after_edges = {item["id"]: item for item in after["edges"]}

        def added(left, right):
            return [right[key] for key in sorted(right.keys() - left.keys())][:limit]

        def removed(left, right):
            return [left[key] for key in sorted(left.keys() - right.keys())][:limit]

        def changed(left, right):
            return [{"before": left[key], "after": right[key]} for key in sorted(left.keys() & right.keys())
                    if left[key] != right[key]][:limit]

        unknown = sum(item.get("observedAt") is None for item in [*after_nodes.values(), *after_edges.values()])
        return {
            "graphRevision": after["graphRevision"],
            "fromGraphRevision": before["graphRevision"], "toGraphRevision": after["graphRevision"],
            "from": start, "to": end,
            "addedNodes": added(before_nodes, after_nodes), "addedEdges": added(before_edges, after_edges),
            "removedNodes": removed(before_nodes, after_nodes), "removedEdges": removed(before_edges, after_edges),
            "changedNodes": changed(before_nodes, after_nodes), "changedEdges": changed(before_edges, after_edges),
            "unknownTimeCount": unknown, "bounded": True, "limit": limit,
            "retainedSnapshotCount": len(retained),
            "windowCoverage": {
                "requestedFrom": start, "requestedTo": end,
                "snapshotFrom": before["capturedAt"], "snapshotTo": after["capturedAt"],
                "fromExact": start_exact, "toExact": end_exact,
                "clamped": not (start_exact and end_exact),
            },
            "temporalSemantics": "RETAINED_IMMUTABLE_SNAPSHOT_DIFF",
        }

    def provenance(self, selection: Dict[str, Any], *, depth: int = 3, limit: int = 100) -> Dict[str, Any]:
        resolved = self.resolve(selection)
        depth = min(max(int(depth), 0), 5); limit = min(max(int(limit), 1), 200)
        start_ids = ([resolved["node"]["id"]] if resolved.get("node") else list(resolved["edge"]["nodes"]))
        nodes = {item["id"]: item for item in map(self._normalize_node, self._nodes())}
        edges = [self._normalize_edge(item) for item in self._edges()]
        visited = set(start_ids); frontier = list(start_ids); path_edges = []
        for _ in range(depth):
            next_frontier = []
            for edge in edges:
                if not visited.intersection(edge["nodes"]):
                    continue
                if edge["id"] not in {item["id"] for item in path_edges}:
                    path_edges.append(edge)
                for node_id in edge["nodes"]:
                    if node_id not in visited:
                        visited.add(node_id); next_frontier.append(node_id)
                if len(visited) + len(path_edges) >= limit:
                    break
            frontier = next_frontier
            if not frontier or len(visited) + len(path_edges) >= limit:
                break
        path_nodes = [nodes[node_id] for node_id in visited if node_id in nodes]
        sources = []
        for entity in [*path_nodes, *path_edges]:
            metadata = entity.get("metadata") or {}
            source = metadata.get("source") or metadata.get("provenance") or metadata.get("dataset")
            if source is not None:
                sources.append({"entityId": entity["id"], "source": source})
        return {"graphRevision": self.revision(), "root": selection.get("entityId"),
                "nodes": path_nodes[:limit], "edges": path_edges[:limit], "sources": sources[:limit],
                "bounded": True, "depth": depth, "limit": limit}

    def contradictions(self, selection: Dict[str, Any], *, limit: int = 100) -> Dict[str, Any]:
        provenance = self.provenance(selection, depth=2, limit=limit)
        entity_ids = {item["id"] for item in [*provenance["nodes"], *provenance["edges"]]}
        findings = []
        for edge in map(self._normalize_edge, self._edges()):
            kind_is_contradiction = "contradict" in edge["kind"].lower() or "conflict" in edge["kind"].lower()
            explicit = edge.get("contradictions") or []
            if entity_ids.intersection(edge["nodes"]) and (kind_is_contradiction or explicit):
                findings.append({**edge, "findingClass": "CONTRADICTION",
                                 "reason": "explicit contradiction relation" if explicit else "contradiction edge kind"})
            if len(findings) >= min(max(int(limit), 1), 200):
                break
        return {"graphRevision": self.revision(), "root": selection.get("entityId"),
                "contradictions": findings, "bounded": True, "limit": limit}
