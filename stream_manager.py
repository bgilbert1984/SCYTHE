"""stream_manager.py

Persistent connection manager for remote eve-streamer / rfscythe feeds.

Binary framing protocol (added in Stage 4 / Stage 4.5):
  Every binary WebSocket message begins with a 1-byte frame tag:
    0x00  →  FlowCore raw struct  (56 bytes after the tag) — FLOW_START / TCP FLOW_END
    0x01  →  rfscythe.FlowEvent  FlatBuffers table (variable length after tag)
    0x02  →  FlowEndEvent raw struct (32 bytes after the tag) — timer-triggered FLOW_END
    0x03  →  EdgeTick raw struct (32 bytes after the tag) — compressed FLOW_UPDATE (Stage 6)
    0x04  →  GraphEdgeEvent raw struct (56 bytes after the tag) — EDGE_OPEN/CLOSE (Phase B)
             event_type field at byte 48 of payload: 0=EDGE_OPEN, 2=EDGE_CLOSE
  Any other tag (or a legacy message without a tag prefix) is treated as
  JSON text and parsed normally.

The manager maintains one asyncio event loop in a background thread and keeps
track of active WebSocket connections.  Decoded events are forwarded to
live_ingest.enqueue for hypergraph ingest.
"""

import asyncio
import json
import socket
import struct
import uuid
import time
import logging
import threading
import sys
import os
from typing import Dict, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fb.rfscythe.FlowEvent import FlowEvent as rfFlowEvent
from fb.rfscythe.FlowCore  import unpack as fc_unpack, FLOW_CORE_SIZE
from fb.rfscythe.FlowCore  import _TYPE_NAMES as FC_TYPE_NAMES, _PROTO_NAMES as FC_PROTO_NAMES
from fb.rfscythe.FlowCore  import FlowEndEvent, FLOW_END_SIZE
from fb.rfscythe.FlowCore  import EdgeTick, EDGE_TICK_SIZE
from fb.rfscythe.FlowCore  import GraphEdgeEvent, GRAPH_EDGE_SIZE, EDGE_OPEN, EDGE_CLOSE

import websockets
from live_ingest import enqueue as enqueue_event

logger = logging.getLogger(__name__)


def _get_stage6_detector():
    """Lazily import and return the Stage 6 drift + fanin detector singletons.

    Import is deferred so stream_manager loads cleanly even when
    questdb_writer / topology_drift are not yet installed.
    """
    try:
        from questdb_writer import get_writer
        from topology_drift import get_detector, get_fanin_detector
        writer = get_writer()
        return get_detector(writer=writer), get_fanin_detector(writer=writer)
    except Exception as exc:
        logger.debug("Stage 6 detectors unavailable: %s", exc)
        return None, None


_stage6_detector = None
_stage6_fanin    = None
_stage6_init_done = False


def _ingest_stage6(event: dict) -> None:
    """Forward event to Stage 6 topology drift + fan-in detectors."""
    global _stage6_detector, _stage6_fanin, _stage6_init_done
    if not _stage6_init_done:
        _stage6_init_done = True
        _stage6_detector, _stage6_fanin = _get_stage6_detector()
    if _stage6_detector is not None:
        try:
            _stage6_detector.ingest(event)
        except Exception as exc:
            logger.debug("stage6 drift ingest error: %s", exc)
    if _stage6_fanin is not None:
        try:
            _stage6_fanin.ingest(event)
        except Exception as exc:
            logger.debug("stage6 fanin ingest error: %s", exc)

# Frame tag constants — must match eve-streamer/capture.go
FRAME_TAG_FLOW_CORE   = 0x00
FRAME_TAG_FLOW_EVENT  = 0x01
FRAME_TAG_FLOW_END    = 0x02
FRAME_TAG_EDGE_TICK   = 0x03
FRAME_TAG_GRAPH_EDGE  = 0x04  # Phase B: EDGE_OPEN (event_type=0) + EDGE_CLOSE (event_type=2)


def _ip_to_str(raw_uint32: int) -> str:
    """Convert a little-endian uint32 IP to dotted-decimal string."""
    return socket.inet_ntoa(struct.pack("<I", raw_uint32))


def _flow_core_to_event(fc) -> dict:
    """Convert a FlowCoreRecord to a hypergraph-friendly event dict."""
    src_ip = _ip_to_str(fc.src_ip)
    dst_ip = _ip_to_str(fc.dst_ip)
    return {
        "event_id":  str(uuid.uuid4()),
        "type":      FC_TYPE_NAMES.get(fc.event_type, "flow_update"),
        # Top-level IPs for fast consumer access (worker, recon bridge, etc.)
        "src_ip":    src_ip,
        "dst_ip":    dst_ip,
        "src_port":  fc.src_port,
        "dst_port":  fc.dst_port,
        "proto":     FC_PROTO_NAMES.get(fc.proto, str(fc.proto)),
        "entities": [
            {"key": "src_ip",     "value": src_ip},
            {"key": "dst_ip",     "value": dst_ip},
            {"key": "src_port",   "value": str(fc.src_port)},
            {"key": "dst_port",   "value": str(fc.dst_port)},
            {"key": "proto",      "value": FC_PROTO_NAMES.get(fc.proto, str(fc.proto))},
            {"key": "packets",    "value": str(fc.packets)},
            {"key": "bytes",      "value": str(fc.bytes)},
            {"key": "flow_hash",  "value": hex(fc.flow_hash)},
        ],
        "edges":     [f"{src_ip} -> {dst_ip}"],
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.%fZ",
                                   time.gmtime(fc.ts / 1e9)),
    }


def _flow_event_to_event(fe: rfFlowEvent) -> dict:
    """Convert a rfscythe.FlowEvent FlatBuffers table to a hypergraph event dict."""
    src_ip = _ip_to_str(fe.SrcIpv4())
    dst_ip = _ip_to_str(fe.DstIpv4())
    return {
        "event_id":  str(uuid.uuid4()),
        "type":      fe.event_type_name(),
        # Top-level IPs for fast consumer access (worker, recon bridge, etc.)
        "src_ip":    src_ip,
        "dst_ip":    dst_ip,
        "src_port":  fe.SrcPort(),
        "dst_port":  fe.DstPort(),
        "proto":     fe.proto_name(),
        "entities": [
            {"key": "src_ip",        "value": src_ip},
            {"key": "dst_ip",        "value": dst_ip},
            {"key": "src_port",      "value": str(fe.SrcPort())},
            {"key": "dst_port",      "value": str(fe.DstPort())},
            {"key": "proto",         "value": fe.proto_name()},
            {"key": "packets",       "value": str(fe.Packets())},
            {"key": "bytes",         "value": str(fe.Bytes())},
            {"key": "tcp_flags",     "value": hex(fe.TcpFlags())},
            {"key": "flow_hash",     "value": hex(fe.FlowHash())},
            {"key": "entropy_hint",  "value": str(fe.EntropyHint())},
            {"key": "anomaly_score", "value": str(fe.AnomalyScore())},
        ],
        "edges":     [f"{src_ip} -> {dst_ip}"],
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.%fZ",
                                   time.gmtime(fe.Ts() / 1e9)),
    }


def _flow_end_to_event(fe: FlowEndEvent) -> dict:
    """Convert a timer-triggered FlowEndEvent to a hypergraph FLOW_END event dict."""
    return {
        "event_id":  str(uuid.uuid4()),
        "type":      "flow_end",
        "entities": [
            {"key": "flow_hash", "value": hex(fe.flow_hash)},
            {"key": "packets",   "value": str(fe.packets)},
            {"key": "bytes",     "value": str(fe.bytes)},
        ],
        "edges":     [],
        "closed_at": time.strftime("%Y-%m-%dT%H:%M:%S.%fZ",
                                   time.gmtime(fe.ts / 1e9)),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.%fZ",
                                   time.gmtime(fe.ts / 1e9)),
    }


def _edge_tick_to_event(et: EdgeTick) -> dict:
    """Convert a compressed EdgeTick to a hypergraph flow_update event dict.

    Stage 6: replaces the 56-byte FlowCore FLOW_UPDATE path.  The edge_id is
    a 128-bit (hex string) formed from edge_hi + edge_lo — collision-resistant
    across flow restarts on the same 5-tuple.
    """
    return {
        "event_id": str(uuid.uuid4()),
        "type":     "flow_update",
        "entities": [
            {"key": "edge_id",  "value": et.edge_id},
            {"key": "edge_hi",  "value": hex(et.edge_hi)},
            {"key": "edge_lo",  "value": hex(et.edge_lo)},
            {"key": "packets",  "value": str(et.packets)},
            {"key": "bytes",    "value": str(et.bytes)},
        ],
        "edges":     [],
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _graph_edge_to_event(ge: GraphEdgeEvent) -> dict:
    """Convert a Phase B GraphEdgeEvent to a hypergraph-native event dict.

    event_type == EDGE_OPEN  → type "graph_edge_open"
    event_type == EDGE_CLOSE → type "graph_edge_close"

    node_id_a and node_id_b are pre-computed 64-bit integers — no IP parsing.
    The topology detectors use these directly as hypergraph node identifiers.
    """
    etype = "graph_edge_open" if ge.is_open else "graph_edge_close"
    return {
        "event_id":   str(uuid.uuid4()),
        "type":       etype,
        "entities": [
            {"key": "node_id_a", "value": str(ge.node_a)},
            {"key": "node_id_b", "value": str(ge.node_b)},
            {"key": "edge_id",   "value": hex(ge.edge_id)},
            {"key": "packets",   "value": str(ge.packets)},
            {"key": "bytes",     "value": str(ge.bytes_)},
        ],
        "edges":      [f"node:{ge.node_a:#018x} -> node:{ge.node_b:#018x}"],
        "timestamp":  time.strftime("%Y-%m-%dT%H:%M:%S.%fZ",
                                    time.gmtime(ge.ts / 1e9)),
        # Expose node IDs at top level for fast detector lookup (no entity scan)
        "node_id_a":  ge.node_a,
        "node_id_b":  ge.node_b,
        "edge_id_int": ge.edge_id,
    }


class RemoteStreamManager:
    def __init__(self):
        self.connections: Dict[str, asyncio.Task] = {}
        # Build a plain SelectorEventLoop that lives only in our daemon thread.
        # We bypass both eventlet's monkey-patched new_event_loop() AND the
        # asyncio._check_running guard (which eventlet poisons by making
        # _get_running_loop() return its hub from *any* OS thread).
        import selectors as _sel
        self._loop = asyncio.SelectorEventLoop(_sel.DefaultSelector())
        self._loop._check_running = lambda: None  # allow running alongside eventlet hub
        t = threading.Thread(target=self._run_loop, daemon=True, name='stream-manager-loop')
        t.start()
        # Optional TAK-ML async inference queue — set via set_takml_client()
        self._takml_queue = None

    def set_takml_client(self, client) -> None:
        """Attach a TakMLClient (or AsyncInferenceQueue) to this stream manager.

        Once set, decoded GraphEdge and FlowCore events will have features
        extracted and enqueued for async TAK-ML inference.

        Args:
            client: a TakMLClient or AsyncInferenceQueue instance from tak_ml_client.py
        """
        try:
            from tak_ml_client import AsyncInferenceQueue, TakMLClient
            if isinstance(client, TakMLClient):
                from tak_ml_client import AsyncInferenceQueue
                self._takml_queue = AsyncInferenceQueue(client)
                self._takml_queue.start()
                logger.info("[tak-ml] inference queue started from TakMLClient")
            elif isinstance(client, AsyncInferenceQueue):
                self._takml_queue = client
                if not client._running:
                    client.start()
                logger.info("[tak-ml] AsyncInferenceQueue attached to RemoteStreamManager")
            else:
                logger.warning("[tak-ml] set_takml_client: unknown type %s", type(client))
        except ImportError:
            logger.warning("[tak-ml] tak_ml_client not available — TAK-ML inference disabled")

    def _maybe_enqueue_takml(self, event: dict) -> None:
        """Extract features from event and enqueue for TAK-ML inference if configured."""
        if self._takml_queue is None:
            return
        # Only process edge-rich event types that carry the features we care about
        evt_type = event.get("type", "")
        if evt_type not in ("flow_core", "graph_edge_open", "graph_edge_close", "edge_agg"):
            return
        try:
            from tak_ml_client import extract_flow_features
            features = extract_flow_features(event)
            self._takml_queue.enqueue(
                features,
                callback=self._on_takml_result,
            )
        except Exception as exc:
            logger.debug("[tak-ml] feature extraction error: %s", exc)

    def _on_takml_result(self, score: float, features: dict) -> None:
        """Called by AsyncInferenceQueue worker after successful inference."""
        logger.debug("[tak-ml] infer score=%.3f fan_in=%.0f t_sync=%.2f",
                     score, features.get("fan_in_count", 0),
                     features.get("temporal_sync", 0))
        # Route to GraphOpsAutopilot SentinelLoop if available
        try:
            from graphops_autopilot import GraphOpsAutopilot
            autopilot = GraphOpsAutopilot.get_instance()
            if autopilot is not None:
                autopilot.sentinel.handle_takml_score(score, features)
        except Exception:
            pass  # autopilot not running — that's fine

    def _run_loop(self):
        # Run the plain SelectorEventLoop in this daemon thread.
        # _check_running is patched to a no-op so eventlet's global
        # _get_running_loop() interference can't block us.
        self._loop.run_forever()

    def connect(self, endpoint: str, token: Optional[str] = None) -> None:
        """Open a persistent WebSocket connection to an eve-streamer endpoint."""
        if endpoint in self.connections:
            logger.debug("stream already connected: %s", endpoint)
            return
        coro = self._connect_and_listen(endpoint, token)
        task = asyncio.run_coroutine_threadsafe(coro, self._loop)
        self.connections[endpoint] = task
        logger.info("scheduled connect to %s", endpoint)

    def disconnect(self, endpoint: str) -> None:
        """Stop reconnecting and close the connection to an endpoint."""
        self.connections.pop(endpoint, None)
        logger.info("disconnect requested for %s", endpoint)

    async def _connect_and_listen(self, endpoint: str, token: Optional[str]):
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        backoff = 2.0
        max_backoff = 60.0
        refused_count = 0
        max_refused = 8  # give up after 8 consecutive ECONNREFUSED (~4 min total)

        while endpoint in self.connections:
            try:
                async with websockets.connect(endpoint, additional_headers=headers) as ws:
                    logger.info("connected to remote stream %s", endpoint)
                    backoff = 2.0
                    refused_count = 0  # reset on any successful connection
                    async for msg in ws:
                        event = self._decode(msg, endpoint)
                        if event is not None:
                            enqueue_event(event)
                            _ingest_stage6(event)
                            self._maybe_enqueue_takml(event)
            except ConnectionRefusedError as exc:
                refused_count += 1
                logger.error("connection to %s refused (%d/%d): %s",
                             endpoint, refused_count, max_refused, exc)
                if refused_count >= max_refused:
                    logger.warning(
                        "stream_manager: giving up on %s after %d consecutive "
                        "connection-refused errors — nothing appears to be listening. "
                        "Use Connect button to retry manually.",
                        endpoint, max_refused,
                    )
                    self.connections.pop(endpoint, None)
                    return
            except Exception as exc:
                logger.error("connection to %s failed: %s", endpoint, exc)
            if endpoint not in self.connections:
                break
            logger.info("reconnecting to %s in %.0fs", endpoint, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 1.5, max_backoff)

        self.connections.pop(endpoint, None)
        logger.info("disconnected from %s", endpoint)

    def _decode(self, msg, endpoint: str) -> Optional[dict]:
        try:
            if isinstance(msg, (bytes, bytearray)):
                if len(msg) < 1:
                    logger.warning("empty binary frame from %s", endpoint)
                    return None

                tag  = msg[0]
                body = msg[1:]

                if tag == FRAME_TAG_FLOW_CORE:
                    if len(body) < FLOW_CORE_SIZE:
                        logger.warning("FlowCore frame too short from %s: %d bytes",
                                       endpoint, len(body))
                        return None
                    return _flow_core_to_event(fc_unpack(body))

                if tag == FRAME_TAG_FLOW_EVENT:
                    fe = rfFlowEvent.GetRootAsFlowEvent(bytes(body), 0)
                    return _flow_event_to_event(fe)

                if tag == FRAME_TAG_FLOW_END:
                    if len(body) < FLOW_END_SIZE:
                        logger.warning("FlowEnd frame too short from %s: %d bytes",
                                       endpoint, len(body))
                        return None
                    return _flow_end_to_event(FlowEndEvent.from_bytes(body))

                if tag == FRAME_TAG_EDGE_TICK:
                    if len(body) < EDGE_TICK_SIZE:
                        logger.warning("EdgeTick frame too short from %s: %d bytes",
                                       endpoint, len(body))
                        return None
                    return _edge_tick_to_event(EdgeTick.from_bytes(body))

                if tag == FRAME_TAG_GRAPH_EDGE:
                    if len(body) < GRAPH_EDGE_SIZE:
                        logger.warning("GraphEdge frame too short from %s: %d bytes",
                                       endpoint, len(body))
                        return None
                    return _graph_edge_to_event(GraphEdgeEvent.from_bytes(body))

                # Unknown tag — fall back to JSON in case it's a legacy unframed message
                logger.debug("unknown frame tag 0x%02x from %s, trying JSON", tag, endpoint)
                return json.loads(msg)

            # Plain text — JSON
            return json.loads(msg)

        except Exception as exc:
            logger.warning("failed to decode message from %s: %s", endpoint, exc)
            return None


# global instance used by RPC handler
remote_stream_manager = RemoteStreamManager()
