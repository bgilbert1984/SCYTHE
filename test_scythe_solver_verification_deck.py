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


if __name__ == "__main__":
    unittest.main()
