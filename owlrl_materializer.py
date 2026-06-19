"""
owlrl_materializer.py — OWL 2 RL materialization using owlrl (Python-side).

Parliament (Raytheon BBN) implements RDFS + selected OWL-Lite but does NOT
reliably execute OWL 2 property-chain axioms (``owl:propertyChainAxiom`` /
``prp-spo2``).  This module bypasses Parliament entirely by:

  1. Converting the hypergraph snapshot (nodes + edges) to an rdflib Graph
  2. Loading the rf_scythe_v0_1.ttl ontology into the same graph
  3. Running ``owlrl.DeductiveClosure(owlrl.OWLRL_Semantics)``
  4. Querying for new inferred triples
  5. Converting those triples back to GraphOps via ``rdf_inferred_to_graphop``

Requirements:
    pip install owlrl rdflib

Usage:
    from owlrl_materializer import OWLRLMaterializer
    mat = OWLRLMaterializer()
    ops = mat.materialize(nodes, edges)
    # → list of GraphOp that can be committed via writebus
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

Json = Dict[str, Any]

# ─────────────────────────────────────────────────────────────────────────────
# Constants — mirror graphop_to_rdf / rdf_inferred_to_graphop
# ─────────────────────────────────────────────────────────────────────────────

RFS_NS = "http://rfscythe.nerfengine.io/ontology/v0.1#"
RFS_DATA = "http://rfscythe.nerfengine.io/data/"

# Inferred OWL properties we care about
INFERRED_PROPERTIES = {
    "inferredHostInOrg",
    "inferredFlowInService",
    "inferredHostOffersService",
    "inferredHostContactedSNI",
    "inferredHostContactedHTTPHost",
    "inferredFlowSNIEqHTTPHost",
    "inferredHostQueriedDNSName",
    "inferredDNSNameResolvesHost",
    "inferredFlowCrossBorder",
}

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
}

# Default ontology path relative to this module
_DEFAULT_ONTO = Path(__file__).parent / "ontology" / "rf_scythe_v0_1.ttl"


def _uri_to_node_id(uri: str) -> str:
    """Convert an RFS data URI back to a graph_ids-style node ID."""
    from urllib.parse import unquote
    if isinstance(uri, str) and uri.startswith(RFS_DATA):
        return unquote(uri[len(RFS_DATA):])
    return unquote(str(uri).rsplit("/", 1)[-1])


def _edge_id(prop_name: str, subj_id: str, obj_id: str) -> str:
    """Deterministic edge ID for an inferred triple."""
    h = hashlib.sha256(f"owlrl|{prop_name}|{subj_id}|{obj_id}".encode()).hexdigest()[:12]
    return f"e:owlrl:{prop_name}:{h}"


# ─────────────────────────────────────────────────────────────────────────────
# GraphOp import — fallback-safe
# ─────────────────────────────────────────────────────────────────────────────

try:
    from writebus import GraphOp
except ImportError:
    from dataclasses import dataclass

    @dataclass
    class GraphOp:  # type: ignore[no-redef]
        event_type: str
        entity_id: str
        entity_data: Dict[str, Any]


# ─────────────────────────────────────────────────────────────────────────────
# Materializer
# ─────────────────────────────────────────────────────────────────────────────

class OWLRLMaterializer:
    """
    Run OWL 2 RL closure over the hypergraph snapshot and return inferred edges.

    This replaces Parliament as the reasoner.  Parliament can still be used as a
    SPARQL store (for queries / dashboards), but materialization is done here.
    """

    def __init__(
        self,
        ontology_path: Optional[str] = None,
    ):
        self.ontology_path = Path(ontology_path) if ontology_path else _DEFAULT_ONTO

    # ─────────────────────────────────────────────────────────────────────
    # Core API
    # ─────────────────────────────────────────────────────────────────────

    def materialize(
        self,
        nodes: List[Json],
        edges: List[Json],
        *,
        existing_edge_ids: Optional[Set[str]] = None,
    ) -> List[Any]:
        """
        Full pipeline:
          snapshot → RDF → OWL-RL closure → extract inferred → GraphOps

        Returns list of GraphOp for new inferred edges.
        """
        try:
            import rdflib
            import owlrl
        except ImportError as exc:
            logger.error(
                "owlrl_materializer requires `owlrl` and `rdflib`: "
                "pip install owlrl rdflib  (%s)", exc,
            )
            return []

        t0 = time.monotonic()
        existing_ids = existing_edge_ids or set()

        # 1. Build RDF graph from snapshot
        g = self._build_graph(nodes, edges)
        triple_count_before = len(g)

        # 2. Load ontology (contains property chain axioms, class hierarchy)
        if self.ontology_path.exists():
            try:
                g.parse(str(self.ontology_path), format="turtle")
                logger.info(
                    "[owlrl] loaded ontology %s (%d triples total)",
                    self.ontology_path.name, len(g),
                )
            except Exception as e:
                logger.warning("[owlrl] failed to load ontology: %s", e)
        else:
            logger.warning("[owlrl] ontology not found: %s", self.ontology_path)

        # 3. Run OWL-RL closure
        try:
            owlrl.DeductiveClosure(owlrl.OWLRL_Semantics).expand(g)
        except Exception as e:
            logger.error("[owlrl] closure failed: %s", e)
            return []

        triple_count_after = len(g)
        new_triples = triple_count_after - triple_count_before
        logger.info(
            "[owlrl] closure complete: %d → %d triples (+%d) in %.2fs",
            triple_count_before, triple_count_after, new_triples,
            time.monotonic() - t0,
        )

        # 4. Extract inferred triples
        ops = self._extract_inferred(g, existing_ids)
        logger.info("[owlrl] extracted %d new inferred GraphOps", len(ops))
        return ops

    # ─────────────────────────────────────────────────────────────────────
    # Build rdflib graph from snapshot
    # ─────────────────────────────────────────────────────────────────────

    def _build_graph(
        self,
        nodes: List[Json],
        edges: List[Json],
    ):
        """Convert nodes + edges to rdflib.Graph using same schema as graphop_to_rdf."""
        try:
            from graphop_to_rdf import GraphToRDF
            converter = GraphToRDF()
            ttl = converter.snapshot_to_turtle(nodes, edges)
        except Exception:
            # Fallback: build manually
            ttl = self._manual_turtle(nodes, edges)

        import rdflib
        g = rdflib.Graph()
        g.parse(data=ttl, format="turtle")
        return g

    def _manual_turtle(self, nodes: List[Json], edges: List[Json]) -> str:
        """Minimal Turtle serialization when graphop_to_rdf is unavailable."""
        from urllib.parse import quote
        lines = [
            f"@prefix rfs: <{RFS_NS}> .",
            f"@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .",
            f"@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
            "",
        ]

        KIND_TO_CLASS = {
            "host": "Host", "flow": "Flow", "geo_point": "GeoPoint",
            "asn": "ASN", "org": "Org", "port_hub": "PortHub",
            "service": "Service", "dns_name": "DNSName", "tls_sni": "TLSSNI",
            "tls_cert": "TLSCert", "http_host": "HTTPHost",
            "pcap_session": "PcapSession", "ja3": "JA3", "ja3s": "JA3S",
        }

        EDGE_TO_PROP = {
            "SESSION_OBSERVED_HOST": "sessionObservedHost",
            "SESSION_OBSERVED_FLOW": "sessionObservedFlow",
            "HOST_GEO_ESTIMATE": "hostGeoEstimate",
            "HOST_IN_ASN": "hostInASN",
            "ASN_IN_ORG": "asnInOrg",
            "FLOW_DST_PORT": "flowDstPort",
            "FLOW_QUERIED_DNS": "flowQueriedDNS",
            "FLOW_TLS_SNI": "flowTLSSNI",
            "FLOW_HTTP_HOST": "flowHTTPHost",
            "PORT_IMPLIED_SERVICE": "portImpliedService",
        }

        for n in nodes:
            nid = n.get("node_id") or n.get("id", "")
            kind = n.get("kind", "")
            uri = f"<{RFS_DATA}{quote(nid, safe='')}>"
            cls = KIND_TO_CLASS.get(kind, kind.title().replace("_", ""))
            lines.append(f"{uri} rdf:type rfs:{cls} .")

            for lk, lv in (n.get("labels") or {}).items():
                safe_val = str(lv).replace('"', '\\"').replace("\n", "\\n")
                lines.append(f'{uri} rfs:{lk} "{safe_val}" .')

        for e in edges:
            src = e.get("source") or e.get("src", "")
            tgt = e.get("target") or e.get("dst", "")
            kind = e.get("kind", "")
            prop = EDGE_TO_PROP.get(kind)
            if not prop or not src or not tgt:
                continue
            s_uri = f"<{RFS_DATA}{quote(src, safe='')}>"
            t_uri = f"<{RFS_DATA}{quote(tgt, safe='')}>"
            lines.append(f"{s_uri} rfs:{prop} {t_uri} .")

        return "\n".join(lines)

    # ─────────────────────────────────────────────────────────────────────
    # Extract inferred triples → GraphOps
    # ─────────────────────────────────────────────────────────────────────

    def _extract_inferred(self, g, existing_ids: Set[str]) -> List[Any]:
        """Query the closed graph for inferred triples and return GraphOps."""
        import rdflib

        ops: List[Any] = []
        rfs = rdflib.Namespace(RFS_NS)

        for prop_local, edge_kind in PROPERTY_TO_EDGE.items():
            prop_uri = rfs[prop_local]
            for subj, _, obj in g.triples((None, prop_uri, None)):
                subj_id = _uri_to_node_id(str(subj))
                obj_id = _uri_to_node_id(str(obj))
                eid = _edge_id(prop_local, subj_id, obj_id)

                if eid in existing_ids:
                    continue

                op = GraphOp(
                    event_type="add_edge",
                    entity_id=eid,
                    entity_data={
                        "id": eid,
                        "source": subj_id,
                        "target": obj_id,
                        "kind": edge_kind,
                        "labels": {
                            "confidence": 0.85,
                            "obs_class": "inferred",
                        },
                        "metadata": {
                            "engine": "owlrl",
                            "reasoner": "OWLRL_Semantics",
                            "inferred_at": time.time(),
                            "ontology": str(self.ontology_path.name),
                        },
                    },
                )
                ops.append(op)
                existing_ids.add(eid)  # dedupe within this batch

        return ops


# ─────────────────────────────────────────────────────────────────────────────
# CLI smoke test
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)

    # Minimal test: 2 nodes, 1 edge — should still work
    test_nodes = [
        {"node_id": "host:test:1", "kind": "host", "labels": {"ip": "10.0.0.1"}},
        {"node_id": "asn:test:1", "kind": "asn", "labels": {"asn": "15169"}},
        {"node_id": "org:test:1", "kind": "org", "labels": {"name": "Google"}},
    ]
    test_edges = [
        {"source": "host:test:1", "target": "asn:test:1", "kind": "HOST_IN_ASN", "id": "e1"},
        {"source": "asn:test:1", "target": "org:test:1", "kind": "ASN_IN_ORG", "id": "e2"},
    ]

    mat = OWLRLMaterializer()
    ops = mat.materialize(test_nodes, test_edges)
    print(f"\n=== OWL-RL Materialization ===")
    print(f"Inferred {len(ops)} new edges:")
    for op in ops:
        d = op.entity_data if hasattr(op, 'entity_data') else op
        print(f"  {d.get('kind', '?')}: {d.get('source', '?')} → {d.get('target', '?')}")
