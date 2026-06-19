"""adaptive_schema_engine.py

Drop-in adaptive ingestion layer for the SCYTHE live-ingest pipeline.

Plugs between stream_manager → live_ingest and adds:

  1. Auto-alias learning  — discovers new IP field names at runtime
  2. Schema confidence    — tracks hit-rate per schema hash
  3. Adaptive routing     — fast / adaptive / quarantine based on confidence
  4. Churn detection      — flags unstable or adversarial streams
  5. Adaptive sampling    — sheds low-priority event types under queue pressure
  6. Priority elevation   — lets high-value events bypass the drop filter

Usage (in live_ingest.py):
    from adaptive_schema_engine import engine as _ase

    # Before appending to queue:
    event = _ase.canonicalize(event)
    route  = _ase.route(event.get('_schema_hash', 0))
    if route == 'quarantine':
        _ase.quarantine(event)
        return True   # absorbed, not lost
    if route == 'sample' and not _ase.sample_accept(event):
        return False  # intentional shed
"""

from __future__ import annotations

import ipaddress
import logging
import time
from collections import deque, defaultdict
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Default alias tables ──────────────────────────────────────────────────────
_DEFAULT_SRC_ALIASES: Tuple[str, ...] = (
    'src_ip', 'src', 'source_ip', 'ip_src', 'SrcIp', 'sourceIp',
    'orig_ip', 'client_ip', 'origin_ip',
)
_DEFAULT_DST_ALIASES: Tuple[str, ...] = (
    'dst_ip', 'dst', 'dest_ip', 'ip_dst', 'DstIp', 'destIp',
    'resp_ip', 'server_ip', 'target_ip',
)

# ── Confidence thresholds ─────────────────────────────────────────────────────
_CONFIDENCE_FAST        = 0.80   # ≥ 80% IP hits → trusted fast path
_CONFIDENCE_ADAPTIVE    = 0.20   # 20–80% → use adaptive parser
_CHURN_WINDOW_S         = 60.0   # seconds for churn rate window
_CHURN_ALERT_RATE       = 10     # distinct schema hashes per minute → alert
_QUARANTINE_MAX         = 500    # max events held in quarantine buffer
_SAMPLE_QUEUE_THRESHOLD = 0.75   # start sampling when queue > 75% full


def _is_ip(value: Any) -> bool:
    """Return True if value looks like a valid IP address string."""
    if not isinstance(value, str):
        return False
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


class SchemaRecord:
    """Per-schema statistics used for routing and confidence scoring."""

    __slots__ = ('hash', 'keys', 'event_type', 'count', 'ip_hits',
                 'first_seen', 'last_seen', 'route_override')

    def __init__(self, schema_hash: int, keys: frozenset, event_type: str) -> None:
        self.hash          = schema_hash
        self.keys          = keys
        self.event_type    = event_type
        self.count         = 0
        self.ip_hits       = 0
        self.first_seen    = time.time()
        self.last_seen     = time.time()
        self.route_override: Optional[str] = None  # 'quarantine' forced by churn guard

    @property
    def confidence(self) -> float:
        return self.ip_hits / self.count if self.count else 0.0

    def record(self, found_ip: bool) -> None:
        self.count    += 1
        self.last_seen = time.time()
        if found_ip:
            self.ip_hits += 1


class AdaptiveSchemaEngine:
    """
    Self-learning ingestion layer.  One singleton is shared by live_ingest
    and the recon worker.  Thread-safe for concurrent read/write because
    all mutable state is updated under Python's GIL (no explicit locking
    needed for dict / list append operations at CPython).
    """

    def __init__(
        self,
        priority_check: Optional[Callable[[str], bool]] = None,
        max_queue_size: int = 10_000,
    ) -> None:
        # Mutable alias tables — extended at runtime
        self.aliases: Dict[str, List[str]] = {
            'src_ip': list(_DEFAULT_SRC_ALIASES),
            'dst_ip': list(_DEFAULT_DST_ALIASES),
        }

        self._schemas: Dict[int, SchemaRecord] = {}
        self._quarantine: deque = deque(maxlen=_QUARANTINE_MAX)
        self._timeline: deque = deque(maxlen=2000)   # (ts, schema_hash)
        self._sample_counters: Dict[str, int] = defaultdict(int)
        self._max_queue_size = max_queue_size

        # Optional callback: returns True if IP is known-high-priority (C2, shadow)
        self._priority_check = priority_check or (lambda ip: False)

        # Learning stats
        self.aliases_learned: int = 0
        self.schemas_quarantined: int = 0

    # ── Public API ────────────────────────────────────────────────────────────

    def canonicalize(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """
        Promote known IP aliases to top-level canonical keys, fingerprint
        schema, and attach _schema_hash.  Returns the (mutated) event.
        """
        # Fix 3: promote IPs out of entities[] FIRST so alias learning and
        # top-level lookup both have something to work with
        for ent in event.get('entities', []):
            k = ent.get('key')
            v = ent.get('value')
            if k and v and k in ('src_ip', 'dst_ip'):
                event.setdefault(k, v)

        # Promote any alias that resolves to a real IP to top-level canonical key
        for canonical, aliases in self.aliases.items():
            if canonical not in event or not _is_ip(str(event.get(canonical, ''))):
                for alias in aliases:
                    val = event.get(alias)
                    if val and _is_ip(str(val)):
                        event[canonical] = val
                        break

        # Auto-learn new IP-shaped fields not in our alias table
        self._learn_aliases(event)

        # Fingerprint stable key-set (exclude per-event volatile keys)
        stable_keys = frozenset(
            k for k in event
            if not k.startswith('_') and k not in ('event_id', 'timestamp')
        )
        h = hash(stable_keys)
        event['_schema_hash'] = h

        # Register or update schema record
        if h not in self._schemas:
            rec = SchemaRecord(h, stable_keys, event.get('type', '<unknown>'))
            self._schemas[h] = rec
            logger.info(
                '[ase] new schema h=%x type=%s keys=%s',
                h, rec.event_type, sorted(stable_keys),
            )

        # Fix 2: accumulate confidence directly in canonicalize so it builds
        # without depending on the recon worker feedback loop
        rec = self._schemas[h]
        has_ip = bool(event.get('src_ip') or event.get('dst_ip'))
        rec.record(found_ip=has_ip)

        self._timeline.append((time.time(), h))
        self._check_churn()

        return event

    def record_outcome(self, schema_hash: int, found_ip: bool) -> None:
        """Called by the recon worker after attempting IP extraction."""
        rec = self._schemas.get(schema_hash)
        if rec:
            rec.record(found_ip)

    def route(self, schema_hash: int) -> str:
        """
        Returns one of:
          'fast'        — trusted schema, full throughput
          'adaptive'    — partially known, parsed with alias fallback
          'quarantine'  — unknown/suspicious, held for sampling
          'sample'      — queue pressure shed path (caller checks sample_accept)
        """
        # Fix 1: unknown schema → adaptive (admit for learning), not quarantine.
        # Cold-start hostile behaviour: rec is None only briefly — after the first
        # canonicalize() call the schema is registered and confidence starts building.
        rec = self._schemas.get(schema_hash)
        if rec is None:
            return 'adaptive'
        if rec.route_override:
            return rec.route_override
        if rec.confidence >= _CONFIDENCE_FAST and rec.count >= 20:
            return 'fast'
        if rec.confidence >= _CONFIDENCE_ADAPTIVE:
            return 'adaptive'
        if rec.count < 10:
            # Not enough data yet — admit tentatively
            return 'adaptive'
        return 'quarantine'

    def should_admit(self, event: Dict[str, Any], queue_depth: int) -> bool:
        """
        Combined gate: returns True if the event should be enqueued.
        Handles priority elevation (bypass drop for C2/shadow IPs) and
        adaptive sampling under queue pressure.
        """
        # Priority elevation — always admit high-value events
        for key in ('src_ip', 'dst_ip'):
            ip = event.get(key)
            if ip and self._priority_check(ip):
                return True

        # Adaptive sampling under queue pressure
        pressure = queue_depth / self._max_queue_size
        if pressure >= _SAMPLE_QUEUE_THRESHOLD:
            etype = event.get('type', '')
            # Only shed low-priority update events; never shed start/end/alert
            if etype in ('flow_update', 'flow_core', 'graph_edge_open'):
                keep_rate = max(1, int(1.0 / (1.0 - pressure + 0.01)))
                self._sample_counters[etype] += 1
                if self._sample_counters[etype] % keep_rate != 0:
                    return False

        return True

    def quarantine(self, event: Dict[str, Any]) -> None:
        """Absorb an unknown-schema event into the quarantine buffer."""
        self._quarantine.append(event)
        self.schemas_quarantined += 1

    def flush_quarantine(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Return up to `limit` quarantined events for offline inspection."""
        out = []
        for _ in range(min(limit, len(self._quarantine))):
            out.append(self._quarantine.popleft())
        return out

    @property
    def stats(self) -> Dict[str, Any]:
        """Snapshot suitable for inclusion in /api/stream/list."""
        top = sorted(
            self._schemas.values(),
            key=lambda r: r.count, reverse=True
        )[:5]
        return {
            'schemas_known':      len(self._schemas),
            'schemas_quarantined': self.schemas_quarantined,
            'aliases_learned':    self.aliases_learned,
            'quarantine_depth':   len(self._quarantine),
            'churn_rate_per_min': self._churn_rate(),
            'top_schemas': [
                {
                    'hash':       hex(r.hash),
                    'type':       r.event_type,
                    'count':      r.count,
                    'confidence': round(r.confidence, 3),
                    'route':      self.route(r.hash),
                }
                for r in top
            ],
        }

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _learn_aliases(self, event: Dict[str, Any]) -> None:
        """Scan event for IP-shaped fields not in current alias tables."""
        all_known = {a for aliases in self.aliases.values() for a in aliases}
        for key, val in event.items():
            if key.startswith('_') or key in all_known:
                continue
            if not _is_ip(str(val)):
                continue
            key_lower = key.lower()
            # Heuristic: assign to src or dst based on key name fragment
            if any(frag in key_lower for frag in ('src', 'source', 'orig', 'client', 'from')):
                target = 'src_ip'
            elif any(frag in key_lower for frag in ('dst', 'dest', 'resp', 'server', 'target', 'to')):
                target = 'dst_ip'
            else:
                continue  # ambiguous — skip
            if key not in self.aliases[target]:
                self.aliases[target].append(key)
                self.aliases_learned += 1
                logger.warning(
                    '[ase] learned new alias %s → %s (value=%s)',
                    key, target, val,
                )

    def _churn_rate(self) -> float:
        """Return distinct schema hashes per minute in the recent window."""
        now = time.time()
        cutoff = now - _CHURN_WINDOW_S
        recent = [h for ts, h in self._timeline if ts >= cutoff]
        if not recent:
            return 0.0
        distinct = len(set(recent))
        return round(distinct / (_CHURN_WINDOW_S / 60.0), 2)

    def _check_churn(self) -> None:
        """Alert and optionally quarantine if schema churn is too high."""
        rate = self._churn_rate()
        if rate > _CHURN_ALERT_RATE:
            logger.warning(
                '[ase] HIGH SCHEMA CHURN %.1f distinct schemas/min — '
                'possible adversarial or unstable stream', rate,
            )


# ── Module-level singleton ────────────────────────────────────────────────────
engine = AdaptiveSchemaEngine()
