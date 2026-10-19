import json
import unittest
from types import SimpleNamespace
from unittest import mock

from flask import Flask

from hypergraph_engine import HypergraphEngine
from mcp_effects import LOCAL_STATE
from mcp_principal import (
    ANONYMOUS, INTERNAL_TOKEN, LOOPBACK, OPERATOR_SESSION, UNSPECIFIED,
    Principal, coerce, current_principal, principal_dict, principal_scope,
)
from mcp_registry import Tool
from mcp_server import register_mcp_routes


class PrincipalTest(unittest.TestCase):
    def test_only_an_operator_session_is_identified(self):
        self.assertTrue(Principal(OPERATOR_SESSION, operator_id="op-1").identified)
        for kind in (INTERNAL_TOKEN, LOOPBACK, ANONYMOUS, UNSPECIFIED):
            self.assertFalse(Principal(kind, operator_id="op-1").identified)
        self.assertFalse(Principal(OPERATOR_SESSION).identified)

    def test_scope_sets_and_restores(self):
        self.assertIsNone(current_principal())
        p = Principal(OPERATOR_SESSION, operator_id="op-1", session_id="s-1")
        with principal_scope(p):
            self.assertIs(current_principal(), p)
            self.assertEqual(principal_dict()["operator_id"], "op-1")
        self.assertIsNone(current_principal())
        self.assertIsNone(principal_dict())

    def test_coerce(self):
        p = Principal(LOOPBACK)
        self.assertIs(coerce(p), p)
        self.assertEqual(coerce(True).kind, UNSPECIFIED)   # a validator that only says yes
        self.assertIsNone(coerce(False))
        self.assertIsNone(coerce(None))

    def test_dict_has_no_token_field(self):
        d = Principal(OPERATOR_SESSION, operator_id="op-1", session_id="s-1").to_dict()
        self.assertEqual(set(d), {"kind", "operator_id", "session_id", "identified"})


def _jsonrpc(client, method, params):
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    return r.status_code, r.get_json()


class McpFlowTest(unittest.TestCase):
    """propose -> execute over the real /mcp route; identity must reach every record."""

    def setUp(self):
        self.app = Flask(__name__)
        self.current = None
        self.handler = register_mcp_routes(
            self.app, HypergraphEngine(), use_orchestrator=True,
            auth_validator=lambda: self.current)
        probe = Tool("probe_mutation", "test tool", {"type": "object"}, {"type": "object"},
                     lambda **k: {"done": True}, mutates_state=True, effect=LOCAL_STATE)
        self.handler._tools["probe_mutation"] = probe
        self.handler._orchestrator.registry.register(probe)
        self.handler._orchestrator.set_phase(2, dry_run=False)   # 0 observe-only, 1 shadow (estimates only), 2 executes
        self.client = self.app.test_client()

    def test_unauthenticated_request_is_refused(self):
        self.current = None
        status, _ = _jsonrpc(self.client, "orchestrate/propose", {"tool_name": "probe_mutation"})
        self.assertEqual(status, 401)

    def test_identity_reaches_proposal_execution_and_audit(self):
        alice = Principal(OPERATOR_SESSION, operator_id="op-alice", session_id="sess-1", role="operator")
        self.current = alice
        _, body = _jsonrpc(self.client, "orchestrate/propose",
                           {"tool_name": "probe_mutation", "params": {}, "confidence": 0.95})
        proposal = body["result"]
        self.assertEqual(proposal["status"], "approved")
        self.assertEqual(proposal["proposed_by"]["operator_id"], "op-alice")
        self.assertTrue(proposal["proposed_by"]["identified"])

        bob = Principal(OPERATOR_SESSION, operator_id="op-bob", session_id="sess-2")
        self.current = bob
        with self.assertLogs("mcp_registry", level="INFO") as logs:
            _, body = _jsonrpc(self.client, "orchestrate/execute", {"proposal_id": proposal["proposal_id"]})
        self.assertTrue(body["result"]["ok"], body)

        logged = next(json.loads(m.split("[MCP_AUDIT] ", 1)[1]) for m in logs.output if "[MCP_AUDIT]" in m)
        self.assertEqual(logged["proposal_id"], proposal["proposal_id"])
        self.assertEqual(logged["principal"]["operator_id"], "op-bob")
        self.assertEqual(logged["effect"], LOCAL_STATE)

        stored = next(p for p in self.handler._orchestrator.dual_agent.decision_log
                      if p["proposal_id"] == proposal["proposal_id"])
        self.assertEqual(stored["proposed_by"]["operator_id"], "op-alice")
        self.assertEqual(stored["executed_by"]["operator_id"], "op-bob")

    def test_non_identifying_callers_are_labelled_not_hidden(self):
        self.current = Principal(LOOPBACK)
        _, body = _jsonrpc(self.client, "orchestrate/propose",
                           {"tool_name": "probe_mutation", "params": {}, "confidence": 0.95})
        proposed_by = body["result"]["proposed_by"]
        self.assertEqual((proposed_by["kind"], proposed_by["identified"]), (LOOPBACK, False))

    def test_plain_truthy_validator_still_works_and_is_labelled(self):
        self.current = True
        _, body = _jsonrpc(self.client, "orchestrate/propose",
                           {"tool_name": "probe_mutation", "params": {}, "confidence": 0.95})
        self.assertEqual(body["result"]["proposed_by"]["kind"], UNSPECIFIED)

    def test_principal_does_not_leak_between_requests(self):
        self.current = Principal(OPERATOR_SESSION, operator_id="op-alice")
        _jsonrpc(self.client, "orchestrate/status", {})
        self.assertIsNone(current_principal())


class ServerBoundaryTest(unittest.TestCase):
    """_rf_bridge_principal keeps the decisions _rf_bridge_authorized made, plus who."""

    @classmethod
    def setUpClass(cls):
        import logging
        logging.disable(logging.CRITICAL)
        import rf_scythe_api_server as rf
        cls.rf = rf

    @classmethod
    def tearDownClass(cls):
        import logging
        logging.disable(logging.NOTSET)

    def principal(self, *, manager, headers=None, remote="127.0.0.1", require_identity=True, internal=""):
        rf = self.rf
        with mock.patch.dict(rf.app.config, {"INTERNAL_TOKEN": internal}), \
             mock.patch.object(rf, "operator_manager", manager), \
             rf.app.test_request_context("/mcp", headers=headers or {}, environ_base={"REMOTE_ADDR": remote}):
            return rf._rf_bridge_principal(require_identity), rf._rf_bridge_authorized(require_identity)

    def manager(self, valid_token="good"):
        op = SimpleNamespace(operator_id="op-1", callsign="ALPHA", role=SimpleNamespace(value="operator"))
        sess = SimpleNamespace(operator_id="op-1", session_id="sess-9", session_token=valid_token)
        return SimpleNamespace(operators={"op-1": op},
                               validate_session=lambda t: sess if t == valid_token else None)

    def test_valid_session_yields_identified_principal_without_token(self):
        p, ok = self.principal(manager=self.manager(), headers={"X-Session-Token": "good"})
        self.assertTrue(ok)
        self.assertEqual((p.kind, p.operator_id, p.session_id, p.role, p.callsign),
                         (OPERATOR_SESSION, "op-1", "sess-9", "operator", "ALPHA"))
        self.assertNotIn("good", json.dumps(p.to_dict()))

    def test_bad_or_missing_session_is_refused(self):
        self.assertEqual(self.principal(manager=self.manager(), headers={"X-Session-Token": "bad"}), (None, False))
        self.assertEqual(self.principal(manager=self.manager()), (None, False))

    def test_internal_token_is_labelled_as_such(self):
        p, ok = self.principal(manager=self.manager(), headers={"X-Internal-Token": "s3cret"}, internal="s3cret")
        self.assertTrue(ok)
        self.assertEqual((p.kind, p.identified), (INTERNAL_TOKEN, False))

    def test_no_operator_manager_modes(self):
        p, ok = self.principal(manager=None, remote="127.0.0.1")
        self.assertEqual((p.kind, ok), (LOOPBACK, True))
        self.assertEqual(self.principal(manager=None, remote="10.1.2.3"), (None, False))
        p, ok = self.principal(manager=None, remote="10.1.2.3", require_identity=False)
        self.assertEqual((p.kind, ok), (ANONYMOUS, True))     # development mode, now labelled


if __name__ == "__main__":
    unittest.main()
