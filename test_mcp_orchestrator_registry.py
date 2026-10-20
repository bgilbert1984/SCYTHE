import importlib.util
import logging
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import control_audit
from hypergraph_engine import HypergraphEngine
from mcp_effects import LOCAL_STATE, validate_tool_contract
from mcp_server import MCPHandler, ToolDef


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

    def test_every_mutating_registry_tool_declares_its_effect(self):
        undeclared = sorted(
            name for name, tool in self.orch.registry._tools.items()
            if tool.mutates_state and not tool.effect_declared
        )
        self.assertEqual(undeclared, [])

    def test_set_scope_filter_is_a_declared_local_state_mutation(self):
        tool = self.orch.registry._tools["set_scope_filter"]
        self.assertTrue(tool.mutates_state)
        self.assertEqual((tool.effect, tool.effect_declared), ("LOCAL_STATE", True))
        self.assertIn("set_scope_filter", self.orch._mutation_tools())

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


class ToolDefThroughOrchestratorTest(unittest.TestCase):
    """#236: a mutating ToolDef used to be approved and then fail with ``Unknown tool``.
    It now takes the same Registry lifecycle as a Registry tool (ADR 0005, option B)."""

    SCHEMA = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"],
              "additionalProperties": False}

    @classmethod
    def setUpClass(cls):
        logging.disable(logging.CRITICAL)

    @classmethod
    def tearDownClass(cls):
        logging.disable(logging.NOTSET)

    def setUp(self):
        self.calls = []
        self.handler = MCPHandler(HypergraphEngine(), use_orchestrator=True)
        self.handler._tools["probe_mutation"] = ToolDef(
            "probe_mutation", "test tool", self.SCHEMA,
            lambda params: self.calls.append(params) or {"ok": True},
            mutates_state=True, effect=LOCAL_STATE,
        )
        self.orch = self.handler._orchestrator
        self.audit_dir = tempfile.TemporaryDirectory()
        self.audit_path = os.path.join(self.audit_dir.name, "audit.sqlite3")
        control_audit.configure(self.audit_path, retention_days=90)

    def tearDown(self):
        control_audit.reset()
        self.audit_dir.cleanup()

    def audit_rows(self):
        with sqlite3.connect(self.audit_path) as conn:
            return conn.execute(
                "SELECT action, effect, result, proposal_id FROM control_audit_events ORDER BY ts_epoch, rowid"
            ).fetchall()

    def test_proposal_is_approved_with_the_declared_effect(self):
        p = self.orch.propose_action("probe_mutation", {"n": 1}, confidence=0.9)
        self.assertEqual((p["status"], p["effect"], p["effect_declared"]), ("approved", LOCAL_STATE, True))

    def test_phase_0_blocks_it_as_a_mutation(self):
        p = self.orch.propose_action("probe_mutation", {"n": 1}, confidence=0.9)
        r = self.orch.execute_proposal(p["proposal_id"])
        self.assertFalse(r["ok"])
        self.assertIn("observe-only", r["error"])
        self.assertEqual(self.calls, [])

    def test_approved_proposal_executes_once(self):
        self.orch.set_phase(2, dry_run=False)
        p = self.orch.propose_action("probe_mutation", {"n": 7}, confidence=0.9)
        r = self.orch.execute_proposal(p["proposal_id"])
        self.assertTrue(r["ok"], r)
        self.assertNotIn("Unknown tool", str(r))
        self.assertEqual(self.calls, [{"n": 7}])

    def test_execution_is_written_to_the_control_audit_with_the_proposal(self):
        self.orch.set_phase(2, dry_run=False)
        p = self.orch.propose_action("probe_mutation", {"n": 7}, confidence=0.9)
        self.orch.execute_proposal(p["proposal_id"])
        rows = self.audit_rows()
        self.assertEqual([(a, e, res) for a, e, res, _ in rows],
                         [("probe_mutation", LOCAL_STATE, "started"), ("probe_mutation", LOCAL_STATE, "success")])
        self.assertEqual({pid for *_, pid in rows}, {p["proposal_id"]})

    @unittest.skipUnless(importlib.util.find_spec("jsonschema"), "Registry skips parameter validation without jsonschema")
    def test_parameters_are_validated_against_the_input_schema(self):
        self.orch.set_phase(2, dry_run=False)
        p = self.orch.propose_action("probe_mutation", {"n": "not an int"}, confidence=0.9)
        r = self.orch.execute_proposal(p["proposal_id"])
        self.assertFalse(r["ok"])
        self.assertIn("Invalid parameters", r["error"])
        self.assertEqual(self.calls, [])

    def test_a_direct_call_is_still_refused(self):
        with self.assertRaises(RuntimeError):
            self.handler._handle_tools_call({"name": "probe_mutation", "arguments": {"n": 1}})
        self.assertEqual(self.calls, [])

    def test_a_tool_the_registry_already_has_is_not_replaced(self):
        before = self.orch.registry._tools["decay_now"]
        self.orch.propose_action("decay_now", {"lambda": 0.001}, confidence=0.9)
        self.assertIs(self.orch.registry._tools["decay_now"], before)

    def test_adopting_does_not_make_an_inferred_effect_look_declared(self):
        self.handler._tools["probe_inferred"] = ToolDef(
            "probe_inferred", "d", {"type": "object"}, lambda params: {}, mutates_state=True)
        self.orch.propose_action("probe_inferred", {}, confidence=0.9)
        self.assertFalse(self.orch.registry._tools["probe_inferred"].effect_declared)


class GraphOpsToolDefAdoptionTest(unittest.TestCase):
    """The real graphops_* mutating tools are adopted without losing their metadata."""

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
        cls.orch = cls.handler._orchestrator
        cls.mutating = sorted(
            n for n, t in cls.handler._tools.items()
            if n.startswith("graphops_") and n not in cls.orch.registry._tools and t.mutates_state
        )

    def test_the_mutating_graphops_tools_were_found(self):
        self.assertIn("graphops_submit_feedback", self.mutating)
        self.assertIn("graphops_explain_coverage_cell", self.mutating)

    def test_proposing_each_one_makes_it_a_registry_tool_that_meets_the_contract(self):
        for name in self.mutating:
            p = self.orch.propose_action(name, {}, confidence=0.9)
            self.assertEqual(p["status"], "approved", name)
        adopted = {n: self.orch.registry._tools[n] for n in self.mutating}
        self.assertEqual(validate_tool_contract(adopted), [])
        self.assertTrue(all(t.mutates_state for t in adopted.values()))


if __name__ == "__main__":
    unittest.main()
