"""The promotion geometry, and nothing else.

Phase 3a, implementing `docs/RF_Signal_Family_Classifier_Scope.md` §5.20
correction **A**.

One source of truth for the window a promotion corpus is drawn from:
**2.048 MS/s, 524 288 samples, 256 ms, `complex64`, no overlap.** Every other
module reads it from here.

It exists because the number had two homes and one of them was wrong.
`rf_iq_ring.DEFAULT_CAPACITY_SAMPLES` was 524 288 while
`rf_null_corpus.GeneratorConfig.window_samples` defaulted to 262 144, and a
reader comparing them could not tell which was the geometry and which was a
convenience -- 262 144 is in fact recorded elsewhere as a *superseded* detector
minimum (`rf_symbol_clock`'s `superseded_minimum_sample_count`), registered
against no implementation. Two constants that disagree are not two opinions;
they are one fact and one bug, and nobody can tell which by looking.

**Its imports are `__future__` and `typing`.** That is the whole point of it.
The ring owns the buffer, `threading` and NumPy; the synthetic harness owns
NumPy. Neither should have to import the other to read a number, because
importing a module executes its graph -- the reasoning §13n Amendment O applied
to `rf_signal_chain_identity`, for the same reason and with the same result.

**It refuses nothing on its own.** `promotion_geometry_deviations` names the
fields that do not conform and the caller raises its own refusal, so this module
never becomes an exception every other module must learn to catch.

The derived values are **derived**. `PROMOTION_WINDOW_MS` and
`PROMOTION_CYCLE_RESOLUTION_HZ` are computed from the rate and the length rather
than transcribed beside them, which is the mistake this module was written to
end rather than to repeat one level down.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

GEOMETRY_SCHEMA = "scythe.rf-promotion-geometry.v1"

# The frozen promotion geometry. §6 Q2 approved the 256 ms window at 4.19 MB;
# §5.20 correction A pins it as the only geometry a promotion corpus may hold.
PROMOTION_SAMPLE_RATE_HZ = 2_048_000.0
PROMOTION_WINDOW_SAMPLES = 524_288
PROMOTION_DTYPE = "complex64"
PROMOTION_BYTES_PER_SAMPLE = 8
PROMOTION_WINDOW_OVERLAP = "NONE"

# Derived, never transcribed.
PROMOTION_WINDOW_MS = 1000.0 * PROMOTION_WINDOW_SAMPLES / PROMOTION_SAMPLE_RATE_HZ
PROMOTION_CYCLE_RESOLUTION_HZ = PROMOTION_SAMPLE_RATE_HZ / PROMOTION_WINDOW_SAMPLES
PROMOTION_PAYLOAD_BYTES = PROMOTION_WINDOW_SAMPLES * PROMOTION_BYTES_PER_SAMPLE

# Named so that a caller's refusal has something to say. This module raises
# nothing; the code is for whoever does.
PROMOTION_GEOMETRY_REFUSED = "PROMOTION_GEOMETRY_REFUSED"

# The fields `promotion_geometry_deviations` inspects, in report order.
GEOMETRY_FIELDS: Tuple[str, ...] = (
    "sample_rate_hz", "window_samples", "dtype", "overlap",
)


def promotion_geometry() -> Dict[str, Any]:
    """The geometry as a declaration, for a manifest or a header.

    A plain dictionary rather than a frozen dataclass: this is read and
    serialised, never constructed by a caller, and a class would invite someone
    to build a second instance with different numbers.
    """
    return {
        "schema": GEOMETRY_SCHEMA,
        "sample_rate_hz": PROMOTION_SAMPLE_RATE_HZ,
        "window_samples": PROMOTION_WINDOW_SAMPLES,
        "window_ms": PROMOTION_WINDOW_MS,
        "dtype": PROMOTION_DTYPE,
        "bytes_per_sample": PROMOTION_BYTES_PER_SAMPLE,
        "payload_bytes": PROMOTION_PAYLOAD_BYTES,
        "overlap": PROMOTION_WINDOW_OVERLAP,
        "cycle_resolution_hz": PROMOTION_CYCLE_RESOLUTION_HZ,
    }


def promotion_geometry_deviations(*, sample_rate_hz: Any, window_samples: Any,
                                  dtype: Any = PROMOTION_DTYPE,
                                  overlap: Any = PROMOTION_WINDOW_OVERLAP,
                                  ) -> Tuple[str, ...]:
    """Which of the four fields do not conform. Empty means they all do.

    Returns names rather than raising, and **all** of them rather than the
    first: a caller told only that something was wrong fixes one field, runs
    again, and learns about the next one.

    `window_samples` is compared nominally -- `type(x) is int` -- because
    `True == 1` and a boolean length is not a length. The rate is compared as a
    float, which is exact here: every value involved is a power of two over a
    power of two.
    """
    deviations = []
    if type(sample_rate_hz) is bool or not isinstance(sample_rate_hz, (int, float)) \
            or float(sample_rate_hz) != PROMOTION_SAMPLE_RATE_HZ:
        deviations.append("sample_rate_hz")
    if type(window_samples) is not int or window_samples != PROMOTION_WINDOW_SAMPLES:
        deviations.append("window_samples")
    if dtype != PROMOTION_DTYPE:
        deviations.append("dtype")
    if overlap != PROMOTION_WINDOW_OVERLAP:
        deviations.append("overlap")
    return tuple(deviations)


def is_promotion_geometry(*, sample_rate_hz: Any, window_samples: Any,
                          dtype: Any = PROMOTION_DTYPE,
                          overlap: Any = PROMOTION_WINDOW_OVERLAP) -> bool:
    """True when nothing deviates. The predicate is the absence of deviations,
    computed from them rather than beside them."""
    return not promotion_geometry_deviations(
        sample_rate_hz=sample_rate_hz, window_samples=window_samples,
        dtype=dtype, overlap=overlap)
