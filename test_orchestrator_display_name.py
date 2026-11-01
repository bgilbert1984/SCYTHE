"""The name the orchestrator hands an instance for a new hub operator: a label only, never a role.

scythe_orchestrator.py starts services on import, so the one function is pulled out of the file and run on its own.
"""
import ast
import pathlib
import unittest

_source = pathlib.Path(__file__).with_name("scythe_orchestrator.py").read_text()
_node = next(n for n in ast.parse(_source).body if isinstance(n, ast.FunctionDef) and n.name == "_operator_display_name")
_namespace = {}
exec(compile(ast.Module([_node], []), "scythe_orchestrator.py", "exec"), _namespace)
name = _namespace["_operator_display_name"]


class DisplayName(unittest.TestCase):
    def test_prefers_the_claims_in_order(self):
        self.assertEqual(name({"preferred_username": "alex", "username": "u", "firstName": "f"}), "alex")
        self.assertEqual(name({"username": "u", "firstName": "f"}), "u")
        self.assertEqual(name({"firstName": "f"}), "f")

    def test_the_placeholder_and_blanks_are_not_names(self):
        self.assertIsNone(name({"firstName": "Operator"}))
        self.assertIsNone(name({"preferred_username": "   ", "firstName": ""}))
        self.assertIsNone(name({}))

    def test_falls_back_to_the_email_local_part(self):
        self.assertEqual(name({"email": "ben@spectrcyde.com"}), "ben")
        self.assertIsNone(name({"email": "@x.test"}))
        self.assertIsNone(name({"email": "not-an-email"}))

    def test_odd_types_and_length(self):
        self.assertIsNone(name({"preferred_username": 7, "firstName": ["a"]}))
        self.assertEqual(len(name({"preferred_username": "x" * 500})), 48)

    def test_role_claims_are_ignored(self):
        self.assertIsNone(name({"role": "admin", "roles": ["admin"]}))


if __name__ == "__main__":
    unittest.main()
