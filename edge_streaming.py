"""
Edge Streaming via WebSocket

Manages per-client subscriptions to graph edges with scoped delivery.
Edges are selection-aware: cluster, node, time window, minimum weight.

Design:
  - Single persistent WS per browser (not per-PCAP)
  - Pull-based: browser requests edges for a specific region/cluster/node
  - Server maintains bounded subscription registry
  - Background tick loop (~1s) streams applicable edges to each client
  - Leverages HypergraphEngine decay logic for effective_weight computation

Protocol (JSON over WebSocket):

  Client → Server:
    { "op": "subscribe", "scope": { "type": "cluster", "id": 7, "min_weight": 0.15, "since_secs": 300 } }
    { "op": "subscribe", "scope": { "type": "node", "id": "entity:abc123", "depth": 1 } }
    { "op": "unsubscribe", "scope_id": "..." }

  Server → Client:
    { "op": "edges", "scope_id": "...", "edges": [ { "src": 123, "dst": 456, "weight": 0.44 } ] }
    { "op": "edge_update", "scope_id": "...", "src": 123, "dst": 456, "weight": 0.39, "action": "update|delete" }
    { "op": "error", "message": "..." }
"""

from collections import defaultdict, deque
import time
import json
import asyncio
import logging
import uuid
import math
from typing import Dict, Any, Optional, List, Callable

logger = logging.getLogger(__name__)

# cap the number of edges sent per subscription to avoid firehosing clients
MAX_EDGES_PER_SCOPE = 5000

# ring-buffer capacity for edge replay (approx. 1 hour at 30 edges/s)
EDGE_HISTORY_MAX = 100_000


class EdgeScope:
    """Represents a subscription scope (what edges to send to a client)."""

    def __init__(self, scope_id: str, scope_dict: Dict[str, Any]):
        self.scope_id = scope_id
        self.scope_type = scope_dict.get("type", "cluster")  # cluster, node, etc.
        self.cluster_id = scope_dict.get("id")
        self.node_id = scope_dict.get("id")
        self.depth = scope_dict.get("depth", 1)
        self.min_weight = scope_dict.get("min_weight", 0.01)
        self.since_secs = scope_dict.get("since_secs", 300)
        # optional scrub timestamp; if set, filtering/weight calculation
        # uses this time instead of "now" so the client can evaluate the
        # graph at an earlier point without mutating server state.
        self.scrub_time: Optional[float] = None
        self.created_at = time.time()
        self._last_sent = {}  # edge_id -> weight (to detect changes)

    def matches_edge(self, edge, engine, now: float) -> Optional[float]:
        """Return effective_weight if edge matches this scope, else None.

        Scope matching:
          - cluster: edge must touch cluster_id node
          - node: edge must touch node_id node (with depth constraint)
          - type: (future) could support other scopes

        Filtering:
          - effective_weight >= min_weight
          - edge.timestamp within [now - since_secs, now]
        """
        # choose evaluation time (scrub overrides real time)
        eval_time = self.scrub_time if self.scrub_time is not None else now
        # Age filter
        age = eval_time - (edge.timestamp or eval_time)
        if age > self.since_secs:
            return None

        # Weight filter: compute exponential decay using engine's lambda
        eff_w = edge.weight
        if hasattr(engine, 'decay_lambda') and engine.decay_lambda:
            eff_w = eff_w * math.exp(-engine.decay_lambda * age)
        if eff_w < self.min_weight:
            return None

        # Topology filter
        if self.scope_type == "cluster":
            # Edge must connect cluster_id node
            if self.cluster_id not in edge.nodes:
                return None
        elif self.scope_type == "node":
            # Edge must touch node_id (with depth constraint)
            if self.node_id not in edge.nodes:
                return None

        return eff_w

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scope_id": self.scope_id,
            "type": self.scope_type,
            "cluster_id": self.cluster_id,
            "node_id": self.node_id,
            "depth": self.depth,
            "min_weight": self.min_weight,
            "since_secs": self.since_secs,
        }


class EdgeStreamingManager:
    """Manages WebSocket subscriptions and edge streaming."""

    def __init__(self, engine_getter: Callable):
        """
        engine_getter: callable that returns the current HypergraphEngine.
                      Allows dynamic engine changes without re-init.
        """
        self.engine_getter = engine_getter
        self.subscriptions: Dict[str, Dict[str, EdgeScope]] = defaultdict(dict)
        # ws_id -> { scope_id: EdgeScope, ... }
        self.session_last_tick: Dict[str, float] = {}
        self.tick_interval_sec = 1.0
        # ring buffer: each entry is {id, src, dst, kind, weight, last_seen, emitted_at}
        self._history: deque = deque(maxlen=EDGE_HISTORY_MAX)

    def register_subscription(self, ws_id: str, scope_dict: Dict[str, Any]) -> str:
        """Register a new subscription for a WebSocket client.

        Returns scope_id for later reference/unsubscribe.
        """
        scope_id = f"scope-{uuid.uuid4().hex[:12]}"
        scope = EdgeScope(scope_id, scope_dict)
        self.subscriptions[ws_id][scope_id] = scope
        logger.info(f"[EdgeStream] WS {ws_id} subscribed: {scope.to_dict()}")
        return scope_id

    def scrub_subscription(self, ws_id: str, scope_id: str, timestamp: float) -> None:
        """Set a custom evaluation time for an existing subscription.
        When `scrub_time` is supplied the scope will filter and weight edges
        as if `now == scrub_time`.  This keeps history evaluation on the
        server side when desired; the client may also compute weights itself.
        """
        subs = self.subscriptions.get(ws_id)
        if not subs:
            return
        scope = subs.get(scope_id)
        if not scope:
            return
        scope.scrub_time = timestamp

    def unregister_subscription(self, ws_id: str, scope_id: str) -> bool:
        """Unregister a subscription. Return True if found and removed."""
        if scope_id in self.subscriptions.get(ws_id, {}):
            del self.subscriptions[ws_id][scope_id]
            logger.info(f"[EdgeStream] WS {ws_id} unsubscribed: {scope_id}")
            return True
        return False

    def on_disconnect(self, ws_id: str) -> None:
        """Clean up when a WebSocket client disconnects."""
        if ws_id in self.subscriptions:
            del self.subscriptions[ws_id]
        if ws_id in self.session_last_tick:
            del self.session_last_tick[ws_id]
        logger.info(f"[EdgeStream] WS {ws_id} disconnected, subscriptions cleared")

    def get_replay_since(self, since_ts: float, limit: int = 2000) -> List[Dict[str, Any]]:
        """Return edges emitted after since_ts (epoch seconds) from the ring buffer.

        Results are capped at `limit` to protect against flooding a freshly
        reconnected client.  The most-recent events are returned when the
        window is larger than the cap.
        """
        if since_ts <= 0:
            return []
        matching = [e for e in self._history if e.get('emitted_at', 0) > since_ts]
        if len(matching) > limit:
            # keep the newest `limit` entries
            matching = matching[-limit:]
        return matching

    def select_edges_for_scope(self, scope: 'EdgeScope', now: float) -> List[Dict[str, Any]]:
        """Return edges matching a scope, respecting decay and weight filters."""
        try:
            engine = self.engine_getter()
        except Exception as e:
            logger.warning(f"[EdgeStream] Engine unavailable: {e}")
            return []

        if not engine or not hasattr(engine, 'edges'):
            return []

        result = []
        for edge_id, edge in (engine.edges or {}).items():
            eff_w = scope.matches_edge(edge, engine, now)
            if eff_w is not None:
                result.append({
                    "id": edge_id,
                    "src": edge.nodes[0] if len(edge.nodes) > 0 else None,
                    "dst": edge.nodes[1] if len(edge.nodes) > 1 else None,
                    "kind": edge.kind,
                    # send raw "base" weight; client will apply decay/pulse
                    "weight": edge.weight,
                    "last_seen": edge.timestamp or now,
                    # reinforcement counter helps heartbeat intensity
                    "reinforcement_count": getattr(edge, 'metadata', {}).get('reinforcement_count', 1),
                    # optional first_seen for additional shading heuristics
                    "first_seen": getattr(edge, 'metadata', {}).get('first_seen', edge.timestamp or now),
                })

        return result

    async def stream_edges_tick(self, send_fn: Callable[[str, str], None]) -> None:
        """Periodic tick: stream edges to all active subscriptions.

        send_fn(ws_id, json_msg): callback to send a message to a WebSocket client.
        """
        now = time.time()
        _recorded_ids: set = set()

        for ws_id, scopes in list(self.subscriptions.items()):
            # Rate-limit per client (send at most once per tick_interval)
            last_tick = self.session_last_tick.get(ws_id, 0)
            if now - last_tick < self.tick_interval_sec:
                continue

            self.session_last_tick[ws_id] = now

            for scope_id, scope in list(scopes.items()):
                try:
                    edges = self.select_edges_for_scope(scope, now)
                    if edges:
                        # enforce cap
                        full_count = len(edges)
                        if full_count > MAX_EDGES_PER_SCOPE:
                            # downsample by base weight (highest first)
                            edges = sorted(edges, key=lambda e: e.get('weight',0), reverse=True)[:MAX_EDGES_PER_SCOPE]
                            warn = json.dumps({
                                "op": "warning",
                                "scope_id": scope_id,
                                "message": f"{full_count} edges exceeds cap {MAX_EDGES_PER_SCOPE}, truncated to top weights"
                            })
                            await send_fn(ws_id, warn)
                        msg = json.dumps({
                            "op": "edges",
                            "scope_id": scope_id,
                            "edges": edges,
                            "timestamp": now,
                        })
                        await send_fn(ws_id, msg)
                        # record to history (de-dup by edge id within this tick)
                        for e in edges:
                            eid = e.get('id')
                            if eid and eid not in _recorded_ids:
                                _recorded_ids.add(eid)
                                self._history.append({**e, 'emitted_at': now})
                except Exception as e:
                    logger.warning(f"[EdgeStream] Error streaming edges to {ws_id}: {e}")

    def stream_edges_tick_sync(self, send_fn: Callable[[str, str], None]) -> None:
        """Synchronous version of stream_edges_tick for eventlet/gevent background tasks.

        send_fn(ws_id, json_msg) must be a plain callable (not a coroutine).
        The async variant (stream_edges_tick) is preserved for asyncio contexts.
        """
        now = time.time()
        _recorded_ids: set = set()
        for ws_id, scopes in list(self.subscriptions.items()):
            last_tick = self.session_last_tick.get(ws_id, 0)
            if now - last_tick < self.tick_interval_sec:
                continue
            self.session_last_tick[ws_id] = now
            for scope_id, scope in list(scopes.items()):
                try:
                    edges = self.select_edges_for_scope(scope, now)
                    if edges:
                        full_count = len(edges)
                        if full_count > MAX_EDGES_PER_SCOPE:
                            edges = sorted(edges, key=lambda e: e.get('weight', 0), reverse=True)[:MAX_EDGES_PER_SCOPE]
                            send_fn(ws_id, json.dumps({
                                "op": "warning",
                                "scope_id": scope_id,
                                "message": f"{full_count} edges exceeds cap {MAX_EDGES_PER_SCOPE}, truncated to top weights",
                            }))
                        send_fn(ws_id, json.dumps({
                            "op": "edges",
                            "scope_id": scope_id,
                            "edges": edges,
                            "timestamp": now,
                        }))
                        # record to history (de-dup by edge id within this tick)
                        for e in edges:
                            eid = e.get('id')
                            if eid and eid not in _recorded_ids:
                                _recorded_ids.add(eid)
                                self._history.append({**e, 'emitted_at': now})
                except Exception as e:
                    logger.warning(f"[EdgeStream] Error streaming edges to {ws_id}: {e}")


# Global instance (initialized at server startup)
edge_streaming_manager: Optional[EdgeStreamingManager] = None


def initialize_edge_streaming(engine_getter: Callable) -> EdgeStreamingManager:
    """Initialize the edge streaming manager at server startup."""
    global edge_streaming_manager
    edge_streaming_manager = EdgeStreamingManager(engine_getter)
    logger.info("[EdgeStream] Manager initialized")
    return edge_streaming_manager


def get_edge_streaming_manager() -> Optional[EdgeStreamingManager]:
    """Retrieve the global edge streaming manager."""
    return edge_streaming_manager
