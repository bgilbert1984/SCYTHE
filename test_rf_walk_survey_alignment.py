"""The alignment stage, and the end-to-end composition it completes."""

import inspect
import itertools
import json
import unittest

import rf_walk_survey_alignment as alignment_module
from rf_receiver_state import (
    ALIGNMENT_STATES, JOIN_REFUSALS, AcquisitionInterval, ClockMapping,
    MonotonicInstant, TimeAlignedJoin, build_receiver_state, may_update_posterior,
)
from rf_walk_survey_admission import (
    BREADCRUMB_ONLY, SURFACE_ELIGIBLE, AdmissionFacts, AlignmentAdmissionFacts,
    MetadataAdmissionFacts, decide,
)
from rf_walk_survey_alignment import (
    CHAIN_CHANGE_NOTE, AlignmentAssessment, AlignmentUnmappable,
    alignment_status, assess_alignment, facts_for,
)
from rf_walk_survey_metadata import assess_frame

SECOND = 1_000_000_000
CLOCK = "phone-boot-1"
ACQ_START = 1_000 * SECOND
ACQUISITION = AcquisitionInterval(CLOCK, ACQ_START, ACQ_START + 100_000_000)


def _at(offset_ms=0.0, source=CLOCK):
    return MonotonicInstant(source, int(ACQ_START + offset_ms * 1e6))


def _state(**overrides):
    kwargs = dict(device_id="phone-1", latitude=29.735, longitude=-94.977,
                  horizontal_accuracy_m=4.8, position_authority="DEVICE_GNSS",
                  speed_mps=1.1, observed_at=_at(50.0),
                  alignment_uncertainty_ms=42.0,
                  alignment_method="BOUNDED_CLOCK_EXCHANGE")
    kwargs.update(overrides)
    return build_receiver_state(**kwargs)


class MappingTests(unittest.TestCase):
    """Every join outcome maps, and the mapping is total by construction."""

    def test_every_alignment_state_has_a_mapping(self):
        table = alignment_status()["status_to_facts"]
        self.assertEqual(set(table), set(ALIGNMENT_STATES))

    def test_the_two_admitting_states_map_identically(self):
        """VERIFIED and BOUNDED both admit; collapsing them here is correct."""
        table = alignment_status()["status_to_facts"]
        self.assertEqual(table["VERIFIED"], table["BOUNDED"])
        self.assertEqual(table["VERIFIED"],
                         {"time_alignment_unverified": False,
                          "receiver_state_stale": False,
                          "signal_chain_changed": False,
                          "receiver_state_chain_changed": False})

    def test_stale_is_not_reported_as_unverified(self):
        """Something did join. 'Nothing joined' would be a different claim."""
        table = alignment_status()["status_to_facts"]
        self.assertEqual(table["STALE"], {"time_alignment_unverified": False,
                                          "receiver_state_stale": True,
                                          "signal_chain_changed": False,
                                          "receiver_state_chain_changed": False})

    def test_every_refusal_maps_to_nothing_joined(self):
        mappable = set(JOIN_REFUSALS) - {"SIGNAL_CHAIN_CHANGED",
                                         "RECEIVER_STATE_CHAIN_CHANGED"}
        for refusal in mappable:
            with self.subTest(refusal=refusal):
                facts = facts_for(TimeAlignedJoin(refusal=refusal))
                self.assertTrue(facts.time_alignment_unverified)
                self.assertFalse(facts.receiver_state_stale,
                                 "a refusal cannot be stale; nothing joined")

    def test_an_unknown_status_raises_rather_than_admitting(self):
        with self.assertRaises(AlignmentUnmappable):
            facts_for(TimeAlignedJoin(joined=True, alignment_status="PROBABLY_FINE"))

    def test_a_joined_result_never_reports_two_alignment_faults(self):
        """A join is absent, or present and stale, or present and fine."""
        for status in ALIGNMENT_STATES:
            facts = facts_for(TimeAlignedJoin(joined=True, alignment_status=status))
            self.assertFalse(facts.time_alignment_unverified
                             and facts.receiver_state_stale, status)


class ChainChangeTests(unittest.TestCase):
    """Vocabulary v2: the two disagreement refusals map without approximation."""

    MAPPING = {
        "SIGNAL_CHAIN_CHANGED": "signal_chain_changed",
        "RECEIVER_STATE_CHAIN_CHANGED": "receiver_state_chain_changed",
    }

    def test_each_refusal_maps_to_exactly_its_own_fact(self):
        for refusal, field in self.MAPPING.items():
            with self.subTest(refusal=refusal):
                facts = facts_for(TimeAlignedJoin(refusal=refusal))
                self.assertTrue(getattr(facts, field))
                others = set(self.MAPPING.values()) - {field}
                for other in others:
                    self.assertFalse(getattr(facts, other),
                                     "one authority's disagreement is not another's")

    def test_a_disagreement_is_not_reported_as_nothing_joined(self):
        """Two different failures; reporting both sends an operator to two places."""
        for refusal in self.MAPPING:
            facts = facts_for(TimeAlignedJoin(refusal=refusal))
            self.assertFalse(facts.time_alignment_unverified, refusal)
            self.assertFalse(facts.receiver_state_stale, refusal)

    def test_a_chain_disagreement_produces_breadcrumb_only(self):
        for refusal, _field in self.MAPPING.items():
            with self.subTest(refusal=refusal):
                facts = facts_for(TimeAlignedJoin(refusal=refusal))
                verdict = decide(AdmissionFacts.from_stages(
                    MetadataAdmissionFacts(False, False, False, False), facts))
                self.assertEqual(verdict.disposition, BREADCRUMB_ONLY)
                self.assertNotEqual(verdict.disposition, SURFACE_ELIGIBLE)

    def test_absent_and_changed_are_distinct_codes(self):
        from rf_walk_survey_admission import (
            RECEIVER_STATE_CHAIN_CHANGED, RECEIVER_STATE_UNBOUND,
            SIGNAL_CHAIN_CHANGED, SIGNAL_CHAIN_UNBOUND,
        )
        self.assertNotEqual(SIGNAL_CHAIN_UNBOUND, SIGNAL_CHAIN_CHANGED)
        self.assertNotEqual(RECEIVER_STATE_UNBOUND, RECEIVER_STATE_CHAIN_CHANGED)

    def test_a_group_is_never_both_unbound_and_changed(self):
        """Disagreement needs two identities; absence means there is not one."""
        from rf_walk_survey_admission import AUTHORITY_GROUPS
        for group, kinds in AUTHORITY_GROUPS.items():
            if "disagreeing" not in kinds:
                continue
            with self.subTest(group=group):
                # The two facts come from different stages, so the only way both
                # could appear is if a stage set the other's field.
                for refusal in self.MAPPING:
                    facts = facts_for(TimeAlignedJoin(refusal=refusal))
                    metadata = MetadataAdmissionFacts(False, False, False, False)
                    reasons = decide(
                        AdmissionFacts.from_stages(metadata, facts)).reasons
                    self.assertNotIn(kinds["absent"], reasons)

    def test_both_disagreements_may_coexist_when_both_chains_moved(self):
        """Separate authorities. A frame can be wrong about both."""
        facts = AlignmentAdmissionFacts(False, False, True, True)
        verdict = decide(AdmissionFacts.from_stages(
            MetadataAdmissionFacts(False, False, False, False), facts))
        self.assertEqual(verdict.disposition, BREADCRUMB_ONLY)
        self.assertEqual(verdict.reasons,
                         ("SIGNAL_CHAIN_CHANGED", "RECEIVER_STATE_CHAIN_CHANGED"))

    def test_the_raise_remains_for_a_genuinely_unmapped_refusal(self):
        with self.assertRaises(AlignmentUnmappable):
            facts_for(TimeAlignedJoin(refusal="SOME_FUTURE_REFUSAL"))

    def test_no_expected_hashes_are_accepted_merely_because_codes_exist(self):
        """A vocabulary that can describe a result is not one that produces it."""
        parameters = list(inspect.signature(assess_alignment).parameters)
        self.assertEqual(parameters, ["acquisition", "state", "clock_mapping"])
        self.assertFalse(alignment_status()["accepts_expected_hashes"])
        self.assertIn("NOT A MECHANISM THAT PRODUCES ONE", CHAIN_CHANGE_NOTE)

    def test_the_status_publishes_the_vocabulary_revision(self):
        self.assertEqual(alignment_status()["reason_vocabulary_revision"], "v2")


class AssessmentTests(unittest.TestCase):
    """Evidence beside facts, as the metadata stage does it."""

    def test_a_contemporaneous_state_admits(self):
        assessment = assess_alignment(ACQUISITION, _state())
        self.assertTrue(assessment.joined)
        self.assertEqual(assessment.join.alignment_status, "BOUNDED")
        self.assertFalse(assessment.facts.time_alignment_unverified)
        self.assertTrue(assessment.may_update_surface)

    def test_a_remote_state_is_stale_and_may_not_update_a_surface(self):
        assessment = assess_alignment(ACQUISITION, _state(observed_at=_at(-3_600_000.0)))
        self.assertEqual(assessment.join.alignment_status, "STALE")
        self.assertTrue(assessment.facts.receiver_state_stale)
        self.assertFalse(assessment.may_update_surface)

    def test_the_join_survives_beside_the_booleans(self):
        """The whole reason for the wrapper: BOUNDED must stay propagatable.

        Contract section 5 requires a BOUNDED join to propagate its
        contribution to pose uncertainty rather than discard it, which it can
        only do if the evidence outlives the collapse to two booleans.
        """
        assessment = assess_alignment(ACQUISITION, _state(observed_at=_at(-400.0)))
        self.assertEqual(assessment.facts,
                         AlignmentAdmissionFacts(False, False, False, False))
        self.assertEqual(assessment.join.separation_ms, 400.0)
        self.assertEqual(assessment.join.timing_budget_ms, 442.0)
        self.assertIsNotNone(assessment.join.pose_uncertainty_m)
        self.assertEqual(assessment.join.method, "BOUNDED_CLOCK_EXCHANGE")

    def test_the_payload_says_the_facts_are_lossy(self):
        payload = assess_alignment(ACQUISITION, _state()).as_dict()
        self.assertTrue(payload["facts_are_lossy"])
        self.assertIn("SURVIVES IN THE JOIN", payload["lossy_note"])
        self.assertEqual(payload["decides"], "ALIGNMENT_FACTS_ONLY_NOT_A_VERDICT")
        json.dumps(payload)

    def test_a_cross_clock_mapping_reaches_the_join(self):
        other = _state(observed_at=_at(50.0, source="watch-boot-3"))
        self.assertTrue(assess_alignment(ACQUISITION, other).facts.time_alignment_unverified)
        mapped = assess_alignment(ACQUISITION, other,
                                  clock_mapping=ClockMapping("watch-boot-3", CLOCK, 0, 20.0))
        self.assertTrue(mapped.joined)
        self.assertEqual(mapped.join.clock_mapping_uncertainty_ms, 20.0)


class CompositionTests(unittest.TestCase):
    """Step 5: both stages into one verdict."""

    def _frame(self, **overrides):
        frame = {
            "frame_id": "f-1", "observer_id": "phone-1",
            "monotonic_source_id": CLOCK,
            "acquisition_start_monotonic_ns": ACQUISITION.start_ns,
            "acquisition_end_monotonic_ns": ACQUISITION.end_ns,
            "configuration_epoch": 7, "power_unit": "DBFS",
            "signal_chain_hash": "blake2s:abc", "antenna_id": "a",
            "feedline_id": "f", "extension_mm": 368.0, "gain_db": 29.7,
            "sample_rate_hz": 2_048_000.0,
            "receiver_state_chain_hash": "blake2s:def",
            "receiver_state_device_id": "phone-1",
            "sweep_plan_revision": "sp1", "processing_revision": "pr1",
        }
        for key, value in overrides.items():
            if value is None:
                frame.pop(key, None)
            else:
                frame[key] = value
        return frame

    # A distinct sentinel: None is a meaningful value for `state` -- it is the
    # "no receiver state at all" case -- so it cannot double as "use the default".
    DEFAULT = object()

    def _verdict(self, frame=DEFAULT, state=DEFAULT):
        assessed = assess_frame(self._frame() if frame is self.DEFAULT else frame)
        aligned = assess_alignment(
            ACQUISITION, _state() if state is self.DEFAULT else state)
        return decide(AdmissionFacts.from_stages(assessed.facts, aligned.facts))

    def test_a_clean_frame_with_a_contemporaneous_state_is_surface_eligible(self):
        verdict = self._verdict()
        self.assertEqual(verdict.disposition, SURFACE_ELIGIBLE)
        self.assertEqual(verdict.reasons, ())

    def test_a_clean_frame_with_a_remote_state_is_breadcrumb_only(self):
        verdict = self._verdict(state=_state(observed_at=_at(-3_600_000.0)))
        self.assertEqual(verdict.disposition, BREADCRUMB_ONLY)
        self.assertEqual(verdict.reasons, ("RECEIVER_STATE_STALE",))

    def test_both_stages_contribute_their_reasons_to_one_verdict(self):
        verdict = self._verdict(frame=self._frame(sweep_plan_revision=None,
                                                  power_unit="DBW"),
                                state=_state(observed_at=_at(-3_600_000.0)))
        self.assertEqual(verdict.disposition, BREADCRUMB_ONLY)
        self.assertEqual(verdict.reasons, ("RECEIVER_STATE_STALE",
                                           "PRODUCT_LINEAGE_UNBOUND",
                                           "POWER_UNIT_UNSUPPORTED"))

    def test_a_well_formed_frame_cannot_buy_admission_without_a_join(self):
        verdict = self._verdict(state=None)
        self.assertEqual(verdict.disposition, BREADCRUMB_ONLY)
        self.assertEqual(verdict.reasons, ("TIME_ALIGNMENT_UNVERIFIED",))

    def test_every_alignment_state_composed_with_clean_metadata(self):
        assessed = assess_frame(self._frame())
        for status in ALIGNMENT_STATES:
            with self.subTest(status=status):
                facts = facts_for(TimeAlignedJoin(joined=True, alignment_status=status))
                verdict = decide(AdmissionFacts.from_stages(assessed.facts, facts))
                admits = status in ("VERIFIED", "BOUNDED")
                self.assertEqual(verdict.disposition,
                                 SURFACE_ELIGIBLE if admits else BREADCRUMB_ONLY)

    def test_a_raw_iq_frame_never_reaches_alignment_at_all(self):
        """The early exit means no fact set is produced to compose with."""
        assessed = assess_frame({**self._frame(), "iq": "AAAA"})
        self.assertEqual(assessed.outcome, "RAW_IQ_FRAME")
        self.assertIsNone(assessed.facts)


class ScopeTests(unittest.TestCase):
    def _source(self):
        with open(inspect.getsourcefile(alignment_module), encoding="utf-8") as handle:
            return handle.read()

    def test_the_module_decides_no_alignment_of_its_own(self):
        import ast
        tree = ast.parse(self._source())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        self.assertEqual(imported, {"__future__", "dataclasses", "typing",
                                    "rf_receiver_state", "rf_walk_survey_admission"})

    def test_it_cannot_store_mutate_or_reach_a_device(self):
        source = self._source()
        for forbidden in ("writebus", "GraphOp", "h3", "sqlite", "flask",
                          "socket", "subprocess", "numpy", "os.environ", "time."):
            self.assertNotIn(forbidden, source, forbidden)

    def test_it_declares_what_it_does_not_decide(self):
        status = alignment_status()
        self.assertFalse(status["decides_metadata_facts"])
        self.assertFalse(status["decides_surface_update"])
        self.assertEqual(status["side_effects"], "NONE")
        self.assertEqual(status["capabilities_authority"],
                         "rf_receiver_state.ALIGNMENT_CAPABILITIES")

    def test_assessment_is_pure_across_repeated_calls(self):
        first = assess_alignment(ACQUISITION, _state()).as_dict()
        for _ in range(5):
            self.assertEqual(assess_alignment(ACQUISITION, _state()).as_dict(), first)


if __name__ == "__main__":
    unittest.main()
