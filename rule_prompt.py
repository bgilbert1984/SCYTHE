"""
rule_prompt.py — Prompt templates for schema-bound LLM inference over RF SCYTHE ontology.

Builds prompts that constrain Gemma 3 (or any structured-output LLM) to emit
*only* deterministic JSON matching the GraphOp schema.

Usage:
    from rule_prompt import build_flow_prompt, build_host_prompt, SYSTEM_PROMPT
    prompt = build_flow_prompt(flow_node, related_edges, related_nodes)
    # → feed to GemmaClient.generate_json(model, prompt, system=SYSTEM_PROMPT)
"""
from __future__ import annotations

import json
import logging
import functools
import threading
from typing import Any, Dict, List, Optional
from takml_runtime_metrics import get_takml_runtime_metrics_tracker

logger = logging.getLogger(__name__)
_runtime_metrics = get_takml_runtime_metrics_tracker()


# ─────────────────────────────────────────────────────────────────────────────
# Recursion Sentinel
# ─────────────────────────────────────────────────────────────────────────────
# Global call-depth counter that trips BEFORE Python's 1000-frame limit.
# Applied as a decorator to normalize_edge_kind, validate_gemma_output, and
# auto_materialize_missing_nodes.  Each thread tracks independently.
#
# If depth exceeds MAX_RECURSION_DEPTH, the sentinel raises RecursionSentinelError
# with a clean diagnostic instead of letting Python die with a raw RecursionError.
MAX_RECURSION_DEPTH: int = 50  # conservative ceiling — normal depth is < 5

_sentinel_depth = threading.local()


class RecursionSentinelError(RuntimeError):
    """Raised when the recursion sentinel detects runaway call depth."""
    pass


def recursion_sentinel(fn):
    """Decorator that enforces MAX_RECURSION_DEPTH on any wrapped function."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        depth_key = f"_depth_{fn.__qualname__}"
        current = getattr(_sentinel_depth, depth_key, 0)
        if current >= MAX_RECURSION_DEPTH:
            raise RecursionSentinelError(
                f"[RECURSION SENTINEL] {fn.__qualname__} hit depth "
                f"{current} (max={MAX_RECURSION_DEPTH}). "
                f"This indicates a micro-recursion loop that was not "
                f"caught by earlier guards."
            )
        setattr(_sentinel_depth, depth_key, current + 1)
        try:
            return fn(*args, **kwargs)
        finally:
            setattr(_sentinel_depth, depth_key, current)
    return wrapper


# ─────────────────────────────────────────────────────────────────────────────
# System prompt (constant across all inference calls)
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are the GraphOps Agent for the RF_SCYTHE Hypergraph (v0.1).
You evaluate network-domain rules against a hypergraph snapshot and return ONLY valid JSON.

Your role:
- Interpret context.
- Query the hypergraph using the provided API.
- Produce structured JSON for hypergraph mutations (GraphOps).
- Never output free text.
- Never invent node IDs, edge IDs, or data not present in the hypergraph.

Hypergraph Model (RF_SCYTHE Ontology v0.1):
Nodes have:
  id, kind, position [lat,lon,alt], labels, metadata, created_at, updated_at

Canonical node kinds:
  host, flow, geo_point, geo_cell, geo_singularity, geo_streamline,
  geo_ridge, geo_patch, service, port_hub, dns_name, tls_sni, http_host,
  asn, org, pcap_session, pcap_artifact, rf

Edges have:
  id, kind, nodes [src,dst], weight, labels, metadata, timestamp

┌─────────────────────────────────────────────────────────────────────────┐
│ CRITICAL: VALID INFERRED EDGE KINDS (EXACT MATCH ONLY)                 │
├─────────────────────────────────────────────────────────────────────────┤
│ When you emit an inferred edge, its 'kind' MUST be EXACTLY ONE of:     │
│                                                                          │
│  • INFERRED_HOST_IN_ORG                                                │
│  • INFERRED_FLOW_IN_SERVICE                                            │
│  • INFERRED_HOST_OFFERS_SERVICE                                        │
│  • INFERRED_HOST_CONTACTED_SNI                                         │
│  • INFERRED_HOST_CONTACTED_HTTP_HOST                                   │
│  • INFERRED_FLOW_SNI_EQ_HTTP_HOST                                      │
│  • INFERRED_HOST_QUERIED_DNSNAME                                       │
│  • INFERRED_DNSNAME_RESOLVES_HOST                                      │
│  • INFERRED_FLOW_CROSS_BORDER                                          │
│  • INFERRED_HOST_ROLE                                                  │
│  • INFERRED_FLOW_ON_RIDGE                                              │
│  • INFERRED_HOST_AT_SINGULARITY                                        │
│  • INFERRED_FLOW_ALIGNED_WITH_STREAMLINE                               │
│                                                                          │
│ REJECTION RULES:                                                       │
│  X  Do NOT emit SESSION_OBSERVED_*, FLOW_*, HOST_*, or any             │
│     observed/sensor-grounded kinds — these are RESERVED for sensors.   │
│  X  Do NOT invent new INFERRED_* kinds not in the list above.          │
│  X  Do NOT use identifiers (IPs, ports, hostnames, IDs) in kind field. │
│  X  Do NOT embed numbers in the kind name (e.g. INFERRED_SESSION-123). │
│                                                                          │
│ If no valid inferred relationship applies EXACTLY, emit no edge.       │
│ Silence is preferable to hallucination.                                │
└─────────────────────────────────────────────────────────────────────────┘

Inference Authorization:
You are explicitly permitted to create inferred edges ONLY when:
- Both source and destination nodes already exist in the hypergraph context.
- The relationship matches one of the INFERRED_* edge kinds in the box above.
- Sufficient evidence exists in observed or implied edges (confidence ≥ 0.6).

You must:
- Use ONLY node IDs and edge IDs from the input data. Never invent IDs.
- Emit inferred edges when a rule clearly applies and evidence is sufficient.
- Return an empty JSON array [] if no inference applies.
- NEVER emit edge kinds outside the INFERRED_* list above.

When mode = graphops_infer:
- Actively search for applicable inference rules.
- Do not wait for the operator to name a rule.
- Produce all valid inferred edges up to system caps.
- Check each emitted edge against the VALID INFERRED EDGE KINDS list above.

Output schema (return a JSON array of these objects):
{
  "rule_id": "R-XXX-000",
  "should_fire": true,
  "confidence": 0.85,
  "evidence": ["edge_id_1", "edge_id_2"],
  "inferred_edges": [
    {
      "kind": "INFERRED_HOST_IN_ORG",
      "src": "node_id",
      "dst": "node_id"
    }
  ],
  "tags": {}
}
"""

# ─────────────────────────────────────────────────────────────────────────────
# JSON output schema (shown to the model for reference)
# ─────────────────────────────────────────────────────────────────────────────

RESULT_SCHEMA = {
    "rule_id": "R-XXX-000",
    "should_fire": True,
    "confidence": 0.0,
    "evidence": ["edge_id"],
    "inferred_edges": [
        {"kind": "INFERRED_*", "src": "node_id", "dst": "node_id"}
    ],
    "tags": {},
}

# Inferred edge kinds the model may emit
VALID_INFERRED_KINDS = frozenset({
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
    # Geo-topological inference kinds (Hairy Ball Theorem features)
    "INFERRED_FLOW_ON_RIDGE",
    "INFERRED_HOST_AT_SINGULARITY",
    "INFERRED_FLOW_ALIGNED_WITH_STREAMLINE",
})

# ── Edge Kind Normalizer ─────────────────────────────────────────────────
# Maps common Gemma hallucinated/drifted edge kinds to their canonical
# VALID_INFERRED_KINDS equivalent.  If a kind isn't in this map AND
# isn't in VALID_INFERRED_KINDS, it gets dropped.
#
# This is the single choke-point for schema drift suppression.
EDGE_KIND_ALIASES: Dict[str, str] = {
    # ── LLM Confusion: Observed kinds emitted as inferred variants ──
    # These are REJECTED rather than remapped (Fix via author check above)
    # because they indicate the model is confusing zones. Catching them early
    # prevents data corruption.

    # ── Common suffix reductions / typos ──
    "OBSERVED_HOST":                  None,  # Drop — observed zone
    "OBSERVED_FLOW":                  None,  # Drop — observed zone
    "SESSION_OBSERVED":               None,  # Drop — observed zone
    "SESSION_FLOW":                   None,  # Drop — observed zone

    # ── Full observed-zone combined forms (model emits as inferred — drop) ──
    # These are valid OBSERVED kinds but must never appear as INFERRED output.
    # Listed explicitly so normalize_edge_kind returns None via the alias path
    # (deliberate schema-policy drop) rather than the unknown-kind path,
    # allowing the validator to log at a lower severity.
    "SESSION_OBSERVED_FLOW":          None,  # OBSERVED zone — not for model output
    "SESSION_OBSERVED_HOST":          None,  # OBSERVED zone — not for model output
    "FLOW_OBSERVED_FLOW":             None,  # Hallucinated compound form
    "HOST_OBSERVED_FLOW":             None,  # OBSERVED zone — not for model output
    "PORT_OBSERVED_FLOW":             None,  # Hallucinated compound form
    "FLOW_SRC":                       "INFERRED_FLOW_IN_SERVICE",
    "FLOW_DST":                       "INFERRED_FLOW_IN_SERVICE",
    "FLOW_HAS_SRC_HOST":              "INFERRED_FLOW_IN_SERVICE",
    "FLOW_HAS_DST_HOST":              "INFERRED_FLOW_IN_SERVICE",
    "FLOW_DST_PORT":                  None,  # Drop — OBSERVED, not INFERRED
    "FLOW_TLS_SNI":                   None,  # Drop — OBSERVED
    "FLOW_HTTP_HOST":                 None,  # Drop — OBSERVED
    "FLOW_QUERIED_DNS":               None,  # Drop — OBSERVED
    "FLOW_DNS_ANSWER":                None,  # Drop — OBSERVED

    # ── Host-query variants (often misemitted) ──
    "HOST_CONTACTED_SNI":             "INFERRED_HOST_CONTACTED_SNI",
    "HOST_CONTACTED_HTTP":            "INFERRED_HOST_CONTACTED_HTTP_HOST",
    "HOST_QUERIED_DNS":               "INFERRED_HOST_QUERIED_DNSNAME",
    "HOST_QUERIED_DNSNAME":           "INFERRED_HOST_QUERIED_DNSNAME",
    "HOST_GEO_ESTIMATE":              None,  # Drop — observed zone
    "HOST_LOCATED_AT_GEOPOINT":       "INFERRED_FLOW_CROSS_BORDER",
    "HOST_MEMBER_OF_ORG":             "INFERRED_HOST_IN_ORG",
    "HOST_IN_ORG":                    "INFERRED_HOST_IN_ORG",

    # ── Complete INFERRED_ prefix variants with wrong suffixes ──
    "INFERRED_FLOW_SRC":              "INFERRED_FLOW_IN_SERVICE",
    "INFERRED_FLOW_DST":              "INFERRED_FLOW_IN_SERVICE",
    "INFERRED_FLOW_DST_PORT":         "INFERRED_FLOW_IN_SERVICE",
    "INFERRED_FLOW_TLS_SNI":          "INFERRED_HOST_CONTACTED_SNI",
    "INFERRED_FLOW_HTTP_HOST":        "INFERRED_HOST_CONTACTED_HTTP_HOST",
    "INFERRED_FLOW_QUERIED_DNS":      "INFERRED_HOST_QUERIED_DNSNAME",
    "INFERRED_FLOW_DNS_ANSWER":       "INFERRED_DNSNAME_RESOLVES_HOST",
    "INFERRED_HOST_GEO_ESTIMATE":     "INFERRED_FLOW_CROSS_BORDER",
    "INFERRED_HOST_CONTACTED":        "INFERRED_HOST_CONTACTED_SNI",
    "INFERRED_HOST_QUERIED":          "INFERRED_HOST_QUERIED_DNSNAME",

    # ── Casing variants / incomplete spellings ──
    "INFERRED_DNS_RESOLVES":          "INFERRED_DNSNAME_RESOLVES_HOST",
    "INFERRED_CROSS_BORDER":          "INFERRED_FLOW_CROSS_BORDER",
    "INFERRED_HOST_OFFERS":           "INFERRED_HOST_OFFERS_SERVICE",
    "INFERRED_SERVICE_ALIAS":         "INFERRED_FLOW_SNI_EQ_HTTP_HOST",
    "INFERRED_OFFERS_SERVICE":        "INFERRED_HOST_OFFERS_SERVICE",

    # ── Entropy Explosion Fix (gemma_runner.md Stage 1) ──────────────────────
    # Exact kinds from doc + high-frequency hallucination variants observed
    # in exhaustion logs.  Rule: preserve semantic meaning, map to closest
    # VALID_INFERRED_KINDS canonical.
    "FLOW_OBSERVED":                  "INFERRED_FLOW_IN_SERVICE",  # bare form — model means "a flow exists"
    "FLOW_OBSERVED_PORT":             "INFERRED_HOST_OFFERS_SERVICE",
    "FLOW_OBSERVED_SERVICE":          "INFERRED_HOST_OFFERS_SERVICE",
    "HOST_OBSERVED":                  "INFERRED_HOST_ROLE",
    "HOST_OBSERVED_SERVICE":          "INFERRED_HOST_OFFERS_SERVICE",
    "OBSERVED":                       "INFERRED_FLOW_IN_SERVICE",   # bare "observed" fallback
    "FLOW_HOST_TO_HOST":              "INFERRED_FLOW_IN_SERVICE",
    "FLOW_TO_HOST":                   "INFERRED_FLOW_IN_SERVICE",
    "FLOW_FROM_HOST":                 "INFERRED_FLOW_IN_SERVICE",
    "FLOW_BETWEEN_HOSTS":             "INFERRED_FLOW_IN_SERVICE",
    "FLOW_OBSERVED_HOST":             None,   # Drop — observed zone
    "FLOW_CONNECTS_HOST":             "INFERRED_FLOW_IN_SERVICE",
    "SESSION_BETWEEN_HOSTS":          "INFERRED_FLOW_IN_SERVICE",
    "SESSION_CONNECTS_HOST":          "INFERRED_FLOW_IN_SERVICE",
    "HOST_FLOWS_TO":                  "INFERRED_FLOW_IN_SERVICE",
    "HOST_IN_ASN":                    "INFERRED_HOST_IN_ORG",
    "HOST_ASN":                       "INFERRED_HOST_IN_ORG",
    "PORT_HUB":                       "INFERRED_HOST_OFFERS_SERVICE",
    "PORT_CLUSTER":                   "INFERRED_HOST_OFFERS_SERVICE",
    # Truncated/partial INFERRED_ forms the model sometimes emits
    "INFERRED_FLOW":                  "INFERRED_FLOW_IN_SERVICE",
    "INFERRED_SESSION":               "INFERRED_FLOW_IN_SERVICE",
    "INFERRED_HOST_IN_ASN":           "INFERRED_HOST_IN_ORG",
    "INFERRED_PORT_CLUSTER":          "INFERRED_HOST_OFFERS_SERVICE",
    "INFERRED_PORT_HUB":              "INFERRED_HOST_OFFERS_SERVICE",
    "INFERRED_HOST_ROLE_SERVICE":     "INFERRED_HOST_ROLE",
}

# Valid *observed* edge kinds (not inferred — these exist from pcap ingest)
VALID_OBSERVED_KINDS = frozenset({
    "SESSION_OBSERVED_HOST", "SESSION_OBSERVED_FLOW",
    "HOST_GEO_ESTIMATE", "HOST_IN_ASN", "ASN_IN_ORG",
    "FLOW_DST_PORT", "FLOW_TLS_SNI", "FLOW_HTTP_HOST",
    "FLOW_QUERIED_DNS", "FLOW_DNS_ANSWER",
})

# ── Edge Authority Partition ────────────────────────────────────────────
# Every edge kind belongs to exactly ONE authority zone.
# The validator uses this to reject edges that cross zones — e.g.
# SESSION_OBSERVED_HOST (zone=OBSERVED) must never be emitted by an
# inference prompt (zone=INFERRED).  This severs the micro-recursion
# where an observed kind gets alias-mapped to an inferred kind, then
# fails validation, then triggers a retry.
#
# Zones:
#   OBSERVED  — sensor-grounded, never the output of the model
#   INFERRED  — model-produced, must be in VALID_INFERRED_KINDS
#   IMPLIED   — derived from observation by deterministic rule
EDGE_ZONE_OBSERVED = "OBSERVED"
EDGE_ZONE_INFERRED = "INFERRED"
EDGE_ZONE_IMPLIED  = "IMPLIED"

EDGE_AUTHORITY: Dict[str, str] = {}
# Populate observed
for _ek in VALID_OBSERVED_KINDS:
    EDGE_AUTHORITY[_ek] = EDGE_ZONE_OBSERVED
# Populate inferred
for _ek in VALID_INFERRED_KINDS:
    EDGE_AUTHORITY[_ek] = EDGE_ZONE_INFERRED
# Populate implied (PORT_IMPLIED_SERVICE and any future IMPLIED_*)
EDGE_AUTHORITY["PORT_IMPLIED_SERVICE"] = EDGE_ZONE_IMPLIED

# Valid node kinds for auto-materialization checks
VALID_NODE_KINDS = frozenset({
    "host", "flow", "geo_point", "geo_cell", "geo_singularity",
    "geo_streamline", "geo_ridge", "geo_patch", "service",
    "port_hub", "dns_name", "tls_sni", "http_host",
    "asn", "org", "pcap_session", "pcap_artifact", "rf",
    "collection_task",
})


@recursion_sentinel
def normalize_edge_kind(kind: str, *, _already_normalized: bool = False) -> Optional[str]:
    """Normalize an edge kind to its canonical form.

    Idempotency contract: if ``_already_normalized`` is True the function
    returns *kind* unchanged (the caller already ran normalization once).
    A kind that is already a member of VALID_INFERRED_KINDS is also
    returned immediately — no alias lookup, no regex, no allocation.

    Steps (when not already normalized):
    1. Uppercase + strip
    2. Reject kinds containing digits (embedded IDs)
    3. Check VALID_INFERRED_KINDS directly  → already canonical
    4. Check EDGE_KIND_ALIASES
    5. Return None if unmappable (caller should drop)
    """
    # ── Idempotency gate ──
    if _already_normalized:
        return kind if kind else None

    if not kind:
        return None
    canonical = kind.strip().upper()

    # Fast-path: already canonical — skip everything else
    if canonical in VALID_INFERRED_KINDS:
        return canonical

    import re
    # Pattern: model hallucinates "session_observed_SESSION-xxx" as an edge kind.
    # Upper-cased this becomes SESSION_OBSERVED_SESSION-HEXID or SESSION_OBSERVED_HEXID.
    # These are always schema-policy drops — return "" (not None) so the validator
    # logs DEBUG instead of wasting a semantic repair attempt.
    if re.match(r'^SESSION_OBSERVED_[A-Z0-9_\-]{6,}$', canonical):
        return ""  # schema-policy drop

    # Reject kinds with embedded identifiers (digits, IP-like patterns)
    if re.search(r'\d{1,3}\.\d{1,3}', canonical) or re.search(r'_\d{2,}', canonical):
        return None

    # Check alias map first — if it maps to None, reject it (observed zone or hallucination)
    if canonical in EDGE_KIND_ALIASES:
        mapped = EDGE_KIND_ALIASES[canonical]
        if mapped is None:
            # Deliberately dropped by schema policy — return "" as sentinel so
            # the validator can distinguish an explicit policy drop (log DEBUG)
            # from a truly unknown kind (log WARNING).
            return ""
        if mapped in VALID_INFERRED_KINDS:
            return mapped
        # Mapped to something invalid — reject
        return None

    # Direct check — already canonical
    if canonical in VALID_INFERRED_KINDS:
        return canonical

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Prompt builders
# ─────────────────────────────────────────────────────────────────────────────

def _compact_json(obj: Any) -> str:
    """Minified JSON for prompt compactness."""
    return json.dumps(obj, separators=(",", ":"), default=str)


def _safe_node(node: Any) -> Dict[str, Any]:
    """Extract a safe dict from a node (handles objects with to_dict())."""
    if isinstance(node, dict):
        return node
    if hasattr(node, "to_dict"):
        return node.to_dict()
    return {"id": str(node)}


def _safe_edge(edge: Any) -> Dict[str, Any]:
    """Extract a safe dict from an edge."""
    if isinstance(edge, dict):
        return edge
    if hasattr(edge, "to_dict"):
        return edge.to_dict()
    return {"id": str(edge)}


def build_flow_prompt(
    flow_node: Any,
    related_edges: List[Any],
    related_nodes: Dict[str, Any],
    *,
    rules: Optional[List[str]] = None,
) -> str:
    """
    Build an inference prompt for a single flow node and its neighborhood.

    Parameters
    ----------
    flow_node : node dict or object
        The flow being evaluated.
    related_edges : list
        Edges touching this flow.
    related_nodes : dict
        node_id → node dict for all neighbors.
    rules : list[str], optional
        Specific rule IDs to evaluate. Default: all rules.
    """
    flow = _safe_node(flow_node)
    edges = [_safe_edge(e) for e in related_edges]
    nodes = {k: _safe_node(v) for k, v in related_nodes.items()}

    payload = {
        "context": "flow_inference",
        "mode": "graphops_infer",
        "confidence_threshold": 0.6,
        "flow": flow,
        "edges": edges,
        "neighbor_nodes": nodes,
    }
    if rules:
        payload["evaluate_rules"] = rules

    return (
        f"Evaluate the following flow context against the RF_SCYTHE v0.1 rules.\n"
        f"Return a JSON array of rule results.\n\n"
        f"INPUT:\n{_compact_json(payload)}"
    )


def build_host_prompt(
    host_node: Any,
    related_edges: List[Any],
    related_nodes: Dict[str, Any],
    *,
    rules: Optional[List[str]] = None,
) -> str:
    """
    Build an inference prompt for a single host node and its neighborhood.
    Good for R-ORG-001, R-ROLE-001, R-DNS-001, etc.
    """
    host = _safe_node(host_node)
    edges = [_safe_edge(e) for e in related_edges]
    nodes = {k: _safe_node(v) for k, v in related_nodes.items()}

    payload = {
        "context": "host_inference",
        "mode": "graphops_infer",
        "confidence_threshold": 0.6,
        "host": host,
        "edges": edges,
        "neighbor_nodes": nodes,
    }
    if rules:
        payload["evaluate_rules"] = rules

    return (
        f"Evaluate the following host context against the RF_SCYTHE v0.1 rules.\n"
        f"Return a JSON array of rule results.\n\n"
        f"INPUT:\n{_compact_json(payload)}"
    )


def build_batch_prompt(
    nodes_batch: List[Any],
    edges_batch: List[Any],
    *,
    rules: Optional[List[str]] = None,
) -> str:
    """
    Build a batch prompt for multiple entities at once.
    Use this when the total token count is small enough for a single call.
    """
    payload = {
        "context": "batch_inference",
        "mode": "graphops_infer",
        "confidence_threshold": 0.6,
        "nodes": [_safe_node(n) for n in nodes_batch],
        "edges": [_safe_edge(e) for e in edges_batch],
    }
    if rules:
        payload["evaluate_rules"] = rules

    return (
        f"Evaluate all applicable RF_SCYTHE v0.1 rules for the following graph snapshot.\n"
        f"Return a JSON array of rule results.\n\n"
        f"INPUT:\n{_compact_json(payload)}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────────────────────

# Reentrancy guard — prevents validator–normalizer self-recursion.
# If validation is already in progress on this thread, any reentrant call
# returns an empty list immediately.
import threading
_validation_lock = threading.local()


def require_structured_gemma_output(results: Any) -> List[Dict]:
    """Accept only dict-or-list[dict] model output.

    tak-ml used to tolerate string items inside otherwise structured output and
    let the validator skip or decode them later. That still lets malformed model
    output reach auto-materialization and shadow re-evaluation paths first.
    Treating non-dict items as a hard failure keeps downstream state changes
    behind a strict structured-output boundary.
    """
    if isinstance(results, dict):
        normalized = [results]
    elif isinstance(results, list):
        normalized = results
    else:
        _runtime_metrics.record_structured_output_hard_failure(type(results).__name__)
        logger.warning(
            "[tak-ml] Structured output rejected: expected list/dict, got %s",
            type(results).__name__,
        )
        return []

    for idx, item in enumerate(normalized):
        if not isinstance(item, dict):
            _runtime_metrics.record_structured_output_hard_failure(type(item).__name__)
            logger.warning(
                "[tak-ml] Structured output rejected: item %d is %s; dict-only output is required",
                idx,
                type(item).__name__,
            )
            return []
    return normalized


@recursion_sentinel
def validate_gemma_output(results: Any, known_node_ids: Optional[set] = None) -> List[Dict]:
    """
    Validate and sanitize Gemma output.

    - Ensures it's a list of dicts
    - Checks required fields
    - Filters hallucinated edge kinds (terminal drop — no materialization)
    - Optionally validates node IDs exist
    - Reentrancy-safe: blocks recursive entry
    """
    # ── Reentrancy guard ──
    if getattr(_validation_lock, 'in_progress', False):
        logger.error("[tak-ml] BLOCKED reentrant validation call")
        return []
    _validation_lock.in_progress = True
    try:
        structured_results = require_structured_gemma_output(results)
        if not structured_results:
            return []
        _runtime_metrics.record_validation_items(len(structured_results))
        return _validate_gemma_output_inner(structured_results, known_node_ids)
    finally:
        _validation_lock.in_progress = False


def _validate_gemma_output_inner(results: Any, known_node_ids: Optional[set] = None) -> List[Dict]:
    """Inner validation logic (called under reentrancy guard)."""
    valid = []
    for idx, r in enumerate(results):
        if not isinstance(r, dict):
            logger.warning(
                "[tak-ml] Validator rejected output: item %d is %s; dict-only output is required",
                idx,
                type(r).__name__,
            )
            return []
        if "rule_id" not in r or "should_fire" not in r:
            # Coerce: if the item has inferred_edges, synthesize required fields
            if r.get("inferred_edges"):
                r.setdefault("rule_id", "R-RECOVERED")
                r.setdefault("should_fire", True)
                logger.debug("[tak-ml] Validator coerced missing rule_id/should_fire for item with edges")
            else:
                _runtime_metrics.record_validation_skip("missing_rule_id_or_should_fire")
                logger.warning("[tak-ml] Validator skipped item: missing rule_id or should_fire")
                continue

        # Sanitize confidence
        try:
            r["confidence"] = max(0.0, min(1.0, float(r.get("confidence", 0.5))))
        except (ValueError, TypeError):
            logger.warning(f"[tak-ml] Validator fixed invalid confidence for {r.get('rule_id')}")
            r["confidence"] = 0.5

        # Filter unknown inferred edge kinds
        inferred = r.get("inferred_edges", [])
        if isinstance(inferred, list):
            clean_edges = []
            for ie in inferred:
                if not isinstance(ie, dict):
                    continue
                raw_kind = ie.get("kind", "")
                already = ie.get("_normalized", False)

                # ── Edge Kind Normalization (idempotent) ──
                # If _normalized is already stamped we skip the alias
                # lookup entirely — prevents re-normalization recursion.
                canonical = normalize_edge_kind(
                    raw_kind, _already_normalized=already,
                )
                if canonical is None:
                    # Truly unknown kind — attempt semantic repair via embeddinggemma
                    # before dropping.  This handles novel hallucination variants that
                    # don't yet have a static alias entry.
                    repaired: Optional[str] = None
                    repair_score: float = 0.0
                    try:
                        from semantic_edge_repair import SemanticEdgeRepair
                        repaired, repair_score = SemanticEdgeRepair.get_instance().repair(raw_kind)
                    except Exception as _rep_err:
                        logger.debug("[tak-ml] Semantic repair unavailable: %s", _rep_err)
                    if repaired:
                        _runtime_metrics.record_semantic_repair()
                        logger.info(
                            "[tak-ml] Semantic repair: '%s' → '%s' (score=%.3f)",
                            raw_kind, repaired, repair_score,
                        )
                        ie["kind"] = repaired
                        ie["_normalized"] = True
                        canonical = repaired
                    else:
                        _runtime_metrics.record_rejected_kind(raw_kind)
                        logger.warning(
                            "[tak-ml] Validator dropped edge: invalid kind '%s' "
                            "(not in VALID_INFERRED_KINDS, EDGE_KIND_ALIASES, or "
                            "semantic repair — score=%.3f)",
                            raw_kind, repair_score,
                        )
                        continue
                if canonical == "":
                    # Explicit schema-policy drop (observed-zone kind or
                    # known hallucination pattern).  Log at DEBUG — this is
                    # expected LLM output and does not indicate a bug.
                    logger.debug(
                        "[tak-ml] Validator dropped edge: kind '%s' "
                        "rejected by schema policy (observed-zone or hallucination)",
                        raw_kind,
                    )
                    continue
                if canonical != raw_kind:
                    logger.info(
                        "[tak-ml] Validator normalized edge kind: '%s' → '%s'",
                        raw_kind, canonical,
                    )
                ie["kind"] = canonical
                ie["_normalized"] = True   # stamp: never re-normalize

                # ── Authority Zone Check ──
                # Edges produced by inference MUST belong to the INFERRED
                # zone.  Observed/implied kinds emitted by the model are
                # authority mismatches → terminal drop.
                edge_zone = EDGE_AUTHORITY.get(canonical)
                if edge_zone and edge_zone != EDGE_ZONE_INFERRED:
                    logger.warning(
                        "[tak-ml] Validator dropped edge: kind '%s' belongs "
                        "to zone %s (only INFERRED edges may be emitted by model)",
                        canonical, edge_zone,
                    )
                    continue

                if not ie.get("src") or not ie.get("dst"):
                    logger.warning("[tak-ml] Validator dropped edge: missing src/dst")
                    continue

                # ── Preflight Node Registry ──
                # Check that src/dst reference known nodes.
                # First, attempt to strip hallucinated edge-kind prefixes
                # (e.g. model emits "session_observed_SESSION-abc123" instead
                # of "SESSION-abc123").  This is a common Gemma drift pattern.
                if known_node_ids:
                    src_id = _strip_edgekind_prefix(ie["src"])
                    dst_id = _strip_edgekind_prefix(ie["dst"])
                    if src_id != ie["src"] or dst_id != ie["dst"]:
                        logger.debug(
                            "[tak-ml] Validator stripped edge-kind prefix from node IDs: "
                            "src '%s'→'%s'  dst '%s'→'%s'",
                            ie["src"], src_id, ie["dst"], dst_id,
                        )
                        ie["src"] = src_id
                        ie["dst"] = dst_id
                    if src_id not in known_node_ids:
                        # Classify what kind of node is missing
                        src_kind = _classify_missing_node(src_id)
                        logger.warning(
                            "[tak-ml] Validator dropped edge: unknown src "
                            "'%s' (probable kind: %s)",
                            src_id, src_kind,
                        )
                        ie["_raw_kind"] = raw_kind
                        _shadow_push(ie, "unknown_src", context_node_id=r.get("context_node_id", ""), rule_id=r.get("rule_id", ""))
                        continue
                    if dst_id not in known_node_ids:
                        dst_kind = _classify_missing_node(dst_id)
                        logger.warning(
                            "[tak-ml] Validator dropped edge: unknown dst "
                            "'%s' (probable kind: %s)",
                            dst_id, dst_kind,
                        )
                        ie["_raw_kind"] = raw_kind
                        _shadow_push(ie, "unknown_dst", context_node_id=r.get("context_node_id", ""), rule_id=r.get("rule_id", ""))
                        continue
                clean_edges.append(ie)
            r["inferred_edges"] = clean_edges

        if r.get("should_fire") and not r.get("inferred_edges"):
            logger.info(
                "[tak-ml] rule %s fired but yielded 0 valid edges "
                "after validation", r.get('rule_id'),
            )

        valid.append(r)

    return valid


# ─────────────────────────────────────────────────────────────────────────────
# Shadow graph helper — routes rejected edges to pre-reality buffer
# ─────────────────────────────────────────────────────────────────────────────

TERMINAL_SHADOW_REJECTION_REASONS = frozenset({
    "invalid_kind",
})


def _shadow_push(edge: dict, reason: str, *, context_node_id: str = "", rule_id: str = "") -> None:
    """Non-blocking best-effort push to the shadow graph."""
    if reason in TERMINAL_SHADOW_REJECTION_REASONS:
        logger.debug(
            "[tak-ml] Shadow push skipped for terminal rejection: kind=%s reason=%s",
            edge.get("_raw_kind") or edge.get("kind", ""),
            reason,
        )
        return
    try:
        from shadow_graph import ShadowGraph
        ShadowGraph.get_instance().push(
            edge, reason,
            context_node_id=context_node_id,
            rule_id=rule_id,
        )
    except Exception:
        pass  # shadow graph is optional infrastructure — never block validation

# Edge-kind prefixes the model sometimes prepends to node IDs (hallucination).
# Pattern: model writes "session_observed_SESSION-xxx" instead of "SESSION-xxx".
_EDGEKIND_PREFIXES = (
    "session_observed_",
    "flow_observed_",
    "host_observed_",
    "port_observed_",
    "inferred_",
)


def _strip_edgekind_prefix(node_id: str) -> str:
    """Strip hallucinated edge-kind prefixes from a node ID.

    When Gemma confuses edge kinds with node ID namespaces it emits:
        "session_observed_SESSION-f02fbc651cefe1d0"
    The real node ID is "SESSION-f02fbc651cefe1d0".

    This function is case-insensitive on the prefix but preserves the
    original casing of the remaining ID to match the registry.
    """
    if not node_id:
        return node_id
    lower = node_id.lower()
    for prefix in _EDGEKIND_PREFIXES:
        if lower.startswith(prefix):
            return node_id[len(prefix):]
    return node_id


def _classify_missing_node(node_id: str) -> str:
    """Guess the probable kind of a missing node from its ID pattern.

    This is purely diagnostic — helps operators understand *what* to
    materialize, not whether to allow it.
    """
    import re
    nid = node_id.lower()
    if nid.startswith("host_") or re.match(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', nid):
        return "host"
    if nid.startswith("flow_"):
        return "flow"
    if nid.startswith("session_"):
        return "pcap_session"
    if nid.startswith("dns_") or nid.startswith("dnsname_"):
        return "dns_name"
    if nid.startswith("sni_") or nid.startswith("tls_"):
        return "tls_sni"
    if nid.startswith("http_host_"):
        return "http_host"
    if nid.startswith("asn_") or nid.startswith("as"):
        return "asn"
    if nid.startswith("org_"):
        return "org"
    if nid.startswith("geo_"):
        return "geo_point"
    if nid.startswith("service_") or nid.startswith("port_"):
        return "service"
    return "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# Auto-Materializer — Create stub nodes when edges reference missing endpoints
# ─────────────────────────────────────────────────────────────────────────────

# Node kinds that may be auto-materialized when referenced by a valid edge
AUTO_MATERIALIZABLE_KINDS = frozenset({
    "host", "flow", "pcap_session",
})

# ── Materialization Policy Table (v1.0) ─────────────────────────────────────
#
# Encodes which (edge_kind, missing_node_kind) pairs allow auto-materialization.
# An edge kind must be OBSERVED (sensor-grounded) to trigger materialization.
# Inferred edge kinds NEVER create new nodes — they only connect existing ones.
#
# Format: frozenset of (edge_kind, node_kind) tuples that ARE allowed.
# Anything not listed is a terminal drop.
#
MATERIALIZATION_POLICY: frozenset = frozenset({
    # Observed sensor edges → create the missing endpoint
    ("FLOW_TLS_SNI",            "flow"),
    ("FLOW_HTTP_HOST",          "flow"),
    ("FLOW_QUERIED_DNS",        "flow"),
    ("FLOW_DNS_ANSWER",         "flow"),
    ("FLOW_DST_PORT",           "flow"),
    ("SESSION_OBSERVED_HOST",   "host"),
    ("SESSION_OBSERVED_FLOW",   "flow"),
    ("SESSION_OBSERVED_FLOW",   "pcap_session"),
    ("HOST_OBSERVED_FLOW",      "flow"),
})

# Edge kinds that are allowed to trigger any materialization at all.
# Derived from the policy table — only observed (sensor-grounded) kinds.
MATERIALIZATION_ELIGIBLE_EDGE_KINDS: frozenset = frozenset(
    ek for ek, _ in MATERIALIZATION_POLICY
)

# Global rules (enforced in auto_materialize_missing_nodes):
#  1. Only materialize when edge kind is canonical (observed)
#  2. Never materialize from inferred edges
#  3. Synthetic nodes never spawn other nodes
#  4. Invalid edge kinds are terminal drops — no classification attempted
#  5. org, geo, sni, http_host, dns_name, service are declaration-only


# ── Materialization Depth Governor ──────────────────────────────────────────
MAX_MATERIALIZATION_DEPTH: int = 1  # conservative: stubs never spawn stubs


@recursion_sentinel
def auto_materialize_missing_nodes(
    results: List[Dict],
    known_node_ids: set,
    engine: Any,
    *,
    source_depth: int = 0,
) -> set:
    """Pre-create stub nodes for edge endpoints that don't exist yet.

    Only materializes when ALL conditions are met:
        1. source_depth < MAX_MATERIALIZATION_DEPTH
        2. The edge kind is in MATERIALIZATION_ELIGIBLE_EDGE_KINDS (observed)
        3. The (edge_kind, node_kind) pair is in MATERIALIZATION_POLICY
        4. The missing node kind is in AUTO_MATERIALIZABLE_KINDS

    Invalid / inferred edge kinds are terminal drops — no classification
    or materialization is even attempted.  This prevents validator–
    normalizer self-recursion.

    Every created stub is tagged:
        _synthetic = True
        _materialization_depth = source_depth + 1
        _materialized_by = "edge_gated_creation"

    Returns the set of newly created node IDs.
    """
    import time

    # ── Depth guard: stop infinite expansion ──
    if source_depth >= MAX_MATERIALIZATION_DEPTH:
        logger.debug(
            "[tak-ml] Auto-materialize skipped: source_depth=%d >= MAX=%d",
            source_depth, MAX_MATERIALIZATION_DEPTH,
        )
        return set()

    if not isinstance(results, list):
        return set()

    # Collect missing node IDs — ONLY from edges with eligible kinds
    missing: Dict[str, str] = {}  # node_id → probable_kind
    for r in results:
        if not isinstance(r, dict):
            continue
        for ie in r.get("inferred_edges", []):
            if not isinstance(ie, dict):
                continue

            # ── GATE 1: edge kind must be canonical observed ──
            edge_kind = (ie.get("kind") or "").strip().upper()
            if edge_kind not in MATERIALIZATION_ELIGIBLE_EDGE_KINDS:
                # Terminal drop — do NOT classify, do NOT materialize
                continue

            for endpoint in ("src", "dst"):
                nid = ie.get(endpoint, "")
                if nid and nid not in known_node_ids and nid not in missing:
                    kind = _classify_missing_node(nid)

                    # ── GATE 2: (edge_kind, node_kind) must be in policy ──
                    if kind not in AUTO_MATERIALIZABLE_KINDS:
                        continue
                    if (edge_kind, kind) not in MATERIALIZATION_POLICY:
                        logger.debug(
                            "[tak-ml] Materialization blocked by policy: "
                            "edge=%s, node=%s(%s)",
                            edge_kind, nid, kind,
                        )
                        continue

                    missing[nid] = kind

    if not missing:
        return set()

    # Sort by materialization order
    kind_order = {
        "host": 0, "flow": 1, "pcap_session": 2,
    }
    sorted_missing = sorted(missing.items(), key=lambda x: kind_order.get(x[1], 99))

    created = set()
    child_depth = source_depth + 1
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for nid, kind in sorted_missing:
        evt = {
            "event_type": "NODE_CREATE",
            "entity_id": nid,
            "entity_data": {
                "id": nid,
                "kind": kind,
                "labels": {},
                "metadata": {
                    "_synthetic": True,
                    "_materialization_depth": child_depth,
                    "_materialized_by": "edge_gated_creation",
                    "auto_materialized": True,
                    "materialized_at": ts,
                    "obs_class": "implied",
                },
            },
        }
        try:
            engine.apply_graph_event(evt)
            known_node_ids.add(nid)
            created.add(nid)
        except Exception as e:
            logger.warning(
                "[tak-ml] Auto-materialize failed for %s (%s): %s",
                nid, kind, e,
            )

    if created:
        logger.info(
            "[tak-ml] Auto-materialized %d stub nodes (depth=%d): %s",
            len(created), child_depth,
            ", ".join(f"{nid}({missing[nid]})" for nid in list(created)[:5]),
        )

    return created
