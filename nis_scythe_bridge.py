#!/usr/bin/env python3
import hashlib
import importlib.util
import json
import math
import sys
import time
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

import numpy as np


REPO_ROOT = Path(__file__).resolve().parent
NIS_ROOT = REPO_ROOT / "assets" / "NIS-main"
SIGINT_CITY_TARGETS_PATH = (
    NIS_ROOT / "NIS-Starlink-Radar-Video-main" / "Python Scripts" / "city_targets.py"
)
SAR_SCENE_DATA_PATH = NIS_ROOT / "NIS-SAR-AMTIGMTI-Video-main" / "sar_scene_data.py"

DEFAULT_SIGINT_RESULTS_DIR = REPO_ROOT / "SIGINT Sim Results"
DEFAULT_SIGINT_NPZ_PATH = DEFAULT_SIGINT_RESULTS_DIR / "sigint_multibeam_data.npz"
DEFAULT_SIGINT_CACHE_PATH = DEFAULT_SIGINT_RESULTS_DIR / "clean_data.js"

SIGINT_PROTOCOLS = (
    {"name": "LTE_B3_1815", "center_hz": 1815e6, "bandwidth_hz": 20e6, "band_label": "1800MHz", "protocol_family": "lte"},
    {"name": "LTE_B3_1835", "center_hz": 1835e6, "bandwidth_hz": 20e6, "band_label": "1800MHz", "protocol_family": "lte"},
    {"name": "LTE_B3_1855", "center_hz": 1855e6, "bandwidth_hz": 20e6, "band_label": "1800MHz", "protocol_family": "lte"},
    {"name": "LTE_B2_1940", "center_hz": 1940e6, "bandwidth_hz": 20e6, "band_label": "1900MHz", "protocol_family": "lte"},
    {"name": "LTE_B2_1960", "center_hz": 1960e6, "bandwidth_hz": 20e6, "band_label": "1900MHz", "protocol_family": "lte"},
    {"name": "LTE_B2_1980", "center_hz": 1980e6, "bandwidth_hz": 20e6, "band_label": "1900MHz", "protocol_family": "lte"},
    {"name": "LTE_B1_2120", "center_hz": 2120e6, "bandwidth_hz": 20e6, "band_label": "2100MHz", "protocol_family": "lte"},
    {"name": "LTE_B1_2140", "center_hz": 2140e6, "bandwidth_hz": 20e6, "band_label": "2100MHz", "protocol_family": "lte"},
    {"name": "LTE_B1_2160", "center_hz": 2160e6, "bandwidth_hz": 20e6, "band_label": "2100MHz", "protocol_family": "lte"},
    {"name": "LTE_B40_2310", "center_hz": 2310e6, "bandwidth_hz": 20e6, "band_label": "2300MHz", "protocol_family": "lte"},
    {"name": "LTE_B40_2330", "center_hz": 2330e6, "bandwidth_hz": 20e6, "band_label": "2300MHz", "protocol_family": "lte"},
    {"name": "LTE_B40_2350", "center_hz": 2350e6, "bandwidth_hz": 20e6, "band_label": "2300MHz", "protocol_family": "lte"},
    {"name": "LTE_B40_2370", "center_hz": 2370e6, "bandwidth_hz": 20e6, "band_label": "2300MHz", "protocol_family": "lte"},
    {"name": "LTE_B40_2390", "center_hz": 2390e6, "bandwidth_hz": 20e6, "band_label": "2300MHz", "protocol_family": "lte"},
    {"name": "WIFI_CH1_2412", "center_hz": 2412e6, "bandwidth_hz": 20e6, "band_label": "2.4GHz", "protocol_family": "wifi"},
    {"name": "WIFI_CH6_2437", "center_hz": 2437e6, "bandwidth_hz": 20e6, "band_label": "2.4GHz", "protocol_family": "wifi"},
    {"name": "WIFI_CH11_2462", "center_hz": 2462e6, "bandwidth_hz": 20e6, "band_label": "2.4GHz", "protocol_family": "wifi"},
    {"name": "LTE_B11_1480", "center_hz": 1480e6, "bandwidth_hz": 20e6, "band_label": "1500MHz", "protocol_family": "lte"},
    {"name": "LTE_B74_1500", "center_hz": 1500e6, "bandwidth_hz": 20e6, "band_label": "1500MHz", "protocol_family": "lte"},
)


@contextmanager
def _prepend_sys_path(path: Path):
    path_str = str(path)
    sys.path.insert(0, path_str)
    try:
        yield
    finally:
        try:
            sys.path.remove(path_str)
        except ValueError:
            pass


@lru_cache(maxsize=8)
def _load_python_module(name: str, path: Path):
    if not path.exists():
        raise FileNotFoundError(f"NIS asset not found: {path}")

    spec = importlib.util.spec_from_file_location(f"_nis_{name}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load NIS asset module: {path}")

    module = importlib.util.module_from_spec(spec)
    with _prepend_sys_path(path.parent):
        spec.loader.exec_module(module)
    return module


def _stable_hash(payload) -> str:
    return hashlib.sha1(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _meters_to_latlon(x_m: float, y_m: float, origin_lat: float, origin_lon: float):
    lon = origin_lon + x_m / (40075000.0 * math.cos(math.radians(origin_lat)) / 360.0)
    lat = origin_lat + y_m / 111320.0
    return lat, lon


def _freq_to_band_label(freq_mhz: float) -> str:
    if 2400.0 <= freq_mhz <= 2500.0:
        return "2.4GHz"
    if 1710.0 <= freq_mhz <= 2200.0:
        return "cellular-midband"
    if 1450.0 <= freq_mhz <= 1550.0:
        return "l-band"
    if 2300.0 <= freq_mhz <= 2400.0:
        return "2300MHz"
    return "rf"


def generate_sigint_emitters(
    emitters_per_band: int = 1,
    scatter_area_m: float = 5000.0,
    seed: int = 1337,
    satellite_grazing_angle_deg: float = 45.0,
    tx_power_dbm: float = 23.0,
):
    city_targets = _load_python_module("city_targets", SIGINT_CITY_TARGETS_PATH)
    rng = np.random.default_rng(seed)
    emitters = []

    for protocol in SIGINT_PROTOCOLS:
        for emitter_idx in range(max(1, int(emitters_per_band))):
            center_x = float(rng.uniform(-scatter_area_m / 2.0, scatter_area_m / 2.0))
            center_y = float(rng.uniform(-scatter_area_m / 2.0, scatter_area_m / 2.0))
            bandwidth_hz = float(rng.uniform(1.4e6, 5.0e6))
            max_shift_hz = max((float(protocol["bandwidth_hz"]) - bandwidth_hz) / 2.0, 0.0)
            center_hz = float(protocol["center_hz"])
            if max_shift_hz > 0.0:
                center_hz += float(rng.uniform(-max_shift_hz, max_shift_hz))

            effective_gain_dbi = float(city_targets.calc_ue_sky_gain(satellite_grazing_angle_deg, rng))
            emitter = city_targets.create_rf_emitter(
                center_x,
                center_y,
                1.2,
                float(tx_power_dbm),
                effective_gain_dbi,
                center_hz,
                bandwidth_hz,
                "802.11_PRN" if protocol["protocol_family"] == "wifi" else "5G_PRN",
                name=f"EMI_{protocol['name']}_{emitter_idx}",
            )
            emitter["protocol_label"] = protocol["name"]
            emitter["protocol_family"] = protocol["protocol_family"]
            emitter["band_label"] = protocol["band_label"]
            emitters.append(emitter)

    return emitters


def normalize_sigint_emitters(
    emitters,
    sensor_id: str = "nis-sim",
    timestamp: float | None = None,
    origin_lat: float | None = None,
    origin_lon: float | None = None,
    mission_id: str | None = None,
):
    ts = float(timestamp or time.time())
    observations = []

    for emitter in emitters or []:
        if not emitter.get("is_emitter"):
            continue

        x_m, y_m, z_m = [float(value) for value in emitter.get("position", [0.0, 0.0, 0.0])]
        frequency_mhz = float(emitter.get("freq_hz", 0.0)) / 1e6
        bandwidth_mhz = float(emitter.get("bandwidth_hz", 0.0)) / 1e6
        fingerprint = _stable_hash(
            {
                "sensor_id": sensor_id,
                "name": emitter.get("name"),
                "x_m": round(x_m, 3),
                "y_m": round(y_m, 3),
                "z_m": round(z_m, 3),
                "frequency_mhz": round(frequency_mhz, 6),
                "bandwidth_mhz": round(bandwidth_mhz, 6),
                "signal_type": emitter.get("signal_type"),
            }
        )

        observation = {
            "observation_id": f"nis-rfobs:{fingerprint[:16]}",
            "timestamp": ts,
            "sensor_id": sensor_id,
            "rf_node_id": f"rf:{sensor_id}:{fingerprint[:12]}",
            "rf_fingerprint": fingerprint,
            "frequency_mhz": frequency_mhz,
            "bandwidth_mhz": bandwidth_mhz,
            "power_dbm": float(emitter.get("tx_power_dbm", 0.0)),
            "modulation": emitter.get("signal_type"),
            "waveform": emitter.get("signal_type"),
            "mission_id": mission_id,
            "labels": {
                "evidence": "SYNTHETIC",
                "band": emitter.get("band_label") or _freq_to_band_label(frequency_mhz),
                "protocol_family": emitter.get("protocol_family") or "rf",
            },
            "source": "nis_sigint_sim",
            "synthetic": True,
            "protocol_label": emitter.get("protocol_label"),
            "protocol_family": emitter.get("protocol_family"),
            "band_label": emitter.get("band_label") or _freq_to_band_label(frequency_mhz),
            "effective_tx_gain_dbi": float(emitter.get("effective_tx_gain_dbi_to_sat", 0.0)),
            "local_position_m": {"x": x_m, "y": y_m, "z": z_m},
            "local_frame": "nis_sigint_scene",
            "scene_name": emitter.get("name"),
        }

        if origin_lat is not None and origin_lon is not None:
            lat, lon = _meters_to_latlon(x_m, y_m, float(origin_lat), float(origin_lon))
            observation["lat"] = lat
            observation["lon"] = lon
            observation["alt_m"] = z_m

        observations.append(observation)

    return observations


def generate_sigint_observations(
    emitters_per_band: int = 1,
    scatter_area_m: float = 5000.0,
    seed: int = 1337,
    satellite_grazing_angle_deg: float = 45.0,
    tx_power_dbm: float = 23.0,
    sensor_id: str = "nis-sim",
    origin_lat: float | None = None,
    origin_lon: float | None = None,
    mission_id: str | None = None,
    timestamp: float | None = None,
):
    emitters = generate_sigint_emitters(
        emitters_per_band=emitters_per_band,
        scatter_area_m=scatter_area_m,
        seed=seed,
        satellite_grazing_angle_deg=satellite_grazing_angle_deg,
        tx_power_dbm=tx_power_dbm,
    )
    observations = normalize_sigint_emitters(
        emitters,
        sensor_id=sensor_id,
        timestamp=timestamp,
        origin_lat=origin_lat,
        origin_lon=origin_lon,
        mission_id=mission_id,
    )
    protocol_counts = {}
    for observation in observations:
        protocol = observation.get("protocol_family") or "rf"
        protocol_counts[protocol] = protocol_counts.get(protocol, 0) + 1

    return {
        "source": "nis_sigint_sim",
        "protocol_count": len(SIGINT_PROTOCOLS),
        "emitter_count": len(emitters),
        "observation_count": len(observations),
        "protocol_family_counts": protocol_counts,
        "observations": observations,
    }


def summarize_sigint_npz(npz_path: str | Path = DEFAULT_SIGINT_NPZ_PATH):
    path = Path(npz_path)
    if not path.exists():
        raise FileNotFoundError(f"SIGINT multibeam NPZ not found: {path}")

    data = np.load(path, allow_pickle=True)
    emitters_gt = []
    if "emitters_gt" in data:
        emitters_gt = json.loads(str(data["emitters_gt"]))

    freqs = data["freqs"] if "freqs" in data else np.array([])
    measured = data["measured_power_mag2"] if "measured_power_mag2" in data else np.array([])
    x_beams = data["x_beams"] if "x_beams" in data else np.array([])
    y_beams = data["y_beams"] if "y_beams" in data else np.array([])

    return {
        "path": str(path),
        "frequency_range_mhz": [
            float(freqs[0] / 1e6) if freqs.size else None,
            float(freqs[-1] / 1e6) if freqs.size else None,
        ],
        "frequency_bin_count": int(freqs.size),
        "beam_grid": {
            "x": int(x_beams.size),
            "y": int(y_beams.size),
        },
        "scene_extent_m": float(x_beams[-1] - x_beams[0]) if x_beams.size > 1 else 0.0,
        "emitter_count": len(emitters_gt),
        "sample_emitters": [
            {
                "x_m": float(emitter.get("x", 0.0)),
                "y_m": float(emitter.get("y", 0.0)),
                "frequency_mhz": float(emitter.get("f", 0.0)) / 1e6,
                "bandwidth_mhz": float(emitter.get("bw", 0.0)) / 1e6,
            }
            for emitter in emitters_gt[:5]
        ],
        "power_stats": {
            "shape": list(measured.shape),
            "max": float(np.max(measured)) if measured.size else 0.0,
            "mean": float(np.mean(measured)) if measured.size else 0.0,
        },
        "has_dirty_psf": "dirty_psf" in data,
        "aperture_radius_m": float(data["aperture_radius"]) if "aperture_radius" in data else None,
        "alt_m": float(data["alt"]) if "alt" in data else None,
    }


def summarize_clean_cache(cache_path: str | Path = DEFAULT_SIGINT_CACHE_PATH):
    path = Path(cache_path)
    if not path.exists():
        raise FileNotFoundError(f"SIGINT cache JS not found: {path}")

    content = path.read_text(encoding="utf-8").strip()
    prefix = "const CACHED_DATA = "
    if not content.startswith(prefix) or not content.endswith(";"):
        raise ValueError(f"Unexpected clean cache format: {path}")
    payload = json.loads(content[len(prefix):-1])

    power = np.array(payload.get("power") or [])
    hue = np.array(payload.get("hue") or [])
    return {
        "path": str(path),
        "frequency_range_mhz": [
            float(payload.get("min_freq", 0.0)) / 1e6,
            float(payload.get("max_freq", 0.0)) / 1e6,
        ],
        "power_shape": list(power.shape),
        "hue_shape": list(hue.shape),
        "power_max": float(np.max(power)) if power.size else 0.0,
        "power_mean": float(np.mean(power)) if power.size else 0.0,
    }


def summarize_sar_priors(materials, scene_models):
    material_categories = {
        "high_reflectivity": 0,
        "terrain": 0,
        "water": 0,
    }
    for name, material in (materials or {}).items():
        dielectric = float(material.get("dielectric", 0.0))
        if dielectric >= 500.0:
            material_categories["high_reflectivity"] += 1
        if name in {"water", "ocean", "coastline", "bay", "swimming_pool"}:
            material_categories["water"] += 1
        if name in {"default", "ground", "terrain", "sand", "grass", "forest", "farmland"}:
            material_categories["terrain"] += 1

    return {
        "material_count": len(materials or {}),
        "scene_model_count": len(scene_models or []),
        "material_categories": material_categories,
        "sample_materials": dict(list((materials or {}).items())[:5]),
        "sample_models": [
            {
                "name": model.get("name"),
                "file": model.get("file"),
                "material": model.get("material"),
                "position": list(model.get("position") or []),
                "height_offset": model.get("height_offset"),
            }
            for model in list(scene_models or [])[:5]
        ],
    }


def load_sar_scene_priors():
    sar_scene = _load_python_module("sar_scene_data", SAR_SCENE_DATA_PATH)
    return summarize_sar_priors(
        getattr(sar_scene, "MATERIALS", {}),
        getattr(sar_scene, "SCENE_MODELS", []),
    )
