"""
tak_ml_gemma_runner.py — TAK-ML model runner that wraps Gemma 3 (via Ollama)
and emits GraphOps through WriteBus.

Pipeline:
    HypergraphEngine snapshot
      → per-flow/host context extraction
      → Gemma 3 structured inference (schema-bound JSON)
      → validate + convert to GraphOps
      → WriteBus.commit()
      → HypergraphEngine mints inferred edges
      → Cesium / TAK-GPT / TAK map overlays update live

Designed to run as:
  - A TAK-ML plugin-style model runner (offline/edge-capable)
  - A server-side batch enrichment step in /api/infer/run
  - A per-event hook on the GraphEventBus

Usage:
    from tak_ml_gemma_runner import TakMlGemmaRunner, GemmaRunnerConfig
    runner = TakMlGemmaRunner(hypergraph_engine)
    runner.run_for_all_flows(limit=500)

    # Or for a single flow:
    runner.run_for_flow("flow:session123:abc")

    # Or batch mode returning ops (for API integration):
    ops = runner.run_batch_return_ops(limit=100)
"""
from __future__ import annotations

import functools
import hashlib
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple
from takml_runtime_metrics import (
    get_takml_runtime_metrics,
    get_takml_runtime_metrics_tracker,
)

logger = logging.getLogger(__name__)

_FORBIDDEN_GEO_TERMS = frozenset({
    "dallas",
    "texas",
    "brazil",
    "europe",
    "european border",
    "southern hemisphere",
    "northern latitudes",
    "equator",
    "atlantic",
    "pacific",
})

_INTERNAL_LEAK_PATTERNS = (
    r'^\s*EPISTEMIC RULES:.*$',
    r'^\s*RULES:.*$',
    r'^\s*BELIEF CHANGES:.*$',
    r'^\s*BELIEF_DRIFT:.*$',
    r'^\s*LEDGER_STATE:.*$',
    r'^\s*MCP_CONTEXT:.*$',
    r'^\s*MCP_FOCUS.*$',
    r'^\s*WRITE_SUMMARY:.*$',
)


class PromptMode(str, Enum):
    QUERY = "QUERY"
    COMPUTE = "COMPUTE"
    ANALYSIS = "ANALYSIS"
    HYPOTHESIS = "HYPOTHESIS"


@dataclass(frozen=True)
class CompiledPrompt:
    """Mode-isolated chat prompt artifact for the analyst surface."""
    mode: PromptMode
    system_prompt: str
    user_prompt: str
    allow_dsl: bool = False
    require_unknown_on_gap: bool = True
    entity_set: Set[str] = field(default_factory=set)
    allowed_geo_terms: Set[str] = field(default_factory=set)
    source_query: str = ""


@dataclass(frozen=True)
class FirewallResult:
    response: str
    violation_codes: Tuple[str, ...] = ()
    rewritten: bool = False


class GraphOpsOutputFirewall:
    """Reject/rewrite analyst-surface output that escapes the runtime contract."""

    _SECTION_PATTERN = re.compile(
        r'^\s*(SITUATION|CHANGE|STRUCTURE|GEOGRAPHY|ASSESSMENT|DIRECTION|ANALYZE):\s*',
        re.IGNORECASE | re.MULTILINE,
    )
    _DSL_PATTERN = re.compile(
        r'^\s*(FIND|QUERY|SELECT|REPORT)\s+(NODES|EDGES|NEIGHBORS|SUBGRAPH|NODE|EDGE|SOURCES|TYPES)\b',
        re.IGNORECASE | re.MULTILINE,
    )
    _IP_PATTERN = re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b')
    _CAMEL_PATTERN = re.compile(r'\b[A-Z][a-z]+(?:[A-Z][a-z]+){1,}\b')
    _TITLE_PATTERN = re.compile(r'\b[A-Z][a-z]{3,}(?:\s+[A-Z][a-z]{3,})+\b')

    def __init__(self, entity_set: Optional[Set[str]] = None, allowed_geo_terms: Optional[Set[str]] = None):
        self.entity_set = {self._normalize_token(v) for v in (entity_set or set()) if v}
        self.allowed_geo_terms = {self._normalize_token(v) for v in (allowed_geo_terms or set()) if v}

    @staticmethod
    def _normalize_token(value: str) -> str:
        return re.sub(r'\s+', ' ', str(value).strip()).lower()

    def evaluate(self, raw_response: str, compiled_prompt: CompiledPrompt) -> FirewallResult:
        response = (raw_response or "").strip()
        if not response:
            return FirewallResult(
                response=self._unknown_response(
                    compiled_prompt,
                    "Model returned no grounded result.",
                    ("retry with a narrower graph-backed query", "collect fresh sensor evidence"),
                ),
                violation_codes=("empty_response",),
                rewritten=True,
            )

        violations: List[str] = []
        sanitized = response

        for pattern in _INTERNAL_LEAK_PATTERNS:
            if re.search(pattern, sanitized, re.IGNORECASE | re.MULTILINE):
                violations.append("internal_prompt_leak")
                sanitized = re.sub(pattern, "", sanitized, flags=re.IGNORECASE | re.MULTILINE)

        if self._SECTION_PATTERN.search(sanitized):
            violations.append("legacy_narrative_surface")
            sanitized = self._SECTION_PATTERN.sub("", sanitized)

        if compiled_prompt.mode != PromptMode.QUERY and self._DSL_PATTERN.search(sanitized):
            violations.append("unexpected_dsl")
            sanitized = self._DSL_PATTERN.sub("", sanitized)

        if compiled_prompt.mode == PromptMode.QUERY and not compiled_prompt.allow_dsl and self._DSL_PATTERN.search(sanitized):
            violations.append("forbidden_dsl")

        unsupported_entities = self._collect_unsupported_entities(sanitized)
        if unsupported_entities:
            violations.append("entity_binding_failed")

        unsupported_geo = self._collect_unsupported_geography(sanitized, compiled_prompt.allowed_geo_terms)
        if unsupported_geo:
            violations.append("unsupported_geography")

        sanitized = re.sub(r'\n{3,}', '\n\n', sanitized).strip()

        if violations and compiled_prompt.require_unknown_on_gap:
            reason_parts = []
            if unsupported_entities:
                reason_parts.append(
                    "unbound entities: " + ", ".join(sorted(unsupported_entities)[:4])
                )
            if unsupported_geo:
                reason_parts.append(
                    "unsupported geography: " + ", ".join(sorted(unsupported_geo)[:4])
                )
            if not reason_parts:
                reason_parts.append("output violated the analyst runtime contract")
            response = self._unknown_response(
                compiled_prompt,
                "; ".join(reason_parts),
                ("ask for a graph-backed entity or IP already present in the graph",
                 "collect or ingest more evidence before analysis"),
            )
            return FirewallResult(
                response=response,
                violation_codes=tuple(dict.fromkeys(violations)),
                rewritten=True,
            )

        return FirewallResult(
            response=sanitized,
            violation_codes=tuple(dict.fromkeys(violations)),
            rewritten=sanitized != response,
        )

    def _collect_unsupported_entities(self, response: str) -> Set[str]:
        if not self.entity_set:
            return set()
        unsupported: Set[str] = set()
        for ip in self._IP_PATTERN.findall(response):
            if self._normalize_token(ip) not in self.entity_set:
                unsupported.add(ip)
        for token in self._CAMEL_PATTERN.findall(response):
            if self._normalize_token(token) not in self.entity_set:
                unsupported.add(token)
        for phrase in self._TITLE_PATTERN.findall(response):
            norm = self._normalize_token(phrase)
            if norm not in self.entity_set and norm not in self.allowed_geo_terms:
                unsupported.add(phrase)
        return unsupported

    def _collect_unsupported_geography(self, response: str, allowed_geo_terms: Set[str]) -> Set[str]:
        lower = response.lower()
        unsupported: Set[str] = set()
        for term in _FORBIDDEN_GEO_TERMS:
            if term in lower and term not in self.allowed_geo_terms and term not in allowed_geo_terms:
                unsupported.add(term)
        return unsupported

    def _unknown_response(
        self,
        compiled_prompt: CompiledPrompt,
        reason: str,
        next_steps: Tuple[str, ...],
    ) -> str:
        lines = [
            "UNKNOWN",
            f"Reason: {reason}.",
        ]
        if compiled_prompt.mode == PromptMode.QUERY and compiled_prompt.source_query:
            lines.append(f"Query: {compiled_prompt.source_query}")
        lines.append("Next steps:")
        for idx, step in enumerate(next_steps, start=1):
            lines.append(f"{idx}. {step}")
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Conversation Sentinel — prevents macro-recursion in dispatch/fallback layer
# ─────────────────────────────────────────────────────────────────────────────
#
# This is the narrative-plane equivalent of the recursion_sentinel in
# rule_prompt.py.  It guards against:
#   dispatch → error → fallback → dispatch → ...
#   dispatch → tool status → LLM explain → dispatch → ...
#
# MAX_DISPATCH_DEPTH is intentionally low.  Normal depth is 1.
# Anything > 3 means a control-flow cycle is forming.

MAX_DISPATCH_DEPTH: int = 5
_dispatch_depth = threading.local()


def _terminal_response(message: str) -> str:
    """Return a static, terminal system response.

    NEVER calls the LLM, dispatch, tools, or any method that could
    re-enter the conversation loop.  This is the last wall.
    """
    return f"[SYSTEM] {message}"


class DispatchRecursionError(RuntimeError):
    """Raised when conversation dispatch depth exceeds MAX_DISPATCH_DEPTH."""
    pass


def dispatch_sentinel(fn):
    """Decorator that enforces MAX_DISPATCH_DEPTH on conversation dispatch.

    If depth is exceeded, returns a terminal response instead of
    raising (to keep the HTTP response clean).
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        depth_key = f"_dispatch_{fn.__qualname__}"
        current = getattr(_dispatch_depth, depth_key, 0)
        if current >= MAX_DISPATCH_DEPTH:
            logger.error(
                "[DISPATCH SENTINEL] %s hit depth %d (max=%d) — "
                "conversation recursion blocked",
                fn.__qualname__, current, MAX_DISPATCH_DEPTH,
            )
            return _terminal_response(
                "Dispatch recursion detected. "
                "No further processing possible for this request."
            )
        setattr(_dispatch_depth, depth_key, current + 1)
        try:
            return fn(*args, **kwargs)
        finally:
            setattr(_dispatch_depth, depth_key, current)
    return wrapper


# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class GemmaRunnerConfig:
    """Configuration for the Gemma inference runner."""
    model_name: str = "gemma3:1b"
    # Reads OLLAMA_URL env var so Docker deployments can override via compose env
    ollama_url: str = field(
        default_factory=lambda: os.environ.get("OLLAMA_URL", "http://localhost:11434")
    )
    timeout: float = 120.0    # 2 min cap — circuit breaker opens after 3 timeouts
    temperature: float = 0.0
    source: str = "tak-ml"
    room_name: str = "Global"
    model_version: str = "gemma3_rf_scythe_v0_1"
    max_context_edges: int = 50      # cap edges per prompt to stay in context
    max_context_nodes: int = 30      # cap neighbor nodes per prompt
    batch_size: int = 5              # nodes per batch prompt
    validate_node_ids: bool = True   # reject hallucinated node IDs
    # Confidence tiers
    auto_commit_threshold: float = 0.85   # Tier A: auto-commit
    review_threshold: float = 0.70        # Tier B: operator review
    # Below review_threshold → Tier C: shadow only


# ─────────────────────────────────────────────────────────────────────────────
# Confidence Tiers
# ─────────────────────────────────────────────────────────────────────────────

TIER_A = "auto_commit"    # ≥ 0.85
TIER_B = "review"         # 0.70 – 0.85
TIER_C = "shadow"         # < 0.70


# ─────────────────────────────────────────────────────────────────────────────
# Gemma Circuit Breaker
# Prevents 15-minute timeout storms when Ollama GPU is degraded.
# Opens after _FAILURE_THRESHOLD consecutive timeouts; re-probes after cooldown.
# ─────────────────────────────────────────────────────────────────────────────

class GemmaCircuitBreaker:
    """Thread-safe circuit breaker for Gemma/Ollama inference calls."""
    _FAILURE_THRESHOLD: int = 3       # consecutive timeouts before opening
    _COOLDOWN_SECS: float = 60.0      # seconds to stay open before half-open probe

    def __init__(self) -> None:
        self._failures: int = 0
        self._open_until: Optional[float] = None
        self._lock = threading.Lock()

    def is_open(self) -> bool:
        """Return True if the circuit is open (inference should be skipped)."""
        with self._lock:
            if self._open_until is None:
                return False
            if time.monotonic() >= self._open_until:
                logger.info("[gemma-circuit] HALF-OPEN — allowing one probe attempt")
                self._open_until = None
                return False
            return True

    def record_success(self) -> None:
        with self._lock:
            if self._failures > 0:
                logger.info("[gemma-circuit] CLOSED — reset after success")
            self._failures = 0
            self._open_until = None

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self._FAILURE_THRESHOLD:
                self._open_until = time.monotonic() + self._COOLDOWN_SECS
                logger.warning(
                    "[gemma-circuit] OPEN — %d consecutive failures, "
                    "skipping Gemma inference for %.0fs",
                    self._failures, self._COOLDOWN_SECS,
                )


# Module-level singleton shared across all GemmaInferenceRunner instances
_gemma_circuit_breaker = GemmaCircuitBreaker()


def classify_confidence(confidence: float, cfg: GemmaRunnerConfig) -> str:
    """Classify an edge into a confidence tier."""
    if confidence >= cfg.auto_commit_threshold:
        return TIER_A
    elif confidence >= cfg.review_threshold:
        return TIER_B
    return TIER_C


# ─────────────────────────────────────────────────────────────────────────────
# Inference Run Log (persistence)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class InferenceRunLog:
    """Record of a single inference run for time-series tracking."""
    run_id: str = ""
    timestamp: float = 0.0
    model: str = ""
    edge_count: int = 0
    tier_a_count: int = 0
    tier_b_count: int = 0
    tier_c_count: int = 0
    lifted_edge_count: int = 0
    edge_kinds: Dict[str, int] = field(default_factory=dict)
    geo_cells_touched: List[str] = field(default_factory=list)
    confidence_histogram: Dict[str, int] = field(default_factory=dict)
    duration_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "model": self.model,
            "edge_count": self.edge_count,
            "tier_a_count": self.tier_a_count,
            "tier_b_count": self.tier_b_count,
            "tier_c_count": self.tier_c_count,
            "lifted_edge_count": self.lifted_edge_count,
            "edge_kinds": self.edge_kinds,
            "geo_cells_touched": self.geo_cells_touched,
            "confidence_histogram": self.confidence_histogram,
            "duration_seconds": self.duration_seconds,
        }


# Module-level inference run history
_inference_run_history: List[InferenceRunLog] = []
_MAX_RUN_HISTORY = 100
_runtime_metrics = get_takml_runtime_metrics_tracker()


def get_inference_history() -> List[Dict[str, Any]]:
    """Return the inference run history as dicts."""
    return [r.to_dict() for r in _inference_run_history]


def get_last_inference_run() -> Optional[Dict[str, Any]]:
    """Return the most recent inference run, or None."""
    return _inference_run_history[-1].to_dict() if _inference_run_history else None


def get_takml_runtime_metrics_snapshot(window_seconds: int = 900) -> Dict[str, Any]:
    """Return shared tak-ml runtime metrics for the requested time window."""
    return get_takml_runtime_metrics(window_seconds=window_seconds)


def compute_belief_drift() -> Dict[str, Any]:
    """Compare the last two inference runs and report drift.

    Returns a dict with:
      - new_kinds: edge kinds that appeared in latest but not previous
      - lost_kinds: edge kinds that disappeared
      - strengthened: kinds whose count increased
      - weakened: kinds whose count decreased
      - edge_count_delta: change in total edge count
      - tier_shift: change in tier A/B/C counts
      - verdict: "strengthening" | "weakening" | "stable" | "insufficient_data"
    """
    if len(_inference_run_history) < 2:
        return {"verdict": "insufficient_data"}

    prev = _inference_run_history[-2].to_dict()
    curr = _inference_run_history[-1].to_dict()

    prev_kinds = prev.get("edge_kinds", {})
    curr_kinds = curr.get("edge_kinds", {})
    all_kinds = set(prev_kinds.keys()) | set(curr_kinds.keys())

    new_kinds = {k: curr_kinds[k] for k in all_kinds if k in curr_kinds and k not in prev_kinds}
    lost_kinds = {k: prev_kinds[k] for k in all_kinds if k in prev_kinds and k not in curr_kinds}
    strengthened = {k: curr_kinds[k] - prev_kinds.get(k, 0)
                    for k in all_kinds
                    if k in curr_kinds and k in prev_kinds and curr_kinds[k] > prev_kinds[k]}
    weakened = {k: prev_kinds[k] - curr_kinds.get(k, 0)
                for k in all_kinds
                if k in curr_kinds and k in prev_kinds and curr_kinds[k] < prev_kinds[k]}

    edge_delta = curr.get("edge_count", 0) - prev.get("edge_count", 0)
    tier_shift = {
        "tier_a": curr.get("tier_a_count", 0) - prev.get("tier_a_count", 0),
        "tier_b": curr.get("tier_b_count", 0) - prev.get("tier_b_count", 0),
        "tier_c": curr.get("tier_c_count", 0) - prev.get("tier_c_count", 0),
    }

    # Verdict
    if edge_delta > 5 and tier_shift["tier_a"] > 0:
        verdict = "strengthening"
    elif edge_delta < -5 or tier_shift["tier_a"] < 0:
        verdict = "weakening"
    else:
        verdict = "stable"

    return {
        "verdict": verdict,
        "edge_count_delta": edge_delta,
        "new_kinds": new_kinds,
        "lost_kinds": lost_kinds,
        "strengthened": strengthened,
        "weakened": weakened,
        "tier_shift": tier_shift,
        "prev_run_id": prev.get("run_id", ""),
        "curr_run_id": curr.get("run_id", ""),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Edge Lifting — collapse flow-level edges into macro structural edges
# ─────────────────────────────────────────────────────────────────────────────

def lift_edges(ops: List[Any]) -> Dict[str, Any]:
    """
    Analyze a batch of inferred edge ops and compute lifted macro-edges.

    Clusters edges by (dst_node, edge_kind) to find structural patterns.
    E.g. 47x INFERRED_FLOW_IN_SERVICE → 6x INFERRED_HOST_IN_SERVICE

    Returns:
        {
            "raw_count": 115,
            "lifted_count": 14,
            "clusters": [{"kind": ..., "dst": ..., "count": ..., "sources": [...]}],
            "macro_edges": [{"kind": ..., "description": ...}],
        }
    """
    from collections import Counter

    if not ops:
        return {"raw_count": 0, "lifted_count": 0, "clusters": [], "macro_edges": []}

    # Cluster by (edge_kind, dst_node)
    clusters: Dict[Tuple[str, str], List[str]] = {}
    for op in ops:
        ed = op.entity_data if hasattr(op, 'entity_data') else (op if isinstance(op, dict) else {})
        kind = ed.get("kind", "")
        nodes = ed.get("nodes", [])
        if len(nodes) >= 2:
            dst = nodes[1]  # convention: [src, dst]
            key = (kind, dst)
            if key not in clusters:
                clusters[key] = []
            clusters[key].append(nodes[0])

    # Build cluster summaries
    cluster_list = []
    for (kind, dst), sources in sorted(clusters.items(), key=lambda x: -len(x[1])):
        cluster_list.append({
            "kind": kind,
            "dst": dst,
            "count": len(sources),
            "sources": sources[:10],  # cap for readability
        })

    # Generate macro edges (lift common patterns)
    LIFT_MAP = {
        "INFERRED_FLOW_IN_SERVICE": "INFERRED_HOST_OFFERS_SERVICE",
        "INFERRED_HOST_CONTACTED_SNI": "INFERRED_HOST_IN_ORG",
        "INFERRED_HOST_CONTACTED_HTTP_HOST": "INFERRED_HOST_IN_ORG",
        "INFERRED_FLOW_SNI_EQ_HTTP_HOST": "INFERRED_SERVICE_ALIAS",
    }

    macro_edges = []
    raw_count = len(ops)
    for cluster in cluster_list:
        if cluster["count"] >= 3:
            lifted_kind = LIFT_MAP.get(cluster["kind"], f"LIFTED_{cluster['kind']}")
            support = cluster["count"]
            strength = round(support / raw_count, 3) if raw_count else 0
            if strength >= 0.6:
                claim_label = "strong indication"
            elif strength >= 0.4:
                claim_label = "emerging pattern"
            else:
                claim_label = "weak signal"
            macro_edges.append({
                "kind": lifted_kind,
                "target": cluster["dst"],
                "support_count": support,
                "claim_strength": strength,
                "claim_label": claim_label,
                "description": f"{support}x {cluster['kind']} → {cluster['dst']}",
            })

    return {
        "raw_count": raw_count,
        "lifted_count": len(macro_edges),
        "clusters": cluster_list[:20],
        "macro_edges": macro_edges,
    }



# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _now_ts() -> float:
    return time.time()


def _stable_id(prefix: str, *parts: str) -> str:
    core = "|".join(parts)
    h = hashlib.sha256(core.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}:{h}"


def _safe_dict(obj: Any) -> Dict[str, Any]:
    """Convert a node/edge object to a plain dict."""
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    return {"id": str(obj)}


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

class TakMlGemmaRunner:
    """
    TAK-ML model runner that wraps Gemma 3 (via Ollama) for structured
    inference over the RF SCYTHE hypergraph.

    Supports per-flow, per-host, and batch inference modes.
    All inferred edges are committed through WriteBus with full provenance.
    """

    def __init__(
        self,
        hypergraph_engine: Any,
        config: Optional[GemmaRunnerConfig] = None,
    ):
        self.hg = hypergraph_engine
        self.config = config or GemmaRunnerConfig()
        self._client = None  # lazy init

        # Inference Exhaustion Ledger — uses module-level singleton so
        # exhaustion state persists across per-request runner instances.
        from ledger_aware_prompt import get_shared_ledger
        self.exhaustion_ledger = get_shared_ledger()
        self.runtime_metrics = _runtime_metrics

        # ── One-Shot Rule Fire Ledger ──
        # Tracks (rule_id, entity_id, evidence_epoch) → fire count.
        # Each rule may fire at most once per entity per epoch.
        # This prevents micro-recursion where a rule fires, produces
        # 0 valid edges, and the batch loop retries it.
        self._rule_fires: Dict[Tuple[str, str, str], int] = {}
        self.MAX_RULE_FIRES_PER_EPOCH: int = 1

    @property
    def client(self):
        if self._client is None:
            from gemma_client import GemmaClient
            self._client = GemmaClient(
                base_url=self.config.ollama_url,
                timeout=self.config.timeout,
            )
        return self._client

    # ─────────────────────────────────────────────────────────────────────
    # Context extraction from HypergraphEngine
    # ─────────────────────────────────────────────────────────────────────

    def _collect_context(
        self,
        node_id: str,
        max_edges: Optional[int] = None,
        max_nodes: Optional[int] = None,
    ) -> Tuple[Dict, List[Dict], Dict[str, Dict]]:
        """
        Extract a node and its neighborhood from the hypergraph.

        Returns (node_dict, edges_list, neighbor_nodes_dict).
        """
        node = None
        # Try direct get
        if hasattr(self.hg, 'get_node'):
            node = self.hg.get_node(node_id)
        elif hasattr(self.hg, 'nodes') and isinstance(self.hg.nodes, dict):
            node = self.hg.nodes.get(node_id)

        if node is None:
            return {}, [], {}

        node_d = _safe_dict(node)

        # Collect edges
        edges_raw = []
        if hasattr(self.hg, 'edges_for_node'):
            edges_raw = list(self.hg.edges_for_node(node_id))
        elif hasattr(self.hg, 'edges') and isinstance(self.hg.edges, dict):
            for eid, e in self.hg.edges.items():
                ed = _safe_dict(e)
                enodes = ed.get('nodes', [])
                src = ed.get('source') or ed.get('src', '')
                dst = ed.get('target') or ed.get('dst', '')
                if node_id in enodes or node_id == src or node_id == dst:
                    edges_raw.append(e)

        limit_e = max_edges if max_edges is not None else self.config.max_context_edges
        edges = [_safe_dict(e) for e in edges_raw[:limit_e]]

        # Collect neighbor nodes
        neighbor_ids: Set[str] = set()
        for ed in edges:
            for nid in ed.get('nodes', []):
                if nid != node_id:
                    neighbor_ids.add(nid)
            for key in ('source', 'src', 'target', 'dst'):
                v = ed.get(key, '')
                if v and v != node_id:
                    neighbor_ids.add(v)

        neighbors: Dict[str, Dict] = {}
        limit_n = max_nodes if max_nodes is not None else self.config.max_context_nodes
        for nid in list(neighbor_ids)[:limit_n]:
            n = None
            if hasattr(self.hg, 'get_node'):
                n = self.hg.get_node(nid)
            elif hasattr(self.hg, 'nodes') and isinstance(self.hg.nodes, dict):
                n = self.hg.nodes.get(nid)
            if n is not None:
                neighbors[nid] = _safe_dict(n)

        return node_d, edges, neighbors

    def _all_node_ids(self) -> Set[str]:
        """Collect all known node IDs for validation."""
        if hasattr(self.hg, 'nodes') and isinstance(self.hg.nodes, dict):
            return set(self.hg.nodes.keys())
        return set()

    def _nodes_by_kind(self, kind: str, limit: Optional[int] = None) -> List[str]:
        """Get node IDs of a given kind."""
        result = []
        if hasattr(self.hg, 'nodes_by_kind'):
            for n in self.hg.nodes_by_kind(kind):
                nid = n.id if hasattr(n, 'id') else (n.get('node_id') or n.get('id', ''))
                result.append(nid)
                if limit and len(result) >= limit:
                    break
        elif hasattr(self.hg, 'nodes') and isinstance(self.hg.nodes, dict):
            for nid, n in self.hg.nodes.items():
                nd = _safe_dict(n)
                if nd.get('kind') == kind:
                    result.append(nid)
                    if limit and len(result) >= limit:
                        break
        return result

    # ─────────────────────────────────────────────────────────────────────
    # Gemma inference
    # ─────────────────────────────────────────────────────────────────────

    # ── Synthetic-node inference governor ────────────────────────────────
    #    Nodes born from auto-materialization are tagged _synthetic.
    #    We only run inference on them if their kind is whitelisted AND
    #    their depth is below the ceiling.  This prevents unbounded
    #    ontological expansion.
    MAX_MATERIALIZATION_DEPTH: int = 1  # stubs never spawn stubs
    ALLOWED_SYNTHETIC_INFERENCE_KINDS: frozenset = frozenset({"flow"})

    def _infer_for_node(
        self,
        node_id: str,
        prompt_builder: str = "flow",
        *,
        _epoch_visited: Optional[set] = None,
    ) -> List[Dict]:
        """
        Run Gemma inference for a single node. Returns validated rule results.

        Guards (checked in order):
        0. Recursion sentinel — hard depth check
        1. Epoch dedup — skip if already visited this batch
        2. Exhaustion ledger — skip if exhausted in current evidence epoch
        2b. One-shot rule fire — max 1 fire per (rule, entity, epoch)
        3. Synthetic-node guard — skip if depth/kind not allowed
        """
        from rule_prompt import (
            SYSTEM_PROMPT,
            build_flow_prompt,
            build_host_prompt,
            require_structured_gemma_output,
            validate_gemma_output,
            auto_materialize_missing_nodes,
            MAX_RECURSION_DEPTH,
            _sentinel_depth,
        )
        from inference_exhaustion_ledger import (
            InferenceExhaustionLedger,
            RESULT_SUCCESS,
            RESULT_NO_VALID_EDGES,
            RESULT_ERROR,
        )

        # ── Guard 0: Recursion sentinel ──
        depth_key = "_depth__infer_for_node"
        current_depth = getattr(_sentinel_depth, depth_key, 0)
        if current_depth >= MAX_RECURSION_DEPTH:
            logger.error(
                "[tak-ml] RECURSION SENTINEL tripped in _infer_for_node "
                "(depth=%d, node=%s)", current_depth, node_id,
            )
            return []
        setattr(_sentinel_depth, depth_key, current_depth + 1)
        try:
            return self.__infer_for_node_body(
                node_id, prompt_builder, _epoch_visited,
                SYSTEM_PROMPT, build_flow_prompt, build_host_prompt,
                require_structured_gemma_output,
                validate_gemma_output, auto_materialize_missing_nodes,
                InferenceExhaustionLedger, RESULT_SUCCESS,
                RESULT_NO_VALID_EDGES, RESULT_ERROR,
            )
        finally:
            setattr(_sentinel_depth, depth_key, current_depth)

    def __infer_for_node_body(
        self,
        node_id: str,
        prompt_builder: str,
        _epoch_visited: Optional[set],
        SYSTEM_PROMPT,
        build_flow_prompt,
        build_host_prompt,
        require_structured_gemma_output,
        validate_gemma_output,
        auto_materialize_missing_nodes,
        InferenceExhaustionLedger,
        RESULT_SUCCESS,
        RESULT_NO_VALID_EDGES,
        RESULT_ERROR,
    ) -> List[Dict]:
        """Inner body of _infer_for_node, called under recursion sentinel."""

        # ── Guard 1: Epoch dedup ──
        if _epoch_visited is not None:
            if node_id in _epoch_visited:
                logger.debug("[tak-ml] Skipping %s: already visited this epoch", node_id)
                return []
            _epoch_visited.add(node_id)

        # ── Guard 2: Exhaustion ledger (TERMINAL — non-reenterable) ──
        # Once an entity is exhausted in the current evidence epoch, NO code
        # path may re-invoke inference for it.  This is the outer wall that
        # prevents micro-recursion from rule retries.
        rule_id = "batch"  # rule-agnostic for now; per-rule possible later
        evidence_epoch = InferenceExhaustionLedger.compute_evidence_epoch(
            self.hg, node_id,
        )
        if self.exhaustion_ledger.is_exhausted(node_id, rule_id, evidence_epoch):
            logger.warning(
                "[tak-ml] TERMINAL SKIP %s: exhausted in epoch %s "
                "(non-reenterable — waiting for new sensor evidence)",
                node_id, evidence_epoch[:8],
            )
            return []

        # ── Guard 2b: One-shot rule fire per epoch ──
        # Each (rule, entity, epoch) triple may fire at most once.
        # Prevents validator→retry micro-loops.
        fire_key = (rule_id, node_id, evidence_epoch)
        prior_fires = self._rule_fires.get(fire_key, 0)
        if prior_fires >= self.MAX_RULE_FIRES_PER_EPOCH:
            logger.warning(
                "[tak-ml] ONE-SHOT BLOCK %s: rule %s already fired %d "
                "time(s) in epoch %s",
                node_id, rule_id, prior_fires, evidence_epoch[:8],
            )
            return []
        self._rule_fires[fire_key] = prior_fires + 1

        node_d, edges, neighbors = self._collect_context(node_id)
        if not node_d:
            logger.warning("[tak-ml] Empty context for node %s. Skipping inference.", node_id)
            return []

        # ── Guard 3: Synthetic-node guard ──
        node_meta = node_d.get("metadata", {})
        if node_meta.get("_synthetic"):
            depth = node_meta.get("_materialization_depth", 999)
            node_kind = node_d.get("kind", "")
            if depth >= self.MAX_MATERIALIZATION_DEPTH:
                logger.info(
                    "[tak-ml] Skip inference on synthetic %s: "
                    "depth=%d >= MAX=%d",
                    node_id, depth, self.MAX_MATERIALIZATION_DEPTH,
                )
                return []
            if node_kind not in self.ALLOWED_SYNTHETIC_INFERENCE_KINDS:
                logger.info(
                    "[tak-ml] Skip inference on synthetic %s: "
                    "kind=%s not in whitelist %s",
                    node_id, node_kind,
                    self.ALLOWED_SYNTHETIC_INFERENCE_KINDS,
                )
                return []

        if prompt_builder == "host":
            prompt = build_host_prompt(node_d, edges, neighbors)
        else:
            prompt = build_flow_prompt(node_d, edges, neighbors)

        # ── Stage 1: Primary Inference (Full Context) ──
        compressed_used = False
        try:
            self.runtime_metrics.record_inference_attempt()
            # ── Circuit Breaker Gate ──
            if _gemma_circuit_breaker.is_open():
                logger.warning(
                    "[tak-ml] Circuit OPEN — skipping Gemma for %s "
                    "(GPU degraded, waiting for cooldown)",
                    node_id,
                )
                self.exhaustion_ledger.record_attempt(
                    node_id, rule_id, evidence_epoch,
                    result=RESULT_ERROR,
                    entity_kind=node_d.get("kind", "unknown"),
                )
                self.runtime_metrics.record_error()
                return []

            raw = self.client.generate_json(
                self.config.model_name,
                prompt,
                system=SYSTEM_PROMPT,
                temperature=self.config.temperature,
            )
            _gemma_circuit_breaker.record_success()
        except Exception as e:
            # Check for timeout (heuristic: 'Read timed out' or 'Timeout' in error msg)
            err_msg = str(e)
            if "Read timed out" in err_msg or "Timeout" in err_msg:
                logger.warning(
                    "[tak-ml] 🧠 COGNITIVE STRAIN for %s: Primary inference timed out. "
                    "Retrying with compressed context window...",
                    node_id
                )
                # ── Stage 2: Cognitive Compression Retry ──
                # Collapse context: sacrifice neighbor breadth for resolution speed.
                compressed_used = True
                node_d2, edges2, neighbors2 = self._collect_context(node_id, max_edges=10, max_nodes=5)
                if prompt_builder == "host":
                    prompt2 = build_host_prompt(node_d2, edges2, neighbors2)
                else:
                    prompt2 = build_flow_prompt(node_d2, edges2, neighbors2)

                try:
                    raw = self.client.generate_json(
                        self.config.model_name,
                        prompt2,
                        system=SYSTEM_PROMPT,
                        temperature=self.config.temperature,
                    )
                    _gemma_circuit_breaker.record_success()
                except Exception as e2:
                    _gemma_circuit_breaker.record_failure()
                    logger.error("[tak-ml] Cognitive compression also failed for %s: %s", node_id, e2)
                    raw = {}
            else:
                _gemma_circuit_breaker.record_failure()
                logger.warning("[tak-ml] Gemma inference failed for %s: %s", node_id, e)
                # Record as ERROR — transient, eligible for retry
                self.exhaustion_ledger.record_attempt(
                    node_id, rule_id, evidence_epoch,
                    result=RESULT_ERROR,
                    entity_kind=node_d.get("kind", "unknown"),
                )
                self.runtime_metrics.record_error()
                return []

        structured_raw = require_structured_gemma_output(raw)
        if not structured_raw:
            logger.warning(
                "[tak-ml] Hard-failed inference for %s: model output was not dict-only structured JSON",
                node_id,
            )
            self.exhaustion_ledger.record_attempt(
                node_id, rule_id, evidence_epoch,
                result=RESULT_ERROR,
                entity_kind=node_d.get("kind", "unknown"),
            )
            self.runtime_metrics.record_error()
            return []

        known_ids = self._all_node_ids() if self.config.validate_node_ids else None

        # Auto-materialize missing nodes before validation
        if known_ids is not None:
            source_depth = node_meta.get("_materialization_depth", 0) if node_meta.get("_synthetic") else 0
            auto_materialize_missing_nodes(
                structured_raw,
                known_ids,
                self.hg,
                source_depth=source_depth,
            )
            # Refresh so newly materialized stub nodes are included in validation
            known_ids = self._all_node_ids()
            # Re-evaluate shadow graph — any edges waiting on these nodes may now promote
            try:
                from shadow_graph import ShadowGraph
                promoted = ShadowGraph.get_instance().re_evaluate(known_ids)
                if promoted:
                    logger.info(
                        "[tak-ml] Shadow graph promoted %d edge(s) after materialization",
                        len(promoted),
                    )
            except Exception:
                pass

        validated = validate_gemma_output(structured_raw, known_node_ids=known_ids)
        if compressed_used:
            for res in validated:
                res["cognitive_compression"] = True

        # ── Guardrail repair pass ──
        # If the rule fired but all edges were dropped (0 valid), run the
        # 3-stage inference guardrail to recover what it can before recording
        # exhaustion.  Recovered edges re-enter the validated results list.
        fired_with_no_edges = any(
            r.get("should_fire") and not r.get("inferred_edges")
            for r in validated
        )
        if fired_with_no_edges:
            try:
                from inference_guardrail import guardrail_repair_pass
                validated = guardrail_repair_pass(
                    structured_raw, validated,
                    context_node_id=node_id,
                    known_node_ids=known_ids,
                )
            except Exception as _gr_err:
                logger.debug("[tak-ml] Guardrail repair pass failed: %s", _gr_err)

        # Count valid edges across all rule results
        total_edges = sum(
            len(r.get("inferred_edges", []))
            for r in validated
            if r.get("should_fire")
        )

        # ── Record attempt in exhaustion ledger ──
        entity_kind = node_d.get("kind", "unknown")
        if total_edges > 0:
            self.exhaustion_ledger.record_attempt(
                node_id, rule_id, evidence_epoch,
                result=RESULT_SUCCESS,
                entity_kind=entity_kind,
                edges_produced=total_edges,
            )
            self.runtime_metrics.record_inference_success(edges_produced=total_edges)
        else:
            self.exhaustion_ledger.record_attempt(
                node_id, rule_id, evidence_epoch,
                result=RESULT_NO_VALID_EDGES,
                entity_kind=entity_kind,
            )
            self.runtime_metrics.record_exhaustion()
            logger.info(
                "[tak-ml] %s exhausted in epoch %s — 0 valid edges, "
                "waiting for new evidence",
                node_id, evidence_epoch[:8],
            )

        return validated

    # ─────────────────────────────────────────────────────────────────────
    # Convert Gemma output → GraphOps
    # ─────────────────────────────────────────────────────────────────────

    def _results_to_ops(
        self,
        results: List[Dict],
        source_node_id: str,
    ) -> List[Any]:
        """Convert validated Gemma rule results to GraphOp objects."""
        try:
            from writebus import GraphOp
        except ImportError:
            from dataclasses import dataclass as _dc

            @_dc
            class GraphOp:  # type: ignore[no-redef]
                event_type: str
                entity_id: str
                entity_data: Dict[str, Any]

        ops = []
        ts = _now_ts()

        for r in results:
            if not r.get("should_fire"):
                continue

            rule_id = r.get("rule_id", "R-UNK-000")
            confidence = float(r.get("confidence", 0.5))
            evidence = r.get("evidence") or []
            tags = r.get("tags") or {}

            for ie in r.get("inferred_edges", []):
                kind = ie.get("kind")
                src = ie.get("src")
                dst = ie.get("dst")
                if not kind or not src or not dst:
                    continue

                eid = _stable_id("e:gemma", kind, src, dst, rule_id)
                tier = classify_confidence(confidence, self.config)

                ops.append(GraphOp(
                    event_type="EDGE_CREATE",
                    entity_id=eid,
                    entity_data={
                        "id": eid,
                        "kind": kind,
                        "nodes": [src, dst],
                        "labels": tags,
                        "metadata": {
                            "obs_class": "inferred",
                            "confidence": confidence,
                            "confidence_tier": tier,
                            "edge_status": "committed" if tier == TIER_A else ("proposed" if tier == TIER_B else "shadow"),
                            "visible": tier != TIER_C,
                            "provenance": {
                                "source": self.config.source,
                                "rule_id": rule_id,
                                "engine": "gemma3",
                                "model": self.config.model_name,
                                "evidence": evidence[:8],
                                "source_node": source_node_id,
                                "timestamp": ts,
                                "cognitive_compression": r.get("cognitive_compression", False),
                            },
                        },
                        "timestamp": ts,
                    },
                ))

        return ops

    # ─────────────────────────────────────────────────────────────────────
    # Public API: per-flow inference
    # ─────────────────────────────────────────────────────────────────────

    def run_for_flow(self, flow_id: str) -> int:
        """
        Run inference for a single flow node. Commits results via WriteBus.
        Returns number of ops committed.
        """
        results = self._infer_for_node(flow_id, prompt_builder="flow")
        ops = self._results_to_ops(results, flow_id)
        if not ops:
            return 0
        self._commit_ops(ops, flow_id, "flow")
        return len(ops)

    def run_for_host(self, host_id: str) -> int:
        """
        Run inference for a single host node. Commits results via WriteBus.
        Returns number of ops committed.
        """
        results = self._infer_for_node(host_id, prompt_builder="host")
        ops = self._results_to_ops(results, host_id)
        if not ops:
            return 0
        self._commit_ops(ops, host_id, "host")
        return len(ops)

    # ─────────────────────────────────────────────────────────────────────
    # Public API: batch mode
    # ─────────────────────────────────────────────────────────────────────

    def run_for_all_flows(self, limit: Optional[int] = None) -> int:
        """
        Run inference for all flow nodes. Returns total ops committed.
        Uses an epoch visited-set to prevent cyclic re-inference.
        Pre-filters exhausted entities at the batch level (non-reenterable).
        """
        self._rule_fires.clear()  # reset one-shot ledger per batch
        total = 0
        epoch_visited: set = set()
        flow_ids = self._nodes_by_kind("flow", limit=limit)
        # ── Batch-level exhaustion pre-filter (non-reenterable) ──
        exhausted_ids = {r["entity_id"] for r in self.exhaustion_ledger.get_exhausted_entities()}
        flow_ids = [f for f in flow_ids if f not in exhausted_ids]
        for fid in flow_ids:
            try:
                results = self._infer_for_node(fid, "flow", _epoch_visited=epoch_visited)
                ops = self._results_to_ops(results, fid)
                if ops:
                    self._commit_ops(ops, fid, "flow")
                    total += len(ops)
            except Exception as e:
                logger.warning("[tak-ml] flow %s error: %s", fid, e)
        logger.info("[tak-ml] batch complete: %d flows → %d ops", len(flow_ids), total)
        return total

    def run_for_all_hosts(self, limit: Optional[int] = None) -> int:
        """
        Run inference for all host nodes. Returns total ops committed.
        Uses an epoch visited-set to prevent cyclic re-inference.
        Pre-filters exhausted entities at the batch level (non-reenterable).
        """
        self._rule_fires.clear()  # reset one-shot ledger per batch
        total = 0
        epoch_visited: set = set()
        host_ids = self._nodes_by_kind("host", limit=limit)
        # ── Batch-level exhaustion pre-filter (non-reenterable) ──
        exhausted_ids = {r["entity_id"] for r in self.exhaustion_ledger.get_exhausted_entities()}
        host_ids = [h for h in host_ids if h not in exhausted_ids]
        for hid in host_ids:
            try:
                results = self._infer_for_node(hid, "host", _epoch_visited=epoch_visited)
                ops = self._results_to_ops(results, hid)
                if ops:
                    self._commit_ops(ops, hid, "host")
                    total += len(ops)
            except Exception as e:
                logger.warning("[tak-ml] host %s error: %s", hid, e)
        logger.info("[tak-ml] batch complete: %d hosts → %d ops", len(host_ids), total)
        return total

    def run_batch_return_ops(self, limit: Optional[int] = None) -> List[Any]:
        """
        Run inference for flows and hosts, return all ops WITHOUT committing.
        Useful for API integration where the caller commits.
        Also records an InferenceRunLog and stores on the engine for MCP.
        """
        self._rule_fires.clear()  # reset one-shot ledger per batch
        t0 = time.time()
        all_ops = []
        epoch_visited: set = set()  # prevent cyclic re-inference
        flow_ids = self._nodes_by_kind("flow", limit=limit)
        host_ids = self._nodes_by_kind("host", limit=limit)

        for fid in flow_ids:
            try:
                results = self._infer_for_node(fid, "flow", _epoch_visited=epoch_visited)
                all_ops.extend(self._results_to_ops(results, fid))
            except Exception as e:
                logger.warning("[tak-ml] flow %s error: %s", fid, e)

        for hid in host_ids:
            try:
                results = self._infer_for_node(hid, "host", _epoch_visited=epoch_visited)
                all_ops.extend(self._results_to_ops(results, hid))
            except Exception as e:
                logger.warning("[tak-ml] host %s error: %s", hid, e)

        # Commit batch
        if all_ops:
            self._commit_ops(all_ops, "batch", "mixed")

        # ── Record InferenceRunLog ──────────────────────────────────────
        duration = time.time() - t0
        run_log = self._build_run_log(all_ops, duration)
        _inference_run_history.append(run_log)
        if len(_inference_run_history) > _MAX_RUN_HISTORY:
            _inference_run_history.pop(0)

        # Store on engine for MCP access
        lifting = lift_edges(all_ops)
        if hasattr(self.hg, '__dict__'):
            self.hg._last_inference_run = {
                "last_run": run_log.to_dict(),
                "lifting": lifting,
            }

        logger.info(
            "[tak-ml] batch return: %d flows + %d hosts → %d ops "
            "(A=%d B=%d C=%d, lifted=%d) in %.1fs",
            len(flow_ids), len(host_ids), len(all_ops),
            run_log.tier_a_count, run_log.tier_b_count, run_log.tier_c_count,
            lifting["lifted_count"], duration,
        )
        return all_ops

    def _build_run_log(self, ops: List[Any], duration: float) -> InferenceRunLog:
        """Build an InferenceRunLog from a completed batch."""
        from collections import Counter
        run_id = _stable_id("run", str(time.time()), str(len(ops)))
        kind_counter: Dict[str, int] = {}
        tier_a = tier_b = tier_c = 0
        geo_cells: Set[str] = set()
        conf_hist: Dict[str, int] = {"0.0-0.3": 0, "0.3-0.5": 0, "0.5-0.7": 0, "0.7-0.85": 0, "0.85-1.0": 0}

        for op in ops:
            ed = op.entity_data if hasattr(op, 'entity_data') else (op if isinstance(op, dict) else {})
            kind = ed.get("kind", "unknown")
            kind_counter[kind] = kind_counter.get(kind, 0) + 1
            meta = ed.get("metadata", {})
            tier = meta.get("confidence_tier", "")
            if tier == TIER_A:
                tier_a += 1
            elif tier == TIER_B:
                tier_b += 1
            elif tier == TIER_C:
                tier_c += 1
            conf = meta.get("confidence", 0.5)
            if conf >= 0.85:
                conf_hist["0.85-1.0"] += 1
            elif conf >= 0.7:
                conf_hist["0.7-0.85"] += 1
            elif conf >= 0.5:
                conf_hist["0.5-0.7"] += 1
            elif conf >= 0.3:
                conf_hist["0.3-0.5"] += 1
            else:
                conf_hist["0.0-0.3"] += 1
            # Track geo cells from node IDs
            for nid in ed.get("nodes", []):
                if nid.startswith("geo_cell:"):
                    geo_cells.add(nid)

        return InferenceRunLog(
            run_id=run_id,
            timestamp=time.time(),
            model=self.config.model_name,
            edge_count=len(ops),
            tier_a_count=tier_a,
            tier_b_count=tier_b,
            tier_c_count=tier_c,
            lifted_edge_count=0,  # filled after lift_edges
            edge_kinds=kind_counter,
            geo_cells_touched=list(geo_cells)[:20],
            confidence_histogram=conf_hist,
            duration_seconds=round(duration, 2),
        )

    # ─────────────────────────────────────────────────────────────────────
    # WriteBus commit
    # ─────────────────────────────────────────────────────────────────────

    def _commit_ops(self, ops: List[Any], ref_id: str, mode: str) -> None:
        """Commit ops through WriteBus."""
        try:
            import writebus
            from writebus import WriteContext

            ctx = WriteContext(
                room_name=self.config.room_name,
                source=self.config.source,
                model_version=self.config.model_version,
                evidence_refs=[ref_id],
            )
            writebus.bus().commit(
                entity_id=f"gemma_{mode}_{int(time.time())}",
                entity_type="tak_ml_gemma_inference",
                entity_data={
                    "mode": mode,
                    "model": self.config.model_name,
                    "op_count": len(ops),
                    "ref": ref_id,
                },
                graph_ops=ops,
                ctx=ctx,
            )
            self.runtime_metrics.record_commit(len(ops), mode=mode)
            logger.info("[tak-ml] committed %d ops for %s (%s)", len(ops), ref_id, mode)
        except Exception as e:
            self.runtime_metrics.record_error()
            logger.error("[tak-ml] commit failed: %s", e)

    # ─────────────────────────────────────────────────────────────────────
    # Health check
    # ─────────────────────────────────────────────────────────────────────

    def is_available(self) -> Dict[str, Any]:
        """Check Ollama + model availability."""
        available = self.client.is_available()
        models = self.client.list_models() if available else []
        model_ready = self.config.model_name in models
        return {
            "ollama_reachable": available,
            "models": models,
            "target_model": self.config.model_name,
            "model_loaded": model_ready,
        }


# ─────────────────────────────────────────────────────────────────────────────
# TAK-GPT compatible chat handler
# ─────────────────────────────────────────────────────────────────────────────

class GraphOpsChatBot:
    """
    TAK-GPT-compatible chat bot that answers natural-language graph queries.

    Mirrors the tak-gpt LLMChatManager pattern:
      sendChatRequest(messageText, context) → responseText

    The bot uses Gemma to translate natural language into graph_query_dsl
    queries, executes them, and returns formatted results.
    """

    SYSTEM_PROMPT = """\
You are the RF_SCYTHE GraphOps Agent v0.4 — a tactical network intelligence
analyst embedded in the RF_SCYTHE hypergraph system.

You operate in two cognitive modes.  Choose the correct one AUTOMATICALLY:

═══ MODE 1: QUERY (when the operator asks for a specific node, IP, ID, or edge) ═══
Generate a graph_query_dsl expression for the SPECIFIC target mentioned.
Use the ACTUAL IP, ID, or entity from the operator message — NOT the examples below.
DSL syntax reference (do NOT copy these examples literally):
    FIND NODES WHERE kind = "host" AND labels.ip = "<the actual IP from the question>"
    FIND EDGES WHERE kind = "FLOW_TLS_SNI" SINCE 10m
    FIND NEIGHBORS OF "<the actual node ID from the question>"
    FIND SUBGRAPH WHERE kind = "flow" IN BBOX [lat1, lon1, lat2, lon2]

═══ MODE 2: ANALYST NARRATIVE (when the operator asks "what is going on",
     "summarize", "what changed", or mentions a region/topic) ═══
Write a PROSE intelligence assessment.  DO NOT output these instructions.
Output format — write these sections as prose paragraphs:

  SITUATION: What exists right now (counts, top hosts, active regions).
  CHANGE: What is new or unusual since the last inference run.
  STRUCTURE: Patterns from lifted macro-edges (use claim_strength language).
  GEOGRAPHY: Where activity is anchored (geo cells, fiber anchors).
  ASSESSMENT: What this likely means (use "may indicate" for Tier C edges).
  DIRECTION: Suggest 1-3 next actions the operator should take.

If asked about a specific region ("Dallas", "Brazil", "Europe"):
  Summarize: dominant node kinds, flow volume vs global baseline, notable
  ASNs/orgs, whether activity is new/stable/increasing.

CREDIBILITY — start every MODE 2 response with one line:
  "Credibility posture: [sensor-grounded|inference-heavy], coverage X%, N stale inferences."
  Pull these values from WRITE_SUMMARY if present; otherwise use "unknown" for values.

BELIEF CHANGES — if asked "is this new?" or "what changed?":
  Check BELIEF_DRIFT.new_kinds (→ "new"), both prev+curr (→ "persistent"),
  lost_kinds (→ "dissipated").  Fallback: "Insufficient history."

EPISTEMIC RULES (ground all claims in MCP_CONTEXT data):
- "sensor-heavy"/"sensor-grounded" trust → say "sensor-confirmed" or "observed"
- "inference-heavy" trust → hedge with "model-inferred", "engine believes"
- evidence_coverage < 0.3 → warn about low evidence, recommend validation
- stale_inference_count > 0 → mention stale inferences explicitly
- If MCP_SENSOR_GROUNDING is present, treat its burst counts as the freshest observed evidence.
- Edge provenance: "pcap_ingest"/sensor → fact; "tak-ml"/inference → belief;
  "manual_ui"/analyst → hypothesis

RULES:
1. Always ground answers in MCP_CONTEXT — never hallucinate IDs or counts.
2. Be brief and actionable.  Use callsigns, IPs, ASN/org names.
3. If the graph is empty, say so explicitly.
4. Distinguish observed vs inferred (with confidence tier) edges.
5. When you include a DSL query, place it on its own line for execution.
6. Never repeat raw MCP_CONTEXT back verbatim — synthesize it.
7. Collection tasks: reference pending tasks in DIRECTION; cite task_id.
8. CAPTURE POLICY: Evaluate via evaluate_capture_policy before recommending
   capture.  Cite verdict verbatim (AUTHORIZE/REQUIRE_APPROVAL/DENY).
9. BELIEF CLOSURE: When a pcap session satisfies a collection task, narrate
   with belief delta values and note "knowledge gap closed."
10. UNKNOWN FALLBACK: If a question can't be answered from graph state,
    respond UNKNOWN and propose instrumentation.  Never fill gaps with fiction.
11. OPERATOR STATE: If a question references a human not recorded as a node,
    state UNKNOWN.  Never hallucinate human intent or state.
12. EVIDENCE-BOUND CLAIMS: Every quantitative claim must cite its MCP_CONTEXT
    source field.  Prefer "insufficient data" over fiction.
"""

    def __init__(
        self,
        hypergraph_engine: Any,
        config: Optional[GemmaRunnerConfig] = None,
    ):
        self.hg = hypergraph_engine
        self.config = config or GemmaRunnerConfig()
        self._client = None
        self._in_chat = False  # Recursion guard for send_chat_request

        # LAPT-DSL compiler — binds prompts to ledger authority
        from ledger_aware_prompt import LAPTCompiler, get_shared_ledger
        self._lapt = LAPTCompiler(hypergraph_engine, get_shared_ledger())

        # Multi-model ensemble registry
        from model_registry import ModelRegistry
        self._model_registry = ModelRegistry()

    @property
    def client(self):
        if self._client is None:
            from gemma_client import GemmaClient
            self._client = GemmaClient(
                base_url=self.config.ollama_url,
                timeout=self.config.timeout,
            )
        return self._client

    @staticmethod
    def _normalize_binding_value(value: Any) -> str:
        if value is None:
            return ""
        return re.sub(r"\s+", " ", str(value).strip()).lower()

    def _has_concrete_lookup_target(self, message: str) -> bool:
        return bool(re.search(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', message)) or bool(
            re.search(r'\b(?:[0-9a-fA-F]{2}[:\-]){5}[0-9a-fA-F]{2}\b', message)
        ) or bool(re.search(r'\b[0-9a-fA-F]{32,64}\b', message)) or bool(
            re.search(r'\b[A-Z]{2,}[\-_][A-Z0-9]{1,}\b', message)
        )

    def _build_entity_binding_context(self, limit: int = 240) -> Tuple[Set[str], Set[str]]:
        entity_set: Set[str] = set()
        allowed_geo_terms: Set[str] = set()
        nodes = self.hg.nodes if hasattr(self.hg, 'nodes') else {}

        def _add(value: Any, target: Set[str]) -> None:
            norm = self._normalize_binding_value(value)
            if norm and len(norm) <= 80:
                target.add(norm)

        for n in (nodes.values() if isinstance(nodes, dict) else nodes):
            nd = _safe_dict(n)
            node_kind = (nd.get("kind") or "").lower()
            _add(nd.get("id"), entity_set)
            labels = nd.get("labels") or {}
            metadata = nd.get("metadata") or {}
            for value in labels.values():
                if isinstance(value, (str, int, float)):
                    _add(value, entity_set)
            for key in ("name", "city", "region", "country", "org", "asn", "hostname", "callsign"):
                if key in labels:
                    _add(labels.get(key), entity_set)
                if key in metadata:
                    _add(metadata.get(key), entity_set)
            if node_kind.startswith("geo") or "geo" in node_kind:
                for source in (labels, metadata):
                    for key in ("name", "city", "region", "country", "location", "anchor"):
                        if key in source:
                            _add(source.get(key), allowed_geo_terms)
            if len(entity_set) >= limit:
                break

        return entity_set, allowed_geo_terms

    @staticmethod
    def _format_binding_preview(values: Set[str], label: str, limit: int = 40) -> str:
        if not values:
            return f"{label}: none"
        preview = sorted(values)[:limit]
        suffix = " ..." if len(values) > limit else ""
        return f"{label}: {', '.join(preview)}{suffix}"

    def _compile_chat_prompt(
        self,
        message_text: str,
        *,
        mcp_text: str,
        focus_text: str,
        sensor_grounding_text: str,
        ctx_info: str,
        lapt_result: Any,
        has_concrete_lookup: bool,
        is_meta: bool,
        wants_analysis: bool,
        has_unknown_target: bool,
        entity_set: Set[str],
        allowed_geo_terms: Set[str],
    ) -> CompiledPrompt:
        message_lower = message_text.strip().lower()
        compute_verbs = (
            "detect ", "identify ", "compute ", "correlate ", "locate ",
            "surface ", "map ", "cluster ", "find ", "return ", "run ",
        )
        mode = PromptMode.ANALYSIS
        if has_concrete_lookup:
            mode = PromptMode.QUERY
        elif message_lower.startswith(compute_verbs):
            mode = PromptMode.COMPUTE
        elif is_meta or wants_analysis:
            mode = PromptMode.ANALYSIS

        binding_preview = self._format_binding_preview(entity_set, "ENTITY_SET")
        geo_preview = self._format_binding_preview(allowed_geo_terms, "ALLOWED_GEO")
        constraints = [
            binding_preview,
            geo_preview,
            "If the requested entity, geography, or metric is not grounded in GRAPH_CONTEXT, return UNKNOWN.",
        ]
        if has_unknown_target:
            constraints.append("The concrete lookup target is absent from the current graph. Return UNKNOWN.")
        if getattr(lapt_result, "forbid_dsl", False):
            constraints.append("DSL emission is forbidden for this request.")
        if focus_text:
            constraints.append(focus_text.strip())
        if sensor_grounding_text:
            constraints.append(sensor_grounding_text.strip())
        if ctx_info:
            constraints.append(ctx_info.strip())

        shared_user = (
            f"OPERATOR_QUERY:\n{message_text}\n\n"
            f"GRAPH_CONTEXT:\n{mcp_text}\n\n"
            + "\n".join(constraints)
        ).strip()

        if mode == PromptMode.QUERY:
            system_prompt = (
                "You are RF_SCYTHE GraphOps Query mode.\n"
                "Output ONLY one FIND query on its own line OR an UNKNOWN response.\n"
                "Never output prose sections, prompt instructions, internal references, or geography narrative.\n"
                "Use only entities from ENTITY_SET. If the target is absent, return:\n"
                "UNKNOWN\nReason: <brief reason>\nNext steps:\n1. <step>\n2. <step>"
            )
        elif mode == PromptMode.COMPUTE:
            system_prompt = (
                "You are RF_SCYTHE GraphOps Compute mode.\n"
                "Answer analytical requests with compact evidence-bound text.\n"
                "Allowed output contract:\n"
                "RESULT: <1-2 short lines>\n"
                "SUPPORT: <cite graph-backed evidence or say insufficient data>\n"
                "NEXT: 1. <step> 2. <step>\n"
                "If unsupported entities, unsupported geography, or insufficient evidence appear, return UNKNOWN.\n"
                "Do NOT output FIND queries, SITUATION/CHANGE/STRUCTURE/GEOGRAPHY/ASSESSMENT/DIRECTION blocks, "
                "EPISTEMIC RULES, RULES, MCP_CONTEXT, or LEDGER_STATE."
            )
        else:
            system_prompt = (
                "You are RF_SCYTHE GraphOps Analysis mode.\n"
                "Produce a short analyst answer grounded only in GRAPH_CONTEXT.\n"
                "Keep it to three blocks maximum:\n"
                "SUMMARY: <brief grounded assessment>\n"
                "EVIDENCE: <what graph-backed evidence supports it>\n"
                "NEXT: 1. <step> 2. <step>\n"
                "If the answer would rely on unsupported geography, unsupported entities, or speculation, return UNKNOWN.\n"
                "Never echo prompt instructions or internal policy blocks."
            )

        return CompiledPrompt(
            mode=mode,
            system_prompt=system_prompt,
            user_prompt=shared_user,
            allow_dsl=(mode == PromptMode.QUERY and not has_unknown_target and not getattr(lapt_result, "forbid_dsl", False)),
            require_unknown_on_gap=True,
            entity_set=entity_set,
            allowed_geo_terms=allowed_geo_terms,
            source_query=message_text,
        )

    @dispatch_sentinel
    def send_chat_request(
        self,
        message_text: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Process a natural-language operator query.

        Guards (in order):
        1. @dispatch_sentinel — hard depth cap (MAX_DISPATCH_DEPTH)
        2. _in_chat boolean — blocks re-entry from MCP side-effects
        3. LAPT short-circuit — ledger/graph answers bypass LLM
        4. Static tool index — common tool queries answered statically

        Parameters
        ----------
        message_text : str
            The operator's question.
        context : dict, optional
            TAK context (callsign, location, etc.)

        Returns
        -------
        str
            The bot's response text.
        """
        # ── Recursion guard (boolean re-entry) ───────────────────────
        if self._in_chat:
            logger.warning("[tak-gpt] recursion guard: blocked re-entrant send_chat_request")
            return _terminal_response(
                "MCP tool call is terminal — no further chat recursion."
            )
        self._in_chat = True
        try:
            return self._send_chat_inner(message_text, context)
        except Exception as e:
            # ── TERMINAL error handler — never re-enters dispatch ──
            logger.error("[tak-gpt] send_chat_request failed: %s", e)
            return _terminal_response(
                f"Chat request failed: {type(e).__name__}. "
                f"No further analysis possible for this request."
            )
        finally:
            self._in_chat = False

    @dispatch_sentinel
    def _send_chat_inner(
        self,
        message_text: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Inner implementation of send_chat_request (after recursion guard)."""
        # ── Static shortcuts — answered WITHOUT LLM, dispatch, or tools ──
        lower_msg = message_text.strip().lower()

        # Tool status variants (expanded to catch common phrasings)
        _TOOL_STATUS_PHRASES = (
            "list mcp tools", "what tools do you have",
            "show tools", "list tools", "mcp tools",
            "what is the status of your tools",
            "what are your tools", "tool status",
            "what can you do", "capabilities",
            "what tools are available",
            "status of your tools",
            "tools",
        )
        if lower_msg in _TOOL_STATUS_PHRASES:
            return self._static_tool_index()

        # Model ensemble status
        _MODEL_STATUS_PHRASES = (
            "model status", "list models", "show models",
            "what models do you have", "ensemble status",
            "model ensemble", "which models",
        )
        if lower_msg in _MODEL_STATUS_PHRASES:
            return self._model_registry.status_report(self.client)

        # PCAP ingestion shortcuts
        _PCAP_INGEST_PHRASES = (
            "ingest pcaps", "ingest pcap", "ingest all pcaps",
            "pcap ingest", "batch ingest", "run pcap ingest",
            "fetch pcaps", "download pcaps", "pull pcaps",
        )
        if lower_msg in _PCAP_INGEST_PHRASES:
            return self._run_pcap_ingest()

        _PCAP_LIST_PHRASES = (
            "list pcaps", "show pcaps", "available pcaps",
            "pcap list", "what pcaps are available",
        )
        if lower_msg in _PCAP_LIST_PHRASES:
            return self._run_pcap_list()

        _SESSION_SUMMARY_PHRASES = (
            "session summary", "show sessions", "list sessions",
            "ingested sessions", "pcap sessions",
        )
        if lower_msg in _SESSION_SUMMARY_PHRASES:
            return self._run_session_summary()

        # ── LAPT-DSL Gate — Ledger-Aware Prompt Template compiler ────
        #    Fires BEFORE all other gates.  If the query is answerable
        #    from ledger + graph alone, short-circuit without LLM.
        #    Hierarchy: Ledger > Graph > Model (always).
        #
        #    LEDGER_ONLY authority → returns here, never reaches
        #    rule_prompt.py, normalize_edge_kind, or any model call.
        #    This is Fix 5: complete authority bypass.
        lapt_result = self._lapt.compile(message_text)
        if lapt_result.short_circuit:
            logger.info("[tak-gpt] LAPT short-circuit: intent=%s", lapt_result.intent)
            return lapt_result.response

        # ── ANALYST_HEURISTIC fast-path ──────────────────────────────────
        # If LAPT classified this as a heuristic ("educated guess") query,
        # run the LLM with the heuristic system prompt and box the output.
        # The heuristic path:
        #   ✅ runs LLM           ❌ emits no graph edges
        #   ✅ labels uncertainty  ❌ executes no DSL
        #   ✅ suggests next steps ❌ asserts no facts
        from ledger_aware_prompt import (
            Authority, HEURISTIC_SYSTEM_PROMPT, format_heuristic_response,
        )
        if lapt_result.authority == Authority.ANALYST_HEURISTIC:
            logger.info(
                "[tak-gpt] ANALYST_HEURISTIC: intent=%s", lapt_result.intent,
            )
            return self._run_heuristic(
                message_text, lapt_result, context,
            )

        # Build operator context line
        ctx_info = ""
        if context:
            cs = context.get("callsign", "OPERATOR")
            lat = context.get("latitude", "")
            lon = context.get("longitude", "")
            if lat and lon:
                ctx_info = f"\nOperator: {cs} at ({lat}, {lon})"
            else:
                ctx_info = f"\nOperator: {cs}"

        # Build MCP context (replaces old _graph_summary)
        mcp_text = self._build_mcp_context(message_text)

        # Inject MCP_FOCUS if large inference delta present
        focus_text = self._build_mcp_focus()

        # Inject explicit sensor grounding results when the server triggered them
        sensor_grounding_text = self._build_sensor_grounding_block(context)

        # Use mode-isolated prompt compilation instead of injecting raw
        # instruction blocks that the model can echo back to the operator.
        has_concrete_lookup = self._has_concrete_lookup_target(message_text)
        meta_gate = self._detect_meta_analysis(message_text)
        unknown_obj_block = self._check_unknown_object(message_text) if has_concrete_lookup else ""
        analyst_nudge = self._detect_analyst_mode(message_text) if not has_concrete_lookup else ""
        entity_set, allowed_geo_terms = self._build_entity_binding_context()

        if unknown_obj_block:
            return (
                "UNKNOWN\n"
                "Reason: the requested lookup target is not currently present in the graph.\n"
                "Next steps:\n"
                "1. Ingest related pcap or session data containing that target.\n"
                "2. Re-run the query after new sensor evidence arrives."
            )

        compiled_prompt = self._compile_chat_prompt(
            message_text,
            mcp_text=mcp_text,
            focus_text=focus_text,
            sensor_grounding_text=sensor_grounding_text,
            ctx_info=ctx_info,
            lapt_result=lapt_result,
            has_concrete_lookup=has_concrete_lookup,
            is_meta=bool(meta_gate),
            wants_analysis=bool(analyst_nudge),
            has_unknown_target=bool(unknown_obj_block),
            entity_set=entity_set,
            allowed_geo_terms=allowed_geo_terms,
        )

        try:
            # ── Circuit Breaker Gate ──
            if _gemma_circuit_breaker.is_open():
                logger.warning("[tak-gpt] Circuit OPEN — falling back to heuristic response")
                return _terminal_response(
                    "LLM inference temporarily suspended (GPU degraded). "
                    "Graph state is queryable via MCP tools. Retrying in 60s."
                )

            # Use chat API (routes through generate internally)
            data = self.client.chat(
                self.config.model_name,
                messages=[
                    {"role": "system", "content": compiled_prompt.system_prompt},
                    {"role": "user", "content": compiled_prompt.user_prompt},
                ],
                temperature=0.1,
                format_json=False,  # chat responses should be natural text
            )
            _gemma_circuit_breaker.record_success()

            # chat() now routes through generate(), which returns {"response": "..."}
            # (not the old chat format {"message": {"content": "..."}})

            # ── LLM degraded/error gate ──
            # If client.chat() returned a status dict (not a real response),
            # emit a terminal message.  NEVER pass raw JSON to the operator.
            llm_status = data.get("status", "")
            if llm_status in ("degraded", "error"):
                reason = data.get("reason", "unknown")
                logger.warning("[tak-gpt] LLM %s: %s", llm_status, reason)
                return _terminal_response(
                    f"LLM {llm_status}: {reason}. "
                    f"Graph state is still queryable via MCP tools."
                )

            response = (
                data.get("response")
                or data.get("message", {}).get("content", "")
            ).strip()

            # Try to also execute any DSL query the model suggests
            # ── Query Authority Gate (DSL Kill-Switch) ──
            # DSL execution is suppressed when:
            #   1. Meta-analysis gate fired (existing)
            #   2. LAPT authority says LEDGER_ONLY or MODEL_SYNTHESIS
            #   3. LAPT authority says ILLEGAL_EXHAUSTED
            # This prevents the DSL Reflex Loop failure class.
            dsl_allowed = compiled_prompt.allow_dsl and not (lapt_result and lapt_result.forbid_dsl)
            if dsl_allowed:
                response = self._try_execute_dsl(response)

            # ── Fallback: if Gemma returned nothing useful, surface the
            #    MCP summary directly so the operator still gets value ──
            if not response or len(response) < 10:
                return self._fallback_summary(mcp_text)

            firewall = GraphOpsOutputFirewall(
                entity_set=compiled_prompt.entity_set,
                allowed_geo_terms=compiled_prompt.allowed_geo_terms,
            )
            return firewall.evaluate(response, compiled_prompt).response

        except Exception as e:
            # ── TERMINAL error handler — never calls LLM or re-dispatches ──
            _gemma_circuit_breaker.record_failure()
            logger.error("[tak-gpt] chat failed: %s", e)
            return _terminal_response(
                f"LLM request failed ({type(e).__name__}). "
                f"Graph state available via MCP tools. "
                f"No further LLM analysis possible for this request."
            )

    @dispatch_sentinel
    def _run_heuristic(
        self,
        message_text: str,
        lapt_result: Any,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Execute ANALYST_HEURISTIC path — multi-model ensemble.

        MODE-LOCK INVARIANT:
        The model receives ONLY the operator's question + heuristic system
        prompt.  NO graph context, NO ledger context, NO MCP context, NO
        DSL examples.  The model must not even *believe* that DSL, graph
        queries, or structured commands are valid output languages.

        ENSEMBLE DISPATCH:
        1. Classify question into domain (protocol / threat / SRE / general)
        2. Route to domain specialist if matched (llama3.2 + domain prompt)
        3. If ensemble enabled, also run validator (gemma3:1b) for
           disagreement detection
        4. All outputs sanitized through format_heuristic_response()

        Properties:
            ✅ Runs LLM(s) with domain-specific heuristic prompts
            ✅ Labels all output as non-authoritative
            ✅ Surfaces disagreement between models
            ❌ Cannot emit graph edges or FIND queries
            ❌ Cannot assert facts
            ❌ Cannot see graph/ledger/MCP state
            ❌ Cannot recurse (dispatch_sentinel + terminal error)
        """
        from model_registry import ModelRole, classify_domain

        try:
            # ── Domain classification ─────────────────────────────────
            domain_role = classify_domain(message_text)
            if domain_role:
                logger.info(
                    "[tak-gpt] HEURISTIC domain=%s for: %.60s",
                    domain_role.value, message_text,
                )
                primary_role = domain_role
            else:
                primary_role = ModelRole.HEURISTIC_SPECIALIST

            # ── Ensemble dispatch ─────────────────────────────────────
            result = self._model_registry.run_ensemble_heuristic(
                self.client,
                message_text,
                primary_role=primary_role,
            )

            # Log ensemble metadata
            if result.errors:
                for mk, err in result.errors.items():
                    logger.warning("[tak-gpt] ensemble error %s: %s", mk, err)
            if result.execution_time_ms:
                times = ", ".join(
                    f"{k}={v:.0f}ms" for k, v in result.execution_time_ms.items()
                )
                logger.info("[tak-gpt] ensemble timing: %s", times)
            if not result.consensus:
                logger.info(
                    "[tak-gpt] DISAGREEMENT: %s", result.disagreement_summary,
                )

            response = result.primary_response

            if not response or len(response) < 10:
                return _terminal_response(
                    "LLM returned empty heuristic response. "
                    "Try a more specific question about protocols or diagnostics."
                )

            return response

        except Exception as e:
            logger.error("[tak-gpt] heuristic failed: %s", e)
            return _terminal_response(
                f"Heuristic analysis failed ({type(e).__name__}). "
                f"Try rephrasing as a specific diagnostic question."
            )

    def _graph_summary(self) -> str:
        """Quick summary of graph state for context (legacy, prefer MCP)."""
        try:
            nodes = self.hg.nodes if hasattr(self.hg, 'nodes') else {}
            edges = self.hg.edges if hasattr(self.hg, 'edges') else {}

            kinds: Dict[str, int] = {}
            for n in (nodes.values() if isinstance(nodes, dict) else nodes):
                nd = _safe_dict(n)
                k = nd.get('kind', 'unknown')
                kinds[k] = kinds.get(k, 0) + 1

            parts = [f"{v} {k}" for k, v in sorted(kinds.items(), key=lambda x: -x[1])]
            return f"Nodes: {len(nodes)} ({', '.join(parts[:8])}), Edges: {len(edges)}"
        except Exception:
            return "Graph state unavailable"

    @staticmethod
    def _static_tool_index() -> str:
        """Return a static index of available MCP tools (no LLM roundtrip)."""
        return (
            "Available MCP tools (18):\n"
            "  1. graph_summary — Node/edge counts by kind\n"
            "  2. find_nodes — Query nodes by kind/label\n"
            "  3. find_edges — Query edges by kind/obs_class\n"
            "  4. node_detail — Full metadata for a node ID\n"
            "  5. edge_detail — Full metadata for an edge ID\n"
            "  6. subgraph — Neighborhood within N hops\n"
            "  7. confidence_histogram — Distribution of edge confidence tiers\n"
            "  8. lift_edges — Run macro-inference (claim_strength/claim_label)\n"
            "  9. write_summary — Epistemic posture (trust, evidence, coverage)\n"
            " 10. list_collection_tasks — Active collection tasks by status/priority\n"
            " 11. collection_tasks_for_node — Tasks targeting a specific node\n"
            " 12. collection_gap_summary — Top beliefs lacking sensor backing\n"
            " 13. capture_commands — Emit pcap.capture commands for active tasks\n"
            " 14. evaluate_capture_policy — Policy verdict for a capture command\n"
            " 15. inference_history — Past inference runs with timestamps\n"
            " 16. pcap_ingest — Batch-ingest PCAPs from FTP → session hypergraphs\n"
            " 17. pcap_list_ftp — List available PCAP files on FTP server\n"
            " 18. session_summary — Summarize ingested sessions by protocol/host\n"
        )

    def _run_pcap_ingest(self, ftp_url: str = None) -> str:
        """Static shortcut: batch-ingest all PCAPs from FTP → session hypergraphs."""
        try:
            from pcap_ingest import PcapIngestPipeline, IngestConfig
            config = IngestConfig(
                ftp_url=ftp_url or "ftp://172.234.197.23",
            )
            ledger = getattr(self, '_exhaustion_ledger', None)
            pipeline = PcapIngestPipeline(self.hg, ledger, config)
            result = pipeline.ingest_all()
            summary = result.summary()
            graph_state = pipeline.graph_summary_after_ingest()
            return f"{summary}\n\n{graph_state}"
        except Exception as e:
            logger.error("[tak-gpt] PCAP ingest failed: %s", e)
            return f"PCAP ingestion failed: {e}"

    def _run_pcap_list(self, ftp_url: str = None) -> str:
        """Static shortcut: list available PCAPs on FTP server."""
        try:
            from pcap_ingest import FTPFetcher
            fetcher = FTPFetcher(
                ftp_url or "ftp://172.234.197.23",
                "/tmp/pcap_staging",
            )
            files = fetcher.list_pcaps()
            if not files:
                return "No PCAP files found on FTP server."
            lines = [f"Available PCAPs ({len(files)}):"]
            for f in files:
                lines.append(f"  • {f}")
            return "\n".join(lines)
        except Exception as e:
            logger.error("[tak-gpt] PCAP list failed: %s", e)
            return f"Failed to list PCAPs: {e}"

    def _run_session_summary(self) -> str:
        """Static shortcut: summarize ingested sessions."""
        try:
            from pcap_ingest import handle_mcp_session_summary
            result = handle_mcp_session_summary(self.hg)
            if result.get("session_count", 0) == 0:
                return "No sessions ingested yet. Use 'ingest pcaps' to start."
            lines = [
                f"Ingested Sessions: {result['session_count']}",
                f"  Hosts: {result['host_count']}",
                f"  PCAPs: {result['pcap_count']}",
                f"  Protocols: {result['protocols']}",
            ]
            if result.get("pcap_files"):
                lines.append(f"  Source files: {', '.join(result['pcap_files'])}")
            return "\n".join(lines)
        except Exception as e:
            logger.error("[tak-gpt] Session summary failed: %s", e)
            return f"Session summary failed: {e}"

    def _build_mcp_context(self, current_query: str = "") -> str:
        """Build compact MCP context for prompt injection."""
        try:
            from mcp_context import MCPBuilder
            builder = MCPBuilder(self.hg)
            return builder.build_compact(current_query=current_query)
        except Exception as e:
            logger.warning("[tak-gpt] MCP context build failed, falling back: %s", e)
            return self._graph_summary()

    def _build_mcp_focus(self) -> str:
        """
        Build MCP_FOCUS block — analyst-grade inference highlights.
        Only injected when there's a meaningful inference delta (>5 edges).
        """
        try:
            lifting_data = {}
            last_run = {}
            if hasattr(self.hg, '_last_inference_run'):
                run_data = self.hg._last_inference_run or {}
                lifting_data = run_data.get("lifting", {})
                last_run = run_data.get("last_run", {})
            else:
                return ""

            edge_count = last_run.get("edge_count", 0) if last_run else 0
            if edge_count < 5:
                return ""

            lines = ["\n\nMCP_FOCUS (inference highlights):"]

            # Tier breakdown
            ta = last_run.get('tier_a_count', 0)
            tb = last_run.get('tier_b_count', 0)
            tc = last_run.get('tier_c_count', 0)
            ab_pct = round(100 * (ta + tb) / edge_count, 1) if edge_count else 0
            lines.append(f"  {edge_count} inferred edges "
                         f"({ab_pct}% high-confidence: A={ta} B={tb}, shadow C={tc})")

            # Top edge kinds
            edge_kinds = last_run.get("edge_kinds", {})
            if edge_kinds:
                top = sorted(edge_kinds.items(), key=lambda x: -x[1])[:5]
                lines.append("  Top kinds: " + ", ".join(f"{k}({v})" for k, v in top))

            # Lifted macro-edges as claims
            macro = lifting_data.get("macro_edges", [])
            if macro:
                lines.append(f"  Structural claims ({len(macro)} lifted patterns):")
                for me in macro[:6]:
                    label = me.get('claim_label', 'signal')
                    strength = me.get('claim_strength', 0)
                    lines.append(f"    [{label}, {strength:.0%}] {me['description']}")

            # Top degree nodes (from graph)
            try:
                degree = self.hg.degree if hasattr(self.hg, 'degree') else {}
                if degree:
                    top_nodes = sorted(degree.items(), key=lambda x: -x[1])[:3]
                    lines.append("  Highest-degree nodes: " +
                                 ", ".join(f"{nid}(deg={d})" for nid, d in top_nodes))
            except Exception:
                pass

            # Geo singularities
            try:
                singularities = []
                nodes = self.hg.nodes if hasattr(self.hg, 'nodes') else {}
                for n in (nodes.values() if isinstance(nodes, dict) else nodes):
                    nd = n if isinstance(n, dict) else (n.to_dict() if hasattr(n, 'to_dict') else {})
                    if nd.get("kind") == "geo_singularity":
                        singularities.append(nd.get("id", "?"))
                if singularities:
                    lines.append(f"  Geo singularities: {', '.join(singularities[:5])}")
            except Exception:
                pass

            # Epistemic posture from write summary
            try:
                from mcp_context import MCPBuilder
                ws = MCPBuilder(self.hg)._build_write_summary()
                if ws.get('total_writes', 0) > 0:
                    lines.append(f"  Trust posture: {ws['trust_posture']}")
                    lines.append(f"  Evidence coverage: {ws['evidence_coverage']:.0%} "
                                 f"of inferred edges have artifact refs")
                    stale = ws.get('stale_inference_count', 0)
                    if stale > 0:
                        lines.append(f"  Stale inferences: {stale} "
                                     f"(no fresh evidence)")
                    # ── Collection tasking: auto-propose tasks ────────
                    if stale >= 3:
                        try:
                            from collection_tasks import CollectionTaskManager
                            mgr = CollectionTaskManager(self.hg)
                            # Check satisfaction of existing tasks first
                            closed = mgr.check_task_satisfaction()
                            if closed:
                                lines.append(f"  ✅ {len(closed)} collection tasks auto-satisfied")
                            # Expire old tasks
                            expired = mgr.expire_stale_tasks()
                            if expired:
                                lines.append(f"  ⏰ {expired} collection tasks expired")
                            # Auto-propose new tasks for uncovered gaps
                            new_tasks = mgr.auto_propose_from_stale(max_tasks=3)
                            if new_tasks:
                                lines.append(f"  📌 {len(new_tasks)} collection tasks proposed:")
                                for t in new_tasks:
                                    lines.append(f"    [{t.priority}] {t.task_id}: {t.objective}")
                            # Show active task count
                            active = mgr.list_tasks()
                            active_count = sum(1 for t in active if t['status'] in ('proposed','accepted','in_progress'))
                            if active_count > 0:
                                lines.append(f"  📝 {active_count} active collection tasks")
                        except Exception as e:
                            logger.debug("[tak-gpt] collection task auto-emit error: %s", e)
                            lines.append("  ⚠ COLLECTION RECOMMENDED:")
                            lines.append(f"    {stale} inferred edges have no sensor backing.")
            except Exception as e:
                logger.debug("[tak-gpt] write summary in focus error: %s", e)

            return "\n".join(lines)
        except Exception as e:
            logger.debug("[tak-gpt] MCP focus build error: %s", e)
            return ""

    @staticmethod
    def _build_sensor_grounding_block(context: Optional[Dict[str, Any]]) -> str:
        """Build MCP_SENSOR_GROUNDING block from server-provided grounding context."""
        if not context:
            return ""
        sg = context.get("sensor_grounding")
        if not sg:
            return ""

        lines = ["\n\nMCP_SENSOR_GROUNDING:"]
        policy = sg.get("policy") or {}
        if policy:
            lines.append(
                "  posture={posture} evidence_coverage={coverage} stale_inferences={stale} triggered={triggered}".format(
                    posture=policy.get("trust_posture", "unknown"),
                    coverage=policy.get("evidence_coverage", "unknown"),
                    stale=policy.get("stale_inference_count", "unknown"),
                    triggered=policy.get("triggered", False),
                )
            )

        preflight = sg.get("preflight") or {}
        if preflight:
            lines.append(
                "  streamer_available={available} health={health} ws={ws} http={http}".format(
                    available=preflight.get("streamer_available", False),
                    health=preflight.get("health", "unknown"),
                    ws=preflight.get("eve_stream_ws", "?"),
                    http=preflight.get("eve_stream_http", "?"),
                )
            )

        burst = sg.get("burst") or {}
        if burst:
            lines.append(
                "  burst: fetched_ws={fetched} committed={committed} new_nodes={nodes} new_edges={edges} auto_trigger={auto}".format(
                    fetched=burst.get("fetched_ws", 0),
                    committed=burst.get("committed", 0),
                    nodes=burst.get("new_nodes", 0),
                    edges=burst.get("new_edges", 0),
                    auto=burst.get("auto_trigger", False),
                )
            )
            if burst.get("message"):
                lines.append(f"  note: {burst['message']}")
        elif sg.get("reused_recent"):
            lines.append("  note: reused recent grounding burst (<30s old)")

        if sg.get("error"):
            lines.append(f"  error: {sg['error']}")

        return "\n".join(lines)

    # ─────────────────────────────────────────────────────────────────
    # Query Intent Gate (Upgrade #1)
    # ─────────────────────────────────────────────────────────────────

    def _detect_query_intent(self, message: str) -> str:
        """Detect high-confidence query intent and force MODE 1.

        Catches: IPs, hashes, callsigns, explicit command verbs.
        Returns an INSTRUCTION block forcing MODE 1 dispatch, or "".
        """
        import re
        msg = message.strip()
        msg_lower = msg.lower()

        # IPv4
        has_ip = bool(re.search(
            r'\b(?:\d{1,3}\.){3}\d{1,3}\b', msg))
        # IPv6 (simplified — colon-hex)
        has_ipv6 = bool(re.search(
            r'\b[0-9a-fA-F:]{6,39}\b', msg)) and ':' in msg
        # MAC address
        has_mac = bool(re.search(
            r'\b(?:[0-9a-fA-F]{2}[:\-]){5}[0-9a-fA-F]{2}\b', msg))
        # SHA / MD5 hash
        has_hash = bool(re.search(
            r'\b[0-9a-fA-F]{32,64}\b', msg))
        # Callsign pattern (e.g. ALPHA-7, OP-BRAVO)
        has_callsign = bool(re.search(
            r'\b[A-Z]{2,}[\-_][A-Z0-9]{1,}\b', msg))
        # Explicit command verb at start
        cmd_verbs = ('list ', 'show ', 'find ', 'where ', 'get ',
                     'lookup ', 'search ', 'query ', 'count ')
        starts_cmd = any(msg_lower.startswith(v) for v in cmd_verbs)

        # ── Meta-analysis exclusion: abstract structural questions
        #    should NOT trigger DSL dispatch even if they start with
        #    "where" or "find".  These need MODE 2 reasoning, not
        #    a FIND query.  Only exclude when no concrete target
        #    (IP, hash, MAC, callsign) is present. ────────────────
        has_concrete_target = has_ip or has_ipv6 or has_mac or has_hash or has_callsign
        meta_keywords = (
            'structurally', 'structural', 'weak', 'weakness',
            'under-instrumented', 'uninstrumented', 'gap',
            'knowledge gap', 'missing', 'blind spot', 'coverage',
            'evidence', 'epistemic', 'uncertainty', 'stale',
            'meta-analysis', 'meta analysis', 'self-audit',
            'what do we need', 'what are we missing',
            'audit', 'posture', 'grounding', 'confidence',
            'belief', 'drift', 'closure', 'collection task',
            'operator state', 'what is operator',
        )
        is_meta = any(kw in msg_lower for kw in meta_keywords)
        if is_meta and not has_concrete_target:
            starts_cmd = False  # Suppress DSL gate for meta questions

        is_query = has_concrete_target or starts_cmd

        if is_query:
            # Extract the likely lookup target for context
            target = ''
            if has_ip:
                m = re.search(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})', msg)
                target = m.group(1) if m else ''
            elif has_hash:
                m = re.search(r'([0-9a-fA-F]{32,64})', msg)
                target = m.group(1) if m else ''
            elif has_mac:
                m = re.search(r'((?:[0-9a-fA-F]{2}[:\-]){5}[0-9a-fA-F]{2})', msg)
                target = m.group(1) if m else ''

            return (
                f"\n\nINSTRUCTION: Use MODE 1 (Query Dispatch). "
                f"The operator is asking about a specific object"
                f"{' (' + target + ')' if target else ''}. "
                f"Translate to a FIND query. Do NOT produce an analyst narrative. "
                f"If the object is not in the graph, say so explicitly."
            )
        return ""

    # ─────────────────────────────────────────────────────────────────
    # Meta-Analysis Gate (Upgrade #6)
    # ─────────────────────────────────────────────────────────────────

    def _detect_meta_analysis(self, message: str) -> str:
        """Detect structural / epistemic meta-questions.

        These are questions like:
          - "Where is the graph structurally weak?"
          - "What evidence is missing?"
          - "What is Operator X doing?"
          - "Which beliefs exceed sensor evidence?"

        Returns an INSTRUCTION block that:
          1. Suppresses DSL query emission
          2. Constrains the LLM to reason over existing metadata
          3. Forces UNKNOWN for unrecorded state
        """
        msg_lower = message.strip().lower()

        # ── Operator state safety ──
        import re

        # Skip common articles / pronouns / system nouns to avoid
        # false matches like "what is the graph" → name="the"
        _STOP_WORDS = frozenset({
            'the', 'a', 'an', 'this', 'that', 'my', 'our', 'your',
            'graph', 'system', 'engine', 'network', 'cluster',
            'minimum', 'optimal', 'best', 'current', 'overall',
        })

        operator_match = re.search(
            r'(?:[Ww]hat is|[Ww]here is|[Ss]tatus of|[Ww]hat\'s)\s+'
            r'(?:[Oo]perator\s+)?([A-Z][\w\-]{2,})',
            message
        )
        if operator_match:
            name = operator_match.group(1)
            if name.lower() in _STOP_WORDS:
                operator_match = None  # Not a real entity name

        if operator_match:
            name = operator_match.group(1)
            # Check if this name exists as a graph node
            nodes = self.hg.nodes if hasattr(self.hg, 'nodes') else {}
            found = False
            for n in (nodes.values() if isinstance(nodes, dict) else nodes):
                nd = n if isinstance(n, dict) else (n.to_dict() if hasattr(n, 'to_dict') else {})
                nid = nd.get('id', '')
                labels = nd.get('labels') or {}
                all_vals = [nid] + list(str(v) for v in labels.values())
                if any(name.lower() in v.lower() for v in all_vals):
                    found = True
                    break
            if not found:
                return (
                    f"\n\nINSTRUCTION: META-ANALYSIS MODE. "
                    f"'{name}' is NOT recorded as a graph entity. "
                    f"Do NOT hallucinate their state, actions, or intent. "
                    f"Respond: \"{name}: UNKNOWN — not recorded as a node, "
                    f"edge, or event in the graph. To track this entity, "
                    f"create a node of appropriate kind with event logging.\" "
                    f"Then suggest what instrumentation would be required."
                )

        # ── Structural / epistemic meta-questions ──
        meta_patterns = [
            (r'structurally?\s+(?:weak|strong|fragile|robust)',
             'structural weakness'),
            (r'under[- ]?instrumented',
             'instrumentation gaps'),
            (r'(?:knowledge|evidence|sensor)\s*gap',
             'knowledge gaps'),
            (r'blind\s*spot',
             'blind spots'),
            (r'which\s+(?:beliefs?|claims?|inferences?)\s+(?:exceed|lack|have no)',
             'evidence-bound claims'),
            (r'evidence[- ]?(?:only|bound|backed)',
             'evidence-bound analysis'),
            (r'self[- ]?audit',
             'self-audit'),
            (r'(?:minimum|optimal|best)\s+(?:capture|collection)',
             'collection optimization'),
            (r'(?:collapse|eliminate|reduce)\s+.*(?:uncertainty|stale|gap)',
             'uncertainty reduction'),
            (r'(?:rank|prioritize)\s+.*(?:task|collection|capture)',
             'collection ranking'),
            (r'(?:adversary|attacker|threat\s*actor)\s+(?:can|could|might|would)|'
             r'(?:can|could|might|would)\s+(?:the\s+)?(?:adversary|attacker|threat\s*actor)',
             'adversary modeling'),
        ]

        matched_type = None
        for pattern, ptype in meta_patterns:
            if re.search(pattern, msg_lower):
                matched_type = ptype
                break

        if not matched_type:
            # Broader keyword check
            broad_meta = (
                'what do we need', 'what are we missing',
                'what evidence is missing', 'where are the gaps',
                'epistemic', 'posture', 'grounding',
            )
            for kw in broad_meta:
                if kw in msg_lower:
                    matched_type = 'epistemic meta-question'
                    break

        if not matched_type:
            return ""

        # Build the meta-analysis instruction block
        instruction = (
            f"\n\nINSTRUCTION: META-ANALYSIS MODE ({matched_type}). "
            f"Do NOT emit FIND queries. Do NOT issue DSL commands. "
            f"Reason ONLY over existing graph metadata, task state, "
            f"policy state, and evidence coverage from MCP_CONTEXT. "
        )

        if 'evidence' in matched_type or 'claims' in matched_type:
            instruction += (
                f"Label each claim: SENSOR (has evidence_refs from pcap_ingest), "
                f"INFERRED (tak-ml inference, cite confidence tier), or "
                f"UNSUPPORTED (no backing artifact). "
            )
        elif 'collection' in matched_type or 'capture' in matched_type:
            instruction += (
                f"Use collection_gap_summary to identify gaps. "
                f"Rank by expected belief_delta per capture second. "
                f"Reference confidence_target from CollectionSpec. "
            )
        elif 'adversary' in matched_type:
            instruction += (
                f"Assume the adversary can only exploit current policy "
                f"constraints and known instrumentation gaps. "
                f"No imaginary capabilities. Ground in actual graph state. "
            )
        else:
            instruction += (
                f"If a claim cannot be grounded in MCP_CONTEXT fields, "
                f"respond UNKNOWN and propose instrumentation or collection. "
            )

        instruction += (
            f"Be concise and evidence-bound. "
            f"Every quantitative claim must cite its MCP_CONTEXT source field."
        )

        return instruction

    # ─────────────────────────────────────────────────────────────────
    # Unknown Object Clarification (Upgrade #2)
    # ─────────────────────────────────────────────────────────────────

    def _check_unknown_object(self, message: str) -> str:
        """Check whether a queried IP / hash / callsign exists in the graph.

        If the target is NOT present, return a clarification block that
        tells the LLM to state absence honestly and suggest next steps.
        """
        import re

        # Extract candidate lookup targets
        targets = []
        for m in re.finditer(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})', message):
            targets.append(('ip', m.group(1)))
        for m in re.finditer(r'([0-9a-fA-F]{32,64})', message):
            targets.append(('hash', m.group(1)))
        for m in re.finditer(r'((?:[0-9a-fA-F]{2}[:\-]){5}[0-9a-fA-F]{2})', message):
            targets.append(('mac', m.group(1)))

        if not targets:
            return ""

        # Search graph nodes for these values
        nodes = self.hg.nodes if hasattr(self.hg, 'nodes') else {}
        all_labels_text = ""
        node_ids = set()
        for n in (nodes.values() if isinstance(nodes, dict) else nodes):
            nd = n if isinstance(n, dict) else (n.to_dict() if hasattr(n, 'to_dict') else {})
            node_ids.add(nd.get('id', ''))
            labels = nd.get('labels') or {}
            for v in labels.values():
                all_labels_text += f" {v}"

        missing = []
        for kind, val in targets:
            if val not in node_ids and val not in all_labels_text:
                missing.append(val)

        if not missing:
            return ""  # All targets found — let normal query proceed

        missing_str = ', '.join(missing)
        return (
            f"\n\nCLARIFICATION: The following objects are NOT currently in the graph: "
            f"{missing_str}. "
            f"Do NOT emit a FIND query for absent objects. Instead, state: "
            f"\"'{missing_str}' is not currently present in the graph.\" "
            f"Then suggest concrete next steps: "
            f"(1) Ingest related pcap containing this address, "
            f"(2) Run inference to discover neighbors, "
            f"(3) Wait for sensor confirmation. "
            f"This reinforces epistemic honesty — we don't query for what we don't have."
        )

    # ─────────────────────────────────────────────────────────────────
    # Packet Dissection Routing (Upgrade #3)
    # ─────────────────────────────────────────────────────────────────

    def _detect_dissection_request(self, message: str) -> str:
        """Detect packet dissection / artifact inspection requests.

        Routes to evidence_refs instead of DSL.  If no artifacts are
        available, instructs the LLM to state absence and recommend
        acquisition.
        """
        import re
        msg_lower = message.lower()

        dissection_patterns = [
            r'\bdissect\b',
            r'\bpacket\s*(?:capture|analysis|inspect|detail|dump)\b',
            r'\bpcap\b',
            r'\bshow.*(?:payload|packet|frame|capture)\b',
            r'\binspect.*(?:traffic|flow|packet)\b',
            r'\btcpdump\b',
            r'\bwireshark\b',
        ]
        is_dissection = any(re.search(p, msg_lower) for p in dissection_patterns)
        if not is_dissection:
            return ""

        # Search for pcap_artifact / evidence_ref nodes
        nodes = self.hg.nodes if hasattr(self.hg, 'nodes') else {}
        edges = self.hg.edges if hasattr(self.hg, 'edges') else {}
        artifacts = []

        for n in (nodes.values() if isinstance(nodes, dict) else nodes):
            nd = n if isinstance(n, dict) else (n.to_dict() if hasattr(n, 'to_dict') else {})
            kind = (nd.get('kind') or '').lower()
            if 'pcap' in kind or 'artifact' in kind or 'capture' in kind:
                artifacts.append(nd.get('id', '?'))

        # Also check for evidence_refs on edges
        evidence_refs = []
        for e in (edges.values() if isinstance(edges, dict) else edges):
            ed = e if isinstance(e, dict) else (e.to_dict() if hasattr(e, 'to_dict') else {})
            meta = ed.get('metadata') or {}
            for prov_key in ('provenance_rule', 'provenance', 'provenance_write'):
                prov = meta.get(prov_key) or {}
                refs = prov.get('evidence_refs') or prov.get('evidence') or []
                evidence_refs.extend(refs)

        # Extract location hints from message
        location_hint = ''
        loc_match = re.search(
            r'(?:from|in|at|near)\s+([A-Z][a-zÀ-ÿ]+(?:\s+[A-Z][a-zÀ-ÿ]+)*)',
            message)
        if loc_match:
            location_hint = loc_match.group(1)

        if artifacts or evidence_refs:
            unique_refs = list(set(evidence_refs))[:10]
            return (
                f"\n\nDISSECTION CONTEXT: This is a packet/artifact inspection request, "
                f"not a graph query. "
                f"Available artifacts: {', '.join(artifacts[:5]) if artifacts else 'none'}. "
                f"Evidence refs on edges: {', '.join(unique_refs[:5]) if unique_refs else 'none'}. "
                f"Describe available artifacts and what they can reveal. "
                f"Do NOT emit a FIND query for packet contents."
            )
        else:
            return (
                f"\n\nDISSECTION CONTEXT: The operator is requesting packet dissection"
                f"{' for ' + location_hint if location_hint else ''}, "
                f"but NO packet artifacts or evidence_refs are currently in the graph. "
                f"State: 'No packet artifacts are available"
                f"{' for ' + location_hint if location_hint else ''}.' "
                f"Recommend: (1) Capture traffic with tcpdump/sensors in the target area, "
                f"(2) Ingest the pcap to populate evidence_refs, "
                f"(3) Re-query after ingestion. "
                f"This is an analyst move — recommend collection, don't fabricate data."
            )

    def _detect_analyst_mode(self, message: str) -> str:
        """Detect if operator query warrants analyst narrative mode.

        Returns a nudge block to prepend before the user message that
        steers the LLM toward narrative synthesis instead of query dispatch.
        """
        import re

        msg_lower = message.lower()

        # Pattern 1: regional situational awareness
        region_patterns = [
            r'\bwhat.{0,15}going on\b',
            r'\bwhat.{0,15}happening\b',
            r'\bsummar',
            r'\boverview\b',
            r'\bsituation\b',
            r'\bbrief\s*me\b',
            r'\bsitrep\b',
            r'\banalyz',
            r'\bassess',
            r'\bexplain\b',
            r'\btell me about\b',
            r'\bwhat changed\b',
            r'\bwhat.{0,10}new\b',
            r'\bany.{0,10}unusual\b',
            r'\bwhat.{0,10}different\b',
            # Abstract analytical commands without a concrete target
            r'\bidentif[yi]',
            r'\bdetect\b',
            r'\bcorrelat',
            r'\bmap\s+(?:the|all|entities|nodes|flows|behavior)',
            r'\bcluster\s+(?:behavior|pattern|analysis|forming)',
            r'\bentit(?:y|ies).{0,30}(?:behavioral|consistenc|pattern)',
            r'\bflow\s+ratio',
            r'\bbidirectional',
            r'\bbelow\s+(?:alert|threshold)',
            r'\bcross[- ](?:ip|asn)',
        ]
        is_narrative = any(re.search(p, msg_lower) for p in region_patterns)

        # Pattern 2: significant inference delta present
        has_delta = False
        if hasattr(self.hg, '_last_inference_run'):
            run_data = self.hg._last_inference_run or {}
            last = run_data.get('last_run', {})
            lifting = run_data.get('lifting', {})
            if last.get('edge_count', 0) >= 20 or lifting.get('lifted_count', 0) >= 3:
                has_delta = True

        # Pattern 3: geo singularity touched
        has_singularity = False
        try:
            nodes = self.hg.nodes if hasattr(self.hg, 'nodes') else {}
            for n in (nodes.values() if isinstance(nodes, dict) else nodes):
                nd = n if isinstance(n, dict) else (n.to_dict() if hasattr(n, 'to_dict') else {})
                if nd.get('kind') == 'geo_singularity':
                    has_singularity = True
                    break
        except Exception:
            pass

        if is_narrative or (has_delta and has_singularity):
            return (
                "\n\nINSTRUCTION: MODE 2. Write a prose analyst assessment "
                "(Situation / Change / Structure / Geography / Assessment / Direction). "
                "Start with a credibility line from WRITE_SUMMARY. "
                "Use MCP_CONTEXT data only — no fabrication."
            )
        return ""

    def _build_belief_drift_block(self) -> str:
        """Format belief drift for prompt injection."""
        try:
            drift = compute_belief_drift()
            if drift.get('verdict') == 'insufficient_data':
                return ""

            lines = ["\n\nBELIEF_DRIFT:"]
            lines.append(f"  Verdict: {drift['verdict']}")
            lines.append(f"  Edge count delta: {drift['edge_count_delta']:+d}")

            ts = drift.get('tier_shift', {})
            lines.append(f"  Tier shift: A={ts.get('tier_a', 0):+d} "
                         f"B={ts.get('tier_b', 0):+d} C={ts.get('tier_c', 0):+d}")

            new = drift.get('new_kinds', {})
            if new:
                lines.append(f"  New kinds: {', '.join(f'{k}({v})' for k, v in new.items())}")
            lost = drift.get('lost_kinds', {})
            if lost:
                lines.append(f"  Lost kinds: {', '.join(f'{k}({v})' for k, v in lost.items())}")
            stronger = drift.get('strengthened', {})
            if stronger:
                top = sorted(stronger.items(), key=lambda x: -x[1])[:5]
                lines.append(f"  Strengthened: {', '.join(f'{k}(+{v})' for k, v in top)}")
            weaker = drift.get('weakened', {})
            if weaker:
                top = sorted(weaker.items(), key=lambda x: -x[1])[:5]
                lines.append(f"  Weakened: {', '.join(f'{k}(-{v})' for k, v in top)}")

            return "\n".join(lines)
        except Exception as e:
            logger.debug("[tak-gpt] belief drift block error: %s", e)
            return ""

    @dispatch_sentinel
    def _fallback_summary(self, mcp_text: str) -> str:
        """When Gemma gives no useful answer, generate a structured analyst summary.

        This is the LAST WALL before the operator.  It must NEVER:
        - Call the LLM
        - Re-enter send_chat_request / _send_chat_inner
        - Raise an exception that propagates

        If anything inside fails, return a terminal static response.
        """
        try:
            return self._fallback_summary_inner(mcp_text)
        except Exception as e:
            logger.error("[tak-gpt] _fallback_summary failed: %s", e)
            return _terminal_response(
                "Unable to generate fallback summary. "
                "Graph state available via MCP tools."
            )

    def _fallback_summary_inner(self, mcp_text: str) -> str:
        """Inner implementation of _fallback_summary (wrapped by terminal guard)."""
        last_run = get_last_inference_run()
        parts = [
            "UNKNOWN",
            "Reason: no grounded model response was available; returning graph-state-only fallback.",
            "Graph summary:",
            mcp_text,
        ]

        if last_run:
            parts.extend([
                "",
                "Last inference run:",
                f"- {last_run['edge_count']} edges (A={last_run['tier_a_count']}, "
                f"B={last_run['tier_b_count']}, C={last_run['tier_c_count']}) "
                f"in {last_run['duration_seconds']}s",
            ])

        try:
            from mcp_context import MCPBuilder
            ws = MCPBuilder(self.hg)._build_write_summary()
            if ws.get('total_writes', 0) > 0:
                parts.extend([
                    "",
                    "Credibility:",
                    f"- trust posture: {ws['trust_posture']}",
                ])
                bs = ws.get('by_source', {})
                parts.append(
                    f"- sources: sensor={bs.get('sensor', 0)}, inference={bs.get('inference', 0)}, analyst={bs.get('analyst', 0)}"
                )
                parts.append(f"- evidence coverage: {ws['evidence_coverage']:.0%}")
                stale = ws.get('stale_inference_count', 0)
                if stale > 0:
                    parts.append(f"- stale inferences: {stale}")
        except Exception:
            pass

        parts.extend([
            "",
            "Next steps:",
            "1. Ask a narrower graph-backed question with a concrete entity or IP.",
            "2. Collect or ingest fresh sensor evidence before retrying analysis.",
        ])

        return "\n".join(parts)

    @dispatch_sentinel
    def _try_execute_dsl(self, response: str) -> str:
        """
        If the response contains a FIND query, try executing it and append results.
        Terminal-safe: returns original response on any failure.
        """
        import re
        match = re.search(r'(FIND\s+(?:NODES|EDGES|NEIGHBORS|SUBGRAPH)\s+.+?)(?:\n|$)', response, re.IGNORECASE)
        if not match:
            return response

        query = match.group(1).strip()
        try:
            from graph_query_dsl import execute_query
            results = execute_query(query, self.hg)
            if results:
                result_summary = f"\n\n📊 Query results ({len(results)} items):\n"
                for r in results[:10]:
                    rd = _safe_dict(r)
                    nid = rd.get('node_id') or rd.get('id', '?')
                    kind = rd.get('kind', '?')
                    ip = (rd.get('labels') or {}).get('ip', '')
                    result_summary += f"  • {kind}: {nid}"
                    if ip:
                        result_summary += f" ({ip})"
                    result_summary += "\n"
                if len(results) > 10:
                    result_summary += f"  ... and {len(results) - 10} more\n"
                return response + result_summary
        except Exception as e:
            logger.debug("[tak-gpt] DSL execution failed: %s", e)

        return response
