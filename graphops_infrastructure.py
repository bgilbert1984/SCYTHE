"""Evidence-partitioned infrastructure projection of a bounded GraphOps snapshot."""

from __future__ import annotations

import hashlib
import ipaddress
import json
from copy import deepcopy
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, Optional


SCHEMA_VERSION = "graphops.infrastructure.v1"
MAX_DOMAINS = 128
MAX_FLOWS = 256
MAX_MEMBERS = 16
MAX_CANDIDATES = 64


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _host_ip(node: Dict[str, Any]) -> str:
    labels = node.get("labels") or {}
    value = str(labels.get("ip") or "").strip()
    if not value and str(node.get("id") or "").startswith("host:"):
        value = str(node["id"])[5:]
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return ""


def _domain(node: Dict[str, Any]) -> Dict[str, Any]:
    ip = _host_ip(node)
    enrichment = node.get("enrichment") or {}
    network = enrichment.get("network") or {}
    geo = enrichment.get("geo") or {}
    try:
        address = ipaddress.ip_address(ip)
        public = address.is_global
    except ValueError:
        public = False
    asn = int(_number(network.get("asn"))) if public and network.get("asn") else 0
    domain_id = f"asn:{asn}" if asn else ("network:unresolved-public" if public else "network:local")
    latitude = geo.get("latitude", geo.get("lat"))
    longitude = geo.get("longitude", geo.get("lon"))
    location = None
    if public and latitude is not None and longitude is not None:
        location = {
            "latitude": _number(latitude), "longitude": _number(longitude),
            "uncertaintyRadiusKm": max(1.0, _number(geo.get("uncertaintyRadiusKm"), 1000.0)),
            "city": str(geo.get("city") or ""), "region": str(geo.get("region") or ""),
            "country": str(geo.get("country") or ""),
            "evidenceClass": "INFERRED", "authority": "GEOIP_ESTIMATE",
        }
    return {
        "id": domain_id, "asn": asn or None,
        "organization": str(network.get("organization") or
                            ("PUBLIC OWNERSHIP UNRESOLVED" if public else "LOCAL OR NON-PUBLIC")),
        "prefix": str(network.get("prefix") or ""), "hostId": str(node.get("id") or ""),
        "ip": ip, "location": location,
        "evidenceClass": "INFERRED" if (asn or public) else "OBSERVED_SCOPE",
        "authority": "HOST_PREFIX_ENRICHMENT" if asn else
                     ("PUBLIC_SCOPE_WITHOUT_ASN_MATCH" if public else "IP_SCOPE_CLASSIFICATION"),
        "source": network.get("source") or geo.get("source"),
    }


def _centroid(locations: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    values = list(locations)
    if not values:
        return None
    weights = [1.0 / max(1.0, _number(item.get("uncertaintyRadiusKm"), 1000.0)) for item in values]
    total = sum(weights)
    return {
        "latitude": round(sum(_number(item["latitude"]) * weight for item, weight in zip(values, weights)) / total, 6),
        "longitude": round(sum(_number(item["longitude"]) * weight for item, weight in zip(values, weights)) / total, 6),
        "uncertaintyRadiusKm": round(max(_number(item.get("uncertaintyRadiusKm"), 1000.0) for item in values), 1),
        "evidenceClass": "INFERRED", "authority": "GEOIP_ESTIMATE_CENTROID",
        "sampleCount": len(values),
    }


def _stable_id(prefix: str, value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return f"{prefix}-{hashlib.blake2s(encoded, digest_size=8).hexdigest()}"


def build_infrastructure_snapshot(
    graph: Dict[str, Any], focus_id: str = "",
    modeled_path_resolver: Optional[Callable[[int, int], Optional[list[int]]]] = None,
) -> Dict[str, Any]:
    """Project graph facts without upgrading inference into observation."""
    nodes = list(graph.get("nodes") or [])
    edges = list(graph.get("edges") or [])
    hosts = {str(node.get("id")): _domain(node) for node in nodes
             if str(node.get("kind") or "").lower() == "network_host" or
             str(node.get("id") or "").startswith("host:")}
    grouped_domains: Dict[str, list[Dict[str, Any]]] = defaultdict(list)
    for item in hosts.values():
        grouped_domains[item["id"]].append(item)

    domain_rows = []
    for domain_id, members in grouped_domains.items():
        locations = [item["location"] for item in members if item.get("location")]
        prefixes = sorted({item["prefix"] for item in members if item.get("prefix")})[:16]
        sources = [item.get("source") for item in members if item.get("source")]
        domain_rows.append({
            "id": domain_id, "asn": members[0]["asn"], "organization": members[0]["organization"],
            "hostCount": len(members), "observedHostIds": sorted(item["hostId"] for item in members)[:MAX_MEMBERS],
            "prefixes": prefixes, "centroid": _centroid(locations),
            "evidenceClass": members[0]["evidenceClass"], "authority": members[0]["authority"],
            "source": sources[0] if sources else None,
        })
    domain_rows.sort(key=lambda row: (-row["hostCount"], row["id"]))
    domain_rows = domain_rows[:MAX_DOMAINS]
    allowed_domains = {item["id"] for item in domain_rows}
    source_freshness = []
    seen_sources = set()
    for row in domain_rows:
        source = row.get("source")
        if not isinstance(source, dict):
            continue
        frame = {key: source.get(key) for key in ("database", "buildEpoch", "sha256", "localOnly")
                 if source.get(key) is not None}
        identity = json.dumps(frame, sort_keys=True, default=str)
        if identity not in seen_sources:
            seen_sources.add(identity); source_freshness.append(frame)

    aggregates: Dict[tuple[str, str, str], Dict[str, Any]] = {}
    for edge in edges:
        members = [str(item) for item in edge.get("nodes") or []]
        endpoints = [hosts[item] for item in members if item in hosts]
        if len(endpoints) < 2:
            continue
        source, target = endpoints[0]["id"], endpoints[1]["id"]
        if source not in allowed_domains or target not in allowed_domains:
            continue
        labels = edge.get("labels") or {}; metadata = edge.get("metadata") or {}
        protocol = str(labels.get("proto") or labels.get("protocol") or "unknown").lower()
        key = (source, target, protocol)
        if key not in aggregates:
            aggregates[key] = {
                "id": _stable_id("infra-flow", key), "sourceDomain": source, "targetDomain": target,
                "protocol": protocol, "flowCount": 0, "bytes": 0, "packets": 0,
                "firstSeen": None, "lastSeen": None, "memberEdgeIds": [],
                "evidenceClass": "OBSERVED", "endpointAuthority": "INFERRED",
                "routeAuthority": "UNOBSERVED", "pathClaim": False,
            }
        row = aggregates[key]; row["flowCount"] += max(1, int(_number(metadata.get("reinforcement_count"), 1)))
        row["bytes"] += max(0, int(_number(labels.get("bytes", metadata.get("bytes")))))
        row["packets"] += max(0, int(_number(labels.get("packets", metadata.get("packets")))))
        observed = edge.get("observedAt") or metadata.get("observed_at") or edge.get("timestamp")
        if observed is not None:
            value = str(observed)
            row["firstSeen"] = min(filter(None, [row["firstSeen"], value]), default=value)
            row["lastSeen"] = max(filter(None, [row["lastSeen"], value]), default=value)
        if edge.get("id") and len(row["memberEdgeIds"]) < MAX_MEMBERS:
            row["memberEdgeIds"].append(str(edge["id"]))
    flows = sorted(aggregates.values(), key=lambda row: (-row["flowCount"], row["id"]))[:MAX_FLOWS]

    candidates = []
    if modeled_path_resolver:
        for flow in flows:
            source = next((item for item in domain_rows if item["id"] == flow["sourceDomain"]), None)
            target = next((item for item in domain_rows if item["id"] == flow["targetDomain"]), None)
            if not source or not target or not source.get("asn") or not target.get("asn"):
                continue
            path = modeled_path_resolver(source["asn"], target["asn"])
            if path:
                candidates.append({
                    "id": _stable_id("modeled-as-path", path), "asns": [int(item) for item in path],
                    "relatedObservedFlow": flow["id"], "evidenceClass": "MODELED_CANDIDATE",
                    "authority": "REFERENCE_MODEL", "observedRoute": False,
                })
            if len(candidates) >= MAX_CANDIDATES:
                break

    focus_domain = hosts.get(focus_id, {}).get("id")
    return {
        "status": "ok" if domain_rows else "empty", "schemaVersion": SCHEMA_VERSION,
        "graphRevision": graph.get("graphRevision") or "graph-empty",
        "capturedAt": graph.get("capturedAt") or datetime.now(timezone.utc).isoformat(),
        "bounded": True,
        "authority": {
            "flows": "OBSERVED_GRAPH_EDGES", "networkOwnership": "INFERRED_HOST_PREFIX_DATABASE",
            "geography": "INFERRED_GEOIP", "modeledPaths": "REFERENCE_MODEL_NOT_OBSERVED",
            "rendering": "DISPLAY_ONLY_NOT_ROUTE",
        },
        "sourceFreshness": {"liveGraphCapturedAt": graph.get("capturedAt"),
                            "localEnrichmentDatabases": source_freshness,
                            "externalControlPlaneCapturedAt": None},
        "domains": domain_rows, "observedFlows": flows, "modeledPathCandidates": candidates,
        "referenceCatalog": {
            "asPathModel": ({"name": "SCYTHE_EMBEDDED_AS_ADJACENCY", "version": "UNVERSIONED",
                              "freshness": "UNKNOWN", "fitness": "DEMONSTRATION_ONLY",
                              "evidenceClass": "REFERENCE_MODEL"} if modeled_path_resolver else None),
            "externalControlPlane": "NOT_ATTACHED",
        },
        "focus": {"entityId": focus_id or None, "domainId": focus_domain},
        "summary": {"domains": len(domain_rows), "observedFlows": len(flows),
                    "modeledPathCandidates": len(candidates), "observedHosts": len(hosts)},
        "boundary": (
            "TRAFFIC EDGES ARE OBSERVED; ASN OWNERSHIP AND GEOIP ARE INFERRED; MODELED AS PATHS "
            "ARE CANDIDATES; GLOBE ARCS CONNECT UNCERTAIN ENDPOINT REGIONS AND ARE NOT ROUTES"
        ),
    }


def attach_external_infrastructure_evidence(snapshot: Dict[str, Any],
                                            peeringdb: Optional[Dict[str, Any]],
                                            control_plane: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Attach parallel declared/control-plane evidence without altering graph topology."""
    result = deepcopy(snapshot)
    pdb = deepcopy(peeringdb or {"status": "unavailable", "networks": [], "ixMemberships": [],
                                 "facilityPresences": [], "facilities": [], "exchanges": []})
    ris = deepcopy(control_plane or {"status": "unavailable", "controlPlanePaths": []})
    observed_asns = {int(item["asn"]) for item in result.get("domains", []) if item.get("asn")}
    observed_prefixes = []
    for item in result.get("domains", []):
        for value in item.get("prefixes", []):
            try: observed_prefixes.append(ipaddress.ip_network(str(value), strict=False))
            except ValueError: continue
    for row in ris.get("controlPlanePaths") or []:
        try: message_prefix = ipaddress.ip_network(str(row.get("prefix")), strict=False)
        except ValueError: message_prefix = None
        origin = row.get("originAsn")
        origin_values = set(origin if isinstance(origin, list) else [origin])
        row["relevance"] = {
            "prefixMatchesObservedScope": bool(message_prefix and any(
                message_prefix.subnet_of(prefix) or prefix.subnet_of(message_prefix)
                for prefix in observed_prefixes if prefix.version == message_prefix.version)),
            "originMatchesObservedAsn": bool(origin_values & observed_asns),
            "collectorRegionMatch": "UNAVAILABLE",
            "authority": "DETERMINISTIC_SCOPE_MATCH",
        }
    by_ix: Dict[int, set[int]] = defaultdict(set)
    for membership in pdb.get("ixMemberships") or []:
        try: by_ix[int(membership["ix_id"])].add(int(membership["asn"]))
        except (KeyError, TypeError, ValueError): continue
    declared = []
    for ix_id, members in sorted(by_ix.items()):
        values = sorted(members)
        for index, source in enumerate(values):
            for target in values[index + 1:]:
                declared.append({"id": _stable_id("pdb-shared-ix", [ix_id, source, target]),
                                 "sourceAsn": source, "targetAsn": target, "ixId": ix_id,
                                 "evidenceClass": "INFRASTRUCTURE_EVIDENCE",
                                 "authority": "PEERINGDB_SELF_REPORTED_SHARED_IX",
                                 "adjacencyClaim": "DECLARED_SHARED_PRESENCE",
                                 "trafficObserved": False, "routeObserved": False})
                if len(declared) >= 128: break
            if len(declared) >= 128: break
        if len(declared) >= 128: break
    result["peeringdbEvidence"] = pdb
    result["controlPlaneEvidence"] = ris
    result["declaredSharedIxCandidates"] = declared
    result["modeledPathCandidates"] = []
    result["referenceCatalog"] = {
        "legacyEmbeddedAdjacency": "DISABLED",
        "peeringdb": {"schemaVersion": pdb.get("schemaVersion"), "datasetRevision": pdb.get("datasetRevision"),
                      "recordUpdatedThrough": pdb.get("recordUpdatedThrough"),
                      "authority": "SELF_REPORTED_DECLARED_INFRASTRUCTURE"},
        "risLive": {"schemaVersion": ris.get("schemaVersion"),
                    "authority": "CONTROL_PLANE_OBSERVATION_AT_COLLECTOR_VANTAGE"},
        "caidaRelationships": {"status": "NOT_ATTACHED", "authority": "RESEARCH_INFERENCE"},
    }
    result["authority"]["declaredInfrastructure"] = "PEERINGDB_SELF_REPORTED"
    result["authority"]["controlPlane"] = "RIS_LIVE_COLLECTOR_VANTAGE"
    result["sourceFreshness"]["peeringdbRetrievedAt"] = pdb.get("retrievedAt")
    paths = ris.get("controlPlanePaths") or []
    result["sourceFreshness"]["externalControlPlaneCapturedAt"] = max(
        (row.get("collectorReceivedAt") for row in paths if row.get("collectorReceivedAt") is not None), default=None)
    result["summary"].update({"declaredSharedIxCandidates": len(declared),
                              "peeringdbNetworks": len(pdb.get("networks") or []),
                              "controlPlaneObservations": len(paths)})
    result["boundary"] += (
        "; PEERINGDB IS SELF-REPORTED; RIS IS COLLECTOR-VANTAGE CONTROL-PLANE EVIDENCE; "
        "NEITHER ESTABLISHES THE DATA-PLANE PATH"
    )
    return result
