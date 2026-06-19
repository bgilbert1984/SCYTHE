"""
geo_inference.py — RTT-Constrained Spatial Solver

Derives geographic coordinates from signal physics + topology.
No external GeoIP required — geography emerges from constraints:

  Inputs:  RTT measurements, ASN relationships, graph topology, temporal behavior
  Outputs: lat/lon estimates with confidence gradients

Inference methods (in order of accuracy):
  1. RTT trilateration  — least-squares solve from multi-anchor RTT + speed-of-light
  2. ASN centroid       — geographic centroid of known ASN address-space
  3. Neighbor centroid  — confidence-weighted centroid of connected known nodes
  4. Temporal inertia   — smooth previous position toward new estimate

Physics constants match fusion_engine.py:
  _FIBER_FACTOR = 0.66c    — fiber propagation speed (fraction of c)
  _MAX_KM_PER_MS = 100.0   — hard upper bound on achievable km/ms one-way
  _MIN_KM_PER_MS = 50.0    — lower bound (conservative; accounts for routing overhead)
"""

from __future__ import annotations

import math
import time
import logging
from typing import Optional

import numpy as np
from scipy.optimize import least_squares, differential_evolution

logger = logging.getLogger(__name__)

# ── Physics ──────────────────────────────────────────────────────────────────
_C_KM_PER_S       = 299_792.458          # speed of light km/s
_FIBER_FACTOR     = 0.66                 # fiber propagation ≈ 0.66c
_FIBER_KM_PER_MS  = _C_KM_PER_S * _FIBER_FACTOR / 1000.0   # ≈ 197.7 km/ms one-way
_MAX_KM_PER_OW_MS = _FIBER_KM_PER_MS    # physical ceiling (one-way)
_RTT_KM_PER_MS    = 50.0                # conservative one-way km/ms (includes routing overhead)

# ── Earth constants ──────────────────────────────────────────────────────────
_EARTH_R_KM = 6_371.0


# ── Coordinate helpers ────────────────────────────────────────────────────────

def _to_ecef(lat_deg: float, lon_deg: float, alt_km: float = 0.0) -> np.ndarray:
    """WGS84 lat/lon/alt → ECEF XYZ (km)."""
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    r   = _EARTH_R_KM + alt_km
    return np.array([
        r * math.cos(lat) * math.cos(lon),
        r * math.cos(lat) * math.sin(lon),
        r * math.sin(lat)
    ])


def _from_ecef(xyz: np.ndarray) -> tuple[float, float]:
    """ECEF XYZ (km) → (lat_deg, lon_deg)."""
    x, y, z = xyz
    lon = math.degrees(math.atan2(y, x))
    hyp = math.hypot(x, y)
    lat = math.degrees(math.atan2(z, hyp))
    return lat, lon


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km."""
    phi1, phi2  = math.radians(lat1), math.radians(lat2)
    dphi        = math.radians(lat2 - lat1)
    dlambda     = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * _EARTH_R_KM * math.asin(min(1.0, math.sqrt(a)))


# ── Physics constraint checker ────────────────────────────────────────────────

def rtt_max_distance_km(rtt_ms: float) -> float:
    """Physical upper bound on one-way distance given an RTT measurement.

    Uses fiber propagation speed (0.66c). One-way = RTT / 2.
    Anything beyond this limit is physically impossible without relaying.
    """
    return (rtt_ms / 2.0) * _MAX_KM_PER_OW_MS


def rtt_estimate_distance_km(rtt_ms: float) -> float:
    """Conservative one-way distance estimate accounting for routing overhead."""
    return (rtt_ms / 2.0) * _RTT_KM_PER_MS


def is_physically_plausible(anchor_lat: float, anchor_lon: float,
                             node_lat: float, node_lon: float,
                             rtt_ms: float, tolerance: float = 1.15) -> bool:
    """Return True if the measured RTT is physically consistent with the distance.

    tolerance > 1.0 adds slack for routing overhead (default 15% margin).
    Returns False → likely relay chain, VPN, or spoofed topology.
    """
    actual_km = haversine_km(anchor_lat, anchor_lon, node_lat, node_lon)
    max_km    = rtt_max_distance_km(rtt_ms) * tolerance
    return actual_km <= max_km


def detect_relay_anomaly(anchor_lat: float, anchor_lon: float,
                          rtt_ms: float, candidate_lat: float, candidate_lon: float,
                          tolerance: float = 1.15) -> dict:
    """Classify a (distance, RTT) pair as plausible, suspect, or impossible.

    Returns:
        {
          'plausible': bool,
          'actual_km': float,
          'max_km': float,
          'ratio': float,          # actual / max (>1 = anomalous)
          'anomaly': str | None    # 'relay_chain' | 'vpn_tunnel' | 'spoofed_topology'
        }
    """
    actual_km = haversine_km(anchor_lat, anchor_lon, candidate_lat, candidate_lon)
    max_km    = rtt_max_distance_km(rtt_ms) * tolerance
    ratio     = actual_km / max(max_km, 1.0)
    plausible = ratio <= 1.0

    anomaly = None
    if ratio > 2.5:
        anomaly = 'relay_chain'
    elif ratio > 1.5:
        anomaly = 'vpn_tunnel'
    elif not plausible:
        anomaly = 'spoofed_topology'

    return {
        'plausible':  plausible,
        'actual_km':  round(actual_km, 1),
        'max_km':     round(max_km, 1),
        'ratio':      round(ratio, 3),
        'anomaly':    anomaly
    }


# ── RTT Trilateration ─────────────────────────────────────────────────────────

class RTTTrilateration:
    """Least-squares RTT trilateration on a spherical Earth.

    Uses ECEF coordinates internally — avoids lat/lon singularities.
    Employs a two-pass approach:
      1. Differential evolution (global, avoids local minima)
      2. Levenberg-Marquardt refinement (local, fast convergence)

    Confidence is derived from residual quality + anchor count.
    """

    MIN_ANCHORS = 3

    def solve(self, anchors: list[dict], rtt_ms_list: list[float],
              fast: bool = False) -> Optional[dict]:
        """Solve for unknown node position.

        anchors: list of {'lat': float, 'lon': float} for geo-known anchors
        rtt_ms_list: RTT in ms from this node to each anchor (same order)
        fast: if True, skip global solver (faster but less reliable)

        Returns:
            {
              'lat': float, 'lon': float,
              'confidence': float,        # 0–1
              'method': str,              # 'rtt_trilateration'
              'residual_km': float,       # mean position error
              'anchor_count': int,
              'anomalies': list           # physically impossible anchors
            }
        or None if unsolvable.
        """
        if len(anchors) < self.MIN_ANCHORS or len(anchors) != len(rtt_ms_list):
            return None

        # Convert to ECEF + estimated distances
        a_ecef   = [_to_ecef(a['lat'], a['lon']) for a in anchors]
        d_km     = [rtt_estimate_distance_km(r) for r in rtt_ms_list]
        d_max_km = [rtt_max_distance_km(r) for r in rtt_ms_list]

        def residuals(p: np.ndarray) -> np.ndarray:
            return np.array([
                np.linalg.norm(p - ae) - dk
                for ae, dk in zip(a_ecef, d_km)
            ])

        x0 = np.mean(a_ecef, axis=0)

        if fast:
            result = least_squares(residuals, x0, method='lm', max_nfev=200)
        else:
            # Bounding box on Earth sphere for global search
            bounds = [(-_EARTH_R_KM * 1.01, _EARTH_R_KM * 1.01)] * 3
            try:
                de = differential_evolution(
                    lambda p: float(np.sum(residuals(p) ** 2)),
                    bounds, maxiter=300, tol=1e-4, seed=42, workers=1
                )
                result = least_squares(residuals, de.x, method='lm', max_nfev=400)
            except Exception:
                result = least_squares(residuals, x0, method='lm', max_nfev=400)

        if not result.success and result.cost > 1e8:
            return None

        # Project back to lat/lon, clamped to Earth surface
        xyz  = result.x
        xyz  = xyz / np.linalg.norm(xyz) * _EARTH_R_KM  # clamp to sphere
        lat, lon = _from_ecef(xyz)

        # Residual quality → confidence
        residual_km = float(np.sqrt(np.mean(result.fun ** 2)))
        conf = max(0.1, min(0.9, 1.0 - residual_km / max(sum(d_km) / len(d_km), 1.0)))
        # Scale confidence by anchor count
        anchor_bonus = min(0.1, (len(anchors) - self.MIN_ANCHORS) * 0.02)
        conf = min(0.9, conf + anchor_bonus)

        # Check which anchors are physically plausible at the solved position
        anomalies = []
        for i, (a, rtt) in enumerate(zip(anchors, rtt_ms_list)):
            chk = detect_relay_anomaly(a['lat'], a['lon'], rtt, lat, lon)
            if not chk['plausible']:
                anomalies.append({'anchor_idx': i, **chk})

        # Penalise confidence for anomalous anchors
        if anomalies:
            conf *= max(0.4, 1.0 - 0.15 * len(anomalies))

        return {
            'lat':          round(lat, 5),
            'lon':          round(lon, 5),
            'confidence':   round(conf, 3),
            'method':       'rtt_trilateration',
            'residual_km':  round(residual_km, 1),
            'anchor_count': len(anchors),
            'anomalies':    anomalies
        }


# ── Temporal Inertia Filter ───────────────────────────────────────────────────

class TemporalInertiaFilter:
    """Smooths position estimates over time using exponential moving average.

    Prevents nodes from "teleporting" when new inferences arrive.
    Confidence decays exponentially when no new data is observed.
    """

    def __init__(self, inertia: float = 0.8, decay_half_life_s: float = 300.0):
        """
        inertia         — weight of previous position (0 = no memory, 1 = static)
        decay_half_life — seconds until confidence halves if node is not updated
        """
        self._inertia   = inertia
        self._lambda    = math.log(2) / decay_half_life_s
        self._positions: dict[str, dict] = {}   # node_id → {lat, lon, conf, ts}

    def update(self, node_id: str, lat: float, lon: float,
               confidence: float) -> dict:
        """Apply inertia filter and return smoothed position + decayed confidence."""
        now = time.time()
        prev = self._positions.get(node_id)

        if prev is None:
            result = {'lat': lat, 'lon': lon, 'confidence': confidence, 'ts': now}
        else:
            age    = now - prev['ts']
            # Confidence decay
            c_prev = prev['confidence'] * math.exp(-self._lambda * age)
            # Exponential position smoothing (spherical lerp approximation)
            w_new  = 1.0 - self._inertia
            w_old  = self._inertia
            s_lat  = prev['lat'] * w_old + lat * w_new
            s_lon  = prev['lon'] * w_old + lon * w_new
            s_conf = min(1.0, c_prev * w_old + confidence * w_new)
            result = {'lat': round(s_lat, 5), 'lon': round(s_lon, 5),
                      'confidence': round(s_conf, 3), 'ts': now}

        self._positions[node_id] = result
        return result

    def decay(self, node_id: str) -> Optional[dict]:
        """Return current state with confidence decayed to present time, or None."""
        prev = self._positions.get(node_id)
        if prev is None:
            return None
        age   = time.time() - prev['ts']
        c_now = prev['confidence'] * math.exp(-self._lambda * age)
        return {**prev, 'confidence': round(c_now, 3), 'age_s': round(age, 1)}


class TemporalVelocityChecker:
    """Detects physically impossible node movement between position updates.

    A node can't move faster than ~300 km/s under any normal routing change.
    Values above the physical threshold indicate:
      - VPN endpoint flip (sudden geography change)
      - Anycast cluster rebalancing
      - Coordinate spoofing / injection

    Maintains per-node position history in memory only (not persisted).
    """

    # Classification thresholds (km/s)
    _THRESHOLD_RAPID    = 300.0    # aggressive relocation (anycast, CDN flip)
    _THRESHOLD_TELEPORT = 5_000.0  # clearly impossible (VPN, spoof)

    def __init__(self):
        self._history: dict[str, dict] = {}  # node_id → {lat, lon, ts}

    def check(self, node_id: str, lat: float, lon: float,
              ts: float | None = None) -> dict | None:
        """Record position and return anomaly dict if velocity is suspicious.

        Returns None if no prior position or movement is normal.
        Always updates internal history regardless of anomaly result.
        """
        now  = ts or time.time()
        prev = self._history.get(node_id)
        self._history[node_id] = {'lat': lat, 'lon': lon, 'ts': now}

        if prev is None:
            return None
        delta_t = now - prev['ts']
        if delta_t <= 0:
            return None

        dist_km    = haversine_km(prev['lat'], prev['lon'], lat, lon)
        vel_km_s   = dist_km / delta_t
        if vel_km_s < self._THRESHOLD_RAPID:
            return None

        anom_type = ('teleport_event'    if vel_km_s >= self._THRESHOLD_TELEPORT
                     else 'rapid_relocation')
        severity  = min(1.0, math.log2(max(vel_km_s / self._THRESHOLD_RAPID, 1.0)) / 4.0)

        return {
            'layer':        'temporal',
            'type':         anom_type,
            'velocity_km_s': round(vel_km_s, 1),
            'dist_km':       round(dist_km, 1),
            'delta_t_s':     round(delta_t, 2),
            'severity':      round(severity, 3)
        }


class TopologyViolationChecker:
    """Detects nodes whose claimed position is inconsistent with their graph neighborhood.

    If a node connects to 3+ peers whose centroid is >2000 km away, that's suspicious.
    Common causes:
      - Proxy / exit node claiming local geography
      - ASN geo-attribution error
      - Misconfigured reverse-path routing

    Thresholds tuned to allow realistic CDN / anycast spread without false positives.
    """

    _SUSPECT_KM    = 1_500.0    # flag as potentially misplaced
    _ANOMALOUS_KM  = 3_000.0    # clearly inconsistent
    _MIN_NEIGHBORS = 3          # need at least 3 neighbors for meaningful centroid

    def check(self, node_lat: float, node_lon: float,
              neighbor_coords: list[dict]) -> dict | None:
        """Return anomaly dict or None.

        neighbor_coords: list of {lat, lon, confidence} for connected nodes.
        """
        known = [n for n in neighbor_coords if n.get('lat') is not None]
        if len(known) < self._MIN_NEIGHBORS:
            return None

        weights    = [n.get('confidence', 0.5) for n in known]
        w_sum      = sum(weights) or 1.0
        c_lat      = sum(n['lat'] * w for n, w in zip(known, weights)) / w_sum
        c_lon      = sum(n['lon'] * w for n, w in zip(known, weights)) / w_sum
        offset_km  = haversine_km(node_lat, node_lon, c_lat, c_lon)

        if offset_km < self._SUSPECT_KM:
            return None

        severity = min(1.0, (offset_km - self._SUSPECT_KM) / self._ANOMALOUS_KM)
        anom_type = ('topology_mismatch' if offset_km >= self._ANOMALOUS_KM
                     else 'topology_suspect')

        return {
            'layer':        'topology',
            'type':         anom_type,
            'offset_km':    round(offset_km, 1),
            'centroid_lat': round(c_lat, 5),
            'centroid_lon': round(c_lon, 5),
            'neighbor_n':   len(known),
            'severity':     round(severity, 3)
        }


def check_triangle_inequality(rtt_ab: float, rtt_bc: float, rtt_ac: float,
                               tolerance: float = 1.25) -> dict | None:
    """Test whether RTT(A→C) ≤ RTT(A→B) + RTT(B→C) within tolerance.

    A violation means latency along the direct path exceeds the sum of two
    intermediate legs — physically impossible under additive delay models.
    Causes:
      - Artificial latency injection (QoS manipulation)
      - Relay distortion (measurements from different vantage points)
      - MPLS/GRE tunnel measurement corruption

    Returns anomaly dict or None.
    """
    expected_max = (rtt_ab + rtt_bc) * tolerance
    if rtt_ac <= expected_max:
        return None

    ratio    = rtt_ac / max(rtt_ab + rtt_bc, 0.001)
    severity = min(1.0, (ratio - 1.0) / 2.0)   # saturates at 3× violation

    return {
        'layer':    'triangle',
        'type':     'triangle_violation',
        'rtt_ac':   round(rtt_ac, 2),
        'expected': round(expected_max, 2),
        'ratio':    round(ratio, 3),
        'severity': round(severity, 3)
    }


# ── Anomaly Score Engine ──────────────────────────────────────────────────────

class AnomalyScoreEngine:
    """Fuses multi-layer constraint violations into a single anomaly score [0, 1].

    Each layer contributes a weighted severity. Layers present are weighted
    in proportion to the number of independent constraints they check, so
    a single physics violation doesn't overwhelm a clean topology signal.

    Classification:
        score < 0.20  → normal
        0.20 – 0.50   → noisy / suspect
        0.50 – 0.80   → suspicious
        > 0.80        → anomalous

    The `feedback_confidence()` method applies score → confidence penalty,
    making the geo inference loop self-correcting.
    """

    _WEIGHTS = {
        'physics':   0.35,
        'temporal':  0.25,
        'topology':  0.20,
        'triangle':  0.15,
        'asn':       0.05,   # reserved — stub until ASN hull engine built
    }

    # Score thresholds
    NORMAL     = 0.20
    NOISY      = 0.50
    SUSPICIOUS = 0.80

    def __init__(self):
        self._velocity = TemporalVelocityChecker()
        self._topology = TopologyViolationChecker()

    def score_violations(self, violations: list[dict]) -> float:
        """Compute fused anomaly score from a list of violation dicts."""
        if not violations:
            return 0.0
        total = 0.0
        for v in violations:
            w   = self._WEIGHTS.get(v.get('layer', ''), 0.05)
            sev = float(v.get('severity', 0.5))
            total += w * sev
        return round(min(1.0, total), 3)

    def classify(self, score: float) -> str:
        if score >= self.SUSPICIOUS:    return 'anomalous'
        if score >= self.NOISY:         return 'suspicious'
        if score >= self.NORMAL:        return 'noisy'
        return 'normal'

    def feedback_confidence(self, confidence: float, anomaly_score: float) -> float:
        """Degrade geo inference confidence by anomaly score.

        High anomaly scores indicate the position claim is unreliable.
        Penalty is non-linear — mild anomalies barely touch confidence,
        severe ones halve it.
        """
        if anomaly_score < self.NORMAL:
            return confidence
        penalty = anomaly_score ** 1.5   # non-linear: 0.5→0.35, 0.8→0.72
        return round(max(0.05, confidence * (1.0 - penalty * 0.6)), 3)

    def check_node(self, node_id: str,
                   lat: float, lon: float,
                   rtt_anchors: list[dict] | None = None,
                   neighbor_coords: list[dict] | None = None,
                   prior_lat: float | None = None,
                   prior_lon: float | None = None,
                   delta_t_s: float | None = None) -> dict:
        """Run all applicable constraint layers for one node.

        Returns:
            {
              'score':      float,        # 0–1 fused anomaly score
              'class':      str,          # normal/noisy/suspicious/anomalous
              'violations': list[dict],   # all triggered violations
              'severity_log2': float      # log2(max_ratio) if physics triggered
            }
        """
        violations = []

        # Layer 1 — Physics (RTT vs distance)
        if rtt_anchors:
            for a in rtt_anchors:
                chk = detect_relay_anomaly(a['lat'], a['lon'], a['rtt_ms'], lat, lon)
                if not chk['plausible']:
                    sev = min(1.0, math.log2(max(chk['ratio'], 1.001)))
                    violations.append({
                        'layer':    'physics',
                        'type':     chk.get('anomaly', 'physics_violation'),
                        'ratio':    chk['ratio'],
                        'dist_km':  chk['actual_km'],
                        'max_km':   chk['max_km'],
                        'severity': round(sev, 3)
                    })

        # Layer 2 — Temporal velocity
        if prior_lat is not None and delta_t_s is not None and delta_t_s > 0:
            dist_km  = haversine_km(prior_lat, prior_lon or 0.0, lat, lon)
            vel_km_s = dist_km / delta_t_s
            if vel_km_s >= TemporalVelocityChecker._THRESHOLD_RAPID:
                anom_type = ('teleport_event'
                             if vel_km_s >= TemporalVelocityChecker._THRESHOLD_TELEPORT
                             else 'rapid_relocation')
                sev = min(1.0, math.log2(
                    max(vel_km_s / TemporalVelocityChecker._THRESHOLD_RAPID, 1.0)) / 4.0)
                violations.append({
                    'layer':         'temporal',
                    'type':          anom_type,
                    'velocity_km_s': round(vel_km_s, 1),
                    'dist_km':       round(dist_km, 1),
                    'severity':      round(sev, 3)
                })

        # Layer 3 — Topology mismatch
        if neighbor_coords:
            topo = self._topology.check(lat, lon, neighbor_coords)
            if topo:
                violations.append(topo)

        # Layer 4 — Triangle inequality (check all anchor triples)
        if rtt_anchors and len(rtt_anchors) >= 3:
            anchors_with_rtt = rtt_anchors
            n = len(anchors_with_rtt)
            for i in range(n):
                for j in range(i + 1, n):
                    for k in range(j + 1, n):
                        # Estimate A→C via position
                        dist_ij = haversine_km(anchors_with_rtt[i]['lat'],
                                               anchors_with_rtt[i]['lon'],
                                               anchors_with_rtt[j]['lat'],
                                               anchors_with_rtt[j]['lon'])
                        dist_jk = haversine_km(anchors_with_rtt[j]['lat'],
                                               anchors_with_rtt[j]['lon'],
                                               anchors_with_rtt[k]['lat'],
                                               anchors_with_rtt[k]['lon'])
                        dist_ik = haversine_km(anchors_with_rtt[i]['lat'],
                                               anchors_with_rtt[i]['lon'],
                                               anchors_with_rtt[k]['lat'],
                                               anchors_with_rtt[k]['lon'])
                        # Map distances to pseudo-RTTs for triangle check
                        rtt_ij = dist_ij / _RTT_KM_PER_MS
                        rtt_jk = dist_jk / _RTT_KM_PER_MS
                        rtt_ik = dist_ik / _RTT_KM_PER_MS
                        tri = check_triangle_inequality(rtt_ij, rtt_jk, rtt_ik)
                        if tri:
                            violations.append(tri)
                            break  # one triangle violation per node is enough
                    else:
                        continue
                    break

        score = self.score_violations(violations)
        return {
            'score':      score,
            'class':      self.classify(score),
            'violations': violations,
        }


# ── Confidence Fusion Engine ──────────────────────────────────────────────────

class ConfidenceFusion:
    """Fuses multiple geo inference signals into a single confidence-weighted estimate.

    Combines:
      w_rtt      — RTT trilateration (highest accuracy)
      w_topology — neighbor centroid (graph topology)
      w_asn      — ASN centroid (coarse but reliable)
      w_temporal — temporal inertia (continuity)
    """

    # Default weights — sum should ≈ 1.0 but don't need to exactly
    WEIGHTS = {
        'rtt_trilateration':   0.45,
        'neighbor_inferred':   0.25,
        'asn_centroid':        0.15,
        'recon_fallback':      0.20,
        'observed':            1.00,   # ground truth — always wins
    }

    @classmethod
    def fuse(cls, candidates: list[dict]) -> Optional[dict]:
        """Merge multiple position estimates into one.

        candidates: list of dicts with {lat, lon, confidence, method}
        Returns best merged estimate or None.
        """
        if not candidates:
            return None

        # Ground truth short-circuits everything
        observed = [c for c in candidates if c.get('method') == 'observed']
        if observed:
            best = max(observed, key=lambda c: c['confidence'])
            return best

        # Weighted average by (method_weight × confidence)
        total_w = 0.0
        lat_acc = 0.0
        lon_acc = 0.0
        for c in candidates:
            mw = cls.WEIGHTS.get(c.get('method', ''), 0.1)
            w  = mw * float(c.get('confidence', 0.1))
            lat_acc += c['lat'] * w
            lon_acc += c['lon'] * w
            total_w += w

        if total_w < 1e-6:
            return None

        fused_lat  = lat_acc / total_w
        fused_lon  = lon_acc / total_w
        # Fused confidence = weighted mean of individual confidences, capped at 0.85
        fused_conf = min(0.85, sum(c['confidence'] * cls.WEIGHTS.get(c.get('method', ''), 0.1)
                                   for c in candidates) / len(candidates))

        methods = list({c.get('method', 'unknown') for c in candidates})

        return {
            'lat':        round(fused_lat, 5),
            'lon':        round(fused_lon, 5),
            'confidence': round(fused_conf, 3),
            'method':     'fused:' + '+'.join(sorted(methods)),
            'sources':    len(candidates)
        }


# ── Top-level inference coordinator ──────────────────────────────────────────

class GeoInferenceEngine:
    """Coordinates all inference methods for a single node.

    Typical usage (from server edge event handler):

        engine = GeoInferenceEngine()

        result = engine.infer(
            node_id   = 'PCAP-1.2.3.4',
            rtt_anchors = [
                {'lat': 37.7, 'lon': -122.4, 'rtt_ms': 22.4},
                {'lat': 51.5, 'lon': -0.12,  'rtt_ms': 148.1},
            ],
            neighbor_coords = [{'lat': 40.7, 'lon': -74.0, 'confidence': 0.8}],
            asn_centroid    = (37.3, -121.9),   # from GeoIP ASN mmdb
        )
        # result: {lat, lon, confidence, method, anomaly_score, anomaly_class}
    """

    def __init__(self):
        self._trilat  = RTTTrilateration()
        self._inertia = TemporalInertiaFilter()
        self._anomaly = AnomalyScoreEngine()

    def infer(self, node_id: str,
              rtt_anchors: list[dict] | None = None,
              neighbor_coords: list[dict] | None = None,
              asn_centroid: tuple[float, float] | None = None,
              fast: bool = True) -> Optional[dict]:
        """Run all available inference methods, score anomalies, fuse results.

        rtt_anchors    — list of {lat, lon, rtt_ms}
        neighbor_coords — list of {lat, lon, confidence}
        asn_centroid   — (lat, lon) tuple from ASN mmdb lookup

        Returns fused {lat, lon, confidence, method, anomaly_score, anomaly_class}
        or None if no data.
        """
        candidates = []

        # 1. RTT trilateration
        if rtt_anchors and len(rtt_anchors) >= RTTTrilateration.MIN_ANCHORS:
            anchors  = [{'lat': a['lat'], 'lon': a['lon']} for a in rtt_anchors]
            rtt_list = [a['rtt_ms'] for a in rtt_anchors]
            try:
                tri = self._trilat.solve(anchors, rtt_list, fast=fast)
                if tri:
                    candidates.append(tri)
            except Exception as e:
                logger.debug(f'[GeoInference] trilateration failed for {node_id}: {e}')

        # 2. Neighbor centroid
        if neighbor_coords and len(neighbor_coords) >= 2:
            weights = [c.get('confidence', 0.5) for c in neighbor_coords]
            w_sum   = sum(weights) or 1.0
            n_lat   = sum(c['lat'] * w for c, w in zip(neighbor_coords, weights)) / w_sum
            n_lon   = sum(c['lon'] * w for c, w in zip(neighbor_coords, weights)) / w_sum
            n_conf  = min(0.6, 0.3 + 0.1 * len(neighbor_coords))
            candidates.append({'lat': round(n_lat, 5), 'lon': round(n_lon, 5),
                                'confidence': n_conf, 'method': 'neighbor_inferred'})

        # 3. ASN centroid
        if asn_centroid:
            candidates.append({'lat': round(asn_centroid[0], 5),
                                'lon': round(asn_centroid[1], 5),
                                'confidence': 0.35, 'method': 'asn_centroid'})

        if not candidates:
            return None

        # Fuse all candidates
        fused = ConfidenceFusion.fuse(candidates)
        if fused is None:
            return None

        # Apply temporal inertia smoothing
        smoothed = self._inertia.update(node_id, fused['lat'], fused['lon'], fused['confidence'])
        lat  = smoothed['lat']
        lon  = smoothed['lon']
        conf = smoothed['confidence']

        # ── Multi-layer anomaly scoring ───────────────────────────────────────
        # Prior position from inertia filter for velocity check
        prior = self._inertia._positions.get(node_id)
        prior_lat = prior['lat'] if prior else None
        prior_lon = prior['lon'] if prior else None
        delta_t   = (time.time() - prior['ts']) if prior else None

        anomaly_result = self._anomaly.check_node(
            node_id,
            lat, lon,
            rtt_anchors     = rtt_anchors,
            neighbor_coords = neighbor_coords,
            prior_lat       = prior_lat,
            prior_lon       = prior_lon,
            delta_t_s       = delta_t
        )
        anom_score = anomaly_result['score']
        anom_class = anomaly_result['class']

        # Feed anomaly score back into confidence — self-correcting inference
        conf = self._anomaly.feedback_confidence(conf, anom_score)

        return {
            **fused,
            'lat':          lat,
            'lon':          lon,
            'confidence':   conf,
            'anomaly_score':  anom_score,
            'anomaly_class':  anom_class,
            'violations':     anomaly_result['violations']
        }

    def check_path_anomalies(self, hops: list[dict]) -> list[dict]:
        """Scan a traceroute hop list for physically impossible RTT segments.

        hops: list of {lat, lon, rtt_ms} in path order.
        Returns list of anomaly dicts for suspicious hops.
        """
        anomalies = []
        for i in range(1, len(hops)):
            prev, curr = hops[i - 1], hops[i]
            if not all(k in prev and k in curr for k in ('lat', 'lon', 'rtt_ms')):
                continue
            delta_rtt = curr['rtt_ms'] - prev['rtt_ms']
            if delta_rtt <= 0:
                continue
            chk = detect_relay_anomaly(prev['lat'], prev['lon'], delta_rtt,
                                        curr['lat'], curr['lon'])
            if not chk['plausible']:
                anomalies.append({'hop': i, 'from': i - 1, **chk})
        return anomalies
