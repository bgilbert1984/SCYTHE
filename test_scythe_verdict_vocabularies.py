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

from scythe_promotion_ledger import (
    EXECUTABILITY_REFUSALS, MERIT_REFUSALS, NOT_YET_REACHABLE,
)

ROOT = os.path.dirname(os.path.abspath(__file__))

# Merit-side declarations, by module and name. Explicit rather than discovered:
# a scan that decided for itself which tuples were merit-side would be making
# the judgement the rule exists to force an author to make.
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
    for word in a:
        for prefix in NEGATIONS:
            if word.startswith(prefix) and word[len(prefix):] in b:
                return True
    for word in b:
        for prefix in NEGATIONS:
            if word.startswith(prefix) and word[len(prefix):] in a:
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

    def test_the_unreachable_set_is_declared_rather_than_assumed(self):
        self.assertEqual(NOT_YET_REACHABLE, ("IDENTITY_UNRESOLVED",))
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
