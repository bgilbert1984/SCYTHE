"""A bounded model of promotion eligibility, written from the contract.

Same discipline as the recovery model, and for the same reason: a model written
*from the implementation* inherits the implementation's dimensions and is blind
in the same places. The state variables below come from what a promotion
decision claims to be about -- who asked, what the verdict permits, what the
capsule is, and whether this identity already exists -- not from what
``decide_promotion`` happens to hold in locals.

The model half imports nothing from ``scythe_promotion_policy``. The
dispositions and refusals are retyped; a test asserts the retyping matches and
that the model half stays import-free. If they could not be retyped without
ambiguity, that would itself be a finding.

Scope is verdict-to-record eligibility and promotion identity. Nothing about
execution is modelled, because nothing executes yet and a model that grew ahead
of the code would be a second artifact to keep in step.
"""

import itertools
import unittest


# ---------------------------------------------------------------------------
# MODEL -- retyped from the contract. Nothing below is imported from the code.
# ---------------------------------------------------------------------------

M_NO_REQUEST = "NO_PROMOTION_REQUESTED"
M_ELIGIBLE = "PROMOTION_ELIGIBLE"
M_REFUSED = "PROMOTION_REFUSED"
DISPOSITIONS = (M_NO_REQUEST, M_ELIGIBLE, M_REFUSED)

M_FINDING = "INVARIANT_FINDING"
M_GAP = "OBSERVATION_GAP"

R_NOT_PROMOTABLE = "VERDICT_NOT_PROMOTABLE"
R_INDETERMINATE = "INDETERMINATE_AS_FAILURE"
R_CAPSULE_UNBOUND = "CAPSULE_UNBOUND"
R_CAPSULE_REVISION = "CAPSULE_REVISION_UNSUPPORTED"
R_MODEL_AUTHORITY = "MODEL_RESPONSE_USED_AS_AUTHORITY"
R_DUPLICATE = "DUPLICATE_PROMOTION"
R_AUTHORITY = "AUTHORITY_INSUFFICIENT"
R_TARGET = "TARGET_UNSUPPORTED"
REFUSALS = (R_NOT_PROMOTABLE, R_INDETERMINATE, R_CAPSULE_UNBOUND,
            R_CAPSULE_REVISION, R_MODEL_AUTHORITY, R_DUPLICATE, R_AUTHORITY,
            R_TARGET)

# State axes, chosen from what a decision is about.
REQUESTED = (False, True)
REQUESTERS = ("AUTHORISED", "UNAUTHORISED")
JUSTIFICATIONS = ("NON_MODEL", "MODEL")
TARGETS = ("SUPPORTED", "UNSUPPORTED")
# Collapsed so impossible combinations do not exist: a capsule cannot be both
# absent and badly versioned.
CAPSULES = ("ABSENT", "BAD_SCHEMA", "UNBOUNDED", "BAD_SCHEMA_AND_UNBOUNDED", "GOOD")
# What the verdict permits, which is the only thing about the verdict that
# matters here.
VERDICT_CLASSES = ("NONE", M_FINDING, M_GAP)
REQUESTED_CLASSES = ("UNSTATED", M_FINDING, M_GAP)
ALREADY = (False, True)


class State(tuple):
    FIELDS = ("requested", "requester", "justification", "target", "capsule",
              "verdict_class", "requested_class", "already_promoted")

    def __new__(cls, **kwargs):
        return super().__new__(cls, tuple(kwargs[f] for f in cls.FIELDS))

    def __getattr__(self, name):
        try:
            return self[self.FIELDS.index(name)]
        except ValueError:
            raise AttributeError(name)


def all_states():
    for combo in itertools.product(REQUESTED, REQUESTERS, JUSTIFICATIONS,
                                   TARGETS, CAPSULES, VERDICT_CLASSES,
                                   REQUESTED_CLASSES, ALREADY):
        yield State(**dict(zip(State.FIELDS, combo)))


def model_refusals(state):
    """Every refusal this state earns, in no particular order."""
    if not state.requested:
        return frozenset()
    found = set()
    if state.requester == "UNAUTHORISED":
        found.add(R_AUTHORITY)
    if state.justification == "MODEL":
        found.add(R_MODEL_AUTHORITY)
    if state.target == "UNSUPPORTED":
        found.add(R_TARGET)
    if state.capsule == "ABSENT":
        found.add(R_CAPSULE_UNBOUND)
    else:
        if state.capsule in ("BAD_SCHEMA", "BAD_SCHEMA_AND_UNBOUNDED"):
            found.add(R_CAPSULE_REVISION)
        if state.capsule in ("UNBOUNDED", "BAD_SCHEMA_AND_UNBOUNDED"):
            found.add(R_CAPSULE_UNBOUND)
    if state.verdict_class == "NONE":
        found.add(R_NOT_PROMOTABLE)
    elif (state.requested_class != "UNSTATED"
          and state.requested_class != state.verdict_class):
        if state.verdict_class == M_GAP and state.requested_class == M_FINDING:
            found.add(R_INDETERMINATE)
        else:
            found.add(R_NOT_PROMOTABLE)
    # An identity exists only when there is a capsule and a promotable verdict.
    if (state.capsule != "ABSENT" and state.verdict_class != "NONE"
            and state.already_promoted):
        found.add(R_DUPLICATE)
    return frozenset(found)


def model_decision(state):
    """Disposition and record class implied by the contract for this state."""
    if not state.requested:
        return M_NO_REQUEST, None
    refusals = model_refusals(state)
    if refusals:
        return M_REFUSED, None
    return M_ELIGIBLE, state.verdict_class


# ---------------------------------------------------------------------------
# Properties of the model
# ---------------------------------------------------------------------------

class ModelTests(unittest.TestCase):

    def setUp(self):
        self.states = list(all_states())

    def test_the_space_is_the_size_it_claims(self):
        self.assertEqual(len(self.states), 2 * 2 * 2 * 2 * 5 * 3 * 3 * 2)
        self.assertEqual(len(set(self.states)), len(self.states))

    def test_every_disposition_is_reachable(self):
        reached = {model_decision(s)[0] for s in self.states}
        self.assertEqual(reached, set(DISPOSITIONS))

    def test_every_refusal_is_reachable(self):
        reached = set()
        for state in self.states:
            reached |= model_refusals(state)
        for refusal in REFUSALS:
            self.assertIn(refusal, reached, f"{refusal} is unreachable")

    def test_every_state_yields_exactly_one_disposition(self):
        for state in self.states:
            disposition, _ = model_decision(state)
            self.assertIn(disposition, DISPOSITIONS, state)

    def test_nothing_requested_is_never_a_refusal(self):
        """The absence of an ask is not a rejection of one."""
        for state in self.states:
            if not state.requested:
                self.assertEqual(model_decision(state), (M_NO_REQUEST, None), state)
                self.assertEqual(model_refusals(state), frozenset(), state)

    def test_eligible_implies_no_refusal_and_the_verdicts_own_class(self):
        for state in self.states:
            disposition, record = model_decision(state)
            if disposition == M_ELIGIBLE:
                self.assertEqual(model_refusals(state), frozenset(), state)
                self.assertEqual(record, state.verdict_class, state)
                self.assertIn(record, (M_FINDING, M_GAP), state)

    def test_refused_implies_at_least_one_reason(self):
        for state in self.states:
            if model_decision(state)[0] == M_REFUSED:
                self.assertTrue(model_refusals(state), state)

    def test_a_satisfied_verdict_is_never_eligible(self):
        """Normal operation writes nothing. No exception anywhere in the space."""
        for state in self.states:
            if state.verdict_class == "NONE" and state.requested:
                self.assertEqual(model_decision(state)[0], M_REFUSED, state)

    def test_a_gap_is_never_eligible_as_a_finding(self):
        """A boot boundary recorded as a violation is a reboot counted as one."""
        for state in self.states:
            if (state.requested and state.verdict_class == M_GAP
                    and state.requested_class == M_FINDING):
                self.assertEqual(model_decision(state)[0], M_REFUSED, state)
                self.assertIn(R_INDETERMINATE, model_refusals(state), state)

    def test_a_finding_is_never_eligible_as_a_gap(self):
        """Understating a violation is refused for the same reason overstating is."""
        for state in self.states:
            if (state.requested and state.verdict_class == M_FINDING
                    and state.requested_class == M_GAP):
                self.assertEqual(model_decision(state)[0], M_REFUSED, state)

    def test_a_model_justification_is_never_eligible(self):
        for state in self.states:
            if state.requested and state.justification == "MODEL":
                self.assertEqual(model_decision(state)[0], M_REFUSED, state)

    def test_an_already_promoted_identity_is_never_eligible_again(self):
        for state in self.states:
            if (state.requested and state.already_promoted
                    and state.capsule != "ABSENT" and state.verdict_class != "NONE"):
                self.assertIn(R_DUPLICATE, model_refusals(state), state)

    def test_an_absent_capsule_is_never_eligible(self):
        for state in self.states:
            if state.requested and state.capsule == "ABSENT":
                self.assertEqual(model_decision(state)[0], M_REFUSED, state)

    def test_a_doubly_bad_capsule_earns_both_capsule_refusals(self):
        for state in self.states:
            if state.requested and state.capsule == "BAD_SCHEMA_AND_UNBOUNDED":
                refusals = model_refusals(state)
                self.assertIn(R_CAPSULE_REVISION, refusals, state)
                self.assertIn(R_CAPSULE_UNBOUND, refusals, state)

    def test_refusals_accumulate_across_independent_faults(self):
        state = State(requested=True, requester="UNAUTHORISED",
                      justification="MODEL", target="UNSUPPORTED",
                      capsule="ABSENT", verdict_class="NONE",
                      requested_class="UNSTATED", already_promoted=False)
        self.assertGreaterEqual(len(model_refusals(state)), 5)


# ---------------------------------------------------------------------------
# DIFFERENTIAL -- the implementation, over the same space.
# ---------------------------------------------------------------------------

CONTRACT_FIELDS = ("keep", "move", "boot")


def _build(state):
    """One abstract state, realised as the objects the implementation expects."""
    from scythe_invariant_ledger import (
        Coordinate, TransitionContract, check_transition, signature,
    )
    from scythe_promotion_policy import CapsuleIdentity, PromotionRequest
    contract = TransitionContract(name="T", must_preserve=("keep",),
                                  must_change=("move",), domain_fields=("boot",))
    before = signature(boot="boot-a", keep="k", move=1)
    after = {
        "NONE": signature(boot="boot-a", keep="k", move=2),
        M_FINDING: signature(boot="boot-a", keep="k", move=1),
        M_GAP: signature(boot="boot-b", keep="k", move=2),
    }[state.verdict_class]
    verdict = check_transition(before, "T", after, contract)

    capsule = None
    if state.capsule != "ABSENT":
        schema = ("scythe.invariant-capsule.v1"
                  if state.capsule in ("UNBOUNDED", "GOOD")
                  else "scythe.invariant-capsule.v0")
        bounded = state.capsule in ("BAD_SCHEMA", "GOOD")
        capsule = CapsuleIdentity(schema=schema, digest="blake2s:aabbcc",
                                  within_bounds=bounded, carries_samples=False)
    request = None
    if state.requested:
        request = PromotionRequest(
            requested_by=("OPERATOR" if state.requester == "AUTHORISED"
                          else "ingest-pipeline"),
            target_graph=("scythe.graphops.evidence"
                          if state.target == "SUPPORTED" else "some.other.graph"),
            justification_source=("OPERATOR" if state.justification == "NON_MODEL"
                                  else "MODEL"),
            record_class=(None if state.requested_class == "UNSTATED"
                          else state.requested_class))
    return verdict, request, capsule


def implementation_decision(state):
    from scythe_promotion_policy import decide_promotion, promotion_identity
    verdict, request, capsule = _build(state)
    already = ()
    if state.already_promoted and capsule is not None and request is not None:
        already = (promotion_identity(verdict, capsule, request.target_graph),)
    decision = decide_promotion(verdict, request, capsule, already_promoted=already)
    return decision.disposition, decision.record_class, frozenset(decision.refusals)


class DifferentialTests(unittest.TestCase):
    """Two independent derivations, over all 1440 states."""

    def test_the_model_and_the_implementation_agree_everywhere(self):
        divergences = []
        for state in all_states():
            expected = model_decision(state) + (model_refusals(state),)
            actual = implementation_decision(state)
            if expected != actual:
                divergences.append((dict(zip(State.FIELDS, state)), expected, actual))
        self.assertEqual(divergences[:3], [],
                         f"{len(divergences)} divergent states")

    def test_the_vocabulary_survives_retyping(self):
        from scythe_promotion_policy import DISPOSITIONS as CODE_DISPOSITIONS
        from scythe_promotion_policy import RECORD_CLASSES, REFUSALS as CODE_REFUSALS
        self.assertEqual(set(DISPOSITIONS), set(CODE_DISPOSITIONS))
        self.assertEqual(set(REFUSALS), set(CODE_REFUSALS))
        self.assertEqual({M_FINDING, M_GAP}, set(RECORD_CLASSES))

    def test_the_model_half_imports_nothing_from_the_code(self):
        import inspect
        source = inspect.getsource(inspect.getmodule(self))
        model_half = source.split("# DIFFERENTIAL")[0]
        for forbidden in ("scythe_promotion_policy", "scythe_invariant_ledger",
                          "scythe_invariant_capsule"):
            self.assertNotIn(forbidden, model_half.split('"""', 2)[2],
                             "the model must be written from the contract")

    def test_the_identity_is_stable_across_the_whole_space(self):
        """Re-evaluation never produces a second key for one state."""
        from scythe_promotion_policy import promotion_identity
        for state in all_states():
            if state.capsule == "ABSENT" or not state.requested:
                continue
            verdict, request, capsule = _build(state)
            keys = {promotion_identity(verdict, capsule, request.target_graph)
                    for _ in range(3)}
            self.assertEqual(len(keys), 1, state)


class MutationTests(unittest.TestCase):
    """A model that cannot fail is not checking anything."""

    def test_permitting_a_gap_as_a_finding_diverges_and_breaks_a_property(self):
        gap_as_finding = [s for s in all_states()
                          if s.requested and s.verdict_class == M_GAP
                          and s.requested_class == M_FINDING]
        self.assertTrue(gap_as_finding)
        # The property that would fail if the rule were dropped.
        for state in gap_as_finding:
            self.assertIn(R_INDETERMINATE, model_refusals(state))
        # And the implementation agrees, so dropping it on either side diverges.
        for state in gap_as_finding[:8]:
            _disposition, _record, refusals = implementation_decision(state)
            self.assertIn(R_INDETERMINATE, refusals)

    def test_permitting_a_satisfied_verdict_would_break_reachability(self):
        satisfied = [s for s in all_states()
                     if s.requested and s.verdict_class == "NONE"]
        self.assertTrue(satisfied)
        for state in satisfied[:8]:
            self.assertIn(R_NOT_PROMOTABLE, model_refusals(state))
            self.assertIn(R_NOT_PROMOTABLE, implementation_decision(state)[2])


if __name__ == "__main__":
    unittest.main()
