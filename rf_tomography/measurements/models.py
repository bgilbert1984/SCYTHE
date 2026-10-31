"""Receivers, emitter classes, and sensing-error assumptions."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class Receiver:
    name: str
    pos: Tuple[float, float, float]
    host_building: Optional[str] = None  # roof/floor mounted -> not blocked by host
    horizontal_aperture_m: float = 0.0  # >0 => azimuth AoA available
    vertical_aperture_m: float = 0.0  # >0 => elevation AoA available
    sync_sigma_s: Optional[float] = None  # None => unsynchronised, no TOA/TDOA


@dataclass(frozen=True)
class EmitterClass:
    name: str
    freq_hz: float
    bandwidth_hz: float
    ref_snr_db: float  # per-receiver SNR at the reference distance


@dataclass(frozen=True)
class SensingParams:
    t_obs_s: float = 0.1
    multipath_range_sigma_m: float = 3.0  # unresolved multipath ranging floor
    multipath_angle_sigma_deg: float = 2.0  # urban angular-spread floor
    # Truth-generation only: errors the *solver does not model* (NLOS bias,
    # geometry error). 0 => inference model matches the world (best case).
    unmodelled_range_bias_m: float = 0.0
    unmodelled_angle_bias_deg: float = 0.0


# Mirrors the four rows in "SCYTHE RF Hunter Over Manhattan.png".
EMITTERS = (
    EmitterClass("narrow_433M_25k", 433.92e6, 25e3, 23.4),
    EmitterClass("lora_915M_200k", 915.0e6, 200e3, 19.8),
    EmitterClass("l_1260M_1M", 1.260e9, 1e6, 26.7),
    EmitterClass("wifi_2402M_20M", 2.402e9, 20e6, 24.1),
)
