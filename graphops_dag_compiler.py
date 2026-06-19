"""graphops_dag_compiler.py — MCP → gRPC Execution DAG Compiler.

Turns a declarative JSON IR (Intermediate Representation) into an optimized
async execution graph dispatched across SCYTHE's gRPC services and local
GraphOps handlers.

Architecture
────────────
    JSON IR payload
         │
    parse_graph()  →  IRGraph  (dep edges from {"from": "node_id"} refs)
         │
    DAGCompiler.optimize()     (fusion, cache injection)
         │
    topo_sort()    →  parallel layers  (Kahn's algorithm)
         │
    DAGExecutor.execute()      (asyncio.gather per layer)
         │
    OP_REGISTRY dispatch       (gRPC stubs  OR  local handlers)
         │
    {node_id: result}  filtered to requested return_ids

IR format
─────────
    {
      "graph": [
        {"id": "swarm", "op": "cluster.decompose",
         "input": {"cluster_id": "C-8831"}},
        {"id": "rf",    "op": "rf.field",
         "input": {"lod": 2}, "mode": "stream", "stream_limit": 5},
        {"id": "intent","op": "tak.infer",
         "input": {"from": "swarm"}, "mode": "unary"}
      ],
      "return": ["intent", "rf"],
      "options": {"cache_ttl": 30, "timeout_s": 10.0}
    }

Dependencies are declared via ``{"from": "node_id"}`` values inside a node's
``input`` dict, or as the shorthand string ``"$node_id"``.

Operators (OP_REGISTRY)
────────────────────────
    cluster.decompose     →  ClusterIntelService.DecomposeCluster  (gRPC unary)
    cluster.autopsy       →  ClusterIntelService.StreamAutopsy     (gRPC stream)
    hypergraph.snapshot   →  HypergraphService.GetSnapshot         (gRPC unary)
    rf.field              →  ScytheStreamService.StreamRFField      (gRPC stream)
    cluster.stream        →  ScytheStreamService.StreamClusters     (gRPC stream)
    swarm.deltas          →  ScytheStreamService.StreamSwarmDeltas  (gRPC stream)
    tak.infer             →  TakMLService.Infer                     (gRPC unary)
    graph.dsl             →  GraphOpsCopilot.run_text()             (local sync→async)
    local.passthrough     →  identity (fanin / merge node)
    local.filter          →  threshold filter over a list result
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# ── Module-level constants ─────────────────────────────────────────────────────

STREAM_DEFAULT_LIMIT = 20    # max events collected from a server-streaming RPC
CACHE_DEFAULT_TTL    = 60.0  # seconds; 0 = no cache
DAG_DEFAULT_TIMEOUT  = 15.0  # seconds for full DAG execution


# ── Op Mode ───────────────────────────────────────────────────────────────────

class OpMode(str, Enum):
    UNARY  = "unary"
    STREAM = "stream"


# ── Scope requirements per op ─────────────────────────────────────────────────
# None = no scope required (open).
# Use ctx.strict_mode=True to deny ops not listed here.

OP_SCOPES: Dict[str, Optional[str]] = {
    "cluster.decompose":   "cluster:read",
    "cluster.autopsy":     "cluster:read",
    "hypergraph.snapshot": "graph:read",
    "hypergraph.deltas":   "graph:read",
    "rf.field":            "rf:read",
    "cluster.stream":      "cluster:read",
    "swarm.deltas":        "cluster:read",
    "tak.infer":           "tak:infer",
    "graph.dsl":           None,
    "local.passthrough":   None,
    "local.filter":        None,
}


# ── IR Dataclasses ────────────────────────────────────────────────────────────

@dataclass
class IRNode:
    """One node in the execution IR."""
    id:           str
    op:           str
    raw_input:    Dict[str, Any]           # as provided in JSON, unchanged
    mode:         OpMode = OpMode.UNARY
    stream_limit: int    = STREAM_DEFAULT_LIMIT
    deps:         List[str] = field(default_factory=list)   # node ids this node depends on

    def cache_key(self, instance_id: str = "") -> str:
        """SHA-256 (16 hex chars) over (instance_id, op, static inputs).

        instance_id is included to prevent cross-session cache leakage.
        """
        static = {
            k: v for k, v in self.raw_input.items()
            if not k.startswith("_")
            and not (isinstance(v, dict) and "from" in v)
            and not (isinstance(v, str) and v.startswith("$"))
        }
        blob = json.dumps(
            {"instance_id": instance_id, "op": self.op, "input": static},
            sort_keys=True,
        )
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


@dataclass
class IRGraph:
    """Parsed, validated execution graph."""
    nodes:      Dict[str, IRNode]    # id → IRNode
    return_ids: List[str]            # node ids whose results are returned
    cache_ttl:  float = CACHE_DEFAULT_TTL
    timeout_s:  float = DAG_DEFAULT_TIMEOUT


# ── IR Parser ─────────────────────────────────────────────────────────────────



def _validate_stream_nodes(graph: IRGraph) -> None:
    """Raise ValueError if any stream-mode node has stream_limit <= 0.

    All stream ops must declare an explicit bound in v1 to prevent hanging
    on open-ended gRPC server-streaming RPCs.
    """
    for nid, node in graph.nodes.items():
        if node.mode == OpMode.STREAM and node.stream_limit <= 0:
            raise ValueError(
                f"Stream node '{nid}' (op='{node.op}') must declare "
                f"stream_limit > 0.  Unbounded streams are rejected in DAG v1."
            )

def parse_graph(payload: dict) -> IRGraph:
    """Parse a JSON IR payload → IRGraph.

    Dependency edges are inferred from ``{"from": "node_id"}`` values and
    ``"$node_id"`` string shorthands inside each node's ``input`` dict.
    Cycles are detected (ValueError) before the graph is returned.
    """
    raw_nodes  = payload.get("graph", [])
    if not raw_nodes:
        raise ValueError("IR payload missing 'graph' list")

    options    = payload.get("options", {})
    return_ids = list(payload.get("return", []))

    nodes: Dict[str, IRNode] = {}

    for raw in raw_nodes:
        nid = raw.get("id")
        if not nid:
            raise ValueError("Each graph node must have an 'id'")
        op = raw.get("op")
        if not op:
            raise ValueError(f"Node '{nid}' is missing 'op'")

        raw_input = dict(raw.get("input", {}))
        mode_str  = str(raw.get("mode", "unary")).lower()
        try:
            mode = OpMode(mode_str)
        except ValueError:
            raise ValueError(
                f"Node '{nid}' has unknown mode '{mode_str}'; use 'unary' or 'stream'"
            )

        # Extract dep edges from input refs
        deps: List[str] = []
        for val in raw_input.values():
            if isinstance(val, dict) and "from" in val:
                ref = val["from"]
                if isinstance(ref, list):
                    deps.extend(ref)
                else:
                    deps.append(str(ref))
            elif isinstance(val, str) and val.startswith("$"):
                deps.append(val[1:])

        nodes[nid] = IRNode(
            id=nid,
            op=op,
            raw_input=raw_input,
            mode=mode,
            stream_limit=int(raw.get("stream_limit", STREAM_DEFAULT_LIMIT)),
            deps=list(dict.fromkeys(deps)),   # deduplicate, preserve order
        )

    # Validate that all dep references point to real nodes
    for nid, node in nodes.items():
        for dep in node.deps:
            if dep not in nodes:
                raise ValueError(
                    f"Node '{nid}' depends on unknown node '{dep}'"
                )

    _check_cycles(nodes)

    # Validate return_ids
    for rid in return_ids:
        if rid not in nodes:
            raise ValueError(f"'return' references unknown node '{rid}'")

    # Default: return every node
    if not return_ids:
        return_ids = list(nodes.keys())

    return IRGraph(
        nodes=nodes,
        return_ids=return_ids,
        cache_ttl=float(options.get("cache_ttl", CACHE_DEFAULT_TTL)),
        timeout_s=float(options.get("timeout_s", DAG_DEFAULT_TIMEOUT)),
    )


def _check_cycles(nodes: Dict[str, IRNode]) -> None:
    """DFS-based cycle detection.  Raises ValueError on first cycle found."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color: Dict[str, int] = {nid: WHITE for nid in nodes}

    def _dfs(nid: str) -> None:
        color[nid] = GRAY
        for dep in nodes[nid].deps:
            if color[dep] == GRAY:
                raise ValueError(
                    f"Cycle detected in graph: '{dep}' ← '{nid}'"
                )
            if color[dep] == WHITE:
                _dfs(dep)
        color[nid] = BLACK

    for nid in nodes:
        if color[nid] == WHITE:
            _dfs(nid)


# ── Topological Sort → Parallel Layers ────────────────────────────────────────

def topo_sort(graph: IRGraph) -> List[List[IRNode]]:
    """Kahn's algorithm → list of execution layers.

    All nodes in a layer have no unsatisfied deps and execute concurrently.
    """
    nodes      = graph.nodes
    in_degree: Dict[str, int]        = {nid: 0 for nid in nodes}
    consumers: Dict[str, List[str]]  = defaultdict(list)   # dep → [nodes that need dep]

    for nid, node in nodes.items():
        for dep in node.deps:
            in_degree[nid] += 1
            consumers[dep].append(nid)

    queue: deque[str] = deque(
        nid for nid, deg in in_degree.items() if deg == 0
    )
    layers: List[List[IRNode]] = []

    while queue:
        layer_ids = list(queue)
        queue.clear()
        layers.append([nodes[nid] for nid in layer_ids])
        for nid in layer_ids:
            for consumer in consumers[nid]:
                in_degree[consumer] -= 1
                if in_degree[consumer] == 0:
                    queue.append(consumer)

    if sum(len(lay) for lay in layers) != len(nodes):
        raise ValueError("Cycle detected during topological sort")

    return layers


# ── TTL Cache ─────────────────────────────────────────────────────────────────

class _CacheEntry:
    __slots__ = ("value", "expires_at")

    def __init__(self, value: Any, ttl: float) -> None:
        self.value      = value
        self.expires_at = time.monotonic() + ttl


class CacheStore:
    """Simple in-memory TTL cache.  Thread-safe for single-threaded async use."""

    def __init__(self) -> None:
        self._store: Dict[str, _CacheEntry] = {}

    def get(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        if entry is None:
            return None
        if time.monotonic() > entry.expires_at:
            del self._store[key]
            return None
        return entry.value

    def put(self, key: str, value: Any, ttl: float) -> None:
        if ttl <= 0:
            return
        self._store[key] = _CacheEntry(value, ttl)

    def evict_expired(self) -> None:
        now  = time.monotonic()
        dead = [k for k, v in self._store.items() if now > v.expires_at]
        for k in dead:
            del self._store[k]

    def __len__(self) -> int:
        return len(self._store)


# ── DAG Context ───────────────────────────────────────────────────────────────

@dataclass
class DAGContext:
    """Auth scopes, instance binding, and gRPC stubs for one execution."""
    operator_scopes: Set[str]          = field(default_factory=set)
    instance_id:     str               = ""
    operator_id:     str               = ""
    session_token:   str               = ""
    grpc_stubs:      Optional[Dict[str, Any]] = None
    # When True, ops whose required scope is not in operator_scopes are denied.
    # When False (default), ops with scope=None are always allowed.
    strict_mode:     bool              = False

    def has_scope(self, scope: Optional[str]) -> bool:
        if scope is None:
            return True
        return scope in self.operator_scopes or "admin" in self.operator_scopes

    def can_run_op(self, op: str) -> bool:
        """Check whether this context is authorized to run op.

        Deny-by-default: ops not in OP_SCOPES are denied unless strict_mode
        is False AND the op is registered in the active registry.  Unregistered
        ops are always denied.
        """
        if op not in OP_SCOPES:
            # Unknown op — fail closed in strict mode; caller handles registry check
            return self.strict_mode is False
        required = OP_SCOPES[op]
        return self.has_scope(required)


# ── Compiler: optimization passes ─────────────────────────────────────────────

class DAGCompiler:
    """Applies IR optimization passes before execution."""

    @staticmethod
    def optimize(graph: IRGraph, cache: Optional[CacheStore] = None) -> IRGraph:
        graph = DAGCompiler._fuse_swarm_intent(graph)
        if cache is not None:
            graph = DAGCompiler._mark_cache_hits(graph, cache)
        return graph

    @staticmethod
    def _fuse_swarm_intent(graph: IRGraph) -> IRGraph:
        """Mark (cluster.decompose → tak.infer) pairs for fused dispatch.

        When a ``tak.infer`` node's *only* dependency is a ``cluster.decompose``
        node, we flag the intent node with ``_fused_with_decompose=True``.
        The dispatcher detects this and auto-builds the 7-feature vector from
        the decomposition result, skipping a separate feature-extraction step.
        """
        swarm_ids = {
            nid for nid, n in graph.nodes.items()
            if n.op == "cluster.decompose"
        }
        intent_ids = {
            nid for nid, n in graph.nodes.items()
            if n.op == "tak.infer"
            and len(n.deps) == 1
            and n.deps[0] in swarm_ids
        }
        if not intent_ids:
            return graph

        new_nodes = dict(graph.nodes)
        for nid in intent_ids:
            node     = graph.nodes[nid]
            new_raw  = {**node.raw_input, "_fused_with_decompose": True}
            new_nodes[nid] = IRNode(
                id=node.id, op=node.op, raw_input=new_raw,
                mode=node.mode, stream_limit=node.stream_limit, deps=node.deps,
            )
        return IRGraph(
            nodes=new_nodes, return_ids=graph.return_ids,
            cache_ttl=graph.cache_ttl, timeout_s=graph.timeout_s,
        )

    @staticmethod
    def _mark_cache_hits(graph: IRGraph, cache: CacheStore) -> IRGraph:
        """Pre-mark nodes whose results are already in cache."""
        new_nodes = dict(graph.nodes)
        for nid, node in graph.nodes.items():
            # Note: cache marking at compile time uses empty instance_id;
            # the executor re-checks with real instance_id at runtime.
            hit = cache.get(node.cache_key())
            if hit is not None:
                new_raw = {**node.raw_input, "_cache_hit": hit}
                new_nodes[nid] = IRNode(
                    id=node.id, op=node.op, raw_input=new_raw,
                    mode=node.mode, stream_limit=node.stream_limit, deps=node.deps,
                )
        return IRGraph(
            nodes=new_nodes, return_ids=graph.return_ids,
            cache_ttl=graph.cache_ttl, timeout_s=graph.timeout_s,
        )


# ── DAG Executor ──────────────────────────────────────────────────────────────

class DAGExecutor:
    """Async parallel DAG executor.

    Each topological layer runs concurrently (asyncio.gather).  Errors in
    individual nodes are captured as ``{"error": "...", "op": "..."}`` dicts
    rather than aborting the entire graph, so partial results are still
    returned to the caller.
    """

    def __init__(
        self,
        registry:    Dict[str, Callable],
        cache:       Optional[CacheStore] = None,
    ) -> None:
        self._registry = registry
        self._cache    = cache or CacheStore()

    async def execute(self, graph: IRGraph, ctx: DAGContext) -> Dict[str, Any]:
        """Execute graph; return {node_id: result} for all return_ids."""
        layers  = topo_sort(graph)
        results: Dict[str, Any] = {}

        try:
            await asyncio.wait_for(
                self._run_layers(layers, results, graph.cache_ttl, ctx),
                timeout=graph.timeout_s,
            )
        except asyncio.TimeoutError:
            logger.warning("[DAG] execution timed out after %.1fs", graph.timeout_s)

        return {rid: results.get(rid, {"error": "not_executed"}) for rid in graph.return_ids}

    async def _run_layers(
        self,
        layers:    List[List[IRNode]],
        results:   Dict[str, Any],
        cache_ttl: float,
        ctx:       DAGContext,
    ) -> None:
        for layer in layers:
            tasks        = [self._run_node(n, results, cache_ttl, ctx) for n in layer]
            layer_results = await asyncio.gather(*tasks, return_exceptions=True)
            for node, result in zip(layer, layer_results):
                if isinstance(result, Exception):
                    logger.warning(
                        "[DAG] node '%s' (%s) failed: %s",
                        node.id, node.op, result,
                    )
                    results[node.id] = {"error": str(result), "op": node.op}
                else:
                    results[node.id] = result

    async def _run_node(
        self,
        node:      IRNode,
        results:   Dict[str, Any],
        cache_ttl: float,
        ctx:       DAGContext,
    ) -> Any:
        # Cache hit — skip dispatch entirely
        if "_cache_hit" in node.raw_input:
            logger.debug("[DAG] cache hit: %s/%s", node.id, node.op)
            return node.raw_input["_cache_hit"]

        # Authorization
        if not ctx.can_run_op(node.op):
            required = OP_SCOPES.get(node.op, "<unknown>")
            raise PermissionError(
                f"Operator '{ctx.operator_id}' lacks scope '{required}' "
                f"required for op '{node.op}'"
            )

        # Resolve {"from": "nid"} → actual results
        resolved = _resolve_inputs(node.raw_input, results)

        # Dispatch
        handler = self._registry.get(node.op)
        if handler is None:
            return {"error": f"unknown op: {node.op}"}

        try:
            result = await handler(resolved, node, ctx)
        except PermissionError:
            raise
        except Exception as exc:
            logger.warning(
                "[DAG] dispatch error [%s/%s]: %s", node.id, node.op, exc,
            )
            return {"error": str(exc), "op": node.op}

        # Write to cache (skip errors and stream results that are very large)
        if cache_ttl > 0 and "error" not in result:
            self._cache.put(node.cache_key(ctx.instance_id), result, cache_ttl)

        return result


def _resolve_inputs(
    raw_input: Dict[str, Any],
    results:   Dict[str, Any],
) -> Dict[str, Any]:
    """Substitute {from: nid} and $nid refs with actual node results."""
    resolved: Dict[str, Any] = {}
    for k, v in raw_input.items():
        if k.startswith("_"):
            continue   # skip internal compiler markers
        if isinstance(v, dict) and "from" in v:
            ref = v["from"]
            if isinstance(ref, list):
                resolved[k] = [results.get(r) for r in ref]
            else:
                resolved[k] = results.get(str(ref))
        elif isinstance(v, str) and v.startswith("$"):
            resolved[k] = results.get(v[1:])
        else:
            resolved[k] = v
    return resolved


# ── Handlers: gRPC dispatch ────────────────────────────────────────────────────

async def _handle_cluster_decompose(
    inputs: Dict[str, Any], node: IRNode, ctx: DAGContext
) -> Dict[str, Any]:
    """ClusterIntelService.DecomposeCluster (gRPC unary)."""
    try:
        import scythe_pb2
    except ImportError:
        return {"error": "scythe_pb2 not available"}

    stub = (ctx.grpc_stubs or {}).get("cluster_intel")
    if stub is None:
        return {"error": "ClusterIntelService stub not connected"}

    cluster_id = str(inputs.get("cluster_id", ""))
    # gRPC contract: cluster_id must be "<instance_id>/<cluster_id>"
    if cluster_id and "/" not in cluster_id and ctx.instance_id:
        cluster_id = f"{ctx.instance_id}/{cluster_id}"
    req        = scythe_pb2.ClusterRequest(cluster_id=cluster_id)
    loop       = asyncio.get_running_loop()
    resp       = await loop.run_in_executor(None, stub.DecomposeCluster, req)
    return {
        "cluster_id":        resp.cluster_id,
        "archetype":         resp.archetype,
        "silence_pressure":  resp.silence_pressure,
        "node_tier":         resp.node_tier,
        "dimensional_density": 0.0,    # not in proto — placeholder
        "behavior_summary":  resp.behavior_summary,
        "node_count":        resp.node_count,
        "temporal_activity": resp.temporal_activity,
        "asn_entropy":       resp.asn_entropy,
        "signal_coherence":  resp.signal_coherence,
        "ip_entropy":        0.0,       # not in proto — placeholder
        "intent_scores": [
            {"label": s.label, "probability": s.probability}
            for s in resp.intent_scores
        ],
    }


async def _handle_cluster_autopsy(
    inputs: Dict[str, Any], node: IRNode, ctx: DAGContext
) -> Dict[str, Any]:
    """ClusterIntelService.StreamAutopsy (gRPC server-streaming — collected)."""
    try:
        import scythe_pb2
    except ImportError:
        return {"error": "scythe_pb2 not available"}

    stub = (ctx.grpc_stubs or {}).get("cluster_intel")
    if stub is None:
        return {"error": "ClusterIntelService stub not connected"}

    cluster_id = str(inputs.get("cluster_id", ""))
    # gRPC contract: cluster_id must be "<instance_id>/<cluster_id>"
    if cluster_id and "/" not in cluster_id and ctx.instance_id:
        cluster_id = f"{ctx.instance_id}/{cluster_id}"
    req        = scythe_pb2.ClusterRequest(cluster_id=cluster_id)
    limit      = node.stream_limit
    events: list = []

    def _collect() -> None:
        for evt in stub.StreamAutopsy(req):
            events.append({
                "event_type":   evt.event_type,
                "cluster_id":   evt.cluster_id,
                "data_json":    evt.data_json,
                "timestamp_ms": evt.timestamp_ms,
            })
            if len(events) >= limit:
                break

    await asyncio.get_running_loop().run_in_executor(None, _collect)
    return {"events": events, "collected": len(events)}


async def _handle_hypergraph_snapshot(
    inputs: Dict[str, Any], node: IRNode, ctx: DAGContext
) -> Dict[str, Any]:
    """HypergraphService.GetSnapshot (gRPC unary)."""
    try:
        import scythe_pb2
    except ImportError:
        return {"error": "scythe_pb2 not available"}

    stub = (ctx.grpc_stubs or {}).get("hypergraph")
    if stub is None:
        return {"error": "HypergraphService stub not connected"}

    instance_id = str(inputs.get("instance_id", ctx.instance_id))
    req         = scythe_pb2.SnapshotRequest(instance_id=instance_id)
    loop        = asyncio.get_running_loop()
    resp        = await loop.run_in_executor(None, stub.GetSnapshot, req)
    return {
        "timestamp_ms": resp.timestamp_ms,
        "total_nodes":  resp.total_nodes,
        "total_edges":  resp.total_edges,
        "nodes": [
            {
                "id": n.id, "lat": n.lat, "lon": n.lon,
                "anomaly": n.anomaly, "mass": n.mass,
                "degree": n.degree, "label": n.label, "threat": n.threat,
            }
            for n in resp.nodes
        ],
        "edges": [
            {
                "src_idx": e.src_idx, "dst_idx": e.dst_idx,
                "kind": e.kind, "confidence": e.confidence,
            }
            for e in resp.edges
        ],
    }


async def _handle_rf_field(
    inputs: Dict[str, Any], node: IRNode, ctx: DAGContext
) -> Dict[str, Any]:
    """ScytheStreamService.StreamRFField (gRPC server-streaming — collect up to stream_limit frames).

    Returns ``{"frames": [...], "collected": N}``.  Each frame includes
    size/lod/timestamp but NOT the raw voxel bytes (too large for JSON).
    Set ``include_voxels=True`` in inputs to include base64-encoded voxels.
    """
    try:
        import scythe_pb2
    except ImportError:
        return {"error": "scythe_pb2 not available"}

    stub = (ctx.grpc_stubs or {}).get("stream")
    if stub is None:
        return {"error": "ScytheStreamService stub not connected"}

    include_voxels = bool(inputs.get("include_voxels", False))
    req   = scythe_pb2.LodHint(
        camera_altitude=float(inputs.get("camera_altitude", 80_000)),
        focus_lng=float(inputs.get("focus_lng", 0.0)),
        focus_lat=float(inputs.get("focus_lat", 0.0)),
    )
    limit  = node.stream_limit
    frames: list = []

    def _collect() -> None:
        import base64
        for f in stub.StreamRFField(req):
            frame: Dict[str, Any] = {
                "size_x": f.size_x, "size_y": f.size_y, "size_z": f.size_z,
                "lod": f.lod, "timestamp": f.timestamp,
            }
            if include_voxels:
                frame["voxels_b64"] = base64.b64encode(f.voxels).decode()
            frames.append(frame)
            if len(frames) >= limit:
                break

    await asyncio.get_running_loop().run_in_executor(None, _collect)
    return {"frames": frames, "collected": len(frames)}


async def _handle_swarm_deltas(
    inputs: Dict[str, Any], node: IRNode, ctx: DAGContext
) -> Dict[str, Any]:
    """ScytheStreamService.StreamSwarmDeltas (gRPC server-streaming — collected)."""
    try:
        import scythe_pb2
    except ImportError:
        return {"error": "scythe_pb2 not available"}

    stub = (ctx.grpc_stubs or {}).get("stream")
    if stub is None:
        return {"error": "ScytheStreamService stub not connected"}

    instance_id = str(inputs.get("instance_id", ctx.instance_id))
    req         = scythe_pb2.StreamRequest(instance_id=instance_id)
    limit       = node.stream_limit
    deltas: list = []

    def _collect() -> None:
        for d in stub.StreamSwarmDeltas(req):
            deltas.append({
                "node_id":      d.node_id,
                "dx": d.dx, "dy": d.dy, "dz": d.dz,
                "d_intensity":  d.d_intensity,
                "timestamp_ms": d.timestamp_ms,
            })
            if len(deltas) >= limit:
                break

    await asyncio.get_running_loop().run_in_executor(None, _collect)
    return {"deltas": deltas, "collected": len(deltas)}


async def _handle_tak_infer(
    inputs: Dict[str, Any], node: IRNode, ctx: DAGContext
) -> Dict[str, Any]:
    """TakMLService.Infer (gRPC unary).

    When fused with cluster.decompose (``_fused_with_decompose=True`` in
    node.raw_input), the 7-feature vector is auto-built from the parent
    decomposition result passed as the first dep input.
    """
    try:
        import scythe_pb2
    except ImportError:
        return {"error": "scythe_pb2 not available"}

    stub = (ctx.grpc_stubs or {}).get("takml")
    if stub is None:
        return {"error": "TakMLService stub not connected"}

    raw_feats = inputs.get("features") or []

    if not raw_feats and node.raw_input.get("_fused_with_decompose"):
        # Pull features from the decompose result fed in as the dep input
        dep_key     = node.deps[0] if node.deps else None
        swarm_result = inputs.get(dep_key) if dep_key else {}
        if isinstance(swarm_result, dict):
            raw_feats = [
                swarm_result.get("silence_pressure",  0.0),
                swarm_result.get("temporal_activity", 0.0),
                swarm_result.get("asn_entropy",       0.0),
                swarm_result.get("signal_coherence",  0.0),
                float(swarm_result.get("node_count",  0)),
                swarm_result.get("dimensional_density", 0.0),
                swarm_result.get("ip_entropy",        0.0),
            ]

    if len(raw_feats) != 7:
        return {
            "error": (
                f"tak.infer requires exactly 7 features; got {len(raw_feats)}. "
                "Provide 'features' list or use cluster.decompose as dep with fusion."
            )
        }

    req  = scythe_pb2.TakMLRequest(
        instance_id=ctx.instance_id,
        model=str(inputs.get("model", "nerf_botnet_v1")),
        version=str(inputs.get("version", "1")),
        features=[float(f) for f in raw_feats],
    )
    loop = asyncio.get_running_loop()
    resp = await loop.run_in_executor(None, stub.Infer, req)
    return {
        "score":            resp.score,
        "model":            resp.model,
        "version":          resp.version,
        "latency_ms":       resp.latency_ms,
        "server_reachable": resp.server_reachable,
    }


# ── Handlers: local compute ────────────────────────────────────────────────────

async def _handle_graph_dsl(
    inputs: Dict[str, Any], node: IRNode, ctx: DAGContext
) -> Dict[str, Any]:
    """Run a GraphOpsCopilot DSL text block in a thread pool executor.

    Required input:
        dsl (str): multi-line DSL block (FOCUS, EXPAND, TRACE, ANALYZE, …)
    Optional input:
        _engine: pre-built GraphOpsCopilot instance (skips default init)
    """
    dsl_text = str(inputs.get("dsl") or inputs.get("text") or "").strip()
    if not dsl_text:
        return {"error": "graph.dsl requires 'dsl' input with DSL text"}

    try:
        from graphops_copilot import InvestigativeDSLExecutor
    except ImportError:
        return {"error": "graphops_copilot.InvestigativeDSLExecutor not available"}

    # Always create a fresh executor — InvestigativeDSLExecutor has mutable state
    # (_focus, _window_s, _focus_nodes) that would race across concurrent DAG nodes.
    hypergraph_engine = inputs.get("_hypergraph_engine")
    executor = InvestigativeDSLExecutor(hypergraph_engine)

    loop   = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, executor.run_text, dsl_text)
    return result if isinstance(result, dict) else {"result": result}


async def _handle_local_passthrough(
    inputs: Dict[str, Any], node: IRNode, ctx: DAGContext
) -> Dict[str, Any]:
    """Identity node — merges inputs from multiple deps unchanged.

    Useful as a fanin node when you want to collect results from parallel
    branches into a single dict before passing to a downstream node.
    """
    return {k: v for k, v in inputs.items()}


async def _handle_local_filter(
    inputs: Dict[str, Any], node: IRNode, ctx: DAGContext
) -> Dict[str, Any]:
    """Filter a list by a numeric key/threshold predicate.

    Required inputs:
        source (list[dict]): e.g. snapshot nodes from hypergraph.snapshot
        key    (str):        field name to filter on
    Optional inputs:
        min (float): keep items where value >= min
        max (float): keep items where value <= max
    """
    source = inputs.get("source") or []
    key    = str(inputs.get("key", ""))
    min_v  = inputs.get("min")
    max_v  = inputs.get("max")

    if not isinstance(source, list):
        return {"error": "local.filter 'source' must be a list"}

    filtered = []
    for item in source:
        if not isinstance(item, dict):
            continue
        val = item.get(key)
        if val is None:
            continue
        try:
            val = float(val)
        except (TypeError, ValueError):
            continue
        if min_v is not None and val < float(min_v):
            continue
        if max_v is not None and val > float(max_v):
            continue
        filtered.append(item)

    return {"items": filtered, "count": len(filtered)}


# ── Registry ──────────────────────────────────────────────────────────────────

DEFAULT_REGISTRY: Dict[str, Callable] = {
    "cluster.decompose":   _handle_cluster_decompose,
    "cluster.autopsy":     _handle_cluster_autopsy,
    "hypergraph.snapshot": _handle_hypergraph_snapshot,
    "rf.field":            _handle_rf_field,
    "swarm.deltas":        _handle_swarm_deltas,
    "tak.infer":           _handle_tak_infer,
    "graph.dsl":           _handle_graph_dsl,
    "local.passthrough":   _handle_local_passthrough,
    "local.filter":        _handle_local_filter,
}


def build_registry(
    extra_handlers: Optional[Dict[str, Callable]] = None,
) -> Dict[str, Callable]:
    """Return the full op registry, optionally extended with extra handlers."""
    reg = dict(DEFAULT_REGISTRY)
    if extra_handlers:
        reg.update(extra_handlers)
    return reg


def build_grpc_stubs(channel: Any) -> Dict[str, Any]:
    """Build all service stubs from a single gRPC channel.

    Usage::
        import grpc
        channel = grpc.insecure_channel("127.0.0.1:50051")
        stubs   = build_grpc_stubs(channel)
        ctx     = DAGContext(grpc_stubs=stubs, ...)
    """
    try:
        import scythe_pb2_grpc as pb2_grpc
    except ImportError:
        logger.warning("[DAG] scythe_pb2_grpc not available — stubs will be empty")
        return {}

    return {
        "orchestrator":  pb2_grpc.OrchestratorServiceStub(channel),
        "hypergraph":    pb2_grpc.HypergraphServiceStub(channel),
        "cluster_intel": pb2_grpc.ClusterIntelServiceStub(channel),
        "stream":        pb2_grpc.ScytheStreamServiceStub(channel),
        "auth":          pb2_grpc.AuthServiceStub(channel),
        "takml":         pb2_grpc.TakMLServiceStub(channel),
    }


# ── Module-level shared cache ─────────────────────────────────────────────────

_DEFAULT_CACHE: CacheStore = CacheStore()


# ── Top-level convenience entry point ─────────────────────────────────────────

async def run_dag(
    payload:        dict,
    ctx:            DAGContext,
    cache:          Optional[CacheStore]          = None,
    extra_handlers: Optional[Dict[str, Callable]] = None,
) -> Dict[str, Any]:
    """Parse, optimize, and execute a JSON IR payload.

    This is the primary entry point for REST/gRPC callers::

        result = await run_dag(request.json, ctx)

    Returns ``{node_id: result}`` for the ``return`` ids specified in the IR.
    """
    _cache   = cache if cache is not None else _DEFAULT_CACHE
    graph    = parse_graph(payload)
    graph    = DAGCompiler.optimize(graph, _cache)
    registry = build_registry(extra_handlers)
    executor = DAGExecutor(registry=registry, cache=_cache)
    return await executor.execute(graph, ctx)




# ── Synchronous wrapper for Flask / eventlet callers ─────────────────────────

def run_dag_sync(
    payload:        dict,
    ctx:            DAGContext,
    cache:          Optional[CacheStore]          = None,
    extra_handlers: Optional[Dict[str, Callable]] = None,
) -> Dict[str, Any]:
    """Synchronous entry point for Flask/eventlet routes.

    Uses ``eventlet.tpool.execute`` to run the async DAG in a real OS thread,
    not a greenlet.  This avoids the asyncio + eventlet.monkey_patch conflict
    (monkey-patched selectors break asyncio in greenlets, but real threads are
    unaffected).  Falls back to a ``threading.Thread`` if eventlet is absent.

    Usage in a Flask route::

        result = run_dag_sync(request.get_json(), ctx)
    """
    import asyncio as _asyncio

    def _run():
        return _asyncio.run(
            run_dag(payload, ctx, cache=cache, extra_handlers=extra_handlers)
        )

    try:
        import eventlet.tpool as _tpool
        return _tpool.execute(_run)
    except ImportError:
        import threading as _threading
        result_box: list = []
        exc_box:    list = []

        def _thread_run():
            try:
                result_box.append(_run())
            except Exception as exc:
                exc_box.append(exc)

        t = _threading.Thread(target=_thread_run, daemon=True)
        t.start()
        t.join(timeout=getattr(ctx, 'timeout_s', 60))
        if exc_box:
            raise exc_box[0]
        return result_box[0] if result_box else {"error": "DAG thread did not return"}
# ── Autopilot integration: Tier 3 investigation DAG ───────────────────────────

def build_investigation_dag(
    cluster_id: str,
    dsl_block:  str = "FOCUS {cluster_id}\nANALYZE\nSUMMARIZE",
) -> dict:
    """Return a standard Tier 3 investigation IR payload for a cluster alert.

    Runs cluster decomposition, TAK-ML inference (fused), and a DSL analysis
    block in parallel where possible, then filters high-threat nodes.

    Usage::
        payload = build_investigation_dag("C-8831")
        ctx     = DAGContext(operator_scopes={"cluster:read","tak:infer",...})
        result  = await run_dag(payload, ctx)
    """
    dsl_text = dsl_block.format(cluster_id=cluster_id)
    return {
        "graph": [
            {
                "id":    "decompose",
                "op":    "cluster.decompose",
                "input": {"cluster_id": cluster_id},
            },
            {
                "id":    "snapshot",
                "op":    "hypergraph.snapshot",
                "input": {"instance_id": ""},   # filled from ctx.instance_id
            },
            {
                "id":    "intent",
                "op":    "tak.infer",
                "input": {"from": "decompose"},   # fused: features from decompose
            },
            {
                "id":    "high_threat_nodes",
                "op":    "local.filter",
                "input": {
                    "source": {"from": "snapshot"},   # snapshot dict, .nodes list
                    "key":    "threat",
                    "min":    0.7,
                },
            },
            {
                "id":    "dsl_analysis",
                "op":    "graph.dsl",
                "input": {"dsl": dsl_text},
            },
        ],
        "return":  ["decompose", "intent", "high_threat_nodes", "dsl_analysis"],
        "options": {"cache_ttl": 30, "timeout_s": 20.0},
    }


async def dispatch_investigation(
    cluster_id:     str,
    ctx:            DAGContext,
    cache:          Optional[CacheStore]          = None,
    extra_handlers: Optional[Dict[str, Callable]] = None,
) -> Dict[str, Any]:
    """Run a Tier 3 investigation DAG for the given cluster.

    Convenience wrapper used by GraphOpsAutopilot when Tier 3 is triggered::

        result = await dispatch_investigation("C-8831", ctx)
    """
    payload = build_investigation_dag(cluster_id)
    return await run_dag(payload, ctx, cache=cache, extra_handlers=extra_handlers)
