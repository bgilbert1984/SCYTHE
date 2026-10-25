import logging
import os
import tempfile
import unittest
from unittest import mock

import control_audit
from hypergraph_engine import HypergraphEngine
from mcp_effects import APPROVAL_MODES_ENV, APPROVERS_ENV, AUTOMATED, LOCAL_STATE, SECOND_PRINCIPAL
from mcp_principal import (
    ANONYMOUS, INTERNAL_TOKEN, LOOPBACK, OPERATOR_SESSION, UNSPECIFIED, Principal, principal_scope,
)
from mcp_server import MCPHandler, ToolDef

ALICE = Principal(kind=OPERATOR_SESSION, operator_id="alice", session_id="s1", role="operator")
ALICE_AGAIN = Principal(kind=OPERATOR_SESSION, operator_id="alice", session_id="s2", role="operator")
BOB = Principal(kind=OPERATOR_SESSION, operator_id="bob", session_id="s3", role="operator")
INTERNAL = Principal(kind=INTERNAL_TOKEN)


class ApprovalActionTest(unittest.TestCase):
    """ADR 0006 Decision 6 / #228: approval is its own transition, by another identified operator,
    and execution re-checks it.  Through MCPHandler, the orchestrator and Registry.execute."""

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
        with mock.patch.dict(os.environ, {APPROVAL_MODES_ENV: "LOCAL_STATE=SECOND_PRINCIPAL", APPROVERS_ENV: "alice,bob"}):
            self.h = MCPHandler(HypergraphEngine(), use_orchestrator=True)
        self.h._tools["probe_write"] = ToolDef(
            "probe_write", "d", {"type": "object", "properties": {"n": {"type": "integer"}}},
            lambda params: self.ran.append(params) or {"ok": True}, mutates_state=True, effect=LOCAL_STATE)
        self.o = self.h._orchestrator
        self.o.set_phase(2, dry_run=False)

    def propose(self, as_principal=ALICE, n=1):
        with principal_scope(as_principal):
            return self.o.propose_action("probe_write", {"n": n}, confidence=0.9)

    def decide(self, proposal, as_principal, decision="approve", note=""):
        with principal_scope(as_principal):
            return self.o.approve_proposal(proposal["proposal_id"], decision, note)

    def execute(self, proposal, as_principal=BOB):
        with principal_scope(as_principal):
            return self.o.execute_proposal(proposal["proposal_id"])

    def test_another_operator_approves_and_it_then_executes_once(self):
        p = self.propose()
        self.assertEqual(p["status"], "pending_approval")
        r = self.decide(p, BOB, note="checked the target")
        self.assertEqual((r["ok"], r["status"]), (True, "approved"))
        rec = next(x for x in self.o.dual_agent.decision_log if x["proposal_id"] == p["proposal_id"])
        self.assertEqual((rec["approved_by"]["operator_id"], rec["approval_note"]), ("bob", "checked the target"))
        self.assertEqual(rec["proposed_by"]["operator_id"], "alice")
        self.assertEqual(rec["approved_params_digest"], rec["params_digest"])
        self.assertTrue(self.execute(p)["ok"])
        self.assertEqual(self.ran, [{"n": 1}])

    def test_approval_does_not_bypass_the_phase(self):
        self.o.set_phase(0)
        p = self.propose()
        self.assertTrue(self.decide(p, BOB)["ok"])
        r = self.execute(p)
        self.assertFalse(r["ok"])
        self.assertIn("observe-only", r["error"])
        self.assertEqual(self.ran, [])

    def test_the_proposer_cannot_approve_even_from_another_session(self):
        p = self.propose()
        for who in (ALICE, ALICE_AGAIN):
            r = self.decide(p, who)
            self.assertFalse(r["ok"])
            self.assertIn("differ from the proposer", r["error"])
        self.assertEqual(self.o.check_proposal(p["proposal_id"])["status"], "pending_approval")

    def test_only_an_identified_operator_can_approve(self):
        p = self.propose()
        for who in (None, Principal(kind=LOOPBACK), Principal(kind=ANONYMOUS), INTERNAL, Principal(kind=UNSPECIFIED),
                    Principal(kind=OPERATOR_SESSION)):
            r = self.decide(p, who)
            self.assertFalse(r["ok"], who)
            self.assertIn("identified operator", r["error"])
        self.assertEqual(self.ran, [])

    def test_an_automation_proposer_can_be_approved_by_any_identified_operator(self):
        p = self.propose(as_principal=INTERNAL)
        self.assertTrue(self.decide(p, ALICE)["ok"])
        self.assertTrue(self.execute(p)["ok"])

    def test_deny_ends_it_and_it_cannot_run(self):
        p = self.propose()
        r = self.decide(p, BOB, "deny", note="wrong target")
        self.assertEqual((r["ok"], r["status"]), (True, "denied"))
        self.assertFalse(self.execute(p)["ok"])
        self.assertFalse(self.decide(p, BOB)["ok"])  # cannot approve after deny
        self.assertEqual(self.ran, [])

    def test_a_proposal_is_decided_once_and_only_from_pending(self):
        p = self.propose()
        self.assertTrue(self.decide(p, BOB)["ok"])
        self.assertIn("not pending_approval", self.decide(p, BOB)["error"])
        self.assertTrue(self.execute(p)["ok"])
        self.assertIn("not pending_approval", self.decide(p, BOB)["error"])

    def test_unknown_proposal_and_bad_decision_are_refused(self):
        self.assertEqual(self.o.approve_proposal("nope", "approve")["ok"], False)
        p = self.propose()
        with principal_scope(BOB):
            r = self.o.approve_proposal(p["proposal_id"], "maybe")
        self.assertFalse(r["ok"])
        self.assertIn("decision must be", r["error"])

    def test_a_proposal_in_another_mode_is_not_approvable(self):
        h = MCPHandler(HypergraphEngine(), use_orchestrator=True)   # no raised mode
        h._tools["probe_write"] = self.h._tools["probe_write"]
        p = h._orchestrator.propose_action("probe_write", {"n": 1}, confidence=0.9)
        self.assertEqual((p["status"], p["approval_mode"]), ("approved", AUTOMATED))
        with principal_scope(BOB):
            self.assertIn("not pending_approval", h._orchestrator.approve_proposal(p["proposal_id"], "approve")["error"])

    def test_parameters_changed_after_approval_do_not_run(self):
        p = self.propose()
        self.decide(p, BOB)
        p["params"]["n"] = 999   # tampering with the stored proposal after approval
        r = self.execute(p)
        self.assertFalse(r["ok"])
        self.assertIn("changed after approval", r["error"])
        self.assertEqual(self.ran, [])

    def test_parameters_changed_before_approval_cannot_be_approved(self):
        p = self.propose()
        p["params"]["n"] = 999
        r = self.decide(p, BOB)
        self.assertFalse(r["ok"])
        self.assertIn("changed after the proposal was created", r["error"])

    def test_a_status_set_to_approved_without_an_approval_still_cannot_execute(self):
        p = self.propose()
        p["status"] = "approved"      # the defect the execution-time check exists for
        r = self.execute(p)
        self.assertFalse(r["ok"])
        self.assertIn("no recorded approval", r["error"])
        self.assertEqual(self.ran, [])

    def test_a_recorded_approver_who_is_the_proposer_cannot_execute(self):
        p = self.propose()
        p.update(status="approved", approved_by=ALICE.to_dict(), approved_params_digest=p["params_digest"])
        r = self.execute(p)
        self.assertFalse(r["ok"])
        self.assertIn("approver is the proposer", r["error"])

    def approved(self):
        p = self.propose()
        self.assertTrue(self.decide(p, BOB)["ok"])
        return p

    def test_tampering_with_the_approval_metadata_stops_execution(self):
        """The execution-time check must not trust anything stored on the proposal."""
        cases = {
            "approved_by removed": lambda p: p.pop("approved_by"),
            "approved_by emptied": lambda p: p.update(approved_by={}),
            "approver replaced by an unidentified principal":
                lambda p: p.update(approved_by=Principal(kind=LOOPBACK).to_dict()),
            "approver replaced by the internal token":
                lambda p: p.update(approved_by=INTERNAL.to_dict()),
            "an unidentified approver claiming identified=True":
                lambda p: p.update(approved_by={"kind": LOOPBACK, "operator_id": "bob", "identified": True}),
            "approver operator_id changed to the proposer":
                lambda p: p["approved_by"].update(operator_id="alice"),
            "approved digest removed": lambda p: p.pop("approved_params_digest"),
            "approved digest replaced": lambda p: p.update(approved_params_digest="0" * 64),
            "approval_mode lowered to AUTOMATED": lambda p: (p.pop("approved_by"), p.update(approval_mode=AUTOMATED)),
            "approval_mode removed": lambda p: (p.pop("approved_by"), p.pop("approval_mode")),
        }
        for label, tamper in cases.items():
            with self.subTest(label):
                p = self.approved()
                tamper(p)
                r = self.execute(p)
                self.assertFalse(r["ok"], label)
        self.assertEqual(self.ran, [])

    def test_a_forged_approved_status_cannot_hide_behind_a_lowered_mode(self):
        p = self.propose()
        p.update(status="approved", approval_mode=AUTOMATED)   # no approval was ever given
        self.assertFalse(self.execute(p)["ok"])
        self.assertEqual(self.ran, [])

    def test_a_proposal_approved_in_a_weaker_mode_is_refused_once_the_setting_is_raised(self):
        h = MCPHandler(HypergraphEngine(), use_orchestrator=True)   # no raised mode when proposing
        h._tools["probe_write"] = self.h._tools["probe_write"]
        h._orchestrator.set_phase(2, dry_run=False)
        p = h._orchestrator.propose_action("probe_write", {"n": 1}, confidence=0.9)
        self.assertEqual(p["status"], "approved")
        h._orchestrator.dual_agent.approval_modes = {LOCAL_STATE: SECOND_PRINCIPAL}   # raised afterwards
        r = h._orchestrator.execute_proposal(p["proposal_id"])
        self.assertFalse(r["ok"])
        self.assertEqual(self.ran, [])

    def test_the_rpc_uses_the_callers_principal_as_the_approver(self):
        p = self.propose()
        req = {"jsonrpc": "2.0", "id": 1, "method": "orchestrate/approve",
               "params": {"proposal_id": p["proposal_id"], "decision": "approve", "note": "ok"}}
        with principal_scope(ALICE):
            self.assertFalse(self.h.handle(req)["result"]["ok"])
        with principal_scope(BOB):
            res = self.h.handle(req)["result"]
        self.assertEqual((res["ok"], res["status"]), (True, "approved"))
        self.assertTrue(self.execute(p)["ok"])

    def test_decide_still_only_reports_status(self):
        p = self.propose()
        req = {"jsonrpc": "2.0", "id": 1, "method": "orchestrate/decide", "params": {"proposal_id": p["proposal_id"]}}
        with principal_scope(BOB):
            res = self.h.handle(req)["result"]
        self.assertEqual((res["approved"], res["status"]), (False, "pending_approval"))


if __name__ == "__main__":
    unittest.main()
