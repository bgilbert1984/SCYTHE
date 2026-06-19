"""
cluster_swarm_engine.py — Hypergraph → CyberCluster Swarm Summarizer

Collapses HypergraphEngine node/edge data into tactical "swarm objects"
suitable for CoT emission and ATAK animated overlays.

Pipeline:
    HypergraphEngine snapshot (nodes, edges)
        ↓
    geo-bucket + BSG behavior grouping
        ↓
    CyberCluster summaries (centroid, nodeCount, threatScore, velocity…)
        ↓
    CoT XML (type="cyber.swarm.*") ready for ATAK injection

Usage:
    from cluster_swarm_engine import detect_clusters, cluster_to_cot

    snap   = engine.snapshot()   # {nodes, edges}
    swarms = detect_clusters(snap['nodes'], snap['edges'])
    cot    = [cluster_to_cot(s) for s in swarms]
"""
from __future__ import annotations

import hashlib
import ipaddress
import logging
import math
import os
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
GEO_BUCKET_DEG   = 1.0    # 1° ≈ 111 km — primary cluster granularity
MIN_CLUSTER_SIZE = 2       # discard singleton clusters
THREAT_WEIGHTS   = {       # weights per obs_class for threat score
    'observed':  1.0,
    'inferred':  0.7,
    'predicted': 0.5,
    'unknown':   0.3,
}
KIND_RF_EMITTER  = {'rf_emitter', 'rf_signal', 'signal'}
KIND_UAV         = {'drone', 'uav', 'aircraft'}
KIND_SENSOR      = {'sensor', 'sdr', 'iot'}
KIND_C2          = {'c2', 'command', 'controller', 'c2_node'}

# ---------------------------------------------------------------------------
# ASN / Infrastructure Fusion — MaxMind GeoLite2 resolver
# ---------------------------------------------------------------------------
_ASN_DB = None
_CITY_DB = None
_ASN_CACHE: Dict[str, Optional[Dict]] = {}   # ip → resolved record (LRU-ish)
_ASN_CACHE_MAX = 10_000

_MMDB_BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets')
_ASN_MMDB  = os.path.join(_MMDB_BASE, 'GeoLite2-ASN.mmdb')
_CITY_MMDB = os.path.join(_MMDB_BASE, 'GeoLite2-City.mmdb')

# pyasn radix-tree database for sub-microsecond prefix→ASN lookup
_PYASN_DB = None
_PYASN_DAT = os.path.join(_MMDB_BASE, 'pyasn-master', 'data',
                           'ipasn_20140513.dat.gz')
_PYASN_NAMES = os.path.join(_MMDB_BASE, 'pyasn-master', 'data',
                             'asnames.json')


def _open_asn_db():
    """Lazy-open MaxMind ASN database."""
    global _ASN_DB
    if _ASN_DB is not None:
        return _ASN_DB
    try:
        import maxminddb
        _ASN_DB = maxminddb.open_database(_ASN_MMDB)
        logger.info("[ASN] Opened GeoLite2-ASN.mmdb")
    except Exception as exc:
        logger.warning("[ASN] Cannot open GeoLite2-ASN.mmdb: %s", exc)
        _ASN_DB = False   # sentinel — don't retry
    return _ASN_DB


def _open_city_db():
    """Lazy-open MaxMind City database for country/city enrichment."""
    global _CITY_DB
    if _CITY_DB is not None:
        return _CITY_DB
    try:
        import maxminddb
        _CITY_DB = maxminddb.open_database(_CITY_MMDB)
        logger.info("[ASN] Opened GeoLite2-City.mmdb")
    except Exception as exc:
        logger.warning("[ASN] Cannot open GeoLite2-City.mmdb: %s", exc)
        _CITY_DB = False
    return _CITY_DB


def _open_pyasn_db():
    """Lazy-open pyasn radix-tree database for fast prefix→ASN resolution."""
    global _PYASN_DB
    if _PYASN_DB is not None:
        return _PYASN_DB
    try:
        import pyasn as _pyasn_mod
        kwargs = {}
        if os.path.isfile(_PYASN_NAMES):
            kwargs['as_names_file'] = _PYASN_NAMES
        _PYASN_DB = _pyasn_mod.pyasn(_PYASN_DAT, **kwargs)
        logger.info("[ASN] Opened pyasn radix DB (%s)", _PYASN_DAT)
    except Exception as exc:
        logger.warning("[ASN] Cannot open pyasn: %s", exc)
        _PYASN_DB = False
    return _PYASN_DB


def resolve_ip(ip: str) -> Optional[Dict[str, Any]]:
    """
    Resolve a single IP to ASN/org/country via pyasn (fast radix) + MaxMind GeoLite2.

    Resolution order: cache → pyasn (sub-μs) → MaxMind ASN → MaxMind City.
    Returns dict with keys: asn_number, asn_org, country, city, prefix
    or None if resolution fails.
    """
    if ip in _ASN_CACHE:
        return _ASN_CACHE[ip]

    # Validate IP format and skip private/reserved
    try:
        addr = ipaddress.ip_address(ip)
        if addr.is_private or addr.is_reserved or addr.is_loopback:
            _ASN_CACHE[ip] = None
            return None
    except ValueError:
        _ASN_CACHE[ip] = None
        return None

    result: Dict[str, Any] = {}

    # Fast path: pyasn radix-tree (sub-microsecond, prefix-level)
    pyasn_db = _open_pyasn_db()
    if pyasn_db and pyasn_db is not False:
        try:
            asn_num, prefix = pyasn_db.lookup(ip)
            if asn_num:
                result['asn_number'] = asn_num
                result['prefix'] = prefix or ''
                # Try to get AS name from pyasn's names DB
                try:
                    name = pyasn_db.get_as_name(asn_num)
                    if name:
                        result['asn_org'] = name
                except Exception:
                    pass
        except Exception:
            pass

    # MaxMind ASN (more current org names, fills gaps)
    asn_db = _open_asn_db()
    if asn_db and asn_db is not False:
        try:
            rec = asn_db.get(ip)
            if rec:
                if not result.get('asn_number'):
                    result['asn_number'] = rec.get('autonomous_system_number')
                # Always prefer MaxMind org name (more current than pyasn)
                mmdb_org = rec.get('autonomous_system_organization', '')
                if mmdb_org:
                    result['asn_org'] = mmdb_org
        except Exception:
            pass

    # City/country lookup
    city_db = _open_city_db()
    if city_db and city_db is not False:
        try:
            rec = city_db.get(ip)
            if rec:
                result['country'] = (rec.get('country') or {}).get('iso_code', '')
                result['city'] = ((rec.get('city') or {})
                                  .get('names', {}).get('en', ''))
        except Exception:
            pass

    final = result if result.get('asn_number') else None
    _ASN_CACHE[ip] = final
    if len(_ASN_CACHE) > _ASN_CACHE_MAX:
        # Evict oldest ~20%
        keys = list(_ASN_CACHE.keys())
        for k in keys[:len(keys) // 5]:
            del _ASN_CACHE[k]
    return final


# Well-known ASN → infrastructure type mapping
_ASN_INFRA_MAP: Dict[int, str] = {
    # Hyperscalers
    16509: 'Hyperscaler',     # Amazon / AWS
    14618: 'Hyperscaler',     # Amazon
    15169: 'Hyperscaler',     # Google
    396982: 'Hyperscaler',    # Google Cloud
    8075:  'Hyperscaler',     # Microsoft / Azure
    36351: 'Hyperscaler',     # Microsoft (Softlayer)
    45090: 'Hyperscaler',     # Alibaba Cloud
    132203: 'Hyperscaler',    # Tencent Cloud
    # Edge / CDN
    13335: 'Edge CDN',        # Cloudflare
    20940: 'Edge CDN',        # Akamai
    54113: 'Edge CDN',        # Fastly
    16625: 'Edge CDN',        # Akamai
    # Hosting / VPS (common C2 infra)
    14061: 'VPS Provider',    # DigitalOcean
    63949: 'VPS Provider',    # Linode / Akamai
    24940: 'VPS Provider',    # Hetzner
    16276: 'VPS Provider',    # OVHcloud
    51167: 'VPS Provider',    # Contabo
    20473: 'VPS Provider',    # Vultr / Choopa
    # Backbone / Transit
    3356:  'Backbone',        # Lumen / Level 3
    174:   'Backbone',        # Cogent
    6939:  'Backbone',        # Hurricane Electric
    1299:  'Backbone',        # Arelion (Telia)
    6461:  'Backbone',        # Zayo
    # ISP / Consumer
    7922:  'ISP',             # Comcast
    22773: 'ISP',             # Cox
    20001: 'ISP',             # Charter / Spectrum
    7018:  'ISP',             # AT&T
    701:   'ISP',             # Verizon
    # Research / Government
    11164: 'Research',        # Internet2
    5765:  'Government',      # DoD Network Information Center
}


def classify_infra(asn_number: Optional[int], asn_org: str = '',
                   pattern: str = '') -> str:
    """Classify infrastructure type from ASN number/org name + behavior pattern."""
    if asn_number and asn_number in _ASN_INFRA_MAP:
        return _ASN_INFRA_MAP[asn_number]

    org_lower = asn_org.lower()
    if any(k in org_lower for k in ('amazon', 'aws', 'google', 'microsoft',
                                     'azure', 'alibaba', 'tencent')):
        return 'Hyperscaler'
    if any(k in org_lower for k in ('cloudflare', 'akamai', 'fastly', 'cdn')):
        return 'Edge CDN'
    if any(k in org_lower for k in ('digital ocean', 'digitalocean', 'linode',
                                     'hetzner', 'ovh', 'vultr', 'contabo')):
        return 'VPS Provider'
    if any(k in org_lower for k in ('comcast', 'cox', 'charter', 'at&t',
                                     'verizon', 'spectrum', 'telecom')):
        return 'ISP'
    if any(k in org_lower for k in ('university', 'edu', 'research', '.gov')):
        return 'Research'
    if any(k in org_lower for k in ('defense', 'military', 'dod', '.mil')):
        return 'Government'

    # Fallback to behavior-based inference
    if pattern in ('BURST_FLOOD', 'PERIODIC_BEACON'):
        return 'Suspect Infrastructure'
    return 'Unknown'


def enrich_cluster_asn(members: List[Dict]) -> Dict[str, Any]:
    """
    Resolve IPs in cluster member nodes and return ASN enrichment summary.

    Returns dict with: dominant_asn, asn_number, asn_org, asn_confidence,
                       country, infra_type, asn_diversity, all_asns
    """
    asn_records: List[Dict] = []
    countries: List[str] = []

    for nd in members:
        # Try to find an IP on the node
        ip = _extract_ip(nd)
        if not ip:
            continue

        resolved = resolve_ip(ip)
        if resolved:
            asn_records.append(resolved)
            if resolved.get('country'):
                countries.append(resolved['country'])

    if not asn_records:
        return {
            'dominant_asn': '',
            'asn_number': None,
            'asn_org': '',
            'asn_confidence': 0.0,
            'country': '',
            'infra_type': 'Unknown',
            'asn_diversity': 0,
            'all_asns': {},
        }

    # Aggregate ASN histogram
    asn_counter: Counter = Counter()
    org_map: Dict[int, str] = {}
    for rec in asn_records:
        num = rec.get('asn_number')
        if num:
            asn_counter[num] += 1
            org_map[num] = rec.get('asn_org', '')

    if not asn_counter:
        return {
            'dominant_asn': '',
            'asn_number': None,
            'asn_org': '',
            'asn_confidence': 0.0,
            'country': '',
            'infra_type': 'Unknown',
            'asn_diversity': 0,
            'all_asns': {},
        }

    dominant_num, dominant_count = asn_counter.most_common(1)[0]
    dominant_org = org_map.get(dominant_num, '')
    confidence = dominant_count / len(asn_records)

    # Country mode
    country_mode = ''
    if countries:
        cc = Counter(countries)
        country_mode = cc.most_common(1)[0][0]

    # ASN string label (e.g., "AS16509")
    asn_label = f"AS{dominant_num}"

    return {
        'dominant_asn': asn_label,
        'asn_number': dominant_num,
        'asn_org': dominant_org,
        'asn_confidence': round(confidence, 3),
        'country': country_mode,
        'infra_type': '',  # filled later with classify_infra()
        'asn_diversity': len(asn_counter),
        'all_asns': {f"AS{k}": v for k, v in asn_counter.most_common(10)},
    }


def _extract_ip(nd: Dict) -> Optional[str]:
    """Extract IP address from a node dict — checks multiple common fields."""
    for key in ('ip', 'ip_addr', 'src_ip', 'dst_ip', 'address'):
        val = nd.get(key)
        if val and isinstance(val, str) and '.' in val:
            return val.split(':')[0]  # strip port if present

    labels = nd.get('labels') or {}
    for key in ('ip', 'ip_addr', 'src_ip', 'address'):
        val = labels.get(key)
        if val and isinstance(val, str) and '.' in val:
            return val.split(':')[0]

    meta = nd.get('metadata') or {}
    for key in ('ip', 'ip_addr', 'src_ip', 'address'):
        val = meta.get(key)
        if val and isinstance(val, str) and '.' in val:
            return val.split(':')[0]

    return None

# ---------------------------------------------------------------------------
# Temporal pattern analysis — ring buffer of per-cluster event history
# ---------------------------------------------------------------------------
_cluster_event_history: Dict[str, List[Dict]] = {}   # cid → [{ts, energy, type, ...}]
_MAX_EVENT_HISTORY = 500

# Cluster cache — populated by detect_clusters() on each intel cycle.
# Consumed read-only by decompose_cluster() to avoid re-running detection.
_cluster_cache: Dict[str, Any] = {}   # cluster_id → CyberCluster

# ── Adaptive Memory Compression ───────────────────────────────────────────────
# Importance = energy × coherence × rarity × control_confidence.
# High-importance events persist; low-importance events are evicted first.
# Periodic clusters use keyframe compression: run of events with low variance
# is collapsed to (t0, period_s, amplitude) to dramatically reduce storage.
# ─────────────────────────────────────────────────────────────────────────────

def _event_importance(e: Dict) -> float:
    """Score 0–1: higher = more valuable to retain."""
    energy    = float(e.get('energy', 0.5))
    coherence = float(e.get('coherence', 0.1))
    # Control-plane types are rarer and more valuable
    type_bonus = 1.4 if e.get('type') in ('c2', 'rf', 'uav') else 1.0
    ctrl_conf  = float(e.get('control_confidence', 0.0))
    return min(1.0, (energy * 0.4 + coherence * 0.3 + ctrl_conf * 0.2) * type_bonus + 0.1)


def _compress_periodic_events(buf: List[Dict]) -> List[Dict]:
    """
    Temporal downsampling for periodic clusters.
    Detect runs of quasi-regular events; replace each run with a keyframe dict:
    {'_keyframe': True, 'ts': t0, 'period_s': p, 'amplitude': avg_energy,
     'event_count': n, 'type': most_common_type}.
    Non-periodic or high-importance events are retained verbatim.
    Requires ≥ 6 events to attempt compression.
    """
    if len(buf) < 6:
        return buf

    gaps = [buf[i+1]['ts'] - buf[i]['ts'] for i in range(len(buf) - 1)]
    if not gaps:
        return buf
    mean_gap = sum(gaps) / len(gaps)
    if mean_gap < 0.01:
        return buf
    var_gap  = sum((g - mean_gap) ** 2 for g in gaps) / len(gaps)
    cv       = (var_gap ** 0.5) / max(mean_gap, 1e-6)

    # Only compress when coefficient of variation < 0.2 (highly periodic)
    if cv >= 0.20:
        return buf

    energies   = [e.get('energy', 0.5) for e in buf]
    avg_energy = sum(energies) / len(energies)
    max_energy = max(energies)

    # High-energy spikes are preserved verbatim
    if max_energy > avg_energy * 2.5:
        spikes = [e for e in buf if e.get('energy', 0.5) > avg_energy * 2.5]
    else:
        spikes = []

    type_counts: Dict[str, int] = {}
    for e in buf:
        t = e.get('type', 'network')
        type_counts[t] = type_counts.get(t, 0) + 1
    dominant_type = max(type_counts, key=lambda k: type_counts[k])

    keyframe: Dict[str, Any] = {
        '_keyframe':   True,
        'ts':          buf[0]['ts'],
        'ts_last':     buf[-1]['ts'],
        'period_s':    round(mean_gap, 4),
        'amplitude':   round(avg_energy, 4),
        'event_count': len(buf),
        'type':        dominant_type,
    }
    return [keyframe] + spikes


def _adaptive_evict(buf: List[Dict], max_size: int) -> List[Dict]:
    """
    Evict lowest-importance events when buffer exceeds max_size.
    Keyframe records are always retained (they represent many compressed events).
    """
    if len(buf) <= max_size:
        return buf

    # Separate keyframes (never evict) from regular events
    keyframes = [e for e in buf if e.get('_keyframe')]
    regular   = [e for e in buf if not e.get('_keyframe')]

    slots_for_regular = max_size - len(keyframes)
    if slots_for_regular <= 0:
        return keyframes  # extreme case: keep only keyframes

    if len(regular) > slots_for_regular:
        # Sort by importance ascending; drop the tail (lowest importance first)
        regular.sort(key=_event_importance)
        regular = regular[len(regular) - slots_for_regular:]

    # Restore chronological order
    combined = keyframes + regular
    combined.sort(key=lambda e: e.get('ts', 0))
    return combined


def record_cluster_event(cluster_id: str, ts: float, energy: float = 1.0,
                         event_type: str = 'network',
                         asn: str = '', position: Optional[Tuple[float,float]] = None,
                         coherence: float = 0.0,
                         control_confidence: float = 0.0,
                         ) -> None:
    """Push a discrete event into a cluster's temporal ring buffer.

    Applies adaptive memory compression:
    - Importance-based eviction when buffer reaches MAX_EVENT_HISTORY
    - Periodic keyframe compression every 50 events (if signal is quasi-periodic)
    """
    buf = _cluster_event_history.setdefault(cluster_id, [])
    entry: Dict[str, Any] = {
        'ts': ts, 'energy': energy, 'type': event_type,
        'coherence': coherence, 'control_confidence': control_confidence,
    }
    if asn:
        entry['asn'] = asn
    if position:
        entry['lat'] = position[0]
        entry['lon'] = position[1]
    buf.append(entry)

    # Every 50 non-keyframe events, attempt periodic compression
    regular_count = sum(1 for e in buf if not e.get('_keyframe'))
    if regular_count > 0 and regular_count % 50 == 0:
        regular = [e for e in buf if not e.get('_keyframe')]
        compressed = _compress_periodic_events(regular[-50:])
        if any(e.get('_keyframe') for e in compressed):
            buf[-50:] = compressed  # replace last 50 with keyframe(s) + spikes
            _cluster_event_history[cluster_id] = buf

    # Importance-based eviction when over capacity
    if len(buf) > _MAX_EVENT_HISTORY:
        _cluster_event_history[cluster_id] = _adaptive_evict(buf, _MAX_EVENT_HISTORY)


def _temporal_analysis(cluster_id: str, window_sec: float = 60.0) -> Dict[str, Any]:
    """
    Derive temporal intelligence from a cluster's event ring buffer.

    Returns:
        burst_rate:    events/sec over the analysis window
        periodicity:   0→1 — higher = periodic (scheduler/beacon signature)
        directionality:0→1 — higher = directionally biased events
        entropy:       spectral entropy of event types (diversity)
        pattern:       human-readable classification string
    """
    buf = _cluster_event_history.get(cluster_id, [])
    now = time.time()
    window = [e for e in buf if now - e['ts'] <= window_sec]

    if len(window) < 3:
        return {
            'burst_rate': 0.0, 'periodicity': 0.0, 'directionality': 0.0,
            'entropy': 0.0, 'pattern': 'QUIESCENT', 'event_count': len(window),
        }

    # Burst rate (events/sec)
    dt_span = max(window[-1]['ts'] - window[0]['ts'], 0.1)
    burst_rate = len(window) / dt_span

    # Periodicity — coefficient of variation of inter-event gaps
    gaps = [window[i+1]['ts'] - window[i]['ts'] for i in range(len(window)-1)]
    mean_gap = sum(gaps) / len(gaps) if gaps else 1.0
    var_gap = sum((g - mean_gap)**2 for g in gaps) / max(len(gaps), 1)
    cv = (var_gap ** 0.5) / max(mean_gap, 1e-6)
    periodicity = max(0.0, min(1.0, 1.0 - cv))  # low CV → high periodicity

    # Entropy of event types (diversity)
    type_counts: Dict[str, int] = {}
    for e in window:
        t = e.get('type', 'network')
        type_counts[t] = type_counts.get(t, 0) + 1
    total = sum(type_counts.values())
    entropy = 0.0
    for c in type_counts.values():
        p = c / total
        if p > 0:
            entropy -= p * math.log2(p)

    # Directionality — fraction of events that are directional types
    dir_types = {'c2', 'rf', 'uav'}
    dir_count = sum(1 for e in window if e.get('type', '') in dir_types)
    directionality = dir_count / max(len(window), 1)

    # Pattern classification heuristic
    if burst_rate > 10.0 and periodicity < 0.3:
        pattern = 'BURST_FLOOD'       # rapid aperiodic = attack / DDoS
    elif periodicity > 0.7 and burst_rate > 0.5:
        pattern = 'PERIODIC_BEACON'   # scheduler / botnet heartbeat
    elif directionality > 0.6:
        pattern = 'DIRECTIONAL_EMITTER'  # UAV / RF jammer
    elif burst_rate < 0.1:
        pattern = 'LOW_ACTIVITY'
    elif entropy > 1.5:
        pattern = 'MIXED_MULTI_TYPE'
    else:
        pattern = 'STEADY_TRAFFIC'

    return {
        'burst_rate':     round(burst_rate, 3),
        'periodicity':    round(periodicity, 3),
        'directionality': round(directionality, 3),
        'entropy':        round(entropy, 3),
        'pattern':        pattern,
        'event_count':    len(window),
    }


# ---------------------------------------------------------------------------
# Phase coherence + control origin inference (Clarktech Mode)
# ---------------------------------------------------------------------------

def _geodistance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in km between two lat/lon points."""
    R = 6371.0
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    Δφ = math.radians(lat2 - lat1)
    Δλ = math.radians(lon2 - lon1)
    a = math.sin(Δφ/2)**2 + math.cos(φ1) * math.cos(φ2) * math.sin(Δλ/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _estimate_fiber_latency_s(dist_km: float) -> float:
    """Estimate one-way fiber latency from geodesic distance (~200,000 km/s)."""
    return dist_km / 200_000.0


def compute_phase_coherence(cluster_id: str, window_sec: float = 60.0,
                             period_hint: float = 0.0) -> Dict[str, Any]:
    """
    Compute phase coherence of events in a cluster's temporal buffer.

    Phase coherence measures how tightly synchronised events are in time.
    High coherence (>0.8) = centrally controlled / scheduler.
    Low coherence (<0.4) = random / organic noise.

    If period_hint > 0, wraps timestamps modulo that period to detect
    periodic synchronisation. Otherwise auto-detects from inter-event gaps.

    Returns:
        phase_coherence: float 0–1
        latency_spread_ms: float — range of estimated propagation delays
        propagation_pattern: str — SYNCHRONISED / SEQUENTIAL_RELAY / SCATTERED
        dominant_period_s: float — detected dominant period (0 if aperiodic)
    """
    buf = _cluster_event_history.get(cluster_id, [])
    now = time.time()
    window = [e for e in buf if now - e['ts'] <= window_sec]

    empty = {
        'phase_coherence': 0.0, 'phase_coherence_delta': 0.0,
        'latency_spread_ms': 0.0,
        'propagation_pattern': 'INSUFFICIENT_DATA', 'dominant_period_s': 0.0,
    }
    if len(window) < 4:
        return empty

    # Detect dominant period from inter-event gaps
    gaps = sorted(window[i+1]['ts'] - window[i]['ts']
                  for i in range(len(window) - 1))
    if period_hint <= 0 and len(gaps) >= 3:
        # Median gap as period estimate (robust to outliers)
        period_hint = gaps[len(gaps) // 2]

    # Compute phase coherence
    if period_hint > 0.01:
        # Wrap timestamps to [0, 1) phase
        phases = [(e['ts'] % period_hint) / period_hint for e in window]
        # Phase coherence = 1 - circular variance
        # circular mean: R = |sum(e^(i*2π*phase))| / N
        cos_sum = sum(math.cos(2 * math.pi * p) for p in phases)
        sin_sum = sum(math.sin(2 * math.pi * p) for p in phases)
        R = math.sqrt(cos_sum**2 + sin_sum**2) / len(phases)
        coherence = R  # 0 = uniformly distributed, 1 = perfectly synchronised
    else:
        coherence = 0.0

    # Latency spread — estimate from inter-event time vs geodesic distance
    latencies_ms: List[float] = []
    for i in range(len(window) - 1):
        e1, e2 = window[i], window[i + 1]
        if 'lat' in e1 and 'lat' in e2:
            d_km = _geodistance_km(e1['lat'], e1['lon'], e2['lat'], e2['lon'])
            if d_km > 10:  # only meaningful for non-co-located events
                expected_s = _estimate_fiber_latency_s(d_km)
                actual_s = abs(e2['ts'] - e1['ts'])
                latencies_ms.append(abs(actual_s - expected_s) * 1000)

    latency_spread = 0.0
    if latencies_ms:
        latency_spread = max(latencies_ms) - min(latencies_ms)

    # Propagation pattern classification
    phase_delta = 0.0  # coherence trajectory over window (negative = decaying, positive = converging)
    if coherence > 0.8:
        prop_pattern = 'SYNCHRONISED'
    elif coherence > 0.5:
        # Check for sequential relay (monotonically increasing delays)
        ts_sorted = sorted(e['ts'] for e in window)
        diffs = [ts_sorted[i+1] - ts_sorted[i] for i in range(len(ts_sorted) - 1)]
        if len(diffs) >= 3:
            # If delay increases monotonically → sequential relay
            increasing = sum(1 for i in range(len(diffs)-1) if diffs[i+1] >= diffs[i] * 0.8)
            if increasing > len(diffs) * 0.6:
                prop_pattern = 'SEQUENTIAL_RELAY'
            else:
                prop_pattern = 'COORDINATED'
        else:
            prop_pattern = 'COORDINATED'
    else:
        # PHASE_DRIFT_LOCK: locally scattered BUT global coherence is converging.
        # Split window into first/second half and compare phase coherence.
        # Rising coherence trend despite overall scatter → overlay stealth coordination.
        prop_pattern = 'SCATTERED'
        if len(window) >= 8 and period_hint > 0.01:
            mid = len(window) // 2
            def _half_coh(half: list) -> float:
                cs = sum(math.cos(2 * math.pi * (e['ts'] % period_hint) / period_hint) for e in half)
                ss = sum(math.sin(2 * math.pi * (e['ts'] % period_hint) / period_hint) for e in half)
                return math.sqrt(cs**2 + ss**2) / len(half)
            c_first = _half_coh(window[:mid])
            c_last  = _half_coh(window[mid:])
            phase_delta = c_last - c_first
            if c_last > c_first + 0.20 and c_last > 0.35:
                prop_pattern = 'PHASE_DRIFT_LOCK'

    return {
        'phase_coherence':       round(coherence, 4),
        'phase_coherence_delta': round(phase_delta, 4),
        'latency_spread_ms':     round(latency_spread, 1),
        'propagation_pattern':   prop_pattern,
        'dominant_period_s':     round(period_hint, 4) if period_hint > 0.01 else 0.0,
    }


def infer_control_origin(cluster: 'CyberCluster',
                          temporal: Optional[Dict] = None,
                          phase: Optional[Dict] = None,
                          ) -> Dict[str, Any]:
    """
    Infer where coordination likely originates in a cluster.

    Combines ASN frequency (ownership density) with phase coherence
    (timing discipline) to estimate control origin.

    Returns:
        control_asn: str — ASN label most likely to host the controller
        control_org: str — organization name
        control_confidence: float 0–1
        control_basis: str — reasoning string
    """
    if temporal is None:
        temporal = _temporal_analysis(cluster.id)
    if phase is None:
        phase = compute_phase_coherence(cluster.id)

    coherence = phase.get('phase_coherence', 0.0)

    # If we have per-event ASN data, use it for weighted inference
    buf = _cluster_event_history.get(cluster.id, [])
    now = time.time()
    recent = [e for e in buf if now - e['ts'] <= 120.0 and e.get('asn')]

    if not recent:
        # Fall back to cluster-level ASN
        conf = cluster.asn_confidence * coherence if coherence > 0.3 else 0.0
        return {
            'control_asn':    cluster.asn or '',
            'control_org':    cluster.asn_org or '',
            'control_confidence': round(conf, 3),
            'control_basis':  'cluster-level ASN (no per-event data)',
        }

    # Weight ASN by recency + energy
    asn_scores: Dict[str, float] = {}
    asn_orgs: Dict[str, str] = {}
    for e in recent:
        a = e['asn']
        age = max(0.1, now - e['ts'])
        recency_w = 1.0 / (1.0 + age * 0.05)   # decays over ~20s
        energy_w = e.get('energy', 1.0)
        asn_scores[a] = asn_scores.get(a, 0) + recency_w * energy_w

    # Apply phase coherence as global multiplier — high coherence amplifies signal
    for a in asn_scores:
        asn_scores[a] *= (0.5 + coherence * 0.5)

    if not asn_scores:
        return {
            'control_asn': '', 'control_org': '',
            'control_confidence': 0.0,
            'control_basis': 'no ASN data in recent events',
        }

    # Dominant ASN
    top_asn = max(asn_scores, key=asn_scores.get)
    total_score = sum(asn_scores.values())
    top_frac = asn_scores[top_asn] / total_score if total_score > 0 else 0

    # Resolve org name
    top_org = ''
    if top_asn.startswith('AS'):
        try:
            num = int(top_asn[2:])
            resolved = resolve_ip_to_asn_name(num)
            if resolved:
                top_org = resolved
        except ValueError:
            pass
    if not top_org:
        top_org = cluster.asn_org if top_asn == cluster.asn else ''

    # Basis reasoning
    bases = []
    if coherence > 0.7:
        bases.append(f'high phase coherence ({coherence:.2f})')
    if top_frac > 0.6:
        bases.append(f'dominant ASN ({top_frac:.0%} of scored events)')
    prop = phase.get('propagation_pattern', '')
    if prop == 'SEQUENTIAL_RELAY':
        bases.append('sequential relay detected')
    elif prop == 'SYNCHRONISED':
        bases.append('synchronised emission pattern')

    return {
        'control_asn':        top_asn,
        'control_org':        top_org,
        'control_confidence': round(min(1.0, top_frac * (0.5 + coherence)), 3),
        'control_basis':      '; '.join(bases) if bases else 'statistical ASN frequency',
    }


def resolve_ip_to_asn_name(asn_number: int) -> str:
    """Get human-readable name for an ASN number using pyasn names DB."""
    pyasn_db = _open_pyasn_db()
    if pyasn_db and pyasn_db is not False:
        try:
            name = pyasn_db.get_as_name(asn_number)
            if name:
                return name
        except Exception:
            pass
    # Fallback to well-known map
    return _ASN_INFRA_MAP.get(asn_number, '')

_INFRA_CLASSIFICATION: Dict[str, Dict[str, Any]] = {
    'BURST_FLOOD': {
        'label': 'Active Threat',
        'icon': '🔴',
        'desc': 'High-rate aperiodic burst — possible DDoS/botnet activation',
        'action': 'Trace upstream ASN, correlate with known C2 infrastructure',
    },
    'PERIODIC_BEACON': {
        'label': 'Botnet Scheduler',
        'icon': '🟠',
        'desc': 'Periodic signal — heartbeat/beacon signature detected',
        'action': 'Monitor for C2 command relay; check ASN reputation',
    },
    'DIRECTIONAL_EMITTER': {
        'label': 'Directional Source',
        'icon': '🔷',
        'desc': 'High directional bias — possible UAV or RF jammer',
        'action': 'Cross-reference with RF classifier; check for movement pattern',
    },
    'LOW_ACTIVITY': {
        'label': 'Dormant Infrastructure',
        'icon': '⚪',
        'desc': 'Low activity — may be staging or idle relay',
        'action': 'Flag for periodic re-scan',
    },
    'MIXED_MULTI_TYPE': {
        'label': 'Multi-Domain Cluster',
        'icon': '🟡',
        'desc': 'Diverse event types — mixed-use infrastructure',
        'action': 'Decompose by event type; look for hidden coordination',
    },
    'STEADY_TRAFFIC': {
        'label': 'Datacenter / Backbone',
        'icon': '🟢',
        'desc': 'Steady uniform traffic — likely datacenter or ISP egress',
        'action': 'Correlate with known IXP/cloud provider ranges',
    },
    'QUIESCENT': {
        'label': 'Quiet',
        'icon': '⚫',
        'desc': 'Insufficient activity for classification',
        'action': 'Awaiting sufficient data',
    },
}


def narrate_cluster(cluster: 'CyberCluster', temporal: Optional[Dict] = None) -> Dict[str, Any]:
    """
    Generate an actionable intelligence summary for a cluster.

    Combines static cluster properties (threat_score, behavior_type, RF/UAV counts)
    with temporal pattern analysis + phase coherence + control origin inference
    to produce a narration object suitable for the Cluster Intel UI panel.
    """
    if temporal is None:
        temporal = _temporal_analysis(cluster.id)

    # Phase coherence + control origin (Clarktech Mode)
    phase = compute_phase_coherence(cluster.id)
    control = infer_control_origin(cluster, temporal, phase)

    pattern = temporal.get('pattern', 'QUIESCENT')
    infra = _INFRA_CLASSIFICATION.get(pattern, _INFRA_CLASSIFICATION['QUIESCENT'])

    # Velocity analysis
    vel_mag = math.hypot(cluster.velocity_dx, cluster.velocity_dy)
    vel_kmh = vel_mag * 111.0 * 3600  # deg/s → km/h (rough)

    if vel_kmh > 500:
        mobility = 'ROUTED'       # non-physical speed = network hops
        mobility_note = f'{vel_kmh:.0f} km/h (non-physical → routed traffic)'
    elif vel_kmh > 5:
        mobility = 'MOBILE'       # physical movement
        mobility_note = f'{vel_kmh:.1f} km/h (physical movement detected)'
    else:
        mobility = 'STATIONARY'
        mobility_note = 'Fixed infrastructure'

    # Synthesize final classification (override behavior_type with temporal insight)
    final_type = cluster.behavior_type
    if temporal.get('periodicity', 0) > 0.7 and cluster.behavior_type == 'MIXED':
        final_type = 'BOTNET'
    elif temporal.get('directionality', 0) > 0.6 and cluster.uav_count > 0:
        final_type = 'RF_SWARM'

    # Upgrade 5: Generate prioritised action recommendations
    recommendations = _generate_recommendations(
        cluster, final_type, temporal, mobility, pattern)

    # Strobe emission signature — tells the frontend what type/energy to emit
    strobe_type = 'CLUSTER'
    if mobility == 'ROUTED':
        strobe_type = 'INTERFERENCE'
    elif final_type == 'RF_SWARM':
        strobe_type = 'RF'
    elif final_type == 'BOTNET':
        strobe_type = 'C2'

    # ASN-informed description enrichment
    asn_desc = ''
    if cluster.asn and cluster.asn_org:
        asn_desc = f' Dominant: {cluster.asn} ({cluster.asn_org})'
        if cluster.infra_type and cluster.infra_type != 'Unknown':
            asn_desc += f' [{cluster.infra_type}]'
        if cluster.asn_confidence < 0.5 and cluster.asn_diversity > 2:
            asn_desc += f' ⚠ mixed infra ({cluster.asn_diversity} ASNs, {cluster.asn_confidence:.0%} confidence)'

    # Build contextual description combining temporal + ASN intel
    desc_parts = [infra['desc']]
    if asn_desc:
        desc_parts.append(asn_desc)
    if cluster.country:
        desc_parts.append(f'Jurisdiction: {cluster.country}')
    full_description = ' · '.join(desc_parts)

    return {
        'id':            cluster.id,
        'centroid':      [cluster.centroid_lat, cluster.centroid_lon],
        'node_count':    cluster.node_count,
        'threat_score':  round(cluster.threat_score, 3),
        'threat_label':  cluster.threat_label(),
        'behavior_type': final_type,
        'rf_emitters':   cluster.rf_emitters,
        'uav_count':     cluster.uav_count,
        'c2_count':      cluster.c2_count,
        'asn':           cluster.asn,
        'asn_org':       cluster.asn_org,
        'asn_confidence': cluster.asn_confidence,
        'asn_diversity': cluster.asn_diversity,
        'country':       cluster.country,
        'infra_type':    cluster.infra_type,
        'mobility':      mobility,
        'mobility_note': mobility_note,
        # Temporal
        'temporal':      temporal,
        # Intel narration
        'classification': infra['label'],
        'icon':          infra['icon'],
        'description':   full_description,
        'action':        infra['action'],
        'recommendations': recommendations,
        # Phase coherence + control origin (Clarktech Mode)
        'phase':         phase,
        'control':       control,
        # Strobe feedback signature — cluster emits back into the field
        # Phase coherence amplifies strobe energy for coordinated clusters
        'strobe_emission': {
            'type':   strobe_type,
            'energy': round(min(2.0, (0.6 + cluster.threat_score * 1.2)
                                * (1.0 + phase.get('phase_coherence', 0) * 0.4)), 3),
        },
        'updated_at':    cluster.updated_at,
    }


def _generate_recommendations(
    cluster: 'CyberCluster',
    final_type: str,
    temporal: Dict,
    mobility: str,
    pattern: str,
) -> List[Dict[str, str]]:
    """Generate prioritised action recommendations based on cluster intel."""
    recs: List[Dict[str, str]] = []

    # ASN tracing for botnet/beacon patterns — enriched with org context
    if final_type in ('BOTNET', 'BEACON') or pattern == 'PERIODIC_BEACON':
        asn_detail = cluster.asn or 'unknown ASN'
        if cluster.asn_org:
            asn_detail = f'{cluster.asn} ({cluster.asn_org})'
        recs.append({
            'action': 'TRACE_UPSTREAM_ASN',
            'priority': 'HIGH' if cluster.threat_score > 0.7 else 'MEDIUM',
            'detail': f'Trace {asn_detail} for C2 infrastructure',
        })

    # RF monitoring for directional emitters
    if cluster.rf_emitters > 0 or pattern == 'DIRECTIONAL_EMITTER':
        recs.append({
            'action': 'MONITOR_RF_BAND',
            'priority': 'HIGH' if cluster.rf_emitters >= 3 else 'MEDIUM',
            'detail': f'{cluster.rf_emitters} RF emitters detected — scan 2.4/5.8 GHz bands',
        })

    # UAV correlation
    if cluster.uav_count > 0 or final_type == 'RF_SWARM':
        recs.append({
            'action': 'CORRELATE_UAV_TELEMETRY',
            'priority': 'HIGH',
            'detail': f'{cluster.uav_count} UAV signatures — cross-reference FAA/RF classifier',
        })

    # Non-physical motion analysis
    if mobility == 'ROUTED':
        recs.append({
            'action': 'ANALYZE_ROUTING_PATH',
            'priority': 'HIGH',
            'detail': 'Non-physical velocity detected — map VPN/relay hop chain',
        })

    # Burst flood response
    if pattern == 'BURST_FLOOD':
        recs.append({
            'action': 'ACTIVATE_FLOOD_MONITOR',
            'priority': 'CRITICAL',
            'detail': f'Burst rate {temporal.get("burst_rate", 0):.1f}/s — possible DDoS activation',
        })

    # C2 infrastructure flagging
    if cluster.c2_count > 0:
        recs.append({
            'action': 'FLAG_C2_INFRASTRUCTURE',
            'priority': 'CRITICAL',
            'detail': f'{cluster.c2_count} C2 nodes detected — monitor for command relay',
        })

    # Periodic re-scan for dormant
    if pattern in ('LOW_ACTIVITY', 'QUIESCENT') and cluster.node_count >= 5:
        recs.append({
            'action': 'SCHEDULE_RESCAN',
            'priority': 'LOW',
            'detail': 'Large dormant cluster — may be staging infrastructure',
        })

    # ASN-informed recommendations (infrastructure fusion)
    if cluster.infra_type == 'VPS Provider' and final_type in ('BOTNET', 'BEACON', 'SCAN'):
        recs.append({
            'action': 'FLAG_EPHEMERAL_VPS',
            'priority': 'HIGH',
            'detail': (f'{cluster.asn_org or cluster.asn} — low-cost VPS common for '
                       f'ephemeral C2. Monitor for churn.'),
        })

    if cluster.asn_diversity > 3 and cluster.asn_confidence < 0.4:
        recs.append({
            'action': 'ANALYZE_MULTI_ASN',
            'priority': 'MEDIUM',
            'detail': (f'{cluster.asn_diversity} distinct ASNs in cluster — '
                       f'mixed infrastructure indicates distributed operation'),
        })

    if cluster.infra_type == 'Hyperscaler' and pattern == 'BURST_FLOOD':
        recs.append({
            'action': 'VERIFY_CLOUD_ABUSE',
            'priority': 'HIGH',
            'detail': (f'Burst flood from {cluster.asn_org or "hyperscaler"} — '
                       f'behavior inconsistent with typical cloud workload'),
        })

    if cluster.infra_type == 'Edge CDN' and cluster.threat_score > 0.5:
        recs.append({
            'action': 'CHECK_CDN_PROXY_ABUSE',
            'priority': 'MEDIUM',
            'detail': (f'{cluster.asn_org or "CDN provider"} edge network — '
                       f'possible abuse of proxy infrastructure'),
        })

    return recs


# ---------------------------------------------------------------------------
# ASN Path Tracer — inter-cluster transit inference
# ---------------------------------------------------------------------------

# Major ASN adjacency graph (CAIDA AS-relationship style)
# Format: ASN → set of known neighbor/transit ASNs
# Sources: CAIDA, PeeringDB topology, Hurricane Electric BGP Toolkit
_ASN_ADJACENCY: Dict[int, List[int]] = {
    # Hyperscalers (multi-homed to multiple transit providers)
    16509: [3356, 174, 1299, 6939, 6461],          # AWS
    14618: [3356, 174, 1299, 6939],                 # Amazon
    15169: [3356, 1299, 6939, 174, 6461],           # Google
    396982: [15169, 3356, 1299],                    # Google Cloud (via Google)
    8075:  [3356, 174, 1299, 6939, 6461, 701],      # Microsoft / Azure
    36351: [8075, 3356, 174],                        # Microsoft (Softlayer)
    45090: [4134, 4837, 174, 3356],                  # Alibaba Cloud
    132203: [4134, 4837, 3356],                      # Tencent Cloud
    # Edge / CDN (peered everywhere)
    13335: [3356, 174, 6939, 1299, 6461],           # Cloudflare
    20940: [3356, 174, 1299, 6939, 6461, 7922],     # Akamai
    54113: [3356, 174, 1299, 6939],                  # Fastly
    16625: [20940, 3356, 174],                       # Akamai CDN
    # VPS Providers
    14061: [3356, 174, 6939, 1299],                  # DigitalOcean
    63949: [3356, 174, 20940],                       # Linode / Akamai
    24940: [3356, 174, 1299, 6939],                  # Hetzner
    16276: [3356, 174, 1299, 6939],                  # OVHcloud
    51167: [174, 3356],                              # Contabo
    20473: [3356, 174, 6939],                        # Vultr / Choopa
    # Tier-1 Backbone / Transit (full mesh)
    3356:  [174, 6939, 1299, 6461, 7018, 701],      # Lumen / Level 3
    174:   [3356, 6939, 1299, 6461, 7018],           # Cogent
    6939:  [3356, 174, 1299, 6461],                  # Hurricane Electric
    1299:  [3356, 174, 6939, 6461],                  # Arelion (Telia)
    6461:  [3356, 174, 6939, 1299],                  # Zayo
    # ISP / Consumer
    7922:  [3356, 174, 6939],                        # Comcast
    22773: [3356, 174],                              # Cox
    20001: [3356, 174, 6939],                        # Charter / Spectrum
    7018:  [3356, 174, 1299, 701],                   # AT&T
    701:   [3356, 174, 1299, 7018],                  # Verizon
    # Chinese backbone (for Alibaba/Tencent paths)
    4134:  [3356, 174, 1299],                        # China Telecom
    4837:  [3356, 174, 1299],                        # China Unicom
    # Research / Government
    11164: [3356, 6939],                             # Internet2
    5765:  [3356, 701, 7018],                        # DoD NIC
}


def _get_asn_neighbors(asn: int) -> List[int]:
    """Get bidirectional neighbors for an ASN from the adjacency graph."""
    neighbors = list(_ASN_ADJACENCY.get(asn, []))
    # Add reverse links (if X lists Y, then Y can reach X)
    for k, v in _ASN_ADJACENCY.items():
        if asn in v and k not in neighbors:
            neighbors.append(k)
    return neighbors


def infer_asn_path(src_asn: int, dst_asn: int, max_depth: int = 6) -> Optional[List[int]]:
    """
    BFS shortest path through ASN adjacency graph (bidirectional).

    Returns list of ASN hops [src, transit1, transit2, ..., dst] or None
    if no path exists within max_depth hops.
    """
    if src_asn == dst_asn:
        return [src_asn]

    visited = {src_asn}
    queue = [(src_asn, [src_asn])]
    while queue:
        current, path = queue.pop(0)
        if len(path) > max_depth:
            break
        for neighbor in _get_asn_neighbors(current):
            if neighbor == dst_asn:
                return path + [neighbor]
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, path + [neighbor]))
    return None


def compute_inter_cluster_paths(clusters_intel: List[Dict]) -> List[Dict]:
    """
    Compute ASN transit paths between all cluster pairs that have
    control origin data. Returns path objects with scoring.
    """
    paths = []
    for i, a in enumerate(clusters_intel):
        ctrl_a = a.get('control', {})
        asn_a_str = ctrl_a.get('control_asn', '') or a.get('asn', '')
        if not asn_a_str:
            continue
        try:
            asn_a = int(asn_a_str.replace('AS', ''))
        except (ValueError, TypeError):
            continue

        for j, b in enumerate(clusters_intel):
            if j <= i:
                continue
            ctrl_b = b.get('control', {})
            asn_b_str = ctrl_b.get('control_asn', '') or b.get('asn', '')
            if not asn_b_str:
                continue
            try:
                asn_b = int(asn_b_str.replace('AS', ''))
            except (ValueError, TypeError):
                continue

            if asn_a == asn_b:
                continue

            hop_path = infer_asn_path(asn_a, asn_b)
            if not hop_path:
                continue

            # Score path confidence
            conf_a = ctrl_a.get('control_confidence', a.get('asn_confidence', 0.5))
            conf_b = ctrl_b.get('control_confidence', b.get('asn_confidence', 0.5))
            phase_a = a.get('phase', {}).get('phase_coherence', 0)
            phase_b = b.get('phase', {}).get('phase_coherence', 0)
            path_score = (conf_a * conf_b) * (0.5 + max(phase_a, phase_b) * 0.5)

            # Cable alignment check
            centroid_a = a.get('centroid', [0, 0])
            centroid_b = b.get('centroid', [0, 0])
            cable_align = check_cable_alignment(
                centroid_a[0], centroid_a[1], centroid_b[0], centroid_b[1])

            # Synthetic routing detection
            is_synthetic = (max(phase_a, phase_b) > 0.7 and
                            not cable_align.get('aligned', False) and
                            len(hop_path) > 3)

            paths.append({
                'src_cluster':  a['id'],
                'dst_cluster':  b['id'],
                'src_asn':      f'AS{asn_a}',
                'dst_asn':      f'AS{asn_b}',
                'hop_path':     [f'AS{h}' for h in hop_path],
                'hop_count':    len(hop_path),
                'transit_asns': [f'AS{h}' for h in hop_path[1:-1]],
                'path_score':   round(path_score, 3),
                'cable_alignment': cable_align,
                'is_synthetic': is_synthetic,
                'centroids':    [centroid_a, centroid_b],
            })

    # Sort by path_score descending
    paths.sort(key=lambda p: p['path_score'], reverse=True)
    return paths


# ---------------------------------------------------------------------------
# Submarine Cable Overlay — physical infrastructure anchoring
# ---------------------------------------------------------------------------

# Major submarine cables with landing points (lat, lon)
# Data: public domain from TeleGeography / ITU submarine cable records
SUBMARINE_CABLES: List[Dict] = [
    {
        'name': 'MAREA',
        'landing_points': [
            {'lat': 39.18, 'lon': -74.17, 'city': 'Virginia Beach, US'},
            {'lat': 43.35, 'lon': -2.98, 'city': 'Bilbao, Spain'},
        ],
        'capacity_tbps': 200, 'owners': ['Microsoft', 'Meta'],
    },
    {
        'name': 'Dunant',
        'landing_points': [
            {'lat': 39.18, 'lon': -74.17, 'city': 'Virginia Beach, US'},
            {'lat': 44.65, 'lon': -1.18, 'city': 'Saint-Hilaire-de-Riez, France'},
        ],
        'capacity_tbps': 250, 'owners': ['Google'],
    },
    {
        'name': 'Grace Hopper',
        'landing_points': [
            {'lat': 40.75, 'lon': -73.80, 'city': 'New York, US'},
            {'lat': 52.98, 'lon': 1.72, 'city': 'Bude, UK'},
            {'lat': 43.35, 'lon': -2.98, 'city': 'Bilbao, Spain'},
        ],
        'capacity_tbps': 350, 'owners': ['Google'],
    },
    {
        'name': 'AEC-1 (Asia-Europe)',
        'landing_points': [
            {'lat': 1.35, 'lon': 103.82, 'city': 'Singapore'},
            {'lat': 22.28, 'lon': 114.16, 'city': 'Hong Kong'},
            {'lat': 31.23, 'lon': 121.47, 'city': 'Shanghai, China'},
            {'lat': 35.44, 'lon': 139.64, 'city': 'Tokyo, Japan'},
        ],
        'capacity_tbps': 40, 'owners': ['NTT', 'China Telecom'],
    },
    {
        'name': 'SEA-ME-WE 6',
        'landing_points': [
            {'lat': 1.35, 'lon': 103.82, 'city': 'Singapore'},
            {'lat': 6.93, 'lon': 79.85, 'city': 'Colombo, Sri Lanka'},
            {'lat': 11.59, 'lon': 43.15, 'city': 'Djibouti'},
            {'lat': 30.06, 'lon': 31.25, 'city': 'Egypt (Suez)'},
            {'lat': 43.30, 'lon': 5.37, 'city': 'Marseille, France'},
        ],
        'capacity_tbps': 100, 'owners': ['Singtel', 'Orange', 'Telstra'],
    },
    {
        'name': 'PEACE',
        'landing_points': [
            {'lat': 39.92, 'lon': 116.46, 'city': 'Beijing region, China'},
            {'lat': 24.87, 'lon': 67.01, 'city': 'Karachi, Pakistan'},
            {'lat': 11.59, 'lon': 43.15, 'city': 'Djibouti'},
            {'lat': 43.30, 'lon': 5.37, 'city': 'Marseille, France'},
        ],
        'capacity_tbps': 96, 'owners': ['PEACE Cable International'],
    },
    {
        'name': 'JUPITER',
        'landing_points': [
            {'lat': 34.05, 'lon': -118.24, 'city': 'Los Angeles, US'},
            {'lat': 35.44, 'lon': 139.64, 'city': 'Tokyo, Japan'},
            {'lat': 14.60, 'lon': 121.00, 'city': 'Manila, Philippines'},
        ],
        'capacity_tbps': 60, 'owners': ['Amazon', 'Meta', 'NTT'],
    },
    {
        'name': 'Equiano',
        'landing_points': [
            {'lat': 38.72, 'lon': -9.14, 'city': 'Lisbon, Portugal'},
            {'lat': 5.56, 'lon': -0.19, 'city': 'Accra, Ghana'},
            {'lat': 6.45, 'lon': 3.42, 'city': 'Lagos, Nigeria'},
            {'lat': -33.92, 'lon': 18.42, 'city': 'Cape Town, South Africa'},
        ],
        'capacity_tbps': 144, 'owners': ['Google'],
    },
    {
        'name': 'SAEx-1',
        'landing_points': [
            {'lat': -33.92, 'lon': 18.42, 'city': 'Cape Town, South Africa'},
            {'lat': -23.55, 'lon': -46.63, 'city': 'São Paulo, Brazil'},
        ],
        'capacity_tbps': 12, 'owners': ['SAEx'],
    },
    {
        'name': 'EllaLink',
        'landing_points': [
            {'lat': 38.72, 'lon': -9.14, 'city': 'Lisbon, Portugal'},
            {'lat': -3.73, 'lon': -38.52, 'city': 'Fortaleza, Brazil'},
        ],
        'capacity_tbps': 72, 'owners': ['EllaLink'],
    },
    {
        'name': 'FLAG Atlantic-1',
        'landing_points': [
            {'lat': 40.75, 'lon': -73.80, 'city': 'New York, US'},
            {'lat': 51.51, 'lon': -0.13, 'city': 'London, UK'},
        ],
        'capacity_tbps': 4.8, 'owners': ['Global Cloud Xchange'],
    },
    {
        'name': 'Pacific Crossing-1',
        'landing_points': [
            {'lat': 47.60, 'lon': -122.33, 'city': 'Seattle, US'},
            {'lat': 35.44, 'lon': 139.64, 'city': 'Tokyo, Japan'},
        ],
        'capacity_tbps': 5.1, 'owners': ['NTT'],
    },
    {
        'name': 'AAG (Asia-America Gateway)',
        'landing_points': [
            {'lat': 34.05, 'lon': -118.24, 'city': 'Los Angeles, US'},
            {'lat': 22.28, 'lon': 114.16, 'city': 'Hong Kong'},
            {'lat': 1.35, 'lon': 103.82, 'city': 'Singapore'},
            {'lat': 14.60, 'lon': 121.00, 'city': 'Manila, Philippines'},
        ],
        'capacity_tbps': 2.88, 'owners': ['AT&T', 'VNPT', 'Telstra'],
    },
    {
        'name': 'Curie',
        'landing_points': [
            {'lat': 34.05, 'lon': -118.24, 'city': 'Los Angeles, US'},
            {'lat': -33.45, 'lon': -70.67, 'city': 'Valparaíso, Chile'},
        ],
        'capacity_tbps': 72, 'owners': ['Google'],
    },
    {
        'name': 'Firmina',
        'landing_points': [
            {'lat': 39.18, 'lon': -74.17, 'city': 'Virginia Beach, US'},
            {'lat': -23.55, 'lon': -46.63, 'city': 'São Paulo, Brazil'},
            {'lat': -34.60, 'lon': -58.38, 'city': 'Buenos Aires, Argentina'},
        ],
        'capacity_tbps': 24, 'owners': ['Google'],
    },
]

# Major Internet Exchange Points (IX) with approximate coordinates
# Enhanced with connected ASNs and nearby cable landing zones
IX_POINTS: List[Dict] = [
    {'name': 'DE-CIX Frankfurt', 'lat': 50.11, 'lon': 8.68, 'peak_tbps': 14.0,
     'connected_asns': [3356, 174, 1299, 6939, 6461, 8075, 15169, 16509, 13335, 20940],
     'cables': ['AEC-1', 'SEA-ME-WE 6']},
    {'name': 'AMS-IX Amsterdam', 'lat': 52.37, 'lon': 4.90, 'peak_tbps': 12.0,
     'connected_asns': [3356, 174, 1299, 6939, 6461, 8075, 15169, 16509, 13335, 20940, 54113],
     'cables': ['MAREA', 'Dunant']},
    {'name': 'LINX London', 'lat': 51.51, 'lon': -0.13, 'peak_tbps': 6.0,
     'connected_asns': [3356, 174, 1299, 6939, 8075, 15169, 16509, 13335, 20940],
     'cables': ['Grace Hopper', 'FLAG Atlantic-1']},
    {'name': 'Equinix Ashburn', 'lat': 39.04, 'lon': -77.49, 'peak_tbps': 8.0,
     'connected_asns': [3356, 174, 6939, 6461, 7018, 701, 7922, 16509, 8075, 15169, 13335],
     'cables': ['MAREA', 'Dunant', 'Firmina']},
    {'name': 'Equinix Chicago', 'lat': 41.88, 'lon': -87.63, 'peak_tbps': 3.0,
     'connected_asns': [3356, 174, 6939, 7922, 20001, 16509, 8075],
     'cables': []},
    {'name': 'Equinix SV (Palo Alto)', 'lat': 37.44, 'lon': -122.14, 'peak_tbps': 4.0,
     'connected_asns': [3356, 174, 6939, 6461, 16509, 15169, 8075, 13335, 20473],
     'cables': ['JUPITER', 'Curie']},
    {'name': 'Equinix Singapore', 'lat': 1.35, 'lon': 103.82, 'peak_tbps': 3.5,
     'connected_asns': [3356, 174, 1299, 45090, 132203, 4134, 4837, 16509, 15169],
     'cables': ['SEA-ME-WE 6', 'AEC-1', 'AAG', 'JUPITER']},
    {'name': 'JPNAP Tokyo', 'lat': 35.69, 'lon': 139.70, 'peak_tbps': 3.0,
     'connected_asns': [3356, 174, 1299, 16509, 15169, 8075, 45090, 4134],
     'cables': ['Pacific Crossing-1', 'JUPITER', 'AEC-1', 'AAG']},
    {'name': 'IX.br São Paulo', 'lat': -23.55, 'lon': -46.63, 'peak_tbps': 26.0,
     'connected_asns': [3356, 174, 6939, 16509, 15169, 8075, 7018],
     'cables': ['EllaLink', 'SAEx-1', 'Firmina']},
    {'name': 'HKIX Hong Kong', 'lat': 22.32, 'lon': 114.17, 'peak_tbps': 2.0,
     'connected_asns': [3356, 174, 4134, 4837, 45090, 132203, 16509, 15169],
     'cables': ['AEC-1', 'AAG', 'PEACE']},
    {'name': 'NAPAfrica Johannesburg', 'lat': -26.20, 'lon': 28.04, 'peak_tbps': 1.2,
     'connected_asns': [3356, 174, 6939],
     'cables': ['Equiano', 'SAEx-1']},
    {'name': 'MSK-IX Moscow', 'lat': 55.76, 'lon': 37.62, 'peak_tbps': 4.5,
     'connected_asns': [3356, 174, 1299, 6939],
     'cables': []},
]


def _point_to_segment_distance_km(lat: float, lon: float,
                                   lat1: float, lon1: float,
                                   lat2: float, lon2: float) -> float:
    """Approximate min distance from a point to a great-circle segment (km)."""
    # Project point onto the line between (lat1,lon1)-(lat2,lon2)
    # Use simple parametric projection on the Mercator plane, then
    # refine with haversine. Good enough for ~100 km resolution.
    dx = lon2 - lon1
    dy = lat2 - lat1
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return _geodistance_km(lat, lon, lat1, lon1)
    t = max(0.0, min(1.0,
        ((lon - lon1) * dx + (lat - lat1) * dy) / (dx * dx + dy * dy)))
    proj_lat = lat1 + t * dy
    proj_lon = lon1 + t * dx
    return _geodistance_km(lat, lon, proj_lat, proj_lon)


def find_nearby_cables(lat: float, lon: float,
                       radius_km: float = 500.0) -> List[Dict]:
    """Find submarine cables with segments within radius_km of a point."""
    results = []
    for cable in SUBMARINE_CABLES:
        pts = cable['landing_points']
        min_dist = float('inf')
        nearest_seg = None
        for k in range(len(pts) - 1):
            d = _point_to_segment_distance_km(
                lat, lon, pts[k]['lat'], pts[k]['lon'],
                pts[k+1]['lat'], pts[k+1]['lon'])
            if d < min_dist:
                min_dist = d
                nearest_seg = (pts[k]['city'], pts[k+1]['city'])
        # Also check landing points directly
        for pt in pts:
            d = _geodistance_km(lat, lon, pt['lat'], pt['lon'])
            if d < min_dist:
                min_dist = d
                nearest_seg = (pt['city'], 'landing')
        if min_dist <= radius_km:
            results.append({
                'cable': cable['name'],
                'distance_km': round(min_dist, 1),
                'nearest_segment': nearest_seg,
                'capacity_tbps': cable['capacity_tbps'],
                'owners': cable['owners'],
            })
    results.sort(key=lambda c: c['distance_km'])
    return results


def find_nearby_ix(lat: float, lon: float,
                   radius_km: float = 300.0) -> List[Dict]:
    """Find Internet Exchange Points within radius_km of a point."""
    results = []
    for ix in IX_POINTS:
        d = _geodistance_km(lat, lon, ix['lat'], ix['lon'])
        if d <= radius_km:
            results.append({
                'name': ix['name'],
                'distance_km': round(d, 1),
                'peak_tbps': ix['peak_tbps'],
                'lat': ix['lat'], 'lon': ix['lon'],
            })
    results.sort(key=lambda x: x['distance_km'])
    return results


def check_cable_alignment(lat1: float, lon1: float,
                          lat2: float, lon2: float,
                          threshold_km: float = 800.0) -> Dict:
    """
    Check if the path between two points aligns with known submarine cables.

    Returns alignment info: which cables the path is near, chokepoints,
    and whether the physical infrastructure supports the logical path.
    """
    cables_near_src = find_nearby_cables(lat1, lon1, threshold_km)
    cables_near_dst = find_nearby_cables(lat2, lon2, threshold_km)

    # Find cables common to both endpoints
    src_names = {c['cable'] for c in cables_near_src}
    dst_names = {c['cable'] for c in cables_near_dst}
    shared = src_names & dst_names

    # IX proximity for chokepoint detection
    midlat = (lat1 + lat2) / 2
    midlon = (lon1 + lon2) / 2
    chokepoints = find_nearby_ix(midlat, midlon, 500.0)
    # Also check endpoints
    for lat, lon in [(lat1, lon1), (lat2, lon2)]:
        for ix in find_nearby_ix(lat, lon, 200.0):
            if ix['name'] not in [c['name'] for c in chokepoints]:
                chokepoints.append(ix)

    return {
        'aligned': len(shared) > 0,
        'shared_cables': list(shared),
        'src_cables': [c['cable'] for c in cables_near_src[:3]],
        'dst_cables': [c['cable'] for c in cables_near_dst[:3]],
        'chokepoints': [{'name': c['name'], 'distance_km': c['distance_km']}
                        for c in chokepoints[:3]],
        'distance_km': round(_geodistance_km(lat1, lon1, lat2, lon2), 0),
    }


def infrastructure_flow_snapshot(clusters_intel: List[Dict]) -> Dict:
    """
    Full infrastructure flow analysis: ASN paths, cable alignment,
    chokepoints, synthetic routing detection.

    Called by the /api/infrastructure/flow endpoint.
    """
    paths = compute_inter_cluster_paths(clusters_intel)

    # Aggregate cable and IX usage across all paths
    cable_usage: Dict[str, int] = {}
    ix_usage: Dict[str, int] = {}
    synthetic_count = 0

    for p in paths:
        ca = p.get('cable_alignment', {})
        for cable in ca.get('shared_cables', []):
            cable_usage[cable] = cable_usage.get(cable, 0) + 1
        for choke in ca.get('chokepoints', []):
            ix_usage[choke['name']] = ix_usage.get(choke['name'], 0) + 1
        if p.get('is_synthetic'):
            synthetic_count += 1

    return {
        'paths': paths[:20],  # top 20 by score
        'path_count': len(paths),
        'cables': SUBMARINE_CABLES,
        'ix_points': IX_POINTS,
        'cable_usage': cable_usage,
        'ix_usage': ix_usage,
        'synthetic_routing_detected': synthetic_count,
        'summary': {
            'total_paths': len(paths),
            'physical_paths': len(paths) - synthetic_count,
            'synthetic_paths': synthetic_count,
            'active_cables': len(cable_usage),
            'active_ix': len(ix_usage),
        },
    }


# ---------------------------------------------------------------------------
# IX Heatmap + Peering Conflict Detector
# ---------------------------------------------------------------------------

# Temporal echo buffer — tracks IX pressure over time for wave detection
_ix_pressure_history: Dict[str, List[Dict]] = {}  # ix_name → [{ts, heat, ...}]
_IX_HISTORY_MAX = 200   # events per IX
_IX_HISTORY_TTL = 300.0  # 5 minutes


# Heat score weights
_IX_HEAT_WEIGHTS = {
    'traffic':          0.25,  # normalised path count through IX
    'latency_variance': 0.20,  # timing instability
    'phase_inversion':  0.25,  # high coherence + diverging paths
    'asymmetry':        0.15,  # inbound/outbound mismatch
    'synthetic_density': 0.15,  # % of paths flagged synthetic
}


def _compute_ix_asn_traffic(ix: Dict, paths: List[Dict]) -> Dict:
    """Compute per-ASN pair traffic metrics flowing through an IX."""
    ix_asns = set(ix.get('connected_asns', []))
    asn_pair_traffic: Dict[Tuple[int, int], float] = {}
    inbound = 0.0
    outbound = 0.0
    synthetic_through = 0

    for p in paths:
        # Check if path transits this IX (any hop ASN in IX's connected set)
        hop_asns_int = []
        for h in p.get('hop_path', []):
            try:
                hop_asns_int.append(int(str(h).replace('AS', '')))
            except (ValueError, TypeError):
                continue

        transits = ix_asns & set(hop_asns_int)
        if not transits:
            continue

        src_asn = int(str(p.get('src_asn', '0')).replace('AS', ''))
        dst_asn = int(str(p.get('dst_asn', '0')).replace('AS', ''))
        score = p.get('path_score', 0)

        # Track directional flow
        if src_asn in ix_asns:
            outbound += score
        if dst_asn in ix_asns:
            inbound += score

        # Track per-pair traffic
        pair = (min(src_asn, dst_asn), max(src_asn, dst_asn))
        asn_pair_traffic[pair] = asn_pair_traffic.get(pair, 0) + score

        if p.get('is_synthetic'):
            synthetic_through += 1

    return {
        'asn_pair_traffic': asn_pair_traffic,
        'inbound': inbound,
        'outbound': outbound,
        'total_traffic': inbound + outbound,
        'synthetic_through': synthetic_through,
    }


def compute_ix_heat(ix: Dict, paths: List[Dict],
                    clusters_intel: List[Dict]) -> Dict:
    """
    Compute heat score for a single IX node.

    Heat = weighted combination of:
      - normalised traffic volume
      - latency variance (timing instability)
      - phase coherence inversion (coordinated but diverging)
      - asymmetry (inbound/outbound imbalance)
      - synthetic path density
    """
    traffic_data = _compute_ix_asn_traffic(ix, paths)
    total_traffic = traffic_data['total_traffic']
    synthetic_count = traffic_data['synthetic_through']
    path_count = max(1, len(paths))

    # 1. Normalised traffic (relative to IX capacity)
    capacity = ix.get('peak_tbps', 1.0)
    traffic_norm = min(1.0, total_traffic / max(0.1, capacity * 0.1))

    # 2. Latency variance — from phase data of clusters near this IX
    phase_values = []
    for c in clusters_intel:
        ph = c.get('phase', {})
        centroid = c.get('centroid', [0, 0])
        d = _geodistance_km(ix['lat'], ix['lon'], centroid[0], centroid[1])
        if d < 1000:  # clusters within 1000km of IX
            latency_ms = ph.get('latency_spread_ms', 0)
            phase_values.append(latency_ms)

    lat_variance = 0.0
    if len(phase_values) >= 2:
        mean_lat = sum(phase_values) / len(phase_values)
        lat_variance = min(1.0, (sum((v - mean_lat)**2 for v in phase_values)
                                  / len(phase_values)) / 10000.0)

    # 3. Phase coherence inversion — high coherence but paths diverge
    phase_inversion = 0.0
    nearby_coherences = []
    for c in clusters_intel:
        centroid = c.get('centroid', [0, 0])
        d = _geodistance_km(ix['lat'], ix['lon'], centroid[0], centroid[1])
        if d < 1000:
            coh = c.get('phase', {}).get('phase_coherence', 0)
            nearby_coherences.append(coh)

    if nearby_coherences:
        avg_coherence = sum(nearby_coherences) / len(nearby_coherences)
        # High coherence but multiple distinct paths = inversion
        distinct_src = len(set(p['src_asn'] for p in paths
                              if any(int(str(h).replace('AS','')) in set(ix.get('connected_asns', []))
                                     for h in p.get('hop_path', []))))
        if distinct_src > 1 and avg_coherence > 0.5:
            phase_inversion = avg_coherence * min(1.0, distinct_src / 5.0)

    # 4. Asymmetry — inbound/outbound traffic imbalance
    asym = 0.0
    if traffic_data['inbound'] + traffic_data['outbound'] > 0:
        total_io = traffic_data['inbound'] + traffic_data['outbound']
        asym = abs(traffic_data['inbound'] - traffic_data['outbound']) / total_io

    # 5. Synthetic path density
    synthetic_density = 0.0
    transiting_paths = sum(1 for p in paths
                           if any(int(str(h).replace('AS','')) in
                                  set(ix.get('connected_asns', []))
                                  for h in p.get('hop_path', [])))
    if transiting_paths > 0:
        synthetic_density = synthetic_count / transiting_paths

    # Weighted heat score
    w = _IX_HEAT_WEIGHTS
    heat = (w['traffic'] * traffic_norm +
            w['latency_variance'] * lat_variance +
            w['phase_inversion'] * phase_inversion +
            w['asymmetry'] * asym +
            w['synthetic_density'] * synthetic_density)
    heat = min(1.0, heat)

    # Determine heat tier
    if heat > 0.7:
        tier = 'CRITICAL'
    elif heat > 0.4:
        tier = 'ELEVATED'
    elif heat > 0.15:
        tier = 'ACTIVE'
    else:
        tier = 'QUIET'

    result = {
        'name':             ix['name'],
        'lat':              ix['lat'],
        'lon':              ix['lon'],
        'peak_tbps':        ix.get('peak_tbps', 0),
        'heat':             round(heat, 4),
        'tier':             tier,
        'traffic_norm':     round(traffic_norm, 4),
        'latency_variance': round(lat_variance, 4),
        'phase_inversion':  round(phase_inversion, 4),
        'asymmetry':        round(asym, 4),
        'synthetic_density': round(synthetic_density, 4),
        'connected_asns':   ix.get('connected_asns', []),
        'cables':           ix.get('cables', []),
        'transiting_paths': transiting_paths,
    }

    # Record temporal pressure echo
    _record_ix_pressure(ix['name'], result)

    return result


def _record_ix_pressure(ix_name: str, heat_data: Dict) -> None:
    """Record a heat measurement for temporal echo analysis."""
    if ix_name not in _ix_pressure_history:
        _ix_pressure_history[ix_name] = []
    buf = _ix_pressure_history[ix_name]
    now = time.time()
    buf.append({
        'ts': now,
        'heat': heat_data['heat'],
        'tier': heat_data['tier'],
        'phase_inversion': heat_data['phase_inversion'],
        'asymmetry': heat_data['asymmetry'],
    })
    # Trim old entries
    cutoff = now - _IX_HISTORY_TTL
    while buf and buf[0]['ts'] < cutoff:
        buf.pop(0)
    if len(buf) > _IX_HISTORY_MAX:
        buf[:] = buf[-_IX_HISTORY_MAX:]


def get_ix_pressure_trend(ix_name: str, window_sec: float = 60.0) -> Dict:
    """Get heat trend for an IX over the last window_sec."""
    buf = _ix_pressure_history.get(ix_name, [])
    now = time.time()
    window = [e for e in buf if now - e['ts'] <= window_sec]
    if len(window) < 2:
        return {'trend': 'STABLE', 'delta': 0.0, 'samples': len(window),
                'velocity': 0.0, 'acceleration': 0.0}

    heats = [e['heat'] for e in window]
    timestamps = [e['ts'] for e in window]
    first_half = heats[:len(heats)//2]
    second_half = heats[len(heats)//2:]
    avg_first = sum(first_half) / len(first_half)
    avg_second = sum(second_half) / len(second_half)
    delta = avg_second - avg_first

    if delta > 0.1:
        trend = 'ESCALATING'
    elif delta < -0.1:
        trend = 'COOLING'
    else:
        trend = 'STABLE'

    # ── Heat derivatives ────────────────────────────────────────────────────
    # Velocity: d(heat)/dt using linear regression over windowed samples
    velocity = 0.0
    acceleration = 0.0
    if len(heats) >= 3:
        # Least-squares velocity (slope of heat vs time)
        t0 = timestamps[0]
        ts_norm = [t - t0 for t in timestamps]
        n = len(ts_norm)
        sum_t = sum(ts_norm)
        sum_h = sum(heats)
        sum_th = sum(ts_norm[i] * heats[i] for i in range(n))
        sum_t2 = sum(t * t for t in ts_norm)
        denom = n * sum_t2 - sum_t * sum_t
        if abs(denom) > 1e-12:
            velocity = (n * sum_th - sum_t * sum_h) / denom  # heat/sec

        # Acceleration: difference of half-window velocities
        mid = n // 2
        if mid >= 2:
            ts_a, h_a = ts_norm[:mid], heats[:mid]
            ts_b, h_b = ts_norm[mid:], heats[mid:]
            vel_a = _lstsq_slope(ts_a, h_a)
            vel_b = _lstsq_slope(ts_b, h_b)
            dt_halves = (sum(ts_b) / len(ts_b)) - (sum(ts_a) / len(ts_a))
            if abs(dt_halves) > 1e-6:
                acceleration = (vel_b - vel_a) / dt_halves

    return {
        'trend': trend,
        'delta': round(delta, 4),
        'current': round(heats[-1], 4),
        'peak': round(max(heats), 4),
        'samples': len(window),
        'velocity': round(velocity, 6),
        'acceleration': round(acceleration, 6),
    }


def _lstsq_slope(xs: List[float], ys: List[float]) -> float:
    """Least-squares slope for a small (x,y) series."""
    n = len(xs)
    if n < 2:
        return 0.0
    sx = sum(xs)
    sy = sum(ys)
    sxy = sum(xs[i] * ys[i] for i in range(n))
    sx2 = sum(x * x for x in xs)
    d = n * sx2 - sx * sx
    return (n * sxy - sx * sy) / d if abs(d) > 1e-12 else 0.0


def get_ix_conflict_replay(window_sec: float = 3600.0, max_ix: int = 20) -> Dict:
    """
    Return per-IX heat time-series for the conflict replay scrubber.

    Each series entry: {ts, heat, tier, inv (phase_inversion bool)}
    Series are ordered chronologically.  Returns the top max_ix IXs by
    peak heat inside the window.
    """
    now = time.time()
    cutoff = now - window_sec

    active = []
    for name, buf in _ix_pressure_history.items():
        pts = [e for e in buf if e['ts'] >= cutoff]
        if not pts:
            continue
        peak = max(e['heat'] for e in pts)
        active.append((name, pts, peak))

    active.sort(key=lambda x: x[2], reverse=True)
    active = active[:max_ix]

    series: Dict[str, List[Dict]] = {}
    for name, pts, _ in active:
        series[name] = [
            {'ts': round(e['ts'], 3),
             'heat': round(e['heat'], 4),
             'tier': e.get('tier', 'NOMINAL'),
             'inv': bool(e.get('phase_inversion', False))}
            for e in pts
        ]

    return {
        'series':     series,
        't_start':    round(cutoff, 3),
        't_end':      round(now, 3),
        'window_sec': window_sec,
        'ix_count':   len(series),
    }


_T_BUCKETS = 30   # sparkline resolution for get_signal_timing_snapshot


def get_signal_timing_snapshot(window_sec: float = 120.0,
                                max_clusters: int = 15) -> Dict:
    """
    Return per-cluster phase-coherence + energy timeline for the Signal Timing panel.

    Each cluster entry contains:
      cluster_id, event_count, phase_coherence, latency_spread_ms,
      propagation_pattern, dominant_period_s,
      energy_timeline (list of _T_BUCKETS floats, avg energy per bucket),
      kc_scores (list from _KC_SCORE_HISTORY deque, newest-last),
      last_event_type, last_ts
    """
    now = time.time()
    cutoff = now - window_sec
    bucket_size = window_sec / _T_BUCKETS
    # Align bucket start to clock boundary to prevent sampling jitter
    import math as _math
    aligned_cutoff = _math.floor(cutoff / bucket_size) * bucket_size

    out = []
    for cid, buf in _cluster_event_history.items():
        window = [e for e in buf if e.get('ts', 0) >= aligned_cutoff]
        if len(window) < 3:
            continue

        # Energy sparkline — _T_BUCKETS aligned time buckets
        buckets: List[float] = []
        for b in range(_T_BUCKETS):
            t_lo = aligned_cutoff + b * bucket_size
            t_hi = t_lo + bucket_size
            pts = [e for e in window if t_lo <= e['ts'] < t_hi]
            if pts:
                buckets.append(round(sum(e.get('energy', 0) for e in pts) / len(pts), 3))
            else:
                buckets.append(0.0)

        phase = compute_phase_coherence(cid, window_sec=window_sec)
        kc_hist = list(_KC_SCORE_HISTORY.get(cid, []))
        # kc_hist entries are {'ts': ..., 'score': ...} dicts
        kc_scores_list = [round(e['score'], 4) for e in kc_hist if isinstance(e, dict)]
        kc_slope = 0.0
        if len(kc_scores_list) >= 3:
            kc_slope = round(_lstsq_slope(list(range(len(kc_scores_list))), kc_scores_list), 5)

        # Drift magnitude from recent energy deltas (proxy for RF fingerprint drift)
        energies = [e.get('energy', 0.0) for e in window]
        d_energy = [abs(energies[i] - energies[i-1]) for i in range(1, len(energies))]
        drift_mag = round(sum(d_energy) / max(1, len(d_energy)), 4)

        # Phase-drift coupling: how much entities change TOGETHER
        # High coupling = coordinated transformation; low = independent noise
        ph_delta = phase['phase_coherence_delta']
        drift_phase_coupling = round(drift_mag * max(0.0, ph_delta), 4)

        # Intent score — fuses kc_slope, phase_delta, drift, and coupling
        # into a single 0–1 scalar indicating how strongly coordination is forming
        intent_score = round(min(1.0, max(0.0,
            0.30 * min(1.0, max(0.0, kc_slope * 10)) +
            0.25 * min(1.0, max(0.0, ph_delta * 4)) +
            0.25 * min(1.0, drift_mag * 3) +
            0.20 * min(1.0, drift_phase_coupling * 5)
        )), 4)

        last = window[-1]

        out.append({
            'cluster_id':            cid,
            'event_count':           len(window),
            'phase_coherence':       phase['phase_coherence'],
            'phase_coherence_delta': ph_delta,
            'latency_spread_ms':     phase['latency_spread_ms'],
            'propagation_pattern':   phase['propagation_pattern'],
            'dominant_period_s':     phase['dominant_period_s'],
            'kc_scores':             kc_scores_list,
            'kc_slope':              kc_slope,
            'drift_mag':             drift_mag,
            'drift_phase_coupling':  drift_phase_coupling,
            'intent_score':          intent_score,
            'energy_timeline':       buckets,
            'last_event_type':       last.get('type', 'unknown'),
            'last_ts':               round(last['ts'], 3),
        })

    out.sort(key=lambda x: x['intent_score'], reverse=True)
    out = out[:max_clusters]

    return {
        'clusters':    out,
        'window_sec':  window_sec,
        't_now':       round(now, 3),
        'T_BUCKETS':   _T_BUCKETS,
    }


def get_killchain_slope(window_steps: int = 5) -> Dict:
    """
    Per-cluster kill-chain escalation slope from _KC_SCORE_HISTORY.

    Computes least-squares linear slope over the last `window_steps` composite
    KC scores and classifies the escalation trajectory:
      IMMINENT   — steep rising slope (> 0.05/step)
      ESCALATING — moderate rise (> 0.02/step)
      DECLINING  — falling slope
      IDLE       — flat
    """
    out = []
    for cid, deq in _KC_SCORE_HISTORY.items():
        entries = list(deq)[-window_steps:]
        if len(entries) < 3:
            continue
        scores = [e['score'] for e in entries if isinstance(e, dict)]
        if len(scores) < 3:
            continue
        slope = _lstsq_slope(list(range(len(scores))), scores)
        if slope > 0.05:
            stage = 'IMMINENT'
        elif slope > 0.02:
            stage = 'ESCALATING'
        elif slope < -0.02:
            stage = 'DECLINING'
        else:
            stage = 'IDLE'
        out.append({
            'cluster_id': cid,
            'slope':      round(slope, 5),
            'scores':     [round(s, 4) for s in scores],
            'current':    round(scores[-1], 4),
            'stage':      stage,
        })
    out.sort(key=lambda x: abs(x['slope']), reverse=True)
    return {'clusters': out, 't_now': round(time.time(), 3)}


def get_fingerprint_drift_snapshot(window_sec: float = 120.0,
                                   min_events: int = 4) -> Dict:
    """
    Approximate temporal RF fingerprint drift from cluster event history.

    Uses delta(energy) and delta(coherence) per cluster as proxies for RF DNA
    drift (actual RF fingerprint vectors live in the GPU strobe buffer; this
    backend proxy captures the same semantic changes via measurable scalars).

    Behavior classifications:
      STABLE      — low-delta, persistent consistent emitter
      DRIFTING    — slow monotonic adaptation (routing changes, antenna pointing)
      SNAPPING    — large discrete step changes (proxy / relay node swap)
      OSCILLATING — periodic high variance (load-balancing / active obfuscation)
    """
    now = time.time()
    cutoff = now - window_sec
    results = []

    for cid, buf in _cluster_event_history.items():
        window = [e for e in buf if e.get('ts', 0) >= cutoff]
        if len(window) < min_events:
            continue

        energies = [e.get('energy', 0.0) for e in window]
        cohs     = [e.get('coherence', 0.0) for e in window]

        d_energy = [abs(energies[i] - energies[i - 1]) for i in range(1, len(energies))]
        d_coh    = [abs(cohs[i]     - cohs[i - 1])     for i in range(1, len(cohs))]

        if not d_energy:
            continue

        mean_d_e = sum(d_energy) / len(d_energy)
        mean_d_c = sum(d_coh) / len(d_coh)
        var_d_e  = sum((x - mean_d_e) ** 2 for x in d_energy) / max(1, len(d_energy))
        max_snap = max(d_energy)

        if mean_d_e < 0.05 and mean_d_c < 0.05:
            behavior = 'STABLE'
        elif max_snap > 0.4:
            behavior = 'SNAPPING'
        elif var_d_e > mean_d_e * 0.8:
            # CLOUD_AUTOSCALE discriminator: oscillating pattern with low coherence change
            # and no strong energy jumps → legitimate cloud autoscaling, not coordination
            phase = compute_phase_coherence(cid, window_sec=window_sec)
            if phase['phase_coherence'] < 0.35 and phase['phase_coherence_delta'] < 0.10:
                behavior = 'CLOUD_AUTOSCALE'
            else:
                behavior = 'OSCILLATING'
        else:
            behavior = 'DRIFTING'

        results.append({
            'cluster_id':  cid,
            'behavior':    behavior,
            'drift_mag':   round(mean_d_e, 4),
            'max_snap':    round(max_snap, 4),
            'mean_d_coh':  round(mean_d_c, 4),
            'event_count': len(window),
            'd_energy':    [round(x, 4) for x in d_energy[-15:]],
        })

    results.sort(key=lambda x: x['drift_mag'], reverse=True)
    return {'clusters': results, 'window_sec': window_sec, 't_now': round(now, 3)}


def get_intent_field_snapshot(window_sec: float = 120.0,
                               max_clusters: int = 50) -> Dict:
    """
    Return per-cluster intent scores with lat/lon for globe field rendering.

    Combines kc_slope, phase_coherence_delta, drift_magnitude, and
    drift_phase_coupling into a single intent_score (0–1) per cluster.
    The globe uses this to render the Intent Field as a heat overlay.

    Also emits composite label:
      FORMING      — intent_score > 0.6 and kc_slope rising
      COVERT       — PHASE_DRIFT_LOCK + rising intent
      BENIGN       — low intent / CLOUD_AUTOSCALE
    """
    timing = get_signal_timing_snapshot(window_sec=window_sec,
                                         max_clusters=max_clusters)
    clusters = timing['clusters']

    points = []
    for cl in clusters:
        cid = cl['cluster_id']
        # Derive lat/lon from cluster event history centroid
        buf = _cluster_event_history.get(cid, [])
        now = time.time()
        cutoff = now - window_sec
        window_events = [e for e in buf if e.get('ts', 0) >= cutoff and 'lat' in e]
        if not window_events:
            continue
        lat = sum(e['lat'] for e in window_events) / len(window_events)
        lon = sum(e['lon'] for e in window_events) / len(window_events)

        intent = cl['intent_score']
        pat    = cl['propagation_pattern']
        slope  = cl['kc_slope']

        if pat == 'PHASE_DRIFT_LOCK' and intent > 0.4:
            label = 'COVERT'
        elif intent > 0.6 and slope > 0.02:
            label = 'FORMING'
        elif intent < 0.15:
            label = 'BENIGN'
        else:
            label = 'MONITORING'

        points.append({
            'cluster_id':           cid,
            'lat':                  round(lat, 5),
            'lon':                  round(lon, 5),
            'intent_score':         intent,
            'label':                label,
            'kc_slope':             cl['kc_slope'],
            'propagation_pattern':  pat,
            'drift_phase_coupling': cl['drift_phase_coupling'],
        })

    points.sort(key=lambda x: x['intent_score'], reverse=True)
    return {
        'points':     points,
        'window_sec': window_sec,
        't_now':      round(time.time(), 3),
    }


# ---------------------------------------------------------------------------
# Control Struggle Index (CSI)
# ---------------------------------------------------------------------------

def compute_csi(ix_heat: Dict) -> Dict:
    """
    Control Struggle Index — headline metric for IX contention.

    CSI = (coherence × instability × asymmetry) / (path_stability + physical_alignment)

    Returns dict with csi value + severity label.
    """
    coherence = ix_heat.get('phase_inversion', 0)
    instability = ix_heat.get('latency_variance', 0)
    asymmetry = ix_heat.get('asymmetry', 0)
    synthetic = ix_heat.get('synthetic_density', 0)

    # Path stability: inverse of synthetic density + low latency variance
    path_stability = max(0.05, (1.0 - synthetic) * 0.6 + (1.0 - instability) * 0.4)
    # Physical alignment: inverse of synthetic density
    physical_alignment = max(0.05, 1.0 - synthetic)

    numerator = coherence * max(0.01, instability) * max(0.01, asymmetry)
    denominator = path_stability + physical_alignment
    csi = min(1.0, numerator / max(0.001, denominator) * 20.0)  # scaled

    if csi > 0.8:
        label = 'ACTIVE_CONFLICT'
    elif csi > 0.5:
        label = 'CONTESTED'
    else:
        label = 'STABLE'

    return {
        'csi': round(csi, 4),
        'label': label,
        'components': {
            'coherence': round(coherence, 4),
            'instability': round(instability, 4),
            'asymmetry': round(asymmetry, 4),
            'path_stability': round(path_stability, 4),
            'physical_alignment': round(physical_alignment, 4),
        }
    }


# ---------------------------------------------------------------------------
# Conflict Probability Forecaster
# ---------------------------------------------------------------------------

def forecast_conflict_probability(ix_name: str, horizon_sec: float = 30.0) -> Dict:
    """
    Short-horizon conflict probability using weighted ensemble of:
      - phase coherence trend
      - heat velocity + acceleration
      - synthetic routing density
      - asymmetry trend

    Returns probability [0,1] and classification.
    """
    trend = get_ix_pressure_trend(ix_name, window_sec=120.0)
    vel = trend.get('velocity', 0)
    acc = trend.get('acceleration', 0)
    current = trend.get('current', 0)

    # Extrapolate heat at t+horizon
    predicted_heat = current + vel * horizon_sec + 0.5 * acc * horizon_sec ** 2
    predicted_heat = max(0.0, min(1.0, predicted_heat))

    # Conflict probability = weighted combination
    p_heat = min(1.0, predicted_heat * 1.3)
    p_velocity = min(1.0, max(0, vel * 50.0))  # positive velocity → rising
    p_accel = min(1.0, max(0, acc * 200.0))     # positive acceleration → surging

    prob = (0.45 * p_heat + 0.30 * p_velocity + 0.25 * p_accel)
    prob = max(0.0, min(1.0, prob))

    if prob > 0.7:
        label = 'IMMINENT'
    elif prob > 0.4:
        label = 'LIKELY'
    elif prob > 0.2:
        label = 'POSSIBLE'
    else:
        label = 'UNLIKELY'

    return {
        'probability': round(prob, 4),
        'label': label,
        'predicted_heat': round(predicted_heat, 4),
        'horizon_sec': horizon_sec,
        'heat_velocity': round(vel, 6),
        'heat_acceleration': round(acc, 6),
    }


# ---------------------------------------------------------------------------
# Multi-IX Cascade Detector
# ---------------------------------------------------------------------------

# Cascade window: two IX spiking within this time with shared ASNs
_CASCADE_WINDOW_SEC = 30.0
_CASCADE_HEAT_THRESHOLD = 0.35
_cascade_history: List[Dict] = []
_CASCADE_MAX_HISTORY = 50


def detect_ix_cascades(ix_heats: List[Dict]) -> List[Dict]:
    """
    Detect cascading conflicts across IX nodes.

    A cascade occurs when:
      - IX_A heat spikes above threshold
      - IX_B heat spikes shortly after
      - Both share at least one connected ASN
      - Both show heat velocity > 0
    """
    now = time.time()
    cascades = []

    hot_ix = [h for h in ix_heats
              if h['heat'] > _CASCADE_HEAT_THRESHOLD]

    for i, a in enumerate(hot_ix):
        for b in hot_ix[i + 1:]:
            # Check ASN overlap
            asns_a = set(a.get('connected_asns', []))
            asns_b = set(b.get('connected_asns', []))
            shared = asns_a & asns_b
            if not shared:
                continue

            # Check temporal ordering via velocity
            trend_a = a.get('trend', {})
            trend_b = b.get('trend', {})
            vel_a = trend_a.get('velocity', 0)
            vel_b = trend_b.get('velocity', 0)

            # Both heating or one leading the other
            if vel_a <= 0 and vel_b <= 0:
                continue

            # Determine direction
            if vel_a > vel_b:
                src, dst = a, b
            else:
                src, dst = b, a

            # Cascade confidence
            overlap_ratio = len(shared) / max(1, min(len(asns_a), len(asns_b)))
            heat_product = src['heat'] * dst['heat']
            conf = min(1.0, overlap_ratio * 0.5 + heat_product * 2.0 +
                        max(abs(vel_a), abs(vel_b)) * 10.0)

            cascade = {
                'src_ix': src['name'],
                'dst_ix': dst['name'],
                'src_heat': src['heat'],
                'dst_heat': dst['heat'],
                'shared_asns': sorted(shared),
                'shared_count': len(shared),
                'confidence': round(min(1.0, conf), 3),
                'src_velocity': round(vel_a, 6),
                'dst_velocity': round(vel_b, 6),
                'ts': now,
            }
            cascades.append(cascade)

    # Record in cascade history
    for c in cascades:
        _cascade_history.append(c)
    # Trim history
    cutoff = now - _IX_HISTORY_TTL
    while _cascade_history and _cascade_history[0].get('ts', 0) < cutoff:
        _cascade_history.pop(0)
    if len(_cascade_history) > _CASCADE_MAX_HISTORY:
        _cascade_history[:] = _cascade_history[-_CASCADE_MAX_HISTORY:]

    cascades.sort(key=lambda c: c['confidence'], reverse=True)
    return cascades


# ---------------------------------------------------------------------------
# Synthetic / Physical Divergence Index
# ---------------------------------------------------------------------------

def compute_divergence_index(paths: List[Dict]) -> Dict:
    """
    Quantify how aggressively physical routing is being bypassed.

    divergence = syntheticPathLatency / physicalExpectedLatency

    Returns global and per-path divergence stats.
    """
    divergences = []
    flagged_paths = []

    for p in paths:
        hops = len(p.get('hop_path', []))
        if hops < 2:
            continue

        is_synthetic = p.get('is_synthetic', False)
        cable_dist = p.get('cable_alignment', {}).get('distance_km', 0)
        # Estimated physical latency: fiber at ~200,000 km/s + switching
        phys_latency = (cable_dist / 200.0) + hops * 0.5 if cable_dist > 0 else hops * 2.0

        # Synthetic paths: estimate latency from hop count + tunnel overhead
        if is_synthetic:
            synth_latency = hops * 3.0 + 10.0  # tunnel overhead
        else:
            synth_latency = hops * 1.5

        div = synth_latency / max(0.1, phys_latency)
        divergences.append(div)

        if div > 1.5:
            flagged_paths.append({
                'src_asn': p.get('src_asn', '?'),
                'dst_asn': p.get('dst_asn', '?'),
                'divergence': round(div, 3),
                'is_synthetic': is_synthetic,
                'hops': hops,
                'interpretation': (
                    'HEAVY_TUNNELING' if div > 2.0 else
                    'SUSPICIOUS_OVERLAY' if div > 1.5 else 'NORMAL'
                ),
            })

    avg_div = sum(divergences) / max(1, len(divergences))
    max_div = max(divergences) if divergences else 0.0

    return {
        'avg_divergence': round(avg_div, 3),
        'max_divergence': round(max_div, 3),
        'total_paths': len(divergences),
        'flagged_count': len(flagged_paths),
        'flagged_paths': sorted(flagged_paths,
                                 key=lambda x: x['divergence'], reverse=True)[:10],
        'interpretation': (
            'HEAVY_TUNNELING' if avg_div > 2.0 else
            'SUSPICIOUS_OVERLAY' if avg_div > 1.5 else
            'MODERATE' if avg_div > 1.0 else 'NORMAL'
        ),
    }


# ---------------------------------------------------------------------------
# Conflict Fingerprint Engine
# ---------------------------------------------------------------------------

_conflict_fingerprints: Dict[str, Dict] = {}
_FINGERPRINT_DECAY = 600.0  # 10 min TTL


def _fingerprint_key(asn_a: int, asn_b: int, ix_name: str) -> str:
    return f"{min(asn_a, asn_b)}-{max(asn_a, asn_b)}@{ix_name}"


def record_conflict_fingerprint(conflict: Dict) -> Optional[Dict]:
    """
    Store recurring conflict patterns. Returns fingerprint if this
    pattern has been seen before (with hit count + first seen).
    """
    now = time.time()
    pair = conflict.get('asn_pair', [0, 0])
    key = _fingerprint_key(pair[0], pair[1], conflict.get('ix', ''))

    if key in _conflict_fingerprints:
        fp = _conflict_fingerprints[key]
        fp['hits'] += 1
        fp['last_seen'] = now
        fp['last_confidence'] = conflict.get('confidence', 0)
        fp['last_type'] = conflict.get('type', '')
        # Update features with latest values
        fp['features']['coherence'] = conflict.get('coherence', 0)
        fp['features']['instability'] = conflict.get('instability', 0)
        fp['features']['synthetic_ratio'] = conflict.get('synthetic_ratio', 0)
        return fp
    else:
        fp = {
            'key': key,
            'asn_pair': pair,
            'ix': conflict.get('ix', ''),
            'first_seen': now,
            'last_seen': now,
            'hits': 1,
            'last_confidence': conflict.get('confidence', 0),
            'last_type': conflict.get('type', ''),
            'features': {
                'coherence': conflict.get('coherence', 0),
                'instability': conflict.get('instability', 0),
                'synthetic_ratio': conflict.get('synthetic_ratio', 0),
                'asymmetry': conflict.get('asymmetry', 0),
            },
        }
        _conflict_fingerprints[key] = fp
        return None  # first occurrence

    # Evict stale fingerprints
    stale = [k for k, v in _conflict_fingerprints.items()
             if now - v['last_seen'] > _FINGERPRINT_DECAY]
    for k in stale:
        del _conflict_fingerprints[k]


def get_active_fingerprints() -> List[Dict]:
    """Return all active (non-expired) conflict fingerprints."""
    now = time.time()
    active = []
    for k, fp in list(_conflict_fingerprints.items()):
        if now - fp['last_seen'] > _FINGERPRINT_DECAY:
            del _conflict_fingerprints[k]
            continue
        fp_copy = dict(fp)
        fp_copy['age_sec'] = round(now - fp['first_seen'], 1)
        fp_copy['recurrence'] = (
            'PERSISTENT' if fp['hits'] > 5 else
            'RECURRING' if fp['hits'] > 2 else
            'EMERGING'
        )
        active.append(fp_copy)
    active.sort(key=lambda x: x['hits'], reverse=True)
    return active


# ---------------------------------------------------------------------------
# Peering Conflict Detector
# ---------------------------------------------------------------------------

# Conflict type classification
CONFLICT_TYPES = {
    'PEERING_WAR': {
        'icon': '🔴',
        'desc': 'Active routing contention — ASNs competing for path control',
        'severity': 'CRITICAL',
    },
    'LOAD_SHEDDING': {
        'icon': '🟠',
        'desc': 'Intentional traffic reduction while coordination persists',
        'severity': 'HIGH',
    },
    'OVERLAY_BYPASS': {
        'icon': '🟡',
        'desc': 'Logical bypass of IX — traffic routed through overlay/VPN',
        'severity': 'MEDIUM',
    },
    'CAPACITY_CONTENTION': {
        'icon': '🟤',
        'desc': 'Traffic exceeds comfortable IX capacity — natural congestion',
        'severity': 'LOW',
    },
}


def detect_peering_conflicts(ix_heat: Dict, paths: List[Dict],
                              clusters_intel: List[Dict]) -> List[Dict]:
    """
    Detect peering conflicts at an IX based on traffic patterns,
    path instability, phase coherence, and ASN pair analysis.

    A peering conflict occurs when:
      - High traffic between two ASNs at an IX
      - Path instability (changing routes, high latency variance)
      - Phase coherence remains high (coordination despite instability)
      - Asymmetric flow (one side pushing harder)
    """
    conflicts = []
    ix_asns = set(ix_heat.get('connected_asns', []))
    ix_name = ix_heat['name']
    heat = ix_heat['heat']

    if heat < 0.1:
        return conflicts  # quiet IX — no conflicts

    # Gather ASN pair traffic data
    pair_scores: Dict[Tuple[int, int], Dict] = {}

    for p in paths:
        hop_asns = []
        for h in p.get('hop_path', []):
            try:
                hop_asns.append(int(str(h).replace('AS', '')))
            except (ValueError, TypeError):
                continue

        if not (ix_asns & set(hop_asns)):
            continue  # path doesn't transit this IX

        src = int(str(p.get('src_asn', '0')).replace('AS', ''))
        dst = int(str(p.get('dst_asn', '0')).replace('AS', ''))
        pair = (min(src, dst), max(src, dst))

        if pair not in pair_scores:
            pair_scores[pair] = {
                'traffic': 0.0,
                'synthetic_count': 0,
                'path_count': 0,
                'coherences': [],
            }

        ps = pair_scores[pair]
        ps['traffic'] += p.get('path_score', 0)
        ps['path_count'] += 1
        if p.get('is_synthetic'):
            ps['synthetic_count'] += 1

    # Enrich with phase coherence from nearby clusters
    for c in clusters_intel:
        centroid = c.get('centroid', [0, 0])
        d = _geodistance_km(ix_heat['lat'], ix_heat['lon'],
                            centroid[0], centroid[1])
        if d > 1500:
            continue
        coh = c.get('phase', {}).get('phase_coherence', 0)
        asn_str = c.get('asn', '')
        try:
            asn_num = int(asn_str.replace('AS', ''))
        except (ValueError, TypeError):
            continue
        # Add coherence data to all pairs involving this ASN
        for pair, ps in pair_scores.items():
            if asn_num in pair:
                ps['coherences'].append(coh)

    # Detect conflicts per pair
    for (asn_a, asn_b), ps in pair_scores.items():
        traffic = ps['traffic']
        path_count = ps['path_count']
        synthetic_ratio = ps['synthetic_count'] / max(1, path_count)
        avg_coherence = (sum(ps['coherences']) / len(ps['coherences'])
                         if ps['coherences'] else 0.0)

        # Determine conflict type
        conflict_type = None
        confidence = 0.0

        # PEERING WAR: high traffic + high coherence + path instability
        if (traffic > 0.3 and avg_coherence > 0.6 and
                ix_heat['latency_variance'] > 0.2):
            conflict_type = 'PEERING_WAR'
            confidence = min(1.0, traffic * avg_coherence *
                             (1 + ix_heat['latency_variance']))

        # LOAD SHEDDING: traffic drops but coherence stays high
        elif (ix_heat['asymmetry'] > 0.4 and avg_coherence > 0.5):
            conflict_type = 'LOAD_SHEDDING'
            confidence = min(1.0, ix_heat['asymmetry'] * avg_coherence)

        # OVERLAY BYPASS: high synthetic density through IX
        elif synthetic_ratio > 0.5 and avg_coherence > 0.4:
            conflict_type = 'OVERLAY_BYPASS'
            confidence = min(1.0, synthetic_ratio * avg_coherence)

        # CAPACITY CONTENTION: high traffic, low coherence
        elif traffic > 0.5 and avg_coherence < 0.4:
            conflict_type = 'CAPACITY_CONTENTION'
            confidence = min(1.0, traffic * 0.7)

        if conflict_type and confidence > 0.2:
            ct = CONFLICT_TYPES[conflict_type]
            # Resolve ASN names
            org_a = resolve_ip_to_asn_name(asn_a) or _ASN_INFRA_MAP.get(asn_a, '')
            org_b = resolve_ip_to_asn_name(asn_b) or _ASN_INFRA_MAP.get(asn_b, '')

            conflicts.append({
                'type': conflict_type,
                'icon': ct['icon'],
                'severity': ct['severity'],
                'ix': ix_name,
                'lat': ix_heat['lat'],
                'lon': ix_heat['lon'],
                'asn_pair': [asn_a, asn_b],
                'asn_labels': [f'AS{asn_a}', f'AS{asn_b}'],
                'org_pair': [org_a, org_b],
                'confidence': round(confidence, 3),
                'coherence': round(avg_coherence, 3),
                'traffic': round(traffic, 3),
                'asymmetry': round(ix_heat['asymmetry'], 3),
                'synthetic_ratio': round(synthetic_ratio, 3),
                'instability': round(ix_heat['latency_variance'], 3),
                'description': ct['desc'],
                'summary': _generate_conflict_summary(
                    conflict_type, ix_name, asn_a, asn_b, org_a, org_b,
                    avg_coherence, traffic, ix_heat),
            })

    # Sort by confidence descending
    conflicts.sort(key=lambda c: c['confidence'], reverse=True)
    return conflicts


def _generate_conflict_summary(conflict_type: str, ix_name: str,
                                asn_a: int, asn_b: int,
                                org_a: str, org_b: str,
                                coherence: float, traffic: float,
                                ix_heat: Dict) -> str:
    """Generate human-readable conflict summary."""
    a_label = f'AS{asn_a}' + (f' ({org_a})' if org_a else '')
    b_label = f'AS{asn_b}' + (f' ({org_b})' if org_b else '')

    if conflict_type == 'PEERING_WAR':
        return (f'Active routing contention at {ix_name} between {a_label} and '
                f'{b_label}. Phase coherence {coherence:.0%} with latency variance '
                f'{ix_heat["latency_variance"]:.0%} indicates coordinated path switching.')

    if conflict_type == 'LOAD_SHEDDING':
        return (f'Asymmetric traffic pattern at {ix_name}: {a_label} ↔ {b_label}. '
                f'Flow imbalance {ix_heat["asymmetry"]:.0%} while coherence remains '
                f'{coherence:.0%} — possible intentional throttling or blackholing.')

    if conflict_type == 'OVERLAY_BYPASS':
        return (f'{ix_name} logically bypassed: {a_label} ↔ {b_label} traffic routed '
                f'through overlay/VPN. No physical cable alignment despite high '
                f'coordination ({coherence:.0%}).')

    return (f'Capacity pressure at {ix_name}: {a_label} ↔ {b_label}. '
            f'Traffic volume {traffic:.2f} approaching IX limits.')


def ix_heatmap_snapshot(clusters_intel: List[Dict],
                         paths: Optional[List[Dict]] = None) -> Dict:
    """
    Full IX heatmap + peering conflict analysis + predictive intelligence.

    Returns heat scores, conflicts, CSI, forecasts, cascades,
    divergence, fingerprints, and global metrics.
    """
    if paths is None:
        paths = compute_inter_cluster_paths(clusters_intel)

    ix_heats = []
    all_conflicts = []

    for ix in IX_POINTS:
        heat = compute_ix_heat(ix, paths, clusters_intel)
        heat['trend'] = get_ix_pressure_trend(ix['name'])
        heat['csi'] = compute_csi(heat)
        heat['forecast'] = forecast_conflict_probability(ix['name'])
        ix_heats.append(heat)

        # Detect conflicts at this IX
        conflicts = detect_peering_conflicts(heat, paths, clusters_intel)
        # Record fingerprints for each conflict
        for c in conflicts:
            fp = record_conflict_fingerprint(c)
            if fp:
                c['fingerprint'] = {
                    'hits': fp['hits'],
                    'recurrence': (
                        'PERSISTENT' if fp['hits'] > 5 else
                        'RECURRING' if fp['hits'] > 2 else 'EMERGING'
                    ),
                    'first_seen_ago': round(time.time() - fp['first_seen'], 1),
                }
        all_conflicts.extend(conflicts)

    # Sort IX by heat descending
    ix_heats.sort(key=lambda h: h['heat'], reverse=True)

    # Multi-IX cascade detection
    cascades = detect_ix_cascades(ix_heats)

    # Synthetic/physical divergence index
    divergence = compute_divergence_index(paths)

    # Active fingerprints
    fingerprints = get_active_fingerprints()

    # Global metrics
    active_ix = [h for h in ix_heats if h['heat'] > 0.15]
    critical_ix = [h for h in ix_heats if h['tier'] == 'CRITICAL']
    total_heat = sum(h['heat'] for h in ix_heats)

    # Pressure trends for top heated IX
    trends = {}
    for h in ix_heats[:5]:
        trends[h['name']] = h['trend']

    # Conflict type distribution
    conflict_types = {}
    for c in all_conflicts:
        ct = c['type']
        conflict_types[ct] = conflict_types.get(ct, 0) + 1

    # Forecast summary
    imminent = [h for h in ix_heats
                if h.get('forecast', {}).get('label') == 'IMMINENT']

    return {
        'ix_heats': ix_heats,
        'conflicts': all_conflicts[:20],
        'conflict_count': len(all_conflicts),
        'conflict_types': conflict_types,
        'cascades': cascades[:10],
        'divergence': divergence,
        'fingerprints': fingerprints[:15],
        'trends': trends,
        'summary': {
            'total_ix': len(IX_POINTS),
            'active_ix': len(active_ix),
            'critical_ix': len(critical_ix),
            'total_heat': round(total_heat, 3),
            'avg_heat': round(total_heat / max(1, len(IX_POINTS)), 4),
            'total_conflicts': len(all_conflicts),
            'peering_wars': conflict_types.get('PEERING_WAR', 0),
            'load_shedding': conflict_types.get('LOAD_SHEDDING', 0),
            'overlay_bypass': conflict_types.get('OVERLAY_BYPASS', 0),
            'cascade_count': len(cascades),
            'imminent_forecasts': len(imminent),
            'divergence_index': divergence['avg_divergence'],
            'active_fingerprints': len(fingerprints),
        },
    }


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class CyberCluster:
    """
    Tactical "swarm object" summarizing a geographic cluster of hypergraph nodes.

    Fields mirror the shape expected by CyberCluster.java (ATAK plugin) and
    the cluster_to_cot() CoT generator below.
    """
    id:            str
    centroid_lat:  float
    centroid_lon:  float
    node_count:    int
    threat_score:  float            # 0.0 → 1.0
    rf_emitters:   int
    uav_count:     int
    c2_count:      int = 0
    asn:           str = ''        # dominant ASN label (e.g., "AS16509")
    asn_number:    Optional[int] = None   # numeric ASN
    asn_org:       str = ''        # organization name (e.g., "Amazon.com, Inc.")
    asn_confidence: float = 0.0    # fraction of nodes with dominant ASN (0–1)
    asn_diversity: int = 0         # unique ASN count in cluster
    country:       str = ''        # ISO country code of dominant ASN
    infra_type:    str = 'Unknown' # Hyperscaler/Edge CDN/VPS/ISP/Backbone/etc.
    behavior_type: str = 'MIXED'   # BOTNET | BEACON | SCAN | MIXED | RF_SWARM
    velocity_dx:   float = 0.0     # deg/s longitudinal drift
    velocity_dy:   float = 0.0     # deg/s latitudinal drift
    updated_at:    float = field(default_factory=time.time)

    # Internals used for velocity estimation (not serialised to CoT)
    _prev_lat:     float = 0.0
    _prev_lon:     float = 0.0
    _prev_ts:      float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        base = {
            'id':            self.id,
            'centroid_lat':  self.centroid_lat,
            'centroid_lon':  self.centroid_lon,
            'node_count':    self.node_count,
            'threat_score':  round(self.threat_score, 3),
            'rf_emitters':   self.rf_emitters,
            'uav_count':     self.uav_count,
            'c2_count':      self.c2_count,
            'asn':           self.asn,
            'asn_org':       self.asn_org,
            'asn_confidence': self.asn_confidence,
            'asn_diversity': self.asn_diversity,
            'country':       self.country,
            'infra_type':    self.infra_type,
            'behavior_type': self.behavior_type,
            'velocity_dx':   self.velocity_dx,
            'velocity_dy':   self.velocity_dy,
            'updated_at':    self.updated_at,
            # Derived for UI
            'radius_m':      self.radius_m(),
            'threat_label':  self.threat_label(),
            'cot_type':      self.cot_type(),
        }
        return base

    def to_intel(self) -> Dict[str, Any]:
        """Full intel narration including temporal pattern analysis."""
        return narrate_cluster(self)

    def radius_m(self) -> float:
        """Visual radius proportional to log(node_count) × 100 m."""
        if self.node_count <= 1:
            return 200.0
        return max(200.0, math.log10(self.node_count) * 100.0 * 1000)

    def threat_label(self) -> str:
        if self.threat_score >= 0.8:  return 'CRITICAL'
        if self.threat_score >= 0.6:  return 'HIGH'
        if self.threat_score >= 0.4:  return 'MEDIUM'
        return 'LOW'

    def cot_type(self) -> str:
        """Map behavior type to ATAK CoT type string."""
        bt = self.behavior_type
        if bt == 'BOTNET':   return 'cyber.botnet.swarm'
        if bt == 'BEACON':   return 'cyber.beacon.cluster'
        if bt == 'SCAN':     return 'cyber.scan.cluster'
        if bt == 'RF_SWARM': return 'a-u-G-U-C-I'     # hostile unknown RF
        return 'cyber.mixed.cluster'


# ---------------------------------------------------------------------------
# Core cluster detection
# ---------------------------------------------------------------------------

def _node_to_dict(node: Any) -> Dict[str, Any]:
    """Normalize HGNode / dict / object to plain dict."""
    if isinstance(node, dict):
        return node
    if hasattr(node, 'to_dict'):
        try:
            return node.to_dict()
        except Exception:
            pass
    # Fallback: attribute access
    return {
        'id':       getattr(node, 'id', ''),
        'kind':     getattr(node, 'kind', ''),
        'position': getattr(node, 'position', None),
        'frequency':getattr(node, 'frequency', None),
        'labels':   dict(getattr(node, 'labels', {}) or {}),
        'metadata': dict(getattr(node, 'metadata', {}) or {}),
        'updated_at': getattr(node, 'updated_at', time.time()),
    }


def _geo_bucket(lat: float, lon: float) -> str:
    """Round lat/lon to nearest GEO_BUCKET_DEG cell."""
    blat = math.floor(lat / GEO_BUCKET_DEG) * GEO_BUCKET_DEG
    blon = math.floor(lon / GEO_BUCKET_DEG) * GEO_BUCKET_DEG
    return f"{blat:.1f},{blon:.1f}"


def _cluster_id(bucket: str) -> str:
    return 'swarm-' + hashlib.md5(bucket.encode()).hexdigest()[:8]


def _infer_behavior(nodes_in_cluster: List[Dict]) -> str:
    """Infer dominant behavior type from node kinds and labels."""
    rf_count  = sum(1 for n in nodes_in_cluster
                    if n.get('kind', '').lower() in KIND_RF_EMITTER)
    uav_count = sum(1 for n in nodes_in_cluster
                    if n.get('kind', '').lower() in KIND_UAV)
    bsg_types: List[str] = []
    for n in nodes_in_cluster:
        labels = n.get('labels') or {}
        bt = str(labels.get('behavior', labels.get('behavior_type', ''))).upper()
        if bt:
            bsg_types.append(bt)

    if rf_count > 0 and rf_count >= len(nodes_in_cluster) * 0.4:
        return 'RF_SWARM'
    if 'BEACON' in bsg_types:     return 'BEACON'
    if 'PORT_SCAN' in bsg_types or 'HORIZ_SCAN' in bsg_types:
        return 'SCAN'
    if bsg_types:                 return 'BOTNET'
    return 'MIXED'


def _threat_score(nodes_in_cluster: List[Dict]) -> float:
    """Compute threat score [0,1] as weighted mean of node confidence."""
    total_weight = 0.0
    total_conf   = 0.0
    for n in nodes_in_cluster:
        labels = n.get('labels') or {}
        meta   = n.get('metadata') or {}
        obs    = str(labels.get('obs_class', 'unknown')).lower()
        try:
            conf = float(labels.get('confidence',
                          meta.get('confidence', 0.5)))
        except (TypeError, ValueError):
            conf = 0.5
        w      = THREAT_WEIGHTS.get(obs, 0.3)
        total_conf   += conf * w
        total_weight += w

    if total_weight == 0:
        return 0.1
    return min(1.0, total_conf / total_weight)


def detect_clusters(
    nodes: Any,
    edges: Any,
    *,
    geo_bucket_deg: float = GEO_BUCKET_DEG,
    min_size: int = MIN_CLUSTER_SIZE,
) -> List[CyberCluster]:
    """
    Collapse hypergraph nodes into CyberCluster swarm objects.

    Args:
        nodes:  iterable of HGNode objects or dicts with 'position', 'kind', etc.
        edges:  iterable of HGEdge objects or dicts (used for velocity future work)
        geo_bucket_deg: grid cell size in degrees (default 1.0°)
        min_size: minimum nodes per cluster to keep

    Returns:
        List of CyberCluster, sorted descending by threat_score.
    """
    # ---- Normalise input -------------------------------------------------
    node_dicts: List[Dict] = []
    nodes_iter = nodes.values() if hasattr(nodes, 'values') else nodes
    for n in nodes_iter:
        d = _node_to_dict(n)
        if d.get('id'):
            node_dicts.append(d)

    # ---- Group by geo bucket --------------------------------------------
    buckets: Dict[str, List[Dict]] = {}
    no_geo_count = 0
    for nd in node_dicts:
        pos = nd.get('position')
        if not pos or len(pos) < 2:
            no_geo_count += 1
            continue
        try:
            lat, lon = float(pos[0]), float(pos[1])
        except (TypeError, ValueError):
            no_geo_count += 1
            continue
        if lat == 0 and lon == 0:
            no_geo_count += 1
            continue
        bkey = _geo_bucket(lat, lon)
        buckets.setdefault(bkey, []).append(nd)

    if no_geo_count > 0:
        logger.debug("detect_clusters: %d nodes without geo position skipped",
                     no_geo_count)

    # ---- Build CyberCluster per bucket ----------------------------------
    clusters: List[CyberCluster] = []
    for bucket_key, members in buckets.items():
        if len(members) < min_size:
            continue

        lats = []
        lons = []
        rf_em = uav_em = c2_em = 0
        asn_counts: Dict[str, int] = {}
        ts_list: List[float] = []

        for nd in members:
            pos = nd.get('position', [])
            lats.append(float(pos[0]))
            lons.append(float(pos[1]))

            kind = (nd.get('kind') or '').lower()
            if kind in KIND_RF_EMITTER:  rf_em  += 1
            if kind in KIND_UAV:         uav_em += 1
            if kind in KIND_C2:          c2_em  += 1

            labels = nd.get('labels') or {}
            meta   = nd.get('metadata') or {}
            asn = str(labels.get('asn', meta.get('asn', ''))).strip()
            if asn:
                asn_counts[asn] = asn_counts.get(asn, 0) + 1

            ts = nd.get('updated_at') or nd.get('created_at') or time.time()
            try:
                ts_list.append(float(ts))
            except (TypeError, ValueError):
                pass

        centroid_lat = sum(lats) / len(lats)
        centroid_lon = sum(lons) / len(lons)
        dominant_asn = max(asn_counts, key=asn_counts.get) if asn_counts else ''
        behavior     = _infer_behavior(members)
        threat       = _threat_score(members)
        cid          = _cluster_id(bucket_key)
        ts_max       = max(ts_list) if ts_list else time.time()

        # ASN / Infrastructure fusion via MaxMind GeoLite2
        asn_enrichment = enrich_cluster_asn(members)
        resolved_asn   = asn_enrichment['dominant_asn'] or dominant_asn
        asn_number     = asn_enrichment['asn_number']
        asn_org        = asn_enrichment['asn_org']
        infra          = classify_infra(asn_number, asn_org, behavior)

        # Risk modifier: mixed ASN (diversity > 3 + low confidence) → suspicious
        if asn_enrichment['asn_diversity'] > 3 and asn_enrichment['asn_confidence'] < 0.5:
            threat = min(1.0, threat + 0.1)

        clusters.append(CyberCluster(
            id             = cid,
            centroid_lat   = centroid_lat,
            centroid_lon   = centroid_lon,
            node_count     = len(members),
            threat_score   = threat,
            rf_emitters    = rf_em,
            uav_count      = uav_em,
            c2_count       = c2_em,
            asn            = resolved_asn,
            asn_number     = asn_number,
            asn_org        = asn_org,
            asn_confidence = asn_enrichment['asn_confidence'],
            asn_diversity  = asn_enrichment['asn_diversity'],
            country        = asn_enrichment['country'],
            infra_type     = infra,
            behavior_type  = behavior,
            updated_at     = ts_max,
        ))

        # Record temporal event for pattern analysis — with ASN + position for phase coherence
        record_cluster_event(cid, ts_max, energy=threat, event_type=behavior.lower(),
                             asn=resolved_asn,
                             position=(centroid_lat, centroid_lon))

    clusters.sort(key=lambda c: c.threat_score, reverse=True)
    logger.info("detect_clusters: %d geo-clusters from %d nodes (%d no-geo)",
                len(clusters), len(node_dicts), no_geo_count)
    # Update cluster cache so decompose_cluster() can access objects without re-running detection.
    global _cluster_cache
    _cluster_cache = {c.id: c for c in clusters}
    return clusters


def intel_snapshot(
    nodes: Any, edges: Any, *,
    geo_bucket_deg: float = GEO_BUCKET_DEG,
    min_size: int = MIN_CLUSTER_SIZE,
) -> List[Dict[str, Any]]:
    """
    High-level convenience: detect clusters and return full intel narration
    for each, sorted by threat_score descending.
    """
    clusters = detect_clusters(nodes, edges,
                               geo_bucket_deg=geo_bucket_deg,
                               min_size=min_size)
    return [narrate_cluster(c) for c in clusters]


# ---------------------------------------------------------------------------
# Latent Swarm Decomposition — deep cluster autopsy
# ---------------------------------------------------------------------------

_ARCHETYPES: List[tuple] = [
    ('Silent Lattice',           'High density, low chatter, multi-ASN blending, periodic synchronization'),
    ('Ghost Mesh',               'Near-zero activity, high phase coherence — coordinated standby'),
    ('Staging Constellation',    'Concentrated nodes, minimal traffic — pre-deployment posture'),
    ('Beacon Forest',            'Periodic heartbeat dominates — scheduler or botnet signature'),
    ('Electronic Warfare Array', 'Directional RF dominance — jamming or SIGINT profile'),
    ('Decaying Field',           'Scattered, low energy, fading — abandoned or fragmenting'),
    ('Active Mesh',              'High activity, elevated threat — operational or attack phase'),
]

_NODE_TIER_TABLE: List[tuple] = [
    (6,            'Probe Cluster',         'Single-target reconnaissance or test group'),
    (20,           'Small Cell',            'Targeted task group — limited operational scope'),
    (80,           'Operational Group',     'Coordinated task execution capability'),
    (153,          'Distributed Tasking',   'Parallel workload distribution — complex objective'),
    (350,          'Regional Mesh',         'Geographic or subnet-scale coordination layer'),
    (700,          'Infrastructure-Scale',  'Broad persistent infrastructure — high strategic value'),
    (float('inf'), 'Macro Infrastructure',  'Nation-state or hyperscale operational footprint'),
]


def _classify_archetype(
    cluster: 'CyberCluster',
    temporal: Dict,
    phase: Dict,
    act: float,
    per: float,
    dr: float,
) -> Dict[str, Any]:
    ph_coh = phase.get('phase_coherence', 0.0)
    n = cluster.node_count
    node_conc = min(1.0, math.log10(n + 1) / 3.0)

    if act > 0.4 and cluster.threat_score > 0.5:
        label, desc = _ARCHETYPES[6]   # Active Mesh
    elif dr > 0.5 and cluster.rf_emitters > 0:
        label, desc = _ARCHETYPES[4]   # EW Array
    elif per > 0.7:
        label, desc = _ARCHETYPES[3]   # Beacon Forest
    elif act < 0.05 and ph_coh > 0.6:
        label, desc = _ARCHETYPES[1]   # Ghost Mesh
    elif node_conc > 0.5 and act < 0.1 and cluster.asn_diversity > 2:
        label, desc = _ARCHETYPES[0]   # Silent Lattice
    elif node_conc > 0.4 and act < 0.15:
        label, desc = _ARCHETYPES[2]   # Staging Constellation
    else:
        label, desc = _ARCHETYPES[5]   # Decaying Field

    traits = []
    if node_conc > 0.6:              traits.append('High density')
    if ph_coh > 0.5:                 traits.append('Phase-locked')
    if act < 0.1:                    traits.append('Low emission')
    if cluster.asn_diversity > 3:    traits.append('Multi-ASN blend')
    if per > 0.5:                    traits.append('Periodic signature')
    return {'label': label, 'description': desc, 'traits': traits}


def _node_count_tier(n: int) -> Dict[str, Any]:
    for threshold, label, desc in _NODE_TIER_TABLE:
        if n <= threshold:
            return {'label': label, 'description': desc, 'node_count': n}
    return {'label': 'Macro Infrastructure', 'description': 'Nation-state or hyperscale', 'node_count': n}


def decompose_cluster(
    cluster: 'CyberCluster',
    narration: Optional[Dict] = None,
) -> Dict[str, Any]:
    """
    Deep decomposition of a cluster for the Latent Swarm Autopsy panel.

    Augments an existing narration object (from narrate_cluster) with additional
    intelligence layers. Does NOT re-run detect_clusters() to avoid inflating
    the event history.

    Args:
        cluster:   CyberCluster object (from _cluster_cache, populated by detect_clusters)
        narration: optional pre-computed narration dict; if None, calls narrate_cluster()

    Returns a dict suitable for the frontend Autopsy modal.
    """
    if narration is None:
        narration = narrate_cluster(cluster)

    temporal = narration.get('temporal') or _temporal_analysis(cluster.id)
    phase    = narration.get('phase')    or compute_phase_coherence(cluster.id)

    now = time.time()
    n   = cluster.node_count
    buf = _cluster_event_history.get(cluster.id, [])

    # ── 1. Dimensional Density ────────────────────────────────────────────
    # node_concentration: log-normalized count (0=isolated, 1=dense field)
    node_concentration = min(1.0, math.log10(n + 1) / 3.0)
    burst_rate         = temporal.get('burst_rate', 0.0)
    temporal_activity  = min(1.0, burst_rate / 5.0)
    asn_div_score      = min(1.0, cluster.asn_diversity / 8.0)
    signal_coherence   = phase.get('phase_coherence', 0.0)
    dimensional_density = {
        'node_concentration':  round(node_concentration, 3),
        'temporal_activity':   round(temporal_activity, 3),
        'asn_diversity_score': round(asn_div_score, 3),
        'signal_coherence':    round(signal_coherence, 3),
    }

    # ── 2. ASN Breakdown (observed event data only; no synthetic attribution) ──
    asn_event_counts: Dict[str, int] = {}
    for e in buf:
        if not e.get('_keyframe') and e.get('asn'):
            asn_event_counts[e['asn']] = asn_event_counts.get(e['asn'], 0) + 1

    asn_breakdown: List[Dict] = []
    if asn_event_counts:
        total_tagged = sum(asn_event_counts.values())
        for asn_label, count in sorted(asn_event_counts.items(), key=lambda x: -x[1]):
            asn_breakdown.append({
                'asn': asn_label, 'fraction': round(count / total_tagged, 3),
                'source': 'observed_events',
            })
    elif cluster.asn:
        # Fall back to cluster-level summary — no invented ASN names
        asn_breakdown.append({
            'asn': cluster.asn, 'org': cluster.asn_org,
            'fraction': round(cluster.asn_confidence, 3),
            'source': 'dominant_asn',
        })
        other_n = max(0, cluster.asn_diversity - 1)
        remainder = round(1.0 - cluster.asn_confidence, 3)
        if other_n > 0 and remainder > 0.01:
            asn_breakdown.append({
                'asn': f'({other_n} other ASN{"s" if other_n > 1 else ""})',
                'fraction': remainder,
                'source': 'unattributed',
            })

    # ── 3. Behavior Fingerprint (event-type distribution from ring buffer) ──
    type_counts: Dict[str, int] = {}
    total_events = 0
    for e in buf:
        etype = e.get('type', 'network')
        count = e.get('event_count', 1) if e.get('_keyframe') else 1
        type_counts[etype] = type_counts.get(etype, 0) + count
        total_events += count
    behavior_fingerprint: List[Dict] = []
    if total_events > 0:
        for etype, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
            behavior_fingerprint.append({'mode': etype, 'fraction': round(cnt / total_events, 3)})
    else:
        behavior_fingerprint = [{'mode': cluster.behavior_type.lower(), 'fraction': 1.0}]

    # ── 4. Temporal Ghost Events (recent retained events, last 24h) ──────
    window_24h = [e for e in buf if not e.get('_keyframe') and now - e.get('ts', 0) <= 86400]
    window_24h.sort(key=lambda e: e.get('energy', 0), reverse=True)
    ghost_events = []
    for e in window_24h[:5]:
        ghost_events.append({
            'ts': round(e['ts']), 'age_s': round(now - e['ts']),
            'energy': round(e.get('energy', 0.5), 3),
            'type': e.get('type', 'network'), 'asn': e.get('asn', ''),
        })
    keyframes_24h = [e for e in buf if e.get('_keyframe') and now - e.get('ts', 0) <= 86400]
    ghost_note = 'ring_buffer_limited' if len(buf) >= _MAX_EVENT_HISTORY else 'retained_events'

    # ── 5. Subclusters (heuristic 3-tier fragmentation) ──────────────────
    core_n      = max(1, round(n * 0.30))
    periphery_n = max(1, round(n * 0.45))
    drift_n     = max(0, n - core_n - periphery_n)
    subclusters = [
        {'tier': 'Core Spine',      'node_estimate': core_n,      'fraction': round(core_n / n, 3)},
        {'tier': 'Peripheral Ring', 'node_estimate': periphery_n, 'fraction': round(periphery_n / n, 3)},
        {'tier': 'Drift Nodes',     'node_estimate': drift_n,     'fraction': round(drift_n / n, 3)},
    ]

    # ── 6. Heuristic Intent Scores (NOT probabilities; basis shown) ──────
    per = temporal.get('periodicity', 0.0)
    dr  = temporal.get('directionality', 0.0)
    ts  = cluster.threat_score
    ph  = signal_coherence
    act = temporal_activity
    rf_frac = min(cluster.rf_emitters / max(n, 1), 1.0)
    c2_frac = min(cluster.c2_count    / max(n, 1), 1.0)

    intent_scores = [
        {'label': 'Staging Infrastructure', 'basis': 'node_concentration × ASN_diversity × inactivity',
         'score': round(min(1.0, node_concentration * 0.5 + asn_div_score * 0.3 + (1 - act) * 0.2), 3)},
        {'label': 'Traffic Relay Mesh',     'basis': 'inverse_threat × node_concentration × ASN_diversity',
         'score': round(min(1.0, (1 - ts) * 0.3 + node_concentration * 0.4 + asn_div_score * 0.3), 3)},
        {'label': 'Active C2 Network',      'basis': 'c2_fraction × phase_coherence × threat',
         'score': round(min(1.0, c2_frac * 0.4 + ph * 0.4 + ts * 0.2), 3)},
        {'label': 'Botnet Mesh',            'basis': 'periodicity × behavior_match × activity',
         'score': round(min(1.0, per * 0.5 + (0.3 if cluster.behavior_type == 'BOTNET' else 0) + act * 0.2), 3)},
        {'label': 'Electronic Warfare',     'basis': 'directionality × RF_fraction × activity',
         'score': round(min(1.0, dr * 0.5 + rf_frac * 0.4 + act * 0.1), 3)},
        {'label': 'Abandoned / Decaying',   'basis': 'inactivity × inverse_threat × incoherence',
         'score': round(min(1.0, (1 - act) * 0.4 + (1 - ts) * 0.4 + (1 - ph) * 0.2), 3)},
    ]
    intent_scores.sort(key=lambda x: x['score'], reverse=True)

    # ── 7. Hypothetical Activation Cascade (simulation, NOT forecast) ────
    beacon_n = max(5, round(n * 0.17))
    route_n  = max(10, round(n * 0.58))
    activation_cascade = {
        '_note': 'HYPOTHETICAL SIMULATION — not a prediction; assumes ideal coordination',
        'steps': [
            {'t_s':  0, 'description': f'{beacon_n} nodes begin beacon broadcast',            'nodes_active': beacon_n},
            {'t_s':  5, 'description': f'{route_n} nodes establish outbound routes',           'nodes_active': route_n},
            {'t_s': 12, 'description': 'ASN blending increases — attribution difficulty elevated', 'nodes_active': route_n},
            {'t_s': 30, 'description': f'Full mesh operational — all {n} nodes engaged',       'nodes_active': n},
        ],
    }

    # ── 8. Silence Pressure (composite inactivity metric) ────────────────
    last_ts = max((e.get('ts', 0) for e in buf if not e.get('_keyframe')), default=0.0)
    inactivity_hours = (now - last_ts) / 3600.0 if last_ts > 0 else 24.0
    sp_raw = math.log2(n + 1) * min(inactivity_hours, 24.0) * (0.2 + ph * 0.8)
    sp_max = math.log2(10001) * 24.0  # normalise against 10k nodes dormant 24h
    silence_pressure = {
        'raw':              round(sp_raw, 3),
        'normalized':       round(min(1.0, sp_raw / sp_max), 3),
        'level':            'HIGH' if sp_raw > sp_max * 0.6 else 'MEDIUM' if sp_raw > sp_max * 0.25 else 'LOW',
        'inactivity_hours': round(inactivity_hours, 1),
    }

    # ── 9. Cluster Archetype ─────────────────────────────────────────────
    archetype = _classify_archetype(cluster, temporal, phase, act, per, dr)

    # ── 10. Node count interpretation tier ───────────────────────────────
    node_tier = _node_count_tier(n)

    return {
        'cluster_id':            cluster.id,
        'node_count':            n,
        'dimensional_density':   dimensional_density,
        'asn_breakdown':         asn_breakdown,
        'behavior_fingerprint':  behavior_fingerprint,
        'temporal_ghost_events': {
            'events':         ghost_events,
            'keyframes_24h':  len(keyframes_24h),
            'note':           ghost_note,
        },
        'subclusters':           subclusters,
        'intent_scores':         intent_scores,
        'activation_cascade':    activation_cascade,
        'silence_pressure':      silence_pressure,
        'archetype':             archetype,
        'node_tier':             node_tier,
        'generated_at':          round(now),
    }


# ---------------------------------------------------------------------------
# CoT XML generation
# ---------------------------------------------------------------------------

def cluster_to_cot(cluster: CyberCluster, stale_seconds: int = 120) -> bytes:
    """
    Serialise a CyberCluster to a CoT XML event (bytes).

    Custom type strings (e.g. "cyber.botnet.swarm") are valid CoT — ATAK
    renders them as generic markers and the SCYTHE plugin uses them for
    custom GL rendering.

    CoT stale = now + stale_seconds so ATAK auto-removes absent clusters.
    """
    now   = datetime.now(timezone.utc)
    stale = datetime.fromtimestamp(now.timestamp() + stale_seconds, timezone.utc)

    def iso(dt: datetime) -> str:
        return dt.strftime('%Y-%m-%dT%H:%M:%SZ')

    cot = (
        f'<event version="2.0"'
        f' uid="{cluster.id}"'
        f' type="{cluster.cot_type()}"'
        f' time="{iso(now)}"'
        f' start="{iso(now)}"'
        f' stale="{iso(stale)}"'
        f' how="m-g">'
        f'<point lat="{cluster.centroid_lat:.6f}"'
        f'       lon="{cluster.centroid_lon:.6f}"'
        f'       hae="0" ce="{int(cluster.radius_m())}" le="9999999"/>'
        f'<detail>'
        f'  <contact callsign="{cluster.behavior_type}-{cluster.id[-6:]}" />'
        f'  <cluster'
        f'    nodes="{cluster.node_count}"'
        f'    threat="{cluster.threat_score:.3f}"'
        f'    threat_label="{cluster.threat_label()}"'
        f'    rf_emitters="{cluster.rf_emitters}"'
        f'    uav_count="{cluster.uav_count}"'
        f'    asn="{cluster.asn}"'
        f'    behavior="{cluster.behavior_type}"'
        f'    radius_m="{cluster.radius_m():.0f}"'
        f'    vel_dx="{cluster.velocity_dx:.6f}"'
        f'    vel_dy="{cluster.velocity_dy:.6f}" />'
        f'  <remarks>{cluster.threat_label()} swarm: {cluster.node_count} nodes'
        f' | {cluster.behavior_type}'
        f'{" | RF×" + str(cluster.rf_emitters) if cluster.rf_emitters else ""}'
        f'{" | UAV×" + str(cluster.uav_count) if cluster.uav_count else ""}'
        f'  </remarks>'
        f'</detail>'
        f'</event>'
    )
    return cot.encode('utf-8')


def clusters_to_cot_list(clusters: List[CyberCluster]) -> List[bytes]:
    """Return list of CoT XML bytes, one per cluster."""
    return [cluster_to_cot(c) for c in clusters]


# ---------------------------------------------------------------------------
# Phantom IX Detection Engine (PXDE)
# ---------------------------------------------------------------------------

# Temporal buffer: grid_cell → deque of convergence events
_PHANTOM_TEMPORAL_BUFFER: Dict[str, deque] = {}

def _grid_cell(lat: float, lon: float, resolution_deg: float = 2.0) -> str:
    """Discretize lat/lon to grid cell key."""
    gl  = math.floor(lat  / resolution_deg) * resolution_deg
    glo = math.floor(lon  / resolution_deg) * resolution_deg
    return f"{gl:.1f},{glo:.1f}"

def _nearest_known_ix_dist_km(lat: float, lon: float) -> float:
    """Distance in km to nearest known IX point."""
    return min(_geodistance_km(lat, lon, ix['lat'], ix['lon']) for ix in IX_POINTS)

def _compute_latency_geometry_violation(path: Dict) -> float:
    """
    Ratio: synthetic/physical latency estimate.
    <0.7 = too fast (cloud fabric), >1.5 = suspicious overlay, >2.0 = heavy tunneling.
    1.0 = expected.
    """
    centroids = path.get('centroids', [[0, 0], [0, 0]])
    if len(centroids) < 2:
        return 1.0
    dist_km = _geodistance_km(
        centroids[0][0], centroids[0][1], centroids[1][0], centroids[1][1])
    hops = path.get('hop_count', 3)
    expected_ms  = (dist_km / 200_000.0) * 1000.0 + hops * 0.5
    synthetic_ms = hops * 3.0 + 10.0
    if expected_ms < 0.1:
        return 1.0
    return round(synthetic_ms / expected_ms, 3)

def _extract_path_midpoints(paths: List[Dict]) -> List[Dict]:
    """Infer geographic midpoint of each path from cluster centroids."""
    midpoints = []
    for p in paths:
        c = p.get('centroids', [])
        if len(c) < 2:
            continue
        midpoints.append({
            'lat':          (c[0][0] + c[1][0]) / 2.0,
            'lon':          (c[0][1] + c[1][1]) / 2.0,
            'src_asn':      p.get('src_asn', ''),
            'dst_asn':      p.get('dst_asn', ''),
            'hop_path':     p.get('hop_path', []),
            'is_synthetic': p.get('is_synthetic', False),
            'path_score':   p.get('path_score', 0.0),
            'hop_count':    p.get('hop_count', 0),
        })
    return midpoints

def _cluster_midpoints_by_grid(midpoints: List[Dict],
                                resolution_deg: float = 2.0) -> Dict[str, List[Dict]]:
    cells: Dict[str, List[Dict]] = {}
    for mp in midpoints:
        key = _grid_cell(mp['lat'], mp['lon'], resolution_deg)
        cells.setdefault(key, []).append(mp)
    return cells

def _compute_asn_entropy(paths: List[Dict]) -> float:
    """Shannon entropy of ASN diversity across paths."""
    from math import log2
    asn_counts: Dict[str, int] = {}
    for p in paths:
        for asn in p.get('hop_path', []):
            asn_counts[asn] = asn_counts.get(asn, 0) + 1
    total = sum(asn_counts.values())
    if total == 0:
        return 0.0
    return round(-sum((c / total) * log2(c / total)
                      for c in asn_counts.values() if c > 0), 3)

def _classify_phantom_type(cell_paths: List[Dict],
                            latency_ratio: float,
                            ix_dist_km: float) -> str:
    """
    Expanded phantom type classification with 7 categories.
    Distinguishes hyperscaler abstraction, SD-WAN, proxy chains, dark fiber,
    and coordinated edge swarms from the original 3 types.
    """
    _HYPERSCALER   = {'AS16509', 'AS15169', 'AS8075', 'AS13335', 'AS20940', 'AS54113'}
    _CDN_PROVIDERS = {'AS20940', 'AS13335', 'AS54113', 'AS16625', 'AS22822', 'AS30675'}
    _MOBILE_EDGE   = {'AS6185', 'AS7922', 'AS20001', 'AS7018', 'AS22394'}

    synth_ratio  = sum(1 for p in cell_paths if p.get('is_synthetic')) / max(1, len(cell_paths))
    avg_hops     = sum(p.get('hop_count', 3) for p in cell_paths) / max(1, len(cell_paths))
    all_asns     = {asn for p in cell_paths for asn in p.get('hop_path', [])}
    hyper_overlap = len(all_asns & _HYPERSCALER)
    cdn_overlap   = len(all_asns & _CDN_PROVIDERS)
    mobile_edge   = len(all_asns & _MOBILE_EDGE)
    path_count    = len(cell_paths)
    avg_score     = sum(p.get('path_score', 0.1) for p in cell_paths) / max(1, path_count)

    # TOO_FAST + hyperscaler → internal hyperscaler backbone fabric
    if latency_ratio < 0.7 and hyper_overlap >= 2:
        return 'HYPERSCALER_ABSTRACTION_LAYER'

    # Stable low latency + CDN + moderate synth → software-defined backbone
    if latency_ratio < 0.9 and cdn_overlap >= 1 and synth_ratio > 0.3 and avg_hops < 5:
        return 'SOFTWARE_DEFINED_BACKBONE'

    # High hop count + high synth + variable latency → proxy chain relay
    if synth_ratio > 0.65 and avg_hops > 5:
        return 'PROXY_CHAIN_RELAY'

    # Very far from any cable + low hop count + consistent latency → dark fiber lease
    if ix_dist_km > 800 and avg_hops <= 3 and synth_ratio < 0.4 and path_count >= 4:
        return 'DARK_FIBER_LEASE'

    # Large path count + mobile edge ASNs + burst behavior → coordinated edge swarm
    if path_count >= 8 and (mobile_edge >= 1 or avg_score > 0.4) and synth_ratio > 0.5:
        return 'COORDINATED_EDGE_SWARM'

    # Original types as fallbacks:
    if synth_ratio > 0.5 and avg_hops > 4:
        return 'RELAY_MESH_HUB'
    if len(cell_paths) < 4 and synth_ratio > 0.5:
        return 'EPHEMERAL_SWARM'
    return 'RELAY_MESH_HUB'


def _compute_latency_dilation_fingerprint(paths: List[Dict]) -> Dict:
    """
    Classify routing behavior by comparing observed vs expected latency.
    Returns dilation pattern + interpretation for phantom typing.
    """
    if not paths:
        return {'pattern': 'UNKNOWN', 'ratio_mean': 0.0, 'ratio_std': 0.0}

    ratios = []
    for p in paths:
        obs = p.get('latency_ms', 0)
        hops = max(1, p.get('hop_count', 3))
        dist = p.get('distance_km', 500)
        expected = (dist / 200_000) * 1000 + hops * 0.5
        synth = hops * 3.0 + 10.0
        ratio = obs / max(1.0, expected) if obs > 0 else synth / max(1.0, expected)
        ratios.append(ratio)

    mean_r = sum(ratios) / len(ratios)
    std_r  = (sum((r - mean_r)**2 for r in ratios) / len(ratios)) ** 0.5

    if mean_r < 0.7:
        pattern = 'TOO_FAST'        # hyperscaler fabric / internal backbone
    elif mean_r < 0.95 and std_r < 0.1:
        pattern = 'TOO_SMOOTH'      # SD-WAN / overlay normalization
    elif std_r > 0.4:
        pattern = 'TOO_JITTERY'     # proxy chain / VPN tunnel
    elif std_r < 0.05 and mean_r > 1.0:
        pattern = 'TOO_CONSISTENT'  # orchestrated relay mesh
    elif mean_r > 2.0:
        pattern = 'HEAVY_TUNNELING' # deep relay / obfuscation
    elif mean_r > 1.5:
        pattern = 'SUSPICIOUS_OVERLAY'
    else:
        pattern = 'NORMAL'

    return {
        'pattern':    pattern,
        'ratio_mean': round(mean_r, 3),
        'ratio_std':  round(std_r, 3),
        'sample_count': len(ratios),
    }


def _record_phantom_event(key: str, lat: float, lon: float,
                           coherence: float, paths_count: int) -> None:
    buf = _PHANTOM_TEMPORAL_BUFFER.setdefault(key, deque(maxlen=60))
    buf.append({'ts': time.time(), 'lat': lat, 'lon': lon,
                'coherence': coherence, 'paths_count': paths_count})

def _compute_persistence_score(key: str) -> float:
    """Persistence = frequency × duration × coherence consistency."""
    buf = _PHANTOM_TEMPORAL_BUFFER.get(key, deque())
    if len(buf) < 2:
        return 0.0
    events = list(buf)
    span = events[-1]['ts'] - events[0]['ts']
    freq = len(events) / max(1.0, span / 60.0)   # events per minute
    avg_coh = sum(e['coherence'] for e in events) / len(events)
    return round(min(1.0, freq / 5.0) * min(1.0, span / 60.0) * avg_coh, 3)

def detect_phantom_ix(clusters_intel: List[Dict],
                       paths: Optional[List[Dict]] = None,
                       resolution_deg: float = 2.0) -> List[Dict]:
    """
    Phantom IX Detection Engine — find convergence attractors with no physical anchor.

    A Phantom IX is a geographic region where many ASN paths converge (high pull)
    but: no known IX within 300 km, no submarine cable nearby, high phase coherence,
    and latency geometry is inconsistent with physical routing.

    Returns candidates sorted by confidence descending.
    """
    if paths is None:
        paths = compute_inter_cluster_paths(clusters_intel)
    if not paths:
        return []

    total_paths = len(paths)
    midpoints   = _extract_path_midpoints(paths)
    cells       = _cluster_midpoints_by_grid(midpoints, resolution_deg)

    coherences       = [c.get('phase', {}).get('phase_coherence', 0)
                        for c in clusters_intel]
    global_coherence = sum(coherences) / max(1, len(coherences))

    phantoms: List[Dict] = []

    for cell_key, cell_paths in cells.items():
        if len(cell_paths) < 2:
            continue

        # Centroid of convergence region
        clat = sum(p['lat'] for p in cell_paths) / len(cell_paths)
        clon = sum(p['lon'] for p in cell_paths) / len(cell_paths)

        # Hard gate 1: must be >300 km from any known IX
        ix_dist = _nearest_known_ix_dist_km(clat, clon)
        if ix_dist < 300.0:
            continue

        # Hard gate 2: no cable alignment
        if find_nearby_cables(clat, clon, radius_km=200.0):
            continue

        # Compute soft signals
        phantom_pull  = round(len(cell_paths) / max(1, total_paths), 3)
        asn_entropy   = _compute_asn_entropy(cell_paths)
        synth_ratio   = sum(1 for p in cell_paths
                            if p.get('is_synthetic')) / max(1, len(cell_paths))
        local_scores  = [p.get('path_score', 0) for p in cell_paths]
        local_coh     = sum(local_scores) / max(1, len(local_scores))
        lat_ratios    = [_compute_latency_geometry_violation(p) for p in cell_paths]
        avg_lat_ratio = sum(lat_ratios) / max(1, len(lat_ratios))
        lat_violation = abs(avg_lat_ratio - 1.0) > 0.3
        convergent    = {asn for p in cell_paths for asn in p.get('hop_path', [])}

        if len(convergent) < 3 or phantom_pull < 0.04:
            continue
        # At least some signal quality — path scores OR cluster count
        if local_coh < 0.05 and len(cell_paths) < 3:
            continue

        _record_phantom_event(cell_key, clat, clon, local_coh, len(cell_paths))
        persistence   = _compute_persistence_score(cell_key)
        lat_viol_score = abs(avg_lat_ratio - 1.0) / max(0.1, avg_lat_ratio)

        confidence = (
            0.25 * min(1.0, phantom_pull * 4.0) +
            0.20 * min(1.0, asn_entropy / 3.0) +
            0.20 * synth_ratio +
            0.15 * min(1.0, lat_viol_score) +
            0.10 * min(1.0, len(convergent) / 8.0) +
            0.10 * persistence
        )
        if confidence < 0.15:
            continue

        phantom_type = _classify_phantom_type(cell_paths, avg_lat_ratio, ix_dist)
        phantom_id   = f"px_{abs(hash(cell_key)) % 9999:04d}"
        label = ('CONFIRMED_PHANTOM' if confidence >= 0.75 else
                 'PROBABLE_PHANTOM'  if confidence >= 0.50 else 'CANDIDATE')

        too_fast = avg_lat_ratio < 0.7
        too_slow = avg_lat_ratio > 1.5

        phantoms.append({
            'id':                   phantom_id,
            'cell_key':             cell_key,
            'lat':                  round(clat, 3),
            'lon':                  round(clon, 3),
            'confidence':           round(confidence, 3),
            'label':                label,
            'type':                 phantom_type,
            'phantom_pull':         phantom_pull,
            'asn_convergence_count': len(convergent),
            'convergent_asns':      sorted(str(a) for a in convergent)[:10],
            'synthetic_ratio':      round(synth_ratio, 3),
            'asn_entropy':          asn_entropy,
            'avg_latency_ratio':    round(avg_lat_ratio, 3),
            'latency_violation':    lat_violation,
            'latency_violation_type': (
                'TOO_FAST_CLOUD_FABRIC' if too_fast else
                'HEAVY_TUNNELING'       if avg_lat_ratio > 2.0 else
                'SUSPICIOUS_OVERLAY'    if too_slow else
                'NOMINAL'
            ),
            'nearest_ix_km':        round(ix_dist, 1),
            'path_count':           len(cell_paths),
            'persistence':          persistence,
            'is_infrastructure_grade': persistence > 0.3,
        })

    phantoms.sort(key=lambda p: p['confidence'], reverse=True)
    return phantoms[:20]


# ---------------------------------------------------------------------------
# Cyber-Physical Kill Chain Graph
# ---------------------------------------------------------------------------

def compute_kill_chain_correlation(clusters_intel: List[Dict],
                                    phantoms: List[Dict],
                                    paths: Optional[List[Dict]] = None) -> List[Dict]:
    """
    Cross-domain correlation: RF emitters + UAV activity + network routing.

    When a Phantom IX co-locates with RF emitters AND UAV activity AND synthetic
    routing → flag as coordination infrastructure (FULL_SPECTRUM_COORDINATION).

    Returns correlation events sorted by kill_chain_score descending.
    """
    if paths is None:
        paths = compute_inter_cluster_paths(clusters_intel)

    correlations: List[Dict] = []
    synth_paths = [p for p in paths if p.get('is_synthetic', False)]

    for phantom in phantoms:
        plat, plon = phantom['lat'], phantom['lon']
        radius_km  = 500.0

        nearby = []
        for cluster in clusters_intel:
            ctr  = cluster.get('centroid', [0, 0])
            dist = _geodistance_km(plat, plon, ctr[0], ctr[1])
            if dist <= radius_km:
                nearby.append({'cluster': cluster, 'distance_km': round(dist, 1)})

        if not nearby:
            continue

        net_score  = min(1.0, len(synth_paths) / max(1, len(paths))) * phantom['confidence']
        rf_clusters = [n for n in nearby if n['cluster'].get('rf_emitters', 0) > 0]
        rf_score    = min(1.0, len(rf_clusters) / max(1, len(nearby)))
        rf_count    = sum(n['cluster'].get('rf_emitters', 0) for n in rf_clusters)
        uav_clusters= [n for n in nearby if n['cluster'].get('uav_count', 0) > 0]
        uav_score   = min(1.0, len(uav_clusters) / max(1, len(nearby)))
        uav_count   = sum(n['cluster'].get('uav_count', 0) for n in uav_clusters)
        cohs        = [n['cluster'].get('phase', {}).get('phase_coherence', 0)
                       for n in nearby]
        avg_coh     = sum(cohs) / max(1, len(cohs))

        domains = sum([net_score > 0.25, rf_score > 0.15, uav_score > 0.10])

        kill_score = (
            0.35 * phantom['confidence'] +
            0.20 * net_score +
            0.20 * rf_score +
            0.15 * uav_score +
            0.10 * avg_coh
        )
        if kill_score < 0.12:
            continue

        if domains == 3:
            kc_type  = 'FULL_SPECTRUM_COORDINATION'
            kc_label = 'All domains aligned — coordination infrastructure detected'
        elif domains == 2 and rf_score > 0.25 and net_score > 0.25:
            kc_type  = 'RF_NETWORK_COUPLING'
            kc_label = 'RF emitters coordinating via synthetic network routing'
        elif domains == 2 and uav_score > 0.15:
            kc_type  = 'UAV_NETWORK_COUPLING'
            kc_label = 'UAV command traffic correlated with phantom routing'
        elif net_score > 0.4:
            kc_type  = 'NETWORK_ONLY'
            kc_label = 'Network-layer phantom — no physical domain coupling yet'
        else:
            kc_type  = 'PARTIAL_CORRELATION'
            kc_label = 'Partial cross-domain signal alignment'

        correlations.append({
            'phantom_id':         phantom['id'],
            'phantom_lat':        plat,
            'phantom_lon':        plon,
            'phantom_type':       phantom['type'],
            'kill_chain_score':   round(kill_score, 3),
            'kill_chain_type':    kc_type,
            'kill_chain_label':   kc_label,
            'domains_active':     domains,
            'net_score':          round(net_score, 3),
            'rf_score':           round(rf_score, 3),
            'uav_score':          round(uav_score, 3),
            'avg_coherence':      round(avg_coh, 3),
            'rf_emitter_count':   rf_count,
            'uav_count':          uav_count,
            'nearby_cluster_count': len(nearby),
            'nearby_clusters':    [
                {
                    'id':           n['cluster']['id'],
                    'distance_km':  n['distance_km'],
                    'behavior':     n['cluster'].get('behavior_type', 'UNKNOWN'),
                    'threat_score': n['cluster'].get('threat_score', 0),
                }
                for n in nearby[:5]
            ],
            'associated_asns': phantom.get('convergent_asns', [])[:5],
        })

    correlations.sort(key=lambda c: c['kill_chain_score'], reverse=True)
    return correlations


def phantom_ix_snapshot(clusters_intel: List[Dict],
                          paths: Optional[List[Dict]] = None) -> Dict:
    """Full Phantom IX Detection + Kill Chain Graph snapshot for API."""
    if paths is None:
        paths = compute_inter_cluster_paths(clusters_intel)

    phantoms   = detect_phantom_ix(clusters_intel, paths)
    kill_chain = compute_kill_chain_correlation(clusters_intel, phantoms, paths)

    confirmed    = [p for p in phantoms if p['label'] == 'CONFIRMED_PHANTOM']
    probable     = [p for p in phantoms if p['label'] == 'PROBABLE_PHANTOM']
    type_counts: Dict[str, int] = {}
    for p in phantoms:
        type_counts[p['type']] = type_counts.get(p['type'], 0) + 1

    full_spectrum = [k for k in kill_chain
                     if k['kill_chain_type'] == 'FULL_SPECTRUM_COORDINATION']

    return {
        'phantoms':           phantoms[:15],
        'phantom_count':      len(phantoms),
        'confirmed_count':    len(confirmed),
        'probable_count':     len(probable),
        'type_distribution':  type_counts,
        'kill_chain':         kill_chain[:10],
        'full_spectrum_count': len(full_spectrum),
        'summary': {
            'total_phantoms':       len(phantoms),
            'confirmed':            len(confirmed),
            'probable':             len(probable),
            'max_confidence':       round(max((p['confidence'] for p in phantoms), default=0), 3),
            'max_pull':             round(max((p['phantom_pull'] for p in phantoms), default=0), 3),
            'kill_chain_events':    len(kill_chain),
            'full_spectrum_events': len(full_spectrum),
            'cloud_fabric_count':   type_counts.get('CLOUD_FABRIC_NODE', 0),
            'relay_mesh_count':     type_counts.get('RELAY_MESH_HUB', 0),
            'ephemeral_swarm_count': type_counts.get('EPHEMERAL_SWARM', 0),
        },
    }


# ── Peering Intent Classifier ─────────────────────────────────────────────────
_PEERING_INTENT_HISTORY: Dict[str, deque] = {}

def infer_peering_intent(ix_heats: List[Dict],
                          paths:    List[Dict],
                          clusters: List[Dict]) -> List[Dict]:
    """
    Classify routing behavior changes as strategic intents:
    PEERING_DISPUTE, TRAFFIC_SHAPING, CENSORSHIP_BYPASS,
    BOTNET_REBALANCE, CLOUD_FAILOVER.

    Uses: IX heat velocity, ASN churn, path divergence, synthetic ratio.
    """
    intents = []
    now = time.time()

    for ix in ix_heats:
        ix_id  = ix.get('id', ix.get('name', ''))
        heat   = ix.get('heat', 0.0)
        vel    = ix.get('heat_velocity', 0.0)
        accel  = ix.get('heat_acceleration', 0.0)
        asn_div = ix.get('asn_diversity', 0)
        lat    = ix.get('lat', 0.0)
        lon    = ix.get('lon', 0.0)

        # Update history
        hist = _PEERING_INTENT_HISTORY.setdefault(ix_id, deque(maxlen=20))
        hist.append({'ts': now, 'heat': heat, 'vel': vel})

        # Gather nearby paths
        nearby_paths = [p for p in paths
                        if abs(p.get('lat', 0) - lat) < 5 and
                           abs(p.get('lon', 0) - lon) < 5]
        synth_ratio = (sum(1 for p in nearby_paths if p.get('is_synthetic'))
                       / max(1, len(nearby_paths)))
        path_flip_rate = sum(1 for p in nearby_paths
                             if p.get('path_instability', 0) > 0.5) / max(1, len(nearby_paths))
        asn_churn = ix.get('asn_churn', 0.0)

        if heat < 0.1 and vel < 0.05:
            continue

        # Classify intent
        score_dispute    = heat * 0.3 + vel * 0.4 + path_flip_rate * 0.3
        score_shaping    = heat * 0.4 + (1 - synth_ratio) * 0.3 + asn_churn * 0.3
        score_censorship = synth_ratio * 0.5 + heat * 0.2 + path_flip_rate * 0.3
        score_botnet     = asn_churn * 0.4 + vel * 0.3 + heat * 0.3
        score_failover   = accel * 0.5 + asn_churn * 0.3 + heat * 0.2

        scores = {
            'PEERING_DISPUTE':   round(score_dispute,    3),
            'TRAFFIC_SHAPING':   round(score_shaping,    3),
            'CENSORSHIP_BYPASS': round(score_censorship, 3),
            'BOTNET_REBALANCE':  round(score_botnet,     3),
            'CLOUD_FAILOVER':    round(score_failover,   3),
        }
        best_intent = max(scores, key=lambda k: scores[k])
        best_score  = scores[best_intent]

        if best_score < 0.15:
            continue

        intents.append({
            'ix_id':       ix_id,
            'lat':         lat,
            'lon':         lon,
            'intent':      best_intent,
            'confidence':  best_score,
            'scores':      scores,
            'heat':        heat,
            'heat_velocity': vel,
            'synth_ratio': round(synth_ratio, 3),
            'path_flip_rate': round(path_flip_rate, 3),
        })

    intents.sort(key=lambda x: x['confidence'], reverse=True)
    return intents


# ── Pre-Kill Chain Emergence Detector ─────────────────────────────────────────
_KC_SCORE_HISTORY: Dict[str, deque] = {}

def detect_emergent_kill_chain(clusters: List[Dict],
                                phantoms:  List[Dict],
                                paths:     List[Dict]) -> List[Dict]:
    """
    Detect EMERGENT_KILL_CHAIN: partial alignment trajectories whose score slope
    is rising rapidly — flagging coordination BEFORE it reaches FULL_SPECTRUM.

    Score = w1*RF + w2*UAV + w3*ASN + w4*coherence + w5*phantom_presence
    Flag when: current_score > 0.3 AND slope > 0.05/interval
    """
    import time as _time
    now = _time.time()
    emergent = []

    for cluster in clusters:
        cid   = cluster.get('cluster_id', cluster.get('id', ''))
        lat   = cluster.get('lat', cluster.get('centroid_lat', 0.0))
        lon   = cluster.get('lon', cluster.get('centroid_lon', 0.0))

        # Domain scores
        rf_score  = min(1.0, cluster.get('rf_energy', 0) * 0.4 +
                              cluster.get('frequency_count', 0) * 0.05)
        uav_score = min(1.0, cluster.get('uav_count', 0) * 0.15 +
                              cluster.get('mobile_count', 0) * 0.05)
        asn_score = min(1.0, cluster.get('asn_diversity', 0) * 0.1 +
                              cluster.get('path_count', 0) * 0.02)
        coh_score = float(cluster.get('phase_coherence', cluster.get('coherence', 0.0)))

        # Phantom presence within 500km
        def _haversine_km(lat1, lon1, lat2, lon2):
            import math
            R, dlat, dlon = 6371, math.radians(lat2 - lat1), math.radians(lon2 - lon1)
            a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
            return R * 2 * math.asin(min(1, a**0.5))

        phantom_near = any(_haversine_km(lat, lon, px.get('lat', 0), px.get('lon', 0)) < 500
                           for px in phantoms)
        ph_score = 0.8 if phantom_near else 0.0

        composite = (0.25 * rf_score + 0.20 * uav_score + 0.20 * asn_score +
                     0.20 * coh_score + 0.15 * ph_score)

        hist = _KC_SCORE_HISTORY.setdefault(cid, deque(maxlen=10))
        hist.append({'ts': now, 'score': composite})

        if len(hist) >= 3:
            scores_list = [e['score'] for e in hist]
            slope = (scores_list[-1] - scores_list[0]) / max(0.001, len(scores_list) - 1)
        else:
            slope = 0.0

        if composite > 0.3 and slope > 0.02:
            emergent.append({
                'cluster_id':    cid,
                'lat':           lat,
                'lon':           lon,
                'composite_score': round(composite, 3),
                'score_slope':   round(slope, 4),
                'rf_score':      round(rf_score, 3),
                'uav_score':     round(uav_score, 3),
                'asn_score':     round(asn_score, 3),
                'coherence':     round(coh_score, 3),
                'phantom_near':  phantom_near,
                'stage':         ('IMMINENT' if composite > 0.65
                                  else 'ESCALATING' if composite > 0.45
                                  else 'EMERGING'),
            })

    emergent.sort(key=lambda x: x['composite_score'], reverse=True)
    return emergent


# ── Dual Reality Divergence Model ─────────────────────────────────────────────
def compute_dual_reality_divergence(paths: List[Dict],
                                     cables: Optional[List[Dict]] = None) -> Dict:
    """
    Physical graph (cables/IX geography) vs Fabric graph (ASN paths/overlays).
    Divergence = how far actual routing departs from physical infrastructure.

    Returns a divergence map with per-path scores and aggregate statistics.
    """
    if not paths:
        return {'entries': [], 'stats': {}, 'global_divergence': 0.0}

    entries = []
    for p in paths:
        dist_km = p.get('distance_km', 0)
        hops    = max(1, p.get('hop_count', 3))
        synth   = p.get('is_synthetic', False)
        lat_r   = p.get('latency_ratio', 1.0)

        # Physical score: lower for synthetic paths, normalized by geography
        cable_aligned = p.get('cable_aligned', not synth)
        physical_score = (1.0 if cable_aligned else 0.2) * max(0.1, 1.0 - abs(lat_r - 1.0))

        # Fabric score: higher for synthetic/overlay paths
        synth_bonus  = 0.6 if synth else 0.0
        overlay_bias = min(1.0, max(0.0, lat_r - 1.0) * 0.5)
        fabric_score = min(1.0, physical_score * 0.3 + synth_bonus + overlay_bias)

        divergence = abs(fabric_score - physical_score)

        if divergence < 0.15:
            tier = 'NORMAL'
        elif divergence < 0.40:
            tier = 'CDN_CLOUD'
        elif divergence < 0.65:
            tier = 'OVERLAY_VPN'
        else:
            tier = 'COVERT_COORDINATION'

        entries.append({
            'path_id':       p.get('path_id', p.get('id', '')),
            'lat':           p.get('lat', 0.0),
            'lon':           p.get('lon', 0.0),
            'physical_score':  round(physical_score, 3),
            'fabric_score':    round(fabric_score, 3),
            'divergence':      round(divergence, 3),
            'tier':            tier,
        })

    # Aggregate stats
    divs = [e['divergence'] for e in entries]
    global_div = sum(divs) / max(1, len(divs))
    tier_counts: Dict[str, int] = {}
    for e in entries:
        tier_counts[e['tier']] = tier_counts.get(e['tier'], 0) + 1

    return {
        'entries':          entries,
        'stats':            tier_counts,
        'global_divergence': round(global_div, 3),
        'path_count':        len(entries),
    }


# ---------------------------------------------------------------------------
# Quick self-test
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import json

    sample_nodes = [
        {'id': f'n{i}', 'kind': 'rf_emitter',
         'position': [34.05 + (i % 3) * 0.1, -118.25 + (i % 5) * 0.1],
         'labels':   {'obs_class': 'observed', 'confidence': 0.8 + i * 0.01},
         'metadata': {'rssi': -50 - i * 2, 'asn': 'AS12345'},
         'updated_at': time.time()}
        for i in range(20)
    ] + [
        {'id': f'm{i}', 'kind': 'drone',
         'position': [29.76 + i * 0.05, -95.37 + i * 0.03],
         'labels':   {'obs_class': 'inferred', 'confidence': 0.65},
         'metadata': {'asn': 'AS4134'},
         'updated_at': time.time()}
        for i in range(8)
    ]

    clusters = detect_clusters(sample_nodes, [])
    for c in clusters:
        print(json.dumps(c.to_dict(), indent=2))
    print(f"\n{len(clusters)} clusters detected")
    print("\nSample CoT:")
    if clusters:
        print(cluster_to_cot(clusters[0]).decode())
