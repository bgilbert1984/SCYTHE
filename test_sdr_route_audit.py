import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import control_audit
from mcp_principal import ANONYMOUS, INTERNAL_TOKEN, LOOPBACK, OPERATOR_SESSION


class FakeBridge:
    def __init__(self):
        self.calls = []
        self.fail_with = None

    def _do(self, name, result):
        self.calls.append(name)
        if self.fail_with:
            raise self.fail_with
        return result

    def start(self): return self._do("start", True)
    def stop(self): return self._do("stop", True)
    def tune(self, frequency_hz, mode=None, bandwidth_hz=0): return self._do("tune", {"frequency_hz": frequency_hz})
    def configure_stream(self, **changes): return self._do("config", {"configured": changes})
    def status(self, include_control=False): return {"state": "fake"}


class _SdrFixture(unittest.TestCase):
    """Real Flask routes, a fake bridge, a temp audit store.  Holds no tests of its own."""

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

    def setUp(self):
        rf = self.rf
        self.dir = tempfile.TemporaryDirectory()
        self.store = control_audit.configure(os.path.join(self.dir.name, "a.sqlite3"), 90,
                                             deployment_id="test-deploy")
        self.bridge = FakeBridge()
        op = SimpleNamespace(operator_id="op-1", callsign="ALPHA", role=SimpleNamespace(value="operator"))
        sess = SimpleNamespace(operator_id="op-1", session_id="sess-9")
        self.manager = SimpleNamespace(operators={"op-1": op},
                                       validate_session=lambda t: sess if t == "good" else None)
        from hypergraph_engine import HypergraphEngine
        from mcp_server import MCPHandler
        self.handler = MCPHandler(HypergraphEngine(), use_orchestrator=True)
        from rf_mcp import register_rf_tools
        register_rf_tools(self.handler.engine, self.handler)
        patches = [mock.patch.object(rf, "_rf_bridge_or_error", lambda: (self.bridge, None)),
                   mock.patch("rf_mcp.get_rf_bridge", lambda: self.bridge),
                   mock.patch.dict(rf.__dict__, {"mcp_handler": self.handler}),
                   mock.patch.object(rf, "operator_manager", self.manager),
                   mock.patch.dict(rf.app.config, {"INTERNAL_TOKEN": "s3cret"})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(control_audit.reset)
        self.addCleanup(self.dir.cleanup)
        self.client = rf.app.test_client()

    def post(self, path, body=None, token="good", remote="127.0.0.1", headers=None):
        h = dict(headers or {})
        if token:
            h["X-Session-Token"] = token
        return self.client.post(path, json=body, headers=h, environ_base={"REMOTE_ADDR": remote})

    def rows(self):
        """Execution rows only; the proposal.* rows are asserted where they matter."""
        return [e for e in self.store.events() if not e["action"].startswith("proposal.")]

    def all_rows(self):
        return self.store.events()


class SdrRouteAuditTest(_SdrFixture):
    def test_tune_is_audited_with_identity_source_and_deployment(self):
        r = self.post("/api/sdr/tune", {"frequency_hz": 433.92e6, "mode": "NFM", "bandwidth_hz": 12500})
        self.assertEqual(r.status_code, 200)
        started, done = self.rows()
        self.assertEqual((started["result"], done["result"]), ("started", "success"))
        for e in (started, done):
            self.assertEqual((e["action"], e["effect"], e["source"]), ("rf_tune", "HARDWARE_CONTROL", "api.sdr.tune"))
            self.assertEqual((e["principal_kind"], e["principal_id"], e["session_id"]),
                             (OPERATOR_SESSION, "op-1", "sess-9"))
            self.assertEqual(e["deployment_id"], "test-deploy")
        self.assertEqual(json.loads(done["parameters_json"]),
                         {"bandwidth_hz": 12500, "frequency_hz": 433.92e6, "mode": "NFM"})
        # The lifecycle wrote these (ADR 0005 Decision 4): one proposal, an operator command, same id on every row.
        self.assertEqual({e["initiator"] for e in self.all_rows()}, {"operator_command"})
        self.assertEqual(len({e["proposal_id"] for e in self.all_rows()}), 1)
        self.assertEqual([e["action"] for e in self.all_rows()], ["proposal.created", "rf_tune", "rf_tune"])
        self.assertEqual({e["source"] for e in self.all_rows()}, {"api.sdr.tune"})

    def test_start_and_stop_use_the_same_action_as_the_mcp_tool(self):
        self.assertEqual(self.post("/api/sdr/start").status_code, 200)
        self.assertEqual(self.post("/api/sdr/stop").status_code, 200)
        by_source = {(e["source"], e["action"]) for e in self.rows()}
        self.assertEqual(by_source, {("api.sdr.start", "rf_capture_start"), ("api.sdr.stop", "rf_capture_stop")})

    def test_config_is_audited(self):
        r = self.post("/api/sdr/config", {"fft_size": 2048})
        self.assertEqual(r.status_code, 200)
        done = self.rows()[-1]
        self.assertEqual((done["action"], done["source"], done["result"]), ("rf_stream_configure", "api.sdr.config", "success"))

    def test_bridge_failure_is_recorded_and_the_response_is_unchanged(self):
        self.bridge.fail_with = RuntimeError("rig refused")
        r = self.post("/api/sdr/tune", {"frequency_hz": 1e6})
        self.assertEqual((r.status_code, r.get_json()["status"]), (503, "error"))
        self.assertEqual([e["result"] for e in self.rows()], ["started", "error"])
        self.assertIn("rig refused", self.rows()[-1]["result_summary"])

    def test_invalid_input_never_reached_the_bridge_and_is_not_audited(self):
        r = self.post("/api/sdr/tune", {"mode": "NFM"})
        self.assertEqual((r.status_code, r.get_json()["message"]), (400, "frequency_hz is required"))
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.bridge.calls, [])

    def test_unauthenticated_request_is_refused_and_not_audited(self):
        r = self.post("/api/sdr/tune", {"frequency_hz": 1e6}, token=None)
        self.assertEqual(r.status_code, 401)
        r = self.post("/api/sdr/tune", {"frequency_hz": 1e6}, token="bad")
        self.assertEqual(r.status_code, 401)
        self.assertEqual((self.rows(), self.bridge.calls), ([], []))

    def test_every_kind_of_caller_keeps_its_kind(self):
        rf = self.rf
        self.post("/api/sdr/start", headers={"X-Internal-Token": "s3cret"}, token=None)
        with mock.patch.object(rf, "operator_manager", None):
            self.post("/api/sdr/start", token=None, remote="127.0.0.1")     # loopback, no manager
            self.post("/api/sdr/start", token=None, remote="10.1.2.3")      # anonymous development mode
        kinds = [e["principal_kind"] for e in self.rows() if e["result"] == "success"]
        self.assertEqual(kinds, [INTERNAL_TOKEN, LOOPBACK, ANONYMOUS])
        self.assertEqual({e["principal_id"] for e in self.rows()}, {None})

    def test_audit_failure_does_not_change_the_response(self):
        with mock.patch.object(self.store, "record", side_effect=OSError("disk full")):
            r = self.post("/api/sdr/tune", {"frequency_hz": 1e6})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.bridge.calls, ["tune"])

    def test_no_audit_store_configured_is_a_no_op(self):
        control_audit.reset()
        self.assertEqual(self.post("/api/sdr/tune", {"frequency_hz": 1e6}).status_code, 200)


class ParityTest(_SdrFixture):
    """The same hardware effect reached over MCP and over HTTP must leave equivalent records;
    the source is the one intended difference."""

    def mcp_rows(self, principal, params):
        from hypergraph_engine import HypergraphEngine
        from mcp_effects import HARDWARE_CONTROL
        from mcp_principal import principal_scope
        from mcp_registry import Registry, Tool
        reg = Registry()
        reg.register(Tool("rf_tune", "d", {"type": "object"}, {"type": "object"}, lambda **k: {"tuned": True},
                          mutates_state=True, effect=HARDWARE_CONTROL))
        with principal_scope(principal):
            reg.execute(HypergraphEngine(), "rf_tune", params, agent_mode="mutate")

    def comparable(self, e):
        return {k: e[k] for k in ("action", "effect", "principal_kind", "principal_id", "session_id",
                                  "deployment_id", "result")}

    def test_mcp_and_http_rf_tune_leave_equivalent_records(self):
        from mcp_principal import Principal
        self.mcp_rows(Principal(OPERATOR_SESSION, operator_id="op-1", session_id="sess-9"),
                      {"frequency_hz": 433.92e6, "api_key": "k-123"})
        self.post("/api/sdr/tune", {"frequency_hz": 433.92e6, "mode": None, "bandwidth_hz": 0})
        mcp = [e for e in self.rows() if e["source"] == "mcp"]
        http = [e for e in self.rows() if e["source"] == "api.sdr.tune"]
        self.assertEqual([self.comparable(e) for e in mcp], [self.comparable(e) for e in http])
        self.assertEqual({e["source"] for e in self.all_rows()}, {"mcp", "api.sdr.tune"})

    def test_secret_redaction_is_the_same_on_both_paths(self):
        from mcp_principal import Principal
        self.mcp_rows(Principal(OPERATOR_SESSION, operator_id="op-1"), {"frequency_hz": 1.0, "api_key": "k-123"})
        self.post("/api/sdr/config", {"fft_size": 512, "api_key": "k-123"})
        for e in self.all_rows():
            self.assertNotIn("k-123", e["parameters_json"])
            self.assertIn("<redacted>", e["parameters_json"])

    def test_an_anonymous_development_caller_is_recorded_as_anonymous_and_has_a_proposal(self):
        """Anonymous development mode still reaches hardware, but now through a proposal: the row says who
        it was (nobody identified) and which proposal carried it."""
        with mock.patch.object(self.rf, "operator_manager", None):
            self.post("/api/sdr/tune", {"frequency_hz": 1e6}, token=None, remote="10.1.2.3")
        row = [e for e in self.rows() if e["result"] == "success"][0]
        self.assertEqual((row["source"], row["principal_kind"], row["principal_id"], row["initiator"]),
                         ("api.sdr.tune", ANONYMOUS, None, "operator_command"))
        self.assertTrue(row["proposal_id"])


class McpSourceTest(unittest.TestCase):
    def test_mcp_rows_say_mcp(self):
        from hypergraph_engine import HypergraphEngine
        from mcp_effects import HARDWARE_CONTROL
        from mcp_registry import Registry, Tool
        with tempfile.TemporaryDirectory() as d:
            store = control_audit.configure(os.path.join(d, "a.sqlite3"), 90)
            reg = Registry()
            reg.register(Tool("rf_tune", "d", {"type": "object"}, {"type": "object"}, lambda **k: {},
                              mutates_state=True, effect=HARDWARE_CONTROL))
            reg.execute(HypergraphEngine(), "rf_tune", {"frequency_hz": 1.0}, agent_mode="mutate")
            self.assertEqual({e["source"] for e in store.events()}, {"mcp"})
            store.close()
            control_audit.reset()

    def test_store_created_before_source_existed_is_migrated(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "old.sqlite3")
            db = sqlite3.connect(path)
            db.executescript(control_audit.SCHEMA.replace("    source           TEXT,\n", ""))
            db.close()
            store = control_audit.ControlAuditStore(path, 90)
            store.record(execution_id="x", action="a", effect="E", result="success", source="api.sdr.tune")
            self.assertEqual(store.events()[0]["source"], "api.sdr.tune")
            store.close()


if __name__ == "__main__":
    unittest.main()
