"""
shadow_graph.py — Pre-reality edge buffer for SCYTHE inference pipeline.

Edges that fail validation (unknown node, low confidence, authority mismatch)
are placed here instead of being silently discarded.  The shadow graph acts as
the "LLM imagination space that hasn't earned reality yet."

Promotion path:
  ShadowEdge (confidence < threshold / unknown node)
    → periodic re-evaluation (every 30s by default)
    → if evidence arrives or confidence improves: promote to HypergraphEngine
    → if TTL expires without promotion: decay (logged, removed)

API:
  shadow = ShadowGraph.get_instance()
  shadow.push(edge_dict, rejection_reason, context_node_id)
  shadow.get_pending() → List[ShadowEdge]
  shadow.promote(edge_id, hypergraph_engine)
  shadow.summary() → dict  (exposed via /api/shadow/summary)
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from takml_runtime_metrics import get_takml_runtime_metrics_tracker

logger = logging.getLogger(__name__)
_runtime_metrics = get_takml_runtime_metrics_tracker()

# ─────────────────────────────────────────────────────────────────────────────
# Rejection reason codes
# ─────────────────────────────────────────────────────────────────────────────

REJECT_UNKNOWN_SRC        = "unknown_src"
REJECT_UNKNOWN_DST        = "unknown_dst"
REJECT_INVALID_KIND       = "invalid_kind"
REJECT_AUTHORITY_MISMATCH = "authority_mismatch"
REJECT_LOW_CONFIDENCE     = "low_confidence"
REJECT_CIRCUIT_OPEN       = "circuit_open"
REJECT_MISSING_SRCDST     = "missing_src_dst"

TERMINAL_REJECTION_REASONS = frozenset({
    REJECT_INVALID_KIND,
    REJECT_AUTHORITY_MISMATCH,
    REJECT_MISSING_SRCDST,
})


# ─────────────────────────────────────────────────────────────────────────────
# ShadowEdge dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ShadowEdge:
    edge_id: str = ""
    src: str = ""
    dst: str = ""
    kind: str = ""
    confidence: float = 0.0
    rejection_reason: str = ""
    context_node_id: str = ""
    rule_id: str = ""
    raw_kind: str = ""           # original kind before normalization attempt
    created_at: float = field(default_factory=time.monotonic)
    promote_attempts: int = 0
    ttl_secs: float = 300.0      # 5 min default; decay after this
    # ── Dual-layer promotion fields ──────────────────────────────────────────
    observations: int = 1        # times this edge has been seen
    evidence_score: float = 0.0  # weighted external evidence (DPI, RTT, repeat)
    requires: list = field(default_factory=list)  # unmet conditions for promotion
    speculative: bool = True     # always True until promoted

    def bump_confidence(self, delta: float = 0.05, evidence_delta: float = 0.0) -> None:
        """Record a new corroborating observation and optionally add evidence."""
        self.observations += 1
        self.confidence = min(1.0, self.confidence + delta)
        self.evidence_score = min(1.0, self.evidence_score + evidence_delta)

    def is_promotable(self) -> bool:
        """True when the edge has earned reality: confidence+evidence+observations."""
        return (
            self.confidence >= 0.75
            and self.evidence_score >= 0.6
            and self.observations >= 3
        )

    def is_expired(self) -> bool:
        return (time.monotonic() - self.created_at) > self.ttl_secs

    def to_dict(self) -> Dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "src": self.src,
            "dst": self.dst,
            "kind": self.kind,
            "raw_kind": self.raw_kind,
            "confidence": self.confidence,
            "evidence_score": self.evidence_score,
            "observations": self.observations,
            "requires": self.requires,
            "speculative": self.speculative,
            "rejection_reason": self.rejection_reason,
            "context_node_id": self.context_node_id,
            "rule_id": self.rule_id,
            "age_secs": round(time.monotonic() - self.created_at, 1),
            "promote_attempts": self.promote_attempts,
            "is_promotable": self.is_promotable(),
        }


# ─────────────────────────────────────────────────────────────────────────────
# ─── SSE subscriber ──────────────────────────────────────────────────────────

class _SseSubscriber:
    """
    Per-client SSE subscriber.

    drop_count tracks how many events were skipped because this client's queue
    was full (dropIfSlow).  The endpoint includes drop_count in heartbeats so
    the browser knows to do a re-bootstrap if it falls behind.
    """

    def __init__(self, maxsize: int = 500) -> None:
        import queue as _q
        self.q          = _q.Queue(maxsize=maxsize)
        self.drop_count = 0
        self.created_at = time.time()


# ShadowGraph
# ─────────────────────────────────────────────────────────────────────────────

class ShadowGraph:
    """Thread-safe shadow edge buffer with TTL decay and promotion logic."""

    _PRUNE_INTERVAL_SECS: float = 30.0
    _instance: Optional["ShadowGraph"] = None
    _instance_lock: threading.Lock = threading.Lock()

    def __init__(self, max_edges: int = 2000) -> None:
        self._edges: Dict[str, ShadowEdge] = {}
        self._lock = threading.Lock()
        self._seq_lock = threading.Lock()   # separate lock — avoids deadlock in _notify_delta
        self._max_edges = max_edges
        self._total_pushed: int = 0
        self._total_promoted: int = 0
        self._total_decayed: int = 0
        self._last_prune: float = time.monotonic()
        self._seq: int = 0                # global monotonic event sequence number
        self._sse_subs: list = []         # list of _SseSubscriber

    @classmethod
    def get_instance(cls) -> "ShadowGraph":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ─── Push ───────────────────────────────────────────────────────────────

    def push(
        self,
        edge: Dict[str, Any],
        rejection_reason: str,
        context_node_id: str = "",
        rule_id: str = "",
        ttl_secs: float = 300.0,
    ) -> Optional[str]:
        """
        Store a rejected edge in the shadow graph.
        Returns the assigned edge_id, or None if the graph is at capacity.
        """
        self._maybe_prune()

        with self._lock:
            if len(self._edges) >= self._max_edges:
                logger.debug("[shadow] At capacity (%d) — edge not stored", self._max_edges)
                return None

            src = edge.get("src", "")
            dst = edge.get("dst", "")
            kind = edge.get("kind", "")
            raw_kind = edge.get("_raw_kind", kind)
            confidence = float(edge.get("confidence", 0.3))
            requires = edge.get("requires", [])

            if rejection_reason in TERMINAL_REJECTION_REASONS:
                logger.info(
                    "[shadow] Skip terminal rejection edge src=%s dst=%s kind=%s raw_kind=%s reason=%s",
                    src,
                    dst,
                    kind,
                    raw_kind,
                    rejection_reason,
                )
                return None

            edge_id = hashlib.md5(
                f"{src}:{dst}:{kind}:{context_node_id}".encode(), usedforsecurity=False
            ).hexdigest()[:12]

            # If already known, just bump rather than creating a duplicate
            if edge_id in self._edges:
                self._edges[edge_id].bump_confidence(delta=0.02)
                self._notify_delta(self._edges[edge_id], "updated")
                return edge_id

            se = ShadowEdge(
                edge_id=edge_id,
                src=src,
                dst=dst,
                kind=kind,
                raw_kind=raw_kind,
                confidence=confidence,
                rejection_reason=rejection_reason,
                context_node_id=context_node_id,
                rule_id=rule_id,
                ttl_secs=ttl_secs,
                requires=requires,
            )
            self._edges[edge_id] = se
            self._total_pushed += 1

            logger.debug(
                "[shadow] +edge %s  src=%s dst=%s kind=%s reason=%s  "
                "(total=%d)",
                edge_id, src, dst, kind, rejection_reason, len(self._edges),
            )
            self._notify_delta(se, "created")
            return edge_id

    # ─── Observe (bump confidence from external corroboration) ──────────────

    def observe(self, edge_id: str, confidence_delta: float = 0.05,
                evidence_delta: float = 0.1) -> Optional[Dict]:
        """
        Record external corroboration for an edge (DPI hit, repeat flow, RTT
        anomaly).  Returns the updated edge dict, or None if not found.
        """
        with self._lock:
            se = self._edges.get(edge_id)
            if not se:
                return None
            se.bump_confidence(delta=confidence_delta, evidence_delta=evidence_delta)
            self._notify_delta(se, "updated")
            return se.to_dict()

    # ─── Promote ────────────────────────────────────────────────────────────

    def try_promote(self, edge_id: str, known_node_ids: set) -> Optional[Dict]:
        """
        Promote a shadow edge when its nodes exist AND it has earned reality:
          confidence >= 0.75  AND  evidence_score >= 0.6  AND  observations >= 3
        """
        with self._lock:
            se = self._edges.get(edge_id)
            if not se:
                return None
            if se.rejection_reason in TERMINAL_REJECTION_REASONS:
                _runtime_metrics.record_shadow_promotion_attempt()
                _runtime_metrics.record_shadow_promotion_block("terminal_rejection")
                logger.warning(
                    "[shadow] Blocking promotion of terminal rejection edge %s src=%s dst=%s kind=%s raw_kind=%s reason=%s",
                    edge_id,
                    se.src,
                    se.dst,
                    se.kind,
                    se.raw_kind,
                    se.rejection_reason,
                )
                del self._edges[edge_id]
                return None
            _runtime_metrics.record_shadow_promotion_attempt()
            se.promote_attempts += 1
            nodes_known = se.src in known_node_ids and se.dst in known_node_ids
            promotable = nodes_known and se.is_promotable()
            if promotable:
                _runtime_metrics.record_shadow_promotion_success()
                del self._edges[edge_id]
                self._total_promoted += 1
                result = se.to_dict()
                result["validated"] = True
                result["speculative"] = False
                logger.info(
                    "[shadow] PROMOTED %s  src=%s dst=%s kind=%s  conf=%.2f "
                    "evidence=%.2f obs=%d  (age=%.1fs)",
                    edge_id, se.src, se.dst, se.kind,
                    se.confidence, se.evidence_score, se.observations,
                    time.monotonic() - se.created_at,
                )
                self._notify_delta(se, "promoted")
                return result
            block_reason = "unknown_nodes" if not nodes_known else "insufficient_evidence"
            _runtime_metrics.record_shadow_promotion_block(block_reason)
            return None

    def re_evaluate(self, known_node_ids: set) -> List[Dict]:
        """
        Bulk re-evaluation pass — promote any edges that have earned reality.
        Processes HOT edges first so high-value promotions aren't delayed by
        a long tail of cold edges.
        """
        try:
            from attention_engine import get_attention_engine
            ae = get_attention_engine()
            with self._lock:
                all_edges = list(self._edges.values())
            hot, warm, cold = ae.allocate(all_edges)
            candidates = [e.edge_id for e in hot + warm + cold]
        except Exception:
            with self._lock:
                candidates = list(self._edges.keys())

        promotable = []
        for eid in candidates:
            result = self.try_promote(eid, known_node_ids)
            if result:
                promotable.append(result)
        return promotable

    # ─── Query ──────────────────────────────────────────────────────────────

    def get_pending(self, limit: int = 200) -> List[Dict]:
        """Return edges sorted by attention score (hot first)."""
        with self._lock:
            edges = list(self._edges.values())
        try:
            from attention_engine import get_attention_engine
            ae = get_attention_engine()
            edges.sort(key=lambda e: ae.score(e).score, reverse=True)
        except Exception:
            edges.sort(key=lambda e: e.confidence, reverse=True)
        return [e.to_dict() for e in edges[:limit]]

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            by_reason: Dict[str, int] = {}
            promotable_count = 0
            hot_count = warm_count = cold_count = 0
            for se in self._edges.values():
                by_reason[se.rejection_reason] = by_reason.get(se.rejection_reason, 0) + 1
                if se.is_promotable():
                    promotable_count += 1
            # Attention tier breakdown (lazy — no import error if missing)
            try:
                from attention_engine import get_attention_engine
                ae = get_attention_engine()
                for se in self._edges.values():
                    t = ae.score(se).tier
                    if t == "hot":   hot_count  += 1
                    elif t == "warm": warm_count += 1
                    else:             cold_count += 1
            except Exception:
                pass
            return {
                "pending":            len(self._edges),
                "promotable":         promotable_count,
                "attention_hot":      hot_count,
                "attention_warm":     warm_count,
                "attention_cold":     cold_count,
                "total_pushed":       self._total_pushed,
                "total_promoted":     self._total_promoted,
                "total_decayed":      self._total_decayed,
                "by_rejection_reason": by_reason,
            }

    # ─── SSE delta queue (for /stream/speculative) ───────────────────────────

    def _notify_delta(self, se: "ShadowEdge", event_type: str) -> None:
        """
        Stamp delta with monotonic seq, push to all SSE subscribers.

        dropIfSlow: subscribers whose queue is full are skipped and their
        drop_count is incremented so the endpoint can report the gap.
        Uses _seq_lock (separate from _lock) to avoid deadlock — push() holds
        _lock when it calls _notify_delta.
        """
        import queue as _q
        with self._seq_lock:
            self._seq += 1
            seq = self._seq
        delta = {**se.to_dict(), "_event": event_type, "_ts": time.time(), "seq": seq}
        # Stamp attention tier so clients can prioritise rendering
        try:
            from attention_engine import get_attention_engine
            result = get_attention_engine().score(se)
            delta["_attention"] = result.score
            delta["_tier"]      = result.tier
        except Exception:
            pass
        for sub in list(self._sse_subs):
            try:
                sub.q.put_nowait(delta)
            except _q.Full:
                sub.drop_count += 1  # slow consumer — drop oldest implicitly

    @property
    def current_seq(self) -> int:
        return self._seq

    def subscribe_sse(self, maxsize: int = 500) -> "_SseSubscriber":
        """Register a new SSE subscriber. Returns an _SseSubscriber to drain."""
        sub = _SseSubscriber(maxsize=maxsize)
        with self._seq_lock:
            self._sse_subs.append(sub)
        return sub

    def unsubscribe_sse(self, sub: "_SseSubscriber") -> None:
        with self._seq_lock:
            try:
                self._sse_subs.remove(sub)
            except ValueError:
                pass

    # ─── Prune (TTL decay) ──────────────────────────────────────────────────

    def _maybe_prune(self) -> None:
        now = time.monotonic()
        if now - self._last_prune < self._PRUNE_INTERVAL_SECS:
            return
        self._last_prune = now
        with self._lock:
            expired = []
            for eid, se in self._edges.items():
                if se.is_expired():
                    # Attention-aware grace: HOT edges survive 1 extra TTL period
                    try:
                        from attention_engine import get_attention_engine
                        if get_attention_engine().score(se).tier == "hot":
                            se.ttl_secs *= 1.5  # extend TTL once, then expire normally
                            continue
                    except Exception:
                        pass
                    expired.append(eid)
            for eid in expired:
                del self._edges[eid]
            self._total_decayed += len(expired)
        if expired:
            logger.debug("[shadow] Decayed %d expired edges", len(expired))
