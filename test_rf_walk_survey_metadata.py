"""Structural validation of a survey frame, and the line it must not cross."""

import inspect
import itertools
import json
import unittest

import rf_walk_survey_metadata as metadata_module
from rf_walk_survey_admission import (
    BREADCRUMB_ONLY, FRAME_REFUSED, SURFACE_ELIGIBLE, AdmissionFacts,
    AlignmentAdmissionFacts, decide,
)
from rf_walk_survey_metadata import (
    ALLOWED_CALIBRATION_FIELDS, MAX_EVIDENCE_REFS, MAX_STRING, METADATA_ASSESSED,
    PRODUCT_LINEAGE_FIELDS, RAW_IQ_FRAME, RECEIVER_STATE_FIELDS,
    SIGNAL_CHAIN_FIELDS, FrameMetadataAssessment, FrameMetadataError,
    ValidatedMetadata, assess_frame, find_sample_bearing_field, metadata_status,
)

ALIGNED = AlignmentAdmissionFacts(False, False, False, False)


def _frame(**overrides):
    frame = {
        "frame_id": "android-388bfd:1788620400123",
        "observer_id": "android-388bfdb841efb651",
        "monotonic_source_id": "pixel-boot-3bfe669f",
        "acquisition_start_monotonic_ns": 8_832_633_213_363,
        "acquisition_end_monotonic_ns": 8_832_733_213_363,
        "configuration_epoch": 7,
        "power_unit": "DBFS",
        "signal_chain_hash": "blake2s:bbfa48d493d885c4b1c02f9298e0e5f6",
        "antenna_id": "nesdr-smart-telescopic",
        "feedline_id": "nesdr-magnetic-base-rg58-2m",
        "extension_mm": 368.0,
        "gain_db": 29.7,
        "sample_rate_hz": 2_048_000.0,
        "receiver_state_chain_hash": "blake2s:537fd548e6d6ac06",
        "receiver_state_device_id": "pixel-7-pro",
        "sweep_plan_revision": "sweep-plan-v1",
        "processing_revision": "proc-v3",
    }
    for key, value in overrides.items():
        if value is None:
            frame.pop(key, None)
        else:
            frame[key] = value
    return frame


def _calibration(**overrides):
    block = {
        "calibration_id": "nesdr-14530058-fm",
        "calibration_revision": "r2",
        "frequency_range_hz": [88.0e6, 108.0e6],
        "gain_state": "29.7",
        "antenna_id": "nesdr-smart-telescopic",
        "feedline_id": "nesdr-magnetic-base-rg58-2m",
        "extension_mm": 368.0,
        "uncertainty_db": 2.5,
    }
    block.update(overrides)
    return block


class RawIQExitTests(unittest.TestCase):
    """The only exclusive short circuit, and it is taken first."""

    def test_a_clean_frame_is_assessed(self):
        assessment = assess_frame(_frame())
        self.assertEqual(assessment.outcome, METADATA_ASSESSED)
        self.assertIsNone(assessment.verdict)

    def test_a_sample_bearing_field_ends_evaluation_with_a_finished_verdict(self):
        assessment = assess_frame({**_frame(), "iq_data": "AAAA"})
        self.assertEqual(assessment.outcome, RAW_IQ_FRAME)
        self.assertEqual(assessment.verdict.disposition, FRAME_REFUSED)
        self.assertEqual(assessment.verdict.reasons, ("RAW_IQ_PRESENT",))
        self.assertIsNone(assessment.facts, "a refused frame produced no facts")
        self.assertIsNone(assessment.metadata)

    def test_aliases_and_casings_are_all_caught(self):
        for alias in ("iq", "IQ", "raw_iq", "iqData", "IQ-Data", "samples",
                      "baseband", "complex_samples", "iq_b64", "sample_data"):
            with self.subTest(alias=alias):
                assessment = assess_frame({**_frame(), alias: [1, 2, 3]})
                self.assertEqual(assessment.outcome, RAW_IQ_FRAME, alias)

    def test_a_nested_container_cannot_smuggle_samples(self):
        nested = {**_frame(), "calibration": {"iq": [1, 2]}}
        self.assertEqual(assess_frame(nested).outcome, RAW_IQ_FRAME)
        deeper = {**_frame(), "calibration": {"a": [{"b": {"raw_samples": "x"}}]}}
        self.assertEqual(assess_frame(deeper).outcome, RAW_IQ_FRAME)

    def test_raw_iq_is_checked_before_unknown_field_policy(self):
        """Reading further into a payload that already violated is the thing not to do."""
        both = {**_frame(), "iq": "x", "totally_unknown_field": 1}
        self.assertEqual(assess_frame(both).outcome, RAW_IQ_FRAME)

    def test_raw_iq_beats_every_structural_failure(self):
        stripped = {k: v for k, v in _frame().items()
                    if k not in SIGNAL_CHAIN_FIELDS + PRODUCT_LINEAGE_FIELDS}
        self.assertEqual(assess_frame({**stripped, "iq": "x"}).outcome, RAW_IQ_FRAME)

    def test_an_evidence_ref_naming_iq_is_a_name_not_a_grant(self):
        frame = _frame(evidence_refs=["iq:android-388bfd:1788620400123"])
        assessment = assess_frame(frame)
        self.assertEqual(assessment.outcome, METADATA_ASSESSED)
        self.assertEqual(assessment.metadata.evidence_ref_count, 1)

    def test_the_detector_reports_where_it_found_them(self):
        self.assertEqual(find_sample_bearing_field({"a": {"b": {"iq": 1}}}), "a.b.iq")
        self.assertIsNone(find_sample_bearing_field({"power_unit": "DBFS"}))


class StructuralRefusalTests(unittest.TestCase):
    """Refusals accumulate; only raw IQ short-circuits."""

    def _facts(self, frame):
        assessment = assess_frame(frame)
        self.assertEqual(assessment.outcome, METADATA_ASSESSED)
        return assessment.facts

    def test_a_complete_frame_has_no_metadata_refusal(self):
        facts = self._facts(_frame())
        self.assertEqual((facts.signal_chain_unbound, facts.receiver_state_unbound,
                          facts.product_lineage_unbound, facts.power_unit_unsupported),
                         (False, False, False, False))

    def test_each_signal_chain_field_alone_unbinds_the_group(self):
        for field in SIGNAL_CHAIN_FIELDS:
            with self.subTest(field=field):
                facts = self._facts(_frame(**{field: None}))
                self.assertTrue(facts.signal_chain_unbound)
                self.assertFalse(facts.receiver_state_unbound)
                self.assertFalse(facts.product_lineage_unbound)

    def test_each_receiver_state_field_alone_unbinds_the_group(self):
        for field in RECEIVER_STATE_FIELDS:
            with self.subTest(field=field):
                facts = self._facts(_frame(**{field: None}))
                self.assertTrue(facts.receiver_state_unbound)
                self.assertFalse(facts.signal_chain_unbound)

    def test_each_product_lineage_field_alone_unbinds_the_group(self):
        for field in PRODUCT_LINEAGE_FIELDS:
            with self.subTest(field=field):
                facts = self._facts(_frame(**{field: None}))
                self.assertTrue(facts.product_lineage_unbound)
                self.assertFalse(facts.signal_chain_unbound)

    def test_structural_refusals_do_not_end_evaluation(self):
        """Every applicable reason must survive to the verdict."""
        frame = _frame(signal_chain_hash=None, sweep_plan_revision=None,
                       receiver_state_chain_hash=None, power_unit="DBW")
        facts = self._facts(frame)
        verdict = decide(AdmissionFacts.from_stages(facts, ALIGNED))
        self.assertEqual(verdict.disposition, BREADCRUMB_ONLY)
        self.assertEqual(verdict.reasons,
                         ("SIGNAL_CHAIN_UNBOUND", "RECEIVER_STATE_UNBOUND",
                          "PRODUCT_LINEAGE_UNBOUND", "POWER_UNIT_UNSUPPORTED"))

    def test_all_group_combinations_stay_independent(self):
        groups = {"signal_chain_unbound": SIGNAL_CHAIN_FIELDS[0],
                  "receiver_state_unbound": RECEIVER_STATE_FIELDS[0],
                  "product_lineage_unbound": PRODUCT_LINEAGE_FIELDS[0]}
        names = list(groups)
        for drop in itertools.product((False, True), repeat=3):
            missing = {groups[name]: None
                       for name, flag in zip(names, drop) if flag}
            facts = self._facts(_frame(**missing))
            for name, flag in zip(names, drop):
                self.assertEqual(getattr(facts, name), flag, (drop, name))


class PowerUnitTests(unittest.TestCase):
    def _unsupported(self, frame):
        return assess_frame(frame).facts.power_unit_unsupported

    def test_dbfs_needs_nothing(self):
        self.assertFalse(self._unsupported(_frame(power_unit="DBFS")))

    def test_an_absent_or_unknown_unit_is_unsupported(self):
        for unit in (None, "DBW", "dB", "watts", ""):
            with self.subTest(unit=unit):
                self.assertTrue(self._unsupported(_frame(power_unit=unit)))

    def test_dbm_without_a_calibration_is_unsupported(self):
        self.assertTrue(self._unsupported(_frame(power_unit="DBM")))

    def test_dbm_with_a_complete_calibration_is_supported(self):
        frame = _frame(power_unit="DBM", calibration=_calibration())
        self.assertFalse(self._unsupported(frame))
        self.assertEqual(assess_frame(frame).metadata.power_unit, "DBM")

    def test_dbm_with_any_field_missing_is_unsupported(self):
        for field in sorted(ALLOWED_CALIBRATION_FIELDS):
            with self.subTest(field=field):
                block = _calibration()
                block.pop(field)
                self.assertTrue(self._unsupported(
                    _frame(power_unit="DBM", calibration=block)))

    def test_a_malformed_calibration_is_refused_not_accepted(self):
        for bad in ({"frequency_range_hz": [108.0e6, 88.0e6]},
                    {"frequency_range_hz": [88.0e6]},
                    {"uncertainty_db": -1.0},
                    {"extension_mm": 0.5}):
            with self.subTest(bad=bad):
                with self.assertRaises(FrameMetadataError):
                    assess_frame(_frame(power_unit="DBM",
                                        calibration=_calibration(**bad)))

    def test_an_unknown_calibration_field_is_refused(self):
        block = _calibration()
        block["fudge_factor_db"] = 3.0
        with self.assertRaises(FrameMetadataError):
            assess_frame(_frame(power_unit="DBM", calibration=block))

    def test_an_unsupported_unit_is_not_carried_into_validated_metadata(self):
        self.assertIsNone(assess_frame(_frame(power_unit="DBW")).metadata.power_unit)


class BoundsTests(unittest.TestCase):
    def test_a_non_mapping_is_not_a_frame(self):
        for bad in ([], "frame", 7, None):
            with self.assertRaises(FrameMetadataError):
                assess_frame(bad)

    def test_unknown_fields_reject_the_whole_frame(self):
        with self.assertRaises(FrameMetadataError):
            assess_frame({**_frame(), "extra": 1})

    def test_the_assessability_note_maps_onto_the_contract_category(self):
        """The contract names the category; the note maps the exception onto it."""
        from rf_walk_survey_metadata import ASSESSABILITY_NOTE
        self.assertIn("NOT A SURVEY FRAME", ASSESSABILITY_NOTE)
        self.assertIn("THE VOCABULARY'S RANGE", ASSESSABILITY_NOTE)
        self.assertIn("NOT REFUSED", ASSESSABILITY_NOTE)
        self.assertIn("FrameMetadataError", ASSESSABILITY_NOTE)
        self.assertIn("assessability_note", metadata_status())

    def test_an_unassessable_input_produces_no_verdict_at_all(self):
        """Outside the vocabulary's range means no disposition, not a refusal."""
        with self.assertRaises(FrameMetadataError) as caught:
            assess_frame(_frame(frame_id=None))
        message = str(caught.exception)
        for verdict_word in ("SURFACE_ELIGIBLE", "BREADCRUMB_ONLY", "FRAME_REFUSED"):
            self.assertNotIn(verdict_word, message)

    def test_missing_identity_makes_the_frame_unassessable(self):
        for field in ("frame_id", "observer_id", "monotonic_source_id",
                      "acquisition_start_monotonic_ns",
                      "acquisition_end_monotonic_ns", "configuration_epoch"):
            with self.subTest(field=field):
                with self.assertRaises(FrameMetadataError) as caught:
                    assess_frame(_frame(**{field: None}))
                self.assertIn(field, str(caught.exception))

    def test_acquisition_bounds_must_be_ordered(self):
        start = _frame()["acquisition_start_monotonic_ns"]
        for end in (start, start - 1, 0):
            with self.subTest(end=end):
                with self.assertRaises(FrameMetadataError):
                    assess_frame(_frame(acquisition_end_monotonic_ns=end))

    def test_an_absurd_acquisition_span_is_refused(self):
        start = _frame()["acquisition_start_monotonic_ns"]
        with self.assertRaises(FrameMetadataError):
            assess_frame(_frame(acquisition_end_monotonic_ns=start + 10**14))

    def test_bounds_must_be_integers_not_floats_or_bools(self):
        for bad in (1.5, True, "1000"):
            with self.subTest(bad=bad):
                with self.assertRaises(FrameMetadataError):
                    assess_frame(_frame(acquisition_start_monotonic_ns=bad))

    def test_strings_are_bounded(self):
        with self.assertRaises(FrameMetadataError):
            assess_frame(_frame(frame_id="x" * (MAX_STRING + 1)))
        with self.assertRaises(FrameMetadataError):
            assess_frame(_frame(observer_id="   "))

    def test_numeric_ranges_are_enforced(self):
        for field, bad in (("extension_mm", 0.73), ("extension_mm", 5000.0),
                           ("gain_db", 999.0), ("sample_rate_hz", 0.0),
                           ("sample_rate_hz", 5e7), ("gain_db", float("nan"))):
            with self.subTest(field=field, bad=bad):
                with self.assertRaises(FrameMetadataError):
                    assess_frame(_frame(**{field: bad}))

    def test_evidence_refs_are_bounded_in_count_and_size(self):
        with self.assertRaises(FrameMetadataError):
            assess_frame(_frame(evidence_refs=["r"] * (MAX_EVIDENCE_REFS + 1)))
        with self.assertRaises(FrameMetadataError):
            assess_frame(_frame(evidence_refs="not-a-list"))
        with self.assertRaises(FrameMetadataError):
            assess_frame(_frame(evidence_refs=["x" * (MAX_STRING + 1)]))

    def test_a_negative_configuration_epoch_is_refused(self):
        with self.assertRaises(FrameMetadataError):
            assess_frame(_frame(configuration_epoch=-1))


class AssessmentShapeTests(unittest.TestCase):
    """The discriminated union cannot be built inconsistently."""

    def test_raw_iq_frame_cannot_carry_facts(self):
        from rf_walk_survey_admission import AdmissionVerdict, MetadataAdmissionFacts
        verdict = AdmissionVerdict(FRAME_REFUSED, ("RAW_IQ_PRESENT",))
        with self.assertRaises(FrameMetadataError):
            FrameMetadataAssessment(RAW_IQ_FRAME, verdict=verdict,
                                    facts=MetadataAdmissionFacts(False, False, False, False))
        with self.assertRaises(FrameMetadataError):
            FrameMetadataAssessment(RAW_IQ_FRAME)

    def test_metadata_assessed_cannot_carry_a_verdict(self):
        """A verdict here would pre-empt alignment."""
        from rf_walk_survey_admission import AdmissionVerdict, MetadataAdmissionFacts
        facts = MetadataAdmissionFacts(False, False, False, False)
        payload = assess_frame(_frame()).metadata
        with self.assertRaises(FrameMetadataError):
            FrameMetadataAssessment(METADATA_ASSESSED, facts=facts, metadata=payload,
                                    verdict=AdmissionVerdict(SURFACE_ELIGIBLE))
        with self.assertRaises(FrameMetadataError):
            FrameMetadataAssessment(METADATA_ASSESSED, facts=facts)

    def test_an_unknown_outcome_is_refused(self):
        with self.assertRaises(FrameMetadataError):
            FrameMetadataAssessment("MAYBE")


class ValidationMeaningTests(unittest.TestCase):
    """Validated must not be readable as trusted or admitted."""

    def test_the_payload_says_what_validated_does_not_mean(self):
        payload = assess_frame(_frame()).metadata.as_dict()
        self.assertEqual(payload["validation"], "STRUCTURALLY_AND_SEMANTICALLY_WELL_FORMED")
        self.assertIn("NOT TRUSTWORTHY", payload["validation_limitation"])
        self.assertIn("NOT SURFACE-ELIGIBLE", payload["validation_limitation"])

    def test_surface_eligibility_is_not_decided_here(self):
        payload = assess_frame(_frame()).metadata.as_dict()
        self.assertIsNone(payload["surface_eligible"])
        self.assertIn("NOT DECIDED HERE", payload["surface_eligibility_note"])

    def test_the_assessment_says_alignment_has_not_run(self):
        payload = assess_frame(_frame()).as_dict()
        self.assertEqual(payload["alignment"], "NOT_RUN_HERE")
        self.assertIn("from_stages", payload["alignment_note"])

    def test_the_status_declares_what_it_does_not_decide(self):
        status = metadata_status()
        self.assertFalse(status["decides_time_alignment"])
        self.assertFalse(status["decides_receiver_state_staleness"])
        self.assertFalse(status["decides_surface_eligibility"])
        self.assertEqual(status["side_effects"], "NONE")
        self.assertEqual(status["unknown_field_policy"], "REJECT_WHOLE_FRAME")

    def test_a_well_formed_frame_is_not_thereby_surface_eligible(self):
        """Syntax cannot buy admission: alignment still decides."""
        facts = assess_frame(_frame()).facts
        starved = AdmissionFacts.from_stages(
            facts, AlignmentAdmissionFacts(time_alignment_unverified=True,
                                           receiver_state_stale=False,
                                           signal_chain_changed=False,
                                           receiver_state_chain_changed=False))
        self.assertEqual(decide(starved).disposition, BREADCRUMB_ONLY)
        aligned = AdmissionFacts.from_stages(facts, ALIGNED)
        self.assertEqual(decide(aligned).disposition, SURFACE_ELIGIBLE)


class ScopeTests(unittest.TestCase):
    def _source(self):
        with open(inspect.getsourcefile(metadata_module), encoding="utf-8") as handle:
            return handle.read()

    def test_the_module_cannot_store_mutate_or_align(self):
        source = self._source()
        for forbidden in ("writebus", "WriteBus", "GraphOp", "h3", "sqlite",
                          "requests", "flask", "socket", "subprocess", "numpy",
                          "os.environ", "time.", "monotonic()"):
            self.assertNotIn(forbidden, source, forbidden)

    def test_it_does_not_import_receiver_state_or_call_into_alignment(self):
        """Prose may name the authority; code may not reach for it."""
        import ast
        tree = ast.parse(self._source())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        self.assertEqual(imported, {"__future__", "dataclasses", "math", "typing",
                                    "rf_walk_survey_admission"})
        called = {node.func.id for node in ast.walk(tree)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        for forbidden in ("time_align", "may_update_posterior", "build_receiver_state"):
            self.assertNotIn(forbidden, called)

    def test_assessment_is_pure_across_repeated_calls(self):
        frame = _frame(sweep_plan_revision=None)
        first = assess_frame(frame).as_dict()
        for _ in range(5):
            self.assertEqual(assess_frame(frame).as_dict(), first)

    def test_payloads_are_json_serializable(self):
        for frame in (_frame(), _frame(signal_chain_hash=None),
                      {**_frame(), "iq": "x"}):
            json.dumps(assess_frame(frame).as_dict())


if __name__ == "__main__":
    unittest.main()
