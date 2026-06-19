"""network_ingress_aggregator.py — Unified network ingress orchestration.

This module provides real-time aggregation of network interface metrics,
autonomous capture triggering, and ingress plane visibility across all
network domains (physical, virtual, mesh, container overlays).

Architecture:
  - Enumerate all active network interfaces
  - Classify interface roles (physical, mesh, container, loopback)
  - Collect RX/TX statistics and deltas
  - Monitor for traffic anomalies
  - Trigger distributed packet capture on new host detection
  - Correlate with hypergraph for autonomous sensor activation

Operational Model:

  Tier 1: Passive Always-On
    └─ metadata only (conntrack, eve-streamer, Zeek)
    └─ unknown entity radar

  Tier 2: Triggered Burst Capture
    └─ when new host detected / ASN changed / protocol anomaly
    └─ tcpdump -i any host X -G 15 -W 1
    └─ micro pcapng files to /ftp_share/pcapng/discovered/

  Tier 3: Full Escalation
    └─ when confidence_score > threshold
    └─ full pcap rotation + Zeek carve + TLS fingerprinting
    └─ hypergraph promotion
"""

import json
import logging
import os
import psutil
import threading
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Interface classification
INTERFACE_ROLES = {
    "physical": lambda n: n.startswith(("eth", "wlan", "en")),
    "mesh_vpn": lambda n: n.startswith("tailscale"),
    "container_overlay": lambda n: n.startswith(("br-", "docker")),
    "loopback": lambda n: n.startswith("lo"),
    "virtual": lambda n: n.startswith(("veth", "vlan")),
}

# Capture configuration
IGNORE_INTERFACES = {"lo", "docker0"}
CAPTURE_TRIGGER_THRESHOLD = 20  # confidence score
MAX_PCAP_MB = 5
CAPTURE_SECONDS = 15
PCAP_TRUNCATE_SIZE = 128  # bytes (metadata-only)

# Host confidence scoring
HOST_CONFIDENCE_WEIGHTS = {
    "unseen_ip": 25,
    "foreign_asn": 15,
    "suspicious_ports": 20,
    "protocol_entropy": 10,
    "dns_anomaly": 15,
    "lateral_behavior": 30,
}

# Global state
_interface_stats = {}
_stats_lock = threading.Lock()
_monitoring = False
_last_stats = {}


def _classify_interface(name: str) -> str:
    """Classify interface by role."""
    if not name:
        return "unknown"

    for role, matcher in INTERFACE_ROLES.items():
        if matcher(name):
            return role

    return "unknown"


def _get_interface_stats() -> Dict[str, Any]:
    """Get current RX/TX stats for all interfaces."""
    try:
        stats = {}

        for iface, addrs in psutil.net_if_addrs().items():
            if iface in IGNORE_INTERFACES:
                continue

            try:
                io_counters = psutil.net_if_stats().get(iface)
                if not io_counters:
                    continue

                # Check if interface is up
                if not io_counters.isup:
                    continue

                stats[iface] = {
                    "name": iface,
                    "role": _classify_interface(iface),
                    "state": "up",
                    "mtu": io_counters.mtu,
                    "rx_bytes": io_counters.bytes_recv,
                    "tx_bytes": io_counters.bytes_sent,
                    "rx_packets": io_counters.packets_recv,
                    "tx_packets": io_counters.packets_sent,
                    "rx_errors": io_counters.errin,
                    "tx_errors": io_counters.errout,
                    "addresses": []
                }

                # Add IP addresses
                for addr in addrs:
                    if addr.family == 2:  # IPv4
                        stats[iface]["addresses"].append({
                            "family": "IPv4",
                            "addr": addr.address,
                            "netmask": addr.netmask
                        })
                    elif addr.family == 10:  # IPv6
                        stats[iface]["addresses"].append({
                            "family": "IPv6",
                            "addr": addr.address,
                            "netmask": addr.netmask
                        })

            except Exception as e:
                logger.debug(f"[IngressAggregator] Error collecting stats for {iface}: {e}")

        return stats

    except Exception as e:
        logger.error(f"[IngressAggregator] Failed to collect interface stats: {e}")
        return {}


def _calculate_traffic_delta() -> Dict[str, Any]:
    """Calculate RX/TX deltas since last collection."""
    global _last_stats

    current = _get_interface_stats()
    deltas = {}

    for iface, stats in current.items():
        if iface in _last_stats:
            prev = _last_stats[iface]
            deltas[iface] = {
                "name": iface,
                "role": stats["role"],
                "rx_mbps": (stats["rx_bytes"] - prev["rx_bytes"]) / 1_000_000 * 8,
                "tx_mbps": (stats["tx_bytes"] - prev["tx_bytes"]) / 1_000_000 * 8,
                "rx_pps": stats["rx_packets"] - prev["rx_packets"],
                "tx_pps": stats["tx_packets"] - prev["tx_packets"],
                "rx_bytes": stats["rx_bytes"],
                "tx_bytes": stats["tx_bytes"],
                "rx_errors": stats["rx_errors"],
                "tx_errors": stats["tx_errors"],
                "addresses": stats["addresses"],
                "state": stats["state"]
            }
        else:
            deltas[iface] = {
                "name": iface,
                "role": stats["role"],
                "rx_mbps": 0,
                "tx_mbps": 0,
                "rx_pps": 0,
                "tx_pps": 0,
                "rx_bytes": stats["rx_bytes"],
                "tx_bytes": stats["tx_bytes"],
                "rx_errors": stats["rx_errors"],
                "tx_errors": stats["tx_errors"],
                "addresses": stats["addresses"],
                "state": stats["state"]
            }

    _last_stats = current
    return deltas


def _monitor_ingress():
    """Background monitoring thread."""
    global _interface_stats

    logger.info("[IngressAggregator] Starting background monitoring")

    while _monitoring:
        try:
            deltas = _calculate_traffic_delta()

            with _stats_lock:
                _interface_stats = {
                    "timestamp": datetime.utcnow().isoformat(),
                    "interfaces": list(deltas.values())
                }

            time.sleep(5)

        except Exception as e:
            logger.error(f"[IngressAggregator] Monitoring error: {e}")
            time.sleep(5)


def initialize():
    """Initialize the ingress aggregator."""
    global _monitoring

    logger.info("[IngressAggregator] Initializing")

    # Initial collection
    _calculate_traffic_delta()

    # Start background monitoring
    _monitoring = True
    monitor_thread = threading.Thread(target=_monitor_ingress, daemon=True)
    monitor_thread.start()

    logger.info("[IngressAggregator] Monitoring started")


def get_current_ingress() -> Dict[str, Any]:
    """Get current ingress state across all interfaces."""
    with _stats_lock:
        if not _interface_stats:
            return {"interfaces": [], "timestamp": datetime.utcnow().isoformat()}
        return _interface_stats.copy()


def get_interface_by_role(role: str) -> List[Dict[str, Any]]:
    """Get all interfaces matching a role."""
    current = get_current_ingress()
    return [i for i in current.get("interfaces", []) if i.get("role") == role]


def get_physical_interfaces() -> List[Dict[str, Any]]:
    """Get all physical network interfaces."""
    return get_interface_by_role("physical")


def get_mesh_interfaces() -> List[Dict[str, Any]]:
    """Get all mesh VPN interfaces (Tailscale, Wireguard, etc)."""
    return get_interface_by_role("mesh_vpn")


def get_container_interfaces() -> List[Dict[str, Any]]:
    """Get all container overlay interfaces."""
    return get_interface_by_role("container_overlay")


def calculate_host_confidence_score(host_ip: str, metadata: Dict) -> float:
    """Calculate confidence score for a discovered host.

    Args:
        host_ip: IP address of the host
        metadata: Dict with optional fields:
            - is_new: bool (first time seen)
            - foreign_asn: bool (different ASN)
            - suspicious_ports: list (unusual service ports)
            - protocol_entropy: float (0-1, protocol randomness)
            - dns_anomaly: bool (suspicious DNS behavior)
            - lateral_behavior: bool (east-west movement)

    Returns:
        Confidence score (0-100) indicating capture priority
    """
    score = 0.0

    if metadata.get("is_new"):
        score += HOST_CONFIDENCE_WEIGHTS["unseen_ip"]

    if metadata.get("foreign_asn"):
        score += HOST_CONFIDENCE_WEIGHTS["foreign_asn"]

    if metadata.get("suspicious_ports"):
        score += HOST_CONFIDENCE_WEIGHTS["suspicious_ports"]

    protocol_entropy = metadata.get("protocol_entropy", 0)
    if protocol_entropy > 0.5:
        score += HOST_CONFIDENCE_WEIGHTS["protocol_entropy"] * protocol_entropy

    if metadata.get("dns_anomaly"):
        score += HOST_CONFIDENCE_WEIGHTS["dns_anomaly"]

    if metadata.get("lateral_behavior"):
        score += HOST_CONFIDENCE_WEIGHTS["lateral_behavior"]

    return min(score, 100.0)


def determine_capture_tier(confidence_score: float) -> str:
    """Determine capture tier based on confidence score.

    Tier 1 (<20): Registry only
    Tier 2 (20-40): Micro pcap (15s, truncated)
    Tier 3 (40-60): Standard pcap + Zeek extraction
    Tier 4 (60+): Full escalation (rotation, TLS fingerprinting, RF correlation)
    """
    if confidence_score < 20:
        return "registry_only"
    elif confidence_score < 40:
        return "micro_pcap"
    elif confidence_score < 60:
        return "standard_pcap"
    else:
        return "full_escalation"


def build_capture_command(host_ip: str, tier: str, interface: Optional[str] = None) -> Optional[List[str]]:
    """Build tcpdump command for the appropriate tier.

    Args:
        host_ip: Target IP address
        tier: Capture tier (micro_pcap, standard_pcap, etc)
        interface: Specific interface to capture on (None = any)

    Returns:
        List of command arguments for subprocess.run(), or None
    """
    if tier == "registry_only":
        return None

    iface_args = ["-i", interface] if interface else ["-i", "any"]
    output_file = f"/ftp_share/pcapng/discovered/{tier}_{host_ip.replace('.', '_')}.pcapng"

    if tier == "micro_pcap":
        return (
            ["tcpdump"] +
            iface_args +
            ["-s", str(PCAP_TRUNCATE_SIZE),  # Truncate to metadata
             "-G", str(CAPTURE_SECONDS),
             "-W", "1",
             "-w", output_file,
             "-n",
             f"host {host_ip}"]
        )

    elif tier in ("standard_pcap", "full_escalation"):
        return (
            ["tcpdump"] +
            iface_args +
            ["-s", "0",  # Full packets
             "-G", str(CAPTURE_SECONDS),
             "-W", "3",  # Keep 3 rotation files
             "-w", output_file,
             "-n",
             f"host {host_ip}"]
        )

    return None


def get_ingress_summary() -> Dict[str, Any]:
    """Get high-level summary of ingress activity."""
    ingress = get_current_ingress()
    interfaces = ingress.get("interfaces", [])

    summary = {
        "timestamp": ingress.get("timestamp"),
        "interface_count": len(interfaces),
        "total_rx_mbps": sum(i.get("rx_mbps", 0) for i in interfaces),
        "total_tx_mbps": sum(i.get("tx_mbps", 0) for i in interfaces),
        "by_role": defaultdict(lambda: {"count": 0, "rx_mbps": 0, "tx_mbps": 0})
    }

    for iface in interfaces:
        role = iface.get("role", "unknown")
        summary["by_role"][role]["count"] += 1
        summary["by_role"][role]["rx_mbps"] += iface.get("rx_mbps", 0)
        summary["by_role"][role]["tx_mbps"] += iface.get("tx_mbps", 0)

    summary["by_role"] = dict(summary["by_role"])

    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    initialize()

    print("\n[IngressAggregator] Testing...")

    time.sleep(2)

    ingress = get_current_ingress()
    print(f"\n✓ Current Ingress State:")
    print(json.dumps(ingress, indent=2))

    summary = get_ingress_summary()
    print(f"\n✓ Ingress Summary:")
    print(json.dumps(summary, indent=2))

    # Test confidence scoring
    test_hosts = [
        ("8.8.8.8", {"is_new": True, "foreign_asn": True}),
        ("192.168.1.1", {"is_new": False}),
        ("10.0.0.50", {"is_new": True, "lateral_behavior": True, "suspicious_ports": [4444, 5555]}),
    ]

    print(f"\n✓ Host Confidence Scores:")
    for ip, metadata in test_hosts:
        score = calculate_host_confidence_score(ip, metadata)
        tier = determine_capture_tier(score)
        print(f"  {ip}: {score:.1f} → {tier}")
