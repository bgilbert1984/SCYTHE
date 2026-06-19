"""
behavior_groups.py — Behavioral Session Group (BSG) Detector.

Collapses raw network sessions into intent-revealing behavioral aggregates:

  BEACON      — Periodic callbacks to the same destination
  PORT_SCAN   — Single source probing many destination ports
  HORIZ_SCAN  — Single source probing many hosts on one port
  FAILED_HANDSHAKE — SYN-only / incomplete TCP connections
  DATA_EXFIL  — Anomalous outbound byte volumes

Design Principles:
    - Raw sessions are NEVER deleted, only grouped.
    - Every derivation is explicit: SESSION_MEMBER_OF_BEHAVIOR_GROUP edges.
    - Court-defensible: confidence scores + detection rationale stored in metadata.
    - BSG nodes are "cognitive compression" — not evidence replacement.

Usage:
    from behavior_groups import BehaviorGroupDetector, BSGConfig
    detector = BehaviorGroupDetector(engine, config=BSGConfig())
    result = detector.detect_all(sessions)
    # result.groups: list of BSG dicts
    # result.edges: list of membership edges
    # Nodes/edges already emitted to engine

    # From API (on existing graph data):
    result = detector.detect_from_graph()
"""
from __future__ import annotations

import hashlib
import logging
import math
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BSGConfig:
    """Tunable thresholds for behavioral detection heuristics."""

    # ── Beaconing ────────────────────────────────────────────────────
    beacon_min_sessions: int = 3          # Min sessions to same dst for beacon
    beacon_max_interval_cv: float = 0.5   # Coefficient of variation threshold
    beacon_max_byte_cv: float = 0.6       # Byte size regularity threshold

    # ── Port Scanning ────────────────────────────────────────────────
    scan_min_ports: int = 10              # Min unique dst_ports from one src
    scan_max_duration: float = 5.0        # Short sessions (seconds)

    # ── Horizontal Scanning ──────────────────────────────────────────
    hscan_min_hosts: int = 5              # Min unique dst_ips on same port
    hscan_max_duration: float = 5.0       # Short sessions (seconds)

    # ── Failed Handshakes ────────────────────────────────────────────
    failed_syn_only: bool = True          # SYN without ACK/PSH = failed
    failed_min_count: int = 3             # Min failed handshakes to group

    # ── Data Exfiltration ────────────────────────────────────────────
    exfil_min_bytes: int = 10_000         # 10 KB threshold
    exfil_byte_rate_threshold: float = 5_000.0  # bytes/sec suspicion threshold
    exfil_min_sessions: int = 1           # Min sessions to form exfil group

    # ── General ──────────────────────────────────────────────────────
    source_tag: str = "bsg_detector"      # Provenance source


# ─────────────────────────────────────────────────────────────────────────────
# BSG Result
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BSGResult:
    """Result of BSG detection across all behaviors."""
    groups_created: int = 0
    edges_created: int = 0
    sessions_grouped: int = 0
    sessions_total: int = 0
    groups: List[Dict[str, Any]] = field(default_factory=list)
    by_behavior: Dict[str, int] = field(default_factory=lambda: {
        "BEACON": 0, "PORT_SCAN": 0, "HORIZ_SCAN": 0,
        "FAILED_HANDSHAKE": 0, "DATA_EXFIL": 0,
    })
    duration_sec: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        lines = [
            f"BSG Detection: {self.groups_created} groups from "
            f"{self.sessions_grouped}/{self.sessions_total} sessions",
        ]
        for behavior, count in self.by_behavior.items():
            if count > 0:
                lines.append(f"  {behavior}: {count} groups")
        lines.append(f"  Duration: {self.duration_sec:.2f}s")
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Deterministic IDs
# ─────────────────────────────────────────────────────────────────────────────

def _bsg_id(behavior: str, key: str) -> str:
    """Deterministic BSG node ID."""
    h = hashlib.sha256(f"{behavior}:{key}".encode()).hexdigest()[:12]
    return f"BSG-{behavior}-{h}"


def _bsg_edge_id(session_id: str, bsg_id: str) -> str:
    """Deterministic membership edge ID."""
    return f"e:bsg:{session_id}:{bsg_id}"


# ─────────────────────────────────────────────────────────────────────────────
# Session Normalization
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_session(raw: Any) -> Dict[str, Any]:
    """Normalize a session from either SessionData, HGNode, or dict.

    Handles both pcap_ingest (SessionData objects) and pcap_registry
    (dict/HGNode with labels) formats transparently.
    """
    if hasattr(raw, "session_id"):
        # SessionData object (from pcap_ingest)
        return {
            "session_id": raw.session_id,
            "src_ip": raw.src_ip or "",
            "dst_ip": raw.dst_ip or "",
            "src_port": raw.src_port,
            "dst_port": raw.dst_port,
            "proto": (raw.proto or "").upper(),
            "tcp_flags": list(raw.tcp_flags) if hasattr(raw, "tcp_flags") else [],
            "duration_sec": getattr(raw, "duration_sec", 0.0),
            "total_bytes": getattr(raw, "total_bytes", 0),
            "packet_count": getattr(raw, "packet_count", 0),
            "time_bucket": getattr(raw, "time_bucket", 0),
            "pcap_file": getattr(raw, "pcap_file", ""),
        }

    # Dict or HGNode — extract from labels
    # Avoid calling arbitrary to_dict() implementations that may walk relationships.
    if hasattr(raw, "to_dict") and type(raw).__name__ in {"HGNode", "HGEdge"}:
        try:
            raw = raw.to_dict()
        except Exception:
            raw = {}

    if isinstance(raw, dict):
        labels = raw.get("labels", raw)
        sid = raw.get("id", labels.get("session_id", ""))
        tcp_flags = labels.get("tcp_flags", [])
        if isinstance(tcp_flags, str):
            tcp_flags = list(tcp_flags)
        return {
            "session_id": sid,
            "src_ip": labels.get("src_ip", ""),
            "dst_ip": labels.get("dst_ip", ""),
            "src_port": labels.get("src_port"),
            "dst_port": labels.get("dst_port"),
            "proto": (labels.get("proto", "") or "").upper(),
            "tcp_flags": tcp_flags,
            "duration_sec": float(labels.get("duration_sec", 0)),
            "total_bytes": int(labels.get("total_bytes", 0)),
            "packet_count": int(labels.get("packet_count", 0)),
            "time_bucket": int(labels.get("time_bucket", 0)),
            "pcap_file": labels.get("pcap_file", raw.get("metadata", {}).get(
                "provenance", {}).get("pcap_file", "")),
        }

    return {}


# ─────────────────────────────────────────────────────────────────────────────
# Statistics Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _coefficient_of_variation(values: List[float]) -> float:
    """CV = std/mean.  0.0 = perfectly uniform, >1.0 = very irregular."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    if mean == 0:
        return 0.0
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(variance) / mean


def _inter_arrival_times(time_buckets: List[int]) -> List[float]:
    """Calculate inter-arrival times from sorted time buckets."""
    sorted_t = sorted(time_buckets)
    return [float(sorted_t[i+1] - sorted_t[i]) for i in range(len(sorted_t) - 1)]


# ─────────────────────────────────────────────────────────────────────────────
# Behavioral Session Group Detector
# ─────────────────────────────────────────────────────────────────────────────

class BehaviorGroupDetector:
    """Detects behavioral patterns in session data and emits BSG nodes/edges.

    Heuristics:
        1. BEACON     — Periodic connections to same destination
        2. PORT_SCAN  — One source → many ports on one destination
        3. HORIZ_SCAN — One source → many hosts on one port
        4. FAILED_HANDSHAKE — SYN-only TCP (no data exchanged)
        5. DATA_EXFIL — Anomalous outbound byte volumes

    All detections are emitted as:
        - behavior_group node (aggregate summary)
        - SESSION_MEMBER_OF_BEHAVIOR_GROUP edge (per member session)
    """

    def __init__(self, engine: Any, config: Optional[BSGConfig] = None):
        self.engine = engine
        self.config = config or BSGConfig()
        self._emitted_ids: Set[str] = set()

    # ──────────────────────────────────────────────────────────────────
    # Main entry points
    # ──────────────────────────────────────────────────────────────────

    def detect_all(self, sessions: Any) -> BSGResult:
        """Run all behavioral detectors on a list of sessions.

        Args:
            sessions: List of SessionData, dicts, or HGNodes.

        Returns:
            BSGResult with groups and membership edges emitted to engine.
        """
        t0 = time.monotonic()

        # Normalize all sessions
        normalized = [_normalize_session(s) for s in sessions]
        normalized = [s for s in normalized if s.get("session_id")]

        result = BSGResult(sessions_total=len(normalized))

        if not normalized:
            result.duration_sec = time.monotonic() - t0
            return result

        grouped_session_ids: Set[str] = set()

        # 1. Beaconing
        beacon_groups = self._detect_beaconing(normalized)
        for g in beacon_groups:
            self._emit_group(g)
            result.groups.append(g)
            result.by_behavior["BEACON"] += 1
            grouped_session_ids.update(g["member_session_ids"])

        # 2. Port Scanning
        scan_groups = self._detect_port_scanning(normalized)
        for g in scan_groups:
            self._emit_group(g)
            result.groups.append(g)
            result.by_behavior["PORT_SCAN"] += 1
            grouped_session_ids.update(g["member_session_ids"])

        # 3. Horizontal Scanning
        hscan_groups = self._detect_horizontal_scanning(normalized)
        for g in hscan_groups:
            self._emit_group(g)
            result.groups.append(g)
            result.by_behavior["HORIZ_SCAN"] += 1
            grouped_session_ids.update(g["member_session_ids"])

        # 4. Failed Handshakes
        failed_groups = self._detect_failed_handshakes(normalized)
        for g in failed_groups:
            self._emit_group(g)
            result.groups.append(g)
            result.by_behavior["FAILED_HANDSHAKE"] += 1
            grouped_session_ids.update(g["member_session_ids"])

        # 5. Data Exfiltration
        exfil_groups = self._detect_exfiltration(normalized)
        for g in exfil_groups:
            self._emit_group(g)
            result.groups.append(g)
            result.by_behavior["DATA_EXFIL"] += 1
            grouped_session_ids.update(g["member_session_ids"])

        result.groups_created = len(result.groups)
        result.edges_created = sum(len(g["member_session_ids"]) for g in result.groups)
        result.sessions_grouped = len(grouped_session_ids)
        result.duration_sec = time.monotonic() - t0

        logger.info("[BSG] %s", result.summary())
        return result

    def detect_from_graph(self) -> BSGResult:
        """Run BSG detection on sessions already in the hypergraph.

        Scans the engine's node store for kind='session' nodes,
        normalizes them, and runs all detectors.
        """
        nodes = getattr(self.engine, "nodes", None) or {}
        sessions = []
        for nid, node in nodes.items():
            # Fast-path: read `kind` directly off the object to avoid calling
            # to_dict() (which uses asdict and recurses into nested metadata)
            # for every node in the graph — only convert session nodes.
            if isinstance(node, dict):
                if node.get("kind") != "session":
                    continue
                nd = node
            else:
                # HGNode or similar: check kind field directly before converting
                node_kind = getattr(node, "kind", None) or (
                    node.get("kind") if isinstance(node, dict) else None
                )
                if node_kind != "session":
                    continue
                nd = node.to_dict() if hasattr(node, "to_dict") else {}

            sessions.append(nd)

        logger.info("[BSG] Detected %d session nodes in graph", len(sessions))
        return self.detect_all(sessions)

    # ──────────────────────────────────────────────────────────────────
    # Emitter
    # ──────────────────────────────────────────────────────────────────

    def _emit_group(self, group: Dict[str, Any]) -> None:
        """Emit a behavior_group node + membership edges to the engine."""
        bsg_id = group["bsg_id"]
        now_iso = datetime.now(timezone.utc).isoformat()

        if bsg_id in self._emitted_ids:
            return
        self._emitted_ids.add(bsg_id)

        # ── behavior_group node ──────────────────────────────────────
        self.engine.add_node({
            "id": bsg_id,
            "kind": "behavior_group",
            "labels": {
                "behavior": group["behavior"],
                "member_count": group["member_count"],
                "summary": group["summary"],
                "confidence": group["confidence"],
                "src_ip": group.get("src_ip", ""),
                "dst_ip": group.get("dst_ip", ""),
                "dst_port": group.get("dst_port"),
                "total_bytes": group.get("total_bytes", 0),
                "total_packets": group.get("total_packets", 0),
                "unique_ports": group.get("unique_ports", 0),
                "unique_hosts": group.get("unique_hosts", 0),
                "mean_interval": group.get("mean_interval"),
                "interval_cv": group.get("interval_cv"),
                "detection_rationale": group.get("rationale", ""),
            },
            "metadata": {
                "provenance": {
                    "source": self.config.source_tag,
                    "evidence_type": "behavioral_inference",
                    "detection_time": now_iso,
                },
                "confidence": group["confidence"],
                "lod_level": "strategic",  # BSGs are strategic-level nodes
            },
        })

        # ── SESSION_MEMBER_OF_BEHAVIOR_GROUP edges ───────────────────
        for sid in group["member_session_ids"]:
            eid = _bsg_edge_id(sid, bsg_id)
            if eid not in self._emitted_ids:
                self._emitted_ids.add(eid)
                self.engine.add_edge({
                    "id": eid,
                    "kind": "SESSION_MEMBER_OF_BEHAVIOR_GROUP",
                    "nodes": [sid, bsg_id],
                    "weight": group["confidence"],
                    "labels": {
                        "behavior": group["behavior"],
                    },
                    "metadata": {
                        "provenance": {"source": self.config.source_tag},
                        "confidence": group["confidence"],
                    },
                })

    # ──────────────────────────────────────────────────────────────────
    # Heuristic 1: Beaconing
    # ──────────────────────────────────────────────────────────────────

    def _detect_beaconing(self, sessions: List[Dict]) -> List[Dict]:
        """Detect beaconing: periodic connections to the same dst:port.

        Heuristic:
            - Group sessions by (src_ip → dst_ip:dst_port)
            - Require ≥ beacon_min_sessions in group
            - Check inter-arrival time regularity (low CV)
            - Check byte-size regularity (low CV)

        High confidence when both timing AND size are regular.
        """
        groups = []

        # Group by src → dst:port
        pair_map: Dict[str, List[Dict]] = defaultdict(list)
        for s in sessions:
            if not s["src_ip"] or not s["dst_ip"]:
                continue
            key = f"{s['src_ip']}→{s['dst_ip']}:{s['dst_port'] or 0}"
            pair_map[key].append(s)

        for pair_key, members in pair_map.items():
            if len(members) < self.config.beacon_min_sessions:
                continue

            # Inter-arrival regularity
            time_buckets = sorted(s["time_bucket"] for s in members if s["time_bucket"])
            iats = _inter_arrival_times(time_buckets)
            iat_cv = _coefficient_of_variation(iats) if iats else 999.0

            # Byte-size regularity
            byte_vals = [float(s["total_bytes"]) for s in members]
            byte_cv = _coefficient_of_variation(byte_vals)

            # Scoring
            timing_regular = iat_cv <= self.config.beacon_max_interval_cv
            size_regular = byte_cv <= self.config.beacon_max_byte_cv

            # Need at least timing regularity to be a beacon
            # If ALL sessions are in the same time_bucket (iat_cv=0), it's more
            # likely a burst than a beacon — require at least 2 distinct buckets
            distinct_buckets = len(set(time_buckets))
            if distinct_buckets < 2 and len(members) > 3:
                # Same bucket burst — not beaconing, could be scanning
                continue

            if timing_regular or (len(members) >= 5 and size_regular):
                confidence = 0.5
                rationale_parts = []

                if timing_regular:
                    confidence += 0.25
                    rationale_parts.append(
                        f"timing_cv={iat_cv:.2f} (≤{self.config.beacon_max_interval_cv})"
                    )
                if size_regular:
                    confidence += 0.15
                    rationale_parts.append(
                        f"byte_cv={byte_cv:.2f} (≤{self.config.beacon_max_byte_cv})"
                    )
                if len(members) >= 10:
                    confidence += 0.1
                    rationale_parts.append(f"count={len(members)}")

                confidence = min(confidence, 0.95)

                # Parse src/dst from key
                parts = pair_key.split("→")
                src_ip = parts[0]
                dst_part = parts[1] if len(parts) > 1 else ""
                dst_ip = dst_part.split(":")[0] if ":" in dst_part else dst_part
                dst_port = dst_part.split(":")[-1] if ":" in dst_part else None
                try:
                    dst_port = int(dst_port) if dst_port else None
                except ValueError:
                    dst_port = None

                mean_iat = sum(iats) / len(iats) if iats else 0
                mean_bytes = sum(byte_vals) / len(byte_vals) if byte_vals else 0

                bsg_id = _bsg_id("BEACON", pair_key)
                groups.append({
                    "bsg_id": bsg_id,
                    "behavior": "BEACON",
                    "member_count": len(members),
                    "member_session_ids": [s["session_id"] for s in members],
                    "confidence": round(confidence, 2),
                    "summary": (
                        f"Beacon: {src_ip} → {dst_ip}:{dst_port}, "
                        f"{len(members)} sessions, "
                        f"interval CV={iat_cv:.2f}, "
                        f"mean {mean_bytes:.0f}B"
                    ),
                    "src_ip": src_ip,
                    "dst_ip": dst_ip,
                    "dst_port": dst_port,
                    "total_bytes": sum(s["total_bytes"] for s in members),
                    "total_packets": sum(s["packet_count"] for s in members),
                    "mean_interval": round(mean_iat, 1),
                    "interval_cv": round(iat_cv, 3),
                    "rationale": "; ".join(rationale_parts),
                })

        logger.info("[BSG:BEACON] Detected %d beacon groups", len(groups))
        return groups

    # ──────────────────────────────────────────────────────────────────
    # Heuristic 2: Port Scanning
    # ──────────────────────────────────────────────────────────────────

    def _detect_port_scanning(self, sessions: List[Dict]) -> List[Dict]:
        """Detect port scanning: one source → many ports on a target.

        Heuristic:
            - Group sessions by (src_ip → dst_ip)
            - Count unique dst_ports
            - Require ≥ scan_min_ports unique ports
            - Higher confidence with more ports + short duration sessions
        """
        groups = []

        # Group by src → dst
        pair_map: Dict[str, List[Dict]] = defaultdict(list)
        for s in sessions:
            if not s["src_ip"] or not s["dst_ip"]:
                continue
            key = f"{s['src_ip']}→{s['dst_ip']}"
            pair_map[key].append(s)

        for pair_key, members in pair_map.items():
            dst_ports = set()
            short_sessions = 0
            for s in members:
                if s["dst_port"] is not None:
                    dst_ports.add(s["dst_port"])
                if s["duration_sec"] <= self.config.scan_max_duration:
                    short_sessions += 1

            if len(dst_ports) < self.config.scan_min_ports:
                continue

            # Scoring
            port_ratio = len(dst_ports) / max(len(members), 1)
            short_ratio = short_sessions / max(len(members), 1)

            confidence = 0.5
            rationale_parts = [f"unique_ports={len(dst_ports)}"]

            if len(dst_ports) >= 50:
                confidence += 0.3
                rationale_parts.append("heavy_scan (≥50 ports)")
            elif len(dst_ports) >= 20:
                confidence += 0.2
                rationale_parts.append("moderate_scan (≥20 ports)")
            else:
                confidence += 0.1

            if short_ratio >= 0.8:
                confidence += 0.1
                rationale_parts.append(f"short_sessions={short_ratio:.0%}")

            if port_ratio >= 0.7:
                confidence += 0.05
                rationale_parts.append("high_port_diversity")

            confidence = min(confidence, 0.95)

            parts = pair_key.split("→")
            src_ip = parts[0]
            dst_ip = parts[1] if len(parts) > 1 else ""

            bsg_id = _bsg_id("PORT_SCAN", pair_key)
            groups.append({
                "bsg_id": bsg_id,
                "behavior": "PORT_SCAN",
                "member_count": len(members),
                "member_session_ids": [s["session_id"] for s in members],
                "confidence": round(confidence, 2),
                "summary": (
                    f"Port scan: {src_ip} → {dst_ip}, "
                    f"{len(dst_ports)} ports probed, "
                    f"{len(members)} sessions"
                ),
                "src_ip": src_ip,
                "dst_ip": dst_ip,
                "unique_ports": len(dst_ports),
                "total_bytes": sum(s["total_bytes"] for s in members),
                "total_packets": sum(s["packet_count"] for s in members),
                "rationale": "; ".join(rationale_parts),
            })

        logger.info("[BSG:PORT_SCAN] Detected %d scan groups", len(groups))
        return groups

    # ──────────────────────────────────────────────────────────────────
    # Heuristic 3: Horizontal Scanning
    # ──────────────────────────────────────────────────────────────────

    def _detect_horizontal_scanning(self, sessions: List[Dict]) -> List[Dict]:
        """Detect horizontal scanning: one source → many hosts on same port.

        Heuristic:
            - Group sessions by (src_ip, dst_port)
            - Count unique dst_ips
            - Require ≥ hscan_min_hosts unique destinations
        """
        groups = []

        # Group by src + dst_port
        pair_map: Dict[str, List[Dict]] = defaultdict(list)
        for s in sessions:
            if not s["src_ip"] or s["dst_port"] is None:
                continue
            key = f"{s['src_ip']}:{s['dst_port']}"
            pair_map[key].append(s)

        for pair_key, members in pair_map.items():
            dst_ips = set(s["dst_ip"] for s in members if s["dst_ip"])
            if len(dst_ips) < self.config.hscan_min_hosts:
                continue

            short_sessions = sum(
                1 for s in members
                if s["duration_sec"] <= self.config.hscan_max_duration
            )

            confidence = 0.5
            rationale_parts = [f"unique_hosts={len(dst_ips)}"]

            if len(dst_ips) >= 20:
                confidence += 0.3
                rationale_parts.append("wide_sweep (≥20 hosts)")
            elif len(dst_ips) >= 10:
                confidence += 0.2
            else:
                confidence += 0.1

            short_ratio = short_sessions / max(len(members), 1)
            if short_ratio >= 0.8:
                confidence += 0.1
                rationale_parts.append(f"short_sessions={short_ratio:.0%}")

            confidence = min(confidence, 0.95)

            parts = pair_key.split(":")
            src_ip = parts[0]
            dst_port = int(parts[1]) if len(parts) > 1 else 0

            bsg_id = _bsg_id("HORIZ_SCAN", pair_key)
            groups.append({
                "bsg_id": bsg_id,
                "behavior": "HORIZ_SCAN",
                "member_count": len(members),
                "member_session_ids": [s["session_id"] for s in members],
                "confidence": round(confidence, 2),
                "summary": (
                    f"Horizontal scan: {src_ip} → {len(dst_ips)} hosts "
                    f"on port {dst_port}, {len(members)} sessions"
                ),
                "src_ip": src_ip,
                "dst_port": dst_port,
                "unique_hosts": len(dst_ips),
                "total_bytes": sum(s["total_bytes"] for s in members),
                "total_packets": sum(s["packet_count"] for s in members),
                "rationale": "; ".join(rationale_parts),
            })

        logger.info("[BSG:HORIZ_SCAN] Detected %d horizontal scan groups", len(groups))
        return groups

    # ──────────────────────────────────────────────────────────────────
    # Heuristic 4: Failed Handshakes
    # ──────────────────────────────────────────────────────────────────

    def _detect_failed_handshakes(self, sessions: List[Dict]) -> List[Dict]:
        """Detect failed TCP handshakes: SYN-only, no data exchange.

        Heuristic:
            - TCP sessions where tcp_flags contains only 'S' (SYN)
            - Or sessions with 0 bytes and very short duration
            - Group by src_ip → dst_ip for attack surface mapping
        """
        groups = []

        # Find failed sessions
        failed: Dict[str, List[Dict]] = defaultdict(list)
        for s in sessions:
            if s["proto"] != "TCP":
                continue

            is_failed = False
            flags = s.get("tcp_flags", [])

            if self.config.failed_syn_only:
                # SYN-only: flags contain S but not A (ACK) or P (PSH)
                if isinstance(flags, list):
                    flag_set = set(flags)
                elif isinstance(flags, str):
                    flag_set = set(flags)
                else:
                    flag_set = set()

                if "S" in flag_set and "A" not in flag_set and "P" not in flag_set:
                    is_failed = True

            # Also catch zero-byte TCP sessions (no data exchanged)
            if not is_failed and s["total_bytes"] == 0 and s["packet_count"] <= 3:
                is_failed = True

            if is_failed:
                key = f"{s['src_ip']}→{s['dst_ip']}"
                failed[key].append(s)

        # Group by src → dst
        for pair_key, members in failed.items():
            if len(members) < self.config.failed_min_count:
                continue

            dst_ports = set(s["dst_port"] for s in members if s["dst_port"])

            confidence = 0.6
            rationale_parts = [f"failed_sessions={len(members)}"]

            if len(members) >= 10:
                confidence += 0.2
                rationale_parts.append("many_failures (≥10)")
            if len(dst_ports) > 5:
                confidence += 0.1
                rationale_parts.append(f"across {len(dst_ports)} ports")

            confidence = min(confidence, 0.95)

            parts = pair_key.split("→")
            src_ip = parts[0]
            dst_ip = parts[1] if len(parts) > 1 else ""

            bsg_id = _bsg_id("FAILED_HANDSHAKE", pair_key)
            groups.append({
                "bsg_id": bsg_id,
                "behavior": "FAILED_HANDSHAKE",
                "member_count": len(members),
                "member_session_ids": [s["session_id"] for s in members],
                "confidence": round(confidence, 2),
                "summary": (
                    f"Failed handshakes: {src_ip} → {dst_ip}, "
                    f"{len(members)} attempts on {len(dst_ports)} ports"
                ),
                "src_ip": src_ip,
                "dst_ip": dst_ip,
                "unique_ports": len(dst_ports),
                "total_bytes": sum(s["total_bytes"] for s in members),
                "total_packets": sum(s["packet_count"] for s in members),
                "rationale": "; ".join(rationale_parts),
            })

        logger.info("[BSG:FAILED_HANDSHAKE] Detected %d groups", len(groups))
        return groups

    # ──────────────────────────────────────────────────────────────────
    # Heuristic 5: Data Exfiltration
    # ──────────────────────────────────────────────────────────────────

    def _detect_exfiltration(self, sessions: List[Dict]) -> List[Dict]:
        """Detect potential data exfiltration: anomalous outbound bytes.

        Heuristic:
            - Sessions with total_bytes ≥ exfil_min_bytes
            - High byte rate (bytes per second)
            - Group by src_ip for exfil source mapping
        """
        groups = []

        # Find high-byte sessions
        exfil_map: Dict[str, List[Dict]] = defaultdict(list)
        for s in sessions:
            if s["total_bytes"] < self.config.exfil_min_bytes:
                continue

            # Calculate byte rate
            duration = max(s["duration_sec"], 0.1)  # avoid /0
            byte_rate = s["total_bytes"] / duration

            if (s["total_bytes"] >= self.config.exfil_min_bytes or
                    byte_rate >= self.config.exfil_byte_rate_threshold):
                # Use src_ip as the exfil source
                key = s["src_ip"] or "unknown"
                exfil_map[key].append({
                    **s,
                    "_byte_rate": byte_rate,
                })

        for src_ip, members in exfil_map.items():
            if len(members) < self.config.exfil_min_sessions:
                continue

            total_bytes = sum(s["total_bytes"] for s in members)
            max_bytes = max(s["total_bytes"] for s in members)
            dst_ips = set(s["dst_ip"] for s in members if s["dst_ip"])
            max_rate = max(s["_byte_rate"] for s in members)

            confidence = 0.4
            rationale_parts = [f"total_bytes={total_bytes}"]

            if total_bytes >= 100_000:  # 100 KB
                confidence += 0.3
                rationale_parts.append("large_volume (≥100KB)")
            elif total_bytes >= 50_000:
                confidence += 0.2
            else:
                confidence += 0.1

            if max_rate >= 50_000:  # 50 KB/s
                confidence += 0.15
                rationale_parts.append(f"high_rate ({max_rate:.0f} B/s)")

            if len(members) >= 3:
                confidence += 0.1
                rationale_parts.append(f"repeated ({len(members)} sessions)")

            confidence = min(confidence, 0.95)

            bsg_id = _bsg_id("DATA_EXFIL", f"{src_ip}:{','.join(sorted(dst_ips))}")
            groups.append({
                "bsg_id": bsg_id,
                "behavior": "DATA_EXFIL",
                "member_count": len(members),
                "member_session_ids": [s["session_id"] for s in members],
                "confidence": round(confidence, 2),
                "summary": (
                    f"Exfil suspect: {src_ip} → {len(dst_ips)} destinations, "
                    f"{total_bytes:,}B total, "
                    f"max {max_bytes:,}B/session"
                ),
                "src_ip": src_ip,
                "unique_hosts": len(dst_ips),
                "total_bytes": total_bytes,
                "total_packets": sum(s["packet_count"] for s in members),
                "max_byte_rate": round(max_rate, 1),
                "rationale": "; ".join(rationale_parts),
            })

        logger.info("[BSG:DATA_EXFIL] Detected %d groups", len(groups))
        return groups


# ─────────────────────────────────────────────────────────────────────────────
# MCP Tool Schema
# ─────────────────────────────────────────────────────────────────────────────

MCP_BSG_DETECT_TOOL = {
    "name": "bsg_detect",
    "description": (
        "Run Behavioral Session Group detection on ingested sessions. "
        "Identifies beaconing, scanning, failed handshakes, and exfiltration "
        "patterns, then emits behavior_group nodes with membership edges."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "beacon_min_sessions": {
                "type": "integer",
                "description": "Min sessions to same destination for beacon detection.",
                "default": 3,
            },
            "scan_min_ports": {
                "type": "integer",
                "description": "Min unique destination ports for scan detection.",
                "default": 10,
            },
            "exfil_min_bytes": {
                "type": "integer",
                "description": "Min bytes for exfiltration detection.",
                "default": 10000,
            },
        },
        "required": [],
    },
}


def handle_mcp_bsg_detect(
    engine: Any,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """MCP tool handler for bsg_detect."""
    # If an authoritative projection already exists, refuse to run detection
    try:
        import rf_scythe_api_server as server_mod
        if 'instance_db' in server_mod.__dict__ and server_mod.instance_db and hasattr(server_mod.instance_db, 'list_bsg_projection'):
            try:
                proj = server_mod.instance_db.list_bsg_projection()
                if proj and proj.get('groups'):
                    return {
                        'status': 'error',
                        'code': 'BSG_ALREADY_MATERIALIZED',
                        'message': 'Behavioral groups already detected. Landscape is read-only.'
                    }
            except Exception:
                pass
    except Exception:
        pass

    config = BSGConfig(
        beacon_min_sessions=params.get("beacon_min_sessions", 3),
        scan_min_ports=params.get("scan_min_ports", 10),
        exfil_min_bytes=params.get("exfil_min_bytes", 10_000),
    )
    detector = BehaviorGroupDetector(engine, config)
    result = detector.detect_from_graph()
    return {
        "status": "ok",
        "summary": result.summary(),
        **result.to_dict(),
    }
