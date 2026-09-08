"""Conservation of evidentiary continuity for a moving receiver."""

import inspect
import itertools
import json
import unittest

from rf_walk_transitions import (
    CONTRACTS, DISPLACEMENT_SCOPE, SIGNATURE_FIELDS, UNDECLARED_MAX_SPEED_MPS,
    WALK_STEP, WALK_STEP_WITH_RECONFIGURATION, check_walk_step, great_circle_m,
    kinematic_budget_m, surface_contract, transitions_status, walk_signature,
    with_step_measures,
)
from scythe_invariant_ledger import (
    COMPARISON_DOMAIN_CHANGED, EVIDENCE_MISSING, INVARIANTS_SATISFIED,
    NUMERIC_BALANCE_EXCEEDED, PROHIBITED_CHANGE, REQUIRED_CHANGE_NOT_OBSERVED,
    Coordinate,
)

SECOND = 1_000_000_000
START_NS = 1_000 * SECOND
LAT, LON = 47.8000, -122.3000


def _end(lat=LAT, lon=LON, ns=START_NS, epoch=7, clock="phone-boot-1",
         chain="blake2s:aaa", rows=0, receiver="blake2s:rs", device="pixel"):
    return walk_signature(
        device_id=device, receiver_state_chain_hash=receiver,
        monotonic_source_id=clock, signal_chain_hash=chain,
        configuration_epoch=epoch, latitude=lat, longitude=lon,
        observed_monotonic_ns=ns, pose_uncertainty_m=5.0,
        surface_rows_contributed=rows)


def _step(seconds=10.0, speed=1.3, pose=5.0, **after):
    before = _end()
    end = _end(ns=START_NS + int(seconds * SECOND), **after)
    return with_step_measures(before, end, speed_before_mps=speed,
                              speed_after_mps=speed,
                              pose_before_m=pose, pose_after_m=pose)


class DisplacementTests(unittest.TestCase):
    """A bound, not an accusation."""

    def test_a_walking_pace_step_satisfies(self):
        before, after = _step(lat=47.8001)          # ~11 m
        self.assertEqual(check_walk_step(before, after).verdict,
                         INVARIANTS_SATISFIED)

    def test_standing_still_is_not_a_finding(self):
        """A ceiling is one-sided; a balance here would fault a stationary survey."""
        before, after = _step()
        self.assertEqual(after["displacement_m"].value, 0.0)
        self.assertEqual(check_walk_step(before, after).verdict,
                         INVARIANTS_SATISFIED)

    def test_eleven_kilometres_in_ten_seconds_at_a_walk_exceeds_the_bound(self):
        before, after = _step(lat=47.9000)
        verdict = check_walk_step(before, after)
        self.assertEqual(verdict.verdict, NUMERIC_BALANCE_EXCEEDED)
        self.assertEqual(verdict.findings[0].field, "displacement_m")

    def test_the_same_displacement_at_vehicle_speed_does_not(self):
        """The bound is against declared speed, not against a fixed distance."""
        before, after = _step(seconds=600.0, speed=30.0, lat=47.9000)
        self.assertEqual(check_walk_step(before, after).verdict,
                         INVARIANTS_SATISFIED)

    def test_pose_uncertainty_at_both_ends_is_in_the_budget(self):
        tight = _step(lat=47.80022, pose=0.0)
        loose = _step(lat=47.80022, pose=20.0)
        self.assertEqual(check_walk_step(*tight).verdict, NUMERIC_BALANCE_EXCEEDED)
        self.assertEqual(check_walk_step(*loose).verdict, INVARIANTS_SATISFIED)

    def test_the_faster_end_sets_the_budget(self):
        """A step that ended in a vehicle was a vehicle for part of it."""
        self.assertEqual(kinematic_budget_m(10.0, 1.3, 30.0), 300.0)
        self.assertEqual(kinematic_budget_m(10.0, 30.0, 1.3), 300.0)

    def test_an_undeclared_speed_assumes_a_generous_bound(self):
        """Assuming a walking pace would manufacture violations from an omission."""
        self.assertEqual(kinematic_budget_m(10.0, None, None),
                         UNDECLARED_MAX_SPEED_MPS * 10.0)
        self.assertGreater(UNDECLARED_MAX_SPEED_MPS, 10.0)

    def test_the_finding_does_not_claim_spoofing(self):
        status = transitions_status()
        self.assertEqual(status["displacement_finding_name"],
                         "DISPLACEMENT_EXCEEDS_DECLARED_BOUNDS")
        self.assertEqual(status["renamed_from"], "IMPOSSIBLE_DISPLACEMENT")
        self.assertIn("DOES NOT ESTABLISH SPOOFING", DISPLACEMENT_SCOPE)
        for word in ("SPOOF", "ATTACK", "ADVERSAR", "FRAUD"):
            self.assertNotIn(word, status["displacement_finding_name"])

    def test_the_great_circle_is_a_distance_not_a_degree_difference(self):
        self.assertAlmostEqual(great_circle_m(0.0, 0.0, 0.0, 1.0), 111_195.0, delta=200)
        self.assertAlmostEqual(great_circle_m(LAT, LON, LAT, LON), 0.0, places=6)


class ClockDomainTests(unittest.TestCase):
    """Two monotonic clocks are not comparable by both being monotonic."""

    def test_a_changed_clock_suppresses_every_other_finding(self):
        before, after = _step(clock="a-different-boot", lat=47.9000)
        verdict = check_walk_step(before, after)
        self.assertEqual(verdict.verdict, COMPARISON_DOMAIN_CHANGED)
        self.assertEqual([f.field for f in verdict.findings],
                         ["monotonic_source_id"])

    def test_no_displacement_is_computed_across_domains(self):
        """Rather than a number derived from incomparable instants."""
        before, after = _step(clock="a-different-boot", lat=47.9000)
        self.assertEqual(after["displacement_m"].kind, "ABSENT")
        self.assertEqual(after["kinematic_budget_m"].kind, "ABSENT")


class ApparatusTests(unittest.TestCase):
    """The apparatus is what a step conserves."""

    def test_a_moved_signal_chain_is_prohibited_in_a_plain_step(self):
        before, after = _step(chain="blake2s:bbb", lat=47.8001)
        verdict = check_walk_step(before, after)
        self.assertEqual(verdict.verdict, PROHIBITED_CHANGE)
        self.assertEqual([f.field for f in verdict.findings], ["signal_chain_hash"])

    def test_an_epoch_that_moved_without_a_declared_cause_is_prohibited(self):
        before, after = _step(epoch=8, lat=47.8001)
        verdict = check_walk_step(before, after)
        self.assertEqual(verdict.verdict, PROHIBITED_CHANGE)
        self.assertEqual([f.field for f in verdict.findings], ["configuration_epoch"])

    def test_a_moved_receiver_chain_is_prohibited(self):
        before, after = _step(receiver="blake2s:other", lat=47.8001)
        verdict = check_walk_step(before, after)
        self.assertEqual(verdict.verdict, PROHIBITED_CHANGE)
        self.assertIn("receiver_state_chain_hash",
                      [f.field for f in verdict.findings])

    def test_a_reconfiguration_requires_both_the_chain_and_the_epoch(self):
        before, after = _step(chain="blake2s:bbb", epoch=8, lat=47.8001)
        self.assertEqual(
            check_walk_step(before, after, reconfigured=True).verdict,
            INVARIANTS_SATISFIED)

    def test_a_reconfiguration_that_left_the_epoch_behind_is_caught(self):
        before, after = _step(chain="blake2s:bbb", lat=47.8001)
        verdict = check_walk_step(before, after, reconfigured=True)
        self.assertEqual(verdict.verdict, REQUIRED_CHANGE_NOT_OBSERVED)
        self.assertEqual([f.field for f in verdict.findings], ["configuration_epoch"])

    def test_a_reconfiguration_that_did_not_reconfigure_is_caught(self):
        before, after = _step(epoch=8, lat=47.8001)
        verdict = check_walk_step(before, after, reconfigured=True)
        self.assertEqual(verdict.verdict, REQUIRED_CHANGE_NOT_OBSERVED)
        self.assertEqual([f.field for f in verdict.findings], ["signal_chain_hash"])


class SurfaceEligibilityTests(unittest.TestCase):
    """Contributing without an admitting join is unrepresentable, not discouraged."""

    def test_a_contribution_behind_an_admitting_join_is_permitted(self):
        before, after = _step(rows=3, lat=47.8001)
        self.assertEqual(check_walk_step(before, after, join_admits=True).verdict,
                         INVARIANTS_SATISFIED)

    def test_a_contribution_without_one_is_a_prohibited_change(self):
        before, after = _step(rows=3, lat=47.8001)
        verdict = check_walk_step(before, after, join_admits=False)
        self.assertEqual(verdict.verdict, PROHIBITED_CHANGE)
        self.assertEqual([f.field for f in verdict.findings],
                         ["surface_rows_contributed"])

    def test_a_refusing_join_still_permits_the_walk_itself(self):
        """Breadcrumbs are not gated. Only the surface is."""
        before, after = _step(lat=47.8001)
        self.assertEqual(check_walk_step(before, after, join_admits=False).verdict,
                         INVARIANTS_SATISFIED)

    def test_the_rule_is_which_contract_applies_not_a_coordinate(self):
        self.assertIn("surface_rows_contributed",
                      surface_contract(False).must_preserve)
        self.assertNotIn("surface_rows_contributed",
                         surface_contract(True).must_preserve)


class CoverageTests(unittest.TestCase):
    def test_every_signature_field_is_governed_by_every_contract(self):
        for name, contract in CONTRACTS.items():
            with self.subTest(transition=name):
                ungoverned = set(SIGNATURE_FIELDS) - set(contract.declared_fields())
                self.assertEqual(ungoverned, set(),
                                 f"{name} governs none of {sorted(ungoverned)}")

    def test_the_refusing_surface_contract_governs_everything_too(self):
        ungoverned = set(SIGNATURE_FIELDS) - set(
            surface_contract(False).declared_fields())
        self.assertEqual(ungoverned, set())

    def test_no_contract_permits_the_device_to_change(self):
        before, after = _step(device="another-phone", lat=47.8001)
        self.assertEqual(check_walk_step(before, after).verdict, PROHIBITED_CHANGE)

    def test_the_conserved_table_names_position_as_permitted_to_change(self):
        conserved = transitions_status()["conserved"]
        self.assertEqual(conserved["position"], "PERMITTED_TO_CHANGE")
        self.assertEqual(conserved["monotonic source identity"], "COMPARISON_DOMAIN")
        self.assertEqual(conserved["surface contribution"],
                         "PERMITTED_ONLY_BEHIND_AN_ADMITTING_JOIN")


class ScopeTests(unittest.TestCase):
    def test_the_module_mutates_nothing(self):
        import rf_walk_transitions as module
        with open(inspect.getsourcefile(module), encoding="utf-8") as handle:
            source = handle.read()
        for forbidden in ("writebus", "GraphOp", "h3", "sqlite", "flask",
                          "socket", "subprocess", "os.environ", "time."):
            self.assertNotIn(forbidden, source, forbidden)
        self.assertFalse(transitions_status()["mutates"])

    def test_the_status_is_serialisable(self):
        json.dumps(transitions_status())

    def test_checks_are_pure_across_repeated_calls(self):
        before, after = _step(lat=47.8001)
        first = check_walk_step(before, after).as_dict()
        for _ in range(5):
            self.assertEqual(check_walk_step(before, after).as_dict(), first)


if __name__ == "__main__":
    unittest.main()
