"""Approval authority is granted, not assumed (#228).

An authenticated operator is not thereby an approver.  MCP_APPROVERS lists the operator ids
entitled to approve or deny; the proposer's identity comes from the authenticated principal,
never from the caller-supplied agent_id; and the grant is checked again at execution, so
revoking it stops a proposal that was approved but has not yet run.
"""
import logging
import os
import tempfile
import unittest
from unittest import mock

import control_audit
from hypergraph_engine import HypergraphEngine
from mcp_effects import (
    APPROVAL_MODES_ENV, APPROVERS_ENV, LOCAL_STATE, deployment_approvers, parse_approvers,
)
from mcp_principal import OPERATOR_SESSION, Principal, principal_scope
from mcp_server import MCPHandler, ToolDef

ALICE = Principal(kind=OPERATOR_SESSION, operator_id="alice", session_id="s1")
BOB = Principal(kind=OPERATOR_SESSION, operator_id="bob", session_id="s2")
CAROL = Principal(kind=OPERATOR_SESSION, operator_id="carol", session_id="s3", role="admin")


class ParseApproversTest(unittest.TestCase):
    def test_no_setting_grants_nobody(self):
        for text in (None, "", "  ", " , "):
            self.assertEqual(parse_approvers(text), frozenset())

    def test_ids_are_trimmed_and_deduplicated(self):
        self.assertEqual(parse_approvers(" alice , bob,alice"), frozenset({"alice", "bob"}))

    def test_an_id_with_inner_whitespace_is_refused(self):
        with self.assertRaises(ValueError):
            parse_approvers("alice,bob smith")

    def test_read_from_the_environment(self):
        self.assertEqual(deployment_approvers({APPROVERS_ENV: "alice"}), frozenset({"alice"}))
        self.assertEqual(deployment_approvers({}), frozenset())
        with mock.patch.dict(os.environ, {APPROVERS_ENV: "bob"}):
            self.assertEqual(deployment_approvers(), frozenset({"bob"}))

    def test_a_bad_setting_stops_the_handler(self):
        with mock.patch.dict(os.environ, {APPROVERS_ENV: "a b"}):
            with self.assertRaises(ValueError):
                MCPHandler(HypergraphEngine(), use_orchestrator=True)


class AuthorityTest(unittest.TestCase):
    """Through MCPHandler and the orchestrator, one raised effect, a probe tool that records runs."""

    APPROVERS = "bob"

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
        env = {APPROVAL_MODES_ENV: "LOCAL_STATE=SECOND_PRINCIPAL", APPROVERS_ENV: self.APPROVERS}
        with mock.patch.dict(os.environ, env):
            self.h = MCPHandler(HypergraphEngine(), use_orchestrator=True)
        self.h._tools["probe_write"] = ToolDef(
            "probe_write", "d", {"type": "object", "properties": {"n": {"type": "integer"}}},
            lambda params: self.ran.append(params) or {"ok": True}, mutates_state=True, effect=LOCAL_STATE)
        self.o = self.h._orchestrator
        self.o.set_phase(2, dry_run=False)

    def propose(self, by=ALICE, **kw):
        with principal_scope(by):
            return self.o.propose_action("probe_write", {"n": 1}, confidence=0.9, **kw)

    def decide(self, p, by, decision="approve"):
        with principal_scope(by):
            return self.o.approve_proposal(p["proposal_id"], decision)

    def execute(self, p, by=ALICE):
        with principal_scope(by):
            return self.o.execute_proposal(p["proposal_id"])

    def test_an_authorized_operator_other_than_the_proposer_can_approve(self):
        p = self.propose()
        self.assertEqual(p["status"], "pending_approval")
        self.assertTrue(self.decide(p, BOB)["ok"])
        self.assertTrue(self.execute(p)["ok"])
        self.assertEqual(self.ran, [{"n": 1}])

    def test_an_authenticated_operator_without_a_grant_cannot_approve_or_deny(self):
        p = self.propose()
        for decision in ("approve", "deny"):
            r = self.decide(p, CAROL, decision)
            self.assertFalse(r["ok"], decision)
            self.assertIn("not authorized", r["error"])
        self.assertEqual(self.propose_status(p), "pending_approval")
        self.assertFalse(self.execute(p)["ok"])
        self.assertEqual(self.ran, [])

    def test_a_high_role_is_not_a_grant(self):
        self.assertEqual(CAROL.role, "admin")
        self.assertFalse(self.decide(self.propose(), CAROL)["ok"])

    def test_the_proposer_cannot_approve_even_when_authorized(self):
        p = self.propose(by=BOB)
        r = self.decide(p, BOB)
        self.assertFalse(r["ok"])
        self.assertIn("differ from the proposer", r["error"])

    def test_agent_id_is_descriptive_and_never_an_identity(self):
        # Bob proposes while claiming to be alice: he is still the proposer, so he still cannot approve.
        p = self.propose(by=BOB, agent_id="alice")
        self.assertEqual(p["proposed_by"]["operator_id"], "bob")
        self.assertNotIn("agent_id", p)
        self.assertEqual(p["claimed_agent_id"], "alice")
        self.assertIn("differ from the proposer", self.decide(p, BOB)["error"])
        # Alice proposes while claiming to be bob: bob is not the proposer, so bob can approve.
        q = self.propose(by=ALICE, agent_id="bob")
        self.assertTrue(self.decide(q, BOB)["ok"])

    def test_a_non_identified_caller_cannot_approve(self):
        from mcp_principal import INTERNAL_TOKEN
        p = self.propose()
        r = self.decide(p, Principal(kind=INTERNAL_TOKEN, operator_id="bob"))
        self.assertFalse(r["ok"])
        self.assertIn("identified operator", r["error"])

    def test_revoking_the_grant_stops_an_approved_proposal_from_running(self):
        p = self.propose()
        self.assertTrue(self.decide(p, BOB)["ok"])
        self.o.dual_agent.approvers = frozenset()          # the grant is withdrawn before execution
        r = self.execute(p)
        self.assertFalse(r["ok"])
        self.assertIn("no longer", r["error"])
        self.assertEqual(self.ran, [])

    def test_a_forged_approval_record_by_an_unauthorized_operator_does_not_run(self):
        p = self.propose()
        record = next(x for x in self.o.dual_agent.decision_log if x["proposal_id"] == p["proposal_id"])
        record.update(status="approved", approved_by=CAROL.to_dict(),
                      approved_params_digest=record["params_digest"])
        self.assertFalse(self.execute(p)["ok"])
        self.assertEqual(self.ran, [])

    def propose_status(self, p):
        return next(x for x in self.o.dual_agent.decision_log if x["proposal_id"] == p["proposal_id"])["status"]


class NobodyAuthorizedTest(AuthorityTest):
    APPROVERS = ""

    def test_with_no_grants_nobody_can_approve(self):
        p = self.propose()
        for who in (ALICE, BOB, CAROL):
            self.assertIn("not authorized", self.decide(p, who)["error"])
        self.assertFalse(self.execute(p)["ok"])
        self.assertEqual(self.ran, [])

    # The inherited tests assume bob is authorized; they do not apply to this configuration.
    test_an_authorized_operator_other_than_the_proposer_can_approve = None
    test_the_proposer_cannot_approve_even_when_authorized = None
    test_agent_id_is_descriptive_and_never_an_identity = None
    test_revoking_the_grant_stops_an_approved_proposal_from_running = None


if __name__ == "__main__":
    unittest.main()
