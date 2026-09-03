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
    # Where the DDC was tuned, and where the carrier actually turned out to be.
    # Two different quantities; conflating them hides tuning error as signal.
    tuning_offset_hz: Optional[float]
    frequency_offset_hz: Optional[float]
    snr_db: Optional[float]

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

    def to_dict(self) -> Dict[str, Any]:
        payload = {field: getattr(self, field) for field in self.__dataclass_fields__}
        payload["reason"] = self.reason
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
                     METHOD_REVISION, outcome)
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
        tuning_offset_hz=None,
        frequency_offset_hz=None,
        snr_db=None,
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
    if candidate_center is None or not candidate_bandwidth or candidate_bandwidth <= 0:
        return _refuse(window, request, "OCCUPIED_BANDWIDTH_UNRESOLVED", candidate=candidate)

    # --- stage 2: selection derived from stage 1, then checked ---------------
    channel_center = candidate_center
    channel_bandwidth = candidate_bandwidth * CHANNEL_MARGIN
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
    occupied_hz, carrier_offset_hz, snr_db = _measure(decimated, output_rate)

    digest = _digest(SCHEMA, window.window_id, window.digest, window.configuration_epoch,
                     window.signal_chain_hash, request.capture_center_hz,
                     request.target_frequency_hz, round(channel_center, 3),
                     round(channel_bandwidth, 3), decimation, FIR_DESIGN, FIR_TAPS,
                     METHOD_REVISION, "CHANNELIZED")
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
        occupied_bandwidth_basis="SAME_WINDOW_AS_SELECTION",
        tuning_offset_hz=tuning_offset,
        frequency_offset_hz=carrier_offset_hz,
        snr_db=snr_db,
        outcome="CHANNELIZED",
        reason_code="CHANNELIZED",
        method_revision=METHOD_REVISION,
    )
    return Channelization(product, decimated)


def _measure(channel: np.ndarray, output_rate_hz: float,
             ) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Occupied bandwidth, residual carrier offset and SNR of the channel.

    ``frequency_offset_hz`` is the carrier's position *within the channel*, which
    is a different quantity from ``tuning_offset_hz``: the first is a measurement
    of where the signal actually is, the second is a record of where the DDC was
    pointed. Reporting only their sum would hide selection error inside the
    measurement.
    """
    power_db, segment = welch_power_db(channel)
    if segment == 0:
        return None, None, None
    bin_hz = output_rate_hz / segment
    peak = int(np.argmax(power_db))
    power = np.power(10.0, power_db / 10.0)

    edges = _walk_occupancy(power_db, peak)
    if edges is None:
        # The signal fills the channel. That is a measurement, not a failure, but
        # the width is a lower bound and the noise estimate has nothing to use.
        return (float(segment * bin_hz),
                float((peak - segment // 2) * bin_hz), None)
    left, right = edges
    occupied = float((right - left) * bin_hz)

    # Power-weighted centroid over the occupied region, not the peak bin. The
    # peak bin of a noise-like band is wherever that realisation happened to be
    # loudest, which is a property of the noise and not of the emitter; the
    # centroid answers "where is this signal" for a tone and a band alike.
    indices = np.arange(left, right + 1)
    weights = power[left:right + 1]
    centroid = float((indices * weights).sum() / max(weights.sum(), 1e-20))
    offset = float((centroid - segment // 2) * bin_hz)

    in_band = float(power[left:right + 1].sum())
    noise_bins = np.concatenate((power[:left], power[right + 1:]))
    if noise_bins.size == 0:
        return occupied, offset, None
    # Median rather than mean: a second emitter inside the analysis span should
    # not be averaged into the noise floor and quietly raise it.
    noise = float(np.median(noise_bins)) * (right - left + 1)
    snr = 10.0 * math.log10(max(in_band - noise, 1e-20) / max(noise, 1e-20))
    return occupied, offset, round(snr, 3)


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
        # Phase 1d: the bridge issues verified windows and records bounded
        # products. Nothing yet consumes those products, which is a separate
        # claim and is reported separately.
        "bridge_integration": "INTEGRATED",
        "detector_integration": "NOT_IMPLEMENTED",
        "raw_iq_exposed": False,
        "baseband_transportable": False,
    }
