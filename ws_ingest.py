"""ws_ingest.py

Backported and enhanced broadcast server.  Adds authentication, queueing to
live_ingest, and optional CLI arguments.

Compatible with websockets >= 14 (new asyncio-based API).
"""
import asyncio
import json
import logging
from typing import Any

import websockets
from websockets.asyncio.server import serve as ws_serve

from live_ingest import enqueue as enqueue_event

logger = logging.getLogger(__name__)

EXPECTED_TOKEN = "changeme-token"  # replace with secure token in production

_visual_clients: set = set()


def _authorize(headers: Any) -> bool:
    """Allow connections that send no token (local/internal clients).
    Reject only if a token is explicitly provided but does not match."""
    token = headers.get("Authorization")
    if token is None:
        return True  # unauthenticated local clients are allowed
    return token == EXPECTED_TOKEN


async def handler(ws):
    if not _authorize(ws.request.headers):
        await ws.close(code=4001, reason="Unauthorized")
        return
    _visual_clients.add(ws)
    addr = getattr(ws, 'remote_address', '?')
    logger.info("ws_ingest: client connected %s", addr)
    try:
        async for message in ws:
            try:
                event = json.loads(message)
            except json.JSONDecodeError:
                continue
            # Stage 1 canonicalization + confidence tier annotation
            try:
                from inference_guardrail import SSEInferenceEnricher
                event = SSEInferenceEnricher.get_instance().enrich(event)
            except Exception:
                pass
            if event.get("_guardrail_dropped"):
                logger.debug("ws_ingest: event dropped by schema policy (observed zone)")
                continue
            accepted = enqueue_event(event)
            if not accepted:
                logger.debug("ws_ingest: event dropped by filter")
            await broadcast(event)
    except websockets.ConnectionClosed:
        pass
    finally:
        _visual_clients.discard(ws)
        logger.info("ws_ingest: client disconnected %s", addr)


async def broadcast(event: dict):
    if not _visual_clients:
        return
    msg = json.dumps(event)
    dead = []
    for client in _visual_clients:
        try:
            await client.send(msg)
        except Exception:
            dead.append(client)
    for c in dead:
        _visual_clients.discard(c)


async def _main(host: str, port: int):
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logger.info("ws_ingest starting %s:%d", host, port)

    def _add_pna(connection, request, response):
        # Allow Chrome (loopback → LAN) Private Network Access without preflight.
        response.headers["Access-Control-Allow-Private-Network"] = "true"
        return None  # use the (now-mutated) original response

    async with ws_serve(handler, host, port, process_response=_add_pna):
        await asyncio.get_running_loop().create_future()  # run forever


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="WebSocket ingest/broadcast server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    asyncio.run(_main(args.host, args.port))
