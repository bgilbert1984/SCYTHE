import json
import unittest

from graphops_autopilot import GraphOpsAutopilot
from graphops_copilot import InvestigativeDSLExecutor
from mcp_server import MCPHandler
from rf_bridge import get_rf_bridge, reset_rf_bridge_for_tests
from rf_mcp import correlate_rf_graph, register_rf_tools


class _Engine:
    nodes = {}

    def __init__(self):
        self.edges = {
            "e1": {"id": "e1", "source": "host-a", "target": "host-b", "timestamp": 100.4},
            "e2": {"id": "e2", "source": "host-c", "target": "host-d", "timestamp": 120.0},
        }
        self.degree = {}


class RFMCPTests(unittest.TestCase):
    def setUp(self):
        reset_rf_bridge_for_tests()
        self.store = get_rf_bridge().observations
        self.observation = self.store.ingest_frame({
            "timestamp": 100.0, "sequence": 1, "sensor_id": "edge-a",
            "center_frequency_hz": 433_920_000, "peak_frequency_hz": 433_921_000,
            "sample_rate_hz": 1_000_000, "peak_dbfs": -25, "noise_floor_dbfs": -50,
        })

    def tearDown(self):
        reset_rf_bridge_for_tests()

    def test_correlation_preserves_evidence_and_marks_inference(self):
        result = correlate_rf_graph(_Engine(), [self.observation], window_s=1.0)
        self.assertEqual(result["finding_class"], "INFERRED")
        self.assertFalse(result["raw_iq_exposed"])
        self.assertEqual(result["correlations"][0]["evidence_id"], self.observation["evidence_id"])
        self.assertEqual(result["correlations"][0]["graph_matches"][0]["edge_id"], "e1")

    def test_copilot_rf_correlate_applies_frequency_and_time_window(self):
        class Provider:
            def __init__(self, observation):
                self.observation = observation
                self.query_args = None

            def query(self, **kwargs):
                self.query_args = kwargs
                return [self.observation]

        provider = Provider(self.observation)
        executor = InvestigativeDSLExecutor(_Engine(), rf_observation_provider=provider)
        result = executor._do_rf_correlate("RF_CORRELATE freq=433.9MHz window=1s")
        self.assertEqual(provider.query_args["frequency_hz"], 433_900_000)
        self.assertEqual(provider.query_args["tolerance_hz"], 25_000)
        self.assertEqual(result["window_s"], 1.0)
        self.assertEqual(result["correlations"][0]["finding_class"], "INFERRED")

    def test_mcp_query_is_read_only_and_direct_control_is_rejected(self):
        handler = MCPHandler(_Engine(), use_orchestrator=True)
        register_rf_tools(handler.engine, handler)
        query = handler.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                "params": {"name": "rf_observations_query", "arguments": {}}})
        self.assertEqual(query["result"]["evidence_class"], "OBSERVED")
        rejected = handler.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                   "params": {"name": "rf_tune", "arguments": {"frequency_hz": 1}}})
        self.assertIn("error", rejected)
        self.assertIn("orchestrate/propose", rejected["error"]["message"])

        proposal = handler._orchestrator.propose_action(
            "rf_capture_control", {"action": "start"}, confidence=0.99,
            justification="test safety boundary",
        )
        blocked = handler._orchestrator.execute_proposal(proposal["proposal_id"])
        self.assertFalse(blocked["ok"])
        self.assertIn("observe-only", blocked["error"])

        handler._orchestrator.set_phase(1, dry_run=False)
        shadow_proposal = handler._orchestrator.propose_action(
            "rf_capture_control", {"action": "start"}, confidence=0.99,
            justification="test shadow boundary",
        )
        shadow = handler._orchestrator.execute_proposal(shadow_proposal["proposal_id"])
        self.assertTrue(shadow["ok"])
        self.assertTrue(shadow["result"]["_shadow_mode"])
        self.assertFalse(get_rf_bridge().status()["running"])

    def test_autopilot_routes_rf_evidence_to_suggestion_queue(self):
        pilot = GraphOpsAutopilot(_Engine())
        pilot.handle_rf_observation({**self.observation, "snr_db": 15.0})
        suggestions = pilot.get_suggestion_queue()
        self.assertEqual(suggestions[0]["pattern"], "rf_peak")
        self.assertEqual(suggestions[0]["evidence_refs"], [self.observation["evidence_id"]])


if __name__ == "__main__":
    unittest.main()
