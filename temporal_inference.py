"""
Temporal inference helpers for SCYTHE.

This module turns sparse event timestamps + entity metadata into a reusable,
longitudinal temporal fingerprint so downstream systems can reason from
measured cadence instead of narrative guesswork.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import threading
from typing import Any, Dict, Iterable, List, Optional


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _optional_float(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _median(values: Iterable[float]) -> Optional[float]:
    ordered = sorted(float(v) for v in values if v is not None)
    if not ordered:
        return None
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _variation_ratio(intervals: List[float], dominant_period: float) -> float:
    if not intervals or dominant_period <= 0.0:
        return 1.0
    deviations = [abs(interval - dominant_period) / max(dominant_period, 1e-6) for interval in intervals]
    return _clamp(_median(deviations) or 1.0, 0.0, 1.0)


def _extract_periodicity_s(
    entity: Dict[str, Any],
    binding: Dict[str, Any],
    intervals: List[float],
) -> tuple[float, str]:
    metadata = dict(entity.get("metadata") or {})
    temporal = dict(metadata.get("temporal") or {})
    session = dict(metadata.get("session") or {})
    behavior = dict(metadata.get("behavior") or {})
    rf_obs = dict(binding.get("rf_observation") or {})
    net_obs = dict(binding.get("network_observation") or {})

    for source_name, raw_value in (
        ("session.avg_interval_s", session.get("avg_interval_s")),
        ("temporal.periodicity_s", temporal.get("periodicity_s")),
        ("behavior.periodicity_s", behavior.get("periodicity_s")),
        (
            "rf.signal.burst_period_ms",
            (((rf_obs.get("metadata") or {}).get("rf") or {}).get("signal") or {}).get("burst_period_ms"),
        ),
        ("rf_observation.burst_period_ms", rf_obs.get("burst_period_ms")),
        ("network.inter_arrival_ms", net_obs.get("inter_arrival_ms")),
    ):
        value = _optional_float(raw_value)
        if value is None or value <= 0.0:
            continue
        if source_name.endswith("_ms"):
            value = value / 1000.0
        return max(0.0, float(value)), source_name

    if intervals:
        return max(0.0, float(_median(intervals) or 0.0)), "history.median_interval"
    return 0.0, "absent"


def _burst_signature(periodicity_s: float, burstiness: float, observation_count: int) -> str:
    if observation_count <= 1:
        return "insufficient_observations"
    if burstiness >= 0.7:
        return "irregular_burst_fanout"
    if periodicity_s > 0.0 and periodicity_s <= 15.0 and burstiness <= 0.3:
        return "short_high_freq_cluster"
    if periodicity_s > 45.0 and burstiness <= 0.35:
        return "steady_low_freq_heartbeat"
    if burstiness <= 0.45:
        return "steady_low_variance"
    return "mixed_transient_cluster"


def _behavior_scores(
    behavior: Dict[str, Any],
    temporal_phase: str,
    temporal_cohesion: float,
    periodicity_s: float,
    burstiness: float,
    identity_pressure: float,
) -> Dict[str, float]:
    relay_hint = max(
        _safe_float(behavior.get("relay_likelihood"), 0.0),
        _safe_float(behavior.get("relay_score"), 0.0),
    )
    beacon_score = _clamp(
        _safe_float(behavior.get("periodicity_score"), 0.0) * 0.42
        + temporal_cohesion * 0.28
        + (0.18 if temporal_phase in {"stable", "resurrected"} else 0.0)
        + (0.12 if periodicity_s and periodicity_s <= 15.0 else 0.0)
    )
    burst_score = _clamp(
        burstiness * 0.48
        + (1.0 - temporal_cohesion) * 0.16
        + _safe_float(behavior.get("entropy_score"), 0.0) * 0.14
        + (0.12 if periodicity_s and periodicity_s <= 8.0 else 0.0)
    )
    relay_score = _clamp(
        relay_hint * 0.45
        + identity_pressure * 0.2
        + _safe_float(behavior.get("periodicity_score"), 0.0) * 0.15
        + (0.2 if temporal_phase in {"stable", "resurrected"} else 0.0)
    )
    human_score = _clamp(
        (1.0 - _safe_float(behavior.get("periodicity_score"), 0.0)) * 0.35
        + burstiness * 0.15
        + (0.25 if temporal_phase == "emergent" else 0.0)
        + (0.25 if periodicity_s >= 45.0 else 0.0)
    )
    return {
        "beacon": round(beacon_score, 4),
        "burst": round(burst_score, 4),
        "relay": round(relay_score, 4),
        "human": round(human_score, 4),
    }


def _pattern_from_scores(scores: Dict[str, float]) -> str:
    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    if not ordered or ordered[0][1] <= 0.0:
        return "UNKNOWN"
    top_label, top_score = ordered[0]
    if top_score < 0.35:
        return "UNKNOWN"
    return {
        "beacon": "BEACON",
        "burst": "BURST",
        "relay": "RELAY",
        "human": "HUMAN",
    }.get(top_label, "UNKNOWN")


def _behavior_class(pattern: str, scores: Dict[str, float]) -> str:
    if pattern == "BEACON":
        return "BEACON_PATH"
    if pattern == "BURST":
        return "BURST_EXFIL_PATH"
    if pattern == "RELAY":
        return "RELAY_MESH"
    if pattern == "HUMAN":
        return "HUMAN_ACTIVITY"
    if max(scores.values(), default=0.0) > 0.0:
        return "MIXED"
    return "UNKNOWN"


@dataclass
class TemporalFingerprint:
    entity_id: str
    dominant_periods: List[float] = field(default_factory=list)
    harmonics: List[float] = field(default_factory=list)
    burst_signature: str = "insufficient_observations"
    stability: float = 0.0
    last_shift: float = 0.0
    periodicity_s: float = 0.0
    periodicity_confidence: float = 0.0
    temporal_phase: str = "unknown"
    temporal_cohesion: float = 0.0
    last_seen_delta_s: float = 0.0
    latest_gap_s: float = 0.0
    observation_count: int = 0
    burstiness: float = 0.0
    pattern: str = "UNKNOWN"
    behavior_class: str = "UNKNOWN"
    behavior_scores: Dict[str, float] = field(default_factory=dict)
    evidence_present: bool = False
    periodicity_source: str = "absent"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "dominant_periods": list(self.dominant_periods),
            "harmonics": list(self.harmonics),
            "burst_signature": self.burst_signature,
            "stability": round(self.stability, 4),
            "last_shift": round(self.last_shift, 4),
            "periodicity_s": round(self.periodicity_s, 4),
            "periodicity_confidence": round(self.periodicity_confidence, 4),
            "temporal_phase": self.temporal_phase,
            "temporal_cohesion": round(self.temporal_cohesion, 4),
            "last_seen_delta_s": round(self.last_seen_delta_s, 4),
            "latest_gap_s": round(self.latest_gap_s, 4),
            "observation_count": int(self.observation_count),
            "burstiness": round(self.burstiness, 4),
            "pattern": self.pattern,
            "behavior_class": self.behavior_class,
            "behavior_scores": dict(self.behavior_scores),
            "evidence_present": bool(self.evidence_present),
            "periodicity_source": self.periodicity_source,
            "temporal_overlay": {
                "periodicity_s": round(self.periodicity_s, 4),
                "periodicity_confidence": round(self.periodicity_confidence, 4),
                "burstiness": round(self.burstiness, 4),
                "pattern": self.pattern,
                "confidence": round(self.stability, 4),
                "phase": self.temporal_phase,
                "temporal_cohesion": round(self.temporal_cohesion, 4),
                "last_seen_delta_s": round(self.last_seen_delta_s, 4),
            },
        }


class TemporalIdentityLedger:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._store: Dict[str, Dict[str, Any]] = {}

    def update(self, fingerprint: TemporalFingerprint, *, observed_at: float) -> TemporalFingerprint:
        with self._lock:
            previous = dict(self._store.get(fingerprint.entity_id) or {})
            last_shift = float(previous.get("last_shift") or observed_at)
            if previous:
                prev_period = _safe_float(previous.get("periodicity_s"), 0.0)
                prev_phase = str(previous.get("temporal_phase") or "unknown")
                prev_pattern = str(previous.get("pattern") or "UNKNOWN")
                phase_changed = prev_phase != fingerprint.temporal_phase
                pattern_changed = prev_pattern != fingerprint.pattern
                period_changed = False
                if prev_period > 0.0 and fingerprint.periodicity_s > 0.0:
                    period_changed = abs(prev_period - fingerprint.periodicity_s) / max(prev_period, 1.0) > 0.2
                elif bool(prev_period > 0.0) != bool(fingerprint.periodicity_s > 0.0):
                    period_changed = True
                if phase_changed or pattern_changed or period_changed:
                    last_shift = observed_at
            fingerprint.last_shift = last_shift
            self._store[fingerprint.entity_id] = fingerprint.to_dict()
            return fingerprint

    def get(self, entity_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            payload = self._store.get(entity_id)
            return dict(payload) if payload else None

    def snapshot(self, limit: int = 12) -> List[Dict[str, Any]]:
        with self._lock:
            values = list(self._store.values())
        values.sort(
            key=lambda item: (
                -_safe_float(item.get("periodicity_confidence"), 0.0),
                -_safe_float(item.get("stability"), 0.0),
                item.get("entity_id", ""),
            )
        )
        return [dict(item) for item in values[: max(1, int(limit or 1))]]


def compute_temporal_fingerprint(
    *,
    entity_id: str,
    entity: Optional[Dict[str, Any]],
    history_timestamps: List[float],
    binding: Optional[Dict[str, Any]],
    binding_timestamp: float,
    identity_pressure: float = 0.0,
) -> TemporalFingerprint:
    entity = entity or {}
    binding = binding or {}
    metadata = dict(entity.get("metadata") or {})
    temporal = dict(metadata.get("temporal") or {})
    session = dict(metadata.get("session") or {})
    behavior = dict(metadata.get("behavior") or {})

    timestamps = [float(ts) for ts in (history_timestamps or []) if ts and float(ts) > 0.0]
    if binding_timestamp > 0.0 and (not timestamps or abs(binding_timestamp - timestamps[-1]) > 1e-6):
        timestamps.append(binding_timestamp)
    timestamps = sorted(set(timestamps))
    intervals = [
        max(0.0, timestamps[index] - timestamps[index - 1])
        for index in range(1, len(timestamps))
        if timestamps[index] > timestamps[index - 1]
    ]

    periodicity_s, periodicity_source = _extract_periodicity_s(entity, binding, intervals)
    periodicity_s = max(0.0, periodicity_s)
    behavior_periodicity = _clamp(_safe_float(behavior.get("periodicity_score"), 0.0))
    burstiness = _clamp(_safe_float(behavior.get("burstiness"), 0.0))
    persistence_score = _clamp(_safe_float(temporal.get("persistence_score"), 0.0))
    observation_count = max(int(temporal.get("session_seen_count") or 0), len(timestamps))
    session_duration_s = max(
        _safe_float(temporal.get("session_duration_s"), 0.0),
        _safe_float(session.get("duration_s"), 0.0),
        (timestamps[-1] - timestamps[0]) if len(timestamps) >= 2 else 0.0,
    )
    last_seen = max(
        _safe_float(temporal.get("last_seen"), 0.0),
        timestamps[-1] if timestamps else 0.0,
        binding_timestamp,
    )
    last_seen_delta_s = max(0.0, binding_timestamp - last_seen)
    latest_gap_s = intervals[-1] if intervals else 0.0
    previous_gap_s = intervals[-2] if len(intervals) >= 2 else latest_gap_s
    variation = _variation_ratio(intervals, periodicity_s) if periodicity_s > 0.0 else 1.0
    interval_support = _clamp(len(intervals) / 4.0)
    metadata_support = 0.0
    if periodicity_source != "absent":
        metadata_support += 0.25
    if behavior_periodicity > 0.0:
        metadata_support += 0.2
    if persistence_score > 0.0:
        metadata_support += 0.15
    if observation_count >= 3:
        metadata_support += 0.2
    periodicity_confidence = _clamp(
        metadata_support
        + behavior_periodicity * 0.22
        + interval_support * 0.22
        + (1.0 - variation) * 0.21
    )
    temporal_cohesion = _clamp(
        behavior_periodicity * 0.32
        + (1.0 - burstiness) * 0.18
        + persistence_score * 0.18
        + interval_support * 0.14
        + (1.0 - variation) * 0.18
    )

    if observation_count <= 2 or session_duration_s < max(periodicity_s * 1.5, 12.0 if periodicity_s > 0.0 else 20.0):
        temporal_phase = "emergent"
    elif (
        len(intervals) >= 2
        and periodicity_s > 0.0
        and latest_gap_s > max(periodicity_s * 2.4, 20.0)
        and previous_gap_s <= max(periodicity_s * 1.5, 15.0)
    ):
        temporal_phase = "resurrected"
    elif (
        periodicity_s > 0.0
        and (last_seen_delta_s > max(periodicity_s * 1.5, 15.0) or latest_gap_s > max(periodicity_s * 1.8, 20.0))
    ):
        temporal_phase = "decaying"
    elif temporal_cohesion >= 0.58 and periodicity_confidence >= 0.45:
        temporal_phase = "stable"
    elif observation_count <= 1:
        temporal_phase = "unknown"
    else:
        temporal_phase = "emergent" if session_duration_s < 45.0 else "decaying"

    dominant_periods: List[float] = []
    if periodicity_s > 0.0:
        dominant_periods.append(round(periodicity_s, 4))
    if len(intervals) >= 3:
        longer_periods = [interval for interval in intervals if interval > periodicity_s * 1.5] if periodicity_s > 0.0 else []
        alt_period = _median(longer_periods)
        if alt_period and all(abs(alt_period - value) > 1e-3 for value in dominant_periods):
            dominant_periods.append(round(alt_period, 4))
    dominant_periods = dominant_periods[:2]

    harmonics: List[float] = []
    if dominant_periods:
        base_period = dominant_periods[0]
        if base_period > 0.0:
            harmonics.extend(
                round(multiplier * base_period, 4)
                for multiplier in (2.0, 0.5)
                if multiplier * base_period > 0.0
            )
    burst_signature = _burst_signature(periodicity_s, burstiness, observation_count)
    stability = _clamp(temporal_cohesion * 0.52 + periodicity_confidence * 0.48)
    scores = _behavior_scores(
        behavior,
        temporal_phase,
        temporal_cohesion,
        periodicity_s,
        burstiness,
        identity_pressure,
    )
    pattern = _pattern_from_scores(scores)
    behavior_class = _behavior_class(pattern, scores)
    evidence_present = bool(
        periodicity_source != "absent"
        or intervals
        or behavior_periodicity > 0.0
        or persistence_score > 0.0
        or observation_count > 1
    )

    return TemporalFingerprint(
        entity_id=entity_id,
        dominant_periods=dominant_periods,
        harmonics=harmonics,
        burst_signature=burst_signature,
        stability=stability,
        periodicity_s=round(periodicity_s, 4),
        periodicity_confidence=round(periodicity_confidence, 4),
        temporal_phase=temporal_phase,
        temporal_cohesion=round(temporal_cohesion, 4),
        last_seen_delta_s=round(last_seen_delta_s, 4),
        latest_gap_s=round(latest_gap_s, 4),
        observation_count=observation_count,
        burstiness=round(burstiness, 4),
        pattern=pattern,
        behavior_class=behavior_class,
        behavior_scores=scores,
        evidence_present=evidence_present,
        periodicity_source=periodicity_source,
    )


temporal_identity_ledger = TemporalIdentityLedger()
