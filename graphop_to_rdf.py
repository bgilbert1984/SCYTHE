"""
graphop_to_rdf.py — Convert RF SCYTHE GraphOps / hypergraph snapshots to RDF.

Produces Turtle (TTL) or SPARQL UPDATE statements that can be pushed to a
Parliament SPARQL endpoint (http://<host>:8089/parliament/sparql).

Usage:
    from graphop_to_rdf import GraphToRDF
    converter = GraphToRDF()
    turtle = converter.snapshot_to_turtle(nodes, edges)
    converter.push_to_parliament(nodes, edges, endpoint="http://localhost:8089/parliament/sparql")
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)

Json = Dict[str, Any]

# ─────────────────────────────────────────────────────────────────────────────
# Ontology namespace
# ─────────────────────────────────────────────────────────────────────────────

RFS_NS = "http://rfscythe.nerfengine.io/ontology/v0.1#"
RFS_DATA = "http://rfscythe.nerfengine.io/data/"
RFS_GRAPH = "http://rfscythe.nerfengine.io/graph/observed"

# Node kind → OWL class
KIND_TO_CLASS = {
    "pcap_session":  "PcapSession",
    "pcap_artifact": "PcapArtifact",
    "host":          "Host",
    "flow":          "Flow",
    "geo_point":     "GeoPoint",
    "asn":           "ASN",
    "org":           "Org",
    "port_hub":      "PortHub",
    "service":       "Service",
    "dns_name":      "DNSName",
    "tls_sni":       "TLSSNI",
    "tls_cert":      "TLSCert",
    "http_host":     "HTTPHost",
    "ja3":           "JA3",
    "ja3s":          "JA3S",
}

# Edge kind → OWL property (camelCase)
EDGE_TO_PROPERTY = {
    "SESSION_OBSERVED_HOST":         "sessionObservedHost",
    "SESSION_OBSERVED_FLOW":         "sessionObservedFlow",
    "HOST_GEO_ESTIMATE":             "hostGeoEstimate",
    "HOST_IN_ASN":                   "hostInASN",
    "ASN_IN_ORG":                    "asnInOrg",
    "FLOW_DST_PORT":                 "flowDstPort",
    "FLOW_QUERIED_DNS":              "flowQueriedDNS",
    "FLOW_TLS_SNI":                  "flowTLSSNI",
    "FLOW_HTTP_HOST":                "flowHTTPHost",
    "PORT_IMPLIED_SERVICE":          "portImpliedService",
    # Inferred
    "INFERRED_HOST_IN_ORG":          "inferredHostInOrg",
    "INFERRED_FLOW_IN_SERVICE":      "inferredFlowInService",
    "INFERRED_HOST_OFFERS_SERVICE":  "inferredHostOffersService",
    "INFERRED_HOST_CONTACTED_SNI":   "inferredHostContactedSNI",
    "INFERRED_HOST_CONTACTED_HTTP_HOST": "inferredHostContactedHTTPHost",
    "INFERRED_FLOW_SNI_EQ_HTTP_HOST":    "inferredFlowSNIEqHTTPHost",
    "INFERRED_HOST_QUERIED_DNSNAME":     "inferredHostQueriedDNSName",
    "INFERRED_DNSNAME_RESOLVES_HOST":    "inferredDNSNameResolvesHost",
    "INFERRED_FLOW_CROSS_BORDER":        "inferredFlowCrossBorder",
}

# Label key → datatype property
LABEL_TO_PROPERTY = {
    "ip":           "ip",
    "bytes":        "bytes",
    "pkts":         "pkts",
    "proto":        "proto",
    "port":         "port",
    "name":         "serviceName",
    "asn":          "asnNumber",
    "org":          "orgName",
    "city":         "city",
    "country":      "country",
    "qname":        "qname",
    "sni":          "sni",
    "host":         "hostHeader",
    "src_ip":       "srcIp",
    "dst_ip":       "dstIp",
    "dst_port":     "dstPort",
}


def _safe_uri(node_id: str) -> str:
    """Encode a graph_ids-style node ID as a safe URI fragment."""
    return quote(node_id, safe="")


def _literal(value: Any) -> str:
    """Format a Python value as a Turtle literal."""
    if isinstance(value, bool):
        return f'"{str(value).lower()}"^^xsd:boolean'
    if isinstance(value, int):
        return f'"{value}"^^xsd:integer'
    if isinstance(value, float):
        return f'"{value}"^^xsd:float'
    # String — escape quotes
    s = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{s}"'


class GraphToRDF:
    """Convert hypergraph nodes and edges to RDF (Turtle or SPARQL UPDATE)."""

    def __init__(self, *, graph_uri: str = RFS_GRAPH):
        self.graph_uri = graph_uri

    def _node_uri(self, node_id: str) -> str:
        return f"<{RFS_DATA}{_safe_uri(node_id)}>"

    def _class_uri(self, kind: str) -> str:
        cls = KIND_TO_CLASS.get(kind, kind.title().replace("_", ""))
        return f"rfs:{cls}"

    def _prop_uri(self, edge_kind: str) -> str:
        prop = EDGE_TO_PROPERTY.get(edge_kind)
        if prop:
            return f"rfs:{prop}"
        # Fallback: camelCase the edge kind
        parts = edge_kind.lower().split("_")
        camel = parts[0] + "".join(p.title() for p in parts[1:])
        return f"rfs:{camel}"

    # ── Turtle generation ───────────────────────────────────────────────────

    def node_to_turtle(self, node: Json) -> str:
        """Convert a single node dict to Turtle triples."""
        nid = node.get("id", "")
        kind = node.get("kind", "unknown")
        uri = self._node_uri(nid)
        lines = [
            f'{uri} a {self._class_uri(kind)} ;',
            f'    rdfs:label {_literal(nid)} .',
        ]
        # Emit label properties
        labels = node.get("labels") or {}
        for key, value in labels.items():
            prop_name = LABEL_TO_PROPERTY.get(key)
            if not prop_name:
                continue
            lines.insert(-1, f'    rfs:{prop_name} {_literal(value)} ;')
        return "\n".join(lines)

    def edge_to_turtle(self, edge: Json) -> str:
        """Convert an edge dict to Turtle triple(s).

        For dyadic edges (2 nodes): single triple  subject → predicate → object.
        For k-ary edges / hyperedges:  subject → predicate → object for each
        pair (nodes[0] → nodes[i]).
        """
        kind = edge.get("kind", "UNKNOWN")
        nodes = edge.get("nodes", [])
        prop = self._prop_uri(kind)
        meta = edge.get("metadata") or {}
        obs_class = meta.get("obs_class", "observed")
        confidence = meta.get("confidence", 1.0)

        lines = []
        if len(nodes) == 2:
            s = self._node_uri(nodes[0])
            o = self._node_uri(nodes[1])
            lines.append(f'{s} {prop} {o} .')
        elif len(nodes) > 2:
            # Hyperedge: star pattern from nodes[0] → each other node
            s = self._node_uri(nodes[0])
            for o_id in nodes[1:]:
                o = self._node_uri(o_id)
                lines.append(f'{s} {prop} {o} .')
        return "\n".join(lines)

    def snapshot_to_turtle(self, nodes: list[Json], edges: list[Json]) -> str:
        """Convert full snapshot to Turtle document."""
        prefixes = [
            f"@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .",
            f"@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
            f"@prefix owl:  <http://www.w3.org/2002/07/owl#> .",
            f"@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .",
            f"@prefix rfs:  <{RFS_NS}> .",
            "",
        ]
        body = []
        for n in nodes:
            body.append(self.node_to_turtle(n))
            body.append("")
        for e in edges:
            t = self.edge_to_turtle(e)
            if t:
                body.append(t)
        return "\n".join(prefixes + body) + "\n"

    # ── SPARQL UPDATE generation ────────────────────────────────────────────

    def snapshot_to_sparql_insert(self, nodes: list[Json], edges: list[Json]) -> str:
        """Generate a SPARQL INSERT DATA statement for the snapshot."""
        triples = []
        for n in nodes:
            triples.append(self.node_to_turtle(n))
        for e in edges:
            t = self.edge_to_turtle(e)
            if t:
                triples.append(t)

        body = "\n".join(triples)
        sparql = (
            f"PREFIX rfs: <{RFS_NS}>\n"
            f"PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n"
            f"PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>\n"
            f"\n"
            f"INSERT DATA {{\n"
            f"  GRAPH <{self.graph_uri}> {{\n"
            f"{body}\n"
            f"  }}\n"
            f"}}"
        )
        return sparql

    # ── Push to Parliament ──────────────────────────────────────────────────

    def push_to_parliament(
        self,
        nodes: list[Json],
        edges: list[Json],
        *,
        endpoint: str = "http://localhost:8089/parliament/sparql",
        batch_size: int = 500,
    ) -> dict:
        """Push a snapshot to Parliament via SPARQL UPDATE.

        Batches triples to avoid overly large payloads.

        Returns: {"ok": bool, "triples_sent": int, "batches": int, "errors": list}
        """
        try:
            import requests
        except ImportError:
            logger.error("requests library required for push_to_parliament")
            return {"ok": False, "triples_sent": 0, "batches": 0, "errors": ["requests not installed"]}

        errors = []
        total_sent = 0
        batch_count = 0

        # Split into batches
        all_items: list[tuple[str, Json]] = []
        for n in nodes:
            all_items.append(("node", n))
        for e in edges:
            all_items.append(("edge", e))

        for i in range(0, len(all_items), batch_size):
            batch = all_items[i:i + batch_size]
            batch_nodes = [item for kind, item in batch if kind == "node"]
            batch_edges = [item for kind, item in batch if kind == "edge"]

            sparql = self.snapshot_to_sparql_insert(batch_nodes, batch_edges)
            try:
                resp = requests.post(
                    endpoint,
                    data=sparql.encode("utf-8"),
                    headers={"Content-Type": "application/sparql-update"},
                    timeout=30,
                )
                if resp.status_code < 300:
                    total_sent += len(batch)
                    batch_count += 1
                else:
                    errors.append(f"batch {batch_count}: HTTP {resp.status_code}: {resp.text[:200]}")
            except Exception as e:
                errors.append(f"batch {batch_count}: {type(e).__name__}: {e}")

        return {
            "ok": len(errors) == 0,
            "triples_sent": total_sent,
            "batches": batch_count,
            "errors": errors,
        }

    # ── Query Parliament ────────────────────────────────────────────────────

    @staticmethod
    def query_parliament(
        sparql_query: str,
        *,
        endpoint: str = "http://localhost:8089/parliament/sparql",
        timeout: int = 30,
    ) -> list[dict]:
        """Execute a SPARQL SELECT query against Parliament and return bindings.

        Returns list of dicts, one per result row.
        """
        try:
            import requests
        except ImportError:
            logger.error("requests library required for query_parliament")
            return []

        resp = requests.post(
            endpoint,
            data=sparql_query.encode("utf-8"),
            headers={
                "Content-Type": "application/sparql-query",
                "Accept": "application/sparql-results+json",
            },
            timeout=timeout,
        )
        if resp.status_code >= 300:
            logger.error(f"SPARQL query failed: {resp.status_code} {resp.text[:200]}")
            return []

        result = resp.json()
        bindings = result.get("results", {}).get("bindings", [])
        rows = []
        for b in bindings:
            row = {}
            for var, val in b.items():
                row[var] = val.get("value", "")
            rows.append(row)
        return rows

    # ── Convenience: query inferred edges ───────────────────────────────────

    def query_inferred_edges(
        self,
        *,
        endpoint: str = "http://localhost:8089/parliament/sparql",
        limit: int = 1000,
    ) -> list[dict]:
        """Query Parliament for all inferred triples (using property chain results).

        Returns list of { subject, predicate, object } dicts.
        """
        sparql = f"""
PREFIX rfs: <{RFS_NS}>
SELECT ?s ?p ?o
WHERE {{
  GRAPH <{self.graph_uri}> {{
    ?s ?p ?o .
    FILTER(STRSTARTS(STR(?p), "{RFS_NS}inferred"))
  }}
}}
LIMIT {limit}
"""
        return self.query_parliament(sparql, endpoint=endpoint)
