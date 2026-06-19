"""Subgraph diff generator for Clarktech UI.

Generates compact diffs between sequence ranges using the event stream
or best-effort scanning of the engine state.
"""
from typing import Dict, Any, List, Set
from datetime import datetime
import json

import graph_query_dsl


class QueryPredicate:
    def __init__(self, parsed: Dict[str, Any]):
        self.parsed = parsed

    def matches_node(self, node: Dict[str, Any]) -> bool:
        # use graph_query_dsl's matching for WHERE clauses
        where = self.parsed.get('where', [])
        try:
            for clause in where:
                if not graph_query_dsl._match_predicate(node, clause):
                    return False
        except Exception:
            return False

        # room scoping if present
        in_room = self.parsed.get('in_room')
        if in_room:
            rooms = node.get('rooms') or node.get('metadata', {}).get('rooms') or []
            try:
                if isinstance(rooms, (list, tuple, set)):
                    if in_room not in rooms:
                        return False
                else:
                    if str(rooms) != str(in_room):
                        return False
            except Exception:
                return False

        # bbox / within handling is left to server snapshot; here assume pass
        return True

    def matches_edge(self, edge: Dict[str, Any]) -> bool:
        # simple edge kind/labels matching
        where = self.parsed.get('where', [])
        if not where:
            return True
        # If WHERE includes kind = "x" and matches edge.kind
        for clause in where:
            m = None
            try:
                m = graph_query_dsl.re.match(r"kind\s*=\s*\"?([^\"]+)\"?", clause, graph_query_dsl.re.I)
            except Exception:
                m = None
            if m:
                want = m.group(1)
                if edge.get('kind') and str(edge.get('kind')) == want:
                    return True
                # else continue checking other clauses
        # default accept
        return True


class SubgraphDiffGenerator:
    def __init__(self, engine, operator_manager=None, redis_client=None):
        self.engine = engine
        self.operator_manager = operator_manager
        self.redis = redis_client

    def generate_diff(self, query_id: str, predicate: QueryPredicate, from_seq: int, to_seq: int) -> Dict[str, Any]:
        diff = {
            'query_id': query_id,
            'from_sequence': int(from_seq or 0),
            'to_sequence': int(to_seq or 0),
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'nodes': {'created': [], 'updated': [], 'deleted': []},
            'edges': {'created': [], 'updated': [], 'deleted': []}
        }

        events = []

        # Try Redis-based replay if available
        try:
            if self.redis:
                stream = 'entity_events_stream'
                # xranged entries
                entries = self.redis.xrange(stream, min='-', max='+', count=10000)
                for msg_id, fields in entries:
                    data = None
                    # redis-py may return dict of bytes or str
                    if isinstance(list(fields.keys())[0], bytes):
                        data = fields.get(b'data')
                    else:
                        data = fields.get('data')
                    if isinstance(data, bytes):
                        try:
                            data = data.decode()
                        except Exception:
                            continue
                    try:
                        obj = json.loads(data)
                    except Exception:
                        continue
                    seq = int(obj.get('sequence_id', 0) or 0)
                    if seq > from_seq and seq <= to_seq:
                        events.append(obj)
        except Exception:
            events = []

        # If no Redis, fall back to empty events (best-effort). Server may choose to scan engine.
        # If no Redis, try to use in-process GraphEventBus history if available
        try:
            if not events:
                # look for event bus on engine or global graph_event_bus
                eb = getattr(self.engine, 'event_bus', None)
                if eb is None:
                    try:
                        from rf_scythe_api_server import graph_event_bus as global_bus
                        eb = global_bus
                    except Exception:
                        eb = None

                if eb and hasattr(eb, 'replay'):
                    hist = eb.replay(from_seq)
                    for e in hist:
                        # convert SimpleNamespace or objects to dict-like
                        obj = None
                        try:
                            if hasattr(e, '__dict__'):
                                obj = dict(getattr(e, '__dict__'))
                            elif isinstance(e, dict):
                                obj = e
                            else:
                                # attempt to coerce
                                obj = dict(e)
                        except Exception:
                            # best-effort: try to introspect common attrs
                            obj = {}
                            try:
                                for k in ('event_type','entity_id','entity_kind','sequence_id','entity_data'):
                                    v = getattr(e, k, None)
                                    if v is not None:
                                        obj[k] = v
                            except Exception:
                                pass
                        seq = int(obj.get('sequence_id') or obj.get('sequence') or 0)
                        if seq > from_seq and seq <= to_seq:
                            events.append(obj)
        except Exception:
            pass

        touched_nodes: Set[str] = set()
        touched_edges: Set[str] = set()

        # Helpers to fetch current entity from engine
        def get_node_state(node_id: str):
            try:
                if hasattr(self.engine, 'nodes') and node_id in getattr(self.engine, 'nodes'):
                    n = getattr(self.engine, 'nodes')[node_id]
                    return n.to_dict() if hasattr(n, 'to_dict') else n
                # fallback to hypergraph_store nodes dict shape
                if hasattr(self.engine, 'get_visualization_data'):
                    viz = self.engine.get_visualization_data()
                    for n in viz.get('nodes', []):
                        if n.get('node_id') == node_id or n.get('id') == node_id:
                            return n
            except Exception:
                return None
            return None

        def get_edge_state(edge_id: str):
            try:
                if hasattr(self.engine, 'edges') and edge_id in getattr(self.engine, 'edges'):
                    e = getattr(self.engine, 'edges')[edge_id]
                    return e.to_dict() if hasattr(e, 'to_dict') else e
            except Exception:
                return None
            return None

        # Process events in sequence order (assume events list is chronological)
        for ev in events:
            et = ev.get('event_type') or ev.get('event') or ev.get('event_type')
            entity_type = ev.get('entity_type') or ev.get('entity_type')
            eid = ev.get('entity_id') or (ev.get('entity_data') or {}).get('id')
            if not eid:
                continue

            if entity_type and entity_type.lower() in ('edge', 'hyperedge'):
                if eid in touched_edges:
                    continue
                touched_edges.add(eid)
                if et in ('EDGE_DELETE','DELETE'):
                    diff['edges']['deleted'].append(eid)
                    continue
                edge = get_edge_state(eid)
                if edge is None:
                    diff['edges']['deleted'].append(eid)
                    continue
                if not predicate.matches_edge(edge):
                    diff['edges']['deleted'].append(eid)
                    continue
                if et in ('EDGE_CREATE','CREATE'):
                    diff['edges']['created'].append(edge)
                else:
                    diff['edges']['updated'].append(edge)

            else:
                # node-like
                if eid in touched_nodes:
                    continue
                touched_nodes.add(eid)
                if et in ('NODE_DELETE','DELETE'):
                    diff['nodes']['deleted'].append(eid)
                    continue
                node = get_node_state(eid)
                if node is None:
                    diff['nodes']['deleted'].append(eid)
                    continue
                if not predicate.matches_node(node):
                    diff['nodes']['deleted'].append(eid)
                    continue
                if et in ('NODE_CREATE','CREATE'):
                    diff['nodes']['created'].append(node)
                else:
                    diff['nodes']['updated'].append(node)

        return diff


__all__ = ['SubgraphDiffGenerator', 'QueryPredicate']
