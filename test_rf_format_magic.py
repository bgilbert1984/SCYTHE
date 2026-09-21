"""Cross-format magic distinctness, witnessed from OUTSIDE the modules involved.

A magic collision raises at import. A witness living in `test_rf_eligible_set`
or `test_rf_corpus_namespace` therefore disappears together with every other
test in those files, which is a detonation rather than a discrimination: the
two collisions produce identical failing sets and neither is told from the
other.

This file imports none of the chain --- not at module scope and not inside a
test. It reads the declared literals out of the source with `ast`, and performs
the colliding import in a subprocess against a shadowed copy of the module.
"""

import ast
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))

ARTEFACT = "rf_eligible_trials_artefact.py"
MANIFEST = "rf_corpus_manifest.py"
WINDOW = "rf_capture_format.py"


def _source(module):
    with open(os.path.join(HERE, module), encoding="utf-8") as handle:
        return handle.read()


def _literal(module, name, _seen=()):
    """The declared value, read rather than imported.

    Importing would defeat the purpose: under the very collision these tests
    exist to witness, importing the chain is what raises.

    An alias --- `IQE_MAGIC = IQC_MAGIC` --- is resolved rather than treated as
    an error. `ast.literal_eval` raises on a name reference, and a probe that
    raises reports the same failure whichever constant was aliased, which is
    how the two collisions came to share a witness.
    """
    for node in ast.parse(_source(module)).body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == name
                for target in node.targets):
            if isinstance(node.value, ast.Name):
                alias = node.value.id
                if alias in _seen:
                    raise AssertionError(f"{name} aliases in a cycle")
                return _visible(module, alias, _seen + (alias,))
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} is not declared at module scope in {module}")


def _visible(module, name, seen):
    """`name` as `module` sees it: declared there, or imported into it."""
    for node in ast.parse(_source(module)).body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == name
                for target in node.targets):
            return _literal(module, name, seen)
        if isinstance(node, ast.ImportFrom) and node.module and any(
                alias.name == name for alias in node.names):
            return _literal(node.module + ".py", name, seen)
    raise AssertionError(f"{name} is neither declared in nor imported into {module}")


def _rewrite_magic(source, value):
    """Replace the IQE_MAGIC declaration, located by AST rather than by text.

    An earlier version matched the literal `IQE_MAGIC = b"\\x89SCYET\\r\\n"`,
    which stops matching the moment IQE_MAGIC is mutated --- so under either
    collision the probe failed on its own "the definition moved" guard rather
    than on the collision, and the two mutations produced identical failing
    sets. Finding the assignment structurally cannot go blind that way.
    """
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "IQE_MAGIC"
                for t in node.targets):
            lines = source.splitlines(keepends=True)
            end = node.end_lineno or node.lineno
            lines[node.lineno - 1:end] = ["IQE_MAGIC = " + repr(value) + "\n"]
            return "".join(lines)
    raise AssertionError("IQE_MAGIC is not declared at module scope")


class DeclaredMagicTests(unittest.TestCase):
    """One test per PAIR. A single assertion over all three pairs gives every
    collision the same witness, which is how B1 and B2 stayed identical."""

    def test_the_sidecar_magic_differs_from_the_manifest_magic(self):
        self.assertNotEqual(_literal(ARTEFACT, "IQE_MAGIC"),
                            _literal(MANIFEST, "IQM_MAGIC"))

    def test_the_sidecar_magic_differs_from_the_window_magic(self):
        self.assertNotEqual(_literal(ARTEFACT, "IQE_MAGIC"),
                            _literal(WINDOW, "IQC_MAGIC"))

    def test_the_manifest_magic_differs_from_the_window_magic(self):
        self.assertNotEqual(_literal(MANIFEST, "IQM_MAGIC"),
                            _literal(WINDOW, "IQC_MAGIC"))

    def test_each_magic_has_the_shared_shape(self):
        for module, name in ((ARTEFACT, "IQE_MAGIC"), (MANIFEST, "IQM_MAGIC"),
                             (WINDOW, "IQC_MAGIC")):
            with self.subTest(magic=name):
                value = _literal(module, name)
                self.assertEqual(len(value), 8)
                self.assertTrue(value[0] & 0x80)
                self.assertTrue(value.endswith(b"\r\n"))


class CollisionRefusedAtImportTests(unittest.TestCase):
    """Each collision separately, named in the message it raises."""

    def _collide(self, other_value):
        shadow = tempfile.mkdtemp(prefix="scythe-magic-")
        self.addCleanup(shutil.rmtree, shadow, ignore_errors=True)
        with open(os.path.join(shadow, ARTEFACT), "w", encoding="utf-8") as out:
            out.write(_rewrite_magic(_source(ARTEFACT), other_value))
        probe = ("import sys\n"
                 "sys.path.insert(0, %r)\n"
                 "sys.path.append(%r)\n"
                 "try:\n"
                 "    import rf_corpus_manifest\n"
                 "except Exception as exc:\n"
                 "    print(type(exc).__name__ + ': ' + str(exc))\n"
                 "else:\n"
                 "    print('NO REFUSAL')\n") % (shadow, HERE)
        done = subprocess.run([sys.executable, "-c", probe], cwd=shadow,
                              capture_output=True, text=True, timeout=120)
        return (done.stdout + done.stderr).strip()

    def test_a_manifest_magic_collision_is_refused_at_import(self):
        message = self._collide(_literal(MANIFEST, "IQM_MAGIC"))
        self.assertNotIn("NO REFUSAL", message)
        self.assertIn("IQM_MAGIC", message,
                      "the manifest collision must name itself, or this "
                      "witness cannot be told from the window collision")

    def test_a_window_magic_collision_is_refused_at_import(self):
        message = self._collide(_literal(WINDOW, "IQC_MAGIC"))
        self.assertNotIn("NO REFUSAL", message)
        self.assertIn("IQC_MAGIC", message,
                      "the window collision must name itself, or this witness "
                      "cannot be told from the manifest collision")


if __name__ == "__main__":
    unittest.main()
