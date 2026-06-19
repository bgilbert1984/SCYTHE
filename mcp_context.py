"""
mcp_context.py — Model Context Protocol (MCP) v1.0 for RF_SCYTHE.

Builds a deterministic, bounded, machine-readable snapshot of hypergraph
state so LLM agents (TAK-GPT, Gemma, rule agents) can reason without
hallucination.

Pipeline:
    HypergraphEngine
        → MCPBuilder.build()
        → JSON envelope
        → injected into LLM system/user prompt

Usage:
    from mcp_context import MCPBuilder
    mcp = MCPBuilder(hypergraph_engine)
    ctx = mcp.build(session_id="SESSION-123", window_minutes=15)
    # ctx is a dict suitable for JSON serialization
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set

from temporal_inference import compute_temporal_fingerprint, temporal_identity_ledger

logger = logging.getLogger(__name__)

MCP_VERSION = "1.0"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class MCPBuilder:
    """
    Builds an MCP context envelope from a live HypergraphEngine.

    The envelope is a plain dict that can be:
    - serialized to JSON
    - injected into LLM prompts
    - served via /api/mcp/snapshot
    - logged for audit
    """

    def __init__(self, engine: Any):
        """
        Parameters
        ----------
        engine : HypergraphEngine
            The live hypergraph to snapshot.
        """
        self.engine = engine

    # ─────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────

    def build(
        self,
        *,
        session_id: Optional[str] = None,
        operator_id: Optional[str] = None,
        window_minutes: int = 15,
        selected_nodes: Optional[List[str]] = None,
        current_query: Optional[str] = None,
        previous_query: Optional[str] = None,
        mode: str = "analysis",
        max_top_nodes: int = 5,
        max_cells: int = 10,
    ) -> Dict[str, Any]:
        """
        Build the full MCP envelope.

        Returns a dict matching MCP v1.0 schema.
        """
        now = time.time()
        start_ts = now - (window_minutes * 60)

        return {
            "mcp_version": MCP_VERSION,
            "generated_at": now,
            "producer": "rf_scythe_mcp_builder",
            "session": self._build_session(
                session_id=session_id,
                operator_id=operator_id,
                selected_nodes=selected_nodes,
            ),
            "time_window": {
                "mode": "rolling",
                "minutes": window_minutes,
                "start_ts": start_ts,
                "end_ts": now,
            },
            "graph_state": self._build_graph_state(max_top_nodes=max_top_nodes),
            "spatial_state": self._build_spatial_state(max_cells=max_cells),
            "temporal_deltas": self._build_temporal_deltas(now),
            "temporal_fingerprints": self._build_temporal_fingerprints(now),
            "inference_state": self._build_inference_state(),
            "write_summary": self._build_write_summary(),
            "operator_intent": {
                "current_query": current_query or "",
                "previous_query": previous_query or "",
                "mode": mode,
                "expected_output": "text",
            },
            "constraints": {
                "max_nodes": 500,
                "max_edges": 1000,
                "max_inferred_edges": 50,
                "min_confidence": 0.6,
                "allowed_edge_kinds": [
                    "INFERRED_HOST_IN_ORG",
                    "INFERRED_FLOW_IN_SERVICE",
                    "INFERRED_HOST_OFFERS_SERVICE",
                    "INFERRED_HOST_CONTACTED_SNI",
                    "INFERRED_HOST_CONTACTED_HTTP_HOST",
                    "INFERRED_FLOW_SNI_EQ_HTTP_HOST",
                    "INFERRED_HOST_QUERIED_DNSNAME",
                    "INFERRED_DNSNAME_RESOLVES_HOST",
                    "INFERRED_FLOW_CROSS_BORDER",
                    "INFERRED_HOST_ROLE",
                    "INFERRED_FLOW_ON_RIDGE",
                    "INFERRED_HOST_AT_SINGULARITY",
                    "INFERRED_FLOW_ALIGNED_WITH_STREAMLINE",
                ],
            },
        }

    def build_compact(
        self,
        *,
        session_id: Optional[str] = None,
        current_query: Optional[str] = None,
        window_minutes: int = 15,
    ) -> str:
        """
        Build a compact text representation suitable for prompt injection.

        Returns a formatted string (not JSON) for token efficiency.
        """
        full = self.build(
            session_id=session_id,
            current_query=current_query,
            window_minutes=window_minutes,
        )
        gs = full["graph_state"]
        td = full["temporal_deltas"]
        tf = full.get("temporal_fingerprints", {})
        ss = full["spatial_state"]

        lines = [
            f"MCP v{MCP_VERSION} | {full['time_window']['minutes']}min window",
            "",
            "GRAPH STATE:",
        ]

        # Node counts
        nc = gs.get("node_counts", {})
        if nc:
            node_parts = [f"  {k}: {v}" for k, v in sorted(nc.items(), key=lambda x: -x[1]) if v > 0]
            lines.extend(node_parts[:12])
        else:
            lines.append("  (empty graph)")

        # Edge counts
        ec = gs.get("edge_counts", {})
        lines.append(f"  edges: observed={ec.get('observed', 0)}, implied={ec.get('implied', 0)}, inferred={ec.get('inferred', 0)}")

        # Top nodes
        tn = gs.get("top_nodes", {})
        for category, items in tn.items():
            if items:
                top = items[0]
                lines.append(f"  top {category}: {top['id']} ({top['count']})")

        # Temporal deltas
        lines.append("")
        lines.append("ACTIVITY:")
        for window_key in ["last_1_min", "last_5_min", "last_15_min"]:
            d = td.get(window_key, {})
            if d:
                lines.append(f"  {window_key}: +{d.get('new_flows', 0)} flows, +{d.get('new_hosts', 0)} hosts")
        top_temporal = tf.get("top_periodic_entities", [])
        if top_temporal:
            lines.append("  temporal fingerprints:")
            for item in top_temporal[:3]:
                lines.append(
                    "    "
                    f"{item.get('entity_id')}: {item.get('pattern', 'UNKNOWN')} "
                    f"period={item.get('periodicity_s', 0):.1f}s "
                    f"cohesion={item.get('temporal_cohesion', 0):.2f} "
                    f"phase={item.get('temporal_phase', 'unknown')}"
                )

        # Spatial
        cells = ss.get("cells", [])
        if cells:
            lines.append("")
            lines.append("SPATIAL:")
            for c in cells[:3]:
                anchors = c.get("anchors", {})
                anchor_str = ", ".join(f"{k}:{v}" for k, v in anchors.items()) if anchors else "none"
                lines.append(f"  {c['cell_id']}: {c['flow_count']} flows, anchors=[{anchor_str}]")

        # ── Inference delta ──────────────────────────────────────────────
        inf = full.get("inference_state", {})
        inferred_total = inf.get("total_inferred_edges", 0)
        if inferred_total > 0:
            lines.append("")
            lines.append("INFERENCE:")
            lines.append(f"  total inferred edges: {inferred_total}")
            ik = inf.get("inferred_edge_kinds", {})
            if ik:
                top_kinds = sorted(ik.items(), key=lambda x: -x[1])[:8]
                for k, v in top_kinds:
                    lines.append(f"  {k}: {v}")

            last_run = inf.get("last_run", {})
            if last_run:
                lines.append(f"  last run: {last_run.get('edge_count', '?')} edges "
                             f"(A={last_run.get('tier_a_count', 0)} "
                             f"B={last_run.get('tier_b_count', 0)} "
                             f"C={last_run.get('tier_c_count', 0)}) "
                             f"in {last_run.get('duration_seconds', '?')}s")

        # ── Lifted macro-edges (from engine._last_inference_run) ─────────
        lifting = {}
        if hasattr(self.engine, '_last_inference_run'):
            lifting = (self.engine._last_inference_run or {}).get("lifting", {})
        macro_edges = lifting.get("macro_edges", [])
        if macro_edges:
            lines.append("")
            lines.append(f"LIFTED ({lifting.get('raw_count', '?')} edges → "
                         f"{lifting.get('lifted_count', '?')} macro):")
            for me in macro_edges[:8]:
                label = me.get('claim_label', 'signal')
                strength = me.get('claim_strength', 0)
                lines.append(f"  [{label}, {strength:.0%}] {me['description']}")

        # ── Write summary (provenance / epistemic posture) ──────────────
        ws = full.get("write_summary", {})
        if ws.get("total_writes", 0) > 0:
            lines.append("")
            lines.append("WRITE_SUMMARY:")
            lines.append(f"  writes: sensor={ws.get('by_source', {}).get('sensor', 0)}, "
                         f"inference={ws.get('by_source', {}).get('inference', 0)}, "
                         f"analyst={ws.get('by_source', {}).get('analyst', 0)}")
            dom = ws.get("dominant_sources", [])
            if dom:
                lines.append(f"  dominant: {', '.join(f'{s[0]}({s[1]:.0%})' for s in dom[:3])}")
            cov = ws.get("evidence_coverage", 0)
            lines.append(f"  evidence coverage: {cov:.0%} of inferred edges have artifact refs")
            lines.append(f"  trust posture: {ws.get('trust_posture', 'unknown')}")
            stale = ws.get("stale_inference_count", 0)
            if stale > 0:
                lines.append(f"  stale inferences (no new evidence): {stale}")

        return "\n".join(lines)

    # ─────────────────────────────────────────────────────────────────────
    # Internal builders
    # ─────────────────────────────────────────────────────────────────────

    def _build_session(
        self,
        session_id: Optional[str],
        operator_id: Optional[str],
        selected_nodes: Optional[List[str]],
    ) -> Dict[str, Any]:
        # Try to find an active pcap_session if none provided
        if not session_id:
            try:
                sessions = list(self.engine.nodes_by_kind("pcap_session"))
                if sessions:
                    session_id = sessions[-1].id  # most recent
            except Exception:
                pass

        return {
            "pcap_session_id": session_id or "none",
            "operator_id": operator_id or "OPERATOR",
            "active_view": "cesium",
            "selection": {
                "selected_nodes": selected_nodes or [],
            },
        }

    def _build_graph_state(self, max_top_nodes: int = 5) -> Dict[str, Any]:
        """Count nodes by kind, edges by obs_class, and find top contributors."""
        nodes = self.engine.nodes if hasattr(self.engine, 'nodes') else {}
        edges = self.engine.edges if hasattr(self.engine, 'edges') else {}

        # Node counts by kind
        node_counts: Dict[str, int] = defaultdict(int)
        for n in (nodes.values() if isinstance(nodes, dict) else nodes):
            nd = self._safe(n)
            node_counts[nd.get("kind", "unknown")] += 1

        # Edge counts by obs_class
        edge_counts = {"observed": 0, "implied": 0, "inferred": 0}
        for e in (edges.values() if isinstance(edges, dict) else edges):
            ed = self._safe(e)
            obs = (ed.get("metadata") or {}).get("obs_class", "observed")
            if obs in edge_counts:
                edge_counts[obs] += 1
            else:
                edge_counts["observed"] += 1

        # Top nodes by degree (flow connectivity)
        degree = self.engine.degree if hasattr(self.engine, 'degree') else {}
        top_by_degree = sorted(degree.items(), key=lambda x: -x[1])[:max_top_nodes]

        # Top hosts by flow count
        host_flow_counts: Dict[str, int] = defaultdict(int)
        for e in (edges.values() if isinstance(edges, dict) else edges):
            ed = self._safe(e)
            if ed.get("kind") in ("SESSION_OBSERVED_FLOW", "FLOW_DST_PORT"):
                for nid in ed.get("nodes", []):
                    if nid.startswith("host:"):
                        host_flow_counts[nid] += 1

        top_hosts = sorted(host_flow_counts.items(), key=lambda x: -x[1])[:max_top_nodes]

        # Top ASNs by flow
        asn_flow_counts: Dict[str, int] = defaultdict(int)
        for e in (edges.values() if isinstance(edges, dict) else edges):
            ed = self._safe(e)
            if ed.get("kind") == "HOST_IN_ASN":
                for nid in ed.get("nodes", []):
                    if nid.startswith("asn:"):
                        asn_flow_counts[nid] += 1

        top_asns = sorted(asn_flow_counts.items(), key=lambda x: -x[1])[:max_top_nodes]

        return {
            "node_counts": dict(node_counts),
            "edge_counts": edge_counts,
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "top_nodes": {
                "by_degree": [{"id": nid, "count": c} for nid, c in top_by_degree],
                "host_by_flow": [{"id": nid, "count": c} for nid, c in top_hosts],
                "asn_by_flow": [{"id": nid, "count": c} for nid, c in top_asns],
            },
        }

    def _build_spatial_state(self, max_cells: int = 10) -> Dict[str, Any]:
        """Build spatial summary: active geo_cells, anchors, singularities, shells."""
        nodes = self.engine.nodes if hasattr(self.engine, 'nodes') else {}
        edges = self.engine.edges if hasattr(self.engine, 'edges') else {}

        # Collect geo_cells
        cells: Dict[str, Dict[str, Any]] = {}
        for n in (nodes.values() if isinstance(nodes, dict) else nodes):
            nd = self._safe(n)
            if nd.get("kind") == "geo_cell":
                cells[nd["id"]] = {
                    "cell_id": nd["id"],
                    "flow_count": 0,
                    "anchors": {},
                    "singularities": [],
                }

        # Count flows per cell (via edges)
        for e in (edges.values() if isinstance(edges, dict) else edges):
            ed = self._safe(e)
            for nid in ed.get("nodes", []):
                if nid in cells:
                    cells[nid]["flow_count"] += 1

        # Attach anchors
        for n in (nodes.values() if isinstance(nodes, dict) else nodes):
            nd = self._safe(n)
            if nd.get("kind") == "geo_fiber_anchor":
                labels = nd.get("labels") or {}
                cell_id = labels.get("cell", "")
                fiber_kind = labels.get("fiber", "unknown")
                if cell_id in cells:
                    cells[cell_id]["anchors"][fiber_kind] = cells[cell_id]["anchors"].get(fiber_kind, 0) + 1

        # Attach singularities
        for n in (nodes.values() if isinstance(nodes, dict) else nodes):
            nd = self._safe(n)
            if nd.get("kind") == "geo_singularity":
                labels = nd.get("labels") or {}
                cell_id = labels.get("cell", "")
                if cell_id in cells:
                    cells[cell_id]["singularities"].append(nd["id"])

        # Sort by flow_count, take top N
        sorted_cells = sorted(cells.values(), key=lambda c: -c["flow_count"])[:max_cells]

        # Shell summary
        shell_counts = {"surface": 0, "low": 0, "mid": 0, "high": 0}
        for n in (nodes.values() if isinstance(nodes, dict) else nodes):
            nd = self._safe(n)
            kind = nd.get("kind", "")
            if kind in ("geo_cell", "geo_point", "host", "rf"):
                shell_counts["surface"] += 1
            elif kind in ("flow", "geo_streamline", "geo_ridge"):
                shell_counts["low"] += 1
            elif kind in ("service", "port_hub", "dns_name", "tls_sni", "http_host", "asn", "org"):
                shell_counts["mid"] += 1
            elif kind in ("geo_singularity", "geo_patch", "pcap_artifact"):
                shell_counts["high"] += 1

        return {
            "cells": sorted_cells,
            "shells": {
                "surface": {"alt_range_km": "0-2", "active_entities": shell_counts["surface"]},
                "low": {"alt_range_km": "10-15", "active_entities": shell_counts["low"]},
                "mid": {"alt_range_km": "80-120", "active_entities": shell_counts["mid"]},
                "high": {"alt_range_km": "300-600", "active_entities": shell_counts["high"]},
            },
        }

    def _build_temporal_deltas(self, now: float) -> Dict[str, Any]:
        """Count new nodes/edges in recent time windows."""
        nodes = self.engine.nodes if hasattr(self.engine, 'nodes') else {}
        edges = self.engine.edges if hasattr(self.engine, 'edges') else {}

        windows = {
            "last_1_min": 60,
            "last_5_min": 300,
            "last_15_min": 900,
        }

        result = {}
        for key, seconds in windows.items():
            cutoff = now - seconds
            new_flows = 0
            new_hosts = 0
            new_edges = 0

            for n in (nodes.values() if isinstance(nodes, dict) else nodes):
                nd = self._safe(n)
                created = nd.get("created_at") or 0
                if created >= cutoff:
                    kind = nd.get("kind", "")
                    if kind == "flow":
                        new_flows += 1
                    elif kind == "host":
                        new_hosts += 1

            for e in (edges.values() if isinstance(edges, dict) else edges):
                ed = self._safe(e)
                ts = ed.get("timestamp") or 0
                if ts >= cutoff:
                    new_edges += 1

            result[key] = {
                "new_flows": new_flows,
                "new_hosts": new_hosts,
                "new_edges": new_edges,
            }

        return result

    def _build_temporal_fingerprints(self, now: float, max_entities: int = 8) -> Dict[str, Any]:
        nodes = self.engine.nodes if hasattr(self.engine, 'nodes') else {}
        payloads: List[Dict[str, Any]] = []

        for item in temporal_identity_ledger.snapshot(limit=max_entities):
            payloads.append(dict(item))

        seen_ids = {item.get("entity_id") for item in payloads}
        for n in (nodes.values() if isinstance(nodes, dict) else nodes):
            nd = self._safe(n)
            metadata = nd.get("metadata") or {}
            if not metadata or not any(key in metadata for key in ("temporal", "behavior", "session")):
                continue
            entity_id = str(nd.get("id") or "")
            if not entity_id or entity_id in seen_ids:
                continue
            binding_timestamp = _safe_float((metadata.get("temporal") or {}).get("last_seen"), now)
            fingerprint = compute_temporal_fingerprint(
                entity_id=entity_id,
                entity=nd,
                history_timestamps=[],
                binding={},
                binding_timestamp=binding_timestamp,
            )
            if not fingerprint.evidence_present:
                continue
            payloads.append(fingerprint.to_dict())
            seen_ids.add(entity_id)
            if len(payloads) >= max_entities:
                break

        payloads.sort(
            key=lambda item: (
                -_safe_float(item.get("periodicity_confidence"), 0.0),
                -_safe_float(item.get("stability"), 0.0),
                item.get("entity_id", ""),
            )
        )
        top_periodic = payloads[:max_entities]
        pattern_counts: Dict[str, int] = defaultdict(int)
        for item in top_periodic:
            pattern_counts[str(item.get("pattern") or "UNKNOWN")] += 1
        return {
            "top_periodic_entities": top_periodic,
            "pattern_counts": dict(pattern_counts),
        }

    def _build_inference_state(self) -> Dict[str, Any]:
        """Summarize the last inference run (if tracked)."""
        # Check if engine has inference tracking metadata
        meta = {}
        if hasattr(self.engine, '_last_inference_run'):
            meta = self.engine._last_inference_run or {}

        # Count inferred edges as a baseline metric
        edges = self.engine.edges if hasattr(self.engine, 'edges') else {}
        inferred_count = 0
        inferred_kinds: Dict[str, int] = defaultdict(int)
        for e in (edges.values() if isinstance(edges, dict) else edges):
            ed = self._safe(e)
            obs_class = (ed.get("metadata") or {}).get("obs_class", "")
            if obs_class == "inferred":
                inferred_count += 1
                inferred_kinds[ed.get("kind", "unknown")] += 1

        return {
            "total_inferred_edges": inferred_count,
            "inferred_edge_kinds": dict(inferred_kinds),
            "last_run": meta.get("last_run", {}),
        }

    def _build_write_summary(self) -> Dict[str, Any]:
        """Scan edge metadata for provenance and compute an epistemic posture.

        Examines provenance_write, provenance_rule, and provenance fields
        across ALL edges to classify write sources and evidence coverage.

        Returns a dict with:
          - total_writes: int
          - by_source: {sensor: N, inference: N, analyst: N, unknown: N}
          - dominant_sources: [(source_label, fraction), ...]
          - evidence_coverage: float (fraction of inferred edges with evidence_refs)
          - trust_posture: "sensor-heavy" | "inference-heavy" | "balanced" | "sparse"
          - stale_inference_count: int (inferred edges with no evidence_refs)
          - recent_operators: [operator_id, ...]
          - room_scope: [room_name, ...]
        """
        edges = self.engine.edges if hasattr(self.engine, 'edges') else {}
        nodes = self.engine.nodes if hasattr(self.engine, 'nodes') else {}

        # Source classification buckets
        SENSOR_SOURCES = {'pcap_ingest', 'rf_ingest', 'ais_ingest', 'sensor',
                          'ndpi', 'nmap', 'tcpdump', 'zeek', 'suricata'}
        INFERENCE_SOURCES = {'tak-ml', 'gemma', 'gemma3', 'owlrl', 'rule_engine',
                            'inference', 'lpi_detector', 'lpi_detector_v1'}
        ANALYST_SOURCES = {'manual_ui', 'operator', 'human', 'analyst', 'tak_gpt'}

        by_source = {'sensor': 0, 'inference': 0, 'analyst': 0, 'unknown': 0}
        total = 0
        inferred_with_evidence = 0
        inferred_without_evidence = 0
        operators_seen: Set[str] = set()
        rooms_seen: Set[str] = set()

        def _classify_source(source_str: str) -> str:
            s = (source_str or '').lower().strip()
            if s in SENSOR_SOURCES or 'pcap' in s or 'ingest' in s:
                return 'sensor'
            if s in INFERENCE_SOURCES or 'gemma' in s or 'inference' in s or 'ml' in s:
                return 'inference'
            if s in ANALYST_SOURCES or 'manual' in s or 'operator' in s:
                return 'analyst'
            return 'unknown'

        # Scan edges for provenance metadata
        for e in (edges.values() if isinstance(edges, dict) else edges):
            ed = self._safe(e)
            meta = ed.get('metadata') or ed.get('meta') or {}
            total += 1

            # Determine source from provenance hierarchy
            prov_write = meta.get('provenance_write') or {}
            prov_rule = meta.get('provenance_rule') or {}
            prov_merged = meta.get('provenance') or {}

            source = (
                prov_write.get('source')
                or prov_rule.get('source')
                or prov_merged.get('source')
                or meta.get('obs_class', '')
            )
            category = _classify_source(source)
            by_source[category] += 1

            # Evidence coverage for inferred edges
            obs_class = meta.get('obs_class', '')
            if obs_class == 'inferred' or (ed.get('kind', '') or '').startswith('INFERRED_'):
                evidence_refs = (
                    prov_rule.get('evidence_refs')
                    or prov_merged.get('evidence_refs')
                    or prov_rule.get('evidence')
                    or prov_merged.get('evidence')
                    or []
                )
                if evidence_refs and len(evidence_refs) > 0:
                    inferred_with_evidence += 1
                else:
                    inferred_without_evidence += 1

            # Track operators and rooms
            op_id = (
                prov_write.get('operator_id')
                or prov_merged.get('operator_id')
                or prov_merged.get('write_operator')
            )
            if op_id:
                operators_seen.add(op_id)

        # Also scan nodes for provenance (some nodes carry write metadata)
        for n in (nodes.values() if isinstance(nodes, dict) else nodes):
            nd = self._safe(n)
            meta = nd.get('metadata') or nd.get('meta') or {}
            prov = meta.get('provenance_write') or meta.get('provenance') or {}
            source = prov.get('source', '')
            if source:
                total += 1
                by_source[_classify_source(source)] += 1

        # Compute dominant sources
        dominant_sources = []
        if total > 0:
            for cat in ['sensor', 'inference', 'analyst', 'unknown']:
                if by_source[cat] > 0:
                    dominant_sources.append((cat, by_source[cat] / total))
            dominant_sources.sort(key=lambda x: -x[1])

        # Evidence coverage ratio
        total_inferred = inferred_with_evidence + inferred_without_evidence
        evidence_coverage = (inferred_with_evidence / total_inferred) if total_inferred > 0 else 0.0

        # Trust posture
        sensor_frac = by_source['sensor'] / total if total > 0 else 0
        inference_frac = by_source['inference'] / total if total > 0 else 0
        if total < 10:
            trust_posture = 'sparse'
        elif sensor_frac >= 0.5:
            trust_posture = 'sensor-heavy'
        elif inference_frac >= 0.5:
            trust_posture = 'inference-heavy'
        elif abs(sensor_frac - inference_frac) < 0.15:
            trust_posture = 'balanced'
        else:
            trust_posture = 'mixed'

        return {
            'total_writes': total,
            'by_source': by_source,
            'dominant_sources': dominant_sources,
            'evidence_coverage': round(evidence_coverage, 3),
            'trust_posture': trust_posture,
            'stale_inference_count': inferred_without_evidence,
            'recent_operators': list(operators_seen)[:10],
            'room_scope': list(rooms_seen)[:5],
        }

    # ─────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def _safe(obj: Any) -> Dict[str, Any]:
        """Convert node/edge to dict safely."""
        if isinstance(obj, dict):
            return obj
        if hasattr(obj, "to_dict"):
            return obj.to_dict()
        return {"id": str(obj)}
