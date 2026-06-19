#!/usr/bin/env python3
"""
VoxelStream Engine
==================
Eve-streamer-inspired binary streaming engine for RF voxel and cluster data.

Replaces the JSON WebSocket path (port 8766) with a purpose-built binary data
plane on port 9001.  The JSON REST endpoints on 8766 remain as a legacy fallback.

Architecture (mirrors eve-streamer's producer → ring buffer → subscriber pattern):

    rf_voxel_processor.py
        ↓  publish(CH_RF_FIELD, binary_frame)
    VoxelStreamHub (asyncio)
        ├── ring buffer (asyncio.Queue, maxsize=128)
        ├── fan-out to all authenticated WS subscribers
        └── LOD downsampler (16³ / 32³ / 64³)

    Browser
        ↓  ws://127.0.0.1:9001/stream
        ↑  binary frames: [1-byte channel][4-byte LE length][payload]

Binary Frame Format
-------------------
Header (5 bytes):
    CH   : uint8   — channel ID (see CH_* constants)
    LEN  : uint32 LE — payload length in bytes

Channels:
    0x01  RF_FIELD       — float32[] voxel grid, preceded by 6-byte dims header
    0x02  CLUSTER_NODES  — array of VoxelNode structs (32 bytes each)
    0x03  CLUSTER_DELTA  — single-node delta (36 bytes)
    0x04  AUTH_CHALLENGE — 32-byte random nonce (server → client)
    0x05  AUTH_RESPONSE  — 4-byte token_len + token bytes (client → server)
    0x06  AUTH_OK        — 16-byte session_id (server → client)
    0x07  AUTH_FAIL      — UTF-8 error message (server → client)
    0x08  PING           — 8-byte monotonic timestamp (ms, LE uint64)
    0x09  PONG           — echo of PING payload

RF_FIELD payload layout:
    SIZE_X : uint16 LE
    SIZE_Y : uint16 LE
    SIZE_Z : uint16 LE
    DATA   : SIZE_X * SIZE_Y * SIZE_Z * float32 (LE)

VoxelNode struct (32 bytes, little-endian):
    node_id      : uint32
    lat          : float32
    lon          : float32
    anomaly      : float32
    threat       : float32
    signal_power : float32
    intensity    : float32
    asn          : uint32

CLUSTER_DELTA payload (36 bytes):
    node_id  : uint32
    d_lat    : float32
    d_lon    : float32
    d_anomaly: float32
    d_threat : float32
    d_power  : float32
    d_intens : float32
    ts_ms    : uint64 LE

LOD Levels (RF_FIELD only):
    lod=0 → 16³  (4.1 KB/frame)
    lod=1 → 32³  (32.8 KB/frame)
    lod=2 → 64³  (262 KB/frame)

Usage:
    python3 voxel_stream_engine.py [--port 9001] [--orchestrator-url http://127.0.0.1:5001]

    or via orchestrator: auto-launched as a subprocess.
"""
from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import logging
import os
import struct
import time
from collections import defaultdict
from typing import Optional

import numpy as np
from scipy.ndimage import zoom

try:
    import websockets
    from websockets.server import WebSocketServerProtocol
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False
    print('[VoxelStream] websockets not found — install: pip install websockets')

log = logging.getLogger('voxel_stream')

# ---------------------------------------------------------------------------
# Channel IDs
# ---------------------------------------------------------------------------
CH_RF_FIELD      = 0x01
CH_CLUSTER_NODES = 0x02
CH_CLUSTER_DELTA = 0x03
CH_AUTH_CHALLENGE = 0x04
CH_AUTH_RESPONSE  = 0x05
CH_AUTH_OK        = 0x06
CH_AUTH_FAIL      = 0x07
CH_PING           = 0x08
CH_PONG           = 0x09

LOD_SIZES = {0: 16, 1: 32, 2: 64}
FRAME_HEADER = struct.Struct('<BL')   # channel(1) + length(4)
NODE_STRUCT   = struct.Struct('<IffffffI')   # 32 bytes per VoxelNode
DELTA_STRUCT  = struct.Struct('<IffffffQ')  # 36 bytes per CLUSTER_DELTA


def pack_frame(channel: int, payload: bytes) -> bytes:
    return FRAME_HEADER.pack(channel, len(payload)) + payload


def pack_rf_field(field: np.ndarray, lod: int = 1) -> bytes:
    """Downsample field to the requested LOD and pack as binary frame."""
    target = LOD_SIZES.get(lod, 32)
    s = field.shape
    if s[0] != target or s[1] != target or s[2] != target:
        factors = (target / s[0], target / s[1], target / s[2])
        field = zoom(field.astype(np.float32), factors, order=1)
    flat = field.astype('<f4').tobytes()
    dims = struct.pack('<HHH', target, target, target)
    return dims + flat


def pack_nodes(nodes: list[dict]) -> bytes:
    parts = []
    for n in nodes:
        parts.append(NODE_STRUCT.pack(
            int(n.get('id_int', 0)) & 0xFFFFFFFF,
            float(n.get('lat', 0.0)),
            float(n.get('lon', 0.0)),
            float(n.get('anomaly', 0.0)),
            float(n.get('threat', 0.0)),
            float(n.get('signal_power', 0.0)),
            float(n.get('intensity', n.get('anomaly', 0.0))),
            int(n.get('asn', 0)) & 0xFFFFFFFF,
        ))
    count_hdr = struct.pack('<I', len(nodes))
    return count_hdr + b''.join(parts)


# ---------------------------------------------------------------------------
# Hub (producer → ring buffer → subscribers)
# ---------------------------------------------------------------------------
class VoxelStreamHub:
    """Central multiplexer: producers publish; authenticated subscribers receive."""

    def __init__(self, ring_size: int = 128) -> None:
        self._ring: asyncio.Queue = None   # initialised in start()
        self._ring_size = ring_size
        self._subscribers: set[asyncio.Queue] = set()
        self._subs_lock = asyncio.Lock()
        # LOD preference per subscriber (default lod=1 → 32³)
        self._sub_lod: dict[asyncio.Queue, int] = {}

    async def start(self) -> None:
        self._ring = asyncio.Queue(maxsize=self._ring_size)
        asyncio.create_task(self._fanout_loop())

    async def _fanout_loop(self) -> None:
        while True:
            frame = await self._ring.get()
            async with self._subs_lock:
                dead = set()
                for q in self._subscribers:
                    try:
                        q.put_nowait(frame)
                    except asyncio.QueueFull:
                        dead.add(q)
                for q in dead:
                    self._subscribers.discard(q)
                    self._sub_lod.pop(q, None)

    async def publish(self, channel: int, payload: bytes) -> None:
        """Publish a raw binary payload on the given channel.
        Drops the oldest frame if the ring is full (back-pressure via drop)."""
        frame = pack_frame(channel, payload)
        try:
            self._ring.put_nowait(frame)
        except asyncio.QueueFull:
            try:
                self._ring.get_nowait()  # drop oldest
            except asyncio.QueueEmpty:
                pass
            await self._ring.put(frame)

    async def publish_rf_field(self, field: np.ndarray) -> None:
        """Publish the RF field at all LOD levels currently subscribed."""
        async with self._subs_lock:
            active_lods = set(self._sub_lod.values()) or {1}
        for lod in active_lods:
            payload = pack_rf_field(field, lod)
            await self.publish(CH_RF_FIELD, payload)

    async def publish_cluster_nodes(self, nodes: list) -> None:
        """Publish a full cluster-nodes snapshot to all subscribers.

        `nodes` is a list of dicts with the same schema expected by pack_nodes().
        Typically called after /api/clusters/intel refreshes the cluster cache.
        """
        payload = pack_nodes(nodes)
        await self.publish(CH_CLUSTER_NODES, payload)

    async def publish_cluster_delta(self, node: dict) -> None:
        """Publish a single-node delta update.

        `node` must contain at least {'id_int', 'lat', 'lon', 'intensity'} and
        any fields that changed since the last snapshot.  The receiver can
        overlay this onto a previously received CH_CLUSTER_NODES frame.
        """
        payload = pack_nodes([node])
        await self.publish(CH_CLUSTER_DELTA, payload)

    async def subscribe(self, lod: int = 1) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=64)
        async with self._subs_lock:
            self._subscribers.add(q)
            self._sub_lod[q] = lod
        return q

    async def unsubscribe(self, q: asyncio.Queue) -> None:
        async with self._subs_lock:
            self._subscribers.discard(q)
            self._sub_lod.pop(q, None)

    def set_lod(self, q: asyncio.Queue, lod: int) -> None:
        self._sub_lod[q] = lod


# Global hub instance — imported by rf_voxel_processor.py
_hub: Optional[VoxelStreamHub] = None


def get_hub() -> Optional[VoxelStreamHub]:
    return _hub


# ---------------------------------------------------------------------------
# Authentication helpers
# ---------------------------------------------------------------------------
async def _validate_token(token: str, orchestrator_url: str, internal_token: str) -> Optional[dict]:
    """Validate bearer token against orchestrator. Returns session dict or None."""
    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f'{orchestrator_url}/api/scythe/sessions/validate',
                headers={
                    'X-Internal-Token': internal_token,
                    'X-Validate-Token': token,
                },
                timeout=aiohttp.ClientTimeout(total=2.0),
            ) as r:
                if r.status == 200:
                    return await r.json()
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# WebSocket connection handler
# ---------------------------------------------------------------------------
async def _handle_ws(
    ws: 'WebSocketServerProtocol',
    orchestrator_url: str,
    internal_token: str,
    hub: VoxelStreamHub,
    no_auth: bool = False,
) -> None:
    peer = ws.remote_address
    log.info(f'[WS] Client connected: {peer}')

    # --- Auth handshake ---
    nonce = os.urandom(32)
    await ws.send(pack_frame(CH_AUTH_CHALLENGE, nonce))

    session: Optional[dict] = None
    if no_auth:
        session = {'operator_id': 'dev', 'instance_id': ''}
    else:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
        except asyncio.TimeoutError:
            await ws.send(pack_frame(CH_AUTH_FAIL, b'Auth timeout'))
            return

        if not isinstance(raw, (bytes, bytearray)) or len(raw) < 6:
            await ws.send(pack_frame(CH_AUTH_FAIL, b'Malformed auth frame'))
            return

        ch, payload_len = FRAME_HEADER.unpack_from(raw, 0)
        if ch != CH_AUTH_RESPONSE or len(raw) < 5 + payload_len:
            await ws.send(pack_frame(CH_AUTH_FAIL, b'Expected AUTH_RESPONSE'))
            return

        payload = raw[5:5 + payload_len]
        if len(payload) < 4:
            await ws.send(pack_frame(CH_AUTH_FAIL, b'Auth payload too short'))
            return

        token_len = struct.unpack_from('<I', payload, 0)[0]
        if len(payload) < 4 + token_len:
            await ws.send(pack_frame(CH_AUTH_FAIL, b'Token length mismatch'))
            return

        token = payload[4:4 + token_len].decode('utf-8', errors='replace').strip()
        session = await _validate_token(token, orchestrator_url, internal_token)
        if session is None:
            await ws.send(pack_frame(CH_AUTH_FAIL, b'Invalid or expired token'))
            log.warning(f'[WS] Auth failed: {peer}')
            return

    # Auth OK — send session_id (first 16 bytes of operator_id hash or random)
    session_marker = os.urandom(16)
    await ws.send(pack_frame(CH_AUTH_OK, session_marker))
    log.info(f'[WS] Auth OK: {peer} op={session.get("operator_id", "?")}')

    # Parse LOD preference from session or default to 1
    lod = int(session.get('lod', 1))
    sub_queue = await hub.subscribe(lod)

    # Ping/pong keepalive
    async def _keepalive():
        while True:
            await asyncio.sleep(15.0)
            ts = int(time.monotonic() * 1000) & 0xFFFFFFFFFFFFFFFF
            try:
                await ws.send(pack_frame(CH_PING, struct.pack('<Q', ts)))
            except Exception:
                break

    ka_task = asyncio.create_task(_keepalive())

    # Inbound message handler (PONG + LOD change + text LOD hints)
    async def _recv_loop():
        nonlocal lod
        async for raw in ws:
            # Text frame: JSON LOD_HINT {"type":"LOD_HINT","camera_altitude":12000,...}
            if isinstance(raw, str):
                try:
                    msg = json.loads(raw)
                    if msg.get('type') == 'LOD_HINT':
                        alt = float(msg.get('camera_altitude', 50_000))
                        new_lod = 0 if alt > 50_000 else (1 if alt > 10_000 else 2)
                        if new_lod in LOD_SIZES and new_lod != lod:
                            lod = new_lod
                            hub.set_lod(sub_queue, lod)
                            log.debug(f'[WS] {peer} LOD_HINT alt={alt:.0f}m → lod={lod}')
                except (ValueError, KeyError):
                    pass
                continue

            if not isinstance(raw, (bytes, bytearray)) or len(raw) < 5:
                continue
            ch, payload_len = FRAME_HEADER.unpack_from(raw, 0)
            payload = raw[5:5 + payload_len] if len(raw) >= 5 + payload_len else b''
            if ch == CH_PONG:
                pass  # keepalive echo — no action needed
            elif ch == CH_AUTH_RESPONSE and len(payload) >= 1:
                # LOD preference update: repurpose AUTH_RESPONSE post-auth
                new_lod = struct.unpack_from('<B', payload, 0)[0]
                if new_lod in LOD_SIZES:
                    lod = new_lod
                    hub.set_lod(sub_queue, lod)

    recv_task = asyncio.create_task(_recv_loop())

    try:
        while True:
            try:
                frame = await asyncio.wait_for(sub_queue.get(), timeout=30.0)
                await ws.send(frame)
            except asyncio.TimeoutError:
                pass  # keepalive task handles pings; loop continues
    except Exception as exc:
        log.debug(f'[WS] Client {peer} disconnected: {exc}')
    finally:
        ka_task.cancel()
        recv_task.cancel()
        await hub.unsubscribe(sub_queue)
        log.info(f'[WS] Client gone: {peer}')


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
async def _amain(
    port: int,
    orchestrator_url: str,
    internal_token: str,
    no_auth: bool,
) -> None:
    global _hub

    if not HAS_WEBSOCKETS:
        log.error('[VoxelStream] websockets package not available — cannot start')
        return

    _hub = VoxelStreamHub()
    await _hub.start()

    import websockets.server as _ws_server

    handler = lambda ws, path=None: _handle_ws(
        ws, orchestrator_url, internal_token, _hub, no_auth
    )

    log.info(f'[VoxelStream] Binary stream engine on ws://127.0.0.1:{port}')
    log.info(f'[VoxelStream] LOD levels: 16³ (lod=0)  32³ (lod=1)  64³ (lod=2)')
    log.info(f'[VoxelStream] Auth: {"DISABLED (dev)" if no_auth else "enabled"}')

    async with _ws_server.serve(handler, '127.0.0.1', port):
        await asyncio.Future()   # run forever


def main() -> None:
    parser = argparse.ArgumentParser(description='SCYTHE VoxelStream binary streaming engine')
    parser.add_argument('--port', type=int, default=9001, help='WebSocket listen port (default: 9001)')
    parser.add_argument('--orchestrator-url', default='http://127.0.0.1:5001',
                        help='Orchestrator URL for token validation')
    parser.add_argument('--internal-token', default='',
                        help='Shared X-Internal-Token for orchestrator calls')
    parser.add_argument('--no-auth', action='store_true',
                        help='Disable auth (dev mode only)')

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [VoxelStream] %(levelname)s %(message)s',
        datefmt='%H:%M:%S',
    )

    asyncio.run(_amain(
        port=args.port,
        orchestrator_url=args.orchestrator_url,
        internal_token=args.internal_token,
        no_auth=args.no_auth,
    ))


if __name__ == '__main__':
    main()
