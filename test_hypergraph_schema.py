import copy
import unittest

from rf_solver_evidence import RFSolverEvidenceStore, explain_coverage_cell
from scythe_hypergraph_schema import (
    BASIS_EXPLICIT, BASIS_FREQUENCY, build_solver_run_lineage, discover_frequency_candidates,
    load_dataset_context, resolve_observations, summarize_lineage, validate_lineage,
)


def _obs(eid, freq=900.01e6, t=1000.0, source="sdrpp_iq_exporter", cls="OBSERVED"):
    return {"evidence_id": eid, "observed_at": t, "sensor_id": "edge-a", "peak_frequency_hz": freq,
            "center_frequency_hz": 900e6, "snr_db": 20.0, "source": source,
            "evidence_class": cls, "mission_id": None}


class _FakeStore:
    def __init__(self, items):
        self.items = items

    def query(self, *, since=None, until=None, frequency_hz=None, tolerance_hz=25_000.0,
              min_snr_db=None, sensor_id=None, limit=100):
        out = []
        for o in self.items:
            if since is not None and o["observed_at"] < since: continue
            if until is not None and o["observed_at"] > until: continue
            if frequency_hz is not None and abs(o["peak_frequency_hz"] - frequency_hz) > tolerance_hz: continue
            out.append(o)
        return out[:limit]

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


class ObservationStoreWiringTests(unittest.TestCase):
    def setUp(self):
        self.store = _FakeStore([
            _obs("rf-real"), _obs("rf-synth", source="operator_synthetic"),
            _obs("rf-far", freq=433e6), _obs("rf-old", t=-99999.0),
        ])
        self.ev = _evidence()

    def test_resolve_reports_missing_and_excludes_synthetic(self):
        r = resolve_observations(self.store, ["observation:rf-real", "rf-synth", "rf-nope"])
        self.assertEqual([o["evidence_id"] for o in r["found"]], ["rf-real"])
        self.assertEqual(r["missing"], ["rf-nope"])
        self.assertEqual(r["excluded"], ["rf-synth"])

    def test_discovery_is_frequency_and_time_bounded_and_measured_only(self):
        ids = [o["evidence_id"] for o in discover_frequency_candidates(self.store, self.ev, window_s=3600)]
        self.assertEqual(ids, ["rf-real"])

    def test_links_carry_basis_and_are_never_verified(self):
        lin = build_solver_run_lineage(self.ev, observation_links=[
            (_obs("rf-a"), BASIS_EXPLICIT), (_obs("rf-b"), BASIS_FREQUENCY)])
        edges = {e["observation_id"]: e for e in lin["hyperedges"]
                 if e["type"] == "ObservationSupportsEntity"}
        self.assertEqual(edges["observation:rf-a"]["basis"], BASIS_EXPLICIT)
        self.assertEqual(edges["observation:rf-b"]["basis"], BASIS_FREQUENCY)
        self.assertTrue(all(e["verified"] is False for e in edges.values()))
        node = next(n for n in lin["nodes"] if n["node_id"] == "observation:rf-a")
        self.assertFalse(node["has_sensor_location"])
        self.assertEqual(lin["violations"], [])
        self.assertIn("UNVERIFIED", summarize_lineage(lin))

    def test_synthetic_observation_never_becomes_a_node(self):
        lin = build_solver_run_lineage(self.ev, observation_links=[
            (_obs("rf-s", source="operator_synthetic"), BASIS_EXPLICIT)])
        self.assertFalse(any(n["type"] == "ObservationNode" for n in lin["nodes"]))

    def test_dangling_or_verified_edges_are_violations(self):
        lin = build_solver_run_lineage(self.ev, observation_links=[(_obs("rf-a"), BASIS_EXPLICIT)])
        bad = copy.deepcopy(lin)
        bad["nodes"] = [n for n in bad["nodes"] if n["type"] != "ObservationNode"]
        self.assertTrue(any("missing ObservationNode" in v for v in validate_lineage(bad)))
        bad = copy.deepcopy(lin)
        next(e for e in bad["hyperedges"] if e["type"] == "ObservationSupportsEntity")["verified"] = True
        self.assertTrue(any("claims verification" in v for v in validate_lineage(bad)))

    def test_real_store_round_trip(self):
        try:
            from rf_bridge import RFObservationStore
        except Exception as exc:  # optional deps (numpy) absent in some environments
            self.skipTest(f"rf_bridge unavailable: {exc}")
        store = RFObservationStore()
        item = store.ingest_frame({"timestamp": 1000.0, "sequence": 1, "sensor_id": "edge-a",
            "center_frequency_hz": 900e6, "peak_frequency_hz": 900.01e6, "sample_rate_hz": 1e6,
            "peak_dbfs": -25, "noise_floor_dbfs": -50})
        synth = store.ingest_frame({"timestamp": 1001.0, "sequence": 2, "sensor_id": "edge-b",
            "center_frequency_hz": 900e6, "peak_frequency_hz": 900.02e6, "sample_rate_hz": 1e6,
            "peak_dbfs": -25, "noise_floor_dbfs": -50, "observation_origin": "OPERATOR_SYNTHETIC"})
        r = resolve_observations(store, [item["evidence_id"], synth["evidence_id"]])
        self.assertEqual(len(r["found"]), 1)
        self.assertEqual(r["excluded"], [synth["evidence_id"]])
        lin = build_solver_run_lineage(self.ev, observation_links=[(o, BASIS_EXPLICIT) for o in r["found"]])
        self.assertEqual(lin["violations"], [])


if __name__ == "__main__":
    unittest.main()
