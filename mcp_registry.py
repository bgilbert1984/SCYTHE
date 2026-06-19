"""mcp_registry.py — Production-grade MCP tool registry with schema validation, audit logging, rate limiting.

Features:
  - 23 tools organized by category (mutation, query, scope, diagnostics)
  - JSON schema validation for parameters and returns
  - Per-tool rate limiting
  - Audit logging (UUID + timestamp + summary)
  - Side-effect classification (for safety analysis)
  - Dynamic introspection (tools/schema endpoint)
"""
from typing import Any, Callable, Dict, Optional
import time
import logging
import uuid
import json

logger = logging.getLogger(__name__)


class Tool:
    """Production MCP tool definition with schema contracts and operational metadata."""

    def __init__(
        self,
        name: str,
        description: str,
        parameters: Dict[str, Any],
        returns: Dict[str, Any],
        run: Callable[..., Any],
        mutates_state: bool = False,
        rate_limit: Optional[float] = None,
        required_mode: str | None = None,
    ):
        self.name = name
        self.description = description
        self.parameters = parameters  # JSON Schema for input validation
        self.returns = returns  # JSON Schema for return validation
        self.run = run
        self.mutates_state = mutates_state
        self.rate_limit = rate_limit  # minimum seconds between calls
        # operational mode: observe | mutate | admin
        if required_mode is not None:
            self.required_mode = required_mode
        else:
            self.required_mode = "mutate" if mutates_state else "observe"

    def to_mcp(self):
        """Return MCP tool definition (for tools/list)."""
        return {"name": self.name, "description": self.description, "inputSchema": self.parameters}

    def to_schema(self):
        """Return full schema (for tools/schema endpoint)."""
        return {
            "parameters": self.parameters,
            "returns": self.returns,
            "mutates_state": self.mutates_state,
            "rate_limit": self.rate_limit,
            "required_mode": self.required_mode,
        }


class Registry:
    """Production registry with validation, audit, and rate limiting."""

    def __init__(self):
        self._tools: Dict[str, Tool] = {}
        self._rate: Dict[str, float] = {}  # {tool_name: last_called_time}
        # metrics counters
        self._metrics = {
            "invocations": {},
            "rate_limit_hits": {},
            "mutations": {},
            "errors": {},
        }

    def register(self, tool: Tool):
        self._tools[tool.name] = tool
        for key in self._metrics:
            self._metrics[key].setdefault(tool.name, 0)

    def list_tools(self):
        return list(self._tools.values())

    def get_schema(self):
        """Return schema for all tools (for introspection endpoint)."""
        return {name: tool.to_schema() for name, tool in self._tools.items()}

    def get_metrics(self):
        """Return current invocation and error metrics."""
        # return a deep copy to avoid mutation
        return {k: dict(v) for k, v in self._metrics.items()}

    def execute(
        self,
        engine,
        name: str,
        params: Dict[str, Any],
        agent_mode: str = "observe",
        mutation_budget: Optional[int] = None,
    ) -> Any:
        """Execute a tool with validation, rate limiting, audit logging, and mode checks."""
        if name not in self._tools:
            raise ValueError(f"Unknown tool: {name}")
        tool = self._tools[name]

        # permission check based on mode
        if agent_mode not in ("observe", "mutate", "admin"):
            raise ValueError(f"Invalid agent_mode: {agent_mode}")
        if tool.required_mode == "mutate" and agent_mode == "observe":
            raise RuntimeError(f"Tool {name} requires mutate mode")
        if tool.required_mode == "admin" and agent_mode != "admin":
            raise RuntimeError(f"Tool {name} requires admin mode")

        # mutation budget check
        if tool.mutates_state and mutation_budget is not None:
            if mutation_budget <= 0:
                raise RuntimeError("MUTATION_BUDGET_EXCEEDED")
            # decrement will be done by caller if tracked externally

        # 1. Validate parameters
        try:
            from jsonschema import validate
            validate(instance=params or {}, schema=tool.parameters)
        except ImportError:
            logger.warning("[mcp] jsonschema not installed; skipping parameter validation")
        except Exception as e:
            self._metrics["errors"][name] += 1
            raise ValueError(f"Invalid parameters for {name}: {e}")

        # 2. Check rate limit
        try:
            self._enforce_rate_limit(tool)
        except RuntimeError:
            self._metrics["rate_limit_hits"][name] += 1
            raise

        # 3. Execute
        try:
            result = tool.run(engine=engine, params=params or {})
        except Exception as e:
            self._metrics["errors"][name] += 1
            logger.exception("[mcp.registry] tool failed: %s", name)
            raise

        # record invocation
        self._metrics["invocations"][name] += 1
        if tool.mutates_state:
            self._metrics["mutations"][name] += 1

        # 4. Validate return type (optional, warn if fails)
        try:
            from jsonschema import validate
            validate(instance=result, schema=tool.returns)
        except ImportError:
            pass
        except Exception as e:
            logger.warning("[mcp] tool %s returned invalid schema: %s", name, e)

        # 5. Audit log
        self._audit_log(tool, params, result)

        return result

    def _enforce_rate_limit(self, tool: Tool):
        """Check and enforce per-tool rate limiting."""
        if tool.rate_limit is None:
            return
        now = time.time()
        last_call = self._rate.get(tool.name, 0)
        elapsed = now - last_call
        if elapsed < tool.rate_limit:
            raise RuntimeError(f"Rate limit for {tool.name}: min {tool.rate_limit}s, got {elapsed:.2f}s")
        self._rate[tool.name] = now

    def _enforce_rate_limit(self, tool: Tool):
        """Check and enforce per-tool rate limiting."""
        if tool.rate_limit is None:
            return
        now = time.time()
        last_call = self._rate.get(tool.name, 0)
        elapsed = now - last_call
        if elapsed < tool.rate_limit:
            raise RuntimeError(f"Rate limit for {tool.name}: min {tool.rate_limit}s, got {elapsed:.2f}s")
        self._rate[tool.name] = now

    def _audit_log(self, tool: Tool, params: Dict[str, Any], result: Any):
        """Log tool execution for audit trail."""
        record = {
            "uuid": str(uuid.uuid4()),
            "timestamp": time.time(),
            "tool": tool.name,
            "mutates_state": tool.mutates_state,
            "params": params or {},
            "result_summary": self._summarize(result),
        }
        logger.info(f"[MCP_AUDIT] {json.dumps(record, default=str)}")

    @staticmethod
    def _summarize(result: Any) -> str:
        """Return a safe summary of result (never full snapshot)."""
        if isinstance(result, dict):
            # Return only keys and types, not values (unless simple)
            summary = {}
            for k, v in result.items():
                if isinstance(v, (int, float, bool, str)):
                    summary[k] = v
                else:
                    summary[k] = f"<{type(v).__name__}>"
            return json.dumps(summary, default=str)
        elif isinstance(result, list):
            return f"<list: {len(result)} items>"
        else:
            return str(result)[:256]


# ============================================================================
# GRAPH MUTATION TOOLS (6)
# ============================================================================
# These tools modify engine state. All have rate limiting and mutates_state=True.

def _decay_now(engine, params):
    """Apply exponential decay to edges. Rate limit: 1.0s between calls."""
    lambda_ = params.get("lambda", 0.001)
    if hasattr(engine, "decay_edges"):
        pruned = engine.decay_edges(lambda_)
        remaining = len(getattr(engine, "edges", []))
        return {"edges_pruned": pruned, "edges_remaining": remaining}
    return {"edges_pruned": 0, "edges_remaining": 0}


def _ingest_pcap(engine, params):
    """Queue PCAP ingest. Returns status + job_id. Rate limit: 10s."""
    path = params.get("path", "")
    if not path:
        raise ValueError("path required")
    if hasattr(engine, "ingest_pcap"):
        job_id = str(uuid.uuid4())
        engine.ingest_pcap(path)  # Fire async
        return {"status": "started", "job_id": job_id}
    raise RuntimeError("Engine does not support ingest_pcap")


def _ingest_live_event(engine, params):
    """Commit a batch of events previously queued via WebSocket.

    `limit` controls how many to dequeue (default 10).  This tool is gated by
    the usual MCP safety checks (confidence/trust/stability/drift) and counts
    against the mutation budget.  The queue resides in live_ingest.py.
    """
    from live_ingest import dequeue

    limit = params.get("limit", 10)
    if not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be positive integer")

    if hasattr(engine, "apply_graph_event"):
        events = dequeue(limit)
        committed = 0
        for ev in events:
            try:
                engine.apply_graph_event(ev)
                committed += 1
            except Exception:
                # swallow errors to avoid aborting whole batch
                continue
        return {"committed": committed, "requested": limit}
    raise RuntimeError("Engine does not support apply_graph_event")


def _run_tak_ml(engine, params):
    """Run TAK/ML inference on a flow. Rate limit: 2s."""
    flow_id = params.get("flow_id", "")
    if not flow_id:
        raise ValueError("flow_id required")
    # Stub: assumes engine.run_ml_inference(flow_id) returns {ops_committed, edges_added}
    if hasattr(engine, "run_ml_inference"):
        result = engine.run_ml_inference(flow_id)
        return {"ops_committed": result.get("ops", 0), "edges_added": result.get("edges", 0)}
    return {"ops_committed": 0, "edges_added": 0}


def _reinforce_edge(engine, params):
    """Reinforce edge between two entities."""
    src = params.get("src", "")
    dst = params.get("dst", "")
    weight = params.get("weight", 1.0)
    if not (src and dst):
        raise ValueError("src and dst required")
    if hasattr(engine, "reinforce_edge"):
        engine.reinforce_edge(src, dst, weight)
        return {"ok": True, "src": src, "dst": dst, "weight": weight}
    return {"ok": False, "error": "reinforce_edge not supported"}


def _prune_below_weight(engine, params):
    """Remove edges below a weight threshold."""
    threshold = params.get("threshold", 0.1)
    if hasattr(engine, "prune_below_weight"):
        pruned = engine.prune_below_weight(threshold)
        return {"edges_pruned": pruned, "threshold": threshold}
    return {"edges_pruned": 0}


def _clear_scope_cache(engine, params):
    """Clear all WebSocket scope streaming caches."""
    if hasattr(engine, "clear_scope_cache"):
        engine.clear_scope_cache()
    return {"ok": True}


# ============================================================================
# GRAPH QUERY TOOLS (6)
# ============================================================================
# These tools are read-only. No rate limiting (light operations).

def _export_graph_snapshot(engine, params):
    """Export full graph with optional edge limit."""
    max_edges = params.get("max_edges", 1000)
    try:
        from mcp_context import MCPBuilder
        builder = MCPBuilder(engine)
        snapshot = builder.build()
        # Cap to max_edges
        if isinstance(snapshot, dict) and "edges" in snapshot:
            snapshot["edges"] = snapshot["edges"][:max_edges]
        return snapshot
    except Exception as e:
        logger.exception("Failed to build snapshot")
        raise RuntimeError(f"Failed to build snapshot: {e}")


def _query_hot_entities(engine, params):
    """Get top N entities by degree centrality."""
    limit = params.get("limit", 10)
    if not hasattr(engine, "degree"):
        return {"entities": []}
    degree_dict = engine.degree
    if not isinstance(degree_dict, dict):
        return {"entities": []}
    items = sorted(degree_dict.items(), key=lambda x: -x[1])[:limit]
    return {"entities": [{"id": k, "degree": v} for k, v in items]}


def _query_recent_edges(engine, params):
    """Get edges added/modified since timestamp, optionally filtered by weight."""
    since = params.get("since", 0)  # Unix timestamp
    min_weight = params.get("min_weight", 0)
    if hasattr(engine, "query_edges_since"):
        edges = engine.query_edges_since(since=since, min_weight=min_weight)
        return {"edges": edges, "count": len(edges)}
    return {"edges": [], "count": 0}


def _query_scope_stats(engine, params):
    """Get aggregated stats for a scope."""
    scope_id = params.get("scope_id", "")
    if not scope_id:
        raise ValueError("scope_id required")
    if hasattr(engine, "get_scope_stats"):
        stats = engine.get_scope_stats(scope_id)
        return stats
    return {"scope_id": scope_id, "node_count": 0, "edge_count": 0}


def _get_entity_neighbors(engine, params):
    """Get neighbors of an entity up to limit."""
    entity_id = params.get("entity_id", "")
    limit = params.get("limit", 20)
    if not entity_id:
        raise ValueError("entity_id required")
    if hasattr(engine, "get_neighbors"):
        neighbors = engine.get_neighbors(entity_id, limit=limit)
        return {"entity_id": entity_id, "neighbors": neighbors}
    return {"entity_id": entity_id, "neighbors": []}


def _get_edge_by_id(engine, params):
    """Fetch a single edge by ID."""
    edge_id = params.get("edge_id", "")
    if not edge_id:
        raise ValueError("edge_id required")
    if hasattr(engine, "get_edge"):
        edge = engine.get_edge(edge_id)
        return edge or {"ok": False, "error": "edge not found"}
    return {"ok": False, "error": "get_edge not supported"}


# ============================================================================
# SCOPE & STREAMING TOOLS (5)
# ============================================================================
# These integrate with WebSocket subscription model.

def _subscribe_scope(engine, params):
    """Subscribe to a scope (returns scope_id)."""
    kind = params.get("kind", "default")
    min_weight = params.get("min_weight", 0)
    scope_id = str(uuid.uuid4())
    if hasattr(engine, "create_scope"):
        engine.create_scope(scope_id, kind=kind, min_weight=min_weight)
    return {"scope_id": scope_id}


def _unsubscribe_scope(engine, params):
    """Unsubscribe from a scope."""
    scope_id = params.get("scope_id", "")
    if not scope_id:
        raise ValueError("scope_id required")
    if hasattr(engine, "destroy_scope"):
        engine.destroy_scope(scope_id)
    return {"ok": True, "scope_id": scope_id}


def _scrub_scope_time(engine, params):
    """Remove edges from scope before a timestamp."""
    scope_id = params.get("scope_id", "")
    timestamp = params.get("timestamp", 0)
    if not scope_id:
        raise ValueError("scope_id required")
    if hasattr(engine, "scrub_scope_before_time"):
        count = engine.scrub_scope_before_time(scope_id, timestamp)
        return {"ok": True, "scope_id": scope_id, "pruned": count}
    return {"ok": False}


def _set_scope_filter(engine, params):
    """Adjust min_weight filter on a scope."""
    scope_id = params.get("scope_id", "")
    min_weight = params.get("min_weight", 0)
    if not scope_id:
        raise ValueError("scope_id required")
    if hasattr(engine, "set_scope_filter"):
        engine.set_scope_filter(scope_id, min_weight=min_weight)
    return {"ok": True, "scope_id": scope_id, "min_weight": min_weight}


def _list_active_scopes(engine, params):
    """Get list of active scope IDs."""
    if hasattr(engine, "list_scopes"):
        scopes = engine.list_scopes()
        return {"scopes": scopes}
    return {"scopes": []}


# ============================================================================
# SYSTEM & DIAGNOSTICS (6)
# ============================================================================
# Read-only introspection and config.

def _get_engine_metrics(engine, params):
    """Get graph metrics: node_count, edge_count, avg_degree, decay_lambda."""
    nodes = getattr(engine, "nodes", {})
    edges = getattr(engine, "edges", [])
    degree = getattr(engine, "degree", {})
    avg_deg = sum(degree.values()) / len(degree) if degree else 0
    decay_lambda = getattr(engine, "decay_lambda", 0.001)
    return {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "avg_degree": avg_deg,
        "decay_lambda": decay_lambda,
    }


def _get_decay_config(engine, params):
    """Get current decay configuration."""
    return {
        "lambda": getattr(engine, "decay_lambda", 0.001),
        "last_decay": getattr(engine, "last_decay_time", 0),
    }


def _set_decay_lambda(engine, params):
    """Update decay rate (rate limit: 1s)."""
    new_lambda = params.get("lambda", 0.001)
    if hasattr(engine, "set_decay_lambda"):
        engine.set_decay_lambda(new_lambda)
    return {"ok": True, "new_lambda": new_lambda}


def _get_tak_ml_status(engine, params):
    """Get ML inference status: model_loaded, last_inference_time, error_count."""
    return {
        "model_loaded": hasattr(engine, "ml_model") and engine.ml_model is not None,
        "last_inference_time": getattr(engine, "last_ml_inference", 0),
        "error_count": getattr(engine, "ml_error_count", 0),
    }


def _get_socket_metrics(engine, params):
    """Get WebSocket metrics: active_connections, active_scopes, avg_emit_rate."""
    return {
        "active_connections": getattr(engine, "active_ws_connections", 0),
        "active_scopes": getattr(engine, "active_scope_count", 0),
        "avg_emit_rate": getattr(engine, "socket_emit_rate", 0),
    }


def _reload_rules(engine, params):
    """Reload rule_prompt definitions (rate limit: 5s)."""
    if hasattr(engine, "reload_rules"):
        engine.reload_rules()
    return {"ok": True}


# ============================================================================
# REGISTRY FACTORY
# ============================================================================

def build_registry(engine) -> Dict[str, Any]:
    """Build production registry with 23 tools.

    Returns {tool_name → Tool, '__registry__' → Registry}
    """
    reg = Registry()

    # MUTATION TOOLS
    reg.register(Tool(
        name="decay_now",
        description="Apply exponential decay to graph edges",
        parameters={
            "type": "object",
            "properties": {
                "lambda": {"type": "number", "minimum": 0, "default": 0.001}
            },
        },
        returns={
            "type": "object",
            "properties": {
                "edges_pruned": {"type": "integer"},
                "edges_remaining": {"type": "integer"},
            },
            "required": ["edges_pruned", "edges_remaining"],
        },
        run=_decay_now,
        mutates_state=True,
        rate_limit=1.0,
    ))

    reg.register(Tool(
        name="ingest_pcap",
        description="Queue a PCAP ingest job",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"}
            },
            "required": ["path"],
        },
        returns={
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "job_id": {"type": "string"},
            },
        },
        run=_ingest_pcap,
        mutates_state=True,
        rate_limit=10.0,
    ))

    # live ingestion tool takes events already sitting in queue
    reg.register(Tool(
        name="ingest_live_event",
        description="Commit a batch of previously queued live events",
        parameters={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "default": 10}
            },
        },
        returns={
            "type": "object",
            "properties": {
                "committed": {"type": "integer"},
                "requested": {"type": "integer"},
            },
        },
        run=_ingest_live_event,
        mutates_state=True,
        rate_limit=1.0,  # at most once per second
    ))

    reg.register(Tool(
        name="run_tak_ml",
        description="Run TAK/ML inference on a flow",
        parameters={
            "type": "object",
            "properties": {
                "flow_id": {"type": "string"}
            },
            "required": ["flow_id"],
        },
        returns={
            "type": "object",
            "properties": {
                "ops_committed": {"type": "integer"},
                "edges_added": {"type": "integer"},
            },
        },
        run=_run_tak_ml,
        mutates_state=True,
        rate_limit=2.0,
    ))

    reg.register(Tool(
        name="reinforce_edge",
        description="Increase weight on an edge",
        parameters={
            "type": "object",
            "properties": {
                "src": {"type": "string"},
                "dst": {"type": "string"},
                "weight": {"type": "number"},
            },
            "required": ["src", "dst"],
        },
        returns={
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "src": {"type": "string"},
                "dst": {"type": "string"},
                "weight": {"type": "number"},
            },
        },
        run=_reinforce_edge,
        mutates_state=True,
        rate_limit=None,
    ))

    reg.register(Tool(
        name="prune_below_weight",
        description="Remove edges below a weight threshold",
        parameters={
            "type": "object",
            "properties": {
                "threshold": {"type": "number", "minimum": 0}
            },
        },
        returns={
            "type": "object",
            "properties": {
                "edges_pruned": {"type": "integer"},
                "threshold": {"type": "number"},
            },
        },
        run=_prune_below_weight,
        mutates_state=True,
        rate_limit=None,
    ))

    reg.register(Tool(
        name="clear_scope_cache",
        description="Clear streaming scope caches",
        parameters={"type": "object", "properties": {}},
        returns={"type": "object", "properties": {"ok": {"type": "boolean"}}},
        run=_clear_scope_cache,
        mutates_state=True,
        rate_limit=None,
    ))

    # QUERY TOOLS (no rate limit)
    reg.register(Tool(
        name="export_graph_snapshot",
        description="Export current graph snapshot (MCP envelope)",
        parameters={
            "type": "object",
            "properties": {
                "max_edges": {"type": "integer", "default": 1000}
            },
        },
        returns={
            "type": "object",
            "properties": {
                "nodes": {"type": "array"},
                "edges": {"type": "array"},
            },
        },
        run=_export_graph_snapshot,
        mutates_state=False,
        rate_limit=None,
    ))

    reg.register(Tool(
        name="query_hot_entities",
        description="Get top N entities by degree",
        parameters={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 10}
            },
        },
        returns={
            "type": "object",
            "properties": {
                "entities": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "degree": {"type": "number"},
                        },
                    },
                },
            },
        },
        run=_query_hot_entities,
        mutates_state=False,
        rate_limit=None,
    ))

    reg.register(Tool(
        name="query_recent_edges",
        description="Get edges since timestamp",
        parameters={
            "type": "object",
            "properties": {
                "since": {"type": "number"},
                "min_weight": {"type": "number", "default": 0},
            },
        },
        returns={
            "type": "object",
            "properties": {
                "edges": {"type": "array"},
                "count": {"type": "integer"},
            },
        },
        run=_query_recent_edges,
        mutates_state=False,
        rate_limit=None,
    ))

    reg.register(Tool(
        name="query_scope_stats",
        description="Get aggregated stats for a scope",
        parameters={
            "type": "object",
            "properties": {
                "scope_id": {"type": "string"}
            },
            "required": ["scope_id"],
        },
        returns={
            "type": "object",
            "properties": {
                "scope_id": {"type": "string"},
                "node_count": {"type": "integer"},
                "edge_count": {"type": "integer"},
            },
        },
        run=_query_scope_stats,
        mutates_state=False,
        rate_limit=None,
    ))

    reg.register(Tool(
        name="get_entity_neighbors",
        description="Get neighbors of an entity",
        parameters={
            "type": "object",
            "properties": {
                "entity_id": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["entity_id"],
        },
        returns={
            "type": "object",
            "properties": {
                "entity_id": {"type": "string"},
                "neighbors": {"type": "array"},
            },
        },
        run=_get_entity_neighbors,
        mutates_state=False,
        rate_limit=None,
    ))

    reg.register(Tool(
        name="get_edge_by_id",
        description="Fetch an edge by ID",
        parameters={
            "type": "object",
            "properties": {
                "edge_id": {"type": "string"}
            },
            "required": ["edge_id"],
        },
        returns={
            "type": "object",
            "properties": {},
        },
        run=_get_edge_by_id,
        mutates_state=False,
        rate_limit=None,
    ))

    # SCOPE TOOLS
    reg.register(Tool(
        name="subscribe_scope",
        description="Create a scope subscription",
        parameters={
            "type": "object",
            "properties": {
                "kind": {"type": "string", "default": "default"},
                "min_weight": {"type": "number", "default": 0},
            },
        },
        returns={
            "type": "object",
            "properties": {
                "scope_id": {"type": "string"},
            },
        },
        run=_subscribe_scope,
        mutates_state=True,
        rate_limit=None,
    ))

    reg.register(Tool(
        name="unsubscribe_scope",
        description="Destroy a scope subscription",
        parameters={
            "type": "object",
            "properties": {
                "scope_id": {"type": "string"}
            },
            "required": ["scope_id"],
        },
        returns={
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "scope_id": {"type": "string"},
            },
        },
        run=_unsubscribe_scope,
        mutates_state=True,
        rate_limit=None,
    ))

    reg.register(Tool(
        name="scrub_scope_time",
        description="Remove edges from scope before timestamp",
        parameters={
            "type": "object",
            "properties": {
                "scope_id": {"type": "string"},
                "timestamp": {"type": "number"},
            },
            "required": ["scope_id"],
        },
        returns={
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "scope_id": {"type": "string"},
                "pruned": {"type": "integer"},
            },
        },
        run=_scrub_scope_time,
        mutates_state=True,
        rate_limit=None,
    ))

    reg.register(Tool(
        name="set_scope_filter",
        description="Adjust min_weight filter on a scope",
        parameters={
            "type": "object",
            "properties": {
                "scope_id": {"type": "string"},
                "min_weight": {"type": "number", "default": 0},
            },
            "required": ["scope_id"],
        },
        returns={
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "scope_id": {"type": "string"},
                "min_weight": {"type": "number"},
            },
        },
        run=_set_scope_filter,
        mutates_state=False,
        rate_limit=None,
    ))

    reg.register(Tool(
        name="list_active_scopes",
        description="List all active scope subscriptions",
        parameters={"type": "object", "properties": {}},
        returns={
            "type": "object",
            "properties": {
                "scopes": {"type": "array"},
            },
        },
        run=_list_active_scopes,
        mutates_state=False,
        rate_limit=None,
    ))

    # DIAGNOSTICS TOOLS
    reg.register(Tool(
        name="get_engine_metrics",
        description="Get graph metrics and stats",
        parameters={"type": "object", "properties": {}},
        returns={
            "type": "object",
            "properties": {
                "node_count": {"type": "integer"},
                "edge_count": {"type": "integer"},
                "avg_degree": {"type": "number"},
                "decay_lambda": {"type": "number"},
            },
        },
        run=_get_engine_metrics,
        mutates_state=False,
        rate_limit=None,
    ))

    reg.register(Tool(
        name="get_decay_config",
        description="Get current decay configuration",
        parameters={"type": "object", "properties": {}},
        returns={
            "type": "object",
            "properties": {
                "lambda": {"type": "number"},
                "last_decay": {"type": "number"},
            },
        },
        run=_get_decay_config,
        mutates_state=False,
        rate_limit=None,
    ))

    reg.register(Tool(
        name="set_decay_lambda",
        description="Update decay rate",
        parameters={
            "type": "object",
            "properties": {
                "lambda": {"type": "number", "minimum": 0}
            },
        },
        returns={
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "new_lambda": {"type": "number"},
            },
        },
        run=_set_decay_lambda,
        mutates_state=True,
        rate_limit=1.0,
    ))

    reg.register(Tool(
        name="get_tak_ml_status",
        description="Get TAK/ML inference status",
        parameters={"type": "object", "properties": {}},
        returns={
            "type": "object",
            "properties": {
                "model_loaded": {"type": "boolean"},
                "last_inference_time": {"type": "number"},
                "error_count": {"type": "integer"},
            },
        },
        run=_get_tak_ml_status,
        mutates_state=False,
        rate_limit=None,
    ))

    reg.register(Tool(
        name="get_socket_metrics",
        description="Get WebSocket and scope metrics",
        parameters={"type": "object", "properties": {}},
        returns={
            "type": "object",
            "properties": {
                "active_connections": {"type": "integer"},
                "active_scopes": {"type": "integer"},
                "avg_emit_rate": {"type": "number"},
            },
        },
        run=_get_socket_metrics,
        mutates_state=False,
        rate_limit=None,
    ))

    reg.register(Tool(
        name="reload_rules",
        description="Reload rule_prompt definitions",
        parameters={"type": "object", "properties": {}},
        returns={
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
            },
        },
        run=_reload_rules,
        mutates_state=True,
        rate_limit=5.0,
    ))

    # Compatibility: wrap tools as ToolDef-compatible objects for mcp_server
    tools_map = {}
    for name, tool in reg._tools.items():
        # Create a closure-friendly tool wrapper
        def make_tool_wrapper(t):
            class ToolCompat:
                def __init__(self, tool):
                    self.tool = tool
                    self.name = tool.name
                    self.description = tool.description
                    self.input_schema = tool.parameters

                def to_mcp(self):
                    return self.tool.to_mcp()

                def fn(self, **arguments):
                    return self.tool.run(engine=engine, params=arguments or {})
            return ToolCompat(t)
        tools_map[name] = make_tool_wrapper(tool)

    # Attach registry for programmatic execution
    tools_map["__registry__"] = reg
    return tools_map
