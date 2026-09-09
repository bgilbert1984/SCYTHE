"""Whether a verdict may become a graph record. Step 1: the policy alone."""

import inspect
import itertools
import json
import unittest

import scythe_promotion_policy as policy_module
from scythe_invariant_ledger import (
    COMPARISON_DOMAIN_CHANGED, COORDINATE_KINDS, EVIDENCE_MISSING,
    INVARIANTS_SATISFIED, NUMERIC_BALANCE_EXCEEDED, PROHIBITED_CHANGE,
    REQUIRED_CHANGE_NOT_OBSERVED, VALUE, Coordinate, Finding, InvariantVerdict,
    TransitionContract, check_transition, signature,
)
from scythe_promotion_policy import (
    AUTHORITY_INSUFFICIENT, CAPSULE_REVISION_UNSUPPORTED, CAPSULE_UNBOUND,
    DISPOSITIONS, DUPLICATE_PROMOTION, INDETERMINATE_AS_FAILURE,
    INVARIANT_FINDING, MODEL_RESPONSE_USED_AS_AUTHORITY, NO_PROMOTION_REQUESTED,
    OBSERVATION_GAP, POLICY_REVISION, PROMOTION_AUTHORITIES, PROMOTION_ELIGIBLE,
    PROMOTION_REFUSED, RECORD_CLASSES, REFUSALS, TARGET_UNSUPPORTED,
    PRIOR_PROMOTION_IDENTITY_COMPARABLE, VERDICT_DIGEST_REVISION,
    VERDICT_NOT_PROMOTABLE, VERDICT_RECORD_CLASS, CapsuleIdentity,
    PromotionDecision, PromotionIdentityError, PromotionRequest,
    PromotionRequestError, decide_promotion, policy_status, promotion_identity,
    verdict_digest,
)

CONTRACT = TransitionContract(name="T", must_preserve=("keep",),
                              must_change=("move",), domain_fields=("boot",))
CAPSULE = CapsuleIdentity(schema="scythe.invariant-capsule.v1",
                          digest="blake2s:aabbcc", within_bounds=True,
                          carries_samples=False)


def _verdict(kind):
    before = signature(boot="boot-a", keep="k", move=1)
    after = {
        PROHIBITED_CHANGE: signature(boot="boot-a", keep="other", move=2),
        REQUIRED_CHANGE_NOT_OBSERVED: signature(boot="boot-a", keep="k", move=1),
        COMPARISON_DOMAIN_CHANGED: signature(boot="boot-b", keep="k", move=2),
        INVARIANTS_SATISFIED: signature(boot="boot-a", keep="k", move=2),
    }[kind]
    if kind == EVIDENCE_MISSING:
        after = dict(signature(boot="boot-a", keep="k", move=2))
    verdict = check_transition(before, "T", after, CONTRACT)
    assert verdict.verdict == kind, (kind, verdict.verdict)
    return verdict


def _evidence_missing():
    before = signature(boot="boot-a", keep="k", move=1)
    after = dict(signature(boot="boot-a", move=2))
    after["keep"] = Coordinate("NOT_ASSESSED")
    verdict = check_transition(before, "T", after, CONTRACT)
    assert verdict.verdict == EVIDENCE_MISSING
    return verdict


def _request(**overrides):
    fields = dict(requested_by="OPERATOR",
                  target_graph="scythe.graphops.evidence",
                  justification_source="OPERATOR",
                  justification="operator reviewed the finding")
    fields.update(overrides)
    return PromotionRequest(**fields)


class NothingImplicitTests(unittest.TestCase):
    """Ingest is not intent."""

    def test_no_request_is_not_a_refusal(self):
        decision = decide_promotion(_verdict(PROHIBITED_CHANGE), None, CAPSULE)
        self.assertEqual(decision.disposition, NO_PROMOTION_REQUESTED)
        self.assertEqual(decision.refusals, ())
        self.assertIsNone(decision.record_class)

    def test_a_completed_check_alone_requests_nothing(self):
        for kind in (PROHIBITED_CHANGE, COMPARISON_DOMAIN_CHANGED,
                     INVARIANTS_SATISFIED):
            with self.subTest(kind=kind):
                self.assertEqual(
                    decide_promotion(_verdict(kind), None, CAPSULE).disposition,
                    NO_PROMOTION_REQUESTED)

    def test_the_policy_declares_that_nothing_is_implicit(self):
        self.assertEqual(policy_status()["implicit_requests"], "NONE")
        self.assertIn("INGEST IS NOT INTENT", policy_status()["implicit_note"])


class VerdictClassTests(unittest.TestCase):
    """Which verdicts become which records, and which become none."""

    def test_a_satisfied_invariant_is_not_a_graph_record(self):
        decision = decide_promotion(_verdict(INVARIANTS_SATISFIED),
                                    _request(), CAPSULE)
        self.assertEqual(decision.disposition, PROMOTION_REFUSED)
        self.assertIn(VERDICT_NOT_PROMOTABLE, decision.refusals)
        self.assertIn("CONFETTI", policy_status()["refusal_notes"][VERDICT_NOT_PROMOTABLE])

    def test_violations_become_invariant_findings(self):
        for kind in (PROHIBITED_CHANGE, REQUIRED_CHANGE_NOT_OBSERVED):
            with self.subTest(kind=kind):
                decision = decide_promotion(_verdict(kind), _request(), CAPSULE)
                self.assertEqual(decision.disposition, PROMOTION_ELIGIBLE)
                self.assertEqual(decision.record_class, INVARIANT_FINDING)

    def test_indeterminate_verdicts_become_observation_gaps(self):
        for verdict in (_verdict(COMPARISON_DOMAIN_CHANGED), _evidence_missing()):
            with self.subTest(verdict=verdict.verdict):
                decision = decide_promotion(verdict, _request(), CAPSULE)
                self.assertEqual(decision.disposition, PROMOTION_ELIGIBLE)
                self.assertEqual(decision.record_class, OBSERVATION_GAP)

    def test_an_indeterminate_verdict_may_not_be_promoted_as_a_finding(self):
        """A boot boundary recorded as a violation is a reboot counted as a failure."""
        for verdict in (_verdict(COMPARISON_DOMAIN_CHANGED), _evidence_missing()):
            with self.subTest(verdict=verdict.verdict):
                decision = decide_promotion(
                    verdict, _request(record_class=INVARIANT_FINDING), CAPSULE)
                self.assertEqual(decision.disposition, PROMOTION_REFUSED)
                self.assertIn(INDETERMINATE_AS_FAILURE, decision.refusals)

    def test_a_violation_may_not_be_understated_as_a_gap(self):
        decision = decide_promotion(_verdict(PROHIBITED_CHANGE),
                                    _request(record_class=OBSERVATION_GAP), CAPSULE)
        self.assertEqual(decision.disposition, PROMOTION_REFUSED)
        self.assertIn(VERDICT_NOT_PROMOTABLE, decision.refusals)

    def test_a_matching_requested_class_is_accepted(self):
        decision = decide_promotion(_verdict(PROHIBITED_CHANGE),
                                    _request(record_class=INVARIANT_FINDING), CAPSULE)
        self.assertEqual(decision.disposition, PROMOTION_ELIGIBLE)

    def test_the_class_table_covers_every_promotable_verdict_once(self):
        self.assertEqual(set(VERDICT_RECORD_CLASS.values()), set(RECORD_CLASSES))
        self.assertNotIn(INVARIANTS_SATISFIED, VERDICT_RECORD_CLASS)


class AuthorityTests(unittest.TestCase):
    """A model may be a justification's subject, never its source."""

    def test_a_model_sourced_justification_is_refused(self):
        decision = decide_promotion(_verdict(PROHIBITED_CHANGE),
                                    _request(justification_source="MODEL"), CAPSULE)
        self.assertEqual(decision.disposition, PROMOTION_REFUSED)
        self.assertIn(MODEL_RESPONSE_USED_AS_AUTHORITY, decision.refusals)

    def test_a_model_is_not_a_promotion_authority(self):
        self.assertNotIn("MODEL", PROMOTION_AUTHORITIES)
        self.assertFalse(policy_status()["model_is_an_authority"])

    def test_an_unrecognised_requester_is_refused(self):
        decision = decide_promotion(_verdict(PROHIBITED_CHANGE),
                                    _request(requested_by="ingest-pipeline"), CAPSULE)
        self.assertIn(AUTHORITY_INSUFFICIENT, decision.refusals)

    def test_both_declared_authorities_are_accepted(self):
        for who in PROMOTION_AUTHORITIES:
            with self.subTest(who=who):
                decision = decide_promotion(_verdict(PROHIBITED_CHANGE),
                                            _request(requested_by=who), CAPSULE)
                self.assertEqual(decision.disposition, PROMOTION_ELIGIBLE)

    def test_an_unknown_justification_source_is_refused_at_construction(self):
        with self.assertRaises(PromotionRequestError):
            _request(justification_source="A_HUNCH")


class CapsuleTests(unittest.TestCase):
    """The policy receives what a capsule is, never what it contains."""

    def test_a_missing_capsule_is_refused(self):
        decision = decide_promotion(_verdict(PROHIBITED_CHANGE), _request(), None)
        self.assertIn(CAPSULE_UNBOUND, decision.refusals)

    def test_an_unsupported_capsule_schema_is_refused(self):
        stale = CapsuleIdentity(schema="scythe.invariant-capsule.v0",
                                digest="blake2s:aa", within_bounds=True,
                                carries_samples=False)
        decision = decide_promotion(_verdict(PROHIBITED_CHANGE), _request(), stale)
        self.assertIn(CAPSULE_REVISION_UNSUPPORTED, decision.refusals)

    def test_an_out_of_bounds_or_sample_bearing_capsule_is_refused(self):
        for kwargs in ({"within_bounds": False}, {"carries_samples": True}):
            with self.subTest(**kwargs):
                bad = CapsuleIdentity(schema=CAPSULE.schema, digest=CAPSULE.digest,
                                      **{"within_bounds": True,
                                         "carries_samples": False, **kwargs})
                decision = decide_promotion(_verdict(PROHIBITED_CHANGE),
                                            _request(), bad)
                self.assertIn(CAPSULE_UNBOUND, decision.refusals)

    def test_the_identity_carries_no_capsule_contents(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(CapsuleIdentity)}
        self.assertEqual(fields, {"schema", "digest", "within_bounds",
                                  "carries_samples"})


class IdentityTests(unittest.TestCase):
    """Deterministic over exactly four things."""

    def test_repeated_evaluation_produces_the_same_key(self):
        verdict = _verdict(PROHIBITED_CHANGE)
        keys = {decide_promotion(verdict, _request(), CAPSULE).idempotency_key
                for _ in range(5)}
        self.assertEqual(len(keys), 1)

    def test_the_key_does_not_move_with_the_requester_or_justification(self):
        verdict = _verdict(PROHIBITED_CHANGE)
        first = decide_promotion(verdict, _request(), CAPSULE).idempotency_key
        second = decide_promotion(
            verdict, _request(requested_by="PROMOTION_POLICY",
                              justification_source="PROMOTION_POLICY",
                              justification="an entirely different reason"),
            CAPSULE).idempotency_key
        self.assertEqual(first, second)

    def test_the_key_moves_with_each_of_the_four_inputs(self):
        verdict = _verdict(PROHIBITED_CHANGE)
        base = promotion_identity(verdict, CAPSULE, "scythe.graphops.evidence")
        other_capsule = CapsuleIdentity(schema="scythe.invariant-capsule.v9",
                                        digest=CAPSULE.digest, within_bounds=True,
                                        carries_samples=False)
        self.assertNotEqual(base, promotion_identity(
            _verdict(REQUIRED_CHANGE_NOT_OBSERVED), CAPSULE,
            "scythe.graphops.evidence"))
        self.assertNotEqual(base, promotion_identity(
            verdict, other_capsule, "scythe.graphops.evidence"))
        self.assertNotEqual(base, promotion_identity(
            verdict, CAPSULE, "some.other.graph"))

    def test_a_duplicate_identity_is_refused(self):
        verdict = _verdict(PROHIBITED_CHANGE)
        key = decide_promotion(verdict, _request(), CAPSULE).idempotency_key
        decision = decide_promotion(verdict, _request(), CAPSULE,
                                    already_promoted=[key])
        self.assertEqual(decision.disposition, PROMOTION_REFUSED)
        self.assertIn(DUPLICATE_PROMOTION, decision.refusals)

    def test_the_verdict_digest_is_stable_and_content_sensitive(self):
        self.assertEqual(verdict_digest(_verdict(PROHIBITED_CHANGE)),
                         verdict_digest(_verdict(PROHIBITED_CHANGE)))
        self.assertNotEqual(verdict_digest(_verdict(PROHIBITED_CHANGE)),
                            verdict_digest(_verdict(REQUIRED_CHANGE_NOT_OBSERVED)))


class TargetTests(unittest.TestCase):
    def test_an_unsupported_target_graph_is_refused(self):
        decision = decide_promotion(_verdict(PROHIBITED_CHANGE),
                                    _request(target_graph="some.other.graph"),
                                    CAPSULE)
        self.assertIn(TARGET_UNSUPPORTED, decision.refusals)


class DecisionShapeTests(unittest.TestCase):
    """An invalid decision is unconstructable."""

    def test_an_eligible_decision_carries_no_refusal(self):
        with self.assertRaises(PromotionRequestError):
            PromotionDecision(PROMOTION_ELIGIBLE, record_class=INVARIANT_FINDING,
                              idempotency_key="k", refusals=(VERDICT_NOT_PROMOTABLE,))

    def test_an_eligible_decision_names_its_record_and_identity(self):
        with self.assertRaises(PromotionRequestError):
            PromotionDecision(PROMOTION_ELIGIBLE)

    def test_a_refusal_without_a_reason_is_unconstructable(self):
        with self.assertRaises(PromotionRequestError):
            PromotionDecision(PROMOTION_REFUSED)

    def test_nothing_asked_names_nothing(self):
        with self.assertRaises(PromotionRequestError):
            PromotionDecision(NO_PROMOTION_REQUESTED, record_class=INVARIANT_FINDING)

    def test_an_unknown_disposition_is_refused(self):
        with self.assertRaises(PromotionRequestError):
            PromotionDecision("PROBABLY_FINE")

    def test_refusals_accumulate_rather_than_stopping_at_the_first(self):
        decision = decide_promotion(
            _verdict(INVARIANTS_SATISFIED),
            _request(requested_by="nobody", justification_source="MODEL",
                     target_graph="elsewhere"), None)
        self.assertGreaterEqual(len(decision.refusals), 4)
        self.assertEqual(len(decision.refusals), len(set(decision.refusals)))

    def test_every_refusal_has_a_note(self):
        self.assertEqual(set(policy_status()["refusal_notes"]), set(REFUSALS))


class ScopeTests(unittest.TestCase):
    """Step 1 decides. It does not execute, ledger, or write."""

    def test_the_module_calls_nothing_that_could_write(self):
        import ast
        with open(inspect.getsourcefile(policy_module), encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                target = node.func
                if isinstance(target, ast.Name):
                    called.add(target.id)
                elif isinstance(target, ast.Attribute):
                    called.add(target.attr)
        for forbidden in ("commit", "write", "execute", "post", "send",
                          "publish", "ingest", "open", "run"):
            self.assertNotIn(forbidden, called, forbidden)

    def test_the_decision_carries_no_write_arguments(self):
        payload = decide_promotion(_verdict(PROHIBITED_CHANGE),
                                   _request(), CAPSULE).as_dict()
        self.assertFalse(payload["executes"])
        self.assertIn("CARRIES NO WRITE ARGUMENTS", payload["execution_note"])
        for value in payload.values():
            self.assertNotIsInstance(value, dict,
                                     "a free-form mapping is a write argument")

    def test_the_later_stages_are_declared_absent(self):
        status = policy_status()
        self.assertEqual(status["shadow_ledger"], "NOT_IMPLEMENTED")
        self.assertEqual(status["execution_adapter"], "NOT_IMPLEMENTED")
        self.assertEqual(status["side_effects"], "NONE")
        self.assertFalse(status["executes"])

    def test_payloads_are_serialisable(self):
        json.dumps(policy_status())
        json.dumps(decide_promotion(_verdict(PROHIBITED_CHANGE),
                                    _request(), CAPSULE).as_dict())

    def test_decisions_are_pure_across_repeated_calls(self):
        verdict = _verdict(PROHIBITED_CHANGE)
        first = decide_promotion(verdict, _request(), CAPSULE).as_dict()
        for _ in range(5):
            self.assertEqual(decide_promotion(verdict, _request(), CAPSULE).as_dict(),
                             first)


# -- promotion identity, v2 -----------------------------------------------

MOVED = TransitionContract(name="M", must_preserve=("chain",),
                           must_change=("seq",), domain_fields=("boot",))


def _moved(before_value, after_value, seq_to=2):
    """A PROHIBITED_CHANGE on `chain`, carrying real coordinates."""
    verdict = check_transition(
        signature(boot="boot-a", chain=before_value, seq=1), "M",
        signature(boot="boot-a", chain=after_value, seq=seq_to), MOVED)
    assert verdict.verdict == PROHIBITED_CHANGE, verdict.verdict
    return verdict


class PromotionIdentityCoordinateTests(unittest.TestCase):
    """The v1 digest hashed the shape of a disagreement, not its coordinates.

    For a PROHIBITED_CHANGE, `expected` and `observed` are the literals
    UNCHANGED and CHANGED, so every movement of one field hashed alike. The
    ledger then refused the second as a duplicate and a distinct finding was
    dropped silently. These tests hold the repair: a finding is bound to the
    canonical before and after coordinates the checker already recorded.
    """

    def test_two_movements_of_one_field_are_two_identities(self):
        """The defect, stated as the behaviour that replaces it.

        Under v1 these hashed identically, and the second promotion was
        refused as DUPLICATE_PROMOTION.
        """
        self.assertNotEqual(verdict_digest(_moved(100.1, 100.2)),
                            verdict_digest(_moved(100.2, 100.3)))

    def test_the_shape_of_the_disagreement_is_still_identical(self):
        """So the distinction above can only come from the coordinates."""
        first, second = _moved(100.1, 100.2), _moved(100.2, 100.3)
        shape = lambda v: [(f.verdict, f.field, f.expected, f.observed)
                           for f in v.findings]
        self.assertEqual(shape(first), shape(second))

    def test_re_evaluating_one_transition_gives_one_identity(self):
        digests = {verdict_digest(_moved(100.1, 100.2)) for _ in range(5)}
        self.assertEqual(len(digests), 1)

    def test_reversing_before_and_after_moves_the_identity(self):
        self.assertNotEqual(verdict_digest(_moved(100.1, 100.2)),
                            verdict_digest(_moved(100.2, 100.1)))

    def test_mapping_insertion_order_does_not_move_the_identity(self):
        one = _moved({"x": 0}, {"a": 1, "b": 2})
        other = _moved({"x": 0}, {"b": 2, "a": 1})
        self.assertEqual(verdict_digest(one), verdict_digest(other))
        # Not vacuous: the same two keys with a different value still differ.
        self.assertNotEqual(verdict_digest(one),
                            verdict_digest(_moved({"x": 0}, {"a": 1, "b": 3})))

    def test_sequence_order_does_move_the_identity(self):
        """For a sequence the order is content, unlike a mapping's."""
        self.assertNotEqual(verdict_digest(_moved(("x",), ("a", "b"))),
                            verdict_digest(_moved(("x",), ("b", "a"))))

    def test_every_governed_coordinate_kind_is_canonically_representable(self):
        encoded = {}
        for kind in COORDINATE_KINDS:
            coordinate = (Coordinate.of("a value") if kind == VALUE
                          else Coordinate(kind))
            encoded[kind] = policy_module._canonical_coordinate(
                coordinate.as_dict())
            json.dumps(encoded[kind])
        self.assertEqual(len(COORDINATE_KINDS), len(encoded))
        self.assertEqual(len(encoded), len({json.dumps(e) for e in encoded.values()}))

    def test_a_side_nobody_recorded_is_not_a_coordinate_saying_absent(self):
        """A prohibited claim has an after and no before. That is not ABSENT."""
        self.assertNotEqual(
            policy_module._canonical_coordinate(None),
            policy_module._canonical_coordinate(Coordinate("ABSENT").as_dict()))

    def test_container_families_are_canonical_rather_than_concrete_types(self):
        """list/tuple, set/frozenset and bytes/bytearray differ in mutability
        and in nothing a coordinate asserts. An identity that moved when a
        caller passed a tuple instead of a list would record the plumbing.
        """
        canon = policy_module._canonical_value
        self.assertEqual(canon(["a", "b"]), canon(("a", "b")))
        self.assertEqual(canon({"a"}), canon(frozenset({"a"})))
        self.assertEqual(canon(b"ab"), canon(bytearray(b"ab")))
        # The families stay apart from each other.
        self.assertNotEqual(canon(["a"]), canon({"a"}))
        self.assertNotEqual(canon(["a"]), canon({"a": None}))

    def test_values_that_share_a_display_form_are_kept_apart(self):
        """1, 1.0, True and "1" are four coordinates, not one repr()."""
        digests = {verdict_digest(_moved(0, value))
                   for value in (1, 1.0, True, "1")}
        self.assertEqual(len(digests), 4)

    def test_findings_are_ordered_canonically_not_by_discovery_order(self):
        """Identity describes the evidence set, not the order it arrived in."""
        one = Finding(PROHIBITED_CHANGE, "a", "UNCHANGED", "CHANGED",
                      before={"kind": VALUE, "value": 1},
                      after={"kind": VALUE, "value": 2})
        other = Finding(PROHIBITED_CHANGE, "b", "UNCHANGED", "CHANGED",
                        before={"kind": VALUE, "value": 3},
                        after={"kind": VALUE, "value": 4})
        self.assertEqual(
            verdict_digest(InvariantVerdict(PROHIBITED_CHANGE, "M", (one, other))),
            verdict_digest(InvariantVerdict(PROHIBITED_CHANGE, "M", (other, one))))

    def test_a_repeated_finding_is_not_collapsed_into_one(self):
        """Sorting is not de-duplication: two identical findings are two."""
        one = Finding(PROHIBITED_CHANGE, "a", "UNCHANGED", "CHANGED",
                      before={"kind": VALUE, "value": 1},
                      after={"kind": VALUE, "value": 2})
        self.assertNotEqual(
            verdict_digest(InvariantVerdict(PROHIBITED_CHANGE, "M", (one,))),
            verdict_digest(InvariantVerdict(PROHIBITED_CHANGE, "M", (one, one))))

    def test_a_kind_with_no_value_field_is_not_an_explicit_value_of_none(self):
        """Finding has no __post_init__, so a hand-built side arrives unchecked.

        Untrusted, {"kind": "VALUE"} would encode exactly like VALUE(None):
        two different inputs, one identity. The v1 defect by another door.
        """
        self.assertEqual(
            policy_module._canonical_coordinate({"kind": VALUE, "value": None}),
            ["value", ["null", ""]])
        with self.assertRaises(PromotionIdentityError):
            policy_module._canonical_coordinate({"kind": VALUE})

    def test_a_non_value_kind_may_not_smuggle_a_value(self):
        """Dropping it silently would hash away a contradiction."""
        with self.assertRaises(PromotionIdentityError):
            policy_module._canonical_coordinate(
                {"kind": "ABSENT", "value": "unexpected"})

    def test_an_unexpected_coordinate_field_is_refused_not_ignored(self):
        with self.assertRaises(PromotionIdentityError):
            policy_module._canonical_coordinate(
                {"kind": VALUE, "value": 1, "authority": "OPERATOR"})

    def test_a_finding_side_that_is_no_coordinate_at_all_is_refused(self):
        for side in ("VALUE", 1, ["kind", VALUE]):
            with self.subTest(side=side), self.assertRaises(PromotionIdentityError):
                policy_module._canonical_coordinate(side)

    def test_every_coordinate_the_ledger_builds_passes_validation(self):
        """The strictness must not refuse what check_transition produces."""
        for kind in COORDINATE_KINDS:
            coordinate = (Coordinate.of("v") if kind == VALUE
                          else Coordinate(kind))
            with self.subTest(kind=kind):
                policy_module._canonical_coordinate(coordinate.as_dict())

    def test_nan_has_no_promotion_identity(self):
        """hex() flattens every NaN to one token, and compare() calls NaN
        CHANGED -- so encoding it would collapse findings the checker told
        apart. NaN in an evidentiary coordinate is an undeclared absence
        wearing a lab coat; the ledger has five honest kinds for not knowing.
        """
        with self.assertRaises(PromotionIdentityError):
            verdict_digest(_moved(1.0, float("nan")))

    def test_the_infinities_have_no_promotion_identity(self):
        for value in (float("inf"), float("-inf")):
            with self.subTest(value=value), self.assertRaises(PromotionIdentityError):
                verdict_digest(_moved(1.0, value))

    def test_a_non_finite_float_inside_a_container_is_still_refused(self):
        with self.assertRaises(PromotionIdentityError):
            verdict_digest(_moved(("x",), ("a", {"b": [float("nan")]})))

    def test_finite_floats_that_share_a_decimal_form_stay_apart(self):
        """hex() is exact, so signed zero survives where decimal text may not."""
        self.assertNotEqual(verdict_digest(_moved("z", 0.0)),
                            verdict_digest(_moved("z", -0.0)))

    def test_a_value_with_no_canonical_encoding_is_refused_not_approximated(self):
        verdict = _moved("before", object())
        with self.assertRaises(PromotionIdentityError):
            verdict_digest(verdict)

    def test_an_identity_computation_terminates(self):
        deep = current = []
        for _ in range(policy_module.MAX_IDENTITY_DEPTH + 4):
            nested = []
            current.append(nested)
            current = nested
        with self.assertRaises(PromotionIdentityError):
            verdict_digest(_moved("before", deep))

    def test_the_identity_break_is_published_rather_than_migrated(self):
        status = policy_status()
        self.assertEqual(status["policy_revision"], "v2")
        self.assertEqual(status["verdict_digest_revision"], "v2")
        self.assertFalse(status["prior_promotion_identity_comparable"])
        self.assertFalse(PRIOR_PROMOTION_IDENTITY_COMPARABLE)
        self.assertEqual(VERDICT_DIGEST_REVISION, "v2")

    def test_the_digest_revision_is_bound_into_the_material(self):
        """A digest revision must move every key, or it is not a namespace."""
        verdict = _moved(100.1, 100.2)
        before = verdict_digest(verdict)
        original = policy_module.VERDICT_DIGEST_REVISION
        try:
            policy_module.VERDICT_DIGEST_REVISION = "v3"
            self.assertNotEqual(before, verdict_digest(verdict))
        finally:
            policy_module.VERDICT_DIGEST_REVISION = original

    def test_the_promotion_key_moves_with_the_coordinates(self):
        """End to end: the ledger's idempotency key, not just the digest."""
        target = "scythe.graphops.evidence"
        self.assertNotEqual(
            promotion_identity(_moved(100.1, 100.2), CAPSULE, target),
            promotion_identity(_moved(100.2, 100.3), CAPSULE, target))

    def test_two_movements_of_one_field_are_two_promotions(self):
        """The cost the collision used to impose, now absent."""
        first = decide_promotion(_moved(100.1, 100.2), _request(), CAPSULE)
        second = decide_promotion(_moved(100.2, 100.3), _request(), CAPSULE,
                                  already_promoted=[first.idempotency_key])
        self.assertEqual(second.disposition, PROMOTION_ELIGIBLE)
        self.assertNotIn(DUPLICATE_PROMOTION, second.refusals)


if __name__ == "__main__":
    unittest.main()
