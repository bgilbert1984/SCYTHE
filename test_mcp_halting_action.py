import logging
import os
import unittest
from unittest import mock

from hypergraph_engine import HypergraphEngine
from mcp_effects import (
    APPROVAL_MODES_ENV, AUTOMATED, EXTERNAL_STATE, HARDWARE_CONTROL, LOCAL_STATE, SECOND_PRINCIPAL, TRAINING_DATA,
    required_approval_mode, validate_tool_contract,
)
from mcp_principal import OPERATOR_SESSION, Principal, principal_scope
from mcp_registry import Tool
from mcp_server import MCPHandler
from rf_bridge import reset_rf_bridge_for_tests
from rf_mcp import register_rf_tools

ALICE = Principal(kind=OPERATOR_SESSION, operator_id="alice", session_id="s1")
NONE = {"type": "object", "properties": {}, "additionalProperties": False}
OBJ = {"type": "object"}


def _tool(name="t", *, effect=HARDWARE_CONTROL, mutates=True, halts=True, params=NONE):
    return Tool(name, "d", params, OBJ, lambda **kw: {}, mutates_state=mutates, effect=effect, halts=halts)


class RuleTest(unittest.TestCase):
    """ADR 0006 Decision 7: a halting tool is capped at AUTOMATED; the cap comes from the tool, not the deployment."""

    def test_a_halting_tool_is_capped_at_automated_under_a_raised_setting(self):
        raised = {HARDWARE_CONTROL: SECOND_PRINCIPAL}
        self.assertEqual(required_approval_mode(HARDWARE_CONTROL, raised, halts=True), AUTOMATED)
        self.assertEqual(required_approval_mode(HARDWARE_CONTROL, raised), SECOND_PRINCIPAL)

    def test_the_cap_does_not_lower_the_floor_or_hide_a_bad_setting(self):
        self.assertEqual(required_approval_mode(HARDWARE_CONTROL, None, halts=True), AUTOMATED)
        with self.assertRaises(ValueError):
            required_approval_mode(HARDWARE_CONTROL, {HARDWARE_CONTROL: "BOGUS"}, halts=True)

    def test_there_is_no_deployment_way_to_grant_it(self):
        from mcp_effects import parse_approval_modes
        for text in ("HARDWARE_CONTROL=HALTS", "halts=1", "rf_capture_stop=AUTOMATED"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_approval_modes(text)


class ContractTest(unittest.TestCase):
    def problems(self, **kw):
        t = _tool(**kw)
        return validate_tool_contract({t.name: t})

    def test_a_well_formed_halting_tool_passes(self):
        self.assertEqual(self.problems(), [])

    def test_halts_on_a_non_mutating_tool_is_refused(self):
        self.assertTrue(any("does not mutate" in p for p in self.problems(effect="READ_ONLY", mutates=False)))

    def test_halts_on_training_data_is_refused(self):
        self.assertTrue(any("never be a halting" in p for p in self.problems(effect=TRAINING_DATA)))

    def test_a_halting_tool_with_parameters_is_refused(self):
        for params in ({"type": "object", "properties": {"action": {"type": "string"}}, "additionalProperties": False},
                       {"type": "object"}, {"type": "object", "properties": {}}):
            with self.subTest(params=params):
                self.assertTrue(any("no parameters" in p for p in self.problems(params=params)))

    def test_the_default_is_not_halting(self):
        self.assertFalse(Tool("x", "d", NONE, OBJ, lambda **k: {}, mutates_state=True, effect=LOCAL_STATE).halts)

    def test_the_set_of_halting_tools_is_pinned(self):
        # Adding a halting tool is a policy decision: change this set deliberately, with the ADR.
        with mock.patch.dict(os.environ, {APPROVAL_MODES_ENV: ""}):
            h = MCPHandler(HypergraphEngine(), use_orchestrator=True)
        register_rf_tools(h.engine, h)
        reg = h._orchestrator.registry._tools
        self.assertEqual(sorted(n for n, t in reg.items() if getattr(t, "halts", False)), ["rf_capture_stop"])
        self.assertEqual(validate_tool_contract(reg), [])


class LifecycleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        logging.disable(logging.CRITICAL)
        cls.addClassCleanup(logging.disable, logging.NOTSET)

    def setUp(self):
        reset_rf_bridge_for_tests()
        self.addCleanup(reset_rf_bridge_for_tests)
        env = {APPROVAL_MODES_ENV: "HARDWARE_CONTROL=SECOND_PRINCIPAL"}
        with mock.patch.dict(os.environ, env):
            self.h = MCPHandler(HypergraphEngine(), use_orchestrator=True)
        register_rf_tools(self.h.engine, self.h)
        self.o = self.h._orchestrator
        self.o.set_phase(2, dry_run=False)

    def propose(self, tool, params=None):
        with principal_scope(ALICE):
            return self.o.propose_action(tool, params or {}, confidence=0.9)

    def test_stop_is_approved_automatically_where_start_and_tune_wait_for_a_second_operator(self):
        stop = self.propose("rf_capture_stop")
        self.assertEqual((stop["status"], stop["approval_mode"]), ("approved", AUTOMATED))
        for tool, params in (("rf_capture_start", {}), ("rf_tune", {"frequency_hz": 1e8}),
                             ("rf_stream_configure", {"fft_size": 1024})):
            with self.subTest(tool=tool):
                p = self.propose(tool, params)
                self.assertEqual((p["status"], p["approval_mode"]), ("pending_approval", SECOND_PRINCIPAL))

    def test_stop_is_still_a_proposal_through_the_lifecycle_and_is_audited(self):
        import sqlite3, tempfile, control_audit
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "a.sqlite3")
            control_audit.configure(path, 90)
            self.addCleanup(control_audit.reset)
            direct = self.h.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                    "params": {"name": "rf_capture_stop", "arguments": {}}})
            self.assertIn("orchestrate/propose", direct["error"]["message"])   # not callable directly
            p = self.propose("rf_capture_stop")
            with principal_scope(ALICE):
                self.assertTrue(self.o.execute_proposal(p["proposal_id"])["ok"])
            with sqlite3.connect(path) as c:
                rows = c.execute("SELECT action, result, proposal_id FROM control_audit_events"
                                 " WHERE action='rf_capture_stop' ORDER BY rowid").fetchall()
            self.assertEqual([(a, r) for a, r, _ in rows], [("rf_capture_stop", "started"), ("rf_capture_stop", "success")])
            self.assertEqual({pid for *_, pid in rows}, {p["proposal_id"]})

    def test_an_unauthorized_or_phase_blocked_stop_is_still_blocked(self):
        self.o.set_phase(0)
        p = self.propose("rf_capture_stop")
        self.assertFalse(self.o.execute_proposal(p["proposal_id"])["ok"])   # autonomous stop obeys Phase 0

    def test_a_tampered_stored_mode_cannot_lower_start(self):
        p = self.propose("rf_capture_start")
        p.update(status="approved", approval_mode=AUTOMATED)
        with principal_scope(ALICE):
            self.assertFalse(self.o.execute_proposal(p["proposal_id"])["ok"])

    def test_the_retired_combined_tool_is_gone(self):
        self.assertNotIn("rf_capture_control", self.o.registry._tools)
        self.assertNotIn("rf_capture_control", self.h._tools)


if __name__ == "__main__":
    unittest.main()
