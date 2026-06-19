from __future__ import annotations

import hashlib
import math
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Sequence, Set


TAU_TIME_S = 60.0
TAU_SPACE_M = 50.0
SIM_THRESHOLD = 0.72
MAX_HISTORY_PER_CLUSTER = 100
MAX_GLOBAL_OBS = 50000
MAX_CANDIDATE_CLUSTERS = 500

WEIGHTS = {
    "rf": 0.15,
    "time": 0.20,
    "space": 0.20,
    "proto": 0.30,
    "behavior": 0.15,
}


def now_ts() -> float:
    return time.time()


def _stable_hash(*parts: Any) -> str:
    payload = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def safe_float(value: Any, default: Optional[float] = 0.0) -> Optional[float]:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def normalize_mac(mac: Any) -> str:
    if mac is None:
        return ""
    raw = "".join(ch for ch in str(mac).lower() if ch in "0123456789abcdef")
    if len(raw) != 12:
        return str(mac).strip().lower()
    return ":".join(raw[idx:idx + 2] for idx in range(0, 12, 2))


def is_randomized_mac(mac: Any) -> bool:
    norm = normalize_mac(mac)
    if len(norm) < 2:
        return False
    try:
        first_byte = int(norm.split(":")[0], 16)
    except ValueError:
        return False
    return bool(first_byte & 0b10)


def exp_decay(x: float, tau: float) -> float:
    if tau <= 0:
        return 0.0 if x > 0 else 1.0
    return math.exp(-max(0.0, x) / tau)


def haversine(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    lat1 = safe_float(a.get("lat"), None)
    lon1 = safe_float(a.get("lon"), None)
    lat2 = safe_float(b.get("lat"), None)
    lon2 = safe_float(b.get("lon"), None)
    if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
        return 99999.0

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    radius_m = 6371000.0
    h = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    return 2.0 * radius_m * math.asin(math.sqrt(h))


def jaccard(a: Sequence[str], b: Sequence[str]) -> float:
    if not a or not b:
        return 0.0
    sa = {str(item) for item in a if item not in (None, "")}
    sb = {str(item) for item in b if item not in (None, "")}
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def _token_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple, set, frozenset)):
        return [str(item) for item in value if item not in (None, "")]
    return [str(value)]


def _protocol_tokens(obs: Dict[str, Any]) -> List[str]:
    tokens: List[str] = []
    for key in (
        "ie_fingerprint",
        "protocol_fingerprint",
        "ssid_fingerprint",
        "security",
        "band",
        "channel_width_mhz",
        "vendor_guess",
        "device_class",
        "scan_type",
        "ht_cap",
        "rf_signature",
        "behavior_hash",
    ):
        tokens.extend(_token_list(obs.get(key)))

    channel = obs.get("channel")
    if channel is not None:
        tokens.append(f"channel:{channel}")

    for rate in _token_list(obs.get("rates")):
        tokens.append(f"rate:{rate}")

    return sorted({token for token in tokens if token not in ("", "unknown", "None")})


def rf_similarity(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    drssi = abs(safe_float(a.get("rssi"), -80.0) - safe_float(b.get("rssi"), -80.0))
    channel_match = 1.0 if a.get("channel") == b.get("channel") and a.get("channel") is not None else 0.5
    band_match = 1.0 if a.get("band") == b.get("band") and a.get("band") else 0.85
    return exp_decay(drssi, 10.0) * channel_match * band_match


def temporal_similarity(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    dt = abs(safe_float(a.get("timestamp"), now_ts()) - safe_float(b.get("timestamp"), now_ts()))
    return exp_decay(dt, TAU_TIME_S)


def spatial_similarity(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    return exp_decay(haversine(a, b), TAU_SPACE_M)


def protocol_similarity(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    return jaccard(a.get("protocol_tokens") or [], b.get("protocol_tokens") or [])


def behavior_similarity(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    score = 1.0
    if a.get("behavior_hash") and b.get("behavior_hash"):
        score *= 1.08 if a.get("behavior_hash") == b.get("behavior_hash") else 0.84
    if a.get("scan_type") and b.get("scan_type") and a.get("scan_type") != b.get("scan_type"):
        score *= 0.7
    if a.get("device_class") and b.get("device_class") and a.get("device_class") != b.get("device_class"):
        score *= 0.75
    if a.get("mobility") and b.get("mobility") and a.get("mobility") != b.get("mobility"):
        score *= 0.8

    a_burst = safe_float(a.get("burstiness"), None)
    b_burst = safe_float(b.get("burstiness"), None)
    if a_burst is not None and b_burst is not None:
        score *= exp_decay(abs(a_burst - b_burst), 0.25)

    a_periodic = safe_float(a.get("periodicity_score"), None)
    b_periodic = safe_float(b.get("periodicity_score"), None)
    if a_periodic is not None and b_periodic is not None:
        score *= exp_decay(abs(a_periodic - b_periodic), 0.25)

    a_entropy = safe_float(a.get("entropy_score"), None)
    b_entropy = safe_float(b.get("entropy_score"), None)
    if a_entropy is not None and b_entropy is not None:
        score *= exp_decay(abs(a_entropy - b_entropy), 0.18)

    a_duty = safe_float(a.get("duty_cycle"), None)
    b_duty = safe_float(b.get("duty_cycle"), None)
    if a_duty is not None and b_duty is not None:
        score *= exp_decay(abs(a_duty - b_duty), 0.2)

    return _clamp(score)


def _similarity_weights(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, float]:
    weights = dict(WEIGHTS)
    if bool(a.get("is_randomized_mac")) or bool(b.get("is_randomized_mac")):
        weights["proto"] += 0.08
        weights["time"] += 0.04
        weights["space"] += 0.04

    total = sum(weights.values()) or 1.0
    return {key: value / total for key, value in weights.items()}


def _mac_evidence_factor(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    mac_a = normalize_mac(a.get("mac"))
    mac_b = normalize_mac(b.get("mac"))
    if not mac_a or not mac_b:
        return 1.0

    rand_a = bool(a.get("is_randomized_mac"))
    rand_b = bool(b.get("is_randomized_mac"))
    if mac_a == mac_b:
        return 1.06 if not (rand_a or rand_b) else 1.02
    if not rand_a and not rand_b:
        return 0.92
    if a.get("oui") and a.get("oui") == b.get("oui"):
        return 0.98
    return 1.0


def total_similarity(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    weights = _similarity_weights(a, b)
    score = (
        weights["rf"] * rf_similarity(a, b)
        + weights["time"] * temporal_similarity(a, b)
        + weights["space"] * spatial_similarity(a, b)
        + weights["proto"] * protocol_similarity(a, b)
        + weights["behavior"] * behavior_similarity(a, b)
    )
    return _clamp(score * _mac_evidence_factor(a, b))


@dataclass
class MacCluster:
    first_obs: Dict[str, Any]
    cluster_id: str = ""
    observations: Deque[Dict[str, Any]] = field(default_factory=lambda: deque(maxlen=MAX_HISTORY_PER_CLUSTER))
    assignment_scores: Deque[float] = field(default_factory=lambda: deque(maxlen=32))
    created_at: float = field(default_factory=now_ts)
    updated_at: float = field(default_factory=now_ts)
    randomized_count: int = 0
    total_count: int = 0
    unique_macs: Set[str] = field(default_factory=set)
    vendor_counts: Counter = field(default_factory=Counter)

    def __post_init__(self) -> None:
        self.cluster_id = self.cluster_id or self._cluster_id_for_obs(self.first_obs)
        self.add_observation(self.first_obs, assignment_similarity=1.0)

    @staticmethod
    def _cluster_id_for_obs(obs: Dict[str, Any]) -> str:
        lat = safe_float(obs.get("lat"), None)
        lon = safe_float(obs.get("lon"), None)
        ts_bucket = int(safe_float(obs.get("timestamp"), now_ts()) // 300)
        return "mac-cluster:" + _stable_hash(
            obs.get("identity_seed"),
            obs.get("ssid_fingerprint"),
            obs.get("band"),
            obs.get("channel"),
            obs.get("device_class"),
            f"{lat:.3f}" if lat is not None else "",
            f"{lon:.3f}" if lon is not None else "",
            ts_bucket,
        )

    def add_observation(self, obs: Dict[str, Any], *, assignment_similarity: float) -> None:
        self.observations.append(dict(obs))
        self.updated_at = now_ts()
        self.total_count += 1
        self.assignment_scores.append(_clamp(assignment_similarity))

        mac = normalize_mac(obs.get("mac"))
        if mac:
            self.unique_macs.add(mac)
        if bool(obs.get("is_randomized_mac")):
            self.randomized_count += 1

        vendor = str(obs.get("vendor_guess") or obs.get("vendor_likelihood") or "Unknown")
        if vendor:
            self.vendor_counts[vendor] += 1

    def _recent_observations(self, limit: int = 6) -> List[Dict[str, Any]]:
        return list(self.observations)[-limit:]

    def centroid(self) -> Dict[str, Any]:
        return self.observations[-1] if self.observations else dict(self.first_obs)

    def similarity(self, obs: Dict[str, Any]) -> float:
        recent = self._recent_observations()
        if not recent:
            return 0.0
        scores = [total_similarity(candidate, obs) for candidate in recent]
        best = max(scores)
        avg = sum(scores) / len(scores)
        return _clamp(0.7 * best + 0.3 * avg)

    def confidence(self) -> float:
        """Derived from assignment similarity over time."""
        if not self.assignment_scores:
            return 0.5
        return float(np.mean(self.assignment_scores))

    def stability_score(self) -> float:
        """Spatial stability: inverse of coordinate variance."""
        if len(self.observations) < 2:
            return 1.0
        lats = [o.get("lat", 0) for o in self.observations]
        lons = [o.get("lon", 0) for o in self.observations]
        var = float(np.var(lats) + np.var(lons))
        return 1.0 / (1.0 + 1000.0 * var)

    def behavior_summary(self) -> str:
        """Human-readable behavioral footprint for embedding."""
        c = self.centroid()
        mac_count = len(self.unique_macs)
        vendors = ", ".join([v for v, _ in self.vendor_counts.most_common(2)])
        kind = c.get("device_class") or c.get("scan_type") or "unknown"
        return f"{kind} node with {mac_count} MACs ({vendors}) near {c.get('lat')}, {c.get('lon')}"

    def _temporal_consistency(self) -> float:
        times = [safe_float(obs.get("timestamp"), None) for obs in self.observations]
        times = [value for value in times if value is not None]
        if len(times) < 2:
            return 0.5
        span = max(times) - min(times)
        intervals = [b - a for a, b in zip(times[:-1], times[1:]) if b >= a]
        if not intervals:
            return 0.5
        mean = sum(intervals) / len(intervals)
        if mean <= 0:
            cadence_score = 0.5
        else:
            variance = sum((value - mean) ** 2 for value in intervals) / len(intervals)
            cadence_score = 1.0 - min(math.sqrt(variance) / mean, 1.0)
        return _clamp(0.5 * exp_decay(span, 300.0) + 0.5 * cadence_score)

    def _spatial_consistency(self) -> float:
        observations = list(self.observations)
        if len(observations) < 2:
            return 0.5
        base = self.centroid()
        dists = [haversine(base, obs) for obs in observations[:-1]]
        dists = [dist for dist in dists if dist < 99999.0]
        if not dists:
            return 0.5
        return _clamp(exp_decay(sum(dists) / len(dists), 100.0))

    def _protocol_consistency(self) -> float:
        if not self.observations:
            return 0.0
        fingerprints = [tuple(obs.get("protocol_tokens") or []) for obs in self.observations]
        if not fingerprints:
            return 0.0
        dominant = Counter(fingerprints).most_common(1)[0][1]
        return _clamp(dominant / len(fingerprints))

    def continuity_confidence(self) -> float:
        if not self.assignment_scores:
            return 0.0
        return _clamp(sum(self.assignment_scores) / len(self.assignment_scores))

    def stability_score(self) -> float:
        sample_score = min(1.0, len(self.observations) / 8.0)
        return _clamp(
            0.35 * self._temporal_consistency()
            + 0.25 * self._spatial_consistency()
            + 0.30 * self._protocol_consistency()
            + 0.10 * sample_score
        )

    def confidence(self) -> float:
        raw = math.log1p(len(self.observations)) * self._temporal_consistency() * self._spatial_consistency() * self._protocol_consistency()
        return _clamp(1.0 - math.exp(-raw))

    def vendor_likelihood(self) -> str:
        if not self.vendor_counts:
            return "Unknown"
        for vendor, _ in self.vendor_counts.most_common():
            if vendor != "Unknown":
                return vendor
        return self.vendor_counts.most_common(1)[0][0]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "size": len(self.unique_macs) or max(1, len(self.observations)),
            "observation_count": len(self.observations),
            "confidence": round(self.confidence(), 4),
            "stability_score": round(self.stability_score(), 4),
            "continuity_confidence": round(self.continuity_confidence(), 4),
            "randomized_ratio": round(self.randomized_count / max(1, self.total_count), 4),
            "vendor_likelihood": self.vendor_likelihood(),
            "last_seen": self.updated_at,
        }


class MacClusterEngine:
    def __init__(
        self,
        *,
        sim_threshold: float = SIM_THRESHOLD,
        max_candidate_clusters: int = MAX_CANDIDATE_CLUSTERS,
        max_global_obs: int = MAX_GLOBAL_OBS,
        cognitive_cache: Optional[Any] = None,
    ):
        self.sim_threshold = float(sim_threshold)
        self.max_candidate_clusters = int(max_candidate_clusters)
        self.clusters: Dict[str, MacCluster] = {}
        self.obs_index: Deque[Dict[str, Any]] = deque(maxlen=int(max_global_obs))
        self.cognitive_cache = cognitive_cache
        self._lock = threading.Lock()

    def _normalize_observation(self, obs: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(obs or {})
        out["timestamp"] = safe_float(out.get("timestamp"), now_ts()) or now_ts()
        out["mac"] = normalize_mac(out.get("mac") or out.get("bssid"))
        out["oui"] = (out.get("oui") or out["mac"][:8]).upper() if out["mac"] else ""
        out["rssi"] = safe_float(out.get("rssi"), -80.0)
        channel = safe_float(out.get("channel"), None)
        out["channel"] = int(channel) if channel is not None else None
        out["lat"] = safe_float(out.get("lat"), None)
        out["lon"] = safe_float(out.get("lon"), None)
        out["band"] = out.get("band")
        out["device_class"] = out.get("device_class")
        out["vendor_guess"] = out.get("vendor_guess") or out.get("vendor_likelihood") or "Unknown"
        out["ssid_fingerprint"] = out.get("ssid_fingerprint")
        out["scan_type"] = out.get("scan_type") or "scan"
        out["is_randomized_mac"] = bool(out.get("is_randomized_mac")) or is_randomized_mac(out.get("mac"))
        out["duty_cycle"] = safe_float(out.get("duty_cycle"), None)
        out["entropy_score"] = safe_float(out.get("entropy_score"), None)
        out["behavior_hash"] = out.get("behavior_hash")
        out["protocol_tokens"] = _protocol_tokens(out)
        return out

    def _new_cluster(self, obs: Dict[str, Any]) -> MacCluster:
        cluster = MacCluster(obs)
        if cluster.cluster_id not in self.clusters:
            return cluster

        suffix = 1
        base_id = cluster.cluster_id
        while f"{base_id}:{suffix}" in self.clusters:
            suffix += 1
        cluster.cluster_id = f"{base_id}:{suffix}"
        return cluster

    def ingest(self, obs: Dict[str, Any]) -> Dict[str, Any]:
        normalized = self._normalize_observation(obs)

        with self._lock:
            best_cluster: Optional[MacCluster] = None
            best_score = 0.0
            candidate_clusters = list(self.clusters.values())[-self.max_candidate_clusters:]

            for cluster in candidate_clusters:
                score = cluster.similarity(normalized)
                if score > best_score:
                    best_score = score
                    best_cluster = cluster

            # ── Semantic Recall (HOT Cache Miss) ──
            if (best_cluster is None or best_score < self.sim_threshold) and self.cognitive_cache:
                recalled_clusters = self.cognitive_cache.semantic_recall(normalized)
                if recalled_clusters:
                    # Found something in WARM/COLD!
                    # Promote the first best match
                    best_cluster = recalled_clusters[0]
                    # Add back to active HOT set
                    self.clusters[best_cluster.cluster_id] = best_cluster
                    best_score = best_cluster.similarity(normalized)
                    import logging
                    logging.info(f"[MacCluster] Promoted {best_cluster.cluster_id} from WARM back to HOT (semantic hit)")

            if best_cluster is not None and best_score >= self.sim_threshold:
                best_cluster.add_observation(normalized, assignment_similarity=best_score)
                cluster = best_cluster
                assigned = True
            else:
                cluster = self._new_cluster(normalized)
                self.clusters[cluster.cluster_id] = cluster
                assigned = False
                best_score = 0.0

            self.obs_index.append(normalized)

            return {
                "cluster_id": cluster.cluster_id,
                "similarity": round(best_score, 4),
                "assigned": assigned,
                "cluster": cluster.to_dict(),
            }

    def get_clusters(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [cluster.to_dict() for cluster in self.clusters.values()]

    def prune(self, max_age_sec: float = 1800.0) -> None:
        cutoff = now_ts() - float(max_age_sec)
        with self._lock:
            stale = [cluster_id for cluster_id, cluster in self.clusters.items() if cluster.updated_at < cutoff]
            for cluster_id in stale:
                del self.clusters[cluster_id]


__all__ = [
    "MacCluster",
    "MacClusterEngine",
    "SIM_THRESHOLD",
    "TAU_SPACE_M",
    "TAU_TIME_S",
    "behavior_similarity",
    "is_randomized_mac",
    "protocol_similarity",
    "rf_similarity",
    "spatial_similarity",
    "temporal_similarity",
    "total_similarity",
]
