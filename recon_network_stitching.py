from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Set


def _stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:12]


def _as_float(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except Exception:
        return None


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _string_set(values: Iterable[Any]) -> List[str]:
    return sorted({str(value) for value in values if value not in (None, "")})


def _id_variants(raw_id: Any) -> Set[str]:
    text = str(raw_id or "").strip()
    if not text:
        return set()
    variants = {text}
    if text.startswith("recon:"):
        variants.add(text[6:])
    else:
        variants.add(f"recon:{text}")
    return variants


def _entity_binding_keys(entity: Dict[str, Any]) -> Set[str]:
    metadata = dict(entity.get("metadata") or {})
    identity = dict(metadata.get("identity") or {})
    temporal = dict(metadata.get("temporal") or {})
    return {
        variant
        for candidate in (
            entity.get("entity_id"),
            entity.get("identity_anchor_id"),
            entity.get("network_identity_id"),
            entity.get("session_id"),
            metadata.get("canonical_node_id"),
            metadata.get("identity_anchor_id"),
            metadata.get("source_node_id"),
            identity.get("alias_device_id"),
            identity.get("canonical_node_id"),
            identity.get("mac_cluster_id"),
            temporal.get("session_id"),
        )
        for variant in _id_variants(candidate)
    }


def _extract_asn(network_observation: Dict[str, Any]) -> Optional[str]:
    metadata = dict(network_observation.get("metadata") or {})
    return next(
        (
            str(value)
            for value in (
                network_observation.get("asn"),
                metadata.get("asn"),
                metadata.get("autonomous_system_number"),
                metadata.get("autonomous_system"),
                metadata.get("as_number"),
            )
            if value not in (None, "")
        ),
        None,
    )


def _extract_carrier(network_observation: Dict[str, Any]) -> Optional[str]:
    metadata = dict(network_observation.get("metadata") or {})
    return next(
        (
            str(value)
            for value in (
                network_observation.get("carrier"),
                metadata.get("carrier"),
                metadata.get("carrier_name"),
                metadata.get("operator"),
                metadata.get("provider"),
                metadata.get("isp"),
                metadata.get("organization"),
                metadata.get("org"),
                metadata.get("asn_org"),
                metadata.get("autonomous_system_organization"),
            )
            if value not in (None, "")
        ),
        None,
    )


def _identity_pressure_summary(
    entity: Dict[str, Any],
    *,
    ip_count: int,
    binding_age_s: Optional[float],
    ja3_values: List[str],
    ja3_consistency: bool,
) -> Dict[str, float]:
    metadata = dict(entity.get("metadata") or {})
    identity = dict(metadata.get("identity") or {})
    behavior = dict(metadata.get("behavior") or {})
    temporal = dict(metadata.get("temporal") or {})
    session = dict(metadata.get("session") or {})

    cluster_stability = _clamp(
        max(
            _as_float(identity.get("stability_score")) or 0.0,
            _as_float(identity.get("continuity_score")) or 0.0,
            _as_float(identity.get("cluster_confidence")) or 0.0,
        )
    )
    entropy_score = _clamp(_as_float(behavior.get("entropy_score")) or 0.5)
    periodicity_score = _clamp(_as_float(behavior.get("periodicity_score")) or 0.0)
    protocol_consistency_score = _clamp(
        (1.0 - entropy_score) * 0.55
        + periodicity_score * 0.2
        + (1.0 if ja3_consistency else 0.55 if ja3_values else 0.35) * 0.25
    )
    randomized_ratio = _clamp(_as_float(identity.get("randomized_ratio")) or 0.0)
    ip_rotation_pressure = _clamp(
        min(max(ip_count - 1, 0), 4) / 4.0 * 0.75
        + randomized_ratio * 0.25
    )
    persistence_score = _clamp(_as_float(temporal.get("persistence_score")) or 0.0)
    session_duration_s = max(
        _as_float(temporal.get("session_duration_s")) or 0.0,
        _as_float(session.get("duration_s")) or 0.0,
    )
    freshness_score = 0.4
    if binding_age_s is not None:
        freshness_score = _clamp(1.0 - min(binding_age_s, 180.0) / 180.0)
    session_overlap_score = _clamp(
        persistence_score * 0.45
        + min(session_duration_s, 300.0) / 300.0 * 0.25
        + freshness_score * 0.30
    )
    identity_pressure = _clamp(
        cluster_stability * 0.35
        + protocol_consistency_score * 0.20
        + ip_rotation_pressure * 0.25
        + session_overlap_score * 0.20
    )
    if ip_rotation_pressure >= 0.45 and protocol_consistency_score >= 0.65:
        identity_pressure = _clamp(identity_pressure + 0.08)

    return {
        "cluster_stability": round(cluster_stability, 4),
        "protocol_consistency_score": round(protocol_consistency_score, 4),
        "ip_rotation_pressure": round(ip_rotation_pressure, 4),
        "session_overlap_score": round(session_overlap_score, 4),
        "identity_pressure": round(identity_pressure, 4),
    }


def summarize_recon_network_stitch(
    entity: Dict[str, Any],
    bindings: Iterable[Dict[str, Any]],
    *,
    network_observations_by_id: Optional[Dict[str, Dict[str, Any]]] = None,
    rf_observations_by_id: Optional[Dict[str, Dict[str, Any]]] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    entity_keys = _entity_binding_keys(entity)
    if not entity_keys:
        return {}

    network_observations_by_id = network_observations_by_id or {}
    rf_observations_by_id = rf_observations_by_id or {}
    matched = [
        dict(binding)
        for binding in (bindings or [])
        if entity_keys & _id_variants(binding.get("recon_entity_id"))
    ]
    if not matched:
        return {}

    matched.sort(key=lambda item: float(item.get("created_at") or 0.0), reverse=True)
    confidence_values = [_as_float(binding.get("confidence")) or 0.0 for binding in matched]
    latest_binding = matched[0]
    src_ips: List[str] = []
    dst_ips: List[str] = []
    protocols: List[str] = []
    ja3_values: List[str] = []
    asn_values: List[str] = []
    carrier_values: List[str] = []
    rf_nodes: List[str] = []
    sensor_ids: List[str] = []
    binding_samples: List[Dict[str, Any]] = []

    for binding in matched:
        sensor_id = binding.get("sensor_id")
        if sensor_id:
            sensor_ids.append(str(sensor_id))
        rf_node_id = binding.get("rf_node_id")
        if rf_node_id:
            rf_nodes.append(str(rf_node_id))

        network_observation = dict(
            network_observations_by_id.get(str(binding.get("network_observation_id")) or "") or {}
        )
        rf_observation = dict(
            rf_observations_by_id.get(str(binding.get("rf_observation_id")) or "") or {}
        )
        src_ip = network_observation.get("src_ip") or (binding.get("evidence") or {}).get("network_src_ip")
        dst_ip = network_observation.get("dst_ip") or (binding.get("evidence") or {}).get("network_dst_ip")
        protocol = network_observation.get("protocol")
        ja3 = network_observation.get("ja3") or (binding.get("evidence") or {}).get("network_ja3")
        asn = _extract_asn(network_observation)
        carrier = _extract_carrier(network_observation)
        if src_ip:
            src_ips.append(str(src_ip))
        if dst_ip:
            dst_ips.append(str(dst_ip))
        if protocol:
            protocols.append(str(protocol))
        if ja3:
            ja3_values.append(str(ja3))
        if asn:
            asn_values.append(str(asn))
        if carrier:
            carrier_values.append(str(carrier))

        binding_samples.append(
            {
                "binding_id": binding.get("binding_id"),
                "confidence": round(_as_float(binding.get("confidence")) or 0.0, 4),
                "created_at": _as_float(binding.get("created_at")) or 0.0,
                "src_ip": src_ip,
                "dst_ip": dst_ip,
                "protocol": protocol,
                "ja3": ja3,
                "asn": asn,
                "carrier": carrier,
                "rf_node_id": rf_node_id,
                "modulation": rf_observation.get("modulation") or (binding.get("evidence") or {}).get("rf_modulation"),
            }
        )

    asn_counter = Counter(asn_values)
    carrier_counter = Counter(carrier_values)
    ja3_consistency = len(set(ja3_values)) == 1 if ja3_values else False
    confidence_max = max(confidence_values) if confidence_values else 0.0
    confidence_avg = sum(confidence_values) / len(confidence_values) if confidence_values else 0.0
    latest_binding_at = _as_float(latest_binding.get("created_at")) or 0.0
    age_s = None
    if now is not None:
        age_s = max(0.0, float(now) - latest_binding_at)

    primary_asn = asn_counter.most_common(1)[0][0] if asn_counter else None
    primary_carrier = carrier_counter.most_common(1)[0][0] if carrier_counter else None
    src_ip_values = _string_set(src_ips)
    dst_ip_values = _string_set(dst_ips)
    ip_values = sorted(set(src_ip_values + dst_ip_values))
    pressure_summary = _identity_pressure_summary(
        entity,
        ip_count=len(ip_values),
        binding_age_s=age_s,
        ja3_values=ja3_values,
        ja3_consistency=ja3_consistency,
    )
    network_identity_id = "network-identity:" + _stable_hash(
        {
            "entity_id": entity.get("entity_id"),
            "src_ips": src_ip_values,
            "dst_ips": dst_ip_values,
            "asns": sorted(asn_counter.keys()),
            "carriers": sorted(carrier_counter.keys()),
        }
    )
    return {
        "network_identity_id": network_identity_id,
        "binding_count": len(matched),
        "binding_confidence": round(confidence_max, 4),
        "binding_confidence_avg": round(confidence_avg, 4),
        "binding_confidence_max": round(confidence_max, 4),
        "latest_binding_at": latest_binding_at,
        "binding_age_s": round(age_s, 2) if age_s is not None else None,
        "ip_count": len(ip_values),
        "src_ips": src_ip_values,
        "dst_ips": dst_ip_values,
        "ips": ip_values,
        "protocols": _string_set(protocols),
        "ja3_consistency": ja3_consistency,
        "ja3_values": _string_set(ja3_values),
        "asn": primary_asn,
        "asn_candidates": [value for value, _ in asn_counter.most_common(3)],
        "carrier": primary_carrier,
        "carrier_candidates": [value for value, _ in carrier_counter.most_common(3)],
        "rf_node_ids": _string_set(rf_nodes),
        "rf_node_count": len(set(rf_nodes)),
        "sensor_ids": _string_set(sensor_ids),
        **pressure_summary,
        "bindings": binding_samples[:4],
        "network_summary": (
            f"{primary_carrier or 'Unknown carrier'}"
            f" | ASN {primary_asn or 'unknown'}"
            f" | {len(matched)} binding(s)"
            f" | pressure {pressure_summary['identity_pressure']:.2f}"
        ),
    }


def apply_recon_network_stitch(
    entity: Dict[str, Any],
    bindings: Iterable[Dict[str, Any]],
    *,
    network_observations_by_id: Optional[Dict[str, Dict[str, Any]]] = None,
    rf_observations_by_id: Optional[Dict[str, Dict[str, Any]]] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    stitched = dict(entity or {})
    summary = summarize_recon_network_stitch(
        stitched,
        bindings,
        network_observations_by_id=network_observations_by_id,
        rf_observations_by_id=rf_observations_by_id,
        now=now if now is not None else time.time(),
    )
    if not summary:
        return stitched

    metadata = dict(stitched.get("metadata") or {})
    cognition = dict(stitched.get("cognition") or metadata.get("cognition") or {})
    cognition["network_summary"] = {
        "binding_count": summary["binding_count"],
        "binding_confidence": summary["binding_confidence"],
        "carrier": summary["carrier"],
        "asn": summary["asn"],
        "ip_count": summary["ip_count"],
        "ja3_consistency": summary["ja3_consistency"],
        "identity_pressure": summary["identity_pressure"],
        "cluster_stability": summary["cluster_stability"],
        "ip_rotation_pressure": summary["ip_rotation_pressure"],
        "session_overlap_score": summary["session_overlap_score"],
        "summary": summary["network_summary"],
    }
    cognition["identity_pressure"] = {
        "score": summary["identity_pressure"],
        "cluster_stability": summary["cluster_stability"],
        "protocol_consistency_score": summary["protocol_consistency_score"],
        "ip_rotation_pressure": summary["ip_rotation_pressure"],
        "session_overlap_score": summary["session_overlap_score"],
    }
    metadata["network_binding"] = summary
    metadata["network_identity_id"] = summary["network_identity_id"]
    metadata["cognition"] = cognition

    stitched["metadata"] = metadata
    stitched["cognition"] = cognition
    stitched["network_binding"] = summary
    stitched["network_identity_id"] = summary["network_identity_id"]
    return stitched


def apply_recon_network_stitch_batch(
    entities: Iterable[Dict[str, Any]],
    bindings: Iterable[Dict[str, Any]],
    *,
    network_observations_by_id: Optional[Dict[str, Dict[str, Any]]] = None,
    rf_observations_by_id: Optional[Dict[str, Dict[str, Any]]] = None,
    now: Optional[float] = None,
) -> List[Dict[str, Any]]:
    return [
        apply_recon_network_stitch(
            entity,
            bindings,
            network_observations_by_id=network_observations_by_id,
            rf_observations_by_id=rf_observations_by_id,
            now=now,
        )
        for entity in (entities or [])
    ]
