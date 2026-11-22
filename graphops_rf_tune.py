"""Validation boundary for browser-originated RF tuning proposals.

A tuning click is a *proposal*, never an execution. This module validates the
request, states the boundaries the operator is accepting, and produces a receipt.
It opens no socket and never contacts Rigctl: execution remains behind the
orchestrate/propose -> decide -> execute flow in the MCP safety gate.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, Optional


ALLOWED_FIELDS = {"frequency_hz", "mode", "bandwidth_hz", "justification",
                  "acknowledge_direct_sampling"}

# rf_tune accepts these; RAW leaves the demodulator untouched.
MODES = ("RAW", "AM", "NFM", "WFM", "USB", "LSB", "CW")

# Declared coverage for the NESDR SMArt v5 (R820T2). These are datasheet facts
# about the model, not a measurement of this unit — see MODEL_DECLARED below.
TUNER_RANGE_HZ = (25e6, 1750e6)
DIRECT_SAMPLING_RANGE_HZ = (100e3, 25e6)
MAX_BANDWIDTH_HZ = 3_200_000

# Coverage by the tuner the dongle itself reports in its rtl_tcp header
# (bridge status: device.tuner_type). The R820T2 row is the model datasheet above.
# The E4000 row is the Nooelec XTR (SN 71874552) as observed with `rtl_test` on
# 2026-11-14: E4K range 52-2205 MHz with an L-band gap at 1104-1250 MHz, where the
# PLL-not-locked lines at 51, 1104, 1250 and 2206 MHz are the range edges. Those
# edge frequencies are therefore excluded. No direct-sampling range is declared for
# it anywhere in this repo, so none is offered. A tuner outside this table is
# refused: no coverage was ever declared for it.
TUNER_COVERAGE = {
    # Each range is (low, high, low_inclusive, high_inclusive).
    "R820T2": {"label": "R820T2", "ranges": [(25e6, 1750e6, True, True)],
               "direct": DIRECT_SAMPLING_RANGE_HZ},
    "E4000": {"label": "E4000 (NESDR XTR)",
              "ranges": [(52e6, 1104e6, True, False), (1250e6, 2205e6, False, True)], "direct": None},
}
TUNER_ALIASES = {"R820T": "R820T2", "R828D": None}   # librtlsdr reports a T2 as R820T

TUNE_TOOL = "rf_tune"
COVERAGE_AUTHORITY = "MODEL_DECLARED"


class TuneProposalRefused(ValueError):
    """The request never became a proposal; nothing was transmitted or tuned."""


def _finite(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise TuneProposalRefused(f"{name} must be numeric") from exc
    if not math.isfinite(number):
        raise TuneProposalRefused(f"{name} must be finite")
    return number


def _coverage_for(tuner_type: Optional[str]) -> Dict[str, Any]:
    """The declared coverage for a device-reported tuner; None means the legacy v5 default."""
    if tuner_type is None:
        return TUNER_COVERAGE["R820T2"]
    key = TUNER_ALIASES.get(str(tuner_type), str(tuner_type))
    if key not in TUNER_COVERAGE:
        raise TuneProposalRefused(
            f"the receiver reports tuner {tuner_type}, for which no coverage was declared; "
            "nothing was proposed")
    return TUNER_COVERAGE[key]


def validate_tune_request(payload: Any, tuner_type: Optional[str] = None) -> Dict[str, Any]:
    """Validate a browser tuning request into bounded rf_tune parameters.

    Raises TuneProposalRefused rather than clamping: a silently corrected
    frequency would produce a receipt for an action the operator did not request.
    """
    if not isinstance(payload, dict):
        raise TuneProposalRefused("tune request must be an object")
    unknown = set(payload) - ALLOWED_FIELDS
    if unknown:
        raise TuneProposalRefused(f"unknown tune fields: {sorted(unknown)}")

    frequency = _finite(payload.get("frequency_hz"), "frequency_hz")
    if frequency <= 0:
        raise TuneProposalRefused("frequency_hz must be greater than zero")

    mode = payload.get("mode")
    if mode is not None:
        mode = str(mode).strip().upper()
        if mode not in MODES:
            raise TuneProposalRefused(f"mode must be one of {list(MODES)}")

    bandwidth = payload.get("bandwidth_hz", 0)
    bandwidth = int(_finite(bandwidth, "bandwidth_hz"))
    if not 0 <= bandwidth <= MAX_BANDWIDTH_HZ:
        raise TuneProposalRefused(f"bandwidth_hz must be between 0 and {MAX_BANDWIDTH_HZ}")

    justification = str(payload.get("justification") or "").strip()[:512]

    coverage = _coverage_for(tuner_type)
    direct = coverage["direct"]
    boundaries = []
    in_range = any((frequency >= low if low_in else frequency > low) and (frequency <= high if high_in else frequency < high)
                   for low, high, low_in, high_in in coverage["ranges"])
    if in_range:
        regime = "TUNER"
    elif direct is not None and direct[0] <= frequency < direct[1]:
        # Direct sampling is a different signal path with different performance.
        # It is never entered implicitly on the operator's behalf.
        if not bool(payload.get("acknowledge_direct_sampling")):
            raise TuneProposalRefused(
                f"{frequency / 1e6:.6f} MHz is below the declared {coverage['label']} range and requires "
                "direct sampling; acknowledge_direct_sampling must be set explicitly"
            )
        regime = "DIRECT_SAMPLING"
        boundaries.append("DIRECT SAMPLING PERFORMANCE DIFFERS FROM ORDINARY TUNER MODE")
    else:
        raise TuneProposalRefused(
            f"{frequency / 1e6:.6f} MHz is outside the declared coverage of the receiver model"
        )

    boundaries.extend([
        f"COVERAGE PROFILE // {coverage['label']}"
        + ("" if tuner_type is not None else " // DEFAULT, NO TUNER REPORTED"),
        "TUNING IS PROPOSED, NOT EXECUTED",
        "NO RIGCTL CONNECTION WAS OPENED BY THIS REQUEST",
        f"COVERAGE CLAIM AUTHORITY // {COVERAGE_AUTHORITY}",
        "RECEIVE ONLY — TUNABILITY IS NOT TRANSMISSION AUTHORIZATION",
    ])

    params: Dict[str, Any] = {"frequency_hz": frequency}
    if mode:
        params["mode"] = mode
    if bandwidth:
        params["bandwidth_hz"] = bandwidth
    return {"tool_name": TUNE_TOOL, "params": params, "regime": regime,
            "justification": justification, "boundaries": boundaries}


def tune_receipt(request: Dict[str, Any], proposal: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Bind the exact proposed parameters to a hash the operator can carry forward."""
    canonical = json.dumps(
        {"tool": request["tool_name"], "params": request["params"], "regime": request["regime"]},
        sort_keys=True, separators=(",", ":"),
    )
    return {
        "requestHash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "proposalId": (proposal or {}).get("proposal_id"),
        "status": (proposal or {}).get("status", "unavailable"),
        "approvalReason": (proposal or {}).get("approval_reason", ""),
        "executed": False,
        "executionPath": "orchestrate/execute REQUIRES A SEPARATE APPROVED DECISION",
        "boundaries": request["boundaries"],
    }
