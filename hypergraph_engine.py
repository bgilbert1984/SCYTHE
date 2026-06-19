
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Any, Set, List, Optional, Iterable, Tuple, Callable
import time
import threading
import math
import json
import os
import uuid
from types import SimpleNamespace
from contextlib import contextmanager


@dataclass
class PerfettoTraceEvent:
    """Perfetto-compatible trace event for temporal causality visualization."""
    trace_id: str
    span_id: str
    event_type: str  # e.g., NODE_CREATE, EDGE_UPDATE, CONFIDENCE_MUTATION
    entity_id: str = ""
    entity_kind: str = ""
    timestamp_ns: int = 0  # nanoseconds since epoch
    duration_ns: int = 0
    parent_span_id: Optional[str] = None

    # Causality tracking
    caused_by: List[str] = field(default_factory=list)  # entity IDs that caused this event

    # State mutations
    before_state: Dict[str, Any] = field(default_factory=dict)
    after_state: Dict[str, Any] = field(default_factory=dict)

    # Semantic specifics
    semantic_payload: Dict[str, Any] = field(default_factory=dict)

    # Optional spatiotemporal telemetry envelope.  Perfetto itself is not a GIS,
    # but SCYTHE can attach renderable spatial context to every trace span.
    geospatial: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to Perfetto JSON format."""
        payload = {
            "traceId": self.trace_id,
            "spanId": self.span_id,
            "parentSpanId": self.parent_span_id,
            "eventType": self.event_type,
            "entityId": self.entity_id,
            "entityKind": self.entity_kind,
            "timestampNs": self.timestamp_ns,
            "durationNs": self.duration_ns,
            "causedBy": self.caused_by,
            "beforeState": self.before_state,
            "afterState": self.after_state,
            "semanticPayload": self.semantic_payload,
        }
        if self.geospatial:
            geo = dict(self.geospatial)
            payload["geospatial"] = geo
            for key in ("lat", "lon", "alt"):
                if key in geo:
                    payload[key] = geo[key]
            if "uncertainty_radius" in geo:
                payload["uncertainty_radius"] = geo["uncertainty_radius"]
                payload["uncertaintyRadius"] = geo["uncertainty_radius"]
        return payload

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PerfettoTraceEvent":
        """Restore a trace event from exported/snapshot JSON."""
        semantic = data.get("semanticPayload", data.get("semantic_payload", {})) or {}
        geo = data.get("geospatial") or semantic.get("geospatial") or {}
        return cls(
            trace_id=data.get("traceId", data.get("trace_id", "")),
            span_id=data.get("spanId", data.get("span_id", "")),
            parent_span_id=data.get("parentSpanId", data.get("parent_span_id")),
            event_type=data.get("eventType", data.get("event_type", "")),
            entity_id=data.get("entityId", data.get("entity_id", "")),
            entity_kind=data.get("entityKind", data.get("entity_kind", "")),
            timestamp_ns=int(data.get("timestampNs", data.get("timestamp_ns", 0)) or 0),
            duration_ns=int(data.get("durationNs", data.get("duration_ns", 0)) or 0),
            caused_by=list(data.get("causedBy", data.get("caused_by", [])) or []),
            before_state=data.get("beforeState", data.get("before_state", {})) or {},
            after_state=data.get("afterState", data.get("after_state", {})) or {},
            semantic_payload=semantic,
            geospatial=geo,
        )


@dataclass
class HGNode:
    id: str
    kind: str
    position: Optional[List[float]] = None
    frequency: Optional[float] = None
    labels: Dict[str, Any] = None
    metadata: Dict[str, Any] = None
    created_at: float = None
    updated_at: float = None

    def to_dict(self) -> Dict[str, Any]:
        # Explicit field extraction avoids dataclasses.asdict's recursive deep-copy
        # which blows the stack when labels/metadata contain nested HGNode refs.
        return {
            "id": self.id,
            "kind": self.kind,
            "position": self.position,
            "frequency": self.frequency,
            "labels": dict(self.labels) if self.labels else {},
            "metadata": dict(self.metadata) if self.metadata else {},
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

@dataclass
class HGEdge:
    id: str
    kind: str
    nodes: List[str]
    weight: float = 1.0
    labels: Dict[str, Any] = None
    metadata: Dict[str, Any] = None
    timestamp: float = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "nodes": list(self.nodes) if self.nodes else [],
            "weight": self.weight,
            "labels": dict(self.labels) if self.labels else {},
            "metadata": dict(self.metadata) if self.metadata else {},
            "timestamp": self.timestamp,
        }


class HypergraphEngine:
    """Clarktech HypergraphEngine with simple GraphEvent emission and Perfetto trace support."""

    def __init__(self, freq_step_mhz: float = 10.0, decay_lambda: float = 0.0):
        # core stores
        self.nodes: Dict[str, HGNode] = {}
        self.edges: Dict[str, HGEdge] = {}
        # temporal decay configuration (λ in the exponential decay formula)
        # when zero the engine behaves as it did historically; a non‑zero
        # value causes existing edge weights to be decayed when reinforced
        # and allows an explicit `decay_edges()` call to prune old links.
        self.decay_lambda = float(decay_lambda)

        # indices
        self.node_to_edges: Dict[str, Set[str]] = defaultdict(set)
        self.kind_index: Dict[str, Set[str]] = defaultdict(set)
        self.edge_kind_index: Dict[str, Set[str]] = defaultdict(set)
        self.label_index: Dict[str, Dict[Any, Set[str]]] = defaultdict(lambda: defaultdict(set))
        self.freq_buckets: Dict[str, Set[str]] = defaultdict(set)
        self.degree: Dict[str, int] = defaultdict(int)

        # spatial helpers
        self._positions: Dict[str, Tuple[float, float, float]] = {}
        self._spatial_dirty = False
        self._spatial_index = None

        # concurrency
        self._lock = threading.RLock()

        # eventing
        self.subscribers: List[Callable] = []
        self.sequence: int = 0
        self.event_bus = None  # optional external GraphEventBus
        self._emitting = False  # re-entrancy guard for _emit

        # Perfetto trace support
        self.trace_id = str(uuid.uuid4())  # unique session identifier for tracing
        self.trace_buffer: List[PerfettoTraceEvent] = []
        self._span_counter = 0
        self.trace_enabled = True  # toggle trace collection
        self.max_trace_events = 100000  # circular buffer limit

        # config
        self.freq_step_mhz = float(freq_step_mhz)

        # spawn a background maintenance thread if a decay constant is set
        if self.decay_lambda and self.decay_lambda > 0:
            self._decay_thread = threading.Thread(target=self._decay_loop, daemon=True)
            self._decay_thread.start()

    @contextmanager
    def _suppress_emit(self):
        prev = self._emitting
        self._emitting = True
        try:
            yield
        finally:
            self._emitting = prev

    # ---------- Trace management ----------
    def _generate_span_id(self) -> str:
        """Generate a unique span ID for this trace session."""
        with self._lock:
            self._span_counter += 1
            return f"{self._span_counter:016x}"

    def _emit_trace_event(self, event: PerfettoTraceEvent) -> None:
        """Add a trace event to the buffer (non-blocking)."""
        if not self.trace_enabled:
            return
        with self._lock:
            # Enforce circular buffer limit
            if len(self.trace_buffer) >= self.max_trace_events:
                self.trace_buffer = self.trace_buffer[1000:]  # trim oldest 1000
            self.trace_buffer.append(event)

    def get_traces(self) -> List[PerfettoTraceEvent]:
        """Return a copy of all buffered trace events."""
        with self._lock:
            return list(self.trace_buffer)

    def export_traces_perfetto(self, path: Optional[str] = None) -> str:
        """Export trace events in Perfetto-compatible JSON format."""
        with self._lock:
            traces = [e.to_dict() for e in self.trace_buffer]

        output = {
            "traceSession": self.trace_id,
            "eventCount": len(traces),
            "geoEventCount": sum(1 for e in traces if e.get("geospatial")),
            "exportedAt": time.time(),
            "events": traces
        }

        if path:
            try:
                ddir = os.path.dirname(path) or '.'
                if not os.path.exists(ddir):
                    os.makedirs(ddir, exist_ok=True)
                with open(path, 'w') as f:
                    json.dump(output, f, indent=2)
            except Exception:
                pass

        return json.dumps(output)

    def clear_traces(self) -> int:
        """Clear trace buffer and return count of cleared events."""
        with self._lock:
            count = len(self.trace_buffer)
            self.trace_buffer.clear()
            return count

    def _trace_number(self, value: Any) -> Optional[float]:
        try:
            n = float(value)
            return n if math.isfinite(n) else None
        except Exception:
            return None

    def _extract_geospatial(self, *sources: Any) -> Dict[str, Any]:
        """Return normalized lat/lon/alt telemetry from graph or trace payloads."""
        stack: List[Any] = list(sources)
        seen: Set[int] = set()

        while stack:
            src = stack.pop()
            if src is None:
                continue
            if isinstance(src, (HGNode, HGEdge)):
                src = src.to_dict()
            if not isinstance(src, dict):
                continue
            oid = id(src)
            if oid in seen:
                continue
            seen.add(oid)

            lat = self._trace_number(src.get("lat", src.get("latitude")))
            lon = self._trace_number(src.get("lon", src.get("lng", src.get("longitude"))))
            alt = self._trace_number(src.get("alt", src.get("altitude", src.get("altitude_m", 0))))

            pos = src.get("position") or src.get("coordinates") or src.get("centroid")
            if (lat is None or lon is None) and isinstance(pos, (list, tuple)) and len(pos) >= 2:
                lat = self._trace_number(pos[0])
                lon = self._trace_number(pos[1])
                if len(pos) >= 3:
                    alt = self._trace_number(pos[2])

            if lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180:
                uncertainty = self._trace_number(
                    src.get(
                        "uncertainty_radius",
                        src.get("uncertaintyRadius", src.get("accuracy_m", src.get("radius_m"))),
                    )
                )
                if uncertainty is None:
                    for child_key in ("metadata", "labels", "geospatial", "geo", "location"):
                        child = src.get(child_key)
                        if isinstance(child, dict):
                            uncertainty = self._trace_number(
                                child.get(
                                    "uncertainty_radius",
                                    child.get("uncertaintyRadius", child.get("accuracy_m", child.get("radius_m"))),
                                )
                            )
                            if uncertainty is not None:
                                break
                geo = {
                    "lat": lat,
                    "lon": lon,
                    "alt": alt or 0.0,
                    "spatial_frame": src.get("spatial_frame", "EPSG:4326"),
                }
                if uncertainty is not None:
                    geo["uncertainty_radius"] = uncertainty
                for key in ("azimuth", "signal_dbm", "doppler", "confidence"):
                    val = self._trace_number(src.get(key))
                    if val is not None:
                        geo[key] = val
                return geo

            for key in (
                "geospatial",
                "geo",
                "location",
                "metadata",
                "labels",
                "semantic_payload",
                "semanticPayload",
                "after_state",
                "afterState",
                "before_state",
                "beforeState",
            ):
                child = src.get(key)
                if isinstance(child, dict):
                    stack.append(child)

        return {}

    def _edge_geospatial(self, edge: HGEdge) -> Dict[str, Any]:
        explicit = self._extract_geospatial(edge)
        if explicit:
            return explicit

        points = []
        for nid in edge.nodes or []:
            node = self.nodes.get(nid)
            geo = self._extract_geospatial(node) if node else {}
            if geo:
                points.append(geo)

        if not points:
            return {}

        lat = sum(p["lat"] for p in points) / len(points)
        lon = sum(p["lon"] for p in points) / len(points)
        alt = sum(p.get("alt", 0.0) for p in points) / len(points)
        uncertainties = [p.get("uncertainty_radius") for p in points if p.get("uncertainty_radius") is not None]
        geo = {
            "lat": lat,
            "lon": lon,
            "alt": alt,
            "spatial_frame": "EPSG:4326",
            "projection": "causal_centroid",
            "source_entities": list(edge.nodes or []),
        }
        if uncertainties:
            geo["uncertainty_radius"] = max(uncertainties)
        return geo

    def _entity_or_causal_geospatial(self, entity_id: str = "", caused_by: Optional[List[str]] = None) -> Dict[str, Any]:
        if entity_id and entity_id in self.nodes:
            geo = self._extract_geospatial(self.nodes[entity_id])
            if geo:
                return geo
        pseudo_edge = HGEdge(id="_trace_context", kind="_trace_context", nodes=list(caused_by or []))
        return self._edge_geospatial(pseudo_edge)

    def _normalize_node_data(self, d: dict, fallback_id: Optional[str] = None) -> dict:
        src = dict(d or {})
        # extract core fields
        nid = src.get('id') or src.get('node_id') or fallback_id
        kind = src.get('kind') or src.get('type') or 'entity'
        pos = src.get('position')
        freq = src.get('frequency')
        labels = src.get('labels') or {}
        meta = src.get('metadata') or {}

        # move everything else into metadata (avoid HGNode strict init errors)
        known_keys = {'id', 'node_id', 'kind', 'type', 'position', 'frequency', 'labels', 'metadata', 'created_at', 'updated_at'}
        for k, v in src.items():
            if k not in known_keys:
                meta[k] = v

        return {
            'id': nid,
            'kind': kind,
            'position': pos,
            'frequency': freq,
            'labels': labels,
            'metadata': meta,
            'created_at': src.get('created_at'),
            'updated_at': src.get('updated_at')
        }

    def _normalize_edge_data(self, d: dict, fallback_id: Optional[str] = None) -> dict:
        src = dict(d or {})
        # defaults
        eid_val = src.get('id') or fallback_id
        kind = src.get('kind') or src.get('type') or 'edge'
        nodes = src.get('nodes') or []
        weight = src.get('weight', 1.0)
        labels = src.get('labels') or {}
        meta = src.get('metadata') or {}
        timestamp = src.get('timestamp') or time.time()

        # move extra keys to metadata
        known_keys = {'id', 'kind', 'type', 'nodes', 'weight', 'labels', 'metadata', 'timestamp'}
        for k, v in src.items():
            if k not in known_keys:
                meta[k] = v

        return {
            'id': eid_val,
            'kind': kind,
            'nodes': nodes,
            'weight': weight,
            'labels': labels,
            'metadata': meta,
            'timestamp': timestamp
        }

    # ---------- Index hygiene helpers ----------
    def _deindex_node(self, node_id: str, old_node: HGNode) -> None:
        """Remove a node from all secondary indices (kind, label, freq, position).
        Must be called BEFORE overwriting self.nodes[node_id]."""
        self.kind_index[old_node.kind].discard(node_id)
        if old_node.labels:
            for k, v in old_node.labels.items():
                if isinstance(v, (list, tuple, set)):
                    for it in v:
                        self.label_index[k][it].discard(node_id)
                else:
                    self.label_index[k][v].discard(node_id)
        if old_node.frequency is not None:
            self.freq_buckets[self._freq_band(old_node.frequency, step=self.freq_step_mhz)].discard(node_id)
        self._positions.pop(node_id, None)

    def _deindex_edge(self, edge_id: str, old_edge: HGEdge) -> None:
        """Remove an edge from all secondary indices.
        Must be called BEFORE overwriting self.edges[edge_id]."""
        self.edge_kind_index[old_edge.kind].discard(edge_id)
        for nid in old_edge.nodes:
            self.node_to_edges[nid].discard(edge_id)
            self.degree[nid] = max(0, self.degree.get(nid, 1) - 1)

    # ---------- Node ops ----------
    def add_node(self, node: Any) -> str:
        _ge = None
        _trace_event = None
        with self._lock:
            now = time.time()
            if not isinstance(node, HGNode):
                # use consistent normalization
                data = self._normalize_node_data(node)
                node = HGNode(**data)
            node.created_at = node.created_at or now
            node.updated_at = now

            # --- index hygiene: clean stale indices if overwriting ---
            existing = self.nodes.get(node.id)
            if existing is not None:
                self._deindex_node(node.id, existing)
                # preserve original created_at on upsert
                node.created_at = existing.created_at or node.created_at

            self.nodes[node.id] = node

            self.kind_index[node.kind].add(node.id)

            if node.labels:
                for k, v in node.labels.items():
                    if isinstance(v, (list, tuple, set)):
                        for it in v:
                            self.label_index[k][it].add(node.id)
                    else:
                        self.label_index[k][v].add(node.id)

            if node.frequency is not None:
                band = self._freq_band(node.frequency, step=self.freq_step_mhz)
                self.freq_buckets[band].add(node.id)

            if node.position:
                self._positions[node.id] = tuple(node.position[:3]) if len(node.position) >= 2 else tuple(node.position)
                self._spatial_dirty = True

            self.degree.setdefault(node.id, 0)

            # build event dict while lock is held; dispatch after releasing
            event_type = 'NODE_UPDATE' if existing else 'NODE_CREATE'
            try:
                self.sequence += 1
                node_state = node.to_dict()
                before_node_state = existing.to_dict() if existing else {}
                geo = self._extract_geospatial(node_state)
                _ge = {
                    'event_type': event_type,
                    'entity_id': node.id,
                    'entity_kind': node.kind,
                    'entity_data': node_state,
                    'timestamp': time.time(),
                    'sequence_id': self.sequence
                }

                # Emit Perfetto trace event
                if self.trace_enabled:
                    _trace_event = PerfettoTraceEvent(
                        trace_id=self.trace_id,
                        span_id=self._generate_span_id(),
                        event_type=event_type,
                        entity_id=node.id,
                        entity_kind=node.kind,
                        timestamp_ns=int(now * 1e9),
                        before_state=before_node_state,
                        after_state=node_state,
                        caused_by=[],  # top-level node creation
                        semantic_payload={
                            'sequence_id': self.sequence,
                            'position': node.position,
                            'frequency': node.frequency,
                            'geospatial': geo,
                        },
                        geospatial=geo,
                    )
            except Exception:
                pass

        # emit AFTER releasing lock so subscriber callbacks don't block
        # other threads waiting to mutate the graph
        if _ge is not None:
            try:
                self._emit(_ge)
            except Exception:
                pass

        if _trace_event is not None:
            try:
                self._emit_trace_event(_trace_event)
            except Exception:
                pass

        return node.id

    def update_node(self, node_id: str, **updates) -> Optional[HGNode]:
        _ge = None
        _trace_event = None
        with self._lock:
            node = self.nodes.get(node_id)
            if not node:
                return None

            # Capture before state for tracing
            before_state = node.to_dict()
            now = time.time()

            # --- remove ALL old indices (kind + labels + freq + position) ---
            self._deindex_node(node_id, node)

            # apply updates
            for k, v in updates.items():
                setattr(node, k, v)
            node.updated_at = now

            # --- reindex ALL (kind + labels + freq + position) ---
            self.kind_index[node.kind].add(node_id)
            if node.labels:
                for k, v in node.labels.items():
                    if isinstance(v, (list, tuple, set)):
                        for it in v:
                            self.label_index[k][it].add(node_id)
                    else:
                        self.label_index[k][v].add(node_id)
            if node.frequency is not None:
                self.freq_buckets[self._freq_band(node.frequency, step=self.freq_step_mhz)].add(node_id)
            if node.position:
                self._positions[node.id] = tuple(node.position[:3]) if len(node.position) >= 2 else tuple(node.position)
                self._spatial_dirty = True

            try:
                self.sequence += 1
                node_state = node.to_dict()
                geo = self._extract_geospatial(node_state, updates)
                _ge = {
                    'event_type': 'NODE_UPDATE',
                    'entity_id': node_id,
                    'entity_kind': node.kind,
                    'entity_data': node_state,
                    'timestamp': now,
                    'sequence_id': self.sequence
                }

                # Emit Perfetto trace event
                if self.trace_enabled:
                    _trace_event = PerfettoTraceEvent(
                        trace_id=self.trace_id,
                        span_id=self._generate_span_id(),
                        event_type='NODE_UPDATE',
                        entity_id=node_id,
                        entity_kind=node.kind,
                        timestamp_ns=int(now * 1e9),
                        before_state=before_state,
                        after_state=node_state,
                        caused_by=updates.get('_caused_by', []),  # allow caller to specify causality
                        semantic_payload={
                            'sequence_id': self.sequence,
                            'updates': {k: v for k, v in updates.items() if k != '_caused_by'},
                            'geospatial': geo,
                        },
                        geospatial=geo,
                    )
            except Exception:
                pass

        if _ge is not None:
            try:
                self._emit(_ge)
            except Exception:
                pass

        if _trace_event is not None:
            try:
                self._emit_trace_event(_trace_event)
            except Exception:
                pass

        return node

    def get_node(self, node_id: str) -> Optional[HGNode]:
        return self.nodes.get(node_id)

    def remove_node(self, node_id: str) -> None:
        _events = []
        _trace_events = []
        with self._lock:
            node = self.nodes.pop(node_id, None)
            if not node:
                return
            now = time.time()
            self.kind_index[node.kind].discard(node_id)
            if node.labels:
                for k, v in node.labels.items():
                    if isinstance(v, (list, tuple, set)):
                        for it in v:
                            self.label_index[k][it].discard(node_id)
                    else:
                        self.label_index[k][v].discard(node_id)
            if node.frequency is not None:
                self.freq_buckets[self._freq_band(node.frequency, step=self.freq_step_mhz)].discard(node_id)
            self._positions.pop(node_id, None)
            self._spatial_dirty = True
            # inline edge removal to keep all mutations under a single lock acquisition
            for eid in list(self.node_to_edges.get(node_id, [])):
                edge = self.edges.pop(eid, None)
                if edge:
                    self.edge_kind_index[edge.kind].discard(eid)
                    for nid in edge.nodes:
                        self.node_to_edges[nid].discard(eid)
                        self.degree[nid] = max(0, self.degree.get(nid, 1) - 1)
                    try:
                        self.sequence += 1
                        _events.append({
                            'event_type': 'EDGE_DELETE',
                            'entity_id': eid,
                            'entity_kind': edge.kind,
                            'entity_data': {'id': eid},
                            'timestamp': now,
                            'sequence_id': self.sequence
                        })

                        # Emit trace event for edge deletion
                        if self.trace_enabled:
                            _trace_events.append(PerfettoTraceEvent(
                                trace_id=self.trace_id,
                                span_id=self._generate_span_id(),
                                event_type='EDGE_DELETE',
                                entity_id=eid,
                                entity_kind=edge.kind,
                                timestamp_ns=int(now * 1e9),
                                before_state=edge.to_dict(),
                                after_state={},
                                caused_by=[node_id],  # cascading delete from node removal
                                semantic_payload={'cascade': True}
                            ))
                    except Exception:
                        pass
            self.node_to_edges.pop(node_id, None)
            self.degree.pop(node_id, None)

            try:
                self.sequence += 1
                _events.append({
                    'event_type': 'NODE_DELETE',
                    'entity_id': node_id,
                    'entity_kind': node.kind,
                    'entity_data': {'id': node_id},
                    'timestamp': now,
                    'sequence_id': self.sequence
                })

                # Emit trace event for node deletion
                if self.trace_enabled:
                    _trace_events.append(PerfettoTraceEvent(
                        trace_id=self.trace_id,
                        span_id=self._generate_span_id(),
                        event_type='NODE_DELETE',
                        entity_id=node_id,
                        entity_kind=node.kind,
                        timestamp_ns=int(now * 1e9),
                        before_state=node.to_dict(),
                        after_state={},
                        caused_by=[],
                        semantic_payload={}
                    ))
            except Exception:
                pass

        for ge in _events:
            try:
                self._emit(ge)
            except Exception:
                pass

        for te in _trace_events:
            try:
                self._emit_trace_event(te)
            except Exception:
                pass

    # ---------- Edge ops ----------
    def add_edge(self, edge: Any) -> str:
        _ge = None
        _trace_event = None
        with self._lock:
            now = time.time()
            if not isinstance(edge, HGEdge):
                # use consistent normalization
                data = self._normalize_edge_data(edge)
                edge = HGEdge(**data)
            edge.timestamp = edge.timestamp or now

            existing = self.edges.get(edge.id)
            before_state = existing.to_dict() if existing else {}

            if existing is not None:
                # reinforce an existing edge; apply decay to its previous
                # weight before combining so we don't accumulate stale mass.
                if self.decay_lambda:
                    age = now - (existing.timestamp or now)
                    existing.weight = existing.weight * math.exp(-self.decay_lambda * age)
                existing.weight = existing.weight + edge.weight
                existing.timestamp = now
                # update reinforcement counter in metadata
                rc = existing.metadata.get('reinforcement_count', 1)
                existing.metadata['reinforcement_count'] = rc + 1
                # preserve first_seen if present
                if 'first_seen' not in existing.metadata:
                    existing.metadata['first_seen'] = existing.timestamp
                edge = existing
                event_type = 'EDGE_UPDATE'
            else:
                # new edge: initialize reinforcement counter and first_seen
                edge.metadata.setdefault('reinforcement_count', 1)
                edge.metadata.setdefault('first_seen', edge.timestamp or now)
                event_type = 'EDGE_CREATE'

            # --- index hygiene: if we replaced an edge object entirely we
            # need to make sure indices reflect the final object.  in the
            # reinforcement case `edge` is the original existing instance,
            # so deindexing/reindexing is unnecessary because nothing changed
            # except weight and timestamp (which aren't indexed).
            self.edges[edge.id] = edge
            self.edge_kind_index[edge.kind].add(edge.id)
            for nid in edge.nodes:
                self.node_to_edges[nid].add(edge.id)
                self.degree[nid] = self.degree.get(nid, 0) + (0 if existing else 1)

            try:
                self.sequence += 1
                edge_state = edge.to_dict()
                geo = self._edge_geospatial(edge)
                _ge = {
                    'event_type': event_type,
                    'entity_id': edge.id,
                    'entity_kind': edge.kind,
                    'entity_data': edge_state,
                    'timestamp': time.time(),
                    'sequence_id': self.sequence
                }

                # Emit Perfetto trace event
                if self.trace_enabled:
                    _trace_event = PerfettoTraceEvent(
                        trace_id=self.trace_id,
                        span_id=self._generate_span_id(),
                        event_type=event_type,
                        entity_id=edge.id,
                        entity_kind=edge.kind,
                        timestamp_ns=int(now * 1e9),
                        before_state=before_state,
                        after_state=edge_state,
                        caused_by=edge.nodes,  # edges are caused by their connected nodes
                        semantic_payload={
                            'sequence_id': self.sequence,
                            'weight': edge.weight,
                            'reinforcement_count': edge.metadata.get('reinforcement_count', 1),
                            'nodes': edge.nodes,
                            'geospatial': geo,
                        },
                        geospatial=geo,
                    )
            except Exception:
                pass

        if _ge is not None:
            try:
                self._emit(_ge)
            except Exception:
                pass

        if _trace_event is not None:
            try:
                self._emit_trace_event(_trace_event)
            except Exception:
                pass

        return edge.id

    def remove_edge(self, edge_id: str) -> None:
        _ge = None
        _trace_event = None
        with self._lock:
            edge = self.edges.pop(edge_id, None)
            if not edge:
                return
            now = time.time()
            self.edge_kind_index[edge.kind].discard(edge_id)
            for nid in edge.nodes:
                self.node_to_edges[nid].discard(edge_id)
                self.degree[nid] = max(0, self.degree.get(nid, 1) - 1)

            try:
                self.sequence += 1
                geo = self._edge_geospatial(edge)
                _ge = {
                    'event_type': 'EDGE_DELETE',
                    'entity_id': edge_id,
                    'entity_kind': edge.kind,
                    'entity_data': {'id': edge_id},
                    'timestamp': now,
                    'sequence_id': self.sequence
                }

                # Emit Perfetto trace event
                if self.trace_enabled:
                    _trace_event = PerfettoTraceEvent(
                        trace_id=self.trace_id,
                        span_id=self._generate_span_id(),
                        event_type='EDGE_DELETE',
                        entity_id=edge_id,
                        entity_kind=edge.kind,
                        timestamp_ns=int(now * 1e9),
                        before_state=edge.to_dict(),
                        after_state={},
                        caused_by=[],
                        semantic_payload={'geospatial': geo},
                        geospatial=geo,
                    )
            except Exception:
                pass

        if _ge is not None:
            try:
                self._emit(_ge)
            except Exception:
                pass

        if _trace_event is not None:
            try:
                self._emit_trace_event(_trace_event)
            except Exception:
                pass

    def get_edge(self, edge_id: str) -> Optional[HGEdge]:
        return self.edges.get(edge_id)

    def _decay_loop(self):
        """Simple periodic maintenance task that decays and prunes edges."""
        # run every minute by default; wheel backoff if desired in the future
        while True:
            time.sleep(60)
            try:
                # use configured lambda, zero min_weight means only decay
                self.decay_edges()
            except Exception:
                # swallow exceptions so the thread doesn't die silently
                continue

    # ---------- Decay helpers ----------
    def compute_edge_persistence(self, edge: HGEdge, now: float) -> float:
        """
        edge_persistence = semantic_importance * recurrence * cross-layer_support * predictive_value
        """
        # 1. Temporal Weight (Recency)
        age = now - (edge.timestamp or now)
        temporal_weight = math.exp(-self.decay_lambda * age) if self.decay_lambda > 0 else 1.0

        # 2. Semantic Importance (Recurrence)
        # Higher reinforcement count = higher persistence
        rc = edge.metadata.get('reinforcement_count', 1)
        recurrence_weight = math.log1p(rc) / 5.0 # Normalized

        # 3. Cross-layer Support
        # Does this edge have support from multiple detectors?
        cl_support = 1.0
        if 'provenance' in edge.metadata:
            cl_support = min(1.5, len(edge.metadata.get('provenance', {}).get('evidence', [])) * 0.2 + 1.0)

        # 4. Predictive Value
        # Edges used in successful forecasts gain "attention salience"
        pv = edge.metadata.get('predictive_value', 1.0)

        return temporal_weight * (0.4 * recurrence_weight + 0.3 * cl_support + 0.3 * pv)

    def decay_edges(self, lambda_const: float = None, min_weight: float = 0.05) -> int:
        """Apply attention-weighted temporal decay to all edges and prune those
        whose *effective* persistence score falls below *min_weight*.
        """
        with self._lock:
            if lambda_const is None:
                lambda_const = self.decay_lambda
            now = time.time()
            to_delete = []
            for eid, edge in list(self.edges.items()):
                persistence = self.compute_edge_persistence(edge, now)

                # Semantic Pruning: keep highly salient edges even if they are old
                # Analogous to "heavy hitter" retention in LLM KV caches.
                if persistence < min_weight:
                    to_delete.append(eid)
                else:
                    # Optional: update weight to reflect persistence for visualization
                    # edge.weight = persistence
                    pass

            for eid in to_delete:
                self.remove_edge(eid)
            return len(to_delete)

    # ---------- Frequency band helpers ----------
    def _freq_band(self, freq: float, step: float = 10.0) -> str:
        """Map a frequency to a band key (e.g., 915.2 MHz -> '910-920' at step=10)."""
        if freq is None:
            return 'none'
        band_min = int((freq // step) * step)
        band_max = band_min + int(step)
        return f"{band_min}-{band_max}"

    def _bands_between(self, fmin: float, fmax: float, step: float = 10.0) -> List[str]:
        """Return list of band keys spanning frequency range."""
        bands = []
        current = int((fmin // step) * step)
        end = int((fmax // step) * step) + int(step)
        while current <= end:
            band_max = current + int(step)
            bands.append(f"{current}-{band_max}")
            current = band_max
        return bands

    # ---------- Spectral Analysis ----------
    def compute_spectral_vulnerability(self) -> Dict[str, Any]:
        """
        Detect graph bottlenecks and biconnected vulnerability.
        Uses the Fiedler value (second smallest eigenvalue of Laplacian)
        as a proxy for graph connectivity.
        """
        import numpy as np

        with self._lock:
            if not self.nodes:
                return {"vulnerability": 0.0, "reason": "empty_graph"}

            node_ids = list(self.nodes.keys())
            node_idx = {nid: i for i, nid in enumerate(node_ids)}
            size = len(node_ids)

            # Adjacency matrix
            adj = np.zeros((size, size))
            for edge in self.edges.values():
                for n1 in edge.nodes:
                    for n2 in edge.nodes:
                        if n1 != n2 and n1 in node_idx and n2 in node_idx:
                            i, j = node_idx[n1], node_idx[n2]
                            adj[i, j] = adj[j, i] = 1

            # Laplacian L = D - A
            deg = np.sum(adj, axis=1)
            laplacian = np.diag(deg) - adj

            # Eigenvalues
            eigenvalues = np.sort(np.linalg.eigvalsh(laplacian))

            # Fiedler value is the second smallest
            fiedler = eigenvalues[1] if size > 1 else 0.0

            # Vulnerability: Inverse to biconnectivity. Low Fiedler = easy to cut/isolate.
            vulnerability = 1.0 / (1.0 + fiedler)

            return {
                "fiedler_value": float(fiedler),
                "vulnerability": float(vulnerability),
                "size": size,
                "bottlenecked": fiedler < 1.0 # Empirical threshold for "fragile" graphs
            }
    def nodes_by_kind(self, kind: str) -> Iterable[HGNode]:
        for nid in self.kind_index.get(kind, []):
            n = self.nodes.get(nid)
            if n:
                yield n

    def nodes_with_label(self, key: str, value: Any) -> Iterable[HGNode]:
        for nid in self.label_index.get(key, {}).get(value, []):
            n = self.nodes.get(nid)
            if n:
                yield n

    def nodes_in_freq_band(self, fmin: float, fmax: float) -> Iterable[HGNode]:
        bands = self._bands_between(fmin, fmax, step=self.freq_step_mhz)
        seen: Set[str] = set()
        for b in bands:
            for nid in self.freq_buckets.get(b, []):
                if nid in seen:
                    continue
                node = self.nodes.get(nid)
                if node and node.frequency is not None and fmin <= node.frequency <= fmax:
                    seen.add(nid)
                    yield node

    def edges_for_node(self, node_id: str) -> Iterable[HGEdge]:
        for eid in self.node_to_edges.get(node_id, []):
            e = self.edges.get(eid)
            if e:
                yield e

    def top_central_nodes(self, k: int = 5):
        with self._lock:
            return sorted(self.degree.items(), key=lambda x: x[1], reverse=True)[:k]

    # ---------- Spatial (simple) ----------
    def rebuild_spatial_index(self):
        """Build a cKDTree from node positions for O(log n) bbox/proximity queries.

        Only indexes positions with valid geographic coordinates (lat ∈ [-90,90],
        lon ∈ [-180,180]) and skips the null-island origin (0,0) to avoid
        pseudo-geo/unpositioned nodes polluting bbox results.
        """
        try:
            from scipy.spatial import cKDTree as _cKDTree
            _scipy_ok = True
        except ImportError:
            _scipy_ok = False

        with self._lock:
            self._spatial_dirty = False
            valid_ids = []
            valid_coords = []
            for nid, pos in self._positions.items():
                if len(pos) < 2:
                    continue
                lat, lon = pos[0], pos[1]
                if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
                    continue
                if lat == 0.0 and lon == 0.0:
                    continue  # null-island / unpositioned default
                valid_ids.append(nid)
                valid_coords.append((lat, lon))

            if not valid_ids or not _scipy_ok:
                self._spatial_index = None
                return
            try:
                import numpy as np
                arr = np.array(valid_coords, dtype=float)
                self._spatial_index = (_cKDTree(arr), valid_ids)
            except Exception:
                self._spatial_index = None

    def _ensure_spatial_index(self):
        if self._spatial_dirty or self._spatial_index is None:
            self.rebuild_spatial_index()

    def nodes_in_bbox(self, min_lat: float, max_lat: float, min_lon: float, max_lon: float) -> Iterable[HGNode]:
        self._ensure_spatial_index()
        if self._spatial_index is not None:
            tree, ids = self._spatial_index
            try:
                import numpy as np
                center = np.array([(min_lat + max_lat) / 2, (min_lon + max_lon) / 2])
                half_diag = math.sqrt(((max_lat - min_lat) / 2) ** 2 + ((max_lon - min_lon) / 2) ** 2)
                candidate_idxs = tree.query_ball_point(center, half_diag)
                for i in candidate_idxs:
                    nid = ids[i]
                    pos = self._positions.get(nid)
                    if pos is None:
                        continue
                    lat, lon = pos[0], pos[1]
                    if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
                        n = self.nodes.get(nid)
                        if n:
                            yield n
                return
            except Exception:
                pass
        # fallback: O(n) scan
        for nid, pos in list(self._positions.items()):
            lat, lon, *_ = pos
            if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
                n = self.nodes.get(nid)
                if n:
                    yield n

    # ---------- Semantic trace emissions ----------
    def trace_confidence_mutation(self, entity_id: str, entity_kind: str,
                                  confidence_before: float, confidence_after: float,
                                  caused_by: List[str] = None, reason: str = "") -> str:
        """Emit a confidence mutation trace event (key semantic event per Perfetto_Hypergraph design).

        This allows tracking how entity confidence evolves over time, which is central to
        the temporal cognition trace visualization concept.
        """
        now = time.time()
        caused_by = caused_by or []
        span_id = self._generate_span_id()
        geo = self._entity_or_causal_geospatial(entity_id, caused_by)

        if self.trace_enabled:
            trace_event = PerfettoTraceEvent(
                trace_id=self.trace_id,
                span_id=span_id,
                event_type='CONFIDENCE_MUTATION',
                entity_id=entity_id,
                entity_kind=entity_kind,
                timestamp_ns=int(now * 1e9),
                caused_by=caused_by,
                semantic_payload={
                    'confidence_before': confidence_before,
                    'confidence_after': confidence_after,
                    'confidence_delta': confidence_after - confidence_before,
                    'reason': reason,
                    'timestamp': now,
                    'geospatial': geo,
                },
                geospatial=geo,
            )
            try:
                self._emit_trace_event(trace_event)
            except Exception:
                pass

        return span_id

    def trace_hypothesis_event(self, hypothesis_id: str, event_type: str,
                               entity_ids: List[str], confidence: float,
                               details: Dict[str, Any] = None) -> str:
        """Emit a hypothesis-related trace event (e.g., generated, rejected, reinforced).

        Useful for agent arbitration and multi-hypothesis tracking.
        """
        now = time.time()
        span_id = self._generate_span_id()
        details = details or {}
        geo = self._entity_or_causal_geospatial(hypothesis_id, entity_ids)

        if self.trace_enabled:
            trace_event = PerfettoTraceEvent(
                trace_id=self.trace_id,
                span_id=span_id,
                event_type=f'HYPOTHESIS_{event_type.upper()}',
                entity_id=hypothesis_id,
                entity_kind='hypothesis',
                timestamp_ns=int(now * 1e9),
                caused_by=entity_ids,
                semantic_payload={
                    'confidence': confidence,
                    'related_entities': entity_ids,
                    'geospatial': geo,
                    **details
                },
                geospatial=geo,
            )
            try:
                self._emit_trace_event(trace_event)
            except Exception:
                pass

        return span_id

    # ---------- Snapshot / persistence ----------
    def snapshot(self, include_traces: bool = False) -> Dict[str, Any]:
        with self._lock:
            result = {
                'nodes': [n.to_dict() for n in self.nodes.values()],
                'edges': [e.to_dict() for e in self.edges.values()],
                'ts': time.time()
            }
            if include_traces:
                result['traces'] = [t.to_dict() for t in self.trace_buffer]
                result['trace_id'] = self.trace_id
            return result

    def save_snapshot(self, path: str, include_traces: bool = False) -> None:
        try:
            dump = self.snapshot(include_traces=include_traces)
            tmp = f"{path}.tmp"
            ddir = os.path.dirname(path) or '.'
            if not os.path.exists(ddir):
                try:
                    os.makedirs(ddir, exist_ok=True)
                except Exception:
                    pass
            with open(tmp, 'w') as f:
                json.dump(dump, f)
            os.replace(tmp, path)
        except Exception:
            return

    def load_snapshot(self, path: str) -> bool:
        try:
            if not os.path.exists(path):
                return False
            with open(path, 'r') as f:
                dump = json.load(f)
            nodes = dump.get('nodes', [])
            edges = dump.get('edges', [])
            traces = dump.get('traces', [])
            trace_id = dump.get('trace_id') or dump.get('traceSession') or self.trace_id
            with self._lock:
                # clear current
                self.nodes.clear()
                self.edges.clear()
                self.trace_buffer.clear()
                self.node_to_edges.clear()
                self.kind_index.clear()
                self.edge_kind_index.clear()
                self.label_index.clear()
                self.freq_buckets.clear()
                self.degree.clear()
                self._positions.clear()

                # Suppress events during snapshot replay to prevent echo storms
                prev_trace_enabled = self.trace_enabled
                self.trace_enabled = False
                try:
                    with self._suppress_emit():
                        for n in nodes:
                            try:
                                self.add_node(n)
                            except Exception:
                                continue
                        for e in edges:
                            try:
                                self.add_edge(e)
                            except Exception:
                                continue
                finally:
                    self.trace_enabled = prev_trace_enabled
                if trace_id:
                    self.trace_id = trace_id
                max_span = 0
                for t in traces:
                    try:
                        event = PerfettoTraceEvent.from_dict(t)
                        self.trace_buffer.append(event)
                        try:
                            max_span = max(max_span, int(event.span_id, 16))
                        except Exception:
                            pass
                    except Exception:
                        continue
                if max_span:
                    self._span_counter = max(self._span_counter, max_span)
            return True
        except Exception:
            return False

    # ---------- Eventing ----------
    def subscribe(self, callback: Callable) -> None:
        with self._lock:
            self.subscribers.append(callback)

    def _emit(self, ge: Dict[str, Any]) -> None:
        # Re-entrancy guard: prevent infinite loops when apply_graph_event
        # triggers further add_node/update_node calls that would re-emit.
        if self._emitting:
            return
        self._emitting = True
        try:
            # local subscribers
            for cb in list(self.subscribers):
                try:
                    cb(ge)
                except Exception:
                    continue

            # external event bus (if attached)
            eb = getattr(self, 'event_bus', None)
            if eb and hasattr(eb, 'publish'):
                try:
                    eb.publish(SimpleNamespace(**ge))
                except Exception:
                    try:
                        eb.publish(ge)
                    except Exception:
                        pass
        except Exception:
            pass
        finally:
            self._emitting = False

    def apply_graph_event(self, ge) -> bool:
        """Apply a GraphEvent (dict or object) to this HypergraphEngine (best-effort)."""
        # (3) Event replay suppression to prevent "echo"
        with self._suppress_emit():
            try:
                if ge is None:
                    return False
                if isinstance(ge, dict):
                    et = ge.get('event_type')
                    eid = ge.get('entity_id')
                    data = ge.get('entity_data') or {}
                else:
                    et = getattr(ge, 'event_type', None)
                    eid = getattr(ge, 'entity_id', None)
                    data = getattr(ge, 'entity_data', None) or {}

                if not et:
                    return False

                if et == 'NODE_CREATE':
                    nd = self._normalize_node_data(data, fallback_id=eid)
                    nid = nd.get('id')
                    if not nid:
                        return False

                    if self.get_node(nid):
                        # (1) Partial update safety: only patch keys present in source
                        src = data if isinstance(data, dict) else {}
                        patch = {}
                        if 'position' in src: patch['position'] = nd['position']
                        if 'frequency' in src: patch['frequency'] = nd['frequency']
                        if 'labels' in src: patch['labels'] = nd['labels']

                        # Handle metadata + implicit extra fields
                        known_keys = {'id', 'node_id', 'kind', 'type', 'position', 'frequency', 'labels', 'metadata', 'created_at', 'updated_at'}
                        has_implicit = any(k not in known_keys for k in src)
                        if 'metadata' in src or has_implicit:
                            patch['metadata'] = nd['metadata']

                        self.update_node(nid, **patch)
                    else:
                        nd.setdefault('id', nid)
                        nd.setdefault('kind', 'entity')
                        self.add_node(nd)
                    return True

                if et == 'NODE_UPDATE':
                    nd = self._normalize_node_data(data, fallback_id=eid)
                    nid = nd.get('id')
                    if not nid:
                        return False

                    # Use upsert logic (create if missing, update if present)
                    if self.get_node(nid):
                        # (1) Partial update safety: only patch keys present in source
                        src = data if isinstance(data, dict) else {}
                        patch = {}
                        if 'position' in src: patch['position'] = nd['position']
                        if 'frequency' in src: patch['frequency'] = nd['frequency']
                        if 'labels' in src: patch['labels'] = nd['labels']

                        # Handle metadata + implicit extra fields
                        known_keys = {'id', 'node_id', 'kind', 'type', 'position', 'frequency', 'labels', 'metadata', 'created_at', 'updated_at'}
                        has_implicit = any(k not in known_keys for k in src)
                        if 'metadata' in src or has_implicit:
                            patch['metadata'] = nd['metadata']

                        self.update_node(nid, **patch)
                    else:
                        nd.setdefault('id', nid)
                        nd.setdefault('kind', 'entity')
                        self.add_node(nd)
                    return True

                if et == 'NODE_DELETE':
                    if eid:
                        self.remove_node(eid)
                        return True
                    return False

                if et in ('EDGE_CREATE', 'HYPEREDGE_CREATE', 'EDGE_UPDATE'):
                    ed = self._normalize_edge_data(data, fallback_id=eid)
                    edge_id = ed.get('id') or eid
                    if not edge_id:
                        return False
                    try:
                        if self.get_edge(edge_id):
                            self.remove_edge(edge_id)
                    except Exception:
                        pass
                    self.add_edge(ed)
                    return True

                if et in ('EDGE_DELETE', 'HYPEREDGE_DELETE'):
                    if eid:
                        self.remove_edge(eid)
                        return True
                    return False

                return False
            except Exception:
                return False

    def add_geo_streamline(self, field: str, index: int, polyline: List[Tuple[float, float, float]], metadata: Dict[str, Any] = None) -> str:
        """Add a geo_streamline node and its member geo_point nodes with edges."""
        sid = f"geo_streamline:{field}:{index}"
        s_node = {
            'id': sid,
            'kind': 'geo_streamline',
            'labels': {'field': field, 'index': index},
            'metadata': metadata or {'source': 'cesium_field'}
        }
        self.add_node(s_node)

        for j, (lat, lon, alt) in enumerate(polyline):
            pid = f"geo_point:{sid}:{j}"
            p_node = {
                'id': pid,
                'kind': 'geo_point',
                'position': [lat, lon, alt],
                'labels': {'index': j},
                'metadata': {'parent_streamline': sid}
            }
            self.add_node(p_node)

            eid = f"e_geo_streamline_member:{sid}:{j}"
            e = {
                'id': eid,
                'kind': 'GEO_STREAMLINE_MEMBER',
                'nodes': [sid, pid],
                'metadata': {
                    'obs_class': 'observed',
                    'confidence': 1.0,
                    'provenance': {
                        'source': 'cesium_field',
                        'rule_id': 'R-GEO-STREAMLINE-001',
                        'evidence': [sid, pid],
                        'timestamp': time.time()
                    }
                }
            }
            self.add_edge(e)
        return sid

    def add_geo_fiber_anchor(self, cell_id: str, fiber_kind: str = "network", metadata: Dict[str, Any] = None) -> str:
        """Add a geo_fiber_anchor node attached to a cell."""
        anchor_id = f"geo_fiber_anchor:{cell_id}:{fiber_kind}"

        cell_node = self.get_node(cell_id)
        pos = cell_node.position if cell_node else None

        md_in = metadata or {}
        anchor_node = {
            'id': anchor_id,
            'kind': 'geo_fiber_anchor',
            'position': pos,
            'labels': {'cell': cell_id, 'fiber': fiber_kind},
            'metadata': {
                # keep caller metadata
                **md_in,
                # defaults if not provided
                'source': md_in.get('source', 'cesium_field'),
                'obs_class': md_in.get('obs_class', 'implied'),
                'confidence': float(md_in.get('confidence', 1.0)),
                'stack_policy': md_in.get('stack_policy', 'by_kind'),
            }
        }
        self.add_node(anchor_node)

        edge_id = f"e_cell_has_anchor:{cell_id}:{fiber_kind}"
        edge = {
            'id': edge_id,
            'kind': 'CELL_HAS_ANCHOR',
            'nodes': [cell_id, anchor_id],
            'metadata': {
                'obs_class': 'implied',
                'confidence': 1.0,
                'provenance': {
                    'source': 'cesium_field',
                    'rule_id': 'R-GEO-FIBER-ANCHOR-001',
                    'evidence': [cell_id, anchor_id],
                    'timestamp': time.time()
                }
            }
        }
        self.add_edge(edge)
        return anchor_id

    def add_geo_singularity(self, cell_id: str, kind_type: str = "vortex", metadata: Dict[str, Any] = None) -> str:
        """Add a geo_singularity node (cowlick/zero) attached to a cell."""
        singularity_id = f"geo_singularity:{cell_id}:{kind_type}"

        # Inherit position
        cell_node = self.get_node(cell_id)
        pos = cell_node.position if cell_node else None

        md_in = metadata or {}
        node = {
            'id': singularity_id,
            'kind': 'geo_singularity',
            'position': pos,
            'labels': {'cell': cell_id, 'singularity': kind_type},
            'metadata': {
                **md_in,
                'source': md_in.get('source', 'math_topology'),
                'obs_class': md_in.get('obs_class', 'inferred'),
                'confidence': float(md_in.get('confidence', 1.0))
            }
        }
        self.add_node(node)

        # Link to cell
        edge_id = f"e_singularity_in_cell:{singularity_id}"
        edge = {
            'id': edge_id,
            'kind': 'SINGULARITY_IN_CELL',
            'nodes': [singularity_id, cell_id],
            'metadata': {
                'obs_class': 'implied',
                'confidence': 1.0,
                'provenance': {
                    'source': 'math_topology',
                    'rule_id': 'R-GEO-SINGULARITY-001',
                    'evidence': [cell_id],
                    'timestamp': time.time()
                }
            }
        }
        self.add_edge(edge)
        return singularity_id


class RFHypergraphAdapter:
    """Adapter mapping RF-style dicts into HGNode/HGEdge calls."""
    def __init__(self, engine: HypergraphEngine):
        self.engine = engine

    def add_node_from_rf(self, node_data: Dict[str, Any]) -> str:
        nid = node_data.get('node_id') or f"rf_{int(time.time()*1000)}"
        node = {
            'id': nid,
            'kind': 'rf',
            'position': node_data.get('position'),
            'frequency': node_data.get('frequency'),
            'labels': node_data.get('labels', {}),
            'metadata': node_data.get('metadata', {})
        }
        return self.engine.add_node(node)

    def add_edge_from_rf(self, edge_data: Dict[str, Any]) -> str:
        eid = edge_data.get('id') or f"edge_{int(time.time()*1000)}"
        edge = {
            'id': eid,
            'kind': edge_data.get('type', 'rf_coherence'),
            'nodes': edge_data.get('nodes', []),
            'weight': float(edge_data.get('signal_strength', 0.0)),
            'labels': edge_data.get('labels', {}),
            'metadata': edge_data.get('metadata', {}),
            'timestamp': edge_data.get('timestamp', time.time())
        }
        return self.engine.add_edge(edge)


__all__ = ['HypergraphEngine', 'HGNode', 'HGEdge', 'RFHypergraphAdapter', 'PerfettoTraceEvent']
