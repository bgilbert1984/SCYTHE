"""
predictive_control_path_engine.py

Lightweight forecast service for probable RF/IP and control-path continuations.

This layer is intentionally additive:
  - observed RF_TO_IP_BINDING edges remain canonical
  - forecast edges are emitted only when explicitly requested
  - read APIs can still surface forecast payloads for UI rendering

Signals blended here:
  - recent RF_TO_IP_BINDING confidence
  - QuestDB temporal pressure (edge rate + top talker density)
  - QuestDB fan-in / relay motifs
  - identity-stitch candidates from semantic retrieval
  - structured RF evidence from upstream emitters like RFUAV
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import ipaddress
import logging
import math
from typing import Any, Callable, Dict, Iterable, List, Optional
from temporal_inference import compute_temporal_fingerprint, temporal_identity_ledger

try:
    from questdb_query import edge_rate, fanin_by_dst, recent_alerts, top_talkers
except Exception:  # pragma: no cover - safe fallback when QuestDB helpers are unavailable
    edge_rate = None
    fanin_by_dst = None
    recent_alerts = None
    top_talkers = None


logger = logging.getLogger(__name__)

FORECAST_MIN_CONFIDENCE = 0.45
FORECAST_RULE_ID = "predictive_control_path_engine.v1"
MOTION_FORECAST_STEPS = 4

try:
    from doma_rf_motion_model import load_default_doma_model, predict_next_states as doma_predict_next_states
except Exception:  # pragma: no cover - optional dependency
    load_default_doma_model = None
    doma_predict_next_states = None


def _stable_id(*parts: Any) -> str:
    payload = "|".join(str(part or "") for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


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


def _label_for_entity(entity_id: str, entity: Optional[Dict[str, Any]]) -> str:
    entity = entity or {}
    return str(
        entity.get("label")
        or entity.get("name")
        or entity.get("title")
        or entity.get("ssid")
        or entity_id
    )


def _coalesce(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _relay_motif_score(entity: Optional[Dict[str, Any]], label: str, dst_node: str = "") -> float:
    entity = entity or {}
    haystack = " ".join(
        str(value or "")
        for value in (
            label,
            entity.get("entity_id"),
            entity.get("type"),
            entity.get("source"),
            entity.get("platform"),
            entity.get("disposition"),
            (entity.get("metadata") or {}).get("source"),
            (entity.get("metadata") or {}).get("network_role"),
            (entity.get("metadata") or {}).get("hostname"),
            dst_node,
        )
    ).lower()
    keywords = (
        "relay",
        "gateway",
        "proxy",
        "bridge",
        "wifi",
        "ap",
        "router",
        "cluster",
        "mesh",
        "control",
        "uplink",
    )
    hits = sum(1 for keyword in keywords if keyword in haystack)
    return _clamp(hits / 3.0)


def _normalize_ip(raw: str) -> Optional[str]:
    candidate = str(raw or "").strip()
    if not candidate:
        return None
    if candidate.startswith("[") and "]" in candidate:
        candidate = candidate[1:candidate.index("]")]
    elif candidate.count(".") == 3 and candidate.count(":") == 1:
        host, _, maybe_port = candidate.rpartition(":")
        if maybe_port.isdigit():
            candidate = host
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def _entity_id_for_ip(raw: str) -> Optional[str]:
    normalized = _normalize_ip(raw)
    if not normalized:
        return None
    return f"IP-{normalized.replace(':', '_').replace('.', '_')}"


def _rf_metadata(binding: Dict[str, Any], entity: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    rf_obs = binding.get("rf_observation") or {}
    rf_meta = (rf_obs.get("metadata") or {}).get("rf")
    if isinstance(rf_meta, dict) and rf_meta:
        return rf_meta

    entity_meta = (entity or {}).get("metadata") or {}
    rf_meta = entity_meta.get("rf")
    if isinstance(rf_meta, dict) and rf_meta:
        return rf_meta
    return {}


def _extract_location(payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, float]]:
    if not isinstance(payload, dict):
        return None

    location = payload.get("location") or {}
    if "lat" in location and "lon" in location:
        return {
            "lat": _safe_float(location.get("lat")),
            "lon": _safe_float(location.get("lon")),
            "alt_m": _safe_float(
                _coalesce(location.get("altitude_m"), location.get("alt_m"), location.get("alt")),
                0.0,
            ),
        }

    if "lat" in payload and ("lon" in payload or "lng" in payload):
        return {
            "lat": _safe_float(payload.get("lat")),
            "lon": _safe_float(_coalesce(payload.get("lon"), payload.get("lng"))),
            "alt_m": _safe_float(_coalesce(payload.get("alt_m"), payload.get("alt")), 0.0),
        }

    sensor = payload.get("sensor") or {}
    sensor_location = sensor.get("location") or {}
    if "lat" in sensor_location and "lon" in sensor_location:
        return {
            "lat": _safe_float(sensor_location.get("lat")),
            "lon": _safe_float(sensor_location.get("lon")),
            "alt_m": _safe_float(
                _coalesce(sensor_location.get("altitude_m"), sensor_location.get("alt_m"), sensor_location.get("alt")),
                0.0,
            ),
        }

    position = payload.get("position")
    if isinstance(position, (list, tuple)) and len(position) >= 2:
        return {
            "lat": _safe_float(position[0]),
            "lon": _safe_float(position[1]),
            "alt_m": _safe_float(position[2] if len(position) > 2 else 0.0, 0.0),
        }
    return None


def _binding_location(binding: Dict[str, Any], entity: Optional[Dict[str, Any]]) -> Optional[Dict[str, float]]:
    for candidate in (
        binding.get("rf_observation"),
        entity or {},
        binding.get("network_observation"),
    ):
        location = _extract_location(candidate)
        if not location:
            continue
        if location.get("lat") is None or location.get("lon") is None:
            continue
        return {
            "lat": float(location["lat"]),
            "lon": float(location["lon"]),
            "alt_m": float(location.get("alt_m", 0.0) or 0.0),
        }
    return None


def _binding_timestamp(binding: Dict[str, Any]) -> float:
    for candidate in (
        binding.get("created_at"),
        (binding.get("rf_observation") or {}).get("timestamp"),
        (binding.get("network_observation") or {}).get("timestamp"),
    ):
        ts = _safe_float(candidate, 0.0)
        if ts > 0.0:
            return ts
    return 0.0


def _entity_metadata(entity: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return dict((entity or {}).get("metadata") or {})


def _entity_section(entity: Optional[Dict[str, Any]], key: str) -> Dict[str, Any]:
    return dict(_entity_metadata(entity).get(key) or {})


def _entity_network_binding(entity: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    entity = entity or {}
    if isinstance(entity.get("network_binding"), dict) and entity.get("network_binding"):
        return dict(entity.get("network_binding") or {})
    return dict(_entity_metadata(entity).get("network_binding") or {})


def _session_window(entity: Optional[Dict[str, Any]]) -> tuple[Optional[float], Optional[float]]:
    temporal = _entity_section(entity, "temporal")
    session = _entity_section(entity, "session")
    start = _optional_float(
        _coalesce(
            temporal.get("session_started_at"),
            temporal.get("first_seen"),
            session.get("started_at"),
        )
    )
    end = _optional_float(
        _coalesce(
            temporal.get("last_seen"),
            session.get("last_seen"),
        )
    )
    return start, end


def _session_overlap_score(
    current_entity: Optional[Dict[str, Any]],
    target_entity: Optional[Dict[str, Any]],
    binding_timestamp: float,
) -> float:
    current_start, current_end = _session_window(current_entity)
    target_start, target_end = _session_window(target_entity)

    if current_start is None and current_end is None and target_start is None and target_end is None:
        return 0.35

    current_start = current_start if current_start is not None else binding_timestamp
    current_end = current_end if current_end is not None else binding_timestamp
    if target_start is None and target_end is None:
        recent_alignment = 1.0 - min(abs(current_end - binding_timestamp), 60.0) / 60.0
        return _clamp(0.35 + recent_alignment * 0.4)

    target_start = target_start if target_start is not None else binding_timestamp
    target_end = target_end if target_end is not None else binding_timestamp
    overlap = max(0.0, min(current_end, target_end) - max(current_start, target_start))
    union = max(current_end, target_end) - min(current_start, target_start)
    overlap_score = overlap / union if union > 0.0 else 0.0
    recent_alignment = 1.0 - min(abs(current_end - target_end), 90.0) / 90.0
    return _clamp(overlap_score * 0.6 + recent_alignment * 0.4)


def _rf_continuity_score(
    current_entity: Optional[Dict[str, Any]],
    target_entity: Optional[Dict[str, Any]],
    rf_signal: Dict[str, Any],
) -> float:
    current_meta = _entity_metadata(current_entity)
    target_meta = _entity_metadata(target_entity)
    current_rf = dict(current_meta.get("rf_profile") or {})
    target_rf = dict(target_meta.get("rf_profile") or {})
    current_rf_id = str(_coalesce(current_meta.get("rf_signature_id"), current_rf.get("rf_signature_id")) or "")
    target_rf_id = str(_coalesce(target_meta.get("rf_signature_id"), target_rf.get("rf_signature_id")) or "")
    current_pattern = str(current_rf.get("pattern") or "")
    target_pattern = str(target_rf.get("pattern") or "")

    signature_score = 0.35
    if current_rf_id and target_rf_id and current_rf_id == target_rf_id:
        signature_score = 1.0
    elif current_pattern and target_pattern and current_pattern == target_pattern:
        signature_score = 0.78
    elif rf_signal.get("present"):
        signature_score = 0.55 + _clamp(_safe_float(rf_signal.get("score"), 0.0)) * 0.45

    behavior = dict(current_meta.get("behavior") or {})
    identity = dict(current_meta.get("identity") or {})
    entropy_score = _clamp(_safe_float(behavior.get("entropy_score"), 0.5))
    continuity_score = _clamp(
        max(
            _safe_float(identity.get("continuity_score"), 0.0),
            _safe_float(identity.get("cluster_confidence"), 0.0),
        )
    )
    return _clamp(signature_score * 0.6 + (1.0 - entropy_score) * 0.25 + continuity_score * 0.15)


def _median(values: List[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _is_uav_like(entity: Optional[Dict[str, Any]], label: str, rf_class: str = "") -> bool:
    rf_class = str(rf_class or "").lower()
    if rf_class in {"uav_controller", "uav_emitter", "drone_video_link", "drone_telemetry"}:
        return True
    entity = entity or {}
    metadata = entity.get("metadata") or {}
    haystack = " ".join(
        str(value or "")
        for value in (
            label,
            entity.get("entity_id"),
            entity.get("type"),
            entity.get("platform"),
            entity.get("source"),
            metadata.get("ontology"),
            metadata.get("entity_type"),
            metadata.get("device_class"),
        )
    ).lower()
    return any(keyword in haystack for keyword in ("uav", "drone", "dji", "fpv", "quadcopter", "uas"))


def _fallback_motion_states(
    history: List[Dict[str, Any]],
    *,
    steps: int,
    step_seconds: float,
) -> List[Dict[str, Any]]:
    if not history:
        return []
    latest = history[-1]
    if len(history) >= 2:
        previous = history[-2]
        dt = max(0.5, float(latest["timestamp"] - previous["timestamp"]))
        d_lat = float(latest["lat"] - previous["lat"]) / dt
        d_lon = float(latest["lon"] - previous["lon"]) / dt
        d_alt = float(latest.get("alt_m", 0.0) - previous.get("alt_m", 0.0)) / dt
    else:
        d_lat = d_lon = d_alt = 0.0
    states: List[Dict[str, Any]] = []
    for step in range(1, max(1, steps) + 1):
        states.append(
            {
                "step": step,
                "time_offset_s": round(step * step_seconds, 3),
                "timestamp": round(float(latest["timestamp"] + step * step_seconds), 3),
                "location": {
                    "lat": round(float(latest["lat"] + d_lat * step * step_seconds), 7),
                    "lon": round(float(latest["lon"] + d_lon * step * step_seconds), 7),
                    "alt_m": round(float(latest.get("alt_m", 0.0) + d_alt * step * step_seconds), 2),
                },
                "confidence": round(max(0.12, min(0.95, float(latest.get("confidence", 0.6)) * (0.92 ** step))), 4),
                "radius_m": round(28.0 + step * 16.0, 2),
                "speed_mps": 0.0,
                "model": "kinematic",
            }
        )
    return states


@dataclass
class PredictionRecord:
    prediction_id: str
    kind: str
    rf_prediction_id: str
    current_entity_id: str
    current_label: str
    target_entity_id: str
    target_label: str
    sensor_node_id: str
    rf_node_id: str
    source_binding_id: str
    confidence: float
    time_horizon_s: int
    candidate_source: str
    supporting_evidence: Dict[str, Any]
    entropy: float = 0.0
    divergence_risk: float = 0.0
    dissonance_score: float = 0.0
    dissonance_zone: str = ""
    identity_pressure: float = 0.0
    temporal_phase: str = ""
    temporal_cohesion: float = 0.0
    periodicity_s: float = 0.0
    last_seen_delta_s: float = 0.0
    intent_hypotheses: Optional[List[Dict[str, Any]]] = None
    top_intent_label: str = ""
    top_intent_probability: float = 0.0
    resilience_score: float = 0.0
    countermeasure_strategy: str = ""
    requires_multi_node_disruption: bool = False
    field_view: Optional[Dict[str, Any]] = None
    motion_forecast: Optional[Dict[str, Any]] = None
    temporal_overlay: Optional[Dict[str, Any]] = None
    temporal_fingerprint: Optional[Dict[str, Any]] = None
    behavior_class: str = ""
    behavior_scores: Optional[Dict[str, float]] = None
    provenance_rule: str = FORECAST_RULE_ID
    obs_class: str = "forecast"
    forecast: bool = True

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "prediction_id": self.prediction_id,
            "kind": self.kind,
            "rf_prediction_id": self.rf_prediction_id,
            "current_entity_id": self.current_entity_id,
            "current_label": self.current_label,
            "target_entity_id": self.target_entity_id,
            "target_label": self.target_label,
            "sensor_node_id": self.sensor_node_id,
            "rf_node_id": self.rf_node_id,
            "source_binding_id": self.source_binding_id,
            "confidence": self.confidence,
            "time_horizon_s": self.time_horizon_s,
            "candidate_source": self.candidate_source,
            "supporting_evidence": self.supporting_evidence,
            "entropy": self.entropy,
            "divergence_risk": self.divergence_risk,
            "dissonance_score": self.dissonance_score,
            "dissonance_zone": self.dissonance_zone,
            "identity_pressure": self.identity_pressure,
            "temporal_phase": self.temporal_phase,
            "temporal_cohesion": self.temporal_cohesion,
            "periodicity_s": self.periodicity_s,
            "last_seen_delta_s": self.last_seen_delta_s,
            "top_intent_label": self.top_intent_label,
            "top_intent_probability": self.top_intent_probability,
            "resilience_score": self.resilience_score,
            "countermeasure_strategy": self.countermeasure_strategy,
            "requires_multi_node_disruption": self.requires_multi_node_disruption,
            "behavior_class": self.behavior_class,
            "provenance_rule": self.provenance_rule,
            "obs_class": self.obs_class,
            "forecast": self.forecast,
            "edge_kinds": ["RF_TO_IP_PREDICTED", "CONTROL_PATH_PREDICTED"],
            "render_style": {
                "line": str((self.field_view or {}).get("line") or "dashed"),
                "opacity": round(float((self.field_view or {}).get("opacity") or (0.22 + self.confidence * 0.38)), 3),
                "pulse": str(
                    (self.field_view or {}).get("pulse")
                    or ("warning" if self.dissonance_zone == "COGNITIVE_CONFLICT_ZONE" else "soft")
                ),
                "ghost": bool((self.field_view or {}).get("ghost", True)),
                "phase": self.temporal_phase or "unknown",
                "flicker": bool(
                    (self.field_view or {}).get("flicker")
                    or self.dissonance_zone == "COGNITIVE_CONFLICT_ZONE"
                    or self.divergence_risk >= 0.55
                ),
                "uncertainty": self.entropy,
                "mode": str((self.field_view or {}).get("mode") or ""),
                "intent": self.top_intent_label,
                "resilience": self.resilience_score,
                "color_lock": bool((self.field_view or {}).get("identity_color_lock")),
                "density": float((self.field_view or {}).get("cluster_density") or 0.0),
            },
        }
        if self.intent_hypotheses:
            payload["intent_hypotheses"] = list(self.intent_hypotheses)
        if self.behavior_scores:
            payload["behavior_scores"] = dict(self.behavior_scores)
        if self.temporal_overlay:
            payload["temporal_overlay"] = dict(self.temporal_overlay)
        if self.temporal_fingerprint:
            payload["temporal_fingerprint"] = dict(self.temporal_fingerprint)
        if self.field_view:
            payload["field_view"] = dict(self.field_view)
        if self.motion_forecast:
            payload["motion_forecast"] = self.motion_forecast
        return payload


class PredictiveControlPathEngine:
    def __init__(self) -> None:
        self.rule_id = FORECAST_RULE_ID
        self._doma_motion_model = None
        if callable(load_default_doma_model):
            try:
                self._doma_motion_model = load_default_doma_model()
            except Exception as exc:
                logger.debug("DOMA motion model unavailable for predictive engine: %s", exc)

    def _questdb_snapshot(
        self,
        *,
        window_ms: int = 5000,
        fanin_limit: int = 10,
        alert_limit: int = 12,
        talker_limit: int = 8,
    ) -> Dict[str, Any]:
        rate = edge_rate(window_ms=window_ms) if callable(edge_rate) else 0.0
        fanin_rows = fanin_by_dst(window_ms=window_ms, limit=fanin_limit) if callable(fanin_by_dst) else []
        alerts = recent_alerts(limit=alert_limit) if callable(recent_alerts) else []
        talkers = top_talkers(window_ms=window_ms, limit=talker_limit) if callable(top_talkers) else []
        return {
            "edge_rate_eps": round(_safe_float(rate), 3),
            "fanin_rows": list(fanin_rows or []),
            "recent_alerts": list(alerts or []),
            "top_talkers": list(talkers or []),
        }

    def _fanin_candidates(
        self,
        *,
        recon_entities_by_id: Dict[str, Dict[str, Any]],
        questdb_signals: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        candidates: List[Dict[str, Any]] = []
        for row in questdb_signals.get("fanin_rows", []):
            dst_node = str(row.get("dst_node") or "").strip()
            entity_id = _entity_id_for_ip(dst_node)
            if not entity_id:
                continue
            target_entity = recon_entities_by_id.get(entity_id) or {}
            unique_srcs = _safe_float(row.get("unique_src_count"))
            ip_entropy = _safe_float(row.get("ip_entropy"))
            timing_entropy = _safe_float(row.get("timing_entropy"))
            score = _clamp(
                (unique_srcs / 12.0) * 0.5
                + min(ip_entropy / 4.0, 1.0) * 0.25
                + min(timing_entropy / 4.0, 1.0) * 0.25
            )
            candidates.append(
                {
                    "entity_id": entity_id,
                    "label": _label_for_entity(entity_id, target_entity) if target_entity else dst_node,
                    "source": "questdb_fanin",
                    "similarity": 0.0,
                    "fanin_score": round(score, 4),
                    "dst_node": dst_node,
                    "verdict": row.get("verdict"),
                    "unique_src_count": row.get("unique_src_count"),
                    "ip_entropy": row.get("ip_entropy"),
                    "timing_entropy": row.get("timing_entropy"),
                }
            )
        return candidates

    def _alert_signal_score(self, entity_id: str, ip_hint: str, questdb_signals: Dict[str, Any]) -> float:
        score = 0.0
        normalized_ip = _normalize_ip(ip_hint or "")
        for row in questdb_signals.get("recent_alerts", []):
            node = str(row.get("node") or "")
            if node == entity_id or (normalized_ip and _normalize_ip(node) == normalized_ip):
                score = max(
                    score,
                    _clamp((_safe_float(row.get("delta"), 1.0) / 10.0) * 0.5 + (_safe_float(row.get("score")) / 3.0) * 0.5),
                )
        return round(score, 4)

    def _rf_signal_score(self, binding: Dict[str, Any], entity: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        rf = _rf_metadata(binding, entity)
        if not rf:
            return {"present": False, "score": 0.0, "evidence": {}}

        signal = rf.get("signal") or {}
        temporal = rf.get("temporal") or {}
        rf_class = str(rf.get("class") or "unknown").lower()
        rf_confidence = _clamp(_safe_float(rf.get("confidence"), 0.0))
        class_weight = {
            "uav_controller": 0.9,
            "uav_emitter": 0.75,
            "drone_video_link": 0.72,
            "drone_telemetry": 0.68,
            "unknown": 0.3,
        }.get(rf_class, 0.5)

        persistence_s = max(0.0, _safe_float(temporal.get("persistence_s"), 0.0))
        repeat_count = max(0.0, _safe_float(temporal.get("repeat_count"), 0.0))
        spectral_entropy = _clamp(_safe_float(signal.get("spectral_entropy"), 0.5))
        burst_period_ms = max(0.0, _safe_float(signal.get("burst_period_ms"), 0.0))
        binding_confidence = _clamp(_safe_float(binding.get("confidence"), 0.0))

        persistence_score = _clamp(math.log1p(persistence_s) / math.log1p(60.0))
        repeat_score = _clamp(math.log1p(repeat_count) / math.log1p(8.0))
        entropy_stability = _clamp(1.0 - spectral_entropy)
        burst_stability = 1.0 if burst_period_ms > 0.0 else 0.25

        score = _clamp(
            class_weight * 0.32
            + rf_confidence * 0.22
            + persistence_score * 0.18
            + repeat_score * 0.08
            + entropy_stability * 0.12
            + burst_stability * 0.08
        )

        if (
            rf_class == "uav_controller"
            and persistence_s >= 12.0
            and spectral_entropy <= 0.35
            and binding_confidence >= 0.72
        ):
            score = _clamp(score + 0.08)

        evidence = {
            "class": rf.get("class"),
            "subtype": rf.get("subtype"),
            "confidence": round(rf_confidence, 4),
            "score": round(score, 4),
            "persistence_s": round(persistence_s, 3),
            "repeat_count": int(repeat_count) if repeat_count else 0,
            "spectral_entropy": round(spectral_entropy, 4),
            "burst_period_ms": round(burst_period_ms, 3) if burst_period_ms else None,
        }
        return {"present": True, "score": round(score, 4), "evidence": evidence}

    def _motion_histories(
        self,
        *,
        recent_bindings: List[Dict[str, Any]],
        recon_entities_by_id: Dict[str, Dict[str, Any]],
    ) -> Dict[str, List[Dict[str, Any]]]:
        histories: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for binding in recent_bindings:
            entity_id = str(binding.get("recon_entity_id") or "").replace("recon:", "")
            if not entity_id:
                continue
            entity = recon_entities_by_id.get(entity_id) or {}
            location = _binding_location(binding, entity)
            if not location:
                continue
            histories[entity_id].append(
                {
                    "timestamp": _binding_timestamp(binding),
                    "lat": location["lat"],
                    "lon": location["lon"],
                    "alt_m": location.get("alt_m", 0.0),
                    "confidence": _safe_float(binding.get("confidence"), 0.6),
                }
            )
        for entity_id, history in list(histories.items()):
            history.sort(key=lambda item: item["timestamp"])
            deduped: List[Dict[str, Any]] = []
            for point in history:
                previous = deduped[-1] if deduped else None
                if previous and all(
                    abs(float(point[key]) - float(previous[key])) < 1e-9
                    for key in ("timestamp", "lat", "lon", "alt_m")
                ):
                    continue
                deduped.append(point)
            histories[entity_id] = deduped[-6:]
        return histories

    def _binding_histories(
        self,
        recent_bindings: List[Dict[str, Any]],
    ) -> Dict[str, List[float]]:
        histories: Dict[str, List[float]] = defaultdict(list)
        for binding in recent_bindings:
            entity_id = str(binding.get("recon_entity_id") or "").replace("recon:", "")
            timestamp = _binding_timestamp(binding)
            if not entity_id or timestamp <= 0.0:
                continue
            histories[entity_id].append(timestamp)
        for entity_id, timestamps in list(histories.items()):
            unique: List[float] = []
            for timestamp in sorted(timestamps):
                if unique and abs(timestamp - unique[-1]) < 1e-6:
                    continue
                unique.append(timestamp)
            histories[entity_id] = unique[-8:]
        return histories

    def _motion_signal_score(self, history: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not history:
            return {"present": False, "score": 0.0, "evidence": {}}
        continuity = _clamp(len(history) / 4.0)
        speed_score = 0.0
        if len(history) >= 2:
            previous = history[-2]
            current = history[-1]
            dt = max(0.5, float(current["timestamp"] - previous["timestamp"]))
            avg_lat = (float(previous["lat"]) + float(current["lat"])) / 2.0
            d_lat_m = (float(current["lat"]) - float(previous["lat"])) * 111_320.0
            d_lon_m = (float(current["lon"]) - float(previous["lon"])) * 111_320.0 * max(math.cos(math.radians(avg_lat)), 1e-6)
            speed_mps = math.sqrt((d_lat_m ** 2) + (d_lon_m ** 2)) / dt
            speed_score = _clamp(speed_mps / 18.0)
        score = _clamp(continuity * 0.7 + speed_score * 0.3)
        return {
            "present": True,
            "score": round(score, 4),
            "evidence": {
                "track_points": len(history),
                "speed_score": round(speed_score, 4),
                "continuity": round(continuity, 4),
            },
        }

    def _temporal_edge_profile(
        self,
        *,
        entity: Dict[str, Any],
        history_timestamps: List[float],
        binding: Dict[str, Any],
        binding_timestamp: float,
        identity_pressure: float = 0.0,
    ) -> Dict[str, Any]:
        entity_id = str(entity.get("entity_id") or entity.get("id") or entity.get("label") or "unknown")
        fingerprint = compute_temporal_fingerprint(
            entity_id=entity_id,
            entity=entity,
            history_timestamps=history_timestamps,
            binding=binding,
            binding_timestamp=binding_timestamp,
            identity_pressure=identity_pressure,
        )
        temporal_identity_ledger.update(fingerprint, observed_at=max(binding_timestamp, 0.0))
        payload = fingerprint.to_dict()
        return {
            "temporal_phase": payload["temporal_phase"],
            "temporal_cohesion": payload["temporal_cohesion"],
            "periodicity_s": payload["periodicity_s"],
            "last_seen_delta_s": payload["last_seen_delta_s"],
            "latest_gap_s": payload["latest_gap_s"],
            "observation_count": payload["observation_count"],
            "periodicity_confidence": payload["periodicity_confidence"],
            "burstiness": payload["burstiness"],
            "pattern": payload["pattern"],
            "behavior_class": payload["behavior_class"],
            "behavior_scores": payload["behavior_scores"],
            "temporal_overlay": payload["temporal_overlay"],
            "temporal_fingerprint": payload,
            "evidence_present": payload["evidence_present"],
            "dominant_periods": payload["dominant_periods"],
            "harmonics": payload["harmonics"],
            "burst_signature": payload["burst_signature"],
            "stability": payload["stability"],
            "last_shift": payload["last_shift"],
            "periodicity_source": payload["periodicity_source"],
        }

    def _path_behavior_profile(
        self,
        *,
        temporal_profile: Dict[str, Any],
        top_intent: Dict[str, Any],
        motif_score: float,
        fanin_score: float,
        identity_pressure_score: float,
    ) -> Dict[str, Any]:
        scores = dict(temporal_profile.get("behavior_scores") or {})
        scores.setdefault("beacon", 0.0)
        scores.setdefault("burst", 0.0)
        scores.setdefault("relay", 0.0)
        scores.setdefault("human", 0.0)

        top_intent_label = str(top_intent.get("label") or "")
        top_intent_probability = _clamp(_safe_float(top_intent.get("probability"), 0.0))
        if top_intent_label == "beacon-maintenance":
            scores["beacon"] = _clamp(max(scores["beacon"], top_intent_probability))
        elif top_intent_label == "data-exfiltration":
            scores["burst"] = _clamp(max(scores["burst"], top_intent_probability))
        elif top_intent_label == "relay-chain-formation":
            scores["relay"] = _clamp(max(scores["relay"], top_intent_probability))

        scores["relay"] = _clamp(max(scores["relay"], motif_score * 0.55 + fanin_score * 0.25 + identity_pressure_score * 0.2))
        ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        top_label = ordered[0][0] if ordered else ""
        pattern = {
            "beacon": "BEACON",
            "burst": "BURST",
            "relay": "RELAY",
            "human": "HUMAN",
        }.get(top_label, temporal_profile.get("pattern") or "UNKNOWN")
        behavior_class = {
            "BEACON": "BEACON_PATH",
            "BURST": "BURST_EXFIL_PATH",
            "RELAY": "RELAY_MESH",
            "HUMAN": "HUMAN_ACTIVITY",
        }.get(pattern, "MIXED" if max(scores.values()) > 0.0 else "UNKNOWN")
        return {
            "pattern": pattern,
            "behavior_class": behavior_class,
            "behavior_scores": {key: round(_clamp(value), 4) for key, value in scores.items()},
        }

    def _identity_pressure_profile(
        self,
        *,
        current_entity: Dict[str, Any],
        target_entity: Dict[str, Any],
        binding: Dict[str, Any],
        rf_signal: Dict[str, Any],
        identity_score: float,
    ) -> Dict[str, Any]:
        identity = _entity_section(current_entity, "identity")
        network_binding = _entity_network_binding(current_entity)
        cluster_stability = _clamp(
            max(
                _safe_float(identity.get("stability_score"), 0.0),
                _safe_float(identity.get("continuity_score"), 0.0),
                _safe_float(identity.get("cluster_confidence"), 0.0),
            )
        )
        rotation_pressure = _clamp(
            max(
                _safe_float(network_binding.get("ip_rotation_pressure"), 0.0),
                min(max(int(network_binding.get("ip_count") or 0) - 1, 0), 4) / 4.0,
            )
        )
        protocol_consistency = _clamp(_safe_float(network_binding.get("protocol_consistency_score"), 0.0))
        session_overlap = _session_overlap_score(current_entity, target_entity, _binding_timestamp(binding))
        rf_continuity = _rf_continuity_score(current_entity, target_entity, rf_signal)
        randomized_ratio = _clamp(_safe_float(identity.get("randomized_ratio"), 0.0))
        masking_pressure = _clamp(rotation_pressure * 0.65 + randomized_ratio * 0.35)
        base_score = _clamp(_safe_float(network_binding.get("identity_pressure"), 0.0))
        score = _clamp(
            max(
                base_score,
                cluster_stability * 0.28
                + rf_continuity * 0.22
                + masking_pressure * 0.20
                + session_overlap * 0.20
                + protocol_consistency * 0.10,
            )
        )
        if identity_score >= 0.6 and masking_pressure >= 0.35:
            score = _clamp(score + 0.06)
        return {
            "score": round(score, 4),
            "cluster_stability": round(cluster_stability, 4),
            "rf_continuity": round(rf_continuity, 4),
            "masking_pressure": round(masking_pressure, 4),
            "session_overlap_score": round(session_overlap, 4),
            "protocol_consistency": round(protocol_consistency, 4),
        }

    def _cognitive_dissonance(
        self,
        *,
        signal_scores: Dict[str, Optional[float]],
    ) -> Dict[str, Any]:
        active = [
            (name, _clamp(_safe_float(score, 0.0)))
            for name, score in (signal_scores or {}).items()
            if score is not None
        ]
        if len(active) < 2:
            return {
                "score": 0.0,
                "zone": "COHERENT",
                "signal_count": len(active),
                "dominant_signal": active[0][0] if active else "",
                "weakest_signal": active[0][0] if active else "",
            }

        values = [score for _, score in active]
        mean_score = sum(values) / len(values)
        spread = math.sqrt(sum((score - mean_score) ** 2 for score in values) / len(values))
        amplitude = max(values) - min(values)
        strong_count = sum(1 for score in values if score >= 0.67)
        weak_count = sum(1 for score in values if score <= 0.33)
        contradiction = 1.0 if strong_count > 0 and weak_count > 0 else 0.0
        score = _clamp(spread * 0.65 + amplitude * 0.25 + contradiction * 0.10)
        if mean_score < 0.25 and contradiction == 0.0:
            score = _clamp(score * 0.35)

        dominant_signal = max(active, key=lambda item: item[1])[0]
        weakest_signal = min(active, key=lambda item: item[1])[0]
        zone = "COGNITIVE_CONFLICT_ZONE" if score >= 0.35 and contradiction > 0.0 else "COHERENT"
        return {
            "score": round(score, 4),
            "zone": zone,
            "signal_count": len(active),
            "dominant_signal": dominant_signal,
            "weakest_signal": weakest_signal,
            "mean_signal": round(mean_score, 4),
            "spread": round(spread, 4),
        }

    def _forecast_uncertainty(
        self,
        *,
        confidence: float,
        dissonance_score: float,
        temporal_phase: str,
        temporal_cohesion: float,
        identity_pressure: float,
    ) -> Dict[str, float]:
        phase_risk = {
            "stable": 0.12,
            "emergent": 0.52,
            "decaying": 0.72,
            "resurrected": 0.64,
        }.get(str(temporal_phase or "").lower(), 0.45)
        entropy = _clamp(
            (1.0 - confidence) * 0.35
            + dissonance_score * 0.40
            + (1.0 - temporal_cohesion) * 0.15
            + (1.0 - identity_pressure) * 0.10
        )
        divergence_risk = _clamp(
            dissonance_score * 0.35
            + phase_risk * 0.30
            + entropy * 0.20
            + (1.0 - confidence) * 0.15
        )
        return {
            "entropy": round(entropy, 4),
            "divergence_risk": round(divergence_risk, 4),
        }

    def _intent_hypotheses(
        self,
        *,
        current_entity: Dict[str, Any],
        current_label: str,
        target_entity: Dict[str, Any],
        target_label: str,
        candidate: Dict[str, Any],
        binding_score: float,
        network_score: float,
        fanin_score: float,
        motif_score: float,
        alert_score: float,
        temporal_score: float,
        temporal_profile: Dict[str, Any],
        identity_pressure: Dict[str, Any],
        rf_signal: Dict[str, Any],
        dissonance: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        current_meta = _entity_metadata(current_entity)
        target_meta = _entity_metadata(target_entity)
        current_behavior = dict(current_meta.get("behavior") or {})
        target_behavior = dict(target_meta.get("behavior") or {})
        periodicity_score = _clamp(
            max(
                _safe_float(current_behavior.get("periodicity_score"), 0.0),
                _safe_float(target_behavior.get("periodicity_score"), 0.0),
            )
        )
        entropy_stability = _clamp(
            1.0 - max(
                _safe_float(current_behavior.get("entropy_score"), 0.5),
                _safe_float(target_behavior.get("entropy_score"), 0.5),
            )
        )
        temporal_phase = str(temporal_profile.get("temporal_phase") or "")
        haystack = " ".join(
            str(value or "")
            for value in (
                current_label,
                target_label,
                current_entity.get("type"),
                target_entity.get("type"),
                current_meta.get("network_role"),
                target_meta.get("network_role"),
                candidate.get("source"),
                candidate.get("verdict"),
                candidate.get("dst_node"),
            )
        ).lower()
        relay_keywords = ("relay", "gateway", "mesh", "bridge", "proxy", "uplink")
        relay_role_score = _clamp(sum(1 for keyword in relay_keywords if keyword in haystack) / 3.0)
        ip_entropy_score = _clamp(_safe_float(candidate.get("ip_entropy"), 0.0) / 4.0)
        unique_src_score = _clamp(_safe_float(candidate.get("unique_src_count"), 0.0) / 8.0)

        hypotheses = [
            {
                "label": "beacon-maintenance",
                "probability": _clamp(
                    periodicity_score * 0.30
                    + entropy_stability * 0.22
                    + _safe_float(temporal_profile.get("temporal_cohesion"), 0.0) * 0.18
                    + temporal_score * 0.14
                    + _safe_float(identity_pressure.get("score"), 0.0) * 0.08
                    + (_safe_float(rf_signal.get("score"), 0.0) if rf_signal.get("present") else 0.0) * 0.08
                    + (0.08 if temporal_phase in {"stable", "resurrected"} else 0.0)
                ),
                "rationale": [
                    "periodic cadence" if periodicity_score >= 0.55 else "",
                    "low entropy rhythm" if entropy_stability >= 0.55 else "",
                    f"phase={temporal_phase}" if temporal_phase else "",
                ],
            },
            {
                "label": "relay-chain-formation",
                "probability": _clamp(
                    fanin_score * 0.28
                    + motif_score * 0.22
                    + relay_role_score * 0.20
                    + _safe_float(identity_pressure.get("score"), 0.0) * 0.12
                    + unique_src_score * 0.10
                    + alert_score * 0.08
                    + (0.08 if temporal_phase in {"stable", "resurrected"} and relay_role_score >= 0.34 else 0.0)
                ),
                "rationale": [
                    "relay/gateway role" if relay_role_score >= 0.34 else "",
                    "fan-in concentration" if fanin_score >= 0.45 else "",
                    "stable relay path" if temporal_phase in {"stable", "resurrected"} else "",
                ],
            },
            {
                "label": "data-exfiltration",
                "probability": _clamp(
                    network_score * 0.22
                    + alert_score * 0.18
                    + ip_entropy_score * 0.18
                    + unique_src_score * 0.10
                    + dissonance.get("score", 0.0) * 0.12
                    + binding_score * 0.10
                    + (1.0 - _safe_float(temporal_profile.get("temporal_cohesion"), 0.0)) * 0.10
                ),
                "rationale": [
                    "network anomaly pressure" if alert_score >= 0.35 else "",
                    "high IP entropy" if ip_entropy_score >= 0.45 else "",
                    "signal disagreement" if dissonance.get("zone") == "COGNITIVE_CONFLICT_ZONE" else "",
                ],
            },
        ]
        ordered = sorted(
            (
                {
                    "label": item["label"],
                    "probability": round(item["probability"], 4),
                    "rationale": [reason for reason in item["rationale"] if reason],
                }
                for item in hypotheses
                if item["probability"] >= 0.28
            ),
            key=lambda item: (-item["probability"], item["label"]),
        )
        if not ordered:
            fallback = max(hypotheses, key=lambda item: item["probability"])
            ordered = [
                {
                    "label": fallback["label"],
                    "probability": round(fallback["probability"], 4),
                    "rationale": [reason for reason in fallback["rationale"] if reason],
                }
            ]
        return ordered[:3]

    def _countermeasure_profile(
        self,
        *,
        current_entity_id: str,
        target_entity_id: str,
        candidate_pool: List[Dict[str, Any]],
        candidate_sources: List[str],
        intent_hypotheses: List[Dict[str, Any]],
        temporal_profile: Dict[str, Any],
        identity_pressure_score: float,
        fanin_score: float,
        divergence_risk: float,
        dissonance_zone: str,
    ) -> Dict[str, Any]:
        alternate_targets = {
            str(candidate.get("entity_id") or "").replace("recon:", "")
            for candidate in (candidate_pool or [])
            if str(candidate.get("entity_id") or "").replace("recon:", "")
            not in {"", current_entity_id, target_entity_id}
        }
        alternate_target_count = len(alternate_targets)
        source_redundancy = _clamp(len(set(candidate_sources or [])) / 6.0)
        phase_resilience = {
            "stable": 0.72,
            "resurrected": 0.84,
            "emergent": 0.42,
            "decaying": 0.28,
        }.get(str(temporal_profile.get("temporal_phase") or "").lower(), 0.45)
        top_intent = (intent_hypotheses or [{}])[0]
        intent_bonus = {
            "relay-chain-formation": 0.14,
            "beacon-maintenance": 0.10,
            "data-exfiltration": 0.08,
        }.get(str(top_intent.get("label") or ""), 0.04)
        reform_probability = _clamp(
            min(alternate_target_count, 3) / 3.0 * 0.32
            + source_redundancy * 0.18
            + identity_pressure_score * 0.22
            + fanin_score * 0.16
            + phase_resilience * 0.12
        )
        resilience_score = _clamp(
            reform_probability * 0.62
            + divergence_risk * 0.12
            + (0.08 if dissonance_zone == "COGNITIVE_CONFLICT_ZONE" else 0.0)
            + intent_bonus
            + min(alternate_target_count, 2) / 2.0 * 0.04
        )
        requires_multi = (
            resilience_score >= 0.68
            or alternate_target_count >= 2
            or (identity_pressure_score >= 0.62 and fanin_score >= 0.52)
            or (
                top_intent.get("label") == "relay-chain-formation"
                and alternate_target_count >= 1
                and identity_pressure_score >= 0.55
            )
        )
        if requires_multi:
            recommended_action = "multi-node disruption"
        elif resilience_score >= 0.45:
            recommended_action = "sensor-focused isolation"
        else:
            recommended_action = "single-node disruption viable"
        return {
            "resilience_score": round(resilience_score, 4),
            "reforms_elsewhere_probability": round(reform_probability, 4),
            "alternate_target_count": alternate_target_count,
            "source_redundancy": round(source_redundancy, 4),
            "phase_resilience": round(phase_resilience, 4),
            "requires_multi_node_disruption": requires_multi,
            "recommended_action": recommended_action,
            "simulate_block_current": round(reform_probability, 4),
            "simulate_block_target": round(_clamp(reform_probability * 0.78 + min(alternate_target_count, 2) / 2.0 * 0.22), 4),
            "simulate_block_rf": round(_clamp(reform_probability * 0.52 + source_redundancy * 0.28 + fanin_score * 0.20), 4),
        }

    def _field_view_profile(
        self,
        *,
        current_entity: Dict[str, Any],
        temporal_profile: Dict[str, Any],
        identity_pressure_score: float,
        top_intent: Dict[str, Any],
        uncertainty: Dict[str, float],
        immune: Dict[str, Any],
        dissonance: Dict[str, Any],
        confidence: float,
    ) -> Dict[str, Any]:
        identity = _entity_section(current_entity, "identity")
        network_binding = _entity_network_binding(current_entity)
        cluster_size = max(
            int(identity.get("cluster_size") or 0),
            int(network_binding.get("binding_count") or 0),
            1,
        )
        identity_continuity = _clamp(
            max(
                _safe_float(identity.get("continuity_score"), 0.0),
                _safe_float(identity.get("cluster_confidence"), 0.0),
            )
        )
        cluster_density = _clamp(min(cluster_size, 6) / 6.0 * 0.55 + identity_pressure_score * 0.45)
        periodicity_s = _safe_float(temporal_profile.get("periodicity_s"), 15.0)
        entropy = _clamp(_safe_float(uncertainty.get("entropy"), 0.0))
        if entropy >= 0.62:
            entropy_rhythm = "chaotic-flicker"
        elif periodicity_s <= 8.0 and temporal_profile.get("temporal_phase") in {"stable", "resurrected"}:
            entropy_rhythm = "steady-pulse"
        else:
            entropy_rhythm = "drift-pulse"

        top_intent_label = str(top_intent.get("label") or "")
        if dissonance.get("zone") == "COGNITIVE_CONFLICT_ZONE" or uncertainty.get("divergence_risk", 0.0) >= 0.55:
            mode = "conflict-flicker"
            line = "dot"
        elif top_intent_label == "relay-chain-formation" and immune.get("resilience_score", 0.0) >= 0.62:
            mode = "relay-lattice"
            line = "dashed"
        elif identity_continuity >= 0.7 and temporal_profile.get("temporal_phase") in {"stable", "resurrected"}:
            mode = "continuity-lock"
            line = "solid"
        else:
            mode = "forecast-ghost"
            line = "dashed"

        return {
            "mode": mode,
            "line": line,
            "opacity": round(0.24 + confidence * 0.36, 3),
            "pulse": "warning" if dissonance.get("zone") == "COGNITIVE_CONFLICT_ZONE" else "soft",
            "ghost": line != "solid",
            "flicker": bool(mode == "conflict-flicker" or uncertainty.get("divergence_risk", 0.0) >= 0.55),
            "identity_color_lock": identity_continuity >= 0.65,
            "identity_continuity": round(identity_continuity, 4),
            "cluster_density": round(cluster_density, 4),
            "entropy_rhythm": entropy_rhythm,
            "observability": "forecast",
        }

    def _motion_forecast(
        self,
        *,
        current_entity_id: str,
        current_entity: Dict[str, Any],
        current_label: str,
        rf_signal: Dict[str, Any],
        history: List[Dict[str, Any]],
        time_horizon_s: int,
        confidence: float,
    ) -> Optional[Dict[str, Any]]:
        rf_class = str((rf_signal.get("evidence") or {}).get("class") or "")
        if not _is_uav_like(current_entity, current_label, rf_class):
            return None
        if not history:
            return None
        step_seconds = max(3.0, float(time_horizon_s) / max(1, MOTION_FORECAST_STEPS - 1))
        if callable(doma_predict_next_states):
            path = doma_predict_next_states(
                history,
                model=self._doma_motion_model,
                steps=MOTION_FORECAST_STEPS,
                step_seconds=step_seconds,
            )
        else:
            path = _fallback_motion_states(
                history,
                steps=MOTION_FORECAST_STEPS,
                step_seconds=step_seconds,
            )
        if not path:
            return None
        model_name = str(path[-1].get("model") or "kinematic")
        return {
            "seed_entity_id": current_entity_id,
            "seed_label": current_label,
            "horizon_s": time_horizon_s,
            "step_seconds": round(step_seconds, 3),
            "history_points": len(history),
            "model": model_name,
            "rf_class": rf_class or None,
            "confidence": round(max(0.12, min(0.99, confidence * 0.94)), 4),
            "path": path,
        }

    def predict(
        self,
        *,
        observer: Dict[str, Any],
        recent_bindings: Iterable[Dict[str, Any]],
        recon_entities_by_id: Dict[str, Dict[str, Any]],
        describe_entity: Callable[[str, Dict[str, Any], Dict[str, Any]], str],
        identity_candidates: Callable[[str, str, int], List[Dict[str, Any]]],
        limit: int = 6,
    ) -> Dict[str, Any]:
        recent_bindings = list(recent_bindings or [])
        questdb_signals = self._questdb_snapshot()
        global_pressure = _clamp(
            min(questdb_signals.get("edge_rate_eps", 0.0) / 120.0, 1.0) * 0.65
            + min(len(questdb_signals.get("top_talkers", [])) / 8.0, 1.0) * 0.35
        )
        observer_recon = str(observer.get("recon_entity_id") or "").replace("recon:", "")
        fanin_candidates = self._fanin_candidates(
            recon_entities_by_id=recon_entities_by_id,
            questdb_signals=questdb_signals,
        )
        binding_histories = self._binding_histories(recent_bindings)
        motion_histories = self._motion_histories(
            recent_bindings=recent_bindings,
            recon_entities_by_id=recon_entities_by_id,
        )
        predictions_by_pair: Dict[str, PredictionRecord] = {}

        for binding in recent_bindings:
            current_entity_id = str(binding.get("recon_entity_id") or "").replace("recon:", "")
            if not current_entity_id or current_entity_id == observer_recon:
                continue

            current_entity = recon_entities_by_id.get(current_entity_id) or {}
            description = describe_entity(current_entity_id, current_entity, binding)
            stitch_candidates = identity_candidates(current_entity_id, description, 4) or []
            candidate_pool = list(stitch_candidates) + list(fanin_candidates)
            if not candidate_pool:
                continue

            binding_score = _clamp(_safe_float(binding.get("confidence"), 0.0))
            current_label = _label_for_entity(current_entity_id, current_entity)
            sensor_node_id = str(observer.get("sensor_node_id") or "")
            rf_node_id = str(binding.get("rf_node_id") or "")
            source_binding_id = str(binding.get("binding_id") or "")
            if not sensor_node_id or not rf_node_id or not source_binding_id:
                continue

            for candidate in candidate_pool:
                candidate_entity_id = str(candidate.get("entity_id") or "").replace("recon:", "")
                if not candidate_entity_id or candidate_entity_id == current_entity_id:
                    continue

                target_entity = recon_entities_by_id.get(candidate_entity_id) or {}
                target_label = str(candidate.get("label") or _label_for_entity(candidate_entity_id, target_entity))
                fanin_score = _clamp(_safe_float(candidate.get("fanin_score")))
                identity_score = _clamp(_safe_float(candidate.get("similarity")))
                motif_score = max(
                    _relay_motif_score(target_entity, target_label, str(candidate.get("dst_node") or "")),
                    _relay_motif_score(current_entity, current_label),
                )
                alert_score = self._alert_signal_score(
                    candidate_entity_id,
                    str(candidate.get("dst_node") or ""),
                    questdb_signals,
                )
                temporal_score = _clamp(global_pressure * 0.7 + alert_score * 0.3)
                rf_signal = self._rf_signal_score(binding, current_entity)
                motion_signal = self._motion_signal_score(motion_histories.get(current_entity_id) or [])
                temporal_profile = self._temporal_edge_profile(
                    entity=current_entity,
                    history_timestamps=binding_histories.get(current_entity_id) or [],
                    binding=binding,
                    binding_timestamp=_binding_timestamp(binding),
                )
                identity_pressure = self._identity_pressure_profile(
                    current_entity=current_entity,
                    target_entity=target_entity,
                    binding=binding,
                    rf_signal=rf_signal,
                    identity_score=identity_score,
                )
                network_score = _clamp(binding_score * 0.72 + fanin_score * 0.16 + motif_score * 0.12)
                if rf_signal["present"]:
                    confidence = round(
                        _clamp(
                            temporal_score * 0.30
                            + network_score * 0.22
                            + identity_score * 0.16
                            + rf_signal["score"] * 0.18
                            + motion_signal["score"] * 0.04
                            + identity_pressure["score"] * 0.10
                        ),
                        4,
                    )
                else:
                    confidence = round(
                        _clamp(
                            binding_score * 0.30
                            + identity_score * 0.22
                            + fanin_score * 0.15
                            + temporal_score * 0.12
                            + motif_score * 0.10
                            + motion_signal["score"] * 0.05
                            + identity_pressure["score"] * 0.06
                        ),
                        4,
                    )
                dissonance = self._cognitive_dissonance(
                    signal_scores={
                        "binding_confidence": binding_score,
                        "network_score": network_score,
                        "identity_similarity": identity_score,
                        "temporal_pressure": temporal_score,
                        "rf_signal": rf_signal["score"] if rf_signal["present"] else None,
                        "motion_signal": motion_signal["score"] if motion_signal["present"] else None,
                        "identity_pressure": identity_pressure["score"],
                    }
                )
                if confidence < FORECAST_MIN_CONFIDENCE:
                    if not (
                        dissonance["zone"] == "COGNITIVE_CONFLICT_ZONE"
                        and max(network_score, identity_score, identity_pressure["score"]) >= 0.52
                    ):
                        continue
                    confidence = round(
                        max(confidence, FORECAST_MIN_CONFIDENCE + min(dissonance["score"] * 0.08, 0.05)),
                        4,
                    )

                uncertainty = self._forecast_uncertainty(
                    confidence=confidence,
                    dissonance_score=dissonance["score"],
                    temporal_phase=temporal_profile["temporal_phase"],
                    temporal_cohesion=temporal_profile["temporal_cohesion"],
                    identity_pressure=identity_pressure["score"],
                )

                time_horizon_s = 6 if confidence >= 0.82 else 15 if confidence >= 0.68 else 30
                candidate_sources = []
                if identity_score > 0.0:
                    candidate_sources.append("identity_stitch")
                if fanin_score > 0.0:
                    candidate_sources.append("fanin_motif")
                if alert_score > 0.0:
                    candidate_sources.append("recent_alert")
                if rf_signal["present"]:
                    candidate_sources.append("rf_signal")
                if motion_signal["present"] and motion_signal["score"] > 0.0:
                    candidate_sources.append("motion_track")
                if identity_pressure["score"] >= 0.6:
                    candidate_sources.append("identity_pressure")
                if dissonance["zone"] == "COGNITIVE_CONFLICT_ZONE":
                    candidate_sources.append("cognitive_dissonance")
                if not candidate_sources:
                    candidate_sources.append("temporal_pressure")
                intent_hypotheses = self._intent_hypotheses(
                    current_entity=current_entity,
                    current_label=current_label,
                    target_entity=target_entity,
                    target_label=target_label,
                    candidate=candidate,
                    binding_score=binding_score,
                    network_score=network_score,
                    fanin_score=fanin_score,
                    motif_score=motif_score,
                    alert_score=alert_score,
                    temporal_score=temporal_score,
                    temporal_profile=temporal_profile,
                    identity_pressure=identity_pressure,
                    rf_signal=rf_signal,
                    dissonance=dissonance,
                )
                top_intent = intent_hypotheses[0] if intent_hypotheses else {}
                behavior_profile = self._path_behavior_profile(
                    temporal_profile=temporal_profile,
                    top_intent=top_intent,
                    motif_score=motif_score,
                    fanin_score=fanin_score,
                    identity_pressure_score=identity_pressure["score"],
                )
                immune = self._countermeasure_profile(
                    current_entity_id=current_entity_id,
                    target_entity_id=candidate_entity_id,
                    candidate_pool=candidate_pool,
                    candidate_sources=candidate_sources,
                    intent_hypotheses=intent_hypotheses,
                    temporal_profile=temporal_profile,
                    identity_pressure_score=identity_pressure["score"],
                    fanin_score=fanin_score,
                    divergence_risk=uncertainty["divergence_risk"],
                    dissonance_zone=dissonance["zone"],
                )
                if immune["requires_multi_node_disruption"]:
                    candidate_sources.append("immune_sim")
                field_view = self._field_view_profile(
                    current_entity=current_entity,
                    temporal_profile=temporal_profile,
                    identity_pressure_score=identity_pressure["score"],
                    top_intent=top_intent,
                    uncertainty=uncertainty,
                    immune=immune,
                    dissonance=dissonance,
                    confidence=confidence,
                )

                prediction_key = f"{current_entity_id}->{candidate_entity_id}"
                motion_forecast = self._motion_forecast(
                    current_entity_id=current_entity_id,
                    current_entity=current_entity,
                    current_label=current_label,
                    rf_signal=rf_signal,
                    history=motion_histories.get(current_entity_id) or [],
                    time_horizon_s=time_horizon_s,
                    confidence=confidence,
                )
                record = PredictionRecord(
                    prediction_id=f"pred-ctrl-{_stable_id(source_binding_id, current_entity_id, candidate_entity_id, time_horizon_s)}",
                    rf_prediction_id=f"pred-rfip-{_stable_id(rf_node_id, candidate_entity_id, source_binding_id)}",
                    kind="CONTROL_PATH_PREDICTED",
                    current_entity_id=current_entity_id,
                    current_label=current_label,
                    target_entity_id=candidate_entity_id,
                    target_label=target_label,
                    sensor_node_id=sensor_node_id,
                    rf_node_id=rf_node_id,
                    source_binding_id=source_binding_id,
                    confidence=confidence,
                    time_horizon_s=time_horizon_s,
                    candidate_source="+".join(candidate_sources),
                    supporting_evidence={
                        "binding_confidence": binding_score,
                        "network_score": round(network_score, 4),
                        "identity_similarity": round(identity_score, 4),
                        "fanin_score": round(fanin_score, 4),
                        "temporal_pressure": round(temporal_score, 4),
                        "alert_score": round(alert_score, 4),
                        "relay_motif_score": round(motif_score, 4),
                        "questdb_edge_rate_eps": questdb_signals.get("edge_rate_eps", 0.0),
                        "questdb_top_talker_count": len(questdb_signals.get("top_talkers", [])),
                        "questdb_dst_node": candidate.get("dst_node"),
                        "questdb_verdict": candidate.get("verdict"),
                        "unique_src_count": candidate.get("unique_src_count"),
                        "ip_entropy": candidate.get("ip_entropy"),
                        "timing_entropy": candidate.get("timing_entropy"),
                        "source_binding_id": source_binding_id,
                        "identity_pressure": identity_pressure["score"],
                        "identity_pressure_components": identity_pressure,
                        "cognitive_dissonance": dissonance,
                        "temporal_overlay": temporal_profile.get("temporal_overlay"),
                        "temporal_fingerprint": temporal_profile.get("temporal_fingerprint"),
                        "temporal_phase": temporal_profile["temporal_phase"],
                        "temporal_cohesion": temporal_profile["temporal_cohesion"],
                        "periodicity_s": temporal_profile["periodicity_s"],
                        "last_seen_delta_s": temporal_profile["last_seen_delta_s"],
                        "periodicity_confidence": temporal_profile.get("periodicity_confidence", 0.0),
                        "dominant_periods": temporal_profile.get("dominant_periods") or [],
                        "harmonics": temporal_profile.get("harmonics") or [],
                        "burst_signature": temporal_profile.get("burst_signature") or "",
                        "temporal_stability": temporal_profile.get("stability", 0.0),
                        "periodicity_source": temporal_profile.get("periodicity_source") or "",
                        "behavior_class": behavior_profile["behavior_class"],
                        "behavior_scores": behavior_profile["behavior_scores"],
                        "entropy": uncertainty["entropy"],
                        "divergence_risk": uncertainty["divergence_risk"],
                        "intent_hypotheses": intent_hypotheses,
                        "top_intent": top_intent,
                        "countermeasure_simulation": immune,
                        "field_view": field_view,
                        "rf": rf_signal["evidence"],
                        "motion": motion_signal["evidence"],
                    },
                    entropy=uncertainty["entropy"],
                    divergence_risk=uncertainty["divergence_risk"],
                    dissonance_score=dissonance["score"],
                    dissonance_zone=dissonance["zone"],
                    identity_pressure=identity_pressure["score"],
                    temporal_phase=temporal_profile["temporal_phase"],
                    temporal_cohesion=temporal_profile["temporal_cohesion"],
                    periodicity_s=temporal_profile["periodicity_s"],
                    last_seen_delta_s=temporal_profile["last_seen_delta_s"],
                    intent_hypotheses=intent_hypotheses,
                    top_intent_label=str(top_intent.get("label") or ""),
                    top_intent_probability=round(_safe_float(top_intent.get("probability"), 0.0), 4),
                    resilience_score=immune["resilience_score"],
                    countermeasure_strategy=immune["recommended_action"],
                    requires_multi_node_disruption=immune["requires_multi_node_disruption"],
                    field_view=field_view,
                    motion_forecast=motion_forecast,
                    temporal_overlay=temporal_profile.get("temporal_overlay"),
                    temporal_fingerprint=temporal_profile.get("temporal_fingerprint"),
                    behavior_class=behavior_profile["behavior_class"],
                    behavior_scores=behavior_profile["behavior_scores"],
                )

                previous = predictions_by_pair.get(prediction_key)
                if previous is None or previous.confidence < record.confidence:
                    predictions_by_pair[prediction_key] = record

        ordered = sorted(
            (record.to_dict() for record in predictions_by_pair.values()),
            key=lambda item: (-item.get("confidence", 0.0), item.get("time_horizon_s", 0), item.get("target_label", "")),
        )[:limit]
        phase_counts = Counter(item.get("temporal_phase") or "unknown" for item in ordered)
        behavior_counts = Counter(item.get("behavior_class") or "unknown" for item in ordered if item.get("behavior_class"))
        conflict_count = sum(1 for item in ordered if item.get("dissonance_zone") == "COGNITIVE_CONFLICT_ZONE")
        avg_identity_pressure = (
            round(sum(_safe_float(item.get("identity_pressure"), 0.0) for item in ordered) / len(ordered), 4)
            if ordered
            else 0.0
        )
        avg_divergence_risk = (
            round(sum(_safe_float(item.get("divergence_risk"), 0.0) for item in ordered) / len(ordered), 4)
            if ordered
            else 0.0
        )
        avg_resilience_score = (
            round(sum(_safe_float(item.get("resilience_score"), 0.0) for item in ordered) / len(ordered), 4)
            if ordered
            else 0.0
        )
        intent_counts = Counter(item.get("top_intent_label") or "unknown" for item in ordered if item.get("top_intent_label"))
        return {
            "status": "ok",
            "signals": {
                "questdb_edge_rate_eps": questdb_signals.get("edge_rate_eps", 0.0),
                "questdb_fanin_events": len(questdb_signals.get("fanin_rows", [])),
                "questdb_recent_alerts": len(questdb_signals.get("recent_alerts", [])),
                "questdb_top_talkers": len(questdb_signals.get("top_talkers", [])),
                "cognitive_conflict_zones": conflict_count,
                "avg_identity_pressure": avg_identity_pressure,
                "avg_divergence_risk": avg_divergence_risk,
                "avg_resilience_score": avg_resilience_score,
                "top_intent_counts": dict(intent_counts),
                "phase_counts": dict(phase_counts),
                "behavior_counts": dict(behavior_counts),
            },
            "predictions": ordered,
        }
