"""
attention_engine.py — Attention scoring for speculative graph edges.

Inspired by the KV-cache paging insight from "Efficient Memory Management for
Large Language Models" (arXiv 2309.06180): not all tokens deserve equal
compute.  Applied to SCYTHE: not all edges deserve equal memory or bandwidth.

Attention score formula
-----------------------
    attention = (confidence       × W_CONF)
              + (evidence         × W_EVID)
              + (recency          × W_REC)
              + (anomaly          × W_ANOM)
              + (protocol_anomaly × W_PROTO)

Where recency decays exponentially with half-life RECENCY_HALF_LIFE_SECS,
anomaly is approximated from observation count + requires-list length, and
protocol_anomaly comes from ProtocolIntel.score_session() injected into the
session node labels during pcap_ingest.emit_session().

Memory tier thresholds
----------------------
    HOT   score >= TIER_HOT   → real-time SSE, fast prune cycle, kept in FAISS hot
    WARM  score >= TIER_WARM  → batched SSE, normal prune
    COLD  score <  TIER_WARM  → slow batch, candidate for eviction

Usage
-----
    from attention_engine import AttentionEngine
    ae = AttentionEngine()
    score = ae.score(shadow_edge_dict_or_obj)
    tier  = ae.tier(score)           # "hot" | "warm" | "cold"
    budget = ae.allocate(edge_list)  # returns (hot, warm, cold) partition
"""

from __future__ import annotations

import math
import time
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

# ── Scoring weights (must sum to 1.0) ────────────────────────────────────────
W_CONF  = 0.37   # was 0.40 — reduced to accommodate W_PROTO
W_EVID  = 0.28   # was 0.30
W_REC   = 0.18   # was 0.20
W_ANOM  = 0.07   # was 0.10 (graph-proxy anomaly)
W_PROTO = 0.10   # NEW: protocol-expectation violation score from ProtocolIntel

# Sanity: W_CONF + W_EVID + W_REC + W_ANOM + W_PROTO == 1.0
assert abs(W_CONF + W_EVID + W_REC + W_ANOM + W_PROTO - 1.0) < 1e-9

# ── Recency decay ─────────────────────────────────────────────────────────────
RECENCY_HALF_LIFE_SECS = 1800.0   # 30 min: a 30-min-old edge is at 0.5 recency

# ── Tier thresholds ───────────────────────────────────────────────────────────
TIER_HOT  = 0.65   # high-value, real-time processing
TIER_WARM = 0.35   # worth watching, batched updates
# below TIER_WARM → COLD

# ── Anomaly proxy scaling ─────────────────────────────────────────────────────
# More observations = more interesting. Saturates at ANOMALY_SAT_OBS.
ANOMALY_SAT_OBS = 10


class AttentionResult(NamedTuple):
    score:            float
    tier:             str    # "hot" | "warm" | "cold"
    conf:             float
    evid:             float
    recency:          float
    anomaly:          float
    protocol_anomaly: float  # ProtocolIntel contribution (0 if unavailable)


class AttentionEngine:
    """
    Stateless scorer.  All methods are pure functions of the edge data;
    no internal state is mutated.

    Accepts both ShadowEdge objects and plain dicts (to_dict() output).

    Protocol anomaly is read from the edge dict's 'protocol_anomaly_score'
    field if present (injected by pcap_ingest.emit_session), or from a
    nested 'context' → 'protocol_anomaly_score' path used by the live
    ingest worker.  Falls back to 0.0 gracefully.
    """

    # ── Public API ────────────────────────────────────────────────────────────

    def score(self, edge: Any) -> AttentionResult:
        """
        Compute attention score for a single edge.

        edge may be a ShadowEdge dataclass instance or a dict (to_dict()).
        """
        d = edge if isinstance(edge, dict) else edge.to_dict()

        conf     = float(d.get("confidence",    0.0))
        evid     = float(d.get("evidence_score", 0.0))
        age_secs = float(d.get("age_secs",       0.0))
        obs      = int(d.get("observations",     0))
        requires = d.get("requires", [])

        # Protocol anomaly — try multiple locations in the edge dict
        proto_anom = self._extract_protocol_anomaly(d)

        recency = self._recency(age_secs)
        anomaly = self._anomaly(obs, requires)

        total = (W_CONF  * conf
               + W_EVID  * evid
               + W_REC   * recency
               + W_ANOM  * anomaly
               + W_PROTO * proto_anom)
        total = round(min(1.0, max(0.0, total)), 4)

        return AttentionResult(
            score            = total,
            tier             = self.tier(total),
            conf             = conf,
            evid             = evid,
            recency          = round(recency, 4),
            anomaly          = round(anomaly, 4),
            protocol_anomaly = round(proto_anom, 4),
        )

    @staticmethod
    def tier(score: float) -> str:
        """Classify a numeric attention score into a memory tier."""
        if score >= TIER_HOT:
            return "hot"
        if score >= TIER_WARM:
            return "warm"
        return "cold"

    def allocate(
        self,
        edges: List[Any],
    ) -> Tuple[List[Any], List[Any], List[Any]]:
        """
        Partition edges into (hot, warm, cold) lists, sorted by score desc
        within each tier.

        Useful for:
          - Deciding which edges get real-time SSE vs batched
          - Prioritising re_evaluate() order in ShadowGraph
          - FAISS tiering decisions

        Returns:
            (hot_edges, warm_edges, cold_edges) — each sorted by score desc
        """
        scored = [(e, self.score(e)) for e in edges]
        scored.sort(key=lambda x: x[1].score, reverse=True)

        hot, warm, cold = [], [], []
        for edge, result in scored:
            if result.tier == "hot":
                hot.append(edge)
            elif result.tier == "warm":
                warm.append(edge)
            else:
                cold.append(edge)
        return hot, warm, cold

    def top_k(self, edges: List[Any], k: int = 20) -> List[Tuple[Any, AttentionResult]]:
        """
        Return the top-k edges by attention score with their results.
        Useful for dashboard / GraphOps inspection.
        """
        scored = [(e, self.score(e)) for e in edges]
        scored.sort(key=lambda x: x[1].score, reverse=True)
        return scored[:k]

    # ── Component scorers ─────────────────────────────────────────────────────

    @staticmethod
    def _recency(age_secs: float) -> float:
        """
        Exponential decay: fresh edges score 1.0, halves every RECENCY_HALF_LIFE_SECS.
        Floor at 0.0 (never negative).
        """
        return math.exp(-0.693 * max(0.0, age_secs) / RECENCY_HALF_LIFE_SECS)

    @staticmethod
    def _anomaly(observations: int, requires: list) -> float:
        """
        Anomaly proxy:
          - Saturating observation count  (more = more interesting)
          - Pending requirements count    (unmet requires = more uncertain = more interesting)

        Normalised to [0, 1].
        """
        obs_score  = min(1.0, observations / ANOMALY_SAT_OBS)
        req_score  = min(1.0, len(requires) / 4.0)   # 4+ requires → max
        # Weight obs heavier — confirmed activity > unmet hypotheses
        return round(0.7 * obs_score + 0.3 * req_score, 4)

    @staticmethod
    def _extract_protocol_anomaly(d: dict) -> float:
        """
        Locate the protocol_anomaly_score in an edge dict.

        Checked paths (in order):
          1. d['protocol_anomaly_score']              (injected by emit_session)
          2. d['context']['protocol_anomaly_score']   (live ingest worker)
          3. d['labels']['protocol_anomaly_score']    (session node labels)
          4. 0.0 if absent
        """
        v = d.get("protocol_anomaly_score")
        if v is not None:
            return float(v)
        ctx = d.get("context") or {}
        v = ctx.get("protocol_anomaly_score")
        if v is not None:
            return float(v)
        labels = d.get("labels") or {}
        v = labels.get("protocol_anomaly_score")
        if v is not None:
            return float(v)
        return 0.0


# ── Module-level singleton ────────────────────────────────────────────────────
_engine: Optional[AttentionEngine] = None


def get_attention_engine() -> AttentionEngine:
    """Return the shared AttentionEngine instance (lazy init)."""
    global _engine
    if _engine is None:
        _engine = AttentionEngine()
    return _engine
