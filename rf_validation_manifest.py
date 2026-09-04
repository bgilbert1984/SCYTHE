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
import hashlib
import math
import time
from typing import Any, Dict, List, Optional, Tuple


SCHEMA = "scythe.rf-validation-manifest.v1"
MANIFEST_REVISION = "false-digital-gate.v1"

# Approved by the operator, 2026-09-02.
MAX_FALSE_DIGITAL_RATE = 0.001
CONFIDENCE = 0.95
TARGET_TOTAL_NULL_WINDOWS = 10_000

# Simultaneous coverage, added 2026-09-03.
#
# Applying a 95% bound to each of thirteen quantities and requiring all thirteen
# to pass does not give 95% coverage over the family: with independent tests the
# chance that at least one bound is violated is 1 - 0.95**13, about 49%. The
# error budget is therefore split across the bounds rather than spent thirteen
# times over. Bonferroni is conservative and makes no independence assumption,
# which is the right trade here because the strata are not independent -- a
# detector that over-calls on FM will over-call on adjacent-channel FM too.
#
# The set of tested bounds is frozen: STRATA plus the aggregate. Adding a
# stratum later changes every per-bound alpha and therefore every verdict, which
# is exactly why it may not be done after a corpus is opened.
FAMILY_ALPHA = 0.05
SIMULTANEOUS_CONTROL = "BONFERRONI"
TESTED_BOUND_COUNT = 13                       # twelve strata plus the aggregate
PER_BOUND_ALPHA = FAMILY_ALPHA / TESTED_BOUND_COUNT
PER_BOUND_CONFIDENCE = 1.0 - PER_BOUND_ALPHA

# Rule of three: with zero observed failures the upper bound is about
# -ln(alpha)/n, so this many trials are needed before a bound can even reach the
# approved rate. At the family-corrected alpha it is markedly more than at 95%,
# which is the cost of the correction and is stated rather than absorbed.
MINIMUM_TRIALS_FOR_ZERO_FAILURES = math.ceil(3.0 / MAX_FALSE_DIGITAL_RATE)
MINIMUM_TRIALS_FOR_ZERO_FAILURES_CORRECTED = math.ceil(
    -math.log(PER_BOUND_ALPHA) / MAX_FALSE_DIGITAL_RATE)


@dataclass(frozen=True)
class PromotionCorpusLock:
    """A promotion corpus, frozen against one detector configuration.

    Thresholds and preprocessing may be developed freely against a training or
    calibration corpus.  Once the *promotion* corpus is opened they are fixed: an
    evaluation presented with a configuration that differs from the frozen one
    does not promote, it reports why.  Without that, repeated tuning against the
    same windows converts validation into training one small adjustment at a time,
    and the measured false-DIGITAL rate becomes a description of how hard someone
    looked rather than of how the detector behaves.

    The strata set is part of the lock because the Bonferroni denominator depends
    on it: adding a stratum after opening would change every per-bound alpha and
    therefore every verdict already recorded.
    """

    corpus_id: str
    opened_at: float
    method_revision: str
    decision_threshold: float
    preprocessing_revision: str
    strata_digest: str
    configuration_digest: str
    tested_bound_count: int
    per_bound_alpha: float

    def to_dict(self) -> Dict[str, Any]:
        return {field: getattr(self, field) for field in self.__dataclass_fields__}


def _configuration_digest(method_revision: str, decision_threshold: float,
                          preprocessing_revision: str) -> str:
    payload = f"{method_revision}|{float(decision_threshold):.12g}|{preprocessing_revision}"
    return f"blake2s:{hashlib.blake2s(payload.encode(), digest_size=16).hexdigest()}"


def _strata_digest() -> str:
    payload = "|".join(f"{s.key}:{s.minimum_windows}:{int(s.buildable)}" for s in STRATA)
    return f"blake2s:{hashlib.blake2s(payload.encode(), digest_size=16).hexdigest()}"


def freeze_promotion_corpus(*, corpus_id: str, method_revision: str,
                            decision_threshold: float, preprocessing_revision: str,
                            opened_at: Optional[float] = None) -> PromotionCorpusLock:
    """Open a promotion corpus. What is frozen here cannot move without a new one."""
    return PromotionCorpusLock(
        corpus_id=corpus_id,
        opened_at=time.time() if opened_at is None else float(opened_at),
        method_revision=method_revision,
        decision_threshold=float(decision_threshold),
        preprocessing_revision=preprocessing_revision,
        strata_digest=_strata_digest(),
        configuration_digest=_configuration_digest(
            method_revision, decision_threshold, preprocessing_revision),
        tested_bound_count=TESTED_BOUND_COUNT,
        per_bound_alpha=PER_BOUND_ALPHA,
    )


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
    # Buildable as of 2026-09-03: SDRPPBridge.set_gain drives the tuner through
    # rtl_tcp's control channel, restricted to the gains the device reports, and
    # IQRetentionOwner.set_gain_db raises GAIN_CHANGE. A corpus can produce a gain
    # step rather than assert one.
    Stratum("GAIN_STEPS", "A gain change part-way through the window",
            750, safety_critical=True),
    Stratum("RETUNE_TRANSIENTS", "Samples spanning or adjacent to a retune",
            750, safety_critical=True),
    # Buildable as of 2026-09-03: ClockContinuityMonitor compares the decoded
    # sample count against elapsed time on every append and separates a transport
    # GAP from a rate DRIFT, so a gap is observed rather than assumed.
    Stratum("DROPPED_FRAMES_TIMING_GAPS", "Lost samples and discontinuous timestamps",
            750, safety_critical=True),
    Stratum("OVERLOADED_CLIPPED", "Converter saturation", 750, safety_critical=True),
    Stratum("RECEIVER_SPURS", "Internal spurious products and images", 500,
            safety_critical=False),
    Stratum("TWO_SIGNAL_COLLISIONS", "Two emitters sharing the analysed span",
            1_000, safety_critical=True),
)

STRATUM_KEYS: Tuple[str, ...] = tuple(stratum.key for stratum in STRATA)


def clopper_pearson_upper(failures: int, trials: int,
                          confidence: float = PER_BOUND_CONFIDENCE) -> Optional[float]:
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
                 confidence: float = PER_BOUND_CONFIDENCE) -> Optional[float]:
    """Wilson score upper bound. Reported beside Clopper-Pearson, never instead.

    The usual claim about Wilson -- that it is the less conservative of the two --
    holds at 95% and stops holding at the family-corrected confidence. Measured
    here at 99.6154%, Wilson is *above* the exact bound at every point in the
    regime this gate operates in (0/10000: 0.000710 vs 0.000556; 1/10000: 0.000899
    vs 0.000772; 50/10000: 0.007263 vs 0.007199), because its normal
    approximation degrades in a far tail with a tiny observed rate.

    That is not a reason to drop it -- being conservative costs nothing in a
    number nobody gates on -- but it is the reason the gate is the exact bound and
    not this one, and the reason the two must not be swapped for convenience.
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


def _corpus_state(lock: Optional[PromotionCorpusLock],
                  configuration: Optional[Dict[str, Any]]) -> Tuple[str, bool]:
    """Whether this evaluation may promote at all, before any arithmetic."""
    if lock is None:
        # Perfectly legitimate, and explicitly not a promotion: this is how a
        # detector is developed. It simply cannot also be how it is validated.
        return "NO_LOCK_EXPLORATORY", False
    if lock.strata_digest != _strata_digest():
        return "STRATA_CHANGED_AFTER_FREEZE", False
    if lock.tested_bound_count != TESTED_BOUND_COUNT:
        return "BOUND_COUNT_CHANGED_AFTER_FREEZE", False
    if configuration is None:
        return "CONFIGURATION_NOT_PRESENTED", False
    try:
        presented = _configuration_digest(
            configuration["method_revision"], configuration["decision_threshold"],
            configuration["preprocessing_revision"])
    except (KeyError, TypeError, ValueError):
        return "CONFIGURATION_NOT_PRESENTED", False
    if presented != lock.configuration_digest:
        return "CONFIGURATION_CHANGED_AFTER_FREEZE", False
    return "FROZEN", True


def evaluate(observations: Dict[str, Tuple[int, int]], *,
             lock: Optional[PromotionCorpusLock] = None,
             configuration: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Evaluate the whole gate. ``observations`` maps stratum key -> (trials, false).

    Both the aggregate bound and every stratum bound must pass. The aggregate
    alone would let a large pile of thermal noise carry a stratum that fails, and
    a stratum alone would not notice a rate that is only visible in the total.

    Every bound is computed at ``PER_BOUND_ALPHA``, not at 0.05: thirteen bounds
    each held to 95% do not give the family 95%.

    Promotion additionally requires a frozen corpus whose configuration matches
    the one presented. Without ``lock`` the report is exploratory by construction.
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

    corpus_state, corpus_permits_promotion = _corpus_state(lock, configuration)
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
        "confidence_method": "EXACT_CLOPPER_PEARSON",
        "simultaneous_control": SIMULTANEOUS_CONTROL,
        "family_alpha": FAMILY_ALPHA,
        "tested_bound_count": TESTED_BOUND_COUNT,
        "per_bound_alpha": PER_BOUND_ALPHA,
        "per_bound_confidence": PER_BOUND_CONFIDENCE,
        "minimum_trials_for_zero_failures": MINIMUM_TRIALS_FOR_ZERO_FAILURES,
        "minimum_trials_for_zero_failures_corrected":
            MINIMUM_TRIALS_FOR_ZERO_FAILURES_CORRECTED,
        "target_total_null_windows": TARGET_TOTAL_NULL_WINDOWS,
        "corpus_state": corpus_state,
        "corpus_lock": lock.to_dict() if lock is not None else None,
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
        # All three, never any two. A promotion needs a frozen corpus, the
        # aggregate bound and every stratum bound.
        "promotes": bool(corpus_permits_promotion and aggregate_passes and not failing),
        "promotion_blocked_reason": (
            None if (corpus_permits_promotion and aggregate_passes and not failing)
            else corpus_state if not corpus_permits_promotion
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
        "confidence_method": "EXACT_CLOPPER_PEARSON",
        "simultaneous_control": SIMULTANEOUS_CONTROL,
        "family_alpha": FAMILY_ALPHA,
        "tested_bound_count": TESTED_BOUND_COUNT,
        "per_bound_alpha": PER_BOUND_ALPHA,
        "per_bound_confidence": PER_BOUND_CONFIDENCE,
        "promotion_corpus": "FROZEN_LOCK_REQUIRED_FOR_PROMOTION",
        "bound_estimators": ["CLOPPER_PEARSON_EXACT", "WILSON_SCORE"],
        "gate_estimator": "CLOPPER_PEARSON_EXACT",
        "minimum_trials_for_zero_failures": MINIMUM_TRIALS_FOR_ZERO_FAILURES,
        "minimum_trials_for_zero_failures_corrected":
            MINIMUM_TRIALS_FOR_ZERO_FAILURES_CORRECTED,
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
