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


def validate_tune_request(payload: Any) -> Dict[str, Any]:
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

    tuner_low, tuner_high = TUNER_RANGE_HZ
    direct_low, direct_high = DIRECT_SAMPLING_RANGE_HZ
    boundaries = []
    if tuner_low <= frequency <= tuner_high:
        regime = "TUNER"
    elif direct_low <= frequency < direct_high:
        # Direct sampling is a different signal path with different performance.
        # It is never entered implicitly on the operator's behalf.
        if not bool(payload.get("acknowledge_direct_sampling")):
            raise TuneProposalRefused(
                f"{frequency / 1e6:.6f} MHz is below the declared R820T2 range and requires "
                "direct sampling; acknowledge_direct_sampling must be set explicitly"
            )
        regime = "DIRECT_SAMPLING"
        boundaries.append("DIRECT SAMPLING PERFORMANCE DIFFERS FROM ORDINARY TUNER MODE")
    else:
        raise TuneProposalRefused(
            f"{frequency / 1e6:.6f} MHz is outside the declared coverage of the receiver model"
        )

    boundaries.extend([
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
