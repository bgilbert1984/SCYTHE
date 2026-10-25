"""Restart policy for proposals (#228, ADR 0006 Durability).

Proposals are in memory.  A restart drops every pending and approved-but-unexecuted proposal,
and a dropped proposal cannot be decided or executed: it fails closed.  The audit then ends
each such history truthfully, with a proposal.expired row, instead of leaving it at
pending_approval for ever.
"""
import ast
import logging
import os
import pathlib
import tempfile
import unittest
from unittest import mock

import control_audit
from hypergraph_engine import HypergraphEngine
from mcp_effects import APPROVAL_MODES_ENV, APPROVERS_ENV, HARDWARE_CONTROL, LOCAL_STATE
from mcp_principal import OPERATOR_SESSION, Principal, principal_scope
from mcp_server import MCPHandler, ToolDef

ALICE = Principal(kind=OPERATOR_SESSION, operator_id="alice", session_id="s1")
BOB = Principal(kind=OPERATOR_SESSION, operator_id="bob", session_id="s2")


class RestartTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        logging.disable(logging.CRITICAL)
        cls.addClassCleanup(logging.disable, logging.NOTSET)

    def setUp(self):
        self.ran = []
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.store = control_audit.configure(os.path.join(self.dir.name, "a.sqlite3"), 90)
        self.addCleanup(control_audit.reset)
        self.h = self.boot()

    def boot(self):
        """A process start: a fresh handler and orchestrator, so an empty in-memory proposal log."""
        env = {APPROVAL_MODES_ENV: "LOCAL_STATE=SECOND_PRINCIPAL", APPROVERS_ENV: "alice,bob"}
        with mock.patch.dict(os.environ, env):
            h = MCPHandler(HypergraphEngine(), use_orchestrator=True)
        h._tools["probe_write"] = ToolDef(
            "probe_write", "d", {"type": "object", "properties": {"n": {"type": "integer"}}},
            lambda params: self.ran.append(params) or {"ok": True}, mutates_state=True, effect=LOCAL_STATE)
        h._orchestrator.set_phase(2, dry_run=False)
        return h

    def propose(self, h, by=ALICE):
        with principal_scope(by):
            return h._orchestrator.propose_action("probe_write", {"n": 1}, confidence=0.9)

    def restart(self):
        self.h = self.boot()
        return control_audit.close_orphaned_proposals()

    def history(self, p):
        return control_audit.reconstruct_proposal(self.store, p["proposal_id"])

    def test_a_pending_proposal_does_not_survive_a_restart_and_cannot_be_decided_or_run(self):
        p = self.propose(self.h)
        self.assertEqual(p["status"], "pending_approval")
        self.restart()
        o = self.h._orchestrator
        with principal_scope(BOB):
            self.assertEqual(o.approve_proposal(p["proposal_id"], "approve"), {"ok": False, "error": "proposal not found"})
            self.assertEqual(o.execute_proposal(p["proposal_id"]), {"ok": False, "error": "proposal not found"})
        self.assertEqual(self.ran, [])

    def test_an_approved_but_unexecuted_proposal_does_not_run_after_a_restart(self):
        p = self.propose(self.h)
        with principal_scope(BOB):
            self.assertTrue(self.h._orchestrator.approve_proposal(p["proposal_id"], "approve")["ok"])
        self.restart()
        with principal_scope(BOB):
            self.assertFalse(self.h._orchestrator.execute_proposal(p["proposal_id"])["ok"])
        self.assertEqual(self.ran, [])

    def test_the_audit_ends_the_history_of_an_orphaned_proposal(self):
        p = self.propose(self.h)
        self.assertEqual(self.history(p)["events"], [("proposal.created", "pending_approval")])
        self.assertEqual(self.restart(), [p["proposal_id"]])
        h = self.history(p)
        self.assertEqual(h["events"], [("proposal.created", "pending_approval"), ("proposal.expired", "expired")])
        self.assertTrue(h["expired"] and h["orphaned_by_restart"])
        self.assertFalse(h["executed"])
        row = self.store.events(proposal_id=p["proposal_id"])[-1]
        self.assertEqual((row["source"], row["effect"], row["initiator"]), ("startup", LOCAL_STATE, "autonomous"))

    def test_closing_is_idempotent(self):
        p = self.propose(self.h)
        self.assertEqual(self.restart(), [p["proposal_id"]])
        self.assertEqual(self.restart(), [])
        self.assertEqual(len(self.store.events(proposal_id=p["proposal_id"])), 2)

    def test_a_proposal_that_already_ended_is_left_alone(self):
        denied = self.propose(self.h)
        with principal_scope(BOB):
            self.h._orchestrator.approve_proposal(denied["proposal_id"], "deny")
        ran = self.propose(self.h)
        with principal_scope(BOB):
            self.h._orchestrator.approve_proposal(ran["proposal_id"], "approve")
            self.assertTrue(self.h._orchestrator.execute_proposal(ran["proposal_id"])["ok"])
        expired = self.propose(self.h)
        next(x for x in self.h._orchestrator.dual_agent.decision_log
             if x["proposal_id"] == expired["proposal_id"])["timestamp"] -= 10_000
        self.h._orchestrator.dual_agent.expire_pending()
        before = len(self.store.events(limit=1000))
        self.assertEqual(self.restart(), [])
        self.assertEqual(len(self.store.events(limit=1000)), before)
        self.assertEqual(self.ran, [{"n": 1}])

    def test_a_rejected_proposal_is_left_alone(self):
        self.store.record(execution_id="x", action="proposal.created", effect=LOCAL_STATE, result="rejected",
                          proposal_id="rejected-1")
        self.assertEqual(control_audit.close_orphaned_proposals(), [])

    def test_an_execution_that_began_is_not_claimed_to_have_failed_to_run(self):
        self.store.record(execution_id="e", action="proposal.created", effect=HARDWARE_CONTROL,
                          result="pending_approval", proposal_id="mid-1")
        self.store.record(execution_id="e2", action="rf_tune", effect=HARDWARE_CONTROL, result="started",
                          proposal_id="mid-1")
        self.assertEqual(control_audit.close_orphaned_proposals(), [])
        self.assertEqual([r["action"] for r in self.store.events(proposal_id="mid-1")], ["proposal.created", "rf_tune"])

    def test_an_automated_proposal_approved_but_never_executed_is_closed(self):
        self.store.record(execution_id="e", action="proposal.created", effect=LOCAL_STATE, result="approved",
                          proposal_id="auto-1")
        self.assertEqual(control_audit.close_orphaned_proposals(), ["auto-1"])

    def test_it_is_a_no_op_without_a_store(self):
        control_audit.reset()
        self.assertEqual(control_audit.close_orphaned_proposals(), [])


class ServerWiringTest(unittest.TestCase):
    """rf_scythe_api_server is too heavy to import here; read its startup block."""

    def test_startup_closes_orphans_after_the_handler_is_published_and_cannot_stop_startup(self):
        src = (pathlib.Path(__file__).parent / "rf_scythe_api_server.py").read_text()
        tree = ast.parse(src)
        matching = [n for n in ast.walk(tree) if isinstance(n, ast.Try) and "close_orphaned_proposals" in ast.unparse(n)]
        # the innermost try: the one that holds the call itself, not the startup blocks around it
        tries = [n for n in matching if not any(m is not n and m in list(ast.walk(n)) for m in matching)]
        self.assertEqual(len(tries), 1)
        self.assertEqual(ast.unparse(tries[0].handlers[0].type), "Exception")      # best effort: evidence only
        self.assertLess(src.index("globals()['mcp_handler'] = mcp_handler"), src.index("close_orphaned_proposals()"))


if __name__ == "__main__":
    unittest.main()
