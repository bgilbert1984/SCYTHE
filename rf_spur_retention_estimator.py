"""The §5.21 feature-retention estimator: protocol §7 item 2.

The persistence margin is declared (`PLAN_PERSISTENCE_MARGIN_DB`, 10 dB);
this module is the estimator that measures a feature's excess over the
tuning-local median. Given the eight repeats captured at one visit -- each
a window of complex IQ at the visit's LO -- and the feature's baseband
frequency, it returns the `SpurPersistenceObservation` the catalogue entry
is built from: the eight excesses, one per repeat, with the verdict
(`qualifying`, `persistent`) derived from them, not stored beside them.

The method, stated so a reader can reproduce it:

1. Each repeat is Hann-windowed and Fourier-transformed (the tree's
   `rf_spectrum_contract` builds its bins Hann-windowed too), and the
   power per bin is taken in dB. The layout is fftshifted: bin 0 is
   -sample_rate/2.
2. The tuning-local median is the median of the bin powers in dB, computed
   per repeat -- "each qualifying one at least 10 dB above its own
   tuning-local median," as the observation's contract puts it. The noise
   floor is not flat across the envelope, so the median is the tuning's
   own, and it is that repeat's own, not pooled across repeats.
3. The feature's excess is the power at the bin nearest the declared
   baseband frequency, minus that repeat's median. One bin: the estimator
   measures at the frequency it is given, it does not hunt for the peak.

What the estimator does not do:

- It does not detect features. The caller declares the feature's baseband
  frequency -- in the live path, the sequencing layer finds the candidates
  and calls this per candidate. Detection is protocol §7 item 1's module,
  which follows the authorisation.
- It never emits `None`. A repeat's excess may be small or negative --
  that is the measurement "the feature was not above the floor." `None`
  in a `SpurPersistenceObservation` is a repeat with no usable measurement
  (a dropped capture, a candidate never detected there), and recording it
  is the caller's, which knows why the repeat is missing.
- It does not pre-empt the observation's denominator. Handed anything but
  eight repeats, `SpurPersistenceObservation` refuses with
  `PLAN_REPEATS_NOT_OBSERVED` itself -- seven of eight is a ratio over a
  fixed denominator, and the observation is the authority that says so.
"""

import math
from typing import Any, Sequence

import numpy as np

from rf_promotion_envelope import SpurPersistenceObservation

RETENTION_TUNING_NOT_DECLARED = "RETENTION_TUNING_NOT_DECLARED"
RETENTION_WINDOWS_NOT_DECLARED = "RETENTION_WINDOWS_NOT_DECLARED"
RETENTION_WINDOW_NOT_OBSERVED = "RETENTION_WINDOW_NOT_OBSERVED"
RETENTION_WINDOW_LENGTH_MISMATCH = "RETENTION_WINDOW_LENGTH_MISMATCH"
RETENTION_SAMPLE_NOT_FINITE = "RETENTION_SAMPLE_NOT_FINITE"
RETENTION_SAMPLE_RATE_NOT_POSITIVE = "RETENTION_SAMPLE_RATE_NOT_POSITIVE"
RETENTION_FEATURE_OUT_OF_SPAN = "RETENTION_FEATURE_OUT_OF_SPAN"


class RetentionRefused(RuntimeError):
    """The estimator's own regime: inputs it cannot measure. A weak feature
    is a small excess, not this; this is for windows that are not windows."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def _power_db(window: np.ndarray) -> np.ndarray:
    """Hann-windowed power spectrum in dB, fftshifted so bin 0 is -fs/2."""
    n = window.size
    spectrum = np.fft.fftshift(np.fft.fft(window * np.hanning(n)))
    power = np.abs(spectrum) ** 2
    return 10.0 * np.log10(np.maximum(power, 1e-300))


def _as_window(value: Any, index: int) -> np.ndarray:
    try:
        arr = np.asarray(value, dtype=np.complex128)
    except (TypeError, ValueError):
        raise RetentionRefused(
            RETENTION_WINDOW_NOT_OBSERVED,
            f"repeat {index} is not IQ samples; got {type(value).__name__}")
    if arr.ndim != 1 or arr.size == 0:
        raise RetentionRefused(
            RETENTION_WINDOW_NOT_OBSERVED,
            f"repeat {index} is not a window of IQ samples; got shape "
            f"{arr.shape}")
    if not bool(np.all(np.isfinite(arr.real) & np.isfinite(arr.imag))):
        raise RetentionRefused(
            RETENTION_SAMPLE_NOT_FINITE,
            f"repeat {index} carries a non-finite sample, which is not a "
            f"measurement")
    return arr


def measure_persistence(*, tuning_id: str,
                        windows: Sequence[Any],
                        sample_rate_hz: float,
                        feature_baseband_hz: float) -> SpurPersistenceObservation:
    """Measure one feature's excess over the tuning-local median in each of
    the visit's repeats, and return the observation the catalogue entry is
    built from."""
    if type(tuning_id) is not str or not tuning_id:
        raise RetentionRefused(
            RETENTION_TUNING_NOT_DECLARED,
            f"a persistence observation names its tuning; got "
            f"{tuning_id!r}")
    if (type(sample_rate_hz) not in (int, float)
            or not math.isfinite(sample_rate_hz)
            or sample_rate_hz <= 0):
        raise RetentionRefused(
            RETENTION_SAMPLE_RATE_NOT_POSITIVE,
            f"the bin map needs a positive finite sample rate; got "
            f"{sample_rate_hz!r}")
    sample_rate_hz = float(sample_rate_hz)
    if (type(feature_baseband_hz) not in (int, float)
            or not math.isfinite(feature_baseband_hz)
            or abs(feature_baseband_hz) > sample_rate_hz / 2):
        raise RetentionRefused(
            RETENTION_FEATURE_OUT_OF_SPAN,
            f"the feature is measured inside the span the repeats cover, "
            f"|f| <= {sample_rate_hz / 2:.0f} Hz; got {feature_baseband_hz!r}")
    feature_baseband_hz = float(feature_baseband_hz)
    if isinstance(windows, np.ndarray) and windows.ndim == 1:
        raise RetentionRefused(
            RETENTION_WINDOWS_NOT_DECLARED,
            "one window is not a visit's repeats; pass the sequence of "
            "repeat windows")
    try:
        repeats = tuple(windows)
    except TypeError:
        raise RetentionRefused(
            RETENTION_WINDOWS_NOT_DECLARED,
            f"a visit's repeats come as a sequence; got "
            f"{type(windows).__name__}")
    if not repeats:
        raise RetentionRefused(
            RETENTION_WINDOWS_NOT_DECLARED,
            "a visit with no repeats retains no features")
    measured = [_as_window(value, index) for index, value in enumerate(repeats)]
    length = measured[0].size
    for index, window in enumerate(measured[1:], start=1):
        if window.size != length:
            raise RetentionRefused(
                RETENTION_WINDOW_LENGTH_MISMATCH,
                f"repeat {index} has {window.size} samples against repeat "
                f"0's {length}. The repeats are the same window laid down "
                f"{len(measured)} times, not {len(measured)} different "
                f"windows")
    # The feature's bin in the fftshifted layout; the span check above keeps
    # this inside the spectrum.
    feature_bin = int(round((feature_baseband_hz + sample_rate_hz / 2)
                            * length / sample_rate_hz))
    feature_bin = min(max(feature_bin, 0), length - 1)
    excesses = []
    for window in measured:
        bins_db = _power_db(window)
        median_db = float(np.median(bins_db))
        excesses.append(float(bins_db[feature_bin] - median_db))
    return SpurPersistenceObservation(tuning_id=tuning_id,
                                      repeat_excess_db=tuple(excesses))
