"""Startup enforcement of the tool contract (#221).

validate_tool_contract was only run by tests, so a tool registered with an invalid
declaration would have been served.  The check now runs when the routes are registered
and again after the late registrations, and a violation stops startup.
"""
import ast
import logging
import pathlib
import sys
import unittest
from unittest import mock

from flask import Flask

from hypergraph_engine import HypergraphEngine
from mcp_effects import (
    HARDWARE_CONTROL, READ_ONLY, ToolContractError, assert_tool_contract, validate_tool_contract,
)
from mcp_server import MCPHandler, ToolDef, register_mcp_routes

OBJ = {"type": "object", "properties": {}}


def _quiet(case):
    logging.disable(logging.CRITICAL)
    case.addCleanup(logging.disable, logging.NOTSET)


def _undeclared(name="late_tool"):
    """A tool that never classified itself: inferred effect, effect_declared False."""
    return ToolDef(name, "d", OBJ, lambda params: {})


class AssertToolContractTest(unittest.TestCase):
    def test_a_valid_set_passes(self):
        good = ToolDef("ok", "d", OBJ, lambda p: {}, effect=READ_ONLY)
        assert_tool_contract({"ok": good})

    def test_every_violation_is_reported_in_one_error(self):
        bad = {"a": _undeclared("a"), "b": _undeclared("b")}
        with self.assertRaises(ToolContractError) as caught:
            assert_tool_contract(bad)
        self.assertEqual(len(caught.exception.problems), 2)
        self.assertEqual(caught.exception.problems, validate_tool_contract(bad))
        self.assertIn("a: effect is not explicitly declared", str(caught.exception))


class HandlerContractTest(unittest.TestCase):
    def setUp(self):
        _quiet(self)
        self.engine = HypergraphEngine()

    def test_the_production_tool_set_passes(self):
        """Everything the server registers, including the late embedding tools."""
        from graphops_autopilot import register_autopilot_tools
        from graphops_copilot import register_graphops_tools
        from rf_mcp import register_rf_tools

        handler = MCPHandler(self.engine, use_orchestrator=True)
        register_graphops_tools(self.engine, handler)
        register_autopilot_tools(self.engine, handler, autopilot=mock.Mock())
        register_rf_tools(self.engine, handler)
        import duckdb  # noqa: F401  (see test_mcp_tooldef_parity for why this comes first)
        with mock.patch.dict(sys.modules, {"faiss": mock.MagicMock()}):
            from embedding_engine import register_embedding_tools
            register_embedding_tools(self.engine, handler, mock.Mock())
        served = handler.served_tools()
        self.assertIn("rf_tune", served)
        self.assertIn("embed_entity", served)
        self.assertIn("graphops_submit_feedback", served)
        handler.assert_contract()

    def test_served_tools_carry_declared_metadata_not_the_wrappers(self):
        handler = MCPHandler(self.engine, use_orchestrator=True)
        tool = handler.served_tools()["set_scope_filter"]
        self.assertTrue(tool.effect_declared)

    def test_an_undeclared_tool_in_the_handler_fails(self):
        handler = MCPHandler(self.engine, use_orchestrator=True)
        handler._tools["late_tool"] = _undeclared()
        with self.assertRaises(ToolContractError) as caught:
            handler.assert_contract()
        self.assertEqual(caught.exception.problems, ["late_tool: effect is not explicitly declared"])

    def test_a_tool_whose_flag_drifted_from_its_effect_fails(self):
        handler = MCPHandler(self.engine, use_orchestrator=True)
        tool = ToolDef("drift", "d", OBJ, lambda p: {}, mutates_state=True, effect=HARDWARE_CONTROL)
        tool.mutates_state = False
        handler._tools["drift"] = tool
        with self.assertRaises(ToolContractError):
            handler.assert_contract()

    def test_a_tool_only_in_the_orchestrator_registry_is_checked(self):
        handler = MCPHandler(self.engine, use_orchestrator=True)
        handler._orchestrator.registry._tools["only_here"] = _undeclared("only_here")
        with self.assertRaises(ToolContractError):
            handler.assert_contract()


class RegisterRoutesTest(unittest.TestCase):
    def setUp(self):
        _quiet(self)

    def test_routes_register_with_the_real_tools(self):
        handler = register_mcp_routes(Flask(__name__), HypergraphEngine(), use_orchestrator=True)
        self.assertIn("rf_tune", handler._tools)

    def test_an_invalid_late_registration_stops_startup(self):
        def add_bad(engine, handler):
            handler._tools["bad"] = _undeclared("bad")
        with mock.patch("rf_mcp.register_rf_tools", add_bad):
            with self.assertRaises(ToolContractError):
                register_mcp_routes(Flask(__name__), HypergraphEngine(), use_orchestrator=True)

    def test_an_unavailable_integration_does_not_stop_startup(self):
        """An integration that cannot load registers nothing; that is not a violation."""
        for target in ("rf_mcp.register_rf_tools", "graphops_copilot.register_graphops_tools",
                       "graphops_autopilot.register_autopilot_tools"):
            with self.subTest(target=target):
                with mock.patch(target, side_effect=ImportError("integration unavailable")):
                    handler = register_mcp_routes(Flask(__name__), HypergraphEngine(), use_orchestrator=True)
                self.assertTrue(handler._tools)


class ServerWiringTest(unittest.TestCase):
    """rf_scythe_api_server is too heavy to import here; read its startup block."""

    @classmethod
    def setUpClass(cls):
        src = (pathlib.Path(__file__).parent / "rf_scythe_api_server.py").read_text()
        tree = ast.parse(src)
        cls.try_nodes = [
            n for n in ast.walk(tree) if isinstance(n, ast.Try)
            and any("register_mcp_routes" in ast.unparse(s) for s in n.body)
        ]
        cls.src = src

    def test_the_contract_error_is_not_swallowed_by_the_startup_block(self):
        self.assertEqual(len(self.try_nodes), 1)
        handlers = self.try_nodes[0].handlers
        self.assertEqual(ast.unparse(handlers[0].type), "ToolContractError")
        self.assertIsInstance(handlers[0].body[0], ast.Raise)
        # The specific handler must come before the catch-all that logs and continues.
        self.assertEqual(ast.unparse(handlers[1].type), "Exception")

    def test_late_registrations_are_checked_after_the_embedding_block(self):
        body = ast.unparse(self.try_nodes[0])
        self.assertLess(body.index("register_embedding_tools"), body.index("mcp_handler.assert_contract()"))


if __name__ == "__main__":
    unittest.main()
