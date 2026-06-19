"""
protocol_intel.py — IANA-grounded protocol expectation engine.

The core insight: IANA port assignments encode *expected behavior*. Any
deviation between what a port promises and what a session delivers is signal.

Violation detection covers:
  1. Missing expected application-layer markers  (e.g. port 443 ∧ no TLS SNI)
  2. Unexpected protocol present                 (e.g. DNS payload on port 80)
  3. Suspicious payload size patterns            (e.g. port 53 frames > 512 B → DNS tunnel)
  4. Constant-size traffic on "variable" ports   (C2 beacon signature)
  5. Wrong transport protocol for port           (e.g. TCP on port 123 NTP)
  6. Known high-risk port usage                  (4444, 31337, 9001 Tor, etc.)
  7. Absence of handshake markers                (SYN-only storms, RST floods)

Output is a ProtocolAnomalyResult that integrates with:
  - AttentionEngine  (protocol_anomaly field in edge dict)
  - ShadowGraph.push (evidence bump proportional to anomaly score)
  - SemanticShadow   (future: fuse into behavioral fingerprint vector)

Usage
-----
    from protocol_intel import ProtocolIntel
    pi = ProtocolIntel()

    # From a pcap_ingest.py SessionData object:
    result = pi.score_session(session)

    # From a plain dict (e.g. ingest event from stream):
    result = pi.score_dict({'dst_port': 443, 'proto': 'TCP', 'tls_snis': []})

    print(result.anomaly_score, result.violations)
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, List, NamedTuple, Optional, Set, Tuple

# ── IANA port behavior profiles ───────────────────────────────────────────────
# Keys are (port, transport) or (port, '*') for protocol-agnostic entries.
# Fields:
#   name            human-readable service name
#   expect_tls      True → TLS ClientHello SNI expected for normal traffic
#   expect_dns      True → DNS query/response expected
#   expect_http     True → HTTP Host header expected
#   max_pkt_bytes   None or int → flag if avg packet size exceeds this
#   fixed_size      True → legitimate traffic has near-constant packet sizes
#   expected_proto  'TCP'|'UDP'|'BOTH' → flag wrong transport
#   risk_base       baseline risk of seeing traffic here (0=normal, 1=always sus)
#   note            human-readable context

_IANA: Dict[Tuple[int, str], Dict[str, Any]] = {
    # ── Common TCP ────────────────────────────────────────────────────────────
    (20,  'TCP'): dict(name='ftp-data',    expect_tls=False, expect_http=False, expect_dns=False, expected_proto='TCP', risk_base=0.1),
    (21,  'TCP'): dict(name='ftp-ctrl',    expect_tls=False, expect_http=False, expect_dns=False, expected_proto='TCP', risk_base=0.1),
    (22,  'TCP'): dict(name='ssh',         expect_tls=False, expect_http=False, expect_dns=False, expected_proto='TCP', risk_base=0.0,  note='encrypted by design'),
    (23,  'TCP'): dict(name='telnet',      expect_tls=False, expect_http=False, expect_dns=False, expected_proto='TCP', risk_base=0.45, note='cleartext — legacy/suspicious'),
    (25,  'TCP'): dict(name='smtp',        expect_tls=False, expect_http=False, expect_dns=False, expected_proto='TCP', risk_base=0.1),
    (80,  'TCP'): dict(name='http',        expect_tls=False, expect_http=True,  expect_dns=False, expected_proto='TCP', risk_base=0.0),
    (110, 'TCP'): dict(name='pop3',        expect_tls=False, expect_http=False, expect_dns=False, expected_proto='TCP', risk_base=0.1),
    (143, 'TCP'): dict(name='imap',        expect_tls=False, expect_http=False, expect_dns=False, expected_proto='TCP', risk_base=0.1),
    (443, 'TCP'): dict(name='https',       expect_tls=True,  expect_http=False, expect_dns=False, expected_proto='BOTH', risk_base=0.0),
    (465, 'TCP'): dict(name='smtps',       expect_tls=True,  expect_http=False, expect_dns=False, expected_proto='TCP', risk_base=0.1),
    (587, 'TCP'): dict(name='submission',  expect_tls=True,  expect_http=False, expect_dns=False, expected_proto='TCP', risk_base=0.0),
    (636, 'TCP'): dict(name='ldaps',       expect_tls=True,  expect_http=False, expect_dns=False, expected_proto='TCP', risk_base=0.15),
    (853, 'TCP'): dict(name='dns-tls',     expect_tls=True,  expect_http=False, expect_dns=True,  expected_proto='TCP', risk_base=0.0),
    (993, 'TCP'): dict(name='imaps',       expect_tls=True,  expect_http=False, expect_dns=False, expected_proto='TCP', risk_base=0.0),
    (995, 'TCP'): dict(name='pop3s',       expect_tls=True,  expect_http=False, expect_dns=False, expected_proto='TCP', risk_base=0.0),
    (1433,'TCP'): dict(name='mssql',       expect_tls=False, expect_http=False, expect_dns=False, expected_proto='TCP', risk_base=0.2),
    (3306,'TCP'): dict(name='mysql',       expect_tls=False, expect_http=False, expect_dns=False, expected_proto='TCP', risk_base=0.2),
    (3389,'TCP'): dict(name='rdp',         expect_tls=True,  expect_http=False, expect_dns=False, expected_proto='BOTH', risk_base=0.2),
    (5222,'TCP'): dict(name='xmpp',        expect_tls=False, expect_http=False, expect_dns=False, expected_proto='TCP', risk_base=0.15),
    (6667,'TCP'): dict(name='irc',         expect_tls=False, expect_http=False, expect_dns=False, expected_proto='TCP', risk_base=0.5,  note='classic C2 channel'),
    (8080,'TCP'): dict(name='http-alt',    expect_tls=False, expect_http=True,  expect_dns=False, expected_proto='TCP', risk_base=0.05),
    (8443,'TCP'): dict(name='https-alt',   expect_tls=True,  expect_http=False, expect_dns=False, expected_proto='TCP', risk_base=0.05),
    # ── Common UDP ────────────────────────────────────────────────────────────
    (53,  'UDP'): dict(name='dns',         expect_tls=False, expect_http=False, expect_dns=True,  expected_proto='BOTH', max_pkt_bytes=512,  risk_base=0.0),
    (53,  'TCP'): dict(name='dns-tcp',     expect_tls=False, expect_http=False, expect_dns=True,  expected_proto='BOTH', risk_base=0.05,     note='DNS-TCP only for large responses; abuse = tunnel'),
    (67,  'UDP'): dict(name='dhcp-server', expect_tls=False, expect_http=False, expect_dns=False, expected_proto='UDP', risk_base=0.0),
    (68,  'UDP'): dict(name='dhcp-client', expect_tls=False, expect_http=False, expect_dns=False, expected_proto='UDP', risk_base=0.0),
    (123, 'UDP'): dict(name='ntp',         expect_tls=False, expect_http=False, expect_dns=False, expected_proto='UDP', max_pkt_bytes=76,  fixed_size=True, risk_base=0.0),
    (161, 'UDP'): dict(name='snmp',        expect_tls=False, expect_http=False, expect_dns=False, expected_proto='UDP', risk_base=0.15),
    (443, 'UDP'): dict(name='quic',        expect_tls=True,  expect_http=False, expect_dns=False, expected_proto='BOTH', risk_base=0.05,   note='QUIC is legitimate UDP 443'),
    (500, 'UDP'): dict(name='isakmp',      expect_tls=False, expect_http=False, expect_dns=False, expected_proto='UDP', risk_base=0.1),
    (514, 'UDP'): dict(name='syslog',      expect_tls=False, expect_http=False, expect_dns=False, expected_proto='UDP', risk_base=0.1,     note='cleartext syslog exfil vector'),
    (1194,'UDP'): dict(name='openvpn',     expect_tls=False, expect_http=False, expect_dns=False, expected_proto='BOTH', risk_base=0.1),
    (4500,'UDP'): dict(name='ipsec-nat-t', expect_tls=False, expect_http=False, expect_dns=False, expected_proto='UDP', risk_base=0.1),
    # ── High-risk / known-bad ─────────────────────────────────────────────────
    (4444, 'TCP'): dict(name='metasploit', expect_tls=False, expect_http=False, expect_dns=False, expected_proto='TCP', risk_base=0.75, note='Metasploit default shell'),
    (4444, 'UDP'): dict(name='metasploit', expect_tls=False, expect_http=False, expect_dns=False, expected_proto='TCP', risk_base=0.75, note='Metasploit default shell'),
    (4445, 'TCP'): dict(name='metasploit-alt', risk_base=0.65, expect_tls=False, expect_http=False, expect_dns=False, expected_proto='TCP'),
    (5555, 'TCP'): dict(name='adb',        expect_tls=False, expect_http=False, expect_dns=False, expected_proto='TCP', risk_base=0.5,  note='Android Debug Bridge — remote device control'),
    (6666, 'TCP'): dict(name='irc-alt',    expect_tls=False, expect_http=False, expect_dns=False, expected_proto='TCP', risk_base=0.5),
    (9001, 'TCP'): dict(name='tor-orport', expect_tls=True,  expect_http=False, expect_dns=False, expected_proto='TCP', risk_base=0.35, note='Tor OR port'),
    (9030, 'TCP'): dict(name='tor-dirport',expect_tls=False, expect_http=True,  expect_dns=False, expected_proto='TCP', risk_base=0.35, note='Tor directory'),
    (9050, 'TCP'): dict(name='tor-socks',  expect_tls=False, expect_http=False, expect_dns=False, expected_proto='TCP', risk_base=0.35, note='Tor SOCKS proxy'),
    (31337,'TCP'): dict(name='elite',      expect_tls=False, expect_http=False, expect_dns=False, expected_proto='TCP', risk_base=0.8,  note='leet port — almost always malicious'),
    (31337,'UDP'): dict(name='elite-udp',  expect_tls=False, expect_http=False, expect_dns=False, expected_proto='TCP', risk_base=0.8),
}

# Constant-size coefficient of variation threshold (below = suspiciously regular)
_CV_BEACON_THRESHOLD = 0.05   # std/mean < 5% → likely beacon

# DNS tunnel heuristic: oversized UDP/53 frames
_DNS_TUNNEL_PKT_BYTES = 300   # avg > 300B on port 53 UDP = suspicious

# Violation weights (scores sum and are clamped to 1.0)
_VW = {
    'missing_tls':         0.35,
    'unexpected_dns':      0.40,
    'dns_tunnel':          0.55,
    'unexpected_http':     0.30,
    'wrong_transport':     0.45,
    'oversized_ntp':       0.50,
    'constant_size_c2':    0.40,
    'risk_port':           None,   # dynamic — from profile.risk_base
    'missing_expected_dpi':0.20,
    'tcp_syn_only':        0.30,
    'tcp_rst_flood':       0.35,
}


class Violation(NamedTuple):
    name:   str
    score:  float
    detail: str


@dataclass
class ProtocolAnomalyResult:
    """Output of ProtocolIntel.score_session() / score_dict()."""
    anomaly_score:    float          # 0-1 aggregate
    violations:       List[Violation] = field(default_factory=list)
    expected_proto:   str = ""       # IANA service name for the port
    port:             int = 0
    transport:        str = ""       # TCP/UDP observed
    notes:            List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "anomaly_score":  round(self.anomaly_score, 4),
            "violations":     [{"name": v.name, "score": round(v.score, 3), "detail": v.detail}
                               for v in self.violations],
            "expected_proto": self.expected_proto,
            "port":           self.port,
            "transport":      self.transport,
            "notes":          self.notes,
        }

    def shadow_evidence_delta(self) -> float:
        """Evidence delta to push to ShadowGraph when observing this anomaly."""
        return round(self.anomaly_score * 0.4, 4)

    def shadow_confidence_delta(self) -> float:
        """Confidence delta to push to ShadowGraph."""
        return round(self.anomaly_score * 0.15, 4)


@dataclass
class BehavioralFingerprint:
    """
    Statistical feature vector for a session.

    Designed to be fused into the SemanticShadow delta embedding:
        fused = concat(text_embedding, fingerprint_features)

    This allows "same actor across port rotation" detection even when
    the IP changes — because the statistical behavior signature persists.
    """
    avg_pkt_bytes:    float = 0.0
    std_pkt_bytes:    float = 0.0
    cv_pkt_bytes:     float = 0.0   # coefficient of variation (std/mean)
    median_pkt_bytes: float = 0.0
    avg_iat_ms:       float = 0.0   # inter-arrival time
    std_iat_ms:       float = 0.0
    pkt_count:        int   = 0
    total_bytes:      int   = 0
    duration_sec:     float = 0.0
    bytes_per_sec:    float = 0.0
    dst_port:         int   = 0
    src_port:         int   = 0
    proto_tcp:        int   = 0     # 1/0 one-hot
    proto_udp:        int   = 0
    proto_icmp:       int   = 0
    has_tls:          int   = 0     # 1/0
    has_dns:          int   = 0
    has_http:         int   = 0
    tcp_flag_syn:     int   = 0
    tcp_flag_rst:     int   = 0
    tcp_flag_fin:     int   = 0
    anomaly_score:    float = 0.0   # from ProtocolIntel

    def to_vector(self) -> List[float]:
        """Return normalised feature vector for embedding fusion."""
        return [
            min(1.0, self.avg_pkt_bytes / 1500.0),
            min(1.0, self.std_pkt_bytes / 750.0),
            min(1.0, self.cv_pkt_bytes),
            min(1.0, self.median_pkt_bytes / 1500.0),
            min(1.0, self.avg_iat_ms / 5000.0),
            min(1.0, self.std_iat_ms / 2500.0),
            min(1.0, math.log1p(self.pkt_count) / 15.0),
            min(1.0, math.log1p(self.total_bytes) / 20.0),
            min(1.0, self.duration_sec / 300.0),
            min(1.0, self.bytes_per_sec / 50000.0),
            self.dst_port / 65535.0,
            self.src_port / 65535.0,
            float(self.proto_tcp),
            float(self.proto_udp),
            float(self.proto_icmp),
            float(self.has_tls),
            float(self.has_dns),
            float(self.has_http),
            float(self.tcp_flag_syn),
            float(self.tcp_flag_rst),
            float(self.tcp_flag_fin),
            self.anomaly_score,
        ]  # 22-dimensional


class ProtocolIntel:
    """
    Stateless protocol expectation engine.

    All public methods accept either SessionData objects (pcap_ingest.py)
    or plain dicts so the same scorer works at every pipeline stage.
    """

    # ── Public API ────────────────────────────────────────────────────────────

    def score_session(self, session: Any) -> ProtocolAnomalyResult:
        """Score a pcap_ingest.py SessionData object."""
        dst_port   = getattr(session, 'dst_port', None) or 0
        src_port   = getattr(session, 'src_port', None) or 0
        transport  = (getattr(session, 'proto', '') or 'TCP').upper()
        pkt_sizes  = [p.length for p in getattr(session, 'packets', [])]
        pkt_times  = [p.timestamp for p in getattr(session, 'packets', [])]
        tcp_flags  = getattr(session, 'tcp_flags', set()) or set()
        has_tls    = bool(getattr(session, 'tls_snis', None))
        has_dns    = bool(getattr(session, 'dns_names', None))
        has_http   = bool(getattr(session, 'http_hosts', None))
        total_bytes= getattr(session, 'total_bytes', 0)
        duration   = getattr(session, 'duration_sec', 0.0)

        return self._score(
            dst_port=dst_port, src_port=src_port,
            transport=transport, pkt_sizes=pkt_sizes,
            pkt_times=pkt_times, tcp_flags=tcp_flags,
            has_tls=has_tls, has_dns=has_dns, has_http=has_http,
            total_bytes=total_bytes, duration=duration,
        )

    def score_dict(self, d: Dict[str, Any]) -> ProtocolAnomalyResult:
        """Score a plain dict (ingest event, shadow edge metadata, etc.)."""
        dst_port  = int(d.get('dst_port') or 0)
        src_port  = int(d.get('src_port') or 0)
        transport = (d.get('proto') or d.get('transport') or 'TCP').upper()
        pkt_sizes = d.get('pkt_sizes') or []
        pkt_times = d.get('pkt_times') or []
        tcp_flags = set(d.get('tcp_flags') or [])
        has_tls   = bool(d.get('tls_snis') or d.get('has_tls'))
        has_dns   = bool(d.get('dns_names') or d.get('has_dns'))
        has_http  = bool(d.get('http_hosts') or d.get('has_http'))

        return self._score(
            dst_port=dst_port, src_port=src_port,
            transport=transport, pkt_sizes=pkt_sizes,
            pkt_times=pkt_times, tcp_flags=tcp_flags,
            has_tls=has_tls, has_dns=has_dns, has_http=has_http,
            total_bytes=int(d.get('total_bytes') or 0),
            duration=float(d.get('duration_sec') or 0.0),
        )

    def behavioral_fingerprint(self, session: Any,
                                anomaly_score: float = 0.0) -> BehavioralFingerprint:
        """
        Compute statistical feature vector from a session.

        This vector is designed for future fusion into delta embeddings:
            fused_vec = alpha * text_embed + (1-alpha) * concat(text_embed, fingerprint)

        Result can also stand alone as a lightweight similarity key for
        "same actor, different IP" detection.
        """
        packets   = getattr(session, 'packets', [])
        pkt_sizes = [p.length for p in packets]
        pkt_times = sorted(p.timestamp for p in packets)
        tcp_flags = getattr(session, 'tcp_flags', set()) or set()
        proto     = (getattr(session, 'proto', '') or 'TCP').upper()

        avg_sz   = statistics.mean(pkt_sizes) if pkt_sizes else 0.0
        std_sz   = statistics.stdev(pkt_sizes) if len(pkt_sizes) > 1 else 0.0
        med_sz   = statistics.median(pkt_sizes) if pkt_sizes else 0.0
        cv       = (std_sz / avg_sz) if avg_sz > 0 else 0.0

        iats_ms: List[float] = []
        for i in range(1, len(pkt_times)):
            iats_ms.append((pkt_times[i] - pkt_times[i-1]) * 1000.0)
        avg_iat  = statistics.mean(iats_ms) if iats_ms else 0.0
        std_iat  = statistics.stdev(iats_ms) if len(iats_ms) > 1 else 0.0

        dur      = getattr(session, 'duration_sec', 0.0)
        total_b  = getattr(session, 'total_bytes', sum(pkt_sizes))
        bps      = (total_b / dur) if dur > 0 else 0.0

        return BehavioralFingerprint(
            avg_pkt_bytes    = round(avg_sz, 2),
            std_pkt_bytes    = round(std_sz, 2),
            cv_pkt_bytes     = round(cv, 4),
            median_pkt_bytes = round(med_sz, 2),
            avg_iat_ms       = round(avg_iat, 2),
            std_iat_ms       = round(std_iat, 2),
            pkt_count        = len(packets),
            total_bytes      = int(total_b),
            duration_sec     = round(dur, 3),
            bytes_per_sec    = round(bps, 2),
            dst_port         = int(getattr(session, 'dst_port', None) or 0),
            src_port         = int(getattr(session, 'src_port', None) or 0),
            proto_tcp        = 1 if proto == 'TCP'  else 0,
            proto_udp        = 1 if proto == 'UDP'  else 0,
            proto_icmp       = 1 if proto == 'ICMP' else 0,
            has_tls          = 1 if getattr(session, 'tls_snis', None) else 0,
            has_dns          = 1 if getattr(session, 'dns_names', None) else 0,
            has_http         = 1 if getattr(session, 'http_hosts', None) else 0,
            tcp_flag_syn     = 1 if 'S' in tcp_flags or 'SYN' in tcp_flags else 0,
            tcp_flag_rst     = 1 if 'R' in tcp_flags or 'RST' in tcp_flags else 0,
            tcp_flag_fin     = 1 if 'F' in tcp_flags or 'FIN' in tcp_flags else 0,
            anomaly_score    = round(anomaly_score, 4),
        )

    # ── Internal scoring ──────────────────────────────────────────────────────

    def _score(
        self,
        dst_port: int, src_port: int,
        transport: str,
        pkt_sizes: List[int],
        pkt_times: List[float],
        tcp_flags: Set[str],
        has_tls: bool, has_dns: bool, has_http: bool,
        total_bytes: int, duration: float,
    ) -> ProtocolAnomalyResult:

        violations: List[Violation] = []
        notes: List[str] = []

        # Probe port → try (port, transport) then (port, '*')
        port     = dst_port or src_port
        profile  = _IANA.get((port, transport)) or _IANA.get((port, '*'))
        svc_name = profile['name'] if profile else f"unregistered:{port}"

        # ── 1. Wrong transport for the port ──────────────────────────────────
        if profile:
            exp = profile.get('expected_proto', 'BOTH')
            if exp not in ('BOTH', transport):
                violations.append(Violation(
                    'wrong_transport',
                    _VW['wrong_transport'],
                    f"port {port} expects {exp} but got {transport}"
                ))

        # ── 2. Missing TLS where expected ────────────────────────────────────
        if profile and profile.get('expect_tls') and not has_tls:
            violations.append(Violation(
                'missing_tls',
                _VW['missing_tls'],
                f"port {port} ({svc_name}) expects TLS SNI but none observed — "
                "possible custom encrypted channel or MITM"
            ))

        # ── 3. Unexpected DNS ─────────────────────────────────────────────────
        if profile and not profile.get('expect_dns') and has_dns:
            violations.append(Violation(
                'unexpected_dns',
                _VW['unexpected_dns'],
                f"DNS payload on non-DNS port {port} ({svc_name}) — "
                "possible DNS covert channel"
            ))

        # ── 4. DNS tunnel heuristic (port 53 with large frames) ───────────────
        if port == 53 and pkt_sizes:
            avg_sz = sum(pkt_sizes) / len(pkt_sizes)
            if avg_sz > _DNS_TUNNEL_PKT_BYTES:
                violations.append(Violation(
                    'dns_tunnel',
                    _VW['dns_tunnel'],
                    f"avg DNS packet size {avg_sz:.0f}B > {_DNS_TUNNEL_PKT_BYTES}B — "
                    "strong DNS tunnel indicator"
                ))

        # ── 5. Oversized NTP ──────────────────────────────────────────────────
        if port == 123 and pkt_sizes:
            avg_sz = sum(pkt_sizes) / len(pkt_sizes)
            if avg_sz > 76:
                violations.append(Violation(
                    'oversized_ntp',
                    _VW['oversized_ntp'],
                    f"avg NTP packet {avg_sz:.0f}B > 76B standard — "
                    "possible NTP amplification or tunneling"
                ))

        # ── 6. Missing expected HTTP ──────────────────────────────────────────
        if profile and profile.get('expect_http') and not has_http:
            # Only flag if there's actual payload data to analyse
            if total_bytes > 500:
                violations.append(Violation(
                    'missing_expected_dpi',
                    _VW['missing_expected_dpi'],
                    f"port {port} ({svc_name}) expects HTTP Host but none seen — "
                    "possibly obfuscated HTTP or encrypted payload on cleartext port"
                ))

        # ── 7. Constant-size traffic (C2 beacon) ─────────────────────────────
        if len(pkt_sizes) >= 5:
            avg = sum(pkt_sizes) / len(pkt_sizes)
            if avg > 0:
                variance = sum((x - avg) ** 2 for x in pkt_sizes) / len(pkt_sizes)
                cv = (variance ** 0.5) / avg
                if cv < _CV_BEACON_THRESHOLD and avg > 20:
                    violations.append(Violation(
                        'constant_size_c2',
                        _VW['constant_size_c2'],
                        f"CV={cv:.4f} < {_CV_BEACON_THRESHOLD} — near-constant "
                        f"packet size ({avg:.0f}B) suggests beacon / C2 keepalive"
                    ))

        # ── 8. TCP SYN-only (scan / half-open) ───────────────────────────────
        if transport == 'TCP' and tcp_flags:
            flags_upper = {f.upper() for f in tcp_flags}
            has_syn = bool({'S', 'SYN'} & flags_upper)
            has_ack = bool({'A', 'ACK'} & flags_upper)
            has_rst = bool({'R', 'RST'} & flags_upper)
            if has_syn and not has_ack and not has_rst:
                violations.append(Violation(
                    'tcp_syn_only',
                    _VW['tcp_syn_only'],
                    "TCP session with SYN only (no ACK/RST) — port scan / "
                    "half-open probe"
                ))
            if has_rst and not has_syn and not has_ack:
                violations.append(Violation(
                    'tcp_rst_flood',
                    _VW['tcp_rst_flood'],
                    "RST flood — aggressive termination / TCP reset injection"
                ))

        # ── 9. High-risk port base score ──────────────────────────────────────
        if profile and profile.get('risk_base', 0) > 0:
            rb = profile['risk_base']
            violations.append(Violation(
                'risk_port',
                rb,
                f"port {port} ({svc_name}) has elevated IANA risk baseline {rb:.2f}"
                + (f" — {profile['note']}" if profile.get('note') else "")
            ))
            if profile.get('note'):
                notes.append(profile['note'])

        # ── Aggregate (cap at 1.0) ────────────────────────────────────────────
        raw = sum(v.score for v in violations)
        anomaly_score = round(min(1.0, raw), 4)

        return ProtocolAnomalyResult(
            anomaly_score  = anomaly_score,
            violations     = violations,
            expected_proto = svc_name,
            port           = port,
            transport      = transport,
            notes          = notes,
        )


# ── Module-level singleton ────────────────────────────────────────────────────
_pi: Optional[ProtocolIntel] = None


def get_protocol_intel() -> ProtocolIntel:
    """Return the shared ProtocolIntel instance (lazy init)."""
    global _pi
    if _pi is None:
        _pi = ProtocolIntel()
    return _pi
