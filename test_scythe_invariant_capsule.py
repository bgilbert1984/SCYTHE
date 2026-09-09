"""What a model may be told, and what it may say back."""

import inspect
import json
import unittest

import scythe_invariant_capsule as capsule_module
from scythe_invariant_capsule import (
    ALLOWED_RESPONSE_FIELDS, COORDINATE_MANUFACTURED,
    INDETERMINATE_CONVERTED_TO_FAILURE, MAX_COORDINATE_TEXT, MAX_ITEMS, MAX_TEXT,
    MODEL_PERMITTED, MODEL_PROHIBITIONS, RESPONSE_UNBOUNDED,
    UNKNOWN_RESPONSE_FIELD, VERDICT_OVERRIDE_ATTEMPTED, capsule_status,
    evidence_capsule, indeterminate_dimensions, review_model_response,
)
from scythe_invariant_ledger import (
    Coordinate, TransitionContract, check_transition, signature,
)

CONTRACT = TransitionContract(
    name="RESTART_CAPTURE",
    must_preserve=("incident_id", "capture_unit"),
    must_change=("capture_pid", "capture_process_start_ticks"),
    domain_fields=("kernel_boot_id",),
)


def _pair(**after_overrides):
    base = dict(kernel_boot_id="boot-a", capture_pid=1000,
                capture_process_start_ticks=5000, incident_id="incident-A",
                capture_unit="scythe-rtl-tcp.service")
    before = signature(**base)
    after = signature(**{**base, **after_overrides})
    return before, after


def _capsule(**after_overrides):
    before, after = _pair(**after_overrides)
    verdict = check_transition(before, "RESTART_CAPTURE", after, CONTRACT)
    return evidence_capsule(before, after, CONTRACT, verdict)


class CapsuleShapeTests(unittest.TestCase):

    def test_the_capsule_carries_the_declared_shape(self):
        payload = _capsule().as_dict()
        for key in ("entity_signature_before", "declared_transition",
                    "entity_signature_after", "invariants_checked",
                    "violations", "indeterminate_dimensions", "authority"):
            self.assertIn(key, payload)

    def test_a_clean_transition_carries_no_violations(self):
        payload = _capsule(capture_pid=2000,
                           capture_process_start_ticks=9000).as_dict()
        self.assertEqual(payload["verdict"], "INVARIANTS_SATISFIED")
        self.assertEqual(payload["violations"], [])

    def test_violations_name_the_coordinate_and_what_was_expected(self):
        payload = _capsule().as_dict()
        fields = {v["field"] for v in payload["violations"]}
        self.assertEqual(fields, {"capture_pid", "capture_process_start_ticks"})
        for violation in payload["violations"]:
            self.assertEqual(violation["expected"], "CHANGED")
            self.assertEqual(violation["observed"], "UNCHANGED")

    def test_the_authority_block_says_the_model_only_proposes(self):
        authority = _capsule().as_dict()["authority"]
        self.assertEqual(authority["model_role"], "PROPOSES_ONLY")
        self.assertEqual(authority["promotion"], "NOT_IMPLEMENTED")
        self.assertFalse(authority["mutates"])
        self.assertEqual(set(authority["model_may_not"]), set(MODEL_PROHIBITIONS))


class IndeterminateTests(unittest.TestCase):
    """Nobody looked is not something is wrong, and is handed over separated."""

    def _with_unassessed(self):
        before, after = _pair(capture_pid=2000, capture_process_start_ticks=9000)
        after = dict(after)
        after["iq_endpoint"] = Coordinate("NOT_ASSESSED")
        verdict = check_transition(before, "RESTART_CAPTURE", after, CONTRACT)
        return before, after, evidence_capsule(before, after, CONTRACT, verdict)

    def test_unassessed_coordinates_are_listed_apart_from_violations(self):
        _b, _a, capsule = self._with_unassessed()
        payload = capsule.as_dict()
        self.assertEqual(payload["indeterminate_dimensions"], ["iq_endpoint"])
        self.assertNotIn("iq_endpoint", {v["field"] for v in payload["violations"]})

    def test_the_capsule_says_what_indeterminate_means(self):
        payload = _capsule().as_dict()
        self.assertIn("NOT A FAILURE", payload["indeterminate_note"])

    def test_the_helper_finds_them_on_either_side(self):
        before = signature(a=1)
        before = dict(before)
        before["b"] = Coordinate("NOT_ASSESSED")
        after = signature(a=1, b=2)
        self.assertEqual(indeterminate_dimensions(before, after), ("b",))


class BoundsTests(unittest.TestCase):
    """A capsule is a summary, not a transport."""

    def test_a_long_coordinate_value_is_truncated(self):
        before, after = _pair(incident_id="x" * 5000)
        verdict = check_transition(before, "RESTART_CAPTURE", after, CONTRACT)
        payload = evidence_capsule(before, after, CONTRACT, verdict).as_dict()
        value = payload["entity_signature_after"]["incident_id"]["value"]
        self.assertLessEqual(len(value), MAX_COORDINATE_TEXT)

    def test_a_capsule_declares_it_carries_no_samples(self):
        payload = _capsule().as_dict()
        self.assertFalse(payload["contains_samples"])
        self.assertFalse(capsule_status()["carries_samples"])
        self.assertIn("RAW IQ NEVER ENTERS ONE", capsule_status()["sample_note"])

    def test_a_capsule_is_json_serialisable(self):
        json.dumps(_capsule().as_dict())
        json.dumps(capsule_status())

    def test_a_non_value_coordinate_carries_no_value_field(self):
        _b, _a, capsule = IndeterminateTests()._with_unassessed()
        entry = capsule.as_dict()["entity_signature_after"]["iq_endpoint"]
        self.assertEqual(entry, {"kind": "NOT_ASSESSED"})


class ResponseGuardTests(unittest.TestCase):
    """The model proposes; this decides whether the proposal is admissible."""

    def setUp(self):
        self.capsule = _capsule()

    def _review(self, response):
        return review_model_response(self.capsule, response)

    def test_an_explanation_is_admissible(self):
        review = self._review({"explanation": "neither identity moved"})
        self.assertTrue(review.admissible)
        self.assertEqual(review.refusals, ())

    def test_suggesting_an_observation_is_admissible(self):
        review = self._review(
            {"suggested_observations": ["read /proc for the current pid"]})
        self.assertTrue(review.admissible)

    def test_disagreeing_with_a_real_finding_is_commentary(self):
        """Disagreement is permitted. It changes nothing, which is why."""
        review = self._review({"disputed_findings": ["capture_pid"]})
        self.assertTrue(review.admissible)

    def test_asserting_a_verdict_is_refused(self):
        for field in ("verdict", "verdict_override", "final_verdict"):
            with self.subTest(field=field):
                review = self._review({field: "INVARIANTS_SATISFIED"})
                self.assertFalse(review.admissible)
                self.assertIn(VERDICT_OVERRIDE_ATTEMPTED, review.refusals)

    def test_supplying_a_coordinate_is_refused(self):
        for field in ("coordinates", "entity_signature_after", "supplied_values"):
            with self.subTest(field=field):
                review = self._review({field: {"capture_pid": 2000}})
                self.assertFalse(review.admissible)
                self.assertIn(COORDINATE_MANUFACTURED, review.refusals)

    def test_calling_an_unassessed_dimension_a_failure_is_refused(self):
        _b, _a, capsule = IndeterminateTests()._with_unassessed()
        review = review_model_response(capsule, {"disputed_findings": ["iq_endpoint"]})
        self.assertFalse(review.admissible)
        self.assertIn(INDETERMINATE_CONVERTED_TO_FAILURE, review.refusals)

    def test_an_unknown_field_is_refused(self):
        review = self._review({"confidence": 0.97})
        self.assertFalse(review.admissible)
        self.assertIn(UNKNOWN_RESPONSE_FIELD, review.refusals)

    def test_an_unbounded_response_is_refused(self):
        self.assertIn(RESPONSE_UNBOUNDED,
                      self._review({"explanation": "x" * (MAX_TEXT + 1)}).refusals)
        self.assertIn(RESPONSE_UNBOUNDED,
                      self._review({"suggested_observations":
                                    ["x"] * (MAX_ITEMS + 1)}).refusals)

    def test_a_non_mapping_response_is_refused(self):
        for bad in ("just prose", ["a", "b"], 7, None):
            self.assertFalse(review_model_response(self.capsule, bad).admissible)

    def test_a_refusal_changes_nothing_about_the_verdict(self):
        payload = self._review({"verdict": "INVARIANTS_SATISFIED"}).as_dict()
        self.assertIn("CHANGES NOTHING ABOUT THE VERDICT", payload["note"])
        self.assertEqual(self.capsule.as_dict()["verdict"],
                         "REQUIRED_CHANGE_NOT_OBSERVED")

    def test_refusals_are_deduplicated_and_carry_reasons(self):
        review = self._review({"verdict": "X", "verdict_override": "Y"})
        self.assertEqual(len(review.refusals), len(set(review.refusals)))
        payload = review.as_dict()
        self.assertEqual(len(payload["reasons"]), len(payload["refusals"]))

    def test_every_prohibition_has_a_refusal_that_catches_it(self):
        catchers = {
            "OVERRIDE_A_VERDICT": {"verdict": "X"},
            "MANUFACTURE_A_MISSING_COORDINATE": {"coordinates": {}},
            "CONVERT_AN_INDETERMINATE_DIMENSION_INTO_A_FAILURE": None,
            "WAIVE_AN_INVARIANT": {"verdict_override": "INVARIANTS_SATISFIED"},
        }
        for prohibition in MODEL_PROHIBITIONS:
            with self.subTest(prohibition=prohibition):
                probe = catchers[prohibition]
                if probe is None:
                    _b, _a, capsule = IndeterminateTests()._with_unassessed()
                    review = review_model_response(
                        capsule, {"disputed_findings": ["iq_endpoint"]})
                else:
                    review = self._review(probe)
                self.assertFalse(review.admissible, prohibition)


class ScopeTests(unittest.TestCase):
    def test_the_module_promotes_nothing_and_calls_no_bus(self):
        import ast
        with open(inspect.getsourcefile(capsule_module), encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                target = node.func
                if isinstance(target, ast.Name):
                    called.add(target.id)
                elif isinstance(target, ast.Attribute):
                    called.add(target.attr)
        for forbidden in ("commit", "post", "send", "publish", "promote",
                          "ingest", "generate", "chat", "open"):
            self.assertNotIn(forbidden, called, forbidden)
        self.assertEqual(capsule_status()["graph_promotion"], "NOT_IMPLEMENTED")
        self.assertFalse(capsule_status()["mutates"])

    def test_the_boundary_publishes_its_own_terms(self):
        status = capsule_status()
        self.assertEqual(set(status["model_may"]), set(MODEL_PERMITTED))
        self.assertEqual(set(status["model_may_not"]), set(MODEL_PROHIBITIONS))
        self.assertEqual(set(status["response_fields"]), set(ALLOWED_RESPONSE_FIELDS))

    def test_review_is_pure_across_repeated_calls(self):
        capsule = _capsule()
        response = {"explanation": "steady"}
        first = review_model_response(capsule, response).as_dict()
        for _ in range(5):
            self.assertEqual(review_model_response(capsule, response).as_dict(),
                             first)


if __name__ == "__main__":
    unittest.main()
