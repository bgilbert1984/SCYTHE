"""
cot_export.py — Cursor-on-Target (CoT) emitter for RF SCYTHE operator layer.

Reads hypergraph snapshots, filters geo-bearing nodes by obs_class + confidence,
and emits CoT XML for TAK endpoints (UDP/TCP/HTTP).

Inspired by LandSAR's approach: treat location as a distribution, not a point.
Each node becomes a CoT marker with confidence-derived uncertainty.

CoT event types:
  a-f-G-U-C   → geo_point (ground / unit / combat)
  a-f-G-E-X   → host (ground / equipment / nondescript)
  a-f-G-U-i   → flow corridor (ground / unit / infrastructure)
  a-n-G        → ASN / Org / Service (non-combatant ground)

obs_class styling:
  observed  → affiliation "f" (friendly/known), color blue
  implied   → affiliation "n" (neutral/assumed), color orange
  inferred  → affiliation "s" (suspect), color red
"""
from __future__ import annotations

import hashlib
import logging
import socket
import struct
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple
from xml.etree.ElementTree import Element, SubElement, tostring

logger = logging.getLogger(__name__)

Json = Dict[str, Any]

# ─── CoT type mappings ───────────────────────────────────────────────────────

NODE_KIND_TO_COT_TYPE = {
    "geo_point":  "a-{aff}-G-U-C",   # ground/unit/combat
    "host":       "a-{aff}-G-E-X",   # ground/equipment
    "flow":       "a-{aff}-G-U-i",   # ground/unit/infrastructure
    "asn":        "a-n-G",
    "org":        "a-n-G",
    "service":    "a-{aff}-G-E-S",   # ground/equipment/sensor
    "dns_name":   "a-n-G",
    "tls_sni":    "a-n-G",
    "http_host":  "a-n-G",
    "port_hub":   "a-n-G",
}

OBS_CLASS_TO_AFFILIATION = {
    "observed":  "f",   # friendly / known
    "implied":   "n",   # neutral / assumed
    "inferred":  "s",   # suspect
}

OBS_CLASS_TO_COLOR = {
    "observed":  -16776961,  # blue  (ARGB: 0xFF0000FF)
    "implied":   -33280,     # orange (ARGB: 0xFFFF7F00)
    "inferred":  -65536,     # red   (ARGB: 0xFFFF0000)
}


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _iso_stale(seconds: int = 300) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )


def _deterministic_uid(node_id: str) -> str:
    """Stable CoT UID so TAK track markers don't flap."""
    return "RFS-" + hashlib.sha256(node_id.encode()).hexdigest()[:12].upper()


# ─── Geo extraction ─────────────────────────────────────────────────────────

def _extract_geo(node: Json) -> Optional[Tuple[float, float]]:
    """Extract (lat, lon) from a node if it has geographic information."""
    labels = node.get("labels") or {}
    meta = node.get("metadata") or {}

    # Direct lat/lon
    lat = labels.get("lat") or meta.get("lat")
    lon = labels.get("lon") or meta.get("lon")
    if lat is not None and lon is not None:
        try:
            return (float(lat), float(lon))
        except (ValueError, TypeError):
            pass

    # GeoIP estimate
    geo = labels.get("geo") or meta.get("geo") or meta.get("geoip")
    if isinstance(geo, dict):
        lat = geo.get("lat") or geo.get("latitude")
        lon = geo.get("lon") or geo.get("longitude") or geo.get("lng")
        if lat is not None and lon is not None:
            try:
                return (float(lat), float(lon))
            except (ValueError, TypeError):
                pass

    return None


def _extract_obs_class(node: Json) -> str:
    """Get obs_class from node metadata, defaulting to 'observed'."""
    meta = node.get("metadata") or {}
    oc = meta.get("obs_class", "observed")
    kind = node.get("kind", "")
    if kind.startswith("INFERRED_"):
        oc = "inferred"
    return oc


def _extract_confidence(node: Json) -> float:
    meta = node.get("metadata") or {}
    try:
        return float(meta.get("confidence", 1.0))
    except (ValueError, TypeError):
        return 1.0


def _node_callsign(node: Json) -> str:
    """Short operator-readable callsign."""
    kind = node.get("kind", "?")
    labels = node.get("labels") or {}
    nid = node.get("id", "")

    if kind == "host":
        return f"HOST-{labels.get('ip', labels.get('address', nid[:16]))}"
    elif kind == "geo_point":
        city = labels.get('city', labels.get('location', ''))
        country = labels.get('country', labels.get('cc', ''))
        return f"GEO-{city or country or nid[:10]}"
    elif kind == "asn":
        asn = labels.get('asn_number') or labels.get('asn') or labels.get('number', nid[:10])
        return f"AS{asn}"
    elif kind == "org":
        org = labels.get('org_name') or labels.get('name') or labels.get('org', nid[:12])
        return f"ORG-{org}"
    elif kind == "dns_name":
        qname = labels.get('qname') or labels.get('name') or labels.get('domain', nid[:16])
        return f"DNS-{qname}"
    elif kind == "tls_sni":
        sni = labels.get('sni') or labels.get('server_name') or labels.get('name', nid[:16])
        return f"SNI-{sni}"
    elif kind == "service":
        svc = labels.get('service_name') or labels.get('name') or labels.get('service', nid[:12])
        return f"SVC-{svc}"
    elif kind == "flow":
        return f"FLOW-{labels.get('src_ip', '?')[:8]}>{labels.get('dst_ip', '?')[:8]}"
    else:
        return f"{kind.upper()[:6]}-{nid[:10]}"


# ─── CoT XML generation ─────────────────────────────────────────────────────

def node_to_cot_event(
    node: Json,
    *,
    stale_seconds: int = 300,
    ce_meters: float = 500.0,
    le_meters: float = 9999999.0,
) -> Optional[Element]:
    """Convert a geo-bearing node to a CoT <event> XML element.

    Args:
        node: Node dict from hypergraph snapshot.
        stale_seconds: How long the marker is considered fresh.
        ce_meters: Circular error (radius of uncertainty).
                   Lower confidence → larger CE (uncertainty ring).
        le_meters: Linear error (altitude uncertainty).

    Returns:
        ElementTree Element for the CoT event, or None if node has no geo.
    """
    geo = _extract_geo(node)
    if geo is None:
        return None

    lat, lon = geo
    obs_class = _extract_obs_class(node)
    confidence = _extract_confidence(node)
    kind = node.get("kind", "unknown")
    nid = node.get("id", "")
    callsign = _node_callsign(node)

    # Confidence → uncertainty radius (LandSAR-inspired)
    # Low confidence = large uncertainty ring
    # ce = base_ce / confidence  (bounded)
    adjusted_ce = max(100.0, min(ce_meters / max(confidence, 0.1), 50000.0))

    # CoT type with affiliation
    aff = OBS_CLASS_TO_AFFILIATION.get(obs_class, "u")
    cot_type_template = NODE_KIND_TO_COT_TYPE.get(kind, "a-{aff}-G")
    cot_type = cot_type_template.format(aff=aff)

    uid = _deterministic_uid(nid)
    now = _iso_now()
    stale = _iso_stale(stale_seconds)

    # Build CoT event
    event = Element("event")
    event.set("version", "2.0")
    event.set("uid", uid)
    event.set("type", cot_type)
    event.set("time", now)
    event.set("start", now)
    event.set("stale", stale)
    event.set("how", "m-g")  # machine-generated

    # Point
    point = SubElement(event, "point")
    point.set("lat", f"{lat:.8f}")
    point.set("lon", f"{lon:.8f}")
    point.set("hae", "0")   # height above ellipsoid
    point.set("ce", f"{adjusted_ce:.1f}")
    point.set("le", f"{le_meters:.1f}")

    # Detail
    detail = SubElement(event, "detail")

    # Contact / callsign
    contact = SubElement(detail, "contact")
    contact.set("callsign", callsign)

    # Remarks with metadata
    remarks = SubElement(detail, "remarks")
    remarks.text = (
        f"RF_SCYTHE {obs_class.upper()} | "
        f"conf={confidence:.2f} | "
        f"kind={kind} | "
        f"id={nid[:32]}"
    )

    # Color / icon styling
    color_elem = SubElement(detail, "__color")
    color_elem.set("argb", str(OBS_CLASS_TO_COLOR.get(obs_class, -1)))

    # RF SCYTHE custom extension
    rfs = SubElement(detail, "__rf_scythe")
    rfs.set("obs_class", obs_class)
    rfs.set("confidence", f"{confidence:.4f}")
    rfs.set("kind", kind)
    rfs.set("node_id", nid)

    labels = node.get("labels") or {}
    for k, v in list(labels.items())[:10]:
        rfs.set(f"label_{k}", str(v)[:64])

    return event


def edge_to_cot_polyline(
    edge: Json,
    node_lookup: Dict[str, Json],
    *,
    stale_seconds: int = 300,
) -> Optional[Element]:
    """Convert a geo-bearing edge (with 2 geo nodes) to a CoT shape (polyline).

    Useful for 'flow corridors' — src_geo → dst_geo lines on TAK map.
    """
    nodes = edge.get("nodes", [])
    if len(nodes) < 2:
        return None

    geos = []
    for nid in nodes[:2]:
        node = node_lookup.get(nid)
        if node:
            g = _extract_geo(node)
            if g:
                geos.append(g)

    if len(geos) < 2:
        return None

    meta = edge.get("metadata") or {}
    obs_class = meta.get("obs_class", "observed")
    confidence = meta.get("confidence", 1.0)
    eid = edge.get("id", str(uuid.uuid4()))
    kind = edge.get("kind", "UNKNOWN")

    aff = OBS_CLASS_TO_AFFILIATION.get(obs_class, "u")
    uid = "RFS-E-" + hashlib.sha256(eid.encode()).hexdigest()[:10].upper()
    now = _iso_now()
    stale = _iso_stale(stale_seconds)

    event = Element("event")
    event.set("version", "2.0")
    event.set("uid", uid)
    event.set("type", f"a-{aff}-G")
    event.set("time", now)
    event.set("start", now)
    event.set("stale", stale)
    event.set("how", "m-g")

    # Midpoint as the CoT point
    mid_lat = (geos[0][0] + geos[1][0]) / 2.0
    mid_lon = (geos[0][1] + geos[1][1]) / 2.0
    point = SubElement(event, "point")
    point.set("lat", f"{mid_lat:.8f}")
    point.set("lon", f"{mid_lon:.8f}")
    point.set("hae", "0")
    point.set("ce", "9999999")
    point.set("le", "9999999")

    detail = SubElement(event, "detail")

    # Link line
    link = SubElement(detail, "link")
    link.set("type", f"a-{aff}-G")
    link.set("point", f"{geos[1][0]:.8f},{geos[1][1]:.8f}")
    link.set("relation", kind)

    contact = SubElement(detail, "contact")
    contact.set("callsign", f"{kind[:16]}")

    remarks = SubElement(detail, "remarks")
    remarks.text = (
        f"RF_SCYTHE EDGE {obs_class.upper()} | "
        f"conf={confidence:.2f} | "
        f"{kind} | "
        f"{nodes[0][:16]}→{nodes[1][:16]}"
    )

    return event


# ─── Snapshot → CoT batch ────────────────────────────────────────────────────

def snapshot_to_cot(
    nodes: List[Json],
    edges: List[Json],
    *,
    obs_classes: Optional[Set[str]] = None,
    min_confidence: float = 0.0,
    geo_kinds: Optional[Set[str]] = None,
    include_edges: bool = False,
    stale_seconds: int = 300,
) -> List[bytes]:
    """Convert a hypergraph snapshot to a list of CoT XML bytes.

    Args:
        nodes: List of node dicts from hypergraph snapshot.
        edges: List of edge dicts.
        obs_classes: Filter set (e.g. {'observed', 'inferred'}). None = all.
        min_confidence: Minimum confidence threshold.
        geo_kinds: Node kinds to include. None = all geo-bearing nodes.
        include_edges: Whether to emit edge polylines.
        stale_seconds: CoT stale duration.

    Returns:
        List of XML bytes, each a complete CoT <event>.
    """
    results: List[bytes] = []
    node_lookup = {n.get("id", ""): n for n in nodes}

    for node in nodes:
        obs = _extract_obs_class(node)
        conf = _extract_confidence(node)
        kind = node.get("kind", "")

        if obs_classes and obs not in obs_classes:
            continue
        if conf < min_confidence:
            continue
        if geo_kinds and kind not in geo_kinds:
            continue

        ev = node_to_cot_event(node, stale_seconds=stale_seconds)
        if ev is not None:
            results.append(tostring(ev, encoding="unicode").encode("utf-8"))

    if include_edges:
        for edge in edges:
            meta = edge.get("metadata") or {}
            obs = meta.get("obs_class", "observed")
            conf = meta.get("confidence", 1.0)
            if obs_classes and obs not in obs_classes:
                continue
            if conf < min_confidence:
                continue
            ev = edge_to_cot_polyline(edge, node_lookup, stale_seconds=stale_seconds)
            if ev is not None:
                results.append(tostring(ev, encoding="unicode").encode("utf-8"))

    return results


# ─── Transport helpers ───────────────────────────────────────────────────────

def send_cot_udp(
    cot_messages: List[bytes],
    host: str = "239.2.3.1",
    port: int = 6969,
) -> int:
    """Send CoT messages via UDP (multicast or unicast).

    Default: ATAK multicast address 239.2.3.1:6969.
    Returns: number of messages sent.
    """
    sent = 0
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 32)
        for msg in cot_messages:
            sock.sendto(msg, (host, port))
            sent += 1
        sock.close()
    except Exception as e:
        logger.error(f"CoT UDP send failed: {e}")
    return sent


def send_cot_tcp(
    cot_messages: List[bytes],
    host: str = "127.0.0.1",
    port: int = 8087,
    timeout: float = 10.0,
) -> int:
    """Send CoT messages via TCP to a TAK Server.

    Returns: number of messages sent.
    """
    sent = 0
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        for msg in cot_messages:
            sock.sendall(msg)
            sent += 1
        sock.close()
    except Exception as e:
        logger.error(f"CoT TCP send failed: {e}")
    return sent


def cot_messages_to_xml_list(cot_bytes: List[bytes]) -> List[str]:
    """Convert CoT byte messages to XML strings (for HTTP/API responses)."""
    return [msg.decode("utf-8", errors="replace") for msg in cot_bytes]
