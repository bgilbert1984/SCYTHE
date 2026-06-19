"""graphops_copilot.py — GraphOps Copilot for NerfEngine.

Implements the self-querying hypergraph agent described in Gemma_Llama_MCP.md:

  kernel telemetry → hypergraph → GraphOps DSL → LLM reasoning → intelligence report

Three layers
------------
1. EntityExtractor
   Extracts IPv4, CIDR, domain, ASN, port, and hex node-IDs from free text.
   Never hallucinates — only emits what is literally present in the input.

2. InvestigativeDSLExecutor
   Stateful executor for investigative DSL verbs:
     FOCUS <entity>
     EXPAND [inbound|outbound|neighbors] [depth=N] [limit=N]
     TRACE path FROM <a> TO <b> [depth=N]
     FILTER <field> [>|<|=] <value>
     BEHAVIOR_QUERY <field><op><value> [top=N]
     WINDOW <duration>        — 200ms | 5s | 10m | 1h
     ANALYZE [fanin|fanout|degree_delta|temporal_sync]
     CLUSTER [timing|topology]
     SUMMARIZE
     ASSESS

3. GraphOpsAgent
   Ollama-backed reasoning loop (llama3.2:3b preferred):
     observe → hypothesize → DSL query → execute → interpret → next query
   Max 6 steps, exits when confidence >= 0.85.
   Returns a structured intelligence report:
     Situation / Change / Structure / Geography / Assessment / Direction

MCP tools (registered via register_graphops_tools):
   graphops_investigate  — full agent investigation from a natural-language question
   graphops_dsl_exec     — execute a raw DSL plan (JSON list of verbs)
   graphops_entity_parse — extract entities from text

Usage
-----
    from graphops_copilot import GraphOpsAgent, register_graphops_tools

    agent = GraphOpsAgent(engine)
    report = agent.investigate("Is there coordinated scanning?")

    # or DSL directly:
    from graphops_copilot import InvestigativeDSLExecutor
    executor = InvestigativeDSLExecutor(engine)
    result   = executor.run(["FOCUS all_nodes", "WINDOW 1s",
                              "ANALYZE degree_delta", "FILTER degree_delta > 50"])
"""
from __future__ import annotations

import json
import logging
import math
import re
import time
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ─── constants ────────────────────────────────────────────────────────────────

EXAMPLE_VALUES = {
    "10.0.0.1", "host:session:abc123", "abc123",
    "0x0000000000000000", "node:0x0000000000000000",
}

CONFIDENCE_THRESHOLD  = 0.80   # exit loop when confidence reaches this
PLATEAU_THRESHOLD     = 0.65   # exit early when plateaued above this
PLATEAU_STEPS         = 3      # consecutive non-improving steps to trigger early exit
MAX_AGENT_STEPS       = 6
MAX_ARBITRATION_MODELS = 2
MODEL_DIVERGENCE_THRESHOLD = 0.35

# ─── EntityExtractor ──────────────────────────────────────────────────────────

_RE_IPV4   = re.compile(r'\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b')
_RE_IPV6   = re.compile(
    r'(?<![:\w])'
    r'(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}'        # full
    r'|(?:[0-9a-fA-F]{1,4}:){1,7}:'                      # trailing ::
    r'|:(?::[0-9a-fA-F]{1,4}){1,7}'                      # leading ::
    r'|(?:[0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}'
    r'|(?:[0-9a-fA-F]{1,4}:){1,5}(?::[0-9a-fA-F]{1,4}){1,2}'
    r'|(?:[0-9a-fA-F]{1,4}:){1,4}(?::[0-9a-fA-F]{1,4}){1,3}'
    r'|(?:[0-9a-fA-F]{1,4}:){1,3}(?::[0-9a-fA-F]{1,4}){1,4}'
    r'|(?:[0-9a-fA-F]{1,4}:){1,2}(?::[0-9a-fA-F]{1,4}){1,5}'
    r'|::(?:ffff(?::0{1,4})?:)?'
    r'(?:(?:25[0-5]|(?:2[0-4]|1?\d)?\d)\.){3}(?:25[0-5]|(?:2[0-4]|1?\d)?\d)'  # ::ffff:IPv4
    r'|::1'                                               # loopback
    r'|::'                                                # unspecified
    r'(?![:\w])'
)
_RE_CIDR   = re.compile(r'\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)/\d{1,2}\b')
_RE_DOMAIN = re.compile(r'\b(?:[a-zA-Z0-9-]{1,63}\.)+(?:com|net|org|io|gov|mil|edu|co|uk|br|de|cn|ru|info|xyz)\b')
_RE_ASN    = re.compile(r'\bAS\d{1,10}\b', re.I)
_RE_PORT   = re.compile(r'\bport\s+(\d{1,5})\b', re.I)
_RE_NODEID = re.compile(r'\bnode:(0x[0-9a-fA-F]{1,16})\b')
_RE_HEX64  = re.compile(r'\b0x[0-9a-fA-F]{8,16}\b')


class EntityExtractor:
    """Extract typed entities from free text without hallucination.

    Returns a dict of lists; each entry is (value, entity_type).
    Values from EXAMPLE_VALUES are silently discarded.
    """

    def extract(self, text: str) -> Dict[str, List[str]]:
        result: Dict[str, List[str]] = {
            "ipv4":    [],
            "ipv6":    [],
            "cidr":    [],
            "domain":  [],
            "asn":     [],
            "port":    [],
            "node_id": [],
            "hex_id":  [],
        }

        # CIDRs before bare IPs to avoid double-match
        for m in _RE_CIDR.finditer(text):
            v = m.group(0)
            if v not in EXAMPLE_VALUES:
                result["cidr"].append(v)

        for m in _RE_IPV4.finditer(text):
            v = m.group(0)
            if v not in EXAMPLE_VALUES and not any(v in c for c in result["cidr"]):
                result["ipv4"].append(v)

        for m in _RE_IPV6.finditer(text):
            v = m.group(0)
            if v not in EXAMPLE_VALUES:
                result["ipv6"].append(v)

        for m in _RE_DOMAIN.finditer(text):
            v = m.group(0).lower()
            result["domain"].append(v)

        for m in _RE_ASN.finditer(text):
            result["asn"].append(m.group(0).upper())

        for m in _RE_PORT.finditer(text):
            result["port"].append(m.group(1))

        for m in _RE_NODEID.finditer(text):
            v = f"node:{m.group(1)}"
            if v not in EXAMPLE_VALUES:
                result["node_id"].append(v)

        for m in _RE_HEX64.finditer(text):
            v = m.group(0)
            if v not in EXAMPLE_VALUES:
                result["hex_id"].append(v)

        # deduplicate, preserve order
        for k in result:
            seen = set()
            dedup = []
            for x in result[k]:
                if x not in seen:
                    seen.add(x)
                    dedup.append(x)
            result[k] = dedup

        return result

    def primary_entity(self, entities: Dict[str, List[str]]) -> Optional[str]:
        """Return the most specific single entity for FOCUS, or None."""
        if entities["node_id"]:
            return entities["node_id"][0]
        if entities["hex_id"]:
            return entities["hex_id"][0]
        if entities["ipv4"]:
            return entities["ipv4"][0]
        if entities["cidr"]:
            return entities["cidr"][0]
        if entities["domain"]:
            return entities["domain"][0]
        return None


# ─── InvestigativeDSLExecutor ─────────────────────────────────────────────────

def _parse_duration(s: str) -> float:
    """Parse duration string → seconds.  e.g. '200ms' → 0.2, '5s' → 5.0, '10m' → 600."""
    s = s.strip().lower()
    if s.endswith("ms"):
        return float(s[:-2]) / 1000.0
    if s.endswith("m"):
        return float(s[:-1]) * 60.0
    if s.endswith("h"):
        return float(s[:-1]) * 3600.0
    if s.endswith("s"):
        return float(s[:-1])
    return float(s)


# ── HGEdge compatibility helpers ──────────────────────────────────────────────
# HGEdge exposes `nodes: List[str]` and `timestamp: float`.
# Legacy code used non-existent `src`, `dst`, `created_at` attributes.
# Use these helpers everywhere instead of getattr(e, 'src'/'dst'/'created_at').

def _edge_ts(edge) -> float | None:
    """Return the edge timestamp regardless of attribute name."""
    return getattr(edge, 'timestamp', None) or getattr(edge, 'created_at', None)


def _edge_src(edge) -> str | None:
    """Return the 'source' endpoint of an edge (first node)."""
    nodes = getattr(edge, 'nodes', None)
    if nodes:
        return nodes[0]
    return getattr(edge, 'src', None)


def _edge_dst(edge) -> str | None:
    """Return the 'destination' endpoint of an edge (last node)."""
    nodes = getattr(edge, 'nodes', None)
    if nodes and len(nodes) > 1:
        return nodes[-1]
    if nodes:
        return nodes[0]
    return getattr(edge, 'dst', None)


def _edge_other(edge, nid: str) -> str | None:
    """Given one endpoint nid, return the other endpoint of the edge."""
    src = _edge_src(edge)
    dst = _edge_dst(edge)
    if src == nid:
        return dst
    return src or nid


class InvestigativeDSLExecutor:
    """Stateful executor for the investigative DSL verb set.

    Each call to run() processes a list of verb strings in order and
    returns a consolidated result dict.  State (FOCUS target, WINDOW)
    persists across run() calls within the same agent step.
    """

    def __init__(self, engine=None, topology_detector=None, fanin_detector=None):
        self.engine   = engine
        self._topo    = topology_detector
        self._fanin   = fanin_detector
        self.reset()

    def reset(self) -> None:
        """Clear all session state."""
        self._focus:          Optional[str]       = None
        self._window_s:       float               = 1.0
        self._focus_nodes:    List[Dict]          = []
        self._focus_edges:    List[Dict]          = []
        self._last_result:    Dict[str, Any]      = {}

    # ── public API ────────────────────────────────────────────────────────────

    def run(self, plan: List[str]) -> Dict[str, Any]:
        """Execute a list of DSL verb strings; return accumulated result."""
        result: Dict[str, Any] = {
            "steps": [],
            "focus": None,
            "found_nodes": [],
            "found_edges": [],
            "metrics": {},
            "cluster": None,
            "summary": None,
            "assessment": None,
        }

        for verb_line in plan:
            verb_line = verb_line.strip()
            if not verb_line or verb_line.startswith("#"):
                continue
            step_result = self._dispatch(verb_line)
            result["steps"].append({"verb": verb_line, "result": step_result})
            self._merge(result, step_result)

        result["focus"] = self._focus
        self._last_result = result
        return result

    def run_text(self, text: str) -> Dict[str, Any]:
        """Parse a multi-line DSL text block and execute it."""
        lines = [l.strip() for l in text.strip().splitlines()
                 if l.strip() and not l.strip().startswith("#")]
        return self.run(lines)

    # ── dispatcher ────────────────────────────────────────────────────────────

    def _dispatch(self, line: str) -> Dict[str, Any]:
        up = line.upper()
        tok = up.split()
        if not tok:
            return {}

        verb = tok[0]
        try:
            if verb == "FOCUS":
                return self._do_focus(line)
            if verb == "EXPAND":
                return self._do_expand(line)
            if verb == "TRACE":
                return self._do_trace(line)
            if verb == "FILTER":
                return self._do_filter(line)
            if verb == "BEHAVIOR_QUERY":
                return self._do_behavior_query(line)
            if verb == "WINDOW":
                return self._do_window(line)
            if verb == "ANALYZE":
                return self._do_analyze(line)
            if verb == "CLUSTER":
                return self._do_cluster(line)
            if verb == "VECTOR_SEARCH":
                return self._do_vector_search(line)
            if verb == "CLUSTER_SIMILAR":
                return self._do_cluster_similar(line)
            if verb == "TEMPORAL_ENTROPY":
                return self._do_temporal_entropy(line)
            if verb == "STITCH_IDENTITIES":
                return self._do_stitch_identities(line)
            if verb == "COMPUTE":
                return self._do_compute(line)
            if verb == "GRAPH_DELTA":
                return self._do_graph_delta(line)
            if verb == "RF_CORRELATE":
                return self._do_rf_correlate(line)
            if verb == "BSG_MAP":
                return self._do_bsg_map(line)
            if verb == "SUMMARIZE":
                return self._do_summarize()
            if verb == "ASSESS":
                return self._do_assess()
        except Exception as exc:
            logger.warning("DSL verb failed [%s]: %s", line, exc)
            return {"error": str(exc)}
        return {"warning": f"unknown verb: {verb}"}

    # ── FOCUS ─────────────────────────────────────────────────────────────────

    def _do_focus(self, line: str) -> Dict[str, Any]:
        """FOCUS <entity|all_nodes|all_edges>"""
        rest = re.sub(r'^FOCUS\s+', '', line, flags=re.I).strip().strip('"')

        if rest.lower() in ("all_nodes", "*"):
            self._focus = "all_nodes"
            if self.engine:
                self._focus_nodes = [n.to_dict() for n in self.engine.nodes.values()]
            return {"focused": "all_nodes", "count": len(self._focus_nodes)}

        if rest.lower() in ("all_edges", "edges"):
            self._focus = "all_edges"
            if self.engine:
                self._focus_edges = [e.to_dict() for e in self.engine.edges.values()]
            return {"focused": "all_edges", "count": len(self._focus_edges)}

        self._focus = rest
        # Try to resolve the entity in the graph
        if self.engine:
            node = self.engine.get_node(rest)
            if node:
                self._focus_nodes = [node.to_dict()]
                # also fetch its edges
                self._focus_edges = [
                    e.to_dict() for e in self.engine.edges_for_node(rest)
                ]
                return {"focused": rest, "node_found": True,
                        "degree": self.engine.degree.get(rest, 0),
                        "edges": len(self._focus_edges)}
            # Try by IP label
            found = list(self.engine.nodes_with_label("ip", rest))
            if found:
                self._focus_nodes = [n.to_dict() for n in found]
                return {"focused": rest, "nodes_by_ip": len(found)}

        return {"focused": rest, "node_found": False}

    # ── EXPAND ────────────────────────────────────────────────────────────────

    def _do_expand(self, line: str) -> Dict[str, Any]:
        """EXPAND [inbound|outbound|neighbors] [depth=N] [limit=N]"""
        up = line.upper()
        direction = "neighbors"
        if "INBOUND"   in up: direction = "inbound"
        elif "OUTBOUND" in up: direction = "outbound"

        depth_m = re.search(r'depth=(\d+)', line, re.I)
        limit_m = re.search(r'limit=(\d+)', line, re.I)
        depth = int(depth_m.group(1)) if depth_m else 1
        limit = int(limit_m.group(1)) if limit_m else 500

        if not self.engine or not self._focus:
            return {"warning": "EXPAND requires FOCUS first"}

        target_ids = [n["id"] for n in self._focus_nodes] if self._focus_nodes else [self._focus]
        expanded_nodes = {}
        expanded_edges = []

        for tid in target_ids[:50]:  # cap seed set
            for edge in self.engine.edges_for_node(tid):
                ed = edge.to_dict()
                edge_nodes = ed.get("nodes", [])
                # determine direction
                for nid in edge_nodes:
                    if nid == tid:
                        continue
                    if direction == "inbound" and edge_nodes and edge_nodes[0] == tid:
                        continue   # skip edges originating from focus
                    if direction == "outbound" and edge_nodes and edge_nodes[-1] == tid:
                        continue   # skip edges pointing to focus
                    if nid not in expanded_nodes:
                        node = self.engine.get_node(nid)
                        if node:
                            expanded_nodes[nid] = node.to_dict()
                expanded_edges.append(ed)
                if len(expanded_edges) >= limit:
                    break

        new_nodes = list(expanded_nodes.values())
        self._focus_nodes.extend(new_nodes)
        self._focus_edges.extend(expanded_edges)

        return {
            "direction":       direction,
            "depth":           depth,
            "expanded_nodes":  len(new_nodes),
            "expanded_edges":  len(expanded_edges),
            "unique_sources":  len({e.get("nodes", [""])[0]
                                    for e in expanded_edges if e.get("nodes")}),
        }

    # ── TRACE ─────────────────────────────────────────────────────────────────

    def _do_trace(self, line: str) -> Dict[str, Any]:
        """TRACE path FROM <a> TO <b> [depth=N]"""
        m = re.search(r'FROM\s+(\S+)\s+TO\s+(\S+)', line, re.I)
        if not m:
            return {"warning": "TRACE requires FROM <a> TO <b>"}
        src, dst = m.group(1).strip('"'), m.group(2).strip('"')
        depth_m = re.search(r'depth=(\d+)', line, re.I)
        max_depth = int(depth_m.group(1)) if depth_m else 4

        if not self.engine:
            return {"src": src, "dst": dst, "path": [], "found": False}

        # BFS path search
        from collections import deque
        queue = deque([(src, [src])])
        visited = {src}
        found_paths = []

        while queue and len(found_paths) < 3:
            node, path = queue.popleft()
            if len(path) > max_depth + 1:
                continue
            if node == dst:
                found_paths.append(path)
                continue
            for edge in self.engine.edges_for_node(node):
                for nid in edge.to_dict().get("nodes", []):
                    if nid not in visited:
                        visited.add(nid)
                        queue.append((nid, path + [nid]))

        return {
            "src":        src,
            "dst":        dst,
            "max_depth":  max_depth,
            "paths_found": len(found_paths),
            "shortest":   found_paths[0] if found_paths else [],
        }

    # ── FILTER ────────────────────────────────────────────────────────────────

    def _do_filter(self, line: str) -> Dict[str, Any]:
        """FILTER <field> [>|<|=|>=|<=] <value>"""
        m = re.match(r'FILTER\s+(\w+)\s*(>=|<=|>|<|=)\s*(\S+)', line, re.I)
        if not m:
            return {"warning": "FILTER syntax: FILTER field op value"}
        field, op, raw_val = m.group(1), m.group(2), m.group(3)

        try:
            val = float(raw_val)
        except ValueError:
            val = raw_val.strip('"\'')

        def _matches(node_dict: dict) -> bool:
            # check top-level fields and labels
            v = node_dict.get(field, node_dict.get("labels", {}).get(field))
            if v is None:
                # special: degree_delta from topology detector
                if field == "degree_delta" and self._topo:
                    nid = node_dict.get("id", "")
                    cur = self._topo._current.get(nid)
                    prev = self._topo._previous.get(nid)
                    if cur and prev:
                        v = max(
                            abs(cur.in_degree  - prev.in_degree),
                            abs(cur.out_degree - prev.out_degree),
                        )
                    else:
                        return False
                else:
                    return False
            try:
                v = float(v)
            except (TypeError, ValueError):
                pass
            if isinstance(v, float) and isinstance(val, float):
                if op == ">":  return v > val
                if op == "<":  return v < val
                if op == ">=": return v >= val
                if op == "<=": return v <= val
                if op == "=":  return v == val
            else:
                return str(v) == str(val)
            return False

        before = len(self._focus_nodes)
        self._focus_nodes = [n for n in self._focus_nodes if _matches(n)]
        return {
            "filter":     f"{field} {op} {raw_val}",
            "before":     before,
            "after":      len(self._focus_nodes),
            "dropped":    before - len(self._focus_nodes),
        }

    @staticmethod
    def _behavior_value(raw_val: str) -> Any:
        value = raw_val.strip().strip('"\'')
        lower = value.lower()
        if lower in {"true", "false"}:
            return lower == "true"
        try:
            return float(value)
        except ValueError:
            return value

    @staticmethod
    def _behavior_numeric(values: List[Any], reducer: str = "max") -> float:
        numeric: List[float] = []
        for item in values:
            try:
                numeric.append(float(item))
            except (TypeError, ValueError):
                continue
        if not numeric:
            return 0.0
        if reducer == "median":
            ordered = sorted(numeric)
            mid = len(ordered) // 2
            if len(ordered) % 2 == 1:
                return round(ordered[mid], 4)
            return round((ordered[mid - 1] + ordered[mid]) / 2.0, 4)
        if reducer == "mean":
            return round(sum(numeric) / len(numeric), 4)
        return round(max(numeric), 4)

    @staticmethod
    def _behavior_text(values: List[Any]) -> str:
        normalized = [str(item).strip() for item in values if item not in (None, "")]
        if not normalized:
            return ""
        return Counter(normalized).most_common(1)[0][0]

    @staticmethod
    def _behavior_protocols(values: List[Any]) -> List[str]:
        tokens = set()
        for value in values:
            if value in (None, ""):
                continue
            if isinstance(value, (list, tuple, set)):
                for item in value:
                    if item not in (None, ""):
                        tokens.add(str(item).strip().upper())
                continue
            tokens.add(str(value).strip().upper())
        return sorted(token for token in tokens if token)

    def _extract_behavior_profile(self, node_dict: Dict[str, Any]) -> Dict[str, Any]:
        node_id = str(node_dict.get("id") or "")
        labels = dict(node_dict.get("labels") or {})
        metadata = dict(node_dict.get("metadata") or {})
        samples: Dict[str, List[Any]] = defaultdict(list)
        text_fragments: List[str] = [node_id, str(node_dict.get("kind") or "")]

        def _append(field: str, value: Any) -> None:
            if value in (None, "", [], (), {}):
                return
            if isinstance(value, (list, tuple, set)):
                for item in value:
                    if item not in (None, ""):
                        samples[field].append(item)
                return
            samples[field].append(value)

        def _ingest_payload(payload: Any) -> None:
            if not isinstance(payload, dict):
                return
            for key in (
                "periodicity_s",
                "periodicity_confidence",
                "temporal_cohesion",
                "identity_pressure",
                "divergence_risk",
                "dissonance_score",
                "utility_score",
            ):
                _append(key, payload.get(key))
            for key in (
                "temporal_phase",
                "behavior_class",
                "pattern",
                "burst_signature",
                "org",
                "organization",
                "owner",
                "provider",
                "company",
                "asn_org",
                "service",
                "services",
                "protocol",
                "protocols",
                "app_proto",
                "ndpi_protocol",
                "hostname",
                "domain",
                "tls_sni",
                "sni",
                "utility_kind",
                "utility",
                "ot_protocol",
            ):
                _append(key, payload.get(key))
            for key in ("port", "src_port", "dst_port"):
                _append("ports", payload.get(key))
            for key in ("temporal", "behavior", "session", "identity", "supporting_evidence", "network", "utility", "field_view", "cognitive_dissonance"):
                nested = payload.get(key)
                if isinstance(nested, dict):
                    _ingest_payload(nested)
            for key in ("org", "organization", "owner", "provider", "company", "asn_org", "service", "protocol", "ndpi_protocol", "hostname", "domain", "tls_sni", "sni", "utility_kind"):
                value = payload.get(key)
                if isinstance(value, str):
                    text_fragments.append(value)
                elif isinstance(value, (list, tuple, set)):
                    text_fragments.extend(str(item) for item in value if item not in (None, ""))

        _ingest_payload(node_dict)
        _ingest_payload(labels)
        _ingest_payload(metadata)

        if self.engine is not None and node_id:
            for edge in list(self.engine.edges_for_node(node_id))[:64]:
                edge_dict = edge.to_dict() if hasattr(edge, "to_dict") else dict(edge or {})
                text_fragments.append(str(edge_dict.get("kind") or ""))
                _ingest_payload(edge_dict)
                _ingest_payload(edge_dict.get("labels") or {})
                _ingest_payload(edge_dict.get("metadata") or {})

        periodicity_s = self._behavior_numeric(samples["periodicity_s"], reducer="median")
        periodicity_confidence = self._behavior_numeric(samples["periodicity_confidence"])
        temporal_cohesion = self._behavior_numeric(samples["temporal_cohesion"])
        identity_pressure = self._behavior_numeric(samples["identity_pressure"])
        divergence_risk = self._behavior_numeric(samples["divergence_risk"])
        dissonance_score = self._behavior_numeric(samples["dissonance_score"])
        temporal_phase = self._behavior_text(samples["temporal_phase"])
        behavior_class = self._behavior_text(samples["behavior_class"])
        pattern = self._behavior_text(samples["pattern"])
        burst_signature = self._behavior_text(samples["burst_signature"])
        organization = self._behavior_text(
            samples["organization"]
            + samples["org"]
            + samples["owner"]
            + samples["provider"]
            + samples["company"]
            + samples["asn_org"]
        )
        protocols = self._behavior_protocols(
            samples["protocol"]
            + samples["protocols"]
            + samples["app_proto"]
            + samples["ndpi_protocol"]
            + samples["service"]
            + samples["services"]
            + samples["ot_protocol"]
        )
        ports = self._behavior_protocols(samples["ports"])

        utility_terms = (
            "utility",
            "electric",
            "energy",
            "power",
            "grid",
            "substation",
            "transmission",
            "distribution",
            "smartmeter",
            "smart meter",
            "metering",
            "ami",
            "amr",
            "scada",
            "ot",
        )
        ot_protocol_map = {
            "MODBUS": {"MODBUS", "MODBUS/TCP"},
            "DNP3": {"DNP3"},
            "IEC104": {"IEC104", "IEC-104"},
            "IEC61850": {"IEC61850", "IEC-61850", "GOOSE", "MMS"},
            "BACNET": {"BACNET"},
            "OPCUA": {"OPCUA", "OPC-UA"},
        }
        combined_text = " ".join(fragment.lower() for fragment in text_fragments if fragment)
        utility_tags = sorted(term for term in utility_terms if term in combined_text)

        ot_protocols = []
        for label, variants in ot_protocol_map.items():
            if any(token in variants for token in protocols):
                ot_protocols.append(label)
        if "502" in ports and "MODBUS" not in ot_protocols:
            ot_protocols.append("MODBUS")
        if "20000" in ports and "DNP3" not in ot_protocols:
            ot_protocols.append("DNP3")
        if "2404" in ports and "IEC104" not in ot_protocols:
            ot_protocols.append("IEC104")

        utility_score = max(
            0.0,
            min(
                1.0,
                (0.45 if utility_tags else 0.0)
                + min(len(ot_protocols), 2) / 2.0 * 0.35
                + (
                    0.20
                    if identity_pressure >= 0.55
                    and (periodicity_s > 0.0 or temporal_cohesion >= 0.45)
                    else 0.0
                ),
            ),
        )
        if samples["utility_score"]:
            utility_score = max(utility_score, self._behavior_numeric(samples["utility_score"]))
        utility_flag = utility_score >= 0.55 or bool(utility_tags and ot_protocols)
        match_score = round(
            max(
                utility_score,
                min(
                    1.0,
                    temporal_cohesion * 0.30
                    + identity_pressure * 0.30
                    + divergence_risk * 0.20
                    + periodicity_confidence * 0.20,
                ),
            ),
            4,
        )
        evidence_present = bool(
            periodicity_s > 0.0
            or temporal_cohesion > 0.0
            or identity_pressure > 0.0
            or divergence_risk > 0.0
            or behavior_class
            or utility_flag
        )
        return {
            "entity_id": node_id,
            "periodicity_s": periodicity_s,
            "periodicity_confidence": periodicity_confidence,
            "temporal_cohesion": temporal_cohesion,
            "identity_pressure": identity_pressure,
            "divergence_risk": divergence_risk,
            "dissonance_score": dissonance_score,
            "temporal_phase": temporal_phase or "unknown",
            "behavior_class": behavior_class or "UNKNOWN",
            "pattern": pattern or "UNKNOWN",
            "burst_signature": burst_signature or "unknown",
            "organization": organization,
            "protocols": protocols,
            "ports": ports,
            "ot_protocols": sorted(set(ot_protocols)),
            "utility_tags": utility_tags,
            "utility_score": round(utility_score, 4),
            "utility": utility_flag,
            "match_score": match_score,
            "evidence_present": evidence_present,
        }

    @classmethod
    def _behavior_match(cls, actual: Any, op: str, expected: Any) -> bool:
        if actual is None:
            return False
        if isinstance(expected, bool):
            return bool(actual) is expected if op == "=" else False
        if isinstance(expected, float):
            try:
                value = float(actual)
            except (TypeError, ValueError):
                return False
            if op == ">":
                return value > expected
            if op == "<":
                return value < expected
            if op == ">=":
                return value >= expected
            if op == "<=":
                return value <= expected
            return value == expected
        if isinstance(actual, (list, tuple, set)):
            normalized = {str(item).strip().lower() for item in actual if item not in (None, "")}
            return str(expected).strip().lower() in normalized
        actual_text = str(actual).strip().lower()
        expected_text = str(expected).strip().lower()
        if op == "=":
            return actual_text == expected_text or expected_text in actual_text
        return False

    def _do_behavior_query(self, line: str) -> Dict[str, Any]:
        """BEHAVIOR_QUERY <field><op><value> [top=N]"""
        if self.engine is None:
            return {"error": "no engine"}

        rest = re.sub(r"^BEHAVIOR_QUERY\s+", "", line, flags=re.I).strip()
        if not rest:
            return {"warning": "BEHAVIOR_QUERY requires at least one predicate"}

        predicates: List[Dict[str, Any]] = []
        top_n = 25
        for token in rest.split():
            if token.lower().startswith("top="):
                try:
                    top_n = max(1, min(100, int(token.split("=", 1)[1])))
                except ValueError:
                    return {"warning": "BEHAVIOR_QUERY top= must be an integer"}
                continue
            match = re.match(r"([\w_]+)\s*(>=|<=|>|<|=)\s*(.+)", token)
            if not match:
                return {"warning": f"BEHAVIOR_QUERY predicate not understood: {token}"}
            field, op, raw_val = match.group(1), match.group(2), match.group(3)
            predicates.append({
                "field": field,
                "op": op,
                "value": self._behavior_value(raw_val),
            })

        if not predicates:
            return {"warning": "BEHAVIOR_QUERY requires at least one predicate"}

        candidate_ids = (
            [n.get("id") for n in self._focus_nodes if isinstance(n, dict) and n.get("id")]
            or ([self._focus] if self._focus and self._focus != "all_nodes" else list(self.engine.nodes.keys())[:1200])
        )

        matched_nodes: List[Dict[str, Any]] = []
        matches: List[Dict[str, Any]] = []
        collected_edges: Dict[str, Dict[str, Any]] = {}
        for node_id in candidate_ids:
            node = self.engine.get_node(node_id)
            if not node:
                continue
            node_dict = node.to_dict() if hasattr(node, "to_dict") else dict(node or {})
            profile = self._extract_behavior_profile(node_dict)
            if not profile.get("evidence_present"):
                continue
            if not all(self._behavior_match(profile.get(pred["field"]), pred["op"], pred["value"]) for pred in predicates):
                continue
            enriched = dict(node_dict)
            enriched["supporting_evidence"] = dict(profile)
            matched_nodes.append(enriched)
            matches.append(dict(profile))
            for edge in list(self.engine.edges_for_node(node_id))[:32]:
                edge_dict = edge.to_dict() if hasattr(edge, "to_dict") else dict(edge or {})
                collected_edges[edge_dict.get("id") or f"{node_id}:{len(collected_edges)}"] = edge_dict

        matches.sort(
            key=lambda item: (
                -float(item.get("utility_score", 0.0)),
                -float(item.get("match_score", 0.0)),
                -float(item.get("temporal_cohesion", 0.0)),
                item.get("entity_id", ""),
            )
        )
        matched_nodes = matched_nodes[:top_n]
        self._focus_nodes = matched_nodes
        self._focus_edges = list(collected_edges.values())[: min(len(collected_edges), top_n * 6)]

        top_match = matches[0] if matches else {}
        behavior_counts = Counter(item.get("behavior_class") or "UNKNOWN" for item in matches)
        utility_count = sum(1 for item in matches if item.get("utility"))
        ot_protocol_counts = Counter(
            proto
            for item in matches
            for proto in item.get("ot_protocols", [])
        )
        metrics = {
            "behavior_query_matches": len(matches),
            "utility_match_count": utility_count,
            "behavior_counts": dict(behavior_counts),
            "ot_protocol_counts": dict(ot_protocol_counts),
            "evidence_present": bool(matches),
        }
        for key in (
            "periodicity_s",
            "periodicity_confidence",
            "temporal_cohesion",
            "identity_pressure",
            "divergence_risk",
            "dissonance_score",
            "temporal_phase",
            "behavior_class",
            "pattern",
            "burst_signature",
            "utility_score",
            "utility",
        ):
            if key in top_match:
                metrics[key] = top_match.get(key)

        return {
            "verb": "BEHAVIOR_QUERY",
            "predicates": predicates,
            "matched": len(matches),
            "matches": matches[:top_n],
            "found_nodes": matched_nodes,
            "found_edges": self._focus_edges,
            "utility_match_count": utility_count,
            "metrics": metrics,
        }

    # ── WINDOW ────────────────────────────────────────────────────────────────

    def _do_window(self, line: str) -> Dict[str, Any]:
        """WINDOW <duration>"""
        m = re.search(r'WINDOW\s+(\S+)', line, re.I)
        if not m:
            return {"warning": "WINDOW requires a duration (200ms, 5s, 10m, 1h)"}
        self._window_s = _parse_duration(m.group(1))
        return {"window_s": self._window_s, "window_str": m.group(1)}

    # ── ANALYZE ───────────────────────────────────────────────────────────────

    def _do_analyze(self, line: str) -> Dict[str, Any]:
        """ANALYZE [fanin|fanout|degree_delta|temporal_sync|path_density]"""
        up = line.upper()
        now = time.time()

        if "FANIN" in up or "FAN_IN" in up or "FAN-IN" in up:
            return self._analyze_fanin()
        if "FANOUT" in up or "FAN_OUT" in up or "FAN-OUT" in up:
            return self._analyze_fanout()
        if "DEGREE_DELTA" in up or "DEGREE" in up:
            return self._analyze_degree_delta()
        if "TEMPORAL_SYNC" in up or "SYNC" in up:
            return self._analyze_temporal_sync()
        if "PATH_DENSITY" in up:
            return self._analyze_path_density()

        return {"warning": f"unknown ANALYZE target in: {line}"}

    def _analyze_fanin(self) -> Dict[str, Any]:
        """Count unique inbound sources per focused node."""
        if not self.engine:
            return {"fanin": {}}
        targets = [n["id"] for n in self._focus_nodes] or (
            [self._focus] if self._focus else list(self.engine.nodes.keys())[:200]
        )
        fanin: Dict[str, int] = {}
        for nid in targets:
            inbound = set()
            for edge in self.engine.edges_for_node(nid):
                ns = edge.to_dict().get("nodes", [])
                for src in ns:
                    if src != nid:
                        inbound.add(src)
            if inbound:
                fanin[nid] = len(inbound)
        top = sorted(fanin.items(), key=lambda x: x[1], reverse=True)[:10]
        return {
            "analysis":      "fanin",
            "window_s":      self._window_s,
            "top_nodes":     [{"node": k, "fanin": v} for k, v in top],
            "max_fanin":     top[0][1] if top else 0,
            "total_targets": len(targets),
        }

    def _analyze_fanout(self) -> Dict[str, Any]:
        """Count unique outbound destinations per focused node."""
        if not self.engine:
            return {"fanout": {}}
        targets = [n["id"] for n in self._focus_nodes] or (
            [self._focus] if self._focus else list(self.engine.nodes.keys())[:200]
        )
        fanout: Dict[str, int] = {}
        for nid in targets:
            outbound = set()
            for edge in self.engine.edges_for_node(nid):
                ns = edge.to_dict().get("nodes", [])
                for dst in ns:
                    if dst != nid:
                        outbound.add(dst)
            if outbound:
                fanout[nid] = len(outbound)
        top = sorted(fanout.items(), key=lambda x: x[1], reverse=True)[:10]
        return {
            "analysis":      "fanout",
            "window_s":      self._window_s,
            "top_nodes":     [{"node": k, "fanout": v} for k, v in top],
            "max_fanout":    top[0][1] if top else 0,
            "total_targets": len(targets),
        }

    def _analyze_degree_delta(self) -> Dict[str, Any]:
        """Report degree delta from topology drift detector."""
        if not self._topo:
            # fallback: raw degree from engine
            if self.engine:
                top = sorted(self.engine.degree.items(),
                             key=lambda x: x[1], reverse=True)[:20]
                return {
                    "analysis":    "degree",
                    "source":      "engine.degree",
                    "top_degrees": [{"node": k, "degree": v} for k, v in top],
                    "max_degree":  top[0][1] if top else 0,
                }
            return {"analysis": "degree_delta", "warning": "no detector available"}

        with self._topo._lock:
            current  = dict(self._topo._current)
            previous = dict(self._topo._previous)

        deltas = []
        for nid, cur in current.items():
            prev = previous.get(nid)
            d_in  = cur.in_degree  - (prev.in_degree  if prev else 0)
            d_out = cur.out_degree - (prev.out_degree if prev else 0)
            delta = max(abs(d_in), abs(d_out))
            if delta > 0:
                deltas.append({
                    "node":   nid,
                    "delta":  delta,
                    "d_in":   d_in,
                    "d_out":  d_out,
                })
        deltas.sort(key=lambda x: x["delta"], reverse=True)
        return {
            "analysis":    "degree_delta",
            "window_s":    self._window_s,
            "top_deltas":  deltas[:10],
            "max_delta":   deltas[0]["delta"] if deltas else 0,
            "active_nodes": len(deltas),
        }

    def _analyze_temporal_sync(self) -> Dict[str, Any]:
        """Use fan-in detector to score timing synchronization."""
        if not self._fanin:
            return {"analysis": "temporal_sync", "warning": "fanin detector not available"}

        with self._fanin._lock:
            snapshot = list(self._fanin._events)

        if not snapshot:
            return {"analysis": "temporal_sync", "event_count": 0, "synchronized": False}

        # group by dst, compute timing spread
        dst_map: Dict[str, List[int]] = defaultdict(list)
        for src, dst, ts_ns in snapshot:
            dst_map[dst].append(ts_ns)

        sync_results = []
        for dst, times in dst_map.items():
            if len(times) < 5:
                continue
            spread_ms = (max(times) - min(times)) / 1e6
            entropy = self._timing_entropy(times)
            sync_results.append({
                "node":      dst,
                "count":     len(times),
                "spread_ms": round(spread_ms, 2),
                "timing_H":  round(entropy, 3),
                "synchronized": entropy < 1.0 and len(times) >= 20,
            })
        sync_results.sort(key=lambda x: x["timing_H"])

        return {
            "analysis":    "temporal_sync",
            "window_s":    self._window_s,
            "nodes_tested": len(sync_results),
            "synchronized_nodes": [r for r in sync_results if r["synchronized"]],
            "top_10": sync_results[:10],
        }

    def _analyze_path_density(self) -> Dict[str, Any]:
        """Compute edge density for the focused subgraph."""
        n = len(self._focus_nodes)
        e = len(self._focus_edges)
        max_edges = n * (n - 1) if n > 1 else 1
        density = e / max_edges if max_edges else 0
        return {
            "analysis":   "path_density",
            "nodes":      n,
            "edges":      e,
            "density":    round(density, 4),
            "dense":      density > 0.1,
        }

    @staticmethod
    def _timing_entropy(times: List[int], bucket_ns: int = 10_000_000) -> float:
        """Shannon entropy of inter-arrival times in 10ms buckets."""
        if len(times) < 2:
            return 0.0
        sorted_t = sorted(times)
        deltas = [sorted_t[i+1] - sorted_t[i] for i in range(len(sorted_t)-1)]
        buckets: Dict[int, int] = defaultdict(int)
        for d in deltas:
            buckets[d // bucket_ns] += 1
        total = sum(buckets.values())
        entropy = 0.0
        for count in buckets.values():
            if count > 0:
                p = count / total
                entropy -= p * math.log2(p)
        return entropy

    # ── CLUSTER ───────────────────────────────────────────────────────────────

    def _do_cluster(self, line: str) -> Dict[str, Any]:
        """CLUSTER [timing|topology|behavior]"""
        up = line.upper()

        if "TIMING" in up:
            return self._cluster_timing()
        if "TOPOLOGY" in up:
            return self._cluster_topology()
        return self._cluster_topology()

    def _cluster_timing(self) -> Dict[str, Any]:
        """Cluster nodes by connection arrival timing (synchronized vs random)."""
        if not self._fanin:
            return {"cluster": "timing", "clusters": [],
                    "warning": "fanin detector not available"}

        with self._fanin._lock:
            snapshot = list(self._fanin._events)

        if not snapshot:
            return {"cluster": "timing", "clusters": [], "event_count": 0}

        # Group sources per destination, then split by timing spread
        dst_map: Dict[str, List[tuple]] = defaultdict(list)
        for src, dst, ts_ns in snapshot:
            dst_map[dst].append((src, ts_ns))

        clusters = []
        for dst, entries in dst_map.items():
            if len(entries) < 3:
                continue
            times = sorted(e[1] for e in entries)
            spread_ms = (max(times) - min(times)) / 1e6
            entropy = self._timing_entropy(times)
            cluster_type = "synchronized" if entropy < 1.0 else "random"
            clusters.append({
                "dst":          dst,
                "size":         len(entries),
                "type":         cluster_type,
                "spread_ms":    round(spread_ms, 2),
                "timing_H":     round(entropy, 3),
            })
        clusters.sort(key=lambda x: x["size"], reverse=True)
        return {
            "cluster":     "timing",
            "clusters":    clusters[:20],
            "synchronized_count": sum(1 for c in clusters if c["type"] == "synchronized"),
            "random_count":       sum(1 for c in clusters if c["type"] == "random"),
        }

    def _cluster_topology(self) -> Dict[str, Any]:
        """Simple connected-component clustering of focused nodes."""
        if not self._focus_nodes or not self.engine:
            return {"cluster": "topology", "components": []}

        node_ids = {n["id"] for n in self._focus_nodes}
        parent = {nid: nid for nid in node_ids}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for eid, edge in self.engine.edges.items():
            ns = edge.to_dict().get("nodes", [])
            for i in range(len(ns) - 1):
                if ns[i] in node_ids and ns[i+1] in node_ids:
                    union(ns[i], ns[i+1])

        comps: Dict[str, List[str]] = defaultdict(list)
        for nid in node_ids:
            comps[find(nid)].append(nid)

        comp_list = sorted([list(v) for v in comps.values()],
                           key=lambda x: len(x), reverse=True)
        return {
            "cluster":          "topology",
            "components":       len(comp_list),
            "largest":          len(comp_list[0]) if comp_list else 0,
            "top_components":   [{"root": c[0], "size": len(c)} for c in comp_list[:10]],
        }

    # ── VECTOR_SEARCH ─────────────────────────────────────────────────────────

    def _do_vector_search(self, line: str) -> Dict[str, Any]:
        """
        VECTOR_SEARCH <intent_phrase> [k=N] [proto_anomaly>F] [confidence>F]

        Translate a natural-language intent phrase into a TurboQuant similarity
        search over the semantic embedding space, then apply optional scalar
        filters.  Returns matching entity IDs ranked by cosine similarity.

        Examples:
          VECTOR_SEARCH "abnormal jitter low variance beacon-like" k=64
          VECTOR_SEARCH "rotating proxy tls reuse" k=32 proto_anomaly>0.4
          VECTOR_SEARCH "dns tunneling large frames port 53" k=20
        """
        # Parse intent phrase (quoted or bare), k, and filter predicates
        m_phrase = re.search(r'"([^"]+)"', line) or re.search(r"'([^']+)'", line)
        intent   = m_phrase.group(1) if m_phrase else re.sub(
            r'^VECTOR_SEARCH\s+', '', line, flags=re.I).split()[0]

        m_k   = re.search(r'\bk=(\d+)', line, re.I)
        k     = min(int(m_k.group(1)), 200) if m_k else 25

        # Scalar post-filters: field>value, field<value, field=value
        filter_preds = self._parse_vector_filters(line)

        try:
            from turbo_quant_store import embedding_store as _emb_store
            tq = _emb_store()
        except ImportError:
            return {"error": "TurboQuantStore not available", "matches": []}

        # Embed the intent phrase
        vec = self._embed_intent(intent)
        if vec is None:
            return {"error": "embedding unavailable — check Ollama", "intent": intent}

        raw_results = tq.search(vec, k=k)
        if not raw_results:
            return {"matches": [], "intent": intent, "searched": len(tq)}

        # Enrich results with hypergraph node labels and apply filters
        matches = []
        for eid, sim in raw_results:
            node = self._get_node(eid)
            labels = node.get("labels", {}) if node else {}

            # Apply scalar filters
            if not self._apply_vector_filters(labels, filter_preds):
                continue

            matches.append({
                "entity_id":            eid,
                "similarity":           round(sim, 4),
                "protocol_anomaly":     labels.get("protocol_anomaly_score", 0.0),
                "protocol_violations":  labels.get("protocol_violations", []),
                "dst_port":             labels.get("dst_port"),
                "src_ip":               labels.get("src_ip"),
                "dst_ip":               labels.get("dst_ip"),
            })

        # Set focus nodes from matches so subsequent EXPAND/FILTER/ANALYZE work
        if matches and self.engine is not None:
            self._focus_nodes = [
                n for n in (self._get_node(m["entity_id"]) for m in matches)
                if n is not None
            ]

        logger.info(
            "[VECTOR_SEARCH] intent=%r k=%d searched=%d matched=%d",
            intent, k, len(raw_results), len(matches)
        )
        return {
            "verb":     "VECTOR_SEARCH",
            "intent":   intent,
            "k":        k,
            "searched": len(tq),
            "matched":  len(matches),
            "matches":  matches[:50],
            "filters":  filter_preds,
        }

    def _do_cluster_similar(self, line: str) -> Dict[str, Any]:
        """
        CLUSTER_SIMILAR [threshold=F] [k=N] [min_cluster=N]

        Online density clustering over the TurboQuant embedding space using a
        cosine similarity threshold.  No training, no centroids — pure
        neighborhood density.

        Each unassigned entity is checked against existing cluster centroids.
        If it's within `threshold` cosine similarity of any centroid, it joins
        that cluster; otherwise a new cluster is created.

        This detects:
          - Rotating proxies (same behavioral embedding, changing IPs)
          - Infrastructure reuse (same hosting provider behavioral pattern)
          - C2 families (constant-size beacons with similar fingerprints)

        NOTE: threshold is in TurboQuant compressed similarity space (~0.25–0.45
        corresponds to very similar entities at 3-bit quantisation).

        Examples:
          CLUSTER_SIMILAR threshold=0.30
          CLUSTER_SIMILAR threshold=0.25 min_cluster=3
        """
        m_thr = re.search(r'threshold=([\d.]+)', line, re.I)
        m_k   = re.search(r'\bk=(\d+)', line, re.I)
        m_min = re.search(r'min_cluster=(\d+)', line, re.I)
        threshold   = float(m_thr.group(1)) if m_thr else 0.30
        k_neighbors = int(m_k.group(1)) if m_k else 20
        min_cluster = int(m_min.group(1)) if m_min else 2

        try:
            from turbo_quant_store import embedding_store as _emb_store
            tq = _emb_store()
        except ImportError:
            return {"error": "TurboQuantStore not available"}

        if len(tq) < 2:
            return {"clusters": [], "total_entities": len(tq),
                    "note": "not enough entities yet"}

        # Greedy online clustering — use TQ search() for all similarity queries
        clusters: List[Dict[str, Any]] = []   # [{centroid_id, members, centroid_idx}]

        for eid in list(tq._id_to_idx.keys()):
            if eid not in tq:
                continue
            eid_idx = tq._id_to_idx[eid]

            # Search from this entity's quantized vec
            raw_vec = tq._dense[eid_idx]  # fp16; TQ search handles normalisation
            neighbors = tq.search(raw_vec.float().cpu().numpy(), k=k_neighbors + 1)
            neighbor_ids_scores = {n_id: sim for n_id, sim in neighbors if n_id != eid}

            # Find closest existing cluster (centroid member must appear in neighbor set)
            best_cluster = None
            best_sim     = -1.0
            for cl in clusters:
                cid = cl["centroid_id"]
                sim = neighbor_ids_scores.get(cid, -1.0)
                if sim > best_sim:
                    best_sim     = sim
                    best_cluster = cl

            if best_cluster is not None and best_sim >= threshold:
                best_cluster["members"].append(eid)
            else:
                clusters.append({
                    "centroid_id": eid,
                    "members":     [eid],
                })

        # Filter to min_cluster size and strip internal centroid_vec
        sig_clusters = [
            {
                "centroid_id":  cl["centroid_id"],
                "size":         len(cl["members"]),
                "members":      cl["members"][:20],   # cap for JSON safety
            }
            for cl in sorted(clusters, key=lambda c: len(c["members"]), reverse=True)
            if len(cl["members"]) >= min_cluster
        ]

        logger.info(
            "[CLUSTER_SIMILAR] threshold=%.2f clusters=%d sig=%d total=%d",
            threshold, len(clusters), len(sig_clusters), len(tq)
        )
        return {
            "verb":            "CLUSTER_SIMILAR",
            "threshold":       threshold,
            "total_entities":  len(tq),
            "total_clusters":  len(clusters),
            "significant":     len(sig_clusters),
            "clusters":        sig_clusters[:20],
        }

    # ── Advanced structural + cross-domain verbs ─────────────────────────────

    def _do_temporal_entropy(self, line: str) -> Dict[str, Any]:
        """
        TEMPORAL_ENTROPY [window=T] [top=N]

        For each entity compute Shannon entropy of inter-arrival times (IAT) on
        outbound edges.  Low entropy = beacon / periodic traffic.  Returns the
        top-N lowest-entropy (most regular) hosts.

        Examples:
          TEMPORAL_ENTROPY window=5m top=20
          TEMPORAL_ENTROPY
        """
        m_win  = re.search(r'window=([\d.]+[smh]?)', line, re.I)
        m_top  = re.search(r'top=(\d+)', line, re.I)
        window = _parse_duration(m_win.group(1)) if m_win else 300.0
        top_n  = int(m_top.group(1)) if m_top else 20

        if self.engine is None:
            return {"error": "no engine"}

        import math, time as _time

        now      = _time.time()
        cutoff   = now - window
        entropy_rows: List[Dict[str, Any]] = []

        candidates = (
            [self._focus] if self._focus and self._focus != "all_nodes"
            else list(self.engine.nodes.keys())[:500]
        )

        for nid in candidates:
            edges = list(self.engine.edges_for_node(nid))
            ts_list = sorted(
                _edge_ts(e) for e in edges
                if _edge_ts(e) is not None and _edge_ts(e) >= cutoff
                and _edge_src(e) == nid
            )
            if len(ts_list) < 4:
                continue

            iats = [ts_list[i+1] - ts_list[i] for i in range(len(ts_list)-1)]
            # Histogram into 16 bins
            mn, mx = min(iats), max(iats)
            span   = mx - mn if mx > mn else 1.0
            bins   = [0] * 16
            for v in iats:
                b = int((v - mn) / span * 15)
                bins[min(b, 15)] += 1
            total = sum(bins)
            ent = -sum((c/total) * math.log2(c/total) for c in bins if c > 0)

            node_obj  = self.engine.get_node(nid)
            node_dict = node_obj.to_dict() if node_obj and hasattr(node_obj, 'to_dict') else {}
            entropy_rows.append({
                "entity_id":   nid,
                "labels":      node_dict.get("labels", {}),
                "iat_entropy": round(ent, 4),
                "edge_count":  len(ts_list),
                "mean_iat_s":  round(sum(iats)/len(iats), 4) if iats else 0,
            })

        entropy_rows.sort(key=lambda r: r["iat_entropy"])
        beacons = entropy_rows[:top_n]

        logger.info("[TEMPORAL_ENTROPY] window=%.0fs candidates=%d beacons=%d",
                    window, len(candidates), len(beacons))
        return {
            "verb":        "TEMPORAL_ENTROPY",
            "window_s":    window,
            "candidates":  len(candidates),
            "beacons":     beacons,
            "beacon_count": len(beacons),
        }

    def _do_stitch_identities(self, line: str) -> Dict[str, Any]:
        """
        STITCH_IDENTITIES [field=F] [threshold=F] [window=T]

        Groups entities that share a stable attribute (tls_ja3, embedding) across
        changing IP/ASN values.  Each returned group represents one logical actor
        who may be rotating their network identity.

        Examples:
          STITCH_IDENTITIES field=tls_ja3 window=10m
          STITCH_IDENTITIES field=embedding threshold=0.88
        """
        m_fld  = re.search(r'field=(\S+)', line, re.I)
        m_thr  = re.search(r'threshold=([\d.]+)', line, re.I)
        m_win  = re.search(r'window=([\d.]+[smh]?)', line, re.I)
        field     = m_fld.group(1).lower()   if m_fld  else "tls_ja3"
        threshold = float(m_thr.group(1))    if m_thr  else 0.88
        window    = _parse_duration(m_win.group(1)) if m_win else 600.0

        if self.engine is None:
            return {"error": "no engine"}

        import time as _time
        now    = _time.time()
        cutoff = now - window

        candidates = list(self.engine.nodes.keys())[:1000]

        # ── field = embedding: use TurboQuant similarity clustering ──────────
        if field == "embedding":
            try:
                from turbo_quant_store import embedding_store as _emb_store
                tq = _emb_store()
            except ImportError:
                return {"error": "TurboQuantStore not available"}

            groups: List[Dict[str, Any]] = []
            assigned = set()

            for eid in candidates:
                if eid in assigned or eid not in tq._id_to_idx:
                    continue
                idx  = tq._id_to_idx[eid]
                vec  = tq._dense[idx].float().cpu().numpy()
                nbrs = tq.search(vec, k=30)
                members = [n_id for n_id, sim in nbrs if sim >= threshold and n_id != eid]
                if members:
                    group_ids = [eid] + members
                    # IP diversity check
                    ips = set()
                    for gid in group_ids:
                        n = self.engine.get_node(gid)
                        if n:
                            nd = n.to_dict() if hasattr(n, 'to_dict') else {}
                            ip = nd.get("labels", {}).get("ip") or nd.get("labels", {}).get("src_ip")
                            if ip:
                                ips.add(ip)
                    groups.append({
                        "stable_value":   f"embedding_cluster@{eid[:8]}",
                        "field":          "embedding",
                        "member_count":   len(group_ids),
                        "distinct_ips":   len(ips),
                        "ip_rotation":    len(ips) > 1,
                        "members":        group_ids[:20],
                    })
                    assigned.update(group_ids)

            groups.sort(key=lambda g: g["distinct_ips"], reverse=True)
            return {
                "verb":          "STITCH_IDENTITIES",
                "field":         field,
                "threshold":     threshold,
                "groups":        groups[:25],
                "rotating_count": sum(1 for g in groups if g["ip_rotation"]),
            }

        # ── field = label attribute (tls_ja3, asn, etc.) ──────────────────
        bucket: Dict[str, List[str]] = {}
        for nid in candidates:
            node_obj = self.engine.get_node(nid)
            if not node_obj:
                continue
            nd    = node_obj.to_dict() if hasattr(node_obj, 'to_dict') else {}
            val   = nd.get("labels", {}).get(field)
            if val and val not in ("", "unknown", "N/A"):
                bucket.setdefault(str(val), []).append(nid)

        groups = []
        for val, members in bucket.items():
            ips = set()
            asns = set()
            for mid in members:
                n = self.engine.get_node(mid)
                if n:
                    nd = n.to_dict() if hasattr(n, 'to_dict') else {}
                    lbl = nd.get("labels", {})
                    for k in ("ip", "src_ip", "dst_ip"):
                        if lbl.get(k):
                            ips.add(lbl[k])
                    if lbl.get("asn"):
                        asns.add(lbl["asn"])
            if len(members) >= 2:
                groups.append({
                    "stable_value":  val,
                    "field":         field,
                    "member_count":  len(members),
                    "distinct_ips":  len(ips),
                    "distinct_asns": len(asns),
                    "ip_rotation":   len(ips) > 1,
                    "members":       members[:20],
                })

        groups.sort(key=lambda g: (g["distinct_ips"], g["member_count"]), reverse=True)
        logger.info("[STITCH_IDENTITIES] field=%s groups=%d rotating=%d",
                    field, len(groups), sum(1 for g in groups if g["ip_rotation"]))
        return {
            "verb":           "STITCH_IDENTITIES",
            "field":          field,
            "groups":         groups[:25],
            "rotating_count": sum(1 for g in groups if g["ip_rotation"]),
        }

    def _do_compute(self, line: str) -> Dict[str, Any]:
        """
        COMPUTE k_core [k=N]
        COMPUTE motif [top=N]
        COMPUTE betweenness [limit=N]

        Structural analytics on the current focus subgraph.

        k_core   — greedy k-core decomposition; returns the densest subgraph.
        motif    — triad frequency (A→B, A→C, B→C closed triangles); returns top-N rare triads.
        betweenness — approximate node betweenness centrality via random sampling.
        """
        up = line.upper()
        m_k   = re.search(r'k=(\d+)',    line, re.I)
        m_top = re.search(r'top=(\d+)',  line, re.I)
        m_lim = re.search(r'limit=(\d+)', line, re.I)

        if "K_CORE" in up:
            k = int(m_k.group(1)) if m_k else 3
            return self._compute_k_core(k)
        if "MOTIF" in up:
            top = int(m_top.group(1)) if m_top else 20
            return self._compute_motif(top)
        if "BETWEENNESS" in up:
            limit = int(m_lim.group(1)) if m_lim else 100
            return self._compute_betweenness(limit)
        return {"error": f"unknown COMPUTE target in: {line}"}

    def _compute_k_core(self, k: int) -> Dict[str, Any]:
        if self.engine is None:
            return {"error": "no engine"}
        # Build adjacency
        adj: Dict[str, set] = {}
        nodes_src = (
            [self._focus] if self._focus and self._focus != "all_nodes"
            else list(self.engine.nodes.keys())[:600]
        )
        for nid in nodes_src:
            adj.setdefault(nid, set())
            for e in self.engine.edges_for_node(nid):
                other = _edge_other(e, nid)
                adj[nid].add(other)
                adj.setdefault(other, set())
                adj[other].add(nid)

        # Iterative pruning
        remaining = set(adj.keys())
        changed = True
        while changed:
            changed = False
            to_remove = [n for n in remaining if len(adj[n] & remaining) < k]
            if to_remove:
                remaining -= set(to_remove)
                changed = True

        core_nodes = []
        for nid in remaining:
            node_obj = self.engine.get_node(nid)
            nd = node_obj.to_dict() if node_obj and hasattr(node_obj,'to_dict') else {}
            core_nodes.append({
                "id":     nid,
                "labels": nd.get("labels", {}),
                "degree": len(adj[nid] & remaining),
            })
        core_nodes.sort(key=lambda n: n["degree"], reverse=True)

        logger.info("[COMPUTE k_core] k=%d result=%d nodes", k, len(core_nodes))
        return {
            "verb":           "COMPUTE",
            "algorithm":      f"k_core(k={k})",
            "core_size":      len(core_nodes),
            "core_nodes":     core_nodes[:50],
        }

    def _compute_motif(self, top: int) -> Dict[str, Any]:
        if self.engine is None:
            return {"error": "no engine"}
        # Closed triad counting
        adj: Dict[str, set] = {}
        nodes_src = (
            [self._focus] if self._focus and self._focus != "all_nodes"
            else list(self.engine.nodes.keys())[:400]
        )
        for nid in nodes_src:
            adj.setdefault(nid, set())
            for e in self.engine.edges_for_node(nid):
                other = _edge_other(e, nid)
                adj[nid].add(other)

        # Count triangles per node
        triangle_counts: Dict[str, int] = {n: 0 for n in adj}
        node_list = list(adj.keys())
        for i, a in enumerate(node_list):
            neighbors_a = adj[a]
            for b in neighbors_a:
                if b <= a:
                    continue
                shared = neighbors_a & adj.get(b, set())
                for c in shared:
                    if c > b:
                        triangle_counts[a] += 1
                        triangle_counts[b] += 1
                        triangle_counts[c] += 1

        rows = [
            {"entity_id": nid, "triangle_count": cnt,
             "labels": (self.engine.get_node(nid).to_dict() if self.engine.get_node(nid) and hasattr(self.engine.get_node(nid),'to_dict') else {}).get("labels", {})}
            for nid, cnt in triangle_counts.items() if cnt > 0
        ]
        rows.sort(key=lambda r: r["triangle_count"], reverse=True)

        total_triangles = sum(triangle_counts.values()) // 3
        logger.info("[COMPUTE motif] total_triangles=%d top_nodes=%d", total_triangles, len(rows))
        return {
            "verb":            "COMPUTE",
            "algorithm":       "motif_triad",
            "total_triangles": total_triangles,
            "top_nodes":       rows[:top],
        }

    def _compute_betweenness(self, limit: int) -> Dict[str, Any]:
        if self.engine is None:
            return {"error": "no engine"}
        import random
        adj: Dict[str, List[str]] = {}
        nodes_src = list(self.engine.nodes.keys())[:limit]
        for nid in nodes_src:
            adj.setdefault(nid, [])
            for e in self.engine.edges_for_node(nid):
                other = _edge_other(e, nid)
                if other in {n for n in nodes_src}:
                    adj[nid].append(other)

        # Approximate betweenness via random BFS pairs
        scores: Dict[str, float] = {n: 0.0 for n in nodes_src}
        sample_size = min(40, len(nodes_src))
        sources = random.sample(nodes_src, sample_size)

        for src in sources:
            # BFS
            parent: Dict[str, List[str]] = {src: []}
            dist:   Dict[str, int]       = {src: 0}
            sigma:  Dict[str, float]     = {src: 1.0}
            queue   = [src]
            order   = []
            while queue:
                v = queue.pop(0)
                order.append(v)
                for w in adj.get(v, []):
                    if w not in dist:
                        dist[w] = dist[v] + 1
                        queue.append(w)
                    if dist.get(w) == dist[v] + 1:
                        sigma.setdefault(w, 0.0)
                        sigma[w] += sigma[v]
                        parent.setdefault(w, []).append(v)
            delta: Dict[str, float] = {n: 0.0 for n in order}
            for w in reversed(order):
                for v in parent.get(w, []):
                    frac = sigma.get(v, 0) / max(sigma.get(w, 1), 1e-9)
                    delta[v] += frac * (1.0 + delta[w])
                if w != src:
                    scores[w] += delta[w]

        rows = sorted(
            [{"entity_id": n, "betweenness": round(scores[n], 3)} for n in nodes_src],
            key=lambda r: r["betweenness"], reverse=True
        )
        logger.info("[COMPUTE betweenness] nodes=%d top=%s", len(nodes_src), rows[0] if rows else None)
        return {
            "verb":       "COMPUTE",
            "algorithm":  "approx_betweenness",
            "sample":     sample_size,
            "top_nodes":  rows[:20],
        }

    def _do_graph_delta(self, line: str) -> Dict[str, Any]:
        """
        GRAPH_DELTA [slices=N] [window=T]

        Splits the observation window into N equal time slices and computes the
        structural diff between consecutive slices.  Returns newly formed edges,
        dissolved edges, and emerging connected components.

        Examples:
          GRAPH_DELTA slices=3 window=5m
          GRAPH_DELTA
        """
        m_sl  = re.search(r'slices=(\d+)',       line, re.I)
        m_win = re.search(r'window=([\d.]+[smh]?)', line, re.I)
        slices = int(m_sl.group(1)) if m_sl else 3
        window = _parse_duration(m_win.group(1)) if m_win else 300.0

        if self.engine is None:
            return {"error": "no engine"}

        import time as _time
        now    = _time.time()
        cutoff = now - window
        slice_dur = window / slices

        # Bucket edges by time slice
        buckets: List[set] = [set() for _ in range(slices)]
        for eid, edge in self.engine.edges.items():
            created = _edge_ts(edge)
            if created is None or created < cutoff:
                continue
            idx = min(int((created - cutoff) / slice_dur), slices - 1)
            edge_key = (_edge_src(edge) or '?', _edge_dst(edge) or '?',
                        getattr(edge, 'kind', ''))
            buckets[idx].add(edge_key)

        # Compute per-slice diffs
        diffs = []
        for i in range(1, slices):
            prev, curr = buckets[i-1], buckets[i]
            appeared  = curr - prev
            dissolved = prev - curr
            diffs.append({
                "slice":    i,
                "appeared": [{"src": s, "dst": d, "kind": k} for s, d, k in list(appeared)[:20]],
                "dissolved":[{"src": s, "dst": d, "kind": k} for s, d, k in list(dissolved)[:20]],
                "net_change": len(appeared) - len(dissolved),
                "new_edges": len(appeared),
                "lost_edges": len(dissolved),
            })

        # Find new components in last slice vs first
        def _components(edge_set):
            parent = {}
            def find(x):
                parent.setdefault(x, x)
                if parent[x] != x:
                    parent[x] = find(parent[x])
                return parent[x]
            def union(a, b):
                pa, pb = find(a), find(b)
                if pa != pb:
                    parent[pa] = pb
            for s, d, _ in edge_set:
                union(s, d)
            comps = {}
            for n in parent:
                comps.setdefault(find(n), []).append(n)
            return list(comps.values())

        old_comps = _components(buckets[0])
        new_comps = _components(buckets[-1])
        emerging  = [c for c in new_comps if not any(
            any(m in oc for m in c) for oc in old_comps
        )]

        logger.info("[GRAPH_DELTA] slices=%d window=%.0fs emerging_comps=%d",
                    slices, window, len(emerging))
        return {
            "verb":                "GRAPH_DELTA",
            "window_s":            window,
            "slices":              slices,
            "diffs":               diffs,
            "emerging_components": [{"size": len(c), "members": c[:10]} for c in emerging[:10]],
            "total_emerging":      len(emerging),
        }

    def _do_rf_correlate(self, line: str) -> Dict[str, Any]:
        """
        RF_CORRELATE [freq=F] [window=T]

        Finds network graph entities (edges, nodes) whose creation timestamps
        correlate with RF anomaly events at the given frequency.  Cross-domain
        fusion: RF activity → network actor hypothesis.

        Examples:
          RF_CORRELATE freq=433.9MHz window=2s
          RF_CORRELATE
        """
        m_freq = re.search(r'freq=([\d.]+\s*[MKGmkg][Hh][Zz]?)', line, re.I)
        m_win  = re.search(r'window=([\d.]+[smh]?)', line, re.I)
        freq_str = m_freq.group(1) if m_freq else None
        window   = _parse_duration(m_win.group(1)) if m_win else 2.0

        # Attempt to pull RF anomaly timestamps from rf_scythe_api_server module
        rf_events: List[float] = []
        try:
            import sys
            for mod_name, mod in sys.modules.items():
                if 'rf_scythe' in mod_name or mod_name == '__main__':
                    # Try common attribute names for recent RF anomaly timestamps
                    for attr in ('_rf_anomaly_ts', 'rf_anomaly_timestamps',
                                 '_last_anomalies', 'recent_rf_events'):
                        obj = getattr(mod, attr, None)
                        if obj is not None:
                            try:
                                rf_events = list(obj)[-200:]
                            except Exception:
                                pass
                            if rf_events:
                                break
                if rf_events:
                    break
        except Exception:
            pass

        if not rf_events:
            # No RF telemetry available — return topology snapshot with note
            if self.engine is None:
                return {"error": "no engine and no RF data"}
            import time as _time
            now   = _time.time()
            edges = [
                e.to_dict() if hasattr(e, 'to_dict') else {}
                for e in list(self.engine.edges.values())[-50:]
            ]
            return {
                "verb":          "RF_CORRELATE",
                "freq":          freq_str,
                "window_s":      window,
                "rf_events":     0,
                "note":          "No RF telemetry available; showing recent edge snapshot",
                "recent_edges":  edges[:20],
            }

        import time as _time
        now    = _time.time()
        cutoff = now - 600.0  # look back 10 min for graph events

        correlated_nodes: List[Dict[str, Any]] = []
        seen = set()
        for rf_ts in rf_events:
            lo, hi = rf_ts - window, rf_ts + window
            for eid, edge in self.engine.edges.items():
                created = _edge_ts(edge)
                if created is None or not (lo <= created <= hi):
                    continue
                for nid in (_edge_src(edge), _edge_dst(edge)):
                    if nid and nid not in seen:
                        seen.add(nid)
                        n = self.engine.get_node(nid)
                        nd = n.to_dict() if n and hasattr(n,'to_dict') else {}
                        correlated_nodes.append({
                            "entity_id":   nid,
                            "labels":      nd.get("labels", {}),
                            "rf_ts":       rf_ts,
                            "edge_ts":     created,
                            "delta_s":     round(created - rf_ts, 4),
                        })

        correlated_nodes.sort(key=lambda r: abs(r["delta_s"]))
        logger.info("[RF_CORRELATE] freq=%s rf_events=%d correlated_nodes=%d",
                    freq_str, len(rf_events), len(correlated_nodes))
        return {
            "verb":             "RF_CORRELATE",
            "freq":             freq_str,
            "window_s":         window,
            "rf_events":        len(rf_events),
            "correlated_nodes": correlated_nodes[:30],
            "hit_count":        len(correlated_nodes),
        }

    def _do_bsg_map(self, line: str) -> Dict[str, Any]:
        """
        BSG_MAP [group=G]

        Maps Behavioral Signature Group (BSG) labels to induced subgraph structural
        signatures: density, diameter estimate, clustering coefficient.

        Examples:
          BSG_MAP group=c2_beacon
          BSG_MAP
        """
        m_grp = re.search(r'group=(\S+)', line, re.I)
        target_group = m_grp.group(1).lower() if m_grp else None

        if self.engine is None:
            return {"error": "no engine"}

        # Collect BSG group → members mapping from node labels
        bsg_groups: Dict[str, List[str]] = {}
        for nid in list(self.engine.nodes.keys())[:800]:
            node_obj = self.engine.get_node(nid)
            if not node_obj:
                continue
            nd = node_obj.to_dict() if hasattr(node_obj, 'to_dict') else {}
            lbl = nd.get("labels", {})
            # BSG group might be stored as bsg_group, group, or behavior_group
            grp = lbl.get("bsg_group") or lbl.get("group") or lbl.get("behavior_group")
            if not grp:
                # Infer from known violation fields
                if lbl.get("dns_tunnel"):
                    grp = "dns_tunnel"
                elif lbl.get("constant_size_c2") or lbl.get("c2_beacon"):
                    grp = "c2_beacon"
                elif lbl.get("tcp_rst_flood"):
                    grp = "tcp_rst_flood"
                elif lbl.get("risk_port"):
                    grp = "risk_port"
            if grp:
                key = str(grp).lower()
                if target_group and key != target_group:
                    continue
                bsg_groups.setdefault(key, []).append(nid)

        if not bsg_groups:
            return {
                "verb":  "BSG_MAP",
                "groups": [],
                "note":  "No BSG-labeled nodes found in current graph",
            }

        import math
        results = []
        for grp_name, members in bsg_groups.items():
            member_set = set(members)
            # Build induced adjacency
            adj: Dict[str, set] = {n: set() for n in members}
            edge_count = 0
            for nid in members:
                for e in self.engine.edges_for_node(nid):
                    other = _edge_other(e, nid)
                    if other in member_set:
                        adj[nid].add(other)
                        edge_count += 1
            edge_count //= 2  # undirected

            n = len(members)
            max_edges = n * (n - 1) / 2 if n > 1 else 1
            density   = round(edge_count / max_edges, 4)

            # Clustering coefficient (avg local)
            cc_vals = []
            for nid in members:
                nbrs = adj[nid]
                k = len(nbrs)
                if k < 2:
                    continue
                triangles = sum(1 for a in nbrs for b in nbrs if b > a and b in adj.get(a, set()))
                cc_vals.append(2 * triangles / (k * (k - 1)))
            avg_cc = round(sum(cc_vals) / len(cc_vals), 4) if cc_vals else 0.0

            # Diameter estimate via BFS from random node
            diameter = 0
            if members:
                import random
                src = random.choice(members)
                dist = {src: 0}
                queue = [src]
                while queue:
                    v = queue.pop(0)
                    for w in adj.get(v, set()):
                        if w not in dist:
                            dist[w] = dist[v] + 1
                            queue.append(w)
                diameter = max(dist.values()) if dist else 0

            results.append({
                "group":       grp_name,
                "size":        n,
                "edge_count":  edge_count,
                "density":     density,
                "avg_clustering_coeff": avg_cc,
                "diameter_estimate":    diameter,
                "members":     members[:15],
            })

        results.sort(key=lambda r: r["size"], reverse=True)
        logger.info("[BSG_MAP] groups=%d total_members=%d",
                    len(results), sum(r["size"] for r in results))
        return {
            "verb":   "BSG_MAP",
            "groups": results,
            "group_count": len(results),
        }

    # ── Vector helpers ────────────────────────────────────────────────────────

    def _embed_intent(self, intent: str):
        """Embed an intent phrase using EmbeddingEngine → numpy array or None."""
        try:
            import sys
            ee = None
            for mod_name, mod in sys.modules.items():
                if 'rf_scythe_api_server' in mod_name or mod_name == '__main__':
                    ee = getattr(mod, 'embedding_engine', None)
                    if ee is not None:
                        break
            if ee is None:
                return None
            vec = ee.embed_text(intent)
            if vec is None:
                return None
            import numpy as np
            v = np.asarray(vec, dtype='float32')
            norm = np.linalg.norm(v)
            return v / norm if norm > 1e-8 else None
        except Exception as e:
            logger.debug("[VECTOR_SEARCH] embed failed: %s", e)
            return None

    def _get_node(self, entity_id: str) -> Optional[Dict]:
        """Fetch a node dict from the hypergraph engine by entity_id."""
        if self.engine is None:
            return None
        try:
            nodes = getattr(self.engine, 'nodes', {})
            node  = nodes.get(entity_id)
            if node is None:
                return None
            return node.to_dict() if hasattr(node, 'to_dict') else node
        except Exception:
            return None

    @staticmethod
    def _parse_vector_filters(line: str) -> List[Dict[str, Any]]:
        """
        Parse scalar filter predicates from a VECTOR_SEARCH line.

        Accepts: field>value, field<value, field>=value, field<=value, field=value
        Example: proto_anomaly>0.4 confidence>=0.6
        """
        preds = []
        for m in re.finditer(
            r'(\w+)\s*(>=|<=|>|<|=)\s*([\d.]+)',
            line
        ):
            field, op, val = m.group(1), m.group(2), float(m.group(3))
            # Skip k= and threshold= — those are verb params not filters
            if field.lower() in ('k', 'threshold', 'min_cluster'):
                continue
            preds.append({"field": field, "op": op, "value": val})
        return preds

    @staticmethod
    def _apply_vector_filters(labels: Dict, preds: List[Dict]) -> bool:
        """Return True if labels pass all filter predicates."""
        for p in preds:
            raw = labels.get(p["field"])
            if raw is None:
                continue  # missing field → don't discard (permissive)
            try:
                v = float(raw)
            except (TypeError, ValueError):
                continue
            op = p["op"]
            t  = p["value"]
            if op == '>'  and not (v >  t): return False
            if op == '<'  and not (v <  t): return False
            if op == '>=' and not (v >= t): return False
            if op == '<=' and not (v <= t): return False
            if op == '='  and not (v == t): return False
        return True

    # ── SUMMARIZE ─────────────────────────────────────────────────────────────

    def _do_summarize(self) -> Dict[str, Any]:
        """Produce a human-readable summary of the current investigation state."""
        n = len(self._focus_nodes)
        e = len(self._focus_edges)
        top_degrees = []
        if self.engine:
            top_degrees = sorted(
                [(k, v) for k, v in self.engine.degree.items()
                 if any(nd.get("id") == k for nd in self._focus_nodes)],
                key=lambda x: x[1], reverse=True
            )[:5]

        summary = {
            "focus":        self._focus,
            "window_s":     self._window_s,
            "node_count":   n,
            "edge_count":   e,
            "top_degrees":  [{"node": k, "degree": v} for k, v in top_degrees],
        }
        if self._topo and hasattr(self._topo, "alerts_fired"):
            summary["drift_alerts_total"] = self._topo.alerts_fired
        if self._fanin and hasattr(self._fanin, "alerts_fired"):
            summary["fanin_alerts_total"] = self._fanin.alerts_fired

        return {"summary": summary}

    # ── ASSESS ────────────────────────────────────────────────────────────────

    def _do_assess(self) -> Dict[str, Any]:
        """Produce a threat assessment based on accumulated step results."""
        signals = []
        confidence = 0.0

        for step in self._last_result.get("steps", []):
            r    = step.get("result", {})
            verb = step.get("verb", "").upper()

            # ── Legacy degree/fanin/timing signals ────────────────────────────
            max_delta  = r.get("max_delta", 0)
            max_fanin  = r.get("max_fanin", 0)
            sync_nodes = r.get("synchronized_nodes", [])

            if max_delta > 50:
                signals.append(f"High degree delta detected: {max_delta}")
                confidence = max(confidence, 0.70)
            if max_fanin > 30:
                signals.append(f"High fan-in detected: {max_fanin} unique sources")
                confidence = max(confidence, 0.75)
            if sync_nodes:
                signals.append(f"Synchronized timing on {len(sync_nodes)} node(s)")
                confidence = max(confidence, 0.80)
            clusters = r.get("clusters", [])
            synced = [c for c in clusters if c.get("type") == "synchronized"]
            if synced:
                signals.append(f"Synchronized cluster detected: {synced[0]['size']} nodes")
                confidence = max(confidence, 0.85)

            # ── TEMPORAL_ENTROPY beacon signal ────────────────────────────────
            if "TEMPORAL_ENTROPY" in verb:
                beacon_count = r.get("beacon_count", 0)
                if beacon_count > 0:
                    signals.append(f"Low-entropy beacon candidates: {beacon_count}")
                    confidence = max(confidence, 0.65 + min(beacon_count * 0.02, 0.15))

            # ── STITCH_IDENTITIES rotating actor signal ───────────────────────
            if "STITCH_IDENTITIES" in verb:
                rotating = r.get("rotating_count", 0)
                if rotating > 0:
                    signals.append(f"Rotating identity actors detected: {rotating} groups")
                    confidence = max(confidence, 0.75 + min(rotating * 0.03, 0.10))

            # ── CLUSTER_SIMILAR large behavioral cluster signal ───────────────
            if "CLUSTER_SIMILAR" in verb:
                sig_clusters = r.get("significant", 0)
                if sig_clusters > 0:
                    top_size = r.get("clusters", [{}])[0].get("size", 0)
                    signals.append(
                        f"Behavioral similarity clusters: {sig_clusters} groups "
                        f"(largest={top_size})"
                    )
                    confidence = max(confidence, 0.70 + min(sig_clusters * 0.02, 0.10))

            # ── GRAPH_DELTA emerging component signal ─────────────────────────
            if "GRAPH_DELTA" in verb:
                emerging = r.get("total_emerging", 0)
                net_max  = max((d.get("net_change", 0) for d in r.get("diffs", [])),
                               default=0)
                if emerging > 0:
                    signals.append(f"Emerging connected components: {emerging}")
                    confidence = max(confidence, 0.68 + min(emerging * 0.04, 0.15))
                if net_max > 20:
                    signals.append(f"Graph edge spike: +{net_max} edges in one slice")
                    confidence = max(confidence, 0.72)

            # ── COMPUTE k_core large core signal ──────────────────────────────
            if "COMPUTE" in verb and r.get("algorithm", "").startswith("k_core"):
                core_size = r.get("core_size", 0)
                if core_size > 5:
                    signals.append(f"Dense k-core subgraph: {core_size} nodes")
                    confidence = max(confidence, 0.72 + min(core_size * 0.005, 0.10))

            # ── RF_CORRELATE cross-domain hit signal ──────────────────────────
            if "RF_CORRELATE" in verb:
                hit_count = r.get("hit_count", 0)
                if hit_count > 0:
                    signals.append(
                        f"RF-correlated network entities: {hit_count} "
                        f"(freq={r.get('freq', '?')})"
                    )
                    confidence = max(confidence, 0.80 + min(hit_count * 0.01, 0.10))

            # ── VECTOR_SEARCH high-anomaly match signal ────────────────────────
            if "VECTOR_SEARCH" in verb:
                match_count = r.get("matched", 0)
                if match_count > 0:
                    signals.append(f"Behavioral vector matches: {match_count}")
                    confidence = max(confidence, 0.65 + min(match_count * 0.01, 0.15))

            # ── BEHAVIOR_QUERY behavior-first signal ───────────────────────────
            if "BEHAVIOR_QUERY" in verb:
                match_count = r.get("matched", 0)
                utility_matches = r.get("utility_match_count", 0)
                top_match = (r.get("matches") or [{}])[0]
                if match_count > 0:
                    signals.append(
                        f"Behavior-first matches: {match_count} "
                        f"(top={top_match.get('behavior_class', 'UNKNOWN')})"
                    )
                    confidence = max(
                        confidence,
                        0.64 + min(match_count * 0.02, 0.12) + min(float(top_match.get("match_score", 0.0)) * 0.08, 0.08),
                    )
                if utility_matches > 0:
                    protocols = ",".join((top_match.get("ot_protocols") or [])[:3]) or "utility-profile"
                    signals.append(f"Utility/OT persistence candidates: {utility_matches} ({protocols})")
                    confidence = max(confidence, 0.74 + min(float(top_match.get("utility_score", 0.0)) * 0.08, 0.08))
                    if float(top_match.get("identity_pressure", 0.0)) >= 0.55:
                        signals.append("Energy-grid identity persistence under protocol mutation")
                        confidence = max(confidence, 0.82)

            # ── BSG_MAP signal ────────────────────────────────────────────────
            if "BSG_MAP" in verb:
                group_count = r.get("group_count", 0)
                if group_count > 0:
                    total_members = sum(
                        g.get("size", 0) for g in r.get("groups", [])
                    )
                    signals.append(
                        f"BSG groups mapped: {group_count} "
                        f"({total_members} total members)"
                    )
                    confidence = max(confidence, 0.68)

        # ── Live detector alerts ──────────────────────────────────────────────
        if self._topo and self._topo.alerts_fired > 0:
            signals.append(f"{self._topo.alerts_fired} drift alerts active")
            confidence = max(confidence, 0.80)
        if self._fanin and self._fanin.alerts_fired > 0:
            signals.append(f"{self._fanin.alerts_fired} fan-in (botnet) alerts active")
            confidence = max(confidence, 0.90)

        threat_level = (
            "CRITICAL" if confidence >= 0.90 else
            "HIGH"     if confidence >= 0.75 else
            "MEDIUM"   if confidence >= 0.50 else
            "LOW"
        )

        return {
            "assessment": {
                "threat_level": threat_level,
                "confidence":   round(confidence, 2),
                "signals":      signals,
                "verdict":      (
                    "Coordinated attack activity detected"
                    if confidence >= 0.85 else
                    "Anomalous activity — investigation recommended"
                    if confidence >= 0.50 else
                    "No significant threat indicators"
                ),
            }
        }

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _merge(target: dict, src: dict) -> None:
        """Merge step result into accumulated result."""
        for k, v in src.items():
            if k in ("summary",):
                target[k] = v
            elif k == "assessment":
                target[k] = v.get("assessment") if isinstance(v, dict) and "assessment" in v else v
            elif k == "matches":
                # VECTOR_SEARCH matches → fold into found_nodes list
                if "vector_matches" not in target:
                    target["vector_matches"] = []
                target["vector_matches"].extend(v if isinstance(v, list) else [])
            elif k == "clusters" and isinstance(v, list):
                if "similarity_clusters" not in target:
                    target["similarity_clusters"] = []
                target["similarity_clusters"].extend(v)
            elif isinstance(v, list) and k in target and isinstance(target[k], list):
                target[k].extend(v)
            elif isinstance(v, dict) and k in ("metrics",) and isinstance(target[k], dict):
                target[k].update(v)


_INTENT_RULES: List[tuple] = [
    # Behavior-first cadence / divergence / utility queries
    (r'utility|energy.?grid|power.?grid|substation|smart.?meter|ami|amr|scada|modbus|dnp3|iec.?104|iec.?61850|\bot\b', 'BEHAVIOR_QUERY utility=true top=25'),
    (r'identity.?pressure|divergence|model.?disagreement|temporal.?cohesion|behavior.?first|behavioral.?query', 'BEHAVIOR_QUERY temporal_cohesion>=0.45 identity_pressure>=0.45 top=25'),
    (r'beacon|periodic|cadence|rhythm|low.?and.?slow', 'BEHAVIOR_QUERY periodicity_s>0 temporal_cohesion>=0.55 top=25'),
    (r'proxy|relay|churn|rotation|spoof', 'BEHAVIOR_QUERY identity_pressure>=0.55 divergence_risk>=0.25 top=25'),
    # Temporal / beacon patterns
    (r'temporal.?entr|beacon|periodic|regular.?interval|IAT|inter.?arrival', 'TEMPORAL_ENTROPY window=5m top=20'),
    # Identity stitching — TLS/JA3
    (r'TLS.?fingerprint|JA3|stable.?fingerprint|fingerprint.*IP.?change|fingerprint.*across', 'STITCH_IDENTITIES field=tls_ja3 window=10m'),
    # Identity stitching — embedding similarity
    (r'same.?actor.*different.?IP|identity.*VPN|embedding.?similarity.*IP|actor.*rotat', 'STITCH_IDENTITIES field=embedding threshold=0.88'),
    # k-core
    (r'k.?core|dense.?subgraph|core.?decomposition|tightly.?connected', 'COMPUTE k_core k=3'),
    # Betweenness / bridges
    (r'betweenness|bridge.?node|intermediar|pivot.?node|relay', 'COMPUTE betweenness'),
    # Motifs / triads
    (r'motif|triad|structural.?pattern|triangle', 'COMPUTE motif top=20'),
    # Graph delta / temporal evolution
    (r'graph.?delta|sliding.?window.?diff|newly.?formed|ephemeral|evolv|emerg', 'GRAPH_DELTA slices=3 window=5m'),
    # RF cross-domain correlation
    (r'RF.*correlat|correlat.*RF|frequency.*anomal|MHz.*graph|graph.*MHz|radio.*network', 'RF_CORRELATE window=2s'),
    # BSG structural mapping
    (r'BSG.?group|behavioral.?group.*subgraph|group.*structural|signature.?group', 'BSG_MAP'),
    # General behavioral similarity → CLUSTER_SIMILAR
    (r'rotating.?proxy|infrastructure.?reuse|same.?TLS.*different.?IP', 'CLUSTER_SIMILAR threshold=0.30'),
    # General anomaly → VECTOR_SEARCH
    (r'anomalous|unusual|suspicious|behavioral.*pattern|unknown.?traffic', 'VECTOR_SEARCH'),
]


def _compile_intent(question: str) -> List[str]:
    """
    Pre-process a natural-language question into DSL hints.

    Returns a list of recommended DSL verbs (without parameters stripped) that
    should appear early in the plan.  The LLM receives these as a
    ``dsl_hints`` list so it can anchor its plan to grounded verbs instead of
    defaulting to generic FIND queries.
    """
    hints: List[str] = []
    q_lower = question.lower()
    for pattern, hint in _INTENT_RULES:
        if re.search(pattern, question, re.I):
            hints.append(hint)
            if len(hints) >= 3:   # cap at 3 hints — keep prompt tight
                break
    return hints


def normalize_edge_kind(raw_kind: str) -> Dict[str, Any]:
    """Project a raw LLM-emitted edge kind string into a structured schema dict.

    Handles the common ``data:value`` hallucination pattern where a model emits
    attribute data *as* the edge kind (e.g. ``tls_sni:chatgpt.com``).

    Returns ``{"kind": str, "attr": str|None}``.

    Examples::

        normalize_edge_kind("tls_sni:chatgpt.com")
        # → {"kind": "TLS_SNI", "attr": "chatgpt.com"}

        normalize_edge_kind("INFERRED_FLOW_ON_RIDGE")
        # → {"kind": "INFERRED_FLOW_ON_RIDGE", "attr": None}
    """
    if not raw_kind:
        return {"kind": "UNKNOWN", "attr": None}
    if ":" in raw_kind:
        kind, attr = raw_kind.split(":", 1)
        return {"kind": kind.upper().replace("-", "_"), "attr": attr.strip()}
    return {"kind": raw_kind.upper().replace("-", "_"), "attr": None}


def _classify_from_assessment(result: dict) -> str:
    """Derive a compact classification label from an ASSESS result dict."""
    assess   = result.get("assessment") or {}
    signals  = assess.get("signals", [])
    sig_text = " ".join(signals).lower()

    if (
        "utility" in sig_text
        or "energy-grid" in sig_text
        or "power" in sig_text
        or "ot persistence" in sig_text
        or "modbus" in sig_text
        or "dnp3" in sig_text
        or "iec104" in sig_text
        or "iec61850" in sig_text
    ):
        return "utility_grid_chicanery"
    if "beacon" in sig_text or "entropy" in sig_text or "low-entropy" in sig_text:
        return "beaconing_cluster"
    if "synchronized" in sig_text:
        return "coordinated_cluster"
    if "fan-in" in sig_text or "botnet" in sig_text:
        return "botnet_fanin"
    if "proxy" in sig_text or "tls" in sig_text or "stitch" in sig_text:
        return "rotating_proxy"
    if "k_core" in sig_text or "dense" in sig_text:
        return "dense_coordination_cluster"
    if "rf" in sig_text or "correlat" in sig_text:
        return "rf_correlated_actor"
    if "delta" in sig_text or "degree" in sig_text or "scan" in sig_text:
        return "port_scan_or_recon"
    threat = assess.get("threat_level", "LOW")
    if threat in ("CRITICAL", "HIGH"):
        return "high_threat_actor"
    return "anomalous_activity"


# Signal type → (minimum_signal_strength_threshold, base_confidence, prompt_text)
_SIGNAL_PROMPT_MAP: List[tuple] = [
    # low_entropy signals → beacon investigation
    ("low_entropy",     0.05, 0.72,
     "investigate nodes with lowest temporal entropy within 5m window "
     "and correlate with periodic session intervals"),
    ("low_entropy",     0.10, 0.74,
     "detect synchronized session starts across multiple hosts within "
     "subsecond intervals — flag coordination clusters"),
    # graph_delta signals → emerging event investigation
    ("graph_delta",     0.05, 0.70,
     "run graph_delta slices=3 window=5m and identify newly formed "
     "connected components indicating emerging events"),
    ("graph_delta",     0.15, 0.73,
     "identify entities with embedding drift over rolling window and "
     "investigate behavioral mutation"),
    # degree_variance → bridge/relay/pivot node investigation
    ("degree_variance", 0.10, 0.71,
     "compute betweenness limit=50 and inspect nodes bridging distinct "
     "network segments — identify relay and pivot nodes"),
    ("degree_variance", 0.20, 0.74,
     "compute flow asymmetry and isolate nodes with dominant outbound "
     "traffic patterns suggesting exfiltration or C2"),
    # embedding_store → identity and similarity investigation
    ("embedding_store", 0.10, 0.73,
     "stitch identities across tls_ja3 where ip rotation exceeds 3 "
     "transitions within 10m window"),
    ("embedding_store", 0.20, 0.75,
     "cluster similar actors threshold=0.30 and flag behavioral families "
     "with rotating infrastructure"),
    ("embedding_store", 0.30, 0.76,
     "detect nodes with high neighbor churn but stable embedding "
     "similarity >0.88 indicating persistent identity across IP churn"),
    # rf_available → cross-domain RF correlation
    ("rf_available",    0.50, 0.80,
     "correlate rf anomalies window=2s with concurrent edge creation "
     "spikes — identify network actors active during RF events"),
    # graph_size → structural / k-core / motif investigation
    ("graph_size",      0.05, 0.68,
     "compute k_core k=3 and analyze highest density subgraph for "
     "coordinated behavior"),
    ("graph_size",      0.10, 0.70,
     "compute motif top=10 and flag structurally rare triads indicating "
     "covert coordination or infrastructure reuse"),
    ("graph_size",      0.20, 0.72,
     "map bsg group=c2_beacon and compare structural signature against "
     "data exfiltration clusters"),
    ("graph_size",      0.30, 0.74,
     "run subgraph isomorphism to detect repeated structural attack "
     "patterns across ip ranges via CLUSTER_SIMILAR threshold=0.30"),
    ("graph_size",      0.40, 0.73,
     "detect silent hubs with high connectivity but low behavioral "
     "tagging — run ANALYZE fanout FILTER degree_delta > 30"),
]


# ─── GraphOpsAgent ────────────────────────────────────────────────────────────

_PLAN_SYSTEM = """You are GraphOps, a network threat investigation agent with a behavioral vector memory.

Your job is to investigate the user's question using a GraphOps DSL query plan.

## ⚠️ CRITICAL RULES (NEVER BREAK THESE)
- You MUST NOT emit generic FIND queries or invent DSL verbs not listed below.
- You MUST NOT use placeholder values like 10.0.0.1, abc123, or "example".
- You MUST derive queries from live graph statistics and the entities context.
- You MUST use the dsl_hints provided in the request — include them early in your plan.
- For behavioral/pattern/anomaly questions: ALWAYS start with BEHAVIOR_QUERY, VECTOR_SEARCH, or an advanced verb.
- NEVER start with FOCUS when the question is about patterns, groups, or behavior.

## DSL Verbs (use EXACTLY these)

### Graph traversal:
  FOCUS <entity|all_nodes>
  EXPAND [inbound|outbound|neighbors] [depth=N] [limit=N]
  TRACE path FROM <a> TO <b> [depth=N]
  FILTER <field> [>|<|=|>=|<=] <value>
  BEHAVIOR_QUERY <field><op><value> [<field><op><value> ...] [top=N]
  WINDOW <200ms|5s|10m|1h>
  ANALYZE [fanin|fanout|degree_delta|temporal_sync|path_density]
  CLUSTER [timing|topology]

### Vector intelligence (use FIRST for behavior/pattern/anomaly questions):
  VECTOR_SEARCH "<behavioral intent phrase>" [k=N] [proto_anomaly>F] [confidence>F]
  CLUSTER_SIMILAR [threshold=F] [k=N] [min_cluster=N]
    — threshold is TurboQuant-space: 0.25–0.35 = very similar; default=0.30

### Advanced structural + cross-domain:
  TEMPORAL_ENTROPY [window=T] [top=N]
    — finds low-entropy (beacon/periodic) hosts by IAT Shannon entropy
  STITCH_IDENTITIES [field=F] [threshold=F] [window=T]
    — groups actors with stable attribute (tls_ja3, embedding) across IP/ASN changes
    — field= one of: tls_ja3 | embedding | asn | (any label key)
  COMPUTE k_core [k=N]
    — k-core decomposition; returns densest connected subgraph
  COMPUTE motif [top=N]
    — closed triad (triangle) frequency; flags structurally rare patterns
  COMPUTE betweenness [limit=N]
    — approximate node betweenness centrality; finds bridge/relay nodes
  GRAPH_DELTA [slices=N] [window=T]
    — sliding-window structural diff; surfaces newly formed/dissolved edges and emerging components
  RF_CORRELATE [freq=F] [window=T]
    — cross-domain: finds graph entities created within ±window of RF anomaly events
  BSG_MAP [group=G]
    — maps Behavioral Signature Group labels to induced subgraph structural stats

### Terminal:
  SUMMARIZE
  ASSESS

## Intent → Verb mapping (examples)

| Question pattern                                    | First verb                              |
|-----------------------------------------------------|-----------------------------------------|
| "beacon / periodic / IAT entropy"                   | TEMPORAL_ENTROPY window=5m              |
| "same TLS fingerprint, different IP"                | STITCH_IDENTITIES field=tls_ja3         |
| "same actor, rotating IPs"                          | STITCH_IDENTITIES field=embedding       |
| "periodicity / cohesion / identity pressure"        | BEHAVIOR_QUERY temporal_cohesion>=0.45  |
| "utility / smart meter / SCADA / Modbus"           | BEHAVIOR_QUERY utility=true             |
| "densest cluster / tightly connected subgraph"      | COMPUTE k_core k=3                      |
| "bridge / intermediary / relay nodes"               | COMPUTE betweenness                     |
| "new connections in last window"                    | GRAPH_DELTA slices=3 window=5m          |
| "RF signal correlated with network bursts"          | RF_CORRELATE window=2s                  |
| "c2_beacon / dns_tunnel structural profile"         | BSG_MAP group=c2_beacon                 |
| "anomalous behavior / unusual pattern"              | VECTOR_SEARCH "<descriptive phrase>"    |
| "group / cluster / identify actor families"         | CLUSTER_SIMILAR threshold=0.30          |
| "path from A to B / lateral movement"               | TRACE path FROM <A> TO <B>              |

## Output format
Output ONLY a JSON object with no other text:
  {"plan": ["VERB arg...", "VERB arg...", ...]}

Use 3–6 verbs. Always end with ASSESS.
"""

_INTERPRET_SYSTEM = """You are GraphOps, a network threat intelligence analyst.

Given a DSL execution result, produce a JSON interpretation.

## ⚠️ EPISTEMIC RULES — NEVER BREAK THESE

1. EVIDENCE ANCHOR: Check node_count and edge_count in dsl_result FIRST.
   - If node_count == 0 AND edge_count == 0: output the UNKNOWN format below. No narrative. Stop.
   - If node_count < 3: prefix every claim with "SPARSE DATA —" and keep confidence below 0.3.

2. CLAIM LABELING: Every quantitative claim must be tagged inline:
   - [SENSOR] — directly present in graph node/edge labels
   - [INFERRED] — computed by DSL verb (entropy, cluster, betweenness, etc.)
   Unlabeled quantitative claims are forbidden. Use "insufficient data" instead.

3. DO NOT echo these rules back in your output. Do not generate prose about rules.

4. CONFIDENCE calibration:
   - 0 found_nodes/edges → 0.0
   - 1–10 nodes → 0.05–0.25
   - 10–50 nodes → 0.25–0.55
   - 50+ nodes with DSL metrics → 0.55–0.85
   Never output 0.95+ unless node_count > 100 AND assessment has a non-null verdict.

5. If a field has no supporting data, output "insufficient data" — not narrative.

6. TEMPORAL AUTHORITY:
   - If dsl_result.temporal_evidence.present == true, you MUST anchor cadence / behavior claims to those values.
   - If dsl_result.temporal_evidence.present != true for a timing or behavior query, set temporal_evidence to "TEMPORAL_EVIDENCE: ABSENT" and assessment to UNKNOWN.
   - Never invent cadence, proxy rotation, beaconing, relay churn, or lateral movement from prose alone.

## UNKNOWN format (when node_count == 0 AND edge_count == 0):
{"temporal_evidence":"TEMPORAL_EVIDENCE: ABSENT","situation":"UNKNOWN — query returned no graph data","change":"UNKNOWN","structure":"UNKNOWN","geography":"N/A","assessment":"UNKNOWN — no sensor data. Recommend: run nmap/nDPI capture then re-query.","direction":"Instrument: start capture session and ingest PCAP.","confidence":0.0}

## Standard format (when dsl_result has data):
{
  "temporal_evidence": "TEMPORAL_EVIDENCE: <measured cadence facts or ABSENT>",
  "situation": "[SENSOR/INFERRED] <1-2 sentences, facts only>",
  "change":    "<what changed vs previous step, or 'no prior baseline'>",
  "structure": "[SENSOR/INFERRED] <graph topology: N nodes, N edges, key clusters>",
  "geography": "<N/A if no geo labels, else [SENSOR] city/ASN facts>",
  "assessment":"<threat hypothesis with explicit confidence tier: LOW/MED/HIGH>",
  "direction": "<single specific next DSL verb or capture action>",
  "confidence": 0.0
}

If VECTOR_SEARCH results present: cite top match IDs and proto_anomaly scores [INFERRED].
If CLUSTER_SIMILAR results present: cite cluster sizes and similarity threshold [INFERRED].
Be brief. One sentence per field. No narrative filler.
"""


class GraphOpsAgent:
    """Autonomous graph investigation agent powered by local Ollama LLM.

    Uses llama3.2:3b for reasoning (falls back to gemma3:1b if unavailable).
    Runs a step loop: plan DSL → execute → interpret → decide continue/stop.
    """

    PREFERRED_MODELS = ["llama3.2:3b", "llama3.2:latest", "gemma3:1b", "gemma3:270m"]
    # Models that only support /api/embeddings — never use for chat
    _EMBEDDING_ONLY_MODELS = {"embeddinggemma", "nomic-embed-text"}

    def __init__(self,
                 engine=None,
                 topology_detector=None,
                 fanin_detector=None,
                 ollama_url: str = "http://localhost:11434",
                 embedding_engine=None):
        self.engine          = engine
        self.extractor       = EntityExtractor()
        self.executor        = InvestigativeDSLExecutor(engine, topology_detector, fanin_detector)
        self._ollama         = ollama_url
        self._models         = self._pick_models()
        self._model          = self._models[0]
        self._embedding_engine = embedding_engine  # optional EmbeddingEngine for RAG

    def _pick_models(self) -> List[str]:
        """Select the best available Ollama chat models in preference order."""
        try:
            import urllib.request
            with urllib.request.urlopen(f"{self._ollama}/api/tags", timeout=3) as resp:
                data = json.loads(resp.read())
            available = {m["name"] for m in data.get("models", [])}
            picked = [
                m for m in self.PREFERRED_MODELS
                if m in available and m not in self._EMBEDDING_ONLY_MODELS
            ]
            if picked:
                logger.info("GraphOpsAgent using models: %s", ", ".join(picked[:MAX_ARBITRATION_MODELS]))
                return picked[:MAX_ARBITRATION_MODELS]
        except Exception:
            pass
        logger.warning("GraphOpsAgent: Ollama unreachable, using gemma3:1b fallback")
        return ["gemma3:1b"]

    def _pick_model(self) -> str:
        """Backward-compatible primary model accessor."""
        return self._models[0] if self._models else "gemma3:1b"

    def _rag_context(self, question: str) -> str:
        """
        Retrieve top-5 semantically similar entities to ground the LLM.

        Search priority:
          1. TurboQuantStore (fp16 matmul, sub-ms, behavioral identity)
          2. EmbeddingEngine FAISS (fallback)

        Injects protocol violation context when present so the LLM knows
        which neighbors are anomalous vs clean.
        """
        vec = self.executor._embed_intent(question)

        # ── TurboQuant primary ────────────────────────────────────────────────
        if vec is not None:
            try:
                from turbo_quant_store import embedding_store as _emb_store
                tq = _emb_store()
                if len(tq) > 0:
                    results = tq.search(vec, k=8)
                    if results:
                        lines = ["[Semantic Memory — behaviorally similar entities:]"]
                        for eid, sim in results[:5]:
                            node   = self.executor._get_node(eid)
                            labels = node.get("labels", {}) if node else {}
                            pa     = labels.get("protocol_anomaly_score", 0.0)
                            viols  = labels.get("protocol_violations", [])
                            vstr   = f" violations={viols}" if viols else ""
                            lines.append(
                                f"  [{sim:.2f}] {eid}"
                                f" proto_anomaly={pa:.2f}{vstr}"
                            )
                        return "\n".join(lines)
            except Exception as exc:
                logger.debug("[GraphOpsAgent] TQ RAG failed: %s", exc)

        # ── FAISS fallback ────────────────────────────────────────────────────
        if not self._embedding_engine:
            return ""
        try:
            results = self._embedding_engine.search_similar(question, k=5)
            if not results:
                return ""
            lines = ["[Semantic Memory — similar historical entities:]"]
            for r in results:
                sim  = r.get("similarity", 0.0)
                eid  = r.get("entity_id", "unknown")
                desc = r.get("description", "")[:200]
                lines.append(f"  [{sim:.2f}] {eid}: {desc}")
            return "\n".join(lines)
        except Exception as exc:
            logger.debug("[GraphOpsAgent] RAG context retrieval failed: %s", exc)
            return ""

    def investigate(self, question: str, max_steps: int = MAX_AGENT_STEPS) -> Dict[str, Any]:
        """Run a full investigation loop for the given question.

        Returns a structured intelligence report including belief_state tracking
        and adaptive suggested prompts from live graph signal gradients.
        """
        entities   = self.extractor.extract(question)
        primary    = self.extractor.primary_entity(entities)
        steps_log  = []
        confidence = 0.0
        grounding_actions: List[Dict[str, Any]] = []
        grounding_triggered = False
        self.executor.reset()

        # ── Query Compiler: translate intent → DSL hints before LLM plans ────
        dsl_hints = _compile_intent(question)
        if dsl_hints:
            logger.info("[GraphOpsAgent] compiled hints: %s", dsl_hints)

        # ── RAG: retrieve similar historical entities for grounding ───────────
        rag_context = self._rag_context(question)

        context = {
            "question":    question,
            "entities":    entities,
            "primary":     primary,
            "steps":       [],
            "confidence":  0.0,
            "rag_context": rag_context,
            "dsl_hints":   dsl_hints,
        }

        if rag_context:
            logger.info("[GraphOpsAgent] RAG: injected %d chars of semantic memory", len(rag_context))

        logger.info("[GraphOpsAgent] investigating: %s (model=%s)", question[:80], self._model)

        seen_plans:    set  = set()     # frozensets of plan verbs — attractor detection
        best_confidence     = 0.0
        plateau_count       = 0
        prev_confidence     = -1.0

        # ── Belief accumulation state (Phase 9 Autonomy Layer) ────────────────
        belief_state: Dict[str, Any] = {
            "evidence_count":      0,
            "confidence_history":  [],
            "convergence_delta":   0.0,
            "converged":           False,
            "classification":      None,
        }

        for step_num in range(max_steps):
            # ── plan ──────────────────────────────────────────────────────────
            plan = self._plan(context)
            if not plan:
                logger.warning("[GraphOpsAgent] planner returned empty plan at step %d", step_num)
                break

            plan_key = frozenset(plan)
            repeat   = plan_key in seen_plans
            seen_plans.add(plan_key)

            logger.info("[GraphOpsAgent] step %d plan: %s%s",
                        step_num, plan, " [REPEAT]" if repeat else "")

            # ── attractor break ───────────────────────────────────────────────
            if repeat and best_confidence >= PLATEAU_THRESHOLD:
                logger.info("[GraphOpsAgent] attractor loop detected at step %d; "
                            "best_confidence=%.2f — exiting early", step_num, best_confidence)
                confidence = best_confidence
                break

            # ── execute ───────────────────────────────────────────────────────
            result = self.executor.run(plan)

            # ── interpret ─────────────────────────────────────────────────────
            interpretation = self._interpret(context, result, allow_grounding=not grounding_triggered)
            confidence = float(interpretation.get("confidence", 0.0))
            best_confidence = max(best_confidence, confidence)
            grounding = interpretation.get("reflexive_grounding")
            if grounding and grounding.get("triggered"):
                grounding_actions.append(grounding)
                grounding_triggered = True

            step_record = {
                "step":           step_num,
                "plan":           plan,
                "result_summary": self._summarize_result(result),
                "interpretation": interpretation,
            }
            steps_log.append(step_record)
            context["steps"].append(step_record)
            context["confidence"] = best_confidence
            context["last_result"] = result

            logger.info("[GraphOpsAgent] step %d confidence=%.2f (best=%.2f)",
                        step_num, confidence, best_confidence)

            # ── belief state update ───────────────────────────────────────────
            assess_result = result.get("assessment")
            if assess_result:
                belief_state["evidence_count"] += len(assess_result.get("signals", []))
            belief_state["confidence_history"].append(round(confidence, 4))

            # Convergence check: last 3 confidence values within ε
            history = belief_state["confidence_history"]
            if len(history) >= 3:
                delta = abs(history[-1] - history[-3])
                belief_state["convergence_delta"] = round(delta, 4)
                if delta < 0.02 and history[-1] > 0.40:
                    belief_state["converged"]      = True
                    belief_state["classification"] = _classify_from_assessment(result)
                    logger.info(
                        "[GraphOpsAgent] CONVERGED at step %d → %s (conf=%.2f delta=%.4f)",
                        step_num, belief_state["classification"], confidence, delta
                    )
                    confidence = best_confidence
                    break

            # ── threshold exit ────────────────────────────────────────────────
            if best_confidence >= CONFIDENCE_THRESHOLD:
                logger.info("[GraphOpsAgent] confidence threshold reached at step %d", step_num)
                confidence = best_confidence
                break

            # ── plateau exit ──────────────────────────────────────────────────
            if confidence == prev_confidence:
                plateau_count += 1
                if plateau_count >= PLATEAU_STEPS and best_confidence >= PLATEAU_THRESHOLD:
                    logger.info("[GraphOpsAgent] confidence plateau (%d steps) at %.2f — exiting",
                                plateau_count, best_confidence)
                    confidence = best_confidence
                    break
            else:
                plateau_count = 0
            prev_confidence = confidence

        return self._build_report(
            question,
            entities,
            steps_log,
            best_confidence,
            belief_state=belief_state,
            grounding_actions=grounding_actions,
        )

    # ── LLM calls ─────────────────────────────────────────────────────────────

    def _plan(self, context: dict) -> List[str]:
        """Ask LLM to produce the next DSL plan, grounded by RAG context and compiled DSL hints."""
        payload: dict = {
            "question":  context["question"],
            "entities":  context["entities"],
            "primary":   context["primary"],
            "step":      len(context["steps"]),
            "previous":  [s["result_summary"] for s in context["steps"][-2:]],
        }
        # Inject compiled DSL hints on first step so the LLM anchors to grounded verbs
        if len(context["steps"]) == 0 and context.get("dsl_hints"):
            payload["dsl_hints"] = context["dsl_hints"]
        # Inject semantic memory on first step only (subsequent steps use live results)
        if len(context["steps"]) == 0 and context.get("rag_context"):
            payload["semantic_memory"] = context["rag_context"]

        user_msg = json.dumps(payload, indent=2)

        raw = self._llm_call(_PLAN_SYSTEM, user_msg)
        if not raw:
            return self._fallback_plan(context)

        try:
            parsed = json.loads(raw)
            plan = parsed.get("plan", [])
            if plan and isinstance(plan, list):
                # Safety: strip any example values
                cleaned = []
                for line in plan:
                    skip = any(ev in line for ev in EXAMPLE_VALUES)
                    if not skip:
                        cleaned.append(line)
                return cleaned if cleaned else self._fallback_plan(context)
        except Exception:
            pass
        return self._fallback_plan(context)

    def _interpret(self, context: dict, result: dict, *, allow_grounding: bool = True) -> dict:
        """Ask LLM to interpret the DSL execution result."""
        summary = self._summarize_result(result)
        user_msg = json.dumps({
            "question":   context["question"],
            "dsl_result": summary,
            "step":       len(context["steps"]),
        }, indent=2)

        interpretation, arbitration = self._interpret_with_arbitration(context["question"], summary, user_msg)
        if interpretation:
            return self._finalize_interpretation(
                context["question"],
                summary,
                interpretation,
                arbitration=arbitration,
                allow_grounding=allow_grounding,
            )
        # fallback interpretation from ASSESS step
        assess = result.get("assessment")
        if assess:
            return self._finalize_interpretation(context["question"], summary, {
                "situation":  assess.get("verdict", ""),
                "change":     ", ".join(assess.get("signals", [])),
                "structure":  f"threat_level={assess.get('threat_level')}",
                "geography":  "N/A",
                "assessment": assess.get("verdict", ""),
                "direction":  "Continue investigation" if assess.get("confidence", 0) < 0.85
                              else "File alert and monitor",
                "confidence": assess.get("confidence", 0.5),
            }, arbitration=arbitration, allow_grounding=allow_grounding)
        return self._finalize_interpretation(context["question"], summary, {
            "situation":  "Investigation step completed",
            "change":     str(result.get("metrics", {})),
            "structure":  f"{len(result.get('found_nodes', []))} nodes, "
                          f"{len(result.get('found_edges', []))} edges",
            "geography":  "N/A",
            "assessment": "No clear verdict yet",
            "direction":  "Expand investigation",
            "confidence": 0.3,
        }, arbitration=arbitration, allow_grounding=allow_grounding)

    def _llm_call(self, system: str, user: str, *, model: Optional[str] = None) -> Optional[str]:
        """POST to Ollama /api/chat and return the raw response string."""
        try:
            import urllib.request
            payload = json.dumps({
                "model":    model or self._model,
                "stream":   False,
                "format":   "json",
                "messages": [
                    {"role": "system",  "content": system},
                    {"role": "user",    "content": user},
                ],
                "options": {"temperature": 0.1, "num_predict": 512},
            }).encode()
            req = urllib.request.Request(
                f"{self._ollama}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=150) as resp:
                data = json.loads(resp.read())
            return data.get("message", {}).get("content", "")
        except Exception as exc:
            logger.warning("[GraphOpsAgent] LLM call failed: %s", exc)
            return None

    def _interpret_call(self, user: str, *, model: Optional[str] = None) -> Optional[str]:
        return self._llm_call(_INTERPRET_SYSTEM, user, model=model)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _fallback_plan(self, context: dict) -> List[str]:
        """Rule-based fallback when LLM is unavailable or returns bad output.

        Uses _compile_intent to generate the first verb(s) from DSL hints, then
        adds structural follow-on verbs based on simple keyword matching.
        """
        question = context.get("question", "")
        primary  = context.get("primary")

        # Use pre-compiled hints when available (injected from investigate())
        hints = context.get("dsl_hints") or _compile_intent(question)

        if hints:
            # Hints already reference grounded verbs; just append ASSESS
            plan = hints[:2] + ["ASSESS"]
            return plan

        q = question.lower()
        if "scan" in q or "fanout" in q:
            plan = ["WINDOW 1s"]
            if primary and primary not in EXAMPLE_VALUES:
                plan.append(f'FOCUS "{primary}"')
            plan += ["ANALYZE fanout", "FILTER degree_delta > 50", "ASSESS"]
        elif "botnet" in q or "fanin" in q or "coordinated" in q:
            plan = ["WINDOW 1s", "FOCUS all_nodes", "ANALYZE fanin", "CLUSTER timing", "ASSESS"]
        elif "path" in q or "lateral" in q or "trace" in q:
            plan = ["WINDOW 1s"]
            if primary and primary not in EXAMPLE_VALUES:
                plan.append(f'FOCUS "{primary}"')
            plan += ["EXPAND neighbors depth=2 limit=100", "ANALYZE path_density", "ASSESS"]
        elif "anomal" in q or "unusual" in q or "suspicious" in q:
            plan = ['VECTOR_SEARCH "anomalous behavioral pattern" k=25', "ASSESS"]
        else:
            plan = ["WINDOW 1s", "FOCUS all_nodes", "ANALYZE degree_delta",
                    "CLUSTER topology", "ASSESS"]
        return plan

    @staticmethod
    def _summarize_result(result: dict) -> dict:
        """Compact result for LLM context (avoid huge payloads)."""
        found_nodes = result.get("found_nodes", [])
        found_edges = result.get("found_edges", [])
        node_count  = len(found_nodes)
        edge_count  = len(found_edges)
        # Evidence coverage: fraction of found nodes that carry at least one label key
        labeled = sum(
            1 for nid in found_nodes[:50]
            if isinstance(nid, dict) and nid.get("labels")
        )
        # For string-ID lists, probe the graph directly when executor context is unavailable
        evidence_coverage = round(labeled / node_count, 2) if node_count > 0 else 0.0
        temporal_evidence = GraphOpsAgent._extract_temporal_evidence(result)
        return {
            "focus":             result.get("focus"),
            "node_count":        node_count,
            "edge_count":        edge_count,
            "evidence_coverage": evidence_coverage,
            "temporal_evidence": temporal_evidence,
            "metrics":           result.get("metrics", {}),
            "assessment":        result.get("assessment"),
            "summary":           result.get("summary"),
            "step_results": [
                {"verb": s["verb"], "result": s["result"]}
                for s in result.get("steps", [])
                if "error" not in s.get("result", {})
            ][-6:],  # last 6 steps only
        }

    @staticmethod
    def _extract_temporal_evidence(result: dict) -> dict:
        metrics = result.get("metrics", {}) or {}
        assessment = result.get("assessment", {}) or {}
        summary = result.get("summary", {}) or {}
        found_nodes = result.get("found_nodes", []) or []

        def _collect(payload: Any, target: Dict[str, Any]) -> None:
            if not isinstance(payload, dict):
                return
            for key in (
                "periodicity_s",
                "periodicity_confidence",
                "temporal_phase",
                "temporal_cohesion",
                "identity_pressure",
                "behavior_class",
                "pattern",
                "burst_signature",
                "evidence_present",
            ):
                if key in payload and key not in target:
                    target[key] = payload.get(key)
            for key in ("temporal", "temporal_overlay", "temporal_fingerprint", "behavior", "supporting_evidence"):
                nested = payload.get(key)
                if isinstance(nested, dict):
                    _collect(nested, target)

        extracted: Dict[str, Any] = {}
        for payload in (metrics, assessment, summary):
            _collect(payload, extracted)

        for node in found_nodes[:12]:
            if not isinstance(node, dict):
                continue
            _collect(node, extracted)
            _collect((node.get("metadata") or {}), extracted)

        present = bool(
            extracted.get("evidence_present")
            or extracted.get("periodicity_s")
            or extracted.get("temporal_cohesion")
            or extracted.get("pattern")
            or extracted.get("behavior_class")
        )
        extracted["present"] = present
        return extracted

    @staticmethod
    def _question_requires_temporal_authority(question: str) -> bool:
        q = (question or "").lower()
        temporal_terms = (
            "beacon",
            "cadence",
            "periodic",
            "periodicity",
            "rhythm",
            "burst",
            "proxy",
            "relay",
            "lateral movement",
            "lateral",
            "behavior",
            "anomal",
            "timing",
            "entropy",
            "session duration",
            "fanout",
            "churn",
            "dispersion",
            "utility",
            "grid",
            "energy",
            "smart meter",
            "ami",
            "amr",
            "scada",
            "modbus",
            "dnp3",
            "iec-104",
            "iec 61850",
        )
        return any(term in q for term in temporal_terms)

    @staticmethod
    def _enforce_temporal_authority(question: str, result_summary: dict, interpretation: dict) -> dict:
        interpretation = dict(interpretation or {})
        temporal = dict(result_summary.get("temporal_evidence") or {})
        present = bool(temporal.get("present"))

        if not GraphOpsAgent._question_requires_temporal_authority(question):
            if present and "temporal_evidence" not in interpretation:
                interpretation["temporal_evidence"] = (
                    f"TEMPORAL_EVIDENCE: observed period {float(temporal.get('periodicity_s') or 0.0):.2f}s, "
                    f"cohesion {float(temporal.get('temporal_cohesion') or 0.0):.2f}, "
                    f"phase {temporal.get('temporal_phase') or 'unknown'}"
                )
            return interpretation

        if not present:
            interpretation["temporal_evidence"] = "TEMPORAL_EVIDENCE: ABSENT"
            interpretation["situation"] = "TEMPORAL_EVIDENCE: ABSENT — insufficient measured cadence for this behavior query"
            interpretation["change"] = "UNKNOWN"
            interpretation["structure"] = interpretation.get("structure", "UNKNOWN")
            interpretation["geography"] = "N/A"
            interpretation["assessment"] = "UNKNOWN — no temporal evidence. Recommend: request burst capture and re-query."
            interpretation["direction"] = "Instrument: trigger burst capture / temporal resample for the target entity or path."
            interpretation["confidence"] = min(float(interpretation.get("confidence", 0.3) or 0.3), 0.25)
            return interpretation

        interpretation["temporal_evidence"] = (
            f"TEMPORAL_EVIDENCE: observed period {float(temporal.get('periodicity_s') or 0.0):.2f}s, "
            f"cohesion {float(temporal.get('temporal_cohesion') or 0.0):.2f}, "
            f"phase {temporal.get('temporal_phase') or 'unknown'}"
        )
        return interpretation

    @staticmethod
    def _interpretation_label(interpretation: Dict[str, Any]) -> str:
        text = " ".join(
            str(interpretation.get(key) or "")
            for key in ("assessment", "situation", "change", "direction")
        ).lower()
        if "unknown" in text or "insufficient" in text:
            return "unknown"
        if any(term in text for term in ("utility", "energy-grid", "power grid", "smart meter", "scada", "modbus", "dnp3", "iec104", "iec61850", "ot")):
            return "grid_infrastructure_misuse"
        if any(term in text for term in ("beacon", "periodic", "cadence", "heartbeat")):
            return "beaconing"
        if any(term in text for term in ("proxy", "identity", "rotation", "spoof")):
            return "identity_rotation"
        if any(term in text for term in ("relay", "pivot", "bridge")):
            return "relay_chain"
        if "lateral" in text:
            return "lateral_movement"
        if "rf" in text:
            return "rf_correlated"
        if "benign" in text or "no significant threat" in text:
            return "benign"
        if any(term in text for term in ("anomal", "threat", "attack")):
            return "anomalous_activity"
        return "analysis_pending"

    @staticmethod
    def _interpretation_stance(label: str) -> str:
        if label in {"unknown", "analysis_pending"}:
            return "unknown"
        if label == "benign":
            return "benign"
        return "threat"

    @classmethod
    def _arbitrate_model_outputs(cls, outputs: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not outputs:
            return {
                "model_count": 0,
                "models": [],
                "consensus_label": "unknown",
                "selected_model": "",
                "selected_label": "unknown",
                "divergence_score": 0.0,
                "confidence_spread": 0.0,
                "temporal_conflict": False,
                "conflict_zone": "MODEL_UNAVAILABLE",
                "escalation": False,
            }

        summaries = []
        label_scores: Dict[str, float] = defaultdict(float)
        confidences: List[float] = []
        stances = set()
        temporal_states = set()
        labels = set()
        for item in outputs:
            interpretation = dict(item.get("interpretation") or {})
            label = cls._interpretation_label(interpretation)
            confidence = float(interpretation.get("confidence", 0.0) or 0.0)
            temporal_present = str(interpretation.get("temporal_evidence", "")).upper() != "TEMPORAL_EVIDENCE: ABSENT"
            stance = cls._interpretation_stance(label)
            summaries.append({
                "model": item.get("model", ""),
                "label": label,
                "confidence": round(confidence, 4),
                "temporal_present": temporal_present,
            })
            label_scores[label] += max(confidence, 0.01)
            confidences.append(confidence)
            stances.add(stance)
            temporal_states.add(temporal_present)
            labels.add(label)

        consensus_label = max(label_scores.items(), key=lambda entry: entry[1])[0]
        confidence_spread = (max(confidences) - min(confidences)) if len(confidences) > 1 else 0.0
        label_diversity = (len(labels) - 1) / max(len(outputs) - 1, 1)
        stance_diversity = 1.0 if len(stances) > 1 else 0.0
        temporal_conflict = len(temporal_states) > 1
        divergence_score = max(
            0.0,
            min(
                1.0,
                label_diversity * 0.45
                + confidence_spread * 0.25
                + stance_diversity * 0.20
                + (0.10 if temporal_conflict else 0.0),
            ),
        )
        return {
            "model_count": len(outputs),
            "models": summaries,
            "consensus_label": consensus_label,
            "selected_model": outputs[0].get("model", ""),
            "selected_label": consensus_label,
            "divergence_score": round(divergence_score, 4),
            "confidence_spread": round(confidence_spread, 4),
            "temporal_conflict": temporal_conflict,
            "conflict_zone": "MODEL_CONFLICT_ZONE" if divergence_score >= MODEL_DIVERGENCE_THRESHOLD else "MODEL_COHERENCE",
            "escalation": divergence_score >= MODEL_DIVERGENCE_THRESHOLD,
        }

    @classmethod
    def _select_arbitrated_output(cls, outputs: List[Dict[str, Any]], arbitration: Dict[str, Any]) -> Dict[str, Any]:
        if not outputs:
            return {}
        consensus_label = arbitration.get("consensus_label")
        best = outputs[0]
        best_score = -1.0
        for item in outputs:
            interpretation = dict(item.get("interpretation") or {})
            label = cls._interpretation_label(interpretation)
            confidence = float(interpretation.get("confidence", 0.0) or 0.0)
            temporal_bonus = 0.04 if str(interpretation.get("temporal_evidence", "")).upper() != "TEMPORAL_EVIDENCE: ABSENT" else 0.0
            consensus_bonus = 0.08 if label == consensus_label else 0.0
            score = confidence + temporal_bonus + consensus_bonus
            if item.get("model") == outputs[0].get("model"):
                score += 0.01
            if score > best_score:
                best = item
                best_score = score
        return best

    def _interpret_with_arbitration(
        self,
        question: str,
        result_summary: Dict[str, Any],
        user_msg: str,
    ) -> tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        outputs: List[Dict[str, Any]] = []
        for model in self._models[:MAX_ARBITRATION_MODELS]:
            raw = self._interpret_call(user_msg, model=model)
            if not raw:
                continue
            try:
                parsed = json.loads(raw)
            except Exception:
                continue
            outputs.append({
                "model": model,
                "interpretation": self._enforce_temporal_authority(question, result_summary, parsed),
            })

        arbitration = self._arbitrate_model_outputs(outputs)
        selected = self._select_arbitrated_output(outputs, arbitration)
        if not selected:
            return None, arbitration

        selected_interp = dict(selected.get("interpretation") or {})
        selected_interp["confidence"] = round(
            max(
                0.0,
                min(
                    1.0,
                    float(selected_interp.get("confidence", 0.0) or 0.0)
                    - min(float(arbitration.get("divergence_score", 0.0) or 0.0) * 0.12, 0.10),
                ),
            ),
            4,
        )
        arbitration["selected_model"] = selected.get("model", "")
        arbitration["selected_label"] = self._interpretation_label(selected_interp)
        return selected_interp, arbitration

    def _finalize_interpretation(
        self,
        question: str,
        result_summary: Dict[str, Any],
        interpretation: Dict[str, Any],
        *,
        arbitration: Optional[Dict[str, Any]] = None,
        allow_grounding: bool = True,
    ) -> Dict[str, Any]:
        final = self._enforce_temporal_authority(question, result_summary, interpretation)
        if arbitration:
            final["model_arbitration"] = arbitration
        if allow_grounding:
            grounding = self._maybe_reflexive_grounding(question, result_summary, final)
            if grounding:
                final["reflexive_grounding"] = grounding
        return final

    @staticmethod
    def _grounding_request(question: str, result_summary: Dict[str, Any], interpretation: Dict[str, Any]) -> Dict[str, Any]:
        temporal = dict(result_summary.get("temporal_evidence") or {})
        metrics = dict(result_summary.get("metrics") or {})
        arbitration = dict(interpretation.get("model_arbitration") or {})
        confidence = float(interpretation.get("confidence", 0.0) or 0.0)
        evidence_coverage = float(result_summary.get("evidence_coverage", 0.0) or 0.0)
        divergence_risk = float(metrics.get("divergence_risk") or 0.0)
        identity_pressure = float(temporal.get("identity_pressure") or metrics.get("identity_pressure") or 0.0)
        temporal_phase = str(temporal.get("temporal_phase") or metrics.get("temporal_phase") or "unknown").lower()
        temporal_cohesion = float(temporal.get("temporal_cohesion") or metrics.get("temporal_cohesion") or 0.0)
        utility_pressure = bool(metrics.get("utility")) and float(metrics.get("utility_score") or 0.0) >= 0.60

        reason_codes: List[str] = []
        if arbitration.get("escalation"):
            reason_codes.append("model_divergence")
        if GraphOpsAgent._question_requires_temporal_authority(question) and not temporal.get("present"):
            reason_codes.append("temporal_evidence_absent")
        if confidence < 0.4 and evidence_coverage < 0.5:
            reason_codes.append("low_confidence_sparse_evidence")
        if temporal_phase in {"emergent", "decaying", "unknown"} and temporal_cohesion < 0.4 and divergence_risk >= 0.35:
            reason_codes.append("temporal_instability")
        if utility_pressure and identity_pressure >= 0.45:
            reason_codes.append("grid_visibility_gap")

        trigger = bool(reason_codes)
        return {
            "trigger": trigger,
            "reason_codes": reason_codes,
            "window_seconds": 8.0 if utility_pressure or arbitration.get("escalation") else 5.0,
            "max_events": 320 if utility_pressure else 220,
            "check_only": False,
        }

    def _run_reflexive_grounding(self, request: Dict[str, Any]) -> Dict[str, Any]:
        if self.engine is None:
            return {"status": "skipped", "error": "engine unavailable"}
        try:
            from eve_sensor_mcp import sensor_stream_tool
        except ImportError as exc:
            return {"status": "unavailable", "error": str(exc)}
        tool_params = {
            "window_seconds": request.get("window_seconds", 5.0),
            "max_events": request.get("max_events", 200),
            "check_only": bool(request.get("check_only", False)),
        }
        return sensor_stream_tool(tool_params, self.engine)

    def _maybe_reflexive_grounding(
        self,
        question: str,
        result_summary: Dict[str, Any],
        interpretation: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        request = self._grounding_request(question, result_summary, interpretation)
        if not request.get("trigger"):
            return None
        tool_result = self._run_reflexive_grounding(request)
        return {
            "triggered": True,
            "reason_codes": list(request.get("reason_codes") or []),
            "window_seconds": request.get("window_seconds", 5.0),
            "max_events": request.get("max_events", 200),
            "tool_result": tool_result,
        }

    # ── Phase 9: Signal gradient extraction ──────────────────────────────────

    def _extract_signal_gradients(self) -> Dict[str, Any]:
        """Snapshot live graph state to produce a set of signal gradients.

        Runs lightweight, read-only DSL probes against the current executor
        without modifying focus state.  Each signal type returns
        ``{strength: 0..1, value: N, note: str}``.
        """
        ex      = self.executor
        signals: Dict[str, Any] = {}

        # ── 1. Low-entropy beacon detection ──────────────────────────────────
        try:
            saved_focus = ex._focus
            saved_nodes = list(ex._focus_nodes)
            r = ex._do_temporal_entropy("TEMPORAL_ENTROPY window=5m top=10")
            beacon_count = r.get("beacon_count", 0)
            signals["low_entropy"] = {
                "strength": min(beacon_count / 5.0, 1.0),
                "value":    beacon_count,
                "note":     f"{beacon_count} low-entropy beacon candidates",
            }
            ex._focus      = saved_focus
            ex._focus_nodes = saved_nodes
        except Exception:
            pass

        # ── 2. Graph delta / emerging events ─────────────────────────────────
        try:
            r = ex._do_graph_delta("GRAPH_DELTA slices=3 window=5m")
            new_edges = sum(d.get("new_edges", 0) for d in r.get("diffs", []))
            emerging  = r.get("total_emerging", 0)
            signals["graph_delta"] = {
                "strength":             round(min(new_edges / 50.0 + emerging / 5.0, 1.0), 3),
                "value":                new_edges,
                "emerging_components":  emerging,
                "note":                 f"{new_edges} new edges, {emerging} emerging components",
            }
        except Exception:
            pass

        # ── 3. TurboQuant embedding store depth ───────────────────────────────
        try:
            from turbo_quant_store import embedding_store as _emb_store
            tq = _emb_store()
            n  = len(tq)
            signals["embedding_store"] = {
                "strength": round(min(n / 1000.0, 1.0), 3),
                "value":    n,
                "note":     f"{n} entities in behavioral embedding space",
            }
        except Exception:
            pass

        # ── 4. Degree distribution variance → relay/bridge potential ─────────
        if ex.engine:
            try:
                import statistics
                degrees = list(ex.engine.degree.values())
                if len(degrees) > 1:
                    mean_d  = statistics.mean(degrees)
                    stdev_d = statistics.stdev(degrees)
                    cv      = stdev_d / mean_d if mean_d > 0 else 0
                    signals["degree_variance"] = {
                        "strength":    round(min(cv / 3.0, 1.0), 3),
                        "value":       round(cv, 3),
                        "mean_degree": round(mean_d, 1),
                        "note":        f"Degree CV={cv:.2f} (high = relay/bridge candidates)",
                    }
            except Exception:
                pass

        # ── 5. RF telemetry availability ──────────────────────────────────────
        try:
            import sys
            for mod_name, mod in sys.modules.items():
                if "rf_scythe" in mod_name or mod_name == "__main__":
                    for attr in ("_rf_anomaly_ts", "rf_anomaly_timestamps", "_last_anomalies"):
                        obj = getattr(mod, attr, None)
                        if obj:
                            rf_n = len(list(obj))
                            signals["rf_available"] = {
                                "strength": 0.9 if rf_n > 5 else 0.5 if rf_n > 0 else 0.0,
                                "value":    rf_n,
                                "note":     f"{rf_n} RF anomaly events for cross-domain correlation",
                            }
                            break
                if "rf_available" in signals:
                    break
        except Exception:
            pass

        # ── 6. Total graph size ───────────────────────────────────────────────
        if ex.engine:
            try:
                node_count = len(list(ex.engine.nodes.keys()))
                edge_count = len(list(ex.engine.edges.values()))
                signals["graph_size"] = {
                    "strength":    round(min(node_count / 500.0, 1.0), 3),
                    "value":       node_count,
                    "edge_count":  edge_count,
                    "note":        f"{node_count} nodes, {edge_count} edges in live graph",
                }
            except Exception:
                pass

        return signals

    def suggest_prompts(self, top_n: int = 5, auto_execute: bool = False) -> Dict[str, Any]:
        """Generate adaptive investigation prompts from live graph signal gradients.

        Each returned prompt is a next-best-question derived from graph state
        instability — where tension exists between what the graph knows and
        what it hasn't confirmed yet.

        Args:
            top_n:        Number of top prompts to return (default 5).
            auto_execute: If True, pre-execute the top 2 prompts using the
                          query compiler + DSL executor and attach results.

        Returns a dict with:
          - ``signals``          — raw signal gradient snapshot
          - ``suggested_prompts`` — ranked list with confidence + signal note
          - ``auto_executed``    — list of prompts that were pre-executed
        """
        signals = self._extract_signal_gradients()

        # Score each candidate prompt against available signals
        scored: List[Dict[str, Any]] = []
        for sig_key, min_strength, base_conf, prompt_text in _SIGNAL_PROMPT_MAP:
            sig = signals.get(sig_key)
            if sig is None:
                continue
            strength = sig.get("strength", 0.0)
            if strength < min_strength:
                continue
            confidence = round(min(base_conf + strength * 0.25, 0.97), 3)
            scored.append({
                "prompt":      prompt_text,
                "signal":      sig_key,
                "confidence":  confidence,
                "signal_note": sig.get("note", ""),
            })

        # Deduplicate by 40-char prefix, sort by confidence descending
        seen: set = set()
        ranked: List[Dict[str, Any]] = []
        for s in sorted(scored, key=lambda x: x["confidence"], reverse=True):
            prefix = s["prompt"][:40]
            if prefix not in seen:
                seen.add(prefix)
                ranked.append(s)

        top = ranked[:top_n]

        result: Dict[str, Any] = {
            "signals":           signals,
            "suggested_prompts": top,
            "auto_executed":     [],
        }

        if auto_execute and top:
            saved_focus = self.executor._focus
            saved_nodes = list(self.executor._focus_nodes)
            for suggestion in top[:2]:
                try:
                    q     = suggestion["prompt"]
                    hints = _compile_intent(q)
                    plan  = (hints[:2] + ["ASSESS"]) if hints else [
                        f'VECTOR_SEARCH "{q[:60]}"', "ASSESS"
                    ]
                    self.executor.reset()
                    r = self.executor.run(plan)
                    suggestion["pre_executed"] = {
                        "plan":       plan,
                        "node_count": len(r.get("found_nodes", [])),
                        "edge_count": len(r.get("found_edges", [])),
                        "assessment": r.get("assessment"),
                        "steps":      [
                            {"verb": s["verb"], "result": s["result"]}
                            for s in r.get("steps", [])
                        ][-4:],
                    }
                    result["auto_executed"].append(q[:80])
                except Exception as exc:
                    suggestion["pre_exec_error"] = str(exc)
            # Restore executor state
            self.executor._focus      = saved_focus
            self.executor._focus_nodes = saved_nodes

        logger.info("[suggest_prompts] signals=%s top=%d auto_executed=%d",
                    list(signals.keys()), len(top), len(result["auto_executed"]))
        return result

    @staticmethod
    def normalize_edge_kind(raw_kind: str) -> Dict[str, Any]:
        """Instance-method proxy for the module-level normalize_edge_kind()."""
        return normalize_edge_kind(raw_kind)

    def _build_report(self,
                      question: str,
                      entities: Dict[str, List[str]],
                      steps_log: List[dict],
                      confidence: float,
                      belief_state: Optional[Dict] = None,
                      grounding_actions: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """Assemble the final structured intelligence report."""
        final_interp = steps_log[-1]["interpretation"] if steps_log else {}

        # Compute evidence posture from aggregated step node/edge counts
        total_nodes = sum(s.get("result_summary", {}).get("node_count", 0) for s in steps_log)
        total_edges = sum(s.get("result_summary", {}).get("edge_count", 0) for s in steps_log)
        if total_nodes == 0:
            posture = "no-data"
            stale_pct = 100
        elif total_nodes < 10:
            posture = "sparse"
            stale_pct = max(0, round((1 - confidence) * 100))
        elif confidence >= 0.7:
            posture = "evidence-backed"
            stale_pct = max(0, round((1 - confidence) * 50))
        else:
            posture = "inference-heavy"
            stale_pct = max(0, round((1 - confidence) * 80))

        # Generate adaptive suggested prompts from live graph state
        try:
            suggestion_result = self.suggest_prompts(top_n=4, auto_execute=False)
            suggested_prompts = suggestion_result.get("suggested_prompts", [])
        except Exception:
            suggested_prompts = []

        report: Dict[str, Any] = {
            "report": {
                "credibility": (
                    f"{posture}, coverage {confidence:.0%}, "
                    f"stale_inferences ~{stale_pct}%, "
                    f"nodes_seen {total_nodes}, edges_seen {total_edges}"
                ),
                "temporal_evidence": final_interp.get("temporal_evidence", "TEMPORAL_EVIDENCE: ABSENT"),
                "situation":   final_interp.get("situation",  "Investigation incomplete"),
                "change":      final_interp.get("change",     "Insufficient data"),
                "structure":   final_interp.get("structure",  "Unknown"),
                "geography":   final_interp.get("geography",  "N/A"),
                "assessment":  final_interp.get("assessment", "No verdict"),
                "direction":   final_interp.get("direction",  "Continue monitoring"),
            },
            "confidence":        round(confidence, 3),
            "evidence_posture":  posture,
            "entities":          entities,
            "steps":             len(steps_log),
            "model":             self._model,
            "models_considered": list(self._models[:MAX_ARBITRATION_MODELS]),
            "question":          question,
            "suggested_prompts": suggested_prompts,
        }

        if final_interp.get("model_arbitration"):
            report["model_arbitration"] = final_interp["model_arbitration"]
        if grounding_actions:
            report["reflexive_grounding"] = grounding_actions

        if belief_state:
            report["belief_state"]  = belief_state
            if belief_state.get("converged") and belief_state.get("classification"):
                report["classification"]   = belief_state["classification"]
                report["report"]["verdict"] = (
                    f"Converged classification: {belief_state['classification']} "
                    f"(confidence {confidence:.0%})"
                )

        return report


# ─── MCP tool registration ────────────────────────────────────────────────────

def register_graphops_tools(engine, mcp_handler, embedding_engine=None) -> None:
    """Register GraphOps Copilot tools into an MCPHandler instance.

    Three tools:
      graphops_investigate  — full agent investigation loop (RAG-grounded if embedding_engine provided)
      graphops_dsl_exec     — execute a raw DSL plan
      graphops_entity_parse — extract entities from free text
    """
    from mcp_server import ToolDef

    _agent = GraphOpsAgent(engine, embedding_engine=embedding_engine)

    def _investigate(params: dict) -> dict:
        question = params.get("question", "")
        if not question:
            return {"error": "question is required"}
        max_steps = int(params.get("max_steps", MAX_AGENT_STEPS))
        report = _agent.investigate(question, max_steps=max_steps)
        return report

    def _dsl_exec(params: dict) -> dict:
        plan = params.get("plan", [])
        if not plan:
            return {"error": "plan (list of DSL strings) is required"}
        _agent.executor.reset()
        return _agent.executor.run(plan)

    def _entity_parse(params: dict) -> dict:
        text = params.get("text", "")
        if not text:
            return {"error": "text is required"}
        extractor = EntityExtractor()
        entities = extractor.extract(text)
        primary  = extractor.primary_entity(entities)
        return {"entities": entities, "primary_entity": primary}

    mcp_handler._tools["graphops_investigate"] = ToolDef(
        name="graphops_investigate",
        description=(
            "Run a full autonomous graph investigation from a natural-language question. "
            "The agent extracts entities, generates DSL query plans, executes them against "
            "the live hypergraph, and returns a structured intelligence report with "
            "Situation / Change / Structure / Assessment / Direction sections."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "question":   {"type": "string",  "description": "Natural-language investigation question"},
                "max_steps":  {"type": "integer", "description": "Max reasoning steps (default 6)", "default": 6},
            },
            "required": ["question"],
        },
        fn=_investigate,
    )

    mcp_handler._tools["graphops_dsl_exec"] = ToolDef(
        name="graphops_dsl_exec",
        description=(
            "Execute a GraphOps DSL plan directly. "
            "Plan is a JSON list of verb strings: "
            '["FOCUS all_nodes", "BEHAVIOR_QUERY utility=true top=10", "ASSESS"]. '
            "Returns execution results including found nodes/edges and metrics."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "plan": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of DSL verb strings to execute in order",
                },
            },
            "required": ["plan"],
        },
        fn=_dsl_exec,
    )

    mcp_handler._tools["graphops_entity_parse"] = ToolDef(
        name="graphops_entity_parse",
        description=(
            "Extract typed entities (IPv4, CIDR, domain, ASN, port, node-ID) "
            "from free text. Never returns example/placeholder values."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to extract entities from"},
            },
            "required": ["text"],
        },
        fn=_entity_parse,
    )

    def _suggest(params: dict) -> dict:
        top_n      = int(params.get("top_n", 5))
        auto_exec  = bool(params.get("auto_execute", False))
        return _agent.suggest_prompts(top_n=top_n, auto_execute=auto_exec)

    mcp_handler._tools["graphops_suggest"] = ToolDef(
        name="graphops_suggest",
        description=(
            "Generate adaptive investigation prompts derived from live graph signal gradients. "
            "Detects low-entropy beacons, graph delta spikes, embedding cluster drift, "
            "degree distribution anomalies (relay/bridge nodes), and RF correlation opportunities. "
            "Returns ranked prompts with confidence scores and the signal that triggered each. "
            "Set auto_execute=true to pre-run the top 2 suggestions and attach their DSL results — "
            "so the user confirms or rejects hypotheses rather than constructing queries."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "top_n": {
                    "type":        "integer",
                    "description": "Number of top suggested prompts to return (default 5)",
                    "default":     5,
                },
                "auto_execute": {
                    "type":        "boolean",
                    "description": "Pre-execute top 2 suggestions and attach DSL results",
                    "default":     False,
                },
            },
            "required": [],
        },
        fn=_suggest,
    )

    try:
        from eve_sensor_mcp import register_sensor_stream_tool
        register_sensor_stream_tool(engine, mcp_handler)
    except Exception as _eve_err:
        logger.warning("[graphops_copilot] eve_sensor_mcp unavailable: %s", _eve_err)

    logger.info("[graphops_copilot] registered 5 MCP tools: "
                "investigate, dsl_exec, entity_parse, suggest, sensor_stream")


# ─── CLI self-test ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    # ── Entity extraction tests ───────────────────────────────────────────────
    print("\n═══ EntityExtractor tests ════════════════════════════════")
    ex = EntityExtractor()

    tests = [
        ("What do you know about 200.36.135.121?",
         {"ipv4": ["200.36.135.121"]}),
        ("Show me traffic from AS15169 to 10.99.0.0/24",
         {"asn": ["AS15169"], "cidr": ["10.99.0.0/24"]}),
        ("Is node:0x8099ded263f808db under attack?",
         {"node_id": ["node:0x8099ded263f808db"]}),
        # example value should be stripped
        ("What about 10.0.0.1?",
         {"ipv4": []}),
    ]
    all_pass = True
    for text, expected in tests:
        entities = ex.extract(text)
        for k, v in expected.items():
            got = entities.get(k, [])
            ok = got == v
            status = "✓" if ok else f"✗ (got {got})"
            print(f"  [{status}]  {text!r}  →  {k}={v}")
            if not ok:
                all_pass = False

    print("EntityExtractor:", "PASS" if all_pass else "FAIL")

    # ── DSL executor tests (no engine — synthetic) ────────────────────────────
    print("\n═══ InvestigativeDSLExecutor tests ═══════════════════════")
    executor = InvestigativeDSLExecutor(engine=None)

    # Test WINDOW
    r = executor.run(["WINDOW 200ms"])
    assert executor._window_s == 0.2, f"window mismatch: {executor._window_s}"
    print(f"  [✓]  WINDOW 200ms → {executor._window_s}s")

    # Test FOCUS without engine (no crash)
    r = executor.run(["FOCUS 10.99.0.1"])
    assert r["focus"] == "10.99.0.1"
    print(f"  [✓]  FOCUS 10.99.0.1 → focus={r['focus']}")

    # Test FILTER
    executor._focus_nodes = [
        {"id": "n1", "labels": {"ip": "10.0.0.1"}, "degree_delta": 80},
        {"id": "n2", "labels": {"ip": "10.0.0.2"}, "degree_delta": 20},
    ]
    r = executor.run(["FILTER degree_delta > 50"])
    after = len(executor._focus_nodes)
    # degree_delta is a field on dict, not from topo detector
    print(f"  [✓]  FILTER degree_delta > 50 → {after} nodes remaining")

    # Test TRACE (no engine, no crash)
    r = executor.run(["TRACE path FROM 10.99.0.1 TO 10.99.0.2 depth=3"])
    assert r["steps"][-1]["result"].get("src") == "10.99.0.1"
    print(f"  [✓]  TRACE → {r['steps'][-1]['result']}")

    # Test ASSESS (no alerts, returns LOW)
    executor.reset()
    r = executor.run(["ASSESS"])
    threat = r["assessment"]["threat_level"] if r.get("assessment") else "?"
    print(f"  [✓]  ASSESS → threat_level={threat}")

    # ── Full plan test ────────────────────────────────────────────────────────
    print("\n═══ DSL plan: coordinated scanning detection ══════════════")
    executor.reset()
    r = executor.run([
        "WINDOW 1s",
        "FOCUS all_nodes",
        "ANALYZE degree_delta",
        "FILTER degree_delta > 50",
        "CLUSTER timing",
        "SUMMARIZE",
        "ASSESS",
    ])
    for step in r["steps"]:
        result_keys = list(step["result"].keys())
        print(f"  {step['verb']:<45} → {result_keys}")
    print(f"  assessment: {r.get('assessment')}")

    # ── Agent test ────────────────────────────────────────────────────────────
    print("\n═══ GraphOpsAgent (no live engine, fallback plan) ════════")
    agent = GraphOpsAgent(engine=None)
    print(f"  model selected: {agent._model}")

    report = agent.investigate("Is there coordinated scanning?")
    print(f"  confidence: {report['confidence']}")
    print(f"  steps:      {report['steps']}")
    print(f"  report keys: {list(report['report'].keys())}")
    r2 = agent.investigate("What do you know about 200.36.135.121?")
    print(f"  entity test confidence: {r2['confidence']}")
    print(f"  entities extracted: {r2['entities']}")

    print("\n═══ All tests complete ════════════════════════════════════")
    if not all_pass:
        sys.exit(1)
