"""The receiver-state chain, and the three ways it refuses to help.

The claims under test are not about accuracy. They are about what may be
asserted from what:

1.  Course is never heading. At 1.1 m/s the two decouple entirely, and the only
    thing stopping a body-shadow experiment from consuming a GNSS course is that
    the constructor refuses to put one in a heading field.
2.  Staleness is metres, not seconds. The same 42 ms is negligible on foot and
    material in a vehicle, and a timestamp cutoff cannot tell the difference.
3.  A posterior is fed through one edge, and that edge can refuse.
"""

import json
import math
import unittest

from rf_receiver_state import (
    ALIGNMENT_CAPABILITIES, DEFAULT_MOUNT_UNCERTAINTY_M, JOIN_REFUSALS,
    build_receiver_state, canonical_bytes, may_update_posterior,
    motion_uncertainty_m, pose_uncertainty_m, receiver_state_chain_hash,
    receiver_state_chain_manifest, receiver_state_status, time_align,
)


def _state(**overrides):
    kwargs = dict(device_id="phone-1", latitude=29.735, longitude=-94.977,
                  horizontal_accuracy_m=4.8, position_authority="DEVICE_GNSS",
                  speed_mps=1.1, course_deg=72.0, course_source="GNSS_COURSE",
                  device_timestamp=1000.0, orchestrator_timestamp=1000.018,
                  offset_estimate_ms=18.2, alignment_uncertainty_ms=42.0,
                  alignment_method="BOUNDED_CLOCK_EXCHANGE")
    kwargs.update(overrides)
    return build_receiver_state(**kwargs)


class SeparationTests(unittest.TestCase):
    """Two chains, because they answer different questions and fail apart."""

    def test_the_receiver_chain_is_not_the_signal_chain(self):
        self.assertFalse(receiver_state_status()["folded_into_signal_chain"])

    def test_a_new_fix_does_not_change_the_chain(self):
        """A chain identity that moved with every fix would make every state an
        incomparable island -- the same reason the signal chain excludes centre
        frequency."""
        first = _state()
        moved = _state(latitude=29.740, longitude=-94.980, device_timestamp=1010.0)
        self.assertEqual(first.receiver_state_chain_hash,
                         moved.receiver_state_chain_hash)
        # The state itself is still a different state.
        self.assertNotEqual(first.receiver_state_id, moved.receiver_state_id)

    def test_changing_the_apparatus_does_change_the_chain(self):
        """A different position source means a position means something else."""
        self.assertNotEqual(_state().receiver_state_chain_hash,
                            _state(position_authority="DEVICE_FUSED"
                                   ).receiver_state_chain_hash)
        self.assertNotEqual(_state().receiver_state_chain_hash,
                            _state(mount_uncertainty_m=0.1
                                   ).receiver_state_chain_hash)

    def test_the_manifest_hashes_the_same_however_it_is_ordered(self):
        manifest = receiver_state_chain_manifest(
            device_id="d", position_authority="DEVICE_GNSS",
            course_source="GNSS_COURSE", heading_source="UNDECLARED",
            alignment_method="BOUNDED_CLOCK_EXCHANGE")
        reordered = dict(reversed(list(manifest.items())))
        self.assertEqual(canonical_bytes(manifest), canonical_bytes(reordered))
        self.assertEqual(receiver_state_chain_hash(manifest),
                         receiver_state_chain_hash(reordered))


class CourseIsNotHeadingTests(unittest.TestCase):
    """The most expensive available mistake in this module."""

    def test_a_gnss_course_never_becomes_a_heading(self):
        state = _state()
        self.assertEqual(state.course_deg, 72.0)
        self.assertEqual(state.course_source, "GNSS_COURSE")
        self.assertIsNone(state.heading_deg)
        self.assertEqual(state.heading_source, "UNDECLARED")

    def test_a_heading_without_an_orientation_source_is_discarded(self):
        """Supplying a number does not make it a measurement of pointing."""
        state = _state(heading_deg=124.0, heading_source="UNDECLARED")
        self.assertIsNone(state.heading_deg)
        self.assertIsNone(state.heading_accuracy_deg)

    def test_a_magnetometer_heading_is_kept_and_named(self):
        state = _state(heading_deg=124.0, heading_source="DEVICE_MAGNETOMETER",
                       heading_accuracy_deg=15.0)
        self.assertEqual(state.heading_deg, 124.0)
        self.assertEqual(state.heading_source, "DEVICE_MAGNETOMETER")

    def test_an_unrecognised_source_is_undeclared_rather_than_carried(self):
        """A vocabulary that accepts anything is not a vocabulary: a consumer
        matching on DEVICE_GNSS would silently exclude a typo, not refuse it."""
        self.assertEqual(_state(position_authority="gps").position_authority,
                         "UNDECLARED")
        self.assertEqual(_state(course_source="compass").course_source, "UNDECLARED")


class PoseBudgetTests(unittest.TestCase):
    """Metres, because that is the only unit both cases share."""

    def test_the_motion_term_is_speed_times_time(self):
        self.assertAlmostEqual(motion_uncertainty_m(1.1, 42.0), 0.0462, places=4)
        self.assertAlmostEqual(motion_uncertainty_m(20.0, 42.0), 0.84, places=4)

    def test_the_budget_combines_three_independent_terms(self):
        budget = pose_uncertainty_m(position_accuracy_m=4.8, speed_mps=1.1,
                                    alignment_uncertainty_ms=42.0,
                                    mount_uncertainty_m=2.0)
        self.assertAlmostEqual(budget, math.sqrt(4.8 ** 2 + 0.0462 ** 2 + 2.0 ** 2),
                               places=4)

    def test_timing_is_negligible_on_foot_and_material_in_a_vehicle(self):
        """One rule covers both because it is expressed in metres."""
        walking = pose_uncertainty_m(position_accuracy_m=4.8, speed_mps=1.1,
                                     alignment_uncertainty_ms=42.0,
                                     mount_uncertainty_m=0.0)
        driving = pose_uncertainty_m(position_accuracy_m=4.8, speed_mps=20.0,
                                     alignment_uncertainty_ms=42.0,
                                     mount_uncertainty_m=0.0)
        self.assertLess(walking - 4.8, 0.001)
        self.assertGreater(driving - 4.8, 0.05)

    def test_a_missing_position_yields_no_budget_rather_than_a_default(self):
        """Substituting a default for an absent GNSS circle would publish a
        confident number about a position nobody supplied."""
        self.assertIsNone(pose_uncertainty_m(position_accuracy_m=None, speed_mps=1.1,
                                             alignment_uncertainty_ms=42.0))
        self.assertIsNone(_state(latitude=None, longitude=None).pose_uncertainty_m)

    def test_the_undeclared_mount_is_in_the_budget_not_forgotten(self):
        """The antenna is on a 2 m base and its relationship to the operator is
        undeclared. That unknown is carried, not assumed away."""
        self.assertEqual(DEFAULT_MOUNT_UNCERTAINTY_M, 2.0)
        self.assertGreater(_state().pose_uncertainty_m, 4.8)


class AlignmentStateTests(unittest.TestCase):
    """Four states, and what each is allowed to contribute."""

    def test_a_bounded_exchange_at_walking_speed_is_bounded(self):
        self.assertEqual(_state().alignment_status, "BOUNDED")

    def test_a_shared_clock_is_verified(self):
        self.assertEqual(_state(alignment_method="SHARED_MONOTONIC_SOURCE",
                                alignment_uncertainty_ms=0.0).alignment_status,
                         "VERIFIED")

    def test_a_tight_exchange_is_verified(self):
        self.assertEqual(_state(alignment_uncertainty_ms=3.0).alignment_status,
                         "VERIFIED")

    def test_trusting_a_device_clock_is_not_an_alignment(self):
        """Whatever number accompanies it."""
        self.assertEqual(_state(alignment_method="DEVICE_TIMESTAMP_TRUSTED",
                                alignment_uncertainty_ms=1.0).alignment_status,
                         "UNVERIFIED")

    def test_no_alignment_attempt_is_unverified(self):
        self.assertEqual(_state(alignment_method="NOT_ATTEMPTED").alignment_status,
                         "UNVERIFIED")

    def test_staleness_is_reached_by_speed_not_by_delay(self):
        """The same uncertainty is bounded on foot and stale in a vehicle.

        At 1.1 m/s, 5 s of timing uncertainty moves the receiver 5.5 m, just past
        a 4.8 m circle. At 30 m/s it takes 160 ms. A seconds-based cutoff would
        have to pick one and be wrong for the other.
        """
        self.assertEqual(_state(alignment_uncertainty_ms=5_000.0).alignment_status,
                         "STALE")
        self.assertEqual(_state(speed_mps=30.0,
                                alignment_uncertainty_ms=200.0).alignment_status,
                         "STALE")
        self.assertEqual(_state(speed_mps=30.0,
                                alignment_uncertainty_ms=100.0).alignment_status,
                         "BOUNDED")

    def test_a_stationary_receiver_does_not_go_stale_from_timing_alone(self):
        """Standing still, a late timestamp costs no position at all."""
        self.assertEqual(_state(speed_mps=0.0,
                                alignment_uncertainty_ms=30_000.0).alignment_status,
                         "BOUNDED")

    def test_every_state_permits_breadcrumbs(self):
        """Showing where the operator walked is a record of the survey, not an
        inference about an emitter."""
        for state, capability in ALIGNMENT_CAPABILITIES.items():
            self.assertTrue(capability["breadcrumbs"], state)

    def test_only_verified_and_bounded_may_update_a_surface(self):
        self.assertTrue(ALIGNMENT_CAPABILITIES["VERIFIED"]["heatmap_update"])
        self.assertTrue(ALIGNMENT_CAPABILITIES["BOUNDED"]["heatmap_update"])
        self.assertFalse(ALIGNMENT_CAPABILITIES["UNVERIFIED"]["heatmap_update"])
        self.assertFalse(ALIGNMENT_CAPABILITIES["STALE"]["heatmap_update"])

    def test_bearing_like_evidence_is_conditional_under_a_bounded_join(self):
        """Time alignment does not supply a verified heading source, so it
        cannot on its own authorise directional evidence."""
        self.assertEqual(ALIGNMENT_CAPABILITIES["BOUNDED"]["bearing_like_evidence"],
                         "CONDITIONAL")
        self.assertTrue(ALIGNMENT_CAPABILITIES["VERIFIED"]["bearing_like_evidence"])


class JoinTests(unittest.TestCase):
    """A survey point enters a posterior through one edge, and it can refuse."""

    def _observation(self, **overrides):
        payload = {"signal_chain_hash": "blake2s:abc", "frequency_hz": 433_920_000.0}
        payload.update(overrides)
        return payload

    def test_a_bounded_join_carries_what_a_consumer_must_propagate(self):
        join = time_align(self._observation(), _state())
        self.assertTrue(join.joined)
        self.assertEqual(join.method, "BOUNDED_CLOCK_EXCHANGE")
        self.assertEqual(join.uncertainty_ms, 42.0)
        self.assertIsNotNone(join.pose_uncertainty_m)
        self.assertTrue(may_update_posterior(join))

    def test_a_stale_join_may_not_reach_the_posterior(self):
        join = time_align(self._observation(),
                          _state(alignment_uncertainty_ms=5_000.0))
        self.assertTrue(join.joined)
        self.assertEqual(join.alignment_status, "STALE")
        self.assertFalse(may_update_posterior(join))

    def test_an_unattempted_alignment_is_refused_with_a_reason(self):
        join = time_align(self._observation(),
                          _state(alignment_method="NOT_ATTEMPTED"))
        self.assertFalse(join.joined)
        self.assertEqual(join.refusal, "ALIGNMENT_NOT_ATTEMPTED")
        self.assertFalse(may_update_posterior(join))

    def test_an_unbounded_alignment_is_refused_rather_than_assumed_tight(self):
        join = time_align(self._observation(),
                          _state(alignment_uncertainty_ms=None))
        self.assertEqual(join.refusal, "ALIGNMENT_UNBOUNDED")

    def test_a_state_without_a_position_is_refused(self):
        join = time_align(self._observation(),
                          _state(latitude=None, longitude=None))
        self.assertEqual(join.refusal, "NO_POSITION")

    def test_a_missing_state_is_refused_rather_than_defaulted(self):
        self.assertEqual(time_align(self._observation(), None).refusal,
                         "NO_RECEIVER_STATE")

    def test_a_changed_rf_chain_breaks_the_join(self):
        join = time_align(
            self._observation(expected_signal_chain_hash="blake2s:other"), _state())
        self.assertEqual(join.refusal, "SIGNAL_CHAIN_CHANGED")

    def test_a_changed_receiver_chain_breaks_the_join(self):
        join = time_align(self._observation(), _state(),
                          expected_receiver_state_chain_hash="blake2s:stale")
        self.assertEqual(join.refusal, "RECEIVER_STATE_CHAIN_CHANGED")

    def test_every_refusal_reaches_the_payload_as_prose(self):
        """A refusal code with no text is a code the operator has to look up."""
        for refusal, text in JOIN_REFUSALS.items():
            self.assertTrue(text.strip(), refusal)
            self.assertNotEqual(text, refusal, refusal)
        join = time_align(self._observation(), None)
        self.assertEqual(join.refusal, "NO_RECEIVER_STATE")
        self.assertIn("NO RECEIVER STATE", join.to_dict()["reason"])

    def test_a_successful_join_carries_no_refusal_text(self):
        self.assertIsNone(time_align(self._observation(), _state()).to_dict()["reason"])


class ContractTests(unittest.TestCase):
    """What is declared, and what is declared absent."""

    def test_nothing_downstream_is_claimed_to_exist(self):
        status = receiver_state_status()
        self.assertEqual(status["state"], "CONTRACT_ONLY_NO_COLLECTION_IMPLEMENTED")
        for absent in ("collection_implemented", "posterior_implemented",
                       "planner_implemented", "body_shadow_implemented"):
            self.assertFalse(status[absent], absent)

    def test_the_state_is_json_serialisable_and_holds_no_samples(self):
        payload = _state().to_dict()
        json.dumps(payload)
        self.assertNotIn("samples", payload)
        self.assertEqual(set(payload["position"]) & {"latitude", "authority"},
                         {"latitude", "authority"})

    def test_staleness_basis_is_published_as_distance(self):
        self.assertEqual(receiver_state_status()["staleness_basis"],
                         "METRES_OF_POSSIBLE_MOVEMENT_NOT_SECONDS")


if __name__ == "__main__":
    unittest.main()
