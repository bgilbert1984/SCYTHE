import importlib.util
import json
import logging
import sys
import unittest
from unittest import mock

from hypergraph_engine import HypergraphEngine
from mcp_effects import LOCAL_STATE, READ_ONLY, validate_tool_contract
from mcp_registry import Tool
from mcp_server import MCPHandler, ToolDef

HAS_JSONSCHEMA = importlib.util.find_spec("jsonschema") is not None
SCHEMA = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}


def _audit_records(captured):
    """The JSON bodies of the [MCP_AUDIT] lines in an assertLogs capture."""
    return [json.loads(line.split("[MCP_AUDIT] ", 1)[1]) for line in captured.output if "[MCP_AUDIT]" in line]


class ReadOnlyToolDefParityTest(unittest.TestCase):
    """#236 / ADR 0005: execution semantics belong to the control plane, not the tool class.
    A non-mutating ToolDef runs through Registry.execute like a Registry tool; only the
    ToolDef response envelope differs."""

    @classmethod
    def setUpClass(cls):
        logging.disable(logging.NOTSET)

    def setUp(self):
        self.calls = []
        self.handler = MCPHandler(HypergraphEngine(), use_orchestrator=False)
        self.handler._tools["probe_read"] = ToolDef(
            "probe_read", "d", SCHEMA, lambda params: self.calls.append(params) or {"seen": params["n"]},
            effect=READ_ONLY,
        )
        self.handler._registry.register(Tool(
            "probe_registry_read", "d", SCHEMA, {"type": "object"},
            lambda engine, params: {"seen": params["n"]}, effect=READ_ONLY,
        ))
        self.handler._tools["probe_registry_read"] = self.handler._tools["probe_read"]  # listed like the real ones

    def call(self, name, **arguments):
        return self.handler._handle_tools_call({"name": name, "arguments": arguments})

    def test_the_tooldef_response_envelope_is_unchanged(self):
        r = self.call("probe_read", n=3)
        self.assertFalse(r["isError"])
        self.assertEqual(json.loads(r["content"][0]["text"]), {"seen": 3})
        self.assertEqual(self.calls, [{"n": 3}])

    def test_the_call_takes_the_registry_lifecycle(self):
        self.call("probe_read", n=1)
        self.assertEqual(self.handler._registry.get_metrics()["invocations"]["probe_read"], 1)

    def test_a_second_call_reuses_the_adapted_tool(self):
        self.call("probe_read", n=1)
        self.call("probe_read", n=2)
        self.assertEqual(self.handler._registry.get_metrics()["invocations"]["probe_read"], 2)

    def test_the_audit_record_has_the_same_shape_for_both_classes(self):
        with self.assertLogs("mcp_registry", level="INFO") as cap:
            self.call("probe_read", n=1)
            self.handler._registry.execute(self.handler.engine, "probe_registry_read", {"n": 1})
        tooldef_rec, registry_rec = _audit_records(cap)
        self.assertEqual(set(tooldef_rec), set(registry_rec))
        self.assertEqual((tooldef_rec["effect"], tooldef_rec["mutates_state"]), (READ_ONLY, False))
        self.assertEqual(tooldef_rec["tool"], "probe_read")

    @unittest.skipUnless(HAS_JSONSCHEMA, "Registry skips parameter validation without jsonschema")
    def test_parameters_are_validated_against_the_input_schema(self):
        with self.assertRaises(ValueError):
            self.call("probe_read", n="not an int")
        self.assertEqual(self.calls, [])

    def test_a_mutating_tooldef_is_still_refused_and_not_adopted(self):
        self.handler._tools["probe_write"] = ToolDef(
            "probe_write", "d", SCHEMA, lambda params: self.calls.append(params), mutates_state=True, effect=LOCAL_STATE)
        with self.assertRaises(RuntimeError):
            self.call("probe_write", n=1)
        self.assertEqual(self.calls, [])
        self.assertNotIn("probe_write", self.handler._registry._tools)

    def test_a_tool_error_still_propagates(self):
        def boom(params):
            raise KeyError("boom")
        self.handler._tools["probe_boom"] = ToolDef("probe_boom", "d", {"type": "object"}, boom, effect=READ_ONLY)
        with self.assertLogs("mcp_registry", level="ERROR"), self.assertRaises(KeyError):
            self.call("probe_boom")


class AllToolDefsTest(unittest.TestCase):
    """Every ToolDef the server registers, not only the graphops_* ones, meets the contract."""

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
        # embedding_engine imports faiss at module level; the tools only need the module to import.
        with mock.patch.dict(sys.modules, {"faiss": mock.MagicMock()}):
            from embedding_engine import register_embedding_tools
            register_embedding_tools(engine, cls.handler, mock.Mock())
        cls.tooldefs = {n: t for n, t in cls.handler._tools.items() if n not in cls.handler._registry._tools}

    def test_the_embedding_tools_were_found(self):
        self.assertTrue({"embed_entity", "search_similar_entities", "detect_anomaly_pattern",
                         "stitch_identities", "get_semantic_clusters"} <= set(self.tooldefs))

    def test_every_tooldef_meets_the_contract(self):
        self.assertEqual(validate_tool_contract(self.tooldefs), [])

    def test_embed_entity_is_a_local_state_mutation(self):
        tool = self.tooldefs["embed_entity"]
        self.assertEqual((tool.mutates_state, tool.effect, tool.effect_declared), (True, LOCAL_STATE, True))
        with self.assertRaises(RuntimeError):
            self.handler._handle_tools_call({"name": "embed_entity", "arguments": {"entity_id": "x"}})

    def test_the_other_embedding_tools_are_read_only(self):
        for name in ("search_similar_entities", "detect_anomaly_pattern", "stitch_identities", "get_semantic_clusters"):
            self.assertEqual((self.tooldefs[name].effect, self.tooldefs[name].mutates_state), (READ_ONLY, False), name)

    @unittest.skipUnless(HAS_JSONSCHEMA, "needs jsonschema")
    def test_every_input_schema_is_a_valid_json_schema(self):
        from jsonschema import Draft7Validator
        for name, tool in self.tooldefs.items():
            Draft7Validator.check_schema(tool.input_schema)


if __name__ == "__main__":
    unittest.main()
