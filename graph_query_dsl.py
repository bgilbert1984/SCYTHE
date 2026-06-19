"""Simple Clarktech Graph Query DSL parser and executor.

Provides a minimal, safe parser for the operator-facing DSL and an
executor that runs queries against the `HypergraphEngine` instance.

Supported features (minimal): FIND NODES/EDGES/SUBGRAPH, WHERE simple
predicates (equality, >, <, BETWEEN, CONTAINS), WITHIN <m> OF "id",
IN BBOX, IN ROOM, SINCE <minutes|timestamp>, RETURN nodes/edges/subgraph.

This is intentionally small and extensible.
"""
import re
import time
from typing import Dict, Any, List, Tuple, Set
from datetime import datetime, timedelta


def _parse_between(val: str) -> Tuple[float, float]:
    m = re.match(r"\s*(\d+(?:\.\d+)?)\s+AND\s+(\d+(?:\.\d+)?)\s*", val, re.I)
    if not m:
        raise ValueError("Invalid BETWEEN syntax")
    return float(m.group(1)), float(m.group(2))


def parse_dsl(text: str) -> Dict[str, Any]:
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    out = {'find': None, 'where': [], 'within': None, 'in_room': None, 'bbox': None, 'since': None, 'return': 'nodes'}
    for ln in lines:
        up = ln.upper()
        if up.startswith('FIND'):
            if 'NODES' in up:
                out['find'] = 'nodes'
            elif 'EDGES' in up:
                out['find'] = 'edges'
            elif 'SUBGRAPH' in up:
                out['find'] = 'subgraph'
            elif 'NEIGHBORS' in up:
                out['find'] = 'neighbors'
            continue
        if up.startswith('WHERE'):
            clause = ln[5:].strip()
            out['where'].append(clause)
            continue
        if up.startswith('WITHIN'):
            # e.g. WITHIN 500m OF "id"
            m = re.match(r"WITHIN\s+(\d+)(m|M)\s+OF\s+\"?([^\"\s]+)\"?", ln, re.I)
            if m:
                out['within'] = {'meters': int(m.group(1)), 'of': m.group(3)}
            continue
        if up.startswith('IN ROOM'):
            m = re.match(r'IN ROOM\s+\"?([^\"]+)\"?', ln, re.I)
            if m:
                out['in_room'] = m.group(1)
            continue
        if up.startswith('IN BBOX'):
            m = re.search(r'IN BBOX\s*\[(.*)\]', ln)
            if m:
                parts = [float(p.strip()) for p in m.group(1).split(',')]
                if len(parts) == 4:
                    out['bbox'] = parts  # lat1,lon1,lat2,lon2
            continue
        if up.startswith('SINCE'):
            rest = ln[5:].strip()
            m = re.match(r"(\d+)m$", rest)
            if m:
                minutes = int(m.group(1))
                out['since'] = time.time() - minutes * 60
            else:
                # try timestamp
                try:
                    out['since'] = datetime.fromisoformat(rest.replace('Z','')).timestamp()
                except Exception:
                    out['since'] = None
            continue
        if up.startswith('RETURN'):
            if 'SUBGRAPH' in up:
                out['return'] = 'subgraph'
            elif 'EDGES' in up:
                out['return'] = 'edges'
            else:
                out['return'] = 'nodes'
            continue
    return out


def _get_nested(d: dict, dotted_key: str):
    """Resolve dotted keys like 'labels.ip' or 'metadata.confidence'."""
    parts = dotted_key.split('.')
    cur = d
    for p in parts:
        if isinstance(cur, dict):
            cur = cur.get(p)
        else:
            return None
        if cur is None:
            return None
    return cur


def _match_predicate(node: Any, clause: str) -> bool:
    # support simple forms: key = "val", key > num, key < num, key BETWEEN a AND b, key CONTAINS "x"
    clause = clause.strip()
    m = re.match(r"([\w\.]+)\s*=\s*\"?([^\"]+)\"?", clause)
    if m:
        k, v = m.group(1), m.group(2)
        # support direct dict keys first (including dotted paths like labels.ip)
        if isinstance(node, dict):
            val = _get_nested(node, k) if '.' in k else node.get(k)
            if val is not None:
                return str(val) == v

            # special-case: allow `kind = "rf"` to match legacy node records
            if k.lower() == 'kind':
                # check 'type' field
                t = node.get('type') or node.get('kind')
                if t is not None and str(t) == v:
                    return True
                # check node_id/id prefix (legacy records use node_id like 'rf_node_...')
                nid = node.get('node_id') or node.get('id') or ''
                try:
                    if nid and str(nid).lower().startswith(str(v).lower()):
                        return True
                except Exception:
                    pass

            return False

        # if node not a dict (unlikely here), fall back to attribute access
        try:
            val = getattr(node, k, None)
            return str(val) == v
        except Exception:
            return False
    m = re.match(r"([\w\.]+)\s+BETWEEN\s+(.+)", clause, re.I)
    if m:
        k, rng = m.group(1), m.group(2)
        a, b = _parse_between(rng)
        val = node.get(k) if isinstance(node, dict) else None
        try:
            return float(val) >= a and float(val) <= b
        except Exception:
            return False
    m = re.match(r"([\w\.]+)\s*([><])\s*(\d+(?:\.\d+)?)", clause)
    if m:
        k, op, num = m.group(1), m.group(2), float(m.group(3))
        val = node.get(k) if isinstance(node, dict) else None
        try:
            v = float(val)
        except Exception:
            return False
        return v > num if op == '>' else v < num
    m = re.match(r"([\w\.]+)\s+CONTAINS\s+\"?([^\"]+)\"?", clause, re.I)
    if m:
        k, sub = m.group(1), m.group(2)
        val = node.get(k) if isinstance(node, dict) else None
        if val is None:
            # try labels
            labels = node.get('labels', {}) if isinstance(node, dict) else {}
            if isinstance(labels.get(k), (list, tuple, set)):
                return sub in labels.get(k)
            return sub in str(labels.get(k, ''))
        return sub in str(val)
    return False


def execute_query(engine, parsed: Dict[str, Any]) -> Dict[str, Any]:
    find = parsed.get('find')
    where = parsed.get('where', [])
    result_nodes: List[Dict[str, Any]] = []
    result_edges: List[Dict[str, Any]] = []

    # Helper: evaluate node by applying where clauses
    def node_matches(n: dict) -> bool:
        for c in where:
            if not _match_predicate(n, c):
                return False
        return True

    # FIND NODES
    if find == 'nodes' or find == 'subgraph' or find == 'neighbors':
        # special-case: if WHERE contains a simple kind = "X", support legacy node_id prefixes
        kind_clause = None
        for c in where:
            m = re.match(r"kind\s*=\s*\"?([^\"]+)\"?", c, re.I)
            if m:
                kind_clause = m.group(1)
                break

        # Build a candidate list from both engine.nodes (if present) and visualization data
        # Tag each candidate with its source so we can prefer engine-origin records.
        candidates = []  # list of (node_dict, source) where source in {'engine','viz'}
        try:
            if hasattr(engine, 'nodes') and engine.nodes:
                for node in getattr(engine, 'nodes').values():
                    candidates.append((node.to_dict() if hasattr(node, 'to_dict') else node, 'engine'))
        except Exception:
            pass
        try:
            viz = engine.get_visualization_data()
            for n in viz.get('nodes', []):
                candidates.append((n, 'viz'))
        except Exception:
            pass

        if candidates:
            for n, src in candidates:
                # direct match on kind/type
                if isinstance(n, dict) and ((n.get('kind') == kind_clause) or (n.get('type') == kind_clause)):
                    result_nodes.append(n)
                    continue
                # legacy node_id substring match
                nid_val = n.get('node_id') if isinstance(n, dict) else None
                if not nid_val:
                    nid_val = n.get('id') if isinstance(n, dict) else None
                try:
                    if nid_val and str(kind_clause).lower() in str(nid_val).lower():
                        result_nodes.append(n)
                except Exception:
                    continue
        else:
            # No candidates from engine/viz — iterate engine.nodes directly
            try:
                for nid, node in engine.nodes.items():
                    n = node.to_dict() if hasattr(node, 'to_dict') else node
                    if node_matches(n):
                        result_nodes.append(n)
            except Exception:
                pass

        # apply spatial within
        if parsed.get('within') and find == 'neighbors':
            info = parsed['within']
            meters = info['meters']
            of = info['of']
            # convert meters to degrees approx
            deg = meters / 111000.0
            neigh_ids = engine.neighbors_in_radius(of, radius_deg=deg)
            result_nodes = [engine.nodes[nid].to_dict() for nid in neigh_ids if nid in engine.nodes]

        # bbox
        if parsed.get('bbox'):
            lat1, lon1, lat2, lon2 = parsed['bbox']
            min_lat, max_lat = min(lat1, lat2), max(lat1, lat2)
            min_lon, max_lon = min(lon1, lon2), max(lon1, lon2)
            result_nodes = [n for n in result_nodes if 'position' in n and n.get('position') and min_lat <= n['position'][0] <= max_lat and min_lon <= n['position'][1] <= max_lon]

    # FIND EDGES
    if find == 'edges' or find == 'subgraph':
        for eid, edge in engine.edges.items():
            e = edge.to_dict() if hasattr(edge, 'to_dict') else edge
            ok = True
            for c in where:
                # simple match against edge labels and kind
                if 'kind = ' in c.lower():
                    m = re.match(r"kind\s*=\s*\"?([^\"]+)\"?", c, re.I)
                    if m and e.get('kind') != m.group(1):
                        ok = False
                        break
            if ok:
                result_edges.append(e)

    # For subgraph return, include edges touching matched nodes
    if parsed.get('return') == 'subgraph' or find == 'subgraph':
        node_ids = {n['id'] for n in result_nodes}
        # include edges touching these nodes
        se = []
        for eid, edge in engine.edges.items():
            ev = edge.to_dict() if hasattr(edge, 'to_dict') else edge
            if any(n in node_ids for n in ev.get('nodes', [])):
                se.append(ev)
        result_edges = se

    # Deduplicate nodes while preferring engine-origin records over viz records
    seen = {}  # nid -> (index_in_list, source)
    dedup_nodes = []
    for idx, item in enumerate(result_nodes):
        # item may be (node, source) if we preserved tags earlier; handle both shapes
        if isinstance(item, tuple) and len(item) == 2:
            n, src = item
        else:
            n, src = item, 'viz'

        # determine canonical id
        nid = None
        if isinstance(n, dict):
            nid = n.get('id') or n.get('node_id') or n.get('nodeId')
        if not nid:
            try:
                import json as _json
                nid = _json.dumps(n, sort_keys=True)
            except Exception:
                nid = str(n)

        if nid in seen:
            prev_idx, prev_src = seen[nid]
            # prefer engine over viz
            if prev_src == 'viz' and src == 'engine':
                dedup_nodes[prev_idx] = n
                seen[nid] = (prev_idx, 'engine')
            # otherwise keep first-seen
            continue

        seen[nid] = (len(dedup_nodes), src)
        dedup_nodes.append(n)

    # Deduplicate edges similarly (by id/node list signature)
    seen_e = set()
    dedup_edges = []
    for e in result_edges:
        eid = None
        if isinstance(e, dict):
            eid = e.get('id') or e.get('edge_id')
        if not eid:
            try:
                import json as _json
                eid = _json.dumps({'nodes': e.get('nodes', []), 'kind': e.get('kind')}, sort_keys=True)
            except Exception:
                eid = str(e)
        if eid in seen_e:
            continue
        seen_e.add(eid)
        dedup_edges.append(e)

    return {
        'nodes': dedup_nodes,
        'edges': dedup_edges,
        'count_nodes': len(dedup_nodes),
        'count_edges': len(dedup_edges)
    }


__all__ = ['parse_dsl', 'execute_query']
