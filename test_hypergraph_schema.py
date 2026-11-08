import copy
import unittest

from rf_solver_evidence import RFSolverEvidenceStore, explain_coverage_cell
from scythe_hypergraph_schema import (
    build_solver_run_lineage, load_dataset_context, summarize_lineage, validate_lineage,
)

SAMPLE = {
    "dataset_id": "ntia-itm-sf-bay-area-v1", "tile_id": "0/0/0",
    "longitude_degrees": -122.4, "latitude_degrees": 37.8, "height_meters": 1.5,
    "frequency_hz": 900e6, "quantity": "basic transmission loss", "value": 142.5,
    "units": "dB", "coverage": False, "coverage_threshold": 130.0,
    "transmitter_id": "sf-itm-tx", "sampled_at": 1000.0,
    "provenance": {"solverName": "NTIA ITS Irregular Terrain Model",
                   "solverVersion": "v1.4-43-g183ad95",
                   "runId": "ntia-itm-sf-bay-area-v1-run-1"},
}


def _evidence():
    return RFSolverEvidenceStore().ingest(copy.deepcopy(SAMPLE))


class LineageTests(unittest.TestCase):
    def test_dataset_context_loads_and_rejects_traversal(self):
        self.assertIn("manifest", load_dataset_context("ntia-itm-sf-bay-area-v1"))
        self.assertEqual(load_dataset_context("../etc"), {})

    def test_solver_run_is_traceable_and_valid(self):
        ev = _evidence()
        lin = build_solver_run_lineage(ev)
        self.assertEqual(lin["violations"], [])
        edge = lin["hyperedges"][0]
        self.assertEqual(edge["type"], "SolverRunHyperedge")
        types = {n["type"] for n in lin["nodes"]}
        self.assertEqual(types, {"EnvironmentNode", "VariantNode", "DeviceNode", "SignalNode"})
        self.assertEqual(edge["geometry"], [])           # ITM: no faces invented
        self.assertEqual(edge["source"]["kind"], "SYNTHETIC_MODEL")
        self.assertEqual(edge["source"]["observation_ids"], [])
        variant = next(n for n in lin["nodes"] if n["type"] == "VariantNode")
        self.assertEqual(variant["environment_id"], edge["environment_id"])

    def test_ids_deterministic_across_calls(self):
        ev = _evidence()
        a = build_solver_run_lineage(ev)
        b = build_solver_run_lineage(ev)
        self.assertEqual([n["node_id"] for n in a["nodes"]], [n["node_id"] for n in b["nodes"]])
        self.assertEqual(a["hyperedges"][0]["edge_id"], b["hyperedges"][0]["edge_id"])

    def test_environment_id_independent_of_dataset_version(self):
        ev = _evidence()
        ctx = load_dataset_context(ev["dataset_id"])
        a = build_solver_run_lineage(ev, ctx)["nodes"][0]["node_id"]
        ev2 = dict(ev, dataset_id="ntia-itm-sf-bay-area-v2")
        b = build_solver_run_lineage(ev2, ctx)["nodes"][0]["node_id"]
        self.assertEqual(a, b)

    def test_observation_edges_only_when_supplied(self):
        lin = build_solver_run_lineage(_evidence(), observation_ids=["sdr-capture-7"])
        sup = [e for e in lin["hyperedges"] if e["type"] == "ObservationSupportsEntity"]
        self.assertEqual(len(sup), 1)
        self.assertEqual(sup[0]["observation_id"], "observation:sdr-capture-7")
        self.assertEqual(lin["violations"], [])

    def test_c3_and_c4_violations_detected(self):
        lin = build_solver_run_lineage(_evidence())
        bad = copy.deepcopy(lin)
        bad["hyperedges"][0]["variant_id"] = "variant:nope"
        self.assertTrue(any(v.startswith("C3") for v in validate_lineage(bad)))
        bad = copy.deepcopy(lin)
        bad["hyperedges"][0]["source"] = {"kind": "UNKNOWN"}
        self.assertTrue(any(v.startswith("C4") for v in validate_lineage(bad)))

    def test_works_without_dataset_files(self):
        ev = dict(_evidence(), dataset_id="no-such-dataset-v1")
        lin = build_solver_run_lineage(ev)
        self.assertEqual(lin["violations"], [])
        self.assertEqual(lin["nodes"][0]["node_id"], "env:no-such-dataset")

    def test_explanation_carries_lineage(self):
        out = explain_coverage_cell(_evidence())
        self.assertEqual(out["lineage"]["violations"], [])
        self.assertIn("SYNTHETIC_MODEL", summarize_lineage(out["lineage"]))


if __name__ == "__main__":
    unittest.main()
