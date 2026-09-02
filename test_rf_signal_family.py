"""Phase 0 contract tests: what the admission gate refuses, and why."""

import unittest

from rf_signal_family import (
    ANALOGUE_DETECTOR, CLAIMABLE_FAMILIES, DIGITAL_EVIDENCE_REQUIRED, NULL_REASON_CODES,
    REASON_CODES, RESERVED_FAMILIES, classifier_status, empty_reason_counts,
    normalize_classification,
)


QUALIFIED = {
    "family": "DIGITAL",
    "authority": "DERIVED_INFERENCE",
    "method": "squared-envelope-cyclic.v1",
    "confidence": 0.87,
    "symbol_rate_hz": 9600.0,
    "detection_statistic": 12.6,
    "window_start": 1000.0,
    "window_end": 1000.256,
}


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
            verdict = normalize_classification({**QUALIFIED, "family": family})
            self.assertEqual(verdict.family, "UNCLASSIFIED")
            self.assertEqual(verdict.reason_code, "ANALOGUE_DETECTOR_NOT_IMPLEMENTED")
        self.assertIn("P25 C4FM", REASON_CODES["ANALOGUE_DETECTOR_NOT_IMPLEMENTED"])

    def test_digital_requires_a_symbol_clock_not_spectral_shape(self):
        without_rate = {k: v for k, v in QUALIFIED.items() if k != "symbol_rate_hz"}
        verdict = normalize_classification({**without_rate, "method": "spectral-flatness"})
        self.assertEqual(verdict.reason_code, "UNQUALIFIED_CLAIM")
        self.assertTrue(any("SYMBOL CLOCK" in reason for reason in verdict.refusals))
        for bad_rate in (0.0, -1.0, float("nan"), "fast", None):
            self.assertEqual(
                normalize_classification({**QUALIFIED, "symbol_rate_hz": bad_rate}).family,
                "UNCLASSIFIED")

    def test_digital_requires_a_thresholdable_significance_number(self):
        without_statistic = {k: v for k, v in QUALIFIED.items() if k != "detection_statistic"}
        verdict = normalize_classification(without_statistic)
        self.assertEqual(verdict.reason_code, "UNQUALIFIED_CLAIM")
        self.assertTrue(any("DETECTION STATISTIC" in reason for reason in verdict.refusals))

    def test_a_verdict_must_cover_an_interval_and_the_detection_must_fall_inside_it(self):
        no_window = {k: v for k, v in QUALIFIED.items()
                     if k not in {"window_start", "window_end"}}
        self.assertTrue(any("VERDICT WINDOW" in reason
                            for reason in normalize_classification(no_window).refusals))
        self.assertEqual(
            normalize_classification({**QUALIFIED, "window_end": 1000.0}).reason_code,
            "UNQUALIFIED_CLAIM", "a zero-length window is not an interval")
        stale = normalize_classification(QUALIFIED, observed_at=1200.0)
        self.assertEqual(stale.reason_code, "STALE_WINDOW")
        self.assertEqual(normalize_classification(QUALIFIED, observed_at=1000.1).family, "DIGITAL")

    def test_authority_confidence_and_method_are_all_required(self):
        cases = {
            "authority": ("OBSERVED", "AUTHORITY MUST BE"),
            "method": ("", "METHOD IS REQUIRED"),
            "confidence": (1.4, "CONFIDENCE MUST BE"),
        }
        for field, (value, fragment) in cases.items():
            verdict = normalize_classification({**QUALIFIED, field: value})
            self.assertEqual(verdict.family, "UNCLASSIFIED", field)
            self.assertTrue(any(fragment in reason for reason in verdict.refusals), field)

    def test_a_fully_evidenced_digital_claim_is_admitted_and_rounded(self):
        verdict = normalize_classification({**QUALIFIED, "confidence": 0.876543210})
        self.assertEqual(verdict.family, "DIGITAL")
        self.assertEqual(verdict.reason_code, "SYMBOL_CLOCK_DETECTED")
        self.assertEqual(verdict.confidence, 0.8765)
        self.assertEqual(verdict.symbol_rate_hz, 9600.0)
        self.assertTrue(verdict.classified)
        self.assertEqual(verdict.refusals, ())
        payload = verdict.to_dict()
        self.assertEqual(payload["reason"], REASON_CODES["SYMBOL_CLOCK_DETECTED"])
        self.assertNotIn("bins_dbfs", payload)

    def test_the_reason_code_for_digital_is_not_the_submitters_to_choose(self):
        verdict = normalize_classification({**QUALIFIED, "reason_code": "NOISE_COMPATIBLE"})
        self.assertEqual(verdict.family, "DIGITAL")
        self.assertEqual(verdict.reason_code, "SYMBOL_CLOCK_DETECTED")

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
        for withheld in ("analogue_family", "constant_envelope_digital", "emitter_identity"):
            self.assertIn(withheld, status["claims_withheld"])

    def test_every_reason_code_is_documented_and_counted(self):
        counts = empty_reason_counts()
        self.assertEqual(set(counts), set(REASON_CODES))
        self.assertEqual(set(NULL_REASON_CODES) | {"SYMBOL_CLOCK_DETECTED"}, set(REASON_CODES))
        for code, description in REASON_CODES.items():
            self.assertTrue(description.strip(), code)
            self.assertEqual(description, description.upper(), code)


if __name__ == "__main__":
    unittest.main()
