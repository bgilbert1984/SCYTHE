"""Bounded sensor-boundary direction and measured flow-motion state.

Tuple orientation is observed in Eve.  Operational IN/OUT classification is
only asserted when an explicit, provenance-bearing sensor boundary contains
one endpoint.  The module never treats all private addresses as local.
"""

from __future__ import annotations

import ipaddress
import json
import math
import os
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Iterable


FLOW_MOTION_LIMIT = 4096
_MOTION: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
_LOCK = threading.RLock()


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def _networks(values: Iterable[Any]) -> list[ipaddress._BaseNetwork]:
    result = []
    for value in values:
        try:
            result.append(ipaddress.ip_network(str(value).strip(), strict=False))
        except ValueError:
            continue
    return result


def sensor_boundary(*, cidrs: Iterable[str] | None = None,
                    zone_file: str | None = None) -> Dict[str, Any]:
    """Load the current boundary on demand so roaming does not require restart."""
    if cidrs is not None:
        networks = _networks(cidrs)
        return {"networks": networks, "basis": "CONFIGURED_SENSOR_BOUNDARY",
                "sensorId": "test-or-explicit", "capturedAt": None}
    configured = [item for item in os.getenv("SCYTHE_SENSOR_LOCAL_CIDRS", "").split(",") if item.strip()]
    if configured:
        return {"networks": _networks(configured), "basis": "CONFIGURED_SENSOR_BOUNDARY",
                "sensorId": os.getenv("SCYTHE_SENSOR_ID", "configured-sensor"), "capturedAt": None}
    path = Path(zone_file or os.getenv("SCYTHE_SENSOR_ZONE_FILE", "")) if (
        zone_file or os.getenv("SCYTHE_SENSOR_ZONE_FILE")) else None
    if path:
        try:
            document = json.loads(path.read_text(encoding="utf-8-sig"))
            networks = _networks(document.get("localCidrs") or [])
            if networks:
                return {"networks": networks,
                        "basis": str(document.get("authority") or "DISCOVERED_SENSOR_INTERFACE")[:64],
                        "sensorId": str(document.get("sensorId") or "capture-sensor")[:128],
                        "capturedAt": document.get("capturedAt")}
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
    return {"networks": [], "basis": "UNAVAILABLE", "sensorId": None, "capturedAt": None}


def classify_flow_direction(source: str, destination: str, *,
                            cidrs: Iterable[str] | None = None,
                            zone_file: str | None = None) -> Dict[str, Any]:
    boundary = sensor_boundary(cidrs=cidrs, zone_file=zone_file)
    try:
        src = ipaddress.ip_address(source); dst = ipaddress.ip_address(destination)
    except ValueError:
        src = dst = None
    networks = boundary["networks"]
    src_local = bool(src and any(src in network for network in networks if network.version == src.version))
    dst_local = bool(dst and any(dst in network for network in networks if network.version == dst.version))
    if not networks or not src or not dst:
        operational = "UNRESOLVED"
    elif src_local and dst_local:
        operational = "EAST_WEST"
    elif src_local:
        operational = "OUTBOUND"
    elif dst_local:
        operational = "INBOUND"
    else:
        operational = "EXTERNAL_TRANSIT"
    return {
        "tuple_direction": "SOURCE_TO_DESTINATION",
        "tuple_direction_basis": "OBSERVED_EVE_TUPLE",
        "operational_direction": operational,
        "direction_basis": boundary["basis"],
        "source_zone": "LOCAL" if src_local else "NON_LOCAL" if networks else "UNRESOLVED",
        "destination_zone": "LOCAL" if dst_local else "NON_LOCAL" if networks else "UNRESOLVED",
        "sensor_id": boundary["sensorId"] or "",
        "sensor_boundary_captured_at": boundary["capturedAt"] or "",
    }


def preview_flow_motion(flow_id: str, fields: Dict[str, Any], observed_at: float) -> Dict[str, Any]:
    """Return deltas against the last committed summary without mutating state."""
    current = {
        "forward": _number(fields.get("flow_pkts_toserver")),
        "reverse": _number(fields.get("flow_pkts_toclient")),
        "forward_bytes": _number(fields.get("flow_bytes_toserver")),
        "reverse_bytes": _number(fields.get("flow_bytes_toclient")),
        "observed_at": _number(observed_at),
    }
    with _LOCK:
        previous = _MOTION.get(flow_id)
    unavailable = {"motion_basis": "INSUFFICIENT_TEMPORAL_COUNTERS",
                   "motion_interval_ms": "", "motion_forward_delta_packets": "",
                   "motion_reverse_delta_packets": "", "motion_forward_delta_bytes": "",
                   "motion_reverse_delta_bytes": ""}
    if not previous or current["observed_at"] is None or previous["observed_at"] is None:
        return unavailable
    interval = (current["observed_at"] - previous["observed_at"]) * 1000
    if interval <= 0:
        return unavailable
    result: Dict[str, Any] = {"motion_interval_ms": round(interval, 3)}
    for key, label in (("forward", "motion_forward_delta_packets"),
                       ("reverse", "motion_reverse_delta_packets"),
                       ("forward_bytes", "motion_forward_delta_bytes"),
                       ("reverse_bytes", "motion_reverse_delta_bytes")):
        before, after = previous.get(key), current.get(key)
        if before is not None and after is not None and after >= before:
            result[label] = int(after - before)
    if "motion_forward_delta_packets" in result or "motion_reverse_delta_packets" in result:
        result["motion_basis"] = "OBSERVED_SURICATA_COUNTER_DELTA"
    else:
        result = unavailable
    return result


def record_flow_motion(flow_id: str, fields: Dict[str, Any], observed_at: float) -> None:
    state = {
        "forward": _number(fields.get("flow_pkts_toserver")),
        "reverse": _number(fields.get("flow_pkts_toclient")),
        "forward_bytes": _number(fields.get("flow_bytes_toserver")),
        "reverse_bytes": _number(fields.get("flow_bytes_toclient")),
        "observed_at": _number(observed_at),
    }
    with _LOCK:
        _MOTION[flow_id] = state; _MOTION.move_to_end(flow_id)
        while len(_MOTION) > FLOW_MOTION_LIMIT:
            _MOTION.popitem(last=False)


def clear_flow_motion() -> None:
    with _LOCK:
        _MOTION.clear()
