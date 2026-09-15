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

Buildability, and where the remaining block actually is
-------------------------------------------------------
``Stratum.buildable`` exists because ``GAIN_STEPS`` and
``DROPPED_FRAMES_TIMING_GAPS`` once had no control path: a corpus labelling
windows "gain step" while nothing could produce or detect one would be
generating its own labels.  Both were wired on 2026-09-03 --
``IQRetentionOwner.set_gain_db`` raises ``GAIN_CHANGE`` and
``ClockContinuityMonitor`` separates a transport gap from a rate drift -- and
**every stratum here is now marked buildable**.  The mechanism stays, because the
next stratum to be declared ahead of its control path should be able to say so.

The block that remains is not buildability.  ``GAIN_STEPS``,
``RETUNE_TRANSIENTS`` and ``RECEIVER_SPURS`` need a receiver rather than a
generator, which ``rf_null_corpus`` records as ``TUNER_REQUIRED``, and **no
capture path exists in this repository** -- no writer, no acquisition, nothing
that turns a tuner event into a stratum window.

``RECEIVER_SPURS`` carries a second condition, and what that condition *is* has
already changed once.  It had no identification protocol at all; §5.21 supplies
one and §5.22 supplies the parameters it left free, both accepted 2026-09-14.
What is missing now is **execution**: no spur catalogue exists, no feasibility
check has been run against an actual count of internal products, and a method
nobody has run identifies nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import time
from typing import Any, Dict, List, Mapping, Optional, Tuple


SCHEMA = "scythe.rf-validation-manifest.v1"
MANIFEST_REVISION = "false-digital-gate.v1"

# Approved by the operator, 2026-09-02.
MAX_FALSE_DIGITAL_RATE = 0.001
CONFIDENCE = 0.95
# Derived below from STRATA, never transcribed (§5.18). A second constant beside
# the strata table is two answers that drift, and the one nobody re-derives is
# the one a harness would read.
TARGET_TOTAL_NULL_WINDOWS: int

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

# --- the family, resolved 2026-09-04 and now immutable ----------------------
#
# Channel-purpose aggregates were considered and REJECTED, on a structural
# argument rather than an arithmetic one. MEASUREMENT_CHANNEL and
# STRUCTURE_CHANNEL are not two populations from which SCYTHE independently makes
# DIGITAL claims: a measurement channel is cut for occupancy, centroid and SNR
# and cannot produce an information-structure verdict at all. Bonferroni must
# cover the inferential claims eligible for promotion, not every implementation
# dimension that shows up in provenance. Adding a bound for a lineage that can
# never emit the claim would spend error budget on a hypothesis nobody tests.
#
# That argument holds only while exactly one lineage is eligible, which is why
# rf_symbol_clock refuses a verdict from any other -- see
# ELIGIBLE_CHANNEL_PURPOSE and MEASUREMENT_CHANNEL_VERDICT_PRODUCTION there. The
# prohibition is what keeps the count at thirteen.
CHANNEL_PURPOSE_ELIGIBLE_FOR_PROMOTION = "STRUCTURE_CHANNEL"
VALIDATION_FAMILY_REVISION = "rf-digital-q4.v1"

# What the strata *mean*, by revision. §5.20 correction D.
#
# `_strata_digest` hashed key, minimum and buildability, and not the meaning. So
# redefining a stratum without touching its name, its count or its buildability
# was invisible to `PromotionCorpusLock` -- the identical gap
# `VALIDATION_FAMILY_REVISION` exists to close one level up, where the bound
# count alone would not notice a family whose membership was rewritten while its
# size stayed the same.
#
# v2 is the redefinition of `GAIN_STEPS` and `RETUNE_TRANSIENTS` as the first
# complete post-invalidation window. v1 described windows the ring cannot issue:
# `GAIN_CHANGE` and `RETUNE` both clear the buffer, so nothing can span one.
#
# **Descriptions are not hashed.** This advances when the operational meaning
# changes, which editorial punctuation is not. A digest over prose would fail in
# both directions at once -- a comma would read as a strata change, and a
# genuine redefinition that reused the same words would not.
STRATA_DEFINITION_REVISION = "rf-null-strata.v2"

# The review named its members in operator vocabulary; the corpus contract names
# them in the vocabulary the strata are actually keyed by. Both are recorded, so
# the correspondence can be audited rather than assumed, and the corpus keys are
# canonical because those are what a labelled window will carry.
FAMILY_MEMBER_ALIASES: Dict[str, str] = {
    "THERMAL_NOISE": "THERMAL_NO_INPUT",
    "ANALOGUE_AM": "AM",
    "ANALOGUE_FM": "STATIONARY_ANALOGUE_FM",
    "OVERLOADED_CLIPPED_INPUT": "OVERLOADED_CLIPPED",
}

# Conditions that would make the family larger. Each is a second path allowed to
# emit the promoted claim, which is a second hypothesis test whatever it is
# called internally.
FAMILY_EXPANSION_TRIGGERS: Tuple[str, ...] = (
    "MULTIPLE_STRUCTURE_CHANNEL_MARGINS_INDEPENDENTLY_PROMOTABLE",
    "MULTIPLE_FIR_REVISIONS_INDEPENDENTLY_PROMOTABLE",
    "MULTIPLE_THRESHOLD_VARIANTS_INDEPENDENTLY_PROMOTABLE",
    "ALTERNATE_DETECTOR_PREPROCESSING_PATHS",
    "SEPARATE_DETECTOR_DECISIONS_FROM_MEASUREMENT_CHANNELS",
    "MULTIPLE_METHODS_EACH_ALLOWED_TO_EMIT_THE_PROMOTED_CLAIM",
)

# The rule that decides whether configuration selection enlarges the family.
#
# Selecting one frozen structure configuration against development data, and
# opening the promotion corpus only afterwards, does not enlarge it: only one
# hypothesis is ever tested against the corpus. Running several configurations
# against the promotion corpus and keeping the best does enlarge it, by exactly
# the number run. Calling them configuration experiments does not stop them being
# multiple hypothesis tests.
SELECTION_RULE: Dict[str, str] = {
    "SELECTED_BEFORE_CORPUS_OPENED": (
        "DOES NOT ENLARGE THE FAMILY. ONE CONFIGURATION IS FROZEN AGAINST "
        "DEVELOPMENT DATA AND IS THE ONLY ONE THE CORPUS EVER SEES"
    ),
    "SELECTED_AGAINST_THE_PROMOTION_CORPUS": (
        "ENLARGES THE FAMILY BY THE NUMBER OF CONFIGURATIONS RUN. CHOOSING THE "
        "BEST OF SEVERAL IS A MULTIPLE COMPARISON WHATEVER IT IS CALLED, AND THE "
        "BOUND COUNT MUST RISE TO MATCH BEFORE ANY VERDICT IS READ"
    ),
}


def family_manifest() -> Dict[str, Any]:
    """The immutable membership, fixed before any trial is run.

    Built from STRATA rather than transcribed beside it: a hand-written list
    would be a second source of truth for the one thing that may not drift.
    """
    members = ("aggregate",) + STRATUM_KEYS
    return {
        "validation_family_revision": VALIDATION_FAMILY_REVISION,
        "simultaneous_control": SIMULTANEOUS_CONTROL,
        "family_alpha": FAMILY_ALPHA,
        "members": list(members),
        "member_count": len(members),
        "tested_bound_count": TESTED_BOUND_COUNT,
        "per_bound_alpha": PER_BOUND_ALPHA,
        "minimum_zero_failure_trials_per_bound":
            MINIMUM_TRIALS_FOR_ZERO_FAILURES_CORRECTED,
        "channel_purpose_eligible_for_promotion": CHANNEL_PURPOSE_ELIGIBLE_FOR_PROMOTION,
        "measurement_channel_verdict_production": "PROHIBITED",
        "member_aliases": dict(FAMILY_MEMBER_ALIASES),
        "expansion_triggers": list(FAMILY_EXPANSION_TRIGGERS),
        "selection_rule": dict(SELECTION_RULE),
        "immutability": (
            "MEMBERSHIP IS FIXED BEFORE TRIALS BEGIN. A CHANGE AFTERWARDS IS "
            "STRATA_CHANGED_AFTER_FREEZE OR BOUND_COUNT_CHANGED_AFTER_FREEZE AND "
            "INVALIDATES EVERY BOUND ALREADY READ"
        ),
    }


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
    # The family this corpus was opened against, by revision. The bound count
    # alone would not notice a family whose membership was rewritten while its
    # size stayed the same.
    validation_family_revision: str = VALIDATION_FAMILY_REVISION
    # What the strata mean, not merely which ones there are. Frozen alongside
    # the digest rather than only inside it, so a lock can be read without
    # recomputing a hash to find out which definitions it was opened against.
    strata_definition_revision: str = STRATA_DEFINITION_REVISION
    # Which lineage was eligible to emit the promoted claim when the corpus
    # opened. Allowing a second one afterwards is a second hypothesis test, and
    # the thirteen-bound denominator would no longer cover it.
    eligible_channel_purpose: str = CHANNEL_PURPOSE_ELIGIBLE_FOR_PROMOTION

    def to_dict(self) -> Dict[str, Any]:
        return {field: getattr(self, field) for field in self.__dataclass_fields__}


def _configuration_digest(method_revision: str, decision_threshold: float,
                          preprocessing_revision: str) -> str:
    payload = f"{method_revision}|{float(decision_threshold):.12g}|{preprocessing_revision}"
    return f"blake2s:{hashlib.blake2s(payload.encode(), digest_size=16).hexdigest()}"


def _strata_digest() -> str:
    """Key, minimum, buildability -- and the revision that says what they mean.

    `STRATA_DEFINITION_REVISION` is in here because without it a stratum could
    be redefined while its name, count and buildability stayed put, and the lock
    would record a digest that had not moved over a population that had. §5.20
    correction D.
    """
    payload = "|".join([STRATA_DEFINITION_REVISION] +
                       [f"{s.key}:{s.minimum_windows}:{int(s.buildable)}"
                        for s in STRATA])
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
        validation_family_revision=VALIDATION_FAMILY_REVISION,
        strata_definition_revision=STRATA_DEFINITION_REVISION,
        eligible_channel_purpose=CHANNEL_PURPOSE_ELIGIBLE_FOR_PROMOTION,
    )


# -- completion, which is not precommitment ---------------------------------
#
# §5.20 correction C split one overloaded state into two. `PromotionCorpusLock`
# is a **configuration precommitment**: it freezes the method, the threshold,
# the preprocessing and the strata before the first promotion window exists,
# because otherwise thresholds get tuned against the windows that validate them.
# It holds no window counts and never did -- read its fields.
#
# `CorpusCompletionReceipt` is the other half: it says every declared stratum is
# present at its fixed count. Nothing may claim a corpus is complete without
# one, and an incomplete corpus may be configuration-frozen indefinitely.
#
# The old rule -- no lock while any stratum is absent -- forbade the
# precommitment until after the thing it precommits to.
COMPLETION_RECEIPT_SCHEMA = "scythe.rf-corpus-completion.v1"

COMPLETION_LOCK_ABSENT = "COMPLETION_LOCK_ABSENT"
COMPLETION_LOCK_STRATA_MOVED = "COMPLETION_LOCK_STRATA_MOVED"
COMPLETION_LOCK_DECLARATION_MOVED = "COMPLETION_LOCK_DECLARATION_MOVED"
COMPLETION_STRATUM_MISSING = "COMPLETION_STRATUM_MISSING"
COMPLETION_STRATUM_UNKNOWN = "COMPLETION_STRATUM_UNKNOWN"
COMPLETION_COUNT_BELOW_REQUIRED = "COMPLETION_COUNT_BELOW_REQUIRED"
COMPLETION_COUNT_ABOVE_REQUIRED = "COMPLETION_COUNT_ABOVE_REQUIRED"
COMPLETION_COUNT_UNCOUNTABLE = "COMPLETION_COUNT_UNCOUNTABLE"
COMPLETION_REFUSALS: Tuple[str, ...] = (
    COMPLETION_LOCK_ABSENT, COMPLETION_LOCK_STRATA_MOVED,
    COMPLETION_LOCK_DECLARATION_MOVED,
    COMPLETION_STRATUM_MISSING, COMPLETION_STRATUM_UNKNOWN,
    COMPLETION_COUNT_BELOW_REQUIRED, COMPLETION_COUNT_ABOVE_REQUIRED,
    COMPLETION_COUNT_UNCOUNTABLE,
)


class CompletionRefused(RuntimeError):
    """A receipt that was not issued. A code, never a repair."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# Every lock field, sorted by how it is checked. A test asserts the three sets
# partition `PromotionCorpusLock` exactly, so a field added later cannot quietly
# land on the unchecked side.
#
# One source: each declaration names how its current value is obtained, and the
# field list is **derived** from that mapping rather than typed beside it. A list
# and the dictionary it describes are one thing said twice, and the copy that
# falls behind is a field nobody checks under a heading that says it is checked.
#
# The values are thunks because they are not all knowable at import -- STRATA is
# defined further down, and `configuration_digest` is computed from the lock
# rather than from this module at all.
_DECLARATION_SOURCES = {
    "strata_digest": lambda lock: _strata_digest(),
    "strata_definition_revision": lambda lock: STRATA_DEFINITION_REVISION,
    "validation_family_revision": lambda lock: VALIDATION_FAMILY_REVISION,
    "tested_bound_count": lambda lock: TESTED_BOUND_COUNT,
    "per_bound_alpha": lambda lock: PER_BOUND_ALPHA,
    "eligible_channel_purpose": lambda lock: CHANNEL_PURPOSE_ELIGIBLE_FOR_PROMOTION,
    "configuration_digest": lambda lock: _configuration_digest(
        lock.method_revision, lock.decision_threshold,
        lock.preprocessing_revision),
}
LOCK_DECLARATION_FIELDS: Tuple[str, ...] = tuple(_DECLARATION_SOURCES)
# Not module constants -- they are this corpus's own choices -- but the lock's
# `configuration_digest` was computed from them, so recomputing that digest from
# the lock's own fields catches a substitution in any of the three.
LOCK_FIELDS_COVERED_BY_DIGEST: Tuple[str, ...] = (
    "method_revision", "decision_threshold", "preprocessing_revision",
)
# Not declarations at all: one names the corpus, the other times it, and neither
# has an answer anywhere to be checked against.
LOCK_FIELDS_NOT_DECLARATIONS: Tuple[str, ...] = ("corpus_id", "opened_at")


def _declaration_disagreements(lock: "PromotionCorpusLock"):
    """Every frozen declaration that disagrees with what is declared now.

    `type(lock) is PromotionCorpusLock` proves the object is the right class.
    It cannot prove the object is **internally coherent**, because the dataclass
    is publicly constructible and `dataclasses.replace` produces an exact
    `PromotionCorpusLock` carrying whatever was substituted. A forged
    `strata_definition_revision` or `validation_family_revision` was then copied
    straight into the receipt -- the receipt attesting to the lie it was handed.

    `configuration_digest` is checked against a digest **recomputed from the
    lock's own fields**, which is the internal-coherence half: substituting
    `method_revision` alone leaves a stored digest that no longer describes the
    configuration beside it.
    """
    expected = {name: source(lock)
                for name, source in _DECLARATION_SOURCES.items()}
    return tuple((name, getattr(lock, name), value)
                 for name, value in expected.items()
                 if getattr(lock, name) != value)


@dataclass(frozen=True)
class CorpusCompletionReceipt:
    """Every declared stratum, present at its fixed count, under one lock.

    **Pure and filesystem-free.** It counts nothing itself and looks at no
    directory: a caller supplies the counts and this decides whether they are a
    complete corpus. Whatever eventually counts files is a separate thing with
    its own authority, and this must not become the place that grows one.

    **It is not a promotion claim.** A complete corpus is a corpus that may be
    evaluated. What the evaluation then finds is a separate result, and a single
    false DIGITAL fails the frozen corpus rather than re-opening it.
    """

    schema: str
    corpus_id: str
    issued_at: float
    configuration_digest: str
    strata_digest: str
    strata_definition_revision: str
    validation_family_revision: str
    windows_per_stratum: int
    total_windows: int
    counted: Tuple[Tuple[str, int], ...]

    def to_dict(self) -> Dict[str, Any]:
        data = {field: getattr(self, field) for field in self.__dataclass_fields__}
        data["counted"] = {key: count for key, count in self.counted}
        data["promotes"] = False
        data["promotion_note"] = (
            "A COMPLETE CORPUS MAY BE EVALUATED. COMPLETION IS NOT A RESULT "
            "AND NEVER A PROMOTION"
        )
        return data


def issue_completion_receipt(*, lock: Any, counted: Mapping[str, Any],
                             issued_at: Optional[float] = None,
                             ) -> CorpusCompletionReceipt:
    """Issue a receipt, or refuse and say which stratum and why.

    The count is **exact**, not a floor. `MINIMUM_WINDOWS_PER_STRATUM` is what
    the zero-failure bound requires; the same number is also the ceiling,
    because a corpus that kept collecting until its bound improved would have a
    sample size that depended on the results it saw, and an upper bound computed
    that way is not the bound that was published. The two numbers coincide for
    unrelated reasons, and the field name records only the first of them.
    """
    if type(lock) is not PromotionCorpusLock:
        raise CompletionRefused(
            COMPLETION_LOCK_ABSENT,
            "a completion receipt requires the PromotionCorpusLock the corpus "
            f"was opened under; got {type(lock).__name__}")
    if lock.strata_digest != _strata_digest():
        raise CompletionRefused(
            COMPLETION_LOCK_STRATA_MOVED,
            "the strata set or its definition revision has moved since this "
            f"lock was opened: {lock.strata_digest} then, {_strata_digest()} now")
    disagreements = _declaration_disagreements(lock)
    if disagreements:
        raise CompletionRefused(
            COMPLETION_LOCK_DECLARATION_MOVED,
            "; ".join(f"{name} is {frozen!r} in the lock and {current!r} now"
                      for name, frozen, current in disagreements))

    supplied = dict(counted)
    unknown = sorted(set(supplied) - set(STRATUM_KEYS))
    if unknown:
        raise CompletionRefused(COMPLETION_STRATUM_UNKNOWN,
                                f"{unknown} are not declared strata")
    missing = [key for key in STRATUM_KEYS if key not in supplied]
    if missing:
        raise CompletionRefused(
            COMPLETION_STRATUM_MISSING,
            f"{missing} have no count. A corpus missing a stratum is "
            "incomplete, not complete with a gap")

    required = MINIMUM_WINDOWS_PER_STRATUM
    for key in STRATUM_KEYS:
        count = supplied[key]
        if type(count) is not int:
            raise CompletionRefused(
                COMPLETION_COUNT_UNCOUNTABLE,
                f"{key} was counted as {type(count).__name__}, which is not a "
                "number of windows")
        if count < required:
            raise CompletionRefused(
                COMPLETION_COUNT_BELOW_REQUIRED,
                f"{key} holds {count} of the {required} the bound requires")
        if count > required:
            raise CompletionRefused(
                COMPLETION_COUNT_ABOVE_REQUIRED,
                f"{key} holds {count}, above the fixed {required}. A sample "
                "whose size depended on the results is not the sample the "
                "published bound was computed over")

    ordered = tuple((key, supplied[key]) for key in STRATUM_KEYS)
    return CorpusCompletionReceipt(
        schema=COMPLETION_RECEIPT_SCHEMA,
        corpus_id=lock.corpus_id,
        issued_at=time.time() if issued_at is None else float(issued_at),
        configuration_digest=lock.configuration_digest,
        strata_digest=lock.strata_digest,
        strata_definition_revision=lock.strata_definition_revision,
        validation_family_revision=lock.validation_family_revision,
        windows_per_stratum=required,
        total_windows=sum(count for _, count in ordered),
        counted=ordered,
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
# §5.18, accepted 2026-09-14 as option C. Every stratum carries its own bound at
# the family-corrected confidence, so every stratum needs the same number of
# zero-failure trials to reach the approved rate. The minima this replaces were
# arithmetic that had fallen behind §5.12's correction, not a judgement about how
# much validation each condition deserves.
#
# The old column ran 500..2_000, where the exact upper bound at zero failures is
# 0.011060 down to 0.002776: **every stratum failed its own gate, all twelve**,
# while the aggregate cleared comfortably at 0.000473 -- the purchase-with-
# thermal-noise §5.8 was written to prevent, arriving through the column §5.8 did
# not update.
#
# Derived rather than typed. 5_561 is what the correction requires, and writing
# it as a literal here would be a number that stops tracking the alpha it came
# from the moment TESTED_BOUND_COUNT moves.
MINIMUM_WINDOWS_PER_STRATUM = MINIMUM_TRIALS_FOR_ZERO_FAILURES_CORRECTED

STRATA: Tuple[Stratum, ...] = (
    Stratum("THERMAL_NO_INPUT", "Terminated or disconnected input: thermal noise only",
            MINIMUM_WINDOWS_PER_STRATUM, safety_critical=False),
    Stratum("STATIONARY_ANALOGUE_FM", "Steady analogue FM voice or tone",
            MINIMUM_WINDOWS_PER_STRATUM, safety_critical=True),
    Stratum("AM", "Amplitude-modulated analogue carrier", MINIMUM_WINDOWS_PER_STRATUM, safety_critical=True),
    # The trap this whole ontology exists for: constant-envelope digital is
    # invisible to an envelope test, so it belongs in the NULL corpus for an
    # ANALOGUE detector and in the POSITIVE corpus for a symbol-clock detector.
    # Here it is null for false-DIGITAL only in the sense that a *wrong* family
    # call on it is the most expensive error the system can make.
    Stratum("CONSTANT_ENVELOPE_DIGITAL", "P25 C4FM, DMR and similar: no envelope cue",
            MINIMUM_WINDOWS_PER_STRATUM, safety_critical=True),
    Stratum("ADJACENT_CHANNEL_INTERFERENCE", "A strong neighbour inside the analysis span",
            MINIMUM_WINDOWS_PER_STRATUM, safety_critical=True),
    Stratum("DC_CONTAMINATION", "Zero-IF DC artefact at or near the channel",
            MINIMUM_WINDOWS_PER_STRATUM, safety_critical=False),
    # Buildable as of 2026-09-03: SDRPPBridge.set_gain drives the tuner through
    # rtl_tcp's control channel, restricted to the gains the device reports, and
    # IQRetentionOwner.set_gain_db raises GAIN_CHANGE. A corpus can produce a gain
    # step rather than assert one.
    # Redefined at STRATA_DEFINITION_REVISION v2 (§5.20 correction B). The v1
    # descriptions -- "a gain change part-way through the window" and "samples
    # spanning or adjacent to a retune" -- described windows this receiver
    # cannot produce: `GAIN_CHANGE` and `RETUNE` are both invalidation reasons,
    # the ring is cleared at the event, and `acquire_window` refuses with
    # INSUFFICIENT_WINDOW until a full window of new samples has arrived. What
    # these strata actually test is settling behaviour in the first window of a
    # new configuration, which is the honest version of what v1 gestured at.
    Stratum("GAIN_STEPS",
            "First complete window after GAIN_CHANGE, linked to the gain "
            "declarations either side",
            MINIMUM_WINDOWS_PER_STRATUM, safety_critical=True),
    Stratum("RETUNE_TRANSIENTS",
            "First complete window after RETUNE, linked to the tuning "
            "declarations either side",
            MINIMUM_WINDOWS_PER_STRATUM, safety_critical=True),
    # Buildable as of 2026-09-03: ClockContinuityMonitor compares the decoded
    # sample count against elapsed time on every append and separates a transport
    # GAP from a rate DRIFT, so a gap is observed rather than assumed.
    Stratum("DROPPED_FRAMES_TIMING_GAPS", "Lost samples and discontinuous timestamps",
            MINIMUM_WINDOWS_PER_STRATUM, safety_critical=True),
    Stratum("OVERLOADED_CLIPPED", "Converter saturation", MINIMUM_WINDOWS_PER_STRATUM, safety_critical=True),
    Stratum("RECEIVER_SPURS", "Internal spurious products and images", MINIMUM_WINDOWS_PER_STRATUM,
            safety_critical=False),
    Stratum("TWO_SIGNAL_COLLISIONS", "Two emitters sharing the analysed span",
            MINIMUM_WINDOWS_PER_STRATUM, safety_critical=True),
)

STRATUM_KEYS: Tuple[str, ...] = tuple(stratum.key for stratum in STRATA)

# The corpus target is the sum of what the strata require: 66_732 today. It moves
# only when the strata set or the correction moves, and then it moves by itself.
# The aggregate remains the thirteenth bound and never substitutes for a stratum.
TARGET_TOTAL_NULL_WINDOWS = sum(stratum.minimum_windows for stratum in STRATA)


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
    if lock.validation_family_revision != VALIDATION_FAMILY_REVISION:
        # A family can be rewritten without changing size, and a denominator that
        # still reads 13 would hide it.
        return "FAMILY_REVISION_CHANGED_AFTER_FREEZE", False
    if lock.eligible_channel_purpose != CHANNEL_PURPOSE_ELIGIBLE_FOR_PROMOTION:
        # A second eligible lineage is a second opportunity to cross the same
        # threshold. Thirteen bounds do not cover fourteen chances.
        return "ELIGIBLE_PURPOSE_CHANGED_AFTER_FREEZE", False
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
        "family_manifest": family_manifest(),
    }
