"""Whether a verdict may become a graph record. Step 1: the policy alone."""

import inspect
import itertools
import json
import unittest

import scythe_promotion_policy as policy_module
from scythe_invariant_ledger import (
    COMPARISON_DOMAIN_CHANGED, EVIDENCE_MISSING, INVARIANTS_SATISFIED,
    NUMERIC_BALANCE_EXCEEDED, PROHIBITED_CHANGE, REQUIRED_CHANGE_NOT_OBSERVED,
    Coordinate, InvariantVerdict, TransitionContract, check_transition,
    signature,
)
from scythe_promotion_policy import (
    AUTHORITY_INSUFFICIENT, CAPSULE_REVISION_UNSUPPORTED, CAPSULE_UNBOUND,
    DISPOSITIONS, DUPLICATE_PROMOTION, INDETERMINATE_AS_FAILURE,
    INVARIANT_FINDING, MODEL_RESPONSE_USED_AS_AUTHORITY, NO_PROMOTION_REQUESTED,
    OBSERVATION_GAP, POLICY_REVISION, PROMOTION_AUTHORITIES, PROMOTION_ELIGIBLE,
    PROMOTION_REFUSED, RECORD_CLASSES, REFUSALS, TARGET_UNSUPPORTED,
    VERDICT_NOT_PROMOTABLE, VERDICT_RECORD_CLASS, CapsuleIdentity,
    PromotionDecision, PromotionRequest, PromotionRequestError,
    decide_promotion, policy_status, promotion_identity, verdict_digest,
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


if __name__ == "__main__":
    unittest.main()
