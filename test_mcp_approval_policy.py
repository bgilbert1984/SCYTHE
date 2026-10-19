import unittest

from hypergraph_engine import HypergraphEngine
from mcp_effects import (
    AUTO_APPROVE, REQUIRES_REVIEW, AUDIT_ONLY, HARDWARE_CONTROL, LOCAL_STATE, READ_ONLY,
    approval_policy,
)
from mcp_registry import Tool
from mcp_safety import DualAgentOrchestrator
from mcp_server import MCPHandler, ToolDef


def _tool(name, mutates_state, effect=None):
    return Tool(name, "d", {}, {}, lambda **k: None, mutates_state=mutates_state, effect=effect)


class ApprovalPolicyTest(unittest.TestCase):
    def test_policy_depends_on_declared_effect(self):
        self.assertEqual(approval_policy(_tool("t", False, READ_ONLY)), AUTO_APPROVE)
        self.assertEqual(approval_policy(_tool("t", False, AUDIT_ONLY)), AUTO_APPROVE)
        self.assertEqual(approval_policy(_tool("t", True, LOCAL_STATE)), REQUIRES_REVIEW)
        self.assertEqual(approval_policy(_tool("t", True, HARDWARE_CONTROL)), REQUIRES_REVIEW)

    def test_unknown_is_not_safe(self):
        self.assertEqual(approval_policy(None), REQUIRES_REVIEW)
        # looks read-only (mutates_state False) but never declared an effect
        self.assertEqual(approval_policy(_tool("t", False, None)), REQUIRES_REVIEW)


class ProposeActionTest(unittest.TestCase):
    def _orch(self, *tools):
        by_name = {t.name: t for t in tools}
        return DualAgentOrchestrator(tool_resolver=by_name.get)

    def test_mutating_tool_named_query_is_not_auto_approved(self):
        """The old rule approved any tool whose name contained "query"."""
        orch = self._orch(_tool("foo_query_bar", True, LOCAL_STATE))
        p = orch.propose_action("foo_query_bar", {}, confidence=0.1)
        self.assertEqual(p["status"], "rejected")          # went through the mutation checks
        self.assertIn("effect LOCAL_STATE", p["approval_reason"])

    def test_read_only_tool_is_auto_approved_whatever_its_name(self):
        orch = self._orch(_tool("harmless_lookup", False, READ_ONLY),
                          _tool("harmless_query_lookup", False, READ_ONLY))
        for name in ("harmless_lookup", "harmless_query_lookup"):
            p = orch.propose_action(name, {}, confidence=0.1)
            self.assertEqual(p["status"], "approved", name)
            self.assertEqual((p["effect"], p["effect_declared"]), (READ_ONLY, True))

    def test_undeclared_and_unknown_tools_get_the_mutation_checks(self):
        orch = self._orch(_tool("legacy_query", False, None))
        p = orch.propose_action("legacy_query", {}, confidence=0.1)
        self.assertEqual(p["status"], "rejected")
        self.assertIn("effect not declared", p["approval_reason"])
        p = orch.propose_action("no_such_tool", {}, confidence=0.1)
        self.assertEqual(p["status"], "rejected")
        self.assertIn("tool not found", p["approval_reason"])

    def test_no_resolver_means_no_auto_approval(self):
        p = DualAgentOrchestrator().propose_action("query_hot_entities", {}, confidence=0.1)
        self.assertEqual(p["status"], "rejected")

    def test_high_confidence_mutation_still_follows_existing_checks(self):
        orch = self._orch(_tool("rf_tune_like", True, HARDWARE_CONTROL))
        p = orch.propose_action("rf_tune_like", {}, confidence=0.95)
        self.assertEqual(p["status"], "approved")
        self.assertIn("effect HARDWARE_CONTROL", p["approval_reason"])


class ServerWiringTest(unittest.TestCase):
    """With the real handler, tools the name rule used to approve are still approved."""

    @classmethod
    def setUpClass(cls):
        cls.handler = MCPHandler(HypergraphEngine(), use_orchestrator=True)
        from rf_mcp import register_rf_tools
        register_rf_tools(HypergraphEngine(), cls.handler)

    def propose(self, name, confidence=0.1):
        return self.handler._orchestrator.propose_action(name, {}, confidence, "test")

    def test_previously_approved_read_tools_still_approved(self):
        for name in ("query_hot_entities", "query_recent_edges", "query_scope_stats",
                     "export_graph_snapshot", "get_engine_metrics",
                     "rf_observations_query", "rf_sparse_supports_query"):
            self.assertEqual(self.propose(name)["status"], "approved", name)

    def test_mutating_tools_need_the_mutation_checks(self):
        for name in ("decay_now", "rf_tune"):
            p = self.propose(name, confidence=0.1)
            self.assertEqual(p["status"], "rejected", name)
            self.assertNotIn("not found", p["approval_reason"], name)   # resolved, then rejected on merit

    def test_resolver_returns_metadata_not_wrappers(self):
        tool = self.handler._resolve_tool("decay_now")
        self.assertTrue(hasattr(tool, "effect_declared"))


if __name__ == "__main__":
    unittest.main()
