"""#217: without jsonschema the registry cannot enforce a tool's schema, so a tool that changes
state must refuse to run rather than execute unchecked input."""
import unittest
from unittest import mock

import mcp_registry
from hypergraph_engine import HypergraphEngine


class MissingValidatorTest(unittest.TestCase):
    def setUp(self):
        self.engine = HypergraphEngine()
        self.reg = mcp_registry.build_registry(self.engine)["__registry__"]

    def _without_jsonschema(self):
        return mock.patch.dict("sys.modules", {"jsonschema": None})

    def test_a_mutating_tool_is_refused(self):
        with self._without_jsonschema(), self.assertRaises(RuntimeError) as cm:
            self.reg.execute(self.engine, "decay_now", {"lambda": 0.001}, agent_mode="admin")
        self.assertIn("jsonschema", str(cm.exception))

    def test_the_refusal_counts_as_an_error(self):
        with self._without_jsonschema(), self.assertRaises(RuntimeError):
            self.reg.execute(self.engine, "decay_now", {}, agent_mode="admin")
        self.assertEqual(self.reg.get_metrics()["errors"]["decay_now"], 1)

    def test_a_read_only_tool_still_runs(self):
        with self._without_jsonschema():
            self.assertIn("node_count", self.reg.execute(self.engine, "get_engine_metrics", {}))


if __name__ == "__main__":
    unittest.main()
