#!/usr/bin/env python3
# filepath: /home/gorelock/gemma/NerfEngine/rf_voxel_processor.py
"""
RF Voxel Processor

This module implements a real-time RF voxel mapping system that processes raw RF data
into 3D voxel grids. It provides a WebSocket API for streaming voxel data and stores
historical data in QuestDB.

The system can be integrated with the RF SCYTHE visualization to show RF signal
propagation in 3D space.
"""

import asyncio
import io
import json
import numpy as np
import struct
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from scipy.ndimage import gaussian_filter, zoom as scipy_zoom
import logging
import os
import time
from typing import Dict, List, Any, Optional

# GPU field generation (PyTorch CUDA, CPU fallback)
try:
    import torch
    _TORCH_DEVICE = 'cuda' if torch.cuda.is_available() else 'cpu'
    logger_pre = logging.getLogger(__name__)
    logger_pre.info(f'PyTorch device: {_TORCH_DEVICE}')
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False
    _TORCH_DEVICE = 'cpu'

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Try to import QuestDB
try:
    from questdb.ingress import Sender
    QUESTDB_AVAILABLE = True
    logger.info("QuestDB ingress available for RF voxel storage")
except ImportError:
    QUESTDB_AVAILABLE = False
    logger.warning("QuestDB not available. Install with: pip install questdb")

# Initialize FastAPI
app = FastAPI(title="RF Voxel Processor API",
              description="Real-time RF voxel mapping with 3D signal processing")

# Allow browsers on loopback to connect via LAN IP (Chrome Private Network Access).
class _PrivateNetworkAccessMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: StarletteRequest, call_next):
        response = await call_next(request)
        response.headers["Access-Control-Allow-Private-Network"] = "true"
        response.headers["Access-Control-Allow-Origin"] = "*"
        return response

app.add_middleware(_PrivateNetworkAccessMiddleware)

# QuestDB Connection settings
QUESTDB_HOST = os.environ.get("QUESTDB_HOST", "localhost")
QUESTDB_PORT = int(os.environ.get("QUESTDB_PORT", 9009))
QUESTDB_TABLE = os.environ.get("QUESTDB_TABLE", "rf_voxels")

# Internal token — set by the orchestrator when launching this subprocess.
# If unset, the compute endpoint is accessible on loopback without auth
# (acceptable for localhost-only deployment; enforce in production via env).
_INTERNAL_TOKEN: str = os.environ.get('SCYTHE_INTERNAL_TOKEN', '')


# Initialize QuestDB sender if available
sender = None
if QUESTDB_AVAILABLE:
    try:
        sender = Sender(QUESTDB_HOST, QUESTDB_PORT)
        logger.info(f"Connected to QuestDB at {QUESTDB_HOST}:{QUESTDB_PORT}")
    except Exception as e:
        logger.error(f"Failed to connect to QuestDB: {e}")
        sender = None

# WebSocket clients
clients: List[WebSocket] = []

# Latest processed voxel field — cached for gRPC StreamRFField polling
_latest_field: Optional[np.ndarray] = None
_latest_field_ts: float = 0.0  # epoch seconds
_latest_field_lock = asyncio.Lock()

# Voxel processing statistics
processing_stats = {
    "total_updates": 0,
    "start_time": time.time(),
    "last_voxel_size": 0,
    "average_processing_time": 0,
    "peak_values": []
}

def process_rf_signals(raw_data: List[float], grid_size: Optional[List[int]] = None) -> List:
    """
    Process raw RF signal data into a 3D voxel grid with smoothing

    Args:
        raw_data: Raw RF signal data as a flattened array
        grid_size: Optional 3D grid dimensions [x, y, z]

    Returns:
        Processed 3D voxel grid data
    """
    start_time = time.time()

    # Default grid size is 16x16x16 if not specified
    if grid_size is None:
        grid_size = [16, 16, 16]

    # Reshape the data into a 3D grid
    try:
        total_voxels = grid_size[0] * grid_size[1] * grid_size[2]

        # If data is shorter than expected, pad with zeros
        if len(raw_data) < total_voxels:
            raw_data = raw_data + [0] * (total_voxels - len(raw_data))
        # If data is longer than expected, truncate
        elif len(raw_data) > total_voxels:
            raw_data = raw_data[:total_voxels]

        # Reshape into 3D grid
        rf_data = np.array(raw_data).reshape(grid_size)

        # Apply Gaussian smoothing for noise reduction
        smoothed = gaussian_filter(rf_data, sigma=1.2)

        # Find peak values
        peak_value = np.max(smoothed)
        peak_position = np.unravel_index(np.argmax(smoothed), smoothed.shape)

        # Update statistics
        processing_stats["total_updates"] += 1
        processing_stats["last_voxel_size"] = total_voxels
        processing_time = time.time() - start_time
        processing_stats["average_processing_time"] = (
            (processing_stats["average_processing_time"] * (processing_stats["total_updates"] - 1) + processing_time) /
            processing_stats["total_updates"]
        )
        processing_stats["peak_values"].append({
            "value": float(peak_value),
            "position": [int(p) for p in peak_position],
            "timestamp": time.time()
        })

        # Keep only the last 100 peak values
        if len(processing_stats["peak_values"]) > 100:
            processing_stats["peak_values"] = processing_stats["peak_values"][-100:]

        # Convert back to Python list for JSON serialization
        voxel_data = smoothed.tolist()

        return voxel_data

    except Exception as e:
        logger.error(f"Error processing RF signals: {e}")
        # Return empty 3D grid in case of error
        return np.zeros(grid_size).tolist()


# ---------------------------------------------------------------------------
# GPU-accelerated RF field synthesis
# ---------------------------------------------------------------------------

def compute_field_gpu(nodes: list, S: int = 32) -> np.ndarray:
    """Synthesize a normalised S³ RF influence field from node positions.

    Each node is [x, y, z, intensity] in normalised [-1, 1] space.
    Uses inverse-distance weighting: field[p] += intensity / (||p - pos||² + ε).
    Falls back to CPU numpy when CUDA is unavailable.
    """
    if not nodes:
        return np.zeros((S, S, S), dtype=np.float32)

    if TORCH_AVAILABLE:
        try:
            device = _TORCH_DEVICE
            n = torch.tensor(nodes, dtype=torch.float32, device=device)   # [N, 4]
            ax = torch.linspace(-1.0, 1.0, S, device=device)
            grid = torch.stack(torch.meshgrid(ax, ax, ax, indexing='ij'), dim=-1)  # [S,S,S,3]

            field = torch.zeros((S, S, S), device=device)
            for row in n:
                pos = row[:3]
                w = row[3]
                dist2 = torch.sum((grid - pos) ** 2, dim=-1)
                field += w / (dist2 + 0.01)

            mx = field.max()
            if mx > 0:
                field = field / mx
            return field.float().cpu().numpy()
        except Exception as exc:
            logger.warning(f'GPU field synthesis failed, falling back to CPU: {exc}')

    # CPU fallback: vectorised numpy inverse-distance weighting
    ax = np.linspace(-1.0, 1.0, S, dtype=np.float32)
    gx, gy, gz = np.meshgrid(ax, ax, ax, indexing='ij')
    grid = np.stack([gx, gy, gz], axis=-1)  # [S,S,S,3]
    field = np.zeros((S, S, S), dtype=np.float32)
    for nx, ny, nz, intensity in nodes:
        pos = np.array([nx, ny, nz], dtype=np.float32)
        dist2 = np.sum((grid - pos) ** 2, axis=-1)
        field += intensity / (dist2 + 0.01)
    mx = field.max()
    if mx > 0:
        field /= mx
    return field


def _pack_field_binary(field) -> bytes:
    """Serialise a 3-D float32 field as [sx:u16][sy:u16][sz:u16][float32...].

    Accepts numpy ndarray *or* torch.Tensor (CPU or CUDA).
    For GPU tensors the transfer is .cpu().contiguous() — no header corruption
    from dtype reinterpretation tricks.
    """
    import numpy as _np  # local to avoid import-time torch dep in plain numpy path
    try:
        import torch as _torch
        if isinstance(field, _torch.Tensor):
            field = field.detach().cpu().contiguous().numpy()
    except ImportError:
        pass
    field = _np.asarray(field, dtype='<f4')
    sx, sy, sz = field.shape
    return struct.pack('<HHH', sx, sy, sz) + field.tobytes()


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------

@app.post('/api/gpu-field')
async def gpu_field(request: Request):
    """Compute a GPU-synthesised RF field from caller-supplied node positions.

    Body JSON: {"nodes": [[x,y,z,intensity],...], "size": 32}
    Response: raw float32 binary (application/octet-stream).
    Dims header prepended: [sx:u16][sy:u16][sz:u16].
    Protected by X-Internal-Token when SCYTHE_INTERNAL_TOKEN env-var is set.
    """
    if _INTERNAL_TOKEN:
        tok = request.headers.get('X-Internal-Token', '')
        if tok != _INTERNAL_TOKEN:
            raise HTTPException(status_code=403, detail='Forbidden')
    body = await request.json()
    nodes = body.get('nodes', [])
    S = int(body.get('size', 32))
    if S not in (16, 32, 64):
        raise HTTPException(status_code=400, detail='size must be 16, 32, or 64')
    field = compute_field_gpu(nodes, S)
    return Response(content=_pack_field_binary(field), media_type='application/octet-stream')


@app.get('/api/voxel/latest-field')
async def latest_field(lod: int = 1):
    """Return the most recently processed RF voxel field as raw binary.

    Query params:
      lod  (int, default 1):  0=16³  1=32³  2=64³
    Response headers:
      X-Field-Timestamp: ISO-8601 UTC of when the field was last updated
    Returns 204 if no field has been computed yet.
    """
    global _latest_field, _latest_field_ts
    async with _latest_field_lock:
        field = _latest_field
        ts = _latest_field_ts

    if field is None:
        return Response(status_code=204)

    # Downsample to requested LOD
    target_s = {0: 16, 1: 32, 2: 64}.get(lod, 32)
    if field.shape[0] != target_s:
        factor = target_s / field.shape[0]
        field = scipy_zoom(field, factor, order=1).astype(np.float32)

    ts_iso = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(ts))
    headers = {'X-Field-Timestamp': ts_iso, 'X-Field-Lod': str(lod)}
    return Response(
        content=_pack_field_binary(field),
        media_type='application/octet-stream',
        headers=headers,
    )


@app.get("/")
async def read_root():
    """Root endpoint providing basic information about the RF voxel processor service"""
    return {
        "name": "RF Voxel Processor API",
        "status": "running",
        "websocket_endpoint": "/ws",
        "uptime_seconds": time.time() - processing_stats["start_time"],
        "total_updates": processing_stats["total_updates"],
        "questdb_available": QUESTDB_AVAILABLE and sender is not None
    }

@app.get("/stats")
async def get_stats():
    """Endpoint providing current processing statistics"""
    return {
        "processing_stats": {
            k: v for k, v in processing_stats.items()
            if k != "peak_values"  # Exclude the large peak_values array
        },
        "uptime_seconds": time.time() - processing_stats["start_time"],
        "updates_per_second": processing_stats["total_updates"] / (time.time() - processing_stats["start_time"])
                            if time.time() > processing_stats["start_time"] else 0,
        "latest_peak": processing_stats["peak_values"][-1] if processing_stats["peak_values"] else None
    }

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for RF voxel data streaming

    Clients can connect to this endpoint to receive real-time updates of
    RF voxel data. Clients should send raw RF data as a flattened array
    with optional grid dimensions.
    """
    await websocket.accept()
    clients.append(websocket)
    client_id = len(clients)
    logger.info(f"New client connected: #{client_id}")

    try:
        while True:
            # Receive raw data from the client
            data = await websocket.receive_json()

            # Extract raw data and optional grid size
            raw_data = data.get("data", [])
            grid_size = data.get("grid_size", [16, 16, 16])

            # Process the RF signals (CPU smoothed grid for legacy JSON clients)
            processed_data = process_rf_signals(raw_data, grid_size)

            # GPU-synthesised field for gRPC / binary stream consumers.
            # Nodes list derived from the raw signal data treated as intensity values.
            S = 32  # canonical LOD-1 resolution
            nodes_gpu = []
            flat = np.array(raw_data, dtype=np.float32)
            if flat.size >= 4:
                # Stride through data 4-at-a-time as [x, y, z, intensity] tuples
                for i in range(0, min(len(flat) - 3, 512), 4):
                    nodes_gpu.append(flat[i:i+4].tolist())
            if nodes_gpu:
                gpu_field = compute_field_gpu(nodes_gpu, S)
            else:
                gpu_field = np.array(processed_data, dtype=np.float32).reshape(grid_size)

            # Cache latest field for /api/voxel/latest-field polling
            async with _latest_field_lock:
                global _latest_field, _latest_field_ts
                _latest_field = gpu_field
                _latest_field_ts = time.time()

            # Add timestamp and metadata
            result = {
                "timestamp": time.time(),
                "voxels": processed_data,
                "grid_size": grid_size,
                "peak": processing_stats["peak_values"][-1] if processing_stats["peak_values"] else None,
                "update_count": processing_stats["total_updates"]
            }

            # Store in QuestDB if available
            if sender is not None:
                try:
                    # For voxel data, we store a compressed representation or metadata
                    # since storing the full 3D grid in each row would be inefficient
                    peak = processing_stats["peak_values"][-1] if processing_stats["peak_values"] else {"value": 0, "position": [0, 0, 0]}

                    sender.row(QUESTDB_TABLE) \
                        .symbol("source", f"client_{client_id}") \
                        .at_now() \
                        .double_column("peak_value", peak["value"]) \
                        .long_column("peak_x", peak["position"][0]) \
                        .long_column("peak_y", peak["position"][1]) \
                        .long_column("peak_z", peak["position"][2]) \
                        .double_column("processing_time", processing_stats["average_processing_time"]) \
                        .at_now()
                except Exception as e:
                    logger.error(f"Failed to store data in QuestDB: {e}")

            # Broadcast to all legacy JSON clients
            for client in clients:
                try:
                    await client.send_json(result)
                except Exception as e:
                    logger.error(f"Failed to send to a client: {e}")

            # Publish binary frames to the VoxelStream engine (port 9001)
            try:
                from voxel_stream_engine import get_hub, pack_nodes
                hub = get_hub()
                if hub is not None:
                    voxel_array = np.array(processed_data).reshape(grid_size)
                    await hub.publish_rf_field(voxel_array)
            except Exception as _pub_exc:
                logger.debug(f'VoxelStream publish skipped: {_pub_exc}')

    except WebSocketDisconnect:
        logger.info(f"Client #{client_id} disconnected normally")
    except Exception as e:
        logger.error(f"WebSocket Error with client #{client_id}: {e}")
    finally:
        # Remove the client from the list
        if websocket in clients:
            clients.remove(websocket)
        logger.info(f"Client #{client_id} connection cleaned up, {len(clients)} clients remaining")

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup resources on shutdown"""
    logger.info("Shutting down RF Voxel Processor server...")

    # Close QuestDB connection if it exists
    if sender is not None:
        try:
            sender.close()
            logger.info("QuestDB connection closed")
        except Exception as e:
            logger.error(f"Error closing QuestDB connection: {e}")

def main():
    """Run the FastAPI application with Uvicorn"""
    logger.info("Starting RF Voxel Processor server")
    uvicorn.run(app, host="0.0.0.0", port=8766)  # Using port 8766 to avoid conflict with the tracking server

if __name__ == "__main__":
    main()
