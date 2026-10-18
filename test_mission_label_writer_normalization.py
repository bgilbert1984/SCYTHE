"""ARR hardening item 2: recon_registry.py, rfuav_inference_service.py, and sensor_registry.py
wrote labels["missionId"] (camelCase) while graphops_graph_resolver.py's mission_id filter
(the resolver backing /api/graphops/selection/graph, mcp_server.py, scythe_orchestrator.py --
see test_graphops_graph_resolver_mission_filter.py's LegacyCamelCaseLabelCompatibilityTests for
the resolver side of this fix) only ever read labels["mission_id"]. These entities existed in
the graph but were invisible under any mission filter. Fix: normalize the writers to the
canonical snake_case key, matching the resolver's long-standing read convention.
"""
import unittest

from registries.recon_registry import _recon_graph_node
from writebus import WriteContext


class ReconRegistryLabelWriterTests(unittest.TestCase):
    def test_a_recon_entity_s_graph_node_carries_the_canonical_snake_case_label(self):
        ctx = WriteContext(mission_id="RFMAP-20261018-070")
        node = _recon_graph_node({"location": {}}, "recon:entity-1", ctx)
        self.assertEqual(node["labels"].get("mission_id"), "RFMAP-20261018-070")
        self.assertNotIn("missionId", node["labels"])

    def test_omitting_mission_id_on_the_context_produces_no_mission_label_at_all(self):
        ctx = WriteContext()
        node = _recon_graph_node({"location": {}}, "recon:entity-2", ctx)
        self.assertNotIn("mission_id", node["labels"])
        self.assertNotIn("missionId", node["labels"])


class RfuavInferenceServiceLabelWriterTests(unittest.TestCase):
    def test_source_file_no_longer_contains_a_missionid_label_write(self):
        # rfuav_inference_service.py has four separate dict-literal label write sites and no
        # existing test harness exercising any of them end to end (no WriteBus/graph fixture
        # is wired up in this codebase's test suite for this module) -- a source-level check
        # is the proportionate way to pin down "the writer side is fixed" without inventing a
        # new integration harness for a file that had zero test coverage before this change.
        # Checks the label-write pattern specifically ("missionId": <value>,) -- NOT the
        # read-side payload.get("missionId") coalesce input, which is deliberately kept so the
        # service still accepts a legacy camelCase key from callers.
        import pathlib
        source = pathlib.Path("rfuav_inference_service.py").read_text()
        self.assertNotIn('"missionId": mission_id', source)
        self.assertIn('payload.get("missionId")', source,
                       "read-side legacy-input compatibility should still be accepted")
        # Four label dicts (sensor_node, rf_node, observed_edge, and the returned
        # observation's own "labels" block) each carry a mission_id label write.
        self.assertGreaterEqual(source.count('"mission_id": mission_id,'), 4)


class SensorRegistryLabelWriterTests(unittest.TestCase):
    def test_norm_labels_writes_the_canonical_snake_case_key(self):
        from sensor_registry import _norm_labels
        labels = _norm_labels({"labels": {}}, mission_id="RFMAP-20261018-071")
        self.assertEqual(labels.get("mission_id"), "RFMAP-20261018-071")
        self.assertNotIn("missionId", labels)

    def test_norm_labels_still_accepts_a_legacy_camelcase_input_label(self):
        from sensor_registry import _norm_labels
        # Backward-compat on the READ side: an object that already carries a legacy
        # labels.missionId (written before this fix) is still recognized as having a mission.
        labels = _norm_labels({"labels": {"missionId": "RFMAP-20261018-072"}})
        self.assertEqual(labels.get("mission_id"), "RFMAP-20261018-072")


if __name__ == "__main__":
    unittest.main()
