"""
pcap_ingest.py — Batch PCAP → Session Hypergraph Ingestion Pipeline.

Fetches PCAPs from an FTP source, decodes packets with scapy,
performs deterministic sessionization (5-tuple + time bucket),
and materializes SESSION subgraphs into the HypergraphEngine
with full provenance and ledger registration.

Design Principles:
    - PCAPs are not evidence until sessionized.
    - Sessions are not knowledge until ledgered.
    - Knowledge is not safe until exhaustion is enforced.
    - Every session ID is deterministic: same PCAP → same sessions.
    - All emitted nodes/edges have source = "pcap_ingest" (sensor-grade).

Usage:
    from pcap_ingest import PcapIngestPipeline, IngestConfig
    pipeline = PcapIngestPipeline(hypergraph_engine, config=IngestConfig(
        ftp_url="ftp://172.234.197.23",
    ))
    result = pipeline.ingest_all()  # batch: FTP → decode → sessionize → emit

    # Or as CLI:
    python pcap_ingest.py --ftp ftp://172.234.197.23 --staging /tmp/pcaps
"""
from __future__ import annotations

import hashlib
import io
import ipaddress
import json
import logging
import math
import os
import re
import shutil
import struct
import subprocess
import tempfile
import time
import urllib.request
import urllib.error
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ── Optional protocol-expectation scorer ─────────────────────────────────────
try:
    from protocol_intel import get_protocol_intel as _get_pi
    _HAS_PROTO_INTEL = True
except ImportError:
    _HAS_PROTO_INTEL = False
    logger.debug("[pcap_ingest] protocol_intel not found — anomaly scoring disabled")

# ── Optional GeoIP (maxminddb) ───────────────────────────────────────────────
try:
    import maxminddb
    HAS_MAXMINDDB = True
except ImportError:
    HAS_MAXMINDDB = False
    # look for geoip database files so the operator gets helpful advice
    db_candidates = [
        "assets/GeoLite2-City.mmdb",
        "assets/GeoLite2-ASN.mmdb",
        "assets/GeoLite2-Country.mmdb",
    ]
    found = [p for p in db_candidates if os.path.isfile(p)]
    if found:
        logger.info(
            "[pcap_ingest] maxminddb not installed — GeoIP enrichment disabled. "
            "MMDB files detected: %s. Install the Python package to enable GeoIP lookups "
            "(e.g. `pip install maxminddb`).",
            ", ".join(found),
        )
    else:
        logger.info(
            "[pcap_ingest] maxminddb not installed — GeoIP enrichment disabled. "
            "No MMDB files were found in assets, so even with the library installed "
            "there would be nothing to query."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class IngestConfig:
    """Configuration for the PCAP ingestion pipeline."""
    ftp_url: str = "ftp://172.234.197.23"       # FTP server base URL
    staging_dir: str = "/tmp/pcap_staging"       # Local directory for downloaded PCAPs
    session_window_sec: int = 30                 # Time bucket for sessionization (seconds)
    max_packets_per_session: int = 50_000        # Cap to prevent memory explosion
    emit_protocol_events: bool = True            # Create PROTOCOL_EVENT nodes
    register_ledger: bool = True                 # Register sessions with IEL
    source_tag: str = "pcap_ingest"              # Provenance source identifier
    parallel_decode: bool = False                # Future: parallel PCAP decode
    skip_existing: bool = True                   # Skip PCAPs already ingested
    normalize_timestamps: bool = True            # Force UTC normalization
    min_session_packets: int = 2                 # Discard sessions with fewer packets
    # ── GeoIP enrichment ──────────────────────────────────────────────
    enable_geoip: bool = True                    # Resolve host IPs to lat/lon (requires maxminddb Python package)
    geoip_city_mmdb: Optional[str] = "assets/GeoLite2-City.mmdb"
    geoip_asn_mmdb: Optional[str] = "assets/GeoLite2-ASN.mmdb"
    # ── DPI enrichment ────────────────────────────────────────────────
    enable_dpi: bool = True                      # Extract DNS/TLS/HTTP from packets
    # ── Flow / port-hub / service nodes ───────────────────────────────
    emit_flow_nodes: bool = True                 # Create flow, port_hub, service nodes
    max_flow_entities: int = 2000                # Cap flow nodes per PCAP
    # ── Behavioral Session Groups ─────────────────────────────────────
    enable_bsg: bool = True                      # Run BSG detection after sessionization


def _get_writebus_instance() -> Optional[Any]:
    """Return the process WriteBus singleton when the server initialized it."""
    try:
        import writebus
        return writebus.bus()
    except Exception:
        return None


class _DirectGraphWriter:
    """Compatibility writer for CLI/tests where WriteBus is not initialized."""

    uses_writebus = False

    def __init__(self, engine: Any, config: IngestConfig):
        self._engine = engine
        self._config = config

    def __getattr__(self, name: str) -> Any:
        return getattr(self._engine, name)

    @property
    def pending_ops(self) -> int:
        return 0

    def add_node(self, node: Dict[str, Any]) -> Any:
        return self._engine.add_node(node)

    def add_edge(self, edge: Dict[str, Any]) -> Any:
        return self._engine.add_edge(edge)

    def flush(
        self,
        *,
        entity_id: str,
        entity_type: str,
        entity_data: Dict[str, Any],
        room_name: str = "Global",
        request_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        evidence_refs: Optional[List[str]] = None,
        idempotency_key: Optional[str] = None,
    ) -> None:
        return None

    def discard_pending(self) -> None:
        return None


class _WriteBusGraphWriter:
    """Queues graph mutations and flushes them through WriteBus.commit()."""

    uses_writebus = True

    def __init__(self, engine: Any, config: IngestConfig, writebus_instance: Any):
        self._engine = engine
        self._config = config
        self._writebus = writebus_instance
        self._ops: List[Any] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._engine, name)

    @property
    def pending_ops(self) -> int:
        return len(self._ops)

    def add_node(self, node: Dict[str, Any]) -> str:
        payload = dict(node or {})
        entity_id = payload.get("id") or payload.get("node_id")
        if not entity_id:
            raise ValueError("pcap_ingest attempted to emit a node without an id")
        self._ops.append(self._graph_op("NODE_UPDATE", str(entity_id), payload))
        return str(entity_id)

    def add_edge(self, edge: Dict[str, Any]) -> str:
        payload = dict(edge or {})
        entity_id = payload.get("id")
        if not entity_id:
            kind = payload.get("kind", "edge")
            members = payload.get("nodes") or payload.get("members") or []
            entity_id = "e:pcap:auto:" + hashlib.sha256(
                json.dumps([kind, members], sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()[:16]
            payload["id"] = entity_id
        self._ops.append(self._graph_op("EDGE_UPDATE", str(entity_id), payload))
        return str(entity_id)

    @staticmethod
    def _graph_op(event_type: str, entity_id: str, entity_data: Dict[str, Any]) -> Any:
        from writebus import GraphOp
        return GraphOp(event_type=event_type, entity_id=entity_id, entity_data=entity_data)

    def flush(
        self,
        *,
        entity_id: str,
        entity_type: str,
        entity_data: Dict[str, Any],
        room_name: str = "Global",
        request_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        evidence_refs: Optional[List[str]] = None,
        idempotency_key: Optional[str] = None,
    ) -> None:
        if not self._ops:
            return
        from writebus import WriteContext

        ops = list(self._ops)
        ctx = WriteContext(
            room_name=room_name,
            operator_id="SYSTEM:PCAP_INGEST",
            request_id=request_id,
            source=self._config.source_tag,
            evidence_refs=list(evidence_refs or []),
            correlation_id=correlation_id or request_id,
        )
        result = self._writebus.commit(
            entity_id=entity_id,
            entity_type=entity_type,
            entity_data=entity_data,
            graph_ops=ops,
            ctx=ctx,
            persist=False,
            audit=True,
            idempotency_key=idempotency_key,
        )
        if not result.ok:
            raise RuntimeError("; ".join(result.errors) or result.commit_status)
        self._ops.clear()

    def discard_pending(self) -> None:
        self._ops.clear()


def _make_graph_writer(engine: Any, config: IngestConfig) -> Any:
    writebus_instance = _get_writebus_instance()
    if writebus_instance is not None:
        return _WriteBusGraphWriter(engine, config, writebus_instance)
    return _DirectGraphWriter(engine, config)


# ─────────────────────────────────────────────────────────────────────────────
# Deterministic Session Key
# ─────────────────────────────────────────────────────────────────────────────

def _session_key(
    src_ip: str, src_port: Optional[int],
    dst_ip: str, dst_port: Optional[int],
    proto: str, time_bucket: int,
) -> str:
    """Build a canonical session key string.

    The key is direction-normalized: (A→B) and (B→A) within the same
    time bucket collapse into the same session.
    """
    # Normalize direction: lower IP first (or lower port if IPs equal)
    endpoint_a = (src_ip, src_port or 0)
    endpoint_b = (dst_ip, dst_port or 0)
    if endpoint_a > endpoint_b:
        endpoint_a, endpoint_b = endpoint_b, endpoint_a

    return (
        f"{endpoint_a[0]}:{endpoint_a[1]}-"
        f"{endpoint_b[0]}:{endpoint_b[1]}-"
        f"{proto}-{time_bucket}"
    )


def _session_id(key: str) -> str:
    """Deterministic session ID from canonical key.

    Invariant: same PCAP → same key → same session ID.
    """
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return f"SESSION-{h}"


def _pcap_artifact_id(filename: str) -> str:
    """Deterministic artifact ID from PCAP filename."""
    name = Path(filename).stem  # strip extension
    h = hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
    return f"PCAP:{name}:{h}"


def _host_id(ip: str) -> str:
    """Deterministic host node ID."""
    return f"host:{ip}"


def _flow_id(session_key: str, index: int) -> str:
    """Deterministic flow ID within a session."""
    h = hashlib.sha256(f"{session_key}:flow:{index}".encode()).hexdigest()[:12]
    return f"flow:{h}"


# ─────────────────────────────────────────────────────────────────────────────
# Packet Metadata (lightweight — no storing raw bytes in the graph)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PacketMeta:
    """Lightweight packet metadata extracted from scapy."""
    timestamp: float
    src_ip: str
    dst_ip: str
    src_port: Optional[int]
    dst_port: Optional[int]
    proto: str              # TCP, UDP, ICMP, etc.
    length: int
    flags: str = ""         # TCP flags if applicable
    ttl: int = 0
    payload_size: int = 0
    # ── DPI fields (populated when enable_dpi=True) ──────────────────
    dns_qname: Optional[str] = None          # DNS query name
    dns_answers: Optional[List[Dict]] = None # DNS response records
    tls_sni: Optional[str] = None            # TLS ClientHello SNI
    http_host: Optional[str] = None          # HTTP Host header


@dataclass
class SessionData:
    """A decoded session: metadata + packet list."""
    session_id: str
    session_key: str
    packets: List[PacketMeta]
    time_bucket: int
    src_ip: str
    dst_ip: str
    src_port: Optional[int]
    dst_port: Optional[int]
    proto: str
    pcap_file: str

    @property
    def packet_count(self) -> int:
        return len(self.packets)

    @property
    def total_bytes(self) -> int:
        return sum(p.length for p in self.packets)

    @property
    def start_time(self) -> float:
        return min(p.timestamp for p in self.packets) if self.packets else 0

    @property
    def end_time(self) -> float:
        return max(p.timestamp for p in self.packets) if self.packets else 0

    @property
    def duration_sec(self) -> float:
        return self.end_time - self.start_time

    @property
    def protocols(self) -> List[str]:
        return list(set(p.proto for p in self.packets))

    @property
    def tcp_flags(self) -> Set[str]:
        return set(f for p in self.packets if p.flags for f in p.flags)

    # ── DPI aggregates ───────────────────────────────────────────────
    @property
    def dns_names(self) -> Dict[str, List[Dict]]:
        """Aggregate DNS qname → answers from all packets in this session."""
        names: Dict[str, List[Dict]] = defaultdict(list)
        for p in self.packets:
            if p.dns_qname:
                names.setdefault(p.dns_qname, [])
            if p.dns_qname and p.dns_answers:
                names[p.dns_qname].extend(p.dns_answers)
        return dict(names)

    @property
    def tls_snis(self) -> Set[str]:
        """Unique TLS SNI values observed in this session."""
        return {p.tls_sni for p in self.packets if p.tls_sni}

    @property
    def http_hosts(self) -> Set[str]:
        """Unique HTTP Host header values observed in this session."""
        return {p.http_host for p in self.packets if p.http_host}


# ─────────────────────────────────────────────────────────────────────────────
# Ingest Result
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class IngestResult:
    """Result of a single PCAP ingestion."""
    pcap_file: str
    pcap_artifact_id: str
    sessions_created: int = 0
    nodes_emitted: int = 0
    edges_emitted: int = 0
    packets_decoded: int = 0
    packets_skipped: int = 0
    errors: List[str] = field(default_factory=list)
    duration_sec: float = 0.0
    session_ids: List[str] = field(default_factory=list)
    geo_points: List[Dict[str, Any]] = field(default_factory=list)  # GeoIP resolved hosts
    dpi_stats: Dict[str, int] = field(default_factory=lambda: {"dns_names": 0, "tls_snis": 0, "http_hosts": 0})
    bsg_summary: Optional[Dict[str, Any]] = None  # BSG detection results

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BatchIngestResult:
    """Result of batch (multi-PCAP) ingestion."""
    pcaps_processed: int = 0
    pcaps_skipped: int = 0
    pcaps_failed: int = 0
    total_sessions: int = 0
    total_nodes: int = 0
    total_edges: int = 0
    total_packets: int = 0
    duration_sec: float = 0.0
    per_file: List[IngestResult] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    geo_points: List[Dict[str, Any]] = field(default_factory=list)  # All GeoIP resolved hosts
    dpi_stats: Dict[str, int] = field(default_factory=lambda: {"dns_names": 0, "tls_snis": 0, "http_hosts": 0})
    bsg_summary: Optional[Dict[str, Any]] = None  # BSG detection results

    @property
    def ok(self) -> bool:
        return self.pcaps_failed == 0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["per_file"] = [r.to_dict() for r in self.per_file]
        return d

    def summary(self) -> str:
        """Operator-facing summary."""
        lines = [
            f"PCAP Ingestion Complete: {self.pcaps_processed} files → "
            f"{self.total_sessions} sessions",
            f"  Nodes: {self.total_nodes}  Edges: {self.total_edges}  "
            f"Packets: {self.total_packets}",
            f"  Duration: {self.duration_sec:.1f}s",
        ]
        if self.pcaps_skipped:
            lines.append(f"  Skipped (already ingested): {self.pcaps_skipped}")
        if self.pcaps_failed:
            lines.append(f"  Failed: {self.pcaps_failed}")
        for err in self.errors[:5]:
            lines.append(f"  ERROR: {err}")
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# FTP Fetcher
# ─────────────────────────────────────────────────────────────────────────────

class FTPFetcher:
    """Simple FTP fetcher using urllib (no lftp dependency)."""

    def __init__(self, base_url: str, staging_dir: str):
        self.base_url = base_url.rstrip("/")
        self.staging_dir = staging_dir
        os.makedirs(staging_dir, exist_ok=True)

    def list_pcaps(self) -> List[str]:
        """List .pcap files available on the FTP server."""
        try:
            # FTP directory listing
            url = self.base_url + "/"
            with urllib.request.urlopen(url, timeout=15) as resp:
                listing = resp.read().decode("utf-8", errors="replace")

            # Parse FTP listing — each line has filename at the end
            pcaps = []
            for line in listing.strip().splitlines():
                parts = line.split()
                if not parts:
                    continue
                fname = parts[-1]
                if fname.lower().endswith((".pcap", ".pcapng", ".cap")):
                    pcaps.append(fname)

            logger.info("[pcap_ingest] FTP listing: %d PCAPs found", len(pcaps))
            return pcaps

        except Exception as e:
            logger.error("[pcap_ingest] FTP listing failed: %s", e)
            raise

    def fetch(self, filename: str) -> Path:
        """Download a single PCAP to staging.  Returns local path.

        Skips download if the file already exists with matching size.
        Uses streaming download with a 120-second timeout to avoid indefinite stalls.
        """
        local_path = Path(self.staging_dir) / filename
        remote_url = f"{self.base_url}/{filename}"

        # Check if already downloaded (size-based skip).
        # NOTE: For FTP, urlopen issues a full RETR so we only probe size when
        # a local copy already exists (avoids double-downloading).
        if local_path.exists():
            try:
                with urllib.request.urlopen(remote_url, timeout=15) as resp:
                    remote_size = int(resp.headers.get("Content-Length", 0))
                local_size = local_path.stat().st_size
                if remote_size > 0 and local_size == remote_size:
                    logger.info(
                        "[pcap_ingest] Skip download (size match): %s", filename,
                    )
                    return local_path
            except Exception:
                pass  # re-download on any probe failure

        # Streaming download with timeout — avoids indefinite stall on slow servers
        try:
            logger.info("[pcap_ingest] Downloading: %s", remote_url)
            with urllib.request.urlopen(remote_url, timeout=120) as resp:
                with open(str(local_path), 'wb') as fout:
                    shutil.copyfileobj(resp, fout, length=65536)
            logger.info(
                "[pcap_ingest] Downloaded: %s (%d bytes)",
                filename, local_path.stat().st_size,
            )
            return local_path
        except Exception as e:
            logger.error("[pcap_ingest] Download failed: %s — %s", filename, e)
            raise


# ─────────────────────────────────────────────────────────────────────────────
# Packet Decoder (scapy)
# ─────────────────────────────────────────────────────────────────────────────

def decode_pcap(filepath: Path, enable_dpi: bool = True) -> List[PacketMeta]:
    """Decode a PCAP file into lightweight PacketMeta objects.

    Uses scapy's rdpcap.  Only extracts IP-layer packets.
    Non-IP traffic (ARP, raw L2) is counted but skipped.

    When enable_dpi=True, also extracts:
      - DNS query names and response records
      - TLS ClientHello SNI
      - HTTP Host headers
    """
    from scapy.all import rdpcap, IP, TCP, UDP, ICMP

    packets: List[PacketMeta] = []
    skipped = 0

    try:
        raw_pkts = rdpcap(str(filepath))
    except Exception as e:
        logger.error("[pcap_ingest] Failed to read %s: %s", filepath, e)
        raise

    for pkt in raw_pkts:
        if not pkt.haslayer(IP):
            skipped += 1
            continue

        ip = pkt[IP]
        proto = "OTHER"
        src_port = None
        dst_port = None
        flags = ""
        payload_size = 0

        if pkt.haslayer(TCP):
            tcp = pkt[TCP]
            proto = "TCP"
            src_port = tcp.sport
            dst_port = tcp.dport
            flags = str(tcp.flags)
            payload_size = len(bytes(tcp.payload)) if tcp.payload else 0
        elif pkt.haslayer(UDP):
            udp = pkt[UDP]
            proto = "UDP"
            src_port = udp.sport
            dst_port = udp.dport
            payload_size = len(bytes(udp.payload)) if udp.payload else 0
        elif pkt.haslayer(ICMP):
            proto = "ICMP"

        # ── DPI extraction ───────────────────────────────────────────
        dns_qname = None
        dns_answers_list = None
        tls_sni = None
        http_host = None

        if enable_dpi:
            # DNS extraction
            try:
                from scapy.layers.dns import DNS, DNSQR, DNSRR
                if pkt.haslayer(DNS):
                    dns = pkt[DNS]
                    if dns.qr == 0 and dns.qd:  # query
                        qn = dns.qd.qname
                        if isinstance(qn, bytes):
                            qn = qn.decode('utf-8', errors='ignore')
                        dns_qname = qn.rstrip('.')
                    elif dns.qr == 1 and dns.qd:  # response
                        qn = dns.qd.qname
                        if isinstance(qn, bytes):
                            qn = qn.decode('utf-8', errors='ignore')
                        dns_qname = qn.rstrip('.')
                        answers = []
                        if dns.an:
                            for i in range(min(dns.ancount, 20)):
                                try:
                                    rr = dns.an[i] if isinstance(dns.an, list) else dns.an
                                    rdata = str(rr.rdata) if hasattr(rr, 'rdata') else str(rr)
                                    rtype = int(rr.type) if hasattr(rr, 'type') else 0
                                    answers.append({"answer": rdata, "type": rtype})
                                except Exception:
                                    break
                        if answers:
                            dns_answers_list = answers
            except ImportError:
                pass
            except Exception:
                pass

            # TLS SNI extraction (ClientHello)
            if pkt.haslayer(TCP) and dst_port == 443 and payload_size > 5:
                try:
                    raw = bytes(pkt[TCP].payload)
                    tls_sni = _extract_tls_sni(raw)
                except Exception:
                    pass

            # HTTP Host header extraction
            if pkt.haslayer(TCP) and dst_port in (80, 8080, 8888) and payload_size > 10:
                try:
                    raw = bytes(pkt[TCP].payload)
                    http_host = _extract_http_host(raw)
                except Exception:
                    pass

        packets.append(PacketMeta(
            timestamp=float(pkt.time),
            src_ip=ip.src,
            dst_ip=ip.dst,
            src_port=src_port,
            dst_port=dst_port,
            proto=proto,
            length=len(pkt),
            flags=flags,
            ttl=ip.ttl,
            payload_size=payload_size,
            dns_qname=dns_qname,
            dns_answers=dns_answers_list,
            tls_sni=tls_sni,
            http_host=http_host,
        ))

    logger.info(
        "[pcap_ingest] Decoded %s: %d IP packets, %d non-IP skipped",
        filepath.name, len(packets), skipped,
    )
    return packets


def _extract_tls_sni(raw: bytes) -> Optional[str]:
    """Extract SNI from a TLS ClientHello payload."""
    if len(raw) < 44 or raw[0] != 0x16:  # TLS handshake
        return None
    # Skip TLS record header (5 bytes) + handshake header (4 bytes)
    # + client version (2) + random (32) = 43 bytes
    pos = 5 + 4 + 2 + 32
    if pos >= len(raw):
        return None
    # Session ID length
    sid_len = raw[pos]
    pos += 1 + sid_len
    if pos + 2 >= len(raw):
        return None
    # Cipher suites length
    cs_len = struct.unpack("!H", raw[pos:pos+2])[0]
    pos += 2 + cs_len
    if pos >= len(raw):
        return None
    # Compression methods length
    cm_len = raw[pos]
    pos += 1 + cm_len
    if pos + 2 >= len(raw):
        return None
    # Extensions length
    ext_len = struct.unpack("!H", raw[pos:pos+2])[0]
    pos += 2
    end = min(pos + ext_len, len(raw))
    while pos + 4 < end:
        ext_type = struct.unpack("!H", raw[pos:pos+2])[0]
        ext_dlen = struct.unpack("!H", raw[pos+2:pos+4])[0]
        pos += 4
        if ext_type == 0 and ext_dlen > 5:  # SNI extension
            # SNI list length (2) + type (1) + name length (2)
            sni_list_len = struct.unpack("!H", raw[pos:pos+2])[0]
            name_type = raw[pos+2]
            name_len = struct.unpack("!H", raw[pos+3:pos+5])[0]
            if name_type == 0 and pos + 5 + name_len <= end:
                return raw[pos+5:pos+5+name_len].decode('ascii', errors='ignore')
        pos += ext_dlen
    return None


def _extract_http_host(raw: bytes) -> Optional[str]:
    """Extract Host header from an HTTP request."""
    try:
        text = raw[:2048].decode('ascii', errors='ignore')
        if not (text.startswith('GET ') or text.startswith('POST ') or
                text.startswith('PUT ') or text.startswith('HEAD ') or
                text.startswith('DELETE ') or text.startswith('PATCH ')):
            return None
        for line in text.split('\r\n')[1:10]:
            if line.lower().startswith('host:'):
                return line[5:].strip()
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Sessionizer
# ─────────────────────────────────────────────────────────────────────────────

def sessionize(
    packets: List[PacketMeta],
    pcap_file: str,
    window_sec: int = 30,
    min_packets: int = 2,
) -> List[SessionData]:
    """Deterministic sessionization: 5-tuple + time bucket.

    Invariant: same packets in → same sessions out (order-independent
    within each bucket thanks to direction-normalized keys).
    """
    buckets: Dict[str, List[PacketMeta]] = defaultdict(list)
    bucket_meta: Dict[str, dict] = {}

    for pkt in packets:
        tb = int(pkt.timestamp) - (int(pkt.timestamp) % window_sec)
        key = _session_key(
            pkt.src_ip, pkt.src_port,
            pkt.dst_ip, pkt.dst_port,
            pkt.proto, tb,
        )
        buckets[key].append(pkt)

        if key not in bucket_meta:
            bucket_meta[key] = {
                "time_bucket": tb,
                "src_ip": pkt.src_ip,
                "dst_ip": pkt.dst_ip,
                "src_port": pkt.src_port,
                "dst_port": pkt.dst_port,
                "proto": pkt.proto,
            }

    sessions = []
    for key, pkts in buckets.items():
        if len(pkts) < min_packets:
            continue
        meta = bucket_meta[key]
        sessions.append(SessionData(
            session_id=_session_id(key),
            session_key=key,
            packets=pkts,
            time_bucket=meta["time_bucket"],
            src_ip=meta["src_ip"],
            dst_ip=meta["dst_ip"],
            src_port=meta["src_port"],
            dst_port=meta["dst_port"],
            proto=meta["proto"],
            pcap_file=pcap_file,
        ))

    # Sort by time_bucket for deterministic ordering
    sessions.sort(key=lambda s: (s.time_bucket, s.session_id))
    logger.info(
        "[pcap_ingest] Sessionized %s: %d sessions from %d packets "
        "(window=%ds, min_pkts=%d)",
        pcap_file, len(sessions), len(packets), window_sec, min_packets,
    )
    return sessions


# ─────────────────────────────────────────────────────────────────────────────
# Hypergraph Emitter
# ─────────────────────────────────────────────────────────────────────────────

class HypergraphEmitter:
    """Materializes session data as nodes + edges in the HypergraphEngine.

    Node kinds:
        - session        (SESSION-xxxx)
        - host           (host:10.0.0.1)
        - pcap_artifact  (PCAP:capture_name:hash)
        - protocol_event (optional — TLS/DNS/HTTP events within session)
        - geo_point      (GeoIP lat/lon → geo_xx.xxxxx_yy.yyyyy)
        - asn            (ASN number → asn:NNNNN)
        - org            (Organization → org:name)
        - flow           (per-session flow → flow:xxxx)
        - port_hub       (proto:port pair → port:tcp:443)
        - service        (inferred service → svc:https)
        - dns_name       (DNS query → dns:example.com)
        - tls_sni        (TLS SNI → tls_sni:example.com)
        - http_host      (HTTP Host → http_host:example.com)

    Edge kinds:
        - SESSION_CONTAINS_FLOW
        - SESSION_DERIVED_FROM_PCAP
        - FLOW_FROM_HOST
        - FLOW_TO_HOST
        - SESSION_BETWEEN_HOSTS
        - HOST_GEO_ESTIMATE
        - HOST_IN_ASN
        - ASN_IN_ORG
        - SESSION_OBSERVED_HOST
        - SESSION_OBSERVED_FLOW
        - FLOW_DST_PORT
        - PORT_IMPLIED_SERVICE
        - flow_observed (hyperedge)
        - FLOW_QUERIED_DNS
        - FLOW_TLS_SNI
        - FLOW_HTTP_HOST

    All nodes/edges carry provenance.source = "pcap_ingest" (sensor-grade).
    """

    # ── Well-known service port map ──────────────────────────────────
    _SERVICE_MAP = {
        22: "ssh", 25: "smtp", 53: "dns", 80: "http", 110: "pop3",
        143: "imap", 443: "https", 993: "imaps", 995: "pop3s",
        3306: "mysql", 3389: "rdp", 5432: "postgres", 5900: "vnc",
        6379: "redis", 8080: "http-alt", 8443: "https-alt", 8888: "http-alt",
    }

    def __init__(self, engine: Any, config: IngestConfig):
        self.engine = _make_graph_writer(engine, config)
        self.config = config
        self._emitted_hosts: Set[str] = set()  # dedup hosts across sessions
        self._emitted_ids: Set[str] = set()     # dedup all geo/asn/org/service/port nodes
        self.geo_points: List[Dict[str, Any]] = []  # accumulated GeoIP results
        self.dpi_stats: Dict[str, int] = {"dns_names": 0, "tls_snis": 0, "http_hosts": 0}

        # ── GeoIP readers (lazy, opened once) ────────────────────────
        self._geoip_city_reader = None
        self._geoip_asn_reader = None
        if HAS_MAXMINDDB and config.enable_geoip:
            if config.geoip_city_mmdb and os.path.isfile(config.geoip_city_mmdb):
                try:
                    self._geoip_city_reader = maxminddb.open_database(config.geoip_city_mmdb)
                    logger.info(f"[pcap_ingest][GeoIP] City DB loaded: {config.geoip_city_mmdb}")
                except Exception as exc:
                    logger.warning(f"[pcap_ingest][GeoIP] City DB failed: {exc}")
            if config.geoip_asn_mmdb and os.path.isfile(config.geoip_asn_mmdb):
                try:
                    self._geoip_asn_reader = maxminddb.open_database(config.geoip_asn_mmdb)
                    logger.info(f"[pcap_ingest][GeoIP] ASN DB loaded: {config.geoip_asn_mmdb}")
                except Exception as exc:
                    logger.warning(f"[pcap_ingest][GeoIP] ASN DB failed: {exc}")

    def flush(
        self,
        *,
        entity_id: str,
        entity_type: str,
        entity_data: Dict[str, Any],
        request_id: Optional[str],
        evidence_refs: List[str],
        idempotency_key: str,
        room_name: str = "Global",
    ) -> None:
        self.engine.flush(
            entity_id=entity_id,
            entity_type=entity_type,
            entity_data=entity_data,
            room_name=room_name,
            request_id=request_id,
            correlation_id=entity_id,
            evidence_refs=evidence_refs,
            idempotency_key=idempotency_key,
        )

    def discard_pending(self) -> None:
        self.engine.discard_pending()

    # ── GeoIP lookup ─────────────────────────────────────────────────
    def _geoip_lookup(self, ip: str) -> Optional[Dict[str, Any]]:
        """Return {lat, lon, city, country, org, asn} for ip, or None."""
        try:
            addr = ipaddress.ip_address(ip)
            if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast:
                return None
        except ValueError:
            return None

        lat = lon = None
        city = country = org = ""
        asn_num = None

        if self._geoip_city_reader:
            try:
                rec = self._geoip_city_reader.get(ip)
                if rec:
                    loc = rec.get("location", {})
                    lat = loc.get("latitude")
                    lon = loc.get("longitude")
                    city_obj = rec.get("city", {})
                    city = (city_obj.get("names", {}).get("en", "")) if city_obj else ""
                    country_obj = rec.get("country", {})
                    country = (country_obj.get("iso_code", "")) if country_obj else ""
            except Exception:
                pass

        if self._geoip_asn_reader:
            try:
                asn_rec = self._geoip_asn_reader.get(ip)
                if asn_rec:
                    org = asn_rec.get("autonomous_system_organization", "")
                    asn_num = asn_rec.get("autonomous_system_number")
            except Exception:
                pass

        if lat is not None and lon is not None:
            return {"lat": lat, "lon": lon, "city": city, "country": country, "org": org, "asn": asn_num}
        return None

    def emit_pcap_artifact(self, filename: str, file_size: int = 0) -> str:
        """Emit a PCAP_ARTIFACT node (one per PCAP file)."""
        artifact_id = _pcap_artifact_id(filename)
        self.engine.add_node({
            "id": artifact_id,
            "kind": "pcap_artifact",
            "labels": {
                "filename": filename,
                "file_size": file_size,
                "ingested_at": datetime.now(timezone.utc).isoformat(),
            },
            "metadata": {
                "provenance": {
                    "source": self.config.source_tag,
                    "evidence_type": "packet_capture",
                },
            },
        })
        return artifact_id

    def emit_session(
        self,
        session: SessionData,
        pcap_artifact_id: str,
    ) -> Tuple[int, int]:
        """Emit a complete session subgraph with GeoIP, DPI, and flow enrichment.

        Returns (nodes_emitted, edges_emitted).
        """
        nodes = 0
        edges = 0
        now_iso = datetime.now(timezone.utc).isoformat()

        # ── SESSION node ─────────────────────────────────────────────
        # ── Protocol-expectation anomaly scoring ─────────────────────
        proto_anomaly = None
        if _HAS_PROTO_INTEL:
            try:
                proto_anomaly = _get_pi().score_session(session)
            except Exception:
                pass

        session_labels: Dict[str, Any] = {
            "window_sec": self.config.session_window_sec,
            "protocols": session.protocols,
            "packet_count": session.packet_count,
            "total_bytes": session.total_bytes,
            "duration_sec": round(session.duration_sec, 2),
            "src_ip": session.src_ip,
            "dst_ip": session.dst_ip,
            "src_port": session.src_port,
            "dst_port": session.dst_port,
            "proto": session.proto,
            "tcp_flags": list(session.tcp_flags),
            "time_bucket": session.time_bucket,
            "start_time": session.start_time,
            "end_time": session.end_time,
        }
        if proto_anomaly is not None:
            session_labels["protocol_anomaly_score"] = proto_anomaly.anomaly_score
            session_labels["protocol_violations"] = [
                v.name for v in proto_anomaly.violations
            ]
            session_labels["expected_protocol"] = proto_anomaly.expected_proto

        self.engine.add_node({
            "id": session.session_id,
            "kind": "session",
            "labels": session_labels,
            "metadata": {
                "provenance": {
                    "source": self.config.source_tag,
                    "evidence_type": "packet_capture",
                    "pcap_file": session.pcap_file,
                    "session_key": session.session_key,
                },
                "confidence": "SENSOR",
                "ingested_at": now_iso,
            },
        })
        nodes += 1

        # ── HOST nodes + GeoIP enrichment (deduplicated) ────────────
        host_bytes: Dict[str, int] = defaultdict(int)
        for pkt in session.packets:
            host_bytes[pkt.src_ip] += pkt.length
            host_bytes[pkt.dst_ip] += pkt.length

        for ip in (session.src_ip, session.dst_ip):
            host_id = _host_id(ip)
            if host_id not in self._emitted_hosts:
                host_node: Dict[str, Any] = {
                    "id": host_id,
                    "kind": "host",
                    "labels": {"ip": ip, "bytes": host_bytes.get(ip, 0)},
                    "metadata": {
                        "provenance": {
                            "source": self.config.source_tag,
                            "evidence_type": "packet_capture",
                        },
                        "confidence": "SENSOR",
                    },
                }

                # ── GeoIP enrichment ─────────────────────────────────
                geo = self._geoip_lookup(ip) if self.config.enable_geoip else None
                if geo:
                    lat, lon = geo["lat"], geo["lon"]
                    host_node["position"] = [lat, lon, 0]
                    host_node["labels"]["city"] = geo["city"]
                    host_node["labels"]["country"] = geo["country"]
                    host_node["labels"]["org"] = geo["org"]

                    # geo_point node
                    gid = f"geo_{lat:.5f}_{lon:.5f}"
                    if gid not in self._emitted_ids:
                        self._emitted_ids.add(gid)
                        self.engine.add_node({
                            "id": gid, "kind": "geo_point",
                            "position": [lat, lon, 0],
                            "labels": {"city": geo["city"], "country": geo["country"]},
                        })
                        nodes += 1

                    # HOST_GEO_ESTIMATE edge
                    ge_id = f"e:hg:{host_id}:{gid}"
                    if ge_id not in self._emitted_ids:
                        self._emitted_ids.add(ge_id)
                        self.engine.add_edge({
                            "id": ge_id, "kind": "HOST_GEO_ESTIMATE",
                            "nodes": [host_id, gid], "weight": 0.6,
                            "metadata": {"source": "geoip", "confidence": 0.6},
                        })
                        edges += 1

                    # ASN node + HOST_IN_ASN edge
                    asn_num = geo.get("asn")
                    if asn_num:
                        aid = f"asn:{asn_num}"
                        if aid not in self._emitted_ids:
                            self._emitted_ids.add(aid)
                            self.engine.add_node({
                                "id": aid, "kind": "asn",
                                "labels": {"asn": asn_num, "org": geo["org"]},
                            })
                            nodes += 1
                        ha_id = f"e:ha:{host_id}:{aid}"
                        if ha_id not in self._emitted_ids:
                            self._emitted_ids.add(ha_id)
                            self.engine.add_edge({
                                "id": ha_id, "kind": "HOST_IN_ASN",
                                "nodes": [host_id, aid], "weight": 0.85,
                                "metadata": {"source": "geoip", "confidence": 0.85},
                            })
                            edges += 1

                        # Org node + ASN_IN_ORG edge
                        if geo["org"]:
                            oid = f"org:{geo['org']}"
                            if oid not in self._emitted_ids:
                                self._emitted_ids.add(oid)
                                self.engine.add_node({
                                    "id": oid, "kind": "org",
                                    "labels": {"name": geo["org"]},
                                })
                                nodes += 1
                            ao_id = f"e:ao:{aid}:{oid}"
                            if ao_id not in self._emitted_ids:
                                self._emitted_ids.add(ao_id)
                                self.engine.add_edge({
                                    "id": ao_id, "kind": "ASN_IN_ORG",
                                    "nodes": [aid, oid], "weight": 0.8,
                                    "metadata": {"source": "geoip", "confidence": 0.8},
                                })
                                edges += 1

                    # Accumulate geo_point for API response
                    self.geo_points.append({
                        "ip": ip, "bytes": host_bytes.get(ip, 0),
                        "lat": lat, "lon": lon,
                        "city": geo["city"], "country": geo["country"],
                        "org": geo["org"],
                    })

                self.engine.add_node(host_node)
                self._emitted_hosts.add(host_id)
                nodes += 1

        # ── SESSION_DERIVED_FROM_PCAP edge ───────────────────────────
        self.engine.add_edge({
            "id": f"e:derived:{session.session_id}:{pcap_artifact_id}",
            "kind": "SESSION_DERIVED_FROM_PCAP",
            "nodes": [session.session_id, pcap_artifact_id],
            "weight": 1.0,
            "labels": {"pcap_file": session.pcap_file},
            "metadata": {
                "provenance": {"source": self.config.source_tag},
                "confidence": "SENSOR",
            },
        })
        edges += 1

        # ── SESSION_OBSERVED_HOST edges ──────────────────────────────
        src_host = _host_id(session.src_ip)
        dst_host = _host_id(session.dst_ip)
        for hid in (src_host, dst_host):
            soh_id = f"e:soh:{session.session_id}:{hid}"
            if soh_id not in self._emitted_ids:
                self._emitted_ids.add(soh_id)
                self.engine.add_edge({
                    "id": soh_id, "kind": "SESSION_OBSERVED_HOST",
                    "nodes": [session.session_id, hid], "weight": 1.0,
                    "metadata": {"provenance": {"source": self.config.source_tag}},
                })
                edges += 1

        # ── SESSION_BETWEEN_HOSTS edges ──────────────────────────────
        self.engine.add_edge({
            "id": f"e:sbh:{session.session_id}:{src_host}:{dst_host}",
            "kind": "SESSION_BETWEEN_HOSTS",
            "nodes": [session.session_id, src_host, dst_host],
            "weight": 1.0,
            "labels": {
                "proto": session.proto,
                "packet_count": session.packet_count,
                "total_bytes": session.total_bytes,
            },
            "metadata": {
                "provenance": {"source": self.config.source_tag},
                "confidence": "SENSOR",
            },
        })
        edges += 1

        # ── FLOW_FROM_HOST / FLOW_TO_HOST edges ─────────────────────
        self.engine.add_edge({
            "id": f"e:from:{session.session_id}:{src_host}",
            "kind": "FLOW_FROM_HOST",
            "nodes": [session.session_id, src_host],
            "weight": 1.0,
            "labels": {"port": session.src_port},
            "metadata": {
                "provenance": {"source": self.config.source_tag},
                "confidence": "SENSOR",
            },
        })
        edges += 1

        self.engine.add_edge({
            "id": f"e:to:{session.session_id}:{dst_host}",
            "kind": "FLOW_TO_HOST",
            "nodes": [session.session_id, dst_host],
            "weight": 1.0,
            "labels": {"port": session.dst_port},
            "metadata": {
                "provenance": {"source": self.config.source_tag},
                "confidence": "SENSOR",
            },
        })
        edges += 1

        # ── Flow / port-hub / service nodes (optional) ───────────────
        if self.config.emit_flow_nodes:
            fn, fe = self._emit_flow_topology(session)
            nodes += fn
            edges += fe

        # ── DPI enrichment nodes (optional) ──────────────────────────
        if self.config.enable_dpi:
            dn, de = self._emit_dpi_enrichment(session)
            nodes += dn
            edges += de

        # ── Protocol events (optional) ───────────────────────────────
        if self.config.emit_protocol_events:
            pe_n, pe_e = self._emit_protocol_events(session)
            nodes += pe_n
            edges += pe_e

        return nodes, edges

    def _emit_flow_topology(self, session: SessionData) -> Tuple[int, int]:
        """Emit flow, port_hub, and service nodes matching pcap_registry topology."""
        nodes = 0
        edges = 0

        proto = session.proto.lower()
        dst_port = session.dst_port or 0

        # ── Flow node ────────────────────────────────────────────────
        fid = _flow_id(session.session_key, 0)
        if fid not in self._emitted_ids:
            self._emitted_ids.add(fid)
            self.engine.add_node({
                "id": fid, "kind": "flow",
                "labels": {
                    "proto": proto, "bytes": session.total_bytes,
                    "pkts": session.packet_count,
                    "src_ip": session.src_ip, "dst_ip": session.dst_ip,
                    "dst_port": dst_port,
                },
                "metadata": {
                    "provenance": {"source": self.config.source_tag},
                    "duration_sec": round(session.duration_sec, 2),
                },
            })
            nodes += 1

        # SESSION_OBSERVED_FLOW edge
        sof_id = f"e:sof:{session.session_id}:{fid}"
        if sof_id not in self._emitted_ids:
            self._emitted_ids.add(sof_id)
            self.engine.add_edge({
                "id": sof_id, "kind": "SESSION_OBSERVED_FLOW",
                "nodes": [session.session_id, fid], "weight": 1.0,
                "metadata": {"provenance": {"source": self.config.source_tag}},
            })
            edges += 1

        # ── Port-hub node ────────────────────────────────────────────
        pid = None
        if dst_port > 0:
            pid = f"port:{proto}:{dst_port}"
            if pid not in self._emitted_ids:
                self._emitted_ids.add(pid)
                self.engine.add_node({
                    "id": pid, "kind": "port_hub",
                    "labels": {"proto": proto, "port": dst_port},
                })
                nodes += 1

            # FLOW_DST_PORT edge
            fp_id = f"e:fp:{fid}:{pid}"
            if fp_id not in self._emitted_ids:
                self._emitted_ids.add(fp_id)
                self.engine.add_edge({
                    "id": fp_id, "kind": "FLOW_DST_PORT",
                    "nodes": [fid, pid], "weight": 1.0,
                })
                edges += 1

        # ── Service node (inferred from port) ────────────────────────
        svc_name = self._SERVICE_MAP.get(dst_port)
        if svc_name:
            sid = f"svc:{svc_name}"
            if sid not in self._emitted_ids:
                self._emitted_ids.add(sid)
                self.engine.add_node({
                    "id": sid, "kind": "service",
                    "labels": {"name": svc_name},
                })
                nodes += 1

            if pid:
                ps_id = f"e:ps:{pid}:{sid}"
                if ps_id not in self._emitted_ids:
                    self._emitted_ids.add(ps_id)
                    self.engine.add_edge({
                        "id": ps_id, "kind": "PORT_IMPLIED_SERVICE",
                        "nodes": [pid, sid], "weight": 0.7,
                        "metadata": {"obs_class": "implied", "confidence": 0.7},
                    })
                    edges += 1

        # ── flow_observed hyperedge (connects flow to hosts, port, service) ──
        src_hid = _host_id(session.src_ip)
        dst_hid = _host_id(session.dst_ip)
        he_members = [fid, src_hid, dst_hid]
        if pid:
            he_members.append(pid)
        if svc_name:
            he_members.append(f"svc:{svc_name}")
        he_id = f"e:fo:{fid}"
        if he_id not in self._emitted_ids:
            self._emitted_ids.add(he_id)
            self.engine.add_edge({
                "id": he_id, "kind": "flow_observed",
                "nodes": he_members, "weight": 1.0,
                "timestamp": time.time(),
            })
            edges += 1

        return nodes, edges

    def _emit_dpi_enrichment(self, session: SessionData) -> Tuple[int, int]:
        """Emit DNS, TLS SNI, and HTTP Host enrichment nodes + edges."""
        nodes = 0
        edges = 0

        fid = _flow_id(session.session_key, 0)

        # ── DNS names ────────────────────────────────────────────────
        for qname, answers in session.dns_names.items():
            if not qname:
                continue
            dnid = f"dns:{qname}"
            if dnid not in self._emitted_ids:
                self._emitted_ids.add(dnid)
                self.engine.add_node({
                    "id": dnid, "kind": "dns_name",
                    "labels": {"qname": qname, "answer_count": len(answers)},
                    "metadata": {"answers": answers[:20]},
                })
                nodes += 1
                self.dpi_stats["dns_names"] += 1

            # FLOW_QUERIED_DNS edge
            fd_id = f"e:fd:{fid}:{dnid}"
            if fd_id not in self._emitted_ids:
                self._emitted_ids.add(fd_id)
                self.engine.add_edge({
                    "id": fd_id, "kind": "FLOW_QUERIED_DNS",
                    "nodes": [fid, dnid], "weight": 1.0,
                })
                edges += 1

        # ── TLS SNIs ─────────────────────────────────────────────────
        for sni in session.tls_snis:
            if not sni:
                continue
            sni_id = f"tls_sni:{sni}"
            if sni_id not in self._emitted_ids:
                self._emitted_ids.add(sni_id)
                self.engine.add_node({
                    "id": sni_id, "kind": "tls_sni",
                    "labels": {"sni": sni},
                })
                nodes += 1
                self.dpi_stats["tls_snis"] += 1

            # FLOW_TLS_SNI edge
            fs_id = f"e:fs:{fid}:{sni_id}"
            if fs_id not in self._emitted_ids:
                self._emitted_ids.add(fs_id)
                self.engine.add_edge({
                    "id": fs_id, "kind": "FLOW_TLS_SNI",
                    "nodes": [fid, sni_id], "weight": 1.0,
                })
                edges += 1

        # ── HTTP Hosts ───────────────────────────────────────────────
        for host in session.http_hosts:
            if not host:
                continue
            hh_id = f"http_host:{host}"
            if hh_id not in self._emitted_ids:
                self._emitted_ids.add(hh_id)
                self.engine.add_node({
                    "id": hh_id, "kind": "http_host",
                    "labels": {"host": host},
                })
                nodes += 1
                self.dpi_stats["http_hosts"] += 1

            # FLOW_HTTP_HOST edge
            fh_id = f"e:fh:{fid}:{hh_id}"
            if fh_id not in self._emitted_ids:
                self._emitted_ids.add(fh_id)
                self.engine.add_edge({
                    "id": fh_id, "kind": "FLOW_HTTP_HOST",
                    "nodes": [fid, hh_id], "weight": 1.0,
                })
                edges += 1

        return nodes, edges

    def _emit_protocol_events(
        self,
        session: SessionData,
    ) -> Tuple[int, int]:
        """Detect and emit notable protocol events within a session.

        Currently detects:
        - TCP SYN (connection initiation)
        - TCP RST (connection reset)
        - TCP FIN (connection teardown)
        - DNS queries (port 53 UDP)
        - TLS handshakes (port 443 TCP, first few packets)
        """
        nodes = 0
        edges = 0

        # Aggregate flag-based events
        flag_events: Dict[str, int] = defaultdict(int)
        for pkt in session.packets:
            if pkt.proto == "TCP" and pkt.flags:
                for flag_char in ("S", "R", "F"):
                    if flag_char in pkt.flags:
                        flag_events[flag_char] += 1

        now_ts = time.time()

        # SYN events → connection initiation
        if flag_events.get("S", 0) > 0:
            event_id = f"pe:syn:{session.session_id}"
            self.engine.add_node({
                "id": event_id,
                "kind": "protocol_event",
                "labels": {
                    "event_type": "TCP_SYN",
                    "count": flag_events["S"],
                    "session": session.session_id,
                },
                "metadata": {
                    "provenance": {"source": self.config.source_tag},
                    "confidence": "SENSOR",
                },
            })
            self.engine.add_edge({
                "id": f"e:pe:{event_id}:{session.session_id}",
                "kind": "SESSION_CONTAINS_EVENT",
                "nodes": [session.session_id, event_id],
                "weight": 1.0,
                "metadata": {"provenance": {"source": self.config.source_tag}},
            })
            nodes += 1
            edges += 1

        # RST events → connection reset (potentially interesting)
        if flag_events.get("R", 0) > 0:
            event_id = f"pe:rst:{session.session_id}"
            self.engine.add_node({
                "id": event_id,
                "kind": "protocol_event",
                "labels": {
                    "event_type": "TCP_RST",
                    "count": flag_events["R"],
                    "session": session.session_id,
                },
                "metadata": {
                    "provenance": {"source": self.config.source_tag},
                    "confidence": "SENSOR",
                },
            })
            self.engine.add_edge({
                "id": f"e:pe:{event_id}:{session.session_id}",
                "kind": "SESSION_CONTAINS_EVENT",
                "nodes": [session.session_id, event_id],
                "weight": 1.0,
                "metadata": {"provenance": {"source": self.config.source_tag}},
            })
            nodes += 1
            edges += 1

        # DNS detection (UDP port 53)
        if session.proto == "UDP" and (session.src_port == 53 or session.dst_port == 53):
            event_id = f"pe:dns:{session.session_id}"
            self.engine.add_node({
                "id": event_id,
                "kind": "protocol_event",
                "labels": {
                    "event_type": "DNS_EXCHANGE",
                    "query_count": session.packet_count,
                    "session": session.session_id,
                },
                "metadata": {
                    "provenance": {"source": self.config.source_tag},
                    "confidence": "SENSOR",
                },
            })
            self.engine.add_edge({
                "id": f"e:pe:{event_id}:{session.session_id}",
                "kind": "SESSION_CONTAINS_EVENT",
                "nodes": [session.session_id, event_id],
                "weight": 1.0,
                "metadata": {"provenance": {"source": self.config.source_tag}},
            })
            nodes += 1
            edges += 1

        # TLS detection (TCP port 443)
        if session.proto == "TCP" and (
            session.src_port == 443 or session.dst_port == 443
        ):
            event_id = f"pe:tls:{session.session_id}"
            self.engine.add_node({
                "id": event_id,
                "kind": "protocol_event",
                "labels": {
                    "event_type": "TLS_SESSION",
                    "packet_count": session.packet_count,
                    "session": session.session_id,
                },
                "metadata": {
                    "provenance": {"source": self.config.source_tag},
                    "confidence": "SENSOR",
                },
            })
            self.engine.add_edge({
                "id": f"e:pe:{event_id}:{session.session_id}",
                "kind": "SESSION_CONTAINS_EVENT",
                "nodes": [session.session_id, event_id],
                "weight": 1.0,
                "metadata": {"provenance": {"source": self.config.source_tag}},
            })
            nodes += 1
            edges += 1

        return nodes, edges


# ─────────────────────────────────────────────────────────────────────────────
# Ledger Registrar
# ─────────────────────────────────────────────────────────────────────────────

class LedgerRegistrar:
    """Registers ingested sessions with the Inference Exhaustion Ledger.

    Each session gets a NO_REINFER record — it's sensor-backed fact,
    not something that needs inference.  The ledger prevents the
    inference engine from re-running over sensor-confirmed data.
    """

    def __init__(self, ledger: Any):
        self.ledger = ledger

    def register_session(
        self,
        session: SessionData,
        pcap_artifact_id: str,
    ) -> None:
        """Register a session as sensor-backed, exhaustion-immune."""
        if self.ledger is None:
            return

        try:
            # Use the session_key as the evidence epoch — it's stable
            # and changes only if the PCAP content changes
            evidence_epoch = hashlib.sha256(
                session.session_key.encode()
            ).hexdigest()[:16]

            self.ledger.record_attempt(
                entity_id=session.session_id,
                rule_id="pcap_ingest",
                evidence_epoch=evidence_epoch,
                result="SUCCESS",
                entity_kind="session",
                edges_produced=4,  # minimum edges per session
            )
            logger.debug(
                "[pcap_ingest] Ledger registered: %s", session.session_id,
            )
        except Exception as e:
            logger.warning(
                "[pcap_ingest] Ledger registration failed for %s: %s",
                session.session_id, e,
            )


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline
# ─────────────────────────────────────────────────────────────────────────────

class PcapIngestPipeline:
    """
    End-to-end PCAP ingestion pipeline.

    FTP → Staging → Decode → Sessionize → Emit → Ledger

    All steps are idempotent and restartable.

    Usage:
        pipeline = PcapIngestPipeline(engine, ledger, config)
        result = pipeline.ingest_all()
        print(result.summary())
    """

    def __init__(
        self,
        engine: Any,
        ledger: Any = None,
        config: Optional[IngestConfig] = None,
    ):
        self.engine = engine
        self.config = config or IngestConfig()
        self.fetcher = FTPFetcher(self.config.ftp_url, self.config.staging_dir)
        self.emitter = HypergraphEmitter(engine, self.config)
        self.registrar = LedgerRegistrar(ledger) if self.config.register_ledger else None

    def _is_already_ingested(self, filename: str) -> bool:
        """Check if a PCAP has already been ingested (by artifact node)."""
        if not self.config.skip_existing:
            return False
        artifact_id = _pcap_artifact_id(filename)
        return artifact_id in getattr(self.engine, "nodes", {})

    def ingest_file(self, filepath: Path) -> IngestResult:
        """Ingest a single PCAP file (local path).

        Returns IngestResult with session IDs, geo_points, and DPI stats.
        """
        t0 = time.monotonic()
        filename = filepath.name
        artifact_id = _pcap_artifact_id(filename)
        result = IngestResult(pcap_file=filename, pcap_artifact_id=artifact_id)

        # Track geo_points emitted before this file (to diff after)
        geo_before = len(self.emitter.geo_points)

        try:
            # ── 1. Decode (with DPI) ─────────────────────────────────
            packets = decode_pcap(filepath, enable_dpi=self.config.enable_dpi)
            result.packets_decoded = len(packets)

            if not packets:
                result.errors.append("No IP packets found in PCAP")
                result.duration_sec = time.monotonic() - t0
                return result

            # ── 2. Emit PCAP artifact node ───────────────────────────
            file_size = filepath.stat().st_size if filepath.exists() else 0
            self.emitter.emit_pcap_artifact(filename, file_size)
            result.nodes_emitted += 1

            # ── 3. Sessionize ────────────────────────────────────────
            sessions = sessionize(
                packets,
                filename,
                window_sec=self.config.session_window_sec,
                min_packets=self.config.min_session_packets,
            )
            result.sessions_created = len(sessions)

            # ── 4. Emit session hypergraphs ──────────────────────────
            for session in sessions:
                nodes, edges = self.emitter.emit_session(session, artifact_id)
                result.nodes_emitted += nodes
                result.edges_emitted += edges
                result.session_ids.append(session.session_id)

                # ── 5. Ledger registration ───────────────────────────
                if self.registrar:
                    self.registrar.register_session(session, artifact_id)

            self.emitter.flush(
                entity_id=artifact_id,
                entity_type="PCAP_INGEST_MATERIALIZATION",
                entity_data={
                    "id": artifact_id,
                    "kind": "pcap_artifact",
                    "labels": {
                        "filename": filename,
                        "file_size": file_size,
                        "sessions_created": result.sessions_created,
                        "nodes_emitted": result.nodes_emitted,
                        "edges_emitted": result.edges_emitted,
                    },
                    "metadata": {
                        "provenance": {
                            "source": self.config.source_tag,
                            "evidence_type": "packet_capture",
                        },
                    },
                },
                request_id=f"pcap_ingest:{artifact_id}:materialize",
                evidence_refs=[artifact_id, filename],
                idempotency_key=f"pcap-ingest:{artifact_id}:materialize:v1",
            )

            # ── 4b. BSG detection (post-sessionization) ──────────────
            if self.config.enable_bsg and sessions:
                try:
                    from behavior_groups import BehaviorGroupDetector, BSGConfig
                    bsg_detector = BehaviorGroupDetector(self.emitter.engine)
                    bsg_result = bsg_detector.detect_all(sessions)
                    self.emitter.flush(
                        entity_id=f"{artifact_id}:bsg",
                        entity_type="PCAP_BSG_MATERIALIZATION",
                        entity_data={
                            "id": f"{artifact_id}:bsg",
                            "kind": "pcap_behavior_groups",
                            "labels": {
                                "filename": filename,
                                "groups_created": bsg_result.groups_created,
                                "sessions_grouped": bsg_result.sessions_grouped,
                                "sessions_total": bsg_result.sessions_total,
                            },
                            "metadata": {
                                "provenance": {
                                    "source": "bsg_detector",
                                    "evidence_type": "behavioral_inference",
                                },
                            },
                        },
                        request_id=f"pcap_ingest:{artifact_id}:bsg",
                        evidence_refs=[artifact_id, filename],
                        idempotency_key=f"pcap-ingest:{artifact_id}:bsg:v1",
                    )
                    result.nodes_emitted += bsg_result.groups_created
                    result.edges_emitted += bsg_result.edges_created
                    result.bsg_summary = bsg_result.to_dict()
                    logger.info(
                        "[pcap_ingest] BSG: %d groups, %d/%d sessions grouped",
                        bsg_result.groups_created,
                        bsg_result.sessions_grouped,
                        bsg_result.sessions_total,
                    )
                except Exception as bsg_err:
                    self.emitter.discard_pending()
                    logger.warning("[pcap_ingest] BSG detection failed: %s", bsg_err)

        except Exception as e:
            self.emitter.discard_pending()
            result.errors.append(f"{type(e).__name__}: {e}")
            logger.error("[pcap_ingest] Failed to ingest %s: %s", filename, e)

        # Collect geo_points + DPI stats from emitter
        result.geo_points = self.emitter.geo_points[geo_before:]
        result.dpi_stats = dict(self.emitter.dpi_stats)
        result.duration_sec = time.monotonic() - t0

        logger.info(
            "[pcap_ingest] %s: %d sessions, %d geo_points, DPI: %s",
            filename, result.sessions_created, len(result.geo_points), result.dpi_stats,
        )
        return result

    def ingest_from_ftp(self, filename: str) -> IngestResult:
        """Fetch a single PCAP from FTP and ingest it."""
        if self._is_already_ingested(filename):
            logger.info("[pcap_ingest] Skipping (already ingested): %s", filename)
            return IngestResult(
                pcap_file=filename,
                pcap_artifact_id=_pcap_artifact_id(filename),
            )

        local_path = self.fetcher.fetch(filename)
        return self.ingest_file(local_path)

    def ingest_all(self) -> BatchIngestResult:
        """Batch ingest: list FTP → download → ingest all PCAPs.

        This is the main entry point for "ingest everything at once."
        Each PCAP is processed independently — failures are isolated.
        """
        t0 = time.monotonic()
        batch = BatchIngestResult()

        try:
            pcap_files = self.fetcher.list_pcaps()
        except Exception as e:
            batch.errors.append(f"FTP listing failed: {e}")
            batch.duration_sec = time.monotonic() - t0
            return batch

        # Track unique geo_point IPs to deduplicate across files
        seen_geo_ips: Set[str] = set()

        for filename in pcap_files:
            # Skip check
            if self._is_already_ingested(filename):
                batch.pcaps_skipped += 1
                logger.info("[pcap_ingest] Skipping: %s", filename)
                continue

            try:
                result = self.ingest_from_ftp(filename)
                batch.per_file.append(result)
                batch.pcaps_processed += 1

                if result.ok:
                    batch.total_sessions += result.sessions_created
                    batch.total_nodes += result.nodes_emitted
                    batch.total_edges += result.edges_emitted
                    batch.total_packets += result.packets_decoded
                    # Accumulate unique geo_points
                    for gp in result.geo_points:
                        if gp["ip"] not in seen_geo_ips:
                            seen_geo_ips.add(gp["ip"])
                            batch.geo_points.append(gp)
                else:
                    batch.pcaps_failed += 1
                    for err in result.errors:
                        batch.errors.append(f"{filename}: {err}")

            except Exception as e:
                batch.pcaps_failed += 1
                batch.errors.append(f"{filename}: {type(e).__name__}: {e}")
                logger.error("[pcap_ingest] PCAP failed: %s — %s", filename, e)

        # Collect DPI stats from emitter
        batch.dpi_stats = dict(self.emitter.dpi_stats)

        # ── BSG detection (across ALL sessions from all PCAPs) ───────
        if self.config.enable_bsg and batch.total_sessions > 0:
            try:
                from behavior_groups import BehaviorGroupDetector, BSGConfig
                bsg_detector = BehaviorGroupDetector(self.emitter.engine)
                bsg_result = bsg_detector.detect_from_graph()
                evidence_refs = [r.pcap_artifact_id for r in batch.per_file if r.ok]
                evidence_hash = hashlib.sha256(
                    json.dumps(sorted(evidence_refs), sort_keys=True).encode("utf-8")
                ).hexdigest()[:16]
                self.emitter.flush(
                    entity_id=f"pcap_ingest:batch:bsg:{evidence_hash}",
                    entity_type="PCAP_BATCH_BSG_MATERIALIZATION",
                    entity_data={
                        "id": f"pcap_ingest:batch:bsg:{evidence_hash}",
                        "kind": "pcap_batch_behavior_groups",
                        "labels": {
                            "groups_created": bsg_result.groups_created,
                            "sessions_grouped": bsg_result.sessions_grouped,
                            "sessions_total": bsg_result.sessions_total,
                            "pcaps_processed": batch.pcaps_processed,
                        },
                        "metadata": {
                            "provenance": {
                                "source": "bsg_detector",
                                "evidence_type": "behavioral_inference",
                            },
                        },
                    },
                    request_id=f"pcap_ingest:batch:bsg:{evidence_hash}",
                    evidence_refs=evidence_refs,
                    idempotency_key=f"pcap-ingest:batch:bsg:{evidence_hash}:v1",
                )
                batch.bsg_summary = bsg_result.to_dict()
                batch.total_nodes += bsg_result.groups_created
                batch.total_edges += bsg_result.edges_created
                logger.info(
                    "[pcap_ingest] BSG batch: %d groups, %d/%d sessions grouped",
                    bsg_result.groups_created,
                    bsg_result.sessions_grouped,
                    bsg_result.sessions_total,
                )
            except Exception as bsg_err:
                self.emitter.discard_pending()
                logger.warning("[pcap_ingest] BSG batch detection failed: %s", bsg_err)

        batch.duration_sec = time.monotonic() - t0
        logger.info(
            "[pcap_ingest] Batch complete: %d files, %d sessions, %d geo_points, %.1fs",
            batch.pcaps_processed, batch.total_sessions, len(batch.geo_points), batch.duration_sec,
        )
        return batch

    def graph_summary_after_ingest(self) -> str:
        """Return a human-readable summary of graph state post-ingest."""
        nodes = getattr(self.engine, "nodes", {})
        edges = getattr(self.engine, "edges", {})

        kinds: Dict[str, int] = defaultdict(int)
        for n in nodes.values():
            k = n.kind if hasattr(n, "kind") else (n.get("kind") if isinstance(n, dict) else "unknown")
            kinds[k] += 1

        edge_kinds: Dict[str, int] = defaultdict(int)
        for e in edges.values():
            k = e.kind if hasattr(e, "kind") else (e.get("kind") if isinstance(e, dict) else "unknown")
            edge_kinds[k] += 1

        lines = [
            "═══ POST-INGEST GRAPH STATE ═══",
            f"Total nodes: {len(nodes)}",
        ]
        for k, v in sorted(kinds.items(), key=lambda x: -x[1]):
            lines.append(f"  {k}: {v}")

        lines.append(f"Total edges: {len(edges)}")
        for k, v in sorted(edge_kinds.items(), key=lambda x: -x[1]):
            lines.append(f"  {k}: {v}")

        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# MCP Tool Schema
# ─────────────────────────────────────────────────────────────────────────────

MCP_PCAP_INGEST_TOOL = {
    "name": "pcap_ingest",
    "description": (
        "Batch-ingest PCAP files from an FTP server into session hypergraphs. "
        "Downloads PCAPs, decodes packets, performs deterministic sessionization "
        "(5-tuple + time bucket), and materializes SESSION subgraphs with full "
        "provenance. All sessions are sensor-backed evidence."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "ftp_url": {
                "type": "string",
                "description": "FTP server URL (e.g. ftp://172.234.197.23)",
                "default": "ftp://172.234.197.23",
            },
            "session_window_sec": {
                "type": "integer",
                "description": "Time bucket for sessionization (seconds). Default 30.",
                "default": 30,
            },
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Specific PCAP filenames to ingest. If omitted, ingests all "
                    "PCAPs found on the FTP server."
                ),
            },
            "skip_existing": {
                "type": "boolean",
                "description": "Skip PCAPs that have already been ingested.",
                "default": True,
            },
        },
        "required": [],
    },
}

MCP_PCAP_LIST_TOOL = {
    "name": "pcap_list_ftp",
    "description": (
        "List available PCAP files on the configured FTP server. "
        "Returns filenames without downloading."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "ftp_url": {
                "type": "string",
                "description": "FTP server URL (e.g. ftp://172.234.197.23)",
                "default": "ftp://172.234.197.23",
            },
        },
        "required": [],
    },
}

MCP_SESSION_SUMMARY_TOOL = {
    "name": "session_summary",
    "description": (
        "Summarize ingested sessions: counts by protocol, top hosts, "
        "time range, PCAP provenance."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}


def handle_mcp_pcap_ingest(
    engine: Any,
    ledger: Any,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """MCP tool handler for pcap_ingest.

    Now returns geo_points and dpi_stats alongside session/node counts
    so the frontend can create recon entities + Cesium markers.
    """
    config = IngestConfig(
        ftp_url=params.get("ftp_url", "ftp://172.234.197.23"),
        session_window_sec=params.get("session_window_sec", 30),
        skip_existing=params.get("skip_existing", True),
        enable_geoip=params.get("enable_geoip", True),
        enable_dpi=params.get("enable_dpi", True),
        emit_flow_nodes=params.get("emit_flow_nodes", True),
    )
    pipeline = PcapIngestPipeline(engine, ledger, config)

    # compute a warning message if geoip was requested but library is missing
    geoip_warning = None
    if config.enable_geoip and not HAS_MAXMINDDB:
        db_candidates = [config.geoip_city_mmdb, config.geoip_asn_mmdb]
        present = [p for p in db_candidates if p and os.path.isfile(p)]
        geoip_warning = (
            "maxminddb library not installed; GeoIP lookups disabled. "
            "Install with `pip install maxminddb`. "
            f"Detected DB files: {', '.join(present) if present else 'none'}."
        )

    specific_files = params.get("files")
    if specific_files:
        # Ingest specific files
        results = []
        all_geo = []
        for f in specific_files:
            r = pipeline.ingest_from_ftp(f)
            results.append(r.to_dict())
            all_geo.extend(r.geo_points)
        total_sessions = sum(r.get("sessions_created", 0) for r in results)
        return {
            "status": "ok",
            "files_processed": len(results),
            "total_sessions": total_sessions,
            "geo_points": all_geo,
            "dpi_stats": pipeline.emitter.dpi_stats,
            "results": results,
            "geoip_warning": geoip_warning,
        }
    else:
        # Ingest all
        result = pipeline.ingest_all()
        d = result.to_dict()
        return {
            "status": "ok" if result.ok else "partial_failure",
            "summary": result.summary(),
            "geo_points": result.geo_points,
            "dpi_stats": result.dpi_stats,
            "geoip_warning": geoip_warning,
            **d,
        }


def handle_mcp_pcap_list(params: Dict[str, Any]) -> Dict[str, Any]:
    """MCP tool handler for pcap_list_ftp."""
    ftp_url = params.get("ftp_url", "ftp://172.234.197.23")
    fetcher = FTPFetcher(ftp_url, "/tmp/pcap_staging")
    try:
        files = fetcher.list_pcaps()
        return {"status": "ok", "files": files, "count": len(files)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def handle_mcp_session_summary(engine: Any) -> Dict[str, Any]:
    """MCP tool handler for session_summary."""
    nodes = getattr(engine, "nodes", {})
    edges = getattr(engine, "edges", {})

    sessions = []
    hosts = set()
    pcaps = set()
    protocols: Dict[str, int] = defaultdict(int)

    for nid, n in nodes.items():
        nd = n.to_dict() if hasattr(n, "to_dict") else (n if isinstance(n, dict) else {})
        kind = nd.get("kind", "")
        labels = nd.get("labels", {})

        if kind == "session":
            sessions.append({
                "id": nid,
                "proto": labels.get("proto", "unknown"),
                "packet_count": labels.get("packet_count", 0),
                "total_bytes": labels.get("total_bytes", 0),
                "src_ip": labels.get("src_ip", ""),
                "dst_ip": labels.get("dst_ip", ""),
                "time_bucket": labels.get("time_bucket", 0),
            })
            protocols[labels.get("proto", "unknown")] += 1
        elif kind == "host":
            hosts.add(labels.get("ip", nid))
        elif kind == "pcap_artifact":
            pcaps.add(labels.get("filename", nid))

    return {
        "status": "ok",
        "session_count": len(sessions),
        "host_count": len(hosts),
        "pcap_count": len(pcaps),
        "protocols": dict(protocols),
        "pcap_files": sorted(pcaps),
        "top_talkers": sorted(hosts)[:20],
        "sessions": sessions[:50],  # cap for display
    }


# ─────────────────────────────────────────────────────────────────────────────
# CLI Entry Point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    """CLI for batch PCAP ingestion."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="PCAP → Session Hypergraph Ingestion Pipeline",
    )
    parser.add_argument(
        "--ftp", default="ftp://172.234.197.23",
        help="FTP server URL (default: ftp://172.234.197.23)",
    )
    parser.add_argument(
        "--staging", default="/tmp/pcap_staging",
        help="Local staging directory for downloads",
    )
    parser.add_argument(
        "--window", type=int, default=30,
        help="Session window in seconds (default: 30)",
    )
    parser.add_argument(
        "--files", nargs="*",
        help="Specific PCAP files to ingest (default: all)",
    )
    parser.add_argument(
        "--no-ledger", action="store_true",
        help="Skip ledger registration",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output results as JSON",
    )
    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    # Create engine + ledger
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from hypergraph_engine import HypergraphEngine
    from inference_exhaustion_ledger import InferenceExhaustionLedger

    engine = HypergraphEngine()
    ledger = InferenceExhaustionLedger() if not args.no_ledger else None

    config = IngestConfig(
        ftp_url=args.ftp,
        staging_dir=args.staging,
        session_window_sec=args.window,
        register_ledger=not args.no_ledger,
    )

    pipeline = PcapIngestPipeline(engine, ledger, config)

    if args.files:
        for f in args.files:
            result = pipeline.ingest_from_ftp(f)
            if args.json:
                print(json.dumps(result.to_dict(), indent=2))
            else:
                print(f"{f}: {result.sessions_created} sessions, "
                      f"{result.nodes_emitted} nodes, {result.edges_emitted} edges")
    else:
        result = pipeline.ingest_all()
        if args.json:
            print(json.dumps(result.to_dict(), indent=2))
        else:
            print(result.summary())
            print()
            print(pipeline.graph_summary_after_ingest())


if __name__ == "__main__":
    main()
