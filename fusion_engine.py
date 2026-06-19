"""
fusion_engine.py — Multi-Signal Probabilistic Geolocation
==========================================================
Fuses RTT latency spheres, GeoIP point estimates, and ASN carrier profiles
into a confidence-weighted location distribution instead of a single noisy point.

Architecture:
    RTTAnalyzer          → statistical envelope + non-monotonic hop filtering
    ASNClassifier        → org-string → carrier type + routing penalty
    RobustDistanceEstimator → min-RTT + ASN-adjusted distance with confidence
    GeoFusion            → Bayesian RTT sphere × GeoIP → probability field
    FusionEngine         → orchestrates all signals → FusionResult
"""

import math
import re
import time
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Speed of light in fiber ≈ 200,000 km/s (refractive index ~1.5).
# One-way: 100,000 km/s. With ~50% routing overhead factor → 50 km/ms.
# Using min_rtt (least-congested path) instead of avg_rtt.
_MIN_RTT_KM_PER_MS = 50.0

# Legacy constant kept for backward compat references
_AVG_RTT_KM_PER_MS = 62.5

# Anomaly codes
ANOMALY_NON_MONOTONIC    = "non_monotonic"
ANOMALY_RTT_SPIKE        = "rtt_spike"
ANOMALY_PRIVATE_BACKBONE = "private_backbone"
ANOMALY_MOBILE_NAT       = "mobile_nat"
ANOMALY_SATELLITE        = "satellite"
ANOMALY_LOOPBACK         = "loopback"
ANOMALY_ASN_MISMATCH     = "asn_mismatch"
ANOMALY_MIMO_REASSEMBLY  = "mimo_reassembly"  # 5G packet-core reassembly spike

# ---------------------------------------------------------------------------
# MIMO-Aware Hop Class taxonomy
# ---------------------------------------------------------------------------
# Nine-class taxonomy derived from Verizon Home 5G traceroute analysis.
# Each class represents a distinct layer of the 5G → public internet path.

HOP_CLASS_RF_LINK               = "rf_link"
HOP_CLASS_MIMO_REASSEMBLY       = "mimo_reassembly"
HOP_CLASS_PACKET_CORE           = "packet_core"
HOP_CLASS_CGNAT_CLUSTER         = "cgnat_cluster"
HOP_CLASS_MPLS_PRIVATE_BACKBONE = "mpls_private_backbone"
HOP_CLASS_ACCESS_ROUTER         = "access_router"
HOP_CLASS_PEERING_EDGE          = "peering_edge"
HOP_CLASS_INTERNATIONAL_TRANSIT = "international_transit"
HOP_CLASS_DESTINATION           = "destination"
HOP_CLASS_UNKNOWN               = "unknown"

# Whether each class should be excluded from distance calculations
_HOP_CLASS_SKIP_DISTANCE = {
    HOP_CLASS_RF_LINK,           # air-interface: HARQ/MIMO latency, not geographic
    HOP_CLASS_MIMO_REASSEMBLY,   # packet-core reassembly spike
    HOP_CLASS_PACKET_CORE,       # pre-NAT internal transport
    HOP_CLASS_CGNAT_CLUSTER,     # NAT hop, no geographic meaning
    HOP_CLASS_MPLS_PRIVATE_BACKBONE,  # MPLS tunnel, synthetic RTT
}

# ── Hostname patterns ──────────────────────────────────────────────────────

# CPE / home router first-hop patterns
_RE_CPE = re.compile(
    r'(mynetworksettings|router\.local|gateway\.local|cpe\.|dsldevice|'
    r'home\.arpa|homerouter|openwrt|tplink|nighthawk|gateway\.lan|'
    r'fritz\.box|modem\.lan|broadband\.home)',
    re.I,
)

# CGNAT cluster: myvzw sub-domain with qarestr or similar cluster marker
_RE_CGNAT = re.compile(r'qarestr|cgnat|nat-cluster|natpool|natgw', re.I)

# Verizon mobile backbone domains
_RE_VZW = re.compile(r'myvzw\.com|vzwenfora|\.vzw\.|verizonwireless', re.I)

# Peering / transit exchange patterns
_RE_PEERING = re.compile(
    r'gtt\.net|alter\.net|cogentco\.com|level3\.net|lumen\.com|'
    r'telia\.net|tata\.com|zayo\.com|ntt\.net|pccwglobal\.com|'
    r'he\.net|equinix|ix\.|ixp\.|nanog|mae-|mix\.',
    re.I,
)

# International TLD patterns (non-US ccTLDs common in long-haul traces)
_RE_INTL_TLD = re.compile(
    r'\.(br|ar|mx|co|cl|pe|ve|py|uy|bo|ec|'     # LatAm
    r'de|fr|nl|uk|gb|it|es|pt|pl|se|no|dk|fi|'  # Europe
    r'jp|kr|cn|hk|sg|in|au|nz|za|'              # APAC / Africa
    r'ae|sa|il|tr)$',
    re.I,
)

# Embratel / Brazilian transit carriers
_RE_LATAM_TRANSIT = re.compile(
    r'embratel|oi\.net|vivo\.|telesp|gvt\.net|'
    r'atcmultimidia|cntfiber|ledinternet|unistar|'
    r'americamovil|telmex|centurylink\.com\.br',
    re.I,
)


def _is_private_ip(ip: str) -> bool:
    return bool(
        ip.startswith("10.")
        or ip.startswith("192.168.")
        or ip.startswith("172.")
        or ip.startswith("100.64.")
        or ip.startswith("fd")
        or ip.startswith("fe80")
    )


class MimoAwareHopClassifier:
    """
    Classifies each traceroute hop into one of nine semantic classes that
    reflect the physical and logical layers of a 5G MIMO → public internet path.

    Decision rules are deterministic and grounded in Verizon Home 5G traceroute
    evidence: rf_link → mimo_reassembly → packet_core → cgnat_cluster →
    mpls_private_backbone → access_router → peering_edge →
    international_transit → destination.

    The classifier is path-context-aware: it tracks the transition from the
    private carrier domain to the public internet and applies that state to
    disambiguate otherwise-identical hop signatures.
    """

    def classify_hops(self, hops: List[Dict]) -> List[Dict]:
        """
        Annotate each hop dict (in-place copy) with:
          hop_class           — one of the nine HOP_CLASS_* strings
          hop_class_confidence — float 0-1
          skip_distance       — bool: exclude from geographic distance calc
          mimo_context        — bool: True if hop is inside the 5G private domain

        Call AFTER RTTAnalyzer.filter_hops() so existing anomaly fields are present.
        """
        if not hops:
            return hops

        result = [dict(h) for h in hops]
        n = len(result)

        # ── Phase 1: detect 5G MIMO path signature ────────────────────────
        # Signature: hop 1 low RTT (< 10ms) + hop 2 private IP + large RTT spike
        hop1_rtt   = result[0].get("rtt_ms") or 0.0
        hop2_ip    = result[1].get("ip", "") if n > 1 else ""
        hop2_rtt   = result[1].get("rtt_ms") or 0.0 if n > 1 else 0.0
        is_5g_path = (
            hop1_rtt > 0
            and hop1_rtt < 10.0
            and _is_private_ip(hop2_ip)
            and hop2_rtt > hop1_rtt * 15  # ≥15× spike = packet-core reassembly
        )

        # ── Phase 2: find where private range ends ─────────────────────────
        last_private_idx = -1
        for i, h in enumerate(result):
            if _is_private_ip(h.get("ip", "")):
                last_private_idx = i

        # ── Phase 3: find peering / international transition ───────────────
        peering_idx = None
        intl_idx    = None
        for i, h in enumerate(result):
            hostname = h.get("hostname") or h.get("ip") or ""
            if peering_idx is None and _RE_PEERING.search(hostname):
                peering_idx = i
            if intl_idx is None and (
                _RE_LATAM_TRANSIT.search(hostname)
                or _RE_INTL_TLD.search(hostname.rstrip(".").rsplit(".", 1)[-1] if "." in hostname else "")
            ):
                intl_idx = i

        # ── Phase 4: classify each hop ─────────────────────────────────────
        for i, hop in enumerate(result):
            ip       = hop.get("ip", "")
            hostname = hop.get("hostname") or ip or ""
            rtt      = hop.get("rtt_ms") or 0.0
            is_last  = (i == n - 1)
            is_priv  = _is_private_ip(ip)
            cls      = HOP_CLASS_UNKNOWN
            conf     = 0.5

            if is_last and i > 0:
                cls, conf = HOP_CLASS_DESTINATION, 0.90

            elif i == 0:
                if is_5g_path and (_RE_CPE.search(hostname) or rtt < 6.0):
                    cls, conf = HOP_CLASS_RF_LINK, 0.92
                else:
                    cls, conf = HOP_CLASS_RF_LINK, 0.70  # first hop always rf_link
                                                          # even without 5G signature

            elif is_5g_path and i == 1 and is_priv and hop2_rtt > 100.0:
                # The big reassembly spike: UPF/S-GW/P-GW layer
                cls, conf = HOP_CLASS_MIMO_REASSEMBLY, 0.95

            elif is_priv and is_5g_path:
                # Subsequent private hops after reassembly = packet core
                cls, conf = HOP_CLASS_PACKET_CORE, 0.88

            elif not is_priv and i == last_private_idx + 1 and last_private_idx >= 0:
                # First public hop after the private range
                if _RE_CGNAT.search(hostname) or _RE_VZW.search(hostname):
                    cls, conf = HOP_CLASS_CGNAT_CLUSTER, 0.90
                elif _RE_VZW.search(hostname):
                    cls, conf = HOP_CLASS_MPLS_PRIVATE_BACKBONE, 0.85
                else:
                    cls, conf = HOP_CLASS_CGNAT_CLUSTER, 0.65

            elif _RE_VZW.search(hostname):
                # Inside Verizon mobile backbone after CGNAT
                if peering_idx is not None and i >= peering_idx - 2:
                    cls, conf = HOP_CLASS_ACCESS_ROUTER, 0.85
                else:
                    cls, conf = HOP_CLASS_MPLS_PRIVATE_BACKBONE, 0.88

            elif peering_idx is not None and i == peering_idx:
                cls, conf = HOP_CLASS_PEERING_EDGE, 0.90

            elif intl_idx is not None and i >= intl_idx:
                cls, conf = HOP_CLASS_INTERNATIONAL_TRANSIT, 0.88

            else:
                # Generic public internet hop — use RTT context
                prev_clean_rtts = [
                    result[j].get("rtt_ms") or 0.0
                    for j in range(max(0, i - 3), i)
                    if not result[j].get("hop_class") in _HOP_CLASS_SKIP_DISTANCE
                    and result[j].get("rtt_ms")
                ]
                prev_rtt = prev_clean_rtts[-1] if prev_clean_rtts else 0.0
                if prev_rtt > 0 and rtt > prev_rtt + 100:
                    cls, conf = HOP_CLASS_INTERNATIONAL_TRANSIT, 0.75
                elif peering_idx is None and _RE_PEERING.search(hostname):
                    cls, conf = HOP_CLASS_PEERING_EDGE, 0.80
                else:
                    cls, conf = HOP_CLASS_ACCESS_ROUTER, 0.55

            hop["hop_class"]            = cls
            hop["hop_class_confidence"] = round(conf, 2)
            hop["skip_distance"]        = cls in _HOP_CLASS_SKIP_DISTANCE
            hop["mimo_context"]         = is_5g_path and i <= max(last_private_idx, 1)

            # Upgrade private_backbone anomaly to mimo_reassembly where appropriate
            if cls == HOP_CLASS_MIMO_REASSEMBLY and hop.get("anomaly") == ANOMALY_PRIVATE_BACKBONE:
                hop["anomaly"] = ANOMALY_MIMO_REASSEMBLY

        return result

    def best_distance_hops(self, classified_hops: List[Dict]) -> List[Dict]:
        """Return only hops suitable for geographic distance estimation."""
        return [h for h in classified_hops if not h.get("skip_distance")]

    def path_summary(self, classified_hops: List[Dict]) -> Dict:
        """High-level summary of the classified path."""
        classes = [h.get("hop_class", HOP_CLASS_UNKNOWN) for h in classified_hops]
        is_5g   = any(c in (HOP_CLASS_RF_LINK, HOP_CLASS_MIMO_REASSEMBLY,
                             HOP_CLASS_PACKET_CORE) for c in classes)
        is_intl = HOP_CLASS_INTERNATIONAL_TRANSIT in classes
        transition_hops = {
            cls: next((h["hop"] for h in classified_hops
                        if h.get("hop_class") == cls), None)
            for cls in [HOP_CLASS_CGNAT_CLUSTER, HOP_CLASS_PEERING_EDGE,
                        HOP_CLASS_INTERNATIONAL_TRANSIT, HOP_CLASS_DESTINATION]
        }
        return {
            "is_5g_mimo_path":    is_5g,
            "is_international":   is_intl,
            "class_sequence":     classes,
            "transition_hops":    {k: v for k, v in transition_hops.items() if v is not None},
            "distance_hop_count": sum(1 for h in classified_hops if not h.get("skip_distance")),
        }


# ---------------------------------------------------------------------------
# ASN Classifier
# ---------------------------------------------------------------------------

# Keyed by ASN number (as string, no "AS" prefix) or keyword patterns on org string
_ASN_BY_NUMBER: Dict[str, str] = {
    # Tier-1 backbone
    "701": "tier1", "1": "tier1", "3356": "tier1", "3549": "tier1",
    "6453": "tier1", "6762": "tier1", "5511": "tier1", "7018": "tier1",
    "7922": "cable",   # Comcast
    "20001": "cable",  # Time Warner / Charter
    "11351": "cable",  # Charter
    "15169": "hyperscaler",  # Google
    "16509": "hyperscaler",  # Amazon AWS
    "13335": "hyperscaler",  # Cloudflare
    "8075":  "hyperscaler",  # Microsoft Azure
    # Mobile
    "6167": "mobile",  "22394": "mobile", "21928": "mobile",
    "20115": "mobile", "13057": "mobile",
    # Satellite
    "14593": "satellite",  # Starlink
    "11042": "satellite",  # HughesNet
}

_ORG_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r'starlink|spacex', re.I),          "satellite"),
    (re.compile(r'hughes|wildblue|viasat|inmarsat|iridium', re.I), "satellite"),
    (re.compile(r'wireless|mobile|cellular|sprint|t-mobile|tmobile|verizon wireless|at&t mobility', re.I), "mobile"),
    (re.compile(r'myvzw\.com|vzwenfora|vzw ', re.I), "mobile"),
    (re.compile(r'mullvad|nordvpn|expressvpn|protonvpn|pia |privateinternetaccess|surfshark|torguard|vpn', re.I), "vpn"),
    (re.compile(r'tor |torproject|onion', re.I),     "vpn"),
    (re.compile(r'google|amazon|aws|cloudflare|microsoft azure|fastly|akamai', re.I), "hyperscaler"),
    (re.compile(r'comcast|xfinity|charter|spectrum|cox |optimum|cablevision|mediacom', re.I), "cable"),
    (re.compile(r'level\s*3|lumen|centurylink|telia|ntt|cogent|tata|zayo|gtt |telelumen', re.I), "tier1"),
]

_PROFILE: Dict[str, Dict[str, Any]] = {
    "tier1":       {"penalty": 1.15, "uncertainty_km": 500,  "geoip_score": 0.85},
    "cable":       {"penalty": 1.45, "uncertainty_km": 800,  "geoip_score": 0.80},
    "mobile":      {"penalty": 2.20, "uncertainty_km": 1200, "geoip_score": 0.35},
    "hyperscaler": {"penalty": 1.08, "uncertainty_km": 300,  "geoip_score": 0.90},
    "vpn":         {"penalty": 3.00, "uncertainty_km": 5000, "geoip_score": 0.10},
    "satellite":   {"penalty": 8.00, "uncertainty_km": 3000, "geoip_score": 0.25},
    "unknown":     {"penalty": 1.60, "uncertainty_km": 1000, "geoip_score": 0.70},
}


class ASNClassifier:
    """Classify an org/ASN string into a carrier type and return its routing profile."""

    def classify(self, org: str, asn: str = "") -> str:
        """Return carrier type string."""
        if asn:
            clean = asn.upper().lstrip("AS").strip()
            if clean in _ASN_BY_NUMBER:
                return _ASN_BY_NUMBER[clean]
        if org:
            for pat, kind in _ORG_PATTERNS:
                if pat.search(org):
                    return kind
        return "unknown"

    def profile(self, org: str, asn: str = "") -> Dict[str, Any]:
        kind = self.classify(org, asn)
        return {"type": kind, **_PROFILE[kind]}

    def anomaly_for_type(self, kind: str) -> Optional[str]:
        if kind == "mobile":
            return ANOMALY_MOBILE_NAT
        if kind == "satellite":
            return ANOMALY_SATELLITE
        return None


_asn_classifier = ASNClassifier()


# ---------------------------------------------------------------------------
# RTT Analyzer
# ---------------------------------------------------------------------------

class RTTAnalyzer:
    """Extract statistical envelope from ping RTT samples and classify traceroute hops."""

    def stats(self, samples: List[float]) -> Dict[str, float]:
        """Return full percentile envelope from a list of RTT samples (ms)."""
        if not samples:
            return {}
        s = sorted(samples)
        n = len(s)
        p25 = s[max(0, n // 4)]
        p75 = s[min(n - 1, 3 * n // 4)]
        return {
            "min":    round(s[0], 3),
            "p25":    round(p25, 3),
            "median": round(s[n // 2], 3),
            "p75":    round(p75, 3),
            "max":    round(s[-1], 3),
            "jitter": round(p75 - p25, 3),  # IQR as congestion proxy
        }

    def filter_hops(self, hops: List[Dict]) -> List[Dict]:
        """
        Annotate each hop with anomaly codes. Does NOT remove hops — callers
        should exclude anomaly-flagged hops from distance calculations.

        Anomaly rules:
          - loopback / private IP          → private_backbone or loopback
          - rtt_ms decrease > 15% from prev non-anomaly hop → non_monotonic
          - rtt_ms > 3× prev non-anomaly hop               → rtt_spike
        """
        annotated = []
        prev_clean_rtt = 0.0

        for h in hops:
            hop = dict(h)
            ip = hop.get("ip", "")
            rtt = hop.get("rtt_ms") or 0.0
            anomaly = None

            # Private / loopback detection
            if ip in ("*", "") or ip.startswith("127.") or ip == "::1":
                anomaly = ANOMALY_LOOPBACK
            elif (ip.startswith("10.") or
                  ip.startswith("192.168.") or
                  ip.startswith("172.") or
                  ip.startswith("100.64.") or
                  ip.startswith("fd") or
                  ip.startswith("fe80")):
                anomaly = ANOMALY_PRIVATE_BACKBONE
            elif prev_clean_rtt > 0 and rtt > 0:
                if rtt < prev_clean_rtt * 0.85:
                    anomaly = ANOMALY_NON_MONOTONIC
                elif rtt > prev_clean_rtt * 3.5:
                    anomaly = ANOMALY_RTT_SPIKE

            hop["anomaly"] = anomaly
            if anomaly is None and rtt > 0:
                prev_clean_rtt = rtt

            annotated.append(hop)

        # Compute delta RTT/km between consecutive clean hops
        last_clean_rtt = 0.0
        for hop in annotated:
            if hop["anomaly"] is None:
                rtt = hop.get("rtt_ms") or 0.0
                delta_rtt = max(0.0, rtt - last_clean_rtt)
                hop["delta_rtt_ms"] = round(delta_rtt, 3)
                hop["delta_km"] = round(delta_rtt * _MIN_RTT_KM_PER_MS, 1)
                last_clean_rtt = rtt
            else:
                hop.setdefault("delta_rtt_ms", None)
                hop.setdefault("delta_km", None)

        return annotated


# ---------------------------------------------------------------------------
# Robust Distance Estimator
# ---------------------------------------------------------------------------

class RobustDistanceEstimator:
    """
    Convert min-RTT + ASN profile into a distance estimate with confidence interval.

    Key insight: use min_rtt (fastest observed path ≈ least congested) rather than
    avg_rtt, then penalize by ASN routing overhead factor.
    """

    def estimate(
        self,
        rtt_min_ms: float,
        rtt_jitter_ms: float = 0.0,
        asn_type: str = "unknown",
        asn_penalty: float = 1.0,
    ) -> Dict[str, Any]:
        """Return distance estimate with confidence and min/max bounds."""
        if rtt_min_ms <= 0:
            return {"estimate_km": None, "confidence": 0.0}

        profile = _PROFILE.get(asn_type, _PROFILE["unknown"])
        penalty = asn_penalty if asn_penalty > 1.0 else profile["penalty"]

        # Base: min_rtt × 50 km/ms (fiber + routing overhead)
        raw_km = rtt_min_ms * _MIN_RTT_KM_PER_MS

        # Adjusted: divide by penalty (higher penalty = routing adds fake distance)
        adjusted_km = raw_km / penalty

        # Confidence: penalised by jitter relative to min (noisy path = low confidence)
        jitter_ratio = rtt_jitter_ms / max(rtt_min_ms, 1.0)
        confidence = max(0.05, min(0.98, 1.0 - min(jitter_ratio, 0.9)))

        # Bounds: ±uncertainty scaled by confidence
        uncertainty = profile["uncertainty_km"]
        margin = uncertainty * (1.0 - confidence * 0.5)

        return {
            "estimate_km":  round(adjusted_km, 1),
            "min_km":       round(max(0, adjusted_km - margin), 1),
            "max_km":       round(adjusted_km + margin, 1),
            "confidence":   round(confidence, 3),
            "asn_type":     asn_type,
            "asn_penalty":  round(penalty, 2),
            "uncertainty_km": round(uncertainty, 0),
        }


# ---------------------------------------------------------------------------
# GeoFusion
# ---------------------------------------------------------------------------

def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


class GeoFusion:
    """
    Bayesian fusion of RTT distance sphere and GeoIP point estimate.

    Produces a `location_distribution` object suitable for Cesium rendering
    and GraphOps hypergraph node annotation.
    """

    def fuse(
        self,
        server_lat: float,
        server_lon: float,
        geoip: Optional[Dict],
        distance_est: Dict,
    ) -> Dict[str, Any]:
        """
        Combine RTT sphere (centered on server) and GeoIP point.

        Returns a location_distribution dict with centroids, uncertainty,
        confidence, and per-evidence breakdown.
        """
        rtt_km       = distance_est.get("estimate_km")
        rtt_conf     = distance_est.get("confidence", 0.5)
        asn_type     = distance_est.get("asn_type", "unknown")
        profile      = _PROFILE.get(asn_type, _PROFILE["unknown"])
        geoip_score  = profile["geoip_score"]

        centroids = []
        geo_lat = geo_lon = None

        if geoip and geoip.get("lat") and geoip.get("lon"):
            geo_lat = float(geoip["lat"])
            geo_lon = float(geoip["lon"])

        # Consistency check: does GeoIP point lie near the RTT sphere?
        asn_mismatch = False
        if rtt_km and geo_lat is not None:
            actual_km = _haversine(server_lat, server_lon, geo_lat, geo_lon)
            ratio = actual_km / max(rtt_km, 1.0)
            # GeoIP says it's much farther or closer than RTT suggests → mismatch
            if ratio > 3.0 or ratio < 0.1:
                asn_mismatch = True
                geoip_score *= 0.3   # dramatically reduce trust
                logger.debug(
                    "GeoFusion: ASN mismatch (RTT implies %.0f km, GeoIP %.0f km)",
                    rtt_km, actual_km
                )

        # Primary centroid: GeoIP if available, else server position
        if geo_lat is not None:
            geoip_weight = geoip_score * rtt_conf
            centroids.append({
                "lat":    round(geo_lat, 4),
                "lon":    round(geo_lon, 4),
                "weight": round(geoip_weight, 3),
                "source": "geoip",
            })

        # Secondary centroid: point on great circle from server toward GeoIP
        # at the RTT-estimated distance (or due North if no GeoIP)
        if rtt_km and rtt_km > 0:
            if geo_lat is not None:
                bearing = math.atan2(
                    math.sin(math.radians(geo_lon - server_lon)) * math.cos(math.radians(geo_lat)),
                    math.cos(math.radians(server_lat)) * math.sin(math.radians(geo_lat)) -
                    math.sin(math.radians(server_lat)) * math.cos(math.radians(geo_lat)) *
                    math.cos(math.radians(geo_lon - server_lon))
                )
            else:
                bearing = 0.0
            R = 6371.0
            d = rtt_km / R
            rtt_lat = math.degrees(math.asin(
                math.sin(math.radians(server_lat)) * math.cos(d) +
                math.cos(math.radians(server_lat)) * math.sin(d) * math.cos(bearing)
            ))
            rtt_lon = server_lon + math.degrees(math.atan2(
                math.sin(bearing) * math.sin(d) * math.cos(math.radians(server_lat)),
                math.cos(d) - math.sin(math.radians(server_lat)) * math.sin(math.radians(rtt_lat))
            ))
            rtt_weight = rtt_conf * (1.0 - geoip_score * 0.5)
            if any(abs(c["lat"] - rtt_lat) > 0.5 or abs(c["lon"] - rtt_lon) > 0.5 for c in centroids) or not centroids:
                centroids.append({
                    "lat":    round(rtt_lat, 4),
                    "lon":    round(rtt_lon, 4),
                    "weight": round(rtt_weight, 3),
                    "source": "rtt_sphere",
                })

        # Normalize weights
        total_w = sum(c["weight"] for c in centroids) or 1.0
        for c in centroids:
            c["weight"] = round(c["weight"] / total_w, 3)

        # Overall confidence
        evidence = {
            "geoip":       round(geoip_score, 3),
            "rtt":         round(rtt_conf, 3),
            "asn":         round(1.0 / profile["penalty"], 3),
        }
        scores = list(evidence.values())
        harmonic = len(scores) / sum(1.0 / max(s, 0.01) for s in scores)
        overall_conf = round(min(harmonic, 0.99), 3)

        return {
            "type":           "gaussian_mixture",
            "centroids":      centroids,
            "uncertainty_km": distance_est.get("uncertainty_km", profile["uncertainty_km"]),
            "confidence":     overall_conf,
            "asn_mismatch":   asn_mismatch,
            "evidence":       evidence,
        }


# ---------------------------------------------------------------------------
# FusionResult
# ---------------------------------------------------------------------------

@dataclass
class FusionResult:
    target:               str
    lat:                  Optional[float]
    lon:                  Optional[float]
    uncertainty_km:       float
    confidence:           float
    distance_estimate_km: Optional[float]
    distance_min_km:      Optional[float]
    distance_max_km:      Optional[float]
    asn_type:             str
    asn_penalty:          float
    anomalies:            List[str]        = field(default_factory=list)
    location_distribution: Dict            = field(default_factory=dict)
    evidence:             Dict             = field(default_factory=dict)
    rtt_stats:            Dict             = field(default_factory=dict)
    hops:                 List[Dict]       = field(default_factory=list)
    geoip:                Optional[Dict]   = None
    path_summary:         Dict             = field(default_factory=dict)
    timestamp:            float            = field(default_factory=time.time)

    def to_dict(self) -> Dict:
        return {
            "target":               self.target,
            "lat":                  self.lat,
            "lon":                  self.lon,
            "uncertainty_km":       self.uncertainty_km,
            "confidence":           self.confidence,
            "distance_estimate_km": self.distance_estimate_km,
            "distance_min_km":      self.distance_min_km,
            "distance_max_km":      self.distance_max_km,
            "asn_type":             self.asn_type,
            "asn_penalty":          self.asn_penalty,
            "anomalies":            self.anomalies,
            "location_distribution": self.location_distribution,
            "evidence":             self.evidence,
            "rtt_stats":            self.rtt_stats,
            "hops":                 self.hops,
            "geoip":                self.geoip,
            "path_summary":         self.path_summary,
            "timestamp":            self.timestamp,
        }


# ---------------------------------------------------------------------------
# FusionEngine
# ---------------------------------------------------------------------------

class FusionEngine:
    """
    Orchestrates RTT analysis, ASN classification, distance estimation,
    and GeoIP fusion into a single FusionResult.

    Usage:
        engine = FusionEngine(server_lat=45.52, server_lon=-122.68)
        result = engine.analyze(
            target="8.8.8.8",
            rtt_samples=[8.2, 8.5, 9.1, 8.8, 14.3],
            geoip={"lat": 37.40, "lon": -122.08, "org": "Google LLC"},
            hops=[...],   # from traceroute (optional)
        )
    """

    def __init__(self, server_lat: float = 0.0, server_lon: float = 0.0):
        self.server_lat = server_lat
        self.server_lon = server_lon
        self._rtt     = RTTAnalyzer()
        self._asn     = ASNClassifier()
        self._dist    = RobustDistanceEstimator()
        self._geo     = GeoFusion()
        self._mimo    = MimoAwareHopClassifier()

    def analyze(
        self,
        target: str,
        rtt_samples: List[float],
        geoip: Optional[Dict] = None,
        hops: Optional[List[Dict]] = None,
    ) -> FusionResult:

        # 1. RTT statistics
        stats = self._rtt.stats(rtt_samples) if rtt_samples else {}
        rtt_min    = stats.get("min", 0.0)
        rtt_jitter = stats.get("jitter", 0.0)

        # 2. ASN classification from GeoIP org field
        org = (geoip or {}).get("org", "") or ""
        asn_str = (geoip or {}).get("as", "") or ""
        asn_type = self._asn.classify(org, asn_str)
        profile  = _PROFILE.get(asn_type, _PROFILE["unknown"])

        # 3. Anomaly collection
        anomalies: List[str] = []
        asn_anomaly = self._asn.anomaly_for_type(asn_type)
        if asn_anomaly:
            anomalies.append(asn_anomaly)

        # 4. Hop annotation + MIMO classification
        annotated_hops: List[Dict] = []
        path_summary: Dict = {}
        if hops:
            annotated_hops = self._rtt.filter_hops(hops)
            annotated_hops = self._mimo.classify_hops(annotated_hops)
            path_summary   = self._mimo.path_summary(annotated_hops)
            hop_anomalies  = [h["anomaly"] for h in annotated_hops if h.get("anomaly")]
            for a in hop_anomalies:
                if a not in anomalies:
                    anomalies.append(a)
            # If 5G MIMO path detected, promote asn_type to mobile
            if path_summary.get("is_5g_mimo_path") and asn_type == "unknown":
                asn_type = "mobile"

        # 5. Distance estimate
        dist_est = self._dist.estimate(
            rtt_min_ms=rtt_min,
            rtt_jitter_ms=rtt_jitter,
            asn_type=asn_type,
            asn_penalty=profile["penalty"],
        )

        # 6. Geo fusion
        location_dist = self._geo.fuse(
            server_lat=self.server_lat,
            server_lon=self.server_lon,
            geoip=geoip,
            distance_est={**dist_est, "uncertainty_km": profile["uncertainty_km"]},
        )
        if location_dist.get("asn_mismatch") and ANOMALY_ASN_MISMATCH not in anomalies:
            anomalies.append(ANOMALY_ASN_MISMATCH)

        # 7. Best-estimate lat/lon from top centroid
        centroids = location_dist.get("centroids", [])
        top = max(centroids, key=lambda c: c["weight"]) if centroids else {}
        best_lat = top.get("lat")
        best_lon = top.get("lon")

        return FusionResult(
            target               = target,
            lat                  = best_lat,
            lon                  = best_lon,
            uncertainty_km       = location_dist.get("uncertainty_km", profile["uncertainty_km"]),
            confidence           = location_dist.get("confidence", 0.5),
            distance_estimate_km = dist_est.get("estimate_km"),
            distance_min_km      = dist_est.get("min_km"),
            distance_max_km      = dist_est.get("max_km"),
            asn_type             = asn_type,
            asn_penalty          = profile["penalty"],
            anomalies            = anomalies,
            location_distribution= location_dist,
            evidence             = location_dist.get("evidence", {}),
            rtt_stats            = stats,
            hops                 = annotated_hops,
            geoip                = geoip,
            path_summary         = path_summary,
        )


# ---------------------------------------------------------------------------
# Module-level singleton helpers
# ---------------------------------------------------------------------------

_engine: Optional[FusionEngine] = None


def get_fusion_engine(server_lat: float = 0.0, server_lon: float = 0.0) -> FusionEngine:
    """Return (or lazily create) a module-level FusionEngine singleton."""
    global _engine
    if _engine is None:
        _engine = FusionEngine(server_lat=server_lat, server_lon=server_lon)
    return _engine


def build_ssе_event(result: FusionResult) -> Dict:
    """Format a FusionResult as a RECON_LATENCY_ANALYSIS SSE payload."""
    return {
        "type":   "RECON_LATENCY_ANALYSIS",
        "entity": result.target,
        "metrics": {
            "min_rtt":    result.rtt_stats.get("min"),
            "median_rtt": result.rtt_stats.get("median"),
            "jitter":     result.rtt_stats.get("jitter"),
        },
        "distance": {
            "estimate_km": result.distance_estimate_km,
            "min_km":      result.distance_min_km,
            "max_km":      result.distance_max_km,
            "confidence":  result.confidence,
        },
        "location_distribution": result.location_distribution,
        "anomalies": result.anomalies,
        "asn_type":  result.asn_type,
        "timestamp": result.timestamp,
    }
