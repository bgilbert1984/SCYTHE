from dataclasses import dataclass, asdict
from collections import deque
import threading
import time
import json
from typing import Callable, Deque, List, Optional


@dataclass
class RedisGraphEvent:
    event_type: str
    entity_id: str
    entity_kind: str
    sequence_id: int
    timestamp: float
    payload: dict

    def to_xadd_fields(self) -> dict:
        return {
            "event_type": self.event_type,
            "entity_id": self.entity_id,
            "entity_kind": self.entity_kind,
            "sequence_id": str(self.sequence_id),
            "timestamp": str(self.timestamp),
            "payload": json.dumps(self.payload)
        }


class GraphEventBus:
    """Dual-write graph event bus: in-process publish + Redis Streams XADD.

    Usage: inject a redis client (redis-py) or None to operate in in-proc only mode.
    """

    def __init__(self, redis_client=None, stream_key: str = "graph:events", max_history: int = 5000):
        self.redis = redis_client
        self.stream_key = stream_key
        self.subscribers: List[Callable] = []
        self.history: Deque = deque(maxlen=max_history)
        self.sequence = 0
        self.lock = threading.RLock()

    def subscribe(self, cb: Callable):
        with self.lock:
            if cb not in self.subscribers:
                self.subscribers.append(cb)

    def unsubscribe(self, cb: Callable):
        """Remove a previously registered subscriber callback."""
        with self.lock:
            try:
                self.subscribers.remove(cb)
            except ValueError:
                pass

    def publish(self, event) -> dict:
        """Publish a GraphEvent-like object.

        Returns a dict: { 'msg_id': <redis_msg_id_or_none>, 'sequence_id': <assigned_sequence> }.
        Always assigns an in-process numeric sequence and fanouts to subscribers.
        """
        msg_id = None
        with self.lock:
            self.sequence += 1
            assigned_seq = self.sequence
            # allow event to carry sequence_id
            try:
                event.sequence_id = assigned_seq
            except Exception:
                pass

            # in-process fanout
            self.history.append(event)
            for cb in list(self.subscribers):
                try:
                    cb(event)
                except Exception:
                    continue

            # durable XADD
            if self.redis:
                try:
                    rge = RedisGraphEvent(
                        event_type=getattr(event, 'event_type', getattr(event, 'type', 'unknown')),
                        entity_id=getattr(event, 'entity_id', getattr(event, 'id', '')),
                        entity_kind=getattr(event, 'entity_kind', getattr(event, 'entity_type', 'entity')),
                        sequence_id=getattr(event, 'sequence_id', assigned_seq),
                        timestamp=time.time(),
                        payload=getattr(event, 'entity_data', getattr(event, 'payload', {})) or {}
                    )
                    msg_id = self.redis.xadd(self.stream_key, rge.to_xadd_fields(), maxlen=100000, approximate=True)
                except Exception:
                    msg_id = None

        return {'msg_id': msg_id, 'sequence_id': assigned_seq}

    def replay(self, since_seq: int = 0):
        """Replay in-process history after sequence (fast local)."""
        with self.lock:
            return [e for e in list(self.history) if getattr(e, 'sequence_id', 0) > since_seq]

    def replay_from_stream(self, redis_client, from_id: str = '0-0', count: int = 100):
        """Read historical events from Redis stream starting at `from_id` (inclusive)."""
        if not redis_client:
            return []
        try:
            entries = redis_client.xread({self.stream_key: from_id}, count=count)
            out = []
            for stream, msgs in entries:
                for msg_id, fields in msgs:
                    data = fields.get('payload') or fields.get(b'payload')
                    if isinstance(data, bytes):
                        data = data.decode()
                    try:
                        payload = json.loads(data) if data else {}
                    except Exception:
                        payload = {}
                    out.append({
                        'id': msg_id,
                        'event_type': fields.get('event_type') or fields.get(b'event_type'),
                        'entity_id': fields.get('entity_id') or fields.get(b'entity_id'),
                        'entity_kind': fields.get('entity_kind') or fields.get(b'entity_kind'),
                        'sequence_id': int(fields.get('sequence_id') or fields.get(b'sequence_id') or 0),
                        'timestamp': float(fields.get('timestamp') or fields.get(b'timestamp') or 0),
                        'payload': payload
                    })
            return out
        except Exception:
            return []
