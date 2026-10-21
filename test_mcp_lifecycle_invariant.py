import logging
import sys
import unittest
from unittest import mock

from hypergraph_engine import HypergraphEngine
from mcp_effects import NON_MUTATING
from mcp_registry import Registry
from mcp_server import MCPHandler


def _minimal_arguments(schema):
    """Arguments that satisfy the required properties of an object schema."""
    values = {"string": "x", "integer": 1, "number": 1.0, "boolean": True, "array": [], "object": {}}
    props = (schema or {}).get("properties", {})
    return {k: values.get((props.get(k) or {}).get("type"), "x") for k in (schema or {}).get("required", [])}


class LifecycleInvariantTest(unittest.TestCase):
    """#236 / ADR 0005: every externally callable MCP tool traverses exactly one lifecycle,
    ``Registry.execute``, whichever class implements it. Stated over the whole tool set, so a
    tool class or registration path added later is held to it without a new test."""

    @classmethod
    def setUpClass(cls):
        from graphops_autopilot import register_autopilot_tools
        from graphops_copilot import register_graphops_tools

        logging.disable(logging.CRITICAL)
        cls.addClassCleanup(logging.disable, logging.NOTSET)
        engine = HypergraphEngine()
        cls.handler = MCPHandler(engine, use_orchestrator=True)
        register_graphops_tools(engine, cls.handler)
        register_autopilot_tools(engine, cls.handler, autopilot=mock.Mock())
        # register_mcp_routes registers these too; leaving them out left both HARDWARE_CONTROL tools untested.
        from rf_mcp import register_rf_tools
        register_rf_tools(engine, cls.handler)
        # embedding_engine imports faiss at module level; the tools only need the module to import.
        with mock.patch.dict(sys.modules, {"faiss": mock.MagicMock()}):
            from embedding_engine import register_embedding_tools
            register_embedding_tools(engine, cls.handler, mock.Mock())
        cls.names = sorted(set(cls.handler._tools) | set(cls.handler._registry._tools))

    def setUp(self):
        self.entered = []
        self.ran = []
        real = Registry.execute

        def spy(registry, engine, name, params, *a, **kw):
            self.entered.append(name)
            return real(registry, engine, name, params, *a, **kw)

        patcher = mock.patch.object(Registry, "execute", spy)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _implementation(self, name):
        """The tool object a caller of the server would reach, with its body replaced by a stub."""
        # A Registry tool is listed in ``_tools`` through a ToolCompat shim; the registry's own
        # object is the tool.
        registered = self.handler._registry._tools.get(name)
        tool = registered or self.handler._tools[name]
        stub_name = "run" if hasattr(tool, "run") else "fn"
        stub = (lambda engine, params: self.ran.append(name) or {}) if stub_name == "run" \
            else (lambda params: self.ran.append(name) or {})
        for t in {id(tool): tool, id(registered): registered}.values():
            if t is not None and hasattr(t, stub_name):
                setattr(t, stub_name, stub)
        return tool

    def test_the_hardware_control_tools_are_in_the_set(self):
        self.assertTrue({"rf_tune", "rf_capture_control"} <= set(self.names))

    def test_the_tool_set_is_not_empty_and_spans_both_classes(self):
        classes = {n in self.handler._registry._tools for n in self.names}
        self.assertEqual(classes, {True, False})

    def test_every_non_mutating_tool_is_executed_by_the_registry_exactly_once(self):
        for name in self.names:
            tool = self._implementation(name)
            if tool.effect in NON_MUTATING:
                before = len(self.entered)
                try:
                    self.handler._handle_tools_call({"name": name, "arguments": _minimal_arguments(
                        getattr(tool, "parameters", getattr(tool, "input_schema", None)))})
                except Exception:
                    pass  # a rejection still entered the lifecycle; entry is what is counted
                self.assertEqual(self.entered[before:], [name], name)

    def test_every_mutating_tool_is_refused_direct_execution_and_never_enters_the_registry(self):
        for name in self.names:
            tool = self._implementation(name)
            if tool.effect not in NON_MUTATING:
                before = len(self.entered)
                with self.assertRaises(RuntimeError, msg=name):
                    self.handler._handle_tools_call({"name": name, "arguments": {}})
                self.assertEqual(self.entered[before:], [], name)
                self.assertEqual(self.ran, [], name)

    def test_every_mutating_tool_is_executed_by_the_registry_exactly_once_via_the_orchestrator(self):
        orch = self.handler._orchestrator
        orch.set_phase(2, dry_run=False)
        for name in self.names:
            tool = self._implementation(name)
            if tool.effect in NON_MUTATING:
                continue
            args = _minimal_arguments(getattr(tool, "parameters", getattr(tool, "input_schema", None)))
            proposal = orch.propose_action(name, args, confidence=0.9)
            if proposal.get("status") != "approved":
                continue  # the approval layer, not the lifecycle, declined it
            before = len(self.entered)
            orch.execute_proposal(proposal["proposal_id"])
            self.assertEqual(self.entered[before:], [name], name)


if __name__ == "__main__":
    unittest.main()
