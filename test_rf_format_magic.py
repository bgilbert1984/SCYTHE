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


def _literal(module, name):
    """The declared value, read rather than imported.

    Importing would defeat the purpose: under the very collision these tests
    exist to witness, importing the chain is what raises.
    """
    for node in ast.parse(_source(module)).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError(f"{name} is not declared at module scope in {module}")


class DeclaredMagicTests(unittest.TestCase):

    def test_the_three_magics_are_declared_distinct(self):
        iqe = _literal(ARTEFACT, "IQE_MAGIC")
        iqm = _literal(MANIFEST, "IQM_MAGIC")
        iqc = _literal(WINDOW, "IQC_MAGIC")
        self.assertEqual(len({iqe, iqm, iqc}), 3)
        for name, value in (("IQE", iqe), ("IQM", iqm), ("IQC", iqc)):
            with self.subTest(magic=name):
                self.assertEqual(len(value), 8)
                self.assertTrue(value[0] & 0x80)
                self.assertTrue(value.endswith(b"\r\n"))


class CollisionRefusedAtImportTests(unittest.TestCase):
    """Each collision separately, named in the message it raises."""

    def _collide(self, other_value):
        source = _source(ARTEFACT)
        needle = 'IQE_MAGIC = b"\\x89SCYET\\r\\n"'
        self.assertIn(needle, source,
                      "the magic definition moved; this probe would be blind")
        shadow = tempfile.mkdtemp(prefix="scythe-magic-")
        self.addCleanup(shutil.rmtree, shadow, ignore_errors=True)
        with open(os.path.join(shadow, ARTEFACT), "w", encoding="utf-8") as out:
            out.write(source.replace(needle, "IQE_MAGIC = " + repr(other_value)))
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
