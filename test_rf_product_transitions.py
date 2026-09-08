"""RF comparability, checked against the real hashers rather than restated.

Every signature here is built through ``product_signature``, which calls
``rf_iq_retention.signal_chain_hash`` and ``graphops_rf_antenna``'s two hashers.
A contract asserting "the signal chain must change" is therefore checked
against the code the bridge uses, not against numbers this test made up.
"""

import inspect
import itertools
import json
import unittest

from graphops_rf_antenna import COMPARABILITY_FIELDS
from rf_iq_retention import SIGNAL_CHAIN_REVISION
from rf_product_transitions import (
    CHANGE_GAIN, CHANGE_SAMPLE_RATE, CONTRACTS, DECLARE_ANTENNA,
    DECLARE_ANTENNA_EXTENSION, DECLARE_FEEDLINE,
    NEVER_ESTABLISHED_BY_A_DECLARATION, RETUNE, SIGNATURE_FIELDS,
    product_signature, transitions_status,
)
from scythe_invariant_ledger import (
    EVIDENCE_MISSING, INVARIANTS_SATISFIED, PROHIBITED_CHANGE,
    REQUIRED_CHANGE_NOT_OBSERVED, Coordinate, check_transition,
)

BASE = dict(sensor_id="NESDR-SMART-V5-14530058", sample_type="uint8",
            sample_rate_hz=2_048_000.0, antenna_id="nesdr-smart-telescopic",
            feedline_id="nesdr-magnetic-base-rg58-2m", extension_mm=173.0,
            gain_db=29.7, center_frequency_hz=100_000_000.0,
            configuration_epoch=7)


def _sig(**overrides):
    return product_signature(**{**BASE, **overrides})


def _check(operation, contract, **after):
    return check_transition(_sig(), operation, _sig(**after), contract)


class HashesAreComputedTests(unittest.TestCase):
    """The signature must come from the real code, or the check is circular."""

    def test_the_hashers_are_the_bridge_s_own(self):
        authority = transitions_status()["hash_authority"]
        self.assertEqual(authority["signal_chain_hash"],
                         "rf_iq_retention.signal_chain_hash")
        self.assertEqual(authority["instrument_hash"],
                         "graphops_rf_antenna.instrument_hash")
        self.assertTrue(transitions_status()["hashes_are_computed_not_restated"])

    def test_the_signal_chain_revision_is_the_one_in_force(self):
        self.assertEqual(SIGNAL_CHAIN_REVISION, "v3")

    def test_every_comparability_field_actually_moves_the_instrument_hash(self):
        """Checked against graphops_rf_antenna, not asserted about it."""
        base = _sig()["instrument_hash"].value
        movers = {"antenna_id": "nesdr-smart-uhf",
                  "feedline_id": "direct", "extension_mm": 368.0}
        self.assertEqual(set(movers), set(COMPARABILITY_FIELDS))
        for field, value in movers.items():
            with self.subTest(field=field):
                self.assertNotEqual(_sig(**{field: value})["instrument_hash"].value,
                                    base, f"{field} must move the instrument hash")

    def test_gain_and_rate_move_the_signal_chain_but_not_the_instrument(self):
        base_chain = _sig()["signal_chain_hash"].value
        base_instrument = _sig()["instrument_hash"].value
        for field, value in (("gain_db", 40.2), ("sample_rate_hz", 2_400_000.0)):
            with self.subTest(field=field):
                moved = _sig(**{field: value})
                self.assertNotEqual(moved["signal_chain_hash"].value, base_chain)
                self.assertEqual(moved["instrument_hash"].value, base_instrument,
                                 "gain and rate are not the instrument")

    def test_a_retune_moves_neither_hash(self):
        """What the receiver listens through is not where it listens."""
        moved = _sig(center_frequency_hz=433_920_000.0)
        self.assertEqual(moved["signal_chain_hash"].value,
                         _sig()["signal_chain_hash"].value)
        self.assertEqual(moved["instrument_hash"].value,
                         _sig()["instrument_hash"].value)


class ExtensionTransitionTests(unittest.TestCase):
    """The defect that motivated the comparability boundary."""

    def test_a_declared_extension_change_satisfies_when_everything_moves(self):
        verdict = _check("DECLARE_ANTENNA_EXTENSION", DECLARE_ANTENNA_EXTENSION,
                         extension_mm=368.0, configuration_epoch=8)
        self.assertEqual(verdict.verdict, INVARIANTS_SATISFIED)

    def test_geometry_moving_without_the_epoch_is_caught(self):
        """730 mm to 165 mm keeps the antenna id and moves the response.

        The declaration hash already knew; the boundary stayed silent. A change
        that moves a hash and raises no boundary is a signal-chain change that
        produces no complaint, and only a required-transition rule catches it.
        """
        verdict = _check("DECLARE_ANTENNA_EXTENSION", DECLARE_ANTENNA_EXTENSION,
                         extension_mm=368.0)
        self.assertEqual(verdict.verdict, REQUIRED_CHANGE_NOT_OBSERVED)
        self.assertEqual([f.field for f in verdict.findings], ["configuration_epoch"])

    def test_an_extension_declaration_may_not_move_the_antenna(self):
        verdict = _check("DECLARE_ANTENNA_EXTENSION", DECLARE_ANTENNA_EXTENSION,
                         extension_mm=368.0, configuration_epoch=8,
                         antenna_id="nesdr-smart-uhf")
        self.assertEqual(verdict.verdict, PROHIBITED_CHANGE)
        self.assertIn("antenna_id", [f.field for f in verdict.findings])

    def test_the_real_quarter_wave_reference_moves_with_the_geometry(self):
        self.assertEqual(_sig(extension_mm=368.0)["quarter_wave_reference_hz"].value,
                         203_663_355)
        self.assertEqual(_sig(extension_mm=173.0)["quarter_wave_reference_hz"].value,
                         433_226_095)

    def test_an_undeclared_extension_is_absent_not_zero(self):
        coordinate = _sig(extension_mm=None)["quarter_wave_reference_hz"]
        self.assertEqual(coordinate.kind, "ABSENT")
        self.assertIsNone(coordinate.value)


class RetuneTests(unittest.TestCase):
    """A retune must not be recordable as an apparatus change."""

    def test_a_retune_satisfies(self):
        verdict = _check("RETUNE", RETUNE, center_frequency_hz=433_920_000.0,
                         configuration_epoch=8)
        self.assertEqual(verdict.verdict, INVARIANTS_SATISFIED)

    def test_a_retune_that_silently_moved_the_antenna_is_prohibited(self):
        """The example the invariant idea was proposed with."""
        verdict = _check("RETUNE", RETUNE, center_frequency_hz=433_920_000.0,
                         configuration_epoch=8, extension_mm=368.0)
        self.assertEqual(verdict.verdict, PROHIBITED_CHANGE)
        moved = {f.field for f in verdict.findings}
        self.assertIn("extension_mm", moved)
        self.assertIn("instrument_hash", moved)
        self.assertIn("signal_chain_hash", moved)

    def test_a_retune_that_did_not_move_is_not_a_retune(self):
        verdict = _check("RETUNE", RETUNE, configuration_epoch=8)
        self.assertEqual(verdict.verdict, REQUIRED_CHANGE_NOT_OBSERVED)
        self.assertEqual([f.field for f in verdict.findings], ["center_frequency_hz"])


class EveryTransitionTests(unittest.TestCase):
    """Each contract, driven positively and negatively."""

    MOVES = {
        "DECLARE_ANTENNA_EXTENSION": {"extension_mm": 368.0},
        "DECLARE_ANTENNA": {"antenna_id": "nesdr-smart-uhf"},
        "DECLARE_FEEDLINE": {"feedline_id": "direct"},
        "RETUNE": {"center_frequency_hz": 433_920_000.0},
        "CHANGE_GAIN": {"gain_db": 40.2},
        "CHANGE_SAMPLE_RATE": {"sample_rate_hz": 2_400_000.0},
    }

    def test_every_contract_has_a_satisfying_transition(self):
        self.assertEqual(set(self.MOVES), set(CONTRACTS))
        for name, move in self.MOVES.items():
            with self.subTest(transition=name):
                verdict = _check(name, CONTRACTS[name], configuration_epoch=8, **move)
                self.assertEqual(verdict.verdict, INVARIANTS_SATISFIED,
                                 [f.as_dict() for f in verdict.findings])

    def test_every_contract_requires_the_epoch_to_advance(self):
        for name, move in self.MOVES.items():
            with self.subTest(transition=name):
                verdict = _check(name, CONTRACTS[name], **move)
                self.assertEqual(verdict.verdict, REQUIRED_CHANGE_NOT_OBSERVED)
                self.assertIn("configuration_epoch",
                              [f.field for f in verdict.findings])

    def test_no_contract_permits_the_sensor_to_change(self):
        for name, move in self.MOVES.items():
            with self.subTest(transition=name):
                verdict = _check(name, CONTRACTS[name], configuration_epoch=8,
                                 sensor_id="SOME-OTHER-RECEIVER", **move)
                self.assertEqual(verdict.verdict, PROHIBITED_CHANGE)
                self.assertIn("sensor_id", [f.field for f in verdict.findings])

    def test_every_signature_field_is_governed_by_every_contract(self):
        """A coordinate no rule mentions would move without complaint."""
        for name, contract in CONTRACTS.items():
            with self.subTest(transition=name):
                declared = set(contract.declared_fields())
                ungoverned = set(SIGNATURE_FIELDS) - declared
                self.assertEqual(ungoverned, set(),
                                 f"{name} governs none of {sorted(ungoverned)}")


class ProhibitedClaimTests(unittest.TestCase):
    """Bookkeeping does not become measurement."""

    def test_no_declaration_may_assert_a_measured_resonance(self):
        for claim in NEVER_ESTABLISHED_BY_A_DECLARATION:
            with self.subTest(claim=claim):
                verdict = check_transition(
                    _sig(), "DECLARE_ANTENNA_EXTENSION",
                    _sig(extension_mm=368.0, configuration_epoch=8, claims=(claim,)),
                    DECLARE_ANTENNA_EXTENSION)
                self.assertEqual(verdict.verdict, PROHIBITED_CHANGE)
                self.assertEqual(verdict.findings[0].observed, claim)

    def test_every_transition_forbids_the_same_claims(self):
        for name, contract in CONTRACTS.items():
            self.assertEqual(set(contract.prohibited_claims),
                             set(NEVER_ESTABLISHED_BY_A_DECLARATION), name)

    def test_an_unstated_claim_set_is_missing_evidence(self):
        after = _sig(extension_mm=368.0, configuration_epoch=8)
        after["claims"] = Coordinate("NOT_ASSESSED")
        verdict = check_transition(_sig(), "DECLARE_ANTENNA_EXTENSION", after,
                                   DECLARE_ANTENNA_EXTENSION)
        self.assertEqual(verdict.verdict, EVIDENCE_MISSING)
        self.assertEqual(verdict.findings[0].field, "claims")


class ScopeTests(unittest.TestCase):
    def test_the_module_computes_and_mutates_nothing(self):
        import rf_product_transitions as module
        with open(inspect.getsourcefile(module), encoding="utf-8") as handle:
            source = handle.read()
        for forbidden in ("writebus", "GraphOp", "h3", "sqlite", "flask",
                          "socket", "subprocess", "os.environ", "time."):
            self.assertNotIn(forbidden, source, forbidden)
        self.assertFalse(transitions_status()["mutates"])
        self.assertEqual(transitions_status()["side_effects"], "NONE")

    def test_the_status_is_serialisable(self):
        json.dumps(transitions_status())

    def test_checks_are_pure_across_repeated_calls(self):
        first = _check("RETUNE", RETUNE, center_frequency_hz=433e6,
                       configuration_epoch=8).as_dict()
        for _ in range(5):
            self.assertEqual(_check("RETUNE", RETUNE, center_frequency_hz=433e6,
                                    configuration_epoch=8).as_dict(), first)


if __name__ == "__main__":
    unittest.main()
