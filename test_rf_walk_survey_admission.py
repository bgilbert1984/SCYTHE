"""§4 of the walking-survey contract, exhaustively.

The combination space is enumerated rather than sampled. Cardinality drift --
a disposition that becomes two, a reason that swallows another -- shows up in
combinations, not in hand-picked examples, and every hand-picked example this
suite could contain is already one of the 128 below.
"""

import inspect
import itertools
import unittest

import rf_walk_survey_admission as admission
from rf_walk_survey_admission import (
    BREADCRUMB_ONLY, DISPOSITIONS, EXCLUSIVE_REASON, FRAME_REFUSED,
    ORDINARY_REASONS, REASON_CODES, REASON_NOTES, SURFACE_ELIGIBLE,
    AdmissionFacts, AdmissionVerdict, UnknownDisposition, UnknownReasonCode,
    VerdictInvariantError, admission_status, decide, decide_from_reason_codes,
)


ORDINARY_FIELDS = ("time_alignment_unverified", "receiver_state_stale",
                   "signal_chain_unbound", "receiver_state_unbound",
                   "product_lineage_unbound", "power_unit_unsupported")


def _facts(flags, raw_iq=False):
    return AdmissionFacts(raw_iq_present=raw_iq,
                          **dict(zip(ORDINARY_FIELDS, flags)))


class CombinationSpaceTests(unittest.TestCase):
    """All 2^6 ordinary combinations, then all of them again with raw IQ."""

    def test_the_space_is_the_size_the_contract_says(self):
        self.assertEqual(len(ORDINARY_FIELDS), 6)
        self.assertEqual(len(ORDINARY_REASONS), 6)
        self.assertEqual(len(REASON_CODES), 7)
        self.assertEqual(len(DISPOSITIONS), 3)

    def test_every_ordinary_combination_yields_exactly_one_disposition(self):
        seen = 0
        for flags in itertools.product((False, True), repeat=6):
            with self.subTest(flags=flags):
                verdict = decide(_facts(flags))
                seen += 1
                self.assertIn(verdict.disposition, DISPOSITIONS)
                expected = tuple(code for flag, code in zip(flags, ORDINARY_REASONS) if flag)
                if not any(flags):
                    self.assertEqual(verdict.disposition, SURFACE_ELIGIBLE)
                    self.assertEqual(verdict.reasons, ())
                else:
                    self.assertEqual(verdict.disposition, BREADCRUMB_ONLY)
                    self.assertEqual(verdict.reasons, expected,
                                     "every applicable reason, in contract order")
                self.assertNotEqual(verdict.disposition, FRAME_REFUSED,
                                    "no raw-IQ input may ever refuse a frame")
        self.assertEqual(seen, 64)

    def test_raw_iq_dominates_every_one_of_those_combinations(self):
        seen = 0
        for flags in itertools.product((False, True), repeat=6):
            with self.subTest(flags=flags):
                verdict = decide(_facts(flags, raw_iq=True))
                seen += 1
                self.assertEqual(verdict.disposition, FRAME_REFUSED)
                self.assertEqual(verdict.reasons, (EXCLUSIVE_REASON,),
                                 "raw IQ short-circuits; nothing else is evaluated")
        self.assertEqual(seen, 64)

    def test_reason_count_matches_flag_count_exactly(self):
        """A reason that swallowed another would show up here and nowhere else."""
        for flags in itertools.product((False, True), repeat=6):
            with self.subTest(flags=flags):
                self.assertEqual(len(decide(_facts(flags)).reasons), sum(flags))

    def test_every_reason_is_reachable_and_reachable_alone(self):
        for index, code in enumerate(ORDINARY_REASONS):
            flags = [False] * 6
            flags[index] = True
            verdict = decide(_facts(tuple(flags)))
            self.assertEqual(verdict.reasons, (code,))
            self.assertEqual(verdict.disposition, BREADCRUMB_ONLY)
        self.assertEqual(decide(_facts((False,) * 6, raw_iq=True)).reasons,
                         (EXCLUSIVE_REASON,))

    def test_no_field_group_can_produce_another_groups_code(self):
        """The three unbound reasons must stay distinct, per contract §4."""
        unbound = {"signal_chain_unbound": "SIGNAL_CHAIN_UNBOUND",
                   "receiver_state_unbound": "RECEIVER_STATE_UNBOUND",
                   "product_lineage_unbound": "PRODUCT_LINEAGE_UNBOUND"}
        for field, code in unbound.items():
            verdict = decide(AdmissionFacts(**{field: True}))
            self.assertEqual(verdict.reasons, (code,))
            for other in set(unbound.values()) - {code}:
                self.assertNotIn(other, verdict.reasons)

    def test_a_two_group_failure_carries_both_codes_under_one_disposition(self):
        verdict = decide(AdmissionFacts(signal_chain_unbound=True,
                                        product_lineage_unbound=True))
        self.assertEqual(verdict.disposition, BREADCRUMB_ONLY)
        self.assertEqual(verdict.reasons,
                         ("SIGNAL_CHAIN_UNBOUND", "PRODUCT_LINEAGE_UNBOUND"))


class OrderingTests(unittest.TestCase):
    """Serialization order is the contract's, never the caller's."""

    def test_reasons_serialize_in_frozen_contract_order(self):
        reversed_codes = tuple(reversed(ORDINARY_REASONS))
        verdict = decide_from_reason_codes(reversed_codes)
        self.assertEqual(verdict.reasons, ORDINARY_REASONS)

    def test_two_callers_discovering_in_different_orders_agree(self):
        a = decide_from_reason_codes(["POWER_UNIT_UNSUPPORTED", "RECEIVER_STATE_STALE"])
        b = decide_from_reason_codes(["RECEIVER_STATE_STALE", "POWER_UNIT_UNSUPPORTED"])
        self.assertEqual(a.as_dict(), b.as_dict())

    def test_duplicates_are_collapsed(self):
        verdict = decide_from_reason_codes(["SIGNAL_CHAIN_UNBOUND"] * 5)
        self.assertEqual(verdict.reasons, ("SIGNAL_CHAIN_UNBOUND",))

    def test_a_hand_built_verdict_is_reordered_rather_than_trusted(self):
        verdict = AdmissionVerdict(BREADCRUMB_ONLY,
                                   ("POWER_UNIT_UNSUPPORTED", "TIME_ALIGNMENT_UNVERIFIED"))
        self.assertEqual(verdict.reasons,
                         ("TIME_ALIGNMENT_UNVERIFIED", "POWER_UNIT_UNSUPPORTED"))


class InvariantTests(unittest.TestCase):
    """An invalid verdict must be unconstructable, not merely unproduced."""

    def test_surface_eligible_cannot_carry_a_reason(self):
        with self.assertRaises(VerdictInvariantError):
            AdmissionVerdict(SURFACE_ELIGIBLE, ("SIGNAL_CHAIN_UNBOUND",))

    def test_frame_refused_cannot_carry_anything_but_raw_iq(self):
        with self.assertRaises(VerdictInvariantError):
            AdmissionVerdict(FRAME_REFUSED, ("SIGNAL_CHAIN_UNBOUND",))
        with self.assertRaises(VerdictInvariantError):
            AdmissionVerdict(FRAME_REFUSED, (EXCLUSIVE_REASON, "SIGNAL_CHAIN_UNBOUND"))
        with self.assertRaises(VerdictInvariantError):
            AdmissionVerdict(FRAME_REFUSED, ())

    def test_breadcrumb_only_needs_a_reason_and_refuses_raw_iq(self):
        with self.assertRaises(VerdictInvariantError):
            AdmissionVerdict(BREADCRUMB_ONLY, ())
        with self.assertRaises(VerdictInvariantError):
            AdmissionVerdict(BREADCRUMB_ONLY, (EXCLUSIVE_REASON,))

    def test_an_unknown_disposition_is_refused_not_coerced(self):
        for bad in ("SURFACE_ELIGABLE", "REJECTED", "", None, "surface_eligible"):
            with self.assertRaises(UnknownDisposition):
                AdmissionVerdict(bad)

    def test_an_unknown_reason_is_refused_not_absorbed(self):
        for bad in ("UNKNOWN", "OTHER", "raw_iq_present", "", None):
            with self.assertRaises(UnknownReasonCode):
                AdmissionVerdict(BREADCRUMB_ONLY, (bad,))
            with self.assertRaises(UnknownReasonCode):
                decide_from_reason_codes([bad])

    def test_there_is_no_fallback_reason(self):
        status = admission_status()
        self.assertIsNone(status["fallback_reason"])
        for code in REASON_CODES:
            for banned in ("UNKNOWN", "OTHER", "UNSPECIFIED", "MISC", "GENERIC"):
                self.assertNotIn(banned, code)

    def test_the_verdict_is_immutable(self):
        verdict = decide(AdmissionFacts(receiver_state_stale=True))
        with self.assertRaises(Exception):
            verdict.disposition = FRAME_REFUSED
        with self.assertRaises(Exception):
            verdict.reasons = ()
        self.assertIsInstance(verdict.reasons, tuple)

    def test_the_verdict_is_bounded_by_the_vocabulary(self):
        every = decide(_facts((True,) * 6))
        self.assertEqual(len(every.reasons), 6)
        self.assertLessEqual(len(every.reasons), len(REASON_CODES))


class CapabilityTests(unittest.TestCase):
    """Breadcrumb and surface follow from the disposition, never beside it."""

    def test_each_disposition_permits_what_the_contract_says(self):
        expected = {
            SURFACE_ELIGIBLE: (True, True),
            BREADCRUMB_ONLY: (True, False),
            FRAME_REFUSED: (False, False),
        }
        cases = {
            SURFACE_ELIGIBLE: AdmissionFacts(),
            BREADCRUMB_ONLY: AdmissionFacts(receiver_state_stale=True),
            FRAME_REFUSED: AdmissionFacts(raw_iq_present=True),
        }
        for disposition, facts in cases.items():
            verdict = decide(facts)
            self.assertEqual(verdict.disposition, disposition)
            self.assertEqual(
                (verdict.breadcrumb_retained, verdict.surface_contribution),
                expected[disposition])

    def test_only_a_refused_frame_loses_its_breadcrumb(self):
        for flags in itertools.product((False, True), repeat=6):
            self.assertTrue(decide(_facts(flags)).breadcrumb_retained)
        self.assertFalse(decide(AdmissionFacts(raw_iq_present=True)).breadcrumb_retained)

    def test_a_refusal_carries_its_scope_so_it_is_not_over_read(self):
        payload = decide(AdmissionFacts(raw_iq_present=True)).as_dict()
        self.assertIn("SCOPED TO THIS FRAME", payload["refusal_scope"])
        self.assertIn("NEITHER DELETED NOR INVALIDATED", payload["refusal_scope"])
        self.assertNotIn("refusal_scope", decide(AdmissionFacts()).as_dict())


class SerializationTests(unittest.TestCase):
    def test_the_payload_names_the_contract_it_implements(self):
        payload = decide(AdmissionFacts(signal_chain_unbound=True)).as_dict()
        self.assertEqual(payload["contract"], "docs/RF_WALK_SURVEY_CONTRACT.md")
        self.assertEqual(payload["contract_section"], "4")
        self.assertEqual(payload["reasons"], ["SIGNAL_CHAIN_UNBOUND"])
        self.assertIn("REPAIRED BY THE RF APPARATUS DECLARATION",
                      payload["reason_notes"]["SIGNAL_CHAIN_UNBOUND"])

    def test_every_reason_has_a_note(self):
        self.assertEqual(set(REASON_NOTES), set(REASON_CODES))

    def test_the_payload_is_json_serializable_and_carries_no_samples(self):
        import json
        for facts in (AdmissionFacts(), AdmissionFacts(raw_iq_present=True),
                      _facts((True,) * 6)):
            blob = json.dumps(decide(facts).as_dict())
            self.assertNotIn("iq", blob.lower().replace("raw_iq", ""))


class ScopeTests(unittest.TestCase):
    """What the module is not allowed to be able to do."""

    def _source(self):
        with open(inspect.getsourcefile(admission), encoding="utf-8") as handle:
            return handle.read()

    def test_the_module_cannot_reach_a_surface_a_graph_or_a_device(self):
        source = self._source()
        for forbidden in ("writebus", "WriteBus", "GraphOp", "h3", "sqlite",
                          "requests", "flask", "socket", "subprocess", "open(",
                          "numpy", "np.", "os.environ"):
            self.assertNotIn(forbidden, source,
                             f"admission must not be able to touch {forbidden}")

    def test_the_module_imports_nothing_beyond_the_standard_library_shapes(self):
        source = self._source()
        imports = [line for line in source.splitlines()
                   if line.startswith(("import ", "from ")) and "__future__" not in line]
        self.assertEqual(imports, ["from dataclasses import dataclass, fields",
                                   "from typing import Any, Dict, Iterable, Tuple"])

    def test_it_does_not_recreate_the_alignment_capability_table(self):
        """Alignment authority stays in rf_receiver_state, not duplicated here."""
        source = self._source()
        self.assertNotIn("ALIGNMENT_CAPABILITIES = ", source)
        self.assertNotIn("heatmap_update", source)
        for state in ("VERIFIED", "BOUNDED", "STALE"):
            self.assertNotIn(f'"{state}":', source)
        self.assertIn("rf_receiver_state", admission_status()["alignment_authority"])

    def test_it_declares_that_it_updates_and_mutates_nothing(self):
        status = admission_status()
        self.assertEqual(status["side_effects"], "NONE")
        self.assertEqual(status["surface_update"], "NOT_IMPLEMENTED")
        self.assertEqual(status["graph_mutation"], "NOT_IMPLEMENTED")

    def test_decide_is_pure_across_repeated_calls(self):
        facts = _facts((True, False, True, False, True, False))
        first = decide(facts).as_dict()
        for _ in range(5):
            self.assertEqual(decide(facts).as_dict(), first)


class FactsTests(unittest.TestCase):
    def test_facts_round_trip_through_reason_codes(self):
        for flags in itertools.product((False, True), repeat=6):
            facts = _facts(flags)
            self.assertEqual(AdmissionFacts.from_reason_codes(facts.reason_codes()),
                             facts)

    def test_facts_default_to_nothing_wrong(self):
        self.assertEqual(AdmissionFacts().reason_codes(), ())
        self.assertEqual(decide(AdmissionFacts()).disposition, SURFACE_ELIGIBLE)

    def test_an_unknown_field_is_refused_by_construction(self):
        with self.assertRaises(TypeError):
            AdmissionFacts(unknown_problem=True)


if __name__ == "__main__":
    unittest.main()
