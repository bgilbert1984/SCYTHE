import unittest
from unittest import mock

from mcp_effects import (
    AUTO_APPROVE, HARDWARE_CONTROL, REQUIRES_REVIEW, LOCAL_STATE, READ_ONLY, validate_tool_contract,
)
from mcp_registry import Registry, Tool, build_registry

OBJ = {"type": "object", "properties": {}}


def _tool(name="t", mutates_state=False, effect=READ_ONLY, parameters=OBJ, returns=OBJ):
    return Tool(name, "d", parameters, returns, lambda **k: None,
                mutates_state=mutates_state, effect=effect)


class RegisteredToolContractTest(unittest.TestCase):
    """Metadata contract for every registered MCP tool.  Runtime capability (does the
    backing engine method exist) is deliberately not checked here; see #220."""

    def test_every_registered_tool_meets_the_contract(self):
        tools = build_registry(object())["__registry__"]._tools
        self.assertTrue(tools)
        self.assertEqual(validate_tool_contract(tools), [])

    def test_every_tool_in_the_served_registries_meets_the_contract(self):
        # build_registry is not the whole set: RF tools are registered afterwards, into
        # both the handler's registry and the orchestrator's.
        import logging
        from hypergraph_engine import HypergraphEngine
        from mcp_server import MCPHandler
        from rf_mcp import register_rf_tools

        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        engine = HypergraphEngine()
        handler = MCPHandler(engine, use_orchestrator=True)
        register_rf_tools(engine, handler)
        for registry in (handler._registry, handler._orchestrator.registry):
            self.assertIn("rf_tune", registry._tools)
            self.assertEqual(validate_tool_contract(registry._tools), [])


class ToolDefContractTest(unittest.TestCase):
    """The GraphOps tools are ToolDef objects in handler._tools, outside both Registry
    instances, so the registry checks above never saw them."""

    @classmethod
    def setUpClass(cls):
        import logging
        from unittest import mock
        from hypergraph_engine import HypergraphEngine
        from mcp_server import MCPHandler
        from graphops_autopilot import register_autopilot_tools
        from graphops_copilot import register_graphops_tools

        logging.disable(logging.CRITICAL)
        cls.addClassCleanup(logging.disable, logging.NOTSET)
        engine = HypergraphEngine()
        handler = MCPHandler(engine, use_orchestrator=True)
        register_graphops_tools(engine, handler)
        register_autopilot_tools(engine, handler, autopilot=mock.Mock())
        cls.tooldefs = {
            name: tool for name, tool in handler._tools.items()
            if name.startswith("graphops_") and name not in handler._registry._tools
        }

    def test_the_graphops_tools_were_found(self):
        self.assertIn("graphops_submit_feedback", self.tooldefs)
        self.assertGreaterEqual(len(self.tooldefs), 12)

    def test_every_graphops_tool_meets_the_contract(self):
        self.assertEqual(validate_tool_contract(self.tooldefs), [])


class ValidatorTest(unittest.TestCase):
    def problems(self, tool, name=None):
        return validate_tool_contract({name or tool.name: tool})

    def test_accepts_a_well_formed_tool(self):
        self.assertEqual(self.problems(_tool()), [])
        self.assertEqual(self.problems(_tool(mutates_state=True, effect=HARDWARE_CONTROL)), [])

    def test_rejects_an_inferred_effect(self):
        self.assertTrue(any("not explicitly declared" in p for p in self.problems(_tool(effect=None))))

    def test_rejects_a_missing_schema(self):
        self.assertTrue(any("parameters must be an object schema" in p for p in self.problems(_tool(parameters={}))))
        self.assertTrue(any("returns must be an object schema" in p for p in self.problems(_tool(returns=None))))

    def test_rejects_mutates_state_that_contradicts_the_effect(self):
        tool = _tool(mutates_state=True, effect=LOCAL_STATE)
        tool.mutates_state = False   # drift after construction
        self.assertTrue(any("contradicts effect" in p for p in self.problems(tool)))

    def test_rejects_an_approval_policy_that_does_not_match_the_effect(self):
        with mock.patch("mcp_effects.approval_policy", return_value=AUTO_APPROVE):
            problems = self.problems(_tool(mutates_state=True, effect=LOCAL_STATE))
        self.assertTrue(any("approval policy" in p for p in problems))
        with mock.patch("mcp_effects.approval_policy", return_value=REQUIRES_REVIEW):
            problems = self.problems(_tool())
        self.assertTrue(any("approval policy" in p for p in problems))

    def test_rejects_a_tool_registered_under_another_name(self):
        self.assertTrue(any("different name" in p for p in self.problems(_tool("a"), name="b")))

    def test_registry_refuses_a_duplicate_name(self):
        reg = Registry()
        reg.register(_tool("dup"))
        with self.assertRaises(ValueError):
            reg.register(_tool("dup"))


if __name__ == "__main__":
    unittest.main()
