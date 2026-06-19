"""
rdf_inferred_to_graphop.py — Convert Parliament SPARQL inference results back to GraphOps.

Queries Parliament for inferred triples (from OWL property chains or
SWRL rules), translates them to INFERRED_* edges with proper metadata,
and returns GraphOps ready for bus().commit().

Usage:
    from rdf_inferred_to_graphop import ParliamentInferenceSync
    sync = ParliamentInferenceSync(endpoint="http://localhost:8089/parliament/sparql")
    new_ops = sync.pull_inferred(existing_edge_ids=set_of_current_ids)
    for op in new_ops:
        bus().commit(entity_id=op.entity_id, ...)
"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Any, Dict, List, Optional, Set
from urllib.parse import unquote

logger = logging.getLogger(__name__)

Json = Dict[str, Any]

# ─────────────────────────────────────────────────────────────────────────────
# Re-use constants from graphop_to_rdf
# ─────────────────────────────────────────────────────────────────────────────

RFS_NS = "http://rfscythe.nerfengine.io/ontology/v0.1#"
RFS_DATA = "http://rfscythe.nerfengine.io/data/"
RFS_GRAPH = "http://rfscythe.nerfengine.io/graph/observed"

# OWL property → edge kind (reverse of graphop_to_rdf.EDGE_TO_PROPERTY)
PROPERTY_TO_EDGE = {
    "inferredHostInOrg":              "INFERRED_HOST_IN_ORG",
    "inferredFlowInService":          "INFERRED_FLOW_IN_SERVICE",
    "inferredHostOffersService":      "INFERRED_HOST_OFFERS_SERVICE",
    "inferredHostContactedSNI":       "INFERRED_HOST_CONTACTED_SNI",
    "inferredHostContactedHTTPHost":  "INFERRED_HOST_CONTACTED_HTTP_HOST",
    "inferredFlowSNIEqHTTPHost":      "INFERRED_FLOW_SNI_EQ_HTTP_HOST",
    "inferredHostQueriedDNSName":     "INFERRED_HOST_QUERIED_DNSNAME",
    "inferredDNSNameResolvesHost":    "INFERRED_DNSNAME_RESOLVES_HOST",
    "inferredFlowCrossBorder":        "INFERRED_FLOW_CROSS_BORDER",
    # Also include observed/implied for completeness
    "sessionObservedHost":            "SESSION_OBSERVED_HOST",
    "sessionObservedFlow":            "SESSION_OBSERVED_FLOW",
    "hostGeoEstimate":                "HOST_GEO_ESTIMATE",
    "hostInASN":                      "HOST_IN_ASN",
    "asnInOrg":                       "ASN_IN_ORG",
    "flowDstPort":                    "FLOW_DST_PORT",
    "flowQueriedDNS":                 "FLOW_QUERIED_DNS",
    "flowTLSSNI":                     "FLOW_TLS_SNI",
    "flowHTTPHost":                   "FLOW_HTTP_HOST",
    "portImpliedService":             "PORT_IMPLIED_SERVICE",
}

# Inferred edge kinds that should be imported from Parliament
INFERRED_PROPERTIES = {k for k, v in PROPERTY_TO_EDGE.items() if v.startswith("INFERRED_")}


def _uri_to_node_id(uri: str) -> str:
    """Convert a Parliament data URI back to the graph_ids-style node ID."""
    if uri.startswith(RFS_DATA):
        return unquote(uri[len(RFS_DATA):])
    # Strip any other prefix — last path segment
    return unquote(uri.rsplit("/", 1)[-1])


def _uri_to_property_name(uri: str) -> str:
    """Extract the property local name from an RFS namespace URI."""
    if "#" in uri:
        return uri.rsplit("#", 1)[-1]
    return uri.rsplit("/", 1)[-1]


def _edge_id_from_triple(prop_name: str, subject_id: str, object_id: str) -> str:
    """Deterministic edge ID derived from the triple."""
    h = hashlib.sha256(f"{prop_name}|{subject_id}|{object_id}".encode()).hexdigest()[:12]
    return f"e:parliament:{prop_name}:{h}"


# ─────────────────────────────────────────────────────────────────────────────
# Import: WriteContext / GraphOp
# ─────────────────────────────────────────────────────────────────────────────

try:
    from writebus import GraphOp
except ImportError:
    # Standalone mode — define minimal GraphOp
    from dataclasses import dataclass

    @dataclass
    class GraphOp:  # type: ignore[no-redef]
        event_type: str
        entity_id: str
        entity_data: Dict[str, Any]


# ─────────────────────────────────────────────────────────────────────────────
# Sync engine
# ─────────────────────────────────────────────────────────────────────────────

class ParliamentInferenceSync:
    """
    Pull inferred triples from Parliament and convert to GraphOps.

    Workflow:
      1. Query Parliament for triples with inferred-* predicates
      2. Filter out edges already present in the hypergraph
      3. Return GraphOps ready for bus().commit()
    """

    def __init__(
        self,
        endpoint: str = "http://localhost:8089/parliament/sparql",
        graph_uri: str = RFS_GRAPH,
    ):
        self.endpoint = endpoint
        self.graph_uri = graph_uri

    def _query(self, sparql: str) -> list[dict]:
        """Execute SPARQL SELECT and return bindings as list of dicts."""
        try:
            import requests
        except ImportError:
            logger.error("requests library required")
            return []

        resp = requests.post(
            self.endpoint,
            data=sparql.encode("utf-8"),
            headers={
                "Content-Type": "application/sparql-query",
                "Accept": "application/sparql-results+json",
            },
            timeout=30,
        )
        if resp.status_code >= 300:
            logger.error(f"SPARQL failed: {resp.status_code} {resp.text[:200]}")
            return []

        result = resp.json()
        bindings = result.get("results", {}).get("bindings", [])
        return [
            {var: val.get("value", "") for var, val in b.items()}
            for b in bindings
        ]

    def pull_inferred(
        self,
        *,
        existing_edge_ids: Set[str] | None = None,
        limit: int = 5000,
    ) -> list[GraphOp]:
        """Query Parliament for inferred triples and return new GraphOps.

        Args:
            existing_edge_ids: Set of edge IDs already in the hypergraph.
                               Edges with matching IDs will be skipped.
            limit: Maximum number of triples to pull per query.

        Returns:
            List of GraphOps for INFERRED_* edges.
        """
        existing = existing_edge_ids or set()

        # Query all inferred predicates
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
        rows = self._query(sparql)
        ops: list[GraphOp] = []
        seen: set[str] = set()

        for row in rows:
            s_uri = row.get("s", "")
            p_uri = row.get("p", "")
            o_uri = row.get("o", "")

            prop_name = _uri_to_property_name(p_uri)
            edge_kind = PROPERTY_TO_EDGE.get(prop_name)
            if not edge_kind or not edge_kind.startswith("INFERRED_"):
                continue

            subject_id = _uri_to_node_id(s_uri)
            object_id = _uri_to_node_id(o_uri)
            edge_id = _edge_id_from_triple(prop_name, subject_id, object_id)

            if edge_id in existing or edge_id in seen:
                continue
            seen.add(edge_id)

            edge_data: Json = {
                "id": edge_id,
                "kind": edge_kind,
                "nodes": [subject_id, object_id],
                "timestamp": time.time(),
                "metadata": {
                    "obs_class": "inferred",
                    "confidence": 0.85,   # default for Parliament; refine per rule
                    "provenance": {
                        "source": "parliament",
                        "rule_id": self._guess_rule_id(edge_kind),
                        "evidence": [],
                        "timestamp": time.time(),
                    },
                },
            }
            ops.append(GraphOp(
                event_type="EDGE_UPDATE",
                entity_id=edge_id,
                entity_data=edge_data,
            ))

        logger.info(f"[Parliament->GraphOp] {len(ops)} new inferred edges (from {len(rows)} triples)")
        return ops

    def pull_all_triples(
        self,
        *,
        limit: int = 10000,
    ) -> list[dict]:
        """Query all triples in the graph (for debugging / export)."""
        sparql = f"""
SELECT ?s ?p ?o
WHERE {{
  GRAPH <{self.graph_uri}> {{
    ?s ?p ?o .
  }}
}}
LIMIT {limit}
"""
        return self._query(sparql)

    @staticmethod
    def _guess_rule_id(edge_kind: str) -> str:
        """Map INFERRED_* edge kind to the corresponding rule ID."""
        mapping = {
            "INFERRED_HOST_IN_ORG":              "R-ORG-001",
            "INFERRED_FLOW_IN_SERVICE":          "R-SVC-001",
            "INFERRED_HOST_OFFERS_SERVICE":      "R-SVC-002",
            "INFERRED_HOST_CONTACTED_SNI":       "R-TLS-001",
            "INFERRED_HOST_CONTACTED_HTTP_HOST": "R-HTTP-001",
            "INFERRED_FLOW_SNI_EQ_HTTP_HOST":    "R-CORR-001",
            "INFERRED_HOST_QUERIED_DNSNAME":     "R-DNS-001",
            "INFERRED_DNSNAME_RESOLVES_HOST":    "R-DNS-002",
            "INFERRED_FLOW_CROSS_BORDER":        "R-GEO-001",
        }
        return mapping.get(edge_kind, "R-UNK-000")

    # ── Convenience: full sync cycle ────────────────────────────────────────

    def sync_to_writebus(
        self,
        *,
        existing_edge_ids: Set[str] | None = None,
        limit: int = 5000,
    ) -> dict:
        """Pull inferred edges from Parliament and commit via WriteBus.

        Returns: {"ok": bool, "edges_synced": int, "errors": list}
        """
        try:
            from writebus import bus, WriteContext
        except ImportError:
            return {"ok": False, "edges_synced": 0, "errors": ["writebus not available"]}

        ops = self.pull_inferred(existing_edge_ids=existing_edge_ids, limit=limit)
        if not ops:
            return {"ok": True, "edges_synced": 0, "errors": []}

        errors = []
        synced = 0
        ctx = WriteContext(
            source="parliament_sync",
            room_name="Global",
        )

        # Batch commit all inferred edges
        try:
            from writebus import bus as get_bus
            wb = get_bus()
            batch_id = f"parliament_sync:{int(time.time()*1000)}"
            wb.commit(
                entity_id=batch_id,
                entity_type="PARLIAMENT_SYNC_BATCH",
                entity_data={"id": batch_id, "type": "PARLIAMENT_SYNC_BATCH",
                             "count": len(ops), "timestamp": time.time()},
                graph_ops=ops,
                ctx=ctx,
                persist=False,
                audit=True,
            )
            synced = len(ops)
        except Exception as e:
            errors.append(f"commit_error: {type(e).__name__}: {e}")

        return {"ok": len(errors) == 0, "edges_synced": synced, "errors": errors}
