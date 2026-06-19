"""eve_sensor_mcp.py — Remote Stream MCP Tool for live sensor grounding.

Registers the ``graphops_sensor_stream`` MCP tool so GraphOps Bot can pull
a burst of live network flow data from the eve-streamer daemon on demand.

When the bot detects ``trust_posture: inference-heavy`` or the graph has few
observed (sensor-backed) edges, it can call this tool to inject real flow data
and immediately shift the trust posture toward ``sensor-heavy``.

Architecture
------------
eve-streamer (Go, :8081)
  └─ /ws  →  binary FlatBuffer FlowEvent frames  (Nerf.FlowEvent, 11 fields)
  └─ /capture/metrics  →  JSON capture health

Nerf.FlowEvent vtable layout (from fb/Nerf/FlowEvent.go):
  slot 0  flow_id    uint64   vtable offset 4
  slot 1  src_ip     uint32   vtable offset 6   (LE; network bytes read as LE by kernel)
  slot 2  dst_ip     uint32   vtable offset 8
  slot 3  src_port   uint16   vtable offset 10
  slot 4  dst_port   uint16   vtable offset 12
  slot 5  proto      uint8    vtable offset 14
  slot 6  packets    uint64   vtable offset 16
  slot 7  bytes      uint64   vtable offset 18
  slot 8  flags      uint32   vtable offset 20
  slot 9  event_type uint8    vtable offset 22
  slot 10 timestamp  uint64   vtable offset 24

IP reconstruction: eve-streamer reads packet header bytes via
binary.LittleEndian.Uint32(), so FlatBuffer stores them in the same LE byte
order.  _ip_from_le_uint32() reverses to dotted-decimal correctly.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import struct
import threading
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ─── New Host Detection & pcapng Capture ──────────────────────────────────────
try:
    import new_host_pcapng_logger
    _new_host_logger_available = True
except ImportError:
    logger.warning("[eve_sensor_mcp] new_host_pcapng_logger not available")
    _new_host_logger_available = False

# ─── GeoIP readers (lazy, module-level) ──────────────────────────────────────

_GEOIP_CITY: Any = None
_GEOIP_ASN:  Any = None
_GEOIP_INIT: bool = False


def _init_geoip() -> None:
    global _GEOIP_CITY, _GEOIP_ASN, _GEOIP_INIT
    if _GEOIP_INIT:
        return
    _GEOIP_INIT = True
    try:
        import maxminddb
        _GEOIP_CITY = maxminddb.open_database("assets/GeoLite2-City.mmdb")
        _GEOIP_ASN  = maxminddb.open_database("assets/GeoLite2-ASN.mmdb")
    except Exception as exc:
        logger.debug("[eve_sensor_mcp] GeoIP unavailable: %s", exc)


# ─── FlatBuffer FlowEvent decoder ────────────────────────────────────────────

# Vtable offset constants — must match fb/Nerf/FlowEvent.go
_VT_FLOW_ID    = 4
_VT_SRC_IP     = 6
_VT_DST_IP     = 8
_VT_SRC_PORT   = 10
_VT_DST_PORT   = 12
_VT_PROTO      = 14
_VT_PACKETS    = 16
_VT_BYTES      = 18
_VT_FLAGS      = 20
_VT_EVENT_TYPE = 22
_VT_TIMESTAMP  = 24


def _ip_from_le_uint32(n: int) -> str:
    """Reconstruct dotted-decimal IP from a little-endian uint32.

    eve-streamer reads IPv4 header bytes with binary.LittleEndian.Uint32(),
    so network-order bytes [192,168,1,1] become 0x0101A8C0 in the uint32.
    FlatBuffers stores it LE on the wire.  Reading it back gives 0x0101A8C0
    and extracting LSB-first yields 192.168.1.1 correctly.
    """
    return (
        f"{n & 0xFF}."
        f"{(n >> 8)  & 0xFF}."
        f"{(n >> 16) & 0xFF}."
        f"{(n >> 24) & 0xFF}"
    )


def _decode_flatbuf_flow(buf: bytes) -> Optional[Dict[str, Any]]:
    """Decode a Nerf.FlowEvent FlatBuffer message into a plain dict.

    Returns None for any buffer that cannot be safely decoded.
    """
    try:
        buf = bytearray(buf)
        if len(buf) < 24:  # minimum realistic FlowEvent
            return None

        # Root offset (UOffsetT, 4-byte LE) → table position
        root_offset = struct.unpack_from('<I', buf, 0)[0]
        if root_offset + 4 > len(buf):
            return None

        # soffset_t at table start points back to vtable (signed 32-bit LE)
        vtable_soffset = struct.unpack_from('<i', buf, root_offset)[0]
        vtable_offset  = root_offset - vtable_soffset
        if vtable_offset < 0 or vtable_offset + 4 > len(buf):
            return None

        vtable_size = struct.unpack_from('<H', buf, vtable_offset)[0]
        if vtable_size < 4:
            return None

        def _abs(vt_offset: int) -> Optional[int]:
            """Resolve a vtable slot to absolute buffer position."""
            if vt_offset >= vtable_size:
                return None
            slot_off = struct.unpack_from('<H', buf, vtable_offset + vt_offset)[0]
            return (root_offset + slot_off) if slot_off else None

        def _u64(vt: int) -> int:
            p = _abs(vt)
            return struct.unpack_from('<Q', buf, p)[0] if p and p + 8 <= len(buf) else 0

        def _u32(vt: int) -> int:
            p = _abs(vt)
            return struct.unpack_from('<I', buf, p)[0] if p and p + 4 <= len(buf) else 0

        def _u16(vt: int) -> int:
            p = _abs(vt)
            return struct.unpack_from('<H', buf, p)[0] if p and p + 2 <= len(buf) else 0

        def _u8(vt: int) -> int:
            p = _abs(vt)
            return buf[p] if p and p < len(buf) else 0

        proto_num = _u8(_VT_PROTO)
        proto_map = {1: "icmp", 6: "tcp", 17: "udp"}
        proto     = proto_map.get(proto_num, f"proto_{proto_num}")

        src = _ip_from_le_uint32(_u32(_VT_SRC_IP))
        dst = _ip_from_le_uint32(_u32(_VT_DST_IP))

        # Discard zero-address frames (padding / empty FlatBuffer)
        if src == "0.0.0.0" or dst == "0.0.0.0":
            return None

        flow_hash = _u64(_VT_FLAGS)  # flags slot reused as flow_hash in older schema
        ts_ns     = _u64(_VT_TIMESTAMP)
        # Derive a stable evidence ref from the 5-tuple + timestamp
        ev_id = f"{src}:{_u16(_VT_SRC_PORT)}:{dst}:{_u16(_VT_DST_PORT)}:{proto}:{ts_ns}"

        return {
            "event_id":  ev_id,
            "type":      "flow",
            "src_ip":    src,
            "dst_ip":    dst,
            "src_port":  _u16(_VT_SRC_PORT),
            "dst_port":  _u16(_VT_DST_PORT),
            "proto":     proto,
            "packets":   _u64(_VT_PACKETS),
            "bytes":     _u64(_VT_BYTES),
            "timestamp_ns": ts_ns,
        }
    except Exception as exc:
        logger.debug("[eve_sensor_mcp] flatbuf decode error: %s", exc)
        return None


# ─── GeoIP enrichment ────────────────────────────────────────────────────────

def _enrich_ip(ip: str) -> Dict[str, str]:
    """Return GeoIP fields for an IP address (best-effort, non-blocking)."""
    result: Dict[str, str] = {}
    try:
        if _GEOIP_CITY:
            rec = _GEOIP_CITY.get(ip) or {}
            result["country"] = (rec.get("country") or {}).get("iso_code", "")
            names = (rec.get("city") or {}).get("names") or {}
            result["city"] = names.get("en", "") if isinstance(names, dict) else ""
            loc = rec.get("location") or {}
            lat, lon = loc.get("latitude"), loc.get("longitude")
            if lat is not None and lon is not None:
                result["lat"] = str(lat)
                result["lon"] = str(lon)
    except Exception:
        pass
    try:
        if _GEOIP_ASN:
            rec = _GEOIP_ASN.get(ip) or {}
            asn = rec.get("autonomous_system_number")
            org = rec.get("autonomous_system_organization", "")
            if asn:
                result["asn"] = str(asn)
            if org:
                result["org"] = org
    except Exception:
        pass
    return result


# ─── Graph event normalisation ────────────────────────────────────────────────

def _normalise_to_graph_events(
    raw_events: List[Dict[str, Any]],
) -> Tuple[List[Dict], int, int]:
    """Convert decoded flow dicts into NODE_CREATE + EDGE_CREATE graph events.

    Deduplicates host nodes and flow edges within the batch.
    Returns (graph_events, n_new_nodes, n_new_edges).
    """
    _init_geoip()

    graph_events: List[Dict] = []
    seen_hosts: set  = set()
    seen_flows: set  = set()
    n_nodes = 0
    n_edges = 0

    for ev in raw_events:
        src = ev.get("src_ip", "")
        dst = ev.get("dst_ip", "")
        if not (src and dst):
            continue

        src_port = ev.get("src_port", 0)
        dst_port = ev.get("dst_port", 0)
        proto    = ev.get("proto", "tcp")
        ev_id    = ev.get("event_id", str(uuid.uuid4()))
        prov     = {
            "source":       "suricata",
            "tool":         "eve_sensor_stream",
            "evidence_refs": [ev_id],
        }

        # ── Host nodes ────────────────────────────────────────────────
        for ip in (src, dst):
            if ip in seen_hosts:
                continue
            seen_hosts.add(ip)
            labels: Dict[str, Any] = {"ip": ip}
            labels.update(_enrich_ip(ip))
            graph_events.append({
                "event_type":  "NODE_CREATE",
                "entity_id":   f"host:{ip}",
                "entity_data": {
                    "id":     f"host:{ip}",
                    "kind":   "host",
                    "labels": labels,
                    "metadata": {
                        "obs_class":        "observed",
                        "provenance_write": prov,
                    },
                },
            })
            n_nodes += 1

        # ── Flow edge ─────────────────────────────────────────────────
        flow_key = f"{src}:{src_port}:{dst}:{dst_port}:{proto}"
        if flow_key in seen_flows:
            continue
        seen_flows.add(flow_key)

        graph_events.append({
            "event_type":  "EDGE_CREATE",
            "entity_id":   f"flow:{flow_key}",
            "entity_data": {
                "id":    f"flow:{flow_key}",
                "kind":  "flow",
                "nodes": [f"host:{src}", f"host:{dst}"],
                "labels": {
                    "src_ip":   src,
                    "dst_ip":   dst,
                    "src_port": str(src_port),
                    "dst_port": str(dst_port),
                    "proto":    proto,
                    "bytes":    str(ev.get("bytes", 0)),
                    "packets":  str(ev.get("packets", 0)),
                },
                "metadata": {
                    "obs_class":        "observed",
                    "provenance_write": prov,
                },
            },
        })
        n_edges += 1

    return graph_events, n_nodes, n_edges


# ─── WebSocket collection (binary FlatBuffer only) ───────────────────────────

def _collect_from_ws(uri: str, window_seconds: float, max_events: int) -> List[Dict]:
    """Connect to eve-streamer /ws, collect binary FlatBuffer frames.

    Returns decoded flow dicts.  JSON text messages are ignored — the /ws
    endpoint feeds only from binaryCh (see eve-streamer main.go:376-377).

    Runs in a fresh thread with its own event loop to avoid conflicting with
    any running asyncio loop in the Flask/MCP server context.
    """
    collected: List[Dict] = []
    ws_error:  List[str]  = []

    async def _client() -> None:
        try:
            import websockets
            deadline = time.time() + window_seconds
            async with websockets.connect(
                uri,
                ping_timeout=None,
                open_timeout=5,
            ) as ws:
                async for message in ws:
                    if not isinstance(message, bytes):
                        continue   # /ws is binary-only; skip any stray text
                    ev = _decode_flatbuf_flow(message)
                    if ev:
                        collected.append(ev)
                        # Process through new host logger if available
                        if _new_host_logger_available:
                            try:
                                new_host_pcapng_logger.process_flow_event(ev)
                            except Exception as e:
                                logger.debug(f"[eve_sensor_mcp] New host logger error: {e}")
                    if len(collected) >= max_events or time.time() >= deadline:
                        break
        except Exception as exc:
            ws_error.append(str(exc))

    def _run_loop() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(
                asyncio.wait_for(_client(), timeout=window_seconds + 3)
            )
        except asyncio.TimeoutError:
            pass
        except Exception as exc:
            ws_error.append(str(exc))
        finally:
            loop.close()

    t = threading.Thread(target=_run_loop, daemon=True)
    t.start()
    t.join(timeout=window_seconds + 5)

    if ws_error:
        logger.info("[eve_sensor_mcp] WS: %s", ws_error[0])

    return collected


# ─── HTTP metrics fetch ───────────────────────────────────────────────────────

def _fetch_metrics(host: str, http_port: int) -> Dict[str, Any]:
    """GET /capture/metrics from the eve-streamer HTTP endpoint."""
    url = f"http://{host}:{http_port}/capture/metrics"
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url), timeout=3
        ) as resp:
            return json.loads(resp.read())
    except urllib.error.URLError as exc:
        return {"available": False, "error": str(exc.reason)}
    except Exception as exc:
        return {"available": False, "error": str(exc)}


def _stream_defaults() -> Dict[str, Any]:
    """Resolve eve-streamer defaults from environment before falling back to localhost."""
    ws_url = os.environ.get("EVE_STREAM_WS_URL", "ws://localhost:8081/ws")
    http_url = os.environ.get("EVE_STREAM_HTTP_URL", "http://localhost:8081")
    ws_parts = urlparse(ws_url)
    http_parts = urlparse(http_url)
    return {
        "host": ws_parts.hostname or http_parts.hostname or "localhost",
        "ws_port": int(os.environ.get("EVE_STREAM_WS_PORT") or ws_parts.port or 8081),
        "http_port": int(os.environ.get("EVE_STREAM_HTTP_PORT") or http_parts.port or 8081),
        "ws_url": ws_url,
        "http_url": http_url,
    }


# ─── Main tool function ───────────────────────────────────────────────────────

def sensor_stream_tool(params: Dict[str, Any], engine: Any) -> Dict[str, Any]:
    """Pull live sensor events from eve-streamer and inject into hypergraph.

    Parameters
    ----------
    host : str
        eve-streamer hostname (default ``localhost``).
    ws_port : int
        WebSocket port (default ``8081``).
    http_port : int
        HTTP metrics port (default ``8081``).
    window_seconds : float
        Collection window in seconds (default ``5.0``).
    max_events : int
        Maximum flow events to collect (default ``200``).
    check_only : bool
        Only return capture metrics, do not inject into graph (default false).
    """
    defaults    = _stream_defaults()
    host        = str(params.get("host") or defaults["host"])
    ws_port     = int(params.get("ws_port", defaults["ws_port"]))
    http_port   = int(params.get("http_port", defaults["http_port"]))
    window_secs = float(params.get("window_seconds", 5.0))
    max_events  = int(params.get("max_events", 200))
    check_only  = bool(params.get("check_only", False))

    result: Dict[str, Any] = {
        "host": host,
        "ws_port": ws_port,
        "http_port": http_port,
        "eve_stream_ws": defaults["ws_url"],
        "eve_stream_http": defaults["http_url"],
    }

    # 1. Capture health (always)
    metrics = _fetch_metrics(host, http_port)
    result["capture_metrics"]   = metrics
    result["streamer_available"] = metrics.get("available", True)

    if check_only:
        return result

    # 2. Snapshot counts before injection
    nodes_before = len(engine.nodes) if hasattr(engine, "nodes") else 0
    edges_before = len(engine.edges) if hasattr(engine, "edges") else 0

    # 3. Collect from WebSocket
    ws_uri  = f"ws://{host}:{ws_port}/ws"
    raw_evs = _collect_from_ws(ws_uri, window_secs, max_events)
    result["fetched_ws"] = len(raw_evs)

    if not raw_evs:
        result.update({
            "committed": 0,
            "new_nodes": 0,
            "new_edges": 0,
            "message": (
                "No sensor events received.  "
                "Verify eve-streamer is running and capturing traffic: "
                f"ws://{host}:{ws_port}/ws"
            ),
        })
        return result

    # 4. Normalise to graph events
    graph_events, n_batch_nodes, n_batch_edges = _normalise_to_graph_events(raw_evs)

    # 5. Apply to engine
    committed = 0
    if hasattr(engine, "apply_graph_event"):
        for ge in graph_events:
            try:
                if engine.apply_graph_event(ge):
                    committed += 1
            except Exception:
                continue
    else:
        result["error"] = "Engine missing apply_graph_event"
        return result

    nodes_after = len(engine.nodes) if hasattr(engine, "nodes") else nodes_before
    edges_after = len(engine.edges) if hasattr(engine, "edges") else edges_before

    result.update({
        "committed":      committed,
        "new_nodes":      nodes_after - nodes_before,
        "new_edges":      edges_after - edges_before,
        "batch_nodes":    n_batch_nodes,
        "batch_edges":    n_batch_edges,
        "message": (
            f"Injected {committed} sensor-backed graph events "
            f"({nodes_after - nodes_before} new nodes, "
            f"{edges_after - edges_before} new edges). "
            "trust_posture will shift toward sensor-heavy on next MCP context rebuild."
        ),
    })

    logger.info(
        "[eve_sensor_mcp] ws=%d committed=%d nodes+%d edges+%d",
        len(raw_evs), committed,
        nodes_after - nodes_before,
        edges_after - edges_before,
    )
    return result


# ─── Registration ─────────────────────────────────────────────────────────────

def register_sensor_stream_tool(engine: Any, mcp_handler: Any) -> None:
    """Register ``graphops_sensor_stream`` into an MCPHandler instance.

    Called at the end of graphops_copilot.register_graphops_tools().
    Uses the same ToolDef path as the other 4 graphops tools.
    The mcp_server._handle_tools_call() dispatch was updated to fall back to
    ToolDef.fn when the tool is not in the registry, so this is callable even
    when mcp_registry is loaded.
    """
    from mcp_server import ToolDef

    # Initialize new host logger
    if _new_host_logger_available:
        try:
            new_host_pcapng_logger.initialize()
            logger.info("[eve_sensor_mcp] New host pcapng logger initialized")
        except Exception as e:
            logger.warning(f"[eve_sensor_mcp] Failed to initialize new host logger: {e}")

    mcp_handler._tools["graphops_sensor_stream"] = ToolDef(
        name="graphops_sensor_stream",
        description=(
            "Pull live network flow events from the eve-streamer sensor daemon "
            "and inject them as observed (sensor-backed) nodes and edges into the "
            "hypergraph.  "
            "CALL THIS when trust_posture is 'inference-heavy', the graph has few "
            "observed edges, or sensor_fraction is low — this tool shifts trust "
            "posture toward 'sensor-heavy' by grounding the graph in real traffic.  "
            "Parameters: host (default localhost), ws_port (default 8081), "
            "window_seconds (default 5.0 — how long to collect), "
            "max_events (default 200), "
            "check_only (bool, only return capture metrics without injecting)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "host":           {"type": "string",  "default": "localhost"},
                "ws_port":        {"type": "integer", "default": 8081},
                "http_port":      {"type": "integer", "default": 8081},
                "window_seconds": {"type": "number",  "default": 5.0},
                "max_events":     {"type": "integer", "default": 200},
                "check_only":     {"type": "boolean", "default": False},
            },
            "required": [],
        },
        fn=lambda p: sensor_stream_tool(p, engine),
    )

    logger.info("[eve_sensor_mcp] registered graphops_sensor_stream tool")
