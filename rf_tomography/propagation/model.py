"""Deliberately simple propagation assumptions (the *baseline*, not the answer).

Log-distance path loss with log-normal shadowing. Learned/field models
(RF-3DGS, GLocFM, Sionna RT) must beat this to justify their complexity.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

C = 299_792_458.0


@dataclass(frozen=True)
class PropagationParams:
    path_loss_exp: float = 2.7
    shadow_sigma_db_los: float = 6.0
    shadow_sigma_db_nlos: float = 8.0
    # Unknown per-emitter transmit power / facade loss, treated as nuisance.
    unknown_offset_sigma_db: float = 30.0
    snr_ref_distance_m: float = 150.0


def wavelength(freq_hz: float) -> float:
    return C / freq_hz


def snr_db_at(ref_snr_db: float, distance_m: float, p: PropagationParams) -> float:
    d = max(distance_m, 1.0)
    return ref_snr_db - 10.0 * p.path_loss_exp * math.log10(d / p.snr_ref_distance_m)
