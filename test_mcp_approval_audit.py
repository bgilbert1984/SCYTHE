import inspect
import logging
import os
import tempfile
import unittest
from unittest import mock

import control_audit
import mcp_orchestrator
import mcp_safety
from hypergraph_engine import HypergraphEngine
from mcp_effects import (
    APPROVAL_MODES_ENV, APPROVAL_TTL_ENV, APPROVERS_ENV, DEFAULT_APPROVAL_TTL_SECONDS, LOCAL_STATE, READ_ONLY, deployment_approval_ttl,
)
from mcp_principal import OPERATOR_SESSION, Principal, principal_scope
from mcp_server import MCPHandler, ToolDef

ALICE = Principal(kind=OPERATOR_SESSION, operator_id="alice", session_id="s1")
BOB = Principal(kind=OPERATOR_SESSION, operator_id="bob", session_id="s2")
SCHEMA = {"type": "object", "properties": {"n": {"type": "integer"}, "api_token": {"type": "string"}}}


class _Fixture(unittest.TestCase):
    SETTING = "LOCAL_STATE=SECOND_PRINCIPAL"

    @classmethod
    def setUpClass(cls):
        logging.disable(logging.CRITICAL)
        cls.addClassCleanup(logging.disable, logging.NOTSET)

    def setUp(self):
        self.ran = []
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "a.sqlite3")
        self.store = control_audit.configure(self.path, 90)
        self.addCleanup(control_audit.reset)
        self.h = self.handler()
        self.o = self.h._orchestrator
        self.o.set_phase(2, dry_run=False)

    def handler(self, setting=None):
        env = {APPROVAL_MODES_ENV: self.SETTING if setting is None else setting, APPROVERS_ENV: "alice,bob"}
        with mock.patch.dict(os.environ, env):
            h = MCPHandler(HypergraphEngine(), use_orchestrator=True)
        h._tools["probe_write"] = ToolDef("probe_write", "d", SCHEMA, lambda p: self.ran.append(p) or {"ok": True},
                                          mutates_state=True, effect=LOCAL_STATE)
        h._tools["probe_read"] = ToolDef("probe_read", "d", SCHEMA, lambda p: {"ok": True}, effect=READ_ONLY)
        return h

    def propose(self, who=ALICE, tool="probe_write", params=None, orch=None):
        with principal_scope(who):
            return (orch or self.o).propose_action(tool, params if params is not None else {"n": 1}, confidence=0.9)

    def decide(self, p, who, decision="approve", note=""):
        with principal_scope(who):
            return self.o.approve_proposal(p["proposal_id"], decision, note)

    def execute(self, p, who=BOB):
        with principal_scope(who):
            return self.o.execute_proposal(p["proposal_id"])

    def rows(self, p):
        return self.store.events(proposal_id=p["proposal_id"])

    def age(self, p, seconds):
        p["timestamp"] -= seconds


class TransitionRowsTest(_Fixture):
    """ADR 0006: proposer, approver and executor are recorded as rows keyed by proposal_id."""

    def test_the_whole_lifecycle_is_recorded_under_the_proposal_id(self):
        p = self.propose()
        self.decide(p, BOB, note="target confirmed")
        self.assertTrue(self.execute(p)["ok"])
        got = [(r["action"], r["result"], r["principal_id"]) for r in self.rows(p)]
        self.assertEqual(got, [("proposal.created", "pending_approval", "alice"),
                               ("proposal.approved", "approved", "bob"),
                               ("probe_write", "started", "bob"),
                               ("probe_write", "success", "bob")])
        self.assertEqual({r["effect"] for r in self.rows(p)}, {LOCAL_STATE})
        self.assertEqual(self.rows(p)[1]["result_summary"], "target confirmed")

    def test_the_created_row_carries_what_is_needed_to_understand_the_proposal(self):
        import json
        p = self.propose(params={"n": 3, "api_token": "s3cret"})
        body = json.loads(self.rows(p)[0]["parameters_json"])
        self.assertEqual((body["tool"], body["approval_mode"], body["params_digest"]),
                         ("probe_write", "SECOND_PRINCIPAL", p["params_digest"]))
        self.assertEqual(body["params"], {"n": 3, "api_token": "<redacted>"})

    def test_denial_is_recorded_with_the_denier_and_note(self):
        p = self.propose()
        self.decide(p, BOB, "deny", note="wrong target")
        denied = [r for r in self.rows(p) if r["action"] == "proposal.denied"]
        self.assertEqual([(r["principal_id"], r["result"], r["result_summary"]) for r in denied], [("bob", "denied", "wrong target")])

    def test_a_rejected_proposal_is_recorded_too(self):
        with principal_scope(ALICE):
            p = self.o.propose_action("probe_write", {"n": 1}, confidence=0.1)
        self.assertEqual([(r["action"], r["result"]) for r in self.rows(p)], [("proposal.created", "rejected")])

    def test_automated_proposals_record_their_proposer_and_read_only_ones_write_nothing(self):
        h = self.handler(setting="")
        w = self.propose(orch=h._orchestrator)
        self.assertEqual([(r["action"], r["result"], r["principal_id"]) for r in self.rows(w)],
                         [("proposal.created", "approved", "alice")])
        r = self.propose(tool="probe_read", orch=h._orchestrator)
        self.assertEqual(self.rows(r), [])

    def test_without_an_audit_store_nothing_changes(self):
        control_audit.reset()
        p = self.propose()
        self.assertEqual(p["status"], "pending_approval")
        self.assertTrue(self.decide(p, BOB)["ok"])
        self.assertTrue(self.execute(p)["ok"])


class ReconstructionTest(_Fixture):
    def test_history_survives_losing_the_in_memory_proposals(self):
        p = self.propose()
        self.decide(p, BOB, note="ok")
        self.assertTrue(self.execute(p)["ok"])
        pid = p["proposal_id"]

        restarted = self.handler()                    # a new process: empty decision log, same audit database
        restarted._orchestrator.set_phase(2, dry_run=False)
        self.assertEqual(restarted._orchestrator.check_proposal(pid), {"ok": False, "error": "proposal not found"})
        history = control_audit.reconstruct_proposal(self.store, pid)
        self.assertEqual((history["tool"], history["proposed_by"], history["approved_by"], history["executed_by"]),
                         ("probe_write", "alice", "bob", "bob"))
        self.assertTrue(history["executed"])
        self.assertEqual(history["events"][0], ("proposal.created", "pending_approval"))

    def test_a_reconstructed_history_does_not_make_a_lost_proposal_executable(self):
        p = self.propose()
        self.decide(p, BOB)
        restarted = self.handler()
        restarted._orchestrator.set_phase(2, dry_run=False)
        with principal_scope(BOB):
            r = restarted._orchestrator.execute_proposal(p["proposal_id"])
        self.assertEqual((r["ok"], r["error"]), (False, "proposal not found"))
        self.assertEqual(self.ran, [])

    def test_denied_and_expired_proposals_reconstruct(self):
        d = self.propose()
        self.decide(d, BOB, "deny")
        e = self.propose(params={"n": 2})
        self.age(e, 10_000)
        self.assertFalse(self.decide(e, BOB)["ok"])
        self.assertEqual(control_audit.reconstruct_proposal(self.store, d["proposal_id"])["denied_by"], "bob")
        self.assertTrue(control_audit.reconstruct_proposal(self.store, e["proposal_id"])["expired"])


class AuditIsEvidenceNotAuthorityTest(_Fixture):
    """The audit records decisions; it never makes them."""

    def test_forged_approval_rows_do_not_approve(self):
        p = self.propose()
        self.store.record(execution_id="forged", action="proposal.approved", effect=LOCAL_STATE, result="approved",
                          principal={"kind": OPERATOR_SESSION, "operator_id": "bob"}, proposal_id=p["proposal_id"])
        self.assertFalse(self.execute(p)["ok"])
        self.assertEqual((self.o.check_proposal(p["proposal_id"])["status"], self.ran), ("pending_approval", []))

    def test_removing_the_audit_store_does_not_unapprove(self):
        p = self.propose()
        self.decide(p, BOB)
        control_audit.reset()
        self.assertTrue(self.execute(p)["ok"])
        self.assertEqual(len(self.ran), 1)

    def test_no_decision_reads_the_audit(self):
        p = self.propose()
        with mock.patch.object(control_audit.ControlAuditStore, "events", side_effect=AssertionError("audit read")), \
             mock.patch.object(control_audit, "get_store", side_effect=AssertionError("audit read")), \
             mock.patch.object(control_audit, "reconstruct_proposal", side_effect=AssertionError("audit read")):
            self.assertTrue(self.decide(p, BOB)["ok"])
            self.assertTrue(self.execute(p)["ok"])
            self.o.dual_agent.expire_pending()
            self.o.check_proposal(p["proposal_id"])

    def test_the_decision_code_does_not_reference_the_audit_readers(self):
        for fn in (mcp_safety.DualAgentOrchestrator.verify_approval, mcp_safety.DualAgentOrchestrator.decide_proposal,
                   mcp_safety.DualAgentOrchestrator.expire_pending, mcp_safety.DualAgentOrchestrator.propose_action,
                   mcp_orchestrator.AliasMCPOrchestrator.execute_proposal, mcp_orchestrator.AliasMCPOrchestrator.check_proposal):
            src = inspect.getsource(fn)
            for banned in ("reconstruct_proposal", ".events(", "get_store"):
                self.assertNotIn(banned, src, f"{fn.__qualname__} reads the audit via {banned}")

    def test_a_failing_audit_write_changes_no_decision(self):
        with mock.patch.object(control_audit.ControlAuditStore, "record", side_effect=OSError("disk full")):
            p = self.propose()
            self.assertEqual(p["status"], "pending_approval")
            self.assertTrue(self.decide(p, BOB)["ok"])
            self.assertTrue(self.execute(p)["ok"])
        self.assertGreaterEqual(self.store.write_failures, 3)
        self.assertEqual(len(self.ran), 1)


class PendingTtlTest(_Fixture):
    def test_the_ttl_setting(self):
        self.assertEqual(deployment_approval_ttl({}), DEFAULT_APPROVAL_TTL_SECONDS)
        self.assertEqual(deployment_approval_ttl({APPROVAL_TTL_ENV: " 60 "}), 60.0)
        for bad in ("0", "-5", "soon", "nan", "inf"):
            with self.assertRaises(ValueError, msg=bad):
                deployment_approval_ttl({APPROVAL_TTL_ENV: bad})

    def test_a_bad_ttl_stops_the_handler(self):
        with mock.patch.dict(os.environ, {APPROVAL_TTL_ENV: "never"}):
            with self.assertRaises(ValueError):
                MCPHandler(HypergraphEngine(), use_orchestrator=True)

    def test_a_proposal_inside_the_ttl_can_be_approved(self):
        p = self.propose()
        self.age(p, self.o.dual_agent.pending_ttl - 60)
        self.assertTrue(self.decide(p, BOB)["ok"])

    def test_an_expired_proposal_cannot_be_approved_or_executed(self):
        p = self.propose()
        self.age(p, self.o.dual_agent.pending_ttl + 1)
        r = self.decide(p, BOB)
        self.assertFalse(r["ok"])
        self.assertIn("expired", r["error"])
        self.assertFalse(self.execute(p)["ok"])
        self.assertIn("not pending_approval", self.decide(p, BOB)["error"])
        self.assertEqual(self.ran, [])
        self.assertEqual([r["action"] for r in self.rows(p)], ["proposal.created", "proposal.expired"])
        self.assertEqual(self.rows(p)[1]["principal_kind"], "none")

    def test_status_reports_expiry_without_anyone_deciding(self):
        p = self.propose()
        self.age(p, self.o.dual_agent.pending_ttl + 1)
        self.assertEqual(self.o.check_proposal(p["proposal_id"])["status"], "expired")

    def test_only_pending_proposals_expire(self):
        approved = self.propose()
        self.decide(approved, BOB)
        h = self.handler(setting="")
        automated = self.propose(orch=h._orchestrator)
        self.age(approved, 10_000)
        self.age(automated, 10_000)
        self.assertEqual(self.o.dual_agent.expire_pending(), 0)
        self.assertEqual(h._orchestrator.dual_agent.expire_pending(), 0)
        self.assertEqual((approved["status"], automated["status"]), ("approved", "approved"))

    def test_the_log_does_not_grow_with_proposals_nobody_approves(self):
        dual = self.o.dual_agent
        ps = [self.propose(params={"n": i}) for i in range(20)]
        self.assertEqual(len(dual.decision_log), 20)
        for p in ps:
            self.age(p, dual.pending_ttl + 1)
        self.assertEqual(dual.expire_pending(), 20)
        self.assertEqual(len(dual.decision_log), 20)           # kept for one more TTL so status stays answerable
        for p in ps:
            p["expired_at"] -= dual.pending_ttl + 1
        dual.expire_pending()
        self.assertEqual(len(dual.decision_log), 0)
        self.assertEqual(len([e for e in self.store.events(limit=1000) if e["action"] == "proposal.expired"]), 20)

    def test_a_new_proposal_sweeps_expired_ones(self):
        old = self.propose()
        self.age(old, self.o.dual_agent.pending_ttl + 1)
        self.propose(params={"n": 2})
        self.assertEqual(old["status"], "expired")


if __name__ == "__main__":
    unittest.main()
