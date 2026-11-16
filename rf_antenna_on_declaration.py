"""Staged check for the antenna-on OBSERVATION-ONLY declaration (§5.21 slice 3, owner scope 2026-11-16).

Pure functions. Nothing here contacts a device, and the sequencer does not call this module.
It checks that a declaration document is internally consistent with the canary gate constants and a
hashed intrinsic-tier artifact, and that it stays inside the scope the owner set: UHF 400-470 MHz,
gain 29.0 dB, E4000, observation-only. A DRAFT declaration never authorizes anything.
"""
from __future__ import annotations

from typing import Any, Dict, List

import rf_canary_gate as gate

REVISION = "antenna-on-decl-1"
SCOPE_LOW_HZ = 400_000_000
SCOPE_HIGH_HZ = 470_000_000
SCOPE_GAIN_DB = 29.0
SCOPE_TUNER = "E4000"
SCOPE_MODE = "OBSERVATION_ONLY"
MIN_REVALIDATION_MONTHS = 1
MAX_REVALIDATION_MONTHS = 12


class DeclarationRefused(Exception):
    def __init__(self, codes: List[str]):
        super().__init__(", ".join(codes))
        self.codes = codes


def check_declaration(decl: Dict[str, Any], tier: Dict[str, Any]) -> Dict[str, Any]:
    """Return {'ok', 'authorizes', 'codes'}; `codes` lists every refusal reason found."""
    codes: List[str] = []

    if decl.get("mode") != SCOPE_MODE:
        codes.append("AOD_MODE_NOT_OBSERVATION_ONLY")
    if decl.get("gain_db") != SCOPE_GAIN_DB:
        codes.append("AOD_GAIN_OUT_OF_SCOPE")
    if decl.get("receiver", {}).get("tuner") != SCOPE_TUNER:
        codes.append("AOD_TUNER_OUT_OF_SCOPE")
    bands = decl.get("bands") or []
    if not bands or any(not (SCOPE_LOW_HZ <= b.get("low_hz", -1) < b.get("high_hz", -1) <= SCOPE_HIGH_HZ) for b in bands):
        codes.append("AOD_BAND_OUT_OF_SCOPE")
    if decl.get("termination", {}).get("fitted") is not False:
        codes.append("AOD_TERMINATION_CLAIM_PRESENT")

    g = decl.get("gate", {})
    if (g.get("canary_min_excess_db") != gate.CANARY_MIN_EXCESS_DB
            or g.get("canary_match_tol_hz") != gate.CANARY_MATCH_TOL_HZ
            or g.get("clip_limit_pct") != gate.CLIP_LIMIT_PCT
            or g.get("revision") != gate.REVISION):
        codes.append("AOD_GATE_CONSTANTS_DIFFER")
    if g.get("tuning_gate") != "EVERY_VISIT":
        codes.append("AOD_TUNING_GATE_NOT_EVERY_VISIT")

    t = decl.get("intrinsic_tier", {})
    try:
        offsets = gate.tier_offsets(tier)
    except gate.CanaryRefused as exc:
        codes.append("AOD_TIER_" + exc.code)
        offsets = []
    else:
        if tier.get("content_sha256") != t.get("content_sha256"):
            codes.append("AOD_TIER_HASH_NOT_BOUND")
        if sorted(offsets) != sorted(float(x) for x in t.get("offsets_hz", [])):
            codes.append("AOD_TIER_OFFSETS_DIFFER")
        if tier.get("revision") != t.get("revision"):
            codes.append("AOD_TIER_REVISION_DIFFERS")

    r = decl.get("revalidation", {})
    months = r.get("interval_months")
    if not isinstance(months, int) or not (MIN_REVALIDATION_MONTHS <= months <= MAX_REVALIDATION_MONTHS):
        codes.append("AOD_REVALIDATION_INTERVAL_INVALID")
    if not {"HARDWARE_CHANGE", "CANARY_FAILING_UNDER_QUIET_CONDITIONS"} <= set(r.get("triggers", [])):
        codes.append("AOD_REVALIDATION_TRIGGERS_MISSING")

    ok = not codes
    authorizes = ok and decl.get("status") == "FROZEN" and decl.get("authorizes") is True
    return {"ok": ok, "authorizes": authorizes, "codes": codes, "revision": REVISION}
