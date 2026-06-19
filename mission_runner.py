
import time
import random
import math
import logging
from typing import List, Dict, Any

logger = logging.getLogger("MissionRunner")


def _dist_m(p1_lat: float, p1_lon: float, p2_lat: float, p2_lon: float) -> float:
    """Flat-earth distance approximation in metres (accurate for small areas ~5 km)."""
    dx = (p2_lon - p1_lon) * 90_000
    dy = (p2_lat - p1_lat) * 111_000
    return math.sqrt(dx * dx + dy * dy)


def run_fusion_demo_5km(sensor_registry):
    """
    Runs the Fusion Demo 5km Mission.
    Simulates LPI detection -> AoA/TDoA tracking.
    """

    # --- Phase 0: Deploy Sensors ---
    sensors = [
        {
            "sensor_id": "SENSOR-ALPHA",
            "label": "RTL-SDR Alpha",
            "lat": 34.0522, "lon": -118.2437, "alt": 100, # Los Angeles (Base 1)
            "type": "RTL-SDR",
            "sample_rate_hz": 2400000,
            "center_freq_hz": 433920000,
            "iq_format": "cs16_iq_interleaved",
            "timing_source": "gpsdo", # Supports TDoA
            "supports_aoa": True
        },
        {
            "sensor_id": "SENSOR-BRAVO",
            "label": "RTL-SDR Bravo",
            "lat": 34.0522, "lon": -118.2337, "alt": 100, # ~1km East (Base 2)
            "type": "RTL-SDR",
            "sample_rate_hz": 2400000,
            "center_freq_hz": 433920000,
            "iq_format": "cs16_iq_interleaved",
            "timing_source": "gpsdo",
            "supports_aoa": True
        },
        {
            "sensor_id": "SENSOR-CHARLIE",
            "label": "RTL-SDR Charlie",
            "lat": 34.0622, "lon": -118.2387, "alt": 150, # ~1km North (Base 3)
            "type": "RTL-SDR",
            "sample_rate_hz": 2400000,
            "center_freq_hz": 433920000,
            "iq_format": "cs16_iq_interleaved",
            "timing_source": "gpsdo",
            "supports_aoa": True
        }
    ]

    for s in sensors:
        sensor_registry.upsert_sensor(s)

    trace = []

    # Target Trajectory (Simulated Drone moving West to East)
    # Start: 34.0572, -118.2500 -> End: 34.0572, -118.2200
    start_lat, start_lon = 34.0572, -118.2500
    steps = 20

    logger.info(f"Starting Fusion Demo 5km Mission with {len(sensors)} sensors.")

    # --- Simulation Loop ---
    for i in range(steps):
        # Move target
        progress = i / steps
        current_lat = start_lat # Straight line East
        current_lon = start_lon + (0.0300 * progress) # ~3km path

        target_pos = {"lat": current_lat, "lon": current_lon, "alt": 500}

        # --- Phase 1: LPI Detection (Trigger) ---
        # Simulate detection on Alpha first
        if i == 0:
            sensor = sensors[0] # Alpha
            # Emit IQ Window
            sensor_registry.emit_activity(sensor["sensor_id"], "iq_window_received", {
                "window": {"center_freq_hz": 433920000, "samples": 120000},
                "evidence_ptr": f"iq_dump_{i}.bin"
            })
            # Emit LPI Candidate
            sensor_registry.emit_activity(sensor["sensor_id"], "lpi_candidate_detected", {
                "freq_hz": 433920000,
                "bandwidth_hz": 50000,
                "snr_db": 12.5 + random.random() * 5,
                "confidence": 0.85
            })
            # Emit Classification
            sensor_registry.emit_activity(sensor["sensor_id"], "waveform_classified", {
                "signal_family": "fmcw_drone_video",
                "confidence": 0.92,
                "algo": "pace_hos_classifier_v1"
            })
            trace.append(f"Step {i}: LPI detection triggered on {sensor['sensor_id']}")

        # --- Phase 2: AoA / TDoA Measurements ---
        # Calculate simulated bearings and TDoA for all sensors
        for s in sensors:
            # 1. AoA Simulation
            # Simple bearing calc
            y = math.sin(math.radians(current_lon - s["lon"])) * math.cos(math.radians(current_lat))
            x = math.cos(math.radians(s["lat"])) * math.sin(math.radians(current_lat)) - \
                math.sin(math.radians(s["lat"])) * math.cos(math.radians(current_lat)) * math.cos(math.radians(current_lon - s["lon"]))
            bearing_rad = math.atan2(y, x)
            bearing_deg = (math.degrees(bearing_rad) + 360) % 360

            # Add noise
            measured_bearing = bearing_deg + random.gauss(0, 2.0) # 2 deg std dev

            # Emit AoA
            sensor_registry.emit_activity(s["sensor_id"], "aoa_measured", {
                "bearing_deg": measured_bearing,
                "sigma_deg": 5.0, # Cone width
                "freq_hz": 433920000,
                "algo": {"name":"music_aoa","version":"1.2"},
                "feature_set_id": f"aoa_step_{i}"
            })

            # 2. TDoA Simulation (Relative to Alpha as reference)
            if s["sensor_id"] != "SENSOR-ALPHA":
                # Distances (approx, flat earth for small area)
                d_target_s = _dist_m(current_lat, current_lon, s["lat"], s["lon"])
                d_target_ref = _dist_m(current_lat, current_lon, sensors[0]["lat"], sensors[0]["lon"])  # Alpha is ref

                diff_dist = d_target_s - d_target_ref
                c = 3e8
                tau_ns = (diff_dist / c) * 1e9

                # Add noise (simulating sync jitter)
                measured_tau = tau_ns + random.gauss(0, 15.0) # 15ns jitter

                sensor_registry.emit_activity(s["sensor_id"], "tdoa_measured", {
                    "ref_sensor_id": "SENSOR-ALPHA",
                    "tau_ns": measured_tau,
                    "sigma_ns": 30.0,
                    "algo": {"name":"gcc_phat_tdoa","version":"2.0"},
                    "feature_set_id": f"tdoa_step_{i}"
                })

        trace.append(f"Step {i}: Measurements emitted for Target at {current_lat:.4f}, {current_lon:.4f}")
        time.sleep(0.1) # Fast replay

    return trace
