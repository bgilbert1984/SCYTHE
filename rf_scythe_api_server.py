
# Early eventlet monkey patching must run before any other imports
# to avoid "Working outside of application/request context" errors.
# See logs for warnings when this is not the case.
try:
    import eventlet
    eventlet.monkey_patch()
    # log early patch when server starts (logging not yet configured here)
    print("[INIT] eventlet monkey patch applied")
except ImportError:
    # eventlet optional; continue if absent
    pass
import os
import sys
import json
import time
import random
import subprocess
import ipaddress
import threading
from datetime import datetime, timedelta
import jwt
import logging
import math
import numpy as np
from functools import wraps
from contextlib import contextmanager
import sqlite3
import os
import sys

# Default Ollama URL — overridable via OLLAMA_URL env var (Docker, remote inference)
_DEFAULT_OLLAMA_URL: str = os.environ.get("OLLAMA_URL", "http://localhost:11434")

# Robust import for bsg_projection: prefer package import, but allow
try:
    from NerfEngine import bsg_projection as _bp
except Exception:
    # Try importing local module when executing the file directly
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    try:
        import bsg_projection as _bp
    except Exception:
        _bp = None
from typing import Dict, List, Any, Optional, Set, Tuple
from datetime import datetime, timezone
from collections import defaultdict, deque
from types import SimpleNamespace
import urllib.request
import urllib.error
import ssl
from recon_enrichment import (
    apply_recon_actor_summary,
    build_cognition_graph_records,
    build_recon_entity_from_graph_event,
    enrich_hypergraph_rf_node,
)
from recon_network_stitching import apply_recon_network_stitch_batch

# ────────────────────────────────────────────────────────────────────
# NETWORK INGRESS AGGREGATOR
# ────────────────────────────────────────────────────────────────────
try:
    import network_ingress_aggregator
    _ingress_aggregator_available = True
except ImportError:
    logger.warning("[API] network_ingress_aggregator not available")
    _ingress_aggregator_available = False

# Safe conversion helpers (non-recursive, DTO-style)
def _safe_nd(node):
    try:
        from hypergraph_engine import HGNode, HGEdge  # local import to avoid cycles at import time
    except Exception:
        HGNode = HGEdge = ()

    if isinstance(node, HGNode):
        return {
            'id': node.id,
            'kind': node.kind,
            'position': node.position,
            'frequency': node.frequency,
            'labels': node.labels or {},
            'metadata': node.metadata or {},
            'created_at': node.created_at,
            'updated_at': node.updated_at,
        }
    if isinstance(node, HGEdge):
        return {
            'id': node.id,
            'kind': node.kind,
            'nodes': list(node.nodes) if node.nodes else [],
            'weight': node.weight,
            'labels': node.labels or {},
            'metadata': node.metadata or {},
            'timestamp': node.timestamp,
        }
    if isinstance(node, dict):
        return node
    # Restrict automatic to_dict() fallbacks to known hypergraph types only.
    if hasattr(node, 'to_dict') and type(node).__name__ in {"HGNode", "HGEdge"}:
        try:
            return node.to_dict()
        except Exception:
            return {}
    return {}

def _safe_bsg_view(nd: dict) -> dict:
    """Project a behavior_group node to a stable DTO (no recursion).

    This adaptor maps internal behavior_group node shape into the
    canonical projection input expected by `bsg_projection.safe_bsg_view`.
    """
    if not nd:
        return {}
    labels = nd.get('labels', {}) or {}
    meta = nd.get('metadata', {}) or {}

    # Build a minimal canonical group dict for projection
    group = {
        'group_id': nd.get('id'),
        'group_type': labels.get('behavior', 'UNKNOWN'),
        'confidence': labels.get('confidence', 0.0),
        'evidence_level': meta.get('evidence_level') or labels.get('evidence_level', 'STRUCTURAL'),
        'rationale': {
            'pattern': labels.get('detection_pattern') or labels.get('summary') or '',
            'interval_variance_sec': labels.get('interval_variance_sec'),
            'repetitions': labels.get('repetitions') or labels.get('member_count'),
        },
        'session_stats': {
            'session_count': labels.get('member_count', 0),
            'avg_duration_sec': labels.get('avg_duration_sec'),
            'avg_bytes_out': labels.get('avg_bytes_out'),
            'avg_bytes_in': labels.get('avg_bytes_in'),
        },
        'network_characteristics': {
            'protocols': meta.get('protocols') or labels.get('protocols') or ['TCP'],
            'dst_port_entropy': labels.get('dst_port_entropy'),
            'dst_ip_count': labels.get('unique_hosts') or labels.get('unique_ips') or None,
        },
        'temporal_bounds': {
            'first_seen': meta.get('first_seen') or labels.get('first_seen'),
            'last_seen': meta.get('last_seen') or labels.get('last_seen'),
        },
        'negative_assertions': meta.get('negative_assertions') or labels.get('negative_assertions') or [],
    }

    # Guard against recursive group references by tracking seen group_ids.
    def guarded_bsg_view(group: dict, _seen: Optional[Set[str]] = None) -> dict:
        if _seen is None:
            _seen = set()
        gid = group.get('group_id') or group.get('bsg_id')
        if gid and gid in _seen:
            return {'group_id': gid, 'error': 'recursive_reference_pruned'}
        if gid:
            _seen.add(gid)
        if not _bp:
            return {**group, 'warning': 'bsg_projection_unavailable'}
        try:
            return _bp.safe_bsg_view(group)
        except RecursionError:
            return {'group_id': gid, 'error': 'recursion_pruned'}
        except Exception:
            return {'group_id': gid, 'error': 'projection_failed'}

    return guarded_bsg_view(group)


# Nuclear JSON sanitizer: converts unknown objects into safe JSON scalars/structures.
# Includes recursion depth limit and circular reference protection.
def hard_json_clean(obj, _depth=0, _seen=None):
    if _depth > 16:
        return "[Depth Limit Exceeded]"
    if _seen is None:
        _seen = set()

    # Track complex objects to avoid circular references
    obj_id = id(obj) if obj is not None else 0
    if obj_id in _seen:
        return "[Circular Reference]"

    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj

    if isinstance(obj, dict):
        _seen.add(obj_id)
        out = {}
        for k, v in obj.items():
            try:
                out[str(k)] = hard_json_clean(v, _depth + 1, _seen)
            except Exception:
                out[str(k)] = str(v)
        return out

    if isinstance(obj, (list, tuple, set)):
        _seen.add(obj_id)
        out = []
        for v in obj:
            try:
                out.append(hard_json_clean(v, _depth + 1, _seen))
            except Exception:
                out.append(str(v))
        return out

    try:
        # For complex objects like HGNode, use their to_dict if safe, or str()
        if hasattr(obj, 'to_dict'):
            _seen.add(obj_id)
            return hard_json_clean(obj.to_dict(), _depth + 1, _seen)
        return str(obj)
    except Exception:
        return None

# Try to import scipy for spatial indexing
try:
    from scipy.spatial import cKDTree
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False
    logger_temp = logging.getLogger(__name__)
    logger_temp.warning("scipy not available - spatial indexing disabled. Install with: pip install scipy")

# Try to import sklearn for advanced spatial queries
try:
    from sklearn.neighbors import BallTree
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

# Check for Flask availability
try:
    from flask import Flask, request, jsonify, send_from_directory, Response, make_response, has_request_context
    from flask_cors import CORS
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False
    print("Flask not installed. Install with: pip install flask flask-cors")

# Check for Flask-SocketIO availability
try:
    from flask_socketio import SocketIO, emit, join_room, leave_room, disconnect
    SOCKETIO_AVAILABLE = True
except ImportError:
    SOCKETIO_AVAILABLE = False
    print("Flask-SocketIO not installed. WebSocket support disabled. Install with: pip install flask-socketio")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('rf_scythe_server')

# ── Module-level UAV registry (route handlers use `global`, so this MUST ──────
# ── be at module scope — not inside register_routes() which is a closure) ─────
_uav_registry: dict = {}   # uav_id → {lat, lon, alt, color, label, speedKmh, last_seen}
_uav_hits:     list = []   # [{uav_id, shooter_id, timestamp, lat, lon}]
_uav_lock = threading.Lock()  # guards both _uav_registry and _uav_hits

# ============================================================================
# PER-INSTANCE DATA DIRECTORY — Sovereign Storage
# ============================================================================
# Default: "metrics_logs" (backward compat). Overridden by --data-dir at startup.
_SCYTHE_DATA_DIR = os.environ.get('SCYTHE_DATA_DIR', 'metrics_logs')

def _data_dir() -> str:
    """Return the per-instance data directory.
    Prefers app.config['SCYTHE_DATA_DIR'] if the Flask app is configured,
    otherwise falls back to the module-level default."""
    try:
        return app.config.get('SCYTHE_DATA_DIR', _SCYTHE_DATA_DIR)
    except Exception:
        return _SCYTHE_DATA_DIR


def _register_session_with_orchestrator(session) -> None:
    """Register a newly created operator session with the orchestrator synchronously.

    Called after successful login so the gRPC TokenAuthInterceptor can validate
    the token via GET /api/scythe/sessions/validate.  Runs synchronously with a
    short timeout so the caller can return a token that is immediately usable for
    gRPC calls — avoiding the post-login UNAUTHENTICATED race.

    Non-fatal: if the orchestrator is unreachable the session still works for
    instance-local HTTP auth; gRPC calls against this instance will fail auth
    until connectivity is restored.
    """
    import requests as _requests

    orch_url = app.config.get('ORCHESTRATOR_URL', '')
    internal_token = app.config.get('INTERNAL_TOKEN', '')
    instance_id = app.config.get('SCYTHE_INSTANCE_ID', '')

    if not orch_url or not internal_token:
        return

    try:
        _requests.post(
            f'{orch_url}/api/scythe/sessions/register',
            json={
                'token': session.session_token,
                'instance_id': instance_id,
                'operator_id': session.operator_id,
                'expires_at': session.expires_at,
            },
            headers={'X-Internal-Token': internal_token},
            timeout=2.0,
        )
    except Exception:
        pass  # non-fatal — token still valid for instance-local HTTP auth


def _revoke_session_with_orchestrator(token: str) -> None:
    """Fire-and-forget: remove a session from the orchestrator's shared registry on logout."""
    import threading as _threading
    import requests as _requests

    orch_url = app.config.get('ORCHESTRATOR_URL', '')
    internal_token = app.config.get('INTERNAL_TOKEN', '')

    if not orch_url or not internal_token or not token:
        return

    def _post():
        try:
            _requests.post(
                f'{orch_url}/api/scythe/sessions/revoke',
                json={'token': token},
                headers={'X-Internal-Token': internal_token},
                timeout=2.0,
            )
        except Exception:
            pass  # non-fatal

    _threading.Thread(target=_post, daemon=True).start()



# ============================================================================
# RF SCYTHE REGISTRIES (Globals)
# ============================================================================
detection_registry = None  # Populated at startup
pcap_registry_instance = None
sensor_registry_instance = None
writebus_instance = None

# ============================================================================
# RF HYPERGRAPH DATA STORAGE
# ============================================================================

class RFHypergraphStore:
    """In-memory storage for RF hypergraph data"""

    def __init__(self):
        self.reset()

    def reset(self):
        """Reset all data"""
        self.session_id = f"session_{int(time.time())}"
        self.nodes = {}
        self.hyperedges = []
        self.start_time = time.monotonic()
        logger.info(f"Hypergraph session reset: {self.session_id}")

    def add_node(self, node_data: Dict[str, Any]) -> str:
        """Add an RF node"""
        node_id = node_data.get('node_id') or f"rf_node_{len(self.nodes)}_{int(time.time()*1000)}"
        metadata = dict(node_data.get('metadata') or {})
        for key in (
            'type', 'source', 'observer_id', 'platform', 'ssid', 'bssid', 'rssi',
            'frequency_mhz', 'channel_width', 'name', 'address', 'tx_power_dbm',
            'timestamp', 'accuracy_m'
        ):
            value = node_data.get(key)
            if value is not None:
                metadata[key] = value

        position = node_data.get('position')
        if not isinstance(position, (list, tuple)) or len(position) < 2:
            lat = node_data.get('lat')
            lon = node_data.get('lon')
            if lat is not None and lon is not None:
                try:
                    position = [
                        float(lat),
                        float(lon),
                        float(node_data.get('alt', node_data.get('alt_m', 0.0)) or 0.0),
                    ]
                except Exception:
                    position = [0, 0, 0]
            else:
                position = [0, 0, 0]

        node_kind = node_data.get('type') or metadata.get('type') or 'rf'
        node_labels = {}
        try:
            enriched = enrich_hypergraph_rf_node(
                node_id,
                node_data,
                metadata=metadata,
                position=position,
            )
            node_id = enriched.get('node_id') or node_id
            metadata = dict(enriched.get('metadata') or metadata)
            node_labels = dict(enriched.get('labels') or {})
            node_kind = enriched.get('kind') or node_kind
        except Exception:
            pass
        self.nodes[node_id] = {
            'node_id': node_id,
            'position': position,
            'frequency': node_data.get('frequency', node_data.get('frequency_mhz', 0)),
            'power': node_data.get('power', node_data.get('rssi', -80)),
            'modulation': node_data.get('modulation', metadata.get('technology', 'Unknown')),
            'timestamp': time.time(),
            'metadata': metadata,
            'labels': node_labels,
        }
        # publish node create event
        try:
            self._maybe_publish_node_create(node_id, self.nodes[node_id])
        except Exception:
            pass
        # mirror into attached HypergraphEngine (unified node model)
        try:
            engine = getattr(self, 'hypergraph_engine', None)
            if engine:
                eng_node = {
                    'id': node_id,
                    'kind': node_kind,
                    'position': self.nodes[node_id].get('position'),
                    'frequency': self.nodes[node_id].get('frequency'),
                    'labels': self.nodes[node_id].get('labels', {}),
                    'metadata': self.nodes[node_id].get('metadata', {})
                }
                engine.add_node(eng_node)
        except Exception:
            pass
        # Mirror into unified HypergraphEngine via adapter if available
        try:
            adapter = getattr(self, 'rf_adapter', None)
            if adapter:
                adapter.add_node_from_rf(self.nodes[node_id])
            else:
                engine = getattr(self, 'hypergraph_engine', None)
                if engine:
                    eng_node = {
                        'id': node_id,
                        'kind': node_kind,
                        'position': self.nodes[node_id].get('position'),
                        'frequency': self.nodes[node_id].get('frequency'),
                        'labels': self.nodes[node_id].get('labels', {}),
                        'metadata': self.nodes[node_id].get('metadata', {})
                    }
                    try:
                        engine.add_node(eng_node)
                    except Exception:
                        pass
        except Exception:
            pass

        try:
            self._maybe_materialize_cognition_schema(node_id, self.nodes[node_id])
        except Exception:
            pass

        return node_id

    def _maybe_publish_node_create(self, node_id: str, node_record: Dict[str, Any]):
        if getattr(self, 'event_bus', None):
            try:
                ev = SimpleNamespace(
                    event_type='NODE_CREATE',
                    entity_id=node_id,
                    entity_kind='rf_node',
                    entity_data=node_record
                )
                self.event_bus.publish(ev)
            except Exception:
                pass

    def _maybe_materialize_cognition_schema(self, node_id: str, node_record: Dict[str, Any]) -> None:
        engine = getattr(self, 'hypergraph_engine', None)
        if engine is None:
            adapter = getattr(self, 'rf_adapter', None)
            engine = getattr(adapter, 'engine', None)
        if engine is None:
            return

        cognition = build_cognition_graph_records(node_id, node_record.get('metadata') or {})
        for companion_node in cognition.get('nodes', []):
            try:
                engine.add_node(companion_node)
            except Exception:
                pass
        for companion_edge in cognition.get('edges', []):
            try:
                engine.add_edge(companion_edge)
            except Exception:
                pass

    def add_hyperedge(self, edge_data: Dict[str, Any]) -> int:
        """Add a hyperedge"""
        edge = {
            'nodes': edge_data.get('nodes', []),
            'cardinality': len(edge_data.get('nodes', [])),
            'signal_strength': edge_data.get('signal_strength', -70),
            'timestamp': time.time(),
            'metadata': edge_data.get('metadata', {})
        }
        self.hyperedges.append(edge)
        # publish hyperedge create
        try:
            if getattr(self, 'event_bus', None):
                ev = SimpleNamespace(
                    event_type='HYPEREDGE_CREATE',
                    entity_id=str(len(self.hyperedges)-1),
                    entity_kind='hyperedge',
                    entity_data=edge
                )
                self.event_bus.publish(ev)
        except Exception:
            pass
        # mirror into unified HypergraphEngine via adapter if available
        try:
            adapter = getattr(self, 'rf_adapter', None)
            if adapter:
                adapter.add_edge_from_rf(edge)
            else:
                engine = getattr(self, 'hypergraph_engine', None)
                if engine:
                    eng_edge = {
                        'id': str(len(self.hyperedges)-1),
                        'kind': edge.get('type') or 'rf_coherence',
                        'nodes': edge.get('nodes', []),
                        'weight': float(edge.get('signal_strength', 0.0)),
                        'labels': {},
                        'metadata': edge.get('metadata', {}),
                        'timestamp': edge.get('timestamp', time.time())
                    }
                    try:
                        engine.add_edge(eng_edge)
                    except Exception:
                        pass
        except Exception:
            pass
        return len(self.hyperedges) - 1

    def get_visualization_data(self) -> Dict[str, Any]:
        """Get data formatted for visualization"""
        nodes_list = list(self.nodes.values())

        # Calculate centrality (simple degree-based)
        centrality = defaultdict(int)
        for edge in self.hyperedges:
            for node_id in edge.get('nodes', []):
                centrality[node_id] += 1

        # Get top central nodes
        central_nodes = sorted(
            [(nid, centrality[nid]) for nid in self.nodes.keys()],
            key=lambda x: x[1],
            reverse=True
        )[:5]

        return {
            'nodes': nodes_list,
            'hyperedges': self.hyperedges,
            'central_nodes': [
                {
                    'node_id': nid,
                    'centrality': cent / max(len(self.hyperedges), 1),
                    'frequency': self.nodes.get(nid, {}).get('frequency', 0)
                }
                for nid, cent in central_nodes
            ],
            'session_id': self.session_id,
            'timestamp': time.time()
        }

    def get_metrics(self) -> Dict[str, Any]:
        """Get hypergraph metrics"""
        # Calculate frequency distribution
        freq_dist = defaultdict(int)
        for node in self.nodes.values():
            freq = node.get('frequency', 0)
            band = f"{int(freq // 10) * 10}-{int(freq // 10) * 10 + 10}"
            freq_dist[band] += 1

        # Calculate centrality for high centrality nodes
        centrality = defaultdict(int)
        for edge in self.hyperedges:
            for node_id in edge.get('nodes', []):
                centrality[node_id] += 1

        high_cent_nodes = sorted(
            [(nid, centrality[nid]) for nid in self.nodes.keys()],
            key=lambda x: x[1],
            reverse=True
        )[:5]

        return {
            'total_nodes': len(self.nodes),
            'total_hyperedges': len(self.hyperedges),
            'session_id': self.session_id,
            'collection_duration': time.monotonic() - self.start_time,
            'frequency_distribution': dict(freq_dist),
            'high_centrality_nodes': [
                {
                    'node_id': nid,
                    'centrality': cent / max(len(self.hyperedges), 1),
                    'frequency': self.nodes.get(nid, {}).get('frequency', 0)
                }
                for nid, cent in high_cent_nodes
            ]
        }

    def generate_test_data(self, num_nodes: int = 20, freq_min: float = 88.0,
                          freq_max: float = 108.0, area_size: float = 1000.0) -> Dict[str, Any]:
        """Generate synthetic test data"""
        # Clear existing data but keep session
        self.nodes = {}
        self.hyperedges = []

        # Base location (San Francisco)
        base_lat, base_lon = 37.7749, -122.4194

        # Generate nodes
        modulations = ['FM', 'AM', 'PSK', 'FSK', 'QAM', 'OFDM']

        for i in range(num_nodes):
            lat_offset = (random.random() - 0.5) * (area_size / 111000)  # Convert meters to degrees
            lon_offset = (random.random() - 0.5) * (area_size / 111000)

            node_data = {
                'node_id': f"rf_node_{i}_{int(time.time()*1000)}",
                'position': [
                    base_lat + lat_offset,
                    base_lon + lon_offset,
                    random.random() * 500  # altitude 0-500m
                ],
                'frequency': freq_min + random.random() * (freq_max - freq_min),
                'power': -80 + random.random() * 50,  # -80 to -30 dBm
                'modulation': random.choice(modulations),
                'metadata': {
                    'source': 'test_generator',
                    'generated_at': time.time()
                }
            }
            self.add_node(node_data)

        # Generate hyperedges
        node_ids = list(self.nodes.keys())
        num_edges = min(num_nodes * 2, 30)

        for _ in range(num_edges):
            cardinality = random.randint(2, min(5, len(node_ids)))
            edge_nodes = random.sample(node_ids, cardinality)

            edge_data = {
                'nodes': edge_nodes,
                'signal_strength': -80 + random.random() * 50,
                'metadata': {
                    'coherence': random.random(),
                    'generated': True
                }
            }
            self.add_hyperedge(edge_data)

        logger.info(f"Generated {num_nodes} nodes and {num_edges} hyperedges")
        return self.get_visualization_data()

    def add_network_host(self, host_data: Dict[str, Any]) -> str:
        """Add a network host as a hypergraph node"""
        ip = host_data.get('ip', '0.0.0.0')
        node_id = f"net_{ip.replace('.', '_').replace(':', '_')}"

        # Convert IP to pseudo-position (for visualization) — IPv4 and IPv6 safe
        try:
            _addr = ipaddress.ip_address(ip)
            _int = int(_addr)
            if _addr.version == 6:
                lat = 37.0 + ((_int >> 16) & 0xFF) * 0.01 - 1.28
                lon = -122.0 + (_int & 0xFF) * 0.01 - 1.28
            else:
                ip_parts = [int(x) for x in ip.split('.')]
                lat = 37.0 + (ip_parts[2] - 128) * 0.01
                lon = -122.0 + (ip_parts[3] - 128) * 0.01
        except ValueError:
            lat, lon = 37.0, -122.0

        self.nodes[node_id] = {
            'node_id': node_id,
            'type': 'network_host',
            'ip': ip,
            'hostname': host_data.get('hostname', ip),
            'position': [lat, lon, 0],
            'ports': host_data.get('ports', []),
            'services': [p.get('service', 'unknown') for p in host_data.get('ports', [])],
            'frequency': len(host_data.get('ports', [])) * 100,  # Pseudo-frequency based on ports
            'power': -50 + len(host_data.get('ports', [])) * 5,  # Signal strength based on activity
            'modulation': 'TCP/IP',
            'timestamp': time.time(),
            'metadata': {
                'source': 'nmap',
                'status': host_data.get('status', 'up'),
                'mac': host_data.get('mac'),
                'os': host_data.get('os')
            }
        }
        # publish network host create
        try:
            self._maybe_publish_network_host(node_id, self.nodes[node_id])
        except Exception:
            pass
        # mirror into unified HypergraphEngine via adapter if available
        try:
            adapter = getattr(self, 'rf_adapter', None)
            if adapter:
                adapter.add_node_from_rf(self.nodes[node_id])
            else:
                engine = getattr(self, 'hypergraph_engine', None)
                if engine:
                    services = [p.get('service') for p in self.nodes[node_id].get('ports', []) if p.get('service')]
                    subnet = None
                    try:
                        subnet = '.'.join(ip.split('.')[:3]) + '.0/24'
                    except Exception:
                        subnet = None
                    labels = {}
                    if services:
                        labels['service'] = services
                    if subnet:
                        labels['subnet'] = subnet
                    eng_node = {
                        'id': node_id,
                        'kind': 'network_host',
                        'position': self.nodes[node_id].get('position'),
                        'frequency': self.nodes[node_id].get('frequency'),
                        'labels': labels,
                        'metadata': self.nodes[node_id].get('metadata', {})
                    }
                    try:
                        engine.add_node(eng_node)
                    except Exception:
                        pass
        except Exception:
            pass

        try:
            self._maybe_materialize_cognition_schema(node_id, self.nodes[node_id])
        except Exception:
            pass

        return node_id

    def _maybe_publish_network_host(self, node_id: str, host_record: Dict[str, Any]):
        if getattr(self, 'event_bus', None):
            try:
                ev = SimpleNamespace(
                    event_type='NODE_CREATE',
                    entity_id=node_id,
                    entity_kind='network_host',
                    entity_data=host_record
                )
                self.event_bus.publish(ev)
            except Exception:
                pass

    def create_service_hyperedges(self):
        """Create hyperedges connecting hosts with same services"""
        # Group nodes by service
        service_groups = defaultdict(list)
        for node_id, node in self.nodes.items():
            if node.get('type') == 'network_host':
                for service in node.get('services', []):
                    if service and service != 'unknown':
                        service_groups[service].append(node_id)

        # Create hyperedges for each service group
        for service, node_ids in service_groups.items():
            if len(node_ids) >= 2:
                edge = {
                    'nodes': node_ids,
                    'cardinality': len(node_ids),
                    'type': 'service_group',
                    'service': service,
                    'signal_strength': -60 + len(node_ids) * 2,
                    'timestamp': time.time(),
                    'metadata': {
                        'relationship': f'shared_{service}_service',
                        'description': f'Hosts running {service}'
                    }
                }
                self.hyperedges.append(edge)
                try:
                    if getattr(self, 'event_bus', None):
                        ev = SimpleNamespace(
                            event_type='HYPEREDGE_CREATE',
                            entity_id=str(len(self.hyperedges)-1),
                            entity_kind='hyperedge',
                            entity_data=edge
                        )
                        self.event_bus.publish(ev)
                except Exception:
                    pass
                # mirror into HypergraphEngine
                try:
                    adapter = getattr(self, 'rf_adapter', None)
                    if adapter:
                        adapter.add_edge_from_rf(edge)
                    else:
                        engine = getattr(self, 'hypergraph_engine', None)
                        if engine:
                            eng_edge = {
                                'id': str(len(self.hyperedges)-1),
                                'kind': 'service_group',
                                'nodes': node_ids,
                                'weight': float(edge.get('signal_strength', 0.0)),
                                'labels': {'service': service},
                                'metadata': edge.get('metadata', {}),
                                'timestamp': edge.get('timestamp')
                            }
                            try:
                                engine.add_edge(eng_edge)
                            except Exception:
                                pass
                except Exception:
                    pass

        return len(service_groups)

    def create_subnet_hyperedges(self):
        """Create hyperedges connecting hosts in same subnet"""
        # Group nodes by /24 subnet
        subnet_groups = defaultdict(list)
        for node_id, node in self.nodes.items():
            if node.get('type') == 'network_host':
                ip = node.get('ip', '')
                if ip:
                    subnet = '.'.join(ip.split('.')[:3])
                    subnet_groups[subnet].append(node_id)

        # Create hyperedges for each subnet
        for subnet, node_ids in subnet_groups.items():
            if len(node_ids) >= 2:
                edge = {
                    'nodes': node_ids,
                    'cardinality': len(node_ids),
                    'type': 'subnet_group',
                    'subnet': f'{subnet}.0/24',
                    'signal_strength': -50 + len(node_ids) * 3,
                    'timestamp': time.time(),
                    'metadata': {
                        'relationship': 'same_subnet',
                        'description': f'Hosts in subnet {subnet}.0/24'
                    }
                }
                self.hyperedges.append(edge)
                try:
                    if getattr(self, 'event_bus', None):
                        ev = SimpleNamespace(
                            event_type='HYPEREDGE_CREATE',
                            entity_id=str(len(self.hyperedges)-1),
                            entity_kind='hyperedge',
                            entity_data=edge
                        )
                        self.event_bus.publish(ev)
                except Exception:
                    pass
                # mirror into HypergraphEngine
                try:
                    adapter = getattr(self, 'rf_adapter', None)
                    if adapter:
                        adapter.add_edge_from_rf(edge)
                    else:
                        engine = getattr(self, 'hypergraph_engine', None)
                        if engine:
                            eng_edge = {
                                'id': str(len(self.hyperedges)-1),
                                'kind': 'subnet_group',
                                'nodes': node_ids,
                                'weight': float(edge.get('signal_strength', 0.0)),
                                'labels': {'subnet': edge.get('subnet')},
                                'metadata': edge.get('metadata', {}),
                                'timestamp': edge.get('timestamp')
                            }
                            try:
                                engine.add_edge(eng_edge)
                            except Exception:
                                pass
                except Exception:
                    pass

        return len(subnet_groups)


# ============================================================================
# NMAP INTEGRATION
# ============================================================================

class NmapScanner:
    """Nmap network scanner integration"""

    def __init__(self):
        self.scan_results = {}
        self.scanning = False
        self.last_scan_time = None

    def check_nmap_available(self) -> bool:
        """Check if nmap is installed"""
        try:
            result = subprocess.run(['which', 'nmap'], capture_output=True, text=True)
            return result.returncode == 0
        except Exception:
            return False

    def scan(self, target: str, options: str = '-sn') -> Dict[str, Any]:
        """Run an nmap scan"""
        if not self.check_nmap_available():
            return {
                'status': 'simulated',
                'message': 'nmap not installed. Install with: sudo dnf install nmap',
                'simulated': True,
                'results': self._generate_simulated_results(target)
            }

        self.scanning = True
        try:
            # Build command - restrict to safe options; add -6 for IPv6 targets
            safe_options = options.replace(';', '').replace('|', '').replace('&', '')
            try:
                _ipv6_flag = ['-6'] if ipaddress.ip_address(target).version == 6 else []
            except ValueError:
                _ipv6_flag = []
            cmd = ['nmap'] + _ipv6_flag + safe_options.split() + [target]

            logger.info(f"Running nmap: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

            self.scan_results = {
                'status': 'success',
                'target': target,
                'options': safe_options,
                'output': result.stdout,
                'timestamp': time.time(),
                'hosts': self._parse_nmap_output(result.stdout)
            }
            self.last_scan_time = time.time()

        except subprocess.TimeoutExpired:
            self.scan_results = {
                'status': 'error',
                'message': 'Scan timed out after 120 seconds'
            }
        except Exception as e:
            self.scan_results = {
                'status': 'error',
                'message': str(e)
            }
        finally:
            self.scanning = False

        return self.scan_results

    def _parse_nmap_output(self, output: str) -> List[Dict[str, Any]]:
        """Parse nmap output into structured data"""
        hosts = []
        current_host = None

        for line in output.split('\n'):
            if 'Nmap scan report for' in line:
                if current_host:
                    hosts.append(current_host)
                parts = line.split()
                ip = parts[-1].strip('()')
                hostname = parts[-2] if len(parts) > 4 else ip
                current_host = {
                    'ip': ip,
                    'hostname': hostname,
                    'ports': [],
                    'status': 'up'
                }
            elif current_host and '/tcp' in line or '/udp' in line:
                parts = line.split()
                if len(parts) >= 3:
                    current_host['ports'].append({
                        'port': parts[0],
                        'state': parts[1],
                        'service': parts[2] if len(parts) > 2 else 'unknown'
                    })

        if current_host:
            hosts.append(current_host)

        return hosts

    def _generate_simulated_results(self, target: str) -> List[Dict[str, Any]]:
        """Generate simulated scan results when nmap is not available"""
        # Parse target for simulation
        if '/' in target:
            base_ip = target.split('/')[0]
        else:
            base_ip = target

        base_parts = base_ip.split('.')[:3]

        hosts = []
        for i in range(random.randint(3, 10)):
            host_ip = f"{'.'.join(base_parts)}.{random.randint(1, 254)}"
            hosts.append({
                'ip': host_ip,
                'hostname': f"host-{host_ip.replace('.', '-')}",
                'status': 'up',
                'ports': [
                    {'port': '22/tcp', 'state': 'open', 'service': 'ssh'},
                    {'port': '80/tcp', 'state': 'open', 'service': 'http'},
                ] if random.random() > 0.5 else []
            })

        return hosts


# ============================================================================
# NDPI INTEGRATION
# ============================================================================

class NDPIAnalyzer:
    """nDPI deep packet inspection integration"""

    def __init__(self):
        self.analysis_results = {}
        self.analyzing = False

    def check_ndpi_available(self) -> bool:
        """Check if ndpiReader is installed"""
        try:
            result = subprocess.run(['which', 'ndpiReader'], capture_output=True, text=True)
            return result.returncode == 0
        except Exception:
            return False

    def analyze_interface(self, interface: str = 'eth0', duration: int = 10) -> Dict[str, Any]:
        """Analyze network traffic on an interface"""
        if not self.check_ndpi_available():
            return {
                'status': 'simulated',
                'message': 'ndpiReader not installed. Install nDPI for real analysis.',
                'results': self._generate_simulated_results()
            }

        self.analyzing = True
        try:
            cmd = ['ndpiReader', '-i', interface, '-s', str(duration)]

            logger.info(f"Running nDPI: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=duration + 30)

            self.analysis_results = {
                'status': 'success',
                'interface': interface,
                'duration': duration,
                'output': result.stdout,
                'protocols': self._parse_ndpi_output(result.stdout),
                'timestamp': time.time()
            }

        except subprocess.TimeoutExpired:
            self.analysis_results = {
                'status': 'error',
                'message': 'Analysis timed out'
            }
        except Exception as e:
            self.analysis_results = {
                'status': 'error',
                'message': str(e)
            }
        finally:
            self.analyzing = False

        return self.analysis_results

    def _parse_ndpi_output(self, output: str) -> Dict[str, Any]:
        """Parse nDPI output into structured data"""
        result = {
            'protocols': [],
            'categories': [],
            'statistics': {},
            'risks': []
        }

        # Find the "Detected protocols:" section
        in_protocols = False
        in_categories = False
        in_risks = False

        for line in output.split('\n'):
            line = line.strip()

            # Section markers
            if 'Detected protocols:' in line:
                in_protocols = True
                in_categories = False
                in_risks = False
                continue
            elif 'Category statistics:' in line:
                in_protocols = False
                in_categories = True
                in_risks = False
                continue
            elif 'Risk stats' in line:
                in_protocols = False
                in_categories = False
                in_risks = True
                continue
            elif line.startswith('Protocol statistics:') or line.startswith('NOTE:'):
                in_protocols = False
                in_categories = False
                continue

            # Parse detected protocols (format: "TLS packets: 79 bytes: 17278 flows: 4")
            if in_protocols and line and not line.startswith('*'):
                # Try to parse: PROTOCOL packets: N bytes: N flows: N
                import re
                match = re.match(r'(\S+)\s+packets:\s*(\d+)\s+bytes:\s*(\d+)\s+flows:\s*(\d+)', line)
                if match:
                    result['protocols'].append({
                        'protocol': match.group(1),
                        'packets': int(match.group(2)),
                        'bytes': int(match.group(3)),
                        'flows': int(match.group(4))
                    })

            # Parse categories (format: "Web packets: 80 bytes: 20357 flows: 8")
            elif in_categories and line and not line.startswith('*'):
                import re
                match = re.match(r'(\S+)\s+packets:\s*(\d+)\s+bytes:\s*(\d+)\s+flows:\s*(\d+)', line)
                if match:
                    result['categories'].append({
                        'category': match.group(1),
                        'packets': int(match.group(2)),
                        'bytes': int(match.group(3)),
                        'flows': int(match.group(4))
                    })

            # Parse key statistics
            if 'IP packets:' in line:
                import re
                match = re.search(r'IP packets:\s*(\d+)', line)
                if match:
                    result['statistics']['ip_packets'] = int(match.group(1))
            elif 'Unique flows:' in line:
                import re
                match = re.search(r'Unique flows:\s*(\d+)', line)
                if match:
                    result['statistics']['unique_flows'] = int(match.group(1))
            elif 'TCP Packets:' in line:
                import re
                match = re.search(r'TCP Packets:\s*(\d+)', line)
                if match:
                    result['statistics']['tcp_packets'] = int(match.group(1))
            elif 'UDP Packets:' in line:
                import re
                match = re.search(r'UDP Packets:\s*(\d+)', line)
                if match:
                    result['statistics']['udp_packets'] = int(match.group(1))
            elif 'nDPI throughput:' in line:
                import re
                match = re.search(r'nDPI throughput:\s*([\d.]+)\s*pps\s*/\s*([\d.]+)\s*(\S+)/sec', line)
                if match:
                    result['statistics']['throughput_pps'] = float(match.group(1))
                    result['statistics']['throughput_rate'] = f"{match.group(2)} {match.group(3)}/sec"

        return result

    def _generate_simulated_results(self) -> Dict[str, Any]:
        """Generate simulated NDPI results"""
        protocols = [
            {'protocol': 'TLS', 'count': random.randint(100, 500), 'bytes': random.randint(50000, 200000), 'category': 'Encrypted'},
            {'protocol': 'HTTP', 'count': random.randint(50, 200), 'bytes': random.randint(20000, 100000), 'category': 'Web'},
            {'protocol': 'DNS', 'count': random.randint(200, 800), 'bytes': random.randint(10000, 50000), 'category': 'Network'},
            {'protocol': 'QUIC', 'count': random.randint(20, 100), 'bytes': random.randint(10000, 80000), 'category': 'Encrypted'},
            {'protocol': 'SSH', 'count': random.randint(5, 30), 'bytes': random.randint(5000, 30000), 'category': 'Remote Access'},
            {'protocol': 'NTP', 'count': random.randint(10, 50), 'bytes': random.randint(1000, 5000), 'category': 'Network'},
            {'protocol': 'Unknown', 'count': random.randint(10, 100), 'bytes': random.randint(5000, 50000), 'category': 'Unknown'},
        ]

        return {
            'protocols': protocols,
            'total_flows': sum(p['count'] for p in protocols),
            'total_bytes': sum(p['bytes'] for p in protocols),
            'duration': 10,
            'categories': {
                'Encrypted': sum(p['count'] for p in protocols if p['category'] == 'Encrypted'),
                'Web': sum(p['count'] for p in protocols if p['category'] == 'Web'),
                'Network': sum(p['count'] for p in protocols if p['category'] == 'Network'),
                'Unknown': sum(p['count'] for p in protocols if p['category'] == 'Unknown'),
            }
        }


# ============================================================================
# AIS VESSEL TRACKING
# ============================================================================

class AISTracker:
    """AIS Vessel Tracking from CSV data"""

    # Path to AIS CSV file
    AIS_CSV_PATH = 'assets/sample-app-ais-integration-rest-master/var/ais_vessels.csv'

    def __init__(self):
        self.vessels = {}  # MMSI -> vessel data
        self.vessel_history = {}  # MMSI -> list of positions
        self.csv_loaded = False
        self.playback_index = {}  # MMSI -> current index in history
        self.all_records = []  # All CSV records
        self.load_csv()

    def load_csv(self):
        """Load AIS data from CSV file"""
        try:
            csv_path = os.path.join(os.path.dirname(__file__), self.AIS_CSV_PATH)
            if not os.path.exists(csv_path):
                # Try alternate path
                csv_path = self.AIS_CSV_PATH

            if not os.path.exists(csv_path):
                logger.warning(f"AIS CSV not found at {csv_path}")
                self._generate_mock_data()
                return

            with open(csv_path, 'r') as f:
                import csv
                reader = csv.DictReader(f)

                for row in reader:
                    self.all_records.append(row)
                    mmsi = row.get('MMSI', '')

                    if mmsi not in self.vessel_history:
                        self.vessel_history[mmsi] = []
                        self.playback_index[mmsi] = 0

                    self.vessel_history[mmsi].append({
                        'mmsi': mmsi,
                        'lat': float(row.get('LAT', 0)),
                        'lon': float(row.get('LON', 0)),
                        'sog': float(row.get('SOG', 0)),  # Speed over ground
                        'cog': float(row.get('COG', 0)),  # Course over ground
                        'heading': float(row.get('Heading', 0)),
                        'name': row.get('VesselName', 'Unknown'),
                        'vessel_type': row.get('VesselType', '0'),
                        'length': float(row.get('Length', 0) or 0),
                        'width': float(row.get('Width', 0) or 0),
                        'draft': float(row.get('Draft', 0) or 0),
                        'timestamp': row.get('BaseDateTime', '')
                    })

                # Initialize current vessel positions with first record
                for mmsi, history in self.vessel_history.items():
                    if history:
                        self.vessels[mmsi] = history[0].copy()
                        self.vessels[mmsi]['history_length'] = len(history)

                self.csv_loaded = True
                logger.info(f"Loaded {len(self.all_records)} AIS records for {len(self.vessels)} vessels")

        except Exception as e:
            logger.error(f"Error loading AIS CSV: {e}")
            self._generate_mock_data()

    def _generate_mock_data(self):
        """Generate mock AIS data if CSV not available"""
        logger.info("Generating mock AIS data")

        mock_vessels = [
            {'mmsi': '730156067', 'name': 'RM SEA TROUT', 'lat': 40.42, 'lon': -124.94, 'type': 'Fishing'},
            {'mmsi': '368179250', 'name': 'SEAHAWK', 'lat': 25.77, 'lon': -80.15, 'type': 'Patrol'},
            {'mmsi': '368138010', 'name': 'NEW YORK', 'lat': 40.46, 'lon': -73.83, 'type': 'Ferry'},
            {'mmsi': '367241000', 'name': 'ATLANTIS', 'lat': 41.88, 'lon': -125.07, 'type': 'Research'},
            {'mmsi': '367796610', 'name': 'HOUSTON', 'lat': 29.30, 'lon': -94.59, 'type': 'Cargo'},
            {'mmsi': '368024740', 'name': 'PILOT BOAT ORION', 'lat': 33.74, 'lon': -118.17, 'type': 'Pilot'},
            {'mmsi': '368126190', 'name': 'GERONIMO', 'lat': 34.23, 'lon': -121.24, 'type': 'Yacht'},
            {'mmsi': '367458840', 'name': 'OSPREY', 'lat': 25.76, 'lon': -80.14, 'type': 'Patrol'},
        ]

        for vessel in mock_vessels:
            mmsi = vessel['mmsi']
            self.vessels[mmsi] = {
                'mmsi': mmsi,
                'lat': vessel['lat'],
                'lon': vessel['lon'],
                'sog': random.uniform(0, 15),
                'cog': random.uniform(0, 360),
                'heading': random.uniform(0, 360),
                'name': vessel['name'],
                'vessel_type': vessel['type'],
                'length': random.uniform(20, 100),
                'width': random.uniform(5, 20),
                'draft': random.uniform(2, 10),
                'timestamp': datetime.now().isoformat(),
                'history_length': 1
            }
            self.vessel_history[mmsi] = [self.vessels[mmsi].copy()]
            self.playback_index[mmsi] = 0

        self.csv_loaded = True

    def get_all_vessels(self) -> List[Dict[str, Any]]:
        """Get all current vessel positions"""
        return list(self.vessels.values())

    def get_vessel(self, mmsi: str) -> Optional[Dict[str, Any]]:
        """Get a specific vessel by MMSI"""
        return self.vessels.get(mmsi)

    def get_vessel_history(self, mmsi: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Get historical positions for a vessel"""
        history = self.vessel_history.get(mmsi, [])
        return history[-limit:] if limit else history

    def advance_playback(self) -> Dict[str, Any]:
        """Advance all vessels to their next position (for simulation)"""
        updated = []

        for mmsi in self.vessels:
            history = self.vessel_history.get(mmsi, [])
            if not history:
                continue

            # Advance index
            idx = self.playback_index.get(mmsi, 0)
            idx = (idx + 1) % len(history)
            self.playback_index[mmsi] = idx

            # Update current position
            self.vessels[mmsi] = history[idx].copy()
            self.vessels[mmsi]['history_length'] = len(history)
            updated.append(self.vessels[mmsi])

        return {
            'updated_count': len(updated),
            'vessels': updated,
            'timestamp': time.time()
        }

    def get_vessels_in_area(self, min_lat: float, max_lat: float,
                           min_lon: float, max_lon: float) -> List[Dict[str, Any]]:
        """Get vessels within a geographic bounding box"""
        return [
            v for v in self.vessels.values()
            if min_lat <= v['lat'] <= max_lat and min_lon <= v['lon'] <= max_lon
        ]

    def correlate_with_rf(self, freq_min: float = 156.0, freq_max: float = 162.5) -> List[Dict[str, Any]]:
        """Correlate vessels with RF signals in maritime VHF band"""
        correlations = []

        for mmsi, vessel in self.vessels.items():
            # Simulate RF correlation - in real system this would check actual RF data
            has_rf_emission = random.random() > 0.7

            if has_rf_emission:
                correlations.append({
                    'mmsi': mmsi,
                    'vessel_name': vessel['name'],
                    'lat': vessel['lat'],
                    'lon': vessel['lon'],
                    'rf_detected': True,
                    'frequency': random.uniform(freq_min, freq_max),
                    'power': random.uniform(-80, -40),
                    'channel': f"CH{random.randint(1, 88)}",
                    'band': 'Maritime VHF',
                    'violation': random.random() > 0.8,
                    'violation_type': random.choice(['Unlicensed', 'Over Power', 'Wrong Channel', None])
                })

        return correlations

    def update_vessel(self, mmsi: str, vessel_data: Dict[str, Any]) -> None:
        """Update or add a vessel with new data from AIS stream"""
        if mmsi not in self.vessels:
            # New vessel
            self.vessels[mmsi] = {
                'mmsi': mmsi,
                'lat': vessel_data.get('lat', 0),
                'lon': vessel_data.get('lon', 0),
                'sog': vessel_data.get('speed', 0),
                'cog': vessel_data.get('course', 0),
                'heading': vessel_data.get('heading', 0),
                'name': vessel_data.get('name', f'MMSI_{mmsi}'),
                'vessel_type': vessel_data.get('vessel_type', 'Unknown'),
                'length': vessel_data.get('length', 0),
                'width': vessel_data.get('width', 0),
                'draft': vessel_data.get('draft', 0),
                'timestamp': vessel_data.get('timestamp', datetime.now().isoformat()),
                'history_length': 1
            }
            self.vessel_history[mmsi] = [self.vessels[mmsi].copy()]
            self.playback_index[mmsi] = 0
        else:
            # Update existing vessel
            self.vessels[mmsi].update({
                'lat': vessel_data.get('lat', self.vessels[mmsi]['lat']),
                'lon': vessel_data.get('lon', self.vessels[mmsi]['lon']),
                'sog': vessel_data.get('speed', self.vessels[mmsi]['sog']),
                'cog': vessel_data.get('course', self.vessels[mmsi]['cog']),
                'heading': vessel_data.get('heading', self.vessels[mmsi]['heading']),
                'timestamp': vessel_data.get('timestamp', datetime.now().isoformat())
            })

            # Update name and type if provided
            if 'name' in vessel_data:
                self.vessels[mmsi]['name'] = vessel_data['name']
            if 'vessel_type' in vessel_data:
                self.vessels[mmsi]['vessel_type'] = vessel_data['vessel_type']

            # Add to history
            self.vessel_history[mmsi].append(self.vessels[mmsi].copy())
            # Keep only last 100 positions
            if len(self.vessel_history[mmsi]) > 100:
                self.vessel_history[mmsi] = self.vessel_history[mmsi][-100:]
            self.vessels[mmsi]['history_length'] = len(self.vessel_history[mmsi])

    def get_vessel_types(self) -> List[str]:
        """Get list of all vessel types currently tracked"""
        types = set()
        for vessel in self.vessels.values():
            vessel_type = vessel.get('vessel_type', 'Unknown')
            if vessel_type:
                types.add(vessel_type)
        return sorted(list(types))

    def get_vessels_by_type(self, vessel_types: List[str]) -> List[Dict[str, Any]]:
        """Get vessels filtered by vessel types"""
        if not vessel_types:
            return list(self.vessels.values())

        return [
            v for v in self.vessels.values()
            if v.get('vessel_type', 'Unknown') in vessel_types
        ]

    def get_vessels_filtered(self, vessel_types: List[str] = None,
                           min_lat: float = None, max_lat: float = None,
                           min_lon: float = None, max_lon: float = None) -> List[Dict[str, Any]]:
        """Get vessels with combined filtering"""
        vessels = list(self.vessels.values())

        # Filter by vessel type
        if vessel_types:
            vessels = [v for v in vessels if v.get('vessel_type', 'Unknown') in vessel_types]

        # Filter by geographic area
        if all([min_lat, max_lat, min_lon, max_lon]) is not None:
            vessels = [
                v for v in vessels
                if min_lat <= v['lat'] <= max_lat and min_lon <= v['lon'] <= max_lon
            ]

        return vessels

    def search_records(self, query: str = None, vessel_type: str = None,
                      min_lat: float = None, max_lat: float = None,
                      min_lon: float = None, max_lon: float = None,
                      limit: int = 100, offset: int = 0, return_total: bool = False):
        """Search through all AIS records with various filters."""

        # Start with all loaded records
        results = list(self.all_records)

        # Text search (MMSI, vessel name, etc.)
        if query:
            query_lower = query.lower()
            results = [
                r for r in results
                if any(query_lower in str(r.get(field, '')).lower()
                      for field in ['MMSI', 'VesselName', 'CallSign', 'IMO'])
            ]

        # Vessel type filter
        if vessel_type and vessel_type != 'all':
            # For CSV records, we might need to decode vessel type from VesselType field
            if vessel_type == 'cargo':
                results = [r for r in results if str(r.get('VesselType', '')).startswith('7')]
            elif vessel_type == 'tanker':
                results = [r for r in results if str(r.get('VesselType', '')).startswith('8')]
            elif vessel_type == 'passenger':
                results = [r for r in results if str(r.get('VesselType', '')).startswith('6')]
            elif vessel_type == 'fishing':
                results = [r for r in results if str(r.get('VesselType', '')).startswith('3')]
            elif vessel_type == 'tug':
                results = [r for r in results if r.get('VesselType') == '52']
            elif vessel_type == 'pilot':
                results = [r for r in results if r.get('VesselType') == '50']

        # Geographic filter
        if min_lat is not None and max_lat is not None and min_lon is not None and max_lon is not None:
            results = [
                r for r in results
                if min_lat <= float(r.get('LAT', 0)) <= max_lat and
                   min_lon <= float(r.get('LON', 0)) <= max_lon
            ]

        # Total matches before paging
        total_matches = len(results)

        # Apply offset/limit for pagination
        if offset and offset > 0:
            results = results[offset:offset + limit]
        else:
            results = results[:limit]

        if return_total:
            return results, total_matches

        return results

    def get_unique_vessels_from_records(self, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Get unique vessels from search results with latest position"""
        vessel_map = {}

        for record in records:
            mmsi = record.get('MMSI', '')
            if mmsi:
                # Keep the most recent record for each MMSI
                if mmsi not in vessel_map:
                    vessel_map[mmsi] = record
                else:
                    # Could compare timestamps if available
                    pass

        return list(vessel_map.values())

    def _decode_vessel_type(self, ais_type_code: int) -> str:
        """Decode AIS vessel type code to human-readable string"""
        if not isinstance(ais_type_code, int) or ais_type_code == 0:
            return 'Unknown'

        # AIS vessel type mapping (simplified)
        type_mapping = {
            # Reserved: 0
            1: 'Reserved',
            2: 'Reserved',
            # Wing in ground: 20-29
            20: 'Wing in Ground',
            21: 'Wing in Ground (Hazardous A)',
            22: 'Wing in Ground (Hazardous B)',
            23: 'Wing in Ground (Hazardous C)',
            24: 'Wing in Ground (Hazardous D)',
            # Special craft: 30-39
            30: 'Fishing',
            31: 'Towing',
            32: 'Towing (large)',
            33: 'Dredger',
            34: 'Diving ops',
            35: 'Military ops',
            36: 'Sailing',
            37: 'Pleasure Craft',
            # High speed craft: 40-49
            40: 'High Speed Craft',
            41: 'High Speed Craft (Hazardous A)',
            42: 'High Speed Craft (Hazardous B)',
            43: 'High Speed Craft (Hazardous C)',
            44: 'High Speed Craft (Hazardous D)',
            # Special craft: 50-59
            50: 'Pilot Vessel',
            51: 'Search and Rescue',
            52: 'Tug',
            53: 'Port Tender',
            54: 'Anti-pollution',
            55: 'Law Enforcement',
            56: 'Spare - Local Vessel',
            57: 'Spare - Local Vessel',
            58: 'Medical Transport',
            59: 'Noncombatant',
            # Passenger ships: 60-69
            60: 'Passenger',
            61: 'Passenger (Hazardous A)',
            62: 'Passenger (Hazardous B)',
            63: 'Passenger (Hazardous C)',
            64: 'Passenger (Hazardous D)',
            65: 'Passenger (Reserved)',
            66: 'Passenger (Reserved)',
            67: 'Passenger (Reserved)',
            68: 'Passenger (Reserved)',
            69: 'Passenger (No additional info)',
            # Cargo ships: 70-79
            70: 'Cargo',
            71: 'Cargo (Hazardous A)',
            72: 'Cargo (Hazardous B)',
            73: 'Cargo (Hazardous C)',
            74: 'Cargo (Hazardous D)',
            75: 'Cargo (Reserved)',
            76: 'Cargo (Reserved)',
            77: 'Cargo (Reserved)',
            78: 'Cargo (Reserved)',
            79: 'Cargo (No additional info)',
            # Tankers: 80-89
            80: 'Tanker',
            81: 'Tanker (Hazardous A)',
            82: 'Tanker (Hazardous B)',
            83: 'Tanker (Hazardous C)',
            84: 'Tanker (Hazardous D)',
            85: 'Tanker (Reserved)',
            86: 'Tanker (Reserved)',
            87: 'Tanker (Reserved)',
            88: 'Tanker (Reserved)',
            89: 'Tanker (No additional info)',
            # Other: 90-99
            90: 'Other',
            91: 'Other (Hazardous A)',
            92: 'Other (Hazardous B)',
            93: 'Other (Hazardous C)',
            94: 'Other (Hazardous D)',
            95: 'Other (Reserved)',
            96: 'Other (Reserved)',
            97: 'Other (Reserved)',
            98: 'Other (Reserved)',
            99: 'Other (No additional info)'
        }

        return type_mapping.get(ais_type_code, f'Unknown ({ais_type_code})')


# ============================================================================
# PERFORMANCE METRICS & PROFILING
# ============================================================================

class PerformanceMetrics:
    """Track performance metrics for API endpoints and computations."""

    def __init__(self, max_history: int = 1000):
        self.metrics: Dict[str, deque] = defaultdict(lambda: deque(maxlen=max_history))
        self.counters: Dict[str, int] = defaultdict(int)
        self.start_time = time.monotonic()
        self._lock = threading.Lock()

    def record(self, operation: str, duration_ms: float, metadata: Dict = None):
        """Record a timing measurement."""
        with self._lock:
            self.metrics[operation].append({
                'duration_ms': duration_ms,
                'timestamp': time.time(),
                'metadata': metadata or {}
            })
            self.counters[operation] += 1

    def increment(self, counter: str, amount: int = 1):
        """Increment a counter."""
        with self._lock:
            self.counters[counter] += amount

    def get_stats(self, operation: str) -> Dict[str, Any]:
        """Get statistics for an operation."""
        with self._lock:
            measurements = list(self.metrics[operation])

        if not measurements:
            return {'count': 0, 'avg_ms': 0, 'min_ms': 0, 'max_ms': 0, 'p95_ms': 0}

        durations = [m['duration_ms'] for m in measurements]
        durations.sort()

        return {
            'count': len(durations),
            'total_calls': self.counters[operation],
            'avg_ms': sum(durations) / len(durations),
            'min_ms': min(durations),
            'max_ms': max(durations),
            'p95_ms': durations[int(len(durations) * 0.95)] if len(durations) > 1 else durations[0],
            'recent_avg_ms': sum(durations[-100:]) / min(len(durations), 100)
        }

    def get_all_stats(self) -> Dict[str, Any]:
        """Get all statistics."""
        with self._lock:
            operations = list(self.metrics.keys())

        return {
            'uptime_seconds': time.monotonic() - self.start_time,
            'operations': {op: self.get_stats(op) for op in operations},
            'counters': dict(self.counters)
        }


def timed_operation(metrics: PerformanceMetrics, operation_name: str):
    """Decorator to time operations and record metrics."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                return result
            finally:
                duration_ms = (time.perf_counter() - start) * 1000
                metrics.record(operation_name, duration_ms)
        return wrapper
    return decorator


# ============================================================================
# PERSISTENT METRICS LOGGER - Append-only log for auditing & analysis
# ============================================================================

class MetricsLogger:
    """
    Persistent metrics logger for long-term storage and auditing.
    Writes to both JSON lines file and optional SQLite database.
    """

    def __init__(self, log_dir: str = None):
        self.log_dir = log_dir or _data_dir()
        self._ensure_log_dir()
        self._lock = threading.Lock()

        # JSON lines log file (append-only)
        self.log_file = os.path.join(self.log_dir, f"metrics_{datetime.now().strftime('%Y%m%d')}.jsonl")

        # SQLite database for structured queries
        self.db_path = os.path.join(self.log_dir, "metrics.db")
        self._init_sqlite()

        # In-memory aggregation for real-time dashboards
        self._session_metrics: Dict[str, List] = defaultdict(list)
        self._session_start = time.monotonic()

        logger.info(f"MetricsLogger initialized: {self.log_dir}")

    def _ensure_log_dir(self):
        """Create log directory if it doesn't exist."""
        if not os.path.exists(self.log_dir):
            os.makedirs(self.log_dir)

    def _init_sqlite(self):
        """Initialize SQLite database with metrics schema."""
        try:
            import sqlite3
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Per-instance isolation pragmas
            cursor.execute('PRAGMA journal_mode = WAL')
            cursor.execute('PRAGMA synchronous = NORMAL')
            cursor.execute('PRAGMA foreign_keys = ON')
            cursor.execute('PRAGMA temp_store = MEMORY')

            # Main metrics table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    session_id TEXT,
                    module TEXT NOT NULL,
                    metric_name TEXT NOT NULL,
                    value REAL,
                    metadata TEXT,
                    user_agent TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Index for common queries
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_metrics_timestamp ON metrics(timestamp)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_metrics_module ON metrics(module)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_metrics_name ON metrics(metric_name)')

            # User interactions table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_interactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    session_id TEXT,
                    action TEXT NOT NULL,
                    target TEXT,
                    details TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Missions: metadata for mission-aware namespaces
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS missions (
                    mission_id TEXT PRIMARY KEY,
                    name TEXT,
                    owner TEXT,
                    status TEXT,
                    metadata TEXT,
                    created_at REAL,
                    updated_at REAL
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS mission_members (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id TEXT NOT NULL,
                    operator_id TEXT NOT NULL,
                    role TEXT,
                    joined_at REAL,
                    UNIQUE(mission_id, operator_id)
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS mission_tasks (
                    task_id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL,
                    title TEXT,
                    status TEXT,
                    priority INTEGER,
                    payload TEXT,
                    created_at REAL,
                    updated_at REAL
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS mission_watchlist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    note TEXT,
                    added_at REAL,
                    UNIQUE(mission_id, entity_id)
                )
            ''')

            conn.commit()
            conn.close()
            logger.info("SQLite metrics database initialized")
        except Exception as e:
            logger.warning(f"SQLite initialization failed (will use JSON only): {e}")

    def log(self, module: str, metric_name: str, value: float,
            metadata: Dict = None, session_id: str = None, user_agent: str = None):
        """
        Log a metric entry to both JSON lines file and SQLite.

        Args:
            module: Component name (e.g., 'recon', 'hypergraph', 'ais')
            metric_name: Metric identifier (e.g., 'update_time_ms', 'entity_count')
            value: Numeric metric value
            metadata: Optional additional context
            session_id: Client session identifier
            user_agent: Client user agent string
        """
        timestamp = time.time()
        entry = {
            'timestamp': timestamp,
            'datetime': datetime.fromtimestamp(timestamp).isoformat(),
            'session_id': session_id,
            'module': module,
            'metric_name': metric_name,
            'value': value,
            'metadata': metadata or {},
            'user_agent': user_agent
        }

        with self._lock:
            # Write to JSON lines file
            self._write_jsonl(entry)

            # Write to SQLite
            self._write_sqlite(entry)

            # Update in-memory aggregation
            key = f"{module}.{metric_name}"
            self._session_metrics[key].append({
                'timestamp': timestamp,
                'value': value
            })

            # Keep only last 1000 entries per metric in memory
            if len(self._session_metrics[key]) > 1000:
                self._session_metrics[key] = self._session_metrics[key][-1000:]

    def _write_jsonl(self, entry: Dict):
        """Append entry to JSON lines log file."""
        try:
            # Rotate log file daily
            today_file = os.path.join(self.log_dir, f"metrics_{datetime.now().strftime('%Y%m%d')}.jsonl")
            if today_file != self.log_file:
                self.log_file = today_file

            with open(self.log_file, 'a') as f:
                f.write(json.dumps(entry) + '\n')
        except Exception as e:
            logger.warning(f"Failed to write JSON log: {e}")

    def _write_sqlite(self, entry: Dict):
        """Insert entry into SQLite database."""
        try:
            import sqlite3
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute('''
                INSERT INTO metrics (timestamp, session_id, module, metric_name, value, metadata, user_agent)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                entry['timestamp'],
                entry.get('session_id'),
                entry['module'],
                entry['metric_name'],
                entry['value'],
                json.dumps(entry.get('metadata', {})),
                entry.get('user_agent')
            ))

            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"Failed to write SQLite: {e}")

    def log_interaction(self, action: str, target: str = None,
                        details: Dict = None, session_id: str = None):
        """Log a user interaction event."""
        timestamp = time.time()
        entry = {
            'timestamp': timestamp,
            'datetime': datetime.fromtimestamp(timestamp).isoformat(),
            'session_id': session_id,
            'action': action,
            'target': target,
            'details': details or {}
        }

        with self._lock:
            # Write to JSON lines
            try:
                interactions_file = os.path.join(self.log_dir, f"interactions_{datetime.now().strftime('%Y%m%d')}.jsonl")
                with open(interactions_file, 'a') as f:
                    f.write(json.dumps(entry) + '\n')
            except Exception as e:
                logger.warning(f"Failed to write interaction log: {e}")

            # Write to SQLite
            try:
                import sqlite3
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO user_interactions (timestamp, session_id, action, target, details)
                    VALUES (?, ?, ?, ?, ?)
                ''', (timestamp, session_id, action, target, json.dumps(details or {})))
                conn.commit()
                conn.close()
            except Exception as e:
                logger.warning(f"Failed to write interaction to SQLite: {e}")

    def log_batch(self, entries: List[Dict]):
        """Log multiple metric entries at once (more efficient)."""
        for entry in entries:
            self.log(
                module=entry.get('module', 'unknown'),
                metric_name=entry.get('metric_name', 'unknown'),
                value=entry.get('value', 0),
                metadata=entry.get('metadata'),
                session_id=entry.get('session_id'),
                user_agent=entry.get('user_agent')
            )

    def get_session_summary(self) -> Dict:
        """Get summary of metrics collected this session."""
        summary = {
            'session_duration_seconds': time.monotonic() - self._session_start,
            'metrics': {}
        }

        with self._lock:
            for key, values in self._session_metrics.items():
                if values:
                    numeric_values = [v['value'] for v in values]
                    summary['metrics'][key] = {
                        'count': len(values),
                        'avg': sum(numeric_values) / len(numeric_values),
                        'min': min(numeric_values),
                        'max': max(numeric_values),
                        'last': values[-1]['value']
                    }

        return summary

    def query_metrics(self, module: str = None, metric_name: str = None,
                      start_time: float = None, end_time: float = None,
                      limit: int = 1000) -> List[Dict]:
        """Query historical metrics from SQLite."""
        try:
            import sqlite3
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            query = "SELECT * FROM metrics WHERE 1=1"
            params = []

            if module:
                query += " AND module = ?"
                params.append(module)
            if metric_name:
                query += " AND metric_name = ?"
                params.append(metric_name)
            if start_time:
                query += " AND timestamp >= ?"
                params.append(start_time)
            if end_time:
                query += " AND timestamp <= ?"
                params.append(end_time)

            query += f" ORDER BY timestamp DESC LIMIT {limit}"

            cursor.execute(query, params)
            rows = cursor.fetchall()
            conn.close()

            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error querying metrics: {e}")
            return []


# Global metrics logger instance
metrics_logger = MetricsLogger()


# Global performance metrics instance
perf_metrics = PerformanceMetrics()

# Initialize satellites table in SQLite (if available)
def _init_satellite_table():
    try:
        import sqlite3
        db_path = metrics_logger.db_path
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS satellites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                lat REAL,
                lon REAL,
                altitude REAL,
                operator TEXT,
                type TEXT,
                frequency TEXT,
                orbit TEXT,
                coverage TEXT,
                status TEXT,
                launch_date TEXT,
                mission TEXT,
                extra JSON
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_sat_name ON satellites(name)')
        conn.commit()
        conn.close()
        logger.info('Satellite table ensured in SQLite DB')
    except Exception as e:
        logger.warning(f'Could not initialize satellite table: {e}')


_init_satellite_table()

# ---------------------------------------------------------------------------
# Satellite TLE fetch & propagation utilities
# ---------------------------------------------------------------------------
def fetch_tles_from_celestrak(category: str = 'visual') -> List[Tuple[str, str, str]]:
    """Fetch TLEs from Celestrak for a given category.

    Returns a list of tuples: (name, line1, line2)
    """
    try:
        import requests

        # Try multiple Celestrak hosts/endpoints to be resilient against redirects/host changes
        candidates = [
            f'https://celestrak.com/NORAD/elements/{category}.txt',
            f'https://celestrak.org/NORAD/elements/{category}.txt',
            f'https://celestrak.org/NORAD/elements/gp.php?GROUP={category}&FORMAT=3le',
            f'https://celestrak.com/NORAD/elements/gp.php?GROUP={category}&FORMAT=3le',
            # Fallback: recent updates feed
            f'https://celestrak.org/NORAD/elements/gp.php?GROUP=last-30-days&FORMAT=3le',
        ]

        resp = None
        used_url = None
        for url in candidates:
            try:
                r = requests.get(url, timeout=12)
                if r.status_code == 200 and r.text and len(r.text) > 100:
                    resp = r
                    used_url = url
                    break
                else:
                    logger.debug(f'Celestrak attempt {url} -> {r.status_code}')
            except Exception as e:
                logger.debug(f'Error fetching {url}: {e}')

        if resp is None:
            logger.warning(f'Failed to fetch TLEs for category "{category}" from Celestrak')
            return []

        lines = [l.strip() for l in resp.text.splitlines() if l.strip()]

        def parse_three_line_groups(lines_list: List[str]) -> List[Tuple[str, str, str]]:
            out = []
            i = 0
            while i + 2 < len(lines_list):
                name = lines_list[i]
                l1 = lines_list[i+1]
                l2 = lines_list[i+2]
                # Basic validation of TLE lines
                if (l1.startswith('1 ') and l2.startswith('2 ')) or (l1[0].isdigit() and l2[0].isdigit()):
                    out.append((name, l1, l2))
                    i += 3
                else:
                    # If format doesn't match, try to slide window forward
                    i += 1
            return out

        # If the feed contains explicit 1/2 lines but no names, pair them
        def parse_pair_lines(lines_list: List[str]) -> List[Tuple[str, str, str]]:
            out = []
            i = 0
            while i < len(lines_list):
                if lines_list[i].startswith('1 ') and i + 1 < len(lines_list) and lines_list[i+1].startswith('2 '):
                    # Try to use previous line as name when available
                    name = lines_list[i-1] if i - 1 >= 0 and not lines_list[i-1].startswith(('1 ', '2 ')) else f'NO_NAME_{i}'
                    out.append((name, lines_list[i], lines_list[i+1]))
                    i += 2
                else:
                    i += 1
            return out

        # Prefer standard 3-line groups; if none found, attempt 1/2 pairing
        tles = parse_three_line_groups(lines)
        if not tles:
            tles = parse_pair_lines(lines)

        logger.info(f'Fetched {len(tles)} TLEs from {used_url or "unknown"} for category {category}')
        return tles
    except Exception as e:
        logger.warning(f'Could not fetch TLEs from Celestrak: {e}')
        return []


def fetch_tles_from_n2yo(ids: List[int]) -> List[Tuple[str, str, str]]:
    """Fetch TLEs from N2YO for a list of NORAD IDs. Requires assets/n2yo.py with API key configured.

    Returns list of (name, line1, line2)
    """
    tles = []
    try:
        # import local helper if present
        try:
            from assets import n2yo as _n2yo
        except Exception:
            # fallback to direct import by filename
            import importlib.util, sys, os
            spec = importlib.util.spec_from_file_location('n2yo', os.path.join(os.path.dirname(__file__), 'assets', 'n2yo.py'))
            n2yo_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(n2yo_mod)
            _n2yo = n2yo_mod

        for nid in ids:
            try:
                data = _n2yo.GetTLEData(str(nid))
                name = data.get('info', {}).get('satname') or f'N2YO_{nid}'
                tle = data.get('tle')
                if not tle:
                    logger.debug(f'N2YO returned no TLE for {nid}')
                    continue
                # TLE may be a string with newlines or a list
                if isinstance(tle, str):
                    lines = [l.strip() for l in tle.splitlines() if l.strip()]
                elif isinstance(tle, (list, tuple)):
                    lines = [l.strip() for l in tle if l and l.strip()]
                else:
                    lines = []

                if len(lines) >= 2:
                    l1 = lines[0] if lines[0].startswith('1 ') else lines[0]
                    l2 = lines[1] if lines[1].startswith('2 ') else lines[1]
                    tles.append((name, l1, l2))
                else:
                    logger.debug(f'Unexpected TLE format from N2YO for {nid}: {tle}')
            except Exception as e:
                logger.debug(f'Failed to fetch/parse N2YO TLE for {nid}: {e}')

    except Exception as e:
        logger.error(f'Error in fetch_tles_from_n2yo: {e}')

    return tles


def propagate_tle_to_latlon(name: str, line1: str, line2: str, when: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """Propagate a TLE to a geodetic lat/lon/alt (WGS84-ish) using SGP4.

    Returns dict with keys: name, lat, lon, altitude  (altitude in km)

    Notes:
    - Uses a simplified TEME->ECEF rotation (GMST-only). For UI visualization this is usually sufficient.
    - Avoids pyorbital's deep-space/near-space limitations that were causing many satellites to fall back to NULL altitude.
    """
    try:
        if when is None:
            when = datetime.utcnow()

        # Prefer python-sgp4: robust for near-earth and deep-space objects
        from sgp4.api import Satrec
        from sgp4.conveniences import jday_datetime
        from sgp4.ext import gstime

        def _ecef_to_geodetic_wgs84(x_m: float, y_m: float, z_m: float):
            # WGS84 constants
            a = 6378137.0
            f = 1.0 / 298.257223563
            b = a * (1.0 - f)
            e2 = 1.0 - (b*b)/(a*a)
            ep2 = (a*a - b*b)/(b*b)

            import math
            lon = math.atan2(y_m, x_m)
            p = math.hypot(x_m, y_m)

            # Bowring's method
            theta = math.atan2(z_m * a, p * b)
            st = math.sin(theta)
            ct = math.cos(theta)
            lat = math.atan2(z_m + ep2 * b * (st**3), p - e2 * a * (ct**3))

            sl = math.sin(lat)
            N = a / math.sqrt(1.0 - e2 * sl * sl)
            alt_m = p / math.cos(lat) - N

            return lat, lon, alt_m

        sat = Satrec.twoline2rv(line1, line2)
        jd, fr = jday_datetime(when)
        err, r_km, _v_km_s = sat.sgp4(jd, fr)
        if err != 0:
            # Non-zero error codes: https://pypi.org/project/sgp4/ docs; keep UI resilient
            logger.warning(f"Propagation failed for {name}: sgp4 error code {err}")
            return None

        import math
        # TEME -> ECEF via GMST rotation (approx)
        theta = gstime(jd + fr)
        c = math.cos(theta)
        s = math.sin(theta)

        x_km = r_km[0] * c + r_km[1] * s
        y_km = -r_km[0] * s + r_km[1] * c
        z_km = r_km[2]

        lat_rad, lon_rad, alt_m = _ecef_to_geodetic_wgs84(x_km * 1000.0, y_km * 1000.0, z_km * 1000.0)

        return {
            "name": name,
            "lat": float(lat_rad * 180.0 / math.pi),
            "lon": float(lon_rad * 180.0 / math.pi),
            "altitude": float(alt_m) / 1000.0,  # km
        }

    except Exception as e:
        # Fallback: keep previous behavior if SGP4 isn't installed
        try:
            from pyorbital.orbital import Orbital
            if when is None:
                when = datetime.utcnow()
            orb = Orbital(name, line1=line1, line2=line2)
            lon, lat, alt_m = orb.get_lonlatalt(when)
            return {"name": name, "lat": lat, "lon": lon, "altitude": float(alt_m) / 1000.0}
        except Exception as e2:
            logger.warning(f"Propagation failed for {name}: {e2}")
            return None

def update_satellite_db_from_tles(tles: List[Tuple[str, str, str]], operator: str = 'Celestrak'):
    """Propagate TLEs and upsert into the satellites SQLite table."""
    try:
        import sqlite3
        db_path = metrics_logger.db_path
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        for name, l1, l2 in tles:
            try:
                pos = propagate_tle_to_latlon(name, l1, l2)
                extra = {'tle': [l1, l2], 'source': 'celestrak'}
                if pos is None:
                    # Still store TLE for future processing
                    cursor.execute('SELECT id FROM satellites WHERE name = ?', (name,))
                    row = cursor.fetchone()
                    if row:
                        cursor.execute('UPDATE satellites SET operator = ?, extra = ? WHERE id = ?', (operator, json.dumps(extra), row[0]))
                    else:
                        cursor.execute('INSERT INTO satellites (name, operator, extra, status) VALUES (?, ?, ?, ?)', (name, operator, json.dumps(extra), 'stale'))
                    continue

                # Try to find existing record by name
                cursor.execute('SELECT id FROM satellites WHERE name = ?', (name,))
                row = cursor.fetchone()
                extra_json = json.dumps(extra)
                if row:
                    cursor.execute('''
                        UPDATE satellites SET lat = ?, lon = ?, altitude = ?, operator = ?, extra = ?, status = ?, launch_date = ? WHERE id = ?
                    ''', (pos['lat'], pos['lon'], pos['altitude'], operator, extra_json, 'active', None, row[0]))
                else:
                    cursor.execute('''
                        INSERT INTO satellites (name, lat, lon, altitude, operator, type, frequency, orbit, coverage, status, launch_date, mission, extra)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (name, pos['lat'], pos['lon'], pos['altitude'], operator, None, None, None, None, 'active', None, None, extra_json))
            except Exception as e:
                logger.warning(f'Failed to upsert satellite {name}: {e}')

        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f'Error updating satellite DB: {e}')


# Background thread to periodically refresh satellite positions
_satellite_refresh_thread = None
_satellite_refresh_running = False

def _satellite_refresh_loop(interval_seconds: int = 60, categories: List[str] = None):
    global _satellite_refresh_running
    _satellite_refresh_running = True
    if categories is None:
        categories = ['visual', 'starlink', 'active']

    while _satellite_refresh_running:
        try:
            all_tles = []
            for cat in categories:
                tles = fetch_tles_from_celestrak(cat)
                if tles:
                    all_tles.extend(tles)

            if all_tles:
                logger.info(f'Updating satellite DB with {len(all_tles)} TLEs')
                update_satellite_db_from_tles(all_tles, operator='Celestrak')
            else:
                logger.info('No TLEs fetched for satellite refresh')
        except Exception as e:
            logger.error(f'Unhandled error in satellite refresh loop: {e}')

        time.sleep(interval_seconds)


def start_satellite_refresh(interval_seconds: int = 60, categories: List[str] = None):
    global _satellite_refresh_thread
    if _satellite_refresh_thread and _satellite_refresh_thread.is_alive():
        return
    _satellite_refresh_thread = threading.Thread(target=_satellite_refresh_loop, args=(interval_seconds, categories or None), daemon=True)
    _satellite_refresh_thread.start()


def _geodetic_to_ecef(lat_deg: float, lon_deg: float, alt_km: float) -> Tuple[float, float, float]:
    """Convert geodetic coordinates (deg,deg,km) to ECEF (km)."""
    # WGS84
    a = 6378.137  # km
    f = 1 / 298.257223563
    e2 = f * (2 - f)

    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    N = a / math.sqrt(1 - e2 * (math.sin(lat) ** 2))

    x = (N + alt_km) * math.cos(lat) * math.cos(lon)
    y = (N + alt_km) * math.cos(lat) * math.sin(lon)
    z = (N * (1 - e2) + alt_km) * math.sin(lat)
    return x, y, z


def _compute_az_el_range(observer_lat: float, observer_lon: float, observer_alt_km: float,
                         sat_lat: float, sat_lon: float, sat_alt_km: float) -> Dict[str, float]:
    """Compute azimuth (deg), elevation (deg) and range (km) from observer to satellite."""
    # Convert to ECEF
    ox, oy, oz = _geodetic_to_ecef(observer_lat, observer_lon, observer_alt_km)
    sx, sy, sz = _geodetic_to_ecef(sat_lat, sat_lon, sat_alt_km)

    # vector from observer to satellite
    vx = sx - ox
    vy = sy - oy
    vz = sz - oz
    # range
    rng = math.sqrt(vx * vx + vy * vy + vz * vz)

    # build local ENU axes at observer
    lat_r = math.radians(observer_lat)
    lon_r = math.radians(observer_lon)
    sin_lat = math.sin(lat_r)
    cos_lat = math.cos(lat_r)
    sin_lon = math.sin(lon_r)
    cos_lon = math.cos(lon_r)

    # East vector
    ex = -sin_lon
    ey = cos_lon
    ez = 0.0

    # North vector
    nx = -sin_lat * cos_lon
    ny = -sin_lat * sin_lon
    nz = cos_lat

    # Up vector
    ux = cos_lat * cos_lon
    uy = cos_lat * sin_lon
    uz = sin_lat

    # projections
    east_comp = ex * vx + ey * vy + ez * vz
    north_comp = nx * vx + ny * vy + nz * vz
    up_comp = ux * vx + uy * vy + uz * vz

    # azimuth: angle from north to east
    az = math.degrees(math.atan2(east_comp, north_comp)) % 360.0
    # elevation
    horiz_dist = math.sqrt(east_comp * east_comp + north_comp * north_comp)
    el = math.degrees(math.atan2(up_comp, horiz_dist))

    return {'az_deg': az, 'el_deg': el, 'range_km': rng}


def populate_satellites_for_category(category: str, observer: Dict[str, float] = None) -> int:
    """Fetch TLEs for a category, propagate, compute az/el (optional), and upsert into DB.

    observer: dict with keys 'lat','lon','alt_km' (altitude in km)
    Returns number of records processed.
    """
    tles = fetch_tles_from_celestrak(category)
    if not tles:
        return 0

    return _populate_tles_into_db(tles, operator='Celestrak', observer=observer)


def _populate_tles_into_db(tles: List[Tuple[str, str, str]], operator: str = 'Celestrak', observer: Dict[str, float] = None) -> int:
    """Common helper to propagate TLEs, compute optional observer az/el, and upsert into DB.
    tles: list of (name, line1, line2)
    operator: source string to store in operator column
    observer: optional dict with lat/lon/alt_km to compute az/el
    """
    processed = 0
    try:
        import sqlite3
        db_path = metrics_logger.db_path
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        now_iso = datetime.utcnow().isoformat()
        for name, l1, l2 in tles:
            try:
                pos = propagate_tle_to_latlon(name, l1, l2)
                extra = {'tle': [l1, l2], 'source': operator.lower() if operator else None, 'updated_at': now_iso}

                azel = None
                if observer and pos:
                    try:
                        azel = _compute_az_el_range(observer.get('lat'), observer.get('lon'), observer.get('alt_km', 0.0),
                                                     pos['lat'], pos['lon'], pos['altitude'])
                        extra['observer_az_el'] = azel
                    except Exception as e:
                        logger.debug(f'Az/el compute failed for {name}: {e}')

                # upsert
                cursor.execute('SELECT id FROM satellites WHERE name = ?', (name,))
                row = cursor.fetchone()
                extra_json = json.dumps(extra)
                if pos:
                    lat_val = pos['lat']
                    lon_val = pos['lon']
                    alt_val = pos['altitude']
                else:
                    lat_val = None
                    lon_val = None
                    alt_val = None

                if row:
                    cursor.execute('''
                        UPDATE satellites SET lat = ?, lon = ?, altitude = ?, operator = ?, extra = ?, status = ? WHERE id = ?
                    ''', (lat_val, lon_val, alt_val, operator, extra_json, 'active' if pos else 'stale', row[0]))
                else:
                    cursor.execute('''
                        INSERT INTO satellites (name, lat, lon, altitude, operator, type, frequency, orbit, coverage, status, launch_date, mission, extra)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (name, lat_val, lon_val, alt_val, operator, None, None, None, None, 'active' if pos else 'stale', None, None, extra_json))

                processed += 1
            except Exception as e:
                logger.debug(f'Failed to populate satellite {name}: {e}')
                continue

        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f'Error populating satellites from TLEs (operator={operator}): {e}')

    return processed


def api_populate_satellites():
    """Populate satellite DB from a Celestrak category and optionally compute az/el for an observer.

    JSON body:
      { "category": "starlink", "observer": {"lat": 37.77, "lon": -122.42, "alt_km": 0.0} }
    """
    try:
        data = request.get_json() or {}
        category = data.get('category') or request.args.get('category') or 'starlink'
        observer = data.get('observer')
        source = data.get('source', 'celestrak')

        # If client provided explicit NORAD ids and requested n2yo source, use that
        if source.lower() == 'n2yo' and data.get('ids'):
            ids = data.get('ids')
            tles = fetch_tles_from_n2yo(ids)
            count = _populate_tles_into_db(tles, operator='N2YO', observer=observer)
            return jsonify({'status': 'ok', 'processed': count, 'source': 'n2yo', 'ids_requested': len(ids)})

        # Default: fetch by category from Celestrak
        count = populate_satellites_for_category(category, observer)
        return jsonify({'status': 'ok', 'processed': count, 'category': category})
    except Exception as e:
        logger.error(f'Error in populate API: {e}')
        return jsonify({'status': 'error', 'message': str(e)}), 500



# ============================================================================
# SPATIAL INDEX FOR O(log n) PROXIMITY QUERIES
# ============================================================================

class SpatialIndex:
    """
    Spatial index using k-d tree for efficient proximity queries.
    Converts lat/lon to 3D Cartesian coordinates for accurate distance computation.
    """

    EARTH_RADIUS_NM = 3440.065  # Nautical miles

    def __init__(self):
        self._tree = None
        self._entity_ids: List[str] = []
        self._coordinates: np.ndarray = None
        self._dirty = True
        self._last_build_time = 0
        self._build_count = 0

    def _latlon_to_cartesian(self, lat: float, lon: float) -> Tuple[float, float, float]:
        """Convert lat/lon to 3D Cartesian coordinates on unit sphere."""
        lat_rad = math.radians(lat)
        lon_rad = math.radians(lon)

        x = math.cos(lat_rad) * math.cos(lon_rad)
        y = math.cos(lat_rad) * math.sin(lon_rad)
        z = math.sin(lat_rad)

        return (x, y, z)

    def _chord_to_arc_distance(self, chord_distance: float) -> float:
        """Convert chord distance on unit sphere to arc distance in nautical miles."""
        # chord = 2 * sin(angle/2), so angle = 2 * arcsin(chord/2)
        if chord_distance >= 2.0:
            return math.pi * self.EARTH_RADIUS_NM  # Half circumference
        angle = 2 * math.asin(chord_distance / 2)
        return angle * self.EARTH_RADIUS_NM

    def _arc_to_chord_distance(self, arc_nm: float) -> float:
        """Convert arc distance in nautical miles to chord distance on unit sphere."""
        angle = arc_nm / self.EARTH_RADIUS_NM
        return 2 * math.sin(angle / 2)

    def build(self, entities: Dict[str, Dict[str, Any]]):
        """Build or rebuild the spatial index from entities."""
        start = time.perf_counter()

        if not entities:
            self._tree = None
            self._entity_ids = []
            self._coordinates = None
            self._dirty = False
            return

        self._entity_ids = list(entities.keys())
        coords = []
        valid_ids = []

        for entity_id in self._entity_ids:
            entity = entities[entity_id]
            loc = entity.get('location') or {}
            lat = loc.get('lat')
            lon = loc.get('lon')
            if lat is None or lon is None:
                continue
            try:
                lat = float(lat)
                lon = float(lon)
            except (TypeError, ValueError):
                continue
            valid_ids.append(entity_id)
            coords.append(self._latlon_to_cartesian(lat, lon))

        self._entity_ids = valid_ids
        self._coordinates = np.array(coords) if coords else None

        if SCIPY_AVAILABLE and len(coords) > 0:
            self._tree = cKDTree(self._coordinates)
        else:
            self._tree = None

        self._dirty = False
        self._last_build_time = time.perf_counter() - start
        self._build_count += 1

        perf_metrics.record('spatial_index_build', self._last_build_time * 1000,
                           {'entity_count': len(entities)})

    def mark_dirty(self):
        """Mark the index as needing rebuild."""
        self._dirty = True

    @property
    def is_dirty(self) -> bool:
        return self._dirty

    def query_radius(self, lat: float, lon: float, radius_nm: float) -> List[Tuple[str, float]]:
        """
        Find all entities within radius_nm of the given point.
        Returns list of (entity_id, distance_nm) tuples, sorted by distance.
        """
        if self._tree is None or len(self._entity_ids) == 0:
            return []

        start = time.perf_counter()

        # Convert query point and radius
        query_point = np.array([self._latlon_to_cartesian(lat, lon)])
        chord_radius = self._arc_to_chord_distance(radius_nm)

        # Query the tree
        if SCIPY_AVAILABLE:
            indices = self._tree.query_ball_point(query_point[0], chord_radius)
        else:
            # Fallback: brute force O(n)
            distances = np.linalg.norm(self._coordinates - query_point, axis=1)
            indices = np.where(distances <= chord_radius)[0].tolist()

        # Convert results with accurate distances
        results = []
        for idx in indices:
            entity_id = self._entity_ids[idx]
            chord_dist = np.linalg.norm(self._coordinates[idx] - query_point[0])
            arc_dist = self._chord_to_arc_distance(chord_dist)
            results.append((entity_id, arc_dist))

        # Sort by distance
        results.sort(key=lambda x: x[1])

        duration_ms = (time.perf_counter() - start) * 1000
        perf_metrics.record('spatial_query_radius', duration_ms,
                           {'radius_nm': radius_nm, 'result_count': len(results)})

        return results

    def query_nearest(self, lat: float, lon: float, k: int = 10) -> List[Tuple[str, float]]:
        """
        Find the k nearest entities to the given point.
        Returns list of (entity_id, distance_nm) tuples.
        """
        if self._tree is None or len(self._entity_ids) == 0:
            return []

        start = time.perf_counter()

        query_point = np.array([self._latlon_to_cartesian(lat, lon)])
        k = min(k, len(self._entity_ids))

        if SCIPY_AVAILABLE:
            distances, indices = self._tree.query(query_point, k=k)
            distances = distances[0]
            indices = indices[0]
        else:
            # Fallback: brute force
            all_distances = np.linalg.norm(self._coordinates - query_point, axis=1)
            indices = np.argsort(all_distances)[:k]
            distances = all_distances[indices]

        results = []
        for i, idx in enumerate(indices):
            entity_id = self._entity_ids[idx]
            arc_dist = self._chord_to_arc_distance(distances[i])
            results.append((entity_id, arc_dist))

        duration_ms = (time.perf_counter() - start) * 1000
        perf_metrics.record('spatial_query_nearest', duration_ms, {'k': k})

        return results

    def get_stats(self) -> Dict[str, Any]:
        """Get spatial index statistics."""
        return {
            'entity_count': len(self._entity_ids),
            'is_dirty': self._dirty,
            'last_build_time_ms': self._last_build_time * 1000,
            'build_count': self._build_count,
            'scipy_available': SCIPY_AVAILABLE
        }


# ============================================================================
# GRAPH EMBEDDING CACHE FOR SCALABLE GRAPH DISTANCES
# ============================================================================

class GraphEmbeddingCache:
    """
    Cache for graph node embeddings to enable O(1) approximate distance lookups.
    Supports incremental updates when graph structure changes.
    """

    def __init__(self, embedding_dim: int = 64):
        self.embedding_dim = embedding_dim
        self.embeddings: Dict[str, np.ndarray] = {}
        self.centrality_cache: Dict[str, float] = {}
        self.dirty_nodes: Set[str] = set()
        self._version = 0
        self._last_compute_time = 0

    def compute_simple_embedding(self, node_id: str, neighbors: List[str],
                                  node_data: Dict[str, Any]) -> np.ndarray:
        """
        Compute a simple embedding for a node based on its properties and neighbors.
        This is a placeholder for more sophisticated methods like Node2Vec or GraphSAGE.
        """
        # Create feature vector from node properties
        features = np.zeros(self.embedding_dim)

        # Encode node type/category
        if 'type' in node_data:
            type_hash = hash(node_data['type']) % (self.embedding_dim // 4)
            features[type_hash] = 1.0

        # Encode frequency information if available
        if 'frequency' in node_data:
            freq_idx = int((node_data['frequency'] % 1000) / 1000 * (self.embedding_dim // 4))
            features[self.embedding_dim // 4 + freq_idx] = node_data['frequency'] / 1000

        # Encode degree (number of neighbors)
        degree = len(neighbors)
        features[self.embedding_dim // 2] = min(1.0, degree / 10)

        # Encode neighbor influence (average of neighbor embeddings if available)
        neighbor_sum = np.zeros(self.embedding_dim // 4)
        neighbor_count = 0
        for n_id in neighbors[:10]:  # Limit to 10 neighbors for efficiency
            if n_id in self.embeddings:
                neighbor_sum += self.embeddings[n_id][:self.embedding_dim // 4]
                neighbor_count += 1

        if neighbor_count > 0:
            features[3 * self.embedding_dim // 4:] = neighbor_sum / neighbor_count

        # Normalize
        norm = np.linalg.norm(features)
        if norm > 0:
            features = features / norm

        return features

    def update_embedding(self, node_id: str, neighbors: List[str], node_data: Dict[str, Any]):
        """Update embedding for a single node."""
        self.embeddings[node_id] = self.compute_simple_embedding(node_id, neighbors, node_data)
        self.dirty_nodes.discard(node_id)

    def mark_dirty(self, node_id: str):
        """Mark a node's embedding as needing recomputation."""
        self.dirty_nodes.add(node_id)

    def get_embedding(self, node_id: str) -> Optional[np.ndarray]:
        """Get the embedding for a node."""
        return self.embeddings.get(node_id)

    def compute_distance(self, node_id1: str, node_id2: str) -> float:
        """
        Compute approximate distance between two nodes using embeddings.
        Returns L2 distance in embedding space.
        """
        emb1 = self.embeddings.get(node_id1)
        emb2 = self.embeddings.get(node_id2)

        if emb1 is None or emb2 is None:
            return float('inf')

        return float(np.linalg.norm(emb1 - emb2))

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        return {
            'embedded_nodes': len(self.embeddings),
            'dirty_nodes': len(self.dirty_nodes),
            'embedding_dim': self.embedding_dim,
            'version': self._version,
            'last_compute_time_ms': self._last_compute_time * 1000
        }


# ============================================================================
# AUTO-RECONNAISSANCE SYSTEM (OPTIMIZED)
# ============================================================================

class AutoReconSystem:
    """
    Auto-Reconnaissance system inspired by Anduril Lattice integration.
    Handles entity tracking, proximity alerts, task management, and disposition tracking.

    OPTIMIZATIONS (Scalable Graph Distances):
    - Spatial indexing with k-d tree for O(log n) proximity queries
    - Dirty flag tracking for lazy/incremental updates
    - Cached threat levels and distances
    - Batch operations support
    """

    # Disposition levels (based on MIL-STD-2525)
    DISPOSITION_UNKNOWN = 'UNKNOWN'
    DISPOSITION_PENDING = 'PENDING'
    DISPOSITION_ASSUMED_FRIEND = 'ASSUMED_FRIEND'
    DISPOSITION_FRIEND = 'FRIEND'
    DISPOSITION_NEUTRAL = 'NEUTRAL'
    DISPOSITION_SUSPICIOUS = 'SUSPICIOUS'
    DISPOSITION_HOSTILE = 'HOSTILE'
    DISPOSITION_JOKER = 'JOKER'
    DISPOSITION_FAKER = 'FAKER'

    # Proximity thresholds (nautical miles)
    PROXIMITY_CRITICAL = 1.0    # 1 NM - immediate threat
    PROXIMITY_WARNING = 3.0     # 3 NM - close monitoring
    PROXIMITY_ALERT = 5.0       # 5 NM - standard alert radius
    PROXIMITY_AWARENESS = 10.0  # 10 NM - situational awareness

    # Movement threshold for dirty tracking (degrees)
    MOVEMENT_THRESHOLD = 0.001  # ~100 meters

    def __init__(self, cache_ttl: int = 120):
        """Initialize the Auto-Recon system"""
        self.entities: Dict[str, Dict[str, Any]] = {}
        self.tasks: Dict[str, Dict[str, Any]] = {}
        self.cache_ttl = cache_ttl  # Time-to-live for entity cache in seconds
        self.reference_point = {'lat': 37.7749, 'lon': -122.4194}  # Default: San Francisco
        self.active = True
        self._task_counter = 0
        self._entity_counter = 0
        self.start_time = time.monotonic()

        # Optimization: Spatial index for O(log n) proximity queries
        self._spatial_index = SpatialIndex()

        # Optimization: Track dirty entities for incremental updates
        self._dirty_entities: Set[str] = set()
        self._last_positions: Dict[str, Tuple[float, float]] = {}

        # Optimization: Cache for computed values
        self._cached_alerts: List[Dict[str, Any]] = []
        self._alerts_cache_valid = False
        self._last_reference_point = self.reference_point.copy()

        # Optimization: Graph embedding cache
        self._embedding_cache = GraphEmbeddingCache(embedding_dim=32)

        # Performance tracking
        self._update_count = 0
        self._query_count = 0

        # Initialize with sample entities
        self._generate_sample_entities()
        self._rebuild_spatial_index()

        logger.info(f"AutoReconSystem initialized with {len(self.entities)} sample entities (spatial index: {SCIPY_AVAILABLE})")

    def _generate_sample_entities(self):
        """Generate sample entities for demo purposes"""
        sample_entities = [
            {'name': 'ALPHA-01', 'lat': 37.80, 'lon': -122.45, 'disposition': self.DISPOSITION_FRIEND, 'ontology': 'aircraft.fixed_wing.patrol'},
            {'name': 'BRAVO-02', 'lat': 37.75, 'lon': -122.38, 'disposition': self.DISPOSITION_SUSPICIOUS, 'ontology': 'vessel.surface.unknown'},
            {'name': 'CHARLIE-03', 'lat': 37.82, 'lon': -122.50, 'disposition': self.DISPOSITION_NEUTRAL, 'ontology': 'vessel.surface.cargo'},
            {'name': 'DELTA-04', 'lat': 37.68, 'lon': -122.42, 'disposition': self.DISPOSITION_HOSTILE, 'ontology': 'vessel.surface.fast_attack'},
            {'name': 'ECHO-05', 'lat': 37.78, 'lon': -122.35, 'disposition': self.DISPOSITION_UNKNOWN, 'ontology': 'aircraft.rotary_wing.unknown'},
            {'name': 'FOXTROT-06', 'lat': 37.72, 'lon': -122.48, 'disposition': self.DISPOSITION_FRIEND, 'ontology': 'vessel.surface.patrol'},
            {'name': 'GOLF-07', 'lat': 37.85, 'lon': -122.40, 'disposition': self.DISPOSITION_PENDING, 'ontology': 'vessel.subsurface.unknown'},
            {'name': 'HOTEL-08', 'lat': 37.70, 'lon': -122.52, 'disposition': self.DISPOSITION_NEUTRAL, 'ontology': 'vessel.surface.fishing'},
        ]

        for entity in sample_entities:
            entity_id = f"ENTITY-{self._entity_counter:04d}"
            self._entity_counter += 1

            # Calculate distance from reference point
            distance = self._haversine_distance(
                self.reference_point['lat'], self.reference_point['lon'],
                entity['lat'], entity['lon']
            )

            # Calculate bearing from reference point
            bearing = self._calculate_bearing(
                self.reference_point['lat'], self.reference_point['lon'],
                entity['lat'], entity['lon']
            )

            self.entities[entity_id] = {
                'entity_id': entity_id,
                'name': entity['name'],
                'is_live': True,
                'location': {
                    'lat': entity['lat'],
                    'lon': entity['lon'],
                    'altitude_m': random.uniform(0, 10000) if 'aircraft' in entity['ontology'] else 0
                },
                'velocity': {
                    'speed_kts': random.uniform(5, 30),
                    'heading_deg': random.uniform(0, 360)
                },
                'disposition': entity['disposition'],
                'ontology': entity['ontology'],
                'distance_nm': distance,
                'bearing_deg': bearing,
                'threat_level': self._calculate_threat_level(entity['disposition'], distance),
                'last_update': time.time(),
                'created': time.time(),
                'rf_emissions': random.random() > 0.5,
                'iff_response': entity['disposition'] in [self.DISPOSITION_FRIEND, self.DISPOSITION_ASSUMED_FRIEND]
            }

            # Track initial position for dirty detection
            self._last_positions[entity_id] = (entity['lat'], entity['lon'])

    def _rebuild_spatial_index(self):
        """Rebuild the spatial index from all entities."""
        self._spatial_index.build(self.entities)
        self._dirty_entities.clear()
        self._invalidate_alerts_cache()

    def _invalidate_alerts_cache(self):
        """Invalidate the alerts cache."""
        self._alerts_cache_valid = False

    def _check_reference_point_changed(self) -> bool:
        """Check if reference point has changed."""
        if (self._last_reference_point['lat'] != self.reference_point['lat'] or
            self._last_reference_point['lon'] != self.reference_point['lon']):
            self._last_reference_point = self.reference_point.copy()
            return True
        return False

    def _haversine_distance(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """
        Calculate the great-circle distance between two points in nautical miles.
        Uses the Haversine formula.
        """
        R = 3440.065  # Earth's radius in nautical miles

        lat1_rad = math.radians(lat1)
        lat2_rad = math.radians(lat2)
        delta_lat = math.radians(lat2 - lat1)
        delta_lon = math.radians(lon2 - lon1)

        a = math.sin(delta_lat/2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon/2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

        return R * c

    def _calculate_bearing(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Calculate initial bearing from point 1 to point 2 in degrees"""
        lat1_rad = math.radians(lat1)
        lat2_rad = math.radians(lat2)
        delta_lon = math.radians(lon2 - lon1)

        x = math.sin(delta_lon) * math.cos(lat2_rad)
        y = math.cos(lat1_rad) * math.sin(lat2_rad) - math.sin(lat1_rad) * math.cos(lat2_rad) * math.cos(delta_lon)

        bearing = math.degrees(math.atan2(x, y))
        return (bearing + 360) % 360

    def _calculate_threat_level(self, disposition: str, distance_nm: float) -> str:
        """Calculate threat level based on disposition and proximity"""
        if disposition == self.DISPOSITION_HOSTILE:
            if distance_nm < self.PROXIMITY_CRITICAL:
                return 'CRITICAL'
            elif distance_nm < self.PROXIMITY_WARNING:
                return 'HIGH'
            elif distance_nm < self.PROXIMITY_ALERT:
                return 'MEDIUM'
            else:
                return 'LOW'
        elif disposition == self.DISPOSITION_SUSPICIOUS:
            if distance_nm < self.PROXIMITY_WARNING:
                return 'MEDIUM'
            elif distance_nm < self.PROXIMITY_ALERT:
                return 'LOW'
            else:
                return 'MINIMAL'
        elif disposition in [self.DISPOSITION_UNKNOWN, self.DISPOSITION_PENDING]:
            if distance_nm < self.PROXIMITY_CRITICAL:
                return 'MEDIUM'
            elif distance_nm < self.PROXIMITY_ALERT:
                return 'LOW'
            else:
                return 'MINIMAL'
        else:
            return 'NONE'

    def _update_entity_metrics(self, entity_id: str, force: bool = False):
        """Update distance, bearing, and threat level for a single entity."""
        if entity_id not in self.entities:
            return

        entity = self.entities[entity_id]

        # Only update if dirty or forced
        if not force and entity_id not in self._dirty_entities:
            return

        # Validate location data
        if 'location' not in entity or not isinstance(entity['location'], dict) or \
           'lat' not in entity['location'] or 'lon' not in entity['location']:
            # Invalid location data - mark as processed to prevent retry loops
            self._dirty_entities.discard(entity_id)
            return

        try:
            entity['distance_nm'] = self._haversine_distance(
                self.reference_point['lat'], self.reference_point['lon'],
                entity['location']['lat'], entity['location']['lon']
            )
            entity['bearing_deg'] = self._calculate_bearing(
                self.reference_point['lat'], self.reference_point['lon'],
                entity['location']['lat'], entity['location']['lon']
            )
            entity['threat_level'] = self._calculate_threat_level(entity.get('disposition', self.DISPOSITION_UNKNOWN), entity['distance_nm'])
        except Exception as e:
            logger.warning(f"Error updating metrics for entity {entity_id}: {e}")

        self._dirty_entities.discard(entity_id)

    def _update_all_dirty_entities(self):
        """Update metrics for all dirty entities (lazy evaluation)."""
        ref_changed = self._check_reference_point_changed()

        if ref_changed:
            # Reference point changed - need to update all entities
            for entity_id in self.entities:
                self._update_entity_metrics(entity_id, force=True)
            self._rebuild_spatial_index()
        elif self._dirty_entities:
            # Only update dirty entities
            for entity_id in list(self._dirty_entities):
                self._update_entity_metrics(entity_id)

            # Rebuild spatial index if significant changes
            if len(self._dirty_entities) > len(self.entities) * 0.1:
                self._rebuild_spatial_index()

    def get_all_entities(self, include_metrics: bool = True) -> List[Dict[str, Any]]:
        """
        Get all tracked entities.

        OPTIMIZATION: Uses lazy evaluation - only updates dirty entities.
        """
        start = time.perf_counter()
        self._query_count += 1

        if include_metrics:
            self._update_all_dirty_entities()

        result = list(self.entities.values())

        duration_ms = (time.perf_counter() - start) * 1000
        perf_metrics.record('get_all_entities', duration_ms, {'count': len(result)})

        return result

    def get_entity(self, entity_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific entity by ID"""
        entity = self.entities.get(entity_id)
        if entity and entity_id in self._dirty_entities:
            self._update_entity_metrics(entity_id)
        return entity

    def get_entities_batch(self, entity_ids: List[str]) -> List[Dict[str, Any]]:
        """
        Get multiple entities by ID in a single call.

        OPTIMIZATION: Batch API to reduce network round-trips.
        """
        start = time.perf_counter()

        results = []
        for entity_id in entity_ids:
            entity = self.get_entity(entity_id)
            if entity:
                results.append(entity)

        duration_ms = (time.perf_counter() - start) * 1000
        perf_metrics.record('get_entities_batch', duration_ms, {'requested': len(entity_ids), 'found': len(results)})

        return results

    def get_entities_in_proximity(self, radius_nm: float = None) -> List[Dict[str, Any]]:
        """
        Get all entities within a specified radius of the reference point.

        OPTIMIZATION: Uses spatial index for O(log n) query instead of O(n).
        """
        start = time.perf_counter()

        if radius_nm is None:
            radius_nm = self.PROXIMITY_ALERT

        # Ensure spatial index is up to date
        if self._spatial_index.is_dirty or self._dirty_entities:
            self._update_all_dirty_entities()
            if self._spatial_index.is_dirty:
                self._rebuild_spatial_index()

        # Use spatial index for efficient query
        results_with_dist = self._spatial_index.query_radius(
            self.reference_point['lat'],
            self.reference_point['lon'],
            radius_nm
        )

        # Build result list with full entity data
        proximate = []
        for entity_id, distance in results_with_dist:
            if entity_id in self.entities:
                entity = self.entities[entity_id].copy()
                entity['distance_nm'] = distance  # Use exact distance from spatial query
                proximate.append(entity)

        duration_ms = (time.perf_counter() - start) * 1000
        perf_metrics.record('get_entities_in_proximity', duration_ms,
                           {'radius_nm': radius_nm, 'result_count': len(proximate)})

        return proximate

    def get_nearest_entities(self, k: int = 10) -> List[Dict[str, Any]]:
        """
        Get the k nearest entities to the reference point.

        OPTIMIZATION: Uses spatial index for O(log n) query.
        """
        start = time.perf_counter()

        if self._spatial_index.is_dirty:
            self._rebuild_spatial_index()

        results_with_dist = self._spatial_index.query_nearest(
            self.reference_point['lat'],
            self.reference_point['lon'],
            k
        )

        nearest = []
        for entity_id, distance in results_with_dist:
            if entity_id in self.entities:
                entity = self.entities[entity_id].copy()
                entity['distance_nm'] = distance
                nearest.append(entity)

        duration_ms = (time.perf_counter() - start) * 1000
        perf_metrics.record('get_nearest_entities', duration_ms, {'k': k, 'result_count': len(nearest)})

        return nearest

    def get_entities_by_disposition(self, disposition: str) -> List[Dict[str, Any]]:
        """Get entities filtered by disposition"""
        return [e for e in self.get_all_entities() if e['disposition'] == disposition]

    def get_proximity_alerts(self) -> List[Dict[str, Any]]:
        """
        Get all proximity alerts based on threat level.

        OPTIMIZATION: Cached results, invalidated when entities change.
        """
        start = time.perf_counter()

        # Check if cache is still valid
        if self._alerts_cache_valid and not self._dirty_entities:
            perf_metrics.record('get_proximity_alerts_cached', 0.01)
            return self._cached_alerts

        # Rebuild alerts
        alerts = []
        for entity in self.get_all_entities():
            if entity['threat_level'] in ['CRITICAL', 'HIGH', 'MEDIUM']:
                alerts.append({
                    'entity_id': entity['entity_id'],
                    'name': entity['name'],
                    'disposition': entity['disposition'],
                    'distance_nm': entity['distance_nm'],
                    'bearing_deg': entity['bearing_deg'],
                    'threat_level': entity['threat_level'],
                    'alert_type': 'PROXIMITY',
                    'location': entity['location'],
                    'timestamp': time.time()
                })

        # Sort by threat level and distance
        threat_order = {'CRITICAL': 0, 'HIGH': 1, 'MEDIUM': 2}
        alerts.sort(key=lambda x: (threat_order.get(x['threat_level'], 99), x['distance_nm']))

        # Cache the result
        self._cached_alerts = alerts
        self._alerts_cache_valid = True

        duration_ms = (time.perf_counter() - start) * 1000
        perf_metrics.record('get_proximity_alerts', duration_ms, {'alert_count': len(alerts)})

        return alerts

    def set_reference_point(self, lat: float, lon: float):
        """Set the reference point for proximity calculations"""
        self.reference_point = {'lat': lat, 'lon': lon}
        self._invalidate_alerts_cache()
        # Mark all entities dirty since distances need recalculation
        self._dirty_entities = set(self.entities.keys())
        logger.info(f"Reference point set to: {lat}, {lon}")

    # ========================================================================
    # TASK MANAGEMENT
    # ========================================================================

    def create_task(self, entity_id: str, task_type: str = 'INVESTIGATE',
                   asset_id: str = None, priority: int = 5) -> Dict[str, Any]:
        """Create a new investigation/tracking task for an entity"""
        if entity_id not in self.entities:
            return {'status': 'error', 'message': f'Entity {entity_id} not found'}

        entity = self.entities[entity_id]
        task_id = f"TASK-{self._task_counter:04d}"
        self._task_counter += 1

        task = {
            'task_id': task_id,
            'entity_id': entity_id,
            'entity_name': entity['name'],
            'task_type': task_type,
            'status': 'ASSIGNED',
            'priority': priority,
            'asset_id': asset_id or f"ASSET-{random.randint(1, 10):02d}",
            'created': time.time(),
            'updated': time.time(),
            'target_location': entity['location'].copy(),
            'notes': f"Auto-generated task for {task_type} of {entity['name']}"
        }

        self.tasks[task_id] = task
        logger.info(f"Created task {task_id} for entity {entity_id}")
        return {'status': 'ok', 'task': task}

    def get_all_tasks(self) -> List[Dict[str, Any]]:
        """Get all tasks"""
        return list(self.tasks.values())

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific task"""
        return self.tasks.get(task_id)

    def update_task_status(self, task_id: str, status: str) -> Dict[str, Any]:
        """Update task status"""
        if task_id not in self.tasks:
            return {'status': 'error', 'message': f'Task {task_id} not found'}

        valid_statuses = ['PENDING', 'ASSIGNED', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED']
        if status not in valid_statuses:
            return {'status': 'error', 'message': f'Invalid status. Must be one of: {valid_statuses}'}

        self.tasks[task_id]['status'] = status
        self.tasks[task_id]['updated'] = time.time()

        return {'status': 'ok', 'task': self.tasks[task_id]}

    def update_entity_disposition(self, entity_id: str, disposition: str) -> Dict[str, Any]:
        """Update an entity's disposition"""
        if entity_id not in self.entities:
            return {'status': 'error', 'message': f'Entity {entity_id} not found'}

        valid_dispositions = [
            self.DISPOSITION_UNKNOWN, self.DISPOSITION_PENDING,
            self.DISPOSITION_ASSUMED_FRIEND, self.DISPOSITION_FRIEND,
            self.DISPOSITION_NEUTRAL, self.DISPOSITION_SUSPICIOUS,
            self.DISPOSITION_HOSTILE, self.DISPOSITION_JOKER, self.DISPOSITION_FAKER
        ]

        if disposition not in valid_dispositions:
            return {'status': 'error', 'message': f'Invalid disposition. Must be one of: {valid_dispositions}'}

        old_disposition = self.entities[entity_id]['disposition']
        self.entities[entity_id]['disposition'] = disposition
        self.entities[entity_id]['last_update'] = time.time()

        # Recalculate threat level
        distance = self.entities[entity_id]['distance_nm']
        self.entities[entity_id]['threat_level'] = self._calculate_threat_level(disposition, distance)

        # Invalidate alerts cache since disposition affects threat level
        self._invalidate_alerts_cache()

        logger.info(f"Entity {entity_id} disposition changed: {old_disposition} -> {disposition}")
        return {'status': 'ok', 'entity': self.entities[entity_id]}

    def simulate_entity_movement(self):
        """
        Simulate entity movement for demo purposes.

        OPTIMIZATION: Only marks moved entities as dirty, tracks movement threshold.
        """
        start = time.perf_counter()
        moved_count = 0

        for entity_id, entity in self.entities.items():
            # Random small movement
            delta_lat = (random.random() - 0.5) * 0.01
            delta_lon = (random.random() - 0.5) * 0.01

            new_lat = entity['location']['lat'] + delta_lat
            new_lon = entity['location']['lon'] + delta_lon

            # Check if movement exceeds threshold
            old_pos = self._last_positions.get(entity_id, (entity['location']['lat'], entity['location']['lon']))
            movement = abs(new_lat - old_pos[0]) + abs(new_lon - old_pos[1])

            if movement > self.MOVEMENT_THRESHOLD:
                self._dirty_entities.add(entity_id)
                self._last_positions[entity_id] = (new_lat, new_lon)
                moved_count += 1

            entity['location']['lat'] = new_lat
            entity['location']['lon'] = new_lon

            # Update velocity heading
            entity['velocity']['heading_deg'] = random.uniform(0, 360)
            entity['velocity']['speed_kts'] = max(0, entity['velocity']['speed_kts'] + (random.random() - 0.5) * 2)
            entity['last_update'] = time.time()

        # Mark spatial index as dirty
        self._spatial_index.mark_dirty()
        self._invalidate_alerts_cache()

        self._update_count += 1

        duration_ms = (time.perf_counter() - start) * 1000
        perf_metrics.record('simulate_entity_movement', duration_ms,
                           {'total': len(self.entities), 'moved': moved_count})

        return {'status': 'ok', 'updated': len(self.entities), 'significantly_moved': moved_count}

    def get_changed_entities(self, since_timestamp: float = None) -> List[Dict[str, Any]]:
        """
        Get entities that have changed since a given timestamp.

        OPTIMIZATION: For incremental frontend updates - only send changed data.
        """
        if since_timestamp is None:
            since_timestamp = time.time() - 60  # Default: last minute

        changed = []
        for entity in self.entities.values():
            if entity['last_update'] > since_timestamp:
                changed.append(entity)

        return changed

    def get_status(self) -> Dict[str, Any]:
        """Get system status summary with performance metrics."""
        disposition_counts = {}
        for entity in self.entities.values():
            disp = entity.get('disposition', self.DISPOSITION_UNKNOWN)
            disposition_counts[disp] = disposition_counts.get(disp, 0) + 1

        task_status_counts = {}
        for task in self.tasks.values():
            status = task['status']
            task_status_counts[status] = task_status_counts.get(status, 0) + 1

        alerts = self.get_proximity_alerts()

        return {
            'active': self.active,
            'entity_count': len(self.entities),
            'task_count': len(self.tasks),
            'alert_count': len(alerts),
            'disposition_breakdown': disposition_counts,
            'task_status_breakdown': task_status_counts,
            'reference_point': self.reference_point,
            'uptime': time.monotonic() - self.start_time,
            'performance': {
                'dirty_entities': len(self._dirty_entities),
                'alerts_cache_valid': self._alerts_cache_valid,
                'spatial_index': self._spatial_index.get_stats(),
                'update_count': self._update_count,
                'query_count': self._query_count
            }
        }


# ============================================================================
# OWL-RL SCOPED / INCREMENTAL MATERIALIZATION HELPERS
# ============================================================================

Json_owlrl = Dict[str, Any]

def _edge_obs_class(e: Json_owlrl) -> str:
    return (e.get("metadata") or {}).get("obs_class", "observed")

def _is_inferred_edge(e: Json_owlrl) -> bool:
    k = (e.get("kind") or "")
    return k.startswith("INFERRED_") or _edge_obs_class(e) == "inferred"

def _node_last_seen_ts(n: Json_owlrl) -> float:
    """Robust 'activity' timestamp for nodes.

    Handles both epoch-float strings and ISO-8601 strings
    (e.g. '2026-04-12T14:11:15.787Z' or '2026-04-12 14:11:15').
    """
    from datetime import datetime, timezone
    meta = n.get("metadata") or {}
    labels = n.get("labels") or {}
    candidates = [
        n.get("updated_at"),
        n.get("created_at"),
        meta.get("last_seen"),
        labels.get("last_seen"),
        labels.get("ts"),
    ]
    best = 0.0
    for v in candidates:
        if v is None:
            continue
        try:
            best = max(best, float(v))
            continue
        except (ValueError, TypeError):
            pass
        # Try ISO-8601 string
        try:
            s = str(v).rstrip('Z')
            # Replace trailing timezone offset if present
            if '+' in s[10:]:
                s = s[:s.rindex('+')]
            elif s.endswith(('-05:00', '-06:00', '+00:00', '+01:00')):
                s = s[:-6]
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                epoch = dt.replace(tzinfo=timezone.utc).timestamp()
            else:
                epoch = dt.timestamp()
            best = max(best, epoch)
        except Exception:
            continue
    return best

# Edges that matter for OWL property chains / rules
_DEFAULT_REASONING_EDGE_KINDS: Set[str] = {
    "SESSION_OBSERVED_HOST",
    "SESSION_OBSERVED_FLOW",
    "HOST_GEO_ESTIMATE",
    "HOST_IN_ASN",
    "ASN_IN_ORG",
    "FLOW_DST_PORT",
    "FLOW_QUERIED_DNS",
    "FLOW_TLS_SNI",
    "FLOW_HTTP_HOST",
    "PORT_IMPLIED_SERVICE",
}

def select_reasoning_view_incremental(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    *,
    window_minutes: int = 10,
    pcap_session_id: Optional[str] = None,
    depth: int = 2,
    max_nodes: int = 25_000,
    max_edges: int = 200_000,
    allowed_edge_kinds: Optional[Set[str]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Build a smaller reasoning view for incremental OWL-RL.

    Seeds = flows active within window (and optionally belonging to pcap_session_id).
    Expands neighborhood up to *depth* using allowed ontology-relevant edge kinds.
    Excludes INFERRED_* edges from input (prevents self-supporting inference spirals).
    Dedup is still against the *full graph* existing_edge_ids.

    Returns: (nodes_view, edges_view, scope_stats)
    """
    t0 = time.time()
    allowed = allowed_edge_kinds or _DEFAULT_REASONING_EDGE_KINDS
    since_ts = t0 - float(window_minutes) * 60.0

    node_by_id: Dict[str, Dict[str, Any]] = {n.get("id"): n for n in nodes if n.get("id")}
    # Only keep non-inferred edges as input to the reasoner
    base_edges: List[Dict[str, Any]] = [e for e in edges if not _is_inferred_edge(e)]
    edge_by_id: Dict[str, Dict[str, Any]] = {e.get("id"): e for e in base_edges if e.get("id")}

    # adjacency for fast neighborhood expansion
    adj: Dict[str, List[str]] = defaultdict(list)  # node_id -> [edge_id...]
    for e in base_edges:
        eid = e.get("id")
        if not eid:
            continue
        for nid in (e.get("nodes") or []):
            if nid:
                adj[nid].append(eid)

    # --- seed flows ---
    active_flows: Set[str] = set()

    # (A) time-window flows by node activity
    for nid, n in node_by_id.items():
        k = n.get("kind")
        if k in ("flow", "dflow"):
            if _node_last_seen_ts(n) >= since_ts:
                active_flows.add(nid)

    # (B) time-window flows by edge timestamps touching flows
    for e in base_edges:
        k = e.get("kind")
        if k and k.startswith("FLOW_"):
            try:
                if float(e.get("timestamp", 0.0)) >= since_ts:
                    ns = e.get("nodes") or []
                    if ns:
                        active_flows.add(ns[0])  # by convention: flow is nodes[0]
            except Exception:
                pass

    # (C) optionally restrict to flows in a given pcap_session
    if pcap_session_id:
        session_flows: Set[str] = set()
        for e in base_edges:
            if e.get("kind") == "SESSION_OBSERVED_FLOW":
                ns = e.get("nodes") or []
                if len(ns) == 2 and ns[0] == pcap_session_id:
                    session_flows.add(ns[1])
        # intersection if we already have a time window; if empty window, fall back to session
        if active_flows:
            active_flows &= session_flows
        else:
            active_flows = session_flows

    # --- BFS expand ---
    picked_nodes: Set[str] = set()
    picked_edges: Set[str] = set()

    q = deque((fid, 0) for fid in active_flows if fid in node_by_id)

    while q and len(picked_nodes) < max_nodes and len(picked_edges) < max_edges:
        nid, d = q.popleft()
        if nid in picked_nodes:
            continue
        picked_nodes.add(nid)

        if d >= depth:
            continue

        for eid in adj.get(nid, []):
            if eid in picked_edges:
                continue
            e = edge_by_id.get(eid)
            if not e:
                continue
            if e.get("kind") not in allowed:
                continue

            picked_edges.add(eid)

            for nn in (e.get("nodes") or []):
                if nn and nn not in picked_nodes:
                    if nn in node_by_id:
                        q.append((nn, d + 1))

    nodes_view = [node_by_id[nid] for nid in picked_nodes if nid in node_by_id]
    edges_view = [edge_by_id[eid] for eid in picked_edges if eid in edge_by_id]

    scope = {
        "mode": "incremental",
        "window_minutes": window_minutes,
        "since_ts": since_ts,
        "pcap_session_id": pcap_session_id,
        "depth": depth,
        "seed_flow_count": len(active_flows),
        "nodes_view": len(nodes_view),
        "edges_view": len(edges_view),
        "t_build_s": round(time.time() - t0, 4),
    }
    return nodes_view, edges_view, scope


# ============================================================================
# CREATE FLASK APP
# ============================================================================

# Import POI Manager
try:
    from poi_manager import POIManager
    POI_MANAGER_AVAILABLE = True
except ImportError:
    POI_MANAGER_AVAILABLE = False
    logger.warning("POI Manager not available - POI features disabled")

# Import Operator Session Manager
try:
    from operator_session_manager import (
        get_session_manager,
        OperatorRole,
        EntityEventType,
        Provenance
    )
    OPERATOR_MANAGER_AVAILABLE = True
except ImportError:
    OPERATOR_MANAGER_AVAILABLE = False
    logger.warning("Operator Session Manager not available - multi-user features disabled")

if FLASK_AVAILABLE:
    # Create Flask app
    app = Flask(__name__, static_folder='.')
    CORS(app)  # Enable CORS for all routes

    # WSGI middleware to proactively reject websocket upgrade attempts to
    # the socket.io endpoint when running under the development server.
    # This prevents low-level websocket handshake errors from reaching
    # the engineio/simple_websocket stack and triggering WSGI write() before
    # start_response assertions when clients send malformed upgrade headers.
    def _disable_ws_upgrades_middleware(wsgi_app):
        def _middleware(environ, start_response):
            path = environ.get('PATH_INFO', '') or ''
            qs = environ.get('QUERY_STRING', '') or ''
            if path.startswith('/socket.io') and 'transport=websocket' in qs:
                start_response('400 Bad Request', [('Content-Type', 'application/json')])
                return [b'{"status":"error","message":"WebSocket transport disabled; use polling"}']
            return wsgi_app(environ, start_response)
        return _middleware

    app.wsgi_app = _disable_ws_upgrades_middleware(app.wsgi_app)

    # Secure API Error Handling (prevent HTML leaks)
    @app.errorhandler(404)
    def handle_404(e):
        if request.path.startswith('/api/'):
            return jsonify({
                "status": "error",
                "error": "Not Found",
                "message": f"API endpoint {request.path} not found"
            }), 404
        return f"Not Found: {request.path}", 404

    @app.errorhandler(500)
    def handle_500(e):
        # Always return JSON for 500s to avoid breaking JSON parsers
        return jsonify({
            "status": "error",
            "error": "Internal Server Error",
            "message": str(e)
        }), 500

    # Initialize SocketIO for WebSocket support
    socketio = None
    if SOCKETIO_AVAILABLE:
        # Prefer eventlet for production-like websocket support when available.
        try:
            # eventlet already monkey-patched at module import; avoid re-patching here
            socketio = SocketIO(
                app,
                cors_allowed_origins="*",
                async_mode='eventlet',
                ping_timeout=60,
                ping_interval=20,
                engineio_options={'allow_upgrades': True}
            )
            logger.info("WebSocket support enabled via Flask-SocketIO (eventlet)")
        except Exception:
            # Fallback: threading + polling-only to avoid engineio websocket
            # upgrade path when eventlet/gevent are not installed.
            socketio = SocketIO(
                app,
                cors_allowed_origins="*",
                async_mode='threading',
                ping_timeout=60,
                ping_interval=20,
                engineio_options={'allow_upgrades': False}
            )
            logger.info("WebSocket support enabled via Flask-SocketIO (polling only)")
    else:
        logger.warning("WebSocket support not available - SSE only mode")

    # Global stores
    hypergraph_store = RFHypergraphStore()
    nmap_scanner = NmapScanner()
    ndpi_analyzer = NDPIAnalyzer()
    ais_tracker = AISTracker()
    recon_system = AutoReconSystem()

    # ── MapStateCache — SQLite-backed arc + geo-path + camera persistence ────
    # Survives orchestrator restarts; zero extra dependencies.
    try:
        from map_cache import MapStateCache
        map_cache = MapStateCache(db_path=os.path.join(_data_dir(), 'map_cache.db'))
        map_cache.vacuum_all()   # clean stale entries on every restart
    except Exception as _mc_exc:
        map_cache = None
        logger.warning(f'[MapCache] init failed (non-fatal): {_mc_exc}')

    # ── MapTileCache — Server-side persistent tile proxy ─────────────────────
    try:
        from map_tile_cache import MapTileCache
        map_tile_cache = MapTileCache(cache_dir=os.path.join(_data_dir(), 'map_tiles'))
    except Exception as _mtc_exc:
        map_tile_cache = None
        logger.warning(f'[TileCache] init failed: {_mtc_exc}')

    def _map_maintenance_loop():
        """Periodic background maintenance for map metadata and tile caches."""
        while True:
            time.sleep(3600)  # Hourly
            try:
                if map_cache:
                    map_cache.vacuum_all()
                    map_cache.backup_db()
                if map_tile_cache:
                    map_tile_cache.vacuum()
            except Exception as e:
                logger.error(f'[MapMaintenance] error: {e}')

    threading.Thread(target=_map_maintenance_loop, daemon=True, name='map-maintenance').start()

    # Graph event bus (optional Redis-backed durable log)
    try:
        from graph_event_bus import GraphEventBus
    except Exception:
        GraphEventBus = None

    redis_client = None
    redis_url = os.environ.get('OP_SESSION_REDIS_URL') or os.environ.get('REDIS_URL')
    if redis_url:
        try:
            import redis as _redis
            redis_client = _redis.from_url(redis_url, decode_responses=True)
            redis_client.ping()
            logger.info(f"Connected to Redis for GraphEventBus: {redis_url}")
        except Exception as e:
            logger.warning(f"Redis for GraphEventBus not available: {e}")

    if GraphEventBus:
        graph_event_bus = GraphEventBus(redis_client=redis_client, stream_key='graph:events')
        # inject into hypergraph and recon system if they support it
        try:
            hypergraph_store.event_bus = graph_event_bus
        except Exception:
            pass
        try:
            recon_system.event_bus = graph_event_bus
        except Exception:
            pass
        # Optional: create a memory-resident HypergraphEngine and subscribe it to GraphEventBus
        try:
            from hypergraph_engine import HypergraphEngine, RFHypergraphAdapter
            decay_val = float(os.environ.get('HYPERGRAPH_DECAY_LAMBDA', 0) or 0)
            if decay_val:
                logger.info(f"Initializing HypergraphEngine with decay_lambda={decay_val}")
            hypergraph_engine = HypergraphEngine(decay_lambda=decay_val)
            logger.info('HypergraphEngine initialized')
        except Exception as _hg_exc:
            hypergraph_engine = None
            logger.error(f'HypergraphEngine init failed: {_hg_exc}', exc_info=True)

        if hypergraph_engine is not None:
            # attach event_bus reference
            hypergraph_engine.event_bus = graph_event_bus
            # subscribe engine to incoming graph events
            try:
                graph_event_bus.subscribe(hypergraph_engine.apply_graph_event)
                logger.info('HypergraphEngine subscribed to GraphEventBus')
            except Exception:
                logger.debug('Could not subscribe HypergraphEngine to GraphEventBus')

            # ── MapStateCache subscription — persists edges as they flow ────────
            if map_cache is not None:
                def _cache_node_event(ev, _rs=recon_system, _mc=map_cache):
                    """Subscriber: upsert node geo coords into the persistent index.

                    Fires on NODE_CREATE/NODE_UPDATE — coordinates are extracted from
                    entity_data using the same field priority as the recon bridge.
                    Stores with confidence=1.0 (observed) so arc persist can resolve
                    coords without touching recon_system.nodes on the hot path.
                    """
                    try:
                        et = getattr(ev, 'event_type', '') or getattr(ev, 'type', '')
                        if 'NODE' not in et.upper():
                            return
                        eid  = getattr(ev, 'entity_id', None)
                        if not eid:
                            return
                        data = getattr(ev, 'entity_data', None) or getattr(ev, 'payload', {}) or {}
                        loc  = data.get('location') or {}
                        pos  = data.get('position')
                        if pos and len(pos) >= 2 and not loc:
                            loc = {'lat': pos[0], 'lon': pos[1]}
                        lat = loc.get('lat') or data.get('lat') or data.get('latitude')
                        lon = loc.get('lon') or data.get('lon') or data.get('longitude')
                        if lat is None or lon is None:
                            return
                        asn  = (data.get('labels', {}).get('asn', '') if isinstance(data.get('labels'), dict)
                                else data.get('asn', '')) or None
                        _mc.upsert_node_geo(eid, float(lat), float(lon), asn=asn,
                                            confidence=1.0, method='observed')
                    except Exception:
                        pass

                def _cache_edge_event(ev, _rs=recon_system, _mc=map_cache):
                    """Subscriber: persist EDGE_CREATE/EDGE_UPDATE to SQLite cache.

                    Coordinate resolution order (fastest → most expensive):
                      1. node_geo_index (persistent, survives restarts — preferred)
                      2. recon_system.nodes (in-memory, fast but ephemeral)
                    Drops the arc silently if neither source has coordinates.
                    """
                    try:
                        et = getattr(ev, 'event_type', '') or getattr(ev, 'type', '')
                        if 'EDGE' not in et.upper():
                            return
                        pl = getattr(ev, 'payload', None) or getattr(ev, 'entity_data', {}) or {}
                        nodes_list = pl.get('nodes') or []
                        src  = pl.get('src') or pl.get('source') or (nodes_list[0]  if len(nodes_list) > 0 else None)
                        dst  = pl.get('dst') or pl.get('target') or (nodes_list[-1] if len(nodes_list) > 1 else None)
                        if not src or not dst:
                            return
                        eid  = getattr(ev, 'entity_id', None) or f'{src}::{dst}'
                        conf = float(pl.get('confidence') or pl.get('weight') or 0.5)
                        entr = float(pl.get('entropy') or 0.5)
                        rfc  = float(pl.get('rf_corr')  or 0.0)
                        shad = int(bool(pl.get('shadow') or False))
                        kind = pl.get('kind') or pl.get('edge_type') or 'FLOW'
                        # Anomaly score flows from geo inference engine if present
                        anom = float(pl.get('anomaly_score') or pl.get('anomaly') or 0.0)

                        # ── Coordinate resolution: geo index first ───────────────
                        # Try the persistent index — works across restarts
                        if _mc.persist_arc_by_ids(eid, src, dst, conf, entr, rfc, shad, kind,
                                                   anomaly_score=anom):
                            return

                        # Fall back to in-memory recon_system.nodes
                        def _ll(nd):
                            lat = nd.get('lat') or nd.get('latitude')
                            lon = nd.get('lon') or nd.get('longitude')
                            if lat is None:
                                pos = nd.get('position') or []
                                lat = pos[0] if len(pos) > 0 else None
                                lon = pos[1] if len(pos) > 1 else None
                            return lat, lon

                        sn = _rs.nodes.get(src) or {}
                        dn = _rs.nodes.get(dst) or {}
                        sl, slon = _ll(sn)
                        dl, dlon = _ll(dn)
                        if sl is None or dl is None:
                            # Last resort: try neighbor inference from geo index
                            neighbors = [n for n in (nodes_list or [src, dst]) if n not in (src, dst)]
                            for nid, coord_fn in [(src, lambda: _mc.infer_neighbor_geo(src, neighbors + [dst])),
                                                  (dst, lambda: _mc.infer_neighbor_geo(dst, neighbors + [src]))]:
                                result = coord_fn()
                                if result:
                                    _mc.upsert_node_geo(nid, result[0], result[1],
                                                        confidence=result[2], method='neighbor_inferred')
                            # Re-attempt persist after inference
                            _mc.persist_arc_by_ids(eid, src, dst, conf, entr, rfc, shad, kind,
                                                    anomaly_score=anom)
                            return
                        # Coords found in recon_system — also populate the geo index for future use
                        _mc.upsert_node_geo(src, float(sl), float(slon), confidence=0.95, method='recon_fallback')
                        _mc.upsert_node_geo(dst, float(dl), float(dlon), confidence=0.95, method='recon_fallback')
                        _mc.persist_arc(eid, src, float(sl), float(slon),
                                        dst, float(dl), float(dlon),
                                        conf, entr, rfc, shad, kind,
                                        anomaly_score=anom)
                    except Exception:
                        pass

                try:
                    graph_event_bus.subscribe(_cache_node_event)
                    graph_event_bus.subscribe(_cache_edge_event)
                    logger.info('[MapCache] node geo + edge persistence subscribed to GraphEventBus')
                except Exception as _sub_exc:
                    logger.debug(f'[MapCache] GraphEventBus subscription failed: {_sub_exc}')

            # ── T3-A + T3-B: Graph event drain queue ────────────────────────────
            # Both the DuckDB delta bus and Socket.IO push must NOT run inline
            # under GraphEventBus.publish()'s lock — a blocking write/emit would
            # stall all concurrent publishers (see graph_event_bus.py:53-68).
            # Solution: a single bounded queue drained by one background thread.
            # DuckDB ref is looked up lazily (globals()['_duck_store']) so it
            # picks up the final per-instance store set by _post_startup().
            try:
                import queue as _queue_mod
                from scene_duckdb_store import TacticalEvent as _TEv
                _graph_event_q: _queue_mod.Queue = _queue_mod.Queue(maxsize=2000)
                globals()['_graph_event_q'] = _graph_event_q  # T4-2: expose for /api/health/queues

                # T5: timestamp of last 3D field push (throttled to ≤1/3 s)
                _rf3d_dirty  = [False]   # set True by drain thread when rf emitters change
                _rf3d_cached = [None]    # latest encoded snapshot, read by /api/rf/field3d

                def _graph_event_drain(_q=_graph_event_q, _TEv=_TEv, _sio=socketio):
                    """Background thread: drain graph events → DuckDB + Socket.IO."""
                    while True:
                        try:
                            ev = _q.get(timeout=2)
                        except _queue_mod.Empty:
                            continue
                        try:
                            et = getattr(ev, 'event_type', '') or getattr(ev, 'type', '')
                            eid = getattr(ev, 'entity_id', '') or getattr(ev, 'id', '') or ''
                            seq = int(getattr(ev, 'sequence_id', 0) or 0)
                            data = getattr(ev, 'entity_data', None) or getattr(ev, 'payload', {}) or {}
                            loc = data.get('location') or {}
                            lat = float(loc.get('lat') or data.get('lat') or 0.0)
                            lon = float(loc.get('lon') or data.get('lon') or 0.0)
                            alt = float(loc.get('alt') or data.get('alt') or 0.0)

                            # T3-A: DuckDB delta log — lazily resolved after _post_startup()
                            ds = globals().get('_duck_store')
                            if ds is not None:
                                try:
                                    ds.append(_TEv(
                                        timestamp=int(time.time() * 1000),
                                        event_type=et,
                                        entity_id=eid,
                                        session_id='global',
                                        lat=lat, lon=lon, alt=alt,
                                        payload=data,
                                        seq=seq,
                                    ))
                                except Exception:
                                    pass

                            # T3-B: Socket.IO push to all connected clients
                            if _sio is not None:
                                try:
                                    _sio.emit('graph_event', {
                                        'event_type': et,
                                        'entity_id': eid,
                                        'entity_kind': getattr(ev, 'entity_kind', '') or getattr(ev, 'entity_type', ''),
                                        'sequence_id': seq,
                                        'data': data,
                                    }, namespace='/')
                                except Exception:
                                    pass

                            # T4-4: rf_field_update push for rf_node events
                            ekind = (getattr(ev, 'entity_kind', '') or getattr(ev, 'entity_type', '') or '').lower()
                            if 'rf' in ekind and _sio is not None:
                                try:
                                    from rf_field_generator import get_field_snapshot as _rff
                                    snap = _rff()
                                    _sio.emit('rf_field_update', snap, namespace='/')
                                except Exception:
                                    pass
                                # T5: mark 3D field dirty — worker thread regenerates asynchronously
                                _rf3d_dirty[0] = True
                        except Exception:
                            pass
                        finally:
                            _q.task_done()

                _drain_thread = threading.Thread(target=_graph_event_drain, daemon=True, name='graph-event-drain')
                _drain_thread.start()

                # T5: 3D RF field worker — polls _rf3d_dirty every 3 s, generates
                # snapshot off the drain thread, caches it for /api/rf/field3d + push
                def _rf3d_worker(_sio=socketio,
                                 _dirty=_rf3d_dirty, _cache=_rf3d_cached):
                    while True:
                        time.sleep(3.0)
                        if not _dirty[0]:
                            continue
                        _dirty[0] = False
                        try:
                            from rf_field_generator import get_field3d_snapshot as _g3d
                            snap3d = _g3d()
                            _cache[0] = snap3d
                            if _sio:
                                _sio.emit('rf_field3d_update', snap3d, namespace='/')
                        except Exception as _e3d_err:
                            logger.warning('[RFVol] worker error: %s', _e3d_err)

                threading.Thread(target=_rf3d_worker, daemon=True,
                                 name='rf3d-worker').start()

                # T4-2: Backpressure counters — exposed via /api/health/queues
                _graph_event_drops = [0]          # list so closure can mutate
                globals()['_graph_event_drops'] = _graph_event_drops  # expose for health endpoint
                _QUEUE_CAPACITY    = 2000
                _QUEUE_WARN_LEVEL  = int(_QUEUE_CAPACITY * 0.80)  # 80% watermark
                _bp_log_counter    = [0]          # throttle: log once per 250 events above watermark

                def _graph_event_enqueue(ev, _q=_graph_event_q):
                    """Fast subscriber: drop event into queue without blocking the bus."""
                    try:
                        _q.put_nowait(ev)
                        depth = _q.qsize()
                        if depth >= _QUEUE_WARN_LEVEL:
                            _bp_log_counter[0] += 1
                            if _bp_log_counter[0] % 250 == 1:  # log at 1, 251, 501 …
                                logger.warning(
                                    '[GraphEvent] drain queue at %d/%d — backpressure'
                                    ' (suppressing logs; will show every 250 events)',
                                    depth, _QUEUE_CAPACITY,
                                )
                            if socketio is not None:
                                try:
                                    socketio.emit('backpressure', {'queue_depth': depth, 'capacity': _QUEUE_CAPACITY}, namespace='/')
                                except Exception:
                                    pass
                        else:
                            _bp_log_counter[0] = 0   # reset counter when queue drains below watermark
                    except Exception:
                        _graph_event_drops[0] += 1

                graph_event_bus.subscribe(_graph_event_enqueue)
                logger.info('[GraphEvent] DuckDB + Socket.IO drain queue subscribed to GraphEventBus')
            except Exception as _ge_exc:
                logger.debug(f'[GraphEvent] drain queue subscription failed: {_ge_exc}')

            # attach for optional use (keeps backward compatibility)
            try:
                hypergraph_store.hypergraph_engine = hypergraph_engine
                # provide an adapter for RF store -> engine mapping
                try:
                    hypergraph_store.rf_adapter = RFHypergraphAdapter(hypergraph_engine)
                except Exception:
                    pass
            except Exception:
                pass
            # ── Snapshot load + persistence are DEFERRED to main() ──
            # In multi-instance mode (--instance-id), snapshots must NOT
            # auto-load from a shared/global data-dir.  The functions below
            # are called explicitly by main() after args are parsed.
            import atexit as _atexit_mod

            def _deferred_load_snapshot(engine=hypergraph_engine):
                """Load hypergraph snapshot — only called when rehydration is allowed."""
                try:
                    spath = os.path.join(_data_dir(), 'hypergraph_snapshot.json')
                    loaded = engine.load_snapshot(spath) if os.path.isfile(spath) else False
                    if loaded:
                        logger.info(f'HypergraphEngine snapshot loaded from {spath}')
                    else:
                        logger.info('HypergraphEngine: no snapshot to load (fresh instance)')
                except Exception as exc:
                    logger.warning(f'HypergraphEngine snapshot load failed: {exc}')

            def _start_snapshot_persistence(engine=hypergraph_engine):
                """Start background snapshot thread + atexit hook."""
                spath = os.path.join(_data_dir(), 'hypergraph_snapshot.json')
                def _runner():
                    while True:
                        try:
                            engine.save_snapshot(spath)
                        except Exception:
                            pass
                        time.sleep(60)
                t = threading.Thread(target=_runner, daemon=True)
                t.start()
                def _save_on_exit():
                    try:
                        engine.save_snapshot(spath)
                    except Exception:
                        pass
                _atexit_mod.register(_save_on_exit)
    else:
        graph_event_bus = None
        # GraphEventBus is unavailable (no Redis / module missing) but HypergraphEngine
        # is self-contained — create it standalone so BSG detection and MCP routes work.
        try:
            from hypergraph_engine import HypergraphEngine
            decay_val = float(os.environ.get('HYPERGRAPH_DECAY_LAMBDA', 0) or 0)
            hypergraph_engine = HypergraphEngine(decay_lambda=decay_val)
            logger.info('HypergraphEngine initialized (standalone, no GraphEventBus)')
        except Exception as _hg_exc:
            hypergraph_engine = None
            logger.warning(f'HypergraphEngine unavailable: {_hg_exc}')

    # ── Graph-node → recon_system bridge ────────────────────────────────────────
    # Subscribes to GraphEventBus so network_host/rf_node graph events
    # automatically populate recon_system.entities (visible in the Recon panel).
    if graph_event_bus is not None:
        def _on_graph_node_to_recon(event, _rs=recon_system):
            try:
                if isinstance(event, dict):
                    etype = event.get('event_type') or event.get('type')
                    ekind = event.get('entity_kind') or event.get('kind')
                    eid   = event.get('entity_id')   or event.get('id')
                    data  = event.get('entity_data') or event
                else:
                    etype = getattr(event, 'event_type', None)
                    ekind = getattr(event, 'entity_kind', None)
                    eid   = getattr(event, 'entity_id',   None)
                    data  = getattr(event, 'entity_data', None) or {}
                if etype not in ('NODE_CREATE', 'NODE_UPDATE', 'NODE_ADD'):
                    return
                if ekind not in ('network_host', 'rf_node'):
                    return
                if not eid:
                    return
                data = data or {}
                entity = build_recon_entity_from_graph_event(
                    eid,
                    ekind,
                    data,
                    observed_at=time.time(),
                )
                entity_id = entity.get('entity_id') or eid
                loc = entity.get('location') or {}
                _rs.entities[entity_id] = entity
                if hasattr(_rs, '_dirty_entities'):
                    _rs._dirty_entities.add(entity_id)
                # Push to SSE subscribers so browser entity store stays live
                try:
                    _subs = getattr(stream_recon_entities, '_subscribers', {})
                    if _subs:
                        _sse = {'type': 'entity_upsert', 'entity': entity}
                        for _q in list(_subs.values()):
                            try: _q.put_nowait(_sse)
                            except Exception: pass
                except Exception:
                    pass
                # Opportunistically populate the node geo index from recon bridge events
                if loc and map_cache is not None:
                    try:
                        _lat = loc.get('lat')
                        _lon = loc.get('lon')
                        if _lat is not None and _lon is not None:
                            map_cache.upsert_node_geo(entity_id, float(_lat), float(_lon),
                                                      confidence=1.0, method='observed')
                    except Exception:
                        pass
            except Exception as _e:
                logger.debug(f'[graph_to_recon] {_e}')
        try:
            graph_event_bus.subscribe(_on_graph_node_to_recon)
            logger.info('Recon bridge subscribed to GraphEventBus (network_host/rf_node → recon_system)')
        except Exception:
            pass

    # ── Live ingest consumer: stream flow events → recon_system ─────────────────
    # stream_manager enqueues decoded flow events (src/dst IPs) into live_ingest.
    # This worker drains that queue every 2s and creates PCAP recon entities for
    # each new public IP observed in the stream.
    def _start_live_ingest_worker(_rs=recon_system):
        def _worker():
            try:
                import importlib, ipaddress as _ipmod
                li = importlib.import_module('live_ingest')
            except ImportError:
                logger.warning('[live_ingest_worker] live_ingest module not available')
                return

            # Wire priority check into adaptive engine — C2 and high-mass IPs
            # bypass the drop filter so they are never shed under queue pressure
            try:
                from adaptive_schema_engine import engine as _ase
                from registries.pcap_registry import is_c2_ip as _is_c2
                _ase._priority_check = _is_c2
            except Exception:
                pass  # runs without priority elevation if registry unavailable

            # Canonical aliases — use engine's live table if available, else static
            try:
                from adaptive_schema_engine import engine as _ase
                def _extract_ip(ev: dict, key: str):
                    """Use engine's learned alias table + entities fallback."""
                    aliases = _ase.aliases.get(key, (key,))
                    for alias in aliases:
                        val = ev.get(alias)
                        if val:
                            return val
                    for ent in ev.get('entities', []):
                        if ent.get('key') in aliases:
                            v = ent.get('value')
                            if v:
                                return v
                    return None
            except ImportError:
                _IP_ALIASES = {
                    'src_ip': ('src_ip', 'src', 'source_ip', 'ip_src', 'SrcIp', 'sourceIp'),
                    'dst_ip': ('dst_ip', 'dst', 'dest_ip',   'ip_dst', 'DstIp', 'destIp'),
                }
                def _extract_ip(ev: dict, key: str):
                    for alias in _IP_ALIASES.get(key, (key,)):
                        val = ev.get(alias)
                        if val:
                            return val
                    for ent in ev.get('entities', []):
                        if ent.get('key') in _IP_ALIASES.get(key, (key,)):
                            v = ent.get('value')
                            if v:
                                return v
                    return None

            _seen_ips: set = set()
            while True:
                try:
                    events = li.dequeue(limit=50)
                    for ev in events:
                        schema_hash = ev.get('_schema_hash', 0)
                        found_ip = False
                        for ip_key in ('src_ip', 'dst_ip', 'src', 'dst'):
                            ip = _extract_ip(ev, ip_key)
                            if not ip or ip in _seen_ips:
                                continue
                            try:
                                addr = _ipmod.ip_address(ip)
                                if addr.is_private or addr.is_loopback or addr.is_link_local:
                                    continue
                            except Exception:
                                continue
                            found_ip = True
                            # Cap seen-IP set to prevent unbounded growth over long sessions
                            if len(_seen_ips) > 50_000:
                                _seen_ips.clear()
                            _seen_ips.add(ip)
                            entity_id = f'PCAP-{ip}'
                            if entity_id not in _rs.entities:
                                _rs.entities[entity_id] = {
                                    'entity_id':   entity_id,
                                    'name':        ip,
                                    'type':        'RECON_ENTITY',
                                    'threat_level': 'UNKNOWN',
                                    'disposition':  'UNKNOWN',
                                    'ip':           ip,
                                    'last_update':  time.time(),
                                    'source':       'stream',
                                }
                                if hasattr(_rs, '_dirty_entities'):
                                    _rs._dirty_entities.add(entity_id)
                                # Push new entity to SSE subscribers
                                try:
                                    _subs = getattr(stream_recon_entities, '_subscribers', {})
                                    if _subs:
                                        _sse = {'type': 'entity_upsert',
                                                'entity': _rs.entities[entity_id]}
                                        for _q in list(_subs.values()):
                                            try: _q.put_nowait(_sse)
                                            except Exception: pass
                                except Exception:
                                    pass
                                # Semantic shadow: embed new entity → auto-create
                                # similarity-driven speculative edges (non-blocking)
                                try:
                                    from semantic_shadow import SemanticShadow as _SS
                                    from protocol_intel import get_protocol_intel as _get_pi
                                    ent  = _rs.entities[entity_id]
                                    asn  = ent.get('asn', '')
                                    port = str(ev.get('dst_port', ev.get('src_port', '')))
                                    # Score protocol anomaly for this stream event
                                    _pa = _get_pi().score_dict(ev)
                                    _pa_score = _pa.anomaly_score
                                    vtags = ' '.join(v.name for v in _pa.violations)
                                    desc = f"{ip} {asn} port={port} {vtags}".strip()
                                    _SS.get_instance().process_entity(
                                        entity_id, desc,
                                        extra_labels={'protocol_anomaly_score': _pa_score,
                                                      'protocol_violations': vtags or None},
                                    )
                                except Exception:
                                    pass
                        # Feed outcome back to engine so confidence scores build up
                        try:
                            from adaptive_schema_engine import engine as _ase
                            _ase.record_outcome(schema_hash, found_ip)
                        except Exception:
                            pass
                        # Shadow graph re-evaluation: promote edges whose nodes now exist
                        try:
                            from shadow_graph import ShadowGraph as _SG
                            _SG.get_instance().re_evaluate(set(_rs.entities.keys()))
                        except Exception:
                            pass
                except Exception as _e:
                    logger.debug(f'[live_ingest_worker] {_e}')
                time.sleep(2)
        t = threading.Thread(target=_worker, daemon=True, name='live-ingest-recon-bridge')
        t.start()
        logger.info('[live_ingest_worker] started (stream → recon bridge active)')
    _start_live_ingest_worker()

    # AISStream WebSocket client
    aisstream_ws = None
    aisstream_thread = None
    aisstream_active = False
    aisstream_bounding_box = None

    # POI Manager
    if POI_MANAGER_AVAILABLE:
        poi_manager = POIManager(db_path='poi_database.db')
    else:
        poi_manager = None

    # ============================================================================
    # REHYDRATION HELPERS
    # ============================================================================
    def ensure_global_room(operator_mgr):
        """Ensure explicit Global room exists in DB."""
        if not operator_mgr: return

        # Try to find Global room
        global_room = None
        if hasattr(operator_mgr, 'get_room_by_name'):
            global_room = operator_mgr.get_room_by_name("Global")

        if not global_room:
            # Create it if missing
            try:
                # public room, system created
                if hasattr(operator_mgr, 'create_room'):
                    res = operator_mgr.create_room("Global", "public", created_by="system")
                    logger.info(f"Created 'Global' room during rehydration: {res}")
            except Exception as e:
                logger.warning(f"Could not ensure Global room: {e}")

    def rehydrate_recon_from_operator_db(operator_mgr, recon_sys):
        """Restore entity state from durable sqlite to in-memory recon system."""
        if not operator_mgr or not recon_sys:
            return

        logger.info("Rehydrating Recon System from Operator Session DB...")

        # 1. Ensure Global room
        ensure_global_room(operator_mgr)

        # 2. Get Global room ID
        global_room_id = "room_global_default"
        if hasattr(operator_mgr, 'get_room_by_name'):
            rm = operator_mgr.get_room_by_name("Global")
            if rm: global_room_id = rm.room_id

        # Types that belong in the Recon system (trackable on the globe)
        REHYDRATE_TYPES = {"RECON_ENTITY", "PCAP_HOST", "NMAP_TARGET"}

        # 3. Load snapshot
        try:
            if hasattr(operator_mgr, 'get_room_entities_snapshot'):
                snapshot = operator_mgr.get_room_entities_snapshot(global_room_id)
                count = 0
                skipped = 0
                for ent in snapshot:
                    # entity = {id:..., type:..., data:{...}}
                    entity_id = ent.get('id')
                    entity_type = ent.get('type', '')
                    data = ent.get('data') or {}

                    # Only rehydrate trackable entity types into Recon
                    if entity_type not in REHYDRATE_TYPES:
                        skipped += 1
                        continue

                    if entity_id and data:
                        # Ensure 'location' dict exists for SpatialIndex (required by rebuild_spatial_index)
                        if 'location' not in data:
                             lat = data.get('lat', 0)
                             lon = data.get('lon', 0)
                             alt = data.get('alt', 0)
                             data['location'] = {
                                 'lat': lat,
                                 'lon': lon,
                                 'altitude_m': alt
                             }

                        # Ensure required metrics keys exist to prevent KeyErrors if metric calculation fails or is skipped
                        data.setdefault('threat_level', 'UNKNOWN')
                        data.setdefault('distance_nm', 0.0)
                        data.setdefault('bearing_deg', 0.0)
                        data.setdefault('disposition', 'UNKNOWN')

                        # Direct injection into recon system
                        recon_sys.entities[entity_id] = data
                        # Mark dirty to update derived stats
                        if hasattr(recon_sys, '_dirty_entities'):
                            recon_sys._dirty_entities.add(entity_id)
                        count += 1

                logger.info(f"Rehydrated {count} trackable entities into Recon System (skipped {skipped} non-recon types).")
                # Trigger spatial index rebuild
                if hasattr(recon_sys, '_rebuild_spatial_index'):
                    recon_sys._rebuild_spatial_index()

        except Exception as e:
            logger.error(f"Rehydration failed: {e}", exc_info=True)

    # Operator Session Manager
    # ── Rehydration is DEFERRED to main() — see _deferred_rehydrate() ──
    operator_manager = None
    sensor_registry_instance = None
    rf_ip_correlation_engine = None
    predictive_control_path_engine = None
    rfuav_evidence_emitter = None
    rfuav_kafka_consumer = None

    if OPERATOR_MANAGER_AVAILABLE:
        # NOTE: db_path left as default here (global identities).
        # main() will re-initialize with instance-scoped session DB
        # when --instance-id is provided.
        operator_manager = get_session_manager()
        logger.info(f"Operator Session Manager initialized: {operator_manager.get_stats()}")

        def _deferred_rehydrate(op_mgr=operator_manager, recon_sys=recon_system):
            """Rehydrate recon from operator DB — only when NOT in instance mode."""
            rehydrate_recon_from_operator_db(op_mgr, recon_sys)

        # Initialize WriteBus (Core Chokepoint)
        try:
            import writebus as wb_module
            logger.info(f"[PCAP] writebus module path = {wb_module.__file__}")
            from writebus import init_writebus
            # hypergraph_engine was initialized earlier (approx line 3117)
            hg_engine_ref = globals().get('hypergraph_engine')
            ge_bus_ref = globals().get('graph_event_bus')

            init_writebus(
                operator_manager=operator_manager,
                hypergraph_engine=hg_engine_ref,
                default_room="Global",
                graph_event_bus=ge_bus_ref,
                strict_no_bypass=True,
                writebus_db_path=os.path.join(_data_dir(), "writebus_state.sqlite3"),
            )
            logger.info("[OK] WriteBus initialized")
        except ImportError:
            logger.warning("[WARN] WriteBus module not found")
        except Exception as e:
            logger.warning(f"[WARN] WriteBus initialization failed: {e}")

        # SensorRegistry: clean chokepoint (only module allowed to touch BOTH
        # OperatorSessionManager.publish_to_room and HypergraphEngine.add_node/add_edge)
        try:
            from sensor_registry import init_sensor_registry, upsert_sensor, assign_sensor, emit_activity
            hg = globals().get("hypergraph_engine")
            sensor_registry_instance = init_sensor_registry(operator_manager, hg, global_room_name="Global")
            logger.info("[OK] SensorRegistry initialized")
        except Exception as e:
            logger.warning(f"[WARN] SensorRegistry not available: {e}")

        try:
            from rf_ip_correlation_engine import RFIPCorrelationEngine
            rf_ip_correlation_engine = RFIPCorrelationEngine()
            logger.info("[OK] RF/IP correlation engine initialized")
        except Exception as e:
            logger.warning(f"[WARN] RF/IP correlation engine not available: {e}")

        try:
            from predictive_control_path_engine import PredictiveControlPathEngine
            predictive_control_path_engine = PredictiveControlPathEngine()
            logger.info("[OK] Predictive control-path engine initialized")
        except Exception as e:
            logger.warning(f"[WARN] Predictive control-path engine not available: {e}")

        try:
            import writebus
            from rfuav_inference_service import RFUAVEvidenceEmitter
            rfuav_evidence_emitter = RFUAVEvidenceEmitter(writebus_provider=writebus.bus)
            logger.info("[OK] RFUAV evidence emitter initialized")
        except Exception as e:
            logger.warning(f"[WARN] RFUAV evidence emitter not available: {e}")

        if rfuav_evidence_emitter and os.environ.get("RFUAV_KAFKA_ENABLED", "").lower() in {"1", "true", "yes", "on"}:
            try:
                from rfuav_kafka_consumer import RFUAVKafkaConsumer

                rfuav_kafka_consumer = RFUAVKafkaConsumer(
                    handler=lambda event: _consume_rfuav_kafka_event(event),
                    bootstrap_servers=os.environ.get("RFUAV_KAFKA_BROKERS", "localhost:9092"),
                    topic=os.environ.get("RFUAV_KAFKA_TOPIC", "rf.uav.detections"),
                    group_id=os.environ.get("RFUAV_KAFKA_GROUP_ID", "scythe-rfuav"),
                    max_poll_records=int(os.environ.get("RFUAV_KAFKA_MAX_POLL_RECORDS", "500")),
                    auto_offset_reset=os.environ.get("RFUAV_KAFKA_AUTO_OFFSET_RESET", "latest"),
                )
                logger.info("[OK] RFUAV Kafka consumer configured")
            except Exception as e:
                logger.warning(f"[WARN] RFUAV Kafka consumer not available: {e}")

        # Initialize Host Confidence Engine
        try:
            from host_confidence_engine import HostConfidenceEngine
            host_confidence_engine = HostConfidenceEngine()
            logger.info("[OK] Host confidence engine initialized")
        except Exception as e:
            logger.warning(f"[WARN] Host confidence engine not available: {e}")
            host_confidence_engine = None

        try:
            from registries.pcap_registry import init_pcap_registry, upsert_pcap_artifact, create_pcap_session, ingest_pcap_session
            hg = globals().get("hypergraph_engine")
            pcap_registry_instance = init_pcap_registry(
                operator_manager, hg, global_room_name="Global",
                enable_geoip=True,
                geoip_city_mmdb="assets/GeoLite2-City.mmdb",
                geoip_asn_mmdb="assets/GeoLite2-ASN.mmdb",
            )
            logger.info("[OK] PcapRegistry initialized (canonical path + graph_ids)")
        except Exception as e:
            logger.error(f"[FATAL] PcapRegistry failed to load: {e}  - graph_ids.py is REQUIRED")
            pcap_registry_instance = None

        @app.route('/api/network/host-confidence/<host_ip>', methods=['GET'])
        def get_host_confidence(host_ip):
            if host_confidence_engine:
                registry = host_confidence_engine.registry
                if host_ip in registry:
                    return jsonify(registry[host_ip])
                return jsonify({"error": "Host not found"}), 404
            return jsonify({"error": "Engine not available"}), 503

        # DetectionRegistry: Two-tier detection policy (Live Edge + Durable Summary)
        try:
            from registries.detection_registry import init_detection_registry
            # Singleton init using default config (Tier A/B enabled)
            # Use globals() assignment to avoid SyntaxError within large function scope
            globals()['detection_registry'] = init_detection_registry()
            logger.info("[OK] DetectionRegistry initialized")
        except ImportError:
            logger.warning("[WARN] DetectionRegistry module not found")
        except Exception as e:
            logger.warning(f"[WARN] DetectionRegistry initialization failed: {e}")


        # Subscribe operator session manager to graph events (prefer durable bus if available)
        try:
            if 'graph_event_bus' in globals() and graph_event_bus is not None:
                try:
                    operator_manager.subscribe_to_graph_events(graph_event_bus)
                except Exception:
                    pass
            elif 'hypergraph_engine' in globals() and hypergraph_engine is not None:
                try:
                    operator_manager.subscribe_to_graph_events(hypergraph_engine)
                except Exception:
                    pass
        except Exception:
            pass
    else:
        operator_manager = None

    # ========================================================================
    # API ROUTES - RF HYPERGRAPH
    # ========================================================================

    @app.route('/api/rf-hypergraph/visualization', methods=['GET'])
    def get_hypergraph_visualization():
        """Get hypergraph visualization data"""
        try:
            # Prefer hypergraph_engine (in-memory indices) when available for faster queries
            if 'hypergraph_engine' in globals() and hypergraph_engine is not None:
                data = hypergraph_engine.get_visualization_data() if hasattr(hypergraph_engine, 'get_visualization_data') else hypergraph_store.get_visualization_data()
            else:
                data = hypergraph_store.get_visualization_data()
            return jsonify(data)
        except Exception as e:
            logger.error(f"Error getting visualization: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/rf-hypergraph/metrics', methods=['GET'])
    def get_hypergraph_metrics():
        """Get hypergraph metrics"""
        try:
            # Prefer metrics from hypergraph_engine when present
            if 'hypergraph_engine' in globals() and hypergraph_engine is not None and hasattr(hypergraph_engine, 'get_metrics'):
                metrics = hypergraph_engine.get_metrics()
            else:
                metrics = hypergraph_store.get_metrics()
            return jsonify({'status': 'ok', 'metrics': metrics})
        except Exception as e:
            logger.error(f"Error getting metrics: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/hypergraph/traces/perfetto', methods=['GET'])
    @app.route('/api/rf-hypergraph/traces/perfetto', methods=['GET'])
    def get_hypergraph_perfetto_traces():
        """Export buffered hypergraph traces with SCYTHE geospatial telemetry."""
        try:
            engine = globals().get('hypergraph_engine') or getattr(hypergraph_store, 'hypergraph_engine', None)
            if engine is None or not hasattr(engine, 'export_traces_perfetto'):
                return jsonify({
                    'status': 'unavailable',
                    'traceSession': None,
                    'eventCount': 0,
                    'geoEventCount': 0,
                    'events': [],
                    'message': 'hypergraph_engine tracing is not available'
                }), 503

            payload = json.loads(engine.export_traces_perfetto())
            events = payload.get('events') or []
            payload['status'] = 'ok'
            payload['geoEventCount'] = sum(1 for e in events if e.get('geospatial'))
            return jsonify(payload)
        except Exception as e:
            logger.error(f"Error exporting Perfetto traces: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/rf-hypergraph/generate-test', methods=['GET'])
    def generate_test_hypergraph():
        """Generate test hypergraph data"""
        try:
            num_nodes = int(request.args.get('nodes', 20))
            freq_min = float(request.args.get('freq_min', 88.0))
            freq_max = float(request.args.get('freq_max', 108.0))
            area_size = float(request.args.get('area_size', 1000.0))

            data = hypergraph_store.generate_test_data(num_nodes, freq_min, freq_max, area_size)
            return jsonify(data)
        except Exception as e:
            logger.error(f"Error generating test data: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/rf-hypergraph/reset', methods=['POST', 'GET'])
    def reset_hypergraph():
        """Reset hypergraph session"""
        try:
            hypergraph_store.reset()
            return jsonify({'status': 'ok', 'message': 'Hypergraph session reset', 'session_id': hypergraph_store.session_id})
        except Exception as e:
            logger.error(f"Error resetting hypergraph: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/rf-hypergraph/status', methods=['GET'])
    def get_hypergraph_status():
        """Get hypergraph status, including spectral vulnerability metrics."""
        # Use the global store initialized in the app setup
        global hypergraph_store

        eng = globals().get('hypergraph_engine')
        # Fallback to engine if stored on the store instance
        if not eng and 'hypergraph_store' in globals():
            eng = getattr(hypergraph_store, 'hypergraph_engine', None)

        return jsonify({
            'status': 'ok',
            'session_id': hypergraph_store.session_id,
            'nodes': len(hypergraph_store.nodes),
            'hyperedges': len(hypergraph_store.hyperedges),
            'uptime': time.monotonic() - hypergraph_store.start_time,
            'spectral_vulnerability': eng.compute_spectral_vulnerability() if eng and hasattr(eng, 'compute_spectral_vulnerability') else None
        })

    # Graph Query DSL endpoint (operator-facing)
    try:
        from graph_query_dsl import parse_dsl, execute_query
    except Exception:
        parse_dsl = None
        execute_query = None

    # Registered long queries (query_id -> stored info)
    REGISTERED_QUERIES = {}
    REGISTERED_QUERIES_LOCK = threading.RLock()

    # Persistence helpers: prefer Redis, fall back to SQLite
    def _persist_registered_query(qid: str, entry: Dict[str, Any]):
        try:
            if 'redis_client' in globals() and redis_client:
                try:
                    # store as hash and add to index set
                    redis_client.hset(f"registered_query:{qid}", mapping={
                        'dsl': entry.get('dsl',''),
                        'parsed': json.dumps(entry.get('parsed',{})),
                        'created_at': entry.get('created_at',''),
                        'owner': entry.get('owner','')
                    })
                    redis_client.sadd('registered_queries:set', qid)
                    return True
                except Exception:
                    pass

            # SQLite fallback
            db_path = os.path.join(_data_dir(), 'registered_queries.sqlite3')
            try:
                os.makedirs(os.path.dirname(db_path), exist_ok=True)
                import sqlite3
                conn = sqlite3.connect(db_path)
                cur = conn.cursor()
                cur.execute('CREATE TABLE IF NOT EXISTS registered_queries (qid TEXT PRIMARY KEY, dsl TEXT, parsed TEXT, created_at TEXT, owner TEXT)')
                cur.execute('REPLACE INTO registered_queries (qid, dsl, parsed, created_at, owner) VALUES (?,?,?,?,?)', (
                    qid, entry.get('dsl',''), json.dumps(entry.get('parsed',{})), entry.get('created_at',''), entry.get('owner','')
                ))
                conn.commit()
                conn.close()
                return True
            except Exception:
                return False
        except Exception:
            return False

    def _delete_registered_query_persist(qid: str):
        try:
            if 'redis_client' in globals() and redis_client:
                try:
                    redis_client.delete(f"registered_query:{qid}")
                    redis_client.srem('registered_queries:set', qid)
                    return True
                except Exception:
                    pass
            db_path = os.path.join(_data_dir(), 'registered_queries.sqlite3')
            try:
                import sqlite3
                conn = sqlite3.connect(db_path)
                cur = conn.cursor()
                cur.execute('DELETE FROM registered_queries WHERE qid = ?', (qid,))
                conn.commit()
                conn.close()
                return True
            except Exception:
                return False
        except Exception:
            return False

    def _load_registered_queries_from_persist():
        try:
            loaded = {}
            if 'redis_client' in globals() and redis_client:
                try:
                    qids = redis_client.smembers('registered_queries:set') or set()
                    for qid in qids:
                        try:
                            h = redis_client.hgetall(f"registered_query:{qid}") or {}
                            if not h:
                                continue
                            parsed = {}
                            try:
                                parsed = json.loads(h.get('parsed') or '{}')
                            except Exception:
                                parsed = {}
                            loaded[qid] = {
                                'dsl': h.get('dsl') or '',
                                'parsed': parsed,
                                'created_at': h.get('created_at') or '',
                                'owner': h.get('owner') or ''
                            }
                        except Exception:
                            continue
                    with REGISTERED_QUERIES_LOCK:
                        REGISTERED_QUERIES.update(loaded)
                    return True
                except Exception:
                    pass

            # SQLite fallback
            db_path = os.path.join(_data_dir(), 'registered_queries.sqlite3')
            try:
                import sqlite3
                if not os.path.exists(db_path):
                    return False
                conn = sqlite3.connect(db_path)
                cur = conn.cursor()
                cur.execute('SELECT qid, dsl, parsed, created_at, owner FROM registered_queries')
                rows = cur.fetchall()
                for qid, dsl, parsed_text, created_at, owner in rows:
                    try:
                        parsed = json.loads(parsed_text or '{}')
                    except Exception:
                        parsed = {}
                    loaded[qid] = {'dsl': dsl or '', 'parsed': parsed, 'created_at': created_at or '', 'owner': owner or ''}
                conn.close()
                with REGISTERED_QUERIES_LOCK:
                    REGISTERED_QUERIES.update(loaded)
                return True
            except Exception:
                return False
        except Exception:
            return False

    # Attempt to load persisted queries at startup
    try:
        _load_registered_queries_from_persist()
    except Exception:
        pass

    @app.route('/api/hypergraph/query', methods=['POST'])
    def hypergraph_query():
        """Accept a Clarktech Graph Query DSL string and execute against the engine.

        POST JSON {"dsl": "FIND NODES\nWHERE kind = \"rf\"\nRETURN nodes"}
        or plain text body containing the DSL.
        """
        if parse_dsl is None or execute_query is None:
            return jsonify({'status': 'error', 'message': 'DSL module not available'}), 500

        try:
            data = request.get_json(silent=True) or {}
            dsl_text = data.get('dsl') if data else None
            if not dsl_text:
                # try raw body
                dsl_text = request.get_data(as_text=True) or ''
            parsed = parse_dsl(dsl_text)

            hg_eng = globals().get('hypergraph_engine')
            hg_store = globals().get('hypergraph_store')
            # Prefer populated hypergraph_engine; fall back to legacy hypergraph_store
            # Use legacy hypergraph_store by default (contains node_id records),
            # otherwise fall back to the newer hypergraph_engine if present.
            if hg_store:
                engine = hg_store
            elif hg_eng and getattr(hg_eng, 'nodes', None):
                engine = hg_eng
            else:
                engine = hg_eng or hg_store

            if engine is None:
                return jsonify({'status': 'error', 'message': 'Hypergraph engine not available'}), 503

            res = execute_query(engine, parsed)

            # Build canonical subgraph response when requested
            import uuid as _uuid
            seq = None
            try:
                if OPERATOR_MANAGER_AVAILABLE and operator_manager:
                    seq = operator_manager.entity_sequence
            except Exception:
                seq = None

            # helper normalizers
            def _norm_node(n: dict) -> dict:
                nid = n.get('id') or n.get('node_id') or n.get('nodeId') or n.get('node')
                kind = n.get('kind') or n.get('type')
                if not kind and nid and isinstance(nid, str):
                    # infer from prefix
                    if nid.lower().startswith('rf'):
                        kind = 'rf'
                    elif nid.lower().startswith('net') or nid.lower().startswith('net_'):
                        kind = 'network_host'
                position = None
                if 'position' in n and n.get('position'):
                    position = n.get('position')
                elif 'lat' in n and 'lon' in n:
                    position = [n.get('lat'), n.get('lon'), n.get('alt', 0)]

                labels = n.get('labels') if isinstance(n.get('labels'), dict) else {}
                # include common labelable fields
                for k in ('service','vessel_type','callsign','hostname'):
                    if k in n and n[k]:
                        labels[k] = n[k]

                created_at = n.get('created_at') or n.get('timestamp') or n.get('time') or None
                updated_at = n.get('updated_at') or n.get('timestamp') or created_at

                return {
                    'id': nid,
                    'kind': kind,
                    'position': position,
                    'frequency': n.get('frequency'),
                    'labels': labels,
                    'metadata': n.get('metadata') or {},
                    'created_at': created_at,
                    'updated_at': updated_at
                }

            def _norm_edge(e: dict) -> dict:
                eid = e.get('id') or e.get('edge_id') or None
                return {
                    'id': eid or None,
                    'kind': e.get('kind'),
                    'nodes': e.get('nodes') or [],
                    'weight': e.get('weight') or e.get('signal_strength') or None,
                    'labels': e.get('labels') or {},
                    'metadata': e.get('metadata') or {},
                    'timestamp': e.get('timestamp') or None
                }

            if parsed.get('return') == 'subgraph' or parsed.get('find') == 'subgraph':
                nodes = [ _norm_node(n) for n in res.get('nodes', []) ]
                edges = [ _norm_edge(e) for e in res.get('edges', []) ]
                stats = {
                    'node_count': len(nodes),
                    'edge_count': len(edges),
                    'central_nodes': [],
                    'kinds': {}
                }
                for n in nodes:
                    k = n.get('kind') or 'unknown'
                    stats['kinds'][k] = stats['kinds'].get(k, 0) + 1

                payload = {
                    'query_id': _uuid.uuid4().hex,
                    'sequence_id': seq or 0,
                    'timestamp': datetime.utcnow().isoformat() + 'Z',
                    'nodes': nodes,
                    'edges': edges,
                    'stats': stats
                }
                return jsonify({'status': 'ok', 'query': parsed, 'result': payload})

            return jsonify({'status': 'ok', 'query': parsed, 'result': res})
        except Exception as e:
            logger.error(f"Error executing DSL query: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500


    @app.route('/api/satellites', methods=['GET'])
    def get_satellites():
        """Return satellite constellation records from SQLite.

        Query params:
            name: optional substring to filter satellite name
            limit: number of records to return (default 100)
            offset: pagination offset (default 0)

        Graceful fallback: if satellites table doesn't exist, returns empty list.
        """
        try:
            name_q = request.args.get('name')
            limit = int(request.args.get('limit', 100))
            offset = int(request.args.get('offset', 0))

            import sqlite3
            db_path = metrics_logger.db_path
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Check if satellites table exists
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='satellites'")
            if cursor.fetchone() is None:
                # Table doesn't exist — return empty gracefully
                conn.close()
                logger.warning('[INFRA] satellites table not initialized; returning empty list')
                return jsonify({'status': 'ok', 'satellites': [], 'count': 0, 'note': 'table not initialized'})

            base_query = 'SELECT id, name, lat, lon, altitude, operator, type, frequency, orbit, coverage, status, launch_date, mission, extra FROM satellites'
            params = []
            if name_q:
                base_query += ' WHERE name LIKE ?'
                params.append(f'%{name_q}%')

            base_query += ' ORDER BY id DESC LIMIT ? OFFSET ?'
            params.extend([limit, offset])

            cursor.execute(base_query, params)
            rows = cursor.fetchall()
            result = []
            for r in rows:
                item = dict(r)
                # parse extra JSON if present
                try:
                    if item.get('extra'):
                        item['extra'] = json.loads(item['extra'])
                except Exception:
                    pass
                result.append(item)

            conn.close()
            return jsonify({'status': 'ok', 'satellites': result, 'count': len(result)})
        except Exception as e:
            logger.error(f'Error fetching satellites: {e}')
            # Fallback: return empty list instead of error
            return jsonify({'status': 'ok', 'satellites': [], 'count': 0, 'error_log': str(e)})

    # Subgraph diff endpoint - incremental updates between sequences
    try:
        from subgraph_diff import SubgraphDiffGenerator, QueryPredicate
    except Exception:
        SubgraphDiffGenerator = None
        QueryPredicate = None

    @app.route('/api/hypergraph/diff', methods=['POST'])
    def hypergraph_diff():
        """Return a Clarktech Subgraph Diff between sequences for a DSL-scoped query.

        POST JSON: { "dsl": "FIND ...", "from_sequence": 123, "to_sequence": 130, "query_id": "optional" }
        """
        if SubgraphDiffGenerator is None or QueryPredicate is None:
            return jsonify({'status': 'error', 'message': 'Subgraph diff module not available'}), 500

        try:
            payload = request.get_json(silent=True) or {}
            dsl_text = payload.get('dsl') or request.get_data(as_text=True) or ''
            from_seq = int(payload.get('from_sequence') or payload.get('from') or 0)
            to_seq = int(payload.get('to_sequence') or payload.get('to') or 0)
            qid = payload.get('query_id') or None

            if not dsl_text:
                return jsonify({'status': 'error', 'message': 'Missing DSL in request'}), 400

            # parse DSL
            parsed = parse_dsl(dsl_text) if parse_dsl else {}

            # select engine (prefer in-memory hypergraph_engine)
            engine = globals().get('hypergraph_engine') or globals().get('hypergraph_store')
            if engine is None:
                return jsonify({'status': 'error', 'message': 'Hypergraph engine not available'}), 503

            # build predicate from parsed DSL
            predicate = QueryPredicate(parsed)

            # use redis_client if present
            redis_conn = globals().get('redis_client')

            gen = SubgraphDiffGenerator(engine, operator_manager=globals().get('operator_manager'), redis_client=redis_conn)

            # If requested to auto-advance to latest seq, set to_seq to current sequence
            if to_seq == 0 and OPERATOR_MANAGER_AVAILABLE and operator_manager:
                try:
                    to_seq = operator_manager.entity_sequence
                except Exception:
                    to_seq = to_seq

            diff = gen.generate_diff(qid or parsed.get('query_id') or 'query', predicate, from_seq, to_seq)
            return jsonify({'status': 'ok', 'query': parsed, 'diff': diff})
        except Exception as e:
            logger.error(f"Error generating subgraph diff: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500


    @app.route('/api/hypergraph/events/since', methods=['GET'])
    def hypergraph_events_since():
        """Return graph events from the DuckDB delta log after a given sequence number.

        Uses the shared _duck_store (lazily resolved after _post_startup) so the
        path is always consistent with the per-instance --data-dir setting.

        Query params:
            seq (int, default 0): return events with store seq > this value
            limit (int, default 200): max events to return
        """
        try:
            since_seq = int(request.args.get('seq') or 0)
            limit = min(int(request.args.get('limit') or 200), 1000)
            ds = globals().get('_duck_store')
            if ds is None:
                return jsonify({'status': 'ok', 'events': [], 'since_seq': since_seq})
            rows = ds.query_since(since_seq, limit=limit)
            return jsonify({'status': 'ok', 'events': rows, 'since_seq': since_seq, 'count': len(rows)})
        except Exception as e:
            logger.debug(f'[events/since] error: {e}')
            return jsonify({'status': 'error', 'message': str(e)}), 500


    # ----------------------- Missions API ---------------------------------

    @contextmanager
    def _metrics_db():
        """Context manager: open metrics.db, commit on success, rollback + close on exit."""
        conn = sqlite3.connect(os.path.join(_data_dir(), 'metrics.db'))
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _save_mission_to_db(mission):
        try:
            nowt = time.time()
            with _metrics_db() as conn:
                conn.execute(
                    '''INSERT OR REPLACE INTO missions
                       (mission_id, name, owner, status, metadata, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM missions WHERE mission_id = ?), ?), ?)''',
                    (mission.get('mission_id'), mission.get('name'), mission.get('owner'),
                     mission.get('status', 'open'), json.dumps(mission.get('metadata', {})),
                     mission.get('mission_id'), nowt, nowt)
                )
            return True
        except Exception as e:
            logger.warning(f"Failed to persist mission: {e}")
            return False

    def _load_mission_from_db(mission_id):
        try:
            conn = sqlite3.connect(os.path.join(_data_dir(), 'metrics.db'))
            conn.row_factory = sqlite3.Row
            try:
                r = conn.execute('SELECT * FROM missions WHERE mission_id = ?', (mission_id,)).fetchone()
            finally:
                conn.close()
            if not r:
                return None
            rec = dict(r)
            try:
                rec['metadata'] = json.loads(rec.get('metadata') or '{}')
            except Exception:
                rec['metadata'] = {}
            return rec
        except Exception as e:
            logger.warning(f"Failed to load mission from DB: {e}")
            return None

    @app.route('/api/missions', methods=['POST'])
    def create_mission():
        try:
            data = request.get_json() or {}
            mission_id = data.get('mission_id') or f"mission_{int(time.time()*1000)}_{random.randint(1,9999)}"
            meta = {
                'mission_id': mission_id,
                'name': data.get('name',''),
                'owner': (operator_manager.get_operator_for_session(request.headers.get('X-Session-Token')).operator_id if operator_manager and request.headers.get('X-Session-Token') else data.get('owner')),
                'status': 'open',
                'metadata': data.get('metadata', {}),
                'created_at': time.time(),
                'updated_at': time.time()
            }
            ok = _save_mission_to_db(meta)
            if not ok:
                return jsonify({'status': 'error', 'message': 'Could not persist mission'}), 500
            return jsonify({'status': 'ok', 'mission': meta}), 201
        except Exception as e:
            logger.error(f"create_mission error: {e}")
            return jsonify({'status':'error','message':str(e)}),500

    @app.route('/api/missions/<mission_id>', methods=['GET'])
    def get_mission(mission_id):
        try:
            rec = _load_mission_from_db(mission_id)
            if not rec:
                return jsonify({'status':'error','message':'Not found'}),404
            return jsonify({'status':'ok','mission':rec})
        except Exception as e:
            logger.error(f"get_mission error: {e}")
            return jsonify({'status':'error','message':str(e)}),500

    @app.route('/api/missions/<mission_id>', methods=['PATCH'])
    def patch_mission(mission_id):
        try:
            rec = _load_mission_from_db(mission_id)
            if not rec:
                return jsonify({'status':'error','message':'Not found'}),404
            data = request.get_json() or {}
            rec['name'] = data.get('name', rec.get('name'))
            rec['metadata'] = {**(rec.get('metadata') or {}), **(data.get('metadata') or {})}
            rec['updated_at'] = time.time()
            _save_mission_to_db(rec)
            return jsonify({'status':'ok','mission':rec})
        except Exception as e:
            logger.error(f"patch_mission error: {e}")
            return jsonify({'status':'error','message':str(e)}),500

    @app.route('/api/missions/<mission_id>/end', methods=['POST'])
    def end_mission(mission_id):
        try:
            rec = _load_mission_from_db(mission_id)
            if not rec:
                return jsonify({'status':'error','message':'Not found'}),404
            rec['status'] = 'closed'
            rec['updated_at'] = time.time()
            _save_mission_to_db(rec)
            return jsonify({'status':'ok','mission':rec})
        except Exception as e:
            logger.error(f"end_mission error: {e}")
            return jsonify({'status':'error','message':str(e)}),500

    @app.route('/api/missions/<mission_id>/join', methods=['POST'])
    def join_mission(mission_id):
        try:
            token = request.headers.get('X-Session-Token')
            operator_id = None
            if operator_manager and token:
                op = operator_manager.get_operator_for_session(token)
                if op: operator_id = getattr(op,'operator_id', None) or getattr(op,'id',None)
            if not operator_id:
                body = request.get_json(silent=True) or {}
                operator_id = body.get('operator_id')
            if not operator_id:
                return jsonify({'status':'error','message':'operator id required'}),400
            with _metrics_db() as conn:
                conn.execute(
                    'INSERT OR IGNORE INTO mission_members (mission_id, operator_id, role, joined_at) VALUES (?, ?, ?, ?)',
                    (mission_id, operator_id, 'member', time.time())
                )
            return jsonify({'status':'ok','mission_id':mission_id,'operator_id':operator_id})
        except Exception as e:
            logger.error(f"join_mission error: {e}")
            return jsonify({'status':'error','message':str(e)}),500

    @app.route('/api/missions/<mission_id>/leave', methods=['POST'])
    def leave_mission(mission_id):
        try:
            token = request.headers.get('X-Session-Token')
            operator_id = None
            if operator_manager and token:
                op = operator_manager.get_operator_for_session(token)
                if op: operator_id = getattr(op,'operator_id', None) or getattr(op,'id',None)
            if not operator_id:
                body = request.get_json(silent=True) or {}
                operator_id = body.get('operator_id')
            if not operator_id:
                return jsonify({'status':'error','message':'operator id required'}),400
            with _metrics_db() as conn:
                conn.execute(
                    'DELETE FROM mission_members WHERE mission_id = ? AND operator_id = ?',
                    (mission_id, operator_id)
                )
            return jsonify({'status':'ok','mission_id':mission_id,'operator_id':operator_id})
        except Exception as e:
            logger.error(f"leave_mission error: {e}")
            return jsonify({'status':'error','message':str(e)}),500

    @app.route('/api/missions/<mission_id>/operators', methods=['GET'])
    def list_mission_operators(mission_id):
        try:
            import sqlite3
            conn = sqlite3.connect(os.path.join(_data_dir(),'metrics.db'))
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute('SELECT operator_id, role, joined_at FROM mission_members WHERE mission_id = ?', (mission_id,))
            rows = [dict(r) for r in c.fetchall()]
            conn.close()
            return jsonify({'status':'ok','operators':rows})
        except Exception as e:
            logger.error(f"list_mission_operators error: {e}")
            return jsonify({'status':'error','message':str(e)}),500

    @app.route('/api/missions/run/fusion_demo_5km', methods=['POST'])
    def run_fusion_mission_demo():
        """Run the Fusion Demo 5km Mission (RTL-SDR Simulation + AoA/TDoA)"""
        if sensor_registry_instance is None:
             return jsonify({'status':'error','message':'Sensor Registry not initialized'}), 503

        try:
            from mission_runner import run_fusion_demo_5km

            logger.info("Starting Fusion Demo 5km Mission...")
            trace = run_fusion_demo_5km(sensor_registry_instance)
            logger.info("Fusion Demo 5km Mission completed.")

            return jsonify({'status': 'ok', 'message': 'Mission executed successfully', 'trace': trace})
        except Exception as e:
            logger.error(f"Fusion Mission Demo Error: {e}")
            return jsonify({'status':'error', 'message': str(e)}), 500

    @app.route('/api/missions/<mission_id>/subgraph', methods=['GET'])
    def mission_subgraph(mission_id):
        try:
            # optional DSL override
            dsl = request.args.get('dsl') or ''
            parsed = {}
            if dsl and 'parse_dsl' in globals() and parse_dsl:
                try:
                    parsed = parse_dsl(dsl)
                except Exception:
                    parsed = {}

            # build mission predicate wrapper
            def _mission_filter_node(n):
                try:
                    labels = n.get('labels') or {}
                    return labels.get('missionId') == mission_id or n.get('metadata',{}).get('missionId') == mission_id
                except Exception:
                    return False

            # Query engine
            engine = globals().get('hypergraph_engine') or globals().get('hypergraph_store')
            if not engine:
                return jsonify({'status':'error','message':'Engine not available'}),503

            # Use SubgraphDiffGenerator's snapshot helper if available, else do simple scan
            snapshot = None
            try:
                if SubgraphDiffGenerator:
                    pred = QueryPredicate(parsed)
                    # wrap predicate to include mission scoping
                    def wrapped_pred(node_or_edge):
                        try:
                            if isinstance(node_or_edge, dict):
                                labels = node_or_edge.get('labels') or {}
                                if labels.get('missionId') == mission_id: return True
                                if node_or_edge.get('metadata',{}).get('missionId') == mission_id: return True
                        except Exception:
                            pass
                        return pred.matches(node_or_edge) if hasattr(pred,'matches') else False
                    # best-effort: ask engine for scan or snapshot
                    if hasattr(engine, 'query_subgraph'):
                        snapshot = engine.query_subgraph(wrapped_pred)
                    else:
                        # fallback: scan nodes/edges in hypergraph_store
                        nodes = []
                        edges = []
                        try:
                            store = globals().get('hypergraph_store')
                            for nid,n in (getattr(store,'nodes',{}) or {}).items():
                                if _mission_filter_node(n): nodes.append(n)
                            for e in (getattr(store,'hyperedges',[]) or []):
                                # coarse check on metadata
                                if e.get('metadata',{}).get('missionId') == mission_id:
                                    edges.append(e)
                        except Exception:
                            pass
                        snapshot = {'nodes': nodes, 'edges': edges}
                else:
                    snapshot = {'nodes': [], 'edges': []}
            except Exception as e:
                logger.warning(f"mission_subgraph error building snapshot: {e}")
                snapshot = {'nodes': [], 'edges': []}

            return jsonify({'status':'ok','mission_id':mission_id,'subgraph':snapshot})
        except Exception as e:
            logger.error(f"mission_subgraph error: {e}")
            return jsonify({'status':'error','message':str(e)}),500

    @app.route('/api/missions/<mission_id>/diff/stream', methods=['GET'])
    def mission_diff_stream(mission_id):
        """SSE stream of subgraph diffs scoped to a missionId."""
        if SubgraphDiffGenerator is None or QueryPredicate is None:
            return jsonify({'status': 'error', 'message': 'Subgraph diff module not available'}), 500

        if not operator_manager:
            return jsonify({'status': 'error', 'message': 'Operator Manager not available'}), 503

        token = request.args.get('token')
        if not token:
            return jsonify({'status': 'error', 'message': 'Session token required'}), 401

        client = operator_manager.register_sse_client(token)
        if not client:
            return jsonify({'status': 'error', 'message': 'Invalid session token'}), 401

        # Build a predicate that filters to mission scope
        parsed = {}
        pred = QueryPredicate(parsed)
        def mission_pred(x):
            try:
                labels = x.get('labels') or {}
                if labels.get('missionId') == mission_id: return True
                if x.get('metadata',{}).get('missionId') == mission_id: return True
            except Exception:
                pass
            # fall back to parsed predicate
            try:
                return pred.matches(x) if hasattr(pred,'matches') else False
            except Exception:
                return False

        engine = globals().get('hypergraph_engine') or globals().get('hypergraph_store')
        redis_conn = globals().get('redis_client')
        gen = SubgraphDiffGenerator(engine, operator_manager=operator_manager, redis_client=redis_conn)

        since = request.args.get('since')
        try:
            last_seq = int(since) if since else (operator_manager.entity_sequence if operator_manager else 0)
        except Exception:
            last_seq = 0

        cond = threading.Condition()
        max_seq = {'v': last_seq}

        # subscribe to graph_event_bus if present (reuse existing pattern)
        subscription = None
        try:
            if 'graph_event_bus' in globals() and graph_event_bus is not None:
                def _on_event(ge):
                    try:
                        seq = getattr(ge, 'sequence_id', None) or ge.get('sequence_id') if isinstance(ge, dict) else None
                        if seq is None:
                            seq = getattr(ge, 'sequence', None)
                        if seq is None:
                            return
                        logger.info(f"mission_diff_stream _on_event mission={mission_id} seq={seq}")
                        with cond:
                            if seq > max_seq['v']:
                                max_seq['v'] = int(seq)
                            cond.notify()
                    except Exception:
                        pass

                try:
                    graph_event_bus.subscribe(_on_event)
                    subscription = _on_event
                except Exception:
                    subscription = None
        except Exception:
            subscription = None

        def generate():
            nonlocal last_seq
            try:
                eb = globals().get('graph_event_bus')
                while True:
                    # Fast-path: replay in-process event bus history since last_seq
                    try:
                        if eb and hasattr(eb, 'replay'):
                            recent = eb.replay(last_seq)
                            if recent:
                                # determine max sequence in recent events
                                maxseq = last_seq
                                for e in recent:
                                    try:
                                        seq = getattr(e, 'sequence_id', None) if not isinstance(e, dict) else e.get('sequence_id')
                                        if seq is None:
                                            seq = getattr(e, 'sequence', None) if not isinstance(e, dict) else e.get('sequence')
                                        if seq and int(seq) > int(maxseq):
                                            maxseq = int(seq)
                                    except Exception:
                                        continue
                                if maxseq > last_seq:
                                    try:
                                        diff = gen.generate_diff(f'mission:{mission_id}', QueryPredicate({'missionId': mission_id}), last_seq, maxseq)
                                        last_seq = maxseq
                                        payload = json.dumps(diff)
                                        yield f"event: DIFF\n"
                                        yield f"data: {payload}\n\n"
                                        # continue to next iteration without waiting
                                        continue
                                    except GeneratorExit:
                                        break
                                    except Exception as e:
                                        logger.info(f"Error producing mission diff (replay path): {e}")
                    except Exception:
                        pass

                    # Fallback: wait for condition notified by subscription or timeout
                    with cond:
                        cond.wait(timeout=5.0)
                        current = max_seq['v']
                    if current is None:
                        current = last_seq
                    if current > last_seq:
                        try:
                            diff = gen.generate_diff(f'mission:{mission_id}', QueryPredicate({'missionId': mission_id}), last_seq, current)
                            last_seq = current
                            payload = json.dumps(diff)
                            yield f"event: DIFF\n"
                            yield f"data: {payload}\n\n"
                        except GeneratorExit:
                            break
                        except Exception as e:
                            logger.info(f"Error producing mission diff: {e}")
                    else:
                        hb = json.dumps({'mission_id': mission_id, 'to_sequence': last_seq, 'timestamp': datetime.utcnow().isoformat() + 'Z'})
                        try:
                            yield f"event: HEARTBEAT\n"
                            yield f"data: {hb}\n\n"
                        except GeneratorExit:
                            break
            finally:
                try:
                    if subscription and 'graph_event_bus' in globals() and graph_event_bus is not None:
                        try:
                            graph_event_bus.unsubscribe(subscription)
                        except Exception:
                            pass
                except Exception:
                    pass

        return Response(
            generate(), mimetype='text/event-stream', headers={'Cache-Control':'no-cache','Connection':'keep-alive','X-Accel-Buffering':'no'}
        )

    # ------------------------------------------------------------------
    # Mission Tasks CRUD
    # ------------------------------------------------------------------
    @app.route('/api/missions/<mission_id>/tasks', methods=['POST'])
    def create_mission_task(mission_id):
        """Create a task scoped to a mission."""
        try:
            # ensure mission exists
            if _load_mission_from_db(mission_id) is None:
                return jsonify({'status': 'error', 'message': 'mission not found'}), 404

            data = request.get_json() or {}
            title = data.get('title') or data.get('name') or 'task'
            status = data.get('status', 'PENDING')
            priority = int(data.get('priority', 5))
            payload = data.get('payload') or {}

            task_id = f"{mission_id}_task_{int(time.time()*1000)}_{random.randint(1,9999)}"
            nowt = time.time()

            import sqlite3
            dbp = os.path.join(_data_dir(), 'metrics.db')
            conn = sqlite3.connect(dbp)
            c = conn.cursor()
            c.execute('''INSERT OR REPLACE INTO mission_tasks (task_id, mission_id, title, status, priority, payload, created_at, updated_at)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', (
                task_id, mission_id, title, status, priority, json.dumps(payload), nowt, nowt
            ))
            conn.commit()
            conn.close()

            return jsonify({'status': 'ok', 'task_id': task_id, 'mission_id': mission_id, 'title': title}), 201
        except Exception as e:
            logger.error(f"create_mission_task error: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/missions/<mission_id>/tasks', methods=['GET'])
    def list_mission_tasks(mission_id):
        """List tasks for a mission."""
        try:
            import sqlite3
            dbp = os.path.join(_data_dir(), 'metrics.db')
            conn = sqlite3.connect(dbp)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute('SELECT task_id, mission_id, title, status, priority, payload, created_at, updated_at FROM mission_tasks WHERE mission_id = ? ORDER BY created_at DESC', (mission_id,))
            rows = [dict(r) for r in c.fetchall()]
            conn.close()
            # parse payload JSON
            for r in rows:
                try:
                    r['payload'] = json.loads(r.get('payload') or '{}')
                except Exception:
                    r['payload'] = {}
            return jsonify({'status': 'ok', 'mission_id': mission_id, 'tasks': rows})
        except Exception as e:
            logger.error(f"list_mission_tasks error: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/missions/<mission_id>/tasks/<task_id>', methods=['GET', 'PATCH', 'DELETE'])
    def mission_task_item(mission_id, task_id):
        """Get, update, or delete a mission task."""
        try:
            import sqlite3
            dbp = os.path.join(_data_dir(), 'metrics.db')
            conn = sqlite3.connect(dbp)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            # ensure task exists and belongs to mission
            c.execute('SELECT * FROM mission_tasks WHERE task_id = ? AND mission_id = ?', (task_id, mission_id))
            row = c.fetchone()
            if not row:
                conn.close()
                return jsonify({'status': 'error', 'message': 'task not found'}), 404

            if request.method == 'GET':
                rec = dict(row)
                try:
                    rec['payload'] = json.loads(rec.get('payload') or '{}')
                except Exception:
                    rec['payload'] = {}
                conn.close()
                return jsonify({'status': 'ok', 'task': rec})

            if request.method == 'PATCH':
                data = request.get_json() or {}
                title = data.get('title', row['title'])
                status = data.get('status', row['status'])
                priority = int(data.get('priority', row['priority'] or 5))
                payload = data.get('payload') or json.loads(row['payload'] or '{}')
                updated = time.time()
                c.execute('''UPDATE mission_tasks SET title = ?, status = ?, priority = ?, payload = ?, updated_at = ? WHERE task_id = ?''', (
                    title, status, priority, json.dumps(payload), updated, task_id
                ))
                conn.commit()
                conn.close()
                return jsonify({'status': 'ok', 'task_id': task_id, 'mission_id': mission_id})

            if request.method == 'DELETE':
                c.execute('DELETE FROM mission_tasks WHERE task_id = ? AND mission_id = ?', (task_id, mission_id))
                conn.commit()
                conn.close()
                return jsonify({'status': 'ok', 'deleted': task_id})

        except Exception as e:
            logger.error(f"mission_task_item error: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    # ------------------------------------------------------------------
    # Mission Watchlist CRUD
    # ------------------------------------------------------------------
    @app.route('/api/missions/<mission_id>/watchlist', methods=['POST'])
    def add_watchlist_entry(mission_id):
        """Add an entity to the mission watchlist."""
        try:
            if _load_mission_from_db(mission_id) is None:
                return jsonify({'status': 'error', 'message': 'mission not found'}), 404

            data = request.get_json() or {}
            entity_id = data.get('entity_id')
            note = data.get('note', '')
            if not entity_id:
                return jsonify({'status': 'error', 'message': 'entity_id required'}), 400

            nowt = time.time()
            import sqlite3
            dbp = os.path.join(_data_dir(), 'metrics.db')
            conn = sqlite3.connect(dbp)
            c = conn.cursor()
            try:
                c.execute('INSERT OR IGNORE INTO mission_watchlist (mission_id, entity_id, note, added_at) VALUES (?, ?, ?, ?)', (mission_id, entity_id, note, nowt))
                conn.commit()
                # fetch inserted/existing row id
                c.execute('SELECT id, mission_id, entity_id, note, added_at FROM mission_watchlist WHERE mission_id = ? AND entity_id = ?', (mission_id, entity_id))
                r = c.fetchone()
                rec = None
                if r:
                    rec = {'id': r[0], 'mission_id': r[1], 'entity_id': r[2], 'note': r[3], 'added_at': r[4]}
                conn.close()
                return jsonify({'status': 'ok', 'entry': rec}), 201
            except Exception as ie:
                conn.close()
                raise ie

        except Exception as e:
            logger.error(f"add_watchlist_entry error: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/missions/<mission_id>/watchlist', methods=['GET'])
    def list_watchlist(mission_id):
        """List watchlist entries for a mission."""
        try:
            import sqlite3
            dbp = os.path.join(_data_dir(), 'metrics.db')
            conn = sqlite3.connect(dbp)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute('SELECT id, mission_id, entity_id, note, added_at FROM mission_watchlist WHERE mission_id = ? ORDER BY added_at DESC', (mission_id,))
            rows = [dict(r) for r in c.fetchall()]
            conn.close()
            return jsonify({'status': 'ok', 'mission_id': mission_id, 'watchlist': rows})
        except Exception as e:
            logger.error(f"list_watchlist error: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/missions/<mission_id>/watchlist/<int:entry_id>', methods=['GET', 'DELETE'])
    def mission_watchlist_item(mission_id, entry_id):
        """Get or remove a watchlist entry."""
        try:
            import sqlite3
            dbp = os.path.join(_data_dir(), 'metrics.db')
            conn = sqlite3.connect(dbp)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute('SELECT id, mission_id, entity_id, note, added_at FROM mission_watchlist WHERE id = ? AND mission_id = ?', (entry_id, mission_id))
            row = c.fetchone()
            if not row:
                conn.close()
                return jsonify({'status': 'error', 'message': 'watchlist entry not found'}), 404

            if request.method == 'GET':
                rec = dict(row)
                conn.close()
                return jsonify({'status': 'ok', 'entry': rec})

            if request.method == 'DELETE':
                c.execute('DELETE FROM mission_watchlist WHERE id = ? AND mission_id = ?', (entry_id, mission_id))
                conn.commit()
                conn.close()
                return jsonify({'status': 'ok', 'deleted': entry_id})

        except Exception as e:
            logger.error(f"mission_watchlist_item error: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/hypergraph/query/register', methods=['POST'])
    def register_hypergraph_query():
        """Register a DSL query server-side and return a stable `query_id`.

        Body: { "dsl": "FIND ...", "query_id": "optional_custom_id" }
        Returns: { status: ok, query_id: "...", parsed: {...} }
        """
        # Require session token (header X-Session-Token or body)
        token = request.headers.get('X-Session-Token') or (request.get_json(silent=True) or {}).get('token')
        if not token or not operator_manager:
            return jsonify({'status': 'error', 'message': 'Session token required'}), 401
        operator = operator_manager.get_operator_for_session(token)
        if not operator:
            return jsonify({'status': 'error', 'message': 'Invalid session token'}), 401

        try:
            data = request.get_json(silent=True) or {}
            dsl = data.get('dsl') or ''
            provided_qid = data.get('query_id')
            if not dsl:
                return jsonify({'status': 'error', 'message': 'dsl required'}), 400

            parsed = None
            try:
                parsed = parse_dsl(dsl) if parse_dsl else {}
            except Exception:
                parsed = {}

            import uuid as _uq
            qid = provided_qid or _uq.uuid4().hex
            entry = {
                'dsl': dsl,
                'parsed': parsed,
                'created_at': datetime.utcnow().isoformat() + 'Z',
                'owner': getattr(operator, 'username', getattr(operator, 'session_id', 'unknown'))
            }
            with REGISTERED_QUERIES_LOCK:
                REGISTERED_QUERIES[qid] = entry

            return jsonify({'status': 'ok', 'query_id': qid, 'parsed': parsed})
        except Exception as e:
            logger.error(f"Error registering query: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/hypergraph/query/register', methods=['GET'])
    def list_registered_queries():
        """List all registered queries (returns map of query_id -> metadata)."""
        # Require session token
        token = request.headers.get('X-Session-Token') or request.args.get('token')
        if not token or not operator_manager:
            return jsonify({'status': 'error', 'message': 'Session token required'}), 401
        operator = operator_manager.get_operator_for_session(token)
        if not operator:
            return jsonify({'status': 'error', 'message': 'Invalid session token'}), 401

        try:
            with REGISTERED_QUERIES_LOCK:
                # Optionally, only return owner-owned queries; for now, return all but label ownership
                summary = {qid: {'created_at': entry.get('created_at'), 'dsl_preview': (entry.get('dsl') or '')[:200], 'owner': entry.get('owner')} for qid, entry in REGISTERED_QUERIES.items()}
            return jsonify({'status': 'ok', 'queries': summary})
        except Exception as e:
            logger.error(f"Error listing registered queries: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/hypergraph/query/register/<query_id>', methods=['GET'])
    def get_registered_query(query_id):
        """Return stored DSL and parsed AST for a `query_id`."""
        # Require session token
        token = request.headers.get('X-Session-Token') or request.args.get('token')
        if not token or not operator_manager:
            return jsonify({'status': 'error', 'message': 'Session token required'}), 401
        operator = operator_manager.get_operator_for_session(token)
        if not operator:
            return jsonify({'status': 'error', 'message': 'Invalid session token'}), 401

        try:
            with REGISTERED_QUERIES_LOCK:
                entry = REGISTERED_QUERIES.get(query_id)
            if not entry:
                return jsonify({'status': 'error', 'message': 'query_id not found'}), 404
            return jsonify({'status': 'ok', 'query_id': query_id, 'entry': entry})
        except Exception as e:
            logger.error(f"Error fetching registered query {query_id}: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/hypergraph/query/register/<query_id>', methods=['DELETE'])
    def delete_registered_query(query_id):
        """Delete a registered query by id."""
        # Require session token and owner match (only owner can delete)
        token = request.headers.get('X-Session-Token') or request.args.get('token')
        if not token or not operator_manager:
            return jsonify({'status': 'error', 'message': 'Session token required'}), 401
        operator = operator_manager.get_operator_for_session(token)
        if not operator:
            return jsonify({'status': 'error', 'message': 'Invalid session token'}), 401

        try:
            with REGISTERED_QUERIES_LOCK:
                existed = REGISTERED_QUERIES.get(query_id)
                if not existed:
                    return jsonify({'status': 'error', 'message': 'query_id not found'}), 404
                owner = existed.get('owner')
                op_name = getattr(operator, 'username', getattr(operator, 'session_id', None))
                # Allow deletion if owner matches or operator has admin flag
                allow = False
                try:
                    if owner and op_name and owner == op_name:
                        allow = True
                except Exception:
                    allow = False
                # admin check (best-effort)
                try:
                    if getattr(operator, 'is_admin', False) or getattr(operator, 'role', '') == 'admin':
                        allow = True
                except Exception:
                    pass
                if not allow:
                    return jsonify({'status': 'error', 'message': 'forbidden - only owner or admin can delete'}), 403
                REGISTERED_QUERIES.pop(query_id, None)
            return jsonify({'status': 'ok', 'deleted': query_id})
        except Exception as e:
            logger.error(f"Error deleting registered query {query_id}: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500


    @app.route('/api/satellites/refresh', methods=['POST', 'GET'])
    def refresh_satellites():
        """Trigger an immediate satellite TLE fetch & propagation (runs async)."""
        try:
            cats = request.args.get('categories', 'visual,starlink,active')
            categories = [c.strip() for c in cats.split(',') if c.strip()]

            def _run_once():
                try:
                    all_tles = []
                    for cat in categories:
                        tles = fetch_tles_from_celestrak(cat)
                        if tles:
                            all_tles.extend(tles)
                    if all_tles:
                        update_satellite_db_from_tles(all_tles, operator='Celestrak')
                        logger.info('Manual satellite refresh completed')
                except Exception as e:
                    logger.error(f'Manual satellite refresh error: {e}')

            threading.Thread(target=_run_once, daemon=True).start()
            return jsonify({'status': 'ok', 'message': 'Satellite refresh started', 'categories': categories})
        except Exception as e:
            logger.error(f'Error starting satellite refresh: {e}')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    # Register populate endpoint (defined later in module) if available
    try:
        if 'api_populate_satellites' in globals():
            app.add_url_rule('/api/satellites/populate', 'api_populate_satellites', globals()['api_populate_satellites'], methods=['POST'])
            logger.info('Registered /api/satellites/populate route')
    except Exception as e:
        logger.warning(f'Could not register populate route at startup: {e}')

    @app.route('/api/rf-hypergraph/node', methods=['POST'])
    def add_hypergraph_node():
        """Add a node to the hypergraph"""
        try:
            data = request.get_json()
            node_id = hypergraph_store.add_node(data)
            return jsonify({'status': 'ok', 'node_id': node_id})
        except Exception as e:
            logger.error(f"Error adding node: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/rf-hypergraph/edge', methods=['POST'])
    def add_hypergraph_edge():
        """Add a hyperedge to the hypergraph"""
        try:
            data = request.get_json()
            edge_idx = hypergraph_store.add_hyperedge(data)
            return jsonify({'status': 'ok', 'edge_index': edge_idx})
        except Exception as e:
            logger.error(f"Error adding edge: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    # ── Map State Cache API ────────────────────────────────────────────────────

    @app.route('/api/cache/arcs', methods=['GET'])
    def cache_warm_arcs():
        """Return recently-active cached arcs for globe warm-boot.

        Supports binary msgpack encoding for ~70% smaller payloads at scale.
        Client requests msgpack via: Accept: application/msgpack
        Falls back to JSON when Accept header not present or msgpack unavailable.

        Query params:
          max_age  — max arc age in seconds (default 90)
          shadow   — '1' to include shadow-graph arcs (default '0')
        """
        if map_cache is None:
            return jsonify({'status': 'ok', 'edges': [], 'count': 0, 'cached': False})
        try:
            max_age    = float(request.args.get('max_age', 90))
            inc_shadow = request.args.get('shadow', '0') == '1'
            raw        = map_cache.restore_arcs(max_age_secs=max_age)
            edges = []
            for a in raw:
                if not inc_shadow and a.get('shadow'):
                    continue
                edges.append({
                    'edge_id':      a['edge_id'],
                    'src':          a['src_id'],
                    'dst':          a['dst_id'],
                    'src_lat':      a['src_lat'],
                    'src_lon':      a['src_lon'],
                    'dst_lat':      a['dst_lat'],
                    'dst_lon':      a['dst_lon'],
                    'confidence':   a['conf'],
                    'entropy':      a['entropy'],
                    'rf_corr':      a['rf_corr'],
                    'shadow':       bool(a['shadow']),
                    'kind':         a['kind'],
                    'last_seen':    a['last_seen'],
                    'anomaly_score': float(a.get('anomaly_score') or 0.0),
                })
            payload = {'status': 'ok', 'edges': edges, 'count': len(edges), 'cached': True}

            # Binary msgpack response when client supports it
            accept = request.headers.get('Accept', '')
            if 'application/msgpack' in accept:
                try:
                    import msgpack as _msgpack
                    return Response(
                        _msgpack.packb(payload, use_bin_type=True),
                        content_type='application/msgpack'
                    )
                except ImportError:
                    pass  # fall through to JSON

            return jsonify(payload)
        except Exception as e:
            logger.error(f'[MapCache] /api/cache/arcs error: {e}')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/cache/nodes', methods=['GET'])
    def cache_node_geos():
        """Return the full node geo index — all known node → lat/lon mappings.

        Useful for pre-seeding the globe with node positions before arc data arrives.
        Supports msgpack binary encoding via Accept: application/msgpack.

        Query params:
          min_conf — minimum confidence threshold (default 0.0 = all)
          method   — filter by resolution method: 'observed', 'neighbor_inferred', etc.
        """
        if map_cache is None:
            return jsonify({'status': 'ok', 'nodes': [], 'count': 0})
        try:
            min_conf = float(request.args.get('min_conf', 0.0))
            method   = request.args.get('method')
            with map_cache._conn() as c:
                sql  = "SELECT node_id, lat, lon, asn, confidence, method FROM node_geo_index WHERE confidence >= ?"
                args = [min_conf]
                if method:
                    sql  += " AND method = ?"
                    args.append(method)
                rows = c.execute(sql, args).fetchall()
            nodes = [dict(r) for r in rows]
            payload = {'status': 'ok', 'nodes': nodes, 'count': len(nodes)}

            accept = request.headers.get('Accept', '')
            if 'application/msgpack' in accept:
                try:
                    import msgpack as _msgpack
                    return Response(
                        _msgpack.packb(payload, use_bin_type=True),
                        content_type='application/msgpack'
                    )
                except ImportError:
                    pass
            return jsonify(payload)
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/cache/camera', methods=['GET', 'POST'])
    def cache_camera_state():
        """Save or restore the last globe camera position.

        POST JSON: { "lat": 37.7, "lon": -122.4, "height": 5000000 }
        GET: returns { "status": "ok", "camera": { lat, lon, height } }
        """
        if map_cache is None:
            return jsonify({'status': 'ok', 'camera': None})
        try:
            if request.method == 'POST':
                d = request.get_json() or {}
                map_cache.save_camera(
                    float(d.get('lat',    20.0)),
                    float(d.get('lon',     0.0)),
                    float(d.get('height', 15_000_000.0))
                )
                return jsonify({'status': 'ok'})
            else:
                return jsonify({'status': 'ok', 'camera': map_cache.get_camera()})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/cache/stats', methods=['GET'])
    def cache_stats():
        """Return cache statistics: arc count, geo-path count, camera state."""
        if map_cache is None:
            return jsonify({'status': 'ok', 'available': False})
        return jsonify({'status': 'ok', 'available': True, **map_cache.stats()})

    @app.route('/api/map/tile/<provider>/<int:z>/<int:x>/<int:y>')
    def api_map_tile_proxy(provider, z, x, y):
        """Server-side proxy for map tiles with 24-hour persistence."""
        if map_tile_cache is None:
            return "Tile proxy unavailable", 503

        api_key = request.args.get('api_key', '')
        result = map_tile_cache.get_tile(provider, z, x, y, api_key=api_key)

        if result:
            data, content_type = result
            return Response(data, mimetype=content_type)
        else:
            return "Tile not found", 404

    @app.route('/api/admin/emit', methods=['POST'])
    def admin_emit():
        """Administrative emit endpoint: accept event dict(s) and publish to GraphEventBus.

        POST JSON: { "events": [ {event}, ... ] } or single event JSON.
        If environment var ADMIN_API_KEY is set, a matching header `X-ADMIN-KEY`
        or query param `admin_key` is required.
        """
        try:
            # optional admin key protection
            admin_key = os.environ.get('ADMIN_API_KEY')
            if admin_key:
                provided = request.headers.get('X-ADMIN-KEY') or request.args.get('admin_key')
                if provided != admin_key:
                    return jsonify({'status': 'error', 'message': 'invalid admin key'}), 401

            payload = request.get_json(silent=True) or {}
            events = payload.get('events') if isinstance(payload, dict) else None
            if events is None:
                # allow a single event body
                if isinstance(payload, dict) and payload:
                    events = [payload]
                else:
                    return jsonify({'status': 'error', 'message': 'no events provided'}), 400

            if 'graph_event_bus' not in globals() or graph_event_bus is None:
                return jsonify({'status': 'error', 'message': 'GraphEventBus not configured on server'}), 503

            results = []
            from types import SimpleNamespace
            for ev in events:
                try:
                    obj = SimpleNamespace(**ev) if isinstance(ev, dict) else ev
                    pubres = graph_event_bus.publish(obj)
                    # pubres is a dict: { 'msg_id': ..., 'sequence_id': ... }
                    msg_id = pubres.get('msg_id') if isinstance(pubres, dict) else pubres
                    seq = pubres.get('sequence_id') if isinstance(pubres, dict) else getattr(obj, 'sequence_id', None)
                    results.append({'status': 'ok', 'msg_id': msg_id, 'sequence_id': seq})
                except Exception as e:
                    logger.error(f'admin_emit publish error: {e}')
                    results.append({'status': 'error', 'message': str(e)})

            return jsonify({'status': 'ok', 'results': results})
        except Exception as e:
            logger.error(f'admin_emit handler error: {e}')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    # ========================================================================
    # API ROUTES - INFERENCE (Python + Parliament)
    # ========================================================================

    from threading import Lock as _InferLock
    _infer_lock = _InferLock()

    def _get_engine():
        """Return the live HypergraphEngine instance, or raise."""
        eng = globals().get('hypergraph_engine') or getattr(hypergraph_store, 'hypergraph_engine', None)
        if eng is not None:
            return eng
        # Last resort: check if it's a closure variable accessible via locals
        if 'hypergraph_engine' in dir():
            eng = locals().get('hypergraph_engine')
            if eng is not None:
                return eng
        raise RuntimeError('HypergraphEngine not available')

    def _get_engine_snapshot():
        """Prefer HypergraphEngine snapshot; fall back to legacy store."""
        try:
            eng = _get_engine()
            if hasattr(eng, 'snapshot'):
                return eng.snapshot()
        except RuntimeError:
            pass
        return {
            'nodes': list(getattr(hypergraph_store, 'nodes', {}).values()),
            'edges': [],
            'hyperedges': list(getattr(hypergraph_store, 'hyperedges', [])),
        }

    # --------------------------------------------------------------------
    # Authority State (canonical counts + health + write gate)
    # --------------------------------------------------------------------
    def _llm_available():
        try:
            req = urllib.request.Request(f'{_DEFAULT_OLLAMA_URL}/api/tags', method='GET')
            with urllib.request.urlopen(req, timeout=2) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _compute_authority_state():
        """Return authoritative counts + health used by UI tutorial gate."""
        node_count = edge_count = session_count = bsg_count = 0
        engine_present = False
        try:
            eng = _get_engine()
            engine_present = True
            nodes_obj = getattr(eng, 'nodes', {}) or {}
            nodes_iter = nodes_obj.values() if hasattr(nodes_obj, 'values') else nodes_obj
            node_count = len(nodes_obj) if hasattr(nodes_obj, '__len__') else len(list(nodes_iter))

            edges_obj = getattr(eng, 'edges', None)
            if edges_obj is None:
                edges_obj = getattr(eng, 'hyperedges', [])
            edge_count = len(edges_obj) if hasattr(edges_obj, '__len__') else len(list(edges_obj))

            for nd in nodes_iter:
                d = nd.to_dict() if hasattr(nd, 'to_dict') else (nd if isinstance(nd, dict) else {})
                k = (d.get('kind') or '').lower()
                if k in ('session', 'pcap_session'):
                    session_count += 1
                elif k == 'behavior_group':
                    bsg_count += 1
        except Exception:
            # Fall back to legacy store if engine unavailable
            nodes_obj = getattr(hypergraph_store, 'nodes', {}) or {}
            node_count = len(nodes_obj) if hasattr(nodes_obj, '__len__') else 0
            edge_count = len(getattr(hypergraph_store, 'hyperedges', []) or [])

        evidence_present = (session_count > 0) or (node_count > 0)
        tutorial_state = 'T2_AWAITING_INGEST'
        if not engine_present:
            tutorial_state = 'T7_DEGRADED'
        elif not evidence_present:
            tutorial_state = 'T2_AWAITING_INGEST'
        elif bsg_count == 0:
            tutorial_state = 'T5_EVIDENCE_BOUND'
        else:
            tutorial_state = 'T6_ANALYSIS_READY'

        # Write gate: allow only when operator session exists
        write_enabled = False
        write_reason = 'No authenticated operator provenance'
        try:
            op_mgr = globals().get('operator_manager')
            if op_mgr and getattr(op_mgr, 'sessions', None) is not None:
                write_enabled = len(op_mgr.sessions) > 0
                if write_enabled:
                    write_reason = None
        except Exception:
            write_enabled = False

        authority_backend = 'filesystem'
        try:
            if os.path.exists(os.path.join(_data_dir(), 'authority.db')):
                authority_backend = 'postgres'
        except Exception:
            pass

        return {
            'ok': True,
            'instance_id': app.config.get('SCYTHE_INSTANCE_ID', 'unknown'),
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'authoritative_state': {
                'sessions': session_count,
                'nodes': node_count,
                'edges': edge_count,
                'bsgs': bsg_count,
            },
            'tutorial_state': tutorial_state,
            'health': {
                'graph_engine': engine_present,
                'bsg_engine': bsg_count > 0,
                'authority': authority_backend,
                'llm': _llm_available(),
            },
            'write_access': {
                'enabled': bool(write_enabled),
                'reason': write_reason,
            },
        }

    @app.route('/api/authority/state', methods=['GET'])
    def api_authority_state():
        """Canonical authority envelope for UI + orchestrator."""
        try:
            return jsonify(_compute_authority_state())
        except Exception as e:
            logger.error(f'[authority] failed: {e}')
            return jsonify({'ok': False, 'error': str(e)}), 500

    @app.route('/api/infer/run', methods=['POST'])
    def api_infer_run():
        """Run inference (Python rules and/or Parliament).

        POST JSON:
        {
          "run_python": true,
          "run_owlrl": false,
          "owlrl_ontology_path": null,
          "owlrl_mode": "full",
          "owlrl_window_minutes": 10,
          "owlrl_depth": 2,
          "owlrl_max_nodes": 25000,
          "owlrl_max_edges": 200000,
          "pcap_session_id": null,
          "run_gemma": false,
          "gemma_model": "gemma3:1b",
          "gemma_flow_limit": 100,
          "push_to_parliament": false,
          "pull_from_parliament": false,
          "parliament_url": "http://localhost:8089/parliament/sparql"
        }
        """
        if not _infer_lock.acquire(blocking=False):
            return jsonify({'status': 'busy', 'message': 'inference already running'}), 429

        try:
            payload = request.get_json(silent=True) or {}
            run_python = bool(payload.get('run_python', True))
            run_owlrl_flag = bool(payload.get('run_owlrl', False))
            owlrl_ontology_path = payload.get('owlrl_ontology_path')
            owlrl_mode = (payload.get('owlrl_mode') or 'full').lower()
            owlrl_window_minutes = int(payload.get('owlrl_window_minutes', 10))
            owlrl_depth = int(payload.get('owlrl_depth', 2))
            owlrl_max_nodes = int(payload.get('owlrl_max_nodes', 25000))
            owlrl_max_edges = int(payload.get('owlrl_max_edges', 200000))
            owlrl_session_id = payload.get('pcap_session_id') or payload.get('owlrl_session_id')
            run_gemma_flag = bool(payload.get('run_gemma', False))
            gemma_model = payload.get('gemma_model', 'gemma3:1b')
            gemma_flow_limit = int(payload.get('gemma_flow_limit', 100))
            push_parliament = bool(payload.get('push_to_parliament', False))
            pull_parliament = bool(payload.get('pull_from_parliament', False))
            parliament_url = payload.get('parliament_url', 'http://localhost:8089/parliament/sparql')

            snap = _get_engine_snapshot()
            nodes = snap.get('nodes', [])
            edges = snap.get('edges', [])

            result = {
                'status': 'ok',
                'nodes': len(nodes),
                'edges_before': len(edges),
                'python_ops': 0,
                'owlrl_ops': 0,
                'owlrl_ok': False,
                'gemma_ops': 0,
                'parliament_push_ok': False,
                'parliament_pull_ops': 0,
            }

            # 1) Python inference
            if run_python:
                try:
                    from infer_rules_v0_1 import InferenceEngine
                    import writebus
                    from writebus import WriteContext

                    ops = InferenceEngine(nodes, edges).run_all()
                    if ops:
                        ctx = WriteContext(
                            room_name='Global',
                            source='python_rules',
                            model_version='rf_scythe_rules_v0_1',
                        )
                        writebus.bus().commit(
                            entity_id=f'infer_py_{int(time.time())}',
                            entity_type='inference_run_v0_1',
                            entity_data={'mode': 'python', 'op_count': len(ops)},
                            graph_ops=ops,
                            ctx=ctx,
                        )
                    result['python_ops'] = len(ops)
                except Exception as e:
                    logger.error(f'[infer] python inference failed: {e}')
                    result['python_error'] = str(e)

            # 2) OWL-RL materialization (Python-side, replaces Parliament reasoning)
            if run_owlrl_flag:
                try:
                    from owlrl_materializer import OWLRLMaterializer
                    import writebus
                    from writebus import WriteContext, GraphOp

                    existing_ids = {e.get('id') for e in edges if isinstance(e, dict) and e.get('id')}
                    mat = OWLRLMaterializer(ontology_path=owlrl_ontology_path) if owlrl_ontology_path else OWLRLMaterializer()

                    # Scoped reasoning view: incremental vs full
                    if owlrl_mode == 'incremental':
                        nodes_view, edges_view, owlrl_scope = select_reasoning_view_incremental(
                            nodes, edges,
                            window_minutes=owlrl_window_minutes,
                            pcap_session_id=owlrl_session_id,
                            depth=owlrl_depth,
                            max_nodes=owlrl_max_nodes,
                            max_edges=owlrl_max_edges,
                        )
                    else:
                        owlrl_scope = {'mode': 'full'}
                        edges_view = [e for e in edges if not _is_inferred_edge(e)]
                        nodes_view = nodes

                    raw_ops = mat.materialize(nodes_view, edges_view, existing_edge_ids=existing_ids)

                    # Normalize ops → EDGE_CREATE + nodes[] + metadata.obs_class/confidence
                    norm_ops = []
                    for op in raw_ops or []:
                        try:
                            et = getattr(op, 'event_type', None) or 'EDGE_CREATE'
                            eid = getattr(op, 'entity_id', None)
                            ed = dict(getattr(op, 'entity_data', None) or {})

                            # Canonical event types only
                            _CANONICAL = ('NODE_CREATE','NODE_UPDATE','NODE_DELETE',
                                          'EDGE_CREATE','EDGE_UPDATE','EDGE_DELETE',
                                          'HYPEREDGE_CREATE','HYPEREDGE_DELETE')
                            if et not in _CANONICAL:
                                et = 'EDGE_CREATE'

                            # Ensure edge endpoints are in nodes[]
                            if et.startswith('EDGE') or et.startswith('HYPEREDGE'):
                                nodes_list = ed.get('nodes') or []
                                if not nodes_list:
                                    src = ed.pop('source', None) or ed.pop('src', None)
                                    dst = ed.pop('target', None) or ed.pop('dst', None)
                                    if src and dst:
                                        ed['nodes'] = [src, dst]

                            # Promote obs_class/confidence from labels → metadata
                            labels = ed.get('labels') or {}
                            meta = ed.get('metadata') or {}
                            if 'obs_class' in labels and 'obs_class' not in meta:
                                meta['obs_class'] = labels['obs_class']
                            if 'confidence' in labels and 'confidence' not in meta:
                                meta['confidence'] = labels['confidence']
                            ed['metadata'] = meta

                            norm_ops.append(GraphOp(event_type=et, entity_id=eid, entity_data=ed))
                        except Exception:
                            continue

                    if norm_ops:
                        ctx = WriteContext(
                            room_name='Global',
                            source='owlrl_rules',
                            model_version='rf_scythe_owlrl_v0_1',
                        )
                        writebus.bus().commit(
                            entity_id=f'infer_owlrl_{int(time.time())}',
                            entity_type='inference_run_owlrl_v0_1',
                            entity_data={'mode': 'owlrl', 'op_count': len(norm_ops)},
                            graph_ops=norm_ops,
                            ctx=ctx,
                        )
                    result['owlrl_ops'] = len(norm_ops)
                    result['owlrl_ok'] = True
                    result['owlrl_scope'] = owlrl_scope
                    result['owlrl_input_counts'] = {'nodes': len(nodes_view), 'edges': len(edges_view)}
                except Exception as e:
                    logger.error(f'[infer] owlrl materialization failed: {e}')
                    result['owlrl_error'] = str(e)

            # 2b) Gemma 3 schema-bound inference via Ollama (TAK-ML style)
            if run_gemma_flag:
                try:
                    from tak_ml_gemma_runner import TakMlGemmaRunner, GemmaRunnerConfig
                    import writebus
                    from writebus import WriteContext

                    cfg = GemmaRunnerConfig(model_name=gemma_model)
                    snap_eng = globals().get('hypergraph_engine') or locals().get('hypergraph_engine')
                    if snap_eng is None:
                        result['gemma_error'] = 'hypergraph_engine not available'
                    else:
                        runner = TakMlGemmaRunner(snap_eng, cfg)
                        gemma_ops = runner.run_batch_return_ops(limit=gemma_flow_limit)
                        result['gemma_ops'] = len(gemma_ops)
                except Exception as e:
                    logger.error(f'[infer] gemma inference failed: {e}')
                    result['gemma_error'] = str(e)

            # 3 — kept for legacy compatibility) Push to Parliament
            if push_parliament:
                try:
                    from graphop_to_rdf import GraphToRDF
                    g2rdf = GraphToRDF()
                    push_res = g2rdf.push_to_parliament(
                        nodes, edges,
                        endpoint=parliament_url,
                        batch_size=500,
                    )
                    result['parliament_push_ok'] = push_res.get('ok', False)
                    result['parliament_triples_sent'] = push_res.get('triples_sent', 0)
                except Exception as e:
                    logger.error(f'[infer] push_to_parliament failed: {e}')
                    result['parliament_push_error'] = str(e)

            # 4) Pull from Parliament
            if pull_parliament:
                try:
                    import writebus
                    from writebus import WriteContext
                    from rdf_inferred_to_graphop import ParliamentInferenceSync

                    existing_ids = {e.get('id') for e in edges if isinstance(e, dict) and e.get('id')}
                    sync = ParliamentInferenceSync(endpoint=parliament_url)
                    inferred_ops = sync.pull_inferred(existing_edge_ids=existing_ids)

                    if inferred_ops:
                        ctx = WriteContext(
                            room_name='Global',
                            source='parliament_rules',
                            model_version='rf_scythe_owl_v0_1',
                        )
                        writebus.bus().commit(
                            entity_id=f'infer_parl_{int(time.time())}',
                            entity_type='inference_run_parliament_v0_1',
                            entity_data={'mode': 'parliament', 'op_count': len(inferred_ops)},
                            graph_ops=inferred_ops,
                            ctx=ctx,
                        )
                    result['parliament_pull_ops'] = len(inferred_ops)
                except Exception as e:
                    logger.error(f'[infer] pull_from_parliament failed: {e}')
                    result['parliament_pull_error'] = str(e)

            return jsonify(result)
        finally:
            _infer_lock.release()

    @app.route('/api/infer/rules', methods=['GET'])
    def api_infer_rules():
        """List available inference rules."""
        try:
            from infer_rules_v0_1 import InferenceEngine
            snap = _get_engine_snapshot()
            eng = InferenceEngine(snap.get('nodes', []), snap.get('edges', []))
            return jsonify({'status': 'ok', 'rules': eng.available_rules()})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/infer/status', methods=['GET'])
    def api_infer_status():
        """Check inference subsystem availability."""
        status = {'python_rules': False, 'owlrl': False, 'gemma': False, 'parliament': False}
        try:
            from infer_rules_v0_1 import InferenceEngine
            status['python_rules'] = True
        except ImportError:
            pass
        try:
            import owlrl as _owlrl_lib
            from owlrl_materializer import OWLRLMaterializer
            status['owlrl'] = True
        except ImportError:
            pass
        try:
            from tak_ml_gemma_runner import TakMlGemmaRunner
            from gemma_client import GemmaClient
            status['gemma'] = True
        except ImportError:
            pass
        try:
            from graphop_to_rdf import GraphToRDF
            from rdf_inferred_to_graphop import ParliamentInferenceSync
            status['parliament'] = True
        except ImportError:
            pass
        return jsonify({'status': 'ok', **status})

    # ========================================================================
    # API ROUTES - TAK-ML (Gemma 3 model-assisted enrichment)
    # ========================================================================

    @app.route('/api/tak-ml/infer', methods=['POST'])
    def api_takml_infer():
        """Run TAK-ML Gemma inference on flows/hosts.

        POST JSON:
        {
          "target": "flows" | "hosts" | "all",
          "limit": 100,
          "model": "gemma3:1b",
          "ollama_url": "http://localhost:11434"
        }
        """
        try:
            from tak_ml_gemma_runner import TakMlGemmaRunner, GemmaRunnerConfig

            payload = request.get_json(silent=True) or {}
            target = payload.get('target', 'flows')
            limit = int(payload.get('limit', 100))
            model = payload.get('model', 'gemma3:1b')
            ollama_url = payload.get('ollama_url', _DEFAULT_OLLAMA_URL)

            eng = globals().get('hypergraph_engine')
            if eng is None:
                return jsonify({'status': 'error', 'message': 'hypergraph_engine not available'}), 500

            cfg = GemmaRunnerConfig(
                model_name=model,
                ollama_url=ollama_url,
            )
            runner = TakMlGemmaRunner(eng, cfg)

            if target == 'hosts':
                ops_count = runner.run_for_all_hosts(limit=limit)
            elif target == 'all':
                ops_count = runner.run_for_all_flows(limit=limit)
                ops_count += runner.run_for_all_hosts(limit=limit)
            else:
                ops_count = runner.run_for_all_flows(limit=limit)

            return jsonify({
                'status': 'ok',
                'target': target,
                'model': model,
                'ops_committed': ops_count,
            })
        except Exception as e:
            logger.error(f'[tak-ml] inference failed: {e}')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/tak-ml/kserve/infer', methods=['POST'])
    def api_takml_kserve_infer():
        """Proxy a raw feature tensor to the KServe/Triton backend.

        Avoids browser→Triton direct calls (localhost confusion, CORS, auth gaps).
        POST JSON: {"model": str, "version": str, "features": [f0..f6]}
        Returns: {"status": "ok"|"error", "score": float|null, "reachable": bool}
        Always returns HTTP 200 so the UI can distinguish Triton-down from proxy errors.
        Accepts X-Session-Token (operator browser) or X-Internal-Token (gRPC servicer).
        """
        _int_tok = app.config.get('INTERNAL_TOKEN', '')
        _is_internal = bool(
            _int_tok and request.headers.get('X-Internal-Token') == _int_tok
        )
        if not _is_internal:
            token = request.headers.get('X-Session-Token') or (request.get_json(silent=True) or {}).get('token')
            if not token or not operator_manager:
                return jsonify({'status': 'error', 'reachable': False,
                                'message': 'Session token required'}), 401
            if not operator_manager.get_operator_for_session(token):
                return jsonify({'status': 'error', 'reachable': False,
                                'message': 'Invalid session token'}), 401

        takml_url = app.config.get('TAKML_URL', 'http://localhost:8234')
        body = request.get_json(silent=True) or {}
        model   = body.get('model', 'nerf_botnet_v1')
        version = str(body.get('version', '1'))
        features = body.get('features', [0.0] * 7)

        if not isinstance(features, list) or len(features) != 7:
            return jsonify({'status': 'error', 'reachable': False,
                            'message': 'features must be a list of 7 floats'}), 400

        payload = {
            'inputs': [{
                'name': 'features',
                'shape': [1, 7],
                'datatype': 'FP32',
                'data': [float(f) for f in features],
            }]
        }
        url = f'{takml_url}/v2/models/{model}/versions/{version}/infer'
        t0 = time.time()
        try:
            r = requests.post(url, json=payload, timeout=2.0)
            latency_ms = round((time.time() - t0) * 1000, 1)
            if r.status_code == 200:
                score = (r.json().get('outputs') or [{}])[0].get('data', [None])[0]
                return jsonify({'status': 'ok', 'score': score,
                                'reachable': True, 'latency_ms': latency_ms})
            return jsonify({'status': 'error', 'reachable': True, 'score': None,
                            'message': f'KServe HTTP {r.status_code}',
                            'latency_ms': latency_ms})
        except requests.exceptions.ConnectionError:
            return jsonify({'status': 'error', 'reachable': False, 'score': None,
                            'message': 'KServe server not reachable',
                            'latency_ms': round((time.time() - t0) * 1000, 1)})
        except Exception as e:
            return jsonify({'status': 'error', 'reachable': False, 'score': None,
                            'message': str(e),
                            'latency_ms': round((time.time() - t0) * 1000, 1)})

    @app.route('/api/tak-ml/kserve/health', methods=['GET'])
    def api_takml_kserve_health():
        """Check KServe/Triton server reachability and return model list.
        Accepts X-Session-Token (operator browser) or X-Internal-Token (gRPC servicer).
        """
        _int_tok = app.config.get('INTERNAL_TOKEN', '')
        _is_internal = bool(
            _int_tok and request.headers.get('X-Internal-Token') == _int_tok
        )
        if not _is_internal:
            token = request.headers.get('X-Session-Token') or request.args.get('token')
            if not token or not operator_manager:
                return jsonify({'reachable': False, 'message': 'Session token required'}), 401
            if not operator_manager.get_operator_for_session(token):
                return jsonify({'reachable': False, 'message': 'Invalid session token'}), 401

        takml_url = app.config.get('TAKML_URL', 'http://localhost:8234')
        t0 = time.time()
        try:
            r = requests.get(f'{takml_url}/v2/health/ready', timeout=1.5)
            latency_ms = round((time.time() - t0) * 1000, 1)
            reachable = r.status_code == 200
        except Exception:
            latency_ms = round((time.time() - t0) * 1000, 1)
            reachable = False
        return jsonify({
            'reachable': reachable,
            'base_url': takml_url,
            'latency_ms': latency_ms,
            'timestamp': time.time(),
        })

    @app.route('/api/grpc/health', methods=['GET'])
    def api_grpc_health():
        """Check gRPC server reachability (port 50051) and return service status.

        Returns: { reachable, host, port, latency_ms, services, timestamp }
        """
        import socket as _socket

        token = request.headers.get('X-Session-Token') or request.args.get('token')
        if not token or not operator_manager:
            return jsonify({'reachable': False, 'message': 'Session token required'}), 401
        if not operator_manager.get_operator_for_session(token):
            return jsonify({'reachable': False, 'message': 'Invalid session token'}), 401

        grpc_host = app.config.get('GRPC_HOST', '127.0.0.1')
        grpc_port = int(app.config.get('GRPC_PORT', 50051))

        t0 = time.time()
        reachable = False
        try:
            with _socket.create_connection((grpc_host, grpc_port), timeout=1.5):
                reachable = True
        except OSError:
            pass
        latency_ms = round((time.time() - t0) * 1000, 1)

        return jsonify({
            'reachable': reachable,
            'host': grpc_host,
            'port': grpc_port,
            'latency_ms': latency_ms,
            'services': [
                'ScytheStreamService',
                'ClusterIntelService',
                'TakMLService',
                'ReconEntityStream',
            ],
            'timestamp': time.time(),
        })

    @app.route('/api/tak-ml/status', methods=['GET'])
    def api_takml_status():
        """Check TAK-ML / Ollama / Gemma availability."""
        result = {
            'tak_ml': False,
            'ollama_reachable': False,
            'models': [],
            'target_model': 'gemma3:1b',
            'model_loaded': False,
        }
        try:
            from tak_ml_gemma_runner import TakMlGemmaRunner, GemmaRunnerConfig
            result['tak_ml'] = True

            eng = globals().get('hypergraph_engine')
            if eng:
                runner = TakMlGemmaRunner(eng)
                health = runner.is_available()
                result.update(health)
        except ImportError:
            pass
        except Exception as e:
            result['error'] = str(e)
        return jsonify(result)

    def _probe_stream_endpoint(url: str, timeout: float = 1.0) -> str:
        """Return online/offline by attempting a TCP connect to the endpoint host."""
        import socket as _sock
        from urllib.parse import urlparse

        parsed = urlparse(url)
        host = parsed.hostname or 'localhost'
        if parsed.port:
            port = parsed.port
        elif parsed.scheme in ('http', 'ws'):
            port = 80
        elif parsed.scheme in ('https', 'wss'):
            port = 443
        else:
            port = 8765
        try:
            _sock.create_connection((host, port), timeout=timeout).close()
            return 'online'
        except OSError:
            return 'offline'

    def _eve_stream_config() -> Dict[str, str]:
        return {
            'eve_stream_ws': app.config.get('EVE_STREAM_WS_URL', 'ws://localhost:8081/ws'),
            'eve_stream_http': app.config.get('EVE_STREAM_HTTP_URL', 'http://localhost:8081'),
        }

    def _eve_stream_runtime_params() -> Dict[str, Any]:
        from urllib.parse import urlparse

        cfg = _eve_stream_config()
        ws_parts = urlparse(cfg['eve_stream_ws'])
        http_parts = urlparse(cfg['eve_stream_http'])
        return {
            'host': ws_parts.hostname or http_parts.hostname or 'localhost',
            'ws_port': ws_parts.port or 8081,
            'http_port': http_parts.port or 8081,
            'ws_url': cfg['eve_stream_ws'],
            'http_url': cfg['eve_stream_http'],
        }

    def _run_eve_sensor_preflight(engine: Any) -> Dict[str, Any]:
        from eve_sensor_mcp import sensor_stream_tool

        params = _eve_stream_runtime_params()
        result = sensor_stream_tool({
            'host': params['host'],
            'ws_port': params['ws_port'],
            'http_port': params['http_port'],
            'check_only': True,
        }, engine)
        result.update({
            'health': _probe_stream_endpoint(params['http_url']),
            'eve_stream_ws': params['ws_url'],
            'eve_stream_http': params['http_url'],
        })
        return result

    def _run_graphops_sensor_grounding(
        engine: Any,
        *,
        reason: str,
        window_seconds: float,
        max_events: int,
        auto_trigger: bool,
        trust_posture: str,
    ) -> Dict[str, Any]:
        from eve_sensor_mcp import sensor_stream_tool

        params = _eve_stream_runtime_params()
        result = sensor_stream_tool({
            'host': params['host'],
            'ws_port': params['ws_port'],
            'http_port': params['http_port'],
            'window_seconds': window_seconds,
            'max_events': max_events,
            'check_only': False,
        }, engine)
        summary = {
            'ts': time.time(),
            'reason': reason,
            'auto_trigger': auto_trigger,
            'trust_posture': trust_posture,
            'eve_stream_ws': params['ws_url'],
            'eve_stream_http': params['http_url'],
            'host': params['host'],
            'ws_port': params['ws_port'],
            'http_port': params['http_port'],
            'window_seconds': window_seconds,
            'max_events': max_events,
            'streamer_available': bool(result.get('streamer_available', False)),
            'capture_metrics': result.get('capture_metrics', {}),
            'fetched_ws': int(result.get('fetched_ws', 0) or 0),
            'committed': int(result.get('committed', 0) or 0),
            'new_nodes': int(result.get('new_nodes', 0) or 0),
            'new_edges': int(result.get('new_edges', 0) or 0),
            'batch_nodes': int(result.get('batch_nodes', 0) or 0),
            'batch_edges': int(result.get('batch_edges', 0) or 0),
            'message': result.get('message', ''),
        }
        app.config['GRAPHOPS_SENSOR_GROUNDING_LAST'] = summary
        return summary

    def _should_auto_ground_graphops(message: str, write_summary: Dict[str, Any]) -> bool:
        posture = write_summary.get('trust_posture', 'sparse')
        if posture in ('sparse', 'inference-heavy'):
            return True
        msg = (message or '').lower()
        return any(term in msg for term in (
            'ground',
            'sensor',
            'observe',
            'observed',
            'verify',
            'confirm',
            'live traffic',
            'current traffic',
            'flow',
            'packet',
        ))

    @app.route('/api/sensor/eve/health', methods=['GET'])
    def api_sensor_eve_health():
        """Return eve-streamer health plus the last GraphOps grounding result."""
        try:
            eng = globals().get('hypergraph_engine')
            preflight = _run_eve_sensor_preflight(eng)
            return jsonify({
                'status': 'ok',
                **preflight,
                'last_grounding': app.config.get('GRAPHOPS_SENSOR_GROUNDING_LAST'),
            })
        except Exception as exc:
            logger.error(f'[eve-stream] health failed: {exc}')
            return jsonify({'status': 'error', 'message': str(exc)}), 503

    @app.route('/api/sensor/eve/ground', methods=['POST'])
    def api_sensor_eve_ground():
        """Run an explicit GraphOps sensor-grounding burst against eve-streamer."""
        try:
            eng = globals().get('hypergraph_engine')
            if eng is None:
                return jsonify({'status': 'error', 'message': 'hypergraph_engine not available'}), 500

            payload = request.get_json(silent=True) or {}
            window_seconds = max(0.5, float(payload.get('window_seconds', 2.5)))
            max_events = max(1, min(int(payload.get('max_events', 64)), 1000))
            reason = str(payload.get('reason', 'ui-ground-graphops'))

            preflight = _run_eve_sensor_preflight(eng)
            if not preflight.get('streamer_available'):
                return jsonify({
                    'status': 'offline',
                    'message': 'eve-streamer unavailable',
                    'health': preflight,
                    'last_grounding': app.config.get('GRAPHOPS_SENSOR_GROUNDING_LAST'),
                }), 503

            write_summary = {'trust_posture': 'manual-grounding'}
            try:
                from mcp_context import MCPBuilder
                write_summary = MCPBuilder(eng)._build_write_summary()
            except Exception as summary_exc:
                logger.warning(f'[eve-stream] write summary unavailable during grounding: {summary_exc}')

            grounding = _run_graphops_sensor_grounding(
                eng,
                reason=reason,
                window_seconds=window_seconds,
                max_events=max_events,
                auto_trigger=bool(payload.get('auto_trigger', False)),
                trust_posture=write_summary.get('trust_posture', 'unknown'),
            )
            return jsonify({
                'status': 'ok',
                'health': preflight,
                'grounding': grounding,
                'last_grounding': grounding,
            })
        except Exception as exc:
            logger.error(f'[eve-stream] grounding failed: {exc}')
            return jsonify({'status': 'error', 'message': str(exc)}), 500

    # ========================================================================
    # API ROUTES - TAK-GPT (GraphOps Chat Bot)
    # ========================================================================

    @app.route('/api/tak-gpt/chat', methods=['POST'])
    def api_takgpt_chat():
        """GraphOps chat bot — natural-language queries over the hypergraph.

        GraphOps operates as a System Principal (SYSTEM:GRAPHOPS), not as
        an operator.  Its provenance is tracked via WriteBus with
        author_class='system' and auth_level='bounded'.

        POST JSON:
        {
          "message": "Show me all hosts that touched tcp/443 in the last 10 min",
          "callsign": "OPERATOR-1",
          "latitude": 38.9072,
          "longitude": -77.0369,
          "model": "gemma3:1b"
        }
        """
        try:
            from tak_ml_gemma_runner import GraphOpsChatBot, GemmaRunnerConfig

            payload = request.get_json(silent=True) or {}
            message = payload.get('message', '')
            if not message:
                return jsonify({'status': 'error', 'message': 'no message provided'}), 400

            model = payload.get('model', 'gemma3:1b')
            ollama_url = payload.get('ollama_url', _DEFAULT_OLLAMA_URL)

            eng = globals().get('hypergraph_engine')
            if eng is None:
                return jsonify({'status': 'error', 'message': 'hypergraph_engine not available'}), 500

            cfg = GemmaRunnerConfig(model_name=model, ollama_url=ollama_url, timeout=300.0)
            bot = GraphOpsChatBot(eng, cfg)

            context = {}
            if payload.get('callsign'):
                context['callsign'] = payload['callsign']
            if payload.get('latitude') is not None:
                context['latitude'] = payload['latitude']
            if payload.get('longitude') is not None:
                context['longitude'] = payload['longitude']

            # Inject authoritative BSG projection into GraphOps context so
            # the LLM reasons only over the canonical, lossy projection.
            try:
                if 'instance_db' in globals() and instance_db and hasattr(instance_db, 'list_bsg_projection'):
                    proj = instance_db.list_bsg_projection()
                    context['bsg_projection'] = proj
            except Exception:
                logger.warning('Failed to attach BSG projection to GraphOps context', exc_info=True)

            try:
                from mcp_context import MCPBuilder
                write_summary = MCPBuilder(eng)._build_write_summary()
            except Exception as summary_exc:
                logger.warning(f'[graphops] write summary unavailable: {summary_exc}')
                write_summary = {
                    'trust_posture': 'unknown',
                    'evidence_coverage': 0.0,
                    'stale_inference_count': 0,
                }

            sensor_grounding = {
                'policy': {
                    'triggered': False,
                    'trust_posture': write_summary.get('trust_posture', 'unknown'),
                    'evidence_coverage': write_summary.get('evidence_coverage', 0.0),
                    'stale_inference_count': write_summary.get('stale_inference_count', 0),
                }
            }
            if _should_auto_ground_graphops(message, write_summary):
                sensor_grounding['policy']['triggered'] = True
                try:
                    preflight = _run_eve_sensor_preflight(eng)
                    sensor_grounding['preflight'] = preflight
                    if preflight.get('streamer_available'):
                        last_grounding = app.config.get('GRAPHOPS_SENSOR_GROUNDING_LAST') or {}
                        last_ts = float(last_grounding.get('ts', 0) or 0)
                        if last_ts and (time.time() - last_ts) < 30.0:
                            sensor_grounding['burst'] = last_grounding
                            sensor_grounding['reused_recent'] = True
                        else:
                            sensor_grounding['burst'] = _run_graphops_sensor_grounding(
                                eng,
                                reason='graphops-chat-auto',
                                window_seconds=2.5,
                                max_events=64,
                                auto_trigger=True,
                                trust_posture=write_summary.get('trust_posture', 'unknown'),
                            )
                    else:
                        sensor_grounding['error'] = 'eve-streamer unavailable'
                except Exception as grounding_exc:
                    logger.warning(f'[graphops] sensor grounding unavailable: {grounding_exc}')
                    sensor_grounding['error'] = str(grounding_exc)
            context['sensor_grounding'] = sensor_grounding

            # Default GraphOps mode — projection-only unless operator requests otherwise
            if 'graphops_mode' not in context:
                context['graphops_mode'] = payload.get('mode') or 'PROJECTION_SUMMARY'

            # Propagate GraphOps mode to global so MCP server can enforce hard boundaries
            prev_mode = globals().get('GRAPHOPS_MODE')
            globals()['GRAPHOPS_MODE'] = context.get('graphops_mode')
            try:
                response_text = bot.send_chat_request(message, context or None)
            finally:
                # Restore previous mode
                globals()['GRAPHOPS_MODE'] = prev_mode

            # ── Attach system principal provenance to response ──
            principal_meta = {
                'principal_id': 'SYSTEM:GRAPHOPS',
                'author_class': 'system',
                'auth_level': 'bounded',
            }

            return jsonify({
                'status': 'ok',
                'response': response_text,
                'model': model,
                'principal': principal_meta,
            })
        except Exception as e:
            logger.error(f'[tak-gpt] chat failed: {e}')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/tak-gpt/status', methods=['GET'])
    def api_takgpt_status():
        """Check TAK-GPT bot availability."""
        try:
            from gemma_client import GemmaClient
            client = GemmaClient()
            available = client.is_available()
            models = client.list_models() if available else []
            return jsonify({
                'status': 'ok',
                'available': available,
                'models': models,
            })
        except Exception as e:
            return jsonify({'status': 'error', 'available': False, 'message': str(e)})

    # ========================================================================
    # SYSTEM PRINCIPAL STATUS
    # ========================================================================

    @app.route('/api/principal/status', methods=['GET'])
    def api_principal_status():
        """Return system principal availability for the UI auth gate.

        The system principal (SYSTEM:GRAPHOPS) is considered active when:
          1. The hypergraph engine is present
          2. The engine contains evidence (nodes > 0)
          3. The Ollama/LLM backend is reachable

        This endpoint lets the UI unlock inference under system authority
        without requiring an operator login.
        """
        try:
            from principals import GRAPHOPS, all_principals
        except ImportError:
            return jsonify({'status': 'ok', 'active': False, 'reason': 'principals module not available'})

        eng = globals().get('hypergraph_engine')
        has_engine = eng is not None
        has_evidence = False
        if has_engine and hasattr(eng, 'nodes') and eng.nodes:
            has_evidence = len(eng.nodes) > 0

        # Check LLM availability (lightweight)
        llm_available = False
        try:
            import urllib.request
            req = urllib.request.Request(f'{_DEFAULT_OLLAMA_URL}/api/tags', method='GET')
            resp = urllib.request.urlopen(req, timeout=2)
            llm_available = resp.status == 200
        except Exception:
            pass

        active = has_engine and has_evidence and llm_available

        return jsonify({
            'status': 'ok',
            'active': active,
            'principal': {
                'id': GRAPHOPS.principal_id,
                'display_name': GRAPHOPS.display_name,
                'author_class': GRAPHOPS.author_class,
                'auth_level': GRAPHOPS.auth_level,
                'capabilities': sorted(GRAPHOPS.capabilities),
            },
            'conditions': {
                'engine_present': has_engine,
                'evidence_exists': has_evidence,
                'llm_available': llm_available,
            },
        })

    # ========================================================================
    # API ROUTES - MCP Context Snapshot
    # ========================================================================

    @app.route('/api/mcp/snapshot', methods=['GET'])
    def api_mcp_snapshot():
        """Return the current MCP v1.0 context envelope as JSON.

        Query params:
          window_minutes: int (default: 15)
          session_id: str (optional)
          compact: bool — if "true", return compact text form instead of JSON
        """
        try:
            from mcp_context import MCPBuilder
            import mcp_server as mcp_mod

            eng = globals().get('hypergraph_engine')
            if eng is None:
                return jsonify({'status': 'error', 'message': 'hypergraph_engine not available'}), 500

            # Prevent re-entrant MCP tool calls while assembling the snapshot
            mcp_mod.start_context_build()
            try:
                builder = MCPBuilder(eng)
                window = int(request.args.get('window_minutes', 15))
                session_id = request.args.get('session_id')

                if request.args.get('compact', '').lower() == 'true':
                    text = builder.build_compact(
                        session_id=session_id,
                        window_minutes=window,
                    )
                    return jsonify({'status': 'ok', 'format': 'compact', 'mcp': text})
                else:
                    envelope = builder.build(
                        session_id=session_id,
                        window_minutes=window,
                    )
                    return jsonify({'status': 'ok', 'format': 'full', 'mcp': envelope})
            finally:
                mcp_mod.end_context_build()

        except Exception as e:
            logger.error(f'[mcp] snapshot failed: {e}')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/inference/history', methods=['GET'])
    def api_inference_history():
        """Return inference run history (time-series of runs).

        Query params:
          last: int — return only the last N runs (default: all, max 100)
        """
        try:
            from tak_ml_gemma_runner import get_inference_history, get_last_inference_run

            last_n = request.args.get('last', type=int)
            history = get_inference_history()

            if last_n and last_n > 0:
                history = history[-last_n:]

            # Also include lifting from engine
            eng = globals().get('hypergraph_engine')
            lifting = {}
            if eng and hasattr(eng, '_last_inference_run'):
                lifting = (eng._last_inference_run or {}).get('lifting', {})

            return jsonify({
                'status': 'ok',
                'run_count': len(history),
                'runs': history,
                'last_lifting': lifting,
            })
        except Exception as e:
            logger.error(f'[inference] history failed: {e}')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/inference/drift', methods=['GET'])
    def api_inference_drift():
        """Return belief drift between the last two inference runs.

        Compares edge kinds, tier counts, and overall trajectory.
        """
        try:
            from tak_ml_gemma_runner import compute_belief_drift
            drift = compute_belief_drift()
            return jsonify({'status': 'ok', 'drift': drift})
        except Exception as e:
            logger.error(f'[inference] drift failed: {e}')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/inference/runtime-metrics', methods=['GET'])
    def api_inference_runtime_metrics():
        """Return tak-ml runtime counters, rates, and rejected-kind recurrence."""
        try:
            from tak_ml_gemma_runner import get_takml_runtime_metrics_snapshot
            window_seconds = request.args.get('window_seconds', default=900, type=int)
            window_seconds = max(60, min(window_seconds, 86400))
            metrics = get_takml_runtime_metrics_snapshot(window_seconds=window_seconds)
            return jsonify({'status': 'ok', 'metrics': metrics})
        except Exception as e:
            logger.error(f'[inference] runtime metrics failed: {e}')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/shadow/summary', methods=['GET'])
    def api_shadow_summary():
        """Return a summary of the shadow graph (pre-reality rejected edges)."""
        try:
            from shadow_graph import ShadowGraph
            return jsonify({'status': 'ok', 'shadow': ShadowGraph.get_instance().summary()})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/shadow/edges', methods=['GET'])
    def api_shadow_edges():
        """Return pending shadow edges (up to 200, sorted by confidence desc)."""
        try:
            from shadow_graph import ShadowGraph
            limit = min(int(request.args.get('limit', 200)), 500)
            edges = ShadowGraph.get_instance().get_pending(limit=limit)
            return jsonify({'status': 'ok', 'edges': edges, 'count': len(edges)})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/shadow/observe', methods=['POST'])
    def api_shadow_observe():
        """Bump confidence + evidence on a shadow edge (external corroboration).

        Body: { "edge_id": "abc123", "confidence_delta": 0.05, "evidence_delta": 0.1 }
        """
        try:
            from shadow_graph import ShadowGraph
            body = request.get_json(silent=True) or {}
            edge_id = body.get('edge_id', '')
            conf_delta = float(body.get('confidence_delta', 0.05))
            ev_delta   = float(body.get('evidence_delta', 0.1))
            result = ShadowGraph.get_instance().observe(edge_id, conf_delta, ev_delta)
            if result is None:
                return jsonify({'status': 'not_found', 'edge_id': edge_id}), 404
            return jsonify({'status': 'ok', 'edge': result})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/stream/speculative')
    def stream_speculative():
        """SSE stream of shadow graph deltas (created / updated / promoted / decayed).

        Each SSE event carries:
          id:   monotonic sequence number
          data: JSON-encoded ShadowEdge dict with '_event', '_ts', 'seq' fields

        Reconnect protocol (EventSource automatic):
          - Browser sends 'Last-Event-ID' header on reconnect
          - Server detects the gap (current_seq - last_seen_seq) and sends a
            '_event: resync' event so the client knows to re-bootstrap from
            GET /api/shadow/edges rather than trusting its stale state.

        dropIfSlow:
          - Each subscriber has a bounded queue (500 events).
          - If a slow client falls behind, events are dropped and drop_count
            is included in heartbeats so the browser can self-triage.
        """
        try:
            from shadow_graph import ShadowGraph
            sg = ShadowGraph.get_instance()

            # Detect reconnecting client — Last-Event-ID is the last seq they saw
            try:
                last_seen_seq = int(request.headers.get('Last-Event-ID', -1))
            except (TypeError, ValueError):
                last_seen_seq = -1

            snapshot = sg.get_pending(limit=200)

            def generate():
                import json as _json
                yield "retry: 3000\n\n"   # tell browser: reconnect after 3s on drop

                # If reconnecting client missed events, signal a resync first
                current = sg.current_seq
                if last_seen_seq >= 0 and current > last_seen_seq + 1:
                    gap = current - last_seen_seq
                    resync = _json.dumps({
                        "_event":   "resync",
                        "gap":      gap,
                        "from_seq": last_seen_seq,
                        "to_seq":   current,
                        "message":  f"Missed {gap} events — re-bootstrap from /api/shadow/edges",
                    })
                    yield f"id: {current}\ndata: {resync}\n\n"

                # Bootstrap with current speculative state
                for edge in snapshot:
                    edge['_event'] = 'preexisting'
                    edge.setdefault('seq', 0)
                    yield f"id: {edge['seq']}\ndata: {_json.dumps(edge)}\n\n"

                sub = sg.subscribe_sse(maxsize=500)
                try:
                    while True:
                        try:
                            delta = sub.q.get(timeout=25)
                            seq   = delta.get('seq', 0)
                            yield f"id: {seq}\ndata: {_json.dumps(delta)}\n\n"
                        except Exception:
                            # Heartbeat — include drop_count so browser can detect lag
                            hb = _json.dumps({
                                "_event":     "heartbeat",
                                "seq":        sg.current_seq,
                                "drop_count": sub.drop_count,
                            })
                            yield f"data: {hb}\n\n"
                finally:
                    sg.unsubscribe_sse(sub)

            return Response(
                generate(),
                mimetype='text/event-stream',
                headers={
                    'Cache-Control':    'no-cache',
                    'X-Accel-Buffering': 'no',
                    'Connection':       'keep-alive',
                }
            )
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/semantic/pca-coords', methods=['GET'])
    def api_semantic_pca():
        """
        Return 2D PCA projection of all FAISS embeddings for Deck.gl rendering.

        Response: { status, nodes: [{entity_id, description, pca_x, pca_y, lon, lat}] }

        Optional query params:
          lat_center, lon_center  — override geographic cluster centre
        """
        try:
            from semantic_shadow import SemanticShadow
            ss = SemanticShadow.get_instance()

            # Build entity_positions from recon system for geographic anchoring
            entity_positions = {}
            try:
                for eid, ent in recon_system.entities.items():
                    loc = ent.get('location') or {}
                    if loc.get('lat') and loc.get('lon'):
                        entity_positions[eid] = {'lat': loc['lat'], 'lon': loc['lon']}
            except Exception:
                pass

            nodes = ss.get_pca_coords(entity_positions)
            return jsonify({'status': 'ok', 'nodes': nodes, 'count': len(nodes)})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

    # ── Intelligence Flywheel ────────────────────────────────────────────────
    # Ported from WorldMonitor:
    #   temporal-baseline.ts → TemporalBaseline
    #   hotspot-escalation.ts → HotspotEscalation
    #   signal-aggregator.ts  → NetworkSignalAggregator

    class TemporalBaseline:
        """Rolling Z-score anomaly detector (WorldMonitor temporal-baseline.ts).
        Tracks per-node observation values in a 168-sample (7-day) window;
        returns a 0-1 deviation score when degree spikes above baseline."""
        _WIN  = 168  # 7 × 24 hourly slots
        _MIN  = 6    # minimum samples before scoring

        def __init__(self):
            self._h: Dict[str, deque] = {}
            self._lk = threading.Lock()

        def record(self, node_id: str, value: float) -> None:
            with self._lk:
                if node_id not in self._h:
                    self._h[node_id] = deque(maxlen=self._WIN)
                self._h[node_id].append(value)

        def z_score(self, node_id: str, current: float) -> float:
            with self._lk:
                hist = list(self._h.get(node_id, []))
            if len(hist) < self._MIN:
                return 0.0
            mean   = sum(hist) / len(hist)
            stddev = (sum((x - mean) ** 2 for x in hist) / len(hist)) ** 0.5
            if stddev < 1e-9:
                # Perfectly stable baseline → any deviation is maximally anomalous
                return 4.0 if abs(current - mean) > 1e-9 else 0.0
            return (current - mean) / stddev

        def baseline_score(self, node_id: str, current: float) -> float:
            """Normalised 0-1; z=4 → 1.0. Clipped so negative deviations score 0."""
            return min(1.0, max(0.0, self.z_score(node_id, current) / 4.0))

    class HotspotEscalation:
        """Dynamic escalation scoring (WorldMonitor hotspot-escalation.ts).
        Blends 4 SCYTHE-adapted components (35/25/25/15) and runs linear
        regression over a 48-point 24-hour history to detect trend."""
        _W        = {'flow': 0.35, 'c2': 0.25, 'conv': 0.25, 'asn': 0.15}
        _MAX_HIST = 48
        _WIN_S    = 86400  # 24 h

        def __init__(self):
            self._scores: Dict[str, dict] = {}
            self._lk = threading.Lock()

        def update(self, node_id: str, flow_norm: float, c2_norm: float,
                   conv_norm: float, asn_norm: float,
                   static_base: float = 0.3) -> dict:
            """All *_norm inputs 0-1. Returns {escalation_score, trend, components}."""
            comp = {
                'flow_activity':   min(100.0, flow_norm * 100),
                'c2_contribution': min(100.0, c2_norm  * 100),
                'geo_convergence': min(100.0, conv_norm * 100),
                'asn_diversity':   min(100.0, asn_norm  * 100),
            }
            raw      = (comp['flow_activity']   * self._W['flow'] +
                        comp['c2_contribution'] * self._W['c2']   +
                        comp['geo_convergence'] * self._W['conv']  +
                        comp['asn_diversity']   * self._W['asn'])
            dynamic  = 1.0 + (raw / 100.0) * 4.0   # maps 0-100 → 1-5
            combined = static_base * 0.3 + dynamic * 0.7
            with self._lk:
                rec = self._scores.setdefault(node_id, {'history': []})
                now = time.time()
                rec['history'] = [h for h in rec['history'] if now - h['t'] < self._WIN_S]
                if len(rec['history']) >= self._MAX_HIST:
                    rec['history'] = rec['history'][-self._MAX_HIST:]
                rec['history'].append({'t': now, 'score': combined})
                trend = self._trend(rec['history'])
            return {'escalation_score': round(combined, 3), 'trend': trend, 'components': comp}

        @staticmethod
        def _trend(history: list) -> str:
            n = len(history)
            if n < 3:
                return 'stable'
            sx = sy = sxy = sx2 = 0.0
            for i, h in enumerate(history):
                sx += i; sy += h['score']
                sxy += i * h['score']; sx2 += i * i
            d = n * sx2 - sx * sx
            if abs(d) < 1e-9:
                return 'stable'
            slope = (n * sxy - sx * sy) / d
            return 'escalating' if slope > 0.1 else ('de-escalating' if slope < -0.1 else 'stable')

    class NetworkSignalAggregator:
        """Multi-source convergence scoring (WorldMonitor signal-aggregator.ts).
        Collects PCAP/C2/CYMRU/RF/shadow signals per node in a 24-h window;
        convergence score spikes when multiple independent signal types agree."""
        _WIN_S   = 86400   # 24 h buffer
        _DEDUP_S = 1800    # 30 min dedup per type (bypassed for high-severity)

        def __init__(self):
            self._sigs: Dict[str, list] = {}
            self._lk = threading.Lock()

        def ingest(self, node_id: str, signal_type: str,
                   severity: str = 'low', title: str = '') -> None:
            with self._lk:
                sigs = self._sigs.setdefault(node_id, [])
                now  = time.time()
                sigs[:] = [s for s in sigs if now - s['t'] < self._WIN_S]
                if severity != 'high':
                    recent = {s['type'] for s in sigs if now - s['t'] < self._DEDUP_S}
                    if signal_type in recent:
                        return
                sigs.append({'type': signal_type, 'severity': severity,
                              'title': title, 't': now})

        def convergence_score(self, node_id: str) -> float:
            """0-100. High when many independent signal types agree on the same node."""
            with self._lk:
                sigs = self._sigs.get(node_id, [])
                if not sigs:
                    return 0.0
                types   = {s['type'] for s in sigs}
                high_ct = sum(1 for s in sigs if s['severity'] == 'high')
            return min(100.0, len(types) * 20.0 + min(30.0, len(sigs) * 5.0) + high_ct * 10.0)

        def convergence_zones(self) -> list:
            """Return nodes with score ≥60 and ≥2 signal types."""
            with self._lk:
                ids = list(self._sigs.keys())
            zones = []
            for nid in ids:
                score = self.convergence_score(nid)
                with self._lk:
                    types = list({s['type'] for s in self._sigs.get(nid, [])})
                if len(types) >= 2 and score >= 60:
                    zones.append({'node_id': nid, 'score': score, 'types': types})
            return sorted(zones, key=lambda z: z['score'], reverse=True)

    # Singletons shared across all gravity API requests
    _TEMPORAL_BASELINE  = TemporalBaseline()
    _HOTSPOT_ESCALATION = HotspotEscalation()
    _SIGNAL_AGGREGATOR  = NetworkSignalAggregator()

    # Gravity nodes result cache — keyed by hypergraph sequence number so scoring
    # singletons only mutate when the graph actually changes (not on every poll).
    _gravity_nodes_cache: dict = {'seq': -1, 'result': None, 'at': 0.0}
    _GRAVITY_NODES_CACHE_TTL = 4.0  # seconds: max staleness even if seq unchanged

    @app.route('/api/gravity/nodes', methods=['GET'])
    def api_gravity_nodes():
        """Return all graph nodes with computed threat mass for the gravity map.

        Mass formula (Intelligence Flywheel):
          0.25 * log(degree + 1)
          0.20 * log(flow_count + 1)
          0.15 * escalation_norm    (HotspotEscalation — weighted 35/25/25/15 blend)
          0.15 * baseline_dev       (TemporalBaseline  — z-score deviation, 0-1)
          0.15 * convergence_norm   (NetworkSignalAggregator — multi-source score)
          0.10 * shadow_norm
          +1.5  C2 anchor bonus
        """
        import math
        try:
            hg = _get_engine()
            if hg is None:
                return jsonify({'status': 'error', 'message': 'engine not ready'}), 503

            # ── Sequence-based result cache ───────────────────────────────────
            # Score singletons (TemporalBaseline, SignalAggregator, Hotspot) should
            # only mutate when the underlying graph changes.  Return cached result
            # if hg.sequence hasn't advanced and result is < TTL seconds old.
            hg_seq = getattr(hg, 'sequence', -1)
            _cache = _gravity_nodes_cache
            now = time.time()
            if (
                _cache['result'] is not None
                and _cache['seq'] == hg_seq
                and now - _cache['at'] < _GRAVITY_NODES_CACHE_TTL
            ):
                return jsonify(_cache['result'])

            from shadow_graph import ShadowGraph
            shadow = ShadowGraph.get_instance()
            shadow_summary = shadow.summary()
            # Count shadow pushes per context_node_id for mass bonus
            shadow_by_node: dict = {}
            for se in shadow.get_pending(limit=2000):
                cid = se.get('context_node_id', '')
                if cid:
                    shadow_by_node[cid] = shadow_by_node.get(cid, 0) + 1

            nodes_out = []
            nodes_dict = hg.nodes if isinstance(hg.nodes, dict) else {}
            for nid, node in nodes_dict.items():
                nd = node if isinstance(node, dict) else (node.to_dict() if hasattr(node, 'to_dict') else {})
                kind = nd.get('kind', 'unknown')
                meta = nd.get('metadata', {}) or {}

                # Degree: count edges touching this node
                edges_for = []
                try:
                    edges_for = list(hg.edges_for_node(nid)) if hasattr(hg, 'edges_for_node') else []
                except Exception:
                    pass
                degree = len(edges_for)

                # Flow count: if it's a flow/host, look at flow-type edges
                flow_count = sum(
                    1 for e in edges_for
                    if (e.get('kind','') if isinstance(e, dict) else getattr(e,'kind','')).startswith('INFERRED_FLOW')
                )

                # Shadow promotion bonus
                shadow_count = shadow_by_node.get(nid, 0)

                # C2 / threat intel enrichment (from pcap_registry Feodo+C2Intel check)
                threat_intel = meta.get('threat_intel') or {}
                is_c2 = bool(threat_intel.get('is_c2'))
                malware_family = threat_intel.get('malware_family') or None
                c2_sources = threat_intel.get('sources') or []

                # CYMRU ASN enrichment labels
                asn_label = nd.get('labels', {}).get('asn', '') if isinstance(nd.get('labels'), dict) else ''
                rir_label  = nd.get('labels', {}).get('rir', '') if isinstance(nd.get('labels'), dict) else ''

                # ── Intelligence Flywheel ──────────────────────────────────
                # 1. Temporal baseline: record degree snapshot → z-score deviation
                _TEMPORAL_BASELINE.record(nid, float(degree))
                baseline_dev = _TEMPORAL_BASELINE.baseline_score(nid, float(degree))

                # 2. Signal aggregator: ingest this node's observable signals
                if flow_count > 0:
                    sev = 'high' if flow_count >= 10 else ('medium' if flow_count >= 3 else 'low')
                    _SIGNAL_AGGREGATOR.ingest(nid, 'pcap_flow', sev)
                if is_c2:
                    _SIGNAL_AGGREGATOR.ingest(nid, 'c2_intel', 'high', malware_family or 'C2')
                if asn_label:
                    _SIGNAL_AGGREGATOR.ingest(nid, 'cymru_asn', 'low')
                if shadow_count > 0:
                    _SIGNAL_AGGREGATOR.ingest(nid, 'shadow_promo',
                                              'high' if shadow_count >= 3 else 'medium')
                if baseline_dev > 0.5:
                    _SIGNAL_AGGREGATOR.ingest(nid, 'temporal',
                                              'high' if baseline_dev > 0.75 else 'medium')
                conv_raw  = _SIGNAL_AGGREGATOR.convergence_score(nid)
                conv_norm = conv_raw / 100.0   # 0-1

                # 3. Hotspot escalation: weighted blend → combined score + trend
                try:
                    _raw_anom = meta.get('anomaly_score') or meta.get('anomaly') or meta.get('confidence') or 0.0
                    anomaly_score = max(0.0, min(1.0, float(_raw_anom)))
                except (ValueError, TypeError):
                    anomaly_score = 0.0

                flow_norm   = min(1.0, flow_count / 20.0)
                c2_norm     = 1.0 if is_c2 else min(1.0, anomaly_score * 1.5)
                asn_norm    = min(1.0, float(bool(asn_label)) * 0.5 + baseline_dev * 0.5)
                static_base = min(1.0, degree / 20.0)
                esc = _HOTSPOT_ESCALATION.update(
                    nid,
                    flow_norm=flow_norm, c2_norm=c2_norm,
                    conv_norm=conv_norm, asn_norm=asn_norm,
                    static_base=static_base,
                )
                escalation_norm = max(0.0, min(1.0, (esc['escalation_score'] - 0.7) / 3.1))  # 0.7-3.8 → 0-1
                trend = esc['trend']

                # Compute mass — flywheel replaces raw persistence + anomaly_score
                mass = (
                    0.25 * math.log(degree + 1)         +
                    0.20 * math.log(flow_count + 1)      +
                    0.15 * escalation_norm               +
                    0.15 * baseline_dev                  +
                    0.15 * conv_norm                     +
                    0.10 * min(1.0, shadow_count / 5.0) +
                    (1.5 if is_c2 else 0.0)
                )
                mass = max(0.1, round(mass, 4))

                # Threat level: 0=benign 1=uncertain 2=threat
                if is_c2 or escalation_norm > 0.6 or shadow_count >= 3:
                    threat_level = 2
                elif anomaly_score > 0.35 or shadow_count >= 1 or conv_norm > 0.3:
                    threat_level = 1
                else:
                    threat_level = 0

                nodes_out.append({
                    'id': nid,
                    'kind': kind,
                    'label': nd.get('label', nid[:24]),
                    'mass': mass,
                    'degree': degree,
                    'flow_count': flow_count,
                    'anomaly_score': anomaly_score,
                    'shadow_count': shadow_count,
                    'threat_level': threat_level,
                    'synthetic': bool(meta.get('_synthetic')),
                    'is_c2': is_c2,
                    'malware_family': malware_family,
                    'c2_sources': c2_sources,
                    'asn': asn_label,
                    'rir': rir_label,
                    'escalation_score': esc['escalation_score'],
                    'trend': trend,
                    'convergence_score': round(conv_raw, 1),
                    'baseline_dev': round(baseline_dev, 3),
                })

            # Sort by mass descending for UI efficiency
            nodes_out.sort(key=lambda n: n['mass'], reverse=True)

            _result = {
                'status': 'ok',
                'nodes': nodes_out,
                'count': len(nodes_out),
                'shadow_summary': shadow_summary,
            }
            # Populate cache so subsequent polls within TTL skip scoring mutations
            _gravity_nodes_cache['seq']    = hg_seq
            _gravity_nodes_cache['result'] = _result
            _gravity_nodes_cache['at']     = time.time()
            return jsonify(_result)
        except Exception as e:
            logger.exception('[gravity] api_gravity_nodes failed')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/gravity/edges', methods=['GET'])
    def api_gravity_edges():
        """Return edges for the gravity map simulation (sampled to max 2000)."""
        try:
            hg = _get_engine()
            if hg is None:
                return jsonify({'status': 'error', 'message': 'engine not ready'}), 503

            edges_dict = hg.edges if isinstance(hg.edges, dict) else {}
            limit = min(int(request.args.get('limit', 2000)), 5000)
            # Indexed edge format: nodes_index + [[si,di,kind,conf],...] cuts ~60% payload
            nodes_index = []
            node_to_idx = {}

            def _nidx(nid):
                if nid not in node_to_idx:
                    node_to_idx[nid] = len(nodes_index)
                    nodes_index.append(nid)
                return node_to_idx[nid]

            edges_out = []
            for eid, edge in list(edges_dict.items())[:limit]:
                ed = edge if isinstance(edge, dict) else (edge.to_dict() if hasattr(edge, 'to_dict') else {})
                nodes_field = ed.get('nodes', [])
                if isinstance(nodes_field, list) and len(nodes_field) >= 2:
                    src = nodes_field[0] if isinstance(nodes_field[0], str) else (nodes_field[0].get('id') if isinstance(nodes_field[0], dict) else str(nodes_field[0]))
                    dst = nodes_field[-1] if isinstance(nodes_field[-1], str) else (nodes_field[-1].get('id') if isinstance(nodes_field[-1], dict) else str(nodes_field[-1]))
                    if not src or not dst:
                        continue
                    _conf = ed.get('confidence') \
                        or (ed.get('metadata') or {}).get('confidence') \
                        or ed.get('weight')
                    try:
                        conf_f = float(_conf) if _conf is not None else 0.5
                    except (ValueError, TypeError):
                        conf_f = 0.5
                    edges_out.append([_nidx(src), _nidx(dst), ed.get('kind', ''), conf_f])
            return jsonify({'status': 'ok', 'nodes_index': nodes_index, 'edges': edges_out, 'count': len(edges_out)})
        except Exception as e:
            logger.exception('[gravity] api_gravity_edges failed')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    # ── Gravity / Cluster export helpers ──────────────────────────────────

    def _gravity_snapshot_readonly():
        """Pure read-only snapshot of the gravity graph with deterministic positions.

        Does NOT call _TEMPORAL_BASELINE, _SIGNAL_AGGREGATOR, or _HOTSPOT_ESCALATION.
        Uses simplified mass (degree + anomaly from metadata only) suitable for
        offline export artifacts.  Applies Fibonacci sphere positions for layout.

        Returns:
          {nodes: [{id, kind, label, mass, degree, threat_level, intensity, x, y, z},...],
           nodes_index: [id,...], edges: [[si,di,kind,conf],...],
           edge_metadata: [{... aligned visual metadata ...}, ...],
           count: N, edge_count: M}
        """
        import math as _math

        hg = _get_engine()
        if hg is None:
            return None

        nodes_dict = hg.nodes if isinstance(hg.nodes, dict) else {}
        edges_dict = hg.edges if isinstance(hg.edges, dict) else {}

        # Build degree map from edges
        degree_map: dict = {}
        for _eid, edge in edges_dict.items():
            ed = edge if isinstance(edge, dict) else (edge.to_dict() if hasattr(edge, 'to_dict') else {})
            nf = ed.get('nodes', [])
            if isinstance(nf, list) and len(nf) >= 2:
                for nid in (nf[0], nf[-1]):
                    key = nid if isinstance(nid, str) else (nid.get('id') if isinstance(nid, dict) else str(nid))
                    if key:
                        degree_map[key] = degree_map.get(key, 0) + 1

        nodes_out = []
        for nid, node in nodes_dict.items():
            nd   = node if isinstance(node, dict) else (node.to_dict() if hasattr(node, 'to_dict') else {})
            meta = nd.get('metadata', {}) or {}
            degree = degree_map.get(nid, 0)

            try:
                raw_anom = meta.get('anomaly_score') or meta.get('anomaly') or meta.get('confidence') or 0.0
                anomaly  = max(0.0, min(1.0, float(raw_anom)))
            except (ValueError, TypeError):
                anomaly = 0.0

            # Simplified mass: no singleton mutations
            mass = round(max(0.1, 0.4 * _math.log(degree + 1) + 0.6 * anomaly), 4)

            threat_level = 2 if anomaly > 0.7 else (1 if anomaly > 0.35 else 0)

            nodes_out.append({
                'id':           nid,
                'kind':         nd.get('kind', 'unknown'),
                'label':        nd.get('label', nid[:24]),
                'mass':         mass,
                'degree':       degree,
                'anomaly_score': anomaly,
                'threat_level': threat_level,
                'intensity':    min(1.0, mass / 2.0),
            })

        nodes_out.sort(key=lambda n: n['mass'], reverse=True)

        # Apply Fibonacci sphere positions to sorted nodes
        n_total = len(nodes_out)
        phi_g   = _math.pi * (1 + _math.sqrt(5))   # golden angle
        for i, nd in enumerate(nodes_out):
            if n_total > 1:
                polar = _math.acos(1 - 2 * (i + 0.5) / n_total)
                az    = phi_g * i
            else:
                polar = 0.0
                az    = 0.0
            r     = 80 + min(1.0, nd['mass'] / 2.0) * 60
            nd['x'] = round(r * _math.sin(polar) * _math.cos(az), 4)
            nd['y'] = round(r * _math.sin(polar) * _math.sin(az), 4)
            nd['z'] = round(r * _math.cos(polar),                  4)

        # Build indexed edge list
        nodes_index: list = [nd['id'] for nd in nodes_out]
        node_to_idx       = {nid: i for i, nid in enumerate(nodes_index)}

        def _edge_float(value):
            try:
                return float(value) if value is not None else None
            except (ValueError, TypeError):
                return None

        edges_out = []
        edge_metadata_out = []
        for _eid, edge in list(edges_dict.items())[:3000]:
            ed = edge if isinstance(edge, dict) else (edge.to_dict() if hasattr(edge, 'to_dict') else {})
            nf = ed.get('nodes', [])
            if not (isinstance(nf, list) and len(nf) >= 2):
                continue
            src_raw = nf[0]  if isinstance(nf[0],  str) else (nf[0].get('id')  if isinstance(nf[0],  dict) else str(nf[0]))
            dst_raw = nf[-1] if isinstance(nf[-1], str) else (nf[-1].get('id') if isinstance(nf[-1], dict) else str(nf[-1]))
            if not src_raw or not dst_raw:
                continue
            si = node_to_idx.get(src_raw)
            di = node_to_idx.get(dst_raw)
            if si is None or di is None:
                continue
            _conf = ed.get('confidence') or (ed.get('metadata') or {}).get('confidence') or ed.get('weight')
            try:
                conf_f = float(_conf) if _conf is not None else 0.5
            except (ValueError, TypeError):
                conf_f = 0.5
            edges_out.append([si, di, ed.get('kind', ''), conf_f])
            meta = ed.get('metadata', {}) or {}
            labels = ed.get('labels', {}) or {}
            render_style = meta.get('render_style') or ed.get('render_style') or {}
            field_view = meta.get('field_view') or {}
            supporting = meta.get('supporting_evidence') or {}
            obs_class = (
                meta.get('obs_class')
                or labels.get('obs_class')
                or ('forecast' if meta.get('forecast') else ('observed' if meta.get('observed') else 'inferred'))
            )
            entropy = _edge_float(supporting.get('entropy') if supporting.get('entropy') is not None else meta.get('entropy'))
            divergence_risk = _edge_float(
                supporting.get('divergence_risk')
                if supporting.get('divergence_risk') is not None
                else meta.get('divergence_risk')
            )
            identity_pressure = _edge_float(
                supporting.get('identity_pressure')
                if supporting.get('identity_pressure') is not None
                else meta.get('identity_pressure')
            )
            periodicity_s = _edge_float(
                supporting.get('periodicity_s')
                if supporting.get('periodicity_s') is not None
                else meta.get('periodicity_s')
            )
            temporal_cohesion = _edge_float(
                supporting.get('temporal_cohesion')
                if supporting.get('temporal_cohesion') is not None
                else meta.get('temporal_cohesion')
            )
            resilience_score = _edge_float(
                supporting.get('resilience_score')
                if supporting.get('resilience_score') is not None
                else meta.get('resilience_score')
            )
            style_hints = {
                'ghost': bool(render_style.get('ghost')) or str(obs_class).lower() != 'observed',
                'flicker': bool(render_style.get('flicker')) or (
                    divergence_risk is not None and divergence_risk >= 0.62
                ) or (
                    entropy is not None and entropy >= 0.58
                ) or meta.get('dissonance_zone') == 'COGNITIVE_CONFLICT_ZONE',
                'pulse': render_style.get('pulse') or (
                    'beacon' if (
                        periodicity_s is not None
                        and periodicity_s <= 15.0
                        and (entropy is None or entropy <= 0.45)
                        and (temporal_cohesion is None or temporal_cohesion >= 0.45)
                    ) else ''
                ),
                'identity_color_lock': bool(render_style.get('color_lock'))
                or bool(field_view.get('identity_color_lock'))
                or bool(identity_pressure is not None and identity_pressure >= 0.72)
                or 'IDENTIT' in str(ed.get('kind', '')).upper(),
            }
            edge_metadata_out.append({
                'id': _eid,
                'kind': ed.get('kind', ''),
                'confidence': conf_f,
                'obs_class': obs_class,
                'temporal_phase': meta.get('temporal_phase') or supporting.get('temporal_phase'),
                'dissonance_zone': meta.get('dissonance_zone') or supporting.get('dissonance_zone'),
                'entropy': entropy,
                'divergence_risk': divergence_risk,
                'identity_pressure': identity_pressure,
                'periodicity_s': periodicity_s,
                'temporal_cohesion': temporal_cohesion,
                'resilience_score': resilience_score,
                'top_intent_label': meta.get('top_intent_label') or supporting.get('top_intent_label'),
                'render_style': render_style,
                'field_view': field_view,
                'style_hints': style_hints,
            })

        return {
            'nodes':       nodes_out,
            'nodes_index': nodes_index,
            'edges':       edges_out,
            'edge_metadata': edge_metadata_out,
            'count':       len(nodes_out),
            'edge_count':  len(edges_out),
        }

    def _get_cluster_hosts(cluster) -> list:
        """Return up to 200 member-host dicts for a cluster, sorted by threat descending.

        Filters engine nodes whose geo position (position[0]=lat, position[1]=lon) falls
        within 120% of cluster.radius_m() from the centroid using haversine distance.
        Enriches each host with IP, hostname, kind, ASN, ports, anomaly score, last seen.
        """
        import math as _m

        hg = _get_engine()
        if hg is None:
            return []

        c_lat, c_lon = cluster.centroid_lat, cluster.centroid_lon
        radius_m     = cluster.radius_m() * 1.2  # slight geo padding

        def _haversine_m(lat1, lon1, lat2, lon2):
            R = 6_371_000.0
            dlat = _m.radians(lat2 - lat1)
            dlon = _m.radians(lon2 - lon1)
            a = (_m.sin(dlat / 2) ** 2 +
                 _m.cos(_m.radians(lat1)) * _m.cos(_m.radians(lat2)) * _m.sin(dlon / 2) ** 2)
            return R * 2 * _m.asin(_m.sqrt(a))

        nodes_dict = hg.nodes if isinstance(hg.nodes, dict) else {}
        hosts = []
        for nid, node in nodes_dict.items():
            nd   = node if isinstance(node, dict) else (node.to_dict() if hasattr(node, 'to_dict') else {})
            pos  = nd.get('position') or []
            if not (isinstance(pos, (list, tuple)) and len(pos) >= 2):
                continue
            try:
                n_lat, n_lon = float(pos[0]), float(pos[1])
            except (TypeError, ValueError):
                continue
            if _haversine_m(c_lat, c_lon, n_lat, n_lon) > radius_m:
                continue

            labels = nd.get('labels') or {}
            meta   = nd.get('metadata') or {}
            try:
                anomaly = max(0.0, min(1.0, float(
                    meta.get('anomaly_score') or meta.get('anomaly') or
                    labels.get('confidence') or 0.0)))
            except (TypeError, ValueError):
                anomaly = 0.0
            try:
                ts = float(nd.get('updated_at') or meta.get('last_seen') or 0.0)
            except (TypeError, ValueError):
                ts = 0.0

            # Collect port list from metadata
            ports = meta.get('ports') or meta.get('open_ports') or labels.get('ports') or []
            if isinstance(ports, str):
                ports = [p.strip() for p in ports.split(',') if p.strip()]
            elif not isinstance(ports, list):
                ports = []

            hosts.append({
                'id':          nid,
                'ip':          (labels.get('ip') or meta.get('ip') or
                                labels.get('ip_addr') or meta.get('ip_addr') or ''),
                'hostname':    (labels.get('hostname') or meta.get('hostname') or
                                nd.get('label') or nid[:40]),
                'kind':        nd.get('kind', 'unknown'),
                'asn':         (labels.get('asn') or meta.get('asn') or ''),
                'asn_org':     (labels.get('asn_org') or meta.get('asn_org') or ''),
                'country':     (labels.get('country') or meta.get('country') or ''),
                'ports':       ports[:20],
                'anomaly':     round(anomaly, 3),
                'lat':         round(n_lat, 5),
                'lon':         round(n_lon, 5),
                'last_seen':   ts,
                'threat_level': ('HIGH' if anomaly > 0.7 else ('MEDIUM' if anomaly > 0.35 else 'LOW')),
                'frequency':   nd.get('frequency'),
                'rssi':        meta.get('rssi'),
                'protocol':    (labels.get('protocol') or meta.get('protocol') or ''),
                'source':      (labels.get('source') or meta.get('source') or ''),
            })

        hosts.sort(key=lambda h: h['anomaly'], reverse=True)
        return hosts[:200]

    def _build_export_bundle(data: dict, title: str = 'Hypergraph Export') -> str:
        """Generate a self-contained HTML export bundle embedding graph data and the viewer component.

        The viewer component source is read from hypergraph-viewer.js (same directory as this server).
        Falls back to a minimal inline loader if the file is not found.

        Safety: JSON data is embedded in <script type="application/json"> with </script> escaped.
        CDN uses three@0.158.0 to match the in-app import-map version.
        """
        import json as _json
        import os as _os

        comp_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'hypergraph-viewer.js')
        try:
            with open(comp_path, 'r', encoding='utf-8') as fh:
                component_src = fh.read()
        except OSError:
            component_src = '/* hypergraph-viewer.js not found — component unavailable */'

        # Escape </script> inside JSON-embedded data to prevent HTML parser from breaking early
        safe_json = _json.dumps(data).replace('</script>', r'<\/script>')

        # ── Host table section (only rendered when bundle includes cluster hosts) ──
        hosts = data.get('hosts') or []
        meta  = data.get('metadata') or {}

        def _threat_cls(t):
            return {'HIGH': 'th-high', 'MEDIUM': 'th-med'}.get(t, 'th-low')

        hosts_rows = ''
        for h in hosts:
            ports_str  = ', '.join(str(p) for p in (h.get('ports') or []))[:60]
            ts         = h.get('last_seen', 0)
            ts_str     = ''
            if ts:
                try:
                    import datetime as _dt
                    ts_str = _dt.datetime.utcfromtimestamp(float(ts)).strftime('%Y-%m-%d %H:%M')
                except Exception:
                    ts_str = str(ts)
            freq       = f"{h.get('frequency', '')}" if h.get('frequency') else ''
            rssi       = f"{h.get('rssi', '')} dBm" if h.get('rssi') is not None else ''
            tc         = _threat_cls(h.get('threat_level', 'LOW'))
            hosts_rows += (
                f'<tr class="{tc}">'
                f'<td>{_json.dumps(h.get("hostname",""))[ 1:-1]}</td>'
                f'<td>{h.get("ip","")}</td>'
                f'<td>{h.get("kind","")}</td>'
                f'<td>{h.get("asn","")} {h.get("asn_org","")}</td>'
                f'<td>{h.get("country","")}</td>'
                f'<td>{ports_str}</td>'
                f'<td class="{tc}">{h.get("threat_level","")}</td>'
                f'<td>{h.get("anomaly", 0.0):.3f}</td>'
                f'<td>{freq}</td>'
                f'<td>{rssi}</td>'
                f'<td>{h.get("protocol","")}</td>'
                f'<td>{ts_str}</td>'
                f'</tr>\n'
            )

        hosts_section = ''
        if hosts:
            cluster_id_safe = _json.dumps(data.get('cluster_id', '')).strip('"')
            hosts_section = f"""
  <section id="hosts-panel">
    <div class="hosts-header" onclick="toggleHosts()">
      <span>🖥 CLUSTER HOSTS — {len(hosts)} members
        · threat: {meta.get('threat_label', '')}
        · {meta.get('behavior_type', '')}
        · {meta.get('asn_org') or meta.get('asn', '')}
        · {meta.get('country', '')}
        &nbsp;<span id="hosts-toggle">▼ collapse</span>
      </span>
      <span class="hosts-meta">
        phase coherence: {meta.get('phase_coherence', 0.0):.3f}
        &nbsp;|&nbsp; asn diversity: {meta.get('asn_diversity', 0)}
        &nbsp;|&nbsp; centroid: {meta.get('centroid_lat', 0.0):.4f}, {meta.get('centroid_lon', 0.0):.4f}
      </span>
    </div>
    <div id="hosts-table-wrap">
      <table id="hosts-table">
        <thead>
          <tr>
            <th>Hostname</th><th>IP</th><th>Kind</th><th>ASN / Org</th>
            <th>Country</th><th>Ports</th><th>Threat</th><th>Anomaly</th>
            <th>Freq</th><th>RSSI</th><th>Protocol</th><th>Last Seen</th>
          </tr>
        </thead>
        <tbody>
{hosts_rows}        </tbody>
      </table>
    </div>
  </section>"""

        hosts_style = """
    #hosts-panel { background: rgba(0,6,18,0.97); border-top: 1px solid #1a2a4a; }
    .hosts-header { padding: 8px 16px; cursor: pointer; display: flex;
                    justify-content: space-between; align-items: center;
                    font-size: 11px; color: #4af; user-select: none; }
    .hosts-header:hover { background: rgba(0,40,80,0.4); }
    .hosts-meta { color: #557; font-size: 10px; }
    #hosts-table-wrap { overflow-x: auto; max-height: 340px; overflow-y: auto; }
    #hosts-table { width: 100%; border-collapse: collapse; font-size: 10px; }
    #hosts-table th { position: sticky; top: 0; background: #07101e;
                      color: #4af; padding: 4px 8px; text-align: left;
                      border-bottom: 1px solid #1a2a4a; white-space: nowrap; }
    #hosts-table td { padding: 3px 8px; border-bottom: 1px solid #0d1926;
                      white-space: nowrap; color: #aac; }
    #hosts-table tr:hover td { background: rgba(0,40,80,0.3); }
    .th-high td { color: #f66; }
    .th-med  td { color: #fa6; }
    .th-low  td { color: #aac; }
    td.th-high { color: #f66 !important; font-weight: bold; }
    td.th-med  { color: #fa6 !important; }
    #viewer { width: 100vw; height: calc(60vh - 40px); }""" if hosts else \
        "\n    #viewer { width: 100vw; height: calc(100vh - 40px); }"

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ background: #06060f; color: #ccc; font-family: monospace; }}
    #header {{ padding: 10px 16px; background: rgba(0,10,30,0.95); border-bottom: 1px solid #1a2a4a;
               display: flex; align-items: center; gap: 14px; }}
    #header h1 {{ font-size: 14px; color: #4af; }}
    #header span {{ font-size: 11px; color: #555; }}{hosts_style}
  </style>
</head>
<body>
  <div id="header">
    <h1>⬡ {title}</h1>
    <span id="hdr-info">loading…</span>
  </div>
  <hypergraph-viewer id="viewer" mode="viewer" style="width:100%;height:calc({'60vh' if hosts else '100vh'} - 40px)"></hypergraph-viewer>
{hosts_section}
  <!-- Three.js 0.149.0 UMD — sets window.THREE and window.THREE.OrbitControls -->
  <script src="https://cdn.jsdelivr.net/npm/three@0.149.0/build/three.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/three@0.149.0/examples/js/controls/OrbitControls.js"></script>
  <script>
    if (window.THREE && window.THREE.OrbitControls) {{
      window.ThreeOrbitControls = window.THREE.OrbitControls;
    }}
    function toggleHosts() {{
      var w = document.getElementById('hosts-table-wrap');
      var t = document.getElementById('hosts-toggle');
      if (!w) return;
      var hidden = w.style.display === 'none';
      w.style.display = hidden ? '' : 'none';
      if (t) t.textContent = hidden ? '▼ collapse' : '► expand';
    }}
  </script>

  <!-- Hypergraph Viewer component -->
  <script>{component_src}</script>

  <!-- Embedded graph data (safe JSON, no executable script) -->
  <script type="application/json" id="hv-data">{safe_json}</script>

  <script>
    (function () {{
      var el  = document.getElementById('viewer');
      var raw = document.getElementById('hv-data').textContent;
      var data;
      try {{ data = JSON.parse(raw); }} catch(e) {{ console.error('Bad embedded data', e); return; }}
      var hdr = document.getElementById('hdr-info');

      el.addEventListener('graph-loaded', function (ev) {{
        var d = ev.detail || {{}};
        hdr.textContent = (d.count || 0) + ' nodes · ' + (d.edge_count || 0) + ' edges'
                        + ({len(hosts)} > 0 ? ' · {len(hosts)} hosts' : '');
      }});

      customElements.whenDefined('hypergraph-viewer').then(function () {{
        el.loadGraph(data);
      }});
    }})();
  </script>
</body>
</html>"""
        return html

    @app.route('/api/gravity/export', methods=['GET'])
    def api_gravity_export():
        """Export the gravity graph as a portable artifact.

        Query params:
          format: 'json' (default) | 'html'

        Returns:
          json  → application/json  (graph snapshot)
          html  → text/html         (standalone self-contained viewer bundle)
        """
        try:
            fmt      = request.args.get('format', 'json').lower()
            snapshot = _gravity_snapshot_readonly()
            if snapshot is None:
                return jsonify({'status': 'error', 'message': 'engine not ready'}), 503

            if fmt == 'html':
                html = _build_export_bundle(snapshot, title='Gravity Map Export')
                resp = make_response(html)
                resp.headers['Content-Type']        = 'text/html; charset=utf-8'
                resp.headers['Content-Disposition'] = f'attachment; filename="gravity-export.html"'
                return resp

            # Default: JSON
            snapshot['status'] = 'ok'
            return jsonify(snapshot)

        except Exception as e:
            logger.exception('[gravity] export failed')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/clusters/export-data/<cluster_id>', methods=['GET'])
    def api_clusters_export_data(cluster_id: str):
        """Return a combined cluster decomposition + graph snapshot for export/viewer.

        Reads cluster metadata from the cache and the full gravity snapshot (read-only).
        Does NOT re-run cluster detection or mutate scoring singletons.

        Returns: { status, cluster_id, nodes, nodes_index, edges, count, edge_count,
                   metadata: { archetype, silence_pressure, node_tier, threat_score,
                               activity_score, phase_coherence, ...decompose fields } }
        """
        try:
            from cluster_swarm_engine import _cluster_cache, decompose_cluster, narrate_cluster

            cluster = _cluster_cache.get(cluster_id)
            if cluster is None:
                return jsonify({
                    'status':  'not_found',
                    'message': 'Cluster not in cache. Call /api/clusters/intel first.',
                }), 404

            narration    = narrate_cluster(cluster)
            decomp       = decompose_cluster(cluster, narration)
            snapshot     = _gravity_snapshot_readonly()

            if snapshot is None:
                return jsonify({'status': 'error', 'message': 'engine not ready'}), 503

            # Build metadata combining cluster fields + decompose intelligence
            _arch  = decomp.get('archetype') or {}
            _tier  = decomp.get('node_tier') or {}
            _sil   = decomp.get('silence_pressure') or {}
            metadata = {
                'archetype':       _arch.get('label', '') if isinstance(_arch, dict) else str(_arch),
                'archetype_desc':  _arch.get('description', '') if isinstance(_arch, dict) else '',
                'silence_pressure': _sil.get('normalized', 0.0) if isinstance(_sil, dict) else float(_sil or 0),
                'node_tier':       _tier.get('label', '') if isinstance(_tier, dict) else str(_tier),
                'threat_score':    cluster.threat_score,
                'activity_score':  narration.get('temporal', {}).get('burst_rate', 0.0),
                'phase_coherence': narration.get('phase', {}).get('phase_coherence', 0.0),
                'asn_diversity':   cluster.asn_diversity,
                'node_count':      cluster.node_count,
                'dimensional_density': decomp.get('dimensional_density'),
                'asn_breakdown':   decomp.get('asn_breakdown'),
                'intent_scores':   decomp.get('intent_scores'),
                'subclusters':     decomp.get('subclusters'),
                'behavior_fingerprint': decomp.get('behavior_fingerprint'),
                'temporal_ghost_events': decomp.get('temporal_ghost_events'),
                'activation_cascade': decomp.get('activation_cascade'),
            }

            return jsonify({
                'status':      'ok',
                'cluster_id':  cluster_id,
                'nodes':       snapshot['nodes'],
                'nodes_index': snapshot['nodes_index'],
                'edges':       snapshot['edges'],
                'count':       snapshot['count'],
                'edge_count':  snapshot['edge_count'],
                'metadata':    metadata,
                'decomposition': decomp,
            })

        except Exception as e:
            logger.exception(f'[clusters] export-data failed for {cluster_id}: {e}')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/clusters/export/<cluster_id>', methods=['GET'])
    def api_clusters_export(cluster_id: str):
        """Download a portable cluster export artifact.

        Query params:
          format: 'bundle' (default) → self-contained HTML viewer with embedded data
                  'json'             → raw export-data JSON

        The bundle embeds Three.js from jsDelivr CDN (0.158.0).
        For air-gapped deployments, download three@0.158.0/build/three.min.js and
        three@0.158.0/examples/js/controls/OrbitControls.js separately and replace the CDN URLs.
        """
        try:
            from cluster_swarm_engine import _cluster_cache, decompose_cluster, narrate_cluster

            cluster = _cluster_cache.get(cluster_id)
            if cluster is None:
                return jsonify({
                    'status':  'not_found',
                    'message': 'Cluster not in cache. Call /api/clusters/intel first.',
                }), 404

            # Build the data object (same logic as export-data endpoint)
            narration = narrate_cluster(cluster)
            decomp    = decompose_cluster(cluster, narration)
            snapshot  = _gravity_snapshot_readonly()
            if snapshot is None:
                return jsonify({'status': 'error', 'message': 'engine not ready'}), 503

            _arch  = decomp.get('archetype') or {}
            _tier  = decomp.get('node_tier') or {}
            _sil   = decomp.get('silence_pressure') or {}
            metadata = {
                'archetype':       _arch.get('label', '') if isinstance(_arch, dict) else str(_arch),
                'archetype_desc':  _arch.get('description', '') if isinstance(_arch, dict) else '',
                'silence_pressure': _sil.get('normalized', 0.0) if isinstance(_sil, dict) else float(_sil or 0),
                'node_tier':       _tier.get('label', '') if isinstance(_tier, dict) else str(_tier),
                'threat_score':    cluster.threat_score,
                # CyberCluster has no activity_score/phase_coherence attributes —
                # pull from the already-computed narration dicts
                'activity_score':  narration.get('temporal', {}).get('burst_rate', 0.0),
                'phase_coherence': narration.get('phase', {}).get('phase_coherence', 0.0),
                'asn_diversity':   cluster.asn_diversity,
                'node_count':      cluster.node_count,
                'asn':             cluster.asn,
                'asn_org':         cluster.asn_org,
                'country':         cluster.country,
                'infra_type':      cluster.infra_type,
                'behavior_type':   cluster.behavior_type,
                'centroid_lat':    cluster.centroid_lat,
                'centroid_lon':    cluster.centroid_lon,
                'dimensional_density': decomp.get('dimensional_density'),
                'intent_scores':   decomp.get('intent_scores'),
                'archetype_traits': decomp.get('archetype_traits'),
                'activation_cascade': decomp.get('activation_cascade'),
                'mobility':        narration.get('mobility'),
                'mobility_note':   narration.get('mobility_note'),
                'threat_label':    cluster.threat_label(),
            }
            hosts = _get_cluster_hosts(cluster)
            data = {
                'cluster_id':  cluster_id,
                'nodes':       snapshot['nodes'],
                'nodes_index': snapshot['nodes_index'],
                'edges':       snapshot['edges'],
                'count':       snapshot['count'],
                'edge_count':  snapshot['edge_count'],
                'metadata':    metadata,
                'hosts':       hosts,
            }

            fmt = request.args.get('format', 'bundle').lower()

            if fmt == 'json':
                data['status'] = 'ok'
                return jsonify(data)

            # HTML bundle
            safe_id  = cluster_id.replace('/', '_').replace('..', '')
            title    = f'Cluster {safe_id} — Hypergraph Export'
            html     = _build_export_bundle(data, title=title)
            resp     = make_response(html)
            resp.headers['Content-Type']        = 'text/html; charset=utf-8'
            resp.headers['Content-Disposition'] = f'attachment; filename="cluster-{safe_id}.html"'
            return resp

        except Exception as e:
            logger.exception(f'[clusters] export failed for {cluster_id}: {e}')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/provenance/summary', methods=['GET'])
    def api_provenance_summary():
        """Return the epistemic posture of the graph.

        Scans edge/node metadata for provenance fields and computes:
          - by_source: sensor / inference / analyst counts
          - evidence_coverage: fraction of inferred edges with artifact refs
          - trust_posture: sensor-heavy | inference-heavy | balanced | sparse
          - stale_inference_count: inferred edges without evidence refs
        """
        try:
            from mcp_context import MCPBuilder
            import mcp_server as mcp_mod

            engine = _get_engine()

            mcp_mod.start_context_build()
            try:
                builder = MCPBuilder(engine)
                ws = builder._build_write_summary()
            finally:
                mcp_mod.end_context_build()

            return jsonify({'status': 'ok', 'write_summary': ws})
        except Exception as e:
            logger.error(f'[provenance] summary failed: {e}')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    # ========================================================================
    # API ROUTES - Collection Tasks
    # ========================================================================

    @app.route('/api/collection/tasks', methods=['GET'])
    def api_collection_tasks():
        """List collection tasks.

        Query params:
          status: filter by status (proposed/accepted/in_progress/satisfied/expired)
          priority: filter by priority (critical/high/medium/low)
          limit: max results (default 20)
        """
        try:
            from collection_tasks import CollectionTaskManager
            engine = _get_engine()
            mgr = CollectionTaskManager(engine)
            status = request.args.get('status')
            priority = request.args.get('priority')
            limit = int(request.args.get('limit', 20))
            tasks = mgr.list_tasks(status=status, priority=priority, limit=limit)
            return jsonify({'status': 'ok', 'tasks': tasks, 'count': len(tasks)})
        except Exception as e:
            logger.error(f'[collection] list failed: {e}')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/collection/tasks', methods=['POST'])
    def api_collection_task_create():
        """Create a new collection task.

        POST JSON body:
          target_type: host|session|geo|flow|edge|org
          target_value: the target ID
          objective: what to learn
          priority: critical|high|medium|low
          recommended_methods: [pcap_capture, sensor_tasking, ...]
          geo_hint: [region names]
          ttl_hours: expiry (default 24)
          interface_hint: capture interface (e.g. "eth0") — optional
          duration_seconds: capture duration (default 60)
          bpf_filter: BPF filter expression (default "ip or ip6")
          sensor_hint: preferred sensors (list of strings)
          confidence_target: desired confidence (default 0.7)
        """
        try:
            from collection_tasks import CollectionTaskManager
            engine = _get_engine()
            mgr = CollectionTaskManager(engine)
            p = request.get_json(silent=True) or {}

            # sensor_hint: accept str or list, normalize to list
            raw_sensor = p.get('sensor_hint')
            if isinstance(raw_sensor, str) and raw_sensor:
                sensor_hint = [raw_sensor]
            elif isinstance(raw_sensor, list):
                sensor_hint = raw_sensor
            else:
                sensor_hint = None

            task = mgr.propose_task(
                target_type=p.get('target_type', 'unknown'),
                target_value=p.get('target_value', ''),
                target_description=p.get('target_description', ''),
                objective=p.get('objective', ''),
                trigger_reason=p.get('trigger_reason', 'operator_request'),
                priority=p.get('priority', 'medium'),
                recommended_methods=p.get('recommended_methods'),
                geo_hint=p.get('geo_hint'),
                ttl_hours=float(p.get('ttl_hours', 24)),
                requested_by=p.get('requested_by', 'operator'),
                related_edges=p.get('related_edges'),
                interface_hint=p.get('interface_hint'),
                duration_seconds=int(p.get('duration_seconds', 60)),
                bpf_filter=p.get('bpf_filter', 'ip or ip6'),
                sensor_hint=sensor_hint,
                confidence_target=float(p.get('confidence_target', 0.7)),
            )
            return jsonify({'status': 'ok', 'task': task.to_dict()})
        except Exception as e:
            logger.error(f'[collection] create failed: {e}')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/collection/tasks/<task_id>/status', methods=['PUT'])
    def api_collection_task_status(task_id):
        """Update a collection task's status.

        PUT JSON body:
          status: proposed|accepted|in_progress|satisfied|expired|rejected
          evidence_refs: [refs] (only for satisfied)
          session_ids: [session_ids] (only for satisfied)
          belief_delta: {edge_id: {before, after}} (only for satisfied)
          by: operator callsign (for accepted)
          reason: rejection reason (for rejected)
        """
        try:
            from collection_tasks import CollectionTaskManager
            engine = _get_engine()
            mgr = CollectionTaskManager(engine)
            p = request.get_json(silent=True) or {}
            new_status = p.get('status', '')
            if new_status == 'satisfied':
                ok = mgr.satisfy_task(
                    task_id,
                    evidence_refs=p.get('evidence_refs'),
                    session_ids=p.get('session_ids'),
                    belief_delta=p.get('belief_delta'),
                )
            else:
                ok = mgr.update_status(
                    task_id, new_status,
                    by=p.get('by', ''),
                    reason=p.get('reason', ''),
                )
            if ok:
                return jsonify({'status': 'ok', 'task_id': task_id, 'new_status': new_status})
            return jsonify({'status': 'error', 'message': 'Task not found or invalid status'}), 404
        except Exception as e:
            logger.error(f'[collection] status update failed: {e}')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/collection/tasks/node/<path:node_id>', methods=['GET'])
    def api_collection_tasks_for_node(node_id):
        """Get collection tasks targeting a specific node."""
        try:
            from collection_tasks import CollectionTaskManager
            engine = _get_engine()
            mgr = CollectionTaskManager(engine)
            tasks = mgr.tasks_for_node(node_id)
            return jsonify({'status': 'ok', 'tasks': tasks, 'count': len(tasks)})
        except Exception as e:
            logger.error(f'[collection] tasks for node failed: {e}')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/collection/gaps', methods=['GET'])
    def api_collection_gaps():
        """Get top beliefs lacking sensor backing (knowledge gaps)."""
        try:
            from collection_tasks import CollectionTaskManager
            engine = _get_engine()
            mgr = CollectionTaskManager(engine)
            limit = int(request.args.get('limit', 10))
            gaps = mgr.collection_gap_summary(limit=limit)
            return jsonify({'status': 'ok', **gaps})
        except Exception as e:
            logger.error(f'[collection] gaps failed: {e}')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/collection/auto-propose', methods=['POST'])
    def api_collection_auto_propose():
        """Auto-propose collection tasks from stale inferences."""
        try:
            from collection_tasks import CollectionTaskManager
            engine = _get_engine()
            mgr = CollectionTaskManager(engine)
            p = request.get_json(silent=True) or {}
            tasks = mgr.auto_propose_from_stale(
                max_tasks=int(p.get('max_tasks', 5)),
                min_confidence=float(p.get('min_confidence', 0.3)),
                ttl_hours=float(p.get('ttl_hours', 24)),
            )
            return jsonify({
                'status': 'ok',
                'tasks': [t.to_dict() for t in tasks],
                'count': len(tasks),
            })
        except Exception as e:
            logger.error(f'[collection] auto-propose failed: {e}')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/collection/check-satisfaction', methods=['POST'])
    def api_collection_check_satisfaction():
        """Check if any active tasks can be closed by new evidence."""
        try:
            from collection_tasks import CollectionTaskManager
            engine = _get_engine()
            mgr = CollectionTaskManager(engine)
            closed = mgr.check_task_satisfaction()
            expired = mgr.expire_stale_tasks()
            return jsonify({
                'status': 'ok',
                'satisfied': closed,
                'expired_count': expired,
            })
        except Exception as e:
            logger.error(f'[collection] satisfaction check failed: {e}')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    # ========================================================================
    # API ROUTES - Capture Commands & Policy
    # ========================================================================

    @app.route('/api/collection/tasks/<task_id>/capture-command', methods=['GET'])
    def api_capture_command(task_id):
        """Emit a pcap.capture command for a collection task.

        TAK-GPT never runs tcpdump. This returns a machine-verifiable
        capture intent dict that operators or automation can execute.
        """
        try:
            from collection_tasks import CollectionTaskManager
            engine = _get_engine()
            mgr = CollectionTaskManager(engine)
            cmd = mgr.emit_capture_command(task_id)
            if cmd is None:
                return jsonify({'status': 'error', 'message': 'Task not found or not active'}), 404
            return jsonify({'status': 'ok', 'command': cmd})
        except Exception as e:
            logger.error(f'[capture] command emission failed: {e}')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/collection/capture-commands', methods=['GET'])
    def api_capture_commands_active():
        """Emit capture commands for all active tasks recommending pcap_capture."""
        try:
            from collection_tasks import CollectionTaskManager
            engine = _get_engine()
            mgr = CollectionTaskManager(engine)
            commands = mgr.emit_capture_commands_for_active()
            return jsonify({'status': 'ok', 'commands': commands, 'count': len(commands)})
        except Exception as e:
            logger.error(f'[capture] active commands failed: {e}')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/capture/policy/evaluate', methods=['POST'])
    def api_capture_policy_evaluate():
        """Evaluate a pcap.capture command against the capture policy.

        POST JSON:
            command: pcap.capture command dict (or task_id to auto-emit)
            context: optional graph context (trust_posture, stale_count, etc.)
        """
        try:
            from capture_policy import get_capture_policy
            from collection_tasks import CollectionTaskManager

            p = request.get_json(silent=True) or {}
            command = p.get('command')
            context = p.get('context', {})

            # Auto-emit command from task_id if not provided
            if not command and p.get('task_id'):
                engine = _get_engine()
                mgr = CollectionTaskManager(engine)
                command = mgr.emit_capture_command(p['task_id'])
                if not command:
                    return jsonify({'status': 'error', 'message': 'Task not found or not active'}), 404

            if not command:
                return jsonify({'status': 'error', 'message': 'No command or task_id provided'}), 400

            policy = get_capture_policy()
            verdict = policy.evaluate(command, context)
            return jsonify({'status': 'ok', 'verdict': verdict.to_dict(), 'command': command})
        except Exception as e:
            logger.error(f'[capture_policy] evaluation failed: {e}')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/capture/policy/rules', methods=['GET'])
    def api_capture_policy_rules():
        """List all capture policy rules."""
        try:
            from capture_policy import get_capture_policy
            policy = get_capture_policy()
            return jsonify({'status': 'ok', 'rules': policy.list_rules()})
        except Exception as e:
            logger.error(f'[capture_policy] list rules failed: {e}')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/capture/policy/rules', methods=['POST'])
    def api_capture_policy_add_rule():
        """Add or update a capture policy rule.

        POST JSON: PolicyRule fields (name, conditions, action, constraints, ...)
        """
        try:
            from capture_policy import get_capture_policy, PolicyRule
            p = request.get_json(silent=True) or {}
            rule = PolicyRule(
                name=p.get('name', ''),
                description=p.get('description', ''),
                conditions=p.get('conditions', {}),
                action=p.get('action', 'REQUIRE_APPROVAL'),
                constraints=p.get('constraints', {}),
                priority_order=int(p.get('priority_order', 100)),
                enabled=p.get('enabled', True),
            )
            if not rule.name:
                return jsonify({'status': 'error', 'message': 'Rule name required'}), 400
            policy = get_capture_policy()
            policy.add_rule(rule)
            return jsonify({'status': 'ok', 'rule': rule.to_dict()})
        except Exception as e:
            logger.error(f'[capture_policy] add rule failed: {e}')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    # ========================================================================
    # API ROUTES - TAK CoT Export
    # ========================================================================

    @app.route('/api/tak/cot', methods=['GET', 'POST'])
    def api_tak_cot():
        """Export geo-bearing nodes as CoT XML for TAK.

        GET params or POST JSON:
          obs_class: comma-separated (e.g. "observed,inferred")
          min_confidence: float (0-1)
          kinds: comma-separated node kinds
          include_edges: bool
          format: "xml_list" | "raw" (default: xml_list)
        """
        try:
            from cot_export import snapshot_to_cot, cot_messages_to_xml_list

            if request.method == 'POST':
                p = request.get_json(silent=True) or {}
            else:
                p = dict(request.args)

            snap = _get_engine_snapshot()
            nodes = snap.get('nodes', [])
            edges = snap.get('edges', [])

            obs_classes = None
            oc_str = p.get('obs_class', p.get('obs_classes', ''))
            if oc_str:
                obs_classes = set(str(oc_str).split(','))

            min_conf = float(p.get('min_confidence', 0.0))

            kinds = None
            kinds_str = p.get('kinds', '')
            if kinds_str:
                kinds = set(str(kinds_str).split(','))

            include_edges = str(p.get('include_edges', 'false')).lower() in ('true', '1', 'yes')

            cot_bytes = snapshot_to_cot(
                nodes, edges,
                obs_classes=obs_classes,
                min_confidence=min_conf,
                geo_kinds=kinds,
                include_edges=include_edges,
            )

            fmt = p.get('format', 'xml_list')
            if fmt == 'raw':
                return Response(
                    b'\n'.join(cot_bytes),
                    mimetype='application/xml',
                )
            else:
                return jsonify({
                    'status': 'ok',
                    'count': len(cot_bytes),
                    'events': cot_messages_to_xml_list(cot_bytes),
                })
        except Exception as e:
            logger.error(f'[TAK] CoT export failed: {e}')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/tak/send', methods=['POST'])
    def api_tak_send():
        """Send CoT markers to a TAK endpoint.

        POST JSON:
        {
          "protocol": "udp" | "tcp",
          "host": "239.2.3.1",
          "port": 6969,
          "obs_class": "observed,inferred",
          "min_confidence": 0.0,
          "include_edges": false
        }
        """
        try:
            from cot_export import snapshot_to_cot, send_cot_udp, send_cot_tcp

            p = request.get_json(silent=True) or {}
            protocol = p.get('protocol', 'udp')
            host = p.get('host', '239.2.3.1')
            port = int(p.get('port', 6969))

            snap = _get_engine_snapshot()
            nodes = snap.get('nodes', [])
            edges = snap.get('edges', [])

            obs_classes = None
            oc_str = p.get('obs_class', '')
            if oc_str:
                obs_classes = set(str(oc_str).split(','))

            min_conf = float(p.get('min_confidence', 0.0))
            include_edges = bool(p.get('include_edges', False))

            cot_bytes = snapshot_to_cot(
                nodes, edges,
                obs_classes=obs_classes,
                min_confidence=min_conf,
                include_edges=include_edges,
            )

            if protocol == 'tcp':
                sent = send_cot_tcp(cot_bytes, host=host, port=port)
            else:
                sent = send_cot_udp(cot_bytes, host=host, port=port)

            return jsonify({'status': 'ok', 'sent': sent, 'total': len(cot_bytes)})
        except Exception as e:
            logger.error(f'[TAK] send failed: {e}')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    # ========================================================================
    # API ROUTES - Heatmap (LandSAR-style probability surfaces)
    # ========================================================================

    @app.route('/api/geo/heatmap', methods=['GET', 'POST'])
    def api_geo_heatmap():
        """Generate a LandSAR-style probability heatmap over H3 cells.

        GET params or POST JSON:
          resolution: int (3-8, default 6)
          obs_class: comma-separated
          min_confidence: float
          kinds: comma-separated node kinds
          format: "json" | "geojson" | "kml" (default: json)
          top_n: int (limit cells, default 500)
          include_boundaries: bool (default true)
        """
        try:
            from h3_heatmap import generate_heatmap

            if request.method == 'POST':
                p = request.get_json(silent=True) or {}
            else:
                p = dict(request.args)

            snap = _get_engine_snapshot()
            nodes = snap.get('nodes', [])
            edges = snap.get('edges', [])

            resolution = int(p.get('resolution', p.get('h3_res', 6)))
            resolution = max(3, min(resolution, 8))

            obs_classes = None
            oc_str = p.get('obs_class', p.get('obs_classes', ''))
            if oc_str:
                obs_classes = set(str(oc_str).split(','))

            min_conf = float(p.get('min_confidence', 0.0))

            kinds = None
            kinds_str = p.get('kinds', '')
            if kinds_str:
                kinds = set(str(kinds_str).split(','))

            top_n = int(p.get('top_n', 500))
            include_boundaries = str(p.get('include_boundaries', 'true')).lower() in ('true', '1', 'yes')
            fmt = p.get('format', 'json')

            layer = generate_heatmap(
                nodes, edges,
                resolution=resolution,
                obs_classes=obs_classes,
                min_confidence=min_conf,
                kind_filter=kinds,
                label=f'rf_scythe_r{resolution}',
            )

            if fmt == 'geojson':
                return jsonify(layer.to_geojson(top_n=top_n))
            elif fmt == 'kml':
                kml = layer.to_kml(top_n=top_n)
                return Response(kml, mimetype='application/vnd.google-earth.kml+xml',
                                headers={'Content-Disposition': 'attachment; filename=heatmap.kml'})
            else:
                return jsonify({
                    'status': 'ok',
                    'heatmap': layer.to_dict(include_boundaries=include_boundaries, top_n=top_n),
                })
        except Exception as e:
            logger.error(f'[Heatmap] generation failed: {e}')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/geo/heatmap/update', methods=['POST'])
    def api_geo_heatmap_update():
        """Bayesian update: apply negative scan results to refine heatmap.

        POST JSON:
        {
          "scanned_cells": ["h3_cell_id_1", ...],
          "detection_probability": 0.8,
          "resolution": 6,
          ... (same filters as /api/geo/heatmap)
        }
        """
        try:
            from h3_heatmap import generate_heatmap, bayesian_update

            p = request.get_json(silent=True) or {}
            scanned = set(p.get('scanned_cells', []))
            pd = float(p.get('detection_probability', 0.8))
            resolution = int(p.get('resolution', 6))

            if not scanned:
                return jsonify({'status': 'error', 'message': 'scanned_cells required'}), 400

            snap = _get_engine_snapshot()
            nodes = snap.get('nodes', [])
            edges = snap.get('edges', [])

            obs_classes = None
            oc_str = p.get('obs_class', '')
            if oc_str:
                obs_classes = set(str(oc_str).split(','))

            # Generate prior
            prior = generate_heatmap(
                nodes, edges,
                resolution=resolution,
                obs_classes=obs_classes,
            )

            # Apply Bayesian update
            posterior = bayesian_update(prior, scanned, detection_probability=pd)

            fmt = p.get('format', 'json')
            top_n = int(p.get('top_n', 500))

            if fmt == 'geojson':
                return jsonify(posterior.to_geojson(top_n=top_n))
            else:
                return jsonify({
                    'status': 'ok',
                    'scanned_cells': len(scanned),
                    'detection_probability': pd,
                    'heatmap': posterior.to_dict(include_boundaries=True, top_n=top_n),
                })
        except Exception as e:
            logger.error(f'[Heatmap] Bayesian update failed: {e}')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    # ========================================================================
    # API ROUTES - NMAP
    # ========================================================================

    @app.route('/api/nmap/scan', methods=['POST', 'GET'])
    def nmap_scan():
        """Run an nmap scan"""
        try:
            if request.method == 'POST':
                data = request.get_json() or {}
                target = data.get('target', '192.168.1.0/24')
                options = data.get('options', '-sn')
            else:
                target = request.args.get('target', '192.168.1.0/24')
                options = request.args.get('options', '-sn')

            results = nmap_scanner.scan(target, options)
            return jsonify(results)
        except Exception as e:
            logger.error(f"Error running nmap scan: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/nmap/status', methods=['GET'])
    def nmap_status():
        """Get nmap scanner status"""
        return jsonify({
            'available': nmap_scanner.check_nmap_available(),
            'scanning': nmap_scanner.scanning,
            'last_scan': nmap_scanner.last_scan_time,
            'cached_results': bool(nmap_scanner.scan_results)
        })

    @app.route('/api/nmap/results', methods=['GET'])
    def nmap_results():
        """Get cached nmap results"""
        return jsonify(nmap_scanner.scan_results or {'status': 'no_results'})

    # ========================================================================
    # API ROUTES - NETWORK HYPERGRAPH (NMAP + HYPERGRAPH)
    # ========================================================================

    @app.route('/api/network-hypergraph/scan', methods=['POST', 'GET'])
    def network_hypergraph_scan():
        """Scan network with nmap and create hypergraph visualization"""
        try:
            if request.method == 'POST':
                data = request.get_json() or {}
                target = data.get('target', '192.168.1.0/24')
                options = data.get('options', '-sV -sn')
                reset = data.get('reset', True)
            else:
                target = request.args.get('target', '192.168.1.0/24')
                options = request.args.get('options', '-sV -sn')
                reset = request.args.get('reset', 'true').lower() == 'true'

            # Reset hypergraph if requested
            if reset:
                hypergraph_store.reset()

            # Run nmap scan
            logger.info(f"Running network hypergraph scan on {target}")
            scan_results = nmap_scanner.scan(target, options)

            if scan_results.get('status') == 'error':
                return jsonify(scan_results), 500

            # Convert scan results to hypergraph nodes
            hosts = scan_results.get('hosts', scan_results.get('results', []))
            node_ids = []

            for host in hosts:
                node_id = hypergraph_store.add_network_host(host)
                node_ids.append(node_id)
                logger.info(f"Added network host: {host.get('ip')} as {node_id}")

            # Create service-based hyperedges
            service_edges = hypergraph_store.create_service_hyperedges()
            logger.info(f"Created {service_edges} service hyperedges")

            # Create subnet-based hyperedges
            subnet_edges = hypergraph_store.create_subnet_hyperedges()
            logger.info(f"Created {subnet_edges} subnet hyperedges")

            # Get visualization data
            viz_data = hypergraph_store.get_visualization_data()
            viz_data['scan_info'] = {
                'target': target,
                'hosts_discovered': len(hosts),
                'service_groups': service_edges,
                'subnet_groups': subnet_edges,
                'nmap_available': nmap_scanner.check_nmap_available(),
                'simulated': scan_results.get('simulated', False)
            }

            return jsonify(viz_data)
        except Exception as e:
            logger.error(f"Error in network hypergraph scan: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/network-hypergraph/localhost', methods=['GET'])
    def network_hypergraph_localhost():
        """Quick scan of localhost services and create hypergraph"""
        try:
            # Reset and scan localhost
            hypergraph_store.reset()

            # Scan localhost for open ports
            scan_results = nmap_scanner.scan('127.0.0.1', '-sV -p 1-1024')

            hosts = scan_results.get('hosts', scan_results.get('results', []))
            for host in hosts:
                hypergraph_store.add_network_host(host)

            # Also add the server itself
            hypergraph_store.add_network_host({
                'ip': '127.0.0.1',
                'hostname': 'rf-scythe-server',
                'ports': [
                    {'port': '8080/tcp', 'state': 'open', 'service': 'http-api'},
                ],
                'status': 'up'
            })

            # Create hyperedges
            hypergraph_store.create_service_hyperedges()
            hypergraph_store.create_subnet_hyperedges()

            return jsonify(hypergraph_store.get_visualization_data())
        except Exception as e:
            logger.error(f"Error scanning localhost: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/network-hypergraph/quick-scan', methods=['GET'])
    def network_hypergraph_quick_scan():
        """Quick network discovery scan (ping sweep only)"""
        try:
            target = request.args.get('target', '192.168.1.0/24')

            # Reset and do ping sweep only (fast)
            hypergraph_store.reset()
            scan_results = nmap_scanner.scan(target, '-sn -T4')

            hosts = scan_results.get('hosts', scan_results.get('results', []))
            for host in hosts:
                hypergraph_store.add_network_host(host)

            # Create subnet hyperedges
            hypergraph_store.create_subnet_hyperedges()

            viz_data = hypergraph_store.get_visualization_data()
            viz_data['scan_type'] = 'quick_discovery'
            viz_data['hosts_found'] = len(hosts)

            return jsonify(viz_data)
        except Exception as e:
            logger.error(f"Error in quick scan: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    # ========================================================================
    # API ROUTES - TIMING & GEOLOCATION (RTT / TDoA)
    # ========================================================================
    # Legacy constant retained for backward compat. New code uses fusion_engine.
    _RTT_EFFECTIVE_KM_PER_MS = 62.5  # km per avg-RTT  (125,000 / 2000)

    # Lazy fusion engine — initialized on first use with server geo if known
    _fusion_engine = None
    def _get_fusion_engine():
        global _fusion_engine
        if _fusion_engine is None:
            try:
                from fusion_engine import FusionEngine
                _fusion_engine = FusionEngine(server_lat=0.0, server_lon=0.0)
            except Exception as _fe_err:
                logger.warning(f"fusion_engine unavailable: {_fe_err}")
        return _fusion_engine

    # Lazy geo inference engine — RTT trilateration + confidence fusion
    _geo_infer_engine = None
    def _get_geo_infer_engine():
        global _geo_infer_engine
        if _geo_infer_engine is None:
            try:
                from geo_inference import GeoInferenceEngine
                _geo_infer_engine = GeoInferenceEngine()
                logger.info('[GeoInfer] RTT trilateration engine initialized')
            except Exception as _gi_err:
                logger.warning(f'[GeoInfer] engine unavailable: {_gi_err}')
        return _geo_infer_engine

    @app.route('/api/geo/infer', methods=['POST'])
    def geo_infer_node():
        """Derive node geographic position from RTT + topology + ASN — no GeoIP required.

        POST JSON:
        {
          "node_id": "PCAP-1.2.3.4",          // required — node to locate
          "rtt_anchors": [                     // optional — known-geo nodes with RTT
            {"lat": 37.7, "lon": -122.4, "rtt_ms": 22.4},
            ...
          ],
          "neighbor_node_ids": ["nodeA","nodeB"],  // optional — connected known nodes
          "asn": "AS15169",                    // optional — ASN for centroid fallback
          "persist": true                      // optional — write result to geo index
        }

        Returns: {lat, lon, confidence, method, anomalies, ...}
        """
        d          = request.get_json() or {}
        node_id    = d.get('node_id', '').strip()
        if not node_id:
            return jsonify({'status': 'error', 'message': 'node_id required'}), 400

        engine = _get_geo_infer_engine()
        if engine is None:
            return jsonify({'status': 'error', 'message': 'geo inference engine unavailable'}), 503

        rtt_anchors      = d.get('rtt_anchors') or []
        neighbor_ids     = d.get('neighbor_node_ids') or []
        asn              = d.get('asn')
        persist_result   = bool(d.get('persist', True))

        # Resolve neighbor coords from node geo index
        neighbor_coords = []
        if neighbor_ids and map_cache is not None:
            known = map_cache.get_multiple_node_geos(neighbor_ids)
            neighbor_coords = [
                {'lat': v['lat'], 'lon': v['lon'], 'confidence': v['confidence']}
                for v in known.values()
            ]

        # ASN centroid — look up in geo index first (any node with matching ASN)
        asn_centroid = None
        if asn and map_cache is not None:
            try:
                with map_cache._conn() as c:
                    row = c.execute(
                        "SELECT AVG(lat) as lat, AVG(lon) as lon, COUNT(*) as n "
                        "FROM node_geo_index WHERE asn=? AND confidence >= 0.8", (asn,)
                    ).fetchone()
                if row and row['n'] >= 2:
                    asn_centroid = (row['lat'], row['lon'])
            except Exception:
                pass

        result = engine.infer(
            node_id         = node_id,
            rtt_anchors     = rtt_anchors if len(rtt_anchors) >= 3 else None,
            neighbor_coords = neighbor_coords if len(neighbor_coords) >= 2 else None,
            asn_centroid    = asn_centroid,
            fast            = len(rtt_anchors) < 6
        )

        if result is None:
            return jsonify({'status': 'insufficient_data', 'node_id': node_id,
                            'message': 'Need ≥3 RTT anchors or ≥2 geo-known neighbors'}), 200

        if persist_result and map_cache is not None:
            map_cache.upsert_node_geo(
                node_id, result['lat'], result['lon'],
                asn=asn, confidence=result['confidence'], method=result['method']
            )

        return jsonify({'status': 'ok', 'node_id': node_id, **result})

    @app.route('/api/geo/check-path', methods=['POST'])
    def geo_check_path():
        """Scan a hop path for physically impossible RTT segments.

        Detects relay chains, VPN tunnels, and spoofed topology.

        POST JSON:
        {
          "hops": [
            {"lat": 37.7, "lon": -122.4, "rtt_ms": 5.2},
            {"lat": 40.7, "lon": -74.0,  "rtt_ms": 28.1},
            ...
          ]
        }

        Returns: {anomalies: [...], plausible_hops: int, total_hops: int}
        """
        d    = request.get_json() or {}
        hops = d.get('hops') or []
        if len(hops) < 2:
            return jsonify({'status': 'error', 'message': 'Need ≥2 hops'}), 400

        engine = _get_geo_infer_engine()
        if engine is None:
            return jsonify({'status': 'error', 'message': 'geo inference engine unavailable'}), 503

        anomalies = engine.check_path_anomalies(hops)
        return jsonify({
            'status':        'ok',
            'total_hops':    len(hops),
            'plausible_hops': len(hops) - 1 - len(anomalies),
            'anomalies':     anomalies,
            'clean':         len(anomalies) == 0
        })

    @app.route('/api/timing/probe', methods=['GET', 'POST'])
    def timing_probe():
        """Measure RTT to a target via ICMP ping and estimate geographic distance.
        Returns min/avg/max RTT and an estimated distance from this server.
        Query params: target (required), count (optional, default 5)
        Returns enhanced RTT stats including min/percentiles/jitter + confidence-weighted distance.
        """
        if request.method == 'POST':
            data = request.get_json() or {}
            target = data.get('target', '')
            count = int(data.get('count', 5))
        else:
            target = request.args.get('target', '')
            count = int(request.args.get('count', 5))

        if not target:
            return jsonify({'status': 'error', 'message': 'target is required'}), 400

        import re
        if not re.match(r'^[a-zA-Z0-9._\-:]+$', target):
            return jsonify({'status': 'error', 'message': 'Invalid target'}), 400

        count = max(3, min(count, 10))

        try:
            try:
                _is_v6 = ipaddress.ip_address(target).version == 6
            except ValueError:
                _is_v6 = False
            cmd = ['ping6' if _is_v6 else 'ping', '-c', str(count), '-W', '2', target]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            output = result.stdout + result.stderr

            # Parse individual RTT samples: "time=23.1 ms"
            sample_re = re.compile(r'time=([\d.]+)\s*ms')
            rtt_samples = [float(m.group(1)) for m in sample_re.finditer(output)]

            # Parse ping summary line: rtt min/avg/max/mdev = ...
            rtt_min = rtt_avg = rtt_max = rtt_mdev = None
            packets_recv = 0
            for line in output.splitlines():
                m = re.search(r'rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)', line)
                if m:
                    rtt_min, rtt_avg, rtt_max, rtt_mdev = map(float, m.groups())
                m2 = re.search(r'(\d+) received', line)
                if m2:
                    packets_recv = int(m2.group(1))

            if rtt_avg is None and not rtt_samples:
                return jsonify({
                    'status': 'unreachable',
                    'target': target,
                    'message': 'Host did not respond to ping',
                    'raw_output': output[:500]
                })

            # Use parsed samples if available; fall back to summary line values
            if not rtt_samples and rtt_min is not None:
                rtt_samples = [rtt_min, rtt_avg, rtt_max]

            # Enhanced stats via fusion engine
            fe = _get_fusion_engine()
            if fe:
                stats = fe._rtt.stats(rtt_samples)
                dist_est = fe._dist.estimate(
                    rtt_min_ms=stats.get('min', rtt_min or 0),
                    rtt_jitter_ms=stats.get('jitter', rtt_mdev or 0),
                )
            else:
                # Fallback stats without fusion engine
                s = sorted(rtt_samples) if rtt_samples else []
                n = len(s)
                stats = {
                    'min':    round(s[0], 3) if s else rtt_min,
                    'p25':    round(s[max(0, n//4)], 3) if s else None,
                    'median': round(s[n//2], 3) if s else rtt_avg,
                    'p75':    round(s[min(n-1, 3*n//4)], 3) if s else None,
                    'max':    round(s[-1], 3) if s else rtt_max,
                    'jitter': round(s[min(n-1,3*n//4)] - s[max(0,n//4)], 3) if n >= 2 else (rtt_mdev or 0),
                }
                eff_min = stats.get('min') or rtt_min or rtt_avg or 0
                estimated = round(eff_min * 50.0, 1)
                dist_est = {'estimate_km': estimated, 'min_km': round(estimated*0.6,1),
                            'max_km': round(estimated*1.8,1), 'confidence': 0.5}

            # Legacy field kept for backward compat
            estimated_distance_km = dist_est.get('estimate_km') or round((rtt_avg or 0) * _RTT_EFFECTIVE_KM_PER_MS, 1)

            return jsonify({
                'status':               'ok',
                'target':               target,
                # Legacy fields (backward compat)
                'rtt_min_ms':           rtt_min,
                'rtt_avg_ms':           rtt_avg,
                'rtt_max_ms':           rtt_max,
                'rtt_jitter_ms':        rtt_mdev,
                'estimated_distance_km': estimated_distance_km,
                # Enhanced fields
                'rtt_stats':            stats,
                'distance_estimate_km': dist_est.get('estimate_km'),
                'distance_min_km':      dist_est.get('min_km'),
                'distance_max_km':      dist_est.get('max_km'),
                'confidence':           dist_est.get('confidence', 0.5),
                'asn_type':             dist_est.get('asn_type', 'unknown'),
                'packets_sent':         count,
                'packets_received':     packets_recv,
                'note':                 'distance_estimate_km uses min-RTT × 50 km/ms (fiber+routing factor)',
                'timestamp':            time.time()
            })

        except subprocess.TimeoutExpired:
            return jsonify({'status': 'timeout', 'target': target, 'message': 'Ping timed out'})
        except Exception as e:
            logger.error(f"Timing probe error: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/timing/traceroute', methods=['GET', 'POST'])
    def timing_traceroute():
        """Run a traceroute to target and return per-hop RTT + cumulative distance estimates.
        Uses nmap --traceroute if available, falls back to traceroute/tracepath binary.
        """
        if request.method == 'POST':
            data = request.get_json() or {}
            target = data.get('target', '')
            max_hops = int(data.get('max_hops', 20))
        else:
            target = request.args.get('target', '')
            max_hops = int(request.args.get('max_hops', 20))

        if not target:
            return jsonify({'status': 'error', 'message': 'target is required'}), 400

        import re
        if not re.match(r'^[a-zA-Z0-9._\-:]+$', target):
            return jsonify({'status': 'error', 'message': 'Invalid target'}), 400

        max_hops = max(5, min(max_hops, 30))

        def _parse_traceroute_line(line):
            """Return (hop_num, ip, rtt_ms) or None."""
            m = re.match(r'^\s*(\d+)\s+(?:([a-zA-Z0-9._\-]+)\s+\(([0-9.]+)\)|([0-9.]+))\s+([\d.]+)\s+ms', line)
            if m:
                hop = int(m.group(1))
                ip = m.group(3) or m.group(4) or m.group(2) or '*'
                rtt = float(m.group(5))
                return hop, ip, rtt
            # Simpler: "  2  192.168.1.1  1.234 ms"
            m2 = re.match(r'^\s*(\d+)\s+([\d.]+)\s+([\d.]+)\s+ms', line)
            if m2:
                return int(m2.group(1)), m2.group(2), float(m2.group(3))
            return None

        hops = []
        used_tool = None

        # Try nmap traceroute first
        if nmap_scanner.check_nmap_available():
            try:
                try:
                    _tr_v6 = ['-6'] if ipaddress.ip_address(target).version == 6 else []
                except ValueError:
                    _tr_v6 = []
                cmd = ['nmap'] + _tr_v6 + ['-sn', '--traceroute', '-T4', '--max-retries', '1', target]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                in_traceroute = False
                for line in res.stdout.splitlines():
                    if 'TRACEROUTE' in line:
                        in_traceroute = True
                        continue
                    if in_traceroute:
                        # nmap format: "HOP RTT     ADDRESS"
                        m = re.match(r'^\s*(\d+)\s+([\d.]+)\s+ms\s+(\S+)', line)
                        if m:
                            hop_n, rtt_ms, addr = int(m.group(1)), float(m.group(2)), m.group(3)
                            hops.append({'hop': hop_n, 'ip': addr, 'rtt_ms': rtt_ms,
                                         'estimated_km': round(rtt_ms * _RTT_EFFECTIVE_KM_PER_MS, 1)})
                        elif line.strip() == '' and hops:
                            break
                if hops:
                    used_tool = 'nmap'
            except Exception:
                pass

        # Fall back to traceroute binary
        if not hops:
            try:
                tr_bin = None
                for binary in ['traceroute', 'tracepath']:
                    check = subprocess.run(['which', binary], capture_output=True)
                    if check.returncode == 0:
                        tr_bin = binary
                        break

                if tr_bin:
                    cmd = [tr_bin, '-n', '-m', str(max_hops), '-w', '2', target]
                    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                    for line in res.stdout.splitlines():
                        parsed = _parse_traceroute_line(line)
                        if parsed:
                            hop_n, ip, rtt_ms = parsed
                            hops.append({'hop': hop_n, 'ip': ip, 'rtt_ms': rtt_ms,
                                         'estimated_km': round(rtt_ms * _RTT_EFFECTIVE_KM_PER_MS, 1)})
                    if hops:
                        used_tool = tr_bin
            except Exception:
                pass

        if not hops:
            return jsonify({
                'status': 'simulated',
                'target': target,
                'message': 'traceroute/nmap not available — returning simulated hops',
                'hops': [
                    {'hop': i, 'ip': f'10.0.0.{i}', 'rtt_ms': round(5 + i * 8 + random.uniform(-2, 2), 1),
                     'estimated_km': round((5 + i * 8) * _RTT_EFFECTIVE_KM_PER_MS, 1),
                     'delta_rtt_ms': round(8 + random.uniform(-1, 1), 1),
                     'delta_km': round(8 * 50, 1), 'anomaly': None}
                    for i in range(1, 9)
                ],
                'simulated': True
            })

        # Annotate hops with anomaly flags, delta distances, and MIMO hop class
        fe = _get_fusion_engine()
        path_summary = {}
        if fe:
            hops = fe._rtt.filter_hops(hops)
            hops = fe._mimo.classify_hops(hops)
            path_summary = fe._mimo.path_summary(hops)
            # Attach ASN type/penalty for public hops with org info
            for hop in hops:
                org = hop.get('org', '') or ''
                if org:
                    hop['asn_type']    = fe._asn.classify(org)
                    hop['asn_penalty'] = fe._asn.profile(org).get('penalty', 1.6)

        # ── Multi-source geo resolver for traceroute hops ────────────────────────
        # Pipeline: POP-code → cloud/CDN subnet → embedded-IP → GeoIP fallback.
        # Hostnames are passed through (ValueError → pass, not continue).
        _HOP_POP_COORDS = {
            'sin': (1.3521,   103.8198,  'Singapore'),
            'hkg': (22.3193,  114.1694,  'Hong Kong'),
            'nrt': (35.6762,  139.6503,  'Tokyo'),
            'syd': (-33.8688, 151.2093,  'Sydney'),
            'iad': (38.9531,  -77.4565,  'Ashburn VA'),
            'ash': (38.9531,  -77.4565,  'Ashburn VA'),
            'dfw': (32.8998,  -97.0403,  'Dallas TX'),
            'dal': (32.7767,  -96.7970,  'Dallas TX'),
            'lax': (33.9416,  -118.4085, 'Los Angeles CA'),
            'sjc': (37.3382,  -121.8863, 'San Jose CA'),
            'sea': (47.6062,  -122.3321, 'Seattle WA'),
            'ord': (41.9742,  -87.9073,  'Chicago IL'),
            'ewr': (40.6895,  -74.1745,  'Newark NJ'),
            'nyc': (40.7128,  -74.0060,  'New York NY'),
            'bos': (42.3601,  -71.0589,  'Boston MA'),
            'mia': (25.7617,  -80.1918,  'Miami FL'),
            'atl': (33.7490,  -84.3880,  'Atlanta GA'),
            'den': (39.7392,  -104.9903, 'Denver CO'),
            'fra': (50.0379,  8.5622,    'Frankfurt'),
            'ams': (52.3105,  4.7683,    'Amsterdam'),
            'lon': (51.5098,  -0.1180,   'London'),
            'par': (48.8600,  2.3522,    'Paris'),
            'mad': (40.4168,  -3.7038,   'Madrid'),
            'mum': (19.0760,  72.8777,   'Mumbai'),
            'del': (28.6139,  77.2090,   'Delhi'),
        }
        # Linode/Akamai subnet (first-two-octet) → (lat, lon, city, confidence)
        _CLOUD_SUBNETS = {
            (172, 104): (40.7357,  -74.1724, 'Newark NJ',      0.85, 'cloud_region'),
            (172, 234): (40.7357,  -74.1724, 'Newark NJ',      0.85, 'cloud_region'),
            (45,  79):  (33.7490,  -84.3880, 'Atlanta GA',     0.85, 'cloud_region'),
            (139, 162): (51.5098,  -0.1180,  'London',         0.85, 'cloud_region'),
            (178, 79):  (50.0379,  8.5622,   'Frankfurt',      0.85, 'cloud_region'),
            (192, 46):  (37.3382,  -121.8863,'Fremont CA',     0.85, 'cloud_region'),
            (23,  92):  (37.3382,  -121.8863,'Fremont CA',     0.85, 'cloud_region'),
            (47,  236): (1.3521,   103.8198, 'Singapore',      0.90, 'cloud_region'),  # Alibaba
            (47,  74):  (37.3382,  -121.8863,'US West',        0.80, 'cloud_region'),
            (47,  88):  (40.7128,  -74.0060, 'US East',        0.80, 'cloud_region'),
        }

        def _pop_resolve(hostname):
            """POP-code label match: sin03 → Singapore. Returns (lat,lon,city,conf) or None."""
            lower = hostname.lower()
            for label in lower.replace('-', '.').split('.'):
                for pop, (lat, lon, city) in _HOP_POP_COORDS.items():
                    if label == pop or (label.startswith(pop) and label[len(pop):].isdigit()):
                        return lat, lon, city, 0.97
            return None

        def _cloud_resolve(ip_str):
            """Cloud/CDN subnet table lookup. Returns (lat,lon,city,conf,method) or None."""
            try:
                p = list(map(int, ip_str.split('.')))
                if len(p) == 4:
                    hit = _CLOUD_SUBNETS.get((p[0], p[1]))
                    if hit:
                        return hit
            except (ValueError, TypeError):
                pass
            return None

        def _extract_embedded_ip(hostname):
            """Recover IP from patterns like 172-234-197-23.x.y.z or a23-213-15-225.cdn.com"""
            try:
                first = hostname.split('.')[0]
                stripped = first.lstrip('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ')
                parts = stripped.split('-')
                if len(parts) == 4:
                    candidate = '.'.join(parts)
                    addr = ipaddress.ip_address(candidate)
                    if not (addr.is_private or addr.is_loopback or addr.is_link_local):
                        return candidate
            except Exception:
                pass
            return None

        for _hop in hops:
            _ip = _hop.get('ip', '')
            if not _ip or _ip == '*':
                continue
            _lookup_target = _ip
            try:
                _hopaddr = ipaddress.ip_address(_ip)
                if _hopaddr.is_private or _hopaddr.is_loopback or _hopaddr.is_link_local:
                    continue  # private IP — no geo possible
                # Cloud subnet fast-path (no API call)
                _cr = _cloud_resolve(_ip)
                if _cr:
                    _hop['geo'] = {'lat': _cr[0], 'lon': _cr[1], 'city': _cr[2],
                                   'country': '', 'org': '', 'as': '',
                                   'method': _cr[4], 'confidence': _cr[3]}
                    _hop['lat'], _hop['lon'] = _cr[0], _cr[1]
                    continue
            except ValueError:
                # Hostname — try POP code first (most authoritative, no API call)
                _pr = _pop_resolve(_ip)
                if _pr:
                    _hop['geo'] = {'lat': _pr[0], 'lon': _pr[1], 'city': _pr[2],
                                   'country': '', 'org': '', 'as': '',
                                   'method': 'pop', 'confidence': _pr[3]}
                    _hop['lat'], _hop['lon'] = _pr[0], _pr[1]
                    continue
                # Extract embedded IP (Akamai/Linode style) then check cloud subnets
                _emb = _extract_embedded_ip(_ip)
                if _emb:
                    _lookup_target = _emb
                    _cr = _cloud_resolve(_emb)
                    if _cr:
                        _hop['geo'] = {'lat': _cr[0], 'lon': _cr[1], 'city': _cr[2],
                                       'country': '', 'org': '', 'as': '',
                                       'method': _cr[4], 'confidence': _cr[3]}
                        _hop['lat'], _hop['lon'] = _cr[0], _cr[1]
                        continue
                # Fall through to GeoIP with hostname or extracted IP as target
            try:
                _geo_url = (f'http://ip-api.com/json/{_lookup_target}'
                            f'?fields=status,lat,lon,city,country,org,as')
                _gctx = ssl.create_default_context()
                _gctx.check_hostname = False
                _gctx.verify_mode = ssl.CERT_NONE
                _greq = urllib.request.Request(
                    _geo_url, headers={'User-Agent': 'rf-scythe/1.0'})
                with urllib.request.urlopen(_greq, timeout=2, context=_gctx) as _gresp:
                    _gd = json.loads(_gresp.read().decode())
                    if _gd.get('status') == 'success':
                        _hop['geo'] = {
                            'lat':        _gd.get('lat'),
                            'lon':        _gd.get('lon'),
                            'city':       _gd.get('city', ''),
                            'country':    _gd.get('country', ''),
                            'org':        _gd.get('org', ''),
                            'as':         _gd.get('as', ''),
                            'method':     'geoip',
                            'confidence': 0.6,
                        }
                        _hop['lat'] = _gd.get('lat')
                        _hop['lon'] = _gd.get('lon')
            except Exception:
                pass  # GeoIP is optional per hop

        anomalous_hops = [h['hop'] for h in hops if h.get('anomaly')]
        # Clean = no anomaly AND not a MIMO skip-distance hop (rf_link, reassembly, core, CGNAT, mpls)
        clean_hops = [h for h in hops if not h.get('anomaly') and not h.get('skip_distance')]

        # Physics-layer anomaly scan — detect relay chains / VPN tunnels in hop path
        _gi = _get_geo_infer_engine()
        physics_anomalies = []
        if _gi is not None:
            _geo_hops = [
                {'lat': h['geo']['lat'], 'lon': h['geo']['lon'], 'rtt_ms': h.get('rtt_ms', 0)}
                for h in hops
                if h.get('geo') and h['geo'].get('lat') is not None and h.get('rtt_ms')
            ]
            if len(_geo_hops) >= 2:
                physics_anomalies = _gi.check_path_anomalies(_geo_hops)
                # Tag hops with physics anomaly flag
                _anom_idx = {a['hop'] for a in physics_anomalies}
                for i, h in enumerate(hops):
                    if i in _anom_idx:
                        h['physics_anomaly'] = next(
                            a for a in physics_anomalies if a['hop'] == i
                        )

        # Best distance: last clean hop's rtt × 50 km/ms
        last_clean = clean_hops[-1] if clean_hops else (hops[-1] if hops else {})
        best_total_rtt = last_clean.get('rtt_ms') or 0
        best_total_km  = round(best_total_rtt * 50.0, 1) if best_total_rtt else None

        return jsonify({
            'status':               'ok',
            'target':               target,
            'hops':                 hops,
            'total_hops':           len(hops),
            'anomalous_hops':       anomalous_hops,
            'physics_anomalies':    physics_anomalies,
            'clean_hop_count':      len(clean_hops),
            'total_rtt_ms':         hops[-1]['rtt_ms'] if hops else None,
            'estimated_total_km':   hops[-1].get('estimated_km') if hops else None,
            'distance_estimate_km': best_total_km,
            'path_summary':         path_summary,
            'tool_used':            used_tool,
            'timestamp':            time.time()
        })

    @app.route('/api/timing/geo-path', methods=['GET', 'POST'])
    def timing_geo_path():
        """Geo-enriched traceroute path for Cesium GreatCircleLayer rendering.
        Runs traceroute, filters anomalous hops, geolocates each hop IP,
        and returns an ordered list of lat/lon waypoints.
        GET/POST params: target (required), max_hops (default 20)
        """
        if request.method == 'POST':
            data = request.get_json() or {}
            target   = data.get('target', '')
            max_hops = int(data.get('max_hops', 20))
        else:
            target   = request.args.get('target', '')
            max_hops = int(request.args.get('max_hops', 20))

        if not target:
            return jsonify({'status': 'error', 'message': 'target required'}), 400

        import re as _re2
        if not _re2.match(r'^[a-zA-Z0-9._\-:]+$', target):
            return jsonify({'status': 'error', 'message': 'Invalid target'}), 400

        # ── Geo-path cache check (survives server restart) ────────────────────
        if map_cache is not None:
            _cached_path = map_cache.get_geo_path(target)
            if _cached_path:
                _cached_path['cached'] = True
                return jsonify(_cached_path)

        # 1. Run traceroute (reuse timing_traceroute logic inline)
        raw_hops = []
        try:
            try:
                _tr_v6 = ['-6'] if ipaddress.ip_address(target).version == 6 else []
            except ValueError:
                _tr_v6 = []
            if nmap_scanner.check_nmap_available():
                cmd = ['nmap'] + _tr_v6 + ['-sn', '--traceroute', '-T4', '--max-retries', '1', target]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                in_tr = False
                for line in res.stdout.splitlines():
                    if 'TRACEROUTE' in line:
                        in_tr = True; continue
                    if in_tr:
                        m = _re2.match(r'^\s*(\d+)\s+([\d.]+)\s+ms\s+(\S+)', line)
                        if m:
                            raw_hops.append({'hop': int(m.group(1)), 'ip': m.group(3), 'rtt_ms': float(m.group(2))})
                        elif line.strip() == '' and raw_hops:
                            break
            if not raw_hops:
                for binary in ['traceroute', 'tracepath']:
                    chk = subprocess.run(['which', binary], capture_output=True)
                    if chk.returncode == 0:
                        res2 = subprocess.run([binary, '-n', '-m', str(max_hops), '-w', '2', target],
                                              capture_output=True, text=True, timeout=60)
                        for line in res2.stdout.splitlines():
                            m2 = _re2.match(r'^\s*(\d+)\s+([\d.]+)\s+([\d.]+)\s+ms', line)
                            if m2:
                                raw_hops.append({'hop': int(m2.group(1)), 'ip': m2.group(2), 'rtt_ms': float(m2.group(3))})
                        break
        except Exception as _tr_err:
            logger.debug(f"geo-path traceroute failed: {_tr_err}")

        # 2. Filter anomalous hops
        fe = _get_fusion_engine()
        annotated = fe._rtt.filter_hops(raw_hops) if (fe and raw_hops) else raw_hops

        # 3. Geolocate each non-private hop (use cached recon_geolocate logic)
        def _quick_geo(ip_addr):
            """Return {'lat','lon','city','org'} or None for private IPs."""
            try:
                addr = ipaddress.ip_address(ip_addr)
                if addr.is_private or addr.is_loopback or addr.is_link_local:
                    return None
            except ValueError:
                return None
            try:
                url = f'http://ip-api.com/json/{urllib.parse.quote(ip_addr)}?fields=status,lat,lon,city,org,as'
                import ssl as _ssl
                ctx = _ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = _ssl.CERT_NONE
                req = urllib.request.Request(url, headers={'User-Agent': 'rf-scythe/1.0'})
                with urllib.request.urlopen(req, timeout=3, context=ctx) as resp:
                    d = json.loads(resp.read().decode())
                    if d.get('status') == 'success':
                        return {'lat': d.get('lat'), 'lon': d.get('lon'),
                                'city': d.get('city'), 'org': d.get('org'), 'as': d.get('as')}
            except Exception:
                pass
            return None

        path = []
        for hop in annotated:
            ip = hop.get('ip', '')
            if not ip or ip == '*':
                continue
            geo = _quick_geo(ip)
            entry = {
                'hop':     hop['hop'],
                'ip':      ip,
                'rtt_ms':  hop.get('rtt_ms'),
                'anomaly': hop.get('anomaly'),
                'private': geo is None and hop.get('anomaly') == 'private_backbone',
            }
            if geo:
                entry.update({'lat': geo['lat'], 'lon': geo['lon'],
                               'city': geo.get('city'), 'org': geo.get('org')})
                if fe:
                    entry['asn_type'] = fe._asn.classify(geo.get('org', ''), geo.get('as', ''))
            path.append(entry)

        # 4. Geolocate target itself
        target_geo = _quick_geo(target)

        # 5. Confidence based on ratio of geolocated vs total hops
        geo_count  = sum(1 for p in path if p.get('lat') is not None)
        confidence = round(geo_count / max(len(path), 1), 2) if path else 0.0

        _geo_result = {
            'status':         'ok',
            'target':         target,
            'target_geo':     target_geo,
            'path':           path,
            'total_hops':     len(annotated),
            'anomalous_hops': [h['hop'] for h in annotated if h.get('anomaly')],
            'confidence':     confidence,
            'timestamp':      time.time()
        }

        # ── Cache result — TTL scales with confidence ─────────────────────────
        if map_cache is not None and path:
            _ttl = 86400 if confidence >= 0.8 else (21600 if confidence >= 0.5 else 3600)
            map_cache.cache_geo_path(target, _geo_result, ttl_secs=_ttl)

        return jsonify(_geo_result)

    @app.route('/api/timing/analyze', methods=['POST'])
    def timing_analyze():
        """Full fusion analysis: probe + optional traceroute + GeoIP → FusionResult.
        POST body: { "target": "...", "include_traceroute": true, "include_geoip": true, "count": 5 }
        """
        data   = request.get_json() or {}
        target = data.get('target', '').strip()
        if not target:
            return jsonify({'status': 'error', 'message': 'target required'}), 400

        import re as _re3
        if not _re3.match(r'^[a-zA-Z0-9._\-:]+$', target):
            return jsonify({'status': 'error', 'message': 'Invalid target'}), 400

        count           = max(3, min(int(data.get('count', 5)), 10))
        do_traceroute   = data.get('include_traceroute', True)
        do_geoip        = data.get('include_geoip', True)

        fe = _get_fusion_engine()
        if not fe:
            return jsonify({'status': 'error', 'message': 'fusion_engine unavailable'}), 503

        # 1. RTT probe
        rtt_samples = []
        try:
            try:
                _is_v6 = ipaddress.ip_address(target).version == 6
            except ValueError:
                _is_v6 = False
            cmd = ['ping6' if _is_v6 else 'ping', '-c', str(count), '-W', '2', target]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            for m in _re3.finditer(r'time=([\d.]+)\s*ms', res.stdout):
                rtt_samples.append(float(m.group(1)))
            if not rtt_samples:
                m = _re3.search(r'rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)', res.stdout + res.stderr)
                if m:
                    rtt_samples = [float(m.group(1)), float(m.group(2)), float(m.group(3))]
        except Exception as _p_err:
            logger.debug(f"analyze probe failed: {_p_err}")

        # 2. GeoIP
        geoip = None
        if do_geoip:
            try:
                url = f'http://ip-api.com/json/{urllib.parse.quote(target)}?fields=status,lat,lon,city,regionName,country,org,as'
                import ssl as _ssl2
                ctx2 = _ssl2.create_default_context(); ctx2.check_hostname = False; ctx2.verify_mode = _ssl2.CERT_NONE
                req2 = urllib.request.Request(url, headers={'User-Agent': 'rf-scythe/1.0'})
                with urllib.request.urlopen(req2, timeout=5, context=ctx2) as resp2:
                    d2 = json.loads(resp2.read().decode())
                    if d2.get('status') == 'success':
                        geoip = d2
            except Exception:
                pass

        # 3. Traceroute hops
        raw_hops = []
        if do_traceroute:
            try:
                tr_res = app.test_client().post('/api/timing/traceroute',
                    json={'target': target, 'max_hops': 20},
                    content_type='application/json')
                tr_data = json.loads(tr_res.data)
                raw_hops = tr_data.get('hops', [])
            except Exception:
                pass  # traceroute optional

        # 4. Full fusion
        result = fe.analyze(target=target, rtt_samples=rtt_samples, geoip=geoip, hops=raw_hops)
        rd = result.to_dict()
        rd['status'] = 'ok' if rtt_samples else 'no_probe'
        return jsonify(rd)

    @app.route('/api/timing/tdoa', methods=['POST'])
    def timing_tdoa():
        """Time Difference of Arrival multilateration.
        POST body: { "target": "...", "observers": [{"lat":..., "lon":..., "rtt_ms":...}, ...] }
        Requires ≥3 observers for a meaningful 2D fix.
        Uses vectorized least-squares gradient descent (numpy) — eliminates O(n×iters×3)
        per-observer haversine loops by computing all observer distances in one numpy batch.
        """
        import numpy as np

        data = request.get_json() or {}
        target = data.get('target', 'unknown')
        observers = data.get('observers', [])

        valid_obs = [o for o in observers if 'lat' in o and 'lon' in o and 'rtt_ms' in o]
        if len(valid_obs) < 2:
            return jsonify({'status': 'error', 'message': 'At least 2 observers with lat/lon/rtt_ms required'}), 400

        for o in valid_obs:
            o['radius_km'] = o['rtt_ms'] * _RTT_EFFECTIVE_KM_PER_MS

        # Vectorized haversine: (est_lat, est_lon) vs array of (obs_lat, obs_lon)
        R = 6371.0
        obs_lats = np.array([o['lat'] for o in valid_obs], dtype=np.float64)
        obs_lons = np.array([o['lon'] for o in valid_obs], dtype=np.float64)
        radii    = np.array([o['radius_km'] for o in valid_obs], dtype=np.float64)

        def haversine_vec(elat: float, elon: float) -> np.ndarray:
            """All observer distances in one numpy pass."""
            dlat = np.radians(obs_lats - elat)
            dlon = np.radians(obs_lons - elon)
            a = (np.sin(dlat / 2) ** 2
                 + np.cos(np.radians(elat)) * np.cos(np.radians(obs_lats)) * np.sin(dlon / 2) ** 2)
            return R * 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))

        # Initial guess: weighted centroid
        weights = 1.0 / np.maximum(radii, 1.0)
        total_w = weights.sum()
        est_lat = float((obs_lats * weights).sum() / total_w)
        est_lon = float((obs_lons * weights).sum() / total_w)

        # Gradient-descent refinement — inner loop is now fully vectorized
        lr = 0.5
        D_LAT = 0.001  # finite-difference step size (degrees)
        D_LON = 0.001
        for _ in range(50):
            d     = haversine_vec(est_lat, est_lon)
            err   = d - radii                          # (n,)
            dlat  = haversine_vec(est_lat + D_LAT, est_lon) - d
            dlon  = haversine_vec(est_lat, est_lon + D_LON) - d
            grad_lat = float((2 * err * dlat / D_LAT).sum())
            grad_lon = float((2 * err * dlon / D_LON).sum())
            est_lat = max(-90.0,  min(90.0,  est_lat - lr * grad_lat * 1e-6))
            est_lon = max(-180.0, min(180.0, est_lon - lr * grad_lon * 1e-6))

        residual_km = float(np.abs(haversine_vec(est_lat, est_lon) - radii).mean())
        confidence  = max(0.0, min(1.0, 1.0 - residual_km / 500.0))

        return jsonify({
            'status': 'ok',
            'target': target,
            'estimated_lat': round(est_lat, 4),
            'estimated_lon': round(est_lon, 4),
            'residual_error_km': round(residual_km, 1),
            'confidence': round(confidence, 3),
            'observer_count': len(valid_obs),
            'distance_circles': [
                {'observer_lat': o['lat'], 'observer_lon': o['lon'],
                 'radius_km': round(o['radius_km'], 1), 'rtt_ms': o['rtt_ms']}
                for o in valid_obs
            ],
            'timestamp': time.time()
        })

    # ========================================================================
    # API ROUTES - NDPI
    # ========================================================================

    @app.route('/api/ndpi/analyze', methods=['POST', 'GET'])
    def ndpi_analyze():
        """Run NDPI analysis"""
        try:
            if request.method == 'POST':
                data = request.get_json() or {}
                network_interface = data.get('interface', 'eth0')
                duration = int(data.get('duration', 10))
            else:
                network_interface = request.args.get('interface', 'eth0')
                duration = int(request.args.get('duration', 10))

            results = ndpi_analyzer.analyze_interface(network_interface, duration)
            return jsonify(results)
        except Exception as e:
            logger.error(f"Error running NDPI analysis: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/ndpi/status', methods=['GET'])
    def ndpi_status():
        """Get NDPI analyzer status"""
        return jsonify({
            'available': ndpi_analyzer.check_ndpi_available(),
            'analyzing': ndpi_analyzer.analyzing,
            'cached_results': bool(ndpi_analyzer.analysis_results)
        })

    @app.route('/api/ndpi/results', methods=['GET'])
    def ndpi_results():
        """Get cached NDPI results"""
        return jsonify(ndpi_analyzer.analysis_results or {'status': 'no_results'})

    # ========================================================================
    # API ROUTES - NETWORK CAPTURE
    # ========================================================================

    @app.route('/api/network/capture-report', methods=['GET', 'POST'])
    def network_capture_report():
        """Generate a network capture report"""
        return jsonify({
            'timestamp': datetime.now().isoformat(),
            'summary': 'Network traffic analysis complete. Active connections detected across infrastructure.',
            'geminiConfidence': random.randint(75, 95),
            'total_packets': random.randint(5000, 15000),
            'violations': [
                {'type': 'Unusual Traffic Pattern', 'severity': 'low', 'source': f'10.0.0.{random.randint(1, 254)}'},
                {'type': 'Port Scan Detected', 'severity': 'medium', 'source': f'192.168.1.{random.randint(1, 254)}'}
            ] if random.random() > 0.5 else [],
            'rf_correlation': {
                'signals_detected': random.randint(5, 15),
                'frequency_range': '2.4GHz - 5.8GHz',
                'interference_level': random.choice(['Low', 'Medium', 'Low'])
            },
            'nmap_available': nmap_scanner.check_nmap_available(),
            'ndpi_available': ndpi_analyzer.check_ndpi_available()
        })

    # ========================================================================
    # API ROUTES - AIS VESSEL TRACKING
    # ========================================================================

    @app.route('/api/ais/vessels', methods=['GET'])
    def ais_get_vessels():
        """Get all AIS vessel positions"""
        try:
            # Support server-side pagination for large live sets
            vessels = ais_tracker.get_all_vessels()

            # Pagination params: page/per_page or offset/limit
            page = request.args.get('page', type=int)
            per_page = request.args.get('per_page', type=int)
            limit = request.args.get('limit', type=int)
            offset = request.args.get('offset', default=0, type=int)

            # Determine page size
            if per_page and per_page > 0:
                page_size = min(per_page, 1000)
            elif limit and limit > 0:
                page_size = min(limit, 1000)
            else:
                page_size = 100  # default

            total_vessels = len(vessels)

            if page and page > 0:
                offset = (page - 1) * page_size

            # Clamp offset
            if offset < 0:
                offset = 0

            vessels_page = vessels[offset: offset + page_size]

            pagination = {
                'page': (offset // page_size) + 1 if page_size > 0 else 1,
                'per_page': page_size,
                'offset': offset,
                'returned': len(vessels_page),
                'total': total_vessels,
                'total_pages': (total_vessels + page_size - 1) // page_size if page_size > 0 else 1
            }

            return jsonify({
                'status': 'ok',
                'vessel_count': len(vessels_page),
                'vessels': vessels_page,
                'csv_loaded': ais_tracker.csv_loaded,
                'timestamp': time.time(),
                'pagination': pagination
            })
        except Exception as e:
            logger.error(f"Error getting AIS vessels: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/ais/vessel/<mmsi>', methods=['GET'])
    def ais_get_vessel(mmsi):
        """Get a specific vessel by MMSI"""
        try:
            vessel = ais_tracker.get_vessel(mmsi)
            if vessel:
                return jsonify({
                    'status': 'ok',
                    'vessel': vessel,
                    'timestamp': time.time()
                })
            else:
                return jsonify({'status': 'not_found', 'message': f'Vessel {mmsi} not found'}), 404
        except Exception as e:
            logger.error(f"Error getting vessel {mmsi}: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/ais/vessel/<mmsi>/history', methods=['GET'])
    def ais_get_vessel_history(mmsi):
        """Get historical positions for a vessel"""
        try:
            limit = int(request.args.get('limit', 100))
            history = ais_tracker.get_vessel_history(mmsi, limit)
            return jsonify({
                'status': 'ok',
                'mmsi': mmsi,
                'history_count': len(history),
                'history': history,
                'timestamp': time.time()
            })
        except Exception as e:
            logger.error(f"Error getting vessel history {mmsi}: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/ais/advance', methods=['POST', 'GET'])
    def ais_advance_playback():
        """Advance all vessels to next position (simulation playback)"""
        try:
            result = ais_tracker.advance_playback()
            return jsonify({
                'status': 'ok',
                **result
            })
        except Exception as e:
            logger.error(f"Error advancing AIS playback: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/ais/area', methods=['GET'])
    def ais_vessels_in_area():
        """Get vessels within a geographic bounding box"""
        try:
            min_lat = float(request.args.get('min_lat', -90))
            max_lat = float(request.args.get('max_lat', 90))
            min_lon = float(request.args.get('min_lon', -180))
            max_lon = float(request.args.get('max_lon', 180))

            vessels = ais_tracker.get_vessels_in_area(min_lat, max_lat, min_lon, max_lon)
            return jsonify({
                'status': 'ok',
                'vessel_count': len(vessels),
                'vessels': vessels,
                'bounding_box': {
                    'min_lat': min_lat, 'max_lat': max_lat,
                    'min_lon': min_lon, 'max_lon': max_lon
                },
                'timestamp': time.time()
            })
        except Exception as e:
            logger.error(f"Error getting vessels in area: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/ais/rf-correlation', methods=['GET'])
    def ais_rf_correlation():
        """Correlate AIS vessels with RF emissions"""
        try:
            freq_min = float(request.args.get('freq_min', 156.0))
            freq_max = float(request.args.get('freq_max', 162.5))

            correlations = ais_tracker.correlate_with_rf(freq_min, freq_max)
            return jsonify({
                'status': 'ok',
                'correlation_count': len(correlations),
                'correlations': correlations,
                'frequency_band': f'{freq_min}-{freq_max} MHz',
                'timestamp': time.time()
            })
        except Exception as e:
            logger.error(f"Error correlating AIS with RF: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/ais/status', methods=['GET'])
    def ais_status():
        """Get AIS tracker status"""
        return jsonify({
            'status': 'ok',
            'csv_loaded': ais_tracker.csv_loaded,
            'vessel_count': len(ais_tracker.vessels),
            'total_records': len(ais_tracker.all_records),
            'unique_vessels': len(ais_tracker.vessel_history),
            'timestamp': time.time()
        })

    @app.route('/api/ais/vessel-types', methods=['GET'])
    def ais_get_vessel_types():
        """Get list of all vessel types currently tracked"""
        try:
            vessel_types = ais_tracker.get_vessel_types()
            return jsonify({
                'status': 'ok',
                'vessel_types': vessel_types,
                'count': len(vessel_types),
                'timestamp': time.time()
            })
        except Exception as e:
            logger.error(f"Error getting vessel types: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/ais/vessels/filter', methods=['GET'])
    def ais_get_vessels_filtered():
        """Get vessels filtered by type and/or geographic area"""
        try:
            # Get filter parameters
            vessel_types = request.args.getlist('type')  # Multiple types allowed
            min_lat = request.args.get('min_lat', type=float)
            max_lat = request.args.get('max_lat', type=float)
            min_lon = request.args.get('min_lon', type=float)
            max_lon = request.args.get('max_lon', type=float)

            # Get filtered vessels
            vessels = ais_tracker.get_vessels_filtered(
                vessel_types=vessel_types if vessel_types else None,
                min_lat=min_lat, max_lat=max_lat,
                min_lon=min_lon, max_lon=max_lon
            )

            return jsonify({
                'status': 'ok',
                'vessel_count': len(vessels),
                'vessels': vessels,
                'filters': {
                    'vessel_types': vessel_types,
                    'min_lat': min_lat,
                    'max_lat': max_lat,
                    'min_lon': min_lon,
                    'max_lon': max_lon
                },
                'timestamp': time.time()
            })
        except Exception as e:
            logger.error(f"Error getting filtered vessels: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/ais/search', methods=['GET'])
    def ais_search_records():
        """Search through all AIS records"""
        try:
            # Get search parameters
            query = request.args.get('q', '').strip()
            vessel_type = request.args.get('type', 'all')
            min_lat = request.args.get('min_lat', type=float)
            max_lat = request.args.get('max_lat', type=float)
            min_lon = request.args.get('min_lon', type=float)
            max_lon = request.args.get('max_lon', type=float)

            # Pagination: support page/per_page or offset/limit
            page = request.args.get('page', type=int)
            per_page = request.args.get('per_page', type=int)
            limit = request.args.get('limit', type=int)

            if per_page and per_page > 0:
                per_page = min(per_page, 1000)
            if limit is None:
                # default page size
                limit = 100
            else:
                limit = min(limit, 1000)

            if page and page > 0:
                # use page/per_page if provided, else page*limit
                page_size = per_page or limit
                offset = (page - 1) * page_size
                page_size = min(page_size, 1000)
                records_slice, total_matches = ais_tracker.search_records(
                    query=query if query else None,
                    vessel_type=vessel_type if vessel_type != 'all' else None,
                    min_lat=min_lat, max_lat=max_lat,
                    min_lon=min_lon, max_lon=max_lon,
                    limit=page_size, offset=offset, return_total=True
                )
                current_page = page
                per_page_used = page_size
            else:
                # fallback to offset/limit style
                offset = int(request.args.get('offset', 0) or 0)
                records_slice, total_matches = ais_tracker.search_records(
                    query=query if query else None,
                    vessel_type=vessel_type if vessel_type != 'all' else None,
                    min_lat=min_lat, max_lat=max_lat,
                    min_lon=min_lon, max_lon=max_lon,
                    limit=limit, offset=offset, return_total=True
                )
                current_page = (offset // limit) + 1 if limit > 0 else 1
                per_page_used = limit

            # Get unique vessels from results
            unique_vessels = ais_tracker.get_unique_vessels_from_records(records_slice)

            return jsonify({
                'status': 'ok',
                'total_records': len(ais_tracker.all_records),
                'search_results': len(records_slice),
                'total_matches': total_matches,
                'unique_vessels': len(unique_vessels),
                'records': records_slice,
                'vessels': unique_vessels,
                'pagination': {
                    'page': current_page,
                    'per_page': per_page_used,
                    'offset': offset,
                    'total_matches': total_matches,
                    'total_pages': (total_matches + per_page_used - 1) // per_page_used if per_page_used > 0 else 1
                },
                'search_params': {
                    'query': query,
                    'vessel_type': vessel_type,
                    'min_lat': min_lat,
                    'max_lat': max_lat,
                    'min_lon': min_lon,
                    'max_lon': max_lon,
                    'limit': limit,
                    'offset': offset
                },
                'timestamp': time.time()
            })
        except Exception as e:
            logger.error(f"Error searching AIS records: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/ais/search/stats', methods=['GET'])
    def ais_search_stats():
        """Get statistics about AIS records for search interface"""
        try:
            total_records = len(ais_tracker.all_records)

            # Count by vessel type
            type_counts = {}
            for record in ais_tracker.all_records:
                vessel_type = record.get('VesselType', '')
                if vessel_type:
                    type_counts[vessel_type] = type_counts.get(vessel_type, 0) + 1

            # Get geographic bounds
            lats = [float(r.get('LAT', 0)) for r in ais_tracker.all_records if r.get('LAT')]
            lons = [float(r.get('LON', 0)) for r in ais_tracker.all_records if r.get('LON')]

            bounds = None
            if lats and lons:
                bounds = {
                    'min_lat': min(lats),
                    'max_lat': max(lats),
                    'min_lon': min(lons),
                    'max_lon': max(lons)
                }

            return jsonify({
                'status': 'ok',
                'total_records': total_records,
                'unique_mmsi': len(set(r.get('MMSI', '') for r in ais_tracker.all_records if r.get('MMSI'))),
                'vessel_type_counts': type_counts,
                'geographic_bounds': bounds,
                'timestamp': time.time()
            })
        except Exception as e:
            logger.error(f"Error getting AIS search stats: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    # ========================================================================
    # API ROUTES - AUTO RECONNAISSANCE
    # ========================================================================

    def _unwrap_room_value(v: Dict[str, Any]) -> Tuple[str, Any]:
        """Helper to unwrap nested OperatorSessionManager room values."""
        # OperatorSessionManager stores: {"id":..., "type":..., "data":...}
        if isinstance(v, dict) and "data" in v and "type" in v and "id" in v:
            return v.get("type", ""), v.get("data") or {}
        # fallback: treat as already-unwrapped or legacy format
        return (v.get("entity_type") or v.get("type") or ""), v

    def _rehydrate_global_room():
        """Syncs all entities (Recon & Sensors) from persisted Global room to memory."""
        if not OPERATOR_MANAGER_AVAILABLE:
            return

        try:
            manager = get_session_manager()
            global_room = manager.get_room_by_name("Global")
            if not global_room:
                return

            persisted = manager.room_entities.get(global_room.room_id, {})

            recon_count = 0

            for k, v in persisted.items():
                etype, payload = _unwrap_room_value(v)

                # Recon Entities + PCAP Hosts + Nmap Targets → trackable on globe
                if etype in ("RECON_ENTITY", "PCAP_HOST", "NMAP_TARGET"):
                    entity_id = payload.get("entity_id") or k
                    if entity_id:
                        recon_system.entities[entity_id] = payload
                        recon_system._dirty_entities.add(entity_id)
                        recon_count += 1

                # Sensors
                elif etype == "SENSOR":
                    node_id = payload.get('node_id') or k
                    if node_id:
                        sensor_store[node_id] = payload

                # Sensor Assignments
                elif etype == "SENSOR_ASSIGNMENT":
                    edge_id = payload.get('edge_id') or k
                    if edge_id:
                        sensor_assignments[edge_id] = payload

            if recon_count > 0:
                try:
                    recon_system._spatial_index.mark_dirty()
                except Exception:
                    pass

        except Exception as e:
            logger.warning(f"Rehydration failed: {e}")

    # Entity types that are "trackable" on the globe / Recon panel
    RECON_TRACKABLE_TYPES = {"RECON_ENTITY", "PCAP_HOST", "NMAP_TARGET"}

    @app.route('/api/recon/entities', methods=['GET'])
    def get_recon_entities():
        """Get all tracked entities (RECON_ENTITY + PCAP_HOST + NMAP_TARGET)"""
        try:
            entities = _trackable_recon_entities_snapshot()

            return jsonify({
                'status': 'ok',
                'entity_count': len(entities),
                'entities': entities,
                'timestamp': time.time()
            })
        except Exception as e:
            logger.error(f"Error getting recon entities: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/recon/entities/grouped', methods=['GET'])
    def get_recon_entities_grouped():
        """Return entities pre-grouped by type+region for the panel UI.

        Query params:
          group_id   — if provided, return full paginated entity list for one group
          page       — page number for group expansion (default 1)
          limit      — entities per page within a group (default 100)
          q          — search/filter string (fuzzy match on name/entity_id)
        """
        try:
            import math

            def _geo_region(lat, lon):
                """Map lat/lon to a readable continent/ocean region label."""
                if lat is None or lon is None:
                    return 'Unknown'
                if -60 < lat < 15 and -85 < lon < -30:
                    return 'South America'
                if 15 < lat < 75 and -130 < lon < -55:
                    return 'North America'
                if 35 < lat < 72 and -15 < lon < 45:
                    return 'Europe'
                if -40 < lat < 38 and -20 < lon < 55:
                    return 'Africa'
                if 5 < lat < 55 and 55 < lon < 150:
                    return 'Asia'
                if -50 < lat < 5 and 90 < lon < 180:
                    return 'Southeast Asia / Oceania'
                if lat < -50:
                    return 'Antarctica / Southern Ocean'
                if lat > 60:
                    return 'Arctic'
                return 'Pacific / Other'

            def _entity_group_key(e):
                eid = e.get('entity_id', '')
                if eid.startswith('PCAP'):    return 'PCAP'
                if eid.startswith('NMAP'):    return 'NMAP'
                if eid.startswith('AIS'):     return 'AIS'
                if eid.startswith('android'): return 'Android'
                name = e.get('name', '')
                if 'STARLINK' in name.upper(): return 'Starlink'
                if 'ENTITY' in eid:           return 'Promoted Entities'
                return 'Other'

            def _threat_color(level):
                return {'HIGH': '#ff4d4d', 'CRITICAL': '#ff0000',
                        'MEDIUM': '#ff9900', 'LOW': '#4a9eff',
                        'NONE': '#4ade80'}.get(str(level).upper(), '#888')

            # ── load all entities ─────────────────────────────────────────
            entities = _trackable_recon_entities_snapshot()

            q = (request.args.get('q') or '').lower().strip()
            if q:
                entities = [e for e in entities
                            if q in (e.get('entity_id') or '').lower()
                            or q in (e.get('name') or '').lower()]

            # ── single group expansion ────────────────────────────────────
            group_id = request.args.get('group_id')
            if group_id:
                page  = max(1, int(request.args.get('page', 1)))
                limit = min(2000, int(request.args.get('limit', 1000)))
                grp_type, _, grp_region = group_id.partition('::')

                filtered = []
                for e in entities:
                    if _entity_group_key(e) != grp_type:
                        continue
                    if grp_region:
                        loc = e.get('location') or {}
                        if _geo_region(loc.get('lat'), loc.get('lon')) != grp_region:
                            continue
                    filtered.append(e)

                total  = len(filtered)
                # Prefer explicit ?offset= (used by frontend cursor-based load-more)
                # so preview page (20 items) doesn't misalign with page*1000 arithmetic.
                if 'offset' in request.args:
                    offset = max(0, int(request.args.get('offset', 0)))
                else:
                    offset = (page - 1) * limit
                page_entities = filtered[offset:offset + limit]

                return jsonify({
                    'status':      'ok',
                    'group_id':    group_id,
                    'total':       total,
                    'page':        page,
                    'limit':       limit,
                    'has_more':    offset + limit < total,
                    'entities':    page_entities,
                })

            # ── build group summaries ─────────────────────────────────────
            from collections import defaultdict, Counter as _Counter

            # Two-level grouping: type → region
            groups: dict = defaultdict(lambda: defaultdict(list))
            for e in entities:
                gtype  = _entity_group_key(e)
                loc    = e.get('location') or {}
                region = _geo_region(loc.get('lat'), loc.get('lon'))
                groups[gtype][region].append(e)

            result_groups = []
            type_order = ['PCAP', 'NMAP', 'Starlink', 'Promoted Entities',
                          'AIS', 'Android', 'Other']
            for gtype in type_order + [k for k in groups if k not in type_order]:
                if gtype not in groups:
                    continue
                region_groups = groups[gtype]

                sub_groups = []
                for region, ents in sorted(region_groups.items(),
                                           key=lambda x: -len(x[1])):
                    threat_counts = _Counter(
                        (e.get('threat_level') or 'UNKNOWN').upper() for e in ents
                    )
                    disp_counts = _Counter(
                        (e.get('disposition') or 'UNKNOWN').upper() for e in ents
                    )
                    # First 20 as preview
                    preview = ents[:20]
                    sub_groups.append({
                        'group_id':       f'{gtype}::{region}',
                        'type':           gtype,
                        'region':         region,
                        'count':          len(ents),
                        'threat_counts':  dict(threat_counts),
                        'disp_counts':    dict(disp_counts),
                        'preview':        preview,
                        'centroid': {
                            'lat': sum(
                                (e.get('location') or {}).get('lat', 0) for e in ents
                            ) / max(len(ents), 1),
                            'lon': sum(
                                (e.get('location') or {}).get('lon', 0) for e in ents
                            ) / max(len(ents), 1),
                        },
                    })

                type_total = sum(len(e) for e in region_groups.values())
                result_groups.append({
                    'type':       gtype,
                    'total':      type_total,
                    'sub_groups': sub_groups,
                })

            return jsonify({
                'status':        'ok',
                'total_entities': len(entities),
                'group_count':   sum(
                    len(g['sub_groups']) for g in result_groups
                ),
                'groups':        result_groups,
                'timestamp':     time.time(),
            })
        except Exception as e:
            logger.error(f'[recon/grouped] {e}')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/recon/entities/stream', methods=['GET'])
    def stream_recon_entities():
        """SSE endpoint — pushes entity upsert events as they arrive.

        The client sends Last-Event-ID for resumable streams.
        Each event: data: <JSON entity object>\\n\\n
        """
        import queue as _queue

        def _generate():
            q = _queue.Queue(maxsize=200)
            client_id = str(time.time())
            if not hasattr(stream_recon_entities, '_subscribers'):
                stream_recon_entities._subscribers = {}
            stream_recon_entities._subscribers[client_id] = q

            try:
                yield 'data: {"type":"connected","client_id":"' + client_id + '"}\n\n'
                while True:
                    try:
                        event = q.get(timeout=25)
                        yield f'data: {json.dumps(event, default=str)}\n\n'
                    except _queue.Empty:
                        yield ': keepalive\n\n'
            finally:
                stream_recon_entities._subscribers.pop(client_id, None)

        resp = app.response_class(
            _generate(),
            mimetype='text/event-stream',
            headers={
                'Cache-Control':    'no-cache',
                'X-Accel-Buffering': 'no',
                'Connection':        'keep-alive',
            },
        )
        return resp

    @app.route('/api/config/streams', methods=['GET'])
    def config_streams():
        """Return configured stream endpoint URLs + live health for UI quick-connect buttons."""
        from flask import request as _req
        from urllib.parse import urlparse
        relay_url = app.config.get('STREAM_RELAY_URL', 'ws://localhost:8765/ws')
        mcp_url   = app.config.get('MCP_WS_URL',       'ws://localhost:8766/ws')
        takml_url = app.config.get('TAKML_URL',         'http://localhost:8234')
        eve_cfg = _eve_stream_config()
        voxel_url = 'ws://localhost:9001/stream'

        # Proxy normalization
        path_prefix = _req.headers.get('X-Forwarded-Prefix', '').rstrip('/')
        if path_prefix:
            host = _req.host
            scheme = _req.headers.get('X-Forwarded-Proto', 'https' if _req.is_secure else 'http')
            ws_proto = 'wss' if scheme == 'https' else 'ws'

            def _prox(url):
                u = urlparse(url)
                if not u.port: return url
                path = (u.path or '').strip('/')
                suffix = '' if not path or path == 'ws' else f'/{path}'
                return f"{ws_proto}://{host}/proxy/{u.port}/ws{suffix}"

            relay_url = _prox(relay_url)
            mcp_url   = _prox(mcp_url)
            eve_cfg['eve_stream_ws'] = _prox(eve_cfg['eve_stream_ws'])
            voxel_url = _prox(voxel_url)

        return jsonify({
            'stream_relay': relay_url,
            'mcp_ws':       mcp_url,
            'takml':        takml_url,
            'eve_stream_ws': eve_cfg['eve_stream_ws'],
            'eve_stream_http': eve_cfg['eve_stream_http'],
            'voxel_stream': voxel_url,
            'health': {
                'stream_relay': _probe_stream_endpoint(relay_url),
                'mcp_ws':       _probe_stream_endpoint(mcp_url),
                'takml':        _probe_stream_endpoint(takml_url),
                'eve_stream_ws': _probe_stream_endpoint(eve_cfg['eve_stream_ws']),
                'eve_stream_http': _probe_stream_endpoint(eve_cfg['eve_stream_http']),
                'voxel_stream': _probe_stream_endpoint(voxel_url),
            },
        })

    @app.route('/api/semantic-repair/stats', methods=['GET'])
    def semantic_repair_stats():
        """Return semantic edge repair stats and ontology evolution candidates."""
        try:
            from semantic_edge_repair import SemanticEdgeRepair
            repair = SemanticEdgeRepair.get_instance()
            stats = repair.get_repair_stats()
            candidates = repair.promote_candidates(
                min_count=int(request.args.get('min_count', 3)),
                min_score=float(request.args.get('min_score', 0.70)),
            )
            return jsonify({'stats': stats, 'promotion_candidates': candidates})
        except Exception as exc:
            return jsonify({'error': str(exc)}), 503

    @app.route('/api/embedding/entity', methods=['POST'])
    def embed_recon_entity():
        """Embed a single entity into semantic memory (called from Recon panel 🧠 button)."""
        try:
            data    = request.get_json() or {}
            eid     = data.get('entity_id', '')
            event   = data.get('event', data)
            if not eid:
                return jsonify({'status': 'error', 'message': 'entity_id required'}), 400
            embed_eng = globals().get('embedding_engine')
            if embed_eng is None:
                return jsonify({'status': 'error', 'message': 'embedding_engine not initialized'}), 503
            from embedding_engine import EmbeddingEngine
            desc    = EmbeddingEngine.build_entity_description({**event, 'entity_id': eid})
            vec_idx = embed_eng.add_entity(eid, desc)
            embed_eng.save_index()
            return jsonify({'status': 'ok', 'entity_id': eid, 'vec_idx': vec_idx,
                            'total_vectors': embed_eng.stats()['total_vectors']})
        except Exception as e:
            logger.error(f'[embedding/entity] {e}')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/recon/probe', methods=['GET'])
    def recon_probe():
        """Fast single-packet ICMP liveness probe for a recon entity IP.
        Results are cached server-side for 30 s to prevent hover-spam.
        GET /api/recon/probe?ip=187.108.252.63
        Returns: {alive, rtt_ms, cached}
        """
        ip = request.args.get('ip', '').strip()
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            return jsonify({'status': 'error', 'message': 'Invalid IP'}), 400

        cache = globals().setdefault('_recon_probe_cache', {})
        now   = time.time()
        if ip in cache and now - cache[ip]['ts'] < 30:
            c = cache[ip]
            return jsonify({'status': 'ok', 'ip': ip, 'alive': c['alive'],
                            'rtt_ms': c['rtt_ms'], 'cached': True})

        alive, rtt_ms = False, None
        try:
            try:
                _v6 = ipaddress.ip_address(ip).version == 6
            except ValueError:
                _v6 = False
            res = subprocess.run(['ping6' if _v6 else 'ping', '-c', '1', '-W', '1', ip],
                                 capture_output=True, text=True, timeout=3)
            alive = res.returncode == 0
            for line in res.stdout.splitlines():
                m = _re.search(r'time=([\d.]+)\s*ms', line)
                if m:
                    rtt_ms = float(m.group(1))
                    break
        except Exception:
            pass

        cache[ip] = {'alive': alive, 'rtt_ms': rtt_ms, 'ts': now}
        # Evict stale entries to prevent unbounded growth
        if len(cache) > 2000:
            cutoff = now - 30
            for k in [k for k, v in cache.items() if v['ts'] < cutoff]:
                del cache[k]
        return jsonify({'status': 'ok', 'ip': ip, 'alive': alive,
                        'rtt_ms': rtt_ms, 'cached': False})

    @app.route('/api/recon/entity/<entity_id>', methods=['GET'])
    def get_recon_entity(entity_id):
        """Get a specific entity by ID"""
        try:
            entity = recon_system.get_entity(entity_id)
            if entity:
                return jsonify({'status': 'ok', 'entity': entity})
            return jsonify({'status': 'error', 'message': f'Entity {entity_id} not found'}), 404
        except Exception as e:
            logger.error(f"Error getting entity {entity_id}: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/sensors', methods=['GET'])
    def get_sensors():
        """Get all registered sensors."""
        try:
            if not sensor_registry_instance:
                 return jsonify({'status': 'error', 'message': 'Sensor registry not initialized'}), 503

            sensors = []
            if hasattr(sensor_registry_instance, 'get_all_sensors'):
                # Assuming returns list of dicts
                sensors = sensor_registry_instance.get_all_sensors()
            elif hasattr(sensor_registry_instance, 'sensors'):
                 # Fallback to simple values list
                 sensors = list(sensor_registry_instance.sensors.values())
                 # Ensure json serializable
                 sensors = [s if isinstance(s, dict) else s.__dict__ for s in sensors]

            return jsonify({'status': 'ok', 'sensors': sensors})
        except Exception as e:
            logger.error(f"Error getting sensors: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500



    auto_mobile_sensor_assignments: Set[str] = set()

    def _scythe_write_context(data=None, source="rf_ip_correlation", default_operator_id="SYSTEM:RF_CORRELATION"):
        from writebus import WriteContext

        payload = data or {}
        headers = request.headers if has_request_context() else {}
        operator_id = headers.get("X-Operator-Id") or payload.get("operator_id") or default_operator_id
        return WriteContext(
            room_name="Global",
            mission_id=payload.get("mission_id") or payload.get("missionId"),
            operator_id=operator_id,
            session_token=headers.get("X-Session-Token") or payload.get("session_token"),
            request_id=headers.get("X-Request-Id") or payload.get("request_id"),
            source=source,
        )

    def _rf_ip_write_context(data=None, source="rf_ip_correlation"):
        return _scythe_write_context(data=data, source=source, default_operator_id="SYSTEM:RF_CORRELATION")

    def _auto_promote_mobile_sensor(entity, payload, ctx):
        if not sensor_registry_instance:
            return None

        entity_id = str(entity.get("entity_id") or "")
        if not entity_id:
            return None

        platform = str(
            entity.get("platform")
            or payload.get("platform")
            or payload.get("source")
            or entity.get("source")
            or ""
        ).lower()
        if not (entity_id.startswith("android-") or "android" in platform):
            return None

        location = entity.get("location") or {}
        lat = location.get("lat")
        lon = location.get("lon")
        alt = location.get("altitude_m")
        sensor_payload = {
            "sensor_id": entity_id,
            "name": entity.get("callsign") or entity.get("name") or entity_id,
            "label": entity.get("callsign") or entity_id,
            "role": "mobile_sensor",
            "tags": ["android", "mobile", "recon-auto", "rf-ip-correlation"],
            "status": {"state": "ONLINE", "last_seen": time.time()},
            "mission_id": ctx.mission_id,
            "platform": "android",
            "location": {
                "lat": lat,
                "lon": lon,
                "alt_m": alt if alt is not None else 0.0,
            } if lat is not None and lon is not None else {},
            "notes": "Auto-promoted from Android recon entity",
            "metadata": {
                "source": payload.get("source") or entity.get("source"),
                "recon_entity_id": entity_id,
                "auto_promoted": True,
            },
        }
        result = sensor_registry_instance.upsert_sensor(
            sensor_payload,
            ctx=ctx,
            persist_to_room=True,
            audit=True,
        )

        if entity_id not in auto_mobile_sensor_assignments:
            sensor_registry_instance.assign_sensor(
                sensor_id=entity_id,
                recon_entity_id=entity_id,
                ctx=ctx,
                mode="observed",
                metadata={"source": "android_auto_promote"},
                persist_to_room=True,
                audit=True,
            )
            auto_mobile_sensor_assignments.add(entity_id)
        return result

    def _emit_rf_ip_binding(binding, rf_obs, net_obs, ctx):
        import writebus
        from writebus import GraphOp

        recon_node_id = net_obs.entity_id if str(net_obs.entity_id).startswith("recon:") else f"recon:{net_obs.entity_id}"
        sensor_node_id = rf_obs.sensor_id if str(rf_obs.sensor_id).startswith("sensor:") else f"sensor:{rf_obs.sensor_id}"
        graph_ops = [
            GraphOp(
                event_type="NODE_UPDATE",
                entity_id=rf_obs.rf_node_id,
                entity_data={
                    "id": rf_obs.rf_node_id,
                    "kind": "rf_emitter",
                    "position": [rf_obs.lat or 0.0, rf_obs.lon or 0.0, rf_obs.alt_m or 0.0],
                    "labels": {
                        "missionId": rf_obs.mission_id or ctx.mission_id,
                        "modulation": rf_obs.modulation,
                    },
                    "metadata": {
                        "observed": True,
                        "frequency_mhz": rf_obs.frequency_mhz,
                        "bandwidth_mhz": rf_obs.bandwidth_mhz,
                        "power_dbm": rf_obs.power_dbm,
                        "sensor_id": rf_obs.sensor_id,
                        "observation_id": rf_obs.observation_id,
                        "source": "rf_ip_correlation",
                    },
                },
            ),
            GraphOp(
                event_type="EDGE_UPDATE",
                entity_id=binding.binding_id,
                entity_data={
                    "id": binding.binding_id,
                    "kind": "RF_TO_IP_BINDING",
                    "nodes": [sensor_node_id, rf_obs.rf_node_id, recon_node_id],
                    "weight": binding.confidence,
                    "labels": {
                        "missionId": rf_obs.mission_id or net_obs.mission_id or ctx.mission_id,
                        "evidence": "OBSERVED",
                    },
                    "metadata": {
                        "observed": True,
                        "confidence": binding.confidence,
                        "binding": binding.to_dict(),
                        "rf_observation": rf_obs.to_dict(),
                        "network_observation": net_obs.to_dict(),
                    },
                    "timestamp": binding.created_at,
                },
            ),
        ]
        return writebus.bus().commit(
            entity_id=binding.binding_id,
            entity_type="RF_TO_IP_BINDING",
            entity_data={
                "binding": binding.to_dict(),
                "rf_observation": rf_obs.to_dict(),
                "network_observation": net_obs.to_dict(),
            },
            graph_ops=graph_ops,
            ctx=ctx,
            persist=True,
            audit=True,
        )

    def _emit_observed_rf_node(rf_obs, ctx, source="rf_observation"):
        import writebus
        from writebus import GraphOp

        metadata = dict(rf_obs.metadata or {})
        local_position = metadata.get("local_position_m") or {}
        x = local_position.get("x")
        y = local_position.get("y")
        z = local_position.get("z")
        has_geo = rf_obs.lat is not None and rf_obs.lon is not None

        if has_geo:
            position = [rf_obs.lat, rf_obs.lon, rf_obs.alt_m or 0.0]
            labels = {"spatial_frame": "geospatial"}
        else:
            position = [x or 0.0, y or 0.0, z or 0.0]
            labels = {"spatial_frame": metadata.get("local_frame") or "local_scene"}

        labels.update(
            {
                "missionId": rf_obs.mission_id or ctx.mission_id,
                "modulation": rf_obs.modulation,
                "evidence": "SYNTHETIC" if metadata.get("synthetic") else "OBSERVED",
            }
        )
        graph_op = GraphOp(
            event_type="NODE_UPDATE",
            entity_id=rf_obs.rf_node_id,
            entity_data={
                "id": rf_obs.rf_node_id,
                "kind": "rf_emitter",
                "position": position,
                "labels": labels,
                "metadata": {
                    "observed": True,
                    "synthetic": bool(metadata.get("synthetic")),
                    "frequency_mhz": rf_obs.frequency_mhz,
                    "bandwidth_mhz": rf_obs.bandwidth_mhz,
                    "power_dbm": rf_obs.power_dbm,
                    "sensor_id": rf_obs.sensor_id,
                    "observation_id": rf_obs.observation_id,
                    "source": source,
                    **metadata,
                },
            },
        )
        return writebus.bus().commit(
            entity_id=rf_obs.rf_node_id,
            entity_type="RF_OBSERVATION",
            entity_data={"rf_observation": rf_obs.to_dict()},
            graph_ops=[graph_op],
            ctx=ctx,
            persist=True,
            audit=True,
        )

    def _write_rfuav_event_to_questdb(event):
        if not isinstance(event, dict) or not event:
            return False
        try:
            from questdb_writer import get_writer
            writer = get_writer()
            return bool(writer.write_rfuav_detection(event))
        except Exception as e:
            logger.debug(f"RFUAV QuestDB write unavailable: {e}")
            return False

    def _observe_rfuav_observation(observation, ctx):
        if not rf_ip_correlation_engine:
            return None, [], []

        rf_obs, bindings = rf_ip_correlation_engine.observe_rf(observation or {})
        emitted_bindings = []
        for binding in bindings:
            net_obs = rf_ip_correlation_engine.get_network_observation(binding.network_observation_id)
            if not net_obs:
                continue
            emit_result = _emit_rf_ip_binding(binding, rf_obs, net_obs, ctx)
            emitted_bindings.append({
                'binding': binding.to_dict(),
                'writebus': {
                    'ok': bool(getattr(emit_result, 'ok', False)),
                    'errors': list(getattr(emit_result, 'errors', []) or []),
                },
            })
        return rf_obs, bindings, emitted_bindings

    def _observe_synthetic_rf_observation(observation, ctx, source="nis_sigint"):
        if rf_ip_correlation_engine:
            rf_obs, bindings = rf_ip_correlation_engine.observe_rf(observation or {})
        else:
            from rf_ip_correlation_engine import RFIPCorrelationEngine

            rf_obs = RFIPCorrelationEngine(max_history=1)._normalize_rf(observation or {})
            bindings = []

        rf_emit_result = _emit_observed_rf_node(rf_obs, ctx, source=source)
        emitted_bindings = []
        if rf_ip_correlation_engine:
            for binding in bindings:
                net_obs = rf_ip_correlation_engine.get_network_observation(binding.network_observation_id)
                if not net_obs:
                    continue
                emit_result = _emit_rf_ip_binding(binding, rf_obs, net_obs, ctx)
                emitted_bindings.append({
                    'binding': binding.to_dict(),
                    'writebus': {
                        'ok': bool(getattr(emit_result, 'ok', False)),
                        'errors': list(getattr(emit_result, 'errors', []) or []),
                    },
                })
        return rf_obs, bindings, emitted_bindings, {
            'ok': bool(getattr(rf_emit_result, 'ok', False)),
            'errors': list(getattr(rf_emit_result, 'errors', []) or []),
        }

    def _ingest_rfuav_detection_event(data, ctx):
        if not rfuav_evidence_emitter:
            raise RuntimeError("RFUAV evidence emitter unavailable")

        result = rfuav_evidence_emitter.ingest(data or {}, ctx=ctx, emit=True, stream=False)
        if not result.get('accepted'):
            result['questdb'] = {'written': False}
            result['binding_count'] = 0
            result['bindings'] = []
            result['binding_emissions'] = []
            return result

        result['questdb'] = {'written': _write_rfuav_event_to_questdb(result.get('event') or {})}
        rf_obs, bindings, emitted_bindings = _observe_rfuav_observation(result.get('observation') or {}, ctx)
        if rf_obs is not None:
            result['rf_observation'] = rf_obs.to_dict()
        result['binding_count'] = len(bindings)
        result['bindings'] = [binding.to_dict() for binding in bindings]
        result['binding_emissions'] = emitted_bindings
        return result

    def _consume_rfuav_kafka_event(event):
        ctx = _scythe_write_context(
            data=event,
            source="rfuav_kafka_consumer",
            default_operator_id="SYSTEM:RFUAV_KAFKA",
        )
        return _ingest_rfuav_detection_event(event, ctx)

    if rfuav_kafka_consumer is not None:
        try:
            rfuav_kafka_consumer.start_background()
            logger.info("[OK] RFUAV Kafka consumer started")
        except Exception as e:
            logger.warning(f"[WARN] RFUAV Kafka consumer failed to start: {e}")

    def _projection_location(payload):
        if not isinstance(payload, dict):
            return None

        location = payload.get("location") or {}
        if "lat" in location and "lon" in location:
            return {
                "lat": float(location["lat"]),
                "lon": float(location["lon"]),
                "alt_m": float(location.get("altitude_m", location.get("alt_m", location.get("alt", 0.0))) or 0.0),
            }

        sensor_payload = payload.get("sensor") or {}
        sensor_location = sensor_payload.get("location") or {}
        if "lat" in sensor_location and "lon" in sensor_location:
            return {
                "lat": float(sensor_location["lat"]),
                "lon": float(sensor_location["lon"]),
                "alt_m": float(sensor_location.get("altitude_m", sensor_location.get("alt_m", sensor_location.get("alt", 0.0))) or 0.0),
            }

        position = payload.get("position")
        if isinstance(position, (list, tuple)) and len(position) >= 2:
            return {
                "lat": float(position[0]),
                "lon": float(position[1]),
                "alt_m": float(position[2] if len(position) > 2 else 0.0),
            }

        node = payload.get("node") or {}
        node_position = node.get("position")
        if isinstance(node_position, (list, tuple)) and len(node_position) >= 2:
            return {
                "lat": float(node_position[0]),
                "lon": float(node_position[1]),
                "alt_m": float(node_position[2] if len(node_position) > 2 else 0.0),
            }

        return None

    def _projection_label(payload, fallback_id):
        if not isinstance(payload, dict):
            return fallback_id
        return (
            payload.get("actor_label")
            or payload.get("callsign")
            or payload.get("name")
            or payload.get("label")
            or (payload.get("metadata") or {}).get("actor_label")
            or (payload.get("sensor") or {}).get("name")
            or (payload.get("metadata") or {}).get("name")
            or fallback_id
        )

    def _haversine_m(lat1, lon1, lat2, lon2):
        radius_m = 6371000.0
        lat1_rad = math.radians(lat1)
        lat2_rad = math.radians(lat2)
        delta_lat = math.radians(lat2 - lat1)
        delta_lon = math.radians(lon2 - lon1)
        a = math.sin(delta_lat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return radius_m * c

    def _bearing_deg(lat1, lon1, lat2, lon2):
        lat1_rad = math.radians(lat1)
        lat2_rad = math.radians(lat2)
        delta_lon = math.radians(lon2 - lon1)
        x = math.sin(delta_lon) * math.cos(lat2_rad)
        y = math.cos(lat1_rad) * math.sin(lat2_rad) - math.sin(lat1_rad) * math.cos(lat2_rad) * math.cos(delta_lon)
        return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0

    def _relative_bearing_deg(observer_heading_deg, absolute_bearing_deg):
        return ((absolute_bearing_deg - observer_heading_deg + 540.0) % 360.0) - 180.0

    def _safe_float_arg(name, default=None):
        raw = request.args.get(name)
        if raw in (None, ""):
            return default
        try:
            return float(raw)
        except Exception:
            return default

    def _trackable_recon_entities_snapshot():
        entities = None
        if OPERATOR_MANAGER_AVAILABLE and operator_manager is not None:
            try:
                room = (
                    operator_manager.get_room_by_name("Global")
                    or operator_manager.get_room_by_name("Recon")
                    or operator_manager.get_room_by_name("CommandOps")
                    or operator_manager.get_room_by_name("Command Ops")
                )
                if room:
                    room_entities = operator_manager.room_entities.get(room.room_id, {})
                    entities = [
                        entry.get("data", {})
                        for entry in room_entities.values()
                        if entry.get("type") in RECON_TRACKABLE_TYPES
                    ]
            except Exception:
                entities = None
        if not entities:
            entities = recon_system.get_all_entities()
        entities = list(entities or [])
        if not entities or not rf_ip_correlation_engine:
            return [apply_recon_actor_summary(entity) for entity in entities]
        try:
            bindings = rf_ip_correlation_engine.recent_bindings(limit=max(len(entities) * 4, 64))
            network_observations_by_id = {}
            rf_observations_by_id = {}
            for binding in bindings:
                network_observation_id = str(binding.get("network_observation_id") or "")
                if network_observation_id and network_observation_id not in network_observations_by_id:
                    net_obs = rf_ip_correlation_engine.get_network_observation(network_observation_id)
                    if net_obs:
                        network_observations_by_id[network_observation_id] = net_obs.to_dict()
                rf_observation_id = str(binding.get("rf_observation_id") or "")
                if rf_observation_id and rf_observation_id not in rf_observations_by_id:
                    rf_obs = rf_ip_correlation_engine.get_rf_observation(rf_observation_id)
                    if rf_obs:
                        rf_observations_by_id[rf_observation_id] = rf_obs.to_dict()
            stitched = apply_recon_network_stitch_batch(
                entities,
                bindings,
                network_observations_by_id=network_observations_by_id,
                rf_observations_by_id=rf_observations_by_id,
                now=time.time(),
            )
            return [apply_recon_actor_summary(entity) for entity in stitched]
        except Exception as exc:
            logger.debug("Recon network stitching skipped: %s", exc)
            return [apply_recon_actor_summary(entity) for entity in entities]

    def _resolve_observer_context(observer_id, lat=None, lon=None, alt_m=None, heading_deg=0.0):
        raw_observer_id = str(observer_id or "").strip()
        sensor_node_id = None
        sensor_payload = None
        if raw_observer_id:
            sensor_node_id = raw_observer_id if raw_observer_id.startswith("sensor:") else f"sensor:{raw_observer_id}"
            sensor_payload = sensor_store.get(sensor_node_id)

        recon_entity_id = raw_observer_id[6:] if raw_observer_id.startswith("sensor:") else raw_observer_id
        recon_payload = recon_system.get_entity(recon_entity_id) if recon_entity_id else None

        observer_location = None
        if lat is not None and lon is not None:
            observer_location = {"lat": float(lat), "lon": float(lon), "alt_m": float(alt_m or 0.0)}
        if observer_location is None:
            observer_location = _projection_location(sensor_payload) or _projection_location(recon_payload)
        if observer_location is None:
            return None

        observer_label = (
            _projection_label(recon_payload or {}, recon_entity_id or raw_observer_id or "observer")
            if recon_payload
            else _projection_label(sensor_payload or {}, raw_observer_id or "observer")
        )
        if not observer_label:
            observer_label = raw_observer_id or "observer"

        return {
            "observer_id": raw_observer_id or (sensor_payload or {}).get("sensor_id") or "observer",
            "sensor_node_id": sensor_node_id,
            "recon_entity_id": recon_entity_id,
            "label": observer_label,
            "lat": observer_location["lat"],
            "lon": observer_location["lon"],
            "alt_m": observer_location.get("alt_m", 0.0),
            "heading_deg": float(heading_deg or 0.0),
            "source": (
                "query_override"
                if lat is not None and lon is not None
                else "sensor_store"
                if sensor_payload
                else "recon_entity"
                if recon_payload
                else "unknown"
            ),
        }

    def _project_target(observer, target_location, *, entity_id, label, projection_type, source, confidence=0.5, metadata=None):
        if not target_location:
            return None

        distance_m = _haversine_m(observer["lat"], observer["lon"], target_location["lat"], target_location["lon"])
        absolute_bearing_deg = _bearing_deg(observer["lat"], observer["lon"], target_location["lat"], target_location["lon"])
        relative_bearing_deg = _relative_bearing_deg(observer.get("heading_deg", 0.0), absolute_bearing_deg)
        alt_delta = float(target_location.get("alt_m", 0.0) or 0.0) - float(observer.get("alt_m", 0.0) or 0.0)
        elevation_deg = math.degrees(math.atan2(alt_delta, max(distance_m, 1.0)))

        return {
            "entity_id": entity_id,
            "label": label,
            "type": projection_type,
            "source": source,
            "confidence": round(float(confidence or 0.0), 4),
            "distance_m": round(distance_m, 2),
            "absolute_bearing_deg": round(absolute_bearing_deg, 2),
            "relative_bearing_deg": round(relative_bearing_deg, 2),
            "elevation_deg": round(elevation_deg, 2),
            "location": {
                "lat": round(float(target_location["lat"]), 7),
                "lon": round(float(target_location["lon"]), 7),
                "alt_m": round(float(target_location.get("alt_m", 0.0) or 0.0), 2),
            },
            "metadata": metadata or {},
        }

    def _describe_control_path_seed(entity_id, entity, binding):
        entity = entity or {}
        metadata = entity.get("metadata") or {}
        score_components = binding.get("score_components") or {}
        rf_meta = ((binding.get("rf_observation") or {}).get("metadata") or {}).get("rf") or {}
        parts = [
            entity_id,
            entity.get("label") or entity.get("name"),
            entity.get("type"),
            entity.get("source"),
            entity.get("platform"),
            entity.get("disposition"),
            metadata.get("network_role"),
            metadata.get("hostname"),
            metadata.get("ssid"),
            metadata.get("bssid"),
            binding.get("rf_node_id"),
            rf_meta.get("class"),
            rf_meta.get("subtype"),
        ]
        for key in ("time_alignment", "frequency_alignment", "power_alignment", "spatial_alignment"):
            if key in score_components:
                parts.append(f"{key}:{score_components.get(key)}")
        return " | ".join(str(part) for part in parts if part not in (None, ""))

    def _identity_stitch_candidates(entity_id, description, limit=4):
        limit = max(1, min(int(limit or 4), 8))
        results = []
        seen = {str(entity_id)}
        ee = globals().get("embedding_engine")

        try:
            from semantic_shadow import SemanticShadow

            shadow = SemanticShadow.get_instance()
        except Exception:
            shadow = None

        if ee is not None and shadow is not None and getattr(shadow, "_tq_store", None) is not None:
            try:
                query_vec = ee.embed_text(description)
                if query_vec is not None:
                    for candidate_id, similarity in shadow._tq_store.search(query_vec.astype("float32"), k=limit + 1):
                        candidate_key = str(candidate_id or "").replace("recon:", "")
                        if not candidate_key or candidate_key in seen:
                            continue
                        seen.add(candidate_key)
                        results.append(
                            {
                                "entity_id": candidate_key,
                                "label": candidate_key,
                                "source": "turboquant",
                                "similarity": round(float(similarity or 0.0), 4),
                            }
                        )
                        if len(results) >= limit:
                            return results
            except Exception as e:
                logger.debug(f"TurboQuant identity stitch lookup failed: {e}")

        if ee is None:
            return results

        try:
            for candidate in ee.search_similar(description, k=limit * 2):
                candidate_key = str(candidate.get("entity_id") or "").replace("recon:", "")
                if not candidate_key or candidate_key in seen:
                    continue
                seen.add(candidate_key)
                results.append(
                    {
                        "entity_id": candidate_key,
                        "label": candidate.get("label") or candidate_key,
                        "source": "faiss",
                        "similarity": round(float(candidate.get("similarity") or 0.0), 4),
                    }
                )
                if len(results) >= limit:
                    break
        except Exception as e:
            logger.debug(f"EmbeddingEngine identity stitch lookup failed: {e}")
        return results

    def _control_path_target_entity(prediction, recon_by_id):
        target_entity_id = str(prediction.get("target_entity_id") or "")
        target_entity = recon_by_id.get(target_entity_id)
        if target_entity:
            return target_entity
        evidence = prediction.get("supporting_evidence") or {}
        dst_node = str(evidence.get("questdb_dst_node") or "")
        return {
            "entity_id": target_entity_id,
            "label": prediction.get("target_label") or dst_node or target_entity_id,
            "type": "PREDICTED_NETWORK_ENDPOINT",
            "source": "predictive_control_path_engine",
            "metadata": {
                "forecast": True,
                "obs_class": "forecast",
                "dst_node": dst_node,
                "source": "questdb_fanin" if dst_node else "identity_stitch",
            },
        }

    def _build_control_path_forecasts(observer, recon_entities, *, limit=6, max_distance_m=10000.0):
        if not predictive_control_path_engine or not rf_ip_correlation_engine:
            return {
                "predictions": [],
                "signals": {
                    "questdb_edge_rate_eps": 0.0,
                    "questdb_fanin_events": 0,
                    "questdb_recent_alerts": 0,
                    "questdb_top_talkers": 0,
                },
                "counts": {
                    "forecast_paths": 0,
                    "projectable_forecasts": 0,
                },
            }

        recon_by_id = {
            str(entity.get("entity_id")): entity
            for entity in (recon_entities or [])
            if entity.get("entity_id")
        }
        recent_bindings = []
        for binding in reversed(rf_ip_correlation_engine.recent_bindings(limit=max(limit * 4, 8))):
            enriched_binding = dict(binding)
            rf_obs = rf_ip_correlation_engine.get_rf_observation(binding.get("rf_observation_id"))
            if rf_obs:
                enriched_binding["rf_observation"] = rf_obs.to_dict()
            net_obs = rf_ip_correlation_engine.get_network_observation(binding.get("network_observation_id"))
            if net_obs:
                enriched_binding["network_observation"] = net_obs.to_dict()
            recent_bindings.append(enriched_binding)

        forecast_bundle = predictive_control_path_engine.predict(
            observer=observer,
            recent_bindings=recent_bindings,
            recon_entities_by_id=recon_by_id,
            describe_entity=_describe_control_path_seed,
            identity_candidates=_identity_stitch_candidates,
            limit=limit,
        )

        predictions = []
        projectable = 0
        for prediction in forecast_bundle.get("predictions", []):
            seed_entity = recon_by_id.get(str(prediction.get("current_entity_id") or ""))
            target_entity = _control_path_target_entity(prediction, recon_by_id)
            seed_projection = _project_target(
                observer,
                _projection_location(seed_entity),
                entity_id=prediction.get("current_entity_id"),
                label=prediction.get("current_label") or prediction.get("current_entity_id"),
                projection_type="CONTROL_PATH_SEED",
                source="control_path_prediction",
                confidence=prediction.get("confidence", 0.0),
                metadata={
                    "forecast": True,
                    "obs_class": "forecast",
                    "prediction_id": prediction.get("prediction_id"),
                },
            )
            target_projection = _project_target(
                observer,
                _projection_location(target_entity),
                entity_id=prediction.get("target_entity_id"),
                label=prediction.get("target_label") or prediction.get("target_entity_id"),
                projection_type="CONTROL_PATH_PREDICTED",
                source="control_path_prediction",
                confidence=prediction.get("confidence", 0.0),
                metadata={
                    "forecast": True,
                    "obs_class": "forecast",
                    "prediction_id": prediction.get("prediction_id"),
                    "time_horizon_s": prediction.get("time_horizon_s"),
                    "provenance_rule": prediction.get("provenance_rule"),
                },
            )
            if target_projection and target_projection["distance_m"] > max_distance_m:
                continue
            if target_projection:
                projectable += 1
            enriched = dict(prediction)
            projected_path = []
            for point in ((prediction.get("motion_forecast") or {}).get("path") or []):
                location = (point or {}).get("location")
                path_projection = _project_target(
                    observer,
                    location,
                    entity_id=prediction.get("current_entity_id"),
                    label=prediction.get("current_label") or prediction.get("current_entity_id"),
                    projection_type="CONTROL_PATH_MOTION_PREDICTED",
                    source="control_path_prediction",
                    confidence=(point or {}).get("confidence", prediction.get("confidence", 0.0)),
                    metadata={
                        "forecast": True,
                        "obs_class": "forecast",
                        "prediction_id": prediction.get("prediction_id"),
                        "step": (point or {}).get("step"),
                        "time_offset_s": (point or {}).get("time_offset_s"),
                        "radius_m": (point or {}).get("radius_m"),
                        "model": (point or {}).get("model"),
                    },
                )
                if path_projection and path_projection["distance_m"] <= max_distance_m:
                    projected_path.append(path_projection)
            if prediction.get("motion_forecast"):
                motion_forecast = dict(prediction.get("motion_forecast") or {})
                motion_forecast["projected_path_count"] = len(projected_path)
                enriched["motion_forecast"] = motion_forecast
            enriched["seed_projection"] = seed_projection
            enriched["projected_target"] = target_projection
            enriched["projected_path"] = projected_path
            predictions.append(enriched)

        return {
            "predictions": predictions,
            "signals": forecast_bundle.get("signals") or {},
            "counts": {
                "forecast_paths": len(predictions),
                "projectable_forecasts": projectable,
            },
        }

    def _forecast_node_payload(node_id, label, projection, prediction):
        evidence = prediction.get("supporting_evidence") or {}
        payload = {
            "id": node_id,
            "kind": "predicted_network_endpoint",
            "labels": {
                "title": label,
                "evidence": "FORECAST",
                "obs_class": "forecast",
            },
            "metadata": {
                "forecast": True,
                "obs_class": "forecast",
                "prediction_id": prediction.get("prediction_id"),
                "confidence": prediction.get("confidence"),
                "time_horizon_s": prediction.get("time_horizon_s"),
                "supporting_evidence": evidence,
                "provenance_rule": prediction.get("provenance_rule"),
                "render_style": prediction.get("render_style") or {},
                "motion_forecast": prediction.get("motion_forecast") or {},
                "projected_path": prediction.get("projected_path") or [],
                "temporal_phase": prediction.get("temporal_phase") or evidence.get("temporal_phase"),
                "temporal_cohesion": prediction.get("temporal_cohesion") or evidence.get("temporal_cohesion"),
                "periodicity_s": prediction.get("periodicity_s") or evidence.get("periodicity_s"),
                "last_seen_delta_s": prediction.get("last_seen_delta_s") or evidence.get("last_seen_delta_s"),
                "identity_pressure": prediction.get("identity_pressure") or evidence.get("identity_pressure"),
                "dissonance_score": prediction.get("dissonance_score") or (evidence.get("cognitive_dissonance") or {}).get("score"),
                "dissonance_zone": prediction.get("dissonance_zone") or (evidence.get("cognitive_dissonance") or {}).get("zone"),
                "entropy": prediction.get("entropy") or evidence.get("entropy"),
                "divergence_risk": prediction.get("divergence_risk") or evidence.get("divergence_risk"),
                "intent_hypotheses": prediction.get("intent_hypotheses") or evidence.get("intent_hypotheses") or [],
                "top_intent_label": prediction.get("top_intent_label") or (evidence.get("top_intent") or {}).get("label"),
                "top_intent_probability": prediction.get("top_intent_probability") or (evidence.get("top_intent") or {}).get("probability"),
                "resilience_score": prediction.get("resilience_score") or (evidence.get("countermeasure_simulation") or {}).get("resilience_score"),
                "countermeasure_strategy": prediction.get("countermeasure_strategy") or (evidence.get("countermeasure_simulation") or {}).get("recommended_action"),
                "requires_multi_node_disruption": prediction.get("requires_multi_node_disruption") or (evidence.get("countermeasure_simulation") or {}).get("requires_multi_node_disruption"),
                "field_view": prediction.get("field_view") or evidence.get("field_view") or {},
            },
        }
        if projection and projection.get("location"):
            location = projection["location"]
            payload["position"] = [
                float(location.get("lat") or 0.0),
                float(location.get("lon") or 0.0),
                float(location.get("alt_m") or 0.0),
            ]
        return payload

    def _emit_control_path_predictions(observer, predictions, ctx):
        import writebus
        from writebus import GraphOp

        results = []
        for prediction in predictions:
            sensor_node_id = str(prediction.get("sensor_node_id") or observer.get("sensor_node_id") or "")
            rf_node_id = str(prediction.get("rf_node_id") or "")
            current_entity_id = str(prediction.get("current_entity_id") or "").replace("recon:", "")
            target_entity_id = str(prediction.get("target_entity_id") or "").replace("recon:", "")
            if not sensor_node_id or not rf_node_id or not current_entity_id or not target_entity_id:
                continue

            current_node_id = current_entity_id if current_entity_id.startswith("recon:") else f"recon:{current_entity_id}"
            target_node_id = target_entity_id if target_entity_id.startswith("recon:") else f"recon:{target_entity_id}"
            rf_prediction_id = str(prediction.get("rf_prediction_id") or f"pred-rfip-{target_entity_id}")
            control_prediction_id = str(prediction.get("prediction_id") or f"pred-ctrl-{current_entity_id}-{target_entity_id}")
            projected_target = prediction.get("projected_target")
            confidence = float(prediction.get("confidence") or 0.0)
            horizon = int(prediction.get("time_horizon_s") or 0)
            evidence = prediction.get("supporting_evidence") or {}
            intent_hypotheses = prediction.get("intent_hypotheses") or evidence.get("intent_hypotheses") or []
            forecast_meta = {
                "forecast": True,
                "obs_class": "forecast",
                "confidence": confidence,
                "time_horizon_s": horizon,
                "supporting_evidence": evidence,
                "provenance_rule": prediction.get("provenance_rule"),
                "render_style": prediction.get("render_style") or {},
                "source_binding_id": prediction.get("source_binding_id"),
                "candidate_source": prediction.get("candidate_source"),
                "motion_forecast": prediction.get("motion_forecast") or {},
                "projected_path": prediction.get("projected_path") or [],
                "temporal_phase": prediction.get("temporal_phase") or evidence.get("temporal_phase"),
                "temporal_cohesion": prediction.get("temporal_cohesion") or evidence.get("temporal_cohesion"),
                "periodicity_s": prediction.get("periodicity_s") or evidence.get("periodicity_s"),
                "last_seen_delta_s": prediction.get("last_seen_delta_s") or evidence.get("last_seen_delta_s"),
                "identity_pressure": prediction.get("identity_pressure") or evidence.get("identity_pressure"),
                "dissonance_score": prediction.get("dissonance_score") or (evidence.get("cognitive_dissonance") or {}).get("score"),
                "dissonance_zone": prediction.get("dissonance_zone") or (evidence.get("cognitive_dissonance") or {}).get("zone"),
                "entropy": prediction.get("entropy") or evidence.get("entropy"),
                "divergence_risk": prediction.get("divergence_risk") or evidence.get("divergence_risk"),
                "intent_hypotheses": intent_hypotheses,
                "top_intent_label": prediction.get("top_intent_label") or (evidence.get("top_intent") or {}).get("label"),
                "top_intent_probability": prediction.get("top_intent_probability") or (evidence.get("top_intent") or {}).get("probability"),
                "resilience_score": prediction.get("resilience_score") or (evidence.get("countermeasure_simulation") or {}).get("resilience_score"),
                "countermeasure_strategy": prediction.get("countermeasure_strategy") or (evidence.get("countermeasure_simulation") or {}).get("recommended_action"),
                "requires_multi_node_disruption": prediction.get("requires_multi_node_disruption") or (evidence.get("countermeasure_simulation") or {}).get("requires_multi_node_disruption"),
                "field_view": prediction.get("field_view") or evidence.get("field_view") or {},
            }
            graph_ops = [
                GraphOp(
                    event_type="NODE_UPDATE",
                    entity_id=target_node_id,
                    entity_data=_forecast_node_payload(
                        target_node_id,
                        prediction.get("target_label") or target_entity_id,
                        projected_target,
                        prediction,
                    ),
                ),
                GraphOp(
                    event_type="EDGE_UPDATE",
                    entity_id=rf_prediction_id,
                    entity_data={
                        "id": rf_prediction_id,
                        "kind": "RF_TO_IP_PREDICTED",
                        "nodes": [sensor_node_id, rf_node_id, target_node_id],
                        "weight": confidence,
                        "labels": {
                            "missionId": ctx.mission_id,
                            "evidence": "FORECAST",
                            "obs_class": "forecast",
                        },
                        "metadata": dict(forecast_meta),
                        "timestamp": time.time(),
                    },
                ),
                GraphOp(
                    event_type="EDGE_UPDATE",
                    entity_id=control_prediction_id,
                    entity_data={
                        "id": control_prediction_id,
                        "kind": "CONTROL_PATH_PREDICTED",
                        "nodes": [sensor_node_id, rf_node_id, current_node_id, target_node_id],
                        "weight": confidence,
                        "labels": {
                            "missionId": ctx.mission_id,
                            "evidence": "FORECAST",
                            "obs_class": "forecast",
                        },
                        "metadata": dict(forecast_meta),
                        "timestamp": time.time(),
                    },
                ),
            ]
            for intent in intent_hypotheses[:2]:
                intent_label = str(intent.get("label") or "").strip()
                if not intent_label:
                    continue
                intent_probability = float(intent.get("probability") or 0.0)
                intent_token = intent_label.lower().replace("_", "-").replace(" ", "-")
                intent_node_id = f"intent:{control_prediction_id}:{intent_token}"
                intent_edge_id = f"intent-edge:{control_prediction_id}:{intent_token}"
                intent_meta = {
                    "forecast": True,
                    "obs_class": "inferred",
                    "prediction_id": control_prediction_id,
                    "intent_label": intent_label,
                    "probability": intent_probability,
                    "provenance_rule": prediction.get("provenance_rule"),
                    "rationale": intent.get("rationale") or [],
                    "field_view": prediction.get("field_view") or evidence.get("field_view") or {},
                }
                graph_ops.extend(
                    [
                        GraphOp(
                            event_type="NODE_UPDATE",
                            entity_id=intent_node_id,
                            entity_data={
                                "id": intent_node_id,
                                "kind": "intent_hypothesis",
                                "labels": {
                                    "title": intent_label,
                                    "evidence": "INFERRED",
                                    "obs_class": "inferred",
                                },
                                "metadata": dict(intent_meta),
                                "timestamp": time.time(),
                            },
                        ),
                        GraphOp(
                            event_type="EDGE_UPDATE",
                            entity_id=intent_edge_id,
                            entity_data={
                                "id": intent_edge_id,
                                "kind": "INTENT_HYPOTHESIS",
                                "nodes": [current_node_id, intent_node_id, target_node_id],
                                "weight": intent_probability,
                                "labels": {
                                    "missionId": ctx.mission_id,
                                    "evidence": "INFERRED",
                                    "obs_class": "inferred",
                                },
                                "metadata": dict(intent_meta),
                                "timestamp": time.time(),
                            },
                        ),
                    ]
                )
            result = writebus.bus().commit(
                entity_id=control_prediction_id,
                entity_type="CONTROL_PATH_PREDICTED",
                entity_data={
                    "observer": observer,
                    "prediction": prediction,
                    "rf_prediction_id": rf_prediction_id,
                },
                graph_ops=graph_ops,
                ctx=ctx,
                persist=True,
                audit=True,
            )
            results.append(
                {
                    "prediction_id": control_prediction_id,
                    "rf_prediction_id": rf_prediction_id,
                    "ok": result.ok,
                    "persisted": result.persisted,
                    "graph_applied": result.graph_applied,
                    "errors": result.errors,
                }
            )
        return results

    def _recon_entity_exists_in_room(room_name: str, entity_id: str, writer=None) -> bool:
        try:
            if writer is None:
                import writebus
                writer = writebus.bus()
            operator_manager = getattr(writer, "operator_manager", None)
            if not operator_manager:
                return False
            room = operator_manager.get_room_by_name(room_name)
            if not room:
                return False
            room_id = getattr(room, "room_id", None)
            if room_id is None and isinstance(room, dict):
                room_id = room.get("room_id") or room.get("id")
            if not room_id:
                return False
            return entity_id in operator_manager.room_entities.get(room_id, {})
        except Exception:
            return False

    def _log_recon_entity_upsert(entity_id: str, *, is_existing: bool) -> None:
        if is_existing:
            logger.debug("Upserted recon entity: %s", entity_id)
        else:
            logger.info("Created recon entity: %s", entity_id)

    @app.route('/api/recon/entity', methods=['POST'])
    def create_recon_entity():
        """Create or persist a new reconnaissance entity via WriteBus/Registry"""
        try:
            from writebus import WriteContext
            from registries.recon_registry import _norm_entity_id, upsert_recon_entity

            data = request.get_json() or {}
            entity_id = _norm_entity_id(data)

            # Build context from request headers
            ctx = WriteContext(
                room_name="Global",
                mission_id=data.get("mission_id") or data.get("missionId"),
                operator_id=request.headers.get("X-Operator-Id"),
                session_token=request.headers.get("X-Session-Token"),
                request_id=request.headers.get("X-Request-Id"),
                source="manual_ui",
            )

            # Execute write via registry (chokepoint)
            entity_already_present = _recon_entity_exists_in_room(ctx.room_name, entity_id)
            result = upsert_recon_entity(data, ctx)
            entity = result['entity']
            _auto_promote_mobile_sensor(entity, data, ctx)

            # --- LEGACY CACHE UPDATE ---
            # Update in-memory recon_system for GET /api/recon/entity/<id> immediate consistency
            if 'recon_system' in globals():
                try:
                    eid = entity['entity_id']
                    # Preserve calculated fields if we can, or let recon_system re-calc on next tick if it does that.
                    # For now, just ensuring presence.
                    recon_system.entities[eid] = entity
                    if hasattr(recon_system, '_dirty_entities'):
                        recon_system._dirty_entities.add(eid)
                    if hasattr(recon_system, '_spatial_index'):
                        recon_system._spatial_index.mark_dirty()
                except Exception as e_cache:
                    logger.warning(f"Failed to update legacy recon_system cache: {e_cache}")
            # ---------------------------

            # ── Push to SSE subscribers ──────────────────────────────────
            try:
                subs = getattr(stream_recon_entities, '_subscribers', {})
                if subs:
                    sse_event = {'type': 'entity_upsert', 'entity': entity}
                    for q in list(subs.values()):
                        try:
                            q.put_nowait(sse_event)
                        except Exception:
                            pass
            except Exception:
                pass

            _log_recon_entity_upsert(entity['entity_id'], is_existing=entity_already_present)
            return jsonify({
                'status': 'ok',
                'entity': entity,
                'debug': result.get('write_result', {}).get('debug', {})
            })
        except Exception as e:
            logger.error(f"Error creating recon entity: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return jsonify({'status': 'error', 'message': str(e)}), 500
            logger.error(f"Error creating recon entity: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    # ------------------------------------------------------------------
    # batch operations convenience helpers
    # ------------------------------------------------------------------

    @app.route('/api/recon/entity/batch', methods=['POST'])
    def create_recon_entities_batch():
        """Batch-create or persist multiple reconnaissance entities.

        The client should send JSON `{ "entities": [ ... ] }`.  This
        endpoint simply loops through and calls the same registry
        helper that `POST /api/recon/entity` uses, updating the legacy
        cache as needed.

        Batching is essential for high‑volume ingestion so the browser
        can send hundreds or thousands of entities in a few requests
        instead of overwhelming the network stack with one‑by‑one POSTs.
        """
        try:
            from writebus import WriteContext
            from registries.recon_registry import upsert_recon_entity

            body = request.get_json() or {}
            entities = body.get('entities') or []
            created = []

            for data in entities:
                ctx = WriteContext(
                    room_name="Global",
                    mission_id=data.get("mission_id") or data.get("missionId"),
                    operator_id=request.headers.get("X-Operator-Id"),
                    session_token=request.headers.get("X-Session-Token"),
                    request_id=request.headers.get("X-Request-Id"),
                    source="manual_ui",
                )
                result = upsert_recon_entity(data, ctx)
                entity = result['entity']
                created.append(entity)
                # maintain legacy cache for UI consistency
                if 'recon_system' in globals():
                    try:
                        eid = entity['entity_id']
                        recon_system.entities[eid] = entity
                        if hasattr(recon_system, '_dirty_entities'):
                            recon_system._dirty_entities.add(eid)
                        if hasattr(recon_system, '_spatial_index'):
                            recon_system._spatial_index.mark_dirty()
                    except Exception:
                        pass

            logger.info(f"Created batch of {len(created)} recon entities")
            return jsonify({'status': 'ok', 'entities': created})
        except Exception as e:
            logger.error(f"Error creating recon entities batch: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/recon/entity/<entity_id>/disposition', methods=['POST', 'PUT'])
    def update_entity_disposition(entity_id):
        """Update an entity's disposition via WriteBus/Registry"""
        try:
            from writebus import WriteContext
            from registries.recon_registry import update_disposition

            data = request.get_json() or {}
            disposition = data.get('disposition') or request.args.get('disposition')

            if not disposition:
                return jsonify({'status': 'error', 'message': 'disposition required'}), 400

            # Legacy system update (handles logic checks)
            legacy_result = recon_system.update_entity_disposition(entity_id, disposition.upper())

            if legacy_result['status'] == 'ok':
                try:
                    # WriteBus update
                    ctx = WriteContext(
                        room_name="Global",
                        operator_id=request.headers.get("X-Operator-Id"),
                        session_token=request.headers.get("X-Session-Token"),
                        request_id=request.headers.get("X-Request-Id"),
                        source="manual_ui",
                    )

                    update_disposition(entity_id, disposition.upper(), ctx)
                except Exception as wb_err:
                    logger.warning(f"WriteBus update failed for disposition: {wb_err}")

                return jsonify(legacy_result)
            return jsonify(legacy_result), 400
        except Exception as e:
            logger.error(f"Error updating entity disposition: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/detection/emit', methods=['POST'])
    def emit_detection_route():
        """
        Emit a high-volume detection event via DetectionRegistry.
        This handles transient graph edges (Tier A) and durable summaries (Tier B).
        """
        try:
            from writebus import WriteContext
            # detection_registry is initialized globally at startup
            global detection_registry

            if detection_registry is None:
                # Attempt lazy init if missing (e.g. testing)
                try:
                    from registries.detection_registry import init_detection_registry
                    detection_registry = init_detection_registry()
                except Exception as e:
                    logger.error(f"Lazy init of detection_registry failed: {e}")
                    return jsonify({'status': 'error', 'message': 'detection_registry not initialized'}), 503

            data = request.get_json() or {}

            # Allow wrapper format {"detection": {...}} or direct {...}
            detection_data = data.get('detection', data)

            ctx = WriteContext(
                room_name="Global",
                operator_id=request.headers.get("X-Operator-Id"),
                session_token=request.headers.get("X-Session-Token"),
                request_id=request.headers.get("X-Request-Id"),
                source=f"api:{request.remote_addr}",
                origin_host=request.headers.get("Host")
            )

            result = detection_registry.emit_detection(detection_data, ctx)
            return jsonify({'status': 'ok', 'result': result})

        except Exception as e:
            logger.error(f"Error emitting detection: {e}")
            # Identify known validation errors (ValueError) vs system errors
            if isinstance(e, ValueError):
                return jsonify({'status': 'error', 'message': str(e)}), 400
            return jsonify({'status': 'error', 'message': str(e)}), 500

    # ========================================================================
    # API ROUTES - SENSORS (Tx+Rx) - Assignable to Recon Entities
    # ========================================================================
    # NOTE: The canonical sensor upsert route is registered below as
    # POST/PUT /api/sensors (upsert_sensor_endpoint) with full normalization,
    # persistence, and provenance. assign_sensor + sensor_activity routes are
    # also registered below with full rehydration support.
    # Do NOT add duplicate route registrations here.

    @app.route('/api/recon/proximity', methods=['GET'])
    def get_proximity_entities():
        """Get entities within proximity of reference point"""
        try:
            radius = float(request.args.get('radius', 5.0))  # Default 5 NM
            entities = recon_system.get_entities_in_proximity(radius)
            return jsonify({
                'status': 'ok',
                'radius_nm': radius,
                'entity_count': len(entities),
                'entities': entities,
                'reference_point': recon_system.reference_point,
                'timestamp': time.time()
            })
        except Exception as e:
            logger.error(f"Error getting proximity entities: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/recon/alerts', methods=['GET'])
    def get_recon_alerts():
        """Get proximity alerts for threatening entities"""
        try:
            alerts = recon_system.get_proximity_alerts()
            return jsonify({
                'status': 'ok',
                'alert_count': len(alerts),
                'alerts': alerts,
                'timestamp': time.time()
            })
        except Exception as e:
            logger.error(f"Error getting alerts: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/recon/reference', methods=['POST', 'PUT'])
    def set_reference_point():
        """Set the reference point for proximity calculations"""
        try:
            data = request.get_json() or {}
            lat = data.get('lat') or float(request.args.get('lat', 37.7749))
            lon = data.get('lon') or float(request.args.get('lon', -122.4194))

            recon_system.set_reference_point(lat, lon)
            return jsonify({
                'status': 'ok',
                'reference_point': recon_system.reference_point,
                'timestamp': time.time()
            })
        except Exception as e:
            logger.error(f"Error setting reference point: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/recon/tasks', methods=['GET'])
    def get_recon_tasks():
        """Get all tasks"""
        try:
            tasks = recon_system.get_all_tasks()
            return jsonify({
                'status': 'ok',
                'task_count': len(tasks),
                'tasks': tasks,
                'timestamp': time.time()
            })
        except Exception as e:
            logger.error(f"Error getting tasks: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/recon/task', methods=['POST'])
    def create_recon_task():
        """Create a new investigation task"""
        try:
            data = request.get_json() or {}
            entity_id = data.get('entity_id')
            task_type = data.get('task_type', 'INVESTIGATE')
            asset_id = data.get('asset_id')
            priority = data.get('priority', 5)

            if not entity_id:
                return jsonify({'status': 'error', 'message': 'entity_id required'}), 400

            result = recon_system.create_task(entity_id, task_type, asset_id, priority)
            if result['status'] == 'ok':
                return jsonify(result)
            return jsonify(result), 400
        except Exception as e:
            logger.error(f"Error creating task: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/recon/task/<task_id>', methods=['GET'])
    def get_recon_task(task_id):
        """Get a specific task"""
        try:
            task = recon_system.get_task(task_id)
            if task:
                return jsonify({'status': 'ok', 'task': task})
            return jsonify({'status': 'error', 'message': f'Task {task_id} not found'}), 404
        except Exception as e:
            logger.error(f"Error getting task: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/recon/task/<task_id>/status', methods=['POST', 'PUT'])
    def update_task_status(task_id):
        """Update a task's status"""
        try:
            data = request.get_json() or {}
            status = data.get('status') or request.args.get('status')

            if not status:
                return jsonify({'status': 'error', 'message': 'status required'}), 400

            result = recon_system.update_task_status(task_id, status.upper())
            if result['status'] == 'ok':
                return jsonify(result)
            return jsonify(result), 400
        except Exception as e:
            logger.error(f"Error updating task status: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/recon/simulate', methods=['POST', 'GET'])
    def simulate_entity_movement():
        """Simulate entity movement for demo"""
        try:
            result = recon_system.simulate_entity_movement()
            return jsonify(result)
        except Exception as e:
            logger.error(f"Error simulating movement: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/recon/status', methods=['GET'])
    def get_recon_status():
        """Get recon system status"""
        try:
            status = recon_system.get_status()
            return jsonify({'status': 'ok', **status})
        except Exception as e:
            logger.error(f"Error getting recon status: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/recon/geolocate', methods=['GET'])
    def recon_geolocate():
        """Geolocate an IP address or hostname using public geolocation services."""
        try:
            target = request.args.get('target')
            if not target:
                return jsonify({'status': 'error', 'message': 'target parameter required'}), 400

            # Simple private network rejection
            private_patterns = [
                lambda t: t.startswith('192.168.'),
                lambda t: t.startswith('10.'),
                lambda t: t.startswith('127.'),
                lambda t: t.startswith('localhost'),
                lambda t: t.startswith('172.') and 16 <= int(t.split('.')[1]) <= 31 if '.' in t and t.split('.')[1].isdigit() else False
            ]
            # If it looks like a private IP or localhost, return 400 so client can fallback
            try:
                if any(p(target) for p in private_patterns):
                    return jsonify({'status': 'error', 'message': 'private network target'}), 400
            except Exception:
                pass

            # Try ip-api.com first (supports hostnames)
            url = f'http://ip-api.com/json/{urllib.parse.quote(target)}'
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'rf-scythe/1.0'})
                with urllib.request.urlopen(req, timeout=6, context=ctx) as resp:
                    raw = resp.read().decode('utf-8')
                    data = json.loads(raw)
                    if data.get('status') == 'success' and data.get('lat') and data.get('lon'):
                        return jsonify({
                            'status': 'ok',
                            'lat': data.get('lat'),
                            'lon': data.get('lon'),
                            'city': data.get('city'),
                            'region': data.get('regionName'),
                            'country': data.get('country'),
                            'org': data.get('org') or data.get('isp')
                        })
            except Exception as e:
                logger.debug(f"ip-api lookup failed for {target}: {e}")

            # Fallback: ipinfo.io (rate-limited) - use unauthenticated endpoint
            try:
                url2 = f'https://ipinfo.io/{urllib.parse.quote(target)}/json'
                req2 = urllib.request.Request(url2, headers={'User-Agent': 'rf-scythe/1.0'})
                with urllib.request.urlopen(req2, timeout=6, context=ctx) as resp2:
                    txt = resp2.read().decode('utf-8')
                    info = json.loads(txt)
                    # ipinfo returns 'loc' as 'lat,lon'
                    loc = info.get('loc')
                    if loc:
                        lat_s, lon_s = loc.split(',')
                        return jsonify({
                            'status': 'ok',
                            'lat': float(lat_s),
                            'lon': float(lon_s),
                            'city': info.get('city'),
                            'region': info.get('region'),
                            'country': info.get('country'),
                            'org': info.get('org')
                        })
            except Exception as e:
                logger.debug(f"ipinfo lookup failed for {target}: {e}")

            return jsonify({'status': 'error', 'message': 'geolocation failed'}), 404
        except Exception as e:
            logger.error(f"Error in recon_geolocate: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    # ========================================================================
    # API ROUTES - SENSORS (Tx+Rx) - Assignable to Recon Entities
    # ========================================================================

    # In-memory sensor store (persisted to OperatorSessionManager for durability)
    sensor_store = {}
    sensor_assignments = {}  # edge_id -> assignment edge

    @app.route('/api/sensors', methods=['GET'])
    def get_all_sensors():
        """Get all sensors"""
        try:
            # Sync from OperatorSessionManager if available
            _rehydrate_global_room()

            sensors = list(sensor_store.values())
            return jsonify({
                'status': 'ok',
                'sensor_count': len(sensors),
                'sensors': sensors,
                'timestamp': time.time()
            })
        except Exception as e:
            logger.error(f"Error getting sensors: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/sensors/<sensor_id>', methods=['GET'])
    def get_sensor(sensor_id):
        """Get a specific sensor by ID"""
        try:
            node_id = f"sensor:{sensor_id}" if not sensor_id.startswith('sensor:') else sensor_id
            sensor = sensor_store.get(node_id)
            if sensor:
                return jsonify({'status': 'ok', 'sensor': sensor})
            return jsonify({'status': 'error', 'message': f'Sensor {sensor_id} not found'}), 404
        except Exception as e:
            logger.error(f"Error getting sensor {sensor_id}: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/sensors', methods=['POST', 'PUT'])
    def upsert_sensor_endpoint():
        """Create or update a sensor (Tx+Rx)"""
        try:
            data = request.get_json() or {}

            # Generate sensor ID if not provided
            sensor_id = data.get('sensor_id') or data.get('id') or f"SENSOR-{int(time.time()*1000) % 100000:05d}"
            if sensor_id.startswith('sensor:'):
                sensor_id = sensor_id[7:]  # Strip prefix for clean ID

            node_id = f"sensor:{sensor_id}"

            # Normalize position
            location = data.get('position') or data.get('location') or {}
            lat = float(location.get('lat', 0))
            lon = float(location.get('lon', location.get('lng', 0)))
            alt = float(location.get('alt_m', location.get('alt', 0)))

            # Build sensor object
            sensor = {
                'sensor_id': sensor_id,
                'node_id': node_id,
                'entity_type': 'SENSOR',
                'type': 'SENSOR',
                'name': data.get('name') or data.get('label') or sensor_id,
                'kind': 'sensor',
                'position': [lat, lon, alt],  # Normalized for hypergraph/UI
                'location': {'lat': lat, 'lon': lon, 'alt_m': alt},
                'tx': data.get('tx') or {
                    'enabled': False,
                    'bands_mhz': [],
                    'max_eirp_dbm': 0,
                    'waveforms': []
                },
                'rx': data.get('rx') or {
                    'enabled': True,
                    'bands_mhz': [[30, 6000]],  # Default wideband
                    'sensitivity_dbm': -110,
                    'sample_rate_hz': 2400000
                },
                'status': data.get('status') or {'state': 'ONLINE', 'last_seen': time.time()},
                'role': data.get('role') or 'static',
                'tags': data.get('tags') or [],
                'labels': {
                    'missionId': data.get('mission_id') or data.get('missionId') or (data.get('labels') or {}).get('missionId'),
                    'teamId': data.get('team_id') or (data.get('labels') or {}).get('teamId'),
                    'roles': ['rx'] if not (data.get('tx') or {}).get('enabled') else ['rx', 'tx'],
                    'tags': data.get('tags') or []
                },
                'metadata': {
                    'owner': data.get('owner') or 'operators',
                    'trust': data.get('trust') or 'full',
                    'notes': data.get('notes') or '',
                    'provenance': {
                        'source_id': None,
                        'source_update_time': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                        'confidence': data.get('confidence', 1.0)
                    }
                },
                'last_update': time.time(),
                'created': sensor_store.get(node_id, {}).get('created') or time.time()
            }

            # Get operator for provenance
            token = request.headers.get("X-Session-Token") or data.get("session_token")
            operator = None
            if OPERATOR_MANAGER_AVAILABLE and token:
                try:
                    manager = get_session_manager()
                    operator = manager.get_operator_for_session(token)
                    if operator:
                        sensor['metadata']['provenance']['source_id'] = f"operator:{operator.operator_id}"
                except Exception:
                    pass

            # Canonical write path: room persistence + hypergraph mutation
            # must pass through WriteBus.
            try:
                import writebus
                from writebus import GraphOp, WriteContext

                ctx = WriteContext(
                    room_name="Global",
                    mission_id=data.get('mission_id') or data.get('missionId'),
                    team_id=data.get('team_id') or (data.get('labels') or {}).get('teamId'),
                    operator=operator,
                    operator_id=(
                        getattr(operator, 'operator_id', None)
                        or request.headers.get("X-Operator-Id")
                        or data.get("operator_id")
                        or "SYSTEM:SENSOR_API"
                    ),
                    session_token=token,
                    request_id=request.headers.get("X-Request-Id") or data.get("request_id"),
                    source="sensor_api",
                    evidence_refs=list(data.get("evidence_refs") or []),
                )
                graph_node = {
                    'id': node_id,
                    'kind': 'sensor',
                    'position': [lat, lon, alt],
                    'labels': sensor['labels'],
                    'metadata': sensor['metadata'],
                }
                write_res = writebus.bus().commit(
                    entity_id=node_id,
                    entity_type="SENSOR",
                    entity_data=sensor,
                    graph_ops=[GraphOp(event_type="NODE_UPDATE", entity_id=node_id, entity_data=graph_node)],
                    ctx=ctx,
                    persist=True,
                    audit=True,
                )
                if not write_res.ok:
                    return jsonify({
                        'status': 'error',
                        'message': 'WriteBus commit failed',
                        'write_result': {
                            'commit_status': write_res.commit_status,
                            'errors': write_res.errors,
                            'debug': write_res.debug,
                        }
                    }), 500
            except Exception as ex:
                logger.error(f"WriteBus sensor commit failed: {ex}")
                return jsonify({'status': 'error', 'message': str(ex)}), 500

            # Store in memory after the canonical write succeeds.
            sensor_store[node_id] = sensor

            logger.info(f"Created/updated sensor: {sensor_id}")
            return jsonify({'status': 'ok', 'sensor': sensor})
        except Exception as e:
            logger.error(f"Error creating/updating sensor: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/sensors/<sensor_id>', methods=['DELETE'])
    def delete_sensor(sensor_id):
        """Delete a sensor"""
        try:
            node_id = f"sensor:{sensor_id}" if not sensor_id.startswith('sensor:') else sensor_id

            if node_id not in sensor_store:
                return jsonify({'status': 'error', 'message': f'Sensor {sensor_id} not found'}), 404

            # Remove any assignments involving this sensor
            to_remove = [eid for eid in sensor_assignments if node_id in eid]

            try:
                import writebus
                from writebus import GraphOp, WriteContext

                data = request.get_json(silent=True) or {}
                token = request.headers.get("X-Session-Token") or data.get("session_token")
                operator = None
                if OPERATOR_MANAGER_AVAILABLE and token:
                    try:
                        operator = get_session_manager().get_operator_for_session(token)
                    except Exception:
                        operator = None
                graph_ops = [GraphOp(event_type="NODE_DELETE", entity_id=node_id, entity_data={"id": node_id})]
                graph_ops.extend(
                    GraphOp(event_type="EDGE_DELETE", entity_id=eid, entity_data={"id": eid})
                    for eid in to_remove
                )
                write_res = writebus.bus().commit(
                    entity_id=node_id,
                    entity_type="SENSOR_TOMBSTONE",
                    entity_data={
                        "entity_id": node_id,
                        "type": "SENSOR_TOMBSTONE",
                        "deleted": True,
                        "deleted_at": time.time(),
                        "assignments_removed": list(to_remove),
                    },
                    graph_ops=graph_ops,
                    ctx=WriteContext(
                        room_name="Global",
                        operator=operator,
                        operator_id=(
                            getattr(operator, 'operator_id', None)
                            or request.headers.get("X-Operator-Id")
                            or data.get("operator_id")
                            or "SYSTEM:SENSOR_API"
                        ),
                        session_token=token,
                        request_id=request.headers.get("X-Request-Id") or data.get("request_id"),
                        source="sensor_api_delete",
                    ),
                    persist=True,
                    audit=True,
                )
                if not write_res.ok:
                    return jsonify({
                        'status': 'error',
                        'message': 'WriteBus delete commit failed',
                        'write_result': {
                            'commit_status': write_res.commit_status,
                            'errors': write_res.errors,
                            'debug': write_res.debug,
                        }
                    }), 500
            except Exception as ex:
                logger.error(f"WriteBus sensor delete failed: {ex}")
                return jsonify({'status': 'error', 'message': str(ex)}), 500

            # Remove from local caches after canonical delete succeeds.
            sensor_store.pop(node_id, None)
            for eid in to_remove:
                sensor_assignments.pop(eid, None)

            logger.info(f"Deleted sensor: {sensor_id}")
            return jsonify({'status': 'ok', 'deleted': sensor_id, 'assignments_removed': len(to_remove)})
        except Exception as e:
            logger.error(f"Error deleting sensor: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    # ------------------------------------------------------------------------
    # PCAP Helper Utilities
    # ------------------------------------------------------------------------

    def _q_int(name: str, default: int, lo: int = None, hi: int = None) -> int:
        try:
            v = int(request.args.get(name, default))
        except Exception:
            v = default
        if lo is not None: v = max(lo, v)
        if hi is not None: v = min(hi, v)
        return v

    def _q_float(name: str, default: float, lo: float = None, hi: float = None) -> float:
        try:
            v = float(request.args.get(name, default))
        except Exception:
            v = default
        if lo is not None: v = max(lo, v)
        if hi is not None: v = min(hi, v)
        return v

    def _parse_pcap_ip_from_id(s: str) -> str:
        # Handles "PCAP-93_184_216_34" -> "93.184.216.34"
        if not s:
            return ""
        if s.startswith("PCAP-"):
            s = s[len("PCAP-"):]
        return s.replace("_", ".")

    def _first_geo_from_endpoints(endpoints: list) -> dict:
        for ep in endpoints or []:
            geo = (ep or {}).get("geo") or {}
            if geo.get("lat") is not None and geo.get("lon") is not None:
                return geo
        return {}

    # ------------------------------------------------------------------------
    # PCAP Ingestion Endpoints (Operator Workflow)
    # ------------------------------------------------------------------------

    @app.route('/api/pcap/upload', methods=['POST'])
    def pcap_upload():
        """Upload a PCAP and create a session, optionally linked to a collection task.

        Form fields:
            file: PCAP binary
            sensor_id, mission_id, tags (JSON array)
            task_id: (optional) collection task to link this capture to
        """
        if not pcap_registry_instance:
             return jsonify({'status': 'error', 'message': 'PcapRegistry not available'}), 503

        try:
            # 1. Handle File
            file = request.files.get('file')
            file_bytes = None
            original_name = None

            if file:
                file_bytes = file.read()
                original_name = file.filename

            # 2. Extract Metadata
            sensor_id = request.form.get('sensor_id')
            mission_id = request.form.get('mission_id')
            tags_json = request.form.get('tags')
            tags = json.loads(tags_json) if tags_json else []
            task_id = request.form.get('task_id', '')

            operator = "unknown"
            if OPERATOR_MANAGER_AVAILABLE:
                token = request.headers.get("X-Session-Token")
                if token:
                    manager = get_session_manager()
                    op_obj = manager.get_operator_for_session(token)
                    if op_obj:
                        operator = getattr(op_obj, 'callsign', None) or getattr(op_obj, 'operator_id', 'unknown')

            # 3. Upsert Artifact
            artifact = pcap_registry_instance.upsert_pcap_artifact(
                file_bytes=file_bytes,
                original_name=original_name,
                operator=operator,
                mission_id=mission_id,
                sensor_id=sensor_id,
                tags=tags
            )

            # 4. Create Session (Receipt)
            session = pcap_registry_instance.create_pcap_session(
                artifact_sha256=artifact['sha256'],
                operator=operator,
                mission_id=mission_id,
                sensor_id=sensor_id,
                tags=tags,
                ingest_plan={"mode": "flows", "dpi": True} # Default plan
            )

            session_id = session.get('session_id', '')

            # 5. Link to collection task if provided
            task_linked = False
            if task_id:
                try:
                    from collection_tasks import CollectionTaskManager
                    engine = _get_engine()
                    mgr = CollectionTaskManager(engine)
                    task_linked = mgr.link_session_to_task(task_id, session_id)
                except Exception as e:
                    logger.warning(f"Failed to link session to task {task_id}: {e}")

            return jsonify({
                'status': 'ok',
                'artifact': artifact,
                'session': session,
                'task_linked': task_linked,
                'task_id': task_id if task_linked else None,
                'ingest_url': f"/api/pcap/{session_id}/ingest",
            })

        except Exception as e:
            logger.error(f"PCAP upload failed: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    # ── Batch PCAP Ingestion from FTP ────────────────────────────────
    @app.route('/api/pcap/batch_ingest', methods=['POST'])
    def pcap_batch_ingest():
        """Batch-ingest PCAPs from FTP → session hypergraphs.

        POST body (all optional):
            ftp_url: str — FTP server URL (default: ftp://172.234.197.23)
            session_window_sec: int — time bucket (default: 30)
            files: list[str] — specific PCAPs (default: all)
            skip_existing: bool — skip already-ingested (default: true)
        """
        try:
            from pcap_ingest import handle_mcp_pcap_ingest
            engine = _get_engine()
            ledger = None
            try:
                from inference_exhaustion_ledger import InferenceExhaustionLedger
                ledger = InferenceExhaustionLedger()
            except Exception:
                pass

            params = request.get_json(silent=True) or {}
            # Ensure staging directory is instance-local
            if 'staging_dir' not in params:
                staging = os.path.join(_data_dir(), 'pcaps')
                os.makedirs(staging, exist_ok=True)
                params['staging_dir'] = staging
            result = handle_mcp_pcap_ingest(engine, ledger, params)

            # ── Auto-run BSG detection after successful ingest ──────
            # BSGs are derived structure (cognitive compression), not
            # operator action — they run under SYSTEM:GRAPHOPS provenance.
            total_sessions = result.get('total_sessions', 0)
            if total_sessions > 0:
                try:
                    from behavior_groups import BehaviorGroupDetector, BSGConfig
                    detector = BehaviorGroupDetector(engine, BSGConfig())
                    bsg_result = detector.detect_from_graph()
                    result['bsg_auto'] = hard_json_clean(bsg_result.to_dict())
                    logger.info("[BSG] Auto-detection after batch ingest: %s",
                                bsg_result.summary())
                except Exception as bsg_err:
                    logger.warning("[BSG] Auto-detection failed (non-fatal): %s", bsg_err)
                    result['bsg_auto'] = {'error': str(bsg_err)}

            return jsonify(result)
        except Exception as e:
            logger.error(f"Batch PCAP ingest failed: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/pcap/list_ftp', methods=['GET'])
    def pcap_list_ftp():
        """List PCAP files available on the FTP server."""
        try:
            from pcap_ingest import handle_mcp_pcap_list
            ftp_url = request.args.get('ftp_url', 'ftp://172.234.197.23')
            result = handle_mcp_pcap_list({'ftp_url': ftp_url})
            return jsonify(result)
        except Exception as e:
            logger.error(f"PCAP list FTP failed: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    # ── Configurable FTP Ingest (operator-defined host/creds) ──────────
    @app.route('/api/ingest/ftp', methods=['POST'])
    def ingest_ftp_configurable():
        """Ingest PCAPs from an operator-specified FTP server.

        POST JSON:
            host: str       — FTP hostname or IP (REQUIRED)
            port: int       — FTP port (default: 21)
            username: str   — FTP username (default: "anonymous")
            password: str   — FTP password (default: "")
            path: str       — Remote directory path (default: "/")
            passive: bool   — Use passive mode (default: true)
            skip_existing: bool — Skip already-ingested files (default: true)
            dry_run: bool   — If true, list files only (default: false)
        """
        try:
            body = request.get_json(silent=True) or {}
            host = body.get('host', '').strip()
            if not host:
                return jsonify({'status': 'error', 'message': 'FTP host is required'}), 400

            port = int(body.get('port', 21))
            username = body.get('username', 'anonymous')
            password = body.get('password', '')
            remote_path = body.get('path', '/')
            passive = body.get('passive', True)
            skip_existing = body.get('skip_existing', True)
            dry_run = body.get('dry_run', False)

            # Sanitize — prevent path traversal in remote path
            if '..' in remote_path:
                return jsonify({'status': 'error', 'message': 'Path traversal not allowed'}), 400

            # Build FTP URL for pcap_ingest compatibility
            scheme = 'ftp'
            ftp_url = f"{scheme}://{host}:{port}{remote_path}"

            logger.info(f"FTP ingest request: host={host}, port={port}, "
                        f"user={username}, path={remote_path}, "
                        f"passive={passive}, dry_run={dry_run}")

            if dry_run:
                # List-only mode
                from pcap_ingest import handle_mcp_pcap_list
                result = handle_mcp_pcap_list({
                    'ftp_url': ftp_url,
                    'username': username,
                    'password': password,
                })
                result['dry_run'] = True
                return jsonify(result)

            # Full ingest
            from pcap_ingest import handle_mcp_pcap_ingest
            engine = _get_engine()
            ledger = None
            try:
                from inference_exhaustion_ledger import InferenceExhaustionLedger
                ledger = InferenceExhaustionLedger()
            except Exception:
                pass

            # Build instance-local staging directory
            staging = os.path.join(_data_dir(), 'pcaps')
            os.makedirs(staging, exist_ok=True)

            params = {
                'ftp_url': ftp_url,
                'username': username,
                'password': password,
                'skip_existing': skip_existing,
                'staging_dir': staging,
            }

            def _pcap_graph_counts(hg):
                ki = getattr(hg, 'kind_index', {}) or {}
                eki = getattr(hg, 'edge_kind_index', {}) or {}
                sessions = set(ki.get('session', set()) or set())
                sessions.update(ki.get('pcap_session', set()) or set())
                artifacts = set(ki.get('pcap_artifact', set()) or set())
                derived_edges = set(
                    eki.get('SESSION_DERIVED_FROM_PCAP', set()) or set()
                )
                return {
                    'graph_epoch': getattr(hg, 'trace_id', None),
                    'sessions': len(sessions),
                    'artifacts': len(artifacts),
                    'derived_edges': len(derived_edges),
                    'nodes_total': len(getattr(hg, 'nodes', {}) or {}),
                    'edges_total': len(getattr(hg, 'edges', {}) or {}),
                }

            result = handle_mcp_pcap_ingest(engine, ledger, params)
            result['ftp_host'] = host
            result['ftp_port'] = port
            graph_counts = _pcap_graph_counts(engine)
            result['graph_counts_after_ingest'] = graph_counts

            expected_sessions = int(result.get('total_sessions') or 0)
            result['pcap_graph_verify_ok'] = (
                expected_sessions <= 0 or graph_counts['sessions'] > 0
            )
            if expected_sessions > 0 and graph_counts['sessions'] == 0:
                message = (
                    f"PCAP ingest sessionized {expected_sessions} sessions but "
                    "the graph has 0 session nodes after materialization"
                )
                logger.error("[PCAP][VERIFY] %s. graph_counts=%s",
                             message, graph_counts)
                warnings = result.get('warnings') or []
                if not isinstance(warnings, list):
                    warnings = [str(warnings)]
                warnings.append(
                    "Graph materialization is empty after PCAP ingest; "
                    "check WriteBus idempotency replay and graph binding."
                )
                result['warnings'] = warnings
                if result.get('status') == 'ok':
                    result['status'] = 'partial_failure'

            # ── Mirror ingest to InstanceDB (Postgres/SQLite authority) ──
            if instance_db and hasattr(instance_db, 'mirror_ingest_result'):
                try:
                    mirror_summary = instance_db.mirror_ingest_result(result, engine)
                    result['db_mirror'] = mirror_summary
                    logger.info(f"[InstanceDB] Ingest mirrored: {mirror_summary}")
                except Exception as db_err:
                    logger.warning(f"[InstanceDB] Ingest mirroring failed: {db_err}")

            # ── Auto-run BSG detection after successful ingest ──────
            total_sessions = result.get('total_sessions', 0)
            if total_sessions > 0:
                try:
                    from behavior_groups import BehaviorGroupDetector, BSGConfig
                    detector = BehaviorGroupDetector(engine, BSGConfig())
                    bsg_result = detector.detect_from_graph()
                    result['bsg_auto'] = hard_json_clean(bsg_result.to_dict())
                    logger.info("[BSG] Auto-detection after FTP ingest: %s",
                                bsg_result.summary())
                except Exception as bsg_err:
                    logger.warning("[BSG] Auto-detection failed (non-fatal): %s", bsg_err)
                    result['bsg_auto'] = {'error': str(bsg_err)}

            return jsonify(result)

        except Exception as e:
            logger.error(f"Configurable FTP ingest failed: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/pcap/session_summary', methods=['GET'])
    def pcap_session_summary():
        """Summarize ingested sessions: protocols, hosts, time range."""
        try:
            from pcap_ingest import handle_mcp_session_summary
            engine = _get_engine()
            result = handle_mcp_session_summary(engine)
            return jsonify(result)
        except Exception as e:
            logger.error(f"Session summary failed: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500


    # ── Remote stream status (for UI) ──────────────────────────────
    @app.route('/api/stream/list', methods=['GET'])
    def list_remote_streams():
        """Return a list of currently connected remote stream endpoints + queue health."""
        try:
            from stream_manager import remote_stream_manager
            import live_ingest as _li
            endpoints = list(remote_stream_manager.connections.keys())
            return jsonify({
                "endpoints": endpoints,
                "queue": _li.live_event_queue.stats,
            })
        except Exception as e:
            logger.error(f"Failed to list remote streams: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/pcap/ftp_sessions', methods=['GET'])
    def pcap_ftp_sessions():
        """List sessions from the hypergraph, grouped by PCAP artifact.

        Graph-native: queries the engine's kind index directly.
        If the graph has sessions, this endpoint WILL return them.
        Never depends on registries, caches, or stale flags.

        Returns pcap_artifacts (source files) each with their sessions.
        Sessions have kind=session and provenance.source=pcap_ingest.
        """
        try:
            # ── Prefer InstanceDB (Postgres/SQLite authority) if available ──
            if instance_db and hasattr(instance_db, 'sessions_grouped_by_artifact'):
                try:
                    logger.info("[FTP-sessions] Using InstanceDB for session listing")
                    artifacts = instance_db.sessions_grouped_by_artifact()
                    return jsonify({
                        'ok': True,
                        'pcap_count': len(artifacts),
                        'session_count': sum(a.get('session_count', 0) for a in artifacts),
                        'host_count': 0,  # Not tracked in DB yet
                        'artifacts': artifacts,
                        'geo_points': [],
                        'dpi_summary': {},
                    })
                except Exception as db_list_err:
                    logger.warning(f"[FTP-sessions] InstanceDB listing failed; falling back to graph: {db_list_err}")

            # ── Fallback: graph-native logic ──
            hg = _get_engine()
            _nodes = hg.nodes or {}
            _edges = hg.edges or {}
            pcap_artifacts = {}
            sessions = {}
            hosts = set()
            _skipped = 0

            # ── 1a. Session nodes ──
            for sid in list(getattr(hg, 'kind_index', {}).get('session', [])) + \
                       list(getattr(hg, 'kind_index', {}).get('pcap_session', [])):
                node = _nodes.get(sid)
                if not node:
                    continue
                try:
                    nd = _safe_nd(node)
                    labels = nd.get('labels', {}) or {}
                    meta = nd.get('metadata', {}) or {}
                    prov = meta.get('provenance', {}) or {}
                    proto = labels.get('proto')
                    if not proto:
                        proto_list = labels.get('protocols', [])
                        proto = proto_list[0] if isinstance(proto_list, list) and proto_list else ''
                    sessions[sid] = {
                        'session_id': sid,
                        'proto': proto or 'UNK',
                        'src_ip': labels.get('src_ip'),
                        'dst_ip': labels.get('dst_ip'),
                        'src_port': labels.get('src_port'),
                        'dst_port': labels.get('dst_port'),
                        'packet_count': labels.get('packet_count', 0),
                        'total_bytes': labels.get('total_bytes', 0),
                        'duration_sec': labels.get('duration_sec', 0),
                        'tcp_flags': labels.get('tcp_flags', []),
                        'time_bucket': labels.get('time_bucket', 0),
                        'pcap_file': prov.get('pcap_file') or labels.get('pcap_file'),
                        'metadata': meta,
                    }
                except Exception as sess_err:
                    logger.warning("[FTP-sessions] Skipping session node %s: %s", sid, sess_err)
                    _skipped += 1

            # ── 1b. Artifact nodes ──
            for aid in getattr(hg, 'kind_index', {}).get('pcap_artifact', []):
                node = _nodes.get(aid)
                if not node:
                    continue
                try:
                    nd = _safe_nd(node)
                    labels = nd.get('labels', {}) or {}
                    meta = nd.get('metadata', {}) or {}
                    pcap_artifacts[aid] = {
                        'id': aid,
                        'filename': labels.get('filename') or labels.get('name') or aid,
                        'file_size': labels.get('file_size') or labels.get('size') or 0,
                        'ingested_at': labels.get('ingested_at') or meta.get('created_at') or '',
                        'sessions': [],
                    }
                except Exception as art_err:
                    logger.warning("[FTP-sessions] Skipping artifact node %s: %s", aid, art_err)
                    _skipped += 1

            # Also catch PCAP:-prefixed artifacts not in kind_index
            for nid in _nodes:
                if nid.startswith('PCAP:') and nid not in pcap_artifacts:
                    node = _nodes[nid]
                    try:
                        nd = _safe_nd(node)
                        kind = nd.get('kind', '')
                        if kind == 'pcap_artifact':
                            labels = nd.get('labels', {}) or {}
                            meta = nd.get('metadata', {}) or {}
                            pcap_artifacts[nid] = {
                                'id': nid,
                                'filename': labels.get('filename') or labels.get('name') or nid,
                                'file_size': labels.get('file_size') or labels.get('size') or 0,
                                'ingested_at': labels.get('ingested_at') or meta.get('created_at') or '',
                                'sessions': [],
                            }
                    except Exception:
                        pass

            # ── 1c. Host nodes ──
            for hid in getattr(hg, 'kind_index', {}).get('host', []):
                node = _nodes.get(hid)
                if not node:
                    continue
                try:
                    nd = _safe_nd(node)
                    labels = nd.get('labels', {}) or {}
                    hosts.add(labels.get('ip') or hid.replace('host_', '').replace('host:', ''))
                except Exception:
                    pass

            logger.info("[FTP-sessions] Discovered: %d sessions, %d artifacts, "
                        "%d hosts (skipped %d)", len(sessions),
                        len(pcap_artifacts), len(hosts), _skipped)

            # ── Phase 2: Link sessions → artifacts via edges ──
            linked_sessions = set()

            # Use edge_kind_index for O(1) lookup instead of scanning all edges
            _link_edge_kinds = ('SESSION_DERIVED_FROM_PCAP', 'SESSION_HAS_ARTIFACT')
            for ekind in _link_edge_kinds:
                for eid in getattr(hg, 'edge_kind_index', {}).get(ekind, []):
                    edge = _edges.get(eid)
                    if not edge:
                        continue
                    try:
                        ed = edge.to_dict() if hasattr(edge, 'to_dict') else (
                            edge if isinstance(edge, dict) else {})
                        enodes = ed.get('nodes', [])
                        if len(enodes) >= 2:
                            sid, aid = enodes[0], enodes[1]
                            if aid in pcap_artifacts and sid in sessions:
                                pcap_artifacts[aid]['sessions'].append(sessions[sid])
                                linked_sessions.add(sid)
                    except Exception as edge_err:
                        logger.warning("[FTP-sessions] Skipping edge %s: %s", eid, edge_err)

            # ── Phase 3: Group unlinked sessions by pcap_file ──
            unlinked_count = 0
            for sid, sdata in sessions.items():
                if sid in linked_sessions:
                    continue
                unlinked_count += 1
                pcap_file = sdata.get('pcap_file') or 'unknown'
                synth_aid = f'PCAP:{pcap_file}'
                if synth_aid not in pcap_artifacts:
                    pcap_artifacts[synth_aid] = {
                        'id': synth_aid,
                        'filename': pcap_file or 'Ingested Sessions',
                        'file_size': 0,
                        'ingested_at': '',
                        'sessions': [],
                    }
                pcap_artifacts[synth_aid]['sessions'].append(sdata)

            if unlinked_count > 0:
                logger.info("[FTP-sessions] %d sessions grouped by pcap_file "
                            "(no artifact edge)", unlinked_count)

            # ── Phase 4: Final assembly ──
            # Remove empty artifacts (no sessions attached)
            result_artifacts = [a for a in pcap_artifacts.values()
                                if len(a.get('sessions', [])) > 0]
            result_artifacts.sort(key=lambda a: a.get('filename', ''))
            for art in result_artifacts:
                art['session_count'] = len(art['sessions'])
                art['sessions'].sort(key=lambda s: s.get('time_bucket', 0))

            # ── Phase 5: Collect geo_points from host nodes ──
            geo_points = []
            dpi_summary = {"dns_names": 0, "tls_snis": 0, "http_hosts": 0}

            for hid in getattr(hg, 'kind_index', {}).get('host', []):
                node = _nodes.get(hid)
                if not node:
                    continue
                try:
                    nd = _safe_nd(node)
                    labels = nd.get('labels', {}) or {}
                    pos = nd.get('position', None)
                    if pos and len(pos) >= 2 and pos[0] is not None:
                        ip = labels.get('ip', hid.replace('host_', '').replace('host:', ''))
                        geo_points.append({
                            'ip': ip,
                            'lat': pos[0], 'lon': pos[1],
                            'city': labels.get('city', ''),
                            'country': labels.get('country', ''),
                            'org': labels.get('org', ''),
                            'bytes': labels.get('bytes', 0),
                        })
                except Exception:
                    pass

            # DPI node counts
            for dpi_kind, dpi_key in (('dns_name', 'dns_names'),
                                       ('tls_sni', 'tls_snis'),
                                       ('http_host', 'http_hosts')):
                dpi_summary[dpi_key] = len(
                    getattr(hg, 'kind_index', {}).get(dpi_kind, []))

            # Geo from HOST_GEO_ESTIMATE edges (hosts without inline position)
            seen_ips = {gp['ip'] for gp in geo_points}
            for eid in getattr(hg, 'edge_kind_index', {}).get(
                    'HOST_GEO_ESTIMATE', []):
                edge = _edges.get(eid)
                if not edge:
                    continue
                try:
                    ed = edge.to_dict() if hasattr(edge, 'to_dict') else (
                        edge if isinstance(edge, dict) else {})
                    enodes = ed.get('nodes', [])
                    if len(enodes) < 2:
                        continue
                    host_id, geo_id = enodes[0], enodes[1]
                    gn = _nodes.get(geo_id)
                    hn = _nodes.get(host_id)
                    if not gn or not hn:
                        continue
                    gnd = _safe_nd(gn)
                    gpos = gnd.get('position', [])
                    if not gpos or len(gpos) < 2:
                        continue
                    hnd = _safe_nd(hn)
                    hlabels = hnd.get('labels', {}) or {}
                    glabels = gnd.get('labels', {}) or {}
                    ip = hlabels.get('ip', host_id.replace(
                        'host_', '').replace('host:', ''))
                    if ip not in seen_ips:
                        seen_ips.add(ip)
                        geo_points.append({
                            'ip': ip,
                            'lat': gpos[0], 'lon': gpos[1],
                            'city': glabels.get('city', ''),
                            'country': glabels.get('country', ''),
                            'org': hlabels.get('org', ''),
                            'bytes': hlabels.get('bytes', 0),
                        })
                except Exception:
                    pass

            geo_points.sort(key=lambda g: g.get('bytes', 0), reverse=True)

            logger.info("[FTP-sessions] Returning: %d artifacts, %d sessions, "
                        "%d hosts, %d geo_points",
                        len(result_artifacts), len(sessions),
                        len(hosts), len(geo_points))

            # Clean response to avoid circular reference errors
            response_data = {
                'ok': True,
                'pcap_count': len(result_artifacts),
                'session_count': len(sessions),
                'host_count': len(hosts),
                'artifacts': result_artifacts,
                'geo_points': geo_points,
                'dpi_summary': dpi_summary,
            }

            # Use hard_json_clean to remove circular references
            response_data = hard_json_clean(response_data)
            return jsonify(response_data)
        except Exception as e:
            logger.error("[FTP-sessions] Endpoint failed: %s", e, exc_info=True)
            return jsonify({'ok': False, 'error': str(e)}), 500

    # ── Behavioral Session Groups (BSG) ──────────────────────────────────

    @app.route('/api/pcap/behavior_groups', methods=['GET', 'POST'])
    def pcap_behavior_groups():
        """Detect or retrieve Behavioral Session Groups.

        GET:  Return existing behavior_group nodes from the hypergraph.
        POST: Run BSG detection on all sessions in the hypergraph.

        POST body (all optional):
            beacon_min_sessions: int — min sessions for beacon detection (default: 3)
            scan_min_ports: int — min ports for scan detection (default: 10)
            exfil_min_bytes: int — min bytes for exfil detection (default: 10000)

        Returns:
            groups: list of BSG objects with behavior, confidence, members
            summary: human-readable summary
            by_behavior: counts per behavior type
        """
        try:
            hg = _get_engine()
        except RuntimeError:
            # No engine yet — return empty (not 500)
            return jsonify({
                'ok': True,
                'groups': [],
                'group_count': 0,
                'by_behavior': {},
                'message': 'HypergraphEngine not initialized — no data to analyze',
            })

        try:
            hg = _get_engine()
        except RuntimeError:
            # No engine yet — return empty (not 500)
            return jsonify({
                'ok': True,
                'groups': [],
                'group_count': 0,
                'by_behavior': {},
                'message': 'HypergraphEngine not initialized — no data to analyze',
            })

        try:
            if request.method == 'GET':
                # Prefer authoritative projection from InstanceDB if available.
                try:
                    if 'instance_db' in globals() and instance_db and hasattr(instance_db, 'list_bsg_projection'):
                        envelope = instance_db.list_bsg_projection()
                        return jsonify({'ok': True, **envelope})
                except Exception:
                    # Fall through to safe empty projection below
                    logger.warning('InstanceDB projection unavailable; returning empty projection')

                # If InstanceDB is not available, return an empty canonical projection
                empty_proj = {
                    'bsg_projection_version': '1.0',
                    'instance_id': getattr(globals().get('instance_id', None), 'instance_id', 'unknown'),
                    'generated_at': None,
                    'evidence_summary': {
                        'sessions_total': 0,
                        'sessions_grouped': 0,
                        'groups_total': 0,
                        'coverage_pct': 0.0,
                    },
                    'groups': [],
                    'constraints': {
                        'no_geo_inference': True,
                        'no_actor_attribution': True,
                        'no_intent_certainty': True,
                    },
                }
                return jsonify({'ok': True, **empty_proj})

            else:
                # POST: Run BSG detection
                from behavior_groups import handle_mcp_bsg_detect
                params = request.get_json(silent=True) or {}
                result = handle_mcp_bsg_detect(hg, params)
                cleaned = hard_json_clean(result) if result is not None else {}
                if not isinstance(cleaned, dict):
                    cleaned = {'result': cleaned}
                return jsonify({
                    'ok': True,
                    **cleaned,
                })

        except Exception as e:
            logger.error(f"Behavior groups failed: {e}")
            return jsonify({'ok': False, 'error': str(e)}), 200

    # ── BSG Health / Lifecycle Status ────────────────────────────────────
    @app.route('/api/pcap/behavior_groups/status', methods=['GET'])
    def pcap_bsg_status():
        """BSG lifecycle status: what ran, what failed, what's pending.

        Returns per-detector health so the UI can show:
          ✔ Beaconing: 3 groups
          ⚠ Exfiltration: inconclusive (low volume variance)
          ℹ Failed Handshakes: not detected
        instead of a binary "no groups / has groups" state.
        """
        try:
            hg = _get_engine()
        except RuntimeError:
            return jsonify({
                'ok': True,
                'state': 'NO_ENGINE',
                'session_count': 0,
                'detectors': {},
                'message': 'HypergraphEngine not yet initialized',
            })

        # Count sessions and existing BSG nodes
        session_count = 0
        bsg_by_behavior = {}
        for nid, node in (getattr(hg, 'nodes', None) or {}).items():
            try:
                nd = node.to_dict() if hasattr(node, 'to_dict') else (
                    node if isinstance(node, dict) else {})
                kind = nd.get('kind', '')
                if kind == 'session':
                    session_count += 1
                elif kind == 'behavior_group':
                    labels = nd.get('labels', {}) or {}
                    behavior = labels.get('behavior', 'UNKNOWN')
                    if behavior not in bsg_by_behavior:
                        bsg_by_behavior[behavior] = {
                            'count': 0, 'state': 'COMPLETE',
                            'total_members': 0, 'max_confidence': 0,
                        }
                    bsg_by_behavior[behavior]['count'] += 1
                    bsg_by_behavior[behavior]['total_members'] += labels.get('member_count', 0)
                    bsg_by_behavior[behavior]['max_confidence'] = max(
                        bsg_by_behavior[behavior]['max_confidence'],
                        labels.get('confidence', 0),
                    )
            except Exception:
                continue

        # Determine per-detector status for ALL 5 behaviors
        _ALL_BEHAVIORS = ['BEACON', 'PORT_SCAN', 'HORIZ_SCAN',
                          'FAILED_HANDSHAKE', 'DATA_EXFIL']
        detectors = {}
        for behavior in _ALL_BEHAVIORS:
            if behavior in bsg_by_behavior:
                info = bsg_by_behavior[behavior]
                detectors[behavior] = {
                    'state': 'COMPLETE',
                    'groups': info['count'],
                    'members': info['total_members'],
                    'max_confidence': round(info['max_confidence'], 2),
                }
            elif session_count == 0:
                detectors[behavior] = {
                    'state': 'PENDING',
                    'groups': 0,
                    'reason': 'No sessions ingested yet',
                }
            else:
                detectors[behavior] = {
                    'state': 'NOT_DETECTED',
                    'groups': 0,
                    'reason': 'No matching patterns found in ingested sessions',
                }

        # Overall state
        completed = sum(1 for d in detectors.values() if d['state'] == 'COMPLETE')
        total_groups = sum(d.get('groups', 0) for d in detectors.values())
        if session_count == 0:
            overall = 'PENDING'
        elif completed == 0:
            overall = 'NOT_DETECTED'
        elif completed < len(_ALL_BEHAVIORS):
            overall = 'PARTIAL'
        else:
            overall = 'COMPLETE'

        return jsonify({
            'ok': True,
            'state': overall,
            'session_count': session_count,
            'total_groups': total_groups,
            'completed_detectors': completed,
            'total_detectors': len(_ALL_BEHAVIORS),
            'detectors': detectors,
        })

    # ====================================================================
    # GRAPHOPS TUTORIAL MODE — Pre-Evidence Cognitive Copilot
    # ====================================================================
    #
    # Tutorial Mode computes a T-state (T0–T7) representing epistemic
    # readiness.  GraphOps uses this to explain capability before evidence,
    # structure after evidence, and intent only when justified.
    #
    # T0  INIT_EMPTY       No engine, fresh boot
    # T1  ENGINE_READY     Engine up, zero nodes
    # T2  AWAITING_INGEST  Engine + tools enumerated, no data yet
    # T3  INGEST_ACTIVE    Evidence arriving — nodes present, 0 sessions
    # T4  SESSIONS_PRESENT Sessions exist, BSG analysis pending
    # T5  BSG_PARTIAL      Some BSG detectors completed
    # T6  BSG_COMPLETE     All detectors ran
    # T7  INFERENCE_READY  Full analysis available (BSGs + system principal)
    # ====================================================================

    @app.route('/api/graphops/dag', methods=['POST'])
    def graphops_dag_run():
        """Execute a GraphOps MCP→gRPC execution DAG.

        Accepts a JSON IR payload::

            {
              "graph": [
                {"id": "decompose", "op": "cluster.decompose",
                 "input": {"cluster_id": "C-8831"}},
                {"id": "intent",    "op": "tak.infer",
                 "input": {"from": "decompose"}}
              ],
              "return": ["intent"],
              "options": {"cache_ttl": 30, "timeout_s": 20}
            }

        Requires X-Session-Token header or ?token= query param.
        """
        # ── Auth ──────────────────────────────────────────────────────────────
        token = (request.headers.get('X-Session-Token')
                 or request.args.get('token')
                 or request.headers.get('Authorization', '').replace('Bearer ', ''))
        if not token or not operator_manager:
            return jsonify({'error': 'Session token required'}), 401
        op = operator_manager.get_operator_for_session(token)
        if not op:
            return jsonify({'error': 'Invalid or expired session token'}), 401

        payload = request.get_json(silent=True)
        if not payload or 'graph' not in payload:
            return jsonify({'error': 'Missing "graph" key in request body'}), 400

        # ── Build DAGContext from operator session ────────────────────────────
        try:
            from graphops_dag_compiler import DAGContext, run_dag_sync
        except ImportError as exc:
            return jsonify({'error': f'DAG compiler unavailable: {exc}'}), 503

        instance_id   = app.config.get('INSTANCE_ID', '')
        operator_role = getattr(op, 'role', 'analyst')
        scopes: list  = getattr(op, 'scopes', None) or []
        # Map common roles to default scopes when session carries none
        if not scopes:
            role_defaults = {
                'admin':    ['rf', 'cluster', 'tak', 'hypergraph', 'stream'],
                'operator': ['rf', 'cluster', 'tak'],
                'analyst':  ['cluster'],
            }
            scopes = role_defaults.get(operator_role, ['cluster'])

        ctx = DAGContext(
            instance_id  = instance_id,
            operator_id  = str(getattr(op, 'id', '') or getattr(op, 'username', '')),
            scopes       = scopes,
            strict_mode  = True,
        )

        # ── Build gRPC channel ────────────────────────────────────────────────
        try:
            import grpc
            from graphops_dag_compiler import build_grpc_stubs
            internal_token = app.config.get('INTERNAL_TOKEN', '')
            grpc_host      = app.config.get('GRPC_HOST', '127.0.0.1')
            grpc_port      = app.config.get('GRPC_PORT', 50051)
            channel        = grpc.insecure_channel(f'{grpc_host}:{grpc_port}')
            stubs          = build_grpc_stubs(channel)
        except Exception as exc:
            stubs = {}
            logger.warning('[DAG] gRPC channel unavailable: %s', exc)

        extra: dict = {'_stubs': stubs, '_internal_token': internal_token}

        # ── Execute ───────────────────────────────────────────────────────────
        try:
            result = run_dag_sync(payload, ctx, extra_handlers=extra)
            return jsonify({'ok': True, 'result': result})
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
        except Exception as exc:
            logger.exception('[DAG] Execution error')
            return jsonify({'error': f'DAG execution failed: {exc}'}), 500

    @app.route('/api/graphops/tutorial', methods=['GET'])
    def graphops_tutorial():
        """Compute current Tutorial Mode T-state and return guidance.

        Returns:
            state:          T0–T7 label
            state_id:       Integer 0–7
            title:          Human-readable state name
            system_message: SYSTEM prompt fragment for LLM context
            guidance:       Plain-text operator guidance
            suggestions:    Actionable next-step suggestions list
            capabilities:   Tool availability map
            bsg_health:     BSG detector summary (if applicable)
        """
        try:
            # ── Gather system state ──
            hg = None
            try:
                hg = _get_engine()
            except Exception:
                pass

            has_engine = hg is not None
            node_count = len(hg.nodes) if has_engine and hasattr(hg, 'nodes') and hg.nodes else 0
            edge_count = len(hg.edges) if has_engine and hasattr(hg, 'edges') and hg.edges else 0

            session_count = 0
            bsg_count = 0
            bsg_by_behavior = {}
            if has_engine and hasattr(hg, 'nodes') and hg.nodes:
                for _nid, nd_obj in hg.nodes.items():
                    try:
                        nd = nd_obj.to_dict() if hasattr(nd_obj, 'to_dict') else (
                            nd_obj if isinstance(nd_obj, dict) else {})
                        kind = nd.get('kind', '')
                        if kind in ('session', 'pcap_session'):
                            session_count += 1
                        elif kind == 'behavior_group':
                            bsg_count += 1
                            labels = nd.get('labels', {}) or {}
                            behavior = labels.get('behavior', 'UNKNOWN')
                            bsg_by_behavior[behavior] = bsg_by_behavior.get(behavior, 0) + 1
                    except Exception:
                        continue

            # ── Capability awareness ──
            _caps = {}
            try:
                _caps['nmap'] = bool(nmap_scanner.check_nmap_available()) if nmap_scanner else False
            except Exception:
                _caps['nmap'] = False
            try:
                _caps['ndpi'] = bool(ndpi_analyzer.check_ndpi_available()) if ndpi_analyzer else False
            except Exception:
                _caps['ndpi'] = False
            try:
                _caps['ais'] = bool(ais_tracker.csv_loaded) if ais_tracker else False
            except Exception:
                _caps['ais'] = False
            try:
                _caps['recon'] = bool(recon_system and len(recon_system.entities) > 0)
            except Exception:
                _caps['recon'] = False
            _caps['bsg_engine'] = True  # Always available (behavior_groups.py)
            try:
                _caps['geolocation'] = bool(hasattr(hg, 'nodes') and any(
                    (nd.to_dict() if hasattr(nd, 'to_dict') else nd).get('kind') == 'geoip'
                    for nd in (hg.nodes or {}).values()
                )) if has_engine else False
            except Exception:
                _caps['geolocation'] = False
            try:
                from tak_ml_gemma_runner import GraphOpsChatBot
                _caps['llm'] = True
            except ImportError:
                _caps['llm'] = False

            # ── System principal status ──
            sys_principal_active = False
            try:
                from principals import SystemPrincipalRegistry
                reg = SystemPrincipalRegistry.get_instance()
                gops_principal = reg.get('SYSTEM:GRAPHOPS')
                sys_principal_active = gops_principal is not None
            except Exception:
                pass

            # ── BSG detector health (compact) ──
            _ALL_BEHAVIORS = ['BEACON', 'PORT_SCAN', 'HORIZ_SCAN',
                              'FAILED_HANDSHAKE', 'DATA_EXFIL']
            bsg_health = {}
            for beh in _ALL_BEHAVIORS:
                if beh in bsg_by_behavior:
                    bsg_health[beh] = {'state': 'COMPLETE', 'groups': bsg_by_behavior[beh]}
                elif session_count == 0:
                    bsg_health[beh] = {'state': 'PENDING'}
                else:
                    bsg_health[beh] = {'state': 'NOT_DETECTED'}

            bsg_completed = sum(1 for v in bsg_health.values() if v['state'] == 'COMPLETE')

            # ── Compute T-state ──
            if not has_engine:
                t_state, t_id = 'INIT_EMPTY', 0
            elif node_count == 0 and session_count == 0:
                # Engine ready but empty — check if tools are enumerated
                any_cap = any(_caps.get(k) for k in ['nmap', 'ndpi', 'ais', 'recon'])
                if any_cap:
                    t_state, t_id = 'AWAITING_INGEST', 2
                else:
                    t_state, t_id = 'ENGINE_READY', 1
            elif session_count == 0:
                t_state, t_id = 'INGEST_ACTIVE', 3
            elif bsg_count == 0:
                t_state, t_id = 'SESSIONS_PRESENT', 4
            elif bsg_completed < len(_ALL_BEHAVIORS):
                t_state, t_id = 'BSG_PARTIAL', 5
            elif sys_principal_active:
                t_state, t_id = 'INFERENCE_READY', 7
            else:
                t_state, t_id = 'BSG_COMPLETE', 6

            # ── State metadata: title, system_message, guidance, suggestions ──
            _T_META = {
                0: {
                    'title': 'Initializing',
                    'system_message': (
                        'You are GraphOps, an RF intelligence copilot. '
                        'The hypergraph engine is not yet initialized. '
                        'You cannot answer data questions. '
                        'Explain what RF SCYTHE does and what the operator '
                        'should expect when the system comes online.'
                    ),
                    'guidance': (
                        'The hypergraph engine is starting up. '
                        'Once initialized, you can ingest PCAP files or '
                        'FTP session data to begin analysis.'
                    ),
                    'suggestions': [
                        'Wait for engine initialization',
                        'Check server logs for startup errors',
                    ],
                },
                1: {
                    'title': 'Engine Ready',
                    'system_message': (
                        'You are GraphOps, an RF intelligence copilot. '
                        'The hypergraph engine is online with zero nodes. '
                        'No evidence has been ingested. '
                        'Do NOT fabricate data or analysis. '
                        'Explain the intelligence cycle: ingest → structure → detect → infer. '
                        'Guide the operator to ingest their first data source.'
                    ),
                    'guidance': (
                        'Engine is live but empty. '
                        'Ingest a PCAP capture or connect to an FTP source '
                        'to populate the hypergraph with evidence.'
                    ),
                    'suggestions': [
                        'Upload a PCAP file for analysis',
                        'Connect to an FTP data source',
                        'Ask: "What data formats can SCYTHE ingest?"',
                    ],
                },
                2: {
                    'title': 'Awaiting Ingest',
                    'system_message': (
                        'You are GraphOps, an RF intelligence copilot. '
                        'The engine is online, no evidence ingested yet. '
                        'Available tools have been detected. '
                        'Do NOT fabricate data. '
                        'Explain available capabilities and guide toward ingest. '
                        'If the operator asks "what can you do?", list available tools.'
                    ),
                    'guidance': (
                        'Engine is ready and analysis tools are available. '
                        'Ingest data to begin the intelligence cycle.'
                    ),
                    'suggestions': [
                        'Upload a PCAP file',
                        'Use "Ingest FTP" to pull session data',
                        'Ask: "What tools are available?"',
                        'Ask: "What should I capture?"',
                    ],
                },
                3: {
                    'title': 'Ingest Active',
                    'system_message': (
                        'You are GraphOps, an RF intelligence copilot. '
                        'Evidence nodes are arriving in the hypergraph but no '
                        'sessions have been structured yet. '
                        'Data is being processed. '
                        'Explain that structuring is in progress and '
                        'sessions will appear once parsing completes.'
                    ),
                    'guidance': (
                        'Data is being ingested and processed. '
                        'Session structuring is in progress — '
                        'sessions will appear once parsing completes.'
                    ),
                    'suggestions': [
                        'Wait for session structuring to complete',
                        'Check ingest progress in the console',
                        'Ask: "How does session detection work?"',
                    ],
                },
                4: {
                    'title': 'Sessions Present',
                    'system_message': (
                        'You are GraphOps, an RF intelligence copilot. '
                        'The hypergraph contains structured sessions. '
                        'Behavioral analysis (BSG) has not yet run. '
                        'You can answer basic session questions. '
                        'Explain the BSG detection pipeline and what it looks for: '
                        'beaconing, port scanning, horizontal scanning, '
                        'failed handshakes, and data exfiltration.'
                    ),
                    'guidance': (
                        'Sessions are structured in the hypergraph. '
                        'Run BSG behavioral analysis to detect patterns '
                        'like beaconing, scanning, and exfiltration.'
                    ),
                    'suggestions': [
                        'Run behavioral group detection',
                        'Browse the session list',
                        'Ask: "What types of behavior can SCYTHE detect?"',
                        'Ask: "How many sessions are loaded?"',
                    ],
                },
                5: {
                    'title': 'BSG Partial',
                    'system_message': (
                        'You are GraphOps, an RF intelligence copilot. '
                        'Behavioral analysis is partially complete. '
                        'Some detectors have results, others are pending or found nothing. '
                        'You can answer questions about detected behaviors. '
                        'Note which detectors completed and which did not.'
                    ),
                    'guidance': (
                        'Some behavioral detectors have completed analysis. '
                        'Review detected patterns and investigate flagged sessions.'
                    ),
                    'suggestions': [
                        'Review detected behavioral groups',
                        'Investigate flagged sessions',
                        'Ask: "Which behaviors were detected?"',
                        'Ask: "Which detectors found nothing?"',
                    ],
                },
                6: {
                    'title': 'BSG Complete',
                    'system_message': (
                        'You are GraphOps, an RF intelligence copilot. '
                        'All 5 behavioral detectors have completed. '
                        'Full structural analysis is available. '
                        'You can answer detailed questions about behavioral groups, '
                        'member sessions, confidence scores, and detection patterns. '
                        'If the system principal is not active, note that '
                        'bounded inference is not yet authorized.'
                    ),
                    'guidance': (
                        'All behavioral detectors have run. '
                        'Full analysis is available — explore behavioral '
                        'groups, confidence scores, and member sessions.'
                    ),
                    'suggestions': [
                        'Explore behavioral groups in detail',
                        'Ask: "Summarize all detected behaviors"',
                        'Ask: "Which sessions have the highest threat score?"',
                        'View session hypergraph topology',
                    ],
                },
                7: {
                    'title': 'Inference Ready',
                    'system_message': (
                        'You are GraphOps, an RF intelligence copilot. '
                        'Full analysis pipeline complete. System principal active. '
                        'You can answer any question about the hypergraph, '
                        'behavioral groups, sessions, hosts, and topology. '
                        'Provide evidence-backed answers with node/edge references. '
                        'Never fabricate data — all answers must be derived from '
                        'the hypergraph.'
                    ),
                    'guidance': (
                        'Full intelligence cycle complete. '
                        'System principal active — bounded inference authorized. '
                        'All queries are evidence-backed.'
                    ),
                    'suggestions': [
                        'Ask any analytical question',
                        'Ask: "What is the most significant threat?"',
                        'Ask: "Show me the network topology"',
                        'Export analysis report',
                    ],
                },
            }

            meta = _T_META.get(t_id, _T_META[0])

            # ── BSG Health → Suggestions matrix ──
            bsg_suggestions = []
            if t_id >= 5:
                for beh, health in bsg_health.items():
                    if health['state'] == 'COMPLETE':
                        bsg_suggestions.append(
                            f'Investigate {beh} groups ({health.get("groups", 0)} detected)')
                    elif health['state'] == 'NOT_DETECTED':
                        bsg_suggestions.append(
                            f'{beh}: no patterns found — consider more data')

            # ── Capability badges ──
            cap_labels = {
                'nmap': 'Network Scanner (nmap)',
                'ndpi': 'Deep Packet Inspection (nDPI)',
                'ais': 'AIS Maritime Tracking',
                'recon': 'Reconnaissance System',
                'bsg_engine': 'Behavioral Group Detection',
                'geolocation': 'GeoIP Enrichment',
                'llm': 'LLM Inference (Ollama)',
            }
            cap_display = []
            for k, available in _caps.items():
                cap_display.append({
                    'tool': k,
                    'label': cap_labels.get(k, k),
                    'available': available,
                })

            return jsonify({
                'ok': True,
                'state': t_state,
                'state_id': t_id,
                'title': meta['title'],
                'system_message': meta['system_message'],
                'guidance': meta['guidance'],
                'suggestions': meta['suggestions'] + bsg_suggestions,
                'capabilities': cap_display,
                'bsg_health': bsg_health if t_id >= 4 else None,
                'metrics': {
                    'node_count': node_count,
                    'edge_count': edge_count,
                    'session_count': session_count,
                    'bsg_count': bsg_count,
                    'bsg_completed_detectors': bsg_completed,
                },
                'system_principal_active': sys_principal_active,
            })
        except Exception as e:
            logger.error(f'[graphops-tutorial] Error computing T-state: {e}')
            return jsonify({
                'ok': False,
                'state': 'INIT_EMPTY',
                'state_id': 0,
                'error': str(e),
            }), 500

    @app.route('/api/pcap/behavior_groups/<bsg_id>/members', methods=['GET'])
    def pcap_bsg_members(bsg_id):
        """Get member sessions for a specific BSG.

        Returns all sessions linked via SESSION_MEMBER_OF_BEHAVIOR_GROUP edges.
        Supports LOD levels via ?lod= parameter:
            strategic: BSG summary only (no members)
            tactical:  BSG + representative sample sessions
            forensic:  BSG + ALL member sessions with full detail
        """
        try:
            hg = _get_engine()
        except RuntimeError:
            return jsonify({'ok': False, 'error': f'BSG {bsg_id} not found (no engine)'}), 404

        try:
            lod = request.args.get('lod', 'tactical')

            # Find the BSG node
            bsg_node = (hg.nodes or {}).get(bsg_id)
            if not bsg_node:
                return jsonify({'ok': False, 'error': f'BSG {bsg_id} not found'}), 404

            bsg_nd = bsg_node.to_dict() if hasattr(bsg_node, 'to_dict') else bsg_node
            bsg_labels = bsg_nd.get('labels', {}) or {}

            bsg_info = {
                'bsg_id': bsg_id,
                'behavior': bsg_labels.get('behavior', ''),
                'member_count': bsg_labels.get('member_count', 0),
                'confidence': bsg_labels.get('confidence', 0),
                'summary': bsg_labels.get('summary', ''),
                'rationale': bsg_labels.get('detection_rationale', ''),
            }

            if lod == 'strategic':
                return jsonify({'ok': True, 'lod': 'strategic', 'bsg': bsg_info, 'members': []})

            # Find member sessions via edges
            member_session_ids = []
            for eid, edge in (hg.edges or {}).items():
                ed = edge.to_dict() if hasattr(edge, 'to_dict') else (
                    edge if isinstance(edge, dict) else {}
                )
                if (ed.get('kind') == 'SESSION_MEMBER_OF_BEHAVIOR_GROUP' and
                        bsg_id in ed.get('nodes', [])):
                    enodes = ed.get('nodes', [])
                    for n in enodes:
                        if n != bsg_id:
                            member_session_ids.append(n)

            # Resolve session details
            members = []
            for sid in member_session_ids:
                snode = (hg.nodes or {}).get(sid)
                if not snode:
                    continue
                snd = snode.to_dict() if hasattr(snode, 'to_dict') else snode
                slabels = snd.get('labels', {}) or {}
                members.append({
                    'session_id': sid,
                    'src_ip': slabels.get('src_ip', ''),
                    'dst_ip': slabels.get('dst_ip', ''),
                    'src_port': slabels.get('src_port'),
                    'dst_port': slabels.get('dst_port'),
                    'proto': slabels.get('proto', ''),
                    'total_bytes': slabels.get('total_bytes', 0),
                    'packet_count': slabels.get('packet_count', 0),
                    'duration_sec': slabels.get('duration_sec', 0),
                    'tcp_flags': slabels.get('tcp_flags', []),
                    'time_bucket': slabels.get('time_bucket', 0),
                })

            # Sort by time_bucket
            members.sort(key=lambda m: m.get('time_bucket', 0))

            # Tactical: sample representative sessions (max 20)
            if lod == 'tactical' and len(members) > 20:
                step = max(1, len(members) // 20)
                members = members[::step][:20]

            return jsonify({
                'ok': True,
                'lod': lod,
                'bsg': bsg_info,
                'members': members,
                'member_count': len(member_session_ids),
            })

        except Exception as e:
            logger.error(f"BSG members failed: {e}")
            return jsonify({'ok': False, 'error': str(e)}), 500

    @app.route('/api/pcap/behavior_groups/landscape', methods=['GET'])
    def pcap_bsg_landscape():
        """BSG landscape view: all BSGs with geolocated host positions.

        Returns BSGs grouped by behavior type with src/dst host coordinates
        for rendering a BSG-only force layout or Cesium overlay.
        This is the "strategic" view — just behaviors, no sessions.
        """
        try:
            hg = _get_engine()
        except RuntimeError:
            return jsonify({
                'ok': True,
                'groups': [],
                'host_count': 0,
                'message': 'HypergraphEngine not initialized',
            })

        try:
            # Prefer authoritative projection from InstanceDB — Landscape must only render materialized projection
            try:
                if 'instance_db' in globals() and instance_db and hasattr(instance_db, 'list_bsg_projection'):
                    proj = instance_db.list_bsg_projection() or {}
                    groups = proj.get('groups', [])
                    host_count = len(proj.get('hosts', {}) if isinstance(proj.get('hosts', {}), dict) else [])
                    return jsonify({'ok': True, 'groups': groups, 'host_count': host_count})
            except Exception:
                logger.warning('InstanceDB projection not available for landscape; returning empty landscape')

            # If no authoritative projection, return an empty canonical landscape (do not run live detectors)
            return jsonify({'ok': True, 'groups': [], 'host_count': 0, 'message': 'No authoritative BSG projection available'})

            # 2. Collect BSG nodes + membership
            membership = {}  # bsg_id → [session_ids]
            for eid, edge in (hg.edges or {}).items():
                ed = edge.to_dict() if hasattr(edge, 'to_dict') else (edge if isinstance(edge, dict) else {})
                if ed.get('kind') == 'SESSION_MEMBER_OF_BEHAVIOR_GROUP':
                    enodes = ed.get('nodes', [])
                    if len(enodes) >= 2:
                        sid, bsg_id = enodes[0], enodes[1]
                        if bsg_id not in membership:
                            membership[bsg_id] = []
                        membership[bsg_id].append(sid)

            # 3. Build BSG landscape entries with geo
            groups = []
            for nid, node in (hg.nodes or {}).items():
                nd = node.to_dict() if hasattr(node, 'to_dict') else (node if isinstance(node, dict) else {})
                if nd.get('kind') != 'behavior_group':
                    continue
                labels = nd.get('labels', {}) or {}
                behavior = labels.get('behavior', 'UNKNOWN')
                src_ip = labels.get('src_ip', '')
                dst_ip = labels.get('dst_ip', '')

                # Resolve geo for src and dst
                src_geo = host_geo.get(src_ip)
                dst_geo = host_geo.get(dst_ip)

                groups.append({
                    'bsg_id': nid,
                    'behavior': behavior,
                    'member_count': len(membership.get(nid, [])),
                    'confidence': labels.get('confidence', 0),
                    'summary': labels.get('summary', ''),
                    'src_ip': src_ip,
                    'dst_ip': dst_ip,
                    'dst_port': labels.get('dst_port'),
                    'total_bytes': labels.get('total_bytes', 0),
                    'total_packets': labels.get('total_packets', 0),
                    'unique_ports': labels.get('unique_ports', 0),
                    'unique_hosts': labels.get('unique_hosts', 0),
                    'rationale': labels.get('detection_rationale', ''),
                    'src_geo': src_geo,
                    'dst_geo': dst_geo,
                })

            groups.sort(key=lambda g: g.get('confidence', 0), reverse=True)

            return jsonify({
                'ok': True,
                'groups': groups,
                'group_count': len(groups),
                'host_count': len(host_geo),
            })

        except Exception as e:
            logger.error(f"BSG landscape failed: {e}")
            return jsonify({'ok': False, 'error': str(e)}), 500

    @app.route('/api/pcap/<session_id>/bsg_context', methods=['GET'])
    def pcap_session_bsg_context(session_id):
        """Return BSG context for a specific session.

        Instead of rendering 6000+ nodes, this tells the operator:
        "This session is part of BEACON group X (142 sessions) and
         DATA_EXFIL group Y (1.2GB)"

        This enables the session modal to show behavioral context first,
        with raw graph access as an explicit opt-in.
        """
        try:
            hg = _get_engine()

            # Find all BSG memberships for this session
            bsg_memberships = []
            for eid, edge in (hg.edges or {}).items():
                ed = edge.to_dict() if hasattr(edge, 'to_dict') else (edge if isinstance(edge, dict) else {})
                if ed.get('kind') == 'SESSION_MEMBER_OF_BEHAVIOR_GROUP':
                    enodes = ed.get('nodes', [])
                    if session_id in enodes:
                        bsg_id = [n for n in enodes if n != session_id]
                        if bsg_id:
                            bsg_id = bsg_id[0]
                            bn = (hg.nodes or {}).get(bsg_id)
                            if bn:
                                bnd = bn.to_dict() if hasattr(bn, 'to_dict') else bn
                                blabels = bnd.get('labels', {}) or {}
                                bsg_memberships.append({
                                    'bsg_id': bsg_id,
                                    'behavior': blabels.get('behavior', ''),
                                    'member_count': blabels.get('member_count', 0),
                                    'confidence': blabels.get('confidence', 0),
                                    'summary': blabels.get('summary', ''),
                                    'total_bytes': blabels.get('total_bytes', 0),
                                })

            # Get session basic info
            session_node = (hg.nodes or {}).get(session_id)
            session_info = None
            if session_node:
                snd = session_node.to_dict() if hasattr(session_node, 'to_dict') else session_node
                slabels = snd.get('labels', {}) or {}
                session_info = {
                    'session_id': session_id,
                    'src_ip': slabels.get('src_ip', ''),
                    'dst_ip': slabels.get('dst_ip', ''),
                    'proto': slabels.get('proto', ''),
                    'total_bytes': slabels.get('total_bytes', 0),
                    'packet_count': slabels.get('packet_count', 0),
                    'duration_sec': slabels.get('duration_sec', 0),
                }

            return jsonify({
                'ok': True,
                'session': session_info,
                'bsg_memberships': bsg_memberships,
                'is_grouped': len(bsg_memberships) > 0,
            })

        except Exception as e:
            logger.error(f"Session BSG context failed: {e}")
            return jsonify({'ok': False, 'error': str(e)}), 500

    @app.route('/api/pcap/<session_id>/ingest', methods=['POST'])
    def pcap_ingest(session_id):
        """Trigger ingestion for a PCAP session.

        Post-ingestion closure loop:
            1. Parse PCAP → expand hypergraph (hosts, flows, DNS, TLS)
            2. Check collection tasks for satisfaction
            3. Auto-close satisfied tasks with evidence_refs + belief_delta
            4. Return ingest result + closure summary
        """
        if not pcap_registry_instance:
             return jsonify({'status': 'error', 'message': 'PcapRegistry not available'}), 503

        try:
            data = request.get_json() or {}
            mode = data.get('mode', 'flows')
            dpi = data.get('dpi', True)

            # 1. Run ingestion
            result = pcap_registry_instance.ingest_pcap_session(
                session_id=session_id,
                mode=mode,
                dpi=dpi
            )

            # 2. Post-ingestion closure loop
            closure_summary = {"satisfied": [], "expired": 0, "matched_tasks": []}
            try:
                from collection_tasks import CollectionTaskManager
                engine = _get_engine()
                mgr = CollectionTaskManager(engine)

                # 2a. Find tasks linked to or matching this session
                matched = mgr.tasks_matching_session(session_id)
                closure_summary["matched_tasks"] = matched

                # 2b. For matched tasks, satisfy with session evidence
                for task_id in matched:
                    ok = mgr.satisfy_task(
                        task_id,
                        evidence_refs=[f"pcap_session:{session_id}"],
                        session_ids=[session_id],
                        belief_delta={"source": "pcap_ingest", "session_id": session_id},
                    )
                    if ok:
                        closure_summary["satisfied"].append(task_id)

                # 2c. Also run general satisfaction check (edge-evidence based)
                edge_closed = mgr.check_task_satisfaction()
                for tid in edge_closed:
                    if tid not in closure_summary["satisfied"]:
                        closure_summary["satisfied"].append(tid)

                # 2d. Expire stale tasks
                closure_summary["expired"] = mgr.expire_stale_tasks()

            except Exception as e:
                logger.warning(f"Post-ingest closure loop failed: {e}")
                closure_summary["error"] = str(e)

            # 2c. opportunistically run edge decay to keep graph bounded
            try:
                engine = _get_engine()
                if hasattr(engine, 'decay_edges'):
                    engine.decay_edges()
            except Exception:
                pass

            # ── Auto-run BSG detection after ingest ──────────────────
            bsg_auto = {}
            try:
                from behavior_groups import BehaviorGroupDetector, BSGConfig
                engine = _get_engine()
                detector = BehaviorGroupDetector(engine, BSGConfig())
                bsg_result = detector.detect_from_graph()
                bsg_auto = hard_json_clean(bsg_result.to_dict())
                logger.info("[BSG] Auto-detection after session ingest: %s",
                            bsg_result.summary())
            except Exception as bsg_err:
                logger.warning("[BSG] Auto-detection failed (non-fatal): %s", bsg_err)
                bsg_auto = {'error': str(bsg_err)}

            return jsonify({
                'status': 'ok',
                'result': result,
                'closure': closure_summary,
                'bsg_auto': bsg_auto,
            })

        except Exception as e:
            logger.error(f"PCAP ingest failed: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/pcap/sessions', methods=['GET'])
    def pcap_list_sessions():
        """List all persisted PCAP sessions available for replay"""
        if not pcap_registry_instance:
            return jsonify({'ok': False, 'error': 'registry_unavailable', 'message': 'PcapRegistry not available'}), 503
        try:
            # Debugging code
            if not hasattr(pcap_registry_instance, 'list_sessions'):
                logger.error(f"PcapRegistry instance ({type(pcap_registry_instance)}) missing list_sessions. Dir: {dir(pcap_registry_instance)}")
                # Attempt to hot-patch or reload?
                return jsonify({'ok': False, 'error': 'method_missing', 'message': f'PcapRegistry missing list_sessions. Type: {type(pcap_registry_instance)}'}), 500

            sessions = pcap_registry_instance.list_sessions()

            # Normalize keys for UI compatibility
            norm = []
            for s in (sessions or []):
                if not isinstance(s, dict):
                    continue
                sid = s.get("session_id") or s.get("id") or s.get("sessionId") or s.get("name")
                s.setdefault("session_id", sid)
                s.setdefault("id", sid)
                s.setdefault("name", sid)
                s.setdefault("display_name", sid)
                norm.append(s)

            return jsonify({'ok': True, 'sessions': norm, 'count': len(norm)})
        except Exception as e:
            logger.error(f"PCAP list sessions failed: {e}")
            return jsonify({'ok': False, 'error': 'list_failed', 'message': str(e)}), 500

    @app.route('/api/pcap/<session_id>/subgraph', methods=['GET'])
    def pcap_session_subgraph(session_id):
        try:
            # Reject obvious bad IDs early (prevents empty modal + weird traversal)
            if not session_id or session_id in ("undefined", "null", ""):
                return jsonify({"ok": False, "error": "invalid_session_id", "message": "Invalid session_id"}), 400

            # Depth clamp (prevents accidental graph-walk explosions)
            depth_raw = request.args.get("depth", "2")
            try:
                max_depth = int(depth_raw)
            except Exception:
                max_depth = 2
            max_depth = max(0, min(10, max_depth))

            # Durable-first subgraph (works even when hypergraph is empty after restart)
            if pcap_registry_instance and hasattr(pcap_registry_instance, "get_session_subgraph"):
                sg = pcap_registry_instance.get_session_subgraph(session_id, depth=max_depth, hydrate_graph=True)
                if sg:
                    return jsonify({"ok": True, "session_id": session_id, "subgraph": sg})

            # Fallback: hypergraph-based subgraph (for sessions not yet in durable storage)
            hg = globals().get('hypergraph_engine')
            if not hg:
                 return jsonify({'ok': False, 'error': 'hypergraph_unavailable', 'message': 'Hypergraph engine not available'}), 503

            def _id_of(x):
                """Extract a stable string ID from node-ish objects."""
                if x is None:
                    return None
                if isinstance(x, str):
                    return x
                if isinstance(x, dict):
                    return x.get("id") or x.get("node_id")
                return getattr(x, "id", None)

            def _safe_serial(obj, _seen=None):
                """Recursively serialize obj, breaking circular references."""
                if _seen is None:
                    _seen = set()
                oid = id(obj)
                if oid in _seen:
                    return "__circular_ref__"
                if isinstance(obj, (str, int, float, bool)) or obj is None:
                    return obj
                _seen.add(oid)
                try:
                    if isinstance(obj, dict):
                        return {str(k): _safe_serial(v, _seen) for k, v in obj.items()}
                    if isinstance(obj, (list, tuple)):
                        return [_safe_serial(v, _seen) for v in obj]
                    if hasattr(obj, "to_dict") and callable(getattr(obj, "to_dict")):
                        return _safe_serial(obj.to_dict(), _seen)
                    if hasattr(obj, "__dict__"):
                        return _safe_serial(dict(obj.__dict__), _seen)
                    return str(obj)
                finally:
                    _seen.discard(oid)

            def _as_dict(x):
                """JSON-safe conversion for HGNode/HGEdge OR raw dicts OR unknown objects."""
                if x is None:
                    return None
                return _safe_serial(x)

            # Helpful: if the session node doesn’t exist, say so explicitly
            root = hg.get_node(session_id) if hasattr(hg, "get_node") else None
            if root is None:
                return jsonify({
                    "ok": False, "error": "session_not_found",
                    "message": f"Unknown session_id: {session_id}",
                    "session_id": session_id
                }), 404

            visited_nodes = set([session_id])
            visited_edges = set()
            frontier = set([session_id])

            for _ in range(max_depth):
                next_frontier = set()
                for nid in list(frontier):
                    # tolerate missing edges_for_node
                    edge_iter = hg.edges_for_node(nid) if hasattr(hg, "edges_for_node") else []
                    for edge in edge_iter:
                        if isinstance(edge, dict):
                            eid = edge.get("id")
                            edge_nodes = edge.get("nodes") or []
                        else:
                            eid = getattr(edge, "id", None)
                            edge_nodes = getattr(edge, "nodes", []) or []

                        if eid:
                            visited_edges.add(eid)

                        # edge_nodes may contain dicts/objects: normalize → id strings only
                        for t in edge_nodes:
                            tid = _id_of(t)
                            if not tid:
                                continue
                            if tid not in visited_nodes:
                                visited_nodes.add(tid)
                                next_frontier.add(tid)

                frontier = next_frontier
                if not frontier:
                    break

            nodes_out = []
            for nid in visited_nodes:
                n = hg.get_node(nid) if hasattr(hg, "get_node") else None
                if n is None:
                    # include a stub so UI can still show the edge endpoints
                    nodes_out.append({"id": nid, "kind": "missing", "metadata": {"stub": True}})
                else:
                    nodes_out.append(_as_dict(n))

            edges_out = []
            for eid in visited_edges:
                e = hg.get_edge(eid) if hasattr(hg, "get_edge") else None
                if e is not None:
                    edges_out.append(_as_dict(e))

            return jsonify({
                "ok": True,
                "session_id": session_id,
                "subgraph": {
                    "nodes": nodes_out,
                    "edges": edges_out,
                    "stats": {"depth": max_depth, "node_count": len(nodes_out), "edge_count": len(edges_out)}
                }
            })

        except Exception as e:
            # IMPORTANT: traceback in server logs, JSON to client
            logger.exception(f"pcap_session_subgraph failed: session_id={session_id}")
            return jsonify({
                "ok": False,
                "error": str(e),
                "session_id": session_id
            }), 500

    # -----------------------------------------------------------------
    # PCAP Globe Overlay — spatial projection for Cesium
    # Modes: ports (default), top, geo_asn
    # -----------------------------------------------------------------
    @app.route('/api/pcap/<session_id>/globe', methods=['GET'])
    def pcap_session_globe(session_id: str):
        try:
            if not session_id or session_id in ("undefined", "null", ""):
                return jsonify({"ok": False, "message": "Invalid session_id"}), 400

            # Query params expected by the UI
            mode = (request.args.get("mode", "ports") or "ports").lower()
            proto_filter = (request.args.get("proto") or "").lower().strip()
            port_filter = request.args.get("port", None)

            limit_ports   = _q_int("limit_ports", 6, 1, 64)
            limit_talkers = _q_int("limit_talkers", 18, 1, 200)

            include_tls = _q_int("include_tls", 1, 0, 1) == 1
            include_geo = _q_int("include_geo", 1, 0, 1) == 1

            hub_alt_m      = _q_float("hub_alt_m", 120000, 0, 2_000_000)
            hub_radius_m   = _q_float("hub_radius_m", 250000, 0, 5_000_000)
            arc_peak_alt_m = _q_float("arc_peak_alt_m", 220000, 0, 5_000_000)
            arc_samples    = _q_int("arc_samples", 48, 8, 256)

            layout = {
                "hub_alt_m": hub_alt_m,
                "hub_radius_m": hub_radius_m,
                "arc_peak_alt_m": arc_peak_alt_m,
                "arc_samples": arc_samples,
            }

            # Prefer a registry-provided implementation if present
            if 'pcap_registry_instance' in globals():
                reg = globals().get('pcap_registry_instance')
                for fn_name in ("build_globe_overlay", "get_globe_overlay", "globe_overlay"):
                    if reg is not None and hasattr(reg, fn_name) and callable(getattr(reg, fn_name)):
                        out = getattr(reg, fn_name)(session_id, dict(request.args))
                        # Expect out already in UI format; just ensure required keys exist
                        if isinstance(out, dict):
                            out.setdefault("ok", True)
                            out.setdefault("layout", layout)
                            out.setdefault("session", {"session_id": session_id, "id": session_id, "name": session_id, "display_name": session_id})
                            return jsonify(out)

            # -----------------------------
            # Fallback: synthesize globe data from what we already have in-memory.
            # This prevents 404 and keeps the UI operational even if the registry
            # hasn't implemented a real port/talker summarizer yet.
            # -----------------------------

            # Session object (minimal)
            session_obj = {"session_id": session_id, "id": session_id, "name": session_id, "display_name": session_id}

            # Collect candidate endpoints from persisted recon entities / room entities.
            # Heuristic: anything with id prefix "PCAP-" and a location/geo.
            endpoints = []
            try:
                # If you have an in-memory recon system
                rs = globals().get("recon_system")
                if rs is not None and hasattr(rs, "entities"):
                    for rid, ent in list(getattr(rs, "entities", {}).items()):
                        if not isinstance(rid, str) or not rid.startswith("PCAP-"):
                            continue
                        if not isinstance(ent, dict):
                            continue
                        loc = ent.get("location") or ent.get("geo") or (ent.get("metadata") or {}).get("geo") or {}
                        lat = loc.get("lat") if isinstance(loc, dict) else None
                        lon = loc.get("lon") if isinstance(loc, dict) else None
                        if lat is None or lon is None:
                            continue
                        endpoints.append({
                            "endpoint_id": rid,
                            "ip": _parse_pcap_ip_from_id(rid),
                            "role": ent.get("role") or "talker",
                            "bytes_total": ent.get("bytes_total") or (ent.get("stats") or {}).get("bytes_total") or 0,
                            "flows": ent.get("flows") or (ent.get("stats") or {}).get("flows") or 1,
                            "scanner_like_mean": (ent.get("scores") or {}).get("scanner_like_mean", 0.2),
                            "geo": {
                                "lat": float(lat),
                                "lon": float(lon),
                                "country_iso": (loc.get("country_iso") if isinstance(loc, dict) else None),
                                "city": (loc.get("city") if isinstance(loc, dict) else None),
                            } if include_geo else None,
                            "geo_provenance": {
                                "geo_source": ((ent.get("metadata") or {}).get("geo_provenance") or {}).get("geo_source", "recon"),
                                "geo_confidence": ((ent.get("metadata") or {}).get("geo_provenance") or {}).get("geo_confidence", 0.4),
                            } if include_geo else None,
                            "tls": (ent.get("tls") if include_tls else None),
                        })
            except Exception:
                logger.exception("[PCAP] globe fallback endpoint harvest failed")

            # Clamp endpoints
            endpoints = endpoints[:limit_talkers]

            # Choose a capture site:
            # 1) if endpoints exist, anchor at the first endpoint (better than 0,0)
            # 2) otherwise default to 0,0
            g0 = _first_geo_from_endpoints(endpoints)
            capture_site = {
                "lat": float(g0.get("lat", 0.0)),
                "lon": float(g0.get("lon", 0.0)),
                "alt_m": 0,
                "label": "PCAP Capture",
            }

            # Build hubs.
            # If you don't have port summaries yet, we create a single "ip:talkers" hub.
            hubs = []
            if endpoints:
                hubs.append({
                    "hub_id": "hub_ip_talkers",
                    "proto": (proto_filter.upper() if proto_filter else "IP"),
                    "port": (int(port_filter) if (port_filter and str(port_filter).isdigit()) else "talkers"),
                    "flow_count": len(endpoints),
                    "scanner_like_p95": 0.3,
                    "top_talkers": endpoints[:limit_talkers],
                })

            # If the UI asked for a specific proto/port, keep response consistent
            if proto_filter or port_filter:
                # "expand hub" re-fetch expects hubs[0].top_talkers
                pass

            return jsonify({
                "ok": True,
                "mode": mode,
                "session": session_obj,
                "capture_site": capture_site,
                "layout": layout,
                "hubs": hubs[:limit_ports],
            })

        except Exception as e:
            logger.exception(f"[PCAP] globe route failed: session_id={session_id}")
            return jsonify({"ok": False, "message": str(e), "session_id": session_id}), 500

    @app.route('/api/recon/entity/<entity_id>/assign_sensor', methods=['POST'])
    def assign_sensor_to_entity(entity_id):
        """Assign a sensor to a recon entity (via sensor_registry)"""
        try:
            # Delegate directly to sensor registry
            if not sensor_registry_instance:
                 return jsonify({'status': 'error', 'message': 'Sensor Registry not available'}), 503

            data = request.get_json() or {}
            sensor_id = data.get('sensor_id')
            if not sensor_id:
                  return jsonify({'status': 'error', 'message': 'sensor_id required'}), 400
            ctx = _rf_ip_write_context(data, source="sensor_assignment")

            # Use the refactored registry logic
            if hasattr(sensor_registry_instance, 'assign_sensor'):
                result = sensor_registry_instance.assign_sensor(
                    sensor_id=sensor_id,
                    recon_entity_id=entity_id,
                    ctx=ctx,
                )
                return jsonify({'status': 'ok', 'assignment': result})
            else:
                 return jsonify({'status': 'error', 'message': 'SensorRegistry missing assign_sensor'}), 500

        except Exception as e:
            logger.error(f"Error assigning sensor: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500


    @app.route('/api/recon/entity/<entity_id>/sensors', methods=['GET'])
    def get_entity_sensors(entity_id):
        """Get all sensors assigned to a recon entity"""
        try:
            to_id = entity_id if entity_id.startswith('recon:') else f"recon:{entity_id}"

            # Find all assignments to this entity
            assigned = []
            for edge_id, assignment in sensor_assignments.items():
                if assignment.get('to') == to_id or assignment.get('recon_entity_id') == entity_id.replace('recon:', ''):
                    sensor_node_id = assignment.get('from') or f"sensor:{assignment.get('sensor_id')}"
                    sensor = sensor_store.get(sensor_node_id)
                    if sensor:
                        assigned.append({
                            'assignment': assignment,
                            'sensor': sensor
                        })

            return jsonify({
                'status': 'ok',
                'entity_id': entity_id,
                'assigned_count': len(assigned),
                'assignments': assigned,
                'timestamp': time.time()
            })
        except Exception as e:
            logger.error(f"Error getting entity sensors: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/sensors/<sensor_id>/unassign/<entity_id>', methods=['DELETE', 'POST'])
    def unassign_sensor(sensor_id, entity_id):
        """Remove a sensor assignment from a recon entity"""
        try:
            from_id = f"sensor:{sensor_id}" if not sensor_id.startswith('sensor:') else sensor_id
            to_id = entity_id if entity_id.startswith('recon:') else f"recon:{entity_id}"
            edge_id = f"edge:{from_id}->{to_id}"

            if edge_id not in sensor_assignments:
                return jsonify({'status': 'error', 'message': 'Assignment not found'}), 404

            try:
                import writebus
                from writebus import GraphOp, WriteContext

                data = request.get_json(silent=True) or {}
                token = request.headers.get("X-Session-Token") or data.get("session_token")
                operator = None
                if OPERATOR_MANAGER_AVAILABLE and token:
                    try:
                        operator = get_session_manager().get_operator_for_session(token)
                    except Exception:
                        operator = None
                write_res = writebus.bus().commit(
                    entity_id=edge_id,
                    entity_type="SENSOR_ASSIGNMENT_TOMBSTONE",
                    entity_data={
                        "entity_id": edge_id,
                        "type": "SENSOR_ASSIGNMENT_TOMBSTONE",
                        "deleted": True,
                        "deleted_at": time.time(),
                        "from": from_id,
                        "to": to_id,
                    },
                    graph_ops=[GraphOp(event_type="EDGE_DELETE", entity_id=edge_id, entity_data={"id": edge_id})],
                    ctx=WriteContext(
                        room_name="Global",
                        operator=operator,
                        operator_id=(
                            getattr(operator, 'operator_id', None)
                            or request.headers.get("X-Operator-Id")
                            or data.get("operator_id")
                            or "SYSTEM:SENSOR_API"
                        ),
                        session_token=token,
                        request_id=request.headers.get("X-Request-Id") or data.get("request_id"),
                        source="sensor_assignment_delete",
                    ),
                    persist=True,
                    audit=True,
                )
                if not write_res.ok:
                    return jsonify({
                        'status': 'error',
                        'message': 'WriteBus unassign commit failed',
                        'write_result': {
                            'commit_status': write_res.commit_status,
                            'errors': write_res.errors,
                            'debug': write_res.debug,
                        }
                    }), 500
            except Exception as ex:
                logger.error(f"WriteBus unassign failed: {ex}")
                return jsonify({'status': 'error', 'message': str(ex)}), 500

            # Remove from memory after canonical delete succeeds.
            sensor_assignments.pop(edge_id, None)

            logger.info(f"Unassigned sensor {sensor_id} from entity {entity_id}")
            return jsonify({'status': 'ok', 'unassigned': edge_id})
        except Exception as e:
            logger.error(f"Error unassigning sensor: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/sensors/<sensor_id>/activity', methods=['POST'])
    def emit_sensor_activity(sensor_id):
        """Emit sensor activity (creates an activity edge in the hypergraph)"""
        try:
            data = request.get_json() or {}
            activity_kind = data.get('kind') or data.get('activity_type') or 'signal_detected'

            from_id = f"sensor:{sensor_id}" if not sensor_id.startswith('sensor:') else sensor_id

            if from_id not in sensor_store:
                return jsonify({'status': 'error', 'message': f'Sensor {sensor_id} not found'}), 404

            # Build activity edge
            activity_id = f"activity:{sensor_id}:{int(time.time()*1000)}"

            # Determine target nodes (sensor + optional RF/recon entity)
            nodes = [from_id]
            recon_entity_id = data.get('recon_entity_id')
            if recon_entity_id:
                nodes.append(f"recon:{recon_entity_id}" if not recon_entity_id.startswith('recon:') else recon_entity_id)

            rf_node_id = data.get('rf_node_id')
            if rf_node_id:
                nodes.append(rf_node_id)

            activity = {
                'activity_id': activity_id,
                'entity_type': 'SENSOR_ACTIVITY',
                'kind': activity_kind,
                'nodes': nodes,
                'sensor_id': sensor_id,
                'payload': {
                    'frequency_mhz': data.get('frequency_mhz'),
                    'power_dbm': data.get('power_dbm'),
                    'snr_db': data.get('snr_db'),
                    'modulation': data.get('modulation'),
                    'confidence': data.get('confidence', 0.5),
                    'bandwidth_hz': data.get('bandwidth_hz'),
                    'bearing_deg': data.get('bearing_deg'),
                    'timestamp': data.get('timestamp') or time.time(),
                    # LPI Fields - Pace/LPI theory support
                    'algo': data.get('algo'), # {name, version, params}
                    'feature_set_id': data.get('feature_set_id'),
                    'window': data.get('window'), # {t0, t1, sample_rate, center_freq, bandwidth}
                    'evidence': data.get('evidence'), # {iq_hash, artifact_ptrs}
                    'estimated_params': data.get('estimated_params'),
                    'classes': data.get('classes'),
                    'association': data.get('association'),
                    'belief': data.get('belief')
                },
                'labels': {
                    'missionId': data.get('mission_id') or data.get('missionId')
                },
                'metadata': {
                    'sensor_name': sensor_store.get(from_id, {}).get('name'),
                    'sensor_position': sensor_store.get(from_id, {}).get('position')
                },
                'timestamp': time.time()
            }

            # Update sensor last_seen
            if from_id in sensor_store:
                sensor_store[from_id]['status']['last_seen'] = time.time()
                sensor_store[from_id]['status']['state'] = 'ACTIVE'

            # Emit to hypergraph as edge (high-volume, not persisted to room by default)
            persist_to_room = data.get('persist_to_room', False)

            try:
                import writebus
                from writebus import GraphOp, WriteContext

                token = request.headers.get("X-Session-Token") or data.get("session_token")
                operator = None
                if OPERATOR_MANAGER_AVAILABLE and token:
                    try:
                        operator = get_session_manager().get_operator_for_session(token)
                    except Exception:
                        operator = None
                write_res = writebus.bus().commit(
                    entity_id=activity_id,
                    entity_type="SENSOR_ACTIVITY",
                    entity_data=activity,
                    graph_ops=[GraphOp(
                        event_type="EDGE_UPDATE",
                        entity_id=activity_id,
                        entity_data={
                            'id': activity_id,
                            'kind': activity_kind,
                            'nodes': nodes,
                            'labels': activity['labels'],
                            'metadata': activity['payload'],
                            'timestamp': activity['timestamp'],
                        },
                    )],
                    ctx=WriteContext(
                        room_name="Global",
                        mission_id=data.get('mission_id') or data.get('missionId'),
                        operator=operator,
                        operator_id=(
                            getattr(operator, 'operator_id', None)
                            or request.headers.get("X-Operator-Id")
                            or data.get("operator_id")
                            or "SYSTEM:SENSOR_ACTIVITY"
                        ),
                        session_token=token,
                        request_id=request.headers.get("X-Request-Id") or data.get("request_id"),
                        source="sensor_activity_api",
                        evidence_refs=list((data.get("evidence") or {}).values()) if isinstance(data.get("evidence"), dict) else [],
                    ),
                    persist=bool(persist_to_room),
                    audit=bool(persist_to_room),
                )
                if not write_res.ok:
                    return jsonify({
                        'status': 'error',
                        'message': 'WriteBus activity commit failed',
                        'write_result': {
                            'commit_status': write_res.commit_status,
                            'errors': write_res.errors,
                            'debug': write_res.debug,
                        }
                    }), 500
            except Exception as ex:
                logger.error(f"WriteBus sensor activity failed: {ex}")
                return jsonify({'status': 'error', 'message': str(ex)}), 500

            return jsonify({'status': 'ok', 'activity': activity})
        except Exception as e:
            logger.error(f"Error emitting sensor activity: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/rf-ip-correlation/observe/rf', methods=['POST'])
    def observe_rf_for_correlation():
        """Observe an RF event and correlate it against recent network activity."""
        try:
            if not rf_ip_correlation_engine:
                return jsonify({'status': 'error', 'message': 'RF/IP correlation engine unavailable'}), 503

            data = request.get_json() or {}
            ctx = _rf_ip_write_context(data, source="rf_ip_rf_observation")
            rf_obs, bindings = rf_ip_correlation_engine.observe_rf(data)

            for binding in bindings:
                net_obs = rf_ip_correlation_engine.get_network_observation(binding.network_observation_id)
                if not net_obs:
                    continue
                _emit_rf_ip_binding(binding, rf_obs, net_obs, ctx)

            return jsonify({
                'status': 'ok',
                'rf_observation': rf_obs.to_dict(),
                'binding_count': len(bindings),
                'bindings': [binding.to_dict() for binding in bindings],
            })
        except Exception as e:
            logger.error(f"Error observing RF correlation event: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/rfuav/observe', methods=['POST'])
    def observe_rfuav_evidence():
        """Normalize RFUAV output into observed RF evidence, then feed correlation."""
        try:
            data = request.get_json() or {}
            ctx = _scythe_write_context(data=data, source="rfuav_inference_service", default_operator_id="SYSTEM:RFUAV")
            return jsonify(_ingest_rfuav_detection_event(data, ctx))
        except Exception as e:
            logger.error(f"Error observing RFUAV evidence: {e}")
            if "unavailable" in str(e).lower():
                return jsonify({'status': 'error', 'message': str(e)}), 503
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/nis/sigint/simulate', methods=['POST'])
    def simulate_nis_sigint_scene():
        """Generate NIS-derived synthetic RF observations and optionally ingest them."""
        try:
            from nis_scythe_bridge import generate_sigint_observations

            data = request.get_json() or {}
            ctx = _scythe_write_context(
                data=data,
                source="nis_sigint_sim",
                default_operator_id="SYSTEM:NIS_SIGINT",
            )
            result = generate_sigint_observations(
                emitters_per_band=max(1, min(int(data.get('emitters_per_band', 1)), 8)),
                scatter_area_m=float(data.get('scatter_area_m', 5000.0)),
                seed=int(data.get('seed', 1337)),
                satellite_grazing_angle_deg=float(data.get('satellite_grazing_angle_deg', 45.0)),
                tx_power_dbm=float(data.get('tx_power_dbm', 23.0)),
                sensor_id=str(data.get('sensor_id') or 'nis-sim'),
                origin_lat=data.get('origin_lat'),
                origin_lon=data.get('origin_lon'),
                mission_id=data.get('mission_id') or data.get('missionId'),
                timestamp=data.get('timestamp'),
            )

            ingest_raw = data.get('ingest_to_graph')
            ingest = ingest_raw if isinstance(ingest_raw, bool) else str(ingest_raw).strip().lower() in {
                '1',
                'true',
                'yes',
                'on',
            }
            if not ingest:
                return jsonify({'status': 'ok', **result})

            ingested = []
            for observation in result.get('observations', []):
                rf_obs, bindings, binding_emissions, rf_emission = _observe_synthetic_rf_observation(
                    observation,
                    ctx,
                    source="nis_sigint_sim",
                )
                ingested.append(
                    {
                        'rf_observation': rf_obs.to_dict(),
                        'binding_count': len(bindings),
                        'bindings': [binding.to_dict() for binding in bindings],
                        'binding_emissions': binding_emissions,
                        'rf_emission': rf_emission,
                    }
                )

            return jsonify(
                {
                    'status': 'ok',
                    **result,
                    'ingested_count': len(ingested),
                    'ingested': ingested,
                }
            )
        except Exception as e:
            logger.error(f"Error simulating NIS SIGINT scene: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/nis/sigint/summary', methods=['GET'])
    def get_nis_sigint_summary():
        """Summarize NIS multibeam outputs for operator/debug use."""
        try:
            from pathlib import Path
            from nis_scythe_bridge import (
                DEFAULT_SIGINT_CACHE_PATH,
                DEFAULT_SIGINT_NPZ_PATH,
                summarize_clean_cache,
                summarize_sigint_npz,
            )

            repo_root = Path(__file__).resolve().parent

            def _resolve_local_repo_path(raw_path, default_path):
                if not raw_path:
                    return default_path
                candidate = Path(raw_path)
                if not candidate.is_absolute():
                    candidate = repo_root / candidate
                candidate = candidate.resolve()
                if not candidate.is_relative_to(repo_root):
                    raise ValueError(f"path must stay within repository: {raw_path}")
                return candidate

            npz_path = _resolve_local_repo_path(request.args.get('npz_path'), DEFAULT_SIGINT_NPZ_PATH)
            cache_path = _resolve_local_repo_path(request.args.get('cache_path'), DEFAULT_SIGINT_CACHE_PATH)
            response = {
                'status': 'ok',
                'npz_summary': summarize_sigint_npz(npz_path),
            }
            if cache_path.exists():
                response['cache_summary'] = summarize_clean_cache(cache_path)
            return jsonify(response)
        except Exception as e:
            logger.error(f"Error summarizing NIS SIGINT outputs: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/nis/sar/scene-priors', methods=['GET'])
    def get_nis_sar_scene_priors():
        """Expose NIS SAR material and scene priors for SCYTHE tooling."""
        try:
            from nis_scythe_bridge import load_sar_scene_priors

            return jsonify({'status': 'ok', 'priors': load_sar_scene_priors()})
        except Exception as e:
            logger.error(f"Error loading NIS SAR scene priors: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/rf-ip-correlation/observe/network', methods=['POST'])
    def observe_network_for_correlation():
        """Observe a network event and correlate it against recent RF activity."""
        try:
            if not rf_ip_correlation_engine:
                return jsonify({'status': 'error', 'message': 'RF/IP correlation engine unavailable'}), 503

            data = request.get_json() or {}
            ctx = _rf_ip_write_context(data, source="rf_ip_network_observation")
            net_obs, bindings = rf_ip_correlation_engine.observe_network(data)

            for binding in bindings:
                rf_obs = rf_ip_correlation_engine.get_rf_observation(binding.rf_observation_id)
                if not rf_obs:
                    continue
                _emit_rf_ip_binding(binding, rf_obs, net_obs, ctx)

            return jsonify({
                'status': 'ok',
                'network_observation': net_obs.to_dict(),
                'binding_count': len(bindings),
                'bindings': [binding.to_dict() for binding in bindings],
            })
        except Exception as e:
            logger.error(f"Error observing network correlation event: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/rf-ip-correlation/status', methods=['GET'])
    def rf_ip_correlation_status():
        """Get RF/IP correlation engine status."""
        try:
            if not rf_ip_correlation_engine:
                return jsonify({'status': 'error', 'message': 'RF/IP correlation engine unavailable'}), 503
            return jsonify({'status': 'ok', 'engine': rf_ip_correlation_engine.status()})
        except Exception as e:
            logger.error(f"Error reading RF/IP correlation status: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/rf-ip-correlation/bindings', methods=['GET'])
    def rf_ip_correlation_bindings():
        """Get recent RF/IP bindings."""
        try:
            if not rf_ip_correlation_engine:
                return jsonify({'status': 'error', 'message': 'RF/IP correlation engine unavailable'}), 503

            limit = max(1, min(int(request.args.get('limit', 25)), 100))
            bindings = rf_ip_correlation_engine.recent_bindings(limit=limit)
            return jsonify({
                'status': 'ok',
                'binding_count': len(bindings),
                'bindings': bindings,
            })
        except Exception as e:
            logger.error(f"Error reading RF/IP correlation bindings: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/control-path/predict', methods=['GET'])
    def get_control_path_predictions():
        try:
            _rehydrate_global_room()

            observer_id = (request.args.get('observer_id') or request.args.get('sensor_id') or '').strip()
            observer_lat = _safe_float_arg('lat')
            observer_lon = _safe_float_arg('lon')
            observer_alt_m = _safe_float_arg('alt_m', 0.0)
            heading_deg = _safe_float_arg('heading_deg', 0.0) or 0.0
            limit = max(1, min(int(request.args.get('limit', 8)), 16))
            max_distance_m = max(100.0, min(float(_safe_float_arg('max_distance_m', 10000.0) or 10000.0), 500000.0))

            if not observer_id and (observer_lat is None or observer_lon is None):
                return jsonify({'status': 'error', 'message': 'observer_id or lat/lon required'}), 400

            observer = _resolve_observer_context(
                observer_id=observer_id,
                lat=observer_lat,
                lon=observer_lon,
                alt_m=observer_alt_m,
                heading_deg=heading_deg,
            )
            if not observer:
                return jsonify({'status': 'error', 'message': f'Observer {observer_id or "query"} not found'}), 404

            forecast_bundle = _build_control_path_forecasts(
                observer,
                _trackable_recon_entities_snapshot(),
                limit=limit,
                max_distance_m=max_distance_m,
            )
            return jsonify({
                'status': 'ok',
                'observer': observer,
                'counts': forecast_bundle.get('counts') or {},
                'signals': forecast_bundle.get('signals') or {},
                'predictions': forecast_bundle.get('predictions') or [],
                'timestamp': time.time(),
            })
        except Exception as e:
            logger.error(f"Error building control-path predictions: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/control-path/predict/emit', methods=['POST'])
    def emit_control_path_predictions():
        try:
            _rehydrate_global_room()

            body = request.get_json(silent=True) or {}
            observer_id = str(body.get('observer_id') or body.get('sensor_id') or request.args.get('observer_id') or '').strip()
            observer_lat = body.get('lat', _safe_float_arg('lat'))
            observer_lon = body.get('lon', _safe_float_arg('lon'))
            observer_alt_m = body.get('alt_m', _safe_float_arg('alt_m', 0.0))
            heading_deg = body.get('heading_deg', _safe_float_arg('heading_deg', 0.0) or 0.0)
            limit = max(1, min(int(body.get('limit', request.args.get('limit', 8)) or 8), 16))
            max_distance_m = max(100.0, min(float(body.get('max_distance_m', _safe_float_arg('max_distance_m', 10000.0) or 10000.0)), 500000.0))
            min_confidence = max(0.0, min(float(body.get('min_confidence', 0.6) or 0.6), 1.0))

            if not observer_id and (observer_lat is None or observer_lon is None):
                return jsonify({'status': 'error', 'message': 'observer_id or lat/lon required'}), 400

            observer = _resolve_observer_context(
                observer_id=observer_id,
                lat=observer_lat,
                lon=observer_lon,
                alt_m=observer_alt_m,
                heading_deg=heading_deg,
            )
            if not observer:
                return jsonify({'status': 'error', 'message': f'Observer {observer_id or "query"} not found'}), 404

            forecast_bundle = _build_control_path_forecasts(
                observer,
                _trackable_recon_entities_snapshot(),
                limit=limit,
                max_distance_m=max_distance_m,
            )
            selected_predictions = [
                prediction
                for prediction in (forecast_bundle.get('predictions') or [])
                if float(prediction.get('confidence') or 0.0) >= min_confidence
            ]

            from writebus import WriteContext

            ctx = WriteContext(
                room_name=str(body.get('room_name') or 'Global'),
                mission_id=body.get('mission_id'),
                operator_id=request.headers.get('X-Operator-Id') or body.get('operator_id') or 'SYSTEM:PREDICTOR',
                session_token=request.headers.get('X-Session-Token'),
                request_id=request.headers.get('X-Request-Id'),
                source='predictive_control_path_engine',
            )
            emit_results = _emit_control_path_predictions(observer, selected_predictions, ctx)
            return jsonify({
                'status': 'ok',
                'observer': observer,
                'selected': len(selected_predictions),
                'emitted': sum(1 for item in emit_results if item.get('ok')),
                'counts': forecast_bundle.get('counts') or {},
                'signals': forecast_bundle.get('signals') or {},
                'results': emit_results,
                'predictions': selected_predictions,
                'timestamp': time.time(),
            })
        except Exception as e:
            logger.error(f"Error emitting control-path predictions: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/ar/projection', methods=['GET'])
    @app.route('/api/digital-twin/projection', methods=['GET'])
    def get_digital_twin_projection():
        """Project graph state into an observer-relative payload for mobile/AR clients."""
        try:
            _rehydrate_global_room()

            observer_id = (request.args.get('observer_id') or request.args.get('sensor_id') or '').strip()
            observer_lat = _safe_float_arg('lat')
            observer_lon = _safe_float_arg('lon')
            observer_alt_m = _safe_float_arg('alt_m', 0.0)
            heading_deg = _safe_float_arg('heading_deg', 0.0) or 0.0
            limit = max(1, min(int(request.args.get('limit', 24)), 100))
            max_distance_m = max(100.0, min(float(_safe_float_arg('max_distance_m', 10000.0) or 10000.0), 500000.0))

            if not observer_id and (observer_lat is None or observer_lon is None):
                return jsonify({'status': 'error', 'message': 'observer_id or lat/lon required'}), 400

            observer = _resolve_observer_context(
                observer_id=observer_id,
                lat=observer_lat,
                lon=observer_lon,
                alt_m=observer_alt_m,
                heading_deg=heading_deg,
            )
            if not observer:
                return jsonify({'status': 'error', 'message': f'Observer {observer_id or "query"} not found'}), 404

            projections = []
            seen_ids = set()
            binding_count = 0
            nearby_count = 0

            recon_entities = _trackable_recon_entities_snapshot()
            recon_by_id = {
                str(entity.get('entity_id')): entity
                for entity in recon_entities
                if entity.get('entity_id')
            }

            if rf_ip_correlation_engine:
                for binding in reversed(rf_ip_correlation_engine.recent_bindings(limit=limit * 3)):
                    recon_entity_id = str(binding.get('recon_entity_id') or '').replace('recon:', '')
                    if not recon_entity_id or recon_entity_id == observer.get('recon_entity_id'):
                        continue
                    if recon_entity_id in seen_ids:
                        continue

                    recon_entity = recon_by_id.get(recon_entity_id)
                    network_obs = rf_ip_correlation_engine.get_network_observation(binding.get('network_observation_id'))
                    rf_obs = rf_ip_correlation_engine.get_rf_observation(binding.get('rf_observation_id'))

                    target_location = (
                        _projection_location(recon_entity)
                        or (
                            {
                                "lat": network_obs.lat,
                                "lon": network_obs.lon,
                                "alt_m": 0.0,
                            }
                            if network_obs and network_obs.lat is not None and network_obs.lon is not None
                            else None
                        )
                        or (
                            {
                                "lat": rf_obs.lat,
                                "lon": rf_obs.lon,
                                "alt_m": rf_obs.alt_m,
                            }
                            if rf_obs and rf_obs.lat is not None and rf_obs.lon is not None
                            else None
                        )
                    )
                    projection = _project_target(
                        observer,
                        target_location,
                        entity_id=recon_entity_id,
                        label=_projection_label(recon_entity or {}, recon_entity_id),
                        projection_type="RF_IP_BOUND",
                        source="rf_ip_binding",
                        confidence=binding.get('confidence', 0.5),
                        metadata={
                            "actor_label": (recon_entity or {}).get("actor_label") or ((recon_entity or {}).get("metadata") or {}).get("actor_label"),
                            "actor_summary": (recon_entity or {}).get("actor_summary") or ((recon_entity or {}).get("metadata") or {}).get("actor_summary"),
                            "binding_id": binding.get('binding_id'),
                            "rf_node_id": binding.get('rf_node_id'),
                            "score_components": binding.get('score_components') or {},
                            "evidence": binding.get('evidence') or {},
                        },
                    )
                    if not projection or projection['distance_m'] > max_distance_m:
                        continue

                    projections.append(projection)
                    seen_ids.add(recon_entity_id)
                    binding_count += 1
                    if len(projections) >= limit:
                        break

            if len(projections) < limit:
                for entity in recon_entities:
                    entity_id = str(entity.get('entity_id') or '')
                    if not entity_id or entity_id == observer.get('recon_entity_id') or entity_id in seen_ids:
                        continue

                    target_location = _projection_location(entity)
                    projection = _project_target(
                        observer,
                        target_location,
                        entity_id=entity_id,
                        label=_projection_label(entity, entity_id),
                        projection_type=str(entity.get('type') or 'RECON_ENTITY'),
                        source='recon_entity',
                        confidence=entity.get('confidence', 0.45),
                        metadata={
                            "actor_label": entity.get('actor_label') or (entity.get('metadata') or {}).get('actor_label'),
                            "actor_summary": entity.get('actor_summary') or (entity.get('metadata') or {}).get('actor_summary'),
                            "platform": entity.get('platform'),
                            "source": entity.get('source'),
                            "icon": entity.get('icon'),
                            "disposition": entity.get('disposition'),
                            "threat_level": entity.get('threat_level'),
                        },
                    )
                    if not projection or projection['distance_m'] > max_distance_m:
                        continue

                    projections.append(projection)
                    seen_ids.add(entity_id)
                    nearby_count += 1
                    if len(projections) >= limit:
                        break

            forecast_bundle = _build_control_path_forecasts(
                observer,
                recon_entities,
                limit=min(limit, 10),
                max_distance_m=max_distance_m,
            )

            projections.sort(key=lambda item: (-1 if item['type'] == 'RF_IP_BOUND' else 0, item['distance_m']))

            return jsonify({
                'status': 'ok',
                'observer': observer,
                'entity_count': len(projections),
                'counts': {
                    'bindings': binding_count,
                    'nearby_entities': nearby_count,
                    'forecast_paths': (forecast_bundle.get('counts') or {}).get('forecast_paths', 0),
                    'projectable_forecasts': (forecast_bundle.get('counts') or {}).get('projectable_forecasts', 0),
                },
                'entities': projections[:limit],
                'predictions': forecast_bundle.get('predictions') or [],
                'signals': forecast_bundle.get('signals') or {},
                'timestamp': time.time(),
            })
        except Exception as e:
            logger.error(f"Error building digital twin projection: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/sensors/<sensor_id>/process/lpi', methods=['POST'])
    def process_lpi_window(sensor_id):
        """
        LPI Worker: Process an IQ window through the LPI detection pipeline.
        Generates events: iq_window_received -> [tf_computed] -> [candidate_detected] -> [classified]

        Supports input Gating and Signal Simulation.
        """
        try:
            data = request.get_json() or {}

            # --- 1. Window & Format Standardization ---
            # Helper to merge defaults with request window
            req_window = data.get('window', {})
            t_now = time.time()
            window = {
                "t0": req_window.get('t0', t_now - 0.5),
                "t1": req_window.get('t1', t_now),
                "sample_rate_hz": req_window.get('sample_rate_hz', 2400000),
                "center_freq_hz": req_window.get('center_freq_hz', 915000000),
                "bandwidth_hz": req_window.get('bandwidth_hz', 2400000),
                "iq_format": req_window.get('iq_format', "cs16_iq_interleaved"),
                "endianness": req_window.get('endianness', "little"),
                "scale": req_window.get('scale', "full_scale=32767")
            }

            # Helper: Artifact Storage Stub
            def _store_artifact_stub(suffix: str = '.bin'):
                 # Simulate SHA256 of content
                import hashlib
                import uuid
                h = hashlib.sha256(str(uuid.uuid4()).encode()).hexdigest()
                return h, f"file:///var/data/artifacts/{h}{suffix}"

            # --- 2. Stage 0: Acquisition Event ---
            iq_hash, iq_ptr = _store_artifact_stub('.iq')
            iq_event = {
                'kind': 'iq_window_received',
                'timestamp': window['t1'],
                'window': window,
                'evidence': {
                    'iq_hash': iq_hash,
                    'iq_ptr': iq_ptr
                },
                'algo': {'name': 'acq', 'version': '1.0.0', 'params': {}},
                'confidence': 1.0,
                'persist_to_room': False # High volume, only local/ephemeral usually
            }

            events_generated = [iq_event]

            # --- 3. Simulation & Gating Logic ---
            simulate = data.get('simulate_detection', False)
            signal_family = data.get('signal_family', 'fmcw') # fmcw, phase_coded, noise_like
            snr_db = float(data.get('snr_db', 10.0))

            # Thresholds
            detection_threshold_snr = 3.0
            classification_threshold_snr = 6.0

            # Compute TF? (Always efficient if simulated)
            if simulate:
                tf_hash, tf_ptr = _store_artifact_stub('.npz')
                tf_event = {
                   'kind': 'tf_computed',
                   'timestamp': t_now + 0.05,
                   'algo': {'name': 'stft', 'version': '2.1.0', 'params': {'nfft': 2048, 'hop': 256}},
                   'feature_set_id': 'tf/stft/v2',
                   'payload': {
                       'summary': {
                           'max_bin_db': -40.0 + snr_db,
                           'occupied_bw_hz': window['bandwidth_hz'] * 0.4,
                           'noise_floor_db': -110.0
                       }
                   },
                   'evidence': {'iq_hash': iq_hash, 'artifact_ptrs': {'tf_matrix_npz': tf_ptr}},
                   'confidence': 0.95,
                   'persist_to_room': False
                }
                events_generated.append(tf_event)

                # Detection Gate
                if snr_db >= detection_threshold_snr:
                    # Stage 3: Candidate Detected
                    cand_hash, cand_ptr = _store_artifact_stub('.json')
                    candidate_event = {
                        'kind': 'lpi_candidate_detected',
                        'timestamp': t_now + 0.1,
                        'algo': {'name': 'lpi_detector', 'version': '1.0.0', 'params': {'algorithm': 'energy_detector'}},
                        'evidence': {'iq_hash': iq_hash, 'candidate_meta_ptr': cand_ptr},
                        'confidence': min(0.99, 0.5 + snr_db/40.0),
                        'persist_to_room': True
                    }
                    events_generated.append(candidate_event)

                    # Classification Gate
                    if snr_db >= classification_threshold_snr:
                        classes = []
                        est_params = {}

                        # Generate payload based on family
                        if signal_family == 'fmcw':
                            classes = [{'label': 'FMCW', 'p': 0.85}, {'label': 'LFM', 'p': 0.10}]
                            est_params = {'sweep_rate_hz_s': 1.2e12, 'bw_hz': 5e6}
                        elif signal_family == 'phase_coded':
                            classes = [{'label': 'PHASE_CODED', 'p': 0.78}, {'label': 'BPSK', 'p': 0.15}]
                            est_params = {'chip_rate_hz': 1.023e6, 'code_len': 1023}
                        elif signal_family == 'noise_like':
                            classes = [{'label': 'NOISE_LIKE', 'p': 0.65}, {'label': 'WIDEBAND_NOISE', 'p': 0.30}]
                            est_params = {'bandwidth_hz': 20e6, 'kurtosis': 3.1}

                        class_event = {
                            'kind': 'waveform_classified',
                            'timestamp': t_now + 0.2,
                            'algo': {'name': 'lpi_classifier', 'version': '0.3.2', 'params': {'model': 'xgb_v7'}},
                            'feature_set_id': 'lpi/features/v7',
                            'classes': [{"class": c['label'], "confidence": c['p']} for c in classes],
                            'estimated_params': est_params,
                            'confidence': classes[0]['p'],
                            'evidence': {'iq_hash': iq_hash},
                            'persist_to_room': True
                        }
                        events_generated.append(class_event)

            # --- 4. Emission to Room/Clients ---
            import uuid
            for evt in events_generated:
                # Inject SNR for UI convenience
                evt['snr_db'] = snr_db

                # Emit
                try:
                    activity_id = f"act:{sensor_id}:{evt['kind']}:{uuid.uuid4().hex[:8]}"
                    wrapper = {
                        'activity_id': activity_id,
                        'entity_type': 'SENSOR_ACTIVITY',
                        'activity_type': evt['kind'],
                        'sensor_id': sensor_id,
                        'payload': evt,
                        'timestamp': evt['timestamp']
                    }

                    import writebus
                    from writebus import WriteContext

                    manager = get_session_manager() if OPERATOR_MANAGER_AVAILABLE else None
                    global_room = manager.get_room_by_name("Global") if manager else None
                    write_res = writebus.bus().commit(
                        entity_id=activity_id,
                        entity_type="SENSOR_ACTIVITY",
                        entity_data=wrapper,
                        graph_ops=[],
                        ctx=WriteContext(
                            room_name=getattr(global_room, 'room_name', 'Global'),
                            operator_id=request.headers.get("X-Operator-Id") or "SYSTEM:LPI_PIPELINE",
                            request_id=request.headers.get("X-Request-Id"),
                            source="lpi_pipeline",
                            evidence_refs=[iq_hash] if iq_hash else [],
                        ),
                        persist=True,
                        audit=True,
                        room_id_override=getattr(global_room, 'room_id', None),
                    )
                    if not write_res.ok:
                        logger.warning(f"LPI Activity WriteBus Error: {write_res.errors}")
                except Exception as ex:
                    logger.warning(f"LPI Activity Emit Error: {ex}")

            return jsonify({
                'status': 'ok',
                'pipeline_trace': events_generated,
                'message': f'LPI pipeline processed (simulated={simulate}, family={signal_family}, snr={snr_db}dB)'
            })
        except Exception as e:
             logger.error(f"Error in LPI worker: {e}")
             return jsonify({'status': 'error', 'message': str(e)}), 500

    # ========================================================================
    # BATCH API ENDPOINTS (Optimization: reduce network round-trips)
    # ========================================================================

    @app.route('/api/recon/entities/batch', methods=['POST'])
    def get_recon_entities_batch():
        """
        Get multiple entities by ID in a single request.

        OPTIMIZATION: Reduces N API calls to 1 for fetching multiple entities.
        """
        try:
            data = request.get_json() or {}
            entity_ids = data.get('entity_ids', [])

            if not entity_ids:
                return jsonify({'status': 'error', 'message': 'entity_ids required'}), 400

            entities = recon_system.get_entities_batch(entity_ids)
            return jsonify({
                'status': 'ok',
                'requested': len(entity_ids),
                'found': len(entities),
                'entities': entities,
                'timestamp': time.time()
            })
        except Exception as e:
            logger.error(f"Error getting batch entities: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/recon/nearest', methods=['GET'])
    def get_nearest_entities():
        """
        Get the k nearest entities to the reference point.

        OPTIMIZATION: Uses spatial index for O(log n) query.
        """
        try:
            k = int(request.args.get('k', 10))
            entities = recon_system.get_nearest_entities(k)
            return jsonify({
                'status': 'ok',
                'k': k,
                'entity_count': len(entities),
                'entities': entities,
                'reference_point': recon_system.reference_point,
                'timestamp': time.time()
            })
        except Exception as e:
            logger.error(f"Error getting nearest entities: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/recon/changes', methods=['GET'])
    def get_changed_entities():
        """
        Get entities that have changed since a timestamp.

        OPTIMIZATION: For incremental frontend updates - only fetch changed data.
        """
        try:
            since = request.args.get('since')
            since_timestamp = float(since) if since else None

            entities = recon_system.get_changed_entities(since_timestamp)
            return jsonify({
                'status': 'ok',
                'entity_count': len(entities),
                'entities': entities,
                'since': since_timestamp,
                'timestamp': time.time()
            })
        except Exception as e:
            logger.error(f"Error getting changed entities: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/ais/vessels/batch', methods=['POST'])
    def get_ais_vessels_batch():
        """
        Get multiple AIS vessels by MMSI in a single request.

        OPTIMIZATION: Batch endpoint for AIS vessel data.
        """
        try:
            data = request.get_json() or {}
            mmsi_list = data.get('mmsi_list', [])

            if not mmsi_list:
                return jsonify({'status': 'error', 'message': 'mmsi_list required'}), 400

            vessels = []
            for mmsi in mmsi_list:
                vessel = ais_tracker.get_vessel(str(mmsi))
                if vessel:
                    vessels.append(vessel)

            return jsonify({
                'status': 'ok',
                'requested': len(mmsi_list),
                'found': len(vessels),
                'vessels': vessels,
                'timestamp': time.time()
            })
        except Exception as e:
            logger.error(f"Error getting batch vessels: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    # ========================================================================
    # PERFORMANCE METRICS ENDPOINT
    # ========================================================================

    @app.route('/api/metrics', methods=['GET'])
    def get_performance_metrics():
        """
        Get performance metrics for monitoring and optimization.

        Returns timing statistics for all tracked operations.
        """
        try:
            metrics = perf_metrics.get_all_stats()
            metrics['recon_performance'] = recon_system.get_status().get('performance', {})
            metrics['spatial_index'] = recon_system._spatial_index.get_stats()

            return jsonify({
                'status': 'ok',
                **metrics
            })
        except Exception as e:
            logger.error(f"Error getting metrics: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/metrics/log', methods=['POST'])
    def log_metrics():
        """
        Log metrics from frontend for persistent storage.

        Accepts single metric or batch of metrics.
        Request body:
        {
            "module": "recon",
            "metric_name": "update_time_ms",
            "value": 12.5,
            "metadata": {...},
            "session_id": "abc123"
        }

        Or for batch:
        {
            "batch": [
                {"module": "recon", "metric_name": "update_time_ms", "value": 12.5},
                {"module": "hypergraph", "metric_name": "node_count", "value": 150}
            ],
            "session_id": "abc123"
        }
        """
        try:
            data = request.get_json() or {}
            user_agent = request.headers.get('User-Agent', 'unknown')
            session_id = data.get('session_id', request.remote_addr)

            # Handle batch logging
            if 'batch' in data:
                entries = data['batch']
                for entry in entries:
                    entry['session_id'] = session_id
                    entry['user_agent'] = user_agent
                metrics_logger.log_batch(entries)
                return jsonify({
                    'status': 'ok',
                    'logged': len(entries),
                    'timestamp': time.time()
                })

            # Handle single metric
            module = data.get('module', 'frontend')
            metric_name = data.get('metric_name', 'unknown')
            value = data.get('value', 0)
            metadata = data.get('metadata', {})

            metrics_logger.log(
                module=module,
                metric_name=metric_name,
                value=value,
                metadata=metadata,
                session_id=session_id,
                user_agent=user_agent
            )

            return jsonify({
                'status': 'ok',
                'logged': 1,
                'timestamp': time.time()
            })
        except Exception as e:
            logger.error(f"Error logging metrics: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/metrics/interaction', methods=['POST'])
    def log_interaction():
        """
        Log user interaction events for analytics.

        Request body:
        {
            "action": "clicked_entity",
            "target": "drone-1",
            "details": {"panel": "recon", "zoom_level": 5000},
            "session_id": "abc123"
        }
        """
        try:
            data = request.get_json() or {}
            session_id = data.get('session_id', request.remote_addr)

            metrics_logger.log_interaction(
                action=data.get('action', 'unknown'),
                target=data.get('target'),
                details=data.get('details'),
                session_id=session_id
            )

            return jsonify({
                'status': 'ok',
                'timestamp': time.time()
            })
        except Exception as e:
            logger.error(f"Error logging interaction: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/metrics/session', methods=['GET'])
    def get_session_metrics():
        """Get summary of metrics collected this session."""
        try:
            summary = metrics_logger.get_session_summary()
            return jsonify({
                'status': 'ok',
                **summary
            })
        except Exception as e:
            logger.error(f"Error getting session metrics: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/metrics/query', methods=['GET'])
    def query_historical_metrics():
        """
        Query historical metrics from persistent storage.

        Query params:
            module: Filter by module name
            metric_name: Filter by metric name
            start_time: Unix timestamp start
            end_time: Unix timestamp end
            limit: Max results (default 1000)
        """
        try:
            module = request.args.get('module')
            metric_name = request.args.get('metric_name')
            start_time = request.args.get('start_time', type=float)
            end_time = request.args.get('end_time', type=float)
            limit = request.args.get('limit', 1000, type=int)

            results = metrics_logger.query_metrics(
                module=module,
                metric_name=metric_name,
                start_time=start_time,
                end_time=end_time,
                limit=limit
            )

            return jsonify({
                'status': 'ok',
                'count': len(results),
                'metrics': results
            })
        except Exception as e:
            logger.error(f"Error querying metrics: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    # ========================================================================
    # API ROUTES - POINTS OF INTEREST
    # ========================================================================

    @app.route('/api/poi/all', methods=['GET'])
    def get_all_pois():
        """Get all Points of Interest"""
        if not poi_manager:
            return jsonify({'status': 'error', 'message': 'POI Manager not available'}), 503
        try:
            pois = poi_manager.get_all_pois()
            return jsonify({
                'status': 'ok',
                'count': len(pois),
                'pois': pois
            })
        except Exception as e:
            logger.error(f"Error getting POIs: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/poi/visualization', methods=['GET'])
    def get_poi_visualization():
        """Get POI data formatted for Cesium visualization"""
        if not poi_manager:
            return jsonify({'status': 'error', 'message': 'POI Manager not available'}), 503
        try:
            data = poi_manager.get_visualization_data()
            return jsonify({
                'status': 'ok',
                **data
            })
        except Exception as e:
            logger.error(f"Error getting POI visualization: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/poi/category/<category>', methods=['GET'])
    def get_pois_by_category(category):
        """Get POIs filtered by category"""
        if not poi_manager:
            return jsonify({'status': 'error', 'message': 'POI Manager not available'}), 503
        try:
            pois = poi_manager.get_pois_by_category(category)
            return jsonify({
                'status': 'ok',
                'category': category,
                'count': len(pois),
                'pois': pois
            })
        except Exception as e:
            logger.error(f"Error getting POIs by category: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/poi/area', methods=['GET'])
    def get_pois_in_area():
        """Get POIs within a bounding box"""
        if not poi_manager:
            return jsonify({'status': 'error', 'message': 'POI Manager not available'}), 503
        try:
            min_lat = float(request.args.get('min_lat', -90))
            max_lat = float(request.args.get('max_lat', 90))
            min_lon = float(request.args.get('min_lon', -180))
            max_lon = float(request.args.get('max_lon', 180))

            pois = poi_manager.get_pois_in_area(min_lat, max_lat, min_lon, max_lon)
            return jsonify({
                'status': 'ok',
                'bounds': {'min_lat': min_lat, 'max_lat': max_lat, 'min_lon': min_lon, 'max_lon': max_lon},
                'count': len(pois),
                'pois': pois
            })
        except Exception as e:
            logger.error(f"Error getting POIs in area: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/poi/add', methods=['POST'])
    def add_poi():
        """Add a new POI manually"""
        if not poi_manager:
            return jsonify({'status': 'error', 'message': 'POI Manager not available'}), 503
        try:
            data = request.get_json()
            if not data:
                return jsonify({'status': 'error', 'message': 'No data provided'}), 400

            required = ['name', 'latitude', 'longitude']
            for field in required:
                if field not in data:
                    return jsonify({'status': 'error', 'message': f'Missing field: {field}'}), 400

            poi_id = poi_manager.add_poi(
                name=data['name'],
                latitude=float(data['latitude']),
                longitude=float(data['longitude']),
                description=data.get('description', ''),
                category=data.get('category', 'manual'),
                altitude=float(data.get('altitude', 0)),
                metadata=data.get('metadata')
            )

            return jsonify({
                'status': 'ok',
                'message': 'POI added successfully',
                'poi_id': poi_id
            })
        except Exception as e:
            logger.error(f"Error adding POI: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/poi/delete/<int:poi_id>', methods=['DELETE'])
    def delete_poi(poi_id):
        """Delete a POI by ID"""
        if not poi_manager:
            return jsonify({'status': 'error', 'message': 'POI Manager not available'}), 503
        try:
            deleted = poi_manager.delete_poi(poi_id)
            if deleted:
                return jsonify({'status': 'ok', 'message': f'POI {poi_id} deleted'})
            else:
                return jsonify({'status': 'error', 'message': f'POI {poi_id} not found'}), 404
        except Exception as e:
            logger.error(f"Error deleting POI: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/poi/import', methods=['POST'])
    def import_kmz():
        """Import POIs from a KMZ file path"""
        if not poi_manager:
            return jsonify({'status': 'error', 'message': 'POI Manager not available'}), 503
        try:
            data = request.get_json()
            if not data or 'file_path' not in data:
                return jsonify({'status': 'error', 'message': 'file_path required'}), 400

            file_path = data['file_path']
            category = data.get('category', 'imported')

            count = poi_manager.import_kmz(file_path, category=category)
            return jsonify({
                'status': 'ok',
                'message': f'Imported {count} POIs from {file_path}',
                'count': count
            })
        except Exception as e:
            logger.error(f"Error importing KMZ: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/poi/categories', methods=['GET'])
    def get_poi_categories():
        """Get list of POI categories"""
        if not poi_manager:
            return jsonify({'status': 'error', 'message': 'POI Manager not available'}), 503
        try:
            categories = poi_manager.get_categories()
            return jsonify({
                'status': 'ok',
                'categories': categories
            })
        except Exception as e:
            logger.error(f"Error getting categories: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/poi/status', methods=['GET'])
    def get_poi_status():
        """Get POI system status"""
        if not poi_manager:
            return jsonify({'status': 'error', 'message': 'POI Manager not available', 'available': False}), 503
        try:
            return jsonify({
                'status': 'ok',
                'available': True,
                'total_pois': poi_manager.get_poi_count(),
                'categories': poi_manager.get_categories(),
                'database': poi_manager.db_path
            })
        except Exception as e:
            logger.error(f"Error getting POI status: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    # ========================================================================
    # API ROUTES - OPERATOR SESSION MANAGEMENT & SSE STREAMING
    # ========================================================================

    @app.route('/api/operator/register', methods=['POST'])
    def operator_register():
        """Register a new operator"""
        if not operator_manager:
            return jsonify({'status': 'error', 'message': 'Operator Manager not available'}), 503

        try:
            data = request.get_json() or {}
            callsign = data.get('callsign')
            email = data.get('email')
            password = data.get('password')
            role = data.get('role', 'operator')
            team_id = data.get('team_id')

            if not all([callsign, email, password]):
                return jsonify({'status': 'error', 'message': 'Missing required fields: callsign, email, password'}), 400

            # Map role string to enum
            role_map = {
                'observer': OperatorRole.OBSERVER,
                'operator': OperatorRole.OPERATOR,
                'supervisor': OperatorRole.SUPERVISOR,
                'admin': OperatorRole.ADMIN
            }
            operator_role = role_map.get(role, OperatorRole.OPERATOR)

            operator = operator_manager.register_operator(
                callsign=callsign,
                email=email,
                password=password,
                role=operator_role,
                team_id=team_id
            )

            if operator:
                return jsonify({
                    'status': 'ok',
                    'message': 'Operator registered successfully',
                    'operator': operator.to_dict()
                })
            else:
                return jsonify({'status': 'error', 'message': 'Registration failed - callsign or email already exists'}), 409

        except Exception as e:
            logger.error(f"Error registering operator: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/operator/login', methods=['POST'])
    def operator_login():
        """Authenticate operator and create session"""
        if not operator_manager:
            return jsonify({'status': 'error', 'message': 'Operator Manager not available'}), 503

        try:
            data = request.get_json() or {}
            callsign = data.get('callsign')
            password = data.get('password')

            if not all([callsign, password]):
                return jsonify({'status': 'error', 'message': 'Missing callsign or password'}), 400

            session = operator_manager.authenticate(callsign, password)

            if session:
                operator = operator_manager.get_operator(session.operator_id)

                # Register session with orchestrator so gRPC TokenAuthInterceptor can validate it
                _register_session_with_orchestrator(session)

                return jsonify({
                    'status': 'ok',
                    'message': 'Login successful',
                    'session': session.to_dict(),
                    'operator': operator.to_dict() if operator else None
                })
            else:
                return jsonify({'status': 'error', 'message': 'Invalid callsign or password'}), 401

        except Exception as e:
            logger.error(f"Error during login: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/operator/logout', methods=['POST'])
    def operator_logout():
        """End operator session"""
        if not operator_manager:
            return jsonify({'status': 'error', 'message': 'Operator Manager not available'}), 503

        try:
            # Get token from header or body
            token = request.headers.get('X-Session-Token') or (request.get_json() or {}).get('session_token')

            if not token:
                return jsonify({'status': 'error', 'message': 'No session token provided'}), 400

            if operator_manager.logout(token):
                _revoke_session_with_orchestrator(token)
                return jsonify({'status': 'ok', 'message': 'Logged out successfully'})
            else:
                return jsonify({'status': 'error', 'message': 'Invalid session token'}), 401

        except Exception as e:
            logger.error(f"Error during logout: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/operator/issue-bootstrap', methods=['POST'])
    def issue_bootstrap():
        """Generate a one-time JWT bootstrap token for instance identity handoff."""
        if not operator_manager:
            return jsonify({'status': 'error', 'message': 'Operator Manager not available'}), 503

        auth_header = request.headers.get('Authorization', '')
        x_token = request.headers.get('X-Session-Token', '')
        data = request.get_json() or {}
        instance_id = data.get('instance_id')

        # Diagnostic logging
        token = auth_header.replace('Bearer ', '').strip() if auth_header else x_token
        logger.info(f"[Bootstrap] Auth: X-Session-Token={x_token}, AuthHeader={auth_header}")
        logger.info(f"[Bootstrap] DB Path: {operator_manager.db_path}")
        logger.info(f"[Bootstrap] Using token: {token[:24]}...")

        if not token or not instance_id:
            return jsonify({'status': 'error', 'message': 'Missing session or instance_id'}), 400

        session = operator_manager.validate_session(token)
        logger.info(f"[Bootstrap] Validation Result: {session}")

        if not session:
            return jsonify({'status': 'error', 'message': 'Invalid session'}), 401

        # Mint JWT bootstrap token
        import jwt
        now = datetime.utcnow()
        payload = {
            "sub": session.operator_id,
            "instance_id": instance_id,
            "iss": "scythe-orchestrator",
            "aud": "scythe-instance",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
            "scope": "bootstrap",
            "session_id": session.session_id
        }
        bootstrap_jwt = jwt.encode(payload, operator_manager.internal_token, algorithm="HS256")

        return jsonify({
            'status': 'ok',
            'bootstrap_token': bootstrap_jwt,
            'expires_in': 300
        })

    @app.route('/api/auth/exchange-bootstrap', methods=['POST'])
    def exchange_bootstrap():
        """Exchange a short-lived JWT bootstrap token for an instance session."""
        if not operator_manager:
            return jsonify({'status': 'error', 'message': 'Operator Manager not available'}), 503

        try:
            import jwt
            data = request.get_json() or {}
            token = data.get('bootstrap_token')
            instance_id = data.get('instance_id')

            if not token or not instance_id:
                return jsonify({'status': 'error', 'message': 'Missing bootstrap credentials'}), 400

            session = None
            internal_secret = operator_manager.internal_token or app.config.get('INTERNAL_TOKEN', '')

            if token.count('.') == 2 and internal_secret:
                claims = jwt.decode(token, internal_secret, algorithms=["HS256"], audience="scythe-instance")
                if claims.get("instance_id") != instance_id:
                    return jsonify({'status': 'error', 'message': 'Instance ID mismatch'}), 403
                session = operator_manager._create_session(claims["sub"])
            elif hasattr(operator_manager, 'exchange_bootstrap_token'):
                session = operator_manager.exchange_bootstrap_token(token, instance_id)

            if not session:
                return jsonify({'status': 'error', 'message': 'Invalid bootstrap token'}), 401

            _register_session_with_orchestrator(session)
            operator = operator_manager.get_operator(session.operator_id)

            return jsonify({
                'status': 'ok',
                'session': session.to_dict(),
                'operator': operator.to_dict() if operator else None
            })

        except jwt.ExpiredSignatureError:
            return jsonify({'status': 'error', 'message': 'Bootstrap token expired'}), 401
        except jwt.InvalidTokenError as e:
            return jsonify({'status': 'error', 'message': f'Invalid bootstrap token: {e}'}), 401
        except Exception as e:
            logger.error(f"Error during bootstrap exchange: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/operator/session', methods=['GET'])
    def operator_session_info():
        """Get current session info"""
        if not operator_manager:
            return jsonify({'status': 'error', 'message': 'Operator Manager not available'}), 503

        try:
            token = request.headers.get('X-Session-Token') or request.args.get('token')

            if not token:
                return jsonify({'status': 'error', 'message': 'No session token provided'}), 400

            session = operator_manager.validate_session(token)
            if session:
                operator = operator_manager.get_operator(session.operator_id)
                return jsonify({
                    'status': 'ok',
                    'session': session.to_dict(),
                    'operator': operator.to_dict() if operator else None
                })
            else:
                return jsonify({'status': 'error', 'message': 'Invalid or expired session'}), 401

        except Exception as e:
            logger.error(f"Error getting session info: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/operator/heartbeat', methods=['POST'])
    def operator_heartbeat():
        """Update session heartbeat and current view"""
        if not operator_manager:
            return jsonify({'status': 'error', 'message': 'Operator Manager not available'}), 503

        try:
            token = request.headers.get('X-Session-Token') or (request.get_json() or {}).get('session_token')
            data = request.get_json() or {}
            current_view = data.get('current_view')

            if not token:
                return jsonify({'status': 'error', 'message': 'No session token provided'}), 400

            if operator_manager.heartbeat(token, current_view):
                return jsonify({'status': 'ok', 'message': 'Heartbeat received'})
            else:
                return jsonify({'status': 'error', 'message': 'Invalid session'}), 401

        except Exception as e:
            logger.error(f"Error processing heartbeat: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/operator/active', methods=['GET'])
    def get_active_operators():
        """Get list of currently active operators"""
        if not operator_manager:
            return jsonify({'status': 'error', 'message': 'Operator Manager not available'}), 503

        try:
            active = operator_manager.get_active_operators()
            return jsonify({
                'status': 'ok',
                'count': len(active),
                'operators': active
            })
        except Exception as e:
            logger.error(f"Error getting active operators: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/operator/stats', methods=['GET'])
    def get_operator_stats():
        """Get operator system statistics"""
        if not operator_manager:
            return jsonify({'status': 'error', 'message': 'Operator Manager not available', 'available': False}), 503

        try:
            stats = operator_manager.get_stats()
            return jsonify({
                'status': 'ok',
                'available': True,
                'stats': stats
            })
        except Exception as e:
            logger.error(f"Error getting operator stats: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/operator/update', methods=['PUT'])
    def update_operator_profile():
        """Update operator profile information"""
        if not operator_manager:
            return jsonify({'status': 'error', 'message': 'Operator Manager not available', 'available': False}), 503

        try:
            # Get session token from header or query param
            token = request.headers.get('X-Session-Token') or request.args.get('token')
            if not token:
                return jsonify({'status': 'error', 'message': 'No session token provided'}), 401

            # Validate session
            session = operator_manager.validate_session(token)
            if not session:
                return jsonify({'status': 'error', 'message': 'Invalid or expired session'}), 401

            # Get operator
            operator = operator_manager.get_operator(session.operator_id)
            if not operator:
                return jsonify({'status': 'error', 'message': 'Operator not found'}), 404

            # Get update data
            data = request.get_json() or {}
            new_callsign = data.get('callsign')
            new_email = data.get('email')

            if not new_callsign or not new_email:
                return jsonify({'status': 'error', 'message': 'Callsign and email are required'}), 400

            # Update operator
            operator.callsign = new_callsign
            operator.email = new_email

            # Save changes
            operator_manager.save_operator(operator)

            logger.info(f"Operator {operator.operator_id} updated profile: callsign={new_callsign}, email={new_email}")

            return jsonify({
                'status': 'ok',
                'message': 'Profile updated successfully',
                'user': operator.to_dict()
            })

        except Exception as e:
            logger.error(f"Error updating operator profile: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/operator/totp/setup', methods=['POST'])
    def setup_totp():
        """Generate TOTP secret for user setup"""
        if not operator_manager:
            return jsonify({'status': 'error', 'message': 'Operator Manager not available'}), 503

        try:
            token = request.headers.get('X-Session-Token') or request.args.get('token')
            if not token:
                return jsonify({'status': 'error', 'message': 'No session token provided'}), 401

            session = operator_manager.validate_session(token)
            if not session:
                return jsonify({'status': 'error', 'message': 'Invalid session'}), 401

            operator = operator_manager.get_operator(session.operator_id)
            if not operator:
                return jsonify({'status': 'error', 'message': 'Operator not found'}), 404

            # Generate TOTP secret using FusionAuth if available
            if fa_client:
                try:
                    response = fa_client.generate_two_factor_secret()
                    if response.was_successful():
                        secret_data = response.success_response
                        return jsonify({
                            'status': 'ok',
                            'secret': secret_data.get('secret'),
                            'qr_code_url': secret_data.get('qrCode', {}).get('image'),
                            'algorithm': secret_data.get('algorithm', 'TOTP'),
                            'digits': secret_data.get('digits', 6),
                            'period': secret_data.get('period', 30)
                        })
                except Exception as e:
                    logger.warning(f"FusionAuth TOTP setup failed: {e}")

            # Fallback: Generate basic TOTP secret
            import base64
            import secrets
            import pyotp
            import urllib.parse

            # Generate a random 32-byte secret
            secret_bytes = secrets.token_bytes(32)
            secret = base64.b32encode(secret_bytes).decode('utf-8')

            # Create TOTP object to generate QR code
            totp = pyotp.TOTP(secret)
            provisioning_uri = totp.provisioning_uri(name=operator.callsign, issuer_name="RF SCYTHE")

            # Properly URL-encode the provisioning URI for the QR code service
            encoded_uri = urllib.parse.quote(provisioning_uri, safe='')
            qr_code_url = f"https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={encoded_uri}"

            return jsonify({
                'status': 'ok',
                'secret': secret,
                'qr_code_url': qr_code_url,
                'algorithm': 'TOTP',
                'digits': 6,
                'period': 30
            })

        except Exception as e:
            logger.error(f"Error setting up TOTP: {e}", exc_info=True)
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/operator/totp/enable', methods=['POST'])
    def enable_totp():
        """Enable TOTP for user after verification"""
        if not operator_manager:
            return jsonify({'status': 'error', 'message': 'Operator Manager not available'}), 503

        try:
            token = request.headers.get('X-Session-Token') or request.args.get('token')
            if not token:
                return jsonify({'status': 'error', 'message': 'No session token provided'}), 401

            session = operator_manager.validate_session(token)
            if not session:
                return jsonify({'status': 'error', 'message': 'Invalid session'}), 401

            data = request.get_json() or {}
            secret = data.get('secret')
            code = data.get('code')

            if not secret or not code:
                return jsonify({'status': 'error', 'message': 'Secret and verification code required'}), 400

            # Verify the code first
            import pyotp
            totp = pyotp.TOTP(secret)
            if not totp.verify(code):
                return jsonify({'status': 'error', 'message': 'Invalid verification code'}), 400

            # Enable TOTP using FusionAuth if available
            if fa_client:
                try:
                    enable_request = {
                        'secret': secret,
                        'code': code
                    }
                    response = fa_client.enable_two_factor(session.operator_id, enable_request)
                    if response.was_successful():
                        return jsonify({'status': 'ok', 'message': 'TOTP enabled successfully'})
                except Exception as e:
                    logger.warning(f"FusionAuth TOTP enable failed: {e}")

            # Fallback: Store TOTP secret in operator data
            operator = operator_manager.get_operator(session.operator_id)
            if operator:
                # Store TOTP secret in operator's data field
                if not hasattr(operator, 'data') or operator.data is None:
                    operator.data = {}
                operator.data['totp_secret'] = secret
                operator.data['totp_enabled'] = True
                operator_manager.save_operator(operator)

                logger.info(f"TOTP enabled for operator {session.operator_id}")
                return jsonify({'status': 'ok', 'message': 'TOTP enabled successfully'})

            return jsonify({'status': 'error', 'message': 'Failed to enable TOTP'}), 500

        except Exception as e:
            logger.error(f"Error enabling TOTP: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/operator/totp/verify', methods=['POST'])
    def verify_totp():
        """Verify TOTP code for login"""
        if not operator_manager:
            return jsonify({'status': 'error', 'message': 'Operator Manager not available'}), 503

        try:
            data = request.get_json() or {}
            callsign = data.get('callsign')
            totp_code = data.get('totp_code')

            if not callsign or not totp_code:
                return jsonify({'status': 'error', 'message': 'Callsign and TOTP code required'}), 400

            # Get operator
            operator = operator_manager.get_operator_by_callsign(callsign)
            if not operator:
                return jsonify({'status': 'error', 'message': 'Operator not found'}), 404

            # Check if TOTP is enabled
            totp_secret = None
            if hasattr(operator, 'data') and operator.data:
                totp_secret = operator.data.get('totp_secret')

            if not totp_secret:
                return jsonify({'status': 'error', 'message': 'TOTP not enabled for this user'}), 400

            # Verify TOTP code
            import pyotp
            totp = pyotp.TOTP(totp_secret)
            if totp.verify(totp_code):
                # Create session for successful 2FA
                session = operator_manager.authenticate_with_2fa(operator.operator_id)
                if session:
                    return jsonify({
                        'status': 'ok',
                        'session': session.to_dict(),
                        'user': operator.to_dict()
                    })
                else:
                    return jsonify({'status': 'error', 'message': 'Failed to create session'}), 500
            else:
                return jsonify({'status': 'error', 'message': 'Invalid TOTP code'}), 400

        except Exception as e:
            logger.error(f"Error verifying TOTP: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/operator/totp/status', methods=['GET'])
    def get_totp_status():
        """Get TOTP status for current user"""
        if not operator_manager:
            return jsonify({'status': 'error', 'message': 'Operator Manager not available'}), 503

        try:
            token = request.headers.get('X-Session-Token') or request.args.get('token')
            if not token:
                return jsonify({'status': 'error', 'message': 'No session token provided'}), 401

            session = operator_manager.validate_session(token)
            if not session:
                return jsonify({'status': 'error', 'message': 'Invalid session'}), 401

            operator = operator_manager.get_operator(session.operator_id)
            if not operator:
                return jsonify({'status': 'error', 'message': 'Operator not found'}), 404

            # Check TOTP status
            totp_enabled = False
            if hasattr(operator, 'data') and operator.data:
                totp_enabled = operator.data.get('totp_enabled', False)

            return jsonify({
                'status': 'ok',
                'totp_enabled': totp_enabled
            })

        except Exception as e:
            logger.error(f"Error getting TOTP status: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    # ========================================================================
    # API ROUTES - SSE ENTITY STREAMING
    # ========================================================================

    @app.route('/api/entities/stream', methods=['GET'])
    def entity_stream():
        """
        Server-Sent Events endpoint for real-time entity synchronization.

        Query params:
            token: Session token for authentication

        Events:
            PREEXISTING - Initial sync of existing entities
            CREATE - New entity created
            UPDATE - Entity modified
            DELETE - Entity removed
            HEARTBEAT - Keep-alive signal
        """
        if not operator_manager:
            return jsonify({'status': 'error', 'message': 'Operator Manager not available'}), 503

        token = request.args.get('token')
        if not token:
            return jsonify({'status': 'error', 'message': 'Session token required'}), 401

        client = operator_manager.register_sse_client(token)
        if not client:
            return jsonify({'status': 'error', 'message': 'Invalid session token'}), 401

        # Optional replay since sequence id
        since = request.args.get('since')
        if since:
            try:
                operator_manager.replay_events_since(client, int(since))
            except Exception:
                pass

        def generate():
            try:
                for event_data in operator_manager.sse_event_generator(client):
                    yield event_data
            except GeneratorExit:
                operator_manager.unregister_sse_client(client.session_id)
            except Exception as e:
                logger.error(f"SSE stream error: {e}")
                operator_manager.unregister_sse_client(client.session_id)

        return Response(
            generate(),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
                'X-Accel-Buffering': 'no'  # Disable nginx buffering
            }
        )

    @app.route('/api/hypergraph/diff/stream', methods=['GET'])
    def hypergraph_diff_stream():
        """SSE stream that pushes Subgraph Diffs scoped to a DSL query.

        Query params:
            token: session token (required)
            dsl: URL-encoded DSL string (required)
            since: optional sequence id to start from
            query_id: optional client query id
        """
        if SubgraphDiffGenerator is None or QueryPredicate is None:
            return jsonify({'status': 'error', 'message': 'Subgraph diff module not available'}), 500

        if not operator_manager:
            return jsonify({'status': 'error', 'message': 'Operator Manager not available'}), 503

        token = request.args.get('token')
        if not token:
            return jsonify({'status': 'error', 'message': 'Session token required'}), 401

        client = operator_manager.register_sse_client(token)
        if not client:
            return jsonify({'status': 'error', 'message': 'Invalid session token'}), 401

        # Accept either a query_id referencing a registered DSL, or a raw dsl param
        query_id_param = request.args.get('query_id')
        dsl = request.args.get('dsl') or ''
        parsed = {}
        query_id = query_id_param

        if query_id_param:
            try:
                with REGISTERED_QUERIES_LOCK:
                    entry = REGISTERED_QUERIES.get(query_id_param)
                if entry:
                    parsed = entry.get('parsed') or {}
                else:
                    return jsonify({'status': 'error', 'message': 'Unknown query_id'}), 404
            except Exception:
                parsed = {}
        else:
            if not dsl:
                return jsonify({'status': 'error', 'message': 'DSL required as query param when no query_id provided'}), 400
            try:
                parsed = parse_dsl(dsl) if parse_dsl else {}
            except Exception:
                parsed = {}
            query_id = query_id or (parsed.get('query_id') if isinstance(parsed, dict) else None) or 'query'

        predicate = QueryPredicate(parsed)

        # engine selection
        engine = globals().get('hypergraph_engine') or globals().get('hypergraph_store')
        redis_conn = globals().get('redis_client')
        gen = SubgraphDiffGenerator(engine, operator_manager=operator_manager, redis_client=redis_conn)

        # starting sequence
        since = request.args.get('since')
        try:
            last_seq = int(since) if since else (operator_manager.entity_sequence if operator_manager else 0)
        except Exception:
            last_seq = 0

        query_id = request.args.get('query_id') or parsed.get('query_id') or 'query'

        cond = threading.Condition()
        max_seq = {'v': last_seq}

        # subscribe to graph_event_bus if present
        subscription = None
        try:
            if 'graph_event_bus' in globals() and graph_event_bus is not None:
                def _on_event(ge):
                    try:
                        seq = getattr(ge, 'sequence_id', None) or ge.get('sequence_id') if isinstance(ge, dict) else None
                        if seq is None:
                            seq = getattr(ge, 'sequence', None)
                        if seq is None:
                            return
                        with cond:
                            if seq > max_seq['v']:
                                max_seq['v'] = int(seq)
                            cond.notify()
                    except Exception:
                        pass

                try:
                    graph_event_bus.subscribe(_on_event)
                    subscription = _on_event
                except Exception:
                    subscription = None
        except Exception:
            subscription = None

        def generate():
            nonlocal last_seq
            try:
                while True:
                    # wait until new events or timeout
                    with cond:
                        cond.wait(timeout=25.0)
                        current = max_seq['v']

                    if current is None:
                        current = last_seq

                    if current > last_seq:
                        try:
                            diff = gen.generate_diff(query_id, predicate, last_seq, current)
                            last_seq = current
                            payload = json.dumps(diff)
                            yield f"event: DIFF\n"
                            yield f"data: {payload}\n\n"
                        except GeneratorExit:
                            break
                        except Exception as e:
                            logger.debug(f"Error producing diff: {e}")
                    else:
                        # heartbeat with current sequence
                        hb = json.dumps({'query_id': query_id, 'to_sequence': last_seq, 'timestamp': datetime.utcnow().isoformat() + 'Z'})
                        try:
                            yield f"event: HEARTBEAT\n"
                            yield f"data: {hb}\n\n"
                        except GeneratorExit:
                            break
                    # loop continues
            finally:
                try:
                    if subscription and 'graph_event_bus' in globals() and graph_event_bus is not None:
                        try:
                            graph_event_bus.unsubscribe(subscription)
                        except Exception:
                            pass
                except Exception:
                    pass

        return Response(
            generate(),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
                'X-Accel-Buffering': 'no'
            }
        )

    @app.route('/api/entities/publish', methods=['POST'])
    def publish_entity():
        """
        Publish or update an entity - broadcasts to all connected clients.

        Request body:
            entity_id: Unique entity identifier
            entity_type: Type of entity (poi, target, asset, etc.)
            entity_data: Entity data object
        """
        if not operator_manager:
            return jsonify({'status': 'error', 'message': 'Operator Manager not available'}), 503

        try:
            token = request.headers.get('X-Session-Token')
            if not token:
                return jsonify({'status': 'error', 'message': 'Session token required'}), 401

            operator = operator_manager.get_operator_for_session(token)
            if not operator:
                return jsonify({'status': 'error', 'message': 'Invalid session'}), 401

            data = request.get_json() or {}
            entity_id = data.get('entity_id')
            entity_type = data.get('entity_type', 'unknown')
            entity_data = data.get('entity_data', {})

            if not entity_id:
                return jsonify({'status': 'error', 'message': 'entity_id required'}), 400

            global_room = operator_manager.get_room_by_name("Global")
            if not global_room:
                return jsonify({'status': 'error', 'message': 'Global room not found'}), 404

            import writebus
            from writebus import WriteContext

            result = writebus.bus().commit(
                entity_id=entity_id,
                entity_type=entity_type,
                entity_data=entity_data,
                graph_ops=[],
                ctx=WriteContext(
                    room_name=getattr(global_room, 'room_name', 'Global'),
                    operator=operator,
                    operator_id=getattr(operator, 'operator_id', None) or "SYSTEM:ENTITY_API",
                    session_token=token,
                    request_id=request.headers.get("X-Request-Id") or data.get("request_id"),
                    source="entity_publish_api",
                ),
                persist=True,
                audit=True,
                idempotency_key=data.get("idempotency_key"),
                room_id_override=getattr(global_room, 'room_id', None),
            )
            if not result.ok:
                return jsonify({
                    'status': 'error',
                    'message': 'WriteBus commit failed',
                    'write_result': {
                        'commit_status': result.commit_status,
                        'errors': result.errors,
                        'debug': result.debug,
                    }
                }), 500

            return jsonify({
                'status': 'ok',
                'message': 'Entity published',
                'entity_id': entity_id,
                'write_result': {
                    'commit_status': result.commit_status,
                    'debug': result.debug,
                }
            })

        except Exception as e:
            logger.error(f"Error publishing entity: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/entities/delete/<entity_id>', methods=['DELETE'])
    def delete_entity(entity_id):
        """Delete an entity - broadcasts removal to all connected clients"""
        if not operator_manager:
            return jsonify({'status': 'error', 'message': 'Operator Manager not available'}), 503

        try:
            token = request.headers.get('X-Session-Token')
            if not token:
                return jsonify({'status': 'error', 'message': 'Session token required'}), 401

            operator = operator_manager.get_operator_for_session(token)
            if not operator:
                return jsonify({'status': 'error', 'message': 'Invalid session'}), 401

            global_room = operator_manager.get_room_by_name("Global")
            if not global_room:
                return jsonify({'status': 'error', 'message': 'Global room not found'}), 404

            import writebus
            from writebus import WriteContext

            result = writebus.bus().commit(
                entity_id=entity_id,
                entity_type="ENTITY_TOMBSTONE",
                entity_data={
                    "entity_id": entity_id,
                    "type": "ENTITY_TOMBSTONE",
                    "deleted": True,
                    "deleted_at": time.time(),
                },
                graph_ops=[],
                ctx=WriteContext(
                    room_name=getattr(global_room, 'room_name', 'Global'),
                    operator=operator,
                    operator_id=getattr(operator, 'operator_id', None) or "SYSTEM:ENTITY_API",
                    session_token=token,
                    request_id=request.headers.get("X-Request-Id"),
                    source="entity_delete_api",
                ),
                persist=True,
                audit=True,
                room_id_override=getattr(global_room, 'room_id', None),
            )
            if not result.ok:
                return jsonify({
                    'status': 'error',
                    'message': 'WriteBus delete commit failed',
                    'write_result': {
                        'commit_status': result.commit_status,
                        'errors': result.errors,
                        'debug': result.debug,
                    }
                }), 500

            return jsonify({
                'status': 'ok',
                'message': 'Entity deleted',
                'entity_id': entity_id,
                'write_result': {
                    'commit_status': result.commit_status,
                    'debug': result.debug,
                }
            })

        except Exception as e:
            logger.error(f"Error deleting entity: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/entities/cached', methods=['GET'])
    def get_cached_entities():
        """Get all currently cached entities"""
        if not operator_manager:
            return jsonify({'status': 'error', 'message': 'Operator Manager not available'}), 503

        try:
            return jsonify({
                'status': 'ok',
                'count': len(operator_manager.entity_cache),
                'entities': list(operator_manager.entity_cache.values())
            })
        except Exception as e:
            logger.error(f"Error getting cached entities: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    # ========================================================================
    # API ROUTES - TEAM MANAGEMENT
    # ========================================================================

    @app.route('/api/team/create', methods=['POST'])
    def create_team():
        """Create a new team"""
        if not operator_manager:
            return jsonify({'status': 'error', 'message': 'Operator Manager not available'}), 503

        try:
            token = request.headers.get('X-Session-Token')
            data = request.get_json() or {}
            team_name = data.get('team_name')

            if not team_name:
                return jsonify({'status': 'error', 'message': 'team_name required'}), 400

            operator = operator_manager.get_operator_for_session(token) if token else None
            team_id = operator_manager.create_team(team_name, operator.operator_id if operator else None)

            if team_id:
                return jsonify({
                    'status': 'ok',
                    'message': 'Team created',
                    'team_id': team_id,
                    'team_name': team_name
                })
            else:
                return jsonify({'status': 'error', 'message': 'Team name already exists'}), 409

        except Exception as e:
            logger.error(f"Error creating team: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/team/<team_id>/members', methods=['GET'])
    def get_team_members(team_id):
        """Get members of a team"""
        if not operator_manager:
            return jsonify({'status': 'error', 'message': 'Operator Manager not available'}), 503

        try:
            members = operator_manager.get_team_members(team_id)
            return jsonify({
                'status': 'ok',
                'team_id': team_id,
                'count': len(members),
                'members': [m.to_dict() for m in members]
            })
        except Exception as e:
            logger.error(f"Error getting team members: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/team/<team_id>/assign', methods=['POST'])
    def assign_to_team(team_id):
        """Assign an operator to a team"""
        if not operator_manager:
            return jsonify({'status': 'error', 'message': 'Operator Manager not available'}), 503

        try:
            data = request.get_json() or {}
            operator_id = data.get('operator_id')

            if not operator_id:
                return jsonify({'status': 'error', 'message': 'operator_id required'}), 400

            if operator_manager.assign_to_team(operator_id, team_id):
                return jsonify({
                    'status': 'ok',
                    'message': 'Operator assigned to team',
                    'operator_id': operator_id,
                    'team_id': team_id
                })
            else:
                return jsonify({'status': 'error', 'message': 'Assignment failed'}), 400

        except Exception as e:
            logger.error(f"Error assigning to team: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    # ========================================================================
    # API ROUTES - ROOM/CHANNEL MANAGEMENT
    # ========================================================================

    @app.route('/api/rooms', methods=['GET'])
    def list_rooms():
        """List all available rooms"""
        if not operator_manager:
            return jsonify({'status': 'error', 'message': 'Operator Manager not available'}), 503

        try:
            include_private = request.args.get('include_private', 'false').lower() == 'true'
            rooms = operator_manager.list_rooms(include_private=include_private)
            return jsonify({
                'status': 'ok',
                'count': len(rooms),
                'rooms': rooms
            })
        except Exception as e:
            logger.error(f"Error listing rooms: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/rooms/create', methods=['POST'])
    def create_room():
        """Create a new room"""
        if not operator_manager:
            return jsonify({'status': 'error', 'message': 'Operator Manager not available'}), 503

        try:
            token = request.headers.get('X-Session-Token')
            data = request.get_json() or {}

            room_name = data.get('room_name')
            if not room_name:
                return jsonify({'status': 'error', 'message': 'room_name required'}), 400

            operator = operator_manager.get_operator_for_session(token) if token else None

            room = operator_manager.create_room(
                room_name=room_name,
                room_type=data.get('room_type', 'custom'),
                created_by=operator.operator_id if operator else None,
                capacity=data.get('capacity', 50),
                is_private=data.get('is_private', False),
                password=data.get('password'),
                metadata=data.get('metadata')
            )

            if room:
                # Auto-join creator to the room
                session = operator_manager.validate_session(token) if token else None
                if session:
                    operator_manager.join_room(room.room_id, session.session_id)

                return jsonify({
                    'status': 'ok',
                    'message': 'Room created',
                    'room': room.to_dict()
                })
            else:
                return jsonify({'status': 'error', 'message': 'Room name already exists'}), 409

        except Exception as e:
            logger.error(f"Error creating room: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/rooms/<room_id>', methods=['GET'])
    def get_room(room_id):
        """Get room details"""
        if not operator_manager:
            return jsonify({'status': 'error', 'message': 'Operator Manager not available'}), 503

        try:
            room = operator_manager.get_room(room_id)
            if not room:
                return jsonify({'status': 'error', 'message': 'Room not found'}), 404

            members = operator_manager.get_room_members(room_id)
            entities = operator_manager.room_entities.get(room_id, {})

            return jsonify({
                'status': 'ok',
                'room': room.to_dict(),
                'member_count': len(members),
                'members': members,
                'entity_count': len(entities),
                'entities': list(entities.values())
            })
        except Exception as e:
            logger.error(f"Error getting room: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/rooms/<room_id>/join', methods=['POST'])
    def join_room_route(room_id):
        """Join a room"""
        if not operator_manager:
            return jsonify({'status': 'error', 'message': 'Operator Manager not available'}), 503

        try:
            token = request.headers.get('X-Session-Token')
            if not token:
                return jsonify({'status': 'error', 'message': 'Session token required'}), 401

            session = operator_manager.validate_session(token)
            if not session:
                return jsonify({'status': 'error', 'message': 'Invalid session'}), 401

            data = request.get_json() or {}
            password = data.get('password')

            success, message = operator_manager.join_room(room_id, session.session_id, password)

            if success:
                room = operator_manager.get_room(room_id)
                return jsonify({
                    'status': 'ok',
                    'message': message,
                    'room': room.to_dict() if room else None
                })
            else:
                return jsonify({'status': 'error', 'message': message}), 400

        except Exception as e:
            logger.error(f"Error joining room: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/rooms/<room_id>/leave', methods=['POST'])
    def leave_room_route(room_id):
        """Leave a room"""
        if not operator_manager:
            return jsonify({'status': 'error', 'message': 'Operator Manager not available'}), 503

        try:
            token = request.headers.get('X-Session-Token')
            if not token:
                return jsonify({'status': 'error', 'message': 'Session token required'}), 401

            session = operator_manager.validate_session(token)
            if not session:
                return jsonify({'status': 'error', 'message': 'Invalid session'}), 401

            success, message = operator_manager.leave_room(room_id, session.session_id)

            return jsonify({
                'status': 'ok' if success else 'error',
                'message': message
            })

        except Exception as e:
            logger.error(f"Error leaving room: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/rooms/<room_id>/close', methods=['DELETE'])
    def close_room_route(room_id):
        """Close/delete a room"""
        if not operator_manager:
            return jsonify({'status': 'error', 'message': 'Operator Manager not available'}), 503

        try:
            token = request.headers.get('X-Session-Token')
            operator = operator_manager.get_operator_for_session(token) if token else None

            success, message = operator_manager.close_room(
                room_id,
                operator.operator_id if operator else "system"
            )

            return jsonify({
                'status': 'ok' if success else 'error',
                'message': message
            })

        except Exception as e:
            logger.error(f"Error closing room: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/rooms/<room_id>/members', methods=['GET'])
    def get_room_members_route(room_id):
        """Get members of a room"""
        if not operator_manager:
            return jsonify({'status': 'error', 'message': 'Operator Manager not available'}), 503

        try:
            members = operator_manager.get_room_members(room_id)
            return jsonify({
                'status': 'ok',
                'room_id': room_id,
                'count': len(members),
                'members': members
            })
        except Exception as e:
            logger.error(f"Error getting room members: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/rooms/<room_id>/entities', methods=['GET'])
    def get_room_entities_route(room_id):
        """Get entities in a room"""
        if not operator_manager:
            return jsonify({'status': 'error', 'message': 'Operator Manager not available'}), 503

        try:
            entities = operator_manager.room_entities.get(room_id, {})
            return jsonify({
                'status': 'ok',
                'room_id': room_id,
                'count': len(entities),
                'entities': list(entities.values())
            })
        except Exception as e:
            logger.error(f"Error getting room entities: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/rooms/<room_id>/publish', methods=['POST'])
    def publish_to_room_route(room_id):
        """Publish an entity to a room"""
        if not operator_manager:
            return jsonify({'status': 'error', 'message': 'Operator Manager not available'}), 503

        try:
            token = request.headers.get('X-Session-Token')
            session = operator_manager.validate_session(token) if token else None
            operator = operator_manager.get_operator_for_session(token) if token else None

            data = request.get_json() or {}
            entity_id = data.get('entity_id')
            entity_type = data.get('entity_type', 'entity')
            entity_data = data.get('entity_data', {})

            if not entity_id:
                return jsonify({'status': 'error', 'message': 'entity_id required'}), 400

            room = operator_manager.get_room(room_id)
            if not room:
                return jsonify({'status': 'error', 'message': 'Room not found'}), 404

            import writebus
            from writebus import WriteContext

            result = writebus.bus().commit(
                entity_id=entity_id,
                entity_type=entity_type,
                entity_data=entity_data,
                graph_ops=[],
                ctx=WriteContext(
                    room_name=getattr(room, 'room_name', 'Global'),
                    operator=operator,
                    operator_id=(
                        getattr(operator, 'operator_id', None)
                        or request.headers.get('X-Operator-Id')
                        or data.get('operator_id')
                        or "SYSTEM:ROOM_API"
                    ),
                    session_token=token,
                    request_id=request.headers.get("X-Request-Id") or data.get("request_id"),
                    source="room_publish_api",
                ),
                persist=True,
                audit=True,
                idempotency_key=data.get("idempotency_key"),
                room_id_override=room_id,
            )

            if result.ok:
                return jsonify({
                    'status': 'ok',
                    'message': 'Entity published to room',
                    'room_id': room_id,
                    'entity_id': entity_id,
                    'write_result': {
                        'commit_status': result.commit_status,
                        'debug': result.debug,
                    }
                })
            else:
                return jsonify({
                    'status': 'error',
                    'message': 'WriteBus commit failed',
                    'write_result': {
                        'commit_status': result.commit_status,
                        'errors': result.errors,
                        'debug': result.debug,
                    }
                }), 500

        except Exception as e:
            logger.error(f"Error publishing to room: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/rooms/<room_id>/message', methods=['POST'])
    def send_room_message_route(room_id):
        """Send a message to a room"""
        if not operator_manager:
            return jsonify({'status': 'error', 'message': 'Operator Manager not available'}), 503

        try:
            token = request.headers.get('X-Session-Token')
            if not token:
                return jsonify({'status': 'error', 'message': 'Session token required'}), 401

            operator = operator_manager.get_operator_for_session(token)
            if not operator:
                return jsonify({'status': 'error', 'message': 'Invalid session'}), 401

            data = request.get_json() or {}
            message = data.get('message', '')
            message_type = data.get('message_type', 'chat')

            if not message:
                return jsonify({'status': 'error', 'message': 'message required'}), 400

            success = operator_manager.send_message_to_room(room_id, message, operator, message_type)

            if success:
                return jsonify({'status': 'ok', 'message': 'Message sent'})
            else:
                return jsonify({'status': 'error', 'message': 'Room not found'}), 404

        except Exception as e:
            logger.error(f"Error sending room message: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    # ── Guest chat system (no auth required) ───────────────
    # In-memory store: {room_id: deque(maxlen=200)} of message dicts
    # SSE subscriber registry: {room_id: [Queue, ...]}
    import queue as _chat_queue
    _guest_chat_rooms = defaultdict(lambda: deque(maxlen=200))
    _guest_chat_subs = defaultdict(list)  # room_id -> list of Queue

    def _guest_chat_broadcast(room_id, msg_dict):
        """Push msg_dict to all SSE subscribers for this room."""
        dead = []
        for q in _guest_chat_subs[room_id]:
            try:
                q.put_nowait(msg_dict)
            except Exception:
                dead.append(q)
        for q in dead:
            try:
                _guest_chat_subs[room_id].remove(q)
            except ValueError:
                pass

    @app.route('/api/chat/<room_id>/stream', methods=['GET'])
    def guest_chat_stream(room_id):
        """SSE stream for guest chat — no auth required."""
        import queue as _q_mod

        def _generate():
            q = _q_mod.Queue(maxsize=100)
            _guest_chat_subs[room_id].append(q)
            try:
                # Push last 50 messages as history on connect
                history = list(_guest_chat_rooms[room_id])[-50:]
                for msg in history:
                    yield f'data: {json.dumps(msg, default=str)}\n\n'
                yield 'data: {"type":"connected","room":"' + room_id + '"}\n\n'
                while True:
                    try:
                        event = q.get(timeout=20)
                        yield f'data: {json.dumps(event, default=str)}\n\n'
                    except _q_mod.Empty:
                        yield ': keepalive\n\n'
            finally:
                try:
                    _guest_chat_subs[room_id].remove(q)
                except ValueError:
                    pass

        resp = app.response_class(
            _generate(),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no',
                'Connection': 'keep-alive',
                'Access-Control-Allow-Origin': '*',
            },
        )
        return resp

    @app.route('/api/chat/<room_id>/send', methods=['POST'])
    def guest_chat_send(room_id):
        """Send a chat message — no auth required."""
        try:
            data = request.get_json() or {}
            message = (data.get('message') or '').strip()
            if not message:
                return jsonify({'status': 'error', 'message': 'message required'}), 400

            # Determine sender IP
            ip = request.headers.get('X-Forwarded-For', request.remote_addr or '0.0.0.0')
            ip = ip.split(',')[0].strip()

            # Resolve callsign — fallback to Guest-{suffix} (IPv4 and IPv6 safe)
            callsign = (data.get('callsign') or '').strip()
            if not callsign:
                try:
                    addr = ipaddress.ip_address(ip)
                    if addr.version == 6:
                        # Use last 4 hex groups (no colons)
                        parts = ip.split(':')
                        callsign = f'Guest-{parts[-2]}.{parts[-1]}' if len(parts) >= 2 else f'Guest-{ip}'
                    else:
                        parts = ip.split('.')
                        callsign = f'Guest-{parts[-2]}.{parts[-1]}' if len(parts) >= 4 else f'Guest-{ip}'
                except ValueError:
                    callsign = f'Guest-{ip}'

            def _maybe_geo(value):
                try:
                    if value is None or value == '':
                        return None
                    return float(value)
                except (TypeError, ValueError):
                    return None

            msg = {
                'type': 'chat',
                'room': room_id,
                'callsign': callsign,
                'message': message,
                'ip': ip,
                'timestamp': datetime.now(timezone.utc).isoformat(),
            }

            _guest_chat_rooms[room_id].append(msg)
            _guest_chat_broadcast(room_id, msg)

            # Auto-register sender as a recon OPERATOR entity
            try:
                ip_dashed = ip.replace('.', '-').replace(':', '-')
                entity_id = f'OPERATOR-{ip_dashed}'
                browser_location = data.get('location') if isinstance(data.get('location'), dict) else {}
                lat = _maybe_geo(data.get('latitude'))
                lon = _maybe_geo(data.get('longitude'))
                alt_m = _maybe_geo(data.get('altitude_m'))
                accuracy_m = _maybe_geo(data.get('accuracy_m'))
                geo_source = 'browser'

                if lat is None:
                    lat = _maybe_geo(browser_location.get('lat'))
                if lon is None:
                    lon = _maybe_geo(browser_location.get('lon'))
                if alt_m is None:
                    alt_m = _maybe_geo(browser_location.get('altitude_m'))
                if accuracy_m is None:
                    accuracy_m = _maybe_geo(browser_location.get('accuracy_m'))

                if lat is None or lon is None:
                    geo_source = 'ip_api'
                    try:
                        geo_url = f'http://ip-api.com/json/{urllib.parse.quote(ip)}'
                        with urllib.request.urlopen(geo_url, timeout=2) as geo_resp:
                            geo_data = json.loads(geo_resp.read().decode())
                            if geo_data.get('status') == 'success':
                                lat = float(geo_data.get('lat'))
                                lon = float(geo_data.get('lon'))
                    except Exception:
                        pass
                if lat is None or lon is None:
                    geo_source = 'unknown'

                entity_payload = {
                    'entity_id': entity_id,
                    'name': callsign,
                    'type': 'OPERATOR',
                    'ontology': 'operator',
                    'disposition': 'FRIENDLY',
                    'icon': 'friendly_force',
                    'location': (
                        {
                            'lat': lat,
                            'lon': lon,
                            'altitude_m': alt_m if alt_m is not None else 0,
                            'accuracy_m': accuracy_m,
                        }
                        if lat is not None and lon is not None else {}
                    ),
                    'source': 'guest_chat',
                    'meta': {
                        'ip': ip,
                        'callsign': callsign,
                        'room': room_id,
                        'geo_source': geo_source,
                        'accuracy_m': accuracy_m,
                    },
                    'last_seen': datetime.now(timezone.utc).isoformat(),
                }
                if 'recon_system' in globals() and recon_system is not None:
                    recon_system.entities[entity_id] = entity_payload
                    if hasattr(recon_system, '_dirty_entities'):
                        recon_system._dirty_entities.add(entity_id)
            except Exception as _op_err:
                logger.debug(f'guest_chat_send: operator entity upsert skipped: {_op_err}')

            return jsonify({'status': 'ok', 'callsign': callsign, 'timestamp': msg['timestamp']})
        except Exception as e:
            logger.error(f'guest_chat_send error: {e}')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/chat/<room_id>/messages', methods=['GET'])
    def guest_chat_messages(room_id):
        """Poll for messages — no auth required.
        Query params:
          since  — ISO timestamp; return only messages after this time
          limit  — max results (default 50)
        """
        try:
            since_str = request.args.get('since', '')
            limit = int(request.args.get('limit', 50))
            msgs = list(_guest_chat_rooms[room_id])
            if since_str:
                try:
                    # naive compare via string works for ISO timestamps
                    msgs = [m for m in msgs if m.get('timestamp', '') > since_str]
                except Exception:
                    pass
            msgs = msgs[-limit:]
            return jsonify({'status': 'ok', 'room': room_id, 'messages': msgs,
                            'count': len(msgs)})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/rooms/my', methods=['GET'])
    def get_my_rooms():
        """Get rooms the current operator has joined"""
        if not operator_manager:
            return jsonify({'status': 'error', 'message': 'Operator Manager not available'}), 503

        try:
            token = request.headers.get('X-Session-Token')
            if not token:
                return jsonify({'status': 'error', 'message': 'Session token required'}), 401

            session = operator_manager.validate_session(token)
            if not session:
                return jsonify({'status': 'error', 'message': 'Invalid session'}), 401

            # Find rooms this session has joined
            my_rooms = []
            for room_id, members in operator_manager.room_members.items():
                if session.session_id in members:
                    room = operator_manager.get_room(room_id)
                    if room:
                        my_rooms.append({
                            **room.to_dict(),
                            'member_count': len(members),
                            'entity_count': len(operator_manager.room_entities.get(room_id, {}))
                        })

            return jsonify({
                'status': 'ok',
                'count': len(my_rooms),
                'rooms': my_rooms
            })

        except Exception as e:
            logger.error(f"Error getting my rooms: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    # ========================================================================
    # WEBSOCKET EVENT HANDLERS (Flask-SocketIO)
    # ========================================================================

    if SOCKETIO_AVAILABLE and socketio:

        @socketio.on('connect')
        def ws_connect():
            """Handle WebSocket connection.

            Auth modes (in priority order):
            1. Authenticated: operator_manager is running AND a valid token is
               present → full session setup, room join, operator context stored.
            2. Anonymous dev mode: operator_manager is None (server started
               without auth) → allow connection, emit connected with guest status.
               This prevents the globe from being permanently disconnected in
               single-operator / dev deployments.
            3. Token present but invalid: reject immediately so stale tokens
               don't silently get treated as anonymous.
            """
            from flask import session as flask_session

            token = request.args.get('token')

            # ── Dev / no-auth mode ─────────────────────────────────────────
            if not operator_manager:
                if token:
                    # Token supplied but we can't validate it — refuse rather
                    # than silently ignore, so the client knows to clear it.
                    logger.warning("[WebSocket] Token supplied but operator_manager unavailable — rejecting")
                    disconnect()
                    return False
                logger.info("[WebSocket] Anonymous connection accepted (no operator_manager)")
                emit('connected', {'status': 'ok', 'operator': None, 'session_id': None})
                return True

            # ── Authenticated mode ─────────────────────────────────────────
            if not token:
                logger.warning("[WebSocket] No token — rejecting unauthenticated connection")
                disconnect()
                return False

            session = operator_manager.validate_session(token)
            if not session:
                logger.warning("[WebSocket] Invalid/expired token — rejecting")
                disconnect()
                return False

            operator = operator_manager.get_operator(session.operator_id)
            if operator:
                flask_session['session_id'] = session.session_id
                flask_session['operator_id'] = operator.operator_id

                logger.info(f"[WebSocket] Client connected: {operator.callsign}")

                global_room = operator_manager.get_room_by_name("Global")
                if global_room:
                    join_room(global_room.room_id)
                    operator_manager.join_room(global_room.room_id, session.session_id)

                emit('connected', {
                    'status': 'ok',
                    'operator': operator.to_dict(),
                    'session_id': session.session_id
                })
                return True

            disconnect()
            return False

        @socketio.on('disconnect')
        def ws_disconnect():
            """Handle WebSocket disconnection: clean up operator session and edge streaming"""
            from flask import session as flask_session
            session_id = flask_session.get('session_id')
            ws_id = request.sid

            # 1. Clean up Operator Session
            if session_id and operator_manager:
                operator_manager.unregister_ws_client(session_id)
                logger.info(f"[WebSocket] Client disconnected: {session_id} (sid={ws_id})")

            # 2. Clean up Edge Streaming subscriptions
            try:
                # Use late import or helper to avoid circularity/init issues
                from edge_streaming import get_edge_streaming_manager
                mgr = get_edge_streaming_manager()
                if mgr:
                    mgr.on_disconnect(ws_id)
                    logger.debug(f"[EdgeStream] Cleaned up subscriptions for sid={ws_id}")
            except Exception as e:
                logger.warning(f"[EdgeStream] Cleanup error on disconnect: {e}")

        @socketio.on('join_room')
        def ws_join_room(data):
            """Handle room join request via WebSocket"""
            from flask import session as flask_session
            session_id = flask_session.get('session_id')

            if not session_id or not operator_manager:
                emit('error', {'message': 'Not authenticated'})
                return

            room_id = data.get('room_id')
            password = data.get('password')

            success, message = operator_manager.join_room(room_id, session_id, password)

            if success:
                join_room(room_id)  # SocketIO room
                room = operator_manager.get_room(room_id)
                emit('room_joined', {
                    'status': 'ok',
                    'room': room.to_dict() if room else None,
                    'message': message
                })
            else:
                emit('error', {'message': message})

        @socketio.on('leave_room')
        def ws_leave_room(data):
            """Handle room leave request via WebSocket"""
            from flask import session as flask_session
            session_id = flask_session.get('session_id')

            if not session_id or not operator_manager:
                emit('error', {'message': 'Not authenticated'})
                return

            room_id = data.get('room_id')
            success, message = operator_manager.leave_room(room_id, session_id)

            if success:
                leave_room(room_id)  # SocketIO room
                emit('room_left', {'status': 'ok', 'room_id': room_id, 'message': message})
            else:
                emit('error', {'message': message})

        @socketio.on('create_room')
        def ws_create_room(data):
            """Handle room creation via WebSocket"""
            from flask import session as flask_session
            session_id = flask_session.get('session_id')
            operator_id = flask_session.get('operator_id')

            if not session_id or not operator_manager:
                emit('error', {'message': 'Not authenticated'})
                return

            room = operator_manager.create_room(
                room_name=data.get('room_name'),
                room_type=data.get('room_type', 'custom'),
                created_by=operator_id,
                capacity=data.get('capacity', 50),
                is_private=data.get('is_private', False),
                password=data.get('password'),
                metadata=data.get('metadata')
            )

            if room:
                # Auto-join creator
                operator_manager.join_room(room.room_id, session_id)
                join_room(room.room_id)
                emit('room_created', {'status': 'ok', 'room': room.to_dict()})
            else:
                emit('error', {'message': 'Failed to create room'})

        @socketio.on('list_rooms')
        def ws_list_rooms(data=None):
            """List available rooms via WebSocket"""
            if not operator_manager:
                emit('error', {'message': 'Not available'})
                return

            data = data or {}
            rooms = operator_manager.list_rooms(include_private=data.get('include_private', False))
            emit('rooms_list', {'status': 'ok', 'rooms': rooms})

        @socketio.on('publish_entity')
        def ws_publish_entity(data):
            """Publish entity to room via WebSocket"""
            from flask import session as flask_session
            session_id = flask_session.get('session_id')
            operator_id = flask_session.get('operator_id')

            if not session_id or not operator_manager:
                emit('error', {'message': 'Not authenticated'})
                return

            room_id = data.get('room_id')
            entity_id = data.get('entity_id')
            entity_type = data.get('entity_type', 'entity')
            entity_data = data.get('entity_data', {})

            operator = operator_manager.get_operator(operator_id)

            if not entity_id:
                emit('error', {'message': 'entity_id required'})
                return

            try:
                import writebus
                from writebus import WriteContext

                room = operator_manager.get_room(room_id) if room_id else operator_manager.get_room_by_name("Global")
                if not room:
                    emit('entity_published', {'status': 'error', 'entity_id': entity_id, 'message': 'Room not found'})
                    return

                result = writebus.bus().commit(
                    entity_id=entity_id,
                    entity_type=entity_type,
                    entity_data=entity_data,
                    graph_ops=[],
                    ctx=WriteContext(
                        room_name=getattr(room, 'room_name', 'Global'),
                        operator=operator,
                        operator_id=operator_id or getattr(operator, 'operator_id', None) or "SYSTEM:ROOM_WS",
                        source="room_publish_ws",
                    ),
                    persist=True,
                    audit=True,
                    idempotency_key=data.get("idempotency_key"),
                    room_id_override=getattr(room, 'room_id', room_id),
                )
                emit(
                    'entity_published',
                    {
                        'status': 'ok' if result.ok else 'error',
                        'entity_id': entity_id,
                        'write_result': {
                            'commit_status': result.commit_status,
                            'errors': result.errors,
                            'debug': result.debug,
                        }
                    }
                )
            except Exception as e:
                logger.error(f"WebSocket WriteBus publish failed: {e}")
                emit('entity_published', {'status': 'error', 'entity_id': entity_id, 'message': str(e)})

        @socketio.on('send_message')
        def ws_send_message(data):
            """Send message to room via WebSocket"""
            from flask import session as flask_session
            session_id = flask_session.get('session_id')
            operator_id = flask_session.get('operator_id')

            if not session_id or not operator_manager:
                emit('error', {'message': 'Not authenticated'})
                return

            room_id = data.get('room_id')
            message = data.get('message', '')
            message_type = data.get('message_type', 'chat')

            operator = operator_manager.get_operator(operator_id)

            if operator and room_id:
                success = operator_manager.send_message_to_room(room_id, message, operator, message_type)
                if success:
                    emit('message_sent', {'status': 'ok'})
                else:
                    emit('error', {'message': 'Failed to send message'})
            else:
                emit('error', {'message': 'Invalid operator or room'})

        @socketio.on('heartbeat')
        def ws_heartbeat(data=None):
            """Handle heartbeat via WebSocket"""
            from flask import session as flask_session
            session_id = flask_session.get('session_id')

            if session_id and operator_manager:
                session = operator_manager.sessions.get(session_id)
                if session:
                    data = data or {}
                    operator_manager.heartbeat(session.session_token, data.get('current_view'))
                    emit('heartbeat_ack', {'status': 'ok', 'timestamp': time.time()})

    # ========================================================================
    # EDGE STREAMING - WebSocket subscription-based edge delivery
    # ========================================================================

    if SOCKETIO_AVAILABLE and socketio:
        from edge_streaming import initialize_edge_streaming, get_edge_streaming_manager

        # Initialize edge streaming manager (lazy, on first connect)
        _edge_streaming_initialized = False

        @socketio.on('subscribe_edges')
        def ws_subscribe_edges(scope_data):
            """Subscribe to edges matching a scope (cluster, node, etc.)

            Request format:
              {
                "scope": {
                  "type": "cluster",
                  "id": 7,
                  "min_weight": 0.15,
                  "since_secs": 300
                }
              }

            Response:
              {
                "op": "subscribed",
                "scope_id": "scope-abc123"
              }
            """
            global _edge_streaming_initialized
            from flask import session as flask_session

            try:
                # Initialize edge streaming on first call
                if not _edge_streaming_initialized:
                    def _get_engine():
                        try:
                            return _get_engine_internal()
                        except:
                            return None

                    initialize_edge_streaming(_get_engine)
                    _edge_streaming_initialized = True

                mgr = get_edge_streaming_manager()
                if not mgr:
                    emit('error', {'message': 'Edge streaming not available'})
                    return

                ws_id = request.sid
                scope = scope_data.get('scope', {})

                if not scope:
                    emit('error', {'message': 'scope required'})
                    return

                scope_id = mgr.register_subscription(ws_id, scope)

                # Replay missed events if client passed a reconnect timestamp
                since_ts = float(scope_data.get('since', 0) or 0)
                if since_ts > 0:
                    replay_edges = mgr.get_replay_since(since_ts)
                    if replay_edges:
                        emit('edges_replay', {
                            'op': 'edges_replay',
                            'scope_id': scope_id,
                            'edges': replay_edges,
                            'count': len(replay_edges),
                            'since': since_ts,
                        })
                        logger.info(f"[EdgeStream] Replayed {len(replay_edges)} edges to {ws_id} since {since_ts}")

                emit('subscribed', {
                    'op': 'subscribed',
                    'scope_id': scope_id,
                    'scope': scope,
                })
                logger.info(f"[EdgeStream] Client {ws_id} subscribed with scope_id {scope_id}")

            except Exception as e:
                logger.error(f"[EdgeStream] subscribe_edges failed: {e}")
                emit('error', {'message': str(e)})

        @socketio.on('unsubscribe_edges')
        def ws_unsubscribe_edges(data):
            """Unsubscribe from edge streaming

            Request format:
              { "scope_id": "scope-abc123" }
            """
            try:
                mgr = get_edge_streaming_manager()
                if not mgr:
                    emit('error', {'message': 'Edge streaming not available'})
                    return

                ws_id = request.sid
                scope_id = data.get('scope_id')

                if not scope_id:
                    emit('error', {'message': 'scope_id required'})
                    return

                success = mgr.unregister_subscription(ws_id, scope_id)

                emit('unsubscribed', {
                    'op': 'unsubscribed',
                    'scope_id': scope_id,
                    'success': success,
                })
                logger.info(f"[EdgeStream] Client {ws_id} unsubscribed from {scope_id}")

            except Exception as e:
                logger.error(f"[EdgeStream] unsubscribe_edges failed: {e}")
                emit('error', {'message': str(e)})

        @socketio.on('scrub_edges')
        def ws_scrub_edges(data):
            """Adjust evaluation time for an existing subscription.

            Request format:
              { "scope_id": "scope-abc123", "timestamp": 1709251912 }
            Response:
              { "op": "scrubbed", "scope_id": "...", "timestamp": 1709251912 }
            """
            try:
                mgr = get_edge_streaming_manager()
                if not mgr:
                    emit('error', {'message': 'Edge streaming not available'})
                    return
                ws_id = request.sid
                scope_id = data.get('scope_id')
                ts = data.get('timestamp')
                if not scope_id or ts is None:
                    emit('error', {'message': 'scope_id and timestamp required'})
                    return
                mgr.scrub_subscription(ws_id, scope_id, float(ts))
                emit('scrubbed', {'op':'scrubbed','scope_id':scope_id,'timestamp':ts})
                logger.info(f"[EdgeStream] Client {ws_id} scrubbed {scope_id} to {ts}")
            except Exception as e:
                logger.error(f"[EdgeStream] scrub_edges failed: {e}")
                emit('error', {'message': str(e)})



    # ========================================================================
    # BACKGROUND TASK - Edge streaming tick loop
    # ========================================================================

    if SOCKETIO_AVAILABLE and socketio:
        def _edge_streaming_loop():
            """Periodic eventlet greenthread that streams edges to subscribed clients.

            Uses eventlet.sleep (not asyncio.sleep) so it cooperates correctly
            with the eventlet hub.  Calls the synchronous stream_edges_tick_sync
            variant so no coroutine is ever created here.
            """
            import eventlet
            while True:
                try:
                    eventlet.sleep(1)  # yield to eventlet hub for 1-second tick
                    mgr = get_edge_streaming_manager()
                    if mgr:
                        def _send_to_client(ws_id: str, msg: str) -> None:
                            try:
                                socketio.emit('edges', json.loads(msg), room=ws_id)
                            except Exception as exc:
                                exc_str = str(exc)
                                # Stale SID — clean up subscription so we stop trying
                                if 'disconnected' in exc_str.lower() or 'not connected' in exc_str.lower():
                                    try:
                                        mgr.on_disconnect(ws_id)
                                        logger.debug(f"[EdgeStream] Cleaned stale SID {ws_id} after disconnect")
                                    except Exception:
                                        pass
                                else:
                                    logger.warning(f"[EdgeStream] Failed to send to {ws_id}: {exc}")

                        mgr.stream_edges_tick_sync(_send_to_client)
                except Exception as e:
                    logger.error(f"[EdgeStream] Loop error: {e}")

        # Start the edge streaming loop when using eventlet
            try:
                socketio.start_background_task(_edge_streaming_loop)
                logger.info("[EdgeStream] Background loop started")
            except Exception as e:
                logger.warning(f"[EdgeStream] Could not start background loop: {e}")

    # ========================================================================
    # API ROUTES - REVENGE ECOSYSTEM HYPERGRAPH
    # ========================================================================

    # Initialize Revenge Ecosystem Engine
    revenge_ecosystem = None
    try:
        from revenge_ecosystem_hypergraph import (
            RevengeEcosystemEngine, EcosystemNode, EcosystemHyperedge,
            EcosystemEvent, OrganMask, ActorKind, InfrastructureKind,
            ArtifactKind, HyperedgeKind, EcosystemEventType, export_shader_uniforms
        )
        revenge_ecosystem = RevengeEcosystemEngine(
            hypergraph_engine=hypergraph_engine if 'hypergraph_engine' in dir() else None
        )
        logger.info("Revenge Ecosystem Engine initialized")
    except ImportError as e:
        logger.warning(f"Revenge Ecosystem module not available: {e}")
    except Exception as e:
        logger.error(f"Failed to initialize Revenge Ecosystem: {e}")

    @app.route('/api/ecosystem/nodes', methods=['GET'])
    def get_ecosystem_nodes():
        """Get all ecosystem nodes"""
        if not revenge_ecosystem:
            return jsonify({'status': 'error', 'message': 'Ecosystem not available'}), 503
        try:
            nodes = [node.to_dict() for node in revenge_ecosystem.nodes.values()]
            return jsonify({'status': 'ok', 'nodes': nodes, 'count': len(nodes)})
        except Exception as e:
            logger.error(f"Error getting ecosystem nodes: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/ecosystem/nodes/<node_id>', methods=['GET'])
    def get_ecosystem_node(node_id):
        """Get a specific ecosystem node"""
        if not revenge_ecosystem:
            return jsonify({'status': 'error', 'message': 'Ecosystem not available'}), 503
        try:
            node = revenge_ecosystem.get_node(node_id)
            if node:
                return jsonify({'status': 'ok', 'node': node.to_dict()})
            return jsonify({'status': 'error', 'message': 'Node not found'}), 404
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/ecosystem/edges', methods=['GET'])
    def get_ecosystem_edges():
        """Get all ecosystem hyperedges"""
        if not revenge_ecosystem:
            return jsonify({'status': 'error', 'message': 'Ecosystem not available'}), 503
        try:
            edges = [edge.to_dict() for edge in revenge_ecosystem.edges.values()]
            return jsonify({'status': 'ok', 'edges': edges, 'count': len(edges)})
        except Exception as e:
            logger.error(f"Error getting ecosystem edges: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/ecosystem/organ-state', methods=['GET'])
    def get_ecosystem_organ_state():
        """Get current organ state (intensities)"""
        if not revenge_ecosystem:
            return jsonify({'status': 'error', 'message': 'Ecosystem not available'}), 503
        try:
            return jsonify({
                'status': 'ok',
                **revenge_ecosystem.organ_state.to_dict()
            })
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/ecosystem/metrics', methods=['GET'])
    def get_ecosystem_metrics():
        """Get ecosystem metrics"""
        if not revenge_ecosystem:
            return jsonify({'status': 'error', 'message': 'Ecosystem not available'}), 503
        try:
            metrics = revenge_ecosystem.get_metrics()
            return jsonify({'status': 'ok', 'metrics': metrics})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/ecosystem/shader-uniforms', methods=['GET'])
    def get_ecosystem_shader_uniforms():
        """Get shader uniforms for GPU rendering"""
        if not revenge_ecosystem:
            return jsonify({'status': 'error', 'message': 'Ecosystem not available'}), 503
        try:
            uniforms = export_shader_uniforms(revenge_ecosystem)
            return jsonify({'status': 'ok', 'uniforms': uniforms})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/ecosystem/generate-scenario', methods=['POST'])
    def generate_ecosystem_scenario():
        """Generate a test scenario"""
        if not revenge_ecosystem:
            return jsonify({'status': 'error', 'message': 'Ecosystem not available'}), 503
        try:
            data = request.get_json() or {}
            scenario_type = data.get('scenario_type', 'harassment_campaign')
            result = revenge_ecosystem.generate_scenario(scenario_type)
            return jsonify({'status': 'ok', 'scenario': result})
        except Exception as e:
            logger.error(f"Error generating scenario: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/ecosystem/process-event', methods=['POST'])
    def process_ecosystem_event():
        """Process an ecosystem event"""
        if not revenge_ecosystem:
            return jsonify({'status': 'error', 'message': 'Ecosystem not available'}), 503
        try:
            data = request.get_json() or {}
            event = EcosystemEvent(
                id=data.get('id', f"event_{int(time.time()*1000)}"),
                event_type=data.get('event_type', 'CommissionCreated'),
                node_ids=data.get('node_ids', []),
                edge_ids=data.get('edge_ids', []),
                intensity=data.get('intensity', 0.5),
                budget=data.get('budget', 0),
                organ_mask=data.get('organ_mask', 0)
            )
            revenge_ecosystem.process_event(event)
            return jsonify({'status': 'ok', 'event_id': event.id})
        except Exception as e:
            logger.error(f"Error processing event: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/ecosystem/tick', methods=['POST'])
    def tick_ecosystem():
        """Advance ecosystem simulation by one tick"""
        if not revenge_ecosystem:
            return jsonify({'status': 'error', 'message': 'Ecosystem not available'}), 503
        try:
            data = request.get_json() or {}
            delta_time = data.get('delta_time', None)
            revenge_ecosystem.tick(delta_time)
            return jsonify({
                'status': 'ok',
                'organ_state': revenge_ecosystem.organ_state.to_dict()
            })
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/ecosystem/attack-surface/<victim_id>', methods=['GET'])
    def get_attack_surface(victim_id):
        """Get attack surface for a victim"""
        if not revenge_ecosystem:
            return jsonify({'status': 'error', 'message': 'Ecosystem not available'}), 503
        try:
            surface = revenge_ecosystem.get_attack_surface(victim_id)
            if surface:
                return jsonify({'status': 'ok', 'attack_surface': surface})
            return jsonify({'status': 'error', 'message': 'Victim not found'}), 404
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/ecosystem/obfuscation-layers/<operator_id>', methods=['GET'])
    def get_obfuscation_layers(operator_id):
        """Trace obfuscation layers from an operator"""
        if not revenge_ecosystem:
            return jsonify({'status': 'error', 'message': 'Ecosystem not available'}), 503
        try:
            layers = revenge_ecosystem.trace_obfuscation_layers(operator_id)
            return jsonify({'status': 'ok', 'layers': layers, 'depth': len(layers)})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/ecosystem/organ/<organ_name>/nodes', methods=['GET'])
    def get_organ_nodes(organ_name):
        """Get all nodes in a specific organ"""
        if not revenge_ecosystem:
            return jsonify({'status': 'error', 'message': 'Ecosystem not available'}), 503
        try:
            organ_map = {
                'harassment': OrganMask.HARASSMENT,
                'doxxing': OrganMask.DOXXING,
                'reputation': OrganMask.REPUTATION,
                'obfuscation': OrganMask.OBFUSCATION,
                'escalation': OrganMask.ESCALATION
            }
            organ = organ_map.get(organ_name.lower())
            if not organ:
                return jsonify({'status': 'error', 'message': 'Unknown organ'}), 400
            nodes = [n.to_dict() for n in revenge_ecosystem.get_organ_nodes(organ)]
            return jsonify({'status': 'ok', 'organ': organ_name, 'nodes': nodes, 'count': len(nodes)})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

    # ========================================================================
    # API ROUTES - CYBER SWARM / CLUSTER VISUALIZATION
    # Collapses hypergraph nodes into geo-clustered "swarm objects" for ATAK
    # ========================================================================

    @app.route('/api/clusters/swarms', methods=['GET'])
    def api_clusters_swarms():
        """
        GET /api/clusters/swarms

        Collapses live hypergraph nodes into geo-bucketed CyberCluster swarm
        objects. Each cluster carries centroid, node_count, threat_score,
        rf_emitters, uav_count, behavior_type, and optional ASN label.

        Query params:
          min_size:       int  (default 2)  — minimum nodes per cluster
          geo_bucket_deg: float (default 1.0) — geo grid cell size in degrees
          format:         "json" | "cot" (default: json)
              json → { status, clusters: [...] }
              cot  → { status, count, events: [<CoT XML>, ...] }
        """
        try:
            from cluster_swarm_engine import detect_clusters, clusters_to_cot_list

            min_size  = int(request.args.get('min_size', 2))
            geo_deg   = float(request.args.get('geo_bucket_deg', 1.0))
            fmt       = request.args.get('format', 'json')

            snap  = _get_engine_snapshot()
            nodes = snap.get('nodes', [])
            edges = snap.get('edges', [])

            clusters = detect_clusters(nodes, edges,
                                       geo_bucket_deg=geo_deg,
                                       min_size=min_size)

            # Auto-log swarm events to the battlefield ledger (fire-and-forget)
            try:
                for c in clusters:
                    _auto_log_swarm(c.to_dict(), 'swarm.create')
            except Exception:
                pass

            if fmt == 'cot':
                cot_list = clusters_to_cot_list(clusters)
                return jsonify({
                    'status': 'ok',
                    'count':  len(cot_list),
                    'events': [c.decode('utf-8') for c in cot_list],
                })

            return jsonify({
                'status':   'ok',
                'count':    len(clusters),
                'clusters': [c.to_dict() for c in clusters],
            })
        except Exception as e:
            logger.error(f'[Swarm] cluster detection failed: {e}')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/clusters/intel', methods=['GET'])
    def api_clusters_intel():
        """
        GET /api/clusters/intel

        Full intelligence narration for all detected clusters.
        Includes temporal pattern analysis, behavioral classification,
        mobility assessment, and recommended actions.

        Query params:
          min_size:       int   (default 2)
          geo_bucket_deg: float (default 1.0)
          window_sec:     float (default 60.0) — temporal analysis window

        Response: { status, count, clusters: [{...intel narration...}] }
        """
        try:
            from cluster_swarm_engine import intel_snapshot

            min_size   = int(request.args.get('min_size', 2))
            geo_deg    = float(request.args.get('geo_bucket_deg', 1.0))

            snap  = _get_engine_snapshot()
            nodes = snap.get('nodes', [])
            edges = snap.get('edges', [])

            intel = intel_snapshot(nodes, edges,
                                   geo_bucket_deg=geo_deg,
                                   min_size=min_size)

            return jsonify({
                'status':   'ok',
                'count':    len(intel),
                'clusters': intel,
            })
        except Exception as e:
            logger.error(f'[ClusterIntel] intel snapshot failed: {e}')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/clusters/decompose/<cluster_id>', methods=['GET'])
    def api_clusters_decompose(cluster_id: str):
        """
        GET /api/clusters/decompose/<cluster_id>

        Latent Swarm Autopsy — deep decomposition of a single cluster.
        Reads from the cluster cache populated by the last /api/clusters/intel call;
        does NOT re-run detect_clusters() to avoid inflating the event history.

        Returns 404 if the cluster is not in the cache (call /api/clusters/intel first).

        Response: { status, decomposition: { dimensional_density, asn_breakdown,
            behavior_fingerprint, temporal_ghost_events, subclusters, intent_scores,
            activation_cascade, silence_pressure, archetype, node_tier } }
        """
        try:
            from cluster_swarm_engine import _cluster_cache, decompose_cluster, narrate_cluster

            cluster = _cluster_cache.get(cluster_id)
            if cluster is None:
                return jsonify({
                    'status': 'not_found',
                    'message': 'Cluster not in cache. Call /api/clusters/intel first to populate the cache.',
                }), 404

            narration = narrate_cluster(cluster)
            result    = decompose_cluster(cluster, narration)

            return jsonify({'status': 'ok', 'decomposition': result})
        except Exception as e:
            logger.error(f'[ClusterDecompose] decompose failed for {cluster_id}: {e}')
            return jsonify({'status': 'error', 'message': str(e)}), 500


    @app.route('/api/clusters/intel/stream', methods=['GET'])
    def api_clusters_intel_stream():
        """
        GET /api/clusters/intel/stream

        SSE stream that pushes intel narration snapshots every ``interval`` seconds.

        SSE frame format:
            event: CLUSTER_INTEL
            data:  { count: N, clusters: [...intel narration...] }

        Query params:
          interval:       float (default 5.0, min 2.0)
          min_size:       int   (default 2)
          geo_bucket_deg: float (default 1.0)
        """
        from cluster_swarm_engine import intel_snapshot
        import json as _json

        interval = max(2.0, float(request.args.get('interval', 5.0)))
        min_size = int(request.args.get('min_size', 2))
        geo_deg  = float(request.args.get('geo_bucket_deg', 1.0))

        def _generate():
            while True:
                try:
                    snap  = _get_engine_snapshot()
                    nodes = snap.get('nodes', [])
                    edges = snap.get('edges', [])

                    intel = intel_snapshot(nodes, edges,
                                           geo_bucket_deg=geo_deg,
                                           min_size=min_size)

                    payload = _json.dumps({
                        'count':    len(intel),
                        'clusters': intel,
                        'ts':       time.time(),
                    })
                    yield f"event: CLUSTER_INTEL\ndata: {payload}\n\n"
                except Exception as exc:
                    logger.debug(f'[ClusterIntelStream] {exc}')
                    yield f"event: error\ndata: {str(exc)}\n\n"

                time.sleep(interval)

        return Response(
            _generate(),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no',
                'Connection': 'keep-alive',
            }
        )

    @app.route('/api/infrastructure/flow', methods=['GET'])
    def api_infrastructure_flow():
        """
        GET /api/infrastructure/flow

        Infrastructure flow analysis: ASN transit paths between clusters,
        submarine cable alignment, IX chokepoints, synthetic routing detection.

        Query params:
          min_size:       int   (default 2)
          geo_bucket_deg: float (default 1.0)

        Response: { status, paths, cables, ix_points, summary }
        """
        try:
            from cluster_swarm_engine import intel_snapshot, infrastructure_flow_snapshot

            min_size = int(request.args.get('min_size', 2))
            geo_deg  = float(request.args.get('geo_bucket_deg', 1.0))

            snap  = _get_engine_snapshot()
            nodes = snap.get('nodes', [])
            edges = snap.get('edges', [])

            intel = intel_snapshot(nodes, edges,
                                   geo_bucket_deg=geo_deg,
                                   min_size=min_size)

            flow = infrastructure_flow_snapshot(intel)
            flow['status'] = 'ok'
            return jsonify(flow)
        except Exception as e:
            logger.error(f'[InfraFlow] infrastructure flow failed: {e}')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/infrastructure/cables', methods=['GET'])
    def api_infrastructure_cables():
        """GET /api/infrastructure/cables — submarine cable + IX data."""
        from cluster_swarm_engine import SUBMARINE_CABLES, IX_POINTS
        return jsonify({
            'status': 'ok',
            'cables': SUBMARINE_CABLES,
            'ix_points': IX_POINTS,
        })

    @app.route('/api/infrastructure/ix/heatmap', methods=['GET'])
    def api_ix_heatmap():
        """
        GET /api/infrastructure/ix/heatmap

        Real-time IX heatmap + peering conflict detection.
        Returns heat scores for all IX nodes, detected conflicts,
        temporal pressure trends, and global metrics.
        """
        try:
            from cluster_swarm_engine import (
                intel_snapshot, ix_heatmap_snapshot,
                compute_inter_cluster_paths
            )

            min_size = int(request.args.get('min_size', 2))
            geo_deg  = float(request.args.get('geo_bucket_deg', 1.0))

            snap  = _get_engine_snapshot()
            nodes = snap.get('nodes', [])
            edges = snap.get('edges', [])

            intel = intel_snapshot(nodes, edges,
                                   geo_bucket_deg=geo_deg,
                                   min_size=min_size)

            paths = compute_inter_cluster_paths(intel)
            result = ix_heatmap_snapshot(intel, paths)
            result['status'] = 'ok'
            return jsonify(result)
        except Exception as e:
            logger.error(f'[IXHeatmap] IX heatmap failed: {e}')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/infrastructure/phantom-ix', methods=['GET'])
    def api_infrastructure_phantom_ix():
        """
        GET /api/infrastructure/phantom-ix

        Phantom IX Detection Engine + Cyber-Physical Kill Chain Graph.

        Detects convergence attractors with no physical anchor (no known IX,
        no cable alignment, inconsistent latency geometry) and correlates them
        with RF emitters, UAV activity, and synthetic routing.

        Returns phantom nodes, kill chain correlations, and summary metrics.
        """
        try:
            from cluster_swarm_engine import (
                intel_snapshot, compute_inter_cluster_paths,
                phantom_ix_snapshot,
            )
            min_size = int(request.args.get('min_size', 2))
            geo_deg  = float(request.args.get('geo_bucket_deg', 1.0))

            snap  = _get_engine_snapshot()
            nodes = snap.get('nodes', [])
            edges = snap.get('edges', [])

            intel  = intel_snapshot(nodes, edges,
                                    geo_bucket_deg=geo_deg,
                                    min_size=min_size)
            paths  = compute_inter_cluster_paths(intel)
            result = phantom_ix_snapshot(intel, paths)
            result['status'] = 'ok'
            return jsonify(result)
        except Exception as e:
            logger.error(f'[PhantomIX] Detection failed: {e}')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/infrastructure/peering-intent', methods=['GET'])
    def api_peering_intent():
        """Classify IX routing behavior as strategic intents."""
        from cluster_swarm_engine import infer_peering_intent
        try:
            from cluster_swarm_engine import get_ix_heatmap_data, get_path_data
            ix_heats = get_ix_heatmap_data() if callable(globals().get('get_ix_heatmap_data')) else []
            paths    = get_path_data()       if callable(globals().get('get_path_data'))    else []
        except Exception:
            ix_heats, paths = [], []
        intents = infer_peering_intent(ix_heats, paths, [])
        return jsonify({'intents': intents, 'count': len(intents), 'ts': time.time()})

    @app.route('/api/infrastructure/emergent-kill-chain', methods=['GET'])
    def api_emergent_kill_chain():
        """Detect pre-kill-chain emergence: partial domain alignment with rising slope."""
        from cluster_swarm_engine import detect_emergent_kill_chain, detect_phantom_ix
        try:
            from cluster_swarm_engine import get_cluster_intel
            clusters = get_cluster_intel() if callable(globals().get('get_cluster_intel')) else []
        except Exception:
            clusters = []
        phantoms = detect_phantom_ix(clusters)
        emergent = detect_emergent_kill_chain(clusters, phantoms, [])
        return jsonify({'emergent': emergent, 'count': len(emergent), 'ts': time.time()})

    @app.route('/api/infrastructure/reality-divergence', methods=['GET'])
    def api_reality_divergence():
        """Compute physical vs fabric graph divergence field."""
        from cluster_swarm_engine import compute_dual_reality_divergence
        try:
            from cluster_swarm_engine import get_path_data
            paths = get_path_data() if callable(globals().get('get_path_data')) else []
        except Exception:
            paths = []
        divergence = compute_dual_reality_divergence(paths)
        return jsonify(divergence)

    @app.route('/api/infrastructure/ix-conflict-replay', methods=['GET'])
    def api_ix_conflict_replay():
        """
        GET /api/infrastructure/ix-conflict-replay
        Returns per-IX heat time-series for the conflict replay scrubber.
        Query params: window (float, default 3600s), max_ix (int, default 20).
        """
        from cluster_swarm_engine import get_ix_conflict_replay
        window  = float(request.args.get('window',  3600))
        max_ix  = int(request.args.get('max_ix',   20))
        try:
            data = get_ix_conflict_replay(window_sec=window, max_ix=max_ix)
            return jsonify({'status': 'ok', **data})
        except Exception as e:
            logger.exception('ix-conflict-replay error')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/signals/timing', methods=['GET'])
    def api_signals_timing():
        """
        GET /api/signals/timing
        Returns per-cluster phase-coherence + energy sparklines for the
        Signal Timing panel.
        Query params: window (float, default 120s), max_clusters (int, default 15).
        """
        from cluster_swarm_engine import get_signal_timing_snapshot
        window       = float(request.args.get('window',       120))
        max_clusters = int(request.args.get('max_clusters',   15))
        try:
            data = get_signal_timing_snapshot(window_sec=window,
                                              max_clusters=max_clusters)
            return jsonify({'status': 'ok', **data})
        except Exception as e:
            logger.exception('signals/timing error')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/killchain/slope', methods=['GET'])
    def api_killchain_slope():
        """
        GET /api/killchain/slope
        Returns per-cluster KC escalation slope + stage classification.
        Query param: steps (int, default 5) — KC history window depth.
        """
        from cluster_swarm_engine import get_killchain_slope
        steps = int(request.args.get('steps', 5))
        try:
            data = get_killchain_slope(window_steps=max(3, min(10, steps)))
            return jsonify({'status': 'ok', **data})
        except Exception as e:
            logger.exception('killchain/slope error')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/signals/fingerprint-drift', methods=['GET'])
    def api_fingerprint_drift():
        """
        GET /api/signals/fingerprint-drift
        Returns per-cluster temporal RF fingerprint drift analysis.
        Query param: window (float, default 120s).
        """
        from cluster_swarm_engine import get_fingerprint_drift_snapshot
        window = float(request.args.get('window', 120))
        try:
            data = get_fingerprint_drift_snapshot(window_sec=window)
            return jsonify({'status': 'ok', **data})
        except Exception as e:
            logger.exception('fingerprint-drift error')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/intent/field', methods=['GET'])
    def api_intent_field():
        """
        GET /api/intent/field
        Returns per-cluster intent scores with lat/lon for globe field rendering.
        Query param: window (float, default 120s), max (int, default 50).
        """
        from cluster_swarm_engine import get_intent_field_snapshot
        window = float(request.args.get('window', 120))
        max_c  = int(request.args.get('max', 50))
        try:
            data = get_intent_field_snapshot(window_sec=window, max_clusters=max_c)
            return jsonify({'status': 'ok', **data})
        except Exception as e:
            logger.exception('intent/field error')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/infrastructure/ix-conflict-replay/trace', methods=['GET'])
    def api_replay_trace():
        """
        GET /api/infrastructure/ix-conflict-replay/trace?ix=NAME&t=TIMESTAMP
        Returns events contributing to a dd_heat spike at time t for IX ix.
        Used by the causal backtracking replay click handler.
        """
        from cluster_swarm_engine import get_ix_conflict_replay, _ix_pressure_history
        ix_name = request.args.get('ix', '')
        try:
            t_cursor = float(request.args.get('t', 0))
        except (ValueError, TypeError):
            return jsonify({'status': 'error', 'message': 'invalid t parameter'}), 400
        look_back = float(request.args.get('window', 30))

        buf = _ix_pressure_history.get(ix_name, [])
        nearby = [e for e in buf if abs(e['ts'] - t_cursor) <= look_back]
        if not nearby:
            return jsonify({'status': 'ok', 'ix': ix_name, 't': t_cursor,
                            'events': [], 'message': 'no events in window'})

        # Compute d_heat for each nearby event relative to prior
        events_sorted = sorted(nearby, key=lambda e: e['ts'])
        annotated = []
        for i, ev in enumerate(events_sorted):
            d_heat = (ev['heat'] - events_sorted[i-1]['heat']) if i > 0 else 0.0
            annotated.append({
                'ts':             round(ev['ts'], 3),
                'heat':           round(ev['heat'], 4),
                'd_heat':         round(d_heat, 4),
                'tier':           ev.get('tier', 'UNKNOWN'),
                'phase_inversion': ev.get('phase_inversion', False),
                'asymmetry':      round(ev.get('asymmetry', 0.0), 3),
                'synthetic':      ev.get('phase_inversion', False),
            })
        # Sort by |d_heat| to surface highest-impact events first
        annotated.sort(key=lambda e: abs(e['d_heat']), reverse=True)

        return jsonify({
            'status':      'ok',
            'ix':          ix_name,
            't_cursor':    t_cursor,
            'look_back_s': look_back,
            'events':      annotated[:20],
        })

    @app.route('/api/ping', methods=['GET'])
    def api_ping():
        """
        GET /api/ping?target=<host_or_url>&timeout=<seconds>

        HTTP-reachability probe for a node IP or URL.
        Uses a HEAD request so the body is never downloaded.
        Returns { success, status_code?, latency_ms, error? }.

        SSRF guard: private / link-local / loopback ranges are blocked.
        """
        import time
        import ipaddress
        import socket
        import urllib.request
        import urllib.error
        import urllib.parse

        # ── SSRF allowlist — private ranges that must never be probed ────────
        _BLOCKED_NETS = [
            ipaddress.ip_network('10.0.0.0/8'),
            ipaddress.ip_network('172.16.0.0/12'),
            ipaddress.ip_network('192.168.0.0/16'),
            ipaddress.ip_network('169.254.0.0/16'),   # link-local / AWS metadata
            ipaddress.ip_network('100.64.0.0/10'),    # carrier-grade NAT
            ipaddress.ip_network('127.0.0.0/8'),
            ipaddress.ip_network('::1/128'),
            ipaddress.ip_network('fc00::/7'),          # ULA
            ipaddress.ip_network('fe80::/10'),         # IPv6 link-local
        ]

        def _is_private(host: str) -> bool:
            """Resolve host and check all returned IPs against blocked ranges."""
            try:
                infos = socket.getaddrinfo(host, None, 0, socket.SOCK_STREAM)
                for *_, sockaddr in infos:
                    ip_str = sockaddr[0]
                    try:
                        ip = ipaddress.ip_address(ip_str)
                        if any(ip in net for net in _BLOCKED_NETS):
                            return True
                    except ValueError:
                        pass
            except socket.gaierror:
                pass  # unresolvable — let urlopen fail normally
            return False

        target  = (request.args.get('target') or '').strip()
        if not target:
            return jsonify({'error': 'target parameter required'}), 400

        try:
            timeout = min(float(request.args.get('timeout', 3.0)), 10.0)
        except (ValueError, TypeError):
            timeout = 3.0

        url = target if target.startswith(('http://', 'https://')) else f'http://{target}'

        # Block private / internal targets before making any network request
        try:
            parsed_host = urllib.parse.urlparse(url).hostname or ''
        except Exception:
            parsed_host = ''
        if not parsed_host or _is_private(parsed_host):
            return jsonify({'success': False, 'error': 'Target resolves to a private or reserved address', 'url': url}), 403

        t0  = time.time()
        try:
            req = urllib.request.Request(url, method='HEAD')
            req.add_header('User-Agent', 'ScytheProbe/1.0')
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                latency = int((time.time() - t0) * 1000)
                return jsonify({'success': True, 'status_code': resp.status, 'latency_ms': latency, 'url': url})
        except urllib.error.HTTPError as e:
            latency = int((time.time() - t0) * 1000)
            return jsonify({'success': True, 'status_code': e.code, 'latency_ms': latency, 'url': url})
        except Exception as e:
            latency = int((time.time() - t0) * 1000)
            return jsonify({'success': False, 'error': str(e)[:200], 'latency_ms': latency, 'url': url})

    # ── UAV registry — declared at module level above register_routes() ──────

    @app.route('/api/uav/positions', methods=['GET', 'POST'])
    def api_uav_positions():
        """
        GET  /api/uav/positions         → returns all live UAV records for AR polling
        POST /api/uav/positions         → globe JS pushes current swarm state (bulk update)

        AR clients (AndroidAppSceneview / RFScytheARNative) call GET at ~10 Hz to drive
        AR overlay positions.  The globe calls POST every ~500 ms to keep this in sync.

        POST body (JSON): { "uavs": [ {id, lat, lon, alt, color, label, speedKmh}, ... ] }
        GET response:     { "uavs": [...], "ts": <epoch_ms> }
        """
        import time
        global _uav_registry, _uav_hits, _uav_lock

        if request.method == 'POST':
            data = request.get_json(silent=True) or {}
            now  = time.time()
            with _uav_lock:
                for uav in data.get('uavs', []):
                    uid = uav.get('id')
                    if uid:
                        _uav_registry[uid] = {
                            'id':        uid,
                            'lat':       float(uav.get('lat', 0)),
                            'lon':       float(uav.get('lon', 0)),
                            'alt':       float(uav.get('alt', 1500)),
                            'color':     uav.get('color', '#00e5ff'),
                            'label':     uav.get('label', uid),
                            'speedKmh':  float(uav.get('speedKmh', 180)),
                            'rfDNA':     uav.get('rfDNA', ''),
                            'last_seen': now,
                        }
                # Expire entries not updated in 60 s
                stale = [k for k, v in _uav_registry.items() if now - v['last_seen'] >= 60]
                for k in stale:
                    del _uav_registry[k]
            return jsonify({'ok': True, 'count': len(_uav_registry)})

        # GET — prune stale then return
        now = time.time()
        with _uav_lock:
            stale = [k for k, v in _uav_registry.items() if now - v['last_seen'] >= 60]
            for k in stale:
                del _uav_registry[k]
            uavs = list(_uav_registry.values())
        return jsonify({'uavs': uavs, 'ts': int(now * 1000)})

    @app.route('/api/uav/hit', methods=['POST'])
    def api_uav_hit():
        """
        POST /api/uav/hit
        Body: { "uav_id": "...", "shooter_id": "ar-device-1", "lat": ..., "lon": ... }

        AR app posts this when the operator locks on and fires.
        Returns hit confirmation + whether the UAV was in registry (real hit vs miss).
        Also removes the UAV from the registry (it's been destroyed).
        Broadcasts a Socket.IO event so the globe can animate a kill effect.
        """
        import time
        data = request.get_json(silent=True) or {}
        uav_id     = data.get('uav_id', '')
        shooter_id = data.get('shooter_id', 'unknown')
        lat        = float(data.get('lat', 0))
        lon        = float(data.get('lon', 0))

        hit_record = {
            'uav_id':     uav_id,
            'shooter_id': shooter_id,
            'lat':        lat,
            'lon':        lon,
            'timestamp':  int(time.time() * 1000),
        }
        _uav_hits.append(hit_record)

        confirmed = uav_id in _uav_registry
        if confirmed:
            del _uav_registry[uav_id]

        # Broadcast to all connected SocketIO clients so the globe reacts
        try:
            socketio.emit('uav_hit', hit_record, namespace='/')
        except Exception:
            pass

        app.logger.info('[UAV-HIT] %s by %s confirmed=%s', uav_id, shooter_id, confirmed)
        return jsonify({'ok': True, 'confirmed': confirmed, 'uav_id': uav_id,
                        'score': 100 if confirmed else 0})

    @app.route('/api/uav/hits', methods=['GET'])
    def api_uav_hits():
        """GET /api/uav/hits — leaderboard / hit history for AR skeet scoring."""
        return jsonify({'hits': _uav_hits[-200:]})


    def api_clusters_swarms_stream():
        """
        GET /api/clusters/swarms/stream

        Server-Sent Events stream that pushes fresh swarm snapshots every
        ``interval`` seconds (default 5).

        SSE frame format:
            event: SWARM_SNAPSHOT
            data:  { count: N, clusters: [...] }

        Query params:
          interval:       float (default 5.0, min 2.0)
          min_size:       int   (default 2)
          geo_bucket_deg: float (default 1.0)
        """
        try:
            from cluster_swarm_engine import detect_clusters
        except ImportError as e:
            return jsonify({'status': 'error',
                            'message': 'cluster_swarm_engine not available: ' + str(e)}), 500

        interval = max(2.0, float(request.args.get('interval', 5.0)))
        min_size = int(request.args.get('min_size', 2))
        geo_deg  = float(request.args.get('geo_bucket_deg', 1.0))

        def generate():
            import json as _json
            try:
                while True:
                    snap     = _get_engine_snapshot()
                    clusters = detect_clusters(
                        snap.get('nodes', []), snap.get('edges', []),
                        geo_bucket_deg=geo_deg, min_size=min_size)

                    payload = _json.dumps({
                        'count':    len(clusters),
                        'clusters': [c.to_dict() for c in clusters],
                    })
                    yield f'event: SWARM_SNAPSHOT\ndata: {payload}\n\n'

                    import time as _time
                    _time.sleep(interval)
            except GeneratorExit:
                pass
            except Exception as ex:
                logger.error(f'[Swarm SSE] error: {ex}')
                yield f'event: ERROR\ndata: {{"message":"{ex}"}}\n\n'

        return Response(
            generate(),
            mimetype='text/event-stream',
            headers={
                'Cache-Control':    'no-cache',
                'Connection':       'keep-alive',
                'X-Accel-Buffering':'no',
            }
        )

    @app.route('/api/clusters/swarms/cot', methods=['GET'])
    def api_clusters_swarms_cot():
        """
        GET /api/clusters/swarms/cot

        Returns current swarm clusters as CoT XML events, ready for
        injection into ATAK or forwarding to a TAK server.

        Same query params as /api/clusters/swarms.
        Convenience alias equivalent to /api/clusters/swarms?format=cot
        """
        try:
            from cluster_swarm_engine import detect_clusters, clusters_to_cot_list

            min_size = int(request.args.get('min_size', 2))
            geo_deg  = float(request.args.get('geo_bucket_deg', 1.0))

            snap     = _get_engine_snapshot()
            clusters = detect_clusters(snap.get('nodes', []), snap.get('edges', []),
                                       geo_bucket_deg=geo_deg, min_size=min_size)
            cot_list = clusters_to_cot_list(clusters)

            return jsonify({
                'status': 'ok',
                'count':  len(cot_list),
                'events': [c.decode('utf-8') for c in cot_list],
            })
        except Exception as e:
            logger.error(f'[Swarm CoT] failed: {e}')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    # ========================================================================
    # API ROUTES - IMMUTABLE BATTLEFIELD LEDGER (replay / event log)
    # ========================================================================

    # Lazy-init: one SceneEventLog shared across all requests.
    # Stored on app config so it survives across requests.
    def _get_event_log():
        if not hasattr(app, '_scene_event_log'):
            try:
                from scene_event_log import SceneEventLog
                from scene_event_schema import (
                    entity_spawn, entity_move, entity_update, entity_remove,
                    swarm_create, swarm_update, swarm_dissolve,
                    rf_detect, rf_triangulate,
                )
                db_path = os.path.join(os.path.dirname(__file__), 'scene_events.db')
                app._scene_event_log = SceneEventLog(db_path)
                app._scene_event_log_available = True
                logger.info('[EventLog] SceneEventLog opened at %s', db_path)
            except Exception as exc:
                app._scene_event_log = None
                app._scene_event_log_available = False
                logger.warning('[EventLog] unavailable: %s', exc)
        return app._scene_event_log, getattr(app, '_scene_event_log_available', False)

    def _auto_log_swarm(cluster: dict, event_type: str = 'swarm.create') -> None:
        """Auto-log a swarm cluster event from a cluster dict (server-internal)."""
        log, ok = _get_event_log()
        if not ok or log is None:
            return
        try:
            from scene_event_schema import (
                swarm_create, swarm_update, swarm_dissolve
            )
            sid = getattr(app, '_active_ledger_session', None)
            if not sid:
                return
            centroid = [cluster.get('centroid_lat', 0), cluster.get('centroid_lon', 0)]
            if event_type == 'swarm.create':
                evt = swarm_create(cluster['id'], centroid,
                                   members=cluster.get('node_count', 0),
                                   threat_score=cluster.get('threat_score', 0),
                                   behavior=cluster.get('behavior_type', 'MIXED'))
            elif event_type == 'swarm.update':
                evt = swarm_update(cluster['id'], centroid=centroid,
                                   members=cluster.get('node_count', 0),
                                   threat_score=cluster.get('threat_score', 0))
            else:
                from scene_event_schema import swarm_dissolve
                evt = swarm_dissolve(cluster['id'])
            log.append(evt, session_id=sid)
        except Exception as exc:
            logger.debug('[EventLog] auto_log_swarm failed: %s', exc)

    @app.route('/api/replay/sessions', methods=['GET'])
    def api_replay_sessions():
        """
        GET /api/replay/sessions
        List all event log sessions.

        Response:
          { "sessions": [ { "session_id", "started_at", "ended_at",
                             "event_count", "meta" }, ... ] }
        """
        log, ok = _get_event_log()
        if not ok or log is None:
            return jsonify({'status': 'error',
                            'message': 'Event log unavailable'}), 503
        try:
            sessions = log.list_sessions()
            # Augment with live event count
            for s in sessions:
                s['event_count'] = log.event_count(s['session_id'])
            return jsonify({'status': 'ok', 'sessions': sessions})
        except Exception as exc:
            return jsonify({'status': 'error', 'message': str(exc)}), 500

    @app.route('/api/replay/session/start', methods=['POST'])
    def api_replay_session_start():
        """
        POST /api/replay/session/start
        Body: { "session_id": "op_2026_03_15", "seed": 0 }  (optional)

        Opens a new ledger session.  All subsequent auto-logged events go here.
        """
        log, ok = _get_event_log()
        if not ok or log is None:
            return jsonify({'status': 'error',
                            'message': 'Event log unavailable'}), 503
        try:
            data = request.get_json(silent=True) or {}
            import time as _time
            sid = data.get('session_id',
                           'session_' + str(int(_time.time())))
            seed = int(data.get('seed', 0))
            log.new_session(sid, seed=seed)
            app._active_ledger_session = sid

            # Emit asset.reference for the current server's terrain source
            from scene_event_schema import asset_reference
            import hashlib
            server_hash = 'sha256:' + hashlib.sha256(
                sid.encode()).hexdigest()[:16]
            log.append(asset_reference('rf_scythe_api_server', server_hash),
                       session_id=sid)

            logger.info('[EventLog] New session: %s', sid)
            return jsonify({'status': 'ok', 'session_id': sid})
        except Exception as exc:
            return jsonify({'status': 'error', 'message': str(exc)}), 500

    @app.route('/api/replay/session/end', methods=['POST'])
    def api_replay_session_end():
        """POST /api/replay/session/end — Close the active session."""
        log, ok = _get_event_log()
        if not ok or log is None:
            return jsonify({'status': 'error',
                            'message': 'Event log unavailable'}), 503
        try:
            sid = getattr(app, '_active_ledger_session', None)
            if not sid:
                return jsonify({'status': 'error',
                                'message': 'No active session'}), 400
            log.snapshot(sid)
            log.end_session(sid)
            app._active_ledger_session = None
            return jsonify({'status': 'ok', 'session_id': sid})
        except Exception as exc:
            return jsonify({'status': 'error', 'message': str(exc)}), 500

    @app.route('/api/replay/snapshot', methods=['POST'])
    def api_replay_snapshot():
        """POST /api/replay/snapshot — Persist a compressed scene snapshot."""
        log, ok = _get_event_log()
        if not ok or log is None:
            return jsonify({'status': 'error',
                            'message': 'Event log unavailable'}), 503
        try:
            sid = (request.get_json(silent=True) or {}).get(
                'session_id', getattr(app, '_active_ledger_session', None))
            if not sid:
                return jsonify({'status': 'error',
                                'message': 'No session specified'}), 400
            rowid = log.snapshot(sid)
            n     = log.event_count(sid)
            return jsonify({'status': 'ok', 'session_id': sid,
                            'after_rowid': rowid, 'total_events': n})
        except Exception as exc:
            return jsonify({'status': 'error', 'message': str(exc)}), 500

    @app.route('/api/replay/events', methods=['GET'])
    def api_replay_events():
        """
        GET /api/replay/events?session_id=X[&after=ROWID][&stream=1]

        Without ``stream=1``: returns all events (or events after *after* rowid)
        as a JSON array.

        With ``stream=1``: SSE stream that pushes events as they are appended
        to the live session (long-poll, 2 s heartbeat).
        """
        log, ok = _get_event_log()
        if not ok or log is None:
            return jsonify({'status': 'error',
                            'message': 'Event log unavailable'}), 503

        sid   = request.args.get('session_id',
                                 getattr(app, '_active_ledger_session', None))
        after = int(request.args.get('after', 0))
        stream = request.args.get('stream', '0') == '1'

        if not sid:
            return jsonify({'status': 'error',
                            'message': 'session_id required'}), 400

        if not stream:
            try:
                import msgpack as _mp
                events = list(log.iter_events(sid, after_rowid=after))
                return jsonify({'status': 'ok', 'session_id': sid,
                                'events': events, 'count': len(events)})
            except Exception as exc:
                return jsonify({'status': 'error', 'message': str(exc)}), 500

        # SSE streaming mode — push new events in real-time
        def _sse_gen():
            cursor = after
            import time as _time
            while True:
                try:
                    new_events = list(log.iter_events(sid, after_rowid=cursor))
                    for evt in new_events:
                        import json as _json
                        yield f"event: SCENE_EVENT\ndata: {_json.dumps(evt)}\n\n"
                    if new_events:
                        # Update cursor to last rowid
                        rows = log._conn.execute(
                            "SELECT MAX(rowid) FROM events WHERE session_id=?",
                            (sid,)).fetchone()
                        if rows and rows[0]:
                            cursor = rows[0]
                except GeneratorExit:
                    break
                except Exception as exc:
                    yield f"event: ERROR\ndata: {str(exc)}\n\n"
                    break
                _time.sleep(2.0)
                yield "event: HEARTBEAT\ndata: {}\n\n"

        from flask import Response
        return Response(_sse_gen(),
                        mimetype='text/event-stream',
                        headers={'Cache-Control': 'no-cache',
                                 'X-Accel-Buffering': 'no'})

    @app.route('/api/replay/state', methods=['GET'])
    def api_replay_state():
        """
        GET /api/replay/state?session_id=X[&timestamp=T]

        Returns the reconstructed SceneState at timestamp T (or current if omitted).
        Uses snapshot for fast reconstruction.
        """
        log, ok = _get_event_log()
        if not ok or log is None:
            return jsonify({'status': 'error',
                            'message': 'Event log unavailable'}), 503

        sid = request.args.get('session_id',
                               getattr(app, '_active_ledger_session', None))
        if not sid:
            return jsonify({'status': 'error',
                            'message': 'session_id required'}), 400

        ts = request.args.get('timestamp')

        try:
            if ts:
                from scene_replay_engine import ReplayEngine
                events_all = list(log.iter_events(sid))
                eng = ReplayEngine(events_all, speed=1e9)
                state = eng.scrub(float(ts))
            else:
                state = log.get_scene_state(sid)

            return jsonify({'status': 'ok', 'session_id': sid,
                            'state': state.to_dict()})
        except Exception as exc:
            return jsonify({'status': 'error', 'message': str(exc)}), 500

    @app.route('/api/replay/export', methods=['GET'])
    def api_replay_export():
        """
        GET /api/replay/export?session_id=X

        Downloads a self-contained .atakrec archive for offline replay.
        """
        log, ok = _get_event_log()
        if not ok or log is None:
            return jsonify({'status': 'error',
                            'message': 'Event log unavailable'}), 503

        sid = request.args.get('session_id',
                               getattr(app, '_active_ledger_session', None))
        if not sid:
            return jsonify({'status': 'error',
                            'message': 'session_id required'}), 400

        try:
            import tempfile, os as _os
            from flask import send_file
            log.snapshot(sid)   # ensure fresh snapshot
            tmp = tempfile.NamedTemporaryFile(suffix='.atakrec', delete=False)
            tmp.close()
            log.export_atakrec(sid, tmp.name)
            fname = sid.replace('/', '_') + '.atakrec'
            return send_file(tmp.name,
                             mimetype='application/zip',
                             as_attachment=True,
                             download_name=fname)
        except Exception as exc:
            return jsonify({'status': 'error', 'message': str(exc)}), 500

    # ========================================================================
    # API ROUTES - DUCKDB / PARQUET EVENT STORE
    # ========================================================================

    try:
        from scene_duckdb_store import ScytheDuckStore, TacticalEvent as _TacticalEvent
        from scene_parquet_pipeline import ParquetPipeline as _ParquetPipeline
        _duck_store = ScytheDuckStore(
            db_path=os.path.join(_data_dir(), 'scythe_events.duckdb'),
            parquet_dir=os.path.join(_data_dir(), 'parquet_blocks'),
        )
        _parquet_pipe = _ParquetPipeline(store=_duck_store)
        logger.info('DuckDB tactical event store initialized')
    except Exception as _e:
        _duck_store = None
        _parquet_pipe = None
        logger.warning('DuckDB store unavailable: %s', _e)

    @app.route('/api/events/ingest', methods=['POST'])
    def events_ingest():
        """Bulk-ingest events from the Android plugin (or any source).

        Body: { "events": [ { timestamp, event_type, entity_id, session_id,
                               lat, lon, alt, payload } ] }
        """
        if _duck_store is None:
            return jsonify({'status': 'error', 'message': 'DuckDB store not available'}), 503
        data = request.get_json(force=True, silent=True) or {}
        raw  = data.get('events', [])
        if not raw:
            return jsonify({'status': 'error', 'message': 'no events'}), 400
        events = []
        for r in raw:
            events.append(_TacticalEvent(
                timestamp  = int(r.get('timestamp', 0)),
                event_type = str(r.get('event_type', 'unknown')),
                entity_id  = str(r.get('entity_id', '')),
                session_id = str(r.get('session_id', 'default')),
                lat        = float(r.get('lat', 0.0)),
                lon        = float(r.get('lon', 0.0)),
                alt        = float(r.get('alt', 0.0)),
                payload    = r.get('payload') or {},
            ))
        count = _duck_store.append_batch(events)
        return jsonify({'status': 'ok', 'ingested': count})

    @app.route('/api/events/query', methods=['GET', 'POST'])
    def events_query():
        """Execute arbitrary DuckDB SQL against the events table.

        GET  ?sql=SELECT+...
        POST { "sql": "SELECT ..." }
        """
        if _duck_store is None:
            return jsonify({'status': 'error', 'message': 'DuckDB store not available'}), 503
        if request.method == 'POST':
            sql = (request.get_json(force=True, silent=True) or {}).get('sql', '')
        else:
            sql = request.args.get('sql', '')
        if not sql:
            return jsonify({'status': 'error', 'message': 'sql parameter required'}), 400
        # Basic safety: allow SELECT only
        if not sql.strip().upper().startswith('SELECT'):
            return jsonify({'status': 'error', 'message': 'only SELECT queries allowed'}), 403
        try:
            rows = _duck_store.query_sql(sql)
            return jsonify({'status': 'ok', 'rows': rows, 'count': len(rows)})
        except Exception as exc:
            return jsonify({'status': 'error', 'message': str(exc)}), 400

    @app.route('/api/events/stats', methods=['GET'])
    def events_stats():
        if _duck_store is None:
            return jsonify({'status': 'error', 'message': 'DuckDB store not available'}), 503
        return jsonify({'status': 'ok', 'stats': _duck_store.stats()})

    @app.route('/api/events/export/parquet', methods=['GET'])
    def events_export_parquet():
        """Export a time-range to a Parquet file and stream it back.

        ?t0=<unix_ms>&t1=<unix_ms>&session_id=<optional>
        """
        if _duck_store is None or _parquet_pipe is None:
            return jsonify({'status': 'error', 'message': 'DuckDB store not available'}), 503
        from flask import send_file
        try:
            t0  = int(request.args.get('t0', 0))
            t1  = int(request.args.get('t1', int(time.time() * 1000)))
            sid = request.args.get('session_id') or None
            fpath = _duck_store.export_parquet_block(t0, t1, sid)
            return send_file(str(fpath),
                             mimetype='application/octet-stream',
                             as_attachment=True,
                             download_name=fpath.name)
        except ValueError as exc:
            return jsonify({'status': 'error', 'message': str(exc)}), 404
        except Exception as exc:
            return jsonify({'status': 'error', 'message': str(exc)}), 500

    @app.route('/api/events/blocks', methods=['GET'])
    def events_list_blocks():
        """List all Parquet blocks in the cold store."""
        if _duck_store is None:
            return jsonify({'status': 'error', 'message': 'DuckDB store not available'}), 503
        if _parquet_pipe is None:
            return jsonify({'status': 'ok', 'blocks': _duck_store.list_parquet_blocks()})
        return jsonify({'status': 'ok', **_parquet_pipe.inventory()})

    @app.route('/api/events/flush', methods=['POST'])
    def events_flush_blocks():
        """Flush hot DuckDB events to Parquet blocks.

        Body: { "block_seconds": 60, "session_id": "..." }
        """
        if _parquet_pipe is None:
            return jsonify({'status': 'error', 'message': 'Parquet pipeline not available'}), 503
        data = request.get_json(force=True, silent=True) or {}
        sid  = data.get('session_id') or None
        _parquet_pipe._block_sec = int(data.get('block_seconds', 60))
        blocks = _parquet_pipe.flush_auto_blocks(sid)
        return jsonify({
            'status': 'ok',
            'blocks_written': len(blocks),
            'blocks': [b.to_dict() for b in blocks],
        })

    # ========================================================================
    # API ROUTES - SYSTEM STATUS
    # ========================================================================

    @app.route('/api/status', methods=['GET'])
    def get_status():
        """Get overall system status"""
        status_data = {
            'status': 'ok',
            'server': 'RF SCYTHE Integrated Server',
            'version': '1.3.0',
            'uptime': time.monotonic() - hypergraph_store.start_time,
            'components': {
                'hypergraph': {
                    'nodes': len(hypergraph_store.nodes),
                    'edges': len(hypergraph_store.hyperedges),
                    'session_id': hypergraph_store.session_id
                },
                'ecosystem': {
                    'available': revenge_ecosystem is not None,
                    'nodes': len(revenge_ecosystem.nodes) if revenge_ecosystem else 0,
                    'edges': len(revenge_ecosystem.edges) if revenge_ecosystem else 0,
                    'events': len(revenge_ecosystem.events) if revenge_ecosystem else 0,
                    'organ_state': revenge_ecosystem.organ_state.to_dict() if revenge_ecosystem else None
                },
                'nmap': {
                    'available': nmap_scanner.check_nmap_available(),
                    'scanning': nmap_scanner.scanning
                },
                'ndpi': {
                    'available': ndpi_analyzer.check_ndpi_available(),
                    'analyzing': ndpi_analyzer.analyzing
                },
                'ais': {
                    'available': ais_tracker.csv_loaded,
                    'vessel_count': len(ais_tracker.vessels)
                },
                'recon': {
                    'active': recon_system.active,
                    'entity_count': len(recon_system.entities),
                    'task_count': len(recon_system.tasks),
                    'alert_count': len(recon_system.get_proximity_alerts())
                },
                'operators': {
                    'available': operator_manager is not None,
                    'stats': operator_manager.get_stats() if operator_manager else None
                },
                'rooms': {
                    'available': operator_manager is not None,
                    'count': len(operator_manager.rooms) if operator_manager else 0,
                    'websocket_available': SOCKETIO_AVAILABLE
                },
                'poi': {
                    'available': poi_manager is not None,
                    'count': poi_manager.get_poi_count() if poi_manager else 0
                }
            },
            'timestamp': time.time()
        }
        return jsonify(status_data)

    @app.route('/api/health', methods=['GET'])
    def health_check():
        """Health check endpoint"""
        return jsonify({'status': 'healthy', 'timestamp': time.time()})

    # ────────────────────────────────────────────────────────────────────
    # NEW HOST DETECTION & pcapng LOGGER ENDPOINTS
    # ────────────────────────────────────────────────────────────────────

    @app.route('/api/network/new-hosts', methods=['GET'])
    def get_new_hosts():
        """Get inventory of newly discovered hosts with pcapng captures."""
        try:
            import new_host_pcapng_logger
            hosts = new_host_pcapng_logger.get_host_inventory()
            stats = new_host_pcapng_logger.get_discovery_stats()
            return jsonify({
                'status': 'ok',
                'hosts': hosts,
                'stats': stats,
                'count': len(hosts)
            })
        except ImportError:
            return jsonify({'status': 'error', 'message': 'New host logger not available'}), 503
        except Exception as e:
            logger.error(f"[API] Error fetching new hosts: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/network/host-discovery/stats', methods=['GET'])
    def get_discovery_stats():
        """Get host discovery statistics and pcapng capture summary."""
        try:
            import new_host_pcapng_logger
            stats = new_host_pcapng_logger.get_discovery_stats()
            return jsonify({
                'status': 'ok',
                'data': stats
            })
        except ImportError:
            return jsonify({'status': 'error', 'message': 'New host logger not available'}), 503
        except Exception as e:
            logger.error(f"[API] Error fetching discovery stats: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/network/host-cleanup', methods=['POST'])
    def cleanup_old_hosts():
        """Remove hosts not seen in N days."""
        try:
            import new_host_pcapng_logger
            days = request.args.get('days', 30, type=int)
            count = new_host_pcapng_logger.cleanup_old_hosts(days)
            return jsonify({
                'status': 'ok',
                'message': f'Cleaned up {count} hosts not seen in {days} days',
                'deleted': count
            })
        except ImportError:
            return jsonify({'status': 'error', 'message': 'New host logger not available'}), 503
        except Exception as e:
            logger.error(f"[API] Error cleaning up hosts: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    # ────────────────────────────────────────────────────────────────────
    # NETWORK INGRESS AGGREGATION & ORCHESTRATION
    # ────────────────────────────────────────────────────────────────────

    @app.route('/api/network/ingress/interfaces', methods=['GET'])
    def get_ingress_interfaces():
        """Get current ingress state across all network interfaces.

        Returns real-time RX/TX metrics, interface roles, and active addresses.
        """
        try:
            if not _ingress_aggregator_available:
                return jsonify({'status': 'error', 'message': 'Ingress aggregator not available'}), 503

            ingress = network_ingress_aggregator.get_current_ingress()
            summary = network_ingress_aggregator.get_ingress_summary()

            return jsonify({
                'status': 'ok',
                'timestamp': ingress.get('timestamp'),
                'interfaces': ingress.get('interfaces', []),
                'summary': summary
            })

        except Exception as e:
            logger.error(f"[API] Error fetching ingress interfaces: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/network/ingress/by-role/<role>', methods=['GET'])
    def get_interfaces_by_role(role):
        """Get interfaces filtered by role (physical, mesh_vpn, container_overlay, etc)."""
        try:
            if not _ingress_aggregator_available:
                return jsonify({'status': 'error', 'message': 'Ingress aggregator not available'}), 503

            interfaces = network_ingress_aggregator.get_interface_by_role(role)

            return jsonify({
                'status': 'ok',
                'role': role,
                'count': len(interfaces),
                'interfaces': interfaces
            })

        except Exception as e:
            logger.error(f"[API] Error fetching {role} interfaces: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/network/host-confidence', methods=['POST'])
    def calculate_host_confidence():
        """Calculate confidence score for a discovered host.

        POST body:
        {
            "ip": "192.168.1.100",
            "metadata": {
                "is_new": true,
                "foreign_asn": false,
                "suspicious_ports": [4444, 5555],
                "protocol_entropy": 0.75,
                "dns_anomaly": false,
                "lateral_behavior": true
            }
        }
        """
        try:
            if not _ingress_aggregator_available:
                return jsonify({'status': 'error', 'message': 'Ingress aggregator not available'}), 503

            data = request.get_json() or {}
            host_ip = data.get('ip')
            metadata = data.get('metadata', {})

            if not host_ip:
                return jsonify({'status': 'error', 'message': 'Missing "ip" field'}), 400

            score = network_ingress_aggregator.calculate_host_confidence_score(host_ip, metadata)
            tier = network_ingress_aggregator.determine_capture_tier(score)

            return jsonify({
                'status': 'ok',
                'ip': host_ip,
                'confidence_score': round(score, 1),
                'capture_tier': tier,
                'should_capture': score >= 20
            })

        except Exception as e:
            logger.error(f"[API] Error calculating confidence: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/network/ingress/summary', methods=['GET'])
    def get_ingress_summary():
        """Get high-level summary of ingress activity across all interfaces."""
        try:
            if not _ingress_aggregator_available:
                return jsonify({'status': 'error', 'message': 'Ingress aggregator not available'}), 503

            summary = network_ingress_aggregator.get_ingress_summary()

            return jsonify({
                'status': 'ok',
                'data': summary
            })

        except Exception as e:
            logger.error(f"[API] Error fetching ingress summary: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/health/queues', methods=['GET'])
    def health_queues():
        """Backpressure / queue depth telemetry for operational monitoring."""
        try:
            q     = globals().get('_graph_event_q')
            drops = globals().get('_graph_event_drops', [0])
            depth = q.qsize() if q is not None else 0
            cap   = 2000
            return jsonify({
                'status': 'ok',
                'graph_event_queue': {
                    'depth': depth,
                    'capacity': cap,
                    'utilization': round(depth / cap, 4) if cap else 0,
                    'drops_total': drops[0],
                    'healthy': depth < cap * 0.80,
                },
                'timestamp': time.time(),
            })
        except Exception as exc:
            return jsonify({'status': 'error', 'message': str(exc)}), 500

    # ========================================================================
    # RF FIELD TENSOR — /api/rf/field
    # ========================================================================
    # Returns 128×128 float32 field + prediction tensors built from live
    # rf_node graph events by the Temporal Field Predictor.
    # The drone-level Socket.IO push (rf_field_update) is emitted from the
    # GraphEventBus drain thread whenever rf_node emitters change.
    # ========================================================================

    try:
        from rf_field_generator import (
            _emitter_history as _rf_emitter_history,
            get_field_snapshot as _rf_field_snapshot,
        )

        @app.route('/api/rf/field', methods=['GET'])
        def rf_field():
            """
            GET /api/rf/field?bounds=min_lon,min_lat,max_lon,max_lat&grid=128

            Returns real + predicted RF field tensors as nested float lists.
            Shape: grid_size × grid_size, values 0.0–1.0.
            """
            try:
                grid   = min(512, max(32, int(request.args.get('grid', 128))))
                raw_b  = request.args.get('bounds')
                bounds = None
                if raw_b:
                    parts = [float(x) for x in raw_b.split(',')]
                    if len(parts) == 4:
                        bounds = parts

                from rf_field_generator import _field_generator, _field_predictor
                _field_generator.grid_size = grid
                snap = _rf_field_snapshot(bounds=bounds)
                return jsonify({'status': 'ok', **snap})
            except Exception as exc:
                return jsonify({'status': 'error', 'message': str(exc)}), 500

        # Wire EmitterHistory into the GraphEventBus now (lazy — bus exists by this point)
        try:
            _rf_emitter_history.subscribe_to_bus(graph_event_bus)
        except NameError:
            pass  # graph_event_bus not in scope — will be wired at startup

        logger.info('[RFField] /api/rf/field endpoint registered + EmitterHistory wired')
    except ImportError as _rffe:
        logger.warning('[RFField] rf_field_generator not available: %s', _rffe)

    # ========================================================================
    # RF VOLUMETRIC FIELD — /api/rf/field3d
    # ========================================================================
    # Returns a (Z,H,W) uint8-quantised base64 field pair for the WebGL2
    # raymarching renderer.  Served from cache (updated every ~3 s by the
    # rf3d-worker thread) — never generates inline on request.
    # ========================================================================
    try:
        @app.route('/api/rf/field3d', methods=['GET'])
        def rf_field3d():
            """
            GET /api/rf/field3d

            Returns cached volumetric snapshot.  Generate on demand by passing
            ?force=1 (forces regeneration, slower — avoid polling with this).
            """
            try:
                force = request.args.get('force', '0') == '1'
                cached = globals().get('_rf3d_cached', [None])[0]
                if force or cached is None:
                    from rf_field_generator import get_field3d_snapshot as _g3d
                    cached = _g3d()
                    _c = globals().get('_rf3d_cached')
                    if _c is not None:
                        _c[0] = cached
                if cached is None:
                    return jsonify({'status': 'pending',
                                    'message': 'No 3D field data yet — waiting for RF emitter events'}), 202
                return jsonify({'status': 'ok', **cached})
            except Exception as exc:
                logger.exception('field3d error')
                return jsonify({'status': 'error', 'message': str(exc)}), 500

        logger.info('[RFField] /api/rf/field3d endpoint registered')
    except Exception as _e3d:
        logger.warning('[RFField] /api/rf/field3d registration failed: %s', _e3d)



    # ========================================================================
    # RF IQ CLASSIFIER  — /api/rf/classify
    # ========================================================================
    # Lightweight signal classification from raw or pre-processed IQ samples.
    # Accepts float32 IQ pairs and returns a drone-class label, confidence,
    # and a bearing hint derived from bearing_deg if provided.
    #
    # Input JSON:
    #   {
    #     "iq":         [float, ...],   // interleaved I,Q samples (min 64 pairs)
    #     "sample_rate": float,          // Hz (optional, default 2e6)
    #     "bearing_deg": float,          // receiver bearing hint (optional)
    #     "lat": float, "lon": float     // receiver location (optional)
    #   }
    #
    # Response JSON:
    #   {
    #     "label": "DJI_Mavic_3",
    #     "confidence": 0.87,
    #     "features": { "bandwidth_hz": ..., "peak_freq_hz": ..., ... },
    #     "bearing_deg": 142.3,          // echoed if provided
    #     "lat": ..., "lon": ...
    #   }
    # ========================================================================

    @app.route('/api/rf/classify', methods=['POST'])
    def rf_classify():
        """Classify an RF IQ sample and return emitter label + features."""
        try:
            body = request.get_json(force=True, silent=True) or {}
            iq_raw = body.get('iq', [])
            sr     = float(body.get('sample_rate', 2e6))

            if len(iq_raw) < 128:
                return jsonify({'error': 'Need at least 64 IQ pairs (128 floats)'}), 400

            import numpy as np

            # --- Build complex IQ array ---
            iq = np.array(iq_raw, dtype=np.float32)
            if iq.ndim == 1:
                iq = iq[:len(iq) - len(iq) % 2]          # ensure even length
                ciq = iq[0::2] + 1j * iq[1::2]
            else:
                ciq = iq[:, 0] + 1j * iq[:, 1]

            N = len(ciq)

            # --- Feature extraction (no heavy model required) ---
            # 1. Power spectral density via FFT
            fft_mag = np.abs(np.fft.fftshift(np.fft.fft(ciq, n=min(N, 2048)))) ** 2
            freqs   = np.fft.fftshift(np.fft.fftfreq(len(fft_mag), d=1.0/sr))

            # 2. Bandwidth (–6 dB)
            peak_power = fft_mag.max()
            mask_6db   = fft_mag >= peak_power * 0.25
            if mask_6db.any():
                bw_hz = float(freqs[mask_6db][-1] - freqs[mask_6db][0])
            else:
                bw_hz = 0.0

            # 3. Peak frequency offset
            peak_idx  = int(np.argmax(fft_mag))
            peak_freq = float(freqs[peak_idx])

            # 4. Modulation entropy (spectral flatness proxy)
            psd_norm = fft_mag / (fft_mag.sum() + 1e-12)
            entropy  = float(-np.sum(psd_norm * np.log2(psd_norm + 1e-12)))

            # 5. Burst / CW detection: ratio of peak to mean power
            peak_to_mean = float(peak_power / (fft_mag.mean() + 1e-12))

            # 6. Signal power (dBm relative)
            power_dbm = float(10 * np.log10(np.mean(np.abs(ciq)**2) + 1e-12))

            features = {
                'bandwidth_hz': round(bw_hz, 1),
                'peak_freq_hz': round(peak_freq, 1),
                'entropy':      round(entropy, 4),
                'peak_to_mean': round(peak_to_mean, 2),
                'power_dbm':    round(power_dbm, 2),
                'n_samples':    N,
            }

            # --- Rule-based classifier (no external model dependency) ---
            # Heuristics derived from known consumer UAV RF profiles.
            label, confidence = _rf_heuristic_classify(bw_hz, peak_freq, entropy, peak_to_mean, sr)

            result = {
                'label':      label,
                'confidence': round(confidence, 3),
                'features':   features,
            }
            if 'bearing_deg' in body:
                result['bearing_deg'] = body['bearing_deg']
            if 'lat' in body:
                result['lat'] = body['lat']
            if 'lon' in body:
                result['lon'] = body['lon']

            # Broadcast to connected globe clients so bearing wedges appear live
            socketio.emit('rf_classification', result)
            return jsonify(result)

        except Exception as exc:
            logger.exception('rf_classify error')
            return jsonify({'error': str(exc)}), 500


    def _rf_heuristic_classify(bw_hz, peak_freq, entropy, peak_to_mean, sample_rate):
        """
        Fast rule-based RF emitter classifier.
        Returns (label, confidence) without requiring a trained model.

        Frequency bands and bandwidths are representative of common UAV RF
        protocols documented in open RF datasets (2.4 GHz and 5.8 GHz ISM,
        GPS L1 at 1575.42 MHz, video downlink at 5.8 GHz).

        The sample_rate argument is used to normalize peak_freq to an absolute
        frequency band when the signal was captured with a known center frequency
        embedded in the stream — here we treat peak_freq as an offset from center.
        """
        abs_freq = abs(peak_freq)  # offset from IF center in Hz

        # ── GPS-like narrow CW: very low BW, low entropy ─────────────────
        if bw_hz < 5e3 and entropy < 4.0:
            return 'GPS_CW', 0.72

        # ── DJI OcuSync / O3: wideband OFDM, high entropy ────────────────
        if bw_hz > 8e6 and entropy > 9.0:
            conf = min(0.93, 0.70 + (entropy - 9.0) * 0.05)
            return 'DJI_OcuSync', conf

        # ── DJI 2.4 GHz Mavic-class: 10–20 MHz, moderate entropy ─────────
        if 10e6 <= bw_hz <= 22e6 and 6.0 < entropy < 9.5:
            return 'DJI_Mavic_class', 0.78

        # ── FHSS (frequency hopping): high peak-to-mean, moderate BW ─────
        if peak_to_mean > 15.0 and 1e6 < bw_hz < 12e6:
            return 'FHSS_UAV', 0.65

        # ── Narrowband telemetry (FrSky/ExpressLRS etc.) ──────────────────
        if bw_hz < 2e6 and 4.0 < entropy < 7.5:
            return 'RC_Telemetry', 0.61

        # ── Wideband video downlink (analog or digital) ───────────────────
        if bw_hz > 20e6 and peak_to_mean < 5.0:
            return 'Video_Downlink', 0.58

        return 'Unknown_RF', 0.30

    # ========================================================================
    # STATIC FILE SERVING
    # ========================================================================

    @app.route('/api/bootstrap.js')
    def bootstrap_js():
        """
        Single source of truth for client-side configuration.
        Served as a synchronous <script> tag before any other JS.
        Sets window.__SCYTHE_BOOTSTRAP__ so _initApiBase() and
        connectDataStreams() have zero-ambiguity config at parse time.
        """
        from flask import request as _req, Response as _Response
        import json as _json
        port     = app.config.get('SCYTHE_PORT', 5001)
        inst_id  = app.config.get('SCYTHE_INSTANCE_ID', '')
        relay    = app.config.get('STREAM_RELAY_URL', 'ws://localhost:8765/ws')
        mcp_ws   = app.config.get('MCP_WS_URL',       'ws://localhost:8766/ws')
        takml    = app.config.get('TAKML_URL',         'http://localhost:8234')
        eve_ws   = app.config.get('EVE_STREAM_WS_URL', 'ws://localhost:8081/ws')
        eve_http = app.config.get('EVE_STREAM_HTTP_URL', 'http://localhost:8081')
        orch_url = app.config.get('ORCHESTRATOR_URL',  '')
        # Use the Host header so the bootstrap reflects the address the
        # browser actually used (handles LAN IP, Tailscale, reverse proxy).
        host     = _req.host  # e.g. "192.168.1.185:46073"
        # Tailscale serve (and other TLS-terminating proxies) forward requests as
        # plain HTTP but set X-Forwarded-Proto: https. Prefer that over is_secure
        # so the client gets an https:// api_base and avoids CORS/mixed-content.
        scheme   = _req.headers.get('X-Forwarded-Proto',
                       'https' if _req.is_secure else 'http')
        # When scythe_orchestrator reverse-proxies ( /scythe/i/<id>/... ), the
        # public API root includes the path prefix; absolute /api/... fetches
        # would otherwise hit the wrong origin.
        path_prefix = _req.headers.get('X-Forwarded-Prefix', '').rstrip('/')
        public_base = _req.headers.get('X-SCYTHE-PUBLIC-BASE', '').rstrip('/')
        if public_base:
            api_base = public_base
        else:
            api_base = f'{scheme}://{host}'

        # Normalize stream URLs if behind orchestrator proxy (path_prefix present)
        def _norm_ws(url):
            if not path_prefix: return url
            from urllib.parse import urlparse
            u = urlparse(url)
            if not u.port: return url
            ws_proto = 'wss' if scheme == 'https' else 'ws'
            path = (u.path or '').strip('/')
            suffix = '' if not path or path == 'ws' else f'/{path}'
            return f"{ws_proto}://{host}/proxy/{u.port}/ws{suffix}"

        relay    = _norm_ws(relay)
        mcp_ws   = _norm_ws(mcp_ws)
        eve_ws   = _norm_ws(eve_ws)
        voxel_ws = _norm_ws(f"ws://localhost:9001/stream")

        socketio_path = (path_prefix + '/socket.io') if path_prefix else '/socket.io'
        boot_obj = {
            'api_base':         api_base,
            'path_prefix':      path_prefix,
            'socketio_path':    socketio_path,
            'instance_id':      inst_id,
            'stream_relay':     relay,
            'mcp_ws':           mcp_ws,
            'takml':            takml,
            'eve_stream_ws':    eve_ws,
            'eve_stream_http':  eve_http,
            'voxel_stream':     voxel_ws,
            'orchestrator_url': orch_url,
        }
        boot_json = _json.dumps(boot_obj)
        js = (
            'window.__SCYTHE_BOOTSTRAP__ = ' + boot_json + ';'
            'window.SCYTHE_API_BASE = window.__SCYTHE_BOOTSTRAP__.api_base;'
            "console.info('[BOOTSTRAP] config injected:', window.__SCYTHE_BOOTSTRAP__);"
            + "(function(){var P=window.__SCYTHE_BOOTSTRAP__&&window.__SCYTHE_BOOTSTRAP__.path_prefix;"
            "if(!P)return;var _f=window.fetch;window.fetch=function(a,b){"
            "if(typeof a==='string'&&(a.startsWith('/api/')||a.startsWith('/socket.io')))a=P+a;"
            "return _f.call(this,a,b);};"
            "var E=window.EventSource; if(E){window.EventSource=function(u,c){"
            "if(typeof u==='string'&&u.startsWith('/api/'))u=P+u;"
            "return c!==undefined?new E(u,c):new E(u);};}"
            "})();"
        )
        return _Response(js, mimetype='application/javascript',
                         headers={'Cache-Control': 'no-store'})

    @app.route('/')
    def serve_index():
        """Serve the main visualization page"""
        return send_from_directory('.', 'command-ops-visualization.html')

    @app.route('/<path:filename>')
    def serve_static(filename):
        """Serve static files"""
        return send_from_directory('.', filename)


# ============================================================================
# MAIN
# ============================================================================

def main():
    # ── InstanceDB health and diagnostics endpoints (MCP tools) ──
    @app.route('/api/instance/db/health', methods=['GET'])
    def instance_db_health():
        if 'instance_db' in globals() and instance_db:
            return jsonify(instance_db.health())
        return jsonify({'ok': False, 'error': 'InstanceDB unavailable'})

    @app.route('/api/instance/db/why_no_sessions', methods=['GET'])
    def instance_db_why_no_sessions():
        if 'instance_db' in globals() and instance_db:
            return jsonify(instance_db.why_no_sessions())
        return jsonify({'ok': False, 'error': 'InstanceDB unavailable'})

    # NOTE: InstanceDB initialization is deferred until after CLI args are
    # parsed and data_dir/instance_id are known – see below in argument
    # handling section.
    """Main entry point"""
    if not FLASK_AVAILABLE:
        print("❌ Flask is required. Install with:")
        print("   pip install flask flask-cors")
        sys.exit(1)

    import argparse

    parser = argparse.ArgumentParser(description='RF SCYTHE Integrated API Server')
    parser.add_argument('--host', type=str, default='0.0.0.0', help='Host to bind to')
    parser.add_argument('--port', type=int, default=8080, help='Port to bind to')
    parser.add_argument('--debug', action='store_true', help='Enable debug mode')
    parser.add_argument('--generate-test', action='store_true', help='Generate test data on startup')
    parser.add_argument('--instance-id', type=str, default=None,
                        help='Unique instance identifier for multi-tenant orchestration')
    parser.add_argument('--orchestrator-url', type=str, default=None,
                        help='URL of the SCYTHE orchestrator to register with on startup')
    parser.add_argument('--data-dir', type=str, default=None,
                        help='Per-instance data directory for SQLite, snapshots, and logs. '
                             'Defaults to "metrics_logs". Set per-instance for storage isolation.')
    parser.add_argument('--satellite-refresh', action='store_true', help='Enable background satellite TLE refresh from Celestrak')
    parser.add_argument('--stream-relay-url', type=str, default='ws://localhost:8765/ws',
                        help='WebSocket URL of the local Go stream relay (default: ws://localhost:8765/ws)')
    parser.add_argument('--mcp-ws-url', type=str, default='ws://localhost:8766/ws',
                        help='WebSocket URL of the MCP WebSocket bridge (default: ws://localhost:8766/ws)')
    parser.add_argument('--takml-url', type=str, default='http://localhost:8234',
                        help='HTTP base URL of the TAK-ML KServe inference server (default: http://localhost:8234)')
    parser.add_argument('--eve-stream-ws-url', type=str, default='ws://localhost:8081/ws',
                        help='WebSocket URL of the eve-streamer sensor daemon (default: ws://localhost:8081/ws)')
    parser.add_argument('--eve-stream-http-url', type=str, default='http://localhost:8081',
                        help='HTTP base URL of the eve-streamer sensor daemon (default: http://localhost:8081)')
    parser.add_argument('--internal-token', type=str, default=None,
                        help='Shared secret for internal orchestrator↔instance calls (X-Internal-Token)')
    args = parser.parse_args()

    # Store internal token for use in route handlers
    app.config['INTERNAL_TOKEN'] = args.internal_token or ''

    # ── Per-instance data directory (storage sovereignty) ──
    global _SCYTHE_DATA_DIR
    if args.data_dir:
        _SCYTHE_DATA_DIR = args.data_dir
    data_dir = _data_dir()
    os.makedirs(data_dir, exist_ok=True)
    app.config['SCYTHE_DATA_DIR'] = data_dir
    app.config['STREAM_RELAY_URL'] = args.stream_relay_url
    app.config['MCP_WS_URL']       = args.mcp_ws_url
    app.config['TAKML_URL']        = args.takml_url
    app.config['EVE_STREAM_WS_URL'] = args.eve_stream_ws_url
    app.config['EVE_STREAM_HTTP_URL'] = args.eve_stream_http_url
    app.config['GRAPHOPS_SENSOR_GROUNDING_LAST'] = None
    os.environ['EVE_STREAM_WS_URL'] = args.eve_stream_ws_url
    os.environ['EVE_STREAM_HTTP_URL'] = args.eve_stream_http_url
    logger.info(f"Stream relay:  {args.stream_relay_url}")
    logger.info(f"MCP WebSocket: {args.mcp_ws_url}")
    logger.info(f"TAK-ML URL:    {args.takml_url}")
    logger.info(f"EVE stream WS: {args.eve_stream_ws_url}")
    logger.info(f"EVE stream HTTP: {args.eve_stream_http_url}")
    logger.info(f"Data directory: {os.path.abspath(data_dir)}")

    # Re-initialize MetricsLogger into this instance's data directory
    global metrics_logger
    metrics_logger = MetricsLogger(log_dir=data_dir)

    # Re-initialize DuckDB event store with the instance-scoped path.
    # The module-level init ran before --data-dir was applied, so it used
    # the default 'metrics_logs' path — which may be locked by the primary
    # instance.  Re-init here with the correct per-instance path.
    global _duck_store, _parquet_pipe
    try:
        from scene_duckdb_store import ScytheDuckStore as _SDS
        from scene_parquet_pipeline import ParquetPipeline as _PP
        if _duck_store is not None:
            try:
                _duck_store._con.close()
            except Exception:
                pass
        _duck_store = _SDS(
            db_path=os.path.join(data_dir, 'scythe_events.duckdb'),
            parquet_dir=os.path.join(data_dir, 'parquet_blocks'),
        )
        _parquet_pipe = _PP(store=_duck_store)
        logger.info('DuckDB event store (re-)initialized: %s', data_dir)
    except Exception as _duck_err:
        _duck_store = None
        _parquet_pipe = None
        logger.warning('DuckDB store unavailable after re-init: %s', _duck_err)
    # Satellite table must be (re-)created in the instance-specific DB
    # because _init_satellite_table() ran at module load against the global DB.
    _init_satellite_table()

    # Store instance identity for API introspection
    instance_id = args.instance_id or f'scythe-{args.port}'
    app.config['SCYTHE_INSTANCE_ID'] = instance_id
    app.config['SCYTHE_PORT'] = args.port
    app.config['ORCHESTRATOR_URL'] = getattr(args, 'orchestrator_url', '') or ''

    # ── Per-instance Postgres/SQLite authority DB ──
    try:
        from scythe_pg import InstanceDB
        global instance_db
        instance_db = InstanceDB(data_dir=data_dir, instance_id=instance_id)
        app.config['INSTANCE_DB'] = instance_db
        logger.info(f"InstanceDB initialized: {instance_db}")
    except Exception as e:
        logger.warning(f"InstanceDB unavailable: {e}")
        instance_db = None

    # ====================================================================
    # REHYDRATION POLICY (Twin of the UI Clearing Policy)
    # ====================================================================
    #
    # Principle:
    #   "Instances must start epistemically empty.
    #    Only the parent/standalone process may auto-rehydrate."
    #
    # What auto-rehydrates:
    #   - Operator identities (global, always)       ← OK
    #
    # What is GATED by instance mode:
    #   - Hypergraph snapshot (evidence)              ← BLOCKED in --instance-id
    #   - Recon entities (derived from evidence)      ← BLOCKED in --instance-id
    #   - Operator sessions (instance-scoped)         ← Re-scoped to data-dir
    #
    # Enforcement:
    #   --instance-id present  → fresh epistemic state, no snapshot, no recon
    #   --instance-id absent   → parent/standalone, full rehydration allowed
    # ====================================================================

    _is_child_instance = bool(args.instance_id)

    # Fix 2: Re-scope operator session DB to instance data-dir
    #   Operator identities remain in the global DB (already initialized).
    #   But the session table for THIS instance uses the instance data-dir
    #   so session state doesn't bleed across instances.
    if _is_child_instance and OPERATOR_MANAGER_AVAILABLE:
        import operator_session_manager as _osm_mod
        instance_db = os.path.join(data_dir, 'operator_sessions.db')
        os.environ['OPERATOR_SESSIONS_DB_PATH'] = instance_db
        os.environ['OP_SESSION_DB_PATH'] = instance_db
        global_operator_manager = globals().get('operator_manager')
        _osm_mod._session_manager = _osm_mod.OperatorSessionManager(
            db_path=instance_db,
            internal_token=args.internal_token,
        )
        if global_operator_manager and getattr(global_operator_manager, 'operators', None):
            _osm_mod._session_manager.operators.update(global_operator_manager.operators)
        # Re-bind the module-level reference so all routes use the new manager
        globals()['operator_manager'] = _osm_mod._session_manager
        logger.info(f"Instance mode: operator session DB re-scoped to {instance_db}")
        logger.info(f"Environment override set: OPERATOR_SESSIONS_DB_PATH={instance_db}")


    # Fix: Allow child instances to load their own snapshot, only skip recon rehydration
    if _is_child_instance:
        if '_deferred_load_snapshot' in globals():
            _deferred_load_snapshot()
        logger.info(f"Instance mode: snapshot loaded, skipping recon rehydration (instance={instance_id}, data_dir={data_dir})")
    else:
        # Parent / standalone — full rehydration
        if '_deferred_load_snapshot' in globals():
            _deferred_load_snapshot()
        if '_deferred_rehydrate' in globals():
            _deferred_rehydrate()
        logger.info("Standalone mode: rehydration complete")

    # Always start snapshot persistence (saves INTO the correct data-dir)
    if '_start_snapshot_persistence' in globals():
        _start_snapshot_persistence()

    # Generate initial test data if requested
    if args.generate_test:
        logger.info("Generating initial test data...")
        hypergraph_store.generate_test_data(20, 88.0, 108.0, 1000.0)

    # Register instance-level status endpoint
    @app.route('/api/instance/info', methods=['GET'])
    def instance_info():
        """Return identity + summary metrics for this SCYTHE instance."""
        hg = None
        try:
            hg = _get_engine()
        except Exception:
            pass
        node_count = len(hg.nodes) if hg and hasattr(hg, 'nodes') and hg.nodes else 0
        edge_count = len(hg.edges) if hg and hasattr(hg, 'edges') and hg.edges else 0
        # Count BSGs
        bsg_count = 0
        session_count = 0
        if hg and hasattr(hg, 'nodes') and hg.nodes:
            try:
                for _, nd in hg.nodes.items():
                    d = _safe_nd(nd)
                    k = d.get('kind', '')
                    if k == 'behavior_group':
                        bsg_count += 1
                    elif k in ('session', 'pcap_session'):
                        session_count += 1
            except Exception as cnt_err:
                # Best-effort counting only; never let this crash the instance
                logger.warning(f'[instance_info] count failed: {cnt_err}')
        return jsonify({
            'ok': True,
            'instance_id': app.config.get('SCYTHE_INSTANCE_ID', 'unknown'),
            'port': app.config.get('SCYTHE_PORT', 0),
            'node_count': node_count,
            'edge_count': edge_count,
            'session_count': session_count,
            'behavior_groups': bsg_count,
            'uptime_seconds': time.monotonic() - _instance_boot_time,
        })

    _instance_boot_time = time.monotonic()

    # Register with orchestrator if specified
    if args.orchestrator_url:
        try:
            import urllib.request
            reg_data = json.dumps({
                'instance_id': instance_id,
                'port': args.port,
                'host': args.host,
            }).encode()
            req = urllib.request.Request(
                f'{args.orchestrator_url}/api/scythe/instances/register',
                data=reg_data,
                headers={'Content-Type': 'application/json'},
                method='POST',
            )
            urllib.request.urlopen(req, timeout=3)
            logger.info(f'Registered with orchestrator at {args.orchestrator_url}')
        except Exception as e:
            logger.warning(f'Could not register with orchestrator: {e}')

        # Probe thread: waits until /api/health responds THEN tells the orchestrator
        # that Socket.IO is accepting connections.  Runs AFTER socketio.run() starts.
        def _ready_prober(orch_url: str, iid: str, port: int) -> None:
            import urllib.request, urllib.error
            for _ in range(40):  # up to 20 s (40 × 0.5 s)
                time.sleep(0.5)
                try:
                    urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health', timeout=1)
                    data = json.dumps({
                        'instance_id':    iid,
                        'port':           port,
                        'host':           args.host,
                        'socket_io_ready': True,
                    }).encode()
                    req = urllib.request.Request(
                        f'{orch_url}/api/scythe/instances/register',
                        data=data,
                        headers={'Content-Type': 'application/json'},
                        method='POST',
                    )
                    urllib.request.urlopen(req, timeout=3)
                    logger.info('[ready-probe] Socket.IO ready — updated orchestrator')
                    return
                except Exception:
                    pass
            logger.warning('[ready-probe] Server did not become healthy within 20 s')

        t = threading.Thread(
            target=_ready_prober,
            args=(args.orchestrator_url, instance_id, args.port),
            daemon=True, name='scythe-ready-probe',
        )
        t.start()

        # Heartbeat thread: re-register every 5 s so the orchestrator knows
        # the instance is still alive.
        def _orch_heartbeat(orch_url: str, iid: str, port: int) -> None:
            import urllib.request
            while True:
                time.sleep(5)
                try:
                    data = json.dumps({
                        'instance_id':    iid,
                        'port':           port,
                        'host':           args.host,
                        'socket_io_ready': True,
                    }).encode()
                    req = urllib.request.Request(
                        f'{orch_url}/api/scythe/instances/register',
                        data=data,
                        headers={'Content-Type': 'application/json'},
                        method='POST',
                    )
                    urllib.request.urlopen(req, timeout=2)
                except Exception:
                    pass  # orchestrator down — keep running, retry next cycle

        threading.Thread(
            target=_orch_heartbeat,
            args=(args.orchestrator_url, instance_id, args.port),
            daemon=True, name='scythe-orch-heartbeat',
        ).start()

    # Check for available tools
    nmap_available = nmap_scanner.check_nmap_available()
    ndpi_available = ndpi_analyzer.check_ndpi_available()
    ais_loaded = ais_tracker.csv_loaded
    ais_vessels = len(ais_tracker.vessels)
    recon_entities = len(recon_system.entities)
    recon_alerts = len(recon_system.get_proximity_alerts())

    # Check room system
    room_count = len(operator_manager.rooms) if operator_manager else 0

    # ========================================================================
    # AISSTREAM WEBSOCKET CLIENT
    # ========================================================================

    import asyncio
    try:
        import websockets
        WEBSOCKETS_AVAILABLE = True
    except ImportError:
        WEBSOCKETS_AVAILABLE = False
        logger.warning("websockets not available - AISStream disabled. Install with: pip install websockets")

    async def connect_aisstream():
        """Connect to AISStream.io and forward vessel updates via SocketIO"""
        global aisstream_active

        if not WEBSOCKETS_AVAILABLE:
            logger.error("websockets library not available")
            return

        API_KEY = os.environ.get("AISSTREAM_API_KEY", "")
        if not API_KEY:
            logger.warning("AISSTREAM_API_KEY not set - AISStream disabled")
            return

        # exponential reconnect delay (seconds)
        reconnect_delay = 1

        while aisstream_active:
            try:
                # Enable ping/pong keepalive and reasonable close timeout to detect half-open sockets
                async with websockets.connect(
                    "wss://stream.aisstream.io/v0/stream",
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                    max_size=None
                ) as websocket:
                    logger.info("[AISStream] Connected to stream")
                    # Reset reconnect delay on successful connect
                    reconnect_delay = 1

                    # Subscribe with bounding box (or use current if set)
                    bbox = aisstream_bounding_box or [[[-180, -90], [180, 90]]]  # Global by default
                    subscribe_message = {
                        "APIKey": API_KEY,
                        "BoundingBoxes": bbox,
                        "FilterMessageTypes": ["PositionReport", "StaticDataReport"]  # Include vessel info
                    }
                    await websocket.send(json.dumps(subscribe_message))

                    async for message_json in websocket:
                        message = json.loads(message_json)
                        message_type = message.get("MessageType")

                        if message_type == "PositionReport":
                            ais_msg = message['Message']['PositionReport']
                            vessel_data = {
                                'mmsi': ais_msg.get('UserID'),
                                'lat': ais_msg.get('Latitude'),
                                'lon': ais_msg.get('Longitude'),
                                'speed': ais_msg.get('Sog'),  # Speed over ground
                                'course': ais_msg.get('Cog'),  # Course over ground
                                'heading': ais_msg.get('TrueHeading'),
                                'timestamp': datetime.now(timezone.utc).isoformat()
                            }

                            # Update local AIS tracker
                            ais_tracker.update_vessel(vessel_data['mmsi'], vessel_data)

                            # Broadcast via SocketIO if available
                            if socketio:
                                socketio.emit('ais_update', vessel_data, broadcast=True)

                            logger.debug(f"[AISStream] Position update: Vessel {vessel_data['mmsi']} @ {vessel_data['lat']},{vessel_data['lon']}")

                        elif message_type == "StaticDataReport":
                            ais_msg = message['Message']['StaticDataReport']
                            mmsi = ais_msg.get('UserID')

                            # Extract vessel type from AIS type code
                            ais_type_code = ais_msg.get('Type', 0)
                            vessel_type = ais_tracker._decode_vessel_type(ais_type_code)

                            vessel_data = {
                                'mmsi': mmsi,
                                'name': ais_msg.get('Name', '').strip() or f'MMSI_{mmsi}',
                                'vessel_type': vessel_type,
                                'length': ais_msg.get('Length', 0),
                                'width': ais_msg.get('Width', 0),
                                'draft': ais_msg.get('MaximumStaticDraught', 0),
                                'timestamp': datetime.now(timezone.utc).isoformat()
                            }

                            # Update local AIS tracker with vessel info
                            ais_tracker.update_vessel(mmsi, vessel_data)

                            logger.debug(f"[AISStream] Static data: Vessel {mmsi} ({vessel_type}) - {vessel_data['name']}")

                        # Note: Could also handle other message types like BinaryBroadcast for additional data

            except Exception as e:
                # Prefer structured handling for websockets close events so logs are actionable
                try:
                    from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError
                except Exception:
                    ConnectionClosedOK = ConnectionClosedError = None

                if ConnectionClosedOK and isinstance(e, ConnectionClosedOK):
                    logger.info(f"[AISStream] Connection closed cleanly: {e}")
                elif ConnectionClosedError and isinstance(e, ConnectionClosedError):
                    # Often remote servers will drop connections without a close frame; log as warning
                    logger.warning(f"[AISStream] Connection closed with error: code={getattr(e, 'code', None)} reason={getattr(e, 'reason', None)} - {e}")
                else:
                    # Unknown exception - log stack for debugging
                    logger.exception(f"[AISStream] Connection error: {e}")

                # Exponential backoff for reconnects (cap at 60s)
                if aisstream_active:
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, 60)

    def start_aisstream():
        """Start AISStream in background thread"""
        global aisstream_active, aisstream_thread

        if not WEBSOCKETS_AVAILABLE:
            logger.warning("[AISStream] Not starting - websockets library not installed")
            return

        # Guard against duplicate starts — only one AIS thread at a time
        if aisstream_thread is not None and aisstream_thread.is_alive():
            logger.info("[AISStream] Already running — skipping duplicate start")
            return

        aisstream_active = True

        def run_async_loop():
            try:
                asyncio.run(connect_aisstream())
            except Exception as e:
                logger.exception(f"[AISStream] Event loop exited: {e}")

        aisstream_thread = threading.Thread(target=run_async_loop, daemon=True)
        aisstream_thread.start()
        logger.info("[AISStream] Started in background thread")

    def stop_aisstream():
        """Stop AISStream"""
        global aisstream_active
        aisstream_active = False
        logger.info("[AISStream] Stopped")

    # API endpoint to control AISStream
    @app.route('/api/ais/stream/start', methods=['POST'])
    def start_ais_stream():
        """Start AISStream with optional bounding box"""
        global aisstream_bounding_box

        data = request.get_json() or {}
        bbox = data.get('bounding_box')

        if bbox:
            aisstream_bounding_box = bbox
            logger.info(f"[AISStream] Bounding box updated: {bbox}")

        start_aisstream()
        return jsonify({'status': 'started', 'bounding_box': aisstream_bounding_box})

    @app.route('/api/ais/stream/stop', methods=['POST'])
    def stop_ais_stream():
        """Stop AISStream"""
        stop_aisstream()
        return jsonify({'status': 'stopped'})

    @app.route('/api/ais/stream/status', methods=['GET'])
    def ais_stream_status():
        """Get AISStream status"""
        return jsonify({
            'active': aisstream_active,
            'bounding_box': aisstream_bounding_box,
            'vessel_count': len(ais_tracker.vessels)
        })

    # ========================================================================

    logger.info(f"nmap available: {nmap_available}")
    logger.info(f"nDPI available: {ndpi_available}")
    logger.info(f"AIS loaded: {ais_loaded}, vessels: {ais_vessels}")
    logger.info(f"Recon system: {recon_entities} entities, {recon_alerts} alerts")
    logger.info(f"Rooms: {room_count} active, WebSocket: {SOCKETIO_AVAILABLE}")

    # Print startup info
    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║         RF SCYTHE Integrated API Server v1.3.0                   ║
║         Instance: {instance_id:<44}║
╠══════════════════════════════════════════════════════════════════╣
║  🌐 Server: http://{args.host}:{args.port}
║  📡 Console: http://localhost:{args.port}/command-ops-visualization.html
║                                                                  ║
║  API Endpoints:                                                  ║
║    /api/rf-hypergraph/*    - Hypergraph visualization            ║
║    /api/infer/*            - Inference (Python + OWL-RL + Gemma) ║
║    /api/tak-ml/*           - TAK-ML model-assisted enrichment    ║
║    /api/tak-gpt/*          - GraphOps chat bot (Ollama)          ║
║    /api/mcp/snapshot       - MCP context envelope (debug)        ║
║    /api/inference/history  - Inference run history (time-series) ║
║    /mcp                    - MCP JSON-RPC 2.0 endpoint           ║
║    /api/tak/*              - TAK CoT export + send               ║
║    /api/geo/heatmap*       - Probability heatmaps (LandSAR)     ║
║    /api/nmap/*             - Network scanning                    ║
║    /api/ndpi/*             - Deep packet inspection              ║
║    /api/ais/*              - AIS vessel tracking                 ║
║    /api/recon/*            - Auto-reconnaissance system          ║
║    /api/rooms/*            - Room/Channel management             ║
║    /api/status             - System status                       ║
║                                                                  ║
║  nmap:      {'✅ Available' if nmap_available else '❌ Not installed'}
║  nDPI:      {'✅ Available' if ndpi_available else '❌ Not installed'}
║  AIS:       {'✅ Loaded (' + str(ais_vessels) + ' vessels)' if ais_loaded else '❌ No data'}
║  Recon:     ✅ Active ({recon_entities} entities)
║  Rooms:     ✅ Active ({room_count} rooms)
║  WebSocket: {'✅ Enabled' if SOCKETIO_AVAILABLE else '❌ SSE only (pip install flask-socketio)'}
╚══════════════════════════════════════════════════════════════════╝
    """)
    # Start background satellite refresh if requested
    if args.satellite_refresh:
        try:
            start_satellite_refresh(interval_seconds=300, categories=['starlink','visual','active'])
            logger.info('Satellite refresh background thread started (300s interval)')
        except Exception as e:
            logger.warning(f'Could not start satellite refresh thread: {e}')
    else:
        logger.info('Satellite refresh disabled (use --satellite-refresh to enable)')

    # Register MCP JSON-RPC endpoint on the Flask app (same port, /mcp path)
    try:
        from mcp_server import register_mcp_routes

        eng = globals().get('hypergraph_engine')
        if eng is not None:
            mcp_handler = register_mcp_routes(app, eng)
            logger.info('MCP server registered at /mcp (%d tools, %d resources)',
                        len(mcp_handler._tools), len(mcp_handler._resources))

            # Initialize semantic memory layer and register embedding MCP tools
            try:
                from embedding_engine import EmbeddingEngine, register_embedding_tools
                from graphops_copilot import register_graphops_tools, GraphOpsAgent

                _embed_engine = EmbeddingEngine(
                    ollama_url=_DEFAULT_OLLAMA_URL,
                    db_path=os.path.join(_data_dir(), 'embedding_store.duckdb'),
                    index_path=os.path.join(_data_dir(), 'embedding_index.faiss'),
                    instance_db=globals().get('instance_db'),
                    tier='analytical'
                )
                globals()['embedding_engine'] = _embed_engine

                # ── Wire Persistent Cognitive Substrate ──
                try:
                    from recon_enrichment import configure_wifi_enricher
                    configure_wifi_enricher(
                        instance_db=globals().get('instance_db'),
                        embedding_engine=_embed_engine
                    )
                except Exception as wire_err:
                    logger.warning('Could not wire cognitive substrate: %s', wire_err)

                register_embedding_tools(eng, mcp_handler, _embed_engine)
                logger.info('Semantic memory registered (%d vectors, model=%s)',
                            _embed_engine.stats()['total_vectors'], _embed_engine._model)

                # Wire embedding engine into the GraphOps agent used by MCP tools
                register_graphops_tools(eng, mcp_handler, embedding_engine=_embed_engine)
            except Exception as emb_err:
                logger.warning('Could not init EmbeddingEngine: %s', emb_err)
        else:
            logger.info('Skipping MCP server — no HypergraphEngine available')
    except Exception as e:
        logger.warning('Could not register MCP routes: %s', e)

    # Register this instance via mDNS so Android clients can discover it
    def _register_mdns_instance(port, instance_id):
        try:
            from zeroconf import Zeroconf, ServiceInfo
            import socket as _socket
            import logging as _logging

            # Silence zeroconf socket noise (unreachable interfaces, mDNS multicast errors)
            _logging.getLogger('zeroconf').setLevel(_logging.ERROR)

            try:
                local_ip = _socket.gethostbyname(_socket.gethostname())
                if local_ip.startswith('127.'):
                    # Fallback: find a non-loopback address via routing table
                    s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
                    s.connect(('8.8.8.8', 80))
                    local_ip = s.getsockname()[0]
                    s.close()
            except Exception:
                local_ip = '0.0.0.0'

            if local_ip == '0.0.0.0':
                logger.warning('[mDNS] Could not determine local IP — skipping mDNS registration')
                return None

            # Bind only to the resolved interface (not all interfaces) to prevent
            # "Network is unreachable" errors on VPN/tunnel interfaces like 10.2.0.2
            zc = Zeroconf(interfaces=[local_ip])
            info = ServiceInfo(
                "_scythe._tcp.local.",
                f"ScytheInstance-{instance_id}._scythe._tcp.local.",
                addresses=[_socket.inet_aton(local_ip)],
                port=port,
                properties={
                    b'type': b'instance',
                    b'instance_id': instance_id.encode(),
                    b'version': b'1.0',
                },
            )
            zc.register_service(info)
            import atexit
            atexit.register(lambda: (zc.unregister_service(info), zc.close()))
            logger.info(f'[mDNS] Registered ScytheInstance-{instance_id}._scythe._tcp.local on {local_ip}:{port}')
            return zc
        except ImportError:
            logger.warning('[mDNS] zeroconf not installed — pip install zeroconf')
        except Exception as e:
            logger.warning(f'[mDNS] Registration failed: {e}')
        return None

    _zc_instance = _register_mdns_instance(args.port, instance_id)

    # Run the server with WebSocket support if available
    if SOCKETIO_AVAILABLE and socketio:
        socketio.run(app, host=args.host, port=args.port, debug=args.debug, allow_unsafe_werkzeug=True)
    else:
        app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


if __name__ == '__main__':
    main()
