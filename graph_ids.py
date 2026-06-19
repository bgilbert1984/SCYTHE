"""
graph_ids.py — Deterministic, stable entity IDs for the RF SCYTHE hypergraph.

Convention:
    <kind>:<qualifier>

All IDs are lowercase, deterministic, and safe for upsert.
Re-ingesting the same PCAP or flow data produces identical IDs,
so apply_graph_event will UPDATE instead of creating duplicates.
"""
from __future__ import annotations

import hashlib
import ipaddress
from typing import Optional, Tuple, Union


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _short_hash(*parts: str, length: int = 10) -> str:
    """SHA-256 of concatenated parts, truncated to `length` hex chars."""
    raw = "|".join(str(p) for p in parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:length]


def _canonical_5tuple(
    src_ip: str, src_port: int,
    dst_ip: str, dst_port: int,
    proto: str,
) -> str:
    """Normalize a 5-tuple by sorting endpoints so direction doesn't matter."""
    a = (src_ip, int(src_port))
    b = (dst_ip, int(dst_port))
    if a > b:
        a, b = b, a
    return f"{a[0]}:{a[1]}-{b[0]}:{b[1]}/{proto.lower()}"


# ---------------------------------------------------------------------------
# Node IDs
# ---------------------------------------------------------------------------

def host_id(ip: str) -> str:
    """Deterministic host node ID.  e.g. host:ip:8.8.8.8"""
    return f"host:ip:{ip}"


def geo_id(lat: float, lon: float, precision: int = 5) -> str:
    """Quantized geo-point ID.  e.g. geo:37.42200:-122.08400"""
    return f"geo:{lat:.{precision}f}:{lon:.{precision}f}"


def asn_id(asn_number: Union[int, str]) -> str:
    """ASN node ID.  e.g. asn:15169"""
    return f"asn:{asn_number}"


def org_id(org_name: str) -> str:
    """Organization node ID. Lowercased slug.  e.g. org:google-llc"""
    slug = org_name.strip().lower().replace(" ", "-").replace(",", "").replace(".", "")
    return f"org:{slug}"


def port_hub_id(proto: str, port: int) -> str:
    """Port-hub node ID. e.g. port:tcp/443"""
    return f"port:{proto.lower()}/{port}"


def service_id(service_name: str) -> str:
    """Service node ID. e.g. svc:tls  svc:http  svc:ssh"""
    return f"svc:{service_name.strip().lower()}"


def dns_name_id(qname: str) -> str:
    """DNS query-name node ID. e.g. dns:qname:example.com"""
    return f"dns:qname:{qname.strip().lower().rstrip('.')}"


def tls_sni_id(sni: str) -> str:
    """TLS SNI node ID. e.g. tls:sni:api.github.com"""
    return f"tls:sni:{sni.strip().lower()}"


def tls_cert_id(fingerprint_sha256: str) -> str:
    """TLS certificate node ID (by fingerprint). e.g. tls:cert:ab12cd..."""
    return f"tls:cert:{fingerprint_sha256[:32].lower()}"


def http_host_id(host_header: str) -> str:
    """HTTP Host header node ID. e.g. http:host:example.com"""
    return f"http:host:{host_header.strip().lower()}"


def ja3_id(ja3_hash: str) -> str:
    """JA3 client fingerprint ID.  e.g. ja3:ab12cd34..."""
    return f"ja3:{ja3_hash[:32].lower()}"


def ja3s_id(ja3s_hash: str) -> str:
    """JA3S server fingerprint ID.  e.g. ja3s:ab12cd34..."""
    return f"ja3s:{ja3s_hash[:32].lower()}"


# ---------------------------------------------------------------------------
# Flow ID (stable across re-ingest of same session)
# ---------------------------------------------------------------------------

def flow_id(
    session_id: str,
    src_ip: str, src_port: int,
    dst_ip: str, dst_port: int,
    proto: str,
) -> str:
    """Deterministic direction-free conversation ID (A↔B identical to B↔A).

    Use this for bidirectional conversations where direction doesn't matter.
    For directional semantics (client→server, DNS queries, etc.), use
    ``flow_id_directional`` instead.
    """
    canon = _canonical_5tuple(src_ip, src_port, dst_ip, dst_port, proto)
    h = _short_hash(session_id, canon)
    return f"flow:{session_id}:{h}"


# Alias: explicit name for direction-free conversations
conv_id = flow_id


def flow_id_directional(
    session_id: str,
    src_ip: str, src_port: int,
    dst_ip: str, dst_port: int,
    proto: str,
) -> str:
    """Deterministic directional flow ID (A→B is distinct from B→A).

    Use for inference rules that depend on client/server roles:
    "host contacted SNI", DNS queries, flow corridors.
    """
    canon = f"{src_ip}:{int(src_port)}->{dst_ip}:{int(dst_port)}/{proto.lower()}"
    h = _short_hash(session_id, canon)
    return f"dflow:{session_id}:{h}"


# ---------------------------------------------------------------------------
# Hyperedge ID for "flow_observed"
# ---------------------------------------------------------------------------

def flow_observed_edge_id(flow_node_id: str) -> str:
    """Hyperedge connecting a flow to all its member nodes."""
    return f"he:flow_observed:{flow_node_id}"


# ---------------------------------------------------------------------------
# Edge IDs (deterministic, direction-free)
# ---------------------------------------------------------------------------

def session_observed_host_edge(session_id: str, host_node_id: str) -> str:
    return f"e:sess_obs_host:{session_id}:{host_node_id}"


def session_observed_flow_edge(session_id: str, flow_node_id: str) -> str:
    return f"e:sess_obs_flow:{session_id}:{flow_node_id}"


def host_geo_edge(host_node_id: str, geo_node_id: str) -> str:
    return f"e:host_geo:{host_node_id}:{geo_node_id}"


def host_in_asn_edge(host_node_id: str, asn_node_id: str) -> str:
    return f"e:host_asn:{host_node_id}:{asn_node_id}"


def asn_org_edge(asn_node_id: str, org_node_id: str) -> str:
    return f"e:asn_org:{asn_node_id}:{org_node_id}"


def flow_dst_port_edge(flow_node_id: str, port_node_id: str) -> str:
    return f"e:flow_port:{flow_node_id}:{port_node_id}"


def port_implied_service_edge(port_node_id: str, svc_node_id: str) -> str:
    return f"e:port_svc:{port_node_id}:{svc_node_id}"


def flow_sni_edge(flow_node_id: str, sni_node_id: str) -> str:
    return f"e:flow_sni:{flow_node_id}:{sni_node_id}"


def flow_dns_edge(flow_node_id: str, dns_node_id: str) -> str:
    return f"e:flow_dns:{flow_node_id}:{dns_node_id}"


def flow_http_host_edge(flow_node_id: str, http_node_id: str) -> str:
    return f"e:flow_http:{flow_node_id}:{http_node_id}"


# ---------------------------------------------------------------------------
# Port → Service heuristic
# ---------------------------------------------------------------------------

WELL_KNOWN_SERVICES = {
    20: "ftp-data", 21: "ftp", 22: "ssh", 23: "telnet",
    25: "smtp", 53: "dns", 67: "dhcp", 68: "dhcp",
    80: "http", 110: "pop3", 123: "ntp", 143: "imap",
    161: "snmp", 162: "snmp-trap", 389: "ldap",
    443: "https", 445: "smb", 465: "smtps",
    514: "syslog", 587: "smtp-submission",
    636: "ldaps", 993: "imaps", 995: "pop3s",
    1433: "mssql", 1521: "oracle", 3306: "mysql",
    3389: "rdp", 5432: "postgresql", 5900: "vnc",
    6379: "redis", 8080: "http-alt", 8443: "https-alt",
    8888: "http-alt", 9200: "elasticsearch", 27017: "mongodb",
}


def infer_service(port: int) -> Optional[str]:
    """Heuristic: map well-known ports to service names."""
    return WELL_KNOWN_SERVICES.get(port)


def is_private_ip(ip: str) -> bool:
    """Check if an IP address is RFC-1918 / link-local / loopback."""
    try:
        return ipaddress.ip_address(ip).is_private
    except Exception:
        return False


__all__ = [
    "host_id", "geo_id", "asn_id", "org_id",
    "port_hub_id", "service_id",
    "dns_name_id", "tls_sni_id", "tls_cert_id", "http_host_id",
    "ja3_id", "ja3s_id",
    "flow_id", "flow_id_directional", "conv_id",
    "flow_observed_edge_id",
    "session_observed_host_edge", "session_observed_flow_edge",
    "host_geo_edge", "host_in_asn_edge", "asn_org_edge",
    "flow_dst_port_edge", "port_implied_service_edge",
    "flow_sni_edge", "flow_dns_edge", "flow_http_host_edge",
    "infer_service", "is_private_ip",
    "WELL_KNOWN_SERVICES",
]
