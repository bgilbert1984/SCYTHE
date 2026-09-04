"""Phase 2: a squared-envelope cyclic detector, running in shadow.

What it computes
----------------
A linearly modulated signal with symbol period ``T`` carries a cyclostationary
feature at the cyclic frequency ``alpha = 1/T``.  The squared envelope
``|x(t)|^2`` is the cheapest place to find it: for most linear modulations the
symbol timing survives into the envelope's second-order statistics, so the
magnitude spectrum of ``|x|^2`` shows a line at the symbol rate where an
unmodulated carrier or noise shows none.

The statistic is a peak-to-sidelobe ratio, not a raw magnitude::

    S = P_peak / median(P over the search band, peak neighbourhood excluded)

A ratio against a *local* floor, for the same reason the channelizer's SNR is a
ratio against a local floor: an absolute magnitude is a statement about the
receiver's gain, and a ratio against a global mean can be dragged down by the
very peak it is meant to measure.  The median with the peak's own neighbourhood
excluded is what keeps the floor from containing the signal.

What it does not do
-------------------
It does not promote.  ``squared-envelope-cyclic.v1`` is ``REGISTERED_NOT_VALIDATED``
until the Q4 corpus passes, so no verdict from it can reach a DIGITAL family
summary, an axis claim, GraphOps or a persisted product.  ``SHADOW_MODE`` is the
whole operating state of this module and every verdict says so.

The constant-envelope trap
--------------------------
A P25 C4FM or DMR transmission has, by construction, no envelope variation to
find.  This detector is blind to exactly those signals, and the honest report of
that blindness is ``CONSTANT_ENVELOPE`` mapping to ``information_structure =
NOT_ATTEMPTED`` -- **not** ``NO_SYMBOL_CLOCK_DETECTED``.  The second would be the
detector reporting a negative result on a test it never ran, which is the single
most expensive mistake this ontology exists to prevent: it turns a digital voice
transmission into a confident ANALOGUE.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Dict, Optional, Tuple

import numpy as np

from rf_detector_contract import DetectorInputRefused, admit_for_detection


SCHEMA = "scythe.rf-symbol-clock-verdict.v1"
METHOD_ID = "squared-envelope-cyclic.v1"
METHOD_REVISION = "squared-envelope-cyclic.v1"
AXIS = "information_structure"

# The threshold that was registered before this statistic existed was 8.4, chosen
# against no implementation. Measured against the implementation it is not merely
# wrong, it is unreachable in the wrong direction: on a *single* periodogram the
# null peak-to-median is ~18 for pure noise, from extreme-value statistics alone
# (the maximum of N exponential bins over their median is about ln(N)/ln 2, which
# for 129,500 bins is 17.0 -- measured 18.4). A threshold of 8.4 there would have
# fired on every noise window in existence.
#
# Averaging is what makes the statistic mean anything, exactly as it is for the
# channelizer's occupancy walk: 63 Welch averages take the null from 18.4 to a
# mean of 1.55 with a maximum of 1.82 over 300 noise windows.
#
# 2.5 is provisional and is calibration, not validation. Three hundred windows
# can bound a false rate near 1%, nowhere near the approved 0.001; that is what
# the Phase 3 promotion corpus is for, and it is frozen separately.
DECISION_THRESHOLD = 2.5
THRESHOLD_BASIS = "PROVISIONAL_FROM_NULL_CHARACTERISATION"
NULL_CHARACTERISATION = {
    "windows": 300,
    "source": "COMPLEX_GAUSSIAN_NOISE_SYNTHETIC",
    "welch_averages": 63,
    "observed_mean": 1.5508,
    "observed_p99_9": 1.8090,
    "observed_max": 1.8183,
    "supports_rate": 0.01,
    "note": (
        "EXPLORATORY CALIBRATION, NOT VALIDATION. THREE HUNDRED WINDOWS CANNOT "
        "BOUND A RATE OF 0.001; BY THE RULE OF THREE THAT NEEDS THOUSANDS. THE "
        "PROMOTION CORPUS IS SEPARATE AND FROZEN"
    ),
    "superseded_threshold": 8.4,
    "superseded_note": (
        "8.4 WAS REGISTERED AGAINST NO IMPLEMENTATION. ON THE UNAVERAGED "
        "STATISTIC THE NULL PEAK-TO-MEDIAN IS ABOUT 18, SO IT WOULD HAVE PASSED "
        "EVERY NOISE WINDOW. THE TWO NUMBERS ARE NOT COMPARABLE"
    ),
}
STATISTIC_DIRECTION = "GREATER_IS_STRONGER"
NULL_MODEL = "CHANNELIZED_NOISE_PLUS_NONCYCLIC_SIGNAL"
# 262,144 was registered against no implementation and is unreachable through the
# production channelizer: a 524,288-sample ring window decimated by 4 yields about
# 131,000 samples, so the detector would never have run on a real product.
#
# Measured instead. Because the Welch segment scales with the window, the number
# of averages stays at 63 and the null is flat: mean 1.44 to 1.56 and maximum
# 1.695 to 1.767 across 32,768 to 262,144 samples, with zero exceedances of 2.5
# in 200 noise windows at every length. What a short window costs is not a wider
# null but a higher *lowest detectable symbol rate*, and that is published on
# every verdict rather than hidden behind one global minimum.
MINIMUM_SAMPLE_COUNT = 32_768
VALIDATION_STATUS = "REGISTERED_NOT_VALIDATED"

# The whole operating state of this module.
SHADOW_MODE = True
PROMOTION_STATE = "SHADOW_NO_PROMOTION"

# Envelope variation below this is not a signal this test can speak about. The
# coefficient of variation of |x|^2 for complex Gaussian noise is 1.0; a pure
# tone is 0.0. Constant-envelope digital sits near a tone.
CONSTANT_ENVELOPE_CV = 0.05

# The search band's lower edge is a capability limit, and it is declared rather
# than discovered.
#
# The squared envelope's continuous data component extends from DC to roughly the
# symbol rate. A candidate line sitting inside that hump cannot be separated from
# it, so the search starts where a segment holds enough symbol periods for the
# line to have cleared its own hump. Measured at 62.5 Hz resolution: a 4 kHz
# signal (bin 64) was reported as 2187.5 Hz with a statistic of 4.56 -- a
# confident wrong rate read off the hump -- while 16, 32 and 64 kHz were exact.
#
# Excluding the region below MIN_SYMBOL_PERIODS_PER_SEGMENT turns that wrong
# answer into no answer. The cost is a declared minimum detectable symbol rate,
# which is a limitation; reporting 2187.5 Hz was a fabrication.
MIN_SYMBOL_PERIODS_PER_SEGMENT = 96
MAX_SYMBOL_RATE_FRACTION = 0.5
# Welch parameters for the cyclic spectrum. Averaging is not an optimisation here
# -- it is the difference between a statistic and a draw from an extreme-value
# distribution. Segments are sized for at least this many averages, then clamped
# so the cyclic resolution stays usable for slow symbol rates.
CYCLIC_TARGET_AVERAGES = 32
CYCLIC_SEGMENT_MIN = 1_024
CYCLIC_SEGMENT_MAX = 32_768
# Bins either side of the peak excluded from the noise floor, so the peak is not
# in its own reference. Mirrors the channelizer's reference-guard reasoning.
PEAK_GUARD_BINS = 4
MIN_SEARCH_BINS = 64
# The floor is measured *locally*, in a neighbourhood around the peak, not over
# the whole search band.
#
# The squared envelope of a linearly modulated signal has two components: a
# discrete line at the symbol rate, and a broad continuous hump from the random
# data, occupying roughly DC to the symbol rate. Against a global median the hump
# wins -- measured on a 4 kHz raised-cosine PSK signal, the six strongest bins
# were all within 1800-2400 Hz at nearly equal power while the true line at
# 4000 Hz sat at half that, and the detector reported 2187.5 Hz with a statistic
# of 35. A confident wrong symbol rate, produced from the signal's own data noise.
#
# A local floor separates the two by shape: a line stands above its immediate
# neighbours, a hump does not, because a hump *is* its neighbours.
LOCAL_FLOOR_BINS = 64

# Modes in which this statistic produces a number it should not, found while
# building it and recorded rather than smoothed over. None of them is fixed here;
# all of them are why the method is REGISTERED_NOT_VALIDATED and why nothing it
# says can promote. Phase 3 has to measure them, which is what the corresponding
# Q4 strata are for.
KNOWN_FALSE_POSITIVE_MODES: Dict[str, str] = {
    "SLOWLY_SLOPING_CYCLIC_SPECTRUM": (
        "A STRONG LOW-FREQUENCY AMPLITUDE DRIFT LEAVES THE CYCLIC SPECTRUM "
        "SLOPING ACROSS THE LOCAL REFERENCE WINDOW, SO THE PEAK EXCEEDS ITS OWN "
        "LOCAL MEDIAN FROM THE SLOPE ALONE. MEASURED AT 4.07 ON A SYNTHETIC "
        "RANDOM-WALK ENVELOPE WITH NO SYMBOL STRUCTURE, AGAINST A THRESHOLD OF "
        "2.5. TAKING THE HIGHER OF THE TWO SIDE MEDIANS RESISTS IT AND COSTS A "
        "FACTOR OF FOUR ON REAL SIGNALS, SO IT IS NOT DONE AND THIS IS DECLARED"
    ),
    "PERIODIC_TRANSPORT_ARTEFACT": (
        "A PERIODIC BUFFER OR USB SEAM IS A GENUINE CYCLIC FEATURE AND IS "
        "INDISTINGUISHABLE FROM SLOW SYMBOLS BY THIS STATISTIC. NOTHING HERE "
        "SEPARATES THEM; SHADOW MODE IS WHAT STOPS IT BECOMING EVIDENCE"
    ),
}

# The other direction, and the more surprising one: a real symbol clock that this
# pipeline cannot see. Recorded for the same reason as the false positives.
KNOWN_FALSE_NEGATIVE_MODES: Dict[str, str] = {
    "CHANNEL_MARGIN_ATTENUATES_EXCESS_BANDWIDTH": (
        "THE SQUARED-ENVELOPE TIMING LINE EXISTS ONLY BECAUSE THE PULSE HAS "
        "EXCESS BANDWIDTH, AND IT LIVES IN THE SPECTRAL SHOULDERS THAT A CHANNEL "
        "CUT AT CHANNEL_MARGIN x A -20 dB OCCUPANCY ESTIMATE PUTS INTO THE FIR "
        "SKIRT. MEASURED AT 50 kBAUD: STATISTIC 56.07 ON THE BASEBAND, 1.38 "
        "AFTER CHANNELIZATION. THE CHANNELIZER IS CORRECT AND THE DETECTOR IS "
        "CORRECT; THE FEATURE IS SIMPLY NOT IN WHAT THE DETECTOR RECEIVES"
    ),
    "DECIMATION_LEAVES_TOO_FEW_SAMPLES": (
        "A NARROW CHANNEL DECIMATES HARD. AT 20 kBAUD A 524288-SAMPLE WINDOW "
        "YIELDS 15884 OUTPUT SAMPLES, BELOW THE METHOD MINIMUM, SO THE DETECTOR "
        "DOES NOT RUN AT ALL"
    ),
}

OUTCOMES: Dict[str, str] = {
    "SYMBOL_CLOCK_LIKE_FEATURE": (
        "A CYCLIC FEATURE IN THE SQUARED ENVELOPE PASSED THE REGISTERED DECISION "
        "RULE. DIGITAL STRUCTURE IS SUPPORTED, NOT PROVEN, AND NOT PROMOTED"
    ),
    "NO_SYMBOL_CLOCK": (
        "THE TEST RAN OVER AN ENVELOPE WITH ENOUGH VARIATION TO CARRY A SYMBOL "
        "CLOCK AND FOUND NO CYCLIC FEATURE MEETING THE RULE"
    ),
    "CONSTANT_ENVELOPE": (
        "ENVELOPE VARIATION BELOW THE TEST FLOOR. CONSTANT-ENVELOPE DIGITAL MODES "
        "SUCH AS P25 C4FM AND DMR ARE INDISTINGUISHABLE FROM FM VOICE HERE, SO "
        "THIS IS A BLIND SPOT AND NOT A NEGATIVE RESULT"
    ),
    "INSUFFICIENT_WINDOW": (
        "FEWER SAMPLES THAN THE REGISTERED METHOD'S MINIMUM, SO ANY CYCLIC "
        "ESTIMATE WOULD HAVE TOO FEW CYCLES TO MEAN ANYTHING"
    ),
    "TIMING_QUALITY_INSUFFICIENT": (
        "THE OUTPUT SAMPLE RATE IS NOT FINITE AND POSITIVE, SO NO CYCLIC "
        "FREQUENCY DERIVED FROM IT WOULD DESCRIBE A RATE"
    ),
    "SOURCE_PRODUCT_UNVERIFIED": (
        "THE CHANNELIZED PRODUCT DID NOT PASS THE DETECTOR INPUT CONTRACT: IT WAS "
        "NOT A TYPED BRIDGE-LOCAL PRODUCT, OR ITS PROVENANCE DID NOT HOLD"
    ),
    "METHOD_NOT_VALIDATED": (
        "THE METHOD IS REGISTERED BUT HAS NOT PASSED PHASE 3 VALIDATION. ITS "
        "FALSE-DIGITAL RATE IS UNMEASURED, SO NO POSITIVE VERDICT MAY PROMOTE"
    ),
    "DETECTOR_ERROR": (
        "THE DETECTOR RAISED. NO VERDICT IS INFERRED FROM A FAILURE TO PRODUCE ONE"
    ),
}

# The mapping every verdict makes to the evidence contract's axis. The important
# row is CONSTANT_ENVELOPE: NOT_ATTEMPTED, never NO_SYMBOL_CLOCK_DETECTED.
AXIS_VALUES: Dict[str, str] = {
    "SYMBOL_CLOCK_LIKE_FEATURE": "SYMBOL_CLOCK_LIKE_FEATURE",
    "NO_SYMBOL_CLOCK": "NO_SYMBOL_CLOCK_DETECTED",
    # A test that could not run has no negative to report.
    "CONSTANT_ENVELOPE": "NOT_ATTEMPTED",
    "INSUFFICIENT_WINDOW": "NOT_ATTEMPTED",
    "TIMING_QUALITY_INSUFFICIENT": "NOT_ATTEMPTED",
    "SOURCE_PRODUCT_UNVERIFIED": "NOT_ATTEMPTED",
    "METHOD_NOT_VALIDATED": "NOT_ATTEMPTED",
    "DETECTOR_ERROR": "NOT_ATTEMPTED",
}


@dataclass(frozen=True)
class SymbolClockVerdict:
    """One shadow verdict. Measurements and metadata; never baseband."""

    schema: str
    method_id: str
    method_revision: str
    axis: str
    outcome: str
    reason_code: str
    axis_value: str

    detection_statistic: Optional[float]
    decision_threshold: float
    statistic_direction: str
    null_model: str
    sample_count: int
    symbol_rate_hz: Optional[float]
    cyclic_resolution_hz: Optional[float]
    # The lowest symbol rate this window could have found. A NO_SYMBOL_CLOCK from
    # a short window is a weaker negative than one from a long window, and saying
    # how weak is the difference between a result and an assertion.
    search_floor_hz: Optional[float]
    envelope_cv: Optional[float]

    source_product_id: Optional[str]
    source_window_id: Optional[str]
    source_window_digest: Optional[str]
    configuration_epoch: Optional[int]
    signal_chain_hash: Optional[str]
    snr_db: Optional[float]
    snr_reason_code: Optional[str]
    snr_stratum: str

    validation_status: str = VALIDATION_STATUS
    promotion_state: str = PROMOTION_STATE
    shadow_mode: bool = SHADOW_MODE
    # A shadow verdict cannot become a family summary, whatever it found.
    family_summary: str = "NOT_DERIVED"
    raw_iq_exposed: bool = False

    @property
    def reason(self) -> str:
        return OUTCOMES.get(self.reason_code, self.reason_code)

    @property
    def promotes(self) -> bool:
        """Always False. Kept as a property so the answer is stated, not implied."""
        return False

    def to_dict(self) -> Dict[str, Any]:
        payload = {field: getattr(self, field) for field in self.__dataclass_fields__}
        payload["reason"] = self.reason
        payload["promotes"] = self.promotes
        return payload


def _verdict(outcome: str, view: Optional[Dict[str, Any]], *,
             statistic: Optional[float] = None, symbol_rate_hz: Optional[float] = None,
             resolution_hz: Optional[float] = None,
             search_floor_hz: Optional[float] = None,
             envelope_cv: Optional[float] = None,
             sample_count: int = 0) -> SymbolClockVerdict:
    view = view or {}
    snr = view.get("snr") or {}
    return SymbolClockVerdict(
        schema=SCHEMA, method_id=METHOD_ID, method_revision=METHOD_REVISION, axis=AXIS,
        outcome=outcome, reason_code=outcome, axis_value=AXIS_VALUES[outcome],
        detection_statistic=statistic, decision_threshold=DECISION_THRESHOLD,
        statistic_direction=STATISTIC_DIRECTION, null_model=NULL_MODEL,
        sample_count=int(sample_count), symbol_rate_hz=symbol_rate_hz,
        cyclic_resolution_hz=resolution_hz, search_floor_hz=search_floor_hz,
        envelope_cv=envelope_cv,
        source_product_id=view.get("product_id"),
        source_window_id=view.get("source_window_id"),
        source_window_digest=view.get("source_window_digest"),
        configuration_epoch=view.get("configuration_epoch"),
        signal_chain_hash=view.get("signal_chain_hash"),
        snr_db=snr.get("snr_db"), snr_reason_code=snr.get("snr_reason_code"),
        snr_stratum=view.get("snr_stratum", "SNR_UNRESOLVED"),
    )


def squared_envelope_statistic(samples: np.ndarray, sample_rate_hz: float,
                               ) -> Tuple[Optional[float], Optional[float],
                                          Optional[float], float, Optional[float]]:
    """``(statistic, symbol_rate_hz, resolution_hz, envelope_cv, search_floor_hz)``.

    ``search_floor_hz`` is the lowest symbol rate this window could have found.
    It is returned rather than assumed because it depends on the window length,
    and a negative verdict is only as strong as the band it looked in.

    The DC term of the squared envelope is its mean power and carries no timing,
    so it is removed before the transform rather than being allowed to dominate
    the peak search.
    """
    envelope = np.abs(np.asarray(samples, dtype=np.complex64)) ** 2
    mean = float(envelope.mean())
    if not mean > 0.0:
        return None, None, None, 0.0, None
    envelope_cv = float(envelope.std() / mean)
    if envelope_cv < CONSTANT_ENVELOPE_CV:
        return None, None, None, envelope_cv, None

    centred = envelope - mean
    count = centred.size
    ideal = 1 << max(int(count // CYCLIC_TARGET_AVERAGES).bit_length() - 1, 0)
    segment = int(min(CYCLIC_SEGMENT_MAX, max(CYCLIC_SEGMENT_MIN, ideal)))
    segment = min(segment, count)
    if segment < CYCLIC_SEGMENT_MIN:
        return None, None, None, envelope_cv, None
    window = np.hanning(segment)
    hop = max(1, segment // 2)
    accumulator = np.zeros(segment // 2 + 1, dtype=np.float64)
    averages = 0
    for start in range(0, count - segment + 1, hop):
        accumulator += np.abs(np.fft.rfft(centred[start:start + segment] * window)) ** 2
        averages += 1
    if averages == 0:
        return None, None, None, envelope_cv, None
    spectrum = accumulator / averages
    resolution = sample_rate_hz / segment

    low = MIN_SYMBOL_PERIODS_PER_SEGMENT
    search_floor = low * resolution
    high = min(spectrum.size, int(MAX_SYMBOL_RATE_FRACTION * segment))
    if high - low < MIN_SEARCH_BINS:
        return None, None, resolution, envelope_cv, search_floor
    band = spectrum[low:high]
    peak = int(np.argmax(band))
    # Local, and with the peak excluded from its own reference -- the same two
    # rules the channelizer's noise reference follows, for the same two reasons.
    # A fixed-width neighbourhood that slides inward at the band edges rather
    # than shrinking. A peak near an edge would otherwise be measured against
    # half a reference, or against none, and refused for the wrong reason.
    width = 2 * LOCAL_FLOOR_BINS + 1
    lower = min(max(0, peak - LOCAL_FLOOR_BINS), max(0, band.size - width))
    upper = min(band.size, lower + width)
    floor_mask = np.zeros(band.size, dtype=bool)
    floor_mask[lower:upper] = True
    floor_mask[max(0, peak - PEAK_GUARD_BINS):peak + PEAK_GUARD_BINS + 1] = False
    if floor_mask.sum() < MIN_SEARCH_BINS:
        return None, None, resolution, envelope_cv, search_floor
    floor = float(np.median(band[floor_mask]))
    if not floor > 0.0:
        return None, None, resolution, envelope_cv, search_floor
    statistic = float(band[peak]) / floor
    return (statistic, float((low + peak) * resolution), resolution, envelope_cv,
            search_floor)


def detect(channelization: Any, *, ring: Any = None) -> SymbolClockVerdict:
    """Run the shadow detector over one channelized product. Never raises.

    Admission and provenance are the contract's job and are asked first; a
    product that fails either is ``SOURCE_PRODUCT_UNVERIFIED`` rather than a
    negative result, because a test that was refused did not run.
    """
    view: Optional[Dict[str, Any]] = None
    try:
        try:
            view = admit_for_detection(channelization, ring=ring)
        except DetectorInputRefused:
            return _verdict("SOURCE_PRODUCT_UNVERIFIED", None)

        samples = channelization.samples
        rate = view["output_sample_rate_hz"]
        if not isinstance(rate, (int, float)) or not math.isfinite(rate) or rate <= 0:
            return _verdict("TIMING_QUALITY_INSUFFICIENT", view,
                            sample_count=int(samples.size))
        if samples.size < MINIMUM_SAMPLE_COUNT:
            return _verdict("INSUFFICIENT_WINDOW", view, sample_count=int(samples.size))

        statistic, symbol_rate, resolution, cv, floor_hz = squared_envelope_statistic(
            samples, float(rate))
        if cv < CONSTANT_ENVELOPE_CV:
            # The blind spot, named. NOT_ATTEMPTED, never NO_SYMBOL_CLOCK_DETECTED.
            return _verdict("CONSTANT_ENVELOPE", view, envelope_cv=cv,
                            resolution_hz=resolution, search_floor_hz=floor_hz,
                            sample_count=int(samples.size))
        if statistic is None:
            return _verdict("INSUFFICIENT_WINDOW", view, envelope_cv=cv,
                            resolution_hz=resolution, search_floor_hz=floor_hz,
                            sample_count=int(samples.size))
        outcome = ("SYMBOL_CLOCK_LIKE_FEATURE" if statistic >= DECISION_THRESHOLD
                   else "NO_SYMBOL_CLOCK")
        return _verdict(outcome, view, statistic=round(statistic, 4),
                        symbol_rate_hz=symbol_rate, resolution_hz=resolution,
                        search_floor_hz=floor_hz, envelope_cv=cv,
                        sample_count=int(samples.size))
    except Exception:
        # A detector that raised produced no evidence, and the absence of a
        # verdict is not a negative one.
        return _verdict("DETECTOR_ERROR", view)


def detector_status() -> Dict[str, Any]:
    """Declared capability, declared absence of validation, declared shadow state."""
    return {
        "schema": SCHEMA,
        "method_id": METHOD_ID,
        "method_revision": METHOD_REVISION,
        "axis": AXIS,
        "state": "IMPLEMENTED_SHADOW_MODE",
        "shadow_mode": SHADOW_MODE,
        "promotion_state": PROMOTION_STATE,
        "validation_status": VALIDATION_STATUS,
        "validation_note": (
            "PHASE 3 HAS NOT RUN. NO LABELLED CORPUS HAS MEASURED THIS METHOD'S "
            "FALSE-DIGITAL RATE, SO A POSITIVE VERDICT IS DIAGNOSTIC ONLY AND MAY "
            "NOT REACH A FAMILY SUMMARY, AN AXIS CLAIM, GRAPHOPS OR STORAGE"
        ),
        "decision_threshold": DECISION_THRESHOLD,
        "threshold_basis": THRESHOLD_BASIS,
        "null_characterisation": dict(NULL_CHARACTERISATION),
        "cyclic_target_averages": CYCLIC_TARGET_AVERAGES,
        "minimum_symbol_periods_per_segment": MIN_SYMBOL_PERIODS_PER_SEGMENT,
        "minimum_detectable_symbol_rate_note": (
            "THE SEARCH BEGINS WHERE A SEGMENT HOLDS "
            f"{MIN_SYMBOL_PERIODS_PER_SEGMENT} SYMBOL PERIODS. BELOW THAT THE "
            "SQUARED ENVELOPE'S CONTINUOUS DATA COMPONENT CANNOT BE SEPARATED "
            "FROM THE TIMING LINE, AND THE DETECTOR REPORTS NO SYMBOL CLOCK "
            "RATHER THAN A RATE READ OFF THE HUMP"
        ),
        "statistic_direction": STATISTIC_DIRECTION,
        "null_model": NULL_MODEL,
        "minimum_sample_count": MINIMUM_SAMPLE_COUNT,
        "superseded_minimum_sample_count": 262_144,
        "superseded_minimum_note": (
            "262144 WAS REGISTERED AGAINST NO IMPLEMENTATION AND IS UNREACHABLE "
            "THROUGH THIS CHANNELIZER, SO THE DETECTOR WOULD NEVER HAVE RUN"
        ),
        "statistic": "WELCH_AVERAGED_SQUARED_ENVELOPE_CYCLIC_PEAK_TO_MEDIAN_SIDELOBE",
        "constant_envelope_cv_floor": CONSTANT_ENVELOPE_CV,
        "outcomes": dict(OUTCOMES),
        "axis_values": dict(AXIS_VALUES),
        "known_false_positive_modes": dict(KNOWN_FALSE_POSITIVE_MODES),
        "known_false_negative_modes": dict(KNOWN_FALSE_NEGATIVE_MODES),
        "digital_reachable": False,
        "digital_reachable_note": (
            "A DIGITAL SUMMARY REQUIRES A VALIDATED METHOD. THIS ONE IS "
            "REGISTERED AND UNVALIDATED, SO LIVE DIGITAL REMAINS UNREACHABLE"
        ),
        "raw_iq_exposed": False,
    }
