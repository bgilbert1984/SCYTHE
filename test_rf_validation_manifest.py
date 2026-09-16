"""Q4 is a bound, not a fraction, and this is what that costs in trials."""

import unittest
from dataclasses import replace

import rf_symbol_clock
from rf_validation_manifest import (
    CONFIDENCE, MAX_FALSE_DIGITAL_RATE, MINIMUM_TRIALS_FOR_ZERO_FAILURES,
    MINIMUM_TRIALS_FOR_ZERO_FAILURES_CORRECTED, MINIMUM_WINDOWS_PER_STRATUM,
    PER_BOUND_ALPHA,
    PER_BOUND_CONFIDENCE, STRATA, STRATUM_KEYS, TARGET_TOTAL_NULL_WINDOWS,
    TESTED_BOUND_COUNT, clopper_pearson_upper, evaluate, family_manifest,
    freeze_promotion_corpus, manifest_status, wilson_upper,
)
import rf_validation_manifest as manifest
from rf_validation_manifest import (
    COMPLETION_COUNT_ABOVE_REQUIRED, COMPLETION_COUNT_BELOW_REQUIRED,
    COMPLETION_COUNT_UNCOUNTABLE, COMPLETION_LOCK_ABSENT,
    COMPLETION_LOCK_STRATA_MOVED, COMPLETION_STRATUM_MISSING,
    COMPLETION_LOCK_DECLARATION_MOVED, COMPLETION_STRATUM_UNKNOWN,
    LOCK_DECLARATION_FIELDS, LOCK_FIELDS_COVERED_BY_DIGEST,
    LOCK_FIELDS_NOT_DECLARATIONS, STRATA_DEFINITION_REVISION,
    CompletionRefused, CorpusCompletionReceipt, PromotionCorpusLock,
    issue_completion_receipt,
)


from rf_promotion_envelope import (
    RECEIVER_ATTESTED_UNIQUE, RECEIVER_INSTANCE_UNIQUENESS_UNATTESTED,
    CONFIDENCE_MIXING_TERMINATION_AND_SECOND_TIME,
    ChainMember, FrontEnd, InstrumentChainEnvelope, VisitAllocation,
    declare_capture_plan, generate_visit_schedule,
)


def _member(gain=20.0, antenna="ANT_A", sensor="rtl2838-unit-1",
            authority=RECEIVER_ATTESTED_UNIQUE):
    return ChainMember(
        sensor_id=sensor, receiver_identity_authority=authority,
        sample_type="cs8", sample_rate_hz=2_048_000.0,
        front_end=FrontEnd(antenna=antenna, extension_mm=0.0,
                           feedline="RG316-1M", feedline_length_m=1.0),
        gain_db=gain)


def _envelope(authority=RECEIVER_ATTESTED_UNIQUE, sensor="rtl2838-unit-1"):
    """Two gains and two front ends -- but four written-down members, not a
    product of two sets of two, which would be the same four by accident."""
    return InstrumentChainEnvelope(members=tuple(
        _member(gain=gain, antenna=antenna, sensor=sensor, authority=authority)
        for antenna in ("ANT_A", "TERMINATION_50R")
        for gain in (20.0, 40.0)))


def _sparse_envelope(**kwargs):
    """Exactly the two pairings a plan sampled -- not the four a product of the
    two declared gains and the two declared front ends would admit."""
    return InstrumentChainEnvelope(members=(
        _member(gain=20.0, antenna="ANT_A", **kwargs),
        _member(gain=40.0, antenna="TERMINATION_50R", **kwargs)))


def _plan(envelope=None, seed=20260915):
    envelope = _envelope() if envelope is None else envelope
    chains = sorted(envelope.admissible_chain_hashes())
    schedule = generate_visit_schedule(seed=seed)
    allocations = tuple(
        VisitAllocation(
            position=visit.position, chain_hash=chains[visit.position % len(chains)],
            stratum=STRATUM_KEYS[visit.position % len(STRATUM_KEYS)],
            block=f"block-{visit.position // 64}", condition="NOMINAL",
            confidence_requirement=CONFIDENCE_MIXING_TERMINATION_AND_SECOND_TIME)
        for visit in schedule)
    return declare_capture_plan(
        seed=seed, envelope=envelope, allocations=allocations,
        reference_hz=28_800_000.0, reference_ppm=1.0)


def _presented_chain(envelope=None):
    envelope = _envelope() if envelope is None else envelope
    return sorted(envelope.admissible_chain_hashes())[0]


def _lock(corpus_id="corpus-a", envelope=None):
    envelope = _envelope() if envelope is None else envelope
    return freeze_promotion_corpus(
        corpus_id=corpus_id, method_revision="squared-envelope-cyclic.v1",
        decision_threshold=6.0, preprocessing_revision="pre.v1",
        envelope=envelope, capture_plan=_plan(envelope), opened_at=1_000.0)


def _full_counts():
    return {key: MINIMUM_WINDOWS_PER_STRATUM for key in STRATUM_KEYS}


class BoundTests(unittest.TestCase):

    def test_zero_failures_in_one_hundred_trials_is_not_evidence_of_a_low_rate(self):
        """The whole reason the gate is a bound: 0/100 looks perfect and is not."""
        bound = clopper_pearson_upper(0, 100, confidence=0.95)
        self.assertGreater(bound, MAX_FALSE_DIGITAL_RATE * 20)
        self.assertAlmostEqual(bound, 0.0295, places=3)

    def test_the_rule_of_three_is_reproduced_exactly_at_95_percent(self):
        """~3/n for zero failures, so ~3000 trials to reach 0.001 at 95%."""
        self.assertEqual(MINIMUM_TRIALS_FOR_ZERO_FAILURES, 3000)
        self.assertGreater(clopper_pearson_upper(0, 2_900, confidence=0.95),
                           MAX_FALSE_DIGITAL_RATE)
        self.assertLessEqual(clopper_pearson_upper(0, 3_000, confidence=0.95),
                             MAX_FALSE_DIGITAL_RATE)

    def test_the_family_correction_costs_trials_and_says_how_many(self):
        """Thirteen bounds at 95% do not give the family 95%, and it is not free."""
        self.assertEqual(TESTED_BOUND_COUNT, 13)
        self.assertAlmostEqual(PER_BOUND_ALPHA, 0.05 / 13, places=12)
        self.assertAlmostEqual(PER_BOUND_CONFIDENCE, 0.9961538, places=6)
        # Roughly -ln(alpha)/rate, and materially more than the 95% figure.
        self.assertEqual(MINIMUM_TRIALS_FOR_ZERO_FAILURES_CORRECTED, 5561)
        self.assertGreater(MINIMUM_TRIALS_FOR_ZERO_FAILURES_CORRECTED,
                           MINIMUM_TRIALS_FOR_ZERO_FAILURES)
        # The default bound is the corrected one, not the nominal 95%.
        self.assertGreater(clopper_pearson_upper(0, 3_000), MAX_FALSE_DIGITAL_RATE)
        self.assertLessEqual(clopper_pearson_upper(0, 5_561), MAX_FALSE_DIGITAL_RATE)

    def test_the_bound_is_exact_rather_than_normal_approximate(self):
        """A Wald interval has zero width at zero failures; this must not."""
        self.assertGreater(clopper_pearson_upper(0, 10_000), 0.0)

    def test_wilson_is_not_a_substitute_at_the_corrected_confidence(self):
        """The usual ordering holds at 95% and reverses at the family alpha.

        Wilson's normal approximation degrades in a far tail with a tiny observed
        rate, so at 99.6154% it sits above the exact bound throughout this gate's
        operating regime. Harmless in a number nobody gates on, and exactly why
        the gate is the exact bound.
        """
        for failures, trials in ((0, 10_000), (1, 10_000), (5, 10_000), (50, 10_000)):
            self.assertLess(wilson_upper(failures, trials, confidence=0.95),
                            clopper_pearson_upper(failures, trials, confidence=0.95))
            self.assertGreater(wilson_upper(failures, trials),
                               clopper_pearson_upper(failures, trials))

    def test_the_bound_rises_with_failures_and_falls_with_trials(self):
        self.assertLess(clopper_pearson_upper(0, 10_000), clopper_pearson_upper(1, 10_000))
        self.assertLess(clopper_pearson_upper(1, 20_000), clopper_pearson_upper(1, 10_000))
        self.assertEqual(clopper_pearson_upper(5, 5), 1.0)
        self.assertIsNone(clopper_pearson_upper(0, 0))


class StratificationTests(unittest.TestCase):

    def test_all_twelve_approved_strata_are_present(self):
        self.assertEqual(len(STRATA), 12)
        for key in ("THERMAL_NO_INPUT", "STATIONARY_ANALOGUE_FM", "AM",
                    "CONSTANT_ENVELOPE_DIGITAL", "ADJACENT_CHANNEL_INTERFERENCE",
                    "DC_CONTAMINATION", "GAIN_STEPS", "RETUNE_TRANSIENTS",
                    "DROPPED_FRAMES_TIMING_GAPS", "OVERLOADED_CLIPPED",
                    "RECEIVER_SPURS", "TWO_SIGNAL_COLLISIONS"):
            self.assertIn(key, STRATUM_KEYS)

    def test_thermal_noise_cannot_carry_a_failing_stratum(self):
        """The aggregate passes comfortably; one safety-critical stratum does not.

        Sized from the declared minimum rather than a round number, so §5.18's
        revision moved this fixture without anyone editing it. The point is the
        shape -- a vast easy stratum beside a small failing one -- and the shape
        is what the numbers below preserve.
        """
        observations = {key: (MINIMUM_WINDOWS_PER_STRATUM, 0)
                        for key in STRATUM_KEYS}
        observations["THERMAL_NO_INPUT"] = (50 * MINIMUM_WINDOWS_PER_STRATUM, 0)
        observations["CONSTANT_ENVELOPE_DIGITAL"] = (
            MINIMUM_WINDOWS_PER_STRATUM, 5)
        report = evaluate(observations)
        self.assertTrue(report["aggregate"]["passes"])
        self.assertIn("CONSTANT_ENVELOPE_DIGITAL", report["failing_strata"])
        self.assertFalse(report["promotes"])

    def test_a_stratum_below_its_own_minimum_does_not_pass_on_a_good_bound(self):
        observations = {key: (60_000, 0) for key in STRATUM_KEYS}
        observations["TWO_SIGNAL_COLLISIONS"] = (100, 0)
        report = evaluate(observations)
        entry = next(r for r in report["strata"] if r["stratum"] == "TWO_SIGNAL_COLLISIONS")
        self.assertEqual(entry["state"], "INSUFFICIENT_TRIALS")
        self.assertFalse(report["promotes"])

    def test_every_stratum_is_now_buildable(self):
        """GAIN_CHANGE and CLOCK_DISCONTINUITY are wired, so nothing is blocked."""
        report = evaluate({key: (60_000, 0) for key in STRATUM_KEYS})
        self.assertEqual(report["not_buildable"], [])
        self.assertEqual(report["failing_strata"], [])
        for stratum in STRATA:
            self.assertTrue(stratum.buildable, stratum.key)
            self.assertIsNone(stratum.blocked_by, stratum.key)

    def test_an_unbuildable_stratum_would_still_block_and_refuse_trials(self):
        """The mechanism stays, because DIRECT_SAMPLING_CHANGE is still unwired."""
        from rf_validation_manifest import Stratum, evaluate_stratum
        blocked = Stratum("HYPOTHETICAL", "not buildable", 100, safety_critical=True,
                          buildable=False, blocked_by="nothing calls it")
        entry = evaluate_stratum(blocked, 60_000, 0)
        self.assertEqual(entry["state"], "NOT_BUILDABLE")
        self.assertFalse(entry["passes"])
        # Trials are refused, not merely absent: a corpus cannot accumulate
        # against a stratum it could not honestly label.
        self.assertEqual(entry["trials"], 0)
        self.assertIsNone(entry["upper_bound_95"])

    def test_a_full_corpus_with_a_frozen_lock_promotes(self):
        """The whole gate, passing, so a failure elsewhere is not mistaken for it."""
        envelope = _envelope()
        lock = freeze_promotion_corpus(
            corpus_id="phase3-a", method_revision="squared-envelope-cyclic.v1",
            decision_threshold=8.4, preprocessing_revision="passband-local-excess-power.v1",
            envelope=envelope, capture_plan=_plan(envelope))
        configuration = {"method_revision": "squared-envelope-cyclic.v1",
                         "decision_threshold": 8.4,
                         "preprocessing_revision": "passband-local-excess-power.v1",
                         "signal_chain_hash": _presented_chain(envelope)}
        report = evaluate({key: (10_000, 0) for key in STRATUM_KEYS},
                          lock=lock, configuration=configuration)
        self.assertEqual(report["corpus_state"], "FROZEN")
        self.assertTrue(report["promotes"])
        self.assertIsNone(report["promotion_blocked_reason"])

    def test_a_corpus_without_a_lock_never_promotes(self):
        """Development against these windows is fine; calling it validation is not."""
        report = evaluate({key: (10_000, 0) for key in STRATUM_KEYS})
        self.assertEqual(report["corpus_state"], "NO_LOCK_EXPLORATORY")
        self.assertFalse(report["promotes"])
        self.assertEqual(report["promotion_blocked_reason"], "NO_LOCK_EXPLORATORY")

    def test_tuning_the_threshold_after_opening_the_corpus_voids_promotion(self):
        """Otherwise repeated tuning turns validation into training."""
        envelope = _envelope()
        lock = freeze_promotion_corpus(
            corpus_id="phase3-a", method_revision="squared-envelope-cyclic.v1",
            decision_threshold=8.4, preprocessing_revision="p.v1",
            envelope=envelope, capture_plan=_plan(envelope))
        observations = {key: (10_000, 0) for key in STRATUM_KEYS}
        tuned = {"method_revision": "squared-envelope-cyclic.v1",
                 "decision_threshold": 8.1, "preprocessing_revision": "p.v1",
                 "signal_chain_hash": _presented_chain(envelope)}
        report = evaluate(observations, lock=lock, configuration=tuned)
        self.assertFalse(report["promotes"])
        self.assertEqual(report["promotion_blocked_reason"],
                         "CONFIGURATION_CHANGED_AFTER_FREEZE")
        # And so does changing the preprocessing, not only the threshold.
        repro = dict(tuned, decision_threshold=8.4, preprocessing_revision="p.v2")
        self.assertEqual(evaluate(observations, lock=lock, configuration=repro)
                         ["promotion_blocked_reason"], "CONFIGURATION_CHANGED_AFTER_FREEZE")

    def test_the_aggregate_alone_does_not_promote(self):
        observations = {key: (60_000, 0) for key in STRATUM_KEYS}
        observations["RETUNE_TRANSIENTS"] = (60_000, 400)
        report = evaluate(observations)
        self.assertGreater(report["aggregate"]["upper_bound_95"], 0.0)
        self.assertIn("RETUNE_TRANSIENTS", report["failing_strata"])
        self.assertFalse(report["promotes"])

    def test_the_target_corpus_size_is_derived_not_transcribed(self):
        """§5.18. The target is the sum of what the strata require.

        Asserted as a relationship rather than as `66_732`: a literal here would
        be the second copy §5.18 warned about, and it would keep passing after
        the strata set or the Bonferroni denominator moved underneath it.
        """
        self.assertEqual(TARGET_TOTAL_NULL_WINDOWS,
                         sum(s.minimum_windows for s in STRATA))
        self.assertEqual(TARGET_TOTAL_NULL_WINDOWS,
                         len(STRATA) * MINIMUM_WINDOWS_PER_STRATUM)
        small = evaluate({key: (100, 0) for key in STRATUM_KEYS})
        self.assertFalse(small["aggregate"]["passes"])

    def test_every_stratum_can_meet_its_own_bound_at_its_own_minimum(self):
        """The §5.18 regression, stated as the thing that was false.

        Before 2026-09-14 every one of the twelve failed here, with zero
        observed failures, while the aggregate cleared at 0.000473.
        """
        report = evaluate({s.key: (s.minimum_windows, 0) for s in STRATA})
        self.assertEqual(report["failing_strata"], [])
        self.assertTrue(report["aggregate"]["passes"])
        for entry in report["strata"]:
            self.assertLessEqual(entry["upper_bound_95"],
                                 MAX_FALSE_DIGITAL_RATE, entry["stratum"])

    def test_the_declared_minimum_is_conservative_against_the_exact_bound(self):
        """The rule of three is asymptotic; the exact inversion clears earlier.

        `-ln(alpha)/rate` gives 5_561, while the exact Clopper-Pearson bound
        first reaches 0.001 at 5_558 — the declared minimum is three windows
        more than strictly required. Conservative in the safe direction, and
        recorded rather than trimmed: an approximation that errs toward more
        evidence is the right way for it to err, and inverting the exact bound
        to save three windows would be optimising the wrong quantity.
        """
        exact = next(n for n in range(1, 20_000)
                     if clopper_pearson_upper(0, n) <= MAX_FALSE_DIGITAL_RATE)
        self.assertEqual(exact, 5_558)
        self.assertGreater(clopper_pearson_upper(0, exact - 1),
                           MAX_FALSE_DIGITAL_RATE)
        self.assertGreaterEqual(MINIMUM_WINDOWS_PER_STRATUM, exact)
        self.assertLessEqual(
            clopper_pearson_upper(0, MINIMUM_WINDOWS_PER_STRATUM),
            MAX_FALSE_DIGITAL_RATE)

    def test_unknown_or_impossible_observations_are_refused(self):
        with self.assertRaises(ValueError):
            evaluate({"NOT_A_STRATUM": (10, 0)})
        with self.assertRaises(ValueError):
            evaluate({"THERMAL_NO_INPUT": (10, 11)})


class DeclarationTests(unittest.TestCase):

    def test_the_manifest_declares_no_corpus_has_been_collected(self):
        status = manifest_status()
        self.assertEqual(status["state"], "DECLARED_NO_CORPUS_COLLECTED")
        self.assertEqual(status["max_false_digital_rate"], MAX_FALSE_DIGITAL_RATE)
        self.assertEqual(status["confidence"], CONFIDENCE)
        self.assertEqual(status["gate_estimator"], "CLOPPER_PEARSON_EXACT")
        self.assertEqual(status["not_buildable"], [])
        self.assertEqual(len(status["strata"]), 12)
        self.assertEqual(status["simultaneous_control"], "BONFERRONI")
        self.assertEqual(status["tested_bound_count"], 13)
        self.assertEqual(status["promotion_corpus"], "FROZEN_LOCK_REQUIRED_FOR_PROMOTION")

    def test_the_rule_is_the_bound_and_says_so(self):
        self.assertIn("UPPER_CONFIDENCE_BOUND", manifest_status()["rule"])


class FamilyManifestTests(unittest.TestCase):
    """Membership is fixed before trials begin, and covers only what can promote."""

    def test_the_family_has_thirteen_members(self):
        manifest = family_manifest()
        self.assertEqual(manifest["validation_family_revision"], "rf-digital-q4.v1")
        self.assertEqual(manifest["member_count"], 13)
        self.assertEqual(manifest["members"][0], "aggregate")
        self.assertEqual(manifest["tested_bound_count"], 13)
        self.assertAlmostEqual(manifest["per_bound_alpha"], 0.003846153846, places=10)
        self.assertEqual(manifest["minimum_zero_failure_trials_per_bound"], 5561)

    def test_membership_is_derived_from_the_strata_not_transcribed(self):
        """A hand-written list would be a second source of truth for the one
        thing that may not drift."""
        self.assertEqual(family_manifest()["members"][1:], list(STRATUM_KEYS))

    def test_only_the_structure_channel_may_produce_the_promoted_claim(self):
        """Bonferroni covers eligible claims, not every provenance dimension."""
        manifest = family_manifest()
        self.assertEqual(manifest["channel_purpose_eligible_for_promotion"],
                         "STRUCTURE_CHANNEL")
        self.assertEqual(manifest["measurement_channel_verdict_production"],
                         "PROHIBITED")
        # And the prohibition is real, not just declared here.
        self.assertEqual(rf_symbol_clock.ELIGIBLE_CHANNEL_PURPOSE, "STRUCTURE_CHANNEL")

    def test_channel_purpose_aggregates_were_not_added(self):
        """The count stays at 13 because only one lineage can emit the claim."""
        self.assertEqual(TESTED_BOUND_COUNT, 13)
        self.assertNotIn("MEASUREMENT_CHANNEL", family_manifest()["members"])

    def test_the_review_vocabulary_maps_onto_the_corpus_keys(self):
        aliases = family_manifest()["member_aliases"]
        self.assertEqual(aliases["THERMAL_NOISE"], "THERMAL_NO_INPUT")
        self.assertEqual(aliases["ANALOGUE_FM"], "STATIONARY_ANALOGUE_FM")
        for corpus_key in aliases.values():
            self.assertIn(corpus_key, STRATUM_KEYS)

    def test_selection_before_the_corpus_does_not_enlarge_the_family(self):
        rule = family_manifest()["selection_rule"]
        self.assertIn("DOES NOT ENLARGE", rule["SELECTED_BEFORE_CORPUS_OPENED"])
        self.assertIn("ENLARGES THE FAMILY",
                      rule["SELECTED_AGAINST_THE_PROMOTION_CORPUS"])

    def test_every_expansion_trigger_is_a_second_promotable_path(self):
        triggers = family_manifest()["expansion_triggers"]
        self.assertEqual(len(triggers), 6)
        self.assertIn("MULTIPLE_STRUCTURE_CHANNEL_MARGINS_INDEPENDENTLY_PROMOTABLE",
                      triggers)


class FamilyLockTests(unittest.TestCase):
    """The lock notices a family rewritten without changing size."""

    def _lock(self, **overrides):
        envelope = _envelope()
        lock = freeze_promotion_corpus(
            corpus_id="c-1", method_revision="squared-envelope-cyclic.v1",
            decision_threshold=2.5, preprocessing_revision="rf-channelizer-fir.v1",
            envelope=envelope, capture_plan=_plan(envelope), opened_at=1000.0)
        return replace(lock, **overrides) if overrides else lock

    def _configuration(self):
        return {"method_revision": "squared-envelope-cyclic.v1",
                "decision_threshold": 2.5,
                "preprocessing_revision": "rf-channelizer-fir.v1",
                "signal_chain_hash": _presented_chain()}

    def test_a_matching_lock_is_frozen(self):
        result = evaluate({key: (6_000, 0) for key in STRATUM_KEYS},
                          lock=self._lock(), configuration=self._configuration())
        self.assertEqual(result["corpus_state"], "FROZEN")

    def test_a_family_rewritten_at_the_same_size_is_caught(self):
        """Thirteen bounds over different members is a different family."""
        result = evaluate({key: (6_000, 0) for key in STRATUM_KEYS},
                          lock=self._lock(validation_family_revision="rf-digital-q4.v2"),
                          configuration=self._configuration())
        self.assertEqual(result["corpus_state"], "FAMILY_REVISION_CHANGED_AFTER_FREEZE")
        self.assertFalse(result["promotes"])

    def test_a_second_eligible_lineage_is_caught(self):
        """Thirteen bounds do not cover fourteen chances at one threshold."""
        result = evaluate({key: (6_000, 0) for key in STRATUM_KEYS},
                          lock=self._lock(eligible_channel_purpose="MEASUREMENT_CHANNEL"),
                          configuration=self._configuration())
        self.assertEqual(result["corpus_state"], "ELIGIBLE_PURPOSE_CHANGED_AFTER_FREEZE")
        self.assertFalse(result["promotes"])


class StrataDefinitionRevisionTests(unittest.TestCase):
    """§5.20 correction D: the lock could not see a stratum being redefined."""

    def test_the_two_tuner_strata_are_the_first_window_after_the_event(self):
        """Correction B. The v1 wording described windows the ring cannot issue:
        GAIN_CHANGE and RETUNE both clear the buffer."""
        described = {s.key: s.description for s in STRATA}
        self.assertIn("First complete window after GAIN_CHANGE",
                      described["GAIN_STEPS"])
        self.assertIn("First complete window after RETUNE",
                      described["RETUNE_TRANSIENTS"])
        for key in ("GAIN_STEPS", "RETUNE_TRANSIENTS"):
            with self.subTest(key=key):
                self.assertNotIn("part-way", described[key])
                self.assertNotIn("spanning", described[key])

    def test_the_redefined_strata_name_an_actual_invalidation_reason(self):
        """A description that named an event the ring does not have would be
        the same class of mistake in a new coat."""
        from rf_iq_ring import INVALIDATION_REASONS
        described = {s.key: s.description for s in STRATA}
        self.assertIn("GAIN_CHANGE", INVALIDATION_REASONS)
        self.assertIn("RETUNE", INVALIDATION_REASONS)
        self.assertIn("GAIN_CHANGE", described["GAIN_STEPS"])
        self.assertIn("RETUNE", described["RETUNE_TRANSIENTS"])

    def test_the_lock_freezes_the_definition_revision(self):
        self.assertEqual(_lock().strata_definition_revision,
                         STRATA_DEFINITION_REVISION)
        self.assertEqual(STRATA_DEFINITION_REVISION, "rf-null-strata.v2")

    def test_the_strata_digest_moves_with_the_definition_revision(self):
        """The gap correction D closed: name, count and buildability unchanged,
        meaning changed, digest unmoved."""
        before = manifest._strata_digest()
        original = manifest.STRATA_DEFINITION_REVISION
        try:
            manifest.STRATA_DEFINITION_REVISION = "rf-null-strata.v3"
            self.assertNotEqual(manifest._strata_digest(), before)
        finally:
            manifest.STRATA_DEFINITION_REVISION = original
        self.assertEqual(manifest._strata_digest(), before)

    def test_the_digest_does_not_move_when_only_prose_moves(self):
        """Descriptions are deliberately not hashed. A digest over sentences
        would read a comma as a strata change, and would still miss a
        redefinition that reused the same words."""
        before = manifest._strata_digest()
        original = manifest.STRATA
        try:
            manifest.STRATA = tuple(
                replace(s, description=s.description + ".") for s in original)
            self.assertEqual(manifest._strata_digest(), before)
        finally:
            manifest.STRATA = original

    def test_the_digest_still_moves_with_the_things_it_always_covered(self):
        before = manifest._strata_digest()
        original = manifest.STRATA
        try:
            manifest.STRATA = tuple(
                replace(s, minimum_windows=s.minimum_windows + 1)
                for s in original)
            self.assertNotEqual(manifest._strata_digest(), before)
        finally:
            manifest.STRATA = original


class CompletionReceiptTests(unittest.TestCase):
    """§5.20 correction C: precommitment and completion are two states."""

    def test_a_complete_corpus_receives_a_receipt(self):
        receipt = issue_completion_receipt(lock=_lock(), counted=_full_counts(),
                                           issued_at=2_000.0)
        self.assertIs(type(receipt), CorpusCompletionReceipt)
        self.assertEqual(receipt.total_windows, TARGET_TOTAL_NULL_WINDOWS)
        self.assertEqual(receipt.windows_per_stratum, MINIMUM_WINDOWS_PER_STRATUM)
        self.assertEqual(len(receipt.counted), len(STRATUM_KEYS))

    def test_the_receipt_is_not_a_promotion_claim(self):
        """A complete corpus may be evaluated. What the evaluation finds is a
        separate result, and one false DIGITAL fails the frozen corpus."""
        data = issue_completion_receipt(lock=_lock(), counted=_full_counts(),
                                        issued_at=2_000.0).to_dict()
        self.assertFalse(data["promotes"])
        self.assertIn("NEVER A PROMOTION", data["promotion_note"])

    def test_a_corpus_without_its_lock_refuses(self):
        for absent in (None, "corpus-a", object()):
            with self.subTest(lock=type(absent).__name__):
                with self.assertRaises(CompletionRefused) as caught:
                    issue_completion_receipt(lock=absent, counted=_full_counts())
                self.assertEqual(caught.exception.code, COMPLETION_LOCK_ABSENT)

    def test_a_look_alike_lock_refuses_nominally(self):
        """`type(x) is T`. A duck-typed stand-in carrying the right attributes
        is the impostor surface every other gate here closes."""
        class NotALock:
            corpus_id = "corpus-a"
            strata_digest = manifest._strata_digest()
            configuration_digest = "blake2s:0"
            strata_definition_revision = STRATA_DEFINITION_REVISION
            validation_family_revision = "rf-digital-q4.v1"
        with self.assertRaises(CompletionRefused) as caught:
            issue_completion_receipt(lock=NotALock(), counted=_full_counts())
        self.assertEqual(caught.exception.code, COMPLETION_LOCK_ABSENT)

    def test_a_missing_stratum_refuses_by_name(self):
        counts = _full_counts()
        del counts["RECEIVER_SPURS"]
        with self.assertRaises(CompletionRefused) as caught:
            issue_completion_receipt(lock=_lock(), counted=counts)
        self.assertEqual(caught.exception.code, COMPLETION_STRATUM_MISSING)
        self.assertIn("RECEIVER_SPURS", caught.exception.detail)

    def test_an_undeclared_stratum_refuses(self):
        counts = dict(_full_counts(), INVENTED_STRATUM=MINIMUM_WINDOWS_PER_STRATUM)
        with self.assertRaises(CompletionRefused) as caught:
            issue_completion_receipt(lock=_lock(), counted=counts)
        self.assertEqual(caught.exception.code, COMPLETION_STRATUM_UNKNOWN)

    def test_a_short_stratum_refuses(self):
        counts = dict(_full_counts())
        counts["AM"] = MINIMUM_WINDOWS_PER_STRATUM - 1
        with self.assertRaises(CompletionRefused) as caught:
            issue_completion_receipt(lock=_lock(), counted=counts)
        self.assertEqual(caught.exception.code, COMPLETION_COUNT_BELOW_REQUIRED)

    def test_a_long_stratum_refuses_too(self):
        """The half that is not obvious. 5 561 is a ceiling as well as a floor:
        a sample whose size depended on the results it saw is not the sample
        the published bound was computed over."""
        counts = dict(_full_counts())
        counts["AM"] = MINIMUM_WINDOWS_PER_STRATUM + 1
        with self.assertRaises(CompletionRefused) as caught:
            issue_completion_receipt(lock=_lock(), counted=counts)
        self.assertEqual(caught.exception.code, COMPLETION_COUNT_ABOVE_REQUIRED)

    def test_a_count_that_is_not_a_count_refuses(self):
        for value in (True, 5_561.0, "5561", None):
            with self.subTest(value=repr(value)):
                counts = dict(_full_counts(), AM=value)
                with self.assertRaises(CompletionRefused) as caught:
                    issue_completion_receipt(lock=_lock(), counted=counts)
                self.assertEqual(caught.exception.code,
                                 COMPLETION_COUNT_UNCOUNTABLE)

    def test_a_lock_opened_against_different_strata_refuses(self):
        lock = _lock()
        original = manifest.STRATA_DEFINITION_REVISION
        try:
            manifest.STRATA_DEFINITION_REVISION = "rf-null-strata.v3"
            with self.assertRaises(CompletionRefused) as caught:
                issue_completion_receipt(lock=lock, counted=_full_counts())
            self.assertEqual(caught.exception.code, COMPLETION_LOCK_STRATA_MOVED)
        finally:
            manifest.STRATA_DEFINITION_REVISION = original

    def test_the_receipt_touches_no_filesystem(self):
        """Pure by construction: the module imports nothing that could."""
        import ast
        import pathlib
        tree = ast.parse(pathlib.Path("rf_validation_manifest.py").read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for forbidden in ("os", "pathlib", "shutil", "tempfile", "io", "open"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, imported)

    def test_the_lock_holds_no_window_counts(self):
        """Correction C's premise, asserted rather than assumed: the lock never
        was a completion certificate, so the old refusal described an object
        this module does not implement."""
        fields = set(PromotionCorpusLock.__dataclass_fields__)
        for name in fields:
            with self.subTest(field=name):
                self.assertNotIn("window", name)
                self.assertNotIn("count", name.replace("bound_count", ""))


class LockCoherenceTests(unittest.TestCase):
    """A nominal type check cannot establish internal coherence.

    `PromotionCorpusLock` is publicly constructible and `dataclasses.replace`
    returns an **exact** `PromotionCorpusLock` carrying whatever was
    substituted. Before this check, a forged `strata_definition_revision` or
    `validation_family_revision` was copied straight into the receipt -- the
    receipt attesting to the lie it had been handed.
    """

    def test_every_forged_declaration_refuses(self):
        lock = _lock()
        for field_name, forged in (
                ("strata_definition_revision", "rf-null-strata.v99"),
                ("validation_family_revision", "forged.v1"),
                ("tested_bound_count", 1),
                ("per_bound_alpha", 0.05),
                ("eligible_channel_purpose", "ANY_CHANNEL"),
                ("configuration_digest", "blake2s:" + "0" * 32)):
            with self.subTest(field=field_name):
                impostor = replace(lock, **{field_name: forged})
                self.assertIs(type(impostor), PromotionCorpusLock)
                with self.assertRaises(CompletionRefused) as caught:
                    issue_completion_receipt(lock=impostor,
                                             counted=_full_counts())
                self.assertEqual(caught.exception.code,
                                 COMPLETION_LOCK_DECLARATION_MOVED)
                self.assertIn(field_name, caught.exception.detail)

    def test_a_substituted_configuration_field_is_caught_by_its_own_digest(self):
        """The internal-coherence half. `method_revision` has no module-level
        answer to be checked against -- but the digest beside it was computed
        from it, and recomputing that digest from the lock's own fields is what
        notices."""
        lock = _lock()
        for field_name, forged in (("method_revision", "other.v9"),
                                   ("decision_threshold", 99.0),
                                   ("preprocessing_revision", "pre.v9")):
            with self.subTest(field=field_name):
                impostor = replace(lock, **{field_name: forged})
                with self.assertRaises(CompletionRefused) as caught:
                    issue_completion_receipt(lock=impostor,
                                             counted=_full_counts())
                self.assertEqual(caught.exception.code,
                                 COMPLETION_LOCK_DECLARATION_MOVED)
                self.assertIn("configuration_digest", caught.exception.detail)

    def test_no_forged_value_reaches_a_receipt(self):
        """What the hole actually cost: the receipt carried the forgery."""
        lock = _lock()
        for field_name in ("strata_definition_revision",
                           "validation_family_revision"):
            with self.subTest(field=field_name):
                impostor = replace(lock, **{field_name: "forged"})
                with self.assertRaises(CompletionRefused):
                    issue_completion_receipt(lock=impostor,
                                             counted=_full_counts())

    def test_every_lock_field_is_either_checked_or_declared_unchecked(self):
        """The partition, asserted so a field added later cannot quietly join
        the unchecked side.

        Three sets, not two: `method_revision`, `decision_threshold` and
        `preprocessing_revision` have no module-level answer to compare against
        and are caught through the digest computed from them. Writing that down
        was the correction; an earlier version of this test assumed two sets and
        failed, which is the test doing its job on its own author.
        """
        direct = set(LOCK_DECLARATION_FIELDS)
        via_digest = set(LOCK_FIELDS_COVERED_BY_DIGEST)
        unchecked = set(LOCK_FIELDS_NOT_DECLARATIONS)
        lock = _lock()
        # Three disjoint sets, together exactly the lock.
        self.assertEqual(direct & via_digest, set())
        self.assertEqual(direct & unchecked, set())
        self.assertEqual(via_digest & unchecked, set())
        self.assertEqual(direct | via_digest | unchecked,
                         set(PromotionCorpusLock.__dataclass_fields__))
        # And the declared list is the one the implementation uses, not a copy:
        # forging each name must produce a disagreement naming that name.
        for name in direct - {"configuration_digest"}:
            with self.subTest(field=name):
                value = getattr(lock, name)
                forged = replace(lock, **{name: f"{value}-forged"
                                          if isinstance(value, str) else 0})
                self.assertIn(
                    name, [row[0] for row
                           in manifest._declaration_disagreements(forged)])

    def test_a_genuine_lock_still_issues(self):
        """The check refuses forgeries and nothing else."""
        receipt = issue_completion_receipt(lock=_lock(), counted=_full_counts(),
                                           issued_at=2_000.0)
        self.assertEqual(receipt.strata_definition_revision,
                         STRATA_DEFINITION_REVISION)


class BuildabilityClaimTests(unittest.TestCase):
    """The docstring said two strata report NOT_BUILDABLE. None do."""

    def test_every_declared_stratum_is_buildable(self):
        self.assertEqual([s.key for s in STRATA if not s.buildable], [])

    def test_the_remaining_block_is_a_receiver_not_buildability(self):
        from rf_null_corpus import TUNER_REQUIRED
        self.assertEqual(sorted(TUNER_REQUIRED),
                         ["GAIN_STEPS", "RECEIVER_SPURS", "RETUNE_TRANSIENTS"])
        for key in TUNER_REQUIRED:
            with self.subTest(key=key):
                self.assertTrue(
                    next(s for s in STRATA if s.key == key).buildable)


if __name__ == "__main__":
    unittest.main()


class UseTimeAdmissionTests(unittest.TestCase):
    """§5.23's enforcement point, and the reason entry 11 can drain.

    A lock that carries an envelope nobody checks makes cross-chain licensing
    *recordable*, not *repaired*. These are the tests that fail if the check is
    removed and the field is left behind.
    """

    def _observations(self):
        return {key: (10_000, 0) for key in STRATUM_KEYS}

    def _configuration(self, envelope, chain=None):
        return {"method_revision": "squared-envelope-cyclic.v1",
                "decision_threshold": 6.0, "preprocessing_revision": "pre.v1",
                "signal_chain_hash": chain or _presented_chain(envelope)}

    def test_a_chain_inside_the_frozen_envelope_promotes(self):
        envelope = _envelope()
        report = evaluate(self._observations(), lock=_lock(envelope=envelope),
                          configuration=self._configuration(envelope))
        self.assertEqual(report["corpus_state"], "FROZEN")
        self.assertTrue(report["promotes"])

    def test_a_chain_outside_the_frozen_envelope_does_not(self):
        """The defect entry 11 names: a corpus validated on one chain licensing
        a promoted claim from another."""
        envelope = _envelope()
        stray = _member(gain=33.0, antenna="ANT_UNSAMPLED").chain_hash()
        report = evaluate(self._observations(), lock=_lock(envelope=envelope),
                          configuration=self._configuration(envelope, stray))
        self.assertEqual(report["corpus_state"], "CHAIN_OUTSIDE_FROZEN_ENVELOPE")
        self.assertFalse(report["promotes"])
        self.assertEqual(report["promotion_blocked_reason"],
                         "CHAIN_OUTSIDE_FROZEN_ENVELOPE")

    def test_an_unsampled_combination_of_declared_parts_does_not_promote(self):
        """The Cartesian-product correction, reaching all the way to promotion.

        Both the gain and the front end are declared; the pairing is not, and a
        product rule would have licensed it here.
        """
        envelope = _sparse_envelope()
        unsampled = _member(gain=40.0, antenna="ANT_A").chain_hash()
        self.assertEqual(len(envelope.admissible_chain_hashes()), 2)
        report = evaluate(self._observations(), lock=_lock(envelope=envelope),
                          configuration=self._configuration(envelope, unsampled))
        self.assertEqual(report["corpus_state"], "CHAIN_OUTSIDE_FROZEN_ENVELOPE")

    def test_a_configuration_with_no_chain_does_not_promote(self):
        """Silence is not admission. An evaluation that forgot to present the
        instrument must not inherit the last one that did."""
        envelope = _envelope()
        configuration = self._configuration(envelope)
        del configuration["signal_chain_hash"]
        report = evaluate(self._observations(), lock=_lock(envelope=envelope),
                          configuration=configuration)
        self.assertEqual(report["corpus_state"], "CHAIN_NOT_PRESENTED")
        self.assertFalse(report["promotes"])

    def test_a_substituted_envelope_is_caught_by_its_own_digest(self):
        """`dataclasses.replace` makes an exact PromotionCorpusLock carrying an
        instrument the corpus was never built on."""
        mine = _envelope()
        theirs = _envelope(sensor="rtl2838-unit-9")
        forged = replace(_lock(envelope=mine), envelope=theirs)
        report = evaluate(self._observations(), lock=forged,
                          configuration=self._configuration(theirs))
        self.assertEqual(report["corpus_state"], "ENVELOPE_CHANGED_AFTER_FREEZE")
        self.assertFalse(report["promotes"])

    def test_a_substituted_capture_plan_is_caught_too(self):
        envelope = _envelope()
        forged = replace(_lock(envelope=envelope),
                         capture_plan=_plan(envelope, seed=1))
        report = evaluate(self._observations(), lock=forged,
                          configuration=self._configuration(envelope))
        self.assertEqual(report["corpus_state"],
                         "CAPTURE_PLAN_CHANGED_AFTER_FREEZE")

    def test_an_unattested_receiver_identity_never_promotes(self):
        """Everything else is right. The claim would still be about a unit
        nobody can name, so it is not made."""
        envelope = _envelope(authority=RECEIVER_INSTANCE_UNIQUENESS_UNATTESTED)
        report = evaluate(self._observations(), lock=_lock(envelope=envelope),
                          configuration=self._configuration(envelope))
        self.assertEqual(report["corpus_state"],
                         "RECEIVER_IDENTITY_NOT_SUFFICIENT_FOR_PROMOTION")
        self.assertFalse(report["promotes"])

    def test_an_unattested_corpus_may_still_be_opened_and_evaluated(self):
        """Buildable, evaluable, and not promotable -- three different things."""
        envelope = _envelope(authority=RECEIVER_INSTANCE_UNIQUENESS_UNATTESTED)
        lock = _lock(envelope=envelope)
        self.assertEqual(lock.envelope.receiver_identity_authority,
                         RECEIVER_INSTANCE_UNIQUENESS_UNATTESTED)
        report = evaluate(self._observations(), lock=lock,
                          configuration=self._configuration(envelope))
        self.assertTrue(report["aggregate"]["passes"])


class LockRequiresAnInstrumentTests(unittest.TestCase):

    def test_a_corpus_cannot_be_opened_without_an_envelope(self):
        """No default. A lock openable without an instrument is the hole."""
        with self.assertRaises(TypeError):
            freeze_promotion_corpus(
                corpus_id="c", method_revision="m", decision_threshold=1.0,
                preprocessing_revision="p")

    def test_an_envelope_that_is_not_one_refuses(self):
        with self.assertRaises(manifest.LockRefused) as caught:
            freeze_promotion_corpus(
                corpus_id="c", method_revision="m", decision_threshold=1.0,
                preprocessing_revision="p", envelope={"sensor": "rtl2838"},
                capture_plan=_plan())
        self.assertEqual(caught.exception.code, manifest.LOCK_ENVELOPE_ABSENT)

    def test_a_plan_that_is_not_one_refuses(self):
        with self.assertRaises(manifest.LockRefused) as caught:
            freeze_promotion_corpus(
                corpus_id="c", method_revision="m", decision_threshold=1.0,
                preprocessing_revision="p", envelope=_envelope(),
                capture_plan="the usual sweep")
        self.assertEqual(caught.exception.code, manifest.LOCK_PLAN_ABSENT)

    def test_a_plan_allocating_outside_its_envelope_refuses(self):
        """Two declarations that disagree, surfaced at the lock rather than at
        the first window."""
        mine = _envelope()
        theirs = _envelope(sensor="rtl2838-unit-9")
        with self.assertRaises(manifest.LockRefused) as caught:
            freeze_promotion_corpus(
                corpus_id="c", method_revision="m", decision_threshold=1.0,
                preprocessing_revision="p", envelope=mine,
                capture_plan=_plan(theirs))
        self.assertEqual(caught.exception.code,
                         manifest.LOCK_PLAN_OUTSIDE_ENVELOPE)

    def test_a_plan_allocating_an_undeclared_stratum_refuses(self):
        envelope = _envelope()
        chains = sorted(envelope.admissible_chain_hashes())
        allocations = tuple(
            VisitAllocation(
                position=v.position, chain_hash=chains[v.position % len(chains)],
                stratum="A_STRATUM_NOBODY_DECLARED", block="b0",
                condition="NOMINAL",
                confidence_requirement=CONFIDENCE_MIXING_TERMINATION_AND_SECOND_TIME)
            for v in generate_visit_schedule(seed=20260915))
        plan = declare_capture_plan(
            seed=20260915, envelope=envelope, allocations=allocations,
            reference_hz=28_800_000.0, reference_ppm=1.0)
        with self.assertRaises(manifest.LockRefused) as caught:
            freeze_promotion_corpus(
                corpus_id="c", method_revision="m", decision_threshold=1.0,
                preprocessing_revision="p", envelope=envelope, capture_plan=plan)
        self.assertEqual(caught.exception.code,
                         manifest.LOCK_PLAN_STRATUM_UNKNOWN)

    def test_the_lock_records_both_digests_from_the_declarations(self):
        envelope = _envelope()
        plan = _plan(envelope)
        lock = freeze_promotion_corpus(
            corpus_id="c", method_revision="m", decision_threshold=1.0,
            preprocessing_revision="p", envelope=envelope, capture_plan=plan)
        self.assertEqual(lock.envelope_digest, envelope.digest())
        self.assertEqual(lock.capture_plan_digest, plan.digest())

    def test_the_lock_dictionary_carries_the_scope_limits(self):
        """§5.22: the scope limits travel with the claim rather than being left
        to be inferred by whoever reads the lock next."""
        data = _lock(envelope=_envelope(
            authority=RECEIVER_INSTANCE_UNIQUENESS_UNATTESTED)).to_dict()
        self.assertEqual(data["receiver_identity_authority"],
                         RECEIVER_INSTANCE_UNIQUENESS_UNATTESTED)
        self.assertFalse(data["may_be_promoted_from"])


class ReceiptCarriesTheInstrumentTests(unittest.TestCase):

    def test_the_receipt_carries_both_digests_and_the_authority(self):
        envelope = _envelope(authority=RECEIVER_ATTESTED_UNIQUE)
        lock = _lock(envelope=envelope)
        receipt = issue_completion_receipt(lock=lock, counted=_full_counts(),
                                           issued_at=2_000.0)
        self.assertEqual(receipt.envelope_digest, lock.envelope_digest)
        self.assertEqual(receipt.capture_plan_digest, lock.capture_plan_digest)
        self.assertEqual(receipt.receiver_identity_authority,
                         RECEIVER_ATTESTED_UNIQUE)

    def test_an_unattested_authority_reaches_the_receipt_unchanged(self):
        """Carried forward, never upgraded. No downstream step may present an
        unattested identity as an attested one."""
        lock = _lock(envelope=_envelope(
            authority=RECEIVER_INSTANCE_UNIQUENESS_UNATTESTED))
        receipt = issue_completion_receipt(lock=lock, counted=_full_counts(),
                                           issued_at=2_000.0)
        self.assertEqual(receipt.receiver_identity_authority,
                         RECEIVER_INSTANCE_UNIQUENESS_UNATTESTED)

    def test_a_forged_envelope_digest_never_reaches_a_receipt(self):
        forged = replace(_lock(), envelope_digest="blake2s:0")
        with self.assertRaises(CompletionRefused) as caught:
            issue_completion_receipt(lock=forged, counted=_full_counts())
        self.assertEqual(caught.exception.code,
                         COMPLETION_LOCK_DECLARATION_MOVED)

    def test_a_forged_capture_plan_digest_never_reaches_a_receipt(self):
        forged = replace(_lock(), capture_plan_digest="blake2s:0")
        with self.assertRaises(CompletionRefused) as caught:
            issue_completion_receipt(lock=forged, counted=_full_counts())
        self.assertEqual(caught.exception.code,
                         COMPLETION_LOCK_DECLARATION_MOVED)
