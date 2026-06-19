#!/usr/bin/env python3
"""
SCYTHE gRPC Server
==================
Standalone subprocess launched by scythe_orchestrator.py at boot.

Architecture:
  Browser
    ↓ gRPC-Web (Authorization: Bearer <token>)
  Envoy :8080
    ↓ native gRPC H2 (authorization: bearer <token>)
  scythe_grpc_server.py :50051
    ├── OrchestratorService  → proxies to orchestrator REST (X-Internal-Token)
    ├── HypergraphService    → proxies to instance REST (X-Internal-Token)
    └── ClusterIntelService  → proxies to instance REST (X-Internal-Token)

Auth flow:
  1. Browser POSTs to /api/operator/login on an instance → gets session_token
  2. Instance calls orchestrator POST /api/scythe/sessions/register
  3. Orchestrator stores token → {instance_id, operator_id, expires_at}
  4. Browser sends Authorization: Bearer <token> in gRPC metadata
  5. TokenAuthInterceptor validates via GET /api/scythe/sessions/validate
  6. All internal proxy calls use X-Internal-Token (shared secret, never the user token)

Usage:
  python3 scythe_grpc_server.py \\
    --grpc-port 50051 \\
    --orchestrator-url http://127.0.0.1:5001 \\
    --internal-token <hex32>
"""
from __future__ import annotations

import argparse
import json
import logging
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Optional

import grpc
import requests

import scythe_pb2
import scythe_pb2_grpc

log = logging.getLogger('scythe.grpc')

# ---------------------------------------------------------------------------
# Token validation cache
# ---------------------------------------------------------------------------
_TOKEN_CACHE: dict[str, tuple[dict, float]] = {}   # token → (session_info, expiry_ts)
_TOKEN_CACHE_TTL = 5.0  # seconds — short window limits post-revocation exposure
_TOKEN_CACHE_LOCK = threading.Lock()


def _cached_validate(token: str, orchestrator_url: str, internal_token: str) -> Optional[dict]:
    """Validate a Bearer token against the orchestrator's shared session registry.

    Results are cached up to TOKEN_CACHE_TTL seconds (or until the session's own
    expires_at is reached, whichever is sooner) to limit loopback HTTP overhead.
    Token is sent in X-Validate-Token header — never in a URL query string.
    Returns the session dict on success, or None if invalid/expired.
    """
    now = time.monotonic()
    with _TOKEN_CACHE_LOCK:
        if token in _TOKEN_CACHE:
            info, expiry = _TOKEN_CACHE[token]
            if now < expiry:
                return info
            del _TOKEN_CACHE[token]

    try:
        r = requests.get(
            f'{orchestrator_url}/api/scythe/sessions/validate',
            headers={
                'X-Internal-Token': internal_token,
                'X-Validate-Token': token,   # ← never in URL (avoids access-log leakage)
            },
            timeout=1.0,
        )
        if r.status_code == 200:
            info = r.json()
            # Respect session's own expires_at so we never cache past true expiry
            cache_ttl = _TOKEN_CACHE_TTL
            exp_str = info.get('expires_at', '')
            if exp_str:
                try:
                    exp_dt = datetime.fromisoformat(exp_str.replace('Z', '+00:00'))
                    secs_left = (exp_dt - datetime.now(timezone.utc)).total_seconds()
                    cache_ttl = min(cache_ttl, max(0.0, secs_left))
                except (ValueError, TypeError):
                    pass
            with _TOKEN_CACHE_LOCK:
                _TOKEN_CACHE[token] = (info, now + cache_ttl)
            return info
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# TokenAuthInterceptor
# ---------------------------------------------------------------------------

# RPCs that must be reachable without a prior session token.
# AuthService.Login  — the login call itself (issues the token)
# OperatorStream.Connect — bidi stream that starts anonymous, upgrades in-place
_BYPASS_METHODS = frozenset([
    '/scythe.v1.AuthService/Login',
    '/scythe.v1.OperatorStream/Connect',
])


class TokenAuthInterceptor(grpc.ServerInterceptor):
    """Validate `authorization: Bearer <token>` metadata on every inbound RPC.

    Methods in _BYPASS_METHODS are passed through without auth; they handle
    identity verification internally (login endpoint, bidi upgrade stream).
    """

    def __init__(self, orchestrator_url: str, internal_token: str) -> None:
        self._orch = orchestrator_url
        self._internal = internal_token

    def intercept_service(self, continuation, handler_call_details):
        # Determine the real handler first — needed for correct abort cardinality.
        real_handler = continuation(handler_call_details)

        # Pass bypass methods through unconditionally.
        if handler_call_details.method in _BYPASS_METHODS:
            return real_handler

        metadata = dict(handler_call_details.invocation_metadata)
        auth_header = metadata.get('authorization', '')

        # Case-insensitive per RFC 7235: "Bearer", "bearer", "BEARER" all valid
        lower = auth_header.lower()
        if lower.startswith('bearer '):
            token = auth_header[7:].strip()
        else:
            token = auth_header.strip()

        def _make_abort(msg: str):
            """Return an abort handler with the correct RPC cardinality."""
            req_stream = real_handler and real_handler.request_streaming
            res_stream = real_handler and real_handler.response_streaming

            def _abort_bidi(req_iter, ctx):
                ctx.abort(grpc.StatusCode.UNAUTHENTICATED, msg)
                return
                yield  # make generator for bidi/server-stream

            def _abort_server_stream(req, ctx):
                ctx.abort(grpc.StatusCode.UNAUTHENTICATED, msg)
                return
                yield

            def _abort_unary(req, ctx):
                ctx.abort(grpc.StatusCode.UNAUTHENTICATED, msg)

            if req_stream and res_stream:
                return grpc.stream_stream_rpc_method_handler(_abort_bidi)
            if res_stream:
                return grpc.unary_stream_rpc_method_handler(_abort_server_stream)
            if req_stream:
                return grpc.stream_unary_rpc_method_handler(
                    lambda req_iter, ctx: _abort_unary(None, ctx)
                )
            return grpc.unary_unary_rpc_method_handler(_abort_unary)

        if not token:
            return _make_abort('Missing authorization header')

        session = _cached_validate(token, self._orch, self._internal)
        if session is None:
            return _make_abort('Invalid or expired session token')

        return real_handler


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
class _BaseServicer:
    def __init__(self, orchestrator_url: str, internal_token: str) -> None:
        self._orch = orchestrator_url
        self._tok = internal_token
        self._instance_cache: dict[str, tuple[str, float]] = {}  # id → (url, expiry)
        self._instance_cache_lock = threading.Lock()

    # HTTP status → gRPC status code mapping
    _HTTP_TO_GRPC = {
        400: grpc.StatusCode.INVALID_ARGUMENT,
        403: grpc.StatusCode.PERMISSION_DENIED,
        404: grpc.StatusCode.NOT_FOUND,
        429: grpc.StatusCode.RESOURCE_EXHAUSTED,
        500: grpc.StatusCode.INTERNAL,
        503: grpc.StatusCode.UNAVAILABLE,
        504: grpc.StatusCode.DEADLINE_EXCEEDED,
    }

    def _check_http(self, r: requests.Response, context) -> bool:
        """Abort the RPC with a mapped gRPC code if the HTTP response is not 200.
        Returns True if the response is OK, False if aborted."""
        if r.status_code == 200:
            return True
        code = self._HTTP_TO_GRPC.get(r.status_code, grpc.StatusCode.UNKNOWN)
        try:
            msg = r.json().get('message') or r.text[:300]
        except Exception:
            msg = r.text[:300]
        context.abort(code, msg or f'HTTP {r.status_code}')
        return False

    def _internal_headers(self) -> dict:
        return {'X-Internal-Token': self._tok}

    def _instance_url(self, instance_id: str) -> Optional[str]:
        """Resolve instance_id → loopback URL, with 10 s cache."""
        now = time.monotonic()
        with self._instance_cache_lock:
            if instance_id in self._instance_cache:
                url, expiry = self._instance_cache[instance_id]
                if now < expiry:
                    return url

        try:
            r = requests.get(
                f'{self._orch}/api/scythe/instances',
                headers=self._internal_headers(),
                timeout=2.0,
            )
            r.raise_for_status()
            for inst in r.json().get('instances', []):
                if inst.get('id') == instance_id:
                    url = f"http://127.0.0.1:{inst['port']}"
                    with self._instance_cache_lock:
                        self._instance_cache[instance_id] = (url, now + 10.0)
                    return url
        except Exception:
            pass
        return None

    def _session_for_request(self, context) -> Optional[dict]:
        """Read the cached session for the current RPC's Bearer token (no round-trip)."""
        metadata = dict(context.invocation_metadata())
        auth = metadata.get('authorization', '')
        token = auth[7:].strip() if auth.lower().startswith('bearer ') else auth.strip()
        if not token:
            return None
        with _TOKEN_CACHE_LOCK:
            entry = _TOKEN_CACHE.get(token)
            if entry:
                info, expiry = entry
                if time.monotonic() < expiry:
                    return info
        return None

    def _check_instance_auth(self, request_instance_id: str, context) -> bool:
        """Enforce that the session's instance_id matches the requested one.
        Returns True if authorized (or no instance restriction on the session)."""
        session = self._session_for_request(context)
        if session is None:
            return True  # --no-auth dev mode or interceptor already validated
        bound_id = session.get('instance_id', '')
        if bound_id and bound_id != request_instance_id:
            context.abort(
                grpc.StatusCode.PERMISSION_DENIED,
                f'Session bound to instance {bound_id!r}, not {request_instance_id!r}',
            )
            return False
        return True


# ---------------------------------------------------------------------------
# OrchestratorServicer
# ---------------------------------------------------------------------------
class OrchestratorServicer(_BaseServicer, scythe_pb2_grpc.OrchestratorServiceServicer):

    def ListInstances(self, request, context):
        try:
            r = requests.get(
                f'{self._orch}/api/scythe/instances',
                headers=self._internal_headers(),
                timeout=3.0,
            )
            if not self._check_http(r, context):
                return
            data = r.json()
        except Exception as exc:
            context.abort(grpc.StatusCode.UNAVAILABLE, f'Orchestrator unreachable: {exc}')
            return

        instances = [
            scythe_pb2.InstanceInfo(
                instance_id=i.get('id', ''),
                port=i.get('port', 0),
                pid=i.get('pid') or 0,
                status=i.get('status', 'unknown'),
                started_at=i.get('created', ''),
                name=i.get('name', ''),
            )
            for i in data.get('instances', [])
        ]
        return scythe_pb2.ListInstancesResponse(
            instances=instances,
            count=len(instances),
            orchestrator_uptime_s=data.get('orchestrator_uptime', 0.0),
        )

    def DeployObserver(self, request, context):
        try:
            r = requests.post(
                f'{self._orch}/api/scythe/instances/new',
                json={'lat': request.lat, 'lon': request.lon, 'region': request.region},
                headers=self._internal_headers(),
                timeout=10.0,
            )
            if not self._check_http(r, context):
                return
            data = r.json()
        except Exception as exc:
            context.abort(grpc.StatusCode.UNAVAILABLE, f'Orchestrator unreachable: {exc}')
            return
        return scythe_pb2.DeployResponse(
            instance_id=data.get('instance_id', ''),
            status=data.get('status', ''),
            port=data.get('port', 0),
        )

    def TerminateInstance(self, request, context):
        try:
            r = requests.delete(
                f'{self._orch}/api/scythe/instances/{request.instance_id}',
                headers=self._internal_headers(),
                timeout=5.0,
            )
            if not self._check_http(r, context):
                return
            data = r.json()
        except Exception as exc:
            context.abort(grpc.StatusCode.UNAVAILABLE, f'Orchestrator unreachable: {exc}')
            return
        return scythe_pb2.TerminateResponse(status=data.get('status', 'ok'))

    def GetHealth(self, request, context):
        try:
            r = requests.get(
                f'{self._orch}/api/scythe/health',
                headers=self._internal_headers(),
                timeout=2.0,
            )
            if not self._check_http(r, context):
                return
            data = r.json()
        except Exception as exc:
            context.abort(grpc.StatusCode.UNAVAILABLE, f'Orchestrator unreachable: {exc}')
            return
        return scythe_pb2.HealthResponse(
            status=data.get('status', 'ok'),
            instance_count=data.get('running_instances', 0),
            version=data.get('version', ''),
            uptime_s=data.get('uptime_s', 0.0),
        )

    def StreamInstanceUpdates(self, request, context):
        prev_states: dict[str, str] = {}
        filter_id = request.instance_id or ''   # empty = stream all instances
        _fail = 0
        while context.is_active():
            try:
                r = requests.get(
                    f'{self._orch}/api/scythe/instances',
                    headers=self._internal_headers(),
                    timeout=2.0,
                )
                r.raise_for_status()
                _fail = 0
                for inst in r.json().get('instances', []):
                    iid = inst.get('id', '')
                    if filter_id and iid != filter_id:
                        continue
                    status = inst.get('status', '')
                    if not request.changes_only or prev_states.get(iid) != status:
                        prev_states[iid] = status
                        yield scythe_pb2.InstanceUpdate(
                            instance_id=iid,
                            status=status,
                            port=inst.get('port', 0),
                            timestamp=inst.get('created', ''),
                        )
            except Exception:
                _fail += 1
                if _fail >= 5:
                    context.abort(grpc.StatusCode.UNAVAILABLE, 'Orchestrator unreachable')
                    return
            time.sleep(5.0)


# ---------------------------------------------------------------------------
# HypergraphServicer
# ---------------------------------------------------------------------------
class HypergraphServicer(_BaseServicer, scythe_pb2_grpc.HypergraphServiceServicer):

    def GetSnapshot(self, request, context):
        if not self._check_instance_auth(request.instance_id, context):
            return

        instance_url = self._instance_url(request.instance_id)
        if not instance_url:
            context.abort(grpc.StatusCode.NOT_FOUND, f'Instance {request.instance_id!r} not found')
            return

        try:
            r = requests.get(
                f'{instance_url}/api/gravity/export',
                params={'format': 'json'},
                headers=self._internal_headers(),
                timeout=10.0,
            )
            if not self._check_http(r, context):
                return
            data = r.json()
        except Exception as exc:
            context.abort(grpc.StatusCode.UNAVAILABLE, f'Instance unreachable: {exc}')
            return

        nodes = [
            scythe_pb2.HypergraphNode(
                id=n.get('id', ''),
                lat=float(n.get('lat', 0.0)),
                lon=float(n.get('lon', 0.0)),
                anomaly=float(n.get('anomaly', 0.0)),
                mass=float(n.get('mass', 0.0)),
                degree=int(n.get('degree', 0)),
                label=n.get('label', ''),
                threat=float(n.get('threat', 0.0)),
            )
            for n in data.get('nodes', [])
        ]
        raw_edges = data.get('edges', [])
        edges = []
        for e in raw_edges:
            if isinstance(e, (list, tuple)) and len(e) >= 2:
                edges.append(scythe_pb2.HypergraphEdge(
                    src_idx=int(e[0]),
                    dst_idx=int(e[1]),
                    kind=str(e[2]) if len(e) > 2 else '',
                    confidence=float(e[3]) if len(e) > 3 else 1.0,
                ))

        return scythe_pb2.HypergraphSnapshot(
            nodes=nodes,
            edges=edges,
            timestamp_ms=int(data.get('timestamp_ms', 0)),
            total_nodes=len(nodes),
            total_edges=len(edges),
        )

    def StreamGraphDeltas(self, request, context):
        if not self._check_instance_auth(request.instance_id, context):
            return

        instance_url = self._instance_url(request.instance_id)
        if not instance_url:
            context.abort(grpc.StatusCode.NOT_FOUND, f'Instance {request.instance_id!r} not found')
            return

        seq = request.since_seq
        _fail = 0
        while context.is_active():
            try:
                r = requests.get(
                    f'{instance_url}/api/hypergraph/events/since',
                    params={'seq': seq},
                    headers=self._internal_headers(),
                    timeout=3.0,
                )
                r.raise_for_status()
                _fail = 0
                for event in r.json().get('events', []):
                    seq = max(seq, int(event.get('seq', seq)))
                    yield scythe_pb2.GraphDelta(
                        op=event.get('op', ''),
                        node_id=event.get('node_id', ''),
                        edge_src=event.get('src', ''),
                        edge_dst=event.get('dst', ''),
                        anomaly_score=float(event.get('anomaly', 0.0)),
                        threat_level=float(event.get('threat', 0.0)),
                        seq=int(event.get('seq', 0)),
                        timestamp_ms=int(event.get('ts', 0)),
                    )
            except Exception:
                _fail += 1
                if _fail >= 5:
                    context.abort(grpc.StatusCode.UNAVAILABLE, f'Instance {request.instance_id!r} unreachable')
                    return
            time.sleep(5.0)


# ---------------------------------------------------------------------------
# ClusterIntelServicer
# ---------------------------------------------------------------------------
class ClusterIntelServicer(_BaseServicer, scythe_pb2_grpc.ClusterIntelServiceServicer):

    def _split_cluster_id(self, combined: str, context) -> tuple[str, str]:
        """Split '<instance_id>/<cluster_id>', abort on bad format."""
        parts = combined.split('/', 1)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                'cluster_id must be "<instance_id>/<cluster_id>"',
            )
            return '', ''
        return parts[0], parts[1]

    def DecomposeCluster(self, request, context):
        instance_id, cluster_id = self._split_cluster_id(request.cluster_id, context)
        if not instance_id:
            return

        if not self._check_instance_auth(instance_id, context):
            return

        instance_url = self._instance_url(instance_id)
        if not instance_url:
            context.abort(grpc.StatusCode.NOT_FOUND, f'Instance {instance_id!r} not found')
            return

        try:
            r = requests.get(
                f'{instance_url}/api/clusters/export-data/{cluster_id}',
                headers=self._internal_headers(),
                timeout=5.0,
            )
            if not self._check_http(r, context):
                return
            data = r.json()
        except Exception as exc:
            context.abort(grpc.StatusCode.UNAVAILABLE, f'Instance unreachable: {exc}')
            return

        meta = data.get('metadata', {})   # REST returns 'metadata', not 'cluster_meta'
        decomp = data.get('decomposition', {})
        return scythe_pb2.ClusterDecomposition(
            cluster_id=cluster_id,
            archetype=meta.get('archetype', ''),
            silence_pressure=float(meta.get('silence_pressure', 0.0)),
            node_tier=meta.get('node_tier', ''),
            dimensional_density=float(meta.get('dimensional_density', 0.0)),
            intent_scores=[
                scythe_pb2.IntentScore(
                    label=item.get('label', ''),
                    probability=float(item.get('score', 0.0)),
                )
                for item in (meta.get('intent_scores') or [])
                if isinstance(item, dict)
            ],
            behavior_summary=decomp.get('behavior_summary', ''),
            timestamp_ms=int(data.get('timestamp_ms', 0)),
            node_count=int(meta.get('node_count', 0)),
            temporal_activity=float(meta.get('activity_score', 0.0)),
            asn_entropy=float(meta.get('asn_diversity', 0.0)),
            signal_coherence=float(meta.get('phase_coherence', 0.0)),
        )

    def StreamAutopsy(self, request, context):
        instance_id, cluster_id = self._split_cluster_id(request.cluster_id, context)
        if not instance_id:
            return

        if not self._check_instance_auth(instance_id, context):
            return

        instance_url = self._instance_url(instance_id)
        if not instance_url:
            context.abort(grpc.StatusCode.NOT_FOUND, f'Instance {instance_id!r} not found')
            return

        _fail = 0
        while context.is_active():
            try:
                r = requests.get(
                    f'{instance_url}/api/clusters/export-data/{cluster_id}',
                    headers=self._internal_headers(),
                    timeout=5.0,
                )
                r.raise_for_status()
                _fail = 0
                data = r.json()
                meta = data.get('metadata', {})   # REST returns 'metadata', not 'cluster_meta'
                yield scythe_pb2.AutopsyEvent(
                    event_type='intent_update',
                    cluster_id=cluster_id,
                    data_json=json.dumps({
                        'archetype': meta.get('archetype', ''),
                        'silence_pressure': meta.get('silence_pressure', 0.0),
                        'intent_scores': meta.get('intent_scores', {}),
                        'node_count': meta.get('node_count', 0),
                        'temporal_activity': meta.get('activity_score', 0.0),
                        'asn_entropy': meta.get('asn_diversity', 0.0),
                        'signal_coherence': meta.get('phase_coherence', 0.0),
                        'dimensional_density': meta.get('dimensional_density', 0.0),
                        'activation_cascade': meta.get('activation_cascade', []),
                        'temporal_ghost_events': meta.get('temporal_ghost_events', []),
                    }),
                    timestamp_ms=int(time.time() * 1000),
                )
            except Exception:
                _fail += 1
                if _fail >= 5:
                    context.abort(grpc.StatusCode.UNAVAILABLE, f'Instance unreachable for cluster {cluster_id!r}')
                    return
            time.sleep(10.0)


# ---------------------------------------------------------------------------
# ScytheStreamServicer
# ---------------------------------------------------------------------------
import struct as _struct  # used by StreamRFField binary parser

class ScytheStreamServicer(_BaseServicer, scythe_pb2_grpc.ScytheStreamServiceServicer):
    """High-throughput binary data plane: RF field streaming, cluster snapshots, deltas.

    All three RPCs use REST polling to remain compatible with the subprocess
    deployment model — no shared in-process state is required.

    Data sources:
      StreamRFField   → GET <voxel_url>/api/voxel/latest-field?lod=N
      StreamClusters  → per-instance /api/clusters/intel  (cluster summaries)
      StreamDeltas    → per-instance /api/gravity/nodes   (incremental diffs)

    Auth: every RPC enforces instance-bound auth via _check_instance_auth.
    """

    _LOD_INTERVALS = {0: 0.2, 1: 0.5, 2: 1.0}   # seconds between polls per LOD

    def __init__(
        self,
        orchestrator_url: str,
        internal_token: str,
        voxel_url: str = 'http://127.0.0.1:8766',
    ) -> None:
        super().__init__(orchestrator_url, internal_token)
        self._voxel_url = voxel_url

    # ---- helpers ------------------------------------------------------------

    def _lod_for_altitude(self, altitude_m: float) -> int:
        """Map camera altitude (metres) → LOD level 0/1/2."""
        if altitude_m > 50_000:
            return 0
        if altitude_m > 10_000:
            return 1
        return 2

    def _clusters_for_instance(self, instance_id: str) -> list:
        """Fetch cluster intel from an instance via /api/clusters/intel.

        Returns list of cluster dicts from narrate_cluster(); [] on any error.
        Falls back to empty list (cache not yet warm) so the stream keeps going.
        """
        inst_url = self._instance_url(instance_id)
        if not inst_url:
            return []
        try:
            r = requests.get(
                f'{inst_url}/api/clusters/intel',
                headers=self._internal_headers(),
                timeout=5.0,
            )
            r.raise_for_status()
            return r.json().get('clusters', [])
        except Exception:
            return []

    def _nodes_for_instance(self, instance_id: str) -> list:
        """Fetch node list from an instance's gravity API; returns [] on error."""
        inst_url = self._instance_url(instance_id)
        if not inst_url:
            return []
        try:
            r = requests.get(
                f'{inst_url}/api/gravity/nodes',
                headers=self._internal_headers(),
                timeout=3.0,
            )
            r.raise_for_status()
            return r.json().get('nodes', [])
        except Exception:
            return []

    # ---- RPCs ---------------------------------------------------------------

    def StreamRFField(self, request, context):
        """Stream RF voxel field frames at the LOD implied by camera altitude.

        `request` is a LodHint (camera_altitude, focus_lng, focus_lat).
        No instance_id here — the RF field is orchestrator-global (no per-instance
        RF processor), so only the orchestrator-level TokenAuthInterceptor applies.
        """
        lod = self._lod_for_altitude(request.camera_altitude)
        interval = self._LOD_INTERVALS.get(lod, 0.5)
        _fail = 0
        last_ts = ''

        while context.is_active():
            try:
                r = requests.get(
                    f'{self._voxel_url}/api/voxel/latest-field',
                    params={'lod': lod},
                    timeout=2.0,
                )
                if r.status_code == 204:
                    time.sleep(interval)
                    continue
                r.raise_for_status()
                _fail = 0

                # Skip unchanged frames via the server-side timestamp header
                ts = r.headers.get('X-Field-Timestamp', '')
                if ts and ts == last_ts:
                    time.sleep(interval)
                    continue
                last_ts = ts

                # Binary layout: [sx:u16 LE][sy:u16 LE][sz:u16 LE][float32 LE ...]
                raw = r.content
                if len(raw) < 6:
                    time.sleep(interval)
                    continue
                sx, sy, sz = _struct.unpack_from('<HHH', raw, 0)
                voxels_bytes = raw[6:]

                yield scythe_pb2.RFField(
                    size_x=sx,
                    size_y=sy,
                    size_z=sz,
                    voxels=voxels_bytes,
                    timestamp=int(time.time() * 1000),
                    lod=lod,
                )
            except Exception:
                _fail += 1
                if _fail >= 5:
                    context.abort(grpc.StatusCode.UNAVAILABLE, 'RF voxel processor unreachable')
                    return
            time.sleep(interval)

    def StreamClusters(self, request, context):
        """Stream cluster topology snapshots (~1 Hz) for a single instance.

        `request` is a StreamRequest with `instance_id`.
        Each cluster from /api/clusters/intel is emitted as a StreamCluster with:
          - A single centroid StreamNode representing the cluster centre
          - Summary metrics: activity_score, asn_diversity, phase_coherence
        cluster_id in the proto is '<instance_id>/<local_cluster_id>'.
        """
        if not self._check_instance_auth(request.instance_id, context):
            return

        _fail = 0
        while context.is_active():
            clusters = self._clusters_for_instance(request.instance_id)
            if not clusters and _fail == 0:
                # Cache likely not warm yet — wait and retry
                time.sleep(2.0)
                _fail += 1
                continue

            ts_ms = int(time.time() * 1000)
            for cl in clusters:
                cid = str(cl.get('id', cl.get('cluster_id', 'unknown')))
                centroid = cl.get('centroid', [0.0, 0.0])  # [lat, lon]
                lat = float(centroid[0]) if len(centroid) > 0 else 0.0
                lon = float(centroid[1]) if len(centroid) > 1 else 0.0

                # Represent the cluster as a single centroid node
                centroid_node = scythe_pb2.StreamNode(
                    id=abs(hash(cid)) % (2**32),
                    x=lon / 180.0,    # normalise to [-1, 1]
                    y=lat / 90.0,
                    z=0.0,
                    intensity=float(cl.get('threat_score') or 0.0),
                    threat=float(cl.get('threat_score') or 0.0),
                    size=float(cl.get('node_count') or 1),
                )

                phase = cl.get('phase') or {}
                yield scythe_pb2.StreamCluster(
                    cluster_id=f'{request.instance_id}/{cid}',
                    nodes=[centroid_node],
                    activity_score=float(cl.get('threat_score') or 0.0),
                    asn_diversity=float(cl.get('asn_diversity') or 0.0),
                    phase_coherence=float(phase.get('phase_coherence') or 0.0),
                    timestamp_ms=ts_ms,
                )

            _fail = 0
            time.sleep(1.0)

    def StreamDeltas(self, request, context):
        """Stream per-node position and intensity deltas for a single instance.

        `request` is a StreamRequest with `instance_id`.
        Tracks previous gravity snapshot and emits StreamDelta for each node
        whose position or intensity changed since the last poll.
        """
        if not self._check_instance_auth(request.instance_id, context):
            return

        _prev: dict[str, tuple] = {}   # node_id_str → (x, y, z, intensity)
        _fail = 0

        while context.is_active():
            raw_nodes = self._nodes_for_instance(request.instance_id)
            if not raw_nodes:
                _fail += 1
                if _fail >= 5:
                    context.abort(grpc.StatusCode.UNAVAILABLE,
                                  f'Instance {request.instance_id!r} unreachable for deltas')
                    return
                time.sleep(1.0)
                continue

            _fail = 0
            ts_ms = int(time.time() * 1000)
            for idx, n in enumerate(raw_nodes):
                nid = int(n.get('id', idx))
                key = str(nid)
                cur = (
                    float(n.get('x') or n.get('lon') or 0.0),
                    float(n.get('y') or n.get('lat') or 0.0),
                    float(n.get('z') or 0.0),
                    float(n.get('intensity') or n.get('mass') or 0.0),
                )
                if key in _prev:
                    p = _prev[key]
                    dx, dy, dz, di = cur[0]-p[0], cur[1]-p[1], cur[2]-p[2], cur[3]-p[3]
                    if abs(dx) + abs(dy) + abs(dz) + abs(di) > 1e-6:
                        yield scythe_pb2.StreamDelta(
                            node_id=nid,
                            dx=dx,
                            dy=dy,
                            dz=dz,
                            d_intensity=di,
                            timestamp_ms=ts_ms,
                        )
                _prev[key] = cur
            time.sleep(1.0)

    def StreamSwarmDeltas(self, request: scythe_pb2.StreamRequest, context):
        """Server-streaming: push SwarmDelta only when values change (~5 s polling)."""
        instance_id = request.instance_id
        self._check_instance_auth(instance_id, context)

        poll_interval = 2.0 if request.changes_only else 5.0
        prev: dict[str, tuple] = {}

        while context.is_active():
            url = f'{self._orch}/api/clusters/intel'
            if instance_id:
                url += f'?instance_id={instance_id}'
            try:
                r = requests.get(
                    url,
                    headers={'Authorization': f'Bearer {self._tok}'},
                    timeout=4.0,
                )
                clusters = r.json().get('clusters', []) if r.status_code == 200 else []
            except Exception:
                clusters = []

            for c in clusters:
                cid = c.get('cluster_id', '')
                sp = float(c.get('silence_pressure', 0.0))
                coh = float(c.get('asn_diversity', 0.0))
                intent_list = c.get('intent_scores') or []
                top = next((i for i in intent_list if isinstance(i, dict)), {})
                intent = top.get('label', '')
                prob = float(top.get('score', 0.0))
                cur = (sp, coh, intent, prob)
                if prev.get(cid) != cur:
                    prev[cid] = cur
                    yield scythe_pb2.SwarmDelta(
                        cluster_id=cid,
                        silence_pressure=sp,
                        coherence=coh,
                        latent_intent=intent,
                        intent_prob=prob,
                        timestamp_ms=int(time.time() * 1000),
                    )

            time.sleep(poll_interval)


# ---------------------------------------------------------------------------
# AuthServicer
# ---------------------------------------------------------------------------

class AuthServicer(_BaseServicer, scythe_pb2_grpc.AuthServiceServicer):
    """Proxy gRPC Login calls to the per-instance REST /api/operator/login."""

    def Login(self, request: scythe_pb2.LoginRequest, context):
        instance_url = self._instance_url(request.instance_id)
        if not instance_url:
            context.abort(
                grpc.StatusCode.NOT_FOUND,
                f'Unknown instance: {request.instance_id!r}',
            )

        try:
            r = requests.post(
                f'{instance_url}/api/operator/login',
                json={'callsign': request.callsign, 'password': request.password},
                timeout=5.0,
            )
        except requests.RequestException as exc:
            context.abort(grpc.StatusCode.UNAVAILABLE, str(exc))

        if r.status_code == 200:
            data = r.json()
            session = data.get('session') or {}
            token = session.get('session_token') or session.get('token', '')
            return scythe_pb2.LoginResponse(success=True, token=token, message='OK')

        try:
            msg = r.json().get('message', r.text[:200])
        except Exception:
            msg = r.text[:200]
        return scythe_pb2.LoginResponse(success=False, token='', message=msg)


# ---------------------------------------------------------------------------
# OperatorStreamServicer
# ---------------------------------------------------------------------------

_BP_POLL_INTERVAL = 10.0     # seconds between backpressure polls
_CLUSTER_INTERVAL = 3.0      # seconds between cluster snapshots
_CONNECT_TICK     = 0.5      # main loop sleep


class OperatorStreamServicer(_BaseServicer, scythe_pb2_grpc.OperatorStreamServicer):
    """
    Bidi stream with anonymous warm-start and in-place auth upgrade.

    Pattern (sync gRPC bidi):
      - Inbound iterator read in a daemon thread → auth_info / upgrade_event
      - Main generator thread pushes outbound envelopes on a timer
    Anonymous: top-5 clusters at LOD 0, no swarm intel, no backpressure
    Authenticated: full clusters, swarm deltas, backpressure signals
    """

    def Connect(self, request_iterator, context):
        auth_info: list[Optional[dict]] = [None]
        upgrade_evt = threading.Event()

        def _read_inbound():
            try:
                for env in request_iterator:
                    kind = env.WhichOneof('payload')
                    if kind == 'auth_request':
                        info = _cached_validate(
                            env.auth_request.token, self._orch, self._tok,
                        )
                        if info:
                            auth_info[0] = info
                            upgrade_evt.set()
                            log.info(
                                '[OpStream] Anonymous→authenticated: %s',
                                info.get('operator_id', '?'),
                            )
            except Exception:
                pass

        threading.Thread(target=_read_inbound, daemon=True).start()

        last_cluster = 0.0
        last_bp = 0.0
        last_model_status = 0.0
        prev_swarm: dict[str, tuple] = {}

        while context.is_active():
            now = time.monotonic()
            authed = auth_info[0] is not None
            instance_id = (auth_info[0] or {}).get('instance_id', '')

            # Cluster snapshots every _CLUSTER_INTERVAL
            if now - last_cluster >= _CLUSTER_INTERVAL:
                last_cluster = now
                for cl in self._fetch_clusters(instance_id, anon=not authed):
                    yield scythe_pb2.OperatorEnvelope(cluster=cl)

                # Swarm deltas piggyback on cluster fetch window (authenticated only)
                if authed:
                    for sd in self._compute_swarm_deltas(instance_id, prev_swarm):
                        yield scythe_pb2.OperatorEnvelope(swarm_delta=sd)

            # Backpressure every _BP_POLL_INTERVAL (authenticated only)
            if authed and now - last_bp >= _BP_POLL_INTERVAL:
                last_bp = now
                bp = self._fetch_backpressure(instance_id)
                if bp:
                    yield scythe_pb2.OperatorEnvelope(backpressure=bp)

            # Model status every 30 s — visible to anonymous operators so they
            # see "TAK-ML offline" even before logging in.
            if now - last_model_status >= _TAKML_MODEL_STATUS_INTERVAL:
                last_model_status = now
                ms = self._fetch_model_status(instance_id)
                if ms:
                    yield scythe_pb2.OperatorEnvelope(model_status=ms)

            time.sleep(_CONNECT_TICK)

    def StreamView(self, request: scythe_pb2.OperatorView, context):
        """Server-streaming with explicit layer subscription. Requires auth."""
        meta = dict(context.invocation_metadata())
        auth_hdr = meta.get('authorization', '')
        token = auth_hdr[7:].strip() if auth_hdr.lower().startswith('bearer ') else auth_hdr.strip()
        auth_info = _cached_validate(token, self._orch, self._tok) if token else None
        instance_id = (auth_info or {}).get('instance_id', request.instance_id or '')

        prev_swarm: dict[str, tuple] = {}
        last_bp = 0.0

        while context.is_active():
            now = time.monotonic()

            if request.clusters:
                for cl in self._fetch_clusters(instance_id, anon=auth_info is None):
                    yield scythe_pb2.OperatorEnvelope(cluster=cl)

            if request.swarm_deltas and auth_info:
                for sd in self._compute_swarm_deltas(instance_id, prev_swarm):
                    yield scythe_pb2.OperatorEnvelope(swarm_delta=sd)

            if request.backpressure and now - last_bp >= _BP_POLL_INTERVAL:
                last_bp = now
                bp = self._fetch_backpressure(instance_id)
                if bp:
                    yield scythe_pb2.OperatorEnvelope(backpressure=bp)

            time.sleep(_CLUSTER_INTERVAL)

    # ── shared fetch helpers ──────────────────────────────────────────────────

    def _fetch_clusters(self, instance_id: str, anon: bool = False) -> list:
        url = f'{self._orch}/api/clusters/intel'
        if instance_id:
            url += f'?instance_id={instance_id}'
        try:
            r = requests.get(
                url,
                headers={'Authorization': f'Bearer {self._tok}'},
                timeout=4.0,
            )
            clusters = r.json().get('clusters', []) if r.status_code == 200 else []
        except Exception:
            clusters = []

        if anon:
            clusters = clusters[:5]  # anonymous: top-5 only

        result = []
        for c in clusters:
            centroid = c.get('centroid', [0.0, 0.0])
            lat = float(centroid[0]) if len(centroid) > 0 else 0.0
            lon = float(centroid[1]) if len(centroid) > 1 else 0.0
            node = scythe_pb2.StreamNode(
                id=abs(hash(c.get('cluster_id', ''))) % (2 ** 32),
                x=lon, y=lat, z=0.0,
                intensity=float(c.get('threat_score', 0.0)),
                threat=float(c.get('threat_score', 0.0)),
                size=float(c.get('node_count', 1)),
            )
            result.append(scythe_pb2.StreamCluster(
                cluster_id=c.get('cluster_id', ''),
                nodes=[node],
                activity_score=float(c.get('activity_score', 0.0)),
                asn_diversity=float(c.get('asn_diversity', 0.0)),
                phase_coherence=float((c.get('phase') or {}).get('phase_coherence', 0.0)),
                timestamp_ms=int(time.time() * 1000),
            ))
        return result

    def _compute_swarm_deltas(
        self, instance_id: str, prev: dict[str, tuple],
    ) -> list:
        """Return SwarmDelta messages for clusters whose metrics changed; updates prev."""
        url = f'{self._orch}/api/clusters/intel'
        if instance_id:
            url += f'?instance_id={instance_id}'
        try:
            r = requests.get(
                url,
                headers={'Authorization': f'Bearer {self._tok}'},
                timeout=4.0,
            )
            clusters = r.json().get('clusters', []) if r.status_code == 200 else []
        except Exception:
            clusters = []

        deltas = []
        for c in clusters:
            cid = c.get('cluster_id', '')
            sp = float(c.get('silence_pressure', 0.0))
            coh = float(c.get('asn_diversity', 0.0))
            intent_list = c.get('intent_scores') or []
            top = next((i for i in intent_list if isinstance(i, dict)), {})
            intent = top.get('label', '')
            prob = float(top.get('score', 0.0))
            cur = (sp, coh, intent, prob)
            if prev.get(cid) != cur:
                prev[cid] = cur
                deltas.append(scythe_pb2.SwarmDelta(
                    cluster_id=cid,
                    silence_pressure=sp,
                    coherence=coh,
                    latent_intent=intent,
                    intent_prob=prob,
                    timestamp_ms=int(time.time() * 1000),
                ))
        return deltas

    def _fetch_backpressure(
        self, instance_id: str,
    ) -> 'Optional[scythe_pb2.BackpressureSignal]':
        """Poll /api/health/queues on the target instance."""
        if not instance_id:
            return None
        inst_url = self._instance_url(instance_id)
        if not inst_url:
            return None
        try:
            r = requests.get(f'{inst_url}/api/health/queues', timeout=3.0)
            if r.status_code != 200:
                return None
            d = r.json().get('graph_event_queue', {})
            return scythe_pb2.BackpressureSignal(
                queue_utilization=float(d.get('utilization', 0.0)),
                drops_total=int(d.get('drops_total', 0)),
                healthy=bool(d.get('healthy', True)),
                timestamp_ms=int(time.time() * 1000),
            )
        except Exception:
            return None

    def _fetch_model_status(
        self, instance_id: str,
    ) -> 'Optional[scythe_pb2.ModelStatus]':
        """Poll /api/tak-ml/kserve/health on the target instance.

        Uses X-Internal-Token so the session-guarded health endpoint accepts the
        request — `OperatorStreamServicer` has no operator session of its own.
        """
        if not instance_id:
            return None
        inst_url = self._instance_url(instance_id)
        if not inst_url:
            return None
        try:
            r = requests.get(
                f'{inst_url}/api/tak-ml/kserve/health',
                headers=self._internal_headers(),
                timeout=3.0,
            )
            if r.status_code != 200:
                return None
            d = r.json()
            return scythe_pb2.ModelStatus(
                model='nerf_botnet_v1',
                reachable=bool(d.get('reachable', False)),
                latency_ms=float(d.get('latency_ms', 0.0)),
                base_url=d.get('base_url', ''),
                timestamp_ms=int(time.time() * 1000),
            )
        except Exception:
            return None


# ---------------------------------------------------------------------------
# TakMLServicer
# ---------------------------------------------------------------------------

_TAKML_MODEL_STATUS_INTERVAL = 30.0  # seconds between model-health polls in Connect


class TakMLServicer(_BaseServicer, scythe_pb2_grpc.TakMLServiceServicer):
    """Route KServe/Triton inference requests through the SCYTHE backend.

    This keeps browsers from hitting Triton directly, and applies the same
    instance-scoping + auth model as every other servicer.
    """

    def Infer(self, request: scythe_pb2.TakMLRequest, context):
        # Enforce instance-bound session scope (same contract as every other servicer)
        if not self._check_instance_auth(request.instance_id, context):
            return scythe_pb2.TakMLResponse()

        inst_url = self._instance_url(request.instance_id)
        if not inst_url:
            context.abort(
                grpc.StatusCode.NOT_FOUND,
                f'Unknown instance: {request.instance_id!r}',
            )

        model    = request.model   or 'nerf_botnet_v1'
        version  = request.version or '1'
        features = list(request.features)

        # Validate feature vector length before forwarding to avoid silent bad scores
        if features and len(features) != 7:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                f'features must be exactly 7 floats, got {len(features)}',
            )
        if not features:
            features = [0.0] * 7

        t0 = time.monotonic()
        try:
            r = requests.post(
                f'{inst_url}/api/tak-ml/kserve/infer',
                json={'model': model, 'version': version, 'features': features},
                headers=self._internal_headers(),
                timeout=3.0,
            )
            latency_ms = int((time.monotonic() - t0) * 1000)
            if r.status_code == 200:
                d = r.json()
                # Downstream failure is encoded as reachable=false / score=null.
                # Do not collapse to 0.0 — that would silently misreport threats.
                if not d.get('reachable'):
                    context.abort(
                        grpc.StatusCode.UNAVAILABLE,
                        f'KServe not reachable: {d.get("message", "")}',
                    )
                raw_score = d.get('score')
                if raw_score is None:
                    context.abort(
                        grpc.StatusCode.UNAVAILABLE,
                        'KServe returned null score',
                    )
                return scythe_pb2.TakMLResponse(
                    score=float(raw_score),
                    model=model,
                    version=version,
                    latency_ms=latency_ms,
                    server_reachable=True,
                )
            else:
                context.abort(
                    grpc.StatusCode.UNAVAILABLE,
                    f'Proxy HTTP {r.status_code}',
                )
        except requests.RequestException as exc:
            context.abort(grpc.StatusCode.UNAVAILABLE, str(exc))

    def StreamInfer(self, request: scythe_pb2.TakMLRequest, context):
        """Not implemented: real streaming requires event-driven inference history.

        Emitting repeated polls of the same static feature vector would produce
        synthetic scores disconnected from actual flow data.  Callers should use
        the unary `Infer` RPC or subscribe to `OperatorStream.Connect` which
        pushes `ModelStatus` health envelopes on a schedule.
        """
        context.abort(
            grpc.StatusCode.UNIMPLEMENTED,
            'StreamInfer requires event-driven inference source; use unary Infer instead',
        )


# ---------------------------------------------------------------------------
# ReconEntityStream
# ---------------------------------------------------------------------------
class ReconEntityStreamServicer(_BaseServicer, scythe_pb2_grpc.ReconEntityStreamServicer):
    """Stream recon entity mutations as binary EntityPatch messages.

    Protocol:
    1. Snapshot: fetch all entities from the instance REST API, emit each as
       op="upsert" so the client builds its initial Map.
    2. Delta loop: re-fetch every _POLL_S seconds, diff against the known-state
       dict keyed on entity_id, emit patches only for new or changed entries.
    3. Deletions: ids present in the previous cycle but absent in the new one
       are emitted as op="delete".

    This polling-diff approach is intentionally simple and consistent with
    StreamGraphDeltas.  A push-based variant (wiring SSE _subscribers → gRPC
    context) can replace it later without changing the client contract.
    """

    _POLL_S = 3.0
    _MAX_SNAPSHOT = 5_000  # guard against unbounded memory in the known-state dict

    @staticmethod
    def _entity_to_patch(e: dict, op: str = 'upsert') -> scythe_pb2.EntityPatch:
        coords = e.get('coords') or e.get('location') or [0.0, 0.0]
        if not isinstance(coords, (list, tuple)) or len(coords) < 2:
            coords = [0.0, 0.0]
        geo = e.get('geo') or {}
        lat = float(geo.get('lat', coords[0]) or coords[0])
        lon = float(geo.get('lon', coords[1]) or coords[1])
        confidence = float(
            e.get('geo_confidence') or
            e.get('confidence') or
            geo.get('confidence') or
            0.3
        )
        sources = e.get('geo_sources') or e.get('sources') or []
        if isinstance(sources, str):
            sources = [sources]
        # Entity store uses 'last_update' (AutoReconSystem) — fall through alternatives
        last_seen_raw = (e.get('last_update') or e.get('last_seen') or
                         e.get('updated_at') or e.get('created') or 0)
        try:
            last_seen_ms = int(float(last_seen_raw) * 1000)
        except (TypeError, ValueError):
            last_seen_ms = 0
        return scythe_pb2.EntityPatch(
            op=op,
            id=str(e.get('entity_id') or e.get('id') or ''),
            # Entity store uses 'type'; fall through to 'entity_type' for compat
            kind=str(e.get('type') or e.get('entity_type') or e.get('kind') or 'HOST'),
            label=str(e.get('label') or e.get('display_name') or e.get('entity_id') or ''),
            lat=lat,
            lon=lon,
            confidence=confidence,
            last_seen=last_seen_ms,
            sources=list(sources),
            threat=float(e.get('threat_score') or e.get('threat') or 0.0),
            instance_id=str(e.get('instance_id') or ''),
        )

    def StreamEntities(self, request: scythe_pb2.ReconStreamRequest, context):
        if not self._check_instance_auth(request.instance_id, context):
            return

        instance_url = self._instance_url(request.instance_id)
        if not instance_url:
            context.abort(grpc.StatusCode.NOT_FOUND, f'Instance {request.instance_id!r} not found')
            return

        kind_filter = (request.filter or '').upper()

        # known[entity_id] = last_update epoch float for delta detection
        known: dict[str, float] = {}
        # pending_del[entity_id] = consecutive-absent-poll count (2 = emit delete)
        pending_del: dict[str, int] = {}
        _fail = 0
        _first = True

        while context.is_active():
            try:
                r = requests.get(
                    f'{instance_url}/api/recon/entities',
                    headers=self._internal_headers(),
                    timeout=5.0,
                )
                r.raise_for_status()
                entities: list[dict] = r.json().get('entities', [])
                _fail = 0
            except Exception as exc:
                _fail += 1
                if _fail >= 5:
                    context.abort(grpc.StatusCode.UNAVAILABLE, f'Instance unreachable: {exc}')
                    return
                time.sleep(self._POLL_S)
                continue

            current_ids: set[str] = set()
            for e in entities:
                eid = str(e.get('entity_id') or e.get('id') or '')
                if not eid:
                    continue
                if kind_filter and (e.get('type') or e.get('entity_type') or '').upper() != kind_filter:
                    current_ids.add(eid)
                    continue
                current_ids.add(eid)
                # Entity store uses 'last_update'; fall through alternatives
                last_seen_raw = (e.get('last_update') or e.get('last_seen') or
                                 e.get('updated_at') or 0.0)
                try:
                    last_seen = float(last_seen_raw)
                except (TypeError, ValueError):
                    last_seen = 0.0

                is_new     = eid not in known
                is_changed = not is_new and known[eid] != last_seen

                if _first or is_new or is_changed:
                    try:
                        yield self._entity_to_patch(e, op='upsert')
                    except Exception:
                        pass

                known[eid] = last_seen
                pending_del.pop(eid, None)  # entity is present — cancel any pending delete

            # Two-poll confirmation before emitting deletes (guards against transient
            # empty snapshots during Flask instance restart / partial rehydration)
            if not _first:
                absent = set(known) - current_ids
                for gone_id in absent:
                    count = pending_del.get(gone_id, 0) + 1
                    if count >= 2:
                        try:
                            yield scythe_pb2.EntityPatch(
                                op='delete',
                                id=gone_id,
                                instance_id=request.instance_id,
                            )
                        except Exception:
                            pass
                        known.pop(gone_id, None)
                        pending_del.pop(gone_id, None)
                    else:
                        pending_del[gone_id] = count
                # Clear pending_del for IDs that are no longer absent
                for eid in list(pending_del):
                    if eid in current_ids:
                        pending_del.pop(eid, None)

            # Bound memory: evict stalest entries (lowest last_update) first so
            # long-lived high-priority entities (C2, persistent threats) are retained
            if len(known) > self._MAX_SNAPSHOT:
                overflow = len(known) - self._MAX_SNAPSHOT
                for eid, _ in sorted(known.items(), key=lambda kv: kv[1])[:overflow]:
                    known.pop(eid, None)
                    pending_del.pop(eid, None)

            _first = False
            time.sleep(self._POLL_S)


# ---------------------------------------------------------------------------
# ControlPathStream
# ---------------------------------------------------------------------------
class ControlPathStreamServicer(_BaseServicer, scythe_pb2_grpc.ControlPathStreamServicer):
    """Stream observer-relative control-path forecast patches as binary protobuf."""

    _POLL_S = 0.5
    _DELETE_CONFIRM_POLLS = 2
    _MAX_TRACKED = 1024

    class _UpstreamHttpError(RuntimeError):
        def __init__(self, status_code: int, message: str):
            super().__init__(message)
            self.status_code = status_code
            self.message = message

    @staticmethod
    def _point_from_motion(point: dict) -> scythe_pb2.ControlPathPoint:
        location = point.get('location') or {}
        return scythe_pb2.ControlPathPoint(
            lat=float(location.get('lat') or 0.0),
            lon=float(location.get('lon') or 0.0),
            alt_m=float(location.get('alt_m') or 0.0),
            confidence=float(point.get('confidence') or 0.0),
            radius_m=float(point.get('radius_m') or 0.0),
            timestamp_ms=int(float(point.get('timestamp') or 0.0) * 1000.0) if point.get('timestamp') else 0,
            step=int(point.get('step') or 0),
            time_offset_s=int(float(point.get('time_offset_s') or 0.0)),
            model=str(point.get('model') or ''),
        )

    @staticmethod
    def _point_from_projection(point: dict) -> scythe_pb2.ControlPathPoint:
        location = point.get('location') or {}
        metadata = point.get('metadata') or {}
        return scythe_pb2.ControlPathPoint(
            lat=float(location.get('lat') or 0.0),
            lon=float(location.get('lon') or 0.0),
            alt_m=float(location.get('alt_m') or 0.0),
            confidence=float(point.get('confidence') or 0.0),
            radius_m=float(metadata.get('radius_m') or 0.0),
            timestamp_ms=0,
            distance_m=float(point.get('distance_m') or 0.0),
            absolute_bearing_deg=float(point.get('absolute_bearing_deg') or 0.0),
            relative_bearing_deg=float(point.get('relative_bearing_deg') or 0.0),
            elevation_deg=float(point.get('elevation_deg') or 0.0),
            step=int(metadata.get('step') or 0),
            time_offset_s=int(float(metadata.get('time_offset_s') or 0.0)),
            model=str(metadata.get('model') or ''),
        )

    @classmethod
    def _prediction_to_patch(
        cls,
        prediction: dict,
        *,
        op: str,
        instance_id: str,
        observer_id: str,
        updated_at_ms: int,
    ) -> scythe_pb2.ControlPathPatch:
        evidence = prediction.get('supporting_evidence') or {}
        projected_target = prediction.get('projected_target') or {}
        patch = scythe_pb2.ControlPathPatch(
            op=op,
            prediction_id=str(prediction.get('prediction_id') or ''),
            rf_prediction_id=str(prediction.get('rf_prediction_id') or ''),
            instance_id=instance_id,
            observer_id=observer_id,
            current_entity_id=str(prediction.get('current_entity_id') or ''),
            current_label=str(prediction.get('current_label') or ''),
            target_entity_id=str(prediction.get('target_entity_id') or ''),
            target_label=str(prediction.get('target_label') or ''),
            confidence=float(prediction.get('confidence') or 0.0),
            time_horizon_s=int(prediction.get('time_horizon_s') or 0),
            candidate_source=str(prediction.get('candidate_source') or ''),
            updated_at_ms=updated_at_ms,
            motion_forecast=[
                cls._point_from_motion(point)
                for point in (((prediction.get('motion_forecast') or {}).get('path')) or [])
                if isinstance(point, dict)
            ],
            projected_path=[
                cls._point_from_projection(point)
                for point in (prediction.get('projected_path') or [])
                if isinstance(point, dict)
            ],
            rf_class=str((evidence.get('rf') or {}).get('class') or ''),
            binding_confidence=float(evidence.get('binding_confidence') or 0.0),
            identity_similarity=float(evidence.get('identity_similarity') or 0.0),
            fanin_score=float(evidence.get('fanin_score') or 0.0),
            temporal_pressure=float(evidence.get('temporal_pressure') or 0.0),
            provenance_rule=str(prediction.get('provenance_rule') or ''),
            entropy=float(prediction.get('entropy') or evidence.get('entropy') or 0.0),
            divergence_risk=float(prediction.get('divergence_risk') or evidence.get('divergence_risk') or 0.0),
            dissonance_score=float(
                prediction.get('dissonance_score')
                or (evidence.get('cognitive_dissonance') or {}).get('score')
                or 0.0
            ),
            dissonance_zone=str(
                prediction.get('dissonance_zone')
                or (evidence.get('cognitive_dissonance') or {}).get('zone')
                or ''
            ),
            identity_pressure=float(prediction.get('identity_pressure') or evidence.get('identity_pressure') or 0.0),
            temporal_phase=str(prediction.get('temporal_phase') or evidence.get('temporal_phase') or ''),
            temporal_cohesion=float(
                prediction.get('temporal_cohesion') or evidence.get('temporal_cohesion') or 0.0
            ),
            periodicity_s=float(prediction.get('periodicity_s') or evidence.get('periodicity_s') or 0.0),
            last_seen_delta_s=float(
                prediction.get('last_seen_delta_s') or evidence.get('last_seen_delta_s') or 0.0
            ),
            top_intent_label=str(
                prediction.get('top_intent_label')
                or (evidence.get('top_intent') or {}).get('label')
                or ''
            ),
            top_intent_probability=float(
                prediction.get('top_intent_probability')
                or (evidence.get('top_intent') or {}).get('probability')
                or 0.0
            ),
            resilience_score=float(
                prediction.get('resilience_score')
                or (evidence.get('countermeasure_simulation') or {}).get('resilience_score')
                or 0.0
            ),
            countermeasure_strategy=str(
                prediction.get('countermeasure_strategy')
                or (evidence.get('countermeasure_simulation') or {}).get('recommended_action')
                or ''
            ),
            field_view_mode=str(
                prediction.get('field_view_mode')
                or ((prediction.get('field_view') or {}).get('mode'))
                or ((evidence.get('field_view') or {}).get('mode'))
                or ''
            ),
            requires_multi_node_disruption=bool(
                prediction.get('requires_multi_node_disruption')
                or (evidence.get('countermeasure_simulation') or {}).get('requires_multi_node_disruption')
            ),
            behavior_class=str(
                prediction.get('behavior_class')
                or evidence.get('behavior_class')
                or ''
            ),
        )
        for hypothesis in (prediction.get('intent_hypotheses') or evidence.get('intent_hypotheses') or []):
            patch.intent_hypotheses.add(
                label=str((hypothesis or {}).get('label') or ''),
                probability=float((hypothesis or {}).get('probability') or 0.0),
            )
        temporal_overlay = prediction.get('temporal_overlay') or evidence.get('temporal_overlay') or {}
        if isinstance(temporal_overlay, dict) and temporal_overlay:
            patch.temporal.CopyFrom(
                scythe_pb2.TemporalOverlay(
                    periodicity_s=float(temporal_overlay.get('periodicity_s') or 0.0),
                    periodicity_confidence=float(temporal_overlay.get('periodicity_confidence') or 0.0),
                    burstiness=float(temporal_overlay.get('burstiness') or 0.0),
                    pattern=str(temporal_overlay.get('pattern') or ''),
                    confidence=float(temporal_overlay.get('confidence') or 0.0),
                    phase=str(temporal_overlay.get('phase') or ''),
                    temporal_cohesion=float(temporal_overlay.get('temporal_cohesion') or 0.0),
                    last_seen_delta_s=float(temporal_overlay.get('last_seen_delta_s') or 0.0),
                )
            )
        behavior_scores = prediction.get('behavior_scores') or evidence.get('behavior_scores') or {}
        if isinstance(behavior_scores, dict):
            for key, value in behavior_scores.items():
                try:
                    patch.behavior_scores[str(key)] = float(value)
                except (TypeError, ValueError):
                    continue
        if isinstance(projected_target, dict) and projected_target.get('location'):
            patch.projected_target.CopyFrom(cls._point_from_projection(projected_target))
        return patch

    @staticmethod
    def _prediction_signature(prediction: dict) -> str:
        evidence = prediction.get('supporting_evidence') or {}
        normalized = {
            'prediction_id': prediction.get('prediction_id'),
            'confidence': round(float(prediction.get('confidence') or 0.0), 4),
            'time_horizon_s': int(prediction.get('time_horizon_s') or 0),
            'candidate_source': prediction.get('candidate_source'),
            'current_entity_id': prediction.get('current_entity_id'),
            'target_entity_id': prediction.get('target_entity_id'),
            'projected_target': prediction.get('projected_target'),
            'projected_path': prediction.get('projected_path') or [],
            'motion_forecast': (prediction.get('motion_forecast') or {}).get('path') or [],
            'rf_class': (evidence.get('rf') or {}).get('class'),
            'binding_confidence': round(float(evidence.get('binding_confidence') or 0.0), 4),
            'identity_similarity': round(float(evidence.get('identity_similarity') or 0.0), 4),
            'fanin_score': round(float(evidence.get('fanin_score') or 0.0), 4),
            'temporal_pressure': round(float(evidence.get('temporal_pressure') or 0.0), 4),
            'entropy': round(float(prediction.get('entropy') or evidence.get('entropy') or 0.0), 4),
            'divergence_risk': round(float(prediction.get('divergence_risk') or evidence.get('divergence_risk') or 0.0), 4),
            'dissonance_score': round(
                float(
                    prediction.get('dissonance_score')
                    or (evidence.get('cognitive_dissonance') or {}).get('score')
                    or 0.0
                ),
                4,
            ),
            'dissonance_zone': str(
                prediction.get('dissonance_zone')
                or (evidence.get('cognitive_dissonance') or {}).get('zone')
                or ''
            ),
            'identity_pressure': round(float(prediction.get('identity_pressure') or evidence.get('identity_pressure') or 0.0), 4),
            'temporal_phase': str(prediction.get('temporal_phase') or evidence.get('temporal_phase') or ''),
            'temporal_cohesion': round(
                float(prediction.get('temporal_cohesion') or evidence.get('temporal_cohesion') or 0.0),
                4,
            ),
            'periodicity_s': round(float(prediction.get('periodicity_s') or evidence.get('periodicity_s') or 0.0), 4),
            'last_seen_delta_s': round(
                float(prediction.get('last_seen_delta_s') or evidence.get('last_seen_delta_s') or 0.0),
                4,
            ),
            'intent_hypotheses': [
                {
                    'label': str((hypothesis or {}).get('label') or ''),
                    'probability': round(float((hypothesis or {}).get('probability') or 0.0), 4),
                }
                for hypothesis in (prediction.get('intent_hypotheses') or evidence.get('intent_hypotheses') or [])
            ],
            'top_intent_label': str(
                prediction.get('top_intent_label')
                or (evidence.get('top_intent') or {}).get('label')
                or ''
            ),
            'top_intent_probability': round(
                float(
                    prediction.get('top_intent_probability')
                    or (evidence.get('top_intent') or {}).get('probability')
                    or 0.0
                ),
                4,
            ),
            'resilience_score': round(
                float(
                    prediction.get('resilience_score')
                    or (evidence.get('countermeasure_simulation') or {}).get('resilience_score')
                    or 0.0
                ),
                4,
            ),
            'countermeasure_strategy': str(
                prediction.get('countermeasure_strategy')
                or (evidence.get('countermeasure_simulation') or {}).get('recommended_action')
                or ''
            ),
            'field_view_mode': str(
                prediction.get('field_view_mode')
                or ((prediction.get('field_view') or {}).get('mode'))
                or ((evidence.get('field_view') or {}).get('mode'))
                or ''
            ),
            'requires_multi_node_disruption': bool(
                prediction.get('requires_multi_node_disruption')
                or (evidence.get('countermeasure_simulation') or {}).get('requires_multi_node_disruption')
            ),
            'temporal_overlay': prediction.get('temporal_overlay') or evidence.get('temporal_overlay') or {},
            'behavior_class': str(prediction.get('behavior_class') or evidence.get('behavior_class') or ''),
            'behavior_scores': {
                str(key): round(float(value), 4)
                for key, value in dict(prediction.get('behavior_scores') or evidence.get('behavior_scores') or {}).items()
                if isinstance(key, str) and isinstance(value, (int, float))
            },
        }
        return json.dumps(normalized, sort_keys=True, separators=(',', ':'))

    def _request_instance_id(self, request, context) -> str:
        instance_id = str(request.instance_id or '').strip()
        if instance_id:
            return instance_id
        session = self._session_for_request(context) or {}
        return str(session.get('instance_id') or '').strip()

    def _fetch_predictions(self, instance_url: str, request: scythe_pb2.ControlPathStreamRequest) -> tuple[list[dict], int]:
        params = {
            'observer_id': request.observer_id,
            'limit': max(1, int(request.limit or 8)),
            'max_distance_m': max(100, int(request.max_distance_m or 10000)),
        }
        r = requests.get(
            f'{instance_url}/api/control-path/predict',
            params=params,
            headers=self._internal_headers(),
            timeout=5.0,
        )
        if r.status_code != 200:
            try:
                payload = r.json()
                message = payload.get('message') or payload.get('error') or ''
            except Exception:
                message = ''
            raise self._UpstreamHttpError(r.status_code, message or f'HTTP {r.status_code}')
        payload = r.json()
        timestamp_ms = int(float(payload.get('timestamp') or time.time()) * 1000.0)
        min_confidence = max(0.0, min(float(request.min_confidence_milli or 0) / 1000.0, 1.0))
        predictions = [
            prediction
            for prediction in (payload.get('predictions') or [])
            if float((prediction or {}).get('confidence') or 0.0) >= min_confidence
        ]
        return predictions, timestamp_ms

    def StreamControlPaths(self, request: scythe_pb2.ControlPathStreamRequest, context):
        instance_id = self._request_instance_id(request, context)
        if not instance_id:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, 'instance_id required')
            return
        if not request.observer_id:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, 'observer_id required')
            return
        if not self._check_instance_auth(instance_id, context):
            return

        instance_url = self._instance_url(instance_id)
        if not instance_url:
            context.abort(grpc.StatusCode.NOT_FOUND, f'Instance {instance_id!r} not found')
            return

        known: dict[str, str] = {}
        pending_del: dict[str, int] = {}
        first = True
        fail_count = 0

        while context.is_active():
            try:
                predictions, updated_at_ms = self._fetch_predictions(instance_url, request)
                fail_count = 0
            except self._UpstreamHttpError as exc:
                if 400 <= exc.status_code < 500:
                    context.abort(
                        self._HTTP_TO_GRPC.get(exc.status_code, grpc.StatusCode.UNKNOWN),
                        exc.message,
                    )
                    return
                fail_count += 1
                if fail_count >= 5:
                    context.abort(grpc.StatusCode.UNAVAILABLE, f'Instance unreachable: {exc.message}')
                    return
                time.sleep(self._POLL_S)
                continue
            except Exception as exc:
                fail_count += 1
                if fail_count >= 5:
                    context.abort(grpc.StatusCode.UNAVAILABLE, f'Instance unreachable: {exc}')
                    return
                time.sleep(self._POLL_S)
                continue

            current_ids: set[str] = set()
            for prediction in predictions:
                prediction_id = str(prediction.get('prediction_id') or '')
                if not prediction_id:
                    continue
                current_ids.add(prediction_id)
                signature = self._prediction_signature(prediction)
                is_new = prediction_id not in known
                is_changed = not is_new and known[prediction_id] != signature
                should_emit = (
                    (first and (request.since_timestamp_ms == 0 or updated_at_ms > int(request.since_timestamp_ms)))
                    or is_new
                    or is_changed
                )
                if should_emit:
                    yield self._prediction_to_patch(
                        prediction,
                        op='upsert',
                        instance_id=instance_id,
                        observer_id=request.observer_id,
                        updated_at_ms=updated_at_ms,
                    )
                known[prediction_id] = signature
                pending_del.pop(prediction_id, None)

            if not first:
                absent_ids = set(known) - current_ids
                for prediction_id in absent_ids:
                    count = pending_del.get(prediction_id, 0) + 1
                    if count >= self._DELETE_CONFIRM_POLLS:
                        yield scythe_pb2.ControlPathPatch(
                            op='delete',
                            prediction_id=prediction_id,
                            instance_id=instance_id,
                            observer_id=request.observer_id,
                            updated_at_ms=updated_at_ms,
                        )
                        known.pop(prediction_id, None)
                        pending_del.pop(prediction_id, None)
                    else:
                        pending_del[prediction_id] = count
                for prediction_id in list(pending_del):
                    if prediction_id in current_ids:
                        pending_del.pop(prediction_id, None)

            if len(known) > self._MAX_TRACKED:
                overflow = len(known) - self._MAX_TRACKED
                for prediction_id in list(known.keys())[:overflow]:
                    known.pop(prediction_id, None)
                    pending_del.pop(prediction_id, None)

            first = False
            time.sleep(self._POLL_S)


# ---------------------------------------------------------------------------
# Server bootstrap
# ---------------------------------------------------------------------------
def serve(
    grpc_port: int,
    orchestrator_url: str,
    internal_token: str,
    no_auth: bool = False,
    voxel_url: str = 'http://127.0.0.1:8766',
) -> None:
    interceptors: list = []
    if not no_auth:
        interceptors.append(TokenAuthInterceptor(orchestrator_url, internal_token))

    # Streaming RPCs hold threads for their full lifetime; use a generous pool
    # so concurrent streaming clients don't starve unary RPCs.
    server = grpc.server(
        ThreadPoolExecutor(max_workers=50),
        interceptors=interceptors,
    )

    scythe_pb2_grpc.add_OrchestratorServiceServicer_to_server(
        OrchestratorServicer(orchestrator_url, internal_token), server,
    )
    scythe_pb2_grpc.add_HypergraphServiceServicer_to_server(
        HypergraphServicer(orchestrator_url, internal_token), server,
    )
    scythe_pb2_grpc.add_ClusterIntelServiceServicer_to_server(
        ClusterIntelServicer(orchestrator_url, internal_token), server,
    )
    scythe_pb2_grpc.add_ScytheStreamServiceServicer_to_server(
        ScytheStreamServicer(orchestrator_url, internal_token, voxel_url), server,
    )
    scythe_pb2_grpc.add_AuthServiceServicer_to_server(
        AuthServicer(orchestrator_url, internal_token), server,
    )
    scythe_pb2_grpc.add_OperatorStreamServicer_to_server(
        OperatorStreamServicer(orchestrator_url, internal_token), server,
    )
    scythe_pb2_grpc.add_TakMLServiceServicer_to_server(
        TakMLServicer(orchestrator_url, internal_token), server,
    )
    scythe_pb2_grpc.add_ReconEntityStreamServicer_to_server(
        ReconEntityStreamServicer(orchestrator_url, internal_token), server,
    )
    scythe_pb2_grpc.add_ControlPathStreamServicer_to_server(
        ControlPathStreamServicer(orchestrator_url, internal_token), server,
    )

    server.add_insecure_port(f'127.0.0.1:{grpc_port}')
    server.start()
    log.info(
        f'[gRPC] Server started on port {grpc_port} '
        f'(auth={"disabled" if no_auth else "enabled"}, voxel={voxel_url})'
    )
    server.wait_for_termination()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='SCYTHE gRPC server')
    parser.add_argument('--grpc-port', type=int, default=50051, help='gRPC listen port')
    parser.add_argument('--orchestrator-url', default='http://127.0.0.1:5001',
                        help='Orchestrator base URL for session validation + REST proxying')
    parser.add_argument('--internal-token', required=True,
                        help='Shared secret for internal proxy calls (X-Internal-Token)')
    parser.add_argument('--no-auth', action='store_true',
                        help='Disable Bearer token validation (development only)')
    parser.add_argument('--voxel-url', default='http://127.0.0.1:8766',
                        help='Base URL of the RF voxel processor (port 8766)')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [gRPC] %(levelname)s %(message)s',
        datefmt='%H:%M:%S',
    )
    serve(args.grpc_port, args.orchestrator_url, args.internal_token, args.no_auth, args.voxel_url)
