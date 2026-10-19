import unittest
from types import SimpleNamespace

from hypergraph_engine import HypergraphEngine
from mcp_effects import (
    EXTERNAL_STATE, HARDWARE_CONTROL, LOCAL_STATE, READ_ONLY, TRAINING_DATA,
    resolve_effect, undeclared_effects,
)
from mcp_registry import Tool
from mcp_server import MCPHandler, ToolDef


class ResolveEffectTest(unittest.TestCase):
    def test_inferred_when_undeclared(self):
        self.assertEqual(resolve_effect("t", False, None), (READ_ONLY, False))
        self.assertEqual(resolve_effect("t", True, None), (LOCAL_STATE, False))

    def test_declared_must_agree_with_mutates_state(self):
        self.assertEqual(resolve_effect("t", True, EXTERNAL_STATE), (EXTERNAL_STATE, True))
        self.assertEqual(resolve_effect("t", False, READ_ONLY), (READ_ONLY, True))
        for effect in (LOCAL_STATE, EXTERNAL_STATE, HARDWARE_CONTROL, TRAINING_DATA):
            with self.assertRaises(ValueError):
                resolve_effect("t", False, effect)
        with self.assertRaises(ValueError):
            resolve_effect("t", True, READ_ONLY)

    def test_unknown_category_rejected(self):
        with self.assertRaises(ValueError):
            resolve_effect("t", True, "SORT_OF_MUTATING")

    def test_both_tool_classes_enforce_it(self):
        with self.assertRaises(ValueError):
            Tool("x", "d", {}, {}, lambda **k: None, mutates_state=False, effect=HARDWARE_CONTROL)
        with self.assertRaises(ValueError):
            ToolDef("x", "d", {}, lambda p: None, mutates_state=False, effect=TRAINING_DATA)
        self.assertTrue(Tool("x", "d", {}, {}, lambda **k: None, mutates_state=True,
                             effect=HARDWARE_CONTROL).effect_declared)

    def test_undeclared_report(self):
        a = ToolDef("a", "d", {}, lambda p: None)
        b = ToolDef("b", "d", {}, lambda p: None, mutates_state=True, effect=LOCAL_STATE)
        self.assertEqual(undeclared_effects({"a": a, "b": b}), ["a"])


class RegisteredToolsTest(unittest.TestCase):
    """The tools whose declarations were wrong, as actually registered."""

    @classmethod
    def setUpClass(cls):
        cls.handler = MCPHandler(HypergraphEngine())
        from graphops_autopilot import GraphOpsAutopilot, register_autopilot_tools
        from graphops_copilot import register_graphops_tools
        register_graphops_tools(HypergraphEngine(), cls.handler)
        register_autopilot_tools(HypergraphEngine(), cls.handler)
        cls._autopilot = GraphOpsAutopilot.get_instance()

    @classmethod
    def tearDownClass(cls):
        if cls._autopilot is not None:
            cls._autopilot.stop()

    def test_submit_feedback_is_mutating_training_data(self):
        tool = self.handler._tools["graphops_submit_feedback"]
        self.assertTrue(tool.mutates_state)
        self.assertEqual((tool.effect, tool.effect_declared), (TRAINING_DATA, True))

    def test_explain_coverage_cell_is_mutating(self):
        tool = self.handler._tools["graphops_explain_coverage_cell"]
        self.assertTrue(tool.mutates_state)
        self.assertEqual((tool.effect, tool.effect_declared), (LOCAL_STATE, True))

    def test_direct_execution_is_refused(self):
        for name, args in (("graphops_submit_feedback", {"card_id": "x", "verdict": "true_positive"}),
                           ("graphops_explain_coverage_cell", {"sample": {}})):
            with self.assertRaises(RuntimeError, msg=name) as ctx:
                self.handler._handle_tools_call({"name": name, "arguments": args})
            self.assertIn("disabled", str(ctx.exception))

    def test_read_only_graphops_tools_still_run_directly(self):
        out = self.handler._handle_tools_call({"name": "graphops_autopilot_status", "arguments": {}})
        self.assertFalse(out["isError"])

    def test_rf_hardware_tools_declare_hardware_control(self):
        from rf_mcp import _mutation_tools
        tools = {t.name: t for t in _mutation_tools()}
        self.assertEqual({n: t.effect for n, t in tools.items()},
                         {"rf_tune": HARDWARE_CONTROL, "rf_capture_control": HARDWARE_CONTROL})


if __name__ == "__main__":
    unittest.main()
