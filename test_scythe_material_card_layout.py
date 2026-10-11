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

TWO DECK FAMILIES. The default generator output ("legacy10") is the historical encoding and must keep
reproducing the committed decks, which are the evidence for recorded solver results and are never
regenerated. The corrected family ("fields20", files named *_matfmt20_*) writes the same decks with
20-character /MAT/ELAST fields. The corrected decks are committed as new files and identified by
deck_family_matfmt20_identity.json; both families coexist, and results from the two are not interchangeable.
"""

import json
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


MESHES = ("coarse", "medium", "fine")
IDENTITY_RECORD = os.path.join(DECK_DIR, "deck_family_matfmt20_identity.json")
# NEW_BASELINE (established 2026-10-12): digests of the corrected family. These are the decks that were
# actually run: the generator output was verified byte-identical to the executed solver inputs.
CORRECTED_STARTER_DIGESTS = {
    "coarse": "8bf74526591d23b2da2d4f4235c7f21871c1c3ad327c185550998176c36a5143",
    "medium": "508c28bf3b0f42be8d8609a71b799c8a87802e6beb87dba19948f0cd4788088c",
    "fine": "b07b181c255d7e4f412b58c1606afccf8eef5d349c1b483e8a9105d0ac370ada",
}
ENGINE_DECK_DIGEST = "94894b74ecfb825cdb41c32f2f2cc803d440b121914d1cf3312c270134c49402"   # identical in both families


def _run_generator(out_dir, *args):
    copy = os.path.join(out_dir, "generate_deck.py")
    shutil.copyfile(GENERATOR, copy)              # an unmodified copy writes beside itself
    return subprocess.run([sys.executable, copy, *args], cwd=out_dir, capture_output=True, text=True,
                          timeout=120, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _sha256(path):
    import hashlib
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class GeneratedAndCommittedDeckTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.out = tempfile.mkdtemp(prefix="scythe-matcard-")
        cls.out20 = tempfile.mkdtemp(prefix="scythe-matcard20-")
        for directory in (cls.out, cls.out20):
            cls.addClassCleanup(shutil.rmtree, directory, ignore_errors=True)
        for directory, args in ((cls.out, ()), (cls.out20, ("--material-layout", "fields20"))):
            proc = _run_generator(directory, *args)
            if proc.returncode != 0:
                raise RuntimeError(f"generator {args} failed:\n{proc.stdout}\n{proc.stderr}")

    def test_corrected_family_emits_a_valid_material_card(self):
        for mesh in MESHES:
            with self.subTest(mesh):
                text = _read(os.path.join(self.out20, f"cantilever_{mesh}_matfmt20_0000.rad"))
                self.assertEqual(validate_material_card(text), [])
                values = read_fields_as_observed(text)
                self.assertEqual((values["MAT_RHO"], values["MAT_E"], values["MAT_NU"]), (7850.0, 2.0e11, 0.3))

    def test_default_output_is_still_the_historical_encoding(self):
        """The default must not change: it is what reproduces the committed historical decks."""
        for mesh in MESHES:
            with self.subTest(mesh):
                text = _read(os.path.join(self.out, f"cantilever_{mesh}_0000.rad"))
                self.assertEqual(len(validate_material_card(text)), 2)
                self.assertEqual(read_fields_as_observed(text)["MAT_NU"], 0.0)

    def test_default_output_equals_the_committed_historical_decks(self):
        for mesh in MESHES:
            for number in ("0000", "0001"):
                with self.subTest(mesh=mesh, deck=number):
                    name = f"cantilever_{mesh}_{number}.rad"
                    self.assertEqual(_sha256(os.path.join(self.out, name)), _sha256(os.path.join(DECK_DIR, name)))

    def test_corrected_family_differs_from_the_historical_decks_only_in_the_material_data_lines(self):
        for mesh in MESHES:
            with self.subTest(mesh):
                historical = _read(os.path.join(DECK_DIR, f"cantilever_{mesh}_0000.rad")).split("\n")
                corrected = _read(os.path.join(self.out20, f"cantilever_{mesh}_matfmt20_0000.rad")).split("\n")
                self.assertEqual(len(historical), len(corrected))
                header = next(i for i, line in enumerate(historical) if line.startswith("/MAT/ELAST/"))
                changed = [i for i, (a, b) in enumerate(zip(historical, corrected)) if a != b]
                self.assertEqual(changed, [header + 2, header + 3])
                self.assertEqual(_sha256(os.path.join(self.out20, f"cantilever_{mesh}_matfmt20_0001.rad")),
                                 _sha256(os.path.join(DECK_DIR, f"cantilever_{mesh}_0001.rad")))

    def test_the_frozen_reference_is_not_rewritten_in_the_corrected_mode(self):
        self.assertNotIn("analytical_reference.json", os.listdir(self.out20))

    def test_unknown_layout_is_refused(self):
        with tempfile.TemporaryDirectory(prefix="scythe-matcard-bad-") as directory:
            proc = _run_generator(directory, "--material-layout", "bogus")
            self.assertNotEqual(proc.returncode, 0)
            self.assertEqual([f for f in os.listdir(directory) if f.endswith(".rad")], [])

    def test_committed_decks_carry_the_historical_encoding(self):
        """The decks behind the recorded solver results. They are evidence and are not regenerated."""
        for name in ("coarse", "medium", "fine", "dynamic"):
            with self.subTest(name):
                text = _read(os.path.join(DECK_DIR, f"cantilever_{name}_0000.rad"))
                problems = validate_material_card(text)
                self.assertEqual(len(problems), 2, problems)
                values = read_fields_as_observed(text)
                self.assertEqual((values["MAT_E"], values["MAT_NU"]), (2.0e11, 0.0))

    def test_committed_corrected_family_is_the_generator_output(self):
        for mesh in MESHES:
            for number in ("0000", "0001"):
                with self.subTest(mesh=mesh, deck=number):
                    name = f"cantilever_{mesh}_matfmt20_{number}.rad"
                    self.assertEqual(_sha256(os.path.join(DECK_DIR, name)), _sha256(os.path.join(self.out20, name)))

    def test_committed_corrected_family_has_a_valid_material_card(self):
        for mesh in MESHES:
            with self.subTest(mesh):
                self.assertEqual(validate_material_card(_read(os.path.join(DECK_DIR, f"cantilever_{mesh}_matfmt20_0000.rad"))), [])

    def test_corrected_family_digests_are_the_new_baseline(self):
        for mesh in MESHES:
            with self.subTest(mesh):
                self.assertEqual(_sha256(os.path.join(DECK_DIR, f"cantilever_{mesh}_matfmt20_0000.rad")), CORRECTED_STARTER_DIGESTS[mesh])
                self.assertEqual(_sha256(os.path.join(DECK_DIR, f"cantilever_{mesh}_matfmt20_0001.rad")), ENGINE_DECK_DIGEST)

    def test_identity_record_agrees_with_the_decks_and_labels_itself_a_new_baseline(self):
        with open(IDENTITY_RECORD, encoding="utf-8") as handle:
            record = json.load(handle)
        self.assertEqual(record["authority"], "NEW_BASELINE")
        for mesh in MESHES:
            for number, expected in (("0000", CORRECTED_STARTER_DIGESTS[mesh]), ("0001", ENGINE_DECK_DIGEST)):
                entry = record["decks"][f"cantilever_{mesh}_matfmt20_{number}.rad"]
                self.assertEqual(entry["sha256"], expected)
                self.assertEqual(entry["sha256"], _sha256(os.path.join(DECK_DIR, f"cantilever_{mesh}_matfmt20_{number}.rad")))
        # the relation to the historical family is stated, and the historical digests are not touched
        for mesh in MESHES:
            self.assertEqual(record["historical_counterparts"][mesh]["starter_sha256"], _sha256(os.path.join(DECK_DIR, f"cantilever_{mesh}_0000.rad")))
        self.assertTrue(any("generator output for dynamic" in item for item in record["not_in_this_family"]))
        self.assertIn("benchmark verdict", record["does_not_establish"])

    # The dynamic decks are not generator output: they are the historical dynamic deck with its two
    # /MAT/ELAST data lines re-encoded (the bytes the 2026-10-12 solver run read).
    DYNAMIC_STARTER_DIGEST = "042a6f2089ef5a3a2ce8eb859a2ead085306c1cccde7fa8d332219f56ccbbb1d"
    DYNAMIC_ENGINE_DIGEST = "2bcee7d089f858200dba7dc5563c8203d7f84bb9dc4f510df0167fd9ef691cc4"

    def test_dynamic_corrected_decks_are_the_historical_deck_with_only_the_material_lines_reencoded(self):
        historical = _read(os.path.join(DECK_DIR, "cantilever_dynamic_0000.rad")).split("\n")
        corrected = _read(os.path.join(DECK_DIR, "cantilever_dynamic_matfmt20_0000.rad")).split("\n")
        self.assertEqual(len(historical), len(corrected))
        differing = [i for i, (a, b) in enumerate(zip(historical, corrected)) if a != b]
        self.assertEqual(differing, [15, 16])      # lines 16-17: the two /MAT/ELAST data lines
        self.assertEqual(corrected[15:17], corrected_card().split("\n")[2:4])
        self.assertEqual(validate_material_card("\n".join(corrected)), [])
        self.assertEqual(read_fields_as_observed("\n".join(corrected))["MAT_NU"], 0.3)
        self.assertEqual(_sha256(os.path.join(DECK_DIR, "cantilever_dynamic_matfmt20_0001.rad")),
                         _sha256(os.path.join(DECK_DIR, "cantilever_dynamic_0001.rad")))

    def test_dynamic_corrected_digests_are_pinned_and_recorded(self):
        with open(IDENTITY_RECORD, encoding="utf-8") as handle:
            record = json.load(handle)
        for name, expected in (("cantilever_dynamic_matfmt20_0000.rad", self.DYNAMIC_STARTER_DIGEST),
                               ("cantilever_dynamic_matfmt20_0001.rad", self.DYNAMIC_ENGINE_DIGEST)):
            with self.subTest(name):
                self.assertEqual(_sha256(os.path.join(DECK_DIR, name)), expected)
                self.assertEqual(record["decks"][name]["sha256"], expected)
        self.assertEqual(record["historical_counterparts"]["dynamic"]["starter_sha256"],
                         _sha256(os.path.join(DECK_DIR, "cantilever_dynamic_0000.rad")))
        self.assertEqual(record["dynamic_decks_provenance"]["executed_with"]["starter_deck_sha256"],
                         self.DYNAMIC_STARTER_DIGEST)


if __name__ == "__main__":
    unittest.main()
