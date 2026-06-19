#!/usr/bin/env python3
"""
SCYTHE Multi-Instance Orchestrator
====================================
Manages the lifecycle of isolated RF_SCYTHE server instances.

Each SCYTHE instance is a sovereign analytic workspace:
  - Own hypergraph, inference ledger, behavioral model
  - No shared memory, no shared state, no cross-contamination
  - Independent port, PID, session history

Endpoints:
  GET  /                                  → serves rf_scythe_home.html
  ANY  /scythe/i/<instance_id>/…          → reverse-proxy to child instance (stable URL for Funnel)
  GET  /api/scythe/instances              → list all active instances (+ live health)
  GET  /api/scythe/ready                  → return first socket_io_ready instance (optional ?wait=1)
  POST /api/scythe/instances/new          → spawn a new instance
  POST /api/scythe/instances/register     → child self-registration (accepts socket_io_ready flag)
  DELETE /api/scythe/instances/<id>       → kill + cleanup
  GET  /api/scythe/health                 → orchestrator health

Usage:
  python3 scythe_orchestrator.py [--port 5000] [--host 0.0.0.0]
"""

import argparse
import hmac
import json
import logging
import os
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
import warnings
import jwt
import requests
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

# Add FusionAuth client to sys path
_FA_CLIENT_PATH = Path(__file__).resolve().parent / "assets" / "fusionauth-python-client-develop" / "src" / "main" / "python"
if _FA_CLIENT_PATH.exists():
    sys.path.append(str(_FA_CLIENT_PATH))

try:
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="pkg_resources is deprecated as an API.*",
            category=UserWarning,
        )
        from fusionauth.fusionauth_client import FusionAuthClient
    HAS_FA_CLIENT = True
except ImportError:
    HAS_FA_CLIENT = False

# Constants for FusionAuth
FUSIONAUTH_URL = os.environ.get("FUSIONAUTH_URL") or os.environ.get("FUSIONAUTH_BASE_URL", "http://localhost:9011")
FUSIONAUTH_API_KEY = os.environ.get("FUSIONAUTH_API_KEY", "") # Should be set in env
FUSIONAUTH_ISSUER = "FusionAuth" # Default issuer
fa_client = FusionAuthClient(FUSIONAUTH_API_KEY, FUSIONAUTH_URL) if HAS_FA_CLIENT and FUSIONAUTH_API_KEY else None

def validate_jwt(token: str):
    """Validate a JWT against local internal session, falling back to FusionAuth if needed."""
    if not token:
        return None

    # 1. Try local SCYTHE internal validation (Sovereign Authority)
    if operator_manager:
        try:
            session = operator_manager.validate_session(token)
            if session:
                operator = operator_manager.get_operator(session.operator_id)
                return {
                    'valid': True,
                    'userId': session.operator_id,
                    'firstName': operator.callsign if operator else 'Operator',
                    'legacy': False
                }
        except Exception as e:
            log.debug(f"Local session validation failed: {e}")

    # 2. Try SCYTHE HS256 tokens issued by this orchestrator.
    try:
        internal_secret = globals().get('_INTERNAL_TOKEN')
        if internal_secret and isinstance(token, str) and token.count('.') == 2:
            claims = jwt.decode(
                token,
                internal_secret,
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
            user_id = claims.get('sub') or claims.get('userId')
            if user_id:
                return {
                    'valid': True,
                    'userId': user_id,
                    'firstName': claims.get('preferred_username') or claims.get('username') or claims.get('firstName') or 'Operator',
                    'claims': claims,
                    'legacy': False,
                }
    except Exception as e:
        log.debug(f"Internal JWT validation failed: {e}")

    # 3. Try FusionAuth (Legacy Fallback)
    try:
        if fa_client:
            response = fa_client.validate_jwt(token)
            if response.was_successful():
                data = response.success_response or {}
                claims = data.get('jwt') if isinstance(data.get('jwt'), dict) else data
                user_id = claims.get('sub') or claims.get('userId') or claims.get('user_id')
                claims['valid'] = True
                if user_id:
                    claims['userId'] = user_id
                return claims
        else:
            headers = {}
            if FUSIONAUTH_API_KEY:
                headers['Authorization'] = FUSIONAUTH_API_KEY

            response = requests.get(
                f"{FUSIONAUTH_URL}/api/jwt/validate?token={token}",
                headers=headers,
                timeout=5
            )
            if response.status_code == 200:
                data = response.json()
                if data.get('valid') or data.get('jwt'):
                    claims = data.get('jwt') if isinstance(data.get('jwt'), dict) else data
                    claims['valid'] = True
                    user_id = claims.get('sub') or claims.get('userId') or claims.get('user_id')
                    if user_id:
                        claims['userId'] = user_id
                    return claims
            elif response.status_code == 401:
                log.debug("FusionAuth remote validation returned 401 (expected if session is internal)")
    except Exception as e:
        log.debug(f"FusionAuth JWT validation path failed: {e}")

    return None
def get_user_wallet_balance(user_id):
    """Fetch wallet balance from FusionAuth or legacy operator manager."""
    # 1. Try FusionAuth
    if fa_client:
        try:
            response = fa_client.retrieve_user(user_id)
            if response.was_successful():
                user = response.success_response.get('user', {})
                return user.get('data', {}).get('wallet_balance', 0.0)
        except Exception as e:
            log.debug(f"FusionAuth balance fetch failed: {e}")

    # 2. Try Legacy Operator Manager
    if operator_manager:
        try:
            return operator_manager.get_wallet_balance(user_id)
        except Exception:
            pass

    return 0.0

def update_user_wallet_balance(user_id, new_balance):
    """Update wallet balance in FusionAuth or legacy operator manager."""
    success = False

    # 1. Try FusionAuth
    if fa_client:
        try:
            user_request = {"user": {"data": {"wallet_balance": new_balance}}}
            response = fa_client.patch_user(user_id, user_request)
            if response.was_successful():
                success = True
        except Exception as e:
            log.debug(f"FusionAuth balance update failed: {e}")

    # 2. Try Legacy Operator Manager
    if operator_manager:
        try:
            # We need to calculate the delta since add_funds takes an amount
            current = operator_manager.get_wallet_balance(user_id)
            delta = new_balance - current
            if delta > 0:
                if operator_manager.add_funds(user_id, delta):
                    success = True
            elif delta < 0:
                if operator_manager.deduct_funds(user_id, abs(delta)):
                    success = True
            else:
                success = True # no change
        except Exception:
            pass

    return success

# Import OperatorSessionManager for centralized auth
try:
    from operator_session_manager import get_session_manager, OperatorRole
    HAS_OPERATOR_MANAGER = True
except ImportError:
    HAS_OPERATOR_MANAGER = False

try:
    from flask import Flask, jsonify, request, send_from_directory, abort
    from flask_cors import CORS
except ImportError:
    print("[ORCHESTRATOR] Flask not found. Install with: pip install flask flask-cors")
    sys.exit(1)

try:
    from simple_websocket import Server as WSServer, ConnectionClosed as WSConnectionClosed
    HAS_SIMPLE_WS = True
except ImportError:
    HAS_SIMPLE_WS = False

try:
    import websocket as ws_client
    HAS_WS_CLIENT = True
except ImportError:
    HAS_WS_CLIENT = False

try:
    from zeroconf import Zeroconf, ServiceInfo
    HAS_ZEROCONF = True
except ImportError:
    HAS_ZEROCONF = False

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [ORCHESTRATOR] %(levelname)s %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger('scythe_orchestrator')

# ---------------------------------------------------------------------------
# Flask App
# ---------------------------------------------------------------------------
app = Flask(__name__, static_folder='.', static_url_path='/static')
CORS(app)

# ---------------------------------------------------------------------------
# Instance Registry
# ---------------------------------------------------------------------------
# { instance_id: { id, name, port, pid, created, status, last_health, info } }
_instances = {}
_registry_lock = threading.Lock()
_boot_time = datetime.now(timezone.utc)

# Path to the API server script
_SCRIPT_DIR = Path(__file__).resolve().parent
_API_SERVER = _SCRIPT_DIR / 'rf_scythe_api_server.py'
_GRPC_SERVER = _SCRIPT_DIR / 'scythe_grpc_server.py'

# Stream endpoint URLs — set by CLI args in main(), forwarded to every spawned instance
_STREAM_RELAY_URL: str = 'ws://localhost:8765/ws'
_MCP_WS_URL: str = 'ws://localhost:8766/ws'
_TAKML_URL: str = 'http://localhost:8234'
_EVE_STREAM_WS_URL: str = 'ws://localhost:8081/ws'
_EVE_STREAM_HTTP_URL: str = 'http://localhost:8081'
_parsed_args = None  # set in main(); used to propagate --ollama-url to subprocesses
_ORCHESTRATOR_PORT: int = 5001  # set in main() — stable loopback URL for child processes

# ---------------------------------------------------------------------------
# Shared Session Registry
# ---------------------------------------------------------------------------
# Populated when instances call POST /api/scythe/sessions/register.
# Used by scythe_grpc_server.py's TokenAuthInterceptor via
# GET /api/scythe/sessions/validate.
#
# { token: { instance_id, operator_id, expires_at (ISO str) } }
_shared_sessions: dict = {}
_sessions_lock = threading.Lock()

# Centralized Operator Manager for Unified Login
operator_manager = None

# Internal shared secret — generated once at startup, passed to every
# spawned instance and to the gRPC server subprocess.
# Requests carrying X-Internal-Token: <value> bypass user-session auth.
_INTERNAL_TOKEN: str = secrets.token_hex(32)

if HAS_OPERATOR_MANAGER:
    try:
        # Use a stable DB path for the orchestrator
        db_path = str(_SCRIPT_DIR / "operator_sessions.db")
        operator_manager = get_session_manager(db_path=db_path, internal_token=_INTERNAL_TOKEN)
        log.info(f"[Orchestrator] Centralized OperatorManager initialized at {db_path}")
        log.info(f"[Orchestrator] Operator count: {len(operator_manager.operators) if operator_manager else 0}")
    except Exception as e:
        log.error(f"[Orchestrator] Failed to initialize OperatorManager: {e}", exc_info=True)
else:
    log.warning("[Orchestrator] OperatorManager import not available - using FusionAuth only")

# Co-managed service processes (started by orchestrator at boot)
_SERVICE_PROCS: dict = {}   # name → subprocess.Popen
_SERVICE_LOCK = threading.Lock()

# Maps URL → (script_path, extra_args_builder)
# extra_args_builder receives the parsed URL and returns a list of CLI args
_SERVICE_MAP = {
    'stream_relay': {
        'script': 'ws_ingest.py',
        'args': lambda u: ['--host', '0.0.0.0', '--port', str(u.port or 8765)],
    },
    'mcp_ws': {
        'script': 'rf_voxel_processor.py',
        'args': lambda u: [],   # rf_voxel_processor uses uvicorn; port is hardcoded in the file
    },
    'voxel_stream': {
        'script': 'voxel_stream_engine.py',
        'args': lambda u: [
            '--port', str(u.port or 9001),
            '--orchestrator-url', f'http://127.0.0.1:{_parsed_args.port if _parsed_args else 5001}',
            '--internal-token', _INTERNAL_TOKEN,
        ],
    },
}


def _launch_services(auto: bool = True) -> None:
    """Optionally launch co-managed WS services alongside the orchestrator."""
    if not auto:
        return
    entries = [
        ('stream_relay', _STREAM_RELAY_URL),
        ('mcp_ws',       _MCP_WS_URL),
        ('voxel_stream', 'ws://127.0.0.1:9001'),
    ]
    for name, url in entries:
        info   = _SERVICE_MAP[name]
        script = _SCRIPT_DIR / info['script']
        if not script.exists():
            log.warning(f"[services] {info['script']} not found — skipping {name}")
            continue
        parsed = urlparse(url)
        host   = parsed.hostname or 'localhost'
        port   = parsed.port or (8765 if name == 'stream_relay' else 8766)
        # Skip launch if something is already listening on that port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.3)
            if probe.connect_ex((host if host not in ('0.0.0.0', '::') else '127.0.0.1', port)) == 0:
                log.info(f"[services] {name} already up on :{port} — skipping launch")
                continue
        extra = info['args'](parsed)
        cmd = [sys.executable, str(script)] + extra
        log_path = _SCRIPT_DIR / f'{name}.log'
        log_fh   = open(log_path, 'a', buffering=1)
        try:
            proc = subprocess.Popen(cmd, stdout=log_fh, stderr=log_fh,
                                    cwd=str(_SCRIPT_DIR), start_new_session=True,
                                    env={**os.environ, 'OLLAMA_URL': _parsed_args.ollama_url})
            with _SERVICE_LOCK:
                _SERVICE_PROCS[name] = proc
            log.info(f"[services] Launched {name} (PID {proc.pid}) → {url}  log={log_path}")
        except Exception as exc:
            log.error(f"[services] Failed to launch {name}: {exc}")


def _stop_services() -> None:
    """Terminate all co-managed services."""
    with _SERVICE_LOCK:
        for name, proc in _SERVICE_PROCS.items():
            if proc.poll() is None:
                try:
                    proc.terminate()
                    log.info(f"[services] Terminated {name} (PID {proc.pid})")
                except Exception:
                    pass


def _allocate_port():
    """Reserve a free port and return (port, socket).

    The caller MUST close the socket immediately before subprocess.Popen() so
    the child can bind the same port.  Keeping the socket alive until that
    moment eliminates the TOCTOU window between allocation and spawn.
    SO_REUSEADDR is set before bind so the child can re-bind the port even if
    the orchestrator socket hasn't fully released yet (e.g. TIME_WAIT state).
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(('', 0))
    return s.getsockname()[1], s


def _generate_instance_id():
    """Generate a short unique instance identifier."""
    return f"scythe-{uuid.uuid4().hex[:8]}"


def _health_check(instance):
    """Poll /api/instance/info on a live instance. Returns dict or None."""
    import urllib.request
    import urllib.error
    port = instance.get('port', 0)
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/instance/info", method='GET')
        with urllib.request.urlopen(req, timeout=3) as resp:
            info = json.loads(resp.read().decode())
    except Exception:
        return None

    authority = None
    try:
        req2 = urllib.request.Request(f"http://127.0.0.1:{port}/api/authority/state", method='GET')
        with urllib.request.urlopen(req2, timeout=3) as resp2:
            authority = json.loads(resp2.read().decode())
    except Exception:
        authority = None

    return {'info': info, 'authority': authority}


def _health_loop():
    """Background thread: periodically health-check all instances."""
    while True:
        time.sleep(5)  # was 15 — faster detection of newly-ready instances
        with _registry_lock:
            ids = list(_instances.keys())
        for iid in ids:
            with _registry_lock:
                inst = _instances.get(iid)
                if not inst:
                    continue
                proc = inst.get('process')
                if proc and proc.poll() is not None:
                    inst['status'] = 'dead'
                    inst['last_health'] = datetime.now(timezone.utc).isoformat()
                    continue
            info = _health_check(inst)
            with _registry_lock:
                inst = _instances.get(iid)
                if not inst:
                    continue
                if info:
                    inst['status'] = 'running'
                    inst['last_health'] = datetime.now(timezone.utc).isoformat()
                    inst['info'] = info.get('info', {})
                    if info.get('authority') is not None:
                        inst['authority'] = info.get('authority')
                else:
                    # Check if process is still alive
                    pid = inst.get('pid')
                    if pid:
                        try:
                            os.kill(pid, 0)
                            inst['status'] = 'starting'
                        except OSError:
                            inst['status'] = 'dead'
                    else:
                        inst['status'] = 'unknown'


# Start health monitor
_health_thread = threading.Thread(target=_health_loop, daemon=True)
_health_thread.start()


# ---------------------------------------------------------------------------
# Routes — Static / Home
# ---------------------------------------------------------------------------
@app.route('/')
def index():
    """Serve the SCYTHE home page."""
    return send_from_directory(str(_SCRIPT_DIR), 'rf_scythe_home.html')


def _public_client_base() -> str:
    """Scheme + host as seen by the browser (Tailscale / reverse-proxy safe)."""
    proto = request.headers.get('X-Forwarded-Proto') or (
        'https' if request.is_secure else 'http'
    )
    host = request.headers.get('X-Forwarded-Host') or request.host
    return f'{proto}://{host}'.rstrip('/')


_HOP_BY_HOP_RESPONSE = {
    'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
    'te', 'trailers', 'transfer-encoding', 'upgrade',
}


def _proxy_http_to_instance(instance_id: str, subpath: str):
    """
    Stream HTTP to 127.0.0.1:<instance port>. Adds forwarded headers so the
    child's /api/bootstrap.js can emit correct api_base + socket.io path.
    """
    import urllib.error as _uerr
    import urllib.request as _ureq

    with _registry_lock:
        inst = _instances.get(instance_id)
    if not inst:
        abort(404, description=f'Unknown SCYTHE instance: {instance_id}')
    port = int(inst['port'])

    path = '/' + subpath.lstrip('/') if subpath else '/'
    qs = request.query_string.decode('latin1') if request.query_string else ''
    backend_url = f'http://127.0.0.1:{port}{path}'
    if qs:
        backend_url += '?' + qs

    prefix = f'/scythe/i/{instance_id}'
    fwd_headers = []
    for key, val in request.headers:
        lk = key.lower()
        if lk in ('host', 'content-length'):
            continue
        if lk == 'connection' and val.lower() == 'upgrade':
            # Raw WS upgrade through WSGI proxy is unreliable; Socket.IO falls back to polling.
            continue
        fwd_headers.append((key, val))

    client_host = request.headers.get('Host', request.host)
    fwd_headers.append(('Host', client_host))
    fwd_headers.append(('X-Forwarded-For', request.remote_addr or ''))
    fwd_headers.append(('X-Forwarded-Host', client_host))
    fwd_headers.append(('X-Forwarded-Proto', _public_client_base().split('://', 1)[0]))
    fwd_headers.append(('X-Forwarded-Prefix', prefix))
    fwd_headers.append(('X-SCYTHE-PUBLIC-BASE', _public_client_base() + prefix))

    body = None
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        raw = request.get_data()
        if raw:
            body = raw

    hdr = dict(fwd_headers)
    if body is not None:
        hdr['Content-Length'] = str(len(body))

    req = _ureq.Request(backend_url, data=body, headers=hdr, method=request.method)

    try:
        upstream = _ureq.urlopen(req, timeout=3600)
    except _uerr.HTTPError as e:
        upstream = e
    except Exception as e:
        log.warning(f'[proxy] instance {instance_id} upstream error: {e}')
        abort(502, description=f'Upstream unreachable: {e}')

    resp_headers = []
    for k, vals in upstream.headers.items():
        lk = k.lower()
        if lk in _HOP_BY_HOP_RESPONSE:
            continue
        if lk == 'content-encoding' and request.method == 'HEAD':
            continue
        if isinstance(vals, str):
            resp_headers.append((k, vals))
        else:
            for v in vals:
                resp_headers.append((k, v))

    def stream_body():
        try:
            while True:
                chunk = upstream.read(65536)
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                upstream.close()
            except Exception:
                pass

    from flask import Response
    return Response(
        stream_body(),
        status=upstream.status,
        headers=resp_headers,
        direct_passthrough=True,
    )


@app.route('/scythe/i/<instance_id>', defaults={'subpath': ''}, methods=[
    'GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS',
])
@app.route('/scythe/i/<instance_id>/', defaults={'subpath': ''}, methods=[
    'GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS',
])
@app.route('/scythe/i/<instance_id>/<path:subpath>', methods=[
    'GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS',
])
def scythe_instance_http_proxy(instance_id, subpath):
    """Stable URL path to an ephemeral instance (Funnel / reverse-proxy friendly)."""
    return _proxy_http_to_instance(instance_id, subpath)


@app.route('/proxy/<int:port>/ws', defaults={'subpath': ''})
@app.route('/proxy/<int:port>/ws/<path:subpath>')
def generic_ws_proxy(port, subpath):
    """WebSocket tunnel to internal ports. Enables binary streams (9001, 8765)
    to be reached through the orchestrator's secure origin.
    """
    if not HAS_SIMPLE_WS or not HAS_WS_CLIENT:
        abort(501, description="WebSocket proxy dependencies (simple-websocket, websocket-client) missing.")

    # Safety: restrict to known internal service ports
    ALLOWED_PORTS = {8080, 8081, 8765, 8766, 8234, 9001, 50051}
    if port not in ALLOWED_PORTS and not (50000 <= port <= 60000):
        abort(403, description=f"WS Proxy to port {port} is not allowed.")

    if request.headers.get('Upgrade', '').lower() != 'websocket':
        # If not a WS upgrade, fall back to standard HTTP proxy (handles gRPC-Web POSTs)
        return generic_port_proxy(port, subpath)

    ws_server = WSServer(request.environ)

    # Connect to local backend. The public route reserves `/ws` as the proxy
    # marker; when no extra subpath is supplied, preserve the common backend
    # `/ws` endpoint instead of forwarding to `/`.
    backend_path = '/' + subpath.lstrip('/') if subpath else '/ws'
    backend_url = f'ws://127.0.0.1:{port}{backend_path}'
    if request.query_string:
        backend_url += '?' + request.query_string.decode('latin1')

    try:
        remote_ws = ws_client.create_connection(backend_url, timeout=5)

        def relay_to_client():
            try:
                while True:
                    data = remote_ws.recv()
                    ws_server.send(data)
            except Exception:
                pass
            finally:
                try: ws_server.close()
                except: pass

        def relay_to_backend():
            try:
                while True:
                    data = ws_server.receive()
                    remote_ws.send(data)
            except Exception:
                pass
            finally:
                try: remote_ws.close()
                except: pass

        # Launch relay threads
        t1 = threading.Thread(target=relay_to_client, daemon=True)
        t2 = threading.Thread(target=relay_to_backend, daemon=True)
        t1.start()
        t2.start()

        # Wait for threads to finish (request stays alive)
        while t1.is_alive() or t2.is_alive():
            time.sleep(0.5)

    except Exception as e:
        log.warning(f'[ws-proxy] Failed to connect to {backend_url}: {e}')
        abort(502, description=f"Upstream WS at {port} unreachable")

    return '', 101


@app.route('/proxy/<int:port>', defaults={'subpath': ''}, methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS'])
@app.route('/proxy/<int:port>/', defaults={'subpath': ''}, methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS'])
@app.route('/proxy/<int:port>/<path:subpath>', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS'])
def generic_port_proxy(port, subpath):
    """Generic proxy to a local port. Enables browser access to internal streams (8765, 8766, etc)
    by routing them through the orchestrator's secure origin.
    """
    import urllib.error as _uerr
    import urllib.request as _ureq

    # Safety: restrict to known internal service ports
    ALLOWED_PORTS = {8080, 8081, 8765, 8766, 8234, 9001, 50051}
    if port not in ALLOWED_PORTS and not (50000 <= port <= 60000):
        abort(403, description=f"Proxy to port {port} is not allowed.")

    path = '/' + subpath.lstrip('/') if subpath else '/'
    qs = request.query_string.decode('latin1') if request.query_string else ''
    backend_url = f'http://127.0.0.1:{port}{path}'
    if qs:
        backend_url += '?' + qs

    fwd_headers = []
    for key, val in request.headers:
        lk = key.lower()
        if lk in ('host', 'content-length'):
            continue
        fwd_headers.append((key, val))

    client_host = request.headers.get('Host', request.host)
    fwd_headers.append(('Host', client_host))
    fwd_headers.append(('X-Forwarded-For', request.remote_addr or ''))
    fwd_headers.append(('X-Forwarded-Host', client_host))
    fwd_headers.append(('X-Forwarded-Proto', _public_client_base().split('://', 1)[0]))
    fwd_headers.append(('X-Forwarded-Prefix', f'/proxy/{port}'))

    body = None
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        raw = request.get_data()
        if raw:
            body = raw

    hdr = dict(fwd_headers)
    if body is not None:
        hdr['Content-Length'] = str(len(body))

    req = _ureq.Request(backend_url, data=body, headers=hdr, method=request.method)

    try:
        upstream = _ureq.urlopen(req, timeout=60)
    except _uerr.HTTPError as e:
        upstream = e
    except Exception as e:
        log.warning(f'[proxy] port {port} upstream error: {e}')
        abort(502, description=f'Service on port {port} unreachable: {e}')

    resp_headers = []
    for k, vals in upstream.headers.items():
        lk = k.lower()
        if lk in _HOP_BY_HOP_RESPONSE:
            continue
        if lk == 'content-encoding' and request.method == 'HEAD':
            continue
        if isinstance(vals, str):
            resp_headers.append((k, vals))
        else:
            for v in vals:
                resp_headers.append((k, v))

    def stream_body():
        try:
            while True:
                chunk = upstream.read(65536)
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                upstream.close()
            except Exception:
                pass

    from flask import Response
    return Response(
        stream_body(),
        status=upstream.status,
        headers=resp_headers,
        direct_passthrough=True,
    )


@app.route('/wordpress', defaults={'subpath': ''}, methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS'])
@app.route('/wordpress/', defaults={'subpath': ''}, methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS'])
@app.route('/wordpress/<path:subpath>', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS'])
def wordpress_proxy(subpath):
    """Proxy requests to the local WordPress container on port 8080."""
    import urllib.error as _uerr
    import urllib.request as _ureq

    from urllib.parse import quote
    path = '/' + subpath.lstrip('/') if subpath else '/'
    # Properly encode path to handle non-ASCII characters
    encoded_path = quote(path, safe='/')
    qs = request.query_string.decode('latin1') if request.query_string else ''
    backend_url = f'http://127.0.0.1:8080{encoded_path}'
    if qs:
        backend_url += '?' + qs

    fwd_headers = []
    for key, val in request.headers:
        lk = key.lower()
        if lk in ('host', 'content-length'):
            continue
        fwd_headers.append((key, val))

    client_host = request.headers.get('Host', request.host)
    fwd_headers.append(('Host', client_host))
    fwd_headers.append(('X-Forwarded-For', request.remote_addr or ''))
    fwd_headers.append(('X-Forwarded-Host', client_host))
    fwd_headers.append(('X-Forwarded-Proto', _public_client_base().split('://', 1)[0]))
    fwd_headers.append(('X-Forwarded-Prefix', '/wordpress'))

    body = None
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        raw = request.get_data()
        if raw:
            body = raw

    hdr = dict(fwd_headers)
    if body is not None:
        hdr['Content-Length'] = str(len(body))

    req = _ureq.Request(backend_url, data=body, headers=hdr, method=request.method)

    try:
        upstream = _ureq.urlopen(req, timeout=60)
    except _uerr.HTTPError as e:
        upstream = e
    except Exception as e:
        log.warning(f'[proxy] wordpress upstream error: {e}')
        abort(502, description=f'WordPress unreachable: {e}')

    resp_headers = []
    for k, vals in upstream.headers.items():
        lk = k.lower()
        if lk in _HOP_BY_HOP_RESPONSE:
            continue
        if lk == 'content-encoding' and request.method == 'HEAD':
            continue
        if isinstance(vals, str):
            resp_headers.append((k, vals))
        else:
            for v in vals:
                resp_headers.append((k, v))

    def stream_body():
        try:
            while True:
                chunk = upstream.read(65536)
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                upstream.close()
            except Exception:
                pass

    from flask import Response
    return Response(
        stream_body(),
        status=upstream.status,
        headers=resp_headers,
        direct_passthrough=True,
    )


@app.route('/api/bootstrap.js')
@app.route('/scythe/i/<instance_id>/api/bootstrap.js')
def bootstrap_js(instance_id=None):
    """
    Synchronous bootstrap config injected before any other JS.
    Supports global orchestrator bootstrap and instance-scoped bootstrap.
    """
    from flask import Response, request as _req
    public_base = _public_client_base()
    host = _req.host
    scheme = public_base.split('://', 1)[0]
    path_prefix = f'/scythe/i/{instance_id}' if instance_id else ''
    api_base = public_base + path_prefix
    ws_base = public_base.replace('http://', 'ws://', 1).replace('https://', 'wss://', 1)

    def _proxy_ws(url):
        if not path_prefix:
            return url
        try:
            parsed = urlparse(url)
            if not parsed.port:
                return url
            ws_proto = 'wss' if scheme == 'https' else 'ws'
            path = (parsed.path or '').strip('/')
            suffix = '' if not path or path == 'ws' else f'/{path}'
            return f'{ws_proto}://{host}/proxy/{parsed.port}/ws{suffix}'
        except Exception:
            return url

    inst_info = {}
    if instance_id:
        with _registry_lock:
            inst_entry = _instances.get(instance_id, {})
            inst_info = inst_entry.get('info', inst_entry)

    js = (
        'window.__SCYTHE_BOOTSTRAP__ = '
        + json.dumps({
            'api_base':         api_base,
            'ws_base':          ws_base,
            'path_prefix':      path_prefix,
            'socketio_path':    f'{path_prefix}/socket.io' if path_prefix else '/socket.io',
            'instance_id':      instance_id or '',
            'runtime_role':     'instance' if instance_id else 'broker',
            'stream_relay':     _proxy_ws(inst_info.get('stream_relay_url') or _STREAM_RELAY_URL),
            'mcp_ws':           _proxy_ws(inst_info.get('mcp_ws_url') or _MCP_WS_URL),
            'takml':            inst_info.get('takml_url') or _TAKML_URL,
            'eve_stream_ws': inst_info.get('eve_stream_ws_url') or _EVE_STREAM_WS_URL,
            'eve_stream_http': inst_info.get('eve_stream_http_url') or _EVE_STREAM_HTTP_URL,
            'voxel_stream':     _proxy_ws('ws://localhost:9001/stream'),
        })
        + ';'
        + 'window.SCYTHE_API_BASE = window.__SCYTHE_BOOTSTRAP__.api_base;'
        + 'console.info("[BOOTSTRAP] orchestrator config injected:", window.__SCYTHE_BOOTSTRAP__);'
    )
    return Response(js, mimetype='application/javascript',
                    headers={'Cache-Control': 'no-store'})


@app.route('/<path:filename>')
def serve_static(filename):
    """Serve static assets from the NerfEngine directory."""
    return send_from_directory(str(_SCRIPT_DIR), filename)


# ---------------------------------------------------------------------------
# Routes — Instance Management
# ---------------------------------------------------------------------------
@app.route('/api/scythe/instances', methods=['GET'])
def list_instances():
    """List all registered instances with latest health info."""
    with _registry_lock:
        instances = []
        for iid, inst in _instances.items():
            instances.append({
                'id': inst['id'],
                'name': inst.get('name', inst['id']),
                'port': inst['port'],
                'pid': inst.get('pid'),
                'created': inst.get('created', ''),
                'status': inst.get('status', 'unknown'),
                'socket_io_ready': bool(inst.get('socket_io_ready')),
                'last_health': inst.get('last_health'),
                'info': inst.get('info', {}),
                'authority': inst.get('authority', {}),
                'log_path': inst.get('log_path'),
            })
    return jsonify({
        'instances': instances,
        'count': len(instances),
        'orchestrator_uptime': (datetime.now(timezone.utc) - _boot_time).total_seconds(),
    })


@app.route('/api/scythe/instances/new', methods=['POST'])
def spawn_instance():
    """Spawn a new isolated SCYTHE server instance."""
    body = request.get_json(silent=True) or {}
    name = body.get('name', '').strip()

    instance_id = _generate_instance_id()
    port, _reserved_sock = _allocate_port()
    if name:
        display_name = name
    else:
        display_name = f"SCYTHE-{port}"

    # Create isolated data directory for this instance
    instance_data_dir = _SCRIPT_DIR / 'instances' / instance_id
    instance_data_dir.mkdir(parents=True, exist_ok=True)
    log.info(f"  Data directory: {instance_data_dir}")

    # Determine orchestrator URL for child registration (always loopback)
    orchestrator_url = f'http://127.0.0.1:{_ORCHESTRATOR_PORT}'

    # Build launch command
    cmd = [
        sys.executable,
        str(_API_SERVER),
        '--port', str(port),
        '--instance-id', instance_id,
        '--orchestrator-url', orchestrator_url,
        '--data-dir', str(instance_data_dir),
        '--stream-relay-url', _STREAM_RELAY_URL,
        '--mcp-ws-url', _MCP_WS_URL,
        '--takml-url', _TAKML_URL,
        '--eve-stream-ws-url', _EVE_STREAM_WS_URL,
        '--eve-stream-http-url', _EVE_STREAM_HTTP_URL,
        '--internal-token', _INTERNAL_TOKEN,
    ]

    log.info(f"Spawning instance '{display_name}' (id={instance_id}) on port {port}")
    log.info(f"  Command: {' '.join(cmd)}")

    # Stream child stdout/stderr into per-instance log to avoid pipe backpressure
    log_file_path = instance_data_dir / 'api_server.log'
    log_file = open(log_file_path, 'a', buffering=1)

    try:
        # Release reserved socket immediately before spawning so the child can bind
        # to the same port.  The gap between close() and Popen() is microseconds;
        # SO_REUSEADDR ensures the child can bind even in TIME_WAIT state.
        _reserved_sock.close()
        proc = subprocess.Popen(
            cmd,
            stdout=log_file,
            stderr=log_file,
            cwd=str(_SCRIPT_DIR),
            start_new_session=True,  # Detach from orchestrator's process group
        )
    except Exception as e:
        log.error(f"Failed to spawn instance: {e}")
        return jsonify({'error': str(e)}), 500

    # Register in registry
    record = {
        'id': instance_id,
        'name': display_name,
        'port': port,
        'pid': proc.pid,
        'created': datetime.now(timezone.utc).isoformat(),
        'status': 'starting',
        'last_health': None,
        'info': {},
        'authority': {},
        'process': proc,  # Keep ref for cleanup
        'log_path': str(log_file_path),
    }
    with _registry_lock:
        _instances[instance_id] = record

    log.info(f"Instance '{display_name}' spawned — PID {proc.pid}, port {port}")

    pub = _public_client_base()
    proxied_base = f'{pub}/scythe/i/{instance_id}'
    viz_url = f'{proxied_base}/command-ops-visualization.html'

    return jsonify({
        'instance_id': instance_id,
        'name': display_name,
        'port': port,
        'pid': proc.pid,
        'status': 'starting',
        'url': proxied_base + '/',
        'visualization_url': viz_url,
        # LAN/direct-child debugging only — not reachable via Tailscale Funnel
        'direct_local_url': f'http://127.0.0.1:{port}/command-ops-visualization.html',
    }), 201


@app.route('/api/scythe/instances/register', methods=['POST'])
def register_instance():
    """Endpoint for child servers to self-register on startup."""
    body = request.get_json(silent=True) or {}
    instance_id = body.get('instance_id', '')
    port = body.get('port', 0)

    if not instance_id:
        return jsonify({'error': 'instance_id required'}), 400

    with _registry_lock:
        if instance_id in _instances:
            # Update existing record
            sio_ready = body.get('socket_io_ready', False)
            new_status = 'ready' if sio_ready else _instances[instance_id].get('status', 'running')
            _instances[instance_id]['status'] = new_status
            _instances[instance_id]['last_health'] = datetime.now(timezone.utc).isoformat()
            _instances[instance_id]['port'] = port or _instances[instance_id].get('port', 0)
            if sio_ready:
                _instances[instance_id]['socket_io_ready'] = True
            log.info(f"Instance '{instance_id}' registered (update) status={new_status} port={port}")
        else:
            # New external registration (instance started outside orchestrator)
            _instances[instance_id] = {
                'id': instance_id,
                'name': body.get('name', instance_id),
                'port': port,
                'pid': body.get('pid'),
                'created': datetime.now(timezone.utc).isoformat(),
                'status': 'running',
                'socket_io_ready': False,
                'last_health': datetime.now(timezone.utc).isoformat(),
                'info': {},
            }
            log.info(f"Instance '{instance_id}' registered (new) on port {port}")

    return jsonify({'status': 'registered', 'instance_id': instance_id})


@app.route('/api/scythe/ready', methods=['GET'])
def get_ready_instance():
    """Return the first instance that has confirmed Socket.IO is accepting connections.

    Query params:
      ?wait=1   — poll up to 10 s (300 ms steps) before returning 503
      ?any=1    — fall back to any running instance if no socket_io_ready one found

    Clients should call this instead of guessing from /api/scythe/instances.
    """
    wait  = request.args.get('wait',  '0') not in ('0', 'false', '')
    allow_any = request.args.get('any', '0') not in ('0', 'false', '')

    deadline = time.time() + (10 if wait else 0)

    while True:
        with _registry_lock:
            ready = [
                i for i in _instances.values()
                if i.get('socket_io_ready') and i.get('status') in ('ready', 'running')
            ]
            fallback = [
                i for i in _instances.values()
                if i.get('status') in ('ready', 'running')
            ] if allow_any else []

        if ready:
            inst = ready[0]
            return jsonify({
                'instance_id': inst['id'],
                'url': inst.get('url', f"http://127.0.0.1:{inst['port']}"),
                'port': inst['port'],
                'socket_io_ready': True,
            })
        if allow_any and fallback:
            inst = fallback[0]
            return jsonify({
                'instance_id': inst['id'],
                'url': inst.get('url', f"http://127.0.0.1:{inst['port']}"),
                'port': inst['port'],
                'socket_io_ready': False,
            })

        if time.time() >= deadline:
            return jsonify({'error': 'no_ready_instance', 'instances': len(_instances)}), 503

        time.sleep(0.3)


@app.route('/api/scythe/instances/<instance_id>', methods=['DELETE'])
def kill_instance(instance_id):
    """Terminate and remove an instance."""
    with _registry_lock:
        inst = _instances.get(instance_id)
        if not inst:
            return jsonify({'error': 'Instance not found'}), 404

    pid = inst.get('pid')
    proc = inst.get('process')
    name = inst.get('name', instance_id)

    # Try graceful shutdown, then force kill
    killed = False
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=5)
            killed = True
        except subprocess.TimeoutExpired:
            proc.kill()
            killed = True
        except Exception as e:
            log.warning(f"Error terminating process for {instance_id}: {e}")
    elif pid:
        try:
            os.kill(pid, signal.SIGTERM)
            killed = True
        except OSError:
            pass

    with _registry_lock:
        _instances.pop(instance_id, None)

    log.info(f"Instance '{name}' (id={instance_id}) terminated — killed={killed}")
    return jsonify({
        'status': 'terminated',
        'instance_id': instance_id,
        'name': name,
        'killed': killed,
    })


@app.route('/api/scythe/instances/<instance_id>/rename', methods=['POST'])
def rename_instance(instance_id):
    """Rename an instance."""
    body = request.get_json(silent=True) or {}
    new_name = body.get('name', '').strip()
    if not new_name:
        return jsonify({'error': 'name required'}), 400

    with _registry_lock:
        inst = _instances.get(instance_id)
        if not inst:
            return jsonify({'error': 'Instance not found'}), 404
        old_name = inst.get('name', instance_id)
        inst['name'] = new_name

    log.info(f"Instance '{instance_id}' renamed: '{old_name}' → '{new_name}'")
    return jsonify({'status': 'renamed', 'instance_id': instance_id, 'name': new_name})


@app.route('/api/scythe/health', methods=['GET'])
def orchestrator_health():
    """Orchestrator health check."""
    with _registry_lock:
        total = len(_instances)
        running = sum(1 for i in _instances.values() if i.get('status') == 'running')
        dead = sum(1 for i in _instances.values() if i.get('status') == 'dead')

    return jsonify({
        'status': 'operational',
        'uptime_seconds': (datetime.now(timezone.utc) - _boot_time).total_seconds(),
        'uptime_s': (datetime.now(timezone.utc) - _boot_time).total_seconds(),
        'total_instances': total,
        'running_instances': running,
        'dead_instances': dead,
        'python': sys.version,
        'api_server_path': str(_API_SERVER),
        'api_server_exists': _API_SERVER.exists(),
    })


# ---------------------------------------------------------------------------
# Shared Session Registry — used by gRPC TokenAuthInterceptor
# ---------------------------------------------------------------------------

def _require_internal_token():
    """Return True if the request carries the correct X-Internal-Token header.
    Uses constant-time comparison to prevent timing oracle attacks."""
    incoming = request.headers.get('X-Internal-Token', '')
    return hmac.compare_digest(incoming, _INTERNAL_TOKEN)


@app.route('/api/scythe/sessions/register', methods=['POST'])
def sessions_register():
    """Called by instances when a new operator session is created.

    Body: { token, instance_id, operator_id, expires_at }
    Stores the token in _shared_sessions so the gRPC interceptor can validate it.
    Requires X-Internal-Token header.
    """
    if not _require_internal_token():
        return jsonify({'error': 'Forbidden'}), 403

    data = request.get_json(silent=True) or {}
    token = data.get('token', '').strip()
    instance_id = data.get('instance_id', '').strip()
    operator_id = data.get('operator_id', '').strip()
    expires_at = data.get('expires_at', '')

    if not token or not instance_id:
        return jsonify({'error': 'token and instance_id are required'}), 400

    with _sessions_lock:
        _shared_sessions[token] = {
            'instance_id': instance_id,
            'operator_id': operator_id,
            'expires_at': expires_at,
        }

    log.debug(f'[sessions] Registered token for instance {instance_id} operator {operator_id}')
    return jsonify({'status': 'ok'})


@app.route('/api/scythe/sessions/revoke', methods=['POST'])
def sessions_revoke():
    """Called by instances on logout.

    Body: { token }
    Requires X-Internal-Token header.
    """
    if not _require_internal_token():
        return jsonify({'error': 'Forbidden'}), 403

    data = request.get_json(silent=True) or {}
    token = data.get('token', '').strip()
    if not token:
        return jsonify({'error': 'token required'}), 400

    with _sessions_lock:
        _shared_sessions.pop(token, None)

    return jsonify({'status': 'ok'})


@app.route('/api/scythe/sessions/validate', methods=['GET'])
def sessions_validate():
    """Validate a session token.  Used by scythe_grpc_server.py's interceptor.

    Query param: ?token=<value>
    Requires X-Internal-Token header.
    Returns 200 + session dict on success, 401 on invalid/expired.
    """
    if not _require_internal_token():
        return jsonify({'error': 'Forbidden'}), 403

    token = request.headers.get('X-Validate-Token', '').strip()
    if not token:
        # Fallback: some callers may still use query param during migration
        token = request.args.get('token', '').strip()
    if not token:
        return jsonify({'error': 'token required'}), 400

    now_utc = datetime.now(timezone.utc)
    with _sessions_lock:
        session = _shared_sessions.get(token)

    if not session:
        return jsonify({'error': 'invalid token'}), 401

    # Check expiry — parse properly to avoid "Z" vs "+00:00" string comparison bugs
    expires_at_str = session.get('expires_at', '')
    if expires_at_str:
        try:
            exp = datetime.fromisoformat(expires_at_str.replace('Z', '+00:00'))
            if now_utc >= exp:
                with _sessions_lock:
                    _shared_sessions.pop(token, None)
                return jsonify({'error': 'token expired'}), 401
        except (ValueError, TypeError):
            pass  # unparseable expiry → treat as no expiry

    return jsonify(session)




@app.route('/api/scythe/authority/summary', methods=['GET'])
def authority_summary():
    """Aggregate authority state for all instances (best effort)."""
    with _registry_lock:
        snapshot = []
        for iid, inst in _instances.items():
            snapshot.append({
                'id': iid,
                'name': inst.get('name', iid),
                'port': inst.get('port'),
                'status': inst.get('status', 'unknown'),
                'authority': inst.get('authority'),
            })
    return jsonify({'instances': snapshot, 'count': len(snapshot)})


# ---------------------------------------------------------------------------
# Unified Authentication & Wallet Routes (Transitioned to User)
# ---------------------------------------------------------------------------

@app.route('/api/user/register', methods=['POST'])
@app.route('/api/operator/register', methods=['POST'])
def orchestrator_user_register():
    """Register a new user (FusionAuth aware)"""
    try:
        data = request.get_json() or {}
        callsign = data.get('callsign')
        email = data.get('email')
        password = data.get('password')
        role = data.get('role', 'operator')

        if not all([callsign, email, password]):
            return jsonify({'status': 'error', 'message': 'Missing required fields'}), 400

        # 1. Try FusionAuth Register if client is available
        if fa_client:
            user_request = {
                'sendSetPasswordEmail': False,
                'skipVerification': True,
                'user': {
                    'email': email,
                    'password': password,
                    'username': callsign,
                    'firstName': callsign,
                    'data': {
                        'wallet_balance': 0.0,
                        'role': role
                    }
                }
            }
            response = fa_client.create_user(user_request)
            if response.was_successful():
                return jsonify({
                    'status': 'ok',
                    'user': {
                        'user_id': response.success_response.get('user', {}).get('id'),
                        'callsign': callsign,
                        'email': email
                    }
                })
            else:
                return jsonify({'status': 'error', 'message': f'FusionAuth: {response.error_response}'}), 400

        # 2. Fallback to Legacy Operator Manager
        if operator_manager:
            role_map = {
                'observer': OperatorRole.OBSERVER,
                'operator': OperatorRole.OPERATOR,
                'supervisor': OperatorRole.SUPERVISOR,
                'admin': OperatorRole.ADMIN
            }
            operator_role = role_map.get(role, OperatorRole.OPERATOR)
            operator = operator_manager.register_operator(
                callsign=callsign, email=email, password=password, role=operator_role
            )
            if operator:
                return jsonify({'status': 'ok', 'user': operator.to_dict()})
            return jsonify({'status': 'error', 'message': 'Registration failed - user exists'}), 409

        return jsonify({'status': 'error', 'message': 'User management unavailable'}), 503
    except Exception as e:
        log.error(f"Registration error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/user/login', methods=['POST'])
@app.route('/api/operator/login', methods=['POST'])
def orchestrator_user_login():
    """Authenticate user and create global session (FusionAuth aware)"""
    try:
        data = request.get_json() or {}
        callsign = data.get('callsign')
        password = data.get('password')

        if not all([callsign, password]):
            return jsonify({'status': 'error', 'message': 'Missing callsign or password'}), 400

        # 1. Try FusionAuth Login if client is available
        if fa_client:
            login_request = {
                'loginId': callsign,
                'password': password
                # 'applicationId': ... # Optional if only one app
            }
            response = fa_client.login(login_request)
            if response.was_successful():
                fa_user = response.success_response.get('user', {})
                token = response.success_response.get('token') # This is the JWT

                # Sync balance to local DB if needed (or just use FusionAuth data)
                user_id = fa_user.get('id')

                return jsonify({
                    'status': 'ok',
                    'session': {'session_token': token, 'expires_at': ''},
                    'user': {
                        'user_id': user_id,
                        'callsign': fa_user.get('firstName') or fa_user.get('username') or callsign,
                        'wallet_balance': fa_user.get('data', {}).get('wallet_balance', 0.0)
                    }
                })

        # 2. Fallback to Legacy Operator Manager
        if operator_manager:
            session = operator_manager.authenticate(callsign, password)
            if session:
                operator = operator_manager.get_operator(session.operator_id)
                with _sessions_lock:
                    _shared_sessions[session.session_token] = {
                        'instance_id': 'orchestrator',
                        'operator_id': session.operator_id,
                        'expires_at': session.expires_at,
                    }
                return jsonify({
                    'status': 'ok',
                    'session': session.to_dict(),
                    'user': operator.to_dict() if operator else None
                })
            else:
                log.debug(f"Operator login failed for {callsign} (operator_manager.authenticate returned None)")
        else:
            log.warning("Operator Manager not available for login fallback")

        return jsonify({'status': 'error', 'message': 'Invalid credentials'}), 401
    except Exception as e:
        log.error(f"Login error: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/user/session', methods=['GET'])
@app.route('/api/operator/session', methods=['GET'])
def orchestrator_user_session():
    """Get current session info (JWT Validated)"""
    try:
        token = request.headers.get('X-Session-Token') or request.args.get('token')
        if not token:
            return jsonify({'status': 'error', 'message': 'No token provided'}), 400

        jwt_data = validate_jwt(token)
        if jwt_data and jwt_data.get('valid'):
            # Token is valid, return info
            return jsonify({
                'status': 'ok',
                'session': {'token': token},
                'user': {
                    'user_id': jwt_data.get('userId'),
                    'callsign': jwt_data.get('firstName') or jwt_data.get('sub') or 'Operator'
                }
            })
        return jsonify({'status': 'error', 'message': 'Invalid or expired session'}), 401
    except Exception as e:
        log.error(f"Session info error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/operator/issue-bootstrap', methods=['POST'])
def orchestrator_issue_bootstrap():
    """Generate a one-time bootstrap token for instance identity handoff."""
    try:
        token = request.headers.get('X-Session-Token') or request.args.get('token')
        data = request.get_json(silent=True) or {}
        instance_id = data.get('instance_id')

        if not token or not instance_id:
            return jsonify({'status': 'error', 'message': 'Missing session or instance_id'}), 400

        jwt_data = validate_jwt(token)
        if not jwt_data or not jwt_data.get('valid'):
            return jsonify({'status': 'error', 'message': 'Invalid session'}), 401

        user_id = jwt_data.get('userId')
        if not user_id:
            return jsonify({'status': 'error', 'message': 'Session missing operator ID'}), 401

        if not operator_manager:
            return jsonify({'status': 'error', 'message': 'Operator Manager not available'}), 503

        now = int(time.time())
        payload = {
            "sub": user_id,
            "instance_id": instance_id,
            "iss": "scythe-orchestrator",
            "aud": "scythe-instance",
            "iat": now,
            "exp": now + 300,
            "scope": "bootstrap",
        }
        bootstrap_token = jwt.encode(payload, _INTERNAL_TOKEN, algorithm="HS256")

        return jsonify({
            'status': 'ok',
            'bootstrap_token': bootstrap_token,
            'expires_in': 300
        })
    except Exception as e:
        log.error(f"Error issuing bootstrap token: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/user/wallet', methods=['GET'])
@app.route('/api/operator/wallet', methods=['GET'])
def orchestrator_user_wallet_balance():
    """Get user wallet balance from FusionAuth"""
    token = request.headers.get('X-Session-Token')
    if not token:
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401

    jwt_data = validate_jwt(token)
    if not jwt_data or not jwt_data.get('valid'):
        return jsonify({'status': 'error', 'message': 'Invalid session'}), 401

    user_id = jwt_data.get('userId')
    balance = get_user_wallet_balance(user_id)
    return jsonify({'status': 'ok', 'balance': balance})


@app.route('/api/user/wallet/add-funds', methods=['POST'])
@app.route('/api/operator/wallet/add-funds', methods=['POST'])
def orchestrator_user_wallet_add_funds():
    """Add funds to user wallet in FusionAuth"""
    token = request.headers.get('X-Session-Token')
    if not token:
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401

    jwt_data = validate_jwt(token)
    if not jwt_data or not jwt_data.get('valid'):
        return jsonify({'status': 'error', 'message': 'Invalid session'}), 401

    user_id = jwt_data.get('userId')
    data = request.get_json() or {}
    amount = float(data.get('amount', 0))

    current_balance = get_user_wallet_balance(user_id)
    new_balance = current_balance + amount

    if update_user_wallet_balance(user_id, new_balance):
        log.info(f"Wallet: Added ${amount} to {user_id}. New balance: ${new_balance}")
        return jsonify({
            'status': 'ok',
            'message': f'Successfully added ${amount:.2f}',
            'new_balance': new_balance
        })
    return jsonify({'status': 'error', 'message': 'Failed to update wallet'}), 400


@app.route('/api/operator/update', methods=['PUT'])
def orchestrator_user_update():
    """Update user profile (callsign, email, etc.) - JWT Validated"""
    token = request.headers.get('X-Session-Token') or request.args.get('token')
    if not token:
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401

    jwt_data = validate_jwt(token)
    if not jwt_data or not jwt_data.get('valid'):
        return jsonify({'status': 'error', 'message': 'Invalid session'}), 401

    user_id = jwt_data.get('userId')
    data = request.get_json() or {}
    new_callsign = data.get('callsign')
    new_email = data.get('email')

    if not new_callsign or not new_email:
        return jsonify({'status': 'error', 'message': 'Callsign and email are required'}), 400

    try:
        if fa_client:
            # Update via FusionAuth
            user_request = {
                'user': {
                    'email': new_email,
                    'username': new_callsign,
                    'firstName': new_callsign
                }
            }
            response = fa_client.patch_user(user_id, user_request)
            if response.was_successful():
                return jsonify({
                    'status': 'ok',
                    'message': 'Profile updated successfully',
                    'user': {
                        'user_id': user_id,
                        'callsign': new_callsign,
                        'email': new_email
                    }
                })
            else:
                return jsonify({'status': 'error', 'message': f'FusionAuth: {response.error_response}'}), 400

        if operator_manager:
            # Update via Legacy Operator Manager
            operator = operator_manager.get_operator(user_id)
            if operator:
                operator.callsign = new_callsign
                operator.email = new_email
                operator_manager.save_operator(operator)
                return jsonify({
                    'status': 'ok',
                    'message': 'Profile updated successfully',
                    'user': operator.to_dict()
                })

        return jsonify({'status': 'error', 'message': 'User not found'}), 404
    except Exception as e:
        log.error(f"User update error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


# =========================================================================
# TOTP TWO-FACTOR AUTHENTICATION
# =========================================================================

@app.route('/api/operator/totp/setup', methods=['POST'])
def orchestrator_totp_setup():
    """Generate TOTP secret for user setup"""
    try:
        import pyotp
        import qrcode
        import io
        import base64
        import json as _json

        token = request.headers.get('X-Session-Token') or request.args.get('token')
        if not token:
            return jsonify({'status': 'error', 'message': 'No session token provided'}), 401

        jwt_data = validate_jwt(token)
        if not jwt_data or not jwt_data.get('valid'):
            return jsonify({'status': 'error', 'message': 'Invalid session'}), 401

        # Generate TOTP secret
        secret = pyotp.random_base32()

        # Create provisioning URI for QR code
        user_email = jwt_data.get('email') or 'operator'
        totp = pyotp.TOTP(secret)
        provisioning_uri = totp.provisioning_uri(
            name=user_email,
            issuer_name='RF SCYTHE'
        )

        # Generate QR code image
        qr = qrcode.QRCode(version=1, box_size=10, border=4)
        qr.add_data(provisioning_uri)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")

        # Convert to base64
        img_buffer = io.BytesIO()
        img.save(img_buffer, format='PNG')
        img_str = base64.b64encode(img_buffer.getvalue()).decode()

        return jsonify({
            'status': 'ok',
            'secret': secret,
            'qr_code_url': f'data:image/png;base64,{img_str}',
            'provisioning_uri': provisioning_uri
        })
    except Exception as e:
        log.error(f"TOTP setup error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/operator/totp/enable', methods=['POST'])
def orchestrator_totp_enable():
    """Enable TOTP with verification code"""
    try:
        import pyotp

        token = request.headers.get('X-Session-Token') or request.args.get('token')
        if not token:
            return jsonify({'status': 'error', 'message': 'No session token provided'}), 401

        jwt_data = validate_jwt(token)
        if not jwt_data or not jwt_data.get('valid'):
            return jsonify({'status': 'error', 'message': 'Invalid session'}), 401

        data = request.get_json() or {}
        secret = data.get('secret')
        code = data.get('code')

        if not secret or not code:
            return jsonify({'status': 'error', 'message': 'Missing secret or code'}), 400

        # Verify TOTP code
        totp = pyotp.TOTP(secret)
        if not totp.verify(code):
            return jsonify({'status': 'error', 'message': 'Invalid TOTP code'}), 401

        # Store TOTP secret for user
        user_id = jwt_data.get('userId')
        if operator_manager:
            operator = operator_manager.get_operator(user_id)
            if operator:
                operator.data = operator.data or {}
                operator.data['totp_enabled'] = True
                operator.data['totp_secret'] = secret
                operator_manager.save_operator(operator)

        return jsonify({
            'status': 'ok',
            'message': 'TOTP enabled successfully'
        })
    except Exception as e:
        log.error(f"TOTP enable error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/operator/totp/verify', methods=['POST'])
def orchestrator_totp_verify():
    """Verify TOTP code during login"""
    try:
        import pyotp

        data = request.get_json() or {}
        callsign = data.get('callsign')
        totp_code = data.get('totp_code')

        if not callsign or not totp_code:
            return jsonify({'status': 'error', 'message': 'Missing callsign or TOTP code'}), 400

        # Get operator and verify TOTP
        if operator_manager:
            operator = operator_manager.get_operator_by_callsign(callsign) if hasattr(operator_manager, 'get_operator_by_callsign') else None
            if not operator:
                # Try to get by username through legacy manager
                all_ops = operator_manager.list_operators() if hasattr(operator_manager, 'list_operators') else []
                operator = next((op for op in all_ops if op.callsign == callsign), None)

            if operator:
                totp_secret = operator.data.get('totp_secret') if operator.data else None
                if totp_secret:
                    totp = pyotp.TOTP(totp_secret)
                    if totp.verify(totp_code):
                        # Create session
                        session = operator_manager.authenticate(callsign, operator.password_hash)
                        if session:
                            with _sessions_lock:
                                _shared_sessions[session.session_token] = {
                                    'instance_id': 'orchestrator',
                                    'operator_id': session.operator_id,
                                    'expires_at': session.expires_at,
                                }
                            return jsonify({
                                'status': 'ok',
                                'session': session.to_dict(),
                                'operator': operator.to_dict()
                            })

        return jsonify({'status': 'error', 'message': 'Invalid TOTP code'}), 401
    except Exception as e:
        log.error(f"TOTP verify error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/operator/totp/status', methods=['GET'])
def orchestrator_totp_status():
    """Get TOTP status for current user"""
    try:
        token = request.headers.get('X-Session-Token') or request.args.get('token')
        if not token:
            return jsonify({'status': 'error', 'message': 'No session token provided'}), 401

        jwt_data = validate_jwt(token)
        if not jwt_data or not jwt_data.get('valid'):
            return jsonify({'status': 'error', 'message': 'Invalid session'}), 401

        # Check TOTP status
        totp_enabled = False
        user_id = jwt_data.get('userId')

        if not user_id:
             return jsonify({'status': 'error', 'message': 'Session missing operator ID'}), 401

        if operator_manager:
            operator = operator_manager.get_operator(user_id)
            if operator and hasattr(operator, 'data') and operator.data:
                totp_enabled = operator.data.get('totp_enabled', False)

        return jsonify({
            'status': 'ok',
            'totp_enabled': totp_enabled
        })
    except Exception as e:
        log.error(f"TOTP status error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/debug/operators', methods=['GET'])
def debug_operators():
    """Debug endpoint: list all operators in the database."""
    if not operator_manager:
        return jsonify({'status': 'error', 'message': 'Operator Manager not available'}), 503

    try:
        operators_list = []
        for op_id, op in operator_manager.operators.items():
            operators_list.append({
                'operator_id': op_id,
                'callsign': op.callsign,
                'email': op.email,
                'role': op.role.value if hasattr(op.role, 'value') else str(op.role),
                'created_at': op.created_at,
                'last_active': op.last_active
            })

        return jsonify({
            'status': 'ok',
            'operator_count': len(operators_list),
            'operators': operators_list
        })
    except Exception as e:
        log.error(f"Debug operators error: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ---------------------------------------------------------------------------
# TAK / ATAK integration passthrough routes
# ---------------------------------------------------------------------------

def _get_primary_instance_port() -> int | None:
    """Return the port of the running instance with the most nodes."""
    with _registry_lock:
        running = [v for v in _instances.values() if v.get('status') == 'running']
    if not running:
        return None
    best = max(running, key=lambda i: i.get('info', {}).get('node_count', 0))
    return best.get('port')


def _proxy_get(instance_port: int, path: str, timeout: int = 5):
    """Forward a GET request to a scythe instance and return the JSON response."""
    import urllib.request as _ureq
    url = f"http://127.0.0.1:{instance_port}{path}"
    try:
        with _ureq.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        return None


@app.route('/api/recon/entities', methods=['GET'])
def orchestrator_recon_entities():
    """Passthrough: proxy /api/recon/entities from the primary active instance.
    Used by tak_cot_relay.py and the ATAK plugin to fetch entity data from a
    stable URL (orchestrator) without needing to know the dynamic instance port.
    """
    port = _get_primary_instance_port()
    if not port:
        return jsonify({'status': 'error', 'message': 'No active SCYTHE instance', 'entities': [], 'entity_count': 0}), 503

    data = _proxy_get(port, '/api/recon/entities')
    if data is None:
        return jsonify({'status': 'error', 'message': f'Instance on port {port} unreachable', 'entities': [], 'entity_count': 0}), 502

    return jsonify(data)


@app.route('/api/clusters/swarms', methods=['GET'])
def orchestrator_swarms():
    """Passthrough: proxy /api/clusters/swarms from the primary active instance."""
    port = _get_primary_instance_port()
    if not port:
        return jsonify({'status': 'error', 'swarms': []}), 503
    data = _proxy_get(port, '/api/clusters/swarms')
    if data is None:
        return jsonify({'status': 'error', 'swarms': []}), 502
    return jsonify(data)


@app.route('/api/rf-hypergraph/status', methods=['GET'])
def orchestrator_hg_status():
    """Passthrough: proxy hypergraph status from the primary active instance."""
    port = _get_primary_instance_port()
    if not port:
        return jsonify({'status': 'error'}), 503
    data = _proxy_get(port, '/api/rf-hypergraph/status')
    return jsonify(data or {'status': 'error'})


def _proxy_post(instance_port: int, path: str, body: dict, timeout: int = 5):
    """Forward a POST request with JSON body to a scythe instance."""
    import urllib.request as _ureq
    url = f"http://127.0.0.1:{instance_port}{path}"
    data = json.dumps(body).encode('utf-8')
    req = _ureq.Request(url, data=data, headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with _ureq.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception:
        return None


@app.route('/api/tak-ml/kserve/infer', methods=['POST'])
def orchestrator_takml_infer():
    """Passthrough: proxy TAK-ML KServe inference to the primary active instance.

    Allows the browser to call a single stable URL regardless of which instance
    port the API server is running on.  Returns the instance response unchanged.
    """
    port = _get_primary_instance_port()
    if not port:
        return jsonify({'status': 'error', 'reachable': False, 'score': 0.0,
                        'message': 'No active SCYTHE instance'}), 200

    body = request.get_json(silent=True) or {}
    data = _proxy_post(port, '/api/tak-ml/kserve/infer', body)
    if data is None:
        return jsonify({'status': 'error', 'reachable': False, 'score': 0.0,
                        'message': f'Instance on port {port} unreachable'}), 200
    return jsonify(data)


@app.route('/api/tak-ml/kserve/health', methods=['GET'])
def orchestrator_takml_health():
    """Passthrough: proxy TAK-ML KServe health to the primary active instance."""
    port = _get_primary_instance_port()
    if not port:
        return jsonify({'reachable': False, 'message': 'No active SCYTHE instance'}), 200
    data = _proxy_get(port, '/api/tak-ml/kserve/health')
    if data is None:
        return jsonify({'reachable': False, 'message': f'Instance on port {port} unreachable'}), 200
    return jsonify(data)


# ---------------------------------------------------------------------------
# Cleanup on exit
# ---------------------------------------------------------------------------
def _cleanup():
    """Terminate all child instances and co-managed services on orchestrator shutdown."""
    log.info("Orchestrator shutting down — terminating child instances...")
    with _registry_lock:
        for iid, inst in _instances.items():
            proc = inst.get('process')
            if proc and proc.poll() is None:
                try:
                    proc.terminate()
                    log.info(f"  Terminated {iid} (PID {proc.pid})")
                except Exception:
                    pass
    _stop_services()

import atexit
atexit.register(_cleanup)


# ---------------------------------------------------------------------------
# mDNS Registration
# ---------------------------------------------------------------------------
def register_mdns(port):
    """Register _scythe._tcp mDNS service so Android clients can auto-discover."""
    if not HAS_ZEROCONF:
        print("[mDNS] zeroconf not installed, skipping mDNS registration")
        print("[mDNS] Install with: pip install zeroconf")
        return None
    try:
        hostname = socket.gethostname()
        try:
            local_ip = socket.gethostbyname(hostname)
        except Exception:
            # Fallback: connect to a public address to discover local IP
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
        zc = Zeroconf()
        info = ServiceInfo(
            "_scythe._tcp.local.",
            "ScytheOrchestrator._scythe._tcp.local.",
            addresses=[socket.inet_aton(local_ip)],
            port=port,
            properties={"version": "1.0", "path": "/command-ops-visualization.html"},
        )
        zc.register_service(info)
        print(f"[mDNS] Registered _scythe._tcp.local on {local_ip}:{port}")
        return zc
    except Exception as e:
        print(f"[mDNS] Registration failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description='SCYTHE Multi-Instance Orchestrator',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 scythe_orchestrator.py                   # default port 5000
  python3 scythe_orchestrator.py --port 9000       # custom port
  python3 scythe_orchestrator.py --host 0.0.0.0    # listen on all interfaces
        """
    )
    parser.add_argument('--port', type=int, default=5000, help='Orchestrator port (default: 5000)')
    parser.add_argument('--host', type=str, default='127.0.0.1', help='Host to bind (default: 127.0.0.1)')
    parser.add_argument('--debug', action='store_true', help='Enable Flask debug mode')
    parser.add_argument('--stream-relay-url', default='ws://localhost:8765/ws',
                        help='WebSocket relay URL forwarded to all spawned instances (default: ws://localhost:8765/ws)')
    parser.add_argument('--mcp-ws-url', default='ws://localhost:8766/ws',
                        help='MCP WebSocket URL forwarded to all spawned instances (default: ws://localhost:8766/ws)')
    parser.add_argument('--eve-stream-ws-url', default='ws://localhost:8081/ws',
                        help='eve-streamer WebSocket URL forwarded to all spawned instances (default: ws://localhost:8081/ws)')
    parser.add_argument('--eve-stream-http-url', default='http://localhost:8081',
                        help='eve-streamer HTTP base URL forwarded to all spawned instances (default: http://localhost:8081)')
    parser.add_argument('--ollama-url', default=os.environ.get('OLLAMA_URL', 'http://localhost:11434'),
                        help='Ollama inference URL (default: $OLLAMA_URL or http://localhost:11434)')
    parser.add_argument('--takml-url', default='http://localhost:8234',
                        help='TAK-ML HTTP URL forwarded to all spawned instances (default: http://localhost:8234)')
    parser.add_argument('--no-services', action='store_true',
                        help='Do not auto-launch ws_ingest / rf_voxel_processor companion services')
    parser.add_argument('--grpc-port', type=int, default=50051,
                        help='gRPC server port (default: 50051)')
    parser.add_argument('--no-grpc', action='store_true',
                        help='Do not launch the gRPC server subprocess')
    args = parser.parse_args()

    # Propagate stream URLs into module-level globals so spawn_instance() picks them up
    global _STREAM_RELAY_URL, _MCP_WS_URL, _TAKML_URL, _EVE_STREAM_WS_URL, _EVE_STREAM_HTTP_URL, _parsed_args, _ORCHESTRATOR_PORT
    _STREAM_RELAY_URL = args.stream_relay_url
    _MCP_WS_URL       = args.mcp_ws_url
    _TAKML_URL        = args.takml_url
    _EVE_STREAM_WS_URL = args.eve_stream_ws_url
    _EVE_STREAM_HTTP_URL = args.eve_stream_http_url
    _parsed_args       = args   # used by _start_services() for OLLAMA_URL propagation
    _ORCHESTRATOR_PORT = int(args.port)

    # Launch companion WS services unless caller opted out
    _launch_services(auto=not args.no_services)

    # Launch gRPC server subprocess unless opted out
    if not args.no_grpc and _GRPC_SERVER.exists():
        orchestrator_url = f'http://127.0.0.1:{args.port}'
        grpc_cmd = [
            sys.executable, str(_GRPC_SERVER),
            '--grpc-port', str(args.grpc_port),
            '--orchestrator-url', orchestrator_url,
            '--internal-token', _INTERNAL_TOKEN,
            '--voxel-url', f'http://127.0.0.1:{args.voxel_port if hasattr(args, "voxel_port") else 8766}',
        ]
        grpc_log = _SCRIPT_DIR / 'logs' / 'grpc_server.log'
        grpc_log.parent.mkdir(exist_ok=True)
        grpc_fh = open(grpc_log, 'a', buffering=1)
        grpc_proc = subprocess.Popen(
            grpc_cmd,
            stdout=grpc_fh,
            stderr=grpc_fh,
            cwd=str(_SCRIPT_DIR),
            start_new_session=True,
        )
        log.info(f'[gRPC] Server started — PID {grpc_proc.pid}, port {args.grpc_port}')
    elif not args.no_grpc:
        log.warning('[gRPC] scythe_grpc_server.py not found — gRPC disabled')

    # Startup banner
    banner = f"""
╔══════════════════════════════════════════════════════════════╗
║           ⚔  SCYTHE MULTI-INSTANCE ORCHESTRATOR ⚔           ║
║                                                              ║
║   "Each instance: one sovereign hypergraph.                  ║
║    No shared memory. No shared state.                        ║
║    No accidental cross-contamination."                       ║
║                                                              ║
║   Orchestrator : http://{args.host}:{args.port:<5}                      ║
║   Home Page    : http://{args.host}:{args.port:<5}/                     ║
║   API Server   : {str(_API_SERVER)[-45:]:<45} ║
║   Server exists: {'YES' if _API_SERVER.exists() else 'NO ':<45} ║
║                                                              ║
║   Endpoints:                                                 ║
║     GET  /api/scythe/instances        — list instances       ║
║     POST /api/scythe/instances/new    — spawn new instance   ║
║     DEL  /api/scythe/instances/<id>   — kill instance        ║
║     GET  /api/scythe/health           — orchestrator health  ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
"""
    print(banner)

    if not _API_SERVER.exists():
        log.warning(f"API server not found at {_API_SERVER}")
        log.warning("Spawning new instances will fail until rf_scythe_api_server.py is available.")

    # Register mDNS service so Android app can auto-discover this orchestrator
    _zc = register_mdns(args.port)
    if _zc is not None:
        atexit.register(lambda: _zc.close())

    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


if __name__ == '__main__':
    main()
