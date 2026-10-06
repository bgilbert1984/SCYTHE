"""Tests for mechanics/: the evidence boundary around simulated state.

Merge gate for PR #130: absence never means zero; simulated geometry must
actually derive from the model; provenance must be internally satisfiable;
and negative controls must prove all three.

Conventions: unittest, stdlib + repo tree only (no jsonschema in CI).
Run: python -m unittest test_scythe_mechanics -v
"""

import csv
import inspect
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mechanics.importers.openradioss_history import (
    MissingChannelError,
    SCHEMA_ID,
    STATE_CLASS,
    derive_antenna_normal,
    parse_th_csv,
    to_mechanical_state,
)
from mechanics import provenance as prov


def _write_csv(rows, fieldnames):
    fd, path = tempfile.mkstemp(suffix=".csv")
    with os.fdopen(fd, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(fieldnames)
        w.writerows(rows)
    return path


FULL_FIELDS = ["time",
               "1042_dx", "1042_dy", "1042_dz",
               "1042_vx", "1042_vy", "1042_vz",
               "1042_ax", "1042_ay", "1042_az"]

FULL_ROW = ["0.734",
            "0.00142", "-0.00031", "0.00010",
            "0.00410", "-0.00220", "0.01380",
            "0.14000", "-0.08000", "0.42000"]


def _solver():
    return {"name": "OpenCourant", "commit": "a" * 40, "build": "test"}


def _provenance():
    d = {k: "b" * 64 for k in
         ("deck_sha256", "mesh_sha256", "material_model_digest",
          "boundary_condition_digest", "export_digest")}
    d["solver_commit"] = "c" * 40
    return d


class TestStateClassConst(unittest.TestCase):
    def test_importer_stamps_simulated(self):
        path = _write_csv([FULL_ROW], FULL_FIELDS)
        try:
            rows, refused = parse_th_csv(path, "1042")
            rec = to_mechanical_state(rows[0][0], rows[0][1], _solver(),
                                      _provenance(), [0.0, 0.0, 1.682],
                                      [0.0, 0.0, 1.0])
            self.assertEqual(rec["state_class"], "SIMULATED_MECHANICAL_STATE")
            self.assertEqual(rec["schema"], "scythe.mechanical-state.v1")
        finally:
            os.unlink(path)

    def test_schema_declares_const(self):
        sp = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "mechanics", "schema", "mechanical_state.v1.json")
        schema = json.load(open(sp))
        self.assertEqual(schema["properties"]["state_class"]["const"],
                         "SIMULATED_MECHANICAL_STATE")

    def test_state_class_not_overridable(self):
        # No caller parameter may change the label.
        params = inspect.signature(to_mechanical_state).parameters
        self.assertNotIn("state_class", params)


class TestMissingChannelRefusal(unittest.TestCase):
    def test_absent_column_is_refusal_not_zero(self):
        fields = [f for f in FULL_FIELDS if not f.endswith("_vx")]
        path = _write_csv([FULL_ROW[:4] + FULL_ROW[5:]], fields)
        try:
            with self.assertRaises(MissingChannelError) as cm:
                parse_th_csv(path, "1042")
            self.assertIn("1042_vx", str(cm.exception))
        finally:
            os.unlink(path)

    def test_blank_cell_refuses_timestep(self):
        bad = list(FULL_ROW)
        bad[4] = ""  # blank vx
        path = _write_csv([FULL_ROW, bad, FULL_ROW], FULL_FIELDS)
        try:
            rows, refused = parse_th_csv(path, "1042")
            self.assertEqual(len(rows), 2)
            self.assertEqual(refused, 1)
            # No record was fabricated for the refused timestep: every
            # emitted row carries observed (non-None) channels.
            for _, ch in rows:
                self.assertTrue(all(v is not None for v in ch.values()
                                    if not isinstance(v, dict)))
        finally:
            os.unlink(path)

    def test_absent_time_column_is_refusal(self):
        path = _write_csv([FULL_ROW[1:]], FULL_FIELDS[1:])
        try:
            with self.assertRaises(MissingChannelError):
                parse_th_csv(path, "1042")
        finally:
            os.unlink(path)


class TestGeometryDerivation(unittest.TestCase):
    def test_phase_center_is_nominal_plus_displacement(self):
        path = _write_csv([FULL_ROW], FULL_FIELDS)
        try:
            rows, _ = parse_th_csv(path, "1042")
            t, ch = rows[0]
            nominal = [0.0, 0.0, 1.682]
            rec = to_mechanical_state(t, ch, _solver(), _provenance(),
                                      nominal, [0.0, 0.0, 1.0])
            # NOT displacement alone: nominal + disp.
            self.assertAlmostEqual(rec["receiver"]["phase_center_m"][0], 0.00142)
            self.assertAlmostEqual(rec["receiver"]["phase_center_m"][2],
                                   1.682 + 0.00010)
            self.assertEqual(rec["receiver"]["phase_center_displacement_m"],
                             [0.00142, -0.00031, 0.00010])
        finally:
            os.unlink(path)

    def test_derive_antenna_normal(self):
        n = derive_antenna_normal([0, 0, 0], [0, 0, 5])
        self.assertEqual(n, [0.0, 0.0, 1.0])
        n = derive_antenna_normal([1, 0, 0], [1, 1, 0])
        self.assertAlmostEqual(n[1], 1.0)

    def test_degenerate_normal_is_refusal(self):
        with self.assertRaises(ValueError):
            derive_antenna_normal([1, 1, 1], [1, 1, 1])

    def test_missing_normal_is_refusal(self):
        path = _write_csv([FULL_ROW], FULL_FIELDS)
        try:
            rows, _ = parse_th_csv(path, "1042")
            with self.assertRaises(MissingChannelError):
                to_mechanical_state(rows[0][0], rows[0][1], _solver(),
                                    _provenance(), [0.0, 0.0, 1.682], None)
        finally:
            os.unlink(path)

    def test_missing_nominal_is_refusal(self):
        path = _write_csv([FULL_ROW], FULL_FIELDS)
        try:
            rows, _ = parse_th_csv(path, "1042")
            with self.assertRaises(MissingChannelError):
                to_mechanical_state(rows[0][0], rows[0][1], _solver(),
                                    _provenance(), None, [0.0, 0.0, 1.0])
        finally:
            os.unlink(path)

    def test_no_fabricated_default(self):
        # antenna_normal and nominal_position must be required positionals:
        # a default would be fabricated evidence.
        params = inspect.signature(to_mechanical_state).parameters
        self.assertIs(params["antenna_normal"].default, inspect.Parameter.empty)
        self.assertIs(params["nominal_position"].default, inspect.Parameter.empty)


class TestProvenanceCompleteness(unittest.TestCase):
    def _deck(self):
        fd, path = tempfile.mkstemp(suffix=".rad")
        os.write(fd, b"*deck*\n")
        os.close(fd)
        return path

    def _export(self):
        fd, path = tempfile.mkstemp(suffix=".csv")
        os.write(fd, b"time,1042_dx\n0.0,0.0\n")
        os.close(fd)
        return path

    def test_embedded_mesh_binds_to_deck(self):
        deck = self._deck()
        try:
            exp = self._export()
            p = prov.build_provenance(deck, exp, solver_commit="c" * 40,
                                      material_block="mat", bc_block="bc")
            os.unlink(exp)
            self.assertEqual(p["mesh_sha256"], p["deck_sha256"])
            self.assertTrue(prov.provenance_complete(p))
        finally:
            os.unlink(deck)

    def test_external_mesh_hashes_independently(self):
        deck = self._deck()
        fd, mesh = tempfile.mkstemp(suffix=".msh")
        os.write(fd, b"*mesh*\n")
        os.close(fd)
        try:
            exp = self._export()
            p = prov.build_provenance(deck, exp, mesh_path=mesh,
                                      solver_commit="c" * 40,
                                      material_block="mat", bc_block="bc")
            os.unlink(exp)
            self.assertNotEqual(p["mesh_sha256"], p["deck_sha256"])
            self.assertTrue(prov.provenance_complete(p))
        finally:
            os.unlink(deck)
            os.unlink(mesh)

    def test_missing_material_is_refusal(self):
        deck = self._deck()
        try:
            exp = self._export()
            with self.assertRaises(ValueError):
                prov.build_provenance(deck, exp, solver_commit="c" * 40,
                                      bc_block="bc")
            os.unlink(exp)
        finally:
            os.unlink(deck)

    def test_missing_bc_is_refusal(self):
        deck = self._deck()
        try:
            exp = self._export()
            with self.assertRaises(ValueError):
                prov.build_provenance(deck, exp, solver_commit="c" * 40,
                                      material_block="mat")
            os.unlink(exp)
        finally:
            os.unlink(deck)

    def test_incomplete_provenance_fails(self):
        p = _provenance()
        self.assertTrue(prov.provenance_complete(p))
        for k in prov.REQUIRED_DIGESTS:
            q = dict(p)
            q[k] = ""
            self.assertFalse(prov.provenance_complete(q), k)

    def test_schema_requires_full_chain(self):
        sp = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "mechanics", "schema", "mechanical_state.v1.json")
        schema = json.load(open(sp))
        req = schema["properties"]["provenance"]["required"]
        for d in ("deck_sha256", "mesh_sha256", "material_model_digest",
                  "boundary_condition_digest", "solver_commit", "export_digest"):
            self.assertIn(d, req)


if __name__ == "__main__":
    unittest.main()
