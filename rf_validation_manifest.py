"""Q4 as something that runs: the false-DIGITAL gate as a bound, not a fraction.

Operator approval, 2026-09-02, refined 2026-09-03.  The maximum false-DIGITAL
rate is ``0.001``, but the promotion rule is the *bound*::

    one-sided 95% upper confidence bound  <=  0.001     PROMOTES
    observed false positives / trials     <=  0.001     DOES NOT

Zero false positives in 100 trials is not evidence of a sub-0.1% rate.  By the
rule of three, zero failures needs roughly ``3/0.001 = 3000`` independent null
trials before a 95% upper bound reaches 0.1% at all -- which is why a stratified
corpus of at least 10,000 null windows is the target rather than a round number
chosen for comfort.

Why strata
----------
An aggregate rate can be bought with thermal noise.  Ten thousand windows of a
terminated input will drive any aggregate bound down while saying nothing about
whether a P25 C4FM transmission or a retune transient produces a false DIGITAL.
Every stratum is therefore bounded separately and the aggregate is bounded too;
both must pass.

Two strata cannot be built yet, and say so
------------------------------------------
``GAIN_STEPS`` needs a gain control, and ``DROPPED_FRAMES_TIMING_GAPS`` needs a
clock-discontinuity detector.  ``rf_iq_retention`` declares ``GAIN_CHANGE``,
``DIRECT_SAMPLING_CHANGE`` and ``CLOCK_DISCONTINUITY`` as invalidation reasons
that nothing calls.  A corpus that labelled windows "gain step" while nothing
could produce or detect one would be generating its own labels, so those strata
report ``NOT_BUILDABLE`` and the manifest does not pass while any required
stratum is in that state.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Dict, List, Optional, Tuple


SCHEMA = "scythe.rf-validation-manifest.v1"
MANIFEST_REVISION = "false-digital-gate.v1"

# Approved by the operator, 2026-09-02.
MAX_FALSE_DIGITAL_RATE = 0.001
CONFIDENCE = 0.95
TARGET_TOTAL_NULL_WINDOWS = 10_000

# Rule of three: with zero observed failures the 95% upper bound is about 3/n, so
# this many trials are needed before the bound can even reach the approved rate.
MINIMUM_TRIALS_FOR_ZERO_FAILURES = math.ceil(3.0 / MAX_FALSE_DIGITAL_RATE)


@dataclass(frozen=True)
class Stratum:
    """One null population, with why it is in the corpus and whether it can be built."""

    key: str
    description: str
    minimum_windows: int
    safety_critical: bool
    buildable: bool = True
    blocked_by: Optional[str] = None


# The twelve approved strata. `minimum_windows` sums past TARGET_TOTAL_NULL_WINDOWS
# on purpose: a stratum minimum is a floor for that stratum, not a share of a quota.
STRATA: Tuple[Stratum, ...] = (
    Stratum("THERMAL_NO_INPUT", "Terminated or disconnected input: thermal noise only",
            2_000, safety_critical=False),
    Stratum("STATIONARY_ANALOGUE_FM", "Steady analogue FM voice or tone",
            1_500, safety_critical=True),
    Stratum("AM", "Amplitude-modulated analogue carrier", 1_000, safety_critical=True),
    # The trap this whole ontology exists for: constant-envelope digital is
    # invisible to an envelope test, so it belongs in the NULL corpus for an
    # ANALOGUE detector and in the POSITIVE corpus for a symbol-clock detector.
    # Here it is null for false-DIGITAL only in the sense that a *wrong* family
    # call on it is the most expensive error the system can make.
    Stratum("CONSTANT_ENVELOPE_DIGITAL", "P25 C4FM, DMR and similar: no envelope cue",
            1_000, safety_critical=True),
    Stratum("ADJACENT_CHANNEL_INTERFERENCE", "A strong neighbour inside the analysis span",
            1_000, safety_critical=True),
    Stratum("DC_CONTAMINATION", "Zero-IF DC artefact at or near the channel",
            750, safety_critical=False),
    Stratum("GAIN_STEPS", "A gain change part-way through the window",
            750, safety_critical=True, buildable=False,
            blocked_by="GAIN_CHANGE is a declared invalidation reason that nothing "
                       "calls: this bridge has no gain control, so a gain step can "
                       "be neither produced nor detected"),
    Stratum("RETUNE_TRANSIENTS", "Samples spanning or adjacent to a retune",
            750, safety_critical=True),
    Stratum("DROPPED_FRAMES_TIMING_GAPS", "Lost samples and discontinuous timestamps",
            750, safety_critical=True, buildable=False,
            blocked_by="CLOCK_DISCONTINUITY is a declared invalidation reason that "
                       "nothing calls: no discontinuity detector exists, so a gap "
                       "cannot be labelled from evidence"),
    Stratum("OVERLOADED_CLIPPED", "Converter saturation", 750, safety_critical=True),
    Stratum("RECEIVER_SPURS", "Internal spurious products and images", 500,
            safety_critical=False),
    Stratum("TWO_SIGNAL_COLLISIONS", "Two emitters sharing the analysed span",
            1_000, safety_critical=True),
)

STRATUM_KEYS: Tuple[str, ...] = tuple(stratum.key for stratum in STRATA)


def clopper_pearson_upper(failures: int, trials: int,
                          confidence: float = CONFIDENCE) -> Optional[float]:
    """Exact one-sided upper bound on a binomial rate. ``None`` when trials is 0.

    Uses the Beta quantile identity ``U = BetaInv(confidence; k+1, n-k)`` and
    solves it by bisection on the regularized incomplete beta function, so there
    is no scipy dependency and no normal approximation.  The approximation is what
    goes wrong here: at ``k = 0`` a Wald interval has zero width and would report
    a measured rate of exactly zero, which is the error this gate exists to refuse.
    """
    if trials <= 0:
        return None
    if failures < 0 or failures > trials:
        raise ValueError("failures must lie between 0 and trials")
    if failures == trials:
        return 1.0
    low, high = 0.0, 1.0
    for _ in range(200):
        mid = 0.5 * (low + high)
        if _binomial_at_most(failures, trials, mid) > 1.0 - confidence:
            low = mid
        else:
            high = mid
    return 0.5 * (low + high)


def _binomial_at_most(failures: int, trials: int, probability: float) -> float:
    """``P(X <= failures | trials, probability)``, computed in log space.

    A corpus of 10,000+ windows makes ``comb(n, k)`` astronomically large and
    ``p**k`` astronomically small, and multiplying them directly overflows before
    they cancel.  Summing ``exp(log C + k log p + (n-k) log(1-p))`` keeps every
    term in range, which matters because the whole point of this gate is that it
    stays correct at the trial counts the rule of three demands.
    """
    if probability <= 0.0:
        return 1.0
    if probability >= 1.0:
        return 1.0 if failures >= trials else 0.0
    log_p = math.log(probability)
    log_q = math.log1p(-probability)
    total = 0.0
    for i in range(failures + 1):
        log_term = (math.lgamma(trials + 1) - math.lgamma(i + 1)
                    - math.lgamma(trials - i + 1) + i * log_p + (trials - i) * log_q)
        if log_term > -745.0:               # below this, exp underflows to zero
            total += math.exp(log_term)
    return min(1.0, total)


def wilson_upper(failures: int, trials: int,
                 confidence: float = CONFIDENCE) -> Optional[float]:
    """Wilson score upper bound. Reported beside Clopper-Pearson, never instead.

    Wilson is less conservative and is the more useful number for tracking a
    corpus as it grows; the exact bound is what the gate is evaluated against.
    """
    if trials <= 0:
        return None
    z = _normal_quantile(confidence)
    phat = failures / trials
    denominator = 1.0 + z * z / trials
    centre = phat + z * z / (2.0 * trials)
    margin = z * math.sqrt(phat * (1.0 - phat) / trials + z * z / (4.0 * trials * trials))
    return min(1.0, (centre + margin) / denominator)


def _normal_quantile(probability: float) -> float:
    """One-sided standard normal quantile, Acklam's rational approximation."""
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)
    p_low = 0.02425
    if probability < p_low:
        q = math.sqrt(-2.0 * math.log(probability))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    if probability <= 1.0 - p_low:
        q = probability - 0.5
        r = q * q
        return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
               (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
    q = math.sqrt(-2.0 * math.log(1.0 - probability))
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)


def evaluate_stratum(stratum: Stratum, trials: int, false_digital: int) -> Dict[str, Any]:
    """One stratum's verdict. A stratum that cannot be built cannot pass."""
    if not stratum.buildable:
        return {
            "stratum": stratum.key, "state": "NOT_BUILDABLE", "passes": False,
            "blocked_by": stratum.blocked_by, "trials": 0, "false_digital": 0,
            "observed_rate": None, "upper_bound_95": None, "wilson_upper_95": None,
            "minimum_windows": stratum.minimum_windows,
            "safety_critical": stratum.safety_critical,
        }
    exact = clopper_pearson_upper(false_digital, trials)
    if trials < stratum.minimum_windows:
        state, passes = "INSUFFICIENT_TRIALS", False
    elif exact is not None and exact <= MAX_FALSE_DIGITAL_RATE:
        state, passes = "PASSES", True
    else:
        state, passes = "BOUND_ABOVE_APPROVED_RATE", False
    return {
        "stratum": stratum.key, "state": state, "passes": passes, "blocked_by": None,
        "trials": trials, "false_digital": false_digital,
        "observed_rate": (false_digital / trials) if trials else None,
        "upper_bound_95": exact,
        "wilson_upper_95": wilson_upper(false_digital, trials),
        "minimum_windows": stratum.minimum_windows,
        "safety_critical": stratum.safety_critical,
    }


def evaluate(observations: Dict[str, Tuple[int, int]]) -> Dict[str, Any]:
    """Evaluate the whole gate. ``observations`` maps stratum key -> (trials, false).

    Both the aggregate bound and every stratum bound must pass. The aggregate
    alone would let a large pile of thermal noise carry a stratum that fails, and
    a stratum alone would not notice a rate that is only visible in the total.
    """
    unknown = sorted(set(observations) - set(STRATUM_KEYS))
    if unknown:
        raise ValueError(f"unknown strata: {unknown}")
    results: List[Dict[str, Any]] = []
    total_trials = total_failures = 0
    for stratum in STRATA:
        trials, failures = observations.get(stratum.key, (0, 0))
        if failures > trials:
            raise ValueError(f"{stratum.key}: false_digital exceeds trials")
        results.append(evaluate_stratum(stratum, int(trials), int(failures)))
        if stratum.buildable:
            total_trials += int(trials)
            total_failures += int(failures)

    aggregate_bound = clopper_pearson_upper(total_failures, total_trials)
    aggregate_passes = (total_trials >= TARGET_TOTAL_NULL_WINDOWS
                        and aggregate_bound is not None
                        and aggregate_bound <= MAX_FALSE_DIGITAL_RATE)
    not_buildable = [r["stratum"] for r in results if r["state"] == "NOT_BUILDABLE"]
    failing = [r["stratum"] for r in results if not r["passes"]]
    return {
        "schema": SCHEMA,
        "manifest_revision": MANIFEST_REVISION,
        "rule": "ONE_SIDED_95_PERCENT_UPPER_CONFIDENCE_BOUND_AT_OR_BELOW_APPROVED_RATE",
        "max_false_digital_rate": MAX_FALSE_DIGITAL_RATE,
        "confidence": CONFIDENCE,
        "minimum_trials_for_zero_failures": MINIMUM_TRIALS_FOR_ZERO_FAILURES,
        "target_total_null_windows": TARGET_TOTAL_NULL_WINDOWS,
        "aggregate": {
            "trials": total_trials,
            "false_digital": total_failures,
            "observed_rate": (total_failures / total_trials) if total_trials else None,
            "upper_bound_95": aggregate_bound,
            "wilson_upper_95": wilson_upper(total_failures, total_trials),
            "passes": aggregate_passes,
        },
        "strata": results,
        "not_buildable": not_buildable,
        "failing_strata": failing,
        # Both, never either. A promotion needs the aggregate and every stratum.
        "promotes": bool(aggregate_passes and not failing),
        "promotion_blocked_reason": (
            None if (aggregate_passes and not failing)
            else "STRATA_NOT_BUILDABLE" if not_buildable
            else "AGGREGATE_OR_STRATUM_BOUND_NOT_MET"),
    }


def manifest_status() -> Dict[str, Any]:
    """The declared gate, before any corpus exists."""
    return {
        "schema": SCHEMA,
        "manifest_revision": MANIFEST_REVISION,
        "state": "DECLARED_NO_CORPUS_COLLECTED",
        "rule": "ONE_SIDED_95_PERCENT_UPPER_CONFIDENCE_BOUND_AT_OR_BELOW_APPROVED_RATE",
        "max_false_digital_rate": MAX_FALSE_DIGITAL_RATE,
        "confidence": CONFIDENCE,
        "bound_estimators": ["CLOPPER_PEARSON_EXACT", "WILSON_SCORE"],
        "gate_estimator": "CLOPPER_PEARSON_EXACT",
        "minimum_trials_for_zero_failures": MINIMUM_TRIALS_FOR_ZERO_FAILURES,
        "target_total_null_windows": TARGET_TOTAL_NULL_WINDOWS,
        "strata": [
            {"key": s.key, "description": s.description,
             "minimum_windows": s.minimum_windows,
             "safety_critical": s.safety_critical,
             "buildable": s.buildable, "blocked_by": s.blocked_by}
            for s in STRATA
        ],
        "not_buildable": [s.key for s in STRATA if not s.buildable],
    }
