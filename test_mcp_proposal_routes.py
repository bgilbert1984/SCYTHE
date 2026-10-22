import logging
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import control_audit
from hypergraph_engine import HypergraphEngine
from mcp_effects import APPROVAL_MODES_ENV
from mcp_server import MCPHandler
from rf_mcp import register_rf_tools
from test_sdr_route_lifecycle import FakeBridge


class ProposalRoutesTest(unittest.TestCase):
    """ADR 0006 Decision 6 over REST: list what is waiting, approve (and run) or deny as another operator."""

    @classmethod
    def setUpClass(cls):
        logging.disable(logging.CRITICAL)
        cls.addClassCleanup(logging.disable, logging.NOTSET)
        import rf_scythe_api_server as rf
        cls.rf = rf

    def setUp(self):
        rf = self.rf
        self.bridge = FakeBridge()
        with mock.patch.dict(os.environ, {APPROVAL_MODES_ENV: "HARDWARE_CONTROL=SECOND_PRINCIPAL"}):
            self.handler = MCPHandler(HypergraphEngine(), use_orchestrator=True)
        register_rf_tools(self.handler.engine, self.handler)
        self.orch = self.handler._orchestrator
        ops = {f"op-{i}": SimpleNamespace(operator_id=f"op-{i}", callsign=f"CALL{i}", role=SimpleNamespace(value="operator"))
               for i in (1, 2)}
        sessions = {"t1": ("op-1", "s1"), "t1b": ("op-1", "s1b"), "t2": ("op-2", "s2")}
        manager = SimpleNamespace(
            operators=ops,
            validate_session=lambda t: SimpleNamespace(operator_id=sessions[t][0], session_id=sessions[t][1])
            if t in sessions else None)
        for p in (mock.patch.object(rf, "_rf_bridge_or_error", lambda: (self.bridge, None)),
                  mock.patch("rf_mcp.get_rf_bridge", lambda: self.bridge),
                  mock.patch.object(rf, "operator_manager", manager),
                  mock.patch.dict(rf.app.config, {"INTERNAL_TOKEN": "s3cret"}),
                  mock.patch.dict(rf.__dict__, {"mcp_handler": self.handler})):
            p.start()
            self.addCleanup(p.stop)
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.store = control_audit.configure(os.path.join(self.dir.name, "a.sqlite3"), 90)
        self.addCleanup(control_audit.reset)
        self.client = rf.app.test_client()

    def call(self, method, path, token="t1", body=None, headers=None):
        h = dict(headers or {})
        if token:
            h["X-Session-Token"] = token
        return getattr(self.client, method)(path, json=body, headers=h, environ_base={"REMOTE_ADDR": "203.0.113.5"})

    def tune(self, token="t1", hz=1e6, **extra):
        r = self.call("post", "/api/sdr/tune", token, {"frequency_hz": hz, **extra})
        self.assertEqual(r.get_json()["outcome"], "pending_approval", r.get_json())
        return r.get_json()["proposal_id"]

    def decide(self, pid, decision="approve", token="t2", headers=None, **body):
        return self.call("post", f"/api/mcp/proposals/{pid}/decision", token, {"decision": decision, **body},
                         headers=headers)


class ListTest(ProposalRoutesTest):
    def test_it_needs_an_authenticated_caller(self):
        self.assertEqual(self.call("get", "/api/mcp/proposals/pending", token=None).status_code, 401)
        self.assertEqual(self.call("get", "/api/mcp/proposals/pending", token="bad").status_code, 401)

    def test_it_lists_only_waiting_proposals_and_marks_the_callers_own(self):
        pid = self.tune(api_key="k-123")
        self.call("post", "/api/sdr/stop", "t1", {})                       # approved at once: not listed
        for token, own in (("t1", True), ("t1b", True), ("t2", False)):
            j = self.call("get", "/api/mcp/proposals/pending", token).get_json()
            (item,) = j["proposals"]
            self.assertEqual((item["proposal_id"], item["tool"], item["own"], item["initiator"]),
                             (pid, "rf_tune", own, "operator_command"), token)
            self.assertEqual(item["proposed_by"]["operator_id"], "op-1")
            self.assertGreater(item["expires_at"], item["created_at"])
        self.assertNotIn("k-123", str(j))                                   # secret-named params are redacted

    def test_an_expired_proposal_is_not_listed(self):
        pid = self.tune()
        next(p for p in self.orch.dual_agent.decision_log if p["proposal_id"] == pid)["timestamp"] -= 10_000
        self.assertEqual(self.call("get", "/api/mcp/proposals/pending", "t2").get_json()["proposals"], [])

    def test_it_is_a_503_without_the_lifecycle(self):
        with mock.patch.dict(self.rf.__dict__, {"mcp_handler": None}):
            self.assertEqual(self.call("get", "/api/mcp/proposals/pending").status_code, 503)


class DecisionTest(ProposalRoutesTest):
    def test_another_operator_approves_and_it_runs_with_the_whole_chain_recorded(self):
        pid = self.tune(hz=433.92e6)
        self.assertEqual(self.bridge.calls, [])
        r = self.decide(pid, note="confirmed with the net")
        j = r.get_json()
        self.assertEqual((r.status_code, j["status"], j["outcome"], j["tool"]), (200, "ok", "executed", "rf_tune"), j)
        self.assertEqual(self.bridge.calls, ["tune"])                         # Phase 0, and it ran: an operator command
        history = control_audit.reconstruct_proposal(self.store, pid)
        self.assertEqual((history["proposed_by"], history["approved_by"], history["executed_by"]),
                         ("op-1", "op-2", "op-2"))
        run = [e for e in self.store.events(proposal_id=pid) if e["action"] == "rf_tune"]
        self.assertEqual({(e["source"], e["initiator"]) for e in run}, {("api.mcp.approve", "operator_command")})

    def test_the_proposer_cannot_approve_even_from_another_session(self):
        pid = self.tune()
        for token in ("t1", "t1b"):
            r = self.decide(pid, token=token)
            self.assertEqual(r.status_code, 403, r.get_json())
            self.assertIn("differ from the proposer", r.get_json()["message"])
        self.assertEqual(self.bridge.calls, [])
        self.assertEqual(len(self.call("get", "/api/mcp/proposals/pending", "t2").get_json()["proposals"]), 1)

    def test_an_internal_token_or_unauthenticated_caller_cannot_approve(self):
        pid = self.tune()
        r = self.decide(pid, token=None, headers={"X-Internal-Token": "s3cret"})
        self.assertEqual((r.status_code, "identified operator" in r.get_json()["message"]), (403, True))
        self.assertEqual(self.decide(pid, token=None).status_code, 401)
        self.assertEqual(self.bridge.calls, [])

    def test_deny_ends_it_and_nothing_runs(self):
        pid = self.tune()
        j = self.decide(pid, "deny", note="not now").get_json()
        self.assertEqual((j["status"], j["outcome"]), ("ok", "denied"))
        self.assertEqual(self.decide(pid).status_code, 409)                   # cannot approve after deny
        self.assertEqual(self.bridge.calls, [])

    def test_double_approval_unknown_id_and_bad_decision(self):
        pid = self.tune()
        self.assertEqual(self.decide(pid).status_code, 200)
        self.assertEqual(self.decide(pid).status_code, 409)
        self.assertEqual(self.bridge.calls, ["tune"])                          # ran once
        self.assertEqual(self.decide("nope").status_code, 404)
        self.assertEqual(self.decide(self.tune(), "maybe").status_code, 400)

    def test_an_expired_proposal_cannot_be_approved(self):
        pid = self.tune()
        next(p for p in self.orch.dual_agent.decision_log if p["proposal_id"] == pid)["timestamp"] -= 10_000
        r = self.decide(pid)
        self.assertEqual((r.status_code, "expired" in r.get_json()["message"]), (409, True))
        self.assertEqual(self.bridge.calls, [])

    def test_a_bridge_failure_after_approval_is_reported_not_hidden(self):
        pid = self.tune()
        self.bridge.fail_with = RuntimeError("rig refused")
        r = self.decide(pid)
        self.assertEqual((r.status_code, r.get_json()["outcome"], r.get_json()["approved"]), (503, "error", True))

    def test_an_agent_proposal_is_still_held_to_the_phase_after_approval(self):
        from mcp_principal import OPERATOR_SESSION, Principal, principal_scope
        with principal_scope(Principal(OPERATOR_SESSION, operator_id="op-1", session_id="s1")):
            p = self.orch.propose_action("rf_tune", {"frequency_hz": 1e6}, confidence=0.9)   # autonomous
        self.assertEqual(p["status"], "pending_approval")
        r = self.decide(p["proposal_id"])
        self.assertEqual((r.status_code, r.get_json()["outcome"], r.get_json()["approved"]), (409, "blocked_by_phase", True))
        self.assertEqual(self.bridge.calls, [])

    def test_it_is_a_503_without_the_lifecycle(self):
        with mock.patch.dict(self.rf.__dict__, {"mcp_handler": None}):
            self.assertEqual(self.decide("x").status_code, 503)


class GovernanceTest(ProposalRoutesTest):
    """The state the Mission Governance page shows: read-only, descriptive, for identified operators."""

    def test_it_needs_an_authenticated_caller(self):
        self.assertEqual(self.call("get", "/api/mcp/governance", token=None).status_code, 401)
        self.assertEqual(self.call("get", "/api/mcp/governance", token="bad").status_code, 401)
        self.assertEqual(self.call("get", "/api/mcp/governance", token=None,
                                   headers={"X-Internal-Token": "s3cret"}).status_code, 200)   # a service may read it

    def test_it_reports_the_phase_the_effective_modes_and_the_deployment_overrides(self):
        j = self.call("get", "/api/mcp/governance").get_json()
        self.assertEqual((j["lifecycle_enabled"], j["phase"], j["remote_phase_control_enabled"]),
                         (True, {"value": 0, "name": "PHASE_0_OBSERVE_ONLY"}, False))
        self.assertEqual(j["approval_modes"]["HARDWARE_CONTROL"], "SECOND_PRINCIPAL")
        self.assertEqual(j["approval_modes"]["LOCAL_STATE"], "AUTOMATED")
        self.assertEqual(j["approval_modes"]["READ_ONLY"], "DIRECT")
        self.assertEqual(j["approval_mode_overrides"], {"HARDWARE_CONTROL": "SECOND_PRINCIPAL"})
        self.assertGreater(j["pending_ttl_seconds"], 0)
        self.assertEqual(j["caller"]["operator_id"], "op-1")

    def test_remote_phase_control_follows_the_deployment_setting(self):
        with mock.patch.dict(os.environ, {"MCP_ALLOW_REMOTE_PHASE_CONTROL": "true"}):
            self.assertTrue(self.call("get", "/api/mcp/governance").get_json()["remote_phase_control_enabled"])

    def test_it_changes_nothing(self):
        before = (self.orch.phase, list(self.orch.dual_agent.decision_log), list(self.bridge.calls))
        self.call("get", "/api/mcp/governance")
        self.assertEqual((self.orch.phase, list(self.orch.dual_agent.decision_log), list(self.bridge.calls)), before)

    def test_without_the_lifecycle_it_says_so_instead_of_failing(self):
        with mock.patch.dict(self.rf.__dict__, {"mcp_handler": None}):
            r = self.call("get", "/api/mcp/governance")
        self.assertEqual((r.status_code, r.get_json()["lifecycle_enabled"]), (200, False))
        self.assertNotIn("phase", r.get_json())


class RecentTest(ProposalRoutesTest):
    """The lifecycle state of every recent proposal, so the page can say why something did or did not run."""

    def recent(self, token="t1", query=""):
        return self.call("get", "/api/mcp/proposals/recent" + query, token)

    def test_it_needs_an_authenticated_caller_and_the_lifecycle(self):
        self.assertEqual(self.recent(token=None).status_code, 401)
        with mock.patch.dict(self.rf.__dict__, {"mcp_handler": None}):
            self.assertEqual(self.recent().status_code, 503)

    def test_it_shows_each_state_who_acted_and_why(self):
        waiting = self.tune(hz=1e6, api_key="k-123")
        approved = self.tune(hz=2e6)
        self.decide(approved)
        denied = self.tune(hz=3e6)
        self.decide(denied, "deny", note="not now")
        j = self.recent("t2").get_json()
        by_id = {p["proposal_id"]: p for p in j["proposals"]}
        self.assertEqual([by_id[waiting]["status"], by_id[approved]["status"], by_id[denied]["status"]],
                         ["pending_approval", "executed", "denied"])
        self.assertEqual(by_id[approved]["approved_by"]["callsign"], "CALL2")
        self.assertEqual(by_id[approved]["proposed_by"]["operator_id"], "op-1")
        self.assertEqual(by_id[denied]["denied_by"]["operator_id"], "op-2")
        self.assertIn("expires_at", by_id[waiting])
        self.assertNotIn("expires_at", by_id[approved])                     # only a waiting proposal has a deadline
        self.assertIn("awaiting independent approval", by_id[waiting]["reason"])
        self.assertNotIn("k-123", str(j))                                   # secret-named params are redacted
        self.assertEqual([p["proposal_id"] for p in j["proposals"]], [denied, approved, waiting])   # newest first

    def test_a_proposal_held_by_the_phase_says_why(self):
        proposal = self.orch.propose_action("rf_tune", {"frequency_hz": 5e6}, confidence=1.0, agent_id="agent")
        self.decide(proposal["proposal_id"])
        item = next(p for p in self.recent().get_json()["proposals"] if p["proposal_id"] == proposal["proposal_id"])
        self.assertEqual(item["initiator"], "autonomous")
        self.assertIn("Phase 0", item["reason"])

    def test_limit_is_bounded_and_validated(self):
        for hz in (1e6, 2e6, 3e6):
            self.tune(hz=hz)
        self.assertEqual(len(self.recent(query="?limit=2").get_json()["proposals"]), 2)
        self.assertEqual(self.recent(query="?limit=abc").status_code, 400)


if __name__ == "__main__":
    unittest.main()
