import logging
import os
import tempfile
import unittest
from unittest import mock

import control_audit
from hypergraph_engine import HypergraphEngine
from mcp_effects import (
    APPROVAL_MODES_ENV, APPROVERS_ENV, AUTOMATED, DIRECT, HARDWARE_CONTROL, LOCAL_STATE, READ_ONLY, SECOND_PRINCIPAL,
    deployment_approval_modes, parse_approval_modes,
)
from mcp_registry import Tool
from mcp_safety import DualAgentOrchestrator
from mcp_server import MCPHandler, ToolDef


def _tool(name, effect, ran=None):
    mutating = effect != READ_ONLY
    return Tool(name, "d", {"type": "object"}, {"type": "object"},
                lambda engine, params: (ran.append(name) if ran is not None else None) or {}, mutates_state=mutating, effect=effect)


class ParseApprovalModesTest(unittest.TestCase):
    """ADR 0006: MCP_APPROVAL_MODES can raise an effect's mode, never lower it."""

    def test_no_setting_is_no_change(self):
        for text in (None, "", "  ", " , "):
            self.assertEqual(parse_approval_modes(text), {})

    def test_a_valid_setting_is_parsed(self):
        self.assertEqual(parse_approval_modes("HARDWARE_CONTROL=SECOND_PRINCIPAL"), {HARDWARE_CONTROL: SECOND_PRINCIPAL})
        self.assertEqual(parse_approval_modes(" HARDWARE_CONTROL = SECOND_PRINCIPAL , READ_ONLY=AUTOMATED "),
                         {HARDWARE_CONTROL: SECOND_PRINCIPAL, READ_ONLY: AUTOMATED})

    def test_a_malformed_or_unknown_setting_is_refused(self):
        for bad in ("HARDWARE_CONTROL", "HARDWARE_CONTROL=", "=SECOND_PRINCIPAL", "NOT_AN_EFFECT=AUTOMATED",
                    "LOCAL_STATE=ALWAYS", "LOCAL_STATE=AUTOMATED,LOCAL_STATE=SECOND_PRINCIPAL"):
            with self.assertRaises(ValueError, msg=bad):
                parse_approval_modes(bad)

    def test_a_setting_below_the_floor_is_refused(self):
        with self.assertRaises(ValueError):
            parse_approval_modes("LOCAL_STATE=DIRECT")

    def test_the_setting_is_read_from_the_environment(self):
        self.assertEqual(deployment_approval_modes({APPROVAL_MODES_ENV: "LOCAL_STATE=SECOND_PRINCIPAL"}), {LOCAL_STATE: SECOND_PRINCIPAL})
        self.assertEqual(deployment_approval_modes({}), {})
        with mock.patch.dict(os.environ, {APPROVAL_MODES_ENV: "HARDWARE_CONTROL=SECOND_PRINCIPAL"}):
            self.assertEqual(deployment_approval_modes(), {HARDWARE_CONTROL: SECOND_PRINCIPAL})


class ProposalModeTest(unittest.TestCase):
    def orch(self, approval_modes=None, *tools):
        by_name = {t.name: t for t in tools}
        return DualAgentOrchestrator(tool_resolver=by_name.get, approval_modes=approval_modes if approval_modes is not None else {})

    def test_by_default_each_proposal_records_its_mode_and_nothing_else_changes(self):
        o = self.orch(None, _tool("r", READ_ONLY), _tool("w", LOCAL_STATE), _tool("h", HARDWARE_CONTROL))
        got = {n: o.propose_action(n, {}, confidence=0.9) for n in ("r", "w", "h")}
        self.assertEqual({n: (p["status"], p["approval_mode"]) for n, p in got.items()},
                         {"r": ("approved", DIRECT), "w": ("approved", AUTOMATED),
                          "h": ("pending_approval", SECOND_PRINCIPAL)})

    def test_undeclared_and_unknown_tools_are_automated(self):
        o = self.orch(None, _tool("legacy", None))
        self.assertEqual(o.propose_action("legacy", {}, confidence=0.9)["approval_mode"], AUTOMATED)
        self.assertEqual(o.propose_action("missing", {}, confidence=0.9)["approval_mode"], AUTOMATED)

    def test_a_raised_effect_stops_at_pending_approval(self):
        o = self.orch({HARDWARE_CONTROL: SECOND_PRINCIPAL}, _tool("h", HARDWARE_CONTROL), _tool("w", LOCAL_STATE))
        p = o.propose_action("h", {}, confidence=0.9)
        self.assertEqual((p["status"], p["approval_mode"]), ("pending_approval", SECOND_PRINCIPAL))
        self.assertIn("awaiting independent approval", p["approval_reason"])
        # another effect is unaffected
        self.assertEqual(o.propose_action("w", {}, confidence=0.9)["status"], "approved")

    def test_the_automated_checks_still_filter_first(self):
        o = self.orch({LOCAL_STATE: SECOND_PRINCIPAL}, _tool("w", LOCAL_STATE))
        p = o.propose_action("w", {}, confidence=0.1)
        self.assertEqual((p["status"], p["approval_mode"]), ("rejected", SECOND_PRINCIPAL))

    def test_hardware_confidence_is_recorded_but_never_decides(self):
        o = self.orch(None, _tool("h", HARDWARE_CONTROL))
        for confidence in (0.0, 0.1, 0.99):
            p = o.propose_action("h", {}, confidence=confidence)
            self.assertEqual(p["status"], "pending_approval", confidence)
            self.assertEqual(p["confidence"], confidence)

    def test_the_stability_gates_still_apply_to_hardware(self):
        o = self.orch(None, _tool("h", HARDWARE_CONTROL))
        with mock.patch.object(o.stability, "is_oscillating", return_value=True):
            p = o.propose_action("h", {}, confidence=0.99)
        self.assertEqual(p["status"], "rejected")
        self.assertIn("oscillation", p["approval_reason"])

    def test_an_explicit_automated_setting_is_honoured_and_ignores_confidence(self):
        o = self.orch({HARDWARE_CONTROL: AUTOMATED}, _tool("h", HARDWARE_CONTROL))
        for confidence in (0.0, 0.99):
            p = o.propose_action("h", {}, confidence=confidence)
            self.assertEqual((p["status"], p["approval_mode"]), ("approved", AUTOMATED), confidence)

    def test_a_halting_hardware_tool_is_not_made_to_wait_by_the_default(self):
        stop = _tool("stop", HARDWARE_CONTROL)
        stop.halts = True
        o = self.orch(None, stop)
        self.assertEqual(o.propose_action("stop", {}, confidence=0.9)["approval_mode"], AUTOMATED)

    def test_a_pending_proposal_cannot_be_executed_by_the_safety_layer(self):
        o = self.orch({HARDWARE_CONTROL: SECOND_PRINCIPAL}, _tool("h", HARDWARE_CONTROL))
        p = o.propose_action("h", {}, confidence=0.9)
        calls = []
        r = o.execute_proposal(p["proposal_id"], lambda **kw: calls.append(kw))
        self.assertFalse(r["ok"])
        self.assertIn("pending_approval", r["error"])
        self.assertEqual(calls, [])

    def test_raising_a_read_only_effect_to_automated_applies_the_mutation_checks(self):
        o = self.orch({READ_ONLY: AUTOMATED}, _tool("r", READ_ONLY))
        self.assertEqual(o.propose_action("r", {}, confidence=0.1)["status"], "rejected")
        self.assertEqual(o.propose_action("r", {}, confidence=0.9)["status"], "approved")

    def test_a_deployment_setting_below_the_floor_is_refused_when_the_orchestrator_is_built(self):
        with self.assertRaises(ValueError):
            DualAgentOrchestrator(approval_modes={LOCAL_STATE: DIRECT}).approval_mode_for(_tool("w", LOCAL_STATE))

    def test_the_environment_setting_reaches_the_orchestrator(self):
        with mock.patch.dict(os.environ, {APPROVAL_MODES_ENV: "HARDWARE_CONTROL=SECOND_PRINCIPAL"}):
            o = DualAgentOrchestrator(tool_resolver={"h": _tool("h", HARDWARE_CONTROL)}.get)
        self.assertEqual(o.propose_action("h", {}, confidence=0.9)["status"], "pending_approval")

    def test_a_raised_mode_logs_that_an_authorized_operator_must_approve(self):
        with self.assertLogs("mcp_safety", level="WARNING") as cap:
            DualAgentOrchestrator(approval_modes={HARDWARE_CONTROL: SECOND_PRINCIPAL}, approvers={"alice"})
        self.assertIn("authorized operator approves", cap.output[0])
        self.assertIn("1 operator(s) are authorized", cap.output[0])

    def test_the_log_says_so_when_nobody_is_authorized(self):
        with self.assertLogs("mcp_safety", level="WARNING") as cap:
            DualAgentOrchestrator(approvers=set())      # the HARDWARE_CONTROL default needs a second principal
        self.assertIn("NO operator is authorized", cap.output[0])
        self.assertIn("HARDWARE_CONTROL", cap.output[0])


class FullOrchestratorTest(unittest.TestCase):
    """Through MCPHandler, the orchestrator and Registry.execute, in the phase where mutations run."""

    @classmethod
    def setUpClass(cls):
        logging.disable(logging.CRITICAL)
        cls.addClassCleanup(logging.disable, logging.NOTSET)

    def setUp(self):
        self.ran = []
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        control_audit.configure(os.path.join(self.dir.name, "a.sqlite3"), 90)
        self.addCleanup(control_audit.reset)

    def handler(self, setting):
        with mock.patch.dict(os.environ, ({APPROVAL_MODES_ENV: setting} if setting else {}) | {APPROVERS_ENV: "alice,bob"}, clear=False):
            if not setting:
                os.environ.pop(APPROVAL_MODES_ENV, None)
            h = MCPHandler(HypergraphEngine(), use_orchestrator=True)
        h._tools["probe_write"] = ToolDef("probe_write", "d", {"type": "object"},
                                          lambda params: self.ran.append(params) or {"ok": True},
                                          mutates_state=True, effect=LOCAL_STATE)
        h._orchestrator.set_phase(2, dry_run=False)
        return h

    def test_by_default_the_tool_executes_as_before(self):
        h = self.handler(None)
        p = h._orchestrator.propose_action("probe_write", {}, confidence=0.9)
        self.assertEqual(p["status"], "approved")
        self.assertTrue(h._orchestrator.execute_proposal(p["proposal_id"])["ok"])
        self.assertEqual(len(self.ran), 1)

    def test_when_raised_the_tool_never_executes_and_check_proposal_says_so(self):
        h = self.handler("LOCAL_STATE=SECOND_PRINCIPAL")
        p = h._orchestrator.propose_action("probe_write", {}, confidence=0.9)
        self.assertEqual(p["status"], "pending_approval")
        r = h._orchestrator.execute_proposal(p["proposal_id"])
        self.assertFalse(r["ok"])
        self.assertEqual(self.ran, [])
        checked = h._orchestrator.check_proposal(p["proposal_id"])
        self.assertEqual((checked["approved"], checked["status"], checked["approval_mode"]),
                         (False, "pending_approval", SECOND_PRINCIPAL))
        # nothing executed, so only the lifecycle row exists and no execution row does
        self.assertEqual([e["action"] for e in control_audit.get_store().events()], ["proposal.created"])

    def test_a_bad_setting_stops_the_handler_instead_of_disabling_the_orchestrator(self):
        with mock.patch.dict(os.environ, {APPROVAL_MODES_ENV: "LOCAL_STATE=DIRECT"}):
            with self.assertRaises(ValueError):
                MCPHandler(HypergraphEngine(), use_orchestrator=True)
        with mock.patch.dict(os.environ, {APPROVAL_MODES_ENV: "LOCAL_STATE=DIRECT"}):
            MCPHandler(HypergraphEngine(), use_orchestrator=False)  # no orchestrator, no setting read


if __name__ == "__main__":
    unittest.main()
