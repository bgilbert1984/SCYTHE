"""The mechanical step SCYTHE_VERDICT_VOCABULARIES.md §3 promises.

§3 requires that before a token is minted in either vocabulary, the name is
checked against the other across the tree, as a substring root. §3 also says
why: the collision it was written for was found *by looking*, and looking is not
repeatable -- "a future author needs a mechanical step they can follow without
having noticed the problem first."

This is that step. It reads **declared token tuples**, never raw source text,
which is the correction PENDING_AMENDMENTS.md entry 5 records: running the check
by grep reported FAILED as a collision, and the hit was English prose inside a
VERDICT_NOTES string. A token is something a module declared as a token.
"""

import ast
import os
import unittest
from typing import Dict

from scythe_promotion_ledger import (
    EXECUTABILITY_REFUSALS, MERIT_REFUSALS, NOT_YET_REACHABLE,
)

ROOT = os.path.dirname(os.path.abspath(__file__))

# Merit-side declarations, by module and name. Explicit, because deciding which
# tuples are *merit-side* is the judgement the rule exists to force an author to
# make. This list is the narrow set; it is NOT the universe a new name is
# checked against -- see DISCOVERED below for why that distinction cost a real
# collision.
MERIT_SOURCES = (
    ("scythe_promotion_policy", "REFUSALS"),
    ("scythe_promotion_policy", "DISPOSITIONS"),
    ("scythe_invariant_ledger", "COORDINATE_KINDS"),
    ("scythe_invariant_ledger", "COMPARISONS"),
    ("scythe_invariant_ledger", "VERDICT_PRECEDENCE"),
)

# Executability-side declarations. The ledger store's codes are listed here and
# NOT imported by the coordinator: the store stays disconnected until slice 6,
# and this file is a test, so reading both costs nothing at runtime.
EXECUTABILITY_SOURCES = (
    ("scythe_promotion_ledger", "EXECUTABILITY_REFUSALS"),
    ("scythe_promotion_ledger_store", "LEDGER_UNAVAILABLE"),
    ("scythe_promotion_ledger_store", "LEDGER_UNREADABLE"),
    ("scythe_promotion_ledger_store", "LEDGER_TORN"),
    ("scythe_promotion_ledger_store", "LOCK_EXCLUSION_UNATTESTED"),
    ("scythe_promotion_ledger_store", "RESERVATION_DURABILITY_UNATTESTED"),
    ("scythe_promotion_ledger_writer", "LEDGER_NOT_OWNED"),
    ("scythe_promotion_ledger_writer", "LEDGER_GENERATION_UNDECLARED"),
    ("scythe_promotion_ledger_writer", "OWNERSHIP_LOST"),
    ("scythe_promotion_ledger_writer", "RESERVATION_NOT_DURABLE"),
    ("scythe_promotion_lineage", "LINEAGE_REFUSALS"),
    ("scythe_promotion_reconciliation", "NOT_RECONCILABLE"),
    ("scythe_promotion_ceilings", "CEILING_REFUSALS"),
    ("scythe_graphops_adapter", "EVIDENCE_CODES"),
    ("scythe_graphops_adapter", "ADAPTER_NOT_CONFORMANT"),
)


def _module_tokens(module):
    """Every UPPER_SNAKE string a module declares at module level.

    Two passes, because the sets are declared by reference:

        VERDICT_NOT_PROMOTABLE = "VERDICT_NOT_PROMOTABLE"
        REFUSALS: Tuple[str, ...] = (VERDICT_NOT_PROMOTABLE, ...)

    A check that read only string literals would find REFUSALS empty and report
    no collisions -- passing loudly, which is the failure mode a name check can
    least afford. `_collect` asserts a named source is non-empty for that
    reason.

    Read from the AST rather than by import, so nothing here executes module
    code for the sake of a name.
    """
    with open(os.path.join(ROOT, f"{module}.py"), "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read())

    scalars = {}
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if (isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)):
                for target in targets:
                    if isinstance(target, ast.Name):
                        scalars[target.id] = node.value.value

    def literal(element):
        if isinstance(element, ast.Constant) and isinstance(element.value, str):
            return element.value
        if isinstance(element, ast.Name):
            return scalars.get(element.id)
        return None

    found = {}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = [t.id for t in targets if isinstance(t, ast.Name)]
        if not names:
            continue
        if isinstance(node.value, (ast.Tuple, ast.List)):
            values = [literal(e) for e in node.value.elts]
        else:
            values = [literal(node.value)]
        for name in names:
            for value in values:
                if value is not None and _is_token(value):
                    found.setdefault(name, []).append(value)
    return found


def _is_token(text):
    return bool(text) and text.replace("_", "").isalnum() and text.upper() == text


def components(token):
    return tuple(token.split("_"))


def is_substring_root(candidate, other):
    """Does one token's component sequence sit inside the other's?

    Component-level and in both directions, because the two collisions this
    repository has actually met came in opposite shapes: INDETERMINATE was a
    prefix of INDETERMINATE_AS_FAILURE, and UNVERIFIED was a suffix of
    LOCK_SEMANTICS_UNVERIFIED. A prefix-only check would have caught one of
    them, which is worse than catching neither -- it would have been trusted.

    Component-level rather than raw substring so that RESERVED does not collide
    with UNRESERVED_THING by accident of spelling. A token is a sequence of
    words, and containment is containment of words.
    """
    a, b = components(candidate), components(other)
    if len(a) > len(b):
        a, b = b, a
    return any(b[i:i + len(a)] == a for i in range(len(b) - len(a) + 1))


NEGATIONS = ("UN", "NON", "NOT")


def is_negation_pair(candidate, other):
    """Do the two differ only by a negating prefix on one shared word?

    VERIFIED_BY_FILESYSTEM_POLICY and UNVERIFIED share no component, so the
    substring-root check clears them -- and they are the worst kind of neighbour
    across two vocabularies, because each reads as the negation of the other.
    RESERVED and UNRESERVED are the same shape.

    Narrow on purpose. A raw character-level check would catch these and would
    also report every accidental spelling overlap, and a name check that cries
    wolf is a name check an author learns to skip -- which is the outcome §3
    exists to prevent.
    """
    a, b = set(components(candidate)), set(components(other))
    for one, two in ((a, b), (b, a)):
        # Shape 1: the negation is a prefix on a word. UNVERIFIED / VERIFIED.
        for word in one:
            for prefix in NEGATIONS:
                # The len() guard matters: a component that *is* the prefix --
                # the NOT in NOT_CREATED -- leaves an empty stem, and an empty
                # stem matches nothing meaningful.
                if (len(word) > len(prefix) and word.startswith(prefix)
                        and word[len(prefix):] in two):
                    return True
    # Shape 2: the negation is a word of its own. NOT_CREATED / CREATED_RECORD.
    # Found by writing a test for shape 1 and picking an example that turned
    # out to be the other shape, which is the more useful way to find it.
    for one, two in ((candidate, other), (other, candidate)):
        stripped = tuple(w for w in components(one) if w not in NEGATIONS)
        if len(stripped) == len(components(one)) or not stripped:
            continue
        whole = components(two)
        if any(whole[i:i + len(stripped)] == stripped
               for i in range(len(whole) - len(stripped) + 1)):
            return True
    return False


def collisions(candidate, tokens):
    return sorted({t for t in tokens
                   if is_substring_root(candidate, t)
                   or is_negation_pair(candidate, t)})


def _collect(sources):
    tokens = set()
    for module, name in sources:
        declared = _module_tokens(module)
        assert name in declared, f"{module}.{name} declares no tokens"
        tokens.update(declared[name])
    return tokens


# -- the discovered universe ----------------------------------------------
#
# A hand-listed universe is a check that stays silent about whatever nobody
# remembered to add, and this one was. `rf_capture_recovery` declares 21 tokens
# and MERIT_SOURCES named none of them, so `GENERATION_SUPERSEDED` was checked,
# reported clear, and collided with recovery's `SUPERSEDED`. It was caught by a
# person recognising a word -- the "found by looking" failure
# SCYTHE_VERDICT_VOCABULARIES.md §3 exists to replace.
#
# So the universe is discovered rather than listed. The cost is false positives
# against tuples that are not vocabularies at all, and §3's own rule says a
# check that cries wolf is one an author learns to skip. JUDGED is how that cost
# is paid without silencing the check: a hit is either a real collision or a
# recorded judgement with a reason, and there is no third state.

EXCLUDED_MODULES = frozenset({
    # Not vocabularies: configuration, schema plumbing, and generated aliases.
    "adaptive_schema_engine",
})


_DISCOVERED: Dict[frozenset, Dict[str, set]] = {}


def discovered_tokens(exclude=EXCLUDED_MODULES):
    """Every module-level token any non-test module in the tree declares.

    Memoized: it parses every module in the tree, and the answer cannot change
    inside one run.
    """
    key = frozenset(exclude)
    if key in _DISCOVERED:
        return _DISCOVERED[key]
    tokens = {}
    for entry in sorted(os.listdir(ROOT)):
        if not entry.endswith(".py") or entry.startswith("test_"):
            continue
        module = entry[:-3]
        if module in exclude:
            continue
        try:
            declared = _module_tokens(module)
        except SyntaxError:                 # pragma: no cover
            continue
        for name, values in declared.items():
            for value in values:
                tokens.setdefault(value, set()).add(f"{module}.{name}")
    _DISCOVERED[key] = tokens
    return tokens


# -- one word, or two? ----------------------------------------------------
#
# A set of token strings cannot tell *this word means one thing, declared in two
# places* from *this word means two things in two domains*. Collapsing them was
# not merely blindness: `cross_set_collisions` clears a hit when candidate and
# hit share a declaring set, and a token declared in several sets cleared
# against **any** of them -- so a genuine cross-domain neighbour could be
# skipped because the same word was also declared somewhere harmless. A false
# negative produced by the mechanism added to control false positives.
#
# So every token declared by more than one module carries a verdict here, and a
# TWO_CONCEPTS token never clears: resembling it is always a real neighbour.
ONE_CONCEPT = "ONE_CONCEPT"
TWO_CONCEPTS = "TWO_CONCEPTS"

DUPLICATE_DECLARATIONS = {
    # One concept, declared where it is produced and where it is recorded.
    "AUTHORIZATION_EXPIRED": (ONE_CONCEPT, "a recovery outcome, also declared as the audit event that records it"),
    "AUTHORIZATION_INVALIDATED": (ONE_CONCEPT, "a recovery outcome, also declared as the audit event that records it"),
    "PROCESS_RESTARTED_STILL_STARVED": (ONE_CONCEPT, "a recovery outcome, also declared as the audit event that records it"),
    "RECOVERY_OUTCOME_UNDETERMINED": (ONE_CONCEPT, "a recovery outcome, also declared as the audit event that records it"),
    "RECOVERY_SUPPRESSED": (ONE_CONCEPT, "a recovery outcome, also declared as the audit event that records it"),
    "RESTART_NOT_OBSERVED": (ONE_CONCEPT, "a recovery outcome, also declared as the audit event that records it"),
    "SAMPLE_FLOW_RESTORED": (ONE_CONCEPT, "a recovery outcome, also declared as the audit event that records it"),
    "RESTART_REQUEST_ACCEPTED": (ONE_CONCEPT, "a restart outcome, also declared as the audit event that records it"),
    "RESTART_REQUEST_FAILED": (ONE_CONCEPT, "a restart outcome, also declared as the audit event that records it"),
    "WOULD_BE_SUPPRESSED": (ONE_CONCEPT, "a shadow promotion judgement, also declared as the audit event that records it"),

    # One concept, shared by a reader and a writer of the same records.
    "RESERVED": (ONE_CONCEPT, "the coordinator's identity state and the ledger's record kind, deliberately one word (§13a B.6)"),
    "COMMITTED": (ONE_CONCEPT, "the coordinator's identity state and the ledger's record kind, deliberately one word (§13a B.6)"),
    "FAILED": (ONE_CONCEPT, "the coordinator's identity state and the ledger's record kind, deliberately one word (§13a B.6)"),
    "LEDGER_TORN": (ONE_CONCEPT, "a readability state, re-declared as the closure reason that cites it (§13e F.7)"),
    "LEDGER_UNREADABLE": (ONE_CONCEPT, "a readability state, re-declared as the closure reason that cites it (§13e F.7)"),

    # One concept, one vocabulary deliberately reused across subsystems.
    "ARMED": (ONE_CONCEPT, "the same three postures, applied to recovery and to promotion"),
    "SHADOW": (ONE_CONCEPT, "the same three postures, applied to recovery and to promotion"),
    "DISABLED": (ONE_CONCEPT, "the same three postures, applied to recovery and to promotion"),
    "GREATER_IS_STRONGER": (ONE_CONCEPT, "one statistic direction, declared by two family modules"),
    "REGISTERED_NOT_VALIDATED": (ONE_CONCEPT, "one validation status, declared by two family modules"),
    "STRUCTURE_CHANNEL": (ONE_CONCEPT, "one channel role, declared by the symbol clock and the manifest"),
    "NOISE_COMPATIBLE": (ONE_CONCEPT, "one finding -- consistent with noise -- declared by two analyzers"),
    "DIRECT_SAMPLING_CHANGE": (ONE_CONCEPT, "one event, invalidating the ring and unwiring retention"),
    "RECONNECT": (ONE_CONCEPT, "one action, declared by retention and by the ring"),
    "LOOPBACK_TCP": (ONE_CONCEPT, "one transport, declared by the bridge and by retention"),
    "NOT_DETERMINABLE_FROM_THIS_PROCESS": (ONE_CONCEPT, "one epistemic state about what this process can see"),
    "NOT_IMPLEMENTED": (ONE_CONCEPT, "a status marker meaning the same thing wherever it appears"),

    # One concept: Amendment B declared the three outcomes for the coordinator
    # and Amendment H's adapter declares the same three. Two WriteResult types
    # exist -- B's and H's superset -- and these are the field both are about.
    "CREATED": (ONE_CONCEPT, "the three write outcomes, declared by the coordinator (§13a B.1) and by the adapter that produces them (§13g H.3)"),
    "NOT_CREATED": (ONE_CONCEPT, "the three write outcomes, declared by the coordinator (§13a B.1) and by the adapter that produces them (§13g H.3)"),
    "UNKNOWN": (ONE_CONCEPT, "the three write outcomes, declared by the coordinator (§13a B.1) and by the adapter that produces them (§13g H.3)"),

    # Two concepts wearing one word. These never clear a collision.
    "UNVERIFIED": (TWO_CONCEPTS, "rf_receiver_state: an alignment state beside VERIFIED/BOUNDED/STALE. scythe_invariant_ledger: a coordinate that is present with its authority not established. This overload is why LOCK_SEMANTICS_UNVERIFIED was renamed"),
    "UNCHANGED": (TWO_CONCEPTS, "scythe_invariant_ledger: a coordinate that did not move. rf_capture_recovery: the same process is still there"),
    "NONE": (TWO_CONCEPTS, "scythe_promotion_ledger: nothing was asked. rf_bridge and rf_iq_ring: an absent value"),
    "UNAVAILABLE": (TWO_CONCEPTS, "scythe_promotion_ledger_store: a ledger that cannot be read, paired with AVAILABLE. rf_bridge and rf_iq_retention: a runtime attestation that could not be obtained"),
}


def declaring_modules(token, tokens):
    return {where.split(".")[0] for where in tokens.get(token, ())}


def duplicate_declarations(tokens):
    """Tokens declared by more than one module, with where."""
    return {token: sorted(where) for token, where in tokens.items()
            if len(declaring_modules(token, tokens)) > 1}


def cross_set_collisions(candidate, tokens):
    """Hits outside the candidate's own declared sets.

    Two tokens declared in the same tuple are **alternatives in one
    enumeration**, and resembling each other is what enumerations do:
    GRAPH_RECORD_FOUND and GRAPH_RECORD_NOT_FOUND are a negation pair on
    purpose, being the two answers to one question, and RECONCILED_COMMITTED
    sits in KNOWN_KINDS beside the COMMITTED it is named after.

    The rule is about **disjointness between the two vocabularies**, so a hit
    within one set is not what it is asking about. Found by the check firing on
    Amendment F's own evidence set, which is the right way to find it: the rule
    was stated for cross-set comparison and implemented against the whole tree.
    """
    mine = tokens.get(candidate, set())
    out = []
    for hit in collisions(candidate, set(tokens) - {candidate}):
        verdict = DUPLICATE_DECLARATIONS.get(hit, (None, ""))[0]
        if mine & tokens.get(hit, set()) and verdict != TWO_CONCEPTS:
            # Alternatives in one enumeration. Not cleared when the hit is one
            # word for two things: sharing a set with one of its meanings says
            # nothing about the other.
            continue
        out.append(hit)
    return sorted(out)


# Cross-set hits that are not collisions, each with the reason. Recorded so the
# check stays loud: an unjudged hit fails, and quieting one by renaming a token
# that did not need renaming is not available.
JUDGED = {
    ("GENERATION_CLOSED", "CLOSED"):
        "CLOSED is an rf_iq_ring buffer state -- a different subject in a "
        "different domain, and neither is a verdict vocabulary",
    ("DURABLE_CEILING_REACHED", "CEILING_REACHED"):
        "§13f G.8: CEILING_REACHED is the closure reason an operator cites "
        "when closing a generation the ceiling stopped. The two name the same "
        "event from the refusal side and the closure side, and renaming either "
        "would hide a link an operator needs",
    ("OUTSTANDING_RESERVATION_CEILING_REACHED", "CEILING_REACHED"):
        "§13f G.8: the same event from the refusal side and the closure side. "
        "This code names its subject rather than the state it counts, because "
        "UNRESOLVED is already doubled in this tree",
}


def judged(one, other):
    """A judgement is about a pair, not about an ordering.

    Keyed one way and read the other, a recorded judgement silently stops
    applying -- which is how the ceiling codes failed their own check after
    Amendment G had judged them.
    """
    return (one, other) in JUDGED or (other, one) in JUDGED


class CheckMechanismTests(unittest.TestCase):
    """The check itself, against the two collisions this repository has met."""

    def test_it_catches_the_indeterminate_collision(self):
        """Found by looking, in the document proposing the rule. Exact match
        would have passed it."""
        self.assertTrue(is_substring_root("INDETERMINATE", "INDETERMINATE_AS_FAILURE"))
        self.assertNotEqual("INDETERMINATE", "INDETERMINATE_AS_FAILURE")

    def test_it_catches_the_unverified_collision_which_is_a_suffix(self):
        """The second one arrived in the opposite shape. A prefix-only check
        would have caught the first and been trusted for the second."""
        self.assertTrue(
            is_substring_root("UNVERIFIED", "LOCK_SEMANTICS_UNVERIFIED"))

    def test_it_is_symmetric(self):
        self.assertTrue(is_substring_root("INDETERMINATE_AS_FAILURE", "INDETERMINATE"))

    def test_it_matches_words_and_not_spellings(self):
        """RESERVED inside UNRESERVED is not a shared component. A raw
        character-level check would report it as containment, and would report
        every accidental spelling overlap too."""
        self.assertFalse(is_substring_root("RESERVED", "UNRESERVED"))
        self.assertTrue(is_substring_root("RESERVED", "IDENTITY_RESERVED_HERE"))

    def test_a_negating_prefix_is_caught_by_the_second_check(self):
        """The pair the component check clears and should not. Two codes that
        read as each other's negation are the worst neighbours across two
        vocabularies, whatever their components say."""
        self.assertFalse(
            is_substring_root("VERIFIED_BY_FILESYSTEM_POLICY", "UNVERIFIED"))
        self.assertTrue(
            is_negation_pair("VERIFIED_BY_FILESYSTEM_POLICY", "UNVERIFIED"))
        self.assertTrue(is_negation_pair("RESERVED", "UNRESERVED"))
        self.assertEqual(collisions("VERIFIED_BY_FILESYSTEM_POLICY",
                                    {"UNVERIFIED"}), ["UNVERIFIED"])

    def test_the_negation_check_does_not_fire_on_unrelated_words(self):
        self.assertFalse(is_negation_pair("UNRESOLVED", "IDENTITY_RESERVED"))
        self.assertFalse(is_negation_pair("BUDGET_EXHAUSTED", "CAPSULE_UNBOUND"))

    def test_a_component_that_is_only_a_negating_prefix_leaves_no_stem(self):
        """NOT_CREATED carries a bare NOT. An unguarded check compares the
        empty string against the other token's components and is correct only
        by accident of what that set happens to hold."""
        self.assertFalse(is_negation_pair("NOT_CREATED", "CAPSULE_UNBOUND"))
        self.assertTrue(is_negation_pair("NOT_CREATED", "CREATED_RECORD"))

    def test_prose_in_a_notes_string_is_not_a_token(self):
        """Entry 5's false positive, pinned. The grep form of this check
        reported FAILED as a merit collision; the hit was inside a VERDICT_NOTES
        string reading THIS IS NOT A FAILED TRANSITION."""
        declared = _module_tokens("scythe_invariant_ledger")
        for name, tokens in declared.items():
            for token in tokens:
                self.assertNotIn(" ", token, f"{name} yielded prose: {token!r}")

    def test_a_declared_tuple_is_read_without_importing_the_module(self):
        self.assertIn("REFUSALS", _module_tokens("scythe_promotion_policy"))


class DiscoveryTests(unittest.TestCase):
    """The universe is found, not remembered."""

    def setUp(self):
        self.tokens = discovered_tokens()

    def test_recovery_is_in_the_universe(self):
        """The omission that cost a real collision. All 21, not the eight the
        merit list would have suggested."""
        recovery = {t for t, where in self.tokens.items()
                    if any(w.startswith("rf_capture_recovery.") for w in where)}
        self.assertGreaterEqual(len(recovery), 21)
        for token in ("SUPERSEDED", "UNCHANGED", "UNRELATED", "UNOBSERVABLE",
                      "RESTART_NOT_OBSERVED", "SAMPLE_FLOW_RESTORED",
                      "RECOVERY_OUTCOME_UNDETERMINED", "RECOVERY_OUTCOME_PENDING",
                      "PROCESS_RESTARTED_STILL_STARVED"):
            self.assertIn(token, recovery, token)

    def test_the_hand_listed_universe_is_a_strict_subset(self):
        self.assertTrue(_collect(MERIT_SOURCES) <= set(self.tokens))
        self.assertLess(len(_collect(MERIT_SOURCES)), len(self.tokens))

    def test_the_rejected_candidate_collides_against_the_discovered_universe(self):
        """`GENERATION_SUPERSEDED` was reported clear by the hand-listed check.
        This is the regression: the repair must catch it."""
        self.assertEqual(collisions("GENERATION_SUPERSEDED", set(self.tokens)),
                         ["SUPERSEDED"])
        self.assertEqual(collisions("GENERATION_SUPERSEDED",
                                    _collect(MERIT_SOURCES)), [])

    def test_the_accepted_replacement_is_clear_apart_from_its_judged_hit(self):
        self.assertEqual(cross_set_collisions("GENERATION_CLOSED", self.tokens),
                         ["CLOSED"])
        self.assertIn(("GENERATION_CLOSED", "CLOSED"), JUDGED)

    def test_alternatives_in_one_enumeration_are_not_collisions(self):
        """A closed set says the same thing several ways on purpose."""
        self.assertEqual(
            cross_set_collisions("GRAPH_RECORD_FOUND", self.tokens), [])
        self.assertIn("GRAPH_RECORD_NOT_FOUND",
                      collisions("GRAPH_RECORD_FOUND",
                                 set(self.tokens) - {"GRAPH_RECORD_FOUND"}))
        self.assertEqual(
            cross_set_collisions("RECONCILED_COMMITTED", self.tokens), [])

    def test_every_judgement_carries_a_reason(self):
        for pair, reason in JUDGED.items():
            self.assertTrue(len(reason) > 30, pair)

    def test_a_judgement_reads_in_both_directions(self):
        """Keyed one way and read the other, a judgement silently stops
        applying. That is how the ceiling codes failed their own check after
        Amendment G had already judged them."""
        for one, other in JUDGED:
            self.assertTrue(judged(one, other))
            self.assertTrue(judged(other, one))

    def test_the_ceiling_codes_are_judged_against_the_closure_reason(self):
        for code in ("DURABLE_CEILING_REACHED",
                     "OUTSTANDING_RESERVATION_CEILING_REACHED"):
            hits = cross_set_collisions(code, self.tokens)
            self.assertEqual(hits, ["CEILING_REACHED"])
            self.assertTrue(judged(code, "CEILING_REACHED"))

    def test_the_rejected_c2_name_is_absent_from_the_tree(self):
        """§13f G.8 renamed it rather than judging it: UNRESOLVED is already
        two concepts here, and a third would have been made permanent."""
        self.assertNotIn("UNRESOLVED_CEILING_REACHED", self.tokens)

    def test_a_judgement_is_only_recorded_for_a_real_hit(self):
        """A judgement for something that does not collide is a line nobody
        will ever remove, asserting a fact that was never true.

        This caught one on its first run: a judgement was recorded for
        `RECONCILED_RELEASED` against `RELEASED`, which nothing in the tree
        declares. The structural check alone passed it -- the two words do
        contain one another -- so the test also requires the hit to be a token
        that actually exists.
        """
        for candidate, hit in JUDGED:
            self.assertIn(hit, self.tokens, f"{hit} is declared nowhere")
            self.assertTrue(
                is_substring_root(candidate, hit) or is_negation_pair(candidate, hit),
                f"{candidate} does not actually hit {hit}")

    def test_amendment_f_tokens_are_clear_or_judged(self):
        """The sweep §13e records, run as a test rather than quoted."""
        for candidate in ("RECONCILED_COMMITTED", "RECONCILED_RELEASED",
                          "NOT_RECONCILABLE", "GENERATION_CHAIN_BROKEN",
                          "GENERATION_CLOSED", "GENERATION_LINEAGE_FORKED",
                          "GENERATION_PUBLICATION_UNCERTAIN",
                          "GRAPH_RECORD_FOUND", "GRAPH_RECORD_NOT_FOUND",
                          "ADAPTER_DENIED_CREATION", "CEILING_REACHED",
                          "DURABLE_CEILING_REACHED",
                          "OUTSTANDING_RESERVATION_CEILING_REACHED"):
            unjudged = [hit for hit in cross_set_collisions(candidate, self.tokens)
                        if not judged(candidate, hit)]
            self.assertEqual(unjudged, [], f"{candidate}: {unjudged}")


class ProvenanceTests(unittest.TestCase):
    """One token declared twice is a question, not a fact."""

    def setUp(self):
        self.tokens = discovered_tokens()
        self.duplicates = duplicate_declarations(self.tokens)

    def test_every_cross_module_duplicate_carries_a_verdict(self):
        """A new one fails here rather than passing quietly, which is the whole
        repair: the check cannot stay silent about what nobody added."""
        missing = sorted(set(self.duplicates) - set(DUPLICATE_DECLARATIONS))
        self.assertEqual(missing, [], f"undeclared duplicates: {missing}")

    def test_every_verdict_is_about_a_real_duplicate(self):
        stale = sorted(set(DUPLICATE_DECLARATIONS) - set(self.duplicates))
        self.assertEqual(stale, [], f"no longer duplicated: {stale}")

    def test_every_verdict_names_its_two_places(self):
        for token, (verdict, reason) in DUPLICATE_DECLARATIONS.items():
            self.assertIn(verdict, (ONE_CONCEPT, TWO_CONCEPTS), token)
            self.assertGreater(len(reason), 30, token)

    def test_a_two_concept_token_never_clears_a_collision(self):
        """Sharing a set with one meaning says nothing about the other."""
        two = [t for t, (v, _r) in DUPLICATE_DECLARATIONS.items()
               if v == TWO_CONCEPTS]
        self.assertTrue(two)
        for token in two:
            for where in self.tokens[token]:
                sibling_set = where
                siblings = [t for t, w in self.tokens.items()
                            if sibling_set in w and t != token]
                for sibling in siblings:
                    if is_substring_root(sibling, token) or is_negation_pair(sibling, token):
                        self.assertIn(token, cross_set_collisions(sibling, self.tokens))

    def test_the_overload_that_caused_a_rename_is_recorded(self):
        """UNVERIFIED is why LOCK_SEMANTICS_UNVERIFIED became
        LOCK_EXCLUSION_UNATTESTED, and it is two concepts, not one."""
        self.assertEqual(DUPLICATE_DECLARATIONS["UNVERIFIED"][0], TWO_CONCEPTS)
        self.assertIn("UNVERIFIED", self.duplicates)


class DisjointnessTests(unittest.TestCase):
    """The two sets, checked against each other as §3 requires."""

    def setUp(self):
        self.merit = _collect(MERIT_SOURCES)
        self.executability = _collect(EXECUTABILITY_SOURCES)

    def test_both_sets_are_non_empty(self):
        self.assertTrue(self.merit)
        self.assertTrue(self.executability)

    def test_no_executability_code_is_a_substring_root_of_a_merit_token(self):
        for code in sorted(self.executability):
            hits = collisions(code, self.merit)
            self.assertEqual(hits, [], f"{code} collides with merit {hits}")

    def test_no_merit_token_is_a_substring_root_of_an_executability_code(self):
        for token in sorted(self.merit):
            hits = collisions(token, self.executability)
            self.assertEqual(hits, [], f"{token} collides with executability {hits}")

    def test_the_sets_do_not_overlap_exactly_either(self):
        self.assertEqual(self.merit & self.executability, set())

    def test_duplicate_promotion_and_identity_unresolved_both_exist_and_differ(self):
        """§5's boundary case. Adjacent states of one identity, different sets:
        one is a fact about the subject's history, the other about the
        apparatus."""
        self.assertIn("DUPLICATE_PROMOTION", self.merit)
        self.assertIn("IDENTITY_UNRESOLVED", self.executability)
        self.assertFalse(
            is_substring_root("DUPLICATE_PROMOTION", "IDENTITY_UNRESOLVED"))

    def test_the_coordinators_declared_sets_are_the_ones_checked(self):
        self.assertTrue(set(MERIT_REFUSALS) <= self.merit)
        self.assertTrue(set(EXECUTABILITY_REFUSALS) <= self.executability)


class ReachabilityTests(unittest.TestCase):
    """A declared code nothing can return looks exactly like a bug."""

    def test_the_unreachable_set_emptied_when_slice_4_landed(self):
        """Slice 3 declared IDENTITY_UNRESOLVED unreachable and said this test
        would have to change when a reservation existed that could be
        unresolved. Slice 4 built one."""
        self.assertEqual(NOT_YET_REACHABLE, ())
        for code in NOT_YET_REACHABLE:
            self.assertIn(code, EXECUTABILITY_REFUSALS)

    def test_no_code_path_returns_an_unreachable_code_yet(self):
        """Slice 4 makes IDENTITY_UNRESOLVED reachable and must change this
        test. That is the point of it: the dependency is enforced rather than
        remembered."""
        with open(os.path.join(ROOT, "scythe_promotion_ledger.py"),
                  "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        returned = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
                for value in node.value.values:
                    if isinstance(value, ast.Name):
                        returned.add(value.id)
        self.assertEqual(returned & set(NOT_YET_REACHABLE), set())


if __name__ == "__main__":
    unittest.main()
