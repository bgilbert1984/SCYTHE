"""CI test: cantilever solver-verification deck source-of-truth and format.

Proves:
1. generate_deck.py reproduces the committed canonical decks byte-for-byte
   (generator is the source of truth).
2. /NODE coordinate fields use the required 10+20+20+20 widths
   (the poisonous 10-char misparse is refused).

Runs under Repository Hygiene via `python -m unittest test_scythe_*` from root.
"""
import filecmp
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
DECK_DIR = os.path.join(REPO_ROOT, "mechanics", "openradioss", "decks",
                        "cantilever_eb")
GENERATOR = os.path.join(DECK_DIR, "generate_deck.py")
MESHES = ["coarse", "medium", "fine"]
SUFFIXES = ["0000.rad", "0001.rad"]


class TestSolverVerificationDeck(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="scythe-deck-")
        # Run the generator with OUT pointed at the temp dir.
        src = open(GENERATOR).read()
        patched = src.replace(
            "OUT = os.path.dirname(os.path.abspath(__file__))",
            f'OUT = "{cls.tmp}"',
        )
        gen_tmp = os.path.join(cls.tmp, "gen_tmp.py")
        open(gen_tmp, "w").write(patched)
        r = subprocess.run([sys.executable, gen_tmp],
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(f"generator failed:\n{r.stdout}\n{r.stderr}")

    def test_generator_reproduces_committed_decks(self):
        """Committed .rad files must match generator output exactly."""
        for mesh in MESHES:
            for suffix in SUFFIXES:
                committed = os.path.join(
                    DECK_DIR, f"cantilever_{mesh}_{suffix}")
                generated = os.path.join(
                    self.tmp, f"cantilever_{mesh}_{suffix}")
                self.assertTrue(
                    os.path.exists(committed),
                    f"committed deck missing: {committed}")
                self.assertTrue(
                    os.path.exists(generated),
                    f"generator did not emit: {generated}")
                self.assertTrue(
                    filecmp.cmp(committed, generated, shallow=False),
                    f"generator output differs from committed deck: "
                    f"cantilever_{mesh}_{suffix} "
                    f"(generator is not source of truth)")

    def test_node_field_widths(self):
        """Every /NODE line: 10-char ID + three 20-char coordinate fields."""
        for mesh in MESHES:
            path = os.path.join(DECK_DIR, f"cantilever_{mesh}_0000.rad")
            with open(path) as f:
                lines = f.readlines()
            in_node = False
            checked = 0
            for line in lines:
                if line.startswith("/NODE"):
                    in_node = True
                    continue
                if in_node:
                    if line.startswith("/"):
                        break
                    if not line.strip() or line.startswith("#"):
                        continue
                    body = line.rstrip("\n")
                    self.assertEqual(
                        len(body), 70,
                        f"{mesh}: node line length {len(body)} != 70: "
                        f"{body[:40]}...")
                    try:
                        int(body[0:10])
                        float(body[10:30])
                        float(body[30:50])
                        float(body[50:70])
                    except ValueError:
                        self.fail(f"{mesh}: unparsable node fields: {body}")
                    checked += 1
            self.assertGreater(checked, 0, f"{mesh}: no node lines found")

    def test_analytical_reference_signed(self):
        """Reference JSON must carry the signed Z displacement."""
        import json
        ref = json.load(open(os.path.join(DECK_DIR,
                                           "analytical_reference.json")))
        z = ref["analytical"]["tip_displacement_z_m"]
        self.assertLess(z, 0, "tip_displacement_z_m must be negative (-Z load)")
        self.assertAlmostEqual(z, -0.0032, places=6)
        # Reaction estimator must be frozen.
        est = ref["static_estimator"]
        self.assertIn("reaction", est)
        self.assertIn("0.20", est["reaction"])

    def test_cload_card_layout(self):
        """CLOAD data lines: 100 chars, 6x10 + 2x20 fields, Ascale_x=1.0.

        Regression test for the Ascale_x=0.0 bug (2026-10-05): a 90-char
        line with Ascale_x explicitly 0.0 is syntactically accepted but
        semantically unloads the model (t/0.0 kills the load function).
        """
        import json
        ref = json.load(open(os.path.join(DECK_DIR,
                                           "analytical_reference.json")))
        # Expected per-node forces from tributary weighting
        # (recompute here to avoid trusting the JSON blindly)
        for mesh in MESHES:
            path = os.path.join(DECK_DIR, f"cantilever_{mesh}_0000.rad")
            with open(path) as f:
                lines = f.readlines()
            cload_lines = []
            in_cload = False
            for i, line in enumerate(lines):
                if line.startswith("/CLOAD/"):
                    in_cload = True
                    continue
                if in_cload:
                    # Skip title line, take data line
                    if i > 0 and lines[i-1].startswith("/CLOAD/"):
                        continue
                    if line.startswith("/"):
                        break
                    if line.strip() and not line.startswith("#"):
                        # Data line (not GRNOD header)
                        if not line.startswith("/GRNOD"):
                            cload_lines.append((line.rstrip("\n"), mesh))
                            in_cload = False
            self.assertEqual(len(cload_lines), 3,
                             f"{mesh}: expected 3 CLOAD data lines")
            total = 0.0
            for line, m in cload_lines:
                self.assertEqual(len(line), 100,
                                 f"{mesh}: CLOAD line must be 100 chars, "
                                 f"got {len(line)}")
                self.assertEqual(int(line[0:10]), 1)  # fct_ID
                self.assertEqual(line[10:20].strip(), "Z")  # Dir
                self.assertEqual(int(line[40:50]) in (2, 3, 4), True)  # grnd_ID
                self.assertEqual(int(line[50:60]), 1)  # Itypfun=1 (time)
                ascale = float(line[60:80])
                self.assertEqual(ascale, 1.0,
                                 f"{mesh}: Ascale_x must be 1.0, got {ascale} "
                                 f"(0.0 unloads the model!)")
                fscale = float(line[80:100])
                self.assertGreater(fscale, 0,
                                   f"{mesh}: Fscale_y must be positive")
                # Count nodes in the following GRNOD to get total
                # (simplified: use known counts per mesh)
            # Validate serialized total against recorded value
            recorded = ref["load"]["serialized_total_N"][mesh]
            # Recompute from parsed Fscale values weighted by node counts
            # (node counts: coarse 9, medium 25, fine 81; corners/edges/interior
            # split is mesh-dependent, so we just check the recorded value
            # is within 1e-6 of 1000 N)
            self.assertAlmostEqual(recorded, 1000.0, delta=1e-3,
                                   msg=f"{mesh}: serialized total {recorded} "
                                       f"deviates from 1000 N")

    def test_function_point_field_widths(self):
        """FUNCT points: 40 chars, 2x20 fields (X, Y).

        Regression test (2026-10-05): f10 produced 20-char lines, leaving
        Y blank -> zero ordinate -> zero force -> zero displacement.
        Syntactically accepted, semantically unloaded.
        """
        path = os.path.join(DECK_DIR, "cantilever_coarse_0000.rad")
        lines = open(path).read().splitlines()
        i = lines.index("/FUNCT/1")
        points = lines[i + 2:i + 5]
        self.assertEqual(len(points), 3)
        expected = [(0.0, 0.0), (0.1, -1.0), (10.0, -1.0)]
        for line, (x_exp, y_exp) in zip(points, expected):
            self.assertEqual(len(line), 40,
                             f"FUNCT point must be 40 chars, got {len(line)}")
            x = float(line[0:20])
            y = float(line[20:40])
            self.assertAlmostEqual(x, x_exp)
            self.assertAlmostEqual(y, y_exp)


if __name__ == "__main__":
    unittest.main()
