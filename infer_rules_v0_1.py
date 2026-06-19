"""
infer_rules_v0_1.py — RF SCYTHE Python inference engine (v0.1)

Consumes a hypergraph snapshot (nodes + edges), applies deterministic
inference rules, and emits GraphOps for any INFERRED_* edges that do
not already exist.  Uses the same deterministic ID generators from
graph_ids so re-runs are idempotent.

Usage:
    from infer_rules_v0_1 import InferenceEngine
    engine = InferenceEngine(nodes, edges)
    new_ops = engine.run_all()
    # new_ops is a list of GraphOp ready for bus().commit()

Rule ID scheme:  R-<DOMAIN>-<NNN>
Domains: ORG, SVC, DNS, TLS, HTTP, CORR, GEO, ANOM
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from graph_ids import (
    host_id as _host_id,
    flow_id as _flow_id,
    org_id as _org_id,
    service_id as _service_id,
    dns_name_id as _dns_name_id,
    tls_sni_id as _tls_sni_id,
    http_host_id as _http_host_id,
)
from writebus import GraphOp

Json = Dict[str, Any]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _inferred_edge_id(rule_id: str, *parts: str) -> str:
    """Deterministic ID for an inferred edge.  Stable across re-runs."""
    h = hashlib.sha256("|".join(parts).encode()).hexdigest()[:12]
    return f"e:inferred:{rule_id.lower()}:{h}"


def _edge_op(edge: Json) -> GraphOp:
    return GraphOp(event_type="EDGE_UPDATE", entity_id=str(edge["id"]), entity_data=edge)


def _make_inferred_edge(
    edge_id: str,
    kind: str,
    node_ids: list,
    *,
    rule_id: str,
    confidence: float,
    evidence: list[str],
    source: str = "python_rules",
) -> Json:
    """Canonical inferred edge dict."""
    return {
        "id": edge_id,
        "kind": kind,
        "nodes": node_ids,
        "timestamp": time.time(),
        "metadata": {
            "obs_class": "inferred",
            "confidence": confidence,
            "provenance": {
                "source": source,
                "rule_id": rule_id,
                "evidence": evidence[:8],          # cap evidence list
                "timestamp": time.time(),
            },
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Index structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class GraphIndex:
    """Lightweight indexes over a snapshot for O(1) lookups."""
    nodes: Dict[str, Json] = field(default_factory=dict)          # id → node
    edges: Dict[str, Json] = field(default_factory=dict)          # id → edge
    nodes_by_kind: Dict[str, List[Json]] = field(default_factory=dict)
    edges_by_kind: Dict[str, List[Json]] = field(default_factory=dict)
    # Adjacency: node_id → list of edges touching it
    edges_of_node: Dict[str, List[Json]] = field(default_factory=dict)
    existing_edge_ids: Set[str] = field(default_factory=set)

    @classmethod
    def build(cls, nodes: list[Json], edges: list[Json]) -> "GraphIndex":
        idx = cls()
        for n in nodes:
            nid = n.get("id", "")
            idx.nodes[nid] = n
            kind = n.get("kind", "")
            idx.nodes_by_kind.setdefault(kind, []).append(n)

        for e in edges:
            eid = e.get("id", "")
            idx.edges[eid] = e
            idx.existing_edge_ids.add(eid)
            kind = e.get("kind", "")
            idx.edges_by_kind.setdefault(kind, []).append(e)
            for nid in e.get("nodes", []):
                idx.edges_of_node.setdefault(nid, []).append(e)
        return idx

    def edges_of_kind(self, kind: str) -> list[Json]:
        return self.edges_by_kind.get(kind, [])

    def nodes_of_kind(self, kind: str) -> list[Json]:
        return self.nodes_by_kind.get(kind, [])

    def node(self, nid: str) -> Json | None:
        return self.nodes.get(nid)


# ─────────────────────────────────────────────────────────────────────────────
# Inference Engine
# ─────────────────────────────────────────────────────────────────────────────

class InferenceEngine:
    """
    Stateless rule evaluator.  Given a snapshot, produces a list of
    GraphOps for new INFERRED_* edges.

    All rules:
      R-ORG-001  HOST_IN_ASN + ASN_IN_ORG → INFERRED_HOST_IN_ORG
      R-SVC-001  FLOW_DST_PORT + PORT_IMPLIED_SERVICE → INFERRED_FLOW_IN_SERVICE
      R-SVC-002  dst_ip match + INFERRED_FLOW_IN_SERVICE → INFERRED_HOST_OFFERS_SERVICE
      R-TLS-001  FLOW_TLS_SNI + flow.src_ip → INFERRED_HOST_CONTACTED_SNI
      R-HTTP-001 FLOW_HTTP_HOST + flow.src_ip → INFERRED_HOST_CONTACTED_HTTP_HOST
      R-CORR-001 FLOW_TLS_SNI + FLOW_HTTP_HOST (same domain) → INFERRED_FLOW_SNI_EQ_HTTP_HOST
      R-DNS-001  FLOW_QUERIED_DNS + flow.src_ip → INFERRED_HOST_QUERIED_DNSNAME
      R-DNS-002  dns_name answer IPs match host → INFERRED_DNSNAME_RESOLVES_HOST
      R-GEO-001  src/dst geo countries differ → INFERRED_FLOW_CROSS_BORDER
    """

    def __init__(self, nodes: list[Json], edges: list[Json]):
        self.idx = GraphIndex.build(nodes, edges)
        self._emitted: set[str] = set()
        self._ops: list[GraphOp] = []

    def _emit(self, edge: Json) -> None:
        eid = edge["id"]
        if eid in self._emitted or eid in self.idx.existing_edge_ids:
            return
        self._emitted.add(eid)
        self._ops.append(_edge_op(edge))

    # ── helpers ─────────────────────────────────────────────────────────────

    def _flow_src_ip(self, flow_node: Json) -> str:
        labels = flow_node.get("labels", {})
        return labels.get("src_ip", "")

    def _flow_dst_ip(self, flow_node: Json) -> str:
        labels = flow_node.get("labels", {})
        return labels.get("dst_ip", "")

    def _flow_node_for_edge(self, edge: Json) -> Json | None:
        """Return the flow node from a flow→X edge (flow is always nodes[0])."""
        nodes = edge.get("nodes", [])
        if nodes:
            return self.idx.node(nodes[0])
        return None

    def _host_node_by_ip(self, ip: str) -> Json | None:
        hid = _host_id(ip)
        return self.idx.node(hid)

    # ── R-ORG-001: Host in Org ──────────────────────────────────────────────

    def _rule_org_001(self) -> None:
        """HOST_IN_ASN(host, asn) + ASN_IN_ORG(asn, org) → INFERRED_HOST_IN_ORG(host, org)"""
        host_asn_edges = self.idx.edges_of_kind("HOST_IN_ASN")
        asn_org_map: dict[str, list[tuple[str, str]]] = {}  # asn_id → [(org_id, edge_id)]

        for e in self.idx.edges_of_kind("ASN_IN_ORG"):
            nodes = e.get("nodes", [])
            if len(nodes) >= 2:
                asn_org_map.setdefault(nodes[0], []).append((nodes[1], e["id"]))

        for ha_edge in host_asn_edges:
            nodes = ha_edge.get("nodes", [])
            if len(nodes) < 2:
                continue
            host_id, asn_id = nodes[0], nodes[1]
            for org_id, ao_eid in asn_org_map.get(asn_id, []):
                eid = _inferred_edge_id("R-ORG-001", host_id, org_id)
                ha_conf = (ha_edge.get("metadata") or {}).get("confidence", 0.85)
                ao_edge = self.idx.edges.get(ao_eid, {})
                ao_conf = (ao_edge.get("metadata") or {}).get("confidence", 0.80)
                conf = round(min(ha_conf, ao_conf) * 0.95, 3)
                self._emit(_make_inferred_edge(
                    eid, "INFERRED_HOST_IN_ORG", [host_id, org_id],
                    rule_id="R-ORG-001", confidence=conf,
                    evidence=[ha_edge["id"], ao_eid],
                ))

    # ── R-SVC-001: Flow implied service ─────────────────────────────────────

    def _rule_svc_001(self) -> None:
        """FLOW_DST_PORT(flow, port_hub) + PORT_IMPLIED_SERVICE(port_hub, svc) → INFERRED_FLOW_IN_SERVICE(flow, svc)"""
        port_svc_map: dict[str, list[tuple[str, str]]] = {}  # port_id → [(svc_id, edge_id)]

        for e in self.idx.edges_of_kind("PORT_IMPLIED_SERVICE"):
            nodes = e.get("nodes", [])
            if len(nodes) >= 2:
                port_svc_map.setdefault(nodes[0], []).append((nodes[1], e["id"]))

        for fp_edge in self.idx.edges_of_kind("FLOW_DST_PORT"):
            nodes = fp_edge.get("nodes", [])
            if len(nodes) < 2:
                continue
            flow_id, port_id = nodes[0], nodes[1]
            for svc_id, ps_eid in port_svc_map.get(port_id, []):
                eid = _inferred_edge_id("R-SVC-001", flow_id, svc_id)
                conf = 0.65
                self._emit(_make_inferred_edge(
                    eid, "INFERRED_FLOW_IN_SERVICE", [flow_id, svc_id],
                    rule_id="R-SVC-001", confidence=conf,
                    evidence=[fp_edge["id"], ps_eid],
                ))

    # ── R-SVC-002: Host offers service ──────────────────────────────────────

    def _rule_svc_002(self) -> None:
        """If flow dst_ip == host AND INFERRED_FLOW_IN_SERVICE → INFERRED_HOST_OFFERS_SERVICE"""
        for inferred_edge in (self.idx.edges_of_kind("INFERRED_FLOW_IN_SERVICE") +
                              [e for e in self._ops_as_edges() if e.get("kind") == "INFERRED_FLOW_IN_SERVICE"]):
            nodes = inferred_edge.get("nodes", [])
            if len(nodes) < 2:
                continue
            flow_id, svc_id = nodes[0], nodes[1]
            flow_node = self.idx.node(flow_id)
            if not flow_node:
                continue
            dst_ip = self._flow_dst_ip(flow_node)
            if not dst_ip:
                continue
            host_nid = _host_id(dst_ip)
            if not self.idx.node(host_nid):
                continue
            eid = _inferred_edge_id("R-SVC-002", host_nid, svc_id)
            self._emit(_make_inferred_edge(
                eid, "INFERRED_HOST_OFFERS_SERVICE", [host_nid, svc_id],
                rule_id="R-SVC-002", confidence=0.55,
                evidence=[inferred_edge["id"]],
            ))

    # ── R-TLS-001: Host contacted SNI ───────────────────────────────────────

    def _rule_tls_001(self) -> None:
        """FLOW_TLS_SNI(flow, sni) + flow.src_ip → INFERRED_HOST_CONTACTED_SNI(host, sni)"""
        for edge in self.idx.edges_of_kind("FLOW_TLS_SNI"):
            nodes = edge.get("nodes", [])
            if len(nodes) < 2:
                continue
            flow_id, sni_id = nodes[0], nodes[1]
            flow_node = self.idx.node(flow_id)
            if not flow_node:
                continue
            src_ip = self._flow_src_ip(flow_node)
            if not src_ip:
                continue
            host_nid = _host_id(src_ip)
            if not self.idx.node(host_nid):
                continue
            eid = _inferred_edge_id("R-TLS-001", host_nid, sni_id)
            self._emit(_make_inferred_edge(
                eid, "INFERRED_HOST_CONTACTED_SNI", [host_nid, sni_id],
                rule_id="R-TLS-001", confidence=0.88,
                evidence=[edge["id"]],
            ))

    # ── R-HTTP-001: Host contacted HTTP host ────────────────────────────────

    def _rule_http_001(self) -> None:
        """FLOW_HTTP_HOST(flow, http_host) + flow.src_ip → INFERRED_HOST_CONTACTED_HTTP_HOST"""
        for edge in self.idx.edges_of_kind("FLOW_HTTP_HOST"):
            nodes = edge.get("nodes", [])
            if len(nodes) < 2:
                continue
            flow_id, hh_id = nodes[0], nodes[1]
            flow_node = self.idx.node(flow_id)
            if not flow_node:
                continue
            src_ip = self._flow_src_ip(flow_node)
            if not src_ip:
                continue
            host_nid = _host_id(src_ip)
            if not self.idx.node(host_nid):
                continue
            eid = _inferred_edge_id("R-HTTP-001", host_nid, hh_id)
            self._emit(_make_inferred_edge(
                eid, "INFERRED_HOST_CONTACTED_HTTP_HOST", [host_nid, hh_id],
                rule_id="R-HTTP-001", confidence=0.88,
                evidence=[edge["id"]],
            ))

    # ── R-CORR-001: SNI matches HTTP host ──────────────────────────────────

    def _rule_corr_001(self) -> None:
        """If FLOW_TLS_SNI + FLOW_HTTP_HOST on same flow and domains match → INFERRED_FLOW_SNI_EQ_HTTP_HOST"""
        # Build flow → sni_node map
        flow_sni: dict[str, tuple[str, str]] = {}  # flow_id → (sni_node_id, edge_id)
        for edge in self.idx.edges_of_kind("FLOW_TLS_SNI"):
            nodes = edge.get("nodes", [])
            if len(nodes) >= 2:
                flow_sni[nodes[0]] = (nodes[1], edge["id"])

        for edge in self.idx.edges_of_kind("FLOW_HTTP_HOST"):
            nodes = edge.get("nodes", [])
            if len(nodes) < 2:
                continue
            flow_id, hh_id = nodes[0], nodes[1]
            sni_info = flow_sni.get(flow_id)
            if not sni_info:
                continue
            sni_id, sni_eid = sni_info
            # Compare domains (normalize)
            sni_node = self.idx.node(sni_id)
            hh_node = self.idx.node(hh_id)
            if not sni_node or not hh_node:
                continue
            sni_val = (sni_node.get("labels") or {}).get("sni", "").lower().strip()
            hh_val = (hh_node.get("labels") or {}).get("host", "").lower().strip()
            if not sni_val or not hh_val:
                continue
            # Compare: exact match or one is suffix of the other
            if sni_val == hh_val or sni_val.endswith("." + hh_val) or hh_val.endswith("." + sni_val):
                eid = _inferred_edge_id("R-CORR-001", flow_id, hh_id)
                self._emit(_make_inferred_edge(
                    eid, "INFERRED_FLOW_SNI_EQ_HTTP_HOST", [flow_id, hh_id],
                    rule_id="R-CORR-001", confidence=0.92,
                    evidence=[sni_eid, edge["id"]],
                ))

    # ── R-DNS-001: Host queried DNS name ───────────────────────────────────

    def _rule_dns_001(self) -> None:
        """FLOW_QUERIED_DNS(flow, dns_name) + flow.src_ip → INFERRED_HOST_QUERIED_DNSNAME"""
        for edge in self.idx.edges_of_kind("FLOW_QUERIED_DNS"):
            nodes = edge.get("nodes", [])
            if len(nodes) < 2:
                continue
            flow_id, dns_id = nodes[0], nodes[1]
            flow_node = self.idx.node(flow_id)
            if not flow_node:
                continue
            src_ip = self._flow_src_ip(flow_node)
            if not src_ip:
                continue
            host_nid = _host_id(src_ip)
            if not self.idx.node(host_nid):
                continue
            eid = _inferred_edge_id("R-DNS-001", host_nid, dns_id)
            self._emit(_make_inferred_edge(
                eid, "INFERRED_HOST_QUERIED_DNSNAME", [host_nid, dns_id],
                rule_id="R-DNS-001", confidence=0.88,
                evidence=[edge["id"]],
            ))

    # ── R-DNS-002: DNS name resolves to host ───────────────────────────────

    def _rule_dns_002(self) -> None:
        """dns_name.metadata.answers contains IP X AND host.ip == X → INFERRED_DNSNAME_RESOLVES_HOST"""
        # Build IP → host_id lookup
        ip_to_host: dict[str, str] = {}
        for h in self.idx.nodes_of_kind("host"):
            ip = (h.get("labels") or {}).get("ip", "")
            if ip:
                ip_to_host[ip] = h["id"]

        for dns_node in self.idx.nodes_of_kind("dns_name"):
            dns_id = dns_node["id"]
            answers = (dns_node.get("metadata") or {}).get("answers", [])
            seen_hosts: set[str] = set()
            for ans in answers:
                answer_val = ans.get("answer", "")
                host_nid = ip_to_host.get(answer_val)
                if host_nid and host_nid not in seen_hosts:
                    seen_hosts.add(host_nid)
                    eid = _inferred_edge_id("R-DNS-002", dns_id, host_nid)
                    self._emit(_make_inferred_edge(
                        eid, "INFERRED_DNSNAME_RESOLVES_HOST", [dns_id, host_nid],
                        rule_id="R-DNS-002", confidence=0.75,
                        evidence=[dns_id],
                    ))

    # ── R-GEO-001: Cross-border flow ───────────────────────────────────────

    def _rule_geo_001(self) -> None:
        """host geo countries differ across flow → INFERRED_FLOW_CROSS_BORDER"""
        # Build host_id → country
        host_country: dict[str, str] = {}
        for edge in self.idx.edges_of_kind("HOST_GEO_ESTIMATE"):
            nodes = edge.get("nodes", [])
            if len(nodes) < 2:
                continue
            host_nid, geo_nid = nodes[0], nodes[1]
            geo_node = self.idx.node(geo_nid)
            if geo_node:
                country = (geo_node.get("labels") or {}).get("country", "")
                if country:
                    host_country[host_nid] = country

        for flow_node in self.idx.nodes_of_kind("flow"):
            src_ip = self._flow_src_ip(flow_node)
            dst_ip = self._flow_dst_ip(flow_node)
            if not src_ip or not dst_ip:
                continue
            src_hid = _host_id(src_ip)
            dst_hid = _host_id(dst_ip)
            src_country = host_country.get(src_hid, "")
            dst_country = host_country.get(dst_hid, "")
            if src_country and dst_country and src_country != dst_country:
                fid = flow_node["id"]
                eid = _inferred_edge_id("R-GEO-001", fid, src_country, dst_country)
                # Find geo edge IDs for evidence
                src_geo_edges = [e["id"] for e in self.idx.edges_of_node.get(src_hid, [])
                                 if e.get("kind") == "HOST_GEO_ESTIMATE"]
                dst_geo_edges = [e["id"] for e in self.idx.edges_of_node.get(dst_hid, [])
                                 if e.get("kind") == "HOST_GEO_ESTIMATE"]
                self._emit(_make_inferred_edge(
                    eid, "INFERRED_FLOW_CROSS_BORDER", [fid],
                    rule_id="R-GEO-001", confidence=0.70,
                    evidence=src_geo_edges[:2] + dst_geo_edges[:2],
                ))

    # ── internal helpers ────────────────────────────────────────────────────

    def _ops_as_edges(self) -> list[Json]:
        """Return edges emitted so far as dicts (for forward-chaining within same pass)."""
        return [op.entity_data for op in self._ops]

    # ── public API ──────────────────────────────────────────────────────────

    def run_all(self) -> list[GraphOp]:
        """Run all v0.1 rules and return new GraphOps.  Idempotent (skips existing edges)."""
        self._emitted.clear()
        self._ops.clear()

        # Phase 1: direct observed → inferred (no dependencies between rules)
        self._rule_org_001()
        self._rule_svc_001()
        self._rule_tls_001()
        self._rule_http_001()
        self._rule_dns_001()
        self._rule_dns_002()
        self._rule_geo_001()
        self._rule_corr_001()

        # Phase 2: forward-chaining (depends on Phase 1 output)
        self._rule_svc_002()

        return list(self._ops)

    def run_rule(self, rule_id: str) -> list[GraphOp]:
        """Run a single rule by ID."""
        self._emitted.clear()
        self._ops.clear()
        rule_map = {
            "R-ORG-001": self._rule_org_001,
            "R-SVC-001": self._rule_svc_001,
            "R-SVC-002": self._rule_svc_002,
            "R-TLS-001": self._rule_tls_001,
            "R-HTTP-001": self._rule_http_001,
            "R-CORR-001": self._rule_corr_001,
            "R-DNS-001": self._rule_dns_001,
            "R-DNS-002": self._rule_dns_002,
            "R-GEO-001": self._rule_geo_001,
        }
        fn = rule_map.get(rule_id.upper())
        if fn:
            fn()
        return list(self._ops)

    @staticmethod
    def available_rules() -> list[dict]:
        """Return metadata about all v0.1 rules."""
        return [
            {"id": "R-ORG-001",  "domain": "ORG",  "name": "Host in Org",                "inputs": ["HOST_IN_ASN", "ASN_IN_ORG"]},
            {"id": "R-SVC-001",  "domain": "SVC",  "name": "Flow implied service",        "inputs": ["FLOW_DST_PORT", "PORT_IMPLIED_SERVICE"]},
            {"id": "R-SVC-002",  "domain": "SVC",  "name": "Host offers service",          "inputs": ["INFERRED_FLOW_IN_SERVICE"]},
            {"id": "R-TLS-001",  "domain": "TLS",  "name": "Host contacted SNI",           "inputs": ["FLOW_TLS_SNI"]},
            {"id": "R-HTTP-001", "domain": "HTTP", "name": "Host contacted HTTP host",     "inputs": ["FLOW_HTTP_HOST"]},
            {"id": "R-CORR-001", "domain": "CORR", "name": "SNI matches HTTP host",        "inputs": ["FLOW_TLS_SNI", "FLOW_HTTP_HOST"]},
            {"id": "R-DNS-001",  "domain": "DNS",  "name": "Host queried DNS name",        "inputs": ["FLOW_QUERIED_DNS"]},
            {"id": "R-DNS-002",  "domain": "DNS",  "name": "DNS name resolves to host",    "inputs": ["dns_name.metadata.answers"]},
            {"id": "R-GEO-001",  "domain": "GEO",  "name": "Cross-border flow",            "inputs": ["HOST_GEO_ESTIMATE"]},
        ]
