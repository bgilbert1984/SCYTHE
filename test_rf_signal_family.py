"""Phase 0 contract tests: what the admission gate refuses, and why."""

from dataclasses import replace
import unittest

from rf_signal_family import (
    ANALOGUE_DETECTOR, AXES, AXIS_EVIDENCE_REQUIRED, AXIS_VOCABULARY,
    CLAIMABLE_INFORMATION_STRUCTURE, GATED_INFORMATION_STRUCTURE,
    INFORMATION_STRUCTURE_VALUES, METHOD_REGISTRY, MODULATION_VALUES,
    NULL_REASON_CODES, POSITIVE_REASON_CODE, PROTOCOL_VALUES, REASON_CODES,
    classifier_status, derive_family, empty_axis_counts, empty_reason_counts,
    normalize_classification, validated_methods,
)


BASE_METHOD = METHOD_REGISTRY["squared-envelope-cyclic.v1"]

# A method that has passed Phase 3. No such entry ships; the accept path is only
# reachable through an injected registry, which is the point of the gate.
VALIDATED = replace(
    BASE_METHOD,
    method_revision="sha256:" + "a" * 64,
    validation_status="VALIDATED",
    validation_note="VALIDATED BY THE TEST CORPUS",
    calibration_revision="rf-family-calibration-test",
)
TEST_REGISTRY = {VALIDATED.method_id: VALIDATED}

QUALIFIED = {
    "information_structure": "SYMBOL_CLOCK_LIKE_FEATURE",
    "authority": "DERIVED_INFERENCE",
    "method": VALIDATED.method_id,
    "method_revision": VALIDATED.method_revision,
    "confidence": 0.87,
    "symbol_rate_hz": 9600.0,
    "detection_statistic": 12.6,
    "decision_threshold": 8.4,
    "statistic_direction": "GREATER_IS_STRONGER",
    "estimated_false_alarm_probability": 0.0004,
    "null_model": "CHANNELIZED_NOISE_PLUS_NONCYCLIC_SIGNAL",
    "sample_count": 524_288,
    "source_window_hash": "sha256:" + "b" * 64,
    "calibration_revision": "rf-family-calibration-test",
    "window_start": 1000.0,
    "window_end": 1000.256,
}


def admit(**overrides):
    return normalize_classification({**QUALIFIED, **overrides}, registry=TEST_REGISTRY)


class AxisSeparationTests(unittest.TestCase):
    """The three axes are independent, and two of them have no detector."""

    def test_the_axes_are_separate_vocabularies_with_declared_defaults(self):
        self.assertEqual(AXES, ("modulation", "information_structure", "protocol"))
        self.assertEqual(set(AXIS_VOCABULARY), set(AXES))
        self.assertEqual(MODULATION_VALUES[0], "UNRESOLVED")
        self.assertEqual(INFORMATION_STRUCTURE_VALUES[0], "NOT_ATTEMPTED")
        self.assertEqual(PROTOCOL_VALUES[0], "UNRESOLVED")
        # Every declared value is counted, so a zero and an absent key differ.
        counts = empty_axis_counts()
        self.assertEqual(set(counts), set(AXES))
        for axis, values in AXIS_VOCABULARY.items():
            self.assertEqual(set(counts[axis]), set(values))
            self.assertEqual(sum(counts[axis].values()), 0)

    def test_a_family_may_not_be_submitted_as_an_observation(self):
        verdict = admit(family="DIGITAL")
        self.assertEqual(verdict.reason_code, "FAMILY_NOT_DIRECTLY_CLAIMABLE")
        self.assertEqual(verdict.family, "UNCLASSIFIED")
        self.assertTrue(any("COMPATIBILITY SUMMARIES" in r for r in verdict.refusals))
        self.assertIn("DERIVED SUMMARIES", REASON_CODES["FAMILY_NOT_DIRECTLY_CLAIMABLE"])

    def test_analogue_is_refused_with_the_reason_that_actually_applies(self):
        """"No detector" is more useful than "summaries are not claimable"."""
        for family in ("ANALOGUE", "analog", "Analog"):
            verdict = admit(family=family)
            self.assertEqual(verdict.family, "UNCLASSIFIED", family)
            self.assertEqual(verdict.reason_code, "ANALOGUE_DETECTOR_NOT_IMPLEMENTED")
        self.assertIn("P25 C4FM", REASON_CODES["ANALOGUE_DETECTOR_NOT_IMPLEMENTED"])

    def test_the_modulation_axis_has_no_detector_and_says_so(self):
        for value in ("AM_LIKE", "FM_LIKE", "FSK_LIKE", "PSK_LIKE", "QAM_LIKE"):
            verdict = normalize_classification({"modulation": value}, registry=TEST_REGISTRY)
            self.assertEqual(verdict.reason_code, "MODULATION_DETECTOR_NOT_IMPLEMENTED", value)
            self.assertEqual(verdict.modulation, "UNRESOLVED", value)
        unknown = normalize_classification({"modulation": "CHIRP_LIKE"})
        self.assertEqual(unknown.reason_code, "UNQUALIFIED_CLAIM")
        self.assertTrue(any("NOT IN THE VOCABULARY" in r for r in unknown.refusals))

    def test_a_protocol_needs_a_hypothesis_source_and_confirmation_needs_a_decoder(self):
        candidate = normalize_classification({"protocol": "CANDIDATE"})
        self.assertEqual(candidate.reason_code, "PROTOCOL_HYPOTHESIS_NOT_IMPLEMENTED")
        confirmed = normalize_classification({"protocol": "CONFIRMED_BY_DECODER"})
        self.assertEqual(confirmed.reason_code, "DECODER_NOT_IMPLEMENTED")
        self.assertEqual(confirmed.protocol, "UNRESOLVED")
        self.assertIn("DECODER EVIDENCE", REASON_CODES["DECODER_NOT_IMPLEMENTED"])
        unknown = normalize_classification({"protocol": "P25"})
        self.assertEqual(unknown.reason_code, "UNQUALIFIED_CLAIM")

    def test_a_capability_refusal_outranks_an_evidence_refusal(self):
        """A perfect modulation claim is still refused, and for the true reason."""
        verdict = admit(modulation="PSK_LIKE")
        self.assertEqual(verdict.reason_code, "MODULATION_DETECTOR_NOT_IMPLEMENTED")

    def test_a_symbol_clock_does_not_leak_into_the_other_two_axes(self):
        verdict = admit()
        self.assertEqual(verdict.information_structure, "SYMBOL_CLOCK_LIKE_FEATURE")
        self.assertEqual(verdict.modulation, "UNRESOLVED")
        self.assertEqual(verdict.protocol, "UNRESOLVED")

    def test_a_method_may_only_speak_for_the_axis_it_is_registered_against(self):
        wrong = {VALIDATED.method_id: replace(VALIDATED, axis="modulation")}
        verdict = normalize_classification(QUALIFIED, registry=wrong)
        self.assertEqual(verdict.reason_code, "METHOD_WRONG_AXIS")
        self.assertIn("information_structure", verdict.refusals[0])


class FamilySummaryTests(unittest.TestCase):
    """DIGITAL and ANALOGUE are derived, and ANALOGUE is not derivable."""

    def test_only_a_symbol_clock_summarises_to_digital(self):
        self.assertEqual(derive_family("UNRESOLVED", "SYMBOL_CLOCK_LIKE_FEATURE"), "DIGITAL")
        for structure in ("NOT_ATTEMPTED", "NO_SYMBOL_CLOCK_DETECTED"):
            self.assertEqual(derive_family("UNRESOLVED", structure), "UNCLASSIFIED")

    def test_no_axis_combination_whatsoever_summarises_to_analogue(self):
        """The P25 trap: FM_LIKE with no symbol clock is not evidence of analogue."""
        for modulation in MODULATION_VALUES:
            for structure in INFORMATION_STRUCTURE_VALUES:
                self.assertNotEqual(derive_family(modulation, structure), "ANALOGUE",
                                    f"{modulation}/{structure}")
        self.assertEqual(derive_family("FM_LIKE", "NO_SYMBOL_CLOCK_DETECTED"), "UNCLASSIFIED")
        self.assertFalse(classifier_status()["family_summary"]["analogue_derivable"])

    def test_a_blind_spot_is_not_recorded_as_a_negative_result(self):
        """CONSTANT_ENVELOPE means the test could not see, not that nothing was there."""
        blind = normalize_classification({"reason_code": "CONSTANT_ENVELOPE"})
        self.assertEqual(blind.reason_code, "CONSTANT_ENVELOPE")
        self.assertEqual(blind.information_structure, "NOT_ATTEMPTED")
        negative = normalize_classification({"reason_code": "NO_SYMBOL_CLOCK_DETECTED"})
        self.assertEqual(negative.information_structure, "NO_SYMBOL_CLOCK_DETECTED")


class AdmissionGateTests(unittest.TestCase):

    def test_absent_claim_is_not_attempted_rather_than_negative(self):
        verdict = normalize_classification(None)
        self.assertEqual(verdict.family, "UNCLASSIFIED")
        self.assertEqual(verdict.reason_code, "NOT_ATTEMPTED")
        self.assertEqual(verdict.axes(), {"modulation": "UNRESOLVED",
                                          "information_structure": "NOT_ATTEMPTED",
                                          "protocol": "UNRESOLVED"})
        self.assertFalse(verdict.classified)
        self.assertEqual(verdict.refusals, ())

    def test_a_negative_result_carries_no_evidence_burden(self):
        """Claiming no structure asserts nothing, so it needs no registered method."""
        self.assertEqual(CLAIMABLE_INFORMATION_STRUCTURE,
                         ("NO_SYMBOL_CLOCK_DETECTED", "SYMBOL_CLOCK_LIKE_FEATURE"))
        self.assertEqual(GATED_INFORMATION_STRUCTURE, ("SYMBOL_CLOCK_LIKE_FEATURE",))
        verdict = normalize_classification(
            {"information_structure": "NO_SYMBOL_CLOCK_DETECTED"})
        self.assertEqual(verdict.information_structure, "NO_SYMBOL_CLOCK_DETECTED")
        self.assertEqual(verdict.reason_code, "NO_SYMBOL_CLOCK_DETECTED")
        self.assertEqual(verdict.family, "UNCLASSIFIED")

    def test_a_symbol_clock_requires_a_rate_not_spectral_shape(self):
        verdict = normalize_classification(
            {**{k: v for k, v in QUALIFIED.items() if k != "symbol_rate_hz"},
             "method": "spectral-flatness"}, registry=TEST_REGISTRY)
        self.assertEqual(verdict.reason_code, "UNQUALIFIED_CLAIM")
        self.assertTrue(any("SYMBOL CLOCK" in reason for reason in verdict.refusals))
        for bad_rate in (0.0, -1.0, float("nan"), "fast", None):
            self.assertEqual(admit(symbol_rate_hz=bad_rate).family, "UNCLASSIFIED")

    def test_a_verdict_must_cover_an_interval_and_the_detection_must_fall_inside_it(self):
        no_window = {k: v for k, v in QUALIFIED.items()
                     if k not in {"window_start", "window_end"}}
        self.assertTrue(any("VERDICT WINDOW" in reason for reason in
                            normalize_classification(no_window, registry=TEST_REGISTRY).refusals))
        self.assertEqual(admit(window_end=1000.0).reason_code, "UNQUALIFIED_CLAIM",
                         "a zero-length window is not an interval")
        stale = normalize_classification(QUALIFIED, observed_at=1200.0, registry=TEST_REGISTRY)
        self.assertEqual(stale.reason_code, "STALE_WINDOW")
        self.assertEqual(
            normalize_classification(QUALIFIED, observed_at=1000.1,
                                     registry=TEST_REGISTRY).family, "DIGITAL")

    def test_authority_confidence_and_method_are_all_required(self):
        cases = {
            "authority": ("OBSERVED", "AUTHORITY MUST BE"),
            "method": ("", "METHOD IS REQUIRED"),
            "confidence": (1.4, "CONFIDENCE MUST BE"),
        }
        for name, (value, fragment) in cases.items():
            verdict = admit(**{name: value})
            self.assertEqual(verdict.family, "UNCLASSIFIED", name)
            self.assertTrue(any(fragment in reason for reason in verdict.refusals), name)

    # --- the decision rule, not merely the shape of the evidence -------------

    def test_an_arbitrary_method_string_cannot_cross_the_gate(self):
        verdict = admit(method="anything.v1")
        self.assertEqual(verdict.reason_code, "METHOD_NOT_REGISTERED")
        self.assertIn("METHOD anything.v1 IS NOT REGISTERED", verdict.refusals)

    def test_a_registered_but_unvalidated_method_cannot_claim_a_symbol_clock(self):
        """This is the shipped state: nothing has passed Phase 3."""
        verdict = normalize_classification(
            {**QUALIFIED, "method_revision": BASE_METHOD.method_revision,
             "calibration_revision": ""})
        self.assertEqual(verdict.family, "UNCLASSIFIED")
        self.assertEqual(verdict.reason_code, "METHOD_NOT_VALIDATED")
        self.assertEqual(validated_methods(), [], "no shipped method may be validated")
        self.assertFalse(classifier_status()["digital_reachable"])

    def test_a_present_statistic_is_not_a_significant_one(self):
        """The bypass an earlier cut of this gate allowed."""
        verdict = admit(detection_statistic=-999)
        self.assertEqual(verdict.family, "UNCLASSIFIED")
        self.assertEqual(verdict.reason_code, "DECISION_RULE_NOT_MET")
        self.assertTrue(any("DID NOT REACH" in reason for reason in verdict.refusals))

    def test_the_submitter_may_not_lower_the_bar_or_reverse_the_test(self):
        lowered = admit(decision_threshold=0.1, detection_statistic=0.2)
        self.assertEqual(lowered.reason_code, "DECISION_RULE_NOT_MET")
        self.assertTrue(any("NOT THE SUBMITTER'S TO LOWER" in r for r in lowered.refusals))
        reversed_sense = admit(statistic_direction="LESS_IS_STRONGER", detection_statistic=0.001)
        self.assertEqual(reversed_sense.reason_code, "DECISION_RULE_NOT_MET")
        self.assertTrue(any("MAY NOT REVERSE THE SENSE" in r for r in reversed_sense.refusals))

    def test_false_alarm_null_model_and_sample_count_are_all_enforced(self):
        cases = {
            "estimated_false_alarm_probability": (0.05, "EXCEEDS THE REGISTERED MAXIMUM"),
            "null_model": ("WHITE_NOISE", "NULL MODEL MUST BE"),
            "sample_count": (4096, "SAMPLE COUNT MUST BE AT LEAST"),
            "method_revision": ("sha256:" + "c" * 64, "METHOD REVISION DOES NOT MATCH"),
            "calibration_revision": ("stale-calibration", "CALIBRATION REVISION MUST BE"),
            "source_window_hash": ("", "SOURCE WINDOW HASH IS REQUIRED"),
        }
        for name, (value, fragment) in cases.items():
            verdict = admit(**{name: value})
            self.assertEqual(verdict.reason_code, "DECISION_RULE_NOT_MET", name)
            self.assertTrue(any(fragment in reason for reason in verdict.refusals), name)

    def test_a_window_hash_must_be_a_recomputable_digest_not_a_bare_string(self):
        """A verdict must be traceable to the samples that produced it."""
        for bad in ("yes", "deadbeef", "sha256:" + "b" * 63, "sha256:" + "B" * 64,
                    "md5:" + "a" * 32, "sha256:", "  "):
            verdict = admit(source_window_hash=bad)
            self.assertEqual(verdict.reason_code, "DECISION_RULE_NOT_MET", bad)
            self.assertTrue(any("SOURCE WINDOW HASH" in r for r in verdict.refusals), bad)
        for good in ("sha256:" + "b" * 64, "sha512:" + "c" * 128, "blake2s:" + "d" * 64):
            self.assertEqual(admit(source_window_hash=good).family, "DIGITAL", good)

    def test_an_axis_claim_is_bridge_local_and_not_ingestible(self):
        """The statistic must be measured by the detector, not asserted by a caller."""
        from graphops_rf_ingest import ALLOWED_FIELDS
        status = classifier_status()
        self.assertEqual(status["classification_trust"], "BRIDGE_LOCAL_DETECTOR_ONLY")
        self.assertIn("NOT ACCEPTED OVER THE OBSERVATION INGEST API",
                      status["classification_trust_note"])
        self.assertNotIn("signal_classification", ALLOWED_FIELDS)
        self.assertEqual(status["window_hash_algorithms"],
                         ["blake2s", "sha256", "sha384", "sha512"])

    def test_an_uncalibrated_confidence_is_refused_as_decorative(self):
        uncalibrated = {VALIDATED.method_id: replace(VALIDATED, calibration_revision=None)}
        verdict = normalize_classification(QUALIFIED, registry=uncalibrated)
        self.assertEqual(verdict.reason_code, "DECISION_RULE_NOT_MET")
        self.assertTrue(any("DECORATIVE" in reason for reason in verdict.refusals))

    def test_a_fully_evidenced_claim_is_admitted_and_records_support(self):
        verdict = admit(confidence=0.876543210)
        self.assertEqual(verdict.information_structure, "SYMBOL_CLOCK_LIKE_FEATURE")
        self.assertEqual(verdict.family, "DIGITAL")
        self.assertEqual(verdict.reason_code, POSITIVE_REASON_CODE)
        self.assertEqual(verdict.confidence, 0.8765)
        self.assertEqual(verdict.detection_statistic, 12.6)
        self.assertEqual(verdict.decision_threshold, 8.4)
        self.assertTrue(verdict.classified)
        self.assertEqual(verdict.refusals, ())
        self.assertEqual(verdict.to_dict()["family_authority"], "DERIVED_SUMMARY")
        # The wording is support, not proof: a cyclic feature is strong positive
        # evidence for digital structure, never metaphysical certainty.
        self.assertIn("SUPPORTED, NOT PROVEN", REASON_CODES[POSITIVE_REASON_CODE])
        self.assertIn("LIKE", POSITIVE_REASON_CODE)

    def test_the_reason_code_for_a_positive_is_not_the_submitters_to_choose(self):
        verdict = admit(reason_code="NOISE_COMPATIBLE")
        self.assertEqual(verdict.family, "DIGITAL")
        self.assertEqual(verdict.reason_code, POSITIVE_REASON_CODE)

    def test_a_detector_that_concluded_nothing_may_say_which_nothing(self):
        for code in NULL_REASON_CODES:
            verdict = normalize_classification({"reason_code": code})
            self.assertEqual(verdict.family, "UNCLASSIFIED", code)
            self.assertEqual(verdict.reason_code, code)
        unknown = normalize_classification({"reason_code": "PROBABLY_A_DRONE"})
        self.assertEqual(unknown.reason_code, "UNQUALIFIED_CLAIM")
        self.assertIn("UNKNOWN REASON CODE PROBABLY_A_DRONE", unknown.refusals)

    def test_unknown_axis_values_and_malformed_claims_are_refused_not_ignored(self):
        for claim in ({"information_structure": "ALIEN", "authority": "DERIVED_INFERENCE"},
                      "DIGITAL", 7, []):
            verdict = normalize_classification(claim)
            self.assertEqual(verdict.family, "UNCLASSIFIED")
            self.assertEqual(verdict.reason_code, "UNQUALIFIED_CLAIM")
            self.assertTrue(verdict.refusals)


class ClassifierStatusTests(unittest.TestCase):

    def test_status_declares_its_absences_rather_than_omitting_them(self):
        status = classifier_status()
        self.assertEqual(status["state"], "NOT_IMPLEMENTED")
        self.assertEqual(status["analogue_detector"], ANALOGUE_DETECTOR)
        self.assertEqual(status["contract_phase"], "0")
        self.assertEqual(status["axis_evidence_required"], list(AXIS_EVIDENCE_REQUIRED))
        self.assertFalse(status["raw_iq_exposed"])
        self.assertEqual(status["iq_retention"], "NONE_BEYOND_ONE_FFT_BLOCK")
        self.assertEqual(status["validated_methods"], [])
        self.assertFalse(status["digital_reachable"])
        self.assertIn("PHASE 3", status["digital_reachable_note"])
        registered = {entry["method_id"] for entry in status["registered_methods"]}
        self.assertIn("squared-envelope-cyclic.v1", registered)
        for entry in status["registered_methods"]:
            self.assertEqual(entry["validation_status"], "REGISTERED_NOT_VALIDATED")
            self.assertIn(entry["axis"], AXES)
        for withheld in ("analogue_family", "constant_envelope_digital",
                         "modulation_family", "protocol_identity", "emitter_identity"):
            self.assertIn(withheld, status["claims_withheld"])

    def test_status_publishes_each_axis_with_its_own_detector_state(self):
        axes = classifier_status()["axes"]
        self.assertEqual(set(axes), set(AXES))
        self.assertEqual(axes["modulation"]["detector"], "NOT_IMPLEMENTED")
        self.assertEqual(axes["modulation"]["claimable"], [])
        self.assertEqual(axes["protocol"]["decoder"], "NOT_IMPLEMENTED")
        self.assertEqual(axes["protocol"]["hypothesis_source"], "NOT_IMPLEMENTED")
        self.assertEqual(axes["protocol"]["claimable"], [])
        self.assertEqual(axes["information_structure"]["reachable"], [])
        self.assertEqual(axes["information_structure"]["gated"],
                         list(GATED_INFORMATION_STRUCTURE))
        for axis, block in axes.items():
            self.assertEqual(block["values"], list(AXIS_VOCABULARY[axis]))
            self.assertEqual(block["default"], AXIS_VOCABULARY[axis][0])

    def test_the_family_summary_declares_itself_derived(self):
        summary = classifier_status()["family_summary"]
        self.assertEqual(summary["authority"], "DERIVED_SUMMARY")
        self.assertEqual(summary["derived_from"], ["modulation", "information_structure"])
        self.assertFalse(summary["analogue_derivable"])
        self.assertIn("NOT PRIMARY OBSERVATIONS", summary["note"])
        self.assertEqual(classifier_status()["claimable_families"], [])

    def test_a_validated_registry_makes_digital_reachable_and_says_so(self):
        status = classifier_status(TEST_REGISTRY)
        self.assertEqual(status["validated_methods"], [VALIDATED.method_id])
        self.assertTrue(status["digital_reachable"])
        self.assertIsNone(status["digital_reachable_note"])
        self.assertEqual(status["axes"]["information_structure"]["reachable"],
                         [VALIDATED.method_id])

    def test_a_validated_method_on_another_axis_does_not_unlock_digital(self):
        elsewhere = {VALIDATED.method_id: replace(VALIDATED, axis="modulation")}
        status = classifier_status(elsewhere)
        self.assertEqual(status["validated_methods"], [VALIDATED.method_id])
        self.assertFalse(status["digital_reachable"])

    def test_every_reason_code_is_documented_and_counted(self):
        counts = empty_reason_counts()
        self.assertEqual(set(counts), set(REASON_CODES))
        self.assertEqual(set(NULL_REASON_CODES) | {POSITIVE_REASON_CODE}, set(REASON_CODES))
        for code, description in REASON_CODES.items():
            self.assertTrue(description.strip(), code)
            self.assertEqual(description, description.upper(), code)


if __name__ == "__main__":
    unittest.main()
