"""Phase 1b: a pure, fail-closed channelizer over a verified IQ window.

Consumes an ``IQWindow`` from ``rf_iq_ring`` and produces one bounded
``ChannelizedProduct``.  It touches nothing else: no bridge, no store, no capture
path, no detector.  The seams stay separate on purpose --

    1. channelizer as a standalone IQWindow consumer   <- this change
    2. bridge instantiates BoundedIQRing
    3. bridge issues verified windows to the channelizer
    4. channelizer emits bounded derived products
    5. detector consumes channelized products

-- so that DSP mathematics and lifecycle enforcement are never reviewed in the
same diff.

What "fail-closed" means here
-----------------------------
Every refusal is a named outcome with a reason, never a silently degraded
product.  A channelizer that quietly widens a filter, normalizes an amplitude, or
processes a window whose epoch has moved does not fail loudly enough to be
noticed, and its output is then indistinguishable from a good one.  The ten
declared outcomes in ``OUTCOMES`` are the whole surface.

The two-stage trap
------------------
An occupied-bandwidth estimate used to *choose* a channel cannot then be reported
as evidence of how well the signal *fits* that channel: the channelizer would be
grading its own selection.  The two stages are therefore recorded separately --
``candidate_center_hz`` / ``candidate_bandwidth_hz`` from the coarse pass over the
whole window, and ``channel_center_hz`` / ``channel_bandwidth_hz`` for what was
actually cut.  ``occupied_bandwidth_basis`` states which window the final estimate
came from, and is ``SAME_WINDOW_AS_SELECTION`` here because there is only one
window.  A later independent measurement may set it otherwise; nothing in this
module may set it to ``INDEPENDENT_WINDOW`` on its own say-so.

Amplitude
---------
No AGC, no per-window normalization.  The FIR has unity DC gain and the DDC is a
unit-modulus rotation, so a product's absolute amplitude remains comparable with
products from other windows on the same signal chain.  Normalizing per window
would make every capture look equally strong, which destroys exactly the temporal
comparison a survey exists to support.

Raw samples stay process-local.  ``ChannelizedProduct`` is metadata and
measurements only; the baseband array is carried beside it in a
``Channelization`` that refuses to serialize.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from typing import Any, Dict, Optional, Tuple

import numpy as np

from rf_iq_ring import IQWindow, RawIQNotTransportable


SCHEMA = "scythe.rf-channelized-product.v1"

# The FIR is declared, not improvised. A product is only comparable with another
# product that went through the same filter, so the design and its revision are
# part of the product's identity and part of its digest.
FIR_DESIGN = "KAISER_WINDOWED_SINC_LOWPASS"
FIR_TAPS = 129
FIR_KAISER_BETA = 8.6            # ~ -90 dB stopband for this beta
FIR_STOPBAND_ATTENUATION_DB = 90.0
METHOD_REVISION = "rf-channelizer-fir.v1"

# The DC spike of a zero-IF receiver sits at the capture centre. This module does
# not notch it; it declares that it does not, and refuses a channel that would
# straddle it. A silent notch deletes real signal at an arbitrary frequency.
DC_NOTCH = "NONE_DECLARED_NOT_APPLIED"
DC_GUARD_HZ = 2_000.0

# A channel must be sampled at least this far above its own bandwidth.
MIN_OVERSAMPLE = 2.0
# Selected channel width as a multiple of the coarse occupancy estimate, so the
# filter edges do not clip the shoulders the estimate found.
CHANNEL_MARGIN = 1.25

# Coarse occupancy: walk out from the peak until the spectrum falls this far.
#
# The walk runs over a Welch-averaged spectrum, not a single periodogram. A raw
# periodogram of a noise-like signal has ~5.6 dB of bin-to-bin variation, so a
# 20 dB walk over one crosses the floor in a spectral null a few bins from the
# peak and reports a 40 kHz channel as 234 Hz wide. Averaging costs nothing here
# and is the difference between measuring the signal and measuring the variance.
OCCUPANCY_FLOOR_DB = 20.0
# Segments are sized to give at least this many averages, then clamped so the
# resolution stays useful for narrow signals.
OCCUPANCY_TARGET_AVERAGES = 32
OCCUPANCY_SEGMENT_MIN = 256
OCCUPANCY_SEGMENT_MAX = 8_192
# The edge is where the spectrum falls below the floor *and stays there*. A
# single bin below the floor is a null, not a shoulder.
OCCUPANCY_RUN_BINS = 3
COARSE_FFT_MIN = 512

# Timestamps must actually describe the samples they are attached to.
TIMING_TOLERANCE_RATIO = 0.02

MIN_OUTPUT_SAMPLES = 64

# --- SNR: excess occupied power over a locally measured, filter-valid floor ---
#
# The first implementation took the noise floor as the median of every bin
# outside the occupied region.  Most of those bins lie outside the channelizer's
# own passband, where this FIR has already crushed them by ~90 dB, so the median
# landed in the stopband and the published SNR measured the filter's rejection
# rather than the signal.  Measured against synthetic ground truth it read 108.7
# dB for a true 20 dB channel: a fixed ~88 dB overstatement.  The median -- chosen
# so a second emitter could not quietly raise the floor -- is precisely what
# guaranteed the stopband won, because stopband bins outnumber real noise bins.
#
# The floor is therefore estimated only from bins the filter left alone, and the
# in-band noise those bins imply is subtracted from the occupied power before the
# ratio is taken.  At 20-40 dB the subtraction barely moves the number; near a
# detection threshold it is the difference between measuring a signal and
# counting noise energy as one.
SNR_BASIS = "OCCUPIED_EXCESS_POWER_OVER_LOCAL_PASSBAND_NOISE_V1"
SNR_AUTHORITY = "DERIVED_MEASUREMENT"
SNR_NOISE_ESTIMATOR = "MEDIAN_LINEAR_POWER"
# Advanced whenever the definition changes.  Products carrying different
# revisions are not numerically comparable and this is part of the product
# digest so that they cannot be silently pooled.
SNR_MEASUREMENT_REVISION = "passband-local-excess-power.v1"

# A reference bin must sit where the declared FIR is still flat.  Measured on the
# shipped design (129 taps, Kaiser beta 8.6) the droop against the cutoff is
# 0.00 dB at 0.80, 0.02 dB at 0.85, 0.37 dB at 0.90, 1.95 dB at 0.95 and 5.98 dB
# at the cutoff itself.  Reference bins taken from the skirt are attenuated
# noise, which biases the floor low and the SNR high -- a smaller version of the
# error this replaces.
PASSBAND_REFERENCE_FRACTION = 0.85
# The occupancy walk declares an edge only after OCCUPANCY_RUN_BINS bins have
# stayed below the floor, so those bins are the signal's own transition and are
# not reference material.
NOISE_REFERENCE_GUARD_BINS = OCCUPANCY_RUN_BINS
NOISE_REFERENCE_MIN_TOTAL = 32
NOISE_REFERENCE_MIN_PER_SIDE = 8

# A prior definition measured the noise floor over every bin outside the occupied
# region, most of which lie in this FIR's stopband. Products carrying that basis
# overstate SNR by roughly the filter's rejection, and the error is not a constant:
# it depends on filter rejection, spectrum geometry and occupancy, and only looked
# fixed across one sweep. They are not correctable and must never be pooled with
# products carrying the current basis.
SUPERSEDED_SNR_BASES: Dict[str, str] = {
    "OCCUPIED_POWER_OVER_ALL_OUT_OF_BAND_MEDIAN": (
        "INVALID -- NOT COMPARABLE. THE NOISE REFERENCE INCLUDED BINS INSIDE THE "
        "CHANNELIZER'S OWN FIR STOPBAND, SO THE PUBLISHED FIGURE MEASURED FILTER "
        "REJECTION RATHER THAN THE SIGNAL. NO CORRECTION FACTOR EXISTS"
    ),
}

# Occupancy has its own verdicts, with their own names. The transformation-level
# OUTCOMES entry of a similar name is a *selection* failure -- no width could be
# found to cut to, so no channel exists. These are *measurement* failures on a
# channel that was cut perfectly well.
#
# The second one exists because the walk will happily close on the filter. Below
# roughly 0 dB the channel's own FIR skirt falls 20 dB before anything else does,
# so the walk returns the transition band and reports the channelizer's passband
# as the signal's occupied bandwidth -- a measurement of the instrument, in the
# same family as the stopband SNR error. Observed at -10 dB in a 250 kHz channel:
# 285 kHz "occupied", wider than the channel it came out of.
OCCUPANCY_REASON_CODES: Dict[str, str] = {
    "WALK_REACHED_ANALYSIS_EDGE": (
        "THE OCCUPANCY WALK REACHED THE EDGE OF THE ANALYSIS SPAN WITHOUT THE "
        "SPECTRUM FALLING TO THE FLOOR. THE ANALYSIS SPAN IS A CEILING, NOT A "
        "MEASUREMENT, AND IS NOT PUBLISHED AS ONE"
    ),
    "OCCUPANCY_EXCEEDS_FLAT_PASSBAND": (
        "THE WALK CLOSED OUTSIDE THE REGION WHERE THIS CHANNEL'S FIR IS FLAT, SO "
        "THE EDGE IT FOUND IS THE FILTER'S SKIRT RATHER THAN THE SIGNAL'S "
        "SHOULDER. THE WIDTH WOULD DESCRIBE THE CHANNELIZER, NOT THE EMITTER"
    ),
}

# Measurement failure is not transformation failure.  These are a separate
# namespace from OUTCOMES on purpose: a channelization can be entirely valid
# while its SNR is unresolved, and collapsing the two would throw away a good
# product because one number could not be defended.
SNR_REASON_CODES: Dict[str, str] = {
    "INSUFFICIENT_CLEAN_REFERENCE_BINS": (
        "TOO FEW BINS LIE INSIDE THE FLAT PASSBAND AND OUTSIDE THE OCCUPIED "
        "REGION TO ESTIMATE A NOISE FLOOR THAT IS NOT THE FILTER'S OWN SKIRT"
    ),
    "OCCUPIED_POWER_NOT_ABOVE_NOISE": (
        "THE OCCUPIED BINS CARRY NO MORE POWER THAN THE REFERENCE FLOOR IMPLIES "
        "FOR THAT MANY BINS, SO NO EXCESS POWER IS ATTRIBUTABLE TO A SIGNAL"
    ),
    "CHANNEL_EDGE_LIMITED": (
        "THE OCCUPANCY WALK REACHED THE ANALYSIS EDGE, SO THERE IS NO REGION "
        "OUTSIDE THE SIGNAL FROM WHICH TO TAKE A REFERENCE"
    ),
    # Reserved. The conditions below are real and worth naming before something
    # needs them, but nothing here detects them yet and none of these is ever
    # emitted. A reserved code is a name, not a claim that a test exists.
    "REFERENCE_BINS_ASYMMETRIC": (
        "RESERVED -- NOT EMITTED. THE TWO REFERENCE SIDES DISAGREE BY MORE THAN "
        "A THRESHOLD THAT HAS NOT BEEN SET. THE DISAGREEMENT IS PUBLISHED AS A "
        "MEASUREMENT SO A THRESHOLD CAN LATER BE CHOSEN FROM EVIDENCE"
    ),
    "REFERENCE_CONTAMINATED": (
        "RESERVED -- NOT EMITTED. AN EMITTER OCCUPIES THE REFERENCE REGION. NO "
        "DETECTOR FOR THIS EXISTS"
    ),
    "INPUT_CLIPPED": (
        "RESERVED -- NOT EMITTED. THE CONVERTER SATURATED, SO THE MEASURED "
        "SPECTRUM IS OF A CLIPPED SIGNAL. NO CLIPPING DETECTOR EXISTS"
    ),
    "DC_EXCLUSION_REMOVED_REFERENCE": (
        "RESERVED -- NOT EMITTED. THE DC EXCLUSION REMOVED THE BINS THE "
        "REFERENCE NEEDED. THIS IS REPORTED AS INSUFFICIENT CLEAN REFERENCE "
        "BINS UNTIL THE DISTINCTION IS SHOWN TO MATTER"
    ),
}

# Only these three are ever produced. The rest of SNR_REASON_CODES are names
# reserved ahead of the detectors that would emit them.
_EMITTED_SNR_REASON_CODES: Tuple[str, ...] = (
    "INSUFFICIENT_CLEAN_REFERENCE_BINS",
    "OCCUPIED_POWER_NOT_ABOVE_NOISE",
    "CHANNEL_EDGE_LIMITED",
)

DIGEST_ALGORITHM = "blake2s"

OUTCOMES: Dict[str, str] = {
    "CHANNELIZED": "A CHANNEL WAS ISOLATED AND A BOUNDED PRODUCT PRODUCED",
    "INSUFFICIENT_WINDOW": (
        "FEWER USABLE SAMPLES THAN THE FILTER AND DECIMATION REQUIRE ONCE "
        "TRANSIENTS ARE DISCARDED"
    ),
    "TARGET_OUTSIDE_CAPTURE_SPAN": (
        "THE REQUESTED FREQUENCY LIES OUTSIDE THE SPAN THIS WINDOW ACTUALLY "
        "COVERS. A CAPTURE CANNOT BE ASKED ABOUT A FREQUENCY IT DID NOT SAMPLE"
    ),
    "OCCUPIED_BANDWIDTH_UNRESOLVED": (
        "THE COARSE OCCUPANCY WALK REACHED THE ANALYSIS EDGE WITHOUT THE "
        "SPECTRUM FALLING TO THE FLOOR, SO NO CHANNEL WIDTH IS DEFENSIBLE"
    ),
    "CHANNEL_EDGE_TRUNCATED": (
        "THE SELECTED CHANNEL EXTENDS PAST THE EDGE OF THE CAPTURE SPAN. THE "
        "PRODUCT WOULD BE A PARTIAL SIGNAL PRESENTED AS A WHOLE ONE"
    ),
    "DC_CONTAMINATION": (
        "THE SELECTED CHANNEL STRADDLES THE CAPTURE CENTRE, WHERE A ZERO-IF "
        "RECEIVER'S DC ARTEFACT SITS. NO NOTCH IS APPLIED, SO THE CHANNEL IS "
        "REFUSED RATHER THAN SILENTLY EDITED"
    ),
    "ALIAS_RISK": (
        "THE DECIMATION RATIO WOULD SAMPLE THE REQUESTED BANDWIDTH BELOW "
        "NYQUIST. ENERGY OUTSIDE THE NEW RATE WOULD FOLD IN AND BE "
        "INDISTINGUISHABLE FROM SIGNAL"
    ),
    "TIMING_QUALITY_INSUFFICIENT": (
        "THE WINDOW'S DURATION AND ITS SAMPLE COUNT DISAGREE AT THE DECLARED "
        "RATE, SO ITS TIMESTAMPS DO NOT DESCRIBE ITS SAMPLES"
    ),
    "SOURCE_WINDOW_EXPIRED": (
        "THE SOURCE WINDOW NO LONGER EXISTS IN THE RING: ITS EPOCH HAS MOVED OR "
        "ITS SAMPLES HAVE BEEN OVERWRITTEN"
    ),
    "SOURCE_WINDOW_UNVERIFIED": (
        "THE SOURCE WINDOW WAS NEVER ISSUED BY THIS RING, OR ITS DIGEST DOES NOT "
        "MATCH THE ONE ISSUED. THIS IS A FORGERY, NOT AN EXPIRY"
    ),
    "SIGNAL_CHAIN_CHANGED": (
        "THE WINDOW WAS CAPTURED UNDER A DIFFERENT SIGNAL CHAIN THAN THE ONE "
        "REQUESTED. PRODUCTS THROUGH DIFFERENT ANTENNAS ARE NOT COMPARABLE"
    ),
}

REFUSAL_OUTCOMES: Tuple[str, ...] = tuple(
    code for code in OUTCOMES if code != "CHANNELIZED")

# A ring verification failure maps to an outcome that keeps the distinction the
# ring drew. An unissued window and an evicted one are different problems, and
# collapsing them would report a forgery as a timing inconvenience.
_VERIFICATION_OUTCOMES: Dict[str, str] = {
    "EPOCH_CHANGED": "SOURCE_WINDOW_EXPIRED",
    "WINDOW_EVICTED": "SOURCE_WINDOW_EXPIRED",
    "RING_CLOSED": "SOURCE_WINDOW_EXPIRED",
    "WINDOW_NOT_ISSUED": "SOURCE_WINDOW_UNVERIFIED",
    "DIGEST_MISMATCH": "SOURCE_WINDOW_UNVERIFIED",
}


@dataclass(frozen=True)
class ChannelRequest:
    """What the caller wants isolated, and what it believes about the capture.

    ``capture_center_hz`` is the receiver's tuned centre; the window carries the
    sample rate, which is measured and is never taken from here.
    """

    capture_center_hz: float
    target_frequency_hz: float
    expected_signal_chain_hash: Optional[str] = None
    expected_configuration_epoch: Optional[int] = None
    # An explicit ratio is honoured only if it does not alias. Left None, the
    # channelizer picks the largest ratio that keeps MIN_OVERSAMPLE.
    decimation: Optional[int] = None
    # A caller-supplied channel width, used when the coarse occupancy walk cannot
    # find one. Below roughly 20 dB in-channel SNR the walk never falls its 20 dB
    # and the selection fails, which would deny a cyclostationary detector exactly
    # the windows it exists to work in: those methods find structure beneath the
    # level where spectral occupancy succeeds. A requested width lets the channel
    # be cut anyway, and `channel_selection_basis` records that the width was
    # asked for rather than measured.
    channel_bandwidth_hz: Optional[float] = None


@dataclass(frozen=True)
class ChannelizedProduct:
    """The bounded, serializable record of one channelization attempt.

    Measurements and verdict metadata only.  There is no field on this class that
    holds baseband, and no code path that puts one there.
    """

    schema: str
    product_id: str
    product_digest: str
    source_window_id: str
    source_window_digest: str
    configuration_epoch: int
    signal_chain_hash: str

    center_frequency_hz: float
    sample_rate_hz: float

    # Stage 1: the coarse candidate the selection was derived from.
    candidate_center_hz: Optional[float]
    candidate_bandwidth_hz: Optional[float]
    candidate_method: str

    # Stage 2: what was actually cut.
    channel_center_hz: Optional[float]
    channel_bandwidth_hz: Optional[float]
    output_sample_rate_hz: Optional[float]
    decimation: Optional[int]

    sample_count: int
    transient_samples_discarded: int
    # Measured on the channelized output. Not independent of the selection above,
    # and `occupied_bandwidth_basis` says so.
    occupied_bandwidth_hz: Optional[float]
    occupied_bandwidth_basis: str
    # None when the walk closed inside the channel; a reason when it did not.
    # Separate from `outcome`, because a channel can be cut perfectly well and
    # still not have a defensible width.
    occupancy_reason_code: Optional[str]
    # Whether the channel width was measured or asked for. A requested width is
    # not evidence about the signal, and a product must not read as though it were.
    channel_selection_basis: str
    # Where the DDC was tuned, and where the carrier actually turned out to be.
    # Two different quantities; conflating them hides tuning error as signal.
    tuning_offset_hz: Optional[float]
    frequency_offset_hz: Optional[float]

    # SNR and everything needed to know what it means. `snr_db` alone is not a
    # measurement: without the basis, the estimator and the reference geometry it
    # is a number whose definition has to be guessed from the source.
    snr_db: Optional[float]
    snr_basis: str
    snr_authority: str
    snr_measurement_revision: str
    # None when snr_db is a number; a SNR_REASON_CODES key when it is not.
    snr_reason_code: Optional[str]
    # DEGRADED_ONE_SIDED when the floor came from one side of the signal only.
    snr_quality: Optional[str]
    noise_estimator: str
    noise_reference_bin_count: Optional[int]
    noise_reference_bandwidth_hz: Optional[float]
    noise_reference_guard_bins: int
    noise_reference_sides: str
    noise_reference_left_bins: Optional[int]
    noise_reference_right_bins: Optional[int]
    # Published, not gated. A large left/right disagreement can mean an adjacent
    # emitter, a sloping frontend response or a wrongly bounded occupied region,
    # but no threshold has been set from evidence, so nothing is refused on it.
    noise_reference_side_disagreement_db: Optional[float]

    outcome: str
    reason_code: str
    method_revision: str
    fir_design: str = FIR_DESIGN
    fir_taps: int = FIR_TAPS
    fir_stopband_attenuation_db: float = FIR_STOPBAND_ATTENUATION_DB
    dc_notch: str = DC_NOTCH
    amplitude_normalization: str = "NONE"
    raw_iq_exposed: bool = False

    @property
    def channelized(self) -> bool:
        return self.outcome == "CHANNELIZED"

    @property
    def reason(self) -> str:
        return OUTCOMES.get(self.reason_code, self.reason_code)

    # Three questions, three answers, published apart. "Was a valid channel
    # produced", "could its width be resolved" and "could its SNR be resolved"
    # are different facts, and a consumer that reads one for another will treat a
    # missing measurement as a failed transformation -- or worse, as a zero.
    _OCCUPANCY_FIELDS = ("occupied_bandwidth_hz", "occupied_bandwidth_basis",
                         "occupancy_reason_code")
    _SNR_FIELDS = ("snr_db", "snr_basis", "snr_authority", "snr_measurement_revision",
                   "snr_reason_code", "snr_quality", "noise_estimator",
                   "noise_reference_bin_count", "noise_reference_bandwidth_hz",
                   "noise_reference_guard_bins", "noise_reference_sides",
                   "noise_reference_left_bins", "noise_reference_right_bins",
                   "noise_reference_side_disagreement_db")
    _TRANSFORMATION_FIELDS = ("outcome", "reason_code")

    def to_dict(self) -> Dict[str, Any]:
        """The product as published. Grouped, and each fact appears once."""
        grouped = set(self._OCCUPANCY_FIELDS + self._SNR_FIELDS
                      + self._TRANSFORMATION_FIELDS)
        payload = {field: getattr(self, field) for field in self.__dataclass_fields__
                   if field not in grouped}
        payload["transformation"] = {
            **{field: getattr(self, field) for field in self._TRANSFORMATION_FIELDS},
            "reason": self.reason,
            # The one question a detector may ask before consuming samples.
            "channelized": self.channelized,
        }
        payload["occupancy"] = {
            "bandwidth_hz": self.occupied_bandwidth_hz,
            "basis": self.occupied_bandwidth_basis,
            "reason_code": self.occupancy_reason_code,
        }
        payload["snr"] = {field: getattr(self, field) for field in self._SNR_FIELDS}
        return payload


@dataclass(frozen=True)
class Channelization:
    """A product plus its process-local baseband, which never leaves the process."""

    product: ChannelizedProduct
    samples: Optional[np.ndarray]

    def __bool__(self) -> bool:
        return self.product.channelized

    def __repr__(self) -> str:
        count = 0 if self.samples is None else self.samples.size
        return (f"Channelization(outcome={self.product.outcome!r}, "
                f"product_id={self.product.product_id!r}, "
                f"samples=<{count} complex64 samples withheld>)")

    def __reduce__(self):
        raise RawIQNotTransportable(
            "a Channelization carries baseband IQ and is not serializable; "
            "publish product.to_dict() instead")


def _digest(*parts: Any) -> str:
    hasher = hashlib.blake2s(digest_size=32)
    for part in parts:
        hasher.update(str(part).encode())
        hasher.update(b"|")
    return f"{DIGEST_ALGORITHM}:{hasher.hexdigest()}"


def design_lowpass(cutoff_normalized: float, taps: int = FIR_TAPS,
                   beta: float = FIR_KAISER_BETA) -> np.ndarray:
    """Kaiser-windowed sinc, normalized to unity DC gain.

    Unity DC gain is the amplitude guarantee: a tone that enters the passband at
    amplitude A leaves at amplitude A, so products from different windows stay
    comparable without any normalization step.
    """
    if not 0.0 < cutoff_normalized < 0.5:
        raise ValueError("cutoff_normalized must lie in (0, 0.5)")
    n = np.arange(taps) - (taps - 1) / 2.0
    h = 2.0 * cutoff_normalized * np.sinc(2.0 * cutoff_normalized * n)
    h *= np.kaiser(taps, beta)
    return (h / h.sum()).astype(np.float64)


def welch_power_db(samples: np.ndarray, segment: Optional[int] = None) -> Tuple[np.ndarray, int]:
    """Averaged, fftshifted power spectrum in dB. Returns ``(power_db, segment)``.

    Hann-windowed segments with 50% overlap.  The segment length is chosen to
    reach ``OCCUPANCY_TARGET_AVERAGES`` averages where the window allows, then
    clamped so a narrow signal does not collapse into a single bin.
    """
    count = int(samples.size)
    if segment is None:
        ideal = 1 << max(int(count // OCCUPANCY_TARGET_AVERAGES).bit_length() - 1, 0)
        segment = int(min(OCCUPANCY_SEGMENT_MAX, max(OCCUPANCY_SEGMENT_MIN, ideal)))
    segment = min(segment, count)
    if segment < 64:
        return np.empty(0), 0
    window = np.hanning(segment)
    hop = max(1, segment // 2)
    accumulator = np.zeros(segment, dtype=np.float64)
    averages = 0
    for start in range(0, count - segment + 1, hop):
        block = samples[start:start + segment] * window
        accumulator += np.abs(np.fft.fft(block)) ** 2
        averages += 1
    if averages == 0:
        return np.empty(0), 0
    power = np.fft.fftshift(accumulator / averages)
    return 10.0 * np.log10(power + 1e-20), segment


def _walk_occupancy(power_db: np.ndarray, peak: int) -> Optional[Tuple[int, int]]:
    """Bin indices where the spectrum falls below the floor and stays below it.

    Returns ``None`` when either walk reaches the analysis edge -- which means the
    signal is wider than this window can characterise, not that it is narrow.
    """
    size = power_db.size
    floor_db = power_db[peak] - OCCUPANCY_FLOOR_DB

    left = peak
    run = 0
    while left > 0:
        left -= 1
        run = run + 1 if power_db[left] <= floor_db else 0
        if run >= OCCUPANCY_RUN_BINS:
            left += OCCUPANCY_RUN_BINS - 1
            break
    else:
        return None

    right = peak
    run = 0
    while right < size - 1:
        right += 1
        run = run + 1 if power_db[right] <= floor_db else 0
        if run >= OCCUPANCY_RUN_BINS:
            right -= OCCUPANCY_RUN_BINS - 1
            break
    else:
        return None

    if left <= 0 or right >= size - 1:
        return None
    return left, right


def estimate_occupied_bandwidth(samples: np.ndarray, sample_rate_hz: float,
                                capture_center_hz: float, target_hz: float,
                                ) -> Tuple[Optional[float], Optional[float], str]:
    """Stage 1. Coarse occupancy around ``target_hz``, on the unchannelized window.

    Returns ``(centre_hz, bandwidth_hz, method)``, with ``(None, None, method)``
    when the occupancy does not close inside the analysed span.
    """
    method = (f"WELCH_PEAK_MINUS_{OCCUPANCY_FLOOR_DB:.0f}DB_WALK_"
              f"RUN{OCCUPANCY_RUN_BINS}")
    if samples.size < COARSE_FFT_MIN:
        return None, None, method
    power_db, segment = welch_power_db(samples)
    if segment == 0:
        return None, None, method

    bin_hz = sample_rate_hz / segment
    frequencies = capture_center_hz + (np.arange(segment) - segment // 2) * bin_hz
    target_bin = int(np.argmin(np.abs(frequencies - target_hz)))
    # Peak within a small neighbourhood of the request, so a detection reported a
    # few bins off still lands on its own carrier rather than on a neighbour.
    span = max(4, segment // 64)
    low, high = max(0, target_bin - span), min(segment, target_bin + span + 1)
    peak = int(low + np.argmax(power_db[low:high]))

    edges = _walk_occupancy(power_db, peak)
    if edges is None:
        return None, None, method
    left, right = edges
    centre = float(0.5 * (frequencies[left] + frequencies[right]))
    bandwidth = float((right - left) * bin_hz)
    if bandwidth <= 0:
        return None, None, method
    return centre, bandwidth, method


def _refuse(window: IQWindow, request: ChannelRequest, outcome: str, *,
            candidate: Tuple[Optional[float], Optional[float], str] = (None, None, "NOT_ATTEMPTED"),
            channel_center: Optional[float] = None,
            channel_bandwidth: Optional[float] = None) -> Channelization:
    """A refusal is a complete product, not a missing one."""
    candidate_center, candidate_bandwidth, candidate_method = candidate
    digest = _digest(SCHEMA, window.window_id, window.digest, window.configuration_epoch,
                     request.capture_center_hz, request.target_frequency_hz,
                     METHOD_REVISION, SNR_MEASUREMENT_REVISION, outcome)
    product = ChannelizedProduct(
        schema=SCHEMA,
        product_id=f"chp-{hashlib.blake2s(digest.encode(), digest_size=8).hexdigest()}",
        product_digest=digest,
        source_window_id=window.window_id,
        source_window_digest=window.digest,
        configuration_epoch=window.configuration_epoch,
        signal_chain_hash=window.signal_chain_hash,
        center_frequency_hz=request.capture_center_hz,
        sample_rate_hz=window.sample_rate_hz,
        candidate_center_hz=candidate_center,
        candidate_bandwidth_hz=candidate_bandwidth,
        candidate_method=candidate_method,
        channel_center_hz=channel_center,
        channel_bandwidth_hz=channel_bandwidth,
        output_sample_rate_hz=None,
        decimation=None,
        sample_count=0,
        transient_samples_discarded=0,
        occupied_bandwidth_hz=None,
        occupied_bandwidth_basis="NOT_MEASURED",
        occupancy_reason_code=None,
        channel_selection_basis="NOT_SELECTED",
        tuning_offset_hz=None,
        frequency_offset_hz=None,
        # Not attempted rather than unresolved: there is no channel to measure.
        # The transformation outcome above is the reason, and duplicating it into
        # the measurement namespace would blur the distinction the two keep.
        **_snr_fields(sides="NOT_ATTEMPTED", attempted=False),
        outcome=outcome,
        reason_code=outcome,
        method_revision=METHOD_REVISION,
    )
    return Channelization(product, None)


def channelize(window: IQWindow, request: ChannelRequest, *,
               ring=None) -> Channelization:
    """Isolate one channel from ``window``. Never raises for a bad request.

    ``ring`` is verified against immediately before processing, so a window whose
    epoch moved between acquisition and use is refused even though the caller
    still holds a structurally valid object with a matching digest.
    """
    # --- provenance, before any arithmetic -----------------------------------
    if ring is not None:
        verification = ring.verify_window(window.window_id, window.digest)
        if not verification:
            return _refuse(window, request,
                           _VERIFICATION_OUTCOMES.get(verification.reason_code,
                                                      "SOURCE_WINDOW_UNVERIFIED"))
    expected_epoch = request.expected_configuration_epoch
    if expected_epoch is not None and expected_epoch != window.configuration_epoch:
        # Checked separately from the digest: a window can be byte-identical to
        # what was issued and still belong to a configuration that has gone.
        return _refuse(window, request, "SOURCE_WINDOW_EXPIRED")
    expected_chain = request.expected_signal_chain_hash
    if expected_chain is not None and expected_chain != window.signal_chain_hash:
        return _refuse(window, request, "SIGNAL_CHAIN_CHANGED")

    # --- the window must describe itself consistently ------------------------
    rate = float(window.sample_rate_hz)          # measured, never from the request
    if not rate > 0 or window.sample_count <= 0:
        return _refuse(window, request, "TIMING_QUALITY_INSUFFICIENT")
    implied = window.sample_count / rate
    if window.duration_s <= 0 or abs(window.duration_s - implied) > implied * TIMING_TOLERANCE_RATIO:
        return _refuse(window, request, "TIMING_QUALITY_INSUFFICIENT")

    samples = np.asarray(window.samples, dtype=np.complex64)
    # Long enough to survive the filter *and* long enough to characterise. A
    # window too short for the coarse estimate is an insufficient window, not an
    # unresolvable signal, and saying the latter would blame the emitter.
    if samples.size < max(FIR_TAPS + MIN_OUTPUT_SAMPLES, COARSE_FFT_MIN):
        return _refuse(window, request, "INSUFFICIENT_WINDOW")

    span_low = request.capture_center_hz - rate / 2.0
    span_high = request.capture_center_hz + rate / 2.0
    if not span_low <= request.target_frequency_hz <= span_high:
        return _refuse(window, request, "TARGET_OUTSIDE_CAPTURE_SPAN")

    # --- stage 1: coarse candidate, recorded in its own fields ---------------
    candidate = estimate_occupied_bandwidth(
        samples, rate, request.capture_center_hz, request.target_frequency_hz)
    candidate_center, candidate_bandwidth, _ = candidate
    resolved = (candidate_center is not None
                and bool(candidate_bandwidth) and candidate_bandwidth > 0)
    requested_bandwidth = request.channel_bandwidth_hz
    if requested_bandwidth is not None and not requested_bandwidth > 0:
        return _refuse(window, request, "ALIAS_RISK", candidate=candidate)
    if not resolved and requested_bandwidth is None:
        # No measured width and none asked for: there is nothing to cut to. This
        # is a selection failure, not a measurement one, and stays an outcome.
        return _refuse(window, request, "OCCUPIED_BANDWIDTH_UNRESOLVED", candidate=candidate)

    # --- stage 2: selection derived from stage 1, then checked ---------------
    if requested_bandwidth is not None:
        # The request wins even when the walk succeeded, so a caller sweeping a
        # fixed width gets that width and not a per-window one. What the walk
        # found is still recorded in the candidate fields either way.
        channel_center = candidate_center if resolved else request.target_frequency_hz
        channel_bandwidth = float(requested_bandwidth)
        selection_basis = "OPERATOR_REQUESTED_WIDTH"
    else:
        channel_center = candidate_center
        channel_bandwidth = candidate_bandwidth * CHANNEL_MARGIN
        selection_basis = "DERIVED_FROM_COARSE_OCCUPANCY"
    edges = (channel_center - channel_bandwidth / 2.0, channel_center + channel_bandwidth / 2.0)
    if edges[0] < span_low or edges[1] > span_high:
        return _refuse(window, request, "CHANNEL_EDGE_TRUNCATED", candidate=candidate,
                       channel_center=channel_center, channel_bandwidth=channel_bandwidth)
    if (edges[0] - DC_GUARD_HZ) <= request.capture_center_hz <= (edges[1] + DC_GUARD_HZ):
        return _refuse(window, request, "DC_CONTAMINATION", candidate=candidate,
                       channel_center=channel_center, channel_bandwidth=channel_bandwidth)

    if channel_bandwidth * MIN_OVERSAMPLE > rate:
        return _refuse(window, request, "ALIAS_RISK", candidate=candidate,
                       channel_center=channel_center, channel_bandwidth=channel_bandwidth)
    if request.decimation is not None:
        decimation = int(request.decimation)
        if decimation < 1 or rate / decimation < channel_bandwidth * MIN_OVERSAMPLE:
            return _refuse(window, request, "ALIAS_RISK", candidate=candidate,
                           channel_center=channel_center, channel_bandwidth=channel_bandwidth)
    else:
        # Bounded by two things, not one. Nyquist sets the largest ratio that
        # does not alias; the window length sets the largest ratio that still
        # leaves a usable number of output samples. Taking only the first turns a
        # narrow candidate into a 39-sample product and then reports the window
        # as too short, which blames the wrong thing.
        by_nyquist = max(1, int(math.floor(rate / (channel_bandwidth * MIN_OVERSAMPLE))))
        by_length = max(1, (samples.size - FIR_TAPS + 1) // MIN_OUTPUT_SAMPLES)
        decimation = min(by_nyquist, by_length)
    output_rate = rate / decimation

    # --- DDC, filter, decimate ----------------------------------------------
    tuning_offset = channel_center - request.capture_center_hz
    n = np.arange(samples.size, dtype=np.float64)
    mixed = samples * np.exp(-2j * np.pi * tuning_offset * n / rate).astype(np.complex64)

    taps = design_lowpass(min(0.499, (channel_bandwidth / 2.0) / rate))
    # 'valid' discards the filter transient outright rather than reporting a
    # usable count that includes samples the filter had not settled on.
    filtered = np.convolve(mixed, taps.astype(np.complex64), mode="valid")
    transient_discarded = samples.size - filtered.size
    decimated = np.ascontiguousarray(filtered[::decimation], dtype=np.complex64)
    if decimated.size < MIN_OUTPUT_SAMPLES:
        return _refuse(window, request, "INSUFFICIENT_WINDOW", candidate=candidate,
                       channel_center=channel_center, channel_bandwidth=channel_bandwidth)
    decimated.setflags(write=False)

    # --- measurements on the product ----------------------------------------
    occupied_hz, carrier_offset_hz, snr, occupancy_reason = _measure(
        decimated, output_rate, channel_bandwidth_hz=channel_bandwidth,
        tuning_offset_hz=tuning_offset)

    digest = _digest(SCHEMA, window.window_id, window.digest, window.configuration_epoch,
                     window.signal_chain_hash, request.capture_center_hz,
                     request.target_frequency_hz, round(channel_center, 3),
                     round(channel_bandwidth, 3), decimation, FIR_DESIGN, FIR_TAPS,
                     METHOD_REVISION, SNR_MEASUREMENT_REVISION, "CHANNELIZED")
    product = ChannelizedProduct(
        schema=SCHEMA,
        product_id=f"chp-{hashlib.blake2s(digest.encode(), digest_size=8).hexdigest()}",
        product_digest=digest,
        source_window_id=window.window_id,
        source_window_digest=window.digest,
        configuration_epoch=window.configuration_epoch,
        signal_chain_hash=window.signal_chain_hash,
        center_frequency_hz=request.capture_center_hz,
        sample_rate_hz=rate,
        candidate_center_hz=candidate_center,
        candidate_bandwidth_hz=candidate_bandwidth,
        candidate_method=candidate[2],
        channel_center_hz=channel_center,
        channel_bandwidth_hz=channel_bandwidth,
        output_sample_rate_hz=output_rate,
        decimation=decimation,
        sample_count=int(decimated.size),
        transient_samples_discarded=int(transient_discarded),
        occupied_bandwidth_hz=occupied_hz,
        # The selection came from this same window, so the fit is not independent
        # evidence of the selection being right.
        occupied_bandwidth_basis=("SAME_WINDOW_AS_SELECTION" if occupancy_reason is None
                                  else "NOT_RESOLVED"),
        occupancy_reason_code=occupancy_reason,
        channel_selection_basis=selection_basis,
        tuning_offset_hz=tuning_offset,
        frequency_offset_hz=carrier_offset_hz,
        **snr,
        outcome="CHANNELIZED",
        reason_code="CHANNELIZED",
        method_revision=METHOD_REVISION,
    )
    return Channelization(product, decimated)


def _snr_fields(*, snr_db: Optional[float] = None, reason_code: Optional[str] = None,
                quality: Optional[str] = None, bin_count: Optional[int] = None,
                bandwidth_hz: Optional[float] = None, sides: str = "NONE",
                left: Optional[int] = None, right: Optional[int] = None,
                disagreement_db: Optional[float] = None,
                attempted: bool = True) -> Dict[str, Any]:
    """One SNR verdict with the geometry it was measured over.

    ``attempted`` is False only where there is no channel to measure at all -- a
    refused channelization -- so an unmeasurable SNR and an unattempted one do not
    both appear as a bare null with the same basis.
    """
    return {
        "snr_db": snr_db,
        "snr_basis": SNR_BASIS if attempted else "NOT_MEASURED",
        "snr_authority": SNR_AUTHORITY,
        "snr_measurement_revision": SNR_MEASUREMENT_REVISION,
        "snr_reason_code": reason_code,
        "snr_quality": quality,
        "noise_estimator": SNR_NOISE_ESTIMATOR if attempted else "NOT_MEASURED",
        "noise_reference_bin_count": bin_count,
        "noise_reference_bandwidth_hz": bandwidth_hz,
        "noise_reference_guard_bins": NOISE_REFERENCE_GUARD_BINS,
        "noise_reference_sides": sides,
        "noise_reference_left_bins": left,
        "noise_reference_right_bins": right,
        "noise_reference_side_disagreement_db": disagreement_db,
    }


def _dc_offset_in_channel(tuning_offset_hz: float, output_rate_hz: float,
                          ) -> Optional[float]:
    """Where the capture centre's DC artefact lands in the decimated channel.

    The DDC moves the capture centre to ``-tuning_offset``; decimation then folds
    that into the new Nyquist span.  The FIR has already crushed it if it lies
    outside the passband, but the exclusion is applied from geometry rather than
    from an assumption about how well the filter worked.
    """
    if not output_rate_hz > 0:
        return None
    half = output_rate_hz / 2.0
    return ((-tuning_offset_hz + half) % output_rate_hz) - half


def _estimate_snr(power: np.ndarray, freqs: np.ndarray, bin_hz: float,
                  left: int, right: int, *, channel_bandwidth_hz: float,
                  dc_offset_hz: Optional[float]) -> Dict[str, Any]:
    """Excess occupied power over a locally measured, filter-valid noise floor.

        N0        = median of the linear power of the reference bins
        P_noise   = N0 * |O|          expected noise inside the occupied region
        P_signal  = max(sum(P_k for k in O) - P_noise, 0)
        SNR_dB    = 10 log10(P_signal / P_noise)

    Subtracting the expected in-band noise matters little at 20-40 dB and matters
    a great deal near a detection threshold, where it is the difference between
    measuring a signal and counting the noise sitting under it as one.

    The median is taken in linear power, not in dB: the median of a set of dB
    values is the dB of the median only because the transform is monotonic, but
    every other quantity here is a power sum, and mixing the two domains is how a
    factor arrives that nobody can later account for.
    """
    usable_edge = PASSBAND_REFERENCE_FRACTION * (channel_bandwidth_hz / 2.0)
    guard_hz = NOISE_REFERENCE_GUARD_BINS * bin_hz
    low_edge = freqs[left] - guard_hz
    high_edge = freqs[right] + guard_hz

    eligible = np.isfinite(power) & (power > 0.0)
    eligible &= np.abs(freqs) <= usable_edge
    eligible &= (freqs < low_edge) | (freqs > high_edge)
    if dc_offset_hz is not None:
        eligible &= np.abs(freqs - dc_offset_hz) > DC_GUARD_HZ

    lower = eligible & (freqs < low_edge)
    upper = eligible & (freqs > high_edge)
    n_left, n_right = int(lower.sum()), int(upper.sum())

    disagreement: Optional[float] = None
    if n_left and n_right:
        disagreement = round(abs(
            10.0 * math.log10(max(float(np.median(power[lower])), 1e-20))
            - 10.0 * math.log10(max(float(np.median(power[upper])), 1e-20))), 3)

    if (n_left + n_right >= NOISE_REFERENCE_MIN_TOTAL
            and n_left >= NOISE_REFERENCE_MIN_PER_SIDE
            and n_right >= NOISE_REFERENCE_MIN_PER_SIDE):
        mask, sides, quality = eligible, "BOTH", None
    # One-sided is permitted but never silent: it is declared in
    # `noise_reference_sides` and flagged in `snr_quality`, and it must clear the
    # full two-sided bin budget on the single side it does have.
    elif n_left >= NOISE_REFERENCE_MIN_TOTAL:
        mask, sides, quality = lower, "LEFT_ONLY", "DEGRADED_ONE_SIDED"
    elif n_right >= NOISE_REFERENCE_MIN_TOTAL:
        mask, sides, quality = upper, "RIGHT_ONLY", "DEGRADED_ONE_SIDED"
    else:
        return _snr_fields(reason_code="INSUFFICIENT_CLEAN_REFERENCE_BINS",
                           sides="NONE", left=n_left, right=n_right,
                           disagreement_db=disagreement)

    used = int(mask.sum())
    noise_floor = float(np.median(power[mask]))
    occupied_bins = right - left + 1
    noise_in_band = noise_floor * occupied_bins
    signal = float(power[left:right + 1].sum()) - noise_in_band
    if not signal > 0.0 or not noise_in_band > 0.0:
        return _snr_fields(reason_code="OCCUPIED_POWER_NOT_ABOVE_NOISE",
                           sides=sides, quality=quality, bin_count=used,
                           bandwidth_hz=round(used * bin_hz, 3),
                           left=n_left, right=n_right, disagreement_db=disagreement)
    return _snr_fields(snr_db=round(10.0 * math.log10(signal / noise_in_band), 3),
                       quality=quality, bin_count=used,
                       bandwidth_hz=round(used * bin_hz, 3), sides=sides,
                       left=n_left, right=n_right, disagreement_db=disagreement)


def _measure(channel: np.ndarray, output_rate_hz: float, *,
             channel_bandwidth_hz: float, tuning_offset_hz: float,
             ) -> Tuple[Optional[float], Optional[float], Dict[str, Any], Optional[str]]:
    """Occupied bandwidth, residual carrier offset and SNR of the channel.

    ``frequency_offset_hz`` is the carrier's position *within the channel*, which
    is a different quantity from ``tuning_offset_hz``: the first is a measurement
    of where the signal actually is, the second is a record of where the DDC was
    pointed. Reporting only their sum would hide selection error inside the
    measurement.
    """
    power_db, segment = welch_power_db(channel)
    if segment == 0:
        return None, None, _snr_fields(
            reason_code="INSUFFICIENT_CLEAN_REFERENCE_BINS", sides="NONE"), \
            "WALK_REACHED_ANALYSIS_EDGE"
    bin_hz = output_rate_hz / segment
    peak = int(np.argmax(power_db))
    power = np.power(10.0, power_db / 10.0)
    freqs = (np.arange(segment) - segment // 2) * bin_hz

    edges = _walk_occupancy(power_db, peak)
    if edges is None:
        # The signal fills the channel. Reporting the analysis span as the width
        # would publish a ceiling as a measurement, so the width is unresolved and
        # says so; the carrier position is still measurable and is still returned.
        return (None, float((peak - segment // 2) * bin_hz),
                _snr_fields(reason_code="CHANNEL_EDGE_LIMITED", sides="NONE"),
                "WALK_REACHED_ANALYSIS_EDGE")
    left, right = edges
    occupied = float((right - left) * bin_hz)
    # The same flat-passband boundary the noise reference uses, for the same
    # reason: past it, what the walk found is the filter and not the signal.
    flat_edge = PASSBAND_REFERENCE_FRACTION * channel_bandwidth_hz / 2.0
    if max(abs(freqs[left]), abs(freqs[right])) > flat_edge:
        return (None, float((peak - segment // 2) * bin_hz),
                _snr_fields(reason_code="INSUFFICIENT_CLEAN_REFERENCE_BINS",
                            sides="NONE"),
                "OCCUPANCY_EXCEEDS_FLAT_PASSBAND")

    # Power-weighted centroid over the occupied region, not the peak bin. The
    # peak bin of a noise-like band is wherever that realisation happened to be
    # loudest, which is a property of the noise and not of the emitter; the
    # centroid answers "where is this signal" for a tone and a band alike.
    indices = np.arange(left, right + 1)
    weights = power[left:right + 1]
    centroid = float((indices * weights).sum() / max(weights.sum(), 1e-20))
    offset = float((centroid - segment // 2) * bin_hz)

    snr = _estimate_snr(power, freqs, bin_hz, left, right,
                        channel_bandwidth_hz=channel_bandwidth_hz,
                        dc_offset_hz=_dc_offset_in_channel(tuning_offset_hz,
                                                           output_rate_hz))
    return occupied, offset, snr, None


def channelizer_status() -> Dict[str, Any]:
    """Declared capability and declared absences, for the status payload."""
    return {
        "schema": SCHEMA,
        "state": "IMPLEMENTED",
        "method_revision": METHOD_REVISION,
        "fir_design": FIR_DESIGN,
        "fir_taps": FIR_TAPS,
        "fir_stopband_attenuation_db": FIR_STOPBAND_ATTENUATION_DB,
        "dc_notch": DC_NOTCH,
        "amplitude_normalization": "NONE",
        "min_oversample": MIN_OVERSAMPLE,
        "channel_margin": CHANNEL_MARGIN,
        "occupancy_floor_db": OCCUPANCY_FLOOR_DB,
        "outcomes": dict(OUTCOMES),
        "refusal_outcomes": list(REFUSAL_OUTCOMES),
        # SNR is declared beside the outcomes but is deliberately not one of
        # them. An unresolved SNR does not refuse a channelization.
        "snr_basis": SNR_BASIS,
        "snr_authority": SNR_AUTHORITY,
        "snr_measurement_revision": SNR_MEASUREMENT_REVISION,
        "noise_estimator": SNR_NOISE_ESTIMATOR,
        "noise_reference_guard_bins": NOISE_REFERENCE_GUARD_BINS,
        "noise_reference_min_total_bins": NOISE_REFERENCE_MIN_TOTAL,
        "noise_reference_min_per_side_bins": NOISE_REFERENCE_MIN_PER_SIDE,
        "noise_reference_passband_fraction": PASSBAND_REFERENCE_FRACTION,
        "occupancy_reason_codes": dict(OCCUPANCY_REASON_CODES),
        "snr_reason_codes": dict(SNR_REASON_CODES),
        "snr_reason_codes_emitted": list(_EMITTED_SNR_REASON_CODES),
        "snr_reason_codes_reserved": [code for code in SNR_REASON_CODES
                                      if code not in _EMITTED_SNR_REASON_CODES],
        "superseded_snr_bases": dict(SUPERSEDED_SNR_BASES),
        # Phase 1d: the bridge issues verified windows and records bounded
        # products. Nothing yet consumes those products, which is a separate
        # claim and is reported separately.
        "bridge_integration": "INTEGRATED",
        "detector_integration": "NOT_IMPLEMENTED",
        "raw_iq_exposed": False,
        "baseband_transportable": False,
    }
