"""§5.20 correction A: one geometry, one home, and no way to reach it impurely.

The module under test declares constants. What is worth testing is not that
524 288 equals 524 288 -- it is that the *derived* values are derived, that a
deviation is reported by name, and that reading the geometry costs nothing: no
NumPy, no threading, no 4 MB allocation, no ring.
"""

import ast
import pathlib
import subprocess
import sys
import unittest

import rf_promotion_geometry as geometry
from rf_promotion_geometry import (
    PROMOTION_BYTES_PER_SAMPLE, PROMOTION_CYCLE_RESOLUTION_HZ, PROMOTION_DTYPE,
    PROMOTION_PAYLOAD_BYTES, PROMOTION_SAMPLE_RATE_HZ, PROMOTION_WINDOW_MS,
    PROMOTION_WINDOW_OVERLAP, PROMOTION_WINDOW_SAMPLES,
    is_promotion_geometry, promotion_geometry, promotion_geometry_deviations,
)

REPO = pathlib.Path(__file__).resolve().parent


def _imported_modules(path):
    """Every module name this file imports, without importing it."""
    tree = ast.parse(pathlib.Path(path).read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


class GeometryTests(unittest.TestCase):
    """The frozen promotion window, and the values that follow from it."""

    def test_the_geometry_is_the_approved_one(self):
        self.assertEqual(PROMOTION_SAMPLE_RATE_HZ, 2_048_000.0)
        self.assertEqual(PROMOTION_WINDOW_SAMPLES, 524_288)
        self.assertEqual(PROMOTION_DTYPE, "complex64")
        self.assertEqual(PROMOTION_BYTES_PER_SAMPLE, 8)
        self.assertEqual(PROMOTION_WINDOW_OVERLAP, "NONE")

    def test_the_derived_values_equal_their_derivations(self):
        """256 ms and 3.90625 Hz agree with the rate and the length."""
        self.assertEqual(
            PROMOTION_WINDOW_MS,
            1000.0 * PROMOTION_WINDOW_SAMPLES / PROMOTION_SAMPLE_RATE_HZ)
        self.assertEqual(
            PROMOTION_CYCLE_RESOLUTION_HZ,
            PROMOTION_SAMPLE_RATE_HZ / PROMOTION_WINDOW_SAMPLES)
        self.assertEqual(PROMOTION_PAYLOAD_BYTES,
                         PROMOTION_WINDOW_SAMPLES * PROMOTION_BYTES_PER_SAMPLE)

    def test_the_derived_values_are_computed_rather_than_transcribed(self):
        """The check the equality above cannot make.

        `PROMOTION_WINDOW_MS = 256.0` satisfies every arithmetic assertion in
        this file: the literal is *correct today*. It stops being correct the
        moment the sample count moves, and the equality test would not notice
        until someone changed the count and the literal in the same edit --
        which is the drift this module was written to end.

        So the property is about the **assignment**, and this reads it: each
        derived name must be bound to an expression, never to a constant.
        A negative control that replaced the expression with the numerically
        identical literal was the thing that showed the earlier test could not
        tell the difference.
        """
        tree = ast.parse((REPO / "rf_promotion_geometry.py").read_text())
        assigned = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                    and isinstance(node.targets[0], ast.Name):
                assigned[node.targets[0].id] = node.value
        for name in ("PROMOTION_WINDOW_MS", "PROMOTION_CYCLE_RESOLUTION_HZ",
                     "PROMOTION_PAYLOAD_BYTES"):
            with self.subTest(name=name):
                self.assertIn(name, assigned)
                self.assertNotIsInstance(
                    assigned[name], ast.Constant,
                    f"{name} is a transcribed literal, not a derivation")
                self.assertIsInstance(assigned[name], ast.BinOp)

    def test_the_derived_values_are_exact(self):
        """Both divisions are powers of two, so neither rounds."""
        self.assertEqual(PROMOTION_WINDOW_MS, 256.0)
        self.assertEqual(PROMOTION_CYCLE_RESOLUTION_HZ, 3.90625)
        self.assertEqual(PROMOTION_PAYLOAD_BYTES, 4_194_304)

    def test_the_declaration_carries_every_constant(self):
        declared = promotion_geometry()
        self.assertEqual(declared["window_samples"], PROMOTION_WINDOW_SAMPLES)
        self.assertEqual(declared["payload_bytes"], PROMOTION_PAYLOAD_BYTES)
        self.assertEqual(declared["schema"], geometry.GEOMETRY_SCHEMA)


class DeviationTests(unittest.TestCase):
    """A deviation is named, and all of them are."""

    def test_the_promotion_geometry_conforms_to_itself(self):
        self.assertEqual(
            promotion_geometry_deviations(
                sample_rate_hz=PROMOTION_SAMPLE_RATE_HZ,
                window_samples=PROMOTION_WINDOW_SAMPLES), ())
        self.assertTrue(is_promotion_geometry(
            sample_rate_hz=PROMOTION_SAMPLE_RATE_HZ,
            window_samples=PROMOTION_WINDOW_SAMPLES))

    def test_the_superseded_length_deviates(self):
        """262 144 is not a smaller promotion geometry. It is not one."""
        self.assertEqual(
            promotion_geometry_deviations(
                sample_rate_hz=PROMOTION_SAMPLE_RATE_HZ,
                window_samples=262_144), ("window_samples",))

    def test_every_deviating_field_is_reported_not_just_the_first(self):
        """A caller told about one field fixes it and learns about the next."""
        self.assertEqual(
            promotion_geometry_deviations(
                sample_rate_hz=1_024_000.0, window_samples=262_144,
                dtype="complex128", overlap="HALF"),
            ("sample_rate_hz", "window_samples", "dtype", "overlap"))

    def test_a_boolean_length_is_not_a_length(self):
        """`True == 1` and `isinstance(True, int)`; a nominal check is why."""
        self.assertIn("window_samples", promotion_geometry_deviations(
            sample_rate_hz=PROMOTION_SAMPLE_RATE_HZ, window_samples=True))

    def test_a_boolean_rate_is_not_a_rate(self):
        self.assertIn("sample_rate_hz", promotion_geometry_deviations(
            sample_rate_hz=True, window_samples=PROMOTION_WINDOW_SAMPLES))

    def test_a_float_length_deviates(self):
        self.assertIn("window_samples", promotion_geometry_deviations(
            sample_rate_hz=PROMOTION_SAMPLE_RATE_HZ,
            window_samples=524_288.0))

    def test_the_predicate_agrees_with_the_deviations(self):
        for kwargs in ({"sample_rate_hz": PROMOTION_SAMPLE_RATE_HZ,
                        "window_samples": PROMOTION_WINDOW_SAMPLES},
                       {"sample_rate_hz": 1.0, "window_samples": 2},
                       {"sample_rate_hz": PROMOTION_SAMPLE_RATE_HZ,
                        "window_samples": 262_144}):
            with self.subTest(**kwargs):
                self.assertEqual(is_promotion_geometry(**kwargs),
                                 not promotion_geometry_deviations(**kwargs))


class PurityTests(unittest.TestCase):
    """Reading a number must not execute a module graph."""

    def test_the_geometry_module_imports_only_future_and_typing(self):
        """§13n Amendment O's reasoning, applied to a second pure module.

        A caller that wanted the window length used to have a choice between
        two impure modules. This one has to stay importable from anywhere,
        which means it has to stay importable from *nothing*.
        """
        self.assertEqual(_imported_modules(REPO / "rf_promotion_geometry.py"),
                         {"__future__", "typing"})

    def test_the_synthetic_harness_does_not_import_the_ring(self):
        """§5.20 correction A, the half that is about dependencies.

        The harness reads the geometry from the pure module. Importing
        `rf_iq_ring` to get a constant would bring `threading`, NumPy's ring
        allocation and the whole retention graph along with it -- importing a
        module executes it.
        """
        self.assertNotIn("rf_iq_ring", _imported_modules(REPO / "rf_null_corpus.py"))

    def test_importing_the_harness_does_not_load_the_ring(self):
        """Statically absent is not the same as transitively absent.

        Run in a clean interpreter, because this suite has already imported
        half the repository and `sys.modules` would answer for the suite
        rather than for the harness.
        """
        result = subprocess.run(
            [sys.executable, "-c",
             "import rf_null_corpus, sys; "
             "print('rf_iq_ring' in sys.modules)"],
            cwd=str(REPO), capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "False")

    def test_the_ring_re_exports_rather_than_redeclares(self):
        """The ring's names keep their spelling and lose their second source."""
        import rf_iq_ring
        self.assertIs(rf_iq_ring.DEFAULT_CAPACITY_SAMPLES, PROMOTION_WINDOW_SAMPLES)
        self.assertEqual(rf_iq_ring.DEFAULT_SAMPLE_RATE_HZ, PROMOTION_SAMPLE_RATE_HZ)
        self.assertEqual(rf_iq_ring.DEFAULT_WINDOW_MS, PROMOTION_WINDOW_MS)
        self.assertEqual(rf_iq_ring.STORAGE_DTYPE, PROMOTION_DTYPE)
        self.assertEqual(rf_iq_ring.WINDOW_OVERLAP, PROMOTION_WINDOW_OVERLAP)
        self.assertEqual(rf_iq_ring.NOMINAL_CYCLE_RESOLUTION_HZ,
                         PROMOTION_CYCLE_RESOLUTION_HZ)

    def test_the_ring_declares_no_geometry_number_of_its_own(self):
        """No second source, checked over code rather than over prose.

        An earlier version of this test scanned the raw text and failed on a
        docstring that *explains* the rule using the number. A literal in a
        sentence is documentation; a literal in an assignment is the second
        source of truth this correction removed. Only the second is a defect,
        so only the second is what this looks at.
        """
        tree = ast.parse((REPO / "rf_iq_ring.py").read_text())
        numbers = {node.value for node in ast.walk(tree)
                   if isinstance(node, ast.Constant)
                   and isinstance(node.value, (int, float))
                   and not isinstance(node.value, bool)}
        for value in (PROMOTION_WINDOW_SAMPLES, PROMOTION_SAMPLE_RATE_HZ,
                      PROMOTION_WINDOW_MS, PROMOTION_CYCLE_RESOLUTION_HZ):
            with self.subTest(value=value):
                self.assertNotIn(value, numbers)


if __name__ == "__main__":
    unittest.main()
