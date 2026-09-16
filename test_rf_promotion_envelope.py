"""§5.23: what a corpus was built on, and what refuses when it is not declared."""

import json
import random
import unittest

import test_scythe_verdict_vocabularies as vocab
from rf_promotion_envelope import (
    ACCEPTED_CONFIDENCE_LEVELS, AUTHORITIES_SUFFICIENT_FOR_PROMOTION, CAPTURED,
    CONFIDENCE_MIXING_TERMINATION_AND_SECOND_TIME,
    CONFIDENCE_REFERENCE_TERMINATION_AND_SECOND_SITE,
    CONSISTENT_WITH_INTERNAL_MIXING, CONSISTENT_WITH_INTERNAL_REFERENCE,
    ENVELOPE_ADMITS_NOTHING, ENVELOPE_ABSENT, ENVELOPE_AUTHORITY_NOT_DECLARED,
    ENVELOPE_DECLARATION_COLLAPSED, ENVELOPE_DECLARATION_REPEATED,
    ENVELOPE_EXTENSION_NOT_CANONICAL, ENVELOPE_GEOMETRY_REFUSED,
    ENVELOPE_MULTIPLE_RECEIVERS, ENVELOPE_QUANTITY_NOT_FINITE,
    ENVELOPE_RECEIVER_SETTING_VARIES,
    PLAN_ABSENT, PLAN_ALLOCATION_OUTSIDE_ENVELOPE, PLAN_BAND_UNUSABLE,
    PLAN_MINIMUM_SEPARATION, PLAN_QUANTITY_NOT_FINITE,
    PLAN_RETUNE_NOT_DECLARED, PLAN_SCHEDULE_NOT_REPRODUCIBLE,
    PLAN_SPUR_ALLOCATION_UNDECLARED, PLAN_SPUR_CATALOGUE_ABSENT,
    PLAN_SPUR_NOT_FEASIBLE, PLAN_TRIALS_DO_NOT_RECONCILE,
    PLAN_TUNING_COUNT, PLAN_TUNING_OUTSIDE_BAND, PLAN_VISITS_PER_TUNING,
    POWER_CYCLE_STABLE, RECEIVER_ATTESTED_UNIQUE, RECEIVER_IDENTITY_AUTHORITIES,
    RECEIVER_INSTANCE_UNIQUENESS_UNATTESTED, RECEIVER_OPERATOR_INSTANCE,
    RECONNECT_STABLE, SCHEDULE_GENERATOR_REVISION, SESSION_SCOPED,
    SPUR_CANDIDATE_UNRESOLVED, SYNTHETIC, VANISHES_ON_DECLARED_TERMINATION,
    PLAN_REPEATS_NOT_OBSERVED, PLAN_SPUR_NOT_PERSISTENT,
    PLAN_TRIAL_NOT_ELIGIBLE, PLAN_TRIAL_IDENTITY_AMBIGUOUS,
    PLAN_PERSISTENCE_MARGIN_DB,
    PLAN_PERSISTENCE_REQUIRED, PLAN_REPEATS_PER_TUNING,
    Band, CapturePlanDeclaration, CataloguedSpur, ChainMember,
    EligibleSpurTrial, EnvelopeRefused,
    FrontEnd, InstrumentChainEnvelope, SpurAllocation,
    SpurPersistenceObservation, StratumTrialPlan, Tuning,
    Visit, declare_capture_plan, declare_instrument_chain_envelope,
    deltas_over_ceiling, retune_delta_ceiling_hz,
    generate_tunings, generate_visit_schedule,
    harmonic_cap,
    select_spur_trials, _canonical_bytes, SELECTION_REVISION,
    PLAN_SELECTION_NOT_REPRODUCIBLE,
    maximum_retune_delta_hz, usable_half_span_hz,
)
from rf_validation_manifest import (
    MINIMUM_WINDOWS_PER_STRATUM, STRATUM_KEYS, TUNER_REQUIRED,
)

SEED = 20260915


def _front_end(antenna="ANT_A", extension_mm=0.0, feedline="RG316-1M",
               length=1.0):
    return FrontEnd(antenna=antenna, extension_mm=extension_mm,
                    feedline=feedline, feedline_length_m=length)


def _member(gain=20.0, antenna="ANT_A", sensor="rtl2838-unit-1",
            authority=RECEIVER_ATTESTED_UNIQUE, extension_mm=0.0,
            sample_type="cs8"):
    return ChainMember(
        sensor_id=sensor, receiver_identity_authority=authority,
        sample_type=sample_type, sample_rate_hz=2_048_000.0,
        front_end=_front_end(antenna=antenna, extension_mm=extension_mm),
        gain_db=gain)


def _envelope(**kwargs):
    """Two written-down members. Not the product of two declared sets."""
    return InstrumentChainEnvelope(members=(
        _member(gain=20.0, antenna="ANT_A", **kwargs),
        _member(gain=40.0, antenna="TERMINATION_50R", **kwargs)))


def _bands():
    return (Band(band_id="UHF_LOW", low_hz=400_000_000.0, high_hz=440_000_000.0),
            Band(band_id="UHF_HIGH", low_hz=440_000_000.0, high_hz=470_000_000.0))


def _spread(total, positions):
    """`total` windows across `positions` visits, deterministically and exactly."""
    base, extra = divmod(total, len(positions))
    return tuple((position, base + (1 if index < extra else 0))
                 for index, position in enumerate(positions))


def _persistence(tuning_id="tuning-000", qualifying=8):
    """Eight repeats, `qualifying` of them at or above the declared margin."""
    return SpurPersistenceObservation(
        tuning_id=tuning_id,
        repeat_excess_db=tuple(12.0 if i < qualifying else None
                               for i in range(8)))


def _catalogue(size=45):
    classes = (SESSION_SCOPED, RECONNECT_STABLE, POWER_CYCLE_STABLE)
    kinds = (CONSISTENT_WITH_INTERNAL_MIXING, CONSISTENT_WITH_INTERNAL_REFERENCE,
             SPUR_CANDIDATE_UNRESOLVED, VANISHES_ON_DECLARED_TERMINATION)
    return tuple(CataloguedSpur(spur_id=f"spur-{i:03d}",
                                classification=kinds[i % len(kinds)],
                                stability_class=classes[i % len(classes)],
                                persistence=_persistence(f"tuning-{i % 64:03d}"))
                 for i in range(size))


def _confidence_for(spur):
    return (spur.required_confidence
            or CONFIDENCE_MIXING_TERMINATION_AND_SECOND_TIME)


def _eligible_trials(catalogue, chains, epochs, needed, tunings=None):
    """Enumerate real `(spur, tuning, epoch)` units until `needed` are reached."""
    if tunings is None:
        tunings = generate_tunings(seed=SEED, bands=_bands())
    trials = []
    for epoch in range(epochs):
        for tuning in tunings:
            for index, spur in enumerate(catalogue):
                if len(trials) >= needed:
                    return tuple(trials)
                trials.append(EligibleSpurTrial(
                    spur_id=spur.spur_id, tuning_id=tuning.tuning_id,
                    epoch_id=f"epoch-{epoch}", chain_hash=chains[index % len(chains)],
                    stability_class=spur.stability_class,
                    signed_baseband_hz=float((index % 400) * 1_000 - 200_000),
                    confidence=_confidence_for(spur)))
    return tuple(trials)


def _spur_allocation(trials=MINIMUM_WINDOWS_PER_STRATUM, size=45, epochs=2,
                     chains=None, eligible=None, selected=None,
                     eligible_surplus=139):
    catalogue = _catalogue(size)
    present = []
    for spur in catalogue:
        if spur.stability_class not in present:
            present.append(spur.stability_class)
    base, extra = divmod(trials, len(present))
    if eligible is None:
        chains = chains or sorted(_envelope().admissible_chain_hashes())
        # A genuine eligible universe, larger than the sample drawn from it:
        # membership alone would let the sample be chosen after the results.
        eligible = _eligible_trials(catalogue, chains, epochs,
                                    trials + (eligible_surplus or 0))
    if selected is None:
        canonical = sorted(
            {_canonical_bytes(t.to_dict()).decode(): t for t in eligible}.values(),
            key=lambda t: (t.spur_id, t.tuning_id, t.epoch_id, t.chain_hash))
        selected = select_spur_trials(eligible=canonical, seed=SEED,
                                      required=trials)
    return SpurAllocation(
        catalogue=catalogue, epochs=epochs,
        per_stability_class=tuple(
            (name, base + (1 if i < extra else 0))
            for i, name in enumerate(present)),
        eligible_trials=eligible, selected_trials=selected,
        selection_seed=SEED)


def _trial_plans(envelope, positions=None):
    chains = sorted(envelope.admissible_chain_hashes())
    if positions is None:
        tunings = generate_tunings(seed=SEED, bands=_bands())
        positions = [v.position
                     for v in generate_visit_schedule(seed=SEED, tunings=tunings)]
    plans = []
    for key in STRATUM_KEYS:
        if key in TUNER_REQUIRED:
            plans.append(StratumTrialPlan(
                stratum=key, source=CAPTURED,
                trials=MINIMUM_WINDOWS_PER_STRATUM,
                chain_hashes=tuple(chains),
                per_visit=_spread(MINIMUM_WINDOWS_PER_STRATUM, positions)))
        else:
            plans.append(StratumTrialPlan(
                stratum=key, source=SYNTHETIC,
                trials=MINIMUM_WINDOWS_PER_STRATUM,
                chain_hashes=(), per_visit=()))
    return tuple(plans)


def _plan(envelope=None, seed=SEED, **kwargs):
    envelope = _envelope() if envelope is None else envelope
    chains = sorted(envelope.admissible_chain_hashes())
    default_spurs = ("spur_allocation" not in kwargs)
    return declare_capture_plan(
        seed=seed, envelope=envelope,
        bands=kwargs.pop("bands", _bands()),
        trial_plans=kwargs.pop("trial_plans", _trial_plans(envelope)),
        spur_allocation=(_spur_allocation(chains=chains) if default_spurs
                         else kwargs.pop("spur_allocation")),
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

    def test_a_value_outside_the_canonical_domain_refuses_at_construction(self):
        """The first version let this through and failed only when `digest()`
        reached JSON -- a nominally valid envelope whose invalidity surfaced on
        a later question, which is the type problem §5.23 rejected one field
        down. `default=str` stays out of the digest, but the property that
        keeps the digest total is now this refusal, upstream of it."""
        with self.assertRaises(EnvelopeRefused) as caught:
            _member(extension_mm=object())
        self.assertEqual(caught.exception.code, ENVELOPE_EXTENSION_NOT_CANONICAL)

    def test_the_governed_undeclared_extension_is_accepted(self):
        """`UNDECLARED` is an absence with a name, and the identity module
        records it as one. Refusing it would forbid a mast nobody measured."""
        envelope = InstrumentChainEnvelope(members=(
            _member(extension_mm="UNDECLARED"),))
        json.dumps(envelope.to_dict())

    def test_the_preserved_boolean_extension_is_still_accepted(self):
        """§13n O.3 keeps every existing digest byte-for-byte, and a boolean
        has always hashed as 1.0 or 0.0. Refusing it here would change one."""
        json.dumps(InstrumentChainEnvelope(
            members=(_member(extension_mm=True),)).to_dict())

    def test_every_constructible_envelope_serialises(self):
        """Which is why the digest needs no `default=str`: the domain is
        closed at construction, so there is nothing left for it to stringify."""
        for extension in (0.0, 12.5, 0, True, False, "UNDECLARED"):
            json.dumps(InstrumentChainEnvelope(
                members=(_member(extension_mm=extension),)).digest())

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


class TuningsAreFrequenciesTests(unittest.TestCase):
    """A tuning index is not a tuning.

    The first implementation permuted 0..63 and digested
    PLAN_RETUNE_DELTAS_HZ, PLAN_MAX_MIXING_SLOPE and PLAN_BAND_EDGE_EXCLUSION
    beside it, none of which described anything the plan did.
    """

    def test_the_declared_tunings_carry_hertz_and_a_band(self):
        tunings = generate_tunings(seed=SEED, bands=_bands())
        self.assertEqual(len(tunings), PLAN_TUNING_COUNT)
        for tuning in tunings:
            self.assertGreater(tuning.center_frequency_hz, 0.0)
            self.assertIn(tuning.band_id, {b.band_id for b in _bands()})

    def test_every_tuning_can_be_retuned_inside_its_band(self):
        """The whole analysis span, after the largest declared excursion. A
        plan that declared a band and sampled outside it would be the
        envelope-scope limit failing quietly."""
        by_id = {b.band_id: b for b in _bands()}
        for tuning in generate_tunings(seed=SEED, bands=_bands()):
            self.assertTrue(
                by_id[tuning.band_id].contains_tuning(tuning.center_frequency_hz))

    def test_the_spacing_is_not_uniform(self):
        """Uniform spacing at a divisor of the reference interval would
        systematically hit or miss the comb, making the catalogue an artefact
        of the grid."""
        tunings = [t for t in generate_tunings(seed=SEED, bands=_bands())
                   if t.band_id == "UHF_LOW"]
        gaps = [round(b.center_frequency_hz - a.center_frequency_hz)
                for a, b in zip(tunings, tunings[1:])]
        self.assertGreater(len(set(gaps)), len(gaps) // 2)

    def test_the_tunings_are_distinct(self):
        tunings = generate_tunings(seed=SEED, bands=_bands())
        self.assertEqual(len({t.center_frequency_hz for t in tunings}),
                         len(tunings))

    def test_every_declared_band_receives_tunings(self):
        placed = {t.band_id for t in generate_tunings(seed=SEED, bands=_bands())}
        self.assertEqual(placed, {b.band_id for b in _bands()})

    def test_a_band_narrower_than_one_span_refuses(self):
        with self.assertRaises(EnvelopeRefused) as caught:
            Band(band_id="TOO_NARROW", low_hz=400_000_000.0,
                 high_hz=400_100_000.0)
        self.assertEqual(caught.exception.code, PLAN_BAND_UNUSABLE)

    def test_a_tuning_outside_its_band_refuses_at_the_plan(self):
        envelope = _envelope()
        good = _plan(envelope)
        moved = (Tuning(tuning_index=good.tunings[0].tuning_index,
                        center_frequency_hz=good.bands[0].high_hz + 1_000_000.0,
                        band_id=good.tunings[0].band_id),) + good.tunings[1:]
        with self.assertRaises(EnvelopeRefused) as caught:
            CapturePlanDeclaration(
                seed=good.seed,
                schedule_generator_revision=good.schedule_generator_revision,
                bands=good.bands, tunings=moved, schedule=good.schedule,
                trial_plans=good.trial_plans,
                spur_allocation=good.spur_allocation,
                reference_hz=good.reference_hz,
                reference_ppm=good.reference_ppm)
        self.assertEqual(caught.exception.code, PLAN_TUNING_OUTSIDE_BAND)

    def test_the_retune_ceiling_is_derived_from_the_slope_bound(self):
        """§5.22 derived 341 kHz from the raw half-span. This is the same
        arithmetic over the *usable* half-span, so it is tighter."""
        self.assertAlmostEqual(usable_half_span_hz(), 921_600.0, places=3)
        self.assertAlmostEqual(maximum_retune_delta_hz(), 307_200.0, places=3)
        self.assertLess(maximum_retune_delta_hz(), 341_000.0)

    def test_the_ceiling_depends_on_the_slope_bound(self):
        """Evaluated at two slopes, so a ceiling that ignored the bound fails
        here rather than by making every plan unconstructable."""
        at_three = retune_delta_ceiling_hz(2_048_000.0, 0.05, 3)
        at_six = retune_delta_ceiling_hz(2_048_000.0, 0.05, 6)
        self.assertAlmostEqual(at_three, 307_200.0, places=3)
        self.assertAlmostEqual(at_six, 153_600.0, places=3)
        self.assertAlmostEqual(at_three, at_six * 2, places=3)

    def test_the_ceiling_depends_on_the_band_edge_exclusion(self):
        self.assertGreater(retune_delta_ceiling_hz(2_048_000.0, 0.0, 3),
                           retune_delta_ceiling_hz(2_048_000.0, 0.05, 3))

    def test_the_module_ceiling_is_that_arithmetic_at_the_declared_constants(self):
        self.assertAlmostEqual(
            maximum_retune_delta_hz(),
            retune_delta_ceiling_hz(2_048_000.0, 0.05, 3), places=3)

    def test_a_delta_over_a_ceiling_is_reported(self):
        """Tested directly, because no legal plan can violate the real ceiling
        and a control over the inline form mutated nothing into nothing."""
        self.assertEqual(deltas_over_ceiling((50_000.0, 400_000.0), 307_200.0),
                         (400_000.0,))
        self.assertEqual(deltas_over_ceiling((50_000.0,), 307_200.0), ())

    def test_every_declared_delta_is_inside_the_ceiling(self):
        for delta in (50_000.0, 100_000.0, 200_000.0):
            self.assertLessEqual(delta, maximum_retune_delta_hz())

    def test_a_retune_beyond_the_ceiling_refuses(self):
        """Checked before the regeneration comparison, so the refusal names the
        substantive fault rather than "it does not regenerate"."""
        good = _plan()
        over = (Visit(position=good.schedule[0].position,
                      tuning_index=good.schedule[0].tuning_index,
                      retune_delta_hz=500_000.0),) + good.schedule[1:]
        with self.assertRaises(EnvelopeRefused) as caught:
            CapturePlanDeclaration(
                seed=good.seed,
                schedule_generator_revision=good.schedule_generator_revision,
                bands=good.bands, tunings=good.tunings, schedule=over,
                trial_plans=good.trial_plans,
                spur_allocation=good.spur_allocation,
                reference_hz=good.reference_hz, reference_ppm=good.reference_ppm)
        self.assertEqual(caught.exception.code, PLAN_RETUNE_NOT_DECLARED)


class ScheduleTests(unittest.TestCase):

    def _tunings(self, seed=SEED):
        return generate_tunings(seed=seed, bands=_bands())

    def _schedule(self, seed=SEED):
        return generate_visit_schedule(seed=seed, tunings=self._tunings(seed))

    def test_the_same_seed_produces_the_same_schedule(self):
        self.assertEqual(self._schedule(), self._schedule())

    def test_a_different_seed_produces_a_different_schedule(self):
        self.assertNotEqual(self._schedule(7), self._schedule(8))

    def test_the_schedule_does_not_depend_on_the_interpreter_rng(self):
        """Not `random.shuffle`. A byte-for-byte promise resting on the
        stability of CPython's RNG is a promise about CPython."""
        random.seed(1)
        first = self._schedule(7)
        random.seed(999)
        [random.random() for _ in range(50)]
        self.assertEqual(self._schedule(7), first)

    def test_every_tuning_is_visited_the_declared_number_of_times(self):
        counts = {}
        for visit in self._schedule():
            counts[visit.tuning_index] = counts.get(visit.tuning_index, 0) + 1
        self.assertEqual(len(counts), PLAN_TUNING_COUNT)
        self.assertEqual(set(counts.values()), {PLAN_VISITS_PER_TUNING})

    def test_repeats_are_separated_by_the_declared_minimum(self):
        last = {}
        for visit in self._schedule():
            previous = last.get(visit.tuning_index)
            if previous is not None:
                self.assertGreater(visit.position - previous,
                                   PLAN_MINIMUM_SEPARATION)
            last[visit.tuning_index] = visit.position

    def test_the_schedule_is_counterbalanced(self):
        schedule = self._schedule()
        half = len(schedule) // 2
        self.assertEqual({v.tuning_index for v in schedule[:half]},
                         set(range(PLAN_TUNING_COUNT)))
        self.assertEqual({v.tuning_index for v in schedule[half:]},
                         set(range(PLAN_TUNING_COUNT)))

    def test_every_visit_carries_a_signed_declared_retune(self):
        """The act PLAN_RETUNE_DELTAS_HZ governs. Without it the declared
        deltas described nothing that happened."""
        used = {v.retune_delta_hz for v in self._schedule()}
        self.assertEqual(used, {50_000.0, -50_000.0, 100_000.0, -100_000.0,
                                200_000.0, -200_000.0})

    def test_each_delta_is_used_in_both_directions(self):
        used = {v.retune_delta_hz for v in self._schedule()}
        for magnitude in (50_000.0, 100_000.0, 200_000.0):
            self.assertIn(magnitude, used)
            self.assertIn(-magnitude, used)

    def test_positions_are_carried_rather_than_implied_by_order(self):
        schedule = self._schedule(3)
        self.assertEqual([v.position for v in schedule],
                         list(range(len(schedule))))

    def test_an_unknown_generator_revision_refuses(self):
        with self.assertRaises(EnvelopeRefused) as caught:
            generate_visit_schedule(seed=7, tunings=self._tunings(7),
                                    generator_revision="something.v9")
        self.assertEqual(caught.exception.code, PLAN_SCHEDULE_NOT_REPRODUCIBLE)

    def test_a_seed_that_is_not_an_integer_refuses(self):
        with self.assertRaises(EnvelopeRefused) as caught:
            generate_tunings(seed="7", bands=_bands())
        self.assertEqual(caught.exception.code, PLAN_QUANTITY_NOT_FINITE)


class TrialAllocationTests(unittest.TestCase):
    """A visit annotation is not a trial allocation.

    192 scheduled visits say nothing about the 16 683 captured windows the
    corpus is made of, and a plan that never mentions a trial lets a lock open
    before the corpus it freezes has been described.
    """

    def test_the_plan_accounts_for_every_declared_trial(self):
        plan = _plan()
        self.assertEqual(len(plan.planned_strata()), len(STRATUM_KEYS))
        self.assertEqual(plan.total_trials(),
                         MINIMUM_WINDOWS_PER_STRATUM * len(STRATUM_KEYS))

    def test_the_captured_strata_are_the_ones_needing_a_receiver(self):
        self.assertEqual(set(_plan().captured_strata()), set(TUNER_REQUIRED))

    def test_captured_trials_reconcile_with_their_visits(self):
        for entry in _plan().trial_plans:
            if entry.source != CAPTURED:
                continue
            self.assertEqual(sum(n for _p, n in entry.per_visit), entry.trials)

    def test_per_visit_counts_that_do_not_sum_refuse(self):
        with self.assertRaises(EnvelopeRefused) as caught:
            StratumTrialPlan(stratum="GAIN_STEPS", source=CAPTURED,
                             trials=5_561, chain_hashes=("blake2s:a",),
                             per_visit=((0, 10), (1, 10)))
        self.assertEqual(caught.exception.code, PLAN_TRIALS_DO_NOT_RECONCILE)

    def test_a_captured_stratum_naming_no_chain_refuses(self):
        with self.assertRaises(EnvelopeRefused) as caught:
            StratumTrialPlan(stratum="GAIN_STEPS", source=CAPTURED, trials=2,
                             chain_hashes=(), per_visit=((0, 2),))
        self.assertEqual(caught.exception.code, PLAN_ABSENT)

    def test_a_synthetic_stratum_naming_a_chain_refuses(self):
        """A regenerated window came through no instrument."""
        with self.assertRaises(EnvelopeRefused) as caught:
            StratumTrialPlan(stratum="AM", source=SYNTHETIC, trials=2,
                             chain_hashes=("blake2s:a",), per_visit=())
        self.assertEqual(caught.exception.code, PLAN_ABSENT)

    def test_trials_allocated_to_a_visit_that_does_not_exist_refuse(self):
        envelope = _envelope()
        plans = list(_trial_plans(envelope))
        plans[0] = StratumTrialPlan(
            stratum=plans[0].stratum, source=plans[0].source,
            trials=plans[0].trials, chain_hashes=plans[0].chain_hashes,
            per_visit=((10_000, plans[0].trials),)
            if plans[0].source == CAPTURED else ())
        if plans[0].source != CAPTURED:
            plans = [p for p in plans if p.stratum in TUNER_REQUIRED] + \
                    [p for p in plans if p.stratum not in TUNER_REQUIRED]
            plans[0] = StratumTrialPlan(
                stratum=plans[0].stratum, source=CAPTURED,
                trials=plans[0].trials, chain_hashes=plans[0].chain_hashes,
                per_visit=((10_000, plans[0].trials),))
        with self.assertRaises(EnvelopeRefused) as caught:
            _plan(envelope, trial_plans=tuple(plans))
        self.assertEqual(caught.exception.code, PLAN_TRIALS_DO_NOT_RECONCILE)


class SpurFeasibilityTests(unittest.TestCase):
    """§5.22: feasibility before the corpus opens, because the lock closes
    option 4 and option 4 is the way out if the catalogue comes back small."""

    def test_a_feasible_catalogue_is_accepted(self):
        allocation = _spur_allocation()
        self.assertTrue(allocation.feasible_for(MINIMUM_WINDOWS_PER_STRATUM))

    def test_feasibility_counts_enumerated_units_not_a_product(self):
        """`S * K * E` is an upper bound. The lock closes option 4, so the
        count that closes it has to be of tuples the plan can produce."""
        allocation = _spur_allocation()
        # Three numbers, strictly ordered, and each says something different:
        # what could exist, what survived eligibility, and what was selected.
        self.assertGreater(allocation.cardinality_bound,
                           allocation.distinct_trial_units)
        self.assertGreater(allocation.distinct_trial_units,
                           len(allocation.selected_trials))
        self.assertEqual(allocation.distinct_trial_units,
                         len({t.key() for t in allocation.eligible_trials}))

    def test_a_catalogue_that_multiplies_up_but_enumerates_short_refuses(self):
        """The case the product hides: S*K*E clears the bound and the tuples
        that survive every eligibility check do not."""
        catalogue = _catalogue(45)
        chains = sorted(_envelope().admissible_chain_hashes())
        short = _eligible_trials(catalogue, chains, 2,
                                 MINIMUM_WINDOWS_PER_STRATUM - 1)
        allocation = _spur_allocation(eligible=short, selected=())
        self.assertGreaterEqual(allocation.cardinality_bound,
                                MINIMUM_WINDOWS_PER_STRATUM)
        self.assertLess(allocation.distinct_trial_units,
                        MINIMUM_WINDOWS_PER_STRATUM)
        with self.assertRaises(EnvelopeRefused) as caught:
            _plan(spur_allocation=allocation)
        self.assertEqual(caught.exception.code, PLAN_SPUR_NOT_FEASIBLE)

    def test_a_trial_inside_the_band_edge_exclusion_refuses(self):
        with self.assertRaises(EnvelopeRefused) as caught:
            EligibleSpurTrial(
                spur_id="spur-000", tuning_id="tuning-000", epoch_id="e0",
                chain_hash="blake2s:a", stability_class=SESSION_SCOPED,
                signed_baseband_hz=1_000_000.0,
                confidence=CONFIDENCE_MIXING_TERMINATION_AND_SECOND_TIME)
        self.assertEqual(caught.exception.code, PLAN_TRIAL_NOT_ELIGIBLE)

    def test_a_trial_at_the_wrong_confidence_for_its_class_refuses(self):
        catalogue = _catalogue(45)
        reference = next(s for s in catalogue
                         if s.classification == CONSISTENT_WITH_INTERNAL_REFERENCE)
        wrong = EligibleSpurTrial(
            spur_id=reference.spur_id, tuning_id="tuning-000", epoch_id="e0",
            chain_hash="blake2s:a", stability_class=reference.stability_class,
            signed_baseband_hz=0.0,
            confidence=CONFIDENCE_MIXING_TERMINATION_AND_SECOND_TIME)
        with self.assertRaises(EnvelopeRefused) as caught:
            SpurAllocation(catalogue=catalogue, epochs=2,
                           per_stability_class=((SESSION_SCOPED, 1),
                                                (RECONNECT_STABLE, 1),
                                                (POWER_CYCLE_STABLE, 1)),
                           eligible_trials=(wrong,), selected_trials=(),
                           selection_seed=SEED)
        self.assertEqual(caught.exception.code, PLAN_TRIAL_NOT_ELIGIBLE)

    def test_a_trial_naming_an_undeclared_tuning_refuses(self):
        catalogue = _catalogue(45)
        chains = sorted(_envelope().admissible_chain_hashes())
        trials = list(_eligible_trials(catalogue, chains, 2,
                                       MINIMUM_WINDOWS_PER_STRATUM))
        trials[0] = EligibleSpurTrial(
            spur_id=trials[0].spur_id, tuning_id="tuning-999",
            epoch_id=trials[0].epoch_id, chain_hash=trials[0].chain_hash,
            stability_class=trials[0].stability_class,
            signed_baseband_hz=trials[0].signed_baseband_hz,
            confidence=trials[0].confidence)
        with self.assertRaises(EnvelopeRefused) as caught:
            _plan(spur_allocation=_spur_allocation(eligible=tuple(trials)))
        self.assertEqual(caught.exception.code, PLAN_TRIAL_NOT_ELIGIBLE)

    def test_a_catalogue_too_small_to_reach_the_bound_refuses(self):
        """Discovered before a lock exists, not after four thousand windows."""
        with self.assertRaises(EnvelopeRefused) as caught:
            _plan(spur_allocation=_spur_allocation(size=2, epochs=1))
        self.assertEqual(caught.exception.code, PLAN_SPUR_NOT_FEASIBLE)

    def test_an_empty_catalogue_refuses(self):
        with self.assertRaises(EnvelopeRefused) as caught:
            SpurAllocation(catalogue=(), epochs=2,
                           per_stability_class=((SESSION_SCOPED, 1),),
                           eligible_trials=(), selected_trials=(),
                           selection_seed=SEED)
        self.assertEqual(caught.exception.code, PLAN_SPUR_CATALOGUE_ABSENT)

    def test_planning_receiver_spurs_without_a_catalogue_refuses(self):
        with self.assertRaises(EnvelopeRefused) as caught:
            _plan(spur_allocation=None)
        self.assertEqual(caught.exception.code, PLAN_SPUR_CATALOGUE_ABSENT)

    def test_a_class_in_the_catalogue_and_not_in_the_allocation_refuses(self):
        """An allocation that under-sampled SESSION_SCOPED would test the
        stratum where it is easiest."""
        catalogue = _catalogue(45)
        with self.assertRaises(EnvelopeRefused) as caught:
            SpurAllocation(catalogue=catalogue, epochs=2,
                           per_stability_class=((SESSION_SCOPED, 5_561),),
                           eligible_trials=(), selected_trials=(),
                           selection_seed=SEED)
        self.assertEqual(caught.exception.code, PLAN_SPUR_ALLOCATION_UNDECLARED)

    def test_a_class_allocated_nothing_refuses(self):
        catalogue = _catalogue(45)
        with self.assertRaises(EnvelopeRefused) as caught:
            SpurAllocation(catalogue=catalogue, epochs=2,
                           per_stability_class=((SESSION_SCOPED, 5_561),
                                                (RECONNECT_STABLE, 0),
                                                (POWER_CYCLE_STABLE, 0)),
                           eligible_trials=(), selected_trials=(),
                           selection_seed=SEED)
        self.assertEqual(caught.exception.code, PLAN_SPUR_ALLOCATION_UNDECLARED)

    def test_an_allocation_that_does_not_sum_to_the_trials_refuses(self):
        """Feasible, enumerated and correctly selected, but the per-class counts
        do not add up to the trials the stratum plans."""
        good = _spur_allocation()
        skewed = SpurAllocation(
            catalogue=good.catalogue, epochs=good.epochs,
            per_stability_class=tuple((name, 1) for name, _c
                                      in good.per_stability_class),
            eligible_trials=good.eligible_trials,
            selected_trials=good.selected_trials,
            selection_seed=good.selection_seed)
        with self.assertRaises(EnvelopeRefused) as caught:
            _plan(spur_allocation=skewed)
        self.assertEqual(caught.exception.code, PLAN_SPUR_ALLOCATION_UNDECLARED)

    def test_the_two_usable_classes_carry_their_governed_confidence(self):
        mixing = CataloguedSpur(spur_id="s1",
                                classification=CONSISTENT_WITH_INTERNAL_MIXING,
                                stability_class=SESSION_SCOPED,
                                persistence=_persistence())
        reference = CataloguedSpur(spur_id="s2",
                                   classification=CONSISTENT_WITH_INTERNAL_REFERENCE,
                                   stability_class=SESSION_SCOPED,
                                   persistence=_persistence())
        self.assertEqual(mixing.required_confidence,
                         CONFIDENCE_MIXING_TERMINATION_AND_SECOND_TIME)
        self.assertEqual(reference.required_confidence,
                         CONFIDENCE_REFERENCE_TERMINATION_AND_SECOND_SITE)

    def test_the_two_undecided_classes_carry_none(self):
        """§5.21 gives them no usable classification, so there is no confidence
        at which they attest to internality. None is the honest report."""
        for classification in (SPUR_CANDIDATE_UNRESOLVED,
                               VANISHES_ON_DECLARED_TERMINATION):
            spur = CataloguedSpur(spur_id="s", classification=classification,
                                  stability_class=SESSION_SCOPED,
                                  persistence=_persistence())
            self.assertIsNone(spur.required_confidence)

    def test_the_bound_does_not_generalise_and_says_so(self):
        data = _spur_allocation().to_dict()
        self.assertFalse(data["generalises_across_spur_types"])
        self.assertFalse(data["generalises_across_receiver_units"])


class CapturePlanTests(unittest.TestCase):

    def test_a_declared_plan_regenerates_to_itself(self):
        plan = _plan()
        self.assertEqual(
            plan.schedule,
            generate_visit_schedule(seed=plan.seed, tunings=plan.tunings))
        self.assertEqual(
            plan.tunings, generate_tunings(seed=plan.seed, bands=plan.bands))

    def test_a_hand_written_schedule_refuses(self):
        """A seed is provenance. Holding both, and comparing byte for byte,
        makes a forged schedule and a drifted generator one failure."""
        good = _plan()
        forged = tuple(Visit(position=i, tuning_index=i % PLAN_TUNING_COUNT,
                             retune_delta_hz=50_000.0)
                       for i in range(len(good.schedule)))
        with self.assertRaises(EnvelopeRefused) as caught:
            CapturePlanDeclaration(
                seed=good.seed,
                schedule_generator_revision=good.schedule_generator_revision,
                bands=good.bands, tunings=good.tunings, schedule=forged,
                trial_plans=good.trial_plans,
                spur_allocation=good.spur_allocation,
                reference_hz=good.reference_hz, reference_ppm=good.reference_ppm)
        self.assertEqual(caught.exception.code, PLAN_SCHEDULE_NOT_REPRODUCIBLE)

    def test_one_reordered_visit_is_caught(self):
        """Byte for byte, not as a set."""
        good = _plan()
        schedule = list(good.schedule)
        schedule[0], schedule[-1] = schedule[-1], schedule[0]
        with self.assertRaises(EnvelopeRefused) as caught:
            CapturePlanDeclaration(
                seed=good.seed,
                schedule_generator_revision=good.schedule_generator_revision,
                bands=good.bands, tunings=good.tunings,
                schedule=tuple(schedule), trial_plans=good.trial_plans,
                spur_allocation=good.spur_allocation,
                reference_hz=good.reference_hz, reference_ppm=good.reference_ppm)
        self.assertEqual(caught.exception.code, PLAN_SCHEDULE_NOT_REPRODUCIBLE)

    def test_one_moved_tuning_is_caught(self):
        good = _plan()
        moved = (Tuning(tuning_index=0,
                        center_frequency_hz=good.tunings[0].center_frequency_hz + 1.0,
                        band_id=good.tunings[0].band_id),) + good.tunings[1:]
        with self.assertRaises(EnvelopeRefused) as caught:
            CapturePlanDeclaration(
                seed=good.seed,
                schedule_generator_revision=good.schedule_generator_revision,
                bands=good.bands, tunings=moved, schedule=good.schedule,
                trial_plans=good.trial_plans,
                spur_allocation=good.spur_allocation,
                reference_hz=good.reference_hz, reference_ppm=good.reference_ppm)
        self.assertEqual(caught.exception.code, PLAN_SCHEDULE_NOT_REPRODUCIBLE)

    def test_an_allocation_outside_the_envelope_refuses(self):
        envelope = _envelope()
        stray = _member(gain=33.0, antenna="ANT_B").chain_hash()
        plans = tuple(
            StratumTrialPlan(stratum=p.stratum, source=p.source,
                             trials=p.trials,
                             chain_hashes=((stray,) if p.source == CAPTURED
                                           else ()),
                             per_visit=p.per_visit)
            for p in _trial_plans(envelope))
        with self.assertRaises(EnvelopeRefused) as caught:
            _plan(envelope, trial_plans=plans)
        self.assertEqual(caught.exception.code, PLAN_ALLOCATION_OUTSIDE_ENVELOPE)

    def test_a_non_positive_reference_refuses_at_construction(self):
        """Not when something later asks for the harmonic cap. A declaration
        only found invalid on a later question is not self-validating."""
        with self.assertRaises(EnvelopeRefused) as caught:
            _plan(reference_ppm=0.0)
        self.assertEqual(caught.exception.code, PLAN_QUANTITY_NOT_FINITE)

    def test_the_plan_carries_the_derived_harmonic_cap(self):
        self.assertEqual(_plan(reference_ppm=100.0).harmonic_cap, 3)
        self.assertEqual(_plan(reference_ppm=1.0).harmonic_cap, 355)

    def test_the_plan_digest_moves_with_the_declared_tolerance(self):
        self.assertNotEqual(_plan(reference_ppm=1.0).digest(),
                            _plan(reference_ppm=100.0).digest())

    def test_the_plan_digest_moves_with_the_declared_band(self):
        other = (Band(band_id="UHF_LOW", low_hz=400_000_000.0,
                      high_hz=440_000_000.0),
                 Band(band_id="UHF_HIGH", low_hz=440_000_000.0,
                      high_hz=469_000_000.0))
        self.assertNotEqual(_plan().digest(), _plan(bands=other).digest())

    def test_a_plan_declared_against_a_non_envelope_refuses(self):
        with self.assertRaises(EnvelopeRefused) as caught:
            declare_capture_plan(seed=1, envelope="an envelope, honestly",
                                 bands=_bands(), trial_plans=(),
                                 reference_hz=28_800_000.0, reference_ppm=1.0)
        self.assertEqual(caught.exception.code, ENVELOPE_ABSENT)

    def test_the_plan_serialises_without_stringifying(self):
        json.dumps(_plan().to_dict())

class PersistenceIsObservedTests(unittest.TestCase):
    """§5.22's R = 8 and 7-of-8, bound to declared repeats.

    Storing `observed_in_repeats` beside a summary `excess_db` was two numbers
    that can disagree about which repeats qualified, and a maximum or a mean
    would let one spectacular repeat launder seven absences.
    """

    def test_the_verdict_is_derived_from_the_repeats(self):
        observation = _persistence(qualifying=7)
        self.assertEqual(observation.qualifying(), 7)
        self.assertTrue(observation.persistent())

    def test_six_of_eight_is_not_persistent(self):
        self.assertFalse(_persistence(qualifying=6).persistent())

    def test_a_spur_that_is_not_persistent_is_not_catalogued(self):
        with self.assertRaises(EnvelopeRefused) as caught:
            CataloguedSpur(spur_id="s", classification=SPUR_CANDIDATE_UNRESOLVED,
                           stability_class=SESSION_SCOPED,
                           persistence=_persistence(qualifying=6))
        self.assertEqual(caught.exception.code, PLAN_SPUR_NOT_PERSISTENT)

    def test_a_repeat_below_the_margin_does_not_qualify(self):
        """Each qualifying observation is at least 10 dB above its own
        tuning-local median, because the noise floor is not flat."""
        just_under = SpurPersistenceObservation(
            tuning_id="tuning-000",
            repeat_excess_db=(PLAN_PERSISTENCE_MARGIN_DB - 0.1,) * 8)
        self.assertEqual(just_under.qualifying(), 0)
        at_the_margin = SpurPersistenceObservation(
            tuning_id="tuning-000",
            repeat_excess_db=(PLAN_PERSISTENCE_MARGIN_DB,) * 8)
        self.assertEqual(at_the_margin.qualifying(), 8)

    def test_one_spectacular_repeat_cannot_launder_seven_absences(self):
        """The failure a maximum or a mean would have allowed."""
        loud_once = SpurPersistenceObservation(
            tuning_id="tuning-000",
            repeat_excess_db=(90.0,) + (None,) * 7)
        self.assertEqual(loud_once.qualifying(), 1)
        self.assertFalse(loud_once.persistent())

    def test_a_wrong_number_of_repeats_refuses(self):
        """Seven of eight is a ratio over a fixed denominator, not over
        whatever was tried."""
        with self.assertRaises(EnvelopeRefused) as caught:
            SpurPersistenceObservation(tuning_id="t", repeat_excess_db=(12.0,) * 4)
        self.assertEqual(caught.exception.code, PLAN_REPEATS_NOT_OBSERVED)
        self.assertEqual(PLAN_REPEATS_PER_TUNING, 8)
        self.assertEqual(PLAN_PERSISTENCE_REQUIRED, 7)

    def test_an_absent_repeat_is_not_a_small_one(self):
        present = SpurPersistenceObservation(
            tuning_id="t", repeat_excess_db=(0.0,) * 8)
        absent = SpurPersistenceObservation(
            tuning_id="t", repeat_excess_db=(None,) * 8)
        self.assertNotEqual(present.to_dict(), absent.to_dict())


class DeclaredConstantsGovernActsTests(unittest.TestCase):
    """The check the first implementation failed: a constant in a digest is not
    a plan if no declared act refers to it."""

    def test_an_undeclared_delta_below_the_ceiling_still_refuses(self):
        """Distinguishes the two retune checks. 75 kHz is under the derived
        ceiling and is not one of the three declared deltas, so only the
        membership check can catch it."""
        self.assertLess(75_000.0, maximum_retune_delta_hz())
        good = _plan()
        odd = (Visit(position=good.schedule[0].position,
                     tuning_index=good.schedule[0].tuning_index,
                     retune_delta_hz=75_000.0),) + good.schedule[1:]
        with self.assertRaises(EnvelopeRefused) as caught:
            CapturePlanDeclaration(
                seed=good.seed,
                schedule_generator_revision=good.schedule_generator_revision,
                bands=good.bands, tunings=good.tunings, schedule=odd,
                trial_plans=good.trial_plans,
                spur_allocation=good.spur_allocation,
                reference_hz=good.reference_hz, reference_ppm=good.reference_ppm)
        self.assertEqual(caught.exception.code, PLAN_RETUNE_NOT_DECLARED)

    def test_the_sample_type_is_fixed_across_the_envelope(self):
        """Distinct hashes are not a licence: uint8 and cs8 members would both
        be admitted, which is two decode paths inside one declared envelope."""
        with self.assertRaises(EnvelopeRefused) as caught:
            InstrumentChainEnvelope(members=(
                _member(gain=20.0, sample_type="cs8"),
                _member(gain=40.0, sample_type="uint8")))
        self.assertEqual(caught.exception.code,
                         ENVELOPE_RECEIVER_SETTING_VARIES)

    def test_the_slope_tolerance_governs_nothing_here_and_is_queued(self):
        """Honest rather than hidden. `PLAN_SLOPE_TOLERANCE` is frozen into the
        digest and enforced by no declaration, because it governs an analysis
        step and there is no analysis in this module. Recorded as
        `PENDING_AMENDMENTS` entry 15 rather than as a comment, which is the
        prose-waits-forever failure entry 11 demonstrated.
        """
        import pathlib
        queue = pathlib.Path("docs/PENDING_AMENDMENTS.md").read_text(
            encoding="utf-8")
        self.assertIn("PLAN_SLOPE_TOLERANCE", queue)
        self.assertIn("## 15.", queue)


class TheUnitsAreRetainedTests(unittest.TestCase):
    """A digest is a commitment; a declaration is what the lock can answer from.

    `SpurAllocation.eligible_trials` holds the units. `to_dict()` exposes their
    digest instead of 5 561 rows so that a status report stays readable, which
    is a summary decision and not a storage one -- and these tests are what
    fails if it ever becomes a storage one.
    """

    def test_the_plan_retains_the_eligible_universe_and_the_selection(self):
        """Two different sets, and the difference is the point. The eligible
        universe proves feasibility; the selection is the sample, and it is
        smaller because a universe exactly the size of its sample would make
        precommitment vacuous."""
        allocation = _plan().spur_allocation
        self.assertGreater(allocation.distinct_trial_units,
                           MINIMUM_WINDOWS_PER_STRATUM)
        self.assertEqual(len(allocation.selected_trials),
                         MINIMUM_WINDOWS_PER_STRATUM)

    def test_the_lock_can_say_which_units_were_authorised(self):
        allocation = _plan().spur_allocation
        first = allocation.eligible_trials[0]
        self.assertIn(first.key(),
                      {t.key() for t in allocation.eligible_trials})

    def test_the_digest_is_reproducible_from_what_the_plan_holds(self):
        allocation = _plan().spur_allocation
        rebuilt = SpurAllocation(
            catalogue=allocation.catalogue, epochs=allocation.epochs,
            per_stability_class=allocation.per_stability_class,
            eligible_trials=allocation.eligible_trials, selected_trials=(), selection_seed=SEED)
        self.assertEqual(rebuilt.eligible_trials_digest(),
                         allocation.eligible_trials_digest())

    def test_one_altered_trial_moves_the_digest_at_an_unchanged_count(self):
        """The mutation a stored root-and-count could not distinguish: the
        count, the cardinality bound and every other summary are identical."""
        catalogue = _catalogue(45)
        chains = sorted(_envelope().admissible_chain_hashes())
        trials = list(_eligible_trials(catalogue, chains, 2,
                                       MINIMUM_WINDOWS_PER_STRATUM))
        original = _spur_allocation(eligible=tuple(trials))
        swapped = list(trials)
        target = swapped[0]
        swapped[0] = EligibleSpurTrial(
            spur_id=target.spur_id, tuning_id=target.tuning_id,
            epoch_id=target.epoch_id,
            chain_hash=chains[(chains.index(target.chain_hash) + 1) % len(chains)],
            stability_class=target.stability_class,
            signed_baseband_hz=target.signed_baseband_hz,
            confidence=target.confidence)
        altered = _spur_allocation(eligible=tuple(swapped))
        self.assertEqual(altered.distinct_trial_units,
                         original.distinct_trial_units)
        self.assertEqual(altered.cardinality_bound, original.cardinality_bound)
        self.assertNotEqual(altered.eligible_trials_digest(),
                            original.eligible_trials_digest())

    def test_a_trial_identity_determines_its_facts(self):
        """Two records of one (spur, tuning, epoch) that disagree are not one
        trial seen twice. Collapsing them would let the feasibility count
        certify a set that never cohered."""
        catalogue = _catalogue(45)
        chains = sorted(_envelope().admissible_chain_hashes())
        first = EligibleSpurTrial(
            spur_id=catalogue[2].spur_id, tuning_id="tuning-000",
            epoch_id="epoch-0", chain_hash=chains[0],
            stability_class=catalogue[2].stability_class,
            signed_baseband_hz=0.0,
            confidence=_confidence_for(catalogue[2]))
        contradicting = EligibleSpurTrial(
            spur_id=first.spur_id, tuning_id=first.tuning_id,
            epoch_id=first.epoch_id, chain_hash=chains[1],
            stability_class=first.stability_class,
            signed_baseband_hz=first.signed_baseband_hz,
            confidence=first.confidence)
        with self.assertRaises(EnvelopeRefused) as caught:
            SpurAllocation(
                catalogue=catalogue, epochs=2,
                per_stability_class=((SESSION_SCOPED, 1), (RECONNECT_STABLE, 1),
                                     (POWER_CYCLE_STABLE, 1)),
                eligible_trials=(first, contradicting), selected_trials=(),
                selection_seed=SEED)
        self.assertEqual(caught.exception.code, PLAN_TRIAL_IDENTITY_AMBIGUOUS)

    def test_a_trial_identity_declared_twice_identically_is_one_trial(self):
        """Not an error. A repeated identical record says nothing new, and the
        refusal above is about disagreement rather than about repetition."""
        catalogue = _catalogue(45)
        chains = sorted(_envelope().admissible_chain_hashes())
        trial = EligibleSpurTrial(
            spur_id=catalogue[2].spur_id, tuning_id="tuning-000",
            epoch_id="epoch-0", chain_hash=chains[0],
            stability_class=catalogue[2].stability_class,
            signed_baseband_hz=0.0, confidence=_confidence_for(catalogue[2]))
        allocation = SpurAllocation(
            catalogue=catalogue, epochs=2,
            per_stability_class=((SESSION_SCOPED, 1), (RECONNECT_STABLE, 1),
                                 (POWER_CYCLE_STABLE, 1)),
            eligible_trials=(trial, trial), selected_trials=(),
            selection_seed=SEED)
        self.assertEqual(allocation.distinct_trial_units, 1)

    def test_the_declared_deltas_are_checked_against_the_derived_ceiling(self):
        """Over the constants, where it is reachable. A schedule delta above the
        ceiling is also undeclared, so the membership check always gets there
        first -- which a control proved by failing zero tests."""
        self.assertEqual(
            [d for d in (50_000.0, 100_000.0, 200_000.0)
             if d > maximum_retune_delta_hz()], [])
        _plan()


class CanonicalDuplicateTests(unittest.TestCase):
    """"It counts once" is not the property. Canonical storage is.

    A duplicate left in the stored field gives one authorised set two
    identities and two digests, which is the defect the member sort fixed one
    layer up, reappearing among the trials.
    """

    def _pair(self):
        catalogue = _catalogue(45)
        chains = sorted(_envelope().admissible_chain_hashes())
        a, b = _eligible_trials(catalogue, chains, 2, 2)
        # An equal but DISTINCT object. Passing `a` twice would be deduplicated
        # by anything at all, including a dictionary keyed on `id()` -- which a
        # control proved by mutating the canonical key and failing zero tests.
        a_again = EligibleSpurTrial(
            spur_id=a.spur_id, tuning_id=a.tuning_id, epoch_id=a.epoch_id,
            chain_hash=a.chain_hash, stability_class=a.stability_class,
            signed_baseband_hz=a.signed_baseband_hz, confidence=a.confidence)
        assert a_again is not a and a_again == a
        per_class = ((SESSION_SCOPED, 1), (RECONNECT_STABLE, 1),
                     (POWER_CYCLE_STABLE, 1))
        once = SpurAllocation(catalogue=catalogue, epochs=2,
                              per_stability_class=per_class,
                              eligible_trials=(a, b), selected_trials=(),
                              selection_seed=SEED)
        twice = SpurAllocation(catalogue=catalogue, epochs=2,
                               per_stability_class=per_class,
                               eligible_trials=(a, a_again, b), selected_trials=(),
                               selection_seed=SEED)
        return once, twice

    def test_a_repeated_record_is_canonicalised_out_of_storage(self):
        once, twice = self._pair()
        self.assertEqual(once.eligible_trials, twice.eligible_trials)
        self.assertEqual(len(twice.eligible_trials), 2)

    def test_a_repeated_record_does_not_move_the_trials_digest(self):
        once, twice = self._pair()
        self.assertEqual(once.eligible_trials_digest(),
                         twice.eligible_trials_digest())

    def test_a_repeated_record_does_not_move_the_plan_digest(self):
        envelope = _envelope()
        chains = sorted(envelope.admissible_chain_hashes())
        catalogue = _catalogue(45)
        eligible = _eligible_trials(catalogue, chains, 2,
                                    MINIMUM_WINDOWS_PER_STRATUM + 139)
        plain = _plan(envelope,
                      spur_allocation=_spur_allocation(eligible=eligible))
        doubled = _plan(envelope,
                        spur_allocation=_spur_allocation(
                            eligible=(eligible[0],) + eligible))
        self.assertEqual(plain.digest(), doubled.digest())

    def test_ordering_the_eligible_units_differently_changes_nothing(self):
        catalogue = _catalogue(45)
        chains = sorted(_envelope().admissible_chain_hashes())
        units = _eligible_trials(catalogue, chains, 2, 40)
        per_class = ((SESSION_SCOPED, 1), (RECONNECT_STABLE, 1),
                     (POWER_CYCLE_STABLE, 1))
        forward = SpurAllocation(catalogue=catalogue, epochs=2,
                                 per_stability_class=per_class,
                                 eligible_trials=units, selected_trials=(),
                                 selection_seed=SEED)
        backward = SpurAllocation(catalogue=catalogue, epochs=2,
                                  per_stability_class=per_class,
                                  eligible_trials=tuple(reversed(units)),
                                  selected_trials=(), selection_seed=SEED)
        self.assertEqual(forward.eligible_trials, backward.eligible_trials)
        self.assertEqual(forward.eligible_trials_digest(),
                         backward.eligible_trials_digest())


class SelectionIsPrecommittedTests(unittest.TestCase):
    """Membership is weaker than precommitment.

    With 5 700 eligible units and 5 561 needed, a corpus that only had to prove
    membership could pick whichever 5 561 produced convenient outcomes once the
    windows existed. That is post-hoc selection of the sample rather than of the
    threshold, and the frozen threshold exists to prevent exactly this move one
    object over.
    """

    def test_the_selection_is_derived_and_not_chosen(self):
        allocation = _plan().spur_allocation
        self.assertEqual(
            allocation.selected_trials,
            select_spur_trials(eligible=allocation.eligible_trials,
                               seed=allocation.selection_seed,
                               required=MINIMUM_WINDOWS_PER_STRATUM))

    def test_a_different_seed_selects_a_different_sample(self):
        allocation = _plan().spur_allocation
        other = select_spur_trials(eligible=allocation.eligible_trials,
                                   seed=allocation.selection_seed + 1,
                                   required=MINIMUM_WINDOWS_PER_STRATUM)
        self.assertNotEqual(set(other), set(allocation.selected_trials))

    def test_a_hand_picked_subset_of_the_same_universe_refuses(self):
        """The case membership checking cannot catch: every chosen identity is
        eligible, the count is exact, and the subset is not the frozen one."""
        good = _spur_allocation()
        unchosen = sorted({t.key() for t in good.eligible_trials}
                          - set(good.selected_trials))
        self.assertTrue(unchosen)
        swapped = tuple(sorted(
            list(good.selected_trials[1:]) + [unchosen[0]]))
        self.assertEqual(len(swapped), len(good.selected_trials))
        with self.assertRaises(EnvelopeRefused) as caught:
            SpurAllocation(catalogue=good.catalogue, epochs=good.epochs,
                           per_stability_class=good.per_stability_class,
                           eligible_trials=good.eligible_trials,
                           selected_trials=swapped,
                           selection_seed=good.selection_seed)
        self.assertEqual(caught.exception.code, PLAN_SELECTION_NOT_REPRODUCIBLE)

    def test_a_selection_of_the_wrong_size_refuses_at_the_plan(self):
        good = _spur_allocation()
        short = SpurAllocation(
            catalogue=good.catalogue, epochs=good.epochs,
            per_stability_class=good.per_stability_class,
            eligible_trials=good.eligible_trials,
            selected_trials=select_spur_trials(
                eligible=good.eligible_trials, seed=good.selection_seed,
                required=MINIMUM_WINDOWS_PER_STRATUM - 1),
            selection_seed=good.selection_seed)
        with self.assertRaises(EnvelopeRefused) as caught:
            _plan(spur_allocation=short)
        self.assertEqual(caught.exception.code, PLAN_SELECTION_NOT_REPRODUCIBLE)

    def test_an_unknown_selection_revision_refuses(self):
        good = _spur_allocation()
        with self.assertRaises(EnvelopeRefused) as caught:
            select_spur_trials(eligible=good.eligible_trials, seed=SEED,
                               required=10, selection_revision="other.v2")
        self.assertEqual(caught.exception.code, PLAN_SELECTION_NOT_REPRODUCIBLE)

    def test_a_universe_smaller_than_the_sample_refuses(self):
        good = _spur_allocation()
        with self.assertRaises(EnvelopeRefused) as caught:
            select_spur_trials(eligible=good.eligible_trials[:10], seed=SEED,
                               required=MINIMUM_WINDOWS_PER_STRATUM)
        self.assertEqual(caught.exception.code, PLAN_SPUR_NOT_FEASIBLE)


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
