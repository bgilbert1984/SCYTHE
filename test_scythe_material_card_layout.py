#!/usr/bin/env python3
"""The solver reads /MAT/ELAST data in 20-character fields; the cantilever decks write 10.

The starter echoed Poisson's ratio 0 and shear modulus E/2 for decks that declare
nu = 0.3, and raised no warning (see channel_semantics_amendment_2026-10-11.md and
the solver-input review). The cause is the card layout: for every input version from
radioss90 up, the card definition is

    CARD("%20lg%20lg", MAT_RHO, Refer_Rho)     line 1: density, reference density
    CARD("%20lg%20lg", MAT_E,   MAT_NU)        line 2: Young's modulus, Poisson's ratio

(OpenCourant 33e685176cccf0c539a3ce07aa2096985a284e2a,
 hm_cfg_files/config/CFG/radioss110/MAT/elast_1.cfg, FORMAT(radioss90) lines 133-173;
 the runtime install's hm_cfg_files is byte-identical to the source tree's, checked
 2026-10-12). The generator writes both lines with 10-character fields, so nu falls
 inside the 20-character E field and the nu field (columns 21-40) is empty.

This module parses a /MAT/ELAST card by that field specification, not by searching for
"0.3000", and shows that the historical encoding fails it while the corrected one passes.

What is and is not established. The field specification is read from the card definition.
What the starter actually did with a two-value field is OBSERVED (its echo: E correct,
nu = 0, G = E/2), not read from source: the tokenizing code is in the prebuilt hm_reader
library, which is not in the OpenCourant clone. `read_fields_as_observed` reproduces the
observation (first value in each field, blank = 0) and is labelled as such.

KNOWN DEFECT. The generator has not been corrected (a separate change, after the controlled
experiment). `test_generator_emits_a_valid_material_card` is therefore an expected failure.
When the generator is fixed it will report an UNEXPECTED SUCCESS, which unittest counts as a
failure: remove the decorator in that same change. The committed decks, which are the
evidence for recorded solver results, keep the historical encoding; the characterization
test pins that, and will need to change together with their digests if they are regenerated.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
DECK_DIR = os.path.join(REPO_ROOT, "mechanics", "openradioss", "decks", "cantilever_eb")
GENERATOR = os.path.join(DECK_DIR, "generate_deck.py")
FIELD_WIDTH = 20
FIELDS_PER_LINE = 2
LINE_FIELD_NAMES = (("MAT_RHO", "Refer_Rho"), ("MAT_E", "MAT_NU"))
NUMBER = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eEdD][+-]?\d+)?$")


def _material_card(text):
    """(header, title, [data lines]) of the first /MAT/ELAST card in a starter deck."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("/MAT/ELAST/"):
            return line, lines[index + 1], lines[index + 2:index + 4]
    raise AssertionError("no /MAT/ELAST card in the deck")


def validate_material_card(text):
    """Problems found reading the card as 2 lines of two 20-character numeric fields.
    An empty list means every field holds at most one value and the card has the expected shape."""
    header, _title, data = _material_card(text)
    problems = []
    if len(data) != len(LINE_FIELD_NAMES):
        problems.append(f"expected {len(LINE_FIELD_NAMES)} data lines, found {len(data)}")
    for row, (line, names) in enumerate(zip(data, LINE_FIELD_NAMES), start=1):
        if line.startswith(("#", "/")):
            problems.append(f"data line {row} is not data: {line[:30]!r}")
            continue
        if len(line) > FIELD_WIDTH * FIELDS_PER_LINE:
            problems.append(f"data line {row} is {len(line)} characters; the card is "
                            f"{FIELD_WIDTH * FIELDS_PER_LINE}")
        for position, name in enumerate(names):
            field = line[position * FIELD_WIDTH:(position + 1) * FIELD_WIDTH]
            tokens = field.split()
            if len(tokens) > 1:
                problems.append(f"line {row} field {position + 1} ({name}, columns "
                                f"{position * FIELD_WIDTH + 1}-{(position + 1) * FIELD_WIDTH}) "
                                f"holds {len(tokens)} values: {tokens}")
            for token in tokens:
                if not NUMBER.match(token):
                    problems.append(f"line {row} field {position + 1} ({name}) is not a number: {token!r}")
    return problems


def read_fields_as_observed(text):
    """The first value in each 20-character field, blank = 0. This reproduces what the
    starter reported for the historical decks; it is an observation, not the parser's source."""
    _header, _title, data = _material_card(text)
    values = {}
    for line, names in zip(data, LINE_FIELD_NAMES):
        for position, name in enumerate(names):
            tokens = line[position * FIELD_WIDTH:(position + 1) * FIELD_WIDTH].split()
            values[name] = float(tokens[0].replace("D", "E").replace("d", "e")) if tokens else 0.0
    return values


def shear_modulus(young, nu):
    return young / (2.0 * (1.0 + nu))


def corrected_card(rho=7850.0, young=2.0e11, nu=0.3):
    """The 20-character encoding of the same declared values."""
    fields = lambda *v: "".join(f"{x:20.6E}" for x in v)
    return "\n".join(["/MAT/ELAST/1", "steel", fields(rho, 0.0), fields(young, nu)]) + "\n"


HISTORICAL_CARD = "\n".join(["/MAT/ELAST/1", "steel", " 7850.0000         0", "2.0000E+11    0.3000"]) + "\n"


class MaterialCardSpecificationTests(unittest.TestCase):

    def test_the_corrected_encoding_passes(self):
        card = corrected_card()
        self.assertEqual(validate_material_card(card), [])
        values = read_fields_as_observed(card)
        self.assertEqual((values["MAT_RHO"], values["MAT_E"], values["MAT_NU"]),
                         (7850.0, 2.0e11, 0.3))

    def test_the_historical_encoding_fails(self):
        problems = validate_material_card(HISTORICAL_CARD)
        self.assertEqual(len(problems), 2, problems)
        self.assertTrue(all("holds 2 values" in p for p in problems), problems)
        self.assertIn("MAT_E", problems[1])

    def test_the_historical_encoding_loses_poissons_ratio(self):
        """Reproduces the observed starter state: E intact, nu 0, G = E/2 (not E/2.6)."""
        values = read_fields_as_observed(HISTORICAL_CARD)
        self.assertEqual(values["MAT_E"], 2.0e11)
        self.assertEqual(values["MAT_NU"], 0.0)
        self.assertEqual(shear_modulus(values["MAT_E"], values["MAT_NU"]), 1.0e11)
        self.assertAlmostEqual(shear_modulus(2.0e11, 0.3), 7.6923076923e10, delta=1.0)

    def test_the_two_encodings_declare_the_same_values(self):
        """The defect is the layout, not the numbers: the same E, nu and rho are written."""
        pairs = {"MAT_E": 2.0e11, "MAT_NU": 0.3, "MAT_RHO": 7850.0}
        historical_tokens = " ".join(HISTORICAL_CARD.splitlines()[2:]).split()
        self.assertEqual([float(t) for t in historical_tokens], [7850.0, 0.0, 2.0e11, 0.3])
        corrected = read_fields_as_observed(corrected_card())
        for name, value in pairs.items():
            self.assertEqual(corrected[name], value)

    def test_other_malformed_cards_are_rejected(self):
        cases = {
            "nu in the E field's tail": "/MAT/ELAST/1\nsteel\n" + f"{7850.0:20.6E}{0.0:20.6E}\n" + f"{2.0e11:12.4E}{0.3:8.4f}\n",
            "line too long": "/MAT/ELAST/1\nsteel\n" + f"{7850.0:20.6E}{0.0:20.6E}\n" + f"{2.0e11:20.6E}{0.3:20.6E}{1.0:20.6E}\n",
            "non-numeric field": "/MAT/ELAST/1\nsteel\n" + f"{7850.0:20.6E}{0.0:20.6E}\n" + f"{2.0e11:20.6E}{'abc':>20}\n",
            "missing data line": "/MAT/ELAST/1\nsteel\n" + f"{7850.0:20.6E}{0.0:20.6E}\n#---\n",
        }
        for label, card in cases.items():
            with self.subTest(label):
                self.assertTrue(validate_material_card(card), label)

    def test_a_card_with_both_values_in_their_own_fields_is_not_confused_by_alignment(self):
        card = "/MAT/ELAST/1\nsteel\n" + f"{'7850.0':<20}{'0':<20}\n" + f"{'2.0E+11':<20}{'0.3':<20}\n"
        self.assertEqual(validate_material_card(card), [])
        self.assertEqual(read_fields_as_observed(card)["MAT_NU"], 0.3)


class GeneratedAndCommittedDeckTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.out = tempfile.mkdtemp(prefix="scythe-matcard-")
        cls.addClassCleanup(shutil.rmtree, cls.out, ignore_errors=True)
        copy = os.path.join(cls.out, "generate_deck.py")
        shutil.copyfile(GENERATOR, copy)          # an unmodified copy writes beside itself
        proc = subprocess.run([sys.executable, copy], cwd=cls.out, capture_output=True, text=True,
                              timeout=120, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        if proc.returncode != 0:
            raise RuntimeError(f"generator failed:\n{proc.stdout}\n{proc.stderr}")

    @unittest.expectedFailure
    def test_generator_emits_a_valid_material_card(self):
        """KNOWN DEFECT: the generator writes 10-character fields. Remove this decorator in
        the change that corrects it; until then an UNEXPECTED SUCCESS here is the signal."""
        for mesh in ("coarse", "medium", "fine"):
            with self.subTest(mesh):
                with open(os.path.join(self.out, f"cantilever_{mesh}_0000.rad")) as handle:
                    self.assertEqual(validate_material_card(handle.read()), [])

    def test_generator_output_has_the_historical_encoding(self):
        """The current behaviour, stated positively, so the expected failure above is
        a statement about the card and not an accident of the test."""
        for mesh in ("coarse", "medium", "fine"):
            with self.subTest(mesh):
                with open(os.path.join(self.out, f"cantilever_{mesh}_0000.rad")) as handle:
                    text = handle.read()
                self.assertEqual(len(validate_material_card(text)), 2)
                self.assertEqual(read_fields_as_observed(text)["MAT_NU"], 0.0)

    def test_committed_decks_carry_the_historical_encoding(self):
        """The decks behind the recorded solver results. They are evidence and are not
        regenerated; this fails if one changes without the others being reviewed."""
        for name in ("coarse", "medium", "fine", "dynamic"):
            with self.subTest(name):
                with open(os.path.join(DECK_DIR, f"cantilever_{name}_0000.rad")) as handle:
                    text = handle.read()
                problems = validate_material_card(text)
                self.assertEqual(len(problems), 2, problems)
                values = read_fields_as_observed(text)
                self.assertEqual((values["MAT_E"], values["MAT_NU"]), (2.0e11, 0.0))


if __name__ == "__main__":
    unittest.main()
