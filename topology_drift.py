"""topology_drift.py

Stage 6 — Topology Drift + Temporal Fan-In Detector for NerfEngine.

Two complementary detection engines:

TopologyDriftDetector
---------------------
Consumes EdgeTick events from live_ingest, maintains a sliding hypergraph
degree model, and fires DriftAlert events on anomalous degree acceleration.

Detection window: 200 ms (configurable via DRIFT_WINDOW_S).

Alert types:
  scanner        — single source fans out to many destinations in one window
  aggregator     — many sources converge on one destination
  lateral        — bidirectional degree acceleration

Flow-duration fingerprinting uses FLOW_END timestamps to classify connection
lifetime: scan (<1 s), probe (1–10 s), session (<60 s), c2 (>60 s).

TemporalFanInDetector
----------------------
Detects VPN-rotating botnets that evade IP reputation by analysing
*coordination timing* rather than individual IPs.

Key insight: rotating proxies randomise IP/ASN/geo but cannot randomise their
task-scheduler timing. A botnet hitting a login endpoint from 183 unique IPs
in a 200 ms window is statistically impossible without central coordination.

Two entropy signals per window:
  H(IP)     — IP address entropy (high for rotating botnets)
  H(Δt)     — inter-arrival timing entropy (LOW for botnets — they tick in sync)

Verdict: botnet_coordination when fan_in > threshold AND timing_entropy < 1.0

Usage (standalone)
------------------
    detector = TopologyDriftDetector()
    fanin    = TemporalFanInDetector(writer=writer)
    detector.start()
    fanin.start()
    detector.ingest(event_dict)
    fanin.ingest(event_dict)
"""

from __future__ import annotations

import logging
import math
import threading
import time
import urllib.parse
import urllib.request
import datetime
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Tunables ─────────────────────────────────────────────────────────────────

DRIFT_WINDOW_S         = 0.200   # analysis window (200 ms)
SCANNER_OUT_THRESHOLD  = 50      # out-degree delta triggers scanner alert
AGGREGATOR_IN_THRESHOLD = 30     # in-degree delta triggers aggregator alert
LATERAL_THRESHOLD      = 20      # bidirectional delta triggers lateral alert

# Flow duration classification thresholds (seconds)
SCAN_DURATION_MAX  = 1.0
PROBE_DURATION_MAX = 10.0
# flows > 60 s are classified as c2

# Fan-in / temporal motif tunables
FANIN_WINDOW_S         = 0.200   # sliding collection window
FANIN_SRC_THRESHOLD    = 50      # unique srcs per dst per window → alert
FANIN_TIMING_ENTROPY_MAX = 1.0   # low timing entropy = synchronized bots

# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class DriftAlert:
    ts:           float
    node_id:      str
    alert_type:   str          # scanner | aggregator | lateral
    degree_delta: int
    in_degree:    int
    out_degree:   int
    score:        float        # normalised severity 0–1


@dataclass
class _NodeState:
    """Per-node degree snapshot used for delta computation."""
    in_degree:  int = 0
    out_degree: int = 0


@dataclass
class _FlowRecord:
    """Tracks an in-flight flow for duration fingerprinting."""
    edge_id:    str
    start_ns:   int
    src_node:   str = ""
    dst_node:   str = ""


# ── Detector ─────────────────────────────────────────────────────────────────

class TopologyDriftDetector:
    """200 ms sliding-window degree-drift detector.

    Thread-safe: events are submitted from the WebSocket ingest thread;
    analysis runs in a dedicated background timer thread.
    """

    def __init__(self,
                 window_s: float = DRIFT_WINDOW_S,
                 scanner_threshold: int = SCANNER_OUT_THRESHOLD,
                 aggregator_threshold: int = AGGREGATOR_IN_THRESHOLD,
                 lateral_threshold: int = LATERAL_THRESHOLD,
                 writer=None):
        self._window_s             = window_s
        self._scanner_threshold    = scanner_threshold
        self._aggregator_threshold = aggregator_threshold
        self._lateral_threshold    = lateral_threshold
        self._writer               = writer  # optional QuestDBWriter

        # current-window degree accumulators  {node_id: _NodeState}
        self._current: Dict[str, _NodeState] = defaultdict(_NodeState)
        # snapshot from previous window
        self._previous: Dict[str, _NodeState] = defaultdict(_NodeState)
        # in-flight flows for duration tracking {edge_id: _FlowRecord}
        self._flows: Dict[str, _FlowRecord] = {}

        self._lock    = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # alert callback list — callers may subscribe
        self._subscribers = []

        # stats
        self.events_processed = 0
        self.alerts_fired     = 0
        self.windows_analyzed = 0

    # ── lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._analysis_loop, name="drift-detector", daemon=True)
        self._thread.start()
        logger.info("TopologyDriftDetector started [window=%.0fms scanner=%d aggregator=%d]",
                    self._window_s * 1000,
                    self._scanner_threshold,
                    self._aggregator_threshold)

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        logger.info("TopologyDriftDetector stopped [events=%d alerts=%d windows=%d]",
                    self.events_processed, self.alerts_fired, self.windows_analyzed)

    def subscribe(self, cb) -> None:
        """Register a callback(DriftAlert) for real-time alert delivery."""
        self._subscribers.append(cb)

    # ── event ingestion ────────────────────────────────────────────────────────

    def ingest(self, event: dict) -> None:
        """Accept a hypergraph event dict from live_ingest / stream_manager."""
        etype = event.get("type", "")

        if etype == "flow_update":
            self._handle_edge_tick(event)
        elif etype == "flow_start":
            self._handle_flow_start(event)
        elif etype == "flow_end":
            self._handle_flow_end(event)
        elif etype == "graph_edge_open":
            self._handle_graph_edge_open(event)
        elif etype == "graph_edge_close":
            self._handle_graph_edge_close(event)

        self.events_processed += 1

    def _handle_edge_tick(self, event: dict) -> None:
        """Update degree counters for an EdgeTick-derived event."""
        entities = {e["key"]: e["value"] for e in event.get("entities", [])}
        edge_id = entities.get("edge_id", "")
        if not edge_id:
            return

        # For EdgeTick events the 5-tuple isn't present; use edge_id as proxy
        # node identity until a FLOW_START with IP mapping arrives.
        src = entities.get("src_ip", f"src:{edge_id[:16]}")
        dst = entities.get("dst_ip", f"dst:{edge_id[:16]}")

        with self._lock:
            self._current[src].out_degree += 1
            self._current[dst].in_degree  += 1

        # forward to QuestDB writer
        if self._writer:
            try:
                packets = int(entities.get("packets", 0))
                bytes_  = int(entities.get("bytes",   0))
                hi = int(entities.get("edge_hi", "0"), 16) if entities.get("edge_hi", "").startswith("0x") else int(entities.get("edge_hi", 0))
                lo = int(entities.get("edge_lo", "0"), 16) if entities.get("edge_lo", "").startswith("0x") else int(entities.get("edge_lo", 0))
                self._writer.write_edge_tick(edge_id, hi, lo, packets, bytes_)
            except Exception as exc:
                logger.debug("writer error: %s", exc)

    def _handle_flow_start(self, event: dict) -> None:
        entities = {e["key"]: e["value"] for e in event.get("entities", [])}
        edge_id  = entities.get("flow_hash", entities.get("edge_id", ""))
        if not edge_id:
            return
        with self._lock:
            self._flows[edge_id] = _FlowRecord(
                edge_id  = edge_id,
                start_ns = time.time_ns(),
                src_node = entities.get("src_ip", ""),
                dst_node = entities.get("dst_ip", ""),
            )

    def _handle_flow_end(self, event: dict) -> None:
        entities = {e["key"]: e["value"] for e in event.get("entities", [])}
        edge_id  = entities.get("flow_hash", entities.get("edge_id", ""))
        if not edge_id:
            return

        with self._lock:
            record = self._flows.pop(edge_id, None)

        if record:
            duration_s = (time.time_ns() - record.start_ns) / 1e9
            ftype = self._classify_duration(duration_s)
            if ftype == "c2":
                logger.warning("C2-duration flow detected [edge=%s src=%s dst=%s dur=%.1fs]",
                               edge_id, record.src_node, record.dst_node, duration_s)

    def _handle_graph_edge_open(self, event: dict) -> None:
        """Phase B: EDGE_OPEN — update degree using pre-computed node IDs.

        node_id_a / node_id_b are 64-bit integers exposed at the top level of
        the event dict by _graph_edge_to_event().  This path avoids all IP
        string lookup, making it ~3× faster than the legacy flow_start path.
        """
        # Fast path: node IDs at top level (set by _graph_edge_to_event)
        node_a = event.get("node_id_a")
        node_b = event.get("node_id_b")
        if node_a is None or node_b is None:
            # Fallback: extract from entities (shouldn't happen in normal operation)
            entities = {e["key"]: e["value"] for e in event.get("entities", [])}
            node_a_s = entities.get("node_id_a", "")
            node_b_s = entities.get("node_id_b", "")
            if not node_a_s or not node_b_s:
                return
            node_a, node_b = int(node_a_s), int(node_b_s)

        src_key = f"node:{node_a:#018x}"
        dst_key = f"node:{node_b:#018x}"

        with self._lock:
            self._current[src_key].out_degree += 1
            self._current[dst_key].in_degree  += 1

            # Track for duration fingerprinting
            edge_id_int = event.get("edge_id_int")
            if edge_id_int is not None:
                self._flows[hex(edge_id_int)] = _FlowRecord(
                    edge_id  = hex(edge_id_int),
                    start_ns = time.time_ns(),
                    src_node = src_key,
                    dst_node = dst_key,
                )

    def _handle_graph_edge_close(self, event: dict) -> None:
        """Phase B: EDGE_CLOSE — retire the flow record and fingerprint duration."""
        edge_id_int = event.get("edge_id_int")
        if edge_id_int is None:
            return
        edge_key = hex(edge_id_int)

        with self._lock:
            record = self._flows.pop(edge_key, None)

        if record:
            duration_s = (time.time_ns() - record.start_ns) / 1e9
            ftype = self._classify_duration(duration_s)
            if ftype == "c2":
                logger.warning(
                    "C2-duration graph edge closed [edge=%s src=%s dst=%s dur=%.1fs]",
                    edge_key, record.src_node, record.dst_node, duration_s,
                )

    @staticmethod
    def _classify_duration(seconds: float) -> str:
        if seconds < SCAN_DURATION_MAX:
            return "scan"
        if seconds < PROBE_DURATION_MAX:
            return "probe"
        if seconds < 60.0:
            return "session"
        return "c2"

    # ── analysis loop ──────────────────────────────────────────────────────────

    def _analysis_loop(self) -> None:
        while self._running:
            time.sleep(self._window_s)
            self._analyze_window()

    def _analyze_window(self) -> None:
        with self._lock:
            current  = dict(self._current)
            previous = dict(self._previous)
            # roll: current becomes previous, reset accumulators
            self._previous = {nid: _NodeState(s.in_degree, s.out_degree)
                              for nid, s in current.items()}
            self._current  = defaultdict(_NodeState)

        self.windows_analyzed += 1
        alerts = []

        for node_id, cur in current.items():
            prev = previous.get(node_id, _NodeState())

            d_in  = cur.in_degree  - prev.in_degree
            d_out = cur.out_degree - prev.out_degree

            alert_type = None
            delta      = 0

            if d_out >= self._scanner_threshold and d_in < self._aggregator_threshold:
                alert_type = "scanner"
                delta      = d_out
            elif d_in >= self._aggregator_threshold and d_out < self._scanner_threshold:
                alert_type = "aggregator"
                delta      = d_in
            elif d_in >= self._lateral_threshold and d_out >= self._lateral_threshold:
                alert_type = "lateral"
                delta      = d_in + d_out

            if alert_type:
                score = min(1.0, delta / (self._scanner_threshold * 3))
                alert = DriftAlert(
                    ts           = time.time(),
                    node_id      = node_id,
                    alert_type   = alert_type,
                    degree_delta = delta,
                    in_degree    = cur.in_degree,
                    out_degree   = cur.out_degree,
                    score        = score,
                )
                alerts.append(alert)
                logger.warning("DRIFT ALERT [%s] node=%s Δ=%d in=%d out=%d score=%.2f",
                               alert_type, node_id, delta,
                               cur.in_degree, cur.out_degree, score)

        for alert in alerts:
            self.alerts_fired += 1
            self._dispatch(alert)

    def _dispatch(self, alert: DriftAlert) -> None:
        """Fan out alert to all registered subscribers + QuestDB."""
        from questdb_writer import AlertRow
        if self._writer:
            row = AlertRow(
                node_id      = alert.node_id,
                alert_type   = alert.alert_type,
                degree_delta = alert.degree_delta,
                in_degree    = alert.in_degree,
                out_degree   = alert.out_degree,
                score        = alert.score,
                ts_ns        = int(alert.ts * 1e9),
            )
            self._writer.write_alert(row)

        for cb in self._subscribers:
            try:
                cb(alert)
            except Exception as exc:
                logger.debug("alert subscriber error: %s", exc)



# ── FanIn alert ───────────────────────────────────────────────────────────────

@dataclass
class FanInAlert:
    ts:              float
    dst_node:        str
    unique_src_count: int
    window_ms:       int
    ip_entropy:      float   # H(IP) — high = many distinct source IPs
    timing_entropy:  float   # H(Δt) — low  = synchronized timing
    verdict:         str     # botnet_coordination | fan_in_spike


# ── TemporalFanInDetector ─────────────────────────────────────────────────────

class TemporalFanInDetector:
    """Detects VPN-rotating botnet coordination via temporal fan-in motifs.

    Maintains a deque of (src, dst, ts_ns) tuples.  Every FANIN_WINDOW_S the
    deque is swept: for each destination node the detector computes unique
    source count, IP entropy, and inter-arrival timing entropy.  When
    fan_in > FANIN_SRC_THRESHOLD *and* timing_entropy < FANIN_TIMING_ENTROPY_MAX
    a FanInAlert fires — catching botnets that rotate IPs but sync on timing.
    """

    def __init__(self,
                 window_s: float = FANIN_WINDOW_S,
                 src_threshold: int = FANIN_SRC_THRESHOLD,
                 timing_entropy_max: float = FANIN_TIMING_ENTROPY_MAX,
                 http_base: str = "http://127.0.0.1:9000",
                 writer=None):
        self._window_s          = window_s
        self._src_threshold     = src_threshold
        self._timing_entropy_max = timing_entropy_max
        self._http_base         = http_base
        self._writer            = writer  # QuestDBWriter (used for edge ticks; alerts go direct HTTP)

        # ring of (src, dst, ts_ns)
        self._events: Deque[Tuple[str, str, int]] = deque()
        self._lock    = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._subscribers: List = []

        self.events_ingested = 0
        self.alerts_fired    = 0

    # ── lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._analysis_loop, name="fanin-detector", daemon=True)
        self._thread.start()
        logger.info("TemporalFanInDetector started [window=%.0fms src_threshold=%d timing_max=%.1f]",
                    self._window_s * 1000, self._src_threshold, self._timing_entropy_max)

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        logger.info("TemporalFanInDetector stopped [ingested=%d alerts=%d]",
                    self.events_ingested, self.alerts_fired)

    def subscribe(self, cb) -> None:
        self._subscribers.append(cb)

    # ── ingestion ──────────────────────────────────────────────────────────────

    def ingest(self, event: dict) -> None:
        etype = event.get("type", "")

        if etype in ("flow_start", "flow_update"):
            # Legacy IP-based path
            entities = {e["key"]: e["value"] for e in event.get("entities", [])}
            src = entities.get("src_ip", "")
            dst = entities.get("dst_ip", "")
            if not src or not dst:
                return
            ts_ns = time.time_ns()
            with self._lock:
                self._events.append((src, dst, ts_ns))
            self.events_ingested += 1

        elif etype == "graph_edge_open":
            # Phase B: use pre-computed node IDs directly — no IP lookup needed.
            # Represent nodes as hex strings for consistency with legacy path.
            node_a = event.get("node_id_a")
            node_b = event.get("node_id_b")
            if node_a is None or node_b is None:
                entities = {e["key"]: e["value"] for e in event.get("entities", [])}
                node_a_s = entities.get("node_id_a", "")
                node_b_s = entities.get("node_id_b", "")
                if not node_a_s or not node_b_s:
                    return
                node_a, node_b = int(node_a_s), int(node_b_s)
            src = f"node:{node_a:#018x}"
            dst = f"node:{node_b:#018x}"
            ts_ns = time.time_ns()
            with self._lock:
                self._events.append((src, dst, ts_ns))
            self.events_ingested += 1

    # ── analysis ───────────────────────────────────────────────────────────────

    def _analysis_loop(self) -> None:
        while self._running:
            time.sleep(self._window_s)
            self._analyze()

    def _analyze(self) -> None:
        now_ns = time.time_ns()
        cutoff_ns = now_ns - int(self._window_s * 1e9)

        with self._lock:
            # drop events older than one window
            while self._events and self._events[0][2] < cutoff_ns:
                self._events.popleft()
            snapshot = list(self._events)

        if not snapshot:
            return

        # group by dst
        dst_map: Dict[str, List[Tuple[str, int]]] = defaultdict(list)
        for src, dst, ts_ns in snapshot:
            dst_map[dst].append((src, ts_ns))

        for dst, entries in dst_map.items():
            srcs    = [e[0] for e in entries]
            times   = sorted(e[1] for e in entries)
            unique  = len(set(srcs))

            if unique < self._src_threshold:
                continue

            ip_entropy     = self._shannon_entropy(srcs)
            timing_entropy = self._interarrival_entropy(times)

            # botnet_coordination: many unique IPs + synchronized timing
            # fan_in_spike: just a lot of sources (could be legitimate flash crowd)
            if timing_entropy < self._timing_entropy_max:
                verdict = "botnet_coordination"
            else:
                verdict = "fan_in_spike"

            alert = FanInAlert(
                ts               = time.time(),
                dst_node         = dst,
                unique_src_count = unique,
                window_ms        = int(self._window_s * 1000),
                ip_entropy       = ip_entropy,
                timing_entropy   = timing_entropy,
                verdict          = verdict,
            )
            self.alerts_fired += 1
            logger.warning(
                "FAN-IN [%s] dst=%s srcs=%d ip_H=%.2f timing_H=%.2f",
                verdict, dst, unique, ip_entropy, timing_entropy,
            )
            self._dispatch(alert)

    # ── entropy helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _shannon_entropy(items: List[str]) -> float:
        """Shannon entropy H of a list of string labels."""
        if not items:
            return 0.0
        counts: Dict[str, int] = defaultdict(int)
        for item in items:
            counts[item] += 1
        n = len(items)
        return -sum((c / n) * math.log2(c / n) for c in counts.values())

    @staticmethod
    def _interarrival_entropy(sorted_ts_ns: List[int]) -> float:
        """Shannon entropy of inter-arrival time buckets (10 ms resolution).

        Low entropy → arrivals are clustered in regular intervals (bot sync).
        High entropy → arrivals are random (legitimate traffic).
        """
        if len(sorted_ts_ns) < 2:
            return 0.0
        deltas = [(sorted_ts_ns[i+1] - sorted_ts_ns[i]) // 10_000_000   # 10 ms buckets
                  for i in range(len(sorted_ts_ns) - 1)]
        return TemporalFanInDetector._shannon_entropy([str(d) for d in deltas])

    # ── dispatch ───────────────────────────────────────────────────────────────

    def _dispatch(self, alert: FanInAlert) -> None:
        self._http_insert(alert)
        for cb in self._subscribers:
            try:
                cb(alert)
            except Exception as exc:
                logger.debug("fanin subscriber error: %s", exc)

    def _http_insert(self, a: FanInAlert) -> None:
        ts_str = datetime.datetime.fromtimestamp(
            a.ts, tz=datetime.timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
        sql = (
            f"INSERT INTO fan_in_events VALUES ("
            f"'{ts_str}',"
            f"'{a.dst_node}',"
            f"{a.unique_src_count},"
            f"{a.window_ms},"
            f"{a.ip_entropy:.6f},"
            f"{a.timing_entropy:.6f},"
            f"'{a.verdict}'"
            f")"
        )
        try:
            url = f"{self._http_base}/exec?query=" + urllib.parse.quote(sql)
            urllib.request.urlopen(url, timeout=5).read()
        except Exception as exc:
            logger.debug("fan_in_events insert failed: %s", exc)


# ── module singleton ──────────────────────────────────────────────────────────

_detector: Optional[TopologyDriftDetector] = None
_fanin_detector: Optional[TemporalFanInDetector] = None


def get_detector(writer=None) -> TopologyDriftDetector:
    global _detector
    if _detector is None:
        _detector = TopologyDriftDetector(writer=writer)
        _detector.start()
    return _detector


def get_fanin_detector(writer=None) -> TemporalFanInDetector:
    global _fanin_detector
    if _fanin_detector is None:
        _fanin_detector = TemporalFanInDetector(writer=writer)
        _fanin_detector.start()
    return _fanin_detector


# ── Upgrade 2: Precomputed Hypergraph Metrics ─────────────────────────────────

@dataclass
class HypergraphSnapshot:
    """Per-window metrics precomputed once and shared across all detectors.

    Avoids recomputing the same aggregates in each detector independently.
    Updated every DRIFT_WINDOW_S by HypergraphMetricsCollector.
    """
    ts:                  float = 0.0
    total_edges:         int   = 0
    total_nodes:         int   = 0
    cluster_density:     float = 0.0   # edges / (nodes*(nodes-1)) — how connected
    edge_churn:          float = 0.0   # fraction of edges not seen in previous window
    temporal_sync:       float = 0.0   # 1 - normalised timing entropy across all edges
    asn_entropy:         float = 0.0   # H(ASN) of all observed source nodes
    top_fan_in_rate:     float = 0.0   # max fan-in per dst across all active windows
    active_attractors:   int   = 0     # number of live GraphAttractor clusters


class HypergraphMetricsCollector:
    """Computes HypergraphSnapshot once per window; exposed via get_snapshot().

    Ingests the same graph_edge_open/close events as the detectors.
    Designed to be a lightweight aggregator — no heavy per-node bookkeeping.
    """

    def __init__(self, window_s: float = DRIFT_WINDOW_S):
        self._window_s  = window_s
        self._lock      = threading.Lock()
        self._snapshot  = HypergraphSnapshot()

        # Rolling edge set for churn computation
        self._current_edges:  set = set()
        self._previous_edges: set = set()

        # Timing samples for temporal_sync (last window)
        self._ts_samples: deque = deque(maxlen=4096)

        # Source node set for ASN-proxy entropy (use node_id as proxy)
        self._src_nodes: List[str] = []

        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="hypergraph-metrics"
        )
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)

    def ingest(self, event: dict) -> None:
        etype = event.get("type", "")
        if etype not in ("graph_edge_open", "graph_edge_close"):
            return
        edge_id  = event.get("edge_id_int") or event.get("edge_id", "")
        node_a   = str(event.get("node_id_a", ""))
        ts_ns    = event.get("ts_ns", 0)
        with self._lock:
            if etype == "graph_edge_open":
                self._current_edges.add(edge_id)
                if node_a:
                    self._src_nodes.append(node_a)
                if ts_ns:
                    self._ts_samples.append(ts_ns)

    def get_snapshot(self) -> HypergraphSnapshot:
        with self._lock:
            return self._snapshot

    def _loop(self) -> None:
        while self._running:
            time.sleep(self._window_s)
            self._compute()

    def _compute(self) -> None:
        with self._lock:
            cur  = self._current_edges
            prev = self._previous_edges
            total_edges  = len(cur)
            total_nodes  = len(set(self._src_nodes))
            churned      = len(cur - prev)
            edge_churn   = churned / max(1, total_edges)
            cluster_density = (
                total_edges / max(1, total_nodes * (total_nodes - 1))
                if total_nodes > 1 else 0.0
            )
            # temporal_sync: 1 - normalised inter-arrival entropy
            t_samples = list(self._ts_samples)
            asn_src   = list(self._src_nodes)
            # roll
            self._previous_edges = set(cur)
            self._current_edges  = set()
            self._src_nodes      = []
            self._ts_samples.clear()

        t_ent = TemporalFanInDetector._interarrival_entropy(sorted(t_samples))
        t_sync = round(max(0.0, 1.0 - min(1.0, t_ent)), 4)
        asn_ent = TemporalFanInDetector._shannon_entropy(asn_src)

        snap = HypergraphSnapshot(
            ts              = time.time(),
            total_edges     = total_edges,
            total_nodes     = total_nodes,
            cluster_density = round(cluster_density, 6),
            edge_churn      = round(edge_churn, 4),
            temporal_sync   = t_sync,
            asn_entropy     = round(asn_ent, 4),
        )
        with self._lock:
            self._snapshot = snap


# ── Upgrade 3: Autonomous Graph Attractor Detector ───────────────────────────

# Thresholds from Gemma_Llama_MCP.md §"Upgrade 3"
ATTRACTOR_CLUSTER_MIN    = 40    # minimum unique srcs converging on same edge_hash
ATTRACTOR_INFRA_ENTROPY  = 0.8   # infrastructure (node_id) entropy threshold
ATTRACTOR_TEMPORAL_SYNC  = 0.75  # temporal synchronisation threshold
ATTRACTOR_WINDOW_S       = 5.0   # sliding window for edge convergence tracking


@dataclass
class AttractorAlert:
    """Fired when a graph attractor (rotating botnet) cluster is detected.

    Unlike FanInAlert (which keys on dst_node), AttractorAlert keys on
    edge_hash — the same infrastructure path used by rotating nodes.
    Catches residential-proxy and VPN-rotating botnets where every node
    changes IP/ASN every minute but all converge on the same edge subgraph.
    """
    ts:                float
    edge_hash:         int           # edge subgraph hash (kernel-computed)
    cluster_size:      int           # unique src nodes using this edge_hash
    infrastructure_entropy: float    # H(src_node_id) — high = many distinct IPs
    temporal_sync:     float         # 1 - timing_entropy
    verdict:           str           # "rotating_botnet"
    score:             float


class GraphAttractorDetector:
    """Detects VPN-rotating botnets via edge convergence (graph attractors).

    Key insight (Gemma_Llama_MCP.md §"Upgrade 3"):
        Rotating botnets change IPs constantly but their *infrastructure paths*
        (edge_hash) converge on the same subgraph.  This signature is extremely
        hard to hide even with residential proxies or Tor.

    Tracks edge_hash → {src_node_ids} over a sliding ATTRACTOR_WINDOW_S window.
    Fires when:
        cluster_size          > ATTRACTOR_CLUSTER_MIN   (40)
        infrastructure_entropy > ATTRACTOR_INFRA_ENTROPY (0.8)
        temporal_sync          > ATTRACTOR_TEMPORAL_SYNC (0.75)
    """

    def __init__(self, window_s: float = ATTRACTOR_WINDOW_S):
        self._window_s   = window_s
        self._lock       = threading.Lock()
        self._subscribers: List = []

        # edge_hash → deque of (src_node_id, ts_ns)
        self._buckets: Dict[int, deque] = defaultdict(lambda: deque(maxlen=2048))

        self.alerts_fired   = 0
        self.events_ingested = 0
        self._running       = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="attractor-detector"
        )
        self._thread.start()
        logger.info("GraphAttractorDetector started (window=%.1fs)", self._window_s)

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        logger.info("GraphAttractorDetector stopped [events=%d alerts=%d]",
                    self.events_ingested, self.alerts_fired)

    def subscribe(self, cb) -> None:
        self._subscribers.append(cb)

    def ingest(self, event: dict) -> None:
        if event.get("type") != "graph_edge_open":
            return
        edge_hash = event.get("edge_id_int")
        node_a    = str(event.get("node_id_a", ""))
        ts_ns     = event.get("ts_ns", time.time_ns())
        if edge_hash is None or not node_a:
            return
        with self._lock:
            self._buckets[edge_hash].append((node_a, ts_ns))
        self.events_ingested += 1

    def _loop(self) -> None:
        while self._running:
            time.sleep(self._window_s)
            self._analyze()

    def _analyze(self) -> None:
        now_ns  = time.time_ns()
        cutoff  = now_ns - int(self._window_s * 1e9)

        with self._lock:
            buckets = {k: list(v) for k, v in self._buckets.items()}
            self._buckets.clear()

        for edge_hash, entries in buckets.items():
            # Filter to window
            entries = [(src, ts) for src, ts in entries if ts >= cutoff]
            if len(entries) < ATTRACTOR_CLUSTER_MIN:
                continue

            srcs   = [e[0] for e in entries]
            times  = sorted(e[1] for e in entries)
            unique = len(set(srcs))

            if unique < ATTRACTOR_CLUSTER_MIN:
                continue

            infra_ent  = TemporalFanInDetector._shannon_entropy(srcs)
            timing_ent = TemporalFanInDetector._interarrival_entropy(times)
            t_sync     = max(0.0, 1.0 - min(1.0, timing_ent))

            if infra_ent < ATTRACTOR_INFRA_ENTROPY or t_sync < ATTRACTOR_TEMPORAL_SYNC:
                continue

            score = round(
                min(1.0, 0.70
                    + (infra_ent / 10.0) * 0.15
                    + t_sync * 0.15),
                4
            )

            alert = AttractorAlert(
                ts                     = time.time(),
                edge_hash              = edge_hash,
                cluster_size           = unique,
                infrastructure_entropy = round(infra_ent, 4),
                temporal_sync          = round(t_sync, 4),
                verdict                = "rotating_botnet",
                score                  = score,
            )
            self.alerts_fired += 1
            logger.warning(
                "ATTRACTOR [rotating_botnet] edge_hash=0x%x cluster=%d "
                "infra_H=%.2f t_sync=%.2f score=%.2f",
                edge_hash, unique, infra_ent, t_sync, score,
            )
            for cb in self._subscribers:
                try:
                    cb(alert)
                except Exception as exc:
                    logger.debug("attractor subscriber error: %s", exc)


_attractor_detector: Optional[GraphAttractorDetector] = None


def get_attractor_detector() -> GraphAttractorDetector:
    global _attractor_detector
    if _attractor_detector is None:
        _attractor_detector = GraphAttractorDetector()
        _attractor_detector.start()
    return _attractor_detector
