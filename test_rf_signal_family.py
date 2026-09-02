"""Phase 0 contract tests: what the admission gate refuses, and why."""

from dataclasses import replace
import unittest

from rf_signal_family import (
    ANALOGUE_DETECTOR, CLAIMABLE_FAMILIES, DIGITAL_EVIDENCE_REQUIRED, METHOD_REGISTRY,
    NULL_REASON_CODES, POSITIVE_REASON_CODE, REASON_CODES, RESERVED_FAMILIES,
    classifier_status, empty_reason_counts, normalize_classification, validated_methods,
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
    "family": "DIGITAL",
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


class SignalFamilyContractTests(unittest.TestCase):

    def test_absent_claim_is_not_attempted_rather_than_negative(self):
        verdict = normalize_classification(None)
        self.assertEqual(verdict.family, "UNCLASSIFIED")
        self.assertEqual(verdict.reason_code, "NOT_ATTEMPTED")
        self.assertFalse(verdict.classified)
        self.assertEqual(verdict.refusals, ())

    def test_analogue_is_reserved_and_unreachable(self):
        self.assertEqual(RESERVED_FAMILIES, ("ANALOGUE",))
        self.assertNotIn("ANALOGUE", CLAIMABLE_FAMILIES)
        for family in ("ANALOGUE", "analog", "Analog"):
            verdict = admit(family=family)
            self.assertEqual(verdict.family, "UNCLASSIFIED")
            self.assertEqual(verdict.reason_code, "ANALOGUE_DETECTOR_NOT_IMPLEMENTED")
        self.assertIn("P25 C4FM", REASON_CODES["ANALOGUE_DETECTOR_NOT_IMPLEMENTED"])

    def test_digital_requires_a_symbol_clock_not_spectral_shape(self):
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

    def test_a_registered_but_unvalidated_method_cannot_claim_digital(self):
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

    def test_a_family_claim_is_bridge_local_and_not_ingestible(self):
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

    def test_a_fully_evidenced_digital_claim_is_admitted_and_records_support(self):
        verdict = admit(confidence=0.876543210)
        self.assertEqual(verdict.family, "DIGITAL")
        self.assertEqual(verdict.reason_code, POSITIVE_REASON_CODE)
        self.assertEqual(verdict.confidence, 0.8765)
        self.assertEqual(verdict.detection_statistic, 12.6)
        self.assertEqual(verdict.decision_threshold, 8.4)
        self.assertTrue(verdict.classified)
        self.assertEqual(verdict.refusals, ())
        # The wording is support, not proof: a cyclic feature is strong positive
        # evidence for digital structure, never metaphysical certainty.
        self.assertIn("SUPPORTED, NOT PROVEN", REASON_CODES[POSITIVE_REASON_CODE])
        self.assertIn("LIKE", POSITIVE_REASON_CODE)

    def test_the_reason_code_for_digital_is_not_the_submitters_to_choose(self):
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

    def test_unknown_families_and_malformed_claims_are_refused_not_ignored(self):
        for claim in ({"family": "ALIEN", "authority": "DERIVED_INFERENCE"}, "DIGITAL", 7, []):
            verdict = normalize_classification(claim)
            self.assertEqual(verdict.family, "UNCLASSIFIED")
            self.assertEqual(verdict.reason_code, "UNQUALIFIED_CLAIM")
            self.assertTrue(verdict.refusals)

    def test_status_declares_its_absences_rather_than_omitting_them(self):
        status = classifier_status()
        self.assertEqual(status["state"], "NOT_IMPLEMENTED")
        self.assertEqual(status["analogue_detector"], ANALOGUE_DETECTOR)
        self.assertEqual(status["contract_phase"], "0")
        self.assertEqual(status["digital_evidence_required"], list(DIGITAL_EVIDENCE_REQUIRED))
        self.assertFalse(status["raw_iq_exposed"])
        self.assertEqual(status["iq_retention"], "NONE_BEYOND_ONE_FFT_BLOCK")
        self.assertEqual(status["validated_methods"], [])
        self.assertFalse(status["digital_reachable"])
        self.assertIn("PHASE 3", status["digital_reachable_note"])
        registered = {entry["method_id"] for entry in status["registered_methods"]}
        self.assertIn("squared-envelope-cyclic.v1", registered)
        for entry in status["registered_methods"]:
            self.assertEqual(entry["validation_status"], "REGISTERED_NOT_VALIDATED")
        for withheld in ("analogue_family", "constant_envelope_digital", "emitter_identity"):
            self.assertIn(withheld, status["claims_withheld"])

    def test_a_validated_registry_makes_digital_reachable_and_says_so(self):
        status = classifier_status(TEST_REGISTRY)
        self.assertEqual(status["validated_methods"], [VALIDATED.method_id])
        self.assertTrue(status["digital_reachable"])
        self.assertIsNone(status["digital_reachable_note"])

    def test_every_reason_code_is_documented_and_counted(self):
        counts = empty_reason_counts()
        self.assertEqual(set(counts), set(REASON_CODES))
        self.assertEqual(set(NULL_REASON_CODES) | {POSITIVE_REASON_CODE}, set(REASON_CODES))
        for code, description in REASON_CODES.items():
            self.assertTrue(description.strip(), code)
            self.assertEqual(description, description.upper(), code)


if __name__ == "__main__":
    unittest.main()
