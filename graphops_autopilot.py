"""graphops_autopilot.py — Autonomous GraphOps patrol and alert system.

Architecture (from Gemma_Llama_MCP.md §1–7):

    TopologyDriftDetector ──subscribe──┐
    TemporalFanInDetector ──subscribe──┤
                                       ▼
                                 SentinelLoop
                                       │
                                  AlertDedup  (30s TTL)
                                       │
                                  TierRouter
                                  ├─ score ≥ 0.60 → observation log
                                  ├─ score ≥ 0.70 → SuggestionQueue  (Tier 1)
                                  ├─ score ≥ 0.80 → EventCard alert  (Tier 2)
                                  └─ score ≥ 0.90 → InvestigatorAgent → report (Tier 3)

GraphOpsAutopilot wires everything together.  Handlers registered via
register_handler(cb) receive EventCard objects for Tier 2/3 events.

MCP tools registered via register_autopilot_tools(engine, handler).
"""

from __future__ import annotations

import hashlib
import logging
import queue
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Tier thresholds (Gemma_Llama_MCP.md §4) ──────────────────────────────────

TIER_OBSERVATION = 0.60   # log only
TIER_SUGGESTION  = 0.70   # suggestion queue (Tier 1)
TIER_ALERT       = 0.80   # auto-emit EventCard (Tier 2)
TIER_AUTONOMOUS  = 0.90   # wake InvestigatorAgent (Tier 3)

DEDUP_TTL_S       = 30.0   # suppress duplicate alert keys for this many seconds
DETECTOR_COOLDOWN = 1.0    # min seconds between alerts of same (source_type, pattern)
PATROL_SLEEP_S    = 0.2    # heartbeat thread sleep (200 ms)
SUGGESTION_MAXLEN = 50     # max items kept in suggestion queue

# TAK-ML integration constants
TAKML_SOURCE_TYPE = "takml"    # source_type label for TAK-ML produced cards


def _new_card_id() -> str:
    """Generate a short unique EventCard ID (hex-8)."""
    return uuid.uuid4().hex[:8]


# ── EventCard ─────────────────────────────────────────────────────────────────

@dataclass
class EventCard:
    """Structured analyst card produced by Tier 2/3 triggers.

    Rendered by format_card() into a human-readable text block.
    """
    pattern:           str
    nodes:             int
    window_ms:         int
    confidence:        float
    tier:              int           # 1=suggestion, 2=alert, 3=autonomous
    source_type:       str           # "drift" | "fanin" | "attractor" | "takml"
    node_ids:          List[str]     = field(default_factory=list)
    temporal_sync:     float         = 0.0
    ip_entropy:        float         = 0.0
    ts:                float         = field(default_factory=time.time)
    suggested_actions: List[str]     = field(default_factory=list)
    investigation:     Optional[Dict[str, Any]] = None  # filled by Tier 3
    # TAK-ML fields
    card_id:           str           = field(default_factory=lambda: _new_card_id())
    takml_model:       Optional[str] = None   # model that produced this card
    takml_features:    Optional[Dict[str, float]] = None  # feature tensor
    analyst_verdict:   Optional[str] = None   # set after analyst review

    def format_card(self) -> str:
        tier_label = {
            1: "Suggestion",
            2: "Alert",
            3: "Autonomous Investigation",
        }.get(self.tier, "Finding")

        lines = [
            f"GraphOps {tier_label}",
            "─" * 44,
            f"Pattern           {self.pattern}",
            f"Nodes             {self.nodes}",
            f"Window            {self.window_ms}ms",
            f"Confidence        {self.confidence:.2f}",
        ]
        if self.temporal_sync:
            lines.append(f"Temporal Sync     {self.temporal_sync:.2f}")
        if self.ip_entropy:
            lines.append(f"IP Entropy        {self.ip_entropy:.2f}")

        if self.suggested_actions:
            lines.append("")
            lines.append("Suggested Actions")
            for action in self.suggested_actions:
                lines.append(f"  ▶ {action}")

        if self.investigation:
            lines.append("")
            lines.append("Investigation Summary")
            sit = self.investigation.get("situation", "")
            if sit:
                lines.append(f"  {sit}")
            ast = self.investigation.get("assessment", "")
            if ast:
                lines.append(f"  Assessment: {ast}")

        return "\n".join(lines)


# ── AlertDedup ────────────────────────────────────────────────────────────────

class AlertDedup:
    """Hash-based alert deduplication with TTL (Gemma_Llama_MCP.md §6).

    Suppresses identical (source_type, pattern, node_ids, window_bucket)
    tuples for DEDUP_TTL_S seconds to prevent LLM alarm storms.
    """

    def __init__(self, ttl_s: float = DEDUP_TTL_S):
        self._ttl  = ttl_s
        self._seen: Dict[str, float] = {}  # key → expiry timestamp
        self._lock = threading.Lock()

    def _make_key(self, source_type: str, pattern: str,
                  node_ids: List[str], window_ms: int) -> str:
        h = hashlib.blake2b(digest_size=8)
        h.update(source_type.encode())
        h.update(pattern.encode())
        for nid in sorted(node_ids):
            h.update(nid.encode())
        # Bucket window to nearest second to absorb minor jitter
        h.update(str(window_ms // 1000).encode())
        return h.hexdigest()

    def is_duplicate(self, source_type: str, pattern: str,
                     node_ids: List[str], window_ms: int) -> bool:
        key = self._make_key(source_type, pattern, node_ids, window_ms)
        now = time.time()
        with self._lock:
            self._evict(now)
            if key in self._seen:
                return True
            self._seen[key] = now + self._ttl
            return False

    def _evict(self, now: float) -> None:
        expired = [k for k, exp in self._seen.items() if exp <= now]
        for k in expired:
            del self._seen[k]

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._seen)


# ── TierRouter ────────────────────────────────────────────────────────────────

class TierRouter:
    """Routes findings to the correct output tier by confidence score."""

    @staticmethod
    def tier(confidence: float) -> int:
        """Return tier integer: -1=discard, 0=observation, 1=suggestion, 2=alert, 3=autonomous."""
        if confidence >= TIER_AUTONOMOUS:
            return 3
        if confidence >= TIER_ALERT:
            return 2
        if confidence >= TIER_SUGGESTION:
            return 1
        if confidence >= TIER_OBSERVATION:
            return 0
        return -1

    @staticmethod
    def label(tier: int) -> str:
        return {
            -1: "discard",
            0:  "observation",
            1:  "suggestion",
            2:  "alert",
            3:  "autonomous",
        }.get(tier, "unknown")


# ── Scoring helpers ───────────────────────────────────────────────────────────

def _score_drift(alert) -> float:
    """Score a DriftAlert.  .score is already normalised 0–1 by the detector."""
    return float(getattr(alert, "score", 0.0))


def _score_fanin(alert) -> float:
    """Score a FanInAlert.

    botnet_coordination → base 0.92  (near-zero timing entropy → Tier 3)
    fan_in_spike        → base 0.72  (high timing entropy may fall below Tier 1)

    Penalised by timing_entropy: high entropy = unsynchronised = less confidence.
    """
    verdict = getattr(alert, "verdict", "fan_in_spike")
    base    = 0.92 if verdict == "botnet_coordination" else 0.72
    t_ent   = float(getattr(alert, "timing_entropy", 1.0))
    penalty = min(0.15, t_ent * 0.10)
    return round(max(0.0, min(1.0, base - penalty)), 4)


def _score_attractor(alert) -> float:
    """Score an AttractorAlert.  .score is precomputed by GraphAttractorDetector."""
    return float(getattr(alert, "score", 0.0))


# ── Suggested-action templates ────────────────────────────────────────────────

_DRIFT_ACTIONS: Dict[str, List[str]] = {
    "scanner":    ["Expand neighbors",         "Trace scan paths",           "Check ASN diversity"],
    "aggregator": ["Inspect fan-in sources",   "Cluster timing",             "Compare with previous windows"],
    "lateral":    ["Trace lateral paths",      "Analyze path depth",         "Check credential reuse patterns"],
}

_FANIN_ACTIONS: Dict[str, List[str]] = {
    "botnet_coordination": ["Cluster timing",           "Analyze ASN diversity",
                            "Trace infrastructure paths", "Compare with previous clusters"],
    "fan_in_spike":        ["Expand neighbors",         "Check for legitimate CDN pattern",
                            "Analyze IP entropy"],
}

_ATTRACTOR_ACTIONS: Dict[str, List[str]] = {
    "rotating_botnet": ["Trace edge subgraph",        "Analyze infrastructure entropy",
                        "Compare attractor with previous windows", "Expand cluster nodes"],
}


# ── InvestigatorAgent ─────────────────────────────────────────────────────────

class InvestigatorAgent:
    """Tier 3: wraps GraphOpsAgent.  Triggered only when confidence >= TIER_AUTONOMOUS.

    Builds a natural-language question from the EventCard context and delegates
    to the full agent investigation loop.
    """

    def __init__(self, engine=None):
        self._engine = engine
        self._agent  = None  # lazy init — avoid import cost at startup

    def _get_agent(self):
        if self._agent is None:
            from graphops_copilot import GraphOpsAgent
            self._agent = GraphOpsAgent(self._engine)
        return self._agent

    def investigate(self, card: EventCard) -> Dict[str, Any]:
        if card.source_type == "fanin":
            question = (
                f"I detected {card.nodes} sources fanning into a destination node "
                f"with temporal sync {card.temporal_sync:.2f} and "
                f"IP entropy {card.ip_entropy:.2f} over a {card.window_ms}ms window. "
                f"Pattern: {card.pattern}. Is this botnet coordination?"
            )
        else:
            node_label = card.node_ids[0] if card.node_ids else "unknown"
            question = (
                f"Node {node_label} shows a degree delta of {card.nodes} connections "
                f"in the last observation window. "
                f"Pattern: {card.pattern}. Confidence {card.confidence:.2f}. "
                "Is this a port scan or lateral movement?"
            )
        try:
            report = self._get_agent().investigate(question)
            return report
        except Exception as exc:
            logger.warning("[Investigator] agent.investigate failed: %s", exc)
            return {"error": str(exc)}


# ── SentinelLoop ──────────────────────────────────────────────────────────────

class SentinelLoop:
    """Subscribes to both detectors via their push callbacks, scores each alert,
    deduplicates, and routes to the correct output tier.

    Tier 0 → observation_log (internal, no external dispatch)
    Tier 1 → suggestion_queue (analyst-pull)
    Tier 2 → registered handlers receive EventCard (auto-alert)
    Tier 3 → InvestigatorAgent runs, then handlers receive enriched EventCard
    """

    def __init__(self,
                 topo_detector=None,
                 fanin_detector=None,
                 attractor_detector=None,
                 investigator: Optional[InvestigatorAgent] = None):
        self._topo          = topo_detector
        self._fanin         = fanin_detector
        self._attractor     = attractor_detector
        self._investigator  = investigator
        self._dedup         = AlertDedup()
        self._handlers:     List[Callable[[EventCard], None]] = []
        self._suggestion_queue: deque = deque(maxlen=SUGGESTION_MAXLEN)
        self._observation_log:  deque = deque(maxlen=200)
        self._alert_count   = 0
        self._last_emit:    Dict[tuple, float] = {}   # cooldown timestamps
        self._lock          = threading.Lock()

        # Tier-3 investigations run in a dedicated thread so that detector
        # callbacks (which call _route) never block waiting for LLM calls.
        self._t3_queue: queue.Queue = queue.Queue(maxsize=8)
        self._t3_thread = threading.Thread(
            target=self._t3_worker, daemon=True, name="sentinel-t3"
        )
        self._t3_thread.start()

        if self._topo:
            self._topo.subscribe(self._on_drift)
        if self._fanin:
            self._fanin.subscribe(self._on_fanin)
        if self._attractor:
            self._attractor.subscribe(self._on_attractor)

    def register_handler(self, cb: Callable[[EventCard], None]) -> None:
        self._handlers.append(cb)

    def _t3_worker(self) -> None:
        """Background thread: dequeue Tier-3 cards, run investigation, dispatch."""
        while True:
            try:
                card: EventCard = self._t3_queue.get(timeout=5)
            except queue.Empty:
                continue
            try:
                if self._investigator:
                    report = self._investigator.investigate(card)
                    card.investigation = report
                    card.tier = 3
            except Exception as exc:
                logger.warning("[Sentinel] investigator failed: %s", exc)
            try:
                for cb in self._handlers:
                    try:
                        cb(card)
                    except Exception as exc:
                        logger.warning("[Sentinel] handler error: %s", exc)
            except Exception as exc:
                logger.warning("[Sentinel] t3_worker dispatch error: %s", exc)
            finally:
                self._t3_queue.task_done()

    def get_suggestion_queue(self) -> List[dict]:
        with self._lock:
            return [
                {
                    "score":     c.confidence,
                    "pattern":   c.pattern,
                    "nodes":     c.nodes,
                    "window_ms": c.window_ms,
                    "ts":        c.ts,
                }
                for c in self._suggestion_queue
            ]

    def get_observation_log(self) -> List[dict]:
        with self._lock:
            return list(self._observation_log)

    @property
    def alert_count(self) -> int:
        return self._alert_count

    # ── detector callbacks ────────────────────────────────────────────────────

    def _on_drift(self, alert) -> None:
        self._route(
            source_type  = "drift",
            pattern      = getattr(alert, "alert_type", "drift"),
            nodes        = getattr(alert, "degree_delta", 0),
            window_ms    = 1000,  # drift detector fires on per-second snapshot
            confidence   = _score_drift(alert),
            node_ids     = [getattr(alert, "node_id", "")],
            temporal_sync= 0.0,
            ip_entropy   = 0.0,
        )

    def _on_fanin(self, alert) -> None:
        t_ent = float(getattr(alert, "timing_entropy", 1.0))
        self._route(
            source_type  = "fanin",
            pattern      = getattr(alert, "verdict", "fan_in_spike"),
            nodes        = getattr(alert, "unique_src_count", 0),
            window_ms    = getattr(alert, "window_ms", 200),
            confidence   = _score_fanin(alert),
            node_ids     = [getattr(alert, "dst_node", "")],
            temporal_sync= round(max(0.0, 1.0 - t_ent), 4),
            ip_entropy   = float(getattr(alert, "ip_entropy", 0.0)),
        )

    def _on_attractor(self, alert) -> None:
        self._route(
            source_type  = "attractor",
            pattern      = getattr(alert, "verdict", "rotating_botnet"),
            nodes        = getattr(alert, "cluster_size", 0),
            window_ms    = int(getattr(alert, "ts", 0) * 0) or 5000,
            confidence   = _score_attractor(alert),
            node_ids     = [f"edge_hash:0x{getattr(alert, 'edge_hash', 0):x}"],
            temporal_sync= float(getattr(alert, "temporal_sync", 0.0)),
            ip_entropy   = float(getattr(alert, "infrastructure_entropy", 0.0)),
        )

    # ── internal routing ──────────────────────────────────────────────────────

    def _route(self, *, source_type: str, pattern: str, nodes: int,
               window_ms: int, confidence: float, node_ids: List[str],
               temporal_sync: float, ip_entropy: float) -> None:

        tier = TierRouter.tier(confidence)
        if tier < 0:
            return

        # Cooldown: prevent alert bursts from noisy detectors
        cooldown_key = (source_type, pattern)
        now = time.time()
        if now - self._last_emit.get(cooldown_key, 0.0) < DETECTOR_COOLDOWN:
            logger.debug("[Sentinel] cooldown suppressed %s/%s", source_type, pattern)
            return
        self._last_emit[cooldown_key] = now

        if self._dedup.is_duplicate(source_type, pattern, node_ids, window_ms):
            logger.debug("[Sentinel] dedup suppressed %s/%s conf=%.2f",
                         source_type, pattern, confidence)
            return

        if source_type == "attractor":
            actions = _ATTRACTOR_ACTIONS.get(pattern, ["Investigate further"])
        elif source_type == "fanin":
            actions = _FANIN_ACTIONS.get(pattern, ["Investigate further"])
        else:
            actions = _DRIFT_ACTIONS.get(pattern, ["Investigate further"])

        card = EventCard(
            pattern           = pattern,
            nodes             = nodes,
            window_ms         = window_ms,
            confidence        = confidence,
            tier              = max(tier, 1),
            source_type       = source_type,
            node_ids          = node_ids,
            temporal_sync     = temporal_sync,
            ip_entropy        = ip_entropy,
            suggested_actions = actions,
        )

        if tier == 0:
            with self._lock:
                self._observation_log.append({
                    "ts":         card.ts,
                    "pattern":    pattern,
                    "confidence": confidence,
                    "source":     source_type,
                })
            logger.debug("[Sentinel] observation %s score=%.2f", pattern, confidence)
            return

        if tier == 1:
            with self._lock:
                self._suggestion_queue.appendleft(card)
            logger.info("[Sentinel] suggestion queued: %s score=%.2f nodes=%d",
                        pattern, confidence, nodes)

        elif tier >= 2:
            self._alert_count += 1
            logger.warning("[Sentinel] ALERT tier=%d %s conf=%.2f nodes=%d",
                           tier, pattern, confidence, nodes)

            if tier >= 3 and self._investigator:
                try:
                    self._t3_queue.put_nowait(card)
                    logger.info("[Sentinel] Tier-3 enqueued for async investigation: %s",
                                pattern)
                    # Handlers are called from _t3_worker after investigation completes.
                    return
                except queue.Full:
                    logger.warning("[Sentinel] Tier-3 queue full — dropping investigation for %s",
                                   pattern)

            for cb in self._handlers:
                try:
                    cb(card)
                except Exception as exc:
                    logger.warning("[Sentinel] handler error: %s", exc)


# ── GraphOpsAutopilot ─────────────────────────────────────────────────────────

class GraphOpsAutopilot:
    """Top-level autopilot: wires Sentinel, Investigator, and output handlers.

    Usage::

        pilot = GraphOpsAutopilot(engine, topo_detector, fanin_detector)
        pilot.register_handler(lambda card: print(card.format_card()))
        pilot.start()
        ...
        pilot.stop()

    The sentinel is push-driven (detector callbacks), so no hot-polling occurs.
    The background heartbeat thread simply keeps the process alive and logs.
    """

    # Module-level singleton — set by start(), read by stream_manager callback
    _instance: Optional["GraphOpsAutopilot"] = None

    def __init__(self, engine=None, topo_detector=None, fanin_detector=None,
                 attractor_detector=None, takml_client=None):
        self._engine  = engine
        investigator  = InvestigatorAgent(engine)
        self.sentinel = SentinelLoop(
            topo_detector      = topo_detector,
            fanin_detector     = fanin_detector,
            attractor_detector = attractor_detector,
            investigator       = investigator,
        )
        self._running = False
        self._thread: Optional[threading.Thread] = None
        # TAK-ML client — optional, used for feedback submission
        self._takml_client = takml_client
        # Card registry: card_id → EventCard (last 200 cards)
        self._card_registry: Dict[str, "EventCard"] = {}
        self._card_order: deque = deque(maxlen=200)

    @classmethod
    def get_instance(cls) -> Optional["GraphOpsAutopilot"]:
        """Return the currently running singleton, or None."""
        return cls._instance

    def register_handler(self, cb: Callable[["EventCard"], None]) -> None:
        self.sentinel.register_handler(cb)

    def get_suggestion_queue(self) -> List[dict]:
        return self.sentinel.get_suggestion_queue()

    def get_observation_log(self) -> List[dict]:
        return self.sentinel.get_observation_log()

    def status(self) -> dict:
        return {
            "running":       self._running,
            "alert_count":   self.sentinel.alert_count,
            "suggestions":   len(self.sentinel.get_suggestion_queue()),
            "observations":  len(self.sentinel.get_observation_log()),
            "dedup_keys":    self.sentinel._dedup.size,
            "takml_enabled": self._takml_client is not None,
        }

    def _register_card(self, card: "EventCard") -> None:
        """Store card in registry for later feedback lookup."""
        self._card_registry[card.card_id] = card
        self._card_order.append(card.card_id)

    def handle_takml_score(self, score: float, features: Dict[str, float],
                           model: str = "nerf_botnet_v1") -> None:
        """Receive a TAK-ML inference score and route it through the tier system.

        Called by RemoteStreamManager._on_takml_result() on the inference queue
        worker thread.  Thread-safe — SentinelLoop._route() is already guarded.
        """
        if score < TIER_OBSERVATION:
            return

        tier = TierRouter.tier(score)
        pattern = "takml_anomaly" if score < TIER_AUTONOMOUS else "takml_high_confidence"
        node_ids: List[str] = []

        card = EventCard(
            pattern        = pattern,
            nodes          = int(features.get("fan_in_count", 0)),
            window_ms      = 1000,
            confidence     = score,
            tier           = tier,
            source_type    = TAKML_SOURCE_TYPE,
            temporal_sync  = features.get("temporal_sync", 0.0),
            ip_entropy     = features.get("source_entropy", 0.0),
            takml_model    = model,
            takml_features = dict(features),
            suggested_actions = [
                "Review TAK-ML feature tensor",
                "Correlate with topology drift",
                "Check temporal sync pattern",
                "Submit analyst feedback",
            ],
        )
        self._register_card(card)

        if tier >= 2:
            for cb in self.sentinel._handlers:
                try:
                    cb(card)
                except Exception as exc:
                    logger.warning("[autopilot] takml handler error: %s", exc)
            logger.info(
                "[GraphOpsAutopilot] TAK-ML score=%.3f tier=%d model=%s",
                score, tier, model,
            )
        elif tier == 1:
            self.sentinel._suggestion_queue.append(card.__dict__)

    def submit_feedback(self, card_id: str, verdict: str,
                        notes: str = "") -> dict:
        """Submit analyst feedback for a specific EventCard to TAK-ML.

        Args:
            card_id: EventCard.card_id hex string
            verdict: "true_positive" | "false_positive" | "wrong_label"
            notes:   optional analyst note

        Returns dict with status and message.
        """
        card = self._card_registry.get(card_id)
        if card is None:
            return {"status": "error", "message": f"card_id '{card_id}' not found"}

        if self._takml_client is None:
            return {"status": "error", "message": "no TAK-ML client configured"}

        # Map verdict to actual_output
        verdict_map = {
            "true_positive":  card.pattern,
            "false_positive": "benign",
            "wrong_label":    "unknown",
        }
        actual_output = verdict_map.get(verdict, verdict)
        card.analyst_verdict = verdict

        ok = self._takml_client.submit_feedback(
            predicted_output = card.pattern,
            actual_output    = actual_output,
            model            = card.takml_model or "nerf_botnet_v1",
            features         = card.takml_features,
            notes            = notes,
        )
        return {
            "status":  "ok" if ok else "error",
            "card_id": card_id,
            "verdict": verdict,
            "message": "feedback submitted" if ok else "submission failed",
        }

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        GraphOpsAutopilot._instance = self
        # Wire card registration into sentinel handler
        self.sentinel.register_handler(self._register_card)
        self._thread = threading.Thread(
            target=self._heartbeat, daemon=True, name="goa-heartbeat"
        )
        self._thread.start()
        logger.info("[GraphOpsAutopilot] started — Tier 2/3 patrol active")

    def stop(self) -> None:
        self._running = False
        if GraphOpsAutopilot._instance is self:
            GraphOpsAutopilot._instance = None
        if self._thread:
            self._thread.join(timeout=2.0)
        logger.info("[GraphOpsAutopilot] stopped (alerts=%d)", self.sentinel.alert_count)

    def _heartbeat(self) -> None:
        while self._running:
            time.sleep(PATROL_SLEEP_S)


# ── MCP tool registration ─────────────────────────────────────────────────────

def register_autopilot_tools(engine, mcp_handler,
                              autopilot: Optional[GraphOpsAutopilot] = None,
                              ) -> GraphOpsAutopilot:
    """Register GraphOps Autopilot MCP tools; creates/starts Autopilot if needed.

    Registers 4 tools:
      graphops_autopilot_status   — runtime stats
      graphops_suggestion_queue   — Tier 1 queue
      graphops_observation_log    — Tier 0 log
      graphops_format_card        — render top suggestion as text card
    """
    from mcp_server import ToolDef

    if autopilot is None:
        autopilot = GraphOpsAutopilot(engine)
        autopilot.start()

    def _status(params: dict) -> dict:
        return autopilot.status()

    def _suggestion_queue(params: dict) -> dict:
        return {"suggestions": autopilot.get_suggestion_queue()}

    def _observation_log(params: dict) -> dict:
        return {"observations": autopilot.get_observation_log()}

    def _format_card(params: dict) -> dict:
        sq = autopilot.get_suggestion_queue()
        if not sq:
            return {"card": "No suggestions pending."}
        top = sq[0]
        card = EventCard(
            pattern     = top["pattern"],
            nodes       = top["nodes"],
            window_ms   = top["window_ms"],
            confidence  = top["score"],
            tier        = 1,
            source_type = "",
        )
        return {"card": card.format_card()}

    mcp_handler._tools["graphops_autopilot_status"] = ToolDef(
        name        = "graphops_autopilot_status",
        description = (
            "Return GraphOps Autopilot runtime status: running flag, alert_count, "
            "suggestion queue length, observation log length, and dedup key count."
        ),
        input_schema = {"type": "object", "properties": {}},
        fn           = _status,
    )
    mcp_handler._tools["graphops_suggestion_queue"] = ToolDef(
        name        = "graphops_suggestion_queue",
        description = (
            "Return the current Tier 1 suggestion queue — score-sorted GraphOps findings "
            "(confidence 0.70–0.80) awaiting analyst review."
        ),
        input_schema = {"type": "object", "properties": {}},
        fn           = _suggestion_queue,
    )
    mcp_handler._tools["graphops_observation_log"] = ToolDef(
        name        = "graphops_observation_log",
        description = (
            "Return low-confidence observations (score 0.60–0.70) logged by the "
            "GraphOps Sentinel. Useful for forensics and analyst training."
        ),
        input_schema = {"type": "object", "properties": {}},
        fn           = _observation_log,
    )
    mcp_handler._tools["graphops_format_card"] = ToolDef(
        name        = "graphops_format_card",
        description = (
            "Format the top queued GraphOps suggestion as a human-readable analyst card "
            "showing Pattern, Nodes, Window, Confidence, and Suggested Actions."
        ),
        input_schema = {"type": "object", "properties": {}},
        fn           = _format_card,
    )

    def _submit_feedback(params: dict) -> dict:
        card_id = params.get("card_id", "")
        verdict = params.get("verdict", "")
        notes   = params.get("notes", "")
        if not card_id or not verdict:
            return {"error": "card_id and verdict are required"}
        valid_verdicts = ("true_positive", "false_positive", "wrong_label")
        if verdict not in valid_verdicts:
            return {"error": f"verdict must be one of {valid_verdicts}"}
        return autopilot.submit_feedback(card_id, verdict, notes)

    mcp_handler._tools["graphops_submit_feedback"] = ToolDef(
        name        = "graphops_submit_feedback",
        description = (
            "Submit analyst feedback for a specific EventCard to the TAK-ML server. "
            "Used to correct model predictions and build training data for the feedback loop. "
            "Requires a card_id (from EventCard.card_id) and a verdict."
        ),
        input_schema = {
            "type":       "object",
            "required":   ["card_id", "verdict"],
            "properties": {
                "card_id": {
                    "type":        "string",
                    "description": "8-char hex EventCard ID (from card_id field)",
                },
                "verdict": {
                    "type":        "string",
                    "enum":        ["true_positive", "false_positive", "wrong_label"],
                    "description": "Analyst assessment of the alert",
                },
                "notes": {
                    "type":        "string",
                    "description": "Optional analyst note (context, alternative explanation)",
                },
            },
        },
        fn = _submit_feedback,
    )

    logger.info("[mcp] GraphOps Autopilot tools registered (5 tools)")
    return autopilot


# ── Wire into mcp_server.register_mcp_routes() ───────────────────────────────
# Call this after MCPHandler is created:
#
#   from graphops_autopilot import register_autopilot_tools
#   register_autopilot_tools(engine, handler)


# ── Self-test ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.DEBUG, stream=sys.stderr,
                        format="%(levelname)s [%(name)s] %(message)s")

    PASS = "[✓]"
    FAIL = "[✗]"
    results = []

    def check(label, cond):
        tag = PASS if cond else FAIL
        print(f"  {tag}  {label}")
        results.append(cond)

    # ── Synthetic alert types (mirror topology_drift dataclasses) ─────────────
    from dataclasses import dataclass as dc

    @dc
    class _DriftAlert:
        ts:           float
        node_id:      str
        alert_type:   str
        degree_delta: int
        in_degree:    int
        out_degree:   int
        score:        float

    @dc
    class _FanInAlert:
        ts:               float
        dst_node:         str
        unique_src_count: int
        window_ms:        int
        ip_entropy:       float
        timing_entropy:   float
        verdict:          str

    # ── EventCard ─────────────────────────────────────────────────────────────
    print("\n═══ EventCard format_card ════════════════════════════════════")
    card = EventCard(
        pattern="botnet_coordination", nodes=94, window_ms=180, confidence=0.84,
        tier=2, source_type="fanin", node_ids=["node:0x8099ded263f808db"],
        temporal_sync=0.82, ip_entropy=6.91,
        suggested_actions=["Cluster timing", "Analyze ASN diversity"],
    )
    rendered = card.format_card()
    print(rendered)
    check("format_card contains Pattern",    "Pattern" in rendered)
    check("format_card contains Confidence", "0.84"    in rendered)
    check("format_card contains Suggested",  "▶"       in rendered)

    # ── AlertDedup ────────────────────────────────────────────────────────────
    print("\n═══ AlertDedup ═══════════════════════════════════════════════")
    dedup = AlertDedup(ttl_s=0.3)
    dup1 = dedup.is_duplicate("fanin", "botnet_coordination", ["nodeA"], 200)
    dup2 = dedup.is_duplicate("fanin", "botnet_coordination", ["nodeA"], 200)
    dup3 = dedup.is_duplicate("fanin", "botnet_coordination", ["nodeB"], 200)  # diff node
    time.sleep(0.35)
    dup4 = dedup.is_duplicate("fanin", "botnet_coordination", ["nodeA"], 200)  # TTL expired
    check("first occurrence not duplicate",  not dup1)
    check("same key is duplicate",           dup2)
    check("different node not duplicate",    not dup3)
    check("after TTL expiry not duplicate",  not dup4)

    # ── TierRouter ────────────────────────────────────────────────────────────
    print("\n═══ TierRouter ═══════════════════════════════════════════════")
    check("0.55 → discard (-1)",      TierRouter.tier(0.55) == -1)
    check("0.62 → observation (0)",   TierRouter.tier(0.62) ==  0)
    check("0.72 → suggestion (1)",    TierRouter.tier(0.72) ==  1)
    check("0.82 → alert (2)",         TierRouter.tier(0.82) ==  2)
    check("0.92 → autonomous (3)",    TierRouter.tier(0.92) ==  3)

    # ── Scoring ───────────────────────────────────────────────────────────────
    print("\n═══ Scoring ══════════════════════════════════════════════════")
    da = _DriftAlert(ts=0.0, node_id="n1", alert_type="scanner",
                     degree_delta=60, in_degree=0, out_degree=60, score=0.75)
    fa_bot = _FanInAlert(ts=0.0, dst_node="n2", unique_src_count=120,
                         window_ms=200, ip_entropy=6.91,
                         timing_entropy=0.01, verdict="botnet_coordination")
    fa_spike = _FanInAlert(ts=0.0, dst_node="n3", unique_src_count=30,
                           window_ms=500, ip_entropy=3.0,
                           timing_entropy=2.0, verdict="fan_in_spike")
    ds = _score_drift(da)
    fs_bot   = _score_fanin(fa_bot)
    fs_spike = _score_fanin(fa_spike)
    print(f"  drift score   = {ds:.4f}")
    print(f"  fanin botnet  = {fs_bot:.4f}")
    print(f"  fanin spike   = {fs_spike:.4f}")
    check("drift score passthrough",             ds == 0.75)
    check("botnet_coordination (low t_ent) → tier 3",
          TierRouter.tier(fs_bot)   == 3)
    check("fan_in_spike (high timing entropy) → discarded",
          TierRouter.tier(fs_spike) == -1)

    # ── SentinelLoop ──────────────────────────────────────────────────────────
    print("\n═══ SentinelLoop dispatch ════════════════════════════════════")
    received_cards = []
    sentinel = SentinelLoop()
    sentinel.register_handler(received_cards.append)

    # Fire a scanner drift alert (score=0.75 → tier 1 suggestion)
    sentinel._on_drift(da)
    check("scanner queued in suggestion queue",
          len(sentinel.get_suggestion_queue()) == 1)
    check("scanner NOT in alert handlers (tier 1)",
          len(received_cards) == 0)

    # Fire a botnet fan-in alert (score~0.88 → tier 3, but no investigator)
    sentinel._on_fanin(fa_bot)
    check("botnet alert dispatched to handlers",
          len(received_cards) == 1)
    check("botnet card tier=3 (score ≥ 0.90)",
          received_cards[0].tier == 3)

    # Dedup: same alert again → suppressed
    sentinel._on_fanin(fa_bot)
    check("dedup suppresses repeated fanin alert",
          len(received_cards) == 1)

    # Low-entropy score (0.55 → discard)
    fa_low = _FanInAlert(ts=0.0, dst_node="n4", unique_src_count=5,
                         window_ms=500, ip_entropy=1.0,
                         timing_entropy=5.0, verdict="fan_in_spike")
    sentinel._on_fanin(fa_low)
    check("low-confidence alert discarded",
          len(received_cards) == 1)

    # ── GraphOpsAutopilot lifecycle ───────────────────────────────────────────
    print("\n═══ GraphOpsAutopilot lifecycle ══════════════════════════════")
    pilot = GraphOpsAutopilot()
    pilot.start()
    s = pilot.status()
    check("autopilot running=True",      s["running"]     == True)
    check("alert_count starts at 0",     s["alert_count"] == 0)
    pilot.stop()
    s2 = pilot.status()
    check("autopilot running=False after stop", s2["running"] == False)

    # ── Summary ───────────────────────────────────────────────────────────────
    total  = len(results)
    passed = sum(results)
    print(f"\n═══ {passed}/{total} tests passed", "✓ ALL PASS" if passed == total else "✗ FAILURES")
    sys.exit(0 if passed == total else 1)
