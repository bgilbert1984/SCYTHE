import logging
import os
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import control_audit
from hypergraph_engine import HypergraphEngine
from mcp_effects import LOCAL_STATE
from mcp_principal import OPERATOR_SESSION
from mcp_server import MCPHandler, ToolDef


class EmbeddingRouteLifecycleTest(unittest.TestCase):
    """#236 / ADR 0005 Decision 4: POST /api/embedding/entity is an adapter into the
    lifecycle.  Real Flask route, real orchestrator, a stub embedding body."""

    @classmethod
    def setUpClass(cls):
        logging.disable(logging.CRITICAL)
        cls.addClassCleanup(logging.disable, logging.NOTSET)
        import rf_scythe_api_server as rf
        cls.rf = rf

    def setUp(self):
        rf = self.rf
        self.embedded = []

        def embed(params):
            self.embedded.append(params)
            return {"entity_id": params["entity_id"], "vec_idx": 7, "total_vectors": 8, "model": "stub"}

        self.handler = MCPHandler(HypergraphEngine(), use_orchestrator=True)
        self.embed_tool = ToolDef(
            "embed_entity", "d",
            {"type": "object", "properties": {"entity_id": {"type": "string"}, "event": {"type": "object"}},
             "required": ["entity_id"]},
            embed, mutates_state=True, effect=LOCAL_STATE)
        self.handler._tools["embed_entity"] = self.embed_tool
        self.orch = self.handler._orchestrator

        op = SimpleNamespace(operator_id="op-1", callsign="ALPHA", role=SimpleNamespace(value="operator"))
        sess = SimpleNamespace(operator_id="op-1", session_id="sess-9")
        manager = SimpleNamespace(operators={"op-1": op},
                                  validate_session=lambda t: sess if t == "good" else None)
        for p in (mock.patch.object(rf, "operator_manager", manager),
                  mock.patch.dict(rf.__dict__, {"mcp_handler": self.handler})):
            p.start()
            self.addCleanup(p.stop)
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.audit_path = os.path.join(self.dir.name, "a.sqlite3")
        control_audit.configure(self.audit_path, 90)
        self.addCleanup(control_audit.reset)
        self.client = rf.app.test_client()

    def post(self, body=None, token="good"):
        h = {"X-Session-Token": token} if token else {}
        return self.client.post("/api/embedding/entity", json=body, headers=h,
                                environ_base={"REMOTE_ADDR": "203.0.113.5"})

    def audit_rows(self):
        with sqlite3.connect(self.audit_path) as conn:
            return conn.execute(
                "SELECT action, effect, result, principal_id, proposal_id FROM control_audit_events ORDER BY ts_epoch, rowid"
            ).fetchall()

    def test_an_unauthenticated_caller_is_refused_and_nothing_is_embedded(self):
        r = self.post({"entity_id": "e1"}, token=None)
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.embedded, [])

    def test_a_missing_entity_id_is_a_bad_request(self):
        self.assertEqual(self.post({"event": {}}).status_code, 400)

    def test_phase_0_is_reported_as_blocked_not_as_a_transport_error(self):
        r = self.post({"entity_id": "e1", "event": {"ssid": "x"}})
        body = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertEqual((body["outcome"], body["status"]), ("blocked_by_phase", "not_executed"))
        self.assertIn("observe-only", body["reason"])
        self.assertEqual(self.embedded, [])

    def test_in_phase_2_it_embeds_once_and_keeps_the_earlier_response_fields(self):
        self.orch.set_phase(2, dry_run=False)
        r = self.post({"entity_id": "e1", "event": {"ssid": "x"}})
        body = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertEqual((body["status"], body["outcome"]), ("ok", "executed"))
        self.assertEqual((body["entity_id"], body["vec_idx"], body["total_vectors"]), ("e1", 7, 8))
        self.assertEqual(self.embedded, [{"entity_id": "e1", "event": {"ssid": "x", "entity_id": "e1"}}])

    def test_the_execution_is_in_the_control_audit_under_the_caller_and_the_proposal(self):
        self.orch.set_phase(2, dry_run=False)
        pid = self.post({"entity_id": "e1", "event": {}}).get_json()["proposal_id"]
        rows = self.audit_rows()
        self.assertEqual([r[2] for r in rows], ["started", "success"])
        for action, effect, _, principal_id, proposal_id in rows:
            self.assertEqual((action, effect, principal_id, proposal_id), ("embed_entity", LOCAL_STATE, "op-1", pid))

    def test_an_exhausted_mutation_budget_is_reported_not_swallowed(self):
        # The effective budget is int(base * executor trust), so a base of 1 is 0 until trust is 1.
        self.orch.set_phase(2, mutation_budget=1, dry_run=False)
        body = self.post({"entity_id": "e1", "event": {}}).get_json()
        self.assertEqual((body["outcome"], body["status"]), ("budget_exhausted", "not_executed"))
        self.assertEqual(self.embedded, [])

    def test_phase_1_is_a_shadow_run_and_embeds_nothing(self):
        self.orch.set_phase(1, dry_run=False)
        body = self.post({"entity_id": "e1", "event": {}}).get_json()
        self.assertEqual(body["outcome"], "shadow")
        self.assertEqual(self.embedded, [])

    def test_a_tool_level_failure_is_an_error_outcome(self):
        self.embed_tool.fn = lambda params: {"error": "embedding failed -- Ollama may be unavailable"}
        self.handler._orchestrator.registry._tools.pop("embed_entity", None)  # re-adopt the replaced body
        self.orch.set_phase(2, dry_run=False)
        r = self.post({"entity_id": "e1", "event": {}})
        self.assertEqual((r.status_code, r.get_json()["outcome"]), (503, "error"))
        self.assertIn("Ollama", r.get_json()["reason"])

    def test_it_is_unavailable_when_the_lifecycle_is_not_initialized(self):
        with mock.patch.dict(self.rf.__dict__, {"mcp_handler": None}):
            self.assertEqual(self.post({"entity_id": "e1"}).status_code, 503)

    def test_the_route_no_longer_mutates_the_embedding_engine_itself(self):
        engine = mock.Mock()
        with mock.patch.dict(self.rf.__dict__, {"embedding_engine": engine}):
            self.post({"entity_id": "e1", "event": {}})
        engine.add_entity.assert_not_called()
        engine.save_index.assert_not_called()


if __name__ == "__main__":
    unittest.main()
