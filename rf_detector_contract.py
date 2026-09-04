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

import math
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


# Structural validation is a separate question from measurement-state eligibility,
# and answering them with one check is how a dictionary becomes evidence. A JSON
# body carrying {"outcome": "CHANNELIZED"} satisfies eligibility perfectly and has
# no samples, no ring, no epoch and no provenance behind it.
#
#     Eligibility     transformation.outcome == CHANNELIZED, and nothing else
#     Authenticity    the provenance contract below
#     Covariates      never an admission gate, at either layer
#
# The provenance layer therefore refuses to look at a mapping at all. It requires
# the typed, bridge-local objects, because those cannot be constructed by a
# caller that did not go through the ring: `Channelization` refuses to serialize,
# so one cannot arrive over a socket.
PROVENANCE_REQUIREMENTS: Dict[str, str] = {
    "TYPED_PRODUCT": (
        "THE PRODUCT MUST BE A ChannelizedProduct BUILT IN THIS PROCESS, NOT A "
        "MAPPING. A DECODED JSON BODY IS A CLAIM ABOUT A PRODUCT, NOT ONE"
    ),
    "PROCESS_LOCAL_SAMPLES": (
        "THE BASEBAND MUST ARRIVE AS A Channelization HOLDING A COMPLEX ARRAY. "
        "A DETECTOR THAT ACCEPTS A PRODUCT WITHOUT SAMPLES IS ANALYSING METADATA"
    ),
    "PRODUCT_DIGEST_VALID": (
        "THE PRODUCT DIGEST MUST RECOMPUTE FROM THE PRODUCT'S OWN FIELDS"
    ),
    "RECOGNIZED_METHOD_REVISION": (
        "THE CHANNELIZER AND SNR MEASUREMENT REVISIONS MUST BE ONES THIS BUILD "
        "KNOWS. AN UNRECOGNISED REVISION IS A PRODUCT FROM A DIFFERENT INSTRUMENT"
    ),
    "SOURCE_WINDOW_IDENTIFIED": (
        "THE SOURCE WINDOW ID AND DIGEST MUST BOTH BE PRESENT, SO THE CHANNEL CAN "
        "BE TRACED TO THE SAMPLES IT CAME FROM"
    ),
    "EPOCH_MATCHES": (
        "THE PRODUCT'S CONFIGURATION EPOCH MUST MATCH THE RING'S CURRENT EPOCH. A "
        "RETUNE BETWEEN CHANNELIZATION AND DETECTION IS A DIFFERENT CAPTURE"
    ),
    "SIGNAL_CHAIN_MATCHES": (
        "THE PRODUCT'S SIGNAL CHAIN HASH MUST MATCH THE ONE IN FORCE. PRODUCTS "
        "THROUGH DIFFERENT ANTENNAS OR DECODES ARE NOT COMPARABLE"
    ),
    "FINITE_OUTPUT_RATE": (
        "THE OUTPUT SAMPLE RATE MUST BE FINITE AND POSITIVE, OR NO CYCLIC "
        "FREQUENCY COMPUTED FROM IT MEANS ANYTHING"
    ),
    "SUFFICIENT_USABLE_SAMPLES": (
        "ENOUGH SAMPLES MUST REMAIN AFTER THE FIR TRANSIENT WAS DISCARDED, "
        "COUNTED RATHER THAN ASSUMED FROM THE WINDOW LENGTH"
    ),
}

# Below this a cyclic estimate has too few cycles to mean anything. Declared
# here rather than inside a detector so the admission floor is reviewable
# separately from whatever statistic later sits on top of it.
MINIMUM_USABLE_SAMPLES = 4096


class DetectorInputRefused(ValueError):
    """The product may not be handed to a detector; the reason names which rule."""

    def __init__(self, message: str, requirement: Optional[str] = None) -> None:
        super().__init__(message)
        self.requirement = requirement


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


def verify_provenance(channelization: Any, *, ring: Any = None,
                      signal_chain_hash: Optional[str] = None) -> Dict[str, Any]:
    """Structural validation, entirely separate from eligibility.

    Looks at no covariate. Occupancy and SNR are not read here and could not
    change the answer if they were: a channel is authentic or it is not, and how
    strong the thing in it happens to be has no bearing on that.

    Raises ``DetectorInputRefused`` naming the requirement that failed, rather
    than returning a degraded verdict a caller might treat as a soft warning.
    """
    from rf_channelizer import (
        METHOD_REVISION, SNR_MEASUREMENT_REVISION, Channelization,
        ChannelizedProduct, product_digest_valid,
    )

    def refuse(requirement: str, detail: str) -> None:
        raise DetectorInputRefused(
            f"{requirement}: {detail} -- {PROVENANCE_REQUIREMENTS[requirement]}",
            requirement)

    if not isinstance(channelization, Channelization):
        # The single most important line here. A mapping decoded from a request
        # body can carry any outcome string it likes; it cannot be a
        # Channelization, because that type refuses to serialize.
        refuse("TYPED_PRODUCT", f"got {type(channelization).__name__}")
    product = channelization.product
    if not isinstance(product, ChannelizedProduct):
        refuse("TYPED_PRODUCT", f"product is {type(product).__name__}")
    samples = channelization.samples
    if samples is None or not hasattr(samples, "dtype") or samples.dtype.kind != "c":
        refuse("PROCESS_LOCAL_SAMPLES", "no complex baseband is attached")
    if not product_digest_valid(product):
        refuse("PRODUCT_DIGEST_VALID", "the digest does not match the record")
    if product.method_revision != METHOD_REVISION:
        refuse("RECOGNIZED_METHOD_REVISION",
               f"channelizer revision {product.method_revision!r}")
    if product.snr_measurement_revision != SNR_MEASUREMENT_REVISION:
        refuse("RECOGNIZED_METHOD_REVISION",
               f"snr revision {product.snr_measurement_revision!r}")
    if not product.source_window_id or not product.source_window_digest:
        refuse("SOURCE_WINDOW_IDENTIFIED", "window id or digest is missing")
    if ring is not None:
        epoch = getattr(ring, "configuration_epoch", None)
        if epoch is None or epoch != product.configuration_epoch:
            refuse("EPOCH_MATCHES",
                   f"product epoch {product.configuration_epoch} vs ring {epoch}")
        chain = getattr(ring, "signal_chain_hash", None)
        if chain is not None and chain != product.signal_chain_hash:
            refuse("SIGNAL_CHAIN_MATCHES", f"ring chain {chain!r}")
    if signal_chain_hash is not None and signal_chain_hash != product.signal_chain_hash:
        refuse("SIGNAL_CHAIN_MATCHES", f"expected {signal_chain_hash!r}")
    rate = product.output_sample_rate_hz
    if rate is None or not math.isfinite(rate) or rate <= 0.0:
        refuse("FINITE_OUTPUT_RATE", f"output rate {rate!r}")
    # Counted, not assumed: the transient the FIR discarded is already out of
    # `sample_count`, and the array is the final authority on what survived.
    usable = int(getattr(samples, "size", 0))
    if usable != product.sample_count:
        refuse("SUFFICIENT_USABLE_SAMPLES",
               f"{usable} samples attached but the product claims {product.sample_count}")
    if usable < MINIMUM_USABLE_SAMPLES:
        refuse("SUFFICIENT_USABLE_SAMPLES",
               f"{usable} usable samples is below the {MINIMUM_USABLE_SAMPLES} floor")
    return {
        "verified": True,
        "requirements_checked": list(PROVENANCE_REQUIREMENTS),
        "usable_samples": usable,
        "output_sample_rate_hz": float(rate),
        "configuration_epoch": product.configuration_epoch,
        "signal_chain_hash": product.signal_chain_hash,
        "source_window_id": product.source_window_id,
    }


def admit_for_detection(channelization: Any, *, ring: Any = None,
                        signal_chain_hash: Optional[str] = None) -> Dict[str, Any]:
    """Both layers, in order, for a detector about to run.

    The type check comes first, ahead of both layers. A mapping has no eligibility
    to evaluate -- reading `outcome` out of one and reporting that it was not
    CHANNELIZED would answer a question about a decoded body as though it were a
    question about a capture, and would say CHANNELIZED for a body that claimed it.

    After that: eligibility, which reads only the transformation outcome, then
    authenticity, which reads no covariate. The view returned carries measurements
    and a process-local sample array that is never placed on the product.
    """
    from rf_channelizer import Channelization

    if not isinstance(channelization, Channelization):
        raise DetectorInputRefused(
            f"got {type(channelization).__name__}: "
            f"{PROVENANCE_REQUIREMENTS['TYPED_PRODUCT']}", "TYPED_PRODUCT")
    view = detector_input(channelization.product.to_dict())     # eligibility
    view["provenance"] = verify_provenance(                     # authenticity
        channelization, ring=ring, signal_chain_hash=signal_chain_hash)
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
        # Separate from admission, and separately declared, because collapsing
        # the two is how a decoded JSON body becomes detector input.
        "provenance_requirements": dict(PROVENANCE_REQUIREMENTS),
        "provenance_reads_covariates": False,
        "minimum_usable_samples": MINIMUM_USABLE_SAMPLES,
        "baseband_transportable": False,
        "detector_implemented": False,
    }
