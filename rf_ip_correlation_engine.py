from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
import threading
import time
from typing import Any, Deque, Dict, List, Optional, Tuple


def _coalesce(*vals: Any) -> Any:
    for val in vals:
        if val is not None and val != "":
            return val
    return None


def _stable_hash(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _safe_float(val: Any) -> Optional[float]:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except Exception:
        return None


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(d_lon / 2) ** 2
    )
    return 2 * radius_km * math.asin(math.sqrt(a))


@dataclass
class RFObservation:
    observation_id: str
    timestamp: float
    sensor_id: str
    rf_node_id: str
    frequency_mhz: Optional[float] = None
    bandwidth_mhz: Optional[float] = None
    power_dbm: Optional[float] = None
    modulation: Optional[str] = None
    burst_period_ms: Optional[float] = None
    entropy_score: Optional[float] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    alt_m: float = 0.0
    mission_id: Optional[str] = None
    labels: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class NetworkObservation:
    observation_id: str
    timestamp: float
    entity_id: str
    src_ip: Optional[str] = None
    dst_ip: Optional[str] = None
    protocol: Optional[str] = None
    ja3: Optional[str] = None
    inter_arrival_ms: Optional[float] = None
    packet_rate: Optional[float] = None
    flow_bytes: Optional[float] = None
    entropy_score: Optional[float] = None
    ip_transition_count: int = 0
    lat: Optional[float] = None
    lon: Optional[float] = None
    mission_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CorrelationBinding:
    binding_id: str
    created_at: float
    rf_observation_id: str
    network_observation_id: str
    sensor_id: str
    rf_node_id: str
    recon_entity_id: str
    confidence: float
    time_delta_ms: float
    score_components: Dict[str, float] = field(default_factory=dict)
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class RFIPCorrelationEngine:
    def __init__(
        self,
        *,
        correlation_window_s: float = 5.0,
        spatial_window_km: float = 5.0,
        binding_threshold: float = 0.78,
        max_history: int = 512,
    ) -> None:
        self.correlation_window_s = correlation_window_s
        self.spatial_window_km = spatial_window_km
        self.binding_threshold = binding_threshold
        self._rf_events: Deque[RFObservation] = deque(maxlen=max_history)
        self._network_events: Deque[NetworkObservation] = deque(maxlen=max_history)
        self._bindings: Deque[CorrelationBinding] = deque(maxlen=max_history)
        self._binding_index: Dict[Tuple[str, str], CorrelationBinding] = {}
        self._lock = threading.RLock()

    def observe_rf(self, payload: Dict[str, Any]) -> Tuple[RFObservation, List[CorrelationBinding]]:
        obs = self._normalize_rf(payload)
        with self._lock:
            self._rf_events.append(obs)
            self._prune_locked(obs.timestamp)
            bindings = self._correlate_locked(obs, list(self._network_events))
        return obs, bindings

    def observe_network(self, payload: Dict[str, Any]) -> Tuple[NetworkObservation, List[CorrelationBinding]]:
        obs = self._normalize_network(payload)
        with self._lock:
            self._network_events.append(obs)
            self._prune_locked(obs.timestamp)
            bindings = self._correlate_locked(obs, list(self._rf_events))
        return obs, bindings

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "rf_window_depth": len(self._rf_events),
                "network_window_depth": len(self._network_events),
                "binding_count": len(self._bindings),
                "correlation_window_s": self.correlation_window_s,
                "spatial_window_km": self.spatial_window_km,
                "binding_threshold": self.binding_threshold,
                "recent_bindings": [b.to_dict() for b in list(self._bindings)[-10:]],
            }

    def recent_bindings(self, limit: int = 25) -> List[Dict[str, Any]]:
        with self._lock:
            return [b.to_dict() for b in list(self._bindings)[-max(1, limit):]]

    def get_rf_observation(self, observation_id: str) -> Optional[RFObservation]:
        with self._lock:
            for obs in self._rf_events:
                if obs.observation_id == observation_id:
                    return obs
        return None

    def get_network_observation(self, observation_id: str) -> Optional[NetworkObservation]:
        with self._lock:
            for obs in self._network_events:
                if obs.observation_id == observation_id:
                    return obs
        return None

    def _prune_locked(self, now: float) -> None:
        cutoff = now - (self.correlation_window_s * 4.0)
        while self._rf_events and self._rf_events[0].timestamp < cutoff:
            self._rf_events.popleft()
        while self._network_events and self._network_events[0].timestamp < cutoff:
            self._network_events.popleft()

        stale_pairs = [
            key for key, binding in self._binding_index.items()
            if binding.created_at < cutoff
        ]
        for key in stale_pairs:
            self._binding_index.pop(key, None)

    def _correlate_locked(
        self,
        obs: Any,
        candidates: List[Any],
    ) -> List[CorrelationBinding]:
        bindings: List[CorrelationBinding] = []
        for candidate in candidates:
            if isinstance(obs, RFObservation) and isinstance(candidate, NetworkObservation):
                rf_obs, net_obs = obs, candidate
            elif isinstance(obs, NetworkObservation) and isinstance(candidate, RFObservation):
                rf_obs, net_obs = candidate, obs
            else:
                continue

            if abs(rf_obs.timestamp - net_obs.timestamp) > self.correlation_window_s:
                continue

            score, components = self._score_pair(rf_obs, net_obs)
            if score < self.binding_threshold:
                continue

            binding = self._register_binding_locked(rf_obs, net_obs, score, components)
            if binding:
                bindings.append(binding)
        return bindings

    def _register_binding_locked(
        self,
        rf_obs: RFObservation,
        net_obs: NetworkObservation,
        score: float,
        components: Dict[str, float],
    ) -> Optional[CorrelationBinding]:
        pair_key = (rf_obs.rf_node_id, net_obs.entity_id)
        current = self._binding_index.get(pair_key)
        now = max(rf_obs.timestamp, net_obs.timestamp)
        if current and (now - current.created_at) < self.correlation_window_s and score <= current.confidence + 0.02:
            return None

        binding = CorrelationBinding(
            binding_id=f"rfip:{_stable_hash({'rf': rf_obs.rf_node_id, 'recon': net_obs.entity_id, 't': int(now * 1000)})}",
            created_at=now,
            rf_observation_id=rf_obs.observation_id,
            network_observation_id=net_obs.observation_id,
            sensor_id=rf_obs.sensor_id,
            rf_node_id=rf_obs.rf_node_id,
            recon_entity_id=net_obs.entity_id,
            confidence=round(score, 4),
            time_delta_ms=round(abs(rf_obs.timestamp - net_obs.timestamp) * 1000.0, 2),
            score_components=components,
            evidence={
                "rf_frequency_mhz": rf_obs.frequency_mhz,
                "rf_modulation": rf_obs.modulation,
                "network_src_ip": net_obs.src_ip,
                "network_dst_ip": net_obs.dst_ip,
                "network_ja3": net_obs.ja3,
            },
        )
        self._bindings.append(binding)
        self._binding_index[pair_key] = binding
        return binding

    def _score_pair(
        self,
        rf_obs: RFObservation,
        net_obs: NetworkObservation,
    ) -> Tuple[float, Dict[str, float]]:
        components: Dict[str, float] = {}
        weights: Dict[str, float] = {}

        dt_ms = abs(rf_obs.timestamp - net_obs.timestamp) * 1000.0
        time_score = max(0.0, 1.0 - (dt_ms / (self.correlation_window_s * 1000.0)))
        components["time_alignment"] = round(time_score, 4)
        weights["time_alignment"] = 0.45

        if rf_obs.burst_period_ms is not None and net_obs.inter_arrival_ms is not None:
            denom = max(rf_obs.burst_period_ms, net_obs.inter_arrival_ms, 1.0)
            periodicity = max(0.0, 1.0 - (abs(rf_obs.burst_period_ms - net_obs.inter_arrival_ms) / denom))
            components["periodicity_overlap"] = round(periodicity, 4)
            weights["periodicity_overlap"] = 0.25

        if rf_obs.entropy_score is not None and net_obs.entropy_score is not None:
            entropy = max(0.0, 1.0 - min(abs(rf_obs.entropy_score - net_obs.entropy_score), 1.0))
            components["entropy_match"] = round(entropy, 4)
            weights["entropy_match"] = 0.10

        if None not in (rf_obs.lat, rf_obs.lon, net_obs.lat, net_obs.lon):
            dist_km = _haversine_km(rf_obs.lat, rf_obs.lon, net_obs.lat, net_obs.lon)
            spatial = max(0.0, 1.0 - min(dist_km / max(self.spatial_window_km, 0.1), 1.0))
            components["spatial_anchor"] = round(spatial, 4)
            weights["spatial_anchor"] = 0.20
            components["distance_km"] = round(dist_km, 4)

        if net_obs.ja3 and net_obs.ip_transition_count >= 3:
            components["identity_persistence"] = 1.0
            weights["identity_persistence"] = 0.15

        weighted_total = sum(weights.values()) or 1.0
        score = sum(components[name] * weight for name, weight in weights.items() if name in components) / weighted_total
        components["score"] = round(score, 4)
        return score, components

    def _normalize_rf(self, payload: Dict[str, Any]) -> RFObservation:
        payload = dict(payload or {})
        timestamp = _safe_float(payload.get("timestamp")) or time.time()
        location = payload.get("location") or {}
        sensor_id = str(_coalesce(payload.get("sensor_id"), payload.get("sensorId"), payload.get("observer_id"), "rf-sensor"))
        fingerprint = str(
            _coalesce(
                payload.get("rf_fingerprint"),
                payload.get("fingerprint"),
                payload.get("rf_signature"),
                _stable_hash({
                    "sensor_id": sensor_id,
                    "frequency_mhz": payload.get("frequency_mhz"),
                    "modulation": payload.get("modulation"),
                    "timestamp_bucket": int(timestamp * 10),
                }),
            )
        )
        rf_node_id = str(_coalesce(payload.get("rf_node_id"), payload.get("rfNodeId"), f"rf:{sensor_id}:{fingerprint[:12]}"))
        observation_id = str(_coalesce(payload.get("observation_id"), payload.get("id"), f"rfobs:{_stable_hash({'rf_node_id': rf_node_id, 'timestamp': timestamp})}"))
        return RFObservation(
            observation_id=observation_id,
            timestamp=timestamp,
            sensor_id=sensor_id,
            rf_node_id=rf_node_id,
            frequency_mhz=_safe_float(_coalesce(payload.get("frequency_mhz"), payload.get("frequency"))),
            bandwidth_mhz=_safe_float(_coalesce(payload.get("bandwidth_mhz"), payload.get("bandwidth"))),
            power_dbm=_safe_float(_coalesce(payload.get("power_dbm"), payload.get("power"))),
            modulation=_coalesce(payload.get("modulation"), payload.get("waveform")),
            burst_period_ms=_safe_float(_coalesce(payload.get("burst_period_ms"), payload.get("periodicity_ms"), payload.get("hop_period_ms"))),
            entropy_score=_safe_float(payload.get("entropy_score")),
            lat=_safe_float(_coalesce(payload.get("lat"), location.get("lat"), (payload.get("sensor_context") or {}).get("lat"))),
            lon=_safe_float(_coalesce(payload.get("lon"), payload.get("lng"), location.get("lon"), location.get("lng"), (payload.get("sensor_context") or {}).get("lon"))),
            alt_m=_safe_float(_coalesce(payload.get("alt_m"), payload.get("alt"), location.get("alt_m"), location.get("alt"))) or 0.0,
            mission_id=_coalesce(payload.get("mission_id"), payload.get("missionId")),
            labels=dict(payload.get("labels") or {}),
            metadata={k: v for k, v in payload.items() if k not in {
                "observation_id", "id", "timestamp", "sensor_id", "sensorId", "observer_id",
                "rf_node_id", "rfNodeId", "frequency_mhz", "frequency", "bandwidth_mhz", "bandwidth",
                "power_dbm", "power", "modulation", "waveform", "burst_period_ms", "periodicity_ms",
                "hop_period_ms", "entropy_score", "lat", "lon", "lng", "alt_m", "alt", "location",
                "labels", "mission_id", "missionId",
            }},
        )

    def _normalize_network(self, payload: Dict[str, Any]) -> NetworkObservation:
        payload = dict(payload or {})
        timestamp = _safe_float(payload.get("timestamp")) or time.time()
        sensor_context = payload.get("sensor_context") or {}
        location = payload.get("location") or sensor_context
        ip_anchor = _coalesce(payload.get("src_ip"), payload.get("src"), payload.get("dst_ip"), payload.get("dst"))
        ip_entity_id = None
        if ip_anchor:
            ip_entity_id = "IP-" + str(ip_anchor).replace(".", "_").replace(":", "_")
        entity_id = str(
            _coalesce(
                payload.get("entity_id"),
                payload.get("recon_entity_id"),
                payload.get("reconEntityId"),
                payload.get("id"),
                payload.get("observer_id"),
                ip_entity_id,
                f"net-{_stable_hash(payload)}",
            )
        )
        observation_id = str(_coalesce(payload.get("observation_id"), payload.get("id"), f"netobs:{_stable_hash({'entity_id': entity_id, 'timestamp': timestamp, 'src_ip': payload.get('src_ip')})}"))
        return NetworkObservation(
            observation_id=observation_id,
            timestamp=timestamp,
            entity_id=entity_id,
            src_ip=_coalesce(payload.get("src_ip"), payload.get("src")),
            dst_ip=_coalesce(payload.get("dst_ip"), payload.get("dst")),
            protocol=_coalesce(payload.get("protocol"), payload.get("proto"), payload.get("observed_kind")),
            ja3=_coalesce(payload.get("ja3"), payload.get("ja3_hash"), payload.get("tls_ja3")),
            inter_arrival_ms=_safe_float(_coalesce(payload.get("inter_arrival_ms"), payload.get("inter_arrival"), payload.get("periodicity_ms"))),
            packet_rate=_safe_float(payload.get("packet_rate")),
            flow_bytes=_safe_float(_coalesce(payload.get("flow_bytes"), payload.get("bytes"), payload.get("bytes_total"))),
            entropy_score=_safe_float(payload.get("entropy_score")),
            ip_transition_count=int(_safe_float(payload.get("ip_transition_count")) or 0),
            lat=_safe_float(_coalesce(payload.get("lat"), location.get("lat"))),
            lon=_safe_float(_coalesce(payload.get("lon"), payload.get("lng"), location.get("lon"), location.get("lng"))),
            mission_id=_coalesce(payload.get("mission_id"), payload.get("missionId")),
            metadata={k: v for k, v in payload.items() if k not in {
                "observation_id", "id", "timestamp", "entity_id", "recon_entity_id", "reconEntityId",
                "src_ip", "src", "dst_ip", "dst", "protocol", "proto", "observed_kind", "ja3", "ja3_hash",
                "tls_ja3", "inter_arrival_ms", "inter_arrival", "periodicity_ms", "packet_rate", "flow_bytes",
                "bytes", "bytes_total", "entropy_score", "ip_transition_count", "lat", "lon", "lng",
                "location", "sensor_context", "mission_id", "missionId",
            }},
        )
