"""
rf_field_generator.py — Temporal Field Predictor + RF Field Tensor Generation

Architecture:
    emitters(t-3, t-2, t-1, t)
        → TemporalFieldPredictor
            → predicted_emitters(t+1 … t+N)   [orange ghost layer]
        → RFFieldGenerator
            → (field_tensor, prediction_tensor)  [128×128 float32 numpy arrays]
                → /api/rf/field HTTP endpoint
                → socketio rf_field_update push

Integrates with the GraphEventBus drain queue: rf_node events flow into
EmitterHistory automatically via subscribe_to_bus().

Color semantics (enforced by globe shader):
    fieldTex    → blue/cyan   (real current signal)
    predictionTex → orange    (predicted future signal)
"""

from __future__ import annotations

import time
import threading
import logging
from collections import deque
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# EmitterHistory
# ---------------------------------------------------------------------------

class EmitterHistory:
    """
    Maintains a sliding window of position+power snapshots per emitter entity.

    Thread-safe. Fed directly from the GraphEventBus drain queue.
    """

    MAX_HISTORY = 5  # positions to retain per emitter

    def __init__(self) -> None:
        self._lock: threading.Lock = threading.Lock()
        self._history: Dict[str, deque] = {}

    def update(
        self,
        entity_id: str,
        lat: float,
        lon: float,
        power: float = -70.0,
        confidence: float = 0.5,
        anomaly_score: float = 0.0,
        freq_mhz: float = 0.0,
        alt_m: float = 0.0,
    ) -> None:
        """Record a new snapshot for the given emitter."""
        snap = {
            'lat': lat,
            'lon': lon,
            'alt_m': alt_m,
            'power': power,
            'confidence': confidence,
            'anomaly_score': anomaly_score,
            'freq_mhz': freq_mhz,
            'ts': time.monotonic(),
        }
        with self._lock:
            if entity_id not in self._history:
                self._history[entity_id] = deque(maxlen=self.MAX_HISTORY)
            self._history[entity_id].append(snap)

    def get_emitters(self) -> List[dict]:
        """Return latest state + history for every tracked emitter."""
        result = []
        with self._lock:
            for eid, hist in self._history.items():
                if not hist:
                    continue
                latest = hist[-1]
                result.append({
                    'id': eid,
                    'lat': latest['lat'],
                    'lon': latest['lon'],
                    'alt_m': latest.get('alt_m', 0.0),
                    'power': latest['power'],
                    'confidence': latest['confidence'],
                    'anomaly_score': latest['anomaly_score'],
                    'freq_mhz': latest['freq_mhz'],
                    'history': list(hist),
                    'predicted': False,
                })
        return result

    def subscribe_to_bus(self, graph_event_bus) -> None:
        """
        Register a GraphEventBus subscriber that feeds rf_node events into
        EmitterHistory.  Safe to call multiple times (bus deduplicates).
        """
        def _on_event(ev):
            try:
                kind = (getattr(ev, 'entity_kind', '') or getattr(ev, 'entity_type', '') or '').lower()
                etype = (getattr(ev, 'event_type', '') or getattr(ev, 'type', '') or '').upper()
                if 'rf' not in kind and etype not in ('RF_NODE', 'RF_SIGNAL', 'EMITTER_UPDATE'):
                    return
                eid  = getattr(ev, 'entity_id', '') or getattr(ev, 'id', '') or ''
                data = getattr(ev, 'entity_data', None) or getattr(ev, 'payload', {}) or {}
                loc  = data.get('location') or {}
                # Support position:[lat,lon,alt] array (RFHypergraphStore format)
                pos  = data.get('position') or []
                lat  = float(loc.get('lat') or data.get('lat') or (pos[0] if len(pos) > 0 else 0.0))
                lon  = float(loc.get('lon') or data.get('lon') or (pos[1] if len(pos) > 1 else 0.0))
                alt  = float(loc.get('alt') or data.get('alt_m') or data.get('alt') or (pos[2] if len(pos) > 2 else 0.0))
                if lat == 0.0 and lon == 0.0:
                    return
                pwr  = float(data.get('power') or data.get('signal_strength') or -70.0)
                conf = float(data.get('confidence') or data.get('weight') or 0.5)
                anom = float(data.get('anomaly_score') or 0.0)
                freq = float(data.get('freq_mhz') or data.get('frequency_mhz') or 0.0)
                self.update(eid, lat, lon, pwr, conf, anom, freq, alt_m=alt)
            except Exception:
                pass

        try:
            graph_event_bus.subscribe(_on_event)
            logger.info('[RFField] EmitterHistory subscribed to GraphEventBus')
        except Exception as exc:
            logger.warning('[RFField] EmitterHistory bus subscription failed: %s', exc)


# ---------------------------------------------------------------------------
# TemporalFieldPredictor
# ---------------------------------------------------------------------------

class TemporalFieldPredictor:
    """
    Projects emitter positions forward in time using linear velocity
    estimated from the last 2–3 position snapshots.

    Ghost emitters carry decayed power + confidence so they fade gracefully
    and do not dominate the field over live observations.
    """

    # Power decay per prediction step (multiplied per step: step1=0.6, step2=0.36 …)
    POWER_DECAY     = 0.60
    # Boost for anomalous nodes — their predicted footprint is amplified
    ANOMALY_BOOST   = 1.50
    ANOMALY_THRESH  = 0.80
    # How many steps forward to project
    PREDICT_STEPS   = 3
    STEP_DT         = 1.0  # seconds per step (matches typical 1-s event cadence)

    def _estimate_velocity(self, history: list) -> Tuple[float, float]:
        """Return (vx, vy) in degrees/second from the last two snapshots."""
        if len(history) < 2:
            return 0.0, 0.0
        a, b = history[-2], history[-1]
        dt = b['ts'] - a['ts']
        if dt <= 0.0:
            dt = self.STEP_DT
        return (b['lon'] - a['lon']) / dt, (b['lat'] - a['lat']) / dt

    def predict(self, emitters: List[dict]) -> List[dict]:
        """
        For each emitter with sufficient history, generate PREDICT_STEPS ghost
        positions projected forward.  Returns only the ghost (predicted) list.
        """
        predicted: List[dict] = []
        for e in emitters:
            hist = e.get('history', [])
            if len(hist) < 2:
                continue

            vx, vy = self._estimate_velocity(hist)
            base_power = e['power'] * e.get('confidence', 0.5)
            if e.get('anomaly_score', 0.0) > self.ANOMALY_THRESH:
                base_power *= self.ANOMALY_BOOST

            lat, lon = e['lat'], e['lon']
            for step in range(1, self.PREDICT_STEPS + 1):
                lat  += vy * self.STEP_DT
                lon  += vx * self.STEP_DT
                decay = self.POWER_DECAY ** step
                predicted.append({
                    'id':           f"{e['id']}_pred_{step}",
                    'lat':          lat,
                    'lon':          lon,
                    'power':        base_power * decay,
                    'confidence':   e.get('confidence', 0.5) * decay,
                    'anomaly_score': e.get('anomaly_score', 0.0),
                    'freq_mhz':     e.get('freq_mhz', 0.0),
                    'predicted':    True,
                    'step':         step,
                })
        return predicted


# ---------------------------------------------------------------------------
# RFFieldGenerator
# ---------------------------------------------------------------------------

class RFFieldGenerator:
    """
    Converts a list of (real + predicted) emitters into two float32 numpy
    tensors on a regular lat/lon grid using vectorized inverse-square
    propagation.

    Returns:
        field      — (H, W) float32, real-emitter intensity 0–1
        prediction — (H, W) float32, ghost-emitter intensity 0–1
    """

    DEFAULT_GRID = 128
    # Approximate 1° lat in km
    KM_PER_LAT = 111.0
    # Regularisation constant to avoid /0 at emitter centre
    EPS = 0.001

    def __init__(self, grid_size: int = DEFAULT_GRID) -> None:
        self.grid_size = grid_size

    def generate(
        self,
        real_emitters:      List[dict],
        predicted_emitters: Optional[List[dict]] = None,
        bounds: Optional[List[float]] = None,
    ) -> Tuple[np.ndarray, np.ndarray, List[float]]:
        """
        Args:
            real_emitters:      list of emitter dicts with lat/lon/power
            predicted_emitters: list of ghost emitters from TemporalFieldPredictor
            bounds:             [min_lon, min_lat, max_lon, max_lat]
                                defaults to ±180 / ±90 global view

        Returns:
            field      — (H, W) float32, 0–1 normalised
            prediction — (H, W) float32, 0–1 normalised
            bounds     — the bounds used (passed through or default)
        """
        if bounds is None:
            bounds = [-180.0, -90.0, 180.0, 90.0]
        if predicted_emitters is None:
            predicted_emitters = []

        min_lon, min_lat, max_lon, max_lat = bounds
        H = W = self.grid_size

        lats = np.linspace(min_lat, max_lat, H, dtype=np.float32)
        lons = np.linspace(min_lon, max_lon, W, dtype=np.float32)
        lon_grid, lat_grid = np.meshgrid(lons, lats)  # both (H, W)

        field      = self._accumulate(real_emitters,      lat_grid, lon_grid)
        prediction = self._accumulate(predicted_emitters, lat_grid, lon_grid)

        return field, prediction, bounds

    def generate_3d(
        self,
        real_emitters:      List[dict],
        predicted_emitters: Optional[List[dict]] = None,
        bounds:             Optional[List[float]] = None,
        grid_xy:            int = 64,
        grid_z:             int = 16,
    ) -> Tuple[np.ndarray, np.ndarray, List[float]]:
        """
        Build a (Z, H, W) float32 volumetric field tensor using true 3D
        inverse-square propagation (dxy² + dz²).

        Each emitter contributes to every (lat, lon, alt) voxel based on full
        3D distance — signals fall off with altitude, not just horizontally.
        Emitter altitude is read from 'alt_m' key (defaults to 0 = ground).

        Both tensors are normalised against a shared combined max so relative
        real vs predicted amplitudes are preserved.

        Data layout: (D=grid_z, H=grid_xy, W=grid_xy), C-order.
        Texture coord mapping: v=0 → min_lat, v=1 → max_lat (no Y-flip needed;
        the GLSL shader uses the same ascending-lat formula).

        Returns:
            field_3d      — (grid_z, grid_xy, grid_xy) float32, 0–1
            prediction_3d — (grid_z, grid_xy, grid_xy) float32, 0–1
            bounds        — [min_lon, min_lat, max_lon, max_lat] used
        """
        if bounds is None:
            bounds = [-180.0, -90.0, 180.0, 90.0]
        if predicted_emitters is None:
            predicted_emitters = []

        min_lon, min_lat, max_lon, max_lat = bounds
        lats     = np.linspace(min_lat, max_lat, grid_xy, dtype=np.float32)
        lons     = np.linspace(min_lon, max_lon, grid_xy, dtype=np.float32)
        lon_grid, lat_grid = np.meshgrid(lons, lats)  # both (H, W)
        altitudes = np.linspace(0.0, 20_000.0, grid_z, dtype=np.float32)  # metres

        field_3d = self._accumulate_3d(real_emitters,      lat_grid, lon_grid, altitudes)
        pred_3d  = self._accumulate_3d(predicted_emitters, lat_grid, lon_grid, altitudes)

        # Shared normalisation — preserves relative real vs predicted amplitudes
        combined_max = max(field_3d.max(), pred_3d.max())
        if combined_max > 0.0:
            field_3d /= combined_max
            pred_3d  /= combined_max

        return field_3d, pred_3d, bounds

    def _accumulate_3d(
        self,
        emitters:  List[dict],
        lat_grid:  np.ndarray,
        lon_grid:  np.ndarray,
        altitudes: np.ndarray,
    ) -> np.ndarray:
        """
        True volumetric inverse-square accumulation.

        For each emitter, horizontal dist² (H,W) and vertical dist² (Z,) are
        broadcast together to (Z, H, W) without a Python Z-loop per emitter.

        EPS is derived from the altitude grid step (half-step²) so ground-level
        emitters don't dominate every altitude slice after shared-max normalisation.
        EPS_XY (self.EPS) is kept small for horizontal; eps_3d is the combined floor.
        """
        Z = len(altitudes)
        H, W = lat_grid.shape
        out = np.zeros((Z, H, W), dtype=np.float32)

        # Altitude-aware EPS: half the altitude step size, squared (km²).
        # Prevents the ground voxel from being ~1000× brighter than altitude slices.
        if Z > 1:
            alt_step_km  = float(altitudes[-1] - altitudes[0]) / (Z - 1) / 1_000.0
            eps_3d = max(self.EPS, (alt_step_km * 0.5) ** 2)
        else:
            eps_3d = self.EPS

        for e in emitters:
            lat = e.get('lat')
            lon = e.get('lon')
            pwr = float(e.get('power') or 0.0)
            if lat is None or lon is None:
                continue
            if pwr < 0:
                pwr = max(0.0, pwr + 130.0)
            if pwr == 0.0:
                continue

            e_alt_m = float(e.get('alt_m') or 0.0)

            # Horizontal planar distance² in km²  (H, W)
            dlat_km = (lat_grid - lat) * self.KM_PER_LAT
            dlon_km = (lon_grid - lon) * self.KM_PER_LAT * np.cos(np.radians(lat))
            dxy2 = dlat_km ** 2 + dlon_km ** 2          # (H, W)

            # Vertical distance² in km²  (Z,)
            dz_km = (altitudes - e_alt_m) / 1_000.0
            dz2   = dz_km ** 2                           # (Z,)

            # Broadcast → (Z, H, W)
            dist2 = dxy2[np.newaxis, :, :] + dz2[:, np.newaxis, np.newaxis]
            out  += pwr / (dist2 + eps_3d)

        return out

    def _accumulate(self, emitters: List[dict], lat_grid: np.ndarray, lon_grid: np.ndarray) -> np.ndarray:
        """Vectorized inverse-square sum over all emitters, normalised to [0,1]."""
        out = np.zeros(lat_grid.shape, dtype=np.float32)
        for e in emitters:
            lat = e.get('lat')
            lon = e.get('lon')
            pwr = float(e.get('power') or 0.0)
            if lat is None or lon is None:
                continue
            # Convert dBm to linear scale for the field (add 130 so -130 dBm → 0)
            if pwr < 0:
                pwr = max(0.0, pwr + 130.0)
            if pwr == 0.0:
                continue

            # Approximate planar distance in km
            dlat = (lat_grid - lat) * self.KM_PER_LAT
            dlon = (lon_grid - lon) * self.KM_PER_LAT * np.cos(np.radians(lat))
            dist2 = dlat * dlat + dlon * dlon
            out += pwr / (dist2 + self.EPS)

        if out.max() > 0.0:
            out = out / out.max()
        return out


# ---------------------------------------------------------------------------
# Singleton instances (used by the server endpoint + bus subscriber)
# ---------------------------------------------------------------------------

_emitter_history   = EmitterHistory()
_field_predictor   = TemporalFieldPredictor()
_field_generator   = RFFieldGenerator(grid_size=128)


def get_field_snapshot(bounds: Optional[List[float]] = None):
    """
    Convenience function called by the Flask endpoint and Socket.IO push.

    Returns a dict ready for JSON serialisation:
        {
            field:       [[...], ...],   # H×W float32 as nested list
            prediction:  [[...], ...],
            bounds:      [min_lon, min_lat, max_lon, max_lat],
            real_emitters:      [...],
            predicted_emitters: [...],
            grid_size:   128,
            timestamp:   float,
        }
    """
    real_emitters  = _emitter_history.get_emitters()
    ghost_emitters = _field_predictor.predict(real_emitters)
    field, prediction, used_bounds = _field_generator.generate(
        real_emitters,
        ghost_emitters,
        bounds=bounds,
    )
    return {
        'field':               field.tolist(),
        'prediction':          prediction.tolist(),
        'bounds':              used_bounds,
        'real_emitters':       [
            {'id': e['id'], 'lat': e['lat'], 'lon': e['lon'],
             'power': e['power'], 'confidence': e['confidence'],
             'anomaly_score': e['anomaly_score']}
            for e in real_emitters
        ],
        'predicted_emitters':  [
            {'id': e['id'], 'lat': e['lat'], 'lon': e['lon'],
             'power': e['power'], 'step': e['step']}
            for e in ghost_emitters
        ],
        'grid_size':           _field_generator.grid_size,
        'timestamp':           time.time(),
    }


def get_field3d_snapshot(
    bounds:  Optional[List[float]] = None,
    grid_xy: int = 64,
    grid_z:  int = 16,
) -> dict:
    """
    Volumetric snapshot for /api/rf/field3d and the rf_field3d_update Socket.IO push.

    Field tensors are uint8-quantised then base64-encoded for compact transport:
        decode on client → atob(b64) → Uint8Array → gl.texImage3D(R8, UNSIGNED_BYTE)

    dims is [W, H, D] matching the WebGL2 texImage3D(width, height, depth) order.
    Numpy layout is (D, H, W); C-order tobytes() matches WebGL TEXTURE_3D storage.
    Longitude wrapping across the antimeridian is not supported — bounds must not
    span more than 360 degrees or cross the ±180° boundary.
    """
    import base64

    real_emitters  = _emitter_history.get_emitters()
    ghost_emitters = _field_predictor.predict(real_emitters)
    field_3d, pred_3d, used_bounds = _field_generator.generate_3d(
        real_emitters, ghost_emitters,
        bounds=bounds, grid_xy=grid_xy, grid_z=grid_z,
    )

    def _encode(arr: np.ndarray) -> str:
        return base64.b64encode(
            (arr * 255.0).clip(0, 255).astype(np.uint8).tobytes()
        ).decode('ascii')

    return {
        'field':              _encode(field_3d),
        'prediction':         _encode(pred_3d),
        'dims':               [grid_xy, grid_xy, grid_z],
        'bounds':             used_bounds,
        'real_emitters':      [
            {'id': e['id'], 'lat': e['lat'], 'lon': e['lon'],
             'power': e['power'], 'confidence': e['confidence'],
             'anomaly_score': e['anomaly_score']}
            for e in real_emitters
        ],
        'predicted_emitters': [
            {'id': e['id'], 'lat': e['lat'], 'lon': e['lon'],
             'power': e['power'], 'step': e['step']}
            for e in ghost_emitters
        ],
        'timestamp': time.time(),
    }
