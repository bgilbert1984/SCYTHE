import logging
import unittest

from hypergraph_engine import HypergraphEngine
from mcp_server import MCPHandler


class OrchestratorRegistryTest(unittest.TestCase):
    """#224: the orchestrator used to keep an empty Registry, so approved registry tools could not run."""

    @classmethod
    def setUpClass(cls):
        logging.disable(logging.CRITICAL)

    @classmethod
    def tearDownClass(cls):
        logging.disable(logging.NOTSET)

    def setUp(self):
        self.handler = MCPHandler(HypergraphEngine(), use_orchestrator=True)
        self.orch = self.handler._orchestrator

    def test_orchestrator_registry_has_the_same_tools_as_the_handler(self):
        self.assertTrue(self.orch.registry._tools)
        self.assertEqual(set(self.orch.registry._tools), set(self.handler._registry._tools))

    def test_mutation_tools_are_seen_as_mutations(self):
        self.assertIn("decay_now", self.orch._mutation_tools())

    def test_phase_0_blocks_a_registry_mutation_tool(self):
        p = self.orch.propose_action("decay_now", {"lambda": 0.001}, confidence=0.9)
        self.assertEqual(p["status"], "approved")
        r = self.orch.execute_proposal(p["proposal_id"])
        self.assertFalse(r["ok"])
        self.assertIn("observe-only", r["error"])

    def test_approved_registry_tool_executes_in_phase_2(self):
        self.orch.set_phase(2, dry_run=False)
        p = self.orch.propose_action("decay_now", {"lambda": 0.001}, confidence=0.9)
        r = self.orch.execute_proposal(p["proposal_id"])
        self.assertTrue(r["ok"], r)
        self.assertNotIn("Unknown tool", str(r))


if __name__ == "__main__":
    unittest.main()
