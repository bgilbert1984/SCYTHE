from __future__ import annotations

import hashlib
import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

from mac_cluster_engine import MacClusterEngine


_VENDOR_BY_OUI = {
    "00:11:22": "Cisco",
    "00:16:3E": "XenSource",
    "00:1A:11": "Google",
    "00:50:56": "VMware",
    "02:42:AC": "Docker",
    "18:B4:30": "Google",
    "20:4E:7F": "TP-Link",
    "24:A4:3C": "Ubiquiti",
    "28:CF:E9": "Apple",
    "34:36:3B": "Samsung",
    "38:AA:3C": "Samsung",
    "40:4E:36": "Samsung",
    "50:C7:BF": "TP-Link",
    "68:7F:74": "Netgear",
    "74:AC:B9": "Ubiquiti",
    "84:16:F9": "Cisco",
    "98:DA:C4": "TP-Link",
    "9C:9D:7E": "Aruba",
    "A0:40:A0": "Netgear",
    "AC:84:C6": "Ubiquiti",
    "AC:DE:48": "Apple",
    "B8:27:EB": "Raspberry Pi",
    "C4:04:15": "Netgear",
    "D8:3A:DD": "Google",
    "DC:A6:32": "Ubiquiti",
    "E4:95:6E": "Espressif",
    "F4:F5:D8": "Cisco",
}

_INFRA_VENDORS = {
    "Aruba",
    "Cisco",
    "Netgear",
    "TP-Link",
    "Ubiquiti",
}

_MOBILE_PATTERNS = (
    "androidap",
    "galaxy",
    "hotspot",
    "iphone",
    "iphone's",
    "pixel",
    "samsung",
    "tether",
)

_SESSION_TIMEOUT_S = 30.0


def _stable_hash(*parts: Any) -> str:
    payload = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _as_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def normalize_mac(mac: Any) -> str:
    if mac is None:
        return ""
    raw = "".join(ch for ch in str(mac).lower() if ch in "0123456789abcdef")
    if len(raw) != 12:
        return str(mac).strip().lower()
    return ":".join(raw[idx:idx + 2] for idx in range(0, 12, 2))


def lookup_vendor(mac: Any) -> str:
    norm = normalize_mac(mac)
    if len(norm) < 8:
        return "Unknown"
    return _VENDOR_BY_OUI.get(norm[:8].upper(), "Unknown")


def is_randomized_mac(mac: Any) -> bool:
    norm = normalize_mac(mac)
    if len(norm) < 2:
        return False
    try:
        first_byte = int(norm.split(":")[0], 16)
    except Exception:
        return False
    return bool(first_byte & 0b10)


def wifi_band_label(freq_mhz: Any) -> Optional[str]:
    freq = _as_float(freq_mhz)
    if freq is None:
        return None
    if 2400 <= freq <= 2500:
        return "2.4GHz"
    if 4900 <= freq <= 5900:
        return "5GHz"
    if 5925 <= freq <= 7125:
        return "6GHz"
    return None


def wifi_channel(freq_mhz: Any) -> Optional[int]:
    freq = _as_float(freq_mhz)
    if freq is None:
        return None
    if 2412 <= freq <= 2484:
        if int(freq) == 2484:
            return 14
        return int(round((freq - 2407) / 5.0))
    if 5000 <= freq <= 5900:
        return int(round((freq - 5000) / 5.0))
    if 5955 <= freq <= 7115:
        return int(round((freq - 5950) / 5.0))
    return None


def channel_width_mhz(raw_width: Any) -> Optional[float]:
    width = _as_float(raw_width)
    if width is None:
        return None
    mapped = {
        0.0: 20.0,
        1.0: 40.0,
        2.0: 80.0,
        3.0: 160.0,
        4.0: 160.0,
    }
    return mapped.get(width, width)


def _ssid_fingerprint(ssid: Any) -> str:
    raw = str(ssid or "").strip()
    if not raw or raw == "[hidden]":
        return "hidden"
    return f"ssid:{_stable_hash(raw.lower())}"


def _classify_device_class(ssid: Any, vendor: str, randomized: bool) -> str:
    ssid_text = str(ssid or "").strip().lower()
    if any(token in ssid_text for token in _MOBILE_PATTERNS):
        return "mobile_hotspot"
    if vendor in _INFRA_VENDORS:
        return "access_point"
    if randomized:
        return "wireless_device"
    return "access_point"


def _ontology_for_class(device_class: str) -> str:
    if device_class == "mobile_hotspot":
        return "network.wifi.mobile_hotspot"
    if device_class == "wireless_device":
        return "network.wifi.device"
    return "network.wifi.access_point"


def _friendly_name(vendor: str, device_class: str, ssid: Any) -> str:
    vendor_label = vendor if vendor != "Unknown" else "Unknown WiFi"
    ssid_label = str(ssid or "").strip() or "[hidden]"
    if ssid_label == "[hidden]":
        ssid_label = "hidden"
    if device_class == "mobile_hotspot":
        kind_label = "Hotspot"
    elif device_class == "wireless_device":
        kind_label = "Device"
    else:
        kind_label = "AP"
    return f"{vendor_label} {kind_label} {ssid_label}"


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_m = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    return 2.0 * radius_m * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    x = math.sin(dlambda) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def _estimate_proxy_radius_m(rssi_dbm: Optional[float], accuracy_m: Optional[float]) -> float:
    if accuracy_m is not None and accuracy_m > 0:
        base = accuracy_m
    else:
        base = 12.0
        if rssi_dbm is None:
            base = 25.0
        elif rssi_dbm <= -80:
            base = 60.0
        elif rssi_dbm <= -70:
            base = 40.0
        elif rssi_dbm <= -60:
            base = 25.0
        else:
            base = 15.0
    return max(8.0, float(base))


def _burstiness(intervals: Deque[float]) -> float:
    if len(intervals) < 2:
        return 0.0
    mean = sum(intervals) / len(intervals)
    if mean <= 0:
        return 0.0
    variance = sum((value - mean) ** 2 for value in intervals) / len(intervals)
    coeff = math.sqrt(variance) / mean
    return _clamp(coeff / 2.0)


def _periodicity_score(intervals: Deque[float]) -> float:
    if len(intervals) < 2:
        return 0.0
    mean = sum(intervals) / len(intervals)
    if mean <= 0:
        return 0.0
    variance = sum((value - mean) ** 2 for value in intervals) / len(intervals)
    coeff = math.sqrt(variance) / mean
    return _clamp(1.0 - min(coeff, 1.0))


def _pattern_label(*, periodicity_score: float, burstiness: float, mobility: str) -> str:
    if periodicity_score >= 0.82 and burstiness <= 0.25:
        return "burst-beacon"
    if mobility == "mobile":
        return "mobile-drift"
    if burstiness >= 0.55:
        return "bursty"
    return "steady-observation"


def _bucket_label(value: float, *, low: float, high: float) -> str:
    if value <= low:
        return "low"
    if value >= high:
        return "high"
    return "medium"


def _timing_entropy_score(
    *,
    burstiness: float,
    periodicity_score: float,
    duty_cycle: float,
    handoff_count: int,
) -> float:
    return _clamp(
        0.52 * burstiness
        + 0.34 * (1.0 - periodicity_score)
        + 0.08 * abs(duty_cycle - 0.5) * 2.0
        + 0.06 * min(float(handoff_count), 3.0) / 3.0
    )


def _behavior_hash(
    *,
    burstiness: float,
    periodicity_score: float,
    mobility: str,
    scan_type: Any,
    entropy_score: float,
) -> str:
    return f"behavior:{_stable_hash(
        _bucket_label(burstiness, low=0.22, high=0.58),
        _bucket_label(periodicity_score, low=0.32, high=0.78),
        _bucket_label(entropy_score, low=0.28, high=0.62),
        mobility,
        str(scan_type or 'scan').lower(),
    )}"


def _destination_point(lat: float, lon: float, heading_deg: float, distance_m: float) -> Dict[str, float]:
    radius_m = 6371000.0
    angular_distance = max(0.0, distance_m) / radius_m
    bearing = math.radians(heading_deg)
    lat1 = math.radians(lat)
    lon1 = math.radians(lon)

    lat2 = math.asin(
        math.sin(lat1) * math.cos(angular_distance)
        + math.cos(lat1) * math.sin(angular_distance) * math.cos(bearing)
    )
    lon2 = lon1 + math.atan2(
        math.sin(bearing) * math.sin(angular_distance) * math.cos(lat1),
        math.cos(angular_distance) - math.sin(lat1) * math.sin(lat2),
    )
    return {
        "lat": math.degrees(lat2),
        "lon": ((math.degrees(lon2) + 540.0) % 360.0) - 180.0,
    }


def _heading_cardinal(heading_deg: Optional[float]) -> Optional[str]:
    if heading_deg is None:
        return None
    directions = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
    idx = int(((heading_deg % 360.0) + 22.5) // 45.0) % len(directions)
    return directions[idx]


def _vendor_likelihood(vendor: str, *, randomized: bool, device_class: str) -> str:
    if vendor != "Unknown":
        return vendor
    if randomized and device_class == "mobile_hotspot":
        return "Android/iOS randomized"
    if randomized:
        return "Randomized wireless device"
    return "Unknown"


def _behavior_classification(
    *,
    device_class: str,
    mobility: str,
    burstiness: float,
    periodicity_score: float,
    persistence_score: float,
    randomized: bool,
    duty_cycle: float,
    handoff_count: int,
    scan_type: Any,
    rf_pattern: str,
    motion: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    tags: List[str] = []
    explanation: List[str] = []
    motion = dict(motion or {})
    drift_class = str(motion.get("drift_class") or mobility)
    entropy_score = _timing_entropy_score(
        burstiness=burstiness,
        periodicity_score=periodicity_score,
        duty_cycle=duty_cycle,
        handoff_count=handoff_count,
    )
    behavior_hash = _behavior_hash(
        burstiness=burstiness,
        periodicity_score=periodicity_score,
        mobility=mobility,
        scan_type=scan_type,
        entropy_score=entropy_score,
    )
    relay_likelihood = _clamp(
        0.3 * min(float(handoff_count), 3.0) / 3.0
        + 0.25 * (1.0 if device_class == "mobile_hotspot" else 0.2)
        + 0.25 * min(_as_float(motion.get("velocity_mps")) or 0.0, 8.0) / 8.0
        + 0.2 * (1.0 - entropy_score)
    )

    if periodicity_score >= 0.82 and burstiness <= 0.25:
        classification = "BEACON"
        confidence = _clamp(0.42 + 0.33 * periodicity_score + 0.15 * (1.0 - burstiness) + 0.1 * (1.0 - entropy_score))
        tags.extend(["BEACON", "LOW_ENTROPY"])
        explanation.append("Strong periodic cadence with low burstiness and compressed timing entropy")
    elif device_class == "mobile_hotspot" and (mobility != "static" or handoff_count > 0):
        classification = "POSSIBLE_RELAY"
        confidence = _clamp(0.33 + 0.22 * persistence_score + 0.15 * (1.0 if randomized else 0.55) + 0.3 * relay_likelihood)
        tags.extend(["MOBILE_HOTSPOT", "POSSIBLE_RELAY", "IDENTITY_PRESSURE"])
        explanation.append("Mobile-hotspot fingerprint with continuity under movement or handoff pressure")
    elif device_class == "access_point" and mobility == "static" and persistence_score >= 0.55 and entropy_score <= 0.55:
        classification = "INFRASTRUCTURE"
        confidence = _clamp(0.42 + 0.28 * persistence_score + 0.14 * periodicity_score + 0.1 * (1.0 - entropy_score))
        tags.extend(["INFRASTRUCTURE", "STABLE_PRESENCE"])
        explanation.append("Stable access-point signature with persistent presence and low timing entropy")
    elif entropy_score >= 0.58 and periodicity_score <= 0.45 and duty_cycle >= 0.12 and duty_cycle <= 0.88:
        classification = "HUMAN_DRIVEN"
        confidence = _clamp(0.31 + 0.2 * entropy_score + 0.2 * persistence_score + 0.12 * burstiness)
        tags.extend(["HUMAN_DRIVEN", "HIGH_ENTROPY"])
        explanation.append("Irregular cadence and broad duty cycle look more human than automated")
    elif mobility == "mobile":
        classification = "MOBILE_DEVICE"
        confidence = _clamp(0.28 + 0.24 * persistence_score + 0.18 * burstiness + 0.12 * min(_as_float(motion.get("velocity_mps")) or 0.0, 8.0) / 8.0)
        tags.extend(["MOBILE", drift_class.upper().replace("-", "_")])
        explanation.append("Observed drift and motion continuity suggest a moving wireless actor")
    else:
        classification = "INTERMITTENT_DEVICE"
        confidence = _clamp(0.23 + 0.18 * persistence_score + 0.12 * max(burstiness, periodicity_score) + 0.1 * entropy_score)
        tags.append("INTERMITTENT")
        explanation.append("Insufficient stability for stronger behavioral attribution")

    if randomized:
        tags.append("RANDOMIZED_MAC")
    if rf_pattern == "burst-beacon":
        tags.append("BURST_BEACON")
    if handoff_count > 0:
        tags.append("HANDOFF_ACTIVITY")

    return {
        "classification": classification,
        "confidence": confidence,
        "tags": sorted(set(tags)),
        "entropy_score": entropy_score,
        "behavior_hash": behavior_hash,
        "behavior_signature": {
            "burstiness_bucket": _bucket_label(burstiness, low=0.22, high=0.58),
            "periodicity_bucket": _bucket_label(periodicity_score, low=0.32, high=0.78),
            "entropy_bucket": _bucket_label(entropy_score, low=0.28, high=0.62),
            "mobility_class": mobility,
            "scan_type": str(scan_type or "scan").lower(),
            "rf_pattern": rf_pattern,
        },
        "relay_likelihood": relay_likelihood,
        "explanation": "; ".join(explanation),
    }


def _cognition_node(node_id: str, kind: str, *, labels: Optional[Dict[str, Any]] = None, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "id": node_id,
        "kind": kind,
        "labels": dict(labels or {}),
        "metadata": dict(metadata or {}),
    }


def _cognition_edge(
    source_id: str,
    relation_kind: str,
    target_id: str,
    *,
    weight: float = 1.0,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    edge_id = f"{relation_kind.lower()}:{_stable_hash(source_id, relation_kind, target_id)}"
    return {
        "id": edge_id,
        "kind": relation_kind,
        "nodes": [source_id, target_id],
        "weight": float(weight),
        "metadata": dict(metadata or {}),
    }


def build_cognition_graph_records(
    entity_id: str,
    metadata: Optional[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    cognition = dict((metadata or {}).get("cognition") or {})
    nodes = [dict(node) for node in cognition.get("nodes") or []]
    edges = [dict(edge) for edge in cognition.get("edges") or []]
    if entity_id:
        for edge in edges:
            edge_nodes = list(edge.get("nodes") or [])
            if len(edge_nodes) == 2 and not edge_nodes[0]:
                edge["nodes"] = [entity_id, edge_nodes[1]]
    return {"nodes": nodes, "edges": edges}


@dataclass
class _WiFiAliasState:
    alias_device_id: str
    first_seen: float
    last_seen: float
    seen_count: int = 0
    session_counter: int = 0
    session_id: str = ""
    session_started_at: float = 0.0
    session_seen_count: int = 0
    rssi_sum: float = 0.0
    rssi_samples: int = 0
    recent_intervals: Deque[float] = field(default_factory=lambda: deque(maxlen=8))
    recent_positions: Deque[Tuple[float, float]] = field(default_factory=lambda: deque(maxlen=8))
    max_drift_m: float = 0.0
    session_recent_intervals: Deque[float] = field(default_factory=lambda: deque(maxlen=8))
    session_positions: Deque[Tuple[float, float]] = field(default_factory=lambda: deque(maxlen=12))
    session_track: Deque[Tuple[float, float, float]] = field(default_factory=lambda: deque(maxlen=12))
    session_max_drift_m: float = 0.0
    raw_macs: Set[str] = field(default_factory=set)
    ssids_seen: Set[str] = field(default_factory=set)
    session_raw_macs: Set[str] = field(default_factory=set)
    session_ssids_seen: Set[str] = field(default_factory=set)


class WiFiObservationEnricher:
    def __init__(self, session_timeout_s: float = _SESSION_TIMEOUT_S):
        self.session_timeout_s = float(session_timeout_s)
        self._lock = threading.Lock()
        self._states: Dict[str, _WiFiAliasState] = {}

        # ── Cognitive Cache Engineering ──
        from cognitive_cache_engine import CognitiveCacheEngine
        self.cluster_engine = MacClusterEngine()
        self.cognitive_cache = CognitiveCacheEngine(self.cluster_engine)
        self.cluster_engine.cognitive_cache = self.cognitive_cache
        self.cognitive_cache.start()

    def enrich_rf_node(
        self,
        node_id: str,
        node_data: Dict[str, Any],
        *,
        metadata: Optional[Dict[str, Any]] = None,
        position: Optional[Any] = None,
    ) -> Dict[str, Any]:
        meta = dict(metadata or node_data.get("metadata") or {})
        node_type = str(node_data.get("type") or meta.get("type") or "").lower()
        if node_type != "wifi_ap" and not meta.get("bssid") and not str(node_id).startswith("wifi-"):
            return {
                "node_id": node_id,
                "metadata": meta,
                "labels": dict(node_data.get("labels") or {}),
                "display_name": meta.get("name") or node_id,
                "kind": node_type or "rf",
            }

        ts = _as_float(node_data.get("timestamp")) or _as_float(meta.get("timestamp")) or time.time()
        bssid = normalize_mac(meta.get("bssid") or node_data.get("bssid") or node_id.replace("wifi-", ""))
        ssid = meta.get("ssid") or node_data.get("ssid") or "[hidden]"
        vendor = lookup_vendor(bssid)
        randomized = is_randomized_mac(bssid)
        frequency_mhz = _as_float(meta.get("frequency_mhz") or node_data.get("frequency_mhz") or node_data.get("frequency"))
        channel = wifi_channel(frequency_mhz)
        band = wifi_band_label(frequency_mhz)
        width_mhz = channel_width_mhz(meta.get("channel_width") or node_data.get("channel_width"))
        rssi = _as_float(meta.get("rssi") or node_data.get("rssi") or node_data.get("power"))
        accuracy_m = _as_float(meta.get("accuracy_m") or node_data.get("accuracy_m"))
        device_class = _classify_device_class(ssid, vendor, randomized)
        ontology = _ontology_for_class(device_class)
        identity_seed_id = self._alias_device_id(
            bssid=bssid,
            ssid=ssid,
            band=band,
            channel=channel,
            width_mhz=width_mhz,
            vendor=vendor,
            device_class=device_class,
            randomized=randomized,
        )
        cluster_assignment = self.cluster_engine.ingest(
            self._build_cluster_observation(
                node_id=node_id,
                timestamp=ts,
                bssid=bssid,
                ssid=ssid,
                vendor=vendor,
                device_class=device_class,
                randomized=randomized,
                band=band,
                channel=channel,
                width_mhz=width_mhz,
                rssi=rssi,
                metadata=meta,
                node_data=node_data,
                position=position or node_data.get("position"),
                identity_seed_id=identity_seed_id,
            )
        )
        cluster_info = dict(cluster_assignment.get("cluster") or {})
        mac_cluster_id = str(cluster_assignment.get("cluster_id") or f"mac-cluster:{_stable_hash(identity_seed_id)}")
        alias_device_id = self._cluster_anchor_id(mac_cluster_id)
        canonical_node_id = self._canonical_node_id(node_id, alias_device_id, randomized, ssid, vendor, channel)
        state = self._update_state(
            alias_device_id=alias_device_id,
            timestamp=ts,
            bssid=bssid,
            ssid=ssid,
            rssi=rssi,
            position=position or node_data.get("position"),
        )

        avg_rssi = state.rssi_sum / state.rssi_samples if state.rssi_samples else rssi
        proxy_radius_m = _estimate_proxy_radius_m(avg_rssi, accuracy_m)
        drift_radius_m = max(proxy_radius_m, state.session_max_drift_m or state.max_drift_m)
        session_intervals = state.session_recent_intervals or state.recent_intervals
        burstiness = _burstiness(session_intervals)
        periodicity_score = _periodicity_score(session_intervals)
        session_duration_s = max(0.0, state.last_seen - state.session_started_at)
        handoff_count = max(0, len(state.session_raw_macs) - 1)
        vendor_likelihood = str(
            cluster_info.get("vendor_likelihood")
            or _vendor_likelihood(vendor, randomized=randomized, device_class=device_class)
        )
        cluster_size = max(int(cluster_info.get("size") or 0), len(state.raw_macs), 1)
        cluster_observation_count = max(int(cluster_info.get("observation_count") or 0), state.seen_count, 1)
        cluster_confidence = _clamp(_as_float(cluster_info.get("confidence")) or 0.0)
        cluster_stability_score = _clamp(_as_float(cluster_info.get("stability_score")) or 0.0)
        cluster_continuity_confidence = _clamp(_as_float(cluster_info.get("continuity_confidence")) or 0.0)
        cluster_similarity = _clamp(_as_float(cluster_assignment.get("similarity")) or 0.0)
        randomized_ratio = _clamp(
            _as_float(cluster_info.get("randomized_ratio"))
            if cluster_info.get("randomized_ratio") is not None
            else (1.0 if randomized else 0.0)
        )

        if drift_radius_m <= 30.0:
            mobility = "static"
        elif drift_radius_m <= 120.0:
            mobility = "local_roaming"
        else:
            mobility = "mobile"

        persistence_score = _clamp(
            0.18 * min(4.0, math.log1p(state.seen_count))
            + min(max(0.0, state.last_seen - state.first_seen), 300.0) / 300.0 * 0.55
            + min(float(state.session_seen_count), 12.0) / 12.0 * 0.27
        )
        continuity_score = _clamp(
            0.35 * persistence_score
            + 0.25 * min(float(len(state.raw_macs)), 4.0) / 4.0
            + 0.2 * (1.0 - burstiness)
            + 0.2 * periodicity_score
        )
        continuity_score = _clamp(max(continuity_score, cluster_continuity_confidence, cluster_similarity))
        stability_score = _clamp(
            0.45 * persistence_score
            + 0.2 * periodicity_score
            + 0.15 * (1.0 - burstiness)
            + 0.2 * (1.0 if mobility == "static" else 0.7 if mobility == "local_roaming" else 0.45)
        )
        stability_score = _clamp(max(stability_score, cluster_stability_score))
        cluster_confidence = _clamp(max(cluster_confidence, stability_score * 0.85))
        duty_cycle = _clamp(state.session_seen_count / max(session_duration_s / 5.0, 1.0) / 2.0)
        rf_pattern = _pattern_label(
            periodicity_score=periodicity_score,
            burstiness=burstiness,
            mobility=mobility,
        )
        geo = self._geo_profile(
            position=position or node_data.get("position"),
            node_data=node_data,
            drift_radius_m=drift_radius_m,
        )
        session = self._session_profile(
            state=state,
            geo=geo,
            mobility=mobility,
            handoff_count=handoff_count,
            duration_s=session_duration_s,
            burstiness=burstiness,
            periodicity_score=periodicity_score,
        )
        temporal = {
            "first_seen": state.first_seen,
            "last_seen": state.last_seen,
            "seen_count": state.seen_count,
            "session_id": state.session_id,
            "session_started_at": state.session_started_at,
            "session_seen_count": state.session_seen_count,
            "persistence_score": persistence_score,
            "session_duration_s": session_duration_s,
            "observation_count": state.session_seen_count,
            "handoff_count": handoff_count,
            "timeline_summary": session["timeline_summary"],
        }
        motion = self._motion_profile(
            state=state,
            session=session,
            mobility=mobility,
        )
        behavior_profile = _behavior_classification(
            device_class=device_class,
            mobility=mobility,
            burstiness=burstiness,
            periodicity_score=periodicity_score,
            persistence_score=persistence_score,
            randomized=randomized,
            duty_cycle=duty_cycle,
            handoff_count=handoff_count,
            scan_type=meta.get("scan_type") or node_data.get("scan_type") or meta.get("source") or node_data.get("source"),
            rf_pattern=rf_pattern,
            motion=motion,
        )

        anomaly_score = 0.05
        if randomized:
            anomaly_score += 0.2
        if str(ssid).strip() in ("", "[hidden]"):
            anomaly_score += 0.1
        if device_class == "mobile_hotspot":
            anomaly_score += 0.15
        if mobility != "static":
            anomaly_score += 0.1
        anomaly_score = _clamp(anomaly_score)

        covert_channel_score = _clamp(
            (0.2 if randomized else 0.0)
            + (0.15 if str(ssid).strip() in ("", "[hidden]") else 0.0)
            + (0.15 if (width_mhz or 0.0) >= 80.0 else 0.0)
            + 0.2 * burstiness
        )

        identity = {
            "alias_device_id": alias_device_id,
            "identity_seed_id": identity_seed_id,
            "canonical_node_id": canonical_node_id,
            "oui_vendor": vendor,
            "device_class": device_class,
            "ssid_fingerprint": _ssid_fingerprint(ssid),
            "is_randomized_mac": randomized,
            "bssid": bssid,
            "raw_mac_count": len(state.raw_macs),
            "mac_cluster_id": mac_cluster_id,
            "cluster_size": cluster_size,
            "cluster_observation_count": cluster_observation_count,
            "stability_score": stability_score,
            "continuity_score": continuity_score,
            "cluster_confidence": cluster_confidence,
            "randomized_ratio": randomized_ratio,
            "assignment_similarity": cluster_similarity,
            "assigned_to_existing_cluster": bool(cluster_assignment.get("assigned")),
            "vendor_likelihood": vendor_likelihood,
        }
        rf_profile = {
            "technology": "802.11",
            "band": band,
            "channel": channel,
            "channel_width_mhz": width_mhz,
            "rssi_mean_dbm": avg_rssi,
            "pattern": rf_pattern,
            "rf_signature_id": f"rf-signature:{_stable_hash(mac_cluster_id, band or '', channel or '', width_mhz or '')}",
        }
        behavior = {
            "mobility": mobility,
            "burstiness": burstiness,
            "periodicity_score": periodicity_score,
            "duty_cycle": duty_cycle,
            "scan_type": str(meta.get("scan_type") or node_data.get("scan_type") or meta.get("source") or node_data.get("source") or "scan"),
            "rf_pattern": rf_pattern,
            "observation_mode": "observer_proxy",
            "classification": behavior_profile["classification"],
            "confidence": behavior_profile["confidence"],
            "tags": behavior_profile["tags"],
            "entropy_score": behavior_profile["entropy_score"],
            "behavior_hash": behavior_profile["behavior_hash"],
            "behavior_signature": dict(behavior_profile["behavior_signature"]),
            "relay_likelihood": behavior_profile["relay_likelihood"],
            "explanation": behavior_profile["explanation"],
        }
        risk = {
            "anomaly_score": anomaly_score,
            "covert_channel_score": covert_channel_score,
        }

        rf_signature_id = rf_profile["rf_signature_id"]
        behavior_profile_id = f"behavior-profile:{_stable_hash(mac_cluster_id, behavior_profile['behavior_hash'])}"
        cognition_nodes = [
            _cognition_node(
                mac_cluster_id,
                "mac_cluster",
                labels={
                    "technology": "wifi",
                    "deviceClass": device_class,
                    "vendorLikelihood": vendor_likelihood,
                },
                metadata={
                    "mac_cluster_id": mac_cluster_id,
                    "identity_anchor_id": alias_device_id,
                    "identity_seed_id": identity_seed_id,
                    "cluster_size": cluster_size,
                    "observation_count": cluster_observation_count,
                    "confidence": cluster_confidence,
                    "stability_score": stability_score,
                    "continuity_score": continuity_score,
                    "vendor_likelihood": vendor_likelihood,
                    "randomized_ratio": randomized_ratio,
                    "randomization_detected": randomized_ratio > 0.0,
                    "assignment_similarity": cluster_similarity,
                    "raw_mac_count": len(state.raw_macs),
                },
            ),
            _cognition_node(
                state.session_id,
                "recon_session",
                labels={
                    "technology": "wifi",
                    "mobility": mobility,
                    "identityAnchor": alias_device_id,
                },
                metadata={
                    "session_id": state.session_id,
                    "identity_anchor_id": alias_device_id,
                    "started_at": state.session_started_at,
                    "last_seen": state.last_seen,
                    "duration_s": session_duration_s,
                    "observation_count": state.session_seen_count,
                    "movement_class": mobility,
                    "handoff_count": handoff_count,
                    "persistence_score": persistence_score,
                    "avg_interval_s": session["avg_interval_s"],
                    "observation_rate_hz": session["observation_rate_hz"],
                    "displacement_m": session["displacement_m"],
                    "heading_deg": session.get("heading_deg"),
                    "velocity_mps": motion["velocity_mps"],
                    "drift_class": motion["drift_class"],
                    "trajectory_confidence": motion["trajectory_confidence"],
                    "timeline_summary": session["timeline_summary"],
                },
            ),
            _cognition_node(
                behavior_profile_id,
                "behavior_profile",
                labels={
                    "technology": "wifi",
                    "classification": behavior_profile["classification"],
                    "mobility": mobility,
                },
                metadata={
                    "behavior_profile_id": behavior_profile_id,
                    "classification": behavior_profile["classification"],
                    "confidence": behavior_profile["confidence"],
                    "tags": behavior_profile["tags"],
                    "explanation": behavior_profile["explanation"],
                    "burstiness": burstiness,
                    "periodicity_score": periodicity_score,
                    "duty_cycle": duty_cycle,
                    "entropy_score": behavior_profile["entropy_score"],
                    "behavior_hash": behavior_profile["behavior_hash"],
                    "relay_likelihood": behavior_profile["relay_likelihood"],
                    "behavior_signature": dict(behavior_profile["behavior_signature"]),
                },
            ),
            _cognition_node(
                rf_signature_id,
                "rf_signature",
                labels={
                    "technology": "wifi",
                    "band": band or "unknown",
                    "channel": channel if channel is not None else "unknown",
                },
                metadata={
                    "rf_signature_id": rf_signature_id,
                    "technology": "802.11",
                    "band": band,
                    "channel": channel,
                    "channel_width_mhz": width_mhz,
                    "rssi_mean_dbm": avg_rssi,
                    "pattern": rf_profile["pattern"],
                },
            ),
        ]
        cognition_edges = [
            _cognition_edge(
                canonical_node_id,
                "MEMBER_OF",
                mac_cluster_id,
                weight=max(cluster_confidence, continuity_score, 0.1),
                metadata={
                    "relationship": "identity_anchor",
                    "confidence": cluster_confidence,
                    "similarity": cluster_similarity,
                    "continuity_confidence": continuity_score,
                },
            ),
            _cognition_edge(
                canonical_node_id,
                "PART_OF",
                state.session_id,
                weight=max(persistence_score, 0.1),
                metadata={"relationship": "observation_session", "confidence": persistence_score},
            ),
            _cognition_edge(
                canonical_node_id,
                "HAS_BEHAVIOR",
                behavior_profile_id,
                weight=max(behavior_profile["confidence"], 0.1),
                metadata={"classification": behavior_profile["classification"]},
            ),
            _cognition_edge(
                canonical_node_id,
                "HAS_RF_SIGNATURE",
                rf_signature_id,
                metadata={"technology": "wifi"},
            ),
        ]
        cognition = {
            "schema_version": "recon-cognition.v1",
            "mac_cluster_id": mac_cluster_id,
            "session_id": state.session_id,
            "behavior_profile_id": behavior_profile_id,
            "rf_signature_id": rf_signature_id,
            "summary": {
                "classification": behavior_profile["classification"],
                "confidence": behavior_profile["confidence"],
                "cluster_size": cluster_size,
                "cluster_confidence": cluster_confidence,
                "session_duration_s": session_duration_s,
                "observation_count": state.session_seen_count,
                "mobility": mobility,
                "entropy_score": behavior_profile["entropy_score"],
                "behavior_hash": behavior_profile["behavior_hash"],
                "drift_class": motion["drift_class"],
                "velocity_mps": motion["velocity_mps"],
                "timeline_summary": session["timeline_summary"],
            },
            "nodes": cognition_nodes,
            "edges": cognition_edges,
        }

        display_name = _friendly_name(vendor, device_class, ssid)
        labels = {
            "technology": "wifi",
            "obsClass": "observed",
            "deviceClass": device_class,
            "vendor": vendor,
            "band": band or "unknown",
            "identityAnchor": alias_device_id,
            "sessionId": state.session_id,
            "macClusterId": mac_cluster_id,
            "behaviorProfileId": behavior_profile_id,
        }

        meta.update(
            {
                "type": "wifi_ap",
                "technology": "wifi",
                "display_name": display_name,
                "canonical_node_id": canonical_node_id,
                "identity_anchor_id": alias_device_id,
                "identity": identity,
                "rf_profile": rf_profile,
                "behavior": behavior,
                "temporal": temporal,
                "session": session,
                "motion": motion,
                "geo": geo,
                "risk": risk,
                "cognition": cognition,
                "mac_cluster": cluster_info,
                "mac_cluster_id": mac_cluster_id,
                "behavior_profile_id": behavior_profile_id,
                "rf_signature_id": rf_signature_id,
                "obs_class": "observed",
                "recon_type": "MOBILE_HOTSPOT" if device_class == "mobile_hotspot" else "WIFI_AP",
                "ontology": ontology,
            }
        )
        if node_id != canonical_node_id:
            meta.setdefault("source_node_id", node_id)

        return {
            "node_id": canonical_node_id,
            "metadata": meta,
            "labels": labels,
            "display_name": display_name,
            "kind": "wifi_ap",
        }

    def _alias_device_id(
        self,
        *,
        bssid: str,
        ssid: Any,
        band: Optional[str],
        channel: Optional[int],
        width_mhz: Optional[float],
        vendor: str,
        device_class: str,
        randomized: bool,
    ) -> str:
        if not randomized and bssid:
            return f"wifi-device:{bssid.replace(':', '')}"

        ssid_text = str(ssid or "").strip()
        if ssid_text in ("", "[hidden]") and vendor == "Unknown" and channel is None:
            return f"wifi-device:{(bssid or 'unknown').replace(':', '')}"

        fingerprint = _stable_hash(
            ssid_text.lower(),
            band or "",
            channel or "",
            width_mhz or "",
            vendor,
            device_class,
        )
        return f"wifi-device:{fingerprint}"

    def _canonical_node_id(
        self,
        node_id: str,
        alias_device_id: str,
        randomized: bool,
        ssid: Any,
        vendor: str,
        channel: Optional[int],
    ) -> str:
        ssid_text = str(ssid or "").strip()
        if randomized and (ssid_text not in ("", "[hidden]") or vendor != "Unknown" or channel is not None):
            return alias_device_id
        return node_id

    def _cluster_anchor_id(self, mac_cluster_id: str) -> str:
        cluster_token = str(mac_cluster_id).replace("mac-cluster:", "").replace(":", "-")
        return f"wifi-device:{cluster_token}"

    def _build_cluster_observation(
        self,
        *,
        node_id: str,
        timestamp: float,
        bssid: str,
        ssid: Any,
        vendor: str,
        device_class: str,
        randomized: bool,
        band: Optional[str],
        channel: Optional[int],
        width_mhz: Optional[float],
        rssi: Optional[float],
        metadata: Dict[str, Any],
        node_data: Dict[str, Any],
        position: Any,
        identity_seed_id: str,
    ) -> Dict[str, Any]:
        lat = lon = None
        if isinstance(position, (list, tuple)) and len(position) >= 2:
            lat = _as_float(position[0])
            lon = _as_float(position[1])
        if lat is None or lon is None:
            lat = _as_float(node_data.get("lat"))
            lon = _as_float(node_data.get("lon"))

        return {
            "observation_id": f"wifi-observation:{_stable_hash(node_id, bssid, timestamp, rssi, channel)}",
            "identity_seed": identity_seed_id,
            "mac": bssid,
            "oui": bssid[:8].upper() if len(bssid) >= 8 else "",
            "rssi": rssi,
            "channel": channel,
            "band": band,
            "channel_width_mhz": width_mhz,
            "ssid_fingerprint": _ssid_fingerprint(ssid),
            "security": metadata.get("security") or node_data.get("security"),
            "vendor_guess": vendor,
            "device_class": device_class,
            "timestamp": timestamp,
            "lat": lat,
            "lon": lon,
            "ie_fingerprint": metadata.get("ie_fingerprint") or node_data.get("ie_fingerprint") or metadata.get("information_elements"),
            "rates": metadata.get("rates") or node_data.get("rates"),
            "ht_cap": metadata.get("ht_cap") or node_data.get("ht_cap"),
            "scan_type": metadata.get("scan_type") or node_data.get("scan_type") or metadata.get("source") or node_data.get("source") or "scan",
            "rf_signature": metadata.get("rf_signature") or node_data.get("rf_signature"),
            "mobility": metadata.get("mobility"),
            "burstiness": _as_float(metadata.get("burstiness")),
            "periodicity_score": _as_float(metadata.get("periodicity_score")),
            "duty_cycle": _as_float(metadata.get("duty_cycle")),
            "entropy_score": _as_float(metadata.get("entropy_score")),
            "behavior_hash": metadata.get("behavior_hash"),
            "is_randomized_mac": randomized,
        }

    def _update_state(
        self,
        *,
        alias_device_id: str,
        timestamp: float,
        bssid: str,
        ssid: Any,
        rssi: Optional[float],
        position: Any,
    ) -> _WiFiAliasState:
        lat_lon = None
        if isinstance(position, (list, tuple)) and len(position) >= 2:
            lat = _as_float(position[0])
            lon = _as_float(position[1])
            if lat is not None and lon is not None:
                lat_lon = (lat, lon)

        with self._lock:
            state = self._states.get(alias_device_id)
            if state is None:
                state = _WiFiAliasState(alias_device_id=alias_device_id, first_seen=timestamp, last_seen=timestamp)
                self._states[alias_device_id] = state

            gap = max(0.0, timestamp - state.last_seen)
            new_session = not state.session_id or gap > self.session_timeout_s
            if new_session:
                state.session_counter += 1
                state.session_id = f"wifi-session:{_stable_hash(alias_device_id, state.session_counter)}"
                state.session_started_at = timestamp
                state.session_seen_count = 0
                state.session_recent_intervals.clear()
                state.session_positions.clear()
                state.session_track.clear()
                state.session_max_drift_m = 0.0
                state.session_raw_macs.clear()
                state.session_ssids_seen.clear()
            elif state.seen_count > 0 and gap > 0:
                state.recent_intervals.append(gap)
                state.session_recent_intervals.append(gap)

            state.last_seen = timestamp
            state.seen_count += 1
            state.session_seen_count += 1

            if bssid:
                state.raw_macs.add(bssid)
                state.session_raw_macs.add(bssid)
            ssid_text = str(ssid or "").strip()
            if ssid_text:
                state.ssids_seen.add(ssid_text)
                state.session_ssids_seen.add(ssid_text)
            if rssi is not None:
                state.rssi_sum += rssi
                state.rssi_samples += 1
            if lat_lon is not None:
                if state.recent_positions:
                    last_lat, last_lon = state.recent_positions[-1]
                    state.max_drift_m = max(state.max_drift_m, _haversine_m(last_lat, last_lon, lat_lon[0], lat_lon[1]))
                state.recent_positions.append(lat_lon)
                if state.session_positions:
                    last_lat, last_lon = state.session_positions[-1]
                    state.session_max_drift_m = max(
                        state.session_max_drift_m,
                        _haversine_m(last_lat, last_lon, lat_lon[0], lat_lon[1]),
                    )
                state.session_positions.append(lat_lon)
                state.session_track.append((timestamp, lat_lon[0], lat_lon[1]))

            return state

    def _geo_profile(self, *, position: Any, node_data: Dict[str, Any], drift_radius_m: float) -> Dict[str, Any]:
        geo: Dict[str, Any] = {
            "drift_radius_m": drift_radius_m,
            "location_method": "observer_proxy",
            "geo_confidence": 0.35,
        }
        if isinstance(position, (list, tuple)) and len(position) >= 2:
            lat = _as_float(position[0])
            lon = _as_float(position[1])
            alt = _as_float(position[2]) if len(position) >= 3 else _as_float(node_data.get("alt") or node_data.get("alt_m"))
            if lat is not None and lon is not None:
                geo["lat"] = lat
                geo["lon"] = lon
            if alt is not None:
                geo["altitude_m"] = alt
        else:
            lat = _as_float(node_data.get("lat"))
            lon = _as_float(node_data.get("lon"))
            alt = _as_float(node_data.get("alt") or node_data.get("alt_m"))
            if lat is not None and lon is not None:
                geo["lat"] = lat
                geo["lon"] = lon
            if alt is not None:
                geo["altitude_m"] = alt
        return geo

    def _session_profile(
        self,
        *,
        state: _WiFiAliasState,
        geo: Dict[str, Any],
        mobility: str,
        handoff_count: int,
        duration_s: float,
        burstiness: float,
        periodicity_score: float,
    ) -> Dict[str, Any]:
        avg_interval_s = None
        if state.session_recent_intervals:
            avg_interval_s = sum(state.session_recent_intervals) / len(state.session_recent_intervals)
        observation_rate_hz = state.session_seen_count / max(duration_s, 1.0)
        displacement_m = 0.0
        heading_deg = None
        first_position = None
        last_position = None
        if state.session_positions:
            first_position = {"lat": state.session_positions[0][0], "lon": state.session_positions[0][1]}
            last_position = {"lat": state.session_positions[-1][0], "lon": state.session_positions[-1][1]}
        if len(state.session_positions) >= 2:
            first_lat, first_lon = state.session_positions[0]
            last_lat, last_lon = state.session_positions[-1]
            displacement_m = _haversine_m(first_lat, first_lon, last_lat, last_lon)
            heading_deg = _bearing_deg(first_lat, first_lon, last_lat, last_lon)

        timeline_summary_parts = [
            f"{state.session_seen_count} obs / {duration_s:.0f}s",
            mobility.replace("_", " "),
        ]
        if avg_interval_s is not None:
            timeline_summary_parts.append(f"{avg_interval_s:.1f}s cadence")
        if displacement_m > 0.0:
            timeline_summary_parts.append(f"{displacement_m:.1f}m drift")
        if handoff_count > 0:
            timeline_summary_parts.append(f"{handoff_count} handoff(s)")

        return {
            "session_id": state.session_id,
            "started_at": state.session_started_at,
            "last_seen": state.last_seen,
            "duration_s": duration_s,
            "observation_count": state.session_seen_count,
            "movement_class": mobility,
            "handoff_count": handoff_count,
            "avg_interval_s": avg_interval_s,
            "observation_rate_hz": observation_rate_hz,
            "burstiness": burstiness,
            "periodicity_score": periodicity_score,
            "displacement_m": displacement_m,
            "heading_deg": heading_deg,
            "first_position": first_position,
            "last_position": last_position,
            "proxy_location": {
                "lat": geo.get("lat"),
                "lon": geo.get("lon"),
                "altitude_m": geo.get("altitude_m"),
            },
            "ssid_count": len(state.session_ssids_seen),
            "raw_mac_count": len(state.session_raw_macs),
            "timeline_summary": " | ".join(timeline_summary_parts),
        }

    def _motion_profile(
        self,
        *,
        state: _WiFiAliasState,
        session: Dict[str, Any],
        mobility: str,
    ) -> Dict[str, Any]:
        track = list(state.session_track)
        duration_s = max(0.0, _as_float(session.get("duration_s")) or 0.0)
        displacement_m = _as_float(session.get("displacement_m")) or 0.0
        heading_deg = _as_float(session.get("heading_deg"))
        path_length_m = 0.0
        last_step_speed_mps = 0.0
        segment_speeds: List[float] = []

        for (ts1, lat1, lon1), (ts2, lat2, lon2) in zip(track[:-1], track[1:]):
            segment_distance_m = _haversine_m(lat1, lon1, lat2, lon2)
            path_length_m += segment_distance_m
            dt = max(0.5, ts2 - ts1)
            segment_speed_mps = segment_distance_m / dt
            segment_speeds.append(segment_speed_mps)
            last_step_speed_mps = segment_speed_mps

        velocity_mps = displacement_m / max(duration_s, 1.0)
        if segment_speeds:
            velocity_mps = max(velocity_mps, sum(segment_speeds) / len(segment_speeds))

        linearity = displacement_m / max(path_length_m, 1.0) if path_length_m > 0.0 else 0.0
        if displacement_m < 8.0 or velocity_mps < 0.35:
            drift_class = "stationary"
        elif linearity >= 0.72:
            drift_class = "consistent_vector"
        elif path_length_m >= max(displacement_m * 2.2, 45.0):
            drift_class = "loitering"
        else:
            drift_class = "drifting"

        trajectory_confidence = _clamp(
            0.2
            + 0.25 * min(len(track), 4) / 4.0
            + 0.35 * linearity
            + 0.2 * min(velocity_mps, 8.0) / 8.0
        )

        predictive_presence = None
        last_position = dict(session.get("last_position") or {})
        if heading_deg is not None and last_position.get("lat") is not None and last_position.get("lon") is not None and velocity_mps >= 0.4:
            horizon_s = 12
            projected = _destination_point(
                float(last_position["lat"]),
                float(last_position["lon"]),
                float(heading_deg),
                velocity_mps * horizon_s,
            )
            predictive_presence = {
                "horizon_s": horizon_s,
                "model": "kinematic",
                "confidence": _clamp(0.32 + 0.45 * trajectory_confidence + 0.1 * min(velocity_mps, 6.0) / 6.0),
                "predicted_location": projected,
            }

        return {
            "velocity_mps": velocity_mps,
            "last_step_speed_mps": last_step_speed_mps,
            "heading_deg": heading_deg,
            "path_length_m": path_length_m,
            "linearity": linearity,
            "drift_class": drift_class,
            "movement_pattern": drift_class,
            "mobility": mobility,
            "trajectory_confidence": trajectory_confidence,
            "predictive_presence": predictive_presence,
        }


_WIFI_ENRICHER = WiFiObservationEnricher()


def configure_wifi_enricher(instance_db: Any = None, embedding_engine: Any = None):
    """
    Wire the persistent cognitive substrate into the global WiFi enricher.
    Enables COLD tier mirroring and semantic recall.
    """
    if _WIFI_ENRICHER:
        _WIFI_ENRICHER.cognitive_cache.instance_db = instance_db
        _WIFI_ENRICHER.cognitive_cache.embedding_engine = embedding_engine
        logger.info("[CognitiveCache] Persistent substrate wired to WiFi enricher")


def enrich_hypergraph_rf_node(
    node_id: str,
    node_data: Dict[str, Any],
    *,
    metadata: Optional[Dict[str, Any]] = None,
    position: Optional[Any] = None,
) -> Dict[str, Any]:
    return _WIFI_ENRICHER.enrich_rf_node(node_id, node_data, metadata=metadata, position=position)


def summarize_recon_actor(entity: Dict[str, Any]) -> Dict[str, str]:
    entity = dict(entity or {})
    metadata = dict(entity.get("metadata") or {})
    identity = dict(metadata.get("identity") or {})
    behavior = dict(entity.get("behavior") or metadata.get("behavior") or {})
    motion = dict(entity.get("motion") or metadata.get("motion") or {})
    network_binding = dict(entity.get("network_binding") or metadata.get("network_binding") or {})

    device_class = str(identity.get("device_class") or "")
    classification = str(behavior.get("classification") or "")
    if device_class == "mobile_hotspot":
        actor_label = "Mobile AP"
    elif classification == "INFRASTRUCTURE":
        actor_label = "Infrastructure AP"
    elif classification == "HUMAN_DRIVEN":
        actor_label = "Human-driven WiFi"
    elif classification == "BEACON":
        actor_label = "Beaconing WiFi"
    else:
        actor_label = "WiFi actor"

    parts: List[str] = [actor_label]
    if classification:
        parts.append(classification.replace("_", " ").lower())
    carrier = str(network_binding.get("carrier") or "")
    if carrier:
        parts.append(carrier)

    velocity_mps = _as_float(motion.get("velocity_mps"))
    heading_deg = _as_float(motion.get("heading_deg"))
    heading_cardinal = _heading_cardinal(heading_deg)
    if velocity_mps is not None and velocity_mps >= 0.4 and heading_cardinal:
        parts.append(f"moving {heading_cardinal} {velocity_mps:.1f} m/s")
    else:
        drift_class = str(motion.get("drift_class") or "")
        if drift_class:
            parts.append(drift_class.replace("_", " "))

    confidence = max(
        _as_float(behavior.get("confidence")) or 0.0,
        _as_float(network_binding.get("binding_confidence")) or 0.0,
        _as_float(((entity.get("cognition") or {}).get("summary") or {}).get("confidence")) or 0.0,
    )
    if confidence > 0.0:
        parts.append(f"{confidence:.2f} confidence")

    return {
        "actor_label": actor_label,
        "actor_summary": " · ".join(part for part in parts if part),
    }


def apply_recon_actor_summary(entity: Dict[str, Any]) -> Dict[str, Any]:
    enriched = dict(entity or {})
    metadata = dict(enriched.get("metadata") or {})
    summary = summarize_recon_actor(enriched)
    metadata.update(summary)
    enriched["metadata"] = metadata
    enriched.update(summary)
    return enriched


def build_recon_entity_from_graph_event(
    entity_id: str,
    entity_kind: str,
    data: Dict[str, Any],
    *,
    observed_at: Optional[float] = None,
) -> Dict[str, Any]:
    observed_at = observed_at or time.time()
    data = dict(data or {})
    meta = dict(data.get("metadata") or {})
    loc = data.get("location") or {}
    pos = data.get("position")
    if not loc and isinstance(pos, (list, tuple)) and len(pos) >= 2:
        loc = {
            "lat": pos[0],
            "lon": pos[1],
            "altitude_m": pos[2] if len(pos) > 2 else 0,
        }

    if entity_kind == "network_host":
        recon_id = entity_id if str(entity_id).startswith("PCAP-") else f"PCAP-{entity_id}"
        entity = {
            "entity_id": recon_id,
            "name": meta.get("name") or data.get("hostname") or meta.get("ip") or data.get("ip") or entity_id,
            "type": "RECON_ENTITY",
            "threat_level": data.get("threat_level") or meta.get("threat_level") or "UNKNOWN",
            "disposition": data.get("disposition") or meta.get("disposition") or "UNKNOWN",
            "ip": meta.get("ip") or data.get("ip") or "",
            "last_update": observed_at,
            "metadata": meta,
        }
        if loc:
            entity["location"] = loc
        return entity

    if meta.get("technology") == "wifi" or str(meta.get("type") or data.get("type") or "").lower() == "wifi_ap":
        risk = meta.get("risk") or {}
        anomaly_score = _as_float(risk.get("anomaly_score")) or 0.0
        if anomaly_score >= 0.75:
            threat_level = "HIGH"
            disposition = "SUSPICIOUS"
        elif anomaly_score >= 0.45:
            threat_level = "MEDIUM"
            disposition = "UNKNOWN"
        else:
            threat_level = "LOW"
            disposition = "UNKNOWN"

        entity = {
            "entity_id": meta.get("canonical_node_id") or entity_id,
            "name": meta.get("display_name") or meta.get("name") or entity_id,
            "label": meta.get("display_name") or meta.get("name") or entity_id,
            "type": meta.get("recon_type") or "WIFI_AP",
            "ontology": meta.get("ontology") or "network.wifi.access_point",
            "threat_level": data.get("threat_level") or meta.get("threat_level") or threat_level,
            "disposition": data.get("disposition") or meta.get("disposition") or disposition,
            "obs_class": meta.get("obs_class") or "observed",
            "last_update": observed_at,
            "metadata": meta,
            "session_id": (meta.get("temporal") or {}).get("session_id"),
            "identity_anchor_id": meta.get("identity_anchor_id"),
            "mac_cluster_id": (meta.get("identity") or {}).get("mac_cluster_id") or meta.get("mac_cluster_id"),
            "behavior_profile_id": meta.get("behavior_profile_id"),
            "rf_signature_id": meta.get("rf_signature_id"),
            "cognition": dict(meta.get("cognition") or {}),
            "behavior": dict(meta.get("behavior") or {}),
            "session": dict(meta.get("session") or {}),
            "motion": dict(meta.get("motion") or {}),
        }
        if loc:
            entity["location"] = loc
        return apply_recon_actor_summary(entity)

    entity = {
        "entity_id": entity_id,
        "name": meta.get("display_name") or meta.get("name") or data.get("hostname") or data.get("ip") or entity_id,
        "type": "RECON_ENTITY",
        "threat_level": data.get("threat_level") or meta.get("threat_level") or "UNKNOWN",
        "disposition": data.get("disposition") or meta.get("disposition") or "UNKNOWN",
        "ip": meta.get("ip") or data.get("ip") or "",
        "last_update": observed_at,
        "metadata": meta,
    }
    if loc:
        entity["location"] = loc
    return entity
