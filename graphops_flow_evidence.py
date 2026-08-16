"""Bounded packet-dissection evidence for revision-pinned network flows."""

from __future__ import annotations

import ipaddress
import math
import statistics
import threading
import time
import uuid
from collections import OrderedDict, deque
from typing import Any, Dict


DISSECTION_LABELS = (
    "app_proto", "flow_id", "flow_pkts_toserver", "flow_pkts_toclient",
    "flow_bytes_toserver", "flow_bytes_toclient", "flow_state", "flow_age",
    "tcp_flags", "tcp_state", "dns_rrname", "dns_rrtype", "dns_rcode",
    "http_hostname", "http_url", "http_method", "http_status",
    "tls_sni", "tls_version", "tls_ja3_hash", "alert_signature",
    "alert_category", "alert_severity",
)

TEMPORAL_RING_LIMIT = 32
TEMPORAL_FLOW_LIMIT = 4096
_TEMPORAL_RINGS: "OrderedDict[str, deque[Dict[str, Any]]]" = OrderedDict()
_TEMPORAL_RING_LOCK = threading.RLock()


def classify_flow_type(labels: Dict[str, Any]) -> tuple[str, str]:
    """Return a display-only flow class and the evidence basis for it."""
    fields = labels or {}
    if fields.get("alert_signature"):
        return "SECURITY_SIGNAL", "OBSERVED_DECODED"
    app_proto = _text(fields.get("app_proto"), 64).lower()
    if any(fields.get(key) for key in ("dns_rrname", "dns_rrtype", "dns_rcode")):
        return "DNS", "OBSERVED_DECODED"
    if any(fields.get(key) for key in ("http_hostname", "http_url", "http_method", "http_status")):
        return "HTTP", "OBSERVED_DECODED"
    if any(fields.get(key) for key in ("tls_sni", "tls_version", "tls_ja3_hash")):
        return "TLS", "OBSERVED_DECODED"
    if app_proto in {"dns", "mdns"}:
        return "DNS", "OBSERVED_DECODED"
    if app_proto in {"http", "http2"}:
        return "HTTP", "OBSERVED_DECODED"
    if app_proto in {"tls", "ssl"}:
        return "TLS", "OBSERVED_DECODED"
    if app_proto in {"quic", "http3"}:
        return "TLS_OR_QUIC", "OBSERVED_DECODED"
    if app_proto in {"ssdp", "llmnr"}:
        return "SERVICE_DISCOVERY", "OBSERVED_DECODED"
    proto = _text(fields.get("proto"), 32).lower()
    destination = _text(fields.get("dest_ip") or fields.get("dst_ip"), 128)
    try:
        multicast = ipaddress.ip_address(destination).is_multicast
    except ValueError:
        multicast = False
    ports = {_number(fields.get("src_port")), _number(fields.get("dest_port") or fields.get("dst_port"))}
    if multicast or ports.intersection({1900, 5353, 5355}):
        return "SERVICE_DISCOVERY", "INFERRED_TUPLE"
    if proto in {"icmp", "icmpv6", "icmp6"}:
        return "ICMP", "OBSERVED_TRANSPORT"
    if 53 in ports:
        return "DNS", "INFERRED_TUPLE"
    if ports.intersection({80, 8000, 8080}):
        return "HTTP", "INFERRED_TUPLE"
    if ports.intersection({443, 8443}):
        return "TLS_OR_QUIC", "INFERRED_TUPLE"
    return "OTHER", "OBSERVED_TRANSPORT" if proto and proto != "unknown" else "UNAVAILABLE"


def record_temporal_dissection(flow_id: str, record: Dict[str, Any]) -> None:
    """Retain one payload-free decoded event in a bounded per-flow tail ring."""
    flow_id = _text(flow_id, 256)
    event_id = _text(record.get("eventId"), 256)
    fields = {key: _text(value, 512) for key, value in (record.get("fields") or {}).items()
              if key in DISSECTION_LABELS and _text(value)}
    bounded = {
        "eventId": event_id, "eventType": _text(record.get("eventType"), 64),
        "observedAt": _text(record.get("observedAt"), 64),
        "observedAtEpoch": _number(record.get("observedAtEpoch")),
        "source": "SURICATA_EVE_DECODED_FIELDS", "evidenceClass": _text(
            record.get("evidenceClass") or "OBSERVED", 32).upper(),
        "fields": fields,
    }
    if not flow_id or not event_id:
        return
    with _TEMPORAL_RING_LOCK:
        ring = _TEMPORAL_RINGS.setdefault(flow_id, deque(maxlen=TEMPORAL_RING_LIMIT))
        if any(item.get("eventId") == event_id for item in ring):
            return
        ring.append(bounded)
        _TEMPORAL_RINGS.move_to_end(flow_id)
        while len(_TEMPORAL_RINGS) > TEMPORAL_FLOW_LIMIT:
            _TEMPORAL_RINGS.popitem(last=False)


def temporal_dissections(flow_id: str) -> list[Dict[str, Any]]:
    with _TEMPORAL_RING_LOCK:
        ring = _TEMPORAL_RINGS.get(str(flow_id))
        if ring is None:
            return []
        _TEMPORAL_RINGS.move_to_end(str(flow_id))
        return [{**item, "fields": dict(item.get("fields") or {})} for item in ring]


def clear_temporal_dissections() -> None:
    with _TEMPORAL_RING_LOCK:
        _TEMPORAL_RINGS.clear()


def _text(value: Any, limit: int = 1024) -> str:
    return str(value or "").strip()[:limit]


def _number(value: Any) -> int | float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return int(number) if number.is_integer() else number


def _endpoint(member: Dict[str, Any], fallback: str) -> Dict[str, Any]:
    labels = member.get("labels") or {}
    value = _text(labels.get("ip") or fallback.removeprefix("host:"), 128)
    try:
        value = str(ipaddress.ip_address(value))
    except ValueError:
        value = ""
    enrichment = member.get("enrichment") or {}
    network = enrichment.get("network") or {}
    geo = enrichment.get("geo") or {}
    return {
        "entityId": _text(member.get("id") or fallback, 256),
        **({"ip": value} if value else {}),
        "network": {key: network.get(key) for key in ("asn", "organization", "prefix")
                    if network.get(key) not in (None, "")},
        "geoip": {key: geo.get(key) for key in
                  ("city", "region", "country", "latitude", "longitude", "accuracyRadiusKm")
                  if geo.get(key) not in (None, "")},
        "geoipEvidenceClass": "INFERRED" if geo else "UNAVAILABLE",
    }


def prepare_flow_evidence(selection: Dict[str, Any], resolved: Dict[str, Any]) -> Dict[str, Any]:
    """Build a server-owned summary; no packet payload can enter this shape."""
    edge = resolved.get("edge") or {}
    if resolved.get("selectionKind") != "graph-edge":
        raise ValueError("flow evidence requires a graph-edge selection")
    if str(edge.get("kind") or "").lower() != "network_flow":
        raise ValueError("selected graph edge is not a network_flow")
    labels = dict(edge.get("labels") or {})
    metadata = dict(edge.get("metadata") or {})
    members = list(resolved.get("memberNodes") or [])[:2]
    member_by_id = {str(item.get("id")): item for item in members}
    node_ids = [str(item) for item in list(edge.get("nodes") or [])[:2]]
    endpoints = [_endpoint(member_by_id.get(node_id, {}), node_id) for node_id in node_ids]
    decoded = {key: _text(labels.get(key)) for key in DISSECTION_LABELS
               if _text(labels.get(key))}
    flow_id = _text(edge.get("id"), 256)
    observed_at = edge.get("observedAt") or edge.get("timestamp")
    selection_cutoff = _number(observed_at)
    all_temporal = sorted(temporal_dissections(flow_id), key=lambda item: (
        item.get("observedAtEpoch") is None, item.get("observedAtEpoch") or 0, item.get("eventId") or ""))
    temporal = [item for item in all_temporal if selection_cutoff is None or
                item.get("observedAtEpoch") is None or item["observedAtEpoch"] <= selection_cutoff]
    events_excluded_after_selection = len(all_temporal) - len(temporal)
    counters = {key: value for key, value in {
        "packets": _number(labels.get("packets")),
        "bytes": _number(labels.get("bytes")),
        "packetsToServer": _number(labels.get("flow_pkts_toserver")),
        "packetsToClient": _number(labels.get("flow_pkts_toclient")),
        "bytesToServer": _number(labels.get("flow_bytes_toserver")),
        "bytesToClient": _number(labels.get("flow_bytes_toclient")),
        "observationCount": _number(metadata.get("reinforcement_count")),
    }.items() if value is not None}
    evidence_class = str(edge.get("evidenceClass") or metadata.get("evidence_class") or
                         "INFERRED").upper()
    if evidence_class not in {"OBSERVED", "SYNTHETIC", "INFERRED", "MEASURED"}:
        evidence_class = "INFERRED"
    evidence_refs = (metadata.get("provenance_write") or {}).get("evidence_refs") or [""]
    if not temporal and decoded:
        temporal = [{"eventId": _text(evidence_refs[0]),
                     "eventType": "flow", "source": "SURICATA_EVE_DECODED_FIELDS",
                     "evidenceClass": evidence_class, "fields": decoded, "observedAt": observed_at,
                     "observedAtEpoch": _number(observed_at)}]
    epochs = [item.get("observedAtEpoch") for item in temporal
              if item.get("observedAtEpoch") is not None]
    intervals_ms = [round((right - left) * 1000, 3) for left, right in zip(epochs, epochs[1:])
                    if right >= left]
    omitted = max(0, int(counters.get("observationCount") or len(temporal)) - len(temporal))
    flow_type, flow_type_basis = classify_flow_type({**labels,
        **{"src_port": labels.get("src_port"), "dest_port": labels.get("dest_port")}})
    result = {
        "schemaVersion": "graphops.flow-evidence.v1",
        "evidenceId": f"flow-evidence-{uuid.uuid4().hex}",
        "capturedAt": time.time(),
        "selection": {
            "kind": "graph-edge", "entityId": selection.get("entityId"),
            "graphRevision": resolved.get("graphRevision") or selection.get("graphRevision"),
        },
        "flow": {
            "id": edge.get("id"), "kind": edge.get("kind"), "endpoints": endpoints,
            "displayType": flow_type, "displayTypeBasis": flow_type_basis,
            "direction": {key: labels.get(key) for key in (
                "tuple_direction", "tuple_direction_basis", "operational_direction",
                "direction_basis", "source_zone", "destination_zone", "sensor_id",
                "sensor_boundary_captured_at") if labels.get(key) not in (None, "")},
            "motion": {key: _number(labels.get(key)) if key != "motion_basis" else labels.get(key)
                       for key in ("motion_basis", "motion_interval_ms",
                                   "motion_forward_delta_packets", "motion_reverse_delta_packets",
                                   "motion_forward_delta_bytes", "motion_reverse_delta_bytes")
                       if labels.get(key) not in (None, "")},
            "transport": {key: labels.get(key) for key in
                          ("src_ip", "src_port", "dest_ip", "dest_port", "proto")
                          if labels.get(key) not in (None, "")},
            "counters": counters,
            "firstObservedAt": metadata.get("first_seen"),
            "lastObservedAt": observed_at,
            "evidenceClass": evidence_class,
        },
        "packetDissections": temporal,
        "temporalDissection": {
            "ringLimit": TEMPORAL_RING_LIMIT, "retainedEventCount": len(temporal),
            "eventsOmittedBeforeRing": omitted,
            "eventsExcludedAfterSelection": events_excluded_after_selection,
            "ordering": "OBSERVED_AT_ASCENDING_WITHIN_RETAINED_DELIVERY_TAIL",
            "windowStart": temporal[0].get("observedAt") if temporal else None,
            "windowEnd": temporal[-1].get("observedAt") if temporal else None,
            "durationMilliseconds": round((epochs[-1] - epochs[0]) * 1000, 3) if len(epochs) > 1 else 0,
            "interArrivalMilliseconds": intervals_ms,
            "medianInterArrivalMilliseconds": round(statistics.median(intervals_ms), 3) if intervals_ms else None,
            "sequenceAuthority": "BOUNDED_DECODED_EVENT_TAIL; NOT A COMPLETE PACKET SEQUENCE",
        },
        "coverage": {
            "status": "DECODED_FIELDS_AVAILABLE" if any(item.get("fields") for item in temporal)
            else "TRANSPORT_SUMMARY_ONLY",
            "decodedFieldCount": sum(len(item.get("fields") or {}) for item in temporal),
            "retainedDecodedEventCount": len(temporal), "temporalRingLimit": TEMPORAL_RING_LIMIT,
            "packetSequenceRetained": False,
            "rawPacketPayloadsRetained": False,
        },
        "suggestedQuestion": ((
            "Classify the activity represented by this flow and analyze sequence and cadence inside "
            "the bounded temporal dissection ring. Separate observed transport and decoded protocol "
            "facts from inferred application intent, identify plausible alternatives, and name the "
            "next packet-level observation that would falsify the leading interpretation."
        ) if len(temporal) >= 2 else (
            "Classify the activity represented by this flow. Separate observed transport and "
            "decoded protocol facts from inferred application intent, identify plausible alternatives, "
            "and name the next packet-level observation that would falsify the leading interpretation. "
            "Only one decoded event is retained, so sequence and cadence are unavailable."
        )),
        "bounded": True,
        "rawPacketsExposed": False,
        "boundary": (
            "FLOW COUNTERS AND ALLOW-LISTED EVE DISSECTION FIELDS ARE OBSERVED SUMMARIES; "
            "APPLICATION PURPOSE, USER INTENT, AND MALICIOUSNESS ARE INFERRED; RAW PACKET "
            "PAYLOADS AND A COMPLETE PACKET SEQUENCE ARE ABSENT"
        ),
    }
    return result
