"""Phase 2 entry: what a detector receives, and what it may not conclude from it.

Frozen before the first detector exists, on purpose.  A contract written after a
detector works is a description of that detector; a contract written before it is
a constraint on it.

The admission rule is one question
----------------------------------
A detector may consume a channel's process-local samples when, and only when, the
*transformation* succeeded::

    product["transformation"]["outcome"] == "CHANNELIZED"

Occupied bandwidth and SNR are covariates, not gates.  Cyclostationary methods
find symbol structure below the level at which ordinary spectral occupancy
closes, so refusing every channel whose width or SNR is unresolved would discard
most of what such a detector is for.  ``rf_channelizer`` therefore reports three
verdicts separately -- transformation, occupancy, SNR -- and this contract reads
only the first of them for admission.

What ``snr_db: None`` means
---------------------------
It means the SNR was not measurable from this channel's geometry.  It does not
mean zero, it does not mean negative infinity, it does not mean weak, and it is
not an ordering relation with any number.  A detector that substitutes a value
for it has invented evidence, so ``qualified_snr_db`` returns ``None`` and every
consumer must branch rather than default.

What decides
------------
The registered method's detection statistic against its registered threshold,
with its calibrated false-alarm probability.  SNR is a *validation covariate*: it
stratifies the Phase 3 corpus and it conditions a reported confidence, but it has
no standing to admit or refuse a detection.  A detector that thresholds on SNR is
a detector whose PFA was never measured.

Baseband
--------
Samples reach a detector inside this process and leave it as measurements.  The
contract carries no field that holds a complex array and no path that would let
one into a status payload, an API response, a log line or GraphOps.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


CONTRACT_SCHEMA = "scythe.rf-detector-input.v1"
CONTRACT_REVISION = "detector-input-contract.v1"

# Admission is this and nothing else.
ADMISSION_FIELD: Tuple[str, str] = ("transformation", "outcome")
ADMISSION_VALUE = "CHANNELIZED"

# What a detector is given about the channel it is looking at. Every one of these
# is a scalar or a string; none of them is baseband.
REQUIRED_CHANNEL_FIELDS: Tuple[str, ...] = (
    "product_id",
    "source_window_id",
    "source_window_digest",
    "configuration_epoch",
    "signal_chain_hash",
    "channel_center_hz",
    "channel_bandwidth_hz",
    "channel_selection_basis",
    "output_sample_rate_hz",
    "decimation",
    "sample_count",
    "transient_samples_discarded",
    "method_revision",
)

# Covariates. Present always, resolved sometimes. A detector must read the reason
# code rather than the bare null, because "no room to measure" and "the walk hit
# the filter" are different facts about the channel it is about to analyse.
COVARIATE_BLOCKS: Tuple[str, ...] = ("occupancy", "snr")

# Stated as prohibitions because each is a specific way a null becomes a number.
PROHIBITED_INFERENCES: Dict[str, str] = {
    "SNR_AS_ZERO": (
        "AN UNRESOLVED SNR IS NOT 0 dB. SUBSTITUTING ZERO MAKES AN UNMEASURED "
        "CHANNEL COMPARE EQUAL TO A MEASURED ONE AT UNITY RATIO"
    ),
    "SNR_AS_NEGATIVE_INFINITY": (
        "AN UNRESOLVED SNR IS NOT THE ABSENCE OF SIGNAL. THE REFERENCE GEOMETRY "
        "FAILED, WHICH IS A STATEMENT ABOUT THE CHANNEL'S WIDTH, NOT ITS CONTENT"
    ),
    "SNR_AS_WEAK": (
        "AN UNRESOLVED SNR IS NOT EVIDENCE OF A WEAK SIGNAL. THE STRONGEST "
        "OBSERVED PRODUCTS FAIL THE REFERENCE BUDGET WHEN THEY FILL THEIR CHANNEL"
    ),
    "SNR_AS_ADMISSION": (
        "SNR DOES NOT ADMIT OR REFUSE A DETECTION. THE REGISTERED METHOD'S "
        "STATISTIC AND ITS CALIBRATED FALSE-ALARM PROBABILITY DECIDE"
    ),
    "OCCUPANCY_AS_SYMBOL_RATE": (
        "AN OCCUPIED BANDWIDTH IS NOT A SYMBOL RATE. THE RELATION BETWEEN THEM "
        "DEPENDS ON A MODULATION AND A FILTER ROLL-OFF THAT NOTHING HAS DETERMINED"
    ),
    "TRANSFORMATION_AS_DETECTION": (
        "A CHANNELIZED PRODUCT IS A CUT SPAN, NOT A SIGNAL. THE CHANNELIZER "
        "SELECTS A REGION; IT DOES NOT ASSERT THAT AN EMITTER OCCUPIES IT"
    ),
    "COVARIATE_AS_CONFIDENCE": (
        "A RESOLVED SNR IS NOT A CONFIDENCE. CONFIDENCE COMES FROM A CALIBRATION "
        "THAT PHASE 3 HAS NOT RUN"
    ),
}


class DetectorInputRefused(ValueError):
    """The product may not be handed to a detector; the reason names which rule."""


def admits(product: Dict[str, Any]) -> bool:
    """True when the transformation succeeded. Reads nothing else."""
    block = product.get(ADMISSION_FIELD[0])
    if not isinstance(block, dict):
        return False
    return block.get(ADMISSION_FIELD[1]) == ADMISSION_VALUE


def qualified_snr_db(product: Dict[str, Any]) -> Optional[float]:
    """The SNR only where it was actually measured, otherwise ``None``.

    Never a default. A caller that wants a number must decide for itself what an
    unmeasured channel means to it, in the open, at its own call site.
    """
    snr = product.get("snr")
    if not isinstance(snr, dict) or snr.get("snr_reason_code") is not None:
        return None
    value = snr.get("snr_db")
    return None if value is None else float(value)


def snr_stratum(product: Dict[str, Any]) -> str:
    """Which validation stratum this product's SNR places it in.

    ``UNRESOLVED`` is its own stratum rather than a bucket boundary. Phase 3 must
    be able to report the detector's behaviour on channels whose SNR nobody knows,
    because in operation that is a large share of them.
    """
    value = qualified_snr_db(product)
    if value is None:
        return "SNR_UNRESOLVED"
    for edge, name in ((0.0, "SNR_BELOW_0_DB"), (10.0, "SNR_0_TO_10_DB"),
                       (20.0, "SNR_10_TO_20_DB"), (30.0, "SNR_20_TO_30_DB")):
        if value < edge:
            return name
    return "SNR_ABOVE_30_DB"


def detector_input(product: Dict[str, Any]) -> Dict[str, Any]:
    """The bounded view of a product that a detector may hold.

    Raises rather than returning a degraded view: a detector handed a refused
    channelization would be analysing a product that names no samples.
    """
    if not admits(product):
        outcome = (product.get("transformation") or {}).get("outcome", "UNKNOWN")
        raise DetectorInputRefused(
            f"transformation outcome {outcome!r} is not {ADMISSION_VALUE!r}; "
            f"no channel was produced, so there is nothing to analyse")
    missing = [field for field in REQUIRED_CHANNEL_FIELDS if field not in product]
    if missing:
        raise DetectorInputRefused(f"product is missing required fields: {missing}")
    view: Dict[str, Any] = {
        "schema": CONTRACT_SCHEMA,
        "contract_revision": CONTRACT_REVISION,
        **{field: product[field] for field in REQUIRED_CHANNEL_FIELDS},
    }
    for block in COVARIATE_BLOCKS:
        view[block] = dict(product.get(block) or {})
    view["snr_stratum"] = snr_stratum(product)
    view["qualified_snr_db"] = qualified_snr_db(product)
    view["raw_iq_exposed"] = False
    return view


def contract_status() -> Dict[str, Any]:
    """The frozen contract, for the status payload and for review."""
    return {
        "schema": CONTRACT_SCHEMA,
        "contract_revision": CONTRACT_REVISION,
        "state": "FROZEN_NO_DETECTOR_IMPLEMENTED",
        "admission_rule": f"{'.'.join(ADMISSION_FIELD)} == {ADMISSION_VALUE}",
        "snr_role": "VALIDATION_COVARIATE_NOT_ADMISSION_AUTHORITY",
        "occupancy_role": "VALIDATION_COVARIATE_NOT_ADMISSION_AUTHORITY",
        "decides": "REGISTERED_METHOD_STATISTIC_AGAINST_CALIBRATED_PFA",
        "required_channel_fields": list(REQUIRED_CHANNEL_FIELDS),
        "covariate_blocks": list(COVARIATE_BLOCKS),
        "prohibited_inferences": dict(PROHIBITED_INFERENCES),
        "baseband_transportable": False,
        "detector_implemented": False,
    }
