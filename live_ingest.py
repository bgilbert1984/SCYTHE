"""live_ingest.py

Lightweight in-memory queue for hypergraph events streamed from remote VM.
Includes simple backpressure filters (event frequency) and novelty scoring.

This module is intentionally separate from the core engine so that the
WebSocket broadcast server can enqueue without touching the graph directly.
The MCP tool `ingest_live_event` then dequeues events under analyst/executor
control, ensuring all constitutional gates apply.
"""
from collections import deque, defaultdict
import logging
import random
import time
from typing import Any, Dict, List, Set

logger = logging.getLogger(__name__)

# Lazy import — engine is optional; live_ingest degrades gracefully without it
try:
    from adaptive_schema_engine import engine as _ase
    _ASE_AVAILABLE = True
except ImportError:
    _ase = None  # type: ignore
    _ASE_AVAILABLE = False


class LiveEventQueue:
    def __init__(self, max_size: int = 10000):
        self.queue: deque = deque()
        self.max_size = max_size
        # sliding window for frequency counting (timestamp, event_type)
        self.window: deque = deque()
        self.type_counts: Dict[str, int] = defaultdict(int)

        # Drop telemetry — logged periodically so silent drops are visible
        self._drop_counts: Dict[str, int] = defaultdict(int)
        self._drop_saturated: int = 0
        self._last_drop_log: float = time.time()
        self._DROP_LOG_INTERVAL: float = 30.0  # seconds

        # Schema fingerprinting — alert when a new key-set is seen
        self._seen_schemas: Set[int] = set()

    def _cleanup_window(self, now: float, window_seconds: float = 60.0) -> None:
        """Remove old entries from frequency window."""
        cutoff = now - window_seconds
        while self.window and self.window[0][0] < cutoff:
            ts, etype = self.window.popleft()
            self.type_counts[etype] -= 1
            if self.type_counts[etype] <= 0:
                del self.type_counts[etype]

    def _should_drop_type(self, etype: str, limit: int = 1000) -> bool:
        """Simple threshold-based drop: too many of same type in last minute."""
        return self.type_counts.get(etype, 0) > limit

    def _check_schema(self, event: Dict[str, Any]) -> None:
        """Fingerprint the event key-set; log on first observation of a new schema."""
        # Exclude per-event volatile keys from the fingerprint
        stable_keys = frozenset(k for k in event if not k.startswith('_') and k != 'event_id')
        schema_hash = hash(stable_keys)
        if schema_hash not in self._seen_schemas:
            self._seen_schemas.add(schema_hash)
            logger.info(
                '[live_ingest] new schema detected (hash=%x type=%s keys=%s)',
                schema_hash,
                event.get('type', '<unknown>'),
                sorted(stable_keys),
            )

    def _flush_drop_log(self, now: float) -> None:
        """Periodically emit a summary of dropped events so operators can see them."""
        if now - self._last_drop_log < self._DROP_LOG_INTERVAL:
            return
        total = sum(self._drop_counts.values()) + self._drop_saturated
        if total:
            logger.warning(
                '[live_ingest] drops in last %.0fs: %d backpressure %s  %d queue-full',
                self._DROP_LOG_INTERVAL,
                sum(self._drop_counts.values()),
                dict(self._drop_counts),
                self._drop_saturated,
            )
            self._drop_counts.clear()
            self._drop_saturated = 0
        self._last_drop_log = now

    def enqueue(self, event: Dict[str, Any]) -> bool:
        """Attempt to enqueue an event.

        Returns True if enqueued; False if dropped by filter or queue full.
        Quarantined events return True (absorbed, not lost).
        """
        now = time.time()

        # ── Adaptive schema layer ─────────────────────────────────────────
        if _ASE_AVAILABLE:
            event = _ase.canonicalize(event)
            route = _ase.route(event.get('_schema_hash', 0))
            if route == 'quarantine':
                # Fix 4: trickle 5% of quarantined events into the queue so the
                # schema can accumulate real confidence without full starvation.
                if random.random() >= 0.05:
                    _ase.quarantine(event)
                    return True   # absorbed into quarantine, not discarded
                # else: fall through and admit this event normally

        etype = event.get("type", "<unknown>")

        # sliding-window frequency update
        self.window.append((now, etype))
        self.type_counts[etype] += 1
        self._cleanup_window(now)

        # Emit drop-rate summary periodically
        self._flush_drop_log(now)

        # Adaptive sampling + priority elevation under queue pressure
        if _ASE_AVAILABLE and not _ase.should_admit(event, len(self.queue)):
            self._drop_counts[etype] += 1
            return False

        # backpressure filter: drop if one type dominates
        if self._should_drop_type(etype):
            self._drop_counts[etype] += 1
            return False

        if len(self.queue) >= self.max_size:
            self._drop_saturated += 1
            return False

        # Fingerprint schema on first-seen key-set (zero cost after warm-up)
        if not _ASE_AVAILABLE:
            self._check_schema(event)

        self.queue.append(event)
        return True

    def dequeue(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Pop up to `limit` events from the queue."""
        events: List[Dict[str, Any]] = []
        for _ in range(min(limit, len(self.queue))):
            events.append(self.queue.popleft())
        return events

    @property
    def stats(self) -> Dict[str, Any]:
        """Snapshot of queue health — exposed via /api/stream/list."""
        base = {
            'queue_depth': len(self.queue),
            'type_counts_60s': dict(self.type_counts),
            'schemas_seen': len(self._seen_schemas),
            'drops_backpressure': dict(self._drop_counts),
            'drops_saturated': self._drop_saturated,
        }
        if _ASE_AVAILABLE:
            base['adaptive_engine'] = _ase.stats
        return base


# single global instance used by websocket server and MCP tool
live_event_queue = LiveEventQueue()


def enqueue(event: Dict[str, Any]) -> bool:
    return live_event_queue.enqueue(event)


def dequeue(limit: int = 10) -> List[Dict[str, Any]]:
    return live_event_queue.dequeue(limit)
