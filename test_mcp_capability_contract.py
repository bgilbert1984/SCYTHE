"""#217: a registry tool reports a missing engine capability as unsupported, never as an empty or
successful result, and a misspelled parameter is rejected rather than defaulted."""
import importlib.util
import logging
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import control_audit
import mcp_registry
from hypergraph_engine import HypergraphEngine
from mcp_server import CAPABILITY_UNSUPPORTED_CODE, MCPHandler

# tool -> minimal valid params, for every handler that needs an engine method HypergraphEngine lacks
UNSUPPORTED = {
    "get_entity_neighbors": {"entity_id": "n1"},
    "subscribe_scope": {},
    "unsubscribe_scope": {"scope_id": "s1"},
    "scrub_scope_time": {"scope_id": "s1", "timestamp": 1},
    "set_scope_filter": {"scope_id": "s1", "min_weight": 0.5},
    "list_active_scopes": {},
    "set_decay_lambda": {"lambda": 0.5},
    "reload_rules": {},
    "clear_scope_cache": {},
    "reinforce_edge": {"src": "a", "dst": "b"},
    "prune_below_weight": {"threshold": 0.2},
}


def _registry(engine):
    return mcp_registry.build_registry(engine)["__registry__"]


NEEDS_JSONSCHEMA = unittest.skipUnless(importlib.util.find_spec("jsonschema"),
                                        "Registry skips parameter validation without jsonschema")


class UnsupportedCapabilityTest(unittest.TestCase):
    def test_bare_engine_lacks_every_method_these_tests_rely_on(self):
        engine = HypergraphEngine()
        for method in ("get_neighbors", "create_scope", "destroy_scope", "list_scopes", "set_decay_lambda",
                       "reload_rules", "clear_scope_cache", "reinforce_edge", "prune_below_weight",
                       "scrub_scope_before_time", "set_scope_filter"):
            self.assertFalse(hasattr(engine, method), method)

    def test_each_tool_raises_unsupported_instead_of_a_default(self):
        engine = HypergraphEngine()
        reg = _registry(engine)
        for name, params in UNSUPPORTED.items():
            with self.subTest(tool=name):
                with self.assertRaises(mcp_registry.CapabilityUnsupported) as cm:
                    reg.execute(engine, name, params, agent_mode="admin")
                self.assertEqual(cm.exception.tool, name)
                self.assertTrue(cm.exception.capability)

    def test_set_decay_lambda_does_not_claim_the_new_value(self):
        engine = HypergraphEngine()
        with self.assertRaises(mcp_registry.CapabilityUnsupported):
            _registry(engine).execute(engine, "set_decay_lambda", {"lambda": 0.5}, agent_mode="admin")
        self.assertEqual(engine.decay_lambda, 0.0)

    def test_a_present_capability_still_executes(self):
        class Engine:
            created = None

            def create_scope(self, scope_id, kind, min_weight):
                Engine.created = (scope_id, kind, min_weight)

            def list_scopes(self):
                return [Engine.created[0]] if Engine.created else []

            def get_neighbors(self, entity_id, limit):
                return []     # supported, no matching data: this one is a legitimate empty

        engine = Engine()
        reg = _registry(engine)
        scope = reg.execute(engine, "subscribe_scope", {}, agent_mode="admin")["scope_id"]
        self.assertEqual(reg.execute(engine, "list_active_scopes", {})["scopes"], [scope])
        self.assertEqual(reg.execute(engine, "get_entity_neighbors", {"entity_id": "x"})["neighbors"], [])

    def test_unsupported_is_a_runtime_error_for_existing_callers(self):
        self.assertTrue(issubclass(mcp_registry.CapabilityUnsupported, RuntimeError))


class UnknownKeyTest(unittest.TestCase):
    def test_every_registry_tool_schema_is_closed(self):
        reg = _registry(HypergraphEngine())
        for tool in reg.list_tools():
            with self.subTest(tool=tool.name):
                self.assertIs(tool.parameters.get("additionalProperties"), False)

    @NEEDS_JSONSCHEMA
    def test_misspelled_key_is_rejected_not_defaulted(self):
        engine = HypergraphEngine()
        reg = _registry(engine)
        with self.assertRaises(ValueError):
            reg.execute(engine, "set_decay_lambda", {"decay_lambda": 0.5}, agent_mode="admin")
        self.assertIn("decay_lambda", reg.check_params("set_decay_lambda", {"decay_lambda": 0.5}))
        self.assertIsNone(reg.check_params("set_decay_lambda", {"lambda": 0.5}))


class EntryPointTest(unittest.TestCase):
    """The same outcome on every path that can reach a registry tool, none of them bypassing policy."""

    @classmethod
    def setUpClass(cls):
        logging.disable(logging.CRITICAL)

    @classmethod
    def tearDownClass(cls):
        logging.disable(logging.NOTSET)

    def setUp(self):
        self.handler = MCPHandler(HypergraphEngine(), use_orchestrator=True)
        self.orch = self.handler._orchestrator
        self.audit_dir = tempfile.TemporaryDirectory()
        self.audit_path = os.path.join(self.audit_dir.name, "audit.sqlite3")
        control_audit.configure(self.audit_path, retention_days=90)

    def tearDown(self):
        control_audit.reset()
        self.audit_dir.cleanup()

    def _call(self, name, arguments):
        return self.handler.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                                    "params": {"name": name, "arguments": arguments}})

    def test_tools_call_carries_a_machine_readable_unsupported_error(self):
        error = self._call("list_active_scopes", {})["error"]
        self.assertEqual(error["code"], CAPABILITY_UNSUPPORTED_CODE)
        self.assertEqual(error["data"], {"kind": "unsupported_capability", "tool": "list_active_scopes",
                                         "capability": "scope_management"})

    def test_an_ordinary_failure_is_still_an_internal_error_without_that_data(self):
        error = self._call("get_entity_neighbors", {})["error"]    # entity_id missing: ValueError, not unsupported
        self.assertEqual(error["code"], -32603)
        self.assertNotIn("data", error)

    @NEEDS_JSONSCHEMA
    def test_a_schema_violation_is_not_reported_as_unsupported(self):
        error = self._call("list_active_scopes", {"scope": "x"})["error"]
        self.assertEqual(error["code"], -32603)
        self.assertNotIn("data", error)

    def test_held_mutation_is_approved_then_fails_as_unsupported_not_as_success(self):
        self.orch.set_phase(2, dry_run=False)
        proposal = self.orch.propose_action("reload_rules", {}, confidence=0.9)
        result = self.orch.execute_proposal(proposal["proposal_id"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "CapabilityUnsupported")
        self.assertEqual(result["capability"], "rule_reload")
        with sqlite3.connect(self.audit_path) as conn:
            rows = conn.execute("SELECT result FROM control_audit_events WHERE action = 'reload_rules'"
                                " ORDER BY ts_epoch, rowid").fetchall()
        self.assertEqual(rows[0], ("started",))
        self.assertNotIn(("success",), rows)
        self.assertEqual(len(rows), 2)

    def test_a_failed_execution_carries_no_capability(self):
        self.orch.set_phase(2, dry_run=False)
        proposal = self.orch.propose_action("decay_now", {"lambda": 0.001}, confidence=0.9)
        with mock.patch.object(self.orch.engine, "decay_edges_pruned", side_effect=OSError("disk"), create=True):
            result = self.orch.execute_proposal(proposal["proposal_id"])
        self.assertFalse(result["ok"])
        self.assertNotIn("capability", result)

    def test_direct_mutating_call_over_tools_call_is_still_refused(self):
        error = self._call("reload_rules", {})["error"]
        self.assertEqual(error["code"], -32603)
        self.assertIn("disabled", error["message"])


if __name__ == "__main__":
    unittest.main()
