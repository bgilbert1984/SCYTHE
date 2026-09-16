"""§5.23: what a corpus was built on, and what refuses when it is not declared."""

import json
import math
import random
import unittest

import test_scythe_verdict_vocabularies as vocab
from rf_promotion_envelope import (
    ACCEPTED_CONFIDENCE_LEVELS, AUTHORITIES_SUFFICIENT_FOR_PROMOTION,
    CONFIDENCE_MIXING_TERMINATION_AND_SECOND_TIME,
    CONFIDENCE_REFERENCE_TERMINATION_AND_SECOND_SITE,
    ENVELOPE_ADMITS_NOTHING, ENVELOPE_ABSENT, ENVELOPE_AUTHORITY_NOT_DECLARED,
    ENVELOPE_DECLARATION_COLLAPSED, ENVELOPE_DECLARATION_REPEATED,
    ENVELOPE_GEOMETRY_REFUSED, ENVELOPE_MULTIPLE_RECEIVERS,
    ENVELOPE_QUANTITY_NOT_FINITE,
    PLAN_ALLOCATION_INCOMPLETE, PLAN_ALLOCATION_OUTSIDE_ENVELOPE,
    PLAN_ABSENT, PLAN_QUANTITY_NOT_FINITE, PLAN_SCHEDULE_NOT_REPRODUCIBLE,
    PLAN_MINIMUM_SEPARATION, PLAN_TUNING_COUNT, PLAN_VISITS_PER_TUNING,
    RECEIVER_ATTESTED_UNIQUE, RECEIVER_IDENTITY_AUTHORITIES,
    RECEIVER_INSTANCE_UNIQUENESS_UNATTESTED, RECEIVER_OPERATOR_INSTANCE,
    SCHEDULE_GENERATOR_REVISION,
    CapturePlanDeclaration, ChainMember, EnvelopeRefused, FrontEnd,
    InstrumentChainEnvelope, Visit, VisitAllocation,
    declare_capture_plan, declare_instrument_chain_envelope,
    generate_visit_schedule, harmonic_cap,
)


def _front_end(antenna="ANT_A", extension_mm=0.0, feedline="RG316-1M",
               length=1.0):
    return FrontEnd(antenna=antenna, extension_mm=extension_mm,
                    feedline=feedline, feedline_length_m=length)


def _member(gain=20.0, antenna="ANT_A", sensor="rtl2838-unit-1",
            authority=RECEIVER_ATTESTED_UNIQUE, extension_mm=0.0):
    return ChainMember(
        sensor_id=sensor, receiver_identity_authority=authority,
        sample_type="cs8", sample_rate_hz=2_048_000.0,
        front_end=_front_end(antenna=antenna, extension_mm=extension_mm),
        gain_db=gain)


def _envelope(**kwargs):
    return InstrumentChainEnvelope(members=(
        _member(gain=20.0, antenna="ANT_A", **kwargs),
        _member(gain=40.0, antenna="TERMINATION_50R", **kwargs)))


def _allocations(envelope, seed=20260915):
    chains = sorted(envelope.admissible_chain_hashes())
    return tuple(
        VisitAllocation(
            position=visit.position,
            chain_hash=chains[visit.position % len(chains)],
            stratum="THERMAL_NO_INPUT", block=f"b{visit.position // 64}",
            condition="NOMINAL",
            confidence_requirement=CONFIDENCE_MIXING_TERMINATION_AND_SECOND_TIME)
        for visit in generate_visit_schedule(seed=seed))


def _plan(envelope=None, seed=20260915, **kwargs):
    envelope = _envelope() if envelope is None else envelope
    return declare_capture_plan(
        seed=seed, envelope=envelope,
        allocations=kwargs.pop("allocations", _allocations(envelope, seed)),
        reference_hz=kwargs.pop("reference_hz", 28_800_000.0),
        reference_ppm=kwargs.pop("reference_ppm", 1.0), **kwargs)


class NotACartesianProductTests(unittest.TestCase):
    """The correction that made this a set of members instead of two sets.

    Declaring the gains {20, 40} and the front ends {ANT_A, TERMINATION} and
    admitting their product would license two chains nobody sampled. This is the
    test that fails if anyone reintroduces the product.
    """

    def test_an_envelope_admits_exactly_what_was_written_down(self):
        envelope = _envelope()
        self.assertEqual(len(envelope.admissible_chain_hashes()), 2)

    def test_the_unsampled_combinations_are_not_admitted(self):
        envelope = _envelope()
        for unsampled in (_member(gain=40.0, antenna="ANT_A"),
                          _member(gain=20.0, antenna="TERMINATION_50R")):
            self.assertFalse(
                envelope.admits(unsampled.chain_hash()),
                "the product of two declared sets is not a declared set")

    def test_the_sampled_combinations_are_admitted(self):
        envelope = _envelope()
        for sampled in (_member(gain=20.0, antenna="ANT_A"),
                        _member(gain=40.0, antenna="TERMINATION_50R")):
            self.assertTrue(envelope.admits(sampled.chain_hash()))


class SelfValidationTests(unittest.TestCase):
    """Constructed directly, not through the factory.

    A test that exercises only `declare_instrument_chain_envelope` tests the
    door beside the open window: the dataclass is public and `type(x) is
    InstrumentChainEnvelope` passes for whatever it was handed.
    """

    def test_a_direct_construction_with_no_members_refuses(self):
        with self.assertRaises(EnvelopeRefused) as caught:
            InstrumentChainEnvelope(members=())
        self.assertEqual(caught.exception.code, ENVELOPE_ADMITS_NOTHING)

    def test_the_factory_refuses_the_same_thing(self):
        with self.assertRaises(EnvelopeRefused) as caught:
            declare_instrument_chain_envelope(members=[])
        self.assertEqual(caught.exception.code, ENVELOPE_ADMITS_NOTHING)

    def test_a_member_that_is_not_a_member_refuses(self):
        with self.assertRaises(EnvelopeRefused) as caught:
            InstrumentChainEnvelope(members=("not-a-chain",))
        self.assertEqual(caught.exception.code, ENVELOPE_ABSENT)

    def test_a_member_with_an_undeclared_authority_refuses(self):
        with self.assertRaises(EnvelopeRefused) as caught:
            _member(authority="TRUST_ME")
        self.assertEqual(caught.exception.code, ENVELOPE_AUTHORITY_NOT_DECLARED)

    def test_two_receivers_in_one_envelope_refuse(self):
        with self.assertRaises(EnvelopeRefused) as caught:
            InstrumentChainEnvelope(members=(
                _member(sensor="unit-1"), _member(sensor="unit-2")))
        self.assertEqual(caught.exception.code, ENVELOPE_MULTIPLE_RECEIVERS)

    def test_a_member_declared_twice_refuses(self):
        with self.assertRaises(EnvelopeRefused) as caught:
            InstrumentChainEnvelope(members=(_member(), _member()))
        self.assertEqual(caught.exception.code, ENVELOPE_DECLARATION_REPEATED)

    def test_two_declarations_that_collapse_to_one_chain_refuse(self):
        """A real collapse, not a contrived one.

        `isinstance(True, (int, float))` is True and `float(True)` is 1.0, so
        §13n O.3's preserved quirk makes `extension_mm=True` and
        `extension_mm=1.0` one chain written two ways. The operator learns that
        from a refusal rather than from a member count that quietly disagrees
        with the number of admissible hashes.
        """
        one = _member(extension_mm=1.0)
        other = _member(extension_mm=True)
        self.assertEqual(one.chain_hash(), other.chain_hash())
        self.assertNotEqual(one.sort_key(), other.sort_key())
        with self.assertRaises(EnvelopeRefused) as caught:
            InstrumentChainEnvelope(members=(one, other))
        self.assertEqual(caught.exception.code, ENVELOPE_DECLARATION_COLLAPSED)

    def test_a_geometry_deviation_refuses(self):
        with self.assertRaises(EnvelopeRefused) as caught:
            ChainMember(
                sensor_id="s", receiver_identity_authority=RECEIVER_ATTESTED_UNIQUE,
                sample_type="cs8", sample_rate_hz=1_024_000.0,
                front_end=_front_end(), gain_db=20.0)
        self.assertEqual(caught.exception.code, ENVELOPE_GEOMETRY_REFUSED)


class NonFiniteTests(unittest.TestCase):
    """NaN is not equal to itself, so an envelope holding one cannot reliably
    answer whether it admits its own declared member."""

    def test_a_nan_gain_refuses(self):
        with self.assertRaises(EnvelopeRefused) as caught:
            _member(gain=float("nan"))
        self.assertEqual(caught.exception.code, ENVELOPE_QUANTITY_NOT_FINITE)

    def test_an_infinite_gain_refuses(self):
        with self.assertRaises(EnvelopeRefused) as caught:
            _member(gain=float("inf"))
        self.assertEqual(caught.exception.code, ENVELOPE_QUANTITY_NOT_FINITE)

    def test_a_nan_feedline_length_refuses(self):
        with self.assertRaises(EnvelopeRefused) as caught:
            FrontEnd(antenna="A", extension_mm=0.0, feedline="F",
                     feedline_length_m=float("nan"))
        self.assertEqual(caught.exception.code, ENVELOPE_QUANTITY_NOT_FINITE)

    def test_a_nan_extension_refuses(self):
        with self.assertRaises(EnvelopeRefused) as caught:
            FrontEnd(antenna="A", extension_mm=float("nan"), feedline="F",
                     feedline_length_m=1.0)
        self.assertEqual(caught.exception.code, ENVELOPE_QUANTITY_NOT_FINITE)

    def test_an_undeclared_gain_is_not_a_non_finite_one(self):
        """`None` is an absence with a name, and the identity records it as one."""
        self.assertIsNone(_member(gain=None).gain_db)


class CanonicalizationTests(unittest.TestCase):

    def test_a_generator_of_members_is_not_consumed_by_validation(self):
        """Convert once, first. Validating an iterable and storing it afterwards
        stores what the checks already drained."""
        envelope = InstrumentChainEnvelope(members=(
            m for m in (_member(gain=20.0), _member(gain=40.0))))
        self.assertEqual(len(envelope.members), 2)
        self.assertEqual(len(envelope.admissible_chain_hashes()), 2)

    def test_reordering_the_members_does_not_change_the_digest(self):
        forward = InstrumentChainEnvelope(members=(
            _member(gain=20.0), _member(gain=40.0)))
        backward = InstrumentChainEnvelope(members=(
            _member(gain=40.0), _member(gain=20.0)))
        self.assertEqual(forward.digest(), backward.digest())
        self.assertEqual(forward.members, backward.members)

    def test_reordering_never_changed_what_was_admitted(self):
        """The defect was two identities for one envelope, not two envelopes."""
        forward = InstrumentChainEnvelope(members=(
            _member(gain=20.0), _member(gain=40.0)))
        backward = InstrumentChainEnvelope(members=(
            _member(gain=40.0), _member(gain=20.0)))
        self.assertEqual(forward.admissible_chain_hashes(),
                         backward.admissible_chain_hashes())

    def test_an_undeclared_gain_sorts_beside_a_declared_one(self):
        """`None < 1.0` raises, so the key makes the absence explicit. A sort
        that crashed on a legitimately undeclared gain would be worse than the
        unordered digest it was introduced to fix."""
        envelope = InstrumentChainEnvelope(members=(
            _member(gain=20.0), _member(gain=None)))
        self.assertEqual(len(envelope.members), 2)
        self.assertIsNone(envelope.members[0].gain_db)

    def test_the_digest_does_not_stringify_what_it_cannot_serialise(self):
        """`default=str` removed: an unanticipated value raises rather than
        being quietly widened into the digest as though it were declared."""
        odd = InstrumentChainEnvelope(members=(
            _member(extension_mm=object()),))
        with self.assertRaises(TypeError):
            odd.digest()

    def test_two_envelopes_over_different_receivers_differ(self):
        self.assertNotEqual(_envelope(sensor="unit-1").digest(),
                            _envelope(sensor="unit-2").digest())


class ReceiverIdentityAuthorityTests(unittest.TestCase):
    """§5.23's correction: a non-unique identifier is not a class-scoped claim."""

    def test_all_three_authorities_are_declared_in_one_tuple(self):
        """Load-bearing, not tidy. See the negation-pair test below."""
        self.assertEqual(len(RECEIVER_IDENTITY_AUTHORITIES), 3)
        for authority in (RECEIVER_ATTESTED_UNIQUE, RECEIVER_OPERATOR_INSTANCE,
                          RECEIVER_INSTANCE_UNIQUENESS_UNATTESTED):
            self.assertIn(authority, RECEIVER_IDENTITY_AUTHORITIES)

    def test_the_two_names_really_are_a_negation_pair(self):
        """Which is why they must share a set: the mechanical name check clears
        a negation pair only within one declared enumeration, on the ground
        that keeps GRAPH_RECORD_FOUND beside GRAPH_RECORD_NOT_FOUND."""
        self.assertTrue(vocab.is_negation_pair(
            RECEIVER_INSTANCE_UNIQUENESS_UNATTESTED, RECEIVER_ATTESTED_UNIQUE))

    def test_an_unattested_identity_does_not_promote(self):
        envelope = _envelope(authority=RECEIVER_INSTANCE_UNIQUENESS_UNATTESTED)
        self.assertFalse(envelope.may_be_promoted_from)

    def test_the_two_sufficient_authorities_do(self):
        for authority in (RECEIVER_ATTESTED_UNIQUE, RECEIVER_OPERATOR_INSTANCE):
            self.assertTrue(_envelope(authority=authority).may_be_promoted_from)
        self.assertEqual(len(AUTHORITIES_SUFFICIENT_FOR_PROMOTION), 2)

    def test_the_authority_is_carried_rather_than_recomputed(self):
        envelope = _envelope(authority=RECEIVER_INSTANCE_UNIQUENESS_UNATTESTED)
        self.assertEqual(envelope.receiver_identity_authority,
                         RECEIVER_INSTANCE_UNIQUENESS_UNATTESTED)
        self.assertEqual(envelope.to_dict()["receiver_identity_authority"],
                         RECEIVER_INSTANCE_UNIQUENESS_UNATTESTED)


class HarmonicCapTests(unittest.TestCase):
    """Derived from the declared tolerance, and checked against §5.22's table."""

    def test_the_three_published_rows_are_reproduced(self):
        for ppm, expected in ((100.0, 3), (10.0, 35), (1.0, 355)):
            self.assertEqual(
                harmonic_cap(reference_hz=28_800_000.0, reference_ppm=ppm),
                expected, f"§5.22's row for +/-{ppm} ppm")

    def test_an_uncalibrated_receiver_earns_almost_no_reference_matches(self):
        """The correct outcome rather than an inconvenience."""
        self.assertLess(
            harmonic_cap(reference_hz=28_800_000.0, reference_ppm=100.0), 5)

    def test_a_non_finite_tolerance_refuses(self):
        with self.assertRaises(EnvelopeRefused) as caught:
            harmonic_cap(reference_hz=28_800_000.0, reference_ppm=float("nan"))
        self.assertEqual(caught.exception.code, PLAN_QUANTITY_NOT_FINITE)

    def test_a_non_positive_reference_refuses(self):
        with self.assertRaises(EnvelopeRefused) as caught:
            harmonic_cap(reference_hz=0.0, reference_ppm=1.0)
        self.assertEqual(caught.exception.code, PLAN_QUANTITY_NOT_FINITE)


class ScheduleTests(unittest.TestCase):

    def test_the_same_seed_produces_the_same_schedule(self):
        self.assertEqual(generate_visit_schedule(seed=7),
                         generate_visit_schedule(seed=7))

    def test_a_different_seed_produces_a_different_schedule(self):
        self.assertNotEqual(generate_visit_schedule(seed=7),
                            generate_visit_schedule(seed=8))

    def test_the_schedule_does_not_depend_on_the_interpreter_rng(self):
        """Not `random.shuffle`. A byte-for-byte promise resting on the
        stability of CPython's RNG is a promise about CPython."""
        random.seed(1)
        first = generate_visit_schedule(seed=7)
        random.seed(999)
        [random.random() for _ in range(50)]
        self.assertEqual(generate_visit_schedule(seed=7), first)

    def test_every_tuning_is_visited_the_declared_number_of_times(self):
        schedule = generate_visit_schedule(seed=20260915)
        counts = {}
        for visit in schedule:
            counts[visit.tuning_index] = counts.get(visit.tuning_index, 0) + 1
        self.assertEqual(len(counts), PLAN_TUNING_COUNT)
        self.assertEqual(set(counts.values()), {PLAN_VISITS_PER_TUNING})

    def test_repeats_are_separated_by_the_declared_minimum(self):
        """A non-stationary emitter is caught between visits rather than fitted
        through them."""
        last = {}
        for visit in generate_visit_schedule(seed=20260915):
            previous = last.get(visit.tuning_index)
            if previous is not None:
                self.assertGreater(visit.position - previous,
                                   PLAN_MINIMUM_SEPARATION)
            last[visit.tuning_index] = visit.position

    def test_the_schedule_is_counterbalanced(self):
        """Every tuning in both halves, so retune position and elapsed time stay
        two dimensions rather than one."""
        schedule = generate_visit_schedule(seed=20260915)
        half = len(schedule) // 2
        first = {v.tuning_index for v in schedule[:half]}
        second = {v.tuning_index for v in schedule[half:]}
        self.assertEqual(first, set(range(PLAN_TUNING_COUNT)))
        self.assertEqual(second, set(range(PLAN_TUNING_COUNT)))

    def test_positions_are_carried_rather_than_implied_by_order(self):
        schedule = generate_visit_schedule(seed=3)
        self.assertEqual([v.position for v in schedule],
                         list(range(len(schedule))))

    def test_an_unknown_generator_revision_refuses(self):
        with self.assertRaises(EnvelopeRefused) as caught:
            generate_visit_schedule(seed=7, generator_revision="something.v9")
        self.assertEqual(caught.exception.code, PLAN_SCHEDULE_NOT_REPRODUCIBLE)

    def test_a_seed_that_is_not_an_integer_refuses(self):
        with self.assertRaises(EnvelopeRefused) as caught:
            generate_visit_schedule(seed="7")
        self.assertEqual(caught.exception.code, PLAN_QUANTITY_NOT_FINITE)


class CapturePlanTests(unittest.TestCase):

    def test_a_declared_plan_regenerates_to_itself(self):
        plan = _plan()
        self.assertEqual(plan.schedule, generate_visit_schedule(seed=plan.seed))

    def test_a_hand_written_schedule_refuses(self):
        """A seed is provenance. Holding both, and comparing byte for byte,
        makes a forged schedule and a drifted generator one failure."""
        envelope = _envelope()
        forged = tuple(Visit(position=i, tuning_index=i % PLAN_TUNING_COUNT)
                       for i in range(PLAN_TUNING_COUNT * PLAN_VISITS_PER_TUNING))
        with self.assertRaises(EnvelopeRefused) as caught:
            CapturePlanDeclaration(
                seed=20260915,
                schedule_generator_revision=SCHEDULE_GENERATOR_REVISION,
                schedule=forged, allocations=_allocations(envelope),
                reference_hz=28_800_000.0, reference_ppm=1.0)
        self.assertEqual(caught.exception.code, PLAN_SCHEDULE_NOT_REPRODUCIBLE)

    def test_one_reordered_visit_is_caught(self):
        """Byte for byte, not as a set: a schedule permuted after declaration is
        a different sequence in the same multiset."""
        envelope = _envelope()
        schedule = list(generate_visit_schedule(seed=20260915))
        schedule[0], schedule[-1] = schedule[-1], schedule[0]
        with self.assertRaises(EnvelopeRefused) as caught:
            CapturePlanDeclaration(
                seed=20260915,
                schedule_generator_revision=SCHEDULE_GENERATOR_REVISION,
                schedule=tuple(schedule), allocations=_allocations(envelope),
                reference_hz=28_800_000.0, reference_ppm=1.0)
        self.assertEqual(caught.exception.code, PLAN_SCHEDULE_NOT_REPRODUCIBLE)

    def test_a_missing_allocation_refuses(self):
        envelope = _envelope()
        with self.assertRaises(EnvelopeRefused) as caught:
            _plan(envelope=envelope,
                  allocations=_allocations(envelope)[:-1])
        self.assertEqual(caught.exception.code, PLAN_ALLOCATION_INCOMPLETE)

    def test_an_allocation_outside_the_envelope_refuses(self):
        envelope = _envelope()
        stray = _member(gain=33.0, antenna="ANT_B").chain_hash()
        allocations = list(_allocations(envelope))
        allocations[0] = VisitAllocation(
            position=0, chain_hash=stray, stratum="THERMAL_NO_INPUT",
            block="b0", condition="NOMINAL",
            confidence_requirement=CONFIDENCE_MIXING_TERMINATION_AND_SECOND_TIME)
        with self.assertRaises(EnvelopeRefused) as caught:
            _plan(envelope=envelope, allocations=tuple(allocations))
        self.assertEqual(caught.exception.code, PLAN_ALLOCATION_OUTSIDE_ENVELOPE)

    def test_an_unaccepted_confidence_level_refuses(self):
        with self.assertRaises(EnvelopeRefused) as caught:
            VisitAllocation(position=0, chain_hash="blake2s:x",
                            stratum="THERMAL_NO_INPUT", block="b0",
                            condition="NOMINAL",
                            confidence_requirement="I_AM_CONFIDENT")
        self.assertEqual(caught.exception.code, PLAN_ABSENT)

    def test_both_accepted_levels_are_accepted(self):
        for level in ACCEPTED_CONFIDENCE_LEVELS:
            VisitAllocation(position=0, chain_hash="blake2s:x",
                            stratum="THERMAL_NO_INPUT", block="b0",
                            condition="NOMINAL", confidence_requirement=level)
        self.assertEqual(len(ACCEPTED_CONFIDENCE_LEVELS), 2)
        self.assertIn(CONFIDENCE_REFERENCE_TERMINATION_AND_SECOND_SITE,
                      ACCEPTED_CONFIDENCE_LEVELS)

    def test_the_plan_carries_the_derived_harmonic_cap(self):
        self.assertEqual(_plan(reference_ppm=100.0).harmonic_cap, 3)
        self.assertEqual(_plan(reference_ppm=1.0).harmonic_cap, 355)

    def test_the_plan_digest_moves_with_the_declared_tolerance(self):
        self.assertNotEqual(_plan(reference_ppm=1.0).digest(),
                            _plan(reference_ppm=100.0).digest())

    def test_a_plan_declared_against_a_non_envelope_refuses(self):
        with self.assertRaises(EnvelopeRefused) as caught:
            declare_capture_plan(seed=1, envelope="an envelope, honestly",
                                 allocations=(), reference_hz=28_800_000.0,
                                 reference_ppm=1.0)
        self.assertEqual(caught.exception.code, ENVELOPE_ABSENT)

    def test_the_plan_serialises_without_stringifying(self):
        json.dumps(_plan().to_dict())


class WhatThisModuleDoesNotDoTests(unittest.TestCase):
    """The claims in the docstring, as checks rather than as prose."""

    def test_the_module_opens_no_device_and_writes_nothing(self):
        import rf_promotion_envelope as module
        with open(module.__file__, encoding="utf-8") as handle:
            source = handle.read()
        for forbidden in ("rtl_tcp", "socket", "subprocess", "open(",
                          "Thread", "mkdir"):
            self.assertNotIn(forbidden, source,
                             f"{forbidden} has no business in a pure declaration")

    def test_captured_window_admission_is_absent_rather_than_stubbed(self):
        """§5.23 assigns it to the persistence slice. A stub that always
        returned True would be the inert admission entry 11 refuses."""
        import rf_promotion_envelope as module
        self.assertFalse(hasattr(module, "admit_captured_window"))


if __name__ == "__main__":
    unittest.main()
